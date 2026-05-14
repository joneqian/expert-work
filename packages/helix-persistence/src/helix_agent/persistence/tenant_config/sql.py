"""SQLAlchemy-backed :class:`TenantConfigStore` — Stream C.7."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from helix_agent.persistence.models import TenantConfigRow
from helix_agent.persistence.tenant_config.base import TenantConfigStore
from helix_agent.persistence.tenant_config.memory import FirstUpsertRequiresDisplayNameError
from helix_agent.protocol import TenantConfigPatch, TenantConfigRecord, TenantPlan


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _row_to_record(row: TenantConfigRow) -> TenantConfigRecord:
    return TenantConfigRecord(
        tenant_id=row.tenant_id,
        display_name=row.display_name,
        plan=TenantPlan(row.plan),
        model_credentials_ref={str(k): str(v) for k, v in row.model_credentials_ref.items()},
        mcp_allowlist=[str(x) for x in row.mcp_allowlist],
        rate_limit_override=dict(row.rate_limit_override),
        pii_fields=[str(x) for x in row.pii_fields],
        audit_retention_days=row.audit_retention_days,
        event_log_retention_days=row.event_log_retention_days,
        created_at=row.created_at,
        updated_at=row.updated_at,
        updated_by=row.updated_by,
    )


class SqlTenantConfigStore(TenantConfigStore):
    """Postgres-backed tenant config repository."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get(self, *, tenant_id: UUID) -> TenantConfigRecord | None:
        async with self._sf() as session:
            row = await session.get(TenantConfigRow, tenant_id)
        return _row_to_record(row) if row is not None else None

    async def upsert(
        self,
        *,
        tenant_id: UUID,
        patch: TenantConfigPatch,
        actor_id: str,
    ) -> TenantConfigRecord:
        async with self._sf() as session:
            existing = await session.get(TenantConfigRow, tenant_id)
            if existing is None:
                if patch.display_name is None:
                    msg = (
                        "first upsert for a tenant must include display_name; "
                        f"got tenant_id={tenant_id}"
                    )
                    raise FirstUpsertRequiresDisplayNameError(msg)
                # Use INSERT ... ON CONFLICT DO UPDATE so a concurrent
                # writer that beat us to the first insert doesn't
                # cause a uniqueness collision.
                now = _utc_now()
                values: dict[str, object] = {
                    "tenant_id": tenant_id,
                    "display_name": patch.display_name,
                    "plan": (patch.plan or TenantPlan.FREE).value,
                    "model_credentials_ref": dict(patch.model_credentials_ref or {}),
                    "mcp_allowlist": list(patch.mcp_allowlist or []),
                    "rate_limit_override": dict(patch.rate_limit_override or {}),
                    "pii_fields": list(patch.pii_fields or []),
                    "created_at": now,
                    "updated_at": now,
                    "updated_by": actor_id,
                }
                if patch.audit_retention_days is not None:
                    values["audit_retention_days"] = patch.audit_retention_days
                if patch.event_log_retention_days is not None:
                    values["event_log_retention_days"] = patch.event_log_retention_days
                stmt = (
                    pg_insert(TenantConfigRow)
                    .values(**values)
                    .on_conflict_do_update(
                        index_elements=["tenant_id"],
                        set_={
                            "display_name": patch.display_name,
                            "updated_at": now,
                            "updated_by": actor_id,
                        },
                    )
                    .returning(TenantConfigRow)
                )
                row = (await session.execute(stmt)).scalar_one()
                await session.commit()
                await session.refresh(row)
                return _row_to_record(row)

            # Update path — merge non-None patch fields.
            if patch.display_name is not None:
                existing.display_name = patch.display_name
            if patch.plan is not None:
                existing.plan = patch.plan.value
            if patch.model_credentials_ref is not None:
                existing.model_credentials_ref = dict(patch.model_credentials_ref)
            if patch.mcp_allowlist is not None:
                existing.mcp_allowlist = list(patch.mcp_allowlist)
            if patch.rate_limit_override is not None:
                existing.rate_limit_override = dict(patch.rate_limit_override)
            if patch.pii_fields is not None:
                existing.pii_fields = list(patch.pii_fields)
            if patch.audit_retention_days is not None:
                existing.audit_retention_days = patch.audit_retention_days
            if patch.event_log_retention_days is not None:
                existing.event_log_retention_days = patch.event_log_retention_days
            existing.updated_at = _utc_now()
            existing.updated_by = actor_id
            await session.commit()
            await session.refresh(existing)
            return _row_to_record(existing)
