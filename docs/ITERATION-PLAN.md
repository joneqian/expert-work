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
| **Stream K — Capability Hardening Sprint** | — | **7-10 周** | 13 条 (c) 类弱版补到生产级；先于 Stream J 剩余子项 |
| **Stream L — Hermes-derived 单 turn 能力强化 sprint** | — | **4-6 周** | 8 条 Hermes 单 turn 成熟能力补到生产级；与 Stream J 剩余子项并行 |
| **M0→M1 Gate**（含 Capability Uplift Sprint） | — | **12-13 周** | 30 天稳定性观察 + 安全演练 + **Capability Uplift Sprint 8 项 gap 提前**（gap report 5 + Memory 深化 3，详见 [§ Capability Uplift Sprint](#capability-uplift-sprint12-13-周与-30-天稳定性观察期并行)） |
| **M1 — 生产化**（减除已提前 4 项） | 10-12 周 | **5-7 个月** | 多租户、Sub-Agent、Python 插槽、Envoy/Vault dynamic、可观测；含 J.7b 7 项 + #4/#5/#7/#8 启用调参 |
| **M2 — Durable + Multi-agent** | 8-10 周 | **5-6 个月** | 长会话恢复、Plan-Execute、HITL、Memory 三层（含 #7 凝结引擎已提前）、Eval gate |
| **M3 — K8s + 生态** | 持续 | 持续 | Helm、K8s 沙盒、A2A、内部 marketplace |
| **总计到 M2 product-ready** | ~6-8 个月 | **~17-21 个月** | 含 Capability Uplift Sprint 12-13 周；M1 因 4 项提前而缩短 |

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
- [x] **A.1 Postgres schema**（event_log、thread_meta、checkpoint）— 落实 ADR-0002（迁移 0001/0002）
- [x] **A.2 Vendor DeerFlow P0 基础设施**（~3.4K LOC，参考文档 06）：
  - event_log 表与读写路径
  - persistence 工厂
  - PostgresSaver/InMemorySaver 抽象
  - stream_bridge（last-event-id 重连 + 心跳）
  - run_manager
- [ ] **A.3 PgBouncer + 连接池规划**（落实 P0 #29；设计：23-postgres-scalability）
- [x] **A.4 audit_log 表**（与 event_log 分表，落实 P0 #5；设计：17-audit-log）— 让后续 Stream B/C 的事件从首次上线起就有审计落地
- [x] **A.5 对象存储抽象**（落实 P0 #16；设计：ADR-0004）— S3 接口；MinIO 自托管 dev；J.6 多模态首消费者（uploads 端点 + ObjectStoreImageResolver）
- [x] **A.6 Postgres 自动备份 + WAL**（落实 P0 #17；设计：22-disaster-recovery）— `docs/dr/RUNBOOK.md` RPO/RTO 文档 + K15 PG 恢复演练 testcontainers 集成测试

**可观测三件套**（日志 + trace + 指标必须同步建，否则后续代码 metric emit 缺失；统一设计：20-observability）
- [~] **A.7 结构化日志规范**（落实 P0 #10）— 字段标准 + redaction 中间层。`ObservabilityMiddleware` 骨架在；字段规范文档化待 M0 收尾补
- [~] **A.8 W3C Trace Context 规范**（落实 P0 #11）+ OpenTelemetry SDK 接入。OTel SDK 接入有；W3C header 传播 + cross-service 验证待补
- [~] **A.9 指标体系**（落实 P0 #12）— Prometheus + 业务指标 + 技术指标 schema。`helix_*` counter/histogram validator 已锁；metrics schema 文档化待 M0 收尾补

**网络与可靠性基础**（统一设计：28-reliability-primitives）
- [~] **A.10 全链路 TLS**（落实 P0 #9 in-transit）— Stream B 暴露 endpoint 前必须生效；mTLS 前提。应用层 mTLS (MTLSVerifier + XFCC) 已有；docker-compose nginx TLS 终止待补
- [x] **A.11 Health checks**（落实 P0 #22）— liveness/readiness/startup probe + 依赖健康（DB/Redis/Vault）
- [x] **A.12 Graceful shutdown**（落实 P0 #23）— SIGTERM、completer-in-flight、超时强制（FastAPI `lifespan` + `Lifecycle`）
- [x] **A.13 超时分层**（落实 P0 #24）— request > session > tool > LLM 级联（cancellation_token 全链路 + K9 reflect deadline + L3 stream deadline）

**Stream A Verification**：起 docker-compose + insert 1 条 event + 1 条 audit_log；shutdown 优雅完成；trace_id 在日志中可串联；`/metrics` 可抓 ≥10 个核心 metric；对象存储 putObject/getObject 可调；Postgres 备份脚本恢复成功；TLS 拦截非加密连接

### Stream B — Control Plane（~3-4 周；含 API 层限流 + 取消信号链）

参考：[architecture/03-MONOREPO-LAYOUT](./architecture/03-MONOREPO-LAYOUT.md)、[architecture/02-AGENT-MANIFEST](./architecture/02-AGENT-MANIFEST.md)

- [x] **B.1 FastAPI 骨架** + Pydantic v2 + SQLAlchemy 2.0
- [x] **B.2 网关层限流 middleware**（落实 P0 #27 第 1 层）— per IP / per API key；与 B.1 同步装上，避免后期回炉
- [x] **B.3 请求取消信号链 — API 层**（落实 P0 #25 第 1 段）— FastAPI request 断开 → 生成 cancellation token；与 B.1 同步装上
- [x] **B.4 Manifest 加载与 Pydantic 校验**（含 `dynamic_context` 字段）
- [x] **B.5 Agent CRUD API**（`services/control-plane/api/agents.py`）
- [x] **B.6 Session CRUD API**（`services/control-plane/api/sessions.py`）
- [x] **B.7 Run trigger API**（`services/control-plane/api/runs.py`，先返回 fake stream）

**Stream B Verification**：可通过 HTTPS 创建 agent + session + 触发 run 拿到 SSE stream；限流压测得到 429；客户端断开连接后服务端 context 在 200ms 内收到 cancellation；所有 admin 动作有 audit_log 落地（A.4）

### Stream C — Auth & 多租户基础（~3-4 周；含业务层限流）

参考：[architecture/07-INFRASTRUCTURE-GAPS](./architecture/07-INFRASTRUCTURE-GAPS.md) §2、§7

- [~] **C.1 OIDC + JWT** 认证（落实 P0 #1）— Keycloak 本地 dev 环境。`JWTVerifier` + `HTTPJWKSProvider` 代码已有；docker-compose Keycloak service + dev realm 配置待补
- [x] **C.2 mTLS 服务间认证**（落实 P0 #2）— Control Plane ↔ Orchestrator ↔ Sandbox-Supervisor（基于 A.10 全链路 TLS）
- [x] **C.3 API Key 管理**（落实 P0 #3）— 创建/吊销/轮换/限流（K1 PR #186 补 rotation + grace window + audit）
- [x] **C.4 会话授权完整化**（落实 P0 #4）+ Postgres RLS（`build_rls_sessionmaker` + `RLSContextMiddleware`）
- [x] **C.5 租户级 quota**（落实 P0 #20）— token / sandbox 实例数 / 准入控制（`TenantQuotaStore` + `QuotaService` + `ReservationReaper`）
- [x] **C.6 业务层限流**（落实 P0 #27 第 2 层）— per-tenant / per-agent / per-route，由 C.5 quota 引擎驱动（`TenantRateLimitMiddleware`）
- [x] **C.7 租户级配置隔离**（落实 P0 #21）— 每租户 model key / Vault path / MCP 白名单

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
- [x] **G.9 token_usage_middleware** #282 — `after_llm_call` 中间件累计 input/output/cached tokens per (tenant, agent, model)；emit `helix_llm_token_usage_total{tenant,agent,model,type}` counter；写入 `token_usage` 持久表（migration 0036；与 C.5 `TokenBudgetLedger` 并存，整合推 M1-D）。**2026-05-25 完成**：`TokenUsageMiddleware`（runtime）+ `TokenUsageStore` ABC + `InMemoryTokenUsageStore` / `SqlTokenUsageStore` + middleware_assembly 注入 + 失败不阻塞 LLM 调用。测试覆盖：5 个 middleware 单测（counter / persist happy / no-tenant skip / persist failure swallow / cache token 分桶）+ 6 个 store 单测。零债 6 条 ✅。Grafana 大盘 + token_usage 表查询面延后到 H.4 治理面接入（属 Admin UI 工作）。

**Stream G Verification**：触发已知错误 → 告警弹出；跑 eval 集 → 拿到 score；feedback 写入并能溯源到 trace；归档脚本可恢复一条历史 event；`helix_llm_token_usage_total` counter 在大盘可见，按 (tenant, agent, model) 维度可拆

### Stream H — Admin UI（~2.5-3 周；产品级 UI/UX；操作端唯一）

参考：[architecture/00-OVERVIEW](./architecture/00-OVERVIEW.md)、[streams/STREAM-H-DESIGN](./streams/STREAM-H-DESIGN.md)（设计先行）

> **2026-05-25 范围澄清**（用户确认）：Business 系统通过 API 消费 helix 的 per-user 持久 agent；helix **不自带末端用户对话 UI**（末端用户通过 business 系统自己的 UI 与 agent 对话）。Admin UI 仅服务操作人群（平台 admin / agent 开发者 / 运营 / SRE）—— **单面 SPA**。debug 能力作为 per-agent **Playground tab** 嵌入 Agent 详情页。原 H.4（用户面）取消；其中 memory / artifact / sandbox 的 backend API 不变，但治理视角下出现在 H.4 跨 agent 治理面，不再独立"用户面"。
>
> **2026-05-20 范围重写**：原 H.1–H.4 范围过于通用 → 重写为锚定能力面的五子项；H.5/H.6 dogfood 已由 canonical 能力 agent 取代；H.5 docker-compose dev.yml 已在 I.1 落地。Admin UI 必须**产品级 UI/UX**（[memory:admin-ui-product-grade](../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_admin_ui_product_grade.md)：先出设计规范文档再实现，不堆 Antd 默认组件）。

- [x] **H.1a 设计基线** #262 #263 — `docs/design/admin-ui-philosophy.md`（6 原则 + Agent-中心 IA + WCAG AA 承诺）+ `admin-ui-language.md`（tokens 全量值 + Antd override + 术语表）+ `mockups/01..08-*.html` + `mockups/shared/{tokens.css, shell.css}` + `docs/streams/STREAM-H-DESIGN.md` + brand glyph。锁 [memory:admin-ui-design-baseline](../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/project_admin_ui_design_baseline.md) 10 条决策
- [x] **H.1b React 19 + Vite + Antd 5 骨架** #264 #272 #274 #277 #278 #279 #280 #281 — `apps/admin-ui/` Vite 工程 + Antd 5 ConfigProvider 接入 helix tokens + i18n（zh-CN/en）+ 路由（`react-router-dom` v7）+ 鉴权（OIDC code-flow PKCE + silent renew，留 API Key/JWT 旁路）+ CommandPalette（Cmd+K real routes）+ Lucide 图标 + dark/light theme toggle + Shell（瘦左导航 + 薄顶 bar）+ 6 SDK clients + 3 pages drop mock data + Storybook + Playwright + axe a11y。**2026-05-25 完成（8 PR 链）**：#264 demo → #272 CI → #274 PR 1/N scaffold + auth + tenant scope + live Agents → #277 PR 2a /v1/me + i18n + Cmd+K → #278 PR 2b OIDC PKCE → #279 移除 admin-ui-demo → #280 PR 3 6 SDK clients → #281 PR 4 Storybook + E2E + a11y。零债 6 条 ✅。
- [x] **H.2 Agent / Manifest 管理 + Playground** #284 #285 #286 — agents 列表(H.1b PR1 已落)+ **Monaco YAML 编辑器**(PR1 替换 JSON pretty-print,view/edit/save 三态,PUT /v1/agents 端到端校验)+ **Create Agent flow**(PR2 列表 Create button + Drawer + Monaco YAML stub + POST /v1/agents + 成功后导航到 detail)+ **Playground tab**(PR3 fetch + ReadableStream SSE + Run/Stop + 色彩分类事件日志 + 自动滚屏 + 新建会话)。**2026-05-25 完成(3 PR 链)**:接 B.5 Agent CRUD + sessions/runs SSE。零债 6 条 ✅。**(a) 显式推迟**:(1) 改 manifest snippet 重跑 → 需要后端 ad-hoc spec override(today runs 始终绑 active AgentSpecRecord),follow-up;(2) 版本对比 / 历史回滚 UI → follow-up;(3) tool calls 时间线语义化(超过 event tag 颜色编码)→ polish 项;(4) 审批 mid-run UX → H.3 一起做
- [x] **H.3 Runs + Trace + Approval** #289 #290 #291 #292 #293 #294 — `GET /v1/runs` 跨 thread 索引 + `agent_run.trace_id` 持久化(migration 0037)+ `run_event` 表(migration 0038)+ producer 双写 + `GET .../events` (live + replay) + EventStreamPanel + ApprovalCard Monaco UX + useStatusPolling + TraceToolbar(Langfuse 外链)+ ApprovalPendingBadge(sidebar 红点)。**2026-05-26 完成(6 PR 链)**:接 B.6/B.7 + E.5 + J.8。零债 6 条 ✅(见 [STREAM-H-DESIGN § 6.5.18](./streams/STREAM-H-DESIGN.md#6518-h3-收尾摘要-2026-05-26))。**(a) 显式推迟**:(1) Trace 嵌入式时间线 → H.4(Mini-ADR H-8 only ships external link);(2) approval 完整 E2E → M0 dogfood(决议 F);(3) RunsList mockup 09 + ApprovalCard 编辑态截图 → H.4 收尾 PR
- [x] **H.4 治理面（Memory / Curation+Eval / Skills / Triggers / Settings IAM+Ops / Audit）** #296 #297 #298 #299 #300 #301 #302 #303 #304 + 收尾 PR — 跨 agent / 跨 user 治理视图。复刻 H.3 范式:PR 0 = 纯设计文档 § 6.6(18 子章节)+ 6 张 mockup,合入后 8 个实施 PR + 1 个收尾。**2026-05-26 完成(10 PR 链)**:接 K.6 / J.12 / J.7 / J.10 / C/D/F/G;Audit endpoint(`GET /v1/audit`)在 H.4 PR 3 新建。零债 6 条 ✅(见 [STREAM-H-DESIGN § 6.6.18](./streams/STREAM-H-DESIGN.md#6618-h4-收尾摘要-2026-05-26))。实施期顺手修了 3 个 latent SDK envelope-vs-raw bug(Curation / Skills / Triggers,H.1b PR 3 遗留;教训记入 [feedback_envelope_vs_raw_contract_check])。**(a) 显式推迟**:(1) Skills marketplace → M1;(2) Memory retention sweep UI → M1;(3) Audit CSV 导出 → M1;(4) TenantConfig ETag 并发(M0 last-writer-wins)→ M1;(5) Trigger webhook secret rotate(M0 走删-重建)→ M1;(6) Trace 嵌入式时间线(Mini-ADR H-8 外链只是第一步)→ M1+;(7) Audit `actor_id` JOIN 用户名 → M1
> **Stream H 整体收官 2026-05-26** — H.1a/H.1b/H.2/H.3/H.4 全部 ✅。Admin UI 工程闭环,Stream H 归档。
- [x] **H.5 docker-compose dev.yml 单机一键启** — 已经在 I.1 `--profile full` 落地

> **依赖**：H.1b+ 任何代码 PR 上线**前提 = Stream N 合入**（系统管理员跨租户能力)。H.1a 设计基线 PR 与 H.1b UI scaffold 可与 Stream N 并行开工。

**Stream H Verification**：H.1a 设计基线 PR 先于 H.1b+ 任何代码 PR 合入；每个子项有产品级体验（响应式 ≥1280px / 键盘可达 / a11y axe 0 critical / 性能 首屏 < 2s, Lighthouse ≥ 90）；UI 集成测试覆盖 happy path；接入的 B/E/J/K 能力面在 UI 上端到端可见；**system_admin 跨租户视角端到端验证（登录默认 "All tenants" → 切到具体 tenant → 切回 → 所有 audit 留痕)**。

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

### Stream N — Cross-tenant Platform Admin（~7-9 天；与 Stream H 并行；H 上线前提）— ✅ 完成 2026-05-25

参考：[streams/STREAM-N-DESIGN](./streams/STREAM-N-DESIGN.md)（设计先行）

> **2026-05-25 用户提出**：平台需要两类管理员 —— **系统管理员（平台域）** 看所有租户的所有数据 + 全操作权限；**租户管理员（租户域）** 仅自己租户内。现有 `Role = ADMIN/OPERATOR/VIEWER` 全部租户内，跨租户机制仅服务于 mTLS 服务主体。本 Stream 补齐"人类系统管理员"这条线。
>
> 锁 4 条决策（[memory:cross-tenant-admin](../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/project_stream_n_cross_tenant_admin.md)）：(1) `Role.SYSTEM_ADMIN` 独立 enum + `role_binding.platform_scope: bool` 字段；(2) 默认"All tenants"聚合视图 + 可切单 tenant；(3) 全面覆盖 ~14 个 list API；(4) 独立 Stream N，与 Stream H 并行。

- [x] **N.1 数据层** — migration `0035_role_binding_platform_scope`（加 `platform_scope` 列 + `tenant_id` 改 nullable + CHECK constraint + 局部 UNIQUE 索引）+ ORM `RoleBindingRow.platform_scope` + DTO + `Role.SYSTEM_ADMIN` enum (PR #266)
- [x] **N.2 Principal + Auth** — `Principal.is_system_admin: bool` + `allowed_tenants` 支持 `Literal["*"]`；`AuthMiddleware` verify 后 `resolve_system_admin(principal, role_binding_store)` 查 platform_scope binding 命中 → 升级 Principal (PR #267)
- [x] **N.3 跨租户 RLS 接入点 + audit** — `services/control-plane/src/control_plane/tenant_scope.py` `ensure_tenant_scope(principal, requested_tenant_id, audit)` + `bypass_rls_session()` + `applied_scope()` 上下文 + `AuditAction.SYSTEM_CROSS_TENANT_QUERY` / `SYSTEM_TENANT_SWITCH` (PR #268)
- [x] **N.4 list API 批量接入 `tenant_id`** — 11 个独立 list endpoint(agents/skills/triggers/curation/eval-datasets — PR #269;service_accounts/role_bindings/sessions/memory/artifacts/api_keys — PR #270)+ 每个 response 带 `cross_tenant: bool` 标识 + 6 store ABCs × 3 impls 新增 `list_all_tenants` + 38 集成测试矩阵全过
- [x] **N.5 role_binding API + 跨租户绑定管理** — `POST /v1/role_bindings` 接 `platform_scope: bool`（仅 system_admin 可创建,DTO validator 强制 `role=SYSTEM_ADMIN ⇔ platform_scope=true` 一致性）+ `GET ?platform_scope=true` filter（仅 system_admin 可查）+ 5 集成测试 + 本 PR
- [x] **N.6 eval + 收尾** — `tools/eval/platform_admin.py` 8 个确定性场景（tenant_admin × {home/none/other/star} + system_admin × {home/none/other/star} 全矩阵）+ `tools/eval/test_platform_admin.py` 4 单测全过 + STREAM-N-DESIGN.md 修订记录 + 零债 6 条核验通过(本 PR)

**Stream N Verification**：所有 list API `tenant_id="*"` 仅 system_admin 可用且必自动落 audit；migration `0035` rollback 双向通过；CHECK constraint 在 DB 层强制 `(platform_scope, tenant_id, role)` 三元组一致性；现有 tenant_admin 不被无意提权；H.1b 依赖的 `useAuth() → { isSystemAdmin, currentTenantScope }` 数据契约清晰可消费。

### Stream K — Capability Hardening Sprint（临时；先于 Stream J 剩余子项）

参考：[streams/STREAM-K-DESIGN](./streams/STREAM-K-DESIGN.md)（设计先行）

> 2026-05-20 用户用"功能可少，能力不可弱"原则（[memory:complete-not-minimal](../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_complete_not_minimal.md)）回头审已交付 Stream，发现 13 条 (c) 类弱版 —— 已声明 `[x]` 完成的功能在失败模式 / 可观测 / 运维路径 / 正确性某一维上未达生产强度。本 Stream 统一补强，**之后才进** Stream J 剩余子项。
> 三条 agent 误判更正记 STREAM-K-DESIGN § 1.2：C.3 API Key 已有 CRUD（仅缺 rotation）、F.6 KMS 已实装（仅缺轮换演练）、J.11 路由是编译期绑定（已生效）。

**P0 — M0→M1 Gate 阻塞**
- [x] **K1 API Key rotation**（补 G1）— `POST /v1/api_keys/{id}/rotate` + 双活窗口（grace 0–3600s，默认 300）+ `AuditAction.API_KEY_ROTATE`；STREAM-K-DESIGN § 3 / Mini-ADR K-1。迁移 0023 加 `api_key.rotated_at` / `grace_period_s`；verifier 在 grace 后视为不可用。**同 PR 修了一个 Stream C.3 的预存设计 bug**：原 prefix `aforge_pat_<5hex>_` (17 chars) 同 tenant 必撞 UNIQUE，扩到 25 chars 含 8 chars random hex 让 per-key unique（K1 rotate 等价 "create 第二个 key" 才暴露此 bug）。6 个集成测试覆盖 happy / grace 内双活 / grace=0 旧立死 / 已 rotated 拒绝 / 已 revoked 拒绝 / 不存在 404。
- [x] **K2 SSE 跨租户隔离**（补 G3）— 安全模型由 thread 归属校验保证（误判更正），补 `test_runs_cross_tenant_sse_rejected` 锁住 invariant + `runs.py:191` docstring 引用 Mini-ADR K-2 说明不加 SSE 层冗余 guard
- [x] **K3 retention CI xfail 收尾**（补 G4）— 两条 `permission denied` 测试 → 绿，xfail marker 移除；同时关 M1-B 挂账项。根因不是 PG 配 / asyncpg role 交互，是测试断言用 `SET LOCAL ROLE audit_writer` 越权读 event_log / jwt_blacklist（audit_writer 仅 audit_log 权限），且 event_log RLS 未设 `app.tenant_id` GUC 把数据全过滤。修法：去 SET ROLE，event_log 断言加 `set_config('app.tenant_id', tenant, true)`。job 代码不动。本地 5/5 通过 ×2。
- [x] **K4 LLM cache 正确性**（补 G11）— `AgentSpecBody.cache.enabled` manifest 入口 + `middleware_assembly` 在 `enabled is False` 时跳过 lookup/store 中间件；Mini-ADR K-3。默认 `enabled=True` 保护现有 manifest。`test_cache_middlewares_skipped_when_manifest_disables` 红→绿。
- [x] **K5 gVisor Gate Exit Criteria**（补 G12）— M0→M1 Gate Exit Criteria 显式列入 7/7 用例 staging Linux 必跑通条款（纯文档；同 PR 在 STREAM-F-DESIGN § 1.3 + § "测试矩阵" 加 K5 锁定引用，让 Stream F 自身文档不再读起来像"软推迟豁免"）

**P1 — 阻塞 Stream J 剩余子项**
- [x] **K6 memory CRUD**（补 G2a）— `GET /v1/memory` + `PATCH/DELETE /v1/memory/{id}` + 迁移 0024 `deleted_at` 列 + partial 索引；Mini-ADR K-4 soft-delete。每端点强制 `caller_user_id`（machine principal 403）、跨 user 404 隐藏存在；PATCH 重新 embed 否则 503；DELETE 幂等。新增 `AuditAction.MEMORY_UPDATE` / `MEMORY_FORGET` + `Resource = "memory"` 三种 role 都有自己 memory 权限。12 个端到端测试覆盖。（迁移号从设计稿 0021 调整为 0024，因 0023 是 K1 的 api_key rotation。）
- [x] **K7 memory writeback 重试 + dedup**（补 G2b）— 迁移 0025 加 `memory_item.content_hash CHAR(64)` + PG-side backfill via pgcrypto + 部分 UNIQUE 索引（`tenant, user, content_hash WHERE deleted_at IS NULL`）+ 新表 `memory_writeback_dlq`；`MemoryStore.write` 用 ON CONFLICT DO NOTHING dedup；`memory_writeback` 节点失败入 DLQ；`control_plane.memory.MemoryDLQWorker` 作为 lifespan background task 按 1m→5m→30m→2h→6h backoff 重试，5 次失败 → 死信。20 个测试覆盖（worker 7 + writeback enqueue 1 + dedup 1 + 既有 11）。Mini-ADR K-5。（迁移号从设计稿 0022 调整为 0025。）
- [x] **K8 `update_plan` 工具**（补 G8）— `plan_execute` workflow 隐式注册 `UpdatePlanTool` 让 agent 在运行中改 `AgentState.plan`；闭环 J.1 重规划路径。机制：扩 `ToolResult.state_updates`（白名单 `TOOL_ALLOWED_STATE_KEYS = {"plan"}`）+ `tools_node` 把 update 提升到 graph state；`ToolContext.plan` 注入当前 plan 让 tool 复用原 `goal`。`_dispatch_tool` / `_invoke_tool` 改成 `(ToolMessage, state_updates)` tuple 返回。7 个单测 + 1 个 e2e 测试覆盖。
- [x] **K9 reflect wall-clock 超时**（补 G9）— `ReflectionSpec.deadline_s` 默认 30s（gt=0, le=600）；`reflect_node` 用 `asyncio.wait_for(token.run_cancellable(...), timeout=deadline_s)`；超时降级为 accept + critique="reflection timed out after Ns"。与 cancellation_token 正交（cancel 走客户端断开路径）。2 个新单测：`test_reflect_node_wallclock_timeout_falls_back_to_accept` + `test_reflect_node_returns_normally_within_deadline`

**P2 — 阻塞 Gate 前真生产 release**
- [x] **K10 G.7 大盘真闭环**（补 G10）— orchestrator SSE worker emit `helix_session_ttft_seconds` 在首 chunk + `helix_durable_resume_seconds`（仅 `RunRecord.is_resume=True` 时），sandbox-supervisor.acquire 在 launch 路径 emit `helix_sandbox_cold_start_seconds`；`tools/observability/rules/sli.yml` 加三条 recording rule；02-orchestrator / 03-sandbox 大盘 panel expr 改用 recording rule + 加 durable_resume panel + 去 "Scaffold" 标记。control-plane runs.py 用 `run_manager.list_by_thread` 判 `is_resume`。3 个新单测。
- [x] **K11 加权金丝雀**（补 G13）— `deploy.py` 已经有 `--canary 10,30,50` 渐进 + nginx `weight=` upstream（Stream I.2 落地），本 PR 补**失败自动回滚**：加 `--soak-check-cmd` 在每 canary step 末跑外部健康探针，非 0 退出 → 把 upstream 写回 100% live + 抛 `CanaryAbortedError` + exit 1。新增 3 个单测覆盖 abort+rollback / 全过完成 flip / 探针 raise 视为失败。`_subprocess_soak_checker` 把 shell 命令包成 `SoakChecker` 回调；callable injection 让单测无需 docker。

**P3 — 阻塞 M1 入口**
- [x] **K12 memory recall eval gate**（补 G2c）— 新 harness `tools/eval/memory_recall.py`（schema + recall@k / mrr@k metric + 异步 runner，embedder-agnostic）+ `tools/eval/datasets/memory_recall/zh_en_seed.yaml` 8 case seed（4 zh + 4 en）+ 9 单测覆盖 metric / loader / runner。slo.md 加 SLO #6（M1 目标 recall@5 ≥ 0.7 against real embedder；M0 在 fake keyword-overlap embedder 上锁 1.0 防 harness 回归）。注：100 case 完整 zh+en benchmark 留到 canonical agent dogfood，K12 提供基础设施 + 验证铺底。
- [x] **K13 KMS 轮换演练**（补 G5）— `FakeKmsBackend` 已支持 reseed（new value/version），加 3 个轮换 drill 测试：rotation 后 cache TTL 过期内仍返旧值（设计预期 stale-by-TTL）→ 过期后取到新值；`put()` 路径立即 invalidate；dynamic kind 用半 TTL 提前收敛。覆盖 \"轮换后 60s 内取新值 < TTL\" 的核心 invariant。
- [x] **K14 WORM 恢复演练**（补 G6）— `tools/persistence/restore_audit.py` 工具 + `docs/runbooks/audit-restore.md` runbook（pre-flight / sibling table / 调 restore tool / 验证 / promote swap-vs-read-across）+ 3 drill 测试（round-trip integrity / per-tenant 隔离 / 损坏对象 tolerance）。InMemoryObjectStore 上端到端验证序列化和 restore 路径，回归会在 CI 红。
- [x] **K15 PG 恢复演练**（补 G7）— 定位现状（docs/dr/RUNBOOK.md 已有完整 dev/prod restore procedure + RPO/RTO M0 目标 24h/4h）+ 新增 `docs/runbooks/pg-restore.md` 入口（指向 dr/RUNBOOK + K15 自动化 drill 说明）+ `tools/persistence/test_pg_restore_drill.py` testcontainers 集成测试（pg_dump → drop → pg_restore round-trip + 行数 + body 完整性 + RTO ceiling < 60s）。CI `Test (integration)` 每次跑 drill，schema 回归不会等到季度 DR 演练才发现。

**Stream K Verification ✅ 已完成（2026-05-20）**：13 条 gap（K1–K15，K5 是纯文档锁，所以代码层 14 条 PR）全部合入 main，零债 6 条核验：
1. **代码干净** ✅ — 新增代码无 `TODO`/`FIXME`/`XXX`/`HACK`（docs 内对零债规则本身的引用不计）
2. **测试达标** ✅ — K1=6 / K2=1 / K3=2 fix / K4=1 / K6=12 / K7=20 / K8=8 / K9=2 / K10=3 / K11=3 / K12=9 / K13=3 / K14=3 / K15=1 integration；无新 xfail / skip
3. **文档同步** ✅ — STREAM-K-DESIGN § 1.1 + § 3 + Mini-ADR 与实现一致；迁移号偏离（0021/0022 → 0024/0025）每处都有 inline 注释解释
4. **可观测齐全** ✅ — K7 DLQ worker emit 3 个 prom counter（cycle_errors / dead_letters / retries_succeeded）；K10 emit 3 个 histogram；新建 audit action 入审计流；新建 endpoint emit OTel span 经现有 ObservabilityMiddleware 自动覆盖
5. **CI 全绿** ✅ — 14 个 PR 合并时全部 8/8（Lint / mypy / unit / integration / pre-commit / pip-audit / CodeQL Analyze / CodeQL）；CodeQL 无新增 high/critical（合并途中处理过 2 处 warning：K1 result-未初始化 / K12 ellipsis-no-effect）
6. **bug 不遗留** ✅ — K3 中调查的 "asyncpg quirk" 改正诊断；M1-B retention-cleanup-job 挂账已关；同 PR 修了 1 个 Stream C.3 prefix UNIQUE 预存设计 bug（K1 暴露）

PR 链（main 上 14 个 squash commits + 1 docs PR）：#172（设计）→ #182 K3 → #183 K5 → #184 K2 → #185 K4 → #186 K1 → #187 K9 → #188 K8 → #189 K6 → #190 K7 → #191 K10 → #192 K11 → #193 K13 → #194 K12 → #195 K14 → #196 K15。Stream J 剩余子项现可开始。

### Stream L — Hermes-derived 单 turn 能力强化 sprint（临时；与 Stream J 剩余子项并行）

参考：[streams/STREAM-L-DESIGN](./streams/STREAM-L-DESIGN.md)（设计先行）

> 2026-05-20 Stream K 收尾后做了一次跨仓 architecture review：把 Hermes-Agent（`/Users/mac/src/github/hermes-agent`）的 `run_agent.py` + `agent/conversation_loop.py`（各 4000+ 行）与我们 `services/orchestrator/`（LangGraph 因子化）做对比。形态分歧不重要 —— 真正学得到的是 Hermes 在**单 turn 之内**积累的 8 条生产能力，每条都是我们完全缺失但任何长 session / per-user 持久 agent（[memory:target-product-form](../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/project_target_product_form.md)）必备的。按 [memory:complete-not-minimal](../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_complete_not_minimal.md) 红线属 (c) 类弱版必补。
> 与 Stream K 的关系：K 补"已勾完 stream 的弱版"，L 补"agent loop 单 turn 内的成熟度"；两者都属于 (c) 类弱版红线。

**P0 — 阻塞 M0→M1 Gate 第一次真生产 release**
- [x] **L1 Anthropic prompt caching** — `AnthropicProvider.cache_enabled: bool = True`（默认开），`ModelSpec.cache_enabled: bool = True` 经 `agent_factory._build_provider` 传入。`_apply_cache_control` 把 `system` 升级成 block list 加 `cache_control={"type":"ephemeral"}` + 末尾 3 个非 system message 末 block 加 marker（Hermes "system_and_3" layout，max 4 breakpoint）。`_inject_plan` / `_inject_memories` 从 `_merge_into_system`（系统消息合并）改为 `_append_tail_human_message`（HumanMessage 尾部追加），守 Mini-ADR L-1 不变式：leading SystemMessage 跨 turn 字节稳定。`_from_anthropic_response` 解析 `usage.cache_creation_input_tokens` / `cache_read_input_tokens` 进 `AIMessage.usage_metadata.input_token_details` 给 langfuse 观测缓存命中。`AnthropicClient.messages` `system` 参数类型放宽 `str | list[dict] | None`。11 个新单测覆盖 cache-control 注入 / opt-out 路径 / 无 system / 已 block 现场 marker / usage 字段；4 个 byte-stable 集成测试用 SHA-256 跨 turn 比对（plan / memory / mutation advisory 三种动态注入都验过 + 历史增长不变）。`test_planner` / `test_memory_nodes` 等下游测试同步从 SystemMessage 期望换到 HumanMessage 期望。STREAM-L-DESIGN § 3.L1 / Mini-ADR L-1。
- [x] **L2 Token preflight + context compressor** — 新 `orchestrator.context` 包（`ContextCompressor` + `ContextOverflowError` + `estimate_tokens`）实现 Hermes summarise-the-middle 模式：`agent_node` 入口检查 `estimate_tokens(messages) >= context_window * threshold_pct`，超过则压缩；保 head/tail 各 N 条非 system message 不动，中间用单独 summariser LLM call 折成 `<context-summary>` SystemMessage 落在 head 与 tail 之间。`ModelSpec.context_window: int = 200_000`（默认 Claude 长 context）；`PolicySpec.context_compression` 从 permissive dict 升级到 typed `ContextCompressionPolicy`（enabled/threshold_pct/head_keep/tail_keep/max_passes + legacy max_turns/max_tokens 保留给 E.3 `DynamicContextMiddleware`）。`agent_factory.build_agent` 当 `cc_policy.enabled` 时构造 compressor 注入 `build_react_graph`。Mini-ADR L-2：one-shot per pass / 独立 summariser LLM caller / `max_passes` 后抛 `ContextOverflowError`（无静默 fallback）/ rough char/4 estimator（与 Hermes 同档）/ summary 落 SystemMessage 不挡 L4 HumanMessage tail。15 个 unit（estimate / threshold / preserve head+tail / leading SystemMessage byte-stable / 无 middle overflow / max_passes 上限 / summariser raise → overflow / Tool/AIMessage 混合 tail）+ 7 个集成测试（preflight 触发 / 跳过 / 无 compressor opt-out / overflow 抛 ContextOverflowError / L1 leading system 守约 / ToolMessage 在 middle / summary 是 SystemMessage 不是 HumanMessage）。STREAM-L-DESIGN § 3.L2 / Mini-ADR L-2。
- [x] **L3 Stream stale-detection** — `LLMRouter._call_one` 套 `_invoke_with_deadline(handle, coro)` helper：每 provider `complete()` 内 `asyncio.wait_for(coro, timeout=stream_deadline_s)`，超时 raise `LLMStreamStaleError`（继承 `LLMServerError` 走 retryable → router 自动 fallback 到下一 provider）。`AgentSpecBody.stream_deadline_s: int = 90`（0 = 关闭，上限 3600）经 `build_step_routers` / `build_llm_router` / J.6 vision router 统一注入。`helix_llm_stream_stale_total{provider_key}` counter +1 in stale path。STREAM-L-DESIGN § 3.L3 / Mini-ADR L-3。6 个新单测：trigger / fallback / 0=disable (None + int) / fast path / counter emit。实装走 per-provider 语义（不在 agent_node 外层 wrap）—— Mini-ADR L-3 写明 "套 complete()" 即 provider 级，agent_node 外层会让 hung primary 吃掉 fallback 预算。
- [x] **L4 File-mutation verifier footer** — 新 `orchestrator.tools.mutation_classifier` 模块（M0 set 仅 `save_artifact`，未来 mutation tool 扩展；不为不存在的 tool 写 stub，按 Mini-ADR L-4）。`AgentState.failed_mutations: NotRequired[list[MutationOutcome]]` narrow channel。`tools_node` 每次 dispatch 后调 classifier，把未 land 的 outcome 累加并写回 state（仅在非空时写以保 happy path 快速）。`agent_node` 进入时若 `failed_mutations` 非空 → `_build_mutation_advisory` 构造 `<mutation-advisory>` HumanMessage 注入 LLM prompt + 持久化进 state messages + 清空 channel。advisory 走 HumanMessage 不进 SystemMessage（守 L-1 不变式）。16 个新测试（classifier 8 + advisory 集成 8）覆盖 land/no-land / 多 mutation 聚合 / 单 fail + 单 success 部分 / 仅 mutation tool 入 advisory / 重复防注入 / SystemMessage 隔离守约。`test_state.py` 加 `failed_mutations` 到 required-keys set。STREAM-L-DESIGN § 3.L4 / Mini-ADR L-4。
- [x] **L5 Iteration budget refund** — `ToolResult.refund_iterations: int = 0`（`__post_init__` 拒绝 negative）+ `AgentState.step_count_refund_pending` narrow channel；`tools_node` 跨 batch 累加 refund，`agent_node` 进入时 `effective = max(0, step_count - refund_pending)` 再做 max_steps 校验，返回时写 `step_count_refund_pending=0` 重置；`_dispatch_tool` / `_invoke_tool` 返回类型从 `tuple[ToolMessage, Mapping]` 扩到 `tuple[ToolMessage, Mapping, int]`。`UpdatePlanTool` 返回 `refund_iterations=1` 闭环 K.8 J.1 重规划路径 —— plan 修订不消耗用户预算。11 个新测试覆盖：rejects negative / accepts zero+positive / single refund / batch accumulation / clamp at 0 / saves from max_steps cap / resets after consumption / update_plan refund=1 / e2e update_plan 不增加 step_count / tool error doesn't refund。`test_state.py` 跟着加 `step_count_refund_pending` 到 required-keys set。STREAM-L-DESIGN § 3.L5 / Mini-ADR L-5。
- [x] **L6 Adaptive tool parallelization** — `ToolSpec` 加 `is_read_only: bool = False` + `path_args: tuple[str, ...] = ()`；新 `orchestrator.tools.scheduling` 模块（`plan_stages` 贪心分阶段 + `conflicts` 规则）+ `tools_node` 重写：每阶段 `asyncio.gather` + `asyncio.Semaphore(MAX_TOOL_WORKERS=8)` 限并发，阶段间顺序。冲突规则：两读永不冲突；写+写 / 读+写 路径相交才冲突；空 `path_args` + 非只读 → 保守与所有冲突（覆盖 `update_plan` / `subagent` 等写共享态）。Builtin tools 标注：`web_search` / `ask_image` / `knowledge_search` / `list_artifacts` → read_only=True；`save_artifact` → path_args=("name",)；`http` 保守默认（POST/PUT/PATCH/DELETE 实际允许，与初稿"M0 只 GET"假设不符）；`mcp:*` / `subagent` / `update_plan` / `exec_python` 默认。`_dispatch_tool` 输入类型 `Mapping → dict` 收紧（mypy）。结果按原 tool_call 顺序回填 + L5 refund 累加 + K8 state_updates 仍按原顺序"后写胜出"。监测 `helix_tools_stages_total` + `helix_tools_dispatched_total` counter pair（不走 histogram —— validator 要求 `_seconds` 后缀）。21 个 scheduling 单测 + 8 个 react graph 集成测试（实测 wall-clock 证明并行 / 同 path 顺序 / order 保持 / counter）。STREAM-L-DESIGN § 3.L6 / Mini-ADR L-6。
- [x] **L7 Trajectory recording** — 新 `orchestrator.trajectory` 包：`TrajectoryRecord` + `TrajectoryRecorder` 写 ObjectStore `trajectories/{tenant_id}/{outcome}/{YYYY}/{MM}/{DD}/{thread_id}.jsonl`；ShareGPT 序列化（4 角色 + `tool_calls`/`tool_call_id` 完整携带 + content block list 扁平化）；4 outcome 分流（success / failed / max_steps / cancelled）；`MaxStepsExceededError` 在 sse.py:run_agent 单独 catch，区别于通用 failed。`sse.py` 加 `trajectory_recorder: TrajectoryRecorder | None = None` 参数，每个终态 path `_dispatch_trajectory` 通过 `asyncio.create_task` + 5s outer deadline fire-and-forget；recorder 自身 swallow 所有错误 + 三档 error counter（invalid_record / store_error / unexpected）。`StreamableGraph` Protocol 扩 `aget_state` 用于取终态 messages。`PoliciesSpec.trajectory_recording: bool = True` manifest opt-out。`helix_trajectory_recorded_total{outcome}` + `helix_trajectory_record_errors_total{outcome,reason}` counter。21 个 recorder 单测 + 7 个 sse 集成测试覆盖：serialise / key 布局 / 4 outcome / 失败 swallow / counter / 慢 recorder 不阻塞 / aget_state 失败仍 emit envelope。STREAM-L-DESIGN § 3.L7 / Mini-ADR L-7。
- [x] **L8 OAuth 401 自动 refresh + 重试一次** — 新 `OAuthCapableProvider` Protocol（`services/orchestrator/src/orchestrator/llm/oauth_provider.py`）+ `LLMRouter._handle_unauthorized` 在 `LLMUnauthorizedError` 后 `isinstance(provider, OAuthCapableProvider)` 才 refresh + 重试 ≤ 1 次；refresh `False` / 重试再 401 → `LLMAuthError`（继承 `LLMServerError` retryable）走 fallback chain。Anthropic/OpenAI adapter 现在 401 路径 raise `LLMUnauthorizedError`（`LLMClientError` 子类，4xx 语义不变）让 OAuth-capable 走分支；non-OAuth provider 401 仍 propagate 不 fallback。`helix_llm_auth_refresh_total{provider_key, result=success|fail}` counter +1。10 个新单测（Protocol 运行时检查 / 401-then-success / 持续 401 / refresh=False / refresh raise / 非 OAuth pass-through / `LLMUnauthorizedError ⊂ LLMClientError` / counter ×2）。STREAM-L-DESIGN § 3.L8 / Mini-ADR L-8。

**Stream L Verification ✅ 已完成（2026-05-20）**：8 条 gap（L1–L8，全部代码层）合入 main，零债 6 条核验：

1. **代码干净** ✅ — 新增代码无 `TODO`/`FIXME`/`XXX`/`HACK`（已在每条 PR + 收尾 PR 扫过）
2. **测试达标** ✅ — 新增测试: L1=11+4 / L2=15+7 / L3=6 / L4=8+8 / L5=11 / L6=21+8 / L7=21+7 / L8=10 ≈ 137 个新测试。Stream L 完成后 orchestrator 套件 546 passed（pre-L 477 → +69 净增；其余更新自现有测试受 L1/L2 invariant 迁移影响而调整）。无新 xfail / skip。
3. **文档同步** ✅ — `STREAM-L-DESIGN.md` § 3 各小节按实装回炉（L1 类层级修订 / L3 placement 更正 per-provider / L6 builtin 标注表纠正 http GET-only 假设 / L7 § 3.L7 已对齐 / 其余无偏）。Mini-ADR L-1 ~ L-8 与实现一致；anthropic.py module docstring 移除"Cache control 推迟"过期语。
4. **可观测齐全** ✅ — 6 个新 Prom counter：`helix_llm_stream_stale_total{provider_key}` (L3) / `helix_llm_auth_refresh_total{provider_key,result}` (L8) / `helix_tools_stages_total` + `helix_tools_dispatched_total` (L6 pair) / `helix_trajectory_recorded_total{outcome}` + `helix_trajectory_record_errors_total{outcome,reason}` (L7)。Anthropic L1 cache token counters 经 `AIMessage.usage_metadata.input_token_details` 进 langfuse 观测。
5. **CI 全绿** ✅ — 9 个 PR 合并时全部 8/8（Lint / mypy / unit / integration / pre-commit / pip-audit / CodeQL Analyze / CodeQL）；CodeQL 无新增 high/critical（合并途中处理 1 处：L4 implicit-string-concat）；CI 工具链 ruff 漂移修过 3 轮（L4 / L7 / L2 的 RUF003 × ambiguous mult-sign，全部 `×` → `x`）。
6. **bug 不遗留** ✅ — L4 暴露 LangChain `add_messages` reducer 给消息分配新 id（test_sse_trajectory 测试断言 `==` 改为 content compare）；pip-audit 上 `pyjwt 2.12.1` 的 `PYSEC-2025-183` 无 upstream fix → CI workflow 加 `--ignore-vuln PYSEC-2025-183`（与 L5 同 PR 顺手解锁所有后续 PR）；L1 byte-stable 不变式与 L4 advisory / L2 summary 都验过共存（test_system_byte_stable 4 个集成测试）。

PR 链（main 上 9 个 squash commits）：#198（设计 L0）→ #199 L3 → #200 L5 → #201 L8 → #202 L7 → #203 L6 → #204 L4 → #205 L1 → #206 L2 → 收尾 PR。Stream J 剩余子项 (J.4 / J.5 / J.7 / J.8 / J.9 / J.10 / J.12 / J.13 / J.15) 与 L 完全独立可继续推进。

### Stream J — Agent Harness 能力补全（大里程碑；canonical agent + dogfood 的前置）

参考：[architecture/08-AGENT-CAPABILITY-ASSESSMENT](./architecture/08-AGENT-CAPABILITY-ASSESSMENT.md)、[streams/STREAM-J-DESIGN](./streams/STREAM-J-DESIGN.md)（设计先行）

> 2026-05-18 的 26 维 agent 能力评估发现：helix M0 把企业基础设施（Stream A–I）做到生产级，但 agent 认知 / harness 层有 14 个缺口。本 Stream 把这 14 个缺口补到生产级 —— helix 才是能支撑目标产品形态的 harness 能力完整平台。
> **目标产品形态 = per-user 持久 agent**（租户=公司、用户=公司的员工/客户；每用户一个持久 agent 实例 = 对话 + 长期记忆 + 持久工作区，空闲释放算力、来消息快速还原）。canonical 能力 agent 即此产品形态本身，**不是另起的验证 agent**；Stream J 验收锚定"端到端支撑该 agent 跑通"。详见 [architecture/08-AGENT-CAPABILITY-ASSESSMENT § 4](./architecture/08-AGENT-CAPABILITY-ASSESSMENT.md)。
> 量级与 M0 若干 Stream 总和相当。每子项设计先行 + TDD + 一 PR 一子任务 + 零技术债。原 M1-D 的 `reflection/resolvers.py`、`uploads_middleware`、subagent executor 及 M1-F 的 Sub-Agent 项提前并入本 Stream。

- [x] **J.1 规划 / 任务分解** — `planner` 图节点:`workflow.type==plan_execute` 时 `START→planner→agent⇄tools`,planner 一次 LLM 调用把任务拆成结构化 `Plan`(进 `AgentState.plan`),agent 每步把计划渲染进 system context。先拆解再执行。运行中重规划(`update_plan`)与 J.2 反思耦合,随 J.2 一并落。STREAM-J-DESIGN § 5。
- [x] **J.2 反思 / 自我修正** — `reflect` 图节点:`reflection:` 块激活,agent 无 tool_call 退出时经 `reflect` 自我批判 —— `accept` 结束 / `revise` 回 agent 带 critique;`budget` 上限封 reflect↔agent 环;unparseable 回复 fail-safe 到 accept。含运行中重规划(`revise` verdict 可带 `revised_steps` 改写 `Plan`)。与 `loop_detection` 中间件正交。STREAM-J-DESIGN § 6。
- [x] **J.3 长期记忆** — 跨会话记忆 store + 检索,接入上下文组装。PR1:`memory_item` 表(pgvector,迁移 0017)+ `MemoryStore` + 用户级 RLS GUC;PR2a:`Embedder`(qwen `text-embedding-v4` 兼容 `/v1/embeddings`);PR2b:`memory_recall`/`memory_writeback` 图终端节点 + `MemoryEnv` 装配;PR2c:control-plane 注入 store/embedder + run config 携带 `user_id`。STREAM-J-DESIGN § 8。
- [x] **J.4 Sub-agent / 多智能体委派** — agent-as-tool + 取消链穿透 + 构建期深度上限 3 × max_iterations + **真正的并行 fan-out**（J.4-补强-2 取消 M2-B 推迟，M0 内交付）。STREAM-J-DESIGN § 11 + Mini-ADR J-12 / J-21 / **J-40 (2026-05-21 新增)**。**已完成 9 PR**：
  - [x] **J.4 PR #151** ChildAgentBuilder protocol + ToolEnv scaffold
  - [x] **J.4 PR #152** SubAgentTool agent-as-tool delegation adapter
  - [x] **J.4 PR #154** ChildAgentBuilder wired into control-plane
  - [x] **J.4-补强 PR #220 (2026-05-21)** Mini-ADR J-21: sub-agent trajectory 单独记录（L7 ObjectStore 单独 key，3 outcome 全 dispatch 含 cancelled）+ budget telemetry 回传父（`iteration_used` / `llm_call_count` / `wall_clock_ms` 写 `ToolResult.meta`）
  - [x] **J.4 eval + J.4-补强 收尾 PR #221 (2026-05-21)** `tools/eval/sub_agent.py` + dataset 8 case + run_baseline J.4 stub 转 PASS（pass_rate=1.00）
  - **J.4-补强-2 拆 4 PR（2026-05-21 完成；用户阅读 DeerFlow 2.0 文章后决策取消 M2-B 推迟）**：
  - [x] **设计 PR #222** — STREAM-J-DESIGN § 11 重写 + Mini-ADR J-40（M0 内交付并行 fan-out + cycle detection + global deadline + fan-in 聚合 + `AgentState` 扩展）
  - [x] **PR 2 基建 #223** — `SubagentStatus` 6 态（PENDING/RUNNING/COMPLETED/FAILED/CANCELLED/TIMED_OUT）+ `SubAgentInvocation` dataclass（helix-protocol）+ `AgentState.subagent_invocations` 通道（`Annotated[list, add]` reducer，参照 `reflections`）+ `TOOL_ALLOWED_STATE_KEYS` 加键 + `SubAgentTool` 3 outcome 各 emit invocation 经 `ToolResult.state_updates`
  - [x] **PR 3 并发 fan-out #224** — `ToolSpec.is_parallel_safe: bool = False` + `SubAgentTool.spec.is_parallel_safe = True` + `plan_stages` 同 stage 收集并发（asyncio.gather + `MAX_TOOL_WORKERS=8` 信号量） + `agent_factory.detect_subagent_cycle` 构建期 DFS（visiting/visited 双集合）+ `ToolContext.deadline_at` + `runs.run_agent` 经 config 传播 `time.monotonic` deadline + 10 个集成测试覆盖 is_parallel_safe spec / plan_stages 3-sibling / deadline 继承 / cycle 检测（2/3 节点 + 不可解 + 菱形）
  - [x] **PR 4 eval + 收尾 (本 PR)** — `tools/eval/sub_agent.py` 扩 `parallel_fanout`（3 case：2/3/5 siblings + wall-clock 并发证）+ `cycle_detection`（1 case：A→B→A AgentFactoryError）+ baseline sample_size 8 → 12 + `_FakeKeywordEmbedder` 改用 `hashlib.blake2b`（修 `hash()` PYTHONHASHSEED 漂；J.3 mrr@5 稳定 0.96875）+ 零债 6 条核验
  - **零债 6 条核验 ✅**：(1) 代码干净（无 TODO/FIXME/XXX；ruff/mypy/pre-commit 全过）/ (2) 测试达标（PR 2/3/4 共 +10 集成 + 2 新 eval + 2 sanity = 14 新单测；orchestrator 571/571 + tools/eval 68/68 + J.4 eval 6/6 全过）/ (3) 文档同步（STREAM-J-DESIGN § 11 + § 19 Mini-ADR J-40 + ITERATION-PLAN J.4 行与代码一致）/ (4) 可观测齐全（`SubAgentInvocation` 6 态 status / budget 字段 / 经 `state_updates` 入 `AgentState.subagent_invocations`；baseline yaml `generated_at` / `helix_commit` 含 provenance）/ (5) CI 全绿（#222 8/8 / #223 8/8 / #224 8/8 / 本 PR 待跑）/ (6) bug 不遗留（顺带修 `_FakeKeywordEmbedder` 的 `hash()` 非确定性 — J.3 mrr@5 不再每跑漂）
  - **(a) 显式推 M1+**：~~控制平面 wiring TrajectoryRecorder 接 runs.py run_agent 调用~~（L7 production 路径未拼，J.4-补强 PR 把 hook 接好；待 L7 production wire-up 一并完成）/ ~~子 SSE 进度流回父~~（M2-B 后续；当前 fan-out 子 result 只在 ToolResult.content + meta，没流式子进度）/ ~~thread_meta 存 sub_thread_id 审计行~~（L7 trajectory 已覆盖审计；可选 J.4-补强-3）
- [x] **J.5 知识 / 检索（RAG）** — 生产级 RAG：pgvector + 全文检索 + RRF + LLM-rerank + 强解析 + 结构 / 语义 / 表格感知切块 + 异步摄取 + 文档运维；`knowledge_search` 工具按需查（不自动注入）。Mini-ADR J-22 拆 4 阶段（数据层 / 解析切块 / 混合检索 / 文档运维）+ SLO baseline 锁定。STREAM-J-DESIGN § 12 + § 12.5 + Mini-ADR J-13 / J-22。**2026-05-21 完成（8 PR 实施 + 1 PR 收尾）**：
  - [x] **J.5a 数据层 + 异步摄取** — `KnowledgeSpec` + 知识 DTO + 迁移 0021/0022 + `KnowledgeStore` ABC + per-KB chunking 配置 + tsvector 全文检索列 + `KnowledgeIngestionRunner` 异步管道 + `knowledge_document.status` 状态机（PRs #155 / #156 / #157 / #162）
  - [x] **J.5b 解析 + 切块** — pymupdf4llm + MarkItDown 兜底 + token 计量切块 + 结构感知 + 表格感知 + 语义切块 + 标题路径前缀（PRs #158 / #159 / #160）
  - [x] **J.5c 混合检索 + rerank** — `KnowledgeRetriever`（vector + 全文 → RRF → LLM-rerank → top-k）+ `knowledge_search` 工具（`knowledge:` 块激活）+ `LLMReranker`（PR #161）
  - [x] **J.5d 文档运维 API** — KB / document CRUD + 重摄取幂等 + 级联删除 + control-plane lifespan wire + `KnowledgeRetriever` 经 `ToolEnv` 注入（PR #162）
  - [x] **J.5 eval + 收尾 PR (本 PR, 2026-05-21)** — `tools/eval/rag.py` + dataset 11 case（4 single-doc / 3 multi-doc 归属 / 2 multi-base / 1 多 KB 多 chunk recall / 1 hybrid 区分 + CJK 中文用例）+ `run_baseline._run_rag()` wire + baseline `J.5_rag` PASS（pass_rate=1.00 / recall_at_k=1.00 / sample=11）+ STREAM-J-DESIGN § 12.5 锁 SLO baseline + 零债 6 条核验
  - **零债 6 条核验 ✅**：(1) 代码干净（无 TODO/FIXME/XXX；ruff/mypy/pre-commit 全过）/ (2) 测试达标（既有 156 个 knowledge 测试 + 本 PR 4 个新 eval 单测）/ (3) 文档同步（STREAM-J-DESIGN § 12.5 + Mini-ADR J-22 + ITERATION-PLAN J.5 行与代码一致）/ (4) 可观测齐全（`knowledge_document.status` / `chunk_count` / `error` 状态机 + SLO 表 5 维量化）/ (5) CI 全绿（#155–#162 各自 8/8 + 本 PR 待跑）/ (6) bug 不遗留（M0 SLO baseline 锁定，非空决策）
- [x] **J.6 多模态输入** — 图像输入能力解析式双路:`ModelSpec.supports_vision: true` 走 content block(主模型直看像素), `supports_vision: false` 走 `ask_image` 工具路由到 `vision:` 块单独声明的 VL 模型;统一经 `POST /v1/sessions/{thread_id}/uploads` 对象存储摄取(`helix://image/...` ref + base64 不进 checkpointer);构建期按 `ModelSpec.supports_vision` 决议、能力不匹配则 422 / `AgentFactoryError`。PR #167–#171 全合(对象存储 wiring → LLM adapter content blocks → upload 端点 → Path A → Path B)。STREAM-J-DESIGN § 13 / Mini-ADR J-14。
  - **2026-05-20 完成补审**：9 维 (c) 红线 6 维 gap → 6 条 Mini-ADR J-30 ~ J-35 + 4 个 (c) 补强子项 + 1 个 (a) 决策项（设计先行已落，实施待开 PR）：
  - [x] **J.6.补强-1** Upload quota 接入 Stream C.5 QuotaService（`image_upload_count_30d` slow-drip bucket + `image_storage_bytes` sticky 字节天花板 + 429 超额含 `Retry-After` + `dimension` 字段）— Mini-ADR J-30。**2026-05-21 完成**：(1) `QuotaDimension` enum 加 2 维 + `CheckRequest.cost_overrides` 让 upload 路径同 check 调用按维度差异化扣费（QPS / count `cost=1`，bytes `cost=file_size`）；(2) `InMemoryQuotaService` / `RedisQuotaService` `_resolve_dimensions` 处理新维（count slow-drip refill = limit / 30d / 86400；bytes refill=0）；(3) `check_admission` 加 `cost` / `cost_overrides` 参数；(4) `uploads.py` 注入 QuotaService + AuditLogger，bytes-validated 后调 `check_admission`，denial 直返 429（与 B.2 同 envelope）；(5) 5 个新单测覆盖 count bucket 耗尽 / bytes override / 不 refill / 端到端 429 双维。零债 6 条 ✅
  - [x] **J.6.补强-2** Upload 单独 audit trail — Mini-ADR J-31。**2026-05-21 完成**：新 `AuditAction.IMAGE_UPLOAD` + `ResourceType` Literal 加 `"image_upload"` + `uploads.py` 端点直接 `audit.emit()` 含 file_size / mime / object_key / sha256 / thread_id / subject_type / auth_method / ext 8 字段；429 denial 路径不 emit IMAGE_UPLOAD（已经由 `check_admission` 写 QUOTA_RATE_LIMIT_DENIED）。2 个新单测覆盖 audit row 字段 + denial-no-emit。零债 6 条 ✅
  - [x] **J.6.补强-3** Image lifecycle — Mini-ADR J-32 拆 2 PR 实施。**2026-05-21 完成**：
    - [x] **3a (PR #229)** `image_upload` 表（迁移 0028）+ ImageUpload DTO + ImageUploadStore ABC + InMemory + SQL + tenant RLS 策略 + uploads.py 入库 + `DELETE /v1/uploads/{id}` 端点（soft-delete + audit `operation=soft_delete`）+ 单测 9 个（5 store + 4 endpoint）
    - [x] **3b (本 PR)** retention-cleanup 加 image pass — `RetentionCleanupJob._delete_expired_images` 找 `created_at < now - image_retention_days` 的行（默认 90 天，env `HELIX_RETENTION_IMAGE_RETENTION_DAYS` 可配）→ ObjectStore.delete(object_key) → ImageUploadStore.hard_delete 行；object-store 失败不阻塞 row 删除（独立计数 + log）；CleanupReport 加 3 个 image 字段。main.py wire 经 `make_object_store` + `SqlImageUploadStore`（仅当 `object_store_backend=s3-compatible` 激活）。4 个新单测。
    - **(a) 推 M1+**：session / user 销毁级联 soft-delete — 当前 control-plane 无 session DELETE / user DELETE 端点（cascade 无 hook 点），随这些端点的 PR 一并加。
  - [x] **J.6.补强-4** Path B VL 模型 fallback chain + EXIF strip + multi-image 集成测试 — Mini-ADR J-33 / J-34。**2026-05-21 完成**：(1) `VisionSpec.fallbacks: list[ModelSpec]` + `build_llm_router(extra_fallbacks=...)` 把 VL 替补链接在 primary 的 E.11 fallback 后面（priority: `primary → primary.fallback... → extra_fallbacks[0]...`）；`agent_factory.build_agent` 拼 `vision.fallbacks` 进 VL router 构建；(2) `control-plane.api._image_sanitize.strip_exif` 用 Pillow 剥 EXIF / PNG text chunks / WebP / GIF metadata（mime 白名单内 4 种），失败 `ImageSanitizeError` → 400；`uploads.py` 在 quota / audit / store 之前剥离，下游计费 + 审计 + 字节落地全用 sanitised payload；(3) 多 image 集成测试 — 3 张不同色 PNG 上传同 thread，verify 3 个对象 key / 3 行 registry / 3 条 audit；(4) `services/control-plane` pillow>=12,<13 deps + 4 个 EXIF strip 单测 + 1 个 VL fallback chain 单测 + 1 个 multi-image 集成测试。零债 6 条 ✅
  - [x] **J.6.决策-5** NSFW / 恶意 SVG 扫描 + 图像 PII redaction 显式 (a) 推 M2（不留空决策）— Mini-ADR J-35（已落入 § 1.2 Out-of-scope 表）
- [x] **J.7a Skill 静态启用（M0）** — skill = prompt 片段 + tools 子集（**不含 code 字段**）；静态启用 + 版本化 + draft 闸门 + 调用 telemetry + 冲突合并语义 + admin CRUD API + `.skill` ZIP import/export + `name@version` 版本固定 + prompt `<skill>` XML 包裹（防 prompt injection）+ regex deny-list moderation + Skill 元数据扩字段 (description / category / required_models) + discovery 分页 filter + `AuditAction.SKILL_*`。Mini-ADR J-23（含 2026-05-21 补强修订）。STREAM-J-DESIGN § 15 + § 15.5 + § 15.6。J.7b 8 项推迟项详见 § M1-K Agent skill 进化。**2026-05-21 完成（5 PR 链）**：
  - [x] **设计 PR #232** — STREAM-J-DESIGN § 15 重写 + § 15.5 admin API + § 15.6 安全章节 + Mini-ADR J-23 修订 + ITERATION-PLAN M1-K backlog（8 项 J.7b 推迟显式落）
  - [x] **Step 1 — 协议 + 持久化 PR #233** — `protocol/skill.py` (Skill/SkillVersion/SkillRef/SkillStatus/SKILL_REF_PATTERN/parse_skill_ref) + `AgentSpec.skills` 字段 + `_check_skills` validator + 迁移 0029 + ORM (SkillRow / SkillVersionRow) + SkillStore ABC + InMemory + SQL + 39 单测
  - [x] **Step 2 — orchestrator 整合 PR #234** — agent_factory `_load_skills` + `<skill>` XML 包裹 + tool conflict reject + required_models 校验 + 5 个新 AgentFactoryError 子类 + ToolSpec.from_skill 字段 + 11 个集成测试
  - [x] **Step 3 — admin API PR #235** — 9 个 endpoint（CRUD + ZIP import/export + status patch）+ regex deny-list moderation + ZIP slip 防护 + size cap + audit SKILL_CREATE/SKILL_VERSION_CREATE/SKILL_STATUS_CHANGE + 15 个 e2e 测试
  - [x] **Step 4 — eval + 收尾 (本 PR)** — `tools/eval/skill.py` + 12 case dataset (3 resolve / 5 error / 2 moderation / 2 zip) + run_baseline wire + baseline `J.7_skill` PASS (pass_rate=1.00 / sample=12) + 3 个新单测 + ITERATION-PLAN J.7a [ ] → [x]
  - **零债 6 条核验 ✅**：(1) 代码干净（无 TODO/FIXME/XXX；ruff/mypy/pre-commit 全过）/ (2) 测试达标（共 +80 个新测试：39 protocol-persistence + 11 orchestrator + 15 control-plane API + 3 eval + 12 dataset case）/ (3) 文档同步（STREAM-J-DESIGN § 15 + § 15.5 + § 15.6 + Mini-ADR J-23 修订 + ITERATION-PLAN J.7a + M1-K backlog 一致）/ (4) 可观测齐全（3 个新 AuditAction + ResourceType "skill" + ToolSpec.from_skill 标签框架就绪）/ (5) CI 全绿（#232-#235 各自 8/8）/ (6) bug 不遗留（首轮 framing 误判 + 8 项推迟 backlog 全显式落，无空决策）
  - **(a) 推 M1-K**：8 项 J.7b 推迟见 § M1-K Agent skill 进化（agent author/refine tool / code 字段执行 / progressive loading / LLM moderation / public 内置库 / supporting files / per-agent 启停细化 / UI 元数据）
- [x] **J.8 人在回路 / 审批** — control-plane resume 端点 + `PolicySpec` 声明式门控 + agent 主动 `ask_for_approval` 工具。**2026-05-20 修订（Mini-ADR J-24）+ 2026-05-22 设计修订**：M0 含**审批超时 fallback**（manifest 可配 / 默认 24h 自动 reject）+ audit trail（APPROVAL_REQUESTED / APPROVAL_DECIDED）+ Admin UI H.3 审批面板接入 + **agent 主动请求路径**（deer-flow 对比启发，与声明式门控并存）+ **reason_kind 5 类型分类**。**暂停机制 = `goto=END` + checkpoint 续跑**（deer-flow 对比后定 — helix `tools_node` 是 L.L6 并行调度，不用 LangGraph `interrupt()`）。STREAM-J-DESIGN § 14（重写 + 加 § 14.3a/14.5~14.9）+ Mini-ADR J-15 / J-24（含 (4)(5)）。**2026-05-22 完成（6 PR 链）**：
  - [x] **设计 PR** #242 — STREAM-J-DESIGN § 14 重写 + Mini-ADR J-24 修订加 (4)(5)
  - [x] **J.8-step1** #243 — protocol approval.py（ApprovalRequest/Decision/ReasonKind）+ AgentState.pending_approval + PolicySpec 审批字段 + AuditAction 两态
  - [x] **J.8-step2** #244 — `tools_node` 前置审批检查 + `goto=END`/`RunStatus.PAUSED` + `ask_for_approval` builtin tool
  - [x] **J.8-step3a** #245 — `agent_approval` 表（migration 0031）+ ApprovalStore + run 暂停写行 + GET 含 pending_approval + APPROVAL_REQUESTED audit
  - [x] **J.8-step3b** #246 — resume 端点（`aupdate_state` 重定位续跑）+ tools_node 决策应用（approve/modify/reject）+ APPROVAL_DECIDED audit + retention-cleanup-job 24h 超时 sweep
  - [x] **J.8-step4** — eval module（tools/eval/hitl.py 17 case @ pass_rate=1.0）+ baseline regen（12 PASS / 2 DEFERRED）+ ITERATION-PLAN [x] + 零债 6 条
  - **零债 6 条核验** ✅：(1) 无 TODO/skeleton（声明式门控 + agent 主动请求双路径全实装 + resume + 超时 sweep）；(2) 测试覆盖达标（10 protocol DTO + 16 approval gate/resume 集成 + 9 ApprovalStore + 3 GET + 4 resume 端点 guard + 1 sse 暂停 + 2 超时 sweep + 17 eval baseline + 3 eval 单测）；(3) 设计文档同步（STREAM-J-DESIGN § 14.1~14.9 + Mini-ADR J-24 5 项）；(4) 可观测齐全（APPROVAL_REQUESTED / APPROVAL_DECIDED audit + RunStatus.PAUSED + retention sweep approvals_timed_out 计数）；(5) CI 全绿（6 PR 链全 8/8 checks）；(6) bug 不遗留
  - **(a) 显式推迟项**：(1) 多审批人 / 升级链 → M1 配 H.3 高级 UI；(2) 成本阈值自动审批 → M1 与 J.12 一起；(3) 异步通知（Slack/邮件/webhook）→ M1 与 Stream A；(4) 审批 SLA 监控指标 → M1+；(5) Admin UI 审批面板代码 → H.3 stream（J.8 仅交 API + audit）；(6) 超时路径的 audit_log APPROVAL_DECIDED 行 → M1（retention-cleanup-job 无 AuditLogger；超时 verdict 已记于 agent_approval 行终态）
  - [x] **J.8 收尾复审補強（Mini-ADR J-41）—— runs 持久化拆分**：复审 `RunManager` 纯内存发现"`runs` 表 M1+"决策含隐性弱能力（`GET /runs/{id}` 对非暂停 run 5 分钟 TTL 后 `404` 名实不符；`agent_approval` 被迫扛平行 status）。翻案：裸 run 生命周期表 `agent_run`（迁移 0032，tenant RLS）提前 M0 + `RunStore` ABC/Memory/SQL + `RunManager` 镜像写 + `GET` 内存未命中读库兜底；排队 / multitask / 重试 / DLQ 留 J.10（Mini-ADR J-26）。STREAM-J-DESIGN § 14.3a + Mini-ADR J-41。**2026-05-22 完成（2 PR）**：
    - [x] **设计 PR** #248 — STREAM-J-DESIGN § 19 加 Mini-ADR J-41 + § 14.3a 修订 + 本行
    - [x] **实施 PR** — 迁移 `0032_agent_run` + `AgentRunRow` ORM + `RunStore`（ABC/InMemory/Sql，置于 helix-runtime/runs/）+ `RunManager` 注入 store 在 create/set_status/cancel 镜像写 + `set_status` 加 `error` 参数（sse.py 2 处 ERROR 路径回传 `str(exc)`）+ `RunRecord`/`create` 加 `user_id` + control-plane app.py/runtime.py 接线 + `GET .../runs/{id}` 内存未命中读 `RunStore` 兜底
    - **零债 6 条核验** ✅：(1) 无 TODO/skeleton（RunStore 三实装 + RunManager 镜像 + GET 兜底全实装）；(2) 测试覆盖达标（10 InMemoryRunStore 单测 + 7 SqlRunStore 集成 + 5 RunManager 镜像 + 1 GET 兜底端点）；(3) 设计文档同步（Mini-ADR J-41 + § 14.3a + `manager.py` 头注释指向 J-41）；(4) 可观测齐全（`agent_run` 行即 run 状态的可查终态记录，GET 兜底消除 404 名实不符）；(5) CI 全绿；(6) bug 不遗留
    - **(a) 显式推迟项**：(1) run 排队 / multitask 策略 / 重试 / DLQ → J.10（Mini-ADR J-26，经 expand-contract 给 `agent_run` 加列）；(2) `GET .../runs` 列表 endpoint → H.3（表已就位，UI 查询底座 ready）；(3) `agent_run` 旧行 retention sweep → M1（M0 单实例累积量可接受）
- [x] **J.9 产物 / Artifact 管理** — artifact + artifact_version 两表 + tenant+user 组合 RLS + 内容经 supervisor 代理读取 + 惰性回填 size/sha256。**2026-05-20 修订（Mini-ADR J-25）+ 2026-05-21 设计修订**：M0 含 lifecycle（保留期 90 天 / DELETE/PATCH/versions API / 卷满清理策略）+ quota 接入 Stream C.5 + audit trail 三态 + **MIME-aware download + XSS 防护**（HTML/SVG 强制 attachment，deer-flow 对比启发 — (c) 红线）；病毒扫描显式 (a) 推 M2。STREAM-J-DESIGN § 10（重写 + 加 § 10.5~10.11）+ Mini-ADR J-11 / J-25（含 (4) MIME/XSS）。**2026-05-21 完成（5 PR 链）**：
  - [x] **设计 PR** #237 — STREAM-J-DESIGN § 10 重写 + § 10.5 MIME/XSS / § 10.6 lifecycle / § 10.7 quota / § 10.8 audit / § 10.9 endpoints / § 10.10 eval / § 10.11 边界 + Mini-ADR J-25 修订加 (4) MIME/XSS
  - [x] **J.9-step1** #238 — 迁移 0030 + lifecycle (soft-delete/restore) + retention cron 扩 artifact 维度
  - [x] **J.9-step2** #239 — Quota 接入 C.5 (artifact_download_count_30d + artifact_storage_bytes) + 端点 429 admission
  - [x] **J.9-step3** #240 — DELETE / PATCH / versions API + Audit trail (ARTIFACT_DELETE/UPDATE) + MIME-aware download + XSS 防护
  - [x] **J.9-step4** — eval module (tools/eval/artifact.py 16 case @ pass_rate=1.0) + baseline regen (11 PASS / 3 DEFERRED) + ITERATION-PLAN [x] + 零债 6 条
  - **零债 6 条核验** ✅：(1) 无 TODO/skeleton（艺术皿 prompt_fragment/save_artifact 工具 / 全部 endpoints 已实装并通过 16 case + 21 unit + SQL integration）；(2) 测试覆盖达标（21 InMemory + 8 SQL integration + 26 control-plane endpoint + 23 MIME unit + 16 eval baseline）；(3) 设计文档同步（STREAM-J-DESIGN § 10.1~10.11 + Mini-ADR J-25 4 项）；(4) 可观测齐全（download 429 audit + ARTIFACT_DELETE/UPDATE audit + retention sweep report 两字段）；(5) CI 全绿（5 PR 链全 8/8 checks）；(6) bug 不遗留（ARTIFACT_SAVE 显式标"orchestrator AuditLogger plumbing M1+"在 audit.py 注释里登记）
  - **(a) 显式推迟项**：(1) ARTIFACT_SAVE audit 待 ToolEnv AuditLogger plumbing（同 K6 memory tool 模式）；(2) save-side STORAGE_BYTES quota 待 orchestrator-side QuotaService 注入；(3) archive 中间档 (tar.zst → ObjectStore) — schema 已预留 `archived_object_key` + 索引，复用 J.15 volume archive 路径（Mini-ADR J-29 第 2 项）；(4) 病毒扫描推 M2（Mini-ADR J-25 (5)）；(5) `.skill` ZIP artifact + Install 按钮 → M1-K（J.7b 一起）；(6) 前端 inline preview → H.4 admin UI
- [x] **J.10 调度 / 触发** — cron + webhook 两类 trigger + scheduler 单副本（轮询 `agent_trigger` 表）+ `trigger_run` 表；`event` 触发器 + 分布式 scheduler 推 M1+。**2026-05-20 修订（Mini-ADR J-26）+ 2026-05-22 deer-flow 对比修订（Mini-ADR J-42）**：M0 含 failed run 重试 / DLQ（K7 模式）+ scheduler quota + 每触发器独立 secret 的 webhook 鉴权；`event` 源（PG NOTIFY）推 M1、APScheduler 弃用改轮询。STREAM-J-DESIGN § 16（重写）+ Mini-ADR J-18 / J-26 / J-42。**2026-05-22 完成（6 PR 链）**：
  - [x] **设计 PR** #250 — STREAM-J-DESIGN § 16 重写 + Mini-ADR J-26 (3)(4) 修订 + 新增 J-42
  - [x] **J.10-step1 数据层** #251 — migration `0033_agent_trigger`（`agent_trigger` + `trigger_run` 两表，tenant RLS）+ ORM + `TriggerKind`/`TriggerSpec` protocol + `TriggerStore` ABC/InMemory/SQL
  - [x] **J.10-step2 scheduler + cron** #252 — `croniter` + in-process `TriggerScheduler`（reaper 范式）+ cron 触发复用 `run_agent` 发起链 + control-plane lifespan 接线
  - [x] **J.10-step3 webhook + CRUD API** #253 — `POST /v1/webhooks/{id}` 入站（per-trigger secret 鉴权 + `/v1/webhooks` middleware 豁免）+ `/v1/triggers` CRUD API + `fire_trigger` 抽取
  - [x] **J.10-step4 DLQ + quota** #254 — failed trigger run DLQ 重试（K7 backoff 1m→5m→30m→2h→6h，scheduler 三遍 fire/reconcile/retry）+ scheduler quota（创建时 `count_cron_by_tenant` 直查，超额 429）+ `AuditAction` 四态
  - [x] **J.10-step5 eval + 收尾** — `tools/eval/trigger.py`（17 case @ pass_rate=1.0）+ baseline regen（`J.10_trigger` PASS → 13 PASS / 1 DEFERRED）+ ITERATION-PLAN [x] + 零债 6 条
  - **零债 6 条核验** ✅：(1) 无 TODO/skeleton（cron + webhook 双触发器 + scheduler 三遍 sweep + DLQ + quota 全实装）；(2) 测试覆盖达标（17 InMemoryTrigger/TriggerRunStore 单测 + 13 SqlTrigger/RunStore 集成 + 13 scheduler 单测 + 12 triggers/webhook API + 17 eval baseline + 3 eval 单测）；(3) 设计文档同步（STREAM-J-DESIGN § 16.1~16.11 + Mini-ADR J-26 (3)(4) 修订 + J-42）；(4) 可观测齐全（`TRIGGER_CREATE/UPDATE/DELETE/FIRE` audit + `helix_..._triggers_fired_total` / `_dead_letters_total` / `_scheduler_cycle_errors_total` counter）；(5) CI 全绿（6 PR 链全 8/8 checks）；(6) bug 不遗留
  - **(a) 显式推迟项**：(1) `event` 触发器 + PG NOTIFY → M1（无 M0 消费场景，Mini-ADR J-42）；(2) 分布式 scheduler / 多副本选主 → M1+（Mini-ADR J-18）；(3) HMAC 载荷签名（webhook 防重放）→ M1；(4) webhook 完成回调 → M1；(5) `GET /v1/triggers` 租户级列表（M0 限 `?agent_name=` per-agent）→ H.3；(6) PAUSED 触发式 run 的 trigger_run reconcile → M1（M0 留 `fired`，run resume 另起 continuation run）
- [x] **J.11 Model 路由** — `routing:` 块按步骤类别声明式选模型(`RouteRule.when` → `ModelSpec`):planner / reflect 节点可路由到与 agent 循环不同的模型,各带自己的 fallback 链。构造期 `build_step_routers` 给每个节点绑定对应 router;无规则的步骤类别复用默认。声明式规则,非动态难度估计(Mini-ADR J-6)。`vision` 步骤类别随 J.6 多模态加入。STREAM-J-DESIGN § 7。
- [x] **J.12 学习 / 反馈闭环** — 把 L7 trajectory ObjectStore + G.6 feedback → 策划 `eval_dataset` 表（与 J.13 共用）；不含训练 / 微调（推 M2+）。**2026-05-20 修订（Mini-ADR J-27）**：与 L7 trajectory 分工修剪 —— J.12 不再写 `trajectory` PG 表（L7 ObjectStore 已是底座）。**2026-05-22 deer-flow 对比修订（Mini-ADR J-43）**：策划机制 = 规则候选 + 人工策划；候选生成 = 后台 `CurationWorker` 预生成；`eval_dataset` 按 (tenant, agent_name) 归集（非 per-instance）。STREAM-J-DESIGN § 17（重写）+ Mini-ADR J-19 / J-27 / J-43。**2026-05-22 完成（5 PR 链）**：
  - [x] **设计 PR** #256 — STREAM-J-DESIGN § 17 全重写 + 新增 Mini-ADR J-43
  - [x] **J.12-step1 数据层** #258 — migration `0034_eval_dataset`（`eval_dataset` + `curation_candidate` 两表，tenant RLS）+ ORM + `EvalDatasetSource`/`CurationSignal`/`CandidateStatus` protocol + `EvalDatasetStore`/`CurationCandidateStore` 三件套 + `TrajectoryReader`（ObjectStore 读 helper）
  - [x] **J.12-step2 CurationWorker + 规则** #259 — 后台 `CurationWorker`（`TriggerScheduler` 范式）+ 跨租户扫 trajectory ObjectStore + `thread_meta`/`feedback` join + 3 类信号规则 upsert `curation_candidate` + control-plane lifespan 接线
  - [x] **J.12-step3 策划 API + eval_dataset CRUD** #260 — `GET /v1/curation/candidates`（+ 详情含 trajectory / promote / dismiss）+ `/v1/eval-datasets` CRUD（含 `source=golden` 纯手工）+ `AuditAction` 五态 + `eval_dataset` 行数 quota
  - [x] **J.12-step4 导出 + eval + 收尾** — 导出 CLI `tools/eval/export_dataset.py`（`eval_dataset` → `tools/eval/datasets` YAML）+ `tools/eval/learning.py`（21 case @ pass_rate=1.0）+ baseline regen（`J.12_learning` PASS → **14 PASS / 0 DEFERRED**）+ ITERATION-PLAN [x] + 零债 6 条
  - **零债 6 条核验** ✅：(1) 无 TODO/skeleton（CurationWorker + 3 类信号规则 + 策划 API + eval_dataset CRUD + 导出 CLI 全实装）；(2) 测试覆盖达标（18 InMemory store 单测 + 11 SQL 集成 + 6 TrajectoryReader + 9 CurationWorker + 13 策划 API + 21 eval baseline + 3 eval 单测）；(3) 设计文档同步（STREAM-J-DESIGN § 17.1~17.11 重写 + Mini-ADR J-43 + J-27 落实）；(4) 可观测齐全（`EVAL_DATASET_CREATE/UPDATE/DELETE` + `CURATION_PROMOTE/DISMISS` audit + `helix_..._curation_worker_cycle_errors_total` / `_curation_candidates_detected_total` counter）；(5) CI 全绿（5 PR 链全 8/8 checks）；(6) bug 不遗留
  - **(a) 显式推迟项**：(1) 训练 / 微调管线 → M2+（Mini-ADR J-19，J.12 终点是策划数据集）；(2) 策划 review 前端 UI → Stream H（J.12 仅交 API + audit，对标 J.8）；(3) `eval_dataset` → J.13 自动回归门 → J.13c M1（导出 CLI 是半自动：导出 + 人工 commit）；(4) feedback 驱动 prompt 自动改进 / RLHF → M2-D；(5) per-instance 个性化 → 属 J.3 记忆，非 J.12；(6) 候选生成高水位标记（避免每轮全量重扫 ObjectStore）→ M1
- [x] **J.13a 逐能力 eval 场景集（M0）** — 7 已交付能力（J.1 / J.2 / J.3 / J.6 / J.11 / J.14 / J.15）各 1 个 eval module + dataset；7 deferred 能力（J.4 / J.5 / J.7 / J.8 / J.9 / J.10 / J.12，J.13 自身排除）写 skeleton stub；aggregator `tools/eval/run_baseline.py` + checked-in baseline `tools/eval/baselines/m0_gate_baseline.yaml` 作为 Stream M Gate 锚点。STREAM-J-DESIGN § 18 + Mini-ADR J-20 / J-28 / J-37 / J-38 / J-39。**2026-05-21 完成（3 PR）**：
  - [x] **设计 PR** #214 — § 18 重写 + Mini-ADR J-37 / J-38 / J-39（per-cap metric 矩阵 + checked-in baseline + Haiku 4.5 judge）
  - [x] **J.13a-1** #215 — `_capability.py` 共享 protocol + run_baseline aggregator + 3 deterministic eval（J.3 memory_recall 扩 32 case / J.11 model_routing 16 case / J.14 per_user_isolation 12 case 强制 1.00）+ 初始 baseline 3 PASS / 11 DEFERRED
  - [x] **J.13a-2** #216 — `_judge.py`（ScriptedJudge CI + AnthropicHaikuJudge 周跑 + `make_judge_from_env` env switch）+ 4 已交付能力 eval（J.1 plan_execute 20 case + judge mean 4.5 / J.2 reflect 16 case 含 fail-safe / J.6 multimodal 12 case dispatch + ask_image / J.15 persistent_volume 11 case lifecycle + quota）+ baseline 推到 7 PASS / 7 DEFERRED
  - **零债 6 条核验 ✅**：(1) 代码干净（无 TODO/FIXME/XXX；ruff/mypy/pre-commit 全过）/ (2) 测试达标（62 个新单测全过 / 含 24 个 J.13a-2 新增）/ (3) 文档同步（STREAM-J-DESIGN § 18 / Mini-ADR J-37/J-38/J-39 与代码一致 / tools/eval/README.md 更新）/ (4) 可观测齐全（baseline yaml metadata 含 `generated_at` / `helix_commit` / `judge_model` provenance）/ (5) CI 全绿（#214-#216 各自 8/8）/ (6) bug 不遗留（CodeQL Protocol ellipsis + unused Literal alias 第 2 轮押定）
  - **baseline 7 PASS**：J.1 plan_execute（pass=1.00 / judge_mean=4.50）/ J.2 reflect（correction=0.875 / fp=0.00）/ J.3 memory_recall（recall@5=1.00 / mrr@5=0.984）/ J.6 multimodal（path_a=1.00 / path_b=1.00）/ J.11 model_routing（pass=1.00）/ J.14 per_user_isolation（pass=1.00）/ J.15 persistent_volume（pass=1.00）
  - **(a) 显式推 M1**：~~J.13b 在线采样 + LLM-judge 配额 + budget cap~~（M1 早期 / 并入 M2-D，依赖 Stream C.5 quota 接 per-tenant judge budget）/ ~~J.13c CI 周跑 job + drift 阈值告警 + N 次重跑置信区间~~（M1 早期 / 并入 M2-D，需先观察 J.13b 真实 flakiness）
- [x] **J.14 租户内 per-user 隔离** — `(tenant_id, user_id)` 复合 scope;thread / 长期记忆 / 工作区按用户隔离(多租户深化,Stream C 性质)。PR1:`tenant_user` 注册表(迁移 0015)+ `TenantUserStore` + `thread_meta.user_id`;PR2:control-plane 接入 —— 会话创建 stamp `user_id`、读 / run / 状态流转的用户所有权强制隔离(`caller_owns_thread`,admin 旁路、机器主体租户级)。STREAM-J-DESIGN § 4。
- [x] **J.15 有状态 per-user 执行环境** — docker named volume per user + 热沙盒会话 + 空闲 TTL reaper（default 15min）+ restore = 冷启动挂暖卷；不走 CRIU（held-pipe 限制）。生产级数据保护三维全到位：quota / lifecycle / archive / daily backup / at-rest 加密文档化。STREAM-J-DESIGN § 9 + § 9.5 + Mini-ADR J-9 / J-10 / J-29 / J-36。**2026-05-21 完成（4 PR）**：
  - [x] **设计 PR** #210 — § 9 修订 + § 9.5 J-29 + J-36 详化
  - [x] **J.15-补强-1** #211 — Volume quota enforcement + soft-delete 状态机（迁移 0026 + `quota_enforcer.py` + `mark_workspace_deleted` API + audit + 429/410 HTTP）
  - [x] **J.15-补强-2** #212 — Volume archive + daily backup + at-rest 加密文档（迁移 0027 DLQ + `lifecycle.py` archive/backup/drain_dlq + `DockerClient.archive_volume/remove_volume` + lifespan daily backup task + `tools/persistence/restore_volume.py` + `docs/runbooks/volume-restore.md` + `deployment.md` at-rest 加密章节）
  - **零债 6 条核验 ✅**：(1) 代码干净（无 TODO/FIXME/XXX）/ (2) 测试达标（103 个新单测 +设计 PR / 补强-1 70 / 补强-2 40）/ (3) 文档同步（STREAM-J-DESIGN § 9 / § 9.5 / Mini-ADR J-29/J-36 与代码一致）/ (4) 可观测齐全（4 个 audit action：`WORKSPACE_QUOTA_DENIED` / `_SOFT_DELETE` / `_ARCHIVE` / `_BACKUP` 入 audit_log）/ (5) CI 全绿（#210-#212 各自 8/8）/ (6) bug 不遗留（K7 backoff 模式复用 / 设计 PR + 补强 PR 一线对齐）
  - **(a) 显式推 M1**：跨 host 调度（与 M1-A sandbox warm pool 同期）/ 90 天 archive hard-delete（retention-cleanup 加 volume 维度）/ recovery API（un-soft-delete workspace）/ multipart streaming ObjectStore.put（M0 单批 in-mem 1.5 GiB cap 够用）

**Stream J Verification**：每子项接入 live agent 路径、单测 + 集成测试 80% 覆盖；26 维能力矩阵无"缺失 / 骨架"遗留（eval 按 J.13 结论）；canonical per-user 持久 agent 端到端跑通。

### M0 Exit Criteria（M0 → M0→M1 Gate 验证门）

- [ ] 24 项 P0 全部勾选完成（参考 [architecture/07-INFRASTRUCTURE-GAPS](./architecture/07-INFRASTRUCTURE-GAPS.md) §"Gap 严重性矩阵"）
- [x] **Stream K（Capability Hardening Sprint）15 子项完成** —— 13 条 (c) 类弱版全部补到生产级（[STREAM-K-DESIGN](./streams/STREAM-K-DESIGN.md)）；零债 6 条核验 ✅，PR #172 + #182–#196 全部 squash 合入 main（2026-05-20）
- [x] **Stream L（Hermes-derived 单 turn 能力强化）8 子项完成** —— L1-L8 全部补到生产级（[STREAM-L-DESIGN](./streams/STREAM-L-DESIGN.md)）；零债 6 条核验 ✅，PRs #198–#206 全部 squash 合入 main（2026-05-20）
- [ ] **Stream J（Agent Harness 能力补全）15 子项完成** —— 26 维能力矩阵无缺口
- [ ] canonical 能力 agent 跑通 + staging 冒烟（便宜模型端到端真实 run）
- [ ] 测试金字塔达标：unit ≥ 85%、integration ≥ 70% 关键路径、E2E 5-10 场景
- [ ] 7 条沙盒安全验证用例全部通过
- [ ] SLO 第一个版本写入文档；P0 告警全部接入
- [x] **control-plane 状态持久化（SQL store 切换）** — `store_backend` setting（`memory` / `sql`），`create_app` 在 `sql` 时建 async engine + `build_rls_sessionmaker` 包装的 sessionmaker，把全部 10 个 `Sql*Store` / `DbFeedbackStore` 注入，lifespan `finally` dispose engine；`infra/docker-compose.yml` 的 `control-plane` 设 `HELIX_AGENT_STORE_BACKEND=sql`。设计：[STREAM-B-DESIGN](./streams/STREAM-B-DESIGN.md) Mini-ADR B-6。同 PR 补齐 `SqlAgentSpecStore` + 3 个 auth SQL store 此前缺失的集成测试。已知项：`/v1/quota/*` mTLS 跨租户路径在 RLS `FORCE` 下的写入待 Stream C 深化时处理（M0 进程内单体不走该 HTTP 路径）。

---

## M0→M1 Gate（4-6 周）—— 重构为绝对数值化 + canonical agent + eval baseline

参考：[streams/STREAM-M-DESIGN](./streams/STREAM-M-DESIGN.md)（设计先行；2026-05-20 落地）

> **2026-05-20 重构**（按 [memory:complete-not-minimal](../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_complete_not_minimal.md) + [memory:general-platform-positioning](../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_general_platform_positioning.md)）：原"相对 Dify"框架（token ±10% / p95 1.2x / 质量评估）被 Stream M 整段替换为**绝对数值化 SLO + canonical agent 端到端 + eval-set baseline + 安全验收 + 数据保护演练**五类 Exit Criteria。helix 已定位通用 agent 平台、不绑用户 Dify 业务；dogfood 仍可平行跑作 sanity check，不再作 Exit Criteria。

### 目标

锁定 helix 已交付能力达到生产强度，进 M1 真生产 release 安全。

### 工作清单

- [ ] **30 天稳定性观察期**（K10 G.7 大盘 + G.2 告警实时采集 § Exit Criteria 全部 SLO 指标）
- [ ] **dogfood 平行运行**（可选 sanity check，不阻塞决策；选项 [[target-product-form]] canonical agent 或 Phase 0.1 Dify 业务任一）
- [ ] **eval baseline 周期跑**（J.13a 逐能力 eval 场景集每周一次，分数写入 `tools/eval/baselines/`）
- [ ] **手工渗透测试 + 沙盒 7/7 用例 staging Linux 跑通**（K5 已锁定不允许"软推迟"）
- [ ] **数据保护演练**（PG / WORM / KMS 各 staging 1 次）

### Exit Criteria（细节见 STREAM-M-DESIGN § 2）

**系统级 SLO（绝对数值）**：
- [ ] 可用性 < 0.1% 5xx in 30d
- [ ] TTFT P95 < 2.0s
- [ ] End-to-end P95 < 30s（canonical agent runs）
- [ ] SSE 流断裂率 < 0.05% in 30d
- [ ] Sandbox 冷启动 P95 < 5s
- [ ] Durable resume P95 < 1.0s
- [ ] Memory recall@5 ≥ 0.7（K12 中 / 英文 seed set against real embedder）
- [ ] **P0 事故数 = 0**（30 天观察期；P0 触发立刻 retro + 修复 + 30 天计数器重置）

**Canonical agent 端到端**（[memory:target-product-form](../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/project_target_product_form.md)）：
- [ ] 多轮对话跨会话保持记忆
- [ ] 持久工作区跨 run 留存
- [ ] 空闲 hibernate + 快速 restore
- [ ] artifact 跨 thread 可访问
- [ ] 审批门跑通 + audit trail
- [ ] 多模态输入 Path A + Path B 真实图像在 staging 跑通

**Eval baseline**：
- [x] J.13a 逐能力 eval 场景集 baseline 锁定（`tools/eval/baselines/m0_gate_baseline.yaml`，2026-05-21 落地；7 PASS + 7 DEFERRED）

**安全验收**：
- [ ] **gVisor 7/7 沙盒安全用例 staging Linux 全部跑通**（含 `test_gvisor_cve_2019_5736_poc_fails`、`test_gvisor_timing_isolation`；K5 锁定不允许"软推迟"）
- [ ] cross-tenant SSE / memory recall / artifact 全部 reject

**数据保护演练**：
- [ ] K15 PG 恢复 staging 1 次
- [ ] K14 WORM 恢复 staging 1 次
- [ ] K13 KMS 轮换 staging 1 次

### Capability Uplift Sprint（~12-13 周；与 30 天稳定性观察期并行）

参考：[../research/capability-uplift-plan.md](../research/capability-uplift-plan.md)（详细排期 + 依赖图）；[../research/helix-vs-hermes-gap.md](../research/helix-vs-hermes-gap.md)（gap 评估 + 优先级判定）

> **2026-05-27 新增** —— 基于 [`helix-vs-hermes-gap.md`](../research/helix-vs-hermes-gap.md) 5 条 gap + Memory 系统深化讨论 3 条 = **8 项能力提升**。按"能提前的尽量提前 ── 不挑成本"原则，**8 项全部启动**：6 项 Gate 期内完整完成，2 项基础设施完成（启用 / 调参按 M1 节奏）。**不打破 M0 阶段标签**，作为 Gate 期附加 sprint，与 30 天稳定性观察、eval baseline、安全演练并行。

**P0 — 安全 / 合规盲点（必须 Gate 内完成）**

- [ ] **#1 Cron prompt 注入扫描（含隐形 Unicode）** —— `helix-common` 抽威胁模式库 + `services/control-plane/api/triggers.py` create/update 严扫 + `trigger_firing.py` 拼完 skill 后宽扫 + 新 audit action `TRIGGER_PROMPT_INJECTION_BLOCKED`。借鉴 Hermes `tools/cronjob_tools.py:68-200` 的双层扫描思路。**Week 1，~3 天**。
- [ ] **#2 Memory 投毒防御 + drift backup** —— `MemoryStore.write/recall` 复用 #1 威胁模式库；写时扫拒入（可选 strict mode）+ 读时扫中毒条目替换为 `[BLOCKED:...]` 占位符（live 保留原文给用户审）+ drift detection 检测外部直改 DB；新 audit action `MEMORY_INJECTION_BLOCKED` / `MEMORY_DRIFT_DETECTED`。**Week 2-3，~1.5 周**。前置：#1 威胁模式库已抽到 `helix-common`。

**P1 — Operator / IDE experience**

- [ ] **#5 MCP Server** —— `FastMCP` lib 包装现有 control-plane API；M0 仅暴露 6 个**读权限**工具（conversations_list / conversation_get / messages_read / events_poll / events_wait / channels_list），写权限（messages_send / permissions_respond）留 M1 后期；auth 复用现有 OIDC + 可选 MCP-specific token；RLS 复用 tenant_scope。**Week 4-6，~2 周**。

**P1 — Memory 系统深化**

- [ ] **#6 Memory hybrid retrieval（向量 + 全文 RRF）** —— `memory_item` 加 tsvector 列（迁移 0039）+ 自动 trigger 维护；`MemoryStore.recall()` 直接 port `J.5 KnowledgeRetriever`（hybrid + RRF rerank）；K.K12 eval baseline 重跑 + 锁新 baseline；per-tenant manifest 可关闭 hybrid 回退纯向量。**Week 4-5，~1.5 周**（与 #5 并行）。
- [ ] **#8 Memory frozen snapshot / 前缀缓存优化** —— `memory_recall_node` 加 `per_session` 召回模式（vs 默认 `per_turn`）；L.L1 prompt caching middleware 适配 cache_control 加在 memory block 末尾；manifest `policies.memory_recall_mode` 字段。**Week 5-6，~1.5 周**（与 #5 / #6 并行）。

**P2 — Skill 库进化基础**

- [ ] **#3 Skill 附属文件（references / templates / scripts）** —— `skill_version.supporting_files` JSONB 列（迁移 0040）+ `skill_manager_tool` 加 `write_file` / `remove_file` action + agent_factory 加载 skill 时按需暴露 `skill_view(name, "references/...")` 三层 API + ZIP import/export 支持子目录 + Admin UI Skills page 文件浏览。**直接抄 Hermes 目录约定 + 用途分工**（references=session 细节 / templates=可复用 / scripts=可执行），省 1-2 轮 ADR。**Week 7-10，~2 周**。
- [ ] **#4 Curator 自动状态机（基础设施）** —— `SkillRow` 加 `pinned: bool` + `last_activity_at: timestamptz`（迁移 0041）+ 新建 `services/control-plane/skill_curator.py` 周期 worker 纯启发式三态转移（默认 30 天 → stale / 90 天 → archived，per-tenant 可配）+ Admin UI Skills page pin 操作 + 状态显示。**基础设施 Gate 期内完成**；启用阈值调参等 J.7b-1 上线后看真实膨胀率（M1-K 期间）。**Week 11-12，~1 周**。
- [ ] **#7 Memory 短期 → 长期凝结引擎（基础设施）** —— 新建 `services/control-plane/memory_consolidator.py` 凝结引擎本体（识别反复事实 trigger + 用 Haiku 等辅助模型总结 N 轮对话窗口 → 写 long-term memory）+ 防误学约束（参考 Hermes Skill review prompt "什么坚决别写"4 条分类：环境性失败 / 负面工具断言 / session-specific transient errors / one-off task narratives）+ M2-C archive 流水线接口预留。**基础设施 Gate 期内完成**；触发策略 + 阈值调优等 M1 dogfood 数据反过来调。**Week 11-13，~3 周**。

**Sprint Verification**：

- 8 项按零债 6 条核验（无 TODO / 测试达标 / 文档同步 / 可观测齐全 / CI 全绿 / bug 不遗留）
- 每项 PR merge 前 K.K12 + J.13a baseline 回放无退化（≥ 5% 退化卡 PR）
- 30 天稳定性观察期不被搅动（SLO 实时采集，破任一立刻 retro）
- schema 变更（#3 迁移 0040 / #4 迁移 0041）**串行上 main**（先 #3 跑 1 周再上 #4），不并行降风险

**M1 期间需要做的事项**（仅启用 + 调参，非新能力开发）：

- **#4 Curator 阈值调参**：J.7b-1 agent 自创建 skill 上线后跑 2-4 周看真实膨胀率，按需把默认 30/90 阈值改 7/30
- **#5 MCP Server 写权限暴露**：M1 后期把 messages_send / permissions_respond 暴露给 IDE 用户，RLS 跨租户审计验证后
- **#7 凝结触发策略调优**：M1 dogfood 数据反过来调"什么时候凝结" + "凝结多深" + 防误学约束的真实失败模式
- **#8 frozen snapshot 默认启用条件**：客户成本报告里 memory recall cache miss > 15% 时让 `per_session` 成为 manifest 默认

**Risk 缓解**（详见 capability-uplift-plan.md § Risk）：

- 12-13 周单人扛不住 → 拆 "前 6 周（#1 #2 #5 #6 #8）+ 后 6-7 周（#3 #4 #7）" 两个 mini-sprint，中间留 1-2 周 dogfood observation
- #2 写时扫影响 J.3 deploy 性能 → 默认仅 read 扫，write 扫为可选 strict mode
- #5 写权限副作用 → 本期不出写权限工具，留 M1
- #6 hybrid 某 query 退化 → per-tenant 可关回退纯向量
- #4 / #7 基础设施重做风险 → 模块化 + manifest 可关，接受 10-20% 重做换 6-12 个月提前价值

**决策点**：全部勾完 → 进 M1；否则选项 A 再走一轮 30 天 / B 暂停修复 / C 回退架构

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

#### M1-B 数据生命周期硬化（~3-4 周；拆 B1 / B2）

> **2026-05-20 拆分**：原 M1-B 把"跨 AZ DR + IaC（大投入，Terraform + 多 AZ 跃迁）"与"zero-downtime migration + 归档（中等投入）"混在一起。按 [memory:complete-not-minimal](../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_complete_not_minimal.md) 拆为两个子组，独立排期。

**M1-B1 中等投入项**（~1.5 周）：
- [ ] DB zero-downtime migration 规范（Alembic + expand-contract）
- [ ] 数据归档完整 pipeline（含 audit_log / event_log / memory 各自归档窗口）

**M1-B2 大投入项**（~2.5 周）：
- [ ] 跨 AZ DR 演练（落实 P1 跨 AZ）
- [ ] IaC（Terraform）描述基础设施

> 历史项：~~**retention-cleanup-job CI-only `permission denied` 收尾**~~ → 已移入 **Stream K.K3**（P0 Gate 阻塞），M0 闭环。

#### M1-C Credential Proxy 升级（~3-4 周；加 C0 评估前置）
参考：[architecture/subsystems/11-credential-proxy.md](./architecture/subsystems/11-credential-proxy.md)（如有）

> **2026-05-20 加 C0 评估前置**：F.5 aiohttp 自研版 + F.6 KMS static + K13 KMS 轮换演练已有，先做"M1-C0 评估"再决定 Envoy 上不上。按 [memory:no-design-choice-disguise](../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_no_design_choice_disguise.md) 不允许"Envoy 是设计选择"包装"aiohttp 已经够用但还是上 Envoy"的过度工程。

- [ ] **M1-C0 评估**（~3 天）：aiohttp 自研版在 M0 dogfood + Gate 期间的真实表现 vs Vault dynamic secrets 强需求；产物 `docs/decisions/m1-credential-proxy.md` 决定上 Envoy 还是继续 aiohttp + 加 dynamic 能力
- [ ] **C1**（基于 C0 决策）：Envoy + Lua + Vault dynamic secrets / 或 aiohttp 加 dynamic secrets 路径
- [ ] **C2**：自动密钥轮换 + 短 TTL（接入 K13 演练经验）

#### M1-D Vendor P1 中间件（~2 周；token_usage 已提前到 M0 G.9）
参考：[architecture/04-ROADMAP](./architecture/04-ROADMAP.md) §"M1 vendor P1"
- [ ] `thread_data_middleware`（118 LOC）—— M0 dogfood + Gate 跑通后回看实际需求再决定
- [ ] `deferred_tool_filter_middleware`（107 LOC）—— 同上
- [ ] ~~`token_usage_middleware`（303 LOC）~~ → **提前到 M0 Stream G.9**（让 dogfood 看见成本；2026-05-20 未交付项审计）
- [ ] ~~`uploads_middleware`~~ → 提前至 **Stream J.6**（多模态输入）
- [ ] ~~`reflection/resolvers.py`~~ → 提前至 **Stream J.2**（反思 / 自我修正）
- [ ] ~~subagent executor + guardrails~~ → 提前至 **Stream J.4**（Sub-agent）

#### M1-E 可观测核心生产化（~2 周；紧跟 M1-B 数据层硬化）
- [ ] OpenTelemetry / Prometheus / Grafana / Loki 全栈生产化
- [ ] Langfuse 业务大盘
- [ ] 成本可视化大盘（基于 token_usage_middleware）

#### M1-F 多租户深化 + Python 插槽（~5-6 周；拆 F1 / F2）

> **2026-05-20 拆分**：Sub-Agent 已提前到 J.4；剩下两块（tenant_id 深化 / 多租户测试 中等投入）+ Python 插槽（重大新能力面，依赖 M1-A cosign）。按 [memory:complete-not-minimal](../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_complete_not_minimal.md) 拆为两个子组独立排期。

**M1-F1 多租户深化 + 测试**（~2 周）：
- [ ] tenant_id 全链路贯通深化（依赖 M1-C Vault dynamic / 或 aiohttp dynamic）
- [ ] 多租户隔离自动化测试（cross-tenant 数据泄漏检测；K2 SSE + J.14 thread/memory/artifact 已铺基础，本项扩到全表）

**M1-F2 Python 插槽**（~3-4 周；依赖 M1-A cosign 供应链）：
参考：[architecture/02-AGENT-MANIFEST](./architecture/02-AGENT-MANIFEST.md) §"Python 插槽"
- [ ] Python 插槽：`code.package` + `tool/graph/hook` 入口
- [ ] 插槽代码安全审查 + 沙盒化（含 J.15 持久卷集成 / 或独立 sandbox 隔离）

历史项：~~Sub-Agent YAML 声明 + LangGraph subgraph 实现~~ → 提前至 **Stream J.4**（Sub-agent / 多智能体委派）

#### M1-K Agent skill 进化（J.7b，~3-4 周；与 M1-F2 Python 插槽同属"动态能力扩展"领域）

> **2026-05-21 补加**（J.7a 启动前 deer-flow 对比调研后用户复审）：M0 J.7a 锁 8 项进 M0 scope；以下 8 项 (c) 红线推迟到 M1，必须显式落 backlog 不丢失（按 [memory:no-design-choice-disguise] + [memory:zero-tech-debt]）。参考：STREAM-J-DESIGN § 15 + Mini-ADR J-23（2026-05-21 修订）。

- [ ] **J.7b-1 agent 进化工具**（`author_skill` / `refine_skill` / `fork_skill` / `propose_skill_to_tenant`）—— agent 在 run 期沉淀新 skill 进 draft；用户审批后切 active；带 audit + 速率限制。**[2026-05-27 关联]** 上线后启动 **Capability Uplift Sprint #4 Curator 自动状态机调参**（基础设施已在 Gate 期完成，跑 2-4 周看真实 skill 库膨胀率，按需把默认 30/90 阈值改 7/30）；**防误学约束**优先参考 Hermes Skill review prompt "什么坚决别写" 4 条分类（已在 Sprint #7 沉淀进 memory consolidator，可同源复用）。**[2026-05-28 设计预约定]** 关键 visibility / fork / promote 三大支柱见 `STREAM-J-DESIGN.md` § 15.7（agent 创建的 skill 默认 `agent_private`、fork 是经验复用通路、promote 走 admin 审）；schema 加 3 列 `visibility` / `created_by_agent_id` / `forked_from` + 6 个新 audit actions；M1-K design phase 必须基于 § 15.7 5 条准入条件展开
- [ ] **J.7b-2 `code` 字段执行边界** —— `SkillVersion.code: str | None` 解禁；依赖 M1-F2 Python 插槽 + sandbox（gVisor 7/7 用例通过）+ AST 静态校验
- [ ] **J.7b-3 Progressive / lazy skill loading** —— agent 引用时才注入 skill prompt（非 build-time 静态拼）；与 M0 静态拼共存模式可配
- [ ] **J.7b-4 LLM-based admin content moderation** —— 升级 M0 的正则 deny-list 到 LLM 审核（用 Haiku judge 模式）；依赖 stable LLM router + budget cap
- [ ] **J.7b-5 Public / system skill 内置库** —— `system` 占位 tenant + 20+ 内置模板（参考 deer-flow `/skills/public`）；可被任意 tenant 引用 + override；fork 模式
- [ ] **J.7b-6 Supporting files / 附件** —— skill 含 `scripts/templates/references/` 子目录；agent 按需读 / 不主动执行；`.skill` ZIP 扩展到带子目录。**[2026-05-27 提前]** 已并入 **Capability Uplift Sprint #3**，在 Gate 期 Week 7-10 完成（vs 原计划 M1-K 期）；M1-K 仅做 J.7b 其他 7 项
- [ ] **J.7b-7 Per-agent skill 启停细化** —— 按 agent role / context 临时启停某 skill；当前 M0 仅按 manifest 静态控制
- [ ] **J.7b-8 Skill UI 元数据** —— `icon` / `color` / `display_name` 字段；M1-I Admin UI 升级一并接入

**M2+ 跟进**：Per-thread skill 激活 / A/B / canary（与 M1-G 灰度 + Canary + 回滚关联），M2 期再决归属。

#### M1-G 灰度 + Canary + 回滚（~3 周）
- [ ] manifest 版本灰度面板
- [ ] 灰度过程指标自动采集（消费 M1-E 数据）
- [ ] A/B 流量切分

#### M1-H 运维可观测扩展（~2 周）
- [ ] Runbook 库（每个 P0 告警 1 份 SOP）
- [ ] Sentry / GlitchTip 错误追踪
- [ ] Falco 运行时安全监控（落实 P1 Falco）

#### M1-I CLI + Admin UI 升级（~3 周）

> **[2026-05-27 关联]** MCP Server（让 Claude Code / Cursor 反向访问 helix sessions / runs / approvals）作为同主题 operator experience 升级在 **Capability Uplift Sprint #5** 已提前到 Gate 期：基础设施 + 6 个读权限工具（conversations_list / messages_read / events_poll 等）M0 完成；M1-I 期仅补写权限工具暴露（RLS 审计验证后）。

- [ ] `helix lint` + `helix run`（本地跑 manifest）
- [ ] Admin UI：版本对比、灰度面板、Vault secret 管理
- [ ] JSON Schema 发布（VS Code/IntelliJ 自动补全）
- [ ] **MCP Server 写权限暴露**（messages_send / permissions_respond）—— Capability Uplift Sprint #5 的 Gate 期产出基础上 + 写权限 RLS 跨租户审计验证

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

#### M2-A Durable Execution — hardening（~3 周；范围明确化）

> **2026-05-20 范围明确化**：L2 context compressor + E.1 PostgresSaver 已交付 80%；M2-A 实际是 hardening 而非从零做。原"Context window 压缩策略（summarization）"已被 L2 覆盖，本子组只补真正新的部分。

参考：[architecture/subsystems/19-durable-execution.md](./architecture/subsystems/19-durable-execution.md)（如有）
- [ ] **小时级 long-running session 加固**（与 L2 compressor 集成；现状 50-turn 测试通过，扩到 100+turn / 小时级）
- [ ] **引擎重启可恢复加固**（E.1 PostgresSaver replay 在 stale checkpoint / partial state 下的边界场景）

历史项（已覆盖）：~~Context window 压缩策略（summarization）~~ → 已在 **Stream L.L2** 落地（PR #206）

#### M2-B Multi-Agent Orchestration（~4 周；重命名）

> **2026-05-20 重命名 + 范围明确化**：原 "M2-B Plan-Execute + HITL"，但 J.1 plan_execute + J.2 reflect + J.8 HITL 已经在 M0 Stream J 落地。M2-B 真正新的是 multi-agent fan-out + 静态检查 + 全局 deadline，故重命名 "Multi-Agent Orchestration"。

- [ ] **fan-out 并行 sub-agent**（J.4 M0 仅做顺序委派树；本项加并行扇出 + fan-in 聚合）
- [ ] **sub-agent 调用图静态检查**（构建期检测循环 / 深度爆炸 / 资源死锁）
- [ ] **全局 deadline 跨 agent 树**（父 deadline 自动下推到子 agent，超时整树取消）

历史项（已覆盖）：~~Plan-Execute 工作流模板~~ → 已在 **J.1**（PR M0）；~~LangGraph interrupt + 审批 UI~~ → 已在 **J.8**（M0）+ **H.3**（Admin UI 审批面板）

#### M2-C Memory archive 层（~2 周；范围明确化）

> **2026-05-20 范围明确化**：J.3 long-term memory + K6/K7 CRUD/DLQ + L2 summarization 已覆盖 working + summarization；M2-C 真正新的只是 archive 层（冷热分层 + 自动晋升 / 召回）。
>
> **[2026-05-27 关联]** Memory 短期 → 长期自动凝结引擎已作为 **Capability Uplift Sprint #7** 在 Gate 期完成基础设施（凝结引擎本体 + 防误学约束）；M2-C 直接对接凝结引擎输出的 long-term entries（archive 流水线接口已在 Sprint #7 预留）；M1 期间用 dogfood 数据调凝结触发策略，M2-C 启动时 archive 路径直接挂凝结后的 long-term 数据。

参考：[research/04-deerflow-source-analysis.md](./research/04-deerflow-source-analysis.md) §"Memory"
- [ ] **memory archive 表 + 冷热分层**（working hot in pgvector / archive cold in S3）
- [ ] **自动晋升策略**（按访问频次 / 时间衰减 → archive）
- [ ] **archive 召回路径**（按需 promote 回 working）

历史项（已覆盖）：~~working layer~~ → 已在 **J.3 + K6/K7**；~~summarization layer~~ → 已在 **L2 ContextCompressor**；~~consolidation engine~~ → 已在 **Capability Uplift Sprint #7（Gate 期）**

#### M2-D Eval Gate + 持续改进 pipeline（~3 周；合并 J.12 + J.13b/c）

> **2026-05-20 合并**：J.12 是 M2-D 的数据底座；J.13b 在线采样 + J.13c CI 回归门 也归到本子组。M0 内只交付 J.13a baseline 集 + L7 trajectory ObjectStore；M2-D 把它们接成完整 pipeline。

- [ ] **A/B Eval gate 上线前自动跑**（基于 J.13a baseline + L7 ObjectStore trajectory）
- [ ] **用户反馈（G.6）→ 自动加入 eval → 触发 prompt 改进 → 验证 → 上线**（来自 J.12 的 `eval_dataset` 策划层）
- [ ] **Quality dashboard**（每个 agent 当前质量得分；接入 K10 G.7 大盘）
- [ ] **J.13b 在线采样 + LLM-as-judge 配额**（M0 内可推迟到此处）
- [ ] **J.13c CI 回归门 + flakiness 缓解**（N 次重跑 + 阈值软门设计）

#### M2-E event_log Replay（~1.5 周；trace UI 砍）

> **2026-05-20 拆分 + 砍**：原 "M2-E Trace 时间线 + Session 回放" 两块。LangSmith 风格 trace 时间线 → Langfuse UI 已经提供，砍掉自建（属 (a) 重复造轮子）。保留 event_log replay（time travel）—— 是新能力。

- [ ] **event_log replay（time travel）**：基于 A.1/A.2 event_log + checkpointer 还原任意历史时刻 session 状态

历史项（已砍）：~~LangSmith 风格 trace 时间线视图~~ → **(a) 砍**：Langfuse UI（E.5）已提供，自建是重复造轮子

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
