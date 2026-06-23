"""Role-based access control decision matrix — Stream C.3.

Single source of truth for the ``(role, resource, action)`` → ``bool``
table from ``subsystems/15-authn-authz.md`` § 3.3. Used by route-level
``authorize()`` dependencies.

Design notes
------------

* Decisions are **deny by default** — only explicit matrix entries grant.
* mTLS service principals get a synthetic ``service`` role with read+write
  over internal resources (``quota``); they cannot touch admin resources
  (``user`` / ``role_binding``).
* API-key service-account principals carry their assigned role via
  :class:`helix_agent.persistence.auth.RoleBindingStore` (resolved
  lazily — pulled in C.3 follow-ups). For this PR, scopes from the key
  itself stand in for roles in :func:`_collect_roles`.
* :func:`authorize` is a **pure** function (no IO, no audit) so it can
  be unit-tested in isolation. The audit write happens at the FastAPI
  dependency layer (``require_role``) so denials still leave a trace.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from helix_agent.protocol import Principal, Role

Resource = Literal[
    "manifest",
    "session",
    "sandbox",
    "secret",
    "audit",
    "quota",
    "tenant_config",
    "user",
    "role_binding",
    "service_account",
    "api_key",
    "memory",  # Stream K.K6 — long-term memory CRUD
    "mcp_server",  # Stream V — tenant remote MCP server registry
    "mcp_catalog",  # Stream W — platform MCP connector catalog (system_admin)
    "mcp_oauth",  # Stream MCP-OAUTH — per-user OAuth connections (own-scoped)
    "billing",  # Stream Y — platform model rate card (system_admin)
]

Action = Literal[
    "read",
    "write",
    "delete",
    "debug",
    "sign",
    "approve",
    "force_destroy",
    # ``check`` covers runtime quota operations (check / reserve /
    # commit / release). Separating it from ``write`` lets us grant
    # service principals (mTLS) the right to consume the quota engine
    # without also letting them rewrite the tenant_quota config.
    "check",
]


def _grants(role: Role) -> dict[Resource, set[Action]]:
    """Return the (resource → allowed actions) map for one role."""
    if role is Role.ADMIN:
        return {
            "manifest": {"read", "write", "delete", "sign", "approve"},
            "session": {"read", "write", "delete", "debug"},
            "sandbox": {"read", "force_destroy"},
            "secret": {"read", "write", "delete"},
            "audit": {"read"},
            "quota": {"read", "write", "delete", "check"},
            # Admin can edit tenant_config (display name, MCP allowlist,
            # secret refs, PII fields …) and read it.
            "tenant_config": {"read", "write"},
            "user": {"read", "write"},
            "role_binding": {"read", "write", "delete"},
            "service_account": {"read", "write", "delete"},
            "api_key": {"read", "write", "delete"},
            # Stream K.K6 — admin can manage every user's memory
            # (support workflows: forget on demand etc.). Per-user
            # filtering still applies in the endpoint via
            # ``caller_user_id``; admin has no extra cross-user power.
            "memory": {"read", "write", "delete"},
            # Stream V — tenant remote MCP server registry
            "mcp_server": {"read", "write", "delete"},
            # Stream W — platform MCP connector catalog (system_admin auto-gets
            # ADMIN via is_allowed; the endpoint also re-checks is_system_admin).
            "mcp_catalog": {"read", "write", "delete"},
            # Stream MCP-OAUTH — per-user OAuth connections. Own-scoped: every
            # endpoint filters by principal.subject_id, so write/delete only
            # ever touch the caller's own connection.
            "mcp_oauth": {"read", "write", "delete"},
            # Stream Y — platform model rate card (system_admin auto-gets ADMIN
            # via is_allowed; the endpoint also re-checks is_system_admin).
            "billing": {"read", "write", "delete"},
        }
    if role is Role.OPERATOR:
        return {
            "manifest": {"read", "write"},
            "session": {"read", "write", "debug"},
            "sandbox": {"read", "force_destroy"},
            "secret": {"read"},
            "audit": {"read"},
            # Operators (mTLS service principals) consume the quota
            # engine at runtime but cannot rewrite tenant_quota config.
            "quota": {"read", "check"},
            # Operators (mTLS services) read tenant_config on the
            # request hot path (LLM gateway / MCP gateway, Stream E).
            # They cannot rewrite it.
            "tenant_config": {"read"},
            # Stream K.K6 — operators (typically a regular logged-in
            # user via JWT carrying ``operator``) own their own memory
            # CRUD. Per-user scoping is enforced in the endpoint.
            "memory": {"read", "write", "delete"},
            # Stream V — tenant remote MCP server registry
            "mcp_server": {"read"},
            # Stream W — platform MCP connector catalog
            "mcp_catalog": {"read"},
            # Stream MCP-OAUTH — a regular user connects/manages their OWN OAuth
            # connections (the per-user agent scenario); endpoint is subject-scoped.
            "mcp_oauth": {"read", "write", "delete"},
            # Stream Y — platform model rate card
            "billing": {"read"},
        }
    # VIEWER
    return {
        "manifest": {"read"},
        "session": {"read"},
        "sandbox": {"read"},
        "audit": {"read"},
        "quota": {"read"},
        "tenant_config": {"read"},
        # Viewers can list their own memory but not edit / forget.
        "memory": {"read"},
        # Stream V — tenant remote MCP server registry
        "mcp_server": {"read"},
        # Stream W — platform MCP connector catalog
        "mcp_catalog": {"read"},
        # Stream MCP-OAUTH — viewers can see their own connection list only.
        "mcp_oauth": {"read"},
        # Stream Y — platform model rate card
        "billing": {"read"},
    }


def grants_for(role: Role) -> dict[Resource, set[Action]]:
    """Public accessor for one role's (resource → actions) grant map.

    Stream 8.5 — the ABAC layer (:mod:`control_plane.auth.abac`) needs the same
    per-role grant table to decide whether a conditioned binding's role covers
    a ``(resource, action)`` before checking its conditions.
    """
    return _grants(role)


def _collect_roles(principal: Principal) -> set[Role]:
    """Translate principal-level role hints into the typed :class:`Role`."""
    out: set[Role] = set()
    # JWT path: roles claim is a flat list of strings.
    for raw in principal.roles:
        try:
            out.add(Role(raw))
        except ValueError:
            continue
    # mTLS path: subject_type=service → grant operator-equivalent access
    # to ``quota`` and read on session/manifest. Modelled as OPERATOR.
    if principal.subject_type == "service":
        out.add(Role.OPERATOR)
    # API-key path: roles came from the role_binding store at verify
    # time (future enrichment). Without that wiring the scopes column
    # acts as a fallback: ``admin`` scope → ADMIN role, ``write`` → OPERATOR,
    # ``read`` → VIEWER.
    if principal.subject_type == "service_account":
        scope_set = set(principal.scopes)
        if "admin" in scope_set:
            out.add(Role.ADMIN)
        elif "write" in scope_set:
            out.add(Role.OPERATOR)
        elif "read" in scope_set:
            out.add(Role.VIEWER)
    return out


def is_allowed(principal: Principal, *, resource: Resource, action: Action) -> bool:
    """``True`` if any of ``principal``'s roles grants ``(resource, action)``.

    A platform ``system_admin`` (Stream N) carries no tenant-scope role, but
    when operating within a tenant it must be able to manage that tenant's
    resources — matching the "switch into a tenant and manage it" UX (Stream U)
    and unblocking every matrix-gated tenant page (mcp_server / user /
    tenant_config / role_binding …). We therefore grant it tenant-ADMIN
    authority here. Tenant data isolation is still enforced downstream by
    ``tenant_id`` scoping + RLS + ``allowed_tenants`` checks at the route layer
    — this only widens the *role* decision, not the *tenant* boundary.
    """
    roles = _collect_roles(principal)
    if principal.is_system_admin:
        roles = roles | {Role.ADMIN}
    for role in roles:
        if action in _grants(role).get(resource, set()):
            return True
    return False


def is_admin(principal: Principal) -> bool:
    """``True`` if the principal holds the ADMIN role (tenant-wide access).

    Used by Stream J.14 per-user scoping: a tenant admin bypasses the
    per-user thread-ownership check and keeps tenant-wide visibility.
    """
    return Role.ADMIN in _collect_roles(principal)


def collect_roles_for_audit(principal: Principal) -> Iterable[str]:
    """Stable role list for audit details payloads."""
    return sorted(role.value for role in _collect_roles(principal))
