"""Orchestrator-level error types."""

from __future__ import annotations


class OrchestratorError(Exception):
    """Base class for orchestrator-raised errors."""


class MaxStepsExceededError(OrchestratorError):
    """Raised when the ReAct loop hits ``max_steps`` and the LLM still
    wants to call tools.

    Per [STREAM-E-DESIGN § 1.1 E.6](../../../../docs/streams/STREAM-E-DESIGN.md),
    the loop has a hard runaway guard — once ``state["step_count"]``
    reaches ``state["max_steps"]``, the agent node raises this instead
    of dispatching another LLM call. Callers should write a
    ``RUN_FAILED`` audit row and surface ``"reached max_steps"`` to
    the user.
    """

    def __init__(self, step_count: int, max_steps: int) -> None:
        super().__init__(f"ReAct loop exceeded max_steps={max_steps} (step_count={step_count})")
        self.step_count = step_count
        self.max_steps = max_steps
