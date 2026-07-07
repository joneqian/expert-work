"""Platform tool-output-budget config ORM model — Phase 3.

A single-row (``id == "singleton"``) table storing the platform-global on/off
for the tool-output-budget feature (generalized externalization + persist floor
+ CM-12 prune). An absent row means "not configured" → the
``PlatformToolBudgetConfigService`` falls back to the ``EXPERT_WORK_TOOL_OUTPUT_BUDGET``
env default.

Platform-global, tenant-less (like ``platform_judge_config`` /
``platform_embedding_config``) — no RLS policy; all access goes through
``bypass_rls_session()``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from expert_work.persistence.base import Base


class PlatformToolBudgetConfigRow(Base):
    """The single platform tool-output-budget on/off row."""

    __tablename__ = "platform_tool_budget_config"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_by: Mapped[str | None] = mapped_column(Text, nullable=True)
