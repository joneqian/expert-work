"""Docker integration tests for the Sandbox Supervisor — Stream F.8 / F.9.

These exercise the *real* ``CliDockerClient`` against the configured OCI
runtime — ``runc`` by default (local / the existing CI integration job),
or ``runsc`` when ``HELIX_TEST_SANDBOX_RUNTIME=runsc`` (the Stream HX-10
gVisor CI workflow runs this same acceptance suite under gVisor). It covers
STREAM-F-DESIGN § 1.3 acceptance gates:

* #45 — ``exec_python`` runs code end to end
* #48 — filesystem + process isolation (gates #1 / #2)
* #49 — egress network isolation (gate #3) — F.9
* #50 — no credentials are visible inside the sandbox (gate #4)
* #56 — a fork bomb is contained by ``--pids-limit`` (gate #5)
* #57 — a cancelled run is SIGKILLed within 1s (gate #8)
* #59 — the image's CPython ships a complete C-extension stdlib

Under runsc the suite additionally asserts the live container's runtime
(guarding against a silent fall-back to runc) and a gVisor isolation
invariant (io_uring unavailable). Two gates change shape under runsc
because gVisor alters their behaviour by design:
#49 (egress) relies on docker embedded DNS, which gVisor netstack does not
support (google/gvisor#7469) — still ``xfail`` pending the HX-10-F1
fixed-IP addressing rollout. #56 (fork bomb) panics the sentry rather than
returning EAGAIN (google/gvisor#2490): the runc graceful-containment test
is ``skip``ped under runsc and replaced by an explicit method-A assertion
(HX-10-F2) — the fork bomb kills its own sandbox, the supervisor survives,
and a fresh sandbox rebuilds. gVisor delegates resource-exhaustion defense
to the host cgroup, so sandbox-death + rebuild is the accepted semantic.
Gate #6 (timing side-channel) and a full CVE-2019-5736 escape PoC stay out
of CI — environment-sensitive / unsafe as a live exploit on shared runners
— and remain staging penetration tests (Mini-ADR F-10 / HX-10 §12.2.2).

A session fixture builds the sandbox image, creates the ``--internal``
egress network, and starts a stub proxy on it; the whole module
``pytest.skip``s when Docker is unavailable, so the unit ``pytest`` job
(``-m "not integration"``) never touches Docker.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
from uuid import UUID, uuid4

import pytest

from helix_agent.persistence import InMemoryUserWorkspaceStore
from helix_agent.runtime.sandbox import SandboxRuntimeProvider
from sandbox_supervisor.docker_client import CliDockerClient
from sandbox_supervisor.domain import SandboxRecord, SandboxState, SupervisorError
from sandbox_supervisor.pool import PoolReplenisher, SandboxPool
from sandbox_supervisor.schemas import AcquireRequest
from sandbox_supervisor.settings import SandboxSupervisorSettings
from sandbox_supervisor.supervisor import SandboxSupervisor, _container_name

pytestmark = pytest.mark.integration

#: Stream HX-10 — the OCI runtime under test. Defaults to ``runc`` (local /
#: the existing CI integration job); the gVisor CI workflow sets
#: ``HELIX_TEST_SANDBOX_RUNTIME=runsc`` so this same acceptance suite runs
#: under gVisor (test matrix #43 extended to the prod runtime).
_OCI_RUNTIME = cast(
    Literal["runc", "runsc"],
    os.environ.get("HELIX_TEST_SANDBOX_RUNTIME", "runc"),
)
#: Skip gVisor-specific invariants when the suite runs under runc.
_gvisor_only = pytest.mark.skipif(
    _OCI_RUNTIME != "runsc",
    reason="gVisor-specific invariant (set HELIX_TEST_SANDBOX_RUNTIME=runsc)",
)

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


def _sweep_workspace_volumes() -> None:
    """Remove any leftover ``helix-ws-*`` per-user workspace volume (J.15).

    Run *after* the containers are swept — docker refuses to remove a
    volume still mounted by a live container.
    """
    leftover = _docker("volume", "ls", "--quiet", "--filter", "name=helix-ws-")
    for volume_name in leftover.stdout.split():
        _docker("volume", "rm", "--force", volume_name)


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
    """Remove leftover ``helix-sb-*`` containers + ``helix-ws-*`` volumes after each test."""
    yield
    _sweep_sandbox_containers()
    _sweep_workspace_volumes()


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

    async def claim_ready(self, record: SandboxRecord) -> bool:
        # HX-6 CAS mirror: rebind only while the row is still READY.
        current = self.rows.get(record.id)
        if current is None or current.state is not SandboxState.READY:
            return False
        self.rows[record.id] = record
        return True


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
        runtime_provider=SandboxRuntimeProvider(oci_runtime=_OCI_RUNTIME, egress_network=_NETWORK),
        workspace_store=InMemoryUserWorkspaceStore(),
        settings=SandboxSupervisorSettings(sandbox_image=_IMAGE, oci_runtime=_OCI_RUNTIME),
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
# J.15 — the per-user persistent workspace volume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persistent_workspace_survives_across_containers(helix: _Harness) -> None:
    # A user-scoped acquire mounts a docker named volume at /workspace.
    # Files written in one container outlive it and reappear in the next
    # — STREAM-J-DESIGN § 9 ("临时容器 + 持久卷"). This also proves a
    # fresh volume is writable by the image's non-root ``agent`` user.
    tenant, user = uuid4(), uuid4()

    box_a = await helix.supervisor.acquire(
        AcquireRequest(tenant_id=tenant, thread_id="t-ws-a", user_id=user)
    )
    written = await helix.supervisor.exec(
        box_a.sandbox_id,
        code="open('/workspace/note.txt', 'w').write('persisted')",
    )
    await helix.supervisor.release(box_a.sandbox_id)
    assert written.exit_code == 0, written.stderr

    # A brand-new container for the same (tenant, user) re-mounts the
    # same volume — box A's file is still there.
    box_b = await helix.supervisor.acquire(
        AcquireRequest(tenant_id=tenant, thread_id="t-ws-b", user_id=user)
    )
    seen = await helix.supervisor.exec(
        box_b.sandbox_id,
        code="print(open('/workspace/note.txt').read())",
    )
    await helix.supervisor.release(box_b.sandbox_id)
    assert seen.exit_code == 0, seen.stderr
    assert "persisted" in seen.stdout


@pytest.mark.asyncio
async def test_read_workspace_file_reads_persisted_content(helix: _Harness) -> None:
    # J.9 — the supervisor reads an artifact's bytes out of the user's
    # volume via a throwaway read-only container, with no sandbox running.
    tenant, user = uuid4(), uuid4()
    box = await helix.supervisor.acquire(
        AcquireRequest(tenant_id=tenant, thread_id="t-rd", user_id=user)
    )
    written = await helix.supervisor.exec(
        box.sandbox_id,
        code="open('/workspace/artifact.txt', 'w').write('artifact body')",
    )
    await helix.supervisor.release(box.sandbox_id)
    assert written.exit_code == 0, written.stderr

    data = await helix.supervisor.read_workspace_file(
        tenant_id=tenant, user_id=user, path="artifact.txt"
    )
    assert data == b"artifact body"


@pytest.mark.asyncio
async def test_warm_session_reused_across_acquires(helix: _Harness) -> None:
    # J.15 — a user-scoped acquire reuses the live warm sandbox: the
    # second acquire does no docker run, and the held runner link still
    # serves exec on the reused container.
    tenant, user = uuid4(), uuid4()
    first = await helix.supervisor.acquire(
        AcquireRequest(tenant_id=tenant, thread_id="t-warm-1", user_id=user)
    )
    await helix.supervisor.exec(first.sandbox_id, code="print('first')")

    second = await helix.supervisor.acquire(
        AcquireRequest(tenant_id=tenant, thread_id="t-warm-2", user_id=user)
    )
    assert second.sandbox_id == first.sandbox_id
    assert second.cold_start is False

    result = await helix.supervisor.exec(second.sandbox_id, code="print(6 * 7)")
    await helix.supervisor.destroy(first.sandbox_id, reason="cancelled")
    assert result.exit_code == 0
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


@pytest.mark.xfail(
    condition=_OCI_RUNTIME == "runsc",
    reason=(
        "gVisor netstack does not support docker embedded DNS (127.0.0.11 is the "
        "sentry's own loopback, not where dockerd listens) — google/gvisor#7469. "
        "The probe resolves the proxy by container name, which fails under runsc "
        "(isolation still holds: public/metadata stay unreachable). Production must "
        "address sandbox->credential-proxy via /etc/hosts / fixed IP. HX-10 follow-up."
    ),
    strict=False,
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


@pytest.mark.skipif(
    _OCI_RUNTIME == "runsc",
    reason=(
        "runc-specific semantic: the host cgroup pids controller returns EAGAIN to "
        "the guest, so the runner survives and the snippet exits cleanly. Under gVisor "
        "the same fork bomb panics the sentry instead (google/gvisor#2490) — that "
        "accepted sandbox-death+rebuild semantic is asserted by the runsc test below "
        "(HX-10-F2, method A)."
    ),
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


@_gvisor_only
@pytest.mark.asyncio
async def test_gate_56_fork_bomb_sandbox_death_then_rebuild(helix: _Harness) -> None:
    """HX-10-F2 method A: under gVisor a fork bomb kills the sandbox, not the host.

    gVisor's ``--pids-limit`` caps the sentry's HOST threads, not guest
    processes — a fork bomb makes the Go runtime fail to spawn an OS thread
    and panics the sentry, killing the runner (google/gvisor#2490). gVisor
    never claims fork-bomb protection: it delegates resource-exhaustion
    defense to the host cgroup (gVisor Security Model). We *accept* the
    sandbox-death + rebuild semantic — the only guarantee that matters is
    that the blast radius stays inside the dead sandbox. This asserts it:
    the fork bomb breaks its own sandbox, but the supervisor survives and a
    fresh sandbox launches and runs immediately. The persistent workspace
    volume is unaffected (covered by the J.15 persistence test), so a real
    user loses only the in-flight exec, not their data.
    """
    box = await helix.supervisor.acquire(_acquire_request("t-56"))
    # The panicked sentry breaks the runner link, surfacing as a
    # SupervisorError (or a non-clean result if the panic races the read);
    # either way the runc "fork-limit-hit" path is gone by design.
    with contextlib.suppress(SupervisorError):
        await helix.supervisor.exec(box.sandbox_id, code=_FORK_BOMB)
    # The dead container may already be gone; releasing it is best-effort
    # (the autouse sweep force-removes any leftover regardless).
    with contextlib.suppress(SupervisorError):
        await helix.supervisor.release(box.sandbox_id)

    # Blast radius contained: the supervisor is alive and a fresh sandbox
    # rebuilds and executes — the host + supervisor are unaffected.
    fresh = await helix.supervisor.acquire(_acquire_request("t-56b"))
    result = await helix.supervisor.exec(fresh.sandbox_id, code="print('rebuilt')")
    await helix.supervisor.release(fresh.sandbox_id)

    assert result.exit_code == 0
    assert result.timed_out is False
    assert "rebuilt" in result.stdout


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


# ---------------------------------------------------------------------------
# Stream HX-6 — warm pool: replenish → claim → exec → release
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_warm_pool_claim_exec_release() -> None:
    # End-to-end over real Docker: the replenisher pre-launches one READY
    # container; an ephemeral acquire claims it (no docker run on the
    # acquire path), ``docker update`` pairs the request's limits, exec
    # runs over the held link, and release destroys it like any
    # ephemeral sandbox.
    store = _InMemoryStore()
    docker = CliDockerClient()
    settings = SandboxSupervisorSettings(
        sandbox_image=_IMAGE, oci_runtime=_OCI_RUNTIME, pool_size_minimal=1
    )
    runtime = SandboxRuntimeProvider(oci_runtime=_OCI_RUNTIME, egress_network=_NETWORK)
    pool = SandboxPool()
    supervisor = SandboxSupervisor(
        store=store,  # type: ignore[arg-type]  # structural SandboxStore
        docker=docker,
        audit=_NullAudit(),  # type: ignore[arg-type]  # structural AuditSink
        runtime_provider=runtime,
        workspace_store=InMemoryUserWorkspaceStore(),
        settings=settings,
        pool=pool,
    )
    replenisher = PoolReplenisher(
        pool=pool,
        store=store,  # type: ignore[arg-type]  # structural SandboxStore
        docker=docker,
        runtime_provider=runtime,
        settings=settings,
    )

    await replenisher.run_once()
    assert pool.size(_IMAGE) == 1

    acquired = await supervisor.acquire(
        AcquireRequest(tenant_id=uuid4(), thread_id="t-pool", cpu=0.5, memory_mb=256, pids_limit=64)
    )
    assert acquired.cold_start is False
    assert pool.size(_IMAGE) == 0

    result = await supervisor.exec(acquired.sandbox_id, code="print(6 * 7)")
    assert result.exit_code == 0, result.stderr
    assert "42" in result.stdout

    await supervisor.release(acquired.sandbox_id)
    assert store.rows[acquired.sandbox_id].state is SandboxState.DESTROYED


# ---------------------------------------------------------------------------
# Stream HX-10 — runtime-under-test assertion + gVisor invariants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_container_runs_under_expected_runtime(helix: _Harness) -> None:
    # Guards against a silent fall-back to runc: if the gVisor CI workflow
    # fails to register runsc with the daemon, docker silently uses the
    # default runtime and the "gVisor verification" would be a no-op. Assert
    # the live container's runtime matches what the provider asked for.
    acquired = await helix.supervisor.acquire(_acquire_request("t-runtime"))
    try:
        inspect = _docker(
            "inspect", _container_name(acquired.sandbox_id), "--format", "{{.HostConfig.Runtime}}"
        )
        assert inspect.returncode == 0, inspect.stderr
        assert inspect.stdout.strip() == _OCI_RUNTIME
    finally:
        await helix.supervisor.release(acquired.sandbox_id)


@_gvisor_only
@pytest.mark.asyncio
async def test_gvisor_io_uring_is_unavailable(helix: _Harness) -> None:
    # gVisor does not implement io_uring (a recurring container-escape
    # surface): the syscall returns ENOSYS. This is the compatibility/escape
    # invariant the research flagged (Claude Code #27230) — under gVisor a
    # script reaching for io_uring gets -1/ENOSYS rather than a live ring.
    # ``425`` is the io_uring_setup syscall number on the linux/amd64 CI runner.
    acquired = await helix.supervisor.acquire(_acquire_request("t-iouring"))
    code = (
        "import ctypes, os\n"
        "libc = ctypes.CDLL(None, use_errno=True)\n"
        "rc = libc.syscall(425, 1, 0)\n"  # io_uring_setup(entries=1, params=NULL)
        "print(rc, os.strerror(ctypes.get_errno()) if rc < 0 else 'ring')\n"
    )
    try:
        result = await helix.supervisor.exec(acquired.sandbox_id, code=code)
        assert result.exit_code == 0, result.stderr
        # -1 return → io_uring rejected by gVisor (ENOSYS / EPERM family).
        assert result.stdout.strip().startswith("-1"), result.stdout
    finally:
        await helix.supervisor.release(acquired.sandbox_id)
