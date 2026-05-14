"""Unit tests for :class:`DefaultSecretRedactor` + :class:`TenantAwareRedactor`."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID, uuid4

import pytest

from helix_agent.runtime.audit import (
    PII_FIELD_HIT,
    REPLACEMENT,
    DefaultSecretRedactor,
    TenantAwareRedactor,
)

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
