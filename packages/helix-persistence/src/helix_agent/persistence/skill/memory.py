"""In-memory ``SkillStore`` — Stream J.7a (Mini-ADR J-23).

Used by unit tests + the dev default. Semantics mirror
:class:`SqlSkillStore`; concurrency safety relies on the asyncio single-
thread model (one in-flight operation per store instance).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from helix_agent.persistence.skill.base import (
    DuplicateSkillError,
    SkillNotFoundError,
    SkillStore,
)
from helix_agent.protocol import Skill, SkillStatus, SkillVersion


def _paginate_skills(
    rows: list[Skill],
    *,
    status: SkillStatus | None,
    category: str | None,
    cursor: UUID | None,
    limit: int,
) -> tuple[list[Skill], UUID | None]:
    """Shared keyset-pagination helper for ``list_skills`` + ``list_skills_all_tenants``."""
    if status is not None:
        rows = [r for r in rows if r.status == status]
    if category is not None:
        rows = [r for r in rows if r.category == category]
    rows.sort(key=lambda r: (r.created_at, r.id), reverse=True)
    if cursor is not None:
        try:
            cut_idx = next(i for i, r in enumerate(rows) if r.id == cursor)
            rows = rows[cut_idx + 1 :]
        except StopIteration:
            rows = []
    page = rows[: limit + 1]
    if len(page) > limit:
        return page[:limit], page[limit - 1].id
    return page, None


class InMemorySkillStore(SkillStore):
    """Single-process skill registry. Process-local; no concurrency guard."""

    def __init__(self) -> None:
        self._skills: dict[UUID, Skill] = {}
        self._versions: list[SkillVersion] = []

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
        for existing in self._skills.values():
            if existing.tenant_id == tenant_id and existing.name == name:
                raise DuplicateSkillError(tenant_id=tenant_id, name=name)
        now = datetime.now(UTC)
        skill = Skill(
            id=skill_id,
            tenant_id=tenant_id,
            name=name,
            status=SkillStatus.DRAFT,
            latest_version=0,
            description=description,
            category=category,
            created_at=now,
            updated_at=now,
        )
        self._skills[skill_id] = skill
        return skill

    async def get_skill(self, *, skill_id: UUID, tenant_id: UUID) -> Skill | None:
        row = self._skills.get(skill_id)
        if row is None or row.tenant_id != tenant_id:
            return None
        return row

    async def get_skill_by_name(self, *, tenant_id: UUID, name: str) -> Skill | None:
        for row in self._skills.values():
            if row.tenant_id == tenant_id and row.name == name:
                return row
        return None

    async def list_skills(
        self,
        *,
        tenant_id: UUID,
        status: SkillStatus | None = None,
        category: str | None = None,
        cursor: UUID | None = None,
        limit: int = 50,
    ) -> tuple[list[Skill], UUID | None]:
        rows = [r for r in self._skills.values() if r.tenant_id == tenant_id]
        return _paginate_skills(rows, status=status, category=category, cursor=cursor, limit=limit)

    async def list_skills_all_tenants(
        self,
        *,
        status: SkillStatus | None = None,
        category: str | None = None,
        cursor: UUID | None = None,
        limit: int = 50,
    ) -> tuple[list[Skill], UUID | None]:
        # Stream N — no tenant filter.
        rows = list(self._skills.values())
        return _paginate_skills(rows, status=status, category=category, cursor=cursor, limit=limit)

    async def set_status(self, *, skill_id: UUID, tenant_id: UUID, status: SkillStatus) -> Skill:
        row = await self.get_skill(skill_id=skill_id, tenant_id=tenant_id)
        if row is None:
            raise SkillNotFoundError(str(skill_id))
        updated = row.model_copy(update={"status": status, "updated_at": datetime.now(UTC)})
        self._skills[skill_id] = updated
        return updated

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
        parent = await self.get_skill(skill_id=skill_id, tenant_id=tenant_id)
        if parent is None:
            raise SkillNotFoundError(str(skill_id))
        next_version = parent.latest_version + 1
        now = datetime.now(UTC)
        if authored_by not in {"human", "agent"}:
            msg = f"authored_by must be 'human' or 'agent' (got {authored_by!r})"
            raise ValueError(msg)
        version = SkillVersion(
            id=version_id,
            skill_id=skill_id,
            tenant_id=tenant_id,
            version=next_version,
            prompt_fragment=prompt_fragment,
            tool_names=tuple(tool_names),
            description=description or parent.description,
            category=category if category is not None else parent.category,
            required_models=tuple(required_models),
            authored_by=authored_by,  # type: ignore[arg-type]
            created_at=now,
        )
        self._versions.append(version)
        # Mirror description / category onto parent + bump latest_version.
        self._skills[skill_id] = parent.model_copy(
            update={
                "latest_version": next_version,
                "description": description or parent.description,
                "category": category if category is not None else parent.category,
                "updated_at": now,
            }
        )
        return version

    async def get_version(self, *, version_id: UUID, tenant_id: UUID) -> SkillVersion | None:
        for v in self._versions:
            if v.id == version_id and v.tenant_id == tenant_id:
                return v
        return None

    async def get_version_by_number(
        self, *, skill_id: UUID, tenant_id: UUID, version: int
    ) -> SkillVersion | None:
        for v in self._versions:
            if v.skill_id == skill_id and v.tenant_id == tenant_id and v.version == version:
                return v
        return None

    async def list_versions(self, *, skill_id: UUID, tenant_id: UUID) -> list[SkillVersion]:
        versions = [
            v for v in self._versions if v.skill_id == skill_id and v.tenant_id == tenant_id
        ]
        versions.sort(key=lambda v: v.version, reverse=True)
        return versions

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


def _new_id() -> UUID:
    return uuid4()
