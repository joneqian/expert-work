"""Unit tests for :class:`RetentionCleanupJob` construction + CleanupReport."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from helix_agent.persistence import InMemoryArtifactStore, InMemoryImageUploadStore
from helix_agent.runtime.storage import InMemoryObjectStore
from retention_cleanup_job.job import CleanupReport, RetentionCleanupJob


def test_cleanup_report_default_is_all_zero() -> None:
    report = CleanupReport()
    assert report.audit_deleted == 0
    assert report.audit_skipped_unacked == 0
    assert report.event_deleted == 0
    assert report.jwt_blacklist_deleted == 0
    assert report.image_uploads_hard_deleted == 0
    assert report.image_object_keys_removed == 0
    assert report.image_object_keys_failed == 0
    assert report.artifacts_soft_deleted == 0
    assert report.artifacts_hard_deleted == 0
    assert report.duration_seconds == 0.0
    assert report.audit_deleted_by_tenant == {}


def test_job_rejects_non_positive_batch_size() -> None:
    """``batch_size <= 0`` is a programmer error — surface early."""
    with pytest.raises(ValueError, match="batch_size"):
        RetentionCleanupJob(
            db_session_factory=lambda: None,  # type: ignore[arg-type]
            batch_size=0,
        )


def test_job_rejects_non_positive_image_retention_days() -> None:
    with pytest.raises(ValueError, match="image_retention_days"):
        RetentionCleanupJob(
            db_session_factory=lambda: None,  # type: ignore[arg-type]
            image_retention_days=0,
        )


def test_job_rejects_non_positive_artifact_retention_days() -> None:
    with pytest.raises(ValueError, match="artifact_retention_days"):
        RetentionCleanupJob(
            db_session_factory=lambda: None,  # type: ignore[arg-type]
            artifact_retention_days=0,
        )


def test_job_rejects_non_positive_artifact_hard_delete_grace_days() -> None:
    with pytest.raises(ValueError, match="artifact_hard_delete_grace_days"):
        RetentionCleanupJob(
            db_session_factory=lambda: None,  # type: ignore[arg-type]
            artifact_hard_delete_grace_days=0,
        )


# ---------------------------------------------------------------------------
# Mini-ADR J-32 (J.6.补强-3b) — image retention sweep
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_expired_images_purges_old_rows_and_object_keys() -> None:
    """Rows older than ``image_retention_days`` get their object key
    removed + are hard-deleted from the registry."""
    images = InMemoryImageUploadStore()
    object_store = InMemoryObjectStore()
    tenant = uuid4()

    # An old row (created_at well past the cutoff).
    old_id = uuid4()
    old_key = "tenants/x/uploads/old.png"
    await object_store.put(old_key, b"OLD", content_type="image/png")
    await images.insert(
        image_id=old_id,
        tenant_id=tenant,
        thread_id=uuid4(),
        user_id=None,
        object_key=old_key,
        size_bytes=3,
        mime_type="image/png",
        sha256="x",
    )
    # Backdate the row to before the retention horizon.
    images._rows[old_id] = images._rows[old_id].model_copy(
        update={"created_at": datetime.now(UTC) - timedelta(days=200)},
    )

    # A fresh row (must stay).
    fresh_id = uuid4()
    fresh_key = "tenants/x/uploads/fresh.png"
    await object_store.put(fresh_key, b"FRESH", content_type="image/png")
    await images.insert(
        image_id=fresh_id,
        tenant_id=tenant,
        thread_id=uuid4(),
        user_id=None,
        object_key=fresh_key,
        size_bytes=5,
        mime_type="image/png",
        sha256="y",
    )

    job = RetentionCleanupJob(
        db_session_factory=lambda: None,  # type: ignore[arg-type]
        image_upload_store=images,
        object_store=object_store,
        image_retention_days=90,
    )

    rows, keys_ok, keys_failed = await job._delete_expired_images()

    assert rows == 1
    assert keys_ok == 1
    assert keys_failed == 0
    from helix_agent.runtime.storage.base import ObjectNotFoundError

    # Old row + key gone.
    assert await images.get(image_id=old_id, tenant_id=tenant) is None
    with pytest.raises(ObjectNotFoundError):
        await object_store.get(old_key)
    # Fresh row + key remain.
    assert await images.get(image_id=fresh_id, tenant_id=tenant) is not None
    assert await object_store.get(fresh_key) == b"FRESH"


@pytest.mark.asyncio
async def test_delete_expired_images_continues_on_object_store_failure() -> None:
    """A failed object-store delete is tallied + logged — the row is
    still hard-deleted (orphaned key < stuck row whose bytes never go away)."""

    images = InMemoryImageUploadStore()
    tenant = uuid4()
    old_id = uuid4()
    await images.insert(
        image_id=old_id,
        tenant_id=tenant,
        thread_id=uuid4(),
        user_id=None,
        object_key="tenants/x/uploads/old.png",
        size_bytes=3,
        mime_type="image/png",
        sha256="x",
    )
    images._rows[old_id] = images._rows[old_id].model_copy(
        update={"created_at": datetime.now(UTC) - timedelta(days=200)},
    )

    class _FailingStore:
        async def delete(self, key: str) -> None:
            raise RuntimeError("boom")

        async def put(self, *args: object, **kwargs: object) -> None:
            return None

        async def get(self, key: str) -> bytes | None:
            return None

        async def list_prefix(self, prefix: str) -> list[str]:
            return []

    job = RetentionCleanupJob(
        db_session_factory=lambda: None,  # type: ignore[arg-type]
        image_upload_store=images,
        object_store=_FailingStore(),  # type: ignore[arg-type]
        image_retention_days=90,
    )

    rows, keys_ok, keys_failed = await job._delete_expired_images()
    assert rows == 1
    assert keys_ok == 0
    assert keys_failed == 1
    assert await images.get(image_id=old_id, tenant_id=tenant) is None


@pytest.mark.asyncio
async def test_delete_expired_images_noop_without_stores() -> None:
    """Job constructed without image stores skips the image pass cleanly."""
    job = RetentionCleanupJob(
        db_session_factory=lambda: None,  # type: ignore[arg-type]
    )
    rows, keys_ok, keys_failed = await job._delete_expired_images()
    assert (rows, keys_ok, keys_failed) == (0, 0, 0)


# ---------------------------------------------------------------------------
# Mini-ADR J-25 (J.9-step1) — artifact lifecycle sweep
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_artifacts_noop_without_store() -> None:
    """Job constructed without ArtifactStore skips the artifact pass cleanly."""
    job = RetentionCleanupJob(
        db_session_factory=lambda: None,  # type: ignore[arg-type]
    )
    soft, hard = await job._sweep_artifacts()
    assert (soft, hard) == (0, 0)


@pytest.mark.asyncio
async def test_sweep_artifacts_soft_deletes_stale_active_rows() -> None:
    """Active rows past ``artifact_retention_days`` get soft-deleted."""
    artifacts = InMemoryArtifactStore()
    tenant, user = uuid4(), uuid4()
    await artifacts.save_version(
        tenant_id=tenant,
        user_id=user,
        name="stale.md",
        kind="document",
        path_in_workspace="stale.md",
        created_in_thread="t-1",
    )
    # Backdate ``updated_at`` to before the retention horizon.
    stale = (await artifacts.list_for_user(tenant_id=tenant, user_id=user))[0]
    artifacts._artifacts[stale.id] = stale.model_copy(  # type: ignore[attr-defined]
        update={"updated_at": datetime.now(UTC) - timedelta(days=120)}
    )
    # And a fresh row that must survive.
    await artifacts.save_version(
        tenant_id=tenant,
        user_id=user,
        name="fresh.md",
        kind="document",
        path_in_workspace="fresh.md",
        created_in_thread="t-2",
    )

    job = RetentionCleanupJob(
        db_session_factory=lambda: None,  # type: ignore[arg-type]
        artifact_store=artifacts,
        artifact_retention_days=90,
    )
    soft, hard = await job._sweep_artifacts()
    assert (soft, hard) == (1, 0)
    # Only ``fresh.md`` remains in the default (non-deleted) listing.
    active = await artifacts.list_for_user(tenant_id=tenant, user_id=user)
    assert [a.name for a in active] == ["fresh.md"]


@pytest.mark.asyncio
async def test_sweep_artifacts_hard_deletes_expired_soft_deleted_rows() -> None:
    """Soft-deleted rows past the hard-delete grace are removed entirely."""
    artifacts = InMemoryArtifactStore()
    tenant, user = uuid4(), uuid4()
    await artifacts.save_version(
        tenant_id=tenant,
        user_id=user,
        name="old.md",
        kind="document",
        path_in_workspace="old.md",
        created_in_thread="t-1",
    )
    # Soft-delete with a backdated timestamp past the grace window.
    long_ago = datetime.now(UTC) - timedelta(days=120)
    await artifacts.soft_delete(tenant_id=tenant, user_id=user, name="old.md", now=long_ago)

    job = RetentionCleanupJob(
        db_session_factory=lambda: None,  # type: ignore[arg-type]
        artifact_store=artifacts,
        artifact_hard_delete_grace_days=60,
    )
    soft, hard = await job._sweep_artifacts()
    # Active sweep finds nothing; hard sweep clears the soft-deleted row.
    assert (soft, hard) == (0, 1)
    # No row should remain even with include_deleted=True.
    assert await artifacts.list_for_user(tenant_id=tenant, user_id=user, include_deleted=True) == []


@pytest.mark.asyncio
async def test_sweep_artifacts_skips_recent_soft_deleted_rows() -> None:
    """Recent soft-deletes (within the grace window) survive the sweep."""
    artifacts = InMemoryArtifactStore()
    tenant, user = uuid4(), uuid4()
    await artifacts.save_version(
        tenant_id=tenant,
        user_id=user,
        name="recent.md",
        kind="document",
        path_in_workspace="recent.md",
        created_in_thread="t-1",
    )
    # Soft-delete only 10 days ago — must stay.
    recent = datetime.now(UTC) - timedelta(days=10)
    await artifacts.soft_delete(tenant_id=tenant, user_id=user, name="recent.md", now=recent)

    job = RetentionCleanupJob(
        db_session_factory=lambda: None,  # type: ignore[arg-type]
        artifact_store=artifacts,
        artifact_hard_delete_grace_days=60,
    )
    soft, hard = await job._sweep_artifacts()
    assert (soft, hard) == (0, 0)
    # Row still in include_deleted listing.
    deleted = await artifacts.list_for_user(tenant_id=tenant, user_id=user, include_deleted=True)
    assert len(deleted) == 1
