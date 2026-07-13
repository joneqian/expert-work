# 调试台 trace 详情增强(LLM prompt 可读 / 工具 i/o / LLM 输出)设计文档

**日期**: 2026-07-13
**分支**: `feat/playground-trace-detail-enrich`
**状态**: 定稿,待实施

## 背景与问题

调试台 trace 精确视图(Batch 4b)的节点详情面板有两个可读性/完整性缺陷(用户实测):

1. **LLM 调用 input(prompt)不好读 + 被截断**:详情面板显示的是消息 list 的 Python-repr 串(`[{'content': 'You are...\n\n# Tool-use...'}]`,字面 `\n` 两字符不渲染),且 io_cap=8192 截断丢内容。
2. **工具调用详情面板空的**:只有 header(工具名 + 耗时),无 input(args)/output(result)。

## 根因(已勘查实证,dev 库真实 trace `675c76fd...`)

- **LLM input**:Langfuse GENERATION `llm_call` 的 `input` 是消息 `list`(`[{content, role...}]`)。facade `_cap` 做 `str(value)` → Python-repr(转义 `\n`),不可读;io_cap=8192 截断。
- **LLM output**:GENERATION 的 `output` = **None**(答案没捕获)。中间件 `langfuse.py:204` 调 `span.record_output(response["output"])`,但 `response["output"]` 空/缺(待 spike 确认)。
- **工具 i/o**:`expert_work.orchestrator.tool_call` SPAN 的 `input`/`output` **都 None**;OTel attributes 只有 `tenant`/`service`/`env`/`tool=exec_python`(工具名)。**工具 args/result 根本没上报 Langfuse**。数据在 SSE 时间线视图里有(Batch 1-3 从 updates 帧),trace 视图(Langfuse)没有。

**承重架构事实**:
- OTel spans(`expert_work_span`,含 tool_call/llm_call SPAN)经 **OTLP exporter**(`BatchSpanProcessor(OTLPSpanExporter)`,`tracing.py:121`)进 Langfuse。**PII mask 不覆盖这条路**(mask 是 Langfuse SDK 回调,不在 OTLP 导出链上)。
- LLM GENERATION 走 **langfuse_sdk 中间件**(`langfuse_sdk.py`,`start_generation`/`record_output`,`_build_pii_mask()` 自动 mask)。
- 故给 tool span 补 args/result 属性**不会自动 mask**,而工具 i/o 含 untrusted 内容/可能 PII → **必须手动过 mask**。

## 决策(brainstorm 定)

- Issue 2 工具详情 = **方案 A 后端埋点补发**(非「trace 视图指向时间线」):orchestrator 给 tool span 补发 masked args/result 到 Langfuse。只对**新 run** 生效(历史 run 无数据,trace 视图本就降级)。
- Issue 1 截断 = **大幅放宽 io_cap**(8192 → 32768)。
- 顺带补 **LLM output**(答案)到 GENERATION,让 trace 详情显示答案。

## 目标 / 非目标

**目标**:
- LLM input 详情渲染成可读文本(消息 role+内容,真换行),不再 repr 串;放宽截断。
- 工具节点详情显示 args(input)+ result(output),**PII-masked**、体积受限。
- LLM 节点详情显示答案(output)。

**非目标**:
- 历史 run 补数据(只影响新 run;历史 trace 视图沿用现降级)。
- 改时间线视图(SSE 路径,已有工具 i/o)。
- 改 Langfuse 摄取基础设施 / 关 PII mask。

## 架构:两侧改动

### Part 1 — 读侧(facade + 前端),Issue 1,惠及现有+新 run

`services/control-plane/src/control_plane/api/trace_facade.py`:
- **可读化**:`_cap`(或新 helper)检测结构化 input/output —— 若是消息 list(`[{role?, content}]`)→ 提取成可读文本:每条消息 `«role»\n{content}` 用真换行拼接;`content` 是 block-list(`[{type,text}]`)则取 text 拼接。非消息结构 → `json.dumps(ensure_ascii=False, indent=2)`(真换行、非 ASCII 不转义)。字符串 → 原样。
- **放宽截断**:`io_cap` 默认 8192 → **32768**(`normalize_trace`/`fetch_and_normalize` 默认参数;端点调用处若显式传 8192 一并改)。

前端 `TraceView.tsx` `IoSection` 已 `<pre> pre-wrap` + `maxHeight:180 overflow:auto` —— 内容有真换行即渲染,无需改(除非需放大 maxHeight,视效果定)。

### Part 2 — 写侧(后端埋点),Issue 2 + LLM output,仅新 run

**工具 i/o**(`services/orchestrator/src/orchestrator/graph_builder/builder.py:2002` 的 `expert_work_span(ORCHESTRATOR, "tool_call", ...)`):
- `expert_work_span` yield OTel `span` → 块内 `_invoke_tool` 后,在 span 上设 masked+capped 的 input(args)/output(result)。
- **属性 key 待 spike 确认**(§ 验证 spike):Langfuse OTel 摄取认 `langfuse.observation.input`/`output` 还是 `input.value`/`output.value` 或 GenAI 语义约定。
- **mask**:复用 `_build_pii_mask()`(`langfuse_sdk.py:40`)对 args/result 手动 mask 后再设属性(OTLP 路径不自动 mask)。mask 返回 JSON-serialisable → 设属性前 `json.dumps(ensure_ascii=False)` 或按属性类型序列化。
- **体积上限**:result(如 exec_python stdout / 文件内容)可能很大 → 设属性前 cap(复用读侧 cap 常量或独立后端 cap,如 8192)。避免撑大 Langfuse payload。
- args 从 `args`(dispatch 处已有,`builder.py:1997`);result 从 `outcome[0].content`(ToolMessage content)。

**LLM output**(中间件生成路径):
- 查 `response["output"]` 为何空(`langfuse.py:~180-210` 的 record_output 调用点)→ 让 GENERATION `record_output` 收到实际答案文本(SDK 路径自动 mask)。

### 验证 spike(实施计划 Task 1,阻塞后续写侧)

在 dev 环境(容器内 SDK,`docker exec expert-work-control-plane-blue /app/.venv/bin/python`)实证:
1. **属性 key**:发一个 OTel span 带候选属性(先试 `langfuse.observation.input`/`output`),经 OTLP→Langfuse,`~1s` 后 `lf.api.trace.get` 拉回,断言 observation 的 `input`/`output` 被填。填不上则试 `input.value`/`output.value`。**产出 = 确认的 key**,写侧任务用它。
2. **mask 缺口确认**:确认 OTLP 路径设的属性在 Langfuse 里未 masked(佐证需手动 mask)。
3. **LLM output 空因**:定位 `response["output"]` 空的原因(record_output 未达 vs 传了空)。

## 数据流

```
新 run 执行
  ├─ tool_call span: _invoke_tool → args/result → mask → cap → 设 OTel observation input/output 属性 → OTLP → Langfuse
  └─ llm_call generation: record_output(答案) → SDK(mask)→ Langfuse
        │
   (已存在) OTLP/SDK → Langfuse ClickHouse
        │
   trace facade 读: normalize_trace 可读化 input/output(消息→文本)+ io_cap 32768
        │
   TraceView 详情面板: IoSection <pre> 渲染真换行
```

## 错误处理与边界

- 埋点补发失败/异常**不得影响 run 执行**:设属性/mask 包在 try 或 best-effort(埋点是旁路,失败只丢观测数据,不阻塞工具执行)。
- 工具 result 为二进制/非文本 → cap 的 `str()` 兜底;mask 只处理字符串叶子。
- 历史 run(无 tool i/o 数据)→ facade `input/output` 仍 None → IoSection 不渲染(现行为,不回归)。

## 测试

- 后端 facade:`normalize_trace` 可读化 —— 消息 list → role+真换行文本;block-list content 取 text;非消息 dict → indent JSON;io_cap 32768 生效;超长仍截断。
- 后端埋点:tool_call span 设了 masked input/output(单测用假 span 断言 set_attribute 调用 + mask 生效 + cap);LLM output record_output 收到答案。
- spike 产出的属性 key 有集成/契约测锚定(防 Langfuse 升级漂移)。
- 前端:IoSection 渲染多行内容(若改 maxHeight 则测)。

## 全局约束

- 后端 `uv run pytest`/`uv run mypy`/`uv run ruff check` 全绿(repo-root 配置;提交前跑 ruff)。
- 前端 `pnpm typecheck` 0 + `npx vitest run` 全绿;i18n 三处 parity(若加文案)。
- **埋点旁路失败不阻塞 run**(硬约束)。
- **工具 i/o 必须 PII-masked**(复用 `_build_pii_mask()`;OTLP 路径不自动 mask 是已证事实)。
- 体积受限:tool i/o 属性设上限,避免撑大 Langfuse payload。
- 只影响新 run;历史 run 沿用现降级,不回归。
- surgical:埋点补发只碰 tool span 块 + 生成 output 记录点;读侧只碰 facade 归一 + cap。

## 文件清单

- 改 `services/control-plane/src/control_plane/api/trace_facade.py` — 可读化 input/output + io_cap 32768。
- 改 `services/control-plane/tests/test_trace_facade_normalize.py` — 可读化 + cap 测。
- 改 `services/orchestrator/src/orchestrator/graph_builder/builder.py` — tool_call span 补 masked/capped input/output。
- 改 orchestrator 埋点测试 — tool i/o 断言。
- 改 `packages/expert-work-runtime/src/expert_work/runtime/middleware/langfuse.py`(或 llm 调用点)— 补 LLM output 记录。
- 可能改 `TraceView.tsx` — IoSection maxHeight(视效果,可选)。
- spike 无产物代码(dev 验证),结论写进 plan/ledger。
