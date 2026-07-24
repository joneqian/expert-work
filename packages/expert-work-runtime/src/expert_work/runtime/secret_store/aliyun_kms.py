"""Aliyun KMS Secrets Manager backend — Stream F.6, ADR-0007 § 2.1.

:class:`AliyunKmsSecretStore` is a :class:`SecretStore` that adds a
short-TTL in-process cache over a :class:`KmsBackend`. The cache keeps
KMS read QPS down (ADR-0007: "secret 读取必须 cache") without holding a
value long enough to miss a rotation:

* ``static`` secrets (long-lived API keys) — cached up to 60 s.
* ``dynamic`` secrets (short-TTL credentials) — cached for half their
  rotation TTL, leaving a refresh window before they expire.

The actual Aliyun SDK :class:`KmsBackend` implementation is a
deploy-time follow-up (STREAM-F-DESIGN Mini-ADR F-7, option 2): it can
only be verified against a live Aliyun account — there is no local KMS
emulator the way MinIO stands in for S3. This module ships the
*testable* core — the cache, TTL policy, and ``SecretStore`` conformance
— with the backend injected so it is fully unit-tested with a fake.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

#: ``static`` secrets are cached at most this long (ADR-0007 § 2.1).
STATIC_CACHE_TTL_S = 60

SecretKind = Literal["static", "dynamic"]


@dataclass(frozen=True)
class FetchedSecret:
    """One secret value plus the metadata the cache TTL is derived from."""

    value: str
    version: str
    kind: SecretKind
    #: The secret's own rotation interval, in seconds (from KMS metadata).
    rotation_ttl_s: int


class KmsBackend(Protocol):
    """The raw KMS operations :class:`AliyunKmsSecretStore` builds on.

    The deploy-time Aliyun SDK adapter implements this; unit tests inject
    a fake. Keeping it a Protocol means the cache logic is verified
    without the (un-CI-testable) real SDK.
    """

    async def fetch_secret(self, name: str, version: str | None) -> FetchedSecret:
        """Return the secret ``name`` (``version=None`` → latest).

        Raises :class:`~expert_work.runtime.secret_store.base.SecretNotFoundError`
        when no such secret / version exists.
        """

    async def put_secret(self, name: str, value: str) -> None:
        """Create or update the secret ``name`` — admin / bootstrap only."""

    async def list_versions(self, name: str) -> list[str]:
        """Return ``name``'s version identifiers, newest first."""

    async def delete_secret(self, name: str) -> None:
        """Remove every version of the secret ``name``.

        Idempotent — deleting an absent name does NOT raise.
        """


@dataclass(frozen=True)
class _CacheEntry:
    value: str
    expires_at: float


def _cache_ttl(kind: SecretKind, rotation_ttl_s: int) -> int:
    """Cache TTL for a secret — never longer than its own rotation window."""
    if kind == "static":
        return min(rotation_ttl_s, STATIC_CACHE_TTL_S)
    # Dynamic: half the rotation TTL leaves a refresh window; floor at 1 s.
    return max(1, rotation_ttl_s // 2)


class AliyunKmsSecretStore:
    """:class:`SecretStore` over Aliyun KMS with a short-TTL read cache.

    ``clock`` is injectable so cache expiry is deterministic in tests; it
    defaults to :func:`time.monotonic` (monotonic — immune to wall-clock
    jumps).
    """

    def __init__(
        self,
        backend: KmsBackend,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._backend = backend
        self._clock = clock
        self._cache: dict[tuple[str, str | None], _CacheEntry] = {}

    async def get(self, name: str, *, version: str | None = None) -> str:
        """Return the secret value, serving a non-expired cache entry if any."""
        key = (name, version)
        entry = self._cache.get(key)
        if entry is not None and self._clock() < entry.expires_at:
            return entry.value

        fetched = await self._backend.fetch_secret(name, version)
        ttl = _cache_ttl(fetched.kind, fetched.rotation_ttl_s)
        self._cache[key] = _CacheEntry(
            value=fetched.value,
            expires_at=self._clock() + ttl,
        )
        return fetched.value

    async def put(self, name: str, value: str) -> None:
        """Write a secret, then drop every cached version of ``name``."""
        await self._backend.put_secret(name, value)
        self._cache = {k: v for k, v in self._cache.items() if k[0] != name}

    async def list_versions(self, name: str) -> list[str]:
        """Return ``name``'s known versions — never cached."""
        return await self._backend.list_versions(name)

    async def delete(self, name: str) -> None:
        """Delete a secret, then drop every cached version of ``name``."""
        await self._backend.delete_secret(name)
        self._cache = {k: v for k, v in self._cache.items() if k[0] != name}
