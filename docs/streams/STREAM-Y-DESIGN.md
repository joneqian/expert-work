# Stream Y — LLM 平台独占 + Rate Card + 计量加价

> 平台中心化治理路线图的**变现层地基**(治理层 W/X 已完成)。锁死 LLM 平台独占,
> 在 G.9 `token_usage` 上建 rate card + 成本派生 + 按租户分月 billing ledger。
> 不碰 C.5 quota 热路径(零风险)。设计先行见 [[feedback_design_first_iteration]]。

## 0. 背景 / 缺口(已 file:line 核实,2026-06-04)

| 子系统 | 现状 | 处置 |
|---|---|---|
| **主对话 LLM key** | `agent_factory.py:813-826`:`manifest entry.api_key_ref` **优先**,否则平台 `provider_key_resolver`(Stream Q) | Y1 砍 `api_key_ref` 分支 → 100% 走平台 resolver |
| **credentials_mode** | `Literal["platform","tenant"]`,默认 `platform`(`tenant_config.py:56-66,177`);resolver 有 tenant/platform 双分支 | Y1 收窄为 `Literal["platform"]`(保留符号),switch gate 硬拒 `tenant` |
| **BYOK 字段** | `model_credentials_ref`/`tool_credentials` 存在但 platform 模式下 resolve 时忽略 | Y1 标 deprecated、保留为 dormant(**不做破坏性删列**) |
| **MCP per-server creds** | `mcp_credentials`(Stream V/W,租户用自己凭证实例化平台目录) | **不动**——这是 W 的预期形态,非 BYOK |
| **真计量源** | `token_usage` 表 **已激活**(`TokenUsageMiddleware` @ `after_llm_call`,`middleware_assembly.py:124-127`),记 `tenant_id/agent_name/agent_version/model/input/output/cache_creation/cache_read/trace_id/observed_at` | Y3 在其上派生 rollup,**不碰中间件** |
| **C.5 quota 引擎** | `reserve/commit/release` 仅 HTTP `api/quota.py` 调,orchestrator run loop **零调用**;`CommitRequest.actual_tokens` 单 int 无拆分 | **保持不动**作参照 |
| **rate_card / billing** | **完全 greenfield**(全仓 grep 零命中);`token_budget_ledger`(C.5)是配额账,语义不同**不复用** | Y2/Y3 新建 |
| **billing RBAC 资源** | `rbac.py:30-45` Resource Literal **无** `billing` | Y2 新增(注意 protocol+control-plane 双 Literal 漂移,见 [[project_audit_literal_drift]]) |
| **model catalog** | `MODEL_CATALOG: dict[Provider, tuple[ModelEntry]]`,有 `deprecated` flag(`model_catalog.py:23-128`) | rate_card `(provider,model)` 据此校验(含 deprecated,供历史用量定价) |

**迁移头** `0057_platform_skill` → Y1 用 `0058`、Y2 `0059`、Y3 `0060`(id ≤32 字符,见 [[feedback_alembic_revision_id_32_chars]])。

## 0.1 已锁业务决策(2026-06-04,AskUserQuestion)

1. **加价粒度 = 按模型行 + 档位覆盖**:`model_rate_card` 每行 `(provider,model)` 带 `markup_bps`,可选 `plan_tier` 覆盖(NULL=通用);最具体行优先 `(provider,model,plan_tier)` → `(provider,model,NULL)`。贵模型可高加价、企业档可优惠。
2. **成本透明度 = 租户只看最终计费价**:租户 API 只暴露 `billed_cost`(含加价)+ token 用量;`base_cost`/`markup` 拆分**仅内部存储**供 system_admin 分账(Stream Z chargeback)。

## 1. Mini-ADRs

### Y-1 治理:LLM 平台独占(移除租户 BYOK)
- `credentials/resolver.py` `resolve_provider`/`resolve_tool` **删 `"tenant"` 分支**,只留 platform(保留 `CredentialsResolverError` 给平台漏配)。
- `protocol/tenant_config.py`:`CredentialsMode` 收窄为 `Literal["platform"]`(**保留符号**避免 import churn);PATCH validator 拒 `"tenant"`(403 `CREDENTIALS_MODE_TENANT_DEPRECATED`);`model_credentials_ref`/`tool_credentials` 保留 dormant,docstring 标 deprecated。`api/tenant_config.py:192` 的 switch gate 改硬拒。
- **防御性迁移 `0058`**:把任何 `credentials_mode='tenant'` 翻成 `'platform'` 并 log `tenant_id`(只读 preflight 脚本列出受影响 provider)。
- **前置依赖**:平台 provider 凭证必须可配(Stream Q 加密存 + Credentials 面板已有 merged view `api/tenant_config.py:65-88`)。Y1 落地前确认 system_admin 能写平台 provider key(若 UI 缺口,Y1 companion 补;不阻断 resolver 路径)。

### Y-2 移除 manifest `api_key_ref` override(计量完整性 > per-manifest key 钉选)
- `agent_factory.py:813-826`:**移除 `entry.api_key_ref` 分支** → 100% 主 key 走 `provider_key_resolver`(平台),杜绝 spend 绕过计量。
- **过渡**:旧 manifest 带 `api_key_ref` = **ignore + warn**(log 一条 warning,落到平台 resolver),不硬失败;一个 release 后删字段。
- 关联反转 [[reference_agent_llm_key_resolution]](主对话 key 旧路径)/[[project_web_paste_key_direction]](平台 key 仍 web 粘贴,**仅租户 BYOK 移除**)。

### Y-3 Rate Card(新平台表 `model_rate_card`,迁移 `0059`)
- `tenant_id NULL` + bypass_rls 写(照 `0050`/`0055` NULL-tenant 模式,见 [[reference_skill_curator_owner_rls_exemption]] 反面=本表无跨租户 sweep,正常 NULL-tenant RLS 即可)。
- 字段:`provider`/`model`(校验 `MODEL_CATALOG` 含 deprecated)、`input_token_micros`/`output_token_micros`/`cache_creation_token_micros`/`cache_read_token_micros`(**整数 micro-USD,杜绝 float**)、`markup_bps`(基点,2000=+20%)、`plan_tier`(NULL=通用)、`effective_from`/`effective_until`(**时序版本必须有——重定价不回溯改上月账单**)。
- 最具体优先:`(provider,model,plan_tier)` → `(provider,model,NULL)`;时间维度取 row `observed_at` 当时 `effective_from ≤ t < effective_until` 的行。
- 协议 `protocol/billing.py`;store `persistence/billing/rate_card.py`(双实现镜像 `token_usage_store.py`);admin API `api/rate_card.py`(system_admin + bypass_rls);RBAC 加 `billing` 资源(租户角色仅 `read`)。

### Y-4 成本派生 + `tenant_billing_ledger`(新表,迁移 `0060`;rollup job)
- 新表(**不扩展 `token_budget_ledger`**):`tenant_id`(RLS)、`month`、`provider`/`model`/`agent_name`(分账维度)、各 token 求和、`base_cost_micros`/`markup_micros`/`billed_cost_micros`(拆分**内部存**=透明度决策2)、`rate_card_priced_at`(审计)。唯一键 `(tenant_id,month,provider,model,agent_name)`。
- **rollup job**(仿 `services/retention-cleanup-job`):读 `token_usage` 窗口 → 按 `(provider,model,agent_name)` 分组 → 用 row `observed_at` 当时生效 rate 定价 → **upsert**(`on_conflict_do_update`)。**纯派生=天然幂等**,重跑安全,改 rate 可确定性重算。当月每小时跑,月结定版一次。
- **provider 派生问题**(已核实:`token_usage` 只记 `model` 不记 `provider`):rollup 需 model→provider。方案:① Y3 给 `token_usage` 加 `provider` 列(**additive nullable**,新行由中间件填,旧行迁移期按 `MODEL_CATALOG` 反查回填);② rollup 对旧行用 `MODEL_CATALOG` 反向索引(model→provider);**同名跨 provider 歧义**则该行标 `unpriced` 并 log(不静默丢)。推荐 ①+②(新行精确、旧行尽力)。

### Y-5 透明度切分(Stream Z 出口,Y4 ledger 已存拆分)
- 租户 cost API 只暴露 `billed_cost_micros` + token 用量;`base`/`markup` 仅 system_admin chargeback 可见。Y4 ledger 存全三档,Z 控制暴露面。

## 2. 迁移与风险

- **最高风险:Y2 移除 `api_key_ref` = 主对话 key 路径变更**。缓解:① ignore+warn 不硬失败;② 防御迁移翻 `tenant`→`platform`;③ 平台漏配 → 清晰 `AgentFactoryError`(非 500);④ 前置确认平台 provider key 可写。
- **次风险:provider 派生歧义**。缓解:加 `token_usage.provider` 列(新行精确),旧行反查 + `unpriced` 标记,不静默错价。
- **零热路径**:rate_card/ledger/rollup 全离线派生,`token_usage` 中间件与 C.5 quota 全不动。原 quota 测须全绿。
- 双 Literal:`billing` RBAC 资源 + 任何 `AuditAction`/`ResourceType` 新值改 protocol+control-plane 两处(见 [[project_audit_literal_drift]])。

## 3. PR 切分(每个 CI 全绿、零债,见 [[feedback_zero_tech_debt]])

- **Y0 设计先行**(本 PR):`STREAM-Y-DESIGN.md` + `ITERATION-PLAN.md` Y backlog。
- **Y1 平台独占锁**:resolver 删 tenant 分支 + `CredentialsMode` 收窄 + switch gate 硬拒 + 防御迁移 `0058` + preflight 脚本。测:patch `tenant`→403;迁移翻库;平台漏配→`CredentialsResolverError`。
- **Y2 移除 manifest api_key_ref**:`agent_factory` 删分支 + ignore/warn 过渡。测:带 `api_key_ref` 旧 manifest warn+走平台 key;漏配清晰报错;无 `api_key_ref` 行为不变。
- **Y3 Rate Card**:协议 `billing.py` + store + admin API + `billing` RBAC + 迁移 `0059` + `token_usage.provider` 列(additive)。测:`(provider,model)` 不在 catalog→422;时序行生效;最具体行优先。
- **Y4 成本 rollup + ledger**:`tenant_billing_ledger` + 迁移 `0060` + rollup job。测:跑 agent 产 `token_usage` → rollup → ledger `billed=base*(1+markup_bps/1e4)`;重跑幂等(行不翻倍);改 rate 重算;provider 歧义→`unpriced`。

**关键路径** Y0→Y1→Y2→Y3→Y4。Y1/Y2 是治理收口,Y3/Y4 是计费地基(为 Stream Z chargeback 出口铺路)。

## 4. Verification

1. **平台独占**:patch `credentials_mode="tenant"`→403;带 `api_key_ref` 旧 manifest warn+ignore、key 走平台 resolver;两者皆漏配→`CredentialsResolverError`/`AgentFactoryError`。
2. **rate card**:写 `(provider,model)` 不在 `MODEL_CATALOG`→422;`effective_from/until` 时序生效;`(provider,model,plan_tier)` 覆盖 `(provider,model,NULL)`。
3. **成本派生**:agent 产 `token_usage` 行 → rollup job → `tenant_billing_ledger` 有 `billed_cost_micros=base*(1+markup_bps/1e4)` 行;重跑 job 幂等;改 rate 重跑可重定价;provider 歧义行标 `unpriced` + log。
4. **零回归**:C.5/quota.check 路径零改动(原 quota 测全绿);`token_usage` 中间件不变。
5. **透明度**:租户 cost API 不返 `base`/`markup`(仅 `billed` + tokens);system_admin chargeback 返全拆分。
6. **通用**:`pre-commit`、`pytest -m "not integration"`、mypy 全 CI 范围(见 [[reference_ci_lint_test_scopes]] /[[reference_ci_lint_type_test_scopes]]);CodeQL 不放 Protocol `...`/assert 内副作用/不 log secret-命名值;migration id ≤32;micro-USD 整数杜绝 float。

## 5. 复用锚点(exact files）

| 关注点 | 资产 | 文件 |
|---|---|---|
| 真计量源 | `token_usage` 表 + 中间件 | `persistence/models/token_usage.py`、`runtime/middleware/token_usage.py`、`persistence/token_usage_store.py` |
| 主 key 解析(Y2 改) | `agent_factory` | `orchestrator/agent_factory.py:813-826` |
| 平台 resolver(Y1 改) | `resolve_provider`/`make_provider_key_resolver` | `credentials/resolver.py`、`control_plane/runtime.py:154` |
| credentials_mode(Y1 改) | `CredentialsMode` + switch gate | `protocol/tenant_config.py:56-66,177`、`api/tenant_config.py:192` |
| NULL-tenant RLS 模板 | `0050`/`0055` | `migrations/versions/0050_encrypted_secret.py` |
| model 校验 | `MODEL_CATALOG`/`models_for_provider` | `protocol/model_catalog.py:23-128` |
| rollup job 范式 | 离线 job | `services/retention-cleanup-job/` |
| upsert 范式 | `on_conflict_do_update` | `quota/sql.py`(C.5,**只读参照不改**) |
| RBAC 资源 | `Resource` Literal | `auth/rbac.py:30-45` + 协议侧 |

## 6. Out of scope / follow-ups

- Stream Z(用量/成本看板 API + chargeback)——Y4 ledger 就绪后才有可展示成本。
- Invoices / 支付集成(M2;发票=ledger 冻结快照)。
- 平台级 named-key 池(替代 manifest `api_key_ref` 的高频 key 钉选场景)。
- KEK→真 KMS wrap(等 aliyun_kms 落地)。
