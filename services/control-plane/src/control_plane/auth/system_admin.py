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

from expert_work.persistence.auth import RoleBindingStore
from expert_work.protocol import Principal

logger = logging.getLogger("expert_work.control_plane.auth.system_admin")


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


async def maybe_bootstrap_system_admin(
    principal: Principal,
    role_binding_store: RoleBindingStore | None,
    *,
    bootstrap_email: str | None,
) -> Principal:
    """First-login auto-grant of the first platform ``system_admin`` — Stream ACCT.

    Removes the need to run ``python -m control_plane.bootstrap_admin`` on a
    fresh deployment: the operator instead sets ``EXPERT_WORK_BOOTSTRAP_ADMIN_EMAIL``
    and the matching user becomes ``system_admin`` the first time they log in.

    The grant is gated by **every** of the following — any miss returns the
    input ``principal`` unchanged (no write):

    * a store is wired and ``bootstrap_email`` is configured (non-empty);
    * the principal is not already ``is_system_admin`` (callers run this only
      after :func:`resolve_system_admin`, but the guard is defensive);
    * the subject is a human ``user`` with a UUID-shaped ``subject_id``;
    * the JWT carried a **verified** email equal (case-insensitive) to
      ``bootstrap_email`` — ``email_verified=false`` is rejected;
    * **the system holds zero platform admins** (the zero-admin gate). Once any
      platform admin exists — granted here, via the API, or via the CLI script
      — this path never fires again, so it can never be used to escalate after
      the first admin is established.

    The platform ``role_binding`` table is FORCE-RLS; reads and the write run
    inside :func:`bypass_rls_session`, mirroring ``bootstrap_admin.py``.
    """
    if role_binding_store is None or not bootstrap_email:
        return principal
    if principal.is_system_admin or principal.subject_type != "user":
        return principal
    if not principal.email_verified or not principal.email:
        return principal
    if principal.email.casefold() != bootstrap_email.casefold():
        return principal
    try:
        subject_uuid = UUID(principal.subject_id)
    except (ValueError, AttributeError):
        return principal

    # Local import avoids a module-load cycle: bootstrap_admin imports settings
    # + persistence, this module is imported by AuthMiddleware at app build.
    from control_plane.bootstrap_admin import bootstrap_system_admin
    from control_plane.tenant_scope import bypass_rls_session

    async with bypass_rls_session():
        if await role_binding_store.list_platform_scope():
            # A platform admin already exists — never auto-grant again.
            return principal

    result = await bootstrap_system_admin(
        role_binding_store,
        subject_id=subject_uuid,
        granted_by="bootstrap-first-login",
    )
    logger.info(
        "system_admin.bootstrap.first_login",
        extra={"subject_id": principal.subject_id, "binding_id": str(result.binding.id)},
    )
    return principal.as_system_admin()
