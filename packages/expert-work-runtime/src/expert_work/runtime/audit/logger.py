"""``AuditLogger`` — the application-facing audit service.

Composes :class:`AuditLogStore` (data layer) + :class:`AuditRedactor`
(PII / secret masking) + :class:`AuditFallbackQueue` (durability on
primary-store failure) + self-audit emission on read.

Design: subsystems/17 § 4.1 + § 5.5 + § 5.6.

What is **not** here (M0 deliberate scope):

- ``Principal`` / role enforcement — Stream C (AuthN/AuthZ). For M0 the
  caller passes ``actor_id`` / ``actor_type`` directly and is trusted.
- S3 async push — M1 (WORM backup).
- Hash chain — M2.
- Strict-mode rejection on detected secrets — we always mask and emit a
  metric hit; a future strict mode can wrap this class.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Literal
from uuid import UUID

from expert_work.persistence.audit_log import AuditLogStore
from expert_work.protocol import AuditAction, AuditEntry, AuditPage, AuditQuery, AuditResult
from expert_work.runtime.audit.fallback import AuditFallbackQueue
from expert_work.runtime.audit.redactor import AuditRedactor

logger = logging.getLogger(__name__)

RedactionHitCallback = Callable[[str, int], None]


class AuditLogger:
    """High-level audit service.

    ``write`` is **best-effort by design** — it never raises on a primary-
    store error so that an admin operation (manifest publish, secret
    rotation, etc.) is not blocked by audit-side flakiness. Failures land
    in the fallback queue and an operator alert fires (the alert wiring
    lives in Stream A.9 metrics; this class emits ``logger.error`` for
    now).

    Pydantic validation errors **do** propagate — those represent a
    programming bug (e.g., an action not in :class:`AuditAction`), not
    a runtime fault we can recover from.
    """

    def __init__(
        self,
        store: AuditLogStore,
        redactor: AuditRedactor,
        fallback: AuditFallbackQueue,
        *,
        on_redact_hit: RedactionHitCallback | None = None,
    ) -> None:
        self._store = store
        self._redactor = redactor
        self._fallback = fallback
        self._on_redact_hit = on_redact_hit

    async def write(self, entry: AuditEntry) -> None:
        """Redact + persist one audit entry.

        Never raises on a store failure: the (already-redacted) entry is
        enqueued to the fallback queue instead and an ``error`` log is
        emitted.

        D.2: the redactor Protocol is now async + tenant-aware. We pass
        ``entry.tenant_id`` through so :class:`TenantAwareRedactor` can
        look up ``tenant_config.pii_fields`` without callers having to
        know about it. :class:`DefaultSecretRedactor` ignores the id.
        """
        redacted = await self._redactor.redact(
            tenant_id=entry.tenant_id,
            details=entry.details,
        )
        if redacted.hits and self._on_redact_hit is not None:
            for name, count in redacted.hits.items():
                self._on_redact_hit(name, count)

        clean_entry = entry.model_copy(update={"details": redacted.redacted})
        try:
            await self._store.append(clean_entry)
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            logger.error(
                "audit.write_failed action=%s tenant=%s reason=%s",
                entry.action.value,
                entry.tenant_id,
                reason,
            )
            await self._fallback.enqueue(clean_entry, reason=reason)

    async def query(
        self,
        q: AuditQuery,
        *,
        actor_id: str,
        actor_type: Literal["user", "service_account", "system", "agent"] = "user",
        actor_tenant_id: str | None = None,
    ) -> AuditPage:
        """Run ``q`` and emit a self-audit (action='audit:read').

        The self-audit is **best-effort**: a failure to emit it does not
        propagate. The query result is what the caller actually needs;
        losing the meta-audit row only impacts cross-checking.

        For a wildcard query (``q.tenant_id == '*'``) the caller must pass
        ``actor_tenant_id`` — that becomes the tenant_id of the self-audit
        row, since the queried tenant has no single value. Stream C will
        wire this from the current ``Principal``.
        """
        page = await self._store.query(q)
        try:
            await self.write(_self_audit_entry(q, actor_id, actor_type, actor_tenant_id))
        except Exception as exc:
            logger.warning("audit.self_audit_failed actor=%s reason=%s", actor_id, exc)
        return page

    async def get_by_id(
        self,
        audit_id: int,
        *,
        tenant_id: UUID,
        actor_id: str,
        actor_type: Literal["user", "service_account", "system", "agent"] = "user",
    ) -> AuditEntry | None:
        """Fetch one ``AuditEntry`` and emit a self-audit row (Stream H.4 PR 3).

        Mirrors :meth:`query` semantics: returns ``None`` when the entry
        does not exist *or* belongs to a different tenant (the store
        enforces tenant filtering — never reveals cross-tenant existence).
        The self-audit emits unconditionally and is best-effort.
        """
        entry = await self._store.get_by_id(audit_id, tenant_id=tenant_id)
        try:
            await self.write(
                AuditEntry(
                    tenant_id=tenant_id,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    action=AuditAction.AUDIT_READ,
                    resource_type="audit",
                    resource_id=str(audit_id),
                    result=AuditResult.SUCCESS,
                    details={"endpoint": "GET /v1/audit/{id}"},
                )
            )
        except Exception as exc:
            logger.warning("audit.self_audit_failed actor=%s reason=%s", actor_id, exc)
        return entry


def _self_audit_entry(
    q: AuditQuery,
    actor_id: str,
    actor_type: Literal["user", "service_account", "system", "agent"],
    actor_tenant_id: str | None,
) -> AuditEntry:
    """Build the ``action='audit:read'`` self-audit row.

    Per subsystems/17 § 5.5 we record the query **conditions** but not the
    returned rows — otherwise audit reads explode the table.
    """
    from uuid import UUID

    if q.tenant_id == "*":
        if actor_tenant_id is None:
            msg = "actor_tenant_id is required when querying tenant='*'"
            raise ValueError(msg)
        tenant = UUID(actor_tenant_id)
    else:
        tenant = q.tenant_id

    return AuditEntry(
        tenant_id=tenant,
        actor_type=actor_type,
        actor_id=actor_id,
        action=AuditAction.AUDIT_READ,
        resource_type="audit",
        resource_id=None,
        result=AuditResult.SUCCESS,
        details={
            "query": {
                "tenant_id": "*" if q.tenant_id == "*" else str(q.tenant_id),
                "actor_id": q.actor_id,
                "action": q.action.value if q.action else None,
                "resource_type": q.resource_type,
                "resource_id": q.resource_id,
                "result": q.result.value if q.result else None,
                "from_ts": q.from_ts.isoformat() if q.from_ts else None,
                "to_ts": q.to_ts.isoformat() if q.to_ts else None,
                "limit": q.limit,
                # ``cursor`` is intentionally omitted — it's opaque and
                # adds no operator value.
            },
        },
    )
