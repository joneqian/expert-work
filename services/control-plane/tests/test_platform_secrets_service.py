"""Tests for :class:`PlatformSecretsService` merge logic — Stream P (P-7/P-9)."""

from __future__ import annotations

import pytest

from control_plane.platform_secrets import PlatformSecretsService
from control_plane.settings import Settings
from helix_agent.persistence.platform_secrets import InMemoryPlatformSecretStore


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "env": "dev",
        "auth_mode": "dev",
        "db_dsn": "postgresql+asyncpg://test@localhost/test",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_db_enabled_row_appears_in_merged_view() -> None:
    store = InMemoryPlatformSecretStore()
    await store.upsert_provider(
        provider="anthropic", secret_ref="kms://db/anthropic", enabled=True, actor_id="a"
    )
    svc = PlatformSecretsService(store=store, settings=_settings())

    merged = await svc.effective_provider_credentials()
    assert merged.get("anthropic") == "kms://db/anthropic"


@pytest.mark.asyncio
async def test_db_overrides_env_per_key() -> None:
    settings = _settings(
        supported_providers=["openai"],
        platform_provider_credentials={"openai": "secret://env-openai"},
    )
    store = InMemoryPlatformSecretStore()
    svc = PlatformSecretsService(store=store, settings=settings)
    # Env seed visible first.
    assert (await svc.effective_provider_credentials()).get("openai") == "secret://env-openai"

    # A DB row overrides the env ref; invalidate to bypass the TTL cache.
    await store.upsert_provider(
        provider="openai", secret_ref="kms://db-openai", enabled=True, actor_id="a"
    )
    svc.invalidate()
    assert (await svc.effective_provider_credentials()).get("openai") == "kms://db-openai"


@pytest.mark.asyncio
async def test_disabled_db_row_suppresses_env_seed() -> None:
    settings = _settings(
        supported_providers=["openai"],
        platform_provider_credentials={"openai": "secret://env-openai"},
    )
    store = InMemoryPlatformSecretStore()
    await store.upsert_provider(
        provider="openai", secret_ref="secret://ignored", enabled=False, actor_id="a"
    )
    svc = PlatformSecretsService(store=store, settings=settings)
    # Disabled DB row wins → provider suppressed even though env seeds it (P-12).
    assert "openai" not in await svc.effective_provider_credentials()


@pytest.mark.asyncio
async def test_tool_merge_independent() -> None:
    store = InMemoryPlatformSecretStore()
    await store.upsert_tool(
        tool="web_search", secret_ref="kms://db/tavily", enabled=True, actor_id="a"
    )
    svc = PlatformSecretsService(store=store, settings=_settings())
    assert (await svc.effective_tool_credentials()).get("web_search") == "kms://db/tavily"
    assert await svc.effective_provider_credentials() == {}
