"""Tests for ``/v1/sessions/{thread_id}/uploads`` — Stream J.6 image upload."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from control_plane.app import create_app
from control_plane.settings import DEFAULT_DEV_TENANT_ID, Settings
from helix_agent.runtime.storage import InMemoryObjectStore
from tests.auth_fixtures import (
    TEST_AUDIENCE,
    TEST_ISSUER,
    build_test_jwt_verifier,
    make_test_jwt,
)

_TENANT = DEFAULT_DEV_TENANT_ID


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "env": "dev",
        "auth_mode": "dev",
        "rate_limit_burst": 10_000,
        "rate_limit_per_second": 10_000.0,
        "oidc_issuer": TEST_ISSUER,
        "oidc_audience": [TEST_AUDIENCE],
        "multimodal_max_image_bytes": 1024,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {make_test_jwt(tenant_id=_TENANT, subject='user-a')}"}


Setup = tuple[AsyncClient, UUID, InMemoryObjectStore]


@pytest.fixture
async def setup() -> AsyncIterator[Setup]:
    """A booted app with an in-memory object store + a pre-seeded thread."""
    object_store = InMemoryObjectStore()
    app = create_app(
        settings=_settings(),
        jwt_verifier=build_test_jwt_verifier(),
        enable_reaper=False,
    )
    app.state.object_store = object_store
    thread_id = uuid4()
    await app.state.thread_meta_repo.create(
        thread_id=thread_id, tenant_id=_TENANT, created_by="user-a"
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://cp.test", headers=_headers()
    ) as client:
        yield client, thread_id, object_store


@pytest.mark.asyncio
async def test_upload_image_returns_image_ref_and_persists_bytes(setup: Setup) -> None:
    client, thread_id, store = setup
    response = await client.post(
        f"/v1/sessions/{thread_id}/uploads",
        files={"file": ("photo.png", b"PNGBYTES", "image/png")},
    )

    assert response.status_code == 201
    image_ref = response.json()["image_ref"]
    assert image_ref.startswith(f"helix://image/{_TENANT}/{thread_id}/")
    assert image_ref.endswith(".png")

    # The bytes landed under the ADR-0004 prefix.
    keys = await store.list_prefix(f"{_TENANT}/uploads/{thread_id}/")
    assert len(keys) == 1
    assert await store.get(keys[0]) == b"PNGBYTES"


@pytest.mark.asyncio
async def test_upload_picks_jpg_extension_for_jpeg(setup: Setup) -> None:
    client, thread_id, _ = setup
    response = await client.post(
        f"/v1/sessions/{thread_id}/uploads",
        files={"file": ("photo.jpeg", b"JPEGBYTES", "image/jpeg")},
    )
    assert response.status_code == 201
    assert response.json()["image_ref"].endswith(".jpg")


@pytest.mark.asyncio
async def test_upload_rejects_unsupported_content_type(setup: Setup) -> None:
    client, thread_id, _ = setup
    response = await client.post(
        f"/v1/sessions/{thread_id}/uploads",
        files={"file": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert response.status_code == 400
    assert "unsupported image content type" in response.json()["detail"]


@pytest.mark.asyncio
async def test_upload_rejects_oversize(setup: Setup) -> None:
    client, thread_id, _ = setup
    too_big = b"x" * (1024 + 1)
    response = await client.post(
        f"/v1/sessions/{thread_id}/uploads",
        files={"file": ("big.png", too_big, "image/png")},
    )
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_upload_rejects_empty_file(setup: Setup) -> None:
    client, thread_id, _ = setup
    response = await client.post(
        f"/v1/sessions/{thread_id}/uploads",
        files={"file": ("empty.png", b"", "image/png")},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_upload_404_for_unknown_thread(setup: Setup) -> None:
    client, _, _ = setup
    response = await client.post(
        f"/v1/sessions/{uuid4()}/uploads",
        files={"file": ("photo.png", b"PNGBYTES", "image/png")},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_upload_503_when_no_object_store_configured() -> None:
    """Without ``app.state.object_store`` set (lifespan never ran), uploads
    fail loud with 503."""
    app = create_app(
        settings=_settings(),
        jwt_verifier=build_test_jwt_verifier(),
        enable_reaper=False,
    )
    thread_id = uuid4()
    await app.state.thread_meta_repo.create(
        thread_id=thread_id, tenant_id=_TENANT, created_by="user-a"
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://cp.test", headers=_headers()
    ) as client:
        response = await client.post(
            f"/v1/sessions/{thread_id}/uploads",
            files={"file": ("photo.png", b"PNGBYTES", "image/png")},
        )
    assert response.status_code == 503
