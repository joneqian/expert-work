"""Shared eval protocol — Stream J.13a (Mini-ADR J-37 / J-38 / J-39).

Every capability evaluator under ``tools/eval/<capability>.py`` returns a
:class:`CapabilityReport`. ``tools/eval/run_baseline.py`` aggregates the
reports into the checked-in baseline YAML
(``tools/eval/baselines/m0_gate_baseline.yaml``) that
``STREAM-M-DESIGN.md`` Exit Criteria reads.

The per-case dataclass is up to each module — capabilities have wildly
different inputs (queries vs RoutingSpec vs ThreadMeta), so we only fix
the *report* shape, not the case shape. ``CapabilityCaseResult`` is the
common rollup unit each module produces.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

EvalStatus = Literal["PASS", "FAIL", "DEFERRED"]

#: Type alias for an LLM-judge ``complete(prompt) -> str`` callable.
#: Mini-ADR J-39 fixes the M0 default to ``claude-haiku-4-5-20251001`` at
#: ``temperature=0.0`` with ``N=3`` reruns; CI uses a mock provider.
JudgeCompletionFn = Callable[[str], Awaitable[str]]


@dataclass(frozen=True)
class CapabilityCaseResult:
    """One case's pass/fail + optional per-case scores.

    ``scores`` lets modules attach the metric values that fed the
    aggregate (e.g. ``recall_at_5 = 0.6``, ``judge_score = 4``). Keeping
    them per-case makes the baseline diff-friendly under git review.
    """

    case_id: str
    passed: bool
    scores: Mapping[str, float] = field(default_factory=dict)
    notes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CapabilityReport:
    """Aggregate report for one capability — written to the baseline YAML.

    ``aggregate_score`` / ``threshold`` are mappings because a single
    capability can score on multiple axes (recall + mrr, pass-rate +
    judge mean) — the baseline file is a transparent mirror of these
    fields (see § 18.5 sample).

    ``status``:
    - ``PASS`` — every metric meets its threshold.
    - ``FAIL`` — at least one metric below threshold.
    - ``DEFERRED`` — capability not yet shipped; baseline carries the
      shape but no score (see ``deferred_reason``).
    """

    capability: str
    metric_type: str
    sample_size: int
    threshold: Mapping[str, float]
    aggregate_score: Mapping[str, float]
    status: EvalStatus
    per_case: tuple[CapabilityCaseResult, ...] = field(default_factory=tuple)
    #: P1-S2.2 (11.3) — session-level rollup over ``per_case`` (each case is
    #: a full agent session). Empty when the capability emits no per-case
    #: rows. See :func:`session_metrics_from_cases`.
    session_metrics: Mapping[str, float] = field(default_factory=dict)
    deferred_reason: str = ""

    @staticmethod
    def deferred(
        *,
        capability: str,
        metric_type: str,
        threshold: Mapping[str, float],
        deferred_reason: str,
    ) -> CapabilityReport:
        """Helper for the skeleton-stub path (Mini-ADR J-28)."""
        return CapabilityReport(
            capability=capability,
            metric_type=metric_type,
            sample_size=0,
            threshold=dict(threshold),
            aggregate_score={},
            status="DEFERRED",
            per_case=(),
            deferred_reason=deferred_reason,
        )


def session_metrics_from_cases(
    per_case: Sequence[CapabilityCaseResult],
) -> dict[str, float]:
    """Roll per-case results up to session-level metrics — P1-S2.2 (11.3).

    Each capability case runs a full agent session, so the fraction of
    cases whose goal was met (``goal_completion``) is a genuine
    session-outcome metric — distinct from the per-axis ``aggregate_score``
    (recall@5, judge mean). Returns ``{}`` when the capability emits no
    per-case rows (nothing to roll up → the persisted column stays null).

    ``escalation_rate`` is emitted **only** when cases carry an
    ``escalated`` score signal; it is never zero-filled, so a capability
    that does not track escalation simply omits the metric (no fake zero).
    """
    if not per_case:
        return {}
    n = len(per_case)
    metrics: dict[str, float] = {
        "goal_completion": sum(1 for c in per_case if c.passed) / n,
    }
    escalated = [float(c.scores["escalated"]) for c in per_case if "escalated" in c.scores]
    if escalated:
        metrics["escalation_rate"] = sum(escalated) / len(escalated)
    return metrics


__all__ = [
    "CapabilityCaseResult",
    "CapabilityReport",
    "EvalStatus",
    "JudgeCompletionFn",
    "session_metrics_from_cases",
]
