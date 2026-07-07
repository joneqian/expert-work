"""Tests for :class:`control_plane.middleware.AuditContextMiddleware`.

After Stream C.1 :class:`AuditContextMiddleware` no longer reads
``X-Expert-Work-*`` headers — it projects ``request.state.principal`` (set by
:class:`AuthMiddleware`) into both the legacy ``request.state.tenant_id``
alias and the structured-log ctxvar. Tests fabricate a probe app where a
small middleware ahead of ``AuditContextMiddleware`` simulates a
verified principal.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from control_plane.middleware import AuditContextMiddleware
from expert_work.common.context import get_current_tenant
from expert_work.protocol import Principal

_DEFAULT_TENANT = UUID("00000000-0000-0000-0000-000000000000")
_REAL_TENANT = UUID("22222222-2222-2222-2222-222222222222")


class _StubPrincipalMiddleware(BaseHTTPMiddleware):
    """Inject a fixed :class:`Principal` (stands in for AuthMiddleware)."""

    def __init__(self, app: object, *, principal: Principal | None) -> None:  # type: ignore[override]
        super().__init__(app)  # type: ignore[arg-type]
        self._principal = principal

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if self._principal is not None:
            request.state.principal = self._principal
        return await call_next(request)


def _build_probe_app(*, principal: Principal | None) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        AuditContextMiddleware,
        default_tenant_id=_DEFAULT_TENANT,
        default_actor_id="anonymous",
    )
    app.add_middleware(_StubPrincipalMiddleware, principal=principal)

    @app.get("/probe")
    async def probe(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "tenant_id": str(request.state.tenant_id),
                "actor_id": request.state.actor_id,
                "ctxvar_tenant": (str(get_current_tenant()) if get_current_tenant() else None),
            }
        )

    return app


@pytest.mark.asyncio
async def test_falls_back_to_defaults_when_no_principal() -> None:
    """Exempt paths reach AuditContextMiddleware without principal — defaults apply."""
    app = _build_probe_app(principal=None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/probe")
    body = response.json()
    assert body["tenant_id"] == str(_DEFAULT_TENANT)
    assert body["actor_id"] == "anonymous"
    assert body["ctxvar_tenant"] == str(_DEFAULT_TENANT)


@pytest.mark.asyncio
async def test_projects_principal_into_state_and_ctxvar() -> None:
    principal = Principal(
        subject_id="alice",
        subject_type="user",
        tenant_id=_REAL_TENANT,
        roles=("admin",),
    )
    app = _build_probe_app(principal=principal)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/probe")
    body = response.json()
    assert body["tenant_id"] == str(_REAL_TENANT)
    assert body["actor_id"] == "alice"
    assert body["ctxvar_tenant"] == str(_REAL_TENANT)


@pytest.mark.asyncio
async def test_legacy_x_expert_work_tenant_header_no_longer_honoured() -> None:
    """Regression guard: the old dev-mode header trust path is gone."""
    app = _build_probe_app(principal=None)
    transport = ASGITransport(app=app)
    bogus_tenant = "11111111-1111-1111-1111-111111111111"
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/probe", headers={"X-Expert-Work-Tenant": bogus_tenant})
    body = response.json()
    # The header is silently ignored — defaults still win.
    assert body["tenant_id"] == str(_DEFAULT_TENANT)
