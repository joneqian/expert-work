"""Stream 12.4 — the rollup-enable gate read from platform_billing_config."""

from __future__ import annotations

from dataclasses import dataclass

from billing_rollup_job.main import rollup_is_enabled


@dataclass(frozen=True)
class _Config:
    rollup_enabled: bool


def test_absent_config_defaults_to_enabled() -> None:
    # No config row → run (back-compat for deployments that never set it).
    assert rollup_is_enabled(None) is True


def test_enabled_flag_runs() -> None:
    assert rollup_is_enabled(_Config(rollup_enabled=True)) is True


def test_disabled_flag_skips() -> None:
    # Operator paused rollup from the admin UI → the job must skip.
    assert rollup_is_enabled(_Config(rollup_enabled=False)) is False
