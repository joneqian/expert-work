"""Quality-drift alert record — Stream RT-5 (RT-ADR-24).

One raised alert when an agent's recent quality mean drops below its baseline
by more than the configured threshold. Persisted per-tenant (RLS) as an
append-only history: it feeds the ``quality.drift`` webhook (RT-ADR-25), the
per-(tenant, agent) cooldown (don't re-alert while an alert is fresh), and the
dashboard alert list (RT-ADR-26).

Honest boundary: drift is a statistical signal on a subjective LLM score, not
a causal diagnosis — the alert says "recent scores are down vs baseline", not
"the agent regressed". Acting on it is a human's call (no auto-remediation).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class QualityDriftAlertRecord(BaseModel):
    """One raised quality-drift alert."""

    model_config = ConfigDict(frozen=True)

    tenant_id: UUID
    agent_name: str
    #: Mean ``overall`` over the recent window.
    recent_mean: float
    #: Mean ``overall`` over the baseline window preceding it.
    baseline_mean: float
    #: Relative drop ``(baseline - recent) / baseline`` that crossed the threshold.
    drift_pct: float
    recent_count: int
    baseline_count: int
    #: Set by the store on insert.
    detected_at: datetime | None = None
    id: int | None = None
