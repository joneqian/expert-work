"""HTTP tests for :class:`TenantRateLimitMiddleware` — Stream C.6."""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from control_plane.app import create_app
from control_plane.audit import build_default_audit_logger
from control_plane.ratelimit import InProcessTokenBucketLimiter, RateLimiter
from control_plane.settings import DEFAULT_DEV_TENANT_ID, Settings
from helix_agent.persistence.audit_log import InMemoryAuditLogStore
from helix_agent.protocol import AuditAction, AuditQuery
from tests.auth_fixtures import TEST_AUDIENCE, TEST_ISSUER, build_test_jwt_verifier, make_test_jwt

_TENANT = DEFAULT_DEV_TENANT_ID


@pytest.fixture
def audit_store() -> InMemoryAuditLogStore:
    return InMemoryAuditLogStore()


def _settings(*, tenant_rate_limit_enabled: bool = True) -> Settings:
    return Settings(
        env="dev",
        auth_mode="dev",
        # Gateway tier kept wide open so it doesn't interfere with the
        # per-tenant tests.
        rate_limit_burst=10_000,
        rate_limit_per_second=10_000.0,
        # Tenant tier is what the tests exercise — concrete values
        # injected per test via the ``tenant_rate_limiter`` kwarg.
        tenant_rate_limit_enabled=tenant_rate_limit_enabled,
        tenant_rate_limit_capacity=10_000,
        tenant_rate_limit_refill_per_sec=10_000.0,
        tenant_rate_limit_audit_sample_every=1,
        oidc_issuer=TEST_ISSUER,
        oidc_audience=[TEST_AUDIENCE],
    )


def _build_client(
    *,
    audit_store: InMemoryAuditLogStore,
    tenant_limiter: RateLimiter,
    enabled: bool = True,
) -> AsyncClient:
    settings = _settings(tenant_rate_limit_enabled=enabled)
    app = create_app(
        settings=settings,
        audit_logger=build_default_audit_logger(audit_store),
        jwt_verifier=build_test_jwt_verifier(),
        tenant_rate_limiter=tenant_limiter,
        enable_reaper=False,
    )
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://control-plane.test")


# ---------------------------------------------------------------------------
# Happy path / exemption
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authenticated_request_passes_with_room_in_bucket(
    audit_store: InMemoryAuditLogStore,
) -> None:
    limiter = InProcessTokenBucketLimiter(capacity=10, refill_per_sec=10.0)
    async with _build_client(audit_store=audit_store, tenant_limiter=limiter) as client:
        token = make_test_jwt(tenant_id=_TENANT)
        resp = await client.get("/v1/agents", headers={"Authorization": f"Bearer {token}"})
        # 200 OK (empty list) — admin role has agent:read.
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_health_path_bypasses_tenant_limiter(
    audit_store: InMemoryAuditLogStore,
) -> None:
    """Even a fully-drained bucket lets /healthz/* through."""
    limiter = InProcessTokenBucketLimiter(capacity=1, refill_per_sec=0.001)
    # Drain via a separate call so /healthz hits an empty bucket if it
    # were inside the limit ring (it isn't — exempt prefix).
    await limiter.acquire(dimension="tenant", key="anything")
    async with _build_client(audit_store=audit_store, tenant_limiter=limiter) as client:
        resp = await client.get("/healthz/live")
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_disabled_middleware_is_a_no_op(audit_store: InMemoryAuditLogStore) -> None:
    """With ``tenant_rate_limit_enabled=False`` the limiter is never consulted."""
    limiter = InProcessTokenBucketLimiter(capacity=1, refill_per_sec=0.001)
    async with _build_client(
        audit_store=audit_store, tenant_limiter=limiter, enabled=False
    ) as client:
        token = make_test_jwt(tenant_id=_TENANT)
        # 50 requests, all should pass.
        for _ in range(50):
            resp = await client.get("/v1/agents", headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Denial
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_burst_exhaustion_returns_429_with_retry_after(
    audit_store: InMemoryAuditLogStore,
) -> None:
    # capacity=1, slow refill so the second hit is firmly denied.
    limiter = InProcessTokenBucketLimiter(capacity=1, refill_per_sec=0.01)
    async with _build_client(audit_store=audit_store, tenant_limiter=limiter) as client:
        token = make_test_jwt(tenant_id=_TENANT)

        first = await client.get("/v1/agents", headers={"Authorization": f"Bearer {token}"})
        assert first.status_code == 200

        denied = await client.get("/v1/agents", headers={"Authorization": f"Bearer {token}"})
        assert denied.status_code == 429
        body = denied.json()
        assert body["success"] is False
        assert body["error"]["code"] == "RATE_LIMIT_EXCEEDED"
        assert body["error"]["dimension"] == "tenant"
        assert body["error"]["retry_after_s"] >= 1
        assert denied.headers["Retry-After"] == str(body["error"]["retry_after_s"])


@pytest.mark.asyncio
async def test_denial_emits_sampled_audit(audit_store: InMemoryAuditLogStore) -> None:
    """``tenant_rate_limit_audit_sample_every=1`` → every denial audits."""
    limiter = InProcessTokenBucketLimiter(capacity=1, refill_per_sec=0.01)
    async with _build_client(audit_store=audit_store, tenant_limiter=limiter) as client:
        token = make_test_jwt(tenant_id=_TENANT)
        first = await client.get("/v1/agents", headers={"Authorization": f"Bearer {token}"})
        assert first.status_code == 200
        denied = await client.get("/v1/agents", headers={"Authorization": f"Bearer {token}"})
        assert denied.status_code == 429

    page = await audit_store.query(AuditQuery(tenant_id=_TENANT))
    matches = [
        e
        for e in page.entries
        if e.action is AuditAction.QUOTA_RATE_LIMIT_DENIED
        and e.details.get("dimension") == "tenant"
    ]
    assert matches, f"no tenant-dim denial audit; saw: {[e.action.value for e in page.entries]}"


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_buckets_are_isolated(audit_store: InMemoryAuditLogStore) -> None:
    """Drained tenant A bucket does not throttle tenant B."""
    limiter = InProcessTokenBucketLimiter(capacity=1, refill_per_sec=0.01)
    other_tenant = uuid4()
    async with _build_client(audit_store=audit_store, tenant_limiter=limiter) as client:
        token_a = make_test_jwt(tenant_id=_TENANT)
        token_b = make_test_jwt(tenant_id=other_tenant)

        # Drain tenant A.
        first_a = await client.get("/v1/agents", headers={"Authorization": f"Bearer {token_a}"})
        assert first_a.status_code == 200
        denied_a = await client.get("/v1/agents", headers={"Authorization": f"Bearer {token_a}"})
        assert denied_a.status_code == 429

        # Tenant B still has a full bucket.
        first_b = await client.get("/v1/agents", headers={"Authorization": f"Bearer {token_b}"})
        assert first_b.status_code == 200
