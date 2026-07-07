"""Unit tests for :class:`BillingRollupSettings` — Stream Y4."""

from __future__ import annotations

from datetime import date

from billing_rollup_job.settings import BillingRollupSettings


def test_default_target_month_is_first_of_current_month() -> None:
    settings = BillingRollupSettings()
    assert settings.target_month.day == 1


def test_target_month_parses_year_month(monkeypatch) -> None:
    monkeypatch.setenv("EXPERT_WORK_BILLING_ROLLUP_TARGET_MONTH", "2026-05")
    settings = BillingRollupSettings()
    assert settings.target_month == date(2026, 5, 1)


def test_target_month_full_date_normalized_to_first(monkeypatch) -> None:
    monkeypatch.setenv("EXPERT_WORK_BILLING_ROLLUP_TARGET_MONTH", "2026-05-17")
    settings = BillingRollupSettings()
    assert settings.target_month == date(2026, 5, 1)
