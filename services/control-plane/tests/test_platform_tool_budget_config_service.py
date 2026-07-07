"""Unit tests for :class:`PlatformToolBudgetConfigService` — Phase 3.

DB-wins over the ``EXPERT_WORK_TOOL_OUTPUT_BUDGET`` env default; TTL-cached with
``invalidate()`` on write for immediate effect on the writing instance.
"""

from __future__ import annotations

import pytest

from control_plane.platform_tool_budget_config import PlatformToolBudgetConfigService
from expert_work.persistence.platform_tool_budget_config import (
    InMemoryPlatformToolBudgetConfigStore,
)


def _service() -> PlatformToolBudgetConfigService:
    # ttl 0 ⇒ every read reloads, so writes are visible without invalidate races.
    return PlatformToolBudgetConfigService(
        store=InMemoryPlatformToolBudgetConfigStore(), ttl_seconds=0.0
    )


@pytest.mark.asyncio
async def test_unset_uses_env_default_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXPERT_WORK_TOOL_OUTPUT_BUDGET", raising=False)
    svc = _service()
    assert await svc.effective_enabled() is True
    assert await svc.configured_enabled() is None


@pytest.mark.asyncio
async def test_unset_uses_env_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXPERT_WORK_TOOL_OUTPUT_BUDGET", "0")
    svc = _service()
    assert await svc.effective_enabled() is False
    assert await svc.configured_enabled() is None


@pytest.mark.asyncio
async def test_db_row_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXPERT_WORK_TOOL_OUTPUT_BUDGET", "0")  # env says off
    svc = _service()
    await svc.put(enabled=True, updated_by="admin")  # DB says on
    assert await svc.effective_enabled() is True
    assert await svc.configured_enabled() is True


@pytest.mark.asyncio
async def test_db_off_wins_over_env_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXPERT_WORK_TOOL_OUTPUT_BUDGET", raising=False)  # env default on
    svc = _service()
    await svc.put(enabled=False, updated_by="admin")
    assert await svc.effective_enabled() is False
    assert await svc.configured_enabled() is False


@pytest.mark.asyncio
async def test_put_invalidates_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXPERT_WORK_TOOL_OUTPUT_BUDGET", raising=False)
    # Long TTL: only invalidate-on-write makes the new value visible.
    svc = PlatformToolBudgetConfigService(
        store=InMemoryPlatformToolBudgetConfigStore(), ttl_seconds=9999.0
    )
    assert await svc.effective_enabled() is True  # warm the cache (env default)
    await svc.put(enabled=False, updated_by="admin")
    assert await svc.effective_enabled() is False  # invalidate made it visible
