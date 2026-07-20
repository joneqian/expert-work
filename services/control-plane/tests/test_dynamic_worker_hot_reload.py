from __future__ import annotations

from typing import Any

import pytest

from control_plane.platform_dynamic_worker_config import (
    DynamicWorkerConfig,
    PlatformDynamicWorkerConfigService,
)
from control_plane.runtime import AgentRuntime
from control_plane.subagent_runtime import _resolve_worker_max_iterations
from expert_work.persistence.platform_dynamic_worker_config import (
    InMemoryPlatformDynamicWorkerConfigStore,
)
from expert_work.protocol import AgentSpec
from expert_work.runtime.runs import RunManager
from expert_work.runtime.stream_bridge import InMemoryStreamBridge


async def _stub_agent_builder(
    spec: AgentSpec, *, tenant_id: object = None, user_id: str | None = None
) -> object:
    return object()


def _runtime(**kwargs: Any) -> AgentRuntime:
    """Minimal AgentRuntime construction — mirrors ``test_runtime.py``'s
    shape for the dataclass's required fields (``run_manager`` /
    ``stream_bridge`` / ``agent_builder`` have no defaults)."""
    return AgentRuntime(
        run_manager=RunManager(store=None),  # type: ignore[arg-type]
        stream_bridge=InMemoryStreamBridge(),
        agent_builder=_stub_agent_builder,  # type: ignore[arg-type]
        **kwargs,
    )


def _service() -> PlatformDynamicWorkerConfigService:
    return PlatformDynamicWorkerConfigService(
        store=InMemoryPlatformDynamicWorkerConfigStore(),
        env_default=DynamicWorkerConfig(max_concurrent=3, max_per_run=16, max_iterations=32),
        ttl_seconds=0.0,
    )


@pytest.mark.asyncio
async def test_spawn_budget_hot_reloads_between_runs() -> None:
    svc = _service()
    runtime = _runtime(dynamic_workers_enabled=True, dynamic_worker_config_service=svc)
    first = await runtime.new_worker_spawn_budget()
    assert (first.max_per_run, first.max_concurrent) == (16, 3)
    await svc.put(max_concurrent=5, max_per_run=32, max_iterations=48, updated_by="admin")
    second = await runtime.new_worker_spawn_budget()
    assert (second.max_per_run, second.max_concurrent) == (32, 5)


@pytest.mark.asyncio
async def test_spawn_budget_falls_back_to_attrs_without_service() -> None:
    runtime = _runtime(
        dynamic_workers_enabled=True,
        dynamic_worker_max_concurrent=2,
        dynamic_worker_max_per_run=8,
    )
    budget = await runtime.new_worker_spawn_budget()
    assert (budget.max_per_run, budget.max_concurrent) == (8, 2)


@pytest.mark.asyncio
async def test_worker_max_iterations_hot_reloads() -> None:
    svc = _service()
    assert await _resolve_worker_max_iterations(svc, 32) == 32
    await svc.put(max_concurrent=3, max_per_run=16, max_iterations=48, updated_by="admin")
    assert await _resolve_worker_max_iterations(svc, 32) == 48
    assert await _resolve_worker_max_iterations(None, 24) == 24
