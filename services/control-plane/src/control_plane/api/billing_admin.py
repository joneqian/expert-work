"""Platform billing chargeback API — Stream Z (Mini-ADR Z-2).

system_admin-only, cross-tenant view of the Y4 ``tenant_billing_ledger``. Unlike
the tenant-facing ``/v1/usage`` surface (billed only), this admin surface shows
the FULL split — ``base_cost``/``markup``/``billed`` + ``margin`` — per tenant,
so the platform can see its cost vs gross margin. Every handler:

* gates on ``require("billing", "read")`` then re-checks ``is_system_admin``
  inline (defense in depth — platform surface, same precedent as rate_card.py);
* reads the ledger inside ``bypass_rls_session()`` (the ledger is tenant-scoped
  RLS, so a normally-scoped session would see only one tenant).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from control_plane.api._authz import require
from control_plane.tenant_scope import bypass_rls_session
from helix_agent.persistence import TenantBillingLedgerStore
from helix_agent.protocol import Principal

from .usage import _parse_month  # shared YYYY-MM parser (422 on bad input)


def _get_ledger_store(request: Request) -> TenantBillingLedgerStore:
    return request.app.state.tenant_billing_ledger_store  # type: ignore[no-any-return]


def _require_system_admin(principal: Principal) -> None:
    if not principal.is_system_admin:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "PLATFORM_SCOPE_FORBIDDEN",
                "message": "only a system admin may view cross-tenant chargeback",
            },
        )


@dataclass
class _TenantCharge:
    """One tenant's month rollup — admin view, FULL split + margin."""

    tenant_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    base_cost_micros: int = 0
    markup_cost_micros: int = 0
    billed_cost_micros: int = 0
    margin_micros: int = 0  # == markup_cost_micros (platform gross margin)
    unpriced_buckets: int = 0


def build_billing_admin_router() -> APIRouter:
    router = APIRouter(prefix="/v1/admin/billing", tags=["billing_admin"])

    @router.get("/chargeback")
    async def chargeback(
        principal: Annotated[Principal, Depends(require("billing", "read"))],
        store: Annotated[TenantBillingLedgerStore, Depends(_get_ledger_store)],
        month: Annotated[str | None, Query()] = None,
        tenant_id: Annotated[UUID | None, Query()] = None,
    ) -> dict[str, object]:
        _require_system_admin(principal)
        target = _parse_month(month)
        async with bypass_rls_session():
            rows = await store.list_for_month_all_tenants(month=target)

        charges: dict[str, _TenantCharge] = defaultdict(lambda: _TenantCharge(tenant_id=""))
        for r in rows:
            if tenant_id is not None and r.tenant_id != tenant_id:
                continue
            key = str(r.tenant_id)
            c = charges[key]
            c.tenant_id = key
            c.input_tokens += r.input_tokens
            c.output_tokens += r.output_tokens
            c.cache_creation_tokens += r.cache_creation_tokens
            c.cache_read_tokens += r.cache_read_tokens
            c.base_cost_micros += r.base_cost_micros
            c.markup_cost_micros += r.markup_cost_micros
            c.billed_cost_micros += r.billed_cost_micros
            c.margin_micros += r.markup_cost_micros
            if not r.priced:
                c.unpriced_buckets += 1

        tenants = sorted(charges.values(), key=lambda c: c.tenant_id)
        return {
            "success": True,
            "data": {
                "month": target.strftime("%Y-%m"),
                "as_of": datetime.now(tz=UTC).isoformat(),
                "total_base_cost_micros": sum(c.base_cost_micros for c in tenants),
                "total_billed_cost_micros": sum(c.billed_cost_micros for c in tenants),
                "total_margin_micros": sum(c.margin_micros for c in tenants),
                "tenants": [asdict(c) for c in tenants],
            },
            "error": None,
        }

    return router
