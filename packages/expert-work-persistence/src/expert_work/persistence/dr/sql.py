"""SQLAlchemy-backed ``BackupRecordStore`` (Postgres / asyncpg)."""

from __future__ import annotations

from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from expert_work.persistence.dr.base import BackupRecordStore
from expert_work.persistence.models import BackupRecordRow, DrDrillRow
from expert_work.protocol import (
    BackupAssetType,
    BackupRecord,
    BackupStatus,
    BackupTier,
    DrillRecord,
    DrillType,
)


def _row_to_record(row: BackupRecordRow) -> BackupRecord:
    return BackupRecord(
        id=row.id,
        asset_type=BackupAssetType(row.asset_type),
        asset_ref=row.asset_ref,
        started_at=row.started_at,
        finished_at=row.finished_at,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        status=BackupStatus(row.status),
        error=row.error,
        region=row.region,
        tier=BackupTier(str(row.tier)),
    )


def _row_to_drill(row: DrDrillRow) -> DrillRecord:
    return DrillRecord(
        id=row.id,
        drill_type=DrillType(row.drill_type),
        started_at=row.started_at,
        finished_at=row.finished_at,
        rpo_actual_s=row.rpo_actual_s,
        rto_actual_s=row.rto_actual_s,
        target_rpo_s=row.target_rpo_s,
        target_rto_s=row.target_rto_s,
        passed=row.passed,
        notes=row.notes,
    )


class SqlBackupRecordStore(BackupRecordStore):
    """Postgres-backed Repository for ``backup_record`` + ``dr_drill``."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def record(self, entry: BackupRecord) -> BackupRecord:
        # Postgres ON CONFLICT lets the BackupJob's
        # "insert RUNNING, then update SUCCESS/FAILED with same asset_ref"
        # pattern collapse into one SQL statement.
        stmt = (
            insert(BackupRecordRow)
            .values(
                asset_type=entry.asset_type.value,
                asset_ref=entry.asset_ref,
                started_at=entry.started_at,
                finished_at=entry.finished_at,
                size_bytes=entry.size_bytes,
                sha256=entry.sha256,
                status=entry.status.value,
                error=entry.error,
                region=entry.region,
                tier=int(entry.tier.value),
            )
            .on_conflict_do_update(
                constraint="backup_record_asset_unique",
                set_={
                    "started_at": entry.started_at,
                    "finished_at": entry.finished_at,
                    "size_bytes": entry.size_bytes,
                    "sha256": entry.sha256,
                    "status": entry.status.value,
                    "error": entry.error,
                    "region": entry.region,
                    "tier": int(entry.tier.value),
                },
            )
            .returning(BackupRecordRow)
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            await session.commit()
            row = result.scalar_one()
            return _row_to_record(row)

    async def latest(self, asset_type: BackupAssetType) -> BackupRecord | None:
        stmt = (
            select(BackupRecordRow)
            .where(BackupRecordRow.asset_type == asset_type.value)
            .order_by(desc(BackupRecordRow.started_at))
            .limit(1)
        )
        async with self._sf() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return _row_to_record(row) if row is not None else None

    async def record_drill(self, drill: DrillRecord) -> DrillRecord:
        row = DrDrillRow(
            drill_type=drill.drill_type.value,
            started_at=drill.started_at,
            finished_at=drill.finished_at,
            rpo_actual_s=drill.rpo_actual_s,
            rto_actual_s=drill.rto_actual_s,
            target_rpo_s=drill.target_rpo_s,
            target_rto_s=drill.target_rto_s,
            passed=drill.passed,
            notes=drill.notes,
        )
        async with self._sf() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _row_to_drill(row)
