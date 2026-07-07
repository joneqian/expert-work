"""Abstract Repositories for DR metadata.

Design: subsystems/22-disaster-recovery § 3.2.

Implementations:

- :class:`expert_work.persistence.dr.memory.InMemoryBackupRecordStore`
- :class:`expert_work.persistence.dr.sql.SqlBackupRecordStore`

Drill records are intentionally write-only via the same store — M0 only
needs to insert one row per quarterly manual drill; query / aggregation
lands when ``DrillRunner`` automates in M1.
"""

from __future__ import annotations

import abc

from expert_work.protocol import BackupAssetType, BackupRecord, DrillRecord


class BackupRecordStore(abc.ABC):
    """Append + update repository for ``backup_record``.

    ``record(...)`` upserts on the ``(asset_type, asset_ref)`` unique key —
    the typical pattern is "insert with status=RUNNING, then update to
    SUCCESS/FAILED on the same ``asset_ref``". Implementations should
    handle both the first insert and the status transition idempotently.
    """

    @abc.abstractmethod
    async def record(self, entry: BackupRecord) -> BackupRecord:
        """Upsert one backup row keyed on ``(asset_type, asset_ref)``.

        Returns the row with ``id`` populated (initial insert) or the
        existing ``id`` (subsequent status update).
        """

    @abc.abstractmethod
    async def latest(self, asset_type: BackupAssetType) -> BackupRecord | None:
        """Most recent row for ``asset_type``, regardless of status.

        Used by the freshness alert: if ``started_at`` is older than
        ``RPO_target``, the alert fires (subsystems/22 § 7).
        """

    @abc.abstractmethod
    async def record_drill(self, drill: DrillRecord) -> DrillRecord:
        """Insert one ``dr_drill`` row. Returns the row with ``id`` set."""
