"""Unit tests for :class:`PlatformDynamicWorkerConfigService` — B3 PR2.

DB-wins over the constructor-injected ``env_default``; TTL-cached with
``invalidate()`` on write for immediate effect on the writing instance.
"""

from __future__ import annotations

import pytest

from control_plane.platform_dynamic_worker_config import (
    DynamicWorkerConfig,
    PlatformDynamicWorkerConfigService,
)
from expert_work.persistence.platform_dynamic_worker_config import (
    InMemoryPlatformDynamicWorkerConfigStore,
)

_ENV_DEFAULT = DynamicWorkerConfig(3, 16, 32)


def _service() -> PlatformDynamicWorkerConfigService:
    # ttl 0 ⇒ every read reloads, so writes are visible without invalidate races.
    return PlatformDynamicWorkerConfigService(
        store=InMemoryPlatformDynamicWorkerConfigStore(),
        env_default=_ENV_DEFAULT,
        ttl_seconds=0.0,
    )


@pytest.mark.asyncio
async def test_unset_uses_env_default() -> None:
    svc = _service()
    assert await svc.effective() == _ENV_DEFAULT
    assert await svc.configured() is None


@pytest.mark.asyncio
async def test_db_row_wins_over_env() -> None:
    svc = _service()
    await svc.put(max_concurrent=5, max_per_run=32, max_iterations=48, updated_by="admin")
    expected = DynamicWorkerConfig(5, 32, 48)
    assert await svc.effective() == expected
    assert await svc.configured() == expected


@pytest.mark.asyncio
async def test_put_invalidates_cache() -> None:
    # Long TTL: only invalidate-on-write makes the new value visible.
    svc = PlatformDynamicWorkerConfigService(
        store=InMemoryPlatformDynamicWorkerConfigStore(),
        env_default=_ENV_DEFAULT,
        ttl_seconds=9999.0,
    )
    assert await svc.effective() == _ENV_DEFAULT  # warm the cache (env default)
    await svc.put(max_concurrent=5, max_per_run=32, max_iterations=48, updated_by="admin")
    assert await svc.effective() == DynamicWorkerConfig(5, 32, 48)  # invalidate made it visible
