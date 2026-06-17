"""Shared trigger-firing logic — Stream J.10 (Mini-ADR J-26 / J-42).

Both the cron scheduler and the webhook ingest endpoint start an agent
run from a trigger. :func:`fire_trigger` is that shared path — it
resolves + builds the agent, opens a fresh thread, spawns the
``run_agent`` worker (no SSE consumer), stamps ``last_fired_at``, and
emits a ``TRIGGER_FIRE`` audit row.

It returns the new ``run_id`` (or ``None`` on a preflight failure). The
caller owns the ``trigger_run`` row — a fresh fire creates one, a DLQ
retry updates the existing one — so this function is reused unchanged
by both the first fire and every retry.

The caller owns the RLS context — ``fire_trigger`` runs entirely within
the trigger's own tenant scope, set by the caller before the call.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from control_plane.audit import emit
from control_plane.runtime import AgentRuntime
from control_plane.uplift.threat_metrics import (
    record_threat_pattern_hits,
    record_threat_scan,
    record_trigger_blocked,
)
from helix_agent.common.observability import current_trace_id_hex, helix_counter
from helix_agent.common.threat_patterns import ThreatFinding, scan_for_threats
from helix_agent.persistence import ApprovalStore, ThreadMetaStore, TriggerStore
from helix_agent.persistence.agent_spec import AgentSpecStore
from helix_agent.persistence.tenant_config import TenantConfigStore
from helix_agent.protocol import AgentSpecStatus, AuditAction, TriggerRecord
from helix_agent.runtime.audit.logger import AuditLogger
from orchestrator import AgentFactoryError, run_agent

logger = logging.getLogger("helix.control_plane.trigger_firing")

#: Triggers fired into a run — cron + webhook share this counter.
_triggers_fired = helix_counter(
    "helix_control_plane_triggers_fired_total",
    "Triggers that started an agent run.",
)


async def _resolve_fire_scan_mode(
    *,
    tenant_id: UUID,
    tenant_config_store: TenantConfigStore | None,
) -> str:
    """Read ``tenant_config.trigger_fire_scan_mode``; default ``warn``.

    No tenant_config row or no store → ``warn`` (platform-wide default).
    """
    if tenant_config_store is None:
        return "warn"
    record = await tenant_config_store.get(tenant_id=tenant_id)
    if record is None:
        return "warn"
    return record.trigger_fire_scan_mode


def _finding_to_dict(f: ThreatFinding) -> dict[str, Any]:
    return {
        "pattern_id": f.pattern_id,
        "category": f.category,
        "severity": f.severity,
        "excerpt": f.excerpt,
    }


async def _scan_fire_time(
    *,
    seed_text: str,
    trigger: TriggerRecord,
    audit_logger: AuditLogger,
    tenant_config_store: TenantConfigStore | None,
) -> bool:
    """Context-scope scan; return True if firing should be aborted (block).

    Emits one of:

    - ``trigger:prompt_injection_warn`` + return False (continue firing) when
      ``tenant_config.trigger_fire_scan_mode == "warn"`` (default).
    - ``trigger:prompt_injection_blocked`` + return True (abort) when mode is
      ``"block"``.

    Clean payload → no audit row, returns False. Per Mini-ADR U-2 Layer B.
    """
    findings = scan_for_threats(seed_text, scope="context")
    if not findings:
        record_threat_scan(scope="context", result="clean")
        return False

    mode = await _resolve_fire_scan_mode(
        tenant_id=trigger.tenant_id, tenant_config_store=tenant_config_store
    )
    record_threat_pattern_hits(findings, scope="context")
    record_threat_scan(scope="context", result="blocked" if mode == "block" else "warned")
    action = (
        AuditAction.TRIGGER_PROMPT_INJECTION_BLOCKED
        if mode == "block"
        else AuditAction.TRIGGER_PROMPT_INJECTION_WARN
    )
    await emit(
        audit_logger,
        tenant_id=trigger.tenant_id,
        actor_id=f"trigger:{trigger.id}",
        action=action,
        resource_type="trigger",
        resource_id=str(trigger.id),
        trace_id=current_trace_id_hex(),
        details={
            "scope": "context",
            "mode": mode,
            "pattern_count": len(findings),
            "findings": [_finding_to_dict(f) for f in findings],
        },
    )
    if mode == "block":
        record_trigger_blocked(phase="fire")
        logger.error(
            "trigger_firing.scan_blocked",
            extra={"trigger_id": str(trigger.id), "pattern_count": len(findings)},
        )
        return True
    logger.warning(
        "trigger_firing.scan_warned",
        extra={"trigger_id": str(trigger.id), "pattern_count": len(findings)},
    )
    return False


async def fire_trigger(
    trigger: TriggerRecord,
    *,
    now: datetime,
    agent_spec_store: AgentSpecStore,
    runtime: AgentRuntime,
    thread_store: ThreadMetaStore,
    audit_logger: AuditLogger,
    approval_store: ApprovalStore,
    trigger_store: TriggerStore,
    tenant_config_store: TenantConfigStore | None = None,
    stamp_last_fired: bool = True,
) -> UUID | None:
    """Start a run for ``trigger``; return the new ``run_id``, or ``None``.

    Must be called within the trigger's tenant RLS context. A preflight
    failure (agent gone / un-buildable) logs and returns ``None`` — no
    thread or run is created. The caller records the ``trigger_run``
    row from the returned ``run_id``.

    Stream 9.5 — ``stamp_last_fired`` controls the ``last_fired_at`` write.
    The cron scheduler already stamps it via ``claim_cron_fire`` (the CAS that
    serialises competing instances), so it passes ``False`` to avoid a
    redundant second write; the webhook ingest + DLQ retry paths keep ``True``.
    """
    record = await agent_spec_store.get(
        tenant_id=trigger.tenant_id,
        name=trigger.agent_name,
        version=trigger.agent_version,
    )
    if record is None or record.status is not AgentSpecStatus.ACTIVE:
        logger.warning(
            "trigger_firing.agent_unavailable",
            extra={"trigger_id": str(trigger.id), "agent": trigger.agent_name},
        )
        return None
    try:
        built = await runtime.get_agent(
            tenant_id=trigger.tenant_id,
            name=trigger.agent_name,
            version=trigger.agent_version,
            spec=record.spec,
        )
    except AgentFactoryError:
        logger.exception("trigger_firing.agent_build_failed", extra={"trigger_id": str(trigger.id)})
        return None

    # A triggered run is an independent conversation — fresh thread.
    thread_id = uuid4()
    await thread_store.create(
        thread_id=thread_id,
        tenant_id=trigger.tenant_id,
        created_by=f"trigger:{trigger.id}",
        user_id=trigger.user_id,
        agent_name=trigger.agent_name,
        agent_version=trigger.agent_version,
    )

    run_id = uuid4()
    run_record = await runtime.run_manager.create(
        run_id=run_id,
        thread_id=thread_id,
        tenant_id=trigger.tenant_id,
        user_id=trigger.user_id,
        is_resume=False,
    )
    # SE-7d-3b-ii — carry build-time distilled skills to the terminal hook.
    run_record.bound_distilled_skills = built.bound_distilled_skills
    seed = trigger.config.get("seed_input")
    seed_text = (
        seed
        if isinstance(seed, str) and seed.strip()
        else f"Scheduled run of trigger '{trigger.name}'."
    )

    # Capability Uplift Sprint #1 (Mini-ADR U-2 Layer B) — fire-time scan.
    blocked = await _scan_fire_time(
        seed_text=seed_text,
        trigger=trigger,
        audit_logger=audit_logger,
        tenant_config_store=tenant_config_store,
    )
    if blocked:
        return None

    graph_input = {
        "messages": [
            SystemMessage(content=built.system_prompt),
            HumanMessage(content=seed_text),
        ],
        "step_count": 0,
        "max_steps": built.max_steps,
    }
    configurable: dict[str, Any] = {
        "thread_id": str(thread_id),
        "tenant_id": str(trigger.tenant_id),
        "run_id": str(run_id),
    }
    if trigger.user_id is not None:
        configurable["user_id"] = str(trigger.user_id)
    if built.run_deadline_s > 0:
        configurable["deadline_at"] = time.monotonic() + float(built.run_deadline_s)
    config: RunnableConfig = {"configurable": configurable}

    worker = asyncio.create_task(
        run_agent(
            bridge=runtime.stream_bridge,
            run_manager=runtime.run_manager,
            record=run_record,
            graph=built.graph,  # type: ignore[arg-type]
            graph_input=graph_input,
            config=config,
            audit_logger=audit_logger,
            approval_store=approval_store,
            # Stream H.3 PR 3 — durable SSE mirror.
            event_store=runtime.run_event_store,
            skill_run_usage_recorder=runtime.skill_run_usage_recorder,
            # Stream L.L7 — record the trajectory (curation / eval-gate source).
            trajectory_recorder=runtime.trajectory_recorder,
            worker_spawn_budget=runtime.new_worker_spawn_budget(),
            # Stream HX-3 — replay-safety resolver for transient retry.
            tool_replay_safe=built.tool_replay_safe,
        )
    )
    await runtime.run_manager.attach_task(run_id, worker)

    if stamp_last_fired:
        await trigger_store.update(trigger.model_copy(update={"last_fired_at": now}))
    await emit(
        audit_logger,
        tenant_id=trigger.tenant_id,
        actor_id=f"trigger:{trigger.id}",
        action=AuditAction.TRIGGER_FIRE,
        resource_type="trigger",
        resource_id=str(trigger.id),
        trace_id=current_trace_id_hex(),
        details={"run_id": str(run_id), "kind": trigger.kind},
    )
    _triggers_fired.inc()
    logger.info(
        "trigger_firing.fired",
        extra={"trigger_id": str(trigger.id), "run_id": str(run_id)},
    )
    return run_id


__all__ = ["fire_trigger"]
