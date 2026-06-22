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
    """Lifecycle states — STREAM-F-DESIGN § 2.2 + HX-6 warm pool.

    ``READY`` (Stream HX-6) is a pre-launched pool container: alive and
    runner-ready but bound to no tenant / user; an ephemeral acquire
    claims it (READY → IN_USE) instead of paying a cold ``docker run``.
    """

    CREATING = "CREATING"
    READY = "READY"
    IN_USE = "IN_USE"
    DESTROYED = "DESTROYED"
    FAILED = "FAILED"


#: ``destroy`` reasons. ``RELEASE`` is the routine caller-driven teardown;
#: the rest are forced and emit a ``sandbox:force_destroy`` audit.
DESTROY_REASON_RELEASE = "release"
DESTROY_REASON_IDLE_TIMEOUT = "idle_timeout"
DESTROY_REASON_CANCELLED = "cancelled"


def container_name(sandbox_id: UUID) -> str:
    """The deterministic ``--name`` for a sandbox's container.

    Lives here (not ``supervisor.py``) so the HX-6 pool replenisher can
    name its pre-launched containers without importing the supervisor.
    """
    return f"helix-sb-{sandbox_id}"


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
    #: Owning user + their persistent workspace (Stream J.15). Both
    #: ``None`` for the ephemeral-tmpfs path — a sandbox acquired
    #: without a user scope.
    user_id: UUID | None = None
    workspace_id: UUID | None = None
    acquired_at: datetime | None = None
    #: Time of the last ``exec`` (J.15 warm sessions) — drives idle reaping.
    last_used_at: datetime | None = None
    released_at: datetime | None = None
    destroyed_at: datetime | None = None
    destroy_reason: str | None = None
    #: sandbox-egress §3.3 — the agent's egress policy + identity. Drives the
    #: ``HTTPS_PROXY`` env + per-sandbox token injection; ``None``/``"none"`` →
    #: no egress (proxy-only/isolated). Kept on the record so reuse can match.
    egress_policy: str | None = None
    agent_name: str | None = None
    agent_version: str | None = None
    #: sandbox-egress Phase 2 — per-agent host allowlist embedded in the token.
    egress_allowlist: tuple[str, ...] = ()

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


class WorkspaceFileNotFoundError(SupervisorError):
    """A workspace file could not be read — missing or unreadable (J.9)."""


class WorkspaceFileTooLargeError(SupervisorError):
    """A workspace file exceeds the supervisor's download size cap (J.9)."""


class InvalidSeedFilesError(SupervisorError):
    """The acquire's ``seed_files`` are malformed (bad base64, unsafe path, or
    over the count/total-bytes cap). skill-runtime §5.1 — a deterministic client
    error → HTTP 400."""


class WorkspaceQuotaExceededError(SupervisorError):
    """The user's workspace is at its ``size_limit_bytes`` quota (J.15-补强-1).

    Raised by :class:`QuotaEnforcer.check` on acquire when the last-
    measured volume size has reached the per-workspace ceiling. The
    control-plane translates this into HTTP 429, matching B.2 / sandbox
    rate-limit semantics.
    """

    def __init__(self, workspace_id: UUID, size_bytes: int, size_limit_bytes: int) -> None:
        super().__init__(
            f"workspace {workspace_id} at quota: {size_bytes} >= {size_limit_bytes} bytes"
        )
        self.workspace_id = workspace_id
        self.size_bytes = size_bytes
        self.size_limit_bytes = size_limit_bytes


class WorkspaceDeletedError(SupervisorError):
    """The user's workspace is soft-deleted; acquire is rejected (Mini-ADR J-36).

    Raised by :class:`QuotaEnforcer.check` when ``user_workspace.deleted_at``
    is set. The control-plane translates this into HTTP 410 Gone — the
    resource existed but is intentionally unavailable; recovery is a
    separate operator action (推 M1).
    """

    def __init__(self, workspace_id: UUID) -> None:
        super().__init__(f"workspace {workspace_id} is soft-deleted")
        self.workspace_id = workspace_id
