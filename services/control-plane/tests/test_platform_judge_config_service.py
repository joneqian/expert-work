import pytest

from control_plane.platform_judge_config import PlatformJudgeConfigService
from helix_agent.persistence.platform_judge_config.memory import (
    InMemoryPlatformJudgeConfigStore,
)


@pytest.mark.asyncio
async def test_none_when_no_db_row() -> None:
    # Unset is a valid state — the runtime falls back to the agent's own model.
    svc = PlatformJudgeConfigService(store=InMemoryPlatformJudgeConfigStore())
    assert await svc.effective_judge_config() is None


@pytest.mark.asyncio
async def test_db_row_wins() -> None:
    store = InMemoryPlatformJudgeConfigStore()
    await store.put(judge_provider="deepseek", judge_model="deepseek-chat", updated_by="a")
    svc = PlatformJudgeConfigService(store=store)
    assert await svc.effective_judge_config() == ("deepseek", "deepseek-chat")


@pytest.mark.asyncio
async def test_partial_row_is_none() -> None:
    store = InMemoryPlatformJudgeConfigStore()
    await store.put(judge_provider="deepseek", judge_model=None, updated_by="a")
    svc = PlatformJudgeConfigService(store=store)
    assert await svc.effective_judge_config() is None


@pytest.mark.asyncio
async def test_cache_then_invalidate_picks_up_new_row() -> None:
    store = InMemoryPlatformJudgeConfigStore()
    svc = PlatformJudgeConfigService(store=store)
    assert await svc.effective_judge_config() is None
    await store.put(judge_provider="glm", judge_model="glm-4-flash", updated_by="a")
    assert await svc.effective_judge_config() is None  # cached until invalidate
    svc.invalidate()
    assert await svc.effective_judge_config() == ("glm", "glm-4-flash")


@pytest.mark.asyncio
async def test_put_writes_and_invalidates_then_clear() -> None:
    store = InMemoryPlatformJudgeConfigStore()
    svc = PlatformJudgeConfigService(store=store)
    await svc.put(judge_provider="glm", judge_model="glm-4-flash", updated_by="admin-1")
    assert await svc.effective_judge_config() == ("glm", "glm-4-flash")
    # clearing both → back to None (fall back to agent model)
    await svc.put(judge_provider=None, judge_model=None, updated_by="admin-1")
    assert await svc.effective_judge_config() is None
