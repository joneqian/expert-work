"""Stream N — ``role_binding.platform_scope`` + ``tenant_id`` nullable.

Revision ID: 0035_role_binding_platform_scope
Revises: 0034_eval_dataset
Create Date: 2026-05-25

Stream N (Mini-ADR N-1):introduces the **platform-scope** binding for
``Role.SYSTEM_ADMIN`` (see ``docs/streams/STREAM-N-DESIGN.md``).

Schema deltas:

1. Add ``platform_scope BOOLEAN NOT NULL DEFAULT false`` —  existing rows
   default to tenant-scope, preserving behavior.
2. Make ``tenant_id`` nullable — platform-scope bindings hold NULL.
3. Add CHECK constraint enforcing the ``(platform_scope, tenant_id, role)``
   triple invariant at DB level.
4. Add partial UNIQUE index — each subject has at most one platform-scope
   binding (filter ``platform_scope = true``).

The existing UNIQUE constraint ``role_binding_subject_tenant_role_uniq``
on ``(subject_type, subject_id, tenant_id, role)`` remains. Postgres
treats NULL as distinct in UNIQUE, so it does not block multiple
platform-scope rows — that's why we add the dedicated partial index.

This migration is **metadata-only** on Postgres ≥ 11 (ADD COLUMN with a
constant DEFAULT does not rewrite the table); the ALTER COLUMN NULL is
also metadata-only. No data backfill is needed — existing rows already
have ``tenant_id IS NOT NULL`` and the new ``platform_scope`` defaults
to ``false``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035_role_binding_platform_scope"
down_revision: str | Sequence[str] | None = "0034_eval_dataset"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


_CHECK_BODY = (
    "(platform_scope = false AND tenant_id IS NOT NULL"
    " AND role IN ('admin','operator','viewer'))"
    " OR "
    "(platform_scope = true AND tenant_id IS NULL"
    " AND role = 'system_admin')"
)


def upgrade() -> None:
    # 1. Add the column with DEFAULT false (PG 11+ metadata-only).
    op.add_column(
        "role_binding",
        sa.Column(
            "platform_scope",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # 2. Relax tenant_id to nullable (metadata-only; platform-scope rows hold NULL).
    op.alter_column(
        "role_binding",
        "tenant_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=True,
    )

    # 3. CHECK constraint — DB-level invariant on the (platform_scope, tenant_id, role) triple.
    op.create_check_constraint(
        "role_binding_scope_triple_ck",
        "role_binding",
        _CHECK_BODY,
    )

    # 4. Partial UNIQUE index — each subject ≤ 1 platform-scope binding.
    op.create_index(
        "role_binding_subject_platform_uniq",
        "role_binding",
        ["subject_type", "subject_id"],
        unique=True,
        postgresql_where=sa.text("platform_scope = true"),
    )


def downgrade() -> None:
    op.drop_index("role_binding_subject_platform_uniq", table_name="role_binding")
    op.drop_constraint("role_binding_scope_triple_ck", "role_binding", type_="check")
    # Restore tenant_id NOT NULL. This will fail if platform-scope rows
    # exist with NULL tenant_id — operators must delete platform bindings
    # before downgrading.
    op.alter_column(
        "role_binding",
        "tenant_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.drop_column("role_binding", "platform_scope")
