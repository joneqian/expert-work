"""Agent planning models — Stream J.1 (task decomposition).

A :class:`Plan` is an ordered decomposition of the user's task produced
by the orchestrator's ``planner`` graph node before the ReAct loop runs
(``WorkflowSpec.type == "plan_execute"``). It is carried on
``AgentState.plan`` — checkpointed — and rendered into the agent's
system context so each ReAct step executes against it.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PlanStep(BaseModel):
    """One concrete step of a :class:`Plan`."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="stable step identifier, e.g. '1'")
    description: str = Field(description="what this step accomplishes")


class Plan(BaseModel):
    """An ordered task decomposition produced by the planner node."""

    model_config = ConfigDict(frozen=True)

    goal: str = Field(description="one-sentence restatement of the task")
    steps: tuple[PlanStep, ...] = Field(description="ordered steps to carry out")
