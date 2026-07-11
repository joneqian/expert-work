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
