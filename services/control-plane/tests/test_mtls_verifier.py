"""Unit tests for the XFCC parser + :class:`MTLSVerifier`."""

from __future__ import annotations

from uuid import UUID

import pytest

from control_plane.auth import (
    InvalidTokenError,
    MTLSVerifier,
    XfccElement,
    build_mtls_verifier,
    parse_xfcc_header,
)

_SYSTEM_TENANT = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")


# ---------------------------------------------------------------------------
# parse_xfcc_header
# ---------------------------------------------------------------------------


def test_empty_header_returns_no_elements() -> None:
    assert parse_xfcc_header("") == []
    assert parse_xfcc_header("   ") == []


def test_simple_subject_pair_parsed() -> None:
    header = 'Subject="CN=orchestrator,O=expert_work"'
    elements = parse_xfcc_header(header)
    assert len(elements) == 1
    assert elements[0].subject_dn == "CN=orchestrator,O=expert_work"
    assert elements[0].common_name == "orchestrator"


def test_unquoted_value_parsed() -> None:
    header = "Hash=abc123;URI=spiffe://expert_work/orchestrator"
    elements = parse_xfcc_header(header)
    assert len(elements) == 1
    assert elements[0].sha256 == "abc123"
    assert elements[0].uri == "spiffe://expert_work/orchestrator"


def test_quoted_subject_with_internal_comma_kept_together() -> None:
    header = 'Subject="CN=foo,O=expert_work,L=Shanghai"'
    elements = parse_xfcc_header(header)
    assert elements[0].subject_dn == "CN=foo,O=expert_work,L=Shanghai"
    assert elements[0].common_name == "foo"


def test_multiple_xfcc_elements_split_on_top_level_comma() -> None:
    header = (
        'Subject="CN=orchestrator,O=expert_work";Hash=aaa,'
        'Subject="CN=intermediate-ca,O=expert_work";Hash=bbb'
    )
    elements = parse_xfcc_header(header)
    assert len(elements) == 2
    assert elements[0].common_name == "orchestrator"
    assert elements[0].sha256 == "aaa"
    assert elements[1].common_name == "intermediate-ca"
    assert elements[1].sha256 == "bbb"


def test_openssl_style_dn_extracts_cn() -> None:
    """Nginx with ``$ssl_client_s_dn`` defaults to OpenSSL slash form."""
    header = 'Subject="/C=CN/O=expert_work/CN=orchestrator"'
    elements = parse_xfcc_header(header)
    assert elements[0].common_name == "orchestrator"


def test_escaped_quote_inside_subject_kept() -> None:
    # XFCC permits ``\"`` inside quoted strings.
    header = 'Subject="CN=foo\\"bar"'
    elements = parse_xfcc_header(header)
    assert elements[0].subject_dn == 'CN=foo"bar'


def test_malformed_garbage_returns_empty_or_partial_without_raising() -> None:
    # Should never raise; missing CN downstream is what fails verification.
    elements = parse_xfcc_header("not a real xfcc header at all")
    # No key=value pairs found, so no elements produced.
    assert elements == []


def test_common_name_falls_back_to_empty_string_without_cn() -> None:
    element = XfccElement(subject_dn="O=expert_work")
    assert element.common_name == ""


# ---------------------------------------------------------------------------
# MTLSVerifier.verify
# ---------------------------------------------------------------------------


def _verifier(
    *,
    allowed: tuple[str, ...] = ("orchestrator",),
    require_uri: bool = False,
) -> MTLSVerifier:
    return build_mtls_verifier(
        allowed_subjects=allowed,
        system_tenant_id=_SYSTEM_TENANT,
        require_uri_san=require_uri,
    )


def test_happy_path_builds_service_principal() -> None:
    header = 'Subject="CN=orchestrator,O=expert_work";Hash=abc'
    principal = _verifier().verify(header)
    assert principal.subject_id == "orchestrator"
    assert principal.subject_type == "service"
    assert principal.tenant_id == _SYSTEM_TENANT
    assert principal.auth_method == "mtls"
    assert "service" in principal.roles


def test_unknown_subject_is_rejected() -> None:
    header = 'Subject="CN=evil,O=expert_work";Hash=abc'
    with pytest.raises(InvalidTokenError):
        _verifier().verify(header)


def test_empty_allowlist_blocks_everyone() -> None:
    """``allowed_subjects=[]`` is opt-in only — no service can authenticate."""
    header = 'Subject="CN=orchestrator,O=expert_work"'
    with pytest.raises(InvalidTokenError):
        _verifier(allowed=()).verify(header)  # the explicit empty allowlist is part of the test


def test_missing_cn_is_rejected() -> None:
    header = 'Subject="O=expert_work"'
    with pytest.raises(InvalidTokenError):
        _verifier().verify(header)


def test_empty_xfcc_is_rejected() -> None:
    with pytest.raises(InvalidTokenError):
        _verifier().verify("")


def test_require_uri_san_rejects_without_uri() -> None:
    header = 'Subject="CN=orchestrator,O=expert_work"'
    with pytest.raises(InvalidTokenError):
        _verifier(require_uri=True).verify(header)


def test_require_uri_san_accepts_with_uri() -> None:
    header = 'Subject="CN=orchestrator,O=expert_work";URI=spiffe://expert_work/orchestrator'
    principal = _verifier(require_uri=True).verify(header)
    assert principal.subject_id == "orchestrator"


def test_first_element_is_the_immediate_peer() -> None:
    """When proxies chain certs, the immediate peer is the first element."""
    header = (
        'Subject="CN=orchestrator,O=expert_work";Hash=aaa,Subject="CN=evil,O=expert_work";Hash=bbb'
    )
    principal = _verifier().verify(header)
    assert principal.subject_id == "orchestrator"
