"""End-to-end tests for the J.12 curation + eval-dataset API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage, HumanMessage

from control_plane.app import create_app
from control_plane.audit import build_default_audit_logger
from control_plane.settings import DEFAULT_DEV_TENANT_ID, Settings
from expert_work.persistence.audit_log import InMemoryAuditLogStore
from expert_work.persistence.curation import (
    InMemoryCurationCandidateStore,
    InMemoryEvalDatasetStore,
)
from expert_work.protocol import CurationCandidateRecord, CurationSignal, TrajectoryOutcome
from expert_work.runtime.storage import InMemoryObjectStore
from orchestrator.trajectory import TrajectoryRecord, TrajectoryRecorder
from tests.agent_fixtures import stub_agent_runtime
from tests.auth_fixtures import (
    TEST_AUDIENCE,
    TEST_ISSUER,
    build_test_jwt_verifier,
    make_test_jwt,
)

_TENANT = DEFAULT_DEV_TENANT_ID
_BASE = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)


class _Ctx:
    """The curation API client plus the in-memory stores behind it."""

    def __init__(
        self,
        client: AsyncClient,
        candidates: InMemoryCurationCandidateStore,
        datasets: InMemoryEvalDatasetStore,
        object_store: InMemoryObjectStore,
    ) -> None:
        self.client = client
        self.candidates = candidates
        self.datasets = datasets
        self.object_store = object_store

    async def seed_candidate(
        self,
        *,
        agent_name: str = "reporter",
        signal: CurationSignal = "failed_outcome",
        outcome: TrajectoryOutcome = "failed",
        trajectory_key: str | None = None,
    ) -> CurationCandidateRecord:
        record = CurationCandidateRecord(
            id=uuid4(),
            tenant_id=_TENANT,
            agent_name=agent_name,
            agent_version="1.0.0",
            thread_id=uuid4(),
            user_id=uuid4(),
            trajectory_key=trajectory_key or f"trajectories/{_TENANT}/{outcome}/x-{uuid4()}.jsonl",
            outcome=outcome,
            signal=signal,
            feedback_rating="down" if signal == "negative_feedback" else None,
            detected_at=_BASE,
        )
        await self.candidates.upsert(record)
        return record


@pytest.fixture
async def ctx() -> AsyncIterator[_Ctx]:
    settings = Settings(
        env="dev",
        auth_mode="dev",
        rate_limit_burst=10_000,
        rate_limit_per_second=10_000.0,
        oidc_issuer=TEST_ISSUER,
        oidc_audience=[TEST_AUDIENCE],
        max_eval_dataset_rows_per_tenant=3,  # low cap so the quota test is cheap
    )
    candidates = InMemoryCurationCandidateStore()
    datasets = InMemoryEvalDatasetStore()
    object_store = InMemoryObjectStore()
    app = create_app(
        settings=settings,
        audit_logger=build_default_audit_logger(InMemoryAuditLogStore()),
        jwt_verifier=build_test_jwt_verifier(),
        agent_runtime=stub_agent_runtime(),
        enable_scheduler=False,
        curation_candidate_repo=candidates,
        eval_dataset_repo=datasets,
    )
    # An injected runtime skips the lifespan branch that builds the
    # ObjectStore — wire one so the candidate-detail path can read.
    app.state.object_store = object_store
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {make_test_jwt(tenant_id=_TENANT)}"}
    async with AsyncClient(
        transport=transport, base_url="http://control-plane.test", headers=headers
    ) as client:
        yield _Ctx(client, candidates, datasets, object_store)


# --- eval-dataset CRUD -----------------------------------------------------


@pytest.mark.asyncio
async def test_create_eval_dataset(ctx: _Ctx) -> None:
    resp = await ctx.client.post(
        "/v1/eval-datasets",
        json={
            "agent_name": "reporter",
            "name": "regression-set",
            "input": {"prompt": "hi"},
            "expected": {"answer": "ok"},
            "source": "golden",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["source"] == "golden"
    assert await ctx.datasets.count_by_tenant(tenant_id=_TENANT) == 1


@pytest.mark.asyncio
async def test_create_golden_without_expected_is_422(ctx: _Ctx) -> None:
    resp = await ctx.client.post(
        "/v1/eval-datasets",
        json={"agent_name": "reporter", "name": "s", "input": {}, "source": "golden"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_trajectory_source_allows_no_expected(ctx: _Ctx) -> None:
    resp = await ctx.client.post(
        "/v1/eval-datasets",
        json={"agent_name": "reporter", "name": "s", "input": {"p": "x"}, "source": "trajectory"},
    )
    assert resp.status_code == 201
    assert resp.json()["expected"] is None


@pytest.mark.asyncio
async def test_list_eval_datasets_by_agent(ctx: _Ctx) -> None:
    for agent in ("reporter", "reporter", "auditor"):
        await ctx.client.post(
            "/v1/eval-datasets",
            json={
                "agent_name": agent,
                "name": "s",
                "input": {},
                "expected": {"a": 1},
                "source": "golden",
            },
        )
    resp = await ctx.client.get("/v1/eval-datasets", params={"agent_name": "reporter"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 2


@pytest.mark.asyncio
async def test_get_and_patch_and_delete_eval_dataset(ctx: _Ctx) -> None:
    created = (
        await ctx.client.post(
            "/v1/eval-datasets",
            json={
                "agent_name": "reporter",
                "name": "s",
                "input": {},
                "expected": {"a": 1},
                "source": "golden",
            },
        )
    ).json()
    dataset_id = created["id"]

    got = await ctx.client.get(f"/v1/eval-datasets/{dataset_id}")
    assert got.status_code == 200

    patched = await ctx.client.patch(f"/v1/eval-datasets/{dataset_id}", json={"name": "renamed"})
    assert patched.status_code == 200
    assert patched.json()["name"] == "renamed"

    deleted = await ctx.client.delete(f"/v1/eval-datasets/{dataset_id}")
    assert deleted.status_code == 200
    gone = await ctx.client.get(f"/v1/eval-datasets/{dataset_id}")
    assert gone.status_code == 404
    delete_again = await ctx.client.delete(f"/v1/eval-datasets/{dataset_id}")
    assert delete_again.status_code == 404


@pytest.mark.asyncio
async def test_eval_dataset_quota_returns_429(ctx: _Ctx) -> None:
    body = {
        "agent_name": "reporter",
        "name": "s",
        "input": {},
        "expected": {"a": 1},
        "source": "golden",
    }
    for _ in range(3):  # max_eval_dataset_rows_per_tenant=3
        created = await ctx.client.post("/v1/eval-datasets", json=body)
        assert created.status_code == 201
    over = await ctx.client.post("/v1/eval-datasets", json=body)
    assert over.status_code == 429


# --- curation candidates ---------------------------------------------------


@pytest.mark.asyncio
async def test_list_candidates_and_filter_by_signal(ctx: _Ctx) -> None:
    await ctx.seed_candidate(signal="failed_outcome")
    await ctx.seed_candidate(signal="negative_feedback", outcome="success")

    all_resp = await ctx.client.get("/v1/curation/candidates")
    assert all_resp.json()["total"] == 2
    filtered = await ctx.client.get(
        "/v1/curation/candidates", params={"signal": "negative_feedback"}
    )
    assert filtered.json()["total"] == 1


@pytest.mark.asyncio
async def test_get_candidate_detail_includes_trajectory(ctx: _Ctx) -> None:
    thread_id, tenant = uuid4(), _TENANT
    record = TrajectoryRecord(
        thread_id=thread_id,
        tenant_id=tenant,
        outcome="failed",
        messages=[HumanMessage(content="hi"), AIMessage(content="bye")],
        finished_at=_BASE,
    )
    recorder = TrajectoryRecorder(object_store=ctx.object_store)
    key = recorder.key_for(record)
    await recorder.record(record)
    candidate = await ctx.seed_candidate(trajectory_key=key)

    resp = await ctx.client.get(f"/v1/curation/candidates/{candidate.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(candidate.id)
    assert body["trajectory"]["messages"][0]["role"] == "user"


@pytest.mark.asyncio
async def test_get_candidate_unknown_is_404(ctx: _Ctx) -> None:
    resp = await ctx.client.get(f"/v1/curation/candidates/{uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_promote_candidate_creates_eval_dataset(ctx: _Ctx) -> None:
    candidate = await ctx.seed_candidate()
    resp = await ctx.client.post(
        f"/v1/curation/candidates/{candidate.id}/promote",
        json={
            "name": "regression-set",
            "input": {"prompt": "hi"},
            "expected": {"answer": "corrected"},
            "source": "regression",
        },
    )
    assert resp.status_code == 201
    dataset = resp.json()
    assert dataset["source"] == "regression"
    assert dataset["source_trajectory_key"] == candidate.trajectory_key

    stored = await ctx.candidates.get(candidate_id=candidate.id, tenant_id=_TENANT)
    assert stored is not None
    assert stored.status.value == "promoted"
    assert str(stored.eval_dataset_id) == dataset["id"]


@pytest.mark.asyncio
async def test_promote_already_reviewed_is_409(ctx: _Ctx) -> None:
    candidate = await ctx.seed_candidate()
    body = {
        "name": "s",
        "input": {},
        "expected": {"a": 1},
        "source": "regression",
    }
    first = await ctx.client.post(f"/v1/curation/candidates/{candidate.id}/promote", json=body)
    assert first.status_code == 201
    second = await ctx.client.post(f"/v1/curation/candidates/{candidate.id}/promote", json=body)
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_dismiss_candidate(ctx: _Ctx) -> None:
    candidate = await ctx.seed_candidate()
    resp = await ctx.client.post(f"/v1/curation/candidates/{candidate.id}/dismiss")
    assert resp.status_code == 200

    stored = await ctx.candidates.get(candidate_id=candidate.id, tenant_id=_TENANT)
    assert stored is not None
    assert stored.status.value == "dismissed"


@pytest.mark.asyncio
async def test_unauthenticated_request_is_401(ctx: _Ctx) -> None:
    app = ctx.client._transport.app  # type: ignore[attr-defined,union-attr]
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://control-plane.test"
    ) as bare:
        resp = await bare.get("/v1/curation/candidates")
    assert resp.status_code == 401
