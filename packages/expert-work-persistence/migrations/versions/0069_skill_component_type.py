"""Stream SE (SE-10) — skill.component_type + target_tool_name.

Revision ID: 0069_skill_component_type   (25 chars; within the 32-char
``version_num`` ceiling per [memory:alembic-revision-id-32-chars]).
Revises: 0068_skill_promote_kill
Create Date: 2026-06-08

SE-10 (Mini-ADR SE-A15) widens the self-evolution target from skill-only
to three text-class harness components, reusing the same ``skill`` /
``skill_version`` carrier + the same with-vs-without replay gate. Two
additive columns on ``skill``:

* ``component_type`` — ``'skill'`` (default; every historical row) /
  ``'system_prompt'`` (agent behaviour patch) / ``'tool_description'``
  (clarify an already-bound tool, text only) / ``'memory_entry'``
  (reusable long-term fact). Code-class components (tool impl / middleware
  / sub-agent code) are out of scope — they stay human-reviewed.
* ``target_tool_name`` — the tool a ``tool_description`` component
  supplements; NULL for every other component_type. A CHECK makes the two
  mutually entailing.

Pure additive (no new table, no RLS change — the ``skill`` policy from
0057 already covers these columns). ``server_default 'skill'`` keeps every
existing row valid.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0069_skill_component_type"
down_revision: str | Sequence[str] | None = "0068_skill_promote_kill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.add_column(
        "skill",
        sa.Column(
            "component_type",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'skill'"),
        ),
    )
    op.add_column("skill", sa.Column("target_tool_name", sa.Text(), nullable=True))
    op.create_check_constraint(
        "skill_component_type_check",
        "skill",
        "component_type IN ('skill', 'system_prompt', 'tool_description', 'memory_entry')",
    )
    op.create_check_constraint(
        "skill_target_tool_name_check",
        "skill",
        "(component_type = 'tool_description') = (target_tool_name IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("skill_target_tool_name_check", "skill", type_="check")
    op.drop_constraint("skill_component_type_check", "skill", type_="check")
    op.drop_column("skill", "target_tool_name")
    op.drop_column("skill", "component_type")
