"""``exec_python`` tool — Stream F.4 / F.7.

Runs LLM-generated Python in a gVisor sandbox via the Sandbox Supervisor
(F.1): ``acquire`` a sandbox, ``exec`` the code, ``release`` it. The
supervisor mediates the held-pipe runner protocol (F.4a); this tool
speaks only the supervisor's HTTP API behind a small
:class:`SupervisorClient` Protocol — tests inject
:class:`RecordingSupervisorClient`.

Output is truncated to :data:`DEFAULT_OUTPUT_CHAR_CAP` for the LLM
(Mini-ADR F-9 / E-10) — the runner already capped it at ~1 MiB for
transport. The gVisor sandbox (read-only rootfs / cap-drop / no-new-privileges
/ pids-mem-cpu caps / proxy-only egress) is the security boundary; submitted
code is recorded into the tool audit (docs/design/sandbox-audit-evaluation.md).

Cancellation (F.7): E.15 races the whole tool dispatch in
``run_cancellable``, so a cancelled run surfaces here as
:class:`asyncio.CancelledError` on the ``exec`` ``await``. The tool then
``destroy``s the sandbox with ``reason="cancelled"`` — a forced SIGKILL
teardown — instead of a routine ``release`` (Mini-ADR F-8).
"""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

import httpx

from helix_agent.common.observability import inject_context
from orchestrator.tools.registry import ToolBlockedError, ToolContext, ToolResult, ToolSpec

logger = logging.getLogger(__name__)

#: Per-stream cap on stdout / stderr handed back to the LLM (Mini-ADR F-9).
DEFAULT_OUTPUT_CHAR_CAP = 20_000
_TRUNCATION_MARKER = "...[truncated]"
_DEFAULT_TIMEOUT_S = 60.0
#: Thread label used for ``acquire`` when the run has no id (ad-hoc call).
_FALLBACK_THREAD_ID = "exec-python"
#: ``destroy`` reason for a cancelled run — drives the supervisor's
#: forced-teardown audit (Mini-ADR F-8).
_CANCELLED_REASON = "cancelled"


@dataclass(frozen=True)
class SandboxOutcome:
    """One code execution's outcome, as the supervisor reports it."""

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool


@dataclass(frozen=True)
class EgressContext:
    """Per-agent sandbox egress binding (sandbox-egress §3.3).

    Carries the manifest's ``sandbox.network.egress`` policy plus the agent
    identity the supervisor needs to mint the per-sandbox egress token. Bound
    once per build and injected into every ``acquire`` by
    :class:`_EgressBindingClient` — so no per-tool threading is needed.
    """

    policy: str  # manifest egress: "none" | "direct" | "proxy"
    agent_name: str
    agent_version: str
    #: Optional per-agent host allowlist (sandbox-egress Phase 2). Empty → any
    #: public host (audited); non-empty → only these hosts pass at the proxy.
    allowlist: tuple[str, ...] = ()


def _traced_headers() -> dict[str, str]:
    """Outbound headers carrying the active W3C trace context (A.8).

    ``inject_context`` writes ``traceparent`` from the current OTel span so
    the sandbox-supervisor continues the same trace. The supervisor is the
    one trusted internal HTTP hop (see 20-observability § 5.8); external
    egress (``http``/``mcp``/LLM tools) deliberately does *not* propagate.
    No active span (OTel uninitialized, e.g. tests) → nothing is written.
    """
    headers: dict[str, str] = {}
    inject_context(headers)
    return headers


class SandboxSupervisorError(RuntimeError):
    """A Sandbox Supervisor HTTP call failed.

    Raised by :class:`HTTPSupervisorClient`; :class:`ExecPythonTool`
    lets it propagate so the ReAct ``tools`` node wraps it into a
    ``ToolMessage(status="error")`` (Mini-ADR E-12).
    """


@runtime_checkable
class SupervisorClient(Protocol):
    """The Sandbox Supervisor operations the tool needs."""

    async def acquire(
        self,
        *,
        tenant_id: UUID,
        thread_id: str,
        user_id: UUID | None = None,
        image_variant: str | None = None,
        seed_files: tuple[tuple[str, bytes], ...] = (),
        egress: EgressContext | None = None,
    ) -> UUID:
        """Launch a sandbox for the tenant; return its id.

        ``user_id`` set → the sandbox mounts that user's persistent
        workspace volume (Stream J.15); ``None`` → an ephemeral tmpfs.
        ``image_variant`` (Stream OFFICE-1a) selects the image (``"office"``
        → office-libs image; else the default minimal image).
        ``seed_files`` (skill-runtime §5.1) are ``(relpath, bytes)`` pairs the
        supervisor materializes under ``/workspace`` before first exec — the
        agent's activated skill files.
        ``egress`` (sandbox-egress §3.3) carries the agent's egress policy +
        identity; normally injected by :class:`_EgressBindingClient`, so
        callers (``run_in_sandbox``) leave it ``None``.
        """

    async def exec(self, *, sandbox_id: UUID, code: str, timeout_s: int | None) -> SandboxOutcome:
        """Run ``code`` in the sandbox; return its captured outcome."""

    async def release(self, *, sandbox_id: UUID) -> None:
        """Routine sandbox teardown (graceful)."""

    async def destroy(self, *, sandbox_id: UUID, reason: str) -> None:
        """Forced sandbox teardown (SIGKILL); ``reason`` is audited."""

    async def read_workspace_file(self, *, tenant_id: UUID, user_id: UUID, path: str) -> bytes:
        """Read a file from a user's persistent workspace volume (J.9 artifact download)."""

    async def reap(self, *, force: bool) -> int:
        """Run the idle-session sweep now; return how many were reaped.

        ``force=True`` reaps every active session regardless of idle age
        (deterministic teardown for ops + the M0→M1 Gate E2E); ``force=False``
        runs the normal idle-TTL sweep. Persistent workspace volumes are
        preserved — only sessions are destroyed (Stream P, Mini-ADR P-14)."""


@dataclass
class HTTPSupervisorClient:
    """Production :class:`SupervisorClient` — calls the supervisor's HTTP API.

    A non-2xx response raises :class:`SandboxSupervisorError`.
    """

    base_url: str
    timeout_s: float = _DEFAULT_TIMEOUT_S
    #: Test seam — inject ``httpx.MockTransport`` / ``ASGITransport`` to
    #: exercise the wire layer (e.g. the A.8 traceparent round-trip).
    #: Production leaves it ``None`` (real network transport).
    transport: httpx.AsyncBaseTransport | None = None

    def _make_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.timeout_s, transport=self.transport)

    async def acquire(
        self,
        *,
        tenant_id: UUID,
        thread_id: str,
        user_id: UUID | None = None,
        image_variant: str | None = None,
        seed_files: tuple[tuple[str, bytes], ...] = (),
        egress: EgressContext | None = None,
    ) -> UUID:
        payload: dict[str, Any] = {"tenant_id": str(tenant_id), "thread_id": thread_id}
        if user_id is not None:
            payload["user_id"] = str(user_id)
        if image_variant is not None:
            payload["image_variant"] = image_variant
        if seed_files:
            payload["seed_files"] = [
                {"path": path, "content_b64": base64.b64encode(data).decode("ascii")}
                for path, data in seed_files
            ]
        if egress is not None:
            payload["egress"] = egress.policy
            payload["agent_name"] = egress.agent_name
            payload["agent_version"] = egress.agent_version
            if egress.allowlist:
                payload["egress_allowlist"] = list(egress.allowlist)
        body = await self._post("/v1/sandboxes:acquire", json=payload)
        return UUID(str(body["sandbox_id"]))

    async def exec(self, *, sandbox_id: UUID, code: str, timeout_s: int | None) -> SandboxOutcome:
        payload: dict[str, Any] = {"code": code}
        if timeout_s is not None:
            payload["timeout_s"] = timeout_s
        body = await self._post(f"/v1/sandboxes/{sandbox_id}:exec", json=payload)
        return SandboxOutcome(
            stdout=str(body.get("stdout", "")),
            stderr=str(body.get("stderr", "")),
            exit_code=int(body.get("exit_code", -1)),
            timed_out=bool(body.get("timed_out", False)),
        )

    async def release(self, *, sandbox_id: UUID) -> None:
        await self._post(f"/v1/sandboxes/{sandbox_id}:release", json=None, expect_body=False)

    async def destroy(self, *, sandbox_id: UUID, reason: str) -> None:
        await self._post(
            f"/v1/sandboxes/{sandbox_id}:destroy",
            json={"reason": reason},
            expect_body=False,
        )

    async def reap(self, *, force: bool) -> int:
        body = await self._post("/v1/sandboxes:reap", json={"force": force})
        return int(body.get("reaped_count", 0))

    async def read_workspace_file(self, *, tenant_id: UUID, user_id: UUID, path: str) -> bytes:
        url = f"{self.base_url}/v1/workspaces/{tenant_id}/{user_id}/file"
        async with self._make_client() as client:
            try:
                response = await client.get(url, params={"path": path}, headers=_traced_headers())
            except httpx.HTTPError as exc:
                msg = f"sandbox supervisor unreachable ({url}): {exc}"
                raise SandboxSupervisorError(msg) from exc
        if response.is_error:
            msg = (
                f"sandbox supervisor workspace read failed: {response.status_code} {response.text}"
            )
            raise SandboxSupervisorError(msg)
        return response.content

    async def _post(
        self,
        path: str,
        *,
        json: Mapping[str, Any] | None,
        expect_body: bool = True,
    ) -> Mapping[str, Any]:
        async with self._make_client() as client:
            try:
                response = await client.post(
                    f"{self.base_url}{path}", json=json, headers=_traced_headers()
                )
            except httpx.HTTPError as exc:
                msg = f"sandbox supervisor unreachable ({path}): {exc}"
                raise SandboxSupervisorError(msg) from exc
        if response.is_error:
            msg = f"sandbox supervisor {path} failed: {response.status_code} {response.text}"
            raise SandboxSupervisorError(msg)
        if not expect_body:
            return {}
        data = response.json()
        if not isinstance(data, Mapping):
            msg = f"sandbox supervisor {path} returned a non-object body"
            raise SandboxSupervisorError(msg)
        return data


@dataclass
class RecordingSupervisorClient:
    """In-memory :class:`SupervisorClient` for dev / tests.

    Records the acquire / exec / release / destroy calls and returns the
    pre-set :attr:`outcome`. Set ``exec_error`` to drive the error path
    (an :class:`asyncio.CancelledError` drives the cancellation path),
    and ``destroy_error`` to drive a failed teardown.
    """

    outcome: SandboxOutcome = field(
        default_factory=lambda: SandboxOutcome(stdout="", stderr="", exit_code=0, timed_out=False)
    )
    exec_error: BaseException | None = None
    destroy_error: Exception | None = None
    workspace_file: bytes = b""
    workspace_file_error: Exception | None = None
    acquired: list[tuple[UUID, str, UUID | None, str | None, tuple[tuple[str, bytes], ...]]] = (
        field(default_factory=list)
    )
    #: sandbox-egress §3.3 — the ``EgressContext`` passed to each acquire (kept
    #: out of the ``acquired`` 5-tuple so existing tests stay unchanged).
    egress_calls: list[EgressContext | None] = field(default_factory=list)
    execs: list[tuple[UUID, str]] = field(default_factory=list)
    released: list[UUID] = field(default_factory=list)
    destroyed: list[tuple[UUID, str]] = field(default_factory=list)
    workspace_reads: list[tuple[UUID, UUID, str]] = field(default_factory=list)
    reaped: list[bool] = field(default_factory=list)
    reap_count: int = 0
    _next_id: int = 0

    async def acquire(
        self,
        *,
        tenant_id: UUID,
        thread_id: str,
        user_id: UUID | None = None,
        image_variant: str | None = None,
        seed_files: tuple[tuple[str, bytes], ...] = (),
        egress: EgressContext | None = None,
    ) -> UUID:
        self.acquired.append((tenant_id, thread_id, user_id, image_variant, seed_files))
        self.egress_calls.append(egress)
        self._next_id += 1
        return UUID(int=self._next_id)

    async def exec(self, *, sandbox_id: UUID, code: str, timeout_s: int | None) -> SandboxOutcome:
        del timeout_s
        self.execs.append((sandbox_id, code))
        if self.exec_error is not None:
            raise self.exec_error
        return self.outcome

    async def release(self, *, sandbox_id: UUID) -> None:
        self.released.append(sandbox_id)

    async def destroy(self, *, sandbox_id: UUID, reason: str) -> None:
        if self.destroy_error is not None:
            raise self.destroy_error
        self.destroyed.append((sandbox_id, reason))

    async def read_workspace_file(self, *, tenant_id: UUID, user_id: UUID, path: str) -> bytes:
        self.workspace_reads.append((tenant_id, user_id, path))
        if self.workspace_file_error is not None:
            raise self.workspace_file_error
        return self.workspace_file

    async def reap(self, *, force: bool) -> int:
        self.reaped.append(force)
        return self.reap_count


@dataclass
class _EgressBindingClient:
    """Wraps a :class:`SupervisorClient`, injecting a fixed :class:`EgressContext`
    into every ``acquire`` (sandbox-egress §3.3).

    Bound once per agent build (``agent_factory``) around the shared supervisor
    client, so all sandbox tools get the agent's egress policy + identity on the
    wire without threading it through each tool. Every other call delegates
    unchanged.
    """

    inner: SupervisorClient
    egress: EgressContext

    async def acquire(
        self,
        *,
        tenant_id: UUID,
        thread_id: str,
        user_id: UUID | None = None,
        image_variant: str | None = None,
        seed_files: tuple[tuple[str, bytes], ...] = (),
        egress: EgressContext | None = None,
    ) -> UUID:
        # The bound context wins — callers don't supply egress themselves.
        return await self.inner.acquire(
            tenant_id=tenant_id,
            thread_id=thread_id,
            user_id=user_id,
            image_variant=image_variant,
            seed_files=seed_files,
            egress=self.egress,
        )

    async def exec(self, *, sandbox_id: UUID, code: str, timeout_s: int | None) -> SandboxOutcome:
        return await self.inner.exec(sandbox_id=sandbox_id, code=code, timeout_s=timeout_s)

    async def release(self, *, sandbox_id: UUID) -> None:
        await self.inner.release(sandbox_id=sandbox_id)

    async def destroy(self, *, sandbox_id: UUID, reason: str) -> None:
        await self.inner.destroy(sandbox_id=sandbox_id, reason=reason)

    async def read_workspace_file(self, *, tenant_id: UUID, user_id: UUID, path: str) -> bytes:
        return await self.inner.read_workspace_file(tenant_id=tenant_id, user_id=user_id, path=path)

    async def reap(self, *, force: bool) -> int:
        return await self.inner.reap(force=force)


def bind_egress(client: SupervisorClient, egress: EgressContext | None) -> SupervisorClient:
    """Wrap ``client`` so every acquire carries ``egress`` (no-op if ``None``)."""
    if egress is None:
        return client
    return _EgressBindingClient(inner=client, egress=egress)


async def run_in_sandbox(
    client: SupervisorClient,
    *,
    code: str,
    timeout_s: int | None,
    ctx: ToolContext,
    persistent_workspace: bool,
    tool_label: str,
    fallback_thread_id: str,
    image_variant: str | None = None,
    seed_files: tuple[tuple[str, bytes], ...] = (),
) -> SandboxOutcome:
    """Acquire a sandbox, run ``code``, and tear it down — shared by the
    ``exec_python`` (F.4) and ``bash`` (TE-5) tools.

    Both tools execute arbitrary code in the same gVisor sandbox (bash
    rides the exec channel as a ``subprocess`` wrapper), so the
    acquire / exec / release / cancel-then-destroy dance lives here once.
    Tenant-scoped (the sandbox quota is per-tenant); a cancellation
    mid-exec SIGKILLs the sandbox rather than releasing it gracefully
    (Mini-ADR F-8 — a graceful release would burn the gate-#8 ≤1s budget).
    """
    if ctx.tenant_id is None:
        msg = f"{tool_label} requires a tenant binding (ctx.tenant_id)"
        raise ToolBlockedError(msg)
    thread_id = str(ctx.run_id) if ctx.run_id is not None else fallback_thread_id
    # Stream J.15 — a persistent-workspace agent acquires against the run's
    # user volume; without the opt-in (or a user binding) the sandbox falls
    # back to an ephemeral tmpfs.
    user_id = ctx.user_id if persistent_workspace else None
    sandbox_id = await client.acquire(
        tenant_id=ctx.tenant_id,
        thread_id=thread_id,
        user_id=user_id,
        image_variant=image_variant,
        seed_files=seed_files,
    )
    cancelled = False
    try:
        return await client.exec(sandbox_id=sandbox_id, code=code, timeout_s=timeout_s)
    except asyncio.CancelledError:
        cancelled = True
        raise
    finally:
        if cancelled:
            await _destroy_quietly(
                client, sandbox_id, reason=_CANCELLED_REASON, tool_label=tool_label
            )
        else:
            await _release_quietly(client, sandbox_id, tool_label=tool_label)


def format_sandbox_outcome(outcome: SandboxOutcome, output_char_cap: int) -> ToolResult:
    """Render a :class:`SandboxOutcome` into the ``ToolResult`` the LLM sees.

    Head-truncates stdout / stderr to ``output_char_cap`` (Mini-ADR F-9)
    and surfaces ``exit_code`` / ``timed_out`` in both the text and the
    structured ``meta``. Shared by ``exec_python`` and ``bash``.

    When either stream was cut, ``full_content`` carries the complete
    rendering so the tools node can externalize it to the workspace
    (Stream CM-5 recoverable compression).
    """
    stdout, cut_out = _truncate(outcome.stdout, output_char_cap)
    stderr, cut_err = _truncate(outcome.stderr, output_char_cap)

    def _render(out: str, err: str) -> str:
        parts: list[str] = []
        if out:
            parts.append(f"stdout:\n{out}")
        if err:
            parts.append(f"stderr:\n{err}")
        if not parts:
            parts.append("(no output)")
        if outcome.timed_out:
            parts.append("[execution timed out]")
        parts.append(f"exit_code: {outcome.exit_code}")
        return "\n\n".join(parts)

    truncated = cut_out or cut_err
    return ToolResult(
        content=_render(stdout, stderr),
        meta={
            "exit_code": outcome.exit_code,
            "timed_out": outcome.timed_out,
            "truncated": truncated,
        },
        full_content=_render(outcome.stdout, outcome.stderr) if truncated else None,
    )


async def _release_quietly(client: SupervisorClient, sandbox_id: UUID, *, tool_label: str) -> None:
    # A release failure must never mask the exec result / error.
    try:
        await client.release(sandbox_id=sandbox_id)
    except Exception:
        logger.exception("%s.release_failed sandbox=%s", tool_label, sandbox_id)


async def _destroy_quietly(
    client: SupervisorClient, sandbox_id: UUID, *, reason: str, tool_label: str
) -> None:
    # A destroy failure must not mask the cancellation — the supervisor's
    # TTL reaper is the backstop for a leaked container.
    try:
        await client.destroy(sandbox_id=sandbox_id, reason=reason)
    except Exception:
        logger.exception("%s.destroy_failed sandbox=%s", tool_label, sandbox_id)


@dataclass
class ExecPythonTool:
    """Sandbox Python execution exposed to the LLM as ``exec_python``."""

    client: SupervisorClient
    output_char_cap: int = DEFAULT_OUTPUT_CHAR_CAP
    #: Stream J.15 — when ``True`` and the run is user-scoped, acquire
    #: the sandbox against the user's persistent workspace volume so
    #: files survive across runs. Set from ``SandboxSpec.filesystem``.
    persistent_workspace: bool = False
    #: Stream OFFICE-1a — sandbox image variant ("office" → office-libs
    #: image). Set from ``SandboxSpec.image_variant``; ``None`` → minimal.
    image_variant: str | None = None
    #: skill-runtime §5.1 — the agent's activated skill files, materialized
    #: under ``/workspace/skills/<name>/`` on each acquire. Set at build.
    skill_seed_files: tuple[tuple[str, bytes], ...] = ()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="exec_python",
            description=(
                "Execute a Python 3 snippet in an isolated sandbox and return "
                "its stdout / stderr / exit code. Use for calculations, data "
                "transforms, or anything better done by running code."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "The Python source to execute.",
                    },
                    "timeout_s": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 300,
                        "description": "Execution timeout in seconds (optional).",
                    },
                },
                "required": ["code"],
            },
        )

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        code = self._require_code(args)
        timeout_s = coerce_timeout(args.get("timeout_s"))
        outcome = await run_in_sandbox(
            self.client,
            code=code,
            timeout_s=timeout_s,
            ctx=ctx,
            persistent_workspace=self.persistent_workspace,
            tool_label="exec_python",
            fallback_thread_id=_FALLBACK_THREAD_ID,
            image_variant=self.image_variant,
            seed_files=self.skill_seed_files,
        )
        return format_sandbox_outcome(outcome, self.output_char_cap)

    def _require_code(self, args: Mapping[str, Any]) -> str:
        raw = args.get("code")
        if not isinstance(raw, str) or not raw.strip():
            msg = "exec_python requires a non-empty 'code' string"
            raise ValueError(msg)
        return raw


def coerce_timeout(raw: object) -> int | None:
    """Read an optional ``timeout_s`` arg; reject ``bool`` and out-of-range."""
    if raw is None or isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return max(1, min(300, raw))


def _truncate(text: str, cap: int) -> tuple[str, bool]:
    """Head-truncate ``text`` to ``cap`` chars; return ``(text, was_truncated)``."""
    if len(text) <= cap:
        return text, False
    return text[:cap] + _TRUNCATION_MARKER, True
