"""J.15 persistent volume eval — Stream J.13a (M0 baseline).

Drives the J.15 lifecycle state machine + quota guard against a fresh
:class:`InMemoryUserWorkspaceStore` per case. Three test families,
deterministic and CI-friendly:

* lifecycle transitions — resolve / soft_delete / mark_archived,
  including the state-machine invariant that ``mark_archived`` requires
  a prior ``soft_delete``.
* listing — ``list_pending_archive`` / ``list_active`` return the
  expected subsets after state transitions.
* quota — :meth:`QuotaEnforcer.check` rejects soft-deleted workspaces
  and quota-exceeded acquires (Mini-ADR J-29 第 1 项 + J-36).

Per Mini-ADR J-37, J.15 metric is ``pass-rate`` with threshold ≥ 0.90
(§ 18.3). Cross-host scheduling is M1 (Mini-ADR J-29 (4)) — out of
scope here.
"""

from __future__ import annotations

import sys as _sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from pathlib import Path as _Path
from typing import Any, Literal, cast
from uuid import UUID

import yaml

from expert_work.persistence.workspace.memory import InMemoryUserWorkspaceStore
from expert_work.protocol import UserWorkspace
from sandbox_supervisor.domain import (
    WorkspaceDeletedError,
    WorkspaceQuotaExceededError,
)
from sandbox_supervisor.quota_enforcer import QuotaEnforcer

_EVAL_DIR = _Path(__file__).resolve().parent
if str(_EVAL_DIR) not in _sys.path:
    _sys.path.insert(0, str(_EVAL_DIR))

from _capability import (  # type: ignore[import-not-found]  # noqa: E402
    CapabilityCaseResult,
    CapabilityReport,
    JudgeCompletionFn,
)

CAPABILITY = "J.15_persistent_volume"
METRIC_TYPE = "pass-rate"
THRESHOLD = {"pass_rate": 0.90}

# Fixed tenant + user — caller_owns_thread / RLS are tested in J.14, so
# every J.15 case shares one (tenant, user) identity.
_TENANT = UUID("00000000-0000-0000-0000-0000000000a1")
_USER = UUID("00000000-0000-0000-0000-0000000000b1")


TestAction = Literal[
    "resolve",
    "soft_delete",
    "mark_archived",
    "list_pending_archive",
    "list_active",
    "quota_check",
]
ExpectedOutcome = Literal[
    "ok",
    "error_workspace_deleted",
    "error_quota_exceeded",
    "error_state_invariant",
]


@dataclass(frozen=True)
class WorkspaceCase:
    """One persistent-volume lifecycle / quota case."""

    case_id: str
    setup: tuple[dict[str, Any], ...]
    test_action: TestAction
    test_args: dict[str, Any] = field(default_factory=dict)
    expected_outcome: ExpectedOutcome = "ok"
    expected: dict[str, Any] = field(default_factory=dict)


class _RecorderAudit:
    """Captures audit entries; QuotaEnforcer needs *some* AuditSink."""

    def __init__(self) -> None:
        self.entries: list[Any] = []

    async def write(self, entry: Any) -> None:
        self.entries.append(entry)


def _require_resolved(workspace_id: UUID | None, action: str) -> UUID:
    if workspace_id is None:
        msg = f"setup action {action!r} requires a prior resolve"
        raise ValueError(msg)
    return workspace_id


async def _apply_setup(store: InMemoryUserWorkspaceStore, steps: Sequence[dict[str, Any]]) -> None:
    """Walk the setup ops on a fresh store. Idempotent / order-sensitive."""
    workspace_id: UUID | None = None
    for step in steps:
        action = step["action"]
        if action == "resolve":
            ws = await store.resolve(tenant_id=_TENANT, user_id=_USER)
            workspace_id = ws.id
        elif action == "soft_delete":
            wid = _require_resolved(workspace_id, action)
            await store.soft_delete(workspace_id=wid, now=datetime.now(UTC))
        elif action == "mark_archived":
            wid = _require_resolved(workspace_id, action)
            await store.mark_archived(
                workspace_id=wid,
                archived_object_key=str(step.get("archived_object_key", "archive/key")),
            )
        elif action == "set_size":
            wid = _require_resolved(workspace_id, action)
            await store.update_size(
                workspace_id=wid,
                size_bytes=int(step["size_bytes"]),
            )
        else:
            msg = f"unknown setup action {action!r}"
            raise ValueError(msg)


async def _run_test_action(
    store: InMemoryUserWorkspaceStore, case: WorkspaceCase
) -> tuple[Any, Exception | None]:
    """Execute the case's test action; return ``(result, exception)``."""
    try:
        if case.test_action == "resolve":
            result: Any = await store.resolve(tenant_id=_TENANT, user_id=_USER)
        elif case.test_action == "soft_delete":
            ws = await store.resolve(tenant_id=_TENANT, user_id=_USER)
            await store.soft_delete(workspace_id=ws.id, now=datetime.now(UTC))
            result = await store.resolve(tenant_id=_TENANT, user_id=_USER)
        elif case.test_action == "mark_archived":
            ws = await store.resolve(tenant_id=_TENANT, user_id=_USER)
            await store.mark_archived(
                workspace_id=ws.id,
                archived_object_key=str(case.test_args.get("archived_object_key", "k")),
            )
            result = await store.resolve(tenant_id=_TENANT, user_id=_USER)
        elif case.test_action == "list_pending_archive":
            result = await store.list_pending_archive()
        elif case.test_action == "list_active":
            result = await store.list_active()
        elif case.test_action == "quota_check":
            ws = await store.resolve(tenant_id=_TENANT, user_id=_USER)
            override_size = case.test_args.get("override_size_bytes")
            override_limit = case.test_args.get("override_size_limit_bytes")
            workspace_for_check = (
                ws.model_copy(
                    update={
                        k: v
                        for k, v in {
                            "size_bytes": override_size,
                            "size_limit_bytes": override_limit,
                        }.items()
                        if v is not None
                    }
                )
                if override_size is not None or override_limit is not None
                else ws
            )
            enforcer = QuotaEnforcer(
                workspace_store=store,
                audit=cast(Any, _RecorderAudit()),
                docker=cast(Any, None),
                measure_image="unused",
                service_name="eval",
            )
            await enforcer.check(workspace=workspace_for_check)
            result = workspace_for_check
    except WorkspaceDeletedError as exc:
        return None, exc
    except WorkspaceQuotaExceededError as exc:
        return None, exc
    except ValueError as exc:
        return None, exc
    return result, None


def _classify_outcome(exc: Exception | None) -> ExpectedOutcome:
    if exc is None:
        return "ok"
    if isinstance(exc, WorkspaceDeletedError):
        return "error_workspace_deleted"
    if isinstance(exc, WorkspaceQuotaExceededError):
        return "error_quota_exceeded"
    return "error_state_invariant"


def _check_expectations(result: Any, case: WorkspaceCase) -> tuple[bool, tuple[str, ...]]:
    """Verify the optional ``expected`` payload (size, count, deleted_at)."""
    notes: list[str] = []
    expected = case.expected
    if "list_length" in expected and isinstance(result, list):
        actual = len(result)
        want = int(expected["list_length"])
        if actual != want:
            notes.append(f"list_length: expected {want} got {actual}")
    if "deleted_at_is_none" in expected and isinstance(result, UserWorkspace):
        want_none = bool(expected["deleted_at_is_none"])
        got_none = result.deleted_at is None
        if want_none != got_none:
            notes.append(f"deleted_at_is_none: expected {want_none} got {got_none}")
    if "archived_object_key_is_set" in expected and isinstance(result, UserWorkspace):
        want_set = bool(expected["archived_object_key_is_set"])
        got_set = result.archived_object_key is not None
        if want_set != got_set:
            notes.append(f"archived_object_key_is_set: expected {want_set} got {got_set}")
    return (not notes), tuple(notes)


async def _run_case(case: WorkspaceCase) -> CapabilityCaseResult:
    store = InMemoryUserWorkspaceStore()
    await _apply_setup(store, case.setup)
    result, exc = await _run_test_action(store, case)

    actual_outcome = _classify_outcome(exc)
    notes: list[str] = []
    if actual_outcome != case.expected_outcome:
        notes.append(
            f"outcome: expected {case.expected_outcome!r} got {actual_outcome!r}"
            + (f" exc={type(exc).__name__}: {exc}" if exc else "")
        )
        return CapabilityCaseResult(case_id=case.case_id, passed=False, notes=tuple(notes))

    expectations_passed, expectation_notes = _check_expectations(result, case)
    notes.extend(expectation_notes)
    return CapabilityCaseResult(
        case_id=case.case_id,
        passed=expectations_passed,
        notes=tuple(notes),
    )


async def evaluate_set(
    cases: Sequence[WorkspaceCase],
    *,
    judge: JudgeCompletionFn | None = None,
    rerun_count: int = 3,
) -> CapabilityReport:
    """Drive every case on a fresh store; aggregate pass-rate."""
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


def load_cases(path: Path) -> list[WorkspaceCase]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    out: list[WorkspaceCase] = []
    for entry in raw.get("cases", []):
        out.append(_parse_case(entry))
    return out


def _parse_case(entry: dict[str, Any]) -> WorkspaceCase:
    return WorkspaceCase(
        case_id=str(entry["id"]),
        setup=tuple(dict(s) for s in entry.get("setup", [])),
        test_action=cast(Any, entry["test_action"]),
        test_args=dict(entry.get("test_args", {})),
        expected_outcome=cast(Any, entry.get("expected_outcome", "ok")),
        expected=dict(entry.get("expected", {})),
    )


__all__ = [
    "CAPABILITY",
    "METRIC_TYPE",
    "THRESHOLD",
    "WorkspaceCase",
    "evaluate_set",
    "load_cases",
]
