# Stream V-C — Tenant MCP Server Registration API (CRUD + connect-probe) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Expose tenant-admin CRUD over the `tenant_mcp_server` registry (built in V-B) — `POST` synchronously probes the remote MCP server (connect + `list_tools`) before persisting, stores the bearer token only as a `secret://` ref in the encrypted secret store, `DELETE` refuses if an agent manifest references the server, and every mutation is audited (no token value ever recorded/logged).

**Architecture:** A new FastAPI router `api/mcp_servers.py` mounted at `/v1/mcp-servers`. The persistence layer (`TenantMcpServerStore`), SSRF guard (`validate_remote_url`), and protocol types already exist from V-B. The probe reuses the orchestrator's remote MCP client (`SseMCPClient`/`StreamableHttpMCPClient`, already a control-plane dep). RBAC gains a new `mcp_server` resource. Audit gains `MCP_SERVER_*` actions + a `tenant_mcp_server` resource type (kept in sync across the two `ResourceType` Literals).

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy async, pytest + httpx ASGITransport.

**Scope (this PR = V-C only):** API + probe + RBAC resource + audit enums + app wiring + tests. NOT in scope: the per-tenant runtime pool (V-D), `MCPToolSpec.servers` schema (V-E), the `/available` + `/{name}/tools` discovery endpoints and UI (V-F), the agent-form picker (V-G).

**Branch:** `stream-v/c-api` (off `main`, after V-B merged).

**Key facts (verified 2026-06-03):**
- Endpoint auth: `from control_plane.api._authz import require` → `Depends(require(resource, action))`; principal via the same dep. `principal.tenant_id` (UUID), `principal.subject_id` (str).
- RBAC matrix: `services/control-plane/src/control_plane/auth/rbac.py` — `Resource`/`Action` Literals + `_grants(role)` per-role dict. **No `mcp_server` resource exists yet.**
- Audit: `await emit(audit, tenant_id=, actor_id=, action=AuditAction.X, resource_type="...", resource_id=, trace_id=current_trace_id_hex(), details={...})` from `control_plane.audit`. `AuditAction` is a **single StrEnum** in `packages/expert-work-protocol/src/expert_work/protocol/audit.py`. `ResourceType` is a Literal duplicated in BOTH `services/control-plane/src/control_plane/audit.py` AND `packages/expert-work-protocol/src/expert_work/protocol/audit.py` — [memory:project_audit_literal_drift] both must change together.
- Secret store dep pattern (Stream Q): `services/control-plane/src/control_plane/api/platform_config.py` — `await secret_store.put(name, value.get_secret_value())` then store `f"secret://{name}"`. Retrieve: `await secret_store.get(parse_secret_ref(ref))` (`from expert_work.runtime.secret_store.refs import parse_secret_ref`).
- Remote MCP client: `from orchestrator.tools.mcp import SseMCPClient, StreamableHttpMCPClient, MCPServerConfig, MCPToolDef` — lifecycle `await client.start()` → `await client.list_tools()` → `await client.close()`. Bearer header injected via `resolved_headers={"Authorization": f"Bearer {token}"}` passed to the client constructor. control-plane already depends on `expert-work-orchestrator`.
- Agent spec store: `request.app.state` has the agent spec store; `list_by_tenant(*, tenant_id, status=None, limit, offset)` → records with `.spec_json` (dict). (Confirm the exact app.state attribute name + store type by grepping `agent_spec` in `app.py`.)
- Response envelope: success `{"success": True, "data": <obj>, "error": None}`; error `HTTPException(status_code, detail={"code": "...", "message": "..."})`.
- Mirror router for structure/DI/tests: `services/control-plane/src/control_plane/api/service_accounts.py` + `services/control-plane/tests/` (service-account / platform-config API tests).

---

## File Structure

**Create:**
- `services/control-plane/src/control_plane/mcp_probe.py` — `probe_remote_mcp(...)` + `McpProbeError` (connect + list_tools under timeout; SSRF-checked; injectable client factory for tests).
- `services/control-plane/src/control_plane/api/mcp_servers.py` — the `/v1/mcp-servers` router (POST/GET/PATCH/DELETE) + request/response models + `_manifest_references_server` helper.
- `services/control-plane/tests/test_mcp_probe.py` — probe unit tests (fake client factory).
- `services/control-plane/tests/test_mcp_servers_api.py` — API tests.
- `services/control-plane/tests/test_rbac_mcp_server.py` — RBAC matrix unit test for the new resource (or add to the existing rbac test file if one exists — grep first).

**Modify:**
- `services/control-plane/src/control_plane/auth/rbac.py` — add `"mcp_server"` to `Resource`; grant it in `_grants` for ADMIN/OPERATOR/VIEWER.
- `packages/expert-work-protocol/src/expert_work/protocol/audit.py` — add `MCP_SERVER_CREATE/UPDATE/DELETE` to `AuditAction`; add `"tenant_mcp_server"` to the `ResourceType` Literal.
- `services/control-plane/src/control_plane/audit.py` — add `"tenant_mcp_server"` to its `ResourceType` Literal.
- `services/control-plane/src/control_plane/app.py` — construct `SqlTenantMcpServerStore` into the store bundle, attach to `app.state`, include the new router.

---

## Task 1: RBAC — add the `mcp_server` resource

**Files:**
- Modify: `services/control-plane/src/control_plane/auth/rbac.py`
- Create/extend: `services/control-plane/tests/test_rbac_mcp_server.py` (or the existing rbac test file)

- [ ] **Step 1: Write the failing test**

First `grep -rn "is_allowed" services/control-plane/tests/ | head` to find the existing rbac test file and its import style. Create `services/control-plane/tests/test_rbac_mcp_server.py` (adapt imports to match an existing rbac test):

```python
"""RBAC matrix coverage for the mcp_server resource (Stream V-C)."""

from __future__ import annotations

from uuid import uuid4

from control_plane.auth.rbac import is_allowed
from expert_work.protocol import Principal, Role


def _principal(role: Role) -> Principal:
    # Match how other rbac tests build a JWT principal; adjust fields to the
    # real Principal constructor (grep an existing rbac test for the exact shape).
    return Principal(
        subject_id="admin@acme",
        subject_type="user",
        tenant_id=uuid4(),
        roles=[role.value],
        scopes=[],
    )


def test_admin_can_write_and_delete_mcp_server() -> None:
    p = _principal(Role.ADMIN)
    assert is_allowed(p, resource="mcp_server", action="read")
    assert is_allowed(p, resource="mcp_server", action="write")
    assert is_allowed(p, resource="mcp_server", action="delete")


def test_operator_can_read_not_write_mcp_server() -> None:
    p = _principal(Role.OPERATOR)
    assert is_allowed(p, resource="mcp_server", action="read")
    assert not is_allowed(p, resource="mcp_server", action="write")
    assert not is_allowed(p, resource="mcp_server", action="delete")


def test_viewer_read_only_mcp_server() -> None:
    p = _principal(Role.VIEWER)
    assert is_allowed(p, resource="mcp_server", action="read")
    assert not is_allowed(p, resource="mcp_server", action="write")
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && uv run python -m pytest services/control-plane/tests/test_rbac_mcp_server.py -q`
Expected: FAIL — `mcp_server` is not a valid `Resource` literal / not granted (mypy/type or assertion failure).

- [ ] **Step 3: Add the resource + grants**

In `services/control-plane/src/control_plane/auth/rbac.py`:
1. Add `"mcp_server",` to the `Resource` Literal (after `"memory"`).
2. In `_grants`, for `Role.ADMIN` add: `"mcp_server": {"read", "write", "delete"},`
3. For `Role.OPERATOR` add: `"mcp_server": {"read"},`  (runtime/agent-authoring read on the hot path)
4. For VIEWER add: `"mcp_server": {"read"},`

Add a short comment on each like `# Stream V — tenant remote MCP server registry`.

- [ ] **Step 4: Run tests to confirm pass**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && uv run python -m pytest services/control-plane/tests/test_rbac_mcp_server.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/control-plane/src/control_plane/auth/rbac.py services/control-plane/tests/test_rbac_mcp_server.py
git commit -m "feat(stream-v): RBAC mcp_server resource (V-C)"
```

---

## Task 2: Audit — new actions + resource type (both Literals)

**Files:**
- Modify: `packages/expert-work-protocol/src/expert_work/protocol/audit.py`
- Modify: `services/control-plane/src/control_plane/audit.py`

- [ ] **Step 1: Write the failing test**

Add `services/control-plane/tests/test_audit_mcp_server_types.py`:

```python
"""Stream V-C — audit enum + resource-type additions for MCP servers."""

from __future__ import annotations

from expert_work.protocol import AuditAction


def test_mcp_server_audit_actions_exist() -> None:
    assert AuditAction.MCP_SERVER_CREATE.value == "mcp_server:create"
    assert AuditAction.MCP_SERVER_UPDATE.value == "mcp_server:update"
    assert AuditAction.MCP_SERVER_DELETE.value == "mcp_server:delete"


def test_resource_type_literal_includes_tenant_mcp_server() -> None:
    # Both Literals must include the new resource type (drift guard).
    from typing import get_args

    from control_plane.audit import ResourceType as CpResourceType
    from expert_work.protocol.audit import ResourceType as ProtoResourceType

    assert "tenant_mcp_server" in get_args(CpResourceType)
    assert "tenant_mcp_server" in get_args(ProtoResourceType)
```

- [ ] **Step 2: Run to confirm fail**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && uv run python -m pytest services/control-plane/tests/test_audit_mcp_server_types.py -q`
Expected: FAIL (`AttributeError: MCP_SERVER_CREATE` / assertion on missing literal member).

- [ ] **Step 3: Add the enum members + literal members**

In `packages/expert-work-protocol/src/expert_work/protocol/audit.py`:
1. Add to the `AuditAction` StrEnum (match the existing `resource:verb` value convention, e.g. near other resource actions):
   ```python
   MCP_SERVER_CREATE = "mcp_server:create"
   MCP_SERVER_UPDATE = "mcp_server:update"
   MCP_SERVER_DELETE = "mcp_server:delete"
   ```
2. Add `"tenant_mcp_server",` to the `ResourceType` Literal in this file (keep alphabetical/grouping consistent with neighbors).

In `services/control-plane/src/control_plane/audit.py`:
3. Add `"tenant_mcp_server",` to its `ResourceType` Literal too (the duplicate — [memory:project_audit_literal_drift]).

- [ ] **Step 4: Run to confirm pass**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && uv run python -m pytest services/control-plane/tests/test_audit_mcp_server_types.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/expert-work-protocol/src/expert_work/protocol/audit.py services/control-plane/src/control_plane/audit.py services/control-plane/tests/test_audit_mcp_server_types.py
git commit -m "feat(stream-v): MCP_SERVER audit actions + tenant_mcp_server resource type (V-C)"
```

---

## Task 3: Connect-probe helper

**Files:**
- Create: `services/control-plane/src/control_plane/mcp_probe.py`
- Create: `services/control-plane/tests/test_mcp_probe.py`

- [ ] **Step 1: Write the failing tests**

Create `services/control-plane/tests/test_mcp_probe.py`:

```python
"""Unit tests for the remote MCP connect-probe (Stream V-C)."""

from __future__ import annotations

import pytest

from control_plane.mcp_probe import McpProbeError, probe_remote_mcp
from orchestrator.tools.mcp import MCPToolDef


class _FakeClient:
    def __init__(self, *, tools=None, raise_on_start=None, raise_on_list=None):
        self._tools = tools or []
        self._raise_on_start = raise_on_start
        self._raise_on_list = raise_on_list
        self.closed = False

    async def start(self) -> None:
        if self._raise_on_start is not None:
            raise self._raise_on_start

    async def list_tools(self):
        if self._raise_on_list is not None:
            raise self._raise_on_list
        return tuple(self._tools)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_probe_returns_tools_on_success() -> None:
    captured: dict[str, object] = {}

    def factory(config, headers):
        captured["transport"] = config.transport
        captured["headers"] = headers
        return _FakeClient(
            tools=[MCPToolDef(name="create_issue", description="", input_schema={})]
        )

    tools = await probe_remote_mcp(
        name="github",
        transport="streamable_http",
        url="https://mcp.example.com/mcp",
        bearer_token="ghp_secret",
        timeout_s=10.0,
        client_factory=factory,
    )
    assert [t.name for t in tools] == ["create_issue"]
    assert captured["headers"]["Authorization"] == "Bearer ghp_secret"


@pytest.mark.asyncio
async def test_probe_no_auth_sends_no_authorization_header() -> None:
    captured: dict[str, object] = {}

    def factory(config, headers):
        captured["headers"] = headers
        return _FakeClient(tools=[])

    await probe_remote_mcp(
        name="open", transport="sse", url="https://mcp.example.com/sse",
        bearer_token=None, timeout_s=10.0, client_factory=factory,
    )
    assert "Authorization" not in captured["headers"]


@pytest.mark.asyncio
async def test_probe_rejects_ssrf_url() -> None:
    with pytest.raises(McpProbeError) as ei:
        await probe_remote_mcp(
            name="evil", transport="streamable_http",
            url="http://169.254.169.254/latest", bearer_token=None,
            timeout_s=10.0, client_factory=lambda c, h: _FakeClient(),
        )
    assert ei.value.code == "MCP_SERVER_INVALID_URL"


@pytest.mark.asyncio
async def test_probe_wraps_connect_failure() -> None:
    def factory(config, headers):
        return _FakeClient(raise_on_start=RuntimeError("connection refused"))

    with pytest.raises(McpProbeError) as ei:
        await probe_remote_mcp(
            name="down", transport="streamable_http", url="https://down.example.com/mcp",
            bearer_token=None, timeout_s=10.0, client_factory=factory,
        )
    assert ei.value.code == "MCP_SERVER_PROBE_FAILED"


@pytest.mark.asyncio
async def test_probe_always_closes_client() -> None:
    client = _FakeClient(raise_on_list=RuntimeError("boom"))

    def factory(config, headers):
        return client

    with pytest.raises(McpProbeError):
        await probe_remote_mcp(
            name="x", transport="sse", url="https://x.example.com/sse",
            bearer_token=None, timeout_s=10.0, client_factory=factory,
        )
    assert client.closed is True
```

- [ ] **Step 2: Run to confirm fail**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && uv run python -m pytest services/control-plane/tests/test_mcp_probe.py -q`
Expected: FAIL — `ModuleNotFoundError: control_plane.mcp_probe`.

- [ ] **Step 3: Implement the probe**

Create `services/control-plane/src/control_plane/mcp_probe.py`:

```python
"""Connect-probe for tenant-registered remote MCP servers — Stream V-C.

Before a remote MCP server is persisted (or its url/token changed), the
control plane connects to it and calls ``list_tools`` to prove it is real and
reachable. The probe runs the orchestrator's remote MCP client in-process
(control-plane already depends on the orchestrator). The URL is SSRF-checked
at this boundary too — never trust that the caller validated it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence

from orchestrator.tools.mcp import (
    MCPServerConfig,
    MCPToolDef,
    SseMCPClient,
    StreamableHttpMCPClient,
)

from expert_work.common.url_validation import RemoteURLError, validate_remote_url

logger = logging.getLogger("expert_work.control_plane.mcp_probe")

# A factory so tests can inject a fake client. Production builds the real
# transport client from config + already-resolved headers.
ProbeClientFactory = Callable[[MCPServerConfig, Mapping[str, str]], object]


class McpProbeError(Exception):
    """Probe failed. ``code`` is the machine-readable API error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _default_client_factory(
    config: MCPServerConfig, headers: Mapping[str, str]
) -> object:
    if config.transport == "sse":
        return SseMCPClient(config=config, resolved_headers=dict(headers))
    return StreamableHttpMCPClient(config=config, resolved_headers=dict(headers))


async def probe_remote_mcp(
    *,
    name: str,
    transport: str,
    url: str,
    bearer_token: str | None,
    timeout_s: float,
    client_factory: ProbeClientFactory = _default_client_factory,
) -> Sequence[MCPToolDef]:
    """Connect to a remote MCP server and return its advertised tools.

    Raises :class:`McpProbeError` (with ``code`` in
    ``{MCP_SERVER_INVALID_URL, MCP_SERVER_PROBE_FAILED}``) on SSRF rejection,
    connect failure, timeout, or list_tools error. Never logs the token.
    """
    try:
        validate_remote_url(url)
    except RemoteURLError as exc:
        raise McpProbeError("MCP_SERVER_INVALID_URL", str(exc)) from exc

    headers: dict[str, str] = {}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    config = MCPServerConfig(
        name=name,
        transport=transport,  # type: ignore[arg-type]
        url=url,
        auth_type="bearer" if bearer_token else "none",
        # The token is injected via headers above; auth_config carries no
        # secret here (the dataclass field is repr=False regardless).
        auth_config={"token_ref": "secret://probe"} if bearer_token else {},
        timeout_s=timeout_s,
    )
    client = client_factory(config, headers)
    try:
        await asyncio.wait_for(client.start(), timeout=timeout_s)  # type: ignore[attr-defined]
        tools = await asyncio.wait_for(client.list_tools(), timeout=timeout_s)  # type: ignore[attr-defined]
        return tools
    except McpProbeError:
        raise
    except (TimeoutError, Exception) as exc:  # noqa: BLE001 — probe maps all failures
        logger.info("mcp_probe.failed server=%s transport=%s", name, transport)
        raise McpProbeError(
            "MCP_SERVER_PROBE_FAILED",
            f"could not connect to MCP server {name!r}: {type(exc).__name__}",
        ) from exc
    finally:
        try:
            await client.close()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 — best-effort teardown
            logger.info("mcp_probe.close_failed server=%s", name)
```

Note on the `MCPServerConfig` construction: bearer auth requires `auth_config["token_ref"]` in `MCPServerConfig.__post_init__`. We supply a sentinel `secret://probe` (the actual token is injected via `headers`); the config object is only a carrier for transport/url/timeout. If `__post_init__` rejects the sentinel, instead set `auth_type="none"` on the config (the header still carries the bearer token) — verify against `MCPServerConfig.__post_init__` and pick whichever passes its validation; the header is what authenticates.

- [ ] **Step 4: Run to confirm pass**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && uv run python -m pytest services/control-plane/tests/test_mcp_probe.py -q`
Expected: PASS (5 tests). If the `noqa: BLE001`/broad-except trips ruff, narrow per Step 6.

- [ ] **Step 5: Lint/type**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && uv run ruff check services/control-plane/src/control_plane/mcp_probe.py services/control-plane/tests/test_mcp_probe.py && uv run mypy services/control-plane/src/control_plane/mcp_probe.py`
Expected: clean. (Note: catching `(TimeoutError, Exception)` is redundant since `Exception` covers it; if ruff/mypy complains, use just `except Exception` with a `# noqa: BLE001` and rely on it also catching asyncio timeout — `asyncio.TimeoutError` is `TimeoutError` which is an `Exception`.)

- [ ] **Step 6: Commit**

```bash
git add services/control-plane/src/control_plane/mcp_probe.py services/control-plane/tests/test_mcp_probe.py
git commit -m "feat(stream-v): remote MCP connect-probe helper (V-C)"
```

---

## Task 4: Manifest reference-check helper

**Files:**
- Create: `services/control-plane/src/control_plane/api/mcp_servers.py` (start the module with just the helper; the router is added in Task 5)
- Create: `services/control-plane/tests/test_mcp_server_reference_check.py`

- [ ] **Step 1: Write the failing test**

Create `services/control-plane/tests/test_mcp_server_reference_check.py`:

```python
"""Unit tests for the MCP-server manifest reference check (Stream V-C)."""

from __future__ import annotations

from control_plane.api.mcp_servers import manifest_references_server


def _spec(tools: list[dict]) -> dict:
    return {"apiVersion": "v1", "kind": "Agent", "spec": {"tools": tools}}


def test_no_reference_when_no_mcp_tool() -> None:
    spec = _spec([{"type": "builtin", "name": "web_search"}])
    assert manifest_references_server(spec, "github") is False


def test_no_reference_when_servers_field_absent() -> None:
    # Pre-V-E manifests have no `servers` key on the mcp tool → no reference.
    spec = _spec([{"type": "mcp", "allow_tools": []}])
    assert manifest_references_server(spec, "github") is False


def test_reference_when_server_in_servers_list() -> None:
    spec = _spec([{"type": "mcp", "servers": ["github", "linear"], "allow_tools": []}])
    assert manifest_references_server(spec, "github") is True
    assert manifest_references_server(spec, "linear") is True
    assert manifest_references_server(spec, "postgres") is False


def test_handles_missing_or_malformed_tools() -> None:
    assert manifest_references_server({}, "github") is False
    assert manifest_references_server({"spec": {}}, "github") is False
    assert manifest_references_server({"spec": {"tools": "nope"}}, "github") is False
```

- [ ] **Step 2: Run to confirm fail**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && uv run python -m pytest services/control-plane/tests/test_mcp_server_reference_check.py -q`
Expected: FAIL — module/function does not exist.

- [ ] **Step 3: Implement the helper**

Create `services/control-plane/src/control_plane/api/mcp_servers.py` with (for now) just:

```python
"""Tenant MCP server registration API — Stream V-C."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def manifest_references_server(spec_json: Mapping[str, Any], server_name: str) -> bool:
    """Return whether an agent manifest references the named MCP server.

    Reads ``spec.tools[].servers`` from the raw manifest dict (the
    ``MCPToolSpec.servers`` field is added in V-E; pre-V-E manifests have no
    ``servers`` key, so this is dormant — and forward-compatible — until then).
    """
    spec = spec_json.get("spec")
    if not isinstance(spec, Mapping):
        return False
    tools = spec.get("tools")
    if not isinstance(tools, list):
        return False
    for tool in tools:
        if not isinstance(tool, Mapping) or tool.get("type") != "mcp":
            continue
        servers = tool.get("servers")
        if isinstance(servers, list) and server_name in servers:
            return True
    return False
```

- [ ] **Step 4: Run to confirm pass**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && uv run python -m pytest services/control-plane/tests/test_mcp_server_reference_check.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add services/control-plane/src/control_plane/api/mcp_servers.py services/control-plane/tests/test_mcp_server_reference_check.py
git commit -m "feat(stream-v): manifest MCP-server reference check helper (V-C)"
```

---

## Task 5: The `/v1/mcp-servers` router (POST/GET/PATCH/DELETE)

**Files:**
- Modify: `services/control-plane/src/control_plane/api/mcp_servers.py` (add the router + models)

- [ ] **Step 1: Read the mirror router**

Read `services/control-plane/src/control_plane/api/service_accounts.py` in full for the exact router/DI/audit idiom. Also read `api/platform_config.py`'s secret-store usage. Note the exact imports (`require`, `_principal`, `emit`, `AuditAction`, `current_trace_id_hex`, `AuditLogger`, `SecretStore`, `parse_secret_ref`).

- [ ] **Step 2: Add request/response models + DI accessors**

Append to `services/control-plane/src/control_plane/api/mcp_servers.py` (keep the helper from Task 4):

```python
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from control_plane.api._authz import require
from control_plane.audit import emit
from control_plane.mcp_probe import McpProbeError, probe_remote_mcp
from expert_work.common.observability import current_trace_id_hex
from expert_work.common.url_validation import RemoteURLError, validate_remote_url
from expert_work.persistence import (
    TenantMcpServerAlreadyExistsError,
    TenantMcpServerNotFoundError,
    TenantMcpServerStore,
)
from expert_work.protocol import (
    AuditAction,
    McpServerAuthType,
    McpServerTransport,
    Principal,
    TenantMcpServerPatch,
)
from expert_work.runtime.audit.logger import AuditLogger
from expert_work.runtime.secret_store.base import SecretStore

logger = logging.getLogger("expert_work.control_plane.api.mcp_servers")

_DEFAULT_TIMEOUT_S = 30.0


class CreateMcpServerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64)
    transport: McpServerTransport
    url: str = Field(min_length=1)
    auth_type: McpServerAuthType = "none"
    token: SecretStr | None = None
    timeout_s: float = Field(default=_DEFAULT_TIMEOUT_S, gt=0, le=300)


class UpdateMcpServerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str | None = Field(default=None, min_length=1)
    token: SecretStr | None = None
    timeout_s: float | None = Field(default=None, gt=0, le=300)
    enabled: bool | None = None


def _get_store(request: Request) -> TenantMcpServerStore:
    return request.app.state.tenant_mcp_server_store  # type: ignore[no-any-return]


def _get_secret_store(request: Request) -> SecretStore:
    return request.app.state.secret_store  # type: ignore[no-any-return]


def _get_audit(request: Request) -> AuditLogger:
    return request.app.state.audit_logger  # type: ignore[no-any-return]


def _get_agent_spec_store(request: Request):  # type: ignore[no-untyped-def]
    # Match the actual app.state attribute (grep `agent_spec` in app.py).
    return request.app.state.agent_spec_store


def _token_secret_name(tenant_id: UUID, name: str) -> str:
    return f"expert-work/tenant/{tenant_id}/mcp/{name}/token"


def _public(record: object) -> dict[str, object]:
    # Serialize the record WITHOUT exposing whether a token exists beyond the
    # auth_type flag. token_secret_ref is a ref (not a secret) but we drop it
    # from the public payload to keep the surface minimal.
    data = record.model_dump(mode="json")  # type: ignore[attr-defined]
    data.pop("token_secret_ref", None)
    return data
```

- [ ] **Step 3: Add the router with the four handlers**

Append:

```python
def build_mcp_servers_router() -> APIRouter:
    router = APIRouter(prefix="/v1/mcp-servers", tags=["mcp-servers"])

    @router.post("", status_code=201)
    async def create_mcp_server(
        payload: CreateMcpServerRequest,
        principal: Annotated[Principal, Depends(require("mcp_server", "write"))],
        store: Annotated[TenantMcpServerStore, Depends(_get_store)],
        secret_store: Annotated[SecretStore, Depends(_get_secret_store)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
    ) -> dict[str, object]:
        tenant_id = principal.tenant_id
        # 1) SSRF check (probe re-checks too, but fail fast with a clear code).
        try:
            validate_remote_url(payload.url)
        except RemoteURLError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "MCP_SERVER_INVALID_URL", "message": str(exc)},
            ) from exc
        # 2) bearer requires a token.
        if payload.auth_type == "bearer" and payload.token is None:
            raise HTTPException(
                status_code=422,
                detail={"code": "MCP_SERVER_TOKEN_REQUIRED", "message": "bearer auth requires token"},
            )
        # 3) reject duplicate BEFORE probe/put (avoid orphan secret version).
        if await store.get(tenant_id=tenant_id, name=payload.name) is not None:
            raise HTTPException(
                status_code=409,
                detail={"code": "MCP_SERVER_DUPLICATE", "message": "name already registered"},
            )
        raw_token = payload.token.get_secret_value() if payload.token is not None else None
        # 4) probe (connect + list_tools) with the raw token in memory.
        try:
            tools = await probe_remote_mcp(
                name=payload.name,
                transport=payload.transport,
                url=payload.url,
                bearer_token=raw_token,
                timeout_s=payload.timeout_s,
            )
        except McpProbeError as exc:
            raise HTTPException(
                status_code=422, detail={"code": exc.code, "message": exc.message}
            ) from exc
        # 5) persist token as a secret ref (only on probe success).
        token_secret_ref: str | None = None
        if raw_token is not None:
            name = _token_secret_name(tenant_id, payload.name)
            await secret_store.put(name, raw_token)
            token_secret_ref = f"secret://{name}"
        # 6) create the row.
        try:
            record = await store.create(
                tenant_id=tenant_id,
                name=payload.name,
                transport=payload.transport,
                url=payload.url,
                auth_type=payload.auth_type,
                token_secret_ref=token_secret_ref,
                timeout_s=payload.timeout_s,
                created_by=principal.subject_id,
            )
        except TenantMcpServerAlreadyExistsError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "MCP_SERVER_DUPLICATE", "message": "name already registered"},
            ) from exc
        await emit(
            audit,
            tenant_id=tenant_id,
            actor_id=principal.subject_id,
            action=AuditAction.MCP_SERVER_CREATE,
            resource_type="tenant_mcp_server",
            resource_id=str(record.id),
            trace_id=current_trace_id_hex(),
            details={
                "name": record.name,
                "transport": record.transport,
                "url": record.url,
                "tool_count": len(tools),
            },  # NEVER include the token
        )
        return {"success": True, "data": {**_public(record), "tool_count": len(tools)}, "error": None}

    @router.get("")
    async def list_mcp_servers(
        principal: Annotated[Principal, Depends(require("mcp_server", "read"))],
        store: Annotated[TenantMcpServerStore, Depends(_get_store)],
    ) -> dict[str, object]:
        rows = await store.list_for_tenant(tenant_id=principal.tenant_id)
        return {"success": True, "data": [_public(r) for r in rows], "error": None}

    @router.patch("/{name}")
    async def update_mcp_server(
        name: str,
        payload: UpdateMcpServerRequest,
        principal: Annotated[Principal, Depends(require("mcp_server", "write"))],
        store: Annotated[TenantMcpServerStore, Depends(_get_store)],
        secret_store: Annotated[SecretStore, Depends(_get_secret_store)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
    ) -> dict[str, object]:
        tenant_id = principal.tenant_id
        existing = await store.get(tenant_id=tenant_id, name=name)
        if existing is None:
            raise HTTPException(status_code=404, detail={"code": "MCP_SERVER_NOT_FOUND", "message": "not found"})
        new_url = payload.url if payload.url is not None else existing.url
        if payload.url is not None:
            try:
                validate_remote_url(new_url)
            except RemoteURLError as exc:
                raise HTTPException(
                    status_code=422, detail={"code": "MCP_SERVER_INVALID_URL", "message": str(exc)}
                ) from exc
        # Re-probe when connectivity-affecting fields change (url or token).
        token_secret_ref = existing.token_secret_ref
        if payload.url is not None or payload.token is not None:
            raw_token: str | None
            if payload.token is not None:
                raw_token = payload.token.get_secret_value()
            elif existing.token_secret_ref is not None:
                from expert_work.runtime.secret_store.refs import parse_secret_ref

                raw_token = await secret_store.get(parse_secret_ref(existing.token_secret_ref))
            else:
                raw_token = None
            try:
                await probe_remote_mcp(
                    name=name, transport=existing.transport, url=new_url,
                    bearer_token=raw_token, timeout_s=payload.timeout_s or existing.timeout_s,
                )
            except McpProbeError as exc:
                raise HTTPException(
                    status_code=422, detail={"code": exc.code, "message": exc.message}
                ) from exc
            if payload.token is not None:
                sname = _token_secret_name(tenant_id, name)
                await secret_store.put(sname, payload.token.get_secret_value())
                token_secret_ref = f"secret://{sname}"
        patch = TenantMcpServerPatch(
            url=payload.url,
            token_secret_ref=(token_secret_ref if payload.token is not None else None),
            timeout_s=payload.timeout_s,
            enabled=payload.enabled,
        )
        try:
            record = await store.update(tenant_id=tenant_id, name=name, patch=patch)
        except TenantMcpServerNotFoundError as exc:
            raise HTTPException(
                status_code=404, detail={"code": "MCP_SERVER_NOT_FOUND", "message": "not found"}
            ) from exc
        await emit(
            audit, tenant_id=tenant_id, actor_id=principal.subject_id,
            action=AuditAction.MCP_SERVER_UPDATE, resource_type="tenant_mcp_server",
            resource_id=str(record.id), trace_id=current_trace_id_hex(),
            details={"name": record.name, "url": record.url, "enabled": record.enabled},
        )
        return {"success": True, "data": _public(record), "error": None}

    @router.delete("/{name}", status_code=204)
    async def delete_mcp_server(
        name: str,
        principal: Annotated[Principal, Depends(require("mcp_server", "delete"))],
        store: Annotated[TenantMcpServerStore, Depends(_get_store)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
        agent_spec_store: Annotated[object, Depends(_get_agent_spec_store)],
    ) -> None:
        tenant_id = principal.tenant_id
        # Reference check: refuse if any agent manifest references this server.
        specs = await agent_spec_store.list_by_tenant(tenant_id=tenant_id, limit=1000)  # type: ignore[attr-defined]
        referencing = [
            s.name for s in specs if manifest_references_server(s.spec_json, name)
        ]
        if referencing:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "MCP_SERVER_IN_USE",
                    "message": f"referenced by agent(s): {', '.join(sorted(set(referencing)))}",
                },
            )
        try:
            await store.delete(tenant_id=tenant_id, name=name)
        except TenantMcpServerNotFoundError as exc:
            raise HTTPException(
                status_code=404, detail={"code": "MCP_SERVER_NOT_FOUND", "message": "not found"}
            ) from exc
        await emit(
            audit, tenant_id=tenant_id, actor_id=principal.subject_id,
            action=AuditAction.MCP_SERVER_DELETE, resource_type="tenant_mcp_server",
            resource_id=name, trace_id=current_trace_id_hex(), details={"name": name},
        )

    return router
```

Adjust any import path that doesn't resolve (e.g. `SecretStore` location, `AuditLogger` location, `current_trace_id_hex`) to match what `service_accounts.py`/`platform_config.py` actually import. Confirm `agent_spec_store` attribute name and its `list_by_tenant` signature against `app.py`/the store.

- [ ] **Step 4: Type/lint check**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && uv run ruff check services/control-plane/src/control_plane/api/mcp_servers.py && uv run mypy services/control-plane/src/control_plane/api/mcp_servers.py`
Expected: clean (control-plane/src is NOT in the CI mypy gate per [memory:reference_ci_lint_type_test_scopes], but run it anyway to catch obvious bugs).

- [ ] **Step 5: Commit**

```bash
git add services/control-plane/src/control_plane/api/mcp_servers.py
git commit -m "feat(stream-v): /v1/mcp-servers CRUD router with connect-probe (V-C)"
```

---

## Task 6: App wiring (store + router)

**Files:**
- Modify: `services/control-plane/src/control_plane/app.py`

- [ ] **Step 1: Wire the store exactly like an existing tenant-scoped store**

In `app.py`, grep for `service_account` (or `tenant_config`) to find every wiring site, and add a parallel `tenant_mcp_server` entry at each:
1. Import: `from expert_work.persistence import SqlTenantMcpServerStore, InMemoryTenantMcpServerStore, TenantMcpServerStore` (match the existing import grouping).
2. `_SqlStores` dataclass: add field `tenant_mcp_server: TenantMcpServerStore`.
3. `_build_sql_stores(...)`: add `tenant_mcp_server=SqlTenantMcpServerStore(session_factory),`.
4. In `create_app` where stores are resolved (sql vs in-memory fallback), add:
   `resolved_tenant_mcp_server = sql_stores.tenant_mcp_server if sql_stores else InMemoryTenantMcpServerStore()` (mirror the exact resolution pattern used for another tenant-scoped store).
5. Attach to app state: `app.state.tenant_mcp_server_store = resolved_tenant_mcp_server`.

- [ ] **Step 2: Register the router**

Find where `build_service_accounts_router()` is `app.include_router(...)`-ed and add right after:
```python
from control_plane.api.mcp_servers import build_mcp_servers_router
app.include_router(build_mcp_servers_router())
```
(Match the existing import placement — top-of-file imports, not inline, if that's the convention.)

- [ ] **Step 3: Smoke-check the app imports + routes**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && uv run python -c "
from control_plane.app import create_app
" 2>&1 | tail -5` (or the project's app-construction smoke test). Confirm no import error. Then run any existing app/route smoke test: `uv run python -m pytest services/control-plane/tests -k "app or routes or openapi" -q` if present.

- [ ] **Step 4: Commit**

```bash
git add services/control-plane/src/control_plane/app.py
git commit -m "feat(stream-v): wire tenant MCP server store + router into app (V-C)"
```

---

## Task 7: API tests

**Files:**
- Create: `services/control-plane/tests/test_mcp_servers_api.py`

- [ ] **Step 1: Read an existing API test for the exact fixture/auth/seed pattern**

Read `services/control-plane/tests/test_platform_config_api.py` (and/or the service-account API test) for: `create_app(...)`, the `ASGITransport`/`AsyncClient` setup, how a principal/JWT is seeded (`_seed_admin` / `_headers`), and how `app.state.secret_store` is asserted. Mirror it. To make the probe deterministic in tests, monkeypatch `control_plane.api.mcp_servers.probe_remote_mcp` (or pass via app.state) — the cleanest is `monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", fake_probe)` where `fake_probe` returns a list of `MCPToolDef` or raises `McpProbeError`.

- [ ] **Step 2: Write the tests**

```python
"""API tests for /v1/mcp-servers (Stream V-C)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from control_plane.mcp_probe import McpProbeError
from orchestrator.tools.mcp import MCPToolDef

# Reuse the app/seed/header helpers from the sibling API test module by
# importing them or copying the minimal fixture wiring. The tests below assume
# helpers `_make_app()`, `_admin_headers(app)`, `_viewer_headers(app)` exist —
# build them to match test_platform_config_api.py's pattern.


async def _fake_probe_ok(**kwargs):
    return [MCPToolDef(name="create_issue", description="", input_schema={})]


async def _fake_probe_fail(**kwargs):
    raise McpProbeError("MCP_SERVER_PROBE_FAILED", "connection refused")


@pytest.mark.asyncio
async def test_post_probes_persists_and_encrypts_token(monkeypatch) -> None:
    app, admin_headers, tenant_id = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        resp = await client.post(
            "/v1/mcp-servers",
            json={
                "name": "github", "transport": "streamable_http",
                "url": "https://mcp.example.com/mcp", "auth_type": "bearer",
                "token": "ghp_REALTOKEN", "timeout_s": 30.0,
            },
            headers=admin_headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["data"]["name"] == "github"
        assert body["data"]["tool_count"] == 1
        # token + ref must NOT appear in the response
        assert "ghp_REALTOKEN" not in resp.text
        assert "token_secret_ref" not in body["data"]
        # the raw token IS resolvable from the secret store under the tenant path
        ref_name = f"expert-work/tenant/{tenant_id}/mcp/github/token"
        assert await app.state.secret_store.get(ref_name) == "ghp_REALTOKEN"


@pytest.mark.asyncio
async def test_post_probe_failure_does_not_persist(monkeypatch) -> None:
    app, admin_headers, tenant_id = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_fail)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        resp = await client.post(
            "/v1/mcp-servers",
            json={"name": "down", "transport": "sse", "url": "https://down.example.com/sse", "auth_type": "none"},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "MCP_SERVER_PROBE_FAILED"
        # nothing persisted
        lst = await client.get("/v1/mcp-servers", headers=admin_headers)
        assert lst.json()["data"] == []


@pytest.mark.asyncio
async def test_post_ssrf_url_rejected(monkeypatch) -> None:
    app, admin_headers, _ = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        resp = await client.post(
            "/v1/mcp-servers",
            json={"name": "evil", "transport": "streamable_http", "url": "http://169.254.169.254/x", "auth_type": "none"},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "MCP_SERVER_INVALID_URL"


@pytest.mark.asyncio
async def test_non_admin_forbidden(monkeypatch) -> None:
    app, _, _ = await _make_app_with_admin()
    viewer_headers = await _seed_viewer_headers(app)
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        resp = await client.post(
            "/v1/mcp-servers",
            json={"name": "x", "transport": "sse", "url": "https://x.example.com/sse", "auth_type": "none"},
            headers=viewer_headers,
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_duplicate_name_conflict(monkeypatch) -> None:
    app, admin_headers, _ = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        payload = {"name": "github", "transport": "sse", "url": "https://x.example.com/sse", "auth_type": "none"}
        assert (await client.post("/v1/mcp-servers", json=payload, headers=admin_headers)).status_code == 201
        dup = await client.post("/v1/mcp-servers", json=payload, headers=admin_headers)
        assert dup.status_code == 409
        assert dup.json()["detail"]["code"] == "MCP_SERVER_DUPLICATE"


@pytest.mark.asyncio
async def test_delete_succeeds_when_unreferenced(monkeypatch) -> None:
    app, admin_headers, _ = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        await client.post(
            "/v1/mcp-servers",
            json={"name": "github", "transport": "sse", "url": "https://x.example.com/sse", "auth_type": "none"},
            headers=admin_headers,
        )
        d = await client.delete("/v1/mcp-servers/github", headers=admin_headers)
        assert d.status_code == 204
        lst = await client.get("/v1/mcp-servers", headers=admin_headers)
        assert lst.json()["data"] == []
```

Implement the `_make_app_with_admin()` / `_seed_viewer_headers()` helpers by mirroring the seed/header utilities in `test_platform_config_api.py` (system-admin/JWT seeding). These tests use the **in-memory** store path (no Docker) — ensure `create_app` falls back to in-memory stores in the test settings (the sibling API tests already do this). The DELETE test needs the agent-spec store present and empty (default).

- [ ] **Step 3: Run the API tests**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && uv run python -m pytest services/control-plane/tests/test_mcp_servers_api.py -q`
Expected: PASS. Debug fixture wiring against the sibling test until green.

- [ ] **Step 4: Commit**

```bash
git add services/control-plane/tests/test_mcp_servers_api.py
git commit -m "test(stream-v): /v1/mcp-servers API tests (probe, encryption, RBAC, dup, delete) (V-C)"
```

---

## Task 8: Full preflight + push + PR

- [ ] **Step 1: Run the affected test scope**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && uv run python -m pytest -m "not integration" services/control-plane packages/expert-work-protocol -q`
Expected: PASS, no regressions.

- [ ] **Step 2: Lint + type (CI scope)**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && uv run ruff check . && uv run ruff format --check .`
Expected: clean. Fix RUF/UP/B traps ([memory:feedback_ruff_strict_lint_traps]).

Run the CI mypy command exactly ([memory:reference_ci_lint_type_test_scopes] — note it covers `packages` + several `services/*/src` but NOT `control-plane/src`):
`cd /Users/mac/src/github/jone_qian/expert-work && uv run mypy packages services/audit-backup-worker/src services/event-log-archive-job/src services/orchestrator/src services/retention-cleanup-job/src`
Expected: clean (the protocol audit.py change is in-scope here).

Run: `cd /Users/mac/src/github/jone_qian/expert-work && pre-commit run --all-files` (if installed).

- [ ] **Step 3: Confirm no uv.lock drift**

Run: `git status --short uv.lock` — expect empty.

- [ ] **Step 4: Push + PR**

```bash
cd /Users/mac/src/github/jone_qian/expert-work
git push -u origin stream-v/c-api
gh pr create --base main --head stream-v/c-api \
  --title "feat(stream-v): PR C — MCP Server Registration API (CRUD + connect-probe)" \
  --body "Implements Stream V-C per docs/streams/STREAM-V-DESIGN.md (Mini-ADR V-5 API+probe, V-2 token→encrypted ref, V-7 SSRF, V-10 audit).

## What
- \`/v1/mcp-servers\` router: POST (probe-then-persist), GET (list), PATCH (re-probe on url/token change), DELETE (refuses if a manifest references the server).
- Connect-probe (\`mcp_probe.py\`): connects via the orchestrator's remote MCP client + \`list_tools\` under a timeout; SSRF-checked; never logs the token.
- Token stored ONLY as a \`secret://\` ref in the encrypted secret store; the value is resolvable but never in the DB/response/audit.
- RBAC: new \`mcp_server\` resource (ADMIN rwd, OPERATOR/VIEWER r).
- Audit: \`MCP_SERVER_CREATE/UPDATE/DELETE\` + \`tenant_mcp_server\` resource type (both Literals).
- Manifest reference-check helper (forward-compatible with V-E's \`MCPToolSpec.servers\`).

## Scope
API + probe + RBAC + audit + wiring. Runtime per-tenant pool (V-D), schema (V-E), discovery+UI (V-F/G) are separate PRs.

## Tests
- Unit: probe (success/no-auth/SSRF/connect-fail/always-close), reference-check helper, RBAC matrix, audit enums.
- API: probe→persist→encrypt (token never leaks), probe-fail→no persist, SSRF 422, non-admin 403, dup 409, delete-unreferenced 204.

🤖 Generated with Claude Code"
```

- [ ] **Step 5: Poll CI to green** and fix any failures before handing off to V-D.

---

## Self-Review (plan author)

**Spec coverage (STREAM-V-DESIGN.md):** V-5 (CRUD + probe + DELETE ref-check) → Tasks 4,5,6,7 ✓. V-2 (token→encrypted ref) → Task 5 POST/PATCH ✓. V-7 (SSRF at registration+probe) → Tasks 3,5 ✓. V-10 (audit actions + resource type, both Literals) → Task 2 ✓. RBAC gap (not in design but required for the endpoints to authorize) → Task 1 ✓. Discovery endpoints `/available` + `/{name}/tools` are correctly deferred to V-F.

**Deviations from design (intentional, noted):** path uses `{name}` not `{id}` (the V-B store is name-keyed; manifests reference by name). PATCH re-probes when url/token change (upholds "probe before persist"). DELETE ref-check is a dormant-but-tested pure helper because `MCPToolSpec.servers` lands in V-E.

**Placeholder scan:** The app.py wiring (Task 6) and API-test seed helpers (Task 7) intentionally instruct "mirror the exact existing pattern (service_accounts / platform_config) and grep for the real attribute names" rather than hardcoding line numbers — app.py is large and its exact construction code is best copied from the live sibling, not transcribed here. Every NEW module (probe, router, reference helper) and every enum/RBAC edit has complete code.

**Type consistency:** `probe_remote_mcp(*, name, transport, url, bearer_token, timeout_s, client_factory)`, `McpProbeError(code, message)`, `manifest_references_server(spec_json, server_name)`, `build_mcp_servers_router()`, `app.state.tenant_mcp_server_store` are used consistently across modules and tests.

**Known risks to watch during execution:** (1) `MCPServerConfig.__post_init__` bearer validation vs the probe's sentinel token_ref — Task 3 Step 3 calls this out with a fallback. (2) the exact `agent_spec_store` app.state attribute + `list_by_tenant` signature — Task 5/6 say grep to confirm. (3) `SecretStore`/`AuditLogger`/`current_trace_id_hex` import paths — Task 5 says adapt to the sibling's actual imports.
