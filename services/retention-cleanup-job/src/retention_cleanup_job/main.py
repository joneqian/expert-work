"""Entrypoint: ``uv run python -m retention_cleanup_job``.

Runs one cleanup sweep and exits — cron / Kubernetes CronJob handles
scheduling. The job is idempotent; running it twice in a row is fine
(the second pass usually deletes nothing).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from helix_agent.persistence import (
    DatabaseConfig,
    SqlArtifactStore,
    SqlImageUploadStore,
    create_async_engine_from_config,
    create_async_session_factory,
)
from helix_agent.runtime.storage import make_object_store
from helix_agent.runtime.storage.factory import S3CompatibleConfig
from retention_cleanup_job.job import RetentionCleanupJob
from retention_cleanup_job.settings import RetentionCleanupSettings

logger = logging.getLogger(__name__)


async def _amain() -> None:
    settings = RetentionCleanupSettings()
    logging.basicConfig(level=settings.log_level)

    engine = create_async_engine_from_config(DatabaseConfig(dsn=settings.db_dsn))
    session_factory = create_async_session_factory(engine)

    async with contextlib.AsyncExitStack() as stack:
        # Mini-ADR J-32 (J.6.补强-3b) — the image pass needs both a
        # registry + the object store. ``memory`` backend skips the
        # pass (in-memory bytes vanish per process anyway).
        image_store: SqlImageUploadStore | None = None
        object_store = None
        if settings.object_store_backend == "s3-compatible":
            if (
                not settings.object_store_endpoint_url
                or not settings.object_store_access_key
                or not settings.object_store_secret_key
            ):
                msg = (
                    "object_store_backend=s3-compatible requires endpoint_url + "
                    "access_key + secret_key"
                )
                raise ValueError(msg)
            object_store = await stack.enter_async_context(
                make_object_store(
                    settings.object_store_backend,
                    S3CompatibleConfig(
                        endpoint_url=settings.object_store_endpoint_url,
                        region=settings.object_store_region,
                        bucket=settings.object_store_bucket,
                        access_key=settings.object_store_access_key,
                        secret_key=settings.object_store_secret_key,
                    ),
                )
            )
            image_store = SqlImageUploadStore(session_factory)

        # Mini-ADR J-25 (J.9-step1) — artifact lifecycle sweep wires the
        # SqlArtifactStore in unconditionally; the artifact pass is
        # metadata-only (no object store / supervisor calls), so it is
        # safe to enable even on deployments without J.6 image uploads.
        artifact_store = SqlArtifactStore(session_factory)

        job = RetentionCleanupJob(
            db_session_factory=session_factory,
            batch_size=settings.batch_size,
            image_upload_store=image_store,
            object_store=object_store,
            image_retention_days=settings.image_retention_days,
            artifact_store=artifact_store,
            artifact_retention_days=settings.artifact_retention_days,
            artifact_hard_delete_grace_days=settings.artifact_hard_delete_grace_days,
        )
        logger.info("retention_cleanup_job.start batch=%d", settings.batch_size)
        report = await job.run_once()
        logger.info(
            "retention_cleanup_job.done audit=%d audit_skipped_unacked=%d "
            "event=%d jwt=%d image_rows=%d image_keys_ok=%d image_keys_failed=%d "
            "artifact_soft=%d artifact_hard=%d duration=%.2fs",
            report.audit_deleted,
            report.audit_skipped_unacked,
            report.event_deleted,
            report.jwt_blacklist_deleted,
            report.image_uploads_hard_deleted,
            report.image_object_keys_removed,
            report.image_object_keys_failed,
            report.artifacts_soft_deleted,
            report.artifacts_hard_deleted,
            report.duration_seconds,
        )
        if report.audit_skipped_unacked > 0:
            logger.warning(
                "retention.skipped_unacked count=%d — D.1c backup worker may be lagging",
                report.audit_skipped_unacked,
            )
        if report.image_object_keys_failed > 0:
            logger.warning(
                "retention.image_object_keys_failed count=%d — object store unhealthy?",
                report.image_object_keys_failed,
            )

    await engine.dispose()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
