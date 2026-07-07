"""Stream HX-2 — feedback→learning loop columns.

Two halves of STREAM-HX-DESIGN § 3:

* ``feedback.processed_at`` (TIMESTAMPTZ, nullable) — the
  ``FeedbackConsumerWorker``'s row-level consumption stamp (Mini-ADR
  HX-B1). NULL ↔ not yet consumed; the partial index below makes the
  worker's cross-tenant scan (``rating='down' AND processed_at IS
  NULL``) an index-only probe.
* ``memory_item.review_flagged_at`` (TIMESTAMPTZ, nullable) — set by
  the worker on memories extracted from a 👎-rated thread (matched via
  ``source_thread_id``); the MemoryConsolidator's SUB-PASS 2 picks
  flagged items up for the U-37 single-item review regardless of age
  (Mini-ADR HX-B3).

Also grants ``audit_reader`` SELECT on ``feedback``: the worker's scan
is cross-tenant and ``feedback`` is FORCE-RLS, so the read must run
under ``SET LOCAL ROLE audit_reader`` (BYPASSRLS) — the exact ledger
precedent (``0061_ledger_audit_reader_grant``). All *writes* (the
``processed_at`` stamp, the memory flag) stay per-tenant-scoped.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0071_hx2_feedback_loop"
down_revision: str | Sequence[str] | None = "0070_skill_pred_verdict"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "feedback",
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Worker scan path: unprocessed 👎 rows, oldest first.
    op.create_index(
        "ix_feedback_unprocessed_down",
        "feedback",
        ["id"],
        postgresql_where=sa.text("rating = 'down' AND processed_at IS NULL"),
    )
    op.execute("GRANT SELECT ON TABLE feedback TO audit_reader;")

    op.add_column(
        "memory_item",
        sa.Column("review_flagged_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Consolidator pickup path: flagged live items per (tenant, user).
    op.create_index(
        "ix_memory_item_review_flagged",
        "memory_item",
        ["tenant_id", "user_id"],
        postgresql_where=sa.text("review_flagged_at IS NOT NULL AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_memory_item_review_flagged", table_name="memory_item")
    op.drop_column("memory_item", "review_flagged_at")
    op.execute("REVOKE SELECT ON TABLE feedback FROM audit_reader;")
    op.drop_index("ix_feedback_unprocessed_down", table_name="feedback")
    op.drop_column("feedback", "processed_at")
