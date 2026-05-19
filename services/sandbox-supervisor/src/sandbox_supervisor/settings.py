"""``SandboxSupervisorSettings`` — env-driven knobs for the F.1 service.

Defaults aim at the local docker-compose fixture. The DB DSN points at
Postgres directly (port 5432), not PgBouncer — the supervisor issues
plain inserts / updates with no need for the bouncer pool.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SandboxSupervisorSettings(BaseSettings):
    """Resolved runtime settings; cheap to construct in tests."""

    model_config = SettingsConfigDict(
        env_prefix="HELIX_SANDBOX_",
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = "sandbox_supervisor"
    log_level: str = "INFO"

    # ------------------------------------------------------------------ db
    db_dsn: str = "postgresql+asyncpg://helix_agent:helix_agent_dev@localhost:5432/helix_agent_dev"
    db_echo: bool = False

    # -------------------------------------------------------------- sandbox
    #: Image the `exec_python` sandbox runs (built from infra/sandbox-image).
    sandbox_image: str = "helix-sandbox:dev"
    #: OCI runtime — `runc` for dev / macOS, `runsc` (gVisor) for Linux prod.
    oci_runtime: Literal["runc", "runsc"] = "runc"
    #: Host identifier recorded on each sandbox_instance row. M0 single-node.
    node_name: str = "local"

    # ----------------------------------------------------------- resources
    default_cpu: float = Field(default=1.0, gt=0, le=16)
    default_memory_mb: int = Field(default=512, gt=0, le=65536)
    default_pids_limit: int = Field(default=128, gt=0, le=4096)
    default_timeout_s: int = Field(default=30, gt=0, le=300)
    #: How long ``acquire`` waits for the runner's readiness line before
    #: treating the launch as failed.
    runner_ready_timeout_s: float = Field(default=15.0, gt=0, le=120)

    # -------------------------------------------------------------- quota
    #: Per-tenant sandbox cap applied when the tenant has no
    #: ``tenant_quota`` row for the ``sandboxes`` dimension. With J.15
    #: warm sessions a sandbox stays ``IN_USE`` across a user's whole
    #: active window, so this cap is effectively the number of
    #: concurrently-active users a tenant supports (STREAM-J-DESIGN § 9).
    default_max_sandboxes: int = Field(default=50, gt=0, le=1000)

    # -------------------------------------------------------------- reaper
    reaper_interval_s: float = Field(default=10.0, gt=0, le=300)
    #: Idle TTL for a warm per-user sandbox session (Stream J.15). The
    #: reaper destroys a session whose last ``exec`` (``last_used_at``)
    #: is older than this — freeing compute; the persistent volume is
    #: kept, so the next message cold-starts a fresh container on it.
    session_idle_ttl_s: int = Field(default=15 * 60, gt=0, le=24 * 60 * 60)
