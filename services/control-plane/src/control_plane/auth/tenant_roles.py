"""Stream R — enrich ``Principal.roles`` from tenant-scope role bindings.

Mirrors :func:`control_plane.auth.system_admin.resolve_system_admin`, but for
*tenant-scope* roles: after the verifier produces a base :class:`Principal`,
``AuthMiddleware`` calls :func:`resolve_tenant_roles` to read the subject's
``role_binding`` rows for their home tenant and merge the granted roles into
``principal.roles``.

Why this exists (Mini-ADR R-7, corrected during W1): tenant RBAC in
``auth.rbac`` reads ``principal.roles``, which until now came *only* from the
JWT ``roles`` claim (Keycloak realm roles). That left ``role_binding`` —
where the Stream R invite flow and the ``POST /v1/role_bindings`` API write a
member's tenant role — unread by the authorization layer. Without this
resolver an invited employee would hold a binding row but ``is_allowed()``
would always return ``False``. This wires the "future enrichment" the RBAC
module flagged, making application-managed authorization actually load-bearing
for tenant roles (the realm-role claim becomes a no-op fallback).

Lookup is per-request today (M0), same cost profile as ``resolve_system_admin``
(one indexed query); an in-process TTL cache is an M1 follow-up.
"""

from __future__ import annotations

import logging
from uuid import UUID

from helix_agent.persistence.auth import RoleBindingStore
from helix_agent.protocol import Principal

logger = logging.getLogger("helix.control_plane.auth.tenant_roles")


async def resolve_tenant_roles(
    principal: Principal,
    role_binding_store: RoleBindingStore | None,
) -> Principal:
    """Merge the subject's tenant-scope role-binding roles into ``principal.roles``.

    Returns ``principal`` unchanged when:

    * ``role_binding_store`` is ``None`` (lightweight / unit configs);
    * the subject type is not ``"user"`` (service accounts derive roles from
      scopes / mTLS identity in ``rbac._collect_roles``, not role bindings);
    * ``tenant_id`` is ``None`` (no home tenant to scope the lookup to);
    * the ``subject_id`` is not a UUID (synthetic test subjects);
    * the subject holds no tenant-scope bindings for their home tenant.

    The merge is additive: any role already present from the JWT claim is kept,
    so this is safe to run before realm roles are removed.
    """
    if role_binding_store is None or principal.subject_type != "user":
        return principal
    if principal.tenant_id is None:
        return principal

    try:
        subject_uuid = UUID(principal.subject_id)
    except (ValueError, AttributeError):
        return principal

    bindings = await role_binding_store.list_for_subject(
        subject_type="user",
        subject_id=subject_uuid,
        tenant_id=principal.tenant_id,
    )
    # list_for_subject(tenant_id=...) returns only tenant-scope rows for that
    # tenant; platform-scope rows are handled separately by resolve_system_admin.
    #
    # Stream 8.5 — only UNCONDITIONED bindings grant a type-wide role here.
    # A conditioned binding must NOT widen ``principal.roles`` (that would make
    # ``is_allowed`` return True for every instance, bypassing its conditions);
    # it is evaluated instance-by-instance at the route layer via
    # ``_authz.require_resource`` / ``abac.authorize_resource`` instead.
    granted = {b.role.value for b in bindings if not b.has_conditions}
    if not granted:
        return principal

    merged = tuple(dict.fromkeys((*principal.roles, *sorted(granted))))
    if merged == principal.roles:
        return principal
    logger.debug(
        "tenant_roles.resolved",
        extra={"subject_id": principal.subject_id, "roles": sorted(granted)},
    )
    return principal.model_copy(update={"roles": merged})
