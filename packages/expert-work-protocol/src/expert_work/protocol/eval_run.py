"""P1-S2.1 — eval run / case-result DTOs (eval platform ops layer).

Wire shape between the resident ``EvalWorker`` (S2.1b), the eval-run
store, and the eval CRUD/read surfaces. The capability-eval *engine*
(``tools/eval``) stays the execution core; these records are the durable
ops layer the worker writes.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "EvalCaseResultRecord",
    "EvalRunRecord",
    "EvalRunStatus",
    "EvalTriggeredBy",
]


class EvalRunStatus(StrEnum):
    """Status machine for one eval run.

    ``queued`` → ``running`` → terminal (``passed`` / ``failed`` / ``error``).
    ``passed``/``failed`` are gate verdicts; ``error`` is an execution fault
    (the suite never produced a verdict).
    """

    QUEUED = "queued"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


class EvalTriggeredBy(StrEnum):
    """What enqueued the run."""

    MANUAL = "manual"
    CI = "ci"
    SCHEDULE = "schedule"


class EvalRunRecord(BaseModel):
    """One execution of an eval suite."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: UUID
    #: Suite name: ``m0_baseline`` / ``adversarial`` / a single capability id.
    suite: str
    status: EvalRunStatus
    triggered_by: EvalTriggeredBy
    #: Aggregate verdict payload (pass_count/total/scores); ``None`` until done.
    summary: dict[str, object] | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class EvalCaseResultRecord(BaseModel):
    """One case outcome under a run."""

    model_config = ConfigDict(frozen=True)

    #: ``None`` before persistence assigns the bigserial id.
    id: int | None = None
    run_id: UUID
    tenant_id: UUID
    capability: str
    case_id: str
    passed: bool
    #: Multi-turn session this case belongs to (S2.2 session metrics); ``None``
    #: for single-shot cases.
    session_id: str | None = None
    scores: dict[str, float] = Field(default_factory=dict)
    #: Session-level metrics (resolution_rate / goal_completion …) when the
    #: case closes a session; ``None`` otherwise (S2.2).
    session_metrics: dict[str, float] | None = None
