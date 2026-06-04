"""Stream T (PR B) — DynamicResolvingEmbedder/Reranker read the live platform
embedding/rerank config at CALL time, so an admin's config change takes effect
without a process restart (Mini-ADR T-3)."""

from uuid import uuid4

import pytest

from control_plane.runtime import DynamicResolvingEmbedder, DynamicResolvingReranker
from helix_agent.common.credentials import CredentialsResolverError
from orchestrator.errors import AgentFactoryError


class _Cfg:
    """Fake config service whose embedding/rerank config can change between calls."""

    def __init__(self) -> None:
        self.embedding: tuple[str, str] | None = ("qwen", "text-embedding-v4")
        self.rerank: tuple[str, str] | None = None

    async def effective_embedding_config(self) -> tuple[str, str] | None:
        return self.embedding

    async def effective_rerank_config(self) -> tuple[str, str] | None:
        return self.rerank


class _Resolver:
    async def resolve_provider(self, *, tenant_id, provider):
        return f"secret://{provider}"


class _SecretStore:
    async def get(self, name):
        return "fake-key"


@pytest.mark.asyncio
async def test_embedder_reads_current_config_each_call(monkeypatch):
    captured: list[str] = []

    class _FakeDelegate:
        def __init__(self, *, client, model):
            captured.append(model)

        async def embed(self, texts, *, tenant_id):
            return [(0.0,) for _ in texts]

    # seam: patch the delegate the dynamic embedder constructs, plus the HTTP
    # client so NO network happens.
    monkeypatch.setattr("control_plane.runtime.OpenAICompatibleEmbedder", _FakeDelegate)
    monkeypatch.setattr("control_plane.runtime.HTTPEmbeddingClient", lambda **kw: object())

    cfg = _Cfg()
    emb = DynamicResolvingEmbedder(
        config_service=cfg, resolver=_Resolver(), secret_store=_SecretStore()
    )
    await emb.embed(["a"], tenant_id=uuid4())
    cfg.embedding = ("glm", "embedding-3")  # admin changes config
    await emb.embed(["b"], tenant_id=uuid4())
    assert captured == ["text-embedding-v4", "embedding-3"]  # second call used NEW model


@pytest.mark.asyncio
async def test_embedder_raises_when_unconfigured():
    cfg = _Cfg()
    cfg.embedding = None
    emb = DynamicResolvingEmbedder(
        config_service=cfg, resolver=_Resolver(), secret_store=_SecretStore()
    )
    with pytest.raises(AgentFactoryError):
        await emb.embed(["a"], tenant_id=uuid4())


@pytest.mark.asyncio
async def test_reranker_degrades_to_identity_when_unconfigured():
    cfg = _Cfg()
    cfg.rerank = None
    rr = DynamicResolvingReranker(
        config_service=cfg, resolver=_Resolver(), secret_store=_SecretStore()
    )
    out = await rr.rerank(query="q", documents=["a", "b", "c"], top_k=2, tenant_id=uuid4())
    assert out == [0, 1]  # identity order, no error


class _RaisingResolver:
    async def resolve_provider(self, *, tenant_id, provider):
        raise CredentialsResolverError(
            "no credential", mode="platform", kind="provider", key=provider
        )


@pytest.mark.asyncio
async def test_reranker_degrades_to_identity_when_credential_missing():
    cfg = _Cfg()
    cfg.rerank = ("qwen", "qwen3-vl-rerank")  # configured, but credential resolve fails
    rr = DynamicResolvingReranker(
        config_service=cfg, resolver=_RaisingResolver(), secret_store=_SecretStore()
    )
    out = await rr.rerank(query="q", documents=["a", "b", "c"], top_k=2, tenant_id=uuid4())
    assert out == [0, 1]  # identity order, no error
