"""Value types + errors crossing the proxy's internal boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

#: ``credential_proxy_audit.status`` values.
ProxyStatus = Literal["ok", "cached", "denied", "secret_miss"]

#: ``sandbox_egress_audit.verdict`` values.
EgressVerdict = Literal[
    "allowed",
    "blocked_ssrf",
    "blocked_allowlist",
    "blocked_denylist",
    "blocked_auth",
    "upstream_error",
]


@dataclass(frozen=True)
class ForwardRequest:
    """A parsed ``POST /forward`` request — what the proxy acts on."""

    tenant_id: UUID
    agent_name: str
    agent_version: str
    secret_ref: str
    upstream_url: str
    method: str
    headers: dict[str, str]
    body: bytes
    session_id: UUID | None = None
    sandbox_id: str | None = None


@dataclass(frozen=True)
class ForwardResult:
    """The upstream response the proxy relays back to the caller."""

    status: int
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True)
class ProxyAuditEntry:
    """One row for ``credential_proxy_audit`` — never carries a secret value."""

    tenant_id: UUID
    target_host: str
    status: ProxyStatus
    agent_name: str | None = None
    agent_version: str | None = None
    session_id: UUID | None = None
    sandbox_id: str | None = None
    secret_ref: str | None = None
    inject_kind: str | None = None
    error_msg: str | None = None
    duration_ms: int | None = None


@dataclass(frozen=True)
class EgressAuditEntry:
    """One row for ``sandbox_egress_audit`` — host + volume + verdict, never
    payload (HTTPS is tunnelled; the proxy never sees plaintext).

    ``tenant_id`` is ``None`` only for a pre-identity rejection (a missing /
    invalid / expired proxy token → ``blocked_auth``): there is no trustworthy
    tenant to attribute it to, so it is recorded as a platform-level anomaly
    visible in the cross-tenant audit view (audit-eval Phase 4)."""

    tenant_id: UUID | None
    target_host: str
    target_port: int
    verdict: EgressVerdict
    agent_name: str | None = None
    agent_version: str | None = None
    sandbox_id: str | None = None
    bytes_up: int = 0
    bytes_down: int = 0
    duration_ms: int | None = None
    error_msg: str | None = None


@dataclass(frozen=True)
class AllowlistKey:
    """The four-tuple that identifies one ``secret_allowlist`` entry."""

    tenant_id: UUID
    agent_name: str
    agent_version: str
    secret_ref: str
    purpose: str | None = field(default=None, compare=False)


class ProxyError(Exception):
    """Base class for proxy request failures."""


class BadForwardRequestError(ProxyError):
    """The ``/forward`` request is missing a required header — HTTP 400."""


class AllowlistDeniedError(ProxyError):
    """The caller is not allowed to reference this secret — HTTP 403."""

    def __init__(self, secret_ref: str) -> None:
        super().__init__(f"secret ref not on the allowlist: {secret_ref!r}")
        self.secret_ref = secret_ref


class SecretMissingError(ProxyError):
    """The allowed secret could not be resolved from the store — HTTP 502."""

    def __init__(self, secret_ref: str) -> None:
        super().__init__(f"secret ref could not be resolved: {secret_ref!r}")
        self.secret_ref = secret_ref
