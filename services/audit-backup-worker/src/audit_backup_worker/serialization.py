"""Audit-row → JSON bytes serializer used by the WORM-backup worker.

Pulled out as a separate module so unit tests can pin the on-disk
shape without booting the worker. The serializer is intentionally
**lossless and stable** — every field of ``AuditLogRow`` is in the
output, and downstream replay / Athena queries depend on the schema.
"""

from __future__ import annotations

import json
from datetime import datetime
from ipaddress import IPv4Address, IPv6Address
from typing import Any
from uuid import UUID

from helix_agent.persistence.models import AuditLogRow


def _jsonable(value: Any) -> Any:
    """Convert non-JSON-native types (UUID / datetime / IPAddress) to strings."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, IPv4Address | IPv6Address):
        return str(value)
    return value


def serialize_row(row: AuditLogRow) -> bytes:
    """Return the canonical UTF-8 JSON body backing up ``row``.

    ``backup_acked`` / ``backup_acked_at`` are deliberately **not**
    serialized — they are the worker's internal progress marker, not
    part of the audit record. Including them would muddle replay
    semantics.
    """
    payload: dict[str, Any] = {
        "id": row.id,
        "tenant_id": _jsonable(row.tenant_id),
        "actor_type": row.actor_type,
        "actor_id": row.actor_id,
        "on_behalf_of": row.on_behalf_of,
        "action": row.action,
        "resource_type": row.resource_type,
        "resource_id": row.resource_id,
        "result": row.result,
        "reason": row.reason,
        "ip": _jsonable(row.ip),
        "user_agent": row.user_agent,
        "request_id": _jsonable(row.request_id),
        "trace_id": row.trace_id,
        "details": row.details,
        "occurred_at": _jsonable(row.occurred_at),
    }
    # ``sort_keys`` for deterministic output; ``separators`` for compact
    # serialization that S3 size limits / metering will appreciate.
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def object_key_for(row: AuditLogRow) -> str:
    """Return the canonical S3 key for ``row``.

    Format ``{tenant_id}/{YYYY}/{MM}/{DD}/{id}.json`` per
    STREAM-D-DESIGN § 2.4 — tenant-prefixed so M1 lifecycle-by-prefix
    policies stay simple, year/month/day for cheap range filtering.
    """
    occurred = row.occurred_at
    return (
        f"{row.tenant_id}/{occurred.year:04d}/{occurred.month:02d}/{occurred.day:02d}/{row.id}.json"
    )
