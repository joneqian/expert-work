"""Smoke tests for :func:`control_plane.app.create_app`."""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from control_plane.app import ProdAuthModeNotReadyError, create_app
from control_plane.settings import Settings
from helix_agent.common.lifecycle import Lifecycle


def test_create_app_returns_fastapi_instance() -> None:
    app = create_app(settings=Settings(_env_file=None))  # type: ignore[call-arg]
    assert isinstance(app, FastAPI)
    assert app.title == "Helix-Agent Control Plane"


def test_create_app_attaches_settings_and_lifecycle() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    lc = Lifecycle()
    app = create_app(settings=settings, lifecycle=lc)
    assert app.state.settings is settings
    assert app.state.lifecycle is lc
    assert app.state.health_provider is not None


def test_prod_auth_mode_refuses_to_boot() -> None:
    settings = Settings(auth_mode="prod")
    with pytest.raises(ProdAuthModeNotReadyError):
        create_app(settings=settings)


def test_health_and_metrics_routes_registered() -> None:
    app = create_app(settings=Settings(_env_file=None))  # type: ignore[call-arg]
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/healthz/live" in paths
    assert "/healthz/ready" in paths
    assert "/healthz/startup" in paths
    assert "/metrics" in paths
