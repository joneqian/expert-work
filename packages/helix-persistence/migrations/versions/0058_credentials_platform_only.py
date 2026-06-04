"""Stream Y-1 — LLM platform-exclusive: lock credentials_mode to 'platform'.

Revision ID: 0058_credentials_platform_only
Revises: 0057_platform_skill
Create Date: 2026-06-04

Stream Y-1 makes LLM credentials platform-exclusive. The former tenant
BYOK mode (``credentials_mode='tenant'``) is removed, so this migration:

* Defensively flips any existing ``tenant`` rows back to ``platform``
  (irreversible by design — see ``downgrade`` note below).
* Tightens the ``tenant_config_credentials_mode_ck`` CHECK from
  ``IN ('platform', 'tenant')`` (migration 0047) to ``IN ('platform')``.

The ``model_credentials_ref`` / ``tool_credentials`` / ``mcp_credentials``
columns are retained dormant (no destructive drop); they are simply no
longer read at resolve time.

Revision id ``0058_credentials_platform_only`` = 29 chars (within the
32-char alembic ``version_num`` ceiling per
[memory:alembic-revision-id-32-chars]).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0058_credentials_platform_only"
down_revision: str | Sequence[str] | None = "0057_platform_skill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    # Defensive data flip: any tenant left in the removed 'tenant' BYOK
    # mode is forced back to 'platform' before the tighter CHECK is applied.
    op.execute(
        "UPDATE tenant_config SET credentials_mode='platform' WHERE credentials_mode<>'platform'"
    )
    op.drop_constraint("tenant_config_credentials_mode_ck", "tenant_config", type_="check")
    op.create_check_constraint(
        "tenant_config_credentials_mode_ck",
        "tenant_config",
        "credentials_mode IN ('platform')",
    )


def downgrade() -> None:
    # Restore the permissive CHECK. The data flip in ``upgrade`` is NOT
    # reversed — once tenant BYOK rows are coerced to 'platform' the prior
    # mode is unrecoverable (irreversible by design).
    op.drop_constraint("tenant_config_credentials_mode_ck", "tenant_config", type_="check")
    op.create_check_constraint(
        "tenant_config_credentials_mode_ck",
        "tenant_config",
        "credentials_mode IN ('platform', 'tenant')",
    )
