# Playground reasoning + tool_args 流式(流式 epic 子项目 3b)— 设计

**状态**:设计定案(2026-07-17)
**类型**:全栈 feature(后端 `TokenSink` 补 reasoning/tool_args 频道 + admin-ui playground 多频道 live 视图)
**范围**:后端一个类(`TokenSink`)加宽三频道 + 前端 3a 基建加宽三频道 + 一条 cancel 传播核查测。

## 背景

端到端 LLM token 流式 epic 的收尾。前序:P1/P1'(router 内部流式)、子项目 1(防御守卫 UI #998)、子项目 2(token SSE 帧 #1000 + O(n²) 重扫 #1001)、子项目 3a(content 打字机 #1002,纯前端)均已交付。

原 spec(`2026-07-16-llm-streaming-design.md` §P2b)明确子项目 3 是"**the full adaptation, not just a typewriter**",九项 a-i。3a 只做了纯前端、仅 `content` 频道的部分(a/b/e/f/g/h-前端/i)。**3b 收口剩余**:c(reasoning 频道)+ d(tool_args)+ h-后端(cancel 中途停读)。

**现状核查(本会话核实,载入设计前提)**:
- **`LLMDelta` 已带三频道**:`content` / `reasoning` / `tool_calls: tuple[ToolCallChunk]`(`ToolCallChunk{index,id,name,args_fragment}`)。见 `llm/providers/_streaming.py`。
- **router 每 delta 都 `await sink(delta)`**(`router.py:672-674` 与 `696-698`,两 `assembler.add` 点之后),**含 reasoning-only、tool-only delta**。今天 `TokenSink.__call__` 只读 `delta.content`,**reasoning/tool_calls 已流到 sink 但被丢弃**。
- ⟹ **3b 后端改动全在 `TokenSink` 一个类**。router 侧零改;构造 site(`builder.py:793` `make_token_sink(step,publish,dlp,screen,judge_enabled)`)签名不变;传输 `_publish_token(frame)`(`sse.py:369`)帧无关(`bridge.publish(run_id,"token",frame)`),加新频道帧零改 sse。
- **门控不变**:judge-off ∧ publish 存在(`make_token_sink` 返 None 否则)。judge-on 回退 step 级帧,不流。
- **cancel 机制已存在**:`builder.py:811` `token.run_cancellable(...)`(E.15,cancel 中途打断在飞 await)。3b 只**核查**它传播到关闭上游 httpx 流(不泄漏还在生成/计费的连接)。
- **3a 前端面**:`useTokenStream` → `liveByStep: Map<step, string>`(content-only,`parseContentToken` 拒非 content);`StreamingStepCard` 渲 text + badges;`StepTimeline` 按权威 `stepCount` 抑制同 step 合成卡;`PlaygroundTab` 分流 token 帧不进 `turn.events`。

## 目标 / 非目标

**3b 目标(c / d / h-后端)**:
- reasoning 频道逐字 live 进活跃 step 卡(**流时展开 → content 起 / step 落定后收起为"💭 思考 Xs"摘要,可重展**)。
- 工具调用 live 存在:工具名首现即显(名字 chip);args **完成时**经权威 `updates` 卡呈现(非逐字符)。
- 纯 reasoning 步、纯工具步现在也有 live 合成卡(3a 只有 content 步有 → 推理模型思考期、工具步生成期不再空白)。
- reasoning / tool name 频道脱敏与安全:reasoning 复用 buffered-release;tool name 非敏感不脱敏;args 不流(名字-only)故零 args 脱敏路径。
- 核查 client abort 中途停读上游 LLM 流(不泄漏计费连接)。

**3b 非目标**:
- **tool args 逐字流式 / 前端 arg 片段重组**:名字-only 版(用户拍板);args 只经权威 `updates` 卡(本就非逐字符呈现完整 tool call)。合成卡不显 args。
- **主动 cancel 修**:仅核查;若测暴露泄漏 → 上报用户定,不静默扩成修。
- reasoning 持久化 / 历史回放:token 帧 live-only 不持久化(#1000 契约),历史轮走 `updates` 回放,天然无 reasoning/tool_args token 帧。
- 无界 email 跨 hold 残留:同 content,provisional 契约由权威帧兜底。

## 架构(方案 A:加宽 3a 管道到三频道)

3b = 把 3a 已验证的分流管道**加宽**,非新管道。数据模型取舍:**加宽 `liveByStep` 值类型 `string → LiveStep`**(单 hook / 单合成卡 / 渲染期 reconcile 全不变),对比每频道独立 hook(穿线翻三倍、打碎"一步一卡")或泛化 channel-map(3 已知频道过度抽象 YAGNI)。

**命脉不变**:token 帧仍**分流不进 `turn.events`**(3a 的 O(n) `parseTimeline`/`summarizeTurn` memo 前提)。3b 只让 hook 认三频道、合成卡渲三区。

```
router _drive_stream: 每 delta await sink(delta)          [已存在,含 reasoning/tool delta]
        │
        ▼
TokenSink.__call__(delta)                                 [后端:唯一改动点]
  ├─ content   → StreamingRedactor #1 → {step,channel:"content",text}      [3a 已有]
  ├─ reasoning → StreamingRedactor #2 → {step,channel:"reasoning",text}    [3b 新;独立流状态]
  └─ tool_calls→ 名字首现 → {step,channel:"tool_args",tool_index,name}     [3b 新;名字-only]
        │  (SSE token 事件,live-only 不持久化)
        ▼
PlaygroundTab 分流: if frame.event==="token" → tokenStream.push; continue  [3a 已有]
        │
        ▼
useTokenStream: liveByStep: Map<step, LiveStep>           [加宽值类型]
        │
        ▼
StreamingStepCard 三区: 💭reasoning(折叠) / 🔧tool 名字 / content 打字机
        │
        ▼
StepTimeline reconcile: 权威 stepCount 落地 → 合成卡换权威卡  [3a 逻辑不变]
```

## 后端:`TokenSink` 三频道

`services/orchestrator/src/orchestrator/graph_builder/streaming_redact.py`。唯一后端改动点。

```python
class TokenSink:
    def __init__(self, *, step: int, publish: TokenPublish, dlp: bool, screen: bool) -> None:
        self._step = step
        self._publish = publish
        self._content = StreamingRedactor(dlp=dlp, screen=screen)
        self._reasoning = StreamingRedactor(dlp=dlp, screen=screen)   # 独立流状态
        self._tool_names: dict[int, str] = {}                         # index → name(只发一次)

    async def __call__(self, delta: LLMDelta) -> None:
        safe = self._content.feed(delta.content)
        if safe:
            await self._publish({"step": self._step, "channel": "content", "text": safe})
        rsafe = self._reasoning.feed(delta.reasoning)
        if rsafe:
            await self._publish({"step": self._step, "channel": "reasoning", "text": rsafe})
        for tc in delta.tool_calls:
            if tc.name and tc.index not in self._tool_names:
                self._tool_names[tc.index] = tc.name
                await self._publish({
                    "step": self._step, "channel": "tool_args",
                    "tool_index": tc.index, "name": tc.name,
                })

    async def flush(self) -> None:
        tail = self._content.flush()
        if tail:
            await self._publish({"step": self._step, "channel": "content", "text": tail})
        rtail = self._reasoning.flush()
        if rtail:
            await self._publish({"step": self._step, "channel": "reasoning", "text": rtail})
```

**要点**:
- **reasoning = 第二个 `StreamingRedactor`**:content 与 reasoning 是 delta 里两个独立字段、两条独立文本流,脱敏状态**不能共享**(共享会把一条流的 hold 尾误接到另一条)。逐字镜像 content。
- **tool name 名字-only**:`tc.name` 首现(某 index 第一帧携带)即发一帧;后续该 index 不再发。工具名 = 声明的静态标识(search_web),非用户数据/非密钥 → **不脱敏**。args 不累积、不流(名字-only 决策),故 sink 里无 args 状态。
- **flush**:content + reasoning 各自 flush 残尾;**无 tool_args flush**(名字-only)。
- 构造 site(`builder.py:793`)、门控(`make_token_sink`)、`_publish_token`(`sse.py`)全不动。

### tool_args args 的"完成时呈现"= 权威卡(名字-only 决策,已拍板)

用户选名字-only(YAGNI):
- 后端每工具 1 帧(仅 name)。**零 args 脱敏路径**(args 不流 → 无跨-delta 密钥重扫风险)。
- args"完成时呈现非逐字符"= 权威 `updates` 卡(本就渲完整 tool call,全守卫已过)。合成卡只显工具名 live 存在(工具生成期,早于 flush/`updates`)。
- 代价:合成卡本身不显 args(略缩原 spec"重组 d"意图);安全上是红利。

## SSE 契约变更

`docs/api/streaming-events.md`:`channel` 从 "always content, others reserved" → 三频道枚举。

```
event: token
data: {"step": 0, "channel": "reasoning", "text": "let me think about..."}
event: token
data: {"step": 0, "channel": "tool_args", "tool_index": 0, "name": "search_web"}
```

- `channel`: `"content"` | `"reasoning"` | `"tool_args"`。
- `reasoning` 帧:`{step, channel, text}`(text 已服务端脱敏,同 content)。
- `tool_args` 帧:`{step, channel, tool_index, name}`(工具名,不脱敏;每 `tool_index` 首现一帧;**无 text/args** —— args 走权威 `updates`)。
- 全频道 provisional、live-only 不持久化、reconnect 不回放(同 content,不变)。

## 前端:多频道 useTokenStream + 三区合成卡

### 数据模型(加宽值类型)

`apps/admin-ui/src/pages/agent_detail/playground/useTokenStream.ts`:
```ts
export interface LiveStep {
  content: string;
  reasoning: string;
  toolNames: ReadonlyMap<number, string>;   // tool_index → name(名字-only)
  reasoningMs: number | null;               // 思考时长,收起后显;流式中(未收起)null
}
export interface TokenStreamState {
  liveByStep: ReadonlyMap<number, LiveStep>;
  ttftMs: number | null;
  finalized: boolean;
}
```

`push(frame)` 按 `channel` 分派(runtime 窄化 `SseEvent.data`):
- `content` → 追加 `content`(+ 记 per-step `contentStart` 首个 content 时戳)。
- `reasoning` → 追加 `reasoning`(+ 记 per-step `reasoningStart` 首个 reasoning 时戳)。
- `tool_args` → `toolNames.set(tool_index, name)`。
- 非上述 / 缺字段 → 忽略(同 3a 的 `parseContentToken` 防御)。

**reasoningMs**(hook 内 refs 记 `reasoningStart`/`contentStart` per step,快照构建时算):
- content 已起:`reasoningMs = contentStart − reasoningStart`(收起后固定)。
- 纯工具步(finalize 时仍无 content):`reasoningMs = finalizeTime − reasoningStart`。
- 否则(仍在 reasoning、未收起):`null`(不显时长,无需 live 跳秒)。

rAF 合批、TTFT 一次、`reset`/`finalize`/unmount `cancel` 生命周期全沿用 3a。`liveByStep` 条目仅在某频道首帧到达时创建 → 无空信号卡。

### StreamingStepCard 三区

`playground/StreamingStepCard.tsx`(加宽 props):各区仅在该频道非空时渲。
```
┌─ Step N   [streaming]  [TTFT]  ─────────┐
│ 💭 思考 8s  ▾    (可折叠)                │  reasoning 区:流时展开→content 起/落定收起为摘要,可重展
│    <reasoning text, pre-wrap>           │
│ 🔧 search_web   🔧 read_file             │  tool 区:名字 chip(多工具=多 chip);args 不在此
│ <content 打字机, pre-wrap>               │  content 区:同 3a
└─────────────────────────────────────────┘
```
- **reasoning 折叠**:`expanded = 流式中(!interrupted && content 为空)`。content 非空 或 interrupted/落定 → 收起摘要"💭 思考 {fmtDuration(reasoningMs)}",点击可重展(局部 `useState`)。reasoning 空 → 不渲该区。
- **tool chip**:`toolNames` 按 index 排序,每个渲一 chip。空 → 不渲。
- **content**:同 3a(`pre-wrap` 纯文本非 markdown)。空 → 不渲。
- streaming/interrupted/TTFT 徽标同 3a。

### StepTimeline reconcile(逻辑不变)

`playground/StepTimeline.tsx`:按权威 `AgentStep.stepCount` 集合抑制同 step 合成卡。3b 副效应=**纯 reasoning 步 / 纯工具步现在也出合成卡**(3a 只有 content 步有)—— 赢面。`updates` 落地照旧换权威卡。**命门(规划核实)**:确认纯 reasoning 步、纯工具步的权威 timeline 有非-null `stepCount`(源 `builder.py step_count`),否则合成卡不被抑制 → dup。

### PlaygroundTab(仅接线,不膨胀)

`handleRun`/`handleDecide` 分流循环、live props 只传 `turn.id===streamTurnId`、`finally` finalize —— **全不变**(hook 返回类型加宽,PlaygroundTab 透传,无逻辑改)。

## Cancel 中途停读核查(h-后端)

加一条后端测:中途取消消费 router 流的 task,断言上游 provider 流被关闭(spy provider stream 的 `aclose` / 捕获 `GeneratorExit`)。验证 client abort → `CancelledError`/`GeneratorExit` 经 astream → graph 节点 → router `_drive_stream` 的 `async for delta in it` → httpx `async with client.stream(...)` 的 `__aexit__` 传播关闭,**不泄漏还在生成(计费)的上游连接**。

`builder.py:811` `token.run_cancellable` 已提供 cancel 打断在飞 await;本核查确认它一路传播到上游流关闭。**测通过 = 文档化 + 回归守卫**;**测暴露泄漏 → 上报用户定**(那是非目标里的"主动 cancel 修")。

## 脱敏安全

- **reasoning**:第二个 `StreamingRedactor`,与 content 同款 buffered-release + `HOLD_CHARS=64`。#1000 已证所有 screen 最小匹配长(sk-≥23/AKIA=20/xox=15/PEM=27/AIza=39/exfil=37)<64 → 整凭据 latch 在 hold 窗内,partial 头不逃。reasoning 是散文(同 content 形态)→ 保证同款。残留仍是无界 email(病态长早期 straddle,provisional 权威帧兜底)。
- **content × reasoning 不跨流**:delta 里两独立字段,模型不把一 token 劈两频道 → 各自独立 redactor 正确;交错 content/reasoning delta 不串(专测)。
- **tool name 不脱敏**:声明的静态工具标识,非密钥/非用户数据。安全。
- **tool args 不流**:名字-only → 零 args 脱敏路径;args 只经全守卫的权威 `updates` 卡。

## 测试

后端(`test_streaming_redact.py` 扩):
- reasoning 帧带脱敏(reasoning 中密钥 → redacted,buffered-release hold 尾)。
- reasoning 与 content redactor 状态隔离(交错 content+reasoning delta 不交叉污染)。
- tool name 每 index 只发一次;多工具并行(多 index)各一帧。
- flush 只发 content+reasoning 尾,无 tool_args 帧。
- 门控不变(judge-on → sink None)。
- **content 频道零回归**(3a 行为不变)。

前端(vitest + RTL):
- `useTokenStream`:三频道按 step 累加;reasoningMs 两路(content-start / finalize);rAF 仍单次 flush;reset/finalize;非法/缺字段帧忽略。
- `StreamingStepCard`:reasoning 折叠行为(流展开→content 起收起→重展)、tool chip(多)、三区按空隐藏、中断态、TTFT。
- `StepTimeline`:纯 reasoning 步出合成卡、纯工具步出合成卡、stepCount 抑制(落权威卡后合成卡消失)、content-only 向后兼容。

Cancel 核查测(后端,见上)。

验证:`cd services/orchestrator && uv run python -m pytest`(裸 python 挑不动编译扩展);`cd apps/admin-ui && pnpm typecheck`(真 `tsc -b`,不信编辑器 stale 诊断)+ 组件测;手动冒烟:真推理模型(glm-z1/deepseek-r1 出 `reasoning_content`)→ 思考 live 流进卡 → 答案起收起为"思考 Xs" → 工具步显工具名 chip → 中途 Stop → partial+中断徽标 → 历史轮不受影响。

## 文件清单

后端:
- 改 `services/orchestrator/src/orchestrator/graph_builder/streaming_redact.py`(`TokenSink` 三频道)。
- 改 `docs/api/streaming-events.md`(channel 枚举 + reasoning/tool_args 帧)。
- 扩 `services/orchestrator/tests/test_streaming_redact.py`(reasoning/tool name/隔离/flush/门控/回归)。
- 新/扩 cancel 传播核查测(定位规划时核实现有 stream 取消测归处)。

前端:
- 改 `apps/admin-ui/src/pages/agent_detail/playground/useTokenStream.ts`(`LiveStep` 多频道 + reasoningMs)。
- 改 `playground/StreamingStepCard.tsx`(三区 + reasoning 折叠)。
- 改 `playground/StepTimeline.tsx`(透传加宽 props;reconcile 逻辑不变)。
- 改 `PlaygroundTab.tsx`(透传加宽 hook 返回;无逻辑改)。
- 扩各 `__tests__`;i18n 新键 `playground.reasoning_label`("思考",流时/展开显)+ `playground.reasoning_summary`("思考 {{d}}",收起显时长,`{{d}}` 复用 `fmtDuration`);三处(接口 + en 值 + zh 值)。

## 后续(本 spec 不做)

- tool args 逐字 live 重组(若将来要合成卡内看 args 成形)。
- 主动 cancel 修(若核查暴露上游泄漏)。
- reasoning 频道之外的多频道扩展已由本设计的 `LiveStep` 模型承载,后续频道加字段即可。
