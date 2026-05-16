"""Docker integration tests for the Sandbox Supervisor — Stream F.8 / F.9.

These exercise the *real* ``CliDockerClient`` against a runc container,
covering STREAM-F-DESIGN § 1.3 acceptance gates that runc fully proves:

* #45 — ``exec_python`` runs code end to end
* #48 — filesystem + process isolation (gates #1 / #2)
* #49 — egress network isolation (gate #3) — F.9
* #50 — no credentials are visible inside the sandbox (gate #4)
* #56 — a fork bomb is contained by ``--pids-limit`` (gate #5)
* #57 — a cancelled run is SIGKILLed within 1s (gate #8)
* #59 — the image's CPython ships a complete C-extension stdlib

Out of scope (Mini-ADR F-10): gates #6 / #7 need real gVisor (runsc) —
M0→M1 penetration testing.

A session fixture builds the sandbox image, creates the ``--internal``
egress network, and starts a stub proxy on it; the whole module
``pytest.skip``s when Docker is unavailable, so the unit ``pytest`` job
(``-m "not integration"``) never touches Docker.
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from helix_agent.runtime.sandbox import SandboxRuntimeProvider
from sandbox_supervisor.docker_client import CliDockerClient
from sandbox_supervisor.domain import SandboxRecord, SandboxState
from sandbox_supervisor.schemas import AcquireRequest
from sandbox_supervisor.settings import SandboxSupervisorSettings
from sandbox_supervisor.supervisor import SandboxSupervisor

pytestmark = pytest.mark.integration

#: Image tag built for the test run; kept distinct from the dev tag.
_IMAGE = "helix-sandbox:itest"
#: Egress network — created ``--internal`` (no NAT / default route) so a
#: sandbox on it can reach only same-network peers (Mini-ADR F-14).
_NETWORK = "helix-sandbox-egress"
#: Stub HTTP listener standing in for the credential-proxy — the one
#: endpoint a sandbox IS allowed to reach (the real proxy lands in F.10).
_STUB_PROXY = "helix-test-proxy"
#: Base image for the stub proxy — already local after the sandbox build.
_STUB_IMAGE = "python:3.12-alpine"
#: ``infra/sandbox-image/`` — the Dockerfile's build context.
_IMAGE_CONTEXT = Path(__file__).resolve().parents[3] / "infra" / "sandbox-image"


def _docker(*args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    """Run a ``docker`` CLI command — a test-harness helper."""
    return subprocess.run(  # noqa: S603 — fixed argv, no shell, test harness only
        ["docker", *args],  # noqa: S607 — ``docker`` is on PATH in CI and dev
        capture_output=True,
        text=True,
        check=check,
    )


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _sweep_sandbox_containers() -> None:
    """Force-remove any leftover ``helix-sb-*`` sandbox container."""
    leftover = _docker("ps", "--all", "--quiet", "--filter", "name=helix-sb-")
    for container_id in leftover.stdout.split():
        _docker("rm", "--force", container_id)


@pytest.fixture(scope="session", autouse=True)
def _docker_env() -> Iterator[None]:
    """Build the image, create the ``--internal`` egress network + stub proxy.

    Skips the whole module when Docker is unavailable.
    """
    try:
        probe = _docker("version", "--format", "{{.Server.Version}}")
    except (OSError, subprocess.SubprocessError):
        pytest.skip("docker CLI unavailable")
    if probe.returncode != 0:
        pytest.skip("docker daemon unreachable")

    build = _docker("build", "-t", _IMAGE, str(_IMAGE_CONTEXT))
    if build.returncode != 0:
        pytest.skip(f"sandbox image build failed: {build.stderr[-400:]}")

    # Clean slate, then (re)create the egress network as ``--internal`` —
    # Docker gives it no NAT / default route, so a sandbox on it cannot
    # reach the internet, cloud metadata, or other docker networks
    # (Mini-ADR F-14); only same-network peers (the proxy) are reachable.
    _sweep_sandbox_containers()
    _docker("rm", "--force", _STUB_PROXY)
    _docker("network", "rm", _NETWORK)
    created = _docker("network", "create", "--internal", _NETWORK)
    if created.returncode != 0:
        pytest.skip(f"egress network create failed: {created.stderr[-200:]}")
    stub = _docker(
        "run",
        "--detach",
        "--name",
        _STUB_PROXY,
        "--network",
        _NETWORK,
        "--entrypoint",
        "python",
        _STUB_IMAGE,
        "-m",
        "http.server",
        "8080",
    )
    if stub.returncode != 0:
        pytest.skip(f"stub proxy start failed: {stub.stderr[-200:]}")

    yield

    _docker("rm", "--force", _STUB_PROXY)
    _docker("network", "rm", _NETWORK)


@pytest.fixture(autouse=True)
def _sweep_containers() -> Iterator[None]:
    """Force-remove any leftover ``helix-sb-*`` container after each test."""
    yield
    _sweep_sandbox_containers()


class _InMemoryStore:
    """A dict-backed ``SandboxStore`` — F.8 exercises Docker, not the DB."""

    def __init__(self) -> None:
        self.rows: dict[UUID, SandboxRecord] = {}

    async def insert(self, record: SandboxRecord) -> None:
        self.rows[record.id] = record

    async def update(self, record: SandboxRecord) -> None:
        self.rows[record.id] = record

    async def get(self, sandbox_id: UUID) -> SandboxRecord | None:
        return self.rows.get(sandbox_id)

    async def count_active_for_tenant(self, tenant_id: UUID) -> int:
        return sum(
            1
            for r in self.rows.values()
            if r.tenant_id == tenant_id and r.state in (SandboxState.CREATING, SandboxState.IN_USE)
        )

    async def sandbox_limit_for_tenant(self, tenant_id: UUID) -> int | None:
        return None


class _NullAudit:
    """An ``AuditSink`` that drops entries — F.8 does not assert on audit."""

    async def write(self, entry: object) -> None:
        """Drop the audit entry — F.8 asserts on Docker behaviour, not audit."""


@dataclass
class _Harness:
    supervisor: SandboxSupervisor
    store: _InMemoryStore


@pytest.fixture
def helix() -> _Harness:
    store = _InMemoryStore()
    supervisor = SandboxSupervisor(
        store=store,  # type: ignore[arg-type]  # structural SandboxStore
        docker=CliDockerClient(),
        audit=_NullAudit(),  # type: ignore[arg-type]  # structural AuditSink
        runtime_provider=SandboxRuntimeProvider(oci_runtime="runc", egress_network=_NETWORK),
        settings=SandboxSupervisorSettings(sandbox_image=_IMAGE, oci_runtime="runc"),
    )
    return _Harness(supervisor=supervisor, store=store)


def _acquire_request(thread_id: str) -> AcquireRequest:
    return AcquireRequest(tenant_id=uuid4(), thread_id=thread_id)


# ---------------------------------------------------------------------------
# #45 — exec_python runs code end to end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_45_exec_python_runs_code(helix: _Harness) -> None:
    acquired = await helix.supervisor.acquire(_acquire_request("t-45"))
    result = await helix.supervisor.exec(acquired.sandbox_id, code="print(6 * 7)")
    await helix.supervisor.release(acquired.sandbox_id)

    assert result.exit_code == 0
    assert result.timed_out is False
    assert "42" in result.stdout


# ---------------------------------------------------------------------------
# #48 — filesystem + process isolation (gates #1 / #2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_48_filesystem_and_process_isolation(helix: _Harness) -> None:
    # Sandbox A writes into /workspace, then is released.
    box_a = await helix.supervisor.acquire(_acquire_request("t-48a"))
    written = await helix.supervisor.exec(
        box_a.sandbox_id,
        code="open('/workspace/leak.txt', 'w').write('from-A')",
    )
    assert written.exit_code == 0
    await helix.supervisor.release(box_a.sandbox_id)

    # Sandbox B is a brand-new container (per-acquire docker run + tmpfs):
    # A's file is gone — no cross-sandbox filesystem leak.
    box_b = await helix.supervisor.acquire(_acquire_request("t-48b"))
    seen = await helix.supervisor.exec(
        box_b.sandbox_id,
        code="import os; print(os.path.exists('/workspace/leak.txt'))",
    )
    assert seen.stdout.strip() == "False"

    # B has its own PID namespace — it sees only a few low PIDs, never the
    # host's hundreds (process isolation, gate #2).
    pids = await helix.supervisor.exec(
        box_b.sandbox_id,
        code="import os; print(max(int(p) for p in os.listdir('/proc') if p.isdigit()))",
    )
    await helix.supervisor.release(box_b.sandbox_id)
    assert int(pids.stdout.strip()) < 100


# ---------------------------------------------------------------------------
# #49 — egress network isolation (gate #3)
# ---------------------------------------------------------------------------

#: Probes three destinations: the same-network proxy stand-in (allowed),
#: cloud metadata, and a public IP (both must be unreachable).
_EGRESS_PROBE = (
    "import socket\n"
    "def reach(host, port):\n"
    "    s = socket.socket()\n"
    "    s.settimeout(3)\n"
    "    try:\n"
    "        s.connect((host, port))\n"
    "        return 'OK'\n"
    "    except OSError as exc:\n"
    "        return 'FAIL:' + type(exc).__name__\n"
    "    finally:\n"
    "        s.close()\n"
    "print('proxy', reach('helix-test-proxy', 8080))\n"
    "print('metadata', reach('169.254.169.254', 80))\n"
    "print('internet', reach('8.8.8.8', 53))\n"
)


@pytest.mark.asyncio
async def test_gate_49_network_egress_isolation(helix: _Harness) -> None:
    box = await helix.supervisor.acquire(_acquire_request("t-49"))
    result = await helix.supervisor.exec(box.sandbox_id, code=_EGRESS_PROBE)
    await helix.supervisor.release(box.sandbox_id)

    assert result.exit_code == 0, result.stderr
    # On the --internal network the sandbox reaches its same-network peer
    # (the proxy stand-in) but has no route to cloud metadata or the
    # public internet — gate #3 (Mini-ADR F-14).
    assert "proxy OK" in result.stdout
    assert "metadata FAIL" in result.stdout
    assert "internet FAIL" in result.stdout


# ---------------------------------------------------------------------------
# #50 — no credentials are visible inside the sandbox (gate #4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_50_no_credentials_in_sandbox(helix: _Harness) -> None:
    box = await helix.supervisor.acquire(_acquire_request("t-50"))
    probe = await helix.supervisor.exec(
        box.sandbox_id,
        code=(
            "import os\n"
            "for name in sorted(os.environ):\n"
            "    print('ENVVAR ' + name)\n"
            "print('RUN_SECRETS ' + str(os.path.isdir('/run/secrets')))\n"
        ),
    )
    await helix.supervisor.release(box.sandbox_id)

    assert probe.exit_code == 0
    # The F.3 docker-run argv injects no -e/--env; credentials only ever
    # appear on the credential-proxy egress path (Mini-ADR F-2).
    env_names = [
        line[len("ENVVAR ") :].lower()
        for line in probe.stdout.splitlines()
        if line.startswith("ENVVAR ")
    ]
    banned = ("secret", "token", "password", "credential", "helix", "dsn")
    for name in env_names:
        assert not any(needle in name for needle in banned), f"leaked env var: {name}"
    assert "RUN_SECRETS False" in probe.stdout


# ---------------------------------------------------------------------------
# #56 — a fork bomb is contained by --pids-limit (gate #5)
# ---------------------------------------------------------------------------

#: Only the parent loops; each child closes the captured pipe and parks, so
#: it holds a PID slot without blocking the runner's stdout EOF.
_FORK_BOMB = (
    "import os, time\n"
    "spawned = 0\n"
    "try:\n"
    "    while True:\n"
    "        if os.fork() == 0:\n"
    "            os.close(1)\n"
    "            os.close(2)\n"
    "            time.sleep(60)\n"
    "            os._exit(0)\n"
    "        spawned += 1\n"
    "except OSError:\n"
    "    print(f'fork-limit-hit spawned={spawned}')\n"
)


@pytest.mark.asyncio
async def test_gate_56_fork_bomb_contained_by_pids_limit(helix: _Harness) -> None:
    box = await helix.supervisor.acquire(_acquire_request("t-56"))
    result = await helix.supervisor.exec(box.sandbox_id, code=_FORK_BOMB)
    await helix.supervisor.release(box.sandbox_id)

    # The fork bomb hit the cgroup pids cap and the snippet exited cleanly —
    # the host and the test suite are unaffected.
    assert result.exit_code == 0
    assert result.timed_out is False
    assert "fork-limit-hit" in result.stdout
    spawned = int(result.stdout.split("spawned=")[1].split()[0])
    assert 0 < spawned < 128  # bounded by the default --pids-limit


# ---------------------------------------------------------------------------
# #57 — a cancelled run is SIGKILLed within 1s (gate #8)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_57_cancellation_kills_sandbox_within_1s(helix: _Harness) -> None:
    box = await helix.supervisor.acquire(_acquire_request("t-57"))

    # Start a long-running exec, then destroy the sandbox mid-flight.
    runaway = asyncio.ensure_future(
        helix.supervisor.exec(box.sandbox_id, code="while True: pass", timeout_s=300)
    )
    await asyncio.sleep(0.5)  # let the runner enter the loop

    start = time.monotonic()
    await helix.supervisor.destroy(box.sandbox_id, reason="cancelled")
    elapsed = time.monotonic() - start

    runaway.cancel()
    await asyncio.gather(runaway, return_exceptions=True)

    # Gate #8: the forced destroy SIGKILLs the busy container in ≤1s — it
    # must not wait on the graceful stdin-EOF close grace (Mini-ADR F-8).
    assert elapsed < 1.0
    row = await helix.store.get(box.sandbox_id)
    assert row is not None
    assert row.state is SandboxState.DESTROYED
    assert row.destroy_reason == "cancelled"
    # The container is really gone.
    remaining = _docker("ps", "--all", "--quiet", "--filter", f"name=helix-sb-{box.sandbox_id}")
    assert remaining.stdout.strip() == ""


# ---------------------------------------------------------------------------
# #59 — the image's CPython ships a complete C-extension stdlib
# ---------------------------------------------------------------------------

#: C-extension stdlib modules — the gates above only touch builtins, so this
#: guards the base-image contract (Mini-ADR F-13: the alpine / musl CPython
#: must expose the same stdlib as the previous Debian image).
_STDLIB_PROBE = (
    "import ssl, hashlib, sqlite3, ctypes, lzma, bz2, zlib, decimal, _socket\nprint('stdlib-ok')\n"
)


@pytest.mark.asyncio
async def test_gate_59_stdlib_c_extensions_importable(helix: _Harness) -> None:
    box = await helix.supervisor.acquire(_acquire_request("t-59"))
    result = await helix.supervisor.exec(box.sandbox_id, code=_STDLIB_PROBE)
    await helix.supervisor.release(box.sandbox_id)

    assert result.exit_code == 0, result.stderr
    assert "stdlib-ok" in result.stdout
