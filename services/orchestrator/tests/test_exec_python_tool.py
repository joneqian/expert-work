"""Unit tests for the ``exec_python`` tool — Stream F.4b (test matrix #46).

The Sandbox Supervisor is faked via :class:`RecordingSupervisorClient`,
so these run in the plain ``pytest`` job — no Docker, no supervisor.
The ``sandbox_audit`` wiring (#47) is verified in
``test_middleware_assembly.py``; the middleware's blocking behaviour
itself is covered by helix-runtime's ``test_sandbox_audit_middleware``.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest

from helix_agent.protocol import BuiltinToolSpec
from orchestrator.errors import AgentFactoryError
from orchestrator.tools import (
    DEFAULT_OUTPUT_CHAR_CAP,
    ExecPythonTool,
    RecordingSupervisorClient,
    SandboxOutcome,
    ToolBlockedError,
    ToolContext,
    ToolEnv,
    build_tool_registry,
)


def _ctx(*, user_id: UUID | None = None) -> ToolContext:
    return ToolContext(tenant_id=uuid4(), run_id=uuid4(), user_id=user_id)


# ---------------------------------------------------------------------------
# the tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exec_python_runs_code_and_returns_output() -> None:
    client = RecordingSupervisorClient(
        outcome=SandboxOutcome(stdout="42\n", stderr="", exit_code=0, timed_out=False)
    )
    tool = ExecPythonTool(client=client)

    result = await tool.call({"code": "print(6 * 7)"}, ctx=_ctx())

    assert "42" in result.content
    assert "exit_code: 0" in result.content
    assert result.meta["exit_code"] == 0
    # Un-truncated output carries no overflow payload (Stream CM-5).
    assert result.full_content is None
    # acquire → exec → release, all once; a clean run never force-destroys.
    assert len(client.acquired) == 1
    assert len(client.execs) == 1
    assert len(client.released) == 1
    assert client.destroyed == []


@pytest.mark.asyncio
async def test_exec_python_truncates_oversized_output() -> None:
    # The supervisor returns 50k chars; the tool caps each stream at 20k.
    client = RecordingSupervisorClient(
        outcome=SandboxOutcome(stdout="x" * 50_000, stderr="", exit_code=0, timed_out=False)
    )
    tool = ExecPythonTool(client=client)

    result = await tool.call({"code": "print('x' * 50000)"}, ctx=_ctx())

    assert result.meta["truncated"] is True
    # content = "stdout:\n" + capped-stdout + marker + exit-code line.
    assert len(result.content) < DEFAULT_OUTPUT_CHAR_CAP + 200
    assert "[truncated]" in result.content
    # Stream CM-5: the complete rendering rides along for externalization.
    assert result.full_content is not None
    assert "x" * 50_000 in result.full_content
    assert "exit_code: 0" in result.full_content
    assert "[truncated]" not in result.full_content


@pytest.mark.asyncio
async def test_exec_python_reports_timeout() -> None:
    client = RecordingSupervisorClient(
        outcome=SandboxOutcome(stdout="", stderr="", exit_code=-1, timed_out=True)
    )
    tool = ExecPythonTool(client=client)

    result = await tool.call({"code": "while True: pass"}, ctx=_ctx())

    assert result.meta["timed_out"] is True
    assert "timed out" in result.content


@pytest.mark.asyncio
async def test_exec_python_requires_tenant_binding() -> None:
    tool = ExecPythonTool(client=RecordingSupervisorClient())
    with pytest.raises(ToolBlockedError, match="tenant binding"):
        await tool.call({"code": "print(1)"}, ctx=ToolContext(tenant_id=None))


@pytest.mark.asyncio
async def test_exec_python_requires_code() -> None:
    tool = ExecPythonTool(client=RecordingSupervisorClient())
    with pytest.raises(ValueError, match="non-empty 'code'"):
        await tool.call({"code": "   "}, ctx=_ctx())


@pytest.mark.asyncio
async def test_exec_python_releases_sandbox_even_on_exec_error() -> None:
    client = RecordingSupervisorClient(exec_error=RuntimeError("runner died"))
    tool = ExecPythonTool(client=client)

    with pytest.raises(RuntimeError, match="runner died"):
        await tool.call({"code": "print(1)"}, ctx=_ctx())
    # An ordinary error is a graceful release — not a forced destroy.
    assert len(client.released) == 1
    assert client.destroyed == []


@pytest.mark.asyncio
async def test_exec_python_passes_timeout_through() -> None:
    client = RecordingSupervisorClient()
    tool = ExecPythonTool(client=client)

    await tool.call({"code": "print(1)", "timeout_s": 15}, ctx=_ctx())
    # The supervisor was acquired with a thread label derived from run_id.
    assert client.execs[0][1] == "print(1)"


def test_exec_python_spec_advertises_code_param() -> None:
    spec = ExecPythonTool(client=RecordingSupervisorClient()).spec
    assert spec.name == "exec_python"
    assert "code" in spec.parameters["required"]


# ---------------------------------------------------------------------------
# J.15 — persistent workspace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exec_python_persistent_workspace_passes_user_id() -> None:
    # persistent_workspace + a user-scoped run → acquire carries user_id.
    client = RecordingSupervisorClient()
    tool = ExecPythonTool(client=client, persistent_workspace=True)
    user_id = uuid4()

    await tool.call({"code": "print(1)"}, ctx=_ctx(user_id=user_id))

    assert client.acquired[0][2] == user_id


@pytest.mark.asyncio
async def test_exec_python_default_does_not_pass_user_id() -> None:
    # Without the opt-in the sandbox stays ephemeral even for a user run.
    client = RecordingSupervisorClient()
    tool = ExecPythonTool(client=client)

    await tool.call({"code": "print(1)"}, ctx=_ctx(user_id=uuid4()))

    assert client.acquired[0][2] is None


@pytest.mark.asyncio
async def test_exec_python_persistent_workspace_without_user_falls_back() -> None:
    # persistent_workspace on but the run has no user binding → tmpfs.
    client = RecordingSupervisorClient()
    tool = ExecPythonTool(client=client, persistent_workspace=True)

    await tool.call({"code": "print(1)"}, ctx=_ctx())

    assert client.acquired[0][2] is None


# ---------------------------------------------------------------------------
# cancellation — Stream F.7 (test matrix #58)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exec_python_destroys_sandbox_on_cancellation() -> None:
    """A cancelled run force-destroys the sandbox, never a graceful release."""
    # E.15 cancels the dispatch task → CancelledError on the exec ``await``.
    client = RecordingSupervisorClient(exec_error=asyncio.CancelledError())
    tool = ExecPythonTool(client=client)

    with pytest.raises(asyncio.CancelledError):
        await tool.call({"code": "while True: pass"}, ctx=_ctx())

    assert client.released == []
    assert len(client.destroyed) == 1
    # The supervisor sees reason="cancelled" → SIGKILL + force-destroy audit.
    assert client.destroyed[0][1] == "cancelled"


@pytest.mark.asyncio
async def test_exec_python_cancellation_destroy_failure_is_swallowed() -> None:
    """A failed destroy must not mask the cancellation (TTL reaper backstops)."""
    client = RecordingSupervisorClient(
        exec_error=asyncio.CancelledError(),
        destroy_error=RuntimeError("supervisor unreachable"),
    )
    tool = ExecPythonTool(client=client)

    # The CancelledError still propagates — the destroy error is swallowed.
    with pytest.raises(asyncio.CancelledError):
        await tool.call({"code": "while True: pass"}, ctx=_ctx())


# ---------------------------------------------------------------------------
# assembly — the exec_python builtin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exec_python_builtin_assembled_when_supervisor_present() -> None:
    env = ToolEnv(supervisor_client=RecordingSupervisorClient())
    registry = await build_tool_registry([BuiltinToolSpec(name="exec_python")], tool_env=env)
    assert registry.get("exec_python") is not None


@pytest.mark.asyncio
async def test_exec_python_builtin_missing_supervisor_raises() -> None:
    with pytest.raises(AgentFactoryError, match="Sandbox Supervisor"):
        await build_tool_registry([BuiltinToolSpec(name="exec_python")], tool_env=ToolEnv())


@pytest.mark.asyncio
async def test_exec_python_builtin_carries_persistent_workspace_flag() -> None:
    # The manifest's sandbox.filesystem.persistent_workspace reaches the tool.
    env = ToolEnv(supervisor_client=RecordingSupervisorClient())
    registry = await build_tool_registry(
        [BuiltinToolSpec(name="exec_python")], tool_env=env, persistent_workspace=True
    )
    tool = registry.get("exec_python")
    assert isinstance(tool, ExecPythonTool)
    assert tool.persistent_workspace is True
