"""Tenant member roster repository — Stream R (member onboarding).

The invitation-state source of truth: who an admin invited, their role, and
their ``invited → active → suspended / revoked`` lifecycle. Distinct from
``tenant_user`` (runtime JIT registry); connected by ``keycloak_user_id``.
See ``docs/streams/STREAM-R-DESIGN.md`` § 3.
"""

from helix_agent.persistence.tenant_member.base import (
    DuplicateMemberError as DuplicateMemberError,
)
from helix_agent.persistence.tenant_member.base import (
    TenantMemberStore as TenantMemberStore,
)
from helix_agent.persistence.tenant_member.memory import (
    InMemoryTenantMemberStore as InMemoryTenantMemberStore,
)
from helix_agent.persistence.tenant_member.sql import (
    SqlTenantMemberStore as SqlTenantMemberStore,
)

__all__ = [
    "DuplicateMemberError",
    "InMemoryTenantMemberStore",
    "SqlTenantMemberStore",
    "TenantMemberStore",
]
