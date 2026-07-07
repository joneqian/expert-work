"""Capability Uplift Sprint #3 — skill_version supporting files + lazy
loading + drift-detection hash + high-risk publish gate.

Revision ID: 0042_skill_supporting_files
Revises: 0041_memory_recall_mode
Create Date: 2026-05-28

Adds 4 columns to ``skill_version`` in support of Mini-ADRs U-15 / U-16
/ U-21 / U-24 (see ``docs/streams/STREAM-UPLIFT-DESIGN.md`` § 4):

* ``supporting_files`` (JSONB, default ``{}``) — Mini-ADR U-16. Map of
  ``{relative/path: {"content": base64, "size": int, "mime": str}}``.
  5 MB cap enforced by ``CHECK (octet_length(...) <= 5_242_880)``;
  per-file 1 MB + per-skill 64 entries enforced at the API layer.

* ``lazy_load`` (BOOL, default ``false``) — Mini-ADR U-15. When ``true``,
  ``agent_factory`` injects only the skill summary; the body is loaded
  on demand via ``skill_view``. Default ``false`` keeps existing eager
  behavior so deployed agents do not regress.

* ``content_hash`` (BYTEA, default ``''``) — Mini-ADR U-21. blake2b-32
  of the canonicalized ``(prompt_fragment, supporting_files)`` bytes.
  Recomputed at ``skill_view`` time; mismatch fires
  ``SKILL_DRIFT_DETECTED`` (the SQL-injection / internal-actor signal).

* ``high_risk`` (BOOL, default ``false``) — Mini-ADR U-24. Set at write
  time when ``tool_names`` intersects ``{exec_python, http, exec_shell}``
  or any supporting file lives under ``scripts/``. The publish gate
  (``PATCH /v1/skills/{id} status=active``) requires tenant_admin /
  system_admin for ``high_risk = true`` rows. M0 is transparent (all
  skill writes are already admin); the gate activates with M1-K J.7b-1
  agent-self-authored skills.

Backfill for existing M0 rows happens in this migration:

* ``content_hash`` is recomputed from ``prompt_fragment`` + empty JSONB
  (using the same canonicalize-then-blake2b path the application uses).
* ``high_risk`` is recomputed from the existing ``tool_names`` only
  (supporting_files is empty for all M0 rows so no scripts/* check).

Without backfill, the first ``skill_view`` on any M0 row would fire a
spurious drift alarm (P0 alert) — which would be a false positive and
SecOps wake-up call we want to avoid.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042_skill_supporting_files"
down_revision: str | Sequence[str] | None = "0041_memory_recall_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

# Keep in sync with expert_work.protocol.skill.HIGH_RISK_TOOLS.
# Duplicated rather than imported because alembic envs commonly run
# without the protocol package on the path.
_HIGH_RISK_TOOLS: frozenset[str] = frozenset({"exec_python", "http", "exec_shell"})


def _canonicalize(prompt_fragment: str, supporting_files: dict[str, object]) -> bytes:
    """Stable byte sequence for hashing — JSONB sort + null separator.

    Mirrors the application-side ``_canonicalize`` exactly so backfilled
    hashes match what the live code computes.
    """
    sorted_files = json.dumps(
        supporting_files,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return prompt_fragment.encode("utf-8") + b"\x00" + sorted_files.encode("utf-8")


def _hash(canonical: bytes) -> bytes:
    return hashlib.blake2b(canonical, digest_size=32).digest()


def upgrade() -> None:
    op.add_column(
        "skill_version",
        sa.Column(
            "supporting_files",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "skill_version",
        sa.Column(
            "lazy_load",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "skill_version",
        sa.Column(
            "content_hash",
            sa.LargeBinary(),
            nullable=False,
            server_default=sa.text("''::bytea"),
        ),
    )
    op.add_column(
        "skill_version",
        sa.Column(
            "high_risk",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_check_constraint(
        "skill_version_supporting_files_size_ck",
        "skill_version",
        "octet_length(supporting_files::text) <= 5242880",
    )

    # Backfill existing rows so the very first skill_view doesn't trip
    # the SKILL_DRIFT_DETECTED P0 alert (or the U-24 publish gate).
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, prompt_fragment, tool_names FROM skill_version "
            "WHERE content_hash = ''::bytea"
        )
    ).all()
    for row in rows:
        canonical = _canonicalize(row.prompt_fragment, {})
        new_hash = _hash(canonical)
        # tool_names is JSONB array; SQLAlchemy returns it as a list.
        tools: list[str] = row.tool_names or []
        is_high = bool(_HIGH_RISK_TOOLS & set(tools))
        bind.execute(
            sa.text("UPDATE skill_version SET content_hash = :h, high_risk = :hr WHERE id = :id"),
            {"h": new_hash, "hr": is_high, "id": row.id},
        )


def downgrade() -> None:
    op.drop_constraint(
        "skill_version_supporting_files_size_ck",
        "skill_version",
        type_="check",
    )
    op.drop_column("skill_version", "high_risk")
    op.drop_column("skill_version", "content_hash")
    op.drop_column("skill_version", "lazy_load")
    op.drop_column("skill_version", "supporting_files")
