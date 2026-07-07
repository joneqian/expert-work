"""SE-16 PR-3 — admit the ``implicit_success`` curation signal (SE-A38).

Widens the ``curation_candidate.signal`` CHECK to include the new implicit
positive signal: an unlabeled success whose thread settled quietly. The
constraint mirrors ``_SIGNAL_VALUES`` in the ORM model.

Revision ID: 0108_implicit_success_signal
Revises: 0107_skill_evolution_gray_retry
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0108_implicit_success_signal"
down_revision: str | Sequence[str] | None = "0107_skill_evolution_gray_retry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_TABLE = "curation_candidate"
_CONSTRAINT = "curation_candidate_signal_valid"
_OLD = "('negative_feedback', 'failed_outcome', 'positive_feedback')"
_NEW = "('negative_feedback', 'failed_outcome', 'positive_feedback', 'implicit_success')"


def upgrade() -> None:
    op.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_CONSTRAINT};")
    op.execute(f"ALTER TABLE {_TABLE} ADD CONSTRAINT {_CONSTRAINT} CHECK (signal IN {_NEW});")


def downgrade() -> None:
    # Static identifiers only (module constants) — no user input reaches this.
    op.execute(f"DELETE FROM {_TABLE} WHERE signal = 'implicit_success';")  # noqa: S608
    op.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_CONSTRAINT};")
    op.execute(f"ALTER TABLE {_TABLE} ADD CONSTRAINT {_CONSTRAINT} CHECK (signal IN {_OLD});")
