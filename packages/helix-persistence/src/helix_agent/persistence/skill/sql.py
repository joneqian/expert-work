"""SQLAlchemy-backed ``SkillStore`` — Stream J.7a (Mini-ADR J-23)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from helix_agent.persistence.models import SkillEvalResultRow, SkillRow, SkillVersionRow
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
from helix_agent.protocol.skill import SkillSupportingFile
from helix_agent.protocol.tenant_config import TenantPlan


def _skill_row_to_dto(row: SkillRow) -> Skill:
    return Skill(
        id=row.id,
        tenant_id=row.tenant_id,
        name=row.name,
        status=SkillStatus(row.status),
        latest_version=row.latest_version,
        description=row.description,
        category=row.category,
        # Stream X (Mini-ADR X-2) — minimum plan tier (platform skills).
        required_tier=TenantPlan(row.required_tier),
        # Capability Uplift Sprint #4 (Mini-ADR U-25). Existing rows
        # carry default values per migration 0043 backfill
        # (pinned=false, last_used_at=updated_at, state_changed_at=updated_at).
        pinned=bool(row.pinned),
        last_used_at=row.last_used_at,
        state_changed_at=row.state_changed_at,
        # Stream SE (Mini-ADR SE-A1) — ownership / lineage. Existing rows
        # carry migration 0065 defaults (visibility='tenant', NULL ids).
        visibility=row.visibility,  # type: ignore[arg-type]
        created_by_agent_id=row.created_by_agent_id,
        forked_from=row.forked_from,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _version_row_to_dto(row: SkillVersionRow) -> SkillVersion:
    raw_supporting = dict(row.supporting_files or {})
    typed_supporting = {path: SkillSupportingFile(**meta) for path, meta in raw_supporting.items()}
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
        # Capability Uplift Sprint #3 fields. Existing M0 rows have
        # default-empty values per migration 0042 backfill.
        supporting_files=typed_supporting,
        lazy_load=bool(row.lazy_load),
        content_hash=bytes(row.content_hash or b""),
        high_risk=bool(row.high_risk),
        # Stream SE (Mini-ADR SE-A1) — evolution provenance. Existing rows
        # carry migration 0065 defaults (origin NULL, round 0).
        evolution_origin=row.evolution_origin,  # type: ignore[arg-type]
        distilled_from_trajectory_key=row.distilled_from_trajectory_key,
        distilled_from_candidate_id=row.distilled_from_candidate_id,
        evolution_round=row.evolution_round,
        created_at=row.created_at,
    )


def _eval_result_row_to_dto(row: SkillEvalResultRow) -> SkillEvalResult:
    return SkillEvalResult(
        id=row.id,
        tenant_id=row.tenant_id,
        skill_id=row.skill_id,
        skill_version=row.skill_version,
        baseline_score=row.baseline_score,
        skill_score=row.skill_score,
        delta=row.delta,
        n_cases=row.n_cases,
        replay_source=row.replay_source,  # type: ignore[arg-type]
        verdict=row.verdict,  # type: ignore[arg-type]
        high_risk=bool(row.high_risk),
        evolution_round=row.evolution_round,
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
                required_tier=required_tier.value,
                visibility=visibility,
                created_by_agent_id=created_by_agent_id,
                forked_from=forked_from,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                # ``skill_tenant_name_uniq`` (COALESCE) violation — POST collision.
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
        return await self._list_skills(
            tenant_id=tenant_id, status=status, category=category, cursor=cursor, limit=limit
        )

    async def list_skills_all_tenants(
        self,
        *,
        status: SkillStatus | None = None,
        category: str | None = None,
        cursor: UUID | None = None,
        limit: int = 50,
    ) -> tuple[list[Skill], UUID | None]:
        # Stream N — no tenant filter; caller must wrap in bypass_rls_session().
        return await self._list_skills(
            tenant_id=None, status=status, category=category, cursor=cursor, limit=limit
        )

    async def _list_skills(
        self,
        *,
        tenant_id: UUID | None,
        status: SkillStatus | None,
        category: str | None,
        cursor: UUID | None,
        limit: int,
    ) -> tuple[list[Skill], UUID | None]:
        async with self._sf() as session:
            stmt = select(SkillRow).order_by(SkillRow.created_at.desc(), SkillRow.id)
            if tenant_id is not None:
                stmt = stmt.where(SkillRow.tenant_id == tenant_id)
            if status is not None:
                stmt = stmt.where(SkillRow.status == status.value)
            if category is not None:
                stmt = stmt.where(SkillRow.category == category)
            if cursor is not None:
                cur_stmt = select(SkillRow).where(SkillRow.id == cursor)
                if tenant_id is not None:
                    cur_stmt = cur_stmt.where(SkillRow.tenant_id == tenant_id)
                cur_row = (await session.execute(cur_stmt)).scalar_one_or_none()
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
            now = datetime.now(UTC)
            result = await session.execute(
                update(SkillRow)
                .where(SkillRow.id == skill_id, SkillRow.tenant_id == tenant_id)
                .values(status=status.value, updated_at=now, state_changed_at=now)
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
        supporting_files: dict[str, dict[str, Any]] | None = None,
        lazy_load: bool = False,
        content_hash: bytes = b"",
        high_risk: bool = False,
        evolution_origin: EvolutionOrigin | None = None,
        distilled_from_trajectory_key: str | None = None,
        distilled_from_candidate_id: UUID | None = None,
        evolution_round: int = 0,
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
                # Capability Uplift Sprint #3 fields. Default-empty values
                # keep Stream J.7a JSON-API path safe; ZIP / supporting-files
                # mutation paths compute + pass real values.
                supporting_files=supporting_files or {},
                lazy_load=lazy_load,
                content_hash=content_hash,
                high_risk=high_risk,
                # Stream SE (SE-A1) — evolution provenance.
                evolution_origin=evolution_origin,
                distilled_from_trajectory_key=distilled_from_trajectory_key,
                distilled_from_candidate_id=distilled_from_candidate_id,
                evolution_round=evolution_round,
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

    # ------------------------------------------------------------ evolution (Stream SE)

    async def record_eval_result(self, *, result: SkillEvalResult) -> SkillEvalResult:
        async with self._sf() as session:
            row = SkillEvalResultRow(
                id=result.id,
                tenant_id=result.tenant_id,
                skill_id=result.skill_id,
                skill_version=result.skill_version,
                baseline_score=result.baseline_score,
                skill_score=result.skill_score,
                delta=result.delta,
                n_cases=result.n_cases,
                replay_source=result.replay_source,
                verdict=result.verdict,
                high_risk=result.high_risk,
                evolution_round=result.evolution_round,
                created_at=result.created_at,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _eval_result_row_to_dto(row)

    async def list_eval_results(
        self, *, skill_id: UUID, tenant_id: UUID | None
    ) -> list[SkillEvalResult]:
        async with self._sf() as session:
            stmt = (
                select(SkillEvalResultRow)
                .where(SkillEvalResultRow.skill_id == skill_id)
                .order_by(SkillEvalResultRow.created_at.desc())
            )
            stmt = (
                stmt.where(SkillEvalResultRow.tenant_id == tenant_id)
                if tenant_id is not None
                else stmt.where(SkillEvalResultRow.tenant_id.is_(None))
            )
            rows = (await session.execute(stmt)).scalars().all()
        return [_eval_result_row_to_dto(r) for r in rows]

    # ------------------------------------------------------------ platform (Stream X)
    #
    # NULL-tenant rows in the same tables. ``WHERE tenant_id IS NULL``
    # everywhere. Caller MUST be inside ``bypass_rls_session()`` so the
    # 0057 ``IS NOT DISTINCT FROM`` policy lets the NULL rows through.

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
        async with self._sf() as session:
            row = (
                await session.execute(
                    select(SkillRow).where(SkillRow.id == skill_id, SkillRow.tenant_id.is_(None))
                )
            ).scalar_one_or_none()
        return _skill_row_to_dto(row) if row is not None else None

    async def get_platform_skill_by_name(self, *, name: str) -> Skill | None:
        async with self._sf() as session:
            row = (
                await session.execute(
                    select(SkillRow).where(SkillRow.tenant_id.is_(None), SkillRow.name == name)
                )
            ).scalar_one_or_none()
        return _skill_row_to_dto(row) if row is not None else None

    async def list_platform_skills(
        self,
        *,
        status: SkillStatus | None = None,
        category: str | None = None,
        cursor: UUID | None = None,
        limit: int = 50,
    ) -> tuple[list[Skill], UUID | None]:
        async with self._sf() as session:
            stmt = (
                select(SkillRow)
                .where(SkillRow.tenant_id.is_(None))
                .order_by(SkillRow.created_at.desc(), SkillRow.id)
            )
            if status is not None:
                stmt = stmt.where(SkillRow.status == status.value)
            if category is not None:
                stmt = stmt.where(SkillRow.category == category)
            if cursor is not None:
                cur_row = (
                    await session.execute(
                        select(SkillRow).where(SkillRow.id == cursor, SkillRow.tenant_id.is_(None))
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
        if authored_by not in {"human", "agent"}:
            msg = f"authored_by must be 'human' or 'agent' (got {authored_by!r})"
            raise ValueError(msg)
        now = datetime.now(UTC)
        async with self._sf() as session:
            parent = (
                await session.execute(
                    select(SkillRow).where(SkillRow.id == skill_id, SkillRow.tenant_id.is_(None))
                )
            ).scalar_one_or_none()
            if parent is None:
                raise SkillNotFoundError(str(skill_id))
            next_version = parent.latest_version + 1
            new_description = description or parent.description
            new_category = category if category is not None else parent.category
            version_row = SkillVersionRow(
                id=version_id,
                tenant_id=None,
                skill_id=skill_id,
                version=next_version,
                prompt_fragment=prompt_fragment,
                tool_names=list(tool_names),
                description=new_description,
                category=new_category,
                required_models=list(required_models),
                authored_by=authored_by,
                supporting_files=supporting_files or {},
                lazy_load=lazy_load,
                content_hash=content_hash,
                high_risk=high_risk,
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

    async def get_platform_version(self, *, version_id: UUID) -> SkillVersion | None:
        async with self._sf() as session:
            row = (
                await session.execute(
                    select(SkillVersionRow).where(
                        SkillVersionRow.id == version_id,
                        SkillVersionRow.tenant_id.is_(None),
                    )
                )
            ).scalar_one_or_none()
        return _version_row_to_dto(row) if row is not None else None

    async def get_platform_version_by_number(
        self, *, skill_id: UUID, version: int
    ) -> SkillVersion | None:
        async with self._sf() as session:
            row = (
                await session.execute(
                    select(SkillVersionRow).where(
                        SkillVersionRow.skill_id == skill_id,
                        SkillVersionRow.tenant_id.is_(None),
                        SkillVersionRow.version == version,
                    )
                )
            ).scalar_one_or_none()
        return _version_row_to_dto(row) if row is not None else None

    async def list_platform_versions(self, *, skill_id: UUID) -> list[SkillVersion]:
        async with self._sf() as session:
            rows = (
                (
                    await session.execute(
                        select(SkillVersionRow)
                        .where(
                            SkillVersionRow.skill_id == skill_id,
                            SkillVersionRow.tenant_id.is_(None),
                        )
                        .order_by(SkillVersionRow.version.desc())
                    )
                )
                .scalars()
                .all()
            )
        return [_version_row_to_dto(r) for r in rows]

    async def set_platform_status(self, *, skill_id: UUID, status: SkillStatus) -> Skill:
        async with self._sf() as session:
            now = datetime.now(UTC)
            result = await session.execute(
                update(SkillRow)
                .where(SkillRow.id == skill_id, SkillRow.tenant_id.is_(None))
                .values(status=status.value, updated_at=now, state_changed_at=now)
                .returning(SkillRow)
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise SkillNotFoundError(str(skill_id))
            await session.commit()
        return _skill_row_to_dto(row)

    async def set_platform_pinned(self, *, skill_id: UUID, pinned: bool) -> Skill:
        async with self._sf() as session:
            result = await session.execute(
                update(SkillRow)
                .where(SkillRow.id == skill_id, SkillRow.tenant_id.is_(None))
                .values(pinned=pinned, updated_at=datetime.now(UTC))
                .returning(SkillRow)
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise SkillNotFoundError(str(skill_id))
            await session.commit()
        return _skill_row_to_dto(row)

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
        """Atomic activity bump + stale→active auto-revive (Mini-ADR U-27/U-29).

        Single transaction: SELECT prior status (with row lock) → UPDATE
        conditionally flips ``stale`` to ``active``. archived / draft
        rows are filtered by the WHERE clause → no-op + return
        (False, False). Pinned rows still bump last_used_at — pin means
        "don't auto-transition", not "don't track activity".
        """
        async with self._sf() as session:
            prior = (
                await session.execute(
                    select(SkillRow.status)
                    .where(SkillRow.id == skill_id, SkillRow.tenant_id == tenant_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if prior is None or prior not in ("active", "stale"):
                # archived / draft / missing → cold path no-op
                await session.commit()
                return (False, False)
            now = datetime.now(UTC)
            auto_revived = prior == "stale"
            await session.execute(
                update(SkillRow)
                .where(SkillRow.id == skill_id, SkillRow.tenant_id == tenant_id)
                .values(
                    last_used_at=now,
                    status="active" if auto_revived else prior,
                    state_changed_at=now if auto_revived else SkillRow.state_changed_at,
                    updated_at=now,
                )
            )
            await session.commit()
        return (True, auto_revived)

    async def curator_promote_active_to_stale(self, *, tenant_id: UUID, stale_days: int) -> int:
        from datetime import timedelta

        cutoff = datetime.now(UTC) - timedelta(days=stale_days)
        async with self._sf() as session:
            now = datetime.now(UTC)
            result = await session.execute(
                update(SkillRow)
                .where(
                    SkillRow.tenant_id == tenant_id,
                    SkillRow.status == "active",
                    SkillRow.pinned.is_(False),
                    # NULL last_used_at sweeps as if "infinitely stale" so
                    # rows created before the migration backfill don't
                    # linger as active forever (defensive: migration 0043
                    # backfills last_used_at to updated_at, so this branch
                    # only catches application-level inserts that forgot
                    # to seed the column).
                    (SkillRow.last_used_at.is_(None) | (SkillRow.last_used_at < cutoff)),
                )
                .values(status="stale", state_changed_at=now, updated_at=now)
            )
            await session.commit()
        # ``Result.rowcount`` is only typed on ``CursorResult``; the
        # base ``Result`` mypy sees from ``session.execute(update(...))``
        # exposes it at runtime but not at the type level. Cast through.
        return int(getattr(result, "rowcount", 0) or 0)

    async def curator_promote_stale_to_archived(self, *, tenant_id: UUID, archive_days: int) -> int:
        from datetime import timedelta

        cutoff = datetime.now(UTC) - timedelta(days=archive_days)
        async with self._sf() as session:
            now = datetime.now(UTC)
            result = await session.execute(
                update(SkillRow)
                .where(
                    SkillRow.tenant_id == tenant_id,
                    SkillRow.status == "stale",
                    SkillRow.pinned.is_(False),
                    (SkillRow.last_used_at.is_(None) | (SkillRow.last_used_at < cutoff)),
                )
                .values(status="archived", state_changed_at=now, updated_at=now)
            )
            await session.commit()
        # ``Result.rowcount`` is only typed on ``CursorResult``; the
        # base ``Result`` mypy sees from ``session.execute(update(...))``
        # exposes it at runtime but not at the type level. Cast through.
        return int(getattr(result, "rowcount", 0) or 0)

    async def set_pinned(self, *, skill_id: UUID, tenant_id: UUID, pinned: bool) -> Skill:
        async with self._sf() as session:
            result = await session.execute(
                update(SkillRow)
                .where(SkillRow.id == skill_id, SkillRow.tenant_id == tenant_id)
                .values(pinned=pinned, updated_at=datetime.now(UTC))
                .returning(SkillRow)
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise SkillNotFoundError(str(skill_id))
            await session.commit()
        return _skill_row_to_dto(row)

    async def curator_distinct_tenant_ids(self) -> list[UUID]:
        async with self._sf() as session:
            rows = (
                await session.execute(
                    select(SkillRow.tenant_id)
                    .where(
                        SkillRow.status.in_(["active", "stale"]),
                        # Stream X (Mini-ADR X-3): platform (NULL-tenant) skills
                        # are shared resources, never swept by per-tenant
                        # inactivity. Exclude them from the Curator's tenant list.
                        SkillRow.tenant_id.isnot(None),
                    )
                    .distinct()
                )
            ).all()
        return [r[0] for r in rows if r[0] is not None]

    async def count_pinned(self) -> int:
        from sqlalchemy import func

        async with self._sf() as session:
            result = (
                await session.execute(
                    select(func.count()).select_from(SkillRow).where(SkillRow.pinned.is_(True))
                )
            ).scalar_one()
        return int(result or 0)
