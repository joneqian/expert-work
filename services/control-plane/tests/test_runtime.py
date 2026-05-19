"""Tests for control-plane ↔ orchestrator runtime glue."""

from __future__ import annotations

import pytest

from control_plane.runtime import resolve_embedder
from helix_agent.runtime.secret_store import parse_secret_ref
from helix_agent.testing import InMemorySecretStore
from orchestrator.llm import OpenAICompatibleEmbedder


@pytest.mark.asyncio
async def test_resolve_embedder_none_ref_returns_none() -> None:
    """No embedding key → no embedder → long-term memory unavailable."""
    embedder = await resolve_embedder(
        api_key_ref=None, model="text-embedding-v4", secret_store=InMemorySecretStore()
    )
    assert embedder is None


@pytest.mark.asyncio
async def test_resolve_embedder_builds_from_secret() -> None:
    store = InMemorySecretStore()
    ref = "secret://helix-agent/dev/embedding"
    await store.put(parse_secret_ref(ref), "sk-embed-test")

    embedder = await resolve_embedder(
        api_key_ref=ref, model="text-embedding-v4", secret_store=store
    )
    assert isinstance(embedder, OpenAICompatibleEmbedder)
    assert embedder.model == "text-embedding-v4"
