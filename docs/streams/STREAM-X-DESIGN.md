# Stream X — Platform Skill Library（平台精选 Skill 库 + 租户自建,混合）设计先行

> 大方向见 [memory:project_platform_centralized_governance]:2026-06-03 拍板"平台中心化治理 + 变现"。Skills 决策 = **混合(平台精选库 + 租户自建)**。复用 Stream W 的 entitlement 地基 `tier_satisfies`(见 [memory:reference_billing_meter_and_entitlement])。本 Stream 演进既有 skill 子系统,不破坏租户自建能力。

## 0. 背景 / 缺口

**产品定位**:helix 服务**企业员工(非程序员)**。对员工最直接的价值是"开箱即用的预制能力"(会议纪要、邮件起草、周报、文档摘要、合同要点),一键开启、无需配置。平台精选 skill 库正是这个载体;租户仍可自建专属 skill。

**两个核实出的关键现状(file:line):**

1. **skill 运行时未接线(Stream U 遗留缺口,本 Stream 必须补)**:`make_agent_builder`(`control_plane/runtime.py:178`)/ `make_child_agent_builder`(`subagent_runtime.py:48`)**不构造也不传 `skill_resolver`**;`build_agent`(runtime.py:259)调用不带 `skill_resolver=` → `_load_skills` 收到 `None` → **manifest `spec.skills` 当前在运行时被跳过,租户自建 skill 根本没生效**(app.py:972 注释明确标记为 future PR)。`SkillViewTool` / `skill_activity_recorder` 同理未接。
   → **Stream X 首次把 skill resolution 接进 agent build**(租户 + 平台),顺带激活 Stream U 已建好的租户 skill 运行时。这是必要且正向的,但属**行为变更 + 范围扩张**,设计里显式标注。

2. **skill 表是严格 tenant 隔离,容不下平台(NULL-tenant)行**:`skill`/`skill_version`(`models/skill.py`)`tenant_id NOT NULL`,RLS 严格相等 `tenant_id = current_setting('app.tenant_id')`(迁移 `0029_skill`),`UNIQUE(tenant_id, name)`。要加平台行需:可空 tenant_id + RLS 换 `IS NOT DISTINCT FROM` + COALESCE 唯一索引(与 `mcp_connector_catalog`/`encrypted_secret` 同款)。当前迁移 head = `0056_mcp_catalog_columns`。

**用户拍板(2026-06-04)**:Skills = 平台精选库 + 租户自建(混合),优先于变现层(Y/Z),贴"服务企业员工"定位。

## 1. Mini-ADRs

- **X-1 平台 skill = 复用 `skill`/`skill_version` 表的 NULL-tenant 行(非新表)**。迁移 `0057_platform_skill`:① `skill.tenant_id` / `skill_version.tenant_id` 改 NULLABLE;② RLS 严格相等 → **`IS NOT DISTINCT FROM NULLIF(current_setting('app.tenant_id',true),'')::uuid`**(照抄 0050/0055);③ 删 `UNIQUE(tenant_id,name)` 约束,建 `COALESCE(tenant_id, zero-uuid), name` 唯一索引;④ 加 `required_tier`(CHECK free|pro|enterprise,default 'free')到 `skill` 表。**理由**:对非 NULL tenant_id,`IS NOT DISTINCT FROM X ≡ = X`,租户隔离**逐字节不变**;NULL 平台行仅在 `app.tenant_id` 未设(bypass)时可见 —— 与 catalog 完全同模型。**最高风险 = 在已有数据表上换 RLS**,必带 RLS 回归测(租户 session 看不到 NULL 平台 skill;bypass 看得到;两租户互不可见对方 skill —— 沿用 W-8 教训)。

- **X-2 `required_tier` 在 `skill`(offering 级,非 version 级)**。复用 `tier_satisfies(tenant_plan, skill.required_tier)`。门控在**绑定时(resolver)+ 租户列表(entitled 标记)**,不在运行热路径。`Skill` protocol record + ORM 加 `required_tier: TenantPlan = FREE`。

- **X-3 Curator 跳过平台(NULL-tenant)行**。平台 skill 是**共享**资源,不应被任一租户的 inactivity 触发 stale/archive。`curator_distinct_tenant_ids()`(`skill/sql.py`)加 `WHERE tenant_id IS NOT NULL`;`_sweep_tenant` 永不收到 NULL。平台 skill 生命周期 = **system_admin 显式管理**(发布/归档);默认 `pinned=true` 作 belt-and-suspenders(即使误入 sweep 也被 pinned 跳过)。平台 skill 的 `last_used_at` bump 可保留为信息性(curator 不读)。

- **X-4 Resolver 双查 + 首次接线 + 档位门控**。`control_plane` 加 `make_skill_resolver(store, tenant_id)`:**先查租户**(`resolve_by_name`/`resolve_pinned`,RLS 自隔离)→ **miss 再查平台**(同方法但 `tenant_id=None` 包 `bypass_rls_session()`)。命中平台 skill 时 `tier_satisfies(plan, skill.required_tier)`,不满足返**专门 reason `not_entitled`** → `_load_skills` 抛清晰 build error("skill X requires {tier} plan")。线程穿:`make_agent_builder` + `make_child_agent_builder` 构造 resolver + activity_recorder,`build_agent`/子 agent 传 `skill_resolver=` + `skill_activity_recorder=`(app.py 两处)。**这是 skill 运行时首次接线**(§0 发现 1),含 `SkillViewTool` 接入。子 agent 漏接会静默失败,必须同接。**`SkillViewTool` 的 `_SkillResolverShim` 必须做同款 tenant-first→platform-fallback 双查(R1)**,否则模型选了平台 skill 却 `skill_view` 不到(渐进式披露下平台 skill 默认 lazy,全文全靠 view)。

- **X-5 平台 skill CRUD API(system_admin)**。新路由 `api/platform_skills.py` `/v1/platform/skills`(镜像 `mcp_catalog.py` 网关:`require("skill"...)` 不适用——skill 无 RBAC resource,改**内联 `_require_system_admin`** + `bypass_rls_session()`),复用现有 `SkillStore`(`tenant_id=None`)+ **复用 moderation/high-risk gate**(`_skill_moderation.py`,平台 skill 同样过威胁扫描;high-risk 由 system_admin 审批激活)。端点:create / add-version / patch(status+pin)/ list / get / versions。`required_tier` 在 create/patch 可设。

- **X-6 租户合并视图 `GET /v1/skills`**。返回租户自建 + 平台 skill,每条带 `source: "tenant"|"platform"` + `entitled: bool`(平台 skill 按 `tier_satisfies`)。平台行经 `bypass_rls_session()` 读。**按 name 去重、租户同名遮蔽平台**(R2,与 resolver 一致)。现有 cross-tenant(`tenant_id=*`,system_admin)逻辑不动。manifest 绑定:`spec.skills` 不变(名字解析时租户优先、平台兜底,X-4);平台 skill 默认 `lazy_load=true`(R1 渐进式披露),不 entitled 的不进 agent 的 `<available_skills>` 摘要(R3)。

- **X-7 审计**:复用现有 skill `AuditAction`,平台操作在 `details` 标 `scope:"platform"`;`ResourceType` 已有 `"skill"`(无需双 Literal 新增)。平台 skill 写**绝不记 prompt 全文**(沿用现有 moderation 不落敏感内容惯例)。

- **X-8 Admin UI**:① 平台 skill 管理页(system_admin,镜像 `SettingsMcpCatalog` 网关 + `SkillsList`/`SkillDetail` 形态);② 租户 `SkillsList`/`SkillDetail` 加 **source 徽章(平台/自建)+ entitled 锁标**(premium 平台 skill 对低档租户显示"需 {tier}");复用 `api/skills.ts`,加平台 client。i18n en/zh-CN 同步。

- **X-9 范围边界**:ZIP export 不含 supporting_files(**既有 bug**,本 Stream 不修,记 follow-up)/ agent-authored skill(M1)/ per-tenant 私有平台库(列已 NULL,本迭代只平台全局)= out of scope。

## 1.5 跨项目调研结论(deer-flow / OpenClaw / Hermes —— 取精华去糟粕)

调研三个 agent 项目的 skill 加载机制,**三者一致收敛到"渐进式披露"**:system prompt 只注入 skill **name+description(+location)**,模型按描述自选,再用 read/`skill_view` 工具按需加载**全文**。这正是 helix 已有的原语(`lazy_load` 标志 + `skill_view` 工具,Stream U)。结论落到 X 的细化:

- **R1 渐进式披露是默认(精华,全员一致)**:**平台 skill 默认 `lazy_load=true`** —— 大库下只把 name+description 进 prompt(helix 的 `skill_summaries`),全文经 `skill_view` 按需取。**关键**:X-4 接线必须让 `skill_view` 运行时工具的 `_SkillResolverShim` **同样做 tenant-first→platform-fallback 双查**(不只 build 期的 `_load_skills`),否则模型选了平台 skill 却 view 不到。
- **R2 名字遮蔽,租户优先(Hermes "local>external,按名跳重"验证 X-4/X-6)**:resolve **和** 合并列表 **和** `skill_view` allowlist 三处都按 name 去重、**租户同名遮蔽平台**,语义一致。
- **R3 档位门控 = 资格过滤(对齐 OpenClaw OS/bin、Hermes requires_toolsets 的 eligibility 模式)**:租户**不 entitled 的平台 skill 不进 `<available_skills>` 摘要**(模型看不到也就选不了),列表里给"锁"标记。`required_models` 不匹配同理过滤(已有)。
- **R4 描述质量驱动选择(三者皆 model-driven,非规则)**:平台 skill 必须写强"何时用"描述 —— 选择全靠模型读 description。X-5 平台 skill 编辑器对 description 给"何时使用"引导。
- **R5 helix 已领先三者的点,务必保留**:① **版本化 + pin(`name@N`)** —— 三者全无版本/pin,helix 有,平台 skill 也能 pin;② **per-skill `tool_names` + 工具冲突检测** —— 比三者"skill 拿到 agent 全部工具"更细;③ **high-risk 门控 + moderation** —— 对应 Hermes 的 trust 分级,平台 skill 由 system_admin 审(最高信任档)但仍过威胁扫描(纵深防御)。
- **R6 follow-up(本 Stream 不做)**:平台 skill resolve 缓存(仿 deer-flow 后台缓存 / Hermes 两层缓存,现按需查 DB 即可,skill 量小);库变大后的语义/embedding 路由(deer-flow 指出 100+ skill 时纯描述选择会漏)。

## 2. 迁移与风险

- 迁移链:`0057_platform_skill`(down_revision=`0056_mcp_catalog_columns`,id=18 字符 ≤32)。downgrade 还原 RLS 严格相等 + 唯一约束 + 去列 + 改回 NOT NULL(需保证无 NULL 行才能改回——downgrade 前提注明)。
- **最高风险:在已有数据的 `skill`/`skill_version` 上换 RLS**。缓解:① 非 NULL 行行为不变(数学等价);② 强制 RLS 回归测(三态:租户隔离 / NULL 平台行对 scoped session 不可见 / bypass 可见);③ 迁移安全测(0056→0057 后既有租户 skill 仍可读、`required_tier='free'`)。
- **次风险:首次接线 skill resolver = 运行时行为变更**。缓解:① 既有无 `spec.skills` 的 manifest 行为不变(resolver 不被调用);② 有 skill 的 agent 现在才真正加载(本就是预期);③ 子 agent 同接防静默失败;④ resolver 错误(漏配/解析失败)翻译成清晰 build error,不 500。

## 3. PR 切分(每个 CI 全绿、零债)

- **X0 设计先行**(本 PR):`STREAM-X-DESIGN.md` + `ITERATION-PLAN.md` X backlog(已有,微调)。
- **X1 协议**:`Skill` record + ORM 加 `required_tier`;`SkillStore` 接口 `tenant_id` 放宽到 `UUID | None`(平台行)+ 平台 resolve 语义文档化。纯 schema 单测。
- **X2 持久化**:迁移 `0057`(nullable tenant_id + RLS swap + COALESCE 唯一索引 + required_tier)+ store NULL-tenant 支持 + curator `WHERE tenant_id IS NOT NULL` + **RLS 回归测 + 迁移安全测**。
- **X3 Resolver 接线 + 双查 + 门控**:`make_skill_resolver`(租户优先 + 平台 bypass 兜底 + `tier_satisfies`)+ 线程穿 `make_agent_builder`/`make_child_agent_builder`/`build_agent`/子 agent + `SkillViewTool` + activity recorder 接入。测:租户命中 / 平台兜底 / 档位拒(not_entitled)/ 子 agent / 无 skill manifest 零回归。
- **X4 平台 skill CRUD API + 租户合并视图**:`api/platform_skills.py`(system_admin + bypass_rls + moderation/high-risk 复用)+ `GET /v1/skills` 加 source/entitled。API 测(非 system_admin 403 / 平台 CRUD / 合并视图 entitled / high-risk 门控)。
- **X5 Admin UI**:平台 skill 管理页 + 租户库 source/entitled 徽章 + i18n;vitest + storybook + Playwright + axe。

**关键路径** X0→X1→X2→X3→X4→X5。**完成 = system_admin 发布平台精选 skill(可 premium 档位门控)→ 租户在 agent manifest 绑定(自建优先、平台兜底)→ agent 运行时真正加载使用**;同时补齐 Stream U 遗留的 skill 运行时接线。

## 4. Verification

1. **RLS(X-1,最高风险)**:scoped session(设 `app.tenant_id`)读不到 NULL 平台 skill;bypass 读得到;两租户直连互不可见对方 skill。
2. **迁移安全**:有租户 skill 旧行的库跑 0056→0057,旧行可读、`required_tier='free'`、隔离不变。
3. **Resolver E2E**:绑租户 skill → agent 加载其 prompt fragment/tools;绑平台 skill(entitled)→ 加载;free 租户绑 premium 平台 skill → build error not_entitled;同名时租户优先;子 agent 同样解析。
4. **Curator**:平台(NULL)skill 不被任何租户 sweep stale/archive。
5. **API**:非 system_admin 打 `/v1/platform/skills` → 403;租户 `GET /v1/skills` 见平台+自建(source/entitled 正确);high-risk 平台 skill 激活需 system_admin。
6. **通用**:`pre-commit`、`pytest -m "not integration"`、mypy 全 CI 范围、前端 typecheck/test/build/playwright;CodeQL 不放 Protocol `...`/assert 内副作用;migration id ≤32。

## 5. 复用锚点（exact files）

| 关注点 | 资产 | 文件 |
|---|---|---|
| NULL-tenant RLS 模板 | `0050`/`0055` | `migrations/versions/0050_encrypted_secret.py` |
| entitlement | `tier_satisfies`/`TenantPlan` | `protocol/entitlement.py`、`protocol/tenant_config.py:76` |
| skill 表/约束/RLS 现状 | `0029_skill` | `migrations/versions/0029_skill.py` |
| skill store + curator 方法 | `SkillStore` | `persistence/skill/{base,sql,memory}.py` |
| resolver + _load_skills | `SkillResolver` | `orchestrator/agent_factory.py:164,474` |
| 接线参照(provider key) | `make_provider_key_resolver` | `control_plane/runtime.py:149` |
| 平台 system_admin 网关 + bypass | `mcp_catalog.py`/`bypass_rls_session` | `api/mcp_catalog.py`、`tenant_scope.py:181` |
| moderation / high-risk | `_skill_moderation`/`HIGH_RISK_TOOLS` | `api/_skill_moderation.py`、`protocol/skill.py:48` |
| 租户 skill UI | `SkillsList`/`SkillDetail`/`api/skills.ts` | `apps/admin-ui/src/pages/Skill*.tsx` |
