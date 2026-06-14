"""``RunBaselineEvalEngine`` — P1-S2.1c (production eval engine adapter).

Bridges the :class:`EvalWorker`'s neutral :class:`EvalEngine` Protocol to
the existing capability-eval engine (``tools/eval/run_baseline.py``). The
worker stays decoupled: it knows only :class:`EvalCaseOutcome`; this
adapter maps the engine's ``CapabilityReport``s onto them.

``run_baseline`` is imported lazily inside :meth:`run` — only environments
that actually execute an eval need the ``tools/eval`` harness on the path,
and a missing harness surfaces as a per-run error (the worker isolates it
to ``status=error``), never an import-time crash of the control plane.
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from control_plane.eval_worker import EvalCaseOutcome

#: The only suite this adapter runs — the deterministic M0 capability gate.
M0_BASELINE_SUITE = "m0_baseline"


class _ReportLike(Protocol):
    """Duck-typed view of ``tools.eval._capability.CapabilityReport`` — kept
    structural so this module needn't import the eval harness at load time."""

    @property
    def status(self) -> str:
        """``PASS`` / ``FAIL`` / ``DEFERRED``."""

    @property
    def aggregate_score(self) -> Mapping[str, float]:
        """Per-metric aggregate scores."""

    @property
    def session_metrics(self) -> Mapping[str, float]:
        """P1-S2.2 (11.3) session-level rollup; empty when not applicable."""


def reports_to_outcomes(reports: Mapping[str, _ReportLike]) -> list[EvalCaseOutcome]:
    """Map ``capability -> CapabilityReport`` onto one outcome per capability.

    A report's ``PASS`` status becomes ``passed``; ``FAIL`` / ``DEFERRED``
    become not-passed (a deferred capability is not a green gate). The
    report's session-level rollup (S2.2) rides along as
    ``session_metrics`` — ``None`` when the capability emits none, so the
    persisted column stays null rather than ``{}``.
    """
    return [
        EvalCaseOutcome(
            capability=capability,
            case_id=capability,
            passed=report.status == "PASS",
            scores={k: float(v) for k, v in dict(report.aggregate_score).items()},
            session_metrics=(
                {k: float(v) for k, v in dict(report.session_metrics).items()} or None
            ),
        )
        for capability, report in reports.items()
    ]


class RunBaselineEvalEngine:
    """Runs the M0 baseline suite via ``tools/eval/run_baseline``."""

    async def run(self, suite: str) -> Sequence[EvalCaseOutcome]:
        if suite != M0_BASELINE_SUITE:
            msg = f"RunBaselineEvalEngine only runs {M0_BASELINE_SUITE!r}, not {suite!r}"
            raise ValueError(msg)
        # Lazy import — the eval harness is a dev/ops dependency, not a
        # control-plane import-time one.
        from tools.eval.run_baseline import run_baseline

        # ``run_baseline`` always writes its YAML; redirect it to a scratch
        # file (we consume the returned reports, not the file).
        out_path = Path(tempfile.gettempdir()) / "helix_eval_worker_baseline.yaml"
        reports = await run_baseline(out_path=out_path)
        return reports_to_outcomes(reports)
