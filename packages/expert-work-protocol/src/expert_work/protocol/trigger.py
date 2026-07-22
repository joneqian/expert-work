"""J.10 调度 / 触发 — DTOs.

Mini-ADR J-18 / J-26 / J-42 (STREAM-J-DESIGN § 16). M0 = ``cron`` +
``webhook`` triggers, a polling scheduler over the ``agent_trigger``
table, DLQ retry, and per-tenant cron quota.

These DTOs are the wire shape between the manifest
(``AgentSpecBody.triggers`` carries :class:`TriggerSpec`), the trigger
stores (rows are :class:`TriggerRecord` / :class:`TriggerRunRecord`),
and the scheduler.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "TriggerKind",
    "TriggerRecord",
    "TriggerRunRecord",
    "TriggerRunStatus",
    "TriggerSource",
    "TriggerSpec",
]

#: A trigger's firing mechanism. ``cron`` fires on a schedule;
#: ``webhook`` fires on an authenticated inbound HTTP request. ``event``
#: (internal events via PG NOTIFY) is M1 — see Mini-ADR J-42.
TriggerKind = Literal["cron", "webhook"]

#: Where a trigger row came from — declared in an agent manifest
#: (``AgentSpecBody.triggers``, reconciled into the table on deploy) or
#: created directly through the triggers CRUD API.
TriggerSource = Literal["manifest", "api"]


class TriggerSpec(BaseModel):
    """One ``triggers:`` manifest entry — also the CRUD create payload.

    ``config`` shape depends on ``kind``:

    * ``cron`` — ``{"expr": "<5-field cron>", "seed_input": "<optional>"}``
    * ``webhook`` — ``{"seed_input": "<optional>"}``

    ``seed_input`` becomes the first user message of the run the trigger
    starts; each firing runs in a fresh thread.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(
        min_length=1,
        max_length=64,
        description="Trigger name — unique per (tenant, agent).",
    )
    kind: TriggerKind
    config: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_config(self) -> TriggerSpec:
        """A ``cron`` trigger needs a non-empty ``expr``; ``webhook`` needs nothing.

        The ``expr`` is only shape-checked here (a non-empty string) —
        full cron-grammar validation needs ``croniter`` and happens in
        the scheduler / CRUD layer, which keeps this package dependency-free.
        """
        if self.kind == "cron":
            expr = self.config.get("expr")
            if not isinstance(expr, str) or not expr.strip():
                msg = "cron trigger requires config['expr'] (a non-empty cron string)"
                raise ValueError(msg)
        return self


class TriggerRunStatus(StrEnum):
    """Lifecycle status of a :class:`TriggerRunRecord` — one trigger firing.

    ``FIRED`` — the run was started. ``SUCCEEDED`` / ``FAILED`` — the
    run outcome. ``RETRYING`` — a failed run is queued for a DLQ re-fire
    (``next_retry_at`` set). ``DEAD_LETTER`` — terminal after the retry
    budget is spent (Mini-ADR J-26 (1)).
    """

    FIRED = "fired"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRYING = "retrying"
    DEAD_LETTER = "dead_letter"


class TriggerRecord(BaseModel):
    """One row of ``agent_trigger`` — a registered trigger.

    Carries the :class:`TriggerSpec` fields plus the tenant / agent
    binding, the enabled flag, the row source, and — for ``webhook``
    triggers — the hashed inbound secret. ``webhook_secret_hash`` is
    never the plaintext; the secret is shown once at creation.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: UUID
    user_id: UUID | None = None
    agent_name: str = Field(min_length=1)
    agent_version: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=64)
    kind: TriggerKind
    config: dict[str, object] = Field(default_factory=dict)
    enabled: bool = True
    source: TriggerSource = "api"
    webhook_secret_hash: str | None = None
    originating_thread_id: UUID | None = None
    context_mode: Literal["reuse_thread", "fresh_thread_per_run"] = "fresh_thread_per_run"
    last_fired_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class TriggerRunRecord(BaseModel):
    """One row of ``trigger_run`` — a single firing of a trigger.

    Links the trigger to the ``agent_run`` it started (``run_id``,
    backfilled once the run is spawned). The DLQ retry state
    (``attempt`` / ``next_retry_at`` / ``status``) lives here —
    Mini-ADR J-26 (1).
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: UUID
    trigger_id: UUID
    run_id: UUID | None = None
    status: TriggerRunStatus = TriggerRunStatus.FIRED
    attempt: int = Field(default=1, ge=1)
    next_retry_at: datetime | None = None
    error: str | None = None
    triggered_at: datetime
