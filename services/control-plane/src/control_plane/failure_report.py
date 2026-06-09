"""Layered, source-linked failure report (Stream SE, SE-12 / Mini-ADR SE-A20..A23).

Borrowed from agentic-harness-engineering's Agent Debugger: instead of a flat
content/execution verdict, compress a batch of failed cases into a two-layer,
drill-downable report — an ``overview`` of root-cause clusters plus per-task
``details`` whose every claim carries a source link back to the trajectory.

Adds a layer, does NOT replace attribution (SE-A20/A23): the report is an INPUT
SIGNAL for the generator + a review artifact — it never gates promotion
(``decide_grounding`` stays the single收口, SE-A0). Attribution still emits the
machine ``FailureKind`` used elsewhere.

Cost (SE-A21): clustering is programmatic (zero tokens) — cases bucket by
``(attribution_kind, exit_phase, first tool/marker)``; each bucket then gets ONE
aux-LLM naming call over a few exemplars. Rule-attributed (environment) buckets
skip the LLM entirely (template narrative). So tokens scale with bucket count,
not case count.

PII (SE-A22): the summariser is told to stay type-level, and any summary that
leaks a concrete identifier (UUID / long digit run) is dropped to a template —
the same abstraction guard the distiller uses. Evidence snippets are length-
capped.

The aux LLM is injected behind :class:`ReportSummarizer` (CI uses a fake; the
real aux model is wired by the evolution worker, integration).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from control_plane.skill_attribution import FailureSignal, SkillAttributor

__all__ = [
    "EvidenceRef",
    "FailureCase",
    "FailureReport",
    "FailureReportBuilder",
    "FailureReportConfig",
    "FailureReportDetail",
    "ReportSummarizer",
    "RootCauseCluster",
]

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_LONG_DIGITS_RE = re.compile(r"\d{12,}")
_SNIPPET_CAP = 240


def _looks_too_specific(text: str) -> bool:
    return bool(_UUID_RE.search(text) or _LONG_DIGITS_RE.search(text))


class ReportSummarizer(Protocol):
    """Aux-LLM call that names a cluster + proposes a guard from exemplars."""

    async def __call__(self, *, prompt: str, tenant_id: UUID) -> str:
        """Return a short ``summary`` line; the caller derives the guard."""


# ── DTOs (control-plane-internal; serialised to an ObjectStore JSON blob) ──


class EvidenceRef(BaseModel):
    """A source link from a report claim back to a trajectory message."""

    model_config = ConfigDict(frozen=True)

    trajectory_key: str
    message_index: int = Field(ge=0)
    tool_call_id: str | None = None
    snippet: str = ""


class RootCauseCluster(BaseModel):
    model_config = ConfigDict(frozen=True)

    cluster_id: str
    kind: str  # FailureKind value (content / execution)
    summary: str
    n_tasks: int = Field(ge=0)
    exemplar_task_ids: tuple[str, ...] = ()
    suggested_guard: str = ""


class FailureReportDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str
    trajectory_key: str
    attribution_kind: str
    by_rule: bool
    narrative: str
    evidence_refs: tuple[EvidenceRef, ...] = ()


class FailureReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    report_id: UUID
    tenant_id: UUID | None
    scope: str  # "candidate" | "skill_version"
    scope_ref: str
    generated_at: datetime
    n_tasks_total: int = Field(ge=0)
    clusters: tuple[RootCauseCluster, ...] = ()
    details: tuple[FailureReportDetail, ...] = ()


@dataclass(frozen=True)
class FailureCase:
    """One failed case fed to the builder."""

    task_id: str
    trajectory_key: str
    signal: FailureSignal
    message_index: int = 0
    tool_call_id: str | None = None
    snippet: str = ""


@dataclass(frozen=True)
class FailureReportConfig:
    exemplars_per_cluster: int = 3
    max_clusters: int = 12


@dataclass(frozen=True)
class FailureReportBuilder:
    """Builds a :class:`FailureReport` from attributed failed cases."""

    attributor: SkillAttributor  # rule-first content/execution classifier
    summarizer: ReportSummarizer
    config: FailureReportConfig = field(default_factory=FailureReportConfig)

    async def build(
        self,
        *,
        tenant_id: UUID,
        scope: str,
        scope_ref: str,
        cases: Sequence[FailureCase],
        now: datetime,
    ) -> FailureReport:
        # 1. Attribute each case (rule-first; LLM fallback inside the attributor).
        attributed: list[tuple[FailureCase, str, bool, str]] = []
        for case in cases:
            verdict = await self.attributor.attribute(
                tenant_id=tenant_id, signal=case.signal, skill_prompt="", skill_tools=()
            )
            attributed.append((case, verdict.kind.value, verdict.by_rule, verdict.reason))

        # 2. Programmatic pre-cluster (zero tokens).
        buckets: dict[tuple[str, str, str], list[tuple[FailureCase, str, bool, str]]] = {}
        for item in attributed:
            case, kind, _by_rule, _reason = item
            buckets.setdefault(self._bucket_key(case, kind), []).append(item)

        # 3. One naming call per bucket (rule/env buckets skip the LLM).
        clusters: list[RootCauseCluster] = []
        for i, (key, items) in enumerate(sorted(buckets.items())[: self.config.max_clusters]):
            clusters.append(await self._name_cluster(tenant_id, f"c{i}", key, items))

        details = tuple(
            FailureReportDetail(
                task_id=case.task_id,
                trajectory_key=case.trajectory_key,
                attribution_kind=kind,
                by_rule=by_rule,
                narrative=self._detail_narrative(kind, by_rule, reason),
                evidence_refs=(
                    EvidenceRef(
                        trajectory_key=case.trajectory_key,
                        message_index=case.message_index,
                        tool_call_id=case.tool_call_id,
                        snippet=case.snippet[:_SNIPPET_CAP],
                    ),
                ),
            )
            for case, kind, by_rule, reason in attributed
        )
        return FailureReport(
            report_id=uuid4(),
            tenant_id=tenant_id,
            scope=scope,
            scope_ref=scope_ref,
            generated_at=now,
            n_tasks_total=len(cases),
            clusters=tuple(clusters),
            details=details,
        )

    def _bucket_key(self, case: FailureCase, kind: str) -> tuple[str, str, str]:
        phase = (case.signal.exit_phase or "").lower()
        marker = case.signal.tool_errors[0] if case.signal.tool_errors else ""
        if not marker and case.signal.timed_out:
            marker = "timeout"
        return (kind, phase, marker[:48])

    async def _name_cluster(
        self,
        tenant_id: UUID,
        cluster_id: str,
        key: tuple[str, str, str],
        items: list[tuple[FailureCase, str, bool, str]],
    ) -> RootCauseCluster:
        kind, phase, marker = key
        task_ids = tuple(c.task_id for c, _, _, _ in items)
        exemplars = items[: self.config.exemplars_per_cluster]
        all_by_rule = all(by_rule for _, _, by_rule, _ in exemplars)

        if all_by_rule:
            # Environment/execution rule hit → template, zero LLM (SE-A21).
            summary = f"{kind} failures in '{phase or 'unknown'}' phase"
            if marker:
                summary += f" (marker: {marker})"
            guard = "Environment/execution failure — not a skill-content fix; check the runtime."
            return RootCauseCluster(
                cluster_id=cluster_id,
                kind=kind,
                summary=summary,
                n_tasks=len(items),
                exemplar_task_ids=task_ids[: self.config.exemplars_per_cluster],
                suggested_guard=guard,
            )

        prompt = self._cluster_prompt(kind, phase, marker, exemplars)
        raw = (await self.summarizer(prompt=prompt, tenant_id=tenant_id)).strip()
        summary, guard = self._split_summary_guard(raw)
        if not summary or _looks_too_specific(summary) or _looks_too_specific(guard):
            summary = f"{kind} failures in '{phase or 'unknown'}' phase"
            guard = ""
        return RootCauseCluster(
            cluster_id=cluster_id,
            kind=kind,
            summary=summary,
            n_tasks=len(items),
            exemplar_task_ids=task_ids[: self.config.exemplars_per_cluster],
            suggested_guard=guard,
        )

    def _cluster_prompt(
        self,
        kind: str,
        phase: str,
        marker: str,
        exemplars: list[tuple[FailureCase, str, bool, str]],
    ) -> str:
        lines = [
            "Summarise the SHARED root cause of these failed agent runs in one "
            "type-level sentence, then propose one guardrail. Do NOT copy concrete "
            "values, IDs, paths, or names. Reply as 'SUMMARY: ...\\nGUARD: ...'.",
            f"kind={kind} phase={phase or 'unknown'} marker={marker or 'none'}",
        ]
        for case, _kind, _by_rule, reason in exemplars:
            lines.append(f"- {reason or case.signal.error_text or '(no detail)'}")
        return "\n".join(lines)

    @staticmethod
    def _split_summary_guard(raw: str) -> tuple[str, str]:
        summary, guard = raw, ""
        for line in raw.splitlines():
            s = line.strip()
            if s.upper().startswith("SUMMARY:"):
                summary = s[len("SUMMARY:") :].strip()
            elif s.upper().startswith("GUARD:"):
                guard = s[len("GUARD:") :].strip()
        return summary, guard

    @staticmethod
    def _detail_narrative(kind: str, by_rule: bool, reason: str) -> str:
        if by_rule:
            return f"[{kind}] {reason}" if reason else f"[{kind}] environment/execution failure"
        return reason or f"[{kind}] failure"
