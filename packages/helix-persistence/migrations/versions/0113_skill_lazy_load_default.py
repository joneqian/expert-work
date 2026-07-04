"""Skill progressive-disclosure default — ``skill_version.lazy_load``.

RT-ADR-11 (STREAM-RT-DESIGN §9). Flip the ``lazy_load`` ``server_default``
from ``false`` (eager: full SKILL.md body inlined into the system prompt) to
``true`` (lazy: only a one-line ``<skill .../>`` summary, body read on demand
via ``skill_view``) so new skills default to progressive disclosure, aligning
with deer-flow / Hermes / Anthropic and cutting fixed system-prompt overhead.

Existing rows store an explicit boolean, so the ``server_default`` flip does
not touch them. One targeted data flip: curated **platform** skills
(``skill.tenant_id IS NULL`` — the shared xlsx / pptx / docx / pdf office
skills) are retro-lazied, since they are the archetypal progressive-disclosure
case (large procedural bodies, read on demand, shared across agents). Tenant-
authored skills are left as-is (respect the Mini-ADR U-15 no-regression line);
their authors flip explicitly.

Revision ID: 0113_skill_lazy_load_default
Revises: 0112_webhook_payload_format
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0113_skill_lazy_load_default"
down_revision: str | Sequence[str] | None = "0112_webhook_payload_format"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.alter_column(
        "skill_version",
        "lazy_load",
        server_default=sa.text("true"),
    )
    # Retro-lazy curated platform skills (shared, tenant_id IS NULL). Tenant
    # skills untouched.
    op.execute(
        "UPDATE skill_version SET lazy_load = true "
        "WHERE skill_id IN (SELECT id FROM skill WHERE tenant_id IS NULL)"
    )


def downgrade() -> None:
    # Revert the server default only. The curated data flip is NOT reversed:
    # a plain ``false`` update could not tell a pre-migration eager row from a
    # post-migration platform skill that was authored lazy, so undoing it would
    # be lossy. Explicit per-skill values remain authoritative.
    op.alter_column(
        "skill_version",
        "lazy_load",
        server_default=sa.text("false"),
    )
