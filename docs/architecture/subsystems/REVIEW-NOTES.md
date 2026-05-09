# 子系统设计文档 — 跨子系统对齐 Review 报告

> 17 篇子系统设计文档由不同 subagent 并行写出后的横向一致性审查。
> 4 个维度并行：A 接口/调用链 / B 数据模型 Schema / C 可观测性命名 / D 安全/多租户隔离。

---

## 总览

| 优先级 | 数量 | 阻塞 | 性质 |
|--------|------|------|------|
| **P0** | ~18 项 | M0 编码 | 编码会冲突、RLS 失效、跨租户漏洞、命名前缀机械错 |
| **P1** | ~19 项 | M1 前必修 | 接口语义未对齐、字段缺失、词表未补全 |
| **P2** | ~24 项 | 合规/优化 | 风格统一、metric 优化、文档可读性 |

> 项目数已含 [追加审查项 E1–E3](#-追加审查项用户提出)。

**修订工作量估算**：P0 ≈ 1.5 天 / P1 ≈ 1 天 / P2 ≈ 0.5 天 / 新建 `99-SHARED-TYPES.md` ≈ 0.5 天 — **总计约 3 天**。

---

## 🔴 P0 — M0 编码阻塞项（必须修）

### 接口/调用链类

#### A1. Quota 接口签名在 10 ↔ 16 不一致
- **位置**：`10-llm-gateway.md` § 5.3 / `16-quota-rate-limit.md` § 4.1 + § 3.3
- **冲突**：函数名（reserve/commit/cancel vs reserve_tokens/commit_tokens/release_tokens）、参数维度（`model` 入参在 10 有在 16 无）、`thread_id` 来源未定义、`actual_tokens` 计算口径（cache_read 折扣是否计入）
- **修订**：以 16 为准；10 § 5.3 改名；16 § 3.3 加可选 `model` 字段；明确 `actual_tokens = input + output`，cache_read/creation 单独走 metric

#### A2. M0 sandbox 出站走 Credential Proxy 工作模式（透明 vs 显式）矛盾
- **位置**：`11-credential-proxy.md` § 4.1（显式 `X-Helix-Upstream`）/ `21-network-policy.md` § 5.4（iptables REDIRECT 透明）
- **冲突**：M0 描述完全相反；21 § 5.5 验收用例与 11 § 5.2 互斥
- **修订**：M0 选**显式代理**；改 21 § 5.4 为"iptables 仅放行 credential-proxy.internal:443"；21 § 5.5 验收第 1 条改"必须 connection refused"；M1 升级 Envoy 后再切透明

#### A3. MCP Gateway 取 secret 时调 Credential Proxy 接口缺失
- **位置**：`12-mcp-gateway.md` § 5.4 / `11-credential-proxy.md` § 4
- **冲突**：12 假设可向 11 取 token，但 11 只暴露 `/forward`，无"读 secret"接口
- **修订**：选 (a) 12 也走 `/forward`（让 proxy 转发到 MCP server，secret 在 proxy 注入）—— 推荐

#### A4. sandbox 内业务代码调 `memory.put/search` 网络路径未定义
- **位置**：`13-memory-store.md` § 4.1 / § 5.2 / `21-network-policy.md` § 5.4
- **冲突**：13 SDK 描述为业务侧，但未说网络拓扑；21 强制 sandbox 出站只过 proxy
- **修订**：13 § 2/§ 4 显式说明"M0 `MemoryClient` 通过 sandbox supervisor unix domain socket 转发到 orchestrator，sandbox 内业务代码不直接接触 memory store"；21 allowlist 显式列该端点

### 数据模型类

#### B1. `event_log` 列名 `type` vs `event_type`
- **位置**：`23-postgres-scalability.md` § 3.2（DDL 用 `event_type`）/ `19-durable-execution.md` § 5.2（SQL 用 `type`）
- **修订**：以 23 为权威；19 § 5.2 SQL 全改 `event_type`

#### B2. `tenant` vs `tenant_id` 列名分裂
- **位置**：23 全用 `tenant_id`；其余 10/11/13/14/15/16/17/18/19/24/25 用 `tenant`
- **修订**：DB 列名统一 `tenant_id`；Pydantic 字段保留 `tenant: str`；改 6 张表（除 23 外的）

#### B3. `token_reservation.thread_id` 类型错误
- **位置**：`16-quota-rate-limit.md` § 3.1 写 `TEXT`，但 thread_id 全局是 `UUID`
- **修订**：改 `UUID`

#### B4. `audit_log` 索引在 17 与 23 重复定义且列名不同
- **位置**：`17` § 3.1 用 `tenant`、`23` § 3.3 用 `tenant_id`
- **修订**：以 17 为主；23 § 3.3 删除对 audit_log 索引重定义

### 可观测性类

#### C1. **批量重命名** — 7 篇 metric/span 缺 `helix_` / `helix.*` 前缀
- **位置**：`10/11/12/13/18/21/22` § 7（共约 50+ 个 metric）
- **修订**：批量加前缀 `helix_*`（snake_case）/ `helix.{component}.{action}`；可机械替换

#### C2. 23 `pgvector_search_latency_ms` 单位错误
- **位置**：`23-postgres-scalability.md` § 7
- **修订**：histogram 必须 `_seconds`，改 `helix_pgvector_search_latency_seconds`

### 安全/多租户类

#### D1. **RLS session 变量名三处不一致**（最危险，RLS 静默失效）
- **位置**：`13` 用 `app.current_tenant`、`15` § 4.3 用 `helix.tenant`、`23` § 8 用 `app.tenant_id`
- **修订**：统一为 `app.tenant_id`（与 `tenant_id` 列名匹配）；CI 加 lint 校验所有 RLS policy 引用一致

#### D2. `manifest_signature` 主键缺 tenant — 跨租户签名复用漏洞
- **位置**：`18-manifest-supply-chain.md` § 3.1
- **修订**：改主键为 `(tenant_id, name, version)` 三元组

#### D3. `event_log` replay 查询 SQL 缺 tenant filter — 越权 replay
- **位置**：`19-durable-execution.md` § 5.3
- **修订**：所有 `WHERE thread_id=$1` SQL 加 `AND tenant_id=$2`

#### D4. `22 DR restore/failover` 未写 audit — 高敏感操作无痕
- **位置**：`22-disaster-recovery.md` § 4.2/4.3
- **修订**：restore/failover/drill 强制写 17 audit_log；17 § 5.1 词表加 `dr:restore / dr:failover / dr:drill`

#### D5. `13 memory` 写入流程未显式列 redactor 节点
- **位置**：`13-memory-store.md` § 5.2（状态机：batch embed → batch insert，无 redact 节点）
- **修订**：状态机加 `[PII redact]` 在 `[batch embed]` 之前；校验 metadata 不含 pii_fields

#### E2. 上下文压缩机制无主定义，多 doc 引用不一致 ⚠️ 用户追加
- **位置**：被引用方散落 — `13-memory-store.md` L258/L432（history layer "session 结束触发"）、`19-durable-execution.md` L227（"messages > 200 触发，保留最近 50 + summary"）、`10-llm-gateway.md` L303（"context_length_exceeded 触发"）；所有引用方都假设 13 提供 summarization API，**但 13 doc 没有专门章节定义算法/接口/触发条件/失败处理**
- **冲突**：3 处触发条件互相矛盾（messages 数 vs token 数 vs session 结束）；无统一接口契约；DeerFlow 已有 `dynamic_context_middleware` + summarization 中间件可 vendor，但 doc 未说明落点
- **影响**：M0/M1 编码时无人知道在哪里实现 summarization；长会话必然在某节点把 LLM context 撑爆 → 400 error；不同子系统各自实现 → 重复 + 不一致
- **修订**：**新建 `27-context-compression.md` 子系统 doc**（推荐）作为横切机制（middleware 层），明确定义：
  1. 触发条件（multi-signal）：`tokens > 0.8 × model_context_limit` OR `messages > 200`（可配）OR `provider 返回 context_length_exceeded`
  2. 算法（分层）：保留 system_prompt（prefix cache 命中）+ 最近 K 轮原文 + 中间 N 轮摘要 + 关键工具结果（标记 pin=true 不压缩）
  3. 不可压缩项：当前 in-flight 的 `tool_call/tool_result` 对、最近一次 LLM 输出
  4. 实现位置：orchestrator 中间件链（vendor `dynamic_context_middleware` 193 行 + 自写 summarization）
  5. 摘要 LLM 选型：用更便宜的模型（如 Haiku）+ 独立 quota bucket
  6. prefix cache 协同：摘要点必须稳定（同一 session 多次压缩用同一前缀）→ 否则 cache 全 miss、成本爆 10×
  7. 失败模式：摘要 LLM 也失败 → 退化为简单截断（保留最近 K 轮）
  8. 观测：emit `helix_context_compression_total{trigger,outcome}` / `helix_context_size_tokens` 直方图
  - 同时修订 13/19/10 三处引用，统一指向 27 doc 的接口
- **优先级**：**P0**（M0 上线后第一周必踩 context 爆问题；架构债）

---

## 🟡 P1 — M1 前必修项

### 接口/调用链类

- **A5**：`subagent.spawn` 在 19 replay 协议中是否被视为"副作用工具"未定义 → 19 § 5.2 显式列 spawn 为副作用调用，server 端生成 idempotency_key
- **A6**：subagent token quota 在"子树预算"与"agent 维度独立计"语义冲突（24 vs 16 § 10）→ reservation 在 child 层，commit 时同步累加到 lead 的 token_budget_ledger；agent 维度 metric 按 child 独立
- **A7**：`25 HITL resume` API 自身的幂等性未明确 → 19 § 4.1 增加"resume 幂等：重复调用返回上次结果"

### 数据模型类

- **B5**：`manifest` 主表 DDL 在 18 § 3.1 缺失 → 增补 `manifest(id, tenant_id, name, version, body_yaml, body_hash, status, created_at)`
- **B6**：`actor_id` 类型在 17(TEXT) / 15(UUID) / 18(TEXT) 不一致 → 统一 `TEXT`（service_account / 'system' / `agent_name@version` 不能塞 UUID）
- **B7**：新建 `subsystems/99-SHARED-TYPES.md` 集中定义 `EventType / ActorType / ResourceType / Action / IsolationLevel`，子系统 doc 引用而非重定义

### 可观测性类

- **C3**：`helix_quota_reject_total` (16) vs `helix_quota_exceeded_total` (20) 同义重复 → 统一 `_exceeded_total`，但保留 16 的 `reason` label
- **C4**：`helix_audit_write_latency_seconds` (17) vs `helix_event_log_append_duration_seconds` (20) 措辞分歧 → 统一 `_duration_seconds`
- **C5**：`pg_connections_active` (23) vs `helix_pg_connection_pool_in_use` (20) → 统一为后者
- **C6**：补充 20 关键 metric 清单（吸纳 14 sandbox_pool_size、19 resume_total、21 egress_meta_attempt_total、22 backup_age_seconds、24 subagent_total/depth、25 hitl_pending_total、26 eval_gate_decision_total）

### 安全/多租户类

- **D6**：`22 BackupJob` S3/KMS 凭证来源未明确 → 显式从 11 取 secret_ref（或 IAM role + 文档说明）
- **D7**：MCP gateway 调用方拓扑澄清 → 调用方是 orchestrator 还是 sandbox 内？影响 21 allowlist
- **D8**：`17 audit_log` 词表补全 → 加 `session:resume / session:pause / session:force_resume / dr:* / eval:force_promote / subagent:spawn_denied`
- **D9**：`event_log` 写入 pipeline 强制经 redactor 的约束未在 13/12/19 共同声明 → 统一加约束
- **D10**：`26 EvalSet` 上传时同步跑 PII detector（不仅 CI）
- **D11**：`15 JWT signing key` 也作为 11 secret_ref 注册（M0 起）
- **D12**：admin `tenant='*'` 路径与 RLS 关系明确化（专用 reader role 绕过 RLS，避免代码层 if-else）
- **D13**：`26 eval` LLM 调用 tenant 透传给 10 LLM Gateway

### 用户追加项（E1 / E3）

#### E1. 模型降级两级语义未明确（同供应商内 → 跨供应商） ⚠️ 用户追加
- **位置**：`10-llm-gateway.md` § 5.2（fallback 策略）/ `02-AGENT-MANIFEST.md` L65/L421（ModelSpec.fallback chain）
- **现状**：已有 fallback 链 + 断路器 + 401/429 → fallback；ModelSpec.fallback `list[ModelSpec]` 机制上**支持**任意 provider 切换
- **缺口**：未明确区分两级语义
  1. **L1 同供应商内降级**：Claude Sonnet → Claude Haiku（同 key、同 region、同 prompt format、几乎零适配成本，秒级切换）
  2. **L2 跨供应商降级**：Anthropic 全挂 → OpenAI GPT-4o（不同 key、不同 prompt format、tool schema 需转换）
- **未定义**：
  - **provider 级断路器**（vs 当前的 model 级断路器）：当某 provider 整体故障率 > 阈值 → 跳过该 provider 的所有 model（即使在 chain 中前置）
  - **跨 provider 适配**：tool_use schema 转换（Anthropic vs OpenAI tool calling 格式）、system message 注入位置、stop reason 映射
  - **推荐 fallback 模板**：默认每个 manifest 自动注入 `[同 provider 降级 model, 跨 provider 等价 model]` 二级链
  - **降级感知**：fallback 成功后是否提示"已降级，输出质量可能下降"给业务侧
- **修订**：在 10 § 5.2 增加"两级降级语义"章节；在 § 5.3 增加 `ProviderCircuitBreaker`（provider 级，与 model 级独立）；在 02 manifest 加 fallback 推荐模板
- **优先级**：**P1**（M0 单 provider 不暴露；M1 多 provider 必须）

#### E3. 完整 lifecycle 追踪端到端设计缺失 ⚠️ 用户追加
- **位置**：`20-observability.md`（命名规范、metric、cardinality 都有）
- **现状**：span 命名 `helix.{component}.{action}` + trace_id W3C 传递 + 关键 attrs（tenant/agent/agent_version/session_id）
- **缺口**：未定义"从 session 创建到完成的完整生命周期 trace 设计"
  - **session root span**：`helix.session.run` 是根 span？所有 LLM/tool/sandbox 子 span 挂它下面？未明示
  - **subagent trace 关系**：`24-subagent-execution.md` § 7 选了"child 用新 trace + Link 关联 parent"，但 20 doc 没把这定为统一规则；其他子 trace（HITL pause、durable resume）该不该新 trace？
  - **长会话 trace 持久化**：HITL pause 数小时 → OTel span 默认 in-memory，会被 OTLP exporter flush 后丢；resume 时如何"接续"原 trace（同一 session 的 trace 应该可拼接）
  - **replay 时 trace 处理**：副作用工具命中 idempotency_key 不重 execute 时，是否 emit 新 span？trace 标记 `replayed=true`？
  - **session 异常终止标注**：crash / OOM / cancelled 时 root span 状态 + reason 必须明确
  - **业务侧查询能力**（核心需求）：
    - 按 `session_id` 拉完整 trace tree（所有 span + 父子关系 + 关键 attrs + log）
    - 按 `agent_name + version + 时间窗口` 列出最近 N 个 session 的 trace 概要
    - 按 `error class` 反查涉及的 session
  - **关键事件标注**：哪些 span 设 `helix.critical=true`（决策点、HITL 触发、降级触发、quota 拒绝）方便 dashboard 高亮
- **修订**：在 20 doc 增加新章节 `§ 5.X Agent 生命周期完整追踪`，明确：
  1. session root span 规范（必填 attrs、最长 7 天 TTL、跨 trace 拼接 ID `session_trace_group_id`）
  2. 子 trace 拆分规则（subagent 新 trace + Link；HITL/Durable resume 也用新 trace + Link，原因：长 pause 期间 OTel TTL 限制）
  3. 跨 trace 拼接：所有同 `session_id` 的 trace 自动通过 trace search 聚合
  4. 业务查询 API：`GET /sessions/{id}/trace-tree`（control plane 暴露，权限同 session 读权限）
  5. Tempo 索引：`session_id` / `agent_name` / `error.class` 必索引
  6. UI：admin web 提供 "session lifecycle 时间线视图"（瀑布图 + 关键事件标注）
- **优先级**：**P1**（M0 OTel 基础够用；M1 长会话 + subagent + HITL 上线后必须有完整查询能力）

---

## 🟢 P2 — 优化项

### 接口/调用链
- **A8**：eval 流量隔离机制（10/16/14 提前加可选 `purpose: Literal["production","eval"]` 字段）

### 数据模型
- **B8**：`agent` 列名规范化为 `agent_name`（避免与 manifest 中 `agent` 标量混淆）
- **B9**：LangGraph 自带表（`checkpoints / checkpoint_writes / checkpoint_blobs`）的备份/分区策略在 19/22/23 显式说明
- **B10**：02 `TenantConfig` 在引用方显式 import，避免散用字符串

### 可观测性
- **C7**：12 篇子系统 § 7 未引用 20 号 log 必填字段约定 → 加一句"完整字段遵循 [20 § 5.3]"
- **C8**：14/15/17/18/22/23 span attrs 列表缺 `agent_version`（15 `auth.login` early-bind 可豁免）
- **C9**：补 13 memory + 16 quota 调 redis 时 emit `helix_redis_command_duration_seconds`

### 安全
- **D14**：21 补一句"HITL 回调走 control plane 入站，不影响 21"
- **D15**：22 备份 worker 不属 sandbox 网络域，但 outbound S3 仍须 control plane egress allowlist
- **D16**：M1 加 outbound body redactor（与 21 § 9 M2 DLP 协同）
- **D17**：18 keyless 模式 Sigstore allowlist

---

## 修订执行计划（建议）

### Phase 1：批量机械修订（半天）
> 适合一个开发者集中处理；可逐一 commit

1. **C1 批量加前缀**：sed-style 替换 7 篇 doc § 7 的 metric/span（`s/^llm_gateway_/helix_llm_gateway_/` 等）
2. **B2 列名统一**：DDL 全 `tenant_id`；Pydantic 全 `tenant`
3. **B1 / B3 / B4**：单点修订
4. **C2** 单位修订

### Phase 2：决策性修订（1 天）
> 需要明确决策；建议主程过一遍后再改

1. **A1**：与 16 owner 对齐 quota API；改 10 + 16 双向
2. **A2 / A3 / A4**：澄清 M0 sandbox 网络拓扑（关键决策）
3. **D1 RLS 变量名**：选 `app.tenant_id` + 改 13/15/23
4. **D2 / D3 / D5**：安全相关，单点修

### Phase 3：新增/扩展（半天）
1. 新建 `99-SHARED-TYPES.md`（B7）
2. **B5** 补 manifest DDL
3. **A5 / A6 / A7** subagent + HITL idempotency 协议补全
4. **C3 / C4 / C5** metric 命名对齐
5. **D6–D13** 安全补全

### Phase 4：P2（半天，可推迟）

---

## 决策点（需要 owner 拍板）

| 决策点 | 选项 | 推荐 |
|--------|------|------|
| Quota API 命名 | 10 跟 16 / 16 跟 10 | 10 跟 16（quota 是被调方）|
| M0 出站代理模式 | 显式 vs 透明 | 显式（与 11 § 9 一致）|
| MCP gateway 取 secret 方式 | (a) 走 11 forward / (b) 加 admin secret API | (a) |
| memory client 网络拓扑 | sandbox 内直连 / orchestrator 转发 | orchestrator 转发 |
| RLS session 变量名 | `app.tenant_id` / `app.current_tenant` / `helix.tenant` | `app.tenant_id` |
| `actor_id` 类型 | TEXT / UUID | TEXT（兼容 service_account 字符串）|
| subagent quota 计费 | child 层 reservation + lead 累加 / lead 层独占 | child 层 + lead 累加（防爆+独立计）|
| **上下文压缩落点** | 新建 27-context-compression.md / 集中到 13 § 5 | **新建 27**（横切机制属 middleware 层，不是 memory store 内部细节）|
| **fallback chain 模板** | 不强制 / 引擎自动注入 L1+L2 默认链 | **引擎自动注入默认链**（manifest 可 override；保证最低韧性）|
| **subagent / HITL 子 trace 关系** | 同 trace 续 / 新 trace + Link | **新 trace + Link**（避免长 pause 期 OTel TTL 失效；与 24 doc 一致）|

---

## 附：4 份 review 报告原文（如需详查）

汇总报告基于以下 4 个并行 review 的结论合并去重：
- 维度 A 接口/调用链：8 个问题（5 P0 + 3 P1，外加 1 P2）
- 维度 B 数据模型：4 P0 + 3 P1 + 3 P2
- 维度 C 可观测性：批量前缀 + 3 P1 同义重复 + 12 P2 字段
- 维度 D 安全：6 P0 + 8 P1 + 4 P2

各维度原始报告可在 git log 找到（subagent 输出，未单独存档）。本汇总文档是后续修订的 single source of truth。

---

## 📌 追加审查项（用户提出）

> 用户在 review 完成后追加的 3 个检查点；逐项 verify 现有 docs 覆盖度后纳入修订清单。

| ID | 主题 | 现状 | 缺口 | 优先级 | 详情位置 |
|----|------|------|------|--------|---------|
| **E1** | 模型降级（同供应商→跨供应商） | 10 § 5.2 已有 fallback 框架；ModelSpec.fallback 机制上支持 | 未明确 L1/L2 两级语义；无 provider 级断路器；无 prompt/tool schema 跨厂商适配；无 fallback 推荐模板 | **P1** | 见 [P1 用户追加项 E1](#e1-模型降级两级语义未明确同供应商内--跨供应商-️-用户追加) |
| **E2** | Agent 上下文压缩 | 13/19/10 多处引用 summarization；DeerFlow 有可 vendor 中间件；ROADMAP M1 提及 | **13 doc 无主定义**；3 处触发条件互相矛盾；无统一接口；与 prefix cache 协同未设计 | **P0** | 见 [P0 用户追加项 E2](#e2-上下文压缩机制无主定义多-doc-引用不一致-️-用户追加) |
| **E3** | 完整 Agent lifecycle 追踪 | 20 doc 有命名规范、cardinality 控制、关键 attrs | session root span 规范缺；subagent/HITL 子 trace 关系不统一；长会话 trace 持久化无方案；业务侧查询 API 缺 | **P1** | 见 [P1 用户追加项 E3](#e3-完整-lifecycle-追踪端到端设计缺失-️-用户追加) |

**E2 关键洞察**：上下文压缩在 6 处 doc 里"假设它存在"但**没人定义**，这种"幽灵依赖"在 M0 上线后第一周就会暴露（长会话必然爆 context window）。强烈建议作为新建子系统 doc `27-context-compression.md` 处理。

**E1 / E3 共性**：都是"现有设计走得对，但深度不够"——E1 fallback 框架有但只覆盖 model 级；E3 OTel 选型对但 lifecycle 端到端串联缺。属于扩展而非重构。
