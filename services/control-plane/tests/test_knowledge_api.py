"""Tests for ``/v1/knowledge`` — Stream J.5 knowledge-base + document API."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from control_plane.app import create_app
from control_plane.audit import build_default_audit_logger
from control_plane.knowledge.ingestion import KnowledgeIngestionRunner
from control_plane.settings import DEFAULT_DEV_TENANT_ID, Settings
from helix_agent.persistence import InMemoryKnowledgeStore
from helix_agent.persistence.audit_log import InMemoryAuditLogStore
from orchestrator.llm import FakeEmbedder
from tests.auth_fixtures import TEST_AUDIENCE, TEST_ISSUER, build_test_jwt_verifier, make_test_jwt

_TENANT = DEFAULT_DEV_TENANT_ID


def _settings() -> Settings:
    return Settings(
        env="dev",
        auth_mode="dev",
        rate_limit_burst=10_000,
        rate_limit_per_second=10_000.0,
        oidc_issuer=TEST_ISSUER,
        oidc_audience=[TEST_AUDIENCE],
    )


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {make_test_jwt(tenant_id=_TENANT, subject='user-a')}"}


Setup = tuple[AsyncClient, KnowledgeIngestionRunner]


@pytest.fixture
async def setup() -> AsyncIterator[Setup]:
    store = InMemoryKnowledgeStore()
    runner = KnowledgeIngestionRunner(store=store, embedder=FakeEmbedder())
    app = create_app(
        settings=_settings(),
        knowledge_repo=store,
        knowledge_ingestion_runner=runner,
        audit_logger=build_default_audit_logger(InMemoryAuditLogStore()),
        jwt_verifier=build_test_jwt_verifier(),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://cp.test", headers=_headers()
    ) as client:
        yield client, runner


# ---------------------------------------------------------------------------
# knowledge bases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_and_list_base(setup: Setup) -> None:
    client, _ = setup
    created = await client.post("/v1/knowledge/bases", json={"name": "hr-policies"})
    assert created.status_code == 201
    assert created.json()["name"] == "hr-policies"

    listed = await client.get("/v1/knowledge/bases")
    assert listed.status_code == 200
    assert [b["name"] for b in listed.json()["bases"]] == ["hr-policies"]


@pytest.mark.asyncio
async def test_create_base_duplicate_returns_409(setup: Setup) -> None:
    client, _ = setup
    await client.post("/v1/knowledge/bases", json={"name": "kb"})
    again = await client.post("/v1/knowledge/bases", json={"name": "kb"})
    assert again.status_code == 409


@pytest.mark.asyncio
async def test_create_base_with_custom_chunk_params(setup: Setup) -> None:
    client, _ = setup
    resp = await client.post(
        "/v1/knowledge/bases",
        json={"name": "tuned", "chunk_max_tokens": 256, "chunk_overlap_tokens": 16},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["chunk_max_tokens"] == 256
    assert body["chunk_overlap_tokens"] == 16


@pytest.mark.asyncio
async def test_create_base_rejects_overlap_not_below_max(setup: Setup) -> None:
    client, _ = setup
    resp = await client.post(
        "/v1/knowledge/bases",
        json={"name": "bad", "chunk_max_tokens": 100, "chunk_overlap_tokens": 100},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_delete_base(setup: Setup) -> None:
    client, _ = setup
    await client.post("/v1/knowledge/bases", json={"name": "kb"})
    deleted = await client.delete("/v1/knowledge/bases/kb")
    assert deleted.status_code == 204
    listed = await client.get("/v1/knowledge/bases")
    assert listed.json()["bases"] == []


@pytest.mark.asyncio
async def test_delete_missing_base_returns_404(setup: Setup) -> None:
    client, _ = setup
    resp = await client.delete("/v1/knowledge/bases/ghost")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# documents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_document_ingests_to_ready(setup: Setup) -> None:
    client, runner = setup
    await client.post("/v1/knowledge/bases", json={"name": "kb"})

    uploaded = await client.post(
        "/v1/knowledge/bases/kb/documents",
        files={"file": ("handbook.md", b"# Handbook\n\nThe deductible is 500.", "text/markdown")},
    )
    assert uploaded.status_code == 202
    assert uploaded.json()["status"] == "pending"

    # Ingestion runs in the background — wait for it, then poll the list.
    await runner.drain()
    documents = (await client.get("/v1/knowledge/bases/kb/documents")).json()["documents"]
    assert len(documents) == 1
    assert documents[0]["filename"] == "handbook.md"
    assert documents[0]["status"] == "ready"
    assert documents[0]["chunk_count"] >= 1


@pytest.mark.asyncio
async def test_upload_to_missing_base_returns_404(setup: Setup) -> None:
    client, _ = setup
    resp = await client.post(
        "/v1/knowledge/bases/ghost/documents",
        files={"file": ("x.md", b"body", "text/markdown")},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_upload_unsupported_extension_returns_400(setup: Setup) -> None:
    client, _ = setup
    await client.post("/v1/knowledge/bases", json={"name": "kb"})
    resp = await client.post(
        "/v1/knowledge/bases/kb/documents",
        files={"file": ("data.xyz", b"body", "application/octet-stream")},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_delete_document(setup: Setup) -> None:
    client, runner = setup
    await client.post("/v1/knowledge/bases", json={"name": "kb"})
    await client.post(
        "/v1/knowledge/bases/kb/documents",
        files={"file": ("doc.md", b"# Doc\n\nbody.", "text/markdown")},
    )
    await runner.drain()
    documents = (await client.get("/v1/knowledge/bases/kb/documents")).json()["documents"]
    document_id = documents[0]["id"]

    deleted = await client.delete(f"/v1/knowledge/bases/kb/documents/{document_id}")
    assert deleted.status_code == 204
    remaining = (await client.get("/v1/knowledge/bases/kb/documents")).json()["documents"]
    assert remaining == []


@pytest.mark.asyncio
async def test_delete_missing_document_returns_404(setup: Setup) -> None:
    client, _ = setup
    await client.post("/v1/knowledge/bases", json={"name": "kb"})
    resp = await client.delete(
        "/v1/knowledge/bases/kb/documents/00000000-0000-0000-0000-000000000000"
    )
    assert resp.status_code == 404
