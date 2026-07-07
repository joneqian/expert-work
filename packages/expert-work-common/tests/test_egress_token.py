"""Tests for the per-sandbox egress identity token (sandbox-egress §3.2)."""

from __future__ import annotations

import pytest

from expert_work.common.egress_token import (
    host_in_allowlist,
    host_in_denylist,
    mint_egress_token,
    verify_egress_token,
)

_SECRET = "test-secret"
_TENANT = "11111111-1111-1111-1111-111111111111"


def _mint(
    *,
    expires_at: float = 1000.0,
    allowlist: tuple[str, ...] = (),
    denylist: tuple[str, ...] = (),
) -> str:
    return mint_egress_token(
        _SECRET,
        tenant_id=_TENANT,
        agent_name="pptx-agent",
        agent_version="1.0.0",
        sandbox_id="sbx-abc",
        expires_at=expires_at,
        allowlist=allowlist,
        denylist=denylist,
    )


def test_round_trip_returns_identity() -> None:
    token = _mint(expires_at=1000.0)
    identity = verify_egress_token(_SECRET, token, now=500.0)
    assert identity is not None
    assert identity.tenant_id == _TENANT
    assert identity.agent_name == "pptx-agent"
    assert identity.agent_version == "1.0.0"
    assert identity.sandbox_id == "sbx-abc"


def test_wrong_secret_rejected() -> None:
    token = _mint()
    assert verify_egress_token("other-secret", token, now=500.0) is None


def test_expired_token_rejected() -> None:
    token = _mint(expires_at=1000.0)
    assert verify_egress_token(_SECRET, token, now=1000.0) is None  # now == exp → expired
    assert verify_egress_token(_SECRET, token, now=1001.0) is None


def test_tampered_payload_rejected() -> None:
    token = _mint()
    version, payload_b64, signature = token.split(".")
    # Flip a char in the payload — signature no longer matches.
    tampered_payload = ("A" if payload_b64[0] != "A" else "B") + payload_b64[1:]
    tampered = f"{version}.{tampered_payload}.{signature}"
    assert verify_egress_token(_SECRET, tampered, now=500.0) is None


def test_malformed_tokens_return_none() -> None:
    for bad in ("", "notatoken", "v1.only", "v2.a.b", "a.b.c.d"):
        assert verify_egress_token(_SECRET, bad, now=500.0) is None


def test_empty_secret_rejected_on_verify() -> None:
    token = _mint()
    assert verify_egress_token("", token, now=500.0) is None


def test_allowlist_round_trips_in_token() -> None:
    token = _mint(allowlist=("api.openai.com", "files.example.com"))
    identity = verify_egress_token(_SECRET, token, now=500.0)
    assert identity is not None
    assert identity.allowlist == ("api.openai.com", "files.example.com")


def test_no_allowlist_defaults_empty() -> None:
    identity = verify_egress_token(_SECRET, _mint(), now=500.0)
    assert identity is not None
    assert identity.allowlist == ()


@pytest.mark.parametrize(
    ("host", "allowlist", "expected"),
    [
        ("api.openai.com", (), True),  # empty allowlist → allow all
        ("api.openai.com", ("api.openai.com",), True),  # exact
        ("api.openai.com", ("openai.com",), True),  # subdomain of entry
        ("openai.com", ("openai.com",), True),  # apex
        ("evilopenai.com", ("openai.com",), False),  # not a subdomain
        ("api.evil.com", ("openai.com",), False),  # unrelated
        ("API.OpenAI.com", ("openai.com",), True),  # case-insensitive
    ],
)
def test_host_in_allowlist(host: str, allowlist: tuple[str, ...], expected: bool) -> None:
    assert host_in_allowlist(host, allowlist) is expected


def test_denylist_round_trips_in_token() -> None:
    token = _mint(denylist=("evil.com", "tracker.example.net"))
    identity = verify_egress_token(_SECRET, token, now=500.0)
    assert identity is not None
    assert identity.denylist == ("evil.com", "tracker.example.net")


def test_no_denylist_defaults_empty() -> None:
    identity = verify_egress_token(_SECRET, _mint(), now=500.0)
    assert identity is not None
    assert identity.denylist == ()


@pytest.mark.parametrize(
    ("host", "denylist", "expected"),
    [
        ("evil.com", (), False),  # empty denylist → blocks nothing
        ("evil.com", ("evil.com",), True),  # exact
        ("api.evil.com", ("evil.com",), True),  # subdomain of entry
        ("notevil.com", ("evil.com",), False),  # not a subdomain
        ("EVIL.com", ("evil.com",), True),  # case-insensitive
        ("pypi.org", ("evil.com",), False),  # unrelated host allowed
    ],
)
def test_host_in_denylist(host: str, denylist: tuple[str, ...], expected: bool) -> None:
    assert host_in_denylist(host, denylist) is expected
