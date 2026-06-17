"""Unit tests for first-login system_admin bootstrap — Stream ACCT (Mini-ADR ACCT-1).

:func:`maybe_bootstrap_system_admin` auto-grants the configured bootstrap email
a platform ``system_admin`` binding on first login, but ONLY while the system
holds zero platform admins (the zero-admin gate). This makes a fresh deployment
self-provisioning without a manual script, without ever becoming a privilege-
escalation path once an admin exists.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from control_plane.auth.system_admin import maybe_bootstrap_system_admin
from helix_agent.persistence.auth import InMemoryRoleBindingStore
from helix_agent.protocol import Principal, Role

BOOTSTRAP_EMAIL = "founder@corp.com"


def _principal(
    *,
    email: str | None = BOOTSTRAP_EMAIL,
    email_verified: bool = True,
    subject_id: str | None = None,
    subject_type: str = "user",
    is_system_admin: bool = False,
) -> Principal:
    return Principal(
        subject_id=subject_id or str(uuid4()),
        subject_type=subject_type,  # type: ignore[arg-type]
        tenant_id=uuid4(),
        email=email,
        email_verified=email_verified,
        is_system_admin=is_system_admin,
    )


async def _platform_admin_count(store: InMemoryRoleBindingStore) -> int:
    return len(await store.list_platform_scope())


@pytest.mark.asyncio
async def test_happy_path_grants_and_promotes() -> None:
    store = InMemoryRoleBindingStore()
    p = _principal()
    out = await maybe_bootstrap_system_admin(p, store, bootstrap_email=BOOTSTRAP_EMAIL)
    assert out.is_system_admin is True
    assert out.allowed_tenants == "*"
    assert await _platform_admin_count(store) == 1


@pytest.mark.asyncio
async def test_email_match_is_case_insensitive() -> None:
    store = InMemoryRoleBindingStore()
    p = _principal(email="Founder@Corp.COM")
    out = await maybe_bootstrap_system_admin(p, store, bootstrap_email=BOOTSTRAP_EMAIL)
    assert out.is_system_admin is True


@pytest.mark.asyncio
async def test_no_store_returns_unchanged() -> None:
    p = _principal()
    out = await maybe_bootstrap_system_admin(p, None, bootstrap_email=BOOTSTRAP_EMAIL)
    assert out is p


@pytest.mark.asyncio
async def test_unset_bootstrap_email_returns_unchanged() -> None:
    store = InMemoryRoleBindingStore()
    p = _principal()
    for email in (None, ""):
        out = await maybe_bootstrap_system_admin(p, store, bootstrap_email=email)
        assert out.is_system_admin is False
    assert await _platform_admin_count(store) == 0


@pytest.mark.asyncio
async def test_already_system_admin_is_noop() -> None:
    store = InMemoryRoleBindingStore()
    p = _principal(is_system_admin=True)
    out = await maybe_bootstrap_system_admin(p, store, bootstrap_email=BOOTSTRAP_EMAIL)
    assert out is p
    assert await _platform_admin_count(store) == 0


@pytest.mark.asyncio
async def test_email_mismatch_does_not_grant() -> None:
    store = InMemoryRoleBindingStore()
    p = _principal(email="someone-else@corp.com")
    out = await maybe_bootstrap_system_admin(p, store, bootstrap_email=BOOTSTRAP_EMAIL)
    assert out.is_system_admin is False
    assert await _platform_admin_count(store) == 0


@pytest.mark.asyncio
async def test_unverified_email_does_not_grant() -> None:
    """email_verified=false must NOT auto-grant — guards against unverified claims."""
    store = InMemoryRoleBindingStore()
    p = _principal(email_verified=False)
    out = await maybe_bootstrap_system_admin(p, store, bootstrap_email=BOOTSTRAP_EMAIL)
    assert out.is_system_admin is False
    assert await _platform_admin_count(store) == 0


@pytest.mark.asyncio
async def test_non_user_subject_does_not_grant() -> None:
    store = InMemoryRoleBindingStore()
    p = _principal(subject_type="service_account")
    out = await maybe_bootstrap_system_admin(p, store, bootstrap_email=BOOTSTRAP_EMAIL)
    assert out.is_system_admin is False
    assert await _platform_admin_count(store) == 0


@pytest.mark.asyncio
async def test_non_uuid_subject_does_not_grant() -> None:
    store = InMemoryRoleBindingStore()
    p = _principal(subject_id="dev-user")
    out = await maybe_bootstrap_system_admin(p, store, bootstrap_email=BOOTSTRAP_EMAIL)
    assert out.is_system_admin is False
    assert await _platform_admin_count(store) == 0


@pytest.mark.asyncio
async def test_existing_admin_blocks_auto_grant() -> None:
    """Zero-admin gate: once ANY platform admin exists, the configured email is
    never auto-promoted — closes the post-bootstrap escalation hole."""
    store = InMemoryRoleBindingStore()
    await store.create(
        subject_type="user",
        subject_id=uuid4(),
        tenant_id=None,
        role=Role.SYSTEM_ADMIN,
        platform_scope=True,
        granted_by="root",
    )
    p = _principal()  # matching, verified email
    out = await maybe_bootstrap_system_admin(p, store, bootstrap_email=BOOTSTRAP_EMAIL)
    assert out.is_system_admin is False
    assert await _platform_admin_count(store) == 1  # no new binding


@pytest.mark.asyncio
async def test_idempotent_second_login_after_grant() -> None:
    store = InMemoryRoleBindingStore()
    subject = str(uuid4())
    first = await maybe_bootstrap_system_admin(
        _principal(subject_id=subject), store, bootstrap_email=BOOTSTRAP_EMAIL
    )
    assert first.is_system_admin is True
    # Second login: a platform admin now exists → zero-admin gate blocks; the
    # per-request resolve_system_admin (not this fn) is what re-promotes them.
    second = await maybe_bootstrap_system_admin(
        _principal(subject_id=subject), store, bootstrap_email=BOOTSTRAP_EMAIL
    )
    assert second.is_system_admin is False
    assert await _platform_admin_count(store) == 1
