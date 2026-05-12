"""``ObjectStore`` Protocol — S3-compatible object storage abstraction.

Per [ADR-0004](../../../../../../docs/adr/0004-object-storage.md). All
call sites depend on this Protocol, not the S3 client directly, so the
in-memory implementation can stand in for unit tests and a different
S3-compatible backend (MinIO / Aliyun OSS / AWS S3 / Tencent COS) can be
swapped via the factory without code changes.

**Multipart upload deferred** to M0 follow-up (per ADR § 3). M0 first cut
handles small objects only; ``put`` takes ``bytes`` rather than a stream.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, Protocol


class ObjectStoreError(Exception):
    """Base class for object-store failures."""


class ObjectNotFoundError(ObjectStoreError):
    """Raised by ``get`` / ``delete`` when the key does not exist."""


class ObjectStore(Protocol):
    """Tenant-namespaced object storage surface.

    The store does **not** enforce key-naming convention; callers must
    prefix with ``{tenant_id}/...`` per ADR-0004 § 2.3. Cross-tenant
    isolation lives at the access-control layer (Stream C).
    """

    async def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        """Write ``data`` at ``key``; overwrites if the key already exists."""

    async def get(self, key: str) -> bytes:
        """Read the full object at ``key``.

        :raises ObjectNotFoundError: the key does not exist.
        """

    async def delete(self, key: str) -> None:
        """Remove the object at ``key``.

        Idempotent — deleting a non-existent key does NOT raise.
        """

    async def list_prefix(self, prefix: str) -> list[str]:
        """Return every key beginning with ``prefix``, lexicographically sorted."""

    async def presigned_url(
        self,
        key: str,
        *,
        expires_in: int = 3600,
        method: Literal["GET", "PUT"] = "GET",
    ) -> str:
        """Generate a time-limited signed URL the client can fetch directly.

        Used by the Control Plane to hand off large upload/download flows to
        the client without proxying bytes through the API tier.
        """
