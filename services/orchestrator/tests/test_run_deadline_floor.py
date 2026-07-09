"""Platform-default wall-clock floor for ``run_deadline_s``.

A long/multi-hour agent whose manifest leaves ``policies.run_deadline_s``
at 0 (the default) otherwise has NO wall-clock ceiling — only ``max_steps``
+ the spawn/worker caps bound it, and a step can be minutes. The platform
default supplies a generous floor so an unconfigured run cannot run
unbounded on wall-clock; an agent that genuinely needs longer raises
``run_deadline_s`` explicitly (manifest wins).
"""

from __future__ import annotations

from orchestrator.agent_factory import _effective_run_deadline_s


def test_manifest_value_wins_over_platform_default() -> None:
    assert _effective_run_deadline_s(120, 3600) == 120


def test_platform_default_applies_when_manifest_unset() -> None:
    assert _effective_run_deadline_s(0, 3600) == 3600


def test_both_off_stays_off() -> None:
    assert _effective_run_deadline_s(0, 0) == 0


def test_manifest_value_with_no_platform_default() -> None:
    assert _effective_run_deadline_s(300, 0) == 300
