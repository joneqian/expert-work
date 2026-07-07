"""P1-S2.1 — ``eval_run`` + ``eval_case_result`` tables (eval platform/ops layer).

The capability-eval *engine* (``tools/eval/run_baseline.py``) already runs
green; what was missing is the production *ops* layer — a durable record of
each eval run + its per-case results that a resident ``EvalWorker`` (S2.1b)
populates. These two tables are that record:

* ``eval_run`` — one execution of a suite (``m0_baseline`` / ``adversarial``
  / a single capability). Carries the status machine + a JSON summary.
* ``eval_case_result`` — one case outcome under a run. ``session_id`` +
  ``session_metrics`` back the S2.2 session-level metrics (resolution rate).

Both are tenant-scoped with the standard ``current_setting('app.tenant_id')``
GUC RLS, ENABLE + FORCE (same posture as ``token_usage`` / ``artifact``).
The cross-tenant worker scan uses ``bypass_rls`` (like the memory
consolidator), never a relaxed policy.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0076_eval_run"
down_revision: str | Sequence[str] | None = "0075_webhook_secret_ref"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_STATUS_VALUES = "('queued', 'running', 'passed', 'failed', 'error')"
_TRIGGER_VALUES = "('manual', 'ci', 'schedule')"


def upgrade() -> None:
    op.create_table(
        "eval_run",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("suite", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("triggered_by", sa.Text(), nullable=False),
        sa.Column("summary", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"status IN {_STATUS_VALUES}", name="eval_run_status_valid"),
        sa.CheckConstraint(
            f"triggered_by IN {_TRIGGER_VALUES}", name="eval_run_triggered_by_valid"
        ),
    )
    op.create_index("ix_eval_run_tenant_id", "eval_run", ["tenant_id"])
    # Partial index — the worker's claim scan touches only queued rows.
    op.create_index(
        "ix_eval_run_queued",
        "eval_run",
        ["created_at"],
        postgresql_where=sa.text("status = 'queued'"),
    )

    op.create_table(
        "eval_case_result",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("capability", sa.Text(), nullable=False),
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column("scores", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("session_metrics", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["eval_run.id"], name="eval_case_result_run_fk", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_eval_case_result_run_id", "eval_case_result", ["run_id"])
    op.create_index("ix_eval_case_result_tenant_id", "eval_case_result", ["tenant_id"])

    for table in ("eval_run", "eval_case_result"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            "USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )


def downgrade() -> None:
    for table in ("eval_case_result", "eval_run"):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.drop_index("ix_eval_case_result_tenant_id", table_name="eval_case_result")
    op.drop_index("ix_eval_case_result_run_id", table_name="eval_case_result")
    op.drop_table("eval_case_result")
    op.drop_index("ix_eval_run_queued", table_name="eval_run")
    op.drop_index("ix_eval_run_tenant_id", table_name="eval_run")
    op.drop_table("eval_run")
