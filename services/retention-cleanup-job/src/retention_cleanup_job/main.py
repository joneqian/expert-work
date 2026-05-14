"""Entrypoint: ``uv run python -m retention_cleanup_job``.

Runs one cleanup sweep and exits — cron / Kubernetes CronJob handles
scheduling. The job is idempotent; running it twice in a row is fine
(the second pass usually deletes nothing).
"""

from __future__ import annotations

import asyncio
import logging

from helix_agent.persistence import (
    DatabaseConfig,
    create_async_engine_from_config,
    create_async_session_factory,
)
from retention_cleanup_job.job import RetentionCleanupJob
from retention_cleanup_job.settings import RetentionCleanupSettings

logger = logging.getLogger(__name__)


async def _amain() -> None:
    settings = RetentionCleanupSettings()
    logging.basicConfig(level=settings.log_level)

    engine = create_async_engine_from_config(DatabaseConfig(dsn=settings.db_dsn))
    session_factory = create_async_session_factory(engine)

    job = RetentionCleanupJob(
        db_session_factory=session_factory,
        batch_size=settings.batch_size,
    )
    logger.info("retention_cleanup_job.start batch=%d", settings.batch_size)
    report = await job.run_once()
    logger.info(
        "retention_cleanup_job.done audit=%d audit_skipped_unacked=%d "
        "event=%d jwt=%d duration=%.2fs",
        report.audit_deleted,
        report.audit_skipped_unacked,
        report.event_deleted,
        report.jwt_blacklist_deleted,
        report.duration_seconds,
    )
    if report.audit_skipped_unacked > 0:
        logger.warning(
            "retention.skipped_unacked count=%d — D.1c backup worker may be lagging",
            report.audit_skipped_unacked,
        )

    await engine.dispose()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
