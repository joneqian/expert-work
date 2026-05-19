"""Pydantic models for the supervisor's HTTP API — STREAM-F-DESIGN § 4.1.

The M0 ``AcquireRequest`` is a subset of subsystems/14 § 3.3: no
``isolation_level`` branch and no ``purpose`` — M0 sandboxes are always
``shared``.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AcquireRequest(BaseModel):
    """Request body for ``POST /v1/sandboxes:acquire``."""

    model_config = ConfigDict(frozen=True)

    tenant_id: UUID
    thread_id: str
    #: Owning user (Stream J.15). When set, the sandbox mounts that
    #: user's persistent workspace volume at ``/workspace``; omitted →
    #: an ephemeral tmpfs workspace (the pre-J.15 behaviour).
    user_id: UUID | None = None
    #: Optional per-call resource overrides; omitted → the service defaults.
    cpu: float | None = Field(default=None, gt=0, le=16)
    memory_mb: int | None = Field(default=None, gt=0, le=65536)
    pids_limit: int | None = Field(default=None, gt=0, le=4096)
    timeout_s: int | None = Field(default=None, gt=0, le=300)


class AcquireResponse(BaseModel):
    """Response body for a successful acquire."""

    model_config = ConfigDict(frozen=True)

    sandbox_id: UUID
    container_id: str
    #: M0 has no warm pool, so every acquire is a cold start.
    cold_start: bool = True
    acquired_at: datetime


class ExecRequest(BaseModel):
    """Request body for ``POST /v1/sandboxes/{id}:exec``."""

    model_config = ConfigDict(frozen=True)

    code: str
    #: Per-call execution timeout; omitted → the sandbox's own default.
    timeout_s: int | None = Field(default=None, gt=0, le=300)


class ExecResponse(BaseModel):
    """Response body for a code execution — the runner's captured outcome."""

    model_config = ConfigDict(frozen=True)

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool


class DestroyRequest(BaseModel):
    """Request body for ``POST /v1/sandboxes/{id}:destroy``."""

    model_config = ConfigDict(frozen=True)

    reason: str = "destroy"


class HealthResponse(BaseModel):
    """Response body for ``GET /v1/health``."""

    model_config = ConfigDict(frozen=True)

    status: str
    docker_ok: bool
