"""Unit tests for the opaque cursor codec."""

from __future__ import annotations

import pytest

from expert_work.persistence.audit_log.cursor import decode_cursor, encode_cursor


def test_round_trip() -> None:
    for audit_id in (1, 42, 999, 2**31, 2**63 - 1):
        assert decode_cursor(encode_cursor(audit_id)) == audit_id


def test_encoded_form_is_url_safe_no_padding() -> None:
    encoded = encode_cursor(123456789)
    assert "=" not in encoded
    assert "/" not in encoded
    assert "+" not in encoded


def test_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="malformed cursor"):
        decode_cursor("***not-base64***")


def test_rejects_unknown_format() -> None:
    # Base64 of "v999:42" — valid base64, unknown format prefix.
    import base64

    bad = base64.urlsafe_b64encode(b"v999:42").decode("ascii").rstrip("=")
    with pytest.raises(ValueError, match="unknown cursor format"):
        decode_cursor(bad)


def test_rejects_non_integer_payload() -> None:
    import base64

    bad = base64.urlsafe_b64encode(b"v1:not-a-number").decode("ascii").rstrip("=")
    with pytest.raises(ValueError, match="not an integer"):
        decode_cursor(bad)
