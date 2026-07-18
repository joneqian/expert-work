# glm `tool_stream` 修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 glm 的**流式**请求注入 `tool_stream: true`,让 glm 增量吐 tool-call 参数,消除组装大 tool-call 时的静默 gap(修 `stream_idle_timeout` 误砍)。

**Architecture:** 方案 A——`OpenAIProvider` 加 stream-only 字段 `stream_extra_body`,只在 `stream()` 里 merge 进 `extra_body`;`complete()`/`_prepare_request` 不动(glm 结构化输出走 `complete()`,`tool_stream` 绝不能漏进去)。`agent_factory._build_provider` 对 `provider == "glm"` 传 `stream_extra_body={"tool_stream": True}`。parser 零改(`delta_from_openai_chunk` 已接 `args_fragment`)。

**Tech Stack:** Python(orchestrator,pytest/asyncio),OpenAI-wire httpx provider。

## Global Constraints

- **stream-only**:`tool_stream` 只进 `stream()` 请求,绝不进 `complete()`(glm 非流式/结构化输出可能拒它)。
- **glm-only**:只 `provider == "glm"` 注入;deepseek/qwen/kimi/doubao 实测默认增量(不发);openai/azure/anthropic 不发。
- **byte-identical 回归**:`stream_extra_body` 默认 `None` → 对所有未触及 provider 的请求逐字节不变。
- **parser/assembler 不改**:`tool_stream` 只改 args 分片粒度,不改最终内容;流式重组 byte-equal 不变式不受影响。
- **测试命令**:`cd services/orchestrator && uv run python -m pytest`(裸 `python` 挑不动编译扩展);提交前 `uv run ruff check <files>` + `ruff format --check <files>`(CI ruff 全库含 tests)。
- **surgical**:只改 `openai.py`(字段+helper+stream merge)、`agent_factory.py`(glm gating 一处)、两个测试文件。不碰 parser/assembler/complete/anthropic。

---

## 文件结构

- `services/orchestrator/src/orchestrator/llm/providers/openai.py` — `OpenAIProvider` 加 `stream_extra_body` 字段 + `_with_stream_extra_body` helper + `stream()` 两处 merge。
- `services/orchestrator/tests/test_llm_provider_openai_stream.py` — 加 stream-only 注入测。
- `services/orchestrator/src/orchestrator/agent_factory.py` — `_build_provider` openai_compatible 分支 glm gating。
- `services/orchestrator/tests/test_agent_factory.py` — 加 glm/非-glm gating 测。

**任务顺序**:Task 1(provider 机制)→ Task 2(agent_factory gating)。Task 2 消费 Task 1 的 `stream_extra_body` 字段。

---

### Task 1: `OpenAIProvider.stream_extra_body`(stream-only 注入)

**Files:**
- Modify: `services/orchestrator/src/orchestrator/llm/providers/openai.py`(`OpenAIProvider` 类:字段 ~465 后、helper、`stream()` ~543-570)
- Test: `services/orchestrator/tests/test_llm_provider_openai_stream.py`(追加)

**Interfaces:**
- Consumes: `RecordingOpenAIClient`(记 `calls[].extra_body`)、`OpenAIProvider`、`ToolSpec`、`_streaming.OpenAIStreamAssembler`(测已导入)。
- Produces: `OpenAIProvider.stream_extra_body: dict[str, Any] | None = None`(dataclass 字段;`OpenAICompatibleProvider` 继承)。语义:非 None 时,仅 `stream()` 请求的 `extra_body` 会并入它。Task 2 靠此字段 gate glm。

- [ ] **Step 1: 写失败测试**(追加到 `test_llm_provider_openai_stream.py` 末尾)

```python
async def _drain(provider: OpenAIProvider, tools: list[ToolSpec] | None = None) -> None:
    async for _ in provider.stream(messages=[HumanMessage(content="hi")], tools=tools or []):
        pass


@pytest.mark.asyncio
async def test_stream_extra_body_merged_into_stream_request() -> None:
    client = RecordingOpenAIClient(stream_chunks=_text_chunks())
    provider = OpenAIProvider(
        client=client, model="glm-5.2", stream_extra_body={"tool_stream": True}
    )
    await _drain(provider)
    assert client.calls[-1]["extra_body"] == {"tool_stream": True}


@pytest.mark.asyncio
async def test_stream_extra_body_not_on_complete_request() -> None:
    # 命门:tool_stream is stream-only — glm's structured-output complete()
    # path must NOT carry it.
    client = RecordingOpenAIClient(response={"choices": [{"message": {"content": "ok"}}]})
    provider = OpenAIProvider(
        client=client, model="glm-5.2", stream_extra_body={"tool_stream": True}
    )
    await provider.complete(messages=[HumanMessage(content="hi")], tools=[])
    assert client.calls[-1]["extra_body"] is None


@pytest.mark.asyncio
async def test_stream_extra_body_none_leaves_request_byte_identical() -> None:
    client = RecordingOpenAIClient(stream_chunks=_text_chunks())
    provider = OpenAIProvider(client=client, model="glm-5.2")  # default None
    await _drain(provider)
    assert client.calls[-1]["extra_body"] is None


@pytest.mark.asyncio
async def test_stream_extra_body_merges_with_thinking_payload() -> None:
    client = RecordingOpenAIClient(stream_chunks=_text_chunks())
    provider = OpenAIProvider(
        client=client,
        model="glm-5.2",
        thinking_payload={"thinking": {"type": "enabled"}},
        stream_extra_body={"tool_stream": True},
    )
    await _drain(provider)
    assert client.calls[-1]["extra_body"] == {
        "thinking": {"type": "enabled"},
        "tool_stream": True,
    }


@pytest.mark.asyncio
async def test_stream_extra_body_applied_on_allowed_tools_retry() -> None:
    # allowed_tools rejection → fall back + re-stream once; the retry request
    # must ALSO carry stream_extra_body.
    @dataclass
    class _RejectConstraintStream:
        calls: list[dict[str, Any]] = field(default_factory=list)

        async def stream_chat_completions(self, **kwargs: Any) -> AsyncIterator[Mapping[str, Any]]:
            self.calls.append(kwargs)
            if kwargs.get("tool_choice") is not None:  # allowed_tools constraint
                raise LLMClientError("openai 400: unknown tool_choice type")
            for chunk in _text_chunks():
                yield chunk

    client = _RejectConstraintStream()
    provider = OpenAIProvider(
        client=client, model="glm-5.2", stream_extra_body={"tool_stream": True}
    )
    # a defer_loading tool makes use_allowed=True → constrained first attempt
    tool = ToolSpec(name="mcp:t", description="d", defer_loading=True)
    await _drain(provider, tools=[tool])
    assert len(client.calls) == 2  # constrained (rejected) + unconstrained retry
    assert client.calls[0]["extra_body"] == {"tool_stream": True}  # main stream path
    assert client.calls[1]["extra_body"] == {"tool_stream": True}  # retry path
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd services/orchestrator && uv run python -m pytest tests/test_llm_provider_openai_stream.py -k "stream_extra_body" -v`
Expected: FAIL —— `OpenAIProvider` 无 `stream_extra_body` 参数(TypeError / unexpected keyword argument)。

- [ ] **Step 3: 加字段 + helper**（`openai.py`,`OpenAIProvider` 类内)

在 `thinking_payload: dict[str, Any] | None = None`(~465)之后、`_allowed_tools_disabled`(~469)之前插入字段:

```python
    #: glm-only (2026-07-18) — vendor request params merged into ``extra_body``
    #: ONLY on the streaming path. glm's ``tool_stream: true`` makes it emit
    #: tool-call args incrementally instead of one silent batch, so the router's
    #: idle timer resets per fragment (else a large tool-call composes in
    #: silence past ``idle_timeout_s`` → spurious ``stream_idle_timeout``). Kept
    #: OFF the non-streaming ``complete`` path (structured output) where glm may
    #: reject it. ``None`` (every other agent/provider) → request byte-identical.
    stream_extra_body: dict[str, Any] | None = None
```

在 `_prepare_request` 方法之后(~516,`complete` 之前)加 helper:

```python
    def _apply_stream_extra_body(self, request: dict[str, Any]) -> None:
        """Merge :attr:`stream_extra_body` into a prepared request's
        ``extra_body`` (stream path only; a no-op when ``None``)."""
        if self.stream_extra_body is not None:
            request["extra_body"] = {**(request["extra_body"] or {}), **self.stream_extra_body}
```

- [ ] **Step 4: 在 `stream()` 两处 merge**

`stream()` 里,两个 `_prepare_request(...)` 调用之后各加一行 `self._apply_stream_extra_body(...)`。改后 `stream()` 长这样(完整替换现有 `stream` 方法体):

```python
    async def stream(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
        output_schema: StructuredOutputSpec | None = None,
    ) -> AsyncIterator[LLMDelta]:
        use_allowed = any(spec.defer_loading for spec in tools) and not self._allowed_tools_disabled
        request = await self._prepare_request(
            messages=messages, tools=tools, output_schema=output_schema, use_allowed=use_allowed
        )
        self._apply_stream_extra_body(request)
        try:
            async for chunk in self.client.stream_chat_completions(**request):
                yield delta_from_openai_chunk(chunk)
            return
        except LLMClientError:
            if not use_allowed:
                raise
            # HX-13 (Mini-ADR HX-J4) — allowed_tools rejected pre-stream.
            # Fail open: drop to the application tier and re-stream once.
            self._allowed_tools_disabled = True
            disclosure_fallback_total.labels(provider="openai").inc()
            logger.warning("openai.allowed_tools_rejected — falling back to app tier")
        retry = await self._prepare_request(
            messages=messages, tools=tools, output_schema=output_schema, use_allowed=False
        )
        self._apply_stream_extra_body(retry)
        async for chunk in self.client.stream_chat_completions(**retry):
            yield delta_from_openai_chunk(chunk)
```

（`complete()` 与 `_prepare_request` **不改** → `tool_stream` 永不进非流式路径。）

- [ ] **Step 5: 跑测试验证通过（含现有回归）**

Run: `cd services/orchestrator && uv run python -m pytest tests/test_llm_provider_openai_stream.py tests/test_llm_provider_openai.py tests/test_llm_provider_openai_compatible.py -v`
Expected: PASS 全部——新 5 测 + 现有 stream/complete/byte-equal 回归全绿(默认 None → 现有测请求不变)。

- [ ] **Step 6: lint + commit**

```bash
cd services/orchestrator && uv run ruff check src/orchestrator/llm/providers/openai.py tests/test_llm_provider_openai_stream.py && uv run ruff format --check src/orchestrator/llm/providers/openai.py tests/test_llm_provider_openai_stream.py
cd /Users/mac/src/github/jone_qian/expert-work
git add services/orchestrator/src/orchestrator/llm/providers/openai.py services/orchestrator/tests/test_llm_provider_openai_stream.py
git commit -m "feat(orchestrator): OpenAIProvider stream-only extra_body 注入(glm tool_stream 前置)"
```

---

### Task 2: agent_factory glm gating

**Files:**
- Modify: `services/orchestrator/src/orchestrator/agent_factory.py:2168-2175`（openai_compatible 分支）
- Test: `services/orchestrator/tests/test_agent_factory.py`(追加)

**Interfaces:**
- Consumes: `OpenAIProvider.stream_extra_body`(Task 1);`_build_provider(model, api_key)`、`_vendor_model(provider, name, **overrides)`、`OpenAICompatibleProvider`(测已有 helper / 需导入)。
- Produces: `_build_provider` 对 `provider == "glm"` 返回的 `OpenAICompatibleProvider.stream_extra_body == {"tool_stream": True}`;其余 compat vendor `is None`。

- [ ] **Step 1: 写失败测试**(追加到 `test_agent_factory.py`,靠近现有 `_build_provider` compat 测,约 1048)

```python
def test_glm_gets_tool_stream_stream_extra_body() -> None:
    # glm batches tool-call args by default; tool_stream=True makes it stream
    # them incrementally so the router's idle timer resets per fragment.
    glm = _build_provider(_vendor_model("glm", "glm-5.2"), "k")
    assert isinstance(glm, OpenAICompatibleProvider)
    assert glm.stream_extra_body == {"tool_stream": True}


def test_non_glm_compat_has_no_stream_extra_body() -> None:
    # deepseek/qwen/kimi/doubao stream tool-args incrementally by default.
    ds = _build_provider(_vendor_model("deepseek", "deepseek-v4-pro"), "k")
    assert isinstance(ds, OpenAICompatibleProvider)
    assert ds.stream_extra_body is None
```

若 `OpenAICompatibleProvider` 未在测试文件导入,在 import 段加:
```python
from orchestrator.llm.providers.openai_compatible import OpenAICompatibleProvider
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd services/orchestrator && uv run python -m pytest tests/test_agent_factory.py -k "stream_extra_body" -v`
Expected: FAIL —— glm 的 `stream_extra_body` 现为 None(gating 未加)。

- [ ] **Step 3: 加 glm gating**（`agent_factory.py`,openai_compatible 分支 2168-2175）

把:
```python
    make_client = openai_compatible.get(provider)
    if make_client is not None:
        return OpenAICompatibleProvider(
            client=make_client(api_key=api_key, timeout_s=timeout_eff),
            model=model.name,
            temperature=model.temperature,
            image_resolver=image_resolver,
            thinking_payload=thinking_payload,
        )
```
改为:
```python
    make_client = openai_compatible.get(provider)
    if make_client is not None:
        # glm batches tool-call args by default (composes the whole arg in
        # silence past idle_timeout_s → spurious stream_idle_timeout). Its
        # ``tool_stream: true`` param streams them incrementally; stream-only
        # (glm's structured-output complete() may reject it). Other compat
        # vendors stream tool-args incrementally already — no injection.
        stream_extra_body = {"tool_stream": True} if provider == "glm" else None
        return OpenAICompatibleProvider(
            client=make_client(api_key=api_key, timeout_s=timeout_eff),
            model=model.name,
            temperature=model.temperature,
            image_resolver=image_resolver,
            thinking_payload=thinking_payload,
            stream_extra_body=stream_extra_body,
        )
```

（self-hosted 分支 2180 非 glm,`stream_extra_body` 保持默认 None,不动。）

- [ ] **Step 4: 跑测试验证通过**

Run: `cd services/orchestrator && uv run python -m pytest tests/test_agent_factory.py -v`
Expected: PASS 全部——新 2 测 + 现有 factory 测无回归。

- [ ] **Step 5: lint + commit**

```bash
cd services/orchestrator && uv run ruff check src/orchestrator/agent_factory.py tests/test_agent_factory.py && uv run ruff format --check src/orchestrator/agent_factory.py tests/test_agent_factory.py
cd /Users/mac/src/github/jone_qian/expert-work
git add services/orchestrator/src/orchestrator/agent_factory.py services/orchestrator/tests/test_agent_factory.py
git commit -m "fix(orchestrator): glm 流式请求注入 tool_stream(修 stream_idle_timeout 误砍大 tool-call)"
```

---

## 最终验证(全任务后)

- `cd services/orchestrator && uv run python -m pytest tests/test_llm_provider_openai_stream.py tests/test_llm_provider_openai.py tests/test_llm_provider_openai_compatible.py tests/test_agent_factory.py tests/test_llm_router_streaming.py -v` — provider/factory/router-stream 全绿。
- `cd services/orchestrator && uv run ruff check . && uv run ruff format --check .` — 干净。
- 手动冒烟(可选,需真 glm key):真跑 health-manger-agent 那类"最后一步生成大 PPT tool-call"的任务 → 不再 `stream_idle_timeout`,tool-call 增量流式、正常完成。(已由裸 probe 实证:165s→4.1s。)

## 自审记录

- **Spec 覆盖**:stream-only 注入=Task 1(字段+helper+stream merge,complete 不动);glm-only gating=Task 2;parser 零改(不在计划=不动);byte-identical=Task 1 的 None 默认测;测试(stream 有/complete 无/None 无/thinking 共存/retry 路径 + factory gating)全覆盖 spec 测试节。
- **类型一致**:`stream_extra_body: dict[str,Any]|None` 在 Task 1 定义、Task 2 传入,一致;`_apply_stream_extra_body` Task 1 定义+调用。
- **无占位**:每步含完整代码/命令/预期。
