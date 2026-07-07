"""Smoke tests for ``expert_work.common.context``.

The contextvars previously lived in ``expert_work.runtime.context``; this
test fixes the API at the new location so the move is visible to CI.
The runtime re-export is exercised by the existing
``packages/expert-work-runtime/tests/test_context.py`` unchanged.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from expert_work.common.context import (
    get_current_tenant,
    get_current_trace_id,
    require_current_tenant,
    reset_current_tenant,
    reset_current_trace_id,
    set_current_tenant,
    set_current_trace_id,
)


def test_tenant_set_get_reset() -> None:
    tenant = UUID("00000000-0000-0000-0000-000000000001")
    assert get_current_tenant() is None
    token = set_current_tenant(tenant)
    try:
        assert get_current_tenant() == tenant
    finally:
        reset_current_tenant(token)
    assert get_current_tenant() is None


def test_require_current_tenant_raises_when_unset() -> None:
    with pytest.raises(RuntimeError, match="tenant context not set"):
        require_current_tenant()


def test_trace_id_set_get_reset() -> None:
    assert get_current_trace_id() is None
    token = set_current_trace_id("abc123")
    try:
        assert get_current_trace_id() == "abc123"
    finally:
        reset_current_trace_id(token)
    assert get_current_trace_id() is None
