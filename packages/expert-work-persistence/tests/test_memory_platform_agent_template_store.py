"""Unit tests for :class:`InMemoryPlatformAgentTemplateStore` — Agent-Templates M1."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import UUID

import pytest

from expert_work.persistence.platform_agent_template import (
    InMemoryPlatformAgentTemplateStore,
    PlatformAgentTemplateAlreadyExistsError,
    PlatformAgentTemplateNotFoundError,
)
from expert_work.protocol import (
    AgentSpec,
    PlatformAgentTemplatePatch,
    PlatformAgentTemplateStatus,
    PlatformAgentTemplateUpsert,
    TenantPlan,
)

_BASE_SPEC: dict[str, Any] = {
    "apiVersion": "expert_work.io/v1",
    "kind": "Agent",
    "metadata": {"name": "support-bot", "version": "1.0.0", "tenant": "platform-eng"},
    "spec": {
        "tenant_config": {},
        "model": {"provider": "anthropic", "name": "claude-sonnet-4-5"},
        "system_prompt": {"template": "you are a support agent"},
        "sandbox": {
            "resources": {"cpu": "1.0", "memory": "1Gi"},
            "network": {"egress": "proxy", "allowlist": ["api.anthropic.com"]},
            "filesystem": {"readonly_root": True, "writable": ["/workspace"]},
        },
    },
}


def _spec(*, name: str = "support-bot", version: str = "1.0.0") -> AgentSpec:
    doc = deepcopy(_BASE_SPEC)
    doc["metadata"]["name"] = name
    doc["metadata"]["version"] = version
    return AgentSpec.model_validate(doc)


def _upsert(
    *,
    name: str = "support-bot",
    version: str = "1.0.0",
    display_name: str = "Support Bot",
    category: str = "support",
    status: PlatformAgentTemplateStatus = PlatformAgentTemplateStatus.PUBLISHED,
    required_tier: TenantPlan = TenantPlan.FREE,
) -> PlatformAgentTemplateUpsert:
    return PlatformAgentTemplateUpsert(
        spec=_spec(name=name, version=version),
        display_name=display_name,
        category=category,
        status=status,
        required_tier=required_tier,
    )


@pytest.fixture
def store() -> InMemoryPlatformAgentTemplateStore:
    return InMemoryPlatformAgentTemplateStore()


@pytest.mark.asyncio
async def test_create_then_get_round_trip(store: InMemoryPlatformAgentTemplateStore) -> None:
    record = await store.create(upsert=_upsert(), created_by="admin")
    assert record.name == "support-bot"
    assert record.version == "1.0.0"
    assert record.tenant_id is None  # platform-global
    assert record.display_name == "Support Bot"
    assert record.status is PlatformAgentTemplateStatus.PUBLISHED
    assert len(record.spec_sha256) == 64
    assert record.created_by == "admin"
    fetched = await store.get(name="support-bot", version="1.0.0")
    assert fetched is not None and fetched.id == record.id


@pytest.mark.asyncio
async def test_duplicate_name_version_raises(store: InMemoryPlatformAgentTemplateStore) -> None:
    await store.create(upsert=_upsert(), created_by="admin")
    with pytest.raises(PlatformAgentTemplateAlreadyExistsError):
        await store.create(upsert=_upsert(), created_by="admin")


@pytest.mark.asyncio
async def test_same_name_distinct_versions_coexist(
    store: InMemoryPlatformAgentTemplateStore,
) -> None:
    await store.create(upsert=_upsert(version="1.0.0"), created_by="admin")
    await store.create(upsert=_upsert(version="1.1.0"), created_by="admin")
    versions = await store.list_versions(name="support-bot")
    assert {r.version for r in versions} == {"1.0.0", "1.1.0"}
    # Newest first (1.1.0 created after 1.0.0).
    assert versions[0].version == "1.1.0"


@pytest.mark.asyncio
async def test_get_latest_picks_most_recent_publish(
    store: InMemoryPlatformAgentTemplateStore,
) -> None:
    await store.create(upsert=_upsert(version="1.0.0"), created_by="admin")
    await store.create(upsert=_upsert(version="2.0.0"), created_by="admin")
    latest = await store.get_latest(name="support-bot")
    assert latest is not None and latest.version == "2.0.0"


@pytest.mark.asyncio
async def test_get_latest_filters_by_status(store: InMemoryPlatformAgentTemplateStore) -> None:
    await store.create(
        upsert=_upsert(version="1.0.0", status=PlatformAgentTemplateStatus.PUBLISHED),
        created_by="admin",
    )
    await store.create(
        upsert=_upsert(version="2.0.0", status=PlatformAgentTemplateStatus.DRAFT),
        created_by="admin",
    )
    # Newest published is 1.0.0 — the draft 2.0.0 is skipped.
    latest = await store.get_latest(
        name="support-bot", status=PlatformAgentTemplateStatus.PUBLISHED
    )
    assert latest is not None and latest.version == "1.0.0"


@pytest.mark.asyncio
async def test_list_filters_category_and_status(
    store: InMemoryPlatformAgentTemplateStore,
) -> None:
    await store.create(upsert=_upsert(name="support-bot", category="support"), created_by="a")
    await store.create(
        upsert=_upsert(name="coder", category="dev", version="1.0.0"), created_by="a"
    )
    assert {r.name for r in await store.list(category="support")} == {"support-bot"}
    assert {r.name for r in await store.list()} == {"support-bot", "coder"}
    drafts = await store.list(status=PlatformAgentTemplateStatus.DRAFT)
    assert drafts == []


@pytest.mark.asyncio
async def test_update_spec_replaces_base_in_place(
    store: InMemoryPlatformAgentTemplateStore,
) -> None:
    created = await store.create(upsert=_upsert(), created_by="admin")
    new_doc = deepcopy(_BASE_SPEC)
    new_doc["spec"]["system_prompt"]["template"] = "you are a fixed support agent"
    new_spec = AgentSpec.model_validate(new_doc)
    updated = await store.update_spec(
        name="support-bot", version="1.0.0", spec=new_spec, updated_by="bob"
    )
    assert updated is not None
    assert updated.spec.spec.system_prompt.template == "you are a fixed support agent"
    assert updated.spec_sha256 != created.spec_sha256
    assert updated.created_by == "bob"


@pytest.mark.asyncio
async def test_update_spec_missing_returns_none(
    store: InMemoryPlatformAgentTemplateStore,
) -> None:
    result = await store.update_spec(
        name="ghost", version="9.9.9", spec=_spec(name="ghost"), updated_by="x"
    )
    assert result is None


@pytest.mark.asyncio
async def test_update_meta_patches_only_supplied_fields(
    store: InMemoryPlatformAgentTemplateStore,
) -> None:
    await store.create(upsert=_upsert(display_name="Old", category="support"), created_by="a")
    patched = await store.update_meta(
        name="support-bot",
        version="1.0.0",
        patch=PlatformAgentTemplatePatch(
            display_name="New", status=PlatformAgentTemplateStatus.DRAFT
        ),
    )
    assert patched is not None
    assert patched.display_name == "New"
    assert patched.status is PlatformAgentTemplateStatus.DRAFT
    assert patched.category == "support"  # unchanged


@pytest.mark.asyncio
async def test_update_meta_missing_returns_none(
    store: InMemoryPlatformAgentTemplateStore,
) -> None:
    result = await store.update_meta(
        name="ghost", version="9.9.9", patch=PlatformAgentTemplatePatch(display_name="X")
    )
    assert result is None


@pytest.mark.asyncio
async def test_delete_then_get_returns_none(store: InMemoryPlatformAgentTemplateStore) -> None:
    await store.create(upsert=_upsert(), created_by="a")
    await store.delete(name="support-bot", version="1.0.0")
    assert await store.get(name="support-bot", version="1.0.0") is None


@pytest.mark.asyncio
async def test_delete_missing_raises(store: InMemoryPlatformAgentTemplateStore) -> None:
    with pytest.raises(PlatformAgentTemplateNotFoundError):
        await store.delete(name="ghost", version="9.9.9")


@pytest.mark.asyncio
async def test_record_ids_unique(store: InMemoryPlatformAgentTemplateStore) -> None:
    a = await store.create(upsert=_upsert(version="1.0.0"), created_by="x")
    b = await store.create(upsert=_upsert(version="1.1.0"), created_by="x")
    assert isinstance(a.id, UUID) and isinstance(b.id, UUID) and a.id != b.id
