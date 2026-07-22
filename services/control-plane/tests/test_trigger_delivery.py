"""Tests for :func:`control_plane.trigger_delivery.inject_delivery`."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from control_plane.transcript import read_turns
from control_plane.trigger_delivery import inject_delivery
from expert_work.protocol import AgentSpec
from expert_work.runtime.checkpointer import make_checkpointer
from expert_work.runtime.secret_store import LocalDevSecretStore
from orchestrator.agent_factory import build_agent

# ---------------------------------------------------------------------------
# Minimal graph build — replicated from
# services/orchestrator/tests/test_agent_factory.py (_MINIMAL_SPEC, _spec(),
# _secret_store(), _platform_resolver). build_agent REQUIRES
# provider_key_resolver (Stream Y-2) or it raises.
# ---------------------------------------------------------------------------

_ANTHROPIC_KEY_NAME = "expert-work/dev/llm/anthropic"
_OPENAI_KEY_NAME = "expert-work/dev/llm/openai"
_KIMI_KEY_NAME = "expert-work/dev/llm/kimi"

_MINIMAL_SPEC: dict[str, Any] = {
    "apiVersion": "expert_work.io/v1",
    "kind": "Agent",
    "metadata": {"name": "test-agent", "version": "1.0.0", "tenant": "platform-eng"},
    "spec": {
        "tenant_config": {},
        "model": {
            "provider": "anthropic",
            "name": "claude-sonnet-4-6",
            "api_key_ref": f"secret://{_ANTHROPIC_KEY_NAME}",
        },
        "system_prompt": {"template": "you are a test agent"},
        "sandbox": {
            "resources": {"cpu": "1.0", "memory": "1Gi"},
            "network": {"egress": "proxy", "allowlist": ["api.anthropic.com"]},
            "filesystem": {"readonly_root": True, "writable": ["/workspace"]},
        },
    },
}


def _spec(**model_overrides: Any) -> AgentSpec:
    doc = deepcopy(_MINIMAL_SPEC)
    doc["spec"]["model"].update(model_overrides)
    return AgentSpec.model_validate(doc)


def _secret_store() -> LocalDevSecretStore:
    return LocalDevSecretStore.from_mapping(
        {
            _ANTHROPIC_KEY_NAME: "sk-ant-test",
            _OPENAI_KEY_NAME: "sk-openai-test",
            _KIMI_KEY_NAME: "sk-kimi-test",
        }
    )


_PROVIDER_KEY_NAMES = {
    "anthropic": _ANTHROPIC_KEY_NAME,
    "openai": _OPENAI_KEY_NAME,
    "kimi": _KIMI_KEY_NAME,
    "self-hosted": _OPENAI_KEY_NAME,
    "azure": _OPENAI_KEY_NAME,
    "qwen": _OPENAI_KEY_NAME,
}


async def _platform_resolver(provider: str) -> list[str]:
    return [f"secret://{_PROVIDER_KEY_NAMES[provider]}"]


async def _built_graph(cp: Any) -> Any:
    spec = _spec()  # a single react agent, no tools needed
    built = await build_agent(
        spec,
        secret_store=_secret_store(),
        checkpointer=cp,
        provider_key_resolver=_platform_resolver,  # required — build_agent raises without it
    )
    return built.graph


@pytest.mark.asyncio
async def test_delivery_appends_ai_message_visible_to_reader() -> None:
    tenant, thread = uuid4(), uuid4()
    async with make_checkpointer("memory") as cp:
        graph = await _built_graph(cp)
        config = {"configurable": {"thread_id": str(thread), "tenant_id": str(tenant)}}
        # seed a prior exchange so the thread has history
        await graph.aupdate_state(
            config,
            {
                "messages": [
                    HumanMessage(content="set up my task"),
                    AIMessage(content="done, it's scheduled"),
                ]
            },
            as_node="agent",
        )
        await inject_delivery(
            graph,
            thread_id=thread,
            tenant_id=tenant,
            result_text="Today's AI news: ...",
            source_run_id=uuid4(),
            trigger_id=uuid4(),
        )
        # the real user-facing read path surfaces it as the last assistant turn
        turns = await read_turns(cp, thread, include_hidden=False)
        assert turns[-1].role == "assistant"
        assert turns[-1].content == "Today's AI news: ..."
        # prove APPEND (not replace): the seeded history must survive
        assert len(turns) == 3
        assert turns[0].content == "set up my task"
        # graph left in a clean turn-complete state (no pending node → next user
        # turn starts fresh, delivery didn't leave the graph mid-execution)
        snap = await graph.aget_state(config)
        assert snap.next == ()


@pytest.mark.asyncio
async def test_delivery_metadata_tags_source() -> None:
    tenant, thread, run_id, trig = uuid4(), uuid4(), uuid4(), uuid4()
    async with make_checkpointer("memory") as cp:
        graph = await _built_graph(cp)
        config = {"configurable": {"thread_id": str(thread), "tenant_id": str(tenant)}}
        await graph.aupdate_state(
            config,
            {"messages": [HumanMessage(content="hi"), AIMessage(content="ok")]},
            as_node="agent",
        )
        await inject_delivery(
            graph,
            thread_id=thread,
            tenant_id=tenant,
            result_text="result",
            source_run_id=run_id,
            trigger_id=trig,
        )
        snap = await graph.aget_state(config)
        last = snap.values["messages"][-1]
        assert last.type == "ai"
        assert last.additional_kwargs["expert_work_scheduled_delivery"] is True
        assert last.additional_kwargs["expert_work_source_run_id"] == str(run_id)
        assert last.additional_kwargs["expert_work_trigger_id"] == str(trig)
        # NOT hidden from the UI
        assert "expert_work_hide_from_ui" not in last.additional_kwargs


@pytest.mark.asyncio
async def test_delivery_into_empty_thread() -> None:
    """No prior history → as_node='__start__' path still lands the message."""
    tenant, thread = uuid4(), uuid4()
    async with make_checkpointer("memory") as cp:
        graph = await _built_graph(cp)
        await inject_delivery(
            graph,
            thread_id=thread,
            tenant_id=tenant,
            result_text="standalone",
            source_run_id=uuid4(),
            trigger_id=uuid4(),
        )
        turns = await read_turns(cp, thread, include_hidden=False)
        assert any(t.role == "assistant" and t.content == "standalone" for t in turns)


@pytest.mark.asyncio
async def test_inject_delivery_is_idempotent_by_source_run_id() -> None:
    """同一 source_run_id 重复投递只落一条消息(FU1a)——消除 fire-now 端点与
    scheduler reconcile 同进程双投递导致的重复贴。"""
    tenant, thread = uuid4(), uuid4()
    src, trig = uuid4(), uuid4()
    async with make_checkpointer("memory") as cp:
        graph = await _built_graph(cp)
        config = {"configurable": {"thread_id": str(thread), "tenant_id": str(tenant)}}
        await graph.aupdate_state(
            config,
            {
                "messages": [
                    HumanMessage(content="set up my task"),
                    AIMessage(content="done, it's scheduled"),
                ]
            },
            as_node="agent",
        )
        for _ in range(2):
            await inject_delivery(
                graph,
                thread_id=thread,
                tenant_id=tenant,
                result_text="今日 AI 新闻:...",
                source_run_id=src,
                trigger_id=trig,
            )
        turns = await read_turns(cp, thread, include_hidden=True)
        delivered = [t for t in turns if t.content == "今日 AI 新闻:..."]
        assert len(delivered) == 1  # 两次调用,只贴一条
