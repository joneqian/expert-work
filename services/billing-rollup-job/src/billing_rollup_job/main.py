"""Entrypoint: ``uv run python -m billing_rollup_job``.

Runs one cost rollup for the target month and exits — cron / Kubernetes
CronJob handles scheduling. The job is idempotent (upsert-overwrite), so running
it repeatedly for the same month recomputes rather than double-counts.
"""

from __future__ import annotations

import asyncio
import logging

from billing_rollup_job.job import BillingRollupJob
from billing_rollup_job.settings import BillingRollupSettings
from helix_agent.persistence import (
    DatabaseConfig,
    DbModelRateCardStore,
    DbTenantBillingLedgerStore,
    SqlTenantConfigStore,
    create_async_engine_from_config,
    create_async_session_factory,
)
from helix_agent.persistence.rls import build_rls_sessionmaker
from helix_agent.persistence.token_usage_store import DbTokenUsageStore

logger = logging.getLogger(__name__)


async def _amain() -> None:
    settings = BillingRollupSettings()
    logging.basicConfig(level=settings.log_level)

    engine = create_async_engine_from_config(
        DatabaseConfig(dsn=settings.db_dsn, echo_sql=settings.db_echo)
    )
    # RLS-wrapped: token_usage reads + ledger writes ride the per-tenant GUC the
    # job sets via _tenant_scope; rate_card reads ride bypass via _bypass_rls.
    session_factory = build_rls_sessionmaker(create_async_session_factory(engine))

    job = BillingRollupJob(
        tenant_config_store=SqlTenantConfigStore(session_factory),
        token_usage_store=DbTokenUsageStore(session_factory),
        rate_card_store=DbModelRateCardStore(session_factory),
        ledger_store=DbTenantBillingLedgerStore(session_factory),
        tenant_page_size=settings.tenant_page_size,
    )
    logger.info(
        "billing_rollup_job.start month=%s",
        settings.target_month.isoformat(),
    )
    report = await job.run_once(month=settings.target_month)
    logger.info(
        "billing_rollup_job.done month=%s tenants=%d rows_priced=%d "
        "rows_unpriced=%d buckets=%d unpriced_buckets=%d duration=%.2fs",
        settings.target_month.isoformat(),
        report.tenants_scanned,
        report.usage_rows_priced,
        report.usage_rows_unpriced,
        report.buckets_upserted,
        report.unpriced_buckets,
        report.duration_seconds,
    )
    if report.usage_rows_unpriced > 0:
        logger.warning(
            "billing_rollup.unpriced_rows count=%d — missing provider mapping or rate card",
            report.usage_rows_unpriced,
        )

    await engine.dispose()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
