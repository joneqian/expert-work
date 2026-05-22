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
from helix_agent.common.observability import current_trace_id_hex, helix_counter
from helix_agent.persistence import ApprovalStore, ThreadMetaStore, TriggerStore
from helix_agent.persistence.agent_spec import AgentSpecStore
from helix_agent.protocol import AgentSpecStatus, AuditAction, TriggerRecord
from helix_agent.runtime.audit.logger import AuditLogger
from orchestrator import AgentFactoryError, run_agent

logger = logging.getLogger("helix.control_plane.trigger_firing")

#: Triggers fired into a run — cron + webhook share this counter.
_triggers_fired = helix_counter(
    "helix_control_plane_triggers_fired_total",
    "Triggers that started an agent run.",
)


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
) -> UUID | None:
    """Start a run for ``trigger``; return the new ``run_id``, or ``None``.

    Must be called within the trigger's tenant RLS context. A preflight
    failure (agent gone / un-buildable) logs and returns ``None`` — no
    thread or run is created. The caller records the ``trigger_run``
    row from the returned ``run_id``.
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
    seed = trigger.config.get("seed_input")
    seed_text = (
        seed
        if isinstance(seed, str) and seed.strip()
        else f"Scheduled run of trigger '{trigger.name}'."
    )
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
        )
    )
    await runtime.run_manager.attach_task(run_id, worker)

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
