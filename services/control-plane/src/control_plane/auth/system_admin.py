"""Stream N — resolve ``Principal.is_system_admin`` from the role-binding store.

After ``JWTVerifier`` / ``ApiKeyVerifier`` produces a base :class:`Principal`,
``AuthMiddleware`` calls :func:`resolve_system_admin` to query the platform-scope
role-binding for the verified subject. If a row exists, the Principal is
augmented with ``is_system_admin=True`` and ``allowed_tenants="*"`` via
:meth:`Principal.as_system_admin`.

This lookup is **per-request** today (M0). M1 may layer an in-process cache
keyed on ``(subject_type, subject_id)`` with TTL invalidation on
role-binding writes; for now the cost is one indexed PK-style query per
authenticated request.

Mini-ADR N-2 (``docs/streams/STREAM-N-DESIGN.md``):the lookup is gated by
``subject_type`` — only ``"user"`` subjects can be platform admins (M0
restriction;``service_account`` platform admins ship in M1, see
out-of-scope in STREAM-N-DESIGN § 1.2).
"""

from __future__ import annotations

import logging
from uuid import UUID

from helix_agent.persistence.auth import RoleBindingStore
from helix_agent.protocol import Principal

logger = logging.getLogger("helix.control_plane.auth.system_admin")


async def resolve_system_admin(
    principal: Principal,
    role_binding_store: RoleBindingStore | None,
) -> Principal:
    """Augment ``principal`` with platform-admin status, if applicable.

    Returns the input ``principal`` unchanged when:

    * ``role_binding_store`` is ``None`` (e.g. unit tests / lightweight
      configurations that did not wire a store);
    * the subject type is not ``"user"`` (M0 restriction);
    * the ``subject_id`` is not a UUID (some test fixtures use synthetic
      string subject ids);
    * the subject has no platform-scope role binding.

    Otherwise returns ``principal.as_system_admin()``, which sets
    ``is_system_admin=True`` and ``allowed_tenants="*"``. ``tenant_id``
    is left untouched so the user's "home tenant" stays the default
    scope until they explicitly request another via the route's
    ``tenant_id`` query parameter (Stream N — Mini-ADR N-3, N.3).
    """
    if role_binding_store is None:
        return principal

    # M0:platform admins are human users only (see STREAM-N-DESIGN § 1.2).
    if principal.subject_type != "user":
        return principal

    try:
        subject_uuid = UUID(principal.subject_id)
    except (ValueError, AttributeError):
        # Subject ids that are not UUID-shaped cannot match a row in
        # ``role_binding.subject_id`` (a ``UUID`` column). Skip the
        # lookup rather than emit a noisy error.
        return principal

    binding = await role_binding_store.get_platform_admin_for_subject(
        subject_type="user", subject_id=subject_uuid
    )
    if binding is None:
        return principal

    logger.debug(
        "system_admin.resolved",
        extra={
            "subject_id": principal.subject_id,
            "binding_id": str(binding.id),
        },
    )
    return principal.as_system_admin()
