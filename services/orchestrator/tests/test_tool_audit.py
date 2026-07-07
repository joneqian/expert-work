"""Unit tests for per-tool-call audit emit (Stream TE-2).

Covers ``_dispatch_tool``'s TOOL_CALL / TOOL_BLOCKED emit, the privacy
rule (arg names + declared paths only — never raw values), the
best-effort contract (no logger / no tenant / write failure never break
dispatch), and the ``audit_logger_from_config`` config lift.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import pytest

from orchestrator import (
    ToolContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)
from orchestrator.graph_builder._config import AUDIT_LOGGER_KEY, audit_logger_from_config
from orchestrator.graph_builder.builder import _dispatch_tool

pytestmark = pytest.mark.asyncio


class _RecordingAuditLogger:
    """Captures raw ``AuditEntry`` objects (pre-redaction). Duck-types the
    slice of ``AuditLogger`` that ``_dispatch_tool`` uses (``write``)."""

    def __init__(self) -> None:
        self.entries: list[Any] = []

    async def write(self, entry: Any) -> None:
        self.entries.append(entry)


class _RaisingAuditLogger:
    async def write(self, entry: Any) -> None:
        raise RuntimeError("audit sink down")


class _EchoTool:
    """Returns its args as content; optional path_args / side_effect."""

    def __init__(self, spec: ToolSpec) -> None:
        self.spec = spec

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        del ctx
        return ToolResult(content=f"ok:{sorted(args)}")


class _BoomTool:
    """Raises inside ``call`` — exercises the tool-error (status=error) path."""

    def __init__(self, spec: ToolSpec) -> None:
        self.spec = spec

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        del args, ctx
        raise ValueError("kaboom")


class _BlockingChain:
    """A before_tool_dispatch chain whose invoke raises (middleware block)."""

    async def invoke(self, ctx: Any, terminal: Any) -> None:
        del ctx, terminal
        raise PermissionError("denied by policy")


def _registry(*tools: Any) -> ToolRegistry:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    return reg


def _ctx(*, tenant: bool = True) -> ToolContext:
    return ToolContext(
        tenant_id=uuid4() if tenant else None,
        run_id=uuid4(),
    )


def _call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "id": "call-1", "args": args}


# --- TOOL_CALL success ------------------------------------------------------


async def test_success_emits_tool_call_success() -> None:
    tool = _EchoTool(ToolSpec(name="reader", description="d", is_read_only=True))
    audit = _RecordingAuditLogger()
    ctx = _ctx()
    await _dispatch_tool(
        _call("reader", {"q": "hi"}),
        _registry(tool),
        ctx,
        before_tool_dispatch_chain=None,
        audit_logger=audit,
    )
    assert len(audit.entries) == 1
    entry = audit.entries[0]
    assert entry.action.value == "tool:call"
    assert entry.result.value == "success"
    assert entry.resource_type == "tool"
    assert entry.resource_id == "reader"
    assert entry.tenant_id == ctx.tenant_id
    assert entry.actor_type == "agent"
    assert entry.reason is None
    assert entry.details["arg_keys"] == ["q"]
    assert "duration_ms" in entry.details


async def test_tool_error_emits_result_error() -> None:
    tool = _BoomTool(ToolSpec(name="boom", description="d"))
    audit = _RecordingAuditLogger()
    await _dispatch_tool(
        _call("boom", {}),
        _registry(tool),
        _ctx(),
        before_tool_dispatch_chain=None,
        audit_logger=audit,
    )
    assert len(audit.entries) == 1
    entry = audit.entries[0]
    assert entry.action.value == "tool:call"
    assert entry.result.value == "error"
    assert entry.reason == "tool_error"


# --- TOOL_BLOCKED + unknown -------------------------------------------------


async def test_middleware_block_emits_tool_blocked_denied() -> None:
    tool = _EchoTool(ToolSpec(name="reader", description="d"))
    audit = _RecordingAuditLogger()
    msg, _, _, _ = await _dispatch_tool(
        _call("reader", {}),
        _registry(tool),
        _ctx(),
        before_tool_dispatch_chain=_BlockingChain(),
        audit_logger=audit,
    )
    assert msg.status == "error"  # dispatch result unchanged — block is wrapped
    assert len(audit.entries) == 1
    entry = audit.entries[0]
    assert entry.action.value == "tool:blocked"
    assert entry.result.value == "denied"
    assert entry.reason == "PermissionError"


async def test_unknown_tool_emits_tool_call_error() -> None:
    audit = _RecordingAuditLogger()
    await _dispatch_tool(
        _call("ghost", {}),
        _registry(),  # empty
        _ctx(),
        before_tool_dispatch_chain=None,
        audit_logger=audit,
    )
    assert len(audit.entries) == 1
    entry = audit.entries[0]
    assert entry.action.value == "tool:call"
    assert entry.result.value == "error"
    assert entry.reason == "unknown_tool"


# --- privacy: no raw arg values, only keys + declared paths -----------------


async def test_details_record_keys_and_paths_never_raw_values() -> None:
    tool = _EchoTool(ToolSpec(name="writer", description="d", path_args=("path",)))
    audit = _RecordingAuditLogger()
    await _dispatch_tool(
        _call("writer", {"path": "/ws/main.py", "password": "s3cr3t-value", "n": 5}),
        _registry(tool),
        _ctx(),
        before_tool_dispatch_chain=None,
        audit_logger=audit,
    )
    entry = audit.entries[0]
    # arg names are recorded (incl. "password" the *key*) ...
    assert entry.details["arg_keys"] == ["n", "password", "path"]
    # ... declared path value is recorded (a filesystem path, not a secret) ...
    assert entry.details["paths"] == ["/ws/main.py"]
    # ... but no raw argument VALUE leaks anywhere in details.
    blob = repr(entry.details)
    assert "s3cr3t-value" not in blob
    assert "5" not in entry.details.get("arg_keys", [])  # the value 5 not present


# --- best-effort: never breaks dispatch -------------------------------------


async def test_no_audit_logger_does_not_crash() -> None:
    tool = _EchoTool(ToolSpec(name="reader", description="d"))
    msg, _, _, _ = await _dispatch_tool(
        _call("reader", {}),
        _registry(tool),
        _ctx(),
        before_tool_dispatch_chain=None,
        audit_logger=None,
    )
    assert msg.status != "error"


async def test_missing_tenant_id_skips_emit() -> None:
    tool = _EchoTool(ToolSpec(name="reader", description="d"))
    audit = _RecordingAuditLogger()
    await _dispatch_tool(
        _call("reader", {}),
        _registry(tool),
        _ctx(tenant=False),  # tenant_id None
        before_tool_dispatch_chain=None,
        audit_logger=audit,
    )
    assert audit.entries == []


async def test_audit_write_failure_is_swallowed() -> None:
    tool = _EchoTool(ToolSpec(name="reader", description="d"))
    msg, _, _, _ = await _dispatch_tool(
        _call("reader", {}),
        _registry(tool),
        _ctx(),
        before_tool_dispatch_chain=None,
        audit_logger=_RaisingAuditLogger(),  # write raises
    )
    # dispatch still returns a normal (non-error) result
    assert msg.status != "error"


async def test_audit_details_build_failure_does_not_reclassify_success() -> None:
    # An arg value whose str() raises is exercised during the details build,
    # which on the success path runs inside _dispatch_tool's try whose except
    # is the middleware-block handler. _emit_tool_audit must swallow it so a
    # successful dispatch is NOT mislabeled as a block / turned into an error.
    class _Unstringable:
        def __str__(self) -> str:
            raise RuntimeError("cannot stringify")

    tool = _EchoTool(ToolSpec(name="writer", description="d", path_args=("path",)))
    audit = _RecordingAuditLogger()
    msg, _, _, _ = await _dispatch_tool(
        _call("writer", {"path": _Unstringable()}),
        _registry(tool),
        _ctx(),
        before_tool_dispatch_chain=None,
        audit_logger=audit,
    )
    assert msg.status != "error"  # success preserved, not reclassified as block
    assert audit.entries == []  # emit swallowed the build error


# --- audit_logger_from_config -----------------------------------------------


async def test_audit_logger_from_config_returns_none_when_absent() -> None:
    assert audit_logger_from_config({}) is None
    assert audit_logger_from_config({"configurable": {}}) is None
    # wrong type under the key → None (isinstance guard)
    assert audit_logger_from_config({"configurable": {AUDIT_LOGGER_KEY: object()}}) is None


async def test_audit_logger_from_config_returns_real_logger() -> None:
    from expert_work.persistence.audit_log import InMemoryAuditLogStore
    from expert_work.runtime.audit.fallback import InMemoryAuditFallbackQueue
    from expert_work.runtime.audit.logger import AuditLogger
    from expert_work.runtime.audit.redactor import DefaultSecretRedactor

    real = AuditLogger(
        InMemoryAuditLogStore(),
        DefaultSecretRedactor(),
        InMemoryAuditFallbackQueue(),
    )
    config = {"configurable": {AUDIT_LOGGER_KEY: real}}
    assert audit_logger_from_config(config) is real


# --- Stream 14.4 — MCP traffic audit ----------------------------------------

from orchestrator.graph_builder.builder import _mcp_audit_details  # noqa: E402


def test_mcp_audit_details_parses_server_and_volume() -> None:
    d = _mcp_audit_details("mcp:github.search_issues", content="hello world")
    assert d == {
        "mcp_server": "github",
        "mcp_tool": "search_issues",
        "mcp_is_error": False,
        "response_chars": len("hello world"),
    }


def test_mcp_audit_details_none_for_non_mcp_tool() -> None:
    assert _mcp_audit_details("reader", content="x") is None


def test_mcp_audit_details_omits_volume_without_content() -> None:
    d = _mcp_audit_details("mcp:fs.read", is_error=True)
    assert d == {"mcp_server": "fs", "mcp_tool": "read", "mcp_is_error": True}
    assert "response_chars" not in d


async def test_mcp_tool_call_audit_carries_traffic_dimensions() -> None:
    tool = _EchoTool(ToolSpec(name="mcp:github.search", description="d"))
    audit = _RecordingAuditLogger()
    await _dispatch_tool(
        _call("mcp:github.search", {"q": "bug"}),
        _registry(tool),
        _ctx(),
        before_tool_dispatch_chain=None,
        audit_logger=audit,
    )
    entry = audit.entries[0]
    assert entry.action.value == "tool:call"
    assert entry.resource_id == "mcp:github.search"
    # MCP traffic dimensions present + structured.
    assert entry.details["mcp_server"] == "github"
    assert entry.details["mcp_tool"] == "search"
    assert entry.details["mcp_is_error"] is False
    assert entry.details["response_chars"] > 0
    # Privacy — the response CONTENT itself is never recorded, only its length.
    assert "ok:" not in str(entry.details)


async def test_non_mcp_tool_audit_has_no_mcp_fields() -> None:
    tool = _EchoTool(ToolSpec(name="reader", description="d"))
    audit = _RecordingAuditLogger()
    await _dispatch_tool(
        _call("reader", {"q": "hi"}),
        _registry(tool),
        _ctx(),
        before_tool_dispatch_chain=None,
        audit_logger=audit,
    )
    assert "mcp_server" not in audit.entries[0].details


# --- sandbox executor code trace (audit over blocking) ----------------------


async def test_exec_python_audit_records_code_and_hash() -> None:
    import hashlib

    code = "import subprocess\nsubprocess.run(['soffice', '--headless'])\n"
    tool = _EchoTool(ToolSpec(name="exec_python", description="d"))
    audit = _RecordingAuditLogger()
    await _dispatch_tool(
        _call("exec_python", {"code": code}),
        _registry(tool),
        _ctx(),
        before_tool_dispatch_chain=None,
        audit_logger=audit,
    )
    details = audit.entries[0].details
    # The removed denylist would have blocked subprocess.run; instead the code
    # is recorded verbatim (under the preview cap) + a full-content sha256.
    assert details["code"] == code
    assert details["code_sha256"] == hashlib.sha256(code.encode()).hexdigest()
    assert details["code_bytes"] == len(code.encode())


async def test_bash_audit_records_command() -> None:
    tool = _EchoTool(ToolSpec(name="bash", description="d"))
    audit = _RecordingAuditLogger()
    await _dispatch_tool(
        _call("bash", {"command": "soffice --headless --convert-to pdf x.pptx"}),
        _registry(tool),
        _ctx(),
        before_tool_dispatch_chain=None,
        audit_logger=audit,
    )
    details = audit.entries[0].details
    assert details["code"] == "soffice --headless --convert-to pdf x.pptx"
    assert "code_sha256" in details


async def test_sandbox_code_preview_is_capped() -> None:
    big = "x" * 9000
    tool = _EchoTool(ToolSpec(name="exec_python", description="d"))
    audit = _RecordingAuditLogger()
    await _dispatch_tool(
        _call("exec_python", {"code": big}),
        _registry(tool),
        _ctx(),
        before_tool_dispatch_chain=None,
        audit_logger=audit,
    )
    details = audit.entries[0].details
    assert details["code"].endswith("…(truncated)")
    assert len(details["code"]) < len(big)  # preview capped
    assert details["code_bytes"] == 9000  # full size still recorded


async def test_non_sandbox_tool_has_no_code_field() -> None:
    tool = _EchoTool(ToolSpec(name="reader", description="d"))
    audit = _RecordingAuditLogger()
    await _dispatch_tool(
        _call("reader", {"code": "not a sandbox tool"}),
        _registry(tool),
        _ctx(),
        before_tool_dispatch_chain=None,
        audit_logger=audit,
    )
    details = audit.entries[0].details
    assert "code" not in details
    assert "code_sha256" not in details
