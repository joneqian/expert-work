"""Unit tests for :class:`ToolRegistry` (Stream E.6)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pytest

from orchestrator import (
    Tool,
    ToolContext,
    ToolNotFoundError,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)


@dataclass
class _DummyTool:
    spec: ToolSpec

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        del ctx
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


# --- Stream TE-1: side_effect / idempotent metadata ------------------------


def test_side_effect_derives_read_only_when_unset_and_read_only() -> None:
    spec = ToolSpec(name="t", description="d", is_read_only=True)
    assert spec.side_effect is None
    assert spec.resolved_side_effect == "read_only"


def test_side_effect_derives_reversible_when_unset_and_not_read_only() -> None:
    # Default (write-ish) tool stays "reversible", NOT "irreversible" — a tool
    # must opt into the gated tier explicitly, preserving pre-TE-1 behaviour.
    spec = ToolSpec(name="t", description="d")
    assert spec.is_read_only is False
    assert spec.side_effect is None
    assert spec.resolved_side_effect == "reversible"


def test_explicit_side_effect_is_honoured_over_derivation() -> None:
    spec = ToolSpec(name="t", description="d", is_read_only=False, side_effect="irreversible")
    assert spec.resolved_side_effect == "irreversible"
    # Explicit value wins even when it disagrees with is_read_only.
    read_only_but_irreversible = ToolSpec(
        name="t2", description="d", is_read_only=True, side_effect="irreversible"
    )
    assert read_only_but_irreversible.resolved_side_effect == "irreversible"


def test_idempotent_defaults_false_and_is_settable() -> None:
    assert ToolSpec(name="t", description="d").idempotent is False
    assert ToolSpec(name="t", description="d", idempotent=True).idempotent is True


# --- Stream TE-6: deferred tool registry (tool RAG) ------------------------


def test_default_no_deferred_specs_returns_all() -> None:
    """Backward compat: with no deferred tools, ``specs()`` == ``all_specs()``."""
    registry = ToolRegistry()
    for name in ("alpha", "bravo"):
        registry.register(_make(name))
    assert [s.name for s in registry.specs()] == ["alpha", "bravo"]
    assert [s.name for s in registry.all_specs()] == ["alpha", "bravo"]


def test_deferred_tool_excluded_from_specs_but_in_all_specs() -> None:
    registry = ToolRegistry()
    registry.register(_make("active"))
    registry.register(_make("hidden"), deferred=True)
    assert [s.name for s in registry.specs()] == ["active"]
    assert [s.name for s in registry.all_specs()] == ["active", "hidden"]


def test_deferred_tool_is_still_dispatchable() -> None:
    """A deferred tool must remain findable so a promoted call can dispatch."""
    registry = ToolRegistry()
    hidden = _make("hidden")
    registry.register(hidden, deferred=True)
    assert "hidden" in registry
    assert registry.get("hidden") is hidden
    assert registry.get_required("hidden") is hidden
    assert len(registry) == 1


def test_deferred_specs_returns_only_deferred_names() -> None:
    registry = ToolRegistry()
    registry.register(_make("active"))
    registry.register(_make("hidden"), deferred=True)
    registry.register(_make("hidden2"), deferred=True)
    # Asking for a mix: only the deferred names come back.
    got = registry.deferred_specs(["active", "hidden", "hidden2", "missing"])
    assert sorted(s.name for s in got) == ["hidden", "hidden2"]
    # Active-only / empty asks return nothing.
    assert registry.deferred_specs(["active"]) == []
    assert registry.deferred_specs([]) == []


def _make_described(name: str, description: str) -> _DummyTool:
    return _DummyTool(spec=ToolSpec(name=name, description=description))


def test_search_only_hits_deferred_tools() -> None:
    registry = ToolRegistry()
    # An active tool whose name matches the query must NOT appear — active
    # tools are already in the bind, no need to retrieve them.
    registry.register(_make_described("github_active", "active github tool"))
    registry.register(_make_described("github_issue", "create a github issue"), deferred=True)
    matches = registry.search("github")
    assert [s.name for s in matches] == ["github_issue"]


def test_search_select_syntax_exact_names() -> None:
    registry = ToolRegistry()
    registry.register(_make("a"), deferred=True)
    registry.register(_make("b"), deferred=True)
    registry.register(_make("c"), deferred=True)
    matches = registry.search("select:a,c,missing")
    assert sorted(s.name for s in matches) == ["a", "c"]


def test_search_plus_keyword_with_extra_filters() -> None:
    registry = ToolRegistry()
    registry.register(
        _make_described("create_issue", "create a github issue on a repo"), deferred=True
    )
    registry.register(
        _make_described("close_issue", "close a github issue on a repo"), deferred=True
    )
    registry.register(_make_described("send_email", "send an email"), deferred=True)
    # +github requires 'github'; 'create' further narrows to the create tool.
    matches = registry.search("+github create")
    assert [s.name for s in matches] == ["create_issue"]


def test_search_substring_and_regex_fallback() -> None:
    registry = ToolRegistry()
    registry.register(_make_described("postgres_query", "run a SQL (read) query"), deferred=True)
    registry.register(_make_described("redis_get", "read a redis key"), deferred=True)
    # Plain substring.
    assert [s.name for s in registry.search("redis")] == ["redis_get"]
    # Valid regex.
    assert sorted(s.name for s in registry.search("post.*query")) == ["postgres_query"]
    # HX-12: an invalid regex like "(read" now hits the ranked path first —
    # the token "read" appears in both descriptions, so both come back
    # (relevance-ranked) instead of the old literal-substring single hit.
    assert {s.name for s in registry.search("(read")} == {"postgres_query", "redis_get"}
    # True substring fallback: the query shares no *token* with any document
    # (BM25 returns nothing) but is a literal substring of a description.
    registry.register(_make_described("blob_store", "handles abcdef payloads"), deferred=True)
    assert [s.name for s in registry.search("bcde")] == ["blob_store"]


def test_search_returns_empty_when_no_deferred_match() -> None:
    registry = ToolRegistry()
    registry.register(_make("active"))
    assert registry.search("active") == []
    assert registry.search("anything") == []


def test_tool_spec_defer_loading_defaults_false() -> None:
    """HX-13 (Mini-ADR HX-J2) — the vendor-disclosure marker defaults off;
    only agent_node's per-bind replace() copies ever set it."""
    spec = ToolSpec(name="x", description="y")
    assert spec.defer_loading is False
