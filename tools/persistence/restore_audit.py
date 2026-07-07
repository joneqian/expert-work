"""Stream K.K14 — replay audit-log rows from the WORM object-store backup.

Used in the recovery runbook (``docs/runbooks/audit-restore.md``): if
the live ``audit_log`` table is destroyed or corrupted, this helper
walks the WORM bucket prefix that the audit-backup-worker writes to
and re-inserts the JSON records into a *restore* table (default
``audit_log_restored``). The operator decides whether to swap that
table into the live name or query side-by-side.

The serializer is the worker's own
:mod:`audit_backup_worker.serialization`, so the file shape this
module reads is the same one the worker wrote — no schema drift.

Inputs:

* ``object_store`` — any :class:`ObjectStore` (S3-compatible in
  production; the in-memory store covers the drill test).
* ``prefix`` — usually ``"{tenant_id}/"`` to scope the restore;
  empty string restores everything in the bucket.
* ``writer`` — a callable invoked once per row with the parsed dict.
  Production binds it to an asyncpg INSERT; the drill test binds it
  to a list.append.

Returns a small :class:`RestoreReport` with the count + a list of
keys that failed to parse (the operator can pull them by key and
investigate).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from expert_work.runtime.storage.base import ObjectStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RestoreReport:
    """Outcome of one :func:`restore_audit_rows` invocation."""

    restored: int = 0
    failed_keys: tuple[str, ...] = field(default_factory=tuple)


#: Stream K.K14 — the runbook's "writer" hook. One serialised audit
#: row in, an awaitable that lands it somewhere out. The drill test
#: stuffs them into a list; production binds to an INSERT statement
#: against ``audit_log_restored`` (see runbook step 3).
AuditRowWriter = Callable[[dict[str, object]], Awaitable[None]]


async def restore_audit_rows(
    *,
    object_store: ObjectStore,
    prefix: str = "",
    writer: AuditRowWriter,
) -> RestoreReport:
    """Walk ``prefix`` in ``object_store`` and replay every JSON row via ``writer``.

    Each object is the byte stream produced by
    :func:`audit_backup_worker.serialization.serialize_row`. We
    intentionally do not parse into ORM rows — the runbook is the
    contract for what columns to land where, and the operator's
    writer hook does that mapping.
    """
    keys = await object_store.list_prefix(prefix)
    restored = 0
    failed: list[str] = []
    for key in keys:
        try:
            blob = await object_store.get(key)
            payload = json.loads(blob.decode("utf-8"))
            if not isinstance(payload, dict):
                msg = f"object {key} did not decode to a JSON object"
                raise ValueError(msg)
            await writer(payload)
        except Exception:
            logger.exception("audit_restore.row_failed key=%s", key)
            failed.append(key)
            continue
        restored += 1
    logger.info(
        "audit_restore.done prefix=%r restored=%d failed=%d",
        prefix,
        restored,
        len(failed),
    )
    return RestoreReport(restored=restored, failed_keys=tuple(failed))
