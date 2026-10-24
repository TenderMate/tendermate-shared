"""Shared agent scaffolding for the TenderMate LLM services.

The complete ``BaseAgent`` lives here — message contract, ``model`` property, :meth:`safe_parse`,
and the ONE entry point both services share: the synchronous :meth:`run`, traced to LangSmith.

* ``tendermate-import-agent`` calls :meth:`run` directly inside its lexicon/extraction ensembles,
  running ``vote_n`` of them concurrently in a ``ThreadPoolExecutor``.
* ``tendermate-agent`` offloads :meth:`run` with ``asyncio.to_thread`` from its LangGraph nodes, so
  the blocking model call never stalls the event loop while products run concurrently.

:meth:`run` wraps the model call in a traced ``llm`` span nested under a traced ``chain`` span, so
token counts and latency show up per-agent in LangSmith for whichever service runs them. Per-call
sampling overrides (temperature / top_p / top_k) travel in a :class:`GenerationConfig`.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langsmith import traceable
from tendermate_shared.agents.config import GenerationConfig
from tendermate_shared.agents.input import AgentInput
from tendermate_shared.agents.result import AgentResult
from tendermate_shared.observability.sentry import report_handled_issue

logger = logging.getLogger(__name__)

TInput = TypeVar("TInput", bound=AgentInput)
TOutput = TypeVar("TOutput")


class BaseAgent(ABC, Generic[TInput, TOutput]):
    """Abstract base for a LangChain-model agent returning a typed, parsed result.

    The message contract is enforced here: :meth:`run`/:meth:`arun` build their messages via
    :meth:`_build_messages`, so the ``SystemMessage`` always comes from :meth:`_system_prompt`
    (agent policy) and the ``HumanMessage`` is ALWAYS :meth:`AgentInput.render_human` (typed data) —
    never a raw string hand-rolled at the call site.
    """

    def __init__(self, model: BaseChatModel) -> None:
        self._model = model

    @property
    def model(self) -> str:
        return getattr(
            self._model, "model_name", getattr(self._model, "model", type(self._model).__name__)
        )

    @abstractmethod
    def _system_prompt(self) -> str:
        """Return the system-prompt text (agent policy; independent of the input)."""
        ...

    @abstractmethod
    def _parse_response(self, response_text: str) -> TOutput:
        """Parse raw response text into the typed result (subclass decides whether to raise)."""
        ...

    def _build_messages(self, agent_input: TInput) -> list[BaseMessage]:
        """Build the full message list: system (policy) + human (typed data)."""
        return [
            SystemMessage(content=self._system_prompt()),
            HumanMessage(content=agent_input.render_human()),
        ]

    @traceable(run_type="llm")
    async def _acall_llm(self, messages: list[BaseMessage], **kwargs: Any) -> str:
        """Invoke the LangChain model asynchronously and return its text content.

        Decorated ``run_type="llm"`` so LangSmith records token usage for async
        calls too. Use this from :meth:`arun` — never ``asyncio.to_thread(_call_llm)``.
        """
        response = await self._model.ainvoke(messages, **kwargs)
        content = response.content
        if content is None:
            return ""
        return content if isinstance(content, str) else str(content)

    @traceable(run_type="chain")
    async def arun(
        self, agent_input: TInput, *, gen_config: GenerationConfig | None = None
    ) -> AgentResult[TOutput]:
        """Async counterpart of :meth:`run` using ``model.ainvoke()`` — no thread pool needed.

        Searching-agent LangGraph nodes use this so LLM calls stay on the event
        loop without saturating the ``asyncio.to_thread`` 5-thread pool. Import-agent
        keeps using the synchronous :meth:`run` inside its ``ThreadPoolExecutor`` ensembles.
        (Named ``gen_config``, not ``config``: ``@traceable`` reserves the ``config`` kwarg.)
        """
        messages = self._build_messages(agent_input)
        response_text = await self._acall_llm(
            messages, **(gen_config.to_model_kwargs() if gen_config else {})
        )

        human = messages[-1].content if messages else ""
        input_chars = len(human) if isinstance(human, str) else len(str(human))
        logger.info(
            "LLM ok model=%s input_chars=%d output_chars=%d",
            self.model,
            input_chars,
            len(response_text),
        )
        if logger.isEnabledFor(logging.DEBUG):
            for i, msg in enumerate(messages):
                role = getattr(msg, "type", type(msg).__name__)
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                logger.debug("LLM input[%d] role=%s: %s", i, role, content)
            logger.debug("LLM output: %s", response_text)

        return AgentResult(data=self._parse_response(response_text), model=self.model)

    @traceable(run_type="llm")
    def _call_llm(self, messages: list[BaseMessage], **kwargs: Any) -> str:
        """Invoke the LangChain model synchronously and return its text content.

        Decorated ``run_type="llm"`` so LangSmith records inputs, output, model name and token
        usage automatically. ``kwargs`` (e.g. a per-call ``temperature``) pass straight to the model.
        """
        response = self._model.invoke(messages, **kwargs)
        content = response.content
        if content is None:
            return ""
        return content if isinstance(content, str) else str(content)

    @traceable(run_type="chain")
    def run(
        self, agent_input: TInput, *, gen_config: GenerationConfig | None = None
    ) -> AgentResult[TOutput]:
        """Build messages, invoke the model synchronously, parse into the typed result.

        The single entry point for both services: import-agent calls it directly inside its
        ``ThreadPoolExecutor`` ensembles; searching-agent offloads it with ``asyncio.to_thread``
        from its async LangGraph nodes. ``gen_config`` (when given) carries per-call sampling
        overrides — temperature / top_p / top_k — forwarded to the model; import-agent uses it to
        run each vote at a different temperature. (Named ``gen_config``, not ``config``: the
        ``@traceable`` wrapper reserves a ``config`` kwarg for the LangChain ``RunnableConfig``.)
        """
        messages = self._build_messages(agent_input)
        response_text = self._call_llm(
            messages, **(gen_config.to_model_kwargs() if gen_config else {})
        )

        human = messages[-1].content if messages else ""
        input_chars = len(human) if isinstance(human, str) else len(str(human))
        logger.info(
            "LLM ok model=%s input_chars=%d output_chars=%d",
            self.model,
            input_chars,
            len(response_text),
        )
        if logger.isEnabledFor(logging.DEBUG):
            for i, msg in enumerate(messages):
                role = getattr(msg, "type", type(msg).__name__)
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                logger.debug("LLM input[%d] role=%s: %s", i, role, content)
            logger.debug("LLM output: %s", response_text)

        return AgentResult(data=self._parse_response(response_text), model=self.model)

    @staticmethod
    def safe_parse(
        parser: PydanticOutputParser,
        response_text: str,
        *,
        context: str,
        message: str,
        fallback: Any,
        level: str = "warning",
    ) -> Any:
        """Parse model output into a Pydantic object; never raise.

        The shared ``_parse_response`` body for agents that must degrade rather than crash: on any
        failure the issue is logged and forwarded to Sentry (with the truncated raw payload) via
        :func:`~tendermate_shared.observability.sentry.report_handled_issue`, and ``fallback`` is
        returned. A ``@staticmethod`` so a tool-loop agent that mirrors the contract WITHOUT
        subclassing :class:`BaseAgent` can reuse the one implementation too.
        """
        try:
            return parser.parse(response_text)
        except Exception as exc:  # noqa: BLE001 — never-raise helper: any parse error → fallback
            report_handled_issue(context, message, exc=exc, level=level, raw=response_text[:500])
            return fallback
