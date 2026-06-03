"""Unit tests for the remote-URL SSRF guard."""

from __future__ import annotations

import pytest

from helix_agent.common.url_validation import RemoteURLError, validate_remote_url


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
