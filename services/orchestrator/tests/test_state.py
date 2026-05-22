"""Smoke tests for :class:`AgentState` shape (Stream E.6)."""

from __future__ import annotations

import inspect

from orchestrator import DEFAULT_MAX_STEPS, AgentState


def test_required_keys_present() -> None:
    """E.6 ``step_count`` / ``max_steps`` on top of E.1 ``messages``;
    J.1 ``plan``, J.2 ``reflections``, J.3 ``recalled_memories``,
    L.5 ``step_count_refund_pending``, L.4 ``failed_mutations``,
    J.4-补强-2 ``subagent_invocations``, J.8 ``pending_approval`` /
    ``approval_resume`` / ``approval_outcome`` (last nine ``NotRequired``)."""
    annotations = inspect.get_annotations(AgentState)
    assert set(annotations) == {
        "messages",
        "step_count",
        "max_steps",
        "plan",
        "reflections",
        "recalled_memories",
        "step_count_refund_pending",
        "failed_mutations",
        "subagent_invocations",
        "pending_approval",
        "approval_resume",
        "approval_outcome",
    }


def test_default_max_steps_constant_is_documented() -> None:
    """``DEFAULT_MAX_STEPS`` must stay at the design-doc-locked value (20)."""
    assert DEFAULT_MAX_STEPS == 20
