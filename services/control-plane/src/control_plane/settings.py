"""Control Plane settings — Stream B.1.

Pydantic v2 ``BaseSettings`` with ``HELIX_AGENT_`` env prefix. The default
values point at the local ``infra/docker-compose.yml`` PgBouncer so a
fresh checkout boots without env tweaks.

ADR B-5 (STREAM-B-DESIGN.md § 3): ``auth_mode`` is the gate between
header-trust dev mode and the prod-mode startup guard that refuses to
boot until C.1 OIDC middleware lands.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Default tenant UUID assigned to header-less dev requests. ``00...00`` is
#: deliberately the nil UUID so it sticks out in audit_log dumps.
DEFAULT_DEV_TENANT_ID: UUID = UUID("00000000-0000-0000-0000-000000000000")

#: Default actor id for dev requests with no ``X-Helix-Actor`` header.
DEFAULT_DEV_ACTOR_ID: str = "anonymous"


class Settings(BaseSettings):
    """Resolved runtime settings; cheap to construct in tests."""

    model_config = SettingsConfigDict(
        env_prefix="HELIX_AGENT_",
        case_sensitive=False,
        # Service file overrides come from environments/<env>.yaml; this
        # only honours real env vars. Keep it strict so typos surface.
        extra="ignore",
    )

    # ------------------------------------------------------------------ identity
    service_name: str = "control_plane"
    env: Literal["dev", "staging", "prod"] = "dev"
    log_level: str = "INFO"

    # ------------------------------------------------------------------ auth (B-5)
    auth_mode: Literal["dev", "prod"] = "dev"
    default_dev_tenant_id: UUID = DEFAULT_DEV_TENANT_ID
    default_dev_actor_id: str = DEFAULT_DEV_ACTOR_ID

    # ------------------------------------------------------------------ database
    db_dsn: str = "postgresql+asyncpg://helix_agent:helix_agent_dev@localhost:6432/helix_agent_dev"
    db_pgbouncer_mode: bool = True
    db_echo: bool = False

    # ------------------------------------------------------------------ otel
    otlp_traces_endpoint: str | None = None

    # ------------------------------------------------------------------ runtime guards
    # ADR B-1: in-process rate-limiter assumes a single replica. Override
    # to ``False`` only after C.6 ships the Redis backend.
    single_instance: bool = True

    health_check_timeout_s: float = Field(default=5.0, gt=0)
