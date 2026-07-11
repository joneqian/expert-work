# 调试台 Batch 4b(Langfuse trace 精确视图)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 调试台事件区加第三档「精确」—— 把一次运行的 Langfuse trace 归一成人话操作树 + 时间轴瀑布,点节点看 prompt/输出/元信息;另加每轮「在 Langfuse 中打开」直达外链。

**Architecture:** control-plane 加只读 facade 端点(归属门控 + 复用已配 Langfuse 凭证 `api.trace.get` + 归一成稳定 DTO + 四态降级);前端第三档「精确」按需 REST 取 DTO,渲染左树+右瀑布+点击详情面板,用途标签(A′)在前端交叉引用 SSE agent 节点。

**Tech Stack:** 后端 Python(control-plane FastAPI,uv,pytest);前端 React + Vite + AntD 5 + react-i18next(vitest)。

**设计来源(权威):** spec `docs/superpowers/specs/2026-07-11-playground-debug-batch4b-langfuse-design.md` + 线框 `docs/superpowers/specs/2026-07-11-batch4b-wireframe.html`(v4)。

## Global Constraints

- **只碰 control-plane 后端 + admin-ui 前端;不碰 orchestrator、不改埋点**(父 spec §9)。facade 只**读** Langfuse。
- **鉴权分层**:facade = 归属门控(同 `get_run`:tenant + `caller_owns_thread`,404 藏跨租户;**不要求系统管理员**,因只返回调用者自己 run 的 span);直达外链(item 15)= `isSystemAdmin` 门控(跳原始 Langfuse UI = 跨租户)。
- **cost/model/tokens best-effort**:facade DTO 带上(可 null);前端有才显。**不为它改埋点。**
- **trace_id 来源**:`agent_run.trace_id`(已验 == Langfuse trace_id),经 `runs.get(...).trace_id` 读;前端外链 trace_id 从 `getRun` 的 `RunDetail.trace_id`。
- **降级永不 500**:facade 返判别 `status: ok|not_ready|unavailable|no_trace`。
- 后端验证:`uv run pytest <path>` + `uv run mypy`(root config)+ **提交前 `uv run ruff check`**(4a 踩过 RUF015)。
- 前端验证:`cd apps/admin-ui && pnpm typecheck`(=tsc -b)+ `npx vitest run <path>`;禁 `any`;`ReactNode` 具名 import,不注解 JSX.Element;样式 `var(--ew-*)`;i18n 三处 parity。
- 每 Task 末 commit,conventional commits。

**Task 0(已在 brainstorm 阶段做,勿重做):** 已实证 SSE updates 暴露 memory 节点但其 LLM 调用不作为 AI message 进帧 → 用途标签走 A′(见 spec §3.2、下 Task 5)。

---

## Task 1: 后端 —— trace 归一器(纯函数)

**Files:**
- Create: `services/control-plane/src/control_plane/api/trace_facade.py`(归一器 + DTO dataclass)
- Test: `services/control-plane/tests/test_trace_facade_normalize.py`

**Interfaces:**
- Consumes:一棵 Langfuse `TraceWithFullDetails`(属性:`.name:str`、`.latency:float`(秒)、`.total_cost:float|None`、`.observations:list[ObservationsView]`;每 obs:`.id`、`.type`("SPAN"/"GENERATION"/…)、`.name`、`.parent_observation_id:str|None`、`.latency:float`(秒)、`.start_time:datetime`、`.model:str|None`、`.prompt_tokens`/`.completion_tokens`(或 `promptTokens`/`completionTokens`)、`.calculated_total_cost:float|None`、`.input`、`.output`)。
- Produces:
  ```python
  @dataclass(frozen=True)
  class TraceSpan:
      id: str; parent_id: str | None; kind: str  # "session"|"llm"|"tool"|"span"
      label: str; detail: str | None
      start_ms: int; latency_ms: int
      model: str | None; input_tokens: int | None; output_tokens: int | None; cost_usd: float | None
      input: str | None; output: str | None

  def normalize_trace(trace: object, *, io_cap: int = 8192) -> dict:
      """→ {"status":"ok","trace":{name,latencyMs,totalCostUsd,spanCount},"spans":[TraceSpan-as-dict...]}"""
  ```

- [ ] **Step 1: 写失败测试**

`test_trace_facade_normalize.py` —— 用轻量 stub obs 对象(`SimpleNamespace`)搭一棵树:root `expert_work.control_plane.http_request`(SPAN)→ `expert_work.session.run`(SPAN)→ [`expert_work.orchestrator.llm_call`(SPAN)→ `llm_call`(GENERATION,带 model/input/output)、`expert_work.orchestrator.tool_call`(SPAN)]。断言归一后:

```python
from types import SimpleNamespace
from datetime import datetime, timezone
from control_plane.api.trace_facade import normalize_trace

def _obs(id, type_, name, parent, lat, start_s, **kw):
    base = dict(id=id, type=type_, name=name, parent_observation_id=parent, latency=lat,
                start_time=datetime(2026,1,1,0,0,start_s,tzinfo=timezone.utc),
                model=None, prompt_tokens=0, completion_tokens=0, calculated_total_cost=0.0,
                input=None, output=None)
    base.update(kw)
    return SimpleNamespace(**base)

def _trace(obs):
    return SimpleNamespace(name="expert_work.control_plane.http_request", latency=33.5, total_cost=0.0, observations=obs)

def test_normalize_merges_wrapper_and_generation_and_humanizes():
    obs = [
        _obs("root","SPAN","expert_work.control_plane.http_request",None,33.8,0),
        _obs("sess","SPAN","expert_work.session.run","root",33.5,0),
        _obs("llmspan","SPAN","expert_work.orchestrator.llm_call","sess",8.2,1),
        _obs("gen","GENERATION","llm_call","llmspan",8.8,1, model="glm-4.6",
             input="You are a memory extraction module...", output='{"memories":[]}', calculated_total_cost=0.0021),
        _obs("toolspan","SPAN","expert_work.orchestrator.tool_call","sess",0.16,28),
    ]
    out = normalize_trace(_trace(obs))
    assert out["status"] == "ok"
    spans = out["spans"]
    kinds = {s["label"] for s in spans}
    # http_request root elided; session.run is root label 会话运行
    assert "会话运行" in kinds
    assert not any("http_request" in s["label"] for s in spans)
    # wrapper llm_call SPAN + its GENERATION merged into ONE llm node carrying model/io
    llm = [s for s in spans if s["kind"] == "llm"]
    assert len(llm) == 1
    assert llm[0]["label"] == "LLM 调用"
    assert llm[0]["model"] == "glm-4.6"
    assert llm[0]["input"].startswith("You are a memory")
    assert llm[0]["latencyMs"] > 0
    # tool humanized
    tool = [s for s in spans if s["kind"] == "tool"]
    assert tool and tool[0]["label"] == "工具调用"
    # every span's parentId resolves within the set or is None (tree connected)
    ids = {s["id"] for s in spans}
    assert all(s["parentId"] is None or s["parentId"] in ids for s in spans)

def test_normalize_unmapped_span_falls_back_to_cleaned_name():
    obs = [_obs("sess","SPAN","expert_work.session.run",None,1.0,0),
           _obs("x","SPAN","expert_work.orchestrator.planner","sess",0.5,0)]
    spans = normalize_trace(_trace(obs))["spans"]
    planner = [s for s in spans if s["id"]=="x"][0]
    assert planner["kind"] == "span"
    assert planner["label"] == "orchestrator.planner"   # expert_work. 前缀去掉

def test_normalize_caps_oversized_io():
    big = "x"*20000
    obs = [_obs("sess","SPAN","expert_work.session.run",None,1.0,0),
           _obs("g","GENERATION","llm_call","sess",1.0,0, input=big)]
    spans = normalize_trace(_trace(obs), io_cap=100)["spans"]
    g = [s for s in spans if s["id"]=="g"][0]
    assert len(g["input"]) <= 130 and "截断" in g["input"]
```

- [ ] **Step 2: FAIL** —— `cd services/control-plane && uv run pytest tests/test_trace_facade_normalize.py -v` → 模块不存在。

- [ ] **Step 3: 写实现** —— `trace_facade.py` 的 `normalize_trace`,规则(spec §2.3):
  1. `trace_start = min(o.start_time for o in observations)`;每 obs `start_ms = round((o.start_time - trace_start).total_seconds()*1000)`、`latency_ms = round(o.latency*1000)`。
  2. 分类 `kind`/`label`(按 `type` 与 `name`):GENERATION→llm/「LLM 调用」;name 含 `.tool_call`→tool/「工具调用」+ detail(工具名,若 name/attributes 有);name 含 `.session.run`→session/「会话运行」;name 含 `.http_request`→标记待省略;其余→span/label=去 `expert_work.` 前缀清名。
  3. **合并** `*.orchestrator.llm_call` SPAN(恰含一个 GENERATION 子)→ 省略该 SPAN,GEN 顶上(GEN.parent 改成 SPAN.parent);GEN 用 llm/「LLM 调用」,model/io/cost 取 GEN,latency 取外层 SPAN(更全)。
  4. **省略** `http_request` 根 → 其子 re-parent 到 None(成根)。
  5. 省略/合并后重算 parent(id→存活祖先 map),保连通。
  6. input/output `str(...)` 后截断到 `io_cap`,超则 `[:io_cap] + "…(已截断)"`;None 保持 None。
  7. tokens:`int(o.prompt_tokens)` 若 >0 else None(best-effort,0/缺 → None);model:`o.model or None`;cost:`o.calculated_total_cost` 若 >0 else None。
  8. 输出 dict:`{"status":"ok","trace":{"name":..., "latencyMs":round(trace.latency*1000), "totalCostUsd": trace.total_cost or None, "spanCount": len(spans)}, "spans":[asdict-with-camel...]}`(DTO 键用 camelCase:parentId/startMs/latencyMs/inputTokens/outputTokens/costUsd)。

  > 实现者:Langfuse obs 属性名以真实 SDK 为准(spike 见 `promptTokens`/`completionTokens` 驼峰 与 `calculated_total_cost` 蛇形混用)—— 用 `getattr(o, "prompt_tokens", None) or getattr(o, "promptTokens", None)` 双取兜底。

- [ ] **Step 4: PASS** —— `cd services/control-plane && uv run pytest tests/test_trace_facade_normalize.py -v` → 3 用例 PASS。

- [ ] **Step 5: 类型 + lint** —— `uv run mypy services/control-plane/src/control_plane/api/trace_facade.py` clean;`uv run ruff check services/control-plane/src/control_plane/api/trace_facade.py services/control-plane/tests/test_trace_facade_normalize.py` clean。

- [ ] **Step 6: 提交** `git commit -m "feat(playground): Langfuse trace 归一器 normalize_trace"`。

---

## Task 2: 后端 —— facade 端点 + 只读 client 接线

**Files:**
- Modify: `services/control-plane/src/control_plane/api/runs.py`(在 `build_runs_router()` 加 `GET /{thread_id}/runs/{run_id}/trace`)
- Modify: `services/control-plane/src/control_plane/app.py`(lifespan 暴露只读 `Langfuse` client 到 `app.state.langfuse_read_client`)
- Modify: `services/control-plane/src/control_plane/api/trace_facade.py`(加 `fetch_and_normalize(client, trace_id) -> dict` 封装 get + 降级)
- Test: `services/control-plane/tests/test_trace_facade_endpoint.py`

**Interfaces:**
- Consumes:Task 1 `normalize_trace`;`agent_run.trace_id`(`runs.get`);`get_run` 的鉴权模式(runs.py:1073-1120)。
- Produces:`GET /v1/sessions/{thread_id}/runs/{run_id}/trace` → `RunTraceResponse`(Task 1 DTO,含 status 降级)。

- [ ] **Step 1: app.py 暴露只读 client** —— lifespan 里(`make_langfuse_client` 附近 :1310),当三 env 齐时另建一个**原始 `Langfuse` SDK** 实例(它有 `.api.trace.get`,middleware adapter 没有)存 `app.state.langfuse_read_client = Langfuse(public_key=..., secret_key=..., host=...)`;缺 env → `app.state.langfuse_read_client = None`。teardown 一并 shutdown。

- [ ] **Step 2: trace_facade.fetch_and_normalize** ——
  ```python
  def fetch_and_normalize(client, trace_id, *, io_cap=8192) -> dict:
      if client is None: return {"status": "unavailable"}
      try:
          trace = client.api.trace.get(trace_id)
      except NotFoundError:            # langfuse.api ...errors NotFoundError（未入库/不存在）
          return {"status": "not_ready"}
      except Exception:
          return {"status": "unavailable"}
      return normalize_trace(trace, io_cap=io_cap)
  ```
  (确切 NotFoundError 导入以 langfuse SDK 为准;分不清就 `except Exception → unavailable`,并把「有 trace 但归一空」也视 ok。)

- [ ] **Step 3: 写失败测试** —— `test_trace_facade_endpoint.py`,用 FastAPI TestClient + 依赖覆盖(照 `get_run` 现有端点测试的 fixture 搭 tenant/owner/run store),mock `app.state.langfuse_read_client`:
  - 归属 owner + client 返回 trace → 200 `status:"ok"` + spans。
  - 非 owner(caller_owns_thread False)→ 404。
  - trace_id 为 None(run 无 trace)→ `status:"no_trace"`(端点在读到 trace_id None 时直接返,不调 client)。
  - client None → `status:"unavailable"`;client.get 抛 NotFound → `not_ready`;抛其它 → `unavailable`。
  - **不要求系统管理员**:普通 owner 即 200。

- [ ] **Step 4: 写端点** —— 在 `build_runs_router()` 加:
  ```python
  @router.get("/{thread_id}/runs/{run_id}/trace", response_model=None)
  async def get_run_trace(thread_id, run_id, request, threads=..., users=..., runs=...) -> JSONResponse:
      # 1) 归属门控:照 get_run 的 tenant + threads.get + caller_owns_thread → 404 藏
      # 2) trace_id = (await runs.get(run_id, tenant_id)).trace_id;None → return JSONResponse({"status":"no_trace"})
      # 3) client = getattr(request.app.state, "langfuse_read_client", None)
      # 4) return JSONResponse(fetch_and_normalize(client, trace_id))
  ```
  DI 只需 threads/users/runs（不需 approvals/token_usage）。

- [ ] **Step 5: PASS + 类型 + lint** —— `cd services/control-plane && uv run pytest tests/test_trace_facade_endpoint.py -v` PASS;`uv run mypy services/control-plane/src` clean;`uv run ruff check` 改动文件 clean。

- [ ] **Step 6: 提交** `git commit -m "feat(playground): trace facade 端点 GET runs/{id}/trace + 只读 Langfuse client"`。

---

## Task 3: 前端 —— `getRunTrace` SDK + DTO 类型

**Files:**
- Create: `apps/admin-ui/src/api/trace_facade.ts`
- Test: `apps/admin-ui/src/api/__tests__/trace_facade.test.ts`

**Interfaces:**
- Consumes:Task 2 端点 `GET /v1/sessions/{thread}/runs/{run}/trace`;现有 `apiClient`(`api/client.ts`,相对 URL + bearer,原始 payload 无 envelope 者照 `getRun` 处理)。
- Produces:
  ```ts
  export type TraceStatus = "ok" | "not_ready" | "unavailable" | "no_trace";
  export interface TraceSpan { id:string; parentId:string|null; kind:"session"|"llm"|"tool"|"span";
    label:string; detail:string|null; startMs:number; latencyMs:number;
    model:string|null; inputTokens:number|null; outputTokens:number|null; costUsd:number|null;
    input:string|null; output:string|null; }
  export interface RunTrace { status:TraceStatus; trace?:{name:string;latencyMs:number;totalCostUsd:number|null;spanCount:number}; spans?:TraceSpan[]; }
  export function getRunTrace(threadId:string, runId:string): Promise<RunTrace>
  ```

- [ ] **Step 1: 写失败测试** —— mock `apiClient.get`,断言 `getRunTrace` 请求 `/v1/sessions/{t}/runs/{r}/trace` 并原样返回四种 status 之一(ok 带 spans / not_ready 等)。照 `api/runs.ts` 的 `getRun` 无 envelope 处理先例。

- [ ] **Step 2: FAIL → 写实现 → PASS + typecheck** —— `cd apps/admin-ui && npx vitest run src/api/__tests__/trace_facade.test.ts && pnpm typecheck`。

- [ ] **Step 3: 提交** `git commit -m "feat(playground): trace_facade getRunTrace SDK + DTO 类型"`。

---

## Task 4: 前端 —— `TraceView` 渲染(树+瀑布+详情面板)

把 `RunTrace` 渲成线框 v4 的左树+右瀑布+点击详情。**JSX 结构/testid/文案/样式转写 `docs/superpowers/specs/2026-07-11-batch4b-wireframe.html`**(`.wf` 两窗行对齐、`.gbar` 瀑布 bar、`.detail` 详情面板、`.tdot` 类型点)。

**Files:**
- Create: `apps/admin-ui/src/pages/agent_detail/playground/TraceView.tsx`
- Create: `apps/admin-ui/src/pages/agent_detail/playground/__tests__/TraceView.test.tsx`
- Modify: `apps/admin-ui/src/i18n/locales/{en,zh-CN}.ts`

**Interfaces:**
- Consumes:`RunTrace`/`TraceSpan`(Task 3);`fmtDuration`(4a,`./duration_format`)。
- Produces:`export function TraceView(props:{ trace: RunTrace }): ...`（null/占位交给状态分支;不注解 JSX.Element）。

- [ ] **Step 1: i18n 键(en 类型+值 / zh 值)** —— 标签「精确」、状态文案（处理中/暂不可用/无 trace/刷新）、详情段标题（输入 prompt / 输出 response）、用途「主推理」等。完整键对着线框补,三处 parity。
- [ ] **Step 2: 写失败测试** —— `TraceView.test.tsx`:①`status:"ok"` + spans(含一 llm 带 model + 一 tool)→ 断言 `data-testid="trace-view"` 存在、渲出各 span label + `fmtDuration` 串、瀑布 bar 存在、model chip 显(有)/不显(空);②点一行 → 详情面板显该 span input/output(有则显、空段不渲);③`not_ready`/`unavailable`/`no_trace` → 各渲对应状态文案,不渲树。(testid 照线框 `trace-view`/`trace-row`/`trace-detail` 派生。)
- [ ] **Step 3: FAIL** —— `cd apps/admin-ui && npx vitest run src/pages/agent_detail/playground/__tests__/TraceView.test.tsx`。
- [ ] **Step 4: 写 `TraceView.tsx`** —— 转写线框:状态四分支;`ok` 时按 `parentId` 建树、按前序渲行(左窗树连接线+类型点+label+detail+model/cost chip 有才显;右窗瀑布 bar `left=startMs/traceLatencyMs`、`width=latencyMs/traceLatencyMs`,类型色);受控 `useState` 选中行 → 下方 `.detail` 面板显选中 span 的 meta + input/output(可折叠、空段不渲);`fmtDuration` 渲耗时。`ReactNode` 具名;`var(--ew-*)`;禁 any。
- [ ] **Step 5: PASS + typecheck + 全量** —— `cd apps/admin-ui && npx vitest run && pnpm typecheck` 全绿。
- [ ] **Step 6: 提交** `git commit -m "feat(playground): TraceView 归一 span 树 + 瀑布 + 节点详情"`。

---

## Task 5: 前端 —— 接入 PlaygroundTab(第三档「精确」+ 用途标签 A′ + 直达外链)

**Files:**
- Modify: `apps/admin-ui/src/pages/agent_detail/PlaygroundTab.tsx`
- Create: `apps/admin-ui/src/pages/agent_detail/playground/trace_purpose.ts`(A′ 用途标注纯函数)+ 测试
- Modify: `apps/admin-ui/src/i18n/locales/{en,zh-CN}.ts`（外链文案，若未在 T4 加）

**Interfaces:** Consumes Task 1–4 全部 + Batch 3 `parseTimeline`(`api/timeline.ts`,取 agent 节点序列)+ `buildLangfuseTraceUrl`(`config/env.ts`)+ `getRun` 的 `RunDetail.trace_id`。

- [ ] **Step 1: 用途标注纯函数(A′)** —— `trace_purpose.ts`:
  ```ts
  export function labelPurpose(spans: readonly TraceSpan[], agentStepCount: number): TraceSpan[]
  ```
  规则:取 spans 里 `kind==="llm"` 者按 startMs 排序;若其数量 == `agentStepCount`(SSE agent AI message 数)→ 全部贴 `detail:"主推理"`(1:1 干净对上);**否则(有隐藏子调用如记忆抽取)→ 一律不改,保持泛化**(不猜)。测试:数量相等→全标主推理;不等→不标。
  > A′ 依据(spec §3.2 spike):SSE 只暴露 agent 的 AI message,记忆抽取等不在;故仅当 llm span 数与 agent 步数一致时能安全全标主推理,否则泛化 + 详情面板 prompt 自证用途。
- [ ] **Step 2: TurnCard 接线** —— 在 events Collapse 的视图切换器(Batch 3 `playground-event-view-toggle` Segmented)**加第三档「精确」**;选中时:`useMemo` 调 `getRunTrace(threadId, runId)`(懒取,仅「精确」激活时)→ `labelPurpose(trace.spans, agentStepCount)`(agentStepCount 由本轮 `parseTimeline` 数 `kind==="agent"`)→ `<TraceView trace={...}/>`。加载态显「加载中」。
- [ ] **Step 3: 直达外链(item 15)** —— 复用 `buildLangfuseTraceUrl(turn 的 trace_id)`(trace_id 从 `RunDetail.trace_id`)+ `isSystemAdmin` 门控(照 `TraceToolbar`;若 PlaygroundTab 已有 identity/isSystemAdmin 则复用,否则按现有获取方式)。env 未配 → url null → 隐藏。放事件区工具条,与现有并列。
- [ ] **Step 4: 全量 + typecheck + e2e 核** —— `cd apps/admin-ui && npx vitest run && pnpm typecheck` 全绿;`grep -rn "playground-event-view-toggle\|trace-view" apps/admin-ui/e2e/` —— 第三档 Segmented 不破坏现有 view-toggle 的 e2e 断言(Batch 3 教训)。
- [ ] **Step 5: 手动冒烟** —— 跑含工具+记忆抽取的 run → 「精确」显归一树+瀑布;agent 调用标主推理、记忆抽取泛化;点记忆抽取行详情面板 prompt 自证;系统管理员见「在 Langfuse 中打开」跳对应 trace。
- [ ] **Step 6: 提交** `git commit -m "feat(playground): TurnCard 接入精确 trace 视图 + 用途标注 + Langfuse 直达"`。

---

## 验收(Batch 4b 整体)

- [ ] 后端:`cd services/control-plane && uv run pytest tests/test_trace_facade_normalize.py tests/test_trace_facade_endpoint.py -v` 全绿;`uv run mypy services/control-plane/src` clean;`uv run ruff check` 改动文件 clean。
- [ ] 前端:`cd apps/admin-ui && npx vitest run` 全绿;`pnpm typecheck` exit 0。
- [ ] `grep -rn "playground-event-view-toggle\|trace-view\|trace-row" apps/admin-ui/e2e/` —— 无因第三档失效的 testid。
- [ ] 手动冒烟(见 Task 5 Step 5)。
- [ ] 回归:时间线/原始两档、Batch 1-4a 其余调试视图不变。

## 依赖与顺序

`T1 归一器 → T2 端点`(端点吃归一器);`T3 SDK` 吃 T2 DTO;`T4 TraceView` 吃 T3;`T5 接入` 吃 T1-4 全部 + Batch 3 parseTimeline。序:**T1 → T2 → T3 → T4 → T5**。

## Self-Review(计划 vs spec 4b)

- **item 13 facade(端点+归属门控+归一+DTO+降级)** → T1(归一器)+ T2(端点+client+降级)。✅
- **item 14 span 树视图(瀑布+详情面板+状态)** → T4(转写线框 v4)+ T3(取数)。✅
- **item 15 直达外链(系统管理员门控)** → T5 Step 3。✅
- **用途标签 A′(部分标+详情兜底)** → T5 Step 1 `labelPurpose`(1:1 才标,否则泛化)+ 详情面板(T4)prompt 自证。✅
- **cost/model best-effort / null 隐藏** → T1 DTO 带 null;T4 有才显。✅
- **归一映射(合并/人话/省略/退化)** → T1 完整规则 + 3 测试(合并/退化/截断)。✅
- **鉴权分层(facade 归属 / 外链系统管理员)** → T2 归属门控测试(非 owner 404、不要求管理员)+ T5 外链 isSystemAdmin。✅
- **Batch 1-4a 教训** → uv pytest/mypy/**ruff**、pnpm typecheck、ReactNode 具名、e2e testid、线框转写,全在 Global Constraints + 各 Task。✅
- **无 placeholder**:T1/T3 完整代码/测试;T2/T4/T5 impl 指向 get_run 现有模式 / 线框 v4 / Batch 3 parseTimeline(现存物,非 placeholder);Langfuse obj 属性名注明双取兜底。✅
