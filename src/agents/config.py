"""Per-call generation config — sampling overrides passed through to the chat model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GenerationConfig:
    """Optional per-call sampling overrides forwarded to ``BaseChatModel.invoke``.

    Only the set (non-``None``) fields are forwarded, so a model never receives a parameter the
    caller did not ask for. Which parameters a given provider honours is provider-specific — e.g.
    the OpenAI-compatible DeepSeek endpoint takes ``temperature``/``top_p`` but not ``top_k``.
    """

    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None

    def to_model_kwargs(self) -> dict[str, Any]:
        """Return only the set fields, as kwargs for ``model.invoke(messages, **kwargs)``."""
        kwargs: dict[str, Any] = {}
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.top_p is not None:
            kwargs["top_p"] = self.top_p
        if self.top_k is not None:
            kwargs["top_k"] = self.top_k
        return kwargs


__all__ = ["GenerationConfig"]
