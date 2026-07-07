"""``find_tools`` — the tool-RAG meta-tool (Stream TE-6, HX-12 ranked).

Treats Context Bloat: tools registered as *deferred* (see
:meth:`ToolRegistry.register`) are kept out of every turn's LLM ``tools``
list. The model discovers and loads them on demand by calling
``find_tools(query)``; the matches are written to the run's
``promoted_tools`` channel (via :attr:`ToolResult.state_updates`) so the
next ``agent_node`` adds their specs to the bind, after which they are
directly callable.

Promotion lives on the LangGraph ``AgentState`` channel — per-thread and
checkpointed — so it never mutates the agent-lifetime-cached registry
(per-run isolation). Stream HX-12 upgrades retrieval to BM25-ranked
natural language (see :mod:`orchestrator.tools.ranking`), truncates
listing descriptions, labels each result with its provenance, and turns
the zero-hit answer into guidance instead of a dead end.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from expert_work.common.observability import expert_work_counter
from orchestrator.tools.registry import ToolContext, ToolRegistry, ToolResult, ToolSpec

#: Stream HX-12 — promotion-domain events. ``miss`` = a find_tools query
#: that matched nothing (a governance signal: the model wanted a
#: capability the deferred pool doesn't cover, or phrased it badly).
promotion_events = expert_work_counter(
    "expert_work_tool_promotion_total",
    "Deferred-tool promotion lifecycle events (Stream HX-12).",
    ("event",),
)

#: Stream HX-12 — cap per-result description length in the find_tools
#: listing (Hermes parity). Full descriptions stay in the registry and
#: in the per-turn bind once promoted; only the listing is truncated.
_LISTING_DESCRIPTION_MAX = 400

_NO_MATCH_GUIDANCE = (
    "No matching tools found. Try a broader natural-language query "
    "describing the capability you need (e.g. 'create a calendar event'), "
    "or one of the precise forms: 'select:name1,name2' for exact names, "
    "'+keyword extra words' to require a keyword. If nothing matches, the "
    "capability is not available — proceed without it or tell the user."
)


def _truncate(text: str) -> str:
    if len(text) <= _LISTING_DESCRIPTION_MAX:
        return text
    return text[: _LISTING_DESCRIPTION_MAX - 1] + "…"


@dataclass
class FindToolsTool:
    """Retrieves currently-unloaded (deferred) tools by query — ``find_tools``.

    Stream TE-6 — holds a reference to the same :class:`ToolRegistry` the
    graph dispatches from. :meth:`call` searches the deferred set and writes
    the matched names to ``promoted_tools`` so the next turn binds them.
    """

    registry: ToolRegistry

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="find_tools",
            description=(
                "Search for and load tools that are not currently available to "
                "you, then they become directly callable on your next step. Use "
                "this when you need a capability you don't see in your tool list "
                "(e.g. a specific integration). Describe what you need in plain "
                "language (English or Chinese) — results are relevance-ranked, "
                "best match first. Two precise forms are also supported: "
                "'select:name1,name2' loads tools by exact name, and '+keyword "
                "extra words' requires 'keyword' and filters by the remaining "
                "words. The result lists the loaded tools; call them directly "
                "afterwards — do not call find_tools again for the same tool."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "The capability you're looking for, in plain language "
                            "(relevance-ranked); or 'select:a,b' / '+keyword ...' "
                            "for precise matching."
                        ),
                    },
                },
                "required": ["query"],
            },
            # Conservative: promotes state + we keep it on the serial path so
            # the promoted_tools write is applied before any dependent call.
            is_read_only=False,
        )

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        del ctx  # find_tools needs no tenant/run binding — it reads the registry.
        raw = args.get("query")
        if not isinstance(raw, str) or not raw.strip():
            msg = "find_tools requires a non-empty 'query' string"
            raise ValueError(msg)

        matches = self.registry.search(raw)
        names = [spec.name for spec in matches]
        if not matches:
            promotion_events.labels(event="miss").inc()
            content = _NO_MATCH_GUIDANCE
        else:
            promotion_events.labels(event="promote").inc()
            listing = "\n".join(
                f"- {spec.name} [{self.registry.source_of(spec.name)}]: "
                f"{_truncate(spec.description)}"
                for spec in matches
            )
            content = f"Loaded the following tools — you can call them directly now:\n{listing}"
        return ToolResult(content=content, state_updates={"promoted_tools": names})
