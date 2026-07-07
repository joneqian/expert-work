"""``BillingRollupSettings`` — env-driven knobs for the cost-rollup job.

Defaults aimed at local docker-compose. Connect direct to Postgres (not via
PgBouncer) so the per-tenant ``SET LOCAL app.tenant_id`` GUC survives across the
read + write within a sweep.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _current_month_first() -> date:
    """First-of-month for the current UTC month."""
    today = datetime.now(tz=UTC).date()
    return today.replace(day=1)


class BillingRollupSettings(BaseSettings):
    """Resolved runtime settings."""

    model_config = SettingsConfigDict(
        env_prefix="EXPERT_WORK_BILLING_ROLLUP_",
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = "billing_rollup_job"
    log_level: str = "INFO"

    # ------------------------------------------------------------------ db
    db_dsn: str = "postgresql+asyncpg://expert_work:expert_work_dev@localhost:5432/expert_work_dev"
    db_echo: bool = False

    # --------------------------------------------------------------- target
    # The month to roll up, as ``YYYY-MM`` (or a full ``YYYY-MM-DD``, normalized
    # to its first-of-month). Default = the current UTC month.
    target_month: date = Field(default_factory=_current_month_first)

    # How many tenants to page per ``list_all`` call when iterating tenants.
    tenant_page_size: int = Field(default=100, gt=0, le=1000)

    @field_validator("target_month", mode="before")
    @classmethod
    def _parse_target_month(cls, value: object) -> object:
        """Accept ``YYYY-MM`` (and full dates), normalize to first-of-month."""
        if isinstance(value, str) and len(value) == 7 and value[4] == "-":
            year, month = value.split("-")
            return date(int(year), int(month), 1)
        return value

    @field_validator("target_month", mode="after")
    @classmethod
    def _normalize_first_of_month(cls, value: date) -> date:
        return value.replace(day=1)
