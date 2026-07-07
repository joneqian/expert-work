"""Pre-evolution domain research (Stream SE, SE-13 / Mini-ADR SE-A24..A28).

Borrowed from agentic-harness-engineering's explore-agent: before an agent's
skills evolve, research the domain (the tenant's own knowledge base + optionally
public web docs), distil the findings into a DRAFT prior, and let the skill
generator use it as background context. Cold-start agents then start from
domain best-practice instead of from a blank trajectory slate.

Design (Mini-ADRs):

* **SE-A24 cold-start + TTL** — research runs once per ``(tenant, agent)`` on
  first evolution and is cached for ``ttl_days`` (domain knowledge changes
  slowly; per-round research would burn web quota for nothing).
* **SE-A25 product = DRAFT agent_private prior** — the finding is persisted as a
  DRAFT, agent_private skill (``evolution_origin='distilled'``), NEVER
  auto-active (SE-A0: unverified → stays DRAFT). It is a generator prior, not a
  runtime-active skill.
* **SE-A26 input = tenant KB (+ optional web)** — the tenant's own knowledge
  base (SOPs live here as documents — there is no separate SOP service) plus an
  opt-in web search. Both read ONLY the calling tenant's data.
* **SE-A27 tenant isolation** — KB + web key resolve through the tenant; never
  cross-tenant, never the platform key on the tenant's behalf (missing web key
  → KB-only, degrade no-op).
* **SE-A28 cost** — cold-start + TTL (the big lever); web off by default + a
  result cap; one aux-LLM summary; abstraction guard rejects over-specific text.

Deps are injected behind Protocols so this is unit-testable with fakes (CI has
no model key); the real KnowledgeRetriever / WebSearchTool / aux-LLM adapters
are wired by the evolution worker's lifespan (integration), mirroring SE-6d.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from expert_work.persistence.skill.base import DuplicateSkillError, SkillStore
from expert_work.protocol import DEFAULT_SKILL_LAZY_LOAD, Skill

__all__ = [
    "DomainResearchConfig",
    "DomainResearchResult",
    "DomainResearcher",
    "KbSearcher",
    "ResearchSummarizer",
    "WebSearcher",
    "needs_research",
    "research_skill_name",
]

# Reject a summary that leaked concrete identifiers (mirrors the distiller's
# abstraction guard — a prior must be type-level to generalise).
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_LONG_DIGITS_RE = re.compile(r"\d{12,}")

_SUMMARY_INSTRUCTIONS = (
    "You are researching a problem domain to brief an agent BEFORE it learns "
    "from its own runs. From the reference material below, write a concise, "
    "TYPE-LEVEL briefing of domain best practices, common pitfalls, and "
    "preconditions for this class of task. Do NOT copy concrete values, IDs, "
    "paths, timestamps, or names — keep it general and reusable."
)


class KbSearcher(Protocol):
    """Tenant knowledge-base search (adapts ``KnowledgeRetriever.search``)."""

    async def __call__(
        self, *, tenant_id: UUID, base_names: Sequence[str], query: str, limit: int
    ) -> list[str]:
        """Return up to ``limit`` relevant snippets for ``query``."""


class WebSearcher(Protocol):
    """Optional public-web search (adapts ``WebSearchTool``)."""

    async def __call__(self, *, tenant_id: UUID, query: str, max_results: int) -> list[str]:
        """Return up to ``max_results`` snippets, or ``[]`` when unavailable."""


class ResearchSummarizer(Protocol):
    """Aux-LLM summariser (adapts ``make_llm_router_aux_model``)."""

    async def __call__(self, *, prompt: str, tenant_id: UUID) -> str:
        """Summarise ``prompt`` for ``tenant_id`` and return the text."""


@dataclass(frozen=True)
class DomainResearchConfig:
    enabled: bool = False  # SE-A28 — opt-in; the worker gates on this
    web_search_enabled: bool = False  # SE-A26/A27 — off by default
    ttl_days: int = 30  # SE-A24 — cache window per (tenant, agent)
    kb_limit: int = 5
    web_max_results: int = 5


@dataclass(frozen=True)
class DomainResearchResult:
    summary: str
    n_kb_snippets: int
    n_web_snippets: int
    used_web: bool


def research_skill_name(agent_name: str) -> str:
    """Stable per-agent name for the domain-research prior skill. Sanitised to
    the skill-name slug; one prior per ``(tenant, agent)``."""
    slug = re.sub(r"[^a-z0-9-]", "-", agent_name.lower()).strip("-") or "agent"
    return f"domain-research-{slug}"[:64]


def needs_research(existing: Skill | None, *, now: datetime, ttl_days: int) -> bool:
    """SE-A24 — research when there is no prior, or the prior is older than the
    TTL. ``existing`` is the current domain-research skill for the agent (by
    :func:`research_skill_name`) or ``None``."""
    if existing is None:
        return True
    return existing.created_at < now - timedelta(days=ttl_days)


def _looks_too_specific(text: str) -> bool:
    return bool(_UUID_RE.search(text) or _LONG_DIGITS_RE.search(text))


@dataclass(frozen=True)
class DomainResearcher:
    """Researches a domain and persists the finding as a DRAFT prior skill."""

    skill_store: SkillStore
    kb_searcher: KbSearcher
    summarizer: ResearchSummarizer
    config: DomainResearchConfig = field(default_factory=DomainResearchConfig)
    web_searcher: WebSearcher | None = None

    async def research(
        self, *, tenant_id: UUID, base_names: Sequence[str], topic: str
    ) -> DomainResearchResult | None:
        """Gather KB (+ optional web) material and summarise it. Returns
        ``None`` when there is nothing to research or the summary is unusable."""
        kb_snippets = await self.kb_searcher(
            tenant_id=tenant_id, base_names=base_names, query=topic, limit=self.config.kb_limit
        )
        web_snippets: list[str] = []
        used_web = False
        if self.config.web_search_enabled and self.web_searcher is not None:
            web_snippets = await self.web_searcher(
                tenant_id=tenant_id, query=topic, max_results=self.config.web_max_results
            )
            used_web = True

        if not kb_snippets and not web_snippets:
            return None  # nothing to brief on — degrade no-op

        prompt = self._build_prompt(topic, kb_snippets, web_snippets)
        summary = (await self.summarizer(prompt=prompt, tenant_id=tenant_id)).strip()
        if not summary or _looks_too_specific(summary):
            return None
        return DomainResearchResult(
            summary=summary,
            n_kb_snippets=len(kb_snippets),
            n_web_snippets=len(web_snippets),
            used_web=used_web,
        )

    async def research_and_persist(
        self,
        *,
        tenant_id: UUID,
        agent_name: str,
        user_id: UUID | None,
        base_names: Sequence[str],
        topic: str,
        now: datetime,
    ) -> UUID | None:
        """Cold-start + TTL gated: research and persist a DRAFT prior. Returns
        the skill id, or ``None`` when skipped (fresh prior exists / disabled /
        nothing found). Never auto-activates (SE-A0)."""
        if not self.config.enabled:
            return None
        name = research_skill_name(agent_name)
        existing = await self.skill_store.get_skill_by_name(tenant_id=tenant_id, name=name)
        if not needs_research(existing, now=now, ttl_days=self.config.ttl_days):
            return None
        result = await self.research(tenant_id=tenant_id, base_names=base_names, topic=topic)
        if result is None:
            return None
        return await self._persist(
            tenant_id=tenant_id,
            agent_name=agent_name,
            user_id=user_id,
            name=name,
            existing=existing,
            summary=result.summary,
        )

    def _build_prompt(
        self, topic: str, kb_snippets: Sequence[str], web_snippets: Sequence[str]
    ) -> str:
        parts = [_SUMMARY_INSTRUCTIONS, f"\nTOPIC: {topic}"]
        if kb_snippets:
            parts.append("\n=== TENANT KNOWLEDGE BASE ===")
            parts.extend(f"- {s}" for s in kb_snippets)
        if web_snippets:
            parts.append("\n=== WEB REFERENCES ===")
            parts.extend(f"- {s}" for s in web_snippets)
        return "\n".join(parts)

    async def _persist(
        self,
        *,
        tenant_id: UUID,
        agent_name: str,
        user_id: UUID | None,
        name: str,
        existing: Skill | None,
        summary: str,
    ) -> UUID:
        """Create (or refresh) the agent's DRAFT domain-research prior skill."""
        if existing is None:
            try:
                skill = await self.skill_store.create_skill(
                    skill_id=uuid4(),
                    tenant_id=tenant_id,
                    name=name,
                    description=f"Domain research prior for {agent_name}",
                    visibility="agent_private",
                    created_by_user_id=user_id,
                    created_by_agent_name=agent_name,
                )
                skill_id = skill.id
            except DuplicateSkillError:  # pragma: no cover — raced create
                refetched = await self.skill_store.get_skill_by_name(tenant_id=tenant_id, name=name)
                if refetched is None:
                    raise
                skill_id = refetched.id
        else:
            skill_id = existing.id
        # RT-ADR-11 — refreshing an existing domain-research skill keeps its
        # disclosure mode; a freshly-created one (no prior version) adopts lazy.
        prior_versions = await self.skill_store.list_versions(
            skill_id=skill_id, tenant_id=tenant_id
        )
        inherited_lazy = prior_versions[0].lazy_load if prior_versions else DEFAULT_SKILL_LAZY_LOAD
        await self.skill_store.add_version(
            version_id=uuid4(),
            skill_id=skill_id,
            tenant_id=tenant_id,
            prompt_fragment=summary,
            description=f"Domain research prior for {agent_name}",
            authored_by="agent",
            lazy_load=inherited_lazy,
            evolution_origin="distilled",
        )
        return skill_id
