"""``role_binding`` ORM model — Stream C.3 + Stream N (Mini-ADR N-1).

Two scopes:

* **Tenant scope** — ``platform_scope=False`` (default);``tenant_id`` is
  required;``role`` ∈ {admin, operator, viewer}.
* **Platform scope** — ``platform_scope=True``;``tenant_id`` IS NULL;
  ``role`` ∈ {system_admin}. Grants cross-tenant capabilities.

The DB enforces the ``(platform_scope, tenant_id, role)`` triple via a
CHECK constraint (see migration ``0035_role_binding_platform_scope``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from expert_work.persistence.base import Base


class RoleBindingRow(Base):
    """Maps a user / service_account to a role.

    See module docstring for the two-scope model.
    """

    __tablename__ = "role_binding"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    subject_type: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    # Stream N: nullable. NULL iff platform_scope is True.
    tenant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    # Stream N: True ⇔ row is a platform-scope binding (tenant_id NULL,
    # role='system_admin'). DB-level CHECK constraint enforces the triple.
    platform_scope: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    # Stream 8.5 — ABAC narrowing conditions (resource_ids / labels /
    # owner_only) serialized as JSONB. NULL ⇒ unconditioned (type-wide grant).
    # Only valid on tenant-scope rows; the CHECK below enforces NULL for
    # platform-scope bindings. ``none_as_null=True`` is REQUIRED: without it
    # SQLAlchemy stores Python ``None`` as a JSONB ``null`` value (not SQL
    # NULL), so ``conditions IS NULL`` is false and the platform-scope CHECK
    # rejects every conditionless platform binding.
    conditions: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    granted_by: Mapped[str] = mapped_column(Text, nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        # Tenant-scope unique: same (subject, tenant, role) cannot repeat.
        # tenant_id is now nullable so this UNIQUE is partial — see migration
        # for the platform-scope partial unique index.
        UniqueConstraint(
            "subject_type",
            "subject_id",
            "tenant_id",
            "role",
            name="role_binding_subject_tenant_role_uniq",
        ),
        # Stream N CHECK — DB-level invariant on (platform_scope, tenant_id, role):
        CheckConstraint(
            "(platform_scope = false AND tenant_id IS NOT NULL"
            " AND role IN ('admin','operator','viewer'))"
            " OR "
            "(platform_scope = true AND tenant_id IS NULL"
            " AND role = 'system_admin')",
            name="role_binding_scope_triple_ck",
        ),
        # Stream 8.5 — ABAC conditions only on tenant-scope bindings.
        CheckConstraint(
            "platform_scope = false OR conditions IS NULL",
            name="role_binding_platform_no_conditions_ck",
        ),
        Index("role_binding_subject_idx", "subject_type", "subject_id"),
        Index("role_binding_tenant_idx", "tenant_id"),
        # Stream N: partial UNIQUE — each subject has at most one platform-scope binding.
        # `postgresql_where` makes this a Postgres partial unique index;
        # other backends (test fixtures) get a regular index — DB tests run on PG.
        Index(
            "role_binding_subject_platform_uniq",
            "subject_type",
            "subject_id",
            unique=True,
            postgresql_where=text("platform_scope = true"),
        ),
    )
