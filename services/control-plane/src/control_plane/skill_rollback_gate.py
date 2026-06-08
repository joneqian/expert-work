"""Rollback gate (Stream SE, SE-7d-3a) — executes the regression-rollback decision.

The symmetric counterpart of SE-7c's :class:`PromotionGate`. Given a live skill
version + its promote-time baseline success rate, it aggregates the post-
promotion outcome window (SE-7d-1 :meth:`SkillStore.skill_run_outcomes`), runs
the SE-7d-2 judge (:func:`decide_rollback`), and on a ``ROLLBACK`` verdict:

* archives the skill (``set_status(ARCHIVED)``),
* feeds the circuit breaker ``ok=False`` on the SAME ``{tenant}:{agent}`` scope
  as auto-promote — a promote that later rolls back IS a failed promote, so a
  run of bad auto-promotes trips the breaker and degrades the channel to all-
  human (SE-A12), and
* writes the ``SKILL_EVOLUTION_ROLLED_BACK`` audit entry, whose ``details``
  carry the rollback evidence (rate / baseline / drop / p-value / n). A rollback
  is not a replay, so it does NOT write a ``skill_eval_result`` row.

The window math (``since``) and ``now`` are injected by the SE-7d-3b monitor so
this stays deterministic. The cross-tenant enumeration loop + the orchestrator
run-end emission both live in SE-7d-3b (real path, integration-validated).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from control_plane.skill_evolution_limits import CircuitBreaker
from control_plane.skill_rollback import (
    RollbackConfig,
    RollbackDecision,
    decide_rollback,
    should_rollback,
)
from helix_agent.persistence.skill.base import SkillStore
from helix_agent.protocol import AuditAction, AuditEntry, AuditResult
from helix_agent.protocol.skill import SkillStatus
from helix_agent.runtime.audit.logger import AuditLogger

__all__ = ["RollbackGate"]


def _scope_key(tenant_id: UUID, agent_name: str) -> str:
    return f"{tenant_id}:{agent_name}"


@dataclass
class RollbackGate:
    """Applies the rollback judge + side effects to one live skill version."""

    skill_store: SkillStore
    breaker: CircuitBreaker
    audit_logger: AuditLogger | None = None
    config: RollbackConfig | None = None

    async def maybe_rollback(
        self,
        *,
        skill_id: UUID,
        skill_version: int,
        tenant_id: UUID,
        agent_name: str,
        promote_baseline: float,
        since: datetime,
        now: datetime,
    ) -> RollbackDecision:
        """Decide + (if ROLLBACK) archive the version, feed the breaker, audit."""
        outcomes = await self.skill_store.skill_run_outcomes(
            skill_id=skill_id,
            skill_version=skill_version,
            tenant_id=tenant_id,
            since=since,
        )
        decision = decide_rollback(outcomes, promote_baseline=promote_baseline, config=self.config)
        if should_rollback(decision):
            await self.skill_store.set_status(
                skill_id=skill_id, tenant_id=tenant_id, status=SkillStatus.ARCHIVED
            )
            self.breaker.record(_scope_key(tenant_id, agent_name), ok=False, now=now)
            await self._audit(tenant_id, skill_id, skill_version, agent_name, decision)
        return decision

    async def _audit(
        self,
        tenant_id: UUID,
        skill_id: UUID,
        skill_version: int,
        agent_name: str,
        decision: RollbackDecision,
    ) -> None:
        if self.audit_logger is None:
            return
        await self.audit_logger.write(
            AuditEntry(
                tenant_id=tenant_id,
                actor_type="system",
                actor_id="skill-evolution-worker",
                action=AuditAction.SKILL_EVOLUTION_ROLLED_BACK,
                resource_type="skill",
                resource_id=str(skill_id),
                result=AuditResult.SUCCESS,
                details={
                    "agent_name": agent_name,
                    "skill_version": skill_version,
                    "observed_rate": round(decision.observed_rate, 4),
                    "baseline_rate": round(decision.baseline_rate, 4),
                    "drop": round(decision.drop, 4),
                    "p_value": round(decision.p_value, 6),
                    "n_cases": decision.n_cases,
                    "reason": decision.reason,
                },
            )
        )
