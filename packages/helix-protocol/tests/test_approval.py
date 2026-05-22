"""Unit tests for J.8 approval DTOs — Stream J.8 (Mini-ADR J-24)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from helix_agent.protocol import ApprovalDecision, ApprovalRequest


def _now() -> datetime:
    return datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)


def test_approval_request_round_trips() -> None:
    req = ApprovalRequest(
        request_id="abc123",
        node="tools",
        reason_kind="policy_gate",
        action_summary="send_email to ops@example.com",
        proposed_args={"to": "ops@example.com", "body": "hi"},
        requested_at=_now(),
        timeout_at=_now() + timedelta(hours=24),
    )
    dumped = req.model_dump()
    restored = ApprovalRequest.model_validate(dumped)
    assert restored == req
    assert restored.reason_kind == "policy_gate"


def test_approval_request_is_frozen() -> None:
    req = ApprovalRequest(
        request_id="x",
        node="tools",
        reason_kind="risk_confirmation",
        action_summary="rm -rf /tmp/work",
        requested_at=_now(),
        timeout_at=_now() + timedelta(hours=24),
    )
    with pytest.raises(ValidationError):
        req.action_summary = "changed"


def test_approval_request_rejects_unknown_reason_kind() -> None:
    with pytest.raises(ValidationError):
        ApprovalRequest(
            request_id="x",
            node="tools",
            reason_kind="not_a_kind",  # type: ignore[arg-type]
            action_summary="x",
            requested_at=_now(),
            timeout_at=_now(),
        )


def test_approval_request_rejects_empty_action_summary() -> None:
    with pytest.raises(ValidationError):
        ApprovalRequest(
            request_id="x",
            node="tools",
            reason_kind="missing_info",
            action_summary="",
            requested_at=_now(),
            timeout_at=_now(),
        )


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_approval_decision_simple_verdicts(decision: str) -> None:
    dec = ApprovalDecision(decision=decision, decided_by="user-a")  # type: ignore[arg-type]
    assert dec.decision == decision
    assert dec.modified_args is None


def test_approval_decision_modify_requires_modified_args() -> None:
    with pytest.raises(ValidationError, match="modified_args"):
        ApprovalDecision(decision="modify", decided_by="user-a")


def test_approval_decision_modify_round_trips() -> None:
    dec = ApprovalDecision(
        decision="modify",
        modified_args={"to": "safe@example.com"},
        decided_by="user-a",
    )
    assert dec.modified_args == {"to": "safe@example.com"}


def test_approval_decision_rejects_modified_args_on_non_modify() -> None:
    """``modified_args`` only makes sense for ``modify`` — reject it elsewhere."""
    with pytest.raises(ValidationError, match="modified_args"):
        ApprovalDecision(
            decision="approve",
            modified_args={"to": "x"},
            decided_by="user-a",
        )


def test_approval_decision_timeout_reason() -> None:
    """The timeout job emits a reject with ``decided_by='system'``."""
    dec = ApprovalDecision(decision="reject", decided_by="system", reason="timeout")
    assert dec.reason == "timeout"
