"""Unit tests for the remote-URL SSRF guard."""

from __future__ import annotations

import pytest

from expert_work.common.url_validation import (
    RemoteURLError,
    resolve_and_pin_host,
    validate_remote_url,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://mcp.githubcopilot.com/mcp",
        "https://api.example.com:8443/sse",
        "http://public.example.org/mcp",
    ],
)
def test_accepts_public_https_and_http(url: str) -> None:
    assert validate_remote_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/mcp",
        "http://127.0.0.1:8080/mcp",
        "http://[::1]/mcp",
        "http://10.1.2.3/mcp",
        "http://172.16.5.4/mcp",
        "http://192.168.0.10/mcp",
        "http://169.254.169.254/latest/meta-data",  # cloud metadata
        "http://0.0.0.0/mcp",
    ],
)
def test_rejects_private_loopback_linklocal_metadata(url: str) -> None:
    with pytest.raises(RemoteURLError):
        validate_remote_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "gopher://example.com/x",
        "ws://example.com/x",
    ],
)
def test_rejects_unsupported_schemes(url: str) -> None:
    with pytest.raises(RemoteURLError):
        validate_remote_url(url)


def test_rejects_missing_hostname() -> None:
    with pytest.raises(RemoteURLError):
        validate_remote_url("https:///mcp")


def test_https_only_mode_rejects_http() -> None:
    with pytest.raises(RemoteURLError):
        validate_remote_url("http://public.example.org/mcp", allowed_schemes=("https",))


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost./mcp",  # trailing-dot FQDN resolves as localhost
        "http://127.1/mcp",  # shortened dotted-decimal
        "http://0x7f000001/mcp",  # hex literal for 127.0.0.1
        "http://2130706433/mcp",  # decimal literal for 127.0.0.1
        "http://0177.0.0.1/mcp",  # octal dotted-decimal
    ],
)
def test_rejects_ssrf_bypass_variants(url: str) -> None:
    with pytest.raises(RemoteURLError):
        validate_remote_url(url)


def test_accepts_public_dns_name_with_digits() -> None:
    url = "https://api2.example.com/mcp"
    assert validate_remote_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "https://198.18.0.1/x",  # RFC2544 benchmarking (was over-blocked via is_reserved)
        "https://198.19.255.254/x",
    ],
)
def test_accepts_reserved_but_harmless_ranges(url: str) -> None:
    # audit-eval Phase 2 — is_reserved removed; 198.18.0.0/15 is no longer an
    # SSRF block (a local fake-ip DNS maps public hosts here). Audited at egress.
    assert validate_remote_url(url) == url


def test_resolve_and_pin_allows_reserved_benchmark_range(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "expert_work.common.url_validation.socket.getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("198.18.0.29", 443))],
    )
    assert resolve_and_pin_host("fakeip.example", 443) == "198.18.0.29"


# ── resolve_and_pin_host (egress proxy connect-out) ──────────────────────────


def test_resolve_and_pin_returns_pinned_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    # Stub getaddrinfo so the test does no real DNS — public IP → pinned.
    monkeypatch.setattr(
        "expert_work.common.url_validation.socket.getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    assert resolve_and_pin_host("example.com", 443) == "93.184.216.34"


def test_resolve_and_pin_blocks_private_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    # A name that resolves to a private IP (DNS-rebind shape) is refused.
    monkeypatch.setattr(
        "expert_work.common.url_validation.socket.getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("10.0.0.5", 443))],
    )
    with pytest.raises(RemoteURLError):
        resolve_and_pin_host("rebind.example.com", 443)


def test_resolve_and_pin_blocks_metadata_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "expert_work.common.url_validation.socket.getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("169.254.169.254", 80))],
    )
    with pytest.raises(RemoteURLError):
        resolve_and_pin_host("metadata", 80)


def test_resolve_and_pin_blocks_localhost_name() -> None:
    with pytest.raises(RemoteURLError):
        resolve_and_pin_host("localhost", 443)


def test_resolve_and_pin_blocks_noncanonical_literal() -> None:
    with pytest.raises(RemoteURLError):
        resolve_and_pin_host("2130706433", 443)  # decimal 127.0.0.1


def test_resolve_and_pin_unresolvable_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: object, **_k: object) -> list[object]:
        raise OSError("nxdomain")

    monkeypatch.setattr("expert_work.common.url_validation.socket.getaddrinfo", _boom)
    with pytest.raises(RemoteURLError):
        resolve_and_pin_host("nope.invalid", 443)
