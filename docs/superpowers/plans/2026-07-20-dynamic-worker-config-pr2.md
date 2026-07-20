# B3 PR2 —— dynamic_worker 平台配置节 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** dynamic_worker 三参数(max_concurrent / max_per_run / max_iterations)进平台设置页,DB-wins-over-env、改后热生效(不重启)。

**Architecture:** 完整复刻 `platform_tool_budget_config` 先例栈(单行 singleton 表 + TTL 缓存 service + system_admin API + SettingsPlatformConfig 节),再把两个现存的"启动时冷读"消费点(`AgentRuntime.new_worker_spawn_budget` 属性读、`make_worker_build_fn` 闭包捕获)改为 per-run / per-build 经 service 读。

**Tech Stack:** SQLAlchemy + alembic / FastAPI / React + antd + vitest / pytest。

## Global Constraints

- spec:`docs/superpowers/specs/2026-07-20-token-budget-breaker-design.md` § "PR2 —— dynamic_worker 平台配置节"。
- 校验界跟 `settings.py` 现有 Field 约束(spec 规则文字为准;spec 例数 max_per_run 1-64 是笔误):`max_concurrent` 1-16、`max_per_run` **1-256**、`max_iterations` 1-64。
- env 默认值(settings.py):max_concurrent=3、max_per_run=16、max_iterations=32。
- 配置行是 all-or-nothing:单行 singleton,三列 NOT NULL;"未配置" = 无行 → 全部回落 env。PUT 必须三个字段全给。
- API 信封 `{success,data,error}`;门 `principal.is_system_admin`,403 detail `{"code": "PLATFORM_SCOPE_FORBIDDEN", ...}`。
- migration revision id ≤32 字符:用 `0124_platform_dynamic_worker`(28 字符),`down_revision = "0123_http_tool_denylist"`。
- 平台级 tenant-less 表:无 RLS policy、无 GRANT(0102 先例)。
- CI 范围:mypy 跑 `uv run mypy packages services/audit-backup-worker/src services/billing-rollup-job/src services/event-log-archive-job/src services/orchestrator/src services/retention-cleanup-job/src`;ruff 全库 + `ruff format --check`;control-plane 测试本地跑(CI pytest 含);admin-ui `pnpm exec vitest run src && pnpm typecheck && pnpm build`。
- IDE 诊断长期 stale,一律以真 tsc / pytest 定论。

---

### Task 1: persistence 层(model + migration + store 三件套)

**Files:**
- Create: `packages/expert-work-persistence/src/expert_work/persistence/models/platform_dynamic_worker_config.py`
- Create: `packages/expert-work-persistence/migrations/versions/0124_platform_dynamic_worker.py`
- Create: `packages/expert-work-persistence/src/expert_work/persistence/platform_dynamic_worker_config/__init__.py` + `base.py` + `memory.py` + `sql.py`
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/models/__init__.py`(加 Row re-export,照 `PlatformToolBudgetConfigRow` 的两处:import 块 + `__all__`)
- Test: `packages/expert-work-persistence/tests/test_platform_dynamic_worker_config_store.py`

**Interfaces:**
- Produces: `PlatformDynamicWorkerConfigStore.get() -> PlatformDynamicWorkerConfigRow | None`;`put(*, max_concurrent: int, max_per_run: int, max_iterations: int, updated_by: str | None) -> None`;dataclass `PlatformDynamicWorkerConfigRow(max_concurrent, max_per_run, max_iterations, updated_by)`;`InMemoryPlatformDynamicWorkerConfigStore` / `SqlPlatformDynamicWorkerConfigStore(session_factory)`。

先例逐文件对照 `platform_tool_budget_config`(模型/base/memory/sql/`__init__`/migration 0102),把单一 `enabled: bool` 换成三个 `int` 列。要点:

- [ ] **Step 1: 写失败测试**(`test_platform_dynamic_worker_config_store.py`,照 `test_platform_tool_budget_config_store.py` 形状,InMemory):

```python
import pytest

from expert_work.persistence.platform_dynamic_worker_config import (
    InMemoryPlatformDynamicWorkerConfigStore,
)


@pytest.mark.asyncio
async def test_get_returns_none_when_unset() -> None:
    store = InMemoryPlatformDynamicWorkerConfigStore()
    assert await store.get() is None


@pytest.mark.asyncio
async def test_put_then_get_round_trips() -> None:
    store = InMemoryPlatformDynamicWorkerConfigStore()
    await store.put(max_concurrent=5, max_per_run=32, max_iterations=48, updated_by="admin-1")
    row = await store.get()
    assert row is not None
    assert (row.max_concurrent, row.max_per_run, row.max_iterations) == (5, 32, 48)
    assert row.updated_by == "admin-1"


@pytest.mark.asyncio
async def test_put_is_last_write_wins_singleton() -> None:
    store = InMemoryPlatformDynamicWorkerConfigStore()
    await store.put(max_concurrent=2, max_per_run=8, max_iterations=16, updated_by="a")
    await store.put(max_concurrent=4, max_per_run=64, max_iterations=32, updated_by="b")
    row = await store.get()
    assert row is not None
    assert (row.max_concurrent, row.max_per_run, row.max_iterations) == (4, 64, 32)
    assert row.updated_by == "b"
```

- [ ] **Step 2: 跑测试确认失败**:`uv run pytest packages/expert-work-persistence/tests/test_platform_dynamic_worker_config_store.py -q` → ImportError。
- [ ] **Step 3: 实现**。ORM model(镜像 `models/platform_tool_budget_config.py`,docstring 说明单行 singleton + tenant-less 无 RLS,`ondelete` 无关):

```python
class PlatformDynamicWorkerConfigRow(Base):
    """The single platform dynamic-worker limits row."""

    __tablename__ = "platform_dynamic_worker_config"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    max_concurrent: Mapped[int] = mapped_column(Integer, nullable=False)
    max_per_run: Mapped[int] = mapped_column(Integer, nullable=False)
    max_iterations: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_by: Mapped[str | None] = mapped_column(Text, nullable=True)
```

migration `0124_platform_dynamic_worker.py`(镜像 0102,`sa.Integer()` 三列,downgrade drop_table)。base.py 的 dataclass + ABC(get/put 签名见 Interfaces);memory.py 单 `self._row` + `asyncio.Lock`;sql.py `_SINGLETON_ID = "singleton"` + `pg_insert(...).on_conflict_do_update(index_elements=["id"], set_={...})`(注意 ORM Row 与 dataclass 同名,sql.py 里 `as _Model` 导入);`__init__.py` 导出四件套。`models/__init__.py` 加 import + `__all__` 项(字母序插入)。
- [ ] **Step 4: 跑测试确认通过** + `uv run pytest packages/expert-work-persistence/tests -q` 全绿;`uv run ruff check packages && uv run ruff format --check packages`。
- [ ] **Step 5: Commit**:`feat: platform_dynamic_worker_config 持久层(单行 singleton + migration 0124)`

---

### Task 2: control-plane service(DB-wins-over-env,TTL 缓存)

**Files:**
- Create: `services/control-plane/src/control_plane/platform_dynamic_worker_config.py`
- Test: `services/control-plane/tests/test_platform_dynamic_worker_config_service.py`

**Interfaces:**
- Consumes: Task 1 store。
- Produces: `DynamicWorkerConfig`(frozen dataclass:`max_concurrent: int, max_per_run: int, max_iterations: int`);`PlatformDynamicWorkerConfigService(store=..., env_default: DynamicWorkerConfig, ttl_seconds=30.0, clock=time.monotonic)`;`await service.effective() -> DynamicWorkerConfig`(DB-wins,无行回落 env_default);`await service.configured() -> DynamicWorkerConfig | None`;`await service.put(*, max_concurrent, max_per_run, max_iterations, updated_by)`(写后 `invalidate()`);`service.invalidate()`。

镜像 `platform_tool_budget_config.py` service:`_maybe_refresh` 双检锁 + TTL;区别:①效果值是 `DynamicWorkerConfig` 而非 bool;②env 默认不是运行时读 env 函数,而是构造时注入 `env_default`(settings 进程内本就冻结)。

- [ ] **Step 1: 写失败测试**(照 `test_platform_tool_budget_config_service.py` 形状;`_service()` helper 用 `ttl_seconds=0.0` + `env_default=DynamicWorkerConfig(3, 16, 32)`):
  - `test_unset_uses_env_default`:`effective() == env_default`,`configured() is None`。
  - `test_db_row_wins_over_env`:`put(max_concurrent=5, max_per_run=32, max_iterations=48, updated_by="admin")` 后 `effective() == DynamicWorkerConfig(5, 32, 48)`,`configured()` 同值。
  - `test_put_invalidates_cache`:`ttl_seconds=9999.0`,先 warm(读一次),`put` 后立即断言新值可见。
- [ ] **Step 2: 跑测试失败** → **Step 3: 实现** → **Step 4: 通过** + `uv run ruff check services/control-plane && uv run ruff format --check services/control-plane`。
- [ ] **Step 5: Commit**:`feat: PlatformDynamicWorkerConfigService(DB-wins-over-env + TTL 缓存)`

---

### Task 3: API 路由 + 审计 + app.py 装配

**Files:**
- Create: `services/control-plane/src/control_plane/api/platform_dynamic_worker_config.py`
- Modify: `packages/expert-work-protocol/src/expert_work/protocol/audit.py`(`PLATFORM_TOOL_BUDGET_UPDATED` 邻位加 `PLATFORM_DYNAMIC_WORKER_UPDATED = "platform_dynamic_worker_config:updated"`)
- Modify: `services/control-plane/src/control_plane/api/__init__.py`(导出 router builder,照 tool_budget 行)
- Modify: `services/control-plane/src/control_plane/app.py` 六处,全部紧贴 tool_budget 先例行:store 导入(:348 邻)、router 导入(:70 邻)、service 导入(:166 邻)、store 解析 + service 构造(:844-861 邻,`env_default=DynamicWorkerConfig(max_concurrent=resolved_settings.dynamic_worker_max_concurrent, max_per_run=resolved_settings.dynamic_worker_max_per_run, max_iterations=resolved_settings.dynamic_worker_max_iterations)`,`ttl_seconds=float(resolved_settings.tenant_config_cache_ttl_s)`)、`app.state.platform_dynamic_worker_config_service`(:1918 邻)、`include_router`(:2111 邻)、`_SqlStores` Protocol 字段(:2166 邻)+ SQL 装配(:2389 邻)。
- Test: `services/control-plane/tests/test_platform_dynamic_worker_config_api.py`

**Interfaces:**
- Consumes: Task 2 service。
- Produces: `GET/PUT /v1/platform/dynamic-worker-config`;view `{"configured": {"max_concurrent","max_per_run","max_iterations"} | null, "effective": {同三键}}`;write 模型 `PlatformDynamicWorkerConfigWrite`(`extra="forbid"`,`max_concurrent: int = Field(ge=1, le=16)`,`max_per_run: int = Field(ge=1, le=256)`,`max_iterations: int = Field(ge=1, le=64)`);Task 4 从 `app.state` 拿 service。

- [ ] **Step 1: 写失败测试**(照 `test_platform_tool_budget_config_api.py`:`_seed_admin` + role_binding 种 system_admin):
  - `test_non_admin_forbidden` / `test_put_non_admin_forbidden` → 403 + `detail.code == "PLATFORM_SCOPE_FORBIDDEN"`。
  - `test_get_unset_uses_env_default` → `data == {"configured": None, "effective": {"max_concurrent": 3, "max_per_run": 16, "max_iterations": 32}}`。
  - `test_put_then_get_reflects` → PUT `{5, 32, 48}` 后 GET configured/effective 均新值。
  - `test_put_rejects_out_of_bounds` → `max_per_run=257` / `max_iterations=0` → 422。
  - `test_put_rejects_unknown_field` → 多传 `"bogus": 1` → 422。
  - `test_put_emits_audit` → audit rows 里有 `action.value == "platform_dynamic_worker_config:updated"`,details 含三值。
- [ ] **Step 2: 失败** → **Step 3: 实现路由**(镜像 tool_budget 路由文件:`_require_system_admin`/`_get_service`/`_get_audit`/`_view`/GET/PUT;PUT 审计 `resource_type="platform_credential"`, `resource_id="dynamic-worker-config"`, `details={"max_concurrent": ..., "max_per_run": ..., "max_iterations": ...}`)+ app.py 六处装配。
- [ ] **Step 4: 通过** + 本地跑 `uv run pytest services/control-plane/tests -q` 全量(app.py 动了,防装配回归)。
- [ ] **Step 5: Commit**:`feat: /v1/platform/dynamic-worker-config API + 审计 + 装配`

---

### Task 4: 消费点热生效改造

**Files:**
- Modify: `services/control-plane/src/control_plane/runtime.py`(AgentRuntime 加字段 + `new_worker_spawn_budget` 改 async 经 service 读)
- Modify: `services/control-plane/src/control_plane/subagent_runtime.py`(`make_worker_build_fn` 加 service 参数,`_build` per-build 读 max_iterations)
- Modify: `services/control-plane/src/control_plane/app.py`(:1233-1241 属性冷读保留作 env 兜底 + 新增 `resolved_agent_runtime.dynamic_worker_config_service = resolved_platform_dynamic_worker_config_service`;:1390-1412 `make_worker_build_fn(...)` 传 `dynamic_worker_config_service=`)
- Modify: 5 个调用点加 `await`:`api/runs.py:730`、`api/runs.py:880`、`run_queue_worker.py:297`、`trigger_firing.py:290`、`orphan_sweep.py:300`(均形如 `worker_spawn_budget=await runtime.new_worker_spawn_budget(),`)
- Test: `services/control-plane/tests/test_dynamic_worker_hot_reload.py`(新)+ 既有 runtime/spawn 相关测试同步改 async

**Interfaces:**
- Consumes: Task 2 `DynamicWorkerConfig` / service;Task 3 app 装配。
- Produces: `AgentRuntime.dynamic_worker_config_service: PlatformDynamicWorkerConfigService | None = None`;`async def new_worker_spawn_budget(self) -> Any`(service 有则 `cfg = await service.effective()` 取 max_per_run/max_concurrent,无则回落现有属性);`make_worker_build_fn(..., dynamic_worker_config_service: PlatformDynamicWorkerConfigService | None = None)`,`_build` 内 `synthesize_worker_spec(..., max_iterations=await _resolve_worker_max_iterations(dynamic_worker_config_service, max_iterations), ...)`。

- [ ] **Step 1: 写失败测试**(`test_dynamic_worker_hot_reload.py`):

```python
import pytest

from control_plane.platform_dynamic_worker_config import (
    DynamicWorkerConfig,
    PlatformDynamicWorkerConfigService,
)
from control_plane.runtime import AgentRuntime
from control_plane.subagent_runtime import _resolve_worker_max_iterations
from expert_work.persistence.platform_dynamic_worker_config import (
    InMemoryPlatformDynamicWorkerConfigStore,
)


def _service() -> PlatformDynamicWorkerConfigService:
    return PlatformDynamicWorkerConfigService(
        store=InMemoryPlatformDynamicWorkerConfigStore(),
        env_default=DynamicWorkerConfig(max_concurrent=3, max_per_run=16, max_iterations=32),
        ttl_seconds=0.0,
    )


@pytest.mark.asyncio
async def test_spawn_budget_hot_reloads_between_runs() -> None:
    svc = _service()
    runtime = AgentRuntime(dynamic_workers_enabled=True, dynamic_worker_config_service=svc)
    first = await runtime.new_worker_spawn_budget()
    assert (first.max_per_run, first.max_concurrent) == (16, 3)
    await svc.put(max_concurrent=5, max_per_run=32, max_iterations=48, updated_by="admin")
    second = await runtime.new_worker_spawn_budget()
    assert (second.max_per_run, second.max_concurrent) == (32, 5)


@pytest.mark.asyncio
async def test_spawn_budget_falls_back_to_attrs_without_service() -> None:
    runtime = AgentRuntime(
        dynamic_workers_enabled=True,
        dynamic_worker_max_concurrent=2,
        dynamic_worker_max_per_run=8,
    )
    budget = await runtime.new_worker_spawn_budget()
    assert (budget.max_per_run, budget.max_concurrent) == (8, 2)


@pytest.mark.asyncio
async def test_worker_max_iterations_hot_reloads() -> None:
    svc = _service()
    assert await _resolve_worker_max_iterations(svc, 32) == 32
    await svc.put(max_concurrent=3, max_per_run=16, max_iterations=48, updated_by="admin")
    assert await _resolve_worker_max_iterations(svc, 32) == 48
    assert await _resolve_worker_max_iterations(None, 24) == 24
```

（`AgentRuntime` 若必填字段无默认,helper 里按现有测试的最小构造改——以既有 runtime 测试的构造形状为准。）
- [ ] **Step 2: 失败** → **Step 3: 实现**:
  - runtime.py:字段 + async 化(docstring 注明"per-run 经 service 读 → DB 改后热生效;service 缺席回落 lifespan 冷读属性");lazy import 不变。
  - subagent_runtime.py 加模块级 helper:

```python
async def _resolve_worker_max_iterations(
    service: PlatformDynamicWorkerConfigService | None, fallback: int
) -> int:
    """Per-build effective worker iteration cap — DB-wins-over-env (B3 PR2)."""
    if service is None:
        return fallback
    return (await service.effective()).max_iterations
```

  `_build` 里 `max_iterations=await _resolve_worker_max_iterations(dynamic_worker_config_service, max_iterations)`。
  - app.py 两处接线 + 5 调用点 `await`。
- [ ] **Step 4: 全量验证**:`uv run pytest services/control-plane/tests -q`(5 调用点 + runtime 既有测试全过);`uv run pytest services/orchestrator/tests -q`(防 spawn_worker 波及);CI 同款 mypy;ruff 全库。
- [ ] **Step 5: Commit**:`feat: dynamic_worker 三参数消费点热生效(per-run/per-build 经 service 读)`

---

### Task 5: admin-ui(api client + 平台节 + i18n + 测试)

**Files:**
- Create: `apps/admin-ui/src/api/platform_dynamic_worker_config.ts`
- Create: `apps/admin-ui/src/pages/settings_platform/PlatformDynamicWorkerSection.tsx`
- Modify: `apps/admin-ui/src/pages/SettingsPlatformConfig.tsx`(cost tab 的 `<Space>` 里、`PlatformToolBudgetSection` Card 之后加 `<Card size="small" title={t("settings_platform.dynamic_worker_heading")}><PlatformDynamicWorkerSection /></Card>`;import 加一行)
- Modify: i18n 三处(en.ts 接口块 tool_budget_* 邻位、en.ts 值块、zh-CN.ts 值块;**先 grep 确认无同名键撞车**)
- Test: `apps/admin-ui/src/pages/settings_platform/__tests__/PlatformDynamicWorkerSection.test.tsx`;Create: `apps/admin-ui/e2e/platform-dynamic-worker.spec.ts`(镜像 `platform-tool-budget.spec.ts`)

**Interfaces:**
- Consumes: Task 3 API。
- Produces:

```typescript
export interface DynamicWorkerLimits {
  max_concurrent: number;
  max_per_run: number;
  max_iterations: number;
}
export interface PlatformDynamicWorkerConfigView {
  configured: DynamicWorkerLimits | null;
  effective: DynamicWorkerLimits;
}
export async function getPlatformDynamicWorkerConfig(): Promise<PlatformDynamicWorkerConfigView>;
export async function putPlatformDynamicWorkerConfig(
  limits: DynamicWorkerLimits,
): Promise<PlatformDynamicWorkerConfigView>;
```

组件形状(照 `PlatformToolBudgetSection` 三态 + testid 前缀 `pdw-`):load → `Spin`(`pdw-loading`)/`Alert`(`pdw-load-error`)/正常(`pdw-root`)。正常态:帮助 `Alert`(`pdw-help`)、三个 `InputNumber`(`pdw-max-concurrent` min 1 max 16 / `pdw-max-per-run` min 1 max 256 / `pdw-max-iterations` min 1 max 64,初值 `view.effective.*`)、`configured === null` 时 `Tag`(`pdw-env-default`)、保存 `Button`(`pdw-save`,`saving` 时 loading)调 `putPlatformDynamicWorkerConfig` → `message.success(t("settings_platform.dynamic_worker_saved"))`、hint 文案(`pdw-hint`,说明热生效:下一次 run/构建生效,无需重启)。

i18n 键(11 个,`settings_platform.` 命名空间,三处同步):`dynamic_worker_heading`、`dynamic_worker_help_title`、`dynamic_worker_help_body`、`dynamic_worker_max_concurrent_label`、`dynamic_worker_max_per_run_label`、`dynamic_worker_max_iterations_label`、`dynamic_worker_env_default`、`dynamic_worker_hint`、`dynamic_worker_save`、`dynamic_worker_saved`、`dynamic_worker_save_failed`。中文值示例:heading "动态 worker 护栏"、labels "单 run 并发上限 / 单 run 累计生成上限 / 单 worker 步数上限"、hint "改后对下一次运行/构建生效,无需重启;未配置时使用环境变量默认值。"

- [ ] **Step 1: 写失败 vitest**(照 `PlatformToolBudgetSection.test.tsx`:mock get 返回 `{configured: null, effective: {max_concurrent: 3, max_per_run: 16, max_iterations: 32}}`):渲染三输入初值、`configured null` 显示 env-default 标签、改值点保存后 `putPlatformDynamicWorkerConfig` 收到正确对象、`configured` 非 null 时无 env-default 标签。
- [ ] **Step 2: 失败**(`pnpm exec vitest run src/pages/settings_platform`)→ **Step 3: 实现** api client + 组件 + 挂载 + i18n 三处 + e2e spec(路由拦截 `**/v1/platform/dynamic-worker-config` GET/PUT,`?tab=cost` 断言展示 + PUT payload + axe 扫描)。
- [ ] **Step 4: 全量验证**:`pnpm exec vitest run src && pnpm typecheck && pnpm build`。
- [ ] **Step 5: Commit**:`feat(admin-ui): 平台设置页 dynamic worker 护栏节(三参数 + env 默认标注)`

---

## Self-Review 记录

- spec PR2 六条(persistence/service+API/三参数界/消费点热生效/admin-ui/测试)→ T1/T2+T3/T3/T4/T5/各 task Step1 覆盖;校验界矛盾按 spec 规则文字取 settings.py(max_per_run le=256)。
- 类型一致:`DynamicWorkerConfig` 三字段名 = store dataclass 字段名 = API view 键名 = TS interface 键名(snake_case 全链一致)。
- 无占位:每 task Step1 有完整测试代码或明确断言清单,实现步有代码或逐文件先例映射。
