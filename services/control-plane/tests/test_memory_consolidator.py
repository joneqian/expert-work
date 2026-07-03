"""Sprint #7 PR B — MemoryConsolidator worker.

Covers the 4 state-machine paths from Mini-ADRs U-34 / U-35 / U-36 / U-37:

1. cluster → consolidate (LLM keep=true)
2. cluster → reject_anti_mislearn (LLM keep=false, reject_reason set)
3. lone_item → purge (single review classifies noise)
4. lone_item → reviewed_durable (single review classifies durable)

Plus parser robustness on malformed JSON, prompt construction includes
all 6 anti-mislearn categories, null aux model returns valid-shape JSON
for both prompt families.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from control_plane.memory_consolidator import (
    ConsolidatorLLMReply,
    MemoryConsolidator,
    _parse_cluster_reply,
    _parse_single_reply,
    make_null_consolidator_aux_model,
)
from control_plane.tenancy import TenantConfigService
from helix_agent.persistence import InMemoryMemoryStore
from helix_agent.persistence.audit_log import InMemoryAuditLogStore
from helix_agent.persistence.memory.hash import hash_content
from helix_agent.persistence.tenant_config import InMemoryTenantConfigStore
from helix_agent.protocol import (
    AuditAction,
    AuditQuery,
    MemoryItem,
    StructuredOutputSpec,
    TenantConfigPatch,
)
from helix_agent.runtime.audit.fallback import InMemoryAuditFallbackQueue
from helix_agent.runtime.audit.logger import AuditLogger
from helix_agent.runtime.audit.redactor import DefaultSecretRedactor
from helix_agent.runtime.middleware import LLMOutputValidationError

_TENANT = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
_USER = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
_NOW = datetime.now(UTC)


def _build_logger() -> tuple[AuditLogger, InMemoryAuditLogStore]:
    store = InMemoryAuditLogStore()
    return (
        AuditLogger(
            store=store,
            redactor=DefaultSecretRedactor(),
            fallback=InMemoryAuditFallbackQueue(),
        ),
        store,
    )


class _ScriptedAuxModel:
    """Returns scripted replies. Tests inject the JSON they want."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[str] = []
        self.schemas: list[StructuredOutputSpec | None] = []

    async def __call__(
        self,
        *,
        prompt: str,
        model: str | None,
        tenant_id: UUID,
        output_schema: StructuredOutputSpec | None = None,
    ) -> ConsolidatorLLMReply:
        self.calls.append(prompt)
        self.schemas.append(output_schema)
        text = self._replies.pop(0) if self._replies else "{}"
        return ConsolidatorLLMReply(
            text=text, model=model or "fake", input_tokens=10, output_tokens=5
        )


class _ValidationExplodingAuxModel:
    """Simulates the RT-1 router exhausting its schema-validation retries."""

    async def __call__(
        self,
        *,
        prompt: str,
        model: str | None,
        tenant_id: UUID,
        output_schema: StructuredOutputSpec | None = None,
    ) -> ConsolidatorLLMReply:
        raise LLMOutputValidationError("still invalid after retries")


class _FakeEmbedder:
    async def embed_one(self, text: str, *, tenant_id: UUID) -> tuple[float, ...]:
        del tenant_id
        return (0.5, 0.5)


def _seed_transient(
    store: InMemoryMemoryStore,
    *,
    contents: Sequence[str],
    embedding: tuple[float, ...] = (1.0, 0.0),
    created_at: datetime | None = None,
    last_used_at: datetime | None = None,
    last_reviewed_at: datetime | None = None,
) -> list[UUID]:
    ids = []
    for content in contents:
        item = MemoryItem(
            id=uuid4(),
            tenant_id=_TENANT,
            user_id=_USER,
            kind="fact",
            content=content,
            embedding=embedding,
            content_hash=hash_content(content),
            created_at=created_at or _NOW,
            last_used_at=last_used_at or (created_at or _NOW),
            last_reviewed_at=last_reviewed_at,
        )
        store._rows.append(item)
        ids.append(item.id)
    return ids


async def _seed_tenant_config(service: TenantConfigService) -> None:
    await service.upsert(
        tenant_id=_TENANT,
        patch=TenantConfigPatch(display_name="Test Tenant"),
        actor_id="seed",
    )


async def _build_worker(
    aux_replies: list[str], *, interval_s: float = 60.0
) -> tuple[MemoryConsolidator, InMemoryMemoryStore, _ScriptedAuxModel, InMemoryAuditLogStore]:
    store = InMemoryMemoryStore()
    audit_logger, audit_store = _build_logger()
    config_service = TenantConfigService(
        store=InMemoryTenantConfigStore(),
        audit_logger=audit_logger,
    )
    aux = _ScriptedAuxModel(aux_replies)
    worker = MemoryConsolidator(
        memory_store=store,
        tenant_config_service=config_service,
        audit_logger=audit_logger,
        aux_model=aux,
        embedder=_FakeEmbedder(),
        interval_s=interval_s,
    )
    await _seed_tenant_config(config_service)
    return worker, store, aux, audit_store


# ─── Parser robustness ─────────────────────────────────────────────────


def _reply(text: str, parsed: dict[str, object] | None = None) -> ConsolidatorLLMReply:
    return ConsolidatorLLMReply(text=text, model="fake", parsed=parsed)


def test_parse_cluster_reply_keep_true() -> None:
    v = _parse_cluster_reply(
        _reply('{"keep": true, "summary": "user likes dark mode", "reject_reason": null}')
    )
    assert v is not None
    assert v.keep is True
    assert v.summary == "user likes dark mode"


def test_parse_cluster_reply_keep_false_anti_mislearn() -> None:
    v = _parse_cluster_reply(
        _reply('{"keep": false, "summary": null, "reject_reason": "anti_mislearn:env_failure"}')
    )
    assert v is not None
    assert v.keep is False
    assert v.reject_reason == "anti_mislearn:env_failure"


def test_parse_cluster_reply_invalid_reason_rejected() -> None:
    v = _parse_cluster_reply(_reply('{"keep": false, "summary": null, "reject_reason": "made_up"}'))
    assert v is None


def test_parse_cluster_reply_malformed_json() -> None:
    assert _parse_cluster_reply(_reply("not json")) is None
    assert _parse_cluster_reply(_reply("[]")) is None


def test_parse_cluster_reply_tolerates_missing_descriptive_fields() -> None:
    """Pre-RT-1 ``.get(...)`` pin — a keep verdict without reject_reason
    and a reject verdict without summary both stay valid verdicts."""
    keep = _parse_cluster_reply(_reply('{"keep": true, "summary": "likes dark mode"}'))
    assert keep is not None
    assert keep.keep is True
    reject = _parse_cluster_reply(_reply('{"keep": false, "reject_reason": "false_cluster"}'))
    assert reject is not None
    assert reject.reject_reason == "false_cluster"


def test_parse_cluster_reply_prefers_router_parsed_dict() -> None:
    """RT-1 — the router-validated ``parsed`` dict wins over raw text."""
    v = _parse_cluster_reply(
        _reply(
            "prose the tool_call path never produces",
            parsed={"keep": True, "summary": "user likes dark mode", "reject_reason": None},
        )
    )
    assert v is not None
    assert v.keep is True
    assert v.summary == "user likes dark mode"


def test_parse_cluster_reply_semantic_rules_apply_to_parsed_dict() -> None:
    """Schema-valid but semantically wrong (keep with empty summary) → None."""
    v = _parse_cluster_reply(
        _reply("{}", parsed={"keep": True, "summary": "  ", "reject_reason": None})
    )
    assert v is None


def test_parse_single_reply_durable() -> None:
    v = _parse_single_reply(_reply('{"is_noise": false, "category": "durable"}'))
    assert v is not None
    assert v.is_noise is False
    assert v.category == "durable"


def test_parse_single_reply_noise() -> None:
    v = _parse_single_reply(_reply('{"is_noise": true, "category": "env_failure"}'))
    assert v is not None
    assert v.is_noise is True
    assert v.category == "env_failure"


def test_parse_single_reply_invalid_category() -> None:
    v = _parse_single_reply(_reply('{"is_noise": true, "category": "made_up"}'))
    assert v is None


def test_parse_single_reply_prefers_router_parsed_dict() -> None:
    v = _parse_single_reply(_reply("garbage", parsed={"is_noise": True, "category": "time_bound"}))
    assert v is not None
    assert v.is_noise is True
    assert v.category == "time_bound"


# ─── Null aux model returns valid-shape JSON for both families ────────


@pytest.mark.asyncio
async def test_null_aux_model_cluster_path_parses() -> None:
    null = make_null_consolidator_aux_model()
    reply = await null(prompt='{"items":[]}', model=None, tenant_id=_TENANT)
    verdict = _parse_cluster_reply(reply)
    assert verdict is not None
    assert verdict.keep is False
    assert verdict.reject_reason == "false_cluster"


@pytest.mark.asyncio
async def test_null_aux_model_single_path_parses() -> None:
    null = make_null_consolidator_aux_model()
    reply = await null(prompt='{"is_noise": true}', model=None, tenant_id=_TENANT)
    verdict = _parse_single_reply(reply)
    assert verdict is not None
    assert verdict.is_noise is False
    assert verdict.category == "durable"


@pytest.mark.asyncio
async def test_null_aux_model_returns_parsed_for_structured_calls() -> None:
    """The null double mirrors the RT-1 contract when a schema is asked for."""
    null = make_null_consolidator_aux_model()
    spec = StructuredOutputSpec(schema={"type": "object"}, name="memory_cluster_verdict")
    reply = await null(prompt="cluster prompt", model=None, tenant_id=_TENANT, output_schema=spec)
    assert reply.parsed == {"keep": False, "summary": None, "reject_reason": "false_cluster"}
    plain = await null(prompt="cluster prompt", model=None, tenant_id=_TENANT)
    assert plain.parsed is None


# ─── Worker 4 paths ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cluster_consolidate_writes_parent_and_links_sources() -> None:
    worker, store, _aux, audit_store = await _build_worker(
        aux_replies=[
            # SUB-PASS 1 reply for the cluster
            '{"keep": true, "summary": "user prefers dark mode", "reject_reason": null}'
        ]
    )
    ids = _seed_transient(store, contents=["dark UI", "dark mode preference", "wants dark theme"])
    summary = await worker.run_once()
    assert summary.consolidated == 1
    assert summary.cluster_rejected == 0
    # Sources are now linked to a consolidated parent.
    rows_by_id = {r.id: r for r in store._rows}
    for sid in ids:
        assert rows_by_id[sid].consolidated_into is not None
    # Audit row emitted.
    page = await audit_store.query(AuditQuery(tenant_id="*", limit=100))
    actions = {entry.action for entry in page.entries}
    assert AuditAction.MEMORY_CONSOLIDATED in actions
    assert AuditAction.MEMORY_CONSOLIDATOR_RUN in actions


@pytest.mark.asyncio
async def test_cluster_reject_anti_mislearn_skips_write_and_audits() -> None:
    worker, store, _aux, audit_store = await _build_worker(
        aux_replies=[
            '{"keep": false, "summary": null, "reject_reason": "anti_mislearn:env_failure"}'
        ]
    )
    _seed_transient(store, contents=["GPG not installed", "missing GPG", "no gpg binary"])
    summary = await worker.run_once()
    assert summary.consolidated == 0
    assert summary.cluster_rejected == 1
    # No source is linked to a parent.
    for row in store._rows:
        assert row.consolidated_into is None
    page = await audit_store.query(AuditQuery(tenant_id="*", limit=100))
    actions = {entry.action for entry in page.entries}
    assert AuditAction.MEMORY_CONSOLIDATION_REJECTED in actions
    assert AuditAction.MEMORY_CONSOLIDATED not in actions


@pytest.mark.asyncio
async def test_lone_item_purge_classifies_noise_and_soft_deletes() -> None:
    # SUB-PASS 1 returns no clusters (single item, can't form cluster of 3);
    # SUB-PASS 2 reviews the lone item and classifies it noise.
    worker, store, _aux, audit_store = await _build_worker(
        aux_replies=[
            # SUB-PASS 2 — single-review noise verdict
            '{"is_noise": true, "category": "transient_error"}'
        ]
    )
    _seed_transient(
        store,
        contents=["one-off timeout error from yesterday"],
        created_at=_NOW - timedelta(days=60),
    )
    summary = await worker.run_once()
    assert summary.purged == 1
    assert summary.reviewed_durable == 0
    # Item now soft-deleted.
    assert store._rows[0].deleted_at is not None
    page = await audit_store.query(AuditQuery(tenant_id="*", limit=100))
    actions = {entry.action for entry in page.entries}
    assert AuditAction.MEMORY_PURGED_AS_NOISE in actions


@pytest.mark.asyncio
async def test_lone_item_reviewed_durable_stamps_last_reviewed_at() -> None:
    worker, store, _aux, audit_store = await _build_worker(
        aux_replies=['{"is_noise": false, "category": "durable"}']
    )
    _seed_transient(
        store,
        contents=["user works at example.com"],
        created_at=_NOW - timedelta(days=60),
    )
    summary = await worker.run_once()
    assert summary.purged == 0
    assert summary.reviewed_durable == 1
    assert store._rows[0].deleted_at is None
    assert store._rows[0].last_reviewed_at is not None
    page = await audit_store.query(AuditQuery(tenant_id="*", limit=100))
    actions = {entry.action for entry in page.entries}
    assert AuditAction.MEMORY_REVIEWED_DURABLE in actions


# ─── RT-1 PR-2: structured output through the aux model ───────────────


@pytest.mark.asyncio
async def test_cluster_call_carries_output_schema() -> None:
    worker, store, aux, _audit_store = await _build_worker(
        aux_replies=['{"keep": true, "summary": "user prefers dark mode", "reject_reason": null}']
    )
    _seed_transient(store, contents=["dark UI", "dark mode preference", "wants dark theme"])
    await worker.run_once()
    assert len(aux.schemas) == 1
    spec = aux.schemas[0]
    assert spec is not None
    assert spec.name == "memory_cluster_verdict"
    # Only the decision field is required; summary/reject_reason keep
    # their pre-RT-1 ``.get(...)`` optionality (model defaults).
    assert set(spec.schema["required"]) == {"keep"}
    assert set(spec.schema["properties"]) == {"keep", "summary", "reject_reason"}


@pytest.mark.asyncio
async def test_single_review_call_carries_output_schema() -> None:
    worker, store, aux, _audit_store = await _build_worker(
        aux_replies=['{"is_noise": false, "category": "durable"}']
    )
    _seed_transient(
        store,
        contents=["user works at example.com"],
        created_at=_NOW - timedelta(days=60),
    )
    await worker.run_once()
    assert len(aux.schemas) == 1
    spec = aux.schemas[0]
    assert spec is not None
    assert spec.name == "memory_review_verdict"
    assert set(spec.schema["required"]) == {"is_noise", "category"}


@pytest.mark.asyncio
async def test_cluster_validation_exhausted_degrades_to_false_cluster() -> None:
    """RT-ADR-3 — LLMOutputValidationError == malformed reply: the
    cluster is skipped as false_cluster, never counted as a worker error."""
    store = InMemoryMemoryStore()
    audit_logger, _audit_store = _build_logger()
    config_service = TenantConfigService(
        store=InMemoryTenantConfigStore(), audit_logger=audit_logger
    )
    worker = MemoryConsolidator(
        memory_store=store,
        tenant_config_service=config_service,
        audit_logger=audit_logger,
        aux_model=_ValidationExplodingAuxModel(),
        embedder=_FakeEmbedder(),
        interval_s=60.0,
    )
    await _seed_tenant_config(config_service)
    _seed_transient(store, contents=["dark UI", "dark mode preference", "wants dark theme"])
    summary = await worker.run_once()
    assert summary.consolidated == 0
    assert summary.cluster_rejected == 1
    assert summary.errors == 0
    for row in store._rows:
        assert row.consolidated_into is None


@pytest.mark.asyncio
async def test_review_validation_exhausted_marks_reviewed_durable() -> None:
    """RT-ADR-3 — same conservative degrade as a malformed review reply:
    keep the item, stamp last_reviewed_at so it is not re-reviewed."""
    store = InMemoryMemoryStore()
    audit_logger, _audit_store = _build_logger()
    config_service = TenantConfigService(
        store=InMemoryTenantConfigStore(), audit_logger=audit_logger
    )
    worker = MemoryConsolidator(
        memory_store=store,
        tenant_config_service=config_service,
        audit_logger=audit_logger,
        aux_model=_ValidationExplodingAuxModel(),
        embedder=_FakeEmbedder(),
        interval_s=60.0,
    )
    await _seed_tenant_config(config_service)
    _seed_transient(
        store,
        contents=["one-off timeout error from yesterday"],
        created_at=_NOW - timedelta(days=60),
    )
    summary = await worker.run_once()
    assert summary.purged == 0
    assert summary.reviewed_durable == 1
    assert summary.errors == 0
    assert store._rows[0].deleted_at is None
    assert store._rows[0].last_reviewed_at is not None


# ─── Idempotency ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_already_reviewed_item_skipped_next_tick() -> None:
    # Pre-stamp last_reviewed_at so the second tick doesn't re-review.
    worker, store, aux, _audit_store = await _build_worker(aux_replies=[])
    _seed_transient(
        store,
        contents=["already reviewed"],
        created_at=_NOW - timedelta(days=60),
        last_reviewed_at=_NOW - timedelta(days=1),
    )
    summary = await worker.run_once()
    # No LLM call, no purge.
    assert aux.calls == []
    assert summary.purged == 0
    assert summary.reviewed_durable == 0


@pytest.mark.asyncio
async def test_purge_disabled_by_tenant_config_skips_sub_pass_2() -> None:
    store = InMemoryMemoryStore()
    audit_logger, _ = _build_logger()
    config_service = TenantConfigService(
        store=InMemoryTenantConfigStore(),
        audit_logger=audit_logger,
    )
    aux = _ScriptedAuxModel(replies=[])
    worker = MemoryConsolidator(
        memory_store=store,
        tenant_config_service=config_service,
        audit_logger=audit_logger,
        aux_model=aux,
        embedder=_FakeEmbedder(),
        interval_s=60.0,
    )
    await config_service.upsert(
        tenant_id=_TENANT,
        patch=TenantConfigPatch(
            display_name="Test",
            memory_purge_enabled=False,
        ),
        actor_id="seed",
    )
    _seed_transient(
        store,
        contents=["aged lone fact"],
        created_at=_NOW - timedelta(days=60),
    )
    summary = await worker.run_once()
    # SUB-PASS 2 skipped entirely; aux not called.
    assert aux.calls == []
    assert summary.purged == 0


# ─── Stream HX-2 (Mini-ADR HX-B3) — SUB-PASS 2a: 👎-flagged review ──────


@pytest.mark.asyncio
async def test_flagged_item_reviewed_regardless_of_age_and_purge_config() -> None:
    """A fresh, retrieved, previously-reviewed item — none of the U-37
    purge filters match — still gets reviewed once flagged, even with
    tenant purge disabled. Durable verdict clears the flag."""
    worker, store, _aux, _audit = await _build_worker(
        aux_replies=['{"is_noise": false, "category": "durable"}']
    )
    # Tenant opts out of background purging — the flagged path must not care.
    await worker._tenant_config.upsert(
        tenant_id=_TENANT,
        patch=TenantConfigPatch(memory_purge_enabled=False),
        actor_id="seed",
    )
    _seed_transient(store, contents=["fresh fact the user disputed"], created_at=_NOW)
    store._rows[0] = store._rows[0].model_copy(
        update={"source_thread_id": "thread-q", "last_reviewed_at": _NOW}
    )
    flagged = await store.flag_for_review(
        tenant_id=_TENANT, user_id=_USER, source_thread_id="thread-q"
    )

    summary = await worker.run_once()

    assert flagged == 1
    assert summary.reviewed_durable == 1
    assert store._rows[0].review_flagged_at is None  # flag consumed
    assert store._rows[0].last_reviewed_at is not None


@pytest.mark.asyncio
async def test_flagged_noise_item_is_soft_deleted() -> None:
    worker, store, _aux, _audit = await _build_worker(
        aux_replies=['{"is_noise": true, "category": "one_off_narrative"}']
    )
    _seed_transient(store, contents=["wrong fact the user disputed"], created_at=_NOW)
    item = store._rows[0]
    store._rows[0] = item.model_copy(update={"source_thread_id": "thread-z"})
    await store.flag_for_review(tenant_id=_TENANT, user_id=_USER, source_thread_id="thread-z")

    summary = await worker.run_once()

    assert summary.purged == 1
    assert store._rows[0].deleted_at is not None


@pytest.mark.asyncio
async def test_aux_calls_write_token_usage_rows() -> None:
    """Chargeback — every consolidator aux call lands one token_usage row
    with usage_kind='memory_consolidation' and tenant/user attribution
    (the other half of the SE-A43 aux-metering gap)."""
    from helix_agent.persistence.token_usage_store import InMemoryTokenUsageStore

    usage_store = InMemoryTokenUsageStore()
    store = InMemoryMemoryStore()
    audit_logger, _ = _build_logger()
    config_service = TenantConfigService(
        store=InMemoryTenantConfigStore(),
        audit_logger=audit_logger,
    )
    worker = MemoryConsolidator(
        memory_store=store,
        tenant_config_service=config_service,
        audit_logger=audit_logger,
        aux_model=_ScriptedAuxModel(
            ['{"keep": true, "summary": "user prefers dark mode", "reject_reason": null}']
        ),
        embedder=_FakeEmbedder(),
        interval_s=60.0,
        usage_store=usage_store,
    )
    await _seed_tenant_config(config_service)
    _seed_transient(store, contents=["dark UI", "dark mode preference", "wants dark theme"])

    summary = await worker.run_once()
    assert summary.consolidated == 1

    rows = usage_store.rows if hasattr(usage_store, "rows") else usage_store._rows
    assert len(rows) == 1
    row = rows[0]
    assert row.usage_kind == "memory_consolidation"
    assert row.tenant_id == _TENANT
    assert row.user_id == _USER
    assert row.agent_name == "memory-consolidator"
    assert row.input_tokens >= 0
