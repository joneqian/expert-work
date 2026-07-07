"""``model_rate_card`` records — Stream Y (Mini-ADR Y-3) / 模型定价简化.

A **platform-curated** model pricing table: one **cost price** per
``(provider, model)``. Stream Y4 derives per-tenant cost from the G.9
``token_usage`` meter by pricing each usage row against the current rate.

Locked design (docs/design/rate-card-simplify-cny.md):

* **One row per ``(provider, model)``.** No plan-tier split, no temporal
  versioning — repricing edits the row in place (the rollup recomputes the
  month against the current price).
* **Integer micro-元 / 百万 tokens — NO floats anywhere.** A price field is
  micro-CNY per *million* tokens (UI shows 元/百万tokens, supports decimals;
  ``store = round(元 * 1_000_000)``). The rollup divides by 1_000_000 to reach
  the ledger's per-token micro-元 cost.
* **No markup here.** ``model_rate_card`` is the platform *cost* price only;
  the per-tenant sales markup lives at tenant scope (separate PR). ``billed``
  currently equals ``base``.

The table is platform-global (``tenant_id`` is ``None`` for now; the column is
kept so future per-tenant private rate cards are a non-migration change,
mirroring ``mcp_connector_catalog`` / ``encrypted_secret``).
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from expert_work.protocol.model_catalog import MODEL_CATALOG

__all__ = [
    "ModelRateCardPatch",
    "ModelRateCardRecord",
    "ModelRateCardUpsert",
    "TenantBillingLedgerRecord",
    "apply_markup",
    "provider_for_model",
]


def _build_model_provider_index() -> dict[str, str | None]:
    """Reverse :data:`MODEL_CATALOG` into ``model name → provider``.

    Built once at import time. A model name that appears under more than one
    provider maps to ``None`` (ambiguous) so the rollup refuses to guess; an
    unknown model is simply absent from the index. Deprecated entries are
    included so historical usage stays resolvable.
    """
    index: dict[str, str | None] = {}
    for provider, entries in MODEL_CATALOG.items():
        for entry in entries:
            if entry.name in index and index[entry.name] != provider:
                # Same model name under >1 provider → ambiguous, refuse to guess.
                index[entry.name] = None
            elif entry.name not in index:
                index[entry.name] = provider
    return index


# Module-level reverse index (model name → provider | None-for-ambiguous).
_MODEL_PROVIDER_INDEX: dict[str, str | None] = _build_model_provider_index()


def provider_for_model(model: str) -> str | None:
    """Reverse-look-up the provider that owns ``model``.

    Returns the provider iff the model name maps to **exactly one** provider.
    Returns ``None`` when the model is unknown OR ambiguous (the same name is
    registered under more than one provider). Used by the Y4 rollup only as a
    fallback when ``token_usage.provider`` is NULL (legacy rows).
    """
    return _MODEL_PROVIDER_INDEX.get(model)


def apply_markup(base_micros: int, markup_bps: int) -> int:
    """Apply a basis-point markup to a base micro-元 amount.

    Integer math only — floor division is the documented convention (no float,
    no banker's rounding). ``markup_bps`` is basis points: 2000 = +20 %.

    Retained for the per-tenant sales-markup PR; ``model_rate_card`` itself no
    longer carries a markup, so the rollup currently calls this with ``0``.

    >>> apply_markup(1_000_000, 2000)
    1200000
    """
    return base_micros + base_micros * markup_bps // 10_000


def _validate_provider_model(provider: str, model: str) -> None:
    """Reject ``(provider, model)`` not present in :data:`MODEL_CATALOG`.

    Membership allows **deprecated** entries so historical usage stays priceable
    (a retired model name on an old usage row must still resolve a rate).
    """
    # Iterate rather than ``MODEL_CATALOG.get(provider)``: the keys are the
    # ``Provider`` Literal, so ``.get(str)`` is a mypy call-overload error. The
    # catalog is ~10 providers, so the linear scan is irrelevant in practice.
    entries = next((v for k, v in MODEL_CATALOG.items() if k == provider), None)
    if entries is None:
        raise ValueError(f"unknown provider {provider!r}: not in MODEL_CATALOG")
    if not any(e.name == model for e in entries):
        raise ValueError(f"unknown model {model!r} for provider {provider!r}: not in MODEL_CATALOG")


class ModelRateCardRecord(BaseModel):
    """One row of ``model_rate_card`` as exposed across layers.

    No ``extra="forbid"``: materialized from a trusted DB row, not untrusted
    input.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    # NULL = platform-global (the only shape today). Kept so future per-tenant
    # private rate cards are a non-migration change.
    tenant_id: UUID | None = None
    provider: str
    model: str
    # micro-元 per *million* tokens (integer; UI shows 元/百万tokens).
    input_per_mtok_micros: int = Field(ge=0)
    output_per_mtok_micros: int = Field(ge=0)
    cache_creation_per_mtok_micros: int = Field(ge=0)
    cache_read_per_mtok_micros: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _validate(self) -> ModelRateCardRecord:
        _validate_provider_model(self.provider, self.model)
        return self


class ModelRateCardUpsert(BaseModel):
    """Create payload (system_admin ``POST /v1/platform/rate-card``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    model: str
    input_per_mtok_micros: int = Field(ge=0)
    output_per_mtok_micros: int = Field(ge=0)
    cache_creation_per_mtok_micros: int = Field(default=0, ge=0)
    cache_read_per_mtok_micros: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _validate(self) -> ModelRateCardUpsert:
        _validate_provider_model(self.provider, self.model)
        return self


class ModelRateCardPatch(BaseModel):
    """Partial update payload (``PATCH``). ``None`` = leave unchanged.

    ``provider`` / ``model`` are immutable post-create — they are the row's
    identity (one row per ``(provider, model)``). Reprice by editing the price
    fields in place.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_per_mtok_micros: int | None = Field(default=None, ge=0)
    output_per_mtok_micros: int | None = Field(default=None, ge=0)
    cache_creation_per_mtok_micros: int | None = Field(default=None, ge=0)
    cache_read_per_mtok_micros: int | None = Field(default=None, ge=0)


class TenantBillingLedgerRecord(BaseModel):
    """One derived per-tenant monthly billing bucket — Stream Y (Mini-ADR Y-4).

    Produced by the Y4 rollup job: ``token_usage`` rows for a tenant + month are
    priced by the rate effective at each row's ``observed_at`` and aggregated
    into ``(tenant, month, provider, model, agent_name)`` buckets. Pure
    derivation, so re-running the rollup overwrites (upsert) a month's buckets
    rather than double-counting.

    Cost is stored as the ``base`` / ``markup`` / ``billed`` split (integer
    micro-元): tenants see only ``billed_cost_micros`` (Stream Z exposure
    control), while ``base``/``markup`` are retained internally for system_admin
    chargeback (transparency decision 2). ``markup_cost_micros`` is always
    ``billed - base`` — never recomputed by division. Markup is currently 0
    (the per-tenant sales markup is a separate PR), so ``billed == base``.

    ``priced`` is ``False`` when the provider could not be derived (unknown /
    ambiguous model) or no rate matched; the token sums are still recorded but
    the cost fields are 0. Unpriced rows are bucketed under ``provider="unknown"``
    so they never pollute a priced bucket.

    No ``extra="forbid"``: materialized from a trusted DB row, not untrusted
    input.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: UUID
    # First-of-month convention (e.g. 2026-06-01 = June 2026).
    month: date
    provider: str
    model: str
    agent_name: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cache_creation_tokens: int = Field(ge=0)
    cache_read_tokens: int = Field(ge=0)
    base_cost_micros: int = Field(ge=0)
    markup_cost_micros: int = Field(ge=0)
    billed_cost_micros: int = Field(ge=0)
    priced: bool
    # When the rollup priced this bucket (audit — distinct from created/updated).
    rate_card_priced_at: datetime
    created_at: datetime
    updated_at: datetime
