# Stream V-D — Per-Tenant Remote MCP Pool (runtime wiring) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`).

**Goal:** Make a tenant's registered remote MCP servers usable by its agents at runtime — read the registry, resolve each token from the encrypted secret store, build/cache a per-tenant remote `MCPServerPool`, union it with the platform (ops-JSON) pool in `_register_mcp`, and invalidate the cache (pool + dependent built-agents) when the registry changes.

**Architecture:** A control-plane `TenantMcpPoolService` owns `dict[tenant_id → MCPServerPool]`, built lazily from `TenantMcpServerStore` + `SecretStore` via the existing `_build_mcp_client`, reused across agent builds, closed at shutdown. `ToolEnv` gains a `tenant_mcp_pool` field; `_register_mcp` registers the platform pool (filtered by the per-tenant allowlist) **and** the tenant pool (no allowlist filter — the tenant owns these), both filtered by the agent's `allow_tools`. The agent builders fetch the tenant pool via a `Callable` provider (orchestrator↔control-plane decoupling, mirroring `mcp_allowlist_provider`). The V-C registry mutations call `pool_service.invalidate(tenant_id)` + `agent_runtime.invalidate_tenant(tenant_id)` so cached agents rebuild against the fresh pool.

**Tech Stack:** Python 3.12, asyncio, FastAPI, pytest. No new deps.

**Scope (V-D only):** runtime pool + union + per-tenant cache/invalidation + builder wiring + V-C invalidation hooks. NOT: `MCPToolSpec.servers` per-agent server *selection* (that's V-E — V-D makes ALL enabled tenant servers available, filtered only by the existing `allow_tools`); discovery endpoints/UI (V-F/G).

**Branch:** `stream-v/d-runtime` (off `main`, after V-C merged).

**CodeQL traps to avoid up front** ([memory:feedback_codeql_protocol_ellipsis], [memory:feedback_codeql_log_injection_request_taint]): never use a standalone `...` in a `Protocol`/ABC method body (use a docstring); never pass tenant-derived values (server name/url/transport, tenant_id) into a `logger.*` call (log static messages or non-tainted constants only).

**Key facts (verified 2026-06-03, file:line):**
- `ToolEnv` (frozen dataclass): `services/orchestrator/src/orchestrator/tools/assembly.py:59` — has `mcp_pool: MCPServerPool | None`, `mcp_allowlist: tuple[str,...]`.
- `_register_mcp(registry, entry, env)`: `assembly.py:313` — iterates `env.mcp_pool.names()`, skips names not in `env.mcp_allowlist` (when non-empty), calls `register_mcp_tools(server_name=, client=, registry=, allow_tools=set(entry.allow_tools) or None)`.
- `register_mcp_tools(*, server_name, client, registry, content_char_cap=, allow_tools=)`: `services/orchestrator/src/orchestrator/tools/mcp.py:665` — `list_tools()` then registers each as `MCPTool` namespaced `mcp:<server>.<tool>`; returns the registered names.
- `MCPServerPool`: `mcp.py:613` — `async add(name, client)` (raises `MCPServerPoolLimitError` past `max_servers=5`, `ValueError` on dup name), `get(name)`, `names()`, `async close_all()`.
- `_build_mcp_client(config, *, secret_store)`: `services/control-plane/src/control_plane/runtime.py:558` — builds Sse/StreamableHttp/Stdio client, resolves bearer `auth_config["token_ref"]` via `secret_store.get(parse_secret_ref(...))` → `Authorization` header, `await .start()`. **Reuse this verbatim.**
- `make_agent_builder(...)`: `runtime.py:167` — `_build(spec, *, tenant_id)` closure; already does `build_tool_env = replace(tool_env, mcp_allowlist=...)` when `mcp_allowlist_provider` returns non-empty (`runtime.py:220-231`); passes `tool_env=build_tool_env` to `build_agent`.
- `make_mcp_allowlist_provider(service)`: `runtime.py:477` — the closure-provider template to mirror.
- `make_child_agent_builder`: `services/control-plane/src/control_plane/subagent_runtime.py:45` — second build site (also needs the tenant pool).
- `AgentRuntime`: `runtime.py:~110-135` — `_cache: dict[(tenant_id, name, version) → BuiltAgent]`, `get_agent(...)` builds on miss. **No invalidate method yet — add one.**
- Platform pool construction: `build_mcp_pool(config_file, *, secret_store, client_factory=None)` ctx mgr `runtime.py:607`; wired in `app.py:662` inside the lifespan `AsyncExitStack`.
- `TenantMcpServerStore.list_for_tenant(*, tenant_id) -> list[TenantMcpServerRecord]`; record fields name/transport/url/auth_type/token_secret_ref/timeout_s/enabled. `from expert_work.persistence import TenantMcpServerStore`.
- `MCPServerConfig` (orchestrator): `name/transport/url/headers/auth_type/auth_config/timeout_s`; bearer requires `auth_config["token_ref"]`.
- V-C router: `services/control-plane/src/control_plane/api/mcp_servers.py` — POST/PATCH/DELETE handlers (the invalidation hook sites).
- Tests to mirror: `services/orchestrator/tests/test_tool_assembly.py` (`MCPServerPool`/`RecordingMCPClient`/`build_tool_registry`/`ToolEnv` fixtures), control-plane runtime tests.

---

## File Structure

**Create:**
- `services/control-plane/src/control_plane/tenant_mcp_pool.py` — `TenantMcpPoolService` (cache + build-from-registry + invalidate + close_all) + `TenantMcpPoolProvider` type.
- `services/control-plane/tests/test_tenant_mcp_pool.py` — service unit tests (RecordingMCPClient via injected client factory).

**Modify:**
- `services/orchestrator/src/orchestrator/tools/assembly.py` — `ToolEnv.tenant_mcp_pool` field + `_register_mcp` union logic.
- `services/orchestrator/tests/test_tool_assembly.py` — union + collision tests.
- `services/control-plane/src/control_plane/runtime.py` — `make_agent_builder` gains `tenant_mcp_pool_provider`; `AgentRuntime.invalidate_tenant`.
- `services/control-plane/src/control_plane/subagent_runtime.py` — `make_child_agent_builder` gains the same provider wiring.
- `services/control-plane/src/control_plane/app.py` — construct `TenantMcpPoolService`, wire provider into both builders, app.state, lifespan shutdown `close_all`, pass service+runtime to the mcp_servers router.
- `services/control-plane/src/control_plane/api/mcp_servers.py` — POST/PATCH/DELETE call `pool_service.invalidate(tenant_id)` + `agent_runtime.invalidate_tenant(tenant_id)`.
- `services/control-plane/tests/test_mcp_servers_api.py` — assert invalidation is called on mutations.

---

## Task 1: `ToolEnv.tenant_mcp_pool` + `_register_mcp` union

**Files:**
- Modify: `services/orchestrator/src/orchestrator/tools/assembly.py`
- Modify: `services/orchestrator/tests/test_tool_assembly.py`

- [ ] **Step 1: Write the failing tests**

Add to `services/orchestrator/tests/test_tool_assembly.py` (reuse the existing `MCPServerPool`/`RecordingMCPClient`/`MCPToolDef`/`build_tool_registry`/`ToolEnv` imports already in that file):

```python
@pytest.mark.asyncio
async def test_tenant_pool_tools_registered_alongside_platform() -> None:
    platform = MCPServerPool()
    await platform.add(
        "ops", RecordingMCPClient(tools=(MCPToolDef(name="deploy", description="", input_schema={}),))
    )
    tenant = MCPServerPool()
    await tenant.add(
        "github", RecordingMCPClient(tools=(MCPToolDef(name="create_issue", description="", input_schema={}),))
    )
    registry = await build_tool_registry(
        [MCPToolSpec()], tool_env=ToolEnv(mcp_pool=platform, tenant_mcp_pool=tenant)
    )
    assert registry.get("mcp:ops.deploy") is not None
    assert registry.get("mcp:github.create_issue") is not None


@pytest.mark.asyncio
async def test_tenant_pool_not_filtered_by_platform_allowlist() -> None:
    # The allowlist gates the PLATFORM pool only; tenant servers are the
    # tenant's own and are always visible.
    platform = MCPServerPool()
    await platform.add(
        "ops", RecordingMCPClient(tools=(MCPToolDef(name="deploy", description="", input_schema={}),))
    )
    tenant = MCPServerPool()
    await tenant.add(
        "github", RecordingMCPClient(tools=(MCPToolDef(name="create_issue", description="", input_schema={}),))
    )
    registry = await build_tool_registry(
        [MCPToolSpec()],
        tool_env=ToolEnv(mcp_pool=platform, tenant_mcp_pool=tenant, mcp_allowlist=("ops",)),
    )
    assert registry.get("mcp:ops.deploy") is not None  # platform, on allowlist
    assert registry.get("mcp:github.create_issue") is not None  # tenant, not gated


@pytest.mark.asyncio
async def test_allow_tools_filters_tenant_pool_too() -> None:
    tenant = MCPServerPool()
    await tenant.add(
        "github",
        RecordingMCPClient(tools=(
            MCPToolDef(name="create_issue", description="", input_schema={}),
            MCPToolDef(name="delete_repo", description="", input_schema={}),
        )),
    )
    registry = await build_tool_registry(
        [MCPToolSpec(allow_tools=["create_issue"])],
        tool_env=ToolEnv(tenant_mcp_pool=tenant),
    )
    assert registry.get("mcp:github.create_issue") is not None
    assert registry.get("mcp:github.delete_repo") is None


@pytest.mark.asyncio
async def test_tenant_pool_only_no_platform_pool_ok() -> None:
    # mcp tool declared with only a tenant pool (no platform pool) must work.
    tenant = MCPServerPool()
    await tenant.add(
        "github", RecordingMCPClient(tools=(MCPToolDef(name="create_issue", description="", input_schema={}),))
    )
    registry = await build_tool_registry([MCPToolSpec()], tool_env=ToolEnv(tenant_mcp_pool=tenant))
    assert registry.get("mcp:github.create_issue") is not None


@pytest.mark.asyncio
async def test_name_collision_platform_wins() -> None:
    platform = MCPServerPool()
    await platform.add(
        "github", RecordingMCPClient(tools=(MCPToolDef(name="from_platform", description="", input_schema={}),))
    )
    tenant = MCPServerPool()
    await tenant.add(
        "github", RecordingMCPClient(tools=(MCPToolDef(name="from_tenant", description="", input_schema={}),))
    )
    registry = await build_tool_registry(
        [MCPToolSpec()], tool_env=ToolEnv(mcp_pool=platform, tenant_mcp_pool=tenant)
    )
    # platform registered first; the colliding tenant server is skipped.
    assert registry.get("mcp:github.from_platform") is not None
    assert registry.get("mcp:github.from_tenant") is None


@pytest.mark.asyncio
async def test_mcp_declared_but_no_pools_raises() -> None:
    with pytest.raises(AgentFactoryError, match="MCP server pool"):
        await build_tool_registry([MCPToolSpec()], tool_env=ToolEnv())
```

Note: the last test replaces/duplicates the existing `test_mcp_missing_pool_raises` — keep the existing one; this asserts the same with both pools None.

- [ ] **Step 2: Run to confirm failure**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && uv run python -m pytest services/orchestrator/tests/test_tool_assembly.py -q -k "tenant_pool or allow_tools_filters_tenant or name_collision or no_pools"`
Expected: FAIL — `ToolEnv` has no `tenant_mcp_pool` (TypeError on unexpected kwarg).

- [ ] **Step 3: Add the field + union logic**

In `services/orchestrator/src/orchestrator/tools/assembly.py`:

(a) Add the field to `ToolEnv` (right after `mcp_allowlist`):
```python
    #: Stream V (Mini-ADR V-4) — the calling tenant's own registered REMOTE
    #: MCP servers (sse / streamable_http), built per-tenant by the control
    #: plane from ``tenant_mcp_server`` + the encrypted secret store. Unlike
    #: ``mcp_pool`` (the operator-controlled platform pool, gated by
    #: ``mcp_allowlist``), this pool is the tenant's own and is never gated by
    #: the allowlist. ``None`` → the tenant registered no remote servers.
    tenant_mcp_pool: MCPServerPool | None = None
```

(b) Rewrite `_register_mcp` to register both pools. Replace the function body:
```python
async def _register_mcp(registry: ToolRegistry, entry: MCPToolSpec, env: ToolEnv) -> None:
    if env.mcp_pool is None and env.tenant_mcp_pool is None:
        raise AgentFactoryError(
            "'mcp' tool declared but no MCP server pool is configured "
            "(ToolEnv.mcp_pool / ToolEnv.tenant_mcp_pool)"
        )
    allow = set(entry.allow_tools) or None
    registered_servers: set[str] = set()

    # Platform pool — gated by the per-tenant allowlist (Mini-ADR O-14).
    if env.mcp_pool is not None:
        server_allow = set(env.mcp_allowlist) or None
        for server_name in env.mcp_pool.names():
            if server_allow is not None and server_name not in server_allow:
                continue
            client = env.mcp_pool.get(server_name)
            if client is None:  # pragma: no cover - name came from names()
                continue
            await register_mcp_tools(
                server_name=server_name, client=client, registry=registry, allow_tools=allow
            )
            registered_servers.add(server_name)

    # Tenant pool — the tenant's own remote servers; never gated by the
    # allowlist. On a name collision the platform server wins (already
    # registered above); skip the tenant duplicate to avoid a double
    # ``mcp:<name>.*`` registration.
    if env.tenant_mcp_pool is not None:
        for server_name in env.tenant_mcp_pool.names():
            if server_name in registered_servers:
                continue
            client = env.tenant_mcp_pool.get(server_name)
            if client is None:  # pragma: no cover
                continue
            await register_mcp_tools(
                server_name=server_name, client=client, registry=registry, allow_tools=allow
            )
            registered_servers.add(server_name)
```

- [ ] **Step 4: Run to confirm pass**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && uv run python -m pytest services/orchestrator/tests/test_tool_assembly.py -q`
Expected: PASS (new + existing MCP tests).

- [ ] **Step 5: Lint/type + commit**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && uv run ruff check services/orchestrator && uv run ruff format --check services/orchestrator && uv run mypy services/orchestrator/src`
Expected: clean.

```bash
git add services/orchestrator/src/orchestrator/tools/assembly.py services/orchestrator/tests/test_tool_assembly.py
git commit -m "feat(stream-v): ToolEnv.tenant_mcp_pool + _register_mcp union (V-D)"
```

---

## Task 2: `TenantMcpPoolService` (build-from-registry + cache + invalidate)

**Files:**
- Create: `services/control-plane/src/control_plane/tenant_mcp_pool.py`
- Create: `services/control-plane/tests/test_tenant_mcp_pool.py`

- [ ] **Step 1: Write the failing tests**

Create `services/control-plane/tests/test_tenant_mcp_pool.py`:

```python
"""Unit tests for the per-tenant remote MCP pool service (Stream V-D)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from control_plane.tenant_mcp_pool import TenantMcpPoolService
from expert_work.persistence import InMemoryTenantMcpServerStore
from orchestrator.tools.mcp import MCPServerConfig, MCPToolDef, RecordingMCPClient


def _client_factory_spy(calls: list[str]):
    async def _factory(config: MCPServerConfig):
        calls.append(config.name)
        return RecordingMCPClient(
            tools=(MCPToolDef(name="t", description="", input_schema={}),)
        )

    return _factory


async def _seed(store: InMemoryTenantMcpServerStore, tenant_id, name="github", enabled=True):
    rec = await store.create(
        tenant_id=tenant_id, name=name, transport="streamable_http",
        url="https://mcp.example.com/mcp", auth_type="none",
        token_secret_ref=None, timeout_s=30.0, created_by="a@x",
    )
    if not enabled:
        from expert_work.protocol import TenantMcpServerPatch
        await store.update(tenant_id=tenant_id, name=name, patch=TenantMcpServerPatch(enabled=False))
    return rec


@pytest.mark.asyncio
async def test_builds_pool_from_enabled_servers() -> None:
    store = InMemoryTenantMcpServerStore()
    tid = uuid4()
    await _seed(store, tid, "github")
    calls: list[str] = []
    svc = TenantMcpPoolService(store=store, secret_store=None, client_factory=_client_factory_spy(calls))
    pool = await svc.get_or_build(tid)
    assert pool.names() == ["github"]
    assert calls == ["github"]


@pytest.mark.asyncio
async def test_disabled_servers_excluded() -> None:
    store = InMemoryTenantMcpServerStore()
    tid = uuid4()
    await _seed(store, tid, "github", enabled=True)
    await _seed(store, tid, "linear", enabled=False)
    svc = TenantMcpPoolService(store=store, secret_store=None, client_factory=_client_factory_spy([]))
    pool = await svc.get_or_build(tid)
    assert pool.names() == ["github"]


@pytest.mark.asyncio
async def test_second_call_returns_cached_pool_no_rebuild() -> None:
    store = InMemoryTenantMcpServerStore()
    tid = uuid4()
    await _seed(store, tid, "github")
    calls: list[str] = []
    svc = TenantMcpPoolService(store=store, secret_store=None, client_factory=_client_factory_spy(calls))
    p1 = await svc.get_or_build(tid)
    p2 = await svc.get_or_build(tid)
    assert p1 is p2
    assert calls == ["github"]  # built once


@pytest.mark.asyncio
async def test_invalidate_closes_and_rebuilds() -> None:
    store = InMemoryTenantMcpServerStore()
    tid = uuid4()
    await _seed(store, tid, "github")
    calls: list[str] = []
    svc = TenantMcpPoolService(store=store, secret_store=None, client_factory=_client_factory_spy(calls))
    p1 = await svc.get_or_build(tid)
    await svc.invalidate(tid)
    p2 = await svc.get_or_build(tid)
    assert p1 is not p2
    assert calls == ["github", "github"]  # rebuilt


@pytest.mark.asyncio
async def test_empty_when_no_servers() -> None:
    store = InMemoryTenantMcpServerStore()
    svc = TenantMcpPoolService(store=store, secret_store=None, client_factory=_client_factory_spy([]))
    pool = await svc.get_or_build(uuid4())
    assert pool.names() == []


@pytest.mark.asyncio
async def test_close_all_clears_cache() -> None:
    store = InMemoryTenantMcpServerStore()
    tid = uuid4()
    await _seed(store, tid, "github")
    svc = TenantMcpPoolService(store=store, secret_store=None, client_factory=_client_factory_spy([]))
    await svc.get_or_build(tid)
    await svc.close_all()
    # after close_all a fresh build is required again (cache cleared)
    assert svc._pools == {}  # noqa: SLF001 — test inspects internal cache
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && uv run python -m pytest services/control-plane/tests/test_tenant_mcp_pool.py -q`
Expected: FAIL — `ModuleNotFoundError: control_plane.tenant_mcp_pool`.

- [ ] **Step 3: Implement the service**

Create `services/control-plane/src/control_plane/tenant_mcp_pool.py`:

```python
"""Per-tenant remote MCP server pool — Stream V-D (Mini-ADR V-4).

A tenant's registered remote MCP servers (``tenant_mcp_server``) are built
into a per-tenant :class:`MCPServerPool` on first use and reused across agent
builds. The pool is invalidated (closed + dropped) when the tenant's registry
changes (the registration API calls :meth:`invalidate`) and all pools are
closed at app shutdown (:meth:`close_all`).

Decoupling: the orchestrator never imports this — the agent builder receives a
``Callable`` provider bound to this service (mirrors ``mcp_allowlist_provider``).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from uuid import UUID

from orchestrator.tools.mcp import MCPClient, MCPServerConfig, MCPServerPool

from expert_work.persistence import TenantMcpServerStore
from expert_work.protocol import TenantMcpServerRecord
from expert_work.runtime.secret_store import SecretStore

logger = logging.getLogger("expert_work.control_plane.tenant_mcp_pool")

# Provider handed to the agent builder: tenant_id -> that tenant's remote pool.
TenantMcpPoolProvider = Callable[[UUID], Awaitable[MCPServerPool]]

# Factory so tests can inject a RecordingMCPClient instead of real transports.
McpClientFactory = Callable[[MCPServerConfig], Awaitable[MCPClient]]


def _record_to_config(record: TenantMcpServerRecord) -> MCPServerConfig:
    """Map a registry record to an orchestrator :class:`MCPServerConfig`.

    Bearer auth carries the ``token_ref`` so the client builder resolves it
    via the SecretStore (the value never lives on the config — Mini-ADR U-11).
    """
    auth_config: dict[str, str] = {}
    if record.auth_type == "bearer" and record.token_secret_ref is not None:
        auth_config["token_ref"] = record.token_secret_ref
    return MCPServerConfig(
        name=record.name,
        transport=record.transport,
        url=record.url,
        auth_type=record.auth_type,
        auth_config=auth_config,
        timeout_s=record.timeout_s,
    )


class TenantMcpPoolService:
    """Caches one :class:`MCPServerPool` per tenant, built from the registry."""

    def __init__(
        self,
        *,
        store: TenantMcpServerStore,
        secret_store: SecretStore | None,
        client_factory: McpClientFactory,
    ) -> None:
        self._store = store
        self._secret_store = secret_store
        self._client_factory = client_factory
        self._pools: dict[UUID, MCPServerPool] = {}
        self._lock = asyncio.Lock()

    async def get_or_build(self, tenant_id: UUID) -> MCPServerPool:
        """Return the tenant's remote pool, building (and caching) on miss.

        A server that fails to connect is skipped (logged, no tenant-derived
        values) so one bad server cannot break the whole agent build.
        """
        async with self._lock:
            cached = self._pools.get(tenant_id)
            if cached is not None:
                return cached
            pool = MCPServerPool()
            records = await self._store.list_for_tenant(tenant_id=tenant_id)
            for record in records:
                if not record.enabled:
                    continue
                try:
                    client = await self._client_factory(_record_to_config(record))
                    await pool.add(record.name, client)
                except Exception:  # noqa: BLE001 — one bad server must not fail the build
                    logger.warning("tenant_mcp_pool.server_build_failed")
            self._pools[tenant_id] = pool
            return pool

    async def invalidate(self, tenant_id: UUID) -> None:
        """Close + drop the tenant's cached pool (next build rebuilds it)."""
        async with self._lock:
            pool = self._pools.pop(tenant_id, None)
        if pool is not None:
            try:
                await pool.close_all()
            except Exception:  # noqa: BLE001 — best-effort teardown
                logger.warning("tenant_mcp_pool.invalidate_close_failed")

    async def close_all(self) -> None:
        """Close every cached pool (app shutdown)."""
        async with self._lock:
            pools = list(self._pools.values())
            self._pools.clear()
        for pool in pools:
            try:
                await pool.close_all()
            except Exception:  # noqa: BLE001 — best-effort teardown
                logger.warning("tenant_mcp_pool.close_all_failed")
```

NOTE: confirm `RecordingMCPClient`, `MCPClient`, `MCPServerConfig`, `MCPServerPool` are exported from `orchestrator.tools.mcp` (they are). Confirm `SecretStore` import path is `expert_work.runtime.secret_store` (V-C used this). If `BLE001` isn't enabled in ruff, drop the `# noqa` (V-C's probe found BLE001 not enabled — verify and remove unused noqa to avoid RUF100).

- [ ] **Step 4: Run to confirm pass + lint**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && uv run python -m pytest services/control-plane/tests/test_tenant_mcp_pool.py -q`
Expected: PASS (6 tests).
Run: `uv run ruff check services/control-plane/src/control_plane/tenant_mcp_pool.py services/control-plane/tests/test_tenant_mcp_pool.py && uv run ruff format --check ... && uv run mypy services/control-plane/src/control_plane/tenant_mcp_pool.py`
Expected: clean (remove any unused `# noqa`).

- [ ] **Step 5: Commit**

```bash
git add services/control-plane/src/control_plane/tenant_mcp_pool.py services/control-plane/tests/test_tenant_mcp_pool.py
git commit -m "feat(stream-v): TenantMcpPoolService (per-tenant remote pool cache) (V-D)"
```

---

## Task 3: `AgentRuntime.invalidate_tenant`

**Files:**
- Modify: `services/control-plane/src/control_plane/runtime.py`
- Modify/Create test: `services/control-plane/tests/test_agent_runtime.py` (or wherever AgentRuntime is tested — grep `AgentRuntime` in tests)

- [ ] **Step 1: Write the failing test**

Grep `grep -rln "AgentRuntime" services/control-plane/tests/` to find the test file; add (adapt the builder fixture to the existing test's style — it builds an `AgentRuntime` with a stub `agent_builder`):

```python
@pytest.mark.asyncio
async def test_invalidate_tenant_drops_only_that_tenants_cached_agents() -> None:
    from uuid import uuid4
    builds: list[tuple] = []

    async def _builder(spec, *, tenant_id=None):  # minimal stub
        builds.append((tenant_id, spec.name if hasattr(spec, "name") else None))
        return object()  # stand-in BuiltAgent

    runtime = AgentRuntime(agent_builder=_builder)  # match real constructor
    a, b = uuid4(), uuid4()
    spec = _make_spec(name="x", version="1")  # reuse the file's spec helper
    await runtime.get_agent(tenant_id=a, name="x", version="1", spec=spec)
    await runtime.get_agent(tenant_id=b, name="x", version="1", spec=spec)
    assert len(builds) == 2
    runtime.invalidate_tenant(a)
    # tenant a rebuilds; tenant b still cached
    await runtime.get_agent(tenant_id=a, name="x", version="1", spec=spec)
    await runtime.get_agent(tenant_id=b, name="x", version="1", spec=spec)
    assert len(builds) == 3  # only a rebuilt
```

(If the existing test file already has spec/builder helpers, reuse them; otherwise mirror them minimally.)

- [ ] **Step 2: Run to confirm failure**

Run the new test → FAIL (`AgentRuntime` has no `invalidate_tenant`).

- [ ] **Step 3: Implement**

In `services/control-plane/src/control_plane/runtime.py`, add a method to `AgentRuntime` (next to `get_agent`):
```python
    def invalidate_tenant(self, tenant_id: UUID) -> None:
        """Drop every cached built-agent for ``tenant_id``.

        Called when the tenant's MCP server registry changes so the next run
        rebuilds the agent against the refreshed tenant MCP pool (Stream V-D).
        The cache key is ``(tenant_id, name, version)``.
        """
        for key in [k for k in self._cache if k[0] == tenant_id]:
            del self._cache[key]
```

(Verify the cache-key tuple order is `(tenant_id, name, version)` — it is per `get_agent`.)

- [ ] **Step 4: Run to confirm pass + commit**

Run the test → PASS. `uv run ruff check services/control-plane/src/control_plane/runtime.py && uv run mypy services/control-plane/src/control_plane/runtime.py` (best-effort; control-plane/src not in CI mypy gate).

```bash
git add services/control-plane/src/control_plane/runtime.py services/control-plane/tests/test_agent_runtime.py
git commit -m "feat(stream-v): AgentRuntime.invalidate_tenant (V-D)"
```

---

## Task 4: Wire the tenant pool provider into the agent builders

**Files:**
- Modify: `services/control-plane/src/control_plane/runtime.py` (`make_agent_builder`)
- Modify: `services/control-plane/src/control_plane/subagent_runtime.py` (`make_child_agent_builder`)
- Modify: tests for the builders (grep `make_agent_builder` in tests)

- [ ] **Step 1: Write the failing test**

In the builder test file (grep `make_agent_builder` under `services/control-plane/tests/`), add a test that a `tenant_mcp_pool_provider` is consulted and its pool reaches the built ToolEnv. Mirror how the existing `mcp_allowlist_provider` test asserts (it likely checks the `build_agent` call captured the env). Minimal shape:

```python
@pytest.mark.asyncio
async def test_agent_builder_sets_tenant_mcp_pool_from_provider(monkeypatch) -> None:
    from orchestrator.tools.mcp import MCPServerPool
    tenant_pool = MCPServerPool()
    captured = {}

    async def fake_build_agent(spec, **kwargs):
        captured["tool_env"] = kwargs.get("tool_env")
        return object()

    monkeypatch.setattr("control_plane.runtime.build_agent", fake_build_agent)

    async def provider(tid):
        return tenant_pool

    builder = make_agent_builder(
        secret_store=_StubSecretStore(), checkpointer=_stub_checkpointer(),
        tool_env=ToolEnv(), tenant_mcp_pool_provider=provider,
    )
    await builder(_make_spec(), tenant_id=uuid4())
    assert captured["tool_env"].tenant_mcp_pool is tenant_pool
```

(Adapt stub names to the existing test module's helpers.)

- [ ] **Step 2: Run → fail** (`make_agent_builder` has no `tenant_mcp_pool_provider`).

- [ ] **Step 3: Implement in `make_agent_builder`**

Add the parameter to the signature:
```python
    tenant_mcp_pool_provider: TenantMcpPoolProvider | None = None,
```
(import `from control_plane.tenant_mcp_pool import TenantMcpPoolProvider`).

In `_build`, after the existing `mcp_allowlist` `replace(...)` block, add:
```python
        # Stream V (Mini-ADR V-4) — attach the tenant's own remote MCP pool.
        if tenant_mcp_pool_provider is not None and tenant_id is not None:
            tenant_pool = await tenant_mcp_pool_provider(tenant_id)
            if tenant_pool.names():
                base_env = build_tool_env if build_tool_env is not None else ToolEnv()
                build_tool_env = replace(base_env, tenant_mcp_pool=tenant_pool)
```
(Ensure `ToolEnv` is imported in runtime.py — it is, since tool_env is typed. `replace` from dataclasses is already imported per the allowlist block.)

- [ ] **Step 4: Mirror in `make_child_agent_builder`** (`subagent_runtime.py`)

Add the same `tenant_mcp_pool_provider` param and the same `replace(..., tenant_mcp_pool=...)` in the child `_build` where it constructs/forwards the ToolEnv. (Read the function first; it builds child agents with a tenant_id in scope — apply the identical pattern. If child builds don't currently apply `mcp_allowlist`, still add the tenant pool the same way so delegated sub-agents can use tenant MCP servers.)

- [ ] **Step 5: Run tests + lint + commit**

Run the builder tests → PASS. ruff/mypy on the two files → clean.
```bash
git add services/control-plane/src/control_plane/runtime.py services/control-plane/src/control_plane/subagent_runtime.py services/control-plane/tests/
git commit -m "feat(stream-v): wire tenant_mcp_pool_provider into agent + child builders (V-D)"
```

---

## Task 5: App wiring (service construction + provider + shutdown)

**Files:**
- Modify: `services/control-plane/src/control_plane/app.py`

- [ ] **Step 1: Construct the service + factory**

In `app.py`, where the platform `mcp_pool` + `secret_store` + `tenant_mcp_server_store` are available (in `create_app`/lifespan), construct:
```python
from control_plane.runtime import _build_mcp_client  # the canonical remote-client builder
from control_plane.tenant_mcp_pool import TenantMcpPoolService

async def _tenant_mcp_client_factory(cfg):
    return await _build_mcp_client(cfg, secret_store=resolved_secret_store)

tenant_mcp_pool_service = TenantMcpPoolService(
    store=resolved_tenant_mcp_server_store,
    secret_store=resolved_secret_store,
    client_factory=_tenant_mcp_client_factory,
)
```
Place it so `resolved_tenant_mcp_server_store` (added in V-C) and `resolved_secret_store` are in scope. (Grep `resolved_secret_store` and `tenant_mcp_server_store` in app.py to find the spot.)

- [ ] **Step 2: Build the provider + pass to both builders**

```python
async def _tenant_mcp_pool_provider(tenant_id):
    return await tenant_mcp_pool_service.get_or_build(tenant_id)
```
Pass `tenant_mcp_pool_provider=_tenant_mcp_pool_provider` into the `make_agent_builder(...)` call AND the `make_child_agent_builder(...)` call (grep both call sites in app.py).

- [ ] **Step 3: app.state + shutdown close_all**

Add `app.state.tenant_mcp_pool_service = tenant_mcp_pool_service`. Register shutdown cleanup: in the lifespan teardown (the same `AsyncExitStack`/`finally` that closes the platform pool, or a shutdown event handler), call `await tenant_mcp_pool_service.close_all()`. Mirror how the platform `mcp_pool` is torn down (it's in the lifespan stack). The cleanest: `stack.push_async_callback(tenant_mcp_pool_service.close_all)` right after constructing the service inside the lifespan stack.

- [ ] **Step 4: Verify app boots + routes intact**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && uv run python -c "import control_plane.app"` and an existing app smoke test if present. `uv run ruff check services/control-plane/src/control_plane/app.py && uv run ruff format --check ...`.

- [ ] **Step 5: Commit**

```bash
git add services/control-plane/src/control_plane/app.py
git commit -m "feat(stream-v): construct + wire TenantMcpPoolService into app lifespan + builders (V-D)"
```

---

## Task 6: V-C registry mutations invalidate the pool + agent cache

**Files:**
- Modify: `services/control-plane/src/control_plane/api/mcp_servers.py`
- Modify: `services/control-plane/src/control_plane/app.py` (pass the service + runtime into the router if needed)
- Modify: `services/control-plane/tests/test_mcp_servers_api.py`

- [ ] **Step 1: Decide the wiring + write the failing test**

The router handlers must, after a successful POST/PATCH/DELETE, call `tenant_mcp_pool_service.invalidate(tenant_id)` and `agent_runtime.invalidate_tenant(tenant_id)`. Both are on `app.state` (`tenant_mcp_pool_service` from Task 5; the agent runtime — grep app.state for its attribute name, likely `agent_runtime`). Add DI accessors:
```python
def _get_tenant_mcp_pool_service(request: Request):  # type: ignore[no-untyped-def]
    return getattr(request.app.state, "tenant_mcp_pool_service", None)

def _get_agent_runtime(request: Request):  # type: ignore[no-untyped-def]
    return getattr(request.app.state, "agent_runtime", None)
```
Add a small helper invoked at the end of POST/PATCH/DELETE:
```python
async def _invalidate_tenant_mcp(pool_service, agent_runtime, tenant_id) -> None:
    if pool_service is not None:
        await pool_service.invalidate(tenant_id)
    if agent_runtime is not None:
        agent_runtime.invalidate_tenant(tenant_id)
```
Add these accessors as `Depends(...)` params to the three mutating handlers and call the helper after the audit emit.

Add an API test (extend `test_mcp_servers_api.py`) that registers a fake pool service + agent runtime on `app.state` (or spies) and asserts `invalidate`/`invalidate_tenant` are called on POST and DELETE. Simplest: monkeypatch lightweight spy objects onto `app.state.tenant_mcp_pool_service` / `app.state.agent_runtime` after app construction, then assert their call flags after a POST and a DELETE.

```python
@pytest.mark.asyncio
async def test_post_and_delete_invalidate_tenant_mcp_cache(monkeypatch) -> None:
    app, admin_headers, tenant_id = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)

    class _PoolSpy:
        def __init__(self): self.invalidated = []
        async def invalidate(self, tid): self.invalidated.append(tid)
    class _RuntimeSpy:
        def __init__(self): self.invalidated = []
        def invalidate_tenant(self, tid): self.invalidated.append(tid)

    pool_spy, rt_spy = _PoolSpy(), _RuntimeSpy()
    app.state.tenant_mcp_pool_service = pool_spy
    app.state.agent_runtime = rt_spy

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        await client.post("/v1/mcp-servers", json={"name":"github","transport":"sse","url":"https://x.example.com/sse","auth_type":"none"}, headers=admin_headers)
        await client.delete("/v1/mcp-servers/github", headers=admin_headers)
    assert pool_spy.invalidated.count(tenant_id) == 2
    assert rt_spy.invalidated.count(tenant_id) == 2
```

- [ ] **Step 2: Run → fail** (handlers don't invalidate yet).

- [ ] **Step 3: Implement the invalidation calls** in POST/PATCH/DELETE per Step 1. The `agent_runtime` app.state attribute name MUST be confirmed by grep; if the runtime isn't on app.state, wire it in app.py (it's constructed there). If wiring the runtime onto app.state is non-trivial, at minimum invalidate the pool service (pool invalidation is the correctness-critical half — a stale pool serves closed clients; a stale cached agent merely lacks the new server until its version is redeployed). Prefer doing both; if the runtime isn't readily on app.state, add `app.state.agent_runtime = <the runtime>` in app.py.

- [ ] **Step 4: Run tests + lint + commit**

Run: `uv run python -m pytest services/control-plane/tests/test_mcp_servers_api.py -q` → PASS. ruff/format clean.
```bash
git add services/control-plane/src/control_plane/api/mcp_servers.py services/control-plane/src/control_plane/app.py services/control-plane/tests/test_mcp_servers_api.py
git commit -m "feat(stream-v): invalidate tenant MCP pool + agent cache on registry change (V-D)"
```

---

## Task 7: Preflight + push + PR

- [ ] **Step 1: Affected test scope**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && uv run python -m pytest -m "not integration" services/control-plane services/orchestrator packages/expert-work-protocol -q`
Expected: PASS, no regressions.

- [ ] **Step 2: Lint + CI mypy scope**

`uv run ruff check . && uv run ruff format --check .` → clean.
`uv run mypy packages services/audit-backup-worker/src services/event-log-archive-job/src services/orchestrator/src services/retention-cleanup-job/src` → clean (note: control-plane/src not in this gate, but `services/orchestrator/src` IS — the ToolEnv/_register_mcp change must type-check here).
`pre-commit run --all-files` if available.

- [ ] **Step 3: uv.lock drift** — `git status --short uv.lock` empty.

- [ ] **Step 4: Push + PR**

```bash
git push -u origin stream-v/d-runtime
gh pr create --base main --head stream-v/d-runtime \
  --title "feat(stream-v): PR D — Per-Tenant Remote MCP Pool (runtime wiring)" \
  --body "Implements Stream V-D per docs/streams/STREAM-V-DESIGN.md (Mini-ADR V-4). Makes a tenant's registered remote MCP servers usable by its agents.

## What
- \`ToolEnv.tenant_mcp_pool\` + \`_register_mcp\` registers platform pool (allowlist-gated) ∪ tenant pool (the tenant's own, never gated), both filtered by \`allow_tools\`; platform wins on name collision.
- \`TenantMcpPoolService\`: per-tenant \`MCPServerPool\` built from the registry + encrypted secret store (token→Authorization header via the existing \`_build_mcp_client\`), cached, reused across builds, closed at shutdown; one bad server is skipped, not fatal.
- \`AgentRuntime.invalidate_tenant\` + builder wiring (\`tenant_mcp_pool_provider\` into main + child builders).
- V-C registry mutations (POST/PATCH/DELETE) invalidate the tenant pool + that tenant's cached agents.

## Lifecycle (user-confirmed)
Per-tenant cached pool + invalidate-on-change. Pool built lazily, reused; registry change → close pool + drop cached agents → next run rebuilds against fresh servers; all pools closed at app shutdown.

## Scope
V-D makes ALL enabled tenant servers available (filtered by existing \`allow_tools\`). Per-agent server *selection* (\`MCPToolSpec.servers\`) is V-E. Discovery + UI are V-F/G.

## Tests
- Orchestrator: union registration, allowlist-not-applied-to-tenant, allow_tools filters tenant pool, tenant-only pool, name-collision platform-wins, no-pools raises.
- Service: build-from-enabled, disabled-excluded, cache-reuse, invalidate-rebuilds, empty, close_all.
- Runtime: invalidate_tenant drops only that tenant.
- Builder: tenant pool reaches ToolEnv. API: mutations invalidate.

## CodeQL
No Protocol \`...\` bodies; no tenant-derived values in logs (static messages only).

🤖 Generated with Claude Code"
```

- [ ] **Step 5: Poll CI green** (resolve any CodeQL review threads — Protocol docstrings + no tainted logs were applied preventively). Fix failures before V-E.

---

## Self-Review (plan author)

**Spec coverage (STREAM-V-DESIGN.md V-4):** per-tenant pool build from registry + token resolution (Task 2) ✓; `_register_mcp` platform∪tenant union, allow_tools filter (Task 1) ✓; builder closure decoupling (Task 4) ✓; child-agent wiring (Task 4 Step 4) ✓; pool lifecycle cache+reuse+invalidate (Tasks 2,3,6) ✓; app lifespan + shutdown (Task 5) ✓. Per-agent `servers` filter correctly deferred to V-E.

**Lifecycle decision (user-confirmed):** per-tenant cached pool + invalidate-on-change — Task 2 (cache), Task 3 (agent-cache invalidate), Task 6 (registry-mutation hooks), Task 5 (shutdown close_all).

**Placeholder scan:** New modules (tenant_mcp_pool.py, ToolEnv/_register_mcp) have complete code. app.py (Task 5) + builder/runtime test files (Tasks 3,4) + the agent_runtime app.state attribute (Task 6) intentionally say "grep the real name + mirror the sibling" because those are large/contextual sites best edited against live code, not transcribed.

**Type consistency:** `TenantMcpPoolService(store, secret_store, client_factory)`, `get_or_build`/`invalidate`/`close_all`, `TenantMcpPoolProvider = Callable[[UUID], Awaitable[MCPServerPool]]`, `ToolEnv.tenant_mcp_pool`, `AgentRuntime.invalidate_tenant(tenant_id)` are consistent across tasks.

**Risks to watch:** (1) `make_child_agent_builder`'s exact ToolEnv construction may differ from `make_agent_builder` — Task 4 Step 4 says read first. (2) `agent_runtime` app.state attribute name — Task 6 says grep; if absent, wire it. (3) name-collision policy (platform wins) is a deliberate choice — documented + tested. (4) the `_build_mcp_client` import is a leading-underscore (private) symbol reused across modules — acceptable (already imported by tests); if it should be public, leave as-is to avoid churn.
