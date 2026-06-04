"""``model_rate_card`` records — Stream Y (Mini-ADR Y-3).

A **platform-curated** model rate card: per-``(provider, model)`` token prices
(in integer **micro-USD**) plus a platform markup (basis points), with an
optional per-``plan_tier`` override and temporal versioning. Stream Y4 derives
per-tenant cost from the G.9 ``token_usage`` meter by resolving the rate that
was in effect when each usage row was observed.

Locked design (STREAM-Y-DESIGN § Y-3):

* **Markup = per-model-row + tier override.** A row is keyed by
  ``(provider, model, plan_tier)`` where ``plan_tier`` is nullable (NULL =
  generic, applies to any tier). Resolution is most-specific-wins:
  ``(provider, model, <tenant's tier>)`` beats ``(provider, model, NULL)``.
* **Integer micro-USD only — NO floats anywhere.** ``markup_bps`` is basis
  points (2000 = +20 %).
* **Temporal versioning.** ``effective_from`` (required) + ``effective_until``
  (nullable = open-ended). A price applies to a usage row observed at time ``t``
  iff ``effective_from <= t < effective_until`` (or ``effective_until`` is
  ``None``). Repricing never mutates a past row — insert a new row with a new
  ``effective_from``.

The table is platform-global (``tenant_id`` is ``None`` for now; the column is
kept so future per-tenant private rate cards are a non-migration change,
mirroring ``mcp_connector_catalog`` / ``encrypted_secret``).
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from helix_agent.protocol.model_catalog import MODEL_CATALOG
from helix_agent.protocol.tenant_config import TenantPlan

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
    """Apply a basis-point markup to a base micro-USD amount.

    Integer math only — floor division is the documented convention (no float,
    no banker's rounding). ``markup_bps`` is basis points: 2000 = +20 %.

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
    input_token_micros: int = Field(ge=0)
    output_token_micros: int = Field(ge=0)
    cache_creation_token_micros: int = Field(ge=0)
    cache_read_token_micros: int = Field(ge=0)
    markup_bps: int = Field(ge=0)
    plan_tier: TenantPlan | None = None
    effective_from: datetime
    effective_until: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _validate(self) -> ModelRateCardRecord:
        _validate_provider_model(self.provider, self.model)
        if self.effective_until is not None and self.effective_until <= self.effective_from:
            raise ValueError("effective_until must be > effective_from")
        return self


class ModelRateCardUpsert(BaseModel):
    """Create payload (system_admin ``POST /v1/platform/rate-card``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    model: str
    input_token_micros: int = Field(ge=0)
    output_token_micros: int = Field(ge=0)
    cache_creation_token_micros: int = Field(default=0, ge=0)
    cache_read_token_micros: int = Field(default=0, ge=0)
    markup_bps: int = Field(default=0, ge=0)
    plan_tier: TenantPlan | None = None
    effective_from: datetime
    effective_until: datetime | None = None

    @model_validator(mode="after")
    def _validate(self) -> ModelRateCardUpsert:
        _validate_provider_model(self.provider, self.model)
        if self.effective_until is not None and self.effective_until <= self.effective_from:
            raise ValueError("effective_until must be > effective_from")
        return self


class ModelRateCardPatch(BaseModel):
    """Partial update payload (``PATCH``). ``None`` = leave unchanged.

    ``provider`` / ``model`` / ``plan_tier`` / ``effective_from`` are immutable
    post-create — they form the row's temporal+specificity identity. Reprice by
    inserting a new row (a new ``effective_from``), never by mutating these.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_token_micros: int | None = Field(default=None, ge=0)
    output_token_micros: int | None = Field(default=None, ge=0)
    cache_creation_token_micros: int | None = Field(default=None, ge=0)
    cache_read_token_micros: int | None = Field(default=None, ge=0)
    markup_bps: int | None = Field(default=None, ge=0)
    effective_until: datetime | None = None


class TenantBillingLedgerRecord(BaseModel):
    """One derived per-tenant monthly billing bucket — Stream Y (Mini-ADR Y-4).

    Produced by the Y4 rollup job: ``token_usage`` rows for a tenant + month are
    priced by the rate effective at each row's ``observed_at`` and aggregated
    into ``(tenant, month, provider, model, agent_name)`` buckets. Pure
    derivation, so re-running the rollup overwrites (upsert) a month's buckets
    rather than double-counting.

    Cost is stored as the ``base`` / ``markup`` / ``billed`` split (integer
    micro-USD): tenants see only ``billed_cost_micros`` (Stream Z exposure
    control), while ``base``/``markup`` are retained internally for system_admin
    chargeback (transparency decision 2). ``markup_cost_micros`` is always
    ``billed - base`` — never recomputed by division.

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
