"""Stream N platform admin eval — capability check on ``ensure_tenant_scope``.

Exercises the pure decision matrix of cross-tenant access control:

* tenant_admin + home tenant            → SingleTenant
* tenant_admin + other tenant           → 403 TENANT_NOT_ALLOWED
* tenant_admin + ``tenant_id=*``        → 403 CROSS_TENANT_FORBIDDEN
* system_admin + home tenant            → SingleTenant
* system_admin + other tenant           → SingleTenant (tenant_switch audit)
* system_admin + ``tenant_id=*``        → CrossTenant (cross_tenant audit)

Mirrors the J.14 ``per_user_isolation`` pattern: pass-rate threshold
1.00 — cross-tenant authorization is security-class and "close enough"
is not acceptable.
"""

from __future__ import annotations

import sys as _sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from pathlib import Path as _Path
from typing import Any, Literal, cast
from uuid import UUID

import yaml
from fastapi import HTTPException

from control_plane.audit import build_default_audit_logger
from control_plane.tenant_scope import (
    CrossTenant,
    SingleTenant,
    ensure_tenant_scope,
)
from expert_work.protocol import Principal
from expert_work.runtime.audit.logger import AuditLogger

_EVAL_DIR = _Path(__file__).resolve().parent
if str(_EVAL_DIR) not in _sys.path:
    _sys.path.insert(0, str(_EVAL_DIR))

from _capability import (  # type: ignore[import-not-found]  # noqa: E402
    CapabilityCaseResult,
    CapabilityReport,
    JudgeCompletionFn,
)

CAPABILITY = "N.0_platform_admin"
METRIC_TYPE = "pass-rate"
THRESHOLD = {"pass_rate": 1.00}


_HOME_TENANT = UUID("00000000-0000-0000-0000-0000000000a1")
_OTHER_TENANT = UUID("00000000-0000-0000-0000-0000000000b2")


_Outcome = Literal["single_home", "single_other", "cross", "forbid_cross", "forbid_other"]


@dataclass(frozen=True)
class ScopeCase:
    """One ``ensure_tenant_scope`` decision case.

    ``is_system_admin`` flips both that flag and ``allowed_tenants``
    (``"*"``) to mirror what :func:`resolve_system_admin` produces in
    the middleware. ``requested_tenant_id``: ``"home"`` / ``"other"`` /
    ``"star"`` / ``"none"``.
    """

    case_id: str
    is_system_admin: bool
    requested: Literal["home", "other", "star", "none"]
    expected: _Outcome


def _build_principal(case: ScopeCase) -> Principal:
    allowed: tuple[UUID, ...] | Literal["*"]
    allowed = "*" if case.is_system_admin else (_HOME_TENANT,)
    return Principal(
        subject_id="00000000-0000-0000-0000-0000000000aa",
        subject_type="user",
        tenant_id=_HOME_TENANT,
        roles=("system_admin",) if case.is_system_admin else ("admin",),
        scopes=(),
        auth_method="jwt",
        allowed_tenants=allowed,
        is_system_admin=case.is_system_admin,
    )


def _requested_value(case: ScopeCase) -> UUID | Literal["*"] | None:
    if case.requested == "home":
        return _HOME_TENANT
    if case.requested == "other":
        return _OTHER_TENANT
    if case.requested == "star":
        return "*"
    return None


def _build_audit() -> AuditLogger:
    return build_default_audit_logger()


def _classify(
    *,
    case: ScopeCase,
    resolution: SingleTenant | CrossTenant | None,
    exc: HTTPException | None,
) -> _Outcome:
    if exc is not None:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        code = detail.get("code")
        if code == "CROSS_TENANT_FORBIDDEN":
            return "forbid_cross"
        if code == "TENANT_NOT_ALLOWED":
            return "forbid_other"
        # Unexpected exception code — surface as a forbid_other so the
        # case fails loudly rather than silently passing.
        return "forbid_other"
    if isinstance(resolution, CrossTenant):
        return "cross"
    if isinstance(resolution, SingleTenant):
        if resolution.tenant_id == _HOME_TENANT:
            return "single_home"
        return "single_other"
    raise AssertionError(f"unreachable for case {case.case_id}")


async def _run_case(case: ScopeCase) -> CapabilityCaseResult:
    principal = _build_principal(case)
    audit = _build_audit()
    requested = _requested_value(case)
    resolution: SingleTenant | CrossTenant | None = None
    exc: HTTPException | None = None
    try:
        resolution = await ensure_tenant_scope(
            principal,
            requested,
            audit,
            trace_id=None,
            endpoint="eval:platform_admin",
        )
    except HTTPException as raised:
        exc = raised
    got = _classify(case=case, resolution=resolution, exc=exc)
    passed = got == case.expected
    notes: tuple[str, ...] = ()
    if not passed:
        notes = (f"expected={case.expected} got={got}",)
    return CapabilityCaseResult(case_id=case.case_id, passed=passed, notes=notes)


async def evaluate_set(
    cases: Sequence[ScopeCase],
    *,
    judge: JudgeCompletionFn | None = None,
    rerun_count: int = 3,
) -> CapabilityReport:
    """Run the scope decisions; pass-rate must be 1.00 (security-class)."""
    _ = judge, rerun_count
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


def load_cases(path: Path) -> list[ScopeCase]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    out: list[ScopeCase] = []
    for entry in raw.get("cases", []):
        out.append(_parse_case(entry))
    return out


_EXPECTED_OUTCOMES: frozenset[str] = frozenset(
    {"single_home", "single_other", "cross", "forbid_cross", "forbid_other"}
)
_REQUESTED_VALUES: frozenset[str] = frozenset({"home", "other", "star", "none"})


def _parse_case(entry: dict[str, Any]) -> ScopeCase:
    requested = str(entry["requested"])
    if requested not in _REQUESTED_VALUES:
        raise ValueError(f"unknown requested value {requested!r}")
    expected = str(entry["expected"])
    if expected not in _EXPECTED_OUTCOMES:
        raise ValueError(f"unknown expected outcome {expected!r}")
    return ScopeCase(
        case_id=str(entry["id"]),
        is_system_admin=bool(entry["is_system_admin"]),
        requested=cast(Any, requested),
        expected=cast(Any, expected),
    )


__all__ = [
    "CAPABILITY",
    "METRIC_TYPE",
    "THRESHOLD",
    "ScopeCase",
    "evaluate_set",
    "load_cases",
]
