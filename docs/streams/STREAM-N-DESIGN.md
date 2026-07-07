# Stream N — Cross-tenant Platform Admin(设计先行)

> 落实 [docs/ITERATION-PLAN.md](../ITERATION-PLAN.md) § Stream N。
> 引入**系统管理员(平台域)** 角色,与现有**租户管理员(租户域)** 区分;
> 实现受控的跨租户数据访问能力(列表聚合 + 显式 tenant scope 切换)+ 强 audit 留痕。
>
> **先于 Stream H.1b 的任何业务 UI**:H.1b 实施期需要 tenant switcher 在系统管理员视角下出现 "All tenants",
> 这一能力依赖本 Stream 落地。两条 stream 可并行开工,但 H.1b 上线需等本 stream 合入。

设计先行规则([memory:feedback_design_first_iteration](../../.claude/projects/-Users-mac-src-github-jone-qian-expert-work/memory/feedback_design_first_iteration.md)):
**任何一行 backend 代码落地之前**,先完成本设计文档 + Mini-ADR 锁定。

---

## 0. 背景与范围澄清(2026-05-25 用户确认)

平台需要两类管理员:

| 类型 | 角色名 | 数据可见性 | 主要场景 |
|---|---|---|---|
| **系统管理员** | `SYSTEM_ADMIN`(平台域) | 全租户可见 + 全操作 + 跨租户聚合视图 | 平台运维 / 安全审计 / 全平台监控 / 客户支持调查 |
| **租户管理员** | `ADMIN`(租户域) | 仅自己租户内 | 租户内 agent 治理 / skill / 配额 / 用户 / API key |

> 现有 `Role = ADMIN / OPERATOR / VIEWER` **全部租户内**;现有"跨租户"机制仅 mTLS 服务主体可用
> (`Principal.allowed_tenants = (system_tenant_id,)`)。**人类用户没有跨租户角色**。本 Stream 补齐这条线。

**关键约束**(2026-05-25 用户确认):

1. **角色实现** = 加 `Role.SYSTEM_ADMIN` 独立 enum + `role_binding.platform_scope: bool` 字段(明确区分 "平台域" 与 "租户域" 角色)
2. **默认视图** = 系统管理员登录后默认 "All tenants" 聚合视图;可切到单 tenant scope
3. **覆盖面** = 全面 —— 所有 list API 都加 `tenant_id: UUID | Literal["*"]` 支持(参考现有 audit `tenant_id="*"` 范式)
4. **PR 拆法** = 独立 Stream N(backend)与 Stream H.1b(UI)并行;Stream N 先合入

---

## 1. 范围 & 边界

### 1.1 In-scope

| 子项 | 实现内容 | 关联 |
|------|---------|------|
| **N.1 数据层** | migration `0035_role_binding_platform_scope` —— `role_binding.platform_scope BOOLEAN NOT NULL DEFAULT false`(true 时 `tenant_id` 可为 NULL,标识"系统域");`Role.SYSTEM_ADMIN` 加进 enum;ORM + DTO 更新;CHECK constraint(`platform_scope=true` ⇒ `tenant_id IS NULL` 且 `role IN ('system_admin')`) | Mini-ADR N-1 |
| **N.2 Principal + Auth** | `Principal` 加 `is_system_admin: bool` + 修改 `allowed_tenants` 支持 `Literal["*"]`;`ApiKeyVerifier` / `JwtVerifier` 在 verify 后查 platform_scope role binding → 命中即 `is_system_admin=True` + `allowed_tenants="*"`;原 mTLS 路径(`allowed_tenants=(system_tenant_id,)`)保持不变 | Mini-ADR N-2 |
| **N.3 跨租户 RLS 接入点** | `_ensure_tenant_scope(principal, requested_tenant_id)`:requested=具体 UUID → 校验在 allowed 内 + 走原 RLS(GUC `app.tenant_id`);requested=`"*"` → 仅 system_admin 可用 + `bypass_rls_var=True` + 强制 audit | Mini-ADR N-3 |
| **N.4 list API 批量改造** | 14 个 list endpoint 都加 `tenant_id: UUID \| Literal["*"]`(query param)+ 列表行返回 `tenant_id` 列(便于 UI 跨租户聚合显示);默认值:租户管理员默认自家 tenant,system_admin 必须显式传 | Mini-ADR N-4 |
| **N.5 audit 跨租户留痕** | 新增 `AuditAction.SYSTEM_CROSS_TENANT_QUERY`(每次 `tenant_id="*"` 查询);`AuditAction.SYSTEM_TENANT_SWITCH`(切到具体 tenant 的)落审计 | Mini-ADR N-5 |
| **N.6 role_binding API** | `POST /v1/role_bindings` 加 `platform_scope: bool`;仅 system_admin 可创建 platform_scope binding;`GET /v1/role_bindings` 加 `?platform_scope=true` filter 查所有平台域绑定 | — |
| **N.7 测试 + eval** | 单测覆盖 `platform_scope` 校验 / `_ensure_tenant_scope` 行为矩阵 / 跨租户 API 集成 / 跨租户 audit 落库;`tools/eval/platform_admin.py` 4-6 个确定性场景 | — |

### 1.2 Out-of-scope(明确推迟)

| 推迟项 | 落地 | 备注 |
|-------|------|------|
| 系统管理员的**操作(写)能力** | M1 后期 | M0 system_admin 仅"跨租户读取"(list/detail);写操作(create/update/delete 跨租户资源)推 M1 —— 写跨租户风险更高,需要更完善的二次确认 + audit + UI |
| `Role.SYSTEM_OPERATOR` / `SYSTEM_VIEWER` 等更细的平台域角色 | M1 | M0 仅 `SYSTEM_ADMIN` 一档;细分推后 |
| 系统管理员的"假冒为某 user"(impersonation) | M1 / M2 | 客户支持调查场景需要;但需另一套机制(impersonation token + 强 audit + UI 横幅) |
| 跨租户**聚合分析大盘**(成本/QPS/失败率 by tenant) | M2 | M0 list 接口已可承担基本数据;独立分析大盘推后 |
| 平台域 service account / API key | M1 | M0 仅人类 system admin(走 JWT) |

### 1.3 验收(Stream N Exit Criteria)

- migration `0035` 在 dev/staging 应用成功;rollback 测试通过
- `Role.SYSTEM_ADMIN` + `platform_scope` 端到端 lint(mypy / ruff / CodeQL)无 warning
- 14 个 list API 全部接入 `tenant_id` 参数,集成测试覆盖:
  - tenant_admin + 自家 tenant_id → 200
  - tenant_admin + 别人 tenant_id → 403
  - tenant_admin + `tenant_id="*"` → 403
  - system_admin + 任意 tenant_id → 200
  - system_admin + `tenant_id="*"` → 200,跨租户返回,行带 `tenant_id` 字段
- 所有 `tenant_id="*"` 查询自动落 audit `system:cross_tenant_query`
- `tools/eval/platform_admin.py` baseline 通过

---

## 2. 架构

### 2.1 角色域模型

```
┌──────────────────────────────────────────────────────┐
│  Role(StrEnum)                                       │
│  ┌────────────────────────────────────────────────┐  │
│  │  租户域(tenant scope)                          │  │
│  │  - ADMIN       —— 租户内全权                   │  │
│  │  - OPERATOR    —— 租户内运维                   │  │
│  │  - VIEWER      —— 租户内只读                   │  │
│  ├────────────────────────────────────────────────┤  │
│  │  平台域(platform scope)— Stream N 新增        │  │
│  │  - SYSTEM_ADMIN  —— 全租户读 + 平台配置        │  │
│  └────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
```

`role_binding.platform_scope` = `True` ⇒ tenant_id 为 NULL,role 必须 ∈ {SYSTEM_ADMIN}。
CHECK constraint(DB 层):
```sql
CHECK (
  (platform_scope = false AND tenant_id IS NOT NULL AND role IN ('admin','operator','viewer'))
  OR
  (platform_scope = true  AND tenant_id IS NULL     AND role = 'system_admin')
)
```

### 2.2 Principal 扩展

```python
# packages/expert-work-protocol/src/expert_work/protocol/auth.py(已有 Principal)
@dataclass(frozen=True)
class Principal:
    user_id: UUID
    tenant_id: UUID | None  # NULL 仅 system admin
    role: Role
    allowed_tenants: tuple[UUID, ...] | Literal["*"]  # Stream N: 增 "*" 选项
    is_system_admin: bool  # Stream N 新增
    # ... 其他字段
```

`is_system_admin = True` 的判定:
- AuthMiddleware verify JWT/API Key → 查该 user 在 `role_binding` 中是否有 `platform_scope=true` + `role=system_admin` 记录
- 命中 ⇒ `Principal(is_system_admin=True, allowed_tenants="*", tenant_id=None)`
- 未命中 ⇒ 正常租户内 Principal

### 2.3 跨租户 RLS 接入点

```python
# 新增:services/control-plane/src/control_plane/tenant_scope.py
async def ensure_tenant_scope(
    principal: Principal,
    requested_tenant_id: UUID | Literal["*"] | None,
    audit: AuditLogger,
) -> TenantScopeResolution:
    """
    路由层 entry — 决定本请求按哪种 RLS 模式跑。

    返回:
    - SingleTenant(tenant_id=UUID, bypass_rls=False)
    - CrossTenant(bypass_rls=True)  # 仅 system_admin + requested="*" 允许
    """
    if requested_tenant_id == "*":
        if not principal.is_system_admin:
            raise HTTPException(403, "cross_tenant_query requires system_admin")
        await audit.emit(SYSTEM_CROSS_TENANT_QUERY, ...)
        return CrossTenant()

    target = requested_tenant_id or principal.tenant_id
    if principal.allowed_tenants != "*" and target not in principal.allowed_tenants:
        raise HTTPException(403, ...)
    if target != principal.tenant_id:
        await audit.emit(SYSTEM_TENANT_SWITCH, ...)
    return SingleTenant(tenant_id=target)
```

每个 list endpoint 调用 `ensure_tenant_scope`;返回 `CrossTenant` 时,在 SQL session 内开 `bypass_rls_var=True` context manager 跑查询。

### 2.4 list API 改造范式

```python
# 范式 — 14 个 list endpoint 都按这个改
@router.get("/v1/agents")
async def list_agents(
    tenant_id: UUID | Literal["*"] | None = Query(None),
    principal: Principal = Depends(get_principal),
    audit: AuditLogger = Depends(get_audit),
) -> AgentListResponse:
    scope = await ensure_tenant_scope(principal, tenant_id, audit)
    if isinstance(scope, CrossTenant):
        async with bypass_rls_session() as session:
            agents = await agent_store.list_all_tenants(session)  # 新方法
            # 返回每行带 tenant_id 字段(用于 UI 聚合显示)
    else:
        agents = await agent_store.list_by_tenant(scope.tenant_id)
    return AgentListResponse(items=agents, cross_tenant=isinstance(scope, CrossTenant))
```

### 2.5 14 个 list endpoint 清单

| # | router | endpoint | 备注 |
|---|---|---|---|
| 1 | agents | `GET /v1/agents` | |
| 2 | runs | `GET /v1/sessions/*/runs` | 现在按 session scope;system_admin 加跨租户聚合 |
| 3 | sessions | `GET /v1/sessions` | |
| 4 | skills | `GET /v1/skills` | |
| 5 | triggers | `GET /v1/triggers` | |
| 6 | memory | `GET /v1/memory` | per-user 既有;system 加 ?tenant_id=* |
| 7 | artifacts | `GET /v1/artifacts` | |
| 8 | curation | `GET /v1/curation/candidates` | |
| 9 | eval-datasets | `GET /v1/eval-datasets` | |
| 10 | api_keys | `GET /v1/api_keys` | |
| 11 | service_accounts | `GET /v1/service_accounts` | |
| 12 | role_bindings | `GET /v1/role_bindings` | 加 `?platform_scope=true` filter |
| 13 | tenant_quotas | `GET /v1/tenants/*/quotas` | |
| 14 | tenant_config | `GET /v1/tenants/*/config` | |
| (audit) | audit | `GET /v1/audit` | **已支持** `tenant_id="*"`,作范式参考 |

---

## 3. Mini-ADR

### Mini-ADR N-1: 独立 `Role.SYSTEM_ADMIN` enum + `platform_scope` 字段 over "复用 ADMIN + 标志位"
**Context**:可以复用现有 `Role.ADMIN`,通过 `role_binding.is_global=true` 区分平台域。但这样 `ADMIN` 在代码中会出现两种语义(租户内 / 平台域),review 时心智负担大。
**Decision**:加独立 `Role.SYSTEM_ADMIN` enum 值。`role_binding` 表加 `platform_scope: bool` —— DB 层 CHECK constraint 保证 `(platform_scope=true) ⇔ (role=system_admin AND tenant_id=NULL)`。
**Consequences**:角色一目了然,新加 system_admin 路径不会污染现有 ADMIN 处理逻辑。代价:DB schema 多 1 列;Role enum 多 1 项。

### Mini-ADR N-2: `Principal.allowed_tenants` 兼容 `Literal["*"]` over 新加单独字段
**Context**:可以加 `Principal.is_global: bool` 单独区分;但 `allowed_tenants` 已是表达"我能访问哪些 tenant"的字段,扩展它语义更连贯。
**Decision**:`allowed_tenants` 类型从 `tuple[UUID, ...]` 改为 `tuple[UUID, ...] | Literal["*"]`;`"*"` 仅 system_admin 命中。新增 `is_system_admin: bool` 为冗余但**显式**的便捷字段(常用 `if principal.is_system_admin` 判定,比 `if principal.allowed_tenants == "*"` 可读)。
**Consequences**:`Principal` 改动小;现有 mTLS 路径(`allowed_tenants=(system_tenant_id,)`)不动;`is_system_admin` 由 verify 时计算填入,downstream 直接读。

### Mini-ADR N-3: `bypass_rls_var=True` 仅在 explicit `tenant_id="*"` + system_admin 时启用 over "登录即 bypass"
**Context**:可以让 system_admin 一登录就全程 `bypass_rls_var=True`(所有查询都跨租户);但这样默认就过于强大,任何无心查询都可能"看到全租户数据",audit 也乱。
**Decision**:**默认仍按 explicit tenant scope 走 RLS**;仅当请求显式带 `tenant_id="*"` 时开 `bypass_rls_var=True`,且必须落 `SYSTEM_CROSS_TENANT_QUERY` audit。
**Consequences**:UI 层 tenant switcher 切到"All tenants"才真跨租户,切到具体 tenant 仍走 RLS;符合"最小权限 + 显式选择"原则;audit 干净。

### Mini-ADR N-4: list endpoint 跨租户聚合返回**包含 tenant_id 列** over "纯数据返回"
**Context**:UI 拿到跨租户数据时需要在表格上显示"这一行属于哪个 tenant",否则没法区分。
**Decision**:每个 list endpoint 在 `CrossTenant` 模式下返回的 DTO 每行都带 `tenant_id` 字段;并在 response 顶层加 `cross_tenant: bool` 标识(UI 据此决定是否渲染 "Tenant" 列)。
**Consequences**:DTO 都得加可选 `tenant_id` 字段(`SingleTenant` 模式下也可填,冗余但一致);UI 不需要根据上下文猜列。

### Mini-ADR N-5: `SYSTEM_CROSS_TENANT_QUERY` + `SYSTEM_TENANT_SWITCH` 双 audit action over 单一 action
**Context**:跨租户查询 与 切到具体租户 是两类操作,合一 audit 不利于后续审查/告警。
**Decision**:加两个独立 audit action。`SYSTEM_CROSS_TENANT_QUERY` 在 `tenant_id="*"` 时落;`SYSTEM_TENANT_SWITCH` 在 `tenant_id ≠ principal.tenant_id`(且 principal 是 system_admin 或 mTLS)时落。
**Consequences**:audit 量增加但可控(typical system admin 一天几十次操作);后续基于 audit 做"系统管理员行为告警"很方便。

---

## 4. 数据库迁移(N.1 PR2)

```python
# 0035_role_binding_platform_scope.py
def upgrade() -> None:
    # 加列(NOT NULL DEFAULT false 兼容现有行)
    op.add_column(
        "role_binding",
        sa.Column("platform_scope", sa.Boolean(), nullable=False, server_default="false"),
    )
    # 改 tenant_id 为 nullable(platform_scope=true 时为 NULL)
    op.alter_column("role_binding", "tenant_id", nullable=True)
    # CHECK constraint
    op.create_check_constraint(
        "role_binding_platform_scope_ck",
        "role_binding",
        "(platform_scope = false AND tenant_id IS NOT NULL AND role IN ('admin','operator','viewer'))"
        " OR "
        "(platform_scope = true AND tenant_id IS NULL AND role = 'system_admin')",
    )
    # 局部 UNIQUE 索引(每个 user 最多一个 platform_scope binding)
    op.create_index(
        "uq_role_binding_user_platform",
        "role_binding",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("platform_scope = true"),
    )

def downgrade() -> None:
    op.drop_index("uq_role_binding_user_platform", "role_binding")
    op.drop_constraint("role_binding_platform_scope_ck", "role_binding")
    op.alter_column("role_binding", "tenant_id", nullable=False)
    op.drop_column("role_binding", "platform_scope")
```

> **注**:迁移号 0035 假设 0034 是 J.12 的 `eval_dataset`(2026-05-25 已合)。落地 PR 时按当时实际编号。

---

## 5. PR 链(6 PR)

| PR | 内容 | 估时 |
|----|------|------|
| **PR1** [x] 设计补丁(纯文档) — PR #265 | 本文档 + Mini-ADR N-1..N-5 + ITERATION-PLAN 加 Stream N 段 + memory:cross-tenant-admin | 0.5 天 |
| **PR2** [x] 数据层 — PR #266 | migration 0035 + ORM `RoleBindingRow.platform_scope` + DTO `RoleBindingRecord` + `Role.SYSTEM_ADMIN` enum + 测试 | 1 天 |
| **PR3** [x] Principal + Auth — PR #267 | `Principal.is_system_admin` + `allowed_tenants` 支持 `"*"` + `ApiKeyVerifier` / `JwtVerifier` 接 platform_scope role_binding 查询 + 测试 | 1 天 |
| **PR4** [x] RLS 接入点 + audit — PR #268 | `tenant_scope.py` `ensure_tenant_scope` + `bypass_rls_session()` + `applied_scope()` + `SYSTEM_CROSS_TENANT_QUERY` / `SYSTEM_TENANT_SWITCH` audit action + 单测 + 集成测 | 1 天 |
| **PR5a** [x] list API 改造 — 5 endpoints — PR #269 | agents/skills/triggers/curation/eval-datasets 接入 `ensure_tenant_scope` + `applied_scope` + 15 集成测试 | 1 天 |
| **PR5b** [x] list API 改造 — 6 endpoints — PR #270 | service_accounts/role_bindings/sessions/memory/artifacts/api_keys 接入 + 18 集成测试 + 6 store ABCs × 3 impls 新增 `list_all_tenants` | 1 天 |
| **PR6** [x] role_binding API + eval + 收尾 — 本 PR | `POST /v1/role_bindings` 加 `platform_scope` + `GET ?platform_scope=true` filter + `tools/eval/platform_admin.py` (8 cases) + 5 集成测试 + STREAM-N-DESIGN 修订记录 + ITERATION-PLAN `[x]` + 零债 6 条 | 0.5 天 |

总估时 **~7-9 天**(一个全职 backend),与 Stream H.1b UI scaffold **可并行**(UI 可先 mock system_admin 视角)。**Stream H.1b 上线需等本 Stream 完成**。

---

## 6. 关键文件 / 复用点

- **现有 `Principal` + AuthMiddleware** — `services/control-plane/src/control_plane/auth/middleware.py`(L117-127)、`packages/expert-work-protocol/src/expert_work/protocol/auth.py`(Principal 定义)
- **现有 `allowed_tenants` mTLS 范式** — `services/control-plane/src/control_plane/auth/mtls.py`(`system_tenant_id` 设置)
- **现有跨租户 API 范式** — `packages/expert-work-protocol/src/expert_work/protocol/audit.py`(L183 `tenant_id: UUID | Literal["*"]`)+ `packages/expert-work-runtime/src/expert_work/runtime/audit/logger.py`(L110-111)
- **现有 RLS bypass** — `packages/expert-work-persistence/src/expert_work/persistence/rls.py`(`bypass_rls_var` ContextVar)
- **现有后台 worker bypass 范式** — `services/control-plane/src/control_plane/scheduler.py` / `curation_worker.py` / `quota/reaper.py`(`_bypass_rls()` 上下文)
- **现有 list endpoint** — 14 个 router 在 `services/control-plane/src/control_plane/api/*.py`(每个 ~半天改造)
- **memory 引用** —
  - [target-product-form](../../.claude/projects/-Users-mac-src-github-jone-qian-expert-work/memory/project_target_product_form.md)
  - [admin-ui-design-baseline](../../.claude/projects/-Users-mac-src-github-jone-qian-expert-work/memory/project_admin_ui_design_baseline.md)
  - [complete-not-minimal](../../.claude/projects/-Users-mac-src-github-jone-qian-expert-work/memory/feedback_complete_not_minimal.md)
  - [no-design-choice-disguise](../../.claude/projects/-Users-mac-src-github-jone-qian-expert-work/memory/feedback_no_design_choice_disguise.md)

---

## 7. 与 Stream H 的衔接

- Stream H 任何 PR(H.1b+)**前提**:本 Stream 已合
- Stream H.1b UI:
  - tenant switcher dropdown 顶部加 "All tenants" 选项(仅 system_admin 可见)
  - 每个 list 页面 props 加 `crossTenant: boolean`,真时表格加 "Tenant" 列
  - 路由 `/system/*` 仅 system_admin(用 `RequireSystemAdmin` route guard)
  - 角色识别:`useAuth()` 返回 `{ user, role, isSystemAdmin, currentTenantScope: UUID | "*" }`
- Stream H 设计文档([STREAM-H-DESIGN.md](./STREAM-H-DESIGN.md))verification 节加一条:
  - "[ ] system_admin 视角端到端验证:登录 → 默认 'All tenants' 视图 → 切到具体 tenant → 切回 → audit 全部留痕"

---

## 8. 风险与缓解

| 风险 | 缓解 |
|---|---|
| `tenant_id="*"` 全表扫描慢 | 所有现有索引按 `(tenant_id, ...)` 建,bypass RLS 时 PG 仍能用索引;但 ORDER BY + LIMIT 优化要确认。验收时跑 EXPLAIN 抽检 3 个 endpoint(agents/runs/audit)|
| 系统管理员误操作面变大 | 默认仅"读"权限(M0 不做跨租户写);UI 横幅持续显示"系统管理员视角",destructive 操作仍走二次确认;audit 全留痕 |
| 现有租户管理员被无意提权 | migration `platform_scope DEFAULT false` 保证现有 admin 不会被自动提为 system_admin;system_admin 只能通过另外的 platform_scope role_binding 显式创建 |
| RLS bypass 漏在错误 endpoint | 集成测试矩阵硬性覆盖每个 list endpoint 的 5 行(tenant_admin 自家 / tenant_admin 别家 / tenant_admin "*" / system_admin 任意 / system_admin "*");CodeQL 自定义规则可后补 |
| `role_binding` migration 时锁表 | `role_binding` 表小(每租户 < 100 行,通常),`ALTER TABLE ADD COLUMN ... DEFAULT false` PG 11+ 是 metadata-only;`ALTER COLUMN ... NULL` 同;无显著锁 |

---

## 9. 验证

### 单测
- `Principal.is_system_admin` 由 verify 流计算正确(单测 4 种组合:JWT/有/无 platform binding × API Key/有/无)
- `ensure_tenant_scope` 行为矩阵 5×4 矩阵
- `Role.SYSTEM_ADMIN` enum + `RoleBindingRecord.platform_scope` validator
- migration 0035 upgrade/downgrade 来回测试

### 集成测试
- 14 个 list endpoint × 5 行测试矩阵(共 70 个 case)
- audit:`tenant_id="*"` 查询自动落 `system:cross_tenant_query`;切 tenant 落 `system:tenant_switch`
- `POST /v1/role_bindings` 创建 platform_scope binding(仅 system_admin 可)
- 性能:EXPLAIN ANALYZE 抽 3 个 endpoint 的跨租户聚合查询,确认走索引

### eval
- `tools/eval/platform_admin.py` 4-6 个场景(认证 / scope 解析 / audit 落库 / 跨租户聚合)pass_rate ≥ 阈值
- `run_baseline.py` 激活 `N_platform_admin` runner

### 零债 6 条
- 无 TODO / 测试覆盖达标 / 设计文档同步 / 可观测齐全(metric + audit)/ CI 全绿 / bug 不遗留

---

## 修订记录

| 日期 | 版本 | 说明 |
|---|---|---|
| 2026-05-25 | v1.0 | 初稿:Role.SYSTEM_ADMIN + platform_scope + ensure_tenant_scope + 14 list API 改造范式 + 5 Mini-ADR(N-1..N-5)+ 6 PR 估时 |
| 2026-05-25 | v1.1 | Stream N 全部 6 PR 合并完成(#265 / #266 / #267 / #268 / #269 / #270 / 本 PR);N.4 落地 11 个 list endpoint(原估 14,经盘点发现 sessions/threads 是同一资源,runs 是 sessions 嵌套不计独立 list);N.6 + N.7 + 零债 6 条核验通过 |
