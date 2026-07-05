"""Stream RT-4 (RT-ADR-16) — the shared kill-switch predicate.

Every path that *spawns a run* must consult this, not just the front-door
session/admission handlers: a disabled agent or a suspended tenant has to be
stopped at the queue claim, at scheduled/webhook trigger firing, at approval
resume, and at orphan respawn too. Otherwise the "emergency stop" leaks — an
unattended cron agent keeps firing, or an approved-then-disabled tool call
resumes on a fresh run id that no front-door gate ever saw.

Both reads hit RLS-protected tables (``tenant_config`` is FORCE-RLS,
``agent_disable`` is ENABLE-RLS), so the caller MUST already be inside the
correct tenant RLS scope before calling this.
"""

from __future__ import annotations

from uuid import UUID

from control_plane.agent_disable_status import AgentDisableService
from control_plane.tenant_status import TenantStatusService


async def run_block_reason(
    *,
    tenant_status: TenantStatusService | None,
    agent_disable: AgentDisableService | None,
    tenant_id: UUID,
    agent_name: str | None,
) -> str | None:
    """Return ``"tenant_suspended"`` / ``"agent_disabled"`` when a run for this
    ``(tenant, agent)`` must not proceed, else ``None``.

    Fail-open on an unwired service (mirrors the existing front-door gates): a
    deployment that has not wired the kill switch behaves as before. Tenant
    suspend is checked first — it subsumes every agent in the tenant.
    """
    if tenant_status is not None and await tenant_status.is_suspended(tenant_id):
        return "tenant_suspended"
    if (
        agent_disable is not None
        and agent_name is not None
        and await agent_disable.is_disabled(tenant_id, agent_name)
    ):
        return "agent_disabled"
    return None
