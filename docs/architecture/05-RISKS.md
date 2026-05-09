# 05 风险与替代方案

## 主要风险

### 0. 多业务线引擎的合规可插拔（关键设计风险）

**评估**：严重性 4/5，概率 5/5（多业务线必然遇到）

Helix 服务多业务线（医疗 + HR + 客服 + 研发等），不同业务的合规要求差异巨大：医疗租户要 HIPAA、HR 要 GDPR、金融要 SOX、研发工具几乎没有合规要求。把合规硬编码成"全引擎默认开启"会让非合规租户付不必要的性能/成本代价；硬编码"默认不开"又会让合规租户漏出 PII。

**应对方案**：
1. **引擎本体业务无关**——不内嵌任何业务领域 prompt/tool/guardrail
2. **`tenant_config.compliance_pack`**：每个 manifest 声明合规级别（`hipaa | gdpr | sox | null`）
3. **引擎根据 pack 自动注入**：
   - PII redact 中间件（pii_fields 配置驱动）
   - 加密策略（强加密 vs 标准加密）
   - 审计保留期（HIPAA 7 年 vs 默认 90 天）
   - 强制 `dedicated_sandbox`（HIPAA）vs 允许 `shared`
   - 数据驻留检查（数据不出 region）
4. **`templates/medical/`** 领域模板包提供医疗场景预设，业务侧 `extends` 引用即可
5. **M0/M1 不做合规认证**（HIPAA/SOC2 正式审计推到 M2），但**M0 提供合规所需的全部前置基础设施**（审计日志、加密、访问控制、数据保留）——这些对所有租户都默认开启

**逃生通道**：如果某租户合规要求超出 pack 覆盖范围，可通过 Python 插槽包自定义中间件（pre_llm hook 做特殊脱敏、post_tool hook 做合规审计写入）。

---

## 主要风险

### 1. LangGraph 锁定风险

**评估**：低偏中
- MIT 协议、不依赖 LangChain、API 稳定
- 24.8k Stars、34.5M 月下载、生态最成熟

**逃生通道（架构层面已隔离）**：
- 所有 LangGraph 接触限制在 `services/orchestrator/graph_builder/` 一处
- `AgentSpec` 是引擎自有 schema，**不暴露 LangGraph 类型给上层**
- 抽象出 `IGraphRuntime` 接口，未来可换 Mastra（TS）/Eino（Go）/自研

**迁移成本估算**：换底层引擎 ≈ 重写 graph_builder + checkpointer 适配，约 2-3 周（不含 sub-agent 复杂场景）

---

### 2. gVisor 性能开销

**评估**：syscall-heavy 场景 1.5-3x 慢；纯 LLM 任务（IO-bound）<10%

**应对**：
- 默认 gvisor，YAML 可声明 `sandbox.runtime: docker`（受信工具，如内部 batch）
- 性能敏感的工具走 MCP（在 host 进程执行）而非 sandbox
- 监控 `runsc` 性能 KPI，必要时部分租户升级到 Firecracker

---

### 3. Docker 单机伸缩瓶颈

**评估**：单机 100-200 sandbox 后 docker daemon 成瓶颈

**应对**：
- 水位 70% 时告警，提前扩容到第二台机器（用 docker swarm 临时）
- M3 平滑迁 K8s（Sandbox Pool 接口已抽象，换实现不动业务）

---

### 🆕 4. Prefix Cache 不命中导致 API 成本爆炸

**评估**：严重性 5/5，概率 5/5（如果不做对）

如果 system_prompt 直接嵌入 session/turn 级动态内容（patient context、当前日期、memory），prompt 前缀每次都不同 → Anthropic prompt caching 永远不命中 → 长会话 API 成本约 **10x**。

**应对**：必须 vendor `dynamic_context_middleware`（详见 [research/05-deerflow-deeper-scan.md](../research/05-deerflow-deeper-scan.md)），把动态内容拆成独立 `<system-reminder>` HumanMessage 注入，保持 system_prompt 永远静态。Manifest schema 已加 `dynamic_context` 字段强制这套约束。

---

### 🆕 5. LLM Provider 抖动级联故障

**评估**：严重性 5/5，概率 4/5

Anthropic / OpenAI 频繁出现瞬时 429/503，单租户的盲目重试会拖垮整个服务。原方案只提了 `model.fallback` 但没有断路器机制。

**应对**：vendor `llm_error_handling_middleware`（368 行），含按 provider+key 维度的断路器、指数退避、错误分类（瞬时/配额/认证）、多语言 busy pattern 识别。

---

### 🆕 6. LLM 生成危险命令在 sandbox 边界外执行

**评估**：严重性 4/5，概率 3/5

gVisor 防容器逃逸，但不能阻止 LLM 决定执行 `rm -rf /workspace/*`（虽然在沙箱内，但会清掉同租户工作目录）。

**应对**：vendor `sandbox_audit_middleware`（363 行），15 条高风险规则 + 5 条中风险规则在 sandbox 之前的工具调用层拦截。这是与 gVisor 正交的逻辑层防护。

---

### 7. DeerFlow vendor 维护成本

**评估**：低
- 我们 vendor 的是 deer-flow 的**基础设施层**（event log/persistence/factories），不是它的特化 middleware
- 这些模块在 deer-flow 中也属于稳定层，变更频率低
- 每文件标记 commit_sha + 修改记录，季度同步上游 bug fix

**应对**：
- 不引入 `deerflow-harness` PyPI 包依赖（会拉进 14 中间件 + 应用层）
- vendor 文件全部加注释 `# Adapted from bytedance/deer-flow @ <sha> — MIT`
- CI 设置 vendor 完整性检查（hash sum）

---

### 5. LangGraph 不合适的备选

| 备选 | 语言 | 成熟度 | 迁移成本 | 何时选 |
|------|------|--------|---------|--------|
| **Mastra** | TS | 中 | 大（语言切换）| 团队是 TS 主力 |
| **Eino**（字节）| Go | 中 | 大 | 极致性能、运维 Go 栈 |
| **自研轻量 Graph** | Python | — | 中（~3 周）| LangGraph 不可控 / 有重大 BUG 时 |

---

## 其他风险速记

| 风险 | 缓解 |
|------|------|
| 模型 provider 抖动 | 多 provider fallback（YAML 声明）+ 本地缓存 |
| Vault 单点 | HA 集群 + 缓存（短 TTL） |
| Postgres 写入热点 | event_log 按 tenant 分区 + 读写分离 |
| Manifest 误改导致全租户故障 | 每次发布需 2 人 review + 自动 eval gate + cosign 签名 |
| MCP server 第三方失控 | 全部出站走 Credential Proxy + tool 白名单 + 超时 |
| 团队 LangGraph 不熟 | 内部培训 + Helix SDK 屏蔽 LangGraph 概念 |
| sandbox 镜像供应链攻击 | 镜像签名 + Trivy 扫描 + 私有 registry |
| event_log 表无限增长 | 按 tenant 分区 + S3 冷归档 + 12 个月窗口 |

---

## 替代方案矩阵（如果当前方案不可行）

| 当前方案组件 | 备选 1 | 备选 2 |
|--------------|--------|--------|
| LangGraph | Eino (Go) | 自研轻量 Graph (Python) |
| gVisor | Kata Containers | Firecracker microVM |
| Docker 单机 | Kubernetes 起步 | Nomad |
| Postgres + pgvector | Postgres + Qdrant | Pinecone（云）|
| Vault | AWS Secrets Manager | HashiCorp Vault Cloud |
| Envoy 凭证代理 | mitmproxy | 自研 aiohttp |
| Anthropic MCP SDK | OpenAI Function Calling 适配器 | 自研协议层 |
| FastAPI | NestJS（如果换 TS）| Fastify |
