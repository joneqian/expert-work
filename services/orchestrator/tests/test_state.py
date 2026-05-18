"""Smoke tests for :class:`AgentState` shape (Stream E.6)."""

from __future__ import annotations

import inspect

from orchestrator import DEFAULT_MAX_STEPS, AgentState


def test_required_keys_present() -> None:
    """E.6 ``step_count`` / ``max_steps`` on top of E.1 ``messages``;
    J.1 added the ``plan`` channel (``NotRequired`` — react inputs omit it)."""
    annotations = inspect.get_annotations(AgentState)
    assert set(annotations) == {"messages", "step_count", "max_steps", "plan"}


def test_default_max_steps_constant_is_documented() -> None:
    """``DEFAULT_MAX_STEPS`` must stay at the design-doc-locked value (20)."""
    assert DEFAULT_MAX_STEPS == 20
