"""Disaster-recovery row shapes — see subsystems/22 § 3.2."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class BackupAssetType(StrEnum):
    """What kind of asset a ``backup_record`` row describes.

    M0 is Postgres-only via ``pg_dump`` (``postgres_full``). M1+ adds
    ``postgres_wal`` (WAL-G continuous archiving), ``vault_snapshot``
    (Credential Proxy backups), and ``object_storage`` (cross-region
    replication checkpoints).
    """

    POSTGRES_FULL = "postgres_full"
    POSTGRES_WAL = "postgres_wal"
    VAULT_SNAPSHOT = "vault_snapshot"
    OBJECT_STORAGE = "object_storage"


class BackupStatus(StrEnum):
    """Lifecycle of one backup attempt."""

    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class BackupTier(StrEnum):
    """Asset criticality per subsystems/22 § 3.1 (T0/T1/T2)."""

    TIER_0 = "0"  # cannot be lost — RPO < 5 min target (M2)
    TIER_1 = "1"  # rebuildable slowly — RPO < 1 h target (M2)
    TIER_2 = "2"  # ephemeral — not backed up


class BackupRecord(BaseModel):
    """One backup attempt.

    Written by ``BackupJob`` implementations (A.6 batch 2) at the start
    of a run (``status=RUNNING``) and again at the end (``SUCCESS`` /
    ``FAILED``). The ``(asset_type, asset_ref)`` pair is unique so reruns
    overwrite the same physical artifact slot rather than accumulating
    duplicate rows.
    """

    model_config = ConfigDict(frozen=True)

    id: int | None = Field(default=None, description="DB autoincrement; None pre-insert")
    asset_type: BackupAssetType
    asset_ref: str = Field(description="S3 URL / git ref / similar locator")
    started_at: datetime
    finished_at: datetime | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    status: BackupStatus
    error: str | None = None
    region: str = Field(description="Aliyun region (e.g., 'cn-hangzhou') or 'local'")
    tier: BackupTier


class DrillType(StrEnum):
    """Categories of DR drill exercises."""

    RESTORE_POSTGRES = "restore_postgres"
    FAILOVER_REGION = "failover_region"
    VAULT_RESTORE = "vault_restore"


class DrillRecord(BaseModel):
    """One quarterly DR drill outcome.

    M0 inserts one row at the end of the manual drill; M1+ runs drills
    programmatically via ``DrillRunner`` (subsystems/22 § 4.3).
    """

    model_config = ConfigDict(frozen=True)

    id: int | None = Field(default=None, description="DB autoincrement; None pre-insert")
    drill_type: DrillType
    started_at: datetime
    finished_at: datetime | None = None
    rpo_actual_s: int | None = Field(default=None, description="Measured RPO in seconds")
    rto_actual_s: int | None = Field(default=None, description="Measured RTO in seconds")
    target_rpo_s: int = Field(description="Target RPO from § 3.1")
    target_rto_s: int = Field(description="Target RTO from § 3.1")
    passed: bool | None = None
    notes: str | None = None
