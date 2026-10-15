"""Per-agent LLM profile — the static model configuration for one agent, loaded from JSON.

An :class:`AgentProfile` says WHICH model an agent runs on (``provider`` / ``model`` / ``base_url``)
and its baked-in sampling (``temperature`` / ``top_p`` / ``top_k`` / ``max_tokens``). The service
builds one chat model per agent from its profile; the sampling fields are set at construction so
they apply to EVERY call, including tool-loop (ReAct) agents the service does not invoke directly.

Dynamic, per-call overrides (e.g. the import-agent vote-temperature sweep) travel separately via
:class:`~tendermate_shared.agents.config.GenerationConfig` — they are not part of the static profile.

The secret ``api_key`` is deliberately NOT a profile field: it stays in the environment and is passed
to the model builder by the service, so the JSON profiles are safe to commit.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict


class AgentProfile(BaseModel):
    """Static LLM configuration for a single agent (one per-agent JSON file)."""

    # protected_namespaces=(): allow a field literally named ``model`` without pydantic's
    # ``model_``-prefix warning. extra="forbid": a typo'd key in the JSON is an error, not silent.
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    provider: str = "deepseek"
    model: str
    base_url: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    max_tokens: int | None = None

    def sampling_kwargs(self) -> dict[str, Any]:
        """Only the set sampling fields, as kwargs for the chat-model constructor."""
        kwargs: dict[str, Any] = {}
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.top_p is not None:
            kwargs["top_p"] = self.top_p
        if self.top_k is not None:
            kwargs["top_k"] = self.top_k
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        return kwargs


def load_agent_profiles(directory: str | Path) -> dict[str, AgentProfile]:
    """Load ``{agent_key: AgentProfile}`` from a directory of per-agent JSON files.

    Each ``<agent_key>.json`` in ``directory`` is one self-contained profile (no shared defaults —
    every agent carries its own full config)::

        config/agents/category_classifier.json
        {
          "provider": "deepseek",
          "model": "deepseek-chat",
          "base_url": "https://api.deepseek.com/v1",
          "temperature": 0.0
        }

    The file stem is the agent key. Raises if ``directory`` is missing or holds no ``*.json`` file,
    or on an unknown profile key inside a file.
    """
    d = Path(directory)
    if not d.is_dir():
        raise NotADirectoryError(f"{d}: agent config directory not found")
    files = sorted(d.glob("*.json"))
    if not files:
        raise FileNotFoundError(f"{d}: no per-agent *.json config files found")
    return {f.stem: AgentProfile(**json.loads(f.read_text(encoding="utf-8"))) for f in files}


__all__ = ["AgentProfile", "load_agent_profiles"]
