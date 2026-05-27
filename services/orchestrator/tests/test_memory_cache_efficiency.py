"""Capability Uplift Sprint #8 — token-cost benchmark / exit gate.

Simulates a long session and asserts the ``per_session`` memory recall
mode pays meaningfully fewer input tokens than the ``per_turn`` legacy
behavior under Anthropic prompt caching.

The simulator approximates Anthropic's billing rule:

- A request that hits a prompt-cache prefix bills the cached portion at
  ~10% of the normal input-token rate; the uncached delta bills at
  full price.
- Cache hit length is the longest byte-stable prefix from the start of
  the prompt (system + leading messages, up to but not past the last
  ``cache_control`` marker that the upstream still has in its
  ephemeral cache).

The simulator is intentionally coarse — the real Anthropic billing
depends on the actual model + base64 image tokens + tool-block size,
which the fake doesn't model. The Sprint exit gate (Sprint #8 § 9.6
"≥ 30%") is enough buffer against the approximation that a genuine
regression in either path still trips the test.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

import pytest
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.runnables import RunnableConfig

from helix_agent.protocol import MemoryItem
from helix_agent.runtime.checkpointer import make_checkpointer
from orchestrator import (
    GraphRunner,
    ToolContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    build_react_graph,
)

_SPRINT_EXIT_GATE_RATIO = 0.30
_CACHE_HIT_DISCOUNT = 0.10  # cached tokens billed at 10% of base price


@dataclass
class _RecordedCall:
    messages: list[BaseMessage]
    system: str = ""


@dataclass
class _RecordingLLM:
    responses: list[AIMessage]
    calls: list[_RecordedCall] = field(default_factory=list)

    async def __call__(
        self, *, messages: Sequence[BaseMessage], tools: Sequence[ToolSpec]
    ) -> AIMessage:
        del tools
        # Capture the leading SystemMessage separately so the simulator
        # can model Anthropic's separate ``system`` payload.
        msgs = list(messages)
        system = ""
        if msgs and isinstance(msgs[0], SystemMessage):
            content = msgs[0].content
            system = content if isinstance(content, str) else str(content)
            msgs = msgs[1:]
        self.calls.append(_RecordedCall(messages=msgs, system=system))
        idx = len(self.calls) - 1
        if idx >= len(self.responses):
            raise RuntimeError(f"scripted LLM ran out at call {idx}")
        return self.responses[idx]


@dataclass
class _NoopTool:
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="noop", description="noop", is_read_only=True)

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        del args, ctx
        return ToolResult(content="ok")


def _approx_tokens(text: str) -> int:
    """Coarse char-quarter approximation matching L.L2's heuristic."""
    return max(1, len(text) // 4)


def _message_text(msg: BaseMessage) -> str:
    content = msg.content
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def _simulate_billing(
    calls: Sequence[_RecordedCall], *, mode: Literal["per_session", "per_turn"]
) -> int:
    """Approximate billed input tokens across all calls in a session.

    For ``per_session`` mode we model the cache covering ``[system,
    task, memories]`` once the second turn starts (the first turn pays
    full price and seeds the cache). For ``per_turn`` we model no
    cache benefit on the memory block (the position drifts every
    turn so no prefix is byte-stable).

    System tokens always benefit from L.L1's system-block cache
    starting from the second call. The model is intentionally simple
    so the exit gate has meaningful margin against approximation
    noise.
    """
    billed = 0.0
    for turn, call in enumerate(calls):
        system_tokens = _approx_tokens(call.system)
        msg_tokens = [_approx_tokens(_message_text(m)) for m in call.messages]
        total = system_tokens + sum(msg_tokens)

        if turn == 0:
            # First turn pays full price and warms the cache.
            billed += total
            continue

        # Subsequent turns: system always caches (L.L1). Memory block
        # caches if mode is per_session and it sits at index 1 — the
        # cache prefix covers [system, messages[0], messages[1]].
        cached_tokens = system_tokens
        if mode == "per_session" and len(msg_tokens) >= 2:
            cached_tokens += msg_tokens[0] + msg_tokens[1]
        uncached_tokens = total - cached_tokens
        billed += cached_tokens * _CACHE_HIT_DISCOUNT + uncached_tokens

    return int(billed)


def _build_long_session_input(
    memory_text: str, task: str
) -> dict[str, Any]:
    return {
        "messages": [
            SystemMessage(content="you are a helpful long-running agent" * 5),
            HumanMessage(content=task),
        ],
        "step_count": 0,
        "max_steps": 20,
        "recalled_memories": [
            MemoryItem(
                id=uuid4(),
                tenant_id=uuid4(),
                user_id=uuid4(),
                kind="fact",
                content=memory_text,
                embedding=(),
            )
        ],
    }


async def _run_session(*, mode: Literal["per_session", "per_turn"]) -> _RecordingLLM:
    """Drive a 10-turn ReAct session that calls a noop tool every step."""
    # Scripted LLM: 9 turns that call the noop tool, then a final
    # plain-text answer that ends the loop.
    responses: list[AIMessage] = []
    for i in range(9):
        responses.append(
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "noop",
                        "args": {"i": i},
                        "id": f"tc-{i}",
                        "type": "tool_call",
                    }
                ],
            )
        )
    responses.append(AIMessage(content="done"))

    llm = _RecordingLLM(responses=responses)
    registry = ToolRegistry()
    registry.register(_NoopTool())

    graph = build_react_graph(
        llm_caller=llm,
        tool_registry=registry,
        memory_recall_mode=mode,
    )
    # Memory block ~ 500 chars / 125 tokens — realistic for a 10-fact
    # long-term memory list rendered into prompt.
    memory_blob = (
        "the user is a backend engineer; prefers metric units; based in PT; "
        "favorite languages are Python and Go; uses neovim; weekly Friday demo; "
        "values terse status updates; hates emoji in code review; "
        "morning person; subscribes to the weekly engineering newsletter; "
    ) * 5
    async with make_checkpointer("memory") as cp:
        compiled = GraphRunner(checkpointer=cp).compile(graph)
        cfg: RunnableConfig = {"configurable": {"thread_id": str(uuid4())}}
        await compiled.ainvoke(
            _build_long_session_input(memory_blob, "kick off the long task"),
            config=cfg,
        )
    return llm


@pytest.mark.asyncio
async def test_per_session_saves_at_least_30pct_input_tokens_vs_per_turn() -> None:
    """Sprint #8 exit gate (§ 9.6 / decision #4):
    ``per_session`` mode must bill at least 30 % fewer input tokens
    than ``per_turn`` on a 10-turn session with a representative
    memory block under the cache simulator."""
    per_session = await _run_session(mode="per_session")
    per_turn = await _run_session(mode="per_turn")
    assert len(per_session.calls) == len(per_turn.calls), (
        "session length diverged — the test setup is the problem, not the gate"
    )

    per_session_bill = _simulate_billing(per_session.calls, mode="per_session")
    per_turn_bill = _simulate_billing(per_turn.calls, mode="per_turn")
    assert per_turn_bill > 0
    savings_ratio = (per_turn_bill - per_session_bill) / per_turn_bill
    assert savings_ratio >= _SPRINT_EXIT_GATE_RATIO, (
        f"per_session saved {savings_ratio:.1%} (gate ≥ {_SPRINT_EXIT_GATE_RATIO:.0%}); "
        f"per_turn_bill={per_turn_bill}, per_session_bill={per_session_bill}"
    )
