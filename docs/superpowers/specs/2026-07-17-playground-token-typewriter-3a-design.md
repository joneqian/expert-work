# Playground content 打字机(流式 epic 子项目 3a)— 设计

**状态**:设计定案(2026-07-17)
**类型**:前端 feature(admin-ui playground 流式渲染适配,第一期)
**范围**:纯前端。仅消费 #1000 已发的 `token` SSE 帧(`channel:"content"`),零后端改动。

## 背景

端到端 LLM token 流式 epic:P1/P1'(router 内部流式)+ 子项目 1(防御守卫 UI #998)+ 子项目 2(token SSE 帧 #1000 + O(n²) 重扫 #1001)已交付。子项目 3 = playground 流式适配,原 spec(`2026-07-16-llm-streaming-design.md` §P2b)明确是"**the full adaptation, not just a typewriter**",共 9 项 a-i,跨前后端。本 spec 只做**子项目 3a**:纯前端、仅用现有 `content` 频道的 token 帧。reasoning/tool_args 频道(需后端补发,#1000 推迟)归**子项目 3b**。

**现状核查(本会话核实)**:
- `token` 帧已进 `turn.events`,但所有渲染解析器(`summarizeTurn`/`parseTimeline`/`parseToolCalls`)只认 `updates` → **token 帧今天零可见效果**。
- dual-track split 存在(`PlaygroundTab.tsx:2130-2140`,running=纯文本/settled=markdown),但 `answer` 只随 `updates` 帧跳变,无 token 插值。
- **无任何渲染批处理**;当前"每帧全量重解析"(`summarizeTurn`/`parseTimeline` O(n) over events)对低频 `updates` 尚可,token 频率会打爆。
- cancel 已有(`abortRef`/`handleStop`);history/resume 天然隔离(token 不持久化,历史走 `GET .../events` 一次性回放,无 token 帧)。
- `PlaygroundTab.tsx` **2465 行**、`TurnCard` 内嵌——流式改动不应继续膨胀它。

**token 帧契约(#1000,`docs/api/streaming-events.md`)**:`event: token`,`data: {step:int, channel:"content", text:str}`,text 已服务端脱敏。provisional:权威 `updates` 帧为最终真相;不持久化、reconnect 不回放。judge-on/queue/cache/非流式 provider 不发 token。

## 目标 / 非目标

**3a 目标(a/b/e/f/g/h-前端/i)**:
- 流式 turn 里,**活跃 step 卡**逐字显示 live token(打字机),该 step 的权威 `updates` 到达后被权威卡取代。
- step timeline 高亮流式中的 step;显 TTFT。
- 中途卡/错/取消 → 保留 partial + 中断徽标。
- 历史 turn 完全走现有路径(不动)。
- 渲染合批,与 token 频率解耦。

**3a 非目标**:
- reasoning 频道 live 视图(c)、tool_args 流式重组(d)—— 需后端补发新频道,归 3b。
- 后端 cancel 中途停读验证(h 后端半)—— 归 3b。
- 主答案区(2130-2140)打字机 —— 已决策为 step 卡内(playground = 调试台心智)。
- token 帧脱敏 —— 已在服务端(#1000 buffered-release),前端只显示。

## 架构(方案 A:独立 live 状态 + rAF 合批 + 合成 streaming 卡)

关键约束:`parseTimeline` 只在 `updates` 帧落地时建 `AgentStep` 卡 —— 流式中该 step **尚无卡**;且 token 高频,**绝不能**让 token 进 `turn.events` 触发 O(n) 重解析。

**数据流**:
```
handleRun SSE 循环:每帧分流
  if frame.event === "token":  tokenStream.push(frame)      # 不进 turn.events
  else:                        setTurns(... push events ...) # 现有路径不变
        │
        ▼
useTokenStream(turnId, { active })        [新 hook,独立 state]
  ├─ push({step,text}) → liveRef.get(step) += text          # O(1),写 mutable ref
  ├─ 收到该 step 的权威 updates → liveRef.delete(step)       # reconcile:弃 provisional
  ├─ rAF flush → setLive(snapshot(liveRef))                 # ~1 render/帧
  ├─ 记首 token 时间戳 → ttftMs
  └─ 返回 { liveByStep: Map<step,string>, streamingStep|null, ttftMs|null, interruptedSteps:Set<step> }
        │
        ▼
StepTimeline(timeline, liveByStep, streamingStep, interruptedSteps, ttftMs)
  └─ 渲染 parseTimeline 的权威 AgentStep 卡 ∪ 合成 StreamingStepCard
       (step ∈ liveByStep 且无权威卡)
```

**分流**:`token` 帧只喂 hook,不入 `turn.events` → `parseTimeline`/`summarizeTurn` 零改动、不因 token 重跑。非 token 帧走完全现有路径。

**reconcile(命脉)**:
- `useTokenStream` 需知某 step 的权威 `updates` 已到 → 从 `liveRef` 删该 step。实现:hook 接收 `settledSteps: Set<number>`(由 `PlaygroundTab` 从 `parseTimeline(turn.events)` 的 `AgentStep.stepCount` 集合派生并传入),或 hook 内订阅 events 的 updates。**选前者**(单向数据流,hook 不碰 events):`useTokenStream(turnId, { active, settledSteps })`,`settledSteps` 变化时删除已 settle 的 live buffer。
- `streamingStep` = `liveByStep` 中最大且不在 `settledSteps` 的 step。
- 同一 step 位置:合成卡 → 权威卡替换,无闪烁(StepTimeline 按 step 排序,合成卡与权威卡互斥)。

**中断态(e)**:流结束(`end`/`error`/abort)时,`liveByStep` 中仍存在(未被 settle 删除)的 step → 标记 `interruptedSteps`,合成卡转 interrupted 外观(保留 partial 文本 + `InterruptedBadge`)。规则纯前端:"有 provisional 无权威即中断",不依赖后端中断帧形态。触发点:`handleRun` 的 finally/catch 调 `tokenStream.finalize(status)`。

**历史/resume(f)**:`useTokenStream` 仅当 `active`(live turn:`status==="running"` 起、非 `readOnly`)激活;历史 turn(readOnly、replay)不实例化 live 逻辑 → 现有路径零改动。守卫:hook 内 `if (!active) return 空快照`。

**取消(h,前端)**:复用 `abortRef`/`handleStop`。abort → 流断 → `finalize("done")` → 未 settle 的 live step 转 interrupted(保留 partial)。后端是否真中途停读属 3b。

## 组件 / 文件

新增(隔离流式关注点,不膨胀 `PlaygroundTab.tsx`):
- **`apps/admin-ui/src/pages/.../playground/useTokenStream.ts`**(~120 行):hook。state = live buffer(mutable ref + rAF-flushed snapshot)、ttftMs、interruptedSteps。API:`push(frame)`、`finalize(status)`、入参 `{ active, settledSteps }`,返回 `{ liveByStep, streamingStep, ttftMs, interruptedSteps }`。含 rAF 调度 + `cancelAnimationFrame` 清理。
- **`playground/StreamingStepCard.tsx`**(~60 行):合成卡。props `{ step, text, interrupted, ttftMs }`。渲染纯文本(`whiteSpace:pre-wrap`,**非 markdown** —— 流式中 markdown reflow janky)+ `StreamingBadge`(streaming 中)或 `InterruptedBadge`(中断)+ TTFT 角标。样式对齐现有 `AgentStepCard` 外观(同 StepTimeline 视觉语言)。

修改(仅接线,不搬大逻辑):
- **`StepTimeline.tsx`**:加 props `liveByStep?/streamingStep?/interruptedSteps?/ttftMs?`;在权威 `AgentStep` 卡序列中,为流式 step 插 `StreamingStepCard`(step 无权威卡时);流式卡高亮。
- **`PlaygroundTab.tsx`**:`handleRun`/`handleDecide` SSE 循环加 token 分流 → `tokenStream.push`;实例化 `useTokenStream`(仅 live turn);从 `parseTimeline` 派生 `settledSteps` 传入;把 hook 返回传给 `TurnCard`→`StepTimeline`;`finally`/`catch` 调 `finalize`。**只接线**。

i18n:新键 `playground.streaming_badge` / `playground.interrupted_badge` / `playground.ttft`(三处:`en.ts` 接口 + en 值 + zh-CN 值)。

## 渲染合批(i)

`useTokenStream` 内:token push 写 mutable `liveRef`(不触发 render);首次脏时 `requestAnimationFrame(flush)`,`flush` 做一次 `setLive(snapshot)` 并清 rAF 句柄。帧内多 token 只一次 render。unmount/finalize 时 `cancelAnimationFrame`。→ render 频率 ≤ 60fps,与 token 频率解耦。不处理 `prefers-reduced-motion`(打字机是内容增量,非装饰动画)。

## 测试

admin-ui 现有前端测框架(写 plan 时核实:vitest/jest + RTL),测:
- **`useTokenStream`**:按 step 累加;reconcile(`settledSteps` 含某 step → 该 step live buffer 删除、不再出现在 `liveByStep`);`streamingStep` 选取(最大未 settle);rAF 合批(多 push 一次 flush —— mock rAF/fake timers 断言 setLive 调用次数);`finalize` 后未 settle step 进 `interruptedSteps`;TTFT 捕获(首 push 时戳);`active=false`(readOnly)返回空快照、不实例化。
- **`StreamingStepCard`**:纯文本渲染(断言非 markdown 元素)、streaming badge、interrupted badge、TTFT 角标。
- **StepTimeline 集成**:流式 step 出合成卡 → 加入对应 `AgentStep`(同 stepCount)后合成卡消失、权威卡在、无重复。
- **分流**:token 帧不进 `turn.events`(断言 `parseTimeline` 输入不含 token → 不因 token 重跑;可断言 `turn.events` 无 token 帧或 parseTimeline 调用次数不随 token 增长)。
- **向后兼容**:无 token 帧(judge-on/queue/cache/非流式)→ StepTimeline 纯权威渲染,与今日一致。

验证:`pnpm typecheck`(真 `tsc -b`,不信编辑器 stale 诊断)+ 上述组件测 + 手动冒烟(真跑流式 agent:逐字进 step 卡 → `updates` 换权威卡 → Stop → partial+中断徽标 → 历史 turn 不受影响)。

## 后续(3b,本 spec 不做)

- 后端 `TokenSink` 补发 `reasoning` / `tool_args` 频道(带脱敏:reasoning/tool_args 亦可能含密钥,复用 buffered-release)。
- 前端:reasoning live 折叠视图(c)、tool_args 重组(d,完成时呈现非逐字符 args)。
- 后端 cancel 中途停读验证(h 后端半)。
- 3a 建的 `useTokenStream` + StepTimeline 合成卡基建供 3b 复用(reasoning/tool_args 走同 hook 的多频道扩展)。
