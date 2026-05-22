"""J.12 学习 / 反馈闭环 eval — Stream J.13a (M0 baseline) closeout.

Mini-ADR J-43 + STREAM-J-DESIGN § 17 behaviour lock. Drives the J.12
pieces against scripted, fully-deterministic cases:

* **classify** — the curation worker's signal rule (👎 / failed /
  👍 / skip; negative outranks failed).
* **key** — ``_tenant_from_key`` trajectory-key parsing.
* **spec** — :class:`EvalDatasetRecord` / :class:`CurationCandidateRecord`
  invariant validators.
* **store** — :class:`InMemoryEvalDatasetStore` /
  ``InMemoryCurationCandidateStore`` create / get / cross-tenant hiding
  / count / upsert dedup / review filter.
* **reader** — :class:`TrajectoryReader` read round-trip + malformed skip.
* **export** — ``render_dataset_yaml`` curated-row → J.13 YAML rendering.

Per Mini-ADR J-37 J.12 metric is deterministic ``pass_rate``; the
baseline threshold is ≥ 0.80 — achievable = 1.00 on these scripted cases.
"""

from __future__ import annotations

import sys as _sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from langchain_core.messages import HumanMessage
from pydantic import ValidationError

from control_plane.curation_worker import _classify, _tenant_from_key
from helix_agent.persistence import InMemoryCurationCandidateStore, InMemoryEvalDatasetStore
from helix_agent.protocol import (
    CandidateStatus,
    CurationCandidateRecord,
    EvalDatasetRecord,
)
from helix_agent.runtime.storage import InMemoryObjectStore
from orchestrator.trajectory import TrajectoryReader, TrajectoryRecord, TrajectoryRecorder

_EVAL_DIR = Path(__file__).resolve().parent
if str(_EVAL_DIR) not in _sys.path:
    _sys.path.insert(0, str(_EVAL_DIR))

from _capability import (  # type: ignore[import-not-found]  # noqa: E402
    CapabilityCaseResult,
    CapabilityReport,
)
from export_dataset import render_dataset_yaml  # type: ignore[import-not-found]  # noqa: E402

CAPABILITY = "J.12_learning"
METRIC_TYPE = "pass-rate"
THRESHOLD = {"pass_rate": 0.80}

_BASE = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)
_TENANT = uuid4()


@dataclass(frozen=True)
class LearningEvalCase:
    """One scripted J.12 behaviour case."""

    case_id: str
    scenario: str
    args: dict[str, Any] = field(default_factory=dict)


def _eval_record(
    *,
    source: str = "golden",
    name: str = "set",
    tenant_id: Any = None,
    agent_name: str = "reporter",
) -> EvalDatasetRecord:
    expected: dict[str, object] | None = {"answer": "ok"} if source != "trajectory" else None
    return EvalDatasetRecord(
        id=uuid4(),
        tenant_id=tenant_id or _TENANT,
        agent_name=agent_name,
        name=name,
        input={"prompt": "hi"},
        expected=expected,
        source=source,  # type: ignore[arg-type]
        created_at=_BASE,
        updated_at=_BASE,
    )


def _candidate_record(
    *,
    tenant_id: Any = None,
    agent_name: str = "reporter",
    trajectory_key: str | None = None,
    signal: str = "failed_outcome",
    outcome: str = "failed",
) -> CurationCandidateRecord:
    return CurationCandidateRecord(
        id=uuid4(),
        tenant_id=tenant_id or _TENANT,
        agent_name=agent_name,
        agent_version="1.0.0",
        thread_id=uuid4(),
        user_id=uuid4(),
        trajectory_key=trajectory_key or f"trajectories/{uuid4()}.jsonl",
        outcome=outcome,  # type: ignore[arg-type]
        signal=signal,  # type: ignore[arg-type]
        feedback_rating="down" if signal == "negative_feedback" else None,
        detected_at=_BASE,
    )


# ---------------------------------------------------------------------------
# classify — the curation worker's signal rule
# ---------------------------------------------------------------------------


async def _run_classify_negative_feedback() -> tuple[bool, str]:
    signal, rating = _classify("success", has_down=True, has_up=False)
    if signal != "negative_feedback" or rating != "down":
        return False, f"👎 should classify negative_feedback/down, got {signal}/{rating}"
    return True, ""


async def _run_classify_failed_outcome() -> tuple[bool, str]:
    signal, rating = _classify("failed", has_down=False, has_up=False)
    if signal != "failed_outcome" or rating is not None:
        return False, f"failed outcome should classify failed_outcome, got {signal}"
    return True, ""


async def _run_classify_max_steps_outcome() -> tuple[bool, str]:
    signal, _ = _classify("max_steps", has_down=False, has_up=False)
    if signal != "failed_outcome":
        return False, f"max_steps outcome should classify failed_outcome, got {signal}"
    return True, ""


async def _run_classify_positive_feedback() -> tuple[bool, str]:
    signal, rating = _classify("success", has_down=False, has_up=True)
    if signal != "positive_feedback" or rating != "up":
        return False, f"👍 should classify positive_feedback/up, got {signal}/{rating}"
    return True, ""


async def _run_classify_plain_success_skipped() -> tuple[bool, str]:
    signal, rating = _classify("success", has_down=False, has_up=False)
    if signal is not None or rating is not None:
        return False, "a plain success run with no feedback should not be a candidate"
    return True, ""


async def _run_classify_negative_outranks_failed() -> tuple[bool, str]:
    signal, _ = _classify("failed", has_down=True, has_up=False)
    if signal != "negative_feedback":
        return False, "a 👎 should outrank the failed outcome"
    return True, ""


# ---------------------------------------------------------------------------
# key parsing
# ---------------------------------------------------------------------------


async def _run_tenant_from_key_valid() -> tuple[bool, str]:
    tenant = uuid4()
    key = f"trajectories/{tenant}/failed/2026/05/22/{uuid4()}.jsonl"
    if _tenant_from_key(key) != tenant:
        return False, "tenant id should be parsed from the trajectory key"
    return True, ""


async def _run_tenant_from_key_malformed() -> tuple[bool, str]:
    if _tenant_from_key("not-a-key") is not None:
        return False, "a malformed key should yield no tenant"
    return True, ""


# ---------------------------------------------------------------------------
# spec — protocol invariant validators
# ---------------------------------------------------------------------------


async def _run_spec_golden_requires_expected() -> tuple[bool, str]:
    try:
        EvalDatasetRecord(
            id=uuid4(),
            tenant_id=_TENANT,
            agent_name="reporter",
            name="s",
            input={},
            expected=None,
            source="golden",
            created_at=_BASE,
            updated_at=_BASE,
        )
    except ValidationError:
        return True, ""
    return False, "a golden eval-dataset row without expected should fail validation"


async def _run_spec_regression_requires_expected() -> tuple[bool, str]:
    try:
        EvalDatasetRecord(
            id=uuid4(),
            tenant_id=_TENANT,
            agent_name="reporter",
            name="s",
            input={},
            expected=None,
            source="regression",
            created_at=_BASE,
            updated_at=_BASE,
        )
    except ValidationError:
        return True, ""
    return False, "a regression eval-dataset row without expected should fail validation"


async def _run_spec_trajectory_allows_no_expected() -> tuple[bool, str]:
    record = _eval_record(source="trajectory")
    if record.expected is not None:
        return False, "a trajectory-source row may leave expected unset"
    return True, ""


async def _run_spec_promoted_requires_dataset_id() -> tuple[bool, str]:
    try:
        CurationCandidateRecord(
            id=uuid4(),
            tenant_id=_TENANT,
            agent_name="reporter",
            agent_version="1.0.0",
            thread_id=uuid4(),
            trajectory_key="trajectories/x.jsonl",
            outcome="failed",
            signal="failed_outcome",
            status=CandidateStatus.PROMOTED,
            eval_dataset_id=None,
            reviewed_at=_BASE,
            detected_at=_BASE,
        )
    except ValidationError:
        return True, ""
    return False, "a promoted candidate without eval_dataset_id should fail validation"


# ---------------------------------------------------------------------------
# stores
# ---------------------------------------------------------------------------


async def _run_eval_store_create_get() -> tuple[bool, str]:
    store = InMemoryEvalDatasetStore()
    record = _eval_record()
    await store.create(record)
    got = await store.get(dataset_id=record.id, tenant_id=_TENANT)
    if got is None or got.id != record.id:
        return False, "eval-dataset create→get round-trip failed"
    return True, ""


async def _run_eval_store_cross_tenant_hidden() -> tuple[bool, str]:
    store = InMemoryEvalDatasetStore()
    record = _eval_record()
    await store.create(record)
    if await store.get(dataset_id=record.id, tenant_id=uuid4()) is not None:
        return False, "cross-tenant eval-dataset get must return None"
    return True, ""


async def _run_eval_store_count_by_tenant() -> tuple[bool, str]:
    store = InMemoryEvalDatasetStore()
    await store.create(_eval_record(name="a"))
    await store.create(_eval_record(name="b"))
    await store.create(_eval_record(tenant_id=uuid4(), name="c"))
    count = await store.count_by_tenant(tenant_id=_TENANT)
    if count != 2:
        return False, f"expected 2 rows for the tenant, got {count}"
    return True, ""


async def _run_candidate_store_upsert_dedup() -> tuple[bool, str]:
    store = InMemoryCurationCandidateStore()
    key = "trajectories/dedup.jsonl"
    first = await store.upsert(_candidate_record(trajectory_key=key))
    second = await store.upsert(_candidate_record(trajectory_key=key))
    if not (first is True and second is False):
        return False, "a trajectory must become a candidate at most once"
    return True, ""


async def _run_candidate_store_cross_tenant_hidden() -> tuple[bool, str]:
    store = InMemoryCurationCandidateStore()
    record = _candidate_record()
    await store.upsert(record)
    if await store.get(candidate_id=record.id, tenant_id=uuid4()) is not None:
        return False, "cross-tenant candidate get must return None"
    return True, ""


async def _run_candidate_list_filter_signal() -> tuple[bool, str]:
    store = InMemoryCurationCandidateStore()
    await store.upsert(_candidate_record(signal="failed_outcome"))
    await store.upsert(_candidate_record(signal="negative_feedback", outcome="success"))
    filtered = await store.list_for_review(tenant_id=_TENANT, signal="negative_feedback")
    if len(filtered) != 1 or filtered[0].signal != "negative_feedback":
        return False, "list_for_review must filter by signal"
    return True, ""


# ---------------------------------------------------------------------------
# trajectory reader
# ---------------------------------------------------------------------------


async def _run_trajectory_reader_roundtrip() -> tuple[bool, str]:
    store = InMemoryObjectStore()
    record = TrajectoryRecord(
        thread_id=uuid4(),
        tenant_id=_TENANT,
        outcome="failed",
        messages=[HumanMessage(content="hi")],
        finished_at=_BASE,
    )
    recorder = TrajectoryRecorder(object_store=store)
    key = recorder.key_for(record)
    await recorder.record(record)
    stored = await TrajectoryReader(object_store=store).read(key)
    if stored is None or stored.outcome != "failed" or stored.tenant_id != _TENANT:
        return False, "recorder→reader round-trip failed"
    return True, ""


async def _run_trajectory_reader_malformed() -> tuple[bool, str]:
    store = InMemoryObjectStore()
    await store.put("trajectories/bad.jsonl", b"not json", content_type="application/jsonl")
    if await TrajectoryReader(object_store=store).read("trajectories/bad.jsonl") is not None:
        return False, "a malformed trajectory object should be skipped"
    return True, ""


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


async def _run_export_renders_cases() -> tuple[bool, str]:
    record = _eval_record(source="golden")
    payload = yaml.safe_load(render_dataset_yaml([record]))
    cases = payload.get("cases", [])
    if len(cases) != 1 or cases[0]["id"] != str(record.id):
        return False, "render_dataset_yaml should emit one case per curated row"
    if cases[0]["source"] != "golden" or cases[0]["expected"] != {"answer": "ok"}:
        return False, "render_dataset_yaml should carry source + expected"
    return True, ""


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


_SCENARIOS: dict[str, Any] = {
    "classify_negative_feedback": _run_classify_negative_feedback,
    "classify_failed_outcome": _run_classify_failed_outcome,
    "classify_max_steps_outcome": _run_classify_max_steps_outcome,
    "classify_positive_feedback": _run_classify_positive_feedback,
    "classify_plain_success_skipped": _run_classify_plain_success_skipped,
    "classify_negative_outranks_failed": _run_classify_negative_outranks_failed,
    "tenant_from_key_valid": _run_tenant_from_key_valid,
    "tenant_from_key_malformed": _run_tenant_from_key_malformed,
    "spec_golden_requires_expected": _run_spec_golden_requires_expected,
    "spec_regression_requires_expected": _run_spec_regression_requires_expected,
    "spec_trajectory_allows_no_expected": _run_spec_trajectory_allows_no_expected,
    "spec_promoted_requires_dataset_id": _run_spec_promoted_requires_dataset_id,
    "eval_store_create_get": _run_eval_store_create_get,
    "eval_store_cross_tenant_hidden": _run_eval_store_cross_tenant_hidden,
    "eval_store_count_by_tenant": _run_eval_store_count_by_tenant,
    "candidate_store_upsert_dedup": _run_candidate_store_upsert_dedup,
    "candidate_store_cross_tenant_hidden": _run_candidate_store_cross_tenant_hidden,
    "candidate_list_filter_signal": _run_candidate_list_filter_signal,
    "trajectory_reader_roundtrip": _run_trajectory_reader_roundtrip,
    "trajectory_reader_malformed": _run_trajectory_reader_malformed,
    "export_renders_cases": _run_export_renders_cases,
}


async def _run_case(case: LearningEvalCase) -> CapabilityCaseResult:
    runner = _SCENARIOS.get(case.scenario)
    if runner is None:
        return CapabilityCaseResult(
            case_id=case.case_id,
            passed=False,
            notes=(f"unknown scenario {case.scenario!r}",),
        )
    passed, note = await runner()
    return CapabilityCaseResult(
        case_id=case.case_id,
        passed=passed,
        notes=(note,) if note else (),
    )


# ---------------------------------------------------------------------------
# Public surface — matches sibling eval modules (load_cases / evaluate_set).
# ---------------------------------------------------------------------------


def load_cases(path: Path) -> tuple[LearningEvalCase, ...]:
    """Parse the YAML dataset into :class:`LearningEvalCase` tuples."""
    with path.open("r", encoding="utf-8") as fh:
        payload = yaml.safe_load(fh)
    raw_cases = payload.get("cases", [])
    out: list[LearningEvalCase] = []
    for raw in raw_cases:
        out.append(
            LearningEvalCase(
                case_id=str(raw["id"]),
                scenario=str(raw["scenario"]),
                args=dict(raw.get("args", {})),
            )
        )
    return tuple(out)


async def evaluate_set(cases: Sequence[LearningEvalCase]) -> CapabilityReport:
    """Run all cases sequentially; produce the capability report."""
    per_case: list[CapabilityCaseResult] = []
    for case in cases:
        per_case.append(await _run_case(case))
    sample_size = len(per_case)
    passed = sum(1 for r in per_case if r.passed)
    pass_rate = passed / sample_size if sample_size else 0.0
    threshold = THRESHOLD["pass_rate"]
    status = "PASS" if pass_rate >= threshold and sample_size > 0 else "FAIL"
    return CapabilityReport(
        capability=CAPABILITY,
        metric_type=METRIC_TYPE,
        sample_size=sample_size,
        threshold=dict(THRESHOLD),
        aggregate_score={"pass_rate": pass_rate},
        status=status,
        per_case=tuple(per_case),
    )


__all__ = [
    "CAPABILITY",
    "METRIC_TYPE",
    "THRESHOLD",
    "LearningEvalCase",
    "evaluate_set",
    "load_cases",
]
