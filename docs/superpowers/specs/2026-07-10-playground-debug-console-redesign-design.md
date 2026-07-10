# 调试台(Playground)可观测性重设计 — 设计文档

**状态:** 设计待评审
**日期:** 2026-07-10
**范围:** admin-ui 调试台(`PlaygroundTab`)+ Run 详情复用组件;分 4 批交付。Batch 1–3 纯前端;Batch 4 含 orchestrator 后端 + 新增 trace-facade 端点 + Langfuse 入口接线。
**关联:** Run 详情页(`RunDetail` / `run_detail/*`)与调试台共用 SSE 帧模型与两个解析器(`tool_timeline.ts` / `turn_summary.ts`),改动同步收益。

---

## 1. 背景与问题

调试台现在按"聊天记录"组织,不按"agent 执行轨迹"组织 —— 价值倒挂。核心矛盾:**调试最需要的数据大量已经到了前端,却只埋在"原始事件"的 JSON dump 里没被解析出来。**

逐条落到代码:

### 1.1 价值倒挂:该看的藏起来,不该抢焦点的抢焦点

- token 统计彩色 badge(输入/输出/合计/缓存/思考)占据每轮视觉重心(`PlaygroundTab.tsx:1820–1847`),调试时这些是次要信息。
- 事件流、工具调用、思考过程**全部默认折叠**(`PlaygroundTab.tsx:1909–2011`),逐轮手点展开。调试第一现场反而最难够到。
- `eventView`(timeline/raw)toggle 虽提升到父级共享(`:174`),但展开态不持久,刷新即丢。

### 1.2 事件视图 = 原始 JSON dump

- 第三张截图的 metadata/updates 块,由 `EventCard` 渲染:给 `<Tag>` 上色 + `JSON.stringify(evt.data, null, 2)` 塞进 `<pre>`(`PlaygroundTab.tsx:2016–2065`)。
- **`EventCard` 重复两份**:`PlaygroundTab.tsx:2016–2065` 与 `run_detail/EventStreamPanel.tsx:248–298` 近乎逐字复制。
- 要在长 JSON 里肉眼找 `finish_reason` / `tool_calls` / error,痛。

### 1.3 大量调试字段已到前端却未结构化(核心问题)

后端 SSE 把整个 LangChain message dict 发过来了(`sse.py` 的 `_to_jsonable` → `msg.model_dump()`)。以下字段**都在前端已收到的帧里**,但只有"原始事件"能看到:

| 字段 / 通道 | 价值 | 现状 |
|---|---|---|
| `response_metadata.finish_reason`(tool_calls/stop/length) | 区分"停下调工具"vs"撞长度上限"vs"正常结束" | 只在 raw dump |
| `response_metadata.model_name`(如 glm-5.2) | 哪个模型答的 | 调试台完全不显示 |
| exec_python `stdout` / `exit_code` | 工具调试第一现场 | 挤成一行塞在 `resultPreview` 字符串里 |
| AgentState 通道:`plan` / `recalled_memories`(memory_recall)/ `tool_failures` / `reflections` / `subagent_invocations` / `no_progress_streak` / `escalate_next` | agent 为什么这么走 | 全在 raw JSON,零结构化 |
| `retry` 事件(attempt / error_class / backoff_s) | 重试可见性 | 无 parser |
| `compaction` 事件 | 上下文压缩可见 | `EventStreamPanel` 有,调试台没有 |
| per-step token 粒度 | 哪一步贵 | `summarizeTurn` 求和抹平(`:98–111`) |
| `cache_creation_tokens` | 缓存写入成本 | `TurnUsage` 只留 `cacheReadTokens` |

### 1.4 结构债

- `PlaygroundTab.tsx` **2066 行**,超 CLAUDE.md 800 行上限 2.5 倍,一个文件塞了 Turn 编排 / TurnCard / EventCard / FeedbackBar / ApprovalGate / downloadJson。
- 精确 per-step / per-tool 计时**不在控制面数据里** —— SSE 帧无 duration,深度 span 计时在 OTLP/Langfuse(run 上有 `trace_id`)。当前"延迟"只是客户端时钟粗算(首帧→末帧 `receivedAt`)。

### 1.5 Langfuse 入口:基础设施全在但没生效、没落到主战场

- `config/env.ts` 已有 `buildLangfuseTraceUrl(traceId)`(读 `VITE_LANGFUSE_BASE_URL`,未配返回 null)。
- `run_detail/TraceToolbar.tsx` 已有"在 Langfuse 中打开"外链,受两道门控:`VITE_LANGFUSE_BASE_URL` 已配 + `isSystemAdmin`(Langfuse 单 ClickHouse **无租户隔离**,刻意的安全决策)。
- i18n 已备 `open_in_langfuse` / `langfuse_unconfigured_hint`。
- 用户"看不到入口"的三条叠加原因:(1) env 大概率没配;(2) 只有系统管理员可见;(3) 入口只在 RunDetail,**调试台没有直达**。

---

## 2. 目标与原则

1. **从"聊天记录"转向"执行轨迹"** —— 按 step / tool / event 组织,而非按对话轮。
2. **先表面化已有数据,再谈新采集** —— Batch 1–2 只用已到前端的字段,零后端依赖,快、低风险。
3. **可观测性分层,为扩展性让路** —— Langfuse/OTLP 做深度计时与 span 树的源头;前端不直连 vendor,经后端 trace-facade 解耦。
4. **surgical,复用优先** —— 复用现成 `PlanPanel` / `parseCompactionEvents` / `TraceToolbar` / `buildLangfuseTraceUrl`,不重造。
5. **顺手还结构债** —— 借重写把 `PlaygroundTab` 拆成聚焦文件、`EventCard` 去重。不做无关重构。

---

## 3. 架构:三层可观测性

```
┌─────────────────────────────────────────────────────────────┐
│ Langfuse / OTLP  ── span 树 + 精确 wall-clock 的唯一源头        │
│   新埋点(子 agent / memory / tool 子 span)自动经 OTLP 进入,   │
│   前端零改动。这是"后面能力扩展"最值的一层。                    │
└───────────────▲─────────────────────────────────────────────┘
                │ 查询(server→server)
┌───────────────┴─────────────────────────────────────────────┐
│ 后端 trace-facade(新增端点)                                   │
│   GET /…/runs/{id}/trace → 归一成稳定 span DTO。               │
│   解耦 vendor:换 OTLP 后端前端一行不改。                        │
│   是以后加 cost 汇总 / 跨 run 对比 / span 过滤的天然落点。       │
└───────────────▲─────────────────────────────────────────────┘
                │ 自家 API
┌───────────────┴─────────────────────────────────────────────┐
│ 前端 调试台                                                    │
│   实时:SSE(含新增 duration_ms)—— 边跑边显示每步粗计时。       │
│   精确/历史:trace-facade span 树视图。                         │
│   快捷跳转:buildLangfuseTraceUrl(trace_id) 外链(系统管理员)。 │
└─────────────────────────────────────────────────────────────┘
```

**为什么 SSE duration 与 Langfuse 都要:** Langfuse 有摄取延迟、实时性差,直播中的 run 不该硬依赖外部分析库在线 → SSE `duration_ms` 只做实时粗计时;精确/历史/span 树看 facade。二者不是二选一,是分层。

---

## 4. 批次规划

伞形设计覆盖全貌;**每批各自出实施计划(writing-plans)后再动工**。Batch 4 跨服务、含未知(Langfuse 查询 API / 摄取延迟 / 鉴权),实施前单独出细化子 spec。

| 批次 | 主题 | 后端依赖 | 交付 |
|---|---|---|---|
| **1** | P0:表面化已有数据 | 无 | finish_reason/model chip、exec_python 结构化、状态外露、展开态持久 |
| **2** | P1:结构化埋没通道 | 无 | AgentState 通道卡片、retry/compaction、per-step token |
| **3** | P2:执行轨迹 UX | 无 | 事件过滤/搜索、step 时间线布局 |
| **4** | 精确计时 + 入口 | orchestrator + 新端点 | metadata 帧带 trace_id、SSE duration_ms、trace-facade、span 树视图、调试台 Langfuse 直达 |

组件拆分(§8)随 Batch 1 起步、贯穿各批,不单列一批。

### Batch 1 — P0:表面化已有数据(纯前端)

改 `turn_summary.ts` 保留/新增字段 + `TurnCard` 渲染,`tool_timeline.ts` 解析工具结果内部结构。

1. **`finish_reason` + `model_name` chip** —— `summarizeTurn` 从每条 AI message 的 `response_metadata` 取(现在没读),挂到轮元信息行(`:1849–1900` 附近)。多模型/多步时取最后一条 AI message 的值,并在有分歧时标注。
2. **exec_python 结果结构化** —— `tool_timeline.ts` 的 `resultPreview` 现在是裸字符串。对内置 `exec_python` 解析出 `stdout` / `stderr` / `exit_code`,渲染成 `exit_code` 徽章 + 分区 monospace(非零 exit 标红)。非 exec_python 工具保持裸预览。
3. **工具状态徽章外露** —— 成功/失败(`ToolMessage.status`)现在要展开工具卡才看到;提到轮标题栏聚合(如"3 工具 · 1 失败")。
4. **展开态持久** —— `eventView` 与 Collapse 展开状态存 localStorage(抄 `EventStreamPanel` 已有做法),刷新不丢。默认把"事件"展开。
5. **`cache_creation_tokens`** —— `TurnUsage` 加字段,badge 区显示缓存写入(数据在 `usage_metadata`,upstream 兼容 vendor 可能为 0,为 0 时不显示)。

### Batch 2 — P1:结构化埋没通道(前端为主 + 1 小后端序列化修复)

> **修订(2026-07-10,基于 Batch 2 数据勘查):** 原设想"纯前端、数据全在帧里可直接解析"对以下 4 个通道**不成立**。`sse.py` 的 `_to_jsonable`(:1075)只对 `BaseMessage` 调 `model_dump()`,其余 pydantic BaseModel / dataclass 全走 `str(value)` 兜底 —— 于是 `recalled_memories`(pydantic)/`tool_failures`(dataclass)/`reflections`(pydantic)/`subagent_invocations`(pydantic)到前端是 **Python repr 字符串,非 JSON**(连现有"原始事件"视图对它们也是退化的)。本质是序列化 bug。**Batch 2 因此含一个小后端前提:给 `_to_jsonable` 加 pydantic(`model_dump(mode="json")`)+ dataclass(`asdict`)序列化。** 不受影响项仍纯前端:`plan`(`PlanPanel` 走 REST 自取)、标量信号(bool/int 干净序列化)、`retry`(payload 本就是 JSON)、`compaction`(parser 已导出)、per-step token(`summarizeTurn` 重构)。

新增按 AgentState 通道的轻量渲染。

6. **AgentState 通道卡片** —— 在轮内新增可折叠段,逐通道解析渲染:
   - `plan`(goal + steps)—— **复用 `run_detail/PlanPanel`**,不重写。
   - `recalled_memories`(memory_recall 节点)—— 召回的记忆列表。
   - `tool_failures`(ClassifiedToolError + recovery advisory)、`reflections`、`subagent_invocations`(每次委派:iteration_used / llm_call_count / wall_clock_ms)。
   - `no_progress_streak` / `escalate_next` —— 异常信号,达阈值时高亮。
7. **retry / compaction 在调试台渲染** —— retry 新增 parser(`{attempt, error_class, backoff_s}`);compaction **复用 `parseCompactionEvents` + `CompactionCard`**(现仅 EventStreamPanel 用)。
8. **per-step token 粒度** —— `summarizeTurn` 保留每步 usage(现在 `:98–111` 求和抹平),按步展示,标出最贵的步。总和仍保留在轮级 badge。

### Batch 3 — P2:执行轨迹 UX(纯前端)

> **设计定稿(2026-07-10,经线框 4 轮迭代评审)。** 结构方向:`events` 段的「时间线」视图从"扁平工具卡"改为**分类型的执行轨迹**;「原始事件」视图不变。数据全部来自已有 parser(Batch 1 `parseToolCalls`、Batch 2 `parseAgentState`/`parseRetryEvents`/`parseCompactionEvents`/`summarizeTurn`)+ 一个新的 per-step reasoning 拆分 —— **本批仍纯前端、不缺数据,主要是重组渲染 + 装配 + 过滤**。

**9. 分类型执行轨迹时间线(替代工具视图)。** Segmented 改「**步骤时间线 / 原始事件**」。步骤时间线 = 一条纵轴穿起**全部节点执行 + 关键事件**,按真实到达序列排,两种视觉权重:

- **agent 步 = 大卡**:标题行 `步骤号 · node · 模型 · finish_reason · per-step token`(**不显 per-step 耗时** —— SSE 无 per-step 墙钟,留 Batch 4 的 `duration_ms`;客户端估会误导)。展开体:**该步的思考(reasoning,按步内联)** → 触发的**工具卡**(嵌在所属步内)/ 或最终答复(content)。
- **工具卡**:头(内置/MCP 徽章 · 名称 · 成功/失败状态)+ **入参(args JSON)** + **出参(结果)**;exec_python/bash 出参走 exit_code 徽章 + stdout/stderr 分区(Batch 1),其它工具走 resultPreview。
- **轻量类型行(aux 节点)**:`memory_recall` / `planner` / `reflect` / `memory_writeback` —— 一行摘要(node 标签 + 计数/verdict),可展开看内容(召回的记忆、plan goal+steps、reflect critique、写回记忆)。
- **轴标记(事件)**:`compaction`(压缩 before→after)/ `retry`(定位在两次尝试之间,取代 chip)/ `error` / `end`(终态)/ **`approval`(紫色暂停标记 —— run 停在哪等人工审批)**。
- **异常步默认展开 + 红节点 + 红条**(工具失败 / retry / 撞长度 / error);正常步/aux 行**默认折叠**。**`reflect` 的 `revise` 用告警色高亮** —— 它回环让 agent 重做,是"为什么循环"的关键线索。

**10. 事件过滤 + 搜索。** 时间线上方工具条:类型 chip(全部 / 工具 / 错误 / retry …)+ 文本搜索(工具名 / error / finish_reason / node)+ 实时计数。过滤 = **隐藏非命中项**(非高亮淡化)。同一工具条也作用于「原始事件」视图。

**与 Batch 2「执行状态」聚合区的关系:** 二者**并存,分工明确** —— 时间线管**序列**(每个节点/事件何时发生),聚合区管**全局汇总**(最终 plan 走 PlanPanel REST、全部记忆/失败/子 agent 的去重汇总)。不合并。

**新增 parser 工作(唯一新数据处理):** per-step reasoning —— `summarizeTurn` 现把 `reasoning_content` 抹平成轮级 `reasoning: string[]`;需像 Batch 2 的 `perStepUsage` 一样按 AI message(步)保留关联,喂 agent 步卡的思考区。时间线的**装配**(把各 parser 的产物按 `receivedAt` 序列合并成有序的 typed items)是新的纯前端逻辑,但底层字段全部已解析。

> 线框存档:见评审时的可交互 HTML 线框(4 轮:替代工具视图 → 思考按步 → 工具入参/出参 → 分类型轨迹 + 可展开 aux 行)。

### Batch 4 — 精确计时 + Langfuse 入口(后端 + facade + 前端)

**先出独立细化子 spec**(Langfuse 查询 API 能力、摄取延迟容忍、鉴权、span→DTO 映射需先验证)。设计层定调:

11. **metadata 帧带 `trace_id`** —— orchestrator `sse.py` 的 metadata payload 现只有 `{run_id, thread_id}`(`:378–379`),加 `trace_id`。trace_id 本就是 run 核心身份,该在这。→ 调试台无需额外 `getRun` 即可拿到,喂 §12 入口与 §11 facade。
12. **SSE `duration_ms`(实时粗计时)** —— orchestrator 给 `updates`(节点级)/工具帧打墙钟耗时,前端时间线显示每步实时耗时,替代客户端时钟估算。
13. **trace-facade 端点** —— `GET /…/runs/{id}/trace`,内部查 Langfuse(bypass 逐 vendor 细节),归一成稳定 span DTO 返回。前端只认自家 API。
14. **前端 span 树视图** —— 调试台"精确/历史"标签,渲染 facade 返回的 span 树(每 LLM 调用 / 工具 / 子 span 的精确耗时、cost、model)。
15. **调试台 Langfuse 直达** —— 复用 `buildLangfuseTraceUrl` + `TraceToolbar` 的门控逻辑,把"在 Langfuse 中打开"落到调试台每轮(靠 §11 拿到的 trace_id)。保留 `isSystemAdmin` 门。**部署侧需真配 `VITE_LANGFUSE_BASE_URL`**,否则按钮隐藏(部署事,非代码,文档提示)。

---

## 5. 数据契约改动

**前端解析器(Batch 1–2,均从已有帧取):**

```ts
// turn_summary.ts
TurnSummary += finishReason: string | null        // 最后 AI message 的 response_metadata.finish_reason
             + modelName: string | null           // response_metadata.model_name
             + perStepUsage: TurnUsage[]           // 每步保留,不再只 sum
TurnUsage   += cacheCreationTokens: number         // usage_metadata.…cache_creation(为 0 不显示)

// tool_timeline.ts
ToolCallEntry += execResult?: {                    // 仅内置 exec_python 解析
  stdout: string; stderr: string; exitCode: number | null;
}
+ parseRetryEvents(events): RetryEntry[]           // event==="retry"
```

**后端(Batch 4):**

```python
# sse.py metadata payload
{"run_id", "thread_id", "trace_id"}                # 加 trace_id
# updates / tool 帧
+ duration_ms                                       # 节点/工具墙钟

# 新端点 GET /v1/sessions/{thread}/runs/{run}/trace → 归一 span DTO(子 spec 定 shape)
```

---

## 6. 后端改动(Batch 4,唯一后端触点)

- `services/orchestrator/src/orchestrator/sse.py`:metadata payload 加 `trace_id`;`updates`/工具帧加 `duration_ms`。
- trace-facade 新端点:查 Langfuse → 归一 span DTO。落点(control-plane vs orchestrator)、Langfuse 查询实现、DTO shape 由 Batch 4 子 spec 定。
- **不做:** 不把深度 span 计时塞进现有控制面模型(保持控制面轻,深计时经 facade 单独取)。

---

## 7. Langfuse 入口设计(Batch 4)

- 复用 `buildLangfuseTraceUrl(traceId)` + `TraceToolbar` 的 `isSystemAdmin` 门控。
- 调试台每轮(拿到 §11 的 trace_id 后)显示"在 Langfuse 中打开"外链,与"查看运行"并列。
- env 未配 → 沿用现有降级:仅显 trace_id + 复制,不显外链;`langfuse_unconfigured_hint` 提示。
- **保留租户隔离安全门,不破。**

---

## 8. 组件重构(贯穿各批)

把 `PlaygroundTab.tsx`(2066 行)拆成聚焦文件,`EventCard` 去重:

```
pages/agent_detail/
  PlaygroundTab.tsx           仅编排/状态(streamRun、turns、handlers)
  playground/
    TurnCard.tsx              单轮卡
    TurnMeta.tsx              token badge + step/latency/finish_reason/model 行
    AgentStatePanels.tsx      Batch 2 的通道卡片
    StepTimeline.tsx          Batch 3 时间线布局
    FeedbackBar.tsx           从 :1384–1495 抽出
    ApprovalGate.tsx          从 :1565–1644 抽出
components/
  EventCard.tsx               去重:PlaygroundTab 与 EventStreamPanel 两份合一
```

原则:每次拆分只搬相关代码,不趁机改逻辑;拆分与功能改同 PR 内可分 commit。

---

## 9. 不在本次范围(YAGNI)

- 跨 run 对比 / 聚合看板(facade 留了落点,不本期实现)。
- Langfuse 之外的 OTLP 后端适配(facade 解耦了,但只实现 Langfuse 查询)。
- 移动端/窄屏适配调试台。
- 会话历史抽屉(`SessionHistoryDrawer`)、`ConversationsTab` 等非调试渲染面。
- 改动 orchestrator 的 span 埋点本身(用现有 trace)。

---

## 10. 测试计划

**前端(vitest + Storybook,各批):**
- `turn_summary`:从 `response_metadata` 取 finishReason/modelName;perStepUsage 不再被求和抹平;cacheCreation 为 0 不渲染。
- `tool_timeline`:exec_python 解析出 stdout/stderr/exitCode,非零 exit 标红;非 exec_python 保持裸预览;retry parser。
- `TurnCard` / `AgentStatePanels`:各 AgentState 通道有值/空值渲染;复用 `PlanPanel` 渲染 plan。
- 展开态 localStorage 持久(mock 断言)。
- 过滤/搜索:类型过滤 + 文本命中(Batch 3)。
- `EventCard` 去重后两处调用点回归不变。
- 各页 story 更新。

**后端(pytest,Batch 4):**
- metadata 帧含 `trace_id`;`updates`/工具帧含 `duration_ms`。
- trace-facade:命中返回归一 span DTO;Langfuse 不可达/摄取未就绪降级(不 500);非系统管理员鉴权拒绝。

**手动冒烟:**
- 跑一轮工具调用 run → 调试台默认见事件流展开、finish_reason/model chip、exec_python exit_code 徽章、工具失败在标题栏聚合。
- 配 `VITE_LANGFUSE_BASE_URL` + 系统管理员 → 调试台每轮见"在 Langfuse 中打开"直达对应 trace。

---

## 11. 文件触点

**前端(Batch 1–3 主体):**
- `apps/admin-ui/src/api/turn_summary.ts`(finishReason/modelName/perStepUsage/cacheCreation)
- `apps/admin-ui/src/api/tool_timeline.ts`(exec_python 解析 + retry parser)
- `apps/admin-ui/src/pages/agent_detail/PlaygroundTab.tsx`(拆分 + 编排)
- `apps/admin-ui/src/pages/agent_detail/playground/*`(新拆出组件)
- `apps/admin-ui/src/components/EventCard.tsx`(去重)
- `apps/admin-ui/src/pages/run_detail/EventStreamPanel.tsx`(改用共享 EventCard)
- `apps/admin-ui/src/pages/run_detail/PlanPanel.tsx`(复用,如需轻量 props 调整)
- `apps/admin-ui/src/config/env.ts`(Batch 4 入口,已有 helper 复用)
- `apps/admin-ui/src/i18n/locales/{en,zh-CN}.ts`(新增文案)
- 相关 `*.stories.tsx` / `*.test.tsx`

**后端(Batch 4):**
- `services/orchestrator/src/orchestrator/sse.py`(metadata trace_id + duration_ms)
- trace-facade 端点 + 测试(落点由子 spec 定)

---

## 12. 执行顺序与门禁

1. Batch 1 → 出实施计划 → TDD 落地 → code review → 合。
2. Batch 2 → 同上(依赖 Batch 1 的拆分骨架)。
3. Batch 3 → 先出线框对齐 → 实施计划 → 落地。
4. Batch 4 → **先出独立子 spec**(Langfuse 查询验证)→ 实施计划 → 落地。

每批独立可合、独立可回滚。前 3 批零后端依赖,任一批延后不阻塞其余。
