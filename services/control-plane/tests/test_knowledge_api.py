"""Tests for ``/v1/knowledge`` — Stream J.5 knowledge-base + document API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from control_plane.app import create_app
from control_plane.audit import build_default_audit_logger
from control_plane.knowledge.ingestion import KnowledgeIngestionRunner
from control_plane.settings import DEFAULT_DEV_TENANT_ID, Settings
from expert_work.persistence import InMemoryKnowledgeStore
from expert_work.persistence.audit_log import InMemoryAuditLogStore
from orchestrator.llm import FakeEmbedder
from orchestrator.tools import KnowledgeRetriever
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
FullSetup = tuple[AsyncClient, KnowledgeIngestionRunner, InMemoryKnowledgeStore]


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


class _FakeEmbeddingConfig:
    """Mutable stand-in for ``PlatformEmbeddingConfigService`` — tests flip
    ``pair`` to simulate a platform embedding-model change."""

    def __init__(self, pair: tuple[str, str] | None) -> None:
        self.pair = pair

    async def effective_embedding_config(self) -> tuple[str, str] | None:
        return self.pair


ReindexSetup = tuple[AsyncClient, KnowledgeIngestionRunner, _FakeEmbeddingConfig]


@pytest.fixture
async def reindex_setup() -> AsyncIterator[ReindexSetup]:
    """``full_setup`` plus a mutable fake embedding-config service so tests can
    drive the ``needs_reindex`` / re-index flow deterministically."""
    store = InMemoryKnowledgeStore()
    embedder = FakeEmbedder()
    runner = KnowledgeIngestionRunner(store=store, embedder=embedder)
    config = _FakeEmbeddingConfig(("qwen", "text-embedding-v4"))
    app = create_app(
        settings=_settings(),
        knowledge_repo=store,
        knowledge_ingestion_runner=runner,
        audit_logger=build_default_audit_logger(InMemoryAuditLogStore()),
        jwt_verifier=build_test_jwt_verifier(),
    )
    app.state.knowledge_retriever = KnowledgeRetriever(store=store, embedder=embedder)
    app.state.platform_embedding_config_service = config
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://cp.test", headers=_headers()
    ) as client:
        yield client, runner, config


@pytest.fixture
async def full_setup() -> AsyncIterator[FullSetup]:
    """Like ``setup`` but also attaches a real :class:`KnowledgeRetriever`
    (the retrieval-test endpoint reads it off ``app.state``) and exposes the
    store so tests can assert/seed directly."""
    store = InMemoryKnowledgeStore()
    embedder = FakeEmbedder()
    runner = KnowledgeIngestionRunner(store=store, embedder=embedder)
    app = create_app(
        settings=_settings(),
        knowledge_repo=store,
        knowledge_ingestion_runner=runner,
        audit_logger=build_default_audit_logger(InMemoryAuditLogStore()),
        jwt_verifier=build_test_jwt_verifier(),
    )
    app.state.knowledge_retriever = KnowledgeRetriever(store=store, embedder=embedder)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://cp.test", headers=_headers()
    ) as client:
        yield client, runner, store


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


# ---------------------------------------------------------------------------
# single-base view + stats + edit (commercial uplift)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_base_with_config_and_get_single(setup: Setup) -> None:
    client, runner = setup
    created = await client.post(
        "/v1/knowledge/bases",
        json={
            "name": "kb",
            "description": "HR docs",
            "retrieval_top_k": 8,
            "retrieval_score_threshold": 0.4,
            "retrieval_method": "vector",
            "rerank_enabled": False,
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["description"] == "HR docs"
    assert body["retrieval_config"] == {
        "top_k": 8,
        "score_threshold": 0.4,
        "method": "vector",
        "rerank_enabled": False,
    }
    assert body["stats"] == {"document_count": 0, "chunk_count": 0}

    # Upload a doc so stats are non-zero, then GET the single base.
    await client.post(
        "/v1/knowledge/bases/kb/documents",
        files={"file": ("h.md", b"# H\n\nThe deductible is 500.", "text/markdown")},
    )
    await runner.drain()
    single = await client.get("/v1/knowledge/bases/kb")
    assert single.status_code == 200
    sbody = single.json()
    assert sbody["stats"]["document_count"] == 1
    assert sbody["stats"]["chunk_count"] >= 1


@pytest.mark.asyncio
async def test_get_missing_base_returns_404(setup: Setup) -> None:
    client, _ = setup
    assert (await client.get("/v1/knowledge/bases/ghost")).status_code == 404


@pytest.mark.asyncio
async def test_list_bases_includes_stats(setup: Setup) -> None:
    client, runner = setup
    await client.post("/v1/knowledge/bases", json={"name": "kb"})
    await client.post(
        "/v1/knowledge/bases/kb/documents",
        files={"file": ("d.md", b"# D\n\nbody text here.", "text/markdown")},
    )
    await runner.drain()
    listed = (await client.get("/v1/knowledge/bases")).json()["bases"]
    assert listed[0]["stats"]["document_count"] == 1


@pytest.mark.asyncio
async def test_patch_base_updates_config(setup: Setup) -> None:
    client, _ = setup
    await client.post("/v1/knowledge/bases", json={"name": "kb", "description": "orig"})
    patched = await client.patch(
        "/v1/knowledge/bases/kb",
        json={"retrieval_top_k": 12, "retrieval_method": "keyword"},
    )
    assert patched.status_code == 200
    cfg = patched.json()["retrieval_config"]
    assert cfg["top_k"] == 12
    assert cfg["method"] == "keyword"
    # Omitted description is preserved.
    assert patched.json()["description"] == "orig"


@pytest.mark.asyncio
async def test_patch_base_clear_vs_omit_nullable(setup: Setup) -> None:
    client, _ = setup
    await client.post(
        "/v1/knowledge/bases",
        json={"name": "kb", "description": "orig", "retrieval_score_threshold": 0.5},
    )
    # Explicit null clears the threshold; description omitted → unchanged.
    patched = await client.patch("/v1/knowledge/bases/kb", json={"retrieval_score_threshold": None})
    assert patched.status_code == 200
    assert patched.json()["retrieval_config"]["score_threshold"] is None
    assert patched.json()["description"] == "orig"


@pytest.mark.asyncio
async def test_patch_base_rejects_overlap_not_below_max(setup: Setup) -> None:
    client, _ = setup
    await client.post(
        "/v1/knowledge/bases",
        json={"name": "kb", "chunk_max_tokens": 200, "chunk_overlap_tokens": 16},
    )
    resp = await client.patch("/v1/knowledge/bases/kb", json={"chunk_overlap_tokens": 500})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_patch_base_rejects_bad_method(setup: Setup) -> None:
    client, _ = setup
    await client.post("/v1/knowledge/bases", json={"name": "kb"})
    resp = await client.patch("/v1/knowledge/bases/kb", json={"retrieval_method": "magic"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_patch_missing_base_returns_404(setup: Setup) -> None:
    client, _ = setup
    assert (await client.patch("/v1/knowledge/bases/ghost", json={})).status_code == 404


# ---------------------------------------------------------------------------
# chunk preview + retrieval test (commercial uplift)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_chunks_preview(full_setup: FullSetup) -> None:
    client, runner, _ = full_setup
    await client.post("/v1/knowledge/bases", json={"name": "kb"})
    await client.post(
        "/v1/knowledge/bases/kb/documents",
        files={"file": ("h.md", b"# Handbook\n\nThe deductible is 500 dollars.", "text/markdown")},
    )
    await runner.drain()
    doc_id = (await client.get("/v1/knowledge/bases/kb/documents")).json()["documents"][0]["id"]
    resp = await client.get(f"/v1/knowledge/bases/kb/documents/{doc_id}/chunks")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert body["chunks"][0]["chunk_index"] == 0
    assert "content" in body["chunks"][0]
    # The (large) embedding is never returned in a preview.
    assert "embedding" not in body["chunks"][0]


@pytest.mark.asyncio
async def test_list_chunks_unknown_document_404(full_setup: FullSetup) -> None:
    client, _, _ = full_setup
    await client.post("/v1/knowledge/bases", json={"name": "kb"})
    resp = await client.get(
        "/v1/knowledge/bases/kb/documents/00000000-0000-0000-0000-000000000000/chunks"
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_retrieval_test_returns_scored_results(full_setup: FullSetup) -> None:
    client, runner, _ = full_setup
    await client.post("/v1/knowledge/bases", json={"name": "kb"})
    await client.post(
        "/v1/knowledge/bases/kb/documents",
        files={"file": ("h.md", b"# H\n\nThe deductible is 500 dollars.", "text/markdown")},
    )
    await runner.drain()
    resp = await client.post("/v1/knowledge/bases/kb/test", json={"query": "deductible"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "deductible"
    assert body["count"] >= 1
    first = body["results"][0]
    assert set(first) >= {"content", "source", "filename", "chunk_index", "score", "recall_source"}
    assert first["source"].startswith("h.md#")


@pytest.mark.asyncio
async def test_retrieval_test_missing_base_404(full_setup: FullSetup) -> None:
    client, _, _ = full_setup
    resp = await client.post("/v1/knowledge/bases/ghost/test", json={"query": "x"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_retrieval_test_503_when_retriever_unavailable(setup: Setup) -> None:
    # The plain ``setup`` fixture does not attach a retriever (app.state value
    # is None), so the endpoint reports the embedding-unconfigured 503.
    client, _ = setup
    await client.post("/v1/knowledge/bases", json={"name": "kb"})
    resp = await client.post("/v1/knowledge/bases/kb/test", json={"query": "x"})
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# embedding pin + needs_reindex + re-index (commercial uplift)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_pins_embedding_model(reindex_setup: ReindexSetup) -> None:
    client, _, _ = reindex_setup
    created = (await client.post("/v1/knowledge/bases", json={"name": "kb"})).json()
    assert created["embedding_provider"] == "qwen"
    assert created["embedding_model"] == "text-embedding-v4"
    assert created["needs_reindex"] is False


@pytest.mark.asyncio
async def test_needs_reindex_flips_on_model_change(reindex_setup: ReindexSetup) -> None:
    client, _, config = reindex_setup
    await client.post("/v1/knowledge/bases", json={"name": "kb"})
    # Platform admin swaps the embedding model.
    config.pair = ("qwen", "text-embedding-v5")
    single = (await client.get("/v1/knowledge/bases/kb")).json()
    assert single["needs_reindex"] is True


@pytest.mark.asyncio
async def test_reindex_reembeds_and_restamps(reindex_setup: ReindexSetup) -> None:
    client, runner, config = reindex_setup
    await client.post("/v1/knowledge/bases", json={"name": "kb"})
    await client.post(
        "/v1/knowledge/bases/kb/documents",
        files={"file": ("h.md", b"# H\n\nThe deductible is 500 dollars.", "text/markdown")},
    )
    await runner.drain()
    # Swap the model → base is now stale.
    config.pair = ("qwen", "text-embedding-v5")
    assert (await client.get("/v1/knowledge/bases/kb")).json()["needs_reindex"] is True

    accepted = await client.post("/v1/knowledge/bases/kb/reindex")
    assert accepted.status_code == 202
    await runner.drain()

    refreshed = (await client.get("/v1/knowledge/bases/kb")).json()
    assert refreshed["embedding_model"] == "text-embedding-v5"
    assert refreshed["needs_reindex"] is False
    assert refreshed["reindexing"] is False


@pytest.mark.asyncio
async def test_reindex_503_when_embedding_unconfigured(reindex_setup: ReindexSetup) -> None:
    client, _, config = reindex_setup
    await client.post("/v1/knowledge/bases", json={"name": "kb"})
    config.pair = None  # platform embedding unconfigured
    resp = await client.post("/v1/knowledge/bases/kb/reindex")
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_reindex_missing_base_404(reindex_setup: ReindexSetup) -> None:
    client, _, _ = reindex_setup
    assert (await client.post("/v1/knowledge/bases/ghost/reindex")).status_code == 404


# ---------------------------------------------------------------------------
# document re-ingest (durability)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reingest_redrives_document(full_setup: FullSetup) -> None:
    client, runner, _ = full_setup
    await client.post("/v1/knowledge/bases", json={"name": "kb"})
    await client.post(
        "/v1/knowledge/bases/kb/documents",
        files={"file": ("h.md", b"# H\n\nThe deductible is 500 dollars.", "text/markdown")},
    )
    await runner.drain()
    doc_id = (await client.get("/v1/knowledge/bases/kb/documents")).json()["documents"][0]["id"]

    resp = await client.post(f"/v1/knowledge/bases/kb/documents/{doc_id}/reingest")
    assert resp.status_code == 202
    assert resp.json()["status"] == "pending"
    await runner.drain()
    refreshed = (await client.get("/v1/knowledge/bases/kb/documents")).json()["documents"][0]
    assert refreshed["status"] == "ready"


@pytest.mark.asyncio
async def test_reingest_without_bytes_returns_409(full_setup: FullSetup) -> None:
    client, _, store = full_setup
    await client.post("/v1/knowledge/bases", json={"name": "kb"})
    base = (await client.get("/v1/knowledge/bases/kb")).json()
    # A legacy document with no retained bytes (seeded straight on the store).
    doc = await store.upsert_document(
        tenant_id=_TENANT, kb_id=UUID(base["id"]), filename="legacy.md"
    )
    resp = await client.post(f"/v1/knowledge/bases/kb/documents/{doc.id}/reingest")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_reingest_missing_document_404(full_setup: FullSetup) -> None:
    client, _, _ = full_setup
    await client.post("/v1/knowledge/bases", json={"name": "kb"})
    resp = await client.post(
        "/v1/knowledge/bases/kb/documents/00000000-0000-0000-0000-000000000000/reingest"
    )
    assert resp.status_code == 404
