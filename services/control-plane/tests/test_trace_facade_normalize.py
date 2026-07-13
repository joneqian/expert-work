"""Unit tests for ``trace_facade.normalize_trace`` — Batch 4b Task 1 (spec §2.3).

``normalize_trace`` is a pure function: no IO, no network. These tests build
lightweight ``SimpleNamespace`` stubs standing in for Langfuse's
``TraceWithFullDetails`` / ``ObservationsView`` shapes and assert on the
normalized DTO the debug console's trace view consumes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from control_plane.api.trace_facade import normalize_trace


def _obs(
    id: str,
    type_: str,
    name: str,
    parent: str | None,
    lat: float,
    start_s: int,
    **kw: Any,
) -> SimpleNamespace:
    base = {
        "id": id,
        "type": type_,
        "name": name,
        "parent_observation_id": parent,
        "latency": lat,
        "start_time": datetime(2026, 1, 1, 0, 0, start_s, tzinfo=UTC),
        "model": None,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "calculated_total_cost": 0.0,
        "input": None,
        "output": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _trace(obs: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(
        name="expert_work.control_plane.http_request",
        latency=33.5,
        total_cost=0.0,
        observations=obs,
    )


def test_normalize_merges_wrapper_and_generation_and_humanizes() -> None:
    obs = [
        _obs("root", "SPAN", "expert_work.control_plane.http_request", None, 33.8, 0),
        _obs("sess", "SPAN", "expert_work.session.run", "root", 33.5, 0),
        _obs("llmspan", "SPAN", "expert_work.orchestrator.llm_call", "sess", 8.2, 1),
        _obs(
            "gen",
            "GENERATION",
            "llm_call",
            "llmspan",
            8.8,
            1,
            model="glm-4.6",
            input="You are a memory extraction module...",
            output='{"memories":[]}',
            calculated_total_cost=0.0021,
        ),
        _obs("toolspan", "SPAN", "expert_work.orchestrator.tool_call", "sess", 0.16, 28),
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
    assert llm[0]["input"]["kind"] == "text"
    assert llm[0]["input"]["text"].startswith("You are a memory")
    assert llm[0]["latencyMs"] > 0
    # tool humanized
    tool = [s for s in spans if s["kind"] == "tool"]
    assert tool and tool[0]["label"] == "工具调用"
    # every span's parentId resolves within the set or is None (tree connected)
    ids = {s["id"] for s in spans}
    assert all(s["parentId"] is None or s["parentId"] in ids for s in spans)


def test_normalize_unmapped_span_falls_back_to_cleaned_name() -> None:
    obs = [
        _obs("sess", "SPAN", "expert_work.session.run", None, 1.0, 0),
        _obs("x", "SPAN", "expert_work.orchestrator.planner", "sess", 0.5, 0),
    ]
    spans = normalize_trace(_trace(obs))["spans"]
    planner = next(s for s in spans if s["id"] == "x")
    assert planner["kind"] == "span"
    assert planner["label"] == "orchestrator.planner"  # expert_work. 前缀去掉


def test_normalize_handles_none_latency_without_raising() -> None:
    """Real Langfuse SDK returns ``latency=None`` while aggregation is still
    in flight (trace just landed, hasn't closed out yet). ``normalize_trace``
    must never raise on this — ``round(None * 1000)`` would otherwise be an
    uncaught ``TypeError`` (硬约束「降级永不 500」). latency_ms falls back
    to 0 both at the trace level and per-observation.
    """
    obs = [
        _obs("sess", "SPAN", "expert_work.session.run", None, None, 0),
    ]
    trace = SimpleNamespace(
        name="expert_work.control_plane.http_request",
        latency=None,
        total_cost=0.0,
        observations=obs,
    )
    out = normalize_trace(trace)
    assert out["status"] == "ok"
    assert out["trace"]["latencyMs"] == 0
    span = out["spans"][0]
    assert span["latencyMs"] == 0


def test_normalize_caps_oversized_text_io_at_text_cap() -> None:
    """纯字符串 input(text kind)超过 _TEXT_CAP → 截断,fullChars 记原长。"""
    from control_plane.api.trace_facade import _TEXT_CAP

    big = "x" * (_TEXT_CAP + 500)
    obs = [
        _obs("sess", "SPAN", "expert_work.session.run", None, 1.0, 0),
        _obs("g", "GENERATION", "llm_call", "sess", 1.0, 0, input=big),
    ]
    spans = normalize_trace(_trace(obs))["spans"]
    g = next(s for s in spans if s["id"] == "g")
    assert g["input"]["kind"] == "text"
    assert g["input"]["truncated"] is True
    assert g["input"]["fullChars"] == _TEXT_CAP + 500
    assert len(g["input"]["text"]) <= _TEXT_CAP + len("…(已截断)")


def test_normalize_surfaces_cost_and_tokens_best_effort() -> None:
    """cost/tokens carry through when Langfuse populated them, else stay None."""
    obs = [
        _obs("sess", "SPAN", "expert_work.session.run", None, 1.0, 0),
        _obs(
            "gpop",
            "GENERATION",
            "llm_call",
            "sess",
            1.0,
            0,
            model="glm-4.6",
            prompt_tokens=100,
            completion_tokens=20,
            calculated_total_cost=0.0021,
        ),
        _obs("gempty", "GENERATION", "llm_call", "sess", 1.0, 1),  # tokens 0 / cost 0.0
    ]
    spans = normalize_trace(_trace(obs))["spans"]
    pop = next(s for s in spans if s["id"] == "gpop")
    assert pop["costUsd"] == 0.0021
    assert pop["inputTokens"] == 100
    assert pop["outputTokens"] == 20
    assert pop["model"] == "glm-4.6"
    empty = next(s for s in spans if s["id"] == "gempty")
    assert empty["costUsd"] is None  # 0.0 → None (best-effort, not "free")
    assert empty["inputTokens"] is None and empty["outputTokens"] is None
    assert empty["model"] is None


def test_normalize_elides_only_root_http_request_not_named_children() -> None:
    """Follow-up: the http_request elision is root-only (parent_id is None).

    A non-root span whose name merely contains ``.http_request`` is KEPT.
    """
    obs = [
        _obs("root", "SPAN", "expert_work.control_plane.http_request", None, 5.0, 0),
        _obs("sess", "SPAN", "expert_work.session.run", "root", 5.0, 0),
        # a legitimately-named non-root span containing the substring:
        _obs("child", "SPAN", "expert_work.tool.retry_http_request", "sess", 0.3, 1),
    ]
    spans = normalize_trace(_trace(obs))["spans"]
    ids = {s["id"] for s in spans}
    assert "root" not in ids  # root http_request elided
    assert "child" in ids  # non-root .http_request-named span kept
    child = next(s for s in spans if s["id"] == "child")
    assert child["parentId"] == "sess"  # still parented correctly


def test_normalize_renders_chat_messages_input_as_readable_text() -> None:
    """LLM input 是消息 list → 渲染成结构化 messages,content 保留真换行、role 可辨。"""
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
    rendered = g["input"]
    assert rendered["kind"] == "messages"
    msgs = rendered["messages"]
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == "You are helpful.\n\n# Rules\nBe terse."
    assert msgs[1]["role"] == "user"
    assert msgs[1]["content"] == "hi"


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
    # 精确断言:区分正确的 text 提取与原样 json.dumps 转储(后者会含 'type'/'{')。
    assert g["input"] == {
        "kind": "messages",
        "messages": [
            {
                "role": "user",
                "content": "block one",
                "truncated": False,
                "fullChars": len("block one"),
                "toolCalls": None,
            }
        ],
    }


def test_normalize_text_io_not_truncated_at_text_cap() -> None:
    """纯字符串 input(text kind)长度恰为 _TEXT_CAP → 不截断。"""
    from control_plane.api.trace_facade import _TEXT_CAP

    big = "x" * _TEXT_CAP
    obs = [
        _obs("sess", "SPAN", "expert_work.session.run", None, 1.0, 0),
        _obs("g", "GENERATION", "llm_call", "sess", 1.0, 0, input=big),
    ]
    g = next(s for s in normalize_trace(_trace(obs))["spans"] if s["id"] == "g")
    assert g["input"]["truncated"] is False
    assert g["input"]["fullChars"] == _TEXT_CAP


def test_normalize_text_io_boundary_at_text_cap() -> None:
    """Exactly _TEXT_CAP chars pass; _TEXT_CAP+1 truncate — pins the text-kind cap."""
    from control_plane.api.trace_facade import _TEXT_CAP

    for n, truncated in ((_TEXT_CAP, False), (_TEXT_CAP + 1, True)):
        obs = [
            _obs("sess", "SPAN", "expert_work.session.run", None, 1.0, 0),
            _obs("g", "GENERATION", "llm_call", "sess", 1.0, 0, input="z" * n),
        ]
        g = next(s for s in normalize_trace(_trace(obs))["spans"] if s["id"] == "g")
        assert g["input"]["truncated"] is truncated


def test_normalize_extracts_level_and_status_message() -> None:
    obs = SimpleNamespace(
        id="o1",
        type="GENERATION",
        name="llm_call",
        parent_observation_id=None,
        start_time=None,
        latency=1.0,
        model="glm-4.6",
        input=None,
        output=None,
        level="ERROR",
        status_message="SandboxTimeout",
    )
    trace = SimpleNamespace(name="t", latency=1.0, total_cost=None, observations=[obs])
    out = normalize_trace(trace)
    span = out["spans"][0]
    assert span["level"] == "error"
    assert span["statusMessage"] == "SandboxTimeout"


def test_normalize_defaults_level_when_absent() -> None:
    obs = SimpleNamespace(
        id="o1",
        type="SPAN",
        name="expert_work.session.run",
        parent_observation_id=None,
        start_time=None,
        latency=1.0,
        input=None,
        output=None,
    )
    trace = SimpleNamespace(name="t", latency=1.0, total_cost=None, observations=[obs])
    span = normalize_trace(trace)["spans"][0]
    assert span["level"] == "default"
    assert span["statusMessage"] is None


# ---------------------------------------------------------------------------
# _render_io — structured i/o (Task 3, spec §A1)
# ---------------------------------------------------------------------------


def test_render_io_messages_role_from_type_and_toolcalls() -> None:
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


def test_render_io_message_per_message_truncation() -> None:
    from control_plane.api.trace_facade import _MSG_CAP, _render_io

    big = "x" * (_MSG_CAP + 10)
    out = _render_io([{"type": "system", "content": big}])
    m = out["messages"][0]
    assert m["truncated"] is True
    assert m["fullChars"] == _MSG_CAP + 10
    assert m["content"].endswith("…(已截断)")
    assert len(m["content"]) <= _MSG_CAP + len("…(已截断)")


def test_render_io_mixed_list_only_oversized_truncated_tail_preserved() -> None:
    """尾部保留(本任务命门):一条超长 system + 正常 human/ai/tool 兄弟,
    只有超长那条截断,后面的对话消息全量保留、原样。防 flatten-then-cut 回归。"""
    from control_plane.api.trace_facade import _MSG_CAP, _render_io

    big = "s" * (_MSG_CAP + 5)
    out = _render_io(
        [
            {"type": "system", "content": big},
            {"type": "human", "content": "现在几号"},
            {"type": "ai", "content": "让我查一下"},
            {"type": "tool", "content": "2026-07-13"},
        ]
    )
    msgs = out["messages"]
    assert [m["role"] for m in msgs] == ["system", "human", "ai", "tool"]
    assert msgs[0]["truncated"] is True
    # 后三条对话消息全量保留、未截断、原样。
    assert [m["truncated"] for m in msgs[1:]] == [False, False, False]
    assert msgs[1]["content"] == "现在几号"
    assert msgs[2]["content"] == "让我查一下"
    assert msgs[3]["content"] == "2026-07-13"


def test_render_io_text_kind_for_tool_args() -> None:
    from control_plane.api.trace_facade import _render_io

    out = _render_io({"code": "print(1)"})
    assert out["kind"] == "text"
    assert '"code"' in out["text"]
    assert out["truncated"] is False


def test_render_io_block_list_content() -> None:
    from control_plane.api.trace_facade import _render_io

    out = _render_io([{"type": "human", "content": [{"type": "text", "text": "hi"}]}])
    assert out["messages"][0]["content"] == "hi"


def test_render_io_none() -> None:
    from control_plane.api.trace_facade import _render_io

    assert _render_io(None) is None
