"""Tool Protocol + ``ToolRegistry`` — Stream E.6.

Concrete tool adapters (``web_search`` E.7, ``http`` E.8, ``mcp:*`` E.9,
``exec_python`` F.4) all implement :class:`Tool` and register here. The
ReAct graph (``orchestrator.graph_builder``) reads
:meth:`ToolRegistry.specs` to hand the LLM the list of callable tools,
and dispatches by name via :meth:`ToolRegistry.get`.

Tool ``call`` exceptions are wrapped into ``ToolMessage(error=...)`` by
the graph's ``tools`` node (per Mini-ADR E-12 in
[STREAM-E-DESIGN](../../../../../docs/streams/STREAM-E-DESIGN.md)) —
adapters can raise freely; the LLM sees the error as a tool result and
reasons about retry / different args / final answer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ToolSpec:
    """Static descriptor of a tool — handed to the LLM for tool selection."""

    name: str
    description: str
    #: JSON Schema for the tool's ``args`` parameter.
    parameters: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    """Result of a successful tool dispatch.

    ``content`` is fed back to the LLM as a ``ToolMessage`` body.
    ``meta`` carries truncation flags and any per-tool metadata (per
    Mini-ADR E-10 — caller knows e.g. ``meta.truncated=True`` ↔ output
    was cut).
    """

    content: str
    meta: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class Tool(Protocol):
    """Async callable wrapped with its static spec."""

    spec: ToolSpec

    async def call(self, args: Mapping[str, Any]) -> ToolResult: ...


class ToolNotFoundError(KeyError):
    """Raised by :meth:`ToolRegistry.get_required` when ``name`` isn't
    registered. The graph's ``tools`` node turns this into a
    ``ToolMessage(error=...)`` rather than propagating."""


class ToolRegistry:
    """In-memory tool catalogue.

    M0 instantiates one per ``orchestrator`` process at startup;
    register all tools available to any agent. Per-agent / per-tenant
    filtering (``http_tool_allowlist`` / ``mcp_servers``) happens at
    dispatch / spec-resolution time — the registry itself is just a
    lookup table.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool by its spec ``name``. Re-registering replaces."""
        self._tools[tool.spec.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def get_required(self, name: str) -> Tool:
        tool = self._tools.get(name)
        if tool is None:
            msg = f"unknown tool: {name!r}"
            raise ToolNotFoundError(msg)
        return tool

    def specs(self) -> list[ToolSpec]:
        """Specs in registration order — handed to the LLM."""
        return [tool.spec for tool in self._tools.values()]

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._tools

    def __len__(self) -> int:
        return len(self._tools)
