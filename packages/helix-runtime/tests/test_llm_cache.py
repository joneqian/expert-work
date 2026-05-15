"""Unit tests for the LLM response cache — Stream E.13.

Covers :class:`LLMResponseCache`, :func:`is_cacheable`, and the
:class:`LLMCacheLookupMiddleware` / :class:`LLMCacheStoreMiddleware`
pair. Test matrix #25 (cross-tenant miss) and #26 (high-temperature
bypass) from STREAM-E-DESIGN § 5 are exercised here; #24 (full-graph
cache hit) lives in the orchestrator integration suite.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from helix_agent.runtime.llm import (
    InMemoryRedisCache,
    LLMResponseCache,
    is_cacheable,
)
from helix_agent.runtime.middleware import (
    LLMCacheLookupMiddleware,
    LLMCacheStoreMiddleware,
    MiddlewareContext,
)

_TENANT_A = UUID("11111111-1111-1111-1111-111111111111")
_TENANT_B = UUID("22222222-2222-2222-2222-222222222222")
_MODEL = "claude-sonnet-4-6"


def _cache() -> LLMResponseCache:
    return LLMResponseCache(redis=InMemoryRedisCache())


async def _terminal(_ctx: MiddlewareContext) -> None:
    """No-op chain terminal."""


def _lookup_mw(cache: LLMResponseCache, *, temperature: float = 0.0) -> LLMCacheLookupMiddleware:
    return LLMCacheLookupMiddleware(
        cache=cache, model=_MODEL, temperature=temperature, max_tokens=4096
    )


def _store_mw(cache: LLMResponseCache, *, temperature: float = 0.0) -> LLMCacheStoreMiddleware:
    return LLMCacheStoreMiddleware(
        cache=cache, model=_MODEL, temperature=temperature, max_tokens=4096
    )


# ---------------------------------------------------------------------------
# is_cacheable
# ---------------------------------------------------------------------------


def test_cacheable_plain_prompt_low_temperature() -> None:
    assert is_cacheable([HumanMessage(content="hi")], temperature=0.0) is True
    assert is_cacheable([HumanMessage(content="hi")], temperature=0.1) is True


def test_not_cacheable_high_temperature() -> None:
    """Test matrix #26 — temperature above the ceiling is non-deterministic."""
    assert is_cacheable([HumanMessage(content="hi")], temperature=0.5) is False
    assert is_cacheable([HumanMessage(content="hi")], temperature=0.11) is False


def test_not_cacheable_empty_messages() -> None:
    assert is_cacheable([], temperature=0.0) is False


def test_not_cacheable_with_tool_message() -> None:
    msgs = [HumanMessage(content="hi"), ToolMessage(content="result", tool_call_id="t1")]
    assert is_cacheable(msgs, temperature=0.0) is False


def test_not_cacheable_with_ai_tool_calls() -> None:
    ai = AIMessage(
        content="",
        tool_calls=[{"id": "t1", "name": "search", "args": {}, "type": "tool_call"}],
    )
    assert is_cacheable([HumanMessage(content="hi"), ai], temperature=0.0) is False


# ---------------------------------------------------------------------------
# Key derivation
# ---------------------------------------------------------------------------


def test_make_key_is_deterministic() -> None:
    cache = _cache()
    args = {
        "tenant_id": _TENANT_A,
        "model": _MODEL,
        "messages": [HumanMessage(content="hello")],
        "temperature": 0.0,
        "max_tokens": 4096,
    }
    assert cache.make_key(**args) == cache.make_key(**args)


def test_make_key_differs_by_tenant() -> None:
    """Test matrix #25 root cause — tenant is part of the key."""
    cache = _cache()
    msgs = [HumanMessage(content="hello")]
    key_a = cache.make_key(
        tenant_id=_TENANT_A, model=_MODEL, messages=msgs, temperature=0.0, max_tokens=4096
    )
    key_b = cache.make_key(
        tenant_id=_TENANT_B, model=_MODEL, messages=msgs, temperature=0.0, max_tokens=4096
    )
    assert key_a != key_b
    assert str(_TENANT_A) in key_a
    assert str(_TENANT_B) in key_b


def test_make_key_differs_by_model_and_messages_and_params() -> None:
    cache = _cache()
    base = {
        "tenant_id": _TENANT_A,
        "model": _MODEL,
        "messages": [HumanMessage(content="hello")],
        "temperature": 0.0,
        "max_tokens": 4096,
    }
    key = cache.make_key(**base)
    assert cache.make_key(**{**base, "model": "gpt-4o"}) != key
    assert cache.make_key(**{**base, "messages": [HumanMessage(content="world")]}) != key
    assert cache.make_key(**{**base, "temperature": 0.1}) != key
    assert cache.make_key(**{**base, "max_tokens": 2048}) != key


def test_make_key_ignores_message_id() -> None:
    """Two prompts differing only by message id must hit the same entry."""
    cache = _cache()
    m1 = HumanMessage(content="hello")
    m1.id = "id-1"
    m2 = HumanMessage(content="hello")
    m2.id = "id-2"
    key1 = cache.make_key(
        tenant_id=_TENANT_A, model=_MODEL, messages=[m1], temperature=0.0, max_tokens=4096
    )
    key2 = cache.make_key(
        tenant_id=_TENANT_A, model=_MODEL, messages=[m2], temperature=0.0, max_tokens=4096
    )
    assert key1 == key2


# ---------------------------------------------------------------------------
# get / put round trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_then_get_round_trip() -> None:
    cache = _cache()
    key = "llm:cache:test:abc"
    await cache.put(key, AIMessage(content="cached answer"))
    got = await cache.get(key)
    assert got is not None
    assert got.content == "cached answer"


@pytest.mark.asyncio
async def test_get_miss_returns_none() -> None:
    cache = _cache()
    assert await cache.get("llm:cache:test:missing") is None


@pytest.mark.asyncio
async def test_round_trip_preserves_tool_calls() -> None:
    cache = _cache()
    response = AIMessage(
        content="",
        tool_calls=[{"id": "t1", "name": "search", "args": {"q": "x"}, "type": "tool_call"}],
    )
    await cache.put("llm:cache:test:tc", response)
    got = await cache.get("llm:cache:test:tc")
    assert got is not None
    assert got.tool_calls == [
        {"id": "t1", "name": "search", "args": {"q": "x"}, "type": "tool_call"}
    ]


@pytest.mark.asyncio
async def test_corrupt_entry_treated_as_miss() -> None:
    """A cache must never crash the call path — bad bytes → miss."""
    redis = InMemoryRedisCache()
    redis.store["llm:cache:test:bad"] = b"{not valid json"
    cache = LLMResponseCache(redis=redis)
    assert await cache.get("llm:cache:test:bad") is None


@pytest.mark.asyncio
async def test_put_uses_default_ttl() -> None:
    """``put`` without explicit ttl falls back to the cache default."""
    captured: dict[str, int | None] = {}

    class _RecordingRedis:
        async def get(self, key: str) -> bytes | None:
            del key
            return None

        async def set(self, key: str, value: bytes, ex: int | None = None) -> None:
            del key, value
            captured["ex"] = ex

    cache = LLMResponseCache(redis=_RecordingRedis(), default_ttl_s=1800)
    await cache.put("k", AIMessage(content="x"))
    assert captured["ex"] == 1800
    await cache.put("k", AIMessage(content="x"), ttl_s=99)
    assert captured["ex"] == 99


# ---------------------------------------------------------------------------
# LLMCacheLookupMiddleware
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lookup_miss_leaves_payload_clean() -> None:
    cache = _cache()
    mw = _lookup_mw(cache)
    ctx = MiddlewareContext(
        payload={"messages": [HumanMessage(content="hi")], "tenant_id": _TENANT_A}
    )
    await mw(ctx, _terminal)
    assert "llm_cache_hit" not in ctx.payload


@pytest.mark.asyncio
async def test_lookup_hit_sets_payload() -> None:
    cache = _cache()
    lookup = _lookup_mw(cache)
    messages = [HumanMessage(content="hi")]
    key = cache.make_key(
        tenant_id=_TENANT_A,
        model=_MODEL,
        messages=messages,
        temperature=0.0,
        max_tokens=4096,
    )
    await cache.put(key, AIMessage(content="from-cache"))

    ctx = MiddlewareContext(payload={"messages": messages, "tenant_id": _TENANT_A})
    await lookup(ctx, _terminal)
    hit = ctx.payload.get("llm_cache_hit")
    assert isinstance(hit, AIMessage)
    assert hit.content == "from-cache"


@pytest.mark.asyncio
async def test_lookup_skipped_without_tenant_id() -> None:
    cache = _cache()
    mw = _lookup_mw(cache)
    ctx = MiddlewareContext(payload={"messages": [HumanMessage(content="hi")]})
    await mw(ctx, _terminal)
    assert "llm_cache_hit" not in ctx.payload


@pytest.mark.asyncio
async def test_lookup_skipped_for_high_temperature() -> None:
    """Test matrix #26 at the middleware boundary."""
    cache = _cache()
    lookup = _lookup_mw(cache, temperature=0.7)
    messages = [HumanMessage(content="hi")]
    # Even if an entry somehow exists under a 0.7-temp key, lookup
    # never runs because is_cacheable rejects temperature 0.7.
    ctx = MiddlewareContext(payload={"messages": messages, "tenant_id": _TENANT_A})
    await lookup(ctx, _terminal)
    assert "llm_cache_hit" not in ctx.payload


# ---------------------------------------------------------------------------
# LLMCacheStoreMiddleware
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_persists_fresh_response() -> None:
    cache = _cache()
    store = _store_mw(cache)
    messages = [HumanMessage(content="hi")]
    ctx = MiddlewareContext(
        payload={
            "prompt_messages": messages,
            "response": AIMessage(content="answer"),
            "tenant_id": _TENANT_A,
            "cache_hit": False,
        }
    )
    await store(ctx, _terminal)

    key = cache.make_key(
        tenant_id=_TENANT_A,
        model=_MODEL,
        messages=messages,
        temperature=0.0,
        max_tokens=4096,
    )
    got = await cache.get(key)
    assert got is not None
    assert got.content == "answer"


@pytest.mark.asyncio
async def test_store_skips_when_cache_hit() -> None:
    """A turn served from cache must not be re-stored."""
    redis = InMemoryRedisCache()
    cache = LLMResponseCache(redis=redis)
    store = _store_mw(cache)
    ctx = MiddlewareContext(
        payload={
            "prompt_messages": [HumanMessage(content="hi")],
            "response": AIMessage(content="answer"),
            "tenant_id": _TENANT_A,
            "cache_hit": True,
        }
    )
    await store(ctx, _terminal)
    assert redis.store == {}


@pytest.mark.asyncio
async def test_store_skips_when_not_cacheable() -> None:
    redis = InMemoryRedisCache()
    cache = LLMResponseCache(redis=redis)
    store = _store_mw(cache)
    ctx = MiddlewareContext(
        payload={
            "prompt_messages": [
                HumanMessage(content="hi"),
                ToolMessage(content="r", tool_call_id="t1"),
            ],
            "response": AIMessage(content="answer"),
            "tenant_id": _TENANT_A,
            "cache_hit": False,
        }
    )
    await store(ctx, _terminal)
    assert redis.store == {}


# ---------------------------------------------------------------------------
# Cross-tenant isolation — test matrix #25
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_tenant_no_hit() -> None:
    """Tenant A stores, tenant B reads the same prompt → miss."""
    cache = _cache()
    messages = [HumanMessage(content="shared prompt")]

    store = _store_mw(cache)
    await store(
        MiddlewareContext(
            payload={
                "prompt_messages": messages,
                "response": AIMessage(content="tenant-A answer"),
                "tenant_id": _TENANT_A,
                "cache_hit": False,
            }
        ),
        _terminal,
    )

    lookup = _lookup_mw(cache)
    ctx_b = MiddlewareContext(payload={"messages": messages, "tenant_id": _TENANT_B})
    await lookup(ctx_b, _terminal)
    assert "llm_cache_hit" not in ctx_b.payload

    # ...and tenant A *does* hit.
    ctx_a = MiddlewareContext(payload={"messages": messages, "tenant_id": _TENANT_A})
    await lookup(ctx_a, _terminal)
    assert isinstance(ctx_a.payload.get("llm_cache_hit"), AIMessage)


@pytest.mark.asyncio
async def test_store_then_lookup_round_trip() -> None:
    """End-to-end at the middleware layer: store-mw writes, lookup-mw
    reads back the identical response."""
    cache = _cache()
    messages = [HumanMessage(content="round trip")]
    tenant = uuid4()

    await _store_mw(cache)(
        MiddlewareContext(
            payload={
                "prompt_messages": messages,
                "response": AIMessage(content="the answer"),
                "tenant_id": tenant,
                "cache_hit": False,
            }
        ),
        _terminal,
    )

    ctx = MiddlewareContext(payload={"messages": messages, "tenant_id": tenant})
    await _lookup_mw(cache)(ctx, _terminal)
    hit = ctx.payload.get("llm_cache_hit")
    assert isinstance(hit, AIMessage)
    assert hit.content == "the answer"


# ---------------------------------------------------------------------------
# Protocol contract
# ---------------------------------------------------------------------------


def test_middlewares_satisfy_protocol() -> None:
    from helix_agent.runtime.middleware import Middleware

    cache = _cache()
    assert isinstance(_lookup_mw(cache), Middleware)
    assert isinstance(_store_mw(cache), Middleware)


def test_lookup_anchor_is_before_llm_call() -> None:
    assert _lookup_mw(_cache()).anchor == "before_llm_call"


def test_store_anchor_is_after_llm_call() -> None:
    assert _store_mw(_cache()).anchor == "after_llm_call"
