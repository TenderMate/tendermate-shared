"""Typed input contract shared by every agent across the TenderMate services.

The user-message ("human turn") content an agent sends is ALWAYS produced by
:meth:`AgentInput.render_human` from typed fields — never an ad-hoc string assembled at the call
site. :class:`~tendermate_shared.agents.base.BaseAgent` consumes an ``AgentInput`` and never sees
raw text, so the prompt layout has a single source of truth per agent. Both ``tendermate-agent``
and ``tendermate-import-agent`` re-export this from their local ``dto/agents/base.py``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class AgentInput(BaseModel):
    """Typed contract for an agent's user turn (shared base for all agent inputs)."""

    model_config = ConfigDict(frozen=True)

    def render_human(self) -> str:
        """Render the user-turn content. Subclasses must override."""
        raise NotImplementedError
