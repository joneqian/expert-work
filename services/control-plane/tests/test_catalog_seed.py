"""Unit tests for the MCP catalog env-seed — Stream MCP-OAUTH (OA-5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from control_plane.app import _seed_mcp_catalog
from control_plane.catalog_seed import CatalogSeedError, load_catalog_seed, seed_catalog
from control_plane.settings import Settings
from control_plane.tenant_scope import bypass_rls_session
from expert_work.persistence import InMemoryMcpConnectorCatalogStore

_ENTRY = {
    "name": "linear",
    "display_name": "Linear",
    "transport": "sse",
    "url_template": "https://mcp.linear.app/sse",
    "auth_type": "oauth2",
    "oauth_client_id": "${MCP_OAUTH_LINEAR_CLIENT_ID}",
    "oauth_scopes": "read",
    "required_tier": "pro",
}


# --- load_catalog_seed -----------------------------------------------------


def test_resolves_placeholder_from_env() -> None:
    ready, skipped = load_catalog_seed(
        json.dumps([_ENTRY]), {"MCP_OAUTH_LINEAR_CLIENT_ID": "cid-123"}
    )
    assert skipped == []
    assert len(ready) == 1
    assert ready[0].name == "linear"
    assert ready[0].oauth_client_id == "cid-123"


def test_missing_placeholder_skips_entry() -> None:
    ready, skipped = load_catalog_seed(json.dumps([_ENTRY]), {})
    assert ready == []
    assert skipped == ["linear"]


def test_empty_env_value_treated_as_missing() -> None:
    ready, skipped = load_catalog_seed(json.dumps([_ENTRY]), {"MCP_OAUTH_LINEAR_CLIENT_ID": ""})
    assert ready == []
    assert skipped == ["linear"]


def test_mixed_resolved_and_skipped() -> None:
    other = {**_ENTRY, "name": "notion", "oauth_client_id": "${MCP_OAUTH_NOTION_CLIENT_ID}"}
    ready, skipped = load_catalog_seed(
        json.dumps([_ENTRY, other]), {"MCP_OAUTH_LINEAR_CLIENT_ID": "cid"}
    )
    assert [u.name for u in ready] == ["linear"]
    assert skipped == ["notion"]


def test_invalid_json_raises() -> None:
    with pytest.raises(CatalogSeedError):
        load_catalog_seed("{not json", {})


def test_non_array_raises() -> None:
    with pytest.raises(CatalogSeedError):
        load_catalog_seed(json.dumps({"name": "x"}), {})


def test_invalid_entry_raises() -> None:
    # oauth2 with no oauth_client_id (after resolution) violates the model.
    bad = {**_ENTRY, "oauth_client_id": None}
    with pytest.raises(CatalogSeedError):
        load_catalog_seed(json.dumps([bad]), {})


# --- seed_catalog (idempotent) --------------------------------------------


@pytest.mark.asyncio
async def test_seed_creates_then_idempotent() -> None:
    store = InMemoryMcpConnectorCatalogStore()
    ready, _ = load_catalog_seed(json.dumps([_ENTRY]), {"MCP_OAUTH_LINEAR_CLIENT_ID": "cid"})

    created, existing = await seed_catalog(store=store, entries=ready)
    assert created == ["linear"]
    assert existing == []

    # Second run: already present → skipped, not duplicated.
    created2, existing2 = await seed_catalog(store=store, entries=ready)
    assert created2 == []
    assert existing2 == ["linear"]


# --- shipped template integrity -------------------------------------------


@pytest.mark.asyncio
async def test_seed_wrapper_reads_file_and_creates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_file = tmp_path / "seed.json"
    seed_file.write_text(json.dumps([_ENTRY]), encoding="utf-8")
    monkeypatch.setenv("MCP_OAUTH_LINEAR_CLIENT_ID", "cid")
    store = InMemoryMcpConnectorCatalogStore()
    await _seed_mcp_catalog(Settings(mcp_catalog_seed_file=str(seed_file)), store)
    async with bypass_rls_session():
        assert await store.get_by_name("linear") is not None


@pytest.mark.asyncio
async def test_seed_wrapper_noop_when_unset() -> None:
    store = InMemoryMcpConnectorCatalogStore()
    await _seed_mcp_catalog(Settings(mcp_catalog_seed_file=None), store)
    assert await store.list() == []


@pytest.mark.asyncio
async def test_seed_wrapper_missing_file_raises() -> None:
    store = InMemoryMcpConnectorCatalogStore()
    with pytest.raises(RuntimeError):
        await _seed_mcp_catalog(Settings(mcp_catalog_seed_file="/no/such/seed.json"), store)


def test_shipped_template_is_valid() -> None:
    """The committed configs/mcp-catalog-seed.json must parse + validate when
    every placeholder is supplied (guards the template against drift). The
    seed file is for env-templated oauth client ids; bearer-secret servers
    (e.g. Tavily) are registered via the admin-UI MCP catalog, not here."""
    repo_root = Path(__file__).resolve().parents[3]
    raw = (repo_root / "configs" / "mcp-catalog-seed.json").read_text(encoding="utf-8")
    data = json.loads(raw)
    env = {f"MCP_OAUTH_{e['name'].upper()}_CLIENT_ID": "cid" for e in data}
    ready, skipped = load_catalog_seed(raw, env)
    assert skipped == []
    assert len(ready) == len(data)
