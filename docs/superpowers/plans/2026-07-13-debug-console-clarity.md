# 调试台清晰度重构 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 让调试台清晰反馈整个 run、分层展示全数据、快速定位错误 —— 修 trace 精确视图(结构化消息/kind 标签/错误红标/原文层)+ 共享清洗与状态条到时间线。

**Architecture:** 两视图各司其职(时间线=语义、trace=时序+全数据),共享「不可信清洗 util」+「RunStatusBanner」。后端 facade 发结构化 `Message[]` + 错误字段 + raw 端点;orchestrator 补工具失败的 span ERROR level。

**Tech Stack:** control-plane(Python uv, FastAPI, Langfuse SDK)、orchestrator(Python, OTel)、admin-ui(React+Vite+AntD5+react-i18next、vitest)。

spec:`docs/superpowers/specs/2026-07-13-debug-console-clarity-design.md`。线框:同目录 `*-wireframe.html` / `*-full-page.html`。

## Global Constraints

- 后端降级**永不 500**:facade 全 best-effort(现有 try/except 链保留);raw 端点任何失败 → 404。
- ownership 门 **404 隐藏跨租户**(复用 trace 端点那套 thread tenant + `caller_owns_thread`)。
- 提交前后端跑 **`uv run ruff check` 且 `uv run ruff format --check`**(两步都跑,CI 有 format-check);mypy 按 CI 范围(含 tests)。
- 前端 **`pnpm typecheck`(tsc -b)+ `npx vitest run`** 必过;i18n **三处齐**(en interface + en value + zh-CN value,编译器强制);语义色走 `--ew-*` 令牌**双主题**。
- **不加新依赖**;immutability(不原地改 dict/state);小文件高内聚;每改动行可溯源到需求。
- 常量精确值:`_MSG_CAP = 8192`、`_TEXT_CAP = 16384`、`DATAMARK_GLYPH = "▁"`(U+2581)、围栏 `«UNTRUSTED nonce=<hex>»`。
- 埋点侧信道全 best-effort(不阻塞 run)。

---

## Task 1: orchestrator — 工具失败 span 置 ERROR level(R1 修复)

**背景:** 工具异常被 `_invoke_tool` 吞成 `ToolMessage(status="error")`,不抛穿 `expert_work_span` → observation `level` 停 `DEFAULT`。trace 错误红标依赖 level=ERROR,故须在 span 内显式置。

**Files:**
- Modify: `services/orchestrator/src/orchestrator/graph_builder/builder.py`(tool_call span 块 ~2035-2048;新增 helper `_record_tool_error` 于 `_record_tool_io` 附近 ~1996)
- Test: `services/orchestrator/tests/test_tool_span_io.py`(现有,追加)

**Interfaces:**
- Consumes: `outcome: tuple[ToolMessage, Mapping, int, ClassifiedToolError | None]`(`_invoke_tool` 返回);`ClassifiedToolError.summary: str`(`tools/error_classifier.py:71`)。
- Produces: 工具失败时 span 状态 = OTel `StatusCode.ERROR`,description = `outcome[3].summary`(或 content 前 200)→ Langfuse 映射 observation `level=ERROR` + `status_message`。

- [ ] **Step 1: 写失败测试** —— `test_tool_span_io.py` 追加:模拟 error outcome,断言 span 收到 `set_status(ERROR)`。用现有测试的 span 桩风格(捕获 `set_status` 调用)。

```python
def test_error_outcome_sets_span_status_error(monkeypatch):
    """工具返回 error ToolMessage 时,tool_call span 置 StatusCode.ERROR + summary。"""
    from orchestrator.graph_builder import builder

    calls: list = []

    class _Span:
        def set_attribute(self, *a, **k): pass
        def set_status(self, status): calls.append(status)
        def record_exception(self, exc): pass

    err = builder.ClassifiedToolError(
        tool_name="exec_python", error_class=list(builder.ToolErrorClass)[0]
        if hasattr(builder, "ToolErrorClass") else "runtime",
        summary="SandboxTimeout: 执行超过 30s", retryable=False, advice="",
    ) if hasattr(builder, "ClassifiedToolError") else None
    msg = builder.ToolMessage(content="[tool error] boom", tool_call_id="c1", status="error", name="exec_python")
    builder._record_tool_error(_Span(), (msg, {}, 0, err))
    assert calls, "expected set_status to be called on error outcome"
    status = calls[0]
    assert status.status_code.name == "ERROR"
```

> 注:`ClassifiedToolError`/`ToolErrorClass` 若未在 builder 命名空间,改从 `orchestrator.tools.error_classifier` 直接 import 构造。实现前先确认导出。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd services/orchestrator && uv run pytest tests/test_tool_span_io.py -k error_outcome -x`
Expected: FAIL(`_record_tool_error` 未定义)

- [ ] **Step 3: 实现** —— builder.py 顶部 import(若无):

```python
from opentelemetry.trace import Status, StatusCode
```

`_record_tool_io` 之后加 helper:

```python
def _record_tool_error(span: Any, outcome: tuple[Any, ...]) -> None:
    """工具返回 error ToolMessage 时,把 tool_call span 标为 ERROR。

    ``_invoke_tool`` 吞掉工具异常(返回 ``ToolMessage(status="error")``),
    span 体不抛,故 ``expert_work_span`` 的异常路径不会置 ERROR。手动置,
    让 Langfuse observation 得到 ``level=ERROR`` + ``status_message``。
    侧信道:任何失败只丢观测数据,不阻塞 run。
    """
    try:
        classified = outcome[3] if len(outcome) > 3 else None
        summary = getattr(classified, "summary", None) or str(outcome[0].content)[:200]
        span.set_status(Status(StatusCode.ERROR, summary))
    except Exception:  # instrumentation side-channel, never blocks a run
        logger.warning("tool_span_error.record_failed", exc_info=True)
```

tool_call span 块内、`_record_tool_io` 之后加:

```python
        with expert_work_span(
            ExpertWorkComponent.ORCHESTRATOR, "tool_call", attributes={"tool": name}
        ) as span:
            outcome = await _invoke_tool(...)
            _record_tool_io(span, args, outcome[0].content)
            if outcome[0].status == "error":
                _record_tool_error(span, outcome)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd services/orchestrator && uv run pytest tests/test_tool_span_io.py -x`
Expected: PASS(含现有测试不回归)

- [ ] **Step 5: lint + 提交**

```bash
cd services/orchestrator && uv run ruff check src tests && uv run ruff format --check src tests
git add services/orchestrator/src/orchestrator/graph_builder/builder.py services/orchestrator/tests/test_tool_span_io.py
git commit -m "feat(orchestrator): 工具失败 span 置 ERROR level + status_message"
```

---

## Task 2: facade — TraceSpan 错误字段(level / statusMessage)

**Files:**
- Modify: `services/control-plane/src/control_plane/api/trace_facade.py`（`TraceSpan`/`_ParsedObs` dataclass、`_parse_observation`、`_span_as_dict`）
- Test: `services/control-plane/tests/test_trace_facade_normalize.py`

**Interfaces:**
- Produces: 每个 span dict 新增 `"level": str`("default"|"warning"|"error")+ `"statusMessage": str | None`。

- [ ] **Step 1: 写失败测试**

```python
def test_normalize_extracts_level_and_status_message():
    from types import SimpleNamespace
    from control_plane.api import trace_facade

    obs = SimpleNamespace(
        id="o1", type="GENERATION", name="llm_call", parent_observation_id=None,
        start_time=None, latency=1.0, model="glm-4.6", input=None, output=None,
        level="ERROR", status_message="SandboxTimeout",
    )
    trace = SimpleNamespace(name="t", latency=1.0, total_cost=None, observations=[obs])
    out = trace_facade.normalize_trace(trace)
    span = out["spans"][0]
    assert span["level"] == "error"
    assert span["statusMessage"] == "SandboxTimeout"


def test_normalize_defaults_level_when_absent():
    from types import SimpleNamespace
    from control_plane.api import trace_facade
    obs = SimpleNamespace(id="o1", type="SPAN", name="expert_work.session.run",
        parent_observation_id=None, start_time=None, latency=1.0, input=None, output=None)
    trace = SimpleNamespace(name="t", latency=1.0, total_cost=None, observations=[obs])
    span = trace_facade.normalize_trace(trace)["spans"][0]
    assert span["level"] == "default"
    assert span["statusMessage"] is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd services/control-plane && uv run pytest tests/test_trace_facade_normalize.py -k "level or status_message" -x`
Expected: FAIL(`KeyError: 'level'`)

- [ ] **Step 3: 实现** —— `TraceSpan` 与 `_ParsedObs` 各加两字段:

```python
    level: str
    status_message: str | None
```

新增 helper:

```python
def _level(o: Any) -> str:
    raw = getattr(o, "level", None)
    if raw is None:
        return "default"
    # ObservationLevel enum → "DEFAULT"/"WARNING"/"ERROR";也兼容裸字符串
    text = getattr(raw, "value", None) or str(raw)
    return text.rsplit(".", 1)[-1].lower()
```

`_parse_observation` 里 `_ParsedObs(...)` 追加 `level=_level(o)`, `status_message=_clean_str(getattr(o, "status_message", None))`。`normalize_trace` 构造 `TraceSpan(...)` 追加 `level=parsed.level`, `status_message=parsed.status_message`。`_span_as_dict` 追加 `"level": span.level, "statusMessage": span.status_message`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd services/control-plane && uv run pytest tests/test_trace_facade_normalize.py -x`
Expected: PASS(现有测试不回归)

- [ ] **Step 5: lint + 提交**

```bash
cd services/control-plane && uv run ruff check src tests && uv run ruff format --check src tests
git add services/control-plane/src/control_plane/api/trace_facade.py services/control-plane/tests/test_trace_facade_normalize.py
git commit -m "feat(control-plane): trace facade 提取 span level + statusMessage"
```

---

## Task 3: facade — 结构化 i/o(RenderedIo / RenderedMessage)

**背景:** 现状 `_render_io` 把消息 list 拍平成单字符串再整串头部截断(io_cap=32768)→ 巨型 system prompt 塔屏、尾部对话被切、role 恒 None 显 `[message]`。改为发结构化消息,role 取 `type`,按消息截断。

**Files:**
- Modify: `services/control-plane/src/control_plane/api/trace_facade.py`（重写 `_render_io`、`_cap`;`TraceSpan`/`_ParsedObs` 的 `input`/`output` 类型 `str | None` → `dict | None`;去掉 `io_cap` 线程)
- Test: `services/control-plane/tests/test_trace_facade_normalize.py`

**Interfaces:**
- Produces: span dict 的 `input`/`output` =
  `{"kind":"messages","messages":[{role,content,truncated,fullChars,toolCalls}]}` 或
  `{"kind":"text","text":str,"truncated":bool,"fullChars":int}` 或 `None`。

- [ ] **Step 1: 写失败测试**

```python
def test_render_io_messages_role_from_type_and_toolcalls():
    from control_plane.api.trace_facade import _render_io
    value = [
        {"type": "system", "content": "you are helpful", "role": None},
        {"type": "ai", "content": "", "tool_calls": [{"name": "exec_python", "args": {}}]},
    ]
    out = _render_io(value)
    assert out["kind"] == "messages"
    assert out["messages"][0]["role"] == "system"
    assert out["messages"][0]["fullChars"] == len("you are helpful")
    assert out["messages"][1]["role"] == "ai"
    assert out["messages"][1]["toolCalls"] == ["exec_python"]


def test_render_io_message_per_message_truncation():
    from control_plane.api.trace_facade import _render_io, _MSG_CAP
    big = "x" * (_MSG_CAP + 10)
    out = _render_io([{"type": "system", "content": big}])
    m = out["messages"][0]
    assert m["truncated"] is True
    assert m["fullChars"] == _MSG_CAP + 10
    assert m["content"].endswith("…(已截断)")
    assert len(m["content"]) <= _MSG_CAP + len("…(已截断)")


def test_render_io_text_kind_for_tool_args():
    from control_plane.api.trace_facade import _render_io
    out = _render_io({"code": "print(1)"})
    assert out["kind"] == "text"
    assert '"code"' in out["text"]
    assert out["truncated"] is False


def test_render_io_block_list_content():
    from control_plane.api.trace_facade import _render_io
    out = _render_io([{"type": "human", "content": [{"type": "text", "text": "hi"}]}])
    assert out["messages"][0]["content"] == "hi"


def test_render_io_none():
    from control_plane.api.trace_facade import _render_io
    assert _render_io(None) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd services/control-plane && uv run pytest tests/test_trace_facade_normalize.py -k render_io -x`
Expected: FAIL

- [ ] **Step 3: 实现** —— 顶部常量:

```python
_MSG_CAP = 8192
_TEXT_CAP = 16384
```

重写 `_render_io`(去掉 `io_cap` 参数)+ helpers:

```python
def _extract_role(m: dict[str, Any]) -> str:
    return str(m.get("type") or m.get("role") or "message")


def _extract_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b["text"] for b in content if isinstance(b, dict) and isinstance(b.get("text"), str)
        )
    return json.dumps(content, ensure_ascii=False)


def _extract_tool_calls(m: dict[str, Any]) -> list[str] | None:
    raw = m.get("tool_calls")
    if not raw:
        ak = m.get("additional_kwargs")
        raw = ak.get("tool_calls") if isinstance(ak, dict) else None
    if not isinstance(raw, list) or not raw:
        return None
    names: list[str] = []
    for c in raw:
        if isinstance(c, dict):
            name = c.get("name") or (c.get("function") or {}).get("name")
            if name:
                names.append(str(name))
    return names or None


def _cap_text(text: str, cap: int) -> tuple[str, bool, int]:
    full = len(text)
    if full > cap:
        return text[:cap] + _TRUNCATION_SUFFIX, True, full
    return text, False, full


def _is_message_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(m, dict) and "content" in m for m in value)
    )


def _render_io(value: Any) -> dict[str, Any] | None:
    """结构化渲染 observation 的 input/output(spec §A1)。"""
    if value is None:
        return None
    if _is_message_list(value):
        messages: list[dict[str, Any]] = []
        for m in value:
            capped, truncated, full = _cap_text(_extract_content(m.get("content")), _MSG_CAP)
            messages.append(
                {
                    "role": _extract_role(m),
                    "content": capped,
                    "truncated": truncated,
                    "fullChars": full,
                    "toolCalls": _extract_tool_calls(m),
                }
            )
        return {"kind": "messages", "messages": messages}
    if isinstance(value, str):
        text_full = value
    else:
        try:
            text_full = json.dumps(value, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            text_full = str(value)
    capped, truncated, full = _cap_text(text_full, _TEXT_CAP)
    return {"kind": "text", "text": capped, "truncated": truncated, "fullChars": full}
```

删除旧 `_cap`(被 `_cap_text` 取代)。`_parse_observation` 改 `input=_render_io(getattr(o, "input", None))`, `output=_render_io(getattr(o, "output", None))`(去掉 io_cap 实参)。`_parse_observation`/`normalize_trace`/`fetch_and_normalize` 签名去掉 `io_cap` 参数。`TraceSpan.input`/`output`、`_ParsedObs.input`/`output` 类型改 `dict[str, Any] | None`。

- [ ] **Step 4: 跑测试确认通过 + 全 facade 测试不回归**

Run: `cd services/control-plane && uv run pytest tests/test_trace_facade_normalize.py tests/test_trace_facade_endpoint.py -x`
Expected: PASS(旧的 io_cap 相关断言可能要改为结构化断言 —— 一并更新)

- [ ] **Step 5: lint + 提交**

```bash
cd services/control-plane && uv run ruff check src tests && uv run ruff format --check src tests
git add services/control-plane/src/control_plane/api/trace_facade.py services/control-plane/tests/
git commit -m "feat(control-plane): trace facade 发结构化消息 i/o(role 取 type、按消息截断)"
```

---

## Task 4: facade — raw 全文端点

**Files:**
- Modify: `services/control-plane/src/control_plane/api/runs.py`(trace 端点旁加 `/trace/raw`)、`trace_facade.py`(加 `fetch_span_raw` helper)
- Test: `services/control-plane/tests/test_trace_facade_endpoint.py`

**Interfaces:**
- Produces: `GET /v1/sessions/{thread_id}/runs/{run_id}/trace/raw?span=<id>&field=input|output` → `{spanId, field, content}` 或 404。
- Consumes: `fetch_span_raw(client, trace_id, span_id, field) -> str | None`(找 observation,渲染该 field 全文,无 cap 无清洗)。

- [ ] **Step 1: 写失败测试**(facade helper 层 + 端点 ownership 复用现有 endpoint 测试桩)

```python
def test_fetch_span_raw_returns_full_untruncated(monkeypatch):
    from control_plane.api.trace_facade import fetch_span_raw
    big = [{"type": "system", "content": "y" * 50000}]
    client = _FakeLangfuseClient(_fake_trace_with(observations=[
        SimpleNamespace(id="o9", type="GENERATION", name="llm_call",
            parent_observation_id=None, start_time=None, latency=1.0, input=big, output=None),
    ]))
    out = fetch_span_raw(client, "trace-1", "o9", "input")
    assert out is not None and len(out) >= 50000 and "…(已截断)" not in out


def test_fetch_span_raw_missing_span_returns_none(...):
    ...
    assert fetch_span_raw(client, "trace-1", "nope", "input") is None
```

> `fetch_span_raw` 拍平 messages 为 `[role]\ncontent`(用 `_extract_role`/`_extract_content`),**不 cap 不清洗**;text kind 返 `str`/`json.dumps`。field 只接受 `"input"|"output"`,其它 → None。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd services/control-plane && uv run pytest tests/test_trace_facade_endpoint.py -k span_raw -x`
Expected: FAIL

- [ ] **Step 3: 实现** —— `trace_facade.py` 加:

```python
def fetch_span_raw(client: Any, trace_id: str, span_id: str, field: str) -> str | None:
    """未截断、未清洗的单 span input/output 全文(raw 层)。best-effort → None。"""
    if client is None or field not in ("input", "output"):
        return None
    try:
        trace = client.api.trace.get(trace_id)
    except Exception:
        return None
    for o in getattr(trace, "observations", None) or []:
        if str(getattr(o, "id", "")) != span_id:
            continue
        value = getattr(o, field, None)
        if value is None:
            return None
        if _is_message_list(value):
            return "\n\n".join(f"[{_extract_role(m)}]\n{_extract_content(m.get('content'))}" for m in value)
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    return None
```

`runs.py` —— 在 trace 端点(`get_thread_run_trace` 附近)加同款 ownership 门的 `/trace/raw` 路由:先复用 `threads.get`+`caller_owns_thread`+`runs.get`(拿 `trace_id`),再 `content = fetch_span_raw(client, trace_id, span, field)`;`content is None` → 404;否则 `JSONResponse({"spanId": span, "field": field, "content": content})`。query 参数 `span: str`、`field: str`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd services/control-plane && uv run pytest tests/test_trace_facade_endpoint.py -x`
Expected: PASS

- [ ] **Step 5: lint + 提交**

```bash
cd services/control-plane && uv run ruff check src tests && uv run ruff format --check src tests
git add services/control-plane/src/control_plane/api/runs.py services/control-plane/src/control_plane/api/trace_facade.py services/control-plane/tests/test_trace_facade_endpoint.py
git commit -m "feat(control-plane): trace raw 端点 —— 未截断全文 i/o(ownership 门)"
```

---

## Task 5: 前端 DTO 镜像 + fetchRunTraceRaw

**Files:**
- Modify: `apps/admin-ui/src/api/trace_facade.ts`(类型 + 拉取函数)
- Test: `apps/admin-ui/src/api/__tests__/trace_facade.test.ts`(若无则建;否则并入现有 api 测试)

**Interfaces:**
- Produces(**纯加法** —— 不动 `TraceSpan.input/output` 类型,那个 breaking 翻转留给 T8 与消费方同落,保 T5 独立 typecheck 过):
```ts
export type RenderedMessage = { role: string; content: string; truncated: boolean; fullChars: number; toolCalls: string[] | null };
export type RunTraceIo =
  | { kind: "messages"; messages: RenderedMessage[] }
  | { kind: "text"; text: string; truncated: boolean; fullChars: number };
// TraceSpan 加(additive): level: "default"|"warning"|"error"; statusMessage: string | null;
// TraceSpan.input/output 仍保持现状类型(T8 翻转为 RunTraceIo | null)
export function fetchRunTraceRaw(threadId: string, runId: string, spanId: string, field: "input" | "output"): Promise<string>;
```

- [ ] **Step 1: 写失败测试** —— 断言 `fetchRunTraceRaw` 命中正确 URL、解包 `content`。用现有 api 测试的 fetch mock 风格。

```ts
it("fetchRunTraceRaw hits the raw endpoint and returns content", async () => {
  const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ spanId: "o1", field: "input", content: "FULL" }), { status: 200 }));
  const out = await fetchRunTraceRaw("t1", "r1", "o1", "input");
  expect(out).toBe("FULL");
  expect(String(spy.mock.calls[0][0])).toContain("/sessions/t1/runs/r1/trace/raw?span=o1&field=input");
});
```

- [ ] **Step 2: 跑确认失败** — `cd apps/admin-ui && npx vitest run src/api/__tests__/trace_facade.test.ts` → FAIL
- [ ] **Step 3: 实现** — 加类型;改 `TraceSpan`(`input`/`output: RunTraceIo | null`、`level`、`statusMessage`);加 `fetchRunTraceRaw`(复用文件内现有 api-base/请求辅助;解包 `.content`)。
- [ ] **Step 4: 跑确认通过 + typecheck** — `npx vitest run src/api/__tests__/trace_facade.test.ts && pnpm typecheck` → PASS/exit 0
- [ ] **Step 5: 提交**

```bash
git add apps/admin-ui/src/api/trace_facade.ts apps/admin-ui/src/api/__tests__/trace_facade.test.ts
git commit -m "feat(admin-ui): trace DTO 结构化 i/o 类型 + level + fetchRunTraceRaw"
```

---

## Task 6: 共享 util — untrusted_clean.ts

**Files:**
- Create: `apps/admin-ui/src/pages/agent_detail/playground/untrusted_clean.ts`
- Test: `apps/admin-ui/src/pages/agent_detail/playground/__tests__/untrusted_clean.test.ts`

**Interfaces:**
- Produces: `export function cleanUntrusted(text: string): { text: string; hadUntrusted: boolean }`

- [ ] **Step 1: 写失败测试**

```ts
import { describe, expect, it } from "vitest";
import { cleanUntrusted } from "../untrusted_clean";

describe("cleanUntrusted", () => {
  it("strips the UNTRUSTED fence + ▁ glyph and flags hadUntrusted", () => {
    const raw = "«UNTRUSTED nonce=0ce9b28d1a1e»\n2026年▁ 12时▁ 星期一\n«/UNTRUSTED nonce=0ce9b28d1a1e»";
    const { text, hadUntrusted } = cleanUntrusted(raw);
    expect(hadUntrusted).toBe(true);
    expect(text).toBe("2026年 12时 星期一");
    expect(text).not.toContain("▁");
    expect(text).not.toContain("UNTRUSTED");
  });
  it("passes clean text through untouched", () => {
    const { text, hadUntrusted } = cleanUntrusted("hello world");
    expect(text).toBe("hello world");
    expect(hadUntrusted).toBe(false);
  });
});
```

- [ ] **Step 2: 跑确认失败** — `cd apps/admin-ui && npx vitest run src/pages/agent_detail/playground/__tests__/untrusted_clean.test.ts` → FAIL
- [ ] **Step 3: 实现**

```ts
/** 剥离不可信内容标记(spotlight 防注入机制产物),供调试台可读展示。
 *  围栏 «UNTRUSTED nonce=…» / «/UNTRUSTED nonce=…»(见 common/spotlight.py)
 *  折叠为一个 hadUntrusted 标志;datamark 的 ▁(U+2581)字形剥除。
 *  raw「查看原文」层不跑此 util —— 那里要看原始标记。 */
const FENCE_OPEN = /«UNTRUSTED nonce=[^»]*»\n?/g;
const FENCE_CLOSE = /\n?«\/UNTRUSTED nonce=[^»]*»/g;
const GLYPH = /▁/g;

export function cleanUntrusted(text: string): { text: string; hadUntrusted: boolean } {
  const hadUntrusted = text.includes("«UNTRUSTED nonce=");
  const cleaned = text.replace(FENCE_OPEN, "").replace(FENCE_CLOSE, "").replace(GLYPH, "");
  return { text: cleaned, hadUntrusted };
}
```

- [ ] **Step 4: 跑确认通过** — 同 Step 2 命令 → PASS
- [ ] **Step 5: 提交**

```bash
git add apps/admin-ui/src/pages/agent_detail/playground/untrusted_clean.ts apps/admin-ui/src/pages/agent_detail/playground/__tests__/untrusted_clean.test.ts
git commit -m "feat(admin-ui): 不可信内容清洗共享 util(剥 ▁/UNTRUSTED 保 badge)"
```

---

## Task 7: 共享组件 — RunStatusBanner

**Files:**
- Create: `apps/admin-ui/src/pages/agent_detail/playground/RunStatusBanner.tsx`
- Modify: `apps/admin-ui/src/i18n/en.ts`、`apps/admin-ui/src/i18n/zh-CN.ts`(新 key)
- Test: `apps/admin-ui/src/pages/agent_detail/playground/__tests__/RunStatusBanner.test.tsx`

**Interfaces:**
- Produces:
```ts
export interface RunStatusBannerProps {
  status: "ok" | "error";
  summary: string;
  metrics?: { label: string; value: string }[];
  errorLabel?: string;
  errorMessage?: string;
  onJump?: () => void;
}
export function RunStatusBanner(props: RunStatusBannerProps): JSX.Element;
```
新 i18n key:`playground.rb_ok`、`playground.rb_failed_at`(带 `{{label}}`)、`playground.rb_jump`。

- [ ] **Step 1: 写失败测试** —— ok 态显 summary + metrics;err 态显 errorLabel/errorMessage + jump 按钮触发 onJump。`data-testid="run-status-banner"`、`run-status-jump`。
- [ ] **Step 2: 跑确认失败**
- [ ] **Step 3: 实现** —— 参照线框 `.banner.ok/.err`。语义色用 `--ew-text-success`/`--ew-text-danger`(双主题)。i18n 三处齐。
- [ ] **Step 4: 跑确认通过 + typecheck**
- [ ] **Step 5: 提交** — `feat(admin-ui): RunStatusBanner 共享组件(顶部错误冒泡)`

---

## Task 8: TraceView — 结构化消息渲染 + kind 自适应标签 + 清洗

**Files:**
- Modify: `apps/admin-ui/src/pages/agent_detail/playground/TraceView.tsx`(`IoSection` 拆分、新增 `MessageBlock`、`TraceDetail` kind-aware 标签)
- Modify: `apps/admin-ui/src/i18n/en.ts`、`zh-CN.ts`
- Test: `apps/admin-ui/src/pages/agent_detail/playground/__tests__/TraceView.test.tsx`

**Interfaces:**
- Consumes: `RunTraceIo`/`RenderedMessage`(Task 5)、`cleanUntrusted`(Task 6)。
- **本任务翻转** `TraceSpan.input/output: RunTraceIo | null`(trace_facade.ts,与消费方 TraceView 同落 → typecheck 一致过)。
- Produces: LLM span → 结构化消息面板(system 默认收起、其余展开、tool 消息带不可信 badge、toolCalls 显「→ 调用 X」);tool span → `参数/结果` 标签;text kind → 单 pre。截断行含 `查看原文`(回调,Task 9 接线)。

**新 i18n key**(替换硬编码 `tr_io_*`):`tr_io_llm_msgs`/`tr_io_llm_msgs_hint`、`tr_io_llm_out`/`tr_io_llm_out_hint`、`tr_io_tool_args`/`tr_io_tool_args_hint`、`tr_io_tool_result`/`tr_io_tool_result_hint`、`tr_io_in`/`tr_io_out`(通用)、`tr_msg_truncated`(`{{n}}`)、`tr_msg_copy`、`tr_msg_raw`、`tr_msg_untrusted`、`tr_msg_toolcall`(`{{name}}`)。

- [ ] **Step 1: 写失败测试**(更新 `okTrace` 的 span fixture:`input` 从字符串改结构化 `RunTraceIo`)

```tsx
const llm = makeSpan({ id:"r1", parentId:"r0", kind:"llm", label:"LLM 调用", detail:"主推理",
  input: { kind:"messages", messages:[
    { role:"system", content:"sys", truncated:false, fullChars:3, toolCalls:null },
    { role:"human", content:"现在几点", truncated:false, fullChars:4, toolCalls:null },
    { role:"tool", content:"«UNTRUSTED nonce=ab»\n2026▁ 年\n«/UNTRUSTED nonce=ab»", truncated:false, fullChars:10, toolCalls:null },
  ]},
  output: { kind:"text", text:"晴天", truncated:false, fullChars:2 },
});
const tool = makeSpan({ id:"r2", parentId:"r0", kind:"tool", label:"工具调用", detail:"exec_python",
  input: { kind:"text", text:'{"code":"1"}', truncated:false, fullChars:11 },
  output: { kind:"text", text:"ok", truncated:false, fullChars:2 } });

it("llm span renders structured messages: system collapsed, human/tool visible, untrusted cleaned+badged", () => {
  render(<TraceView trace={okTrace([root, llm, tool])} />);
  fireEvent.click(screen.getAllByTestId("trace-row")[1]);
  const detail = screen.getByTestId("trace-detail");
  // system 默认收起:内容 "sys" 不在 DOM,role 标签在
  expect(within(detail).queryByText("sys")).not.toBeInTheDocument();
  expect(within(detail).getByText("现在几点")).toBeInTheDocument();
  // 不可信清洗:▁ 与 UNTRUSTED 不出现,badge 出现
  expect(within(detail).queryByText(/UNTRUSTED|▁/)).not.toBeInTheDocument();
  expect(within(detail).getByTestId("msg-untrusted")).toBeInTheDocument();
});

it("tool span uses 参数/结果 labels, not prompt/response", () => {
  render(<TraceView trace={okTrace([root, llm, tool])} />);
  fireEvent.click(screen.getAllByTestId("trace-row")[2]);
  const detail = screen.getByTestId("trace-detail");
  expect(within(detail).getByText("参数")).toBeInTheDocument();
  expect(within(detail).getByText("结果")).toBeInTheDocument();
  expect(within(detail).queryByText(/prompt|模型回复/)).not.toBeInTheDocument();
});
```

- [ ] **Step 2: 跑确认失败**

Run: `cd apps/admin-ui && npx vitest run src/pages/agent_detail/playground/__tests__/TraceView.test.tsx`
Expected: FAIL

- [ ] **Step 3: 实现** —— 新增 `MessageBlock`(单条消息:折叠头 role 色标 + size;body 经 `cleanUntrusted` 渲染 pre;`role==="system"` 初始 `expanded=false`,其余 `true`;`toolCalls` 非空且 content 空 → 显 `t("playground.tr_msg_toolcall",{name})`;`hadUntrusted` → `data-testid="msg-untrusted"` badge;`truncated` → 截断行 `tr_msg_truncated` + copy + `tr_msg_raw`(回调 prop `onViewRaw`,本任务先留 `undefined` 占位,Task 9 接))。`IoSection` 改为按 `RunTraceIo` 判别:`kind==="messages"` → map `MessageBlock`;`kind==="text"` → 清洗后单 pre + 截断行。`TraceDetail` 按 `span.kind` 取标签:助手函数 `ioLabels(kind)` → `{inTitle,inHint,outTitle,outHint}`。移除硬编码 `tr_io_input/_hint/_output/_hint` 用法(保留旧 key 或删,视引用)。去 `maxHeight:180` → `280`。i18n 三处齐。

- [ ] **Step 4: 跑确认通过 + typecheck**

Run: `cd apps/admin-ui && npx vitest run src/pages/agent_detail/playground/__tests__/TraceView.test.tsx && pnpm typecheck`
Expected: PASS / exit 0(现有 TraceView 测试的 string-input fixture 一并改结构化)

- [ ] **Step 5: 提交** — `feat(admin-ui): TraceView 结构化消息 + kind 自适应标签 + 不可信清洗`

---

## Task 9: TraceView — 错误红标 + 详情错误 + 查看原文弹层

**Files:**
- Modify: `apps/admin-ui/src/pages/agent_detail/playground/TraceView.tsx`
- Test: 同 `TraceView.test.tsx`

**Interfaces:**
- Consumes: `span.level`/`span.statusMessage`(Task 5)、`fetchRunTraceRaw`(Task 5)。
- Produces: `level==="error"` span → 树点/甘特条红、行底浅红;`TraceDetail` 顶 `dt-err` 显 `statusMessage`;`MessageBlock`/text 截断行「查看原文」→ 弹层显 raw 全文(`TraceView` 接 prop `threadId`/`runId` 以拉 raw)。

- [ ] **Step 1: 写失败测试** —— error span 行有 `data-testid="trace-row"` 且含红标(断 style/class 或 `trace-error-dot`);选中 error span 详情有 `data-testid="trace-detail-error"` 含 statusMessage;点截断消息「查看原文」触发 `fetchRunTraceRaw`(mock)并弹层显 content。
- [ ] **Step 2: 跑确认失败**
- [ ] **Step 3: 实现** —— `kindDotColor`/`kindBarColor` 加 `level==="error"` 分支返 `DANGER`;`TraceRow` error 行底色;`TraceDetail` 顶插 error 块;`TraceView` 新增 props `threadId?`/`runId?`,下传到 `MessageBlock` 的 `onViewRaw`(调 `fetchRunTraceRaw` → `useState` 弹层)。弹层用 AntD `Modal` 或简单覆盖层显 `<pre>`(不跑清洗)。
- [ ] **Step 4: 跑确认通过 + typecheck**
- [ ] **Step 5: 提交** — `feat(admin-ui): TraceView 错误红标 + 详情 statusMessage + 查看原文弹层`

---

## Task 10: PlaygroundTab — 接 RunStatusBanner(trace)+ 传 threadId/runId

**Files:**
- Modify: `apps/admin-ui/src/pages/agent_detail/PlaygroundTab.tsx`(~2445 `eventView === "exact"` 分支)
- Test: `apps/admin-ui/src/pages/agent_detail/__tests__/PlaygroundTab*.test.tsx`(相关)

**Interfaces:**
- Consumes: `labeledTrace`(现有)、`RunStatusBanner`(Task 7)。
- Produces: exact 视图渲 `<RunStatusBanner>`(从 `labeledTrace.spans` 派生:有 `level==="error"` → error、firstErrorLabel、onJump 选中);`<TraceView threadId={threadId} runId={runId} …/>`。

- [ ] **Step 1: 写失败测试** —— exact 视图有失败 span 时渲 `run-status-banner`(error 态)。
- [ ] **Step 2–4: 实现 + 验**(纯组装,派生函数 `traceBannerStatus(trace)`;TraceView 传 threadId/runId)。typecheck + vitest。
- [ ] **Step 5: 提交** — `feat(admin-ui): 调试台 exact 视图接 RunStatusBanner + raw 上下文`

---

## Task 11: 时间线 — ToolCallCard 清洗 + RunStatusBanner

**Files:**
- Modify: `apps/admin-ui/src/components/ToolTimeline.tsx`(`ToolCallCard` 工具 result 接 `cleanUntrusted`)
- Modify: `apps/admin-ui/src/pages/agent_detail/PlaygroundTab.tsx`(timeline 分支挂 `RunStatusBanner`,status 从 `visibleTimeline`/items 派生 SSE hasError)
- Test: `ToolTimeline` 测试 + `StepTimeline`/PlaygroundTab 相关

**Interfaces:**
- Consumes: `cleanUntrusted`、`RunStatusBanner`。
- Produces: 工具卡 result 清洗 + 不可信 badge;时间线视图顶 banner(SSE-derived,不依赖 Langfuse level)。

- [ ] **Step 1: 写失败测试** —— `ToolCallCard` 渲含 `«UNTRUSTED…»▁` 的 result → 清洗后无标记 + 有 badge;timeline 视图有 error step → 渲 `run-status-banner` error 态。
- [ ] **Step 2: 跑确认失败**
- [ ] **Step 3: 实现** —— `ToolCallCard` result 文本过 `cleanUntrusted`,`hadUntrusted` 挂 badge;PlaygroundTab timeline 分支加 `<RunStatusBanner status={items 有 hasError?"error":"ok"} …/>`,派生函数 `timelineBannerStatus(items)`。
- [ ] **Step 4: 跑确认通过 + typecheck + 全前端相关测试不回归**

Run: `cd apps/admin-ui && npx vitest run src/pages/agent_detail src/components/ToolTimeline* && pnpm typecheck`

- [ ] **Step 5: 提交** — `feat(admin-ui): 时间线工具卡清洗 + 接 RunStatusBanner`

---

## 收尾

全任务后:终门 opus 全支审(`superpowers:requesting-code-review`),再 `superpowers:finishing-a-development-branch`。人工冒烟(需 `make dev-up` 重部署 dev):精确视图 system 默认折叠 / 工具显参数结果 / 不可信清洗 / 查看原文拉全文 / 造一个工具失败 run 看错误红标 + 顶部状态条。
