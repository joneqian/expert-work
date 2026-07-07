"""Smoke tests for :func:`control_plane.app.create_app`."""

from __future__ import annotations

from fastapi import FastAPI

from control_plane.app import create_app
from control_plane.settings import Settings
from expert_work.common.lifecycle import Lifecycle
from tests.auth_fixtures import build_test_jwt_verifier


def test_create_app_returns_fastapi_instance() -> None:
    app = create_app(
        settings=Settings(_env_file=None),  # type: ignore[call-arg]
        jwt_verifier=build_test_jwt_verifier(),
    )
    assert isinstance(app, FastAPI)
    assert app.title == "Expert Work Control Plane"


def test_create_app_attaches_settings_and_lifecycle() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    lc = Lifecycle()
    app = create_app(settings=settings, lifecycle=lc, jwt_verifier=build_test_jwt_verifier())
    assert app.state.settings is settings
    assert app.state.lifecycle is lc
    assert app.state.health_provider is not None
    assert app.state.jwt_verifier is not None


def test_create_app_accepts_prod_auth_mode_after_c1() -> None:
    """After C.1, ``auth_mode=prod`` boots — JWT middleware enforces auth."""
    settings = Settings(_env_file=None, auth_mode="prod")  # type: ignore[call-arg]
    app = create_app(settings=settings, jwt_verifier=build_test_jwt_verifier())
    assert isinstance(app, FastAPI)


def test_health_and_metrics_routes_registered() -> None:
    app = create_app(
        settings=Settings(_env_file=None),  # type: ignore[call-arg]
        jwt_verifier=build_test_jwt_verifier(),
    )
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/healthz/live" in paths
    assert "/healthz/ready" in paths
    assert "/healthz/startup" in paths
    assert "/metrics" in paths
