# Stream P — E2E Readiness(平台入驻 + 端到端测试 Phase 0–6 跑通)(设计先行)

> 让 [`canonical-agent-e2e-test.md`](../runbooks/canonical-agent-e2e-test.md) 的 **Phase 0–6 在 dev 环境真正跑通**:
> 平台管理员引导(第一个 system_admin)→ 平台级运行时配模型/工具 → 创建租户 → 注册 canonical agent
> → 多轮记忆 / 持久工作区 / artifact+审批 / 多模态 / SLO 采集。
>
> **起因**:用户准备按 E2E SOP 做 M0→M1 Gate 测试。核查发现这份 SOP(#306)早于 Stream N(system_admin)
> 和 Stream O(凭证/provider catalog),前半段既无文档又有真实代码缺口;`/goal` 后把范围拉到**全部 Phase**,
> 经 5 个 Explore agent 逐 Phase 体检 + 自核得到完整缺口图。
>
> **本次范围(用户拍板)**:dev 跑通 **Phase 0–6**;**Phase 7**(staging Linux 安全/数据保护)单列后续。

设计先行规则([memory:design-first-iteration](../../.claude/projects/-Users-mac-src-github-jone-qian-expert-work/memory/feedback_design_first_iteration.md)):
**任何一行代码落地之前**,先完成本设计文档 + Mini-ADR 锁定。

---

## 0. 背景与范围(2026-05-29 用户拍板)

### 0.1 完整缺口图(Phase 0–7,经 file:line 体检)

| Phase | 现状 | 缺口 | 性质 | 本次 |
|-------|------|------|------|------|
| **0 前半段** | 🔴 缺口最大 | bootstrap / 建租户 API / 平台配模型 / manifest / 登录 / 文档 | 代码+UI+文档 | ✅ |
| **1 eval baseline** | ✅ 基本能跑(`run_baseline.py` + `m0_gate_baseline.yaml` 14 cap + tests 都在) | `--warmup` 缺、ANTHROPIC_KEY 可选(ScriptedJudge fallback) | 仅文档 | ✅ |
| **2 长记忆** | ✅ 能跑(Playground `PlaygroundTab.tsx`、`/v1/memory` CRUD+UI 齐、cross-tenant RLS) | auto-writeback 机制说明 | 仅文档 | ✅ |
| **3 工作区/冷启动** | ⚠️ 大体能跑(持久 volume、reaper 只删 session 保留 volume、cold_start 指标在) | `POST /v1/sandboxes/reap?force=true` **端点缺**;macOS runc 非 gVisor | 小代码+文档 | ✅ |
| **4 artifact/审批** | ⚠️ 部分(artifact API + save_artifact + ApprovalCard + resume + audit 齐) | query 应 `status=paused`;`hitl.triggers.match` 正则运行期**不存在**(只有 `approval_required_tools`) | 仅文档 | ✅ |
| **5 多模态** | ⚠️ API 在(uploads 端点 + content-block + ask_image 齐) | **Playground 无传图 UI**;dev 需真 key 才能真推理;supports_vision 说明 | UI+文档 | ✅ |
| **6 SLO 8 项** | 🔴 跑不通 | sandbox-supervisor **无 scrape job**(orchestrator 是进程内库,指标本就在 control-plane `/metrics`);Grafana overview 只 4/8 panel | 基础设施+dashboard | ✅ |
| **7 安全/数据保护** | 🔴 跑不通 | gVisor 7 用例 / cross-tenant 3 命名件套 / KMS runbook 缺;**staging Linux 主机 TBD** | 代码+环境 | ⏸ 后续 |

### 0.2 Phase 0 范围决策(管理员入驻)

| # | 决策 | 选定 | 反对方案为何弃 |
|---|------|------|-----------------|
| 1 | "配置模型/工具"范围 | **B**:额外做平台级运行时配模型 UI/API(DB 覆盖 env、DB 优先) | A(只文档化 env)— 用户要在管理面里配模型,不接受改 env 重启 |
| 2 | "创建租户"形态 | **A**:真端点 `POST /v1/tenants`(system_admin 门控)+ Admin UI 入口 | B(沿用 lazy-init 只补文档)— "建租户"仍不是一等公民 |
| 3 | 第一个 system_admin bootstrap | **A**:脚本/CLI + runbook | B(dev seed)生产不可用;C(空表窗口 API)有安全窗口顾虑 |

### 0.3 E2E 范围决策(Phase 1–6 readiness)

| # | 决策 | 选定 | 说明 |
|---|------|------|------|
| 4 | dev 跑真实 agent turn | **A**:配真 LLM key(最简) | `mock-upstream` 只是 echo server 非 mock LLM;dev docker-compose 经 env 注入真 key + `.env.example`,不入仓明文;**不**造 mock-LLM |
| 5 | Phase 7 是否本次做 | **A**:先做 Phase 0–6 | Phase 7 需 staging Linux 主机(现 TBD),单列后续 |
| 6 | Phase 4 审批门 | **A**:用现有 `policies.approval_required_tools`(精确工具名) | SOP 的 `hitl.triggers[].match` 正则运行期不存在;不新建正则,改 manifest + 文档贴合现状 |

### 0.4 范围纪律(simplicity first)

**显式不做**:mock-LLM 服务、HITL 正则、凭证轮换/版本化历史、凭证诊断页、批量导入 agent、
manifest JSON Schema/dry-run/文件上传 UI、跨租户凭证共享、租户级 tool/skill allowlist、独立 Artifacts UI 页。
Phase 7 单列。将来要的单列 backlog。

---

## 1. Mini-ADRs(统一 P- 前缀,避免与 Stream O 的 O-15/O-16 冲突)

### 1.1 建租户

| ADR | 决策 | 理由 |
|-----|------|------|
| **P-1** | 不加 tenants 主表,沿用懒初始化;`POST /v1/tenants` = 显式写第一行 `tenant_config` | 加主表要 RLS+FK 回填+backfill,超范围。要 tenant 生命周期状态再 revisit |
| **P-2** | 权限用 `is_system_admin` 直接判,**不**给 RBAC 加 `"tenant"` Resource | 建租户是平台级;RBAC 是 per-tenant role,给 ADMIN 加会让每个 tenant ADMIN 都能建。照 `role_bindings.py:66` inline |
| **P-3** | store 加显式 `create`(已存在 → 409),**不**复用 `upsert` | `upsert` ON CONFLICT DO UPDATE 静默覆盖,语义错 |
| **P-4** | 建租户只建 `tenant_config` 一行,不连带 quota/binding/user | 各项后续单独配;单 INSERT 原子,无需事务 |
| **P-5** | `tenant_id` 默认服务端 `uuid4()`,也接受客户端传 | 支持上游幂等 provisioning;重复(409)只在客户端传 UUID 时可达 |

### 1.2 bootstrap

| ADR | 决策 | 理由 |
|-----|------|------|
| **P-6** | `python -m control_plane.bootstrap_admin --subject-id=<uuid>`,复用 Settings/`SqlRoleBindingStore`/`bypass_rls_session`;幂等;不建 platform 租户 | 模块内调复用引导 wiring;platform binding `tenant_id=NULL` 不需要租户行 |

### 1.3 平台级运行时配模型

| ADR | 决策 | 理由 |
|-----|------|------|
| **P-7** | 存储 = DB 行覆盖 env seed,**DB 优先**;env 仍 fallback,不自动把 env 写进 DB | 未写 DB 的部署行为**完全不变**(向后兼容);DB 清空退化到当前 env 行为 |
| **P-8** | **只存 ref(`secret://`/`kms://`),拒绝明文**(validator `^(secret\|kms)://`) | 延续 Stream O ref 契约;明文绝不落 DB/日志/audit |
| **P-9** | resolver 读"活快照":`PlatformCredentialsService`(TTL 缓存 merged 视图,仿 `TenantConfigService`),写端点 `invalidate()`;`CredentialsResolver` 加可选 getter(**additive**) | 避免每次 LLM 调用查 DB;getter additive → 旧 dict 调用与测试不变 |
| **P-10** | boot `_validate_platform_catalog` 对 env 字段语义不变(仍 fail-fast);DB 行写时校验,不加新 fatal boot check | 今天能 boot 的部署不会因本 Stream 起不来 |
| **P-11** | 平台端点 `is_system_admin` inline 门控(同 P-2),handler 在 `bypass_rls_session()` 内 | 平台凭证 tenant-less(同 role_binding platform-scope) |
| **P-12** | 删除受被引用检查门控(agent 在用/env 定义 → 409);`enabled=false` 软停用永远可用 | 删在用 provider 会让其下次 LLM 调用 fail;UI 引导用停用 |

### 1.4 E2E readiness(Phase 1–6)

| ADR | 决策 | 理由 |
|-----|------|------|
| **P-13** | dev 真实 turn 用真 LLM key:docker-compose env 注入,`.env.example` 占位,文档强调本地填,**不入仓明文** | 决策 4;最简、不造 mock-LLM |
| **P-14** | Phase 3 `POST /v1/sandboxes/reap?force=true`:control-plane admin 端点(`is_system_admin`)→ sandbox-supervisor reaper,返 `{reaped_count}`,volume 保留语义不变 | SOP 强制回收演示依赖此端点;reaper 现有 `run_once()` 只删 session |
| **P-15** | Phase 6 可观测接线:**确认** orchestrator 4 指标已在 control-plane `/metrics`(进程内同 registry);**新增** sandbox-supervisor scrape job(+ 确认其 `/metrics`);Grafana `01-overview.json` 补 TTFT/duration/stream-stale/cold-start 4 panel | orchestrator 非独立服务故无独立 scrape job;sandbox 是独立服务需 scrape |
| **P-16** | Phase 5 Playground 传图:`PlaygroundTab.tsx` 加文件选择 → `POST /v1/sessions/{thread_id}/uploads` → `image_ref` 进 turn | API 已齐,只差 UI 入口 |
| **P-17** | Phase 4 贴合现状:canonical manifest 用 `policies.approval_required_tools`(精确工具名);SOP query 改 `status=paused`;不做正则 | 决策 6;`hitl.triggers.match` 运行期不存在 |
| **P-18** | E2E SOP 全面重写(Phase 0–6):profile 组合 / bootstrap / 登录 recipe / 真 `POST /v1/tenants` / 平台配模型 / 删 `--warmup` / `status=paused` / runc 非 gVisor 标注 / 多模态 dev-vs-staging 分层 / Phase 7 单列 | 修正全部过期点 |

---

## 2. 数据模型 & 持久层(平台配模型)

新表(migration `0049_platform_credentials`)仿 `role_binding` 的 **tenant-less、RLS-exempt** 形态(全程 `bypass_rls_session`,不挂 RLS policy):

```
platform_provider_credential
  provider     TEXT PK        -- 必须 ∈ PROVIDER_CATALOG(app 层校验)
  secret_ref   TEXT NOT NULL  -- 仅 secret:// / kms://
  enabled      BOOLEAN NOT NULL DEFAULT true   -- 动态 supported_providers 等价物
  created_at / updated_at TIMESTAMPTZ / updated_by TEXT

platform_tool_credential       -- 同构,PK 为 tool TEXT
```

revision id ≤ 32 字符([memory:alembic-revision-id-32-chars]);down_revision = `0048_tenant_mcp_creds`。
store 包 `persistence/platform_credentials/{base,sql,memory}.py` 仿 `tenant_config/`;protocol 记录 + ref validator。

---

## 3. Service + Resolver wiring(boot 协调核心)

新增 `control_plane/platform_credentials.py` → `PlatformCredentialsService`:
- `effective_provider_credentials()`:merge env seed(`settings.effective_platform_provider_credentials`)+ enabled DB 行,**DB 优先**;TTL 缓存。tool 同。
- `effective_supported_providers()`:上面 keys。`invalidate()`:写端点调。

`lifespan` 把 frozen-dict resolver 构造改为 service-backed getter;boot 仍对 env fail-fast(P-10);merged 视图 lazy 计算,`create_app` 不查 DB。`tenant_config.py:_credentials_catalog` 改读 merged(行为变更:面板反映运行时启用项,additive 超集)。

---

## 4. API 表面

### 4.1 建租户
`api/tenants.py` `build_tenants_router()`,prefix `/v1/tenants`(`POST ""` 不与 `{tenant_id}/...` 冲突):
```
POST /v1/tenants  body: { tenant_id?, display_name(必填), plan? }
  gate: not is_system_admin -> 403 PLATFORM_SCOPE_FORBIDDEN
  write: bypass_rls_session() -> store.create(...)  ; audit TENANT_CREATE(块内 emit)
  -> 201 / 已存在 409 TENANT_ALREADY_EXISTS
```

### 4.2 平台配置
`api/platform_config.py`,prefix `/v1/platform/credentials`,每 handler `is_system_admin` + `bypass_rls_session()`:
| 端点 | 作用 |
|------|------|
| `GET /v1/platform/credentials` | merged 视图(每 provider/tool → name/source(env\|db)/secret_ref/enabled/used_by_agents);**只返 ref/enabled/计数** |
| `PUT .../providers/{provider}` / `PUT .../tools/{tool}` | upsert ref+enabled(422 if ∉catalog/ref 非法);audit + invalidate |
| `DELETE .../providers/{provider}` | 被引用(used>0 或 env 定义)→ 409 `PLATFORM_CREDENTIAL_IN_USE`;否则删 |

### 4.3 sandbox reap(Phase 3)
`POST /v1/sandboxes/reap?force=true`(`is_system_admin`)→ SupervisorClient 调 reaper → `{reaped_count}`。

### 4.4 audit 双 Literal(同 PR 改全,[memory:audit-literal-drift])
- `protocol/audit.py` `AuditAction`:`TENANT_CREATE`、`PLATFORM_PROVIDER_CREDENTIAL_UPSERT/DELETE`、`PLATFORM_TOOL_CREDENTIAL_UPSERT/DELETE`
- `control_plane/audit.py` `ResourceType`:`"tenant"`、`"platform_credential"`

---

## 5. Admin UI

| 页面 | 形态 |
|------|------|
| `pages/SettingsCreateTenant.tsx` | Antd form(display_name 必填 / plan / tenant_id 选填);`isSystemAdmin` 门控;`api/tenants.ts:createTenant` |
| `pages/SettingsPlatformConfig.tsx` | 仿 `SettingsTenantCredentials.tsx`,**非 tenant-scoped** + `isSystemAdmin` 门控(非 admin 见 Alert);provider/tool 表 + source Tag + enabled Switch + used-by + 编辑 modal |
| `agent_detail/PlaygroundTab.tsx`(改) | 加文件选择 → uploads → `image_ref` 进 turn(Phase 5) |

路由 `router.tsx`(`/settings/create-tenant`、`/settings/platform`);nav 仅 `isSystemAdmin` 可见(`SettingsRoleBindings.tsx:71` pattern);i18n zh-CN+en 类型安全。

---

## 6. PR 拆分(~13 PR)

| PR | 分支 | 内容 | 依赖 | Phase |
|----|------|------|------|------|
| **A** ✅[#328] | `stream-p/a-design` | 本设计 + ITERATION-PLAN(含本轮 E2E 扩展) | — | — |
| **B** | `stream-p/b-bootstrap` | bootstrap CLI + runbook(并 P-13 dev-key recipe) | — | 0 |
| **C→D→E** | `stream-p/{c,d,e}-tenant-*` | 建租户 store → 端点 → Admin UI | 链 | 0 |
| **F→G→H→I** | `stream-p/{f,g,h,i}-platform-*` | 持久层+resolver → service+boot → API → Admin UI | 链 | 0 |
| **K** | `stream-p/k-sandbox-reap` | `POST /v1/sandboxes/reap` admin 端点 + 集成测 | — | 3 |
| **L** | `stream-p/l-observability` | sandbox scrape job + 确认 orchestrator 指标 + Grafana 补 4 panel | — | 6 |
| **M** | `stream-p/m-playground-upload` | Playground 传图 UI | — | 5 |
| **J** | `stream-p/j-manifest-config` | canonical manifest(`approval_required_tools`+`supports_vision`)+ dev key 注入 + 登录 recipe | B,(F-I 或 env) | 0/4/5 |
| **N** | `stream-p/n-e2e-sop` | E2E SOP 全面重写(Phase 0–6 + Phase 7 单列)**capstone** | 全部 feature | 1–6 |

> 关键路径:A → B + C/D → J 先让链走通;F→G→H→I 平台配模型并行;K/L/M 独立可并行;N 收口。PR 可酌情合并(C+D、F+G、K+L)。

---

## 7. 关键复用(已核实)

- 创建型端点模板:`api/service_accounts.py`、`api/tenant_quotas.py`;platform-scope inline gate `api/role_bindings.py:66`
- RLS bypass:`tenant_scope.py:bypass_rls_session()`;用量统计 `api/tenant_config.py:_provider_usage_counts`(泛化全租户)
- TTL 缓存模板:`TenantConfigService`(`settings.py:463` 附近);store 三件套 `persistence/tenant_config/{base,sql,memory}.py`;migration `0047`/`0048`
- 前端模板:`SettingsTenantCredentials.tsx` + `api/tenant_config.ts`;admin 门控 `SettingsRoleBindings.tsx:71`
- **reap**:`services/sandbox-supervisor/.../reaper.py:run_once()`;control→supervisor 经 SupervisorClient
- **可观测**:control-plane `/metrics` `api/metrics.py:build_metrics_router`;sandbox cold_start `supervisor.py:55`;scrape `infra/observability/prometheus.yml`;dashboard `tools/observability/dashboards/01-overview.json`(参照 02/03)
- **Playground 传图**:`PlaygroundTab.tsx`;`api/uploads.py`(返 `image_ref`);content-block `orchestrator/multimodal.py`
- **canonical manifest**:AgentSpec `packages/expert-work-protocol/.../agent_spec.py`(`supports_vision` / `policies.approval_required_tools`)
- **eval**:`tools/eval/run_baseline.py`(无 `--warmup`)、`baselines/m0_gate_baseline.yaml`(14 cap)、`memory_recall.py` + `datasets/memory_recall/zh_en_seed.yaml`
- Keycloak dev:`infra/keycloak/realm-expert-work.json`(`dev/devpass`,sub=UUID)

---

## 8. 与现有 Stream 的关系 + 风险

**关系**:依赖 Stream N system_admin、Stream O CredentialsResolver+Platform Catalog、Stream C.7 TenantConfigService+RLS bypass、Stream H Admin UI、Stream F/G sandbox+可观测、Keycloak dev realm。**与 Stream O 冲突 + 兼容**:平台凭证 env-only → DB 可变(P-7/P-10),未写 DB 部署行为不变。被依赖:M0→M1 Gate E2E + M1 dogfood。

**风险**:
1. **audit 双 Literal 漂移**:`TENANT_CREATE`/`PLATFORM_*` 必须 protocol + control-plane 同 PR 改全([memory:audit-literal-drift])。
2. **resolver getter 触及 expert-work-common**(Read 受限)——保持 additive,验旧 dict 快照测试不变([memory:harness-denies-credentials-paths]:从测试读契约)。
3. **audit 写在 bypass 内**:建租户/平台写的 audit_log 若 FORCE-RLS keyed on tenant_id,emit 要在 bypass 块内——落地前验。
4. **Phase 6 orchestrator 指标假设**:PR L 必须先实测 `expert_work_session_ttft_seconds` 是否已在 control-plane `/metrics`,否则补接线;并确认 sandbox-supervisor `/metrics` 是否已暴露。
5. **缓存跨副本 staleness**:M0 单实例可接受,文档标注,不做跨副本失效。
6. **dev 真 key 不入仓**:env passthrough + `.env.example` 占位。
7. **真实 turn 依赖**:Phase 2/4/5 真实回话/推理依赖真 key,无 key 只验 API/结构层——SOP 分层标注。
8. **Phase 7 阻塞透明**:SOP 标 Phase 7 单列 + 依赖 staging Linux。
9. **CI 门禁**:mypy 不含 control-plane/src;pytest `-m "not integration"`;push 前 `uv run pre-commit run --all-files`([memory:ci-lint-type-test-scopes] / [memory:ruff-strict-lint-traps])。

---

## 9. Verification(本次完成 = dev 跑通 Phase 0–6)

按重写后的 `canonical-agent-e2e-test.md` 走:起栈(`full`+`auth`+`observability`)+ dev 真 key → bootstrap admin 幂等 → OIDC 登录 `/v1/me` is_system_admin → 平台配置页填 provider → `POST /v1/tenants`(201/403/409)→ 注册 manifest → **P1** baseline diff 空 + J.1/3/6 过 → **P2** 跨 thread 召回 + cross-tenant 隔离 → **P3** reap 后文件仍在 + cold_start P95<5s → **P4** artifact 跨 thread + 危险工具 PAUSED → `status=paused` → ApprovalCard → resume + audit → **P5** Playground 传图描述 + ask_image → **P6** 8 项 query 有数据 + Grafana 8 panel。

每 PR 自带测试;后端 `uv run python -m pytest -m "not integration"`;前端 Storybook+Playwright+axe;push 前 `uv run pre-commit run --all-files`。

---

## 10. 后续(不在本迭代)

**Phase 7 — 安全 + 数据保护(staging Linux)**:gVisor 7 用例 + cross-tenant 3 命名件套(现有覆盖散在 `test_tenant_scope_endpoints.py` 等,需集中/补名)+ KMS 轮换 runbook + **staging Linux 主机 provisioning**(现 `environments/staging.yaml` 全 TBD)。等 staging 就绪单列 Stream/PR。
