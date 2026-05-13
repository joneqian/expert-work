"""Tenancy primitives for the Control Plane — Stream C.4 onwards.

Exposes:

* :class:`RLSContextMiddleware` — projects the authenticated
  principal's ``tenant_id`` into the ContextVar consumed by
  :mod:`helix_agent.persistence.rls` (C.4).
* :class:`TenantConfigService` — cached, audit-emitting accessor for
  ``tenant_config`` rows (C.7).
"""

from control_plane.tenancy.rls_context import RLSContextMiddleware
from control_plane.tenancy.tenant_config import (
    TenantConfigNotConfiguredError,
    TenantConfigService,
)

__all__ = [
    "RLSContextMiddleware",
    "TenantConfigNotConfiguredError",
    "TenantConfigService",
]
