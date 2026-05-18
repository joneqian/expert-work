"""Unit tests for InMemoryMemoryStore — Stream J.3 contract."""

from __future__ import annotations

from uuid import uuid4

import pytest

from helix_agent.persistence import InMemoryMemoryStore
from helix_agent.protocol import MemoryItem


def _item(
    *,
    tenant: object,
    user: object,
    embedding: tuple[float, ...],
    kind: str = "fact",
    content: str = "c",
) -> MemoryItem:
    return MemoryItem(
        id=uuid4(),
        tenant_id=tenant,  # type: ignore[arg-type]
        user_id=user,  # type: ignore[arg-type]
        kind=kind,  # type: ignore[arg-type]
        content=content,
        embedding=embedding,
    )


@pytest.mark.asyncio
async def test_retrieve_orders_by_cosine_distance() -> None:
    store = InMemoryMemoryStore()
    tenant, user = uuid4(), uuid4()
    await store.write(
        [
            _item(tenant=tenant, user=user, embedding=(1.0, 0.0), content="east"),
            _item(tenant=tenant, user=user, embedding=(0.0, 1.0), content="north"),
            _item(tenant=tenant, user=user, embedding=(0.7, 0.7), content="ne"),
        ]
    )
    hits = await store.retrieve(tenant_id=tenant, user_id=user, query_embedding=(1.0, 0.0), limit=3)
    assert [h.content for h in hits] == ["east", "ne", "north"]


@pytest.mark.asyncio
async def test_retrieve_filters_by_tenant_and_user() -> None:
    store = InMemoryMemoryStore()
    tenant, user, other_user, other_tenant = uuid4(), uuid4(), uuid4(), uuid4()
    await store.write(
        [
            _item(tenant=tenant, user=user, embedding=(1.0, 0.0), content="mine"),
            _item(tenant=tenant, user=other_user, embedding=(1.0, 0.0), content="peer"),
            _item(tenant=other_tenant, user=user, embedding=(1.0, 0.0), content="other-tenant"),
        ]
    )
    hits = await store.retrieve(tenant_id=tenant, user_id=user, query_embedding=(1.0, 0.0))
    assert [h.content for h in hits] == ["mine"]


@pytest.mark.asyncio
async def test_retrieve_kind_filter_and_limit() -> None:
    store = InMemoryMemoryStore()
    tenant, user = uuid4(), uuid4()
    await store.write(
        [
            _item(tenant=tenant, user=user, embedding=(1.0, 0.0), kind="fact", content="f1"),
            _item(tenant=tenant, user=user, embedding=(0.9, 0.1), kind="fact", content="f2"),
            _item(tenant=tenant, user=user, embedding=(1.0, 0.0), kind="episodic", content="e1"),
        ]
    )
    facts = await store.retrieve(
        tenant_id=tenant, user_id=user, query_embedding=(1.0, 0.0), kind="fact"
    )
    assert {h.content for h in facts} == {"f1", "f2"}

    limited = await store.retrieve(
        tenant_id=tenant, user_id=user, query_embedding=(1.0, 0.0), limit=1
    )
    assert len(limited) == 1
