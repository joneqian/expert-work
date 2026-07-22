from __future__ import annotations

from uuid import uuid4

from orchestrator.graph_builder.builder import _build_tool_context


def test_lifts_thread_id() -> None:
    thread_id = uuid4()
    ctx = _build_tool_context(
        {"configurable": {"thread_id": str(thread_id), "tenant_id": str(uuid4())}}
    )
    assert ctx.thread_id == thread_id


def test_trigger_origin_defaults_false() -> None:
    ctx = _build_tool_context({"configurable": {"thread_id": str(uuid4())}})
    assert ctx.trigger_origin is False


def test_trigger_origin_true_when_flagged() -> None:
    ctx = _build_tool_context({"configurable": {"thread_id": str(uuid4()), "trigger_origin": True}})
    assert ctx.trigger_origin is True


def test_missing_thread_id_is_none() -> None:
    ctx = _build_tool_context({"configurable": {"tenant_id": str(uuid4())}})
    assert ctx.thread_id is None
