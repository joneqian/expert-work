"""``update_plan`` tool — Stream K.K8.

When the manifest's ``workflow.type`` is ``plan_execute`` the
``planner`` node (Stream J.1) writes an initial :class:`Plan` into
``AgentState`` once at the start of the run; ``agent_node`` then
renders that plan into the system context every step. Without an
in-run mutation entry the agent has no way to revise the plan when
execution diverges from it — STREAM-K-DESIGN § 3.K8 calls this the
"plan_execute closure gap".

This tool is the closure: the agent can call ``update_plan`` with a
fresh ordered list of steps; the tools node promotes the new
:class:`Plan` onto ``AgentState.plan`` via the
:data:`~orchestrator.tools.registry.TOOL_ALLOWED_STATE_KEYS` channel.
The reflect node's existing ``revise`` path (Stream J.2) already
covers reflective replans; ``update_plan`` adds the agent-initiated
path.

The tool is implicit — never declared in the manifest. The factory
registers it exactly when ``workflow.type == "plan_execute"``, so a
react-mode agent does not see it (and cannot accidentally rewrite a
plan it never had).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from helix_agent.protocol import Plan, PlanStep
from orchestrator.tools.registry import ToolContext, ToolResult, ToolSpec

logger = logging.getLogger(__name__)

#: Caps on plan size to keep the rendered system context bounded. The
#: J.1 planner uses the same shape — keep these in step with
#: ``planner.py``'s soft caps so the agent can't sneak past the limits
#: by going through ``update_plan``.
_MAX_STEPS: int = 20
_MAX_STEP_DESCRIPTION_CHARS: int = 500


@dataclass(frozen=True)
class UpdatePlanTool:
    """``update_plan(steps, reason)`` — agent-initiated replan.

    Replaces the run's :class:`Plan` with a new ordered set of steps.
    The replacement is *complete* (not a patch) — modelling partial
    diffs would add a lot of surface for arguable gain. ``reason`` is
    captured for trace / audit only; it is not rendered back to the
    agent.
    """

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="update_plan",
            description=(
                "Replace your current plan with a revised ordered list of "
                "steps. Use this when execution has diverged from the "
                "initial plan and you need to chart a fresh path."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": _MAX_STEPS,
                        "description": (
                            "Ordered list of step descriptions for the "
                            "revised plan. Each entry is a short imperative "
                            "phrase the agent will execute in order."
                        ),
                    },
                    "reason": {
                        "type": "string",
                        "description": (
                            "Why the plan is being revised — recorded for "
                            "the trace, not fed back to the agent."
                        ),
                    },
                },
                "required": ["steps", "reason"],
            },
        )

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        if ctx.plan is None:
            # The factory only registers ``update_plan`` for plan_execute
            # workflows, so we should normally see a plan. Defend
            # against an ordering bug (tool dispatched before the
            # planner node ran) so the LLM gets a clean error rather
            # than a crash.
            msg = "update_plan called before a plan was established; nothing to revise"
            raise ValueError(msg)
        steps_raw = args.get("steps")
        reason = str(args.get("reason", "")).strip()

        if not isinstance(steps_raw, list) or not steps_raw:
            msg = "update_plan requires a non-empty 'steps' array"
            raise ValueError(msg)
        if not reason:
            msg = "update_plan requires a non-empty 'reason' string"
            raise ValueError(msg)

        # Trim each step + drop empties. The schema's minItems=1 already
        # rejects an empty array, but a list of empty strings would
        # produce an unusable plan; surface it as a value error so the
        # LLM gets feedback rather than the run silently accepting a
        # blank plan.
        cleaned: list[PlanStep] = []
        for index, raw_step in enumerate(steps_raw, start=1):
            description = str(raw_step).strip()
            if not description:
                continue
            if len(description) > _MAX_STEP_DESCRIPTION_CHARS:
                description = description[:_MAX_STEP_DESCRIPTION_CHARS] + "…"
            cleaned.append(PlanStep(id=str(index), description=description))

        if not cleaned:
            msg = "update_plan requires at least one non-empty step description"
            raise ValueError(msg)
        if len(cleaned) > _MAX_STEPS:
            cleaned = cleaned[:_MAX_STEPS]

        new_plan = Plan(goal=ctx.plan.goal, steps=tuple(cleaned))
        logger.info("update_plan.applied n_steps=%d reason=%r", len(cleaned), reason)

        rendered = "\n".join(f"{step.id}. {step.description}" for step in cleaned)
        return ToolResult(
            content=f"Plan revised to {len(cleaned)} step(s):\n{rendered}",
            meta={"n_steps": len(cleaned), "reason": reason},
            state_updates={"plan": new_plan},
        )
