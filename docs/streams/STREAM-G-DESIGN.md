# Stream G — SRE + Eval + Feedback（设计先行）

> 落实 [docs/ITERATION-PLAN.md](../ITERATION-PLAN.md) § Stream G。
> 执行 [architecture/subsystems/20-observability](../architecture/subsystems/20-observability.md)
> 与 [architecture/subsystems/26-eval-framework](../architecture/subsystems/26-eval-framework.md)
> 的 M0 子集；落 24 P0 中的 **#13（SLO/SLI）、#14（告警）、#26（runbook）、#18（event_log 归档）、
> #36（Eval 框架）、#37（用户反馈）、#38（Eval 数据集）**。
>
> 本 Stream **不**重做可观测三大支柱的 SDK 层 —— 结构化日志（A.7）、W3C trace + OTel SDK（A.8）、
> Prometheus metric 注册表（A.9）、health 探针（A.11）、Langfuse 中间件（E.5）在 Stream A/E 已建好。
> G 在它们之上拼出"可运维 + 可评测 + 可收集反馈"的产品级骨架。

设计先行规则（[memory:feedback_design_first_iteration.md](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_design_first_iteration.md)）：
所有架构 / 接口 / Mini-ADR 必须在编码前锁定，G.0 – G.8 的 PR 仅执行本文档。

---

## 1. 范围 & 边界

### 1.0 M0 scope 调和（先读 —— Mini-ADR G-1）

`subsystems/20 § 9` 把**完整 SLO + 错误预算 + 告警体系**列为 **M1**，`subsystems/26 Eval` 在子系统索引里整体标 **M1**；
而 ITERATION-PLAN 把 G.1–G.8 列进 **M0**（"全 24 P0 **骨架**到位"、G.4"**先简版**"）。
两者不矛盾 —— **M0 Stream G = 各 P0 的骨架 / 简版**，M1 把骨架升级为生产级自动化。判定见 Mini-ADR G-1；
每个子项的 In-scope 表都明确标注"M0 做什么、什么推 M1"。

### 1.1 In-scope（G.0 – G.8）

> G.0 为本 Stream 推进识别的前置新增（ITERATION-PLAN 原列 G.1 – G.8）：G.2 告警、G.7 大盘都需要可观测后端栈先就位。

| 子项 | M0 实现内容 | 推 M1 的部分 | 关联 |
|------|------------|-------------|------|
| **G.0 可观测后端栈** | `infra/docker-compose.yml` 加 `observability` profile：OTel Collector + Prometheus + Tempo + Loki + Grafana + Alertmanager 自托管；Prometheus scrape config（抓 helix 服务 `/metrics`）；Grafana datasource 自动注入（Prometheus/Tempo/Loki）。 | 生产部署（K8s）、HA、远端存储 | subsystem 20 § 2；Mini-ADR G-2 |
| **G.1 SLO/SLI 定义** | `docs/architecture/subsystems/20` § 5.4 SLO 表落成 `docs/runbooks/slo.md`（5 条 M0 SLO + 测量 PromQL）；Prometheus recording rule（`tools/observability/rules/sli.yml`）预聚合 SLI。 | `slo_definition` 入库、burn-rate 自动算、错误预算耗尽自动冻结发布 | P0 #13；subsystem 20 § 5.4 |
| **G.2 告警体系** | Prometheus alert rule（`tools/observability/rules/alerts.yml`）：5xx 率、Postgres 连接耗尽、`helix_network_egress_meta_attempt_total > 0` 等 M0 关键条件；Alertmanager config P0/P1/P2 分级路由骨架（webhook receiver 占位，飞书/PagerDuty URL 由 env 注入）。 | PagerDuty 实接、飞书实接、错误预算 burn-rate 告警、告警风暴抑制调优 | P0 #14；subsystem 20 § 5.7 |
| **G.3 故障预案 runbook** | `docs/runbooks/`：control-plane / postgres / anthropic（LLM provider）/ sandbox / credential-proxy 各 1 篇（故障现象、诊断、处置、回滚）。control-plane 一篇因 G.2 的告警 `runbook_url` 均指向它而补入（原草图列 4 篇，实为 5）。 | 每个告警 1 篇 SOP 全覆盖（M1-H） | P0 #26 |
| **G.4 Eval 框架** | promptfoo 简版：`tools/eval/promptfooconfig.yaml` + 1 个 eval set；`uv run` 包装脚本本地可跑；CI 加非 gating job。 | LLM-as-judge gate、A/B、PR 前异步触发、regression 阻断 | P0 #36；subsystem 26 |
| **G.5 Eval 数据集管理** | `tools/eval/datasets/`：golden / regression set 目录结构 + YAML 格式约定 + README；git 版本化。 | 数据集自动晋升、从反馈回流 candidate case | P0 #38；subsystem 26 |
| **G.6 用户反馈收集** | 新 `feedback` 表（migration）+ control-plane `POST /v1/sessions/{thread_id}/feedback` API（👍/👎 + 文本，关联 thread + turn seq + trace_id）；写入有 audit。 | 反馈 → candidate case → 人工审核 → golden set 回流；反馈大盘 | P0 #37；subsystem 26 |
| **G.7 Grafana 大盘** | `tools/observability/dashboards/`：`01-overview` / `02-orchestrator` / `03-sandbox` 三份 Grafana JSON（subsystem 20 § 9 M0）；Grafana 自动 provision。 | `04-llm-gateway` / `05-control-plane` / `06-tenant` / `07-slo` 共 6+ 份（M1） | subsystem 20 § 5.6 / § 9 |
| **G.8 event_log 冷归档** | 新 `event-log-archive-job` 服务（复用 `retention-cleanup-job` / `audit-backup-worker` 的 job 模式）：扫超龄 `event_log` 行 → 批量写 S3（`ObjectStore` 抽象，A.5）→ 确认后删行；归档清单可查。 | 归档后透明查询路径（查询层自动回 S3 取）、分区 | P0 #18；subsystem 20 |

### 1.2 Out-of-scope（明确推迟）

| 推迟项 | 落地 | 备注 |
|-------|------|------|
| 完整 SLO 入库 + 错误预算自动冻结发布 | M1-E | subsystem 20 § 9 M1 |
| 6+ Grafana dashboard 全量 + 单租户视图 | M1-E | M0 只 3 份 |
| Alertmanager 实接 PagerDuty / 飞书 | M1-H | M0 留 webhook receiver 占位 + env 注入点；需真实账号/webhook |
| LLM-as-judge Eval gate + A/B + regression 阻断 | M2-D | subsystem 26；M0 只 promptfoo 简版 |
| 用户反馈 → golden set 自动回流 pipeline | M2-D | M0 只落表 + capture API |
| 归档后 event_log 透明查询路径 | M1-B | M0 归档 + 可查清单；查询层回 S3 取留 M1 |
| Langfuse 后端实接（agent 链路可视化） | M2-E | E.5 仍是 in-memory recording client |
| Sentry / GlitchTip 异常追踪、py-spy 火焰图 | M1-H / M2 | subsystem 20 § 1 ❌ |
| redactor 中间件正式上线 + CI PII 扫描 | M1-E | A.7 redactor 接口已在；CI 扫描留 M1 |

### 1.3 验收（G Exit）

1. `docker compose --profile observability up` 拉起后端栈；Prometheus 抓到 helix 服务 metric、Tempo 收到 span、Loki 收到日志、Grafana 三份大盘有数据。
2. `promtool check rules` 校验 SLI recording rule + alert rule 通过；`amtool check-config` 校验 Alertmanager config 通过。
3. `feedback` API：提交 👍/👎 + 文本 → 落 `feedback` 表、关联 trace_id、写 audit；跨租户隔离（RLS）。
4. `event-log-archive-job` 把超龄行写 S3 并删表行，集成测试覆盖。
5. promptfoo 简版本地 `uv run` 可跑通 1 个 eval set。
6. runbook 4 篇齐、SLO 定义文档齐。
7. ITERATION-PLAN G checklist 全勾；CI 全绿。

---

## 2. 架构

### 2.1 可观测数据流（G.0 落地）

```
helix 服务（control-plane / orchestrator(库) / sandbox-supervisor / credential-proxy）
  │  metric: prometheus_client → /metrics                       （A.9 已建）
  │  trace : OTel SDK → OTLP                                     （A.8 已建）
  │  log   : stdout JSON（HelixJsonFormatter）                   （A.7 已建）
  ▼
OTel Collector ──▶ Tempo（trace）
Prometheus ──scrape /metrics──▶ Prometheus TSDB
Promtail / Collector ──▶ Loki（log）
        │
        ▼
     Grafana（Prometheus + Tempo + Loki 三 datasource，自动 provision）
        │
        ▼
  Alertmanager ◀── Prometheus alert rules ──▶ webhook receiver（飞书/PagerDuty 占位）
```

G.0 只把上图后端用 compose `observability` profile 拉起；SDK 侧（A.7/A.8/A.9）零改动 —— 已 emit，缺的只是"收的人"。

### 2.2 子项依赖与 PR 顺序

```
G.0（后端栈）── 前置 ──┬─▶ G.1（SLI recording rule，需 Prometheus）
                       ├─▶ G.2（alert rule + Alertmanager，需 Prometheus）
                       └─▶ G.7（Grafana 大盘，需 Grafana + 三 datasource）

G.3 / G.4 / G.5 / G.6 / G.8 —— 不依赖 G.0，可独立并行
```

PR 顺序：**G.0 → G.1 → G.2 → G.7**（可观测链路一条线收口）→ **G.3 → G.6 → G.8 → G.4 → G.5**（独立项，按 docs→code→eval 排）。

### 2.3 G.6 反馈表 schema

```sql
CREATE TABLE feedback (
    id          BIGSERIAL PRIMARY KEY,
    tenant_id   UUID NOT NULL,
    thread_id   UUID NOT NULL,             -- 关联 session/thread
    turn_seq    BIGINT,                    -- event_log.seq —— 指向被评价的 turn；NULL = 整会话
    trace_id    TEXT,                      -- W3C，关联 trace（查询时跳 Tempo）
    rating      TEXT NOT NULL,             -- 'up' | 'down'
    comment     TEXT,                      -- 可选自由文本
    actor_id    TEXT NOT NULL,             -- 谁提交的
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 索引：(tenant_id, thread_id)；(tenant_id, created_at)
-- RLS：与 event_log / audit_log 同 tenant 隔离策略（C.4）
```

`turn_seq` 关联 `event_log.seq`（同 `thread_id` 下的序号），不加外键（event_log 会被 G.8 归档删行 —— 外键会挡归档）。

### 2.4 G.8 归档 job

复用已确立的 job 服务模式（`services/retention-cleanup-job` D.3、`services/audit-backup-worker` D.1c）：
新 `services/event-log-archive-job`，一次性 / cron 触发：

1. 选 `event_log` 中 `created_at < now() - 归档阈值`（M0 默认 180 天，env 可配）的行，按 `thread_id` 批。
2. 序列化为 JSONL，`ObjectStore.put(key=event-log/{year}/{month}/{thread_id}.jsonl)`（A.5 抽象，dev=MinIO / prod=OSS）。
3. `put` 确认成功后 `DELETE` 该批行（先归档后删 —— 失败可重跑、不丢数据）。
4. 写一条 `archive_manifest`（或 audit）记录归档键 + 行数 + 时间范围，作为"归档后可查清单"。

> 失败模式：归档中途崩 → 已 `put` 未 `DELETE` 的批下次重跑 `put` 覆盖同 key（幂等）、再删；不会丢、最多重传一批。

---

## 3. Mini-ADR

### Mini-ADR G-1：M0 Stream G = SRE/Eval/Feedback 骨架，生产级自动化推 M1

- **背景**：`subsystems/20 § 9` 把完整 SLO/错误预算/告警体系列 M1；`subsystems/26 Eval` 子系统索引整体标 M1；ITERATION-PLAN 却把 G.1–G.8 列进 M0。
- **冲突点**：照搬 subsystems 文档 → Stream G 在 M0 几乎空；照搬 ITERATION-PLAN 字面 → M0 要做 M1 量级的告警/eval 体系。
- **决策**：M0 Stream G 做**每个 P0 的骨架 / 简版** —— 结构立起来、接口定下来、dogfood 能用；**生产级自动化**（错误预算自动冻结、LLM-as-judge gate、PagerDuty 实接、反馈自动回流、6+ 大盘）推 M1。ITERATION-PLAN 的"全 24 P0 **骨架**到位"、G.4"**先简版**"本就是此意；subsystems 文档的 M0/M1 分界是同一判定的另一种表述。
- **代价**：M0 的 SLO 是文档 + recording rule 不是自动冻结引擎、告警能触发但 receiver 是占位、eval 能跑但不阻断 —— 这些在每个子项 In-scope 表显式标注，避免"以为做完了"。M1-E/M1-H/M2-D 承接升级。

### Mini-ADR G-2：可观测后端栈用 compose `observability` profile 自托管

- **背景**：A.7/A.8/A.9 让 helix 服务已 emit log/trace/metric，但 compose 里没有"收的人"（Explore 确认 compose 仅数据层 + helix 服务）。
- **决策**：`infra/docker-compose.yml` 加 `observability` profile：OTel Collector + Prometheus + Tempo + Loki + Grafana + Alertmanager（均自托管社区镜像，不自建）。与现有 `proxy`/`auth`/`sandbox`/`full`/`e2e` profile 模式一致 —— 默认 `up` 不拉、`--profile observability up` 才拉。
- **理由**：(1) 自托管符合 ADR-0005（国内 LangSmith 不可用）。(2) profile 隔离 —— 数据层集成测试默认 `up` 不受可观测栈拖慢。(3) 全部社区官方镜像，无供应链新增面。
- **代价**：6 个新容器，本地 dev 资源占用上升 —— 故独立 profile，按需启。生产部署（K8s/HA/远端存储）推 M1。

### Mini-ADR G-3：Eval 用 promptfoo，M0 只简版

- **背景**：ITERATION-PLAN G.4 已点名 promptfoo；subsystem 26 设计的是完整 Eval 框架（EvalSet/EvalRun 状态机/LLM-as-judge/A-B gate）。
- **决策**：M0 落 promptfoo **简版** —— `tools/eval/promptfooconfig.yaml` + 1 个 eval set + `uv run` 包装脚本 + CI 非 gating job。完整框架（DB 状态机、LLM-as-judge、PR 前异步触发、regression 阻断）推 M2-D。
- **理由**：promptfoo 是成熟工具，简版即可让 dogfood 前有回归护栏；自建完整 EvalRun 状态机在 M0 是过度投入（subsystem 26 本就标 M1+）。
- **代价**：M0 eval 不阻断合并、不入库 —— 接受；先有"能跑的回归集"比"完整但晚"更重要。

### Mini-ADR G-4：event_log 冷归档新建独立 job 服务，不并入 retention-cleanup-job

- **背景**：`retention-cleanup-job`（D.3）做 TTL **删除**；event_log 冷归档是**先归档 S3 再删**，语义不同（涉及 ObjectStore、幂等重传、归档清单）。
- **决策**：新建 `services/event-log-archive-job`，复用 job 服务的工程模式（pyproject / 镜像 / 测试结构），但独立代码 —— 不塞进 retention-cleanup-job。
- **理由**：(1) 职责单一 —— 删除 vs 归档是两件事，混在一个 job 里两套失败模式纠缠。(2) 归档要接 `ObjectStore`，retention job 不需要 —— 依赖面不同。(3) 与 `audit-backup-worker`（也是"备份到 S3"语义）并列更自然。
- **代价**：多一个 job 服务骨架；可接受 —— job 服务模式已成熟、复制成本低。

### Mini-ADR G-5：用户反馈 capture API 在 control-plane，UI 留 Stream H

- **背景**：P0 #37"👍/👎 关联 turn + trace"。反馈有"前端按钮"和"后端落库"两面。
- **决策**：G.6 只做**后端** —— `feedback` 表 + control-plane `POST /v1/sessions/{thread_id}/feedback`。👍/👎 按钮在 Admin UI，属 **Stream H**（H.3 session 时间线）。
- **理由**：control-plane 已是 session/thread API 的归属服务；反馈天然挂在 session 资源下。后端先行 → Stream H 直接接 API。
- **代价**：M0 结束时反馈只能经 API 提交（curl / 测试），没按钮 —— 可接受，UI 是 Stream H 既定范围。

---

## 4. Verification

| 验证 | 手段 |
|------|------|
| G.0 后端栈 | `docker compose --profile observability up` → Prometheus targets 全 up、Grafana 三 datasource 连通、三大盘有数据 |
| G.1/G.2 规则 | `promtool check rules tools/observability/rules/*.yml` + `amtool check-config` CI 校验 |
| G.6 反馈 API | unit（schema/校验）+ integration（落表 + RLS 跨租户隔离 + audit）|
| G.8 归档 job | integration：种超龄 event_log 行 → 跑 job → 断言 S3 有对象、表行已删、清单已写；崩溃重跑幂等 |
| G.4 Eval | `uv run` 包装脚本本地跑通 1 个 eval set |
| G.3/G.5/文档 | 评审 + 链接有效性 |
| CI | 8/8；新增 promptfoo 非 gating job |

### 测试矩阵（接续 Stream I，I 收于 #60）

| # | 用例 | 子项 | 层级 | 说明 |
|---|------|------|------|------|
| 61 | 可观测后端栈拉起 | G.0 | integration/smoke | `--profile observability up` → Prometheus 抓到 ≥1 helix target、Grafana datasource 健康 |
| 62 | SLI/alert 规则合法 | G.1/G.2 | unit | `promtool check rules` + `amtool check-config` 通过 |
| 63 | 反馈落表 + 关联 | G.6 | integration | `POST /feedback` 👍/👎+文本 → `feedback` 行含 trace_id/turn_seq；写 audit |
| 64 | 反馈跨租户隔离 | G.6 | integration | 租户 A 的反馈对租户 B 不可见（RLS）|
| 65 | 反馈输入校验 | G.6 | unit | `rating` 非 up/down → 422；空 body → 422 |
| 66 | event_log 归档 + 删行 | G.8 | integration | 超龄行 → S3 有 JSONL 对象、表行删、清单写 |
| 67 | 归档 job 幂等重跑 | G.8 | integration | `put` 后模拟崩溃，重跑 → 不重复、不丢、最终一致 |
| 68 | Eval 简版可跑 | G.4 | integration | promptfoo 包装脚本跑通 1 个 eval set，产出报告 |

---

## 5. PR 顺序

| PR | 内容 | 验证 |
|----|------|------|
| **G.0** | compose `observability` profile（6 后端）+ Prometheus scrape + Grafana provision | #61 |
| **G.1** | SLO 定义文档（`docs/runbooks/slo.md`）+ SLI recording rule | #62（部分）|
| **G.2** | alert rule + Alertmanager P0/P1/P2 路由 config | #62 |
| **G.7** | 3 份 Grafana dashboard JSON + provision | #61（大盘有数据）|
| **G.3** | 4 篇 runbook（`docs/runbooks/`）| 评审 |
| **G.6** | `feedback` 表 migration + control-plane capture API | #63 #64 #65 |
| **G.8** | `event-log-archive-job` 服务 + 归档逻辑 | #66 #67 |
| **G.4** | promptfoo 简版 config + eval set + CI job | #68 |
| **G.5** | eval 数据集目录结构 + 格式约定 + README | 评审 |

> G.1/G.2/G.7 依赖 G.0；G.3–G.8 互不依赖，顺序可调。每个子项一个 PR、零债收尾。

---

## 6. 失败模式（Stream 级）

| 失败场景 | 处理 |
|---------|------|
| 可观测后端栈拖慢本地 dev | 独立 `observability` profile，默认 `up` 不拉（Mini-ADR G-2）|
| OTel Collector 不可达 | SDK 侧 A.8 已配 retry buffer；G.0 不改 SDK |
| 归档 job 中途崩 | 先 `put` 后 `DELETE`，幂等重传（Mini-ADR G-4 / § 2.4）|
| 反馈 API 被跨租户滥用 | RLS + RBAC `session:read` 同源校验（测试 #64）|
| 告警 receiver 占位未实接 | M0 显式标注（Mini-ADR G-1）；config 校验通过即可，实接 M1-H |
