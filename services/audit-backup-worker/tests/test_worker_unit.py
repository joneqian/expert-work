"""Unit tests for ``AuditWormBackupWorker`` construction + retention resolver.

The substantive end-to-end behavior (read → put with lock → UPDATE
acked) needs a real Postgres + ObjectStore and lives in
``test_worker_integration.py``. These unit tests cover only the
shape contracts.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from audit_backup_worker.worker import (
    AuditBackupResult,
    AuditWormBackupWorker,
    static_retention_resolver,
)
from helix_agent.runtime.storage import InMemoryObjectStore


@pytest.mark.asyncio
async def test_static_retention_resolver_returns_configured_value() -> None:
    resolver = static_retention_resolver(45)
    assert await resolver(uuid4()) == 45
    # Different tenant id, same answer.
    assert await resolver(uuid4()) == 45


def test_worker_rejects_non_positive_batch_size() -> None:
    """``batch_size <= 0`` is a programmer error — surface it early."""
    with pytest.raises(ValueError, match="batch_size"):
        AuditWormBackupWorker(
            db_session_factory=lambda: None,  # type: ignore[arg-type]
            object_store=InMemoryObjectStore(),
            retention_resolver=static_retention_resolver(1),
            batch_size=0,
        )


def test_audit_backup_result_carries_counts() -> None:
    """Tally fields are positional + final; subscribed by run_forever."""
    result = AuditBackupResult(processed=3, failed=1)
    assert result.processed == 3
    assert result.failed == 1
