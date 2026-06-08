"""Failure attribution (Stream SE, SE-5b) — content error vs execution lapse.

When a candidate skill fails replay (SE-4), we must decide *why* before acting:

* **content_error** — the skill's content (wrong / misleading instructions)
  caused the failure → feed back to the SE-6 co-evolve loop to revise the draft.
* **execution_error** — an execution / environment lapse unrelated to the skill's
  correctness (timeout, network, sandbox, missing dependency, ...) → **discard**
  the signal, never feed it back (EmbodiSkill + hermes "don't capture" list; this
  is what stops the loop from self-reinforcing on noise — anti-collapse).

Method (design § SE-5, externally grounded):

* **Rule prefilter (programmatic, SkillsBench-style)** — unambiguous environment
  failures are classified as ``execution_error`` cheaply, without an LLM call.
  The patterns are anchored on the Aegis agent-environment failure taxonomy +
  hermes don't-capture list. Ambiguous signals (e.g. an HTTP 404, which a bad
  skill could just as well have caused) are deliberately *not* ruled here.
* **LLM fallback (LLM-as-judge, Terminal-Bench-style)** — only when no rule
  fires: a single All-at-Once judgement of content vs execution. We do **not**
  attempt step-level localization (its accuracy ceiling is ~14%). When the LLM
  is unparseable or ambiguous we default to ``execution_error`` — biased toward
  *not learning*, the safe direction against collapse.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

__all__ = [
    "AttributionModel",
    "AttributionVerdict",
    "FailureKind",
    "FailureSignal",
    "SkillAttributor",
    "should_feed_back",
]


class FailureKind(StrEnum):
    CONTENT = "content_error"  # skill content caused it -> co-evolve revise
    EXECUTION = "execution_error"  # execution/environment lapse -> discard, don't learn


@dataclass(frozen=True)
class FailureSignal:
    """What replay / the trajectory observed about a failure (SE-4b retains this)."""

    error_text: str = ""
    exit_phase: str | None = None
    tool_errors: tuple[str, ...] = ()
    timed_out: bool = False


@dataclass(frozen=True)
class AttributionVerdict:
    kind: FailureKind
    by_rule: bool
    reason: str


class AttributionModel(Protocol):
    """Returns the model's raw text reply for an attribution prompt."""

    async def __call__(self, *, prompt: str, tenant_id: UUID, model: str | None = None) -> str:
        """Run ``prompt`` for ``tenant_id`` and return the raw reply text."""


# Unambiguous environment / infrastructure failure markers (Aegis-anchored).
# Deliberately excludes ambiguous signals (404 / 500) that a bad skill could
# cause — those go to the LLM.
_ENV_MARKERS: tuple[str, ...] = (
    "timeout",
    "timed out",
    "connection refused",
    "econnrefused",
    "connection reset",
    "could not resolve",
    "name resolution",
    "dns",
    "rate limit",
    "429",
    "503 service unavailable",
    "service unavailable",
    "sandbox",
    "quota exceeded",
    "modulenotfounderror",
    "no module named",
    "importerror",
    "permission denied",
    "eacces",
    "out of memory",
    "oom",
    "no space left",
)

# Run phases that are environment setup, not skill content.
_ENV_PHASES: frozenset[str] = frozenset({"setup", "environment", "sandbox", "provisioning"})


def _rule_execution_reason(signal: FailureSignal) -> str | None:
    """Return a reason if this is *unambiguously* an execution/environment error."""
    if signal.timed_out:
        return "timed out (environment)"
    if signal.exit_phase and signal.exit_phase.lower() in _ENV_PHASES:
        return f"failed in {signal.exit_phase} phase (environment)"
    haystack = " ".join((signal.error_text, *signal.tool_errors)).lower()
    for marker in _ENV_MARKERS:
        if marker in haystack:
            return f"environment marker: {marker!r}"
    return None


def _attribution_prompt(
    signal: FailureSignal, skill_prompt: str, skill_tools: Sequence[str]
) -> str:
    tools = ", ".join(skill_tools) or "(none)"
    return (
        "A candidate agent skill failed during replay. Decide the cause:\n"
        "- content_error: the SKILL's instructions are wrong or misleading and "
        "caused the failure.\n"
        "- execution_error: an execution/environment lapse unrelated to the "
        "skill's correctness (flaky tool, transient error, env issue).\n\n"
        f"Skill instructions:\n{skill_prompt}\n\n"
        f"Skill tools: {tools}\n\n"
        f"Failure phase: {signal.exit_phase or '(unknown)'}\n"
        f"Error: {signal.error_text or '(none)'}\n"
        f"Tool errors: {'; '.join(signal.tool_errors) or '(none)'}\n\n"
        "Answer with exactly one token: content_error or execution_error."
    )


def _parse_kind(reply: str) -> FailureKind | None:
    text = reply.lower()
    has_content = "content_error" in text
    has_execution = "execution_error" in text
    if has_content and not has_execution:
        return FailureKind.CONTENT
    if has_execution and not has_content:
        return FailureKind.EXECUTION
    return None  # neither, or both → undecided


def should_feed_back(verdict: AttributionVerdict) -> bool:
    """Only content errors are fed back to co-evolve; execution errors are dropped."""
    return verdict.kind is FailureKind.CONTENT


@dataclass(frozen=True)
class SkillAttributor:
    """Classifies a failed replay as content vs execution error."""

    model: AttributionModel
    model_name: str | None = None

    async def attribute(
        self,
        *,
        tenant_id: UUID,
        signal: FailureSignal,
        skill_prompt: str,
        skill_tools: Sequence[str] = (),
    ) -> AttributionVerdict:
        """Attribute a replay failure (rule prefilter first, then LLM fallback)."""
        ruled = _rule_execution_reason(signal)
        if ruled is not None:
            return AttributionVerdict(kind=FailureKind.EXECUTION, by_rule=True, reason=ruled)

        prompt = _attribution_prompt(signal, skill_prompt, skill_tools)
        raw = await self.model(prompt=prompt, tenant_id=tenant_id, model=self.model_name)
        kind = _parse_kind(raw)
        if kind is None:
            return AttributionVerdict(
                kind=FailureKind.EXECUTION,
                by_rule=False,
                reason="LLM undecided -> default execution (do not learn)",
            )
        return AttributionVerdict(kind=kind, by_rule=False, reason=f"LLM: {kind.value}")
