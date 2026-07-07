"""Unit tests for ``expert_work.runtime.context``."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from expert_work.runtime.context import (
    get_current_tenant,
    get_current_trace_id,
    require_current_tenant,
    reset_current_tenant,
    reset_current_trace_id,
    set_current_tenant,
    set_current_trace_id,
)


def test_tenant_default_is_none() -> None:
    assert get_current_tenant() is None


def test_tenant_set_get_reset_round_trip() -> None:
    tenant = uuid4()
    token = set_current_tenant(tenant)
    try:
        assert get_current_tenant() == tenant
    finally:
        reset_current_tenant(token)
    assert get_current_tenant() is None


def test_require_current_tenant_raises_when_unset() -> None:
    with pytest.raises(RuntimeError, match="tenant context not set"):
        require_current_tenant()


def test_require_current_tenant_returns_when_set() -> None:
    tenant = uuid4()
    token = set_current_tenant(tenant)
    try:
        assert require_current_tenant() == tenant
    finally:
        reset_current_tenant(token)


@pytest.mark.asyncio
async def test_tenant_is_task_local() -> None:
    """Different asyncio tasks see independent contexts."""
    tenant_a, tenant_b = uuid4(), uuid4()
    seen: list[tuple[str, object]] = []

    async def task_a() -> None:
        token = set_current_tenant(tenant_a)
        try:
            await asyncio.sleep(0.01)
            seen.append(("a", get_current_tenant()))
        finally:
            reset_current_tenant(token)

    async def task_b() -> None:
        token = set_current_tenant(tenant_b)
        try:
            await asyncio.sleep(0.02)
            seen.append(("b", get_current_tenant()))
        finally:
            reset_current_tenant(token)

    await asyncio.gather(task_a(), task_b())
    by_label = dict(seen)
    assert by_label["a"] == tenant_a
    assert by_label["b"] == tenant_b


def test_trace_id_set_get_reset() -> None:
    assert get_current_trace_id() is None
    token = set_current_trace_id("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    try:
        assert get_current_trace_id() == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    finally:
        reset_current_trace_id(token)
    assert get_current_trace_id() is None
