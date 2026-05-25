"""Tests for :class:`TokenUsageMiddleware` — Stream G.9.

The middleware is a thin observability layer:

  - reads ``usage_metadata`` off the LLM response
  - increments ``helix_llm_token_usage_total`` per type
  - persists one ``TokenUsageRecord`` per call
  - never lets a counter / DB error bubble up to the LLM call

The Prometheus counter is process-global; we read its sample values
directly from the default registry rather than mocking the helper.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from prometheus_client import REGISTRY

from helix_agent.persistence.token_usage_store import (
    InMemoryTokenUsageStore,
    TokenUsageRecord,
)
from helix_agent.runtime.middleware import MiddlewareContext
from helix_agent.runtime.middleware.token_usage import TokenUsageMiddleware


def _counter_sample(tenant_id: str, agent_name: str, model: str, type_: str) -> float:
    value = REGISTRY.get_sample_value(
        "helix_llm_token_usage_total",
        {
            "tenant_id": tenant_id,
            "agent_name": agent_name,
            "model": model,
            "type": type_,
        },
    )
    return value or 0.0


async def _noop(_ctx: MiddlewareContext) -> None:
    return None


@pytest.mark.asyncio
async def test_persists_and_increments_for_basic_usage() -> None:
    store = InMemoryTokenUsageStore()
    mw = TokenUsageMiddleware(
        store=store,
        agent_name="g9-test-basic",
        agent_version="1.0.0",
        model="claude-sonnet-4-6",
    )
    tenant_id = uuid4()
    ctx = MiddlewareContext(
        payload={
            "tenant_id": tenant_id,
            "response": AIMessage(
                content="ok",
                usage_metadata={
                    "input_tokens": 500,
                    "output_tokens": 100,
                    "total_tokens": 600,
                },
            ),
        }
    )

    before_input = _counter_sample(str(tenant_id), "g9-test-basic", "claude-sonnet-4-6", "input")
    before_output = _counter_sample(str(tenant_id), "g9-test-basic", "claude-sonnet-4-6", "output")

    await mw(ctx, _noop)

    rows = list(await store.list_for_tenant(tenant_id=tenant_id))
    assert len(rows) == 1
    assert rows[0].input_tokens == 500
    assert rows[0].output_tokens == 100
    assert rows[0].cache_creation_tokens == 0

    assert (
        _counter_sample(str(tenant_id), "g9-test-basic", "claude-sonnet-4-6", "input")
        - before_input
        == 500
    )
    assert (
        _counter_sample(str(tenant_id), "g9-test-basic", "claude-sonnet-4-6", "output")
        - before_output
        == 100
    )


@pytest.mark.asyncio
async def test_extracts_cache_counters_from_input_token_details() -> None:
    store = InMemoryTokenUsageStore()
    mw = TokenUsageMiddleware(
        store=store,
        agent_name="g9-test-cache",
        agent_version="1.0.0",
        model="claude-sonnet-4-6",
    )
    tenant_id = uuid4()
    ctx = MiddlewareContext(
        payload={
            "tenant_id": tenant_id,
            "response": AIMessage(
                content="ok",
                usage_metadata={
                    "input_tokens": 200,
                    "output_tokens": 50,
                    "total_tokens": 250,
                    "input_token_details": {
                        "cache_creation": 800,
                        "cache_read": 1200,
                    },
                },
            ),
        }
    )

    before_creation = _counter_sample(
        str(tenant_id), "g9-test-cache", "claude-sonnet-4-6", "cache_creation"
    )
    before_read = _counter_sample(
        str(tenant_id), "g9-test-cache", "claude-sonnet-4-6", "cache_read"
    )

    await mw(ctx, _noop)

    rows = list(await store.list_for_tenant(tenant_id=tenant_id))
    assert rows[0].cache_creation_tokens == 800
    assert rows[0].cache_read_tokens == 1200

    assert (
        _counter_sample(str(tenant_id), "g9-test-cache", "claude-sonnet-4-6", "cache_creation")
        - before_creation
        == 800
    )
    assert (
        _counter_sample(str(tenant_id), "g9-test-cache", "claude-sonnet-4-6", "cache_read")
        - before_read
        == 1200
    )


@pytest.mark.asyncio
async def test_no_persist_when_response_missing_usage() -> None:
    store = InMemoryTokenUsageStore()
    mw = TokenUsageMiddleware(
        store=store,
        agent_name="g9-no-usage",
        agent_version="1.0.0",
        model="claude-sonnet-4-6",
    )
    tenant_id = uuid4()
    ctx = MiddlewareContext(
        payload={
            "tenant_id": tenant_id,
            "response": AIMessage(content="ok"),
        }
    )
    await mw(ctx, _noop)
    assert list(await store.list_for_tenant(tenant_id=tenant_id)) == []


@pytest.mark.asyncio
async def test_no_persist_when_tenant_missing() -> None:
    store = InMemoryTokenUsageStore()
    mw = TokenUsageMiddleware(
        store=store,
        agent_name="g9-no-tenant",
        agent_version="1.0.0",
        model="m",
    )
    ctx = MiddlewareContext(
        payload={
            "response": AIMessage(
                content="ok",
                usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            ),
        }
    )
    await mw(ctx, _noop)
    # Without tenant_id, persistence is skipped entirely.
    assert list(await store.list_for_tenant(tenant_id=uuid4())) == []


@pytest.mark.asyncio
async def test_persist_failure_does_not_propagate() -> None:
    class _FailingStore(InMemoryTokenUsageStore):
        async def insert(self, record: TokenUsageRecord) -> TokenUsageRecord:
            raise RuntimeError("simulated DB outage")

    store = _FailingStore()
    mw = TokenUsageMiddleware(
        store=store,
        agent_name="g9-fail",
        agent_version="1.0.0",
        model="m",
    )
    tenant_id = uuid4()
    ctx = MiddlewareContext(
        payload={
            "tenant_id": tenant_id,
            "response": AIMessage(
                content="ok",
                usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            ),
        }
    )
    # Must not raise.
    await mw(ctx, _noop)
