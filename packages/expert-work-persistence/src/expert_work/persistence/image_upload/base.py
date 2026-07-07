"""Abstract ``ImageUploadStore`` repository — Stream J.6.补强-3 (Mini-ADR J-32).

Implementations:

* :class:`expert_work.persistence.image_upload.memory.InMemoryImageUploadStore`
* :class:`expert_work.persistence.image_upload.sql.SqlImageUploadStore`

The store is scoped by ``tenant_id`` at the application layer; the SQL
implementation also applies a tenant RLS policy (migration 0028) so a
forgotten WHERE clause cannot cross-leak.
"""

from __future__ import annotations

import abc
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from expert_work.protocol import ImageUpload


class ImageUploadNotFoundError(KeyError):
    """Raised when an op targets an ``image_upload`` row that doesn't exist."""


class ImageUploadStore(abc.ABC):
    """Per-tenant registry of landed image uploads.

    Two outward-facing read shapes: ``get`` (one row by id) and
    ``list_active`` (rows for a thread / tenant where ``deleted_at IS
    NULL``). The retention sweep uses :meth:`list_expired` to find rows
    past their tenant's retention window + :meth:`hard_delete` to remove
    them once the object-store key is cleared.
    """

    @abc.abstractmethod
    async def insert(
        self,
        *,
        image_id: UUID,
        tenant_id: UUID,
        thread_id: UUID,
        user_id: UUID | None,
        object_key: str,
        size_bytes: int,
        mime_type: str,
        sha256: str,
    ) -> ImageUpload:
        """Persist one row for a successful upload.

        Returns the materialised row including the server-assigned
        ``created_at`` so the caller can echo it back in the API
        response without a re-read.
        """

    @abc.abstractmethod
    async def get(self, *, image_id: UUID, tenant_id: UUID) -> ImageUpload | None:
        """Return the row by id, or ``None`` when the id is unknown or
        belongs to a different tenant (404-equivalent — never raise on
        cross-tenant probes)."""

    @abc.abstractmethod
    async def soft_delete(self, *, image_id: UUID, tenant_id: UUID, now: datetime) -> bool:
        """Flip ``deleted_at`` for an active row; return ``False`` when
        the id is unknown, already soft-deleted, or in another tenant
        (caller turns that into 404 — same hides-cross-tenant rule)."""

    @abc.abstractmethod
    async def list_active_for_thread(
        self,
        *,
        tenant_id: UUID,
        thread_id: UUID,
    ) -> list[ImageUpload]:
        """Active rows in a thread — used by tests + the J.6 image listing
        path. Sorted by ``created_at`` descending."""

    @abc.abstractmethod
    async def list_expired(
        self,
        *,
        before: datetime,
        limit: int = 1000,
    ) -> list[ImageUpload]:
        """Rows older than ``before`` AND still alive OR already soft-deleted.

        The retention sweep calls this with ``now - retention_days``;
        any row with ``created_at < before`` is eligible to be removed
        from the object store + hard-deleted, regardless of
        ``deleted_at`` state.
        """

    @abc.abstractmethod
    async def hard_delete(self, *, image_ids: Sequence[UUID]) -> int:
        """Remove rows from the table; caller must have already cleared
        the corresponding object-store keys. Returns the count of rows
        actually deleted (idempotent — already-removed ids contribute 0)."""
