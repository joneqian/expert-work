"""In-memory ``SkillStore`` — Stream J.7a (Mini-ADR J-23).

Used by unit tests + the dev default. Semantics mirror
:class:`SqlSkillStore`; concurrency safety relies on the asyncio single-
thread model (one in-flight operation per store instance).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from helix_agent.persistence.skill.base import (
    DuplicateSkillError,
    SkillNotFoundError,
    SkillStore,
)
from helix_agent.protocol import (
    EvolutionOrigin,
    Skill,
    SkillEvalResult,
    SkillStatus,
    SkillVersion,
    SkillVisibility,
)
from helix_agent.protocol.tenant_config import TenantPlan


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
        self._eval_results: list[SkillEvalResult] = []  # Stream SE (SE-A2)

    # ------------------------------------------------------------ skill

    async def create_skill(
        self,
        *,
        skill_id: UUID,
        tenant_id: UUID,
        name: str,
        description: str = "",
        category: str | None = None,
        required_tier: TenantPlan = TenantPlan.FREE,
        visibility: SkillVisibility = "tenant",
        created_by_agent_id: UUID | None = None,
        forked_from: UUID | None = None,
    ) -> Skill:
        return await self._create_skill_row(
            skill_id=skill_id,
            tenant_id=tenant_id,
            name=name,
            description=description,
            category=category,
            required_tier=required_tier,
            visibility=visibility,
            created_by_agent_id=created_by_agent_id,
            forked_from=forked_from,
        )

    async def _create_skill_row(
        self,
        *,
        skill_id: UUID,
        tenant_id: UUID | None,
        name: str,
        description: str,
        category: str | None,
        required_tier: TenantPlan,
        visibility: SkillVisibility = "tenant",
        created_by_agent_id: UUID | None = None,
        forked_from: UUID | None = None,
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
            required_tier=required_tier,
            visibility=visibility,
            created_by_agent_id=created_by_agent_id,
            forked_from=forked_from,
            # Sprint #4 — match the SQL ``server_default=text("now()")``
            # so the in-memory and Postgres backends emit the same
            # DTO shape on first read.
            state_changed_at=now,
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
        now = datetime.now(UTC)
        updated = row.model_copy(
            update={"status": status, "updated_at": now, "state_changed_at": now}
        )
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
        supporting_files: dict[str, dict[str, Any]] | None = None,
        lazy_load: bool = False,
        content_hash: bytes = b"",
        high_risk: bool = False,
        evolution_origin: EvolutionOrigin | None = None,
        distilled_from_trajectory_key: str | None = None,
        distilled_from_candidate_id: UUID | None = None,
        evolution_round: int = 0,
    ) -> SkillVersion:
        parent = await self.get_skill(skill_id=skill_id, tenant_id=tenant_id)
        return await self._append_version(
            parent=parent,
            version_id=version_id,
            skill_id=skill_id,
            tenant_id=tenant_id,
            prompt_fragment=prompt_fragment,
            tool_names=tool_names,
            description=description,
            category=category,
            required_models=required_models,
            authored_by=authored_by,
            supporting_files=supporting_files,
            lazy_load=lazy_load,
            content_hash=content_hash,
            high_risk=high_risk,
            evolution_origin=evolution_origin,
            distilled_from_trajectory_key=distilled_from_trajectory_key,
            distilled_from_candidate_id=distilled_from_candidate_id,
            evolution_round=evolution_round,
        )

    async def _append_version(
        self,
        *,
        parent: Skill | None,
        version_id: UUID,
        skill_id: UUID,
        tenant_id: UUID | None,
        prompt_fragment: str,
        tool_names: Sequence[str],
        description: str,
        category: str | None,
        required_models: Sequence[str],
        authored_by: str,
        supporting_files: dict[str, dict[str, Any]] | None,
        lazy_load: bool,
        content_hash: bytes,
        high_risk: bool,
        evolution_origin: EvolutionOrigin | None = None,
        distilled_from_trajectory_key: str | None = None,
        distilled_from_candidate_id: UUID | None = None,
        evolution_round: int = 0,
    ) -> SkillVersion:
        if parent is None:
            raise SkillNotFoundError(str(skill_id))
        next_version = parent.latest_version + 1
        now = datetime.now(UTC)
        if authored_by not in {"human", "agent"}:
            msg = f"authored_by must be 'human' or 'agent' (got {authored_by!r})"
            raise ValueError(msg)
        from helix_agent.protocol.skill import SkillSupportingFile  # local to avoid cycle

        typed_supporting = {
            path: SkillSupportingFile(**meta) for path, meta in (supporting_files or {}).items()
        }
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
            supporting_files=typed_supporting,
            lazy_load=lazy_load,
            content_hash=content_hash,
            high_risk=high_risk,
            evolution_origin=evolution_origin,
            distilled_from_trajectory_key=distilled_from_trajectory_key,
            distilled_from_candidate_id=distilled_from_candidate_id,
            evolution_round=evolution_round,
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

    # ------------------------------------------------------------ evolution (Stream SE)

    async def record_eval_result(self, *, result: SkillEvalResult) -> SkillEvalResult:
        self._eval_results.append(result)
        return result

    async def list_eval_results(
        self, *, skill_id: UUID, tenant_id: UUID | None
    ) -> list[SkillEvalResult]:
        rows = [
            r for r in self._eval_results if r.skill_id == skill_id and r.tenant_id == tenant_id
        ]
        rows.sort(key=lambda r: r.created_at, reverse=True)
        return rows

    # ------------------------------------------------------------ platform (Stream X)
    #
    # Platform rows have ``tenant_id is None``. Mirrors the SQL store's
    # ``tenant_id IS NULL`` filter.

    async def create_platform_skill(
        self,
        *,
        skill_id: UUID,
        name: str,
        description: str = "",
        category: str | None = None,
        required_tier: TenantPlan = TenantPlan.FREE,
    ) -> Skill:
        return await self._create_skill_row(
            skill_id=skill_id,
            tenant_id=None,
            name=name,
            description=description,
            category=category,
            required_tier=required_tier,
        )

    async def get_platform_skill(self, *, skill_id: UUID) -> Skill | None:
        row = self._skills.get(skill_id)
        if row is None or row.tenant_id is not None:
            return None
        return row

    async def get_platform_skill_by_name(self, *, name: str) -> Skill | None:
        for row in self._skills.values():
            if row.tenant_id is None and row.name == name:
                return row
        return None

    async def list_platform_skills(
        self,
        *,
        status: SkillStatus | None = None,
        category: str | None = None,
        cursor: UUID | None = None,
        limit: int = 50,
    ) -> tuple[list[Skill], UUID | None]:
        rows = [r for r in self._skills.values() if r.tenant_id is None]
        return _paginate_skills(rows, status=status, category=category, cursor=cursor, limit=limit)

    async def add_platform_version(
        self,
        *,
        version_id: UUID,
        skill_id: UUID,
        prompt_fragment: str,
        tool_names: Sequence[str] = (),
        description: str = "",
        category: str | None = None,
        required_models: Sequence[str] = (),
        authored_by: str = "human",
        supporting_files: dict[str, dict[str, Any]] | None = None,
        lazy_load: bool = False,
        content_hash: bytes = b"",
        high_risk: bool = False,
    ) -> SkillVersion:
        parent = await self.get_platform_skill(skill_id=skill_id)
        return await self._append_version(
            parent=parent,
            version_id=version_id,
            skill_id=skill_id,
            tenant_id=None,
            prompt_fragment=prompt_fragment,
            tool_names=tool_names,
            description=description,
            category=category,
            required_models=required_models,
            authored_by=authored_by,
            supporting_files=supporting_files,
            lazy_load=lazy_load,
            content_hash=content_hash,
            high_risk=high_risk,
        )

    async def get_platform_version(self, *, version_id: UUID) -> SkillVersion | None:
        for v in self._versions:
            if v.id == version_id and v.tenant_id is None:
                return v
        return None

    async def get_platform_version_by_number(
        self, *, skill_id: UUID, version: int
    ) -> SkillVersion | None:
        for v in self._versions:
            if v.skill_id == skill_id and v.tenant_id is None and v.version == version:
                return v
        return None

    async def list_platform_versions(self, *, skill_id: UUID) -> list[SkillVersion]:
        versions = [v for v in self._versions if v.skill_id == skill_id and v.tenant_id is None]
        versions.sort(key=lambda v: v.version, reverse=True)
        return versions

    async def set_platform_status(self, *, skill_id: UUID, status: SkillStatus) -> Skill:
        row = await self.get_platform_skill(skill_id=skill_id)
        if row is None:
            raise SkillNotFoundError(str(skill_id))
        now = datetime.now(UTC)
        updated = row.model_copy(
            update={"status": status, "updated_at": now, "state_changed_at": now}
        )
        self._skills[skill_id] = updated
        return updated

    async def set_platform_pinned(self, *, skill_id: UUID, pinned: bool) -> Skill:
        row = await self.get_platform_skill(skill_id=skill_id)
        if row is None:
            raise SkillNotFoundError(str(skill_id))
        updated = row.model_copy(update={"pinned": pinned, "updated_at": datetime.now(UTC)})
        self._skills[skill_id] = updated
        return updated

    async def resolve_platform_by_name(self, *, name: str) -> SkillVersion | None:
        skill = await self.get_platform_skill_by_name(name=name)
        if skill is None or skill.status != SkillStatus.ACTIVE or skill.latest_version == 0:
            return None
        return await self.get_platform_version_by_number(
            skill_id=skill.id, version=skill.latest_version
        )

    async def resolve_platform_pinned(self, *, name: str, version: int) -> SkillVersion | None:
        skill = await self.get_platform_skill_by_name(name=name)
        if skill is None:
            return None
        return await self.get_platform_version_by_number(skill_id=skill.id, version=version)

    # ------------------------------------------------------------ Curator (Sprint #4)

    async def bump_last_used_at(self, *, skill_id: UUID, tenant_id: UUID) -> tuple[bool, bool]:
        row = await self.get_skill(skill_id=skill_id, tenant_id=tenant_id)
        if row is None:
            return (False, False)
        # archived / draft never advance via activity; only admin status
        # PATCH can move them. Pinned still bumps last_used_at — pin is
        # "don't auto-transition", not "don't track".
        if row.status not in (SkillStatus.ACTIVE, SkillStatus.STALE):
            return (False, False)
        now = datetime.now(UTC)
        auto_revived = row.status == SkillStatus.STALE
        updated = row.model_copy(
            update={
                "last_used_at": now,
                "status": SkillStatus.ACTIVE if auto_revived else row.status,
                "state_changed_at": now if auto_revived else row.state_changed_at,
                "updated_at": now,
            }
        )
        self._skills[skill_id] = updated
        return (True, auto_revived)

    async def curator_promote_active_to_stale(self, *, tenant_id: UUID, stale_days: int) -> int:
        from datetime import timedelta

        cutoff = datetime.now(UTC) - timedelta(days=stale_days)
        n = 0
        for skill_id, row in list(self._skills.items()):
            if row.tenant_id != tenant_id:
                continue
            if row.status != SkillStatus.ACTIVE or row.pinned:
                continue
            # NULL last_used_at means "never been used since migration";
            # rely on backfill to have populated it. Belt-and-braces: a
            # row with NULL last_used_at is treated as "infinitely
            # stale" (transition).
            if row.last_used_at is None or row.last_used_at < cutoff:
                now = datetime.now(UTC)
                self._skills[skill_id] = row.model_copy(
                    update={
                        "status": SkillStatus.STALE,
                        "state_changed_at": now,
                        "updated_at": now,
                    }
                )
                n += 1
        return n

    async def curator_promote_stale_to_archived(self, *, tenant_id: UUID, archive_days: int) -> int:
        from datetime import timedelta

        cutoff = datetime.now(UTC) - timedelta(days=archive_days)
        n = 0
        for skill_id, row in list(self._skills.items()):
            if row.tenant_id != tenant_id:
                continue
            if row.status != SkillStatus.STALE or row.pinned:
                continue
            if row.last_used_at is None or row.last_used_at < cutoff:
                now = datetime.now(UTC)
                self._skills[skill_id] = row.model_copy(
                    update={
                        "status": SkillStatus.ARCHIVED,
                        "state_changed_at": now,
                        "updated_at": now,
                    }
                )
                n += 1
        return n

    async def set_pinned(self, *, skill_id: UUID, tenant_id: UUID, pinned: bool) -> Skill:
        row = await self.get_skill(skill_id=skill_id, tenant_id=tenant_id)
        if row is None:
            raise SkillNotFoundError(str(skill_id))
        updated = row.model_copy(update={"pinned": pinned, "updated_at": datetime.now(UTC)})
        self._skills[skill_id] = updated
        return updated

    async def curator_distinct_tenant_ids(self) -> list[UUID]:
        seen: set[UUID] = set()
        for row in self._skills.values():
            # Stream X (Mini-ADR X-3): skip platform (NULL-tenant) skills —
            # they are never swept by per-tenant inactivity.
            if row.tenant_id is not None and row.status in (
                SkillStatus.ACTIVE,
                SkillStatus.STALE,
            ):
                seen.add(row.tenant_id)
        return sorted(seen)

    async def count_pinned(self) -> int:
        return sum(1 for row in self._skills.values() if row.pinned)


def _new_id() -> UUID:
    return uuid4()
