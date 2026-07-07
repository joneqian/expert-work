"""Unit tests for the Stream N Principal extensions.

* ``allowed_tenants`` extended to ``tuple[UUID, ...] | Literal["*"]``
* ``is_system_admin: bool = False`` (default)
* ``Principal.as_system_admin()`` returns a system-admin copy
"""

from __future__ import annotations

from uuid import uuid4

from expert_work.protocol import ALL_TENANTS, JWTClaims, Principal


def test_principal_defaults_to_not_system_admin() -> None:
    p = Principal(
        subject_id="u-1",
        subject_type="user",
        tenant_id=uuid4(),
        roles=("admin",),
        allowed_tenants=(uuid4(),),
    )
    assert p.is_system_admin is False


def test_principal_accepts_all_tenants_sentinel() -> None:
    p = Principal(
        subject_id="u-1",
        subject_type="user",
        tenant_id=uuid4(),
        allowed_tenants=ALL_TENANTS,
        is_system_admin=True,
    )
    assert p.allowed_tenants == "*"
    assert p.is_system_admin is True


def test_from_jwt_claims_defaults_to_not_system_admin() -> None:
    """JWT claims alone do not promote to system_admin — the role-binding
    lookup in :mod:`control_plane.auth.system_admin` is the authoritative source."""
    tenant = uuid4()
    claims = JWTClaims(
        iss="i",
        sub="u-1",
        aud=("a",),
        exp=999,
        tenant_id=tenant,
    )
    p = Principal.from_jwt_claims(claims)
    assert p.is_system_admin is False
    assert p.allowed_tenants == (tenant,)


def test_as_system_admin_returns_augmented_copy() -> None:
    tenant = uuid4()
    p = Principal(
        subject_id="u-1",
        subject_type="user",
        tenant_id=tenant,
        roles=("admin",),
        allowed_tenants=(tenant,),
    )
    sa = p.as_system_admin()
    assert sa.is_system_admin is True
    assert sa.allowed_tenants == "*"
    # tenant_id and other fields are preserved.
    assert sa.tenant_id == tenant
    assert sa.subject_id == "u-1"
    assert sa.roles == ("admin",)
    # The original is unchanged (frozen + model_copy returns a new instance).
    assert p.is_system_admin is False
    assert p.allowed_tenants == (tenant,)


def test_as_system_admin_idempotent() -> None:
    """Calling ``as_system_admin`` twice yields the same shape — useful for
    defensive callers without a precondition check."""
    p = Principal(subject_id="u-1", subject_type="user", tenant_id=uuid4())
    sa1 = p.as_system_admin()
    sa2 = sa1.as_system_admin()
    assert sa2.is_system_admin is True
    assert sa2.allowed_tenants == "*"
