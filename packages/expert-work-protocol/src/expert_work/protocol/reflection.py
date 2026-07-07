"""Agent reflection models — Stream J.2 (self-critique / self-correction).

When a manifest carries a ``reflection:`` block the orchestrator inserts
a ``reflect`` graph node before the run ends: it critiques the
trajectory and either accepts the result or routes the agent back to
keep working. :class:`Reflection` records one such critique;
:class:`ReflectionSpec` is the manifest knob.

Distinct from the ``loop_detection`` middleware — that guards against
mechanical repetition; reflection guards against *semantic* drift
(stopping early, wrong answer, plan gone stale).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Reflection(BaseModel):
    """One self-critique produced by the ``reflect`` node."""

    model_config = ConfigDict(frozen=True)

    verdict: Literal["accept", "revise"] = Field(
        description="accept → finish the run; revise → route the agent back to keep working"
    )
    critique: str = Field(description="why — fed back to the agent when verdict is 'revise'")
    run_id: str | None = Field(
        default=None,
        description="the run this reflection belongs to — scopes the per-run budget "
        "(reflections accumulate across runs on a checkpointed thread)",
    )


class ReflectionSpec(BaseModel):
    """Manifest ``reflection:`` block — presence activates the reflect node."""

    model_config = ConfigDict(extra="forbid")

    budget: int = Field(
        default=2,
        gt=0,
        description="max reflection LLM calls per run — caps the reflect↔agent loop",
    )
    deadline_s: int = Field(
        default=30,
        gt=0,
        le=600,
        description=(
            "Stream K.K9 — wall-clock cap on a single reflect LLM call. "
            "If the call has not returned within this many seconds the "
            "node force-accepts (so a hung provider can't lock the run "
            "indefinitely). Cancellation-token plumbing still guards "
            "client-disconnect; this is the orthogonal time-bound on the "
            "provider itself."
        ),
    )
