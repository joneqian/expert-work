# ============================================================
# Adapted from bytedance/deer-flow @ 813d3c94efa7fdea6aafcb4f459304db91fcaed0
# Source: backend/packages/harness/deerflow/runtime/runs/schemas.py
# License: MIT (see vendor LICENSE)
# Modifications:
#   - Lowercase enum values aligned to ADR-0002 audit_log result words
# Last sync: 2026-05-11
# ============================================================

"""Run lifecycle status + disconnect-mode enums + the ``RunStore`` DTO."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class RunStatus(StrEnum):
    """Lifecycle status of a single run."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    INTERRUPTED = "interrupted"
    #: Stream J.8 (Mini-ADR J-24) — run ended at an approval gate and is
    #: resumable. The graph routed to END after writing
    #: ``AgentState.pending_approval``; the checkpoint persists so
    #: ``POST /v1/runs/{id}/resume`` can re-invoke from it. Distinct from
    #: ``INTERRUPTED`` (a user-driven cancel) — a PAUSED run is waiting
    #: on a human verdict, not aborted.
    PAUSED = "paused"


class DisconnectMode(StrEnum):
    """Behaviour when the SSE consumer disconnects mid-run."""

    CANCEL = "cancel"  # abort the run
    CONTINUE = "continue"  # keep running; results still go to event_log


#: Run statuses that mark a run as finished — ``RunManager`` stamps
#: ``finished_at`` when a run transitions into one of these.
TERMINAL_RUN_STATUSES: frozenset[RunStatus] = frozenset(
    {
        RunStatus.SUCCESS,
        RunStatus.ERROR,
        RunStatus.TIMEOUT,
        RunStatus.INTERRUPTED,
        RunStatus.PAUSED,
    }
)


@dataclass(frozen=True, slots=True)
class RunInfo:
    """Serialisable run-lifecycle snapshot — the ``RunStore`` DTO.

    A persistence-facing projection of
    :class:`~helix_agent.runtime.runs.manager.RunRecord` without the
    live-execution handles (``task`` / ``abort_event``), which are
    process-bound and never persisted. ``RunStore`` reads and writes
    this; ``RunManager`` builds it on each create.
    """

    run_id: UUID
    tenant_id: UUID
    thread_id: UUID
    user_id: UUID | None
    status: RunStatus
    on_disconnect: DisconnectMode
    is_resume: bool
    error: str | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None
