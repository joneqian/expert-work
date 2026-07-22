from __future__ import annotations

from orchestrator.graph_builder.builder import _filter_scheduling_tools
from orchestrator.tools.registry import ToolSpec


def _specs() -> list[ToolSpec]:
    return [
        ToolSpec(name="web_search", description="", parameters={}),
        ToolSpec(name="manage_task", description="", parameters={}),
    ]


def test_normal_run_keeps_manage_task() -> None:
    names = [s.name for s in _filter_scheduling_tools(_specs(), trigger_origin=False)]
    assert "manage_task" in names and "web_search" in names


def test_trigger_run_drops_manage_task() -> None:
    names = [s.name for s in _filter_scheduling_tools(_specs(), trigger_origin=True)]
    assert "manage_task" not in names
    assert "web_search" in names
