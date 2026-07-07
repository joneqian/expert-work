"""Stream K.K14 — WORM restore drill.

End-to-end test for the restore tool against an in-memory object store
seeded with serialised audit rows. Proves the round-trip:

    serialize_row(row) → object_store.put(...)
        → ... operator drops audit_log ...
            → restore_audit_rows(...) → writer captures original payload

The drill answers the runbook's central question — "did we lose
audit data when the live table went away?" — with a green test
rather than a quarterly tabletop exercise. The full DR cycle (real
S3 Object Lock, real audit_log_restored table) lives in
``docs/runbooks/audit-restore.md``; this test pins the data-path
contract so a regression in ``serialize_row`` or the worker key
shape would fail CI here.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

# Co-located helper modules import as top-level names (no package).
_TOOLS = Path(__file__).resolve().parents[1]
if str(_TOOLS / "persistence") not in sys.path:
    sys.path.insert(0, str(_TOOLS / "persistence"))

from restore_audit import restore_audit_rows  # noqa: E402

from audit_backup_worker.serialization import object_key_for, serialize_row  # noqa: E402
from expert_work.persistence.models import AuditLogRow  # noqa: E402
from expert_work.runtime.storage.memory import InMemoryObjectStore  # noqa: E402


def _row(tenant: object, action: str = "session:write") -> AuditLogRow:
    """Build a minimally-populated AuditLogRow without touching the DB."""
    row = AuditLogRow(
        tenant_id=tenant,  # type: ignore[arg-type]
        actor_type="user",
        actor_id="alice",
        action=action,
        resource_type="session",
        resource_id="t-1",
        result="success",
        occurred_at=datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC),
        details={"k": "v"},
    )
    row.id = 1234
    return row


@pytest.mark.asyncio
async def test_restore_recovers_all_rows_from_worm_prefix() -> None:
    """The runbook's main path: seed the bucket with serialised rows,
    drop the live state, replay everything via ``restore_audit_rows``."""
    tenant_id = uuid4()
    store = InMemoryObjectStore()
    rows = [
        _row(tenant_id, action="session:write"),
        _row(tenant_id, action="run:completed"),
        _row(tenant_id, action="api_key:rotate"),
    ]
    for i, row in enumerate(rows, start=1):
        row.id = i
        await store.put(
            object_key_for(row),
            serialize_row(row),
            content_type="application/json",
            retain_until=datetime(2030, 1, 1, tzinfo=UTC),
            lock_mode="compliance",
        )

    # Operator's writer hook — production points at an INSERT statement
    # against ``audit_log_restored``; the drill captures into a list to
    # assert the shape.
    restored: list[dict[str, object]] = []

    async def _capture(payload: dict[str, object]) -> None:
        restored.append(payload)

    report = await restore_audit_rows(object_store=store, prefix=f"{tenant_id}/", writer=_capture)

    assert report.restored == 3
    assert report.failed_keys == ()
    actions = sorted(str(p["action"]) for p in restored)
    assert actions == ["api_key:rotate", "run:completed", "session:write"]


@pytest.mark.asyncio
async def test_restore_isolates_per_tenant_via_prefix() -> None:
    """A prefix-scoped restore touches only the requested tenant — proves
    the runbook's "restore just one tenant" path is workable."""
    tenant_a = uuid4()
    tenant_b = uuid4()
    store = InMemoryObjectStore()

    for tenant in (tenant_a, tenant_b):
        row = _row(tenant)
        row.id = hash(tenant) & 0xFFFF
        await store.put(
            object_key_for(row),
            serialize_row(row),
            content_type="application/json",
            retain_until=datetime(2030, 1, 1, tzinfo=UTC),
            lock_mode="compliance",
        )

    restored: list[dict[str, object]] = []

    async def _capture(payload: dict[str, object]) -> None:
        restored.append(payload)

    report = await restore_audit_rows(object_store=store, prefix=f"{tenant_a}/", writer=_capture)

    assert report.restored == 1
    assert str(restored[0]["tenant_id"]) == str(tenant_a)


@pytest.mark.asyncio
async def test_restore_records_bad_payload_keys_but_keeps_going() -> None:
    """A corrupt blob does not halt the restore — the runbook needs to
    finish even if one object is malformed, and the operator gets the
    list of failed keys to investigate by hand."""
    store = InMemoryObjectStore()
    good_row = _row(uuid4())
    good_row.id = 1
    await store.put(
        object_key_for(good_row),
        serialize_row(good_row),
        content_type="application/json",
        retain_until=datetime(2030, 1, 1, tzinfo=UTC),
        lock_mode="compliance",
    )
    # Plant a junk object next to it.
    await store.put(
        f"{good_row.tenant_id}/2026/05/20/corrupt.json",
        b"not json {",
        content_type="application/json",
        retain_until=datetime(2030, 1, 1, tzinfo=UTC),
        lock_mode="compliance",
    )

    restored: list[dict[str, object]] = []

    async def _capture(payload: dict[str, object]) -> None:
        restored.append(payload)

    report = await restore_audit_rows(
        object_store=store, prefix=f"{good_row.tenant_id}/", writer=_capture
    )

    assert report.restored == 1
    assert len(report.failed_keys) == 1
    assert "corrupt" in report.failed_keys[0]
