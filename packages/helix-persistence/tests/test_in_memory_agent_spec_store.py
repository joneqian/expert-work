"""Unit tests for :class:`InMemoryAgentSpecStore`."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import UUID, uuid4

import pytest

from helix_agent.persistence.agent_spec import (
    DuplicateAgentSpecError,
    InMemoryAgentSpecStore,
)
from helix_agent.protocol import AgentSpec, AgentSpecStatus

_TENANT_A = UUID("11111111-1111-1111-1111-111111111111")
_TENANT_B = UUID("22222222-2222-2222-2222-222222222222")

_BASE_SPEC: dict[str, Any] = {
    "apiVersion": "helix.io/v1",
    "kind": "Agent",
    "metadata": {"name": "code-reviewer", "version": "1.0.0", "tenant": "platform-eng"},
    "spec": {
        "tenant_config": {},
        "model": {"provider": "anthropic", "name": "claude-sonnet-4-5"},
        "system_prompt": {"template": "you are a reviewer"},
        "sandbox": {
            "resources": {"cpu": "1.0", "memory": "1Gi"},
            "network": {"egress": "proxy", "allowlist": ["api.anthropic.com"]},
            "filesystem": {"readonly_root": True, "writable": ["/workspace"]},
        },
    },
}


def _spec(*, version: str = "1.0.0", name: str = "code-reviewer") -> AgentSpec:
    doc = deepcopy(_BASE_SPEC)
    doc["metadata"]["version"] = version
    doc["metadata"]["name"] = name
    return AgentSpec.model_validate(doc)


def _sha() -> str:
    return "a" * 64


@pytest.fixture
def store() -> InMemoryAgentSpecStore:
    return InMemoryAgentSpecStore()


@pytest.mark.asyncio
async def test_create_then_get_round_trip(store: InMemoryAgentSpecStore) -> None:
    record = await store.create(
        tenant_id=_TENANT_A, spec=_spec(), spec_sha256=_sha(), created_by="alice"
    )
    assert record.name == "code-reviewer"
    assert record.status is AgentSpecStatus.ACTIVE
    fetched = await store.get(tenant_id=_TENANT_A, name="code-reviewer", version="1.0.0")
    assert fetched is not None
    assert fetched.id == record.id


@pytest.mark.asyncio
async def test_duplicate_create_raises(store: InMemoryAgentSpecStore) -> None:
    await store.create(tenant_id=_TENANT_A, spec=_spec(), spec_sha256=_sha(), created_by="a")
    with pytest.raises(DuplicateAgentSpecError):
        await store.create(tenant_id=_TENANT_A, spec=_spec(), spec_sha256=_sha(), created_by="a")


@pytest.mark.asyncio
async def test_tenant_isolation_on_get(store: InMemoryAgentSpecStore) -> None:
    await store.create(tenant_id=_TENANT_A, spec=_spec(), spec_sha256=_sha(), created_by="a")
    assert await store.get(tenant_id=_TENANT_B, name="code-reviewer", version="1.0.0") is None


@pytest.mark.asyncio
async def test_list_filters(store: InMemoryAgentSpecStore) -> None:
    await store.create(
        tenant_id=_TENANT_A, spec=_spec(version="1.0.0"), spec_sha256=_sha(), created_by="a"
    )
    await store.create(
        tenant_id=_TENANT_A, spec=_spec(version="1.0.1"), spec_sha256=_sha(), created_by="a"
    )
    await store.create(
        tenant_id=_TENANT_A, spec=_spec(name="other"), spec_sha256=_sha(), created_by="a"
    )
    rows = await store.list_by_tenant(tenant_id=_TENANT_A, name="code-reviewer")
    assert len(rows) == 2
    # Newest first ordering.
    assert rows[0].version == "1.0.1"


@pytest.mark.asyncio
async def test_update_spec_round_trip(store: InMemoryAgentSpecStore) -> None:
    await store.create(tenant_id=_TENANT_A, spec=_spec(), spec_sha256=_sha(), created_by="a")
    new_doc = deepcopy(_BASE_SPEC)
    new_doc["spec"]["system_prompt"]["template"] = "updated prompt"
    new_spec = AgentSpec.model_validate(new_doc)
    record = await store.update_spec(
        tenant_id=_TENANT_A,
        name="code-reviewer",
        version="1.0.0",
        spec=new_spec,
        spec_sha256="b" * 64,
        updated_by="alice",
    )
    assert record is not None
    assert record.spec.spec.system_prompt.template == "updated prompt"
    assert record.spec_sha256 == "b" * 64


@pytest.mark.asyncio
async def test_update_spec_returns_none_when_missing(store: InMemoryAgentSpecStore) -> None:
    result = await store.update_spec(
        tenant_id=_TENANT_A,
        name="none",
        version="9.9.9",
        spec=_spec(),
        spec_sha256=_sha(),
        updated_by="a",
    )
    assert result is None


@pytest.mark.asyncio
async def test_soft_delete_hides_from_get(store: InMemoryAgentSpecStore) -> None:
    await store.create(tenant_id=_TENANT_A, spec=_spec(), spec_sha256=_sha(), created_by="a")
    deleted = await store.update_status(
        tenant_id=_TENANT_A,
        name="code-reviewer",
        version="1.0.0",
        status=AgentSpecStatus.DELETED,
    )
    assert deleted is not None and deleted.status is AgentSpecStatus.DELETED
    assert await store.get(tenant_id=_TENANT_A, name="code-reviewer", version="1.0.0") is None
    # Include-deleted opt-in returns the row.
    fetched = await store.get(
        tenant_id=_TENANT_A,
        name="code-reviewer",
        version="1.0.0",
        include_deleted=True,
    )
    assert fetched is not None and fetched.status is AgentSpecStatus.DELETED


@pytest.mark.asyncio
async def test_update_status_unknown_returns_none(store: InMemoryAgentSpecStore) -> None:
    result = await store.update_status(
        tenant_id=_TENANT_A,
        name="missing",
        version="0.0.0",
        status=AgentSpecStatus.DEPRECATED,
    )
    assert result is None


@pytest.mark.asyncio
async def test_record_ids_are_unique() -> None:
    s = InMemoryAgentSpecStore()
    a = await s.create(
        tenant_id=_TENANT_A, spec=_spec(version="1.0.0"), spec_sha256=_sha(), created_by="x"
    )
    b = await s.create(
        tenant_id=_TENANT_A, spec=_spec(version="1.0.1"), spec_sha256=_sha(), created_by="x"
    )
    assert isinstance(a.id, UUID) and isinstance(b.id, UUID)
    assert a.id != b.id and a.id != uuid4()
