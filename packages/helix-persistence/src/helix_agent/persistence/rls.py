"""Row-level security session wiring — Stream C.4.

Postgres RLS policies (migration ``0005_rls_baseline``) compare
``tenant_id`` against ``current_setting('app.tenant_id', true)::uuid``.
The application code must set that GUC variable on every transaction
**before** the first ``SELECT``/``INSERT`` runs, otherwise the policy
denies everything (``current_setting(..., true)`` returns ``''`` →
``::uuid`` cast errors out → policy evaluates to ``false``).

Design intent (STREAM-C-DESIGN § 2.6):

* The tenant id is carried by a :class:`~contextvars.ContextVar` set
  by :class:`control_plane.tenancy.RLSContextMiddleware`. ContextVar
  is inherited by asyncio tasks spawned within the request scope, so
  it flows correctly through SQLAlchemy's awaited operations.

* :func:`build_rls_sessionmaker` is the public entry point that opts
  a given :class:`sqlalchemy.ext.asyncio.async_sessionmaker` into the
  RLS wiring. Idempotent — calling it twice is a no-op. Under the
  hood the listener is attached to the base :class:`sqlalchemy.orm.Session`
  class so every session participates; the listener itself is a
  no-op when the ContextVar is unset, which keeps tests that don't
  care about RLS unaffected.

* The wrapped factory is otherwise indistinguishable from the
  original: existing SQL stores keep their ``async with self._sf()
  as session`` pattern with no code changes. RLS is transparent to
  the store layer.

PgBouncer compatibility:

* ``SET LOCAL`` is bound to the current transaction and reset on
  ``COMMIT`` / ``ROLLBACK``. Transaction-mode pooling is therefore
  safe — the server-side connection returned to PgBouncer carries no
  residual ``app.tenant_id`` value.

* ``set_config(name, value, is_local=true)`` is used rather than
  ``SET LOCAL app.tenant_id = '<uuid>'`` because the former accepts
  bind parameters; the latter cannot use placeholders, which would
  require building SQL by string interpolation and a separate UUID
  validator.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Final
from uuid import UUID

from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session, SessionTransaction

__all__ = [
    "RLS_GUC_NAME",
    "RLS_USER_GUC_NAME",
    "build_rls_sessionmaker",
    "bypass_rls_var",
    "current_tenant_id_var",
    "current_user_id_var",
]


RLS_GUC_NAME: Final[str] = "app.tenant_id"

#: Stream J.3 — the user-level GUC. Per-user data tables (memory_item,
#: and later workspace / artifact) carry a ``user_id`` predicate on top
#: of tenant isolation (Mini-ADR J-1, defence in depth).
RLS_USER_GUC_NAME: Final[str] = "app.user_id"

# Set by the per-request middleware; cleared on response. Default
# ``None`` means "no tenant scoped", which makes every read return
# zero rows under RLS — that's the desired fail-closed behaviour.
current_tenant_id_var: ContextVar[UUID | None] = ContextVar(
    "helix.rls.tenant_id",
    default=None,
)

# Stream J.3 — set alongside the tenant var for per-user data access.
# ``None`` → ``app.user_id`` is not emitted; a query against a
# user-scoped table (memory_item) then sees zero rows (fail-closed).
current_user_id_var: ContextVar[UUID | None] = ContextVar(
    "helix.rls.user_id",
    default=None,
)

# Explicit opt-out for admin paths that want to ``SET ROLE`` to a
# BYPASSRLS role (e.g. ``audit_reader``). When ``True``, the
# ``after_begin`` listener skips emitting ``set_config`` so the admin
# session can manage its own role with no interference.
bypass_rls_var: ContextVar[bool] = ContextVar(
    "helix.rls.bypass",
    default=False,
)

# Module-level flag: do we have the listener attached to ``Session``?
# A list (not a bool) so it works under hot reloads / test imports
# where the module body is re-evaluated but the SQLAlchemy event
# registry survives.
_LISTENER_INSTALLED: list[bool] = []


def _emit_set_config(connection: Connection, name: str, value: str) -> None:
    """Run ``SELECT set_config(name, value, true)`` on ``connection``."""
    connection.execute(
        text("SELECT set_config(:name, :value, true)"),
        {"name": name, "value": value},
    )


def _rls_after_begin(
    _session: Session,
    _transaction: SessionTransaction,
    connection: Connection,
) -> None:
    """``after_begin`` listener — emits ``SET LOCAL`` from the ContextVars.

    Emits ``app.tenant_id`` and — for per-user data tables (Stream J.3)
    — ``app.user_id``. An unset var means the GUC is not emitted, so
    RLS on that axis fails closed (``NULLIF→NULL`` → row denied).
    """
    if bypass_rls_var.get():
        return
    tenant_id = current_tenant_id_var.get()
    if tenant_id is not None:
        _emit_set_config(connection, RLS_GUC_NAME, str(tenant_id))
    user_id = current_user_id_var.get()
    if user_id is not None:
        _emit_set_config(connection, RLS_USER_GUC_NAME, str(user_id))


def _install_listener_once() -> None:
    if _LISTENER_INSTALLED:
        return
    if event.contains(Session, "after_begin", _rls_after_begin):
        # Defensive: another import path already attached us.
        _LISTENER_INSTALLED.append(True)
        return
    event.listen(Session, "after_begin", _rls_after_begin)
    _LISTENER_INSTALLED.append(True)


def build_rls_sessionmaker(
    base: async_sessionmaker[AsyncSession],
) -> async_sessionmaker[AsyncSession]:
    """Opt ``base`` into the RLS ``after_begin`` listener (idempotent).

    The listener is attached at module level on the global
    :class:`sqlalchemy.orm.Session` class — every ``async_sessionmaker``
    in the process therefore participates automatically once any
    factory has been wrapped. The function still exists so callers
    declare intent explicitly and so tests can build factories that
    are *not* wrapped if they need to bypass RLS at the SQLAlchemy
    layer (e.g. low-level migration helpers).
    """
    _install_listener_once()
    return base
