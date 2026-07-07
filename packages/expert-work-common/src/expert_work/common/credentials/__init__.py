"""Stream O — Credentials & Provider Catalog (expert-work-common surface).

Exposes :class:`CredentialsResolver` so callers across orchestrator,
control-plane, and any other service can resolve a ``(tenant, provider/tool)``
pair to a secret_ref through one consistent path.
"""

from expert_work.common.credentials.resolver import (
    CredentialsResolver,
    CredentialsResolverError,
    TenantConfigGetter,
)

__all__ = [
    "CredentialsResolver",
    "CredentialsResolverError",
    "TenantConfigGetter",
]
