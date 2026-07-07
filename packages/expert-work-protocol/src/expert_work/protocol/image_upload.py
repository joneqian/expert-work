"""J.6.补强-3 (Mini-ADR J-32) — ``image_upload`` row DTO.

The control-plane ``POST /v1/sessions/{thread_id}/uploads`` endpoint
writes one row per landed image; ``DELETE /v1/uploads/{id}`` flips
``deleted_at`` to soft-delete the row. The retention-cleanup-job hard-
deletes rows past their tenant's retention window + the corresponding
object-store keys.

Tenant RLS lives on the table (migration 0028). The DTO is the wire
shape — every CRUD surface (API responses, admin tooling, retention
sweep) reads / writes through this model so the wire schema can't
drift from the DB row.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["ImageUpload"]


class ImageUpload(BaseModel):
    """One row of ``image_upload`` — one landed image.

    ``deleted_at IS None`` = active (visible to runs, billable against
    the J-30 ``IMAGE_STORAGE_BYTES`` quota). Soft-deleted rows linger
    until retention cleanup hard-deletes them and the object store key.

    ``user_id`` is nullable because not every upload path runs under a
    per-user JWT (admin / service-account uploads carry the tenant but
    not a user).
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: UUID
    thread_id: UUID
    user_id: UUID | None = None
    object_key: str
    size_bytes: int = Field(ge=0)
    mime_type: str
    sha256: str
    created_at: datetime
    deleted_at: datetime | None = None
