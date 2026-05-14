"""``RetentionCleanupSettings`` — env-driven knobs for the cleanup job.

Defaults aimed at local docker-compose; connect direct to Postgres
(not via PgBouncer) so transactional ``SET LOCAL ROLE`` survives.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RetentionCleanupSettings(BaseSettings):
    """Resolved runtime settings."""

    model_config = SettingsConfigDict(
        env_prefix="HELIX_RETENTION_",
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = "retention_cleanup_job"
    log_level: str = "INFO"

    # ------------------------------------------------------------------ db
    db_dsn: str = "postgresql+asyncpg://helix_agent:helix_agent_dev@localhost:5432/helix_agent_dev"
    db_echo: bool = False

    # ------------------------------------------------------------------ tuning
    # Per-table per-sweep batch ceiling. Bounded so a single sweep
    # doesn't take an autovacuum-blocking lock for too long; with
    # ``WHERE`` predicates each DELETE is plan-time pruned.
    batch_size: int = Field(default=10000, gt=0, le=1000000)
