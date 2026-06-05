"""Unit tests for :class:`PgWorkspaceLock` SQL — Stream TE-8.

These assert the *statements* the lock emits (no live DB), pinning the two
correctness fixes that a live test can't cheaply prove:

- ``SET LOCAL idle_in_transaction_session_timeout`` / ``statement_timeout``
  raised above the max write exec, so the per-DB short-DML defaults can't
  kill the lock txn mid-write and release the advisory lock early (C1/H1);
- the *two-arg* ``pg_advisory_xact_lock(classid, hashtext(key))`` key space,
  structurally disjoint from event_log's single-arg space (M1);
- an ephemeral workspace (``user_id=None``) takes no lock at all.

Cross-session mutual exclusion against a real Postgres lives in
``test_workspace_lock_integration.py``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from control_plane.workspace_lock import PgWorkspaceLock


class _RecordingSession:
    def __init__(self) -> None:
        self.executed: list[tuple[str, dict[str, Any] | None]] = []

    async def execute(self, clause: Any, params: dict[str, Any] | None = None) -> None:
        self.executed.append((str(clause), params))

    @asynccontextmanager
    async def begin(self) -> Any:
        yield


class _FakeSessionFactory:
    """Mimics ``async_sessionmaker``: calling it returns an async context
    manager yielding the recording session."""

    def __init__(self, session: _RecordingSession) -> None:
        self._session = session

    def __call__(self) -> Any:
        session = self._session

        @asynccontextmanager
        async def _cm() -> Any:
            yield session

        return _cm()


async def test_acquire_emits_set_local_timeouts_and_namespaced_key() -> None:
    session = _RecordingSession()
    lock = PgWorkspaceLock(_FakeSessionFactory(session))  # type: ignore[arg-type]
    tenant, user = uuid4(), uuid4()

    async with lock.acquire(tenant_id=tenant, user_id=user):
        pass

    sqls = [sql for sql, _ in session.executed]
    # Both DB short-DML timeouts overridden above the 300s bash ceiling (C1/H1).
    assert any("idle_in_transaction_session_timeout = 360000" in s for s in sqls)
    assert any("statement_timeout = 360000" in s for s in sqls)
    # Two-arg advisory key space, namespaced + keyed on {tenant}:{user} (M1).
    lock_calls = [(s, p) for s, p in session.executed if "pg_advisory_xact_lock" in s]
    assert len(lock_calls) == 1
    sql, params = lock_calls[0]
    assert "pg_advisory_xact_lock(:classid, hashtext(:k))" in sql
    assert params == {"classid": 1, "k": f"{tenant}:{user}"}


async def test_acquire_ephemeral_takes_no_lock() -> None:
    session = _RecordingSession()
    lock = PgWorkspaceLock(_FakeSessionFactory(session))  # type: ignore[arg-type]

    async with lock.acquire(tenant_id=uuid4(), user_id=None):
        pass

    assert session.executed == []  # no transaction, no lock for ephemeral
