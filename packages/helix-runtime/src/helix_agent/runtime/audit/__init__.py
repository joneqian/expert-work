"""Audit service layer — :class:`AuditLogger` + redactor + fallback queue.

Layers on top of :mod:`helix_agent.persistence.audit_log` (the Repository).
"""

from helix_agent.runtime.audit.fallback import AuditFallbackQueue as AuditFallbackQueue
from helix_agent.runtime.audit.fallback import FallbackRecord as FallbackRecord
from helix_agent.runtime.audit.fallback import (
    InMemoryAuditFallbackQueue as InMemoryAuditFallbackQueue,
)
from helix_agent.runtime.audit.fallback import (
    JsonlFileAuditFallbackQueue as JsonlFileAuditFallbackQueue,
)
from helix_agent.runtime.audit.logger import AuditLogger as AuditLogger
from helix_agent.runtime.audit.logger import RedactionHitCallback as RedactionHitCallback
from helix_agent.runtime.audit.redactor import REPLACEMENT as REPLACEMENT
from helix_agent.runtime.audit.redactor import AuditRedactor as AuditRedactor
from helix_agent.runtime.audit.redactor import (
    DefaultSecretRedactor as DefaultSecretRedactor,
)
from helix_agent.runtime.audit.redactor import RedactionResult as RedactionResult

__all__ = [
    "REPLACEMENT",
    "AuditFallbackQueue",
    "AuditLogger",
    "AuditRedactor",
    "DefaultSecretRedactor",
    "FallbackRecord",
    "InMemoryAuditFallbackQueue",
    "JsonlFileAuditFallbackQueue",
    "RedactionHitCallback",
    "RedactionResult",
]
