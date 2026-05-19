"""Unit tests for the J.4 sub-agent scaffold — ``ChildAgentBuilder`` + depth cap."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from orchestrator.tools import MAX_SUBAGENT_DEPTH, ChildAgentBuilder, ToolEnv


class _FakeChildAgentBuilder:
    """Conforms to :class:`ChildAgentBuilder` — async ``__call__`` with the
    keyword-only signature. PR3's ``SubAgentTool`` tests reuse this shape."""

    async def __call__(
        self,
        *,
        tenant_id: UUID,
        name: str,
        version: str,
        depth: int,
    ) -> Any:
        return None


def test_max_subagent_depth_is_3() -> None:
    assert MAX_SUBAGENT_DEPTH == 3


def test_child_agent_builder_protocol_accepts_conforming_callable() -> None:
    # runtime_checkable — a class with an async __call__ satisfies the
    # Protocol, so the control-plane's injected callback type-checks.
    assert isinstance(_FakeChildAgentBuilder(), ChildAgentBuilder)


def test_child_agent_builder_protocol_rejects_non_callable() -> None:
    assert not isinstance(object(), ChildAgentBuilder)


def test_tool_env_child_agent_builder_defaults_none() -> None:
    # An empty ToolEnv has no sub-agent builder — a manifest declaring
    # subagents against it raises AgentFactoryError (wired in J.4 PR4).
    assert ToolEnv().child_agent_builder is None


def test_tool_env_carries_child_agent_builder() -> None:
    builder = _FakeChildAgentBuilder()
    env = ToolEnv(child_agent_builder=builder)
    assert env.child_agent_builder is builder
