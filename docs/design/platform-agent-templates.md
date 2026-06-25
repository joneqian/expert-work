# 平台 Agent 模板 + 租户继承(分层 manifest)

## 1. 背景与动机

平台已对 **MCP**(平台目录 + 租户启用,#785-797)和 **技能**(市场,Stream X)落地「平台策展 + 租户使用」治理。本设计把同一治理轴**延伸到 Agent**:平台维护**官方 agent 模板**,租户实例化为自己的 per-user agent。

owner 的核心诉求(2 项,方向相反,必须分层才能同时满足):
- **底层能力的 bug 修复 → 租户自动生效**(平台统一维护,租户零运维)。
- **prompt 等 → 租户 fork 后自己改**(业务定制,不被平台覆盖)。

这两个诉求落在**同一个 agent 的不同字段**上,所以方案不是「fork 整份」(平台改不动)也不是「纯共享」(租户改不了),而是**按字段分层继承**:不同层不同所有者,无合并冲突。

## 2. 已有的基础(building blocks)

- **`AgentSpecBody.extends: str | None`** —— manifest 里**已声明但全仓无消费**的继承字段(休眠占位)。本设计激活它。
- **AgentSpec 已版本化**:`AgentSpecRecord{tenant_id, name, version, spec, status, revisions}`,按 `(name, version)` 唯一,有 revision 历史 + publish 流。
- **NULL-tenant 平台行范式**:`mcp_connector_catalog`(RLS,bypass_rls 读)已验证可行,平台 agent 模板照搬。
- **缓存失效机制**:catalog 改动 → `invalidate_all` agent 缓存 → 下次构建重读(MCP 平台池刚用过)。propagation 直接复用。
- **tier② 的来源原语已就绪**:MCP 租户 enable + 技能订阅 —— 模板声明「用某类能力」,租户的 enable/订阅决定具体生效集。

## 3. 三层模型(owner 已拍板)

| 层 | 语义 | 字段(映射到真实 `AgentSpecBody`) | 所有者 |
|---|---|---|---|
| **① 安全强制层** | 平台强制 floor,租户**只能加严,不能删/放松** | `defenses`(prompt_injection / output_screen / output_judge / action_screen / output_dlp)+ `policies.safety` / `policies.pii` | 平台 |
| **② 能力层(增删 delta)** | 平台给默认,**租户可加可删** | `tools` / `skills` / `subagents` / `dynamic_workers` / `workflow` / `reflection` / `routing` | 平台默认 + 租户 delta |
| **③ 租户自有层(整份替换)** | 租户拥有,平台不碰 | `system_prompt`(整份替换)/ `model` / `memory` / `knowledge`(RAG)/ `vision` / `cache` / `description` / `triggers` / `tenant_config` | 租户 |

**owner 锁定的细节决策**:
1. 三层划分认可;**①安全层强制 floor,租户不可覆盖**。
2. **prompt 整份替换**(降难度;放弃 prompt 级自动修复——平台改基座 prompt 不流给已 override 的租户)。
3. **模型档 + 参数租户可改,暂不设限**(后续可加平台上限/白名单,非本期)。
4. **知识库 RAG 归租户③**(租户自己的数据)。

## 4. 数据模型

### 4.1 平台 agent 模板
**新表 `platform_agent_template`**(NULL-tenant,镜像 `mcp_connector_catalog`),或复用 `agent_spec` 表加 NULL-tenant 行 + `is_platform_template` 标志。**倾向新表**(agent_spec 的 RLS/owner/revision 语义按租户设计,平台模板治理语义不同,混表风险高;参见 [[reference_skill_curator_owner_rls_exemption]] FORCE-RLS 坑)。

字段:`{id, name, version, spec: AgentSpec(完整基座 manifest), required_tier, enabled, status(draft/published), created_by, timestamps, revisions}`。`spec` 是一份完整 AgentSpec(三层都填,作为基座默认)。

### 4.2 租户实例(继承)
租户 per-user agent 的 manifest **不存完整副本**,只存:
- `extends: "<template-name>@<version-policy>"` —— `@latest`(跟随,自动修复)或 `@1.2.0`(钉死,逃生口)。
- **tier③ 覆盖**:租户填了就整份替换基座对应字段;没填用基座。
- **tier② delta**:`{added: [...], removed: [name,...]}` —— 生效集 = `(基座 − removed) ∪ added`。
- **tier① 不存**:始终取基座(强制),租户只能**叠加更严的**(merge 取「更严」side,见 §5)。

## 5. 解析(构建期合并)

在**物化 effective AgentSpec**(build 前)注入 `resolve_extends`:
1. `extends` 非空 → bypass_rls 拉平台模板基座 spec(按 version-policy 选版本)。
2. 三层合并:
   - **① defenses**:`max-strict(base, tenant)` —— 每个开关取更严档(如 base=`off` 租户=`block` → `block`;base=`block` 租户=`off` → **`block`**,租户放松被忽略)。租户删不掉。
   - **② tools/skills/...**:`(base − removed) ∪ added`,按 name/key 去重。
   - **③ system_prompt/model/...**:租户字段非空 → **整份替换**;否则用基座。
3. 合并产物是一份普通 AgentSpec → 走现有 `build_agent`,**下游零改动**。

**propagation**:租户实例只存 ref + delta + override,不存基座副本 → 每次 build 重解析 → 平台基座改 tier① / tier②-未删项 / tier③-未 override 项 → **自动流入**(`@latest`)。复用 catalog-change → `invalidate_all` 失效链。

## 6. 反方风险 / 缓解

- **爆炸半径**:平台基座一个坏 fix 经 `@latest` 传给所有继承者。M1 靠**惰性重建 + 钉版逃生口**(租户可 pin),不做裸推。**灰度发布**(模板 `published` 前过平台 staging 验证)留 **M3**。
- **安全 floor 绕过**:tier① 合并必须是「取更严」单向,**严禁**租户值覆盖基座放松 —— 这是「平台修复对安全自动生效」的命门(呼应 [[feedback_audit_over_blocking]] 必要安全保留)。
- **prompt 级修复不传播**:owner 接受(决策 2,整份替换)。代价是平台改不了已 fork 租户的 prompt bug —— 记录在案。
- **字段分层漂移**:三层字段划分要在 protocol 层固化为单一事实源(一个 `TEMPLATE_FIELD_TIERS` 表),避免散落多处(参见 [[project_audit_literal_drift]] 双份漂移坑)。

## 7. 租户实例化 UX

镜像 MCP catalog browser:平台模板「市场」→ 租户选模板 → 实例化为自己的 agent(默认 `@latest` + 空 override)→ 在 agent 详情页编辑 tier③ override + tier② delta(tier① 只读展示「平台强制」)。复用刚建的 `CatalogConfigForm`/详情页范式。

## 8. 分期

- **M1 模板 + 实例化 + 钉版**:平台模板表 + system_admin CRUD(版本化)+ `resolve_extends` 三层合并 + 租户实例化(默认钉基座当前版,**先不做 auto-propagate**,先拿「模板、方便」)+ 前端模板市场 + 实例化。
- **M2 跟随 latest + 自动修复**:`@latest` 策略 + 基座改动失效链 → 拿「修 bug 自动生效」,钉版逃生口兜底。
- **M3 灰度 + 安全**:模板 publish 灰度(staging 验证 / 分批)+ tier① 合并的全链 live 验。

## 9. 待确认(实现期细化)

1. **tier② delta 的 MCP/技能** 与现有「租户 enable / 订阅」如何收口?(模板声明 vs 租户 enable 谁说了算 —— 倾向:模板声明 = 默认 enable 集,租户 delta 再增删,二者合并)。
2. **新表 vs 复用 agent_spec**:倾向新表,实现期 final。
3. **tier①「取更严」** 对非布尔字段(如 `approval_required_tools` 列表)语义 = 并集(更严);实现期逐字段定。

> 范式:本设计为 **design-only PR**(零代码),镜像 MCP 平台 server 的 #785。评审/合入后再开 M1。
