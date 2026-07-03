"""SE-16 (SE-A43) — evolution aux calls write real ``token_usage`` rows.

Covers the metering scope semantics and the wiring-level aux adapters:
inside a candidate's scope every aux call lands one row with the true
tenant, the distilled agent's name, an evolution-scoped trace_id and
``usage_kind='skill_evolution'``; outside a scope nothing is recorded.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from control_plane.memory_consolidator import ConsolidatorLLMReply
from control_plane.skill_evolution_metering import current_metering, metering_scope
from control_plane.skill_evolution_wiring import _AuxJudge, _AuxText
from helix_agent.persistence.token_usage_store import InMemoryTokenUsageStore
from helix_agent.protocol import CurationCandidateRecord, StructuredOutputSpec

_NOW = datetime(2026, 7, 3, 9, 0, 0, tzinfo=UTC)


class _FakeAux:
    def __init__(self, text: str = "5") -> None:
        self.text = text
        self.tenants: list[UUID] = []

    async def __call__(
        self,
        *,
        prompt: str,
        model: str | None,
        tenant_id: UUID,
        output_schema: StructuredOutputSpec | None = None,
    ) -> ConsolidatorLLMReply:
        self.tenants.append(tenant_id)
        return ConsolidatorLLMReply(text=self.text, model="aux-m", input_tokens=7, output_tokens=3)


def _candidate(*, tenant: UUID) -> CurationCandidateRecord:
    return CurationCandidateRecord(
        id=uuid4(),
        tenant_id=tenant,
        agent_name="reporter",
        agent_version="2.0.0",
        thread_id=uuid4(),
        trajectory_key=f"k/{uuid4()}",
        outcome="success",
        signal="positive_feedback",
        detected_at=_NOW,
    )


def test_metering_scope_sets_and_resets() -> None:
    tenant = uuid4()
    candidate = _candidate(tenant=tenant)
    assert current_metering() is None
    with metering_scope(candidate):
        ctx = current_metering()
        assert ctx is not None
        assert ctx.tenant_id == tenant
        assert ctx.agent_name == "reporter"
        assert ctx.agent_version == "2.0.0"
        assert ctx.trace_id == f"skill-evo-{candidate.id}"
    assert current_metering() is None


@pytest.mark.asyncio
async def test_aux_text_records_usage_inside_scope() -> None:
    tenant = uuid4()
    store = InMemoryTokenUsageStore()
    aux_text = _AuxText(_FakeAux(), usage_store=store)

    with metering_scope(_candidate(tenant=tenant)):
        await aux_text(prompt="distil this", tenant_id=tenant)

    (row,) = await store.list_for_tenant(tenant_id=tenant)
    assert row.usage_kind == "skill_evolution"
    assert row.agent_name == "reporter"
    assert row.agent_version == "2.0.0"
    assert row.trace_id is not None and row.trace_id.startswith("skill-evo-")
    assert (row.input_tokens, row.output_tokens) == (7, 3)


@pytest.mark.asyncio
async def test_aux_text_outside_scope_records_nothing() -> None:
    tenant = uuid4()
    store = InMemoryTokenUsageStore()
    aux_text = _AuxText(_FakeAux(), usage_store=store)

    await aux_text(prompt="unscoped", tenant_id=tenant)

    assert await store.list_for_tenant(tenant_id=tenant) == []


@pytest.mark.asyncio
async def test_aux_judge_bills_the_scoped_tenant() -> None:
    """The replay judge resolves credentials + bills to the candidate's own
    tenant — the ``_NULL_TENANT`` placeholder only survives unscoped calls."""
    tenant = uuid4()
    store = InMemoryTokenUsageStore()
    fake = _FakeAux("4")
    judge = _AuxJudge(fake, usage_store=store)

    with metering_scope(_candidate(tenant=tenant)):
        score = await judge.score(case_id="c1", prompt="rate this")

    assert score == 4
    assert fake.tenants == [tenant]
    (row,) = await store.list_for_tenant(tenant_id=tenant)
    assert row.usage_kind == "skill_evolution"


@pytest.mark.asyncio
async def test_persist_failure_never_breaks_the_aux_call() -> None:
    class _ExplodingStore(InMemoryTokenUsageStore):
        async def insert(self, record):  # type: ignore[override]
            raise RuntimeError("db down")

    aux_text = _AuxText(_FakeAux(), usage_store=_ExplodingStore())
    with metering_scope(_candidate(tenant=uuid4())):
        assert await aux_text(prompt="p", tenant_id=uuid4()) == "5"
