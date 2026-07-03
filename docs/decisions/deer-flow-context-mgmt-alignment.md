# deer-flow 上下文管理对照（全生命周期 tracker）

## Context

[deer-flow 上下文管理分析](../../.claude/plans/deer-flow-zippy-karp.md) 列了 18 个 middleware + 7 大类防爆机制（static prompt + ID-swap / summarization + skill rescue / SubagentLimit / LoopDetection / tool truncation / memory injection cap / TokenUsage 归因）。

[STREAM-E-DESIGN](../streams/STREAM-E-DESIGN.md) 已经把 M0 范围内能做的全做了（dynamic_context / loop_detection / tool truncation / tool error wrapper / dangling sanitize），但 deer-flow 其余 5 类机制和若干 middleware 是 **M1/M2 才有 use case**（依赖 memory / sub-agent / 长会话 等底层能力）。

本文档是一份 **living tracker**，目的是 **后续 Stream 启动前不忘对接 deer-flow 已验证的具体技术细节**。每个 M1/M2 Stream 的设计文档启动前必须 review 本表对应行，把这里列的"待锁条款"显式落进新 Stream 的设计文档（按 [设计先行规则](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_design_first_iteration.md) 的口径，"先锁设计、再写代码"）。

---

## deer-flow 18-middleware 总览

按 deer-flow `agents/lead_agent/agent.py:_build_middlewares` 的链顺序：

| # | deer-flow middleware | 对接 Stream | 状态 | 说明 |
|---|---|---|---|---|
| 1 | ThreadData | M1-D | 🕒 待 | vendor P1，M1 进入 vendor 同步周期 |
| 2 | Uploads | M1-D | 🕒 待 | vendor P1 |
| 3 | Sandbox | F.3/F.4 | 🕒 待 | Stream F 沙盒主线 |
| 4 | DanglingToolCall | E.15 sanitize | ✅ M0 已接 | 不上独立 middleware，作为 GraphRunner.run resume 路径 guard（~20 行）|
| 5 | LLMErrorHandling | E.4 | 🕒 M0 待做 | 当前实现到 E.3，E.4 是下一个 PR |
| 6 | Guardrail（业务级拦截）| M1-D | 🕒 待 | 比 PII redactor 范围更宽（合规话术 / 禁谈竞品 / 安全词） |
| 7 | SandboxAudit | E.10 | 🕒 M0 待做 | F.4 接入前空跑 |
| 8 | ToolErrorHandling | E.6 内置 | ✅ M0 已接 | 不上独立 middleware，ReAct tools 节点 dispatch 统一 try/except → ToolMessage(error) |
| 9 | DynamicContext | E.3 | ✅ 已合（PR #61） | 朴素 last-N + token budget，无 ID-swap（M2-C 加） |
| 10 | Summarization + skill rescue | M2-C | 🕒 待，细节见下 | 需要 ID-swap + 多个 hook |
| 11 | TodoList | — | 🚫 业务取舍 | helix 平台不内置，agent 用 Python slot (M1-F) 自实现 |
| 12 | TokenUsage | M1-D | 🕒 待 | vendor P1，仅 step-level 归因；M0 Langfuse token 数据已可用 |
| 13 | Title | — | 🚫 业务取舍 | session 标题让 admin UI 后处理调 LLM 做，不必中间件 |
| 14 | Memory | M2-C | 🕒 待，细节见下 | 需要 2k cap + ID-swap |
| 15 | ViewImage | M2/M3 | 🕒 待 | 多模态 |
| 16 | DeferredToolFilter | M1-D | 🕒 待 | vendor P1 |
| 17 | SubagentLimit ≤3 | M1-F | 🕒 待，细节见下 | sub-agent 落地必须显式 limit |
| 18 | LoopDetection | E.10.5 | 🕒 M0 待做 | 设计已锁，待实施 |
| ＋ | Clarification | — | 🚫 业务取舍 | "信息不全反问"靠 agent prompt 工程解决 |

**Legend**：✅ 已接 ｜ 🕒 待做（设计已锁或某 Stream 计划内） ｜ 🚫 业务取舍（明确不补，记录理由）

---

## M0 已对接（STREAM-E-DESIGN）

| 机制 | 对接位置 |
|---|---|
| 静态 system_prompt 原则 | § 6 风险表禁动态 system_prompt + CI lint 守门；§ 1.2 Out-of-scope 注 "Prefix cache 优化" 走 Anthropic SDK 内置 |
| DynamicContext（朴素截断）| E.3 ✅ PR #61 |
| LoopDetection middleware | E.10.5（计划已锁） |
| Tool output truncation | E.7 web 4k / E.8 HTTP 20k tail / E.9 MCP 20k middle-trim |
| ToolErrorHandling | E.6 ReAct tools 节点统一 wrapper |
| DanglingToolCall sanitize | E.15 resume 路径 guard |

---

## M1-D 启动前必锁条款

| 条款 | 来源（deer-flow） | M0 我们已有的部分 | M1-D 必须加 |
|---|---|---|---|
| **SubagentLimit ≤3** | `subagent_limit_middleware.py` | 无（M0 单 agent） | sub-agent 落 M1-F 后立即装；LLM 一次 task() 调用超 3 → 截断重写 AIMessage；同一 turn 内累计上限走 manifest 配置 |
| **Guardrail middleware**（业务级拦截）| `guardrail_middleware` | D.2 PII redactor（只覆盖 PII 字段名） | M1-D 加业务可配置规则集（合规话术 / 禁谈竞品 / 安全词），命中 → 修改 LLM 输入或注入 system reminder；规则集走 tenant_config |
| **ThreadData** | `thread_data_middleware` | 无 | vendor P1，约 118 LOC |
| **Uploads** | `uploads_middleware` | 无（uploads 经 H.1 object storage） | vendor P1，约 295 LOC |
| **DeferredToolFilter** | `deferred_tool_filter_middleware` | 无 | vendor P1，约 107 LOC；按 phase 过滤可调工具 |
| **TokenUsage** step-level | `token_usage_middleware` | Langfuse 记 per-call token | vendor P1，约 303 LOC；区分 todo_update / subagent / search / final_answer / thinking 五类 step 归因 |

**Verification**：M1-D 设计文档启动前 review 本节，落进 `STREAM-M1D-DESIGN.md`（或对应文件）。

---

## M2-C 启动前必锁条款（Memory 三层）

| 条款 | 来源（deer-flow） | 必锁细节 |
|---|---|---|
| **ID-swap 技术** | `dynamic_context_middleware.py:121-143` | reminder HumanMessage 借用原 user 消息 ID（原消息派生 `{id}__user` 作新 ID），靠 LangGraph `add_messages` reducer 让 reminder 占稳定位置；让前缀变化不在 system_prompt 而是 reminder block 内；Anthropic prefix cache 仍命中（cached input 计费 ~10%） |
| **`<system-reminder>` HumanMessage 注入 pattern** | 同上 | memory facts / 当前日期 / 跨午夜更新 都注入这条消息；`hide_from_ui: True` 让前端过滤；首轮注入 + 跨午夜轻量 prepend 两种触发 |
| **Summarization 三件套** | `summarization_middleware.py:97-318` | (1) 触发条件配 tokens / messages / fraction-of-max-input 三选一阈值（2）`RemoveMessage(id=REMOVE_ALL_MESSAGES)` 批量删除模式（3）keep 策略（保留近期消息数或 tokens 数） |
| **Skill rescue** | `summarization_middleware.py:182-228` | 摘要时把最近被加载的 skill 文件按 3 个预算救出：`preserve_recent_skill_count`（默认 5）/ `preserve_recent_skill_tokens`（默认 25k 总）/ `preserve_recent_skill_tokens_per_skill`（默认 5k 单 skill 上限） |
| **before_summarization hook** | `summarization_middleware.py:33-37, 331-353` | 让 MemoryMiddleware 在历史压缩前把对话刷入 memory 队列；hook 接口要在 Summarization middleware 设计时预留 |
| **Memory injection 2k 硬上限** | `agents/lead_agent/prompt.py:_get_memory_context` | `format_memory_for_injection` 按 `max_injection_tokens=2000` 硬截断；最多 top 15 个 fact + 摘要式上下文；超出 → 截尾 + 末尾标 `[truncated N more facts]` |
| **Memory 异步队列模式** | `agents/memory/{queue,updater,storage}.py` + `memory_middleware.py` | MemoryMiddleware 在 `after_agent` 只入队，不直接动 messages；后台 worker 拉队列、调 LLM 提取 fact、写 memory store；这种异步隔离避免 LLM 调用阻塞主链路 |

**Verification**：M2-C 设计文档启动前 review 本节，逐条落进 `STREAM-M2C-DESIGN.md`；ID-swap 技术尤其要在 LangGraph `add_messages` reducer 行为基础上写清楚 ID 怎么算（deer-flow 用 `{id}__user` 派生），否则前缀缓存击穿不可见。

> **[2026-07-03 上游复核注记]** 本表基于 2026-06-09 快照，Stream RT-2 PR-0 已按 deer-flow 新版（本地 @b3c312b7 + GitHub #3887 @442248dd）逐条复核，三条已过期：
> ① **Skill rescue 三预算已被上游 #3887 整体废弃**——`preserve_recent_skill_*` 三字段删除，替代 = durable `skill_context` channel 只存引用（name/path/description≤500c，上限 8 条），需要时模型重读文件。**不要按本表实现三预算**，helix 落法见 STREAM-RT-DESIGN RT-ADR-7（引用+重读 stub）。
> ② **ID-swap 已从 HumanMessage 升级为三元组 SystemMessage 角色分离**（#3630），且 #3746 修了递归注入（`id__user__user…`）与孤儿压缩两坑；helix prompt-view-only 不需要 ID-swap（RT-ADR-8），但要补注入块×压缩组合测试。
> ③ **Summarization 触发已从"三选一"升级为任意组合 OR**；#3887 摘要不再写回 messages（改 state channel + 请求时投影）——helix prompt-view-only 模式与之同向，不要倒退成写回。
> 另新增事实：#3711 SystemMessageCoalescing——对话中部 SystemMessage 在严格 OpenAI-compatible 后端 400，**helix `<context-summary>` 正是该形态**（RT-ADR-5 最高优先核证）。Memory injection 2k 上限仍有效但细节已变（贪心逐条预算 + correction 类保底 500t 替代 top-15+截尾标记）。

---

## 业务取舍（明确不补 — 防止 M1/M2 重新发明）

| 项 | 不补理由 |
|---|---|
| **TodoList middleware** | deer-flow agent 业务特性（用户期待"todo 进度展示"）；helix 平台无 opinion，agent 业务侧用 Python slot (M1-F) 自实现，不强加给所有 agent |
| **Title middleware**（session 自动起标题）| 是 admin UI 用户体验事，不是 LLM 调用主路径；admin UI 后处理调 LLM 做即可，不上中间件减少链复杂度 |
| **Clarification middleware**（缺信息时反问用户）| 是 prompt 工程层面的事 — 在 system_prompt 写 `"如果用户需求模糊，反问"` 让 agent 自己处理；上中间件会跟"agent 自主性"打架 |

---

## Verification（每 Stream 启动前 review）

| Stream 启动 | 必 review 本表 |
|---|---|
| **M1-D**（vendor P1 中间件）| M1-D 表全行；落 SubagentLimit / Guardrail / ThreadData / Uploads / DeferredToolFilter / TokenUsage 6 条进 `STREAM-M1D-DESIGN.md` |
| **M1-F**（多租户 + Sub-Agent + Python 插槽）| 业务取舍表（TodoList 是否要 Python slot 留口） + SubagentLimit ≤3 条款 |
| **M2-C**（Memory 三层）| M2-C 表全行；ID-swap / Summarization 三件套 / Memory cap / 异步队列 全锁进 `STREAM-M2C-DESIGN.md` |
| **M2/M3**（多模态）| ViewImage middleware 对接 |

每个 Stream 设计文档 review 完本表后，**在对应 Stream 设计文档加一节"deer-flow 对照"** 列出消化结果（"X 条已落 / X 条 N/A 理由"），把本 tracker 的对应行更新为 ✅ 状态。

---

## 引用

- 总分析：[`~/.claude/plans/deer-flow-zippy-karp.md`](../../.claude/plans/deer-flow-zippy-karp.md)
- Stream E 设计：[STREAM-E-DESIGN.md](../streams/STREAM-E-DESIGN.md)
- ITERATION-PLAN（M1/M2 时间表）：[ITERATION-PLAN.md](../ITERATION-PLAN.md)
- 设计先行规则：[feedback_design_first_iteration.md](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_design_first_iteration.md)
