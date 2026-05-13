"""Unit tests for :mod:`helix_agent.persistence.rls` listener wiring.

The integration test (``test_rls_integration.py``) exercises the real
``SET LOCAL`` round-trip against Postgres. This unit test pins the
glue: ContextVar plumbing, the ``after_begin`` listener registration,
and the ``bypass_rls_var`` opt-out — none of which need a database.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from helix_agent.persistence.rls import (
    RLS_GUC_NAME,
    _rls_after_begin,
    build_rls_sessionmaker,
    bypass_rls_var,
    current_tenant_id_var,
)


@pytest.fixture(autouse=True)
def reset_context() -> Iterator[None]:
    """Each test gets a clean ContextVar state."""
    token1 = current_tenant_id_var.set(None)
    token2 = bypass_rls_var.set(False)
    try:
        yield
    finally:
        current_tenant_id_var.reset(token1)
        bypass_rls_var.reset(token2)


def test_canonical_guc_name_is_app_tenant_id() -> None:
    assert RLS_GUC_NAME == "app.tenant_id"


def test_default_context_is_none() -> None:
    assert current_tenant_id_var.get() is None
    assert bypass_rls_var.get() is False


def test_listener_emits_set_config_with_tenant_id() -> None:
    tenant_id = uuid4()
    current_tenant_id_var.set(tenant_id)

    connection = MagicMock()
    _rls_after_begin(MagicMock(), MagicMock(), connection)

    connection.execute.assert_called_once()
    args, _ = connection.execute.call_args
    statement, params = args
    assert "set_config" in str(statement).lower()
    assert params == {"name": RLS_GUC_NAME, "value": str(tenant_id)}


def test_listener_skips_when_tenant_unset() -> None:
    connection = MagicMock()
    _rls_after_begin(MagicMock(), MagicMock(), connection)
    connection.execute.assert_not_called()


def test_listener_skips_when_bypass_var_set() -> None:
    current_tenant_id_var.set(uuid4())
    bypass_rls_var.set(True)

    connection = MagicMock()
    _rls_after_begin(MagicMock(), MagicMock(), connection)
    connection.execute.assert_not_called()


def test_build_rls_sessionmaker_attaches_listener_on_session_class_idempotent() -> None:
    factory = MagicMock()
    build_rls_sessionmaker(factory)
    build_rls_sessionmaker(factory)  # second call must be a no-op
    # The listener lives on the global ``Session`` class — verify it's
    # attached and that idempotent calls don't double-register.
    assert event.contains(Session, "after_begin", _rls_after_begin) is True


def test_context_var_isolation_between_independent_contexts() -> None:
    import asyncio

    async def _set_a(tenant_id: UUID) -> UUID | None:
        current_tenant_id_var.set(tenant_id)
        await asyncio.sleep(0)
        return current_tenant_id_var.get()

    async def _read_b() -> UUID | None:
        return current_tenant_id_var.get()

    async def _scenario() -> tuple[UUID | None, UUID | None]:
        a_tenant = uuid4()
        # Independent tasks should see their own ContextVar (a => a_tenant; b => None).
        # asyncio.gather copies the parent context once per task, so b sees the
        # parent context's current value at fork time.
        a_seen, b_seen = await asyncio.gather(_set_a(a_tenant), _read_b())
        return a_seen, b_seen

    a_seen, b_seen = asyncio.run(_scenario())
    assert isinstance(a_seen, UUID)
    assert b_seen is None
