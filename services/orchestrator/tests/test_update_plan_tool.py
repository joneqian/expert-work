"""Unit tests for the ``update_plan`` tool — Stream K.K8."""

from __future__ import annotations

import pytest

from expert_work.protocol import Plan, PlanStep
from orchestrator.tools.registry import ToolContext
from orchestrator.tools.update_plan import UpdatePlanTool

_INITIAL_PLAN = Plan(
    goal="Ship the dogfood agent",
    steps=(
        PlanStep(id="1", description="Read the manifest"),
        PlanStep(id="2", description="Run the planner"),
    ),
)


def _ctx_with_plan(plan: Plan | None = _INITIAL_PLAN) -> ToolContext:
    """Build a context that mimics what ``tools_node`` injects for a
    plan_execute workflow."""
    return ToolContext(plan=plan)


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_plan_replaces_steps_and_keeps_original_goal() -> None:
    """The agent supplies new steps; ``ctx.plan.goal`` is preserved."""
    tool = UpdatePlanTool()

    result = await tool.call(
        {
            "steps": [
                "Catch up on the new spec",
                "Generate a fresh implementation",
                "Re-run the validation suite",
            ],
            "reason": "Initial plan diverged after spec was revised mid-run",
        },
        ctx=_ctx_with_plan(),
    )

    # state_updates carries the new Plan onto the K.K8 allowlisted channel.
    new_plan = result.state_updates["plan"]
    assert isinstance(new_plan, Plan)
    assert new_plan.goal == _INITIAL_PLAN.goal  # goal preserved
    assert [s.description for s in new_plan.steps] == [
        "Catch up on the new spec",
        "Generate a fresh implementation",
        "Re-run the validation suite",
    ]
    # Step ids are renumbered 1..n so the agent's system context render
    # stays clean after a revise.
    assert [s.id for s in new_plan.steps] == ["1", "2", "3"]

    # The content / meta surface back to the LLM so it can confirm.
    assert "3 step(s)" in result.content
    assert result.meta["n_steps"] == 3
    assert "diverged" in result.meta["reason"]


# ---------------------------------------------------------------------------
# safety: tool can only run inside a plan_execute workflow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_plan_rejects_when_no_plan_in_context() -> None:
    """The factory only registers ``update_plan`` for plan_execute, but
    defend the tool against an ordering bug in case it ever fires with
    no plan in the context."""
    tool = UpdatePlanTool()
    with pytest.raises(ValueError, match="nothing to revise"):
        await tool.call(
            {"steps": ["step"], "reason": "x"},
            ctx=_ctx_with_plan(plan=None),
        )


# ---------------------------------------------------------------------------
# input validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_plan_rejects_empty_steps_array() -> None:
    tool = UpdatePlanTool()
    with pytest.raises(ValueError, match="non-empty 'steps' array"):
        await tool.call({"steps": [], "reason": "x"}, ctx=_ctx_with_plan())


@pytest.mark.asyncio
async def test_update_plan_rejects_missing_reason() -> None:
    tool = UpdatePlanTool()
    with pytest.raises(ValueError, match="non-empty 'reason'"):
        await tool.call({"steps": ["a"], "reason": "   "}, ctx=_ctx_with_plan())


@pytest.mark.asyncio
async def test_update_plan_rejects_all_blank_step_descriptions() -> None:
    """``minItems`` in the schema doesn't catch a list of empty strings,
    so the tool itself must surface the error so the LLM sees feedback."""
    tool = UpdatePlanTool()
    with pytest.raises(ValueError, match="at least one non-empty step"):
        await tool.call(
            {"steps": ["   ", "\n"], "reason": "broken plan"},
            ctx=_ctx_with_plan(),
        )


@pytest.mark.asyncio
async def test_update_plan_trims_step_descriptions() -> None:
    tool = UpdatePlanTool()
    result = await tool.call(
        {"steps": ["  do thing  ", "next"], "reason": "tidy"},
        ctx=_ctx_with_plan(),
    )
    plan = result.state_updates["plan"]
    assert plan.steps[0].description == "do thing"


# ---------------------------------------------------------------------------
# Stream CM-0 (N1) — per-step status so the agent can mark progress
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_plan_accepts_object_steps_with_status() -> None:
    """Object-form steps let the agent set status; the recitation reflects it."""
    tool = UpdatePlanTool()
    result = await tool.call(
        {
            "steps": [
                {"description": "read the spec", "status": "completed"},
                {"description": "implement", "status": "in_progress"},
                {"description": "review"},  # no status → pending
            ],
            "reason": "mark progress",
        },
        ctx=_ctx_with_plan(),
    )
    new_plan = result.state_updates["plan"]
    assert [s.status for s in new_plan.steps] == ["completed", "in_progress", "pending"]


@pytest.mark.asyncio
async def test_update_plan_string_steps_default_to_pending() -> None:
    """Backward compatible: bare string steps stay pending."""
    tool = UpdatePlanTool()
    result = await tool.call({"steps": ["one", "two"], "reason": "plain"}, ctx=_ctx_with_plan())
    new_plan = result.state_updates["plan"]
    assert all(s.status == "pending" for s in new_plan.steps)


@pytest.mark.asyncio
async def test_update_plan_invalid_status_falls_back_to_pending() -> None:
    """A bogus status does not reject the replan — it degrades to pending."""
    tool = UpdatePlanTool()
    result = await tool.call(
        {"steps": [{"description": "x", "status": "bogus"}], "reason": "r"},
        ctx=_ctx_with_plan(),
    )
    assert result.state_updates["plan"].steps[0].status == "pending"
