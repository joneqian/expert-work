"""J.13a baseline aggregator — Stream J.13a (Mini-ADR J-38).

Runs every per-capability eval module under ``tools/eval/`` and writes
``tools/eval/baselines/m0_gate_baseline.yaml`` — the checked-in artifact
that ``STREAM-M-DESIGN.md`` Exit Criteria reads.

Usage::

    .venv/bin/python tools/eval/run_baseline.py

Capabilities not yet shipped (per Stream J ITERATION-PLAN) emit
``status: DEFERRED`` so the file shape locks early; the corresponding
capability PR then turns the entry into a real PASS / FAIL by registering
a real runner in :data:`_RUNNERS`.

The fake keyword embedder lets J.3 run without an LLM API key (CI path).
Mini-ADR J-39 fixes the LLM-judge model + parameters for M0 but the
judge is not used by any J.13a-1 capability (only J.1 needs it — landed
in J.13a-2).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import subprocess
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml

# Co-located in tools/eval/ — load via path injection because the
# directory is not a package (matches the existing memory_recall test
# pattern, see ``test_memory_recall.py`` lines 11-13).
_EVAL_DIR = Path(__file__).resolve().parent
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

import memory_recall as _mr  # type: ignore[import-not-found]  # noqa: E402
import model_routing as _mrt  # type: ignore[import-not-found]  # noqa: E402
import per_user_isolation as _pui  # type: ignore[import-not-found]  # noqa: E402
from _capability import (  # type: ignore[import-not-found]  # noqa: E402
    CapabilityReport,
)

logger = logging.getLogger(__name__)

_DATASETS = _EVAL_DIR / "datasets"
_DEFAULT_OUT = _EVAL_DIR / "baselines" / "m0_gate_baseline.yaml"

# Mini-ADR J-39 — fixed M0 judge parameters; recorded in baseline
# metadata so the file is reproducible.
_JUDGE_MODEL = "claude-haiku-4-5-20251001"
_JUDGE_TEMPERATURE = 0.0
_RERUN_COUNT = 3
_EMBEDDER_TAG = "helix-fake-keyword-embedder-v1"


# ---------------------------------------------------------------------------
# Fake embedder — drives the J.3 recall baseline with no LLM dependency.
# ---------------------------------------------------------------------------


class _FakeKeywordEmbedder:
    """Deterministic keyword-overlap embedder.

    Identical in mechanism to the test embedder in
    ``test_memory_recall.py`` (CJK bigrams + ASCII words → one-hot
    accumulator). Lives here so ``run_baseline`` works without depending
    on test code; the dataset is calibrated to score ≥ 0.7 recall / 0.55
    MRR against this embedder, well above the M1 thresholds the design
    locked in § 18.3.
    """

    DIM: int = 256

    async def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        return [self._encode(t) for t in texts]

    def _encode(self, text: str) -> tuple[float, ...]:
        vec = [0.0] * self.DIM
        for token in _tokenise(text):
            vec[hash(token) % self.DIM] += 1.0
        return tuple(vec)


def _tokenise(text: str) -> list[str]:
    """ASCII whitespace split + CJK char + bigram tokens (test-embedder parity)."""
    cleaned = text.lower().strip()
    if not cleaned:
        return []
    ascii_words = [w for w in cleaned.replace(",", " ").split() if w]
    cjk_chars = [c for c in cleaned if "一" <= c <= "鿿"]
    cjk_bigrams = [cjk_chars[i] + cjk_chars[i + 1] for i in range(len(cjk_chars) - 1)]
    return ascii_words + cjk_chars + cjk_bigrams


# ---------------------------------------------------------------------------
# Per-capability runners — each returns a CapabilityReport.
# ---------------------------------------------------------------------------


async def _run_memory_recall() -> CapabilityReport:
    cases = _mr.load_cases(_DATASETS / "memory_recall" / "m0_baseline.yaml")
    report = await _mr.evaluate_set(cases, embedder=_FakeKeywordEmbedder(), k=5)
    recall = report.mean_recall_at_k
    mrr = report.mean_mrr_at_k
    threshold = {"recall_at_5": 0.70, "mrr_at_5": 0.55}
    status: str = (
        "PASS" if recall >= threshold["recall_at_5"] and mrr >= threshold["mrr_at_5"] else "FAIL"
    )
    return CapabilityReport(
        capability="J.3_memory_recall",
        metric_type="recall@5+mrr@5",
        sample_size=report.n_cases,
        threshold=threshold,
        aggregate_score={"recall_at_5": recall, "mrr_at_5": mrr},
        status=cast(Any, status),
        # K12 EvalReport's per-case shape differs from CapabilityCaseResult;
        # left empty here, full per-case stays in the K12 path.
        per_case=(),
    )


async def _run_model_routing() -> CapabilityReport:
    cases = _mrt.load_cases(_DATASETS / "model_routing" / "m0_baseline.yaml")
    return await _mrt.evaluate_set(cases)


async def _run_per_user_isolation() -> CapabilityReport:
    cases = _pui.load_cases(_DATASETS / "per_user_isolation" / "m0_baseline.yaml")
    return await _pui.evaluate_set(cases)


# ---------------------------------------------------------------------------
# Registry — single source of truth for what enters the baseline.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Runner:
    """One row of the baseline registry."""

    capability: str
    metric_type: str
    threshold: Mapping[str, float]
    runner_fn: Callable[[], Awaitable[CapabilityReport]] | None
    deferred_reason: str


_DEFERRED_PENDING_J13A2 = "Capability shipped; eval scheduled for J.13a-2 PR."
_DEFERRED_PENDING_CAPABILITY = (
    "Capability not yet shipped (see Stream J ITERATION-PLAN); "
    "skeleton stub locks the baseline file shape."
)

# Order = the order capabilities appear in the baseline YAML.
_RUNNERS: tuple[_Runner, ...] = (
    _Runner(
        "J.1_plan_execute",
        "pass-rate+llm-judge",
        {"pass_rate": 0.80, "judge_mean": 4.0},
        None,
        _DEFERRED_PENDING_J13A2,
    ),
    _Runner(
        "J.2_reflect",
        "pass-rate",
        {"correction_rate": 0.75, "false_positive_rate": 0.20},
        None,
        _DEFERRED_PENDING_J13A2,
    ),
    _Runner(
        "J.3_memory_recall",
        "recall@5+mrr@5",
        {"recall_at_5": 0.70, "mrr_at_5": 0.55},
        _run_memory_recall,
        "",
    ),
    _Runner(
        "J.4_sub_agent",
        "pass-rate",
        {"pass_rate": 0.80},
        None,
        _DEFERRED_PENDING_CAPABILITY,
    ),
    _Runner(
        "J.5_rag",
        "pass-rate+recall@k",
        {"pass_rate": 0.80, "recall_at_k": 0.70},
        None,
        _DEFERRED_PENDING_CAPABILITY,
    ),
    _Runner(
        "J.6_multimodal",
        "pass-rate",
        {"pass_rate_path_a": 0.80, "pass_rate_path_b": 0.80},
        None,
        _DEFERRED_PENDING_J13A2,
    ),
    _Runner(
        "J.7_skill",
        "pass-rate",
        {"pass_rate": 0.80},
        None,
        _DEFERRED_PENDING_CAPABILITY,
    ),
    _Runner(
        "J.8_hitl",
        "pass-rate",
        {"pass_rate": 0.95},
        None,
        _DEFERRED_PENDING_CAPABILITY,
    ),
    _Runner(
        "J.9_artifact",
        "pass-rate",
        {"pass_rate": 0.90},
        None,
        _DEFERRED_PENDING_CAPABILITY,
    ),
    _Runner(
        "J.10_trigger",
        "pass-rate",
        {"pass_rate": 0.90},
        None,
        _DEFERRED_PENDING_CAPABILITY,
    ),
    _Runner(
        "J.11_model_routing",
        "pass-rate",
        {"pass_rate": 0.95},
        _run_model_routing,
        "",
    ),
    _Runner(
        "J.12_learning",
        "pass-rate",
        {"pass_rate": 0.80},
        None,
        _DEFERRED_PENDING_CAPABILITY,
    ),
    _Runner(
        "J.14_per_user_isolation",
        "pass-rate",
        {"pass_rate": 1.00},
        _run_per_user_isolation,
        "",
    ),
    _Runner(
        "J.15_persistent_volume",
        "pass-rate",
        {"pass_rate": 0.90},
        None,
        _DEFERRED_PENDING_J13A2,
    ),
)


# ---------------------------------------------------------------------------
# Aggregation + YAML writing.
# ---------------------------------------------------------------------------


async def run_baseline(*, out_path: Path = _DEFAULT_OUT) -> dict[str, CapabilityReport]:
    """Run every registered capability; write the baseline YAML; return reports."""
    reports: dict[str, CapabilityReport] = {}
    for runner in _RUNNERS:
        if runner.runner_fn is None:
            reports[runner.capability] = CapabilityReport.deferred(
                capability=runner.capability,
                metric_type=runner.metric_type,
                threshold=runner.threshold,
                deferred_reason=runner.deferred_reason,
            )
            continue
        logger.info("baseline: running %s", runner.capability)
        reports[runner.capability] = await runner.runner_fn()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_render_yaml(reports), encoding="utf-8")
    return reports


def _render_yaml(reports: Mapping[str, CapabilityReport]) -> str:
    """Render the baseline file — deterministic key order matching :data:`_RUNNERS`."""
    metadata = {
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "helix_commit": _git_head(),
        "judge_model": _JUDGE_MODEL,
        "judge_temperature": _JUDGE_TEMPERATURE,
        "rerun_count": _RERUN_COUNT,
        "embedder": _EMBEDDER_TAG,
    }
    capabilities: dict[str, Any] = {}
    for cap, report in reports.items():
        entry: dict[str, Any] = {
            "metric_type": report.metric_type,
            "sample_size": report.sample_size,
            "threshold": dict(report.threshold),
            "score": dict(report.aggregate_score),
            "status": report.status,
        }
        if report.status == "DEFERRED" and report.deferred_reason:
            entry["deferred_reason"] = report.deferred_reason
        capabilities[cap] = entry
    payload = {"metadata": metadata, "capabilities": capabilities}
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)


def _git_head() -> str:
    """Best-effort commit sha for the baseline metadata; ``unknown`` outside a repo."""
    try:
        argv = ["git", "rev-parse", "HEAD"]
        result = subprocess.run(  # noqa: S603 — fixed argv, no shell, dev tool
            argv,
            cwd=_EVAL_DIR,
            capture_output=True,
            check=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return "unknown"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the J.13a baseline aggregator.")
    parser.add_argument(
        "--out",
        type=Path,
        default=_DEFAULT_OUT,
        help=f"output path for the baseline YAML (default: {_DEFAULT_OUT.relative_to(Path.cwd())})",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    reports = asyncio.run(run_baseline(out_path=args.out))

    pass_count = sum(1 for r in reports.values() if r.status == "PASS")
    fail_count = sum(1 for r in reports.values() if r.status == "FAIL")
    deferred_count = sum(1 for r in reports.values() if r.status == "DEFERRED")
    logger.info(
        "baseline: %d PASS / %d FAIL / %d DEFERRED — wrote %s",
        pass_count,
        fail_count,
        deferred_count,
        args.out,
    )
    return 1 if fail_count > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
