"""Audit service layer — :class:`AuditLogger` + redactor + fallback queue.

Layers on top of :mod:`expert_work.persistence.audit_log` (the Repository).
"""

from expert_work.runtime.audit.fallback import AuditFallbackQueue as AuditFallbackQueue
from expert_work.runtime.audit.fallback import FallbackRecord as FallbackRecord
from expert_work.runtime.audit.fallback import (
    InMemoryAuditFallbackQueue as InMemoryAuditFallbackQueue,
)
from expert_work.runtime.audit.fallback import (
    JsonlFileAuditFallbackQueue as JsonlFileAuditFallbackQueue,
)
from expert_work.runtime.audit.logger import AuditLogger as AuditLogger
from expert_work.runtime.audit.logger import RedactionHitCallback as RedactionHitCallback
from expert_work.runtime.audit.redactor import PII_FIELD_HIT as PII_FIELD_HIT
from expert_work.runtime.audit.redactor import REPLACEMENT as REPLACEMENT
from expert_work.runtime.audit.redactor import AuditRedactor as AuditRedactor
from expert_work.runtime.audit.redactor import (
    DefaultSecretRedactor as DefaultSecretRedactor,
)
from expert_work.runtime.audit.redactor import PiiFieldsResolver as PiiFieldsResolver
from expert_work.runtime.audit.redactor import RedactionResult as RedactionResult
from expert_work.runtime.audit.redactor import (
    TenantAwareRedactor as TenantAwareRedactor,
)

__all__ = [
    "PII_FIELD_HIT",
    "REPLACEMENT",
    "AuditFallbackQueue",
    "AuditLogger",
    "AuditRedactor",
    "DefaultSecretRedactor",
    "FallbackRecord",
    "InMemoryAuditFallbackQueue",
    "JsonlFileAuditFallbackQueue",
    "PiiFieldsResolver",
    "RedactionHitCallback",
    "RedactionResult",
    "TenantAwareRedactor",
]
