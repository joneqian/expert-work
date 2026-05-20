"""Unit tests for the ``update_plan`` tool — Stream K.K8."""

from __future__ import annotations

import pytest

from helix_agent.protocol import Plan, PlanStep
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
