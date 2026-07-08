"""Unit tests for ``ensure_member_active`` — Stream R W3 (R-8).

The first-run hook that promotes an invited member to active. Tested directly
against an in-memory member store with a minimal fake request.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from control_plane.api._user_scope import ensure_member_active
from expert_work.persistence import InMemoryTenantMemberStore
from expert_work.protocol import Principal


def _request(*, subject_id: str, member_repo: object | None) -> object:
    principal = Principal(
        subject_id=subject_id,
        subject_type="user",
        tenant_id=uuid4(),
        roles=("operator",),
    )
    app = SimpleNamespace(state=SimpleNamespace(tenant_member_repo=member_repo))
    return SimpleNamespace(state=SimpleNamespace(principal=principal), app=app)


@pytest.mark.asyncio
async def test_promotes_invited_member_on_first_run() -> None:
    store = InMemoryTenantMemberStore()
    tenant = uuid4()
    kc_id = str(uuid4())
    member = await store.create(
        tenant_id=tenant, email="e@co.com", role="operator", invited_by="admin"
    )
    await store.set_keycloak_user_id(member_id=member.id, tenant_id=tenant, keycloak_user_id=kc_id)

    caller_user_id = uuid4()
    req = _request(subject_id=kc_id, member_repo=store)
    await ensure_member_active(req, caller_user_id=caller_user_id)  # type: ignore[arg-type]

    got = await store.get(tenant_id=tenant, member_id=member.id)
    assert got is not None
    assert got.status == "active"
    assert got.subject_id == caller_user_id


@pytest.mark.asyncio
async def test_idempotent_second_run_noop() -> None:
    store = InMemoryTenantMemberStore()
    tenant = uuid4()
    kc_id = str(uuid4())
    member = await store.create(
        tenant_id=tenant, email="e@co.com", role="operator", invited_by="admin"
    )
    await store.set_keycloak_user_id(member_id=member.id, tenant_id=tenant, keycloak_user_id=kc_id)
    req = _request(subject_id=kc_id, member_repo=store)
    first_user = uuid4()
    await ensure_member_active(req, caller_user_id=first_user)  # type: ignore[arg-type]
    # Second run must not re-activate / overwrite subject_id.
    await ensure_member_active(req, caller_user_id=uuid4())  # type: ignore[arg-type]
    got = await store.get(tenant_id=tenant, member_id=member.id)
    assert got is not None and got.subject_id == first_user


@pytest.mark.asyncio
async def test_machine_principal_skipped() -> None:
    store = InMemoryTenantMemberStore()
    req = _request(subject_id=str(uuid4()), member_repo=store)
    # caller_user_id None → machine principal → no-op (and no crash).
    await ensure_member_active(req, caller_user_id=None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_no_roster_row_skipped() -> None:
    # A user with no tenant_member row (e.g. bootstrap admin) is a clean no-op.
    store = InMemoryTenantMemberStore()
    req = _request(subject_id=str(uuid4()), member_repo=store)
    await ensure_member_active(req, caller_user_id=uuid4())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_missing_repo_skipped() -> None:
    # No member repo wired (lightweight config) → no-op.
    req = _request(subject_id=str(uuid4()), member_repo=None)
    await ensure_member_active(req, caller_user_id=uuid4())  # type: ignore[arg-type]
