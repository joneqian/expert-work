"""Unit tests for :class:`AliyunKmsSecretStore` — Stream F.6 (test matrix #54).

The Aliyun SDK :class:`KmsBackend` is faked, so these exercise the
testable core: cache hits, the static / dynamic TTL policy, expiry,
write-through invalidation, and ``SecretStore`` conformance.
"""

from __future__ import annotations

import pytest

from helix_agent.runtime.secret_store import (
    AliyunKmsSecretStore,
    FetchedSecret,
    SecretNotFoundError,
    SecretStore,
)
from helix_agent.runtime.secret_store.aliyun_kms import SecretKind


class FakeClock:
    """A manually-advanced monotonic clock for deterministic expiry tests."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeKmsBackend:
    """A :class:`KmsBackend` fake — records calls, no real Aliyun SDK."""

    def __init__(self) -> None:
        self.secrets: dict[str, FetchedSecret] = {}
        self.fetch_calls = 0
        self.put_calls: list[tuple[str, str]] = []

    def seed(
        self,
        name: str,
        value: str,
        *,
        kind: SecretKind = "static",
        rotation_ttl_s: int = 3600,
        version: str = "v1",
    ) -> None:
        self.secrets[name] = FetchedSecret(
            value=value, version=version, kind=kind, rotation_ttl_s=rotation_ttl_s
        )

    async def fetch_secret(self, name: str, version: str | None) -> FetchedSecret:
        self.fetch_calls += 1
        if name not in self.secrets:
            raise SecretNotFoundError(name)
        return self.secrets[name]

    async def put_secret(self, name: str, value: str) -> None:
        self.put_calls.append((name, value))

    async def list_versions(self, name: str) -> list[str]:
        if name not in self.secrets:
            raise SecretNotFoundError(name)
        return [self.secrets[name].version]


# ---------- basic get ----------


@pytest.mark.asyncio
async def test_get_returns_secret_value() -> None:
    backend = FakeKmsBackend()
    backend.seed("anthropic/api-key", "sk-secret")
    store = AliyunKmsSecretStore(backend)

    assert await store.get("anthropic/api-key") == "sk-secret"


@pytest.mark.asyncio
async def test_get_missing_secret_raises() -> None:
    store = AliyunKmsSecretStore(FakeKmsBackend())
    with pytest.raises(SecretNotFoundError):
        await store.get("nope")


# ---------- caching ----------


@pytest.mark.asyncio
async def test_get_serves_second_read_from_cache() -> None:
    backend = FakeKmsBackend()
    backend.seed("k", "v")
    store = AliyunKmsSecretStore(backend, clock=FakeClock())

    await store.get("k")
    await store.get("k")
    assert backend.fetch_calls == 1


@pytest.mark.asyncio
async def test_static_secret_cached_for_at_most_60s() -> None:
    backend = FakeKmsBackend()
    backend.seed("k", "v", kind="static", rotation_ttl_s=3600)
    clock = FakeClock()
    store = AliyunKmsSecretStore(backend, clock=clock)

    await store.get("k")
    clock.advance(59)
    await store.get("k")
    assert backend.fetch_calls == 1  # still within the 60s cap

    clock.advance(2)  # now past 60s
    await store.get("k")
    assert backend.fetch_calls == 2


@pytest.mark.asyncio
async def test_static_ttl_never_exceeds_secrets_own_rotation() -> None:
    # A static secret rotating faster than 60s is cached only that long.
    backend = FakeKmsBackend()
    backend.seed("k", "v", kind="static", rotation_ttl_s=30)
    clock = FakeClock()
    store = AliyunKmsSecretStore(backend, clock=clock)

    await store.get("k")
    clock.advance(31)
    await store.get("k")
    assert backend.fetch_calls == 2


@pytest.mark.asyncio
async def test_dynamic_secret_cached_for_half_its_ttl() -> None:
    backend = FakeKmsBackend()
    backend.seed("db/cred", "pw", kind="dynamic", rotation_ttl_s=600)
    clock = FakeClock()
    store = AliyunKmsSecretStore(backend, clock=clock)

    await store.get("db/cred")
    clock.advance(299)
    await store.get("db/cred")
    assert backend.fetch_calls == 1  # within 300s (= 600 / 2)

    clock.advance(2)  # past 300s
    await store.get("db/cred")
    assert backend.fetch_calls == 2


@pytest.mark.asyncio
async def test_version_specific_get_cached_under_its_own_key() -> None:
    backend = FakeKmsBackend()
    backend.seed("k", "v")
    store = AliyunKmsSecretStore(backend, clock=FakeClock())

    await store.get("k")
    await store.get("k", version="v2")
    # Distinct cache keys → each cold-fetches once.
    assert backend.fetch_calls == 2


# ---------- writes ----------


@pytest.mark.asyncio
async def test_put_delegates_to_backend() -> None:
    backend = FakeKmsBackend()
    store = AliyunKmsSecretStore(backend)

    await store.put("k", "new-value")
    assert backend.put_calls == [("k", "new-value")]


@pytest.mark.asyncio
async def test_put_invalidates_cached_value() -> None:
    backend = FakeKmsBackend()
    backend.seed("k", "v")
    store = AliyunKmsSecretStore(backend, clock=FakeClock())

    await store.get("k")  # caches
    await store.put("k", "rotated")
    await store.get("k")  # cache dropped → re-fetch
    assert backend.fetch_calls == 2


@pytest.mark.asyncio
async def test_list_versions_delegates_to_backend() -> None:
    backend = FakeKmsBackend()
    backend.seed("k", "v", version="rev-7")
    store = AliyunKmsSecretStore(backend)

    assert await store.list_versions("k") == ["rev-7"]


# ---------- protocol conformance ----------


def test_satisfies_secret_store_protocol() -> None:
    store = AliyunKmsSecretStore(FakeKmsBackend())
    assert isinstance(store, SecretStore)
