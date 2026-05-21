"""Thin helpers around :class:`AuditLogger` for the Control Plane.

Constructs an ``AuditLogger`` wired to a SQL or in-memory store + a
redactor (D.2: :class:`TenantAwareRedactor` when a PII-fields resolver
is supplied, :class:`DefaultSecretRedactor` otherwise) + the in-memory
fallback queue. The B.5 handlers call :func:`emit` with the per-request
actor / tenant; redaction + durability fallback are handled by
``AuditLogger`` itself.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from helix_agent.persistence.audit_log import AuditLogStore, InMemoryAuditLogStore
from helix_agent.protocol import AuditAction, AuditEntry, AuditResult
from helix_agent.runtime.audit.fallback import InMemoryAuditFallbackQueue
from helix_agent.runtime.audit.logger import AuditLogger
from helix_agent.runtime.audit.redactor import (
    AuditRedactor,
    DefaultSecretRedactor,
    PiiFieldsResolver,
    TenantAwareRedactor,
)

if TYPE_CHECKING:
    from control_plane.tenancy import TenantConfigService

logger = logging.getLogger(__name__)


class TenantConfigPiiResolver:
    """Late-bound :class:`PiiFieldsResolver` backed by ``TenantConfigService``.

    The audit logger and the tenant_config service have a circular
    construction dependency:

    * ``AuditLogger`` needs the resolver to wire the
      :class:`TenantAwareRedactor`.
    * ``TenantConfigService`` needs ``AuditLogger`` to emit
      ``tenant_config:read`` / ``write`` audits.

    This adapter resolves the cycle: it's constructed first (returning
    ``[]`` for every tenant when unbound), passed into the audit
    logger, and ``bind()``-ed to the service after both are wired.
    Before binding no audit emissions happen — startup is single-
    threaded — so the empty-list behavior is invisible.
    """

    def __init__(self) -> None:
        self._service: TenantConfigService | None = None

    def bind(self, service: TenantConfigService) -> None:
        """Attach the live ``TenantConfigService``. Idempotent."""
        self._service = service

    async def __call__(self, tenant_id: UUID) -> Sequence[str]:
        if self._service is None:
            return []
        # ``TenantConfigService.get`` raises ``TenantConfigNotConfiguredError``
        # when no row exists — that's a normal "tenant not seeded yet"
        # state, not an error. Return [] so the redactor falls through
        # to global patterns only. Other failures bubble up and are
        # swallowed by :class:`TenantAwareRedactor` itself per the
        # design.
        from control_plane.tenancy import TenantConfigNotConfiguredError

        try:
            record = await self._service.get(tenant_id=tenant_id)
        except TenantConfigNotConfiguredError:
            return []
        return list(record.pii_fields)


def build_default_audit_logger(
    store: AuditLogStore | None = None,
    *,
    pii_fields_resolver: PiiFieldsResolver | None = None,
) -> AuditLogger:
    """Default wiring used by ``create_app`` in tests / single-process dev.

    Production swaps the in-memory store for a SQL one (see
    ``control_plane.main``).

    When ``pii_fields_resolver`` is supplied the redactor is
    :class:`TenantAwareRedactor` and per-tenant ``pii_fields`` are
    masked in addition to the global secret patterns. With no
    resolver the logger uses :class:`DefaultSecretRedactor` alone —
    fine for unit tests that don't exercise per-tenant PII.
    """
    return AuditLogger(
        store=store or InMemoryAuditLogStore(),
        redactor=_build_redactor(pii_fields_resolver),
        fallback=InMemoryAuditFallbackQueue(),
    )


def _build_redactor(resolver: PiiFieldsResolver | None) -> AuditRedactor:
    global_redactor = DefaultSecretRedactor()
    if resolver is None:
        return global_redactor
    return TenantAwareRedactor(
        global_redactor=global_redactor,
        pii_fields_resolver=resolver,
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
    "feedback",
    "image_upload",  # Stream J.6.补强-2 — Mini-ADR J-31
    "skill",  # Stream J.7a — Mini-ADR J-23
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
