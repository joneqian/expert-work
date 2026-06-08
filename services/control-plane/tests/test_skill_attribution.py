"""Tests for the SE-5b failure attribution (rule prefilter + LLM fallback).

Coarse binary: content_error (→ co-evolve revise) vs execution_error (→ discard,
don't feed back). Rules catch unambiguous environment failures cheaply; the LLM
(injected seam, CI fake) decides the rest, defaulting to execution when unsure.
"""

from __future__ import annotations

from uuid import UUID

from control_plane.skill_attribution import (
    FailureKind,
    FailureSignal,
    SkillAttributor,
    should_feed_back,
)

_TENANT = UUID("33333333-3333-3333-3333-333333333333")


class FakeModel:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    async def __call__(self, *, prompt: str, tenant_id: UUID, model: str | None = None) -> str:
        self.calls += 1
        return self.reply


def _attr(model: FakeModel) -> SkillAttributor:
    return SkillAttributor(model=model)


# --------------------------------------------------------------------------- #
# Rule prefilter → execution_error, no LLM call
# --------------------------------------------------------------------------- #


async def test_timeout_is_execution_by_rule() -> None:
    model = FakeModel("content_error")  # would say content, but rule wins
    verdict = await _attr(model).attribute(
        tenant_id=_TENANT, signal=FailureSignal(timed_out=True), skill_prompt="x"
    )
    assert verdict.kind is FailureKind.EXECUTION
    assert verdict.by_rule is True
    assert model.calls == 0  # short-circuited, no LLM


async def test_connection_refused_tool_error_is_execution_by_rule() -> None:
    model = FakeModel("content_error")
    verdict = await _attr(model).attribute(
        tenant_id=_TENANT,
        signal=FailureSignal(tool_errors=("Connection refused to api.x",)),
        skill_prompt="x",
    )
    assert verdict.kind is FailureKind.EXECUTION
    assert verdict.by_rule is True
    assert model.calls == 0


async def test_module_not_found_is_execution_by_rule() -> None:
    model = FakeModel("content_error")
    verdict = await _attr(model).attribute(
        tenant_id=_TENANT,
        signal=FailureSignal(error_text="ModuleNotFoundError: No module named 'foo'"),
        skill_prompt="x",
    )
    assert verdict.kind is FailureKind.EXECUTION
    assert verdict.by_rule is True


async def test_setup_phase_is_execution_by_rule() -> None:
    model = FakeModel("content_error")
    verdict = await _attr(model).attribute(
        tenant_id=_TENANT, signal=FailureSignal(exit_phase="setup"), skill_prompt="x"
    )
    assert verdict.kind is FailureKind.EXECUTION
    assert verdict.by_rule is True


# --------------------------------------------------------------------------- #
# LLM fallback (ambiguous signals)
# --------------------------------------------------------------------------- #


async def test_ambiguous_404_goes_to_llm() -> None:
    model = FakeModel("content_error")
    verdict = await _attr(model).attribute(
        tenant_id=_TENANT,
        signal=FailureSignal(error_text="HTTP 404 Not Found"),
        skill_prompt="call /v2/users",
    )
    assert model.calls == 1
    assert verdict.kind is FailureKind.CONTENT
    assert verdict.by_rule is False


async def test_llm_execution_verdict() -> None:
    model = FakeModel("execution_error: the environment was flaky")
    verdict = await _attr(model).attribute(
        tenant_id=_TENANT, signal=FailureSignal(error_text="weird"), skill_prompt="x"
    )
    assert verdict.kind is FailureKind.EXECUTION
    assert verdict.by_rule is False


async def test_llm_unparseable_defaults_to_execution() -> None:
    model = FakeModel("I am not sure what happened here")
    verdict = await _attr(model).attribute(
        tenant_id=_TENANT, signal=FailureSignal(error_text="weird"), skill_prompt="x"
    )
    assert verdict.kind is FailureKind.EXECUTION  # conservative: don't learn
    assert verdict.by_rule is False


async def test_llm_ambiguous_both_tokens_defaults_to_execution() -> None:
    model = FakeModel("could be content_error or execution_error")
    verdict = await _attr(model).attribute(
        tenant_id=_TENANT, signal=FailureSignal(error_text="weird"), skill_prompt="x"
    )
    assert verdict.kind is FailureKind.EXECUTION


# --------------------------------------------------------------------------- #
# should_feed_back
# --------------------------------------------------------------------------- #


async def test_should_feed_back_only_for_content() -> None:
    content = await _attr(FakeModel("content_error")).attribute(
        tenant_id=_TENANT, signal=FailureSignal(error_text="x"), skill_prompt="x"
    )
    execution = await _attr(FakeModel("nope")).attribute(
        tenant_id=_TENANT, signal=FailureSignal(timed_out=True), skill_prompt="x"
    )
    assert should_feed_back(content) is True
    assert should_feed_back(execution) is False
