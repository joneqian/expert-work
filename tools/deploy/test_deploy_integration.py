"""Integration smoke for blue/green deploy + rollback — test matrix #69 / #70.

Brings up the M0 stack (data layer + both control-plane colours + nginx)
once, then runs two ordered smokes against it:

* ``#69`` — ``deploy.py`` flips blue → green; asserts the upstream
  switched, the new colour serves through nginx, the old colour drained.
* ``#70`` — ``rollback.py`` fast path flips green → blue back; asserts
  the upstream returned to blue. Runs *after* #69 (it consumes the
  green-live state #69 leaves).

Heavy (image build + full stack) — ``@pytest.mark.integration``, runs in
the non-gating ``Test (integration)`` job; skipped when Docker is
unavailable so the unit ``pytest`` job is unaffected.
"""

from __future__ import annotations

import os
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
from deploy import parse_live_color

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INFRA = _REPO_ROOT / "infra"
_COMPOSE_FILE = _INFRA / "docker-compose.yml"
_UPSTREAM_CONF = _INFRA / "nginx" / "conf.d" / "control-plane-upstream.conf"
_DEPLOY_PY = _REPO_ROOT / "tools" / "deploy" / "deploy.py"
_ROLLBACK_PY = _REPO_ROOT / "tools" / "deploy" / "rollback.py"
_CERTGEN_PY = _REPO_ROOT / "tools" / "dev-certs" / "generate.py"

#: Services the #69 smoke needs — data layer + both colours + nginx.
_STACK = (
    "postgres",
    "pgbouncer",
    "redis",
    "migrate",
    "control-plane-blue",
    "control-plane-green",
    "nginx",
)
_DEPLOY_TAG = "itest-deploy"


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a ``docker`` CLI command (test-harness helper)."""
    return subprocess.run(  # noqa: S603 — fixed argv, no shell, test harness
        ["docker", *args],  # noqa: S607 — docker on PATH in CI / dev
        check=check,
        text=True,
        capture_output=True,
    )


def _compose_files() -> list[str]:
    """``-f`` arguments — the infra compose file, plus an optional local
    override from ``HELIX_TEST_COMPOSE_OVERRIDE`` for dev hosts where a
    stack port (e.g. redis 6379) is already taken. CI leaves it unset."""
    files = ["-f", str(_COMPOSE_FILE)]
    override = os.environ.get("HELIX_TEST_COMPOSE_OVERRIDE")
    if override:
        files += ["-f", override]
    return files


def _compose(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run ``docker compose`` against the infra compose file."""
    return _docker("compose", *_compose_files(), *args, check=check)


def _container_state(name: str) -> str:
    return _docker("inspect", "-f", "{{.State.Status}}", name).stdout.strip()


def _http_status(url: str) -> int | None:
    """GET ``url`` — HTTP status, or ``None`` when the connection is refused."""
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 — fixed localhost URL
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        return None


@pytest.fixture(scope="module")
def deploy_stack() -> Iterator[None]:
    """Bring the M0 stack up; restore the (committed) upstream conf after."""
    probe = _docker("version", "--format", "{{.Server.Version}}", check=False)
    if probe.returncode != 0:
        pytest.skip("docker daemon unavailable")

    # deploy.py rewrites this committed file — snapshot it for restore.
    original_upstream = _UPSTREAM_CONF.read_text()

    # nginx's 8443 mTLS listener needs the dev PKI present to start.
    # ``--force`` regenerates over any pre-existing dev-certs/ dir.
    certgen = subprocess.run(  # noqa: S603
        [sys.executable, str(_CERTGEN_PY), "--force"],
        check=False,
        text=True,
        capture_output=True,
    )
    if certgen.returncode != 0:
        pytest.skip(f"dev-cert generation failed: {certgen.stderr.strip()}")

    build = _compose("build", "control-plane-blue", check=False)
    if build.returncode != 0:
        pytest.skip(f"control-plane image build failed: {build.stderr.strip()[-400:]}")

    up = _compose(
        "--profile",
        "full",
        "--profile",
        "proxy",
        "up",
        "-d",
        "--wait",
        "--wait-timeout",
        "240",
        *_STACK,
        check=False,
    )
    if up.returncode != 0:
        logs = _compose("logs", "--tail", "60", check=False).stdout
        _compose("--profile", "full", "--profile", "proxy", "down", "--remove-orphans", check=False)
        _UPSTREAM_CONF.write_text(original_upstream)
        pytest.fail(f"stack failed to come up:\n{up.stderr}\n{logs}")

    try:
        yield
    finally:
        _compose("--profile", "full", "--profile", "proxy", "down", "--remove-orphans", check=False)
        _UPSTREAM_CONF.write_text(original_upstream)


def test_gate_69_blue_green_deploy(deploy_stack: None) -> None:
    """deploy.py flips blue → green: upstream switched, new colour serves,
    old colour drained + stopped."""
    # Precondition — the committed default routes to blue.
    assert parse_live_color(_UPSTREAM_CONF.read_text()) == "blue"
    assert _http_status("http://localhost:8080/healthz/live") == 200

    # A genuine new-tag deploy — re-tag the built image.
    _docker("tag", "helix-control-plane:dev", f"helix-control-plane:{_DEPLOY_TAG}")

    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(_DEPLOY_PY),
            "--tag",
            _DEPLOY_TAG,
            "--drain-timeout",
            "10",
            "--ready-timeout",
            "120",
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, f"deploy.py failed:\n{result.stdout}\n{result.stderr}"

    # Upstream flipped to green.
    assert parse_live_color(_UPSTREAM_CONF.read_text()) == "green"

    # Green runs the freshly deployed tag.
    green_image = _docker(
        "inspect", "-f", "{{.Config.Image}}", "helix-control-plane-green"
    ).stdout.strip()
    assert green_image == f"helix-control-plane:{_DEPLOY_TAG}"

    # Green up, blue drained + stopped (kept for rollback).
    assert _container_state("helix-control-plane-green") == "running"
    assert _container_state("helix-control-plane-blue") == "exited"

    # Traffic still served through nginx (now routed to green); the
    # per-colour host ports confirm which colour is up. Blue's port no
    # longer serves — a stopped container's published port answers with
    # a connection error (standard Docker) or a 502 (OrbStack), so
    # assert "not 200" rather than a specific failure shape.
    assert _http_status("http://localhost:8080/healthz/live") == 200
    assert _http_status("http://localhost:8001/healthz/live") == 200  # green
    assert _http_status("http://localhost:8000/healthz/live") != 200  # blue stopped


def test_gate_70_rollback(deploy_stack: None) -> None:
    """rollback.py fast path flips green → blue back: the kept blue
    container is restarted, the upstream returns to blue, green drained.

    Runs after test_gate_69 — it consumes the green-live state #69 left.
    """
    # Precondition — #69 left the stack on green.
    assert parse_live_color(_UPSTREAM_CONF.read_text()) == "green"

    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(_ROLLBACK_PY),
            "--drain-timeout",
            "10",
            "--ready-timeout",
            "120",
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, f"rollback.py failed:\n{result.stdout}\n{result.stderr}"

    # Upstream rolled back to blue.
    assert parse_live_color(_UPSTREAM_CONF.read_text()) == "blue"

    # Blue restarted + serving, green drained + stopped.
    assert _container_state("helix-control-plane-blue") == "running"
    assert _container_state("helix-control-plane-green") == "exited"

    assert _http_status("http://localhost:8080/healthz/live") == 200
    assert _http_status("http://localhost:8000/healthz/live") == 200  # blue
    assert _http_status("http://localhost:8001/healthz/live") != 200  # green stopped
