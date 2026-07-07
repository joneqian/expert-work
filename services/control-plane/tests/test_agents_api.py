"""End-to-end tests for ``/v1/agents`` CRUD."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from control_plane.app import create_app
from control_plane.audit import build_default_audit_logger
from control_plane.settings import DEFAULT_DEV_TENANT_ID, Settings
from expert_work.persistence.audit_log import InMemoryAuditLogStore
from expert_work.protocol import AuditQuery
from tests.auth_fixtures import (
    TEST_AUDIENCE,
    TEST_ISSUER,
    build_test_jwt_verifier,
    make_test_jwt,
)

_DEFAULT_TENANT = DEFAULT_DEV_TENANT_ID

_VALID_YAML = """\
apiVersion: expert_work.io/v1
kind: Agent
metadata:
  name: code-reviewer
  version: "1.0.0"
  tenant: platform-eng
spec:
  tenant_config: {}
  model:
    provider: anthropic
    name: claude-sonnet-4-5
  system_prompt:
    template: "you are a reviewer"
  sandbox:
    resources: { cpu: "1.0", memory: "1Gi" }
    network:
      egress: proxy
      allowlist: ["api.anthropic.com"]
    filesystem:
      readonly_root: true
      writable: ["/workspace"]
"""


@pytest.fixture
def audit_store() -> InMemoryAuditLogStore:
    return InMemoryAuditLogStore()


@pytest.fixture
async def b5_client(audit_store: InMemoryAuditLogStore) -> AsyncIterator[AsyncClient]:
    """A control-plane client that uses an InMemoryAuditLogStore the test
    can introspect (the default fixture builds an isolated audit logger)."""
    settings = Settings(
        env="dev",
        auth_mode="dev",
        rate_limit_burst=10_000,
        rate_limit_per_second=10_000.0,
        oidc_issuer=TEST_ISSUER,
        oidc_audience=[TEST_AUDIENCE],
    )
    audit_logger = build_default_audit_logger(audit_store)
    app = create_app(
        settings=settings,
        audit_logger=audit_logger,
        jwt_verifier=build_test_jwt_verifier(),
    )
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {make_test_jwt(tenant_id=_DEFAULT_TENANT)}"}
    async with AsyncClient(
        transport=transport,
        base_url="http://control-plane.test",
        headers=headers,
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_creates_agent_and_emits_audit(
    b5_client: AsyncClient, audit_store: InMemoryAuditLogStore
) -> None:
    response = await b5_client.post("/v1/agents", json={"manifest_yaml": _VALID_YAML})
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    record = body["data"]["record"]
    assert record["name"] == "code-reviewer"
    assert record["version"] == "1.0.0"
    assert record["status"] == "active"
    assert len(record["spec_sha256"]) == 64

    # Audit row landed.
    page = await audit_store.query(AuditQuery(tenant_id=_DEFAULT_TENANT))
    assert any(
        r.action.value == "manifest:write" and r.result.value == "success" for r in page.entries
    )


@pytest.mark.asyncio
async def test_duplicate_returns_409(b5_client: AsyncClient) -> None:
    await b5_client.post("/v1/agents", json={"manifest_yaml": _VALID_YAML})
    response = await b5_client.post("/v1/agents", json={"manifest_yaml": _VALID_YAML})
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "MANIFEST_DUPLICATE"


@pytest.mark.asyncio
async def test_invalid_manifest_returns_422_with_errors(b5_client: AsyncClient) -> None:
    broken = _VALID_YAML.replace("kind: Agent\n", "")
    response = await b5_client.post("/v1/agents", json={"manifest_yaml": broken})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "MANIFEST_INVALID"
    assert body["error"]["errors"]


@pytest.mark.asyncio
async def test_yaml_syntax_error_returns_400(b5_client: AsyncClient) -> None:
    response = await b5_client.post("/v1/agents", json={"manifest_yaml": "this: is: broken"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "MANIFEST_SYNTAX"


# ---------------------------------------------------------------------------
# read / list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_single_agent(b5_client: AsyncClient) -> None:
    await b5_client.post("/v1/agents", json={"manifest_yaml": _VALID_YAML})
    response = await b5_client.get("/v1/agents/code-reviewer/1.0.0")
    assert response.status_code == 200
    record = response.json()["data"]["record"]
    assert record["name"] == "code-reviewer"


@pytest.mark.asyncio
async def test_get_returns_404_when_missing(b5_client: AsyncClient) -> None:
    response = await b5_client.get("/v1/agents/no-such/9.9.9")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_after_two_posts(b5_client: AsyncClient) -> None:
    await b5_client.post("/v1/agents", json={"manifest_yaml": _VALID_YAML})
    second = _VALID_YAML.replace('version: "1.0.0"', 'version: "1.0.1"')
    await b5_client.post("/v1/agents", json={"manifest_yaml": second})
    response = await b5_client.get("/v1/agents?name=code-reviewer")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 2


@pytest.mark.asyncio
async def test_list_filters_by_status(b5_client: AsyncClient) -> None:
    await b5_client.post("/v1/agents", json={"manifest_yaml": _VALID_YAML})
    response = await b5_client.get("/v1/agents?status=deleted")
    assert response.json()["data"]["total"] == 0


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_replaces_spec(b5_client: AsyncClient) -> None:
    await b5_client.post("/v1/agents", json={"manifest_yaml": _VALID_YAML})
    updated_yaml = _VALID_YAML.replace(
        'template: "you are a reviewer"',
        'template: "you are a senior reviewer"',
    )
    response = await b5_client.put(
        "/v1/agents/code-reviewer/1.0.0",
        json={"manifest_yaml": updated_yaml},
    )
    assert response.status_code == 200
    spec = response.json()["data"]["record"]["spec"]["spec"]["system_prompt"]["template"]
    assert spec == "you are a senior reviewer"


@pytest.mark.asyncio
async def test_put_path_mismatch_returns_422(b5_client: AsyncClient) -> None:
    await b5_client.post("/v1/agents", json={"manifest_yaml": _VALID_YAML})
    response = await b5_client.put(
        "/v1/agents/different-name/1.0.0",
        json={"manifest_yaml": _VALID_YAML},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MANIFEST_PATH_MISMATCH"


@pytest.mark.asyncio
async def test_put_404_when_missing(b5_client: AsyncClient) -> None:
    response = await b5_client.put(
        "/v1/agents/code-reviewer/1.0.0",
        json={"manifest_yaml": _VALID_YAML},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_soft_removes(b5_client: AsyncClient) -> None:
    await b5_client.post("/v1/agents", json={"manifest_yaml": _VALID_YAML})
    response = await b5_client.delete("/v1/agents/code-reviewer/1.0.0")
    assert response.status_code == 204

    # GET no longer returns the row (soft-deleted rows are hidden).
    follow_up = await b5_client.get("/v1/agents/code-reviewer/1.0.0")
    assert follow_up.status_code == 404


@pytest.mark.asyncio
async def test_delete_404_when_missing(b5_client: AsyncClient) -> None:
    response = await b5_client.delete("/v1/agents/no-such/9.9.9")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# tenant isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_other_tenant_cannot_see_agent(b5_client: AsyncClient) -> None:
    from uuid import UUID

    # Default ``b5_client`` JWT is tied to ``_DEFAULT_TENANT``.
    await b5_client.post("/v1/agents", json={"manifest_yaml": _VALID_YAML})

    other_tenant = UUID("11111111-1111-1111-1111-111111111111")
    other_jwt = make_test_jwt(tenant_id=other_tenant)
    response = await b5_client.get(
        "/v1/agents/code-reviewer/1.0.0",
        headers={"Authorization": f"Bearer {other_jwt}"},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Stream HX-5 — revision history / rollback
# ---------------------------------------------------------------------------

_UPDATED_YAML = _VALID_YAML.replace("you are a reviewer", "you are a strict reviewer")


@pytest.mark.asyncio
async def test_revisions_list_and_get_snapshot(b5_client: AsyncClient) -> None:
    await b5_client.post("/v1/agents", json={"manifest_yaml": _VALID_YAML})
    await b5_client.put("/v1/agents/code-reviewer/1.0.0", json={"manifest_yaml": _UPDATED_YAML})

    listing = await b5_client.get("/v1/agents/code-reviewer/1.0.0/revisions")
    assert listing.status_code == 200
    items = listing.json()["data"]["items"]
    assert [i["revision"] for i in items] == [2, 1]
    assert items[0]["actor_id"]
    assert len(items[0]["spec_sha256"]) == 64
    assert "spec" not in items[0]  # summaries only — diff fetches snapshots

    snap = await b5_client.get("/v1/agents/code-reviewer/1.0.0/revisions/1")
    assert snap.status_code == 200
    record = snap.json()["data"]["record"]
    assert record["revision"] == 1
    assert record["spec"]["spec"]["system_prompt"]["template"] == "you are a reviewer"

    missing = await b5_client.get("/v1/agents/code-reviewer/1.0.0/revisions/9")
    assert missing.status_code == 404
    unknown_agent = await b5_client.get("/v1/agents/nope/1.0.0/revisions")
    assert unknown_agent.status_code == 404


@pytest.mark.asyncio
async def test_rollback_appends_revision_with_old_content(
    b5_client: AsyncClient, audit_store: InMemoryAuditLogStore
) -> None:
    await b5_client.post("/v1/agents", json={"manifest_yaml": _VALID_YAML})
    await b5_client.put("/v1/agents/code-reviewer/1.0.0", json={"manifest_yaml": _UPDATED_YAML})

    response = await b5_client.post("/v1/agents/code-reviewer/1.0.0/revisions/1/rollback")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["rolled_back_to"] == 1
    assert data["revision"] == 3  # rollback moved *forward* to old content
    assert data["record"]["spec"]["spec"]["system_prompt"]["template"] == "you are a reviewer"

    # History now has three entries; current content equals revision 1's.
    listing = await b5_client.get("/v1/agents/code-reviewer/1.0.0/revisions")
    items = listing.json()["data"]["items"]
    assert [i["revision"] for i in items] == [3, 2, 1]
    assert items[0]["spec_sha256"] == items[2]["spec_sha256"]

    # Audit row carries the rollback provenance.
    page = await audit_store.query(AuditQuery(tenant_id=_DEFAULT_TENANT))
    rollbacks = [
        r
        for r in page.entries
        if r.action.value == "manifest:write" and (r.details or {}).get("rolled_back_to") == 1
    ]
    assert len(rollbacks) == 1
    assert rollbacks[0].details["revision"] == 3


@pytest.mark.asyncio
async def test_rollback_to_current_content_is_recorded_noop(b5_client: AsyncClient) -> None:
    await b5_client.post("/v1/agents", json={"manifest_yaml": _VALID_YAML})

    response = await b5_client.post("/v1/agents/code-reviewer/1.0.0/revisions/1/rollback")
    assert response.status_code == 200
    assert response.json()["data"]["revision"] is None  # same sha — nothing recorded

    listing = await b5_client.get("/v1/agents/code-reviewer/1.0.0/revisions")
    assert [i["revision"] for i in listing.json()["data"]["items"]] == [1]


@pytest.mark.asyncio
async def test_rollback_unknown_revision_404(b5_client: AsyncClient) -> None:
    await b5_client.post("/v1/agents", json={"manifest_yaml": _VALID_YAML})
    response = await b5_client.post("/v1/agents/code-reviewer/1.0.0/revisions/7/rollback")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# runtime build-cache invalidation on write — an in-place config edit (approval
# gate, tools, model, prompt) must take effect on the next run WITHOUT a
# control-plane restart. ``AgentRuntime`` caches built agents by
# ``(tenant, name, version)`` and only consults the spec on a miss, so a
# same-version spec change is invisible unless the write path invalidates it.
# ---------------------------------------------------------------------------


@pytest.fixture
async def b5_app_client(
    audit_store: InMemoryAuditLogStore,
) -> AsyncIterator[tuple[object, AsyncClient]]:
    """Like ``b5_client`` but also hands back the ``app`` so a test can inspect
    ``app.state.agent_runtime`` — the write paths must evict its build cache."""
    settings = Settings(
        env="dev",
        auth_mode="dev",
        rate_limit_burst=10_000,
        rate_limit_per_second=10_000.0,
        oidc_issuer=TEST_ISSUER,
        oidc_audience=[TEST_AUDIENCE],
    )
    app = create_app(
        settings=settings,
        audit_logger=build_default_audit_logger(audit_store),
        jwt_verifier=build_test_jwt_verifier(),
    )
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {make_test_jwt(tenant_id=_DEFAULT_TENANT)}"}
    async with AsyncClient(
        transport=transport, base_url="http://control-plane.test", headers=headers
    ) as client:
        yield app, client


def _seed_build_cache(app: object) -> tuple[object, str, str]:
    """Stand a sentinel build in the runtime cache for code-reviewer@1.0.0 so a
    following write can be shown to evict it. Returns the cache key."""
    runtime = app.state.agent_runtime  # type: ignore[attr-defined]
    key = (_DEFAULT_TENANT, "code-reviewer", "1.0.0")
    runtime._cache[key] = object()  # stand-in for a BuiltAgent
    return key


@pytest.mark.asyncio
async def test_put_invalidates_runtime_build_cache(
    b5_app_client: tuple[object, AsyncClient],
) -> None:
    app, client = b5_app_client
    await client.post("/v1/agents", json={"manifest_yaml": _VALID_YAML})
    key = _seed_build_cache(app)

    updated = _VALID_YAML.replace("you are a reviewer", "you are a strict reviewer")
    resp = await client.put("/v1/agents/code-reviewer/1.0.0", json={"manifest_yaml": updated})
    assert resp.status_code == 200
    # Stale build evicted → the next run rebuilds from the edited spec.
    assert key not in app.state.agent_runtime._cache  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_rollback_invalidates_runtime_build_cache(
    b5_app_client: tuple[object, AsyncClient],
) -> None:
    app, client = b5_app_client
    await client.post("/v1/agents", json={"manifest_yaml": _VALID_YAML})
    await client.put("/v1/agents/code-reviewer/1.0.0", json={"manifest_yaml": _UPDATED_YAML})
    key = _seed_build_cache(app)

    resp = await client.post("/v1/agents/code-reviewer/1.0.0/revisions/1/rollback")
    assert resp.status_code == 200
    assert key not in app.state.agent_runtime._cache  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_delete_invalidates_runtime_build_cache(
    b5_app_client: tuple[object, AsyncClient],
) -> None:
    app, client = b5_app_client
    await client.post("/v1/agents", json={"manifest_yaml": _VALID_YAML})
    key = _seed_build_cache(app)

    resp = await client.delete("/v1/agents/code-reviewer/1.0.0")
    assert resp.status_code == 204
    # A re-register at the same (name, version) must not reuse the deleted build.
    assert key not in app.state.agent_runtime._cache  # type: ignore[attr-defined]
