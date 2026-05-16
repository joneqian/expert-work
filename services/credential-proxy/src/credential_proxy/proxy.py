"""``CredentialProxy`` — the F.5 secret-injection core.

One ``forward`` call: allowlist-check the ref, resolve it (cache →
:class:`SecretStore`), inject it as ``Authorization: Bearer``, relay the
request upstream, and write a ``credential_proxy_audit`` row. All
dependencies are injected so the logic is unit-testable with fakes
(test matrix #51 / #52 / #53).
"""

from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

from credential_proxy.allowlist import AllowlistStore
from credential_proxy.audit import ProxyAuditStore
from credential_proxy.cache import SecretCache
from credential_proxy.domain import (
    AllowlistDeniedError,
    AllowlistKey,
    ForwardRequest,
    ForwardResult,
    ProxyAuditEntry,
    ProxyStatus,
    SecretMissingError,
)
from credential_proxy.forwarder import Forwarder
from helix_agent.runtime.secret_store import SecretNotFoundError, SecretStore

logger = logging.getLogger(__name__)

#: Header the resolved secret is injected into (M0 fixed inject rule —
#: subsystems/11 ``InjectRule`` default ``Bearer {value}``).
_INJECT_HEADER = "Authorization"
_INJECT_KIND = "header"


class CredentialProxy:
    """Allowlist → resolve → inject → forward → audit."""

    def __init__(
        self,
        *,
        allowlist: AllowlistStore,
        secret_store: SecretStore,
        cache: SecretCache,
        audit: ProxyAuditStore,
        forwarder: Forwarder,
    ) -> None:
        self._allowlist = allowlist
        self._secret_store = secret_store
        self._cache = cache
        self._audit = audit
        self._forwarder = forwarder

    async def forward(self, request: ForwardRequest) -> ForwardResult:
        """Inject the requested secret and relay ``request`` upstream.

        Raises :class:`AllowlistDeniedError` (the ref is not declared)
        or :class:`SecretMissingError` (the ref does not resolve); both
        are audited before the raise.
        """
        started = time.monotonic()
        target_host = urlparse(request.upstream_url).hostname or request.upstream_url

        await self._enforce_allowlist(request, target_host)
        secret_value, status = await self._resolve_secret(request, target_host)

        result = await self._forwarder.forward(
            method=request.method,
            url=request.upstream_url,
            headers=_inject(request.headers, secret_value),
            body=request.body,
        )

        await self._record(
            request,
            target_host,
            status=status,
            inject_kind=_INJECT_KIND,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return _strip_control_headers(result)

    async def _enforce_allowlist(self, request: ForwardRequest, target_host: str) -> None:
        key = AllowlistKey(
            tenant_id=request.tenant_id,
            agent_name=request.agent_name,
            agent_version=request.agent_version,
            secret_ref=request.secret_ref,
        )
        if not await self._allowlist.is_allowed(key):
            await self._record(
                request,
                target_host,
                status="denied",
                error=f"secret ref {request.secret_ref!r} not on the allowlist",
            )
            raise AllowlistDeniedError(request.secret_ref)

    async def _resolve_secret(
        self, request: ForwardRequest, target_host: str
    ) -> tuple[str, ProxyStatus]:
        cache_key = (request.tenant_id, request.secret_ref)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached, "cached"

        try:
            value = await self._secret_store.get(request.secret_ref)
        except SecretNotFoundError as exc:
            await self._record(request, target_host, status="secret_miss", error=str(exc))
            raise SecretMissingError(request.secret_ref) from exc

        self._cache.put(cache_key, value)
        return value, "ok"

    async def _record(
        self,
        request: ForwardRequest,
        target_host: str,
        *,
        status: ProxyStatus,
        inject_kind: str | None = None,
        error: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        await self._audit.record(
            ProxyAuditEntry(
                tenant_id=request.tenant_id,
                agent_name=request.agent_name,
                agent_version=request.agent_version,
                session_id=request.session_id,
                sandbox_id=request.sandbox_id,
                secret_ref=request.secret_ref,
                target_host=target_host,
                inject_kind=inject_kind,
                status=status,
                error_msg=error,
                duration_ms=duration_ms,
            )
        )


def _inject(headers: dict[str, str], secret_value: str) -> dict[str, str]:
    """Return request headers with the secret injected.

    The ``X-Helix-*`` control headers and ``Host`` are dropped so they
    never reach the real upstream; ``Authorization`` carries the secret.
    """
    clean = {
        k: v
        for k, v in headers.items()
        if not k.lower().startswith("x-helix-") and k.lower() != "host"
    }
    clean[_INJECT_HEADER] = f"Bearer {secret_value}"
    return clean


def _strip_control_headers(result: ForwardResult) -> ForwardResult:
    """Strip ``X-Helix-*`` headers from the upstream response (§ 3.3)."""
    cleaned = {k: v for k, v in result.headers.items() if not k.lower().startswith("x-helix-")}
    if cleaned == result.headers:
        return result
    return ForwardResult(status=result.status, headers=cleaned, body=result.body)
