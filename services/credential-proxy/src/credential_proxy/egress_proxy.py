"""Transparent egress proxy — the audited path from a sandbox to the internet.

sandbox-egress design §3.1. A raw-asyncio HTTP ``CONNECT`` proxy (run alongside
the aiohttp ``/forward`` app in the same process). A sandbox configured with
``HTTPS_PROXY=http://<token>:@<proxy>`` reaches public APIs with no code changes;
the proxy:

* authenticates the per-sandbox token (design §3.2) — bad/missing → 407;
* resolves the target and **pins** the IP, refusing private/loopback/link-local/
  metadata addresses (necessary SSRF control) → 403 ``blocked_ssrf``;
* tunnels the (TLS) bytes without decrypting — it sees only ``host:port`` and
  the byte volumes, never payload;
* writes one ``sandbox_egress_audit`` row per connection (audit over blocking).

Two methods are handled, both behind the **same** auth + allowlist + SSRF-pin
gate (:meth:`EgressProxyServer._secure_connect`):

* ``CONNECT`` (HTTPS) — opaque byte tunnel, the proxy never sees plaintext.
* plain HTTP absolute-form (``GET http://host/path``) — the proxy rewrites the
  request line to origin-form, strips proxy/hop-by-hop headers, forces
  ``Connection: close`` (one request per connection, so a pipelined second
  request can't smuggle past the per-request host check) and relays the body
  and response. For plain HTTP it *does* see the bytes — there is no TLS — but
  it neither inspects nor logs the payload (only host/port + byte volumes).
"""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit
from uuid import UUID

from credential_proxy.audit import EgressAuditStore
from credential_proxy.domain import EgressAuditEntry, EgressVerdict
from helix_agent.common.egress_token import (
    EgressIdentity,
    host_in_allowlist,
    verify_egress_token,
)
from helix_agent.common.url_validation import RemoteURLError, resolve_and_pin_host

logger = logging.getLogger(__name__)

_MAX_HEAD_BYTES = 16384
_PIPE_CHUNK = 65536


class EgressProxyServer:
    """A forward proxy (CONNECT + plain-HTTP) with token auth + SSRF block + audit."""

    def __init__(
        self,
        *,
        token_secret: str,
        audit: EgressAuditStore,
        now: Callable[[], float],
        resolve_host: Callable[[str, int], str] = resolve_and_pin_host,
        connect_timeout_s: float = 10.0,
    ) -> None:
        self._secret = token_secret
        self._audit = audit
        self._now = now
        self._resolve = resolve_host
        self._connect_timeout = connect_timeout_s

    async def start(self, host: str, port: int) -> asyncio.AbstractServer:
        """Bind and start serving; returns the server (caller owns its lifetime)."""
        server = await asyncio.start_server(self.handle, host, port)
        logger.info("egress_proxy.listen port=%d", port)
        return server

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await self._handle(reader, writer)
        except Exception:  # never let one connection take the server down
            logger.exception("egress_proxy.handler_error")
        finally:
            await _close_writer(writer)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await _read_head(reader)
        if head is None:
            return  # client hung up / headers too large
        head_bytes, rest = head
        method, target, headers = _parse_request(head_bytes)

        if method == "CONNECT":
            await self._handle_connect(reader, writer, target, headers)
        else:
            # Anything else is treated as a plain-HTTP absolute-form proxy
            # request (``GET http://host/path``); a non-URL target → 400.
            await self._handle_http(reader, writer, method, target, headers, rest)

    async def _secure_connect(
        self,
        writer: asyncio.StreamWriter,
        headers: dict[str, str],
        host: str,
        port: int,
    ) -> tuple[EgressIdentity, asyncio.StreamReader, asyncio.StreamWriter] | None:
        """Auth + allowlist + SSRF-pin + open the upstream connection — the gate
        shared by both the CONNECT and plain-HTTP paths. On any rejection it
        writes the status line + an audit row and returns ``None``; on success
        it returns ``(identity, up_reader, up_writer)``."""
        identity = self._authenticate(headers)
        if identity is None:
            # audit-eval Phase 4 — a missing/invalid/expired token has no
            # trustworthy tenant; record it as a platform-level anomaly
            # (tenant_id=None) so the rejection is still traceable.
            await self._record_unauthenticated(host, port)
            await _write_status(
                writer,
                407,
                "Proxy Authentication Required",
                extra=('Proxy-Authenticate: Basic realm="helix-egress"',),
            )
            return None

        # sandbox-egress §3.1 Phase 2 — optional per-agent host allowlist
        # (opt-in hardening). Empty allowlist = any public host (audited).
        if not host_in_allowlist(host, identity.allowlist):
            await self._record(identity, host, port, "blocked_allowlist")
            await _write_status(writer, 403, "Forbidden")
            return None

        try:
            pinned_ip = self._resolve(host, port)
        except RemoteURLError as exc:
            await self._record(identity, host, port, "blocked_ssrf", error=str(exc))
            await _write_status(writer, 403, "Forbidden")
            return None

        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(pinned_ip, port), self._connect_timeout
            )
        except (TimeoutError, OSError) as exc:
            await self._record(identity, host, port, "upstream_error", error=str(exc))
            await _write_status(writer, 502, "Bad Gateway")
            return None

        return identity, up_reader, up_writer

    async def _handle_connect(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        target: str,
        headers: dict[str, str],
    ) -> None:
        host, port = _split_hostport(target)
        if host is None:
            await _write_status(writer, 400, "Bad Request")
            return
        conn = await self._secure_connect(writer, headers, host, port)
        if conn is None:
            return
        identity, up_reader, up_writer = conn
        await _write_raw(writer, b"HTTP/1.1 200 Connection Established\r\n\r\n")
        started = self._now()
        bytes_up, bytes_down = await _tunnel(reader, writer, up_reader, up_writer)
        await self._record(
            identity,
            host,
            port,
            "allowed",
            bytes_up=bytes_up,
            bytes_down=bytes_down,
            duration_ms=int((self._now() - started) * 1000),
        )

    async def _handle_http(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        method: str,
        target: str,
        headers: dict[str, str],
        rest: bytes,
    ) -> None:
        parsed = _parse_http_target(target)
        if parsed is None:
            await _write_status(writer, 400, "Bad Request")
            return
        host, port, path = parsed
        conn = await self._secure_connect(writer, headers, host, port)
        if conn is None:
            return
        identity, up_reader, up_writer = conn
        forward_head = _build_forward_head(method, path, host, port, headers)
        started = self._now()
        bytes_up, bytes_down = await _relay_http(
            reader, writer, up_reader, up_writer, head=forward_head, rest=rest
        )
        await self._record(
            identity,
            host,
            port,
            "allowed",
            bytes_up=bytes_up,
            bytes_down=bytes_down,
            duration_ms=int((self._now() - started) * 1000),
        )

    def _authenticate(self, headers: dict[str, str]) -> EgressIdentity | None:
        token = _extract_token(headers.get("proxy-authorization"))
        if token is None:
            return None
        return verify_egress_token(self._secret, token, now=self._now())

    async def _record(
        self,
        identity: EgressIdentity,
        host: str,
        port: int,
        verdict: EgressVerdict,
        *,
        bytes_up: int = 0,
        bytes_down: int = 0,
        duration_ms: int | None = None,
        error: str | None = None,
    ) -> None:
        try:
            tenant_id = UUID(identity.tenant_id)
        except ValueError:
            logger.warning("egress_proxy.bad_tenant_in_token")
            return
        await self._audit.record(
            EgressAuditEntry(
                tenant_id=tenant_id,
                target_host=host,
                target_port=port,
                verdict=verdict,
                agent_name=identity.agent_name,
                agent_version=identity.agent_version,
                sandbox_id=identity.sandbox_id,
                bytes_up=bytes_up,
                bytes_down=bytes_down,
                duration_ms=duration_ms,
                error_msg=error,
            )
        )

    async def _record_unauthenticated(self, host: str, port: int) -> None:
        """Record a ``blocked_auth`` row for a pre-identity rejection (407).

        No token ⇒ no tenant; ``tenant_id=None`` makes it a platform anomaly
        (cross-tenant view only). Best-effort — never let auditing break the
        rejection path."""
        try:
            await self._audit.record(
                EgressAuditEntry(
                    tenant_id=None,
                    target_host=host,
                    target_port=port,
                    verdict="blocked_auth",
                )
            )
        except Exception:
            logger.exception("egress_proxy.audit_failed verdict=blocked_auth")


# ── wire helpers ──────────────────────────────────────────────────────────────


async def _read_head(reader: asyncio.StreamReader) -> tuple[bytes, bytes] | None:
    """Read up to the end of the request headers; return ``(head, rest)`` where
    ``rest`` is any already-buffered bytes past the blank line (the start of a
    plain-HTTP request body — CONNECT has none and ignores it)."""
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = await reader.read(1024)
        if not chunk:
            return None
        data += chunk
        if len(data) > _MAX_HEAD_BYTES:
            return None
    head, _, rest = data.partition(b"\r\n\r\n")
    return head, rest


def _parse_request(head: bytes) -> tuple[str, str, dict[str, str]]:
    lines = head.split(b"\r\n")
    request_line = lines[0].decode("latin-1")
    parts = request_line.split(" ")
    method = parts[0] if parts else ""
    target = parts[1] if len(parts) > 1 else ""
    headers: dict[str, str] = {}
    for line in lines[1:]:
        key, sep, value = line.partition(b":")
        if sep:
            headers[key.decode("latin-1").strip().lower()] = value.decode("latin-1").strip()
    return method, target, headers


def _split_hostport(target: str) -> tuple[str | None, int]:
    host, sep, port_raw = target.rpartition(":")
    if not sep or not host:
        return None, 0
    try:
        return host, int(port_raw)
    except ValueError:
        return None, 0


def _parse_http_target(target: str) -> tuple[str, int, str] | None:
    """Parse a plain-HTTP absolute-form target (``http://host[:port]/path?q``)
    into ``(host, port, origin_form_path)``. Returns ``None`` for a non-``http``
    scheme or a host-less target (origin-form / garbage) → caller 400s."""
    parsed = urlsplit(target)
    if parsed.scheme.lower() != "http" or not parsed.hostname:
        return None
    host = parsed.hostname
    try:
        port = parsed.port or 80
    except ValueError:
        return None  # malformed port in the authority
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return host, port, path


#: Request headers the proxy must not forward verbatim: the proxy-auth token
#: (consumed here), proxy/connection hop-by-hop controls, and ``host`` (rebuilt
#: from the URL authority so it can't disagree with the pinned target).
_DROP_FORWARD_HEADERS = frozenset({"proxy-authorization", "proxy-connection", "connection", "host"})


def _header_title(key: str) -> str:
    """Re-title a lower-cased header name (``content-type`` → ``Content-Type``).
    Header names are case-insensitive; this only restores conventional casing."""
    return "-".join(part.capitalize() for part in key.split("-"))


def _build_forward_head(
    method: str, path: str, host: str, port: int, headers: dict[str, str]
) -> bytes:
    """Rewrite the request to origin-form for the upstream: ``METHOD path
    HTTP/1.1`` + a URL-derived ``Host`` + the client headers minus proxy/
    connection controls, with ``Connection: close`` forced (one request per
    connection)."""
    authority = host if port == 80 else f"{host}:{port}"
    lines = [f"{method} {path} HTTP/1.1", f"Host: {authority}"]
    for key, value in headers.items():
        if key in _DROP_FORWARD_HEADERS:
            continue
        lines.append(f"{_header_title(key)}: {value}")
    lines.append("Connection: close")
    lines.extend(["", ""])
    return "\r\n".join(lines).encode("latin-1")


async def _relay_http(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    up_reader: asyncio.StreamReader,
    up_writer: asyncio.StreamWriter,
    *,
    head: bytes,
    rest: bytes,
) -> tuple[int, int]:
    """Send the rewritten request head (+ any already-buffered body ``rest``) to
    upstream, then relay the request body up and the response down. The response
    completing (upstream EOF, guaranteed by the forced ``Connection: close``)
    tears the connection down. Returns ``(bytes_up, bytes_down)``."""
    up_writer.write(head)
    if rest:
        up_writer.write(rest)
    await up_writer.drain()
    counters = [len(head) + len(rest), 0]  # [bytes_up, bytes_down]

    async def _pump_up() -> None:
        try:
            while True:
                data = await client_reader.read(_PIPE_CHUNK)
                if not data:
                    break
                counters[0] += len(data)
                up_writer.write(data)
                await up_writer.drain()
        except (OSError, asyncio.CancelledError):
            pass

    up_task = asyncio.create_task(_pump_up())
    try:
        while True:
            data = await up_reader.read(_PIPE_CHUNK)
            if not data:
                break
            counters[1] += len(data)
            client_writer.write(data)
            await client_writer.drain()
    except OSError:
        # Either side dropped mid-response — expected at teardown.
        pass
    finally:
        up_task.cancel()
        try:
            await up_task
        except asyncio.CancelledError:
            pass
        await _close_writer(up_writer)
    return counters[0], counters[1]


def _extract_token(auth_header: str | None) -> str | None:
    """Pull the egress token from ``Proxy-Authorization: Basic <b64(token:)>``.

    The token is injected as the proxy URL's username (empty password), so HTTP
    clients send it as standard Basic proxy auth."""
    if not auth_header:
        return None
    scheme, _, value = auth_header.partition(" ")
    if scheme.lower() != "basic":
        return None
    try:
        decoded = base64.b64decode(value).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    user = decoded.split(":", 1)[0]
    return user or None


async def _tunnel(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    up_reader: asyncio.StreamReader,
    up_writer: asyncio.StreamWriter,
) -> tuple[int, int]:
    """Pipe bytes both ways until either side closes; return (up, down) volumes."""
    counters = [0, 0]  # [bytes_up (client→upstream), bytes_down (upstream→client)]

    async def _pump(src: asyncio.StreamReader, dst: asyncio.StreamWriter, idx: int) -> None:
        try:
            while True:
                data = await src.read(_PIPE_CHUNK)
                if not data:
                    break
                counters[idx] += len(data)
                dst.write(data)
                await dst.drain()
        except (OSError, asyncio.CancelledError):
            # Either side dropping the connection ends the pump — expected at
            # tunnel teardown, nothing to recover.
            pass
        finally:
            if dst.can_write_eof():
                try:
                    dst.write_eof()
                except OSError:
                    # Peer already closed its read end — best-effort EOF.
                    pass

    await asyncio.gather(
        _pump(client_reader, up_writer, 0),
        _pump(up_reader, client_writer, 1),
    )
    await _close_writer(up_writer)
    return counters[0], counters[1]


async def _write_status(
    writer: asyncio.StreamWriter, code: int, reason: str, *, extra: tuple[str, ...] = ()
) -> None:
    lines = [f"HTTP/1.1 {code} {reason}", *extra, "Content-Length: 0", "", ""]
    await _write_raw(writer, "\r\n".join(lines).encode("latin-1"))


async def _write_raw(writer: asyncio.StreamWriter, data: bytes) -> None:
    try:
        writer.write(data)
        await writer.drain()
    except OSError:
        # Client already gone — the status/handshake write is best-effort.
        pass


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    try:
        if not writer.is_closing():
            writer.close()
        await writer.wait_closed()
    except OSError:
        # Socket already torn down — closing is idempotent / best-effort.
        pass


# Re-exported for callers that build the server with a custom resolver/await.
EgressHandler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]
