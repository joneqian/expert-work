"""SE-16 live pilot finding #6 — backfill empty ``skill_version.content_hash``.

The evolution processor's ``add_version`` never passed ``content_hash``, so
every distilled version landed with the ``b""`` default. The U-21 drift check
(``skill_seed`` / ``skill_view`` recompute-and-compare) therefore dropped every
distilled skill at load time — attached but unusable. The processor now hashes
at persist time; this migration repairs the rows written before the fix by
recomputing ``blake2b(canonical(prompt_fragment, supporting_files))`` in place.

Revision ID: 0111_backfill_skill_hash
Revises: 0110_token_usage_kind
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from helix_agent.protocol.skill import compute_content_hash

revision: str = "0111_backfill_skill_hash"
down_revision: str | Sequence[str] | None = "0110_token_usage_kind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, prompt_fragment, supporting_files FROM skill_version "
            "WHERE length(content_hash) = 0"
        )
    ).fetchall()
    for row in rows:
        digest = compute_content_hash(row.prompt_fragment, row.supporting_files or {})
        bind.execute(
            sa.text("UPDATE skill_version SET content_hash = :h WHERE id = :id"),
            {"h": digest, "id": row.id},
        )


def downgrade() -> None:
    # The empty-hash state was a defect, not a schema shape — nothing to revert.
    pass
