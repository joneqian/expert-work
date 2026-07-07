"""Stream Q — encrypted-secret vault table (Mini-ADR Q-1/Q-2/Q-3).

Backs :class:`SqlEncryptedSecretStore`. Holds AES-256-GCM **ciphertext** of
secret values (never plaintext, Mini-ADR Q-6) so a platform admin can paste a
raw provider key in the web UI and have it stored encrypted at rest.

``tenant_id`` is NULLABLE (Mini-ADR Q-3): NULL = platform-global (this
iteration's only writer, accessed via ``bypass_rls_session()``); non-NULL =
tenant-scoped (reserved). RLS is enabled with an ``IS NOT DISTINCT FROM``
policy so future tenant rows are isolated by default while NULL (platform)
rows remain reachable under the bypass path.

The "one current version per (tenant, name)" invariant uses a partial unique
index over ``COALESCE(tenant_id, <zero-uuid>)`` because Postgres treats NULLs
as distinct — a plain ``UNIQUE (tenant_id, name) WHERE is_current`` would NOT
prevent two current platform (NULL-tenant) rows for the same name (risk #2).

Revision ID: 0050_encrypted_secret   (21 chars; within the 32-char
``version_num`` ceiling per [memory:alembic-revision-id-32-chars]).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0050_encrypted_secret"
down_revision: str | Sequence[str] | None = "0049_platform_secrets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "encrypted_secret"
_POLICY = "encrypted_secret_tenant_isolation"
_ZERO_UUID = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("kek_version", sa.Text(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("created_by", sa.Text(), nullable=False),
    )
    op.create_unique_constraint(
        "uq_encrypted_secret_name_version",
        _TABLE,
        ["tenant_id", "name", "version"],
    )
    # One current version per (tenant, name). COALESCE collapses NULL tenant_id
    # to a sentinel so platform (NULL) rows actually collide (NULLs are
    # otherwise distinct in a unique index).
    op.execute(
        f"CREATE UNIQUE INDEX uq_encrypted_secret_current ON {_TABLE} "
        f"(COALESCE(tenant_id, '{_ZERO_UUID}'::uuid), name) WHERE is_current"
    )
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"CREATE POLICY {_POLICY} ON {_TABLE} "
        "USING (tenant_id IS NOT DISTINCT FROM "
        "NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id IS NOT DISTINCT FROM "
        "NULLIF(current_setting('app.tenant_id', true), '')::uuid);"
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON {_TABLE};")
    op.drop_table(_TABLE)
