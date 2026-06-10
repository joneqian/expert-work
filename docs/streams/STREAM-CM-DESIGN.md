# Stream CM — 上下文与记忆管理强化（设计先行）

> **背景**：2026-06-09 基于系列文章《Harness 工程》对 OpenClaw / deer-flow / Hermes 做了上下文与记忆管理的源码级对比（`docs/research/2026-06-09-context-memory-management-comparison.md`），并对 helix 现状做了 10 维取证 + 2024–2026 论文/工程博客二次评估（`docs/research/2026-06-09-helix-context-memory-improvement-framework.md`，v2 含外部证据）。本 Stream 把框架报告里确认的改进条目落成可执行设计。
>
> **总基调（已拍板）**：**② 混合——DB 为真相源 + workspace 文件投影**，用**单向错时双流**实现（turn 末 DB→file 投影 / turn 始 file→DB 受控 ingest），**不做对称双向同步**（外部证据判为反模式）。
>
> **设计先行规则**（[memory:design-first-iteration]）：所有总体架构 / 跨切面接口 / Mini-ADR 在本文件锁定；每条 gap PR 在对应章节基础上做局部细化。
>
> **零债收尾规则**（[memory:zero-tech-debt]）：每条交付收尾 6 条全过 —— 无 TODO / 测试达标 / 文档同步 / 可观测齐全 / CI 全绿 / bug 不遗留。
>
> **本文件状态**：CM-0（地基，§2）+ CM-1（运行时 error-as-guidance，§3）+ CM-2（working memory 滑动窗口，§4）+ CM-3（压缩前 flush，§5）+ CM-4（reranker 接通，§6）+ CM-5（可恢复压缩，§7）+ CM-6（时间衰减+MMR，§8）+ CM-7（结构化摘要+写入显式操作，§9）详设已锁定；CM-8…CM-9 / CM-N 列入范围表，待各自 PR 时在本文件细化。

---

## 1. 范围 & 边界

### 1.1 In-scope（映射框架报告 A/B/C/N 档）

| ID | 框架条目 | Gap | 交付 | 优先级 | Mini-ADR |
|----|---------|-----|------|--------|---------|
| **CM-0** | C0 + N1 | 状态内化、无文件投影；workspace 卷已有但状态不可见、不可手改 | 状态↔workspace 文件投影 + 单向错时双流 + recitation 复诵 | **先行** | CM-A1…A8（本文详设） |
| CM-1 | A1 | 运行时主循环工具失败无 grounded 恢复建议（SE-12 是离线 skill 进化，L-4 只覆盖文件 mutation） | 通用工具失败→结构化恢复 advisory 注入主循环 | P0 | CM-B1…B6（§3 详设） |
| CM-2 | A2 | 无"保留最近 N 轮"廉价前置闸，轻溢出每次走 LLM 摘要 | `agent_node` compressor 前加滑窗截断（保 ToolCall↔ToolResult 配对） | P0 | CM-C1…C6（§4 详设） |
| CM-3 | A3 | compressor 丢弃中段前无 flush，`memory_writeback` 只在 run 末 | 压缩前回调 → 复用 writeback 通道中途落盘 | P1 | CM-D1…D6（§5 详设） |
| CM-4 | B5 | rerank_provider/model 配置预留但检索路径无 rerank 调用 | memory recall Hybrid 召回后接 cross-encoder rerank | P1 | CM-E1…E6（§6 详设） |
| CM-5 | B6 | 超大工具结果 char-cap 截断丢弃，不可找回 | 超限结果外部化 workspace 文件 + 虚拟引用 footer + read 类豁免（"可恢复压缩"通用原则） | P1 | CM-F1…F6（§7 详设） |
| CM-6 | B4 | 对称 RRF，无 MMR 去冗余、无时间衰减 | 时间衰减进 retrieve()（两 store）+ MMR 在 orchestrator rerank 后殿后（管线顺序修正见 §8） | P1 | CM-G1…G6（§8 详设） |
| CM-7 | B7 | `<context-summary>` 缺"背景非指令"强语义、二次压缩链式重生成、writeback 直写同义堆积矛盾不废弃 | 摘要 preamble+三段结构+增量更新 ① + run 末 writeback 显式 ADD/UPDATE/DELETE/NOOP ② | P2 | CM-H1…H6（§9 详设） |
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
| 可恢复压缩"留引用不丢源"（Manus / Anthropic tool-clearing） | ✅ 借（CM-5） | 存储基底=workspace 文件（取证修正：artifact 表只是元数据注册，内容基底本就是 workspace 文件；ObjectStore M0 未接 artifact 内容） |
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

1. **新模块 `orchestrator/tools/error_classifier.py`**（与 `mutation_classifier.py` 真同层 = `tools/`；PR1 误放 `graph_builder/`，PR2 接线时发现 `AgentState` 运行时需 `get_type_hints` 解析 `ClassifiedToolError`，从 `graph_builder/` 运行时导入会触发 `state→graph_builder→builder→state` 环，故 PR2 `git mv` 到 `tools/` 下层根治）：
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
3. **不改 `ToolResult`/`ToolSpec`/`Tool` 公共协议**——分类在 catch 点用 `exc`+`spec`。`_dispatch_tool`/`_invoke_tool` 的**内部**返回元组从 3 元扩到 4 元（追加 `ClassifiedToolError | None`，成功路径为 `None`），随之更新 `_run_call`/`_bounded`/`results` 标注与 `test_tool_audit` 的解构（机械）。
4. **无 `AuditAction`/`ResourceType` 变更**——advisory 是 in-context 引导，非审计事件（失败已由 `_tool_call_total{outcome}` + tool audit 记录）。

### 3.7 边界情况

| 场景 | 处理 |
|---|---|
| 一个 tool batch 多个失败 | 全部分类，`_build_recovery_advisory` 合并为单个 `<recovery-advisory>` 多行块 |
| 失败 + 成功混合 | 仅失败项进 advisory；成功项正常 ToolMessage |
| 分类不出明确类 | 落 `unknown`，给保守 advice（检视+勿原样重试），retryable=False |
| advisory 过长（多失败） | 每条 summary 截断（沿用 `_format_error` 的 500 cap 思路），整块 char gauge 观测 |
| `mutation_not_landed`（mutation 工具失败） | `_classify_tool_failure` **优先**跑 L-4 mutation 校验：已知 mutation 工具（save_artifact）无论 raise 还是成功态未落地，都归 `mutation_not_landed`（带 path，比泛型 error 更可操作）；非 mutation 工具才回落 catch 点的 `classified` |
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
2. **CM-1 PR2 — 接线 + L-4 收敛（端到端）**（已实现）：模块 `git mv` graph_builder/→tools/（破 import 环）；catch 点(`_invoke_tool`/`_dispatch_tool`)调 `classify_tool_error` → 4 元元组带出 `ClassifiedToolError`；`_classify_tool_failure` 优先 mutation 校验、回落 catch 分类 → 聚合进 `tool_failures`；`AgentState` `failed_mutations`→`tool_failures` 泛化；agent_node `_build_recovery_advisory`/`render_recovery_advisory` 取代 `_build_mutation_advisory`（`<recovery-advisory>`，path 保留）+ 注入/reset 不变；`helix_cm_tool_error_total{error_class,tool}` + `helix_cm_recovery_advisory_chars`；迁移 L-4 测试（test_mutation_advisory→test_recovery_advisory，含"失败只读工具现在也 advise"的 CM-1 增量用例）+ test_state/test_system_byte_stable/test_tool_audit 更新。977 orchestrator 回归绿。**→ CM-1 完成**。

> 每个 PR 在本 §3 基础上局部细化；ITERATION-PLAN 增 CM-1 backlog，ship 后回填 `[x]`+PR 号（[memory:iteration-plan-sync]）。

---

## 4. CM-2 详细设计 —— Working Memory 滑动窗口（compressor 前的廉价前置闸）

> **目标**：在 `agent_node` 入口、compressor preflight **之前**，加一道**按用户轮次裁剪、廉价、无 LLM**的滑动窗口闸。轻溢出场景直接裁到"最近 N 轮"即可压回阈值下，**省掉 compressor 的 LLM 摘要调用**；compressor 退为只处理裁剪后仍重溢出的第二道闸。映射框架报告 A2（OpenClaw `limitHistoryTurns`，生产 bug #1084 验证配对完整性是硬约束）。
>
> **核心策略**：**token-gate 的轮次裁剪**——仅当估算 prompt 超阈值才裁，未溢出**完全 no-op（零行为变更）**；裁剪只在 **HumanMessage 边界**切，天然保 ToolCall↔ToolResult 配对；**保首轮（初始任务）+ 最近 N 轮**，对抗"裁掉原始目标"。

### 4.1 关键约束（接缝核准结论，已源码核准）

| 约束 | 事实 | file:line |
|---|---|---|
| **compressor 是唯一现有压缩闸，且是 LLM 重武器** | `agent_node` 仅在所有注入后调 `context_compressor.should_compress` → `compress`（调摘要 LLM）；无任何"保留最近 N 轮"的廉价前置裁剪 | `graph_builder/builder.py:317-318`、`context/compressor.py:228-272` |
| **裁剪只作用当次 prompt，不改 checkpoint** | `agent_node` return 只 append `response`（`{"messages":[response]}`），从不重写 history；compressor 对 `messages` 的裁剪只影响本次 LLM 调用，checkpoint 里完整 history 保留 → **window 裁掉的中间轮在 checkpoint 无永久丢失**，下一 turn 从 checkpoint 重新加载再裁 | `builder.py:363-380`（return 只 append）、`:317-318`（compress 只改局部 `messages`） |
| **leading SystemMessage 必须冻结（L1 不变量）** | compressor `_split` 把 leading `SystemMessage` 排除在 head/tail 之外原样保留；window 必须沿用同一不变量，永不裁系统前缀 | `context/compressor.py:146-159` |
| **复用估算器，不另造** | `estimate_tokens(messages)=total_chars//4` 已是 compressor 的 preflight 估算；window 的 token-gate 复用同一函数，保口径一致 | `context/compressor.py:94-105` |
| **注入块在 window 之后加** | plan(J.1)/memory(J.3)/recovery-advisory(CM-1) 注入都在 `messages=list(state["messages"])` 之后；window 放在**注入之前**，只裁原始 history，绝不裁当前 turn 的引导块 | `builder.py:279`（取 history）、`:282-308`（注入） |
| **compressor 同款配置范式** | `ContextCompressionPolicy` 经 `PolicySpec.context_compression` → factory 构造 `ContextCompressor` 传 `build_react_graph`；window 镜像此范式（新 `WorkingMemoryPolicy` → `WorkingWindow` → 新参数） | `agent_spec.py:420-449`、`agent_factory.py:478-488`、`builder.py:201` |

> **结论**：CM-2 = 新增 `context/working_window.py`（与 compressor 同层），在 `agent_node` 取 history 后、注入前插一道 token-gate 轮次裁剪。因裁剪不改 checkpoint，**无永久信息损失**，与 CM-3（压缩前 flush）解耦——CM-3 是为 compressor 的有损摘要服务，window 本身不需要它兜底。

### 4.2 设计：token-gate → 轮次裁剪 → compressor 兜底（三段）

```
agent_node 入口： messages = list(state["messages"])
  │
  ├─ CM-2 working_window.apply(messages)        ← 新增第一道闸（本设计）
  │     ├─ should_trim? estimate_tokens >= threshold_tokens     （未溢出 → 原样返回，零行为变更）
  │     └─ 溢出 → trim_to_recent_turns：
  │           leading systems（冻结）
  │           + [首轮 first turn]（keep_first_turn，含初始任务目标）
  │           + 最近 max_recent_turns 个用户轮（HumanMessage 边界切）
  │           丢弃中间老轮（仅本次 prompt 视图；checkpoint 完整）
  │
  ├─ 注入 plan / memory / recovery-advisory（不受 window 影响）
  │
  └─ compressor.should_compress(messages)?      ← 退为第二道闸
        裁剪后仍 >= compressor 阈值（如最近 N 轮里有超大 tool result）→ LLM 摘要中段
        裁剪后 < 阈值 → should_compress=False，省掉 LLM 调用 ✓
```

两闸协同：window 与 compressor 默认同 `threshold_pct`（0.7）。window 先跑（注入前），多数轻溢出裁完即 < 阈值 → compressor 跳过。仅"最近 N 轮自身仍超窗"的重溢出才落到 compressor。

### 4.3 ToolCall↔ToolResult 配对保证（OpenClaw #1084 硬约束）

裁剪点**恒为 HumanMessage 索引**。论证：良构 ReAct history 中，`AIMessage(tool_calls)` 后紧跟对应 `ToolMessage`，一个 `HumanMessage` 永远是新一轮的开始、**绝不出现在 tool_use↔tool_result 之间**。因此：

- 保留"从某 HumanMessage 起的后缀"→ 后缀内所有 tool 对完整。
- 首轮片段 `remainder[首个HumanMessage : 第二个HumanMessage]` 以 HumanMessage 始、HumanMessage 前止 → 完整。

退化与去重：① 无 HumanMessage（纯系统/AI 序列，无法定义轮次）→ no-op 全留；② 用户轮总数 ≤ `max_recent_turns`(+首轮)→ no-op；③ 首轮片段与最近 N 轮窗重叠 → 不重复拼首轮，直接返回 `leading + remainder[窗起:]`。三种边界均有单测。

### 4.4 数据/协议变更

1. **新模块 `orchestrator/context/working_window.py`**（与 `compressor.py` 同层 `context/`，复用其 `estimate_tokens` + leading-system 分离逻辑，无环）：
   ```python
   @dataclass(frozen=True)
   class TrimResult:
       messages: list[BaseMessage]
       dropped_turns: int          # 裁掉的用户轮数（0 = no-op）

   def trim_to_recent_turns(
       messages: Sequence[BaseMessage], *, max_recent_turns: int, keep_first_turn: bool
   ) -> TrimResult: ...            # 纯函数、token-unaware、按 HumanMessage 边界切

   @dataclass(frozen=True)
   class WorkingWindow:
       context_window: int
       threshold_pct: float = 0.7
       max_recent_turns: int = 20
       keep_first_turn: bool = True
       @property
       def threshold_tokens(self) -> int: ...
       def should_trim(self, messages) -> bool: ...     # token-gate（复用 estimate_tokens）
       def apply(self, messages) -> TrimResult: ...      # gate 通过才 trim，否则原样
   ```
2. **新 `WorkingMemoryPolicy`**（`agent_spec.py`）挂 `PolicySpec.working_memory`（`extra="forbid"`，与 `context_compression` 并列）：`enabled: bool=True` / `threshold_pct: float=0.7(gt 0, le 1)` / `max_recent_turns: int=20(gt 0)` / `keep_first_turn: bool=True`。默认值保守：典型对话 ≤20 轮或未溢出 → no-op，**对现有 manifest 零行为变更**。
3. **factory 接线**（`agent_factory.py`）：`wm_policy = spec.spec.policies.working_memory`；`enabled` 则构造 `WorkingWindow(context_window=spec.spec.model.context_window, ...)` 传 `build_react_graph(working_window=...)`。
4. **builder 接线**（`graph_builder/builder.py`）：新参数 `working_window: WorkingWindow | None = None`；`agent_node` 在 `messages = list(state["messages"])` 后、plan 注入前：`if working_window is not None: result = working_window.apply(messages); messages = result.messages` + emit metric。
5. **不改 `AgentState` / `ToolResult` / `ToolSpec`**——window 是纯 prompt 视图裁剪，无状态通道、无 checkpoint 变更、无迁移。

### 4.5 边界情况

| 场景 | 处理 |
|---|---|
| 未溢出（estimate < 阈值） | `should_trim=False` → 原样返回，`dropped_turns=0`，零行为变更 |
| 无 HumanMessage（纯系统/AI 序列） | 无法定义轮次 → no-op 全留（安全退化） |
| 用户轮 ≤ max_recent_turns(+首轮) | no-op，全留 |
| 含 tool_calls 的轮被裁 | 切点恒在 HumanMessage 边界 → 保留部分无悬空 tool_use/tool_result（单测覆盖） |
| 首轮片段与最近 N 轮窗重叠 | 去重，直接 `leading + remainder[窗起:]`，不重复拼首轮 |
| disabled | factory 不构造 WorkingWindow，`build_react_graph(working_window=None)` → 与 CM-2 前完全一致 |
| window 裁后仍超 compressor 阈值 | compressor 第二道闸接手摘要中段（协同，非互斥） |

### 4.6 可观测（零债"可观测齐全"）

- `helix_cm_working_window_trim_total{outcome}`（counter）—— `outcome=trimmed/noop`，看 window 实际触发率（高 trimmed 率 ⇒ 省了多少 compressor LLM 调用）。
- `helix_cm_working_window_dropped_turns`（gauge）—— 最近一次裁掉的用户轮数，观测裁剪深度。
- 复用 compressor 既有 `estimate_tokens`，不新造估算 metric；compressor 自身的压缩日志不动 → 通过"trim 触发但 compressor 未触发"的差值即可读出 LLM 节省量。

### 4.7 测试 & 验收（CM-2 Exit）

- **unit（≥85%，纯核心）**：token-gate（未溢出 no-op / 溢出触发）；轮次裁剪保留首轮 + 最近 N 轮；HumanMessage 边界切**不破 tool_use↔tool_result 对**（含 tool_calls 轮被裁）；leading SystemMessage 冻结；无 HumanMessage / 轮数不足 / 首轮与窗重叠三种边界 no-op 或去重；`dropped_turns` 计数正确。
- **integration（真 graph）**：scripted LLM + 构造一段长 history（>N 轮且超阈值）→ 发给 LLM 的 prompt 只含 首轮+最近 N 轮 且 tool 对完整；checkpoint 仍含完整 history（下一 turn 可重新加载）；与 compressor 协同（裁后 < 阈值 → compressor 不触发）。
- **零债 6 条**：无 TODO/FIXME；§4 与实现一致；metric 齐全；CI 8/8 + CodeQL 无新增 high；现有 compressor / agent_node 行为不回归（disabled + 未溢出双路 no-op 验证）。

### 4.8 Mini-ADR（CM-2 锁定）

| ID | 决策 |
|---|---|
| **CM-C1** | **token-gate 轮次裁剪**：仅当 `estimate_tokens >= threshold` 才裁，未溢出完全 no-op（零行为变更）；不做无条件裁剪，避免改变未溢出对话的 prompt |
| **CM-C2** | 裁剪点**恒为 HumanMessage 边界**，天然保 ToolCall↔ToolResult 配对（OpenClaw #1084），不引入 tool-pair 修复逻辑（在正确的边界切就无需修复） |
| **CM-C3** | **保首轮 + 最近 N 轮**：呼应 compressor head/tail 哲学，对抗"裁掉初始任务目标"；首轮是廉价的目标锚（与 CM-0 recitation 互补，不依赖其开启） |
| **CM-C4** | **只裁当次 prompt 视图，不改 checkpoint**（沿用 compressor 同款语义）：被裁的中间轮无永久丢失，与 CM-3 压缩前 flush 解耦（CM-3 服务 compressor 的有损摘要，window 不需要它兜底） |
| **CM-C5** | window 与 compressor **协同非互斥**：window 第一道（注入前、廉价）、compressor 第二道（注入后、LLM），默认同阈值，多数轻溢出由 window 解决省 LLM，重溢出 compressor 兜底 |
| **CM-C6** | 复用 `compressor.estimate_tokens` + leading-system 分离逻辑（DRY，口径一致），不另造估算；window 不改 `AgentState`/`ToolResult`/`ToolSpec`，无迁移 |

### 4.9 PR 切分（CM-2）

1. **CM-2 PR1 — 滑窗纯核心（CI 全测）**（已实现）：`context/working_window.py`（`TrimResult` + `trim_to_recent_turns` 纯函数 + `WorkingWindow` dataclass + token-gate，复用 `compressor.estimate_tokens`）+ 13 unit（token-gate / 保首轮 / 配对完整 / leading 冻结 / 三种边界 / dropped_turns / 不可变）。**不接图、不动 protocol**（pure-core-先行，对齐 CM-0 PR1 / CM-1 PR1 节奏）。
2. **CM-2 PR2 — 接线（端到端）**（已实现）：`WorkingMemoryPolicy` → `PolicySpec.working_memory`（保守默认 ⇒ 现有 manifest 零行为变更）；factory 构造 `WorkingWindow` 传 `build_react_graph`（新 `working_window` 参）；`agent_node` 取 history 后、注入前插 `working_window.apply`（`outcome=trimmed/noop` 计数 + dropped_turns gauge）；`helix_cm_working_window_trim_total{outcome}` + `helix_cm_working_window_dropped_turns`；3 集成测（长 history 裁剪 + checkpoint 完整 + 未溢出 no-op + working_window=None 原路径）+ 4 protocol policy 测（默认/自定义/边界拒绝/extra-forbid）。991 orchestrator + 311 protocol 回归绿。**→ CM-2 完成**。

> 每个 PR 在本 §4 基础上局部细化；ITERATION-PLAN 增 CM-2 backlog，ship 后回填 `[x]`+PR 号（[memory:iteration-plan-sync]）。

---

## 5. CM-3 详细设计 —— 压缩前 flush（中段被摘要吞掉前抢救进长期记忆）

> **目标**：compressor 把对话中段 LLM 摘要后**丢弃**前，先把中段的耐久要点 flush 进长期记忆（`memory_item`），让长任务跨多次压缩不丢关键决策。映射框架报告 A3（deer-flow `summarization_hook` 压缩前 flush + Anthropic structured note-taking + OpenClaw pre-compaction memory flush，已是标准范式）。
>
> **核心策略**：**复用 `memory_writeback` 的抽取管道**——不新造记忆写入逻辑，而是把 writeback 节点内联的"render→LLM 抽取→parse→embed→write + blocked/DLQ 处理"抽成共享函数，run-末 writeback 与压缩前 flush 都调它；compressor 暴露一个 `on_pre_compaction(middle)` 回调，在 `_compress_once` 丢弃中段前 await 它。

### 5.1 关键约束（接缝核准结论，已源码核准）

| 约束 | 事实 | file:line |
|---|---|---|
| **中段被摘要后即丢弃，无 flush** | `_compress_once` 把 `split.middle` 交摘要 LLM → 换成单条 `<context-summary>` SystemMessage，中段原文不再进上下文、也不落盘 | `context/compressor.py:274-293`（`_compress_once`） |
| **summary 是 in-context 易逝、非耐久** | 摘要只在本次 prompt，后续轮可能被再压缩；不进 `memory_item`、不可跨 run 检索 → 中段关键决策无耐久副本 | `context/compressor.py:288-293` |
| **writeback 只在 run 末触发，且抽取的是压缩后的 state** | run 末 `memory_writeback_node` 从 `state["messages"]` 抽取——此时中段已是 summary（原文已丢），故 run-末抽取**抓不到**被压缩吞掉的中段细节 | `graph_builder/memory.py:240-319`；compress 只改局部 prompt 不改 checkpoint，但中段一旦被摘要替换，后续 state 即不含原文 |
| **writeback 抽取管道可复用** | `memory_writeback_node` 内联：`_render_trajectory` → `llm_caller` 抽取 → `parse_extracted_memories` → `embedder.embed` → `memory_store.write`，含 `MemoryInjectionBlockedError`/DLQ/best-effort 处理 | `graph_builder/memory.py:250-319`、`:58-125`（_render/parse 辅助） |
| **compress 在 agent_node 内调用，有 token+config** | `agent_node` 行 256 取 `token=cancellation_token(config)`，行 317-318 调 `compress`；tenant/user/thread 可经 `configurable_uuid(config, ...)` 解析（flush 回调内部解析，免 reorder） | `graph_builder/builder.py:256/317-318`、`graph_builder/_config.py:50`（`cancellation_token`）、`memory.py:244-248`（config→uuid） |
| **flush 与 writeback 同 gate（per-user + memory 启用）** | 两者都仅在 `memory.long_term` 启用 + `MemoryEnv` 有 store+embedder + 运行带 `user_id` 时有意义；factory `_build_memory_nodes` 已是此 gate | `agent_factory.py:1151-1189`；writeback no-op when no user_id（`memory.py:246-247`） |

> **结论**：CM-3 = 抽 `flush_messages_to_memory` 共享函数（行为保持地从 writeback 节点提取）+ compressor `compress(on_pre_compaction=...)` 回调；factory 在 memory 启用时构造一个 config-bound flush 回调传 `build_react_graph`，agent_node 调 compress 时绑定该回调。flush 抽取的是**即将丢弃的中段** `split.middle`（run-末 writeback 抓不到的内容）。

### 5.2 设计：共享抽取核心 + compressor 回调（两段）

```
context/compressor.py  ContextCompressor.compress(messages, on_pre_compaction=None)
  └─ _compress_once：split → 若有 on_pre_compaction：await on_pre_compaction(split.middle)   ← 丢弃前 flush
                     → 摘要中段 → 换成 <context-summary> → 返回（中段原文此刻才丢）

graph_builder/memory.py  flush_messages_to_memory(messages, *, store, embedder, llm_caller,
                                                   tenant_id, user_id, thread_id, token, dlq) -> int
  └─ render→LLM 抽取→parse→embed→write + blocked/DLQ/best-effort（从 writeback 节点抽出，二者共用）
  memory_writeback_node：解析 config → 调 flush_messages_to_memory → 返回 {}（变薄，行为不变）

agent_factory  _build_memory_nodes：memory 启用 + cc_policy.flush_before_compaction 时，
  构造 pre_compaction_flush 回调（绑 store/embedder/llm/dlq）→ build_react_graph(pre_compaction_flush=...)

graph_builder/builder.py  agent_node：调 compress 时若 pre_compaction_flush 非空，
  绑 async _flush(mid)=pre_compaction_flush(mid, config, token) 传 compress(on_pre_compaction=_flush)
  + 发 helix_cm_precompaction_flush_total{outcome} / helix_cm_precompaction_flush_memories
```

不变量：① flush 在中段**丢弃前**（即便后续摘要 LLM 失败抛 `ContextOverflowError`，要点已落盘）；② flush **best-effort**——`flush_messages_to_memory` 吞非 cancel 异常返 0，绝不阻断压缩或 run（沿用 writeback 语义）；③ 抽取**中段** `split.middle`（run-末 writeback 抓压缩后 state、抓不到原文，二者互补不重复）；④ 仅 memory 启用 + per-user 才动（gate 与 writeback 一致）。

### 5.3 与 writeback 的复用收敛（零债，不留并行抽取逻辑）

- `memory_writeback_node` 内联的 render→抽取→embed→write + `MemoryInjectionBlockedError`/DLQ/best-effort，**抽成模块级 `flush_messages_to_memory`**（参数化 messages/tenant/user/thread/token/store/embedder/llm/dlq，返回写入条数）。
- `memory_writeback_node` 变薄：解析 config→调共享函数→`return {}`，**行为完全保持**（现有 writeback 测试不改即过）。
- 压缩前 flush 复用同一函数，仅传入 `split.middle` 而非全 trajectory，且 tenant/user/thread 由 agent_node 的 config 解析。
- 共享函数内部 warning 日志措辞泛化（`memory.flush_blocked`/`memory.flush_failed`），summary 行（count）由各 caller 用自己 label 记（`memory.writeback`/`memory.precompaction_flush`），区分两路来源。

### 5.4 数据/协议变更

1. **`graph_builder/memory.py`**：新增模块级 `async def flush_messages_to_memory(...) -> int`（抽取核心）；`memory_writeback_node` 改调它。无新 import 之外的依赖。
2. **`context/compressor.py`**：`ContextCompressor.compress` + `_compress_once` 增可选 `on_pre_compaction: Callable[[Sequence[BaseMessage]], Awaitable[None]] | None = None`（默认 None ⇒ 与 CM-3 前完全一致）。`ContextCompressor` 本身**不持** store/embedder（保持 context 层纯净、不反向依赖 memory 层）——回调由上层注入。
3. **`build_react_graph`**：新参数 `pre_compaction_flush: PreCompactionFlush | None = None`（`PreCompactionFlush = Callable[[Sequence[BaseMessage], RunnableConfig, CancellationToken], Awaitable[int]]`）。agent_node 调 compress 时绑定 config+token。
4. **`ContextCompressionPolicy`**（`agent_spec.py`）增 `flush_before_compaction: bool = True`——memory 启用时默认开（A3 是 P1 默认能力）；memory 未启用则 factory 不构造回调，字段无副作用。
5. **factory `_build_memory_nodes`**：memory 启用 + `flush_before_compaction` 时构造 `pre_compaction_flush` 回调（绑 env.store/embedder/llm/dlq），随返回值传到 `build_react_graph`。
6. **不改 `AgentState`/`MemoryItem`/`MemoryStore` 契约**——flush 写的就是普通 `MemoryItem`（`source_thread_id` 标来源），与 writeback 同构。

### 5.5 边界情况

| 场景 | 处理 |
|---|---|
| memory 未启用 / 无 user_id | factory 不构造回调（或回调内 config 解析 user_id=None 即 no-op）→ 压缩照常，零 flush |
| 中段为空（head+tail 覆盖全部） | `_compress_once` 此前已抛 `ContextOverflowError`；flush 在 split 后、空中段不调回调（无内容可抽） |
| 摘要 LLM 失败 | flush 在摘要**前**已完成 → 要点已落盘；摘要失败照常抛 `ContextOverflowError` |
| flush 自身失败（抽取/embed/write 异常） | `flush_messages_to_memory` 吞非 cancel 异常返 0 + DLQ（有则入队）→ 压缩与 run 不受影响 |
| 抽取被 strict scanner 拒（`MemoryInjectionBlockedError`） | 共享函数捕获 → log + 返 0（与 writeback 一致，run 不受影响） |
| 多 pass 压缩 | 每个 `_compress_once` 丢弃各自中段前 flush（各 pass 中段不同，无重复）；上限 `max_passes` |
| 取消（RunCancelledError） | 共享函数 re-raise，沿 token 正常取消（不吞） |

### 5.6 可观测（零债"可观测齐全"）

- `helix_cm_precompaction_flush_total{outcome}`（counter）—— `outcome=flushed/empty/failed`，看压缩前 flush 触发与成败。
- `helix_cm_precompaction_flush_memories`（gauge）—— 最近一次压缩前 flush 写入的记忆条数。
- 共享函数内 warning（blocked/failed）+ 各 caller 的 count info 日志（`memory.precompaction_flush count=N` / `memory.writeback count=N`）区分来源。
- 复用现有 memory write 路径的 audit（`MemoryItem` 写入照常触发既有审计），不新增 audit。

### 5.7 测试 & 验收（CM-3 Exit）

- **unit（≥85%）**：`flush_messages_to_memory` 抽取核心（抽取→embed→write / 空抽取→0 / blocked→0 / 异常→DLQ 或 0 / 取消 re-raise）；`memory_writeback_node` 重构后行为保持（现有 writeback 测试不改即过）；compressor `on_pre_compaction` 在丢弃中段**前**被调、入参是 `split.middle`、回调失败不阻断压缩、None 回调与旧行为一致。
- **integration（真 graph）**：scripted LLM + 构造超阈值长 history → 触发 compaction → fake store 收到中段抽取的 `MemoryItem`；memory 未启用时零 flush（`pre_compaction_flush=None` 原路径）；`flush_before_compaction=False` 关闭。
- **零债 6 条**：无 TODO；§5 与实现一致；metric+log 齐全（无新增 audit）；CI 8/8 + CodeQL 无新增 high；writeback 行为不回归（共享重构后现有测试全过）。

### 5.8 Mini-ADR（CM-3 锁定）

| ID | 决策 |
|---|---|
| **CM-D1** | **复用 writeback 抽取管道**：抽 `flush_messages_to_memory` 共享函数，run-末 writeback 与压缩前 flush 共用；不新造第二套记忆写入逻辑（零债，writeback 行为保持） |
| **CM-D2** | flush 抽取**即将丢弃的中段** `split.middle`（run-末 writeback 抓压缩后 state、抓不到原文，二者互补不重复） |
| **CM-D3** | flush 在中段**丢弃前** + 摘要 LLM **前**（要点先落盘，摘要失败也不丢）；**best-effort**（吞非 cancel 异常，绝不阻断压缩/run） |
| **CM-D4** | compressor **不反向依赖 memory 层**：`ContextCompressor` 仅暴露 `on_pre_compaction` 回调，store/embedder 由上层（factory→agent_node）注入，保 context 层纯净 |
| **CM-D5** | flush gate 与 writeback 一致（memory 启用 + per-user）；`ContextCompressionPolicy.flush_before_compaction` 默认 True（memory 启用时即生效），memory 未启用则无副作用 |
| **CM-D6** | tenant/user/thread 由 flush 回调**内部从 config 解析**（agent_node 免在 compress 前 reorder tenant 解析）；写入即普通 `MemoryItem`（`source_thread_id` 标来源），不改 `MemoryItem`/`MemoryStore` 契约 |

### 5.9 PR 切分（CM-3）

1. **CM-3 PR1 — 共享抽取核心 + compressor 回调（CI 全测）**（已实现）：`graph_builder/memory.py` 抽 `flush_messages_to_memory`（行为保持地从 `memory_writeback_node` 提取，节点变薄；`log_label` 区分两路来源、writeback 日志串字节不变）；`context/compressor.py` `compress`/`_compress_once` 增 `on_pre_compaction` 回调（丢弃中段前且摘要前 await）+ 导出 `PreCompactionHook` + 7 unit（抽取核心各路径 / writeback 重构不回归 / compressor 回调时序+入参+None 等价）。**不接 factory、不动 policy**（pure-core 先行，对齐 CM-1/CM-2 PR1）。
2. **CM-3 PR2 — 接线（端到端）**（已实现）：`ContextCompressionPolicy.flush_before_compaction`（默认 True）；`memory.py` `make_pre_compaction_flush`（config-bound 回调，no-user no-op）；factory `_build_memory_nodes` 返 3 元、write_back ∧ flush_before_compaction 时构造回调传 `build_react_graph`；agent_node 调 compress 时绑定 config+token 的 `on_pre_compaction`（`outcome=flushed/empty` 计数 + memories gauge）；`helix_cm_precompaction_flush_total{outcome}` + `helix_cm_precompaction_flush_memories`；2 集成测（compaction 触发 flush 到 fake store + memory 未启用零 flush）+ 3 protocol policy 测（默认/关闭/extra-forbid）。1000 orchestrator + 78 protocol 回归绿。**→ CM-3 完成**。

> 每个 PR 在本 §5 基础上局部细化；ITERATION-PLAN 增 CM-3 backlog，ship 后回填 `[x]`+PR 号（[memory:iteration-plan-sync]）。

---

## 6. CM-4 详细设计 —— Reranker 接通长期记忆召回（"配置都摆好就差接线"）

> **目标**：把已有的 cross-encoder reranker 接进长期记忆召回（J.3）——Hybrid（向量+全文+RRF）召回**更宽的候选**后，用 reranker 重排到 top-k，再注入上下文。映射框架报告修订③ B5（cross-encoder rerank 是 RAG 末段公认最高 ROI 一环，+33~40% 准确率 / 仅 +120ms；多跳查询 ROI 最强）。优先级由 P2 **提到 P1**。
>
> **核心事实（已源码核准）**：reranker 基建**已完整存在且已接入知识检索（J.5）**——`Reranker` Protocol + `LLMReranker` + `ResolvingReranker`/`DynamicResolvingReranker` + `resolve_reranker`（control-plane），`KnowledgeRetriever` 已"宽召回→rerank→top-k"。**唯一缺口**：J.3 长期记忆召回 `memory_recall_node` 直接返回 RRF top-k、未接 rerank。CM-4 = **复用同一 reranker 抽象与实例**接进记忆召回，**不新造任何 reranker**。

### 6.1 关键约束（接缝核准结论，已源码核准）

| 约束 | 事实 | file:line |
|---|---|---|
| **reranker 抽象已存在** | `Reranker` Protocol（`rerank(query, documents, top_k, tenant_id) -> list[int]`）+ `LLMReranker`（LLM 排序、解析失败回落输入序、绝不破检索） | `tools/knowledge.py:54-119` |
| **知识检索已用 reranker（待镜像的范式）** | `KnowledgeRetriever`：宽召回 `recall_limit=20` → RRF fuse → `_rerank`（无 reranker 直接 fused[:limit]；有则 rerank 候选 content → 重排 top-k） | `tools/knowledge.py:122-187` |
| **control-plane 已构造一个 reranker** | `reranker = DynamicResolvingReranker(...)` 已建并传给知识 retriever；读 live 平台 rerank 配置、无凭证降级到 fused 序 | `control-plane/app.py:834-840`；`runtime.py:541-573`（`resolve_reranker`/`DynamicResolvingReranker`） |
| **记忆召回是唯一未接 rerank 的检索路径** | `memory_recall_node`：embed→`memory_store.retrieve(query_embedding, query_text, limit=top_k)`（内部 Hybrid+RRF）→ redact → 直接返回，**无 rerank** | `graph_builder/memory.py:187-219` |
| **reranker 经 Env 注入（embedder 同款范式）** | `MemoryEnv{store, embedder, dlq, tenant_config_store}` 由 control-plane 构造注入 factory；embedder 即 `DynamicResolvingEmbedder`——reranker 应循同一注入路径加 `MemoryEnv.reranker` | `agent_factory.py:163-187`（MemoryEnv）、`app.py:862-869`（构造） |
| **召回 best-effort、per-user gate** | recall 失败 log+继续（无记忆）；无 user_id no-op；rerank 接入须保持此契约（rerank 失败降级到 RRF 序，不丢全部记忆） | `graph_builder/memory.py:193-216` |
| **rerank 抽象在 tools 层（可被 graph_builder 下依赖）** | `Reranker` 在 `tools/knowledge.py`，control-plane runtime 已从此处导入 → `graph_builder/memory.py` 导入 `Reranker` 是向下依赖、无环 | `tools/knowledge.py` 仅依赖 `tools/registry`+`llm`，不依赖 graph_builder |

> **结论**：CM-4 = `make_memory_recall_node` 加可选 `reranker` 注入；召回**更宽候选**（`max(top_k, _MEMORY_RERANK_RECALL_LIMIT)`，仅当 reranker 存在）→ rerank 重排到 top_k → redact。`MemoryEnv.reranker` 字段 + factory 透传 + control-plane 把**已建的** `DynamicResolvingReranker` 一并传给 `MemoryEnv`（一行激活）。复用知识检索同一 reranker 抽象与实例，零新造。

### 6.2 设计：宽召回 → rerank → top-k（镜像知识检索）

```
memory_recall_node（reranker 注入，可选）
  embed(task) → memory_store.retrieve(limit = max(top_k, 20) if reranker else top_k)  ← 宽召回
  └─ reranker 存在：reranker.rerank(query=task, documents=[m.content], top_k, tenant_id)
       → 按返回 index 重排 → 取 top_k
       （rerank 内部已 best-effort：LLMReranker 解析失败回落输入序；
         ResolvingReranker 无凭证降级 fused 序；外再裹一层 → 失败回落 candidates[:top_k]）
  └─ reranker 为 None：candidates[:top_k]（= 现状，零行为变更）
  → redact 最终 top_k → recalled_memories
```

不变量：① reranker 为 None ⇒ **零行为变更**（仍 retrieve(top_k) 直接返回，不宽召回）；② rerank 在 **redact 前**（对原始 content 判相关性，redact 最终 top_k）；③ rerank **best-effort 降级到 RRF 序**（绝不因 rerank 失败丢全部记忆）；④ per-user gate / 召回 best-effort 契约不变；⑤ **复用知识检索同一 reranker 实例**（control-plane 一个 `DynamicResolvingReranker` 喂知识 + 记忆两路）。

### 6.3 数据/协议变更

1. **`graph_builder/memory.py`**：`make_memory_recall_node` 加可选 `reranker: Reranker | None = None`（从 `orchestrator.tools.knowledge` 导入 `Reranker`，向下依赖无环）；召回宽度 `recall_limit = max(top_k, _MEMORY_RERANK_RECALL_LIMIT)` 当 reranker 存在；新增 `_rerank_memories` 私有助手（镜像 `KnowledgeRetriever._rerank`，含 best-effort 回落 `candidates[:top_k]`）。`_MEMORY_RERANK_RECALL_LIMIT = 20`（对齐知识检索）。
2. **`MemoryEnv`**（`agent_factory.py`）加 `reranker: Reranker | None = None` 字段（embedder 同款注入语义）。
3. **factory `_build_memory_nodes`**：`make_memory_recall_node(..., reranker=env.reranker)` 透传。
4. **control-plane `app.py`**：`MemoryEnv(..., reranker=reranker)`——`reranker`（`DynamicResolvingReranker`）已在 `:834` 为知识检索构造，CM-4 仅多传一处（一行激活，无新建 reranker）。
5. **不改 `MemoryStore.retrieve` 契约**——宽召回只是传更大的 `limit`；不改 `Reranker` Protocol；不改 `MemoryItem`。
6. **可观测**：`uplift_metrics` 加 `record_memory_rerank(*, outcome)`（`reranked`/`skipped`/`degraded`），与现有 `record_memory_retrieval` 并列。

### 6.4 边界情况

| 场景 | 处理 |
|---|---|
| reranker 为 None（未配置/无凭证降级 None） | 不宽召回、retrieve(top_k) 直接返回 → 现状，零行为变更 |
| 候选数 ≤ top_k | rerank 仍可调（重排顺序），或直接返回——top_k 截断即全集，rerank 仅改序，调用无害 |
| rerank 抛异常 | 外层 best-effort 捕获 → 回落 `candidates[:top_k]`（RRF 序），记 `degraded`；绝不丢全部记忆 |
| reranker 内部无凭证（ResolvingReranker） | reranker 自身降级返回 fused 序 index → 等价不重排 |
| 召回为空 | rerank 不调，返回空（现状） |
| 取消（RunCancelledError） | rerank 在召回 try 内，cancel 照常 re-raise（不吞） |

### 6.5 测试 & 验收（CM-4 Exit）

- **unit（≥85%）**：`make_memory_recall_node` 注入 fake reranker（返回逆序 index）→ 召回结果按 rerank 序、截到 top_k；宽召回 limit 正确（reranker 存在取 `max(top_k,20)`、不存在取 top_k）；rerank 抛异常 → 回落 RRF 序不丢记忆；reranker=None → 与现状字节一致；rerank 在 redact 前。
- **integration（真 graph）**：scripted reranker + InMemoryMemoryStore 多条记忆 → recall 注入的 `recalled_memories` 顺序由 reranker 决定；control-plane MemoryEnv 携带 reranker（PR2 验证传入）。
- **零债 6 条**：无 TODO；§6 与实现一致；metric（`record_memory_rerank`）齐全；CI 8/8 + CodeQL 无新增 high；reranker=None 路径不回归（现有 memory recall 测试不改即过）。
- **效益数字（诚实标注）**：+33~40% 是厂商级数字；helix 叠加增益（pgvector+RRF+rerank）业界无统一 benchmark，**自测留给 CM-N5（LongMemEval/LoCoMo）**，不在 CM-4 内声称具体百分比。

### 6.6 Mini-ADR（CM-4 锁定）

| ID | 决策 |
|---|---|
| **CM-E1** | **复用既有 `Reranker` 抽象（`tools/knowledge.py`），零新造**；control-plane 一个 `DynamicResolvingReranker` 实例同时喂知识检索（J.5）与记忆召回（J.3） |
| **CM-E2** | **宽召回→rerank→top-k**，镜像 `KnowledgeRetriever`；仅当 reranker 存在才宽召回（`max(top_k,20)`），否则 retrieve(top_k) 直返（零行为变更） |
| **CM-E3** | rerank **best-effort 降级到 RRF 序**：reranker 内部已回落（LLM 解析失败 / 无凭证），外再裹一层 → 失败回落 `candidates[:top_k]`，绝不因 rerank 丢全部记忆 |
| **CM-E4** | reranker 经 **`MemoryEnv.reranker` 注入**（embedder 同款），factory 不构造、只透传；`None` → 无 rerank（现状） |
| **CM-E5** | rerank 在 **redact 前**（对原始 content 判相关性，redact 最终 top_k）；per-user gate / 召回 best-effort 契约不变 |
| **CM-E6** | 效益自测留给 **CM-N5**（LongMemEval/LoCoMo）；CM-4 不在文档声称具体增益百分比（厂商数字不横比，[memory:N5 纪律]） |

### 6.7 PR 切分（CM-4）

1. **CM-4 PR1 — 记忆召回接 rerank（orchestrator，CI 全测）**（已实现）：`make_memory_recall_node` 加可选 `reranker` + 宽召回 `max(top_k,20)` + `_rerank_memories`（best-effort 回落 `candidates[:top_k]`）；`MemoryEnv.reranker` 字段；factory 透传 `env.reranker`；`record_memory_rerank{outcome=reranked/degraded}` + 5 unit（重排+截 top_k / 宽召回 limit / None 零变更 / 失败降级不丢记忆 / 空召回跳过）。1005 orchestrator + 302 common 回归绿。
2. **CM-4 PR2 — control-plane 激活（last mile）**（已实现）：`app.py` `MemoryEnv(..., reranker=reranker)`（复用 `:834` 已建的 `DynamicResolvingReranker`，喂知识检索与记忆召回两路）+ orchestrator factory 透传测（`_build_memory_nodes` 把 `MemoryEnv.reranker` 传到 recall 节点、spy reranker 被调）+ 文档标 done + ITERATION-PLAN 回填。**→ CM-4 完成**。

> 每个 PR 在本 §6 基础上局部细化；ITERATION-PLAN 增 CM-4 backlog，ship 后回填 `[x]`+PR 号（[memory:iteration-plan-sync]）。

---

## 7. CM-5 详细设计 —— 可恢复压缩（超大工具结果外部化 + 虚拟引用）

> **目标**：把工具结果截断从"永久丢弃"变为"可恢复"——完整输出落 workspace 文件，ToolMessage 保留截断内容 + 引用 footer，agent 需要时用既有读工具换回。映射框架报告 B6 + 修订 N3（Manus 法则"压缩必须可恢复——丢正文留引用"；Anthropic tool result clearing 是同方向的官方背书）。
>
> **框架报告假设修正（2026-06-10 用户拍板）**：报告原句"已有 artifact 表 + ObjectStore，天然适配"经源码取证不成立——artifact 表只是**元数据注册**（`ArtifactVersionRow.path_in_workspace` 指向 workspace 卷文件，内容基底本就是 workspace 文件），ObjectStore M0 未接 artifact 内容（`archived_object_key` 不填充），agent 也没有 read_artifact 工具。故 CM-5 存储基底 = **workspace 文件**（= artifact 的实际内容基底），**不入 artifact 表**（避免工具结果残片污染用户产物列表）、**不动 ObjectStore**。

### 7.1 关键约束（接缝核准结论，已源码核准）

| 约束 | 事实 | file:line |
|---|---|---|
| **截断在工具内部、策略各异（领域知识）** | bash/exec_python：`format_sandbox_outcome` head-trim 20k；http：body 20k tail + headers 4k 分离 cap；mcp：middle-trim 20k 留头尾；web_search：单条结果 4096；read_file：sandbox snippet 内 `text[:cap]`（全文不出 supervisor 线） | `tools/sandbox.py:38,314-322`、`tools/bash.py:138`、`tools/http.py:53-54,216-236`、`tools/mcp.py:63,642-660`、`tools/web_search.py:36,232`、`tools/file_ops.py:177` |
| **截断后完整结果即丢、LLM 不知情** | `ToolResult.meta["truncated"]` 在工具内生成，但 ToolMessage 只装 `content`+`tool_call_id`，meta 提取后丢弃；完整原文不落任何地方 | `graph_builder/builder.py:584,1232-1237`、`tools/registry.py:198-227` |
| **首批 4 工具的完整输出在 orchestrator 内存可得** | bash/exec_python：supervisor 返回**全量** `SandboxOutcome`，cap 在 orchestrator 侧 `format_sandbox_outcome`；http：`_format` 前全量 body 在内存；mcp：middle-trim 前全量在内存 | `tools/sandbox.py:314-322`、`tools/http.py:216`、`tools/mcp.py:642` |
| **CM-0 writer 范式可整体复用** | `WorkspaceFileWriter` Protocol（`write(*, rel, content)`）+ `SandboxWorkspaceWriter`（sandbox 原子写、自动建父目录）+ `workspace_writer_factory` 已从 factory 流到 builder（gate：persistent_workspace ∧ supervisor_client）+ best-effort 钩子先例 `_project_workspace_state` | `context/workspace_projection.py:130-134`、`tools/file_ops.py:759-774`、`agent_factory.py:120,595-621`、`builder.py:243,632,1126-1156` |
| **恢复工具已存在，不新造** | `read_file`（20k cap、无 offset——超 20k 文件用 exec_python/bash 切片/grep 是预期恢复路径，sandbox 内零网络成本）；`list_dir` 可发现 `.tool_results/` | `tools/file_ops.py:460-513` |
| **ToolResult 可向后兼容扩展** | frozen dataclass + 默认值字段追加不破现有构造点 | `tools/registry.py:198-227` |

> **结论**：CM-5 = `ToolResult` 加可选 `full_content` 字段（4 个工具截断时带出全文）+ tools_node 中央 best-effort 外部化（复用 CM-0 `workspace_writer_factory`，零新注入参数）+ ToolMessage 引用 footer。截断策略不动、无 schema 变更、无新工具。

### 7.2 设计：工具截留 → 中央外部化 → 引用 footer（三段）

```
工具内（截断策略不动，一行增量）
  按既有领域策略截断 content（head/tail/middle/分离 cap 各异）
  截断发生时：ToolResult(content=截断版, full_content=完整渲染, meta={"truncated": True, ...})

tools_node（中央，best-effort，复用 CM-0 writer）
  _invoke_tool 返回后：
    full_content 存在 ∧ workspace_writer_factory 存在 ∧ spec 非 read_only
      → rel = ".tool_results/<run_id>/<call_id>-<tool>.txt"
      → writer.write(rel=rel, content=full_content[:_OVERFLOW_MAX_CHARS])   # sandbox 原子写
      → ToolMessage.content += "\n\n<tool-result-overflow>output truncated; full output
         (N chars) saved to .tool_results/…; use read_file / exec_python / bash to inspect
         </tool-result-overflow>"
    写失败 / 无 writer / read_only → 维持现状（inline "...[truncated]" marker 仍在）
```

**职责分工（CM-F1，用户确认"不考虑成本也是最优"）**：截断**策略**是领域知识——web_search 按单条结果截、http 头/体分离 cap、skill_view 中段截留头尾、read_file 必须在 sandbox snippet 内截（否则超大文件全量过 supervisor HTTP 线）——统一 middleware 会抹掉这些语义，且 read_file 的 snippet 内截根本无法中央化。真正跨切面的只有**溢出后的处置**（外部化 + 引用），归中央一处。

不变量：① 未截断 ⇒ `full_content=None` ⇒ 零行为变更；② 无 writer（无持久 workspace / 无 supervisor）⇒ 现状截断，与今日字节一致；③ 外部化 best-effort——写失败不影响 run、不吞 cancel；④ footer 仅在**写成功**后追加（绝不引用不存在的文件）；⑤ read 类豁免双保险（见 7.4）。

### 7.3 数据/协议变更

1. **`tools/registry.py`**：`ToolResult` 加 `full_content: str | None = None`。契约：仅当 `content` 被截断**且**工具持有完整原文时填（与 `content` 同构的未截断渲染）；read_only 工具**禁止**填。无 protocol 包变更（ToolResult 是 orchestrator 内部类型）。
2. **首批 4 工具**（各一处增量）：`format_sandbox_outcome`（bash/exec_python 共用）截断时带出全量渲染；`HTTPTool._format` 截断时带出全量渲染（headers+body 未截版）；`MCPTool` middle-trim 时带出全量。
3. **新模块 `tools/overflow.py`**（纯函数，与 `tools/error_classifier.py` 同层级先例）：`overflow_rel_path(run_id, call_id, tool_name) -> str`、`render_overflow_footer(rel, total_chars) -> str`、`_OVERFLOW_MAX_CHARS = 2_000_000` 上限保护（超限只写前 2M + 文件尾注记）。
4. **`graph_builder/builder.py`**：tools_node 在 ToolMessage 构造处接外部化钩子 `_externalize_tool_overflow`（async、best-effort）；**复用既有 `workspace_writer_factory` 参数**——零新 build_react_graph 参数、gate 与 CM-0 投影完全一致。
5. **可观测**：`helix_cm_tool_overflow_total{outcome=externalized/degraded, tool}` counter + `helix_cm_tool_overflow_chars` gauge（builder 内定义，CM-2/CM-3 同款）+ 结构化日志 `tool.overflow {tool, rel, chars}`。不发 audit（agent 自身 workspace 的运行时产物，非平台代写状态；metrics+log 足够，区别于 CM-0 投影的 STATE_PROJECTED）。
6. **不加 policy 开关**：gate 行为 = workspace_writer_factory 存在与否（CM-0 投影同款先例）；无 manifest/policy 变更。

### 7.4 Read 类豁免（防 persist→read→persist 循环，设计铁律）

| 层 | 保险 |
|---|---|
| 工具契约 | read_only 工具（read_file/list_dir/skill_view/web_search/knowledge_search/list_artifacts…）**不填** `full_content`：web_search 单条自带 URL（Manus 式"丢正文留 URL"已天然满足）；read_file/skill_view 源文件可重读（且 read_file 全文根本不出 sandbox） |
| 中央双保险 | tools_node 外部化前检查 `spec.resolved_side_effect == "read_only"` ⇒ 跳过（防未来工具误填；测试锁死） |

循环场景推演：agent `read_file` 读回 `.tool_results/` 大文件 → read_file 是 read_only → 即使再截断也不外部化 → 无循环。

### 7.5 边界情况

| 场景 | 处理 |
|---|---|
| 未截断（绝大多数调用） | `full_content=None`，中央钩子零开销跳过 |
| 无持久 workspace / 无 supervisor（writer factory=None） | 现状截断，字节一致 |
| 写失败（sandbox 不可达/IO 错） | log + `outcome=degraded`，content 保持截断版（inline marker 仍在），run 不受影响 |
| 超大全文（如 500MB stdout） | `_OVERFLOW_MAX_CHARS=2M` 截顶写入 + 文件尾注记（防巨量 payload 过 supervisor HTTP 线） |
| 取消（RunCancelledError/CancelledError） | 外部化不吞 cancel，照常 re-raise |
| 并行工具调用 | 路径含 `call_id` 唯一，无写冲突 |
| `.tool_results/` 累积 | 生命周期复用 workspace 既有留存机制（每日备份 / 90 天 archive，J.15）；agent/用户可自删；**不新造清理机制**（明确 non-goal，非遗留债） |
| 工具本身 error（ToolMessage status=error） | 错误路径不外部化（错误摘要已有 500 cap + CM-1 advisory 负责） |

### 7.6 可观测（零债"可观测齐全"）

- `helix_cm_tool_overflow_total{outcome,tool}`：externalized / degraded 两值。
- `helix_cm_tool_overflow_chars`：最近一次外部化的全文字符数 gauge。
- 结构化日志 `tool.overflow`：{tool, rel, chars}；写失败 `tool.overflow_failed` WARNING。

### 7.7 测试 & 验收（CM-5 Exit）

- **unit（≥85%）**：4 工具截断时 `full_content` = 未截断渲染、未截断时 None；read_only 工具不填（锁契约）；`overflow_rel_path`/`render_overflow_footer` 纯函数；`_OVERFLOW_MAX_CHARS` 截顶；中央钩子——写成功加 footer、写失败 degraded 不加、read_only 跳过、cancel re-raise。
- **integration（真 graph + fake writer）**：超限 bash 输出 → ToolMessage 带 `<tool-result-overflow>` footer + fake writer 收到完整全文；无 writer → 与现状字节一致；checkpoint 里 ToolMessage 含 footer（被截内容可在后续 turn 经引用恢复）。
- **零债 6 条**：无 TODO；本 §7 与实现一致；metrics/log 齐全；CI 8/8；`full_content=None` 路径零行为变更（现有工具测试不改即过）。

### 7.8 Mini-ADR（CM-5 锁定）

| ID | 决策 |
|---|---|
| **CM-F1** | **截断策略留工具内（领域知识），中央只管溢出处置**——非成本折中而是职责正确：per-result cap / 头体分离 / middle-trim / sandbox 内截各有语义，middleware 统一化会抹掉且部分（read_file）无法中央化（2026-06-10 用户确认） |
| **CM-F2** | **存储基底 = workspace 文件** `.tool_results/<run_id>/<call_id>-<tool>.txt`；不入 artifact 表（防污染用户产物）、不接 ObjectStore（修正框架报告假设：artifact 内容基底本就是 workspace 文件） |
| **CM-F3** | **首批 bash/exec_python/http/mcp**（截断即永久丢失的 4 类）；web_search（URL 即引用）、read_file/skill_view/list_dir（源可重读）豁免；read_only 双保险防 persist→read→persist 循环 |
| **CM-F4** | **复用 CM-0 `workspace_writer_factory` + gate**（persistent_workspace ∧ supervisor_client），零新注入参数；无 sandbox 降级现状截断 |
| **CM-F5** | best-effort 铁律：写失败不影响 run、cancel re-raise、`_OVERFLOW_MAX_CHARS=2M` 上限防巨量 payload 过线；footer 仅写成功后追加 |
| **CM-F6** | **恢复路径 = 既有 read_file/exec_python/bash**（不新造 read 工具、不加 policy 开关、不发 audit）；`.tool_results/` 生命周期归 workspace 既有留存机制 |

### 7.9 PR 切分（CM-5）

1. **CM-5 PR1 — 纯核心（不接图）**（已实现，PR #508）：`ToolResult.full_content` 字段 + 4 工具截断时带出全文（bash/exec_python 经 `format_sandbox_outcome`、http、mcp）+ `tools/overflow.py` 纯函数（rel path / footer / 2M 上限）+ unit tests。
2. **CM-5 PR2 — 接线（收尾 CM-5）**（已实现）：`_invoke_tool` 成功路径接 `_externalize_tool_overflow` best-effort 钩子（复用 `workspace_writer_factory`，与 stage 内其他工具并行）+ metrics/日志 + 集成测（真 graph + fake writer / 无 writer 字节一致 / 写失败降级 / read_only 双保险）+ ITERATION-PLAN 回填。**→ CM-5 完成**。

> 每个 PR 在本 §7 基础上局部细化；ITERATION-PLAN 增 CM-5 backlog，ship 后回填 `[x]`+PR 号。

---

## 8. CM-6 详细设计 —— 检索质量：时间衰减 + MMR 去冗余

> **目标**：长期记忆召回从"纯相关性对称 RRF"升级为"新近度加权 + 结果多样性"——时间衰减让久未使用的记忆让位于新近记忆（OpenClaw temporalDecay 半衰期 30 天范式），MMR 去掉高度同质的候选（λ=0.7）。映射框架报告 B4（组件级有据：MMR/衰减各自经典；**叠加增益业界无统一 benchmark，自测留 CM-N5**，本设计不声称数字）。
>
> **管线顺序修正（2026-06-10 用户拍板，偏离范围表原句）**：范围表原句"`memory/sql.py:retrieve()` RRF 后加 MMR + 时间衰减"写于 CM-4 之前；CM-4 已把 cross-encoder rerank 放在 retrieve **之后**（orchestrator 侧）——若 MMR 在 retrieve 内做，rerank 按纯相关性重排会打散 MMR 的多样性选择，白做。修正为：**时间衰减进 retrieve()**（所有召回部署受益，含无 reranker），**MMR 放 orchestrator 召回管线末段**（rerank 之后）。最终管线：宽召回（RRF+衰减）→ rerank（相关性）→ MMR（去冗余）→ top_k → redact，每段职责单一。

### 8.1 关键约束（接缝核准结论，已源码核准）

| 约束 | 事实 | file:line |
|---|---|---|
| **RRF 融合在 Python 侧、两 store 镜像实现** | sql：两条独立 SELECT（HNSW 向量 + GIN 全文各取 `_HYBRID_RECALL_LIMIT=20`）→ `rrf_fuse` 融合 row id → `[:limit]`；in-memory 同构（jieba 关键词 + cosine） | `persistence/memory/sql.py:27,180-218`、`memory/memory.py:20,98-134` |
| **`rrf_fuse` 只返回排序项、不带分数** | `rrf_fuse(rankings, k=60) -> list[T]`——衰减加权需要分数 ⇒ 需并列新增带分版本，**不破现有调用方**（knowledge J.5 也用它） | `helix-common/search/rrf.py:29-55` |
| **两 store 各有 hybrid + 纯向量两条路径** | `query_text` 为 None/空 ⇒ 纯向量路径（cosine 距离序），无 RRF——衰减须两路都覆盖才一致 | `sql.py:115-186`、`memory.py:122-135` |
| **候选 embedding 随行返回（MMR 无阻碍）** | `_row_to_item` 转 `embedding=tuple(float,...)`；`MemoryItem.embedding` 必有 | `sql.py:30-52`、`protocol/memory_item.py:28-96` |
| **时间戳字段齐全** | `created_at` / `last_used_at`（初值=created_at，retrieve 访问刷新）/ `last_reviewed_at`；衰减用 `last_used_at`（被用即保鲜） | `models/memory_item.py:48-57`、migration 0017:53-57 |
| **CM-4 管线已定召回结构** | recall node：embed → `retrieve(limit=宽召回)` → `_rerank_memories`（best-effort）→ top_k → redact；`_MEMORY_RERANK_RECALL_LIMIT=20`；query embedding（`vectors[0]`）在手——MMR 可直接复用 | `graph_builder/memory.py:59,216-286` |
| **retrieve() 唯一在线调用方是 recall 路径** | consolidator 走 `list_purge_candidates` 非 retrieve；knowledge J.5 是独立 retriever——衰减只影响记忆召回，无旁路风险 | `sql.py:481-506` |
| **协议签名 sweep 范围** | retrieve 签名**不变**（衰减是内部重排）⇒ tools/eval doubles 无需同步改（[memory:protocol-sweep]） | `persistence/memory/base.py:53-72` |

> **结论**：CM-6 = helix-common 新增带分 RRF + 衰减 + MMR 纯函数；两 store retrieve() 内部接衰减（hybrid+纯向量两路）；orchestrator recall 节点 rerank 后接 MMR。签名零变更、协议零变更、schema 零变更。

### 8.2 设计：衰减进 retrieve，MMR 殿后（管线四段）

```
retrieve()（两 store 镜像）
  hybrid： vector top20 + keyword top20 → rrf_fuse_scored → score *= decay_factor(now - last_used_at)
  纯向量： similarity = 1 - cosine_distance/2 → score *= decay_factor(...)
  → 按衰减后分数排序 → [:limit]

decay_factor(age) = 0.5 + 0.5 * 2^(-age_days / 30)        # ∈ (0.5, 1]
  半衰期 30 天（OpenClaw 范式）；带 0.5 floor——老记忆最多减半、
  绝不被埋死（canonical 事实如用户偏好可能数月前写入，纯指数衰减
  180 天即衰到 1.6%，会制造"平台失忆"）

memory_recall_node（orchestrator，CM-4 结构上加一段）
  retrieve(limit=宽召回 20) → rerank（可选；改为全序输出 top_k=len(候选)，
    不再截断——否则 MMR 只能在已截集合内重排，无法从宽池换入多样候选，
    去冗形同虚设；最终截断职责移交 MMR 段，PR2 局部细化）
  → mmr_select(query_embedding, candidates, k=top_k, λ=0.7)   ← 新增，殿后
  → redact → recalled_memories
```

不变量：① retrieve 签名不变、返回仍是排序后 `list[MemoryItem]`；② 衰减只重排**候选窗口内**的相对顺序（宽召回 20 内），不改召回集合本身；③ MMR best-effort——异常回落输入序 `[:top_k]`（与 rerank 降级同款契约，绝不丢全部记忆）；④ MMR 在 redact 前（用原始 embedding）；⑤ `last_used_at` 刷新语义不动（自增强回路被"衰减只作用于排序、相关性仍是主因子"抑制，且 floor 限幅）。

### 8.3 数据/协议变更

1. **`helix-common/search/rrf.py`**：并列新增 `rrf_fuse_scored(rankings, k=60) -> list[tuple[T, float]]`（`rrf_fuse` 改为其薄包装，行为字节不变；knowledge J.5 调用方零感知）。
2. **`helix-common/search/decay.py`（新）**：`temporal_decay_factor(*, age: timedelta, half_life: timedelta = 30d, floor: float = 0.5) -> float`。
3. **`helix-common/search/mmr.py`（新）**：`mmr_select(*, query_embedding, candidates: Sequence[tuple[T, Sequence[float]]], k, lambda_=0.7) -> list[T]`——贪心 MMR：`λ*sim(q,c) - (1-λ)*max sim(c, selected)`，cosine；候选 embedding 维度不一致/为空时跳过该候选。
4. **`persistence/memory/sql.py` + `memory/memory.py`**：retrieve() 两路接衰减（`datetime.now(UTC)` 取齐一次；`last_used_at` 为 naive 的兼容按现有行为处理）；`_HYBRID_RECALL_LIMIT` 不变。
5. **`graph_builder/memory.py`**：`_mmr_memories` 私有助手（best-effort，镜像 `_rerank_memories` 契约）；插在 rerank 后、redact 前；**宽召回条件从 `reranker is not None` 扩为恒宽**（MMR 默认开 ⇒ `recall_limit = max(top_k, 20)` 恒成立——SQL 本就各取 20 候选融合，增量成本为零，只是融合后截断更晚）。
6. **可观测**：`record_memory_mmr(*, outcome)`（`applied`/`degraded`，`helix_cm_memory_mmr_total`，uplift_metrics 与 `record_memory_rerank` 并列）；persistence 衰减为确定性纯变换，不加 metric（debug 日志足够）。
7. **无 policy / tenant_config / schema / 协议变更**：λ、半衰期、floor 为模块常数（同 rerank 先例——质量增强不开 knob，调参等 CM-N5 评测基线就位后再议）。

### 8.4 边界情况

| 场景 | 处理 |
|---|---|
| 纯向量路径（query_text 空 / 全文无命中场景） | similarity = `1 - cosine_distance/2` 后同样衰减——两路语义一致 |
| `last_used_at` 缺失/异常（理论不可能，NOT NULL） | `max(age, 0)`——未来时间戳按 0 龄处理（时钟偏移防御） |
| 候选 embedding 全相同（极端同质） | MMR 第二项恒高 → 自动只选 top1 后按 λ 权衡，仍返回 k 个（贪心不死锁） |
| 候选数 ≤ top_k | MMR 仍跑（只改序不减量）；零候选直接返回 |
| MMR 计算异常（维度不齐等） | best-effort 回落输入序 `[:top_k]`，记 `degraded`，绝不丢记忆 |
| reranker 与 MMR 同时存在 | 顺序 rerank→MMR：先按语义相关性重排，再在高相关集合内去冗余（设计本意） |
| 老部署回归担忧 | 衰减 floor 0.5 + 只作用候选窗口内排序 ⇒ 高相关老记忆仍可召回，只在同分竞争时让位新记忆 |

### 8.5 可观测（零债"可观测齐全"）

- `helix_cm_memory_mmr_total{outcome=applied/degraded}`（uplift_metrics）。
- persistence retrieve debug 日志保持现状（衰减为确定性变换，不另设 metric）。

### 8.6 测试 & 验收（CM-6 Exit）

- **helix-common unit**：`rrf_fuse_scored` 分数正确 + `rrf_fuse` 包装后行为字节不变（现有 rrf 测试不改即过）；`temporal_decay_factor`（0 龄=1 / 30 天=0.75 / ∞→floor 0.5 / 负龄钳 0）；`mmr_select`（同质候选被去冗、λ=1 退化为纯相关序、空候选/维度不齐跳过、k≥候选数全返）。
- **persistence unit（in-memory）+ integration（SQL）**：同相关性老/新两条记忆 → 新者排前；高相关老记忆仍胜低相关新记忆（floor 保护）；纯向量路径同样衰减；retrieve 签名/返回类型不变（现有 store 测试不改即过）。
- **orchestrator unit**：recall 节点冗余候选（近重复 embedding）被 MMR 去冗；MMR 异常 → 回落输入序不丢记忆；rerank→MMR 顺序（scripted reranker + spy）；恒宽召回 limit=20。
- **零债 6 条**：无 TODO；本 §8 与实现一致；metric 齐全；CI 8/8；叠加增益自测留 CM-N5（诚实标注，不声称数字）。

### 8.7 Mini-ADR（CM-6 锁定）

| ID | 决策 |
|---|---|
| **CM-G1** | **管线顺序：宽召回（RRF+衰减）→ rerank → MMR → top_k → redact**；MMR 必须在 rerank 后（否则被纯相关性重排打散，白做）——修正范围表"都在 retrieve()"原句（2026-06-10 用户拍板） |
| **CM-G2** | **时间衰减进 retrieve()**（两 store 镜像、hybrid+纯向量两路一致），所有召回部署受益（含无 reranker）；基于 `last_used_at`（被用即保鲜，频率×新近度复合信号） |
| **CM-G3** | **衰减带 floor**：`0.5 + 0.5 * 2^(-age_days/30)`——半衰期 30 天（OpenClaw 范式），老记忆最多减半绝不埋死（canonical 事实保护，防"平台失忆"） |
| **CM-G4** | **MMR λ=0.7（OpenClaw 范式）、cosine、greedy**；纯函数进 helix-common search 包与 rrf 并列；无 policy knob（同 rerank 先例，调参等 CM-N5 基线） |
| **CM-G5** | **召回恒宽 `max(top_k, 20)`**（MMR 默认开）；SQL 本就各取 20 候选融合，增量成本零；`rrf_fuse_scored` 并列新增、`rrf_fuse` 薄包装零破坏（knowledge J.5 零感知） |
| **CM-G6** | MMR **best-effort 降级输入序**（与 rerank 同契约）；衰减为确定性纯变换不设 metric；**叠加增益不声称数字，自测留 CM-N5** |

### 8.8 PR 切分（CM-6）

1. **CM-6 PR1 — 纯核心（helix-common，不接任何调用方）**（已实现，PR #511）：`rrf_fuse_scored`（`rrf_fuse` 薄包装化）+ `search/decay.py` + `search/mmr.py` + unit tests。
2. **CM-6 PR2 — 接线（收尾 CM-6）**（已实现）：两 store retrieve() 接衰减（hybrid+纯向量）+ orchestrator recall 节点 `_mmr_memories`（rerank 后、redact 前；rerank 改全序输出由 MMR 殿后截断，见 §8.2）+ 恒宽召回 + `record_memory_mmr` + persistence/orchestrator tests + ITERATION-PLAN 回填。**→ CM-6 完成**。

> 每个 PR 在本 §8 基础上局部细化；ITERATION-PLAN 增 CM-6 backlog，ship 后回填 `[x]`+PR 号。

---

## 9. CM-7 详细设计 —— 结构化摘要 + 记忆写入显式操作（A-MEM/Mem0 范式）

> **目标**：两个独立面（2026-06-10 用户拍板"①+② 全做，② 只在 run 末 writeback"）：
> ① **compressor 摘要强化**——`<context-summary>` 加"背景非指令"强语义（Hermes SUMMARY_PREFIX 范式，防模型把历史摘要当新指令重复执行）+ 结构化 sections + 二次压缩从"摘要的摘要"改为**增量更新前次摘要**（Hermes `context_compressor.py:659-660` 范式）；
> ② **记忆写入显式操作**——run 末 writeback 抽取后先检索相似既有记忆，LLM 判 `ADD/UPDATE/DELETE/NOOP` 再落库（Mem0 extract→update，2504.19413：结构化 note + 显式操作省 85~93% token、矛盾可废弃）。**这是真能力缺口非重复机制**：consolidator 只会合并簇新建 consolidated + 清噪声（`memory_consolidator.py:584-691`），没有"更新/废弃被新事实矛盾的旧记忆"能力——"喜欢浅烘"后来变"喜欢深烘"，今天两条都活着。

### 9.1 关键约束（接缝核准结论，已源码核准）

| 约束 | 事实 | file:line |
|---|---|---|
| **摘要无"背景非指令"语义** | `<context-summary>` 只是裸标签包 bullets；summariser prompt 要求 3-7 bullets 自由文本，无任何 reference-only 声明 | `context/compressor.py:66-75,317-320` |
| **二次压缩 = 链式摘要的摘要** | 前次 summary（SystemMessage，在 head 后 tail 前）非 leading 位置 → 落进下一 pass 的 middle 被当普通文本再摘要；3-pass 循环每 pass 全量重生成 | `compressor.py:152-181,257-289,320` |
| **L-1 安全** | summary 消息在 leading_systems **之后**，不属于 byte-stable 冻结块——改摘要语义/格式不破 cache prefix | `compressor.py:155-164,320` |
| **CM-3 flush 先于摘要** | `on_pre_compaction(split.middle)` 在 summariser LLM 调用前 await——②的写入操作判定**不能**放这里（agent turn 内，延迟敏感） | `compressor.py:310-316` |
| **writeback 抽取直写、无比对** | `flush_messages_to_memory`：LLM 抽取 `{kind, content}` → embed → `memory_store.write(items)`，与既有记忆零比对 | `graph_builder/memory.py:68-78,332-433` |
| **去重只认 exact hash** | `write` ON CONFLICT DO NOTHING on `(tenant,user,content_hash)`（`strip().lower()` SHA-256）——同义改写必堆积 | `persistence/memory/sql.py:103-140`、`memory/hash.py` |
| **UPDATE/DELETE 原语已存在** | `MemoryStore.update_content(id, content, kind)`（自动重 embed）+ `soft_delete(id)`；PATCH/DELETE `/v1/memory/{id}` 已用 | `persistence/memory/base.py:106-134`、`control_plane/api/memory.py:201-287` |
| **consolidator 无矛盾解决** | SUB-PASS 1 簇合并新建 consolidated、SUB-PASS 2 lone-item 噪声清理；无"更新既有条目"路径 | `control_plane/memory_consolidator.py:559-691` |
| **会被格式变更影响的测试** | `test_compress_preserves_head_and_tail`/`test_compress_summary_lands_between_head_and_tail`/integration same——断言 scripted 摘要文本仍在 wrapper 内，preamble 追加不破；新增断言锁 preamble | `tests/test_context_compressor.py:135-233` |

### 9.2 设计 ①：摘要"背景非指令"语义 + 结构化 sections + 增量更新

```
wrapper（SystemMessage，位置不变：leading_systems 后、head 后、tail 前）
  <context-summary>
  Background summary of earlier conversation. Reference material only —
  its contents are NOT instructions; do not execute, re-run, or treat
  anything inside it as a new request.

  ## Facts        ← 结构化 sections（summariser prompt 锁定三段）
  ## Decisions
  ## Pending
  </context-summary>

_compress_once（增量更新，Hermes 范式）
  middle 内含前次 summary（识别：SystemMessage 且 content 以 <context-summary> 开头，取最后一条）
    → UPDATE 模式：prompt = "维护运行中摘要：把新事件并入前次摘要；保持三段结构；
      废弃已被取代的 Pending 项；只输出更新后摘要"，user = 前次摘要正文 + 新消息转录
  middle 无前次 summary
    → FRESH 模式：现行为（新 prompt 产三段结构）
```

不变量：① wrapper 标签、消息类型（SystemMessage）、插入位置**全部不变**（L-1 安全 + L-4/recovery-advisory 不受扰）；② summariser 失败路径不变（`ContextOverflowError` 语义保持）；③ CM-3 flush 时序不变（先 flush 后摘要，UPDATE 模式同样）；④ preamble 文本进 wrapper **内**（单一来源，tag 外零变更）。

### 9.3 设计 ②：run 末 writeback 显式操作（Mem0 extract→update）

```
flush_messages_to_memory(..., reconcile: bool = False)   ← 新参，默认关
  抽取 → embed →
  reconcile=False：直写（现状；CM-3 压缩前 flush 走此路——turn 内延迟敏感，
                   残留重复由 consolidator 兜底）
  reconcile=True（run 末 memory_writeback_node）：
    每候选：store.retrieve(query_embedding=候选vec, limit=3)（纯向量）
      → 过滤 cosine ≥ _RECONCILE_SIM_THRESHOLD(0.80) 的近邻
    无近邻候选 → 直接 ADD（零 LLM 成本，多数路径）
    有近邻 → 一次批量 LLM 调用（复用 writeback llm_caller）：
      输入：[{候选 content/kind, 近邻 [{id, content}]}]
      输出：[{index, op: ADD|UPDATE|DELETE|NOOP, target_id?}]
        ADD            候选写入（新信息）
        UPDATE target  update_content(target, content=候选, kind=候选)（同义/矛盾改写，自动重 embed）
        DELETE target  soft_delete(target) 且候选不写（旧事实被撤销，候选只是撤销事件）
        NOOP           候选跳过（与既有重复）
    解析失败 / LLM 异常 / 单 op 应用失败 → 该候选回落直写 ADD（best-effort，绝不丢记忆）
```

### 9.4 数据/协议变更

1. **`context/compressor.py`**：`_SUMMARY_PREAMBLE` 常量（背景非指令声明，wrapper 内）；summariser system prompt 改三段结构；`_compress_once` 识别 middle 中最后一条前次 summary → UPDATE 模式 prompt；无则 FRESH 模式。无签名变更。
2. **`graph_builder/memory.py`**：`flush_messages_to_memory` 加 `reconcile: bool = False`；新私有 `_reconcile_candidates`（近邻检索 + 批量 ops LLM + 应用，best-effort 全程）；`_RECONCILE_SIM_THRESHOLD = 0.80`；`memory_writeback_node` 按 policy 传 `reconcile=True`；`make_pre_compaction_flush` 保持 False。
3. **`agent_spec.py`**：`MemoryPolicy.reconcile_writes: bool = True`（默认开——能力优先；关闭即回现状直写）。factory 透传。
4. **存储零变更**：复用 `retrieve`/`write`/`update_content`/`soft_delete` 四原语；无 schema/migration；`MemoryItem` 不加字段（标题/标签等留 A-MEM 全量观望项，§1.3）。
5. **可观测**：`record_memory_reconcile(*, op)`（`helix_cm_memory_reconcile_total{op=add/update/delete/noop/degraded}`，uplift_metrics）+ compressor `summary.update_mode` 结构化日志。

### 9.5 边界情况

| 场景 | 处理 |
|---|---|
| 二次压缩 middle 含多条历史 summary（理论上限 pass 链） | 取**最后一条**作前次摘要，更早的并入新事件转录（链收敛为单条运行摘要） |
| UPDATE 模式下前次摘要超大 | 与现状同（transcript 无硬上限）；3-pass + `ContextOverflowError` 兜底不变 |
| reconcile 近邻检索失败 | 候选回落直写 ADD（与 recall best-effort 同款契约） |
| ops LLM 输出含越界 index / 未知 op / 缺 target | 该候选回落 ADD；其余候选照常应用 |
| UPDATE/DELETE 目标已被并发删除（update_content 返 None / soft_delete 返 False） | 记 degraded，候选回落 ADD（UPDATE 场景）或跳过（DELETE 场景） |
| update_content 的威胁扫描 | 复用存储层既有路径（PATCH API 同款）；blocked 异常 → 该候选 degraded 跳过，不阻断其余 |
| 取消（RunCancelledError） | reconcile 全程在 writeback try 内，cancel 照常 re-raise |
| `reconcile_writes=False` | 字节级回现状直写（zero behavior change 开关） |

### 9.6 可观测（零债"可观测齐全"）

- `helix_cm_memory_reconcile_total{op=add/update/delete/noop/degraded}`。
- compressor 结构化日志：`context.summary mode=fresh|update`（现有压缩日志扩一个字段）。

### 9.7 测试 & 验收（CM-7 Exit）

- **compressor unit**：wrapper 含 preamble（reference-only 字样）+ 三段结构 prompt；二次压缩 UPDATE 模式（scripted summariser 断言收到前次摘要正文 + 新转录、不再是裸链式全量）；middle 多条 summary 取最后；无前次 summary FRESH 模式；现有 head/tail/leading byte-stable/3-pass/CM-3 时序测试不回归。
- **writeback unit**：无近邻 → 直 ADD 零 LLM 调用；有近邻 → ops 应用四分支（ADD/UPDATE/DELETE/NOOP 各一）；解析失败回落直写；目标失踪 degraded；`reconcile=False` 与现状字节一致；pre-compaction flush 不 reconcile。
- **protocol**：`MemoryPolicy.reconcile_writes` 默认/关闭/extra-forbid。
- **零债 6 条**：无 TODO；本 §9 与实现一致；metrics/日志齐全；CI 8/8；token 节省数字不声称（Mem0 论文数字不平移，自测留 CM-N5）。

### 9.8 Mini-ADR（CM-7 锁定）

| ID | 决策 |
|---|---|
| **CM-H1** | **preamble 进 `<context-summary>` 内**、消息类型/位置/标签全不变——L-1 cache prefix 与 L-4 通道零扰动；"背景非指令"语义单一来源 |
| **CM-H2** | **二次压缩 = 增量更新前次摘要**（识别 middle 内最后一条 summary → UPDATE 模式），不再链式"摘要的摘要"；三段结构（Facts/Decisions/Pending）锁定 prompt |
| **CM-H3** | **显式操作只在 run 末 writeback**（2026-06-10 用户拍板）；CM-3 压缩前 flush 保持直写（turn 内延迟敏感），残留重复由 consolidator 兜底 |
| **CM-H4** | **ops 四值 ADD/UPDATE/DELETE/NOOP**（Mem0 范式）；无近邻（cosine < 0.80）直 ADD 零 LLM 成本；一切失败回落直写 ADD——宁可重复绝不丢记忆 |
| **CM-H5** | **复用存储四原语**（retrieve/write/update_content/soft_delete），零 schema 变更；`MemoryItem` 不加结构化字段（A-MEM 全量 Zettelkasten 维持观望，§1.3） |
| **CM-H6** | `MemoryPolicy.reconcile_writes` 默认 **True**（能力优先）；False 字节级回现状；token 节省不声称数字，自测留 CM-N5 |

### 9.9 PR 切分（CM-7）

1. **CM-7 PR1 — compressor 摘要强化（①）**：preamble + 三段结构 prompt + UPDATE/FRESH 双模式 + `summary.update_mode` 日志 + unit tests（含现有压缩测试不回归）。
2. **CM-7 PR2 — writeback 显式操作（②，收尾 CM-7）**：`flush_messages_to_memory(reconcile=)` + `_reconcile_candidates`（近邻 + 批量 ops + best-effort 应用）+ `MemoryPolicy.reconcile_writes` + factory 透传 + `record_memory_reconcile` + unit/protocol tests + ITERATION-PLAN 回填。

> 每个 PR 在本 §9 基础上局部细化；ITERATION-PLAN 增 CM-7 backlog，ship 后回填 `[x]`+PR 号。

---

## 10. 与既有 Stream 的衔接

- **Stream J**：复用 `user_workspace`（J.15 卷）、`memory_item`（J.3）、approval（J.8 pause→ingest 时机）、`update_plan`（K.8）。
- **Stream L**：投影钩子在 agent_node/tools_node，与 L-1 cache prefix（system 冻结）、L-2 compressor（CM-2/3 在此之上）共存；recitation 放非 system 区不破 L-1。**L-4 mutation advisory 被 CM-1 收敛**（`failed_mutations`→`tool_failures`、`<mutation-advisory>`→`<recovery-advisory>`，mutation 校验作为 `mutation_not_landed` 一类保留），非并行；L-5 退款语义不动（失败工具调用照常计步）。
- **Stream SE**：CM-1（运行时 error-as-guidance）与 SE-12（离线 skill 进化失败归因）是**两个层面**——SE 学习离线进化、CM-1 运行时即时恢复，不重叠。
- **Stream H（admin-ui）**：CM-8 文件投影 + UI 双通道依赖 CM-0 的 ingest 路径。
