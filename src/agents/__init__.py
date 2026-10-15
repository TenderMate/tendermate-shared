"""Shared agent scaffolding (BaseAgent + typed I/O), consumed by the TenderMate LLM services."""

from __future__ import annotations

from tendermate_shared.agents.base import BaseAgent
from tendermate_shared.agents.config import GenerationConfig
from tendermate_shared.agents.input import AgentInput
from tendermate_shared.agents.profile import AgentProfile, load_agent_profiles
from tendermate_shared.agents.result import AgentResult

__all__ = [
    "AgentInput",
    "AgentProfile",
    "AgentResult",
    "BaseAgent",
    "GenerationConfig",
    "load_agent_profiles",
]
