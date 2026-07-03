"""SE-16 (SE-A43) — per-candidate metering context for evolution aux spend.

The evolution pipeline's aux LLM calls (distil / revise / attribute /
judge / SE-A45 screen) happen deep inside seams that only see a prompt —
they don't know which candidate they're working for. This module carries
that attribution as a :mod:`contextvars` scope the processor enters per
candidate, so the wiring-level aux adapters can write real
``token_usage`` rows: true ``tenant_id``, ``agent_name`` = the distilled
agent, and an evolution-scoped ``trace_id`` joining every call made for
one candidate.

Candidate processing is strictly sequential per worker task, so a plain
ContextVar is race-free; the async context manager restores the previous
value on exit either way.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID

from helix_agent.protocol import CurationCandidateRecord

__all__ = [
    "EVOLUTION_USAGE_KIND",
    "EvolutionMeteringContext",
    "current_metering",
    "metering_scope",
]

#: ``token_usage.usage_kind`` value for every evolution-pipeline row (aux
#: calls here + the replay builds in ``skill_evolution_wiring``).
EVOLUTION_USAGE_KIND = "skill_evolution"


@dataclass(frozen=True)
class EvolutionMeteringContext:
    """Who to bill one candidate's aux calls to."""

    tenant_id: UUID
    agent_name: str
    agent_version: str
    trace_id: str

    @classmethod
    def for_candidate(cls, candidate: CurationCandidateRecord) -> EvolutionMeteringContext:
        return cls(
            tenant_id=candidate.tenant_id,
            agent_name=candidate.agent_name,
            agent_version=candidate.agent_version or "unknown",
            # Deterministic per candidate — joins every aux call made for it,
            # and a transient-retry re-run lands on the same trace.
            trace_id=f"skill-evo-{candidate.id}",
        )


_metering_var: ContextVar[EvolutionMeteringContext | None] = ContextVar(
    "skill_evolution_metering", default=None
)


def current_metering() -> EvolutionMeteringContext | None:
    """The active candidate's metering context, or ``None`` outside a scope."""
    return _metering_var.get()


@contextmanager
def metering_scope(candidate: CurationCandidateRecord) -> Iterator[None]:
    """Attribute aux calls inside the block to ``candidate``'s tenant/agent."""
    token = _metering_var.set(EvolutionMeteringContext.for_candidate(candidate))
    try:
        yield
    finally:
        _metering_var.reset(token)
