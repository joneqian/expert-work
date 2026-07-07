# Stream U PR E — Tenant Deactivate/Activate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** A system_admin can suspend (deactivate) and reactivate a tenant. A suspended tenant's members are blocked (403 `TENANT_SUSPENDED`) at the auth middleware; system_admin is unaffected and can still manage/reactivate. The tenant list/manage page shows status + a deactivate/activate action.

**Architecture:** Add a `status` column to `tenant_config` (migration `0053_tenant_status`, default `active`). Dedicated `POST /v1/tenants/{id}/deactivate`·`/activate` (system_admin only, audited) — status does NOT go through `TenantConfigPatch`. Enforcement is a single chokepoint in `AuthMiddleware._call_with_principal`: after setting `request.state.tenant_id`, a cached `TenantStatusService.is_suspended(tenant_id)` → 403 if suspended. Defensive second check at run creation. Frontend: status badge + action on `/settings/tenants`.

**Tech Stack:** Alembic/SQLAlchemy, FastAPI, pytest; React/Antd/Vitest/Playwright.

**Audit note:** `AuditAction` is a SINGLE `StrEnum` in `packages/expert-work-protocol/src/expert_work/protocol/audit.py` — add `TENANT_DEACTIVATE`/`TENANT_ACTIVATE` in ONE place (the "double Literal" in the design doc was over-cautious; only `ResourceType` is duplicated, and we reuse the existing `"tenant"` resource_type, so no ResourceType change).

---

## Task 1: `status` column + protocol + store + list summary

**Files:**
- `packages/expert-work-persistence/migrations/versions/0053_tenant_status.py` (create)
- `packages/expert-work-persistence/src/expert_work/persistence/models/tenant_config.py` (add column)
- `packages/expert-work-protocol/src/expert_work/protocol/tenant_config.py` (add `TenantStatus` + `status` field)
- `packages/expert-work-protocol/src/expert_work/protocol/__init__.py` (export `TenantStatus` if siblings are exported)
- `.../persistence/tenant_config/{base,sql,memory}.py` (add `set_status` + map status in `_row_to_record` + memory create default)
- `services/control-plane/src/control_plane/api/tenants.py` (GET summary includes `status`)
- Tests: `packages/expert-work-persistence/tests/test_in_memory_tenant_config_store.py`, `services/control-plane/tests/test_tenants_api.py`

- [ ] **Step 1: Migration**

Create `0053_tenant_status.py`. READ an existing recent migration (e.g. `0051_platform_embed_config.py`) for the exact module shape (revision/down_revision/upgrade/downgrade headers). Set:
```python
revision = "0053_tenant_status"
down_revision = "0051_platform_embed_config"  # current head
```
`upgrade()`: add column with server default + CHECK:
```python
op.add_column(
    "tenant_config",
    sa.Column("status", sa.Text(), nullable=False, server_default="active"),
)
op.create_check_constraint(
    "ck_tenant_config_status",
    "tenant_config",
    "status IN ('active', 'suspended')",
)
```
`downgrade()`: drop the constraint then the column.

- [ ] **Step 2: Verify single alembic head**

Run: `cd packages/expert-work-persistence && uv run alembic heads 2>&1 | tail` → expect single head `0053_tenant_status`. (revision id "0053_tenant_status" = 18 chars ≤ 32.)

- [ ] **Step 3: Protocol — TenantStatus + status field**

In `protocol/tenant_config.py`: add near the top (after imports / other Literals):
```python
TenantStatus = Literal["active", "suspended"]
```
(ensure `Literal` is imported). Add to `TenantConfigRecord` (after `plan`):
```python
    status: TenantStatus = "active"
```
If `protocol/__init__.py` re-exports `TenantConfigRecord`/`TenantPlan`, also export `TenantStatus` alongside them.

- [ ] **Step 4: Model column**

In `persistence/models/tenant_config.py` `TenantConfigRow`, add (mirror the `plan` column's Mapped/mapped_column style):
```python
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
```

- [ ] **Step 5: Map status + store.set_status + memory default**

- `sql.py` `_row_to_record`: add `status=cast(TenantStatus, row.status),` (import `TenantStatus` from protocol).
- `base.py`: add abstract method:
```python
    @abc.abstractmethod
    async def set_status(
        self, *, tenant_id: UUID, status: str, actor_id: str
    ) -> TenantConfigRecord:
        """Set tenant lifecycle status ('active'|'suspended'). Raises
        TenantConfigNotFoundError if the tenant has no config row."""
```
- `sql.py` `SqlTenantConfigStore.set_status`: UPDATE status + updated_at + updated_by where tenant_id; if no row → raise `TenantConfigNotFoundError`; return `_row_to_record`. Mirror the UPDATE pattern used by `upsert`.
- `memory.py` `InMemoryTenantConfigStore`: `create` already builds a `TenantConfigRecord` (status defaults to "active" via the new field — no change needed there). Add `set_status`: under lock, get existing or raise `TenantConfigNotFoundError`; replace with `existing.model_copy(update={"status": status, "updated_by": actor_id, "updated_at": _now()})`; return it.

- [ ] **Step 6: GET /v1/tenants summary includes status**

In `api/tenants.py` `list_tenants`, add `"status": r.status,` to each summary dict.

- [ ] **Step 7: Tests (RED→GREEN)**

Persistence (`test_in_memory_tenant_config_store.py`): created tenant has `status == "active"`; `set_status(..., status="suspended")` then `get` reflects it; `set_status` on unknown tenant raises `TenantConfigNotFoundError`. Run: `cd packages/expert-work-persistence && uv run python -m pytest tests/test_in_memory_tenant_config_store.py -v`.
Control-plane (`test_tenants_api.py`): the list summary item now includes `status` key == "active". Update the existing `test_list_tenants_system_admin_lists_all` key-set assertion to `{"tenant_id","display_name","plan","status","created_at"}`. Run: `cd services/control-plane && uv run python -m pytest tests/test_tenants_api.py -v`.

- [ ] **Step 8: pre-commit + commit**

Run pre-commit on all changed files (`uv run pre-commit run --files ...`).
```bash
git add packages/expert-work-persistence services/control-plane/src/control_plane/api/tenants.py services/control-plane/tests/test_tenants_api.py packages/expert-work-protocol
git commit -m "feat(stream-u): PR E — tenant status column + set_status store + list summary"
```

---

## Task 2: deactivate/activate endpoints + middleware enforcement

**Files:**
- `packages/expert-work-protocol/src/expert_work/protocol/audit.py` (2 enum values)
- `services/control-plane/src/control_plane/tenant_status.py` (create — `TenantStatusService`)
- `services/control-plane/src/control_plane/api/tenants.py` (deactivate/activate handlers)
- `services/control-plane/src/control_plane/auth/middleware.py` (enforcement)
- `services/control-plane/src/control_plane/app.py` (wire service into middleware)
- `services/control-plane/src/control_plane/api/runs.py` (defensive check)
- Tests: `services/control-plane/tests/test_tenants_api.py`, a middleware/enforcement test

- [ ] **Step 1: AuditAction values**

In `protocol/audit.py` `AuditAction` (near `TENANT_CREATE = "tenant:create"`):
```python
    TENANT_DEACTIVATE = "tenant:deactivate"
    TENANT_ACTIVATE = "tenant:activate"
```

- [ ] **Step 2: TenantStatusService (TTL cache)**

Create `services/control-plane/src/control_plane/tenant_status.py`. Mirror the caching shape of `control_plane/platform_embedding_config.py` (`PlatformEmbeddingConfigService`: store + clock + ttl_seconds, per-key cache, invalidate-on-write). Interface:
```python
class TenantStatusService:
    def __init__(self, *, store: TenantConfigStore, ttl_seconds: float = 30.0, clock=...) -> None: ...
    async def is_suspended(self, tenant_id: UUID) -> bool:
        """True iff the tenant's config row has status == 'suspended'.
        Missing row (e.g. the system tenant) → False (treated active).
        30s TTL cache keyed by tenant_id."""
    def invalidate(self, tenant_id: UUID) -> None: ...
```
Implement get→cache. Default clock to a monotonic-ish callable (mirror how PlatformEmbeddingConfigService takes `clock`); **do not** call `time.monotonic` at import — accept a `clock` param defaulting to `time.monotonic` (a module-level reference is fine; the workflow-script Date ban does NOT apply to app code).

- [ ] **Step 3: deactivate/activate endpoints**

In `api/tenants.py` `build_tenants_router()`, add (system_admin gate mirroring `list_tenants`/`create_tenant` — `PLATFORM_SCOPE_FORBIDDEN`; `bypass_rls_session` for the write; audit; invalidate the status service). Add `_get_tenant_status_service(request)` accessor (reads `request.app.state.tenant_status_service`).
```python
    @router.post("/{tenant_id}/deactivate")
    async def deactivate_tenant(
        tenant_id: UUID,
        principal: Annotated[Principal, Depends(_principal)],
        repo: Annotated[TenantConfigStore, Depends(_get_repo)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
        status_svc: Annotated[TenantStatusService, Depends(_get_tenant_status_service)],
    ) -> dict[str, object]:
        return await _set_tenant_status(
            tenant_id, "suspended", AuditAction.TENANT_DEACTIVATE,
            principal=principal, repo=repo, audit=audit, status_svc=status_svc,
        )

    @router.post("/{tenant_id}/activate")
    async def activate_tenant(...same shape...):
        return await _set_tenant_status(tenant_id, "active", AuditAction.TENANT_ACTIVATE, ...)
```
`_set_tenant_status` helper: gate `is_system_admin` (else 403 PLATFORM_SCOPE_FORBIDDEN); `async with bypass_rls_session(): record = await repo.set_status(tenant_id=tenant_id, status=status, actor_id=principal.subject_id)`; on `TenantConfigNotFoundError` → 404 `TENANT_NOT_FOUND`; `emit(audit, ..., action=action, resource_type="tenant", resource_id=str(tenant_id), result=AuditResult.SUCCESS, ...)`; `status_svc.invalidate(tenant_id)`; return `{"success": True, "data": {"tenant_id": str(tenant_id), "status": status}, "error": None}`. (Place audit emit inside the bypass session, mirroring create_tenant.)

- [ ] **Step 4: Middleware enforcement**

In `auth/middleware.py`:
- `__init__`: add param `tenant_status: TenantStatusService | None = None` → `self._tenant_status = tenant_status`.
- Remove `@staticmethod` from `_call_with_principal`; add `self` as first param (call sites already use `self._call_with_principal(...)` so they keep working). After setting `request.state.actor_id`, before `return await call_next(request)`:
```python
        if self._tenant_status is not None and await self._tenant_status.is_suspended(
            principal.tenant_id
        ):
            return JSONResponse(
                status_code=403,
                content={
                    "success": False,
                    "data": None,
                    "error": {
                        "code": "TENANT_SUSPENDED",
                        "message": "this tenant is suspended",
                    },
                },
            )
```
(system_admin's `principal.tenant_id` is the system tenant, never suspended → unaffected; a suspended tenant's member is blocked. `JSONResponse` already imported.)

- [ ] **Step 5: Wire in app.py**

After `resolved_tenant_config_repo` is built, construct `tenant_status_service = TenantStatusService(store=resolved_tenant_config_repo)`; expose `app.state.tenant_status_service = tenant_status_service`; pass `tenant_status=tenant_status_service` into the `AuthMiddleware` `add_middleware(...)` call. (Mirror how `platform_embedding_config_service` is constructed + stashed on app.state.)

- [ ] **Step 6: runs.py defensive check**

In `api/runs.py` run-creation handler, after `tenant_id = request.state.tenant_id`, add a defensive guard via the status service on app.state (read `request.app.state.tenant_status_service`): if suspended → return/raise the same 403 `TENANT_SUSPENDED` envelope. Keep it minimal; the middleware is the primary gate, this is defense-in-depth. (If wiring the service into runs is awkward, a comment + the middleware gate suffices — but prefer the explicit check.)

- [ ] **Step 7: Tests (RED→GREEN)**

In `test_tenants_api.py` (mirror harness):
- system_admin deactivate → 200, GET list shows status "suspended"; activate → status "active".
- non-admin deactivate → 403.
- deactivate unknown tenant → 404.
- **enforcement**: a request from a member whose tenant is suspended → 403 `TENANT_SUSPENDED` (drive a non-admin principal whose tenant_id is the suspended tenant against any authed route; assert 403 + code). system_admin against the same suspended tenant's management route → still works (deactivate/activate/list 200).
- Add a `TenantStatusService` unit test (suspended/active/missing-row→active/cache-invalidation) if a natural test module exists; else fold into the api test.
Run: `cd services/control-plane && uv run python -m pytest tests/test_tenants_api.py -v` + any middleware test.

- [ ] **Step 8: pre-commit + commit**

```bash
git add packages/expert-work-protocol/src/expert_work/protocol/audit.py services/control-plane
git commit -m "feat(stream-u): PR E — deactivate/activate endpoints + suspended-tenant 403 enforcement"
```

---

## Task 3: Frontend — status badge + deactivate/activate action

**Files:**
- `apps/admin-ui/src/api/tenants.ts` (SDK + `TenantSummary.status`)
- `apps/admin-ui/src/pages/SettingsTenants.tsx` (status column + action)
- `apps/admin-ui/src/i18n/locales/{en,zh-CN}.ts`
- Tests: `apps/admin-ui/src/pages/__tests__/SettingsTenants.test.tsx`, `apps/admin-ui/e2e/tenants.spec.ts`

- [ ] **Step 1: SDK**

In `tenants.ts`: add `status: "active" | "suspended";` to `TenantSummary`. Add:
```ts
export async function deactivateTenant(tenantId: string): Promise<void> {
  await postJson(`/v1/tenants/${tenantId}/deactivate`, {});
}
export async function activateTenant(tenantId: string): Promise<void> {
  await postJson(`/v1/tenants/${tenantId}/activate`, {});
}
```

- [ ] **Step 2: i18n**

Add to `settings_tenants` (both locales): `col_status` ("Status"/"状态"), `active` ("Active"/"正常"), `suspended` ("Suspended"/"已停用"), `deactivate` ("Deactivate"/"停用"), `activate` ("Activate"/"恢复"), `deactivate_confirm` ("Suspend this tenant? Its members will be blocked until reactivated."/"停用该租户？其成员将被拦截，直到恢复。"), `status_change_failed` ("Failed to change tenant status"/"租户状态变更失败").

- [ ] **Step 3: Page — status column + action (tests first)**

In `SettingsTenants.test.tsx` add: a suspended tenant row shows the suspended badge; clicking deactivate calls `deactivateTenant` then refetches; (mock `deactivateTenant`/`activateTenant`). RED → implement.
In `SettingsTenants.tsx`: add a status column rendering an Antd `Tag` (green "active" / red "suspended"), testid `st-status-${tenant_id}`. In the actions column, after Manage, add a button: if `status==="active"` → Deactivate (`st-deactivate-${id}`, `Popconfirm` with `deactivate_confirm`) calling `deactivateTenant(id)`; if suspended → Activate (`st-activate-${id}`) calling `activateTenant(id)`. After success, refetch `listTenants()` (extract the fetch into a `reload` callback) + antd `message`. Errors → `message.error(t("settings_tenants.status_change_failed"))`.

- [ ] **Step 4: e2e**

In `e2e/tenants.spec.ts`: stub list with status; add a test that the suspended badge renders; (optionally) stub the deactivate POST and assert the action calls it. Keep axe green.

- [ ] **Step 5: Verify + commit**

Run: `cd apps/admin-ui && pnpm run typecheck && pnpm vitest run src/pages/__tests__/SettingsTenants.test.tsx && pnpm run build`. Then pre-commit + commit.
```bash
git commit -m "feat(stream-u): PR E — tenant status badge + deactivate/activate action"
```

---

## Task 4: backlog tick + whole-PR gate

- [ ] Tick `U-E` in `docs/ITERATION-PLAN.md` (`[ ]`→`[x]`, append `(PR E)`).
- [ ] Whole-PR preflight: `uv run pre-commit run --all-files`; control-plane `pytest -m "not integration"`; persistence pytest; admin-ui typecheck+vitest+build+storybook+e2e. Fix drift.
- [ ] Commit backlog; open PR `stream-u/e-deactivate`.

## Self-Review (controller)
- **Enforcement correctness (highest risk):** middleware blocks suspended tenant's members; system_admin's principal.tenant_id = system tenant (active) → unaffected, can still deactivate/activate/list. Tests pin both. ✅
- **No status in TenantConfigPatch** (only via dedicated endpoints). ✅
- **AuditAction single-location** (protocol/audit.py); reuse `"tenant"` resource_type. ✅
- **Cache invalidation** on status change (else 30s stale window where a reactivated tenant stays blocked). ✅
- **Migration head** down_revision = `0051_platform_embed_config`; id ≤32. ✅
