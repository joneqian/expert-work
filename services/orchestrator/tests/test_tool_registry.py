"""Unit tests for :class:`ToolRegistry` (Stream E.6)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pytest

from orchestrator import Tool, ToolNotFoundError, ToolRegistry, ToolResult, ToolSpec


@dataclass
class _DummyTool:
    spec: ToolSpec

    async def call(self, args: Mapping[str, Any]) -> ToolResult:
        return ToolResult(content=f"called with {dict(args)}")


def _make(name: str) -> _DummyTool:
    return _DummyTool(spec=ToolSpec(name=name, description=f"dummy {name}"))


def test_empty_registry() -> None:
    registry = ToolRegistry()
    assert len(registry) == 0
    assert registry.get("anything") is None
    assert registry.specs() == []


def test_register_and_lookup() -> None:
    registry = ToolRegistry()
    tool = _make("web_search")
    registry.register(tool)
    assert "web_search" in registry
    assert registry.get("web_search") is tool
    assert len(registry) == 1


def test_specs_preserve_registration_order() -> None:
    registry = ToolRegistry()
    for name in ("alpha", "bravo", "charlie"):
        registry.register(_make(name))
    assert [s.name for s in registry.specs()] == ["alpha", "bravo", "charlie"]


def test_re_registration_replaces() -> None:
    registry = ToolRegistry()
    first = _make("web_search")
    second = _make("web_search")
    registry.register(first)
    registry.register(second)
    assert registry.get("web_search") is second
    assert len(registry) == 1


def test_get_required_raises_on_unknown() -> None:
    registry = ToolRegistry()
    with pytest.raises(ToolNotFoundError, match="unknown tool"):
        registry.get_required("missing")


def test_get_returns_none_on_unknown() -> None:
    registry = ToolRegistry()
    assert registry.get("missing") is None


def test_dummy_tool_satisfies_protocol() -> None:
    assert isinstance(_make("x"), Tool)
