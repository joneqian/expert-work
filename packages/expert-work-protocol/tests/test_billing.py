"""Validation + pricing-helper tests for ``protocol/billing.py`` — Stream Y-3."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from expert_work.protocol import (
    ModelRateCardPatch,
    ModelRateCardUpsert,
    TenantBillingLedgerRecord,
    apply_markup,
    provider_for_model,
)
from expert_work.protocol.billing import _build_model_provider_index
from expert_work.protocol.model_catalog import ModelEntry


def _upsert(**over: object) -> ModelRateCardUpsert:
    kwargs: dict[str, object] = {
        "provider": "anthropic",
        "model": "claude-opus-4-8",
        "input_per_mtok_micros": 15_000_000,
        "output_per_mtok_micros": 75_000_000,
    }
    kwargs.update(over)
    return ModelRateCardUpsert(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# apply_markup — integer math only
# ---------------------------------------------------------------------------


def test_apply_markup_twenty_percent() -> None:
    assert apply_markup(1_000_000, 2000) == 1_200_000


def test_apply_markup_zero() -> None:
    assert apply_markup(1_000_000, 0) == 1_000_000


def test_apply_markup_floor_division() -> None:
    # 7 * 2500 // 10000 = 17500 // 10000 = 1 (floor), so 7 + 1 = 8.
    assert apply_markup(7, 2500) == 8


# ---------------------------------------------------------------------------
# Upsert validation
# ---------------------------------------------------------------------------


def test_valid_upsert() -> None:
    row = _upsert()
    assert row.provider == "anthropic"
    assert row.cache_creation_per_mtok_micros == 0


def test_deprecated_model_allowed() -> None:
    # gpt-4o is deprecated in MODEL_CATALOG but must stay priceable.
    row = _upsert(provider="openai", model="gpt-4o")
    assert row.model == "gpt-4o"


def test_unknown_provider_rejected() -> None:
    with pytest.raises(ValidationError):
        _upsert(provider="nope")


def test_unknown_model_rejected() -> None:
    with pytest.raises(ValidationError):
        _upsert(model="not-a-model")


def test_negative_micros_rejected() -> None:
    with pytest.raises(ValidationError):
        _upsert(input_per_mtok_micros=-1)


# ---------------------------------------------------------------------------
# Patch
# ---------------------------------------------------------------------------


def test_patch_negative_rejected() -> None:
    with pytest.raises(ValidationError):
        ModelRateCardPatch(input_per_mtok_micros=-1)


def test_patch_partial_ok() -> None:
    patch = ModelRateCardPatch(input_per_mtok_micros=500_000)
    assert patch.input_per_mtok_micros == 500_000
    assert patch.output_per_mtok_micros is None


# ---------------------------------------------------------------------------
# provider_for_model — reverse MODEL_CATALOG lookup (Y4)
# ---------------------------------------------------------------------------


def test_provider_for_model_happy_path() -> None:
    assert provider_for_model("claude-opus-4-8") == "anthropic"


def test_provider_for_model_unknown_returns_none() -> None:
    assert provider_for_model("not-a-real-model") is None


def test_provider_for_model_includes_deprecated() -> None:
    # gpt-4o is deprecated but must still reverse-resolve to its provider.
    assert provider_for_model("gpt-4o") == "openai"


def test_build_index_marks_ambiguous_as_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """A model name registered under >1 provider maps to None (ambiguous)."""
    fake_catalog = {
        "anthropic": (ModelEntry(name="shared-model"),),
        "openai": (ModelEntry(name="shared-model"), ModelEntry(name="solo-model")),
    }
    monkeypatch.setattr("expert_work.protocol.billing.MODEL_CATALOG", fake_catalog)
    index = _build_model_provider_index()
    assert index["shared-model"] is None
    assert index["solo-model"] == "openai"


# ---------------------------------------------------------------------------
# TenantBillingLedgerRecord validation (Y4)
# ---------------------------------------------------------------------------


def _ledger(**over: object) -> TenantBillingLedgerRecord:
    now = datetime.now(UTC)
    kwargs: dict[str, object] = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "month": date(2026, 6, 1),
        "provider": "anthropic",
        "model": "claude-opus-4-8",
        "agent_name": "support",
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "base_cost_micros": 1000,
        "markup_cost_micros": 200,
        "billed_cost_micros": 1200,
        "priced": True,
        "rate_card_priced_at": now,
        "created_at": now,
        "updated_at": now,
    }
    kwargs.update(over)
    return TenantBillingLedgerRecord(**kwargs)  # type: ignore[arg-type]


def test_ledger_record_valid() -> None:
    rec = _ledger()
    assert rec.billed_cost_micros == 1200
    assert rec.priced is True


def test_ledger_record_rejects_negative_tokens() -> None:
    with pytest.raises(ValidationError):
        _ledger(input_tokens=-1)


def test_ledger_record_rejects_negative_cost() -> None:
    with pytest.raises(ValidationError):
        _ledger(base_cost_micros=-1)


def test_ledger_record_unpriced_zero_cost() -> None:
    rec = _ledger(
        provider="unknown",
        priced=False,
        base_cost_micros=0,
        markup_cost_micros=0,
        billed_cost_micros=0,
    )
    assert rec.priced is False
    assert rec.billed_cost_micros == 0
