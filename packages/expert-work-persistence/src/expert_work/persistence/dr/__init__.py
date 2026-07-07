"""DR metadata Repository (per subsystems/22-disaster-recovery).

State-layer store for ``backup_record`` + ``dr_drill``. The ``PostgresFullBackup``
service that consumes this is Stream A.6 batch 2.
"""

from expert_work.persistence.dr.base import BackupRecordStore as BackupRecordStore
from expert_work.persistence.dr.memory import (
    InMemoryBackupRecordStore as InMemoryBackupRecordStore,
)
from expert_work.persistence.dr.sql import SqlBackupRecordStore as SqlBackupRecordStore

__all__ = [
    "BackupRecordStore",
    "InMemoryBackupRecordStore",
    "SqlBackupRecordStore",
]
