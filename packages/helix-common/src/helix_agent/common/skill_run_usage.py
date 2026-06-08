"""Cross-service ``SkillRunUsageRecorder`` Protocol + carrier — Stream SE (SE-7d-3b-ii).

The SE-7d regression-rollback monitor needs to know, per auto-promoted skill
version, the outcomes of the runs that actually used it. That signal is emitted
at run finalization (the orchestrator knows the terminal ``outcome`` + the run's
``thread_id``), one row per *distilled* skill version bound into the agent at
build time. The actual SQL write lives in the control-plane process (alongside
the SkillStore); the orchestrator can't import from control-plane (one-way
dep), so this Protocol is the contract the orchestrator depends on and
control-plane implements — mirroring :class:`SkillActivityRecorder` (U-27).

``BoundDistilledSkill`` is the build-time snapshot the run carries to its
finalization hook: which distilled version was bound, and the running agent's
name (half the ``{tenant}:{agent}`` circuit-breaker scope key). Only distilled
versions are tracked — human-authored skills never auto-promote, so there is
nothing to roll back.

The orchestrator may receive ``None`` (no recorder wired) — tests + the eval
CLI commonly leave it unset; emission then silently no-ops. Implementations
MUST NOT raise on the run's terminal path — failures are logged + swallowed (a
bookkeeping hiccup must never fail a user's run).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID


@dataclass(frozen=True)
class BoundDistilledSkill:
    """One distilled skill version bound into an agent at build time."""

    skill_id: UUID
    skill_version: int
    agent_name: str


@runtime_checkable
class SkillRunUsageRecorder(Protocol):
    """Best-effort: record that a run used a distilled skill version + how it ended.

    ``outcome`` is the run's terminal ``TrajectoryOutcome`` value (``success`` /
    ``failed`` / ``max_steps`` / ``cancelled``), typed as ``str`` here to keep
    helix-common free of a helix-protocol dependency; the SkillStore validates
    it against the ``TrajectoryOutcome`` literal on write.

    Implementations MUST NOT raise on the run hot path.
    """

    async def record(
        self,
        *,
        skill_id: UUID,
        skill_version: int,
        tenant_id: UUID,
        agent_name: str,
        thread_id: UUID,
        outcome: str,
    ) -> None:
        """Append one ``skill_run_usage`` row. Returns nothing — the caller
        cannot act on success / failure of the bookkeeping write."""


__all__ = ["BoundDistilledSkill", "SkillRunUsageRecorder"]
