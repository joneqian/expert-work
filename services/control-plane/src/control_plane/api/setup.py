"""First-run setup wizard — Stream ACCT (Mini-ADR ACCT-2).

Lets an operator stand up the **first platform ``system_admin`` without ever
opening the Keycloak Admin Console**: a fresh deployment exposes an
unauthenticated ``POST /v1/setup`` that creates the reserved "[Platform]"
tenant, a Keycloak account (verified, with the chosen password), and the
platform-scope ``system_admin`` binding — after which the operator logs in
normally and manages everyone else from the admin UI.

Two hard gates make the unauthenticated endpoint safe:

* **Setup token** — the request MUST carry ``X-Setup-Token`` matching
  ``settings.setup_token`` (set as an env var at deploy). With no token
  configured the endpoint is refused outright. This closes the deploy-window
  hijack where anyone reaching the instance before the operator could seize
  ``system_admin``.
* **Zero-admin invariant** — setup only runs while the platform holds zero
  ``system_admin`` bindings; once one exists the endpoint returns 409. Combined
  with the token, setup is effectively one-shot.

``GET /v1/setup/status`` (no token) lets the SPA decide whether to route a
fresh visitor to the wizard.
"""

from __future__ import annotations

import hmac
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field, SecretStr

from control_plane.bootstrap_admin import bootstrap_system_admin
from control_plane.keycloak import KeycloakAdminClient, KeycloakUnavailableError
from control_plane.keycloak.errors import KeycloakUserExistsError
from control_plane.settings import Settings
from control_plane.tenant_scope import bypass_rls_session
from expert_work.persistence.auth import RoleBindingStore
from expert_work.persistence.tenant_config.base import TenantConfigStore
from expert_work.protocol import AuditAction
from expert_work.runtime.audit.logger import AuditLogger
from expert_work.runtime.secret_store.base import SecretNotFoundError

logger = logging.getLogger("expert_work.control_plane.setup")

_ACTOR = "setup-wizard"


class SetupRequest(BaseModel):
    """First-run payload — creates the first platform ``system_admin``."""

    admin_email: str = Field(min_length=3, max_length=254)
    admin_password: SecretStr = Field(min_length=8, max_length=256)
    admin_display_name: str | None = Field(default=None, max_length=128)
    platform_tenant_display_name: str = Field(default="Platform", min_length=1, max_length=128)


def _get_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def _get_role_binding_repo(request: Request) -> RoleBindingStore:
    return request.app.state.role_binding_repo  # type: ignore[no-any-return]


def _get_tenant_config_repo(request: Request) -> TenantConfigStore:
    return request.app.state.tenant_config_repo  # type: ignore[no-any-return]


def _get_keycloak(request: Request) -> KeycloakAdminClient:
    return request.app.state.keycloak_admin_client  # type: ignore[no-any-return]


def _get_audit(request: Request) -> AuditLogger:
    return request.app.state.audit_logger  # type: ignore[no-any-return]


async def _platform_admin_exists(store: RoleBindingStore) -> bool:
    async with bypass_rls_session():
        return len(await store.list_platform_scope()) > 0


def build_setup_router() -> APIRouter:
    router = APIRouter(prefix="/v1/setup", tags=["setup"])

    @router.get("/status")
    async def status(
        role_binding_repo: Annotated[RoleBindingStore, Depends(_get_role_binding_repo)],
        settings: Annotated[Settings, Depends(_get_settings)],
    ) -> dict[str, object]:
        # No token required — the SPA calls this pre-auth to decide whether to
        # show the wizard. Only leaks "is this instance initialized yet".
        initialized = await _platform_admin_exists(role_binding_repo)
        return {
            "success": True,
            "data": {"initialized": initialized, "setup_enabled": settings.setup_token is not None},
            "error": None,
        }

    @router.post("")
    async def run_setup(
        body: SetupRequest,
        role_binding_repo: Annotated[RoleBindingStore, Depends(_get_role_binding_repo)],
        tenant_config_repo: Annotated[TenantConfigStore, Depends(_get_tenant_config_repo)],
        keycloak: Annotated[KeycloakAdminClient, Depends(_get_keycloak)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
        settings: Annotated[Settings, Depends(_get_settings)],
        x_setup_token: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        # Gate 1 — setup token (constant-time). No configured token ⇒ refuse.
        if settings.setup_token is None:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "SETUP_NOT_CONFIGURED",
                    "message": "EXPERT_WORK_SETUP_TOKEN is not set; setup is disabled",
                },
            )
        if x_setup_token is None or not hmac.compare_digest(x_setup_token, settings.setup_token):
            raise HTTPException(
                status_code=403,
                detail={"code": "INVALID_SETUP_TOKEN", "message": "missing or invalid setup token"},
            )

        # Gate 2 — zero-admin invariant. Once a platform admin exists, setup is
        # closed (one-shot). This is also the idempotency guard against retries.
        if await _platform_admin_exists(role_binding_repo):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "ALREADY_INITIALIZED",
                    "message": "a platform admin already exists; setup is complete",
                },
            )

        result = await provision_platform_admin(
            email=body.admin_email,
            password=body.admin_password.get_secret_value(),
            display_name=body.admin_display_name,
            platform_tenant_id=settings.platform_tenant_id,
            platform_tenant_display_name=body.platform_tenant_display_name,
            tenant_config_store=tenant_config_repo,
            role_binding_store=role_binding_repo,
            keycloak=keycloak,
            audit=audit,
        )
        return {"success": True, "data": result, "error": None}

    return router


async def provision_platform_admin(
    *,
    email: str,
    password: str,
    display_name: str | None,
    platform_tenant_id: UUID,
    platform_tenant_display_name: str,
    tenant_config_store: TenantConfigStore,
    role_binding_store: RoleBindingStore,
    keycloak: KeycloakAdminClient,
    audit: AuditLogger,
) -> dict[str, str]:
    """Create the platform tenant + first ``system_admin`` (verified, password set).

    Caller MUST have already enforced the token + zero-admin gates. Steps are
    ordered so an external (Keycloak) failure leaves only the idempotent tenant
    row behind; re-running after such a failure re-creates nothing it shouldn't.
    """
    # Step 1 — reserved platform tenant (FORCE-RLS ⇒ bypass). Idempotent.
    async with bypass_rls_session():
        if await tenant_config_store.get(tenant_id=platform_tenant_id) is None:
            await tenant_config_store.create(
                tenant_id=platform_tenant_id,
                display_name=platform_tenant_display_name,
                actor_id=_ACTOR,
            )

    # Step 2 — Keycloak account: verified + chosen password, homed to the
    # platform tenant (the tenant_id attribute → JWT claim → Principal).
    try:
        kc_user = await keycloak.create_user(
            email=email,
            tenant_id=platform_tenant_id,
            display_name=display_name,
            email_verified=True,
        )
    except KeycloakUserExistsError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ADMIN_EMAIL_EXISTS",
                "message": "an account with this email already exists in the IdP",
            },
        ) from exc
    except KeycloakUnavailableError as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": "KEYCLOAK_UNAVAILABLE", "message": "identity provider unreachable"},
        ) from exc
    except SecretNotFoundError as exc:
        # The Keycloak Admin client needs its confidential-client secret from the
        # vault to mint an admin token. Missing it is a deploy prerequisite, not a
        # user error — surface it cleanly instead of a bare 500.
        raise HTTPException(
            status_code=502,
            detail={
                "code": "KEYCLOAK_ADMIN_SECRET_MISSING",
                "message": (
                    "the Keycloak admin client secret is not provisioned in the secret "
                    "store (expert-work/platform/keycloak/admin-client-secret); seed it "
                    "before running setup"
                ),
            },
        ) from exc

    try:
        await keycloak.reset_password(user_id=kc_user.id, password=password, temporary=False)
    except KeycloakUnavailableError as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": "KEYCLOAK_UNAVAILABLE", "message": "identity provider unreachable"},
        ) from exc

    # Step 3 — platform-scope system_admin binding (own bypass; idempotent).
    binding = await bootstrap_system_admin(
        role_binding_store, subject_id=UUID(kc_user.id), granted_by=_ACTOR
    )

    await emit_setup_audit(
        audit,
        tenant_id=platform_tenant_id,
        email=email,
        subject_id=kc_user.id,
    )
    logger.info(
        "setup.platform_admin_created",
        extra={"subject_id": kc_user.id, "binding_id": str(binding.binding.id)},
    )
    return {"tenant_id": str(platform_tenant_id), "subject_id": kc_user.id}


async def emit_setup_audit(
    audit: AuditLogger, *, tenant_id: UUID, email: str, subject_id: str
) -> None:
    from control_plane.audit import emit

    details: dict[str, object] = {"email": email, "via": "setup-wizard"}
    await emit(
        audit,
        tenant_id=tenant_id,
        actor_id=_ACTOR,
        action=AuditAction.TENANT_CREATE,
        resource_type="tenant",
        resource_id=str(tenant_id),
        details=details,
    )
    await emit(
        audit,
        tenant_id=tenant_id,
        actor_id=_ACTOR,
        action=AuditAction.ROLE_BINDING_CREATE,
        resource_type="role_binding",
        resource_id=subject_id,
        details=details,
    )
