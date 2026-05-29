"""HTTP tests for the Stream O Mini-ADR O-13 Credentials-panel endpoints:

* ``GET  /v1/tenants/{tid}/config/credentials``      — composite view
* ``POST /v1/tenants/{tid}/config/credentials-mode/dry-run`` — switch preview
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from control_plane.app import create_app
from control_plane.settings import DEFAULT_DEV_TENANT_ID, Settings
from helix_agent.protocol import AgentSpec
from tests.auth_fixtures import TEST_AUDIENCE, TEST_ISSUER, build_test_jwt_verifier, make_test_jwt

_TENANT = DEFAULT_DEV_TENANT_ID
_SHA = "a" * 64


def _catalog_settings() -> Settings:
    return Settings(
        env="dev",
        auth_mode="dev",
        rate_limit_burst=10_000,
        rate_limit_per_second=10_000.0,
        tenant_rate_limit_capacity=10_000,
        tenant_rate_limit_refill_per_sec=10_000.0,
        oidc_issuer=TEST_ISSUER,
        oidc_audience=[TEST_AUDIENCE],
        supported_providers=["anthropic", "openai", "qwen"],
        platform_provider_credentials={
            "anthropic": "secret://plat/anthropic",
            "openai": "secret://plat/openai",
            "qwen": "secret://plat/qwen",
        },
        supported_tools=["web_search"],
        platform_tool_credentials={"web_search": "secret://plat/tavily"},
    )


@pytest.fixture
async def app_client() -> AsyncIterator[tuple[object, AsyncClient]]:
    app = create_app(
        settings=_catalog_settings(),
        jwt_verifier=build_test_jwt_verifier(),
        enable_reaper=False,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://control-plane.test") as c:
        yield app, c


def _token(tenant: UUID = _TENANT) -> str:
    return make_test_jwt(tenant_id=tenant, subject="admin-user", roles=("admin",))


def _agent_spec(*, model_provider: str, long_term: bool = False) -> AgentSpec:
    body: dict[str, object] = {
        "tenant_config": {},
        "model": {"provider": model_provider, "name": "m"},
        "system_prompt": {"template": "x"},
        "sandbox": {
            "resources": {"cpu": "1", "memory": "1Gi"},
            "network": {"egress": "proxy", "allowlist": ["a.com"]},
            "filesystem": {},
        },
    }
    if long_term:
        body["memory"] = {"long_term": {}}
    return AgentSpec.model_validate(
        {
            "apiVersion": "helix.io/v1",
            "kind": "Agent",
            "metadata": {"name": "a", "version": "1.0.0", "tenant": "t"},
            "spec": body,
        }
    )


async def _seed_agent(app: object, spec: AgentSpec) -> None:
    repo = app.state.agent_spec_repo  # type: ignore[attr-defined]
    await repo.create(tenant_id=_TENANT, spec=spec, spec_sha256=_SHA, created_by="seed")


# ─── credentials view ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_view_unconfigured_tenant_shows_catalog_platform_mode(
    app_client: tuple[object, AsyncClient],
) -> None:
    _, client = app_client
    resp = await client.get(
        f"/v1/tenants/{_TENANT}/config/credentials",
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["mode"] == "platform"
    provs = {p["provider"]: p for p in data["providers"]}
    assert set(provs) == {"anthropic", "openai", "qwen"}
    assert provs["anthropic"]["platform_configured"] is True
    assert provs["anthropic"]["tenant_secret_ref"] is None
    assert provs["anthropic"]["used_by_agents"] == 0
    tools = {tl["tool"]: tl for tl in data["tools"]}
    assert tools["web_search"]["platform_configured"] is True


@pytest.mark.asyncio
async def test_view_reflects_tenant_secret_ref(
    app_client: tuple[object, AsyncClient],
) -> None:
    _, client = app_client
    await client.put(
        f"/v1/tenants/{_TENANT}/config",
        headers={"Authorization": f"Bearer {_token()}"},
        json={
            "display_name": "ACME",
            "model_credentials_ref": {"openai": "kms://acme/openai"},
            "tool_credentials": {"web_search": "kms://acme/tavily"},
        },
    )
    resp = await client.get(
        f"/v1/tenants/{_TENANT}/config/credentials",
        headers={"Authorization": f"Bearer {_token()}"},
    )
    data = resp.json()["data"]
    provs = {p["provider"]: p for p in data["providers"]}
    assert provs["openai"]["tenant_secret_ref"] == "kms://acme/openai"
    assert provs["anthropic"]["tenant_secret_ref"] is None
    tools = {tl["tool"]: tl for tl in data["tools"]}
    assert tools["web_search"]["tenant_secret_ref"] == "kms://acme/tavily"


@pytest.mark.asyncio
async def test_view_used_by_counts_include_embedding(
    app_client: tuple[object, AsyncClient],
) -> None:
    app, client = app_client
    # One agent on anthropic with long_term memory → anthropic used_by=1,
    # and the embedding provider (qwen) used_by=1.
    await _seed_agent(app, _agent_spec(model_provider="anthropic", long_term=True))
    resp = await client.get(
        f"/v1/tenants/{_TENANT}/config/credentials",
        headers={"Authorization": f"Bearer {_token()}"},
    )
    provs = {p["provider"]: p for p in resp.json()["data"]["providers"]}
    assert provs["anthropic"]["used_by_agents"] == 1
    assert provs["qwen"]["used_by_agents"] == 1  # embedding_provider default
    assert provs["openai"]["used_by_agents"] == 0


# ─── dry-run ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dry_run_ok_when_no_agents(app_client: tuple[object, AsyncClient]) -> None:
    _, client = app_client
    resp = await client.post(
        f"/v1/tenants/{_TENANT}/config/credentials-mode/dry-run",
        headers={"Authorization": f"Bearer {_token()}"},
        json={"model_credentials_ref": {}, "tool_credentials": {}},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ok"] is True
    assert data["missing_providers"] == []
    assert data["missing_tools"] == []


@pytest.mark.asyncio
async def test_dry_run_reports_missing_provider(
    app_client: tuple[object, AsyncClient],
) -> None:
    app, client = app_client
    await _seed_agent(app, _agent_spec(model_provider="openai"))
    # Propose only anthropic → openai is missing.
    resp = await client.post(
        f"/v1/tenants/{_TENANT}/config/credentials-mode/dry-run",
        headers={"Authorization": f"Bearer {_token()}"},
        json={"model_credentials_ref": {"anthropic": "kms://acme/anthropic"}},
    )
    data = resp.json()["data"]
    assert data["ok"] is False
    assert data["missing_providers"] == ["openai"]


@pytest.mark.asyncio
async def test_dry_run_ok_when_all_covered(
    app_client: tuple[object, AsyncClient],
) -> None:
    app, client = app_client
    await _seed_agent(app, _agent_spec(model_provider="openai"))
    resp = await client.post(
        f"/v1/tenants/{_TENANT}/config/credentials-mode/dry-run",
        headers={"Authorization": f"Bearer {_token()}"},
        json={"model_credentials_ref": {"openai": "kms://acme/openai"}},
    )
    data = resp.json()["data"]
    assert data["ok"] is True
    assert data["missing_providers"] == []
