"""``ask_for_approval`` builtin tool — Stream J.8 (Mini-ADR J-24).

The agent-initiated half of the J.8 approval design (STREAM-J-DESIGN
§ 14.5). The declarative gate (``PolicySpec.approval_required_tools``)
is platform-enforced; this builtin is the *agent's* own escape hatch —
when it hits a run-time decision point it is unsure about, it calls
``ask_for_approval`` to pause for a human verdict.

The tool is **not dispatched like a normal tool**: ``tools_node``
special-cases a call to it (by name) *before* the parallel staging,
turning it into an :class:`~expert_work.protocol.approval.ApprovalRequest`
and pausing the run (see ``graph_builder/_approval.py``). This class
exists purely so the LLM sees ``ask_for_approval`` in its tool list;
:meth:`call` is a defensive guard that should never run.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from orchestrator.tools.registry import ToolContext, ToolResult, ToolSpec

#: The agent-initiated approval builtin's tool name. ``tools_node``
#: special-cases a call to it: instead of dispatching through the
#: registry, it pauses the run for a human verdict (STREAM-J-DESIGN
#: § 14.5). Defined here (the tool's home, a leaf module) so
#: ``graph_builder/_approval`` can import it without a package cycle.
ASK_FOR_APPROVAL_TOOL = "ask_for_approval"


@dataclass
class AskForApprovalTool:
    """Lets an agent pause itself for human approval — ``ask_for_approval``."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=ASK_FOR_APPROVAL_TOOL,
            description=(
                "Pause and ask a human to approve, reject, or modify what "
                "you are about to do. Use this when you hit a decision you "
                "should not make unilaterally — a risky / irreversible "
                "action, missing information, or a genuine fork between "
                "approaches. The run pauses until a human responds."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "reason_kind": {
                        "type": "string",
                        "enum": [
                            "missing_info",
                            "ambiguous_requirement",
                            "approach_choice",
                            "risk_confirmation",
                        ],
                        "description": "Why you need a human — pick the closest category.",
                    },
                    "action_summary": {
                        "type": "string",
                        "description": "One-line summary of what awaits the human's verdict.",
                    },
                    "proposed_args": {
                        "type": "object",
                        "description": (
                            "The concrete values you propose — the human may "
                            "approve them as-is or hand back modified ones."
                        ),
                    },
                },
                "required": ["reason_kind", "action_summary"],
            },
            # ``ask_for_approval`` writes the run's ``pending_approval``
            # state; it is never safe to run alongside another tool.
            is_read_only=False,
        )

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        """Defensive guard — ``tools_node`` intercepts this tool before dispatch.

        Reaching here means the approval pre-check in ``tools_node`` was
        bypassed (a wiring bug). Return an error result rather than
        silently doing nothing.
        """
        del args, ctx
        return ToolResult(
            content=(
                "[tool error] ask_for_approval must be handled by the "
                "approval gate, not dispatched directly"
            ),
        )
