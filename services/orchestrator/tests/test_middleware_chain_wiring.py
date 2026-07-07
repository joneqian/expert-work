"""Integration tests for Stream E.12.5 — middleware chain wiring.

Covers test matrix #42 (anchor trigger sequence through a full ReAct
step), #43 (per-provider around_llm_call so E.4 breaker can isolate),
and #44 (LLMClientError short-circuits the router even when wrapped
in a chain).

The setup uses ``spying`` middlewares that record every invocation so
tests can assert on the exact anchor sequence + per-anchor payload
without needing the real E.3/E.4/E.5/E.10/E.10.5 middlewares plugged in
(those have their own unit tests). This keeps the integration test
focused on the wiring contract.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import pytest
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig

from expert_work.runtime.checkpointer import make_checkpointer
from expert_work.runtime.middleware import (
    CallNext,
    LLMClientError,
    LLMServerError,
    MiddlewareChain,
    MiddlewareContext,
)
from orchestrator import (
    AgentState,
    GraphRunner,
    LLMRouter,
    ProviderHandle,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    build_react_graph,
)

# ---------------------------------------------------------------------------
# Spying middleware + scripted provider/tool helpers
# ---------------------------------------------------------------------------


@dataclass
class _SpyMiddleware:
    """Records every call into ``log`` with the anchor name and a
    snapshot of selected payload keys, then forwards to call_next."""

    name: str
    anchor: str
    log: list[tuple[str, dict[str, object]]]
    after: tuple[str, ...] = field(default_factory=tuple)
    before: tuple[str, ...] = field(default_factory=tuple)
    snapshot_keys: tuple[str, ...] = ()

    async def __call__(self, ctx: MiddlewareContext, call_next: CallNext) -> None:
        snap = {k: ctx.payload.get(k) for k in self.snapshot_keys}
        self.log.append((self.name, snap))
        await call_next(ctx)


@dataclass
class _RaisingMiddleware:
    """Around-LLM middleware that always raises a given exception
    instead of calling its inner — used to verify that LLMClientError
    raised by a middleware doesn't get retried / fallen back over."""

    name: str
    exc: BaseException
    anchor: str = "around_llm_call"
    after: tuple[str, ...] = field(default_factory=tuple)
    before: tuple[str, ...] = field(default_factory=tuple)

    async def __call__(self, _ctx: MiddlewareContext, _call_next: CallNext) -> None:
        raise self.exc


@dataclass
class _ScriptedProvider:
    """LLMProvider stub returning a scripted sequence of responses."""

    responses: list[AIMessage]
    raise_with: BaseException | None = None
    calls: int = 0

    async def complete(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
    ) -> AIMessage:
        del messages, tools
        if self.raise_with is not None:
            raise self.raise_with
        idx = self.calls
        self.calls += 1
        if idx >= len(self.responses):
            raise RuntimeError(f"scripted provider ran out at call {idx}")
        return self.responses[idx]


@dataclass
class _ScriptedTool:
    name: str
    result: str = "tool-ok"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description=f"scripted {self.name}")

    async def call(self, args: object, *, ctx: object) -> ToolResult:
        del args, ctx
        return ToolResult(content=self.result)


def _tool_call(name: str, args: dict[str, object], call_id: str) -> dict[str, object]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def _config() -> RunnableConfig:
    return {"configurable": {"thread_id": "test-thread"}}


# ---------------------------------------------------------------------------
# Test matrix #42 — anchor trigger sequence through one ReAct step
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anchor_sequence_through_one_react_step() -> None:
    """Drive the graph: agent decides to call a tool, tool succeeds,
    agent finalises with text-only response. Verify the anchor sequence
    matches the design (Mini-ADR § 2.4)::

        before_llm_call → around_llm_call → after_llm_call
          → before_tool_dispatch
          → before_llm_call → around_llm_call → after_llm_call
    """
    log: list[tuple[str, dict[str, object]]] = []

    before = MiddlewareChain.from_middlewares(
        "before_llm_call",
        [_SpyMiddleware("ctx_redactor", "before_llm_call", log)],
    )
    around = MiddlewareChain.from_middlewares(
        "around_llm_call",
        [
            _SpyMiddleware(
                "tracer",
                "around_llm_call",
                log,
                snapshot_keys=("provider_key",),
            )
        ],
    )
    after = MiddlewareChain.from_middlewares(
        "after_llm_call",
        [_SpyMiddleware("loop_guard", "after_llm_call", log)],
    )
    btd = MiddlewareChain.from_middlewares(
        "before_tool_dispatch",
        [
            _SpyMiddleware(
                "sandbox",
                "before_tool_dispatch",
                log,
                snapshot_keys=("tool_name",),
            )
        ],
    )

    tool = _ScriptedTool("echo", result="42")
    registry = ToolRegistry()
    registry.register(tool)

    provider = _ScriptedProvider(
        responses=[
            AIMessage(
                content="",
                tool_calls=[_tool_call("echo", {"x": 1}, "tc-1")],
                id="ai-1",
            ),
            AIMessage(content="all done", id="ai-2"),
        ]
    )
    router = LLMRouter(
        providers=[ProviderHandle(provider=provider, key="anthropic:primary")],
        around_llm_chain=around,
    )

    async with make_checkpointer("memory") as cp:
        runner = GraphRunner(checkpointer=cp)
        compiled = runner.compile(
            build_react_graph(
                llm_caller=router,
                tool_registry=registry,
                before_llm_chain=before,
                after_llm_chain=after,
                before_tool_dispatch_chain=btd,
            )
        )
        initial: AgentState = {
            "messages": [HumanMessage(content="echo 1")],
            "step_count": 0,
            "max_steps": 5,
        }
        final = await compiled.ainvoke(initial, config=_config())

    sequence = [entry[0] for entry in log]
    assert sequence == [
        "ctx_redactor",
        "tracer",
        "loop_guard",
        "sandbox",
        "ctx_redactor",
        "tracer",
        "loop_guard",
    ], f"unexpected anchor sequence: {sequence}"

    # provider_key snapshot must be propagated to around_llm_call.
    around_snapshots = [snap for name, snap in log if name == "tracer"]
    assert all(s.get("provider_key") == "anthropic:primary" for s in around_snapshots)

    # tool_name snapshot must reach before_tool_dispatch.
    btd_snapshots = [snap for name, snap in log if name == "sandbox"]
    assert btd_snapshots[0].get("tool_name") == "echo"

    # Sanity on final state: 4 messages = [Human, AIMessage(tool_calls),
    # ToolMessage, AIMessage("all done")].
    assert len(final["messages"]) == 4
    assert isinstance(final["messages"][-1], AIMessage)
    assert final["messages"][-1].content == "all done"
    assert isinstance(final["messages"][-2], ToolMessage)
    assert final["messages"][-2].content == "42"
    assert final["step_count"] == 2


@pytest.mark.asyncio
async def test_no_chain_path_still_works() -> None:
    """``build_react_graph`` without any chains must behave exactly like
    the pre-E.12.5 code path — keeps the M0 unit-test surface intact."""
    tool = _ScriptedTool("echo", result="ok")
    registry = ToolRegistry()
    registry.register(tool)
    provider = _ScriptedProvider(responses=[AIMessage(content="hi", id="ai-1")])
    router = LLMRouter(providers=[ProviderHandle(provider=provider, key="anthropic:primary")])

    async with make_checkpointer("memory") as cp:
        runner = GraphRunner(checkpointer=cp)
        compiled = runner.compile(build_react_graph(llm_caller=router, tool_registry=registry))
        final = await compiled.ainvoke(
            {
                "messages": [HumanMessage(content="hi")],
                "step_count": 0,
                "max_steps": 5,
            },
            config=_config(),
        )
    assert final["messages"][-1].content == "hi"
    assert final["step_count"] == 1


# ---------------------------------------------------------------------------
# Test matrix #43 — per-provider around_llm_call (Mini-ADR E-13)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_around_llm_call_fires_once_per_provider_on_fallback() -> None:
    """LLMRouter with primary+fallback; primary raises LLMServerError.
    The around_llm_call chain must be invoked **twice** — once per
    provider — with the matching ``provider_key`` in payload each time.
    This is what lets E.4 BreakerRegistry build per-key breakers."""
    log: list[tuple[str, dict[str, object]]] = []
    around = MiddlewareChain.from_middlewares(
        "around_llm_call",
        [
            _SpyMiddleware(
                "spy",
                "around_llm_call",
                log,
                snapshot_keys=("provider_key",),
            )
        ],
    )

    primary = _ScriptedProvider(responses=[], raise_with=LLMServerError("primary 503"))
    fallback = _ScriptedProvider(responses=[AIMessage(content="from-fallback")])
    router = LLMRouter(
        providers=[
            ProviderHandle(provider=primary, key="anthropic:primary"),
            ProviderHandle(provider=fallback, key="kimi:fallback"),
        ],
        around_llm_chain=around,
    )

    result = await router(messages=[HumanMessage(content="hi")], tools=[])

    assert result.content == "from-fallback"
    # Chain invoked twice: once per provider, in order.
    provider_keys = [snap.get("provider_key") for _, snap in log]
    assert provider_keys == ["anthropic:primary", "kimi:fallback"], (
        f"around_llm_call must fire per-provider; got {provider_keys}"
    )


@pytest.mark.asyncio
async def test_around_llm_call_payload_preserves_messages_and_response() -> None:
    """Verify the chain's terminal correctly stashes response in payload
    and the router decodes it back — without this round-trip
    after_llm_call middlewares can't observe the response."""
    log: list[tuple[str, dict[str, object]]] = []
    around = MiddlewareChain.from_middlewares(
        "around_llm_call",
        [
            _SpyMiddleware(
                "spy",
                "around_llm_call",
                log,
                snapshot_keys=("messages", "tools"),
            )
        ],
    )

    provider = _ScriptedProvider(responses=[AIMessage(content="hello", id="ai-1")])
    router = LLMRouter(
        providers=[ProviderHandle(provider=provider, key="anthropic:primary")],
        around_llm_chain=around,
    )

    msgs = [HumanMessage(content="user-1")]
    tools = [ToolSpec(name="t", description="tool")]
    result = await router(messages=msgs, tools=tools)

    assert result.content == "hello"
    # Spy saw the inputs.
    snap = log[0][1]
    captured_messages = snap.get("messages")
    captured_tools = snap.get("tools")
    assert isinstance(captured_messages, list)
    assert isinstance(captured_tools, list)
    assert len(captured_messages) == 1
    assert len(captured_tools) == 1


# ---------------------------------------------------------------------------
# Test matrix #44 — LLMClientError short-circuits even through the chain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_client_error_raised_inside_chain_does_not_fallback() -> None:
    """A chain middleware (e.g. E.4 retry catching a 4xx) that raises
    LLMClientError must propagate out of the chain → out of the router
    **without** triggering fallback. The router treats 4xx as a
    caller-bug short circuit regardless of where it originates."""
    around = MiddlewareChain.from_middlewares(
        "around_llm_call",
        [_RaisingMiddleware("4xx_raiser", exc=LLMClientError("bad request"))],
    )

    primary = _ScriptedProvider(responses=[AIMessage(content="never seen")])
    fallback = _ScriptedProvider(responses=[AIMessage(content="must not be reached")])
    router = LLMRouter(
        providers=[
            ProviderHandle(provider=primary, key="anthropic:primary"),
            ProviderHandle(provider=fallback, key="kimi:fallback"),
        ],
        around_llm_chain=around,
    )

    with pytest.raises(LLMClientError, match="bad request"):
        await router(messages=[HumanMessage(content="hi")], tools=[])

    # Neither provider's complete() was called — the chain's
    # ``terminal`` (which is what invokes the provider) never runs
    # because the middleware raises before forwarding to it.
    assert primary.calls == 0
    assert fallback.calls == 0


@pytest.mark.asyncio
async def test_server_error_inside_chain_falls_back() -> None:
    """Companion to the 4xx test: LLMServerError raised inside the
    chain still triggers fallback — same classification as a
    raw-provider failure."""

    @dataclass
    class _OneShot503:
        """First call raises 503; subsequent calls just pass through."""

        name: str = "503_then_pass"
        anchor: str = "around_llm_call"
        after: tuple[str, ...] = field(default_factory=tuple)
        before: tuple[str, ...] = field(default_factory=tuple)
        fired: bool = False

        async def __call__(self, ctx: MiddlewareContext, call_next: CallNext) -> None:
            if not self.fired:
                self.fired = True
                raise LLMServerError("simulated 503 inside chain")
            await call_next(ctx)

    around = MiddlewareChain.from_middlewares("around_llm_call", [_OneShot503()])

    primary = _ScriptedProvider(responses=[])
    fallback = _ScriptedProvider(responses=[AIMessage(content="ok-fallback")])
    router = LLMRouter(
        providers=[
            ProviderHandle(provider=primary, key="anthropic:primary"),
            ProviderHandle(provider=fallback, key="kimi:fallback"),
        ],
        around_llm_chain=around,
    )

    result = await router(messages=[HumanMessage(content="hi")], tools=[])
    assert result.content == "ok-fallback"
    # The primary's terminal never ran (middleware raised first), but
    # fallback's terminal did (second middleware invocation passes
    # through to call_next).
    assert primary.calls == 0
    assert fallback.calls == 1


# ---------------------------------------------------------------------------
# Sanity — before_llm_chain may rewrite messages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_before_llm_chain_can_mutate_messages() -> None:
    """E.3 dynamic_context / E.5 pii_redact need to rewrite messages
    before the LLM sees them. Verify that mutations to
    ``ctx.payload['messages']`` carry forward to the actual LLM call."""

    @dataclass
    class _AppendSystemNote:
        name: str = "note_injector"
        anchor: str = "before_llm_call"
        after: tuple[str, ...] = field(default_factory=tuple)
        before: tuple[str, ...] = field(default_factory=tuple)

        async def __call__(self, ctx: MiddlewareContext, call_next: CallNext) -> None:
            msgs = list(ctx.payload.get("messages", []))
            msgs.insert(0, HumanMessage(content="[injected note]"))
            ctx.payload["messages"] = msgs
            await call_next(ctx)

    before = MiddlewareChain.from_middlewares("before_llm_call", [_AppendSystemNote()])

    captured_messages: list[Sequence[BaseMessage]] = []

    class _CapturingProvider:
        async def complete(
            self,
            *,
            messages: Sequence[BaseMessage],
            tools: Sequence[ToolSpec],
        ) -> AIMessage:
            del tools
            captured_messages.append(list(messages))
            return AIMessage(content="captured", id="ai-1")

    router = LLMRouter(
        providers=[ProviderHandle(provider=_CapturingProvider(), key="anthropic:primary")]
    )
    registry = ToolRegistry()
    async with make_checkpointer("memory") as cp:
        runner = GraphRunner(checkpointer=cp)
        compiled = runner.compile(
            build_react_graph(
                llm_caller=router,
                tool_registry=registry,
                before_llm_chain=before,
            )
        )
        await compiled.ainvoke(
            {
                "messages": [HumanMessage(content="real-user")],
                "step_count": 0,
                "max_steps": 3,
            },
            config=_config(),
        )

    assert len(captured_messages) == 1
    seen = captured_messages[0]
    assert len(seen) == 2
    assert isinstance(seen[0], HumanMessage)
    assert seen[0].content == "[injected note]"
    assert isinstance(seen[1], HumanMessage)
    assert seen[1].content == "real-user"
