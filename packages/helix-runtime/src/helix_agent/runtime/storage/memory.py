"""``InMemoryObjectStore`` — process-local store for unit tests / dev."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from helix_agent.runtime.storage.base import ObjectNotFoundError


@dataclass(frozen=True)
class _StoredObject:
    """Internal record. Kept frozen to enforce the immutability rule."""

    data: bytes
    content_type: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


class InMemoryObjectStore:
    """Thread-safe in-memory store; intended for unit tests and dev fakes."""

    def __init__(self) -> None:
        self._objects: dict[str, _StoredObject] = {}
        self._lock = asyncio.Lock()

    async def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        record = _StoredObject(
            data=data,
            content_type=content_type,
            metadata=dict(metadata) if metadata else {},
        )
        async with self._lock:
            self._objects[key] = record

    async def get(self, key: str) -> bytes:
        record = self._objects.get(key)
        if record is None:
            msg = f"object not found: {key!r}"
            raise ObjectNotFoundError(msg)
        return record.data

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._objects.pop(key, None)

    async def list_prefix(self, prefix: str) -> list[str]:
        return sorted(k for k in self._objects if k.startswith(prefix))

    async def presigned_url(
        self,
        key: str,
        *,
        expires_in: int = 3600,
        method: Literal["GET", "PUT"] = "GET",
    ) -> str:
        """Return a deterministic fake URL — there is no real signing here.

        Sufficient for tests that need *some* URL; production code paths
        must hit :class:`S3CompatibleObjectStore`.
        """
        return f"memory://{key}?method={method}&expires_in={expires_in}"
