# 调试台历史对话调试视图重建 — 设计文档

**日期**: 2026-07-11
**分支**: `feat/playground-history-debug-reconstruction`
**状态**: 定稿,待实施

## 背景与问题

admin-ui 调试台(`PlaygroundTab`)在 resume 一个已有 thread 时,历史对话通过 `GET /v1/sessions/{thread_id}/messages` 载入,后端只从 checkpoint 取 human/ai 非空文本轮(`transcript.read_turns`),前端渲染成**扁平只读文本气泡**(`PlaygroundTab.tsx` history 块,opacity 0.75)。

结果:历史轮**丢失了实时轮的全部调试可观测性** —— 执行时间线、工具调用、每步/每工具耗时、token 成本、结构化 AgentState、Langfuse trace。对一个「调试台」而言,载入历史后调试视图**展示不全**。

用户诉求:**历史轮与实时轮完全一致**。

## 根因(已勘查实证)

- 历史走 `/messages` 极简路径,**按设计**丢弃全部调试数据。
- 实时轮的 `Turn` 完全由 `events: SseEvent[]` 驱动;`TurnCard` 及其所有解析器(`parseTimeline` / `summarizeTurn` / `parseAgentState` / TraceView 等)都从 `turn.events` 渲染。
- 每个历史 run 的完整 SSE 事件流**durably 存在** `run_event` 表,可经现有端点 `GET /v1/sessions/{thread_id}/runs/{run_id}/events` 回放(终态 run 从 `RunEventStore` 重放)。前端 `streamRunEvents(threadId, runId)` 已封装该端点(导出功能已在用,视其为权威完整流)。
- **实测**(dev 库真实 run `e3afe2aa`):事件流**不含用户输入** —— frame 0 为 `metadata`(仅 run_id/thread_id),其余为 node `updates`(memory_recall/agent/tools/agent/memory_writeback)。用户输入是 graph 输入,存 checkpoint,不在 `updates` 重发。
- **实测** `RunInfo`(`expert_work.runtime.runs.schemas`):同步(SSE)run **不持久化 input**;`enqueued_input` 仅 queue 模式,注释明示同步 run 的 input「lives in the checkpoint / event log」。

结论:**这不是 bug,是功能缺口**。重建历史调试视图需要:(1) 每个历史 run 的事件流(回放,已有);(2) 每轮的用户输入文本(另找来源)。

## run ↔ 用户轮 映射(关键,已实证修正)

- `agent_run.is_resume = bool(prior_runs)`(`runs.py`)—— 语义是「thread 里之前有过 run」,**不是**「审批续跑」。**第一轮之后每个新用户消息都是 `is_resume=true`**,因此 `is_resume` **与轮次映射无关**,不能用它过滤/折叠。
- 正确映射:**用户消息[i] ↔ 按 `created_at` 排序的 run[i]**(1:1)。
- 审批边界:一个用户轮被审批门拆成 2+ run —— 初始(paused)run + 续跑 run(走 `POST /resume`,`apply_approval_decision` 生成**新 run_id**,经 `agent_approval` 行关联原 run;`is_resume` 不区分它)。此时 `#run > #用户消息`。
- **计数守卫**:`#用户消息 !== #run` 时(审批拆分 / 自动触发 run / 异常 run)→ **整体降级回现在的扁平文本气泡**(诚实、安全,不做易错的启发式分组)。常见场景(1 run = 1 用户轮)计数相等 → 逐轮 enrich。
- 实测验证:dev thread `e33ef6fb` 有 2 用户轮 ↔ 2 run(created_at 序 `e3afe2aa`→`bf83f831`),计数相等,配对成立。

## 目标 / 非目标

**目标**:
- resume 载入的历史轮,渲染成与实时轮同构的完整 `TurnCard`(时间线/工具/耗时/成本/结构化态/trace 全部可用)。
- 懒填充:载入不阻塞,滚到可见的历史轮才回放其事件。
- 历史轮只读:不暴露对已结束 run 的可变交互(审批决策等)。
- 任何环节失败都优雅降级到现在的扁平文本,不白屏、不丢内容。

**非目标**:
- 审批拆分轮(`#run≠#用户消息`)的逐轮精确重建 —— 走整体降级,不做启发式分组(YAGNI;审批在 playground 历史里是边缘场景)。
- 修改埋点 / 事件格式 / 后端事件存储。
- 历史轮的反馈打分、重跑、继续等写操作。

## 架构

单条数据链,复用现有渲染:

```
resume(thread)
  ├─ getSessionMessages(threadId)          → [user, assistant, ...] 文本轮(已有)
  └─ listThreadRuns(threadId)              → [{run_id, created_at, ...}] oldest-first(新端点)
        │
        ▼  前端配对(计数守卫)
  historyTurns: [{ input, fallbackAnswer, runId, events: null, status }]
        │
        ▼  渲染每轮为 TurnCard(readOnly)
  events===null → 输入气泡 + fallbackAnswer(暗)+ 面板占位骨架
        │
        ▼  IntersectionObserver 可见时
  streamRunEvents(threadId, runId) → events 填充 → TurnCard 全面板渲染
        │
        └─ 回放失败 → 保留 fallbackAnswer 扁平文本
```

分隔线「以下为本次新消息」保留:上=重建历史轮(只读),下=本次新轮(实时)。

## 组件与接口

### 后端(唯一新增)

**`GET /v1/sessions/{thread_id}/runs`**(`api/runs.py`,挂在 sessions 路由前缀下)
- 归属门控:照搬 `get_thread_messages` 的 owner 校验(`caller_owns_thread` + 404 隐藏跨租户/跨用户存在性;`tenant_id` query 支持 system_admin 跨租户抽查,同 `/messages`)。
- 数据:`runs.list_by_thread(thread_id, tenant_id=...)`(已存在,oldest-first)。
- 返回信封:`{"success": true, "data": {"runs": [RunSummary...]}}`,`RunSummary = {run_id: str, status: str, is_resume: bool, created_at: iso8601}`,oldest-first。
- 降级:checkpointer/store 无 或 异常 → best-effort 返回 `{"runs": []}`(不 500),与 `/messages` 一致。
- 测试:owner 可读、非 owner 404、oldest-first 序、空 thread 空列表、跨租户 system_admin 门控。

### 前端 API

**`listThreadRuns(threadId, tenantId?)`**(`api/runs.ts`)
- `GET /v1/sessions/{threadId}/runs` → `unwrap(...).runs`。
- 类型 `ThreadRunSummary { runId: string; status: string; isResume: boolean; createdAt: string }`(camelCase 映射)。

### 前端组装:历史轮描述符

`buildHistoryTurns(messages: HistoryMessage[], runs: ThreadRunSummary[]): HistoryTurn[] | null`(纯函数,新文件 `playground/history_turns.ts`,可独立单测)
- 从 `messages` 抽用户轮序列 + 各自紧随的 assistant 文本(配对成 `(input, answer)` 对)。
- 计数守卫:`#(user,assistant)对 !== #runs` → 返回 `null`(调用方走扁平降级)。
- 否则:`HistoryTurn[i] = { key, input: pair[i].user, fallbackAnswer: pair[i].assistant, runId: runs[i].runId, status: runs[i].status }`。
- `HistoryTurn` 接口:`{ key: string; input: string; fallbackAnswer: string; runId: string; status: string }`。

### 前端渲染:懒填充历史 TurnCard

`PlaygroundTab` 历史块改造:
- resume 时并行 `getSessionMessages` + `listThreadRuns`,`buildHistoryTurns` 组装;`null` → 保留现扁平文本渲染(降级路径原样保留)。
- 每个 `HistoryTurn` 渲染为 `TurnCard`(新 `readOnly` 语义),事件懒态由父层持有:`Map<runId, {events: SseEvent[] | null, state: "pending"|"loading"|"done"|"error"}>`。
- **IntersectionObserver**(单个 observer,注册各历史轮容器 ref):容器可见且 `state==="pending"` → `setState("loading")` → `streamRunEvents(threadId, runId)` 收集到 `end` → `setState("done")` + 存 events;异常 → `setState("error")`。
- `events===null`(pending/loading)时 `TurnCard` 显示:输入气泡 + `fallbackAnswer`(暗)+ 面板区骨架/「载入调试数据…」占位。`done` → 正常全渲染。`error` → 输入气泡 + `fallbackAnswer` 扁平(等价现状,不丢内容)。

### TurnCard 改造

- 新 prop `readOnly?: boolean`(default false)。为 true 时:
  - 不渲染审批决策控件(`onDecide` 路径 / approval 面板按钮)。
  - 不渲染任何对 run 的可变操作。
  - **保留**只读操作:导出事件 JSON、打开 Langfuse trace(受 `isSystemAdmin` 门控)、artifact 下载、事件视图切换(timeline/raw/exact)。
- 新 prop 承载懒态:`historyEvents?: SseEvent[] | null` + `loadState?`(或复用既有 `turn.events` + 一个 `placeholder` 标志)。实现时择一,保持 `TurnCard` 由 events 驱动的既有契约不破。
- 历史轮 `turnSeq` / 反馈:只读下反馈打分禁用或隐藏(不对历史 run 发 feedback)。

## 数据流与状态

- 懒态存 `PlaygroundTab` 局部(`Map<runId, ...>`),resume 切换或新轮不影响历史懒态。
- IntersectionObserver 在历史块挂载时建、卸载时断;新历史轮加入时重新 observe。
- 中止:resume 到别的 thread → 取消在途回放(AbortController per runId 或共享 signal),清空历史懒态。

## 错误处理与降级(硬约束:永不白屏/丢内容)

| 失败点 | 降级 |
|---|---|
| `listThreadRuns` 失败 | 走现扁平文本历史(等价现状) |
| 计数守卫不等(审批/异常 run) | 走现扁平文本历史 |
| 单轮 `streamRunEvents` 回放失败 | 该轮停在输入气泡 + `fallbackAnswer` 扁平文本 |
| 回放空/无 end | `error` 态,同上 |

## 测试

- 后端:新端点 pytest(owner/非owner/序/空/跨租户门控)。
- 前端单元:`buildHistoryTurns` 纯函数(配对、计数守卫返回 null、oldest-first 保序);`listThreadRuns` SDK(信封解包、camelCase 映射)。
- 前端组件:`PlaygroundTab` resume 路径 —— 计数相等渲染懒 TurnCard、计数不等回退扁平、回放失败保留 fallback、readOnly 隐藏审批控件。IntersectionObserver 在 jsdom 需 mock/shim。
- 覆盖率 ≥ 现有基线。

## 全局约束

- admin-ui:`pnpm typecheck`(tsc -b)0 报错;`npx vitest run` 全绿。i18n 三处同步(`en.ts` 接口+值 / `zh-CN.ts` 值)编译器强制。
- 后端:`uv run pytest` / `uv run mypy`(repo-root 配置)/ `uv run ruff check` 全绿。**提交前本地跑 ruff**(历史教训:本地 mypy 过 ≠ ruff 过)。
- 只读安全:历史轮**不得**对已结束 run 发任何可变请求。
- 降级:任一环节失败回退扁平文本,不 500、不白屏、不丢已有内容。
- 复用现成 `TurnCard` 渲染路径,不 fork 出平行渲染实现。

## 文件清单

- 改 `services/control-plane/src/control_plane/api/runs.py` — 加 `GET /{thread_id}/runs` 端点。
- 新 `services/control-plane/tests/test_thread_runs_endpoint.py` — 端点测试。
- 改 `apps/admin-ui/src/api/runs.ts` — `listThreadRuns` + `ThreadRunSummary`。
- 新 `apps/admin-ui/src/pages/agent_detail/playground/history_turns.ts` — `buildHistoryTurns` + `HistoryTurn`。
- 新 `apps/admin-ui/src/pages/agent_detail/playground/__tests__/history_turns.test.ts`。
- 改 `apps/admin-ui/src/pages/agent_detail/PlaygroundTab.tsx` — 历史块懒填充 + IntersectionObserver + 组装。
- 改 `TurnCard`(同文件或抽出)— `readOnly` + 懒态占位。
- 改 i18n `en.ts` / `zh-CN.ts` — 占位/载入文案键。
- 改 `apps/admin-ui/src/pages/agent_detail/__tests__/PlaygroundTab.test.tsx` — resume 重建路径测试。
