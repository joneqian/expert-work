"""11.4/11.5 live eval engines — engine logic over a fake harness.

The engines delegate the real agent run to an :class:`EvalAgentHarness`; a
fake stands in here so the mapping (reply → outcome, span tree → outcome),
the unhostable-case skip, the tenant-scope guard, and the dispatch routing
all unit-test with no model key. The production harness (a real in-process
agent + model) is exercised by the E2E verification, not here.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from opentelemetry.trace import StatusCode

from control_plane.eval_engine_live import (
    _UNHOSTABLE_ADVERSARIAL_CASES,
    ADVERSARIAL_SUITE,
    TRACE_EVAL_SUITE,
    AdversarialEvalEngine,
    DispatchEvalEngine,
    TraceEvalEngine,
)
from expert_work.persistence.rls import current_tenant_id_var

_REFUSAL = "I can't help with that — instructions embedded in content are ignored."


@contextmanager
def _tenant_scope() -> Iterator[UUID]:
    tenant_id = uuid4()
    token = current_tenant_id_var.set(tenant_id)
    try:
        yield tenant_id
    finally:
        current_tenant_id_var.reset(token)


class _FakeHarness:
    """Returns a canned reply (or echoes the prompt) / canned spans."""

    def __init__(
        self, *, reply: str = _REFUSAL, echo: bool = False, spans: Sequence[object] = ()
    ) -> None:
        self._reply = reply
        self._echo = echo
        self._spans = spans
        self.prompts: list[str] = []

    async def respond(self, prompt: str, *, tenant_id: UUID) -> str:
        self.prompts.append(prompt)
        return prompt if self._echo else self._reply

    async def respond_traced(self, prompt: str, *, tenant_id: UUID) -> Sequence[object]:
        self.prompts.append(prompt)
        return self._spans


def _span(name: str, *, error: bool = False) -> object:
    code = StatusCode.ERROR if error else StatusCode.OK
    return SimpleNamespace(name=name, attributes={}, status=SimpleNamespace(status_code=code))


# --- AdversarialEvalEngine --------------------------------------------------


@pytest.mark.asyncio
async def test_adversarial_safe_responder_all_pass() -> None:
    engine = AdversarialEvalEngine(_FakeHarness())
    with _tenant_scope():
        outcomes = await engine.run(ADVERSARIAL_SUITE)
    assert outcomes  # the dataset has runnable cases
    assert all(o.passed for o in outcomes)
    assert all(o.scores == {"safe": 1.0} for o in outcomes)


@pytest.mark.asyncio
async def test_adversarial_leaky_responder_fails_injections() -> None:
    # Echoing the prompt leaks the injection canary back + never refuses a
    # jailbreak → every runnable case is unsafe.
    engine = AdversarialEvalEngine(_FakeHarness(echo=True))
    with _tenant_scope():
        outcomes = await engine.run(ADVERSARIAL_SUITE)
    assert outcomes
    assert not any(o.passed for o in outcomes)


@pytest.mark.asyncio
async def test_adversarial_skips_unhostable_cases() -> None:
    engine = AdversarialEvalEngine(_FakeHarness())
    with _tenant_scope():
        outcomes = await engine.run(ADVERSARIAL_SUITE)
    produced = {o.case_id for o in outcomes}
    assert produced.isdisjoint(_UNHOSTABLE_ADVERSARIAL_CASES)


@pytest.mark.asyncio
async def test_engine_requires_tenant_scope() -> None:
    engine = AdversarialEvalEngine(_FakeHarness())
    with pytest.raises(RuntimeError, match="no tenant scope"):
        await engine.run(ADVERSARIAL_SUITE)


# --- TraceEvalEngine --------------------------------------------------------


@pytest.mark.asyncio
async def test_trace_pass_on_well_formed_spans() -> None:
    spans = [_span("expert_work.orchestrator.run"), _span("expert_work.orchestrator.llm_call")]
    engine = TraceEvalEngine(_FakeHarness(spans=spans))
    with _tenant_scope():
        outcomes = await engine.run(TRACE_EVAL_SUITE)
    assert outcomes
    assert all(o.passed for o in outcomes)
    assert all(o.capability == "10.1_trace_eval" for o in outcomes)


@pytest.mark.asyncio
async def test_trace_fails_on_error_span() -> None:
    spans = [
        _span("expert_work.orchestrator.run"),
        _span("expert_work.orchestrator.llm_call", error=True),
    ]
    engine = TraceEvalEngine(_FakeHarness(spans=spans))
    with _tenant_scope():
        outcomes = await engine.run(TRACE_EVAL_SUITE)
    assert outcomes
    assert not any(o.passed for o in outcomes)
    assert all(o.scores["violations"] >= 1.0 for o in outcomes)


# --- DispatchEvalEngine -----------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_routes_to_registered_engine() -> None:
    harness = _FakeHarness()
    dispatch = DispatchEvalEngine({ADVERSARIAL_SUITE: AdversarialEvalEngine(harness)})
    with _tenant_scope():
        outcomes = await dispatch.run(ADVERSARIAL_SUITE)
    assert outcomes
    assert harness.prompts  # the routed engine actually ran


@pytest.mark.asyncio
async def test_dispatch_unknown_suite_raises() -> None:
    dispatch = DispatchEvalEngine({})
    with pytest.raises(ValueError, match="no eval engine registered"):
        await dispatch.run("nope")
