"""4.4 (#5) — curation_candidate.evolved_at (SE-6 evolution marker).

Adds a nullable ``evolved_at`` timestamp so the skill-evolution worker can mark
a candidate it has already distilled + replayed and stop re-processing it every
interval (the live flywheel re-distilled the same trajectories forever — a cost
runaway the unit tests, which call run_once once, never exercised). Orthogonal
to ``status`` (the J.12 human-review verdict): a candidate may be both
SE-6-evolved and still pending human curation review.

Revision id ``0078_curation_evolved_at`` = 24 chars (within the 32-char alembic
``version_num`` ceiling per [memory:alembic-revision-id-32-chars]).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0078_curation_evolved_at"
down_revision: str | Sequence[str] | None = "0077_platform_judge_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.add_column(
        "curation_candidate",
        sa.Column("evolved_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("curation_candidate", "evolved_at")
