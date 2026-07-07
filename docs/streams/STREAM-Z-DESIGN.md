# Stream Z — Chargeback / 用量面(变现层出口)

> 平台中心化治理+变现路线图的**最后一块**:消费 Y4 `tenant_billing_ledger`,
> 让租户看自己的用量/成本、system_admin 看跨租户分账、成本可观测。
> 设计先行见 [[feedback_design_first_iteration]];消费入口见 [[reference_billing_meter_and_entitlement]]。

## 0. 背景 / 缺口(已 file:line 核实,2026-06-04)

变现地基(Y)已通:`token_usage`(计量)→ `model_rate_card`(Y3 定价+加价)→ `billing-rollup-job`(逐行时序定价+幂等)→ `tenant_billing_ledger`(Y4,bucket `(tenant_id,month,provider,model,agent_name)` + `base/markup/billed_cost_micros` 拆分 + `priced` flag,**tenant-scoped RLS**)。**缺出口**:没有任何 API 让租户/admin 看到成本。

- 现状 **无 `/v1/usage` 面**(全仓 grep 零命中)——greenfield。
- ledger store(`persistence/billing/ledger.py`)只有 `list_for_tenant(*, tenant_id, month)`(租户自隔离)+ `delete_month`;**无跨租户读** → Z2 需加 `list_for_month_all_tenants`(bypass)。
- `token_usage` store 有 `list_for_tenant_window(*, tenant_id, start, end)`(Y4 加,无 cap)→ Z1 当月实时 token 直读它(rollup 前)。
- RBAC `billing` 资源(Y3 加):SYSTEM_ADMIN/ADMIN `{read,write,delete}`,租户角色 `{read}` → Z 的租户读用 `billing:read`,已就位。
- Prometheus `expert_work_counter`(`expert_work.common.observability`);rollup job 已发 `expert_work_billing_rollup_*`。
- 迁移头 `0060`(Z2 跨租户读不改 schema,**无新迁移**)。

## 0.1 已锁业务决策(沿用 Y0,2026-06-04)

- **成本透明度 = 租户只看最终计费价**:租户 API **只暴露 `billed_cost_micros`** + token 用量;`base_cost`/`markup` 拆分**仅 system_admin chargeback 可见**。这是 Z 的硬约束——租户面任何字段都不得回显 base/markup/margin。

## 1. Mini-ADRs

### Z-1 租户用量/成本 API(`billing:read`,RLS 自隔离)
- `GET /v1/usage/cost?month=YYYY-MM&group_by=agent|model|none`:读 `tenant_billing_ledger.list_for_tenant(tenant_id, month)`(租户 RLS 自隔离),按 `group_by` 聚合,返回每组 `{key, input/output/cache_* tokens, billed_cost_micros}`(**绝不含 base/markup**)+ 顶层 `total_billed_cost_micros`。`month` 缺省=当月(`date.today()` 的月首)。`group_by=none` 返回原始 bucket(provider/model/agent_name 三维)。`unpriced` bucket(`priced=false`)单列计数/token,`billed=0`,标 `unpriced: true`,让租户知道有未计价用量(透明但不暴露平台成本)。
- `GET /v1/usage/tokens?month=`:**当月实时**(rollup 可能滞后,直读 `token_usage` 的 windowed read),返回当月 token 总和(input/output/cache_*)+ 按 agent/model 拆分。无成本(纯 token);用于"实时看板",cost 走 ledger(滞后到下次 rollup)。
- 路由 `api/usage.py` `build_usage_router()`,`Depends(require("billing","read"))`;响应 `{success,data,error}` 信封(沿用 rate_card/mcp_catalog 风格)。

### Z-2 admin chargeback API + 成本指标(system_admin,bypass)
- `GET /v1/admin/billing/chargeback?month=&tenant_id=`(可选 tenant_id 过滤):**跨租户**(`bypass_rls_session()` + `_require_system_admin`),返回每租户 `{tenant_id, base_cost_micros, markup_cost_micros, billed_cost_micros, margin_micros=markup, tokens...}`(显**全拆分**=admin 视角)。需 ledger store 加 `list_for_month_all_tenants(month)`(bypass,无 tenant 过滤,仿 `list_skills_all_tenants` 模式)。
- 路由 `api/billing_admin.py`(或并入 `api/rate_card.py` 同 `billing` 资源域),`Depends(require("billing","read"))` + 内联 `_require_system_admin`(平台面纵深防御,同 mcp_catalog)。
- **成本 Prometheus 指标**:`billing-rollup-job` 自增 `expert_work_llm_cost_micros_total{tenant,model}`(billed)——在 Y4 rollup 写 bucket 处加(小幅跨 stream 触碰已落地 job);label **不含 base/markup**(指标对运维可见,但保守只发 billed)。基数控制:label 只 `tenant`+`model`(不含 agent_name,避免高基数)。

### Z-3 看板 UI(前端,Z 给契约本 PR 不实现 UI 逻辑深做)
- 租户用量页:月选择器、总 billed 成本、按 agent/model 拆分(消费 Z-1)、token 计数(消费 Z-1 tokens)、环比(可选)。
- system_admin chargeback 页:跨租户表,每租户 billed/base/markup/margin(消费 Z-2)。
- 复用 Admin UI 设计基线([[project_admin_ui_design_baseline]]);i18n en/zh-CN;vitest+storybook+Playwright+axe。**租户页严禁渲染 base/markup**(前端也守约束)。

### Z-4 范围外(M2)
- **Invoices/发票**:发票=月度 ledger 的冻结快照(编号/税务/PDF/支付),无 schema 阻塞,chargeback 报表已交付变现价值 → 推迟 M2。
- 实时成本(当月 cost 不等下次 rollup):Z-1 `tokens` 给实时 token,`cost` 给 rollup 后 billed;真要当月实时 cost 可在 Z-1 cost 端按 `token_usage` 现算(用 rate_card.resolve),但**重复 rollup 逻辑**,YAGNI——M1 接受 cost 滞后到 hourly rollup。

## 2. 风险 / 约束

- **最高约束:租户面不得泄露 base/markup**。缓解:① Z-1 响应模型**物理上不含**这些字段(不是过滤,是 schema 里就没有);② 测试断言租户响应键集不含 `base/markup/margin`;③ 前端 Z-3 同守。
- **跨租户读隔离**:Z-2 `list_for_month_all_tenants` 必须 `bypass_rls_session()`(ledger 是 tenant-scoped RLS,scoped session 读不到别的租户)+ `_require_system_admin`;非 admin 打 `/v1/admin/billing/*` → 403。RLS 回归测:scoped 租户经 Z-1 只见自己;Z-2 非 admin 拒。
- **零热路径**:全部读 ledger/token_usage,不碰 LLM/quota run loop。
- **滞后语义**:cost(ledger)滞后 rollup;tokens(token_usage)实时。API 文档/响应 `as_of` 标注数据时点,避免误解。

## 3. PR 切分(每个 CI 全绿、零债)

- **Z0 设计先行**(本 PR):`STREAM-Z-DESIGN.md` + `ITERATION-PLAN.md` Z backlog。
- **Z1 租户用量/成本 API**:`api/usage.py`(`/v1/usage/cost` + `/v1/usage/tokens`,`billing:read`,只暴露 billed)+ 响应模型 + 测试(自隔离 / 键集不含 base-markup / group_by / unpriced / 当月实时 tokens)。
- **Z2 admin chargeback API + 指标**:ledger store `list_for_month_all_tenants`(bypass)+ `api/billing_admin.py`(system_admin,跨租户,全拆分)+ `expert_work_llm_cost_micros_total` 指标(rollup 自增)+ 测试(非 admin 403 / 跨租户聚合 / margin 正确)。
- **Z3 看板 UI**(前端):租户用量页 + admin chargeback 页 + i18n + vitest/storybook/Playwright/axe。

**关键路径** Z0→Z1→Z2→Z3。**完成 = 路线图收官**:平台供给可治理(W/X)、用量可计量可加价(Y)、成本可看可分账(Z)——Expert Work 从"自助工具"成为"可变现的多租户 PaaS"。

## 4. Verification

1. **租户自隔离 + 不泄露**:scoped 租户 `GET /v1/usage/cost` 只见自己的 ledger;响应键集**不含** `base_cost`/`markup`/`margin`;两租户互不可见。
2. **group_by**:`agent`/`model`/`none` 聚合数与 ledger bucket 对得上;`total_billed` = 各组之和;`unpriced` bucket 单列、billed=0。
3. **当月实时 tokens**:产 `token_usage` 行 → `GET /v1/usage/tokens` 即时反映(不等 rollup);cost 端在 rollup 后才出 billed。
4. **admin chargeback**:system_admin 跨租户聚合、显 base/markup/billed/margin;非 admin → 403;`bypass` 下能读 NULL... (ledger 非 NULL-tenant,是 bypass 读全租户)。
5. **指标**:rollup 后 `expert_work_llm_cost_micros_total{tenant,model}` 增量 = billed 总和;label 无高基数 agent_name。
6. **通用**:`pre-commit`、`pytest -m "not integration"`、mypy 全 CI 范围(见 [[reference_ci_lint_type_test_scopes]]);前端 typecheck/test/build/playwright;CodeQL 不放 assert 内副作用 / 不 log 租户派生值进 `extra=`;`billing` 资源已存无需双 Literal 新增(审计若记 chargeback 查询用现有 action 或不记读)。

## 5. 复用锚点（exact files）

| 关注点 | 资产 | 文件 |
|---|---|---|
| ledger 读(租户) | `list_for_tenant` | `persistence/billing/ledger.py` |
| ledger 跨租户读(Z2 加) | 仿 `list_skills_all_tenants` | `persistence/skill/sql.py` |
| 当月实时 token | `list_for_tenant_window` | `persistence/token_usage_store.py` |
| 租户面 API 范式 | `tenant_quotas.py` / `quota.py` | `api/tenant_quotas.py` |
| 平台 admin API 范式(bypass+system_admin) | `rate_card.py` / `mcp_catalog.py` | `api/rate_card.py` |
| RBAC `billing` | `Resource` Literal | `auth/rbac.py` |
| bypass | `bypass_rls_session` | `tenant_scope.py` |
| Prometheus | `expert_work_counter` | `common/observability/__init__.py` |
| 信封 | `{success,data,error}` | `api/rate_card.py` |

## 6. Out of scope / follow-ups

- Invoices / 支付集成(M2)。
- 当月实时 cost(现 cost 滞后 rollup;真需要再在 Z-1 现算)。
- 成本预算告警 / 超额阈值通知(消费 ledger,独立 feature)。
- 多币种 / 汇率(现 micro-USD 单币种)。
