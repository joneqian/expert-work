# 04 分阶段实施路线 + 验证方案

## 路线总览

| 阶段 | 时长 | 人力 | 关键交付 | 验证手段 |
|------|------|------|----------|----------|
| **M0 — MVP** | 4-6 周 | 2-3 人 | 1 个内部业务 Agent 跑通 | 与 Dify 平行运行对比 |
| **M1 — 生产化** | 6-8 周 | 3 人 | 多租户 + Python 插槽 + 灰度 | 负载测试 + 安全测试 |
| **M2 — Durable + 多 Agent** | 6-8 周 | 3 人 | 长会话恢复 + Plan-Execute + HITL | 混沌测试 + Eval gate |
| **M3 — K8s + A2A** | 持续 | 4+ 人 | Helm + Operator + 跨集群协议 | 多区域演练 |

---

## M0 — MVP（4-6 周）

### 目标
跑通 1 个内部业务 Agent（取代 1 个 Dify 应用），证明范式可行。

### 交付清单
- [ ] **Control Plane** 基础 CRUD + Manifest 加载 + Pydantic 校验
- [ ] **Orchestrator** 纯 ReAct 模式（LangGraph 默认 Agent）
- [ ] 工具支持：`builtin`（web_search）+ `http` + `mcp` 三类
- [ ] **Sandbox** 单镜像 Python，Docker + gVisor，仅 `exec_python`
- [ ] **Credential Proxy** 自研 aiohttp 版（不上 Envoy），支持 Vault 静态拉取
- [ ] **Event Store** Postgres + LangGraph PostgresSaver
- [ ] **Admin UI** Agent 列表 + Monaco YAML 编辑 + Session 时间线（只读）
- [ ] **docker-compose** 单机启动
- [ ] **vendor P0 基础设施**：event_log、persistence、checkpointer/store 工厂、stream_bridge、run_manager
- [ ] **🆕 vendor P0 生产必备中间件**（来自第三次源码扫描，详见 research/05）：
  - [ ] `dynamic_context_middleware`（193 行）— 前缀缓存优化，影响 API 成本 10x
  - [ ] `llm_error_handling_middleware`（368 行）— 断路器 + 自动重试，多租户级联故障防护
  - [ ] `sandbox_audit_middleware`（363 行）— LLM 生成命令的安全网（gVisor 之前的逻辑层防护）
  - [ ] `@Next/@Prev` 锚点系统（factory.py:289-379，~120 行）— 让外部 middleware 可干净插入到内置链
- [ ] **Manifest schema** 增加 `dynamic_context` 字段（声明动态注入的 memory / 日期 / 自定义 reminder）

### M0 新增工时（vs 原估计）
原 4-6 周 → **+5-7 工作日 ≈ 5-7 周**（vendor 这 4 个 P0 中间件 + 锚点系统 + 适配 + 测试）

### 关键风险
- gVisor 在 macOS docker desktop 不支持 → dev 用 OrbStack/Lima、prod Linux 用 runsc（compose runtime 区分）
- LangGraph 流式 SSE 的 backpressure → 早期实现注意 chunk 大小

### 验证手段
- **沿用一个现有 Dify 已上线业务迁移**（具体业务待用户告知）
- pinpoint 对比：token 消耗、p95 延迟、回答质量（人工评估）
- 手工渗透测试：在 sandbox 内 `cat /run/secrets/* 2>&1`、`curl 169.254.169.254`，确认全部失败

---

## M1 — 生产化（6-8 周）

### 目标
能跑 10+ Agent、3+ 租户、支撑生产流量。

### 交付清单
- [ ] **多租户**：tenant 字段贯通、Postgres RLS、quota 配额
- [ ] **Sub-Agent**：YAML 声明 + LangGraph subgraph 实现
- [ ] **Python 插槽**：`code.package` + `tool/graph/hook` 入口
- [ ] **Sandbox 池化**（warm pool）+ image build cache
- [ ] **Credential Proxy** 升级 Envoy + Lua + Vault dynamic
- [ ] **灰度发布**、版本回滚、A/B 流量切分
- [ ] **完整可观测性**：OpenTelemetry / Prometheus / Grafana / Loki
- [ ] **CLI**：`helix lint`、`helix run`（本地跑 manifest）
- [ ] **Admin UI** 升级：版本对比、灰度面板、secret 管理（写 Vault）
- [ ] **vendor P1 核心中间件**（subagent executor、guardrails、5 个核心 middleware）
- [ ] **🆕 vendor P1 增补**（来自第三次源码扫描）：
  - [ ] `thread_data_middleware`（118 行）— tenant/thread 元数据传播底座
  - [ ] `uploads_middleware`（295 行）— 文件上传 + 大纲提取（line number 引用，省 token）
  - [ ] `deferred_tool_filter_middleware`（107 行）— 配合 tool_search 解决工具数量爆炸
  - [ ] `token_usage_middleware`（303 行）— 步骤归因，dogfood 阶段成本分析
  - [ ] `reflection/resolvers.py`（98 行）— 字符串路径动态类加载

### M1 新增工时（vs 原估计）
原 6-8 周 → **+3-4 工作日**（这些都是直接 vendor + 适配 tenant 字段）

### 关键风险
- Sandbox 冷启动 P95 > 3s → 引入 warm pool，目标 < 500ms
- LangGraph 升级断 API → pin 主版本 + adapter 层
- Vault 单点故障 → HA 集群 + 短 TTL 缓存

### 验证手段
- **负载测试**：单机 200 concurrent sessions、TTFT < 1.5s
- **安全测试**：HouYi、Garak、自定义 prompt injection 脚本
- **混沌测试**：随机 kill sandbox / orchestrator，验证 session 可恢复
- **多租户隔离测试**（必跑用例见下文）

---

## M2 — Durable Execution + 多 Agent 协作（6-8 周）

### 目标
长会话恢复 + 多 Agent 编排可生产使用。

### 交付清单
- [ ] **Durable execution**：long-running session（小时级）、引擎重启可恢复（依赖 PostgresSaver + replay）
- [ ] **Plan-Execute 工作流模板**（lead agent 分解任务 → fan-out specialists → fan-in 合并）
- [ ] **Human-in-the-loop**：interrupt + 审批（LangGraph 原生支持）
- [ ] **Memory 分层**：working / archive / summarization 三层（借鉴 deer-flow 设计）
- [ ] **Observability** 升级：trace 加时间线视图（类似 LangSmith）
- [ ] **A/B Eval gate**：每个 Agent 关联 eval set，新版本上线前自动跑

### 关键风险
- 长会话状态膨胀 → 引入压缩策略（context window summarization）
- 多 Agent 死锁 → 全局 deadline + sub-agent 调用图静态检查

---

## M3 — K8s 迁移 + A2A 协议（持续）

### 目标
水平扩展能力 + 跨集群协作。

### 交付清单
- [ ] **Helm chart + Operator**
- [ ] **Sandbox** 转 K8s Pod + RuntimeClass=gvisor / Kata Containers
- [ ] **跨集群 A2A 协议**（Google A2A 或自定义）
- [ ] **与高级形态对接**：OpenAI Realtime、Claude Computer Use
- [ ] **内部 Skills 市场**（公司内部 marketplace）

---

## 验证方案

### 测试金字塔

| 层级 | 范围 | 工具 | 覆盖目标 |
|------|------|------|---------|
| **单元** | 每个包内部纯函数 | pytest + pytest-asyncio | ≥85% |
| **集成** | 服务间 API（含 Postgres、Redis） | testcontainers-python | ≥70% 关键路径 |
| **合约** | Manifest schema、SDK 接口 | schemathesis、Pydantic | 100% |
| **E2E** | docker-compose 全栈 | pytest + httpx + 真实 LLM mock | 5-10 场景 |
| **Chaos** | 随机故障 | toxiproxy / gremlin | 关键 SLO 不破 |

### 沙盒安全验证（多租户隔离必跑用例）

1. **文件系统**：tenant A 写 `/workspace/x`，tenant B 启动 sandbox 后 `ls /workspace` 必须为空
2. **进程**：sandbox A 启 daemon，sandbox B 内 `ps aux` 不可见
3. **网络**：sandbox 内尝试连接 `host.docker.internal`、`169.254.169.254`、Vault 内网 IP，全部 connection refused
4. **secret**：sandbox 内 `env`、`/proc/self/environ`、磁盘 `/var/run/secrets` 均无真实凭证
5. **资源耗尽**：fork bomb（`(){ :|:& };:`）应被 PID limit 终止，不影响其他 sandbox
6. **Side channel**：用 `perf` 测量 timing，验证 gVisor syscall 拦截使主机 binary 不可被 fingerprint
7. **逃逸**：跑 CVE-2019-5736 (runc)、CVE-2022-0185 等已知 PoC，必须失败（gVisor 用户态实现免疫大部分内核 CVE）

工具：`pyrasite` / `containerd-rootless-tester` / 自写 pytest fixtures

### 性能基准（M1 目标）

| 指标 | 目标 | 测量方式 |
|------|------|---------|
| Sandbox 冷启动（无 warm pool） | < 3s P95 | 直接 `docker run` 计时 |
| Sandbox 热启动（warm pool） | < 200ms P95 | acquire from pool |
| Session 启动到 first token (TTFT) | < 1.5s P95 | E2E SSE 测试 |
| 单机并发 Agent 数 | ≥ 100（2 vCPU、8GB sandbox 配置） | k6 + 自研 driver |
| Event log 写入 | ≥ 5000 evt/s | pgbench |
| Checkpoint 恢复 | < 500ms | kill orchestrator + 重连 |

### Dogfood 计划

> **业务选型原则**：Helix 是业务无关的多业务线引擎，dogfood 优先沿用现有 Dify 已上线业务做平迁对比（验证范式 + token/延迟/质量对比），不预设具体业务领域。

1. **第 1 业务（M0 末）**：**沿用现有 Dify 上线业务迁移**（业务待用户告知）
   - 平行运行 30 天（Dify + Helix 都接同一流量，对比答案/成本/延迟）
   - 切流量比例：1% → 10% → 50% → 100%
   - **优先选**：流量适中（1k-10k req/天）+ 工具调用简单（builtin/http/mcp）+ 合规简单
2. **第 2 业务（M1 中）**：选一个**带 Python 插槽需求**的 Dify 应用迁移（自定义 tool / custom workflow / hooks 完整链路）
   - 如该业务带合规要求（医疗/HR/金融），同步验证 `tenant_config.compliance_pack` 可插拔合规
3. **内部工具**（M1 末）：用 Helix 自身写 1 个内部研发工具（如 code-reviewer-agent / docs-summarizer / oncall-assistant），验证开发体验和 SDK 完整度

---

## 落地准备清单（启动前）

- [ ] **立项**：3 人（1 后端 lead + 1 平台 + 1 前端兼职），周期 6 个月到 M2
- [ ] 选定**首个迁移业务**（与业务方对齐：选哪个 Dify 已上线应用做 M0 dogfood，建议挑流量适中、工具简单、合规简单的）
- [ ] **1 台 Linux 服务器**（gVisor 不支持 macOS prod；dev 用 OrbStack/Lima）
- [ ] 申请 **Vault / Postgres / 监控基础设施**
- [ ] 团队补 **LangGraph 培训**（2-3 天）
- [ ] **第 1 周**：冲 M0 骨架——control-plane + orchestrator + 1 个最小 manifest（先用 examples/code-reviewer-agent 当烟囱 demo）跑通
