"""Internal domain types — sandbox state, the record shape, errors.

Kept separate from the Pydantic HTTP schemas (``schemas.py``): these
types cross the supervisor ↔ store boundary, the schemas cross the
HTTP boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class SandboxState(StrEnum):
    """Lifecycle states — STREAM-F-DESIGN § 2.2 (M0 cold-start subset).

    No ``READY`` pool state: M0 has no warm pool, so a sandbox goes
    straight from launch to ``IN_USE`` and is destroyed on release.
    """

    CREATING = "CREATING"
    IN_USE = "IN_USE"
    DESTROYED = "DESTROYED"
    FAILED = "FAILED"


#: ``destroy`` reasons. ``RELEASE`` is the routine caller-driven teardown;
#: the rest are forced and emit a ``sandbox:force_destroy`` audit.
DESTROY_REASON_RELEASE = "release"
DESTROY_REASON_IDLE_TIMEOUT = "idle_timeout"
DESTROY_REASON_CANCELLED = "cancelled"


@dataclass(frozen=True)
class SandboxRecord:
    """One sandbox's full state — the unit the :class:`SandboxStore` persists.

    Frozen: state transitions produce a new record via
    :meth:`with_state` rather than mutating in place.
    """

    id: UUID
    tenant_id: UUID
    image_ref: str
    node: str
    container_id: str | None
    state: SandboxState
    thread_id: str
    cpu_quota: float
    memory_mb: int
    pids_limit: int
    timeout_s: int
    created_at: datetime
    acquired_at: datetime | None = None
    released_at: datetime | None = None
    destroyed_at: datetime | None = None
    destroy_reason: str | None = None

    def with_state(self, state: SandboxState, **changes: object) -> SandboxRecord:
        """Return a copy in ``state`` with any extra field overrides applied."""
        from dataclasses import replace

        return replace(self, state=state, **changes)  # type: ignore[arg-type]


class SupervisorError(Exception):
    """Base class for supervisor-level failures."""


class QuotaExceededError(SupervisorError):
    """The tenant is already at its ``sandboxes`` quota — acquire is refused."""

    def __init__(self, tenant_id: UUID, limit: int) -> None:
        super().__init__(f"tenant {tenant_id} is at its sandbox quota (limit={limit})")
        self.tenant_id = tenant_id
        self.limit = limit


class SandboxNotFoundError(SupervisorError):
    """No sandbox is registered under the requested id."""

    def __init__(self, sandbox_id: UUID) -> None:
        super().__init__(f"sandbox not found: {sandbox_id}")
        self.sandbox_id = sandbox_id
