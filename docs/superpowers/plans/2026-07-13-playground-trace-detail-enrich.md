# 调试台 trace 详情增强 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 trace 精确视图节点详情:(1) LLM prompt 渲染成可读文本、不早截断;(2) 工具调用显示 masked args/result;(3) LLM 调用显示答案。

**Architecture:** 读侧(facade 可读化 + 放宽 io_cap,惠及现有+新 run)+ 写侧(orchestrator 给 tool span 补 masked/capped i/o、让 LLM generation 记录 output,仅新 run)。写侧的 Langfuse OTel 属性 key + LLM-output 空因由一个 dev 验证 spike 先坐实。

**Tech Stack:** 后端 Python(control-plane facade + orchestrator 埋点 + runtime 中间件),`uv` workspace;Langfuse v3(OTel/OTLP 摄取 + SDK generation)。

## Global Constraints

- 后端 `uv run pytest` / `uv run mypy` / `uv run ruff check` 全绿(repo-root 配置)。**提交前 `uv run ruff check`**(本地 mypy 过 ≠ ruff 过)。
- **埋点旁路失败不得阻塞 run**:写侧设属性/mask/record 全 best-effort(try/except 吞,只丢观测数据)。
- **工具 i/o 必须 PII-masked**:复用 `expert_work.runtime.audit.redactor` 的 `DefaultSecretRedactor(patterns={**DEFAULT_PATTERNS, **PII_PATTERNS})`(与 Langfuse SDK mask 同款);OTLP 路径不自动 mask 是已证事实。
- **体积受限**:工具 i/o 设属性前 cap(常量,如 8192),避免撑大 Langfuse payload。
- 只影响新 run;历史 run(无数据)沿用现降级,不回归。
- surgical:写侧只碰 tool span 块 + LLM output 记录点;读侧只碰 facade 归一 + cap 默认值。
- 命令工作目录:`services/control-plane`(facade)/ `services/orchestrator`(埋点)/ repo root(跨包 mypy/ruff)。

## File Structure

- `services/control-plane/src/control_plane/api/trace_facade.py` — 可读化 helper + io_cap 默认值(Task 1)。
- `services/control-plane/tests/test_trace_facade_normalize.py` — 可读化/cap 测(Task 1)。
- `.superpowers/sdd/spike-trace-enrich.md` — spike findings(Task 2,非生产代码)。
- `services/orchestrator/src/orchestrator/graph_builder/builder.py` — tool span 补 i/o(Task 3)。
- orchestrator 埋点测试(Task 3)。
- `packages/expert-work-runtime/src/expert_work/runtime/middleware/langfuse.py` 或 LLM 调用点 — LLM output(Task 4)。

---

### Task 1: 读侧 —— facade input/output 可读化 + io_cap 32768

**Files:**
- Modify: `services/control-plane/src/control_plane/api/trace_facade.py`(`_cap` 附近 ~325;`_parse_observation` :220-221;`normalize_trace` :79 与 `fetch_and_normalize` :161 的 `io_cap` 默认值)
- Test: `services/control-plane/tests/test_trace_facade_normalize.py`

**Interfaces:**
- Consumes:Langfuse observation `input`/`output`(str / 消息 list `[{role?, content}]` / content 可为 block-list `[{type,text}]` / None)。
- Produces:`_parse_observation` 的 `input`/`output` 现为**可读文本**(消息→role+真换行内容;非消息 dict/list→indent JSON;str→原样),`io_cap` 默认 32768。

- [ ] **Step 1: 写失败测试(加到 `test_trace_facade_normalize.py`)**

```python
def test_normalize_renders_chat_messages_input_as_readable_text() -> None:
    """LLM input 是消息 list → 渲染成 role+真换行内容,不是 Python-repr 串。"""
    obs = [
        _obs("sess", "SPAN", "expert_work.session.run", None, 1.0, 0),
        _obs(
            "g",
            "GENERATION",
            "llm_call",
            "sess",
            1.0,
            0,
            input=[
                {"role": "system", "content": "You are helpful.\n\n# Rules\nBe terse."},
                {"role": "user", "content": "hi"},
            ],
        ),
    ]
    spans = normalize_trace(_trace(obs))["spans"]
    g = next(s for s in spans if s["id"] == "g")
    text = g["input"]
    # 真换行渲染(非字面 \n),role 可见,不是 repr 串
    assert "\n\n# Rules\nBe terse." in text
    assert "You are helpful." in text
    assert "system" in text and "user" in text
    assert "{'role'" not in text and "\\n" not in text  # 不是 Python-repr


def test_normalize_renders_block_list_content() -> None:
    """content 是 block-list [{type,text}] → 取 text 拼接。"""
    obs = [
        _obs("sess", "SPAN", "expert_work.session.run", None, 1.0, 0),
        _obs(
            "g",
            "GENERATION",
            "llm_call",
            "sess",
            1.0,
            0,
            input=[{"role": "user", "content": [{"type": "text", "text": "block one"}]}],
        ),
    ]
    g = next(s for s in normalize_trace(_trace(obs))["spans"] if s["id"] == "g")
    assert "block one" in g["input"]


def test_normalize_io_cap_default_raised_to_32768() -> None:
    """默认 io_cap 放宽到 32768:20000 字符的 input 不再截断。"""
    big = "x" * 20000
    obs = [
        _obs("sess", "SPAN", "expert_work.session.run", None, 1.0, 0),
        _obs("g", "GENERATION", "llm_call", "sess", 1.0, 0, input=big),
    ]
    g = next(s for s in normalize_trace(_trace(obs))["spans"] if s["id"] == "g")
    assert "截断" not in g["input"] and len(g["input"]) == 20000


def test_normalize_still_caps_beyond_32768() -> None:
    obs = [
        _obs("sess", "SPAN", "expert_work.session.run", None, 1.0, 0),
        _obs("g", "GENERATION", "llm_call", "sess", 1.0, 0, input="y" * 40000),
    ]
    g = next(s for s in normalize_trace(_trace(obs))["spans"] if s["id"] == "g")
    assert "截断" in g["input"]
```

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `cd services/control-plane && uv run pytest tests/test_trace_facade_normalize.py -k "readable or block_list or 32768 or beyond" -v`
Expected: 前两条 FAIL(现渲染 repr 串);cap 两条按现默认 8192 行为断言不符。

- [ ] **Step 3: 实现可读化 + 放宽默认(trace_facade.py)**

新增 helper(放在 `_cap` 前后):

```python
def _render_io(value: Any, io_cap: int) -> str | None:
    """把 observation 的 input/output 渲染成人类可读文本再截断。

    - 消息 list(``[{role?, content}]``)→ 每条 ``role`` 行 + content(真换行),
      content 为 block-list(``[{type,text}]``)则取 text 拼接。
    - 其它 list/dict → ``json.dumps(ensure_ascii=False, indent=2)``(真换行、非 ASCII 不转义)。
    - str → 原样。None → None。
    """
    if value is None:
        return None
    if isinstance(value, str):
        return _cap(value, io_cap)
    if isinstance(value, list) and value and all(isinstance(m, dict) and "content" in m for m in value):
        parts: list[str] = []
        for m in value:
            role = str(m.get("role", "")) or "message"
            content = m.get("content")
            if isinstance(content, list):
                text = "".join(
                    b["text"] for b in content if isinstance(b, dict) and isinstance(b.get("text"), str)
                )
            else:
                text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            parts.append(f"[{role}]\n{text}")
        return _cap("\n\n".join(parts), io_cap)
    try:
        return _cap(json.dumps(value, ensure_ascii=False, indent=2), io_cap)
    except (TypeError, ValueError):
        return _cap(str(value), io_cap)
```

确认文件顶部 `import json`(若无则加)。`_parse_observation`(:220-221)改:

```python
        input=_render_io(getattr(o, "input", None), io_cap),
        output=_render_io(getattr(o, "output", None), io_cap),
```

`normalize_trace`(:79)与 `fetch_and_normalize`(:161)签名默认 `io_cap: int = 8192` 改为 `io_cap: int = 32768`。`_cap` 本身不动(仍是纯截断)。

- [ ] **Step 4: 跑测试确认 PASS + 现有归一测不回归**

Run: `cd services/control-plane && uv run pytest tests/test_trace_facade_normalize.py -v`
Expected: 新 4 条 PASS;现有测试全绿(注意原 `test_normalize_caps_oversized_io` 用显式 `io_cap=100` 不受默认值影响,仍绿)。

- [ ] **Step 5: mypy + ruff**

Run: `cd services/control-plane && uv run mypy src/control_plane/api/trace_facade.py && uv run ruff check`
Expected: clean。

- [ ] **Step 6: Commit**

```bash
git add services/control-plane/src/control_plane/api/trace_facade.py services/control-plane/tests/test_trace_facade_normalize.py
git commit -m "feat(playground): trace 详情 input/output 可读化(消息→文本)+ io_cap 放宽 32768"
```

---

### Task 2: 验证 spike —— OTel observation i/o 属性 key + mask 缺口 + LLM output 空因

**目的**:坐实 Task 3/4 的两个未知,写进 findings 文件供后续任务用。**dev 环境实证,无生产代码。**

**Files:**
- Create: `.superpowers/sdd/spike-trace-enrich.md`(findings;git-ignored scratch,不提交)

- [ ] **Step 1: 确认 Langfuse OTel 摄取的 input/output 属性 key**

在 dev 容器内发一个带候选属性的 OTel span(用 orchestrator 已配置的 tracer/OTLP→Langfuse),flush,~1s 后 `lf.api.trace.get` 拉回,看 observation 的 `input`/`output` 是否被填。

先试 Langfuse 原生 key `langfuse.observation.input` / `langfuse.observation.output`;填不上再试 `input.value`/`output.value`(OpenInference)与 GenAI 语义约定。

验证法(参照 Batch 4b spike):`docker exec -i expert-work-control-plane-blue /app/.venv/bin/python`(heredoc 需 `-i`),脚本用 `expert_work.common.observability` 的 `expert_work_span` / `get_tracer` 发 span 设候选属性,`from opentelemetry import trace; trace.get_tracer_provider().force_flush()` 后用 `Langfuse(...).api.trace.get(<新 trace_id>)` 读回。DB 佐证:`docker exec expert-work-postgres psql -U $POSTGRES_USER -d expert_work_dev`。

**产出**:确认的属性 key(写进 findings 文件)。

- [ ] **Step 2: 确认 OTLP 路径不 mask**

在 Step 1 的 span 属性里放一个已知 secret 样式串(如 `sk-test-AKIA...`),读回看是否被 redact。**预期未 masked** → 佐证 Task 3 必须手动 mask。findings 记结论。

- [ ] **Step 3: 定位 LLM output 空因**

读 `packages/expert-work-runtime/src/expert_work/runtime/middleware/langfuse.py` 的 `_record_response_safe`(:192-208):它在 `"output" in ctx.payload["llm_response"]` 时才 `record_output`。追谁写 `ctx.payload["llm_response"]`(grep `llm_response` 全仓)→ 判定:是没设 `llm_response`、还是设了但无 `output` 键、还是 `_record_response_safe` 没被调。findings 记**具体空因 + 修点(哪个文件哪行让 `llm_response["output"]` = 答案文本)**。

- [ ] **Step 4: 写 findings**

把三项结论写进 `.superpowers/sdd/spike-trace-enrich.md`:(1) 属性 key;(2) OTLP 未 mask 确认;(3) LLM output 空因 + 精确修点。**无 commit**(scratch)。控制器把结论转述给 Task 3/4 的实施者。

---

### Task 3: 写侧 —— orchestrator 给 tool span 补 masked/capped input/output

**依赖 Task 2 的属性 key。**

**Files:**
- Modify: `services/orchestrator/src/orchestrator/graph_builder/builder.py`(:2002-2013 tool span 块 + 新 helper)
- Test: orchestrator 侧对应测试文件(找 `builder` / `_dispatch_tool` / tool span 现有测试;若无专门文件则新建 `services/orchestrator/tests/test_tool_span_io.py`)

**Interfaces:**
- Consumes:`args`(dict,dispatch 处已有 :1997);`outcome[0]`(ToolMessage,`.content`=result,`.status`);`expert_work_span(...)` yield 的 OTel `span`(有 `.set_attribute(key, value)`);`DefaultSecretRedactor` + `DEFAULT_PATTERNS` + `PII_PATTERNS`(`expert_work.runtime.audit.redactor`,builder.py 已依赖 `expert_work.runtime.*`);Task 2 确认的属性 key。
- Produces:新 run 的 tool_call observation 带 masked+capped `input`(args)/`output`(result)。

- [ ] **Step 1: 写失败测试**

新建 `services/orchestrator/tests/test_tool_span_io.py`(或加到现有 tool-dispatch 测)。用一个记录 `set_attribute` 调用的假 span(或 opentelemetry InMemorySpanExporter)断言:

```python
# 意图(具体 harness 对齐 orchestrator 现有 span 测试):
# 1. 派发一个工具(args 含一个 secret 样式串 + result 文本)→ tool_call span 上
#    set_attribute 收到 input(含 args) + output(含 result),二者 secret 已 redact。
# 2. 超长 result → 属性值被 cap(≤ 上限 + 截断标记)。
# 3. set_attribute 抛异常时工具仍正常返回(best-effort 不阻塞)。
```

> 若 orchestrator 已有 span 断言 harness(grep `InMemorySpanExporter` / `expert_work_span` 测试),复用之;否则用最小假 span:`class _RecSpan: def set_attribute(self,k,v): self.calls[k]=v`,并把 helper 抽成可注入 span 的纯函数便于测。

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `cd services/orchestrator && uv run pytest tests/test_tool_span_io.py -v`
Expected: FAIL(span 上无 input/output 属性)。

- [ ] **Step 3: 实现 tool i/o 埋点(builder.py)**

新增 masked+capped 记录 helper(模块级,便于单测):

```python
_TOOL_IO_CAP = 8192
_tool_io_redactor = DefaultSecretRedactor(patterns={**DEFAULT_PATTERNS, **PII_PATTERNS})


def _record_tool_io(span: Any, args: Mapping[str, Any], result: Any) -> None:
    """Best-effort:给 tool_call span 补 masked+capped input/output。

    OTLP 路径不自动过 Langfuse mask(已证),故这里手动 redact。埋点旁路,
    任何异常只丢观测数据,绝不阻塞工具执行。属性 key 来自 spike 确认。
    """
    try:
        masked_in = _tool_io_redactor.redact_tree(dict(args))
        masked_out = _tool_io_redactor.redact_tree(str(result))
        in_text = json.dumps(masked_in, ensure_ascii=False)[:_TOOL_IO_CAP]
        out_text = str(masked_out)[:_TOOL_IO_CAP]
        span.set_attribute(_LANGFUSE_OBS_INPUT_KEY, in_text)   # ← spike 确认的 key
        span.set_attribute(_LANGFUSE_OBS_OUTPUT_KEY, out_text)
    except Exception:  # noqa: BLE001 — 埋点旁路,不阻塞 run
        logger.warning("tool_span_io.record_failed", exc_info=True)
```

`_LANGFUSE_OBS_INPUT_KEY` / `_LANGFUSE_OBS_OUTPUT_KEY` = Task 2 findings 确认的 key(预期 `"langfuse.observation.input"` / `"langfuse.observation.output"`)。顶部补 import:`from expert_work.runtime.audit.redactor import DEFAULT_PATTERNS, PII_PATTERNS, DefaultSecretRedactor`;确认 `json`、`logger`、`Mapping` 已在 builder.py(通常有)。

tool span 块(:2002-2013)绑定 span 并在 outcome 后调用:

```python
        with expert_work_span(
            ExpertWorkComponent.ORCHESTRATOR, "tool_call", attributes={"tool": name}
        ) as span:
            outcome = await _invoke_tool(
                tool, args, call_id, ctx,
                overflow_writer=overflow_writer,
                spotlight_nonce=spotlight_nonce,
                budget_enabled=budget_enabled,
            )
            _record_tool_io(span, args, outcome[0].content)
```

（只加 `as span:` 与块内最后一行 `_record_tool_io(...)`;其余不动。`outcome` 在块内已赋值,`args` 块外已备。）

- [ ] **Step 4: 跑测试确认 PASS**

Run: `cd services/orchestrator && uv run pytest tests/test_tool_span_io.py -v`
Expected: PASS(input/output 属性设置 + redact + cap + best-effort)。

- [ ] **Step 5: mypy + ruff(仓库根,跨包)**

Run: `cd services/orchestrator && uv run mypy src/orchestrator/graph_builder/builder.py && uv run ruff check`
Expected: clean(注意 builder.py 可能有 pre-existing mypy;确认 0 新增)。

- [ ] **Step 6: Commit**

```bash
git add services/orchestrator/src/orchestrator/graph_builder/builder.py services/orchestrator/tests/test_tool_span_io.py
git commit -m "feat(playground): tool_call span 补 masked/capped input/output(trace 显示工具详情)"
```

---

### Task 4: 写侧 —— LLM generation 记录 output(答案)

**依赖 Task 2 的 LLM-output 空因结论。**

**Files:**
- Modify:Task 2 findings 指向的文件(预期 LLM 调用点填 `ctx.payload["llm_response"]["output"]`,让 `langfuse.py:_record_response_safe`(:200)`record_output` 收到答案文本)
- Test:对应中间件/调用点测试

**Interfaces:**
- Consumes:LLM 响应的答案文本(AIMessage content);`_record_response_safe`(`langfuse.py`)已在 `"output" in llm_response` 时 `record_output`。
- Produces:新 run 的 `llm_call` GENERATION 的 `output` = 答案文本(SDK 路径自动 mask)。

- [ ] **Step 1: 写失败测试**

按 Task 2 findings 定位的修点,写断言:LLM 调用后 `ctx.payload["llm_response"]` 含 `"output"` = 答案文本(或直接断言 `record_output` 被以答案调用)。具体 harness 对齐该文件现有测试。

```python
# 意图:走一次 LLM 中间件调用(assistant 答案 "hello") →
# 断言 generation.record_output 收到 "hello"(现状:output 从未被记录 / llm_response 无 output 键)。
```

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `cd <pkg> && uv run pytest <test> -v`(pkg 由 findings 定)
Expected: FAIL(output 未记录)。

- [ ] **Step 3: 实现(按 findings 精确修点)**

让 LLM 调用点把答案写进 `ctx.payload["llm_response"]["output"]`(或等价路径),使 `_record_response_safe` 记录。**最小改动**,不重构中间件管线。若 findings 显示空因是更深的管线问题(disproportionate),标 DONE_WITH_CONCERNS 并上报控制器决定是否降级本任务(LLM output 为 nice-to-have,非用户两个原始诉求)。

- [ ] **Step 4: 跑测试确认 PASS**

Run: `cd <pkg> && uv run pytest <test> -v`
Expected: PASS。

- [ ] **Step 5: mypy + ruff**

Run: `cd <pkg> && uv run mypy <file> && uv run ruff check`
Expected: clean。

- [ ] **Step 6: Commit**

```bash
git add <files>
git commit -m "feat(playground): LLM generation 记录 output(trace 显示答案)"
```

---

## 收尾

全部任务后:
- 后端 `uv run pytest`(control-plane + orchestrator + runtime 受影响测)+ `uv run mypy` + `uv run ruff check`。
- 终门 opus 全支评审;再走 finishing-a-development-branch。
- **人工冒烟(需 dev server + 新 run)**:发一个带工具调用的新 run → trace 精确视图 → 确认 LLM prompt 可读多行不早截断、工具节点详情显示 masked args/result、LLM 节点显示答案。历史 run 确认无回归(工具详情仍空=预期)。
