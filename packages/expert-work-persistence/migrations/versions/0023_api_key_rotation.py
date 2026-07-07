"""Stream K.K1 — api_key.rotated_at / grace_period_s for double-active rotation.

Revision ID: 0023_api_key_rotation
Revises: 0022_knowledge_chunking_hybrid
Create Date: 2026-05-20

Mini-ADR K-1 (STREAM-K-DESIGN § 4): API-key rotation uses a double-active
window — the old key keeps verifying until ``rotated_at + grace_period_s``
elapses, so clients have time to swap to the new bearer without an outage.
Immediate revocation stays available via ``DELETE /v1/api_keys/{id}``;
``rotated_at = NULL`` means the row is in its normal pre-rotation state.

Both columns are nullable — every existing row stays in the "never
rotated" state without a backfill. The verifier and ``is_active``
helpers treat NULL ``rotated_at`` exactly as before.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_api_key_rotation"
down_revision: str | Sequence[str] | None = "0022_knowledge_chunking_hybrid"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.add_column(
        "api_key",
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "api_key",
        sa.Column("grace_period_s", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("api_key", "grace_period_s")
    op.drop_column("api_key", "rotated_at")
