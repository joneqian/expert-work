# Stream U PR B — Tenant List Backend + SDK Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** Expose `GET /v1/tenants` (system_admin-only, paginated) so the admin UI can list all tenants, backed by a new `TenantConfigStore.list_all()` and a frontend `listTenants()` SDK.

**Architecture:** Add `list_all(*, limit, offset)` to the `TenantConfigStore` protocol (SQL + memory). New `GET /v1/tenants` handler mirrors `create_tenant`'s `is_system_admin` gate + `bypass_rls_session` (cross-tenant read), returns a lean summary list `{tenant_id, display_name, plan, created_at}` in the standard envelope. (Tenant `status` arrives in PR E; not here.)

**Tech Stack:** SQLAlchemy async, FastAPI, pytest; TS SDK.

---

## File Structure

- `packages/expert-work-persistence/src/expert_work/persistence/tenant_config/base.py` — add abstract `list_all`.
- `.../tenant_config/sql.py` — SQL impl.
- `.../tenant_config/memory.py` — memory impl.
- `packages/expert-work-persistence/tests/test_tenant_config_store.py` (existing or new) — store tests.
- `services/control-plane/src/control_plane/api/tenants.py` — add `GET ""` handler.
- `services/control-plane/tests/test_tenants_api.py` (existing or new) — API tests.
- `apps/admin-ui/src/api/tenants.ts` — add `listTenants()` + `TenantSummary`.
- `apps/admin-ui/src/api/__tests__/sdks.test.ts` — SDK test.

---

## Task 1: `TenantConfigStore.list_all` (protocol + SQL + memory)

**Files:**
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/tenant_config/{base,sql,memory}.py`
- Test: `packages/expert-work-persistence/tests/test_tenant_config_store.py` (create if absent; otherwise extend)

> Method named `list_all` (NOT `list`) to avoid ruff flake8-builtins A003 (builtin shadowing) on a class attribute.

- [ ] **Step 1: Write failing tests**

First READ `packages/expert-work-persistence/tests/` for the existing tenant_config store test (how it builds `InMemoryTenantConfigStore`, and whether SQL store is integration-marked). Mirror that. Add memory-store tests (unit; the SQL path is covered by the existing integration suite — add a SQL test only if the file already exercises SQL non-integration):

```python
@pytest.mark.asyncio
async def test_list_all_empty() -> None:
    store = InMemoryTenantConfigStore()
    assert await store.list_all() == []


@pytest.mark.asyncio
async def test_list_all_returns_created_tenants() -> None:
    store = InMemoryTenantConfigStore()
    a = await store.create(tenant_id=uuid4(), display_name="Acme", actor_id="sys")
    b = await store.create(tenant_id=uuid4(), display_name="Beta", actor_id="sys")
    got = {r.tenant_id for r in await store.list_all()}
    assert got == {a.tenant_id, b.tenant_id}


@pytest.mark.asyncio
async def test_list_all_paginates() -> None:
    store = InMemoryTenantConfigStore()
    for i in range(3):
        await store.create(tenant_id=uuid4(), display_name=f"T{i}", actor_id="sys")
    page = await store.list_all(limit=2, offset=0)
    assert len(page) == 2
    rest = await store.list_all(limit=2, offset=2)
    assert len(rest) == 1
```
(Import `uuid4`, `InMemoryTenantConfigStore`.)

- [ ] **Step 2: Run red**

Run: `cd packages/expert-work-persistence && uv run python -m pytest tests/test_tenant_config_store.py -k list_all -v`
Expected: FAIL (no `list_all`).

- [ ] **Step 3: Add abstract method to base.py**

In `TenantConfigStore` (after `upsert`):
```python
    @abc.abstractmethod
    async def list_all(
        self, *, limit: int = 50, offset: int = 0
    ) -> list[TenantConfigRecord]:
        """Return tenant config rows ordered by ``created_at`` (oldest first).

        Platform-level cross-tenant read behind ``GET /v1/tenants``
        (system_admin only). Paginated via ``limit``/``offset``.
        """
```

- [ ] **Step 4: SQL impl in sql.py**

Add `from sqlalchemy import select` to imports. Add method on `SqlTenantConfigStore`:
```python
    async def list_all(
        self, *, limit: int = 50, offset: int = 0
    ) -> list[TenantConfigRecord]:
        async with self._sf() as session:
            result = await session.execute(
                select(TenantConfigRow)
                .order_by(TenantConfigRow.created_at)
                .limit(limit)
                .offset(offset)
            )
            rows = result.scalars().all()
        return [_row_to_record(r) for r in rows]
```

- [ ] **Step 5: Memory impl in memory.py**

Add on `InMemoryTenantConfigStore`:
```python
    async def list_all(
        self, *, limit: int = 50, offset: int = 0
    ) -> list[TenantConfigRecord]:
        async with self._lock:
            ordered = sorted(self._rows.values(), key=lambda r: r.created_at)
        return ordered[offset : offset + limit]
```

- [ ] **Step 6: Run green**

Run: `cd packages/expert-work-persistence && uv run python -m pytest tests/test_tenant_config_store.py -k list_all -v` → PASS.
Run full file: `uv run python -m pytest tests/test_tenant_config_store.py -v` → all pass.

- [ ] **Step 7: pre-commit + commit**

Run: `uv run pre-commit run --files packages/expert-work-persistence/src/expert_work/persistence/tenant_config/base.py packages/expert-work-persistence/src/expert_work/persistence/tenant_config/sql.py packages/expert-work-persistence/src/expert_work/persistence/tenant_config/memory.py packages/expert-work-persistence/tests/test_tenant_config_store.py`
```bash
git add packages/expert-work-persistence/src/expert_work/persistence/tenant_config packages/expert-work-persistence/tests/test_tenant_config_store.py
git commit -m "feat(stream-u): PR B — TenantConfigStore.list_all (protocol + sql + memory)"
```

---

## Task 2: `GET /v1/tenants` endpoint

**Files:**
- Modify: `services/control-plane/src/control_plane/api/tenants.py`
- Test: `services/control-plane/tests/test_tenants_api.py`

- [ ] **Step 1: Write failing tests**

READ `services/control-plane/tests/test_tenants_api.py` (or wherever `POST /v1/tenants` is tested) for the harness: how it builds the app/client, how it makes a system_admin vs non-admin principal. Mirror exactly. Add:

```python
@pytest.mark.asyncio
async def test_list_tenants_system_admin_lists_all(...):
    # seed two tenants via the store/create path the harness uses
    resp = await client.get("/v1/tenants")  # system_admin client
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    ids = {t["tenant_id"] for t in body["data"]}
    assert seeded_a in ids and seeded_b in ids
    # summary shape only — no full config leak
    assert set(body["data"][0].keys()) == {"tenant_id", "display_name", "plan", "created_at"}


@pytest.mark.asyncio
async def test_list_tenants_non_admin_forbidden(...):
    resp = await client.get("/v1/tenants")  # non-system-admin principal
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_list_tenants_pagination(...):
    resp = await client.get("/v1/tenants?limit=1&offset=0")
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 1
```
> Adapt principal/seed helpers to the existing module. If the existing test seeds tenants via `repo.create`, reuse it.

- [ ] **Step 2: Run red**

Run: `cd services/control-plane && uv run python -m pytest tests/test_tenants_api.py -k list -v` → FAIL (404 / no route).

- [ ] **Step 3: Add the handler**

In `build_tenants_router()`, after the `create_tenant` handler, add. Mirror `create_tenant`'s system_admin gate (it raises 403 when `not principal.is_system_admin` — copy that exact style/shape) and its `bypass_rls_session()` usage for cross-tenant `tenant_config` access:

```python
    @router.get("")
    async def list_tenants(
        principal: Annotated[Principal, Depends(_principal)],
        repo: Annotated[TenantConfigStore, Depends(_get_repo)],
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, object]:
        """List all tenants (summary). Platform-level: system_admin only."""
        if not principal.is_system_admin:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "FORBIDDEN",
                    "message": "only a system admin may list tenants",
                },
            )
        capped = max(1, min(limit, 200))
        async with bypass_rls_session():
            records = await repo.list_all(limit=capped, offset=max(0, offset))
        return {
            "success": True,
            "data": [
                {
                    "tenant_id": str(r.tenant_id),
                    "display_name": r.display_name,
                    "plan": r.plan.value,
                    "created_at": r.created_at.isoformat(),
                }
                for r in records
            ],
            "error": None,
        }
```
> Match the exact 403 detail shape that `create_tenant` uses if it differs (mirror it for consistency). `bypass_rls_session` is already imported in this module.

- [ ] **Step 4: Run green**

Run: `cd services/control-plane && uv run python -m pytest tests/test_tenants_api.py -v` → all pass.

- [ ] **Step 5: pre-commit + commit**

Run: `uv run pre-commit run --files services/control-plane/src/control_plane/api/tenants.py services/control-plane/tests/test_tenants_api.py`
```bash
git add services/control-plane/src/control_plane/api/tenants.py services/control-plane/tests/test_tenants_api.py
git commit -m "feat(stream-u): PR B — GET /v1/tenants (system_admin, paginated)"
```

---

## Task 3: SDK `listTenants()`

**Files:**
- Modify: `apps/admin-ui/src/api/tenants.ts`
- Test: `apps/admin-ui/src/api/__tests__/sdks.test.ts`

- [ ] **Step 1: Write failing test**

Mirror the existing SDK test pattern in `sdks.test.ts` (the `captureAdapter`/enveloped-response helper). Add:
```ts
it("listTenants calls GET /v1/tenants and unwraps data", async () => {
  // enveloped response { success, data: [ {tenant_id, display_name, plan, created_at} ], error: null }
  const out = await listTenants();
  // assert GET to "/v1/tenants"; out is the array
  expect(Array.isArray(out)).toBe(true);
});
```
Add `listTenants` to the import.

- [ ] **Step 2: Run red**

Run: `cd apps/admin-ui && pnpm vitest run src/api/__tests__/sdks.test.ts -t "listTenants"` → FAIL.

- [ ] **Step 3: Add SDK fn + type**

In `tenants.ts`:
```ts
export interface TenantSummary {
  tenant_id: string;
  display_name: string;
  plan: TenantPlan;
  created_at: string;
}

export async function listTenants(limit = 50, offset = 0): Promise<TenantSummary[]> {
  return getJson<TenantSummary[]>(`/v1/tenants?limit=${limit}&offset=${offset}`);
}
```
Add `getJson` to the import from `./client` (currently imports `postJson`).

- [ ] **Step 4: Run green + typecheck**

Run: `cd apps/admin-ui && pnpm vitest run src/api/__tests__/sdks.test.ts -t "listTenants" && pnpm run typecheck` → PASS, typecheck 0.
> Stale LSP "cannot find module" diagnostics: typecheck exit 0 is authoritative.

- [ ] **Step 5: pre-commit + commit**

Run: `uv run pre-commit run --files apps/admin-ui/src/api/tenants.ts apps/admin-ui/src/api/__tests__/sdks.test.ts`
```bash
git add apps/admin-ui/src/api/tenants.ts apps/admin-ui/src/api/__tests__/sdks.test.ts
git commit -m "feat(stream-u): PR B — listTenants SDK"
```

---

## Self-Review (controller)
- **Coverage:** U-1 (list endpoint + store.list_all + SDK). Status field intentionally deferred to PR E. ✅
- **Type consistency:** `list_all(*, limit, offset) -> list[TenantConfigRecord]` identical across base/sql/memory; endpoint maps to 4-key summary; SDK `TenantSummary` matches those 4 keys. ✅
- **Cross-tenant read:** endpoint wraps `bypass_rls_session()` (mirrors create_tenant) so RLS on tenant_config doesn't filter the list. ✅
- **Naming:** `list_all` avoids ruff A003. ✅
- **Pre-commit (ruff-format) before each commit.** ✅
