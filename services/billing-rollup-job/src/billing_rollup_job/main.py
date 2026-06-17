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
from helix_agent.persistence.platform_billing_config import (
    SqlPlatformBillingConfigStore,
)
from helix_agent.persistence.rls import build_rls_sessionmaker
from helix_agent.persistence.token_usage_store import DbTokenUsageStore

logger = logging.getLogger(__name__)


def rollup_is_enabled(config: object | None) -> bool:
    """Whether to run, given the platform billing config row (or None).

    Stream 12.4 — an absent row means "default" → enabled; otherwise honour the
    operator's ``rollup_enabled`` toggle. Pure so the gate is unit-testable.
    """
    if config is None:
        return True
    return bool(getattr(config, "rollup_enabled", True))


async def _amain() -> None:
    settings = BillingRollupSettings()
    logging.basicConfig(level=settings.log_level)

    engine = create_async_engine_from_config(
        DatabaseConfig(dsn=settings.db_dsn, echo_sql=settings.db_echo)
    )
    # RLS-wrapped: token_usage reads + ledger writes ride the per-tenant GUC the
    # job sets via _tenant_scope; rate_card reads ride bypass via _bypass_rls.
    session_factory = build_rls_sessionmaker(create_async_session_factory(engine))

    # Stream 12.4 — the platform billing toggle (admin-ui controlled). When the
    # operator has disabled rollup, skip the run without touching the k8s cron.
    # An absent config row means "default" → enabled. ``platform_billing_config``
    # is a tenant-less no-RLS table, so no bypass scope is needed.
    billing_config = await SqlPlatformBillingConfigStore(session_factory).get()
    if not rollup_is_enabled(billing_config):
        logger.info(
            "billing_rollup_job.skipped month=%s reason=rollup_disabled",
            settings.target_month.isoformat(),
        )
        await engine.dispose()
        return

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
