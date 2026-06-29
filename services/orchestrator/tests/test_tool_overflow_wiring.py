"""Stream CM-5 PR2 — overflow externalization wiring into the ReAct graph.

Drives ``build_react_graph`` with an injected ``workspace_writer_factory``
(a recording fake, no live sandbox) and a tool that truncates its output:
the full rendering lands under ``.tool_results/`` and the ToolMessage gains
the ``<tool-result-overflow>`` reference footer. No writer / write failure /
read-only tool all degrade to today's truncation, byte-identical.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
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
from orchestrator.context import WorkspaceFileWriter

_FULL = "x" * 50_000
_CAPPED = _FULL[:100] + "...[truncated]"


@dataclass
class _RecordingWriter:
    writes: dict[str, str] = field(default_factory=dict)

    async def write(self, *, rel: str, content: str) -> None:
        self.writes[rel] = content


@dataclass
class _FailingWriter:
    attempts: int = 0

    async def write(self, *, rel: str, content: str) -> None:
        del rel, content
        self.attempts += 1
        raise OSError("sandbox unreachable")


@dataclass
class _ScriptedLLM:
    responses: list[AIMessage]
    calls: int = 0

    async def __call__(
        self, *, messages: Sequence[BaseMessage], tools: Sequence[ToolSpec]
    ) -> AIMessage:
        del messages, tools
        idx = self.calls
        self.calls += 1
        return self.responses[idx]


@dataclass
class _SpillTool:
    """A tool that truncated its output and carries the full rendering."""

    name: str = "spill"
    read_only: bool = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description="spills", is_read_only=self.read_only)

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        del args, ctx
        return ToolResult(content=_CAPPED, meta={"truncated": True}, full_content=_FULL)


async def _run_one_turn(
    *, tool: _SpillTool, writer: WorkspaceFileWriter | None, thread_id: str
) -> AgentState:
    llm = _ScriptedLLM(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": tool.name, "args": {}, "id": "tc-1", "type": "tool_call"}],
            ),
            AIMessage(content="done"),
        ]
    )
    registry = ToolRegistry()
    registry.register(tool)
    factory = (lambda _ctx: writer) if writer is not None else None
    async with make_checkpointer("memory") as cp:
        compiled = GraphRunner(checkpointer=cp).compile(
            build_react_graph(
                llm_caller=llm,
                tool_registry=registry,
                workspace_writer_factory=factory,
            )
        )
        cfg: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        return await compiled.ainvoke(
            {"messages": [HumanMessage(content="go")], "step_count": 0, "max_steps": 5},
            config=cfg,
        )


def _tool_message(state: AgentState) -> ToolMessage:
    messages = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert len(messages) == 1
    return messages[0]


async def test_overflow_externalized_and_footer_appended() -> None:
    writer = _RecordingWriter()
    state = await _run_one_turn(tool=_SpillTool(), writer=writer, thread_id="ov-1")

    # The full rendering landed under .tool_results/ (adhoc — no run_id).
    assert list(writer.writes) == [".tool_results/adhoc/tc-1-spill.txt"]
    assert writer.writes[".tool_results/adhoc/tc-1-spill.txt"] == _FULL
    # The checkpointed ToolMessage keeps the truncated content and gains
    # the recoverable reference footer.
    message = _tool_message(state)
    content = str(message.content)
    assert content.startswith(_CAPPED)
    assert "<tool-result-overflow>" in content
    assert ".tool_results/adhoc/tc-1-spill.txt" in content
    assert f"{len(_FULL)} chars" in content


async def test_tool_meta_surfaced_as_tool_message_artifact() -> None:
    # ToolResult.meta rides into ToolMessage.artifact (event stream / audit) —
    # otherwise the tool's structured metadata is dropped from the message.
    state = await _run_one_turn(tool=_SpillTool(), writer=None, thread_id="ov-art")
    assert _tool_message(state).artifact == {"truncated": True}


async def test_no_writer_keeps_truncation_byte_identical() -> None:
    state = await _run_one_turn(tool=_SpillTool(), writer=None, thread_id="ov-2")
    assert str(_tool_message(state).content) == _CAPPED


async def test_write_failure_degrades_without_footer() -> None:
    writer = _FailingWriter()
    state = await _run_one_turn(tool=_SpillTool(), writer=writer, thread_id="ov-3")
    assert writer.attempts == 1
    # Best-effort: the run completed and the content stands alone.
    assert str(_tool_message(state).content) == _CAPPED


async def test_read_only_tool_is_never_externalized() -> None:
    # Central double guard (CM-F3): even a read-only tool that wrongly
    # sets full_content must not be persisted (loop guard).
    writer = _RecordingWriter()
    state = await _run_one_turn(
        tool=_SpillTool(name="spill_ro", read_only=True), writer=writer, thread_id="ov-4"
    )
    assert writer.writes == {}
    assert str(_tool_message(state).content) == _CAPPED
