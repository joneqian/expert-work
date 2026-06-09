"""Skill distiller (Stream SE, SE-5a) — posterior distillation from trajectories.

Given the real interaction traces behind a curation candidate, distil a
reusable :class:`SkillDraft` (name / prompt_fragment / tool_names / ...). The
draft is *not* persisted — the SE-6 worker validates + writes it as a DRAFT
version that then goes through the SE-4 replay gate.

Method (design § SE-5, externally grounded — see STREAM-SE-DESIGN "SE-5 设计依据"):

* **Contrastive induction (SkillGen)** — distil from successful runs *and*, when
  available, failed runs for contrast: capture the success procedure plus the
  failure modes to avoid (behaviours in failures absent from nearby successes),
  encoded as guardrails. Stronger than success-only.
* **Abstraction guard** — the model is told to extract the *type-level* approach,
  not the specifics of any one run; drafts whose ``prompt_fragment`` still carry
  concrete identifiers (UUIDs, long digit runs) are rejected, since a memorised
  skill degenerates into a useless fragment.
* **Posterior only (SPARK)** — at least one successful trace is required; nothing
  is invented from a plan. ``tool_names`` are filtered to the caller-supplied
  allowed set (the agent's real tools) so the model can't hallucinate tools.

The LLM is injected behind :class:`DistillerModel` (CI uses a fake; the real aux
model is wired by the SE-6 worker).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from helix_agent.protocol.skill import HIGH_RISK_TOOLS

__all__ = [
    "DistillerModel",
    "SkillDistiller",
    "SkillDraft",
    "render_trajectory",
    "tools_used",
]

# A concrete identifier that should never survive into a reusable skill body.
_HEX = "[0-9a-fA-F]"
_UUID_RE = re.compile(rf"{_HEX}{{8}}-{_HEX}{{4}}-{_HEX}{{4}}-{_HEX}{{4}}-{_HEX}{{12}}")
_LONG_DIGITS_RE = re.compile(r"\d{12,}")
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


class DistillerModel(Protocol):
    """Returns the model's raw text reply for a distillation prompt."""

    async def __call__(self, *, prompt: str, tenant_id: UUID, model: str | None = None) -> str:
        """Run ``prompt`` for ``tenant_id`` and return the raw reply text."""


@dataclass(frozen=True)
class SkillDraft:
    """A distilled, not-yet-persisted skill (the SE-6 worker writes the DRAFT)."""

    name: str
    prompt_fragment: str
    tool_names: tuple[str, ...]
    description: str
    category: str | None
    high_risk: bool


def tools_used(messages: Sequence[Mapping[str, Any]]) -> frozenset[str]:
    """Collect the tool names invoked across a ShareGPT-shaped trajectory."""
    names: set[str] = set()
    for msg in messages:
        for call in msg.get("tool_calls", []) or []:
            name = call.get("name")
            if isinstance(name, str) and name:
                names.add(name)
    return frozenset(names)


def render_trajectory(messages: Sequence[Mapping[str, Any]]) -> str:
    """Render a ShareGPT-shaped trajectory to readable ``role: content`` text."""
    lines: list[str] = []
    for msg in messages:
        role = str(msg.get("role", "?"))
        content = str(msg.get("content", "")).strip()
        if content:
            lines.append(f"{role}: {content}")
        for call in msg.get("tool_calls", []) or []:
            lines.append(f"{role} -> tool {call.get('name', '?')}({call.get('args', {})})")
    return "\n".join(lines)


_INSTRUCTIONS = (
    "You distil a reusable agent skill from real interaction traces.\n"
    "Extract the GENERAL, type-level procedure that solves this CLASS of task — "
    "NOT the specifics of any single run. Do NOT copy concrete values, IDs, file "
    "paths, timestamps, or names from the traces.\n"
    "Use the SUCCESSFUL runs for the working procedure and (when present) the "
    "FAILED runs for contrast: encode behaviours that appear in failures but not "
    "in nearby successes as guardrails.\n"
    'Return ONLY a JSON object: {"name": "<kebab-case>", "prompt_fragment": '
    '"<reusable how-to with guardrails>", "tool_names": ["..."], "description": '
    '"<one line>", "category": "<string or null>"}'
)


def _build_prompt(successes: Sequence[str], failures: Sequence[str], hint_text: str = "") -> str:
    parts = [_INSTRUCTIONS]
    # Stream SE — SE-14: a Best-of-N strategy hint steers emphasis only; it
    # never relaxes the hard guards (allowed_tools cap + _looks_too_specific).
    if hint_text:
        parts.append(hint_text)
    parts.append("\n=== SUCCESSFUL RUNS ===")
    parts.extend(f"--- success {i + 1} ---\n{text}" for i, text in enumerate(successes))
    if failures:
        parts.append("\n=== FAILED RUNS (for contrast) ===")
        parts.extend(f"--- failure {i + 1} ---\n{text}" for i, text in enumerate(failures))
    return "\n".join(parts)


def _parse_object(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def _looks_too_specific(fragment: str) -> bool:
    return bool(_UUID_RE.search(fragment) or _LONG_DIGITS_RE.search(fragment))


@dataclass(frozen=True)
class SkillDistiller:
    """Distils a :class:`SkillDraft` from rendered trajectory text."""

    model: DistillerModel
    model_name: str | None = None

    async def distill(
        self,
        *,
        tenant_id: UUID,
        successes: Sequence[str],
        failures: Sequence[str] = (),
        allowed_tools: frozenset[str] | None = None,
        hint_text: str = "",
    ) -> SkillDraft | None:
        """Distil a draft, or return ``None`` if the traces yield nothing usable.

        ``successes`` / ``failures`` are pre-rendered trajectory texts (use
        :func:`render_trajectory`). At least one success is required (posterior
        only). ``allowed_tools`` (when given) caps ``tool_names`` to the agent's
        real tools. ``hint_text`` (Stream SE — SE-14) is a Best-of-N strategy
        angle appended to the prompt; emphasis only, never relaxes the guards.
        """
        if not successes:
            return None

        prompt = _build_prompt(successes, failures, hint_text)
        raw = await self.model(prompt=prompt, tenant_id=tenant_id, model=self.model_name)
        obj = _parse_object(raw)
        if obj is None:
            return None

        name = obj.get("name")
        fragment = obj.get("prompt_fragment")
        if not isinstance(name, str) or not _NAME_RE.match(name):
            return None
        if not isinstance(fragment, str) or not fragment.strip():
            return None
        if _looks_too_specific(fragment):
            return None

        raw_tools = obj.get("tool_names") or []
        tool_names = tuple(
            dict.fromkeys(
                t
                for t in raw_tools
                if isinstance(t, str) and t and (allowed_tools is None or t in allowed_tools)
            )
        )
        description = obj.get("description")
        category = obj.get("category")

        return SkillDraft(
            name=name,
            prompt_fragment=fragment.strip(),
            tool_names=tool_names,
            description=description if isinstance(description, str) else "",
            category=category if isinstance(category, str) and category else None,
            high_risk=bool(HIGH_RISK_TOOLS & set(tool_names)),
        )
