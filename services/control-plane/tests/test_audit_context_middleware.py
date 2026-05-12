"""Tests for :class:`control_plane.middleware.AuditContextMiddleware`."""

from __future__ import annotations

from uuid import UUID

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from starlette.responses import JSONResponse

from control_plane.middleware import AuditContextMiddleware

_DEFAULT_TENANT = UUID("00000000-0000-0000-0000-000000000000")


def _build_probe_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        AuditContextMiddleware,
        default_tenant_id=_DEFAULT_TENANT,
        default_actor_id="anonymous",
    )

    @app.get("/probe")
    async def probe(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "tenant_id": str(request.state.tenant_id),
                "actor_id": request.state.actor_id,
            }
        )

    return app


@pytest.mark.asyncio
async def test_defaults_used_when_headers_absent() -> None:
    app = _build_probe_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/probe")
    body = response.json()
    assert body["tenant_id"] == str(_DEFAULT_TENANT)
    assert body["actor_id"] == "anonymous"


@pytest.mark.asyncio
async def test_headers_take_precedence() -> None:
    app = _build_probe_app()
    tenant = "11111111-1111-1111-1111-111111111111"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/probe",
            headers={"X-Helix-Tenant": tenant, "X-Helix-Actor": "alice"},
        )
    body = response.json()
    assert body["tenant_id"] == tenant
    assert body["actor_id"] == "alice"


@pytest.mark.asyncio
async def test_malformed_tenant_falls_back_to_default() -> None:
    app = _build_probe_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/probe", headers={"X-Helix-Tenant": "not-a-uuid"})
    body = response.json()
    assert body["tenant_id"] == str(_DEFAULT_TENANT)


@pytest.mark.asyncio
async def test_tenant_ctxvar_set_during_handler() -> None:
    """The handler must see the tenant via the helix-common ctxvar API."""
    from helix_agent.common.context import get_current_tenant

    app = FastAPI()
    app.add_middleware(
        AuditContextMiddleware,
        default_tenant_id=_DEFAULT_TENANT,
        default_actor_id="anonymous",
    )

    @app.get("/probe")
    async def probe() -> JSONResponse:
        observed = get_current_tenant()
        return JSONResponse({"observed": str(observed) if observed else None})

    tenant = "22222222-2222-2222-2222-222222222222"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/probe", headers={"X-Helix-Tenant": tenant})
    assert response.json()["observed"] == tenant
