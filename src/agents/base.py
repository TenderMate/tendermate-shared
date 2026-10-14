"""Shared agent scaffolding for the TenderMate LLM services.

This is the framework-agnostic core of ``BaseAgent`` — everything EXCEPT ``run()``. The two
services drive the model differently and cannot share a single ``run``:

* ``tendermate-agent`` — ``async def run`` traced to LangSmith, offloading the sync model call to a
  thread executor (awaited inside LangGraph nodes).
* ``tendermate-import-agent`` — ``def run`` (sync, with a per-call ``temperature`` override), called
  inside a ``ThreadPoolExecutor`` by the lexicon/extraction ensembles.

So each service subclasses :class:`BaseAgent` and adds its own ``run``; the message contract,
``model`` property and :meth:`safe_parse` live here, once.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser

from tendermate_shared.agents.input import AgentInput
from tendermate_shared.observability.sentry import report_handled_issue

TInput = TypeVar("TInput", bound=AgentInput)
TOutput = TypeVar("TOutput")


@dataclass
class AgentResult(Generic[TOutput]):
    """Uniform result envelope returned by every agent's ``run``."""

    data: TOutput
    model: str


class BaseAgent(ABC, Generic[TInput, TOutput]):
    """Abstract base for a LangChain-model agent returning a typed, parsed result.

    The message contract is enforced here: a subclass' ``run`` MUST build its messages via
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
