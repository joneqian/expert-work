"""Endpoint tests for ``GET /v1/platform/embedding-config`` — Stream T (PR C).

Mirrors ``test_platform_config_api.py``: the full ``create_app`` harness wires
``platform_embedding_config_service`` + ``platform_secrets_service`` onto
``app.state``; a system_admin ``role_binding`` is seeded and the principal is
delivered via a real JWT.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from control_plane.app import create_app
from control_plane.auth import JWTVerifier
from control_plane.settings import Settings
from helix_agent.common.lifecycle import Lifecycle
from helix_agent.protocol import Role
from tests.auth_fixtures import make_test_jwt


async def _seed_admin(app: object) -> UUID:
    sys_admin_id = uuid4()
    await app.state.role_binding_repo.create(  # type: ignore[attr-defined]
        subject_type="user",
        subject_id=sys_admin_id,
        tenant_id=None,
        role=Role.SYSTEM_ADMIN,
        platform_scope=True,
        granted_by="seed",
    )
    return sys_admin_id


def _headers(subject: UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_test_jwt(tenant_id=uuid4(), subject=str(subject))}"}


@pytest.fixture
async def admin_client(
    settings: Settings,
    lifecycle: Lifecycle,
    jwt_verifier: JWTVerifier,
) -> AsyncIterator[tuple[AsyncClient, UUID]]:
    # Configure ``qwen`` so it is "configured" in the platform secrets overlay,
    # and pin the effective embedding/rerank to known catalog models.
    settings = settings.model_copy(
        update={
            "supported_providers": ["qwen"],
            "platform_provider_credentials": {"qwen": "secret://x"},
            "embedding_provider": "qwen",
            "embedding_model": "text-embedding-v4",
            "rerank_provider": "qwen",
            "rerank_model": "qwen3-vl-rerank",
        }
    )
    app = create_app(settings=settings, lifecycle=lifecycle, jwt_verifier=jwt_verifier)
    sys_admin_id = await _seed_admin(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://control-plane.test") as client:
        yield client, sys_admin_id


@pytest.mark.asyncio
async def test_non_admin_forbidden(admin_client: tuple[AsyncClient, UUID]) -> None:
    client, _ = admin_client
    resp = await client.get("/v1/platform/embedding-config", headers=_headers(uuid4()))
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "PLATFORM_SCOPE_FORBIDDEN"


@pytest.mark.asyncio
async def test_get_returns_selection_and_options(
    admin_client: tuple[AsyncClient, UUID],
) -> None:
    client, admin = admin_client
    resp = await client.get("/v1/platform/embedding-config", headers=_headers(admin))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["error"] is None
    data = body["data"]

    # Current effective selection reflects the service (env fallback here).
    assert data["embedding"] == {"provider": "qwen", "model": "text-embedding-v4"}
    assert data["rerank"] == {"provider": "qwen", "model": "qwen3-vl-rerank"}

    # Options: only configured providers' catalog entries surface.
    assert {"provider": "qwen", "model": "text-embedding-v4"} in data["available_embedding"]
    assert {"provider": "qwen", "model": "qwen3-vl-rerank"} in data["available_rerank"]

    # Unconfigured providers (e.g. openai) must NOT contribute options.
    configured = {opt["provider"] for opt in data["available_embedding"]}
    assert configured == {"qwen"}
    assert all(opt["provider"] == "qwen" for opt in data["available_rerank"])
