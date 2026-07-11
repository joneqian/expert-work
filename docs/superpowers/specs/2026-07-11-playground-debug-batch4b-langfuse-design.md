# 调试台 Batch 4b(Langfuse trace 精确视图)子 spec

> **父 spec:** `docs/superpowers/specs/2026-07-10-playground-debug-console-redesign-design.md`(伞形 §Batch4 item 13/14/15)。
> **线框(权威转写源):** `docs/superpowers/specs/2026-07-11-batch4b-wireframe.html`(v4,经评审定稿)。
> **勘查依据:** 2026-07-10/11 Langfuse 实证(见记忆 `playground-debug-console-batches`)。

## 1. 目标与范围

**目标:** 调试台事件区加第三档「精确」视图 —— 把一次运行的 Langfuse trace 归一成**人话操作树 + 时间轴瀑布**,点任意节点看该步的 prompt/输出/元信息。另加每轮「在 Langfuse 中打开」直达外链。

**范围(经 brainstorm 定):** 合一份 4b,做伞形 **item 13(facade)+ 14(span 树视图)+ 15(直达外链)**。
- **item 11(trace_id 进 metadata)舍弃** —— 实证 `agent_run.trace_id` == Langfuse 摄取的 trace_id(后台 asyncio.Task 继承控制面请求 OTel context,W3C traceparent 传播),且已由 `getRun` 的 `RunDetail.trace_id` 暴露给前端。facade + 外链直接用,无需改 SSE metadata。

**关键实证前提(定预期):**
- **trace_id 对得上**:facade 按 `agent_run.trace_id` 查 Langfuse 即命中。
- **cost/model/tokens best-effort**:中间件(`langfuse_sdk.py`)设计上传 model/usage/output,但采样 run 里这些字段**多为空**。§9 禁改 orchestrator 埋点 → **不去修中间件让它们落库**。item 14 **可靠交付 = span 树结构 + 每 span 精确 latency + input/output**;model/cost **有才显、空则隐**。(model/token 调试台别处已从 SSE 帧有,不缺。)

## 2. 后端 facade(item 13)—— control-plane

**只碰 control-plane**(新端点 + 归一器 + DTO + 测试),不碰 orchestrator、不改埋点。

### 2.1 端点
`GET /v1/sessions/{thread_id}/runs/{run_id}/trace` —— 照 `services/control-plane/src/control_plane/api/runs.py:1054-1150` 的 `get_run` 模板。内部:
1. 归属门控(见 §2.2)。
2. 读 `agent_run.trace_id`(`runs.py:1119` 现成读法)。
3. 复用 app state 已构造的 Langfuse client(`app.py:1310-1319`)`.api.trace.get(trace_id)`。
4. 归一(§2.3)→ DTO(§2.4)返回。

### 2.2 鉴权 —— facade 归属门控(非系统管理员)
- **facade 只返回「这一个 run」的 span**,按归属校验(`caller_owns_thread` + `request.state.tenant_id`,同 `get_run`,404 藏跨租户)。返回的 span 只属调用者自己的 run → **不跨租户泄露 → 只需归属门控,不要求系统管理员。**
- **系统管理员门控是 item 15(直达外链)的事** —— 点开跳 Langfuse UI = 能看整个实例所有租户,那个必须系统管理员。

### 2.3 归一(facade 核心逻辑)
把原始 Langfuse observation 树归一成人话操作树:
- **合并冗余双行**:一个 `expert_work.orchestrator.llm_call` SPAN 若含单个 GENERATION 子(同一次 LLM 调用)→ **合成一个 `kind:"llm"` 节点**。latency 取外层 span;model/cost/input/output 取 generation。
- **技术名 → 人话 + kind 映射**(已知名表):
  - GENERATION / `*.llm_call` → `kind:"llm"`, `label:"LLM 调用"`。
  - `*.tool_call` → `kind:"tool"`, `label:"工具调用"`, `detail:` 工具名(从 span 属性/名取)。
  - `expert_work.session.run` → `kind:"session"`, `label:"会话运行"` → 作树根(见下)。
  - `expert_work.control_plane.http_request`(trace 根)→ **省略**,其子 `session.run` 上提为树根(用户不关心 HTTP 包裹层)。
  - **未知/未映射 span** → `kind:"span"`, `label:` 去 `expert_work.` 前缀的清名(**退化不崩不藏**,保结构)。
- **拍平**:合并/省略后重算 parentId,保持树连通。

> 用途标签(主推理/记忆抽取…)**不在 facade 做** —— facade 返回泛化 `label:"LLM 调用"`;用途由前端交叉引用 SSE 节点补(§3.2),因为 SSE 节点序列已在前端解析(Batch 3 timeline),无需 facade 再拉 event_store。

### 2.4 DTO(前端只认这个)
判别式响应:
```ts
interface RunTraceResponse {
  status: "ok" | "not_ready" | "unavailable" | "no_trace";
  trace?: { name: string; latencyMs: number; totalCostUsd: number | null; spanCount: number };
  spans?: TraceSpan[];   // 扁平、前序有序;前端按 parentId 建树
}
interface TraceSpan {
  id: string;
  parentId: string | null;
  kind: "session" | "llm" | "tool" | "span";
  label: string;              // 人话:会话运行 / LLM 调用 / 工具调用 / <清名>
  detail: string | null;      // 次要:工具名等
  startMs: number;            // 相对 trace 起点偏移(瀑布定位)
  latencyMs: number;          // 精确耗时(瀑布长度 + 行显)
  model: string | null;       // best-effort
  inputTokens: number | null; outputTokens: number | null;  // best-effort
  costUsd: number | null;     // best-effort
  input: string | null;       // prompt(详情面板),facade 截断上限(如 8KB)+ 截断标记
  output: string | null;      // response,同截断
}
```
- input/output **随初始 DTO 带上(截断上限兜底)**,详情面板即点即显,不另开端点。超上限 → 截断 + `…（已截断）` 标记。

### 2.5 降级(判别 status,永不 500)
- trace 未入库(~1s 延迟 / run 刚完)→ `status:"not_ready"`(前端「处理中,点刷新」)。
- Langfuse 不可达 / SDK 抛错 → 捕获 → `status:"unavailable"`(软提示,不 500)。
- `agent_run.trace_id` 为 null → `status:"no_trace"`。

## 3. 前端(item 14 span 树视图 + item 15 直达)

### 3.1 落点 + 取数
- 事件区现有视图切换器(Batch 3 `playground-event-view-toggle` Segmented:时间线/原始)**加第三档「精确」**。选中渲 span 树。
- 按需 REST 自取 `getRunTrace(threadId, runId)`(新 `api/trace_facade.ts`),循 PlanPanel「走 REST 自取」—— 打开「精确」才拉。

### 3.2 用途标签(前端交叉引用 SSE)
- span 树视图拿到 facade 的 spans(`kind:"llm"`,泛化 label)+ 本轮 SSE 事件(已由 Batch 3 `parseTimeline` 解析出按序的节点/AgentStep)。
- 从 SSE 取**有序的 LLM 触发节点序列**(每条 AI message 的 `node`)→ 映射用途(`agent`→主推理、memory 节点→记忆抽取、`reflect`→反思、`planner`→规划…)→ **按序 zip** facade 的 `kind:"llm"` spans。
- **计数匹配**才贴用途 `detail`;**不匹配 → 泛化「LLM 调用」兜底**(不猜、不错标)。
- **⚠️ 计划期须先验证的风险**:此关联成立的前提是「每次 LLM 调用在 SSE updates 帧里都有对应节点/AI message」。但**记忆抽取/反思这类内部子调用可能不作为 AI message 进 SSE `messages` 通道**(只在 Langfuse 有 generation)。若如此 → SSE 的 LLM 节点数 < Langfuse generation 数 → 计数不匹配 → 全部退化泛化「LLM 调用」(安全但用途标签失效)。**Plan Task 0 = 跑一条含记忆抽取的 run,核对 SSE updates 帧是否暴露 memory 节点的 LLM 调用**:暴露 → §3.2 关联可行;不暴露 → 用途标签降级为「仅 agent 主调用能标,其余泛化」并在 spec/plan 注明,或改用 Langfuse generation 的 `input` 特征(如"memory extraction module"提示词)启发式判用途(次选,较脆)。**不为拿用途去改埋点(§9)。**

### 3.3 渲染(转写线框 v4)
- **左树 + 右瀑布两窗,行对齐**(线框 `.wf`):左 = 树连接线(├─└─)+ 类型点(session 灰 / llm 蓝 / tool 紫)+ label(+ detail 灰后缀)(+ model/cost miniChip 有才显);右 = 时间轴瀑布(每 span 一条 bar,`left=startMs/traceLatency`、`width=latencyMs/traceLatency`,类型色;顶部刻度轴 + 竖网格)。
- **耗时复用 4a `fmtDuration`**。
- **点行 → 下方详情面板**(线框 `.detail`):元信息(kind/latency/起止/model/tokens/cost 有才显)+ **输入(prompt)** + **输出(response)**(各可折叠;来自 span.input/output;空则不显该段)。× 关闭。
- **状态**:`ok`→渲;`not_ready`→「处理中」+刷新钮;`unavailable`→软提示;`no_trace`→「无 trace」。
- 样式令牌 `var(--ew-*)`;`ReactNode` 具名 import,不注解 JSX.Element;禁 `any`。

### 3.4 item 15 直达外链
- 复用 `buildLangfuseTraceUrl(traceId)`(`config/env.ts`)+ `isSystemAdmin` 门控(照 `TraceToolbar`)。调试台每轮显「在 Langfuse 中打开」,`trace_id` 从 `getRun` 的 `RunDetail.trace_id`。
- env 未配 → `buildLangfuseTraceUrl` 返 null → 隐藏链接(现有降级)。

### 3.5 i18n
新键:标签「精确」、各状态文案、「在 Langfuse 中打开」、详情面板段标题(输入/输出)、用途标签(主推理/记忆抽取/反思/规划)等 —— 三处 parity。

## 4. 测试计划

**后端(pytest,`uv run`):**
- facade 命中:mock Langfuse client 返回一棵含 `session.run` + `orchestrator.llm_call`(套 GENERATION)+ `tool_call` 的 observation 树 → 断言归一后 spans:双行合一、人话 label、http_request 省略、未知名退化清名、parentId 连通、input/output 带上且超限截断。
- 降级:`api.trace.get` 抛 → `unavailable`;404/空 → `not_ready`;trace_id null → `no_trace`。均非 500。
- 鉴权:非归属调用者 → 404(藏);归属调用者 → 200。**不要求系统管理员**。

**前端(vitest):**
- `getRunTrace` SDK 解析判别响应。
- span 树:建树(parentId)、瀑布定位(startMs/latency 百分比)、fmtDuration、model/cost 空隐藏、状态四态渲染。
- 用途标签:SSE 节点序列 zip llm spans(计数匹配贴用途 / 不匹配泛化兜底)。
- 详情面板:点行显 input/output/meta;空段不渲;× 关闭。
- 直达外链:isSystemAdmin 门控 + env 未配隐藏。

**手动冒烟:** 跑一条含工具 + 记忆抽取的 run → 「精确」标签显归一操作树 + 瀑布;记忆抽取标对用途;点它详情面板显其 prompt/输出;「在 Langfuse 中打开」(系统管理员)跳对应 trace。

## 5. 不在范围(YAGNI)
- 改 orchestrator 埋点让 cost/model 落库(§9;cost/model best-effort)。
- item 11 trace_id 进 metadata(trace_id 已由 getRun 有)。
- 跨 run 对比 / 聚合看板(父 §9)。
- 非 Langfuse OTLP 后端(facade 只实现 Langfuse 查询)。
- span io 的 lazy-load 二次端点(初始 DTO 截断带上足够;超大再议)。

## 6. 文件触点
**后端(Batch 4b):**
- `services/control-plane/src/control_plane/api/runs.py`(新 trace 端点 或 新 `api/trace_facade.py` 模块)+ 归一器 + DTO + pytest。

**前端(Batch 4b):**
- `apps/admin-ui/src/api/trace_facade.ts`(`getRunTrace` + DTO 类型)+ 测试。
- `apps/admin-ui/src/pages/agent_detail/playground/TraceView.tsx`(span 树 + 瀑布 + 详情面板)+ 测试。
- `apps/admin-ui/src/pages/agent_detail/PlaygroundTab.tsx`(第三档「精确」接线 + 直达外链)。
- 用途标签:复用 `api/timeline.ts` 的节点序列(Batch 3)。
- `apps/admin-ui/src/config/env.ts`(复用 `buildLangfuseTraceUrl`,无需新增)。
- `apps/admin-ui/src/i18n/locales/{en,zh-CN}.ts`(新文案)。

## 7. Batch 1-4a 教训延续
- 前端 `pnpm typecheck`(tsc -b)非裸 npx tsc;`ReactNode` 具名;`npx vitest run <path>`。
- 后端 uv workspace:`uv run pytest/mypy` + root config;**提交前显式 `uv run ruff check`**(4a 踩过 RUF015:mypy 过 ≠ ruff 过)。
- e2e testid 与 src 一起改(本批加第三档 Segmented + 新视图,grep 核 e2e)。
- 编辑器 stale 诊断以亲跑为准。
