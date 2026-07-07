"""Raise the ``skill_version.supporting_files`` size cap to 12 MiB.

The zip-import allowlist now admits font assets (``.ttf`` / ``.otf``) and its
total-size cap moved 5 MiB → 8 MiB (calibrated against anthropics/skills
``canvas-design``: 54 bundled fonts, ~5.6 MiB raw). Supporting files are
stored base64-encoded inside the ``supporting_files`` JSON, so the stored
text is ~4/3 the raw payload (+ JSON envelope): an 8 MiB package needs
~11 MiB of text. 12 MiB gives headroom while keeping a hard resource bound.

Revision id ``0120_supporting_files_cap`` = 25 chars (within the 32-char
alembic ``version_num`` ceiling).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0120_supporting_files_cap"
down_revision: str | Sequence[str] | None = "0119_platform_quality_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CK = "skill_version_supporting_files_size_ck"
_TABLE = "skill_version"


def upgrade() -> None:
    op.drop_constraint(_CK, _TABLE, type_="check")
    op.create_check_constraint(
        _CK,
        _TABLE,
        "octet_length(supporting_files::text) <= 12582912",
    )


def downgrade() -> None:
    op.drop_constraint(_CK, _TABLE, type_="check")
    op.create_check_constraint(
        _CK,
        _TABLE,
        "octet_length(supporting_files::text) <= 5242880",
    )
