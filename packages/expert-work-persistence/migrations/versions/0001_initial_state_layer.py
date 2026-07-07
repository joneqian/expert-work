"""Initial state layer — event_log + thread_meta + audit_log.

Revision ID: 0001_initial_state_layer
Revises:
Create Date: 2026-05-11

Implements Stream A.1 per ADR-0002. RLS is intentionally NOT enabled here;
Stream C.4 adds the policies once Stream B/C have wired tenant context.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_state_layer"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Alembic loads the above names by module reflection; declare them as the
# public contract so CodeQL / mypy don't flag them as unused.
__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.create_table(
        "event_log",
        sa.Column("id", sa.BigInteger(), nullable=False, autoincrement=True),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("thread_id", "seq", name="event_log_thread_seq_unique"),
    )
    op.create_index("event_log_thread_seq_idx", "event_log", ["thread_id", "seq"])
    op.create_index("event_log_tenant_created_idx", "event_log", ["tenant_id", "created_at"])

    op.create_table(
        "thread_meta",
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column("agent_name", sa.Text(), nullable=True),
        sa.Column("agent_version", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("thread_id"),
    )
    op.create_index("thread_meta_tenant_status_idx", "thread_meta", ["tenant_id", "status"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), nullable=False, autoincrement=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("on_behalf_of", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=False),
        sa.Column("resource_id", sa.Text(), nullable=True),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("audit_log_tenant_time_idx", "audit_log", ["tenant_id", "occurred_at"])
    op.create_index(
        "audit_log_actor_idx",
        "audit_log",
        ["tenant_id", "actor_type", "actor_id", "occurred_at"],
    )
    op.create_index(
        "audit_log_resource_idx",
        "audit_log",
        ["tenant_id", "resource_type", "resource_id", "occurred_at"],
    )
    op.create_index(
        "audit_log_action_idx",
        "audit_log",
        ["tenant_id", "action", "occurred_at"],
    )
    op.create_index("audit_log_request_idx", "audit_log", ["request_id"])


def downgrade() -> None:
    op.drop_index("audit_log_request_idx", table_name="audit_log")
    op.drop_index("audit_log_action_idx", table_name="audit_log")
    op.drop_index("audit_log_resource_idx", table_name="audit_log")
    op.drop_index("audit_log_actor_idx", table_name="audit_log")
    op.drop_index("audit_log_tenant_time_idx", table_name="audit_log")
    op.drop_table("audit_log")

    op.drop_index("thread_meta_tenant_status_idx", table_name="thread_meta")
    op.drop_table("thread_meta")

    op.drop_index("event_log_tenant_created_idx", table_name="event_log")
    op.drop_index("event_log_thread_seq_idx", table_name="event_log")
    op.drop_table("event_log")
