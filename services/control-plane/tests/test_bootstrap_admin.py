"""Tests for the first-system-admin bootstrap — Stream P (Mini-ADR P-6)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from control_plane.bootstrap_admin import bootstrap_system_admin
from helix_agent.persistence.auth import InMemoryRoleBindingStore
from helix_agent.protocol import Role


@pytest.mark.asyncio
async def test_bootstrap_creates_platform_system_admin() -> None:
    store = InMemoryRoleBindingStore()
    subject = uuid4()

    result = await bootstrap_system_admin(store, subject_id=subject)

    assert result.created is True
    assert result.binding.role is Role.SYSTEM_ADMIN
    assert result.binding.platform_scope is True
    assert result.binding.tenant_id is None
    assert result.binding.subject_id == subject
    # The binding is now discoverable the same way AuthMiddleware looks it up.
    found = await store.get_platform_admin_for_subject(subject_type="user", subject_id=subject)
    assert found is not None


@pytest.mark.asyncio
async def test_bootstrap_is_idempotent_per_subject() -> None:
    store = InMemoryRoleBindingStore()
    subject = uuid4()

    first = await bootstrap_system_admin(store, subject_id=subject)
    second = await bootstrap_system_admin(store, subject_id=subject)

    assert first.created is True
    assert second.created is False
    assert second.binding.id == first.binding.id
    # Re-running did not write a duplicate row.
    assert len(await store.list_platform_scope()) == 1


@pytest.mark.asyncio
async def test_bootstrap_grants_distinct_subjects_separately() -> None:
    store = InMemoryRoleBindingStore()
    alice, bob = uuid4(), uuid4()

    await bootstrap_system_admin(store, subject_id=alice)
    bob_result = await bootstrap_system_admin(store, subject_id=bob)

    assert bob_result.created is True
    assert len(await store.list_platform_scope()) == 2
