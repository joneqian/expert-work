"""``/v1/members`` tenant-member onboarding endpoints — Stream R W2 (R-5).

A tenant ADMIN invites employees, lists the roster, re-sends a stalled invite,
and revokes / suspends a member. Each invite provisions a Keycloak account
(``create_user`` + native set-password email) and writes a tenant-scope role
binding, using the same DB-first + idempotent-compensation discipline as the
first-admin flow (Mini-ADR R-4).

Scope: these endpoints are tenant-scoped — an admin manages **their own**
tenant via request-time RLS (``principal.tenant_id``); there is no cross-tenant
member management here (that is the platform-admin / first-admin path).
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from control_plane.api._authz import require
from control_plane.api.member_ops import (
    MemberConflictError,
    MemberKeycloakUnavailableError,
    invite_member,
    resend_member,
)
from control_plane.audit import emit
from control_plane.keycloak import KeycloakAdminClient, KeycloakUnavailableError
from control_plane.settings import Settings
from helix_agent.common.observability import current_trace_id_hex
from helix_agent.persistence.auth import RoleBindingStore
from helix_agent.persistence.tenant_member import TenantMemberStore
from helix_agent.protocol import AuditAction, MemberRole, MemberStatus, Principal
from helix_agent.runtime.audit.logger import AuditLogger

logger = logging.getLogger("helix.control_plane.api.members")

_MAX_BATCH = 50


def _normalise_email(value: str) -> str:
    email = value.strip()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise ValueError("not a valid email address")
    return email.lower()


class InvitationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(max_length=320)
    role: MemberRole
    display_name: str | None = Field(default=None, max_length=128)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return _normalise_email(v)


class InviteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invitations: list[InvitationItem] = Field(min_length=1, max_length=_MAX_BATCH)


def _get_member_repo(request: Request) -> TenantMemberStore:
    return request.app.state.tenant_member_repo  # type: ignore[no-any-return]


def _get_role_binding_repo(request: Request) -> RoleBindingStore:
    return request.app.state.role_binding_repo  # type: ignore[no-any-return]


def _get_keycloak(request: Request) -> KeycloakAdminClient:
    return request.app.state.keycloak_admin_client  # type: ignore[no-any-return]


def _get_audit(request: Request) -> AuditLogger:
    return request.app.state.audit_logger  # type: ignore[no-any-return]


def _get_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def build_members_router() -> APIRouter:
    router = APIRouter(prefix="/v1/members", tags=["members"])

    @router.post("/invite", status_code=201)
    async def invite(
        payload: InviteRequest,
        principal: Annotated[Principal, Depends(require("user", "write"))],
        member_repo: Annotated[TenantMemberStore, Depends(_get_member_repo)],
        role_binding_repo: Annotated[RoleBindingStore, Depends(_get_role_binding_repo)],
        keycloak: Annotated[KeycloakAdminClient, Depends(_get_keycloak)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
        settings: Annotated[Settings, Depends(_get_settings)],
    ) -> dict[str, object]:
        # Each invitation is independent — one Keycloak 409 does not abort the
        # rest of the batch (per-item result with error_code).
        results: list[dict[str, object]] = []
        for item in payload.invitations:
            try:
                summary = await invite_member(
                    tenant_id=principal.tenant_id,
                    email=item.email,
                    role=item.role,
                    display_name=item.display_name,
                    actor_id=principal.subject_id,
                    member_store=member_repo,
                    role_binding_store=role_binding_repo,
                    keycloak=keycloak,
                    audit=audit,
                    email_action_lifespan_s=settings.keycloak_email_action_lifespan_s,
                )
                results.append(
                    {
                        "email": item.email,
                        "member_id": str(summary.member_id),
                        "status": summary.status,
                        "error_code": None,
                    }
                )
            except MemberConflictError:
                results.append(
                    {
                        "email": item.email,
                        "member_id": None,
                        "status": None,
                        "error_code": "MEMBER_KEYCLOAK_CONFLICT",
                    }
                )
            except MemberKeycloakUnavailableError:
                results.append(
                    {
                        "email": item.email,
                        "member_id": None,
                        "status": None,
                        "error_code": "KEYCLOAK_UNAVAILABLE",
                    }
                )
        return {"success": True, "data": {"results": results}, "error": None}

    @router.get("")
    async def list_members(
        principal: Annotated[Principal, Depends(require("user", "read"))],
        member_repo: Annotated[TenantMemberStore, Depends(_get_member_repo)],
        status: Annotated[MemberStatus | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, object]:
        items = await member_repo.list_for_tenant(
            tenant_id=principal.tenant_id, status=status, limit=limit, offset=offset
        )
        return {
            "success": True,
            "data": {
                "items": [m.model_dump(mode="json") for m in items],
                "total": len(items),
            },
            "error": None,
        }

    @router.post("/{member_id}/resend")
    async def resend(
        member_id: UUID,
        principal: Annotated[Principal, Depends(require("user", "write"))],
        member_repo: Annotated[TenantMemberStore, Depends(_get_member_repo)],
        role_binding_repo: Annotated[RoleBindingStore, Depends(_get_role_binding_repo)],
        keycloak: Annotated[KeycloakAdminClient, Depends(_get_keycloak)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
        settings: Annotated[Settings, Depends(_get_settings)],
    ) -> dict[str, object]:
        member = await member_repo.get(tenant_id=principal.tenant_id, member_id=member_id)
        if member is None:
            raise HTTPException(status_code=404, detail={"code": "MEMBER_NOT_FOUND"})
        if member.status != "invited":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "MEMBER_NOT_RESENDABLE",
                    "message": f"member is {member.status}, not invited",
                },
            )
        try:
            summary = await resend_member(
                member=member,
                actor_id=principal.subject_id,
                member_store=member_repo,
                role_binding_store=role_binding_repo,
                keycloak=keycloak,
                audit=audit,
                email_action_lifespan_s=settings.keycloak_email_action_lifespan_s,
            )
        except MemberConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "MEMBER_KEYCLOAK_CONFLICT",
                    "message": "email already exists in keycloak",
                },
            ) from exc
        except MemberKeycloakUnavailableError as exc:
            raise HTTPException(
                status_code=502,
                detail={"code": "KEYCLOAK_UNAVAILABLE", "message": "keycloak unreachable; retry"},
            ) from exc
        return {
            "success": True,
            "data": {
                "member_id": str(summary.member_id),
                "status": summary.status,
                "keycloak_user_id": summary.keycloak_user_id,
            },
            "error": None,
        }

    @router.delete("/{member_id}", status_code=204)
    async def revoke(
        member_id: UUID,
        principal: Annotated[Principal, Depends(require("user", "write"))],
        member_repo: Annotated[TenantMemberStore, Depends(_get_member_repo)],
        keycloak: Annotated[KeycloakAdminClient, Depends(_get_keycloak)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
    ) -> None:
        from datetime import UTC, datetime

        member = await member_repo.get(tenant_id=principal.tenant_id, member_id=member_id)
        if member is None:
            raise HTTPException(status_code=404, detail={"code": "MEMBER_NOT_FOUND"})

        now = datetime.now(UTC)
        if member.status == "invited":
            # Withdraw the invite — soft-delete + remove the Keycloak account.
            moved = await member_repo.transition(
                member_id=member.id, tenant_id=principal.tenant_id, to="revoked", now=now
            )
            if moved and member.keycloak_user_id is not None:
                try:
                    await keycloak.delete_user(user_id=member.keycloak_user_id)
                except KeycloakUnavailableError:
                    logger.warning("member.revoke.keycloak_delete_failed member_id=%s", member.id)
            action = AuditAction.MEMBER_REVOKE
        elif member.status == "active":
            # Suspend — disable the account (single-direction this iteration).
            moved = await member_repo.transition(
                member_id=member.id, tenant_id=principal.tenant_id, to="suspended", now=now
            )
            if moved and member.keycloak_user_id is not None:
                try:
                    await keycloak.set_enabled(user_id=member.keycloak_user_id, enabled=False)
                except KeycloakUnavailableError:
                    logger.warning("member.suspend.keycloak_disable_failed member_id=%s", member.id)
            action = AuditAction.MEMBER_SUSPEND
        else:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "MEMBER_NOT_REMOVABLE",
                    "message": f"member is already {member.status}",
                },
            )
        await emit(
            audit,
            tenant_id=principal.tenant_id,
            actor_id=principal.subject_id,
            action=action,
            resource_type="tenant_member",
            resource_id=str(member.id),
            trace_id=current_trace_id_hex(),
            details={"email": member.email, "from_status": member.status},
        )

    return router
