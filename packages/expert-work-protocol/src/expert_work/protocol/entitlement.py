"""Plan-tier entitlement primitive — Stream W (Mini-ADR W-3).

A single, reusable gate over the pre-existing :class:`TenantPlan`
(free / pro / enterprise). Gated resources (MCP catalog entries, platform
skills, rate-card rows …) each carry a ``required_tier``; a tenant may use a
resource iff its plan tier is **at least** the required tier.

This lives in ``expert-work-protocol`` (not ``expert-work-common``) because the comparison
is a pure function over :class:`TenantPlan`, and ``expert-work-protocol`` is the base
layer every service already imports — placing it here avoids a new cross-package
dependency while still giving control-plane, the skills path, and billing one
thing to import.
"""

from __future__ import annotations

from expert_work.protocol.tenant_config import TenantPlan

__all__ = ["TIER_ORDER", "tier_satisfies"]

# Higher number = more entitled. The only ordering that matters is the
# ``>=`` comparison in :func:`tier_satisfies`.
TIER_ORDER: dict[TenantPlan, int] = {
    TenantPlan.FREE: 0,
    TenantPlan.PRO: 1,
    TenantPlan.ENTERPRISE: 2,
}


def tier_satisfies(tenant_tier: TenantPlan, required_tier: TenantPlan) -> bool:
    """``True`` if ``tenant_tier`` is at least ``required_tier``.

    Gating happens at write / instantiate time (never on the runtime hot path),
    so a later downgrade does not interrupt already-running agents.
    """
    return TIER_ORDER[tenant_tier] >= TIER_ORDER[required_tier]
