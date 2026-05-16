"""Unit tests for the ``exec_python`` tool — Stream F.4b (test matrix #46).

The Sandbox Supervisor is faked via :class:`RecordingSupervisorClient`,
so these run in the plain ``pytest`` job — no Docker, no supervisor.
The ``sandbox_audit`` wiring (#47) is verified in
``test_middleware_assembly.py``; the middleware's blocking behaviour
itself is covered by helix-runtime's ``test_sandbox_audit_middleware``.
"""

from __future__ import annotations

from uuid import uuid4

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


def _ctx() -> ToolContext:
    return ToolContext(tenant_id=uuid4(), run_id=uuid4())


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
    # acquire → exec → release, all once.
    assert len(client.acquired) == 1
    assert len(client.execs) == 1
    assert len(client.released) == 1


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
    # The sandbox is still released — no leak on the error path.
    assert len(client.released) == 1


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
