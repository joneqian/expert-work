"""J.10 调度 / 触发 eval — Stream J.13a (M0 baseline) closeout.

Mini-ADR J-26 / J-42 + STREAM-J-DESIGN § 16 behaviour lock. Drives the
J.10 pieces against scripted, fully-deterministic cases:

* **cron** — ``_next_fire`` / ``_is_cron_due`` schedule maths.
* **backoff** — the DLQ retry backoff schedule (``_backoff_for``).
* **webhook** — per-trigger secret hashing + constant-time compare.
* **spec** — :class:`TriggerSpec` cron/webhook config validation.
* **store** — :class:`InMemoryTriggerStore` / ``InMemoryTriggerRunStore``
  create / get / cross-tenant hiding / cron count / DLQ list filters.

Per Mini-ADR J-37 J.10 metric is deterministic ``pass_rate``; the
baseline threshold is ≥ 0.90 — achievable = 1.00 on these scripted
all-deterministic cases.
"""

from __future__ import annotations

import hmac
import sys as _sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from pydantic import ValidationError

from control_plane.api.triggers import _hash_secret
from control_plane.scheduler import _backoff_for, _is_cron_due, _next_fire
from expert_work.persistence import InMemoryTriggerRunStore, InMemoryTriggerStore
from expert_work.protocol import (
    TriggerRecord,
    TriggerRunRecord,
    TriggerRunStatus,
    TriggerSpec,
)

_EVAL_DIR = Path(__file__).resolve().parent
if str(_EVAL_DIR) not in _sys.path:
    _sys.path.insert(0, str(_EVAL_DIR))

from _capability import (  # type: ignore[import-not-found]  # noqa: E402
    CapabilityCaseResult,
    CapabilityReport,
)

CAPABILITY = "J.10_trigger"
METRIC_TYPE = "pass-rate"
THRESHOLD = {"pass_rate": 0.90}

_BASE = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)
_TENANT = uuid4()


@dataclass(frozen=True)
class TriggerEvalCase:
    """One scripted J.10 behaviour case."""

    case_id: str
    scenario: str
    args: dict[str, Any] = field(default_factory=dict)


def _trigger(
    *,
    tenant_id: Any = None,
    name: str = "nightly",
    kind: str = "cron",
    enabled: bool = True,
    last_fired_at: datetime | None = None,
    created_at: datetime = _BASE,
) -> TriggerRecord:
    config: dict[str, object] = {"expr": "0 9 * * *"} if kind == "cron" else {}
    return TriggerRecord(
        id=uuid4(),
        tenant_id=tenant_id or _TENANT,
        agent_name="reporter",
        agent_version="1.0.0",
        name=name,
        kind=kind,  # type: ignore[arg-type]
        config=config,
        enabled=enabled,
        source="api",
        last_fired_at=last_fired_at,
        created_at=created_at,
        updated_at=created_at,
    )


def _run_record(
    *, status: TriggerRunStatus, next_retry_at: datetime | None = None
) -> TriggerRunRecord:
    return TriggerRunRecord(
        id=uuid4(),
        tenant_id=_TENANT,
        trigger_id=uuid4(),
        run_id=uuid4(),
        status=status,
        attempt=1,
        next_retry_at=next_retry_at,
        triggered_at=_BASE,
    )


# ---------------------------------------------------------------------------
# cron schedule maths
# ---------------------------------------------------------------------------


async def _run_cron_next_fire() -> tuple[bool, str]:
    after = datetime(2026, 5, 22, 8, 0, 0, tzinfo=UTC)
    nxt = _next_fire("0 9 * * *", after)
    if nxt != datetime(2026, 5, 22, 9, 0, 0, tzinfo=UTC):
        return False, f"expected 09:00, got {nxt}"
    return True, ""


async def _run_cron_is_due_true() -> tuple[bool, str]:
    trig = _trigger(created_at=datetime(2026, 5, 22, 8, 0, tzinfo=UTC))
    if not _is_cron_due(trig, now=datetime(2026, 5, 22, 10, 0, tzinfo=UTC)):
        return False, "trigger past its 09:00 slot should be due"
    return True, ""


async def _run_cron_is_due_false() -> tuple[bool, str]:
    trig = _trigger(created_at=datetime(2026, 5, 22, 8, 0, tzinfo=UTC))
    if _is_cron_due(trig, now=datetime(2026, 5, 22, 8, 30, tzinfo=UTC)):
        return False, "trigger before its 09:00 slot should not be due"
    return True, ""


async def _run_cron_not_due_after_fire() -> tuple[bool, str]:
    fired = datetime(2026, 5, 22, 9, 0, 0, tzinfo=UTC)
    trig = _trigger(last_fired_at=fired)
    if _is_cron_due(trig, now=fired + timedelta(minutes=30)):
        return False, "daily trigger just fired should not be due again"
    return True, ""


# ---------------------------------------------------------------------------
# DLQ backoff schedule
# ---------------------------------------------------------------------------


async def _run_backoff_first_retry() -> tuple[bool, str]:
    if _backoff_for(1) != 60:
        return False, f"first retry should wait 60s, got {_backoff_for(1)}"
    return True, ""


async def _run_backoff_escalates() -> tuple[bool, str]:
    if not (_backoff_for(1) < _backoff_for(2) < _backoff_for(3) < _backoff_for(4)):
        return False, "backoff must escalate 1m→5m→30m→2h"
    return True, ""


async def _run_backoff_clamps() -> tuple[bool, str]:
    if _backoff_for(99) != _backoff_for(5):
        return False, "an out-of-range attempt should clamp to the last slot"
    return True, ""


# ---------------------------------------------------------------------------
# webhook secret
# ---------------------------------------------------------------------------


async def _run_webhook_secret_roundtrip() -> tuple[bool, str]:
    token = "tok-abc123"  # noqa: S105 — eval fixture, not a credential
    if not hmac.compare_digest(_hash_secret(token), _hash_secret(token)):
        return False, "the same secret must hash-compare equal"
    return True, ""


async def _run_webhook_secret_mismatch() -> tuple[bool, str]:
    if hmac.compare_digest(_hash_secret("right"), _hash_secret("wrong")):
        return False, "different secrets must not hash-compare equal"
    return True, ""


# ---------------------------------------------------------------------------
# TriggerSpec validation
# ---------------------------------------------------------------------------


async def _run_spec_cron_requires_expr() -> tuple[bool, str]:
    try:
        TriggerSpec(name="x", kind="cron", config={})
    except ValidationError:
        return True, ""
    return False, "a cron TriggerSpec without an expr should fail validation"


async def _run_spec_cron_rejects_blank_expr() -> tuple[bool, str]:
    try:
        TriggerSpec(name="x", kind="cron", config={"expr": "   "})
    except ValidationError:
        return True, ""
    return False, "a cron TriggerSpec with a blank expr should fail validation"


async def _run_spec_webhook_needs_no_expr() -> tuple[bool, str]:
    spec = TriggerSpec(name="x", kind="webhook", config={})
    if spec.kind != "webhook":
        return False, "a webhook TriggerSpec needs no expr"
    return True, ""


# ---------------------------------------------------------------------------
# stores
# ---------------------------------------------------------------------------


async def _run_store_create_get() -> tuple[bool, str]:
    store = InMemoryTriggerStore()
    trig = _trigger()
    await store.create(trig)
    got = await store.get(trigger_id=trig.id, tenant_id=_TENANT)
    if got is None or got.id != trig.id:
        return False, "create→get round-trip failed"
    return True, ""


async def _run_store_cross_tenant_hidden() -> tuple[bool, str]:
    store = InMemoryTriggerStore()
    trig = _trigger()
    await store.create(trig)
    if await store.get(trigger_id=trig.id, tenant_id=uuid4()) is not None:
        return False, "cross-tenant get must return None"
    return True, ""


async def _run_store_count_cron() -> tuple[bool, str]:
    store = InMemoryTriggerStore()
    await store.create(_trigger(name="c1", kind="cron"))
    await store.create(_trigger(name="c2", kind="cron"))
    await store.create(_trigger(name="w1", kind="webhook"))
    count = await store.count_cron_by_tenant(tenant_id=_TENANT)
    if count != 2:
        return False, f"expected 2 cron triggers, got {count}"
    return True, ""


async def _run_run_store_list_fired() -> tuple[bool, str]:
    store = InMemoryTriggerRunStore()
    await store.create(_run_record(status=TriggerRunStatus.FIRED))
    await store.create(_run_record(status=TriggerRunStatus.SUCCEEDED))
    fired = await store.list_fired()
    if len(fired) != 1 or fired[0].status is not TriggerRunStatus.FIRED:
        return False, "list_fired must return only fired rows"
    return True, ""


async def _run_run_store_list_due_retries() -> tuple[bool, str]:
    store = InMemoryTriggerRunStore()
    now = datetime(2026, 5, 22, 15, 0, 0, tzinfo=UTC)
    await store.create(
        _run_record(status=TriggerRunStatus.RETRYING, next_retry_at=now - timedelta(minutes=1))
    )
    await store.create(
        _run_record(status=TriggerRunStatus.RETRYING, next_retry_at=now + timedelta(hours=1))
    )
    due = await store.list_due_retries(before=now)
    if len(due) != 1:
        return False, f"expected 1 due retry, got {len(due)}"
    return True, ""


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


_SCENARIOS: dict[str, Any] = {
    "cron_next_fire": _run_cron_next_fire,
    "cron_is_due_true": _run_cron_is_due_true,
    "cron_is_due_false": _run_cron_is_due_false,
    "cron_not_due_after_fire": _run_cron_not_due_after_fire,
    "backoff_first_retry": _run_backoff_first_retry,
    "backoff_escalates": _run_backoff_escalates,
    "backoff_clamps": _run_backoff_clamps,
    "webhook_secret_roundtrip": _run_webhook_secret_roundtrip,
    "webhook_secret_mismatch": _run_webhook_secret_mismatch,
    "spec_cron_requires_expr": _run_spec_cron_requires_expr,
    "spec_cron_rejects_blank_expr": _run_spec_cron_rejects_blank_expr,
    "spec_webhook_needs_no_expr": _run_spec_webhook_needs_no_expr,
    "store_create_get": _run_store_create_get,
    "store_cross_tenant_hidden": _run_store_cross_tenant_hidden,
    "store_count_cron": _run_store_count_cron,
    "run_store_list_fired": _run_run_store_list_fired,
    "run_store_list_due_retries": _run_run_store_list_due_retries,
}


async def _run_case(case: TriggerEvalCase) -> CapabilityCaseResult:
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


def load_cases(path: Path) -> tuple[TriggerEvalCase, ...]:
    """Parse the YAML dataset into :class:`TriggerEvalCase` tuples."""
    with path.open("r", encoding="utf-8") as fh:
        payload = yaml.safe_load(fh)
    raw_cases = payload.get("cases", [])
    out: list[TriggerEvalCase] = []
    for raw in raw_cases:
        out.append(
            TriggerEvalCase(
                case_id=str(raw["id"]),
                scenario=str(raw["scenario"]),
                args=dict(raw.get("args", {})),
            )
        )
    return tuple(out)


async def evaluate_set(cases: Sequence[TriggerEvalCase]) -> CapabilityReport:
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
    "TriggerEvalCase",
    "evaluate_set",
    "load_cases",
]
