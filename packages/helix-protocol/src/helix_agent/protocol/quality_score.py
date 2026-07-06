"""Production quality-score record — Stream RT-5 (RT-ADR-24).

One LLM-judge verdict for a sampled production run. Persisted to the
``quality_score`` time-series (per-tenant, RLS) so the drift worker
(RT-ADR-24) and the dashboard (RT-ADR-26) can read a per-agent series.

Honest boundary (RT-ADR-23): ``overall`` / ``dimensions`` are a subjective
LLM rubric score, **not** ground truth — there is no gold label. The score
covers the sampled run's latest user<->assistant exchange; ``dimensions`` are
each on the same 1-5 scale as ``overall`` (``addressed_request`` is
relevance / completeness, *not* factual correctness — the platform is
domain-agnostic and cannot verify truth).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class QualityScoreRecord(BaseModel):
    """One judged run's quality verdict."""

    model_config = ConfigDict(frozen=True)

    tenant_id: UUID
    agent_name: str
    agent_version: str
    run_id: UUID
    thread_id: UUID
    #: Holistic 1-5 score; the series the drift worker tracks.
    overall: int
    #: Per-axis 1-5 breakdown (``addressed_request`` / ``coherence`` /
    #: ``safety``) for dashboard drill-down; free-form to allow rubric growth.
    dimensions: dict[str, int]
    #: The judge's short justification, surfaced on low-score drill-down.
    rationale: str
    #: Provider model that produced the verdict (audit / reproducibility).
    judge_model: str
    #: Set by the store on insert.
    observed_at: datetime | None = None
    id: int | None = None
