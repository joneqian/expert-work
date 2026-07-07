"""Tests for platform secret-ref records + ref validator — Stream P (P-8)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from expert_work.protocol import (
    PlatformProviderSecretRecord,
    PlatformSecretUpsert,
    validate_secret_ref,
)


def _now() -> datetime:
    return datetime(2026, 5, 29, tzinfo=UTC)


@pytest.mark.parametrize("ref", ["secret://anthropic", "kms://platform/anthropic-key"])
def test_validate_secret_ref_accepts_refs(ref: str) -> None:
    assert validate_secret_ref(ref) == ref


@pytest.mark.parametrize("bad", ["sk-ant-plaintext", "anthropic-key", "https://x", ""])
def test_validate_secret_ref_rejects_plaintext(bad: str) -> None:
    with pytest.raises(ValueError, match="secret:// or kms://"):
        validate_secret_ref(bad)


def test_record_rejects_plaintext_secret_ref() -> None:
    with pytest.raises(ValidationError):
        PlatformProviderSecretRecord(
            provider="anthropic",
            secret_ref="sk-ant-plaintext",
            enabled=True,
            created_at=_now(),
            updated_at=_now(),
            updated_by="admin",
        )


def test_upsert_payload_validates_and_defaults_enabled() -> None:
    payload = PlatformSecretUpsert(secret_ref="kms://x")
    assert payload.enabled is True
    with pytest.raises(ValidationError):
        PlatformSecretUpsert(secret_ref="plaintext")
