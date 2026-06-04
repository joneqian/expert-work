"""Stream X (X-1/X-2) — platform (NULL-tenant) skill library.

Evolves the ``skill`` / ``skill_version`` tables (migration 0029) so the
platform-curated skill library can live as NULL-tenant rows in the SAME
tables, mirroring the ``encrypted_secret`` (0050) / ``mcp_connector_catalog``
(0055) NULL-tenant pattern.

Changes (Mini-ADR X-1):

* ``skill.tenant_id`` / ``skill_version.tenant_id`` → NULLABLE
  (``NULL`` = platform-global; non-NULL = tenant-owned).
* RLS swaps the strict-equality policies for ``IS NOT DISTINCT FROM
  NULLIF(current_setting('app.tenant_id', true), '')::uuid``. For any
  non-NULL ``tenant_id`` this is byte-for-byte equivalent to the old
  ``= current_setting(...)::uuid`` — tenant isolation is unchanged. NULL
  (platform) rows are visible ONLY when ``app.tenant_id`` is unset (the
  ``bypass_rls_session()`` path); a tenant-scoped session sees ZERO
  platform rows (the X-8 / W-8 isolation property).
* The ``UNIQUE (tenant_id, name)`` constraint is dropped and replaced with
  a unique index over ``COALESCE(tenant_id, <zero-uuid>), name`` because
  Postgres treats NULLs as distinct — a plain unique constraint would NOT
  prevent two platform (NULL-tenant) skills sharing a name.

Mini-ADR X-2:

* ``skill.required_tier`` (CHECK ``free`` / ``pro`` / ``enterprise``,
  default ``'free'``) — the minimum plan tier a tenant needs to bind a
  platform skill. Gated at bind/list time via ``tier_satisfies``.

``downgrade()`` reverses everything (drop required_tier + index → restore
the strict-equality policies + UNIQUE constraint + NOT NULL columns). It
REQUIRES that no NULL-tenant (platform) rows exist — the NOT NULL re-add
would otherwise fail.

Revision ID: 0057_platform_skill   (19 chars; within the 32-char
``version_num`` ceiling per [memory:alembic-revision-id-32-chars]).
Revises: 0056_mcp_catalog_columns
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0057_platform_skill"
down_revision: str | Sequence[str] | None = "0056_mcp_catalog_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_ZERO_UUID = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    # ── tenant_id → NULLABLE (NULL = platform) ───────────────────────────
    op.alter_column(
        "skill",
        "tenant_id",
        existing_type=sa.dialects.postgresql.UUID(),
        nullable=True,
    )
    op.alter_column(
        "skill_version",
        "tenant_id",
        existing_type=sa.dialects.postgresql.UUID(),
        nullable=True,
    )

    # ── required_tier (Mini-ADR X-2) ─────────────────────────────────────
    op.add_column(
        "skill",
        sa.Column("required_tier", sa.Text(), nullable=False, server_default=sa.text("'free'")),
    )
    op.create_check_constraint(
        "skill_required_tier_check",
        "skill",
        "required_tier IN ('free', 'pro', 'enterprise')",
    )

    # ── COALESCE unique index replaces UNIQUE(tenant_id, name) ────────────
    # Postgres treats NULLs as distinct, so a plain UNIQUE(tenant_id, name)
    # would NOT collide two platform (NULL-tenant) rows sharing a name.
    op.drop_constraint("skill_tenant_name_uq", "skill", type_="unique")
    op.execute(
        f"CREATE UNIQUE INDEX skill_tenant_name_uniq ON skill "
        f"(COALESCE(tenant_id, '{_ZERO_UUID}'::uuid), name)"
    )

    # ── RLS swap: strict equality → IS NOT DISTINCT FROM ─────────────────
    # For non-NULL tenant_id this is mathematically identical to the old
    # policy, so tenant isolation is unchanged; NULL (platform) rows become
    # reachable only under the bypass path (app.tenant_id unset).
    # NOTE: deliberately NO ``FORCE ROW LEVEL SECURITY`` — keep the 0029
    # ENABLE-only posture. The ``skill`` curator (skill_curator.py
    # ``run_once`` → ``curator_distinct_tenant_ids`` → per-tenant sweep) reads
    # ACROSS all tenants WITHOUT setting ``app.tenant_id`` and WITHOUT a
    # SET ROLE — there is no BYPASSRLS role granted on skill (``audit_reader``
    # from 0005 covers only audit_log/event_log/thread_meta). So that
    # cross-tenant read works ONLY because the worker connection is the table
    # OWNER, and a Postgres owner is exempt from RLS *only while the table is
    # ENABLE-only* (not FORCE). Adding FORCE would subject the owner to the
    # ``IS NOT DISTINCT FROM NULL`` policy under the unset GUC → it would then
    # match ONLY platform (NULL) rows and silently stop sweeping tenant skills.
    # Platform-row visibility (X-1) does NOT need FORCE — the policy expression
    # alone delivers it. (Hardening skill to FORCE is a separate, deliberate
    # change that must first rework the curator's bypass — e.g. grant a
    # BYPASSRLS role on skill + SET ROLE — and add a real-PG curator test.)
    op.execute("DROP POLICY IF EXISTS skill_version_tenant_isolation ON skill_version")
    op.execute("DROP POLICY IF EXISTS skill_tenant_isolation ON skill")
    op.execute("ALTER TABLE skill ENABLE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY skill_tenant_isolation ON skill "
        "USING (tenant_id IS NOT DISTINCT FROM "
        "NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id IS NOT DISTINCT FROM "
        "NULLIF(current_setting('app.tenant_id', true), '')::uuid);"
    )
    op.execute("ALTER TABLE skill_version ENABLE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY skill_version_tenant_isolation ON skill_version "
        "USING (tenant_id IS NOT DISTINCT FROM "
        "NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id IS NOT DISTINCT FROM "
        "NULLIF(current_setting('app.tenant_id', true), '')::uuid);"
    )


def downgrade() -> None:
    # Requires no NULL-tenant (platform) rows to exist — the NOT NULL re-add
    # below fails otherwise. Operators must delete platform skills first.

    # Restore strict-equality policies (matches the 0029 ENABLE-only shape).
    op.execute("DROP POLICY IF EXISTS skill_version_tenant_isolation ON skill_version")
    op.execute("DROP POLICY IF EXISTS skill_tenant_isolation ON skill")
    op.execute(
        "CREATE POLICY skill_tenant_isolation ON skill "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )
    op.execute(
        "CREATE POLICY skill_version_tenant_isolation ON skill_version "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )

    # Restore UNIQUE(tenant_id, name).
    op.execute("DROP INDEX IF EXISTS skill_tenant_name_uniq")
    op.create_unique_constraint("skill_tenant_name_uq", "skill", ["tenant_id", "name"])

    # Drop required_tier (+ its CHECK).
    op.drop_constraint("skill_required_tier_check", "skill", type_="check")
    op.drop_column("skill", "required_tier")

    # tenant_id → NOT NULL.
    op.alter_column(
        "skill_version",
        "tenant_id",
        existing_type=sa.dialects.postgresql.UUID(),
        nullable=False,
    )
    op.alter_column(
        "skill",
        "tenant_id",
        existing_type=sa.dialects.postgresql.UUID(),
        nullable=False,
    )
