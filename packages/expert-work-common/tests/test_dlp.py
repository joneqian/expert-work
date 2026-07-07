"""Tests for outbound DLP — Stream 7.4."""

from __future__ import annotations

import pytest

from expert_work.common.dlp import DLP_REPLACEMENT, DlpResult, scan_and_redact

# --- clean text passes through unchanged -----------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "",
        "The deploy finished at 14:00 with no errors.",
        "Order #1234 shipped to the warehouse.",  # 4 digits, not a card
        "Call extension 5551234 for support.",  # 7 digits, not a CN mobile
        "See https://example.com/guide for details.",
    ],
)
def test_clean_text_unchanged(text: str) -> None:
    result = scan_and_redact(text)
    assert result == DlpResult(redacted=text, categories=())
    assert result.changed is False


# --- each PII category is detected + redacted ------------------------------


def test_email_redacted() -> None:
    result = scan_and_redact("Contact me at alice@example.com please.")
    assert "alice@example.com" not in result.redacted
    assert DLP_REPLACEMENT in result.redacted
    assert result.categories == ("email",)


def test_cn_mobile_redacted() -> None:
    result = scan_and_redact("My number is 13912345678.")
    assert "13912345678" not in result.redacted
    assert result.categories == ("phone_cn",)


def test_cn_id_card_redacted() -> None:
    result = scan_and_redact("ID: 11010119900307123X on file.")
    assert "11010119900307123X" not in result.redacted
    assert result.categories == ("id_card_cn",)


def test_credit_card_redacted() -> None:
    result = scan_and_redact("Card 4111 1111 1111 1111 charged.")
    assert "4111" not in result.redacted
    assert result.categories == ("credit_card",)


def test_multiple_categories_all_fire() -> None:
    result = scan_and_redact("Reach alice@example.com or 13912345678.")
    assert set(result.categories) == {"email", "phone_cn"}
    assert "alice@example.com" not in result.redacted
    assert "13912345678" not in result.redacted


def test_matched_value_never_in_categories() -> None:
    """The verdict must carry only category names, never the secret value."""
    result = scan_and_redact("ssn-ish 11010119900307123X")
    assert all(c in {"email", "phone_cn", "id_card_cn", "credit_card"} for c in result.categories)
    assert "11010119900307123X" not in "".join(result.categories)


def test_changed_property_tracks_categories() -> None:
    assert scan_and_redact("alice@example.com").changed is True
    assert scan_and_redact("nothing here").changed is False
