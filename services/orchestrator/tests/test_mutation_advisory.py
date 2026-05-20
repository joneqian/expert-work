"""Stream L.L4 — file-mutation advisory footer integration tests.

Drives the full ``tools_node`` → ``AgentState.failed_mutations`` →
``agent_node`` round-trip through the compiled graph: a failing
``save_artifact`` call surfaces a ``<mutation-advisory>``
HumanMessage on the next agent step, the advisory persists in
conversation history, the channel resets so a second turn doesn't
re-inject, and (Mini-ADR L-4 invariant) the advisory never lands in
a ``SystemMessage``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig

from helix_agent.runtime.checkpointer import make_checkpointer
from orchestrator import (
    AgentState,
    GraphRunner,
    ToolContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    build_react_graph,
)

# ---------------------------------------------------------------------------
# Test stubs
# ---------------------------------------------------------------------------


@dataclass
class _ScriptedLLM:
    responses: list[AIMessage]
    seen_prompts: list[list[BaseMessage]]
    calls: int = 0

    async def __call__(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
    ) -> AIMessage:
        del tools
        self.seen_prompts.append(list(messages))
        idx = self.calls
        self.calls += 1
        if idx >= len(self.responses):
            msg = f"scripted LLM ran out at call {idx}"
            raise RuntimeError(msg)
        return self.responses[idx]


@dataclass
class _ScriptedSaveArtifact:
    """``save_artifact`` stub. ``fail`` controls whether the dispatch
    raises (→ ToolMessage(status="error") via the builder's error
    wrapper) or returns success."""

    name: str = "save_artifact"
    fail: bool = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="scripted save_artifact",
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            # L6 path_args lets multiple saves to different paths
            # parallelise; not exercised by L4 tests but kept for parity.
            path_args=("name",),
        )

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        del ctx
        if self.fail:
            msg = "disk full"
            raise OSError(msg)
        return ToolResult(content=f"Saved {args.get('name')!r}.")


def _tc(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


async def _run(
    llm: _ScriptedLLM,
    registry: ToolRegistry,
    *,
    max_steps: int = 5,
) -> AgentState:
    async with make_checkpointer("memory") as cp:
        runner = GraphRunner(checkpointer=cp)
        compiled = runner.compile(build_react_graph(llm_caller=llm, tool_registry=registry))
        cfg: RunnableConfig = {"configurable": {"thread_id": str(uuid4())}}
        return await compiled.ainvoke(
            {
                "messages": [HumanMessage(content="start")],
                "step_count": 0,
                "max_steps": max_steps,
            },
            config=cfg,
        )


def _find_advisory(messages: Sequence[BaseMessage]) -> HumanMessage | None:
    for msg in messages:
        if isinstance(msg, HumanMessage):
            content = msg.content if isinstance(msg.content, str) else ""
            if "<mutation-advisory>" in content:
                return msg
    return None


# ---------------------------------------------------------------------------
# tools_node → failed_mutations channel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failing_save_artifact_populates_failed_mutations_channel() -> None:
    """A failing ``save_artifact`` produces a ``MutationOutcome`` row
    in ``AgentState.failed_mutations`` so the next agent step can see
    the failure aggregated across the batch."""
    prompts: list[list[BaseMessage]] = []
    llm = _ScriptedLLM(
        responses=[
            AIMessage(
                content="",
                tool_calls=[_tc("save_artifact", {"name": "report.md"}, "tc-1")],
            ),
            AIMessage(content="done"),
        ],
        seen_prompts=prompts,
    )
    registry = ToolRegistry()
    registry.register(_ScriptedSaveArtifact(fail=True))

    state = await _run(llm, registry)

    # Pulled from the final state — the agent step consumed the channel
    # so it should be empty by the time we read it back.
    assert state.get("failed_mutations", []) == []
    # But the second LLM call (call index 1) DID see the advisory in
    # its prompt.
    second_prompt = prompts[1]
    advisory = _find_advisory(second_prompt)
    assert advisory is not None
    assert "report.md" in (advisory.content if isinstance(advisory.content, str) else "")


@pytest.mark.asyncio
async def test_successful_save_artifact_does_not_inject_advisory() -> None:
    """Happy path: no failed mutations → no advisory in any future
    prompt. The L1 cache-prefix invariant cares that we don't
    spuriously inject HumanMessages when the agent is healthy."""
    prompts: list[list[BaseMessage]] = []
    llm = _ScriptedLLM(
        responses=[
            AIMessage(
                content="",
                tool_calls=[_tc("save_artifact", {"name": "report.md"}, "tc-1")],
            ),
            AIMessage(content="done"),
        ],
        seen_prompts=prompts,
    )
    registry = ToolRegistry()
    registry.register(_ScriptedSaveArtifact(fail=False))

    await _run(llm, registry)

    second_prompt = prompts[1]
    assert _find_advisory(second_prompt) is None


# ---------------------------------------------------------------------------
# agent_node — invariants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_advisory_lives_in_human_message_not_system() -> None:
    """Mini-ADR L-1 / L-4: the advisory must NOT live in a
    ``SystemMessage`` (would invalidate the prompt-cache prefix)."""
    prompts: list[list[BaseMessage]] = []
    llm = _ScriptedLLM(
        responses=[
            AIMessage(
                content="",
                tool_calls=[_tc("save_artifact", {"name": "x.md"}, "tc-1")],
            ),
            AIMessage(content="done"),
        ],
        seen_prompts=prompts,
    )
    registry = ToolRegistry()
    registry.register(_ScriptedSaveArtifact(fail=True))

    await _run(llm, registry)

    second_prompt = prompts[1]
    # No SystemMessage in the second prompt should contain the tag.
    for msg in second_prompt:
        if isinstance(msg, SystemMessage):
            content = msg.content if isinstance(msg.content, str) else ""
            assert "<mutation-advisory>" not in content


@pytest.mark.asyncio
async def test_advisory_persists_in_conversation_history() -> None:
    """The advisory is part of the agent step's return dict so it
    lands in ``state["messages"]`` via the ``add_messages`` reducer
    — the next checkpoint resume sees the same history."""
    prompts: list[list[BaseMessage]] = []
    llm = _ScriptedLLM(
        responses=[
            AIMessage(
                content="",
                tool_calls=[_tc("save_artifact", {"name": "x.md"}, "tc-1")],
            ),
            AIMessage(content="done"),
        ],
        seen_prompts=prompts,
    )
    registry = ToolRegistry()
    registry.register(_ScriptedSaveArtifact(fail=True))

    state = await _run(llm, registry)

    # The full conversation log includes the advisory line.
    assert _find_advisory(state["messages"]) is not None


@pytest.mark.asyncio
async def test_advisory_does_not_double_inject_on_followup_turn() -> None:
    """After consumption ``failed_mutations`` resets to ``[]``; a
    follow-up agent step (no further failures) must NOT re-inject the
    same advisory text."""
    prompts: list[list[BaseMessage]] = []
    llm = _ScriptedLLM(
        responses=[
            # Turn 1: fail.
            AIMessage(
                content="",
                tool_calls=[_tc("save_artifact", {"name": "x.md"}, "tc-1")],
            ),
            # Turn 2: ack the advisory, call no tools.
            AIMessage(content="I understand the save failed"),
            # Turn 3 won't fire — run ends at turn 2 (no tool_calls).
        ],
        seen_prompts=prompts,
    )
    registry = ToolRegistry()
    registry.register(_ScriptedSaveArtifact(fail=True))

    state = await _run(llm, registry)

    # Exactly one advisory in the persisted history — not duplicated.
    advisories = [
        m
        for m in state["messages"]
        if isinstance(m, HumanMessage)
        and isinstance(m.content, str)
        and "<mutation-advisory>" in m.content
    ]
    assert len(advisories) == 1


# ---------------------------------------------------------------------------
# Multiple failing mutations aggregate into one advisory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_failures_aggregate_into_single_advisory() -> None:
    """Two failing ``save_artifact`` calls in one batch produce a
    single advisory listing both paths — Hermes's "footer aggregates
    cross-tool failures" guarantee. (Same-path saves get serialised
    into separate stages by L6, but we exercise two different paths
    so they share one stage.)"""
    prompts: list[list[BaseMessage]] = []
    llm = _ScriptedLLM(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    _tc("save_artifact", {"name": "a.md"}, "tc-1"),
                    _tc("save_artifact", {"name": "b.md"}, "tc-2"),
                ],
            ),
            AIMessage(content="done"),
        ],
        seen_prompts=prompts,
    )
    registry = ToolRegistry()
    registry.register(_ScriptedSaveArtifact(fail=True))

    await _run(llm, registry)

    second_prompt = prompts[1]
    advisory = _find_advisory(second_prompt)
    assert advisory is not None
    content = advisory.content if isinstance(advisory.content, str) else ""
    assert "a.md" in content
    assert "b.md" in content


@pytest.mark.asyncio
async def test_only_failing_mutation_tool_appears_in_advisory() -> None:
    """A failing ``save_artifact`` alongside a successful read-only
    tool surfaces only the mutation. The advisory is scoped to
    mutations, not generic tool errors."""

    @dataclass
    class _ReadTool:
        @property
        def spec(self) -> ToolSpec:
            return ToolSpec(
                name="web_search",
                description="scripted web_search",
                is_read_only=True,
            )

        async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
            del args, ctx
            return ToolResult(content="some search result")

    prompts: list[list[BaseMessage]] = []
    llm = _ScriptedLLM(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    _tc("save_artifact", {"name": "report.md"}, "tc-1"),
                    _tc("web_search", {"q": "x"}, "tc-2"),
                ],
            ),
            AIMessage(content="done"),
        ],
        seen_prompts=prompts,
    )
    registry = ToolRegistry()
    registry.register(_ScriptedSaveArtifact(fail=True))
    registry.register(_ReadTool())

    await _run(llm, registry)

    second_prompt = prompts[1]
    advisory = _find_advisory(second_prompt)
    assert advisory is not None
    content = advisory.content if isinstance(advisory.content, str) else ""
    # Only save_artifact appears in the advisory — web_search is not
    # tracked by the classifier.
    assert "save_artifact" in content
    assert "web_search" not in content


@pytest.mark.asyncio
async def test_tool_dispatch_error_unrelated_to_mutation_does_not_advise() -> None:
    """A failing read-only tool produces a normal error ToolMessage
    but no mutation advisory — the LLM already saw the error in the
    inline ToolMessage and L4 is mutation-specific."""

    @dataclass
    class _FailingRead:
        @property
        def spec(self) -> ToolSpec:
            return ToolSpec(
                name="web_search",
                description="scripted web_search",
                is_read_only=True,
            )

        async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
            del args, ctx
            msg = "rate limit"
            raise RuntimeError(msg)

    prompts: list[list[BaseMessage]] = []
    llm = _ScriptedLLM(
        responses=[
            AIMessage(
                content="",
                tool_calls=[_tc("web_search", {"q": "x"}, "tc-1")],
            ),
            AIMessage(content="done"),
        ],
        seen_prompts=prompts,
    )
    registry = ToolRegistry()
    registry.register(_FailingRead())

    state = await _run(llm, registry)

    # The error ToolMessage is in history (the LLM saw it inline).
    tool_msgs = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].status == "error"
    # But no advisory — mutation-specific guard.
    assert _find_advisory(state["messages"]) is None
