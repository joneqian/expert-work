"""In-memory ``BackupRecordStore`` for unit tests."""

from __future__ import annotations

import asyncio

from expert_work.persistence.dr.base import BackupRecordStore
from expert_work.protocol import BackupAssetType, BackupRecord, DrillRecord


class InMemoryBackupRecordStore(BackupRecordStore):
    """Process-local store keyed on ``(asset_type, asset_ref)``.

    Backed by a flat dict mirroring the Postgres ``UNIQUE`` constraint —
    inserting with an existing key overwrites in place (same id).
    """

    def __init__(self) -> None:
        self._backups: dict[tuple[str, str], BackupRecord] = {}
        self._drills: list[DrillRecord] = []
        self._next_id: int = 1
        self._lock = asyncio.Lock()

    async def record(self, entry: BackupRecord) -> BackupRecord:
        key = (entry.asset_type.value, entry.asset_ref)
        async with self._lock:
            existing = self._backups.get(key)
            assigned_id = existing.id if existing is not None else self._next_id
            if existing is None:
                self._next_id += 1
            stamped = entry.model_copy(update={"id": assigned_id})
            self._backups[key] = stamped
            return stamped

    async def latest(self, asset_type: BackupAssetType) -> BackupRecord | None:
        candidates = [r for r in self._backups.values() if r.asset_type == asset_type]
        if not candidates:
            return None
        return max(candidates, key=lambda r: r.started_at)

    async def record_drill(self, drill: DrillRecord) -> DrillRecord:
        async with self._lock:
            stamped = drill.model_copy(update={"id": self._next_id})
            self._next_id += 1
            self._drills.append(stamped)
            return stamped

    def snapshot_drills(self) -> list[DrillRecord]:
        """Test helper: return a copy of all inserted drill rows."""
        return list(self._drills)
