"""``tenant_user`` row shape — Stream J.14 (per-user scope).

A *user* is a principal (a human, or a service account) acting within a
tenant. The canonical product form is per-user: each user owns a
persistent agent instance — conversation, long-term memory, workspace.
``tenant_user`` is the registry that makes "user" a first-class entity.

``(tenant_id, subject_type, subject_id)`` is the natural identity key;
``id`` is the surrogate ``user_id`` that owned tables (``thread_meta``
today, memory / workspace / artifact in later Stream J sub-items)
reference. Hard isolation stays at the tenant boundary (RLS); ``user_id``
is a first-class ownership column scoped at the application layer
(STREAM-J-DESIGN Mini-ADR J-1).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from expert_work.protocol.auth import SubjectType


class TenantUser(BaseModel):
    """One row of ``tenant_user`` — the per-user registry entry."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: UUID
    subject_type: SubjectType
    subject_id: str = Field(description="OIDC sub / service-account id of the principal")
    display_name: str | None = Field(
        default=None, description="human-readable label; populated by Stream H admin UI"
    )
    created_at: datetime | None = None
    last_active_at: datetime | None = Field(
        default=None,
        description="bumped on every resolve(); drives J.15 idle/hibernate later",
    )
    deleted_at: datetime | None = Field(
        default=None,
        description=(
            "soft-deactivation stamp (Phase 3a purge_user). NON-NULL = the user "
            "was purged; list_by_tenant excludes these rows so a purged user "
            "never reappears in the roster. get still returns them (re-purge "
            "stays idempotent); resolve clears the stamp so a returning identity "
            "reactivates cleanly."
        ),
    )
