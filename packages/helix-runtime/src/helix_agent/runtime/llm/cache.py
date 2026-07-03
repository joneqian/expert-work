"""LLM response cache — Stream E.13.

Exact-match cache for deterministic LLM calls. Keyed by
``sha256(tenant_id ‖ model ‖ normalize(messages) ‖ temperature ‖
max_tokens)`` with a per-tenant key prefix so there is no cross-tenant
hit risk (test matrix #25).

Cacheability (test matrix #26) — a call is cacheable only when:

- ``temperature <= TEMPERATURE_CACHE_CEILING`` (0.1) — above that the
  model is non-deterministic and a cached answer would be wrong to
  replay.
- No message in the prompt carries tool interaction — any
  :class:`ToolMessage`, or any :class:`AIMessage` with ``tool_calls``,
  means the turn depends on external tool state that the cache can't
  reason about, so we bypass.
- The prompt is non-empty.

Backend is any object satisfying :class:`RedisLike` — production wires
``redis.asyncio.Redis`` (structurally compatible); tests use
:class:`InMemoryRedisCache`. helix-runtime deliberately takes **no**
hard dependency on ``redis`` — the Protocol keeps the package backend-
agnostic, mirroring how ``stream_bridge`` defers its Redis backend.

Per [STREAM-E-DESIGN § 2.8 + Mini-ADR E-6](../../../../../../../docs/streams/STREAM-E-DESIGN.md).
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from helix_agent.protocol import StructuredOutputSpec

logger = logging.getLogger(__name__)

#: Calls with ``temperature`` above this are treated as non-deterministic
#: and never cached. 0.1 mirrors STREAM-E-DESIGN § 2.8.
TEMPERATURE_CACHE_CEILING = 0.1

#: Default entry lifetime; manifest ``llm.cache.ttl_s`` overrides per agent.
DEFAULT_TTL_S = 3600

_KEY_PREFIX = "llm:cache"


@runtime_checkable
class RedisLike(Protocol):
    """Minimal async key-value surface the cache depends on.

    ``redis.asyncio.Redis`` satisfies this structurally;
    :class:`InMemoryRedisCache` is the test double. Kept deliberately
    tiny — only the three operations the cache actually performs.
    """

    async def get(self, key: str) -> bytes | None:
        """Return the raw value for ``key``, or ``None`` if absent."""

    async def set(self, key: str, value: bytes, ex: int | None = None) -> Any:
        """Store ``value`` at ``key`` with optional expiry ``ex`` seconds."""


@dataclass
class InMemoryRedisCache:
    """Dict-backed :class:`RedisLike` for dev / tests.

    TTL is accepted but **not enforced** — unit tests assert on hit /
    miss semantics, not on expiry timing. A real Redis handles expiry
    in production.
    """

    store: dict[str, bytes] = field(default_factory=dict)

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    async def set(self, key: str, value: bytes, ex: int | None = None) -> None:
        del ex  # expiry intentionally not simulated
        self.store[key] = value


def is_cacheable(messages: Sequence[BaseMessage], temperature: float) -> bool:
    """Return whether a call with these inputs may be cached.

    See module docstring for the three conditions. Centralised here so
    the lookup and store middlewares apply identical logic — a mismatch
    would store entries that can never be read, or vice versa.
    """
    if temperature > TEMPERATURE_CACHE_CEILING:
        return False
    if not messages:
        return False
    for msg in messages:
        if isinstance(msg, ToolMessage):
            return False
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            return False
    return True


def _normalize_message(msg: BaseMessage) -> dict[str, Any]:
    """Canonical, order-stable representation of one message for hashing.

    Uses the message ``type`` discriminator + text content. ``id`` and
    provider metadata are intentionally excluded — two prompts that
    differ only by message id should hit the same cache entry.
    """
    content = msg.content
    if not isinstance(content, str):
        content = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return {"type": msg.type, "content": content}


@dataclass
class LLMResponseCache:
    """Exact-match LLM response cache over a :class:`RedisLike` backend."""

    redis: RedisLike
    default_ttl_s: int = DEFAULT_TTL_S

    def make_key(
        self,
        *,
        tenant_id: UUID,
        model: str,
        messages: Sequence[BaseMessage],
        temperature: float,
        max_tokens: int,
        output_schema: StructuredOutputSpec | None = None,
    ) -> str:
        """Derive the per-tenant cache key.

        The ``tenant_id`` appears both in the key prefix (namespace
        isolation / easy ``SCAN`` per tenant) **and** inside the hash
        (defence in depth — a prefix-stripping bug still can't cause a
        cross-tenant hit).

        ``output_schema`` (Stream RT-1 PR-2) joins the key as a
        fingerprint — name + strict + content digest, never the schema
        body — so a structured call cannot hit an unstructured entry for
        the same messages (or one made under a different schema, or the
        same schema with a different ``strict``: on the native path
        strict toggles wire-level enforcement, so the responses are not
        interchangeable; folded in during RT-1 PR-3 while structured
        entries had zero production data — no migration cost). ``None``
        adds nothing to the key material, keeping every pre-RT-1 key
        byte-identical: existing cache entries stay reachable.
        """
        key_material: dict[str, Any] = {
            "tenant_id": str(tenant_id),
            "model": model,
            "messages": [_normalize_message(m) for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if output_schema is not None:
            schema_canonical = json.dumps(
                output_schema.schema, sort_keys=True, separators=(",", ":")
            )
            key_material["output_schema"] = {
                "name": output_schema.name,
                "strict": output_schema.strict,
                "schema_sha256": hashlib.sha256(schema_canonical.encode("utf-8")).hexdigest(),
            }
        canonical = json.dumps(key_material, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"{_KEY_PREFIX}:{tenant_id}:{digest}"

    async def get(self, key: str) -> AIMessage | None:
        """Return the cached :class:`AIMessage` for ``key``, or ``None``.

        A corrupt / undecodable entry is treated as a miss (logged) —
        a cache must never be able to crash the call path.
        """
        raw = await self.redis.get(key)
        if raw is None:
            return None
        try:
            data = json.loads(raw)
            return _deserialize_response(data)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.warning("llm_cache.corrupt_entry key=%s err=%s", key, exc)
            return None

    async def put(
        self,
        key: str,
        response: AIMessage,
        ttl_s: int | None = None,
    ) -> None:
        """Store ``response`` under ``key`` with ``ttl_s`` (or the default)."""
        payload = json.dumps(_serialize_response(response)).encode("utf-8")
        await self.redis.set(key, payload, ex=ttl_s or self.default_ttl_s)


def _serialize_response(response: AIMessage) -> dict[str, Any]:
    return {
        "content": response.content,
        "tool_calls": list(getattr(response, "tool_calls", None) or []),
        "id": response.id,
    }


def _deserialize_response(data: Any) -> AIMessage:
    if not isinstance(data, dict):
        raise ValueError(f"expected cache object, got {type(data).__name__}")
    msg = AIMessage(
        content=data.get("content", ""),
        tool_calls=list(data.get("tool_calls") or []),
    )
    cached_id = data.get("id")
    if isinstance(cached_id, str):
        msg.id = cached_id
    return msg
