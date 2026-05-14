"""Unit tests for :class:`RetentionCleanupJob` construction + CleanupReport."""

from __future__ import annotations

import pytest

from retention_cleanup_job.job import CleanupReport, RetentionCleanupJob


def test_cleanup_report_default_is_all_zero() -> None:
    report = CleanupReport()
    assert report.audit_deleted == 0
    assert report.audit_skipped_unacked == 0
    assert report.event_deleted == 0
    assert report.jwt_blacklist_deleted == 0
    assert report.duration_seconds == 0.0
    assert report.audit_deleted_by_tenant == {}


def test_job_rejects_non_positive_batch_size() -> None:
    """``batch_size <= 0`` is a programmer error — surface early."""
    with pytest.raises(ValueError, match="batch_size"):
        RetentionCleanupJob(
            db_session_factory=lambda: None,  # type: ignore[arg-type]
            batch_size=0,
        )
