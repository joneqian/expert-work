"""``agent_disable`` records — Stream RT-4 (RT-ADR-16, kill switch).

A per-(tenant, agent_name) emergency-stop flag. Distinct from
:class:`~expert_work.protocol.agent_spec.AgentSpecStatus` (lifecycle
active/deprecated/deleted): ``disabled`` is a reversible emergency operation
that covers **all versions** of an agent name, so it lives in its own scope
(agents are stored per ``(name, version)``). When set, the agent rejects new
runs / sessions, its in-flight runs are bulk-cancelled, and the run-queue
worker refuses to claim its queued runs. Reverting the flag restores normal
operation. Materialized from a trusted DB row.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

__all__ = ["AgentDisableRecord"]


class AgentDisableRecord(BaseModel):
    """One ``agent_disable`` row — the agent-level kill-switch state."""

    model_config = ConfigDict(frozen=True)

    tenant_id: UUID
    agent_name: str
    disabled: bool = False
    #: Free-text reason captured at disable time (audit / UI display). ``None``
    #: when never disabled or when disabled without a reason.
    reason: str | None = None
    #: Actor id that last flipped the flag. ``None`` before the first write.
    disabled_by: str | None = None
    #: When the agent was last disabled. ``None`` when currently enabled (the
    #: enable path clears it).
    disabled_at: datetime | None = None
    updated_at: datetime
