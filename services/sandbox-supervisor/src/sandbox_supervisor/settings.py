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
    #: Stream OFFICE-1a — image for the "office" variant (built from
    #: infra/sandbox-image-office: slim + office libs + CJK fonts). Selected
    #: per-acquire via ``AcquireRequest.image_variant == "office"``.
    sandbox_image_office: str = "helix-sandbox-office:dev"
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

    # ------------------------------------------------------- workspace J.15-补强-1
    #: Per-workspace volume size ceiling (Mini-ADR J-29 第 1 项). Mirrored
    #: into ``user_workspace.size_limit_bytes`` at row creation; supervisor
    #: rejects ``acquire()`` when ``size_bytes >= size_limit_bytes``.
    #: Default 10 GiB matches migration 0026's server_default.
    default_workspace_size_limit_mb: int = Field(default=10 * 1024, gt=0, le=1024 * 1024)

    # ------------------------------------------------------ workspace J.15-补强-2
    #: When set, the supervisor periodically archives soft-deleted
    #: workspaces (Mini-ADR J-36 lifecycle 第 2 → 第 3 档) and writes a
    #: daily snapshot of each active workspace (Mini-ADR J-29 第 2 项).
    workspace_lifecycle_enabled: bool = True
    #: ObjectStore key prefix for J-36 archives. Layout:
    #: ``{prefix}/{tenant_id}/{user_id}/{volume_name}.tar.gz``.
    workspace_archive_prefix: str = "volume-archive"
    #: ObjectStore key prefix for J-29 第 2 项 daily backups. Layout:
    #: ``{prefix}/{tenant_id}/{user_id}/{YYYY-MM-DD}/{volume_name}.tar.gz``.
    workspace_backup_prefix: str = "volume-backups"
    #: Days a daily backup snapshot is retained before retention-cleanup
    #: prunes it. The archive (J-36) is kept until 90-day hard-delete
    #: (推 M1 per Mini-ADR J-36); this knob is the rolling window only.
    workspace_backup_retention_days: int = Field(default=7, gt=0, le=365)
    #: Local hour-of-day (0-23) the daily backup sweep runs. Default 03:00
    #: lands in off-peak. Set to ``-1`` to disable the daily backup loop
    #: (archive-on-soft-delete still runs every reaper tick).
    workspace_backup_hour: int = Field(default=3, ge=-1, le=23)
    #: Hard cap on the in-memory tar.gz buffer per volume archive (see
    #: :meth:`DockerClient.archive_volume`). 1.5 GiB matches the
    #: practical single-shot ObjectStore.put ceiling — multipart is M1.
    workspace_archive_max_inflight_bytes: int = Field(default=1536 * 1024 * 1024, gt=0)

    # -------------------------------------------------------- object store
    #: Object-store backend for the J.15 archive + backup pipelines.
    #: ``memory`` is the dev / CI default; ``s3-compatible`` plugs into
    #: MinIO / Aliyun OSS for prod (ADR-0004).
    object_store_backend: Literal["memory", "s3-compatible"] = "memory"
    object_store_endpoint_url: str = ""
    object_store_region: str = "cn-hangzhou"
    object_store_bucket: str = "helix-agent-volume-backups"
    object_store_access_key: str = ""
    object_store_secret_key: str = ""
    object_store_use_path_style: bool = True
