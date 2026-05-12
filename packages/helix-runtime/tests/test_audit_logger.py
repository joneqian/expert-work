"""Unit tests for :class:`AuditLogger`."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from helix_agent.persistence.audit_log import (
    AuditLogStore,
    InMemoryAuditLogStore,
)
from helix_agent.protocol import (
    AuditAction,
    AuditEntry,
    AuditPage,
    AuditQuery,
    AuditResult,
)
from helix_agent.runtime.audit import (
    AuditLogger,
    DefaultSecretRedactor,
    InMemoryAuditFallbackQueue,
)


def _entry(
    tenant_id: UUID | None = None,
    *,
    action: AuditAction = AuditAction.MANIFEST_WRITE,
    details: dict[str, object] | None = None,
) -> AuditEntry:
    return AuditEntry(
        tenant_id=tenant_id or uuid4(),
        actor_type="user",
        actor_id="alice",
        action=action,
        resource_type="manifest",
        resource_id="demo@1",
        result=AuditResult.SUCCESS,
        details=details or {},
    )


@pytest.mark.asyncio
async def test_write_redacts_then_persists() -> None:
    store = InMemoryAuditLogStore()
    logger = AuditLogger(
        store=store,
        redactor=DefaultSecretRedactor(),
        fallback=InMemoryAuditFallbackQueue(),
    )

    tenant = uuid4()
    await logger.write(_entry(tenant, details={"key": "sk-ABCDEFGHIJKLMNOPQRSTUVWX"}))

    page = await store.query(AuditQuery(tenant_id=tenant))
    assert len(page.entries) == 1
    assert "sk-ABCDEFGHIJKLMNOPQRSTUVWX" not in page.entries[0].details["key"]


@pytest.mark.asyncio
async def test_write_invokes_redact_hit_callback() -> None:
    hits: list[tuple[str, int]] = []
    logger = AuditLogger(
        store=InMemoryAuditLogStore(),
        redactor=DefaultSecretRedactor(),
        fallback=InMemoryAuditFallbackQueue(),
        on_redact_hit=lambda name, count: hits.append((name, count)),
    )

    await logger.write(
        _entry(
            details={"prompt": "use key sk-ABCDEFGHIJKLMNOPQRSTUVWX twice sk-YYYYYYYYYYYYYYYYYYYY"}
        )
    )

    assert hits == [("openai_key", 2)]


@pytest.mark.asyncio
async def test_write_falls_back_to_queue_on_store_failure() -> None:
    class FlakyStore(InMemoryAuditLogStore):
        async def append(self, entry: AuditEntry) -> AuditEntry:
            msg = "OperationalError: connection closed"
            raise RuntimeError(msg)

    fallback = InMemoryAuditFallbackQueue()
    logger = AuditLogger(
        store=FlakyStore(),
        redactor=DefaultSecretRedactor(),
        fallback=fallback,
    )

    # write() must not raise even though the store is broken.
    await logger.write(_entry(details={"k": "sk-ABCDEFGHIJKLMNOPQRSTUVWX"}))

    snapshot = fallback.snapshot()
    assert len(snapshot) == 1
    # Entry written to fallback is the **redacted** version, not the raw one.
    assert "sk-ABCDEFGHIJKLMNOPQRSTUVWX" not in snapshot[0].entry.details["k"]
    assert "OperationalError" in snapshot[0].reason


@pytest.mark.asyncio
async def test_query_emits_self_audit() -> None:
    store = InMemoryAuditLogStore()
    logger = AuditLogger(
        store=store,
        redactor=DefaultSecretRedactor(),
        fallback=InMemoryAuditFallbackQueue(),
    )

    tenant = uuid4()
    # Pre-populate one manifest:write so query() returns something + then
    # a self-audit fires.
    await logger.write(_entry(tenant, action=AuditAction.MANIFEST_WRITE))

    result = await logger.query(AuditQuery(tenant_id=tenant), actor_id="admin")

    # The original write is returned to the caller (page has 1 entry).
    assert len(result.entries) == 1
    assert result.entries[0].action == AuditAction.MANIFEST_WRITE

    # ... but a self-audit row was also written to the store.
    full = await store.query(AuditQuery(tenant_id=tenant))
    actions = [e.action for e in full.entries]
    assert AuditAction.AUDIT_READ in actions


@pytest.mark.asyncio
async def test_query_self_audit_records_query_conditions() -> None:
    store = InMemoryAuditLogStore()
    logger = AuditLogger(
        store=store,
        redactor=DefaultSecretRedactor(),
        fallback=InMemoryAuditFallbackQueue(),
    )

    tenant = uuid4()
    await logger.query(
        AuditQuery(tenant_id=tenant, action=AuditAction.AUTH_LOGIN, limit=50),
        actor_id="admin",
    )

    page = await store.query(AuditQuery(tenant_id=tenant, action=AuditAction.AUDIT_READ))
    assert len(page.entries) == 1
    details = page.entries[0].details["query"]
    assert details["action"] == "auth:login"
    assert details["limit"] == 50


@pytest.mark.asyncio
async def test_query_self_audit_survives_emission_failure() -> None:
    """If the self-audit write fails (e.g., store flips broken after query),
    the user's query result must still be returned to the caller."""

    class StoreThatBreaksOnSecondWrite(InMemoryAuditLogStore):
        def __init__(self) -> None:
            super().__init__()
            self._writes = 0

        async def append(self, entry: AuditEntry) -> AuditEntry:
            self._writes += 1
            if self._writes >= 2:
                msg = "store closed"
                raise RuntimeError(msg)
            return await super().append(entry)

    store = StoreThatBreaksOnSecondWrite()
    logger = AuditLogger(
        store=store,
        redactor=DefaultSecretRedactor(),
        fallback=InMemoryAuditFallbackQueue(),
    )

    tenant = uuid4()
    await logger.write(_entry(tenant))  # writes=1 (success)

    # Query succeeds (it's a read; reads don't break). The self-audit write
    # is the 2nd write — that throws. logger.query() must absorb the failure
    # and still return the page.
    page = await logger.query(AuditQuery(tenant_id=tenant), actor_id="admin")
    assert isinstance(page, AuditPage)


@pytest.mark.asyncio
async def test_query_wildcard_tenant_requires_actor_tenant_id() -> None:
    logger = AuditLogger(
        store=InMemoryAuditLogStore(),
        redactor=DefaultSecretRedactor(),
        fallback=InMemoryAuditFallbackQueue(),
    )

    # Without actor_tenant_id, the self-audit emission would fail internally
    # — but we suppress emission errors, so query() still returns. The
    # important behavior is that the page comes back regardless.
    page = await logger.query(AuditQuery(tenant_id="*"), actor_id="admin")
    assert isinstance(page, AuditPage)


@pytest.mark.asyncio
async def test_query_wildcard_tenant_self_audits_under_actor_tenant() -> None:
    store = InMemoryAuditLogStore()
    logger = AuditLogger(
        store=store,
        redactor=DefaultSecretRedactor(),
        fallback=InMemoryAuditFallbackQueue(),
    )

    actor_tenant = uuid4()
    await logger.query(
        AuditQuery(tenant_id="*"),
        actor_id="admin",
        actor_tenant_id=str(actor_tenant),
    )

    page = await store.query(AuditQuery(tenant_id=actor_tenant))
    assert any(e.action == AuditAction.AUDIT_READ for e in page.entries)
    self_audit = next(e for e in page.entries if e.action == AuditAction.AUDIT_READ)
    assert self_audit.details["query"]["tenant_id"] == "*"


@pytest.mark.asyncio
async def test_audit_logger_accepts_store_protocol() -> None:
    """Sanity check: AuditLogger only depends on the abstract store."""
    # ``AuditLogStore`` is the abstract base; passing the in-memory subclass
    # exercises the structural-typing path through the constructor.
    store: AuditLogStore = InMemoryAuditLogStore()
    AuditLogger(
        store=store,
        redactor=DefaultSecretRedactor(),
        fallback=InMemoryAuditFallbackQueue(),
    )
