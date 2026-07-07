"""Tests for :mod:`orchestrator.run_retry` — Stream HX-3 (§ 4.5).

Classifier matrix, defensive env parsing, and the checkpoint-tail
replay-safety guard. The in-worker retry loop itself is exercised
end-to-end in ``test_sse.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from expert_work.runtime.middleware import LLMClientError
from orchestrator.errors import MaxStepsExceededError
from orchestrator.llm.router import AllProvidersExhaustedError
from orchestrator.run_retry import (
    is_transient_run_error,
    replay_is_safe,
    retry_backoff_s,
    retry_enabled,
)

# ---------------------------------------------------------------------------
# Classifier (Mini-ADR HX-C1)
# ---------------------------------------------------------------------------


def test_all_providers_exhausted_is_transient() -> None:
    assert is_transient_run_error(AllProvidersExhaustedError(TimeoutError("boom")))


@pytest.mark.parametrize(
    "exc",
    [
        LLMClientError("bad request"),
        RuntimeError("some bug"),
        MaxStepsExceededError(step_count=5, max_steps=5),
        ValueError("nope"),
    ],
)
def test_other_errors_are_permanent(exc: BaseException) -> None:
    assert not is_transient_run_error(exc)


# ---------------------------------------------------------------------------
# Env config (axiom ③ — defensive parsing)
# ---------------------------------------------------------------------------


def test_retry_enabled_defaults_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXPERT_WORK_RUN_TRANSIENT_RETRY", raising=False)
    assert retry_enabled()


@pytest.mark.parametrize("value", ["0", "false", "OFF", " no "])
def test_retry_enabled_explicit_falsey(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("EXPERT_WORK_RUN_TRANSIENT_RETRY", value)
    assert not retry_enabled()


def test_retry_enabled_garbage_stays_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXPERT_WORK_RUN_TRANSIENT_RETRY", "banana")
    assert retry_enabled()


def test_backoff_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXPERT_WORK_RUN_RETRY_BACKOFF_S", raising=False)
    assert retry_backoff_s() == 10.0


def test_backoff_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXPERT_WORK_RUN_RETRY_BACKOFF_S", "9999")
    assert retry_backoff_s() == 120.0
    monkeypatch.setenv("EXPERT_WORK_RUN_RETRY_BACKOFF_S", "0")
    assert retry_backoff_s() == 1.0


def test_backoff_bad_parse_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXPERT_WORK_RUN_RETRY_BACKOFF_S", "ten seconds")
    assert retry_backoff_s() == 10.0


# ---------------------------------------------------------------------------
# Replay-safety guard (Mini-ADR HX-C2)
# ---------------------------------------------------------------------------


class _StateGraph:
    """Stub graph: ``aget_state`` returns the given messages (or raises)."""

    def __init__(self, messages: list[Any] | None = None, *, raises: bool = False) -> None:
        self._messages = messages or []
        self._raises = raises

    async def aget_state(self, config: Any) -> Any:
        del config
        if self._raises:
            raise RuntimeError("state fetch broke")
        return SimpleNamespace(values={"messages": self._messages})


def _ai_with_calls(*names: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": {}, "id": f"call-{i}"} for i, name in enumerate(names)],
    )


@pytest.mark.asyncio
async def test_no_dangling_tail_is_safe() -> None:
    messages = [HumanMessage(content="hi"), AIMessage(content="done")]
    assert await replay_is_safe(_StateGraph(messages), {}, lambda name: False)


@pytest.mark.asyncio
async def test_answered_batch_is_safe() -> None:
    messages = [
        _ai_with_calls("bash"),
        ToolMessage(content="ok", tool_call_id="call-0"),
    ]
    # The batch is fully answered — even an irreversible tool is fine
    # because committed history never re-executes.
    assert await replay_is_safe(_StateGraph(messages), {}, lambda name: False)


@pytest.mark.asyncio
async def test_dangling_safe_batch_is_safe() -> None:
    messages = [HumanMessage(content="hi"), _ai_with_calls("read_file", "grep")]
    assert await replay_is_safe(_StateGraph(messages), {}, lambda name: True)


@pytest.mark.asyncio
async def test_dangling_unsafe_batch_is_rejected() -> None:
    messages = [HumanMessage(content="hi"), _ai_with_calls("read_file", "bash")]
    assert not await replay_is_safe(_StateGraph(messages), {}, lambda name: name == "read_file")


@pytest.mark.asyncio
async def test_partially_answered_batch_checks_remainder() -> None:
    messages = [
        _ai_with_calls("bash", "read_file"),
        ToolMessage(content="ok", tool_call_id="call-0"),  # bash answered
    ]
    # Only the unanswered ``read_file`` re-dispatches — safe.
    assert await replay_is_safe(_StateGraph(messages), {}, lambda name: name == "read_file")


@pytest.mark.asyncio
async def test_dangling_without_resolver_is_rejected() -> None:
    messages = [_ai_with_calls("read_file")]
    assert not await replay_is_safe(_StateGraph(messages), {}, None)


@pytest.mark.asyncio
async def test_state_fetch_failure_is_rejected() -> None:
    assert not await replay_is_safe(_StateGraph(raises=True), {}, lambda name: True)


@pytest.mark.asyncio
async def test_non_mapping_state_is_safe() -> None:
    class _WeirdGraph:
        async def aget_state(self, config: Any) -> Any:
            del config
            return SimpleNamespace(values=None)

    # No readable messages → nothing dangles → the replay is the agent
    # node's pure LLM call.
    assert await replay_is_safe(_WeirdGraph(), {}, None)
