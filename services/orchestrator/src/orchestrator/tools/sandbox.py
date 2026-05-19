"""``exec_python`` tool — Stream F.4 / F.7.

Runs LLM-generated Python in a gVisor sandbox via the Sandbox Supervisor
(F.1): ``acquire`` a sandbox, ``exec`` the code, ``release`` it. The
supervisor mediates the held-pipe runner protocol (F.4a); this tool
speaks only the supervisor's HTTP API behind a small
:class:`SupervisorClient` Protocol — tests inject
:class:`RecordingSupervisorClient`.

Output is truncated to :data:`DEFAULT_OUTPUT_CHAR_CAP` for the LLM
(Mini-ADR F-9 / E-10) — the runner already capped it at ~1 MiB for
transport. The ``sandbox_audit`` middleware (E.10) blocks dangerous
code *before* this tool ever dispatches.

Cancellation (F.7): E.15 races the whole tool dispatch in
``run_cancellable``, so a cancelled run surfaces here as
:class:`asyncio.CancelledError` on the ``exec`` ``await``. The tool then
``destroy``s the sandbox with ``reason="cancelled"`` — a forced SIGKILL
teardown — instead of a routine ``release`` (Mini-ADR F-8).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

import httpx

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
        self, *, tenant_id: UUID, thread_id: str, user_id: UUID | None = None
    ) -> UUID:
        """Launch a sandbox for the tenant; return its id.

        ``user_id`` set → the sandbox mounts that user's persistent
        workspace volume (Stream J.15); ``None`` → an ephemeral tmpfs.
        """

    async def exec(self, *, sandbox_id: UUID, code: str, timeout_s: int | None) -> SandboxOutcome:
        """Run ``code`` in the sandbox; return its captured outcome."""

    async def release(self, *, sandbox_id: UUID) -> None:
        """Routine sandbox teardown (graceful)."""

    async def destroy(self, *, sandbox_id: UUID, reason: str) -> None:
        """Forced sandbox teardown (SIGKILL); ``reason`` is audited."""

    async def read_workspace_file(self, *, tenant_id: UUID, user_id: UUID, path: str) -> bytes:
        """Read a file from a user's persistent workspace volume (J.9 artifact download)."""


@dataclass
class HTTPSupervisorClient:
    """Production :class:`SupervisorClient` — calls the supervisor's HTTP API.

    A non-2xx response raises :class:`SandboxSupervisorError`.
    """

    base_url: str
    timeout_s: float = _DEFAULT_TIMEOUT_S

    async def acquire(
        self, *, tenant_id: UUID, thread_id: str, user_id: UUID | None = None
    ) -> UUID:
        payload: dict[str, Any] = {"tenant_id": str(tenant_id), "thread_id": thread_id}
        if user_id is not None:
            payload["user_id"] = str(user_id)
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

    async def read_workspace_file(self, *, tenant_id: UUID, user_id: UUID, path: str) -> bytes:
        url = f"{self.base_url}/v1/workspaces/{tenant_id}/{user_id}/file"
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            try:
                response = await client.get(url, params={"path": path})
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
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            try:
                response = await client.post(f"{self.base_url}{path}", json=json)
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
    acquired: list[tuple[UUID, str, UUID | None]] = field(default_factory=list)
    execs: list[tuple[UUID, str]] = field(default_factory=list)
    released: list[UUID] = field(default_factory=list)
    destroyed: list[tuple[UUID, str]] = field(default_factory=list)
    workspace_reads: list[tuple[UUID, UUID, str]] = field(default_factory=list)
    _next_id: int = 0

    async def acquire(
        self, *, tenant_id: UUID, thread_id: str, user_id: UUID | None = None
    ) -> UUID:
        self.acquired.append((tenant_id, thread_id, user_id))
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


@dataclass
class ExecPythonTool:
    """Sandbox Python execution exposed to the LLM as ``exec_python``."""

    client: SupervisorClient
    output_char_cap: int = DEFAULT_OUTPUT_CHAR_CAP
    #: Stream J.15 — when ``True`` and the run is user-scoped, acquire
    #: the sandbox against the user's persistent workspace volume so
    #: files survive across runs. Set from ``SandboxSpec.filesystem``.
    persistent_workspace: bool = False

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
        timeout_s = _coerce_timeout(args.get("timeout_s"))
        if ctx.tenant_id is None:
            # exec_python is tenant-scoped — the sandbox quota is per-tenant.
            msg = "exec_python requires a tenant binding (ctx.tenant_id)"
            raise ToolBlockedError(msg)
        thread_id = str(ctx.run_id) if ctx.run_id is not None else _FALLBACK_THREAD_ID
        # Stream J.15 — a persistent-workspace agent acquires against the
        # run's user volume; without the opt-in (or a user binding) the
        # sandbox falls back to an ephemeral tmpfs.
        user_id = ctx.user_id if self.persistent_workspace else None

        sandbox_id = await self.client.acquire(
            tenant_id=ctx.tenant_id, thread_id=thread_id, user_id=user_id
        )
        cancelled = False
        try:
            outcome = await self.client.exec(sandbox_id=sandbox_id, code=code, timeout_s=timeout_s)
        except asyncio.CancelledError:
            # The run was cancelled mid-exec (E.15 races tool dispatch in
            # ``run_cancellable``). SIGKILL the sandbox now — a graceful
            # release would burn the gate-#8 ≤1s budget (Mini-ADR F-8).
            cancelled = True
            raise
        finally:
            if cancelled:
                await self._destroy_quietly(sandbox_id, reason=_CANCELLED_REASON)
            else:
                await self._release_quietly(sandbox_id)
        return self._format(outcome)

    # ------------------------------------------------------------------

    def _require_code(self, args: Mapping[str, Any]) -> str:
        raw = args.get("code")
        if not isinstance(raw, str) or not raw.strip():
            msg = "exec_python requires a non-empty 'code' string"
            raise ValueError(msg)
        return raw

    async def _release_quietly(self, sandbox_id: UUID) -> None:
        # A release failure must never mask the exec result / error.
        try:
            await self.client.release(sandbox_id=sandbox_id)
        except Exception:
            logger.exception("exec_python.release_failed sandbox=%s", sandbox_id)

    async def _destroy_quietly(self, sandbox_id: UUID, *, reason: str) -> None:
        # A destroy failure must not mask the cancellation — the
        # supervisor's TTL reaper is the backstop for a leaked container.
        try:
            await self.client.destroy(sandbox_id=sandbox_id, reason=reason)
        except Exception:
            logger.exception("exec_python.destroy_failed sandbox=%s", sandbox_id)

    def _format(self, outcome: SandboxOutcome) -> ToolResult:
        stdout, cut_out = _truncate(outcome.stdout, self.output_char_cap)
        stderr, cut_err = _truncate(outcome.stderr, self.output_char_cap)

        parts: list[str] = []
        if stdout:
            parts.append(f"stdout:\n{stdout}")
        if stderr:
            parts.append(f"stderr:\n{stderr}")
        if not parts:
            parts.append("(no output)")
        if outcome.timed_out:
            parts.append("[execution timed out]")
        parts.append(f"exit_code: {outcome.exit_code}")

        return ToolResult(
            content="\n\n".join(parts),
            meta={
                "exit_code": outcome.exit_code,
                "timed_out": outcome.timed_out,
                "truncated": cut_out or cut_err,
            },
        )


def _coerce_timeout(raw: object) -> int | None:
    """Read an optional ``timeout_s`` arg; reject ``bool`` and out-of-range."""
    if raw is None or isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return max(1, min(300, raw))


def _truncate(text: str, cap: int) -> tuple[str, bool]:
    """Head-truncate ``text`` to ``cap`` chars; return ``(text, was_truncated)``."""
    if len(text) <= cap:
        return text, False
    return text[:cap] + _TRUNCATION_MARKER, True
