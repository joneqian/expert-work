"""``agent_instance`` records — Stream Agent-Templates (M1-5b).

A lightweight per-(tenant, agent_code, end-user) binding. The agent *definition*
is shared (the tenant fork); this row + the per-user memory / workspace / threads
are the per-user "instance". Used to enumerate which end-users use an agent and to
track per-user activity. ``agent_code`` is the tenant fork's name.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

__all__ = ["AgentInstanceRecord"]


class AgentInstanceRecord(BaseModel):
    """One end-user's binding to a tenant agent. Materialized from a trusted DB row."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: UUID
    agent_code: str
    user_id: UUID
    created_at: datetime
    last_active_at: datetime
