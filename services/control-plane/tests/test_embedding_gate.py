"""Stream T (PR B) — build-time embedding gate in ``make_agent_builder``.

The dynamic embedder is never ``None`` (it resolves the live config per call),
so the orchestrator's ``embedder is None`` build-time gate can no longer fire.
The "memory.long_term declared but platform embedding unconfigured" check moves
into the control-plane builder, which has the
:class:`PlatformEmbeddingConfigService` in scope. This test pins that gate.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from control_plane.platform_embedding_config import PlatformEmbeddingConfigService
from control_plane.runtime import make_agent_builder
from control_plane.settings import Settings
from helix_agent.persistence.platform_embedding_config import (
    InMemoryPlatformEmbeddingConfigStore,
)
from helix_agent.protocol import AgentSpec
from helix_agent.runtime.secret_store import LocalDevSecretStore
from orchestrator.errors import AgentFactoryError

_ANTHROPIC_KEY_NAME = "anthropic-api-key"

_LONG_TERM_SPEC: dict[str, Any] = {
    "apiVersion": "helix.io/v1",
    "kind": "Agent",
    "metadata": {"name": "mem-agent", "version": "1.0.0", "tenant": "platform-eng"},
    "spec": {
        "tenant_config": {},
        "model": {
            "provider": "anthropic",
            "name": "claude-sonnet-4-6",
            "api_key_ref": f"secret://{_ANTHROPIC_KEY_NAME}",
        },
        "system_prompt": {"template": "you are a test agent"},
        "memory": {"long_term": {"retrieve_top_k": 5, "write_back": True}},
        "sandbox": {
            "resources": {"cpu": "1.0", "memory": "1Gi"},
            "network": {"egress": "proxy", "allowlist": ["api.anthropic.com"]},
            "filesystem": {"readonly_root": True, "writable": ["/workspace"]},
        },
    },
}


def _long_term_spec() -> AgentSpec:
    return AgentSpec.model_validate(deepcopy(_LONG_TERM_SPEC))


def _secret_store() -> LocalDevSecretStore:
    return LocalDevSecretStore.from_mapping({_ANTHROPIC_KEY_NAME: "sk-ant-test"})


def _config_service(store: InMemoryPlatformEmbeddingConfigStore) -> PlatformEmbeddingConfigService:
    # ``Settings()`` default: no embedding key ref and empty supported_providers,
    # so the env-fallback path also yields None — an empty store means the gate
    # must trip.
    return PlatformEmbeddingConfigService(store=store, settings=Settings(), ttl_seconds=0.0)


@pytest.mark.asyncio
async def test_build_raises_when_long_term_and_embedding_unconfigured() -> None:
    store = InMemoryPlatformEmbeddingConfigStore()  # empty → unconfigured
    builder = make_agent_builder(
        _secret_store(),
        InMemorySaver(),
        platform_embedding_config_service=_config_service(store),
    )
    with pytest.raises(AgentFactoryError, match="platform embedding is not configured"):
        await builder(_long_term_spec(), tenant_id=uuid4())


@pytest.mark.asyncio
async def test_build_succeeds_when_embedding_configured() -> None:
    store = InMemoryPlatformEmbeddingConfigStore()
    await store.put(
        embedding_provider="qwen",
        embedding_model="text-embedding-v4",
        rerank_provider=None,
        rerank_model=None,
        updated_by="admin",
    )
    builder = make_agent_builder(
        _secret_store(),
        InMemorySaver(),
        platform_embedding_config_service=_config_service(store),
    )
    # The gate must NOT trip. The build itself may fail downstream (this builder
    # has no MemoryEnv), but it must NOT be the embedding-config gate.
    try:
        await builder(_long_term_spec(), tenant_id=uuid4())
    except AgentFactoryError as exc:
        assert "platform embedding is not configured" not in str(exc)
