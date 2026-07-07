"""Unit tests for :class:`DefaultSecretRedactor` + :class:`TenantAwareRedactor`."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID, uuid4

import pytest

from expert_work.runtime.audit import (
    PII_FIELD_HIT,
    REPLACEMENT,
    DefaultSecretRedactor,
    TenantAwareRedactor,
)
from expert_work.runtime.audit.redactor import DEFAULT_PATTERNS, PII_PATTERNS

_T = uuid4()


# ---------- DefaultSecretRedactor (global patterns) ----------


@pytest.mark.asyncio
async def test_passes_through_when_no_secrets() -> None:
    redactor = DefaultSecretRedactor()
    result = await redactor.redact(
        tenant_id=_T,
        details={"action": "manifest:write", "lines_added": 42},
    )

    assert result.redacted == {"action": "manifest:write", "lines_added": 42}
    assert result.hits == {}


@pytest.mark.asyncio
async def test_masks_openai_key() -> None:
    redactor = DefaultSecretRedactor()
    result = await redactor.redact(
        tenant_id=_T,
        details={"prompt": "use key sk-ABCDEFGHIJKLMNOPQRSTUVWX for this"},
    )

    assert REPLACEMENT in result.redacted["prompt"]
    assert "sk-ABCDEFGHIJKLMNOPQRSTUVWX" not in result.redacted["prompt"]
    assert result.hits == {"openai_key": 1}


def test_redact_text_masks_secrets_in_a_string() -> None:
    """The text entry point used by the E.5 PII middleware."""
    redactor = DefaultSecretRedactor()
    out = redactor.redact_text("use key sk-ABCDEFGHIJKLMNOPQRSTUVWX now")
    assert REPLACEMENT in out
    assert "sk-ABCDEFGHIJKLMNOPQRSTUVWX" not in out


def test_redact_text_passes_clean_string_through() -> None:
    redactor = DefaultSecretRedactor()
    assert redactor.redact_text("just a normal sentence") == "just a normal sentence"


@pytest.mark.asyncio
async def test_masks_jwt_three_segment() -> None:
    redactor = DefaultSecretRedactor()
    jwt = (
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIiwibmFtZSI6IkFsaWNlIn0"
        ".dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    )
    result = await redactor.redact(
        tenant_id=_T,
        details={"authorization": f"Bearer {jwt}"},
    )

    assert jwt not in result.redacted["authorization"]
    assert result.hits == {"jwt": 1}


@pytest.mark.asyncio
async def test_masks_bcrypt() -> None:
    redactor = DefaultSecretRedactor()
    bcrypt_hash = "$2a$12$R9h/cIPz0gi.URNNX3kh2OPST9/PgBkqquzi.Ss7KIUgO2t0jWMUW"
    result = await redactor.redact(
        tenant_id=_T,
        details={"hashed_password": bcrypt_hash},
    )

    assert bcrypt_hash not in result.redacted["hashed_password"]
    assert result.hits == {"bcrypt": 1}


@pytest.mark.asyncio
async def test_masks_pem_private_key_header() -> None:
    redactor = DefaultSecretRedactor()
    # Construct the header at runtime so the literal doesn't trip git/pre-commit
    # secret scanners on this test file itself.
    pem_header = "-" * 5 + "BEGIN " + "RSA PRIVATE KEY" + "-" * 5
    pem = pem_header + "\nMIIEogIBA..."
    result = await redactor.redact(tenant_id=_T, details={"key": pem})

    assert pem_header not in result.redacted["key"]
    assert result.hits == {"pem_private_key": 1}


@pytest.mark.asyncio
async def test_walks_nested_dicts_and_lists() -> None:
    redactor = DefaultSecretRedactor()
    payload = {
        "request": {
            "headers": {"authorization": "Bearer sk-ABCDEFGHIJKLMNOPQRSTUVWX"},
            "args": ["sk-ZZZZZZZZZZZZZZZZZZZZ", 42],
        }
    }
    result = await redactor.redact(tenant_id=_T, details=payload)

    assert (
        "sk-ABCDEFGHIJKLMNOPQRSTUVWX" not in result.redacted["request"]["headers"]["authorization"]
    )
    assert "sk-ZZZZZZZZZZZZZZZZZZZZ" not in result.redacted["request"]["args"][0]
    assert result.redacted["request"]["args"][1] == 42
    assert result.hits["openai_key"] == 2


@pytest.mark.asyncio
async def test_does_not_mutate_input() -> None:
    redactor = DefaultSecretRedactor()
    original = {"prompt": "sk-ABCDEFGHIJKLMNOPQRSTUVWX"}
    await redactor.redact(tenant_id=_T, details=original)

    # Input must be untouched (immutability rule).
    assert original == {"prompt": "sk-ABCDEFGHIJKLMNOPQRSTUVWX"}


@pytest.mark.asyncio
async def test_counts_multiple_hits_in_same_string() -> None:
    redactor = DefaultSecretRedactor()
    s = "sk-ABCDEFGHIJKLMNOPQRSTUVWX and sk-ZZZZZZZZZZZZZZZZZZZZ"
    result = await redactor.redact(tenant_id=_T, details={"prompts": [s]})

    assert result.hits == {"openai_key": 2}


@pytest.mark.asyncio
async def test_anthropic_pat_pattern() -> None:
    redactor = DefaultSecretRedactor()
    pat = "aforge_pat_abcDEF123_xyz"
    result = await redactor.redact(tenant_id=_T, details={"token": pat})

    assert pat not in result.redacted["token"]
    assert result.hits == {"anthropic_pat": 1}


# ---------- TenantAwareRedactor (per-tenant pii_fields) ----------


def _static_resolver(fields: Sequence[str]) -> object:
    async def resolve(_tenant_id: UUID) -> Sequence[str]:
        return list(fields)

    return resolve


@pytest.mark.asyncio
async def test_tenant_aware_masks_configured_key() -> None:
    redactor = TenantAwareRedactor(
        global_redactor=DefaultSecretRedactor(),
        pii_fields_resolver=_static_resolver(["ssn"]),  # type: ignore[arg-type]
    )

    result = await redactor.redact(
        tenant_id=_T,
        details={"ssn": "123-45-6789", "x": 1},
    )
    assert result.redacted == {"ssn": REPLACEMENT, "x": 1}
    assert result.hits == {PII_FIELD_HIT: 1}


@pytest.mark.asyncio
async def test_tenant_aware_key_match_is_case_insensitive() -> None:
    redactor = TenantAwareRedactor(
        global_redactor=DefaultSecretRedactor(),
        pii_fields_resolver=_static_resolver(["ssn"]),  # type: ignore[arg-type]
    )

    result = await redactor.redact(
        tenant_id=_T,
        details={"SSN": "123-45-6789"},
    )
    assert result.redacted == {"SSN": REPLACEMENT}
    assert result.hits == {PII_FIELD_HIT: 1}


@pytest.mark.asyncio
async def test_tenant_aware_recurses_into_nested_structures() -> None:
    redactor = TenantAwareRedactor(
        global_redactor=DefaultSecretRedactor(),
        pii_fields_resolver=_static_resolver(["patient_id_card"]),  # type: ignore[arg-type]
    )

    result = await redactor.redact(
        tenant_id=_T,
        details={
            "request": {
                "body": {"patient_id_card": "11010120010101001X"},
                "items": [{"patient_id_card": "11010120010101001Y"}],
            }
        },
    )
    body = result.redacted["request"]["body"]
    items = result.redacted["request"]["items"]
    assert body["patient_id_card"] == REPLACEMENT
    assert items[0]["patient_id_card"] == REPLACEMENT
    assert result.hits == {PII_FIELD_HIT: 2}


@pytest.mark.asyncio
async def test_tenant_aware_combines_global_and_pii_hits() -> None:
    redactor = TenantAwareRedactor(
        global_redactor=DefaultSecretRedactor(),
        pii_fields_resolver=_static_resolver(["ssn"]),  # type: ignore[arg-type]
    )

    result = await redactor.redact(
        tenant_id=_T,
        details={
            "ssn": "123-45-6789",
            "prompt": "use key sk-ABCDEFGHIJKLMNOPQRSTUVWX",
        },
    )
    assert result.redacted["ssn"] == REPLACEMENT
    assert "sk-ABCDEFGHIJKLMNOPQRSTUVWX" not in result.redacted["prompt"]
    assert result.hits == {"openai_key": 1, PII_FIELD_HIT: 1}


@pytest.mark.asyncio
async def test_tenant_aware_no_pii_fields_returns_global_result() -> None:
    """Empty tenant pii_fields → identical to global-only run."""
    redactor = TenantAwareRedactor(
        global_redactor=DefaultSecretRedactor(),
        pii_fields_resolver=_static_resolver([]),  # type: ignore[arg-type]
    )

    result = await redactor.redact(
        tenant_id=_T,
        details={"ssn": "123-45-6789", "x": 1},
    )
    # ssn isn't a global pattern; the tenant didn't list it → kept.
    assert result.redacted == {"ssn": "123-45-6789", "x": 1}
    assert result.hits == {}


@pytest.mark.asyncio
async def test_tenant_aware_resolver_failure_falls_back_to_global_only() -> None:
    """Resolver errors must never block the audit path."""

    async def boom(_t: UUID) -> Sequence[str]:
        msg = "tenant_config service down"
        raise RuntimeError(msg)

    redactor = TenantAwareRedactor(
        global_redactor=DefaultSecretRedactor(),
        pii_fields_resolver=boom,
    )

    result = await redactor.redact(
        tenant_id=_T,
        details={
            "ssn": "123-45-6789",
            "prompt": "key sk-ABCDEFGHIJKLMNOPQRSTUVWX",
        },
    )
    # Global pattern still ran.
    assert "sk-ABCDEFGHIJKLMNOPQRSTUVWX" not in result.redacted["prompt"]
    # Per-tenant masking skipped — ssn passes through.
    assert result.redacted["ssn"] == "123-45-6789"
    assert result.hits == {"openai_key": 1}


@pytest.mark.asyncio
async def test_tenant_aware_does_not_mutate_input() -> None:
    redactor = TenantAwareRedactor(
        global_redactor=DefaultSecretRedactor(),
        pii_fields_resolver=_static_resolver(["ssn"]),  # type: ignore[arg-type]
    )

    original = {"ssn": "123-45-6789", "nested": {"ssn": "X"}}
    await redactor.redact(tenant_id=_T, details=original)

    assert original == {"ssn": "123-45-6789", "nested": {"ssn": "X"}}


# ---------- PII_PATTERNS (conversational PII — Mini-ADR OBS-L1) ----------
#
# Separate from DEFAULT_PATTERNS (secrets): only the Langfuse mask uses the
# union; the audit path stays secrets-only (surgical). These patterns are
# regex heuristics — the decision-2 "regex 扩展起步" tier, not NER.

_PII_REDACTOR = DefaultSecretRedactor(patterns={**DEFAULT_PATTERNS, **PII_PATTERNS})


def test_pii_patterns_are_separate_from_default_secret_patterns() -> None:
    """A bare DefaultSecretRedactor (audit path) must NOT mask conversational
    PII — that behaviour is opt-in via the PII_PATTERNS union only."""
    audit = DefaultSecretRedactor()
    assert audit.redact_text("reach me at alice@example.com") == "reach me at alice@example.com"


def test_pii_mask_email() -> None:
    out = _PII_REDACTOR.redact_text("reach me at alice@example.com please")
    assert "alice@example.com" not in out
    assert REPLACEMENT in out


def test_pii_mask_cn_mobile() -> None:
    out = _PII_REDACTOR.redact_text("我的手机是 13812345678 打我")
    assert "13812345678" not in out
    assert REPLACEMENT in out


def test_pii_mask_cn_id_card() -> None:
    out = _PII_REDACTOR.redact_text("身份证 11010119900307123X 登记")
    assert "11010119900307123X" not in out
    assert REPLACEMENT in out


def test_pii_mask_credit_card() -> None:
    out = _PII_REDACTOR.redact_text("card 4111 1111 1111 1111 expires soon")
    assert "4111 1111 1111 1111" not in out
    assert REPLACEMENT in out


def test_pii_mask_leaves_clean_text_untouched() -> None:
    text = "the agent planned three steps and finished"
    assert _PII_REDACTOR.redact_text(text) == text


# ---------- redact_tree (structured recursion — the Langfuse mask seam) ----------


def test_redact_tree_walks_nested_messages() -> None:
    """redact_tree generalises redact_text to the nested input/output shapes
    Langfuse passes (a list of message dicts)."""
    data = {
        "messages": [
            {"role": "user", "content": "email alice@example.com about it"},
            {"role": "assistant", "content": "sure, no PII here"},
        ],
        "model": "qwen-max",
    }
    out = _PII_REDACTOR.redact_tree(data)

    assert "alice@example.com" not in out["messages"][0]["content"]
    assert REPLACEMENT in out["messages"][0]["content"]
    # Clean leaves + non-string leaves pass through untouched.
    assert out["messages"][1]["content"] == "sure, no PII here"
    assert out["model"] == "qwen-max"


def test_redact_tree_handles_bare_string() -> None:
    out = _PII_REDACTOR.redact_tree("completion mentioning 13812345678")
    assert "13812345678" not in out
    assert REPLACEMENT in out


def test_redact_tree_does_not_mutate_input() -> None:
    original = {"content": "id 11010119900307123X"}
    _PII_REDACTOR.redact_tree(original)
    assert original == {"content": "id 11010119900307123X"}


def test_redact_tree_passes_non_string_leaves() -> None:
    data = {"tokens": 42, "ok": True, "ratio": 0.5, "none": None}
    assert _PII_REDACTOR.redact_tree(data) == data
