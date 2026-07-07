"""Unit tests for :class:`control_plane.settings.Settings`."""

from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError

from control_plane.settings import DEFAULT_DEV_TENANT_ID, Settings


def test_default_values() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.service_name == "control_plane"
    assert settings.env == "dev"
    assert settings.auth_mode == "dev"
    assert settings.default_dev_tenant_id == DEFAULT_DEV_TENANT_ID
    assert settings.default_dev_actor_id == "anonymous"
    assert settings.db_pgbouncer_mode is True
    assert settings.single_instance is True
    assert settings.health_check_timeout_s == 5.0


def test_env_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXPERT_WORK_SERVICE_NAME", "control_plane_alt")
    monkeypatch.setenv("EXPERT_WORK_ENV", "staging")
    monkeypatch.setenv("EXPERT_WORK_AUTH_MODE", "dev")
    monkeypatch.setenv(
        "EXPERT_WORK_DEFAULT_DEV_TENANT_ID",
        "11111111-1111-1111-1111-111111111111",
    )

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.service_name == "control_plane_alt"
    assert settings.env == "staging"
    assert settings.default_dev_tenant_id == UUID("11111111-1111-1111-1111-111111111111")


def test_invalid_auth_mode_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXPERT_WORK_AUTH_MODE", "wat")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_health_check_timeout_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(health_check_timeout_s=0)
