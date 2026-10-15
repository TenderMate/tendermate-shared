"""The uniform result envelope returned by every agent's ``run``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

TOutput = TypeVar("TOutput")


@dataclass
class AgentResult(Generic[TOutput]):
    """Uniform result envelope returned by every agent's ``run``."""

    data: TOutput
    model: str


__all__ = ["AgentResult"]
