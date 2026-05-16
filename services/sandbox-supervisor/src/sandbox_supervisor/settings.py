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

    # -------------------------------------------------------------- quota
    #: Per-tenant sandbox cap applied when the tenant has no
    #: ``tenant_quota`` row for the ``sandboxes`` dimension. Sandboxes are
    #: an expensive resource, so M0 defaults to a bounded cap rather than
    #: "unlimited" (STREAM-F-DESIGN § 1.1 F.1).
    default_max_sandboxes: int = Field(default=10, gt=0, le=1000)

    # -------------------------------------------------------------- reaper
    reaper_interval_s: float = Field(default=10.0, gt=0, le=300)
    #: Grace added to a sandbox's own ``timeout_s`` before the reaper
    #: treats an ``IN_USE`` row as an orphan (STREAM-F-DESIGN § 2.7).
    reaper_grace_s: int = Field(default=30, ge=0, le=600)
