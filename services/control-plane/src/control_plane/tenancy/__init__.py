"""Tenancy primitives for the Control Plane — Stream C.4 onwards.

Exposes the per-request RLS middleware that projects the authenticated
principal's ``tenant_id`` into the ContextVar consumed by
:mod:`helix_agent.persistence.rls`.
"""

from control_plane.tenancy.rls_context import RLSContextMiddleware

__all__ = ["RLSContextMiddleware"]
