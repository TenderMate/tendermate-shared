"""Shared agent scaffolding (BaseAgent + typed I/O), consumed by the TenderMate LLM services."""

from __future__ import annotations

from tendermate_shared.agents.base import AgentResult, BaseAgent
from tendermate_shared.agents.input import AgentInput

__all__ = ["AgentInput", "AgentResult", "BaseAgent"]
