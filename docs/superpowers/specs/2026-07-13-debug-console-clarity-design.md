# 调试台清晰度重构 — 设计 spec

> 状态:已评审(线框 + 完整页 + AskUserQuestion 决策全过)。下一步:writing-plans 拆实施计划。
> 线框:`2026-07-13-debug-console-clarity-wireframe.html`(分屏标注版) / `2026-07-13-debug-console-full-page.html`(完整可交互页)。

## 目标

调试台三根支柱:

1. **清晰反馈整个 run 过程** —— 步骤序列、每步耗时/状态一眼可读。
2. **清晰展示所有数据** —— 全保真数据可达,但通过**分层渐进披露**避免噪声淹没。
3. **快速定位问题** —— 错误从底层**冒泡**到顶,不用扫屏找。

核心张力:「展示所有数据」与「排除干扰/减少心智负担」互斥。唯一解 = **分层渐进披露**:默认层只给信号,全保真数据永远在「一次下钻之外」。

## 背景:两视图现状不对称

调试台有两个视图(用户在两者间切换,非同屏):

| 视图 | 天然职责 | 现状 |
|---|---|---|
| **时间线**(`StepTimeline`,SSE 语义/因果流) | 啥步骤、啥顺序、每步决定了啥 | ✅ 基本达标(错误冒泡已有:红边框+自动展开+error marker) |
| **trace 精确视图**(`TraceView`,Langfuse 时序/成本) | 耗时/token/cost + 全数据下钻 | ❌ 重灾区:无错误冒泡、标签挂错、扁平 pre 塞原始噪声 |

两视图**不合并**(职责不同、硬合并丢瓶布时序 + 双数据源对齐代价大)。各司其职,**共享**清洗/错误冒泡组件。

## Spike 实证(dev 环境,read-only,不改码)

对 dev Langfuse 真 agent-run trace(`a54cfbbc` 等,「现在是几号几点」run)实测,推翻/确认了若干假设:

- **E-1 · 错误状态存在**:observation 带 `level`(`ObservationLevel.DEFAULT|WARNING|ERROR`)+ `status_message`。facade 可提取。⚠️ 所探 run 全成功(均 DEFAULT)—— 见 R1。
- **E-2 · 写时不截断(LLM)**:Langfuse 实存 GENERATION `input` 达 **35,680 / 70,685 字全量**,尾部干净非截断符。**当前截断纯是 facade 读时 `io_cap=32768`**,不是写时。→ raw 端点对 LLM 能真返 70k 全文。
- **消息结构 = LangChain 消息 dict**,`role` **恒为 None**,真正角色在 **`type`** 字段(`system/human/ai/tool`)。这是现状 `[message]` 乱标的**根因**。
- 带 `tool_calls` 键的 ai 消息 content 可能为空(len 0)。
- system prompt 常达 **33,884 字且常重复**(同一份出现在 msg[0] 与 msg[3])。
- **工具 span** input = `{code: …}` dict、output = str → 走 text kind(非消息 list)。仅 GENERATION 走结构化消息。
- 工具 i/o 写时 cap = `_TOOL_IO_CAP=8192`(`builder.py`);所探工具 114/144 字,远没到 → 全量存。仅 >8192 的工具写时截。
- **不可信标记精确串**(`packages/expert-work-common/.../spotlight.py`):
  - 围栏:`«UNTRUSTED nonce=<hex>»\n<内容>\n«/UNTRUSTED nonce=<hex>»`
  - 字形:`DATAMARK_GLYPH = "▁"`(U+2581);datamark 把每段空白 run 换成 `"▁ "`(glyph+单空格),**有损**(原换行已折叠)。清洗只能剥 `▁` 留单空格。

## 决策记录(AskUserQuestion)

1. 范围 = **整个调试台**(时间线 + trace,保留两视图各司其职)。
2. i/o 契约 = **后端发结构化 `Message[]`**(非拍平字符串)。
3. 噪声 = **清洗 + 保留「含不可信」badge**(前端共享 util,覆盖两视图)。
4. 错误冒泡 = **全套**(顶部 run 状态条 + 内联红标 + 详情错误)。
5. 全数据兜底 = **建 raw 端点**看未截断全文 + 宽 cap/复制。
6. E-2 = **spike 先摸底**(已完成,见上)。

## 架构:两视图 × 三层 + 两共享件

```
时间线视图(语义/因果)          trace 视图(时序/成本 + 全数据)
  默认层: 步骤卡(现状)           默认层: 瀑布(现状)
  下钻层: 工具卡 i/o(接清洗)      下钻层: 节点详情(结构化消息) ← 重改
                                   原文层: raw 全文(新端点)
        └──────── 共享 ────────┘
   ① 不可信清洗 util(剥 ▁ / UNTRUSTED→badge)
   ② RunStatusBanner(顶部错误冒泡)
```

---

## 组件设计

### A. 后端 facade(`services/control-plane/.../api/trace_facade.py`)

#### A1. i/o 结构化契约

`TraceSpan.input/output` 从 `str | None` 改为结构化 `RenderedIo | None`。

```python
# 判别联合(dict 序列化,camelCase)
RenderedIo =
  | { "kind": "messages", "messages": list[RenderedMessage] }
  | { "kind": "text", "text": str, "truncated": bool, "fullChars": int }

RenderedMessage = {
  "role": str,          # 取自 LangChain `type`(system/human/ai/tool),fallback `role`,再 fallback "message"
  "content": str,       # 已按 MSG_CAP 截断
  "truncated": bool,
  "fullChars": int,     # content 原始长度(未截断)
  "toolCalls": list[str] | None,  # ai 消息的 tool_calls 名字列表(content 空时前端显「→ 调用 X」);无则 None
}
```

规则:
- **role 提取**:`m.get("type") or m.get("role") or "message"`(修 `[message]` 根因)。
- **messages vs text 判别**:`value` 是 list 且每项 dict 含 `content` 键 → messages;否则(dict/str/其它)→ text。output 同判别(通常落 text:str 或 dict)。
- **content 提取**:str 原样;block-list(`[{type,text}]`)取 text 拼接(沿用现状 `_render_io` 逻辑)。
- **toolCalls**:从 `m` 的 `tool_calls`(或 `additional_kwargs.tool_calls`)取 `name` 列表。
- **按消息截断**:每条 content cap `MSG_CAP = 8192`;text kind cap `TEXT_CAP = 16384`。`fullChars` 记原长,`truncated = 原长 > cap`。**不再整串头部切**(修 P0,保尾部对话)。

常量:`MSG_CAP = 8192`,`TEXT_CAP = 16384`,`_TRUNCATION_SUFFIX = "…(已截断)"`(保留)。

#### A2. 错误字段

`TraceSpan` 新增:

```python
level: str            # "default" | "warning" | "error",取自 ObservationLevel(小写)
status_message: str | None  # 取自 observation.status_message,清洗为 None-or-str
```

提取:`level = str(getattr(o, "level", "DEFAULT")).rsplit(".", 1)[-1].lower()`(enum → "error");`status_message = _clean_str(getattr(o, "status_message", None))`。session/http_request 省略后不影响。

顶部 run 状态条**不加后端 run 级字段** —— 前端从 span 的 `level` 派生。

#### A3. raw 端点

```
GET /v1/sessions/{thread_id}/runs/{run_id}/trace/raw?span=<obs_id>&field=input|output
→ 200 { "spanId": str, "field": "input"|"output", "content": str }   # 未截断 + 未清洗(含原始 ▁/UNTRUSTED)
→ 404 (span 不存在 / 无权 / 无 trace)
```

- **ownership 门复用** trace 端点那套(thread tenant + `caller_owns_thread`,404 隐藏跨租户存在)。
- 复用 `client.api.trace.get(trace_id)`,按 `span=obs_id` 找 observation,渲染该 `field` 的**全量文本**(messages 拍平为 `[role]\ncontent`,**无 cap**),原样返回(不跑清洗)。
- best-effort degrade:client None / NotFound / 异常 → 404(不 500)。

### B. 前端 DTO 镜像(`apps/admin-ui/src/api/trace_facade.ts`)

`TraceSpan.input/output: RunTraceIo | null`,新增 `level`/`statusMessage`。类型:

```ts
type RenderedMessage = { role: string; content: string; truncated: boolean; fullChars: number; toolCalls: string[] | null };
type RunTraceIo =
  | { kind: "messages"; messages: RenderedMessage[] }
  | { kind: "text"; text: string; truncated: boolean; fullChars: number };
// TraceSpan 增:level: "default" | "warning" | "error"; statusMessage: string | null;
```

`fetchRunTraceRaw(threadId, runId, spanId, field): Promise<string>` —— 拉 raw 端点。

### C. `TraceView.tsx` 重改

- **`IoSection` 拆两态**:
  - `kind==="messages"` → **结构化消息渲染**:每条消息一可折叠块(`MessageBlock`)。`role` 决定色标(system/human/ai/tool,复用 `--ew-*`)。**`role==="system"` 默认收起**(头显 `role · fullChars 字`);其余默认展开。content 经清洗 util 渲染。`truncated` → 底部 `已截断 N 字` + `复制本段` + `查看原文`(拉 raw 端点弹层)。`toolCalls` 非空且 content 空 → 显 `→ 调用 name`。含不可信 → 头挂 `⚑ 含不可信` badge。
  - `kind==="text"` → 单 `<pre>`(经清洗),`truncated` → 同上截断行。
- **kind 自适应标签**(干掉硬编码 `tr_io_*`):按 `span.kind`
  - `llm` → 输入区「对话消息」、输出区「回复」
  - `tool` → 输入区「参数」、输出区「结果」
  - 其它 → 「输入」「输出」(通用)
- **错误红标**:`span.level==="error"` → 瀑布树点/甘特条转红(`--ew-text-danger`)、行底浅红;`TraceDetail` 顶插 `dt-err` 块显 `status_message`。
- 干掉 `IoSection` 的 `maxHeight:180`,改 `maxHeight:280`(每块独立滚动)。
- **移除** `maxHeight` 憋屈:消息块 body 各自 `max-height:280;overflow:auto`。

### D. 共享:`untrusted_clean.ts`(新,`apps/admin-ui/src/.../playground/`)

```ts
export function cleanUntrusted(text: string): { text: string; hadUntrusted: boolean }
```

- 检测 + 去围栏:`«UNTRUSTED nonce=<...>»`(开)与 `«/UNTRUSTED nonce=<...>»`(闭)全部移除,连其相邻换行;任一命中 → `hadUntrusted = true`。正则用非全局 `.test()` 或 `.match()`(避免 lastIndex 坑)。
- 剥字形:移除所有 `▁`(`▁`)。
- 返回 `{ text: 清洗后, hadUntrusted }`。
- **消费方**:`TraceView` 的 `MessageBlock`/text `<pre>` + 时间线 `ToolCallCard`(见 F)。raw 原文层**不跑**此 util(要看原始标记)。

### E. 共享:`RunStatusBanner.tsx`(新)

```ts
interface RunStatusBannerProps {
  status: "ok" | "error";
  summary: string;                 // "运行成功 · 6 步 · …"
  metrics?: { label: string; value: string }[];  // ok 态:耗时/tokens/$
  errorLabel?: string;             // err 态:出错节点 label
  errorMessage?: string;           // err 态:status_message
  onJump?: () => void;             // err 态:跳到出错节点
}
```

- 纯展示 dumb 组件。挂两视图顶部。各视图**自算 status**(见数据流)。

### F. 时间线视图小改(`StepTimeline.tsx` + `ToolTimeline.tsx`)

- `ToolCallCard` 渲染工具 result 时接 `cleanUntrusted`(剥噪声 + 挂 `⚑ 含不可信` badge)。
- 顶部挂 `RunStatusBanner`:status 从 items 派生(任一 agent step `hasError` 或 error marker → error;errorLabel = 该步)。**时间线的 error 用 SSE 数据(现有可靠)**,不依赖 Langfuse level。
- 其余步骤卡骨架不动。

---

## 数据流

**trace 视图**:`PlaygroundTab` 拉 `trace`(现有)→ 计算 `bannerStatus`(遍历 spans:有 `level==="error"` → error,firstErrorLabel = 首个 error span,onJump=选中它)→ `<RunStatusBanner>` + `<TraceView>`。`TraceView` 点行 → `TraceDetail`;详情内 `MessageBlock` 的 `查看原文` → `fetchRunTraceRaw` → 弹层。

**时间线视图**:items 派生 banner status(SSE hasError)→ `<RunStatusBanner>` + `<StepTimeline>`。

## 错误处理 / 降级(硬约束:降级永不 500)

- facade 全 best-effort(现有 try/except 降级链保留:client None→unavailable、NotFound→not_ready、latency None→not_ready、normalize 异常→unavailable、半摄取→not_ready)。
- 结构化 i/o 提取失败(异常消息形态)→ 该 span i/o 退回 text kind 的 `str(value)`(不整体 500)。
- raw 端点:任何失败 → 404。
- 前端:`RunTraceIo` 判别失败 / 空 → 不渲染该 io 段(现有「空段不渲染」语义保留)。

## 测试

- **facade**(pytest,`ruff check` + `ruff format --check` 两步都跑):
  - `_render_io` messages 结构(role 取 `type`、toolCalls、block-list content、按消息截断 fullChars/truncated 边界 MSG_CAP)。
  - text kind 判别 + TEXT_CAP 边界。
  - error 字段提取(level enum→小写、status_message)。
  - raw 端点:ownership 404、找 span 返全文、缺 span 404、client None 404。
- **前端**(vitest):
  - `cleanUntrusted`:去围栏 + 剥 ▁ + hadUntrusted 标志 + 无标记时 passthrough。
  - `MessageBlock`:system 默认收起、其余展开、toolCalls 空 content、含不可信 badge、truncated 截断行 + 查看原文触发 fetch。
  - `TraceView`:kind 自适应标签(tool→参数/结果)、error span 红标 + 详情 status_message。
  - `RunStatusBanner`:ok/err 两态、onJump 触发。
  - `TraceView`/timeline 现有测试不回归。
- i18n 三处齐(en interface + en value + zh-CN value),编译器强制。

## 风险

- **R1 · 错误 level 可能未发**:spike 样本无失败 run,未证实 orchestrator 在工具/LLM 失败时把 observation `level` 置 ERROR。trace 视图的红标 + 详情 error **依赖它**。**plan 首任务验证**:探一个失败 run 或读 orchestrator span 关闭代码。若未发 ERROR:(a) 小改 orchestrator 失败时置 `level`+`status_message`,或 (b) trace error 只显已发的 level、以时间线(SSE)为权威错误源。**时间线 banner 用 SSE error 不受影响**。
- **R2 · raw 端点工具受限**:工具写时 cap 8192,raw 拿不到更多。已接受(工具通常极短)。文档注明。

## 范围外(YAGNI)

- 不合并两视图。
- 不提工具写时 cap(不涨 Langfuse 存储)。
- 不改 SSE run_event schema。
- raw 层不做 per-message 粒度(field 级即可)。
- 不做 system prompt 去重(默认折叠已解决视觉膨胀)。

## 全局约束

- **后端**:control-plane(python uv workspace);best-effort 降级永不 500;ownership 门 404 隐藏跨租户;提交前 `uv run ruff check` **且** `uv run ruff format --check`(+ pre-commit ruff-format);mypy 按 CI 范围(含 tests)。
- **前端**:React+Vite+AntD5+react-i18next;`pnpm typecheck`(tsc -b)+ `npx vitest run` 必过;i18n 三处齐;语义色走 `--ew-*` 令牌双主题(light + dark,含 `data-theme` 覆盖)。
- **通用**:不加新依赖;immutability(不原地改);小文件高内聚;每改动行可溯源到需求。
