"""Bootstrap the first platform ``system_admin`` — Stream P (Mini-ADR P-6).

The chicken-and-egg problem: minting a platform-scope role binding requires
``principal.is_system_admin`` (``api/role_bindings.py``), but
``resolve_system_admin`` only reports ``is_system_admin=True`` when such a
binding already exists. With an empty ``role_binding`` table nobody can grant
the first admin over the API.

This module is the **only** code path that breaks that loop. It writes one
platform-scope binding directly to the database, gated by infra-level access
(it needs ``EXPERT_WORK_DB_DSN`` to resolve to a writable DB — there is no
HTTP/JWT surface). Run it once per deployment from a controlled ops host:

.. code:: sh

    uv run python -m control_plane.bootstrap_admin --subject-id <keycloak-user-uuid>

After the first admin exists, every subsequent grant goes through the audited
``POST /v1/role_bindings`` API — this script is not needed again.

The write is idempotent per subject: re-running for a subject that already
holds a platform-scope binding is a no-op (exit 0). See
``docs/runbooks/bootstrap-admin.md`` for the end-to-end local recipe.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass
from uuid import UUID

from control_plane.settings import Settings
from control_plane.tenant_scope import bypass_rls_session
from expert_work.persistence import (
    DatabaseConfig,
    build_rls_sessionmaker,
    create_async_engine_from_config,
    create_async_session_factory,
)
from expert_work.persistence.auth import RoleBindingStore, SqlRoleBindingStore
from expert_work.protocol import Role, RoleBinding

logger = logging.getLogger("expert_work.control_plane.bootstrap_admin")

_GRANTED_BY = "bootstrap"


@dataclass(frozen=True)
class BootstrapResult:
    """Outcome of a bootstrap attempt.

    ``created`` is ``False`` when the subject already held a platform-scope
    binding (idempotent re-run) and ``True`` when a new one was written.
    """

    binding: RoleBinding
    created: bool


async def bootstrap_system_admin(
    store: RoleBindingStore,
    *,
    subject_id: UUID,
    subject_type: str = "user",
    granted_by: str = _GRANTED_BY,
) -> BootstrapResult:
    """Grant ``subject_id`` a platform-scope ``SYSTEM_ADMIN`` binding.

    Idempotent per subject: if the subject already holds a platform-scope
    binding it is returned unchanged with ``created=False``. The caller
    supplies the store so this is unit-testable against the in-memory store;
    the CLI wires the SQL store + RLS bypass below.

    All access runs inside :func:`bypass_rls_session` because ``role_binding``
    is RLS-managed and a CLI has no request-scoped ``app.tenant_id``.
    """
    async with bypass_rls_session():
        existing = await store.get_platform_admin_for_subject(
            subject_type=subject_type,
            subject_id=subject_id,
        )
        if existing is not None:
            return BootstrapResult(binding=existing, created=False)
        binding = await store.create(
            subject_type=subject_type,
            subject_id=subject_id,
            tenant_id=None,
            role=Role.SYSTEM_ADMIN,
            granted_by=granted_by,
            platform_scope=True,
        )
        return BootstrapResult(binding=binding, created=True)


async def _amain(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    dsn = args.dsn or settings.db_dsn

    engine = create_async_engine_from_config(
        DatabaseConfig(dsn=dsn, pgbouncer_mode=settings.db_pgbouncer_mode)
    )
    try:
        session_factory = build_rls_sessionmaker(create_async_session_factory(engine))
        store = SqlRoleBindingStore(session_factory)
        result = await bootstrap_system_admin(
            store,
            subject_id=args.subject_id,
            subject_type=args.subject_type,
        )
    finally:
        await engine.dispose()

    if result.created:
        logger.info(
            "granted platform system_admin: binding=%s subject=%s",
            result.binding.id,
            args.subject_id,
        )
        print(
            f"OK: created platform system_admin binding {result.binding.id} for {args.subject_id}"
        )
    else:
        logger.info("platform system_admin already exists for subject=%s", args.subject_id)
        print(f"OK (idempotent): {args.subject_id} already holds platform system_admin")
    return 0


def main() -> None:
    """CLI entrypoint — ``python -m control_plane.bootstrap_admin``."""
    parser = argparse.ArgumentParser(
        prog="python -m control_plane.bootstrap_admin",
        description="Grant the first platform system_admin (one-time bootstrap).",
    )
    parser.add_argument(
        "--subject-id",
        type=UUID,
        required=True,
        help="The user's UUID — the Keycloak token 'sub' claim, NOT the email.",
    )
    parser.add_argument(
        "--subject-type",
        default="user",
        choices=["user"],
        help="Only 'user' subjects can be platform admins in M0 (default: user).",
    )
    parser.add_argument(
        "--dsn",
        default=None,
        help="Override the DB DSN (default: Settings.db_dsn / EXPERT_WORK_DB_DSN).",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()
