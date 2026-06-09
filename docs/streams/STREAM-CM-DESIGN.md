# Stream CM — 上下文与记忆管理强化（设计先行）

> **背景**：2026-06-09 基于系列文章《Harness 工程》对 OpenClaw / deer-flow / Hermes 做了上下文与记忆管理的源码级对比（`docs/research/2026-06-09-context-memory-management-comparison.md`），并对 helix 现状做了 10 维取证 + 2024–2026 论文/工程博客二次评估（`docs/research/2026-06-09-helix-context-memory-improvement-framework.md`，v2 含外部证据）。本 Stream 把框架报告里确认的改进条目落成可执行设计。
>
> **总基调（已拍板）**：**② 混合——DB 为真相源 + workspace 文件投影**，用**单向错时双流**实现（turn 末 DB→file 投影 / turn 始 file→DB 受控 ingest），**不做对称双向同步**（外部证据判为反模式）。
>
> **设计先行规则**（[memory:design-first-iteration]）：所有总体架构 / 跨切面接口 / Mini-ADR 在本文件锁定；每条 gap PR 在对应章节基础上做局部细化。
>
> **零债收尾规则**（[memory:zero-tech-debt]）：每条交付收尾 6 条全过 —— 无 TODO / 测试达标 / 文档同步 / 可观测齐全 / CI 全绿 / bug 不遗留。
>
> **本文件状态**：CM-0（地基）详设已锁定；CM-1…CM-9 / CM-N 列入范围表，待各自 PR 时在本文件细化。

---

## 1. 范围 & 边界

### 1.1 In-scope（映射框架报告 A/B/C/N 档）

| ID | 框架条目 | Gap | 交付 | 优先级 | Mini-ADR |
|----|---------|-----|------|--------|---------|
| **CM-0** | C0 + N1 | 状态内化、无文件投影；workspace 卷已有但状态不可见、不可手改 | 状态↔workspace 文件投影 + 单向错时双流 + recitation 复诵 | **先行** | CM-A1…A8（本文详设） |
| CM-1 | A1 | 运行时主循环工具失败无 grounded 恢复建议（SE-12 是离线 skill 进化，L-4 只覆盖文件 mutation） | 通用工具失败→结构化恢复 advisory 注入主循环 | P0 | 待细化 |
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
| `/workspace/PLAN.md` | `AgentState.plan`（goal + steps） | 双向（投影 + ingest） | 人可改步骤 → ingest 回灌 |
| `/workspace/TODO.md` | plan steps + **新增 step status** | 双向 | 勾选态需 `PlanStep.status`（CM-A5） |
| `/workspace/MEMORY.md` | `memory_item`（consolidated + top recalled） | **单向只读投影** | 不 ingest（记忆有独立写路径 memory_writeback + consolidator） |

不变量：① **path-addressable**——固定路径，内容可随时从 DB 重建；② **compaction-stable**——投影文件不进 LLM 上下文（只 recitation 摘要进），故不受 compaction 影响、丢失可重建。

### 2.4 挂钩点（已核准位置）

| 钩子 | 位置 | 频率 | 可访问 |
|---|---|---|---|
| ingest（file→DB） | `agent_node` 入口、消息合成前（`builder.py:236` 前），仅 run 首 turn / resume | run 始一次 | state, config（tenant/user/thread），可改 messages/plan via 返回 dict |
| recitation（N1） | `agent_node` 内、plan 注入处附近（`builder.py:239-241`） | 每 turn | 当前 plan/todo，注入 messages 尾部 |
| 投影（DB→file） | `tools_node` 返回前（`builder.py:507`），only-if plan 变更 | 每 turn（条件触发） | state + accumulated_state（含本 turn plan 变更） |
| MEMORY.md 投影 | `memory_writeback` 节点内/后（`memory.py:240-319`） | run 末一次 | 完整轨迹 + 已写记忆 |

投影/ingest 逻辑封装到 **新模块 `orchestrator/context/workspace_projection.py`**（`WorkspaceProjector` / `WorkspaceIngester`），builder.py 仅在钩子点调用，保持节点函数瘦。

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

### 2.7 N1 Recitation（并入 CM-0）

每 turn 在 `agent_node` 把当前 PLAN/TODO 的**紧凑摘要**（goal + 未完成 steps）注入上下文**尾部**（非 system，保 L-1 cache prefix 稳定），对抗 long-context lost-in-the-middle。纯 in-context，不触发文件 IO。与现有 `_inject_plan` 区别：recitation 放尾部且只放未完成项摘要，`_inject_plan` 是完整 plan。二者去重，避免重复占 token（CM-A7）。

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
| **CM-A5** | `PlanStep` 加 `status`（pending/in_progress/completed，默认 pending），支撑 TODO.md 勾选；无迁移（plan 在 checkpointer 非 DB 表） |
| **CM-A6** | `AuditAction` 加 `STATE_PROJECTED`/`STATE_INGESTED` —— 核准后 `AuditAction` 是**单源 StrEnum**（仅 protocol，无 control-plane 镜像；drift 只适用 `ResourceType` 这个双处 Literal）；投影 `resource_type` 复用现成 `user_workspace`，**不新增 ResourceType**。按项目纪律枚举成员随**发射 PR**加（STATE_PROJECTED→PR2 接线、STATE_INGESTED→PR2 ingest），避免"定义即未用" |
| **CM-A7** | recitation 注入 plan 尾部摘要、与 `_inject_plan` 去重；非 system 区保 L-1 cache prefix |
| **CM-A8** | 投影/ingest best-effort：无沙盒/冷启动/解析失败均不阻塞 run，记 metric+log；DB 始终权威，人的编辑校验不过则保留原文 + warn 不静默丢 |

### 2.12 PR 切分（CM-0）

1. **CM-0 PR1 — 投影纯核心（CI 全测）**：`context/workspace_projection.py`（render 纯函数 + `WorkspaceProjector` + `WorkspaceFileWriter` Protocol seam + only-if-changed，projector 收 `last_digest` 参数）+ `PlanStep.status`（CM-A5）+ 结构化日志可观测（与 compressor 同款，非 in-module Prometheus）+ unit。**不接图、不引未发射枚举、不加未读写的状态字段**（pure-core-先行，对齐 SE-4a/SE-5a）。
2. **CM-0 PR2a — 投影接线**（已实现）：真 `SandboxWorkspaceWriter`（在 `tools/file_ops.py`，包 `build_write_wrapper`/`run_in_sandbox`，结构化满足 `WorkspaceFileWriter`）+ `build_react_graph` 新增可选 `workspace_writer_factory`（per-turn 绑 ctx）+ `tools_node` return 前 best-effort 投影钩子（`_project_workspace_state`）+ `AgentState.last_projection_hash`（投影游标）+ `AuditAction.STATE_PROJECTED` 发射（`_emit_state_projected_audit`，resource_type=`user_workspace`）+ `helix_cm_projection_total{outcome}` + agent_factory gate（persistent_workspace ∧ supervisor_client）。**MEMORY.md 无需单独 run 末投影**——`recalled_memories` 在 state、tools_node 每 turn 投影、only-if-changed 保证只写一次。单测：SandboxWorkspaceWriter（RecordingSupervisorClient 真 snippet 往返）+ graph wiring（fake writer 驱动 build_react_graph）。
3. **CM-0 PR2b — 受控 ingest**：WorkspaceIngester（`read_volume_file` 读 + 解析 + 校验 + DB 事务回灌）+ `agent_node` run 始钩子 + `AuditAction.STATE_INGESTED` + 漂移裁决（DB 权威）+ integration（真沙盒往返 + 人改回灌）。
4. **CM-0 PR3 — N1 recitation**：`agent_node` 尾部摘要注入 + 去重 + token gauge + unit。

> 每个 PR 在本 §2 基础上局部细化；ITERATION-PLAN 增 Stream CM backlog，ship 后回填 `[x]`+PR 号（[memory:iteration-plan-sync]）。

---

## 3. 与既有 Stream 的衔接

- **Stream J**：复用 `user_workspace`（J.15 卷）、`memory_item`（J.3）、approval（J.8 pause→ingest 时机）、`update_plan`（K.8）。
- **Stream L**：投影钩子在 agent_node/tools_node，与 L-1 cache prefix（system 冻结）、L-2 compressor（CM-2/3 在此之上）、L-4 mutation advisory（CM-1 泛化它）共存；recitation 放非 system 区不破 L-1。
- **Stream SE**：CM-1（运行时 error-as-guidance）与 SE-12（离线 skill 进化失败归因）是**两个层面**——SE 学习离线进化、CM-1 运行时即时恢复，不重叠。
- **Stream H（admin-ui）**：CM-8 文件投影 + UI 双通道依赖 CM-0 的 ingest 路径。
