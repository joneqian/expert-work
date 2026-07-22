"""Integration tests for ``POST /v1/triggers/{trigger_id}:fire`` — Spec 1 PR4
Task 3 (conversational scheduled tasks, debug-console "fire now").

App fixture + auth-header helpers copied from ``test_triggers_api.py`` (the
real trigger CRUD integration-test harness) — same ``Settings``/``create_app``
shape, same ``_client_as`` distinct-principal helper. The "real graph +
checkpointer" seeding pattern for the delivery test is copied from
``test_scheduler.py``'s ``test_reconcile_delivers_result_to_originating_thread``
/ ``test_trigger_delivery.py``'s ``test_deliver_run_result_delivers_and_mirrors``
— those establish how to give ``deliver_run_result`` a real checkpoint to read
from and write into.

The fire-now endpoint spawns the actual run asynchronously via
``fire_trigger`` (fire-and-forget ``asyncio.create_task``), then polls the run
store for a terminal status. To avoid a real sleep in the success test, we
monkeypatch ``control_plane.api.triggers.fire_trigger`` to synchronously seed
a SUCCESS ``RunInfo`` (with an assistant reply on its scratch thread) into
the same ``run_store`` the route reads from — so the route's very first
``runs.get(...)`` poll already observes a terminal run.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage, HumanMessage

from control_plane.app import create_app
from control_plane.audit import build_default_audit_logger
from control_plane.settings import DEFAULT_DEV_TENANT_ID, Settings
from expert_work.persistence.audit_log import InMemoryAuditLogStore
from expert_work.protocol import AgentSpec, TriggerRecord
from expert_work.runtime.checkpointer import make_checkpointer
from expert_work.runtime.runs import DisconnectMode, RunInfo, RunStatus
from expert_work.runtime.secret_store import LocalDevSecretStore
from orchestrator.agent_factory import build_agent
from tests.agent_fixtures import stub_agent_runtime
from tests.auth_fixtures import (
    TEST_AUDIENCE,
    TEST_ISSUER,
    build_test_jwt_verifier,
    make_test_jwt,
)

_DEFAULT_TENANT = DEFAULT_DEV_TENANT_ID
_NOW = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)

_REPORTER_YAML = """\
apiVersion: expert_work.io/v1
kind: Agent
metadata:
  name: reporter
  version: "1.0.0"
  tenant: platform-eng
spec:
  tenant_config: {}
  model:
    provider: anthropic
    name: claude-sonnet-4-5
  system_prompt:
    template: "you report"
  sandbox:
    resources: { cpu: "1.0", memory: "1Gi" }
    network:
      egress: proxy
      allowlist: ["api.anthropic.com"]
    filesystem:
      readonly_root: true
      writable: ["/workspace"]
"""

# Same identity (reporter/1.0.0) as ``_REPORTER_YAML`` above, as a python dict
# — needed to build a REAL graph (``build_agent``) bound to an explicit
# checkpointer, matching ``test_scheduler.py`` / ``test_trigger_delivery.py``'s
# "real graph, no LLM call" delivery-test pattern (they never invoke the
# graph — only ``aupdate_state``/``aget_state`` — so the fake anthropic key
# below never reaches the network).
_MANIFEST: dict[str, Any] = {
    "apiVersion": "expert_work.io/v1",
    "kind": "Agent",
    "metadata": {"name": "reporter", "version": "1.0.0", "tenant": "platform-eng"},
    "spec": {
        "tenant_config": {},
        "model": {"provider": "anthropic", "name": "claude-sonnet-4-5"},
        "system_prompt": {"template": "you report"},
        "sandbox": {
            "resources": {"cpu": "1.0", "memory": "1Gi"},
            "network": {"egress": "proxy", "allowlist": ["api.anthropic.com"]},
            "filesystem": {"readonly_root": True, "writable": ["/workspace"]},
        },
    },
}

_ANTHROPIC_KEY_NAME = "expert-work/dev/llm/anthropic"
_OPENAI_KEY_NAME = "expert-work/dev/llm/openai"
_KIMI_KEY_NAME = "expert-work/dev/llm/kimi"


def _secret_store() -> LocalDevSecretStore:
    return LocalDevSecretStore.from_mapping(
        {
            _ANTHROPIC_KEY_NAME: "sk-ant-test",
            _OPENAI_KEY_NAME: "sk-openai-test",
            _KIMI_KEY_NAME: "sk-kimi-test",
        }
    )


_PROVIDER_KEY_NAMES = {
    "anthropic": _ANTHROPIC_KEY_NAME,
    "openai": _OPENAI_KEY_NAME,
    "kimi": _KIMI_KEY_NAME,
    "self-hosted": _OPENAI_KEY_NAME,
    "azure": _OPENAI_KEY_NAME,
    "qwen": _OPENAI_KEY_NAME,
}


async def _platform_resolver(provider: str) -> list[str]:
    return [f"secret://{_PROVIDER_KEY_NAMES[provider]}"]


@pytest.fixture
def audit_store() -> InMemoryAuditLogStore:
    return InMemoryAuditLogStore()


@pytest.fixture
async def triggers_client(audit_store: InMemoryAuditLogStore) -> AsyncIterator[AsyncClient]:
    """Copied from ``test_triggers_api.py``'s fixture of the same name."""
    settings = Settings(
        env="dev",
        auth_mode="dev",
        rate_limit_burst=10_000,
        rate_limit_per_second=10_000.0,
        oidc_issuer=TEST_ISSUER,
        oidc_audience=[TEST_AUDIENCE],
        max_cron_triggers_per_tenant=2,
    )
    app = create_app(
        settings=settings,
        audit_logger=build_default_audit_logger(audit_store),
        jwt_verifier=build_test_jwt_verifier(),
        agent_runtime=stub_agent_runtime(),
        enable_scheduler=False,  # this suite drives firing directly
    )
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {make_test_jwt(tenant_id=_DEFAULT_TENANT)}"}
    async with AsyncClient(
        transport=transport, base_url="http://control-plane.test", headers=headers
    ) as client:
        await client.post("/v1/agents", json={"manifest_yaml": _REPORTER_YAML})
        yield client


def _client_as(
    authed: AsyncClient,
    *,
    subject: str,
    roles: tuple[str, ...] = ("viewer",),
    sub_type: str = "user",
) -> AsyncClient:
    """A client over the same app, authenticated as a distinct principal.

    Copied from ``test_triggers_api.py``: ``triggers_client``'s own JWT
    (subject ``dev-user``) defaults ``roles=("admin",)``.
    """
    app = authed._transport.app  # type: ignore[attr-defined,union-attr]
    token = make_test_jwt(
        tenant_id=_DEFAULT_TENANT, subject=subject, roles=roles, sub_type=sub_type
    )
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://control-plane.test",
        headers={"Authorization": f"Bearer {token}"},
    )


async def _create_cron(
    client: AsyncClient, *, name: str = "nightly", agent_version: str = "1.0.0"
) -> dict[str, Any]:
    resp = await client.post(
        "/v1/triggers",
        json={
            "agent_name": "reporter",
            "agent_version": agent_version,
            "name": name,
            "kind": "cron",
            "config": {"expr": "0 9 * * *"},
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()  # type: ignore[no-any-return]


@pytest.mark.asyncio
async def test_fire_now_delivers_result_to_originating_thread(
    triggers_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reuse_thread cron trigger's manual fire delivers into the originating
    conversation, and the poll's first ``runs.get`` already sees SUCCESS —
    ``fire_trigger`` is faked to seed the run store synchronously instead of
    spawning a real background task, so the test never sleeps."""
    app = triggers_client._transport.app  # type: ignore[attr-defined,union-attr]
    run_store = app.state.run_store
    runtime = app.state.agent_runtime
    orig = uuid4()

    async with make_checkpointer("memory") as cp:
        built = await build_agent(
            AgentSpec.model_validate(_MANIFEST),
            secret_store=_secret_store(),
            checkpointer=cp,
            provider_key_resolver=_platform_resolver,  # required (Stream Y-2)
        )
        # seed the originating conversation's prior history.
        await built.graph.aupdate_state(
            {"configurable": {"thread_id": str(orig), "tenant_id": str(_DEFAULT_TENANT)}},
            {
                "messages": [
                    HumanMessage(content="set up my task"),
                    AIMessage(content="scheduled"),
                ]
            },
            as_node="agent",
        )
        await app.state.thread_meta_repo.create(
            thread_id=orig, tenant_id=_DEFAULT_TENANT, created_by="dev-user"
        )

        trigger = TriggerRecord(
            id=uuid4(),
            tenant_id=_DEFAULT_TENANT,
            agent_name="reporter",
            agent_version="1.0.0",
            name="fire-now-success",
            kind="cron",
            config={"expr": "0 9 * * *", "seed_input": "go"},
            enabled=True,
            source="api",
            originating_thread_id=orig,
            context_mode="reuse_thread",
            created_at=_NOW,
            updated_at=_NOW,
        )
        await app.state.trigger_store.create(trigger)

        async def _fake_fire_trigger(
            record: TriggerRecord, *, now: datetime, **_kwargs: Any
        ) -> UUID:
            """Stand-in for the real ``fire_trigger``: seeds a terminal SUCCESS
            run directly (no spawned task) so the route's poll loop's FIRST
            ``runs.get`` call already observes a terminal run — no sleeping."""
            scratch = uuid4()
            await built.graph.aupdate_state(
                {
                    "configurable": {
                        "thread_id": str(scratch),
                        "tenant_id": str(record.tenant_id),
                    }
                },
                {
                    "messages": [
                        HumanMessage(content="go"),
                        AIMessage(content="Today's AI news: X"),
                    ]
                },
                as_node="__start__",
            )
            run_id = uuid4()
            await run_store.create(
                RunInfo(
                    run_id=run_id,
                    tenant_id=record.tenant_id,
                    thread_id=scratch,
                    user_id=None,
                    status=RunStatus.SUCCESS,
                    on_disconnect=DisconnectMode.CANCEL,
                    is_resume=False,
                    error=None,
                    created_at=now,
                    updated_at=now,
                    finished_at=now,
                )
            )
            return run_id

        monkeypatch.setattr("control_plane.api.triggers.fire_trigger", _fake_fire_trigger)
        runtime.durable_checkpointer = cp

        async def _get_agent(**_kwargs: Any) -> Any:
            return built

        monkeypatch.setattr(runtime, "get_agent", _get_agent)

        resp = await triggers_client.post(f"/v1/triggers/{trigger.id}:fire")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["delivery"] == "delivered"
        assert body["delivered_text"] == "Today's AI news: X"
        assert body["trigger_run_status"] == "succeeded"

        # the result really landed in the originating conversation.
        msgs = await triggers_client.get(f"/v1/sessions/{orig}/messages")
        assert msgs.status_code == 200, msgs.text
        contents = [m["content"] for m in msgs.json()["data"]["messages"]]
        assert body["delivered_text"] in contents


@pytest.mark.asyncio
async def test_fire_now_forbidden_for_non_owner(triggers_client: AsyncClient) -> None:
    """触发器属 admin(默认 owner);非 admin 的 distinct-subject user-b → 403."""
    created = await _create_cron(triggers_client, name="owned-by-admin")
    trigger_id = created["id"]

    other = _client_as(triggers_client, subject="user-b", roles=("viewer",))
    async with other:
        resp = await other.post(f"/v1/triggers/{trigger_id}:fire")
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "USER_SCOPE_FORBIDDEN"


@pytest.mark.asyncio
async def test_fire_now_agent_unavailable_returns_409(triggers_client: AsyncClient) -> None:
    """Trigger points at an agent_version that was never registered →
    ``fire_trigger`` (the REAL one — no monkeypatch) returns ``None`` at its
    preflight ``agent_spec_store.get`` check → the endpoint 409s."""
    created = await _create_cron(triggers_client, name="ghost-agent", agent_version="9.9.9")
    trigger_id = created["id"]

    resp = await triggers_client.post(f"/v1/triggers/{trigger_id}:fire")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_fire_now_paused_run_left_pending(
    triggers_client: AsyncClient,
    audit_store: InMemoryAuditLogStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fire-now run that lands on a human-approval gate is ``PAUSED`` — a
    *live*, resumable run, not an outcome. ``PAUSED`` is nonetheless in
    ``TERMINAL_RUN_STATUSES`` (it stops the poll loop), so the disposition
    code must special-case it BEFORE the generic terminal branch: leave the
    trigger_run row at ``FIRED`` (not FAILED) and report ``delivery="pending"``
    — mirroring ``scheduler._reconcile_one``, which leaves PAUSED/RUNNING/
    PENDING firings alone for the next sweep.

    A wrong FAILED mismark would be permanent: ``list_fired`` filters on
    ``status == FIRED``, so a mismarked row silently drops out of every
    future reconcile sweep and the eventual approve → SUCCESS delivery is
    lost forever.
    """
    app = triggers_client._transport.app  # type: ignore[attr-defined,union-attr]
    run_store = app.state.run_store

    created = await _create_cron(triggers_client, name="fire-now-paused")
    trigger_id = created["id"]

    async def _fake_fire_trigger(record: TriggerRecord, *, now: datetime, **_kwargs: Any) -> UUID:
        """Seed a PAUSED run directly — the route's first poll already
        observes a stop-the-loop status, no real sleep needed."""
        run_id = uuid4()
        await run_store.create(
            RunInfo(
                run_id=run_id,
                tenant_id=record.tenant_id,
                thread_id=uuid4(),
                user_id=None,
                status=RunStatus.PAUSED,
                on_disconnect=DisconnectMode.CANCEL,
                is_resume=False,
                error=None,
                created_at=now,
                updated_at=now,
                finished_at=now,
            )
        )
        return run_id

    monkeypatch.setattr("control_plane.api.triggers.fire_trigger", _fake_fire_trigger)

    resp = await triggers_client.post(f"/v1/triggers/{trigger_id}:fire")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["delivery"] == "pending"
    assert body["trigger_run_status"] == "fired"

    # the trigger_run row must NOT have been flipped to FAILED.
    rows = await app.state.trigger_run_store.list_by_trigger(
        trigger_id=UUID(trigger_id), tenant_id=_DEFAULT_TENANT
    )
    assert len(rows) == 1
    assert rows[0].status.value == "fired"

    # ...and no bogus TRIGGER_FAILED audit entry was left as the permanent
    # record for this trigger (the pre-fix code emits one every time).
    from expert_work.protocol import AuditAction, AuditQuery

    page = await audit_store.query(
        AuditQuery(
            tenant_id=_DEFAULT_TENANT,
            action=AuditAction.TRIGGER_FAILED,
            resource_id=trigger_id,
        )
    )
    assert page.entries == []


@pytest.mark.asyncio
async def test_fire_now_timeout_returns_pending(
    triggers_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Coverage gap: the poll loop's own deadline branch. A run that never
    reaches a terminal status inside the poll window must return
    ``delivery="pending"`` with the trigger_run left ``FIRED`` — exercises
    the ``datetime.now(UTC) >= deadline`` branch and the
    ``await asyncio.sleep(1)`` line.

    ``trigger_fire_now_timeout_s`` is monkeypatched to 1 directly on the
    running app's shared ``Settings`` instance — ``Settings`` is a plain
    (non-frozen, no ``validate_assignment``) ``pydantic_settings.BaseSettings``,
    so a normal attribute set is honoured by ``_get_settings``'s
    ``request.app.state.settings`` read — so this test costs ~1s wall clock
    instead of the fixture's 60s default.
    """
    app = triggers_client._transport.app  # type: ignore[attr-defined,union-attr]
    run_store = app.state.run_store
    monkeypatch.setattr(app.state.settings, "trigger_fire_now_timeout_s", 1)

    created = await _create_cron(triggers_client, name="fire-now-timeout")
    trigger_id = created["id"]

    async def _fake_fire_trigger(record: TriggerRecord, *, now: datetime, **_kwargs: Any) -> UUID:
        """Seed a run that stays RUNNING forever — never terminal, so the
        route must fall through to the deadline branch."""
        run_id = uuid4()
        await run_store.create(
            RunInfo(
                run_id=run_id,
                tenant_id=record.tenant_id,
                thread_id=uuid4(),
                user_id=None,
                status=RunStatus.RUNNING,
                on_disconnect=DisconnectMode.CANCEL,
                is_resume=False,
                error=None,
                created_at=now,
                updated_at=now,
                finished_at=None,
            )
        )
        return run_id

    monkeypatch.setattr("control_plane.api.triggers.fire_trigger", _fake_fire_trigger)

    resp = await triggers_client.post(f"/v1/triggers/{trigger_id}:fire")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["delivery"] == "pending"
    assert body["trigger_run_status"] == "fired"
    assert body["run_status"] == "running"
