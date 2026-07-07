"""Audit log repository — append-only operational audit (subsystems/17).

The Repository pattern mirrors ``thread_meta``: an abstract
:class:`AuditLogStore` with in-memory + Postgres implementations. The
higher-level ``AuditLogger`` service that adds PII redaction, fallback
queue, and self-audit on read is Stream A.4 batch 2.
"""

from expert_work.persistence.audit_log.base import AuditLogStore as AuditLogStore
from expert_work.persistence.audit_log.cursor import decode_cursor as decode_cursor
from expert_work.persistence.audit_log.cursor import encode_cursor as encode_cursor
from expert_work.persistence.audit_log.memory import (
    InMemoryAuditLogStore as InMemoryAuditLogStore,
)
from expert_work.persistence.audit_log.sql import SqlAuditLogStore as SqlAuditLogStore

__all__ = [
    "AuditLogStore",
    "InMemoryAuditLogStore",
    "SqlAuditLogStore",
    "decode_cursor",
    "encode_cursor",
]
