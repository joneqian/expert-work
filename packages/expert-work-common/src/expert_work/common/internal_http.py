"""Service-to-service HTTP client wiring — Stream C.2.

When the orchestrator (Stream E) or sandbox-supervisor (Stream F) calls
the control plane's internal endpoints (``/v1/quota/*`` ships in C.5),
the request is authenticated by **mTLS**, not a JWT. The shape of that
client is identical in dev and prod — only the cert paths and the base
URL change — so we centralise it here.

Two factory functions:

* :func:`build_internal_http_client` — async client (used inside FastAPI
  handlers, agent tools, the credential proxy, etc.)
* :func:`build_internal_http_sync_client` — sync client (admin scripts,
  Alembic env, one-shot CLIs)

Both expect the cert files to be **PEM-encoded** and reside on a path
readable by the caller. Production callers should hold cert paths via
the secret store (ADR-0007) and never inline the bytes.
"""

from __future__ import annotations

import logging
import os
import ssl
from collections.abc import Mapping
from pathlib import Path

import httpx

logger = logging.getLogger("expert_work.common.internal_http")

#: Default timeout for service-to-service calls. Short by design — a
#: caller that needs to hold a long-lived stream should override it.
DEFAULT_TIMEOUT_S: float = 5.0


class InternalHttpConfigError(ValueError):
    """Raised when the mTLS configuration is incomplete or unreadable."""


def build_internal_http_client(
    *,
    base_url: str,
    client_cert_path: str | os.PathLike[str],
    client_key_path: str | os.PathLike[str],
    ca_bundle_path: str | os.PathLike[str],
    timeout_s: float = DEFAULT_TIMEOUT_S,
    headers: Mapping[str, str] | None = None,
) -> httpx.AsyncClient:
    """Return an :class:`httpx.AsyncClient` configured for mTLS to ``base_url``.

    The caller owns the lifecycle — ``async with`` the result, or call
    ``aclose()`` explicitly. The cert files are validated for existence
    at construction time so misconfiguration surfaces before any request.
    """
    ssl_context = _build_ssl_context(
        client_cert_path=client_cert_path,
        client_key_path=client_key_path,
        ca_bundle_path=ca_bundle_path,
    )
    return httpx.AsyncClient(
        base_url=base_url,
        verify=ssl_context,
        timeout=timeout_s,
        headers=dict(headers) if headers else None,
    )


def build_internal_http_sync_client(
    *,
    base_url: str,
    client_cert_path: str | os.PathLike[str],
    client_key_path: str | os.PathLike[str],
    ca_bundle_path: str | os.PathLike[str],
    timeout_s: float = DEFAULT_TIMEOUT_S,
    headers: Mapping[str, str] | None = None,
) -> httpx.Client:
    """Synchronous twin of :func:`build_internal_http_client`."""
    ssl_context = _build_ssl_context(
        client_cert_path=client_cert_path,
        client_key_path=client_key_path,
        ca_bundle_path=ca_bundle_path,
    )
    return httpx.Client(
        base_url=base_url,
        verify=ssl_context,
        timeout=timeout_s,
        headers=dict(headers) if headers else None,
    )


def _build_ssl_context(
    *,
    client_cert_path: str | os.PathLike[str],
    client_key_path: str | os.PathLike[str],
    ca_bundle_path: str | os.PathLike[str],
) -> ssl.SSLContext:
    """Build a TLS-1.2+ context with client cert + custom CA trust store."""
    cert = Path(client_cert_path)
    key = Path(client_key_path)
    ca = Path(ca_bundle_path)
    if not cert.is_file():
        msg = f"client cert file not found: {cert}"
        raise InternalHttpConfigError(msg)
    if not key.is_file():
        msg = f"client key file not found: {key}"
        raise InternalHttpConfigError(msg)
    if not ca.is_file():
        msg = f"CA bundle file not found: {ca}"
        raise InternalHttpConfigError(msg)
    context = ssl.create_default_context(cafile=str(ca))
    context.load_cert_chain(certfile=str(cert), keyfile=str(key))
    return context
