"""Tests for the egress urllib shim baked into the sandbox images
(``infra/sandbox-image/sitecustomize.py``, sandbox-egress §3.5).

stdlib ``urllib`` does not send the proxy token on an HTTPS ``CONNECT``; the
shim patches ``http.client.HTTPConnection.set_tunnel`` to add it from the
supervisor-injected ``EXPERT_WORK_EGRESS_PROXY_AUTH`` env. These tests load the shim
file directly (it lives in the image build context, not an importable package)
and verify the three behaviors: add when env is set, no-op without it, and never
override a client's own auth. Each test saves/restores the global
``set_tunnel`` so the patch does not leak across tests.
"""

from __future__ import annotations

import http.client
import importlib.util
from pathlib import Path

import pytest

_SHIM = Path(__file__).resolve().parents[3] / "infra" / "sandbox-image" / "sitecustomize.py"


def _load_shim(monkeypatch: pytest.MonkeyPatch, auth: str | None) -> None:
    """Exec the shim file with the env set as given. It patches the global
    ``http.client.HTTPConnection.set_tunnel`` at import time iff ``auth`` set."""
    if auth is None:
        monkeypatch.delenv("EXPERT_WORK_EGRESS_PROXY_AUTH", raising=False)
    else:
        monkeypatch.setenv("EXPERT_WORK_EGRESS_PROXY_AUTH", auth)
    spec = importlib.util.spec_from_file_location("_egress_shim_under_test", _SHIM)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


def test_shim_adds_proxy_auth_to_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    orig = http.client.HTTPConnection.set_tunnel
    try:
        _load_shim(monkeypatch, "QUJDOg==")
        conn = http.client.HTTPConnection("proxy.local", 8081)
        conn.set_tunnel("example.com", 443)
        assert conn._tunnel_headers.get("Proxy-Authorization") == "Basic QUJDOg=="
    finally:
        http.client.HTTPConnection.set_tunnel = orig  # type: ignore[method-assign]


def test_shim_noop_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    orig = http.client.HTTPConnection.set_tunnel
    try:
        _load_shim(monkeypatch, None)
        # No env → the shim must not patch set_tunnel at all.
        assert http.client.HTTPConnection.set_tunnel is orig
        conn = http.client.HTTPConnection("proxy.local", 8081)
        conn.set_tunnel("example.com", 443)
        assert "Proxy-Authorization" not in conn._tunnel_headers
    finally:
        http.client.HTTPConnection.set_tunnel = orig  # type: ignore[method-assign]


def test_shim_preserves_client_supplied_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    orig = http.client.HTTPConnection.set_tunnel
    try:
        _load_shim(monkeypatch, "QUJDOg==")
        conn = http.client.HTTPConnection("proxy.local", 8081)
        conn.set_tunnel("example.com", 443, headers={"Proxy-Authorization": "Basic CLIENTOWN"})
        # A client that already set proxy auth keeps it; the shim only fills gaps.
        assert conn._tunnel_headers["Proxy-Authorization"] == "Basic CLIENTOWN"
    finally:
        http.client.HTTPConnection.set_tunnel = orig  # type: ignore[method-assign]
