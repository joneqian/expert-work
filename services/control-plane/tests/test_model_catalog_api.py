"""GET /v1/model-catalog — Stream S PR B (Mini-ADR S-4)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from control_plane.api.model_catalog import build_model_catalog_router
from control_plane.platform_secrets import PlatformSecretsService
from control_plane.settings import Settings
from helix_agent.persistence.platform_secrets import InMemoryPlatformSecretStore


class _FakeProviders:
    def __init__(self, enabled: set[str]) -> None:
        self._enabled = enabled

    async def configured_enabled_providers(self) -> set[str]:
        return self._enabled


def _client(enabled: set[str]) -> TestClient:
    app = FastAPI()
    app.state.model_catalog_providers = _FakeProviders(enabled)
    app.include_router(build_model_catalog_router())
    return TestClient(app)


def test_lists_only_configured_enabled_providers_with_models() -> None:
    resp = _client({"deepseek"}).get("/v1/model-catalog")
    assert resp.status_code == 200
    data = resp.json()["data"]
    provs = {row["provider"] for row in data["providers"]}
    assert provs == {"deepseek"}
    ds = next(r for r in data["providers"] if r["provider"] == "deepseek")
    names = {m["name"]: m for m in ds["models"]}
    assert "deepseek-chat" in names
    assert names["deepseek-chat"]["vision"] is False


def test_empty_when_no_provider_configured() -> None:
    resp = _client(set()).get("/v1/model-catalog")
    assert resp.json()["data"]["providers"] == []


# ---------------------------------------------------------------------------
# PlatformConfiguredProviders adapter — integration against InMemoryStore
# ---------------------------------------------------------------------------


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "env": "dev",
        "auth_mode": "dev",
        "db_dsn": "postgresql+asyncpg://test@localhost/test",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_platform_configured_providers_db_enabled() -> None:
    """PlatformConfiguredProviders returns DB-enabled providers."""
    from control_plane.api.model_catalog import PlatformConfiguredProviders

    store = InMemoryPlatformSecretStore()
    await store.upsert_provider(
        provider="anthropic", secret_ref="kms://test/anthropic", enabled=True, actor_id="a"
    )
    svc = PlatformSecretsService(store=store, settings=_settings())
    adapter = PlatformConfiguredProviders(svc)
    result = await adapter.configured_enabled_providers()
    assert "anthropic" in result


@pytest.mark.asyncio
async def test_platform_configured_providers_disabled_excluded() -> None:
    """PlatformConfiguredProviders excludes disabled DB rows."""
    from control_plane.api.model_catalog import PlatformConfiguredProviders

    store = InMemoryPlatformSecretStore()
    await store.upsert_provider(
        provider="openai", secret_ref="kms://test/openai", enabled=False, actor_id="a"
    )
    svc = PlatformSecretsService(store=store, settings=_settings())
    adapter = PlatformConfiguredProviders(svc)
    result = await adapter.configured_enabled_providers()
    assert "openai" not in result


@pytest.mark.asyncio
async def test_platform_configured_providers_env_seed() -> None:
    """PlatformConfiguredProviders includes env-seeded providers."""
    from control_plane.api.model_catalog import PlatformConfiguredProviders

    settings = _settings(
        supported_providers=["deepseek"],
        platform_provider_credentials={"deepseek": "secret://env-deepseek"},
    )
    store = InMemoryPlatformSecretStore()
    svc = PlatformSecretsService(store=store, settings=settings)
    adapter = PlatformConfiguredProviders(svc)
    result = await adapter.configured_enabled_providers()
    assert "deepseek" in result
