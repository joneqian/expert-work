"""End-to-end tests for ``/v1/agents`` CRUD."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from control_plane.app import create_app
from control_plane.audit import build_default_audit_logger
from control_plane.settings import DEFAULT_DEV_TENANT_ID, Settings
from helix_agent.persistence.audit_log import InMemoryAuditLogStore
from helix_agent.protocol import AuditQuery

_DEFAULT_TENANT = DEFAULT_DEV_TENANT_ID

_VALID_YAML = """\
apiVersion: helix.io/v1
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
    )
    audit_logger = build_default_audit_logger(audit_store)
    app = create_app(settings=settings, audit_logger=audit_logger)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://control-plane.test") as client:
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
    await b5_client.post(
        "/v1/agents",
        json={"manifest_yaml": _VALID_YAML},
        headers={"X-Helix-Tenant": str(_DEFAULT_TENANT)},
    )
    other_tenant = "11111111-1111-1111-1111-111111111111"
    response = await b5_client.get(
        "/v1/agents/code-reviewer/1.0.0",
        headers={"X-Helix-Tenant": other_tenant},
    )
    assert response.status_code == 404
