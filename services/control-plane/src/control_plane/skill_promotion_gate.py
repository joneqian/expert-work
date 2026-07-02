"""Promotion gate (Stream SE, SE-7c) — executes the auto-promote decision.

Wraps the pure SE-7a policy (:func:`decide_promotion`) with the SE-7b guardrail
state (rate limiter + circuit breaker) and the side effects: on an
``AUTO_PROMOTE`` it flips the DRAFT skill to ACTIVE, records the rate event, and
writes the ``SKILL_EVOLUTION_AUTO_PROMOTED`` audit entry. Everything else stays
DRAFT for human review.

The clock is injected (``now``) so the guardrail bookkeeping is deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from control_plane.skill_evolution_limits import CircuitBreaker, RateLimiter
from control_plane.skill_promotion import PromoteDecision, decide_promotion, should_auto_promote
from helix_agent.persistence.skill.base import SkillStore
from helix_agent.protocol import AuditAction, AuditEntry, AuditResult, CurationCandidateRecord
from helix_agent.protocol.eval_dataset import CurationSignal
from helix_agent.protocol.skill import SkillStatus
from helix_agent.runtime.audit.logger import AuditLogger

__all__ = ["WEAK_LABEL_SIGNALS", "PromotionGate"]

#: SE-16 (SE-A44) — weak-label candidate origins whose distillates never
#: auto-promote, however strong the replay evidence: the input labels are
#: inferred, so human review accumulates trust first. Explicit 👍 stays
#: auto-eligible (human endorsement + independent replay verification).
WEAK_LABEL_SIGNALS: frozenset[CurationSignal] = frozenset({"implicit_success"})


def _scope_key(tenant_id: UUID, agent_name: str) -> str:
    return f"{tenant_id}:{agent_name}"


@dataclass
class PromotionGate:
    """Applies the auto-promote policy + guardrails to a grounded DRAFT."""

    skill_store: SkillStore
    rate_limiter: RateLimiter
    breaker: CircuitBreaker
    audit_logger: AuditLogger | None = None

    async def maybe_promote(
        self,
        *,
        candidate: CurationCandidateRecord,
        skill_id: UUID,
        auto_promote_eligible: bool,
        high_risk: bool,
        now: datetime,
    ) -> PromoteDecision:
        """Decide + (if AUTO_PROMOTE) flip the DRAFT to ACTIVE. Always grounded."""
        key = _scope_key(candidate.tenant_id, candidate.agent_name)
        # SE-16 (SE-A44) — a weak-label origin (implicit_success) caps the
        # path at human review regardless of replay strength.
        if candidate.signal in WEAK_LABEL_SIGNALS:
            auto_promote_eligible = False
        # SE-8 (SE-A13c) — persistent manual emergency stop. The worker runs
        # cross-tenant (owner / bypass), so this reads the global NULL-tenant
        # row alongside the candidate's tenant row.
        evolution_halted = await self.skill_store.is_evolution_halted(tenant_id=candidate.tenant_id)
        decision = decide_promotion(
            grounded=True,
            auto_promote_eligible=auto_promote_eligible,
            high_risk=high_risk,
            breaker_open=self.breaker.is_open(key, now),
            within_rate_limit=self.rate_limiter.within_limit(key, now),
            evolution_halted=evolution_halted,
        )
        if should_auto_promote(decision):
            await self.skill_store.set_status(
                skill_id=skill_id, tenant_id=candidate.tenant_id, status=SkillStatus.ACTIVE
            )
            self.rate_limiter.record(key, now)
            await self._audit(candidate, skill_id)
        return decision

    async def _audit(self, candidate: CurationCandidateRecord, skill_id: UUID) -> None:
        if self.audit_logger is None:
            return
        await self.audit_logger.write(
            AuditEntry(
                tenant_id=candidate.tenant_id,
                actor_type="system",
                actor_id="skill-evolution-worker",
                action=AuditAction.SKILL_EVOLUTION_AUTO_PROMOTED,
                resource_type="skill",
                resource_id=str(skill_id),
                result=AuditResult.SUCCESS,
                details={"agent_name": candidate.agent_name, "candidate_id": str(candidate.id)},
            )
        )
