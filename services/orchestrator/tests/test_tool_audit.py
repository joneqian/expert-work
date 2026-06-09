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
    from helix_agent.persistence.audit_log import InMemoryAuditLogStore
    from helix_agent.runtime.audit.fallback import InMemoryAuditFallbackQueue
    from helix_agent.runtime.audit.logger import AuditLogger
    from helix_agent.runtime.audit.redactor import DefaultSecretRedactor

    real = AuditLogger(
        InMemoryAuditLogStore(),
        DefaultSecretRedactor(),
        InMemoryAuditFallbackQueue(),
    )
    config = {"configurable": {AUDIT_LOGGER_KEY: real}}
    assert audit_logger_from_config(config) is real
