"""``tenant_skill_subscription`` record — Skill Marketplace Phase 1.

A tenant's "I selected this platform skill" marker. Pure accounting/UX — it
does NOT affect the runtime resolver fallback (semantic A; the tier gate on the
hot path is the real gate). No skill content is copied: the row holds only the
``platform_skill_id`` pointer.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

__all__ = ["TenantSkillSubscriptionRecord"]


class TenantSkillSubscriptionRecord(BaseModel):
    """One row of ``tenant_skill_subscription`` as exposed across layers.

    No extra="forbid": materialized from a trusted DB row, not untrusted API
    input.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: UUID
    platform_skill_id: UUID
    # Soft-stop flag: cancelling a subscription sets this False (audit trail
    # preserved); re-subscribing flips it back True.
    enabled: bool = True
    created_at: datetime
    created_by: str
