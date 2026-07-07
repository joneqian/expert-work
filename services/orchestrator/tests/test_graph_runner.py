"""Tests for the SE-4c real-graph TaskRunner adapter.

The end-to-end with a real LLM is exercised by the eval harness / SE-9
benchmark (CI integration has no model keys). Here we unit-test the wiring:
spec variant construction, build-once caching, graph invocation, and answer
extraction — with a fake agent builder + fake graph.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from expert_work.protocol import AgentSpec
from orchestrator.agent_factory import BuiltAgent
from orchestrator.evolution.graph_runner import (
    GraphReplayTaskRunner,
    with_candidate_skill,
    without_candidate_skill,
)

_MINIMAL_SPEC: dict[str, Any] = {
    "apiVersion": "expert_work.io/v1",
    "kind": "Agent",
    "metadata": {"name": "test", "version": "1.0.0", "tenant": "test-tenant"},
    "spec": {
        "tenant_config": {},
        "model": {
            "provider": "anthropic",
            "name": "claude-sonnet-4-6",
            "api_key_ref": "secret://anthropic-key",
        },
        "system_prompt": {"template": "you are an agent"},
        "sandbox": {
            "resources": {"cpu": "1.0", "memory": "1Gi"},
            "network": {"egress": "proxy", "allowlist": ["api.anthropic.com"]},
            "filesystem": {"readonly_root": True, "writable": ["/workspace"]},
        },
    },
}


def _spec(skills: list[str]) -> AgentSpec:
    doc = deepcopy(_MINIMAL_SPEC)
    doc["spec"]["skills"] = skills
    return AgentSpec.model_validate(doc)


# --------------------------------------------------------------------------- #
# Pure spec-variant helpers
# --------------------------------------------------------------------------- #


def test_without_candidate_skill_removes_named_ref() -> None:
    out = without_candidate_skill(_spec(["foo", "bar@2"]), skill_name="foo")
    assert out.spec.skills == ["bar@2"]


def test_with_candidate_skill_pins_version_and_keeps_others() -> None:
    out = with_candidate_skill(_spec(["bar@2"]), skill_name="foo", skill_version=3)
    assert "foo@3" in out.spec.skills
    assert "bar@2" in out.spec.skills


def test_with_candidate_skill_replaces_existing_ref_no_dup() -> None:
    out = with_candidate_skill(_spec(["foo"]), skill_name="foo", skill_version=5)
    assert out.spec.skills == ["foo@5"]


def test_helpers_do_not_mutate_input() -> None:
    base = _spec(["foo"])
    without_candidate_skill(base, skill_name="foo")
    with_candidate_skill(base, skill_name="foo", skill_version=2)
    assert base.spec.skills == ["foo"]


# --------------------------------------------------------------------------- #
# Runner wiring
# --------------------------------------------------------------------------- #


class FakeGraph:
    def __init__(self, answer: str, *, emit_ai: bool = True) -> None:
        self.answer = answer
        self.emit_ai = emit_ai
        self.calls: list[Any] = []

    async def ainvoke(self, inp: Any, config: Any) -> dict[str, Any]:
        self.calls.append((inp, config))
        msgs: list[Any] = [HumanMessage(content="q")]
        if self.emit_ai:
            msgs.append(AIMessage(content=self.answer))
        return {"messages": msgs, "step_count": 1}


def _built(answer: str, *, emit_ai: bool = True) -> BuiltAgent:
    return BuiltAgent(
        graph=FakeGraph(answer, emit_ai=emit_ai),  # type: ignore[arg-type]
        system_prompt="sys",
        max_steps=5,
    )


class FakeBuilder:
    """Returns GOOD if the spec carries the candidate skill, else BAD."""

    def __init__(self) -> None:
        self.count = 0

    async def __call__(self, spec: AgentSpec) -> BuiltAgent:
        self.count += 1
        has = any(ref.split("@", 1)[0] == "cand" for ref in spec.spec.skills)
        return _built("GOOD" if has else "BAD")


def _config(case_id: str, with_skill: bool) -> Any:
    return {"configurable": {"thread_id": f"{case_id}-{with_skill}"}}


def _runner(builder: Any) -> GraphReplayTaskRunner:
    return GraphReplayTaskRunner.from_candidate(
        _spec([]),
        skill_name="cand",
        skill_version=1,
        agent_builder=builder,
        config_factory=_config,
    )


async def test_runner_picks_variant_and_extracts_answer() -> None:
    runner = _runner(FakeBuilder())
    baseline = await runner.run(case_id="c0", prompt="do it", with_skill=False)
    treatment = await runner.run(case_id="c0", prompt="do it", with_skill=True)
    assert baseline == "BAD"
    assert treatment == "GOOD"


async def test_runner_builds_each_variant_once() -> None:
    builder = FakeBuilder()
    runner = _runner(builder)
    for i in range(3):
        await runner.run(case_id=f"c{i}", prompt="x", with_skill=False)
        await runner.run(case_id=f"c{i}", prompt="x", with_skill=True)
    assert builder.count == 2  # one baseline build + one treatment build, cached after


async def test_runner_empty_when_no_ai_message() -> None:
    async def builder(spec: AgentSpec) -> BuiltAgent:
        return _built("ignored", emit_ai=False)

    runner = _runner(builder)
    answer = await runner.run(case_id="c0", prompt="x", with_skill=True)
    assert answer == ""
