"""LangChain callback that turns every chat-model call into metrics (Sentry + tm-obs) + logs.

Each service attaches one per model in its registry (``llm/registry.py``:
``model.callbacks = [LlmTelemetryCallback(key, provider, model)]``), so every agent and the ReAct
tool loop are covered regardless of provider, and both agents report the same ``llm.*`` metrics
(the LLM dashboard reads them). Safe from pipeline threads: one dict op per call, OTel and Sentry
metrics are thread-safe.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from tendermate_shared.observability import metrics

logger = logging.getLogger(__name__)


class LlmTelemetryCallback(BaseCallbackHandler):
    """Emit ``llm.call`` count/duration and token usage per agent; log every failed call."""

    # Metrics/logging are non-blocking — run on the event loop, not in a thread executor.
    run_inline = True

    def __init__(self, agent: str, provider: str, model: str) -> None:
        self._attrs = {"agent": agent, "provider": provider, "model": model}
        self._started: dict[UUID, float] = {}

    def on_chat_model_start(
        self, serialized: dict[str, Any], messages: list[list[Any]], *, run_id: UUID, **_: Any
    ) -> None:
        self._started[run_id] = time.monotonic()

    def on_llm_start(
        self, serialized: dict[str, Any], prompts: list[str], *, run_id: UUID, **_: Any
    ) -> None:
        self._started[run_id] = time.monotonic()

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **_: Any) -> None:
        elapsed = self._elapsed(run_id)
        attributes = {**self._attrs, "result": "ok"}
        metrics.count("llm.call", 1, attributes=attributes)
        if elapsed is not None:
            metrics.distribution("llm.call.duration", elapsed, unit="second", attributes=attributes)
        prompt_tokens, completion_tokens = _token_usage(response)
        if prompt_tokens:
            metrics.distribution("llm.tokens.prompt", prompt_tokens, attributes=self._attrs)
        if completion_tokens:
            metrics.distribution("llm.tokens.completion", completion_tokens, attributes=self._attrs)

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **_: Any) -> None:
        elapsed = self._elapsed(run_id)
        attributes = {**self._attrs, "result": "error", "error_type": type(error).__name__}
        metrics.count("llm.call", 1, attributes=attributes)
        if elapsed is not None:
            metrics.distribution("llm.call.duration", elapsed, unit="second", attributes=attributes)
        # Auth/quota/rate-limit errors from the provider land here first; agents may swallow them.
        logger.warning(
            "LLM call failed agent=%s model=%s after %s: %s: %s",
            self._attrs["agent"],
            self._attrs["model"],
            f"{elapsed:.1f}s" if elapsed is not None else "?",
            type(error).__name__,
            error,
        )

    def _elapsed(self, run_id: UUID) -> float | None:
        started = self._started.pop(run_id, None)
        return None if started is None else time.monotonic() - started


def _token_usage(response: LLMResult) -> tuple[int, int]:
    """Best-effort (prompt, completion) token counts across providers."""
    for generations in response.generations:
        for gen in generations:
            usage = getattr(getattr(gen, "message", None), "usage_metadata", None)
            if usage:
                return int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
    usage = (response.llm_output or {}).get("token_usage") or {}
    return int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
