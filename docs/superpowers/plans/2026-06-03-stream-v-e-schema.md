# Stream V-E — `MCPToolSpec.servers` (per-agent server selection) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** Let an agent manifest select *which* MCP servers the agent may use, via a new `MCPToolSpec.servers` field, and enforce it in `_register_mcp`. Empty `servers` = all available servers (backward-compatible with every existing manifest).

**Architecture:** Add `servers: list[str]` to `MCPToolSpec` (protocol). In `_register_mcp` (orchestrator), when `entry.servers` is non-empty, register only servers whose name is in that set — applied to BOTH the platform pool and the tenant pool, composing with the existing `mcp_allowlist` (platform) and `allow_tools` (tool-name) filters.

**Tech Stack:** Pydantic v2, pytest. No new deps. No DB/migration (manifests are JSON, the field just appears under `tools[].servers`).

**Scope (V-E only):** the `servers` field + the `_register_mcp` server filter + tests + canonical-manifest verification. NOT: the discovery endpoints / management UI (V-F), the agent-form server picker (V-G).

**Branch:** `stream-v/e-schema` (off `main`, after V-D merged).

**CodeQL guardrails:** no `Protocol`/ABC standalone `...` bodies; no tenant-derived values in `logger.*` calls.

**Key facts (verified 2026-06-03, file:line):**
- `MCPToolSpec` — `packages/helix-protocol/src/helix_agent/protocol/agent_spec.py:574`: `model_config = ConfigDict(extra="forbid")`, fields `type: Literal["mcp"] = "mcp"`, `allow_tools: list[str] = Field(default_factory=list)`.
- `_register_mcp(registry, entry, env)` — `services/orchestrator/src/orchestrator/tools/assembly.py:320`: computes `allow = set(entry.allow_tools) or None`, iterates platform pool (gated by `set(env.mcp_allowlist) or None`) then tenant pool (collision-skip via `registered_servers`), calling `register_mcp_tools(server_name=, client=, registry=, allow_tools=allow)`.
- Orchestrator MCP assembly tests: `services/orchestrator/tests/test_tool_assembly.py` (`MCPServerPool` / `RecordingMCPClient` / `MCPToolDef` / `build_tool_registry` / `ToolEnv` / `MCPToolSpec` fixtures).
- Canonical manifest test: `services/control-plane/tests/test_canonical_manifest.py`.
- Protocol signature sweep ([memory:reference_protocol_sweep_includes_tools_eval]): construction of `MCPToolSpec` may appear in tools/eval doubles — grep before finishing.

---

## File Structure
**Modify:**
- `packages/helix-protocol/src/helix_agent/protocol/agent_spec.py` — add `servers` to `MCPToolSpec`.
- `packages/helix-protocol/tests/` (the agent_spec test file — grep `MCPToolSpec`) — field + backward-compat tests.
- `services/orchestrator/src/orchestrator/tools/assembly.py` — `_register_mcp` server filter.
- `services/orchestrator/tests/test_tool_assembly.py` — server-filter tests.

---

## Task 1: Add `MCPToolSpec.servers`

**Files:**
- Modify: `packages/helix-protocol/src/helix_agent/protocol/agent_spec.py`
- Modify/Create test: the protocol test that covers `MCPToolSpec` (grep `grep -rln "MCPToolSpec" packages/helix-protocol/tests/`; if none, create `packages/helix-protocol/tests/test_mcp_tool_spec.py`)

- [ ] **Step 1: Write the failing test**

Add (to the located test file, or a new `test_mcp_tool_spec.py`):

```python
from helix_agent.protocol.agent_spec import MCPToolSpec


def test_mcp_tool_spec_defaults_empty_servers() -> None:
    spec = MCPToolSpec()
    assert spec.servers == []          # empty = all available servers
    assert spec.allow_tools == []


def test_mcp_tool_spec_accepts_servers() -> None:
    spec = MCPToolSpec(servers=["github", "linear"], allow_tools=["create_issue"])
    assert spec.servers == ["github", "linear"]


def test_mcp_tool_spec_backward_compatible_without_servers() -> None:
    # A manifest dict from before V-E (no "servers" key) must still parse,
    # defaulting servers to [].
    spec = MCPToolSpec.model_validate({"type": "mcp", "allow_tools": ["x"]})
    assert spec.servers == []


def test_mcp_tool_spec_still_forbids_extra() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        MCPToolSpec.model_validate({"type": "mcp", "bogus": 1})
```

- [ ] **Step 2: Run → fail**

Run: `cd /Users/mac/src/github/jone_qian/helix-agent && uv run python -m pytest <test file> -q`
Expected: FAIL — `MCPToolSpec` has no `servers` attribute.

- [ ] **Step 3: Add the field**

In `packages/helix-protocol/src/helix_agent/protocol/agent_spec.py`, edit `MCPToolSpec` (insert `servers` before `allow_tools`) and update the docstring:

```python
class MCPToolSpec(BaseModel):
    """Enable MCP tools for this agent.

    ``servers`` optionally restricts the agent to the named MCP servers
    (from the platform pool the tenant is allowed to use + the tenant's own
    registered remote servers); empty means every available server. Stream V.
    ``allow_tools`` optionally filters which advertised tools the agent sees
    (by bare tool name, across the selected servers); empty means all."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["mcp"] = "mcp"
    servers: list[str] = Field(default_factory=list)
    allow_tools: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Run → pass**

Run: `cd /Users/mac/src/github/jone_qian/helix-agent && uv run python -m pytest <test file> -q`
Expected: PASS.

- [ ] **Step 5: Lint/type + commit**

Run: `uv run ruff check packages/helix-protocol && uv run ruff format --check packages/helix-protocol && uv run mypy packages`
Expected: clean.

```bash
git add packages/helix-protocol/src/helix_agent/protocol/agent_spec.py packages/helix-protocol/tests/
git commit -m "feat(stream-v): MCPToolSpec.servers field (per-agent server selection) (V-E)"
```

---

## Task 2: `_register_mcp` server filter

**Files:**
- Modify: `services/orchestrator/src/orchestrator/tools/assembly.py`
- Modify: `services/orchestrator/tests/test_tool_assembly.py`

- [ ] **Step 1: Write the failing tests**

Add to `services/orchestrator/tests/test_tool_assembly.py`:

```python
@pytest.mark.asyncio
async def test_servers_filter_restricts_to_named_servers() -> None:
    pool = MCPServerPool()
    await pool.add("github", RecordingMCPClient(tools=(MCPToolDef(name="gh", description="", input_schema={}),)))
    await pool.add("linear", RecordingMCPClient(tools=(MCPToolDef(name="li", description="", input_schema={}),)))
    registry = await build_tool_registry(
        [MCPToolSpec(servers=["github"])], tool_env=ToolEnv(mcp_pool=pool)
    )
    assert registry.get("mcp:github.gh") is not None
    assert registry.get("mcp:linear.li") is None


@pytest.mark.asyncio
async def test_empty_servers_means_all() -> None:
    pool = MCPServerPool()
    await pool.add("github", RecordingMCPClient(tools=(MCPToolDef(name="gh", description="", input_schema={}),)))
    await pool.add("linear", RecordingMCPClient(tools=(MCPToolDef(name="li", description="", input_schema={}),)))
    registry = await build_tool_registry([MCPToolSpec()], tool_env=ToolEnv(mcp_pool=pool))
    assert registry.get("mcp:github.gh") is not None
    assert registry.get("mcp:linear.li") is not None


@pytest.mark.asyncio
async def test_servers_filter_applies_to_tenant_pool() -> None:
    tenant = MCPServerPool()
    await tenant.add("github", RecordingMCPClient(tools=(MCPToolDef(name="gh", description="", input_schema={}),)))
    await tenant.add("postgres", RecordingMCPClient(tools=(MCPToolDef(name="pg", description="", input_schema={}),)))
    registry = await build_tool_registry(
        [MCPToolSpec(servers=["github"])], tool_env=ToolEnv(tenant_mcp_pool=tenant)
    )
    assert registry.get("mcp:github.gh") is not None
    assert registry.get("mcp:postgres.pg") is None


@pytest.mark.asyncio
async def test_servers_filter_composes_with_platform_and_tenant() -> None:
    platform = MCPServerPool()
    await platform.add("ops", RecordingMCPClient(tools=(MCPToolDef(name="deploy", description="", input_schema={}),)))
    tenant = MCPServerPool()
    await tenant.add("github", RecordingMCPClient(tools=(MCPToolDef(name="gh", description="", input_schema={}),)))
    # select only the tenant's github; the platform ops server is excluded.
    registry = await build_tool_registry(
        [MCPToolSpec(servers=["github"])],
        tool_env=ToolEnv(mcp_pool=platform, tenant_mcp_pool=tenant),
    )
    assert registry.get("mcp:github.gh") is not None
    assert registry.get("mcp:ops.deploy") is None
```

- [ ] **Step 2: Run → fail**

Run: `cd /Users/mac/src/github/jone_qian/helix-agent && uv run python -m pytest services/orchestrator/tests/test_tool_assembly.py -q -k "servers_filter or empty_servers_means_all"`
Expected: FAIL (the `servers` field isn't consulted yet → linear/postgres/ops still registered).

- [ ] **Step 3: Add the filter**

In `services/orchestrator/src/orchestrator/tools/assembly.py` `_register_mcp`, after `allow = set(entry.allow_tools) or None` add:
```python
    server_select = set(entry.servers) or None  # None = no per-agent restriction
```
Then in the PLATFORM loop, after the `server_allow` skip and before fetching the client, add:
```python
            if server_select is not None and server_name not in server_select:
                continue
```
And in the TENANT loop, after the `registered_servers` collision-skip and before fetching the client, add the same:
```python
            if server_select is not None and server_name not in server_select:
                continue
```

- [ ] **Step 4: Run → pass**

Run: `cd /Users/mac/src/github/jone_qian/helix-agent && uv run python -m pytest services/orchestrator/tests/test_tool_assembly.py -q`
Expected: PASS (new + all existing MCP tests).

- [ ] **Step 5: Lint/type + commit**

Run: `uv run ruff check services/orchestrator && uv run ruff format --check services/orchestrator && uv run mypy services/orchestrator/src`
Expected: clean.

```bash
git add services/orchestrator/src/orchestrator/tools/assembly.py services/orchestrator/tests/test_tool_assembly.py
git commit -m "feat(stream-v): _register_mcp filters by MCPToolSpec.servers (V-E)"
```

---

## Task 3: Protocol sweep + canonical manifest verification

**Files:**
- Verify only (+ fix any fallout): grep usages; run canonical manifest test.

- [ ] **Step 1: Sweep for MCPToolSpec construction in doubles/fixtures**

Run: `cd /Users/mac/src/github/jone_qian/helix-agent && grep -rn "MCPToolSpec(" --include=*.py | grep -v "tests/test_tool_assembly.py" | grep -v "agent_spec.py"`
The `servers` field has a default, so existing constructions stay valid — but confirm none passes positional args that would now misalign (they don't; the model is keyword-only / Pydantic). If anything constructs it positionally or asserts its field set, fix it. Report what you found.

- [ ] **Step 2: Run the canonical manifest + full protocol/orchestrator unit suites**

Run: `cd /Users/mac/src/github/jone_qian/helix-agent && uv run python -m pytest services/control-plane/tests/test_canonical_manifest.py -q`
Expected: PASS (canonical manifest still validates; the new optional field doesn't break it).

Run: `cd /Users/mac/src/github/jone_qian/helix-agent && uv run python -m pytest -m "not integration" packages/helix-protocol services/orchestrator -q`
Expected: PASS, no regressions.

- [ ] **Step 3: Commit (only if Step 1 required a fix; otherwise skip)**

```bash
git add -A
git commit -m "test(stream-v): fix MCPToolSpec construction fallout from servers field (V-E)"
```

---

## Task 4: Preflight + push + PR

- [ ] **Step 1: Full affected scope**

Run: `cd /Users/mac/src/github/jone_qian/helix-agent && uv run python -m pytest -m "not integration" packages/helix-protocol services/orchestrator services/control-plane -q`
Expected: PASS.

- [ ] **Step 2: Lint + CI mypy scope**

Run: `uv run ruff check . && uv run ruff format --check .` → clean.
Run: `uv run mypy packages services/audit-backup-worker/src services/event-log-archive-job/src services/orchestrator/src services/retention-cleanup-job/src` → clean (protocol + orchestrator changes are in scope).

- [ ] **Step 3: uv.lock drift** — `git status --short uv.lock` empty.

- [ ] **Step 4: Push + PR**

```bash
git push -u origin stream-v/e-schema
gh pr create --base main --head stream-v/e-schema \
  --title "feat(stream-v): PR E — MCPToolSpec.servers (per-agent server selection)" \
  --body "Implements Stream V-E per docs/streams/STREAM-V-DESIGN.md (Mini-ADR V-3).

## What
- \`MCPToolSpec.servers: list[str]\` — selects which MCP servers an agent may use; empty = all available (backward-compatible with every existing manifest).
- \`_register_mcp\` filters both the platform pool and the tenant pool by \`servers\` (composes with the existing \`mcp_allowlist\` platform gate + \`allow_tools\` tool-name filter).

## Backward compatibility
Old manifests have no \`servers\` key → defaults to \`[]\` → behaves exactly as before (all servers). \`extra=\"forbid\"\` preserved.

## Scope
Schema + runtime filter only. Discovery endpoints + management UI (V-F) and the agent-form server picker (V-G) are separate PRs. This is the piece that makes the V-G picker meaningful.

## Tests
- Protocol: default-empty, accepts servers, parses pre-V-E manifest dict, still forbids extra.
- Orchestrator: servers-filter restricts (platform + tenant), empty=all, composes platform+tenant.
- Canonical manifest still validates; full protocol+orchestrator unit suites green.

🤖 Generated with Claude Code"
```

- [ ] **Step 5: Poll CI green**; resolve any CodeQL threads (none expected — no Protocol `...`, no tainted logs added).

---

## Self-Review (plan author)
**Spec coverage (V-3):** `servers` field + backward-compat (Task 1) ✓; runtime filter on both pools (Task 2) ✓; sweep + canonical verification (Task 3) ✓.
**Placeholder scan:** all code complete; Task 1's test-file location is "grep then create if absent" (legit — the exact protocol test file name varies).
**Type consistency:** `servers: list[str] = Field(default_factory=list)`; `server_select = set(entry.servers) or None`; same skip idiom in both `_register_mcp` loops, mirroring the existing `server_allow` filter.
**Composition note:** `servers` (per-agent) AND `mcp_allowlist` (per-tenant platform gate) AND `allow_tools` (tool-name) all compose — a platform server must pass both the allowlist and the servers filter; a tenant server must pass the servers filter; tools then pass allow_tools. Covered by `test_servers_filter_composes_with_platform_and_tenant`.
