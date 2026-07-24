"""Unit tests for :class:`AliyunKmsSecretStore` — Stream F.6 (test matrix #54).

The Aliyun SDK :class:`KmsBackend` is faked, so these exercise the
testable core: cache hits, the static / dynamic TTL policy, expiry,
write-through invalidation, and ``SecretStore`` conformance.
"""

from __future__ import annotations

import pytest

from expert_work.runtime.secret_store import (
    AliyunKmsSecretStore,
    FetchedSecret,
    SecretNotFoundError,
    SecretStore,
)
from expert_work.runtime.secret_store.aliyun_kms import SecretKind


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
        self.delete_calls: list[str] = []

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

    async def delete_secret(self, name: str) -> None:
        self.delete_calls.append(name)
        self.secrets.pop(name, None)

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
async def test_delete_delegates_to_backend() -> None:
    backend = FakeKmsBackend()
    backend.seed("k", "v")
    store = AliyunKmsSecretStore(backend)

    await store.delete("k")
    assert backend.delete_calls == ["k"]


@pytest.mark.asyncio
async def test_delete_invalidates_cached_value() -> None:
    backend = FakeKmsBackend()
    backend.seed("k", "v")
    store = AliyunKmsSecretStore(backend, clock=FakeClock())

    await store.get("k")  # caches
    await store.delete("k")
    backend.seed("k", "v2")  # re-seed so the post-delete re-fetch has something
    await store.get("k")  # cache dropped → re-fetch
    assert backend.fetch_calls == 2


@pytest.mark.asyncio
async def test_delete_missing_secret_does_not_raise() -> None:
    """Idempotent — deleting an absent name is a no-op, not an error."""
    store = AliyunKmsSecretStore(FakeKmsBackend())
    await store.delete("nope")


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


# ---------- Stream K.K13 — KMS rotation drill ----------


@pytest.mark.asyncio
async def test_rotation_eventually_serves_new_value_after_cache_ttl() -> None:
    """Stream K.K13 — when KMS rotates a secret behind our back, the
    cache holds the stale value for at most one TTL window, then the
    next read picks up the new version.

    Matches the production failure mode: an operator triggers a
    rotation on the KMS side; the expert_work process should converge to the
    new value within the configured 60 s static TTL without anyone
    restarting the service.
    """
    backend = FakeKmsBackend()
    backend.seed("anthropic/api-key", "sk-old", kind="static", rotation_ttl_s=3600)
    clock = FakeClock()
    store = AliyunKmsSecretStore(backend, clock=clock)

    # Initial read fills the cache with the old value.
    assert await store.get("anthropic/api-key") == "sk-old"

    # KMS rotates the secret behind our back — same name, new value /
    # version. The fake mirrors what a real KMS rotation looks like
    # from the cache's point of view: ``fetch_secret`` will return the
    # new value, but the store does not call ``put`` itself.
    backend.seed("anthropic/api-key", "sk-new", kind="static", rotation_ttl_s=3600, version="v2")

    # Within the 60 s static TTL the cached old value is still served
    # (the price of read caching — bounded by TTL).
    clock.advance(30)
    assert await store.get("anthropic/api-key") == "sk-old"

    # After the cache TTL elapses (60 s static), the next read fetches
    # fresh — and now sees the rotated value.
    clock.advance(31)  # total 61 s since the original read
    assert await store.get("anthropic/api-key") == "sk-new"


@pytest.mark.asyncio
async def test_rotation_with_explicit_put_invalidates_immediately() -> None:
    """Stream K.K13 — when the rotation happens through our own
    :meth:`put` (e.g. an admin tool wiring through this store), the
    cache invalidates immediately and the next read shows the new
    value, no TTL wait."""
    backend = FakeKmsBackend()
    backend.seed("db/password", "pw-old")
    clock = FakeClock()
    store = AliyunKmsSecretStore(backend, clock=clock)

    assert await store.get("db/password") == "pw-old"

    # Rotate via the store's put — invalidate cache, write through backend.
    backend.seed("db/password", "pw-new", version="v2")
    await store.put("db/password", "pw-new")

    # Same clock tick — cache TTL hasn't moved — yet we see the new value.
    assert await store.get("db/password") == "pw-new"


@pytest.mark.asyncio
async def test_dynamic_rotation_converges_faster_than_static() -> None:
    """Stream K.K13 — dynamic secrets (Vault-style short-TTL credentials)
    should converge to a rotated value in half the rotation window, not
    the 60 s static cap."""
    backend = FakeKmsBackend()
    backend.seed("vault/db", "cred-old", kind="dynamic", rotation_ttl_s=600)
    clock = FakeClock()
    store = AliyunKmsSecretStore(backend, clock=clock)

    assert await store.get("vault/db") == "cred-old"
    backend.seed("vault/db", "cred-new", kind="dynamic", rotation_ttl_s=600, version="v2")

    # 250 s in — still inside the half-TTL cache (300 s).
    clock.advance(250)
    assert await store.get("vault/db") == "cred-old"

    # Past 300 s, the next read fetches and sees the new credential.
    clock.advance(60)
    assert await store.get("vault/db") == "cred-new"
