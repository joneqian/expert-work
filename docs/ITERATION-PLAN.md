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
- [x] 按文档 03 的目录树创建空骨架（`packages/`、`services/`、`tools/`、`tests/`、`infra/`）（对账 2026-06-12：早已落地）
- [x] 根 `pyproject.toml` + uv workspace 配置（成员分别有自己的 pyproject.toml）（对账 2026-06-12）
- [x] 工具链固化：ruff + mypy + pytest + pytest-asyncio + pre-commit（对账 2026-06-12）
- [x] 把 `src/helix_agent/__init__.py` 占位删掉，转 `packages/helix-runtime/` 真正的包（对账 2026-06-12：src/ 已不存在）

**0.3 CI/CD pipeline**（落实 P0 #30、#31）
- [x] CI 工作流升级：lint + mypy + test + 镜像构建 + Trivy 镜像 CVE 扫描（对账 2026-06-12：CI 8 检 + sandbox-image（minimal）/-office build+smoke+Trivy + 周扫 cve-weekly；Trivy 已随 HX-10 PR3 #579 补齐）
- [x] 三环境配置框架：`environments/{dev,staging,prod}.yaml`（对账 2026-06-12：三文件在）
- [x] CodeQL 已生效（保留），追加 `pip-audit`、`pre-commit-ci`（对账 2026-06-12：CI Security/pip-audit + Pre-commit job 均在）
- [x] 加 `dependabot.yml` 的 pip ecosystem（对账 2026-06-12：以 `uv` ecosystem 形式落地——repo 用 uv 不用 pip，等效兑现）

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
- [x] **A.7 结构化日志规范**（落实 P0 #10）— 字段标准 + redaction 中间层。`HelixJsonFormatter` 强制 10 字段 schema（`_MANDATORY_FIELDS`）+ `ExtrasRedactor`（按 `tenant_config.pii_fields`）+ 缺失字段写 null+WARNING；字段规范成文于 20-observability §5.3（补全 `run_id` + null 语义 + redaction 说明）
- [x] **A.8 W3C Trace Context 规范**（落实 P0 #11）+ OpenTelemetry SDK 接入。OTel SDK + propagation 原语 + control-plane 入站提取 + **#600**：`HTTPSupervisorClient` 出站注入 + sandbox-supervisor `TraceContextMiddleware` 入站提取 + 跨服务 e2e 测（inject/extract 两半交汇于 traceparent wire 契约）。注入纪律=仅信任内部 hop（→supervisor），外部 egress/沙箱边界不注（设计 20-observability §5.8）
- [x] **A.9 指标体系**（落实 P0 #12）— Prometheus + 业务指标 + 技术指标 schema。`helix_*` validator（前缀 + label cardinality 拦截）已锁。**as-built catalog 成文**于 20-observability §5.2.1（102 实测指标据定义点逐个核，按子系统分组 + type + labels）+ §5.2.2 M1-target（subagent/hitl/eval 等未实现项）；消除原设计期清单漂移（`helix_llm_tokens_total`→`helix_llm_token_usage_total`、`tool_call_duration`→`tool_call_total`+`tool_latency` 等）。§5.4 SLO http 指标名同步校准

**网络与可靠性基础**（统一设计：28-reliability-primitives）
- [x] **A.10 全链路 TLS**（落实 P0 #9 in-transit）— 应用层 mTLS (MTLSVerifier + XFCC) + nginx 8443 TLS 终止 (`infra/nginx/nginx.conf` ssl_verify_client) + dev PKI (`tools/dev-certs/generate.py`：ca 5y/server·client 1y，gitignored) + compose `./dev-certs:/etc/nginx/certs` 挂载，全在。证书运维（生成/轮换/到期诊断）成文 [runbooks/tls-certs.md](./runbooks/tls-certs.md)；到期 metric `helix_tls_cert_expiry_seconds` 标 M1（20-observability §5.2.2）
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

- [x] **C.1 OIDC + JWT** 认证（落实 P0 #1）— `JWTVerifier` + `HTTPJWKSProvider`（`auth/jwt_verifier.py`）+ app.py `_build_default_jwt_verifier`（issuer/jwks 取 `settings.oidc_issuer`/`resolve_jwks_uri`）+ auth middleware Bearer 路径全接线；compose Keycloak 25.0 service（`--profile auth`）+ dev realm（`infra/keycloak/realm-helix-agent.json`：clients/roles/dev 用户）全在。端到端验证（起 Keycloak → 取 token → `/v1/me` Bearer → `is_system_admin`）成文 [runbooks/bootstrap-admin.md](./runbooks/bootstrap-admin.md)。`--profile auth` e2e CI 测 = staging 手动（CI 无 Keycloak，同 gVisor 7/7 staging-manual）
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
  - [x] **OBS-L1**（Mini-ADR，[ADR-0005 §6](./adr/0005-observability-stack.md)）— Langfuse 激活：dev compose 栈（`observability` profile）+ **入库前 PII 脱敏**（SDK `mask=`，secrets+对话 PII，默认全开）；runbook [docs/runbooks/langfuse.md](./runbooks/langfuse.md)；生产部署押 M1
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

> **前端覆盖债 backlog（2026-06-08 SE-8 评估期前端覆盖度全量对账发现；登记不实现，与 SE-8 自进化治理面无关，归 Stream H 后续）**：control-plane 仍有 4 处 operator-facing 能力后端已发但前端无/半可视化（已逐文件核实，非按 router 名猜；其余 `quota`/`sandboxes`/`feedback`/`first_admin`/`mcp_oauth`/`metrics`/`health` 等是 runtime 内部/终端用户/机器端点，正确地不做 admin UI，不计债）。这些缺口**不并入 SE-8**（保 SE-8 范围聚焦自进化治理，符合零技术债收尾不混 scope）。
- [x] **H.6 AgentDetail 4 个 tab 真实现（Runs / Skills / Triggers / Memory）** — **已交付**（2026-06-12，#581 设计 / #582 PR1 / PR2）。占位 `tab_coming_soon` 仅余 unknown-tab fallback；8 个 tab 全真实现。详设 [STREAM-H-DESIGN § 6.7](./streams/STREAM-H-DESIGN.md)。
  - [x] **H.6 设计先行**（#581）：STREAM-H-DESIGN § 6.7 详设——现状取证 12 条 file:line（envelope-vs-raw 逐端点核实）+ 4 条 Mini-ADR（H-10~H-13）+ 2-PR 切分
  - [x] **H.6 PR1（backend，#582）**：ThreadMetaStore agent 过滤 + RunStore thread_ids（空集≠None）+ SkillStore created_by_agent_name + TriggerStore agent_version + 3 端点 query params + `thread_window_capped` 信号 + version 单给 422 + 测试 +15
  - [x] **H.6 PR2（frontend，收尾）**：SDK 增参（listRuns agentName/agentVersion / listSkills createdByAgentName / listTriggers agentVersion + RunList.thread_window_capped）+ RunsTab（status 过滤+cap Alert+行跳 run detail）/ SkillsTab（agent-authored+行跳 skill detail）/ TriggersTab（version-bound+enabled Badge）/ MemoryTab（kind 过滤+user-scope 明示 H-13）+ AgentDetail 接线去占位 + i18n 双语 4 namespace + Storybook 5 stories + vitest 7 测 + Playwright tab 冒烟；零债 6 条
- [x] **H.7 Knowledge 知识库治理面** — **已交付**（2026-06-12，#586 设计 / PR1）。后端 `/v1/knowledge` 零改，纯前端消费。详设 [STREAM-H-DESIGN § 6.9](./streams/STREAM-H-DESIGN.md)。
  - [x] **H.7 设计先行**（#586）：§ 6.9 详设——取证 9 条 file:line + Mini-ADR H-17~H-19 + 纯前端 2-PR
  - [x] **H.7 PR1（实现，收尾）**：SDK knowledge.ts（6 方法全 raw + 扩展白名单常量/校验）+ KnowledgeAdmin 单页 master-detail（H-17：bases 表+create modal 409/400 映射+删库连带提示 / documents 表 4 态 Tag+failed error tooltip+行删）+ Upload accept 白名单+beforeUpload 预校验+503 embedder 文案 + ingest 条件轮询（H-18：未终态 5s/全终态停/切 base 清 timer）+ H-19 scope note（非 home scope 顶部声明）+ router/Sidebar（BookOpen）/CommandPalette（g k）/i18n 双语 29 key + Storybook 2 stories + vitest 6 测 + Playwright 冒烟；顺手矫正 ArtifactsList/KnowledgeAdmin 用 `App.useApp()` 注入 message（house style，静态 message 不渲染于测试环境）
  - [ ] **H.7-F1 follow-up（后端能力，另立项）**：knowledge 跨租户治理（router 加 tenant_id query + Stream N 接线）
- [ ] **KB 知识库商业级补齐** — 引擎已生产级（hybrid+RRF+rerank+引文+语义切块），补管理面 + 检索透明 + 摄取持久化。范围 A「管理面 + 检索透明」+ 四能力（命中测试 / 片段预览 / per-KB embedding 安全重建 / 文档进度+重试+重摄取）。设计见 plan `kb`。
  - [x] **M1-PR1 schema + DTO**：migration 0100（knowledge_base 元数据/检索配置/embedding 钉版列 + CHECK）+ 0101（knowledge_document durability + content BYTEA + 部分索引）+ ORM + protocol（RetrievalMethod/ScoredChunk/DEFAULT_RETRIEVAL_TOP_K + KnowledgeBase/Document 扩字段）+ 17 protocol 测
  - [x] **M1-PR2 store 层**：create/update_base + base_stats(_many) + search_scored/keyword_search_scored + list_chunks + claim_documents_for_ingest(CAS) + get_document_content + upsert_document(bytes)
  - [x] **M2-PR3 retriever**：per-KB 配置 + 分数 + recall_source + 阈值过滤（加性，工具路径兼容）
  - [x] **M2-PR4 API**：GET 单 KB+stats / PATCH / 片段预览 / 检索命中测试端点（挂 app.state retriever）
  - [x] **M3-PR5 embedding 钉版 + needs_reindex + reindex 端点**（重嵌已留 chunk 文本，事务化回滚）
  - [x] **M3-PR6 durable 摄取 worker + 崩溃恢复 reaper + reingest 端点 + content BYTEA**（真 PG 并发 exactly-once + 崩溃恢复测）
  - **后端 6 PR 全交付(打包为一个后端 PR);118 unit + 18 SQL 集成全绿。**
  - [x] **M4 前端**：SDK(扩类型+方法,修 ingesting→processing)+ list/detail 拆分(KnowledgeDetail tabs 文档/检索测试/设置)+ 富创建 Modal(描述+高级检索默认值)+ DocumentsTab(本地化状态+真进度轮询+重试+重摄取+Dragger 多文件)+ 片段预览抽屉 + **检索命中测试面板**(score 进度条+recall-path tag)+ 设置 tab(PATCH+embedding+重建)+ i18n 三写 + stories + vitest(KnowledgeAdmin/Detail)+ e2e+axe。tsc-b/vitest 495/build/storybook/playwright 全绿。
  - **KB 知识库商业级补齐 = 全交付(后端 #836 + 前端);直击「做薄」屏的产品级修复。**
  - 维持推迟（backlog）：RBAC 硬化（行为变更 403 风险）/ KB 改名（refs 改按 id）/ URL·网页摄取 / 上传配额 / 去重 / OCR / 平台共享 KB / 文档级权限 / 字节下沉对象存储
- [x] **H.8 Artifacts 运行产物治理面** — **已交付**（2026-06-12，#584 设计 / PR1）。后端 `/v1/artifacts` 零改，纯前端消费。详设 [STREAM-H-DESIGN § 6.8](./streams/STREAM-H-DESIGN.md)。
  - [x] **H.8 设计先行**（#584）：§ 6.8 详设——取证 9 条 file:line + Mini-ADR H-14~H-16 + 纯前端 2-PR
  - [x] **H.8 PR1（实现，收尾）**：SDK artifacts.ts（5 方法全 raw + `filenameFromDisposition` RFC 5987 解析）+ ArtifactsList 双态（home=我的产物全功能 / cross-tenant=只读聚合带 tenant/user 列无行动作，H-14）+ versions 抽屉（NULL digest 显 "—"）+ download（axios blob+objectURL，H-15）/delete（软删 Popconfirm）/patch（kind 内联 Select，同值不发请求 H-16）+ router `/artifacts` + Sidebar + CommandPalette（g f）+ i18n 双语 + Storybook 3 stories + vitest 9 测 + Playwright 冒烟
  - [ ] **H.8-F1 follow-up（后端能力，另立项）**：租户 admin 代管他人产物（需新端点 + 权限模型，超出前端债范围）
- [x] **H.9 Rate Card 计价管理面（system_admin）** — **已交付**（2026-06-12，#588 设计 / PR1，**H.6–H.9 前端债全清**）。详设 [STREAM-H-DESIGN § 6.10](./streams/STREAM-H-DESIGN.md)。
  - [x] **H.9 设计先行**（#588）：§ 6.10 详设——取证 8 条 file:line + Mini-ADR H-20~H-22 + 纯前端 2-PR
  - [x] **H.9 PR1（实现，收尾）**：SDK rate_card.ts（5 方法全 envelope + `microsPerTokenToUsdPerMillion` 换算）+ SettingsRateCard（isSystemAdmin 门控不发 fetch（H-22）/ 列表+provider/model 过滤+include_expired / create modal 全字段+until>from 预校验+µ$ 换算只读提示（H-21）/ 编辑抽屉只开放 Patch 可变字段+身份字段只读展示+重定价新建引导（H-20）/ delete 谨慎文案）+ router `/settings/rate-card` + Sidebar（Banknote）+ i18n 双语 35 key + Storybook + vitest 5 测 + Playwright 门控冒烟

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
- [x] **Stream J（Agent Harness 能力补全）15 子项完成** —— 26 维能力矩阵无缺口（对账 2026-06-12：J.1-J.15 全勾；后续 HX 评估的补强亦已收官）
- [ ] canonical 能力 agent 跑通 + staging 冒烟（便宜模型端到端真实 run）
- [ ] 测试金字塔达标：unit ≥ 85%、integration ≥ 70% 关键路径、E2E 5-10 场景
- [ ] 7 条沙盒安全验证用例全部通过
- [x] SLO 第一个版本写入文档；P0 告警全部接入（对账 2026-06-12：`docs/runbooks/slo.md` 13 条 SLO + `tools/observability/rules/` sli/alerts/uplift 三文件在册）
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

- [x] **#1 Cron prompt 注入扫描（含隐形 Unicode）** —— `helix-common` 抽威胁模式库 + `services/control-plane/api/triggers.py` create/update 严扫 + `trigger_firing.py` 拼完 skill 后宽扫 + 新 audit action `TRIGGER_PROMPT_INJECTION_BLOCKED`。借鉴 Hermes `tools/cronjob_tools.py:68-200` 的双层扫描思路。**2026-05-27 完成（PR #307）**。
- [x] **#2 Memory 投毒防御 + drift backup** —— `MemoryStore.write/recall` 复用 #1 威胁模式库；写时扫拒入（可选 strict mode）+ 读时扫中毒条目替换为 `[BLOCKED:...]` 占位符（live 保留原文给用户审）+ drift detection 检测外部直改 DB；新 audit action `MEMORY_INJECTION_BLOCKED` / `MEMORY_DRIFT_DETECTED`。**2026-05-27 完成（PR #308）**。

**P1 — Operator / IDE experience**

- [x] **#5 MCP Client HTTP/SSE transport（接入外部 MCP 生态，原"MCP Server"已推翻）** —— 2026-05-27 复审重定向：原方向"暴露 MCP Server 给 Claude Code / Cursor"违反 helix server-side 多租户 backend 定位，推翻。新方向：扩 `MCPClient` transport（stdio only → 加 HTTP / SSE / StreamableHTTP）让 agent 沙箱能接入 2026 年公开 MCP 生态（GitHub / Postgres / Linear / Notion / Slack）。`MCPServerConfig` 加 `transport` 字段 + secret 隔离 + 远端 server 失败模式（timeout + retry + circuit breaker）。**2026-05-27 完成（PR #311 设计 / PR #312 实施）**。M1-I 写权限暴露推迟（仍记入 backlog，但实际由 MCP 远端 server 提供）。

**P1 — Memory 系统深化**

- [x] **#6 Memory hybrid retrieval（向量 + 全文 RRF）** —— `memory_item` 加 tsvector 列（migration 0040）+ 自动 trigger 维护；`MemoryStore.recall()` 直接 port `J.5 KnowledgeRetriever`（hybrid + RRF rerank）；K.K12 eval baseline 重跑 + 锁新 baseline；per-tenant manifest 可关闭 hybrid 回退纯向量。**2026-05-27 完成（PR #309）**。
- [x] **#8 Memory frozen snapshot / 前缀缓存优化** —— `memory_recall_node` 加 `per_session` 召回模式（vs 默认 `per_turn`）；L.L1 prompt caching middleware 适配 cache_control 加在 memory block 末尾；manifest `policies.memory_recall_mode` 字段。**2026-05-27 完成（PR #310）**。

**P2 — Skill 库进化基础**

- [x] **#3 Skill 附属文件 + Claude Code 标准 SKILL.md + Progressive Disclosure + 多层威胁防御** —— `skill_version.supporting_files` JSONB 列（migration 0042，5 MB cap）+ Claude Code 标准 SKILL.md 格式（YAML frontmatter + `helix:` 命名空间扩展，跨平台互通）+ progressive disclosure（per-skill `lazy_load` flag，可选 body 懒加载到 `skill_view` 工具）+ 单文件 PUT/DELETE supporting-files API + ZIP 双格式 read（向后兼容）+ U-21 drift detection（blake2b content_hash + 读时校验 → BLOCKED）+ U-22 obfuscation 防御（base64 / NFKC / 空格归一 4 variants）+ U-23 中文 prompt injection patterns（12 cn_* 规则）+ U-24 high-risk publish gate（含 `exec_python` / `http` / `scripts/*` → DRAFT→ACTIVE 必 admin）+ Admin UI 双栏 Monaco 编辑器（5 mutation 路径 + 高危徽章 + Lazy 调试视图）。**Mini-ADRs U-14 ~ U-24**（PR A 设计 #313 + PR B 设计补强 #314 + PR B backend #315 + PR C Admin UI #316）。**2026-05-28 完成**。**J.7b-6 supporting files 已并入,M1-K J.7b 仅剩 7 项**;**J.7b-1 visibility / fork / promote 三大支柱**在 2026-05-28 预约定到 STREAM-J-DESIGN § 15.7（PR #319），M1-K design phase 直接基于此展开。
- [x] **#4 Curator 自动状态机** —— `SkillRow` 加 `pinned: bool` + `last_used_at: timestamptz` + `state_changed_at: timestamptz`（migration 0043）+ `tenant_config` 加 `skill_stale_days` / `skill_archive_days`（migration 0044，cross-field validator + DB CHECK）+ 新建 `services/control-plane/skill_curator.py` 每天 03:00 UTC 周期 worker 四条状态机路径（active→stale / stale→archived / pin 屏蔽 / stale→active 自动复活）+ `ThrottledActivityRecorder` 双路径 activity 跟踪（_load_skills + skill_view）+ Admin UI Pin 按钮 + 状态分组 + 距 stale ETA hint + Tenant Config 阈值显示 + 5 个新 audit action + 4 recording rules + 2 alerts + runbook 7 节。**Mini-ADRs U-25 ~ U-32**（PR A 设计 #317 + PR B 实施 #318）。**2026-05-28 完成**。**基础设施 Gate 期完成**；启用阈值调参等 J.7b-1 上线后看真实膨胀率（M1-K 期间，默认 30/90 可能改 7/30）。
- [x] **#7 Memory 短期 → 长期凝结引擎（基础设施）** —— `MemoryItemRow` 加 4 列（`status` Literal[transient/consolidated/archived] + `consolidated_into: UUID|None` + `consolidated_from: list[UUID]` JSONB + `last_reviewed_at: timestamptz|None`，migration 0045）+ `tenant_config` 加 4 阈值字段（`memory_consolidation_min_cluster_size` / `memory_consolidation_similarity` / `memory_purge_enabled` / `memory_purge_min_age_days`，migration 0046）+ 新建 `services/control-plane/memory_consolidator.py` 每 4h 周期双 pass worker：SUB-PASS 1 hybrid clustering（embedding 召回候选 cluster → LLM 验证 + 总结 + 防误学三合一 prompt），SUB-PASS 2 lone-item noise purge（30 天 + 0 召回 + last_reviewed_at 三保护）+ Hermes 4 类 anti-mislearn + helix 2 类扩展（time-bound state / credential-shape）+ `MemoryStore.archive()` abstract method 预留 M2-C + `retrieve()` 默认 WHERE 跳被凝结的 raw transient + `MemoryConsolidationPolicy.aux_model` 独立于 ContextCompressor + 7 audit actions 双 Literal 同步 + 7 metrics + 5 recording rules + 3 alerts + runbook 9 节。**Mini-ADRs U-33 ~ U-42**（PR A 设计 #321 + PR B 实施）。**3 个不可逆 ADR 2026-05-28 用户拍板：数据模型 / 触发机制 / 防误学层级**。**2026-05-28 完成**。**Aux 模型默认 no-op 占位**（schema + worker + audit + 可观测均产品级；真 LLM 适配器作为 M1 跟进项 wire；详见 runbook § 3）。**基础设施 Gate 期完成**；触发阈值（default min_cluster_size=3 / similarity=0.85 / purge_age=30d）+ 凝结频率（default 4h）M1 dogfood 数据反过来调。

**Sprint Verification**：

- 8 项按零债 6 条核验（无 TODO / 测试达标 / 文档同步 / 可观测齐全 / CI 全绿 / bug 不遗留）
- 每项 PR merge 前 K.K12 + J.13a baseline 回放无退化（≥ 5% 退化卡 PR）
- 30 天稳定性观察期不被搅动（SLO 实时采集，破任一立刻 retro）
- schema 变更（#6 migration 0040 → #3 migration 0042 → #4 migrations 0043+0044）**串行上 main**，不并行降风险

**进度（2026-05-28）**：**8/8 全部闭环** —— #1 #2 #3 #4 #5 #6 #7 #8 全部 shipped。Capability Uplift Sprint 收官（M1 期间仅做阈值调参 / Aux 模型 wire，不再新能力）。

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

### Stream O — Credentials & Provider Catalog（~2 周；与 30 天稳定性观察期并行）

参考：[streams/STREAM-O-DESIGN](./streams/STREAM-O-DESIGN.md)（设计先行）

> **2026-05-28 用户提出**：Capability Uplift Sprint #7 凝结引擎 ship 后,发现"系统模块的 LLM 配置"分散在 4 层（env / tenant_config.model_credentials_ref / agent manifest / 各 caller 各自处理），没有统一面 + 没有"租户用自己模型"的合规通道。Sprint #7 aux 模型 wire 是 Stream O PR 1 的第一个 caller 落地点。
>
> **4 个不可逆决策**（2026-05-28 用户拍板）：(1) 平台只锁 provider，model 名 tenant/agent 可选；(2) credentials_mode 切换走 API gate（缺凭证 403 fail fast），不允许运行期 401；(3) mode 切换立即生效（无 caching）；(4) MCP servers 推到 PR 3，本 Stream 只覆盖 LLM provider + tool API key。
>
> **核心原则**：
> 1. **凭证 vs 模型名分离** — `credentials_mode` 只决定凭证来源，不影响模型名选择
> 2. **All-or-nothing 强制** — 没有"embedding 用平台 key、主模型用租户 key"的混搭
> 3. **Provider/Tool 白名单** — 租户在 tenant mode 下只能给平台已支持的配凭证
> 4. **凭证缺失硬失败** — tenant mode 缺凭证 → 401 fail-fast，不静默回退

- [x] **O.1 Platform Catalog** — settings 加 `supported_providers: list[Provider]` + `platform_provider_credentials` + `supported_tools` + `platform_tool_credentials`；启动期 fail-fast 校验（`_validate_platform_catalog` in app.py）。**Mini-ADR O-1**。**2026-05-28 完成（PR #?）**
- [x] **O.2 tenant_config schema 扩展** — 加 `credentials_mode: Literal["platform","tenant"]` + `tool_credentials` JSONB；migration 0047；DB CHECK；**保留 `model_credentials_ref` 字段名不变**（rename 改动面 7+ 文件含 admin-ui，业务价值低于代价，per [memory:business-value-over-implementation-cost]）。**Mini-ADR O-2**。**2026-05-28 完成**
- [x] **O.3 CredentialsResolver** — 新建 `helix-common.credentials.CredentialsResolver`；`resolve_provider(tenant_id, provider)` / `resolve_tool(tenant_id, tool)`；mode 决策 + tenant mode 缺凭证 raise + no silent platform fallback。**Mini-ADR O-3**。**2026-05-28 完成**
- [x] **O.4 All-or-nothing 校验（2 gates）** — `_validate_credentials_mode_switch` 在 PUT `/v1/tenants/{id}/config` 切换 mode 时校验已用 provider/tool 凭证完整性（403 `CREDENTIALS_MODE_SWITCH_INCOMPLETE` + missing 列表）+ agent manifest publish 时 provider 白名单校验（403 `MANIFEST_PROVIDER_NOT_SUPPORTED`）。**Mini-ADR O-4**。**2026-05-28 完成**
- [x] **O.5 Legacy settings deprecation 标注** — `embedding_api_key_ref` / `rerank_api_key_ref` / `tavily_api_key_ref` 字段加 deprecation 注释 + 指明 platform_*_credentials 迁移路径；callers 仍读 legacy 字段（per-tenant 改造留 PR 2，影响面大）。**Mini-ADR O-5**。**2026-05-28 完成**
- [x] **O.6 Caller 集成（PR 1 范围）** — consolidator aux model：新建 `LLMRouterAuxModelAdapter`（`control_plane.aux_model_adapter`）走 CredentialsResolver + `build_llm_router`，替换 Sprint #7 `_NullConsolidatorAuxModel`；`ConsolidatorAuxModel.__call__` 加 `tenant_id` 参数；wire 在 app.py（默认 provider 缺凭证时降级 null + log warning）。**Mini-ADR O-6**。**2026-05-28 完成**
- [x] **O.7 Admin UI Credentials 面板（PR 2b）** — `SettingsTenantCredentials.tsx`（挂 `/settings/credentials` + Sidebar 入口）：mode 切换器(dry-run 预览缺项后再 PUT) + Provider 凭证表 + Tool 凭证表(平台状态 / 租户 ref / used-by / 编辑弹窗)；镜像 `SettingsTenantQuotas` + `--hx-*` token + zh-CN/en + Storybook(3 stories) + Playwright e2e + axe。**Mini-ADR O-7**。**2026-05-29 完成**
- [x] **O.13 Credentials 面板后端端点（PR 2b）** — `GET /v1/tenants/{id}/config/credentials`(组合视图:catalog provider/tool × platform-configured × 租户 ref × used-by × mode,不回显 secret 值) + `POST .../config/credentials-mode/dry-run`(切 tenant 前预览缺项,不落库);写复用现有 `PUT /config`;`_collect_used_*` 重构出 per-agent helper 供 used-by 计数复用。**Mini-ADR O-13**。**2026-05-29 完成**
- [x] **O.8 Audit + 可观测** — 4 audit actions（CREDENTIALS_MODE_CHANGED / PROVIDER_CREDENTIALS_UPDATED / TOOL_CREDENTIALS_UPDATED / CREDENTIALS_RESOLVE_FAILED）；5 metrics + gauge；3 recording rules（resolve_failure_rate + tenant_mode_adoption_ratio + legacy_fallback_rate）+ 2 alerts（CredentialsResolveFailureSpike P1 + LegacyCredentialsFallbackPresent P3）。**Mini-ADR O-8**。**2026-05-28 完成**
- [x] **O.9 runbook** — `docs/runbooks/credentials.md` 7 节（概念 / 平台 setup / 2 alert 诊断 / mode 切换流程 / Sprint #7 aux wire / M1 follow-ups）。**2026-05-28 完成**
- [x] **O.10 Per-tenant resolving callers（PR 2a）** — embedder / reranker / web_search 迁到 `CredentialsResolver`：`Embedder.embed(*, tenant_id)` / `Reranker.rerank(*, tenant_id)` / `TavilyClient.search(*, tenant_id)` 协议签名扩展 + `ResolvingEmbedder` / `ResolvingReranker` / `ResolvingTavilyClient` 包装类（control-plane glue，结构化实现 orchestrator 协议）；全 call site 透传 tenant_id（memory recall/writeback / knowledge tool / ingestion+chunking / DLQ worker / consolidator adapter）。reranker 缺凭证优雅降级到 RRF-fused。**Mini-ADR O-9**。**2026-05-29 完成**
- [x] **O.11 Legacy → effective catalog 派生（PR 2a）** — `Settings.effective_*` gap-fill：legacy `embedding_api_key_ref` / `rerank_api_key_ref` / `tavily_api_key_ref` 并进 catalog，未 opt-in Stream O 的部署透明走 platform mode（零回归）；显式 Stream O 配置优先，legacy 只补缺；startup `_signal_legacy_credentials_derivation` warn + meter（per-role `record_legacy_credentials_fallback`）。**Mini-ADR O-10/O-11**。**2026-05-29 完成**
- [x] **O.12 Mode-switch gate 完整性（PR 2a）** — `_collect_used_providers` 在任一 agent 声明 `memory.long_term` 时把 `settings.embedding_provider` 计入 used（infra provider 缺凭证必崩，必须 gate）；rerank 不进 gate（优雅降级）。**Mini-ADR O-12**。**2026-05-29 完成**
- [x] **O.14 MCP allowlist 强制 + 凭证 schema 基础（PR 3a）** — `mcp_allowlist` 最终语义(空=不限制/非空=白名单)并**强制**:`ToolEnv.mcp_allowlist` + `_register_mcp` 按 server 名过滤;`make_agent_builder` 经 `make_mcp_allowlist_provider` 按租户读 `mcp_allowlist`,`AgentBuilder` 协议加 `tenant_id`,`_build` per-tenant `replace(tool_env, mcp_allowlist=…)`。纯过滤、不新建连接、用平台 token。附带落地 M1 基础:`tenant_config.mcp_credentials`(migration 0048) + `mcp_auth.resolve_mcp_bearer_ref`(forward-reserved,本次不接运行时)。**Mini-ADR O-14**。**2026-05-29 完成**
- [ ] **O.15 / O.16 MCP per-tenant secret（推 M1）** — bearer token 按租户解析需 per-tenant 持久连接 + 懒缓存 + 驱逐(= M1 per-tenant-pool 架构,代码注释本就标 M1);严格闸门 + view 端点 MCP 字段 + Credentials 面板 MCP 分类一并随之。**实现期复议改判降级**(读连接池代码后,2026-05-29),schema + resolver 已作 M1 明文基础。**Mini-ADR O-15/O-16**

**Stream O Verification**：8 项按零债 6 条核验；Platform Catalog 启动校验在 fail-fast 路径上不绕过；CredentialsResolver 双 mode 4 路径 fixture 全过；mode 切换缺凭证返回 403 + 完整 missing 列表；agent manifest publish 引用未支持 provider 立刻 reject；Sprint #7 consolidator aux 在 dogfood 启动前真接 LLMRouter（M1 dogfood 凝结数据采集前提）。

**PR 拆分**：
- **PR A**（设计）：本 Stream § + ITERATION-PLAN 同步；无代码（~1 天）
- **PR B**（实施）：migration 0047 + Platform Catalog + tenant_config schema + CredentialsResolver + all-or-nothing 2 gates + consolidator aux LLMRouter adapter + Admin UI Credentials 面板 + 4 audit + 5 metrics + 2 alerts + runbook + 单测 + e2e + Legacy 派生（~2 周）

**后续 PR**（Stream O 范围内但 PR 1 不做）：
- **PR 2a**（~3-4 天）✅ **2026-05-29 完成**：embedder / reranker / web_search 迁到 resolver（per-tenant 改造）+ legacy effective-catalog 派生 + mode-switch gate 补 embedding_provider（O.10/O.11/O.12）
- **PR 2b**（~3-4 天）✅ **2026-05-29 完成**：Admin UI Credentials 面板（mode 切换器 + dry-run + provider/tool 凭证表）+ 2 后端只读/预览端点（O.7 / O.13）
- **PR 3a**（backend）✅ **2026-05-29 完成**：MCP allowlist 强制 + mcp_credentials schema(0048) + mcp_auth resolver 基础（O.14）。**Stream O 在此收口**（per-tenant MCP secret 整体推 M1）。
- **M1 — MCP per-tenant secret**：O.15 per-tenant bearer 连接(懒建+缓存+驱逐,= per-tenant pool 架构) + O.16 严格闸门 + Credentials 面板 MCP 分类 + Admin UI allowlist 编辑器。跟 M1 沙箱/运行时改造一起做。

---

### Stream P — E2E Readiness（平台入驻 + 端到端测试 Phase 0–6 跑通；~2-3 周；M0→M1 Gate 前置）

参考：[streams/STREAM-P-DESIGN](./streams/STREAM-P-DESIGN.md)（设计先行）

> **2026-05-29 用户提出**：准备按 [`canonical-agent-e2e-test.md`](./runbooks/canonical-agent-e2e-test.md) 跑 M0→M1 Gate 端到端测试。`/goal` 把目标定为"评估跑通整个 E2E（Phase 0–7）到底还缺多少 + 完整迭代规划"。经 5 个 Explore agent 逐 Phase 体检：这份 SOP 写于 #306，**早于 Stream N(system_admin) 和 Stream O(凭证/provider catalog)**，前半段(Phase 0)既无文档又有代码缺口；Phase 1/2 现就能跑(仅文档)；Phase 3/4/5 是小代码+UI+文档；Phase 6 可观测接线缺；Phase 7 需 staging Linux。
>
> **本次范围**：dev 跑通 **Phase 0–6**；**Phase 7**（staging Linux 安全/数据保护）单列后续。
>
> **Phase 0 决策**（管理员入驻）：(1) "配模型"= 平台级运行时配模型 UI/API（DB 覆盖 env、DB 优先，向后兼容）；(2) "建租户"= 真端点 `POST /v1/tenants`（system_admin 门控）+ Admin UI；(3) bootstrap = CLI（`python -m control_plane.bootstrap_admin`）+ runbook。
>
> **E2E 决策**（Phase 1–6）：(4) dev 真实 turn = 配真 LLM key（最简，`mock-upstream` 只是 echo server 非 mock LLM，不造 mock-LLM）；(5) 本次只到 Phase 0–6，Phase 7 单列；(6) Phase 4 审批门用现有 `policies.approval_required_tools`（精确工具名，`hitl.triggers.match` 正则运行期不存在，不新建）。
>
> **硬缺口**：① 首个 system_admin 鸡生蛋；② 无建租户 API；③ 本地登录无 recipe；④ E2E 文档过期(`POST /v1/tenants` 虚构 / `--profile full` 起不全 / `status=awaiting_approval` 应 `paused` / `hitl.triggers.match` 已弃);⑤ canonical manifest 没入仓;⑥ 平台配模型只能改 env;⑦ Phase 3 `POST /v1/sandboxes/reap` 端点缺;⑧ Phase 5 Playground 无传图 UI;⑨ Phase 6 sandbox scrape job + Grafana 4 panel 缺。
>
> **范围纪律**（simplicity first）：显式不做 — mock-LLM 服务、HITL 正则、凭证轮换/版本化、诊断页、批量导入、manifest JSON Schema/dry-run/文件上传 UI、跨租户凭证共享、租户级 tool/skill allowlist、独立 Artifacts UI 页。Phase 7 单列。

- [x] **P.1 建租户：不加主表，POST = 显式写第一行 tenant_config（PR C/D）** — **Mini-ADR P-1**
- [x] **P.2 建租户权限 = is_system_admin inline，不加 RBAC `tenant` Resource（PR D）** — **Mini-ADR P-2**
- [x] **P.3 `TenantConfigStore.create` + `TenantConfigAlreadyExistsError`（已存在 409，不复用 upsert）（PR C）** — **Mini-ADR P-3**
- [x] **P.4 建租户只建 tenant_config 一行（不连带 quota/binding/user）（PR D）** — **Mini-ADR P-4**
- [x] **P.5 tenant_id 默认服务端 uuid4，也接受客户端传（PR D）** — **Mini-ADR P-5**
- [x] **P.6 bootstrap CLI `python -m control_plane.bootstrap_admin`（复用 Settings/Store/bypass_rls，幂等，不建 platform 租户）+ runbook（PR B）** — **Mini-ADR P-6**
- [x] **P.7 平台凭证存储 = DB 行覆盖 env seed、DB 优先（env 仍 fallback，未写 DB 行为不变）（PR F/G）** — **Mini-ADR P-7**
- [x] **P.8 平台凭证只存 ref（secret://、kms://），拒绝明文（validator）（PR F）** — **Mini-ADR P-8**
- [x] **P.9 `PlatformCredentialsService`（TTL 缓存 merged 视图）+ resolver getter（additive）+ 写端点 invalidate（PR F/G）** — **Mini-ADR P-9**
- [x] **P.10 boot `_validate_platform_catalog` 对 env 字段语义不变（仍 fail-fast），不加新 fatal boot check（PR G）** — **Mini-ADR P-10**
- [x] **P.11 平台端点 is_system_admin inline 门控 + handler 在 bypass_rls_session 内（PR H）** — **Mini-ADR P-11**
- [x] **P.12 删除受被引用检查门控（agent 在用/env 定义则 409），enabled=false 软停用永远可用（PR H）** — **Mini-ADR P-12**
- [x] **P.13 平台配置 Admin UI `SettingsPlatformConfig.tsx`（非 tenant-scoped + isSystemAdmin 门控）+ 建租户 UI `SettingsCreateTenant.tsx`（PR E/I）** ✅ shipped
- [x] **P.14 canonical manifest 入仓（用 `approval_required_tools` + `supports_vision`）（PR J）** ✅ `manifests/canonical-agent/v1.0.0.yaml` + CI guard `test_canonical_manifest.py`
- [x] **P.15 dev 真实 turn 用真 LLM key：local_dev SecretStore 文件（非进程 env）+ compose 挂载 `infra/dev-keys` + `.example` 模板 + 文档（不入仓明文）（PR J）** — **Mini-ADR P-13** ✅ 修正了 PR B 把 key 走 `ANTHROPIC_API_KEY` 进程 env 的失效 recipe
- [x] **P.16 Phase 3 `POST /v1/sandboxes/reap?force=true` admin 端点（is_system_admin → supervisor reaper，返 reaped_count，volume 保留）（PR K #338）** — **Mini-ADR P-14** ✅ shipped
- [x] **P.17 Phase 6 可观测接线：确认 orchestrator 指标在 control-plane /metrics + sandbox-supervisor scrape job（+确认其 /metrics）+ Grafana 01-overview 补 4 panel（PR L）** — **Mini-ADR P-15** ✅ shipped
- [x] **P.18 Phase 5 Playground 传图 UI：`PlaygroundTab.tsx` 文件选择 → POST /uploads → image_ref 进 turn（PR M #339）** — **Mini-ADR P-16** ✅ shipped
- [x] **P.19 Phase 4 贴合现状：manifest 用 `approval_required_tools`(✅ PR J)；SOP query 改 `status=paused`(✅ PR N)；不做正则** — **Mini-ADR P-17** ✅
- [x] **P.20 E2E SOP 全面重写（Phase 0–6 修正 + Phase 7 单列）（PR N #341，capstone）** — **Mini-ADR P-18** ✅ shipped

**Stream P Verification**：dev 跑通 Phase 0–6 — 起栈(full+auth+observability)+真 key → bootstrap admin 幂等 → OIDC 登录 `/v1/me` is_system_admin → 平台配置页填 provider → `POST /v1/tenants`(201/403/409) → 注册 manifest → **P1** baseline diff 空+J.1/3/6 过 → **P2** 跨 thread 召回+cross-tenant 隔离 → **P3** reap 后文件仍在+cold_start P95<5s → **P4** artifact 跨 thread+危险工具 PAUSED+`status=paused`+ApprovalCard+resume+audit → **P5** Playground 传图描述+ask_image → **P6** 8 项 query 有数据+Grafana 8 panel。每 PR 零债 6 条;平台凭证 env-only→DB 可变向后兼容。

**PR 拆分**（~13 PR；可酌情合并 C+D、F+G、K+L）：
- **PR A**（设计）✅ **[#328]**：本 Stream § + STREAM-P-DESIGN + backlog；无代码
- **PR B**（bootstrap）：CLI + runbook + dev-key recipe + 测试（独立）
- **PR C → D → E**（建租户）：store+protocol → 端点 → 前端
- **PR F → G → H → I**（平台配模型）：持久层+resolver → service+boot → API → 前端
- **PR K**（Phase 3）：`POST /v1/sandboxes/reap` admin 端点 + 集成测（独立）
- **PR L**（Phase 6）：sandbox scrape job + 确认 orchestrator 指标 + Grafana 补 4 panel（独立）
- **PR M**（Phase 5）：Playground 传图 UI（独立）
- **PR J**（manifest+key）：canonical manifest + dev key 注入 + 登录 recipe
- **PR N**（capstone）：E2E SOP 全面重写（Phase 0–6 + Phase 7 单列）

---

### Stream ACCT — 账号自助化（bootstrap 平滑 + 平台账号管理 UI）— ✅ 完成 2026-06-17

> 延伸 Stream P/N/R。起因:评估 SSO 是否过度设计 —— 结论 **架构本身不过度**
> (Keycloak OIDC 单 IdP + helix `role_binding` 双层权限是标准多租户形态),但暴露两处
> 运维摩擦:①生产无平台管理账号要手跑脚本;②平台级账号/角色管理无 UI。本 Stream 消除
> 这两处摩擦,不改 IdP 架构。详设 [STREAM-ACCT-DESIGN](./streams/STREAM-ACCT-DESIGN.md)。

- [x] **ACCT.1 邮箱首登自动升 system_admin（P1）** — **Mini-ADR ACCT-1**：`JWTClaims`/`Principal`
  接入 `email`+`email_verified`(OIDC claim);`settings.bootstrap_admin_email`(env
  `HELIX_AGENT_BOOTSTRAP_ADMIN_EMAIL`);`maybe_bootstrap_system_admin` 零-admin 门控 +
  `email_verified` 必检 + 大小写不敏感匹配,复用 `bootstrap_system_admin` 幂等写
  (bypass_rls);AuthMiddleware JWT 路径接线 + app.py 注入。脚本退为 break-glass。
  单测五条件矩阵 + middleware 端到端。
- [x] **ACCT.2 平台管理员管理页（P2）** — `apps/admin-ui` `SettingsPlatformUsers.tsx`
  (`/settings/platform-users`):system_admin 门控,接已有 `/v1/role_bindings` SDK
  列/授予(UUID 校验 + Keycloak `sub` 提示)/撤销(Popconfirm+自撤警告);SE-8 全接线
  (router/Sidebar/CommandPalette/i18n 双语/stories/vitest/e2e)。
- [x] **ACCT.3 跨租户成员只读视图（P3）** — `TenantMemberStore.list_all_tenants`
  (sql 用 `SET LOCAL ROLE audit_reader` 跨 FORCE-RLS,migration **0085** GRANT SELECT);
  `GET /v1/members?tenant_id=*` is_system_admin 门控;admin-ui `scope=*` 跨租户只读视图
  (加 `tenant_id` 列、隐藏写操作)。真 PG 集成测实证 SET ROLE 跨租户。

**Stream ACCT Verification**:`HELIX_AGENT_BOOTSTRAP_ADMIN_EMAIL` 首登升格仅在零-admin 时
触发且 `email_verified` 必检;存在 admin 后永不再触发(无事后提权);`/settings/platform-users`
非 admin 见通知;`?tenant_id=*` 仅 system_admin、否则 403;跨租户成员读经 audit_reader 真 PG 验。
零债 6 条核验通过(后端 protocol 329 / persistence 44 / control-plane auth 204 / 集成 1 全绿;
前端 typecheck 净 + vitest 366/366)。

> **关键路径**：A → B + C/D → J 先让链走通；F→G→H→I / K / L / M 并行；N 收口（等 feature 齐）。

**后续（不在本迭代）**：
- **Phase 7 — 安全 + 数据保护（staging Linux）**：gVisor 7 用例 + cross-tenant 3 命名件套（现有覆盖散在 `test_tenant_scope_endpoints.py` 等，需集中/补名）+ KMS 轮换 runbook + **staging Linux 主机 provisioning**（现 `environments/staging.yaml` 全 TBD）。等 staging 就绪单列 Stream/PR。

---

### Stream Q — Web Key Management（平台统一密钥管理 + agent 主模型接线；~1-2 周）— 设计 [STREAM-Q-DESIGN](./streams/STREAM-Q-DESIGN.md)

**触发**：用户问"大模型 key 不是该登录后 web 上填么?为什么写文件?"——戳中真实 gap：① 平台配置页只存 ref(P-8 拒明文)、值要另放 secret 后端(dev 落文件、`LocalDevSecretStore.put` 还不持久)；② agent 主对话模型 key 走 manifest `api_key_ref` 根本不经过配置页。**目标**：web 粘贴真 key(OpenAI 控制台式)→ 加密落库 → agent 主模型也用它 → E2E 全程 web。

**范围决策(2026-05-29)**：① 加密落库做成新 `SecretStore` 后端(非裸列)；② 两半都做(金库+UI / chat-LLM 接线)；③ canonical manifest 去 `api_key_ref` 走平台 key + 留 override fixture；④ 出口只平台级(租户级/MCP/S3 后续按需接,后端通用不返工)。

- [x] **Q.1 `SqlEncryptedSecretStore`(加密落库做 SecretStore 后端,get/put 复用,解析链零改)（PR B #343）** — **Mini-ADR Q-1** ✅
- [x] **Q.2 AES-256-GCM 单 KEK + per-row nonce + AAD 绑 name；env KEK(`HELIX_AGENT_SECRET_ENCRYPTION_KEY`)now / KMS-wrap follow-up；`kek_version` 留轮换钩子（PR B #343）** — **Mini-ADR Q-2** ✅
- [x] **Q.3 `encrypted_secret` 表 `tenant_id NULL` + RLS `IS NOT DISTINCT FROM`,平台行 bypass_rls；通用后端、本迭代只平台行（PR B #343,migration 0050）** — **Mini-ADR Q-3** ✅
- [x] **Q.4 写路径 `PUT /v1/platform/credentials` 收原始 `value`(SecretStr,与 secret_ref 二选一)→ put → catalog 存 ref(表不改 schema)；re-paste=新 version（PR C #344）** — **Mini-ADR Q-4** ✅
- [x] **Q.5 chat-LLM 接线 `ProviderKeyResolver` 闭包穿 build_llm_router/step_routers/agent/vision + agent/child builder + app；manifest 优先/平台兜底；子 agent+vision 都接（PR DEF）** — **Mini-ADR Q-5** ✅
- [x] **Q.6 P-8 姿态变更(显式)：明文绝不进任何表;`encrypted_secret` 只存 AES-GCM 密文;catalog 仍 refs-only（PR A/B）** — **Mini-ADR Q-6** ✅
- [x] **Q.7 安全:value 全程 SecretStr / 不 log 值 / 审计去值 / 前端 type=password 不回显 / nonce os.urandom（PR B/C/DEF）** — **Mini-ADR Q-7** ✅
- [x] **Q.8 canonical manifest 去 `api_key_ref` 走平台 key + E2E 文档收口（PR DEF；override 路径由 build_llm_router 测覆盖,不另建 fixture）** — **Mini-ADR Q-8** ✅
- [x] **Q.9 Admin UI `/settings/platform` 粘贴 key 输入(type=password 不回显)+ i18n + Playwright/axe（PR DEF）** ✅

**Stream Q Verification**：PR B 加解密 round-trip + 密文落库(grep 不到明文)+ 错 KEK 失败 + RLS；PR C 粘贴/覆盖/非 admin 403/值不入日志；PR E `build_llm_router` manifest-wins/平台-fallback/皆无 raise/子 agent；PR D Playwright type=password+axe；**端到端**(PR F 后,真 key)：起栈无 dev-keys 文件 → 登录 → web 粘贴真 key → 注册无 api_key_ref 的 canonical manifest → Playground 真实回话。每 PR 零债 6 条。

**PR 拆分(~6 PR)**：A(设计) → B(金库后端) → C(写路径) → D(UI) → E(接线,可与 C/D 并行) → F(canonical 收口)。关键路径 A→B→C→D；E 依赖 B 接口可并行；F 收口(依赖 B+E)。

**后续(不在本迭代)**：真 KMS-wrapped KEK(等 aliyun_kms 落地)+ KEK 轮换自动化；租户级粘贴 UI + MCP/S3 secret 迁入金库;`ChainedSecretStore` 混合后端;版本回滚端点。

---

### Stream R — 公司成员 Onboarding + per-user Agent 编排（~2-3 周）— 设计 [STREAM-R-DESIGN](./streams/STREAM-R-DESIGN.md)

**触发**：跑 canonical E2E 前用户追问"新增管理员/租户新增账号怎么办?"——审计暴露:平台 admin 新增 ✅ 通,但**租户成员 onboarding 缺**:Keycloak `registrationAllowed:false`,新人账号从哪来无解(无邀请流程/无 Keycloak Admin API 集成/无成员 UI)。与目标产品形态(per-user 持久 agent,用户=公司的人)冲突,作废 STREAM-H §0"不做用户面"。**目标**：system_admin 建公司+首admin → admin 邀请员工 → helix 调 Keycloak Admin API 自动开账号 → 员工登录 → 员工用上专属 agent(全程 web)。

**范围决策(2026-06-01)**：① 邀请态建表(状态机+审计+list)；② 删装饰性 Keycloak realm role,维持应用自管授权(Keycloak 只管认证+tenant_id 属性)；③ Keycloak Admin 凭据走 Stream Q 加密金库,service account `helix-agent-api-internal` 配最小 `manage-users`；④ per-user agent 惰性初始化；⑤ 租户可配默认 agent;⑥ 链路终点=员工用上 agent;⑦ W3 本轮做。

**W1 — 地基（PR #351）**
- [x] **R-1 Keycloak Admin client 封装(`keycloak/` module:Protocol+Http+token cache+errors+Fake;service-account client_credentials grant;凭据金库取)（#351）** — **Mini-ADR R-1/R-2** ✅
- [x] **R-2 ~~删装饰 realm role~~ → 实施期纠正:补全租户 role_binding enrich 进授权(`resolve_tenant_roles`)。审计发现 rbac 只读 JWT roles claim、租户级 role_binding 没接授权层 → 邀请来的员工会无权限;realm role 保留(dev 租户权唯一来源,删了回归)（#351）** — **Mini-ADR R-7（已修正）** ✅
- [x] **R-3 `tenant_member` 表(migration 0051,状态机+partial-unique 幂等键+RLS)+ audit Literal 两处同改 + `TenantMemberStore`（#351）** — **Mini-ADR R-6/R-10** ✅
- [x] **R-4 `POST /v1/tenants` 加 first_admin(DB-first+幂等补偿跨系统事务;cross-tenant role_binding 写;Keycloak 建号+发设密码邮件)（#351）** — **Mini-ADR R-4/R-5/R-11** ✅

**W2 — 成员 onboarding（PR #352）**
- [x] **R-5 邀请 API `/v1/members`(invite batch/list/resend 幂等补偿/revoke;Keycloak 原生 execute-actions-email)（#352）** — **Mini-ADR R-3** ✅
- [x] **R-6 成员 UI `SettingsMembers.tsx`(邀请 drawer+status badge+resend/revoke;envelope 对账;Playwright/axe)（#352）** ✅

**W3 — per-user agent 编排（PR #353）**
- [x] **R-7 租户默认 agent(migration 0052 `tenant_config.default_agent_name`+session 创建读默认 fallback canonical-agent;agent_name/version 改可选,version 缺省取最新 ACTIVE)（#353）** — **Mini-ADR R-9** ✅
- [x] **R-8 per-user 惰性编排 → 实施期纠正:`ensure_member_active`(member 首登 invited→active,经 kc_user_id 反查)。**workspace.resolve 砍掉**——`UserWorkspaceStore` 是 supervisor 域+无 RLS,且 supervisor 已在首次 exec 惰性建行(`supervisor.py:158`),control-plane 不跨界重复建（#353）** — **Mini-ADR R-8（已修正）** ✅

**B 层（交互入口）显式化（2026-06-01）**：员工跑 agent 的**最低可用角色 = operator**(viewer 只有 `session:read` 发不了 run);本轮**交互入口 = admin-ui Playground**(员工登录→打开默认 agent Playground→发对话);**专属员工产品 UI 留后续独立 Stream**(别再犯 STREAM-H §0 假装 B 层不存在的错)。

**W4 — dev Keycloak 接通（收尾补丁,实跑 E2E 前置）**
> 实跑准备时发现:W1–W3 代码 ship 且用 Fake 客户端单测覆盖,但**在 dev 真让员工浏览器登录**的环境管线没接通(`keycloak_enabled` 默认关、admin-client-secret 无种入金库路径、realm 无 SMTP、admin-ui redirectUris 端口漂移)。调查确认**两条 provision 路径已先写账号+binding 再发邮件且容错邮件失败**(`member_ops.py:147`/`first_admin.py:147`)→ 无 SMTP 时 invite/建租户仍 201,控制台手设密码即可。**纯环境接线 + 文档 + UI 暴露,零 product 代码改动。**
- [x] **R-13 compose 加 `HELIX_AGENT_KEYCLOAK_ENABLED`(默认 false→CI integration 不受影响;dev 在 infra/.env 开 true)；realm admin-ui `:3000`→`:5173` 端口对齐** — **Mini-ADR R-13**
- [x] **R-14 seed CLI `control_plane.seed_keycloak_secret`(镜像 bootstrap_admin;把 admin-client-secret put 进金库;value 走 `--value`/env,不进容器运行时 env)+ 单测** — **Mini-ADR R-14**
- [x] **R-15 建租户 UI 暴露 first_admin(`SettingsCreateTenant` 加 email/display_name 字段+成功回显 first_admin;api 类型+i18n+e2e)→ 建公司+首管全程浏览器** — **Mini-ADR R-15**
- [x] **R-16 `getting-started.md` 重写成真实 Stream R 闭环 + 修 `bootstrap-admin.md`(subject-id 改服务账号 Admin API,绕开已禁 password grant;`control-plane`→`control-plane-blue`)** — 文档同步

**Stream R Verification**：单测 Keycloak client(token 过期重取/409 映射/unavailable)+`TenantMemberStore`(状态机+幂等键+kc 反查)+`resolve_tenant_roles`(merge/隔离)+`ensure_member_active` 幂等 + session 默认 agent 解析;集成(FakeKeycloak)W1 建租户+first_admin 全链+各失败点补偿/W2 invite/resend/revoke;**端到端**:建公司(填首admin email)→ admin 邀请员工(operator)→ 员工收 Keycloak 邮件设密码登录 → 员工 Playground 真实回话(全程 web,人只在网页填过 email)。每 PR 零债 6 条。email 用普通 str 校验(不引 email-validator)。

**PR 拆分(按 wave 合并,减 CI 等待)**：A(设计 #350) → W1 PR(R-1+2+3+4 #351) → W2 PR(R-5+6 #352) → W3 PR(R-7+8 #353)。关键路径 A→W1→W2→W3 全部 shipped。

**后续(不在本迭代)**：真 KMS-wrap Keycloak secret;member 角色变更端点+suspended 反激活(本版单向);SCIM/SSO 联邦/Keycloak group 同步;per-员工 agent 模板;`ensure_user_instance` 进程内缓存。

---

### Stream S — 可视化 Manifest 编辑器（~1-2 周）— 设计 [STREAM-S-DESIGN](./streams/STREAM-S-DESIGN.md)

**触发(2026-06-01)**：注册/编辑 agent 只有纯 Monaco YAML，对不懂 YAML 的管理员不友好（建一个 agent 要手写整份 manifest；换模型要再写一遍）。目标：VS Code Settings 式编辑器——可视化表单为主、原始 YAML 为逃生舱、双向；覆盖**全字段**；建 agent 时模型从**已配 key 的 provider** 下拉选。

**锁定决策**：① 全字段表单；② **schema 驱动**（后端导出 `AgentSpec` JSON Schema，前端 RJSF 渲染，`uiSchema` 精修，永不漂移）；③ 表单⇄YAML **切标签同步**（非实时）；④ 模型 = provider 下拉（已配 key）+ 模型名内置目录（选定自动带出 vision）；⑤ create+edit 复用 `<ManifestEditor>`。

- [x] **S-A 设计先行**（STREAM-S-DESIGN + 本 backlog）— **Mini-ADR S-1~S-8** #356 ✅
- [x] **S-B 后端**：`GET /v1/agents/schema`（`model_json_schema()`）+ `MODEL_CATALOG`（官网核对最新模型 + 能力）+ `GET /v1/model-catalog`（目录 ∩ 已配 provider）#357；模型清单后续按各 provider 官网刷新一轮 #359 ✅
- [x] **S-C 前端内核**：`<ManifestEditor>` = RJSF + 表单/YAML 双标签 + 切换同步 + ajv 校验 → 替换 Create 抽屉 #360 ✅
- [x] **S-D 前端模型选择 + 默认模板**：`ModelSelect` 联动控件（provider 下拉只列已配 key + model 目录选 + 自动带出 vision）+ 默认模板按能力自适应（capability-adaptive 默认 + 无 embedding 内联提示）+ i18n #361。**作用域**：主模型 + 直接 fallback（routing/vision/aux 走默认字段 + YAML）✅
- [x] **S-E 编辑接入**：`智能体详情→配置清单` 标签编辑态接 `<ManifestEditor>`（保留只读默认浏览，点 Edit 进编辑器）+ create/edit 两条 Playwright/axe + 收尾 ✅

**关键路径** A→B→C→D→E；C 依赖 B 两端点。每 PR CI-green + 零债 6 条。**新前端依赖** `@rjsf/core`+`@rjsf/antd`+`@rjsf/utils`+`@rjsf/validator-ajv8`（v5.24，React 19 / Antd 5 零 peer-dep workaround）。**Stream S 全部 shipped（2026-06-02）**。RJSF `SchemaField` 委托递归 schema 会死循环 → ModelSpec 高级字段改手渲染有界集（详见 PR D）。

**UI polish（2026-06-03）**：RJSF schema-dump 仍把后端类名 / Pydantic docstring / 英文标签 / 坏枚举 / `tenant_config`·`apiVersion`·`api_key_ref` 直接糊到表单，对管理员不可用。

- [x] 重做建/改 agent 表单：RJSF schema-dump → 手工 curated 表单（名称/描述/模型+温度/系统提示词/长期记忆开关/工具 web_search·http·mcp），中文 + 下拉 + 开关；非 curated 字段合并保留；YAML 页保留为完整逃生口

---

### Stream T — 平台 Embedding/Rerank 配置 + 长期记忆默认化 — 设计 [STREAM-T-DESIGN](./streams/STREAM-T-DESIGN.md)

**触发(2026-06-02)**：用户拍板（记忆 `project_longterm_memory_default_embedder_platform`）——① 长期记忆是**默认能力**非 opt-in（无记忆 agent 无商业价值）；② embedder/rerank 是**平台系统级配置**，要有 UI 让管理员自己配（选 provider+model+确认 key），不靠 env 写死；③ 不做纯 chat agent。现状：embedding/rerank 的 provider+model 是 **env-only 设置**、**启动定死**，无 DB/无 UI。

**锁定决策**：① 立即生效（动态解析，embedder 运行期读当前平台配置）；② 未配 embedding 时建 agent **挡住+引导**（不静默降级）；③ rerank 一起做、可选；④ 平台全局（不做 per-tenant override）；⑤ 记忆默认开落在 **UI 默认模板**层（schema 默认仍 None，向后兼容）；⑥ **回炉 PR D**：删 `ModelSelectField` "主模型 provider 无 embedding→记忆不可用" 误导提示（embedding 与主模型 provider 解耦）。

- [x] **T-A 设计先行**（STREAM-T-DESIGN + 本 backlog）— **Mini-ADR T-1~T-8**（PR #364）
- [x] **T-B 后端核心**：`platform_embedding_config` 单行表 + `PlatformEmbeddingConfigService`（DB 优先/env 回落/TTL 缓存）+ `DynamicResolvingEmbedder/Reranker`（运行期读配置，立即生效）+ app 接线（PR #365）
- [x] **T-C 后端 API + 门控**：`GET/PUT /v1/platform/embedding-config`（校验 model∈目录且 embeddings、provider key 已配）+ build-time gate 复用 + 审计（PR #366）
- [x] **T-D 前端配置 UI**：`/settings/platform` Embedding/Rerank 区（provider 只列有 embedding 模型的、model 只列 embeddings、rerank 可选、校验 key 已配）+ SDK + i18n + Storybook/Playwright/axe（PR #367）
- [x] **T-E 记忆默认化 + 回炉 + 收尾**：默认模板 memory-on + 删 PR D 误导提示 + 建 agent "先配 embedding" 引导（新增 role-agnostic `GET /v1/platform/embedding-config/status`，租户 admin 也能预检）+ getting-started §5.1 必配 + create-agent gate e2e（PR E）

**关键路径** A→B→C→D→E。每 PR CI-green + 零债 6 条。**E 之后跑 E2E 闭环**：平台配 embedding → 可视化编辑器建 memory-on DeepSeek agent → 真实多轮验证记忆召回（全程 web）。

---

### Stream U — 租户管理（列表 / 切进去管 / 停用 / 后台设密码）— 设计 [STREAM-U-DESIGN](./streams/STREAM-U-DESIGN.md)

**触发（2026-06-02 dogfood 实测）**：`POST /v1/tenants` 能建租户，租户作用域的 config/quota/credential 端点能改，但中间断了——**无 `GET /v1/tenants` 列表**、`TenantSwitcher` 对 system_admin 只有「主租户/全部租户(聚合)」**切不进具体租户**（聚合模式不让编辑），于是刚建的租户 UI 上**管不了**；且**无停用能力**、给首管/成员**设密码要去 Keycloak 控制台**（dev 无 SMTP）。

**锁定决策**：① 列表 + 切进去都做；② 改 display_name/plan 复用现有租户配置页（不重复）；③ **停用要做**（删除=follow-up）；④ 后台设密码 = **管理员输临时密码**（temporary=true 首登强改）；⑤ 停用 enforcement 单一卡口在 auth middleware（被停租户成员 403，system_admin 不受影响）。

- [x] **U-A 设计先行**（STREAM-U-DESIGN + 本 backlog）— **Mini-ADR U-1~U-8**
- [x] **U-B 列表后端**：`tenant_config` store `list()`（sql+memory）+ `GET /v1/tenants`（system_admin、分页）+ SDK `listTenants()` + 测试
- [x] **U-C 切进租户**：`TenantSwitcher` system_admin 填充具体租户 + 切进设 `TenantScope`（解死结，现有租户作用域页生效）+ 单测/e2e
- [x] **U-D 租户管理页**：`/settings/tenants` 列表/管理页（显示名/套餐/状态/id/创建时间 + 「管理」切进去）+ 导航「租户」+ i18n + storybook/e2e/axe
- [x] **U-E 停用/恢复**：migration `0053_tenant_status`（status 列）+ model/protocol + `POST /v1/tenants/{id}/deactivate`·`/activate` + middleware 403 `TENANT_SUSPENDED` enforcement（`TenantStatusService` 30s TTL 缓存，system_admin 不受影响）+ runs 防御 + 审计（`AuditAction` 单一 StrEnum，非双 Literal）+ 列表页状态徽章&停用/恢复操作（PR E）
- [x] **U-F 后台设密码**：Keycloak `reset_password`（temporary=true）+ `POST /v1/members/{id}/reset-password`（SecretStr 不落日志、审计无值、`require("user","write")`）+ SDK + 成员页「设密码」modal（Input.Password）+ i18n + 测试（PR F）
- [x] **U-G IA 收口**：创建租户折进 `/settings/tenants` 抽屉（`CreateTenantDrawer`，保留 tenant_id UUID 校验）+ 删独立创建页/路由/「创建租户」菜单（PR G）

**关键路径** A→B→{C, D, E, F}（G 收口）。每 PR CI-green + 零债 6 条。**完成 = system_admin 全程在 helix 后台管租户**（建→列出→切进去→给首管设临时密码登录→需要时停用），不再碰 Keycloak 控制台。

---

### Stream V — MCP Server 管理（租户自助注册远程 server + agent 表单按 server 选择）— ✅ 完成 2026-06-03（PR #381–#387）— 设计 [STREAM-V-DESIGN](./streams/STREAM-V-DESIGN.md)

**触发（2026-06-03 dogfood 实测）**：MCP 运行时早已生产级（client + pool + 远程 sse/streamable_http 客户端 + token 注入 + 熔断），但**管理面/用户面几乎全缺**——从"配置一个 MCP server"到"在 agent 里勾选它"这条链路**一格 UI 都没有**。Agent 表单 MCP = 一个开关 + 一个**手打逗号分隔工具名**的文本框（`MCPToolSpec` 连 server 选择字段都没有）；无枚举接口（UI 不知道能用哪些 server / 哪些工具）；租户 `mcp_allowlist` 只读、`mcp_credentials` 无 UI；租户**加不了自己的远程 server**（只能等 ops 改磁盘文件）。

**锁定决策**：① server 来源 = **租户自助加远程 server**（URL+token，HTTP/SSE，无 RCE；stdio 仍 ops-only）；② agent 层 = **先选 server 再可选窄到工具**（给 `MCPToolSpec` 加 `servers` 字段）；③ **平台 stdio + 租户远程并存**（不取代 allowlist）；④ **按租户建 pool**（私有 token 隔离）；⑤ token 走 encrypted secret store（与 LLM key web 粘贴同款）；⑥ **SSRF 防护**（注册+探测+运行时三处统一 URL 校验）。

- [x] **V-A 设计先行**（PR #381）（STREAM-V-DESIGN + 本 backlog）— **Mini-ADR V-1~V-11**
- [x] **V-B 注册表后端**（PR #382）：`tenant_mcp_server` model + migration `0054_tenant_mcp_server` + persistence store（base/sql/memory）+ protocol `TenantMcpServerRecord` + SSRF 校验 util；单测 CRUD/RLS/SSRF 拒私网
- [x] **V-C 注册 API + 探测**（PR #383）：CRUD 端点 + 注册时同步探测（连+list_tools，失败 422 不落库）+ token→encrypted store + DELETE 引用检查（409）+ 审计（`AuditAction` 单 StrEnum + `ResourceType` 双 Literal）；API 测（token 不入日志/非 admin 403/SSRF 422）
- [x] **V-D 按租户 pool 运行时**（PR #384）：`tenant_mcp_servers_provider` 闭包 + 租户 pool 构建 + token 解析注入 header + `_register_mcp` 并集逻辑（平台 stdio ∪ 租户远程，按 `servers`/`allow_tools` 过滤）+ 运行时 SSRF 校验；单测过滤/注入/并存/缓存复用/单 server 失败不炸 build
- [x] **V-E manifest schema**（PR #385）：`MCPToolSpec.servers`（空=全部，向后兼容）+ canonical manifest 验证；单测旧 manifest 无 servers=全部、servers 过滤生效
- [x] **V-F 发现接口 + 租户管理 UI**（PR #386）：`GET /v1/mcp-servers/available`（平台∪租户）+ `/{name}/tools`（live list_tools）+ `/settings/mcp-servers` 页 + 添加抽屉 +「测试连接」+ SDK + i18n + storybook/e2e/axe
- [x] **V-G agent 表单选择器**（PR #387）：FormView MCP 段重做（server 多选 + 工具展开勾选，替换开关+文本框）+ `form_model.ts` accessor（merge-preserving）+ i18n + Playwright 勾 server/展开工具/存读往返

**关键路径** A→B→C→D（后端链）；E 可与 C/D 并行；F 依赖 C；G 依赖 E+F。每 PR CI-green + 零债 6 条。**完成 = 租户在 helix 后台自助接入远程 MCP server，agent 表单按 server 勾选**（注册→测试连接→建 agent 勾选→Playground 真实调用），token 全程只在 web 填、加密存。

---

## 平台中心化治理 + 变现路线图（Stream W/X/Y/Z；2026-06-03 拍板）— [memory:project_platform_centralized_governance]

**触发（2026-06-03）**：用户质疑"租户自配 LLM KEY / 自建 MCP / SKILL / TOOLS 导致结构混乱，平台统一供给更利变现"。核实后：LLM key 与内置 tool **已平台默认/平台定义**，真正放任租户的只有 MCP（刚 V 上线）与 Skills。**用户拍板四项**：① MCP=平台目录+租户实例化；② LLM Key=平台独占+计量加价（移除 BYOK）；③ Skills=混合（平台库+租户自建）；④ 治理层+计费层一起、拆多 Stream。**目标**：把 helix 从"自助工具"推向"可变现多租户 PaaS"，同时保留 per-company 定制。

**两个改变设计的发现**（见 [memory:reference_billing_meter_and_entitlement]）：① entitlement 原语 `tenant_config.plan`（free/pro/enterprise）已存在但从未用于 gating → 加 `tier_satisfies` 复用，不建新表；② 真正在线计量是 G.9 `token_usage`（带 input/output 拆分），C.5 quota 引擎根本没被 run loop 调用 → 成本从 `token_usage` 派生为可重算 rollup，不碰热路径。

**依赖**：`W（含 entitlement 地基）→ X（Skills，复用）∥ Y（LLM 锁+rate card+计量）→ Z（chargeback 面）`。

### Stream W — MCP Connector Catalog（平台精选目录 + 租户实例化 + entitlement 地基）— ✅ 核心完成 2026-06-04（W0–W5 / PR #390–#393,#395–#396；W6 可选未做）— 设计 [STREAM-W-DESIGN](./streams/STREAM-W-DESIGN.md)

**演进 Stream V，不破坏已上线行/端点**。平台维护精选连接器目录（system_admin），租户从目录实例化、填自己凭证；premium 项按档位门控。保留自定义逃生口（per-tenant kill-switch）。

- [x] **W0 设计先行**（PR #390）（STREAM-W-DESIGN + 本 backlog）— **Mini-ADR W-1~W-11**
- [x] **W1 协议 + entitlement 原语**（PR #391）：`protocol/mcp_connector_catalog.py`（records + `McpConnectorAuthSchema`）+ `TenantMcpServerRecord.catalog_id` + `TenantConfigRecord/Patch.allow_custom_mcp_servers` + `protocol/entitlement.py` `TIER_ORDER`/`tier_satisfies`（放 helix-protocol 非 helix-common，避免新增跨包依赖）；纯 schema 单测
- [x] **W2 持久化**（PR #392）：迁移 `0055_mcp_connector_catalog`（NULL-tenant RLS，照抄 0050）+ `0056_mcp_catalog_columns`（catalog_id + allow_custom_mcp_servers）+ ORM + `McpConnectorCatalogStore`(base/sql/memory) + store `catalog_id` kwarg + `tenant_config` 列；RLS 测（租户读目录须 bypass）+ 迁移安全测（V 旧行→catalog_id=NULL）+ validate-before-commit 负向测
- [x] **W3 平台目录 CRUD API + RBAC**（PR #393）：`mcp_catalog` 资源 + `api/mcp_catalog.py`（system_admin，bypass_rls）+ delete-in-use 409（FK RESTRICT→IntegrityError）+ 审计双 Literal；9 authz/CRUD 测
- [x] **W4 租户实例化 + 档位门控 + `/available` + 自定义 kill-switch**（PR #395）：`GET /catalog`（带 entitled）+ `POST /catalog/{id}/instances`（复用 probe/secret/invalidate）+ `tier_satisfies` 门控 + `allow_custom_mcp_servers` enforce；**param 结构字符黑名单防 host-pivot SSRF**（review HIGH）；15 测
- [x] **W5 Admin UI**（PR #396）：平台目录管理页（system_admin）+ 租户"从目录添加"向导（auth_schema 驱动表单 + entitlement 锁标）+ 自定义降级为 Advanced + i18n en/zh-CN；vitest 240 + storybook + Playwright 9 + axe；TS-review 1 HIGH（auth_type 编辑不可变）+3 MED 修完
- [ ] **W6（可选）初始目录 seed**：GitHub/Postgres 官方连接器（config flag 后）

**关键路径** W0→W1→W2→W3→W4→W5。**完成 = system_admin 维护连接器目录，租户从目录实例化（含 premium 档位门控）并在 agent 里使用**；entitlement 地基（tier_satisfies）就绪供 X/Y/Z 复用。

### Stream MCP-OAUTH — per-user OAuth 2.1 连接器（解锁 OAuth-only 托管 MCP）— 设计 [STREAM-MCP-OAUTH-DESIGN](./streams/STREAM-MCP-OAUTH-DESIGN.md)

**触发（2026-06-06）**：审计后盘点 MCP 生态发现最热门的 SaaS 连接器（Notion/Linear/Jira/Confluence/Sentry/Asana）托管端点**只支持交互式 OAuth 2.1 授权码流程**，现有 catalog 只认 `none/bearer` 全挡在门外。**用户拍板**：授权主体 = **per-user**；首个端到端验证 = **Linear**。规范化实现 OAuth 2.1 + PKCE + 元数据发现（RFC 9728/8414）。

- [x] **OA-0 设计先行**：STREAM-MCP-OAUTH-DESIGN + 本 backlog（per-user / Linear-first / 6 阶段 / Mini-ADR OA-1~OA-6）
- [x] **OA-1a catalog OAuth 基础**：`CatalogAuthType += "oauth2"`（auth_schema 校验:oauth2 无 secret 字段 + 必须 `oauth_client_id`）+ catalog `oauth_client_id`/`oauth_scopes` 列（DTO/ORM/store 三层）+ 迁移 `0062_mcp_catalog_oauth`（加列 + auth_type CHECK 扩 oauth2）；protocol + memory store 单测。无 control-plane 重声明（单源 protocol）
- [x] **OA-1b 连接表**（PR #440）：`mcp_oauth_connection`（per-(tenant,user,catalog) + token ref + status pending/connected/expired/revoked/error + 短期 oauth_state/pkce_verifier 流程字段）；protocol DTO（`McpOAuthConnectionRecord`/`Patch` + `OAuthConnectionStatus`，connected 必须有 access_token_ref）+ ORM + 迁移 `0063_mcp_oauth_connection`（RLS 租户隔离严格相等 + catalog FK CASCADE + unique(tenant,user,catalog) + status CHECK）+ store 三层（base/sql/memory，**每方法按 user_id 过滤=用户级隔离**叠加 RLS 租户级）；protocol + memory store 单测（含用户隔离、clear_flow_state、duplicate）
- [x] **OA-2 OAuth 引擎**（PR #441，纯逻辑）：`control_plane/mcp_oauth.py` — `discover_oauth_metadata`（RFC 9728 PRM → RFC 8414 AS metadata）+ `generate_pkce`(S256)/`generate_state` + `build_authorize_url`(含 RFC 8707 resource)+ `exchange_code`/`refresh_token`；httpx 注入(MockTransport 测试不打真网)+ 发现的每个 URL 过 `validate_remote_url`(SSRF) + 响应大小上限 + token/verifier `repr=False` 脱敏。**initiate/callback 端点留 OA-3** 跟 Linear 垂直切片一起接。12 单测（PKCE S256 / 发现 / 缺 authorization_servers / 私网 token 端点拒 / 交换 / 错误状态 / 缺 access_token / refresh / repr 脱敏）
- [x] **OA-3a OAuth 端点**（PR #442）：`api/mcp_oauth_api.py` — `POST /v1/mcp-servers/catalog/{id}/oauth/initiate`（发现 AS + PKCE + state + 建 pending 连接 + 返 authorize URL；oauth2/tier/redirect 校验；re-init 重置旧连接）+ `GET /v1/mcp-oauth/callback`（校验 state → exchange_code → token 存 secret store(只 `secret://` ref) → 置 connected + 清流程态 + 审计）；settings `mcp_oauth_redirect_uri`（缺则 503）+ app 接 `mcp_oauth_connection` store/router。**部署项**：redirect URI 须公网可达 + 登记到连接器 OAuth app allowlist。5 API 测（monkeypatch 引擎不打真网：initiate / 全程 roundtrip / 坏 state 拒 / 非 oauth2 拒 / 未配置 503）
- [x] **OA-3b-1 per-user OAuth pool 解析引擎**（PR #443）：`UserMcpOAuthPoolService`（按 (tenant,user) 缓存）—— 读该用户 `connected` 且未过期的 `mcp_oauth_connection` → 产出 **bearer 式** `MCPServerConfig`（`token_ref=access_token_ref`,transport 从 catalog 读）→ client_factory → pool。**复用现有 `_build_mcp_client` bearer 分支注入,无需 oauth2 client 改动**;过期则跳过(proactive refresh 留 OA-6);并发用 V-D 同款(per-key lock + generation + rebuild-on-conflict)。6 单测(connected/expired/pending/cache/invalidate/用户隔离)。**不动 hot path**
- [x] **OA-3b-2 build 路径接入**（PR #444）：`subject_id`（=连接 user_id）串过 `get_agent`→`AgentBuilder`→`make_agent_builder._build` + `ToolEnv.user_mcp_oauth_pool`（assembly `_register_mcp` 注册,allowlist 不 gate,名冲突让平台/租户先）+ app 接 `UserMcpOAuthPoolService`/provider + `runs.py` 两处传 `principal.subject_id`（触发器无 subject 传 None）。**缓存按需 per-user**:`get_agent` 先解析 user pool,**仅当非空才用 4 元组 key**（无 OAuth 的 agent 仍全租户共享,不膨胀）+ `invalidate_user` + callback 连接成功后 invalidate user pool + user agents。子 agent 的 per-user OAuth 留后续。runtime + assembly 单测;全套 1854 回归零失败。**复用 bearer 注入,`_build_mcp_client` 未改**
- [ ] **OA-3b-后续（可选）**：子 agent(委派)解析调用方 OAuth 池（需 SubAgentTool 透传 user_id）
- [x] **OA-4 连接管理端点 + 状态可观测**（PR #445）：`GET /v1/mcp-oauth/connections`（当前用户连接:status/scopes/expiry/last_error,**token ref + 流程态永不暴露**)+ `DELETE /v1/mcp-oauth/connections/{id}`（断开:best-effort 覆写 token 吊销[SecretStore 无 delete]+ 删行 + invalidate user pool/agents + 审计 MCP_SERVER_DELETE scope=oauth）。callback/disconnect 共用 `_invalidate_user_caches`。8 API 测。per-user pool 维度已在 OA-3b 落地
- [x] **OA-5 扩连接器(env-seed 模板)**（PR #447）：catalog 原本纯 API 驱动无 seed 机制 → 补上 W-6 规划的 env-seed。`configs/mcp-catalog-seed.json` 模板(Linear/Notion/Sentry/Atlassian,`oauth_client_id` 用 `${MCP_OAUTH_<NAME>_CLIENT_ID}` 占位)；`catalog_seed.py` 纯 loader(`load_catalog_seed` 解析+占位符 env 替换:缺 env→跳过该条平台照常启动,JSON/校验错→fail-fast)+ idempotent `seed_catalog`(get_by_name create-if-absent,bypass RLS 写 NULL-tenant);`settings.mcp_catalog_seed_file`(默认 None 不 seed)；lifespan 启动期 seed + log created/existing/skipped。**填 env 即生效**(未注册先跳过,填 client_id 重启自动 create)。runbook `docs/runbooks/mcp-oauth-connector-seed.md`。client_id 是公开标识非 secret,日志只记名字不记值。限制:create-if-absent 不更新已存在(改 client_id 走 admin PATCH)。12 单测(loader/idempotent/wrapper/模板完整性)
- [x] **OA-6 token 刷新硬化**（PR #446）：`McpOAuthRefresher`(`mcp_oauth_refresh.py`)在 pool 构建期(per-user 锁下)惰性刷新——临过期(< 60s skew)经 OA-2 引擎 `refresh_token` 换新 access、覆写 secret ref(含轮换 refresh token)、回写 `token_expires_at`/`last_refresh_at`、清 `last_error`、置 `connected`。失败分类:`invalid_grant`→`revoked`(需重连);其它失败仅在已过期时→`error`(下次构建重试),未过期则原样服务当前 token。引擎 `McpOAuthError.oauth_error` 暴露 RFC 6749 error 字段供区分;`McpOAuthConnectionPatch.clear_last_error`(镜像 clear_flow_state)清恢复态;状态/过期经 OA-4 list 暴露。不做后台调度(不活跃用户无需刷新)。诚实限制:跨副本并发刷新可能竞 refresh-token 轮换(进程内 per-user 锁串行)。10 单测(refresh/revoked/transient×2/expired-no-refresh/rotated/pool-wiring)+2 引擎测

**关键路径** OA-0→OA-1a→OA-1b→OA-2→OA-3（首连接器端到端）→OA-4→OA-5→OA-6。**out-of-scope**：per-tenant 共享连接、DCR/CIMD（用静态预注册 client_id）。

**MCP 基建加固 follow-up**（加固审计 #438 后口头提的 3 项）：
- [x] **#1 OAuth-only 连接器支持** — 即 Stream MCP-OAUTH 全套（OA-0~OA-6，上文）
- [x] **#3 DNS-rebind / egress 防御决议**（PR #448）：[ADR-0009](./adr/0009-control-plane-egress-ssrf-dns-rebind.md) — DNS-rebind 防御放基础设施 egress 层（部署须拒 control-plane 出方向私网/loopback/链路本地），不在 app 层做 resolve-then-pin 半成品；保留 `validate_remote_url` 作纵深第一层；沙箱 `helix-sandbox-egress` 只覆盖沙箱出站不覆盖 control-plane connect-out。`url_validation.py` docstring 指向该 ADR。纯文档
- [x] **#2 instance 健康状态字段**（PR #449）：探活结果原本不持久化 → `tenant_mcp_server` 加 `last_probe_at`/`last_probe_status`(`Literal["ok","error"]`)/`last_probe_error`(都 None=从未探活=unknown)+ migration 0064(含 CHECK)+ 专用 store 方法 `record_probe_result`(不经用户 Patch 防伪造,**不动 updated_at**——探活非配置变更)。写入点:注册/catalog 实例化(探活成功→ok)、`GET /{name}/tools`(按需探活 ok / **失败→error**,捕获掉线)、`PATCH`(重探成功→ok)。health 经 list/`_public` 自动暴露;best-effort 写(health 写失败不影响主操作)。health 纯观测、不进 hot path、不触发 pool invalidation。**不做后台周期探活**(超范围,注明)。健康加在 instance 而非 catalog(catalog 是带占位符模板不可探活)/oauth-conn(已有自身生命周期 status)。13 测(store 4 + API 3 health + 既有回归 + SQL 集成 1)

### Stream X — Platform Skill Library（平台精选库 + 租户自建，混合）— 设计 [STREAM-X-DESIGN](./streams/STREAM-X-DESIGN.md)

平台发布精选 skill 库（`tenant_id NULL`，可 premium），租户继续自建；manifest 同时可绑平台/租户 skill。复用 W 的 `tier_satisfies`、Stream U 的 moderation/curator。**关键发现**：skill resolver 当前**未接入 agent build**（Stream U 遗留缺口，app.py:972 TODO）→ 租户 skill 运行时也未生效；X3 首次接线（租户+平台），属行为变更。

- [x] **X0 设计先行**（STREAM-X-DESIGN + Mini-ADR W… X-1~X-9）（对账 2026-06-12：设计文档在仓，X1-X9 已全部交付）
- [x] **X1 协议**（PR #399）：`Skill.required_tier`（纯 additive，default FREE）。`tenant_id: UUID|None` 的放宽**移到 X2**（它会 ripple 进 store/curator/orchestrator，须与迁移同 PR 处理，避免悬空类型变更）
- [x] **X2 持久化 + 迁移**（PR #400）：协议 `Skill/SkillVersion.tenant_id → UUID|None` + 迁移 `0057_platform_skill`（skill/skill_version tenant_id 改 NULLABLE + RLS 严格相等→`IS NOT DISTINCT FROM` + COALESCE 唯一索引 + `required_tier` 列）+ store/curator/orchestrator 的 `UUID|None` ripple（memory store set、`SkillActivityRecorder.record`、`_load_skills`）+ curator `WHERE tenant_id IS NOT NULL`；**RLS 回归测 + 迁移安全测**（最高风险=已有数据表换 RLS）
- [x] **X3 resolver 接线 + 双查 + 门控**（PR #401）：`make_skill_resolver`（租户优先 + 平台 bypass 兜底 + `tier_satisfies` not_entitled）+ **首次线程穿 `make_agent_builder`/`make_child_agent_builder`/`build_agent`/子 agent + `SkillViewTool` + activity recorder**（补 Stream U 运行时接线）
- [x] **X4 平台 skill CRUD API + 租户合并视图**（PR #402）（system_admin，bypass_rls，复用 moderation/high-risk）+ `GET /v1/skills` 加 source/entitled
- [x] **X5 Admin UI**（PR #403）：平台库管理页（system_admin）+ 租户库 source/entitled 徽章 + i18n

### Stream Y — LLM 平台独占 + Rate Card + 计量（治理 + 成本地基）— 设计 [STREAM-Y-DESIGN](./streams/STREAM-Y-DESIGN.md)

锁死 LLM 平台独占（移除租户 BYOK + manifest `api_key_ref` override）；在 G.9 `token_usage` 上建 rate card + 成本派生 rollup + 按租户分月 billing ledger（**不碰 C.5 热路径**）。业务决策：加价=按模型行+档位覆盖；成本透明度=租户只看最终计费价。

- [x] **Y0 设计先行**（PR #404）（STREAM-Y-DESIGN + Mini-ADR Y-1~Y-5 + 现状核实）
- [x] **Y1 平台独占锁**（PR #405）：resolver 删 tenant 分支 + `CredentialsMode` 收窄 `Literal["platform"]` + 全量移除 tenant-mode 机器（mcp_auth 死代码 / switch gate / dry-run 端点 / metrics）+ 防御性迁移 `0058`（翻 tenant→platform + CHECK 收紧）+ admin-ui 裁剪。PATCH `tenant`→422
- [x] **Y2 移除 manifest `api_key_ref`**（PR #406）：`build_agent` 路径 `ignore_api_key_ref=True` → manifest api_key_ref 被忽略（ignore+warn）+ 100% 主 key 走平台 resolver；内部 rerank/embed/aux 管道（默认 honor）不变；杜绝 spend 绕过计量
- [x] **Y3 Rate Card**（PR #407）：`model_rate_card` 表（NULL-tenant FORCE RLS + 整数 micro-USD + markup_bps + 时序 effective_from/until + plan_tier 最具体优先 + resolve 半开窗）+ store（双实现）+ admin API（system_admin/bypass）+ `billing` RBAC + audit ResourceType + `token_usage.provider` 列（additive,中间件填充）+ 迁移 `0059`
- [x] **Y4 成本派生 + billing ledger**（PR #408）：`tenant_billing_ledger`（tenant-scoped RLS；不扩展 token_budget_ledger；base/markup/billed 拆分内部存）+ `billing-rollup-job` 服务（按 row `observed_at` 当时生效 rate **逐行**定价→聚合 bucket→幂等 upsert-overwrite；provider 反查 + 歧义/无 rate 标 unpriced）+ `token_usage` windowed read + 迁移 `0060`。**Stream Y 收官**

#### Stream Y-MK — 平台级 per-provider 多 key failover（2026-06-17 拍板；分支 `llm/multi-key-failover`）— 设计 [STREAM-Y-MULTIKEY-DESIGN](./streams/STREAM-Y-MULTIKEY-DESIGN.md) — [memory:project_ymk_provider_multikey_failover]

**触发**：单 provider 只能配一把 key，被限流/欠费/撤销即整条 run 挂。**用户拍板语义**：key 限流/欠费/撤销→跳同 provider 兄弟 key；该 provider 全 key 死→跳下个 provider；"请求报错"=只指 5xx/网络/超时（400 fail-fast 不翻 E.11 #21）；优先级 failover 无加权；平台级先行（租户多 key=二期 Y-MK-2）。**关键复用**：E.11 LLMRouter + per-key breaker 早为多 key 设计，缺的只是装配。

- [x] **L1 错误轴**：`LLMKeyUnavailableError`（欠费/quota/撤销，不 retry 但记 breaker）+ middleware 拦截
- [x] **L2 router 两级链**：`ProviderHandle.group` + group-aware 游走（key 级跳兄弟 / provider 级跳过兄弟 / 400 fail-fast）；non-OAuth 401 改 key 级
- [x] **L3 status 分类器**：`_errors.classify_http_error`（402/429-quota/403-billing→账号死轴），openai/anthropic 接入
- [x] **L4 存储 1:1→1:N**：`platform_provider_secret` 加 key_id+priority 复合主键 + migration `0084`（in-place backfill default）；真 PG 集成 4/4
- [x] **L5 解析 + 装配**：service `effective_provider_secret_refs[_for]`（priority 聚合，DB 权威）+ `TenantOverlay.resolve_provider_keys`（helix-common 禁改照 HX-8）+ `make_provider_key_resolver` 返 list + `build_llm_router` 展开兄弟 handle
- [x] **L6a 操作端 API**：`PUT/DELETE /v1/platform/credentials/providers/{provider}/keys/{key_id}` + GET catalog `keys[]` + 软删兄弟保留
- [x] **L6b admin-ui**：平台凭证页 provider 行可展开 key 列表 + 添加密钥弹窗（key_id+优先级）+ SDK/i18n 双语/Storybook/vitest/e2e
- [ ] **L6c（二期可选）**：`token_usage` 加 `provider_key_id` label（多账号按 key 对账发票）

**完成 = 平台为每 provider 配多把 key，run 在限流/欠费/撤销时自动切兄弟 key，全死才切 provider**；全 TDD（MK-1..15）+ 真 PG 集成。

### Stream Z — Chargeback / 用量面（计费层出口）— 设计 [STREAM-Z-DESIGN](./streams/STREAM-Z-DESIGN.md)

租户看自己用量/成本，system_admin 看跨租户分账，成本可观测。**硬约束：租户面只暴露 billed，绝不回显 base/markup**。发票推迟 M2。

- [x] **Z0 设计先行**（PR #409）（STREAM-Z-DESIGN + Mini-ADR Z-1~Z-4 + 现状核实）
- [x] **Z1 租户用量/成本 API**（PR #410）：`GET /v1/usage/cost`（RLS 自隔离，**只 billed**，group_by agent/model/none，unpriced 标记）+ `/v1/usage/tokens`（当月实时直读 token_usage）+ `billing:read`；ledger store 接入 control-plane app.state
- [x] **Z2 admin chargeback API + 指标**（PR #411）：`GET /v1/admin/billing/chargeback`（system_admin，跨租户，显 base/markup/billed/margin）+ ledger `list_for_month_all_tenants`（**`SET LOCAL ROLE audit_reader`** 跨 FORCE-RLS + migration `0061` 授权 + 真 PG 集成测）+ `helix_llm_billed_cost_micros` gauge（rollup set-overwrite）。✅ 收尾顺手修复同款潜伏 RLS bug：`audit_log/sql.py` 跨租户读（`tenant_id="*"`）缺 `SET LOCAL ROLE audit_reader` → 生产返零行（PR #412，真 PG 集成测 RED/GREEN；见 [memory:reference_cross_tenant_read_force_rls_set_role]）
- [x] **Z3 看板 UI**（PR #413）（前端）：租户用量页 `/settings/usage`（月选择 + agent/model 拆分 + billed-only + 实时 token）+ system_admin chargeback 页 `/settings/billing-chargeback`（跨租户 base/markup/billed/margin）+ `formatMicros` + i18n + vitest/storybook/Playwright/axe。**W/X/Y/Z 路线图收官**

### W/X/Y/Z 显式推迟项（M2+ follow-up backlog）

路线图收官时显式登记的 out-of-scope 项（核心变现能力已交付，以下为增量/演进，不阻塞）。遵循零技术债收尾原则：宁可显式登记也不留空决策（见 [memory:feedback_no_design_choice_disguise] / [memory:feedback_zero_tech_debt]）。

- [ ] **发票 / 支付集成**（M2）— 发票 = 月度 `tenant_billing_ledger` 的冻结快照，需编号 / 税务 / PDF / 支付网关；**无 schema 阻塞**（chargeback 报表已交付变现价值）
- [ ] **目录模板 re-sync**（M2）— 现为快照语义（实例化时把 url/transport/auth_type 快照到行，目录改模板不静默改活实例）；批量"重建活实例"流程留后续。来源 Mini-ADR W（实例快照）
- [ ] **平台级 named-key 池**（M2）— 替代已移除的 manifest `api_key_ref` override，覆盖"高频 key 钉选"场景（当前 100% 走平台 resolver；Y2 移除 override 时记的过渡缺口）
- [ ] **à-la-carte per-feature entitlement**（按需）— 现 tier 比较（`tier_satisfies`）够用；真需要细粒度授权再加 `tenant_entitlement` 表，不提前建
- [ ] **W6（可选）初始目录 seed**（已在 W backlog）— GitHub/Postgres 官方连接器（config flag 后）
- [ ] **KEK → 真 KMS wrap**（依赖 aliyun_kms 落地）— 现 env KEK（`HELIX_AGENT_SECRET_ENCRYPTION_KEY`）；与 Stream Q 后续项同源，非本路线图新增
- [ ] **跨租户 write**（按需）— Stream N 仍 read-only；chargeback / catalog 平台面均为 read 或 system_admin 受控写

## Stream TE — Tool Execution Engine Hardening（2026-06-05 起）— 设计 [STREAM-TE-DESIGN](./streams/STREAM-TE-DESIGN.md)

把"改进版 Tool 执行引擎"落地 helix（源自 Go Harness 专栏 + openclaw/deer-flow/hermes 源码对照 + helix 现状探查）。**关键利好**：文章最难的并行调度（只读并发/涉写串行）helix 已成熟（`tools/scheduling.py`，L.L6），不重做。**用户拍板**：bash IN / edit 鲁棒化 / 跨副本锁做强 / 性能+可观测进门 / 另开办公能力包 / 按"办公读多写少"杠杆重排（可观测·工具面·bash 前置，锁·CAS 押后保留）。**TE-0 核实关键事实**：workspace **非单 sandbox 独占租约**（多 sandbox 可并挂同卷、supervisor 可多副本、无 DB 锁）→ 跨副本锁**必须** PG `pg_advisory_xact_lock`（仿 `DbEventStore`，合规 infra 约束），in-process 锁不安全。

**进度（2026-06-05）**：**P0（TE-1~5）+ P1（TE-6/6b）+ P2（TE-7→10：文件原语/锁/edit-CAS+模糊/性能门）全部已交付**（PR #415–#429）。**Stream TE 引擎层完成**。仅 TE-3b（Langfuse span + trajectory 富化）延后（无 sink/数据源）。**Stream OFFICE（国内办公能力包）OFFICE-0 设计已出**（PR #430，纯 Python 库 + office variant + 连接器暂缓），OFFICE-1/3 待开。详见各条。

- [x] **TE-0 设计先行**（PR #415）：`STREAM-TE-DESIGN.md` + 本 backlog — Mini-ADR TE-ADR-1~6。核实 workspace 非单 sandbox 独占 → 跨副本锁必须 PG advisory
- [x] **TE-1 工具元数据**（PR #416）：`ToolSpec` 加 `side_effect: SideEffectLevel|None`（None→由 `is_read_only` 保守派生 read_only/reversible）+ `idempotent` + `resolved_side_effect` 属性。纯增量、零行为变更（无消费者）
- [x] **TE-2 工具级审计 emit**（PR #417）：`builder.py:_dispatch_tool` emit `TOOL_CALL`/`TOOL_BLOCKED`（运行时经 `config["configurable"]` 注入 audit_logger，不碰 build_agent）；脱敏 arg 名+path 值（绝不记原始值）；`ResourceType` 加 `"tool"`（双 Literal）
- [x] **TE-3 可观测补全**（PR #418）：per-tool Prometheus `helix_tool_call_total{tool,outcome}` + `helix_tool_latency_seconds{tool}`（`_error_total` 由 outcome label 取代）；MCP 名归一 `mcp:<server>` 控基数。**Langfuse span + trajectory 富化拆为 TE-3b（按价值延后）**
- [ ] **TE-3b Langfuse tool span + trajectory 富化**（延后；**前提已半兑现**——对账 2026-06-12：Langfuse 生产 sink 已由 HX-7 交付（LangfuseSdkClient #558），剩余=LLM span 之外的 tool span 富化；trajectory 的 exit_code/读写集仍无数据源 → 并入 TE-5/TE-7）
- [x] **TE-4 side_effect 门控**（PR #419）：扩 `scheduling.py` `irreversible`→强制串行（检查前置）；build 期把 irreversible 工具名并入 `_gated_tools`→自动审批门控（运行时 `_approval.py` 本无硬编码集，gating 经集合并集）。零当前行为变更（彼时无 irreversible 工具）
- [x] **TE-5 bash 工具**（PR #420）：经 sandbox-supervisor 复用 exec 通道（subprocess wrapper，零 supervisor 改动）；`side_effect="irreversible"`→首个被 TE-4 自动串行+门控的工具；信号退出码映射 shell 惯例。抽取 `run_in_sandbox`/`format_sandbox_outcome` 与 exec_python 共用
- [x] **TE-6 deferred registry + find_tools**（PR #421）：`ToolRegistry` `register(deferred=)` + `specs()`/`all_specs()`/`deferred_specs()`/`search()`（select:/+keyword/regex）；`find_tools` 元工具 + `AgentState.promoted_tools` 通道（per-run 隔离，非 ContextVar）。**机制层默认休眠**。bind 经 `specs()` 过滤（无需 middleware）。顺带修好 `subagent_invocations` 并行 fan-out 丢行潜伏 bug
- [x] **TE-6b 激活 tool RAG**（PR #422）：MCP 工具默认全部 deferred（deer-flow always-defer-MCP，无阈值）+ 有 deferred 时自动注册 `find_tools`(active)。有意行为变更（MCP 先 find_tools 再调）；无 MCP 的 agent 零变更
- [x] **TE-7 workspace 文件原语**（P2，PR #425）：`read_file`/`write_file`/`list_dir`（基础 exact）走 **exec-warm 通道**（复用 J.15 warm 沙箱，非 supervisor 专用 API——探查发现专用 API 每次 `docker run` 临时容器秒级，2026-06-05 复议改定 TE-ADR-2）；snippet=`_PARAMS`(repr of json)+固定 body(json.loads→print JSON envelope)；沙箱内 realpath confinement 防 `..`/symlink 越界 + orchestrator 侧拒绝 绝对/`..`/NUL；原子写（tmp+`os.replace`）= TE-8 lock-free 读基础；尺寸上限(read/write 10MiB、list 1000)；过 security+code 双评审（M-1~3/L-1/2/4 已处理）
- [x] **TE-8 per-workspace 排他写锁**（P2，PR #426）：**写**（文件原语 + bash）经 PG **双参** `pg_advisory_xact_lock(1, hashtext("{tenant}:{user}"))`（**per-workspace 单锁**，与 event_log 单参 key space 隔离；orchestrator DB 事务横跨写 exec）；**读/list 无锁**（原子 rename 保读一致快照）。per-workspace 而非 per-path：bash 无 path 参数 + advisory 无共享锁 → per-path 覆盖不了 bash 互斥。**评审修复两个破锁 bug**：`SET LOCAL idle_in_transaction_session_timeout/statement_timeout=360s` 防持锁事务被 per-DB 60s/30s 默认杀掉提前释放锁；双参 key space 防跨子系统碰撞。连接池容量耦合记 follow-up（TE-ADR-3）
- [x] **TE-9a edit 精确 + 硬 CAS**（P2，PR #427）：`edit_file` 精确匹配 + `expected_hash` 硬 CAS；read→verify→match→atomic-write 单 exec 原子（无 TOCTOU）；`stale`{current_hash}/`no_match`/`ambiguous`{count} 结构化错误（接自纠错）；复用 TE-7 snippet + TE-8 写锁；原子写提取为共享 `_atomic_write`。硬 CAS 是 helix 差异化（三家最强也只 advisory）。过 code 评审（assembly/lock 覆盖补齐）
- [x] **TE-9b edit 模糊降级**（P2，PR #428）：精确→空白归一行块 多级降级 + 全失败 difflib 候选提示（`near line N`）；`meta.match=exact|fuzzy`；纯 CRLF 文件 fuzzy rebuild 保留行尾。过 code 评审（CRLF 修复 + strip 语义文档化 + 3 回归测试）
- [x] **TE-10 性能验收门**（P2，PR #429）：**诚实分界**——CI 无完整沙箱，文件原语端到端延迟/soak 是生产压测。CI 门禁=**锁竞争 benchmark**（真 PG：10 并发写同 workspace 无死锁/饥饿、5 不同 workspace 真并发）；**SLO** 写入 STREAM-TE-DESIGN §6（warm read P95<500ms / write·edit P95<1s / cold-start acquire P95<5s，TE-3 per-tool 延迟指标已采集）；**load/soak runbook**（staging k6/locust，盯延迟尾+泄漏）。warm/cold 分别度量列为可选增强（需 acquire 暴露 cold_start）

## Stream OFFICE — 企业办公能力包（国内）— 设计 [STREAM-OFFICE-DESIGN](./streams/STREAM-OFFICE-DESIGN.md)

补足 per-user 持久 agent"日常办公占 70%"的工具面。**TE 做引擎"能承载"，OFFICE 做能力面"能办公"**。**2026-06-05 拍板：服务国内客户、去国外连接器**；本轮做文件能力 + 办公 Skill，**国内协作连接器（钉钉/企微/飞书）暂缓**（MCP-client-only + 国内 remote MCP 生态薄）。

- [x] **OFFICE-0 设计先行**（PR #430）：STREAM-OFFICE-DESIGN + 现状核实 + Mini-ADR（纯 Python 库 / office variant / 中文 / 连接器暂缓 / 办公 Skill）；3 个范围决策经 AskUserQuestion 拍板
- [x] **OFFICE-1a 镜像 variant 机制**（PR #431）：`SandboxSpec.image_variant: minimal|office`（受控枚举非自由 image）+ supervisor `_select_image`（未知→降级 minimal + warn）+ `AcquireRequest.image_variant` + orchestrator 全链路（6 沙箱工具经 assembly 透传到 run_in_sandbox→acquire，照搬 persistent_workspace）；**warm session 跨 variant 隔离**（minimal session 不被 office 复用，mismatch 主动 destroy 释放 quota）；image_variant=None 向后兼容。过 code 评审（链路完整性 + warm 隔离 MEDIUM 修）
- [x] **OFFICE-1b office 镜像**（PR #432）：`infra/sandbox-image-office/Dockerfile` = `python:3.12-slim` + build-time pip（pandas/openpyxl/python-docx/python-pptx/pypdf/pdfplumber/Pillow/matplotlib，pin 在 requirements.txt）+ `fonts-noto-cjk`/fontconfig + `zh_CN.UTF-8` + 装完卸 pip + 非 root uid 10000；**runner.py 走共享 build context（infra/）不复制**；CI workflow `sandbox-image-office.yml`（PR 仅 build+smoke 无凭证；main/dispatch 推阿里云 ACR，secrets/vars 待填）；matplotlibrc 烤 CJK 默认（`.ttc` 只注册第 0 面 `Noto Sans CJK JP`，含简体汉字）。本地 build+smoke 通过（831MB，中文 xlsx/docx/pptx/图表不乱码、运行时无 pip）；gVisor 验证留目标主机（OFFICE-ADR-1/3）。libreoffice/pandoc 重型转换推后（OFFICE-1c，看真需求）
- [x] **OFFICE-3 平台 skill 导入端点**（PR #437，OFFICE-ADR-5）：`POST /v1/platform/skills/import`（system_admin + bypass_rls，multipart `.skill` ZIP，复用租户 `parse_skill_zip` 解析 + moderation + U-21 strict 威胁扫描）→ 平台 skill（NULL-tenant），租户经 X-6 merged view 自动可见。**不自写/不批量移植 skill**（实证：Anthropic 官方 office skill Proprietary 禁移植、ClawHub 第三方多无 license）。**content_hash 幂等(租户+平台统一)**：同名导入与 latest hash 同则跳过(`200` + `created:false` + 现有 version)、不同则加版本(`201` + `created:true`)；顺带补租户 `POST /v1/skills/import` 此前缺的幂等。纯 API（无 migration，store 方法/`compute_content_hash` 复用现成）
- [ ] **OFFICE-2 国内连接器**（暂缓，backlog）：钉钉/企微/飞书需官方 remote MCP（sse/streamable_http）进 catalog；国内生态薄→等成熟，或单租户 on-prem stdio off-catalog；不破 client-only（OFFICE-ADR-4）

---

## Stream SE — 自我进化 Skill（Self-Evolving Skills，2026-06-06 起）— 设计 [STREAM-SE-DESIGN](./streams/STREAM-SE-DESIGN.md)

把 skill 从"人写、静态启用"（J.7a/X）升级为"agent 可自我生成、被真实证据验证、有界自动演化"。**需求决策（用户确认）：全闭环 + 尽量全自动 + 重放验证为主**。核心判断：helix 已有 ~80% 地基（skill 协议/持久化/curator/高危 gate/curation 全链/aux LLM/judge/沙箱全现成），本 Stream = **补三缺口（自著工具 / 后验蒸馏 / 重放验证）+ 串闭环 + 全自动护栏**。咽喉是**重放验证**（替代人审充当安全闸；三仓与当前 helix 共缺）。实证对照见 [docs/research/2026-06-06-self-evolving-skills.md](./research/2026-06-06-self-evolving-skills.md)。

> 全自动 ≠ 无界自改（[memory:no-design-choice-disguise](../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_no_design_choice_disguise.md)）：四道硬护栏永不松动 —— 高危永远人审 / 跨边界永远人审 / 自动 active 必须有 pass 证据 / 可回滚可熔断。

- [x] **SE-0 设计先行**（PR #TBD）：STREAM-SE-DESIGN 全文（架构 / 数据模型 / SE-1~SE-9 子项 / 治理 / 14 条 Mini-ADR SE-A0~SE-A14）+ 3 仓实证对照 + 与 3 篇论文可追溯映射；3 个需求级决策（范围/治理/验证）经 AskUserQuestion 拍板
- [x] **SE-1 数据模型**（PR #451）：迁移 **0065_skill_evolution**（`skill` 加 visibility/created_by_agent_id/forked_from + visibility CHECK；`skill_version` 加 evolution_origin/distilled_from_*/evolution_round + CHECK；新表 `skill_eval_result` + NULL-tenant RLS ENABLE-only）+ DTO 扩展（`SkillVersion`+4/`Skill`+3/新 `SkillEvalResult` + `SkillVisibility`/`EvolutionOrigin`/`EvalVerdict`/`ReplaySource`）+ ORM（`SkillEvalResultRow`）+ sql `_row_to_dto` 读新列 + `ResourceType` 双镜像加 `skill_eval_result`。**实现细化**：① 迁移号 0058→**0065**（0058 已占用、链头 0064）；② `AuditAction` 事件成员推迟到各自 emit 的 PR（SE-3/4/6/7）加，本 PR 只加数据模型层 `ResourceType`（避免"定义即未用"）。纯增量 + NULL-tenant RLS（SE-A1/A2）；14 单测 + 634 回归绿 + mypy/ruff/pre-commit 通过；迁移在 integration 真 PG 验证
- [x] **SE-2 SkillStore 演化 API**（PR #452）：`create_skill` 加 visibility/created_by_agent_id/forked_from、`add_version` 加 evolution_origin/distilled_from_*/evolution_round（穿过公共方法 + 共享私有助手，平台路径默认不变）；`fork_skill`（基类组合复用原语，单实现）；`record_eval_result`/`list_eval_results`（base+sql+memory）。**实现细化**：visibility 过滤强制（resolve/list 按 agent 上下文）下放 **SE-3**（需 agent_id 在 loader 链路 plumb）；`request/approve promote` + §15.7 权限矩阵下放 **SE-3/SE-7**（需审批面/agent 上下文）；本 PR 只做存储数据层。in-memory 11 测 + sql 集成 3 测（新列 round-trip / fork / `skill_eval_result` RLS 隔离）+ 526 mypy + 335 回归绿（SE-A3）
- [x] **SE-3 in-session 自著（Layer A = J.7b-1）**：4 个 builtin（author/refine/fork/propose）+ provenance + 高危 gate 接线；默认产出停 DRAFT（SE-A4）。**拆 3 子 PR**：
  - [x] **SE-3a owner 身份重构**（PR #453）：经 AskUserQuestion 拍板 owner = **per-user 持久 agent** =(tenant, user_id, agent_name)（跨 manifest 版本稳定，避免版本级 spec id 在 agent 升级时丢失 private skill）。迁移 **0066** 改 SE-1 的 `created_by_agent_id UUID` → `created_by_user_id UUID` + `created_by_agent_name TEXT`（列未被用，无数据迁移）；DTO/ORM/sql `_row_to_dto` + SE-2 `create_skill`/`fork_skill` 签名同步；测试更新。527 mypy + 642 回归绿
  - [x] **SE-3b 自著工具（author/refine/fork）+ 接线**（PR #454）：`tools/skill_authoring.py` 3 个 builtin（owner=(tenant,user_id,agent_name);tenant_id/user_id 从 `ToolContext` 读、agent_name=spec.metadata.name **bake**——无需动 ToolContext/run-state）；KNOWN_BUILTINS +3、`_register_builtin` no-op、`build_agent` 按 manifest 声明注册（缺 skill_store→AgentFactoryError）；`make_agent_builder`/`_build`/app 透传 skill_store+audit_logger；U-22 写时 strict 扫描 + `is_high_risk` 标记 + 新 AuditAction（SKILL_AUTHORED/REFINED/FORKED_BY_AGENT）emit；refine 仅限 owner、默认 DRAFT/agent_private。13 单测 + 528 mypy + 404 orchestrator 回归绿。**细化**：`propose_skill_to_tenant` 推迟（需 promote 审批面）→ **SE-3c**；**visibility 过滤强制**推迟（DRAFT 不被 resolve_by_name 绑，待 agent_private 可激活即 SE-7 接）
  - [x] **SE-3c propose_skill_to_tenant builtin**（本次）：补第 4 个 Layer A builtin（SE-3b 当年 defer，SE-8 只建了管理端 promote 审批 API/store，缺 agent 在 run 内主动发起的工具）。`ProposeSkillToTenantTool` 调既有 `request_skill_promote`（SE-8-1）→ 开 `skill_promote_request` 审批单 + emit `SKILL_PROMOTE_REQUESTED`（resource_type=skill_promote_request）；仅限 owner 自己的 agent_private skill、已 tenant/无版本/已有 pending 各有友好返回（不直接生效，等人审）。`SKILL_AUTHORING_BUILTINS` + `KNOWN_BUILTINS` + `build_skill_authoring_tools` 同步 +propose。4 单测（开单/非 owner forbidden/unknown/dup pending）+ 17 skill_authoring + 147 assembly/agent_factory 回归 + ruff/mypy 绿。**SE-3 四工具齐 → Stream SE 全闭环无残留**
- **SE-4 重放验证 runner（咽喉）**：拆 SE-4a（判定核心）+ SE-4b（编排核心）+ SE-4c（真 graph adapter）
  - [x] **SE-4a grounding 判定核心**（PR #455）：纯逻辑模块 `orchestrator/evolution/grounding.py`——`decide_grounding` 给逐 case baseline/treatment 产 verdict(pass/fail/inconclusive)。**配对显著性**(SE-A5)手写无 scipy：二元 assertion→McNemar 精确二项、序数 judge 分→Wilcoxon 符号秩(n≤15 精确枚举/否则正态近似)；pass = 无新失败 ∧ p<α(0.05) ∧ delta≥θ(0.08) ∧ n≥n_min。**关键**：双侧精确 p 在 n=5 下限 0.0625>0.05 → `n_min` 默认 **6**(显著性下限,T≥5 仅稳定性)。**信号层级**(SE-A5b)`SignalTier` T1 硬verifier/T2 校准GenRM+锚点/T3 → auto-promote 资格分流(高危永不自动)。`to_eval_result` 组装 `SkillEvalResult`(id/created_at 注入,保持纯)。23 单测 + 530 mypy 绿。**深挖溯源**：本 PR 把 4 轮外部检索(CTA swap-order judge / 执行式验证 / 失败归因 14.2% 天花板 / 1% 锚点防坍缩)落进设计文档 SE-4 设计依据 + SE-A5b。**细化**：SignalTier 持久化、GenRM 置信/judge-人一致率的真实计算推迟 SE-4b/SE-7
  - [x] **SE-4b 重放编排核心**（PR #456）：`orchestrator/evolution/replay.py` `ReplayRunner` 编排 with-vs-without 重放 → `decide_grounding`(SE-4a) → `record_eval_result` 落库。真 graph + 真 judge 经**接缝注入**(`TaskRunner`/`ReplayJudge` Protocol),CI 用 fake runner + marker judge 全测(SE-A6:真 graph/Haiku 仅 integration)。**逐点打分**(baseline/treatment 各自独立打分)天然无位置偏置 → 不需 swap-order(那是 pairwise 才需)。assertion 在场=二元硬 verifier 信号(1.0/0.0)、否则 judge 序数;**锚点**(`is_anchor`)treatment 回归即 `anchors_passed=False`(SE-A5b/A9);**held-out 防泄漏**:`trajectory_key==distilled_from_trajectory_key` 的任务重放前剔除(SPARK)。6 单测 + 531 mypy 绿。**细化**:`SignalTier` 持久化、GenRM 置信/judge-人一致率真实计算推迟 SE-7
  - [x] **SE-4c 真 graph adapter**（PR #457）：`orchestrator/evolution/graph_runner.py` `GraphReplayTaskRunner`(实现 SE-4b 的 `TaskRunner` 接缝)——`from_candidate` 按 candidate 构造两个仅差 `spec.skills` 的 AgentSpec(`with_/without_candidate_skill` 纯 model_copy helper,pin `name@N`、去重)、注入式 `agent_builder`→`graph.ainvoke` 跑 baseline/treatment、`_final_answer` 取末条 AIMessage;两变体各 build 一次缓存。**高危沙箱**:注入的 builder 接 sandboxed `ToolEnv` 即天然满足(runner 不特判)。**真 LLM e2e** 归 SE-9 基准/eval harness(CI integration 无 model key),本 PR CI 用 fake builder+fake graph 单测 wiring。7 单测 + 532 mypy 绿。→ **SE-4 咽喉闭环完整(判定 SE-4a + 编排 SE-4b + 真跑 SE-4c)**
- **SE-5 蒸馏 + 归因**：拆 SE-5a（蒸馏）+ SE-5b（归因）。两模块 aux LLM 经接缝注入(CI fake，真接线推 SE-6)
  - [x] **SE-5a 蒸馏**（PR #458）：`control_plane/skill_distiller.py` `SkillDistiller.distill` 给(成功/失败轨迹文本)产结构化 `SkillDraft`(不落库)。**对比式蒸馏**(SkillGen,成功+失败,失败模式编 guardrail,强于 positive-only)+ **抽象 guard**(prompt 强制 type-level;校验拒绝含 UUID/≥12 位数字的草案防记忆碎片)+ **后验 only**(必须有成功 trace,不从计划编)+ `tool_names` 过滤到 `allowed_tools`(防瞎加工具)+ `high_risk` 由 HIGH_RISK_TOOLS 派生。helper `render_trajectory`/`tools_used`(ShareGPT)。`DistillerModel` 接缝 CI fake 测。11 单测 + ruff/mypy 绿。**深挖溯源**:本 PR 把蒸馏/归因检索(SkillGen 架构 1:1 印证 / 抽象过拟合 / Aegis taxonomy / 混合归因法)落进设计文档 SE-5 设计依据 + SE-A7/A8
  - [x] **SE-5b 归因**（PR #459）：`control_plane/skill_attribution.py` 两阶段——**规则前置**(`_rule_execution_reason`:timed_out / setup-env phase / Aegis-anchored 环境 marker[timeout/连接/DNS/限流/sandbox/模块缺失/权限...] → `execution_error` 不调 LLM;**歧义 404/500 不入规则**,交 LLM)+ **LLM 兜底**(`AttributionModel` 接缝 All-at-Once 问 content vs execution;不确定/双 token/不可解析 → 默认 `execution_error` 不学,防坍缩);`should_feed_back`=仅 content 喂回;只粗粒度二分类不步级。9 单测 + ruff/mypy 绿。真 aux LLM 接线 + FailureSignal 组装推 SE-6（SE-A8）→ **SE-5 蒸馏+归因完整**
- **SE-6 进化 worker（Layer B 引擎）**：拆 SE-6a（co-evolve 编排核心）+ SE-6b（worker 外壳 + 真实接线 + lifespan）
  - [x] **SE-6a co-evolve 编排核心**（PR #460）：`control_plane/skill_evolution.py` `evolve()` 纯逻辑——`distill → loop[replay → 若 fail 则 attribute → content 则 revise 再 replay] → grounded/rejected/exhausted/no_draft`,有界轮 `max_rounds`(SkillGen gen-verify-refine + CoEvoSkills 生成/验证分离)。**只读 `verdict`(protocol EvalVerdict)解耦,不引 orchestrator → 无 import cycle**;execution 错不喂回(`should_feed_back` 门控,防坍缩)、inconclusive→rejected(DRAFT 待人审)。4 seam(distill/replay/attribute/revise)注入,CI fake 全测。`RoundRecord` 历史可观测。8 单测 + ruff/mypy 绿（SE-A9）
  - [x] **SE-6b worker 外壳**（PR #461）：`control_plane/skill_evolution_worker.py` `SkillEvolutionWorker`——克隆 CurationWorker 骨架(start/stop/_loop/run_once + `_bypass_rls` 跨租户扫 + `_tenant_scope` 逐租户)。`run_once` 扫 PENDING candidate、过滤 `signal∈{positive_feedback,failed_outcome}`、batch 截断、逐个委托注入的 `CandidateProcessor`、按 outcome 计 `EvolutionTally`。可观测:cycle_errors + grounded counter。per-candidate 重活(组装轨迹/接真 aux LLM/graph replay/落 DRAFT)经 `processor` 注入,CI fake 全测。6 单测 + ruff/mypy 绿（SE-A9）
  - [x] **SE-6c 进化 processor 胶水**（PR #462）：`control_plane/skill_evolution_processor.py` `EvolutionProcessor`(实现 `CandidateProcessor`)——按 candidate 跑 distill→**落 DRAFT**(create_skill+add_version,`evolution_origin='distilled'`+`distilled_from_*`+`agent_private`+owner)→replay→归因→revise→evolve 全链。**cycle 安全**:replay 经 `ReplayInvoker` 接缝注入(真实现含 GraphReplayTaskRunner,SE-6d lazy 接),处理器只依赖 control-plane+protocol → CI 全测(真 distiller/attributor/evolve/SkillStore,仅 LLM/replay 边界 fake)。`SkillEvidence`/`EvidenceProvider`/`HeldOutProvider` 接缝。**修**:测试抓到 SE-5b 环境 marker `oom` 子串误匹配 `boom` → 收紧为 `oomkilled`。4 单测 + ruff/mypy 绿（SE-A9）
  - [x] **SE-6d 真 invoker + providers + lifespan**（PR #463）：`control_plane/skill_evolution_assembly.py`(纯助手:信号层级选择/golden prompt 提取/轨迹首消息,**CI 测**)+ `skill_evolution_wiring.py`(真 `_GraphReplayInvoker`[ReplayRunner+GraphReplayTaskRunner,per-candidate `agent_spec_store` 取 active spec + `make_agent_builder`]+ `_TrajectoryEvidenceProvider`[渲染成功/失败轨迹]+ `_DualSourceHeldOutProvider`[golden 优先含 expected→assertion 锚点 + 同 agent success 轨迹兜底]+ `_AuxText`/`_AuxJudge` aux LLM 适配 + `build_evolution_worker`,**lazy 引 orchestrator 避 cycle**)+ app lifespan gated 接线(`enable_skill_evolution_worker` 默认 OFF,自建 aux_model,start/stop)。**验证边界(诚实)**:真 graph/LLM 路径 CI(pytest+integration)均无 model key 跑不了 → 归 SE-9 基准/手动;持久化/RLS 路径由 SE-1/SE-2 SQL store integration + SE-6c 胶水覆盖。6 纯助手单测(合计 44 SE 单测)+ ruff/mypy 绿 → **SE-6 Layer B 引擎完整(6a 脑/6b 壳/6c 胶水/6d 真接线)**（SE-A9）
- **SE-7 全自动护栏**：拆 SE-7a（auto-promote 策略）+ SE-7b（速率限制 + 熔断）+ SE-7c（promote 接线 + 审计）+ SE-7d（回归回滚）
  - [x] **SE-7a auto-promote 策略**（PR #464）：`control_plane/skill_promotion.py` `decide_promotion` 纯决策——grounded DRAFT → `AUTO_PROMOTE`/`HUMAN_REVIEW`/`HOLD`。优先级(硬到软):未 grounded→HOLD；**高危→永远人审**(SE-A0)；熔断开→人审(降级全人审,SE-A12)；不 eligible(SE-4a 信号层级/锚点)→人审；超速率→人审(SE-A12)；否则自动。`should_auto_promote` 便捷判定。9 单测 + ruff/mypy 绿（SE-A10）
  - [x] **SE-7b 速率限制 + 熔断**（PR #465）：`control_plane/skill_evolution_limits.py` `RateLimiter`(per-key 滑动窗 max/window → `within_limit`→SE-7a `within_rate_limit`)+ `CircuitBreaker`(窗内失败率超阈且样本≥min → `is_open`→SE-7a `breaker_open`,降级全人审)。纯逻辑、注入 `now` 保确定;in-process 状态(单 worker 够,分布式后续)。9 单测 + ruff/mypy 绿（SE-A12）
  - [x] **SE-7c promote 接线 + 审计**（PR #467）：`control_plane/skill_promotion_gate.py` `PromotionGate`——查 `decide_promotion`(7a)+ `RateLimiter`/`CircuitBreaker`(7b)→ `AUTO_PROMOTE` 则 `set_status(ACTIVE)` + rate.record + emit `SKILL_EVOLUTION_AUTO_PROMOTED`(新 AuditAction,protocol-only StrEnum);否则留 DRAFT。`auto_promote_eligible` 从 SE-4a decide_grounding 一路 thread:ReplayOutcome→EvolutionResult(SE-6a)→invoker(SE-6d)→processor(SE-6c 接 gate,clock 注入)。`build_evolution_worker` 装配 gate(rate 5/h、breaker 0.5/min5/24h)+ app lifespan 传 audit。9(gate)+ 1(processor wiring)单测 + 69 全 SE 回归 + 532 mypy 绿（SE-A10/A12）
  - **SE-7d 回归回滚**：拆 SE-7d-1（归因采集）+ SE-7d-2（判定器）+ SE-7d-3（monitor 接线）。按**专表 `skill_run_usage`**(§4.4) skill-centric 归因（**不借 trajectory metadata**——查询 skill-centric vs trajectory run-centric，建模错配）；**按 skill_version 判定不连坐**（SE-A11）
    - [x] **SE-7d-1 归因表 + store**（PR #468）：迁移 `0067_skill_run_usage`(tenant_id NULL-RLS ENABLE-only + `(tenant_id,skill_id,skill_version,created_at)` 窗口索引；带 `skill_version` 按版本判定不连坐 + `thread_id` + `agent_name` 熔断 scope 半键 + `outcome` 复用 `TrajectoryOutcome`) + `SkillRunUsage` DTO + `SkillRunUsageRow` ORM + `SkillStore.record_skill_run_usage`(best-effort 写)/`skill_run_outcomes`(窗口聚合，monitor 读、判定器打分)base+memory+sql 三实现。**专表非 trajectory metadata**:skill-centric 查询 vs run-centric 存储(§4.4)。4 单测 + 646 protocol/persistence 回归 + ruff/format/mypy 绿。采集埋点 + monitor 归 7d-3、判定器归 7d-2（SE-A11）
    - [x] **SE-7d-2 判定器**（PR #469）：`control_plane/skill_rollback.py` `decide_rollback(outcomes, *, promote_baseline, config) -> RollbackDecision`(ROLLBACK/KEEP/INSUFFICIENT)。**统计修正**:回滚非配对场景→不能复用 SE-4a McNemar/Wilcoxon;正确用**单侧精确二项**(窗口成功率显著低于 baseline:`p<α`∧`drop≥θ`)+ **绝对地板**(率<floor 兜底净有害);`cancelled` 排除样本;`n<n_min` 不动。`binomial_cdf` 手写无 scipy。8 单测(insufficient/healthy/显著退化/地板/cancelled 排除/容差内/config 覆盖/cdf)+ ruff/format/mypy 绿（SE-A11）
    - **SE-7d-3 monitor + 动作**：拆 SE-7d-3a（gate，CI 可测）+ SE-7d-3b（monitor 循环 + 埋点 + 接线，真路径）
      - [x] **SE-7d-3a `RollbackGate`**（PR #470）：`control_plane/skill_rollback_gate.py`——对称 SE-7c `PromotionGate`。给 live version + promote 基线 → `skill_run_outcomes` 聚合窗口 → `decide_rollback` → ROLLBACK 则 `set_status(ARCHIVED)` + `breaker.record(ok=False)`(同 `{tenant}:{agent}` scope，回滚=失败 promote→累积熔断全自动通道) + emit `SKILL_EVOLUTION_ROLLED_BACK`(新 AuditAction，protocol-only StrEnum)。**证据落 audit details(rate/baseline/drop/p/n)而非 `skill_eval_result`**——回滚非 replay，`replay_source` CHECK 容不下，审计行才是正确归宿(设计修正)。`since`/`now` 注入保确定。7 单测 + 8 判定器回归 + ruff/format/mypy(459)绿（SE-A11）
      - **SE-7d-3b monitor 循环 + 埋点**：拆 SE-7d-3b-i（monitor + 接线，纯 control-plane）+ SE-7d-3b-ii（埋点，侵入 orchestrator 热路径）
        - [x] **SE-7d-3b-i `RollbackMonitor` + 接线**（PR #471）：`control_plane/skill_rollback_monitor.py`——周期 `run_once` 跨租户(owner RLS 豁免，同 curator，skill 表 ENABLE-only)枚举 ACTIVE distilled version + 取 promote 基线(最近 pass `skill_eval_result.skill_score`，无则 skip)→ 逐个过 `RollbackGate`(注入)。**共享熔断器**:app lifespan 构造 1 个 `CircuitBreaker` 注入 PromotionGate(SE-7c)+ RollbackGate——回滚喂 `ok=False` 才真熔断 auto-promote 通道(否则死效果);`build_evolution_worker` 加可选 `breaker` 参。settings 门 `enable_skill_rollback_monitor`(默认 off)+ interval(3600)+ window_days(7)。**埋点未上线前安全 no-op**。5 sweep 单测(归档/保留/human skip/无基线 skip/跨租户)+ 11 SE 套件回归 + ruff/format/mypy(457 packages)绿（SE-A11/A12）
        - **SE-7d-3b-ii 埋点**（把数据喂进 monitor）。调研修正:主 run 路径 trajectory 录制其实也没接(`run_agent` 3 调用点都不传 `trajectory_recorder`，sse.py `_dispatch_trajectory` 主 run dead)→ 不复用现成 seam，照搬 `SkillActivityRecorder` 模式做同构兄弟。拆 ii-A(基础 CI 可测)+ ii-B(emit 真路径)
          - [x] **SE-7d-3b-ii-A 基础**（PR #472）：helix-common `skill_run_usage.py` `SkillRunUsageRecorder` Protocol + `BoundDistilledSkill` DTO(单向依赖同 `skill_activity.py`，`outcome: str` 避 common→protocol)；`BuiltAgent.bound_distilled_skills` ← 纯 helper `_bound_distilled_skills(resolved_versions, agent_name)`(distilled+tenant 子集、确定序)；control-plane `StoreSkillRunUsageRecorder`(写 `record_skill_run_usage`，best-effort swallow)。零热路径改动。3(helper)+3(recorder)单测 + 82 agent_factory 回归 + pre-commit ruff/mypy(514 含 orchestrator)绿
          - [x] **SE-7d-3b-ii-B emit + 接线**（PR #473）：`RunRecord.bound_distilled_skills` 字段；`run_agent` 加 `skill_run_usage_recorder` 参 + `_dispatch_skill_run_usage` fire-and-forget helper(asyncio.create_task + 硬超时 + swallow，绝不阻塞 run 热路径)挂 sse.py 4 终态分支(success/failed/max_steps/cancelled)紧挨 `_dispatch_trajectory`，每个 bound 版本一行；`AgentRuntime.skill_run_usage_recorder` 注入，3 调用点(api/runs trigger+resume、trigger_firing)填 `built.bound_distilled_skills` + 传 recorder。**emission 与 monitor 同 `enable_skill_rollback_monitor` 门**(一个 flag 控整条 SE-7d 数据路；多数 run 绑 0 distilled→no-op)。3 emit helper 单测 + 164 run 路径回归 + pre-commit + mypy(514 含 orchestrator)绿。**SE-7d / SE-7 彻底闭环**
- **SE-8 admin API / UI**（SE-A13/A13b/A13c/A13d）：review 队列 + eval 证据可视化 + lineage 图 + 手动覆盖 + 紧急停。**SE-8-0 细化设计 2026-06-08**（[STREAM-SE-DESIGN § SE-8](./streams/STREAM-SE-DESIGN.md)）：4 项需求级决策经对话拍板——promote 做完整审批流 / 紧急停做持久 kill-switch / 可视化用手工 SVG+dagre（否决 Recharts）/ **IA 不新建页不加导航**（丰富 Skills 列表当 review 队列 + SkillDetail 展开全貌 + 列表头 scope 感知 kill-switch 开关）；权限统一规则=租户管理员管本租户 / system_admin 跨租户。拆 6 PR：
  - [x] **SE-8-0 细化设计**（本次）：数据模型（迁移 0068 两新表 `skill_promote_request`/`skill_evolution_kill_switch`）+ Store/API/UI 契约 + 4 Mini-ADR（SE-A13b/c/d）+ 前端接线点清单（[memory:admin_ui_wiring_touchpoints]，SDK TS 类型现无 SE-1 字段必补）
  - [x] **SE-8-1 数据层**（本次）：迁移 `0068_skill_promote_kill`（两表 `skill_promote_request`/`skill_evolution_kill_switch`，NULL-tenant RLS ENABLE-only + 部分唯一索引[一 skill 一 pending / 一 global / 每租户一 tenant]）+ DTO `SkillPromoteRequest`/`KillSwitch` + `PromoteRequestStatus`/`KillSwitchScope` Literal（protocol `__all__` 同步）+ ORM `SkillPromoteRequestRow`/`SkillEvolutionKillSwitchRow` + 审计 AuditAction `SKILL_PROMOTE_REQUESTED/APPROVED/REJECTED` + `SKILL_EVOLUTION_KILL_SWITCH_ENGAGED/RELEASED`（protocol StrEnum）+ ResourceType 双 Literal `skill_promote_request`/`skill_evolution_kill_switch`（protocol + control-plane）+ SkillStore（base+sql+memory）promote 审批（request/approve[原子翻 visibility agent_private→tenant]/reject/get/list[+all_tenants]）+ kill-switch（get/set 上插/`is_evolution_halted` global∨tenant）+ `list_skills` 加 `visibility`/`created_by_user_id` 过滤 + `decide_promotion` 加 `evolution_halted` 入参（精度位于 high_risk 之后、breaker 之前）。**细化**：`evolution_origin` 列级过滤推迟（版本级属性，队列主筛=visibility+pending，origin 在 API/UI 由 version 数据派生）；PromotionGate 读 kill-switch 接线归 **SE-8-3**。21 in-memory 单测 + 6 decide_promotion 单测 + 5 SQL integration（promote 审批翻可见性/pending 唯一/RLS 隔离 + kill-switch round-trip/halt）；持久化/RLS 仅 integration 真 PG 验。661 persistence/protocol + 195 control-plane skill 回归 + ruff/format/mypy(183 src) 绿
  - [x] **SE-8-2 读+审批 API**（本次）：独立 router `api/skill_evolution.py`（prefix `/v1/skill-evolution`，**避 `/v1/skills/{skill_id}` UUID path 冲突**）—— `GET promote-requests?status=&tenant_id=*`（review 队列,CrossTenant→all_tenants）+ `POST skills/{id}/promote-requests`（发起,409 dup/404 unknown）+ `POST promote-requests/{rid}/approve|reject`（404/409,approve 原子翻 visibility）+ `GET skills/{id}/eval-results` + `GET skills/{id}/lineage`（skill+versions+forked_from 源）。全 raw JSONResponse + `audit_emit`（SKILL_PROMOTE_*,resource_type=skill_promote_request）+ authz `ensure_tenant_scope`（租户 admin 本租户 / system_admin 跨租户,写不收 `*`）。`skills.py` 附加扩展:`_skill_dict` +visibility/created_by_*/forked_from、`_version_dict` +evolution 溯源、`GET /v1/skills` +visibility/created_by_user_id 过滤。persistence `__init__` 导出两新异常;app 注册 router。**archive 复用既有 PATCH status,不新建**。8 API 单测 + 347 control-plane 回归 + ruff/format/mypy 绿。**细化**:kill-switch API → SE-8-3
  - [x] **SE-8-3 kill-switch API + 接线**（本次）：`api/skill_evolution.py` 加 `GET /v1/skill-evolution/kill-switch`（tenant 行 applied_scope + global NULL-tenant 行 bypass 读 → `effective_halted`）+ `POST .../kill-switch/engage|release`（body scope=tenant/global + reason）。**authz**：tenant scope=本租户;**global scope 仅 system_admin**（`principal.is_system_admin` else 403,set 走 bypass）。emit `SKILL_EVOLUTION_KILL_SWITCH_ENGAGED/RELEASED`。**PromotionGate 接线**：`maybe_promote` 前置 `is_evolution_halted(tenant)` → 喂 `decide_promotion(evolution_halted=)`（gate 已持 skill_store + worker 跨租户 bypass 能见 global 行,无新 lifespan 代码）。2 gate 单测（tenant/global halted→HUMAN_REVIEW 不 active）+ 5 API 单测（engage/release+审计、global 租户 admin 403、global system_admin 200）+ 287 control-plane 回归 + ruff/format 绿。**SE-8 后端 API 闭环（8-1/8-2/8-3）**
  - [x] **SE-8-4 UI 列表+detail**（本次）：SDK `api/skills.ts` 补 SE 字段（SkillRecord +visibility/created_by_*/forked_from、SkillVersion +evolution 溯源,均 optional 不破现有 mock）+ listSkills 加 visibility/createdByUserId 参;新 `api/skill-evolution.ts`（listPromoteRequests/requestPromote/approve/reject,raw 不 envelope）。SkillsList 加 visibility 徽章 + 可见性筛选。SkillDetail 加 `GovernancePanel`（visibility/owner/forked_from + agent_private→tenant **propose**;pending 时 admin **approve/reject**;archive 复用既有 status select 不重复）。i18n 新增 `skill_evolution.*` 块（en interface + en/zh 值,15 键）。e2e fixtures 加 `**/v1/skill-evolution/**` mock 保现有 e2e 绿。5 GovernancePanel vitest + 266 全量 vitest + tsc + vite build + storybook build 绿 + GovernancePanel.stories（3 态）。**显式推迟**：治理流 Playwright e2e → SE-8-5（连 eval viz/lineage/kill-switch UI 一起做完整 e2e;本地无法跑 Playwright 故不盲写）
  - [x] **SE-8-5 UI 可视化+紧急停**（本次）：SDK `api/skill-evolution.ts` 加 listEvalResults/getLineage/getKillSwitch/engage/release + 类型。3 组件(全**手工 SVG 不引 dagre**——单 skill lineage 数据小,hand SVG 更贴"不堆库"基线):`EvalEvidencePanel`(baseline-vs-skill 配对条 + delta + verdict,空态)、`LineagePanel`(SVG fork 边 + 版本时间线带 origin 标签,**防御式容忍残缺 payload**)、`SkillEvolutionKillSwitch`(SkillsList 头部 scope 感知开关:租户 + system_admin 全局,engage 二次确认)。接进 SkillDetail + SkillsList。i18n `skill_evolution.*` +28 键(en interface + en/zh 值)。e2e fixtures 既有 `**/v1/skill-evolution/**` mock + 组件防御保现有 SkillDetail e2e 绿。7 vitest(eval 2/lineage 2/killswitch 3)+ 273 全量 vitest + 5 stories + **新 Playwright e2e `skill-evolution.spec.ts`**(/skills kill-switch + skill detail 三面板 + axe,路由覆盖)+ tsc + vite build + storybook build 绿。**SE-8 全闭环(8-0…8-5)**——自进化治理后端 API + 前端面完整。**修正**:dagre 未引入(数据形态不需图布局库)
- [x] **SE-9 self-evolution 基准 + SLO**（本次）：`tools/eval/self_evolution.py` + `datasets/self_evolution/m0_baseline.yaml`（10 确定性场景,无 LLM CI 路径）证明闭环真有效非 gaming —— **闭环**(ReplayRunner with-vs-without fake 硬verifier → ground pass + delta>0 + auto-eligible → decide_promotion AUTO)、**held-out 成功率↑**、**anti-gaming**(distill-source 轨迹 held-out 剔除,SPARK)、**无误报**(no-gain→inconclusive / harmful→fail 不静默上线)、**安全臂**(kill-switch 降级 AUTO→人审 / 回归 decide_rollback ROLLBACK,健康 KEEP)、**SLO**(replay batch 墙钟预算)。注册进 J.13 baseline 合并门(`run_baseline._RUNNERS` +SE.9,制品 `m0_gate_baseline.yaml` 加 SE.9 块,test 14→15)。真 LLM/graph 路径归 SE-6/SE-4c integration(CI 无 key)。3 se9 + 3 run_baseline 测试 + ruff 绿。**Stream SE 全部完成(SE-0…SE-9)**

> **Stream SE 整体收官** — SE-0 设计 / SE-1~7 自著+蒸馏+重放验证+归因+co-evolve+全自动护栏 / SE-8 admin API+UI / SE-9 基准+SLO 合并门。自进化 skill 从"人写静态启用"升级为"agent 自生成、被重放证据验证、有界自动演化、可治理可回滚可熔断"的一等能力。

> **SE-10 ~ SE-15 借鉴增强（2026-06-08 起，源自 agentic-harness-engineering 对照）** — 6 个增量子项，设计见 [STREAM-SE-DESIGN](./streams/STREAM-SE-DESIGN.md)。三原则：泛化复用现有验证门 / 不新建并行子系统 / 守代码进化红线。
- [x] **SE-10 文本类 harness 组件进化扩展**（SE-A15~A17）：进化对象从「仅 skill」扩到三类无执行风险文本组件——C1 system_prompt 增量 / C2 tool_description 补充 / C3 memory_entry。`skill` 加 `component_type`+`target_tool_name`（迁移 0069 纯增量，无新表/RLS）；`Skill` DTO + `ComponentType` Literal + 互斥校验；3 store create_skill 透传。装配：`_load_skills` 按 component_type 分流渲染为 `<behavior-patch>`/`<tool-note>`/`<long-term-memory>` advisory 系统提示块（不改 ToolSpec，replay 维度不变，`GraphReplayTaskRunner` 零改）；文本组件入 resolved_versions 供回滚覆盖。in-session 3 builtin。**跟进 SE-10b**：Layer B 蒸馏产文本组件 + SE-9 eval component 场景。
- [x] **SE-11 进化变更预测—自动证伪纪律**（SE-A18/A19）：replay 预测=`skill_eval_result` 行（无冗余表）；迁移 0070 单表 `skill_prediction_verdict`（栈在 0069 上）；纯逻辑 `decide_prediction_verdict`（realized_fraction 版本级带）并入 RollbackMonitor sweep，叠加不替代 decide_rollback。**跟进 SE-11b**：generator 自陈预测源 + co-evolve 反哺。
- [x] **SE-12 分层带源失败报告 引擎**（SE-A20~A23）：`FailureReportBuilder` 程序化预聚类零 token + 每桶一次 aux 命名（规则桶跳 LLM）+ evidence 回链 + abstraction guard；DTO control-plane 内部 JSON blob。**跟进 SE-12b**：blob/API/admin-ui FailureReportPanel + 喂 distill。
- [x] **SE-13 进化前领域预研**（SE-A24~A28）：`DomainResearcher`（KB/web/summary 三 Protocol 注入）；冷启动+TTL；产 DRAFT agent_private 先验绝不自动 active；settings 默认 off。**跟进 SE-13 wiring**：真 KB/web/aux + 喂 distill。
- [x] **SE-14 Best-of-N 候选多样性 substrate**（SE-A31~A33）：`skill_best_of_n.py`（DistillHint + BestOfNConfig 默认 OFF + 纯逻辑 pick_winner）+ distiller hint。**跟进 SE-14b**：processor fan-out 编排。
- [x] **SE-15 harness 规范 + linter + profile 对账**（SE-A34~A37）：`docs/HARNESS-COMPLIANCE.md` + `tools/harness/check_harness_compliance.py` 接 CI lint + helix_profile + hermes 对账文档。

---

## Phase M1 — 生产化（6-8 个月）

### 目标
能跑 10+ Agent、3+ 租户、支撑生产流量；填齐 P1 必要项；DX 与 SRE 闭环。

### Entry Criteria
- M0→M1 Gate 通过

### 工作清单

> **M1 排序遵循自下而上**：先做基础设施硬化（沙盒池化、数据生命周期、凭证代理）和可观测核心，再做依赖这些底座的高阶能力（多租户深化、Sub-Agent、Python 插槽），最后是灰度/UI/dogfood。

#### M1-A Sandbox 池化 + 镜像供应链（~4 周）
- [x] Sandbox warm pool（目标 P95 < 500ms）（对账 2026-06-12：HX-6 已交付——READY 池 + replenisher + 镜像预拉，#554-556；池命中率 SLO #13 在册）
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
> **[2026-05-27 关联 / 2026-05-28 状态更新]** Memory 短期 → 长期自动凝结引擎作为 **Capability Uplift Sprint #7** 在 Gate 期交付基础设施（凝结引擎本体 + 防误学约束 + `MemoryStore.archive()` 抽象方法 + `status='archived'` retrieve filter）；**Sprint #7 设计 + 实施 2026-05-28 完成（Mini-ADRs U-33~U-42，PR A #321 + PR B #?）**；M2-C 直接对接凝结引擎输出的 long-term entries（archive 流水线接口在 Sprint #7 预留 `NotImplementedError` 占位，M2-C PR 只需实现不动 schema）；M1 期间用 dogfood 数据调凝结触发策略，M2-C 启动时 archive 路径直接挂凝结后的 long-term 数据。

参考：[research/04-deerflow-source-analysis.md](./research/04-deerflow-source-analysis.md) §"Memory"
- [ ] **memory archive 表 + 冷热分层**（working hot in pgvector / archive cold in S3）
- [ ] **自动晋升策略**（按访问频次 / 时间衰减 → archive）
- [ ] **archive 召回路径**（按需 promote 回 working）

历史项（已覆盖）：~~working layer~~ → 已在 **J.3 + K6/K7**；~~summarization layer~~ → 已在 **L2 ContextCompressor**；~~consolidation engine~~ → 已在 **Capability Uplift Sprint #7（2026-05-28 完成）**

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

## Stream CM — 上下文与记忆管理强化（2026-06-09 起）— 设计 [STREAM-CM-DESIGN](./streams/STREAM-CM-DESIGN.md)

把"上下文与记忆管理"从现状（检索层已强、运行时上下文控制偏薄、error-as-guidance 运行时版空白）补齐。**总基调（已拍板）**：② 混合——DB 真相源 + workspace 文件投影，**单向错时双流**（turn 末 DB→file 投影 / run 始 file→DB 受控 ingest），拒对称双向同步。依据：对比报告 [docs/research/2026-06-09-context-memory-management-comparison.md] + 改进框架 v2（含 2024–2026 外部证据）[docs/research/2026-06-09-helix-context-memory-improvement-framework.md]。

> 关键架构约束（已源码核准）：orchestrator/supervisor **无 docker 卷写权**，投影 DB→file 必须经 warm sandbox `file_ops`；ingest 经 `read_volume_file` 只读读 + orchestrator DB 事务写（与 artifact"内容在卷/元数据在 DB"范式一致）。

- [x] **CM-0 设计先行**（PR #489）：STREAM-CM-DESIGN 全文——总体范围表（CM-0…CM-9/CM-N 映射框架报告 A/B/C/N）+ CM-0（文件投影 + 单向错时双流 + N1 recitation）详设（接缝核准 file:line / 同步模型 / 投影什么 / 挂钩点 / 数据协议变更 / 冲突校验 / 边界 / 可观测 / 测试 / 8 条 Mini-ADR CM-A1~A8 / PR 切分）
- **CM-0 实现**（地基，先行）：
  - [x] **CM-0 PR1 投影纯核心**（PR #490）：`orchestrator/context/workspace_projection.py`（render 纯函数 + `WorkspaceProjector` + `WorkspaceFileWriter` seam + only-if-changed）+ `PlanStep.status`（CM-A5）+ 结构化日志可观测 + 11 unit。**不接图**（pure-core 先行）。CodeQL `py/ineffectual-statement`（Protocol 体 `...`）改 docstring 押 review-thread
  - [x] **CM-0 PR2a 投影接线**（本次）：真 `SandboxWorkspaceWriter`（`tools/file_ops.py`，包 `build_write_wrapper`/`run_in_sandbox`）+ `build_react_graph` 可选 `workspace_writer_factory`（per-turn 绑 ctx）+ `tools_node` best-effort 投影钩子（`_project_workspace_state`）+ `AgentState.last_projection_hash` 游标 + `AuditAction.STATE_PROJECTED` 发射（resource_type=`user_workspace`；单源 StrEnum 无镜像）+ `helix_cm_projection_total{outcome}` + agent_factory gate（persistent_workspace∧supervisor_client）。MEMORY.md 由 tools_node 钩子覆盖（only-if-changed 只写一次）。5 unit（SandboxWorkspaceWriter 真 snippet 往返 + graph wiring）+ test_state 键集 +1；932 orchestrator + 307 protocol 回归绿
  - [x] **CM-0 PR2b-i ingest 纯核心**（本次）：`render_plan_md` 改带 status checkbox 使 PLAN.md round-trippable + `parse_plan_md`（render 精确逆）+ `WorkspaceFileReader` seam + `WorkspaceIngester.ingest_plan`（读→parse→仅 ≠current 返候选；read/parse 失败、无变更均 no-op 永不 raise）+ **PLAN.md 单一 ingest 源、TODO.md/MEMORY.md 转只读**（CM-A5b，修正 §2.3 双源）。12 unit（round-trip/勾选编辑/无 goal 或 step→None/无变更 no-op）。944 orchestrator 回归绿。**不接图**
  - [x] **CM-0 PR2b-ii ingest 接线**（本次）：`SandboxWorkspaceReader`（`tools/file_ops.py`，包 `build_read_wrapper`/`run_in_sandbox`，not_found→None）+ `graph_builder/workspace_ingest.py:make_workspace_ingest_node`（entry-chain，planner 后 agent 前，每 ainvoke 一次=run 始/resume）+ `build_react_graph` 可选 `workspace_ingest_node` 参 + 注入扫描校验（`scan_for_threats` strict 扫 goal+步骤描述，命中即拒 DB 权威）+ `AuditAction.STATE_INGESTED` + `helix_cm_ingest_total{outcome}` + agent_factory gate（同投影）。6 unit（reader 真 snippet 往返/not_found/io_error + wiring 人改回灌/无变更 no-op/注入拒绝）。950 orchestrator + 307 protocol 回归绿。真 live-sandbox e2e 归 integration/手动。**→ CM-0 双向闭环完整（投影 DB→file + ingest file→DB）**
  - [x] **CM-0 PR3 N1 recitation**（本次）：尾部复述机制已由 `_inject_plan` 提供（每 turn append `render_plan` 到上下文尾），PR3 让其**进度可见**——`render_plan` 改 status checkbox（`[ ]/[~]/[x]`）+ `update_plan` 支持每步 status（string|`{description,status}`，agent 标记进度，闭合"标记→显进度→聚焦"环）+ `helix_cm_recitation_chars` gauge。4 unit（render status / update_plan object status / string→pending / 无效 status→pending）+ 现有 render_plan/update_plan 断言不破。954 orchestrator 回归绿。**→ CM-0 地基全部完成（设计 + 投影 + ingest + recitation 五 PR）**
- [x] **CM-1 设计先行**（本次，PR 待开）：STREAM-CM-DESIGN §3 详设——接缝核准 file:line（三层 catch / L-4 注入范式 / ToolSpec 能力元数据 / 无运行时分类体系）+ 分类→通道→注入三段（泛化 L-4）+ 8 类工具错误 grounded taxonomy + retry 受 spec 约束 + 与 L-4 收敛（`failed_mutations`→`tool_failures`、`<mutation-advisory>`→`<recovery-advisory>`，mutation 作 `mutation_not_landed` 一类）+ 数据/协议变更 + 边界 + 可观测 + 测试 + 6 条 Mini-ADR（CM-B1~B6）+ 2-PR 切分
- **CM-1（A1）运行时 error-as-guidance** P0：通用工具失败→grounded 恢复 advisory 注入主循环（泛化 L-4 `<mutation-advisory>`；注入须源自真实工具/执行信号）
  - [x] **CM-1 PR1 分类器纯核心**（本次）：`graph_builder/error_classifier.py`（PR2 移至 `tools/` 破 import 环）（`ToolErrorClass` 8 类 taxonomy + `ClassifiedToolError` frozen dataclass + `classify_tool_error` 纯函数：blocked→`blocked_by_policy`、`ToolNotFoundError`→`unknown_tool`、异常类型(Timeout/Permission/FileNotFound)优先于文本模式 + `_is_retryable` 受 `ToolSpec.idempotent`/`resolved_side_effect` 约束(CM-B5：仅 read_only/idempotent 的 transient 才 retryable) + `render_recovery_advisory` 拼 `<recovery-advisory>` + summary 300 截断）+ 21 unit。**不接图、不动 state、不碰 L-4**（pure-core 先行）。975 orchestrator 回归绿（1 integration error 为本地无 Docker）
  - [x] **CM-1 PR2 接线 + L-4 收敛**（本次）：模块 `git mv` `graph_builder/`→`tools/error_classifier.py`（破 `state→graph_builder→builder→state` import 环，使 `get_type_hints(AgentState)` 可解析；同时对齐"与 mutation_classifier 同层"本意，修正 PR1 的放置）；catch 点(`_invoke_tool`/`_dispatch_tool`)调 `classify_tool_error`→4 元元组带出 `ClassifiedToolError`；`_classify_tool_failure` **优先** mutation 校验(save_artifact raise/未落地均归 `mutation_not_landed` 带 path)、回落 catch 分类 → 聚合进 `tool_failures`；`AgentState` `failed_mutations`→`tool_failures` 泛化；agent_node `_build_recovery_advisory`/`render_recovery_advisory` 取代 `_build_mutation_advisory`(`<recovery-advisory>`，path 保留)+ 注入/reset 不变；`helix_cm_tool_error_total{error_class,tool}`+`helix_cm_recovery_advisory_chars`。迁移 L-4 测试(test_mutation_advisory→test_recovery_advisory，含"失败只读工具现也 advise"的 CM-1 增量 e2e)+test_state/test_system_byte_stable/test_tool_audit。977 orchestrator 回归绿。**→ CM-1 完成**
- [x] **CM-2 设计先行**（本次）：STREAM-CM-DESIGN §4 详设——接缝核准 file:line（compressor 唯一现有闸/裁剪只作用当次 prompt 不改 checkpoint/leading system 冻结/复用 estimate_tokens/注入块在 window 后加）+ token-gate→轮次裁剪→compressor 兜底三段 + HumanMessage 边界切保 ToolCall↔ToolResult 配对（OpenClaw #1084）+ 保首轮+最近 N 轮 + 数据/协议变更（新 `working_window.py`/`WorkingMemoryPolicy`）+ 边界 + 可观测 + 测试 + 6 条 Mini-ADR（CM-C1~C6）+ 2-PR 切分
- **CM-2（A2）working memory 滑窗** P0：`agent_node` compressor 前廉价轮次截断（保 ToolCall↔ToolResult 配对）
  - [x] **CM-2 PR1 滑窗纯核心**（PR #499）：`context/working_window.py`（`TrimResult`+`trim_to_recent_turns` 纯函数：按 HumanMessage 边界切保「首轮+最近 N 轮」、天然保 ToolCall↔ToolResult 配对无需 pair-repair；`WorkingWindow` dataclass：`should_trim` token-gate 复用 `compressor.estimate_tokens`（未溢出 no-op 零行为变更）+`apply`；三种边界 no-op/去重）+ 13 unit（token-gate/保首轮/配对完整/leading 冻结/三边界/dropped_turns/不可变）。不接图、不动 protocol（pure-core 先行）。483 行净增
  - [x] **CM-2 PR2 接线**（本次）：`WorkingMemoryPolicy`（enabled/threshold_pct/max_recent_turns/keep_first_turn，保守默认 ⇒ 现有 manifest 零行为变更）→`PolicySpec.working_memory`；factory `wm_policy.enabled` 则构造 `WorkingWindow` 传 `build_react_graph`（新 `working_window` 参）；`agent_node` 取 history 后、plan/memory/advisory 注入**前**插 `working_window.apply`（只裁当次 prompt 视图不改 checkpoint）+ `helix_cm_working_window_trim_total{outcome=trimmed/noop}`+`helix_cm_working_window_dropped_turns`；3 集成测（长 history 裁剪+checkpoint 完整+未溢出 no-op+working_window=None 原路径）+ 4 protocol policy 测（默认/自定义/边界拒绝/extra-forbid）。991 orchestrator + 311 protocol 回归绿。**→ CM-2 完成（设计 + 纯核心 + 接线 三 PR）**
- [x] **CM-3 设计先行**（本次）：STREAM-CM-DESIGN §5 详设——接缝核准 file:line（中段被摘要即丢弃无 flush/summary 易逝非耐久/writeback 只 run 末且抓压缩后 state/writeback 抽取管道可复用/compress 在 agent_node 有 token+config/flush 与 writeback 同 gate）+ 共享抽取核心 `flush_messages_to_memory` + compressor `on_pre_compaction` 回调两段 + 与 writeback 复用收敛 + 数据/协议变更（compressor 不反向依赖 memory 层）+ 边界 + 可观测 + 测试 + 6 条 Mini-ADR（CM-D1~D6）+ 2-PR 切分
- **CM-3（A3）压缩前 flush** P1：compressor 压缩前回调 → 复用 `memory_writeback` 中途落盘
  - [x] **CM-3 PR1 共享抽取核心 + compressor 回调**（PR #502）：`memory.py` 抽模块级 `flush_messages_to_memory`（render→LLM 抽取→parse→embed→write + blocked/DLQ/best-effort/cancel re-raise；`memory_writeback_node` 改调它变薄，行为完全保持；`log_label` 区分来源、writeback 日志串字节不变）；`compressor.py` `compress`/`_compress_once` 增 `on_pre_compaction(middle)`（丢弃中段前且摘要 LLM 前 await，compressor 不持 store/embedder）+ 导出 `PreCompactionHook`。7 unit（flush 抽取/空/blocked/DLQ/cancel re-raise + 回调时序+入参+None 等价）。998 orchestrator 回归绿（含 35 memory+compressor 既有测试不回归）。不接 factory、不动 policy（pure-core 先行）
  - [x] **CM-3 PR2 接线**（本次）：`ContextCompressionPolicy.flush_before_compaction`（默认 True）；`memory.py` `make_pre_compaction_flush`（config-bound 回调，no-user no-op，`memory.precompaction_flush` 日志）；factory `_build_memory_nodes` 返 3 元、`write_back ∧ flush_before_compaction` 时构造回调传 `build_react_graph`（新 `pre_compaction_flush` 参）；agent_node 调 compress 时绑 config+token 的 `on_pre_compaction`（`outcome=flushed/empty` 计数 + memories gauge）；`helix_cm_precompaction_flush_total{outcome}`+`helix_cm_precompaction_flush_memories`；2 集成测（compaction 触发 flush 到 fake store+memory 未启用零 flush）+ 3 protocol policy 测（默认/关闭/extra-forbid）。1000 orchestrator + 78 protocol 回归绿。**→ CM-3 完成（设计 + 共享核心 + 接线 三 PR）**
- [x] **CM-4 设计先行**（本次）：STREAM-CM-DESIGN §6 详设——接缝核准 file:line（reranker 抽象+LLMReranker 已存于 tools/knowledge / 知识检索 J.5 已用"宽召回→rerank→top-k" / control-plane 已建 DynamicResolvingReranker / 记忆召回 J.3 是唯一未接 rerank 的路径 / reranker 经 MemoryEnv 注入同 embedder）+ 宽召回→rerank→top-k 镜像知识检索 + 复用同一 reranker 实例零新造 + best-effort 降级 RRF 序 + 数据/协议变更 + 边界 + 可观测 + 测试（效益自测留 CM-N5）+ 6 条 Mini-ADR（CM-E1~E6）+ 2-PR 切分
- **CM-4（B5）reranker 接通** P1：memory recall Hybrid 后接 cross-encoder rerank（配置已预留 migration 0051）
  - [x] **CM-4 PR1 记忆召回接 rerank（orchestrator）**（PR #505）：`make_memory_recall_node` 加可选 `reranker`（从 tools/knowledge 导 `Reranker`）+ 宽召回 `max(top_k,_MEMORY_RERANK_RECALL_LIMIT=20)` + `_rerank_memories`（best-effort 回落 `candidates[:top_k]`，cancel re-raise，rerank 在 redact 前）；`MemoryEnv.reranker` 字段（embedder 同款注入）；factory 透传 `env.reranker`；`record_memory_rerank{outcome=reranked/degraded}`（helix_cm_memory_rerank_total）+ 5 unit（重排+截 top_k/宽召回 limit/None 零变更/失败降级不丢记忆/空召回跳过）。1005 orchestrator + 302 common 回归绿
  - [x] **CM-4 PR2 control-plane 激活**（本次）：`app.py` `MemoryEnv(..., reranker=reranker)`（复用 `:834` 已建的 `DynamicResolvingReranker`，同一实例喂知识检索 J.5 与记忆召回 J.3 两路）+ orchestrator factory 透传测（`_build_memory_nodes` 把 `MemoryEnv.reranker` 传到 recall 节点、spy reranker 被调）。**→ CM-4 完成（设计 + orchestrator + 激活 三 PR）**
- [x] **CM-5 设计先行**（本次）：STREAM-CM-DESIGN §7 详设——接缝核准 file:line（截断在 6 工具内部策略各异/meta["truncated"] 在 ToolMessage 构造丢弃 LLM 不知情/首批 4 工具完整输出在 orchestrator 内存可得/CM-0 writer 范式整体复用/恢复工具已存在）+ 框架报告假设修正（artifact 表只是元数据注册，存储基底=workspace 文件，不入 artifact 表不接 ObjectStore）+ 工具截留→中央外部化→引用 footer 三段（截断策略留工具内是职责正确非成本折中）+ read 类豁免双保险防 persist→read→persist + 数据/协议变更（ToolResult.full_content/tools/overflow.py/复用 workspace_writer_factory 零新参数）+ 边界 + 可观测 + 测试 + 6 条 Mini-ADR（CM-F1~F6）+ 2-PR 切分
- **CM-5（B6）可恢复压缩** P1：超大工具结果外部化 workspace 文件 + 虚拟引用 + read 类豁免
  - [x] **CM-5 PR1 纯核心**（PR #508）：`ToolResult.full_content`（截断时携带完整渲染，read_only 禁填）+ 4 工具带出全文（`format_sandbox_outcome` 任一流截即全量渲染 / http body∨headers 截即全量 / mcp middle-trim 即原文）+ `tools/overflow.py` 纯函数（`overflow_rel_path` 组件 sanitize 防路径注入+adhoc 回落 / `clamp_overflow` 2M 截顶+尾注记 / `render_overflow_footer` 引用块）+ 8 unit + 4 工具截断测试各加 full_content 断言。1014 orchestrator 回归绿。不接图（pure-core 先行）
  - [x] **CM-5 PR2 接线**（本次）：`_invoke_tool` 成功路径接 `_externalize_tool_overflow`（best-effort：full_content∧writer∧非 read_only → 写 `.tool_results/` + footer 追加；写失败 degraded 内容原样；CancelledError/RunCancelledError re-raise 不吞）；tools_node 每 turn 从 CM-0 `workspace_writer_factory` 建 overflow writer（零新 build_react_graph 参数，gate 同投影）经 `_dispatch_tool` 透传；`helix_cm_tool_overflow_total{outcome=externalized/degraded,tool}` + `helix_cm_tool_overflow_chars` + `tool.overflow` 日志；4 集成测（外部化+footer 进 checkpoint/无 writer 字节一致/写失败降级/read_only 中央双保险）。1018 orchestrator 回归绿。**→ CM-5 完成（设计 + 纯核心 + 接线 三 PR）**
- [x] **CM-6 设计先行**（本次）：STREAM-CM-DESIGN §8 详设——管线顺序修正（范围表原句"都在 retrieve()"写于 CM-4 前；MMR 在 retrieve 内做会被 rerank 纯相关性重排打散 ⇒ 衰减进 retrieve()、MMR 放 orchestrator rerank 后殿后，2026-06-10 用户拍板）+ 接缝核准 file:line（RRF Python 侧两 store 镜像/`rrf_fuse` 不带分需并列新增 scored 版/两 store 各有 hybrid+纯向量两路/embedding 随行返回 MMR 无阻碍/retrieve 唯一在线调用方是召回路径）+ 衰减公式 `0.5+0.5*2^(-age_days/30)` 带 floor 防老记忆埋死（基于 last_used_at 被用即保鲜）+ MMR λ=0.7 greedy cosine + 召回恒宽 max(top_k,20) + 数据/协议变更（helix-common decay/mmr 纯函数，签名/协议/schema 零变更）+ 边界 + 可观测 + 测试 + 6 条 Mini-ADR（CM-G1~G6）+ 2-PR 切分
- **CM-6（B4）MMR + 时间衰减** P1：时间衰减进 retrieve() + MMR 在 rerank 后（叠加增益自测留 CM-N5）
  - [x] **CM-6 PR1 纯核心**（PR #511）：helix-common `rrf_fuse_scored`（返回 `(item, score)` 对；`rrf_fuse` 改薄包装、sorted 稳定性保 tie 序字节不变，现有 10 rrf 测试不改即过）+ `search/decay.py`（`temporal_decay_factor`：`0.5+0.5*2^(-age/half_life)`，floor 0.5/负龄钳 0/单调不增）+ `search/mmr.py`（`mmr_select` greedy λ=0.7 cosine：tie 保调用方序/维度不齐与空 embedding 跳过/零范数得 0 不 NaN）+ 7 decay + 7 mmr unit。316 common 回归绿。不接调用方（pure-core 先行）
  - [x] **CM-6 PR2 接线**（本次）：两 store retrieve() 接衰减（sql `_decay_for` last_used_at 回落 created_at/naive 钳 UTC + hybrid `rrf_fuse_scored`×decay 重排 + 纯向量 `(1-distance/2)×decay`；in-memory 镜像）；orchestrator `_mmr_memories`（rerank 后 redact 前，异常/全维度不齐降级输入序 `[:top_k]`）+ **rerank 改全序输出**（`top_k=len(候选)`，否则 MMR 只能在已截集合重排，最终截断移交 MMR，§8.2 细化）+ 召回恒宽 `max(top_k,20)`（`_MEMORY_RERANK_RECALL_LIMIT`→`_MEMORY_RECALL_WIDE_LIMIT`）+ `record_memory_mmr{outcome=applied/degraded}`；测试：3 in-memory 衰减（同分新者先/floor 老 canonical 不被埋/hybrid 路径）+ 1 SQL integration（双路衰减）+ 3 MMR recall（近重复去冗/rerank→MMR 顺序/维度不齐降级）+ rerank 测试更新恒宽语义。1694 全仓回归绿。**→ CM-6 完成（设计 + 纯核心 + 接线 三 PR）**
- [x] **CM-7 设计先行**（本次）：STREAM-CM-DESIGN §9 详设——范围拍板（①compressor 摘要强化 + ②run 末 writeback 显式操作，②不进 CM-3 压缩前 flush——turn 内延迟敏感，2026-06-10 用户确认；consolidator 无矛盾解决能力故②是真缺口非重复机制）+ 接缝核准 file:line（摘要无 reference-only 语义/二次压缩链式摘要的摘要/summary 非 leading 位置改格式不破 L-1/writeback 直写零比对/去重只认 exact hash/update_content+soft_delete 原语已存在）+ ①preamble 进 wrapper 内+三段结构（Facts/Decisions/Pending）+UPDATE/FRESH 双模式（识别 middle 最后一条前次 summary）+ ②近邻检索（cosine≥0.80 才进 LLM，无近邻直 ADD 零成本）+批量 ops（ADD/UPDATE/DELETE/NOOP）+一切失败回落直写 ADD + `MemoryPolicy.reconcile_writes` 默认 True + 边界 + 可观测（helix_cm_memory_reconcile_total{op}）+ 测试 + 6 条 Mini-ADR（CM-H1~H6）+ 2-PR 切分
- **CM-7（B7）结构化 note** P2：摘要"背景非指令"语义+增量更新 ① + writeback 显式操作 ②（A-MEM/Mem0）
  - [x] **CM-7 PR1 compressor 摘要强化（①）**（PR #514）：`_SUMMARY_PREAMBLE` reference-only 声明进 wrapper 内（标签/消息类型/位置全不变，L-1 零扰动）+ 三段结构 prompt（`_SUMMARY_STRUCTURE_RULES` 共享，Facts/Decisions/Pending）+ UPDATE/FRESH 双模式（`_extract_prior_summary` 取 middle 最后一条前次 summary、早者并入转录链收敛、legacy 无 preamble 可解析 → `_summarise_update` PREVIOUS SUMMARY+NEW EVENTS 增量合并）+ `context_compressor.summary mode` 日志 + 5 新 unit + 2 既有测试窗口 200→280（preamble ~33 token 头室）。1026 orchestrator 回归绿
  - [x] **CM-7 PR2 writeback 显式操作（②，收尾）**（本次）：`flush_messages_to_memory(reconcile=)`（默认 False；run 末 writeback 传 True、CM-3 flush 保持直写）+ `_reconcile_and_apply`（近邻 retrieve(limit=3)→cosine≥0.80 过滤→无近邻直 ADD 零 LLM 成本→批量 ops LLM `{"ops":[{index,op,target_id}]}`→ADD/UPDATE(update_content 重 embed)/DELETE(soft_delete 候选不存)/NOOP；解析失败/目标失踪/任何异常回落直写 ADD，cancel re-raise）+ `LongTermMemorySpec.reconcile_writes`（默认 True）+ factory 透传 + `record_memory_reconcile{op=add/update/delete/noop/degraded}`；7 reconcile unit（无近邻零 ops 调用/UPDATE 改写/NOOP/DELETE 撤销/乱 JSON 降级/未知 target 降级/reconcile=False 现状直写单 LLM 调用）+ 3 protocol（默认/关闭/extra-forbid）。1661 全链回归绿。**→ CM-7 完成（设计 + ① + ② 三 PR）**
- [x] **CM-8 设计先行**（本次）：STREAM-CM-DESIGN §10 详设——范围修正（modify UX 已被早期 H.x 实现：ApprovalCard 完整 Edit→Monaco→modify+modified_args 链路在册，CM-8 不重做，2026-06-10 用户确认）+ 真缺口取证（plan 前后端全缺：零字段/零端点/零外部写路径；approval resume `as_node="agent"` 直入 tools 不经 entry chain ⇒ CM-0 PLAN.md ingest 在 pause→resume 不跑，暂停期手改丢失）+ plan 读写 API 设计（GET checkpointer 直读零 build 轮询友好 / PUT build+aupdate_state resume 同款，仅非 RUNNING 409，注入 strict 扫描，PLAN_EDITED audit 双 Literal）+ resume ingest 修复（tools_node resume 分支调既有 ingest 节点，零新参数）+ 前端 PlanPanel（结构化编辑非 raw JSON，遵守设计基线）+ 边界 + 可观测 + 测试 + 6 条 Mini-ADR（CM-I1~I6）+ 4-PR 切分
- **CM-8（C8）plan UI 通道 + resume ingest 修复** P2：~~modify UX~~ 已实现；plan 读写 API + PlanPanel + ingest 漏洞 + approval E2E
  - [x] **CM-8 PR1 后端 plan API**（PR #517）：`api/plan.py` GET（AgentRuntime 缓存 build + aget_state，轮询零重建）/PUT（仅非 PENDING/RUNNING 409 + strict 注入扫描 422 + aupdate_state——有历史 as_node="agent"、fresh thread "__start__" 防空 messages 条件边炸）+ `AuditAction.PLAN_EDITED`（单源 StrEnum，实情优于设计的双 Literal 假设；resource_type 复用 session）+ 7 API 测试。1503 回归绿
  - [x] **CM-8 PR2 resume ingest 修复（orchestrator）**（PR #518）：tools_node resume 分支调 workspace_ingest_node 一次（approve/modify 并入 result_dict 放最前、工具自身 plan 写仍胜；reject 早返回同样携带——人工编辑不因否决作废）+ 3 集成测（approve 回灌/reject 回灌不派发/无接线零变更）。1036 回归绿
  - [x] **CM-8 PR3 前端 PlanPanel**（PR #519）：`api/plan.ts` SDK（raw payload 对账 runs.ts 风格，204→null）+ `run_detail/PlanPanel.tsx`（只读视图：goal+步骤 ○/◐/✓ 色形字三重+完成度+空态；结构化编辑：goal/步骤行内/状态 Select/增删+草稿校验；live 时 Edit 禁用+tooltip；DI 缝 fetchPlan/savePlan 供 Storybook）+ RunDetail 接入（pollTick 复用 3s 轮询节奏）+ i18n plan_panel.* en/zh-CN 双语 + 3 Storybook（ReadView/Empty/Locked）+ 6 vitest（读/空/锁/编辑提交 payload/增删校验/取消零写）。279 前端测试 + tsc 全绿
  - [x] **CM-8 PR4 E2E（收尾）**（本次）：`e2e/run_detail.spec.ts` 5 测——approval 卡渲染+approve 决议 POST（PR 7e 残债闭）/reject 决议/plan 面板读+编辑 PUT 往返/live 锁 Edit/approval+plan 同屏 axe；mock 路由叠加 fixtures 范式 + 条件 dev-login toggle（本地 VITE_OIDC 与 CI 双兼容）。本地 5/5 过。**→ CM-8 完成（设计 + 后端 API + resume ingest 修复 + 前端 PlanPanel + E2E 五 PR）**
- [x] **CM-9 设计先行**（本次）：STREAM-CM-DESIGN §11 详设——范围修正（iteration budget 与调用指纹去重已实现：max_iterations+MaxStepsExceededError+L-5 refund / E.10.5 LoopDetectionMiddleware 指纹窗口 3；per-run plan mode 开关砍掉，显式决策记录 CM-J1，2026-06-10 用户拍板）+ 线格式 claude-api skill 核准（thinking adaptive + output_config.effort GA 无 beta header；budget_tokens 弃用；haiku 发 effort 400；**opus-4-7/4-8 发 temperature 400——helix 现存隐患**）+ ②effort 接通（ModelSpec.effort/adaptive_thinking 默认零行为变更 + 目录能力位 supports_effort/supports_sampling + build 期 fail-fast gate + temperature 条件化修 400）+ N4 撞限升档（escalated caller 零 LLMCaller 协议变更；双信号：loop 命中经 middleware payload→escalate_next 通道 ∨ step≥75% max_steps，一档封顶，2026-06-10 用户拍板）+ 边界 + 可观测 + 测试 + 6 条 Mini-ADR（CM-J1~J6）+ 2-PR 切分
- **CM-9（C9 + N4）effort 档位 + 撞限升档** P2：~~plan mode 开关~~（砍）~~iteration budget~~ ~~指纹去重~~（已实现）
  - [x] **CM-9 PR1 effort 基建**（PR #522）：`ModelSpec.effort/adaptive_thinking`（默认 None/False 零行为变更，extra-forbid 校验）+ `ModelEntry.effort/sampling` 能力位（opus-4-8 effort✓sampling✗ / sonnet-4-6 双✓ / haiku effort✗）+ `catalog_entry` 单源 lookup + adapter 三处签名（Protocol/HTTP/Recording 默认参零破坏）body 条件携带 thinking/output_config + `AnthropicProvider.effort/adaptive_thinking` + factory gate（haiku+effort build 期 AgentFactoryError fail-fast；**opus-4-8 temperature 条件剔除修现存 400**；目录外不 gate）+ 测试：3 protocol + 2 catalog + 3 adapter（含 MockTransport 线级 body 断言）+ 4 factory gate。1365 回归绿
  - [x] **CM-9 PR2 撞限升档（收尾）**（本次）：factory `_escalated_model`（梯子 None/low→medium→high→max，max 封顶；启用条件=算力字段已配 ∧ 目录 effort 支持，目录外不升）+ `build_react_graph(escalated_llm_caller=)` + loop middleware 命中置 `ctx.payload["loop_detected"]` + `AgentState.escalate_next` transient 通道（loop 命中收尾 run 落 checkpoint，**下一 run 首步升档**，消费即 reset）+ agent_node 双信号切换（loop ∨ step×4≥max×3 即 75%）+ `helix_cm_effort_escalation_total{signal=loop/budget}`；测试：3 集成（预算切换/loop 跨 run 升档+reset/无 escalated 零变更）+ 4 梯子推导 + state 键集 +1。1707 回归绿。**→ CM-9 完成（设计 + effort 基建 + 升档 三 PR）**
- [x] **CM-N5 设计先行**（本次）：STREAM-CM-DESIGN §12 详设——范围拍板（分层全做 + 端到端大档全量：LongMemEval_S 500 题 + LoCoMo cat1-4 全 1540 题，2026-06-10 用户拍板）+ 一手取证（LongMemEval MIT/answer_session_ids+has_answer 零 LLM 检索 ground truth；LoCoMo CC BY-NC 不 vendor；cat5/abstention 不进 overall——Zep 84→58 争议教训）+ 两层四态架构（P0 检索层机械回归闸：消融矩阵 decay×MMR×rerank×hybrid 归因 CM-4/6 / P1 端到端 LLM judge 横比：ingestion 走 flush_messages_to_memory reconcile=True 验 CM-7）+ 时间平移代替 now 注入（decay 可验零协议变更）+ SQL write 尊重 created_at 补全 + 断点续跑 + 基线 YAML 落盘 + 7 条 Mini-ADR（CM-K1~K7）+ 3-PR 切分 + 基线真跑
- **CM-N5 评测基线** 贯穿：LongMemEval + LoCoMo 自测纳入 eval（验 CM-4/6/7）
  - [x] **CM-N5 PR2 P0 检索层**（本次）：`tools/eval/longmem/` 子包——download（sha256 pin 三数据集 + 流式下载 .part 原子换名 + cache env 可覆写）/adapter（双格式 locale 无关日期解析 + turn/session 双粒度 + LoCoMo cat5 默认排除 + 共享语料 tuple 防 1986x600 复制 + CM-K4 时间平移纯函数）/metrics（recall/ndcg/mrr 纯函数）/retrieval（消融矩阵 hybrid/decay/mmr/rerank 全数据-参数级零 monkeypatch；decay-off=写入不带时间戳；按共享语料分组建库省真 embedder 成本；threat-scan 预滤计数 blocked/deduped 不静默）+ CM-K7 扩两列（created_at+last_used_at 都尊重 item 值，否则 SQL decay anchor 永远 fresh）+ synthetic fixture 两份（零 license 暴露）+ 32 单测（decay tiebreak/rerank 翻转/共享建库计数断言）+ 1 integration（时间戳 passthrough+decay 排序）。3893 回归绿
  - [x] **CM-N5 PR3 P1 端到端（收尾）**（本次）：`endtoend.py`（ingestion 逐 session 经 `flush_messages_to_memory(reconcile=True)` CM-7 真路径 + session 日期 transcript 头注入 + 检索→MMR→reading→judge；**单库口径修订**：LoCoMo 双库喂同 transcript 抽取趋同只剩 2x 成本，改单 per-user 库记 fingerprint）+ `judge.py`（LongMemEval per-type 官方 prompt 5 模板 + Mem0 ACCURACY_PROMPT 逐字移植，verdict 按官方规则 substring yes / JSON label==CORRECT；judge=claude 差异标注 CM-K6）+ `anthropic_client.py`（httpx 轻传输 _judge.py 同形；temperature 按目录 sampling 能力位条件发）+ `runner.py`+`run_longmem.py` CLI（消融臂注册表 / jsonl 断点续跑撕裂行容错 / merge fresh-wins / baseline YAML 分节合并带 fingerprint）+ 23 单测（judge 模板路由/verdict 解析/管线冒烟/reconcile 降级 ADD/resume 跳过/abstention 模板/baseline 合并）；CLI fixture 实跑 4 臂消融数字可复现。tools/eval 151 全绿
  - [x] **CM-N5 PR4 真跑准备（Qwen 全栈 + 提速）**（本次）：`openai_client.py`（DashScope compatible-mode LLMCaller/TextJudge，judge 差异进 fingerprint）+ `CachedEmbedder`（sqlite content-hash 缓存防 5 臂 5x 重复 embed + batch<=10 分批并发）+ 两层 `concurrency`（共享 semaphore + gather 保序，=1 与串行等价 CI 确定性不变，jsonl 写锁）+ runner `--llm-provider/--concurrency` + 8 新测（payload 渲染/缓存命中/持久化/model 隔离/batch 上限/两层并发≡串行）。3923 回归绿
  - [ ] **基线真跑**：本地全 Qwen 全量（层 1 + 层 2），数字进 `baselines/`
- [x] **CM-10 设计先行**（本次）：STREAM-CM-DESIGN §13 详设——立项动机（CM-9 effort/撞限升档 anthropic 专属，用户 2026-06-10 拍板即做）+ web 调研核准全 9 provider 思考深度参数（openai/azure/deepseek=reasoning_effort 多档 / qwen/doubao=thinking_budget 连续 / glm/kimi=仅开关 / 纯思考模型无控制；OpenRouter effort-budget 换算先例）+ 三形态能力位（ModelEntry.effort:bool 替换为 thinking:effort|budget|toggle|None，CM-9 刚 ship 下游可控替换优于双字段漂移）+ ModelSpec 零变更（effort/adaptive_thinking 语义升级厂商中立）+ factory `_thinking_payload` 翻译层 + `OpenAIClient.extra_body` 统一通道 + 升档梯子 per 形态（toggle 退化"开思考"一跳）+ 目录外 openai-compat 不发（与 CM-9 anthropic 原样发的差异显式记录）+ 7 条 Mini-ADR（CM-L1~L7）+ 3-PR 切分
- **CM-10 跨厂商思考深度统一** P2：CM-9 增益从 anthropic 推广到全厂商
  - [x] **CM-10 PR2 能力位 + 翻译层**（PR #529）：`ModelEntry.effort: bool` 替换为 `thinking: effort|budget|toggle|None`（CM-L1 全仓 sweep：factory gate/_escalated_model/目录测试）+ 目录全厂商标注（openai gpt-5.5 系/deepseek v4=effort；qwen3 商业系/doubao seed-2.0=budget；glm/kimi k2.5+=toggle；纯思考与 haiku=None）+ `_thinking_payload` 纯函数（effort 直传 reasoning_effort / budget OpenRouter 比例换算 clamp 1024..81920 / toggle 归一 / adaptive-only per 形态 / anthropic 与目录外 None）+ `OpenAIClient.extra_body` 三处签名 + HTTP merge 顶层 + `OpenAIProvider.thinking_payload` 透传。11 新测，3934 回归绿
  - [x] **CM-10 PR3 gate + 升档推广（收尾）**（本次）：`_build_provider` 全 compat 分支（openai/5 国产厂商/self-hosted/azure）接线 thinking_payload + gate 推广（目录内无 thinking 控制配 effort 即 build 期报错，厂商中立文案；toggle 档位归一 DEBUG）+ `_escalated_model` 去 anthropic 短路按 thinking 形态分流（effort/budget 走 CM-9 四档梯子；toggle 一跳 untouched→high 即开思考、已开无可升——untouched 升档语义注记见 §13.7）+ 7 新测（跨厂商梯子/toggle 一跳/纯思考永不升/gate fail-fast/payload 接线/目录外不发/untouched 零 payload）。**→ CM-10 完成（设计 + 翻译层 + 升档推广 三 PR）**
- [x] **CM-11 事件驱动升档（设计 + 实现一 PR）**（本次）：博客「驾驭工程」动态算力分配对账发现的唯一真 gap——CM-9 升档机制（`escalated_llm_caller` + `escalate_next` + `helix_cm_effort_escalation_total{signal}`）完备但触发只有「系统压力」双信号（loop/budget），缺「任务语义」双触发。STREAM-CM-DESIGN §14 详设 + 实现：复用 CM-9 机制加两信号零新基建——**微观** `error_signal`=上一批工具非 `transient` 真异常本 turn 升档（`tool_failures`/CM-1 已在手，信号在 LLM 调用前可知故本 turn 而非 arm-next；`transient` 排除对齐 CM-B5）；**宏观** `goal_signal`=`plan.goal` 较上 turn 变（重规划 / 人工 PLAN.md 编辑经 CM-0 ingest 回灌）→ 一次深推理重校，**首个 plan 不算变更**（planner 已深想，需 prior goal 比对避误触发）。新 `AgentState.last_plan_goal` 通道每 turn rebaseline（一次变更只升一档 turn）；signal label 优先序 loop/budget/error/goal 复用同 metric（新 label 值非新 metric，零 catalog 漂移）。4 新测（非 transient 升/transient 不升/goal 变更升+rebaseline/首 plan 不升）+ test_state 键集 +1。1152 orchestrator 单元回归绿。1 条 Mini-ADR（CM-M1）。**→ CM-11 完成**
- **工具结果上下文预算（tool-result-context-budget）** 贯穿：超大/累积工具结果撑爆上下文 → 超时/retry 死循环（live run `254cdad8` 实证 ~140k token）。设计 `docs/design/tool-result-context-budget.md`：Phase 1 通用外化（每结果，eager，机械）+ Phase 2 机械剪枝闸（会话级补缺口）。Hermes/deer-flow 对标 + owner 拍板「机械优于 prompt」（worker 委派 prompt 决定不可靠）。
  - [x] **Phase 1 通用外化**（PR #859）：泛化 CM-5 外化从「工具设 `full_content`」到「渲染 size 阈值」——`tools/overflow.py` 加 `EXTERNALIZE_MIN_CHARS=12k`/`make_preview`(head 2k+tail 1k)/`fallback_truncate`/`EXEMPT_TOOLS`(read_document/read_file/list_dir 取回类豁免，从「每 read_only」收窄)；`builder._externalize_tool_overflow` 返 `(replacement_body, footer)`，大 content 无 full_content 也外化留 head+tail preview+引用，写失败回落原地截断。8 wiring 测 + 99 回归绿。直杀 web_search 搜索膨胀。
  - [x] **Phase 2 机械剪枝闸（CM-12）**（本次）：探查修正前提——会话级压缩**已存在**（CM-2 WorkingWindow 丢整轮 + L.L2 ContextCompressor LLM 摘要），唯一真缺口=「丢整轮↔LLM 摘要」之间的便宜机械中间档。新 `orchestrator/context/tool_result_prune.py` `ToolResultPruner`（frozen，token 闸，prompt-view-only 镜像 WorkingWindow）：超阈值时把**旧**工具结果（超出最近 N 个 ToolMessage）collapse 成 1-liner——Phase-1 已外化的剪成纯 footer（**无损**，全文在 `.tool_results/`，且丢 untrusted preview 改善信任态），小结果剪成 `<tool-result-pruned>` 残柱（有损但旧、严格优于丢整轮）。**排 agent_node 最前**（最便宜最少损先跑，后续粗闸重估更小 prompt 少触发）。只改 ToolMessage content 不删消息 → 配对天然安全；按 `add_messages` 不删 + 只返新 tail → checkpoint 不重写（CM-C4）。计数保护用 ToolMessage 数非轮数（膨胀形态=单轮多调用）。新 `ToolResultPrunePolicy`（enabled/threshold_pct 0.7/recent_tool_results_kept 4，默认零行为变更）。10 unit（阈值/外化 footer 无损/残柱/配对/幂等/多模态跳过/at-or-under noop）+ 127 回归绿（含 109 compressor/window/factory + 102 protocol + 90 graph/builder）。1 设计修正 commit。
  - [x] **Phase 2.1 剪枝三跟进(dedup + persist floor + kill switch)**（本次）：**①dedup** prune 内精确重复 collapse(`prune_old_tool_results` 算每内容 last-occurrence,早于最后一次的相同内容→引用,**覆盖最近窗口保护**,纯逻辑无 I/O);**②persist floor + externalize-on-prune** `PERSIST_MIN_CHARS=4k`,4k-12k 结果**工具时**写盘(留全文在上下文,无 preview/footer)+ 路径记 `ToolMessage.artifact[_tool_result_path]`,prune 读 artifact 渲染无损 footer 引用(工具时写=write-once 保 pruner 纯,prune 每超阈轮重跑若现写会每轮重写);`_externalize_tool_overflow` 返三元 `(replacement, footer, persist_path)`;**③kill switch** `HELIX_TOOL_OUTPUT_BUDGET` env(默认开,falsey 全回退,照 run_retry),关→工厂不建 pruner + 跳 #859 通用外化 + item2 persist,**CM-5 full_content 老路不 gate**(单 env 翻转全平台回退零 manifest/重发)。`<4k` 仍 lossy 残柱(太小不值文件)。11 新测(dedup 早期 collapse/近窗 dedup/artifact-path 无损/persist floor 写盘+artifact 无 footer/kill switch 关通用外化但留 full_content)+ 24 overflow+prune 全绿 + 96+105 回归绿。
  - [x] **Phase 3 M1 受管开关后端(平台 DB + per-agent + gating)**（本次）：env-only kill switch → admin-ui 可管两级,均控整套(externalize+persist+prune),`effective = platform AND agent`,build 期解析。**平台** `platform_tool_budget_config` 单行表(migration 0102 revises 0101,25 字符 id)+ store(base/sql/memory)+ `PlatformToolBudgetConfigService`(DB-wins over `HELIX_TOOL_OUTPUT_BUDGET` env 默认,TTL 缓存,照 PlatformJudgeConfigService)+ `GET/PUT /v1/platform/tool-budget-config`(system_admin,envelope,审计 `PLATFORM_TOOL_BUDGET_UPDATED`)+ app/runtime 全接线(8 点 + runtime build_agent 前 `effective_enabled()`)。**per-agent** 新 `policies.tool_output_budget.enabled`(主开关,默认 True,prune.* 留细调)。**gating** `_externalize_tool_overflow` 改收 `budget_enabled` 参(不再直读 env,穿 build_react_graph→_dispatch_tool→_invoke_tool)+ factory `effective = platform_budget_on AND agent` 喂 pruner + build_react_graph;`build_agent` 加 `platform_tool_budget_enabled: bool|None`(None→env 默认)。**架构**:服务直 import overflow.tool_output_budget_enabled(control-plane 已多处 top-level import orchestrator.tools,安全);CM-5 full_content 老路永不 gate。21 新测(3 protocol policy + 3 store + 5 service DB-wins/env-default/invalidate + 7 API 403/env-default/put/audit + 2 factory effective=platform∧agent + env-fallback)+ 225 orchestrator + 156 control-plane 回归绿。M2 admin-ui(平台 Switch + agent 表单 Switch + SDK + i18n + stories + e2e)待。
  - [x] **Phase 3 M2 受管开关前端(admin-ui)**（本次）：**平台**新 `api/platform_tool_budget_config.ts`(get/put,envelope 经 getJson/putJson 解 `.data`,view `{enabled:bool|null, effective:bool}`)+ `PlatformToolBudgetSection`(照 `PlatformBillingSection` 自包含布尔 section:GET on mount→Switch(checked=effective)→PUT;`enabled===null` 显「环境默认」Tag;aria-label 防 axe)挂进 `SettingsPlatformConfig`。**per-agent** `form_model.ts` `readToolBudgetOn`/`setToolBudgetOn`(默认 on→删块清 YAML,off→写 `{enabled:false}`,照 dynamic_workers)+ policies type 加 `tool_output_budget` + `FormView.tsx` governance-advanced Collapse 加 Switch(紧随 trajectory)。**i18n** en(TranslationKeys interface + 值)+ zh 双写:`settings_platform.tool_budget_*`(11 键)+ `agent_form.tool_budget`/`_help`。**测试**:5 section vitest(help/effective 反映/env-default tag/PUT off/disabled 态)+ form_model 加 read/set(默认/off 写/on 删块保 approval)+ `e2e/platform-tool-budget.spec.ts`(system_admin 查+toggle PUT enabled=false + axe)。tsc -b(含 stories/e2e)过 + 535 vitest 全绿。**→ Phase 3 受管开关全交付(M1 后端 #863 + M2 前端)**;整个工具结果上下文预算线收官(#857/858 超时根因 + #859 Phase1 外化 + #860 Phase2 剪枝 + #861 Phase2.1 dedup/persist/kill-switch + #862 compose passthrough + Phase3 受管开关)。

---

## Stream HX — Harness 强化（2026-06-10 起）— 依据 [十维能力评估](./research/2026-06-10-helix-harness-capability-assessment.md)

> **立项（2026-06-10 用户拍板）**：把评估报告的全部非强项做强。按 Wave 严格顺序推进（Wave 1 零债收完再进 Wave 2）；每条设计先行 + 零债收尾（CM 同节奏）。
> **拍板边界**：浏览器执行面**不纳入**（产品决策，真实租户任务撞到交互式网页需求再立项）；agent 间通信 / 事务性工具**维持不做**（报告 §12 论证：单链委托是业界可靠性共识 / 行业未解题，irreversible 门控 + approval 是诚实替代——需求出现随时重议）。

### Wave 1 — 高 ROI 小改动（1 sprint，全部立项）

- [x] **HX-1 真 tokenizer + 长上下文阈值参数化**（评估 ③，设计 + 2 PR）：`len//4`（±15% 漂移）→ 真分词计数（estimator 注入点：`dynamic_context.py`/`compressor.py`/`working_window.py`）；压缩/滑窗阈值按 `ModelSpec.context_window` 参数化（1M 模型不再用 200K 同套参数）
  - [x] **HX-1 PR2 长上下文阈值参数化（收尾）**（本次）：`ModelSpec.context_window` 默认 200_000→`None`=build 期目录解析（`_resolved_context_window`：显式值永远优先→`catalog_entry().context_window`→200K 兜底；qwen3.7-max/deepseek-v4 自动拿 1M 比例阈值，manifest 不再手抄目录数字）+ **E.3 遗留默认裁剪退役**（`ContextCompressionPolicy.max_turns/max_tokens` 默认 20/8000→`None`=不注册 DynamicContextMiddleware——M0 naïve trim 旧默认每调静默裁到 ~8K/20 条架空五层级联；任一显式设置 opt-in 保留，未设轴用构造默认）+ sweep（admin-ui 只读目录展示不写 manifest 零前端改动 / control-plane / manifest fixtures 全无硬写）+ 7 新测（解析四态矩阵 / protocol 双默认 / opt-in 单轴 / assembly 注册语义全改写）。**→ HX-1 完成（设计 #540 + PR1 + PR2 三 PR，零债收尾）**
  - [x] **HX-1 PR1 estimator 统一 + 真分词**（PR #541）：`helix_agent.runtime.tokens` 新模块（`TokenEstimator` 协议 + `CharTokenEstimator` 现状语义 + `TiktokenEstimator` o200k_base 惰性加载/任何失败一次 warn 永久回落 chars//4/bounded LRU 4096 + `flatten_message` 上移共享 + `default_estimator` 进程单例）+ 三消费点注入（ContextCompressor/WorkingWindow `estimator` 字段默认 None 现状、DynamicContextMiddleware 走现成 `token_estimator` 缝、factory 统一注入）+ drift 可观测 `helix_hx_token_estimated_total` counter 进 TokenUsageMiddleware（cache hit 跳过，never-fail 契约；原设计 ratio histogram 因"直方图仅时长"公约改 counter 对，PromQL 求比）+ helix-runtime tiktoken 依赖 + 16 新测（加载失败回落/memo 命中与上界/CJK 真 vocab 冒烟 skip-on-offline/注入矩阵/factory 缝/drift 三态）。3969 回归绿
  - [x] **HX-1 设计先行**（PR #540）：[STREAM-HX-DESIGN](./streams/STREAM-HX-DESIGN.md) §2 详设——范围修正（阈值参数化机制已在：compressor/working_window 均 `context_window×threshold_pct`，真 gap=`ModelSpec.context_window` 默认 200K 不随目录解析）+ 新发现 gap ②（always-on DynamicContextMiddleware 默认 20 条/8000 token 每调裁剪 LLM 视图——E.3 M0 遗留默认值静默架空五层级联，所有模型实际每调只见 ~8K）+ tokenizer 选型五方案对比（tiktoken `o200k_base` 统一近似 + fail-open chars//4 + bounded LRU，不做 per-provider/不做 count API）+ `TokenEstimator` 协议三消费点统一注入 + context_window 目录解析（None=catalog_entry→200K 兜底）+ E.3 默认裁剪退役（双 None 不注册、显式配置 opt-in 保留）+ drift 可观测 `helix_hx_token_estimate_ratio` + 行为变更三条显式清单 + 6 条 Mini-ADR（HX-A1~A6）+ 2-PR 切分
- [x] **HX-2 用户反馈→学习闭环**（评估 ⑦ 断链，设计 + 2 PR）：feedback consumer worker（复用 DLQ worker 骨架）——👎 关联该 run 绑定的 skill 版本（SE-8 归因通道已在）进 SE 修订队列；关联记忆条目打 review 标记进 consolidator 队列
  - [x] **HX-2 PR2 memory review 通路 + FeedbackConsumerWorker（收尾）**（本次）：迁移 0071（`feedback.processed_at` + 部分索引 + **GRANT feedback→audit_reader**（FORCE-RLS 跨租户扫描的 ledger 先例）+ `memory_item.review_flagged_at` + 部分索引）+ `FeedbackStore.list_unprocessed_down_all_tenants`（SET LOCAL ROLE audit_reader）/`mark_processed`（租户域写）+ `MemoryStore.flag_for_review/list_review_flagged`（transient only，忽略 last_reviewed_at——👎 可重开已复审项）+ `mark_reviewed` 清 flag + consolidator **SUB-PASS 2a**（flagged 走 U-37 单条复审通路，不受 age/purge_enabled 限制——用户主动争议 ≠ 后台噪声扫描）+ `FeedbackConsumerWorker`（cross-tenant 枚举 bypass + per-row thread_meta 解析 user + tenant+user 双 GUC 域 flag + 戳后置 crash-safe + 失败行留待重试 + `helix_control_plane_feedback_consumed_total{action}` + `AuditAction.FEEDBACK_CONSUMED`）+ settings 默认开 + lifespan 接线 + 4 worker 单测 + 3 flag 生命周期 + 2 consolidator SUB-PASS 2a + 2 集成（SET ROLE 扫描/SQL flag 三方法）。3991 回归绿。**→ HX-2 完成（设计 #543 + PR1 #544 + PR2 三 PR，零债收尾）**
  - [x] **HX-2 PR1 rollback gate 接 👎（skill 侧）**（PR #544）：`SkillStore.skill_run_outcomes` 改形为 `skill_run_usage_window`（返回全 `SkillRunUsage` 行供 gate 按 thread join，双实现 + 既有测试改取 `.outcome`）+ `FeedbackStore.down_rated_threads` 批量 👎 查询（InMemory/Db 双实现）+ `RollbackGate.feedback_store` 注入（None=纯机器 outcome 零变更；👎 命中的 success 样本 demote 为 failed 进二项检验，cancelled 仍剔除；audit details 增 `disapproved_n`）+ gate 内 `_tenant_scope` 读 feedback——monitor sweep 是 owner bypass 而 feedback 表 FORCE-RLS，bypass 读静默零行的坑显式绕开 + app lifespan 接线 + 4 gate join 测试（demote 触发回滚/未接线零变更/cancelled+👎 不计入/单 👎 容忍）+ 1 RLS 集成测试（跨租户 👎 不串）。3982 回归绿
  - [x] **HX-2 设计先行**（PR #543）：[STREAM-HX-DESIGN](./streams/STREAM-HX-DESIGN.md) §3 详设——断点精确化（评估"无消费者"部分过期：J.12 已把 👎 物化为 negative_feedback candidate，真断点①=`EVOLVE_SIGNALS` 显式排除 negative_feedback 致 👎 candidate 零自动消费者 ②=记忆侧零通道；"SE 修订队列"具体化为既有 SE-7d rollback down-gate，不新造队列）+ skill 侧拉取式 join（rollback gate 聚合时 👎 thread 样本按 failed 计入二项检验——归因行不可变/零 TrajectoryOutcome 扩展/多 skill 连坐噪声由 n_min+显著性+效应量地板吸收）+ memory 侧 `review_flagged_at`（复用 U-37 单条复审通路，flagged 不要求 aged）+ FeedbackConsumerWorker 直扫 feedback 表行级 `processed_at`（不消费 candidate：late-👎 被 J.12 唯一性 pre-check 吞 + 依赖轨迹存在；curation 人工状态机零接触）+ 👍 零新动作 + 5 条 Mini-ADR（HX-B1~B5）+ 2-PR 切分
- [x] **HX-3 run 级瞬态故障自动重试**（评估 ⑧，设计 + 1 PR）：瞬态分类（类型注册表，初始 `AllProvidersExhaustedError`）∧ replay-safety 守卫（checkpoint 尾部 dangling 批次 capability-bounded 判定）→ in-worker 同 run_id 自动重试 1 次
  - [x] **HX-3 PR1 run 级瞬态重试实现（收尾）**（本次）：`orchestrator/run_retry.py` 新模块（`TRANSIENT_RUN_ERRORS` 类型注册表 + `retry_enabled`/`retry_backoff_s` env 防御解析 clamp [1,120] + `replay_is_safe` checkpoint 尾部守卫——dangling 批次逐个过 capability 判定，state fetch 失败/resolver 缺失/未知名全 fail-closed）+ `run_agent` 重试循环（同 run_id、`graph_input=None` J-24 续跑、`retry` SSE 事件照常 `_persist_event` 保历史真相、abort-aware backoff、`retry_attempts` 在 try 前初始化防 except 路径 NameError）+ `BuiltAgent.tool_replay_safe` 闭包（registry CM-B5 规则）三调用点接线（runs×2/trigger_firing）+ trajectory `metadata.retried` 四 outcome 贯穿 + `helix_orchestrator_run_retry_total{outcome=recovered/failed_again}` + 21 新测（分类器矩阵/env 防御/守卫 9 态/e2e 6 态：恢复·二轮失败·永久不重试·env off·守卫拒/放）。4019 回归绿。**→ HX-3 完成（设计 #546 + PR1 两 PR，零债收尾）**
  - [x] **HX-3 设计先行**（PR #546）：[STREAM-HX-DESIGN](./streams/STREAM-HX-DESIGN.md) §4 详设——取证修正两条（①沙盒 acquire 失败已被 E-12 在工具层自愈变 error ToolMessage 喂回 LLM，不是 run 级故障类；②"run 零 irreversible 调用"守卫精确化：checkpoint 按 super-step 提交、committed 历史不重放，唯一重放窗口 = 尾部 dangling `AIMessage.tool_calls` 批次，CM-B5 capability-bounded 规则上移——全批 read_only∨idempotent 才放行，守卫 fail-closed）+ 瞬态集类型判定零文本嗅探（`AllProvidersExhaustedError` 构造保证瞬态族；未知异常默认永久）+ in-worker 同 run_id 重试（SSE 流连续/trajectory 单记录 + `metadata.retried`/`retry` 事件持久化保历史真相/step_count 不重置）+ backoff env 配置防御解析 + `helix_orchestrator_run_retry_total{outcome}` + 4 条 Mini-ADR（HX-C1~C4）+ 2-PR 切分
- [x] **HX-4 可观测补强**（评估 ⑨，设计 + 1 PR）：approval pending gauge + checkpoint 计时 wrapper + run_id 结构化贯穿 + recording rules/SLO 同步（工具延迟直方图与 run 成功率判定过期，见设计 §5.1）
  - [x] **HX-4 PR1 可观测补强实现（收尾）**（本次）：`ApprovalStore.count_pending` 三处（abstract + InMemory + SQL `COUNT(*) WHERE pending`，ENABLE-only RLS owner 豁免跨租户读）+ `ApprovalGaugeWorker`（control-plane lifespan 常驻 60s 常量节奏不开配置面、启动即刷一次防重启盲区、读失败计数跳过 cycle、`helix_control_plane_approvals_pending` 单 gauge 无租户标签）+ `TimingCheckpointSaver`（helix-runtime/checkpointer/timing.py：aput/aput_writes/aget_tuple 计时进 `helix_checkpoint_op_seconds{op}`、observe never-fail、其余全方法显式委托、factory 两 backend 统一包）+ `current_run_id` contextvar（context.py 三函数面 + HelixJsonFormatter mandatory 字段 + run_agent 入口 set/finally reset，spawn 点零接触）+ sli.yml 五条新 rule（run 成功率排除用户中止/retry 恢复率/token drift 比/checkpoint P95/approvals pending）+ slo.md SLO #8-12（目标值 TBD 不编造）+ 13 新测（gauge worker 4/timing saver 4/run_id formatter 2/run_agent 绑定 1/factory wrapper 断言改写）。4030 回归绿。**→ HX-4 完成（设计 #548 + PR1 两 PR，零债收尾）；Wave 1 全清（HX-1/2/3/4）**
  - [x] **HX-4 设计先行**（PR #548）：[STREAM-HX-DESIGN](./streams/STREAM-HX-DESIGN.md) §5 详设——取证修正三条（①`helix_tool_latency_seconds{tool}` + audit duration_ms 已在 builder.py；②run 成功率从 `helix_session_duration_seconds{outcome}` histogram `_count` 直接推导，不加冗余 counter——缺口在查询层 recording rule 不在 emit 层；③trace_id 已完整贯穿 W3C→OTel→contextvar→formatter，只缺 run_id 轴；另 approval timeout sweep 已在 retention-cleanup-job J.8-step3b，缺的只是 pending gauge）+ 真缺口收敛三件（`ApprovalStore.count_pending` + control-plane 周期 gauge 任务 curator 先例同款单 gauge 防 label 爆炸 / `TimingCheckpointSaver` 代理 wrapper `helix_checkpoint_op_seconds{op}` never-fail / `current_run_id` contextvar + formatter 字段绑定 run_agent 任务边界）+ sli.yml/slo.md 资产同步（run 成功率/retry 恢复率/drift 比/approvals pending/checkpoint P95，SLO 目标值留 TBD 不编造验收线）+ 边界（dashboard JSON 独立 infra 项/cron 不上 pushgateway/不做 per-tenant 标签/OTel span 扩展另议）+ 5 条 Mini-ADR（HX-D1~D5）+ 2-PR 切分

### Wave 2 — 中体量（2 sprint，Wave 1 收完启动）

- [x] **HX-5 prompt 版本管理 + 离线 A/B**（评估 ①，设计 + 3 PR）：`agent_spec_revision` 不可变修订史（diff/回滚/audit）+ 离线 variant 对比 harness；online A/B 留 Wave 3
  - [x] **HX-5 PR3 prompt_ab 离线对比 harness（收尾）**（本次）：`tools/eval/prompt_ab.py`——两 manifest variant（YAML 文件，History tab/revisions API 导出快照即输入）× 同一 EvalSet 配对跑（复用 helix_eval 断言式判分）→ AbReport（per-case A/B 矩阵 + 通过率 Δ + McNemar 不对称计数 a_only/b_only，不内置胜负阈值——HX-E5）+ JSON artifact 落 eval-out + `--provider mock|env`（env 走 HELIX_EVAL_LLM_* run_longmem 同约定；CI 只验 mock 全链）+ 3 测（配对/discordant 计数/variant 解析防御/CLI artifact）+ README 章节。CLI 真跑冒烟过；4041 回归绿。**→ HX-5 完成（设计 #550 + PR1 #551 + PR2 #552 + PR3 四 PR，零债收尾）**
  - [x] **HX-5 PR2 revisions/rollback API + admin UI History tab**（PR #552）：三端点（`GET .../revisions` 摘要列表不带 spec payload / `GET .../revisions/{n}` 全快照 / `POST .../revisions/{n}/rollback`——取快照走既有 update_spec 写路径产生新 revision，audit details 带 `rolled_back_to`，回滚到当前内容 = recorded no-op `revision: null`）+ admin UI History tab（revision 表格当前标记/勾两版 Monaco DiffEditor YAML 对比 older→newer 懒加载快照/非当前版 Popconfirm 回滚/错误 alert；SDK 4 方法 + i18n 双语 + Storybook story + 4 vitest）+ 4 后端 API 测试（列表+快照/回滚追加+audit 断言/no-op/404 矩阵）。前端 283 测 + tsc 全绿；4038 unit 回归绿
  - [x] **HX-5 PR1 agent_spec_revision 存储层**（PR #551）：迁移 0072（建表 + 唯一约束 `(tenant,name,version,revision)` + ENABLE RLS tenant policy + **回填现存 manifest 为 revision 1**——否则首次编辑无"before"快照不可回滚）+ `AgentSpecRevisionRecord` protocol + `AgentSpecRevisionRow` ORM + `update_spec` 返回改形 `AgentSpecUpdateResult(record, revision, prev_sha256)`（事务内 SELECT FOR UPDATE → same-sha no-op 不记 → max+1 append revision + 主行覆盖；`updated_by` 落 revision 行，主行保创建者）+ create 同事务写 revision 1 + `list_revisions`/`get_revision` 双实现 + API 适配（update audit details 增 `prev_sha256`/`revision`，create 增 `revision: 1`）+ 8 新测（revision 生命周期/no-op/跨租户隔离/SQL 真 PG 全链含 actor 归属）。4034 回归绿
  - [x] **HX-5 设计先行**（PR #550）：[STREAM-HX-DESIGN](./streams/STREAM-HX-DESIGN.md) §6 详设——范围界定（prompt 是多源组合：manifest template + skill fragments + 动态注入；skill 已有 SE-7 版本系统、动态注入是运行期行为，唯一无版本可控源 = manifest → 版本实体定为 **manifest 整体修订史**而非单拆 prompt 字段表，兑现 `agent_spec/sql.py:141` "M1 row history table" 既定意图）+ `agent_spec_revision` 不可变 append-only（create→rev 1 / update 事务内 append N+1 / no-op 不增 / 行永不改删）+ 回滚=前进到旧内容产生新 revision（skill 先例 + 历史真相公理）+ diff 读取侧 Monaco 渲染不落库 + admin UI History tab（SE-8 接线点全过）+ `tools/eval/prompt_ab.py` 离线对比 harness（复用 helix_eval 断言式判分，报通过率 Δ + McNemar 不对称计数不内置胜负阈值；真 LLM 手动跑 CI 只验 fake provider 全链；artifact 落 eval-out 不建库表）+ 5 条 Mini-ADR（HX-E1~E5）+ 4-PR 切分
- [x] **HX-6 sandbox 热池 + 资源限额粒度**（评估 ⑤，设计 #554 + PR1 #555 + PR2）：per-variant READY 池 + replenisher + claim 时 docker update 限额配对 + 镜像预拉（M1-A 既定项提前）
  - [x] **HX-6 设计先行**（#554）：[STREAM-HX-DESIGN](./streams/STREAM-HX-DESIGN.md) §7 详设——取证修正两条半（①J.15 per-user 暖会话已在，"每 acquire 冷启动"半过期，冷的只剩首触/variant 切换/TTL 后；②cpu/mem/pids 已是 per-acquire 字段进 docker run，"限额粒度缺"半过期）+ **关键约束发现**：persistent workspace 的 named volume 只能 docker run 时挂载 → 持久用户首触在 docker 范式下不可池化（业界全形态 warm 靠 microVM snapshot；数据面方案 M2 另立项）——暖路径矩阵显式过堂：池覆盖临时沙盒（<500ms），持久用户暖靠 J.15 + 镜像预拉保底，不包装"首触仍冷" + READY 状态 + per-variant 固定 size replenisher（0=关 fail-open，EWMA 留到有命中率数据）+ claim 时 docker update 配对限额（fail-closed 销毁走冷启动——限额是安全面）+ per-exec 动态限额不做论证 + 池容器不计租户配额 + `helix_sandbox_pool_*` 指标 + 5 条 Mini-ADR（HX-F1~F5）+ 3-PR 切分
  - [x] **HX-6 PR1**（#555）：`SandboxState.READY` + `pool.py`（SandboxPool per-variant 库存 / PoolReplenisher reaper 同款骨架：补齐-收缩-失败下轮重试 fail-open / POOL_TENANT_ID 哨兵零 schema 变更）+ acquire claim 路径（J.15 会话 + 配额之后，仅无 user_id 询池；store CAS READY 守卫 → `docker update` 配对请求限额，失败销毁走冷启动 fail-closed）+ `CliDockerClient.update_limits`（--memory-swap 2x 配对）+ settings `pool_size_minimal=2/office=0`（clamp [0,16]）+ `helix_sandbox_pool_total{event}` counter；单测 16 + 真 docker integration 1
  - [x] **HX-6 PR2**（收尾）：镜像预拉（lifespan 后台 task，inspect→pull，fail-open，shutdown cancel 不拖 teardown——对持久用户首触也有效的唯一加速件）+ `helix_sandbox_pool_ready` gauge（replenisher 每 tick set）+ SLO #13 池命中率（`helix:sli:sandbox_pool_hit:ratio1h` rule）+ SLO #4 M1 行验收口径补注
- [x] **HX-7 trace 生产接线 + approval 队列页**（评估 ⑨，设计 #557 + PR1 #558 + PR2 #559 + PR3）：Langfuse Recording stub → SDK adapter（ADR-0005）；admin UI 独立待批列表页（跨 run 聚合 + 批量操作）
  - [x] **HX-7 设计先行**（本次）：[STREAM-HX-DESIGN](./streams/STREAM-HX-DESIGN.md) §8 详设——取证修正两条（①OTel 链已全通，trace 真缺口只剩 Langfuse 档；②approval 24h 超时 TIMEOUT 已在 retention-cleanup-job，初判"缺 worker"有误）+ 架构事实：resume continuation worker 与 SSE 消费端已解耦 → 批量 decide 可做非流式端点。方案：①`LangfuseSdkClient` 实现既有 `LangfuseClient` Protocol（middleware 零改动，E.5 自注 follow-up 兑现；`langfuse>=3` 进 helix-runtime，v3 OTel-based 与现有 TracerProvider 同源——ADR-0005 trace_id 共享零额外代码）+ settings 三件套 + runtime factory（配置缺省=Recording fail-open，CI 无凭证纪律）②`list_by_status` store/API（`GET /v1/approvals`，requested_at 最老优先，不 join agent_name）③批量 `POST /v1/approvals:decide`（resume 内核提取共享，≤20/批防 continuation 风暴，逐项独立失败）④`/approvals` 队列页（Badge 迁移 + 单/批统一走 :decide，modify 留 RunDetail；SE-8 接线点全量）。边界：Langfuse 实例部署归 infra 另立；通知通道 M2。Mini-ADR HX-G1~G5 + 4-PR 切分
  - [x] **HX-7 PR1**（#558）：LangfuseSdkClient 实现既有 LangfuseClient Protocol（middleware 零改动；langfuse>=3 进 helix-runtime，v3 OTel-based 与现有 TracerProvider 同 trace_id）+ make_langfuse_client factory（三 settings 齐→SDK，缺任一/import 失败→Recording fail-open）+ lifespan teardown shutdown；adapter 12 测（SDK 方法名按 langfuse==3.15 实测核准）
  - [x] **HX-7 PR2**（#559）：ApprovalStore list_for_tenant/list_all_tenants（队列序+真 total）+ GET /v1/approvals（Stream N scope 框架）+ resume 内核提取 apply_approval_decision + POST /v1/approvals:decide（≤20/批，逐项独立失败，非流式——worker/SSE 本就解耦）；API 10 测 + store 3 测 + resume 回归零改动
  - [x] **HX-7 PR3**（收尾）：/approvals 队列页（最老优先 + 行内/批量 approve-reject 统一走 :decide + modify 留 RunDetail）+ ApprovalPendingBadge 迁移（数据源改 approvals API + 挂新 Approvals 导航项）+ SE-8 全接线（router/Sidebar/CommandPalette/SDK/i18n 双语/Storybook/vitest 4 测/Playwright 冒烟 2 测含 axe）
- [x] **HX-8 多租户薄点**（评估 ⑩，设计 #562 + PR1 #563 + PR2 #564 + PR3，2026-06-12 拍板：per-tenant override 平台管理非 BYOK + 部署级 block 开关）：平台 provider/tool 凭证 per-tenant override（姊妹表 + service 合并视图 + overlay resolver + 管理 API/UI，与 Stream Y 计量路线衔接）+ 跨租户查询部署级 block 开关（system_admin 不得越出 home tenant，`"*"` 与显式 switch 同拦）
  - [x] **HX-8 PR3 跨租户 block 开关（收尾）**（本次）：settings `cross_tenant_query_enabled: bool = True`（default 保 Stream N 现状）+ `ensure_tenant_scope` 可选 kw `cross_tenant_enabled`（off 时 `"*"` 聚合与 system_admin 显式 switch 双分支 403 `CROSS_TENANT_DISABLED` + `SYSTEM_CROSS_TENANT_BLOCKED` audit——details 带 mode=aggregate|switch/target_tenant，home 访问与普通用户 allowed_tenants 路径零接触）+ `cross_tenant_query_enabled(request)` helper（app.state.settings 防御读）+ 14 文件 16 调用点 sweep（api_keys/role_bindings/service_accounts 补 request 参数）+ 新 AuditAction（protocol 单点，grep 无双份镜像）+ 4 单元矩阵测 + 1 端点级集成测（off 时 star/switch 403、home 200）。**→ HX-8 完成（设计 + 3 PR，零债收尾）**
  - [x] **HX-8 设计先行**（#562）：[STREAM-HX-DESIGN](./streams/STREAM-HX-DESIGN.md) §9 详设——取证五条（平台凭证管理面 P/Q 已完整，HX-8a 只加租户维度；resolver tenant_id 接缝已在签名；Y-1 BYOK 边界不动摇；rate_card 预留列路线对已存在表不成立；ensure_tenant_scope 单决策点覆盖 14 端点零开关）+ 姊妹表 `tenant_provider_secret`/`tenant_tool_secret`（行存在=override，ENABLE RLS 纵深）+ service 合并序 env→platform→tenant（disabled=suppress 镜像 P-12）+ resolver 可选 tenant getter 零签名破坏 + `/v1/platform/credentials/tenants/*` system_admin CRUD + SettingsPlatformConfig 行级抽屉 + `cross_tenant_query_enabled` 开关（403 `CROSS_TENANT_DISABLED` + `SYSTEM_CROSS_TENANT_BLOCKED` audit）+ 5 条 Mini-ADR（HX-H1~H5）+ 3-PR 切分
  - [x] **HX-8 PR1**（#563）：迁移 0073 `tenant_provider_secret`/`tenant_tool_secret`（ENABLE-only RLS，owner 豁免承载 service bypass 读——agent_approval 同款）+ `PlatformSecretStore` 6 个 tenant 方法三实现（base/memory/sql，upsert 保 created_at）+ `PlatformSecretsService.effective_*_credentials_for(tenant_id)`（同 TTL 缓存/invalidate；enabled=override、disabled=suppress 镜像 P-12）+ `TenantOverlayCredentialsResolver` 子类（**实施修订**：helix-common/credentials 是 harness 禁写区，"resolver 加 kwargs" 改为 control-plane 子类 override，错误契约与基类逐字段一致——HX-H3 语义不变）+ app.py 构造点换子类 + `helix_platform_credentials_tenant_overrides` gauge + 16 单测 + 2 真 PG integration（round-trip + 非 owner 租户上下文只见己行）
  - [x] **HX-8 PR2**（#564）：管理 API 5 端点（`GET /v1/platform/credentials/tenants/{tid}` per-key effective 来源标注 tenant/suppressed/db/env/unset + PUT/DELETE providers|tools——PUT 复用 PlatformSecretWrite 写穿语义、ref 落 `secret://…/tenant/{tid}/…` 命名空间；DELETE 无 in-use 守卫＝回 fallback 本身安全）+ 4 新 AuditAction（details 带 tenant_id 无真值）+ 总目录 `tenant_override_count` + 租户 404 校验（TenantConfigNotConfiguredError→TENANT_NOT_FOUND）+ SettingsPlatformConfig 租户 override 抽屉（页头入口 + listTenants 选择器 + per-key effective tag + 增改删复用既有 edit Modal 分流 tenant API）+ SDK 5 函数 + i18n 双语 + Storybook mock 补字段 + 5 后端 API 测 + 4 vitest（291 前端全过）
  - [ ] **HX-8 PR3**（收尾）：settings 开关 + ensure_tenant_scope block + 14 调用点 sweep + 文档同步
- [x] **HX-12 工具披露 2.0·应用层**（评估 ②，源于 [Hermes Tool Search 源码分析](./research/2026-06-11-hermes-tool-search-source-analysis.md) + 业界调研，2026-06-11 拍板，设计 + 4 PR）：① `registry.search` 自然语言 ranked 检索（BM25 + jieba + 零 IDF substring 兜底，留向量接缝）+ find_tools 防呆包（source 三池标注/描述截 400/教学式错误/docstring 过期修正）② 阈值逃生门——assembly 注册期用 HX-1 真 tokenizer 估算 MCP schema 总量，< min(上下文 10%, 20k) 直接 active 注册 ③ call-through——dispatch 拦截直调 deferred 名自动 promote + 本 turn 执行，未命中附 ranked 建议 ④ promotion 退场（compressor 触发时降级 N turn 未调用项，reducer dict 删除语义）+ 指标（`helix_tool_promotion_total{event=promote|call_through|demote|miss}`）。公理：不存在 drop core tool 代码路径；fail-open（故障=多花 token 绝非少能力）
  - [x] **HX-12 设计先行**（#566）：[STREAM-HX-DESIGN](./streams/STREAM-HX-DESIGN.md) §10 详设——取证八条（search 三语法 substring 无 ranked / MCP 三池 always-defer 硬编码无逃生门 / find_tools 描述未截断零命中无引导 / deferred 直调裸报 unknown / promoted_tools reducer append-only 永不删 / HX-1 tokenizer 资产可直用——"chars/4 先行"已过期 / jieba 已是仓内依赖 J.5 先例 / promotion 域零指标）+ 5 条 Mini-ADR（HX-I1~I5：rank-bm25+jieba 不自研 / 函数级向量接缝不建 Protocol / 真 tokenizer 阈值常量 / call-through 本 turn 执行零新攻击面 / reducer dict 删除语义 list 兼容）+ 4-PR 切分
  - [x] **HX-12 PR1**（#567）：`tools/ranking.py` 检索内核（`rank_tools`——BM25Plus + jieba 分词 + 名字拆词/camelCase/参数名进语料 + top-K=8 + 显式 token-overlap 过滤防 Plus delta 平滑误报；**BM25Okapi 小语料 IDF 归零坑实测后换 Plus**）+ registry.search ranked 模式（三语法保留，自然语言 BM25 先行、零命中回 regex/substring 兜底）+ register 加 `source` 标注（MCP 注册传 `mcp:<server>`）+ BM25 语料 registry 内 lazy 缓存注册失效 + find_tools 防呆包（描述截 400/教学式零命中文案/结果带 [source]/docstring 改 ranked 语义）+ `helix_tool_promotion_total{event}`（promote/miss 两事件先行）+ 7 ranking 单测 + 4 find_tools 新测 + 2 既有测试更新为 ranked 语义（(read 双命中 / read_file 排首）+ 真降级路径补测（token-mismatch substring）。orchestrator 1119 / 全仓 3876 回归绿
  - [x] **HX-12 PR2**（#568）：阈值逃生门——`build_tool_registry` 加 `context_window` 参数 + `_maybe_activate_small_deferred_pool`（HX-1 `default_estimator()` 估每工具 name+description+parameters JSON，总量 < min(10% ctx, 20k) → 全池 re-register active、find_tools 自然不挂；超阈值早停；estimator 异常 fail-open 维持 defer）+ `registry.deferred_names()` + agent_factory 传 `_resolved_context_window`；**实施注记：`context_window=None`（legacy 调用点）不启用——TE-6b 字节不变，仅 agent_factory 主路径显式启用** + 5 测（under/over/None/estimator 故障/source 保留）。orchestrator 1124 / 全仓 3881 回归绿
  - [x] **HX-12 PR3**（#569）：call-through + ranked 错误建议——**取证修正**（设计 §10.1-4 已改）：`get_required` 查含 deferred 的 `_tools`，deferred 名直调**本就能执行**（TE-6 "stays dispatchable"），真缺口 = 执行后不 promote（schema 不进后续 bind 持续盲调）+ 真未知名无建议。dispatch 成功路径检测 deferred 名 → outcome state_updates 合并 `promoted_tools`（reducer dedupe 幂等）+ `call_through` 事件；`ToolNotFoundError` → 错误附 ranked top-3 建议（registry.search 复用 PR1 内核，best-effort fail-open）+ 2 e2e 测（直调执行+promote+次 turn bind / 拼错名带 Did-you-mean 建议且零 promote）。orchestrator 1126 / 全仓 3883 回归绿
  - [x] **HX-12 PR4**（收尾，本次）：promotion 退场——`_merge_promoted` reducer 升级 dict 删除语义（`{"add","remove"}`，list 形态保旧 add-only 语义全部既有写点零改动）+ 新 channel `promoted_tool_last_used: dict[str,int]`（per-key max reducer；tools_node 每 batch 给 dispatch 的 promoted 工具刷 stamp、新 promote 名打基线——promote 即亮相，人人可老化）+ agent_node compress 分支 demote（`step_count - last_used > 12` 常量；压缩压力信号复用，下 turn bind 生效；demote 不出 deferred 池 find_tools/call-through 随时召回——能力永不丢）+ `helix_tool_promotion_total{event=demote}`（四事件收齐 promote/call_through/demote/miss）+ 6 测（reducer add/remove/list 兼容矩阵 + last_used max 单测 + 真 graph e2e：stale 压缩即降级且可再召回/fresh 不降/dispatch 刷 stamp）。orchestrator 1132 / 全仓 3889 回归绿。**→ HX-12 完成（设计 #566 + 4 PR，零债收尾）**
- [x] **HX-13 工具披露 2.0·厂商原生档**（设计 #571 + PR1 #572 + PR2 #573 + PR3，前置 HX-12）：① `ModelEntry.tool_disclosure: Literal["native_search","allowed_tools"] | None` 能力位 + catalog 标注 + `ToolSpec.defer_loading` 单标记 ② anthropic：defer_loading + beta header `tool-search-tool-2025-10-19`，此档停用自家 find_tools，beta 拒绝 fail-open 降 None 档 ③ openai/azure：全量 schema 冻结 + `tool_choice.allowed_tools`=未标记者（promotion 驱动子集而非 bind，find_tools 保留为唯一 promote 入口）；compat 家族逐家核实不支持归 None 档（CM-L5）。None 档=HX-12 全套（兜底档保语义底线）
  - [x] **HX-13 设计先行**（#571）：[STREAM-HX-DESIGN](./streams/STREAM-HX-DESIGN.md) §11 详设——取证六条（CM-10 thinking 能力位完全同模式可循 / Provider 值域无 openrouter 剔除 / anthropic 自写 httpx 客户端加 beta header 一行事 / openai adapter 现无 tool_choice / **LLMCaller 无 deferred 可见性但 agent_node bind 组装点持有 registry 闭包——disclosure 分支落位 agent_node，caller 接口不动** / HX-12 call-through 已兜原生档检索结果回流 promote 零额外代码）+ 5 条 Mini-ADR（HX-J1~J5：分支落位 agent_node / 单 defer_loading 标记服务两档 / find_tools 档位差异——native 排除 allowed 保留 / fail-open 降级重发+进程内记忆 / 能力位声明式不探测）+ 3-PR 切分
  - [x] **HX-13 PR1**（#572）：`ModelEntry.tool_disclosure` 字段（默认 None，50+ catalog 条目 + doubles 零破坏——sweep 仅 test_billing 两处不受影响）+ catalog 标注（anthropic opus-4-8/sonnet-4-6 → native_search、haiku 保守 None；openai gpt-5.5/5.5-pro/5.4-mini → allowed_tools、embedding/deprecated 不标；compat 五家全 None 注释入册 CM-L5；azure/self-hosted off-catalog 天然 None 兜底）+ `ToolSpec.defer_loading: bool = False`（HX-J2 单标记，agent_node per-bind replace 副本专用）+ 3 测（默认值/catalog 标注矩阵/compat 全 None 断言）。**实施微调：档位解析挪 PR2 与消费者同进**（避免预备性未引用参数）。全仓 3892 回归绿
  - [x] **HX-13 PR2**（#573）：anthropic native_search 接线——档位解析（`_resolved_tool_disclosure` 纯 catalog 查询，off-catalog→None）+ `build_react_graph(tool_disclosure=)` + agent_node **双档 bind 分支同进**（native：排除 find_tools + still-deferred 带 `defer_loading` replace 副本 + promoted 去标记；allowed：全量 schema + find_tools 保留 + still-deferred 带标记——bind 逻辑同构故并入本 PR，PR3 只剩 openai adapter）+ `_to_anthropic_tool` 输出 defer_loading + `AnthropicClient` Protocol/双实现 `betas` kwarg + 条件 `anthropic-beta: tool-search-tool-2025-10-19` header + **HX-J4 fail-open**（beta 4xx → 去标记重发一次 + 实例级粘性降级 + `helix_llm_tool_disclosure_fallback_total{provider}`）+ 8 测（adapter 4：标记+header/无标记无 header/拒绝降级且粘性/普通 4xx 不重发；bind 4：native 排除 find_tools 标记矩阵/promoted 去标记/allowed 全量保 find_tools/None 档字节不变）。orchestrator 1141 / 全仓 3900 回归绿
  - [x] **HX-13 PR3**（收尾，本次）：openai/azure allowed_tools 接线——`OpenAIClient` Protocol + HTTP/Recording 三处 `tool_choice` kwarg + provider 检测 defer 标记构造 `{type: allowed_tools, mode: auto, tools: [未标记者]}`（全量 schema 冻结上线，allowed 子集 = active + promoted + find_tools）+ HX-J4 fail-open（tool_choice 4xx → 去约束重发一次 + 实例级粘性降级 + 共享 `disclosure_fallback_total{provider=openai}`——counter 收敛进 `providers/_metrics.py` 共享模块）+ compat 家族核实结论已随 PR1 注释入册（五家 None 待逐家核实，azure off-catalog 天然兜底）+ 4 测（子集构造含 find_tools/无标记零 tool_choice/拒绝降级且粘性/普通 4xx 不重发）。orchestrator 1145 / 全仓 3904 回归绿。**→ HX-13 完成（设计 + 3 PR，零债收尾）；Stream HX 全部子项收官（HX-1~8 + HX-12/13，Wave 1+2 完成；Wave 3 HX-9/10/11 维持规划态）**

### Wave 3 — 架构级（设计先行重点评审）

- [x] **HX-9 租户级出站 webhook hook**（评估 ④ 架构级缺口，Wave 3，已交付 #595/#596/#597/#598/#待填）：平台在 agent 生命周期关键点签名 POST 租户注册 URL（webhook 回调式，非任意代码）。**方向已拍板（2026-06-13，对比讨论后，research `2026-06-13-tenant-hook-extension-patterns.md`）**：① 注册面=平台 API CRUD（照 triggers J.10，运行时可改不弹 agent 版本，manifest `hooks` 字段转 deprecated）② 起步事件集=三类（run 终态现成 + 审批/artifact 补 2 个 run_event emit 帧走单事件主轴）③ 投递=自建薄版（复用 MemoryDLQWorker/FeedbackConsumerWorker 形态 + PG 队列表，不引 Svix）④ 仅出站异步（同步改写回调违 fail-open 归 M1-F）。横切公理：fail-open（投递故障不阻 run）/ per-tenant 队列 + per-endpoint 断路器（爆炸半径由跨租户决定）/ 不带平台凭证（HMAC-SHA256 签名）/ SSRF 双段校验。设计 STREAM-HX §13。
  - [ ] **HX-9 PR0 设计先行**（本次）：STREAM-HX §13（4 决策 + 6 Mini-ADR HX-J0~J5 + 事件 schema + 投递状态机 + PR 切分）+ research 对比稿 + 本 backlog 细化。纯文档。
  - [ ] **HX-9 PR1**：迁移 `webhook_endpoint`+`webhook_delivery` 两表 + RLS + 部分索引 + DTO/ORM/Store 三层（照 trigger）+ ResourceType 双镜像（AuditAction WEBHOOK_* 推迟到 PR2 emit 时加）。**纯数据模型，零 orchestrator 改动**——取数机制实现期订正为方案 (b)（worker 读 3 源表），原 (a) 补 run_event emit 帧否决（run_event 无干净 run 终态帧 + 无 tenant_id 列；幂等去重靠 webhook_delivery UNIQUE(endpoint_id,event_id)）
  - [ ] **HX-9 PR2**：`api/webhook_endpoints.py` 5 端点 CRUD + tenant scope authz + Stream N 跨租户 + audit + HMAC secret show-once + 配额 + SSRF（validate_remote_url + host-pivot 字符黑名单）+ admin-ui（SDK/WebhooksList/AgentDetail tab/i18n/router-Sidebar-CommandPalette 接线）
  - [x] **HX-9 PR3a**（#598）：投递引擎 `WebhookDeliveryWorker` 消费 webhook_delivery（list_ready → SSRF 复检 + HMAC-SHA256 签名 POST → 2xx delivered/4xx dead_letter/5xx 退避重试 60s~6h/max5/DLQ + per-endpoint 断路器 + per-tenant 并发上限）+ lifespan 接线 + 计量 + 可观测（成功率/DLQ 深度/断路器状态）。**含 secret_ref 修正**：PR1 套 trigger 入站存 sha256 hash，出站 HMAC 签名需明文→改 secret_store 写穿 + 迁移 0075 rename secret_hash→secret_ref。
  - [x] **HX-9 PR3b（收尾）**（#待填）：入队扫描——`WebhookDeliveryWorker.enqueue_once` 扫 3 源表（**复用现成 `list_all_tenants`，零新增 store 方法**——idempotent 去重 `UNIQUE(endpoint_id,event_id)` 使粗粒度重扫安全）→ agent 范围经 thread_meta 反解（run/approval 有 thread；**artifact 父行无 thread → 仅 all-agents endpoint，文档限制**）→ 幂等入队 → 端到端闭环。enqueue+deliver e2e 测 + 10 worker 测。HX-9 全交付。
- [x] **HX-10 sandbox 安全纵深**（评估 ⑤，设计 + 3 PR，**已交付** 2026-06-12；F1/F2 为 gVisor 生产上线前置 follow-up）：**多租户共享宿主下 agent 执行 LLM 生成代码的隔离纵深**。**实证驱动**（四角业界调研落 [research](./research/2026-06-12-sandbox-isolation-defense-in-depth.md)），方案 1 拍板（2026-06-12，gVisor 当期纳入不缓做）：⓪ **misconfig 断言**（最高优先级、零成本——实证 SANDBOXESCAPEBENCH 前沿 LLM 对 docker-socket/特权/可写宿主挂载 100% 逃逸；钉死防回归）；① seccomp = 仓库自管 pinned profile（Docker default 基线 + 收紧 io_uring/userfaultfd/keyctl/bpf；**实证修正：clone3 保留允许防崩新 glibc / perf_event_open 移出强制 / io_uring 禁用标兼容代价**）；② gVisor **可配置 + CI 真验证**（生产多租户推荐 runsc/dev runc；workflow 装 runsc systrap + env 参数化 + runtime 真起断言 + 兼容坑断言按 Tencent 清单 + CVE-2019-5736 PoC）；③ Trivy **分镜像门禁 + `--ignore-unfixed`**（minimal/debian 卡 CRITICAL+HIGH；office LibreOffice 卡 CRITICAL+周扫——卡 HIGH 恒红失效）。威胁模型修正：爆炸半径由跨租户决定不由信任决定。详设 §12（8 条 Mini-ADR HX-K0~K7）
  - [x] **HX-10 设计先行**（#576）：§12 详设 + research 文档（四角实证）——取证 + 实证驱动三档修正 + 威胁模型修正（信任只降概率不改影响）+ 横切公理脚注（安全配置事故 fail-closed）+ 3-PR 切分；Sysbox 记为 DooD 友好对照候选（HX-K7，对外开放里程碑评估）
  - [x] **HX-10 PR1**（#577）：misconfig 断言（argv 无 socket/特权/cap-add/宿主 bind mount + 有 cap-drop/read-only/no-new-priv）+ seccomp pinned profile 入仓（`infra/sandbox-image/seccomp-profile.json`，vendored moby v27 default；**取证修正：v27 default + cap-drop ALL 已拒目标高危族，真实增量=pinned**；clone3 保留 ENOSYS 回退）+ `runtime_provider` `--security-opt seccomp` + settings + supervisor fail-closed 校验
  - [x] **HX-10 PR2**（#578）：gVisor CI workflow（`sandbox-gvisor.yml`，路径过滤+dispatch，runsc systrap 装载+`runsc install` 注册 dockerd）+ 集成测试 `HELIX_TEST_SANDBOX_RUNTIME` 参数化（默认 runc）+ runtime 真起断言 + io_uring ENOSYS 不变量 + compose runtime 注释。**实证修正：CVE-2019-5736 真 PoC 降级 staging（CI 跑真逃逸不安全/脆弱）；首跑暴露 2 个 gVisor 架构 gap（gate_49 DNS / gate_56 fork-bomb）runsc 下 xfail，转 HX-10-F1/F2 follow-up（方案 Y：不卡 PR + 可见 + 记账）**
  - [x] **HX-10 PR3**（#579 + 修复 #580，收尾 HX-10）：minimal 镜像补 CI 构建（`sandbox-image.yml`，build+smoke+scan，对齐 office buildx/gha-cache/SHA-pin）+ 两镜像 Trivy 分镜像门禁（minimal `CRITICAL,HIGH` / office `CRITICAL`——实证 HX-K5：LibreOffice 大基底卡 HIGH 恒红，HIGH 降周扫）+ 全部 `--ignore-unfixed`（门禁只卡有上游修复的可行动项）+ `.trivyignore` 强制 `expires:YYYY-MM-DD` 纪律 header（禁永久 mute）+ `sandbox-image-cve-weekly.yml` 周扫两镜像 `CRITICAL,HIGH`（office HIGH 在此现，**不应用 .trivyignore** 使 muted CVE 仍现于审计面）+ SARIF 上传 Security tab + minimal smoke test（stdlib-only payload）
  - [x] **HX-10-F1 gVisor 生产 proxy 寻址**（2026-06-13，方案=`/etc/hosts` 固定 IP）：沙箱→credential-proxy 从 docker embedded DNS（gVisor netstack 不支持，google/gvisor#7469）改 `/etc/hosts` + 固定 IP（`--add-host`，gofer 文件 gVisor 天然工作）。**业界实证背书**：gVisor 官方 FAQ「用 IP 代替容器名」是钦定裸 Docker 解（`--network=host` 破隔离/`--link` 破 egress 管控均否决）；规模化玩家上 K8s 走 CoreDNS 问题消失（坑是裸 compose 单机特有产物，M1+ 自然退役）；egress proxy 静态 IP 是行业常态（Cloudflare/Blaxel 产品化）。实证落 research §7。**接缝（#592）**：`runtime_provider.extra_hosts`（`--add-host` argv）+ `HELIX_SANDBOX_EXTRA_HOSTS`（fail-closed）+ supervisor app 接线。**CI 转正（本 PR）**：集成测 egress 网络固定 subnet（172.30.0.0/24）+ stub proxy 钉静态 IP（172.30.0.10）+ harness 传 extra_hosts + 探针经主机名 `credential-proxy.internal` 打 proxy，**runc+runsc 双 runtime 同 /etc/hosts 路径，gate_49 由 xfail 转正**；compose 落静态 IP 默认（egress ipam subnet + proxy ipv4_address + supervisor 注释给具体值）。**剩纯运维**：生产选不撞私网段 + runsc 环境配 HELIX_SANDBOX_EXTRA_HOSTS。设计 STREAM-HX §12.2.5
  - [x] **HX-10-F2 gVisor fork-bomb 语义**（2026-06-13，方案 A 拍板）：`--pids-limit` 在 gVisor 下限 sentry 宿主线程非 guest（google/gvisor#2490），fork bomb panic sentry 杀沙箱。**已决方案 A「接受沙箱阵亡+重建」**（否决 B guest cgroupfs pids.max）——业界实证背书：gVisor Security Model 明文资源耗尽委托宿主 cgroup / #2490·#2489·#3942 三 issue 长期 open 不修 / Modal·GKE Agent Sandbox 全走 ephemeral+重建（实证落 research §6）。横切公理：F2 非安全洞是错误优雅度，安全目标（爆炸半径锁沙箱）已达成。落地零生产代码（宿主 cgroup 限总量 + ephemeral + warm pool[HX-6] + reaper 重建[已在]，workspace volume[J.15] 持久故数据零损失）；gate_56 由 xfail 转正为显式 A 语义断言（fork bomb 杀自身沙箱 → supervisor 存活 + 全新沙箱重建执行成功）。设计 STREAM-HX §12.2.4
- [ ] **HX-11 online A/B + 自动 prompt 优化**（评估 ⑦ 远期，依赖 HX-5）：流量分桶 + 评测数字回流

### Wave — 2026-06 沙箱出网 + 审计评估 + 镜像合并

> 起点：用户问「沙箱 network=none 是不是把 F 类技能能力锁死了」+「审计规则是否过度拦截」。
> 纠正「network=none 硬天花板」误判（实为 internal 网 + 死字段 NetworkSpec），落「能力优先、
> 审计追溯优先于过度拦截」原则（设计 `docs/design/sandbox-egress-per-agent.md` /
> `sandbox-audit-evaluation.md` / `sandbox-image-consolidation.md`）。

- [x] **per-agent 沙箱出网**（#745-750）：沙箱内代码经审计代理访问公网解锁 F 类技能。**1a** 透明 CONNECT 代理（裸 asyncio 同进程 listener）+ per-sandbox HMAC token + `resolve_and_pin_host` 防 rebind + `sandbox_egress_audit`（0087，不记 payload）；**1b** `EgressContext` + `_EgressBindingClient` 包 supervisor_client 零 per-tool 穿线 + token 注 `HTTPS_PROXY`；**Phase 2** per-agent host allowlist 消费（#747，opt-in 硬化非默认闸）；**Phase 3** admin-ui 出网审计页 + agent egress 徽章（#748/#749）；真栈 e2e（#750）。默认 `egress:proxy` 开 + 全连接审计。
- [x] **egress urllib CONNECT 鉴权 shim**（#752）：stdlib urllib 在 CONNECT 不带 `Proxy-Authorization`（requests/httpx/urllib3 带）→ `sitecustomize.py` monkeypatch `set_tunnel` 从 `HELIX_EGRESS_PROXY_AUTH` 补头，烤进镜像全局 site-packages（`python -I` 仍跑 site）。真栈 e2e 转绿（#750）。
- [x] **egress 明文 HTTP**（#762，audit-eval 衍生 backlog②）：代理加 absolute-form HTTP 分支（`GET http://host/path`），与 CONNECT 同 `_secure_connect` 闸（auth/allowlist/SSRF-pin/审计）；改写 origin-form + 剥代理头 + Host 由 URL 重建 + 强制 `Connection: close`（单请求/连接防管道化 host 混淆）；`HTTP_PROXY` 早已注入零 supervisor 改。设计 §3.6。
- [x] **审计规则全面评估**（设计 `sandbox-audit-evaluation.md`，Phase 1-6）：必要安全（gVisor 隔离/egress SSRF-pin/URL host-pivot/RBAC-ABAC/不可逆审批/PI-1 spotlight/PI-2 输出筛/技能导入结构拒）保留；过度拦截放成 allow+审计。**P1** #754 删 `sandbox_audit` 拦截剧场(bash 早绕过且 gVisor 才是边界)+ 录沙箱 exec 代码进审计；**P2** egress `_ip_is_blocked` 收窄显式 RFC1918(放行 198.18 fake-ip)；**P3** user-authored 写路径 strict→warn+审计(保留运行时注入向量硬拦)；**P4** #758 output-guard 持久审计行 + egress 407 `blocked_auth`(migration 0088 tenant_id nullable)；**P5** #759 `rate_limit_override` 死字段接成真生效(per-tenant 限流+422 校验+admin-ui 展示)；**P6** #760 skill_seed 丢文件(drift/injection/坏 base64)落审计行(`SKILL_DRIFT_DETECTED`/`SKILL_PROMPT_INJECTION_BLOCKED`，只记名+path 不记内容)。
- [x] **沙箱镜像多架构发布**（#761，backlog①）：CI 单镜像加 `setup-qemu` + push 步 `platforms: linux/amd64,linux/arm64` → manifest list；本地 load+smoke+Trivy 仍原生 amd64 门控；Apple-silicon 开发机不再手动 `docker tag` 兜架构错配。main 构建+推送 live 实证。
- [x] **双镜像合并为单一 full 镜像 + node**（#763，backlog③ node 并入）：`minimal`(纯 stdlib 实用性太薄)+`office` 合并为单一 `helix-sandbox`(python+办公/数据/媒体库+soffice/poppler/ffmpeg+**node**+CJK)；删 `image_variant` 机制(字段保留 deprecated+忽略,SandboxSpec extra=forbid 防旧 agent 422)；node 经 `bash` 跑零新 tool，browser→MCP；单 pool/单 CI(CRITICAL 门,office 大基底)。胖镜像成本真相=磁盘单份共享/RAM 按需，唯一真残留 CVE 面。`.env.example` 收尾 #765。
- [x] **持久 workspace 对 user 运行自动开**（#763 B）：文件耐久从 manifest 开关解绑→跟 `ctx.user_id` 绑(有 user 自动挂命名卷,reaper rm 容器不删卷,跨回收还原)；`FilesystemSpec.persistent_workspace` 降级只控 CM-0 计划投影(与耐久解耦,投影每 run 读 PLAN.md 有成本不默认开)。语义：`/workspace`(user)=命名卷还原 / `/tmp`=tmpfs 永清。
- [x] **live 三件套验证**（CI 绿≠live）：① soffice pptx→pdf ② node ③ 持久还原。`infra/sandbox-image/smoke_test.py` 一把验 soffice+poppler+node+办公库+CJK；`tools/eval/verify_live_persist.py`（#766）驱动 supervisor :8001(无鉴权)验持久(POSITIVE restored / NEGATIVE gone)。本地真栈全过。

### Wave — 2026-06/07 运行可观测富化 + 对话中心化运营 IA

> 起点：用户看 test-agent「运行」列表觉得模块薄，问「直接接 Langfuse 取代？」。评估否决取代
> （审批/resume/改参是控制操作 Langfuse 只读做不了 + 单实例无 RLS 跨租户红线），拍板富化自有
> runs 面；继而确认真实运营单位 = (user_id × conversation)，把运营 IA 从扁平 run 列表重组为
> **用户 → 对话 → run 三层下钻**（设计 [conversation-centric-ia](./design/conversation-centric-ia.md)）。

- [x] **Runs 富化**（#876）：`token_usage` 按 `trace_id` 聚合喂 list/get + `q` 搜索；列表加耗时/token/错误列 + RunSummaryPanel 详情摘要面；深度 LLM trace 继续外链 Langfuse 不重建
- [x] **运行记录按 user_id 过滤 + 用户列**（#878）：成员运行视图，`?user_id=` URL-owned filter
- [x] **可观测性 hub 入口**（#879）：Langfuse/Grafana/Tempo 平台运维入口（system_admin 专属，跨租户红线不暴露给租户）
- [x] **对话中心化 IA M1a+M1b-1**（#880，e2e 修 #881）：设计文档 + 后端 `GET /v1/conversations`（thread_meta + `agent_run` rollup + token totals）/`{thread_id}` 详情 + `ThreadRunAggregate` store 聚合（GROUP BY 非 N+1）+ agent 详情「对话」tab 替换运行 tab + 对话详情页（`/conversations/:threadId` 摘要 + run 列表）+「历史 → 配置历史」改名
- [x] **对话中心化 IA M1b-2**（#882）：顶层 `/runs` → 全局对话浏览器 `/conversations`（thread 分组，agent/user/status/q 过滤，旧路径 redirect 兼容）+ nav「运行记录 → 对话」+ memory label →「记忆治理」+ RunDetail 返回链改指对话 + RunsList/`listRuns` SDK 死代码清退
- [x] **M1.5 统一消息 transcript**（#883）：盘点修正「无读 API」假设已过期——Playground 的 `GET /v1/sessions/{id}/messages`（checkpoint 最新 tuple + admin 放行）现成 → M1.5 纯前端：对话详情「消息记录」面（user/assistant 轮，失败只隐藏面板；tool/system 轮 by design 留 per-run 事件流）
- [x] **M2 后端**（#884）：`GET /v1/agents/{name}/{version}/users` per-user rollup（thread 窗口 + aggregate_by_threads fold，token 走 token_usage 三列零 trace join）+ `TokenUsageStore.totals_by_users` + `TenantUserStore.get_many` + memory/artifacts/usage `?user_id=` 治理过滤（非 admin 403）+ migration 0105 thread_meta (tenant, agent, user) 索引
- [x] **M2 前端**（#885）：agent 详情「用户」tab（rollup 列表）+ 用户详情页四 tab（对话/记忆/产物/用量，每 tab 独立降级）——三层下钻 UI 全通
- [x] **M3 顶层收口 + H.8-F1**（#886）：artifacts download/versions/delete/patch 四端点补 `?user_id=` admin 治理目标（统一 `resolve_target_user_id` 闸；supervisor 读天然按 user 参数化零改动）→ 用户详情产物 tab 升级全动作面 → 顶层 `/artifacts` 删；agent 详情 MemoryTab 删（维度错位,记忆=per-user 跨 agent 资产）；`/memory` 治理页补 user 过滤
- [x] **fast-follow：messages `tenant_id` + 单用户端点**（#887）：`GET /v1/sessions/{id}/messages` 补 `?tenant_id=`（system_admin 跨租户下钻 transcript，显式跨租户读记 SESSION_READ 审计；普通 admin 传外租户 403）+ `GET /v1/users/{user_id}`（用户详情 display_name 直开 URL 可取，self-or-admin 闸）+ `rbac.is_admin` 补 system_admin（对齐 is_allowed 的 tenant-ADMIN 语义，否则 per-user 闸拒掉 system_admin）
- [x] **对话浏览器运营过滤**（#888）：按智能体过滤（agents 列表喂下拉）+「仅看错误」（`RunStore` 失败线程集 → `thread_ids` 组合过滤，`has_error` ≠ `status=failed` 线程生命周期）
- [x] **对话浏览器分页真实化 + 时间窗**（#889）：`total` 改真实计数（`ThreadMetaStore.count_*` 与 list 共享 WHERE 构建防漂移）+ 前端接 server 分页（offset 翻页 + 过滤变更重置页码 + stale 响应闸）；`since` 活动窗口过滤（`thread_ids_with_runs` 泛化自 error_thread_ids，`since × failed_only` 组合 =「今天坏了什么」；前端 1h/24h/7d 预设）；顺手修 N-4——`list_all_tenants` 补 `user_id` 下推，退场跨租户 Python post-filter
- [x] **对话浏览器末次活跃排序 + 待审批过滤**（#890）：列表默认排序改「末次活跃」（`ThreadMetaStore` `order_by="last_activity"`——max(run.created_at) 子查询 outerjoin + coalesce 回退线程创建时间，仅 SQL 后端真排；会话历史等其他消费者默认 `created_at` 不变）+ `has_pending` 过滤（`thread_ids_with_runs` `only="failed"|"pending"`，多过滤线程集在 API 层求交）+ 前端「待审批」checkbox
- [x] **对话浏览器过滤 URL 化 + 自动刷新 + 返回链**（#891，纯前端）：全部过滤/页码收进 URL（可分享、刷新不丢、tenant 切换清页码）+ 详情页 back 链接经 `location.state.from` 原样还原浏览器视图（过滤+页码；无 state 回退 agent 对话 tab）+「自动刷新（30 秒）」监控模式（silent 轮询不闪 loading）
- [x] **对话全文搜索 M4**（#892，用户拍板含 agent 详情对话 tab）：checkpoint blob 无法 SQL 下推 → `thread_message` 镜像表（(thread_id,seq) PK + pg_trgm GIN + FORCE RLS）+ `thread_message_sync` 水位（缺失=回填队列，自动收敛）+ `TranscriptMirrorSweep`（60s，bypass 扫/per-tenant 写，水位先于读防漏尾；提取抽 `transcript.read_turns` 与 messages 端点同源）；`q` 升级「标题 OR 消息内容」（`q_thread_ids` 下推 list/count 共享 WHERE）；agent 对话 tab 加搜索框 + 顺手接真分页
- [ ] fast-follow：对话/run 成本（人民币）列（现定价是月度 rollup，无干净 per-run cost 函数）

### Wave — 2026-07 SE-16 进化飞轮可运转性 uplift

> 触发：用户要求评估 skill 进化功能整体设计。全链只读评估结论：**验证-晋升-回滚闭环成熟
> （晋升闸三层 tier 核实已达标，judge-only 永不 auto），但飞轮不可运转**——输入端信号饥饿
> （打标端点 G.6 就绪但全仓无调用方，👍/👎 按钮从未落地）、输出端晋升后无 agent 装配（白蒸馏）、
> 生产端无灰度（默认 False 至今）。业界对标（2026-07-02 二轮调研）：显式打标率 <1% 不能当主粮，
> 主流=隐式信号+judge 抽样+打标校准三层金字塔；技能库标配 dedup/merge（helix 缺）；
> 检索式技能消费（lazy 档在小库等价，>20 转检索）。
> 设计：[STREAM-SE-DESIGN § 12 SE-16](./streams/STREAM-SE-DESIGN.md)（Mini-ADR SE-A38~A47）。

- [x] **设计定稿**（本 PR）：核实结论 + 断点图 + 信号金字塔 + 九决策 + 实施拆分
- [x] **PR-1 打标入口**（本 PR）：Playground assistant 轮挂 👍/👎（👎 弹 comment 框，每轮一次，失败内联可重试），接现成 `POST /v1/sessions/{id}/feedback`（turn_seq=转录序号）；业务系统代提交契约记入设计 §12.3（对外集成文档体系缺失另立项）——试点信号主力
- [x] **PR-2 重试档 + 灰度**（本 PR）：蒸馏 transient 异常不烧候选（`TransientEvolutionError` + cause 链嗅探，`retry_count` 列 ≥3 才标 evolved_at；migration 0107）+ `tenant_config.skill_evolution_enabled` per-tenant 白名单（worker per-candidate 查含缓存，未开通候选跳过不烧；无 config 行=未开通）+ **admin-ui**：租户设置页展示「技能自进化」状态（改值走既有 JSON 编辑面——灰度是审慎操作非随手开关；抽样率输入随 PR-4）——解锁 live 试点
- [x] **PR-3 信号扩容**（本 PR）：`implicit_success` 隐式正信号——**实施期修正**：设计的 5min 重发规则会把正常多轮追问全排除，改为 run 级「静默收尾」判定（thread 无失败/待审 run 且末次 run 静默 ≥30min，零新依赖 RunStore 聚合现成；活跃 thread 只是「还不是候选」下轮重评）+ 👎 轨迹（带 comment，含「假成功」）优先注入蒸馏 failures 对比侧 + `WEAK_LABEL_SIGNALS` 永不 auto-promote（gate 收紧；👍 维持可 auto）；migration 0108 放宽 signal CHECK
- [x] **PR-4 judge 抽样评分**（本 PR）：隐式候选池抽样提纯——`tenant_config.skill_evolution_judge_sample_pct`（默认 5%，migration 0109），evolution worker 蒸馏前对 `implicit_success` 候选过筛：按 trajectory_key 哈希**确定性抽样**（retry 重扫不重掷），抽中者便宜 aux 模型评会话质量分（≥4 过），未抽中/低分标 evolved 不进蒸馏（日志+metric 可见）；judge transient 错误走 PR-2 重试档；aux 调用记真 tenant_id（部分对齐 SE-A43）；与晋升闸 judge 体系分离，👍/failed 候选不过筛 + **admin-ui**：租户设置页展示抽样率 + JSON 编辑模板补进化两键；打标用作 judge 校准集
- [x] **PR-5 晋升→生效补链**（本 PR）：`AgentSpec.auto_attach_evolved_skills` opt-in（默认 off，FIELD_TIERS 归 CAPABILITY 档）——build 时自动附加「本租户 + 本 agent 蒸馏 + ACTIVE + evolution_origin=distilled」技能，**强制 lazy**（仅 summary 进 available-skills）+ **软失败**（重名/模型不匹配/工具冲突/文本类组件跳过并 log，绝不 brick 合法 manifest；manifest 显式声明恒优先）；进 `resolved_versions` → `bound_distilled_skills` → rollback 监控/curator 衰退语义自动恢复；>20 只 log warn（检索预选=既定演进触发条件，不提前建设）+ **admin-ui**：Skills tab 开关（form_model 读写器 + i18n 双语）
- [x] **PR-6 aux 计量**（本 PR）：核实发现 aux 调用（distil/revise/attribute/judge/screen）**此前完全无计量**（token 数在 adapter 回复里就地蒸发）——补 `token_usage.usage_kind` 列（A 案，migration 0110，默认 conversation 回填旧行）+ per-candidate metering scope（contextvar，worker 进入；真 tenant_id + 蒸馏源 agent + `skill-evo-{candidate_id}` 确定性 trace_id）+ aux 座记账（never-fail 契约）+ **replay 主图 build 穿 `token_usage_kind='skill_evolution'`**（replay 是进化最大开销，不穿 kind 则"进化成本单独可见"不成立；AgentBuilder/build_agent/middleware 链全穿）+ replay judge 弃 `_NULL_TENANT` 改记候选租户 + `/v1/usage/tokens` 加 `by_kind` 分列与 `kind` 过滤 + **admin-ui**：用量页「按用途」表（对话/技能自进化分列）
- [x] **PR-7 入库查重**（本 PR）：蒸馏产物 vs 同 agent 现有 distilled 技能（DRAFT+ACTIVE，各取最新版）embedding 余弦相似（阈值 0.9，比 consolidator 同义 0.85 更严——误合并代价高），命中 → 挂为该技能下一版（version+1，replay/晋升路径照旧），**ACTIVE 目标先翻 DRAFT 重审**（关键副作用：`add_version` 即刻 bump `latest_version` 被 bare-name 解析服务，未验证修订绝不能直接上线；auto-promote 通过再翻回=无缝升级）；降级语义=deduper 任何故障返 None 正常新建（查重是优化不是闸）；命中写 `SKILL_EVOLUTION_DEDUP_REVISION` 审计（含相似度）+ **admin-ui**：SkillsList DRAFT 行 v>1 显「修订待审」Tag+tooltip（审阅者应 diff 前版而非当新条目审）
- [x] **PR-8 promote 事件发射**（本 PR）：`skill_promote.requested` 加入 `WebhookEventType`（protocol Literal + API 校验 frozenset 双处同步）；webhook 投递 worker sweep 加段——扫 pending 可见性晋升申请（`skill_promote:{request_id}` 幂等 event_id），payload 带 skill/version/发起 agent/reason；per-agent endpoint 按 `requested_by_agent_name` 匹配 + **admin-ui**：webhook 表单事件下拉补新类型（触达渠道与审批通知 backlog 合并另立项）
- [x] **收官 = live 试点全场景**（计划成文于设计 §12.10，14 场景五阶段）：信号→候选（三类信号+改口排除+transient 重试+judge 抽样滤除）→ 蒸馏→验证（👎语料佐证+implicit 禁 auto+查重转修订轮）→ 晋升→生效（人审/auto/限速/kill-switch+**attach 后 skill_run_usage 出 with 行**=断链修复直接证据）→ 回滚+计量对账（造衰退→ARCHIVED+共享熔断；token_usage 无 NULL_TENANT 残留）→ 运营面（审批证据/打标控件/未灰度租户全链静默）；预期逮 3-5 live 坑（9.4/4.4 先例），修完才 ★5
  - 2026-07-03 live 试点跑完（dev 栈，test 租户/test-agent）：**全飞轮首次 live 闭环**，with 行 + last_used_at 关键断言达成；实逮 9 坑（结果注记见设计 §12.10 末）。修复 PR：replay 计量断链（config 穿 tenant/user）、蒸馏版本 content_hash 缺失+0111 回填（drift 拦加载）、BuiltAgent 缓存吞晋升（四翻转点 invalidate_tenant）、recorder 与 rollback-monitor 解耦、LLM router 故障全族入重试档（含 key/auth，拍板）、implicit 改口 5min 重发排除（拍板）、dedup 阈值 0.9→0.60（真 embedder 校准）。partial：场景 9/10 auto 通道与限速、11 人为衰退、13 浏览器面（逻辑单测厚覆盖，live 需可控 delta/批量失败流量）
- 显式不做（对标依据在设计 §12.8）：shadow/champion-challenger 对照、环境信号（缺埋点，backlog）、技能 merge
- [x] **收官后 backlog 三连**（SE-16 使人审队列有真流量后价值上升）：① webhook 渠道格式 `payload_format`（generic/feishu/dingtalk/wecom，migration 0112）——IM 机器人直接收 `approval.requested`/`skill_promote.requested` 文本消息，飞轮人审环节有触达不再积压（HMAC 照签、断路器/重试/dead-letter 全复用；钉钉加签/卡片消息/@人 backlog）；② consolidator aux 计量（`usage_kind='memory_consolidation'`，PR-6 同族 gap 的另一半，chargeback 全覆盖）；③ 人审面冷启动空态文案（「轨迹池不足时蒸馏跳过重放」，live 试点坑 #1）

### 显式不做（理由在册，需求出现随时重议）

- 浏览器执行面 / GPU（产品决策，非工程债）；多语言运行时：**node 已纳入单一 full 镜像**（经 `bash` 跑 JS 技能），浏览器自动化（Chromium/Playwright）仍归 MCP 不进镜像
- agent 间通信、专门化 agent 类型注册表（单链委托可靠性共识）
- 事务性工具 / 部分回滚（行业未解题）

---

## Stream RT — Runtime 对标缺口补齐（2026-07-03 起）— 设计 [STREAM-RT-DESIGN](./streams/STREAM-RT-DESIGN.md)

> 触发：五方对标矩阵（[research/2026-07-03-agent-runtime-five-way-benchmark.md](./research/2026-07-03-agent-runtime-five-way-benchmark.md)，PR #904）——helix 84%、9 格独占，4 格被压制、3 格已知弱点。价值导向排 7 项 × 4 Wave（~8-9 周），每项 PR-0 设计先合再编码，每项显式含 admin-ui 前端工作。已拍板：browser 不纳入（归 MCP，见「显式不做」）；企业深水区（KEK rotation/数据驻留/多区域）等客户信号。
> 勘误注记：对标矩阵 2.1 给 helix 1 分偏低——L2 ContextCompressor（PR #206）已有 preflight 摘要压缩，RT-2 范围据此收敛为深水区补齐（详见设计 §6）。

### Wave 1（并行）
- [ ] **RT-1 结构化输出强制**（~1 周，4 PR）：router 层 JSON Schema 声明→按 provider 能力选径（response_format/tool-call 强制/prompt+校验）→校验+有界重试；内部链路迁移（judge/consolidator/evolution 删手工 parse）；Tier3 暴露 output_schema + manifest 编辑器提示
  - [x] **PR-0 细设计定稿**（本 PR，设计 §7）：现状取证（provider 层零 response_format 代码；全仓 9 处手工 JSON parse 点清单）+ RT-ADR-1~4（校验重试独立于 E.4 failover 绝不轮 key/三级降级链能力声明在适配器/迁移不改失败语义/Tier3 仅约束收尾回复）+ PR-1~3 切分
  - [x] **PR-1 结构化输出核心**（本 PR）：`StructuredOutputSpec` 协议 + `complete()` 可选 `output_schema`（None 时 byte-identical,旧 double 零破坏）+ 三径（openai native response_format strict / anthropic 强制单 tool_call+钉 thinking disabled / openai_compatible prompt 注入）+ 校验重试循环（同 key max2 错误上下文回喂,`LLMOutputValidationError` 不入 failover,显式测试不轮 key）+ 12-call worst case 计量（`helix_llm_structured_validation_*`）。code review ✅ 零 CRITICAL/HIGH,3 便宜项已修;rebase 时逮到并修复 coalescing×prompt 径交互 bug（原 wire 后追加 system 会复活 400 面,改 coalesce 前注入 SystemMessage 折进前导块末段,真函数串联集成测试锁死）。全量 5366 passed
  - [x] **PR-2 内部链路迁移**（本 PR）：7 处 parse 点迁结构化输出（LongMemEval 纯文本 yes/no 按设计不迁）——走 router 的 5 处（output/action judge、consolidator 集群+单项、skill distiller）定义 pydantic 输出模型传 `output_schema`、消费 `additional_kwargs["parsed"]`,删手工 json.loads/正则抽取;直连 API 的 2 处（eval judge Anthropic 信封、LoCoMo label）迁 pydantic `model_validate_json`,不接 output_schema,保留原局部容错（digit 正则/label 词兜底）。RT-ADR-3 语义规则与失败方向逐处保留(judge 失败→raise→fail-open/closed、consolidator→false_cluster/保守已审、distiller→None;形状校验收紧,分歧全部保守向且伴随 router 强制+重试近不可达),`LLMOutputValidationError` 显式映射到同一降级分支并测试锁死;核心决策字段必填,迁移前本就带 `.get` 默认的描述性字段(judge reason/cluster summary+reject_reason/distiller tool_names+description+category)保留默认值,消除「缺描述字段→校验失败→fail-open 放行」安全翻转角(无 reason 的 leak verdict 仍 BLOCK,测试锁死)。aux 协议全链透传:`ConsolidatorAuxModel`/`DistillerModel` 加可选 `output_schema`,`ConsolidatorLLMReply.parsed`/`DistillerReply` 回带校验 dict,null/fake double(含 metering/screener aux double)同步。附带修 PR-1 披露交互:LLM cache 键并入 schema 指纹（name+schema 内容 sha256）,`output_schema=None` 时键 byte-identical（旧缓存不失效,金测试锁死）;指纹现阶段休眠——生产 before/after payload 尚无人放 spec,自 PR-3 agent-loop 接线 `payload["output_schema"]` 起生效（设计 §7.4 已注记接线硬要求）
  - [x] **PR-3 Tier3 output_schema 暴露 + 收尾应用 + cache 接线 + 前端**（本 PR，设计 §7.6）：`AgentSpecBody.output_schema`（`OutputSchemaSpec`={name,json_schema,strict}，FIELD_TIERS=CAPABILITY，形状校验在协议、深度 check_schema 在 build）+ agent loop 收尾应用（RT-ADR-4 三径统一两段式：主调用永不带 schema/收尾候选本地校验零成本/不符一次结构化重发 tools=[] 候选+correction 追加/仍不符 `LLMOutputValidationError` 挂 run；候选-correction 交换不落 checkpoint，budget-exhausted 收尾轮同受约束）+ §7.4 cache 接线生效（重发自有 before pass + 底部 after pass 均携 spec **实例**，主调用单独 after pass 保 G.9 计量 exactly-once；spy identity + 真 E.13 write→read 往返 + 异 schema fingerprint 必 miss 三证）+ §7.5 prompt 径围栏（`fence_nonce` 只 delimiting 不 datamark——datamark 破坏键 byte-exact 复现；aux None 径 byte-identical）+ DLP 改写后 parsed 重导出防泄漏 + `helix_llm_structured_finalize_total` + 前端（schema 端点自动透出验证、FormView prompt tab 状态块、i18n 双语）+ 文档（设计 §7.6）+ review 修复：**结构化缓存投毒双侧防**（重发后被 PI-2/judge/DLP 改写的响应落结构化键→下次命中重校验失败硬挂；lookup 侧命中先校验、不合规忽略回落真重发 + store 侧无 parsed 不打 schema 键落非结构化键；红绿回归双测）+ E.13 指纹并入 strict（结构化条目本 PR 才开始可写零迁移，None 金测试不动）+ metric 保真（cache_hit 只计校验通过命中）
- [x] **RT-2 compaction 深水区补齐**（6+2 PR，★5 live 已实证 2026-07-04）：范围经 PR-0 复核修正（设计 §8 为准）——PR-1 adapter 兼容核证+summariser 失败语义+摘要 prompt 预算硬化+order-pin 测试 / PR-2 memory 注入 2k 预算+correction 保底 / PR-3 skill 引用式 rescue / PR-4 COMPACTION 事件+metrics+hide_from_ui（顺修 CM-1 泄漏）/ PR-5 前端 / PR-6 live E2E（含 qwen 严格后端）/ PR-7 harness 硬化（token 自动刷新 + auto-size）/ PR-8 harness 硬化二轮（首次成功 live run 暴露：8192 输入上限致 422 / 固定开销空中段误报 RT-ADR-5 / None 窗口该查 catalog / 进度打印 / 无记忆 agent 文档）；ID-swap 与异步队列判定不做（RT-ADR-8）。**★5 live 实证**：test-agent@qwen3.7-max（真严格后端）三断言全 PASS——压缩零 400（RT-ADR-5 coalesce 成立）+ COMPACTION 50805→33817 token + 播种 code 完整答出
  - [x] **PR-0 细设计定稿**（本 PR，设计 §8）：deer-flow 新版复核颠覆 tracker 三处（skill rescue 三预算被 #3887 废弃改引用+重读/ID-swap 升三元组且 helix 无需求/摘要写回被废弃 helix 模式本就领先）+ 新事实 #3711：**helix `<context-summary>` 中部 SystemMessage 在严格后端疑似 live bug（RT-ADR-5 最高优先核证）** + RT-ADR-5~10 + PR 切分修正（兼容鲁棒/memory 注入硬化/skill 引用 rescue/可观测含 CM-1 泄漏修复/前端/live E2E）；tracker 已同步上游复核注记
  - [x] **PR-1 兼容与鲁棒**（本 PR）：RT-ADR-5 核证结论——**live bug 证实**：openai.py 中部 SystemMessage 按原位透传 role:system（qwen/glm/deepseek/vLLM 全走此路 400 面真实），anthropic 本就上提合并幸免；修复 = `llm/coalesce.py` per-request coalescing（first-wins kwargs、围栏逐字保留、conscious-call 签字），anthropic/openai 入口各一行。RT-ADR-6 失败语义：transient 跳过+**per-thread streak 分桶**（review HIGH 修复：BuiltAgent 按租户缓存共享实例，全局计数会跨对话假失败/掩盖真故障；1024 桶上限最旧驱逐）连 3 抛、RunCancelledError 放行、empty-middle fail-hard 保留。RT-ADR-10 摘要预算：per-message 2000/总 24000 字符、头 2/3+尾兜底、update 双半分。四道闸 order-pin 测试 + 3 个 `helix_cm_compressor_*` 计数器。全量 5334 passed
  - [x] **PR-2 memory 注入硬化**（本 PR）：RT-ADR-10 注入预算——`_inject_memories` 贪心逐条 token 预算（默认 2000，`memory.long_term.injection_token_budget`，ge=100/le=100k），按 rerank/MMR 既有序累加、边界条截断（可见标记）后即停、后续条丢弃；correction 保底——**核实 `MemoryItem.kind` 无 correction 值（仅 fact/episodic），最合理等价 = M-4 纠正 API 的 `confidence=1.0`（user-asserted）**，保底 500 tokens（`correction_token_budget`，ge=0 可关）先选不被普通记忆挤出、超保底回落总预算竞争（floor 非 cap，deer-flow guaranteed_categories）；选择变、渲染序不变→默认路径（top_k=5 普通记忆）逐字节不变（显式测试钉死）；估算器复用 HX-A1 共享 tiktoken 单例（compressor 同源）。RT-ADR-8 注入块x压缩组合测试：真 `_inject_memories`+真 compressor+假 summariser——head_keep≥1 时 messages[1] cache-anchor 块保留在 head 位置 1；边界锁现状：head_keep=1 块活但 task 被摘走、**head_keep=0（协议 ge=0 允许）块被摘掉=anchor+记忆静默全失（已知风险只锁不修）**。metric `helix_memory_injection_truncated_total{outcome=truncated|dropped}`。FIELD_TIERS 无需动（memory 整块 TENANT_OWNED）。全量 5381 passed
  - [x] **PR-3 skill 引用式 rescue**（本 PR）：RT-ADR-7（上游 #3887 已废弃三预算全文保留，改引用+重读）——lazy skill 运行期以 `ToolMessage(name="skill_view")` 进 context（≤20k，artifact 带 skill_name/path/result），两道吃它的 prompt-view 闸改留一行引用（skill 名 + 来源 path + 重读提示，O(1) token）。新共享渲染器 `context/skill_reference.py::skill_view_reference`。**结果门（review HIGH 修复）**：skill_view 全部失败分支（not_allowed/not_found/archived/drift/redacted）也往 meta 塞 skill_name，其中 drift/redacted 是安全拦截——引用 stub 只对 `result∈{ok,truncated}` 成功读生效，BLOCKED 占位绝不改写成"可重读"怂恿（五值参数化 + pruner 级回落测试锁死）。**注入面（review MEDIUM 修复）**：name/path 全 `!r` repr 转义（换行不出括号）+ skill_name cap 200。**path rung 点亮（review 放开纪律一行）**：skill_view 成功分支 meta 补 `"path"`（失败分支不补，配合结果门），测试全走真实 meta 形状。**措辞（review MEDIUM 修复）**：stub 用非调用语法 `re-readable via the skill_view tool (name 'x', path 'y')`——summariser 系统提示禁 tool-call 语法，调用形提示会被摘要丢弃；声明收敛 = 摘要保 skill 名，重读把手由 available-skills 列表 + pruner stub 提供。CM-12 pruner：`_collapsed_content` 梯子加第 0 级——成功 skill_view 收敛为引用 stub（包 `<tool-result-pruned>` 标签幂等免费），**优先于 footer/persist-path 两级**（skill 库持久、workspace 副本 run-scoped）；L2 compressor：`_format_middle_for_summary` 中段成功 skill_view 以引用行进 summariser 输入，真 compress+假 summariser 串联测试锁死。默认路径（无 skill_view 消息）byte-identical（显式测试钉死）。CM-2 WorkingWindow 整 turn 丢弃**不加特判**（RT-ADR-7 最小实现）：available-skills 列表仍在 system prompt，模型可再调 skill_view；turn 级窗口做 per-message 手术得不偿失（skill_reference 模块 docstring 记录取舍）。全量 5431 passed
  - [x] **PR-4 可观测 + hide_from_ui**（本 PR）：**COMPACTION 事件**——`compressor.compress` 新增 `on_compacted` 最佳努力回调（对称 `on_pre_compaction`，压缩真产摘要时**每 compress 一次**触发一次，携 `CompactionStats{passes,tokens_before,tokens_after,summary_chars}`，skip-only/未过阈不触发）；低耦合接线 = compressor 不依赖 SSE，`agent_node` 从 `config[COMPACTION_SINK_KEY]` 取 sink 建回调（吞非 cancel 失败），sink 由 `sse.run_agent` 注入（闭包持 bridge/event_store/`nonlocal event_seq`，`bridge.publish(run_id,"compaction",…)`+`_persist_event` 同一 seq，落在该 turn `updates` chunk 前）；`EventType.COMPACTION="compaction"` 补进协议 taxonomy（run_event event_name 仍自由字符串，无枚举闸门），sse.py 用 `EventType.COMPACTION.value` 单一真值源。**metrics**——补 `helix_cm_compressor_tokens_saved_total`（回收 token 计数；histogram 因 `_seconds` 命名硬约束不可用故用 counter），PR-1 三计数器不重复。**hide_from_ui（RT-ADR-9，Option A——独立审查逮 HIGH 后按 deer-flow 源码修正）**——`additional_kwargs["helix_hide_from_ui"]=True` 标记；**过滤只在 UI 服务边界，记录层永远忠实**：`read_turns(include_hidden=True)` 为安全默认（mirror sweep + 跨租户审计下钻拿完整记录，新持久/审计调用方忘传也不静默丢审计——#880-892 全文搜索/审计源保真），仅 UI 气泡视图（同租户自读 `/messages` 端点）传 `include_hidden=False` 过滤；**绝不在 mirror/搜索层过滤**（参考 deer-flow `thread_runs.py:209-220` gateway router UI 读路径判 hide_from_ui、checkpoint 忠实读全部）。**顺修 CM-1 泄漏**——recovery-advisory 是唯一落 checkpoint 的注入 HumanMessage（plan/memory/wrapup 均 prompt-view-only 不入 checkpoint，已核实无需标），打标后不再在会话详情显为用户气泡，但审计/搜索保留。**head_keep 保护（§8.4 拍板）**——`floor_head_keep_for_injection` 运行期 floor（per_session recall 激活且 head_keep<1 抬到 1+warning，非 memory agent 的 0 不 brick），PR-2「锁现状」组合测试改「保护生效」。测试：compressor on_compacted 触发/不触发/tokens_saved 增量 + agent_node sink 接线（真图触发/不触发）+ sse publish&persist 顺序&seq + advisory 打标 + transcript 过滤 + factory floor+warning（正反）+ floor helper 三例。CI 全绿（ruff/format/mypy/check_metric_names/pytest not-integration）。前端 EVENT_COLOR + compaction 卡片留 PR-5
  - [x] **PR-5 前端 compaction 渲染**（本 PR）：run 事件流把 COMPACTION 事件从"不可见"到可读。`api/tool_timeline.ts` 加类型化解析器 `parseCompactionEvents`（收窄 `SseEvent.data:unknown`→`CompactionSummary{passes,tokensBefore,tokensAfter,summaryChars}`，缺字段/非数值/非对象一律跳过不半渲染）；`EventStreamPanel` EVENT_COLOR 补 `compaction:"purple"`（raw 视图 tag），timeline 视图（默认）在 ToolTimeline 前渲染 `CompactionSummaryList`/`CompactionCard`——「上下文压缩 · N 次 · 12,000 → 3,400 tokens(−72%) · 摘要 890 字」，token 数 `toLocaleString` 千分位、reduction% 前端算（before>0 时 `round((before-after)/before*100)`）；i18n 双语 4 键（compaction_label/passes/reduction/summary_chars，en 类型块+值块+zh-CN 值块三处）；SDK `SseEvent.data:unknown` 无需改。后端已挡 hide_from_ui（PR-4 Option A），前端会话详情无需再处理隐藏。测试：`tool_timeline.test.ts` parseCompactionEvents（提取有序 + 跳过畸形/非 compaction）+ `EventStreamPanel.test.tsx` timeline 渲染 compaction 卡片（断言 label/12,000/3,400/72%）。前端全门：tsc -b --noEmit exit 0 / vitest 597 passed(+3) / vite build ✓ / storybook build ✓；顺修 #914 遗留的本表 PR-4 hide_from_ui 描述（旧 Option-B 表述改 Option-A）
  - [x] **PR-6 live E2E harness（本 PR，★5 live 待用户环境跑）**：`tools/eval/verify_live_compaction.py`——照 `verify_live_*` 家族惯例（keyless，key 服务端；读 `HELIX_API_URL`/`HELIX_API_TOKEN`；client 可注入 → MockTransport CI 覆盖）。驱动真实 agent（优先挑 qwen/glm/deepseek 严格后端）：turn1 播种独特 account code → 大 filler 轮堆上下文直到 COMPACTION 事件 → probe 复述。**三断言**（CI 无法覆盖的 live 半）：① 压缩发生时无 error 帧（RT-ADR-5 严格后端 400 回归信号——coalesce 若退化即报）② 观测到 `event:compaction`（PR-4 可观测，`tokens_after<tokens_before`）③ 播种事实经压缩后仍可答（head/tail+摘要保真）。companion `test_verify_live_compaction.py` 5 例（PASS/错误帧→FAIL/无压缩→FAIL/事实丢失→FAIL/非严格后端 warn）全绿；ruff/format 过。**live run（用户手动，dev-up + qwen agent）**：`export HELIX_API_URL/HELIX_API_TOKEN; uv run python tools/eval/verify_live_compaction.py`——退出码 0 即 RT-2 ★5 live 章达成。**RT-2 父项 ★5 待此 live run PASS 后勾。**
  - [x] **PR-7 harness 硬化（本 PR，首次 live run 暴露两处脆弱后补）**：真环境首跑 doubao 逮两坑——① dev bearer token 从 `helix-agent-api-internal` client 发，该 client `access.token.lifespan=300`（5min），多分钟 live 跑必中途 401；② 固定 8000 字/轮 fill 对未知 `context_window` 要么永不触发（大窗口）要么撑爆（小窗口）。**修**：① `_RefreshingAuth(httpx.Auth)` 401 自动重 mint + 重试一次（仅看 status，不读 body 故 streaming 安全），`_mint_token`/`_build_mint` 从 `HELIX_OIDC_*` env 取（client_credentials 或 password grant），缺 OIDC env 则回落静态 `HELIX_API_TOKEN`（旧行为不变，token 永不 log）；② `_plan_fill(context_window,threshold_pct)` 按 compressor 契约（`tokens≈chars//4`，触发=`context_window*threshold_pct`）算每轮 fill——每轮封顶阈值 35%（单轮永不溢出触发面，保住可切中段）且累计过冲 30% 保证落，`_agent_context` 从 `/v1/agents` 读 `spec.model.context_window`/`spec.policies.context_compression.threshold_pct`（缺则回落 200k/0.7 factory 默认），`--fill-chars`/`--max-turns` 仍可覆盖。测试 +7（`_plan_fill` 五窗口参数化：单轮<阈值∧累计>阈值∧max_turns≥3 / `_RefreshingAuth` 401→重 mint→重试带新 token / 无 minter 401 透传）；5 例既有 MockTransport 测不回归。全 tools/eval 211 passed；ruff/format 过。**live run 命令不变**（可选加 `HELIX_OIDC_*` 走自动刷新）。
  - [x] **PR-8 harness 硬化二轮（本 PR，首次成功 live run 逮 5 项）**：真跑 test-agent 逮到——① auto-size 无视 `RunRequest.input` 8192 上限 → 大窗口算出 37KB → 422；② `_plan_fill` 没扣 agent 固定开销（system prompt+工具+技能+记忆 实测 ~20-24k token）→ 小窗口触发太早致空中段 `ContextOverflowError` → harness **误报成 RT-ADR-5 帧**；③ `_agent_context` None 窗口死回落 200k，服务端实按 catalog（qwen3.7-max=1M）；④ fill 期全静默；⑤ 事实留存断言被跨会话长期记忆污染（旧 code 盖当前 seed）。**修**：① `_plan_fill` 封顶 `_MAX_INPUT_CHARS=8000`；② 按 `room=threshold-_ASSUMED_OVERHEAD_TOKENS(24k)` 算 fill + `_is_context_overflow` 把 ContextOverflowError 判为**配置问题非 RT-ADR-5**（关键正确性修复，别再误报 coalesce 回归）；③ guarded import `catalog_entry` 镜像 `agent_factory._resolved_context_window`（显式>catalog>200k）；④ 每轮 `[fill] turn=i/N` 进度；⑤ docstring 注明目标 agent 要小 `context_window`(~64k) + 关长期记忆。测试 +8（8192 上限 7 窗口参数化 / ContextOverflowError 判配置非 RT-ADR-5）；全 tools/eval 220 passed；ruff/format 过。**★5 live 实证：test-agent@qwen3.7-max(context_window=64000+关记忆) 三断言全 PASS，退出码 0**——RT-2 收官。
  - [x] **RT-follow-up 输入上限放宽 8192→65536**（PR-8 live run 暴露的两个产品决策之一，deer-flow/Hermes 对标后收敛）：核实 8192 是 #41 早期任意 DoS 兜底，且**只咬"裸粘长文本进消息框"这一场景**——书/文档走 25MB 文档路（沙箱 `read_document` 按页读，字节从不进 `input`）、图片走 `image_refs`；deer-flow（graph input 无 char 上限）/ Hermes（平台消息无框架层上限）都不在入口按小固定值拒,靠模型窗口+压缩接管(helix RT-2 已具备)。改动外科式:新增 `MAX_RUN_INPUT_CHARS=65536` 常量(runs.py 定义、agents.py 导入)用于 `RunRequest.input` + `ExternalRunRequest.input` + 前端 `PlaygroundTab` maxLength;**分流字段不动**(`untrusted_content` 16×8192 / jinja `inputs` 每值 8192,各有语义边界);同步 tools/eval 陈旧注释。测试 +3(RunRequest/ExternalRunRequest 上限收放边界)。**第二个产品决策(agent ~20-24k 固定开销瘦身)见下条 RT-follow-up 已收敛。**
  - [ ] **RT-follow-up 技能渐进式披露默认化**（第二个产品决策，设计 [STREAM-RT-DESIGN](./streams/STREAM-RT-DESIGN.md) §9 / RT-ADR-11~12）：实测澄清"20-24k 固定开销"是 PR-8 harness 假设虚高（把 head/tail 保留的 10 条 filler≈20k 误算进固定开销）——真实 test-agent 系统提示 ≈5k tok，**92% 是 2 个 eager 技能 body 内联**（`SkillVersion.lazy_load` 默认 False）；平台强制子句仅 ~300 tok（非 bloat），工具 schema ~3-4k（不进压缩估算）。deer-flow/Hermes 源码对标：**两者都纯渐进式披露（name+description 索引 + body 按需 read_file/skill_view），从不默认内联 body**。决策（用户拍板 2026-07-04）：**翻 authoring 默认 lazy_load False→True**（新技能）+ **仅翻 curated 平台技能存量**（xlsx/pptx/docx/pdf，`WHERE tenant_id IS NULL`，定向 migration）；租户自建存量不动（U-15 不回归）；eager 留 opt-in。护栏 RT-ADR-12：lazy 成败押描述质量，eval 验机制（摘要在/body 不 eager 注入）。**PR-0 设计（#926 已合）/ PR-1 后端默认+迁移+单测+eval 脚本护栏（#927）**。**PR-2 前端砍**：UI 已只读展示 lazy/eager badge+tooltip，技能导入优先（手建表单已删 #709-718），无"创建表单"可翻默认；设 eager 走 frontmatter `helix.lazy:false`/API 足够，加 UI 开关是低频动作重复造轮子（用户 2026-07-04 拍板砍）。eval 护栏落法：脚本层 `tools/eval/skill.py` 加 `expected_prompt_absent` + lazy 用例（CI 证机制：摘要 present、body absent）；behavioral（真模型凭描述 skill_view）经现有 curated 技能 agent（pptx/xlsx 迁移后已 lazy）正常使用即验，不另造跨模型 live 脚本。backlog：Hermes 式分层降级、per-binding manifest override。

### Wave 2（并行，W1 收完启动）
- [x] **RT-3 prompt cache 成本工程（PR-0 设计 #928 + PR-1 前端透出，本次）**（设计 [STREAM-RT-DESIGN](./streams/STREAM-RT-DESIGN.md) §10 / RT-ADR-13~15）：**grounding 核实计划假设过期——后端基本已建好**（四段计量端到端 + Anthropic cache_control 断点编排 + cache 折扣定价列全已存在）。真实 gap = **前端透出**。**keep-warm 不做**（RT-ADR-13，多租户成本不划算，用户拍板）；tool-def 断点推迟（RT-ADR-15）。OpenAI 无 cache_control 是正确的（隐式缓存读 cached_tokens 即可，非缺陷）。
  - [x] **PR-1 前端透出（本次）**：`SettingsUsage.tsx` cost/token/kind 三表补 cache_read/cache_creation 列 + 命中率列 + 头部命中率 Statistic；`SettingsBillingChargeback.tsx` 租户表 + per-agent 下钻表补 cache 列 + 命中率；共享 `utils/cache.ts`（`cacheHitRate=cache_read/(input+cache_read+cache_creation)`/`formatHitRate`，8 例 pure 单测）；i18n 双语；零后端。**节省金额已砍（RT-ADR-14 修订，用户拍板 2026-07-05）**：撞租户 Usage 页无泄漏规则（不露价差）+ 租户无 per-mtok 定价访问（rate-card 是 system_admin-only）+ 两侧 cost API 无 per-segment/per-model 成本→纯前端算不了权威反事实。命中率（token 派生非价格，两页合规）即 COGS 有效性信号，满足目标；硬金额需后端 rollup 补 `cache_savings_micros`（进 backlog，只上 Chargeback）。前端审 tsc-b/vitest 613 绿。
- [x] **RT-4 全局 kill switch（全交付：#928 设计 + #929 后端 + #930 前端）**（设计 [STREAM-RT-DESIGN](./streams/STREAM-RT-DESIGN.md) §11 / RT-ADR-16~18）：grounding 核实——**租户级 suspend 已有**（TenantStatus + TenantStatusService + 中间件/准入 403，但只拒新不杀运行中）；**agent 级 disable 缺**；**bulk-cancel 运行中 run 缺**（RunManager.cancel 只按 run_id）。决策（用户拍板）：agent 级 disable（净新，独立 `agent_disable` 表 + 缓存服务 + 三 gate + enable，RT-ADR-16）+ **bulk-cancel 运行中 run 两级都做**（RT-ADR-17，新 `list_running_runs(scope)` + 循环 RunManager.cancel，跨副本经 abort_event；agent disable + 租户 suspend 都补终止）+ 审计（RT-ADR-18，新 AuditAction `agent:disabled/enabled` + resource_type `agent` 加两处 Literal）。PR-0 设计 / PR-1 后端（表+migration+service+三 gate+bulk-cancel+API+审计+单测）/ PR-2 前端（AgentDetail danger 按钮复用 SettingsTenants Popconfirm 模式 + 状态标识 + i18n）。
  - [x] **PR-0 设计（#928 已合）**：RT-ADR-16~18 落 §11。
  - [x] **PR-1 后端（#929 已合）**：`agent_disable` 表（0114 migration + RLS，复合 PK tenant_id+agent_name）+ `AgentDisableRow/Record` + store（base/sql/memory）+ `AgentDisableService`（TTL 缓存 `kill_switch_cache_ttl_s=5s`，镜像 TenantStatusService）+ 共享 `kill_switch.run_block_reason` 助手。**六个 run-spawn gate 全堵**（两轮独立 review 逮到真绕过后补齐）：①准入 runs.py ②`_resolve_session` agents.py ③`_is_killed` run_queue_worker（**整体裹 `_tenant_scope`**，否则 FORCE-RLS `tenant_config` 无 scope 读 0 行→gate 静默失效+毒缓存）④`fire_trigger` trigger_firing ⑤`resolve_approval_decision` **在 mark_decided CAS 之前**（覆盖 HTTP resume+批量 decide+超时 sweep，CAS 前→被堵审批留 PENDING 可逆）⑥`orphan_sweep._respawn`。**跨副本 bulk-cancel 关键更正 RT-ADR-17**：`abort_event` 是**进程内内存态非 DB 持久**——本地 run 走 `RunManager.cancel`，peer-owned run 走 `RunStore.request_cancel`（guarded `UPDATE...WHERE status IN (running,pending)`）→拥有副本下次 heartbeat CAS（status=running AND claimed_by=owner）失败→`_renew_lease` 置该 worker 的 abort_event→run 在 lease_ttl_s/3 内停（复用 9.4 HA 心跳，零热循环改动）。租户 suspend bulk-cancel 分页 RUNNING+PENDING 上限 5000。CodeQL log-injection（tenants.py 日志去 request 派生 tenant_id）修复后合。195 测独立通过。
  - [x] **PR-2 前端（#930 已合）**：AgentDetail disable/enable danger 按钮（`AgentKillSwitch`，复用 SettingsTenants Popconfirm + 可选 reason TextArea，空白 reason 传 undefined）接入 PageHeader actions + 红色「已禁用」状态标识 tag（reason tooltip）+ i18n 双语。**最小后端读路径补齐**：`GET /v1/agents/{name}/{version}` 详情折入 `disabled`+`disable`（PR-1 只暴露写+闸门，list/detail 均无 disabled 态，前端无法渲染初始标识）。权限=manifest:write，按钮恒显（不按 isSystemAdmin 隐藏，会误锁租户管理员），只读成员点击得 403 toast。code-review 无 CRITICAL/HIGH，修 i18next 复数键脆弱性 + 补空白-reason 测试。测试:后端 test_get_detail_surfaces_disabled_state + 前端 AgentKillSwitch.test 5 例;全量 vitest 604 绿。

### Wave 3（并行，依赖 RT-1）
- [x] **RT-5 生产质量监控**（5 PR + ★5 live 已实证 2026-07-06）（设计 [STREAM-RT-DESIGN](./streams/STREAM-RT-DESIGN.md) §13 / RT-ADR-22~26）：**★5 LIVE 实证达成**——`verify_live_quality.py` 两相全 PASS：Phase 1 真流量采样(sampling=100%)→ RT-1 判分(judge=deepseek-v4-pro,overall=5)→ 落库 + `quality_sampling` aux 计量可见;Phase 2 seed 陈年 baseline(5)/近窗低分(2)→ drift worker 检出跌 60%(n=12/12)→ 告警落库。IM 真送达那跳未验(未设 bot URL),但 emit/入队走全 webhook 共用 fan_out_event+投递循环(HX-9 已证复用),非 RT-5 新代码。**live run 暴露 2 坑(均已修/记)**：①**compose env 透传 gap #941**——PR-1/2/3 加 quality settings 但 `infra/docker-compose.yml` control-plane environment 逐个显式透传无 blanket env_file,漏 quality 组致 infra/.env 的 `HELIX_AGENT_QUALITY_*` 不进容器、worker 用默认 False 不起(两相皆 FAIL);补 13 行透传照 eval-worker 同款;②**judge 独立 provider**——judge 默认 anthropic/claude-haiku,与 agent 主对话 provider 两码事,用户无 anthropic key 致 `quality_judge.score_failed` CredentialsResolverError、run 全 success 仍 0 分;设 `HELIX_AGENT_QUALITY_JUDGE_PROVIDER/MODEL` 到有 key 的 provider 即通。**grounding 核实计划三处"复用"假设过期**——① `eval_engine_live` 跑合成数据集 + 确定性判分（safety_verdict/evaluate_trace），不采真流量、不调 LLM judge（仅脚手架可复用）；② `_judge.py` 直连 Anthropic + 正则抠单 int，**绕开 router、未接 RT-1**，RT-1 结构化输出在 router 侧（`router.py:384` output_schema + `structured_output.py`，消费方=aux_model_adapter/consolidator/distiller，**不含 _judge**）→ 判分走 RT-1-correct 的 router `output_schema` 路径不复用 _judge；③ webhook 全挂 run_event spine（`event_id={run_id}:{seq}`），`quality.drift` 非 run 事件 → 需新 emitter（run_id=None、自造 event_id），payload_format 渲染正交可复用。**决策**（产品意图不变，只改复用谁）：pull 式 `QualityMonitorWorker`+水位+确定性采样（脱 run 热路径，采样率+日上限护栏）→ `QualityJudge`（RT-1 router output_schema + 通用 rubric，不碰 _judge，surgical 独立 caller）→ 新 `quality_score` 时序表（RLS）→ `QualityDriftWorker` 滑窗基线偏离 + `quality_drift_alert` cooldown → `quality.drift` 新 emitter（4 同步点）；aux 计量 `usage_kind="quality_sampling"`（memory_consolidation 对称）；质量看板新页 + 平台/租户质量配置。诚实边界：抽样非全量／LLM 主观分非 ground-truth／漂移是统计信号不自动处置（`feedback_no_design_choice_disguise`）。
  - [x] **PR-0 设计（本次）**：RT-ADR-22~26 落 §13（三处复用假设勘误 + pull 采样 + RT-1 判分 + 时序落库 + 滑窗漂移 + 脱-spine emitter + 看板接线 + 诚实边界）。
  - [x] **PR-1 后端 采样+判分+落库+计量（本次）**：`QualityMonitorWorker`（pull loop-drain + 内存水位 + 确定性 hash(run_id) 采样 + per-tenant 日上限 + `exists()` 判分前去重）+ `QualityJudge`（RT-1 router `output_schema` + `_QualityVerdict` rubric，surgical 独立 caller 不碰 consolidator，平台凭据）+ `QualityScoreStore`/`QualityCandidateSource` 三件套 + `quality_score` 表（RLS）+ migration 0117 + aux 计量 `usage_kind="quality_sampling"`（memory_consolidation 对称，never-fail）+ 19 单测 + 4 真 PG（migration/RLS 隔离/ON CONFLICT 幂等/exists）。**实现级细化（批准设计内）**：①质量配置走 **settings 平台默认**（`quality_sampling_rate_pct`/`quality_daily_cap`/`quality_monitor_batch_size`/`quality_judge_model`），**`quality_config` 表 + 租户覆盖挪 PR-3**（跟 UI 一起，无 UI 的 override 表=死重）；②只采 `success` run（需 reply 可判）；③判分单元=thread 最后一轮交换（无 run→turn 映射，快速多轮 best-effort 归因，诚实边界）；④BYOK 已被 Y-1 移除→判分天然走平台凭据。**code-review 处置**：MEDIUM 加 `exists()` 判分前去重（防 updated_at 重 bump 重判/重计费）+ `batch_size` settings 化 + loop-drain（防高 run-rate 落后）；HIGH（`credentials_resolver` 作用域）核实为 test-only 且与 consolidator（app.py:1490/1507 同 sibling 作用域）完全同构、无生产注入 `agent_runtime`，非新 bug。
  - [x] **PR-2 后端 漂移+告警（本次）**：`QualityDriftWorker`（滑窗基线偏离：近窗均值 vs 基线窗均值，相对跌幅超阈 ∧ 双窗 min_samples；半开窗无缝隙；改进不告警；per-agent try/except 隔离；**advisory-lock 单飞整轮**防多副本重复告警）+ `QualityDriftAlertStore`（append 历史 + `latest_alert_at` cooldown + `list_alerts`）+ migration 0118（RLS）+ `QualityScoreStore` 补 `list_agents_with_scores_since`（跨租户 distinct，owner 豁免同 webhook）+ `window_stats`（SQL AVG/COUNT，免 list_scores limit 截断）。**`quality.drift` 脱 run_event spine**：抽 `fan_out_event` 模块函数（原 `_fan_out` 改委托，两 worker 共用 DRY）+ run_id=None + event_id=`quality_drift:{alert_id}`；投递复用现有 delivery 循环 + payload_format（feishu/钉钉/企微，`_im_text` 通用渲染 scalar 零模板改）。4 同步点（protocol Literal / api `_EVENT_TYPES` / `_EVENT_TITLES` / admin-ui `webhooks.ts`）。诚实边界：漂移是统计信号非因果、不自动处置。**code-review 处置**：APPROVE 无 CRITICAL/HIGH；两 MEDIUM 已修（per-agent 错误隔离 + advisory-lock 单飞）。测试：43 单测（协议/内存 store window_stats+list_agents/drift 算法边界:触发/cooldown/样本不足/无基线/改进不告警/阈下不告警/多租户 per-endpoint 域/emit 失败隔离 + webhook+monitor 回归）+ 7 真 PG（migration 0117+0118/RLS 隔离/window_stats/drift alert）。
  - [x] **PR-3 前端 质量看板（本次）**：后端薄读 API `api/quality.py`（`GET /v1/quality/scores` 趋势+低分下钻、`GET /v1/quality/drift-alerts`；home-tenant RLS，raw payload 同 eval_runs；stores 无条件挂 `app.state`，脱 `enable_quality_monitor` gate——读存量分与 worker 是否运行解耦）+ 新页 `SettingsQuality.tsx`（漂移告警表 / per-agent 趋势=依赖-free 内联 SVG sparkline+均值 / 最低分 run 表下钻链 `/runs/:threadId/:runId`；诚实边界 note 置顶）+ SDK `api/quality.ts`（raw，非 envelope）+ 全套接线（router `/settings/quality` / navModel tenant-settings 组 / CommandPalette `go-quality` / i18n 双语 `nav.quality`+`quality_page.*`）+ Storybook（Populated/Empty）。`WebhooksList` 的 `quality.drift` 已于 PR-2 加。测试：6 后端 API 单测（趋势 newest-first+drill 字段/agent 过滤/window 截断/租户隔离/漂移 newest-first）+ 4 前端页测（三段渲染/drill 链 href/空态/错误）+ full vitest 621 全绿 + tsc-b 0 + storybook build 0。**范围决策（本次用户拍板，`feedback_surface_requirement_changes`）**：配置 UI（平台+租户 override + `quality_config` 表/service）**单开后续 PR**——PR-1 已延后的 config 表地基不在，捆入会把「前端」PR 撑成两个功能；配置现走 settings 平台 env，看板（RT-5 真卖点『租户可见质量看板』）先出可见性。
  - [ ] **PR-3b 后端 质量配置（设计 §14）**：`platform_quality_config` 单行 singleton 表(tenant-less bypass RLS,照 `platform_judge_config`)+ 三件套 + migration 0119 + `EffectiveQualityConfig` 解析器/loader + `GET/PUT /v1/platform-quality-config`(system_admin+审计)+ **worker 重构**(常起去 env 门控 + per-cycle 读 config + judge provider/model per-call)+ app.py 接线 + env `enable_quality_monitor` 默认翻 true(降级 deploy 硬off 兜底)+ worker 单测改造 + 真 PG。**决策(用户拍板)**:enable=worker 常起+config `enabled`(有效=env AND config.enabled,env 硬off/config UI 开关默认 false opt-in);v1 只平台级(租户覆盖 backlog)。**迁移代价**:现有 env=true 栈无 config row→enabled 默认 false→转空转,UI 翻一下恢复。
  - [ ] **PR-3c 前端 平台质量配置 UI**：`PlatformQualitySection.tsx`(挂 `SettingsPlatformConfig`,照 `PlatformJudgeSection`)+ `api/platform_quality_config.ts` + i18n 双语 + Storybook + tsc-b/vitest。
  - [x] **PR-4 live E2E harness（#940，★5 live 已实证 2026-07-06）**：`tools/eval/verify_live_quality.py`（照 `verify_live_*` 家族惯例：keyless，key 服务端；读 `HELIX_API_URL`/`HELIX_API_TOKEN`）+ `manifests/quality-test/v1.0.0.yaml`（纯对话 agent）。**两相**：① Phase 1 纯 API——驱动真实 agent 若干 run → 采样(需 sampling=100%)→ judge → 落库,断言 `GET /v1/quality/scores` 有分 + `quality_sampling` aux kind 可见(`/v1/usage/tokens`);② Phase 2 注入劣化——drift 是时序信号,API 无法造陈年 baseline,故经 `HELIX_DB_DSN` **直 seed** `quality_score`(陈年 baseline 高分 + 近窗低分,合成 agent)→ 注册 `quality.drift` webhook 指向 IM bot → 待 drift worker 检出 → 断言 `GET /v1/quality/drift-alerts` 有告警;IM 送达眼看 bot(无 delivery 读 API)。**诚实边界**:baseline/recent 分层 DB-seed 非 live 对话(回填数天分不可能经运行系统),被测 RT-5 代码(window stats→drift→alert 落库→脱-spine emit)真跑,仅陈年输入序列合成;Phase 1 覆盖真流量 sample+judge。ruff/format 过(tools/eval 非 mypy-gated,同 egress harness bar)。**live run 前置**(用户手动,infra/.env):`HELIX_AGENT_ENABLE_QUALITY_MONITOR=true`+`QUALITY_SAMPLING_RATE_PCT=100`+短 `MONITOR_INTERVAL_S`/`DRIFT_INTERVAL_S`+`DRIFT_MIN_SAMPLES<=seed`;`export HELIX_API_URL/HELIX_API_TOKEN/HELIX_DB_DSN/HELIX_IM_WEBHOOK_URL`;`uv run python tools/eval/verify_live_quality.py`——退出码 0 即 RT-5 ★5 live 章达成。**2026-07-06 两相全 PASS,RT-5 ★5 已勾**(live 暴露 compose env 透传 gap #941 + judge 独立 provider 需配,详见父项)。
- [x] **RT-6 审批工件绑定（全交付 #932+#933+#934+PR-2）**（设计 [STREAM-RT-DESIGN](./streams/STREAM-RT-DESIGN.md) §12 / RT-ADR-19~21）：**grounding 核实计划前提部分过期——args 本就被 checkpoint 冻结**（`apply_resume_decision` approve 原样派发 checkpointed tool_calls，exec_python/http 全参内联），无漂移路径；真威胁面 = **bash 引用的 workspace 文件内容可变**（跨 run/副本按 user 共享，并发 run 可 approve-then-swap-script）。决策（用户拍板，约束=不削弱 Agent 能力+不增操作复杂度）：**Tier A args canonical 指纹硬拦**（RT-ADR-19，digest 存审批行=独立完整性域，防 checkpoint 篡改；零假阳故拦得起）+ **Tier B bash workspace 写代际察觉**（RT-ADR-20，审计-only 不拦，闭合问责半，零摩擦）；modify 重铸绑定原子写（RT-ADR-21）。文件层硬拦破约束→per-manifest opt-in backlog（[memory:audit-over-blocking]）。
  - [x] **PR-0 设计（#932 已合）**：RT-ADR-19~21 落 §12（威胁模型勘误 + Tier A 硬绑规格 + Tier B 审计信号 + modify 重铸 + migration + UI 面）。
  - [x] **PR-1 后端 Tier A（本次）**：canonical 指纹助手落 `helix-protocol`（两服务共享，避跨服务 import）+ `ApprovalRequest/Record.binding_digest` 字段 + mint 算指纹（`build_approval_request`）+ exec 前校验硬拒（`apply_resume_decision` 加 `binding_drift` outcome，`builder.py:1013` 发 `APPROVAL_BINDING_DRIFT` 审计 + terminal veto）+ resume 端点透 digest（`resolve_approval_decision`→`approval_resume`）+ modify 重铸原子写（`mark_decided` 加 `binding_digest` 参数，搭 CAS）+ migration `0115_approval_binding`（nullable，真 PG 验）+ 单测（protocol/gate/store/resume/真 PG round-trip）。**Tier B 拆到 PR-1b**（涉 workspace 写代际=不同子系统+机制待定，避巨 PR 难审）。
  - [x] **PR-1b 后端 Tier B（本次）**：定案 = `user_workspace.last_write_at TIMESTAMPTZ`（migration 0116，该表无 RLS）——两候选（复用 TE-2 审计 COUNT／独立 gen 计数器）实现期都否（`AuditQuery` 无 user scope／接线偏重），`requested_at` 已做 mint 基线故免 mint-read+免 agent_approval 加列。写路径 `PgWorkspaceLock.acquire`（write_file/edit_file/bash 三者共用锁）成功写后**独立 best-effort txn** bump（不入 advisory-lock txn，表缺/错绝不破坏 exclusion）；`resolve_approval_decision` 判 `policy_gate`∧有 user∧`last_write_at>requested_at`→APPROVAL_DECIDED 审计 `workspace_drift` 标（不拦）。诚实：`NullWorkspaceLock`（单进程）不 bump→drift False，跨副本威胁面对齐。单测（drift 真/假/无 store）+真 PG（bump 验证 + migration 0116 + 无 schema lock 仍绿）。前端徽标并入 PR-2。
  - [x] **PR-2 前端（本次）**：**后端小补** = GET run-detail pending 面加 `binding_digest`(Tier A receipt)+ `workspace_drift`(Tier B,GET-time 算——resume 审计只在决策时太晚,pending 卡片要实时算;抽 `_workspace_drift` helper 供 resolve+GET 复用,try/except 永不拦)。**ApprovalCard**:workspace_drift 警告 Alert(决策前警示)+ binding_digest 短指纹 receipt(Tooltip)。**IM webhook**:approval.requested payload 补 action_summary/reason_kind/binding 短指纹(`_im_text` 通用渲染 scalar 字段自动进卡片,零模板改;binding None 时跳行)。i18n 双语(binding/binding_hint/workspace_drift_title/body)。测试:ApprovalCard 4(binding receipt 有/无 + drift 警告 有/无)+ get_run pending binding/drift 2;前端 tsc-b/vitest 617 全绿。**binding_drift 硬拒态砍**:终态 reject message「[approval binding drift]」已在 run 事件流可见,无需 ApprovalCard 特殊 UI(卡片只在 PENDING 显)。**ApprovalsList drift 标识砍**:列表 drift 需 per-item N 次 workspace 读,粒度/成本不划算,pending 卡片实时警示已够。

### Wave 4
- [ ] **RT-7 成本感知模型路由**（~1.5 周，4 PR）：任务档位路由（强推理/常规/轻量）+ strict 来源契约（显式指定不降档）+ 沿用 Y-MK failover + PlatformRoutingSection 平台配置；eval 回归护质量

### 收口
- [ ] **对标矩阵重打分**：每 wave 收口重打对应格；全部完成 helix 预期 109→~122/129（≈95%），压制格清零

## 计划使用建议

1. **作为活文档维护**：每完成一个 Stream，在对应 checklist 打勾；遇到偏差修订估时与依赖
2. **每月节点回顾**：对照 Phase Exit Criteria 看是否要砍范围或引入帮手
3. **gate 不可越过**：M0 Exit / M1 Exit 必须全勾才进下阶段，否则会带技术债指数级增长
4. **优先级冲突时的取舍顺序**：安全（C/D Stream） > 可观测（A.4/A.5 + I Stream）> 性能 > DX > 业务功能扩展
