"""Tests for the transparent egress proxy (sandbox-egress §3.1).

A real local asyncio echo server stands in for the upstream; the SSRF check is
exercised via an injected resolver. No DB — a recording audit store.
"""

from __future__ import annotations

import asyncio
import base64

from credential_proxy.domain import EgressAuditEntry
from credential_proxy.egress_proxy import EgressProxyServer
from helix_agent.common.egress_token import mint_egress_token
from helix_agent.common.url_validation import RemoteURLError

_SECRET = "egress-test-secret"
_NOW = 500.0
_TENANT = "11111111-1111-1111-1111-111111111111"


class _RecordingEgressAudit:
    def __init__(self) -> None:
        self.entries: list[EgressAuditEntry] = []

    async def record(self, entry: EgressAuditEntry) -> None:
        self.entries.append(entry)


def _token(*, expires_at: float = 1000.0, allowlist: tuple[str, ...] = ()) -> str:
    return mint_egress_token(
        _SECRET,
        tenant_id=_TENANT,
        agent_name="agent",
        agent_version="1.0.0",
        sandbox_id="sbx-1",
        expires_at=expires_at,
        allowlist=allowlist,
    )


async def _start_echo() -> tuple[asyncio.AbstractServer, int]:
    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(_handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def _start_http_upstream(
    captured: list[tuple[bytes, bytes]], *, body: bytes = b"hello"
) -> tuple[asyncio.AbstractServer, int]:
    """A minimal HTTP/1.1 upstream: read the request head (+ Content-Length
    body), record both, reply 200 with ``body`` and close (Connection: close)."""

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            content_length = 0
            for line in head.split(b"\r\n"):
                if line.lower().startswith(b"content-length:"):
                    content_length = int(line.split(b":", 1)[1].strip())
            req_body = await reader.readexactly(content_length) if content_length else b""
            captured.append((head, req_body))
            resp = (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/plain\r\n"
                b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                b"Connection: close\r\n\r\n" + body
            )
            writer.write(resp)
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(_handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def _start_proxy(
    audit: _RecordingEgressAudit,
    *,
    resolve_host=lambda _h, _p: "127.0.0.1",  # type: ignore[no-untyped-def]
) -> tuple[asyncio.AbstractServer, int]:
    proxy = EgressProxyServer(
        token_secret=_SECRET,
        audit=audit,
        now=lambda: _NOW,
        resolve_host=resolve_host,
    )
    server = await proxy.start("127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def _connect_request(port: int, target: str, *, auth: str | None) -> tuple[bytes, ...]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    lines = [f"CONNECT {target} HTTP/1.1", f"Host: {target}"]
    if auth is not None:
        lines.append(f"Proxy-Authorization: Basic {auth}")
    writer.write(("\r\n".join([*lines, "", ""])).encode("latin-1"))
    await writer.drain()
    return reader, writer  # type: ignore[return-value]


def _basic(token: str) -> str:
    return base64.b64encode(f"{token}:".encode()).decode("ascii")


async def _wait_for_audit(audit: _RecordingEgressAudit, *, timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not audit.entries:
            await asyncio.sleep(0.01)


async def test_connect_tunnels_and_audits_allowed() -> None:
    audit = _RecordingEgressAudit()
    echo, echo_port = await _start_echo()
    proxy, proxy_port = await _start_proxy(audit)
    try:
        reader, writer = await _connect_request(
            proxy_port, f"example.com:{echo_port}", auth=_basic(_token())
        )
        status = await reader.readline()
        assert b"200" in status
        await reader.readuntil(b"\r\n")  # consume the blank line after status

        writer.write(b"ping")
        await writer.drain()
        echoed = await reader.readexactly(4)
        assert echoed == b"ping"

        writer.write_eof()
        await reader.read()  # drain to EOF
        writer.close()

        await _wait_for_audit(audit)
        entry = audit.entries[0]
        assert entry.verdict == "allowed"
        assert entry.target_host == "example.com"
        assert entry.target_port == echo_port
        assert entry.bytes_up >= 4
        assert entry.bytes_down >= 4
    finally:
        echo.close()
        proxy.close()
        await echo.wait_closed()
        await proxy.wait_closed()


async def test_missing_token_returns_407_and_audits_blocked_auth() -> None:
    # audit-eval Phase 4 — a pre-identity rejection is still traceable, recorded
    # as a platform anomaly (tenant_id=None), not attributed to any tenant.
    audit = _RecordingEgressAudit()
    proxy, proxy_port = await _start_proxy(audit)
    try:
        reader, writer = await _connect_request(proxy_port, "example.com:443", auth=None)
        status = await reader.readline()
        assert b"407" in status
        writer.close()
        await _wait_for_audit(audit)
        assert len(audit.entries) == 1
        entry = audit.entries[0]
        assert entry.verdict == "blocked_auth"
        assert entry.tenant_id is None
        assert entry.target_host == "example.com"
        assert entry.target_port == 443
    finally:
        proxy.close()
        await proxy.wait_closed()


async def test_bad_token_returns_407_and_audits_blocked_auth() -> None:
    audit = _RecordingEgressAudit()
    proxy, proxy_port = await _start_proxy(audit)
    try:
        reader, writer = await _connect_request(
            proxy_port, "example.com:443", auth=_basic("garbage.token.here")
        )
        status = await reader.readline()
        assert b"407" in status
        writer.close()
        await _wait_for_audit(audit)
        assert audit.entries[0].verdict == "blocked_auth"
        assert audit.entries[0].tenant_id is None
    finally:
        proxy.close()
        await proxy.wait_closed()


async def test_ssrf_blocked_returns_403_and_audits() -> None:
    audit = _RecordingEgressAudit()

    def _blocking_resolver(_host: str, _port: int) -> str:
        raise RemoteURLError("private/loopback/link-local IP not allowed")

    proxy, proxy_port = await _start_proxy(audit, resolve_host=_blocking_resolver)
    try:
        reader, writer = await _connect_request(
            proxy_port, "metadata.internal:80", auth=_basic(_token())
        )
        status = await reader.readline()
        assert b"403" in status
        writer.close()
        await _wait_for_audit(audit)
        assert audit.entries[0].verdict == "blocked_ssrf"
        assert audit.entries[0].target_host == "metadata.internal"
    finally:
        proxy.close()
        await proxy.wait_closed()


async def test_allowlist_blocks_unlisted_host_and_audits() -> None:
    audit = _RecordingEgressAudit()
    proxy, proxy_port = await _start_proxy(audit)
    try:
        # Token's allowlist permits only api.openai.com; request a different host.
        reader, writer = await _connect_request(
            proxy_port,
            "api.evil.com:443",
            auth=_basic(_token(allowlist=("api.openai.com",))),
        )
        status = await reader.readline()
        assert b"403" in status
        writer.close()
        await _wait_for_audit(audit)
        assert audit.entries[0].verdict == "blocked_allowlist"
        assert audit.entries[0].target_host == "api.evil.com"
    finally:
        proxy.close()
        await proxy.wait_closed()


async def test_allowlist_permits_listed_host() -> None:
    audit = _RecordingEgressAudit()
    echo, echo_port = await _start_echo()
    proxy, proxy_port = await _start_proxy(audit)
    try:
        # The allowlist names the host being connected to → tunnel succeeds.
        reader, writer = await _connect_request(
            proxy_port,
            f"api.openai.com:{echo_port}",
            auth=_basic(_token(allowlist=("openai.com",))),  # subdomain match
        )
        status = await reader.readline()
        assert b"200" in status
        await reader.readuntil(b"\r\n")
        writer.write(b"ok")
        await writer.drain()
        assert await reader.readexactly(2) == b"ok"
        writer.write_eof()
        await reader.read()
        writer.close()
        await _wait_for_audit(audit)
        assert audit.entries[0].verdict == "allowed"
    finally:
        echo.close()
        proxy.close()
        await echo.wait_closed()
        await proxy.wait_closed()


async def test_plain_http_get_proxied_and_audited() -> None:
    audit = _RecordingEgressAudit()
    captured: list[tuple[bytes, bytes]] = []
    upstream, up_port = await _start_http_upstream(captured)
    proxy, proxy_port = await _start_proxy(audit)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
        req = (
            f"GET http://example.com:{up_port}/path?q=1 HTTP/1.1\r\n"
            f"Host: example.com:{up_port}\r\n"
            f"Proxy-Authorization: Basic {_basic(_token())}\r\n"
            f"Accept: text/plain\r\n\r\n"
        )
        writer.write(req.encode("latin-1"))
        await writer.drain()
        resp = await reader.read()  # read to EOF (upstream forced Connection: close)
        assert b"200 OK" in resp
        assert b"hello" in resp

        await _wait_for_audit(audit)
        entry = audit.entries[0]
        assert entry.verdict == "allowed"
        assert entry.target_host == "example.com"  # parsed from the URL, not Host
        assert entry.target_port == up_port
        assert entry.bytes_down >= len(b"hello")

        head, _body = captured[0]
        # Request line rewritten to origin-form; proxy-auth stripped; close forced;
        # Host rebuilt from the URL authority; ordinary headers preserved.
        assert head.startswith(b"GET /path?q=1 HTTP/1.1\r\n")
        assert b"proxy-authorization" not in head.lower()
        assert b"Connection: close" in head
        assert f"Host: example.com:{up_port}".encode() in head
        assert b"Accept: text/plain" in head
        writer.close()
    finally:
        upstream.close()
        proxy.close()
        await upstream.wait_closed()
        await proxy.wait_closed()


async def test_plain_http_post_body_forwarded() -> None:
    audit = _RecordingEgressAudit()
    captured: list[tuple[bytes, bytes]] = []
    upstream, up_port = await _start_http_upstream(captured)
    proxy, proxy_port = await _start_proxy(audit)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
        body = b'{"k":"v"}'
        req = (
            f"POST http://example.com:{up_port}/submit HTTP/1.1\r\n"
            f"Host: example.com:{up_port}\r\n"
            f"Proxy-Authorization: Basic {_basic(_token())}\r\n"
            f"Content-Length: {len(body)}\r\n\r\n"
        ).encode("latin-1") + body
        writer.write(req)
        await writer.drain()
        resp = await reader.read()
        assert b"200 OK" in resp

        head, recv_body = captured[0]
        assert head.startswith(b"POST /submit HTTP/1.1\r\n")
        assert recv_body == body  # request body relayed intact

        await _wait_for_audit(audit)
        entry = audit.entries[0]
        assert entry.verdict == "allowed"
        assert entry.bytes_up >= len(body)
        writer.close()
    finally:
        upstream.close()
        proxy.close()
        await upstream.wait_closed()
        await proxy.wait_closed()


async def test_plain_http_missing_token_returns_407_and_audits() -> None:
    # The plain-HTTP path goes through the same auth gate as CONNECT.
    audit = _RecordingEgressAudit()
    proxy, proxy_port = await _start_proxy(audit)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
        writer.write(b"GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n")
        await writer.drain()
        status = await reader.readline()
        assert b"407" in status
        await _wait_for_audit(audit)
        assert audit.entries[0].verdict == "blocked_auth"
        writer.close()
    finally:
        proxy.close()
        await proxy.wait_closed()


async def test_plain_http_ssrf_blocked_returns_403() -> None:
    # SSRF pin applies to the plain-HTTP path too.
    def _blocked_resolver(_host: str, _port: int) -> str:
        raise RemoteURLError("private address blocked")

    audit = _RecordingEgressAudit()
    proxy, proxy_port = await _start_proxy(audit, resolve_host=_blocked_resolver)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
        writer.write(
            f"GET http://169.254.169.254/latest HTTP/1.1\r\n"
            f"Host: 169.254.169.254\r\n"
            f"Proxy-Authorization: Basic {_basic(_token())}\r\n\r\n".encode("latin-1")
        )
        await writer.drain()
        status = await reader.readline()
        assert b"403" in status
        await _wait_for_audit(audit)
        assert audit.entries[0].verdict == "blocked_ssrf"
        writer.close()
    finally:
        proxy.close()
        await proxy.wait_closed()


async def test_origin_form_request_rejected_400() -> None:
    # A non-absolute (origin-form) target isn't a proxy request → 400, even with
    # a valid token (the target is parsed before the auth gate).
    audit = _RecordingEgressAudit()
    proxy, proxy_port = await _start_proxy(audit)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
        writer.write(
            f"GET /path HTTP/1.1\r\nProxy-Authorization: Basic {_basic(_token())}\r\n\r\n".encode(
                "latin-1"
            )
        )
        await writer.drain()
        status = await reader.readline()
        assert b"400" in status
        writer.close()
    finally:
        proxy.close()
        await proxy.wait_closed()
