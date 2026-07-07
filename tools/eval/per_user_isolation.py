"""J.14 per-user isolation eval — Stream J.13a (M0 baseline).

Cross-user / admin-bypass / machine-principal / unowned-thread cases
exercised against :func:`control_plane.api._user_scope.caller_owns_thread`
— a pure decision function so the eval is fully deterministic.

Per Mini-ADR J-37, J.14 metric is ``pass-rate`` with the threshold
fixed at **1.00** — isolation is a security-class capability where
"close enough" is not acceptable (§ 18.3).
"""

from __future__ import annotations

import sys as _sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from pathlib import Path as _Path
from typing import Any, cast
from uuid import UUID

import yaml

from control_plane.api._user_scope import caller_owns_thread
from expert_work.protocol import Principal, ThreadMeta

_EVAL_DIR = _Path(__file__).resolve().parent
if str(_EVAL_DIR) not in _sys.path:
    _sys.path.insert(0, str(_EVAL_DIR))

from _capability import (  # type: ignore[import-not-found]  # noqa: E402
    CapabilityCaseResult,
    CapabilityReport,
    JudgeCompletionFn,
)

CAPABILITY = "J.14_per_user_isolation"
METRIC_TYPE = "pass-rate"
THRESHOLD = {"pass_rate": 1.00}


@dataclass(frozen=True)
class IsolationCase:
    """One isolation decision case.

    ``meta_user_id`` ``None`` represents a pre-J.14 (legacy / unowned)
    thread. ``caller_user_id`` ``None`` represents a machine principal
    that has no per-user identity. ``principal_roles`` is the JWT
    ``roles`` claim — ``("admin",)`` triggers the
    :func:`is_admin` tenant-wide bypass.
    """

    case_id: str
    meta_user_id: UUID | None
    caller_user_id: UUID | None
    principal_subject_type: str
    principal_roles: tuple[str, ...]
    expected_allowed: bool


# Fixed tenant — caller_owns_thread does not consult tenant_id; tenant
# scoping is enforced upstream of this decision.
_TENANT = UUID("00000000-0000-0000-0000-000000000001")


def _build_principal(case: IsolationCase) -> Principal:
    return Principal(
        subject_id="test-subject",
        subject_type=cast(Any, case.principal_subject_type),
        tenant_id=_TENANT,
        roles=case.principal_roles,
        scopes=(),
        auth_method="jwt",
    )


def _build_meta(case: IsolationCase) -> ThreadMeta:
    return ThreadMeta(
        thread_id=UUID("00000000-0000-0000-0000-0000000000aa"),
        tenant_id=_TENANT,
        user_id=case.meta_user_id,
        created_by="test",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


async def _run_case(case: IsolationCase) -> CapabilityCaseResult:
    principal = _build_principal(case)
    meta = _build_meta(case)
    got = caller_owns_thread(
        meta=meta,
        caller_user_id=case.caller_user_id,
        principal=principal,
    )
    passed = got is case.expected_allowed
    notes: tuple[str, ...] = ()
    if not passed:
        notes = (f"expected_allowed={case.expected_allowed} got={got}",)
    return CapabilityCaseResult(case_id=case.case_id, passed=passed, notes=notes)


async def evaluate_set(
    cases: Sequence[IsolationCase],
    *,
    judge: JudgeCompletionFn | None = None,
    rerun_count: int = 3,
) -> CapabilityReport:
    """Run isolation decisions; require pass-rate == 1.00 (isolation = no slack)."""
    per_case: list[CapabilityCaseResult] = []
    for case in cases:
        per_case.append(await _run_case(case))
    sample = len(per_case)
    pass_rate = sum(1 for r in per_case if r.passed) / sample if sample else 0.0
    status = "PASS" if pass_rate >= THRESHOLD["pass_rate"] else "FAIL"
    return CapabilityReport(
        capability=CAPABILITY,
        metric_type=METRIC_TYPE,
        sample_size=sample,
        threshold=THRESHOLD,
        aggregate_score={"pass_rate": pass_rate},
        status=cast(Any, status),
        per_case=tuple(per_case),
    )


def load_cases(path: Path) -> list[IsolationCase]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    out: list[IsolationCase] = []
    for entry in raw.get("cases", []):
        out.append(_parse_case(entry))
    return out


def _parse_case(entry: dict[str, Any]) -> IsolationCase:
    meta_uid = entry.get("meta_user_id")
    caller_uid = entry.get("caller_user_id")
    principal = entry["principal"]
    return IsolationCase(
        case_id=str(entry["id"]),
        meta_user_id=UUID(meta_uid) if meta_uid is not None else None,
        caller_user_id=UUID(caller_uid) if caller_uid is not None else None,
        principal_subject_type=str(principal["subject_type"]),
        principal_roles=tuple(str(r) for r in principal.get("roles", ())),
        expected_allowed=bool(entry["expected_allowed"]),
    )


__all__ = [
    "CAPABILITY",
    "METRIC_TYPE",
    "THRESHOLD",
    "IsolationCase",
    "evaluate_set",
    "load_cases",
]
