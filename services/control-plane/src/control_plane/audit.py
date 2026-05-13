"""Thin helpers around :class:`AuditLogger` for the Control Plane.

Constructs an ``AuditLogger`` wired to a SQL or in-memory store + the
default secret redactor + the in-memory fallback queue. The B.5 handlers
call :func:`emit` with the per-request actor / tenant; redaction +
durability fallback are handled by ``AuditLogger`` itself.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from helix_agent.persistence.audit_log import AuditLogStore, InMemoryAuditLogStore
from helix_agent.protocol import AuditAction, AuditEntry, AuditResult
from helix_agent.runtime.audit.fallback import InMemoryAuditFallbackQueue
from helix_agent.runtime.audit.logger import AuditLogger
from helix_agent.runtime.audit.redactor import DefaultSecretRedactor


def build_default_audit_logger(store: AuditLogStore | None = None) -> AuditLogger:
    """Default wiring used by ``create_app`` in tests / single-process dev.

    Production swaps the in-memory store for a SQL one (see
    ``control_plane.main``).
    """
    return AuditLogger(
        store=store or InMemoryAuditLogStore(),
        redactor=DefaultSecretRedactor(),
        fallback=InMemoryAuditFallbackQueue(),
    )


ResourceType = Literal[
    "manifest",
    "session",
    "sandbox",
    "secret",
    "audit",
    "quota",
    "tenant_config",
    "user",
    "role_binding",
    "api_key",
    "service_account",
]


async def emit(
    logger: AuditLogger,
    *,
    tenant_id: UUID,
    actor_id: str,
    action: AuditAction,
    resource_type: ResourceType,
    resource_id: str | None = None,
    result: AuditResult = AuditResult.SUCCESS,
    reason: str | None = None,
    trace_id: str | None = None,
    details: dict[str, object] | None = None,
) -> None:
    """One-shot helper: build :class:`AuditEntry` + write.

    Defaults ``actor_type="user"`` because the dev-mode middleware does
    not yet distinguish service accounts; Stream C.1 will override per
    JWT principal.
    """
    entry = AuditEntry(
        tenant_id=tenant_id,
        actor_type="user",
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        result=result,
        reason=reason,
        trace_id=trace_id,
        details=details or {},
    )
    await logger.write(entry)
