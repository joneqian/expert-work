"""Helix-Agent persistence — SQLAlchemy 2.0 async ORM + Alembic migrations."""

# Explicit `as` re-exports signal intentional public API to static analyzers
# (mypy --strict, CodeQL py/unused-import).
from helix_agent.persistence.audit_log import AuditLogStore as AuditLogStore
from helix_agent.persistence.audit_log import (
    InMemoryAuditLogStore as InMemoryAuditLogStore,
)
from helix_agent.persistence.audit_log import SqlAuditLogStore as SqlAuditLogStore
from helix_agent.persistence.base import Base as Base
from helix_agent.persistence.database import DatabaseConfig as DatabaseConfig
from helix_agent.persistence.database import (
    create_async_engine_from_config as create_async_engine_from_config,
)
from helix_agent.persistence.database import (
    create_async_session_factory as create_async_session_factory,
)
from helix_agent.persistence.dr import BackupRecordStore as BackupRecordStore
from helix_agent.persistence.dr import (
    InMemoryBackupRecordStore as InMemoryBackupRecordStore,
)
from helix_agent.persistence.dr import SqlBackupRecordStore as SqlBackupRecordStore
from helix_agent.persistence.models import AuditLogRow as AuditLogRow
from helix_agent.persistence.models import BackupRecordRow as BackupRecordRow
from helix_agent.persistence.models import DrDrillRow as DrDrillRow
from helix_agent.persistence.models import EventLogRow as EventLogRow
from helix_agent.persistence.models import ThreadMetaRow as ThreadMetaRow
from helix_agent.persistence.rls import RLS_GUC_NAME as RLS_GUC_NAME
from helix_agent.persistence.rls import build_rls_sessionmaker as build_rls_sessionmaker
from helix_agent.persistence.rls import bypass_rls_var as bypass_rls_var
from helix_agent.persistence.rls import current_tenant_id_var as current_tenant_id_var
from helix_agent.persistence.thread_meta import (
    InMemoryThreadMetaStore as InMemoryThreadMetaStore,
)
from helix_agent.persistence.thread_meta import (
    SqlThreadMetaStore as SqlThreadMetaStore,
)
from helix_agent.persistence.thread_meta import ThreadMetaStore as ThreadMetaStore

__all__ = [
    "RLS_GUC_NAME",
    "AuditLogRow",
    "AuditLogStore",
    "BackupRecordRow",
    "BackupRecordStore",
    "Base",
    "DatabaseConfig",
    "DrDrillRow",
    "EventLogRow",
    "InMemoryAuditLogStore",
    "InMemoryBackupRecordStore",
    "InMemoryThreadMetaStore",
    "SqlAuditLogStore",
    "SqlBackupRecordStore",
    "SqlThreadMetaStore",
    "ThreadMetaRow",
    "ThreadMetaStore",
    "build_rls_sessionmaker",
    "bypass_rls_var",
    "create_async_engine_from_config",
    "create_async_session_factory",
    "current_tenant_id_var",
]
