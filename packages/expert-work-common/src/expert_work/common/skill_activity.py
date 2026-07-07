"""Cross-service ``SkillActivityRecorder`` Protocol — Mini-ADR U-27.

The orchestrator's ``agent_factory._load_skills`` (build-time bind
event) and the ``skill_view`` tool (runtime read event) both need to
mark a skill as "just used" so the Sprint #4 Curator doesn't transition
it to ``stale``. The actual SQL write (and the throttle around it)
lives in the control-plane process (alongside the SkillStore), but the
orchestrator can't import from control-plane (one-way dep). This
Protocol is the contract the orchestrator depends on; ``control_plane.
skill_activity.ThrottledActivityRecorder`` is the production
implementation, injected through ``build_agent``.

The orchestrator may receive ``None`` (no recorder wired) — tests +
the eval CLI commonly leave it unset; in that case activity tracking
silently no-ops. The Curator's behavior degrades gracefully (the rows
just look "less recently used" than they really are) — never an
incorrectness, only a triage-thresholds tuning concern.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID


@runtime_checkable
class SkillActivityRecorder(Protocol):
    """Best-effort activity bump for one skill.

    Implementations MUST NOT raise on the agent hot path — failures
    should be logged + swallowed (the Curator can't be allowed to
    cause an agent run to fail). Implementations SHOULD throttle so
    high-fanout callers (1000 agent runs / sec) don't amplify into
    1000 SQL UPDATEs / sec on the same skill row.
    """

    async def record(self, *, skill_id: UUID, tenant_id: UUID) -> None:
        """Mark ``skill_id`` as just-used. Returns nothing — the caller
        cannot do anything useful with success / failure information."""


__all__ = ["SkillActivityRecorder"]
