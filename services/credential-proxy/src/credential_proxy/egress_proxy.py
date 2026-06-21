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

Only ``CONNECT`` (HTTPS) is handled here — it covers effectively every real
external API. Plain-HTTP absolute-form proxying is a follow-up.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import Awaitable, Callable
from uuid import UUID

from credential_proxy.audit import EgressAuditStore
from credential_proxy.domain import EgressAuditEntry, EgressVerdict
from helix_agent.common.egress_token import EgressIdentity, verify_egress_token
from helix_agent.common.url_validation import RemoteURLError, resolve_and_pin_host

logger = logging.getLogger(__name__)

_MAX_HEAD_BYTES = 16384
_PIPE_CHUNK = 65536


class EgressProxyServer:
    """A CONNECT-only forward proxy with token auth + SSRF block + audit."""

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
        method, target, headers = _parse_request(head)

        if method != "CONNECT":
            await _write_status(writer, 405, "Method Not Allowed")
            return

        identity = self._authenticate(headers)
        if identity is None:
            await _write_status(
                writer,
                407,
                "Proxy Authentication Required",
                extra=('Proxy-Authenticate: Basic realm="helix-egress"',),
            )
            return

        host, port = _split_hostport(target)
        if host is None:
            await _write_status(writer, 400, "Bad Request")
            return

        try:
            pinned_ip = self._resolve(host, port)
        except RemoteURLError as exc:
            await self._record(identity, host, port, "blocked_ssrf", error=str(exc))
            await _write_status(writer, 403, "Forbidden")
            return

        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(pinned_ip, port), self._connect_timeout
            )
        except (TimeoutError, OSError) as exc:
            await self._record(identity, host, port, "upstream_error", error=str(exc))
            await _write_status(writer, 502, "Bad Gateway")
            return

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


# ── wire helpers ──────────────────────────────────────────────────────────────


async def _read_head(reader: asyncio.StreamReader) -> bytes | None:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = await reader.read(1024)
        if not chunk:
            return None
        data += chunk
        if len(data) > _MAX_HEAD_BYTES:
            return None
    return data.split(b"\r\n\r\n", 1)[0]


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
