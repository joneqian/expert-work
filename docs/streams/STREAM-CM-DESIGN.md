# Stream CM — 上下文与记忆管理强化（设计先行）

> **背景**：2026-06-09 基于系列文章《Harness 工程》对 OpenClaw / deer-flow / Hermes 做了上下文与记忆管理的源码级对比（`docs/research/2026-06-09-context-memory-management-comparison.md`），并对 helix 现状做了 10 维取证 + 2024–2026 论文/工程博客二次评估（`docs/research/2026-06-09-helix-context-memory-improvement-framework.md`，v2 含外部证据）。本 Stream 把框架报告里确认的改进条目落成可执行设计。
>
> **总基调（已拍板）**：**② 混合——DB 为真相源 + workspace 文件投影**，用**单向错时双流**实现（turn 末 DB→file 投影 / turn 始 file→DB 受控 ingest），**不做对称双向同步**（外部证据判为反模式）。
>
> **设计先行规则**（[memory:design-first-iteration]）：所有总体架构 / 跨切面接口 / Mini-ADR 在本文件锁定；每条 gap PR 在对应章节基础上做局部细化。
>
> **零债收尾规则**（[memory:zero-tech-debt]）：每条交付收尾 6 条全过 —— 无 TODO / 测试达标 / 文档同步 / 可观测齐全 / CI 全绿 / bug 不遗留。
>
> **本文件状态**：CM-0（地基，§2）+ CM-1（运行时 error-as-guidance，§3）详设已锁定；CM-2…CM-9 / CM-N 列入范围表，待各自 PR 时在本文件细化。

---

## 1. 范围 & 边界

### 1.1 In-scope（映射框架报告 A/B/C/N 档）

| ID | 框架条目 | Gap | 交付 | 优先级 | Mini-ADR |
|----|---------|-----|------|--------|---------|
| **CM-0** | C0 + N1 | 状态内化、无文件投影；workspace 卷已有但状态不可见、不可手改 | 状态↔workspace 文件投影 + 单向错时双流 + recitation 复诵 | **先行** | CM-A1…A8（本文详设） |
| CM-1 | A1 | 运行时主循环工具失败无 grounded 恢复建议（SE-12 是离线 skill 进化，L-4 只覆盖文件 mutation） | 通用工具失败→结构化恢复 advisory 注入主循环 | P0 | CM-B1…B6（§3 详设） |
| CM-2 | A2 | 无"保留最近 N 轮"廉价前置闸，轻溢出每次走 LLM 摘要 | `agent_node` compressor 前加滑窗截断（保 ToolCall↔ToolResult 配对） | P0 | 待细化 |
| CM-3 | A3 | compressor 丢弃中段前无 flush，`memory_writeback` 只在 run 末 | 压缩前回调 → 复用 writeback 通道中途落盘 | P1 | 待细化 |
| CM-4 | B5 | rerank_provider/model 配置预留但检索路径无 rerank 调用 | memory recall Hybrid 召回后接 cross-encoder rerank | P1 | 待细化 |
| CM-5 | B6 | 超大工具结果 char-cap 截断丢弃，不可找回 | 超限结果存 artifact + 虚拟引用 + read 类豁免（"可恢复压缩"通用原则） | P1 | 待细化 |
| CM-6 | B4 | 对称 RRF，无 MMR 去冗余、无时间衰减 | `memory/sql.py:retrieve()` RRF 后加 MMR + 时间衰减 | P1 | 待细化 |
| CM-7 | B7 | `<context-summary>` 缺"背景非指令"强语义、无增量更新 | 结构化摘要条目 + 显式更新操作（A-MEM/Mem0 范式） | P2 | 待细化 |
| CM-8 | C8 | approval `decision='modify'` 无法编辑 `proposed_args` 再提交 | 文件投影（CM-0）+ admin UI plan/todo 可视化可编辑双通道 | P2 | 待细化 |
| CM-9 | C9 + N4 | 无 plan mode 开关、无 effort 控制、iteration budget 仅概念、无 loop 去重 | plan mode 开关 + adaptive thinking `effort` 档位 + iteration budget 真实现 + 调用指纹去重 | P2 | 待细化 |
| CM-N5 | N5 | 检索/记忆改动无回归基线 | LongMemEval + LoCoMo 自测纳入 eval（贯穿 CM-4/6/7） | 贯穿 | 待细化 |

### 1.2 设计选择对比（"为什么不全抄"）

| 外部 pattern | 借/不借 | 理由 |
|---|---|---|
| 文件作唯一真相源（OpenClaw bootstrap / Hermes MEMORY.md / Manus file-as-context） | ❌ 不借 | 与多租户查询/隔离/可检索冲突；helix 选 DB 权威（STREAM-J §8） |
| 对称双向 file↔DB 同步 / 最后写赢 | ❌ **反模式** | Oracle/同步工程共识：单向 master→target；对称双写需 trump rules，已知脆弱点 |
| 进程级文件锁 + PID watchdog（OpenClaw） | ❌ 不借 | helix 用 PG 事务 / advisory lock；并发已被 per-(tenant,user) 单 warm session 串行化 |
| **DB 权威 + 单向投影 + 受控 ingest**（Deep Agents CompositeBackend 思路） | ✅ 借 | 拿到文件透明/可干预红利，不丢 DB 权威；任一时刻单向流动 |
| **Recitation 复诵 todo 到上下文尾**（Manus/Claude Code） | ✅ 借 | 低成本抗 long-context lost-in-the-middle |
| 可恢复压缩"留引用不丢源"（Manus / Anthropic tool-clearing） | ✅ 借（CM-5） | helix 已有 artifact+ObjectStore 基建 |
| Dreaming / 按日期 Episodic 编年落盘 | ❌ 不借 | 文章理想化；consolidator(transient→consolidated) 已覆盖等价价值 |

### 1.3 Out-of-scope（明确推迟）

| 推迟项 | 落地 | 备注 |
|-------|------|------|
| 知识图谱记忆（Zep/Graphiti bi-temporal KG） | 待评估 | 仅当出现强时序/事实纠正需求；收益曾被高估（Zep 84%→58%） |
| A-MEM 全量 Zettelkasten 自组织 | 观望 | 研究原型；其"结构化 note + 显式操作"思想已被 CM-7 局部吸收 |
| 可学习压缩 token / 硬 prompt 压缩（LLMLingua-2） | 前沿引用 | 前者需训练；后者作"LLM 摘要"低成本替代候选，非首选 |
| 推理层 KV-cache 压缩（PyramidKV 等） | 不在范围 | 属推理层，与应用层上下文管理正交 |
| 每 turn 强制 file→DB ingest（侦测 run 中途手改） | CM-0 后评估 | 默认 ingest 只在 run 始 + resume（见 CM-A4），成本/收益自测后再决定是否每 turn |

### 1.4 验收（每条 CM-x 独立 Exit）

每条 CM-x 收尾零债 6 条全过；CM-0 的具体验收见 §2.10。

---

## 2. CM-0 详细设计 —— 状态↔workspace 文件投影 + 单向错时双流（地基）

### 2.1 关键约束（接缝核准结论，已源码核准）

| 约束 | 事实 | file:line |
|---|---|---|
| **orchestrator/supervisor 无卷写权** | workspace 是 docker named volume，仅挂进沙盒容器；supervisor 只能用一次性只读容器 `read_volume_file` 读卷，**无写卷 API** | `helix-runtime/.../runtime_provider.py:109-122`（`--volume {vol}:/workspace`）；`sandbox-supervisor/.../docker_client.py:129-157`（`read_volume_file`，`{volume}:/ws:ro`） |
| **唯一卷写通道 = warm sandbox** | `file_ops.write_file` 走 `run_in_sandbox`，在 warm session 内跑 Python snippet（temp+atomic replace），毫秒级 | `orchestrator/.../tools/file_ops.py:12-18`（设计注释明示放弃专用 supervisor 文件 API，改 warm session snippet） |
| **turn 边界** | turn 始 = `agent_node` 入口；turn 末 = `tools_node` 返回前 | `graph_builder/builder.py:212`（agent_node）/ `:507`（tools_node return） |
| **run 边界** | `memory_recall` 在 run 启动一次（entry chain）；`memory_writeback` 在 run 结束一次（exit chain） | `graph_builder/memory.py:187-219` / `:240-319`；`builder.py:517-533` |
| **plan 持久化** | `update_plan` 返 `ToolResult.state_updates={"plan": ...}`，经 `TOOL_ALLOWED_STATE_KEYS` 白名单 merge 进 checkpointer | `tools/update_plan.py:140-145`；`tools/registry.py:192-194`（白名单 `{"plan","subagent_invocations","promoted_tools"}`） |
| **PlanStep 无 status** | `PlanStep` 仅 `id`+`description`，**无完成态**——TODO.md 勾选需补字段 | `protocol/plan.py:15-21` |
| **artifact 范式可参考** | 内容留卷、元数据进 `artifact` 表（orchestrator 可直接写 DB）；下载经 supervisor 代理 | `orchestrator/.../tools/artifact.py:1-13`；`artifact/base.py:29-49` |
| **audit 接口** | `AuditAction` 枚举 + `audit_log.append(entry)`；已有 `WORKSPACE_*` 系列 | `protocol/audit.py:100-105`；`audit_log/base.py:36-42` |

> **结论**：投影 DB→file **必须经 warm sandbox `file_ops` 写**（orchestrator 直接写卷不可行）；ingest file→DB 经 supervisor `read_volume_file` 只读读卷 + orchestrator DB 事务写。这与 artifact"内容在卷、元数据在 DB"范式一致。

### 2.2 同步模型：单向错时双流（CM-A2）

任一时刻只有单向流动，DB 恒为真相源：

```
run 启动
  └─ memory_recall 后、首个 agent_node：file→DB 受控 ingest（一次）
       读 /workspace/PLAN.md·TODO.md（经 read_volume_file）
       → diff vs 上次投影快照 → 校验（schema/size/注入扫描）
       → 有合法变更 ⇒ DB 事务写 AgentState.plan（DB 权威）+ audit STATE_INGESTED

每个 turn（agent_node 入口）
  └─ N1 recitation：把当前 PLAN/TODO 摘要注入上下文尾部（纯 in-context，不读写文件）

每个 turn 末（tools_node 返回前）
  └─ DB→file 投影（only-if-changed）
       若 plan 本 turn 变更（hash 比对）⇒ 经 file_ops 写 /workspace/PLAN.md·TODO.md
       + audit STATE_PROJECTED；未变则跳过（避免无谓 sandbox 往返）

run 结束（memory_writeback 后）
  └─ MEMORY.md 投影（一次）：把 consolidated/top recalled 记忆投影为只读视图
```

**为什么 ingest 默认只在 run 始 + resume，而非每 turn**（CM-A4）：helix 的人机协同是 **approval-pause 驱动**（J.8），人最自然在 run PAUSED 或两次 run 之间手改文件。run 始 + resume-from-pause ingest 覆盖该场景且零每-turn 卷读成本。每-turn ingest 列入 §1.3 推迟项，自测成本/收益后再决定。

### 2.3 投影什么（path-addressable + compaction-stable 不变量）

| 文件 | 来源（DB 真相源） | 方向 | 备注 |
|---|---|---|---|
| `/workspace/PLAN.md` | `AgentState.plan`（goal + steps + status checkbox） | **双向（投影 + ingest）—— 唯一 ingest 源** | round-trippable：`render_plan_md`↔`parse_plan_md` 互逆；人改 goal/步骤/勾选 → ingest 回灌（CM-A5/A5b） |
| `/workspace/TODO.md` | plan steps + status | **单向只读投影** | 编辑被忽略（要改去改 PLAN.md）；保留为人友好的扁平 checklist 视图 |
| `/workspace/MEMORY.md` | `memory_item`（consolidated + top recalled） | **单向只读投影** | 不 ingest（记忆有独立写路径 memory_writeback + consolidator） |

> **设计细化（CM-A5b，对 §2.3 原"TODO.md 双向"的修正）**：原设计 PLAN.md + TODO.md 都可 ingest，双源回灌易冲突（人同时改两处谁赢）。收敛为 **PLAN.md 单一 ingest 源**——它带 status checkbox 可精确 round-trip，是唯一可编辑回灌的文件；TODO.md/MEMORY.md 只读。这消除双源歧义，且 parse 失败永远丢弃文件改动、DB 保持权威（CM-A8 安全）。

不变量：① **path-addressable**——固定路径，内容可随时从 DB 重建；② **compaction-stable**——投影文件不进 LLM 上下文（只 recitation 摘要进），故不受 compaction 影响、丢失可重建。

### 2.4 挂钩点（已核准位置）

| 钩子 | 位置 | 频率 | 可访问 |
|---|---|---|---|
| ingest（file→DB） | **entry-chain `workspace_ingest` 节点**（planner 后、agent 前），实现为独立节点而非 agent_node 入口判断——entry chain 每 ainvoke 跑一次 = run 始/resume，天然满足"一次"语义 | run 始一次 | state, config（tenant/user/run），返回 `{"plan": ...}` 回灌 |
| recitation（N1） | `agent_node` 内、plan 注入处附近（`builder.py:239-241`） | 每 turn | 当前 plan/todo，注入 messages 尾部 |
| 投影（DB→file） | `tools_node` 返回前（`builder.py:507`），only-if plan 变更 | 每 turn（条件触发） | state + accumulated_state（含本 turn plan 变更） |
| MEMORY.md 投影 | 同 tools_node 投影钩子（`recalled_memories` 在 state，only-if-changed 只写一次） | 随投影 | — |

投影/ingest 纯逻辑封装到 **`orchestrator/context/workspace_projection.py`**（`WorkspaceProjector` / `WorkspaceIngester`）；真沙盒读写适配（`SandboxWorkspaceWriter`/`SandboxWorkspaceReader`）在 `tools/file_ops.py`；ingest 节点在 `graph_builder/workspace_ingest.py`（`make_workspace_ingest_node`）。builder.py 仅在钩子点/entry chain 调用，保持节点函数瘦。

### 2.5 数据/协议变更

1. **`PlanStep` 加 status**（`protocol/plan.py`，CM-A5）：
   ```python
   PlanStepStatus = Literal["pending", "in_progress", "completed"]
   class PlanStep(BaseModel):
       id: str
       description: str
       status: PlanStepStatus = "pending"   # 新增，默认 pending 向后兼容
   ```
   - 无迁移：plan 存 AgentState（checkpointer dill），非 DB 表；旧 checkpoint 反序列化走默认值。
   - `update_plan` 工具支持设置 status；TODO.md 渲染 `- [x]/[ ]` 由 status 决定。

2. **`AgentState` 加投影游标**（`orchestrator/state.py`）：
   ```python
   last_projection_hash: NotRequired[str | None]   # 上次投影内容 hash，only-if-changed + ingest diff 基准
   ```
   覆写 reducer；用于 §2.2 的 hash 比对与 ingest diff。

3. **`AuditAction` 双处 Literal 同步加**（[memory:audit-literal-drift]——protocol + control-plane 两处都改）：
   - `STATE_PROJECTED = "state:projected"`、`STATE_INGESTED = "state:ingested"`。

4. **新模块**：`orchestrator/context/workspace_projection.py`（projector/ingester + 渲染/解析/校验纯函数，便于 unit 测）。

### 2.6 冲突与校验（DB 权威）

- **ingest 校验**：size 上限、Markdown→Plan 解析容错、**复用记忆写入的注入扫描**（`scan_for_threats(scope="strict")`，与 `_redact_memory` 同源）拒绝注入式文件内容。
- **漂移裁决**：ingest 时文件 vs DB 漂移 —— **DB 权威**，仅当文件存在合法显式编辑（diff vs `last_projection_hash` 且通过校验）才以 DB 事务回灌；解析失败/校验不过 ⇒ 丢弃文件改动 + 记 audit + 保留原文供人审查（不静默覆盖人的编辑：记 warn）。
- **无锁**：per-(tenant,user) 单 warm session 已串行化；DB 写用事务兜底。

### 2.7 N1 Recitation（已实现，CM-0 PR3）

**关键现状**：尾部复述机制**已存在** —— `_inject_plan`（builder.py）每 turn 用 `render_plan(plan)` 把 plan append 到上下文**尾部**（非 system，保 L-1 cache prefix），这正是 Manus/Claude Code 的 recitation 范式。所以 N1 不是新加一个注入（会与 `_inject_plan` 重复，违反 CM-A7），而是**让这个已有的尾部复述带上进度**，使注意力聚焦在未完成步骤：

- **`render_plan` 改 status-aware**：每步渲染 `- [ ]/[~]/[x] N. desc`（用 PR1 的 `PlanStep.status`），复述即进度。仅被 `_inject_plan` 消费，与 `planner.parse_plan` 独立，改格式安全。
- **`update_plan` 支持每步 status**：steps 接受 string（→pending）或 `{description, status}` 对象，让 **agent 能标记进度**（否则 status 只有人改 PLAN.md 才变 → recitation 进度空转，是弱版）。这闭合"agent 标记进度 → recitation 显进度 → 注意力聚焦"的环。
- **token gauge**：`helix_cm_recitation_chars`（gauge）观测每次复述字符数，盯 plan 膨胀。

> 设计修正：原 §2.7 设想"新加一个未完成项摘要注入并与 _inject_plan 去重"。落地发现 `_inject_plan` 已做尾部复述，去重的正解 = 不新增注入、直接增强 `render_plan` 进度可见性（CM-A7 本意）。

### 2.8 边界情况

| 场景 | 处理 |
|---|---|
| 无 warm sandbox（纯 chat agent，无沙盒工具） | 投影/ingest **best-effort no-op**，log debug，不起沙盒、不失败 run |
| 沙盒冷启动/不可用 | 投影 best-effort：记 `helix_cm_projection_failed_total`，不阻塞 turn |
| plan 为 None（非 plan_execute workflow） | 不投影 PLAN/TODO；MEMORY.md 仍可投影 |
| ingest 文件被删/不存在 | 视为无编辑，跳过 |
| 大文件/恶意内容 | size cap + 注入扫描拒绝（§2.6） |

### 2.9 可观测（零债"可观测齐全"）

- `helix_cm_projection_total{file}` / `helix_cm_projection_failed_total{file,reason}`
- `helix_cm_ingest_total{result="applied|rejected|noop"}` / `helix_cm_ingest_drift_total`
- projection / ingest p95 延迟（含 sandbox 往返）
- 每次 projected/ingested emit log（tenant/user/thread）+ audit（STATE_PROJECTED/INGESTED）
- recitation 注入 token 数 gauge（防膨胀）

### 2.10 测试 & 验收（CM-0 Exit）

- **unit（≥85%）**：plan→PLAN.md/TODO.md 渲染、Markdown→Plan 解析、status 勾选映射、hash only-if-changed 跳过、ingest 校验（注入/超限拒绝）、recitation 去重与尾部注入、no-sandbox no-op。
- **integration（关键路径 ≥70%，真沙盒）**：真 warm session 写卷→`read_volume_file` 读回往返；"人手改 PLAN.md → resume → ingest 回灌 DB"；漂移→DB 权威 + audit；解析失败→丢弃文件改动不覆盖。
- **零债 6 条**：无 TODO/FIXME；docs（本节）与实现一致；metric+log+trace+audit 齐全；CI 8/8 + CodeQL 无新增 high；bug 不遗留。
- **能力指标**：projection only-if-changed 命中率（plan 未变 turn 应跳过≈100%）；ingest 回灌正确性。

### 2.11 Mini-ADR（CM-0 锁定）

| ID | 决策 |
|---|---|
| **CM-A1** | 投影 DB→file 经 warm sandbox `file_ops` 写（orchestrator/supervisor 无卷写权，已核准）；ingest 经 `read_volume_file` 只读读 + orchestrator DB 事务写 |
| **CM-A2** | 单向错时双流：turn 末 DB→file 投影 / run 始 file→DB 受控 ingest；拒对称双向同步 |
| **CM-A3** | 投影 only-if-changed（plan hash 比对，存 `AgentState.last_projection_hash`），避免每 turn 无谓 sandbox 往返 |
| **CM-A4** | ingest 默认仅 run 始 + resume-from-pause（人机协同是 approval-pause 驱动）；每-turn ingest 推迟，自测后再定 |
| **CM-A5** | `PlanStep` 加 `status`（pending/in_progress/completed，默认 pending），支撑勾选；无迁移（plan 在 checkpointer 非 DB 表） |
| **CM-A5b** | **PLAN.md 单一 ingest 源**：带 status checkbox 可精确 round-trip（`render_plan_md`↔`parse_plan_md` 互逆），是唯一可编辑回灌的文件；TODO.md/MEMORY.md 只读。消除双源回灌冲突；parse 失败→丢弃文件改动、DB 权威（修正 §2.3 原"TODO.md 双向"） |
| **CM-A6** | `AuditAction` 加 `STATE_PROJECTED`/`STATE_INGESTED` —— 核准后 `AuditAction` 是**单源 StrEnum**（仅 protocol，无 control-plane 镜像；drift 只适用 `ResourceType` 这个双处 Literal）；投影 `resource_type` 复用现成 `user_workspace`，**不新增 ResourceType**。按项目纪律枚举成员随**发射 PR**加（STATE_PROJECTED→PR2 接线、STATE_INGESTED→PR2 ingest），避免"定义即未用" |
| **CM-A7** | recitation 注入 plan 尾部摘要、与 `_inject_plan` 去重；非 system 区保 L-1 cache prefix |
| **CM-A8** | 投影/ingest best-effort：无沙盒/冷启动/解析失败均不阻塞 run，记 metric+log；DB 始终权威，人的编辑校验不过则保留原文 + warn 不静默丢 |

### 2.12 PR 切分（CM-0）

1. **CM-0 PR1 — 投影纯核心（CI 全测）**：`context/workspace_projection.py`（render 纯函数 + `WorkspaceProjector` + `WorkspaceFileWriter` Protocol seam + only-if-changed，projector 收 `last_digest` 参数）+ `PlanStep.status`（CM-A5）+ 结构化日志可观测（与 compressor 同款，非 in-module Prometheus）+ unit。**不接图、不引未发射枚举、不加未读写的状态字段**（pure-core-先行，对齐 SE-4a/SE-5a）。
2. **CM-0 PR2a — 投影接线**（已实现）：真 `SandboxWorkspaceWriter`（在 `tools/file_ops.py`，包 `build_write_wrapper`/`run_in_sandbox`，结构化满足 `WorkspaceFileWriter`）+ `build_react_graph` 新增可选 `workspace_writer_factory`（per-turn 绑 ctx）+ `tools_node` return 前 best-effort 投影钩子（`_project_workspace_state`）+ `AgentState.last_projection_hash`（投影游标）+ `AuditAction.STATE_PROJECTED` 发射（`_emit_state_projected_audit`，resource_type=`user_workspace`）+ `helix_cm_projection_total{outcome}` + agent_factory gate（persistent_workspace ∧ supervisor_client）。**MEMORY.md 无需单独 run 末投影**——`recalled_memories` 在 state、tools_node 每 turn 投影、only-if-changed 保证只写一次。单测：SandboxWorkspaceWriter（RecordingSupervisorClient 真 snippet 往返）+ graph wiring（fake writer 驱动 build_react_graph）。
3. **CM-0 PR2b-i — ingest 纯核心（CI 全测）**（已实现）：`render_plan_md` 改带 status checkbox 使 PLAN.md round-trippable + `parse_plan_md`（render 的精确逆）+ `WorkspaceFileReader` Protocol seam + `WorkspaceIngester.ingest_plan`（读 PLAN.md→parse→仅当 ≠ current 才返回候选；read 失败/parse 失败/无变更均 no-op，永不 raise）+ TODO.md/MEMORY.md 转只读（CM-A5b）+ unit（round-trip / 勾选编辑 / 无 goal 或无 step→None / 无变更 no-op）。**不接图**。
4. **CM-0 PR2b-ii — ingest 接线**（已实现）：`SandboxWorkspaceReader`（`tools/file_ops.py`，包 `build_read_wrapper`/`run_in_sandbox`，not_found→None）+ `graph_builder/workspace_ingest.py:make_workspace_ingest_node`（entry-chain，planner 后 agent 前，每 ainvoke 一次=run 始/resume）+ `build_react_graph` 可选 `workspace_ingest_node` 参 + 注入扫描校验（`scan_for_threats(scope="strict")` 扫 goal+步骤描述，命中即拒、DB 权威）+ `AuditAction.STATE_INGESTED` 发射 + `helix_cm_ingest_total{outcome}` + agent_factory gate（同投影）。6 unit（SandboxWorkspaceReader 真 snippet 往返/not_found/io_error + graph wiring：人改回灌 / 无变更 no-op / 注入拒绝）。真 live-sandbox e2e 归 integration/手动（CI 无沙盒凭证，同 PR2a 边界）。
5. **CM-0 PR3 — N1 recitation**（已实现）：尾部复述机制已由 `_inject_plan` 提供 → PR3 让其**进度可见**：`render_plan` 改 status checkbox + `update_plan` 支持每步 status（agent 标记进度，闭合进度环）+ `helix_cm_recitation_chars` gauge + unit（render status / update_plan string|object status / 无效 status→pending）。**→ CM-0 全部完成**。

> 每个 PR 在本 §2 基础上局部细化；ITERATION-PLAN 增 Stream CM backlog，ship 后回填 `[x]`+PR 号（[memory:iteration-plan-sync]）。

---

## 3. CM-1 详细设计 —— 运行时 Error-as-Guidance（工具失败→grounded 恢复 advisory）

> **目标**：ReAct 主循环里任一工具失败时，按错误类型 + 工具能力，向模型注入一条**结构化、有倾向性、grounded 于真实执行信号**的恢复建议，对抗模型"瞎猜路径已生效 / 原样重试同一失败调用"。映射框架报告 A1（实证：结构化错误恢复率 >85% vs 模糊信号 17%）。
>
> **核心策略**：**泛化已有的 L-4 mutation advisory 机制**——不新造注入管道，而是把 L-4"仅 save_artifact 落盘校验 → `failed_mutations` 通道 → 下一 turn `<mutation-advisory>` 尾部注入 → 注入后 reset"这套**已验证的范式**，泛化到**所有工具的失败**，并把 L-4 的"mutation 未落盘"收敛为新分类器的一个错误类（零债，不留并行机制）。

### 3.1 关键约束（接缝核准结论，已源码核准）

| 约束 | 事实 | file:line |
|---|---|---|
| **工具失败已被三层 catch 转 ToolMessage** | `_invoke_tool` catch 工具自身异常、`_dispatch_tool` catch `ToolNotFoundError`(未知工具) 与 middleware block，全部 → `ToolMessage(content=_format_error(exc), status="error")`，**无分类、所有异常等价** | `graph_builder/builder.py:1090-1108`(`_invoke_tool`)、`:823-880`(`_dispatch_tool`)、`:1116-1120`(`_format_error`：`[tool error] {Type}: {summary≤500}`) |
| **L-4 注入范式（待泛化）** | `classify_mutation`(仅 `save_artifact`) → tools_node 收集 `failed_mutations` 进 state → 下一 agent_node `_build_mutation_advisory` 拼 `<mutation-advisory>` `HumanMessage` 注入**尾部**(保 L-1 cache prefix) → 持久化后 `failed_mutations:[]` reset | `tools/mutation_classifier.py:41-91`；`builder.py:508-525`(收集)、`:277-289`(注入)、`:347-354`(持久化+reset)、`:670-694`(`_build_mutation_advisory`) |
| **ToolSpec 带能力元数据** | `idempotent: bool`、`side_effect: "read_only"/"reversible"/"irreversible"`(`resolved_side_effect`)、`is_read_only` —— 让"是否建议重试"grounded（幂等/只读才安全重试，不可逆非幂等需先核实状态） | `tools/registry.py:40`(`SideEffectLevel`)、`:119/:128`(`side_effect`/`resolved_side_effect`)、`:113`(`idempotent`) |
| **dispatch 三种失败 outcome** | `_record_tool_metrics` 已区分 `ok/error/blocked`：`error`=工具/未知工具异常，`blocked`=middleware(approval/guardrail)拦截 | `builder.py:823`(blocked)、`:1093`(error)；`_tool_call_total{tool,outcome}` |
| **ToolResult 无 error 字段** | 错误走异常路径(非 ToolResult)；`ToolResult{content,meta,state_updates,refund_iterations}` 无 error/is_error —— CM-1 不改 ToolResult 契约，在 catch 点(有 `exc` 真类型)分类 | `tools/registry.py:197-238` |
| **失败仍计 1 步（不退款）** | catch 路径返回 `refund_inc=0`：失败的工具调用仍是 user-visible 尝试，照常消耗 iteration（与 L-5 退款语义一致，不动） | `builder.py:1100-1108`(error 返回 `0`) |
| **运行时无错误分类体系** | `errors.py` 仅 factory 期异常(`AgentFactoryError`/`SkillNotFoundError`)，主循环无任何 `ClassifiedError`/recovery —— CM-1 从零建（参考 Hermes 结构，不抄其 API-层 taxonomy） | `orchestrator/errors.py` |

> **结论**：CM-1 = 在 catch 点（`exc` 真类型在手，信号最丰富）做**确定性分类** → 产出 `ClassifiedToolError` → 经统一 `tool_failures` 通道（泛化 `failed_mutations`）→ 下一 turn `_build_recovery_advisory` 拼 `<recovery-advisory>` 尾部注入。L-4 的 mutation 校验作为"未落盘"一类收敛进来。

### 3.2 设计：分类 → 通道 → 注入（泛化 L-4，三段）

```
tools_node（每个失败的工具调用）
  ├─ 错误路径(status="error")：在 _invoke_tool/_dispatch_tool catch 处，
  │    classify_tool_error(tool_name, args, exc, spec) → ClassifiedToolError
  └─ 成功但语义未落地：classify_mutation 现逻辑 → ClassifiedToolError(class="mutation_not_landed")
  ⇒ 聚合进 state["tool_failures"]: list[ClassifiedToolError]   ← 泛化 failed_mutations

下一个 agent_node 入口
  └─ 若 tool_failures 非空：_build_recovery_advisory(tool_failures) → HumanMessage
       注入 messages 尾部（非 system，保 L-1 cache prefix；与 N1 recitation 同区错开拼接）
  └─ 持久化该 advisory + response；返回 {"tool_failures": []} reset（单次注入，不重复）
```

不变量沿用 L-4：① 注入在**尾部 HumanMessage**（不污染 system，保 prompt-cache）；② **单次注入即 reset**（下一步无残留）；③ 失败信号在 state 上跨节点传递（tools_node 产、agent_node 消费）。

### 3.3 工具错误 taxonomy（grounded、确定性，无 LLM）

分类**纯由真实信号推导**（异常类型名 + 错误文本模式 + `ToolSpec` 能力），不调模型、不靠内省——直接落实框架报告修订①"注入须源自真实工具/执行信号"。

| `error_class` | grounded 信号 | 恢复倾向（advice 模板核心） | retryable |
|---|---|---|:---:|
| `unknown_tool` | `ToolNotFoundError` | 该工具不存在；从可用工具里选，**勿原样重试** | 否 |
| `invalid_arguments` | `ValueError`/校验类异常 + args 被拒文本 | 参数被拒(附 why)；**修正参数**再调，勿重复同一调用 | 否（改参后可） |
| `blocked_by_policy` | dispatch outcome=`blocked`（middleware/approval/guardrail） | 被策略/审批拦截；**等待审批或上报用户**，勿绕过重试 | 否 |
| `resource_not_found` | `FileNotFoundError`/"not found"/404 类文本 | 目标(路径/id)不存在；**先核实存在性**再操作 | 否（核实后可） |
| `permission_denied` | `PermissionError`/auth/403 类文本 | 权限不足；**勿暴力重试**，上报 | 否 |
| `transient` | `TimeoutError`/连接/沙盒不可用/5xx 类文本 | 暂时性失败；**幂等/只读可重试一次**(读 `spec`)，仍失败则上报 | 是（受 spec 约束） |
| `mutation_not_landed` | L-4 现逻辑：成功态但写未落地 | 路径**未**含所请求内容，勿假定已生效；重试或上报（沿用 L-4 文案） | 是 |
| `unknown` | 兜底（不可分类） | 原因不明；检视错误、**考虑替代手段**，避免原样重试 | 否（保守） |

分类实现 = 异常类型名优先 + 错误文本小写模式匹配兜底（确定性 if/elif 链，无正则灾难）。新增类只在此表扩。

### 3.4 恢复建议构造（grounded 约束）

- **retryable 受 `ToolSpec` 约束**：即便 `transient`，也仅当 `spec.idempotent` 或 `resolved_side_effect=="read_only"` 才建议"可安全重试"；不可逆非幂等工具一律"重试前先核实状态"。这让"建议重试"不会诱导模型对不可逆副作用工具盲目重放。
- **advice 是模板化结构文本**（错误类 → 固定话术 + 注入工具名/参数摘要/错误摘要），非自由生成；保证可预测、可测、token 受控。
- **不含 fingerprint 升档**：同工具同参连续失败的"停止重试/换路"升档属 **CM-9 N4（loop 指纹去重）**，CM-1 advice 文案仅以"勿原样重试"软提示，不引入 per-thread 指纹存储（避免越界，保 surgical）。

### 3.5 与 L-4 收敛（零债，不留并行机制）

框架报告写明 CM-1"**泛化 L-4**"。落地为**收敛而非并行**：

- `MutationOutcome{tool_name,path,landed,error}` 的失败语义 → 映射为 `ClassifiedToolError(error_class="mutation_not_landed", tool_name, summary=error, path)`。
- `failed_mutations` 状态键 → 由统一 `tool_failures: list[ClassifiedToolError]` 取代；`classify_mutation` 现逻辑保留为"未落盘"这一类的**贡献者**（成功路径专用：status 非 error 但写未落地，这是 CM-1 错误路径覆盖不到的语义失败，必须保留）。
- `_build_mutation_advisory` 的 `<mutation-advisory>` 文案 → 并入 `_build_recovery_advisory` 的 `<recovery-advisory>` 块，mutation 类保留原措辞（行为不回归，L-4 测试相应更新）。
- 因是 checkpointer 内 transient 每-step 状态（非 DB 表、每 agent step reset），**无迁移**；旧 checkpoint 的 `failed_mutations` 键由 reducer 默认 `[]` 兜底。

### 3.6 数据/协议变更

1. **新模块 `orchestrator/graph_builder/error_classifier.py`**（与 `mutation_classifier.py` 同层）：
   ```python
   ToolErrorClass = Literal[
       "unknown_tool", "invalid_arguments", "blocked_by_policy",
       "resource_not_found", "permission_denied", "transient",
       "mutation_not_landed", "unknown",
   ]
   @dataclass(frozen=True)
   class ClassifiedToolError:
       tool_name: str
       error_class: ToolErrorClass
       summary: str               # 错误摘要（≤cap）
       retryable: bool
       advice: str                # 模板化恢复建议（注入 advisory 的正文）
       path: str | None = None    # mutation 类沿用
   def classify_tool_error(tool_name, args, exc_or_message, spec) -> ClassifiedToolError: ...
   def render_recovery_advisory(failures: list[ClassifiedToolError]) -> str: ...  # 纯函数，拼 <recovery-advisory>
   ```
2. **`AgentState` 通道泛化**（`orchestrator/state.py`）：`failed_mutations: NotRequired[list[MutationOutcome]]` → `tool_failures: NotRequired[list[ClassifiedToolError]]`（覆写 reducer，每 agent step reset；docstring 更新 L-4→CM-1 由 L-4 泛化）。
3. **不改 `ToolResult`/`ToolSpec`/`Tool` 协议**——分类在 catch 点用 `exc`+`spec`，零契约变更。
4. **无 `AuditAction`/`ResourceType` 变更**——advisory 是 in-context 引导，非审计事件（失败已由 `_tool_call_total{outcome}` + tool audit 记录）。

### 3.7 边界情况

| 场景 | 处理 |
|---|---|
| 一个 tool batch 多个失败 | 全部分类，`_build_recovery_advisory` 合并为单个 `<recovery-advisory>` 多行块 |
| 失败 + 成功混合 | 仅失败项进 advisory；成功项正常 ToolMessage |
| 分类不出明确类 | 落 `unknown`，给保守 advice（检视+勿原样重试），retryable=False |
| advisory 过长（多失败） | 每条 summary 截断（沿用 `_format_error` 的 500 cap 思路），整块 char gauge 观测 |
| `mutation_not_landed`（成功态语义失败） | 沿用 L-4 校验逻辑产出，文案不变 |
| 非 plan_execute / 无 plan | 与 plan 无关，照常工作（CM-1 不依赖 plan） |

### 3.8 可观测（零债"可观测齐全"）

- `helix_cm_tool_error_total{error_class, tool}`（counter）—— 按类 × 工具计失败，看哪些工具/错误类高频。
- `helix_cm_recovery_advisory_chars`（gauge）—— 每次注入 advisory 字符数，防膨胀（与 N1 `helix_cm_recitation_chars` 同款）。
- 复用现有 `helix_tool_call_total{tool,outcome}`（raw 失败计数，不重复造）。
- 每次注入 emit log（tenant/user/thread + error_class 列表）；失败本身已有 tool audit，不新增 audit。

### 3.9 测试 & 验收（CM-1 Exit）

- **unit（≥85%）**：每种异常类型/文本 → 正确 `error_class`+`retryable`+advice；`transient` × (idempotent/read_only/irreversible) → retry 建议差异；`blocked` outcome → `blocked_by_policy` 不建议重试；`mutation_not_landed` 收敛正确（沿用 L-4 用例语义）；`render_recovery_advisory` 多失败合并/截断；空失败 → 无 advisory；注入后 reset。
- **integration（关键路径，真 graph）**：scripted LLM + 一个会 raise 的工具 → 下一 agent turn 尾部含 `<recovery-advisory>` 且含正确 class/advice；与 L-4 e2e 等价迁移（save_artifact 失败仍产出 advisory）。
- **零债 6 条**：无 TODO/FIXME；§3 与实现一致；metric+log 齐全（无新增 audit 因失败已记）；CI 8/8 + CodeQL 无新增 high；L-4 行为不回归。

### 3.10 Mini-ADR（CM-1 锁定）

| ID | 决策 |
|---|---|
| **CM-B1** | 分类**确定性、无 LLM**：纯由异常类型名 + 错误文本模式 + `ToolSpec` 能力推导（grounded，落实框架报告修订①"注入须源自真实执行信号"，不靠模型内省） |
| **CM-B2** | **泛化而非并行 L-4**：统一 `tool_failures` 通道取代 `failed_mutations`；`<recovery-advisory>` 块取代 `<mutation-advisory>`；mutation 校验作为 `mutation_not_landed` 一类收敛进来（成功路径语义失败专用，保留） |
| **CM-B3** | 分类在 **catch 点**做（`exc` 真类型在手，信号最丰富），不在 tools_node 事后从 ToolMessage 文本反推 |
| **CM-B4** | 注入沿用 L-4 不变量：尾部 `HumanMessage`（保 L-1 cache prefix）+ 单次注入即 reset；不改 `ToolResult`/`ToolSpec`/`Tool` 契约 |
| **CM-B5** | retry 建议**受 `ToolSpec.idempotent`/`side_effect` 约束**：仅幂等/只读才建议"安全重试"，不可逆非幂等→"重试前核实状态"，防诱导对副作用工具盲目重放 |
| **CM-B6** | fingerprint 升档（同工具同参连续失败→强制停/换路）**不在 CM-1**，归 CM-9 N4；CM-1 advice 仅以"勿原样重试"软提示，不引入 per-thread 指纹存储（保 surgical） |

### 3.11 PR 切分（CM-1）

1. **CM-1 PR1 — 分类器纯核心（CI 全测）**：`graph_builder/error_classifier.py`（`ToolErrorClass` taxonomy + `ClassifiedToolError` + `classify_tool_error` 纯函数 + `render_recovery_advisory` 纯函数 + retry 受 spec 约束逻辑）+ unit（每类分类 / retry×spec 矩阵 / advisory 渲染合并截断）。**不接图、不动 state、不碰 L-4**（pure-core-先行，对齐 CM-0 PR1 / SE-4a 节奏）。
2. **CM-1 PR2 — 接线 + L-4 收敛（端到端）**：catch 点(`_invoke_tool`/`_dispatch_tool`)调 `classify_tool_error` → 聚合进 `tool_failures`；`AgentState` `failed_mutations`→`tool_failures` 泛化；`classify_mutation` 现逻辑改产 `ClassifiedToolError(mutation_not_landed)`；agent_node `_build_recovery_advisory` 取代 `_build_mutation_advisory`（`<recovery-advisory>`，mutation 文案保留）+ 注入/reset 不变；`helix_cm_tool_error_total` + `helix_cm_recovery_advisory_chars` + 注入 log；迁移 L-4 测试 + 新增 e2e（raise 工具 → 下一 turn advisory）。**→ CM-1 完成**。

> 每个 PR 在本 §3 基础上局部细化；ITERATION-PLAN 增 CM-1 backlog，ship 后回填 `[x]`+PR 号（[memory:iteration-plan-sync]）。

---

## 4. 与既有 Stream 的衔接

- **Stream J**：复用 `user_workspace`（J.15 卷）、`memory_item`（J.3）、approval（J.8 pause→ingest 时机）、`update_plan`（K.8）。
- **Stream L**：投影钩子在 agent_node/tools_node，与 L-1 cache prefix（system 冻结）、L-2 compressor（CM-2/3 在此之上）共存；recitation 放非 system 区不破 L-1。**L-4 mutation advisory 被 CM-1 收敛**（`failed_mutations`→`tool_failures`、`<mutation-advisory>`→`<recovery-advisory>`，mutation 校验作为 `mutation_not_landed` 一类保留），非并行；L-5 退款语义不动（失败工具调用照常计步）。
- **Stream SE**：CM-1（运行时 error-as-guidance）与 SE-12（离线 skill 进化失败归因）是**两个层面**——SE 学习离线进化、CM-1 运行时即时恢复，不重叠。
- **Stream H（admin-ui）**：CM-8 文件投影 + UI 双通道依赖 CM-0 的 ingest 路径。
