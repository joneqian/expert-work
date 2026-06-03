"""Remote-URL validation — SSRF guard for tenant-supplied MCP server URLs.

A tenant registers a remote MCP server by URL; the control plane then
*connects out* to that URL (registration probe + runtime tool calls). An
unchecked URL lets a tenant point the platform at internal services or the
cloud metadata endpoint (169.254.169.254) — a classic SSRF. This guard is
applied at every connect-out site (registration, probe, runtime).

The check is static (scheme + IP-literal ranges + localhost names). It does
NOT resolve DNS; DNS-rebind defense (resolve, then re-check the resolved IP)
is a deeper follow-up. Static checks block the common cases (literal private
IPs, localhost, metadata IP).
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

_LOCALHOST_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})


class RemoteURLError(ValueError):
    """A URL fails remote-endpoint validation (unsupported scheme or SSRF risk)."""


def _ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local  # includes 169.254.0.0/16 (cloud metadata)
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified  # 0.0.0.0 / ::
    )


def validate_remote_url(
    url: str,
    *,
    allowed_schemes: tuple[str, ...] = ("http", "https"),
) -> str:
    """Validate a tenant-supplied remote URL for safe connect-out.

    Returns ``url`` unchanged when valid. Raises :class:`RemoteURLError` for
    an unsupported scheme, a missing hostname, a localhost name, or a
    private / loopback / link-local / reserved / multicast / unspecified IP
    literal.

    ``allowed_schemes`` defaults to ``("http", "https")``; pass
    ``("https",)`` to forbid plaintext (production).
    """
    parsed = urlparse(url)

    if parsed.scheme not in allowed_schemes:
        msg = f"unsupported URL scheme {parsed.scheme!r}; allowed: {allowed_schemes}"
        raise RemoteURLError(msg)

    hostname = parsed.hostname
    if not hostname:
        msg = f"URL has no hostname: {url!r}"
        raise RemoteURLError(msg)

    if hostname.lower() in _LOCALHOST_NAMES:
        msg = f"localhost address {hostname!r} not allowed"
        raise RemoteURLError(msg)

    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        # Hostname is a DNS name, not an IP literal — static check passes.
        return url

    if _ip_is_blocked(ip):
        msg = f"private/loopback/link-local IP {hostname!r} not allowed"
        raise RemoteURLError(msg)

    return url
