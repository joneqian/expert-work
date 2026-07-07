"""Unit tests for the Credential Proxy — Stream F.5 (test matrix #51/#52/#53).

All DB / upstream dependencies are faked, so these run in the plain
``pytest`` job. Groups:

* #51 — allowlist enforcement + secret injection
* #52 — the in-process LRU cache
* #53 — audit rows never carry a secret value

Plus aiohttp-route smoke tests over an injected proxy.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from aiohttp.test_utils import TestClient, TestServer

from credential_proxy.app import create_app
from credential_proxy.cache import SecretCache
from credential_proxy.domain import (
    AllowlistDeniedError,
    AllowlistKey,
    ForwardRequest,
    ForwardResult,
    ProxyAuditEntry,
    SecretMissingError,
)
from credential_proxy.proxy import CredentialProxy
from credential_proxy.settings import CredentialProxySettings
from expert_work.runtime.secret_store import SecretNotFoundError

_SECRET_REF = "anthropic/api-key"
_SECRET_VALUE = "sk-super-secret-value"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeSecretStore:
    """A counting :class:`SecretStore` — no real backend."""

    def __init__(self, secrets: dict[str, str]) -> None:
        self._secrets = dict(secrets)
        self.get_calls = 0

    async def get(self, name: str, *, version: str | None = None) -> str:
        self.get_calls += 1
        if name not in self._secrets:
            raise SecretNotFoundError(name)
        return self._secrets[name]

    async def put(self, name: str, value: str) -> None:
        self._secrets[name] = value

    async def list_versions(self, name: str) -> list[str]:
        return ["v1"]


class InMemoryAllowlistStore:
    """An :class:`AllowlistStore` backed by a set of :class:`AllowlistKey`."""

    def __init__(self) -> None:
        self.keys: set[AllowlistKey] = set()

    async def is_allowed(self, key: AllowlistKey) -> bool:
        return key in self.keys

    async def add(self, key: AllowlistKey) -> None:
        self.keys.add(key)

    async def remove_agent_version(
        self, tenant_id: UUID, agent_name: str, agent_version: str
    ) -> int:
        before = len(self.keys)
        self.keys = {
            k
            for k in self.keys
            if not (
                k.tenant_id == tenant_id
                and k.agent_name == agent_name
                and k.agent_version == agent_version
            )
        }
        return before - len(self.keys)


class RecordingForwarder:
    """A :class:`Forwarder` that records calls and returns a canned response."""

    def __init__(self, result: ForwardResult | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self._result = result or ForwardResult(status=200, headers={}, body=b"upstream-ok")

    async def forward(
        self, *, method: str, url: str, headers: dict[str, str], body: bytes
    ) -> ForwardResult:
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        return self._result


class RecordingProxyAuditStore:
    """A :class:`ProxyAuditStore` collecting entries in memory."""

    def __init__(self) -> None:
        self.entries: list[ProxyAuditEntry] = []

    async def record(self, entry: ProxyAuditEntry) -> None:
        self.entries.append(entry)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _build(
    *,
    secrets: dict[str, str] | None = None,
) -> tuple[
    CredentialProxy,
    InMemoryAllowlistStore,
    FakeSecretStore,
    RecordingForwarder,
    RecordingProxyAuditStore,
    SecretCache,
]:
    allowlist = InMemoryAllowlistStore()
    store = FakeSecretStore({_SECRET_REF: _SECRET_VALUE} if secrets is None else secrets)
    forwarder = RecordingForwarder()
    audit = RecordingProxyAuditStore()
    cache = SecretCache(max_size=100, ttl_s=60.0)
    proxy = CredentialProxy(
        allowlist=allowlist,
        secret_store=store,
        cache=cache,
        audit=audit,
        forwarder=forwarder,
    )
    return proxy, allowlist, store, forwarder, audit, cache


def _request(tenant: UUID, *, secret_ref: str = _SECRET_REF) -> ForwardRequest:
    return ForwardRequest(
        tenant_id=tenant,
        agent_name="code-reviewer",
        agent_version="1.0.0",
        secret_ref=secret_ref,
        upstream_url="https://api.anthropic.com/v1/messages",
        method="POST",
        headers={"X-Expert-Work-Secret-Ref": secret_ref, "Content-Type": "application/json"},
        body=b'{"q": 1}',
    )


def _allow(allowlist: InMemoryAllowlistStore, tenant: UUID, secret_ref: str = _SECRET_REF) -> None:
    allowlist.keys.add(
        AllowlistKey(
            tenant_id=tenant,
            agent_name="code-reviewer",
            agent_version="1.0.0",
            secret_ref=secret_ref,
        )
    )


# ---------------------------------------------------------------------------
# #51 — allowlist + injection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forward_injects_secret_for_allowlisted_ref() -> None:
    tenant = uuid4()
    proxy, allowlist, _store, forwarder, _audit, _cache = _build()
    _allow(allowlist, tenant)

    result = await proxy.forward(_request(tenant))

    assert result.status == 200
    sent = forwarder.calls[0]["headers"]
    assert sent["Authorization"] == f"Bearer {_SECRET_VALUE}"  # type: ignore[index]


@pytest.mark.asyncio
async def test_forward_strips_control_headers_before_upstream() -> None:
    tenant = uuid4()
    proxy, allowlist, _store, forwarder, _audit, _cache = _build()
    _allow(allowlist, tenant)

    await proxy.forward(_request(tenant))

    sent: dict[str, str] = forwarder.calls[0]["headers"]  # type: ignore[assignment]
    # The X-Expert-Work-* routing headers must never reach the real upstream.
    assert not any(k.lower().startswith("x-expert-work-") for k in sent)


@pytest.mark.asyncio
async def test_forward_denied_for_unlisted_ref() -> None:
    tenant = uuid4()
    proxy, _allowlist, _store, forwarder, _audit, _cache = _build()  # allowlist empty

    with pytest.raises(AllowlistDeniedError):
        await proxy.forward(_request(tenant))
    # A denied request is never forwarded upstream.
    assert forwarder.calls == []


@pytest.mark.asyncio
async def test_forward_secret_miss_raises() -> None:
    tenant = uuid4()
    proxy, allowlist, _store, _forwarder, _audit, _cache = _build(secrets={})
    _allow(allowlist, tenant)

    with pytest.raises(SecretMissingError):
        await proxy.forward(_request(tenant))


# ---------------------------------------------------------------------------
# #52 — LRU cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_forward_serves_secret_from_cache() -> None:
    tenant = uuid4()
    proxy, allowlist, store, _forwarder, _audit, _cache = _build()
    _allow(allowlist, tenant)

    await proxy.forward(_request(tenant))
    await proxy.forward(_request(tenant))
    # Second call hit the cache — the SecretStore was read only once.
    assert store.get_calls == 1


@pytest.mark.asyncio
async def test_cached_forward_is_audited_as_cached() -> None:
    tenant = uuid4()
    proxy, allowlist, _store, _forwarder, audit, _cache = _build()
    _allow(allowlist, tenant)

    await proxy.forward(_request(tenant))
    await proxy.forward(_request(tenant))

    assert [e.status for e in audit.entries] == ["ok", "cached"]


def test_cache_expires_after_ttl() -> None:
    now = [0.0]
    cache = SecretCache(max_size=10, ttl_s=60.0, clock=lambda: now[0])
    key = (uuid4(), _SECRET_REF)

    cache.put(key, _SECRET_VALUE)
    now[0] = 59.0
    assert cache.get(key) == _SECRET_VALUE
    now[0] = 61.0
    assert cache.get(key) is None


def test_cache_evicts_least_recently_used() -> None:
    cache = SecretCache(max_size=2, ttl_s=60.0)
    k1, k2, k3 = (uuid4(), "a"), (uuid4(), "b"), (uuid4(), "c")
    cache.put(k1, "1")
    cache.put(k2, "2")
    cache.put(k3, "3")  # evicts k1 (LRU)
    assert cache.get(k1) is None
    assert cache.get(k2) == "2"
    assert cache.get(k3) == "3"


# ---------------------------------------------------------------------------
# #53 — audit never carries the secret value
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_row_never_contains_the_secret_value() -> None:
    tenant = uuid4()
    proxy, allowlist, _store, _forwarder, audit, _cache = _build()
    _allow(allowlist, tenant)

    await proxy.forward(_request(tenant))

    entry = audit.entries[0]
    assert entry.status == "ok"
    assert entry.secret_ref == _SECRET_REF
    # The plaintext secret must appear in no field of the audit row.
    assert _SECRET_VALUE not in repr(entry)


@pytest.mark.asyncio
async def test_denied_request_is_audited_as_denied() -> None:
    tenant = uuid4()
    proxy, _allowlist, _store, _forwarder, audit, _cache = _build()

    with pytest.raises(AllowlistDeniedError):
        await proxy.forward(_request(tenant))

    assert [e.status for e in audit.entries] == ["denied"]


@pytest.mark.asyncio
async def test_secret_miss_is_audited() -> None:
    tenant = uuid4()
    proxy, allowlist, _store, _forwarder, audit, _cache = _build(secrets={})
    _allow(allowlist, tenant)

    with pytest.raises(SecretMissingError):
        await proxy.forward(_request(tenant))

    assert [e.status for e in audit.entries] == ["secret_miss"]


# ---------------------------------------------------------------------------
# aiohttp route smoke tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forward_route_returns_upstream_response() -> None:
    tenant = uuid4()
    proxy, allowlist, _store, _forwarder, _audit, cache = _build()
    _allow(allowlist, tenant)
    app = create_app(CredentialProxySettings(), proxy=proxy, allowlist=allowlist, cache=cache)

    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/forward",
            headers={
                "X-Expert-Work-Tenant": str(tenant),
                "X-Expert-Work-Agent": "code-reviewer",
                "X-Expert-Work-Agent-Version": "1.0.0",
                "X-Expert-Work-Secret-Ref": _SECRET_REF,
                "X-Expert-Work-Upstream": "https://api.anthropic.com/v1/messages",
            },
            data=b"{}",
        )
        assert resp.status == 200
        assert await resp.read() == b"upstream-ok"


@pytest.mark.asyncio
async def test_forward_route_missing_header_is_400() -> None:
    tenant = uuid4()
    proxy, allowlist, _store, _forwarder, _audit, cache = _build()
    app = create_app(CredentialProxySettings(), proxy=proxy, allowlist=allowlist, cache=cache)

    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/forward",
            headers={"X-Expert-Work-Tenant": str(tenant)},  # missing the rest
            data=b"{}",
        )
        assert resp.status == 400


@pytest.mark.asyncio
async def test_forward_route_denied_is_403() -> None:
    tenant = uuid4()
    proxy, allowlist, _store, _forwarder, _audit, cache = _build()  # not allowlisted
    app = create_app(CredentialProxySettings(), proxy=proxy, allowlist=allowlist, cache=cache)

    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/forward",
            headers={
                "X-Expert-Work-Tenant": str(tenant),
                "X-Expert-Work-Agent": "code-reviewer",
                "X-Expert-Work-Agent-Version": "1.0.0",
                "X-Expert-Work-Secret-Ref": _SECRET_REF,
                "X-Expert-Work-Upstream": "https://api.anthropic.com/v1/messages",
            },
            data=b"{}",
        )
        assert resp.status == 403


@pytest.mark.asyncio
async def test_health_route_ok() -> None:
    proxy, allowlist, _store, _forwarder, _audit, cache = _build()
    app = create_app(CredentialProxySettings(), proxy=proxy, allowlist=allowlist, cache=cache)

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/admin/health")
        assert resp.status == 200
        assert (await resp.json())["status"] == "ok"
