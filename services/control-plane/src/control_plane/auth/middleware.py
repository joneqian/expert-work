"""``AuthMiddleware`` — JWT + mTLS validation in front of the API surface.

Stream C.1 shipped the JWT branch; Stream C.2 added the mTLS branch.
API Key (Stream C.3) will land as a third bearer-prefix recogniser. All
three converge on the same :class:`Principal` envelope (ADR C-2 — single
middleware, unified Principal).

Branch selection (first match wins):

1. ``Authorization: Bearer <jwt>`` → :class:`JWTVerifier`
2. ``X-Forwarded-Client-Cert: <XFCC>`` → :class:`MTLSVerifier`
3. neither → ``401 AUTH_MISSING_CREDENTIALS``

Endpoints exempt from authentication are matched by *prefix* — defaults
are ``/healthz`` and ``/metrics``. Exempt requests pass through without a
principal; AuditContextMiddleware falls back to its dev defaults.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from uuid import UUID

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from control_plane.audit import emit
from control_plane.auth.api_key_verifier import ApiKeyVerifier, is_api_key_bearer
from control_plane.auth.errors import AuthError, MissingCredentialsError
from control_plane.auth.jwt_verifier import JWTVerifier
from control_plane.auth.mtls import MTLSVerifier
from control_plane.auth.system_admin import maybe_bootstrap_system_admin, resolve_system_admin
from control_plane.auth.tenant_roles import resolve_tenant_roles
from control_plane.tenant_status import TenantStatusService
from helix_agent.common.observability import current_trace_id_hex
from helix_agent.persistence.auth import RoleBindingStore
from helix_agent.protocol import AuditAction, AuditResult, Principal
from helix_agent.runtime.audit.logger import AuditLogger

logger = logging.getLogger("helix.control_plane.auth")

_BEARER_PREFIX = "bearer "
_DEFAULT_XFCC_HEADER = "x-forwarded-client-cert"

# Audit failures are bucketed under a synthetic tenant when we couldn't
# extract one from the credentials. Matches B-5's nil-UUID dev convention.
_UNKNOWN_TENANT = UUID("00000000-0000-0000-0000-000000000000")
_UNKNOWN_ACTOR = "unauthenticated"


class AuthMiddleware(BaseHTTPMiddleware):
    """Authenticate the request via JWT or mTLS, then populate ``request.state``."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        verifier: JWTVerifier,
        exempt_path_prefixes: Iterable[str] = ("/healthz", "/metrics"),
        audit_logger: AuditLogger | None = None,
        mtls_verifier: MTLSVerifier | None = None,
        mtls_header_name: str = _DEFAULT_XFCC_HEADER,
        api_key_verifier: ApiKeyVerifier | None = None,
        role_binding_store: RoleBindingStore | None = None,
        tenant_status: TenantStatusService | None = None,
        bootstrap_admin_email: str | None = None,
    ) -> None:
        super().__init__(app)
        self._verifier = verifier
        # Tuple so the prefix scan is O(n) but n is tiny (~2).
        self._exempt = tuple(p.rstrip("/") for p in exempt_path_prefixes)
        self._audit_logger = audit_logger
        self._mtls_verifier = mtls_verifier
        self._mtls_header = mtls_header_name
        self._api_key_verifier = api_key_verifier
        # Stream N:after a JWT or API Key verifier produces a base Principal,
        # ``resolve_system_admin`` queries this store for a platform-scope
        # role binding and augments the Principal with ``is_system_admin``
        # + ``allowed_tenants="*"``. mTLS path bypasses this lookup —
        # internal service principals are not platform admins
        # (they already carry ``allowed_tenants=(system_tenant_id,)``).
        self._role_binding_store = role_binding_store
        # Stream ACCT — when set, the first login of this (verified) email
        # auto-grants platform system_admin while the system has zero admins.
        self._bootstrap_admin_email = bootstrap_admin_email
        # Stream U (PR E) — when set, a SUSPENDED tenant's members are 403'd.
        # system_admin principals carry the system tenant_id, which is never
        # suspended, so they pass through and can reactivate the tenant.
        self._tenant_status = tenant_status

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if self._is_exempt(request.url.path):
            return await call_next(request)

        bearer = _extract_bearer(request)
        if bearer is not None:
            # Pre-dispatch on bearer prefix so API keys never run through
            # the JWT verifier (saves a JWKS lookup; matches ADR C-2).
            if self._api_key_verifier is not None and is_api_key_bearer(bearer):
                try:
                    principal = await self._api_key_verifier.verify(bearer)
                except AuthError as exc:
                    return await self._reject(request, exc)
                # Stream N — augment with platform-admin status if applicable.
                principal = await resolve_system_admin(principal, self._role_binding_store)
                return await self._call_with_principal(request, call_next, principal)
            try:
                claims = await self._verifier.verify(bearer)
            except AuthError as exc:
                return await self._reject(request, exc)
            principal = Principal.from_jwt_claims(claims)
            # Stream N — augment with platform-admin status if applicable.
            principal = await resolve_system_admin(principal, self._role_binding_store)
            # Stream ACCT — first-login bootstrap of the first platform admin.
            # No-op once any admin exists or when no bootstrap email is set.
            if not principal.is_system_admin and self._bootstrap_admin_email:
                principal = await maybe_bootstrap_system_admin(
                    principal,
                    self._role_binding_store,
                    bootstrap_email=self._bootstrap_admin_email,
                )
            # Stream R — merge tenant-scope role-binding roles so the
            # authorization layer (auth.rbac) sees roles granted via the
            # invite flow / role_bindings API, not only the JWT claim.
            principal = await resolve_tenant_roles(principal, self._role_binding_store)
            return await self._call_with_principal(request, call_next, principal)

        if self._mtls_verifier is not None:
            xfcc = request.headers.get(self._mtls_header)
            if xfcc:
                try:
                    principal = self._mtls_verifier.verify(xfcc)
                except AuthError as exc:
                    return await self._reject(request, exc)
                # mTLS principals are internal service identities — they
                # carry ``allowed_tenants=(system_tenant_id,)`` already
                # and do not go through the platform-admin lookup.
                return await self._call_with_principal(request, call_next, principal)

        return await self._reject(request, MissingCredentialsError())

    def _is_exempt(self, path: str) -> bool:
        path_clean = path.rstrip("/")
        return any(
            path_clean == prefix or path_clean.startswith(prefix + "/") for prefix in self._exempt
        )

    async def _call_with_principal(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
        principal: Principal,
    ) -> Response:
        request.state.principal = principal
        # Legacy aliases — pre-C.1 handlers (audit emit, rate-limit key
        # builder, runs.py) read these and continue to work unchanged.
        request.state.tenant_id = principal.tenant_id
        request.state.actor_id = principal.subject_id
        # Stream U (PR E) — block a suspended tenant's members. system_admin's
        # principal.tenant_id is the system tenant, never suspended.
        if self._tenant_status is not None and await self._tenant_status.is_suspended(
            principal.tenant_id
        ):
            return JSONResponse(
                status_code=403,
                content={
                    "success": False,
                    "data": None,
                    "error": {
                        "code": "TENANT_SUSPENDED",
                        "message": "this tenant is suspended",
                    },
                },
            )
        return await call_next(request)

    async def _reject(self, request: Request, error: AuthError) -> JSONResponse:
        # Server-side detail goes to the structured logger; the response
        # only ever carries the public ``code`` / ``public_message``.
        logger.info(
            "auth.reject",
            extra={
                "error_code": error.code,
                "path": request.url.path,
                "method": request.method,
            },
        )
        if self._audit_logger is not None:
            try:
                await emit(
                    self._audit_logger,
                    tenant_id=_UNKNOWN_TENANT,
                    actor_id=_UNKNOWN_ACTOR,
                    action=AuditAction.AUTH_LOGIN_FAILED,
                    resource_type="user",
                    resource_id=error.code,
                    trace_id=current_trace_id_hex(),
                    result=AuditResult.DENIED,
                    reason=error.code,
                    details={"path": request.url.path, "method": request.method},
                )
            except Exception:
                # Audit failures must never block the auth response.
                logger.exception("auth.reject.audit_emit_failed")
        return JSONResponse(
            status_code=error.status,
            content={
                "success": False,
                "data": None,
                "error": {"code": error.code, "message": error.public_message},
            },
            headers={"WWW-Authenticate": 'Bearer realm="helix-agent"'},
        )


def _extract_bearer(request: Request) -> str | None:
    """Return the bearer token from ``Authorization`` or ``None`` if absent/malformed."""
    raw = request.headers.get("Authorization", "")
    if not raw:
        return None
    if not raw.lower().startswith(_BEARER_PREFIX):
        return None
    token = raw[len(_BEARER_PREFIX) :].strip()
    return token or None
