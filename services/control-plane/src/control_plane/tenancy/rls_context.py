"""RLS context projection — Stream C.4.

Reads :attr:`request.state.principal` (populated by
:class:`control_plane.auth.AuthMiddleware`) and copies the tenant id
into :data:`expert_work.persistence.rls.current_tenant_id_var` for the
lifetime of the request. The persistence-layer RLS sessionmaker reads
that ContextVar on every transaction begin and emits ``SET LOCAL
app.tenant_id`` so Postgres' policies isolate the response set.

When no principal is attached (auth-exempt paths such as ``/healthz``
and ``/metrics``) the ContextVar stays at its default ``None`` —
fail-closed: any SQL that *would* touch a tenant-scoped table from an
exempt path simply sees zero rows, never another tenant's data.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from expert_work.persistence.rls import current_tenant_id_var
from expert_work.protocol import Principal

logger = logging.getLogger("expert_work.control_plane.rls_context")


class RLSContextMiddleware(BaseHTTPMiddleware):
    """Project ``request.state.principal.tenant_id`` into the RLS ContextVar."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        principal: Principal | None = getattr(request.state, "principal", None)
        if principal is None:
            return await call_next(request)

        token = current_tenant_id_var.set(principal.tenant_id)
        try:
            return await call_next(request)
        finally:
            current_tenant_id_var.reset(token)
