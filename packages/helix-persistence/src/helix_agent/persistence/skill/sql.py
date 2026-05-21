"""SQLAlchemy-backed ``SkillStore`` — Stream J.7a (Mini-ADR J-23)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from helix_agent.persistence.models import SkillRow, SkillVersionRow
from helix_agent.persistence.skill.base import (
    DuplicateSkillError,
    SkillNotFoundError,
    SkillStore,
)
from helix_agent.protocol import Skill, SkillStatus, SkillVersion


def _skill_row_to_dto(row: SkillRow) -> Skill:
    return Skill(
        id=row.id,
        tenant_id=row.tenant_id,
        name=row.name,
        status=SkillStatus(row.status),
        latest_version=row.latest_version,
        description=row.description,
        category=row.category,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _version_row_to_dto(row: SkillVersionRow) -> SkillVersion:
    return SkillVersion(
        id=row.id,
        skill_id=row.skill_id,
        tenant_id=row.tenant_id,
        version=row.version,
        prompt_fragment=row.prompt_fragment,
        tool_names=tuple(row.tool_names or ()),
        description=row.description,
        category=row.category,
        required_models=tuple(row.required_models or ()),
        authored_by=row.authored_by,  # type: ignore[arg-type]
        created_at=row.created_at,
    )


class SqlSkillStore(SkillStore):
    """Postgres-backed skill registry."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    # ------------------------------------------------------------ skill

    async def create_skill(
        self,
        *,
        skill_id: UUID,
        tenant_id: UUID,
        name: str,
        description: str = "",
        category: str | None = None,
    ) -> Skill:
        now = datetime.now(UTC)
        async with self._sf() as session:
            row = SkillRow(
                id=skill_id,
                tenant_id=tenant_id,
                name=name,
                status=SkillStatus.DRAFT.value,
                latest_version=0,
                description=description,
                category=category,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                # ``skill_tenant_name_uq`` violation — admin POST collision.
                raise DuplicateSkillError(tenant_id=tenant_id, name=name) from exc
            await session.refresh(row)
            return _skill_row_to_dto(row)

    async def get_skill(self, *, skill_id: UUID, tenant_id: UUID) -> Skill | None:
        async with self._sf() as session:
            row = (
                await session.execute(
                    select(SkillRow).where(SkillRow.id == skill_id, SkillRow.tenant_id == tenant_id)
                )
            ).scalar_one_or_none()
        return _skill_row_to_dto(row) if row is not None else None

    async def get_skill_by_name(self, *, tenant_id: UUID, name: str) -> Skill | None:
        async with self._sf() as session:
            row = (
                await session.execute(
                    select(SkillRow).where(SkillRow.tenant_id == tenant_id, SkillRow.name == name)
                )
            ).scalar_one_or_none()
        return _skill_row_to_dto(row) if row is not None else None

    async def list_skills(
        self,
        *,
        tenant_id: UUID,
        status: SkillStatus | None = None,
        category: str | None = None,
        cursor: UUID | None = None,
        limit: int = 50,
    ) -> tuple[list[Skill], UUID | None]:
        async with self._sf() as session:
            stmt = (
                select(SkillRow)
                .where(SkillRow.tenant_id == tenant_id)
                .order_by(SkillRow.created_at.desc(), SkillRow.id)
            )
            if status is not None:
                stmt = stmt.where(SkillRow.status == status.value)
            if category is not None:
                stmt = stmt.where(SkillRow.category == category)
            if cursor is not None:
                # Cursor = last row's id; load it to get (created_at) for
                # keyset comparison.
                cur_row = (
                    await session.execute(
                        select(SkillRow).where(
                            SkillRow.id == cursor, SkillRow.tenant_id == tenant_id
                        )
                    )
                ).scalar_one_or_none()
                if cur_row is not None:
                    stmt = stmt.where(
                        (SkillRow.created_at < cur_row.created_at)
                        | ((SkillRow.created_at == cur_row.created_at) & (SkillRow.id > cur_row.id))
                    )
            stmt = stmt.limit(limit + 1)
            rows = (await session.execute(stmt)).scalars().all()
        items = [_skill_row_to_dto(r) for r in rows]
        if len(items) > limit:
            return items[:limit], items[limit - 1].id
        return items, None

    async def set_status(self, *, skill_id: UUID, tenant_id: UUID, status: SkillStatus) -> Skill:
        async with self._sf() as session:
            result = await session.execute(
                update(SkillRow)
                .where(SkillRow.id == skill_id, SkillRow.tenant_id == tenant_id)
                .values(status=status.value, updated_at=datetime.now(UTC))
                .returning(SkillRow)
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise SkillNotFoundError(str(skill_id))
            await session.commit()
        return _skill_row_to_dto(row)

    # ------------------------------------------------------------ skill_version

    async def add_version(
        self,
        *,
        version_id: UUID,
        skill_id: UUID,
        tenant_id: UUID,
        prompt_fragment: str,
        tool_names: Sequence[str] = (),
        description: str = "",
        category: str | None = None,
        required_models: Sequence[str] = (),
        authored_by: str = "human",
    ) -> SkillVersion:
        if authored_by not in {"human", "agent"}:
            msg = f"authored_by must be 'human' or 'agent' (got {authored_by!r})"
            raise ValueError(msg)
        now = datetime.now(UTC)
        async with self._sf() as session:
            parent = (
                await session.execute(
                    select(SkillRow).where(SkillRow.id == skill_id, SkillRow.tenant_id == tenant_id)
                )
            ).scalar_one_or_none()
            if parent is None:
                raise SkillNotFoundError(str(skill_id))
            next_version = parent.latest_version + 1
            new_description = description or parent.description
            new_category = category if category is not None else parent.category
            version_row = SkillVersionRow(
                id=version_id,
                tenant_id=tenant_id,
                skill_id=skill_id,
                version=next_version,
                prompt_fragment=prompt_fragment,
                tool_names=list(tool_names),
                description=new_description,
                category=new_category,
                required_models=list(required_models),
                authored_by=authored_by,
                created_at=now,
            )
            session.add(version_row)
            parent.latest_version = next_version
            parent.description = new_description
            parent.category = new_category
            parent.updated_at = now
            await session.commit()
            await session.refresh(version_row)
            return _version_row_to_dto(version_row)

    async def get_version(self, *, version_id: UUID, tenant_id: UUID) -> SkillVersion | None:
        async with self._sf() as session:
            row = (
                await session.execute(
                    select(SkillVersionRow).where(
                        SkillVersionRow.id == version_id,
                        SkillVersionRow.tenant_id == tenant_id,
                    )
                )
            ).scalar_one_or_none()
        return _version_row_to_dto(row) if row is not None else None

    async def get_version_by_number(
        self, *, skill_id: UUID, tenant_id: UUID, version: int
    ) -> SkillVersion | None:
        async with self._sf() as session:
            row = (
                await session.execute(
                    select(SkillVersionRow).where(
                        SkillVersionRow.skill_id == skill_id,
                        SkillVersionRow.tenant_id == tenant_id,
                        SkillVersionRow.version == version,
                    )
                )
            ).scalar_one_or_none()
        return _version_row_to_dto(row) if row is not None else None

    async def list_versions(self, *, skill_id: UUID, tenant_id: UUID) -> list[SkillVersion]:
        async with self._sf() as session:
            rows = (
                (
                    await session.execute(
                        select(SkillVersionRow)
                        .where(
                            SkillVersionRow.skill_id == skill_id,
                            SkillVersionRow.tenant_id == tenant_id,
                        )
                        .order_by(SkillVersionRow.version.desc())
                    )
                )
                .scalars()
                .all()
            )
        return [_version_row_to_dto(r) for r in rows]

    # ------------------------------------------------------------ resolve

    async def resolve_by_name(self, *, tenant_id: UUID, name: str) -> SkillVersion | None:
        skill = await self.get_skill_by_name(tenant_id=tenant_id, name=name)
        if skill is None or skill.status != SkillStatus.ACTIVE or skill.latest_version == 0:
            return None
        return await self.get_version_by_number(
            skill_id=skill.id, tenant_id=tenant_id, version=skill.latest_version
        )

    async def resolve_pinned(
        self, *, tenant_id: UUID, name: str, version: int
    ) -> SkillVersion | None:
        skill = await self.get_skill_by_name(tenant_id=tenant_id, name=name)
        if skill is None:
            return None
        return await self.get_version_by_number(
            skill_id=skill.id, tenant_id=tenant_id, version=version
        )
