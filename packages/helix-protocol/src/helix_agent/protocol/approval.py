"""J.8 人在回路 / 审批 — DTOs.

Mini-ADR J-15 + J-24 (STREAM-J-DESIGN § 14). M0 = LangGraph
``interrupt()`` 审批节点 + 声明式 ``PolicySpec`` 门控 + agent 主动
``ask_for_approval`` 工具 + 24h 超时 fallback + audit trail.

These DTOs are the wire shape between orchestrator (the ``approval``
graph node writes :class:`ApprovalRequest` into ``AgentState``) and
control-plane (the resume endpoint takes :class:`ApprovalDecision`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "ApprovalDecision",
    "ApprovalReasonKind",
    "ApprovalRequest",
]

#: Why a run is paused for approval — borrowed from deer-flow's
#: ``ask_clarification`` taxonomy (STREAM-J-DESIGN § 14.6). Admin UI
#: (H.3) filters / sorts the approval queue by this; audit analysis
#: groups "which kind of approval is most frequent" by it.
#:
#: ``policy_gate`` is fixed for the declarative path
#: (``PolicySpec.approval_required_tools``); the other four are what
#: the agent itself passes to the ``ask_for_approval`` builtin.
ApprovalReasonKind = Literal[
    "policy_gate",
    "missing_info",
    "ambiguous_requirement",
    "approach_choice",
    "risk_confirmation",
]


class ApprovalRequest(BaseModel):
    """A run paused for human approval — lives in ``AgentState.pending_approval``.

    Produced by the ``approval`` graph node (declarative gate) or the
    ``ask_for_approval`` builtin (agent-initiated). Persisted with the
    rest of ``AgentState`` via the checkpointer, so a resume after a
    process restart still sees the pending request.
    """

    model_config = ConfigDict(frozen=True)

    request_id: str = Field(
        min_length=1,
        description=(
            "Stable id — a hash of (thread_id, node, action_summary). "
            "Deterministic so a retried interrupt does not surface the "
            "same approval twice."
        ),
    )
    node: str = Field(
        min_length=1,
        description="Graph node that raised the interrupt (e.g. 'tools', 'ask_for_approval').",
    )
    reason_kind: ApprovalReasonKind
    action_summary: str = Field(
        min_length=1,
        description="Human-readable one-liner of what is awaiting approval.",
    )
    proposed_args: dict[str, object] = Field(
        default_factory=dict,
        description="The tool-call arguments the human is approving / may modify.",
    )
    requested_at: datetime
    timeout_at: datetime = Field(
        description=(
            "``requested_at + policies.approval_timeout_s``. The "
            "approval-timeout job auto-rejects requests past this."
        ),
    )


class ApprovalDecision(BaseModel):
    """A human's verdict on a pending approval — the resume API's body.

    ``modify`` replaces the tool-call arguments with ``modified_args``
    and resumes; ``modified_args`` is required in that case and
    ignored otherwise.
    """

    model_config = ConfigDict(frozen=True)

    decision: Literal["approve", "reject", "modify"]
    modified_args: dict[str, object] | None = Field(
        default=None,
        description="Replacement tool-call args — required iff decision == 'modify'.",
    )
    decided_by: str = Field(
        min_length=1,
        description="Subject id of the human (or 'system' for the timeout job).",
    )
    reason: str | None = Field(
        default=None,
        description="Optional free-text note; the timeout job sets reason='timeout'.",
    )

    @model_validator(mode="after")
    def _check_modified_args(self) -> ApprovalDecision:
        """``modified_args`` is required for — and only for — ``modify``."""
        if self.decision == "modify" and self.modified_args is None:
            msg = "decision='modify' requires modified_args"
            raise ValueError(msg)
        if self.decision != "modify" and self.modified_args is not None:
            msg = f"modified_args is only valid with decision='modify', not {self.decision!r}"
            raise ValueError(msg)
        return self
