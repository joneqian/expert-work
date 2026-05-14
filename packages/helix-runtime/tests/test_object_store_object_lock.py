"""Unit tests for the D.1b Object Lock contract.

Covers behavior shared by every :class:`ObjectStore` backend
(``validate_lock_args`` + in-memory enforcement). The MinIO integration
test in ``test_minio_object_lock_integration.py`` covers the S3 path.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from helix_agent.runtime.storage import (
    InMemoryObjectStore,
    ObjectLockedError,
)

# ---------- validate_lock_args ----------


@pytest.mark.asyncio
async def test_put_rejects_retain_until_without_lock_mode() -> None:
    store = InMemoryObjectStore()
    with pytest.raises(ValueError, match="retain_until and lock_mode"):
        await store.put(
            "k",
            b"v",
            retain_until=datetime.now(tz=UTC) + timedelta(hours=1),
        )


@pytest.mark.asyncio
async def test_put_rejects_lock_mode_without_retain_until() -> None:
    store = InMemoryObjectStore()
    with pytest.raises(ValueError, match="retain_until and lock_mode"):
        await store.put("k", b"v", lock_mode="compliance")


# ---------- compliance: re-put blocked while retained ----------


@pytest.mark.asyncio
async def test_compliance_lock_blocks_overwrite_within_retention() -> None:
    store = InMemoryObjectStore()
    later = datetime.now(tz=UTC) + timedelta(days=7)
    await store.put("k", b"first", retain_until=later, lock_mode="compliance")
    with pytest.raises(ObjectLockedError):
        await store.put("k", b"second", retain_until=later, lock_mode="compliance")
    # First value preserved.
    assert await store.get("k") == b"first"


@pytest.mark.asyncio
async def test_compliance_lock_allows_overwrite_after_retention_elapsed() -> None:
    """An object whose retain_until has passed is overwritable again."""
    store = InMemoryObjectStore()
    past = datetime.now(tz=UTC) - timedelta(seconds=1)
    await store.put("k", b"first", retain_until=past, lock_mode="compliance")
    # Retention has elapsed, second put proceeds normally.
    later = datetime.now(tz=UTC) + timedelta(days=1)
    await store.put("k", b"second", retain_until=later, lock_mode="compliance")
    assert await store.get("k") == b"second"


# ---------- governance: re-put permitted by design ----------


@pytest.mark.asyncio
async def test_governance_lock_allows_overwrite_within_retention() -> None:
    """Governance mode is permissive — privileged callers may override.

    The in-memory store models this by not raising; the S3 backend
    relies on the role having ``s3:BypassGovernanceRetention``.
    """
    store = InMemoryObjectStore()
    later = datetime.now(tz=UTC) + timedelta(days=1)
    await store.put("k", b"v1", retain_until=later, lock_mode="governance")
    await store.put("k", b"v2", retain_until=later, lock_mode="governance")
    assert await store.get("k") == b"v2"


# ---------- no-lock path is unchanged ----------


@pytest.mark.asyncio
async def test_put_without_lock_args_overwrites_freely() -> None:
    """Existing callers passing no lock args see no behavior change."""
    store = InMemoryObjectStore()
    await store.put("k", b"v1")
    await store.put("k", b"v2")
    assert await store.get("k") == b"v2"


@pytest.mark.asyncio
async def test_unlocked_object_can_be_replaced_by_locked_put() -> None:
    """First put unlocked → second put locked is allowed (no prior retention).

    Locks the result going forward, but doesn't retroactively block
    that first replacement.
    """
    store = InMemoryObjectStore()
    await store.put("k", b"v1")
    later = datetime.now(tz=UTC) + timedelta(days=7)
    await store.put("k", b"v2", retain_until=later, lock_mode="compliance")
    assert await store.get("k") == b"v2"
    # Now the lock is in effect; a further compliance re-put is blocked.
    with pytest.raises(ObjectLockedError):
        await store.put("k", b"v3", retain_until=later, lock_mode="compliance")
