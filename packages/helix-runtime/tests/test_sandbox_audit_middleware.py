"""Unit tests for :class:`SandboxAuditMiddleware` (Stream E.10)."""

from __future__ import annotations

import pytest

from helix_agent.runtime.middleware import (
    Middleware,
    MiddlewareContext,
    SandboxAuditBlockedError,
    SandboxAuditMiddleware,
)

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


async def _terminal(ctx: MiddlewareContext) -> None:
    ctx.payload["terminal_called"] = ctx.payload.get("terminal_called", 0) + 1


def _ctx(tool_name: str = "", **args: object) -> MiddlewareContext:
    return MiddlewareContext(payload={"tool_name": tool_name, "tool_args": dict(args)})


# ---------------------------------------------------------------------------
# Non-sandbox tools pass through unconditionally
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_search_tool_passes_through() -> None:
    mw = SandboxAuditMiddleware()
    ctx = _ctx("web_search", query="anything", code="os.system('rm -rf /')")
    await mw(ctx, _terminal)
    assert ctx.payload["terminal_called"] == 1


@pytest.mark.asyncio
async def test_http_tool_passes_through() -> None:
    mw = SandboxAuditMiddleware()
    ctx = _ctx("http", url="https://evil.com/x", command="rm -rf /")
    await mw(ctx, _terminal)
    assert ctx.payload["terminal_called"] == 1


@pytest.mark.asyncio
async def test_mcp_tool_passes_through() -> None:
    mw = SandboxAuditMiddleware()
    ctx = _ctx("mcp:fs.read_file", path="/etc/passwd")
    await mw(ctx, _terminal)
    assert ctx.payload["terminal_called"] == 1


@pytest.mark.asyncio
async def test_missing_tool_name_passes_through() -> None:
    mw = SandboxAuditMiddleware()
    ctx = MiddlewareContext(payload={})
    await mw(ctx, _terminal)
    assert ctx.payload["terminal_called"] == 1


# ---------------------------------------------------------------------------
# Python AST denylist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_safe_python_passes() -> None:
    mw = SandboxAuditMiddleware()
    ctx = _ctx("exec_python", code="x = 1 + 2\nprint(x)")
    await mw(ctx, _terminal)
    assert ctx.payload["terminal_called"] == 1


@pytest.mark.asyncio
async def test_os_system_blocked() -> None:
    mw = SandboxAuditMiddleware()
    ctx = _ctx("exec_python", code="import os\nos.system('rm -rf /')")
    with pytest.raises(SandboxAuditBlockedError) as excinfo:
        await mw(ctx, _terminal)
    assert excinfo.value.rule == "os.system"


@pytest.mark.asyncio
async def test_subprocess_run_blocked() -> None:
    mw = SandboxAuditMiddleware()
    ctx = _ctx("exec_python", code="import subprocess\nsubprocess.run(['ls'])")
    with pytest.raises(SandboxAuditBlockedError) as excinfo:
        await mw(ctx, _terminal)
    assert excinfo.value.rule == "subprocess.run"


@pytest.mark.asyncio
async def test_subprocess_popen_blocked() -> None:
    mw = SandboxAuditMiddleware()
    ctx = _ctx("exec_python", code="from subprocess import Popen\nsubprocess.Popen(['x'])")
    with pytest.raises(SandboxAuditBlockedError) as excinfo:
        await mw(ctx, _terminal)
    assert excinfo.value.rule == "subprocess.Popen"


@pytest.mark.asyncio
async def test_eval_blocked() -> None:
    mw = SandboxAuditMiddleware()
    ctx = _ctx("exec_python", code="eval('1+1')")
    with pytest.raises(SandboxAuditBlockedError) as excinfo:
        await mw(ctx, _terminal)
    assert excinfo.value.rule == "eval"


@pytest.mark.asyncio
async def test_exec_blocked() -> None:
    mw = SandboxAuditMiddleware()
    ctx = _ctx("exec_python", code="exec('x = 1')")
    with pytest.raises(SandboxAuditBlockedError) as excinfo:
        await mw(ctx, _terminal)
    assert excinfo.value.rule == "exec"


@pytest.mark.asyncio
async def test_dunder_import_blocked() -> None:
    """``__import__('os').system(...)`` is the classic eval-bypass."""
    mw = SandboxAuditMiddleware()
    ctx = _ctx("exec_python", code="__import__('os').system('curl x')")
    with pytest.raises(SandboxAuditBlockedError) as excinfo:
        await mw(ctx, _terminal)
    # Both __import__ and os.system are forbidden; either one wins
    # depending on AST walk order. Stable enough as long as one fires.
    assert excinfo.value.rule in {"__import__", "os.system"}


@pytest.mark.asyncio
async def test_compile_blocked() -> None:
    mw = SandboxAuditMiddleware()
    ctx = _ctx("exec_python", code="compile('1+1', '<x>', 'eval')")
    with pytest.raises(SandboxAuditBlockedError) as excinfo:
        await mw(ctx, _terminal)
    assert excinfo.value.rule == "compile"


@pytest.mark.asyncio
async def test_syntax_error_passes_through_to_sandbox() -> None:
    """Parse failures aren't audit violations — let the sandbox surface them."""
    mw = SandboxAuditMiddleware()
    ctx = _ctx("exec_python", code="def(((")
    await mw(ctx, _terminal)
    assert ctx.payload["terminal_called"] == 1


@pytest.mark.asyncio
async def test_python_with_script_key_inspected() -> None:
    """Sandbox adapters may use 'script' instead of 'code'."""
    mw = SandboxAuditMiddleware()
    ctx = _ctx("exec_python", script="os.system('x')")
    with pytest.raises(SandboxAuditBlockedError):
        await mw(ctx, _terminal)


# ---------------------------------------------------------------------------
# Shell substring denylist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_safe_shell_passes() -> None:
    mw = SandboxAuditMiddleware()
    ctx = _ctx("shell", command="ls -la /workspace")
    await mw(ctx, _terminal)
    assert ctx.payload["terminal_called"] == 1


@pytest.mark.asyncio
async def test_rm_rf_blocked() -> None:
    mw = SandboxAuditMiddleware()
    ctx = _ctx("shell", command="rm -rf /tmp/data")
    with pytest.raises(SandboxAuditBlockedError) as excinfo:
        await mw(ctx, _terminal)
    assert excinfo.value.rule == "rm -rf"


@pytest.mark.asyncio
async def test_cloud_metadata_curl_blocked() -> None:
    mw = SandboxAuditMiddleware()
    ctx = _ctx("shell", command="curl http://169.254.169.254/latest/meta-data/iam/")
    with pytest.raises(SandboxAuditBlockedError) as excinfo:
        await mw(ctx, _terminal)
    assert excinfo.value.rule == "169.254.169.254"


@pytest.mark.asyncio
async def test_gce_metadata_blocked() -> None:
    mw = SandboxAuditMiddleware()
    ctx = _ctx("shell", command="wget metadata.google.internal/computeMetadata/v1/")
    with pytest.raises(SandboxAuditBlockedError) as excinfo:
        await mw(ctx, _terminal)
    assert excinfo.value.rule == "metadata.google.internal"


@pytest.mark.asyncio
async def test_fork_bomb_blocked() -> None:
    mw = SandboxAuditMiddleware()
    ctx = _ctx("shell", command=":(){:|:&};:")
    with pytest.raises(SandboxAuditBlockedError):
        await mw(ctx, _terminal)


@pytest.mark.asyncio
async def test_dd_disk_wipe_blocked() -> None:
    mw = SandboxAuditMiddleware()
    ctx = _ctx("shell", command="dd if=/dev/zero of=/dev/sda")
    with pytest.raises(SandboxAuditBlockedError) as excinfo:
        await mw(ctx, _terminal)
    assert excinfo.value.rule == "dd if=/dev/zero"


@pytest.mark.asyncio
async def test_shell_with_cmd_key_inspected() -> None:
    mw = SandboxAuditMiddleware()
    ctx = _ctx("shell", cmd="rm -rf /")
    with pytest.raises(SandboxAuditBlockedError):
        await mw(ctx, _terminal)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_code_arg_safe_pass() -> None:
    """Sandbox tool without a code/command key → audit just passes."""
    mw = SandboxAuditMiddleware()
    ctx = _ctx("exec_python", other_field="value")
    await mw(ctx, _terminal)
    assert ctx.payload["terminal_called"] == 1


@pytest.mark.asyncio
async def test_non_string_code_safe_pass() -> None:
    mw = SandboxAuditMiddleware()
    ctx = _ctx("exec_python", code=12345)
    await mw(ctx, _terminal)
    assert ctx.payload["terminal_called"] == 1


@pytest.mark.asyncio
async def test_custom_sandbox_tool_names() -> None:
    mw = SandboxAuditMiddleware(sandbox_tool_names=frozenset({"my_repl"}))
    # Default 'exec_python' no longer audited.
    ctx_skip = _ctx("exec_python", code="os.system('x')")
    await mw(ctx_skip, _terminal)
    assert ctx_skip.payload["terminal_called"] == 1
    # The custom one is audited — but with non-sandbox name semantics
    # only the python AST runs if the tool_name == "exec_python".
    # We just verify the filter applies cleanly.
    ctx_custom = _ctx("my_repl", code="os.system('x')")
    await mw(ctx_custom, _terminal)
    # 'my_repl' is in sandbox_tool_names but doesn't match the exec_python
    # branch, so it passes through without inspection (the audit doesn't
    # know what shape its code lives in).
    assert ctx_custom.payload["terminal_called"] == 1


# ---------------------------------------------------------------------------
# Anchor wiring + Protocol
# ---------------------------------------------------------------------------


def test_registers_before_tool_dispatch_anchor() -> None:
    mw = SandboxAuditMiddleware()
    assert mw.anchor == "before_tool_dispatch"


def test_satisfies_middleware_protocol() -> None:
    assert isinstance(SandboxAuditMiddleware(), Middleware)


def test_blocked_error_truncates_long_snippet() -> None:
    long_code = "os.system('" + "x" * 5000 + "')"
    err = SandboxAuditBlockedError("os.system", long_code)
    assert "os.system" in str(err)
    # Truncated to <= 200 chars + "..." marker; full snippet stored on
    # the attribute for downstream audit.
    assert len(str(err)) < 500
    assert err.snippet == long_code
