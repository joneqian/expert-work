"""Outbound HTTP — the proxy's request relay to the real upstream.

A :class:`Forwarder` Protocol so :class:`CredentialProxy` is testable
without real network calls; :class:`AiohttpForwarder` is the real impl.
"""

from __future__ import annotations

from typing import Protocol

import aiohttp

from credential_proxy.domain import ForwardResult


class Forwarder(Protocol):
    """Relays one request to the real upstream and returns its response."""

    async def forward(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> ForwardResult:
        """Send the (secret-injected) request upstream; return the response."""


class AiohttpForwarder:
    """:class:`Forwarder` over a long-lived :class:`aiohttp.ClientSession`."""

    def __init__(self, *, timeout_s: float) -> None:
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)
        self._session: aiohttp.ClientSession | None = None

    async def forward(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> ForwardResult:
        session = self._ensure_session()
        async with session.request(method, url, headers=headers, data=body or None) as response:
            payload = await response.read()
            return ForwardResult(
                status=response.status,
                headers=dict(response.headers),
                body=payload,
            )

    async def aclose(self) -> None:
        """Close the underlying session — call on service shutdown."""
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _ensure_session(self) -> aiohttp.ClientSession:
        # Lazily created so the session binds to the running event loop.
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session
