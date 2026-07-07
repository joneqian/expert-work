"""Per-user registry repository — Stream J.14 (per-user scope).

Makes "user" a first-class entity: ``(tenant_id, subject_type,
subject_id)`` identity, surrogate ``user_id`` referenced by owned tables.
See ``docs/streams/STREAM-J-DESIGN.md`` § 4.
"""

from expert_work.persistence.tenant_user.base import TenantUserStore as TenantUserStore
from expert_work.persistence.tenant_user.memory import (
    InMemoryTenantUserStore as InMemoryTenantUserStore,
)
from expert_work.persistence.tenant_user.sql import (
    SqlTenantUserStore as SqlTenantUserStore,
)

__all__ = ["InMemoryTenantUserStore", "SqlTenantUserStore", "TenantUserStore"]
