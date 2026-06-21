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


def _token(*, expires_at: float = 1000.0) -> str:
    return mint_egress_token(
        _SECRET,
        tenant_id=_TENANT,
        agent_name="agent",
        agent_version="1.0.0",
        sandbox_id="sbx-1",
        expires_at=expires_at,
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


async def test_missing_token_returns_407() -> None:
    audit = _RecordingEgressAudit()
    proxy, proxy_port = await _start_proxy(audit)
    try:
        reader, writer = await _connect_request(proxy_port, "example.com:443", auth=None)
        status = await reader.readline()
        assert b"407" in status
        writer.close()
        assert audit.entries == []  # unauthenticated → not attributed/audited
    finally:
        proxy.close()
        await proxy.wait_closed()


async def test_bad_token_returns_407() -> None:
    audit = _RecordingEgressAudit()
    proxy, proxy_port = await _start_proxy(audit)
    try:
        reader, writer = await _connect_request(
            proxy_port, "example.com:443", auth=_basic("garbage.token.here")
        )
        status = await reader.readline()
        assert b"407" in status
        writer.close()
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


async def test_non_connect_method_rejected() -> None:
    audit = _RecordingEgressAudit()
    proxy, proxy_port = await _start_proxy(audit)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
        writer.write(b"GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n")
        await writer.drain()
        status = await reader.readline()
        assert b"405" in status
        writer.close()
    finally:
        proxy.close()
        await proxy.wait_closed()
