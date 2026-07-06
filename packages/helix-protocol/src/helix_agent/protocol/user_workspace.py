"""Per-user persistent workspace — Stream J.15.

A :class:`UserWorkspace` is the durable workspace of one
``(tenant_id, user_id)`` pair — a docker named volume that outlives the
ephemeral sandbox containers mounting it. Files an agent writes under
``/workspace`` in one run survive into the next (STREAM-J-DESIGN § 9).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class UserWorkspace(BaseModel):
    """One row of ``user_workspace`` — a user's persistent volume.

    J.15-补强-1 (STREAM-J-DESIGN § 9.5) adds three fields:

    * ``size_limit_bytes`` — quota ceiling (Mini-ADR J-29 第 1 项).
    * ``deleted_at`` — soft-delete timestamp (Mini-ADR J-36). ``None`` ⇒ active.
    * ``archived_object_key`` — ObjectStore key where the tar.zst archive
      landed after the reaper sweep (Mini-ADR J-36). ``None`` while
      pending archive.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: UUID
    user_id: UUID
    volume_name: str = Field(
        description="docker named volume identifier — deterministic per (tenant, user)"
    )
    size_bytes: int = Field(
        default=0, ge=0, description="last measured volume size; 0 until first measurement"
    )
    size_limit_bytes: int = Field(
        default=10 * 1024 * 1024 * 1024,
        gt=0,
        description=(
            "quota ceiling — supervisor rejects acquire when size_bytes >= size_limit_bytes"
        ),
    )
    created_at: datetime | None = None
    last_accessed_at: datetime | None = None
    last_write_at: datetime | None = Field(
        default=None,
        description=(
            "RT-6 Tier B (RT-ADR-20) — bumped to now() under the workspace write "
            "lock whenever a write-capable tool (write_file / edit_file / bash) "
            "completes. A conservative over-approximation of 'the workspace "
            "changed': a read-only bash (ls / cat) also takes the lock and bumps, "
            "so it means 'a mutating-capable tool ran', not a proven mutation — "
            "the safe direction for a forensic signal (never miss a real write). "
            "A paused approval whose requested_at predates this may have had its "
            "workspace swapped before execution (approve-then-swap-script); "
            "surfaced audit-only, never blocks. None ⇒ no such write recorded."
        ),
    )
    deleted_at: datetime | None = Field(
        default=None,
        description="soft-delete timestamp; None ⇒ active. Acquire rejects deleted workspaces.",
    )
    archived_object_key: str | None = Field(
        default=None,
        description=(
            "ObjectStore key of the tar.zst archive. Populated by reaper after archive "
            "completes; only meaningful when deleted_at is also set."
        ),
    )
