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
    assert llm[0]["input"].startswith("You are a memory")
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


def test_normalize_caps_oversized_io() -> None:
    big = "x" * 20000
    obs = [
        _obs("sess", "SPAN", "expert_work.session.run", None, 1.0, 0),
        _obs("g", "GENERATION", "llm_call", "sess", 1.0, 0, input=big),
    ]
    spans = normalize_trace(_trace(obs), io_cap=100)["spans"]
    g = next(s for s in spans if s["id"] == "g")
    assert len(g["input"]) <= 130 and "截断" in g["input"]


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
    # 精确断言:区分正确的 text 提取与原样 json.dumps 转储(后者会含 'type'/'{')。
    assert g["input"] == "[user]\nblock one"


def test_normalize_io_cap_default_raised_to_32768() -> None:
    """默认 io_cap 放宽到 32768:20000 字符的 input 不再截断。"""
    big = "x" * 20000
    obs = [
        _obs("sess", "SPAN", "expert_work.session.run", None, 1.0, 0),
        _obs("g", "GENERATION", "llm_call", "sess", 1.0, 0, input=big),
    ]
    g = next(s for s in normalize_trace(_trace(obs))["spans"] if s["id"] == "g")
    assert "截断" not in g["input"] and len(g["input"]) == 20000


def test_normalize_io_cap_boundary_at_32768() -> None:
    """Exactly 32768 chars pass; 32769 truncate — pins the default at 32768."""
    for n, truncated in ((32768, False), (32769, True)):
        obs = [
            _obs("sess", "SPAN", "expert_work.session.run", None, 1.0, 0),
            _obs("g", "GENERATION", "llm_call", "sess", 1.0, 0, input="z" * n),
        ]
        g = next(s for s in normalize_trace(_trace(obs))["spans"] if s["id"] == "g")
        assert ("截断" in g["input"]) is truncated


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
