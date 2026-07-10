# 调试台 Batch 4a(实时每步/每工具耗时)子 spec

> **父 spec:** `docs/superpowers/specs/2026-07-10-playground-debug-console-redesign-design.md`(伞形 §Batch4 item 12)。
> **勘查依据:** 2026-07-10 Langfuse/计时勘查(见记忆 `playground-debug-console-batches`)。

## 1. 目标与范围

**目标:** 给调试台的 StepTimeline(Batch 3)填上 Batch 3 特意留空的「每步耗时」列 —— agent 步 / aux 节点显 node 级耗时,嵌套工具卡显 per-tool 耗时。**纯前后端、零 Langfuse、零外部依赖、实时(run 进行中即可见)。**

**范围拆分(经 brainstorm 定):** 伞形 Batch 4 两条独立轴拆两份子 spec:
- **本 spec = 4a 计时**,只做伞形 **item 12**(SSE `duration_ms`)。
- **4b(后出)** = 伞形 item 13/14/15(Langfuse trace-facade + span 树视图 + 直达外链)。
- **伞形 item 11(metadata 帧带 `trace_id`)挪去 4b** —— 它对计时零 payoff,且其「正确 trace_id」= Langfuse span 真正挂的那个(sse.py 后台任务 session span,`:406`),要甄别哪个 trace_id,是 facade/直达的固有关注点。4a 不碰。

**计时粒度决策(Q5):** 每步 + 每工具拆分,复用已有测量 —— node 走 superstep 间隔(sse.py 消费循环算,零 node 埋点),工具复用 builder.py 已算好的 per-tool `duration_ms`(现只进 audit_log)。**不新增 orchestrator 埋点**(合父 spec §9)。

## 2. 后端契约

**只碰两文件:** `services/orchestrator/src/orchestrator/sse.py`(node 计时 + 注入)+ `services/orchestrator/src/orchestrator/graph_builder/builder.py`(表面化已有 tool duration)。

### 2.1 node 级耗时 —— superstep 间隔(sse.py)

`sse.py` 的 astream 消费循环(`:414-437`)`updates` 流模式**一个 node 执行吐一个 chunk**。在循环里测相邻 chunk 到达墙钟差:

```python
last_frame_ts = ttft_started            # 复用 :394 已有的 RUNNING→首chunk 基线
async for chunk in graph.astream(...):
    now = time.monotonic()
    dur_ms = round((now - last_frame_ts) * 1000)
    last_frame_ts = now
    # 注入到每个 node 的通道 dict —— 不是帧顶层(见 §2.3)
    ...  # 在 _to_jsonable 之后、publish 之前给 jsonable_chunk[node] 加 "_duration_ms"
```

- 首 chunk 的 `dur_ms` = RUNNING → 首 chunk 的墙钟 ≈ TTFT(`:423` 已在算的那个),口径一致。
- 一 chunk 含多 node(罕见)时,各 node 带同一 superstep 值。

### 2.2 工具级耗时 —— 复用已有(builder.py)

`builder.py` 的工具派发(`:1989` `started = time.monotonic()` / `:2130-2132` `_elapsed_ms`)**已算好** per-tool `duration_ms`,现进 Prometheus + audit_log(`_emit_tool_audit`),从没进帧。把这**已有值**挂到该工具的 `ToolMessage`(确切落点 —— `additional_kwargs` vs 独立字段 —— 由实施计划定,以能过 `_to_jsonable` 序列化 + 被 `parseToolCalls` 读到为准),随 tools 节点的 `messages` 通道进帧。

- **口径:** 派发到完成、含 `before_tool_dispatch_chain` 中间件时间(即 builder 现有 `duration_ms` 原义)—— 认可作为「工具真实花的墙钟」展示。
- **不新增计时数学** —— 仅表面化已有值。

### 2.3 帧形状(关键决策)

chunk 是 `{node名: {通道...}}`。node 级 `_duration_ms` **塞进 node 的通道 dict**,不塞帧顶层:

```jsonc
{ "agent": { /* …现有通道… */, "_duration_ms": 1200 } }
```

- **为何不塞顶层:** `parseTimeline`(Batch 3 `timeline.ts`)把 `data` 每个顶层 key 当 node 名 `Object.entries` 遍历 —— 顶层加 `_duration_ms` 会被误当成一个 node。塞通道内则**非破坏**:Batch 1-3 各 parser 忽略未知通道 key。
- 备选(独立 `timing` SSE 事件)已否:多帧 + 相关性复杂。
- 工具 `duration_ms` 走 §2.2 的 ToolMessage,不用这个通道 key。

## 3. 前端契约

**碰三文件:** `api/timeline.ts`、`api/tool_timeline.ts`、`pages/agent_detail/playground/StepTimeline.tsx`(+ i18n en/zh 各一键)。

### 3.1 解析器读出

- `timeline.ts` `parseTimeline`:读 node 通道的 `_duration_ms`(typeof-number 守卫,否则 null)→ `AgentStep.durationMs: number | null` + `AuxNodeItem.durationMs: number | null`。
- `tool_timeline.ts` `parseToolCalls`:读工具结果消息上的 duration → `ToolCallEntry.durationMs: number | null`。

### 3.2 StepTimeline 渲染(填 Batch 3 空位)

- **agent 步头:** Batch 3 头是 `步骤号·node·model·finish·token`(当时明写「不显耗时,留 Batch 4」)→ 追加耗时到这行。
- **工具卡头:** 状态徽章旁显 per-tool 耗时。
- **aux 行**(memory_recall/planner/reflect):同走 `_duration_ms`,一并显。
- `durationMs === null` → 隐藏、不占位。
- **不做「慢步」阈值染色**(阈值主观,按 node 类型分别定值得单独想)→ follow-up。
- **无 per-step 耗时以外的新计时展示**(不显墙钟以外估算)。

### 3.3 格式

自适应纯函数 `fmtDuration(ms: number): string`:

| 输入 | 输出 |
|---|---|
| `< 1000` | `"820ms"` |
| `>= 1000`(< 60000) | `"1.2s"`(1 位小数) |
| `>= 60000` | `"1m2s"` |

边界(999ms → "999ms" / 1000ms → "1.0s")由测试锁。

### 3.4 i18n

数字+单位语言中立,不需翻译键。**只加一个** aria-label 键 `tl_duration`(en 值 `"step duration"` / zh 值 `"本步耗时"`),三处 parity(en 类型+值 / zh 值):

```jsx
<span aria-label={t("playground.tl_duration")}>{fmtDuration(ms)}</span>
```

屏幕阅读器读「本步耗时 1.2s」;视觉只见 `1.2s`。

## 4. 测试计划

**后端(pytest,`uv run`):**
- `updates` 帧的 node 通道含 `_duration_ms`(数值)。
- 首 chunk 的 duration 基线 = RUNNING→首 chunk(TTFT 口径)。
- 工具结果消息含 per-tool `duration_ms`(复用值)。
- 无计时数据 → 字段缺席、不报错、不 500。

**前端(vitest):**
- `parseTimeline` 读 `_duration_ms` → durationMs(present/absent → number/null)。
- `parseToolCalls` 读工具 durationMs(present/absent)。
- `fmtDuration` 边界:999ms→"999ms" / 1000ms→"1.0s" / 60000ms→"1m0s"。
- `StepTimeline`:渲染格式串;`durationMs===null` 时该处不渲染;aria-label 存在。

**手动冒烟:** 跑一条含工具/多步的 run → StepTimeline 每步头显耗时、工具卡各显 per-tool ms、run 进行中即实时可见。

## 5. 不在范围(YAGNI)

- item 11 trace_id / 任何 Langfuse / facade / span 树 / 直达外链(→ 4b)。
- 慢步阈值染色(follow-up)。
- 新增 orchestrator span 埋点(父 spec §9;本 spec 只表面化已有测量 + sse.py 消费侧算 superstep 间隔)。
- per-node「纯计算」精确耗时(选 B 非 C;superstep 间隔=墙钟到结果,是要的口径)。

## 6. 文件触点

**后端(Batch 4a):**
- `services/orchestrator/src/orchestrator/sse.py`(node superstep 间隔 + 注入通道)
- `services/orchestrator/src/orchestrator/graph_builder/builder.py`(表面化已有 tool `duration_ms` 到 ToolMessage)
- 对应 pytest

**前端(Batch 4a):**
- `apps/admin-ui/src/api/timeline.ts`(AgentStep/AuxNodeItem `.durationMs`)
- `apps/admin-ui/src/api/tool_timeline.ts`(ToolCallEntry `.durationMs`)
- `apps/admin-ui/src/pages/agent_detail/playground/StepTimeline.tsx`(渲染 + `fmtDuration`)
- `apps/admin-ui/src/i18n/locales/{en,zh-CN}.ts`(单键 `tl_duration`)
- 对应 vitest

## 7. 依赖与顺序

后端(item 12 帧)与前端(读+渲染)有契约依赖:先落后端 `_duration_ms`/tool duration 帧形状,前端按真帧解析。序:**后端计时 → 前端解析 → 前端渲染**(前端解析可对着 §2.3 帧形状先行,以后端契约为准)。

## 8. Batch 1-3 教训延续

- 前端验证用 `pnpm typecheck`(=`tsc -b --noEmit`)非裸 `npx tsc`;`ReactNode` 具名 import,不注解 `JSX.Element` 返回。
- 后端 uv workspace:pytest/mypy 走 `uv run` + root config。
- e2e testid 与 src 一起改(本 spec 不改 testid,仅加耗时文本,风险低;仍 grep 核 e2e)。
- 编辑器 "React UMD"/"module not found" 诊断多为 stale,以亲跑为准。
