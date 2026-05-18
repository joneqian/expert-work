# 完整开发迭代计划

> 本文档把 [architecture/04-ROADMAP](./architecture/04-ROADMAP.md) 的里程碑 + [architecture/07-INFRASTRUCTURE-GAPS](./architecture/07-INFRASTRUCTURE-GAPS.md) 的 24 项 P0 串成一条**可执行的、单人版**迭代路径。是 04/07 的实施面投影，不替代它们。

## Context

**项目**：Helix — 业务无关的多租户企业 Agent 执行引擎，用于替代 Dify。已有完整架构设计（Brain-Hands-Session 三层解耦）、4 阶段路线图（M0→M3）、24 项产品级 P0 基础设施清单、DeerFlow vendor 检查表。当前代码仓近乎空白（仅 `src/helix_agent/__init__.py` 占位包 + 完整 `docs/`）。

**为什么需要这份计划**：把已有的架构文档落地到一个**单人可执行的、覆盖全周期**的迭代计划。文档中已经有 M0/M1/M2/M3 检查表和 24 项 P0 清单，但没把它们串成"下一步该做什么"的连贯路径，也没解决依赖排序、验证门、单人时长换算等执行问题。

**配置**（已确认）：
- **范围**：M0 → M3 全程
- **M0 质量门**：全 24 项 P0 一次到位（"真 M0"，文档中的 8-10 周三人方案）
- **团队**：单人
- **节奏**：按里程碑分阶段，不切 Sprint，用 checklist 驱动

**单人时长换算**（参考 architecture/04-ROADMAP §"工时估算修正"）：3 人团队估时 × 2.5-3 倍 = 单人估时。下表是按这个换算的总览。

---

## 时长总览（单人，全 P0）

| 阶段 | 3 人团队估时 | 单人估时 | 主要交付 |
|------|-------------|---------|---------|
| **Phase 0 — Pre-flight** | 1 周 | 2-3 周 | monorepo、CI/CD 骨架、Phase 0 决策、ADR backlog |
| **M0 — Product-grade MVP** | 8-10 周 | **5-7 个月** | 1 个 dogfood 业务 E2E + 全 24 P0 |
| **M0→M1 Gate** | — | 2-4 周 | dogfood 平行运行 30 天，参数对比 |
| **M1 — 生产化** | 10-12 周 | **6-8 个月** | 多租户、Sub-Agent、Python 插槽、Envoy/Vault dynamic、可观测、Admin UI 全功能 |
| **M2 — Durable + Multi-agent** | 8-10 周 | **5-6 个月** | 长会话恢复、Plan-Execute、HITL、Memory 三层、Eval gate |
| **M3 — K8s + 生态** | 持续 | 持续 | Helm、K8s 沙盒、A2A、内部 marketplace |
| **总计到 M2 product-ready** | ~6-8 个月 | **~16-20 个月** | |

> **现实校验**：单人做"全 24 P0 + M0→M3"是 1.5-2 年量级的项目。若进入实施后发现耗时偏离严重，建议在 M0→M1 Gate 节点重新评估，砍掉 P2 / 推迟部分 P1 / 引入第二个人。

---

## 迭代启动前置条件（所有 Phase / Stream 共用）

每个 Phase 或 Stream 开始**编码**前，必须先完成下面三步，否则不进入工作清单：

1. **架构设计**
   - 明确组件边界、数据流、关键 API/Schema
   - 列依赖（外部服务、内部模块）
   - 明确与 24 项 P0 横切关注点的对接点（auth/audit/PII/observability/limits/...）
   - 写下 Verification 方案（怎么证明这块设计可行）

2. **更新设计文档**
   - 已有模块 → 更新 `docs/architecture/` 对应文件
   - 新子系统 → 新增 `docs/architecture/subsystems/xx-*.md`
   - 技术选型 → 新增 `docs/adr/000X-*.md`
   - 设计与实现的偏离一律以**先改文档再改代码**的顺序解决

3. **设计 self-review**（单人项目用以下 checklist）
   - [ ] 边界清晰：能 1 句话说出"这个模块只做 X，不做 Y"
   - [ ] 数据流可追踪：每条数据从入口到出口路径明确
   - [ ] 失败模式列举：列出至少 3 种失败场景及对应处理
   - [ ] 与现有架构无冲突（看 `docs/architecture/00-OVERVIEW.md` 的核心范式）
   - [ ] 24 P0 关联标注：本 Stream 落实哪些 P0 已在 checklist 中点名

> 每个 Stream 的 Exit Criteria 隐含包含"对应设计文档已更新且与实现一致"。如果实施过程中发现设计漏洞，**回到第 1 步**，不是边写边改。

---

## 迭代收尾标准（所有 Phase / Stream 共用）

每个 Phase 或 Stream 视为"完成"前，必须**全部满足**下列 6 条 — 否则不进下一迭代：

1. **代码干净**
   - 无 `TODO` / `FIXME` / `XXX` / `HACK` 注释
   - 无占位 `pass` / `raise NotImplementedError`
   - 无被注释掉的死代码
2. **测试达标**
   - unit coverage ≥ 85%
   - integration coverage ≥ 70% 关键路径
   - 无 `skip` / `xfail`（除非引用具体 issue 编号）
   - 连跑 3 次稳定通过（无 flaky）
3. **文档同步**
   - `docs/architecture/` 下相关文档与实现一致
   - 本迭代涉及的 ADR 已合并
   - 本 Stream 在 ITERATION-PLAN.md 的 checklist 全部勾选
4. **可观测齐全**
   - 本迭代新组件均 emit metric + structured log + trace span
   - 告警阈值已定义（即使是 placeholder）
5. **CI 全绿**
   - lint + mypy + test + 镜像构建 + 安全扫描全绿
   - CodeQL 无新增 high / critical
6. **bug 不遗留**
   - 已知 bug 要么本迭代修复
   - 要么明确写进**下一迭代 checklist**（不能只在 issue tracker 飘着）

> **"能工作但有 N 个 TODO" = 该迭代未完成。**
>
> 技术债复利效应在 16-20 个月单人项目上尤其致命 — 一项 M0 没解决的债，到 M1 会变成相关的 5 项。零债收尾的成本，远低于积累后再清理。
>
> 若发现某项确实超出迭代范围，**减小本迭代范围（保留干净）** 或 **把超出部分明确移入下一迭代 checklist 后再关本迭代**；不允许带债结束。

---

## Phase 0 — Pre-flight（2-3 周）

### 目标
在写第一行业务代码前，把所有工程基础设施立起来，并解决 5 项启动前决策。

### Entry Criteria
- 已读完 [architecture/00-OVERVIEW](./architecture/00-OVERVIEW.md) 和 [architecture/01-SYSTEM-ARCHITECTURE](./architecture/01-SYSTEM-ARCHITECTURE.md)

### 工作清单

**0.1 启动前决策**（汇总于 [docs/decisions/phase-0-launch.md](../decisions/phase-0-launch.md)）
- [ ] **决策 1：dogfood 首迁业务** — ❓ TBD（可推迟到 Stream H 前再拍板，不阻塞 0.2-0.5 与 Stream A-G）
- [x] **决策 2：Linux 服务器落地** — ✅ 可申请 / 报备（阿里云 ECS，本周内到位；本地 dev 用 OrbStack / Lima）
- [x] **决策 3：基础设施部署姿态** — ✅ 混合：阿里云 ECS / RDS / KMS / OSS / ACR + 自托管 Langfuse（国内 LangSmith 不可用）；应用 Secret 存储（Vault vs KMS Secrets Manager）待 ADR-0007 决策
- [x] **决策 4：项目最终命名** — ✅ Helix-Agent（与现 repo / package 对齐；docs 措辞 cleanup 另开 PR）
- [ ] **决策 5：LangGraph 训练时间窗** — 软推迟，边干边读；如需集中补课再开 2-3 天

**0.2 Monorepo 与工具链**（参考 [architecture/03-MONOREPO-LAYOUT](./architecture/03-MONOREPO-LAYOUT.md)）
- [ ] 按文档 03 的目录树创建空骨架（`packages/`、`services/`、`tools/`、`tests/`、`infra/`）
- [ ] 根 `pyproject.toml` + uv workspace 配置（成员分别有自己的 pyproject.toml）
- [ ] 工具链固化：ruff + mypy + pytest + pytest-asyncio + pre-commit
- [ ] 把 `src/helix_agent/__init__.py` 占位删掉，转 `packages/helix-runtime/` 真正的包

**0.3 CI/CD pipeline**（落实 P0 #30、#31）
- [ ] CI 工作流升级：lint + mypy + test + 镜像构建 + Trivy 扫描
- [ ] 三环境配置框架：`environments/{dev,staging,prod}.yaml`
- [ ] CodeQL 已生效（保留），追加 `pip-audit`、`pre-commit-ci`
- [ ] 加 `dependabot.yml` 的 pip ecosystem（等 `pyproject.toml` 落地后）

**0.4 ADR Backlog**（在 `docs/adr/` 增补）
- [x] ADR-0002：状态层 schema（event_log + audit_log 分表）
- [x] ADR-0003：认证选型 — OIDC + 自建 Keycloak + JWT
- [x] ADR-0004：对象存储选型 — 阿里云 OSS + S3 兼容抽象层
- [x] ADR-0005：可观测栈选型 — 自托管 Langfuse + Prometheus + Loki + Tempo + Grafana
- [x] ADR-0006：合规可插拔架构（`compliance_pack` 字段语义）
- [x] ADR-0007：应用 Secret 存储 — 阿里云 KMS Secrets Manager（M0）+ M1 评估 Vault

**0.5 测试基础设施**（落实 P0 #34、#35）
- [x] pytest fixture 库（`tests/conftest.py`：`tmp_postgres_dsn`、`mock_llm`、`mock_secret_store` — Vault fixture 改名按 ADR-0007）
- [x] testcontainers-python 集成（postgres extra）
- [x] Mock LLM 抽象（MockLLM + FakeCompletion）+ VCR 脚手架（vcrpy 已装；cassettes/ 目录就绪，首条录制随 Stream E.1）

### Exit Criteria（Phase 0 → M0 验证门）
- [x] CI 跑通：lint + mypy + test + 安全扫描全绿（镜像构建按 JIT 策略推迟到首个 service 创建时，见 03-MONOREPO-LAYOUT.md §"创建策略"）
- [x] `uv sync` 在干净环境一行安装（PR #13 验证：26 packages resolved）
- [x] ADR-0002~0007 全部决策完成
- [x] 5 项启动前决策书面化并写入 `docs/decisions/phase-0-launch.md`（决策 1 dogfood 业务 + 决策 5 训练时间窗 显式标 TBD/软推迟）

---

## Phase M0 — Product-grade MVP（5-7 个月）

### 目标
1 个 dogfood 业务在 Helix 上跑通，与原 Dify 部署平行运行可对比；全 24 项 P0 横切关注点骨架到位。

### 关键原则
- **垂直切片优先**：先做最简单的"输入→LLM→工具→输出"端到端通路，再横向加 P0
- **每个 Stream 自带 verification**：完成后能跑可见 demo 或测试，不留账

> **Stream 排序遵循自下而上**：每个横切关注点（cancellation / rate limit / cache / metrics / audit / TLS / 对象存储）都被设计进**首次使用它的早期 Stream**，而不是放成晚期独立 Stream。这避免"先建上层再回头补底"的大面积返工。

### Stream A — Foundation Runtime（~7-9 周；含数据层 + 可观测三件套 + 网络基础）

参考：[architecture/01-SYSTEM-ARCHITECTURE](./architecture/01-SYSTEM-ARCHITECTURE.md)、[architecture/06-OPEN-SOURCE-DEPS](./architecture/06-OPEN-SOURCE-DEPS.md)

**设计文档全图**（设计先行规则已完成）：

| 子组 | 设计文档 |
|------|---------|
| A.1/A.2/A.4 数据层 schema + vendor | [ADR-0002](./adr/0002-state-layer-schema.md) + [06-OPEN-SOURCE-DEPS](./architecture/06-OPEN-SOURCE-DEPS.md) §P0 vendor 表 + [subsystems/17-audit-log](./architecture/subsystems/17-audit-log.md) |
| A.3 连接池 | [subsystems/23-postgres-scalability](./architecture/subsystems/23-postgres-scalability.md) |
| A.5 对象存储 | [ADR-0004](./adr/0004-object-storage.md) |
| A.6 备份 + WAL | [subsystems/22-disaster-recovery](./architecture/subsystems/22-disaster-recovery.md) |
| A.7/A.8/A.9 可观测三件套 | [subsystems/20-observability](./architecture/subsystems/20-observability.md) |
| **A.10-A.13 可靠性基础** | [subsystems/28-reliability-primitives](./architecture/subsystems/28-reliability-primitives.md) **🆕** |

**数据层基础**
- [ ] **A.1 Postgres schema**（event_log、thread_meta、checkpoint）— 落实 ADR-0002
- [ ] **A.2 Vendor DeerFlow P0 基础设施**（~3.4K LOC，参考文档 06）：
  - event_log 表与读写路径
  - persistence 工厂
  - PostgresSaver/InMemorySaver 抽象
  - stream_bridge（last-event-id 重连 + 心跳）
  - run_manager
- [ ] **A.3 PgBouncer + 连接池规划**（落实 P0 #29；设计：23-postgres-scalability）
- [ ] **A.4 audit_log 表**（与 event_log 分表，落实 P0 #5；设计：17-audit-log）— 让后续 Stream B/C 的事件从首次上线起就有审计落地
- [ ] **A.5 对象存储抽象**（落实 P0 #16；设计：ADR-0004）— S3 接口；MinIO 自托管 dev；uploads / snapshots / 归档共用基底
- [ ] **A.6 Postgres 自动备份 + WAL**（落实 P0 #17；设计：22-disaster-recovery）— RPO/RTO 文档

**可观测三件套**（日志 + trace + 指标必须同步建，否则后续代码 metric emit 缺失；统一设计：20-observability）
- [ ] **A.7 结构化日志规范**（落实 P0 #10）— 字段标准 + redaction 中间层
- [ ] **A.8 W3C Trace Context 规范**（落实 P0 #11）+ OpenTelemetry SDK 接入
- [ ] **A.9 指标体系**（落实 P0 #12）— Prometheus + 业务指标 + 技术指标 schema；让 A→F 所有新代码从第一天就 emit metric

**网络与可靠性基础**（统一设计：28-reliability-primitives）
- [ ] **A.10 全链路 TLS**（落实 P0 #9 in-transit）— Stream B 暴露 endpoint 前必须生效；mTLS 前提
- [ ] **A.11 Health checks**（落实 P0 #22）— liveness/readiness/startup probe + 依赖健康（DB/Redis/Vault）
- [ ] **A.12 Graceful shutdown**（落实 P0 #23）— SIGTERM、completer-in-flight、超时强制
- [ ] **A.13 超时分层**（落实 P0 #24）— request > session > tool > LLM 级联

**Stream A Verification**：起 docker-compose + insert 1 条 event + 1 条 audit_log；shutdown 优雅完成；trace_id 在日志中可串联；`/metrics` 可抓 ≥10 个核心 metric；对象存储 putObject/getObject 可调；Postgres 备份脚本恢复成功；TLS 拦截非加密连接

### Stream B — Control Plane（~3-4 周；含 API 层限流 + 取消信号链）

参考：[architecture/03-MONOREPO-LAYOUT](./architecture/03-MONOREPO-LAYOUT.md)、[architecture/02-AGENT-MANIFEST](./architecture/02-AGENT-MANIFEST.md)

- [ ] **B.1 FastAPI 骨架** + Pydantic v2 + SQLAlchemy 2.0
- [ ] **B.2 网关层限流 middleware**（落实 P0 #27 第 1 层）— per IP / per API key；与 B.1 同步装上，避免后期回炉
- [ ] **B.3 请求取消信号链 — API 层**（落实 P0 #25 第 1 段）— FastAPI request 断开 → 生成 cancellation token；与 B.1 同步装上
- [ ] **B.4 Manifest 加载与 Pydantic 校验**（含 `dynamic_context` 字段）
- [ ] **B.5 Agent CRUD API**（`services/control-plane/api/agents.py`）
- [ ] **B.6 Session CRUD API**（`services/control-plane/api/sessions.py`）
- [ ] **B.7 Run trigger API**（`services/control-plane/api/runs.py`，先返回 fake stream）

**Stream B Verification**：可通过 HTTPS 创建 agent + session + 触发 run 拿到 SSE stream；限流压测得到 429；客户端断开连接后服务端 context 在 200ms 内收到 cancellation；所有 admin 动作有 audit_log 落地（A.4）

### Stream C — Auth & 多租户基础（~3-4 周；含业务层限流）

参考：[architecture/07-INFRASTRUCTURE-GAPS](./architecture/07-INFRASTRUCTURE-GAPS.md) §2、§7

- [ ] **C.1 OIDC + JWT** 认证（落实 P0 #1）— Keycloak 本地 dev 环境
- [ ] **C.2 mTLS 服务间认证**（落实 P0 #2）— Control Plane ↔ Orchestrator ↔ Sandbox-Supervisor（基于 A.10 全链路 TLS）
- [ ] **C.3 API Key 管理**（落实 P0 #3）— 创建/吊销/轮换/限流
- [ ] **C.4 会话授权完整化**（落实 P0 #4）+ Postgres RLS
- [ ] **C.5 租户级 quota**（落实 P0 #20）— token / sandbox 实例数 / 准入控制
- [ ] **C.6 业务层限流**（落实 P0 #27 第 2 层）— per-tenant / per-agent / per-route，由 C.5 quota 引擎驱动
- [ ] **C.7 租户级配置隔离**（落实 P0 #21）— 每租户 model key / Vault path / MCP 白名单

**Stream C Verification**：跨租户访问被 RLS 拒绝；超 quota 返回 429；mTLS 握手失败的请求被拒；所有 auth 事件已写入 A.4 audit_log

### Stream D — 高级数据保护（~2 周；建立在 A.4 audit_log + A.10 TLS 之上）

参考：[architecture/07-INFRASTRUCTURE-GAPS](./architecture/07-INFRASTRUCTURE-GAPS.md) §3、§4

> A.4 audit_log 表 + A.10 全链路 TLS 已在 Stream A 完成；本 Stream 在它们之上做产品级数据保护。

- [x] **D.1 审计日志不可篡改**（落实 P0 #6）— append-only + WORM 备份到 S3 Object Lock（基于 A.5 对象存储）
- [x] **D.2 PII redactor 中间件**（落实 P0 #7）— 通用 + per-tenant `pii_fields` 配置驱动；接入 E.2 锚点系统的固定 anchor 位
- [x] **D.3 数据保留策略文档 + TTL 自动清理 job**（落实 P0 #8、#19）
- [x] **D.4 Postgres TDE**（落实 P0 #9 at-rest）— 数据落盘加密（ADR-0008：OS/卷层 + 云厂托管 RDS；不引入 pg_tde）

**Stream D Verification**：插入含 PII 数据走 Orchestrator，audit log 写入并被 redactor 处理；WORM 桶禁止 overwrite；TDE 启用后磁盘文件加密

### Stream E — Orchestrator + 工具体系（~7-9 周；中间件链先建 + provider 限流缓存 + 取消传播）

参考：[architecture/01-SYSTEM-ARCHITECTURE](./architecture/01-SYSTEM-ARCHITECTURE.md) §"Orchestrator"

**🔴 中间件链先建**（顺序硬性要求 — 否则 prefix cache / 断路器 / Langfuse 都覆盖不到首次 LLM 调用）：
- [x] **E.1 LangGraph PostgresSaver 接入**（#87 control-plane 注入；`make_checkpointer` postgres 后端）
- [x] **E.2 `@Next/@Prev` 锚点系统** — `MiddlewareChain` / `ANCHORS` / 拓扑排序
- [x] **E.3 `dynamic_context_middleware`**（**API 成本影响 10x，绝不能省**）
- [x] **E.4 `llm_error_handling_middleware`**（断路器 + 自动重试；防开发期被 LLM 限流爆）
- [x] **E.5 Langfuse middleware**（落实 P0 #15）— M0 用 span-recording 客户端；SDK 适配器 M1
- [x] **D.2 PII redactor middleware 注册**（与 D 跨流；#93 接 `DefaultSecretRedactor` 全局模式）

**业务流程实现**
- [x] **E.6 ReAct mode**（单 agent，无 sub-agent）— `build_react_graph`
- [x] **E.7 工具：builtin** — `web_search`（#91 接 Tavily）
- [x] **E.8 工具：HTTP** — M0 直连 + 租户 allowlist，**不**经 Credential Proxy（Mini-ADR E-7）
- [x] **E.9 工具：MCP** — `MCPTool` + `MCPServerPool`（#92；server 列表平台配置，Mini-ADR E-17）
- [x] **E.10 `sandbox_audit_middleware`**（LLM 命令安全网；中间件已实现，链装配随 Stream F sandbox 工具接入）
- [x] **E.10.5 `loop_detection_middleware`**（对照 deer-flow 补全；N=3 重复 tool_call 早期 abort —— ITERATION-PLAN 原未列，Stream E 设计细化新增）

**LLM 调用与流式**
- [x] **E.11 LLM Provider Fallback Chain** + E.11.5 国内 provider（kimi/glm/deepseek/qwen/doubao）
- [x] **E.12 提供商层限流**（落实 P0 #27 第 3 层）— per LLM key `RateLimitedProvider`
- [x] **E.13 LLM response cache**（落实 P0 #28）— `LLMResponseCache` lookup/store 中间件
- [x] **E.14 SSE 流式输出 + backpressure**（in-process 单体 `run_agent` + `sse_consumer`）
- [x] **E.15 请求取消的 engine 节点传播**（落实 P0 #25 第 2 段）— `CancellationToken` 协作式取消
- [x] **E 后续：agent factory + tool/middleware 装配**（#84/#85/#90 manifest → 编译 graph 装配 + control-plane 注入）

**Stream E Verification**：1 个 minimal agent 跑通 builtin/http/mcp 三类工具；故意触发 LLM 限流断路器接管；故意输入 PII redactor 工作；prefix cache 命中率 > 80%；cancellation 触达 in-flight LLM call；Langfuse 上每步可见 trace
> **验证状态**：上述每条机制均有 unit / integration 测试覆盖（`test_react_graph` / `test_tool_assembly` / `test_llm_error_handling` / `test_pii_redact` / `test_llm_cache_integration` / `test_cancellation_integration` / `test_middleware_chain_wiring`）；953 unit 全绿。holistic 的"单 minimal agent 一次跑通全部 + cache 命中率量化观测"需真实 LLM/Tavily 凭证 + 运行环境，归入 **M0→M1 Gate 的 dogfood 平行运行**。

### Stream F — Sandbox（~4-5 周；含 sandbox 端取消）

参考：[architecture/01-SYSTEM-ARCHITECTURE](./architecture/01-SYSTEM-ARCHITECTURE.md) §"Sandbox"、[research/02-sandbox-isolation.md](./research/02-sandbox-isolation.md)

- [x] **F.1 Sandbox Supervisor 服务**（`services/sandbox-supervisor/`）
- [x] **F.2 单 Python 镜像**（含 minimal Python 3.12 + 限定库）
- [x] **F.3 Docker + gVisor (runsc)** 启动路径
- [x] **F.4 `exec_python` tool 接入 Orchestrator**
- [x] **F.5 Credential Proxy aiohttp 自研版**（落实 M0 文档清单；通过 SecretStore 抽象拉取后端凭据）
- [x] **F.6 SecretStore 抽象 + 阿里云 KMS Secrets Manager 实现**（短 TTL 缓存；落实 ADR-0007）
- [x] **F.7 请求取消的 sandbox kill 信号**（落实 P0 #25 第 3 段）— 收到 cancellation token 后 SIGKILL 沙盒、回收资源

> 以下 F.8 – F.11 为 Stream F 推进中识别的设计细化新增（详见 [streams/STREAM-F-DESIGN](./streams/STREAM-F-DESIGN.md) § 1.1）：

- [x] **F.8 沙盒 Docker 集成测试 harness** — 进程内 supervisor + 真实 runc，自动化验收门 #1/#2/#4/#5/#8（Mini-ADR F-10/F-11/F-12）
- [x] **F.9 sandbox egress 网络隔离** — `helix-sandbox-egress` 为 Docker `--internal` 网络；`SandboxRuntimeProvider.docker_run_argv` 用 `--network helix-sandbox-egress` 启沙盒；测试矩阵 #49（`test_gate_49_network_egress_isolation` —— 实测连 `169.254.169.254` 被拒）关闭验收门 #3；Mini-ADR F-14
- [x] **F.10 credential-proxy 容器化 + 入 docker-compose** — 多阶段 uv 构建 Dockerfile（确立 helix 服务容器化 pattern，I.1 复用）+ `helix-sandbox-egress`（internal）/ 默认网双归属；Mini-ADR F-15
- [x] **F.11 control-plane 接 `ToolEnv.supervisor_client`** — `create_app` 经 `build_supervisor_client(settings.sandbox_supervisor_url)` 注入 `HTTPSupervisorClient`；全栈 egress e2e（#60）随 I.1 的 `test_fullstack_egress_e2e.py` 落地

**Stream F Verification**（落实 [architecture/04-ROADMAP](./architecture/04-ROADMAP.md) §"沙盒安全验证"7 条用例）：
- [x] 文件系统隔离测试 — F.8 harness 自动化
- [x] 进程隔离测试 — F.8 harness 自动化
- [x] 网络隔离测试（连 169.254.169.254 失败）— F.9 自动化（`--internal` 网络；测试矩阵 #49）
- [x] secret 不可见测试 — F.8 harness 自动化
- [x] fork bomb PID limit — F.8 harness 自动化
- [ ] timing 测试 — 需真实 runsc，归 M0→M1 Gate 人工渗透
- [ ] 跑 CVE-2019-5736 PoC，验证失败 — 需真实 runsc，归 M0→M1 Gate 人工渗透
- [x] 取消请求触发后 sandbox 在 1s 内被 kill 干净 — F.8 harness 自动化

### Stream G — SRE + Eval + Feedback（~4 周；消费 A 的可观测数据）

参考：[architecture/07-INFRASTRUCTURE-GAPS](./architecture/07-INFRASTRUCTURE-GAPS.md) §5、§8、§11、§12、[streams/STREAM-G-DESIGN](./streams/STREAM-G-DESIGN.md)（设计先行）

> A.9 指标体系 + A.4 audit_log + E.5 Langfuse 已提供数据底座；本 Stream 在它们之上做产品级 SRE/Eval/Feedback。
> **M0 范围 = 各 P0 的骨架 / 简版**（Mini-ADR G-1）：结构立起、接口定下、dogfood 能用；生产级自动化（错误预算自动冻结、LLM-as-judge gate、PagerDuty 实接、6+ 大盘）推 M1。

- [x] **G.0 可观测后端栈**（设计细化新增）— `infra/docker-compose.yml` 加 `observability` profile：OTel Collector + Prometheus + Tempo + Loki + Promtail + Grafana + Alertmanager 自托管；G.2/G.7 的前置（Mini-ADR G-2）
- [x] **G.1 SLO/SLI 定义文档**（落实 P0 #13）— 可用性、TTFT P95、恢复时间
- [x] **G.2 告警体系**（落实 P0 #14）— 飞书/PagerDuty 通道、P0/P1/P2 分级
- [x] **G.3 故障预案 runbook**（落实 P0 #26）— Postgres / Vault / Anthropic / sandbox 各 1 份
- [x] **G.4 Eval 框架**（落实 P0 #36）— Python-native 轻量 eval harness（`tools/eval/`，非 promptfoo —— Mini-ADR G-3，deer-flow 调研依据）
- [x] **G.5 Eval 数据集管理**（落实 P0 #38）— golden / regression set 版本化
- [x] **G.6 用户反馈收集**（落实 P0 #37）— 👍/👎 关联 turn + trace
- [x] **G.7 第一版 Grafana 大盘**
- [x] **G.8 event_log 冷归档 pipeline**（落实 P0 #18）— 半年后归档 S3，归档后查询路径

**Stream G Verification**：触发已知错误 → 告警弹出；跑 eval 集 → 拿到 score；feedback 写入并能溯源到 trace；归档脚本可恢复一条历史 event

### Stream H — Admin UI + Dogfood 准备（~3 周）

参考：[architecture/00-OVERVIEW](./architecture/00-OVERVIEW.md)、[architecture/04-ROADMAP](./architecture/04-ROADMAP.md) §"Dogfood 计划"

> **排序与 dogfood 重构**（评估 08 + Stream J 决策）：H.5 / H.6 的 dogfood 部分由 **canonical 能力 agent** 取代（与具体 Dify 业务解耦），且排在 **Stream J 之后**；H.1–H.4 Admin UI 不受影响、不依赖 Stream J。Admin UI 须做到产品级 UI/UX。

- [ ] **H.1 React 19 + Vite + Antd 骨架**
- [ ] **H.2 Agent 列表 + Monaco YAML 编辑器**
- [ ] **H.3 Session 时间线（只读）**
- [ ] **H.4 docker-compose `dev.yml` 单机一键启**
- [ ] **H.5 Phase 0.1 选定的 dogfood 业务 manifest 编写**
- [ ] **H.6 平行运行测试 harness**（Dify + Helix 同流量对比脚本）

### Stream I — 部署与发布闭环（~2 周；承接 Phase 0.3 CI/CD）

参考：[architecture/07-INFRASTRUCTURE-GAPS](./architecture/07-INFRASTRUCTURE-GAPS.md) §10、[streams/STREAM-I-DESIGN](./streams/STREAM-I-DESIGN.md)（设计先行；覆盖 I.1–I.4）

> Phase 0.3 已建立 baseline CI/CD + 三环境配置框架；本 Stream 把它生产化。

- [x] **I.1 服务容器化 + 全栈 compose** — M0 在线栈 = **3 个 helix 服务镜像**（control-plane / sandbox-supervisor / credential-proxy）多阶段 uv 构建 Dockerfile + `docker compose --profile full up` 起 M0 完整栈。pattern 由 **Stream F.10** 用 credential-proxy 作 pilot 先行确立；I.1 复用它新建 **2 个镜像**（control-plane、sandbox-supervisor）。
  - **orchestrator 不独立成镜像**（Mini-ADR I-1）：它是纯库（无 server 入口），control-plane 把它当 workspace 依赖装进自己镜像里 in-process 跑（STREAM-E-DESIGN § 2.6）；拆独立服务推 M1。
  - **sandbox-supervisor 用 docker-out-of-docker**（Mini-ADR I-2）：容器化后挂宿主 `/var/run/docker.sock` 启沙盒兄弟容器。
  - **I.1a**：上述 Dockerfile + compose（`migrate` 一次性服务 + 两服务 + `full` profile）。
  - **I.1b — 全栈 egress 端到端测试**（测试矩阵 #60，原属 Stream F.11）：`exec_python` → sandbox →（仅）真 credential-proxy → mock upstream 全链路通。原计划在 sandbox-supervisor 集成 harness 做，但忠实验证需 proxy + postgres + 迁移 + 种 `secret_allowlist`/secret 一整套 —— 等于在 harness 里重建迷你栈；故移入 I.1，待全栈 compose 就位后顺势做。
- [x] **I.2 服务发布策略**（落实 P0 #32）— control-plane 蓝绿（nginx upstream 切换）+ 加权金丝雀 + `tools/deploy/deploy.py`；STREAM-I-DESIGN § 6 / Mini-ADR I-4
- [x] **I.3 服务回滚机制**（落实 P0 #33）— `tools/deploy/rollback.py`（快路径切回旧色 / `--to-tag` 兜底）+ expand-contract 迁移纪律（迁移只向前）；STREAM-I-DESIGN § 7
- [x] **I.4 三环境部署文档**（dev / staging / prod）— `docs/runbooks/deployment.md`（三环境矩阵 / 配置来源 / 首次部署 / 发布清单）+ `environments/*.yaml` 结构补全；STREAM-I-DESIGN § 8

### Stream J — Agent Harness 能力补全（大里程碑；canonical agent + dogfood 的前置）

参考：[architecture/08-AGENT-CAPABILITY-ASSESSMENT](./architecture/08-AGENT-CAPABILITY-ASSESSMENT.md)、[streams/STREAM-J-DESIGN](./streams/STREAM-J-DESIGN.md)（设计先行）

> 2026-05-18 的 21 维 agent 能力评估发现：helix M0 把企业基础设施（Stream A–I）做到生产级，但 agent 认知 / harness 层有 8 个缺口。本 Stream 把这 8 个缺口补到生产级 —— helix 才是一个 harness 能力完整的通用 agent 平台。
> **canonical 能力 agent + dogfood 必须在 Stream J 之后做** —— canonical agent 是能力评估的载体，平台能力不完整就评不出完整能力面。
> 量级与 M0 若干 Stream 总和相当。每子项设计先行 + TDD + 一 PR 一子任务 + 零技术债。原 M1-D 的 `reflection/resolvers.py`、`uploads_middleware`、subagent executor 及 M1-F 的 Sub-Agent 项提前并入本 Stream。

- [ ] **J.1 规划 / 任务分解** — planner / todo，先拆解再执行
- [ ] **J.2 反思 / 自我修正** — 通用自我批判 / 修正中间件（非现有 loop detection）
- [ ] **J.3 长期记忆** — 跨会话记忆 store + 检索，接入上下文组装
- [ ] **J.4 Sub-agent / 多智能体委派** — agent 派生 / 委派 / agent-as-tool，隔离 + 取消 + token 预算
- [ ] **J.5 知识 / 检索（RAG）** — 检索增强；工具检索 vs 向量库由 STREAM-J-DESIGN 定
- [ ] **J.6 多模态输入** — 图像 / 文件输入
- [ ] **J.7 Skill + skill 进化** — skill 概念 + 习得 / 进化机制
- [ ] **J.8 人在回路 / 审批** — 运行中可中断 / 纠偏 / 危险操作审批
- [ ] **J.9 eval 强化** — 评估 G.4 骨架是否需升级（canonical agent 的度量工具）

**Stream J Verification**：每子项接入 live agent 路径、单测 + 集成测试 80% 覆盖；21 维能力矩阵无"缺失 / 骨架"遗留（eval 按 J.9 结论）。

### M0 Exit Criteria（M0 → M0→M1 Gate 验证门）

- [ ] 24 项 P0 全部勾选完成（参考 [architecture/07-INFRASTRUCTURE-GAPS](./architecture/07-INFRASTRUCTURE-GAPS.md) §"Gap 严重性矩阵"）
- [ ] **Stream J（Agent Harness 能力补全）9 子项完成** —— 21 维能力矩阵无缺口
- [ ] canonical 能力 agent 跑通 + staging 冒烟（便宜模型端到端真实 run）
- [ ] 测试金字塔达标：unit ≥ 85%、integration ≥ 70% 关键路径、E2E 5-10 场景
- [ ] 7 条沙盒安全验证用例全部通过
- [ ] SLO 第一个版本写入文档；P0 告警全部接入
- [x] **control-plane 状态持久化（SQL store 切换）** — `store_backend` setting（`memory` / `sql`），`create_app` 在 `sql` 时建 async engine + `build_rls_sessionmaker` 包装的 sessionmaker，把全部 10 个 `Sql*Store` / `DbFeedbackStore` 注入，lifespan `finally` dispose engine；`infra/docker-compose.yml` 的 `control-plane` 设 `HELIX_AGENT_STORE_BACKEND=sql`。设计：[STREAM-B-DESIGN](./streams/STREAM-B-DESIGN.md) Mini-ADR B-6。同 PR 补齐 `SqlAgentSpecStore` + 3 个 auth SQL store 此前缺失的集成测试。已知项：`/v1/quota/*` mTLS 跨租户路径在 RLS `FORCE` 下的写入待 Stream C 深化时处理（M0 进程内单体不走该 HTTP 路径）。

---

## M0→M1 Gate（2-4 周，dogfood 平行运行）

> **前置**：Gate 顺延到 **Stream J 之后**（先补全 harness 能力，再 dogfood）。
> **退出标准待重构**：当前"相对 Dify"框架（token 偏差、p95 倍数、质量对比）将改为**绝对目标 / eval-set 驱动** —— helix 是通用平台，dogfood 用 canonical 能力 agent、与具体 Dify 业务解耦（见 [architecture/08-AGENT-CAPABILITY-ASSESSMENT](./architecture/08-AGENT-CAPABILITY-ASSESSMENT.md)）。重构随 canonical agent 落地。

### 目标
证明 Helix 在真实流量下与 Dify 参数对齐。

### 工作清单

- [ ] 灰度切流：Dify 100% → Helix 1% → 10% → 50% → 100%
- [ ] **每日对比报表**：token 消耗、p95 延迟、TTFT、回答质量人工评估
- [ ] 手工渗透测试：sandbox 内 `cat /run/secrets/*`、`curl 169.254.169.254` 必须失败
- [ ] 跑 Garak / HouYi prompt injection 套件
- [ ] 30 天稳定性观察

### Exit Criteria
- [ ] token 消耗与 Dify 偏差 < ±10%
- [ ] p95 延迟 < Dify 的 1.2 倍
- [ ] 回答质量人工评估不劣于 Dify
- [ ] 30 天无 P0 事故
- [ ] **决策点**：是否进入 M1（继续）/暂停（修复后再上量）/回退（架构问题）

---

## Phase M1 — 生产化（6-8 个月）

### 目标
能跑 10+ Agent、3+ 租户、支撑生产流量；填齐 P1 必要项；DX 与 SRE 闭环。

### Entry Criteria
- M0→M1 Gate 通过

### 工作清单

> **M1 排序遵循自下而上**：先做基础设施硬化（沙盒池化、数据生命周期、凭证代理）和可观测核心，再做依赖这些底座的高阶能力（多租户深化、Sub-Agent、Python 插槽），最后是灰度/UI/dogfood。

#### M1-A Sandbox 池化 + 镜像供应链（~4 周）
- [ ] Sandbox warm pool（目标 P95 < 500ms）
- [ ] 镜像 build cache + 内部 registry
- [ ] Trivy/Grype 扫描 CI gate（落实 P1 镜像扫描）
- [ ] cosign 签名 manifest + image（落实 P1 supply chain）

#### M1-B 数据生命周期硬化（~3 周）
- [ ] 跨 AZ DR 演练（落实 P1 跨 AZ）
- [ ] IaC（Terraform）描述基础设施
- [ ] DB zero-downtime migration 规范（Alembic + expand-contract）
- [ ] 数据归档完整 pipeline
- [ ] **retention-cleanup-job CI-only `permission denied` 收尾** — `test_event_log_retention_deletes_old_rows` / `test_jwt_blacklist_expired_rows_deleted` 在 CI 的 testcontainers Postgres 上 `DELETE` 报 `permission denied`（本地通过；`audit_log` 同 role/grant 模式正常）。job 已把 `event_log` 的 `ctid IN (SELECT … LIMIT N)` 改为扁平 `DELETE`，xfail 可能已 stale。需一次 CI run 确认两测试是否 `XPASS`：是 → 删 xfail；否 → 定位 asyncpg/SQLAlchemy 2.0/PG 交互（疑似 persistence engine factory 的 per-txn `SET LOCAL ROLE` 覆盖了 superuser 连接的有效 role）。M0 零技术债盘点新增；`test-integration` job 为 `continue-on-error` 非门控，影响低。

#### M1-C Credential Proxy 升级（~3 周）
参考：[architecture/subsystems/11-credential-proxy.md](./architecture/subsystems/11-credential-proxy.md)（如有）
- [ ] Envoy + Lua + Vault dynamic secrets
- [ ] 自动密钥轮换 + 短 TTL

#### M1-D Vendor P1 中间件（~3 周）
参考：[architecture/04-ROADMAP](./architecture/04-ROADMAP.md) §"M1 vendor P1"
- [ ] `thread_data_middleware`（118 LOC）
- [ ] `deferred_tool_filter_middleware`（107 LOC）
- [ ] `token_usage_middleware`（303 LOC）
- [ ] ~~`uploads_middleware`~~ → 提前至 **Stream J.6**（多模态输入）
- [ ] ~~`reflection/resolvers.py`~~ → 提前至 **Stream J.2**（反思 / 自我修正）
- [ ] ~~subagent executor + guardrails~~ → 提前至 **Stream J.4**（Sub-agent）

#### M1-E 可观测核心生产化（~2 周；紧跟 M1-B 数据层硬化）
- [ ] OpenTelemetry / Prometheus / Grafana / Loki 全栈生产化
- [ ] Langfuse 业务大盘
- [ ] 成本可视化大盘（基于 token_usage_middleware）

#### M1-F 多租户 + Sub-Agent + Python 插槽（~6 周；建立在 M1-A/B/C/D 硬化基础上）
参考：[architecture/02-AGENT-MANIFEST](./architecture/02-AGENT-MANIFEST.md) §"Python 插槽"
- [ ] ~~Sub-Agent YAML 声明 + LangGraph subgraph 实现~~ → 提前至 **Stream J.4**（Sub-agent / 多智能体委派）
- [ ] Python 插槽：`code.package` + `tool/graph/hook` 入口（依赖 M1-A cosign 供应链）
- [ ] tenant_id 全链路贯通深化（依赖 M1-C Vault dynamic）
- [ ] 多租户隔离自动化测试（cross-tenant 数据泄漏检测）

#### M1-G 灰度 + Canary + 回滚（~3 周）
- [ ] manifest 版本灰度面板
- [ ] 灰度过程指标自动采集（消费 M1-E 数据）
- [ ] A/B 流量切分

#### M1-H 运维可观测扩展（~2 周）
- [ ] Runbook 库（每个 P0 告警 1 份 SOP）
- [ ] Sentry / GlitchTip 错误追踪
- [ ] Falco 运行时安全监控（落实 P1 Falco）

#### M1-I CLI + Admin UI 升级（~3 周）
- [ ] `helix lint` + `helix run`（本地跑 manifest）
- [ ] Admin UI：版本对比、灰度面板、Vault secret 管理
- [ ] JSON Schema 发布（VS Code/IntelliJ 自动补全）

#### M1-J 第二个 dogfood 业务（~4 周）
参考：[architecture/04-ROADMAP](./architecture/04-ROADMAP.md) §"Dogfood 计划"
- [ ] 选一个带 Python 插槽需求的 Dify 应用迁移
- [ ] 若带合规需求，验证 `compliance_pack` 可插拔

### M1 Exit Criteria
- [ ] 10+ Agent 同时在线
- [ ] 3+ 租户隔离运行
- [ ] 性能基准达标（参考 [architecture/04-ROADMAP](./architecture/04-ROADMAP.md) §"性能基准 M1 目标"）：
  - Sandbox 冷启动 P95 < 3s、热启动 P95 < 200ms
  - TTFT P95 < 1.5s
  - 单机并发 Agent ≥ 100
  - Event log 写入 ≥ 5000 evt/s
  - Checkpoint 恢复 < 500ms
- [ ] 负载测试：单机 200 concurrent sessions 不崩
- [ ] 多租户隔离测试用例 100% 通过
- [ ] 第二个 dogfood 业务上量 100%

---

## Phase M2 — Durable Execution + 多 Agent 协作（5-6 个月）

### 目标
长会话恢复 + 多 Agent 编排可生产使用。

### Entry Criteria
- M1 Exit 全过

### 工作清单

#### M2-A Durable Execution（~5 周）
参考：[architecture/subsystems/19-durable-execution.md](./architecture/subsystems/19-durable-execution.md)（如有）
- [ ] Long-running session（小时级）
- [ ] 引擎重启可恢复（PostgresSaver + replay 加固）
- [ ] Context window 压缩策略（summarization）

#### M2-B Plan-Execute + HITL（~4 周）
- [ ] Plan-Execute 工作流模板（lead → fan-out → fan-in）
- [ ] LangGraph interrupt + 审批 UI
- [ ] 全局 deadline + sub-agent 调用图静态检查

#### M2-C Memory 三层（~3 周）
参考：[research/04-deerflow-source-analysis.md](./research/04-deerflow-source-analysis.md) §"Memory"
- [ ] working / archive / summarization 三层实现
- [ ] 自动晋升与召回策略

#### M2-D Eval Gate + 持续改进 pipeline（~3 周）
- [ ] A/B Eval gate：新版本上线前自动跑
- [ ] 用户反馈 → 自动加入 eval → 触发 prompt 改进 → 验证 → 上线
- [ ] Quality dashboard（每个 agent 当前质量得分）

#### M2-E Trace 时间线 + Session 回放（~3 周）
- [ ] LangSmith 风格 trace 时间线视图
- [ ] event_log 重放（time travel）

#### M2-F Chaos 工程（~3 周）
- [ ] toxiproxy / chaos-mesh 集成
- [ ] 定期跑 chaos 套件（断 PG / Vault / Anthropic / sandbox 随机 kill）
- [ ] SLO 不破即通过

#### M2-G 第三个 dogfood：内部研发工具（~4 周）
- [ ] 用 Helix 自身写 1 个内部工具（code-reviewer / docs-summarizer / oncall-assistant）
- [ ] 验证开发体验、SDK 完整度、Cookbook 第一篇

### M2 Exit Criteria
- [ ] 小时级长会话重启可恢复
- [ ] HITL 审批流跑通
- [ ] Eval gate 阻断质量回归至少 1 次
- [ ] Chaos 套件 SLO 不破
- [ ] 内部研发工具上线

---

## Phase M3 — K8s + 生态（持续）

### 目标
水平扩展能力 + 跨集群协作 + 生态构建。

### Entry Criteria
- M2 Exit 全过

### 工作清单（无固定先后，按业务需要排序）

- [ ] **Helm chart + Operator**
- [ ] **Sandbox 转 K8s Pod + RuntimeClass=gvisor / Kata**
- [ ] **跨集群 A2A 协议**（Google A2A 或自定义）
- [ ] **OpenAI Realtime / Claude Computer Use 接入**
- [ ] **内部 Skills 市场**（公司内部 marketplace）
- [ ] **Manifest 继承 / mixin**
- [ ] **HIPAA / SOC2 / 等保三级正式审计**（按业务需要启动）
- [ ] **跨 region 数据驻留**

### M3 Exit Criteria
无固定终点；以业务驱动持续演进。

---

## 横切关注点

### 风险登记表

| 风险 | 触发场景 | 缓解 |
|------|---------|------|
| **Prefix cache 失效** | system_prompt 注入动态内容 | manifest schema 强制 + CI lint 检查 + Anthropic SDK 监控命中率 |
| **gVisor macOS 不可用** | 本地开发 | OrbStack/Lima for dev；CI 在 Linux runner 跑 sandbox 测试 |
| **LangGraph 升级断 API** | 主版本升级 | pin 主版本 + 适配层 + 升级前跑全 eval |
| **Vault 单点故障** | M0 单实例时 | M0 短 TTL 缓存兜底；M1 上 HA 集群 |
| **单人时长偏离严重** | M0 超 9 个月仍未跑通 dogfood | M0→M1 Gate 重评估；考虑砍 P2、推迟 P1、引入第二人 |
| **DeerFlow 上游 breaking change** | vendor 后季度 sync 时 | 季度 sync 流程文档化；vendor diff 自动检测 CI |
| **Sandbox 镜像膨胀** | M0 后追加包 | 镜像分层 + multi-stage + size budget |

### 验证框架（贯穿 M0→M3）

| 层级 | 工具 | 覆盖目标 | 跑频 |
|------|------|---------|------|
| Unit | pytest + pytest-asyncio | ≥85% | 每次 commit |
| Integration | testcontainers-python | ≥70% 关键路径 | 每次 PR |
| Contract | schemathesis + Pydantic | 100% manifest schema | 每次 PR |
| E2E | docker-compose + httpx + Mock LLM | 5-10 场景 | 每次 PR |
| Chaos | toxiproxy / chaos-mesh | 关键 SLO 不破 | 每周 + release 前 |
| Sandbox 安全 | 自写 pytest fixture + CVE PoC | 7 条用例 100% | release 前 |
| Eval | promptfoo + golden set | 不回归 | 每次 manifest 改 + nightly |

### 还需要最终拍板的开放决策

1. **dogfood 首迁业务名**（Phase 0.1 决策 1）
2. **认证方案**（OIDC + Keycloak？或公司内部已有 SSO？）— 影响 ADR-0003
3. **可观测栈**（Langfuse 自托管 vs LangSmith 云？）— 影响 ADR-0005
4. **对象存储**（自托管 MinIO vs 云 OSS？）— 影响 ADR-0004
5. **是否要在 M0→M1 Gate 时引入第二人**（单人 16-20 个月偏长）

---

## 关键文档参考地图

| 需要做的事 | 看哪份文档 |
|-----------|-----------|
| 总体了解项目 | [README](./README.md) + [architecture/00-OVERVIEW](./architecture/00-OVERVIEW.md) |
| 系统架构、组件矩阵 | [architecture/01-SYSTEM-ARCHITECTURE](./architecture/01-SYSTEM-ARCHITECTURE.md) |
| Manifest schema、Python 插槽 | [architecture/02-AGENT-MANIFEST](./architecture/02-AGENT-MANIFEST.md) |
| 仓库目录树、M0 首批文件 | [architecture/03-MONOREPO-LAYOUT](./architecture/03-MONOREPO-LAYOUT.md) |
| M0/M1/M2/M3 检查表、性能基准 | [architecture/04-ROADMAP](./architecture/04-ROADMAP.md) |
| 已识别风险 | [architecture/05-RISKS](./architecture/05-RISKS.md) |
| DeerFlow vendor 文件清单 | [architecture/06-OPEN-SOURCE-DEPS](./architecture/06-OPEN-SOURCE-DEPS.md) |
| 24 项 P0 详细分组 | [architecture/07-INFRASTRUCTURE-GAPS](./architecture/07-INFRASTRUCTURE-GAPS.md) |
| 单个子系统深入 | [architecture/subsystems/00-INDEX](./architecture/subsystems/00-INDEX.md) |
| 编排框架选型证据 | [research/01-orchestration-engines.md](./research/01-orchestration-engines.md) |
| 沙盒方案对比 | [research/02-sandbox-isolation.md](./research/02-sandbox-isolation.md) |
| Anthropic Managed Agents 范式 | [research/03-managed-agents-platforms.md](./research/03-managed-agents-platforms.md) |
| DeerFlow 第一次源码扫描 | [research/04-deerflow-source-analysis.md](./research/04-deerflow-source-analysis.md) |
| 🔥 第三次扫描（Prefix cache、6 中间件、@Next/@Prev） | [research/05-deerflow-deeper-scan.md](./research/05-deerflow-deeper-scan.md) |
| Python vs TS 决策证据 | [adr/0001-python-vs-typescript-stack.md](./adr/0001-python-vs-typescript-stack.md) |

---

## 关键文件路径（M0 实施时直接编辑）

依据 [architecture/03-MONOREPO-LAYOUT](./architecture/03-MONOREPO-LAYOUT.md)（实施时如目录结构与文档不一致，以文档为准重建）：

- 根：`pyproject.toml`、`uv.lock`、`docker-compose.dev.yml`、`docker-compose.prod.yml`
- 共享运行时：`packages/helix-runtime/event_log/`、`packages/helix-runtime/middleware/`
- 控制面：`services/control-plane/api/{agents,sessions,runs}.py`
- 编排器：`services/orchestrator/graph_builder/builder.py`、`services/orchestrator/runtime/`
- 沙盒：`services/sandbox-supervisor/pool/supervisor.py`
- 凭证代理：`services/credential-proxy/proxy.py`（M0 aiohttp 版）
- MCP 网关：`services/mcp-gateway/{client,pool}.py`
- Admin UI：`services/admin-ui/`
- 基础设施：`infra/` + `environments/{dev,staging,prod}.yaml`
- ADR：`docs/adr/0002-...md` 起新增
- 决策：`docs/decisions/dogfood-target.md` 等

---

## 计划使用建议

1. **作为活文档维护**：每完成一个 Stream，在对应 checklist 打勾；遇到偏差修订估时与依赖
2. **每月节点回顾**：对照 Phase Exit Criteria 看是否要砍范围或引入帮手
3. **gate 不可越过**：M0 Exit / M1 Exit 必须全勾才进下阶段，否则会带技术债指数级增长
4. **优先级冲突时的取舍顺序**：安全（C/D Stream） > 可观测（A.4/A.5 + I Stream）> 性能 > DX > 业务功能扩展
