"""Remote-URL validation — SSRF guard for tenant-supplied MCP server URLs.

A tenant registers a remote MCP server by URL; the control plane then
*connects out* to that URL (registration probe + runtime tool calls). An
unchecked URL lets a tenant point the platform at internal services or the
cloud metadata endpoint (169.254.169.254) — a classic SSRF. This guard is
applied at every connect-out site (registration, probe, runtime).

The check is static (scheme + IP-literal ranges + localhost names). It does
NOT resolve DNS, so it does not stop DNS-rebind (a hostname that resolves to a
public IP at check time and a private one at connect time). By decision, that
defense lives at the infrastructure egress layer (deny RFC1918 / loopback /
link-local egress from the control plane), NOT in this module — see
ADR-0009. This static guard is the defense-in-depth first layer: it blocks the
common cases (literal private IPs, localhost, metadata IP) cheaply.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse

_LOCALHOST_NAMES = frozenset(
    {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}
)


class RemoteURLError(ValueError):
    """A URL fails remote-endpoint validation (unsupported scheme or SSRF risk)."""


#: Explicit private/internal ranges that are a genuine SSRF / infra threat
#: (RFC1918 + IPv6 unique-local). We do NOT use ``ipaddress.is_private`` here:
#: as of CPython 3.12.4+ it also returns True for RFC2544 benchmarking
#: (198.18.0.0/15), TEST-NET docs ranges, and 240.0.0.0/4 — none of which are an
#: infra threat, and 198.18/15 is exactly what a local fake-ip DNS maps public
#: hosts into, so blanket ``is_private`` over-blocks (audit-eval Phase 2). These
#: harmless reserved ranges are non-routable / synthetic → allowed + audited at
#: egress. See docs/design/sandbox-audit-evaluation.md.
_BLOCKED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),  # IPv6 unique-local
)


def _ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # Precise, version-stable predicates for the always-dangerous ranges...
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        # is_link_local includes 169.254.0.0/16 (cloud metadata 169.254.169.254).
        return True
    # ...plus the explicit private/internal networks above.
    return any(ip in net for net in _BLOCKED_NETWORKS if ip.version == net.version)


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

    hostname = hostname.rstrip(".")  # FQDN trailing dot resolves identically

    if hostname.lower() in _LOCALHOST_NAMES:
        msg = f"localhost address {hostname!r} not allowed"
        raise RemoteURLError(msg)

    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        # Non-canonical IP literals (decimal 2130706433, hex 0x7f000001,
        # shortened/octal dotted 127.1 / 0177.0.0.1) parse as private addrs in
        # many HTTP stacks but not in ``ipaddress`` — reject them explicitly.
        if re.fullmatch(r"[0-9.]+", hostname) or re.fullmatch(r"0[xX][0-9a-fA-F]+", hostname):
            msg = f"non-canonical IP literal {hostname!r} not allowed"
            raise RemoteURLError(msg) from None
        return url

    if _ip_is_blocked(ip):
        msg = f"private/loopback/link-local IP {hostname!r} not allowed"
        raise RemoteURLError(msg)

    return url


def resolve_and_pin_host(host: str, port: int = 443) -> str:
    """Resolve ``host`` and return a single safe, **pinned** IP to connect to.

    Unlike :func:`validate_remote_url` (static, no DNS), this is for the egress
    proxy's connect-out: it resolves the name, rejects the connection if *any*
    resolved address is private/loopback/link-local/metadata, and returns the
    first allowed IP. The caller MUST connect to that returned IP (not re-resolve
    the name) — pinning is what closes the DNS-rebind window between check and
    connect (the gap :func:`validate_remote_url` leaves to the infra layer).

    Raises :class:`RemoteURLError` on a localhost name, a non-canonical IP
    literal, a blocked address, or an unresolvable host.
    """
    cleaned = host.rstrip(".")
    if cleaned.lower() in _LOCALHOST_NAMES:
        msg = f"localhost address {cleaned!r} not allowed"
        raise RemoteURLError(msg)

    # Reject the non-canonical IP literals ``ipaddress`` would refuse but an
    # HTTP/socket stack may accept as a private addr (decimal/hex/octal forms).
    if (
        re.fullmatch(r"[0-9.]+", cleaned) or re.fullmatch(r"0[xX][0-9a-fA-F]+", cleaned)
    ) and not _is_canonical_ip(cleaned):
        msg = f"non-canonical IP literal {cleaned!r} not allowed"
        raise RemoteURLError(msg)

    try:
        infos = socket.getaddrinfo(cleaned, port, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        msg = f"could not resolve host {cleaned!r}: {exc}"
        raise RemoteURLError(msg) from exc

    for info in infos:
        addr = info[4][0]
        ip = ipaddress.ip_address(addr)
        if _ip_is_blocked(ip):
            # Any blocked address among the results aborts — refuse to "pick a
            # good one" when a name also resolves to a private target.
            msg = f"host {cleaned!r} resolves to a blocked address {addr!r}"
            raise RemoteURLError(msg)

    if not infos:
        msg = f"host {cleaned!r} did not resolve to any address"
        raise RemoteURLError(msg)
    return str(infos[0][4][0])


def _is_canonical_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True
