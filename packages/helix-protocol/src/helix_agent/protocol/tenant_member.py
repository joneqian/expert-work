"""``tenant_member`` row shape — Stream R (member onboarding).

A *member* is the **invitation-state roster** of a tenant: who the company's
admin has invited, what role they were granted, and where they are in the
``invited → active → suspended / revoked`` lifecycle. It is the control-plane
source of truth for "who belongs to this company", distinct from
``tenant_user`` (the runtime registry created JIT on first login — Stream
J.14): the two are connected by ``keycloak_user_id``, with no FK (a FORCE-RLS
table FK is a known footgun, see migration 0015).

``(tenant_id, lower(email))`` is the natural identity for an active invite
(a partial-unique index excludes ``revoked`` so a revoked email can be
re-invited — Mini-ADR R-10). ``subject_id`` is NULL until first login, when
the W3 hook back-fills it with the resolved ``tenant_user.id`` (Mini-ADR R-6).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

MemberStatus = Literal["invited", "active", "suspended", "revoked"]
"""Lifecycle states. ``invited``: roster row written, Keycloak account may or
may not be provisioned yet (``keycloak_user_id`` NULL = pending retry).
``active``: employee has logged in and run at least once (W3 hook). ``suspended``:
admin disabled an active member (Keycloak ``enabled=false``); single-direction
this iteration. ``revoked``: invite withdrawn before activation (soft-delete;
the email may be re-invited)."""

# The tenant-scope roles a member may hold; system_admin is platform-scope and
# never lives in tenant_member.
MemberRole = Literal["admin", "operator", "viewer"]


class TenantMember(BaseModel):
    """One row of ``tenant_member`` — a tenant's invitation-state roster entry."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: UUID
    email: str = Field(description="invitation target; lower()-normalised for the identity key")
    display_name: str | None = None
    role: MemberRole
    status: MemberStatus
    keycloak_user_id: str | None = Field(
        default=None,
        description="Keycloak user uuid; NULL until account provisioning succeeds (pending retry)",
    )
    subject_id: UUID | None = Field(
        default=None,
        description="back-filled with tenant_user.id on first login (Mini-ADR R-6)",
    )
    invited_by: str = Field(description="actor principal.subject_id")
    invited_at: datetime | None = None
    activated_at: datetime | None = Field(
        default=None, description="set when the W3 first-run hook promotes invited→active"
    )
    updated_at: datetime | None = None
