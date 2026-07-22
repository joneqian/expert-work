from __future__ import annotations

from expert_work.protocol import AuditAction


def test_trigger_completed_wire_value() -> None:
    assert AuditAction.TRIGGER_COMPLETED.value == "trigger:completed"


def test_trigger_failed_wire_value() -> None:
    assert AuditAction.TRIGGER_FAILED.value == "trigger:failed"
