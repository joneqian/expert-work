"""SQLAlchemy-backed ``ArtifactStore`` (Postgres / asyncpg)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from helix_agent.persistence.artifact.base import ArtifactStore
from helix_agent.persistence.models import ArtifactRow, ArtifactVersionRow
from helix_agent.protocol import Artifact, ArtifactKind, ArtifactVersion


def _row_to_artifact(row: ArtifactRow) -> Artifact:
    return Artifact(
        id=row.id,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        name=row.name,
        kind=row.kind,  # type: ignore[arg-type]
        latest_version=row.latest_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _row_to_version(row: ArtifactVersionRow) -> ArtifactVersion:
    return ArtifactVersion(
        id=row.id,
        artifact_id=row.artifact_id,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        version=row.version,
        path_in_workspace=row.path_in_workspace,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        created_in_thread=row.created_in_thread,
        created_at=row.created_at,
    )


class SqlArtifactStore(ArtifactStore):
    """Postgres-backed agent-artifact registry."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def save_version(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        name: str,
        kind: ArtifactKind,
        path_in_workspace: str,
        created_in_thread: str,
    ) -> ArtifactVersion:
        now = datetime.now(UTC)
        # INSERT ... ON CONFLICT DO UPDATE — a race-free upsert of the
        # logical artifact. On first save ``latest_version`` is 1; a
        # repeat save bumps it. ``kind`` is left out of the conflict
        # SET, so an existing artifact keeps its original kind.
        insert_artifact = pg_insert(ArtifactRow).values(
            tenant_id=tenant_id,
            user_id=user_id,
            name=name,
            kind=kind,
            latest_version=1,
            created_at=now,
            updated_at=now,
        )
        upsert = insert_artifact.on_conflict_do_update(
            constraint="artifact_identity_uniq",
            set_={"latest_version": ArtifactRow.latest_version + 1, "updated_at": now},
        ).returning(ArtifactRow.id, ArtifactRow.latest_version)

        version_id = uuid4()
        async with self._sf() as session:
            artifact_id, version = (await session.execute(upsert)).one()
            session.add(
                ArtifactVersionRow(
                    id=version_id,
                    artifact_id=artifact_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    version=version,
                    path_in_workspace=path_in_workspace,
                    created_in_thread=created_in_thread,
                    created_at=now,
                )
            )
            await session.commit()

        return ArtifactVersion(
            id=version_id,
            artifact_id=artifact_id,
            tenant_id=tenant_id,
            user_id=user_id,
            version=version,
            path_in_workspace=path_in_workspace,
            created_in_thread=created_in_thread,
            created_at=now,
        )

    async def list_for_user(self, *, tenant_id: UUID, user_id: UUID) -> list[Artifact]:
        stmt = (
            select(ArtifactRow)
            .where(ArtifactRow.tenant_id == tenant_id, ArtifactRow.user_id == user_id)
            .order_by(ArtifactRow.updated_at.desc())
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_artifact(row) for row in rows]

    async def get_latest_version(
        self, *, tenant_id: UUID, user_id: UUID, name: str
    ) -> ArtifactVersion | None:
        async with self._sf() as session:
            artifact = (
                await session.execute(
                    select(ArtifactRow).where(
                        ArtifactRow.tenant_id == tenant_id,
                        ArtifactRow.user_id == user_id,
                        ArtifactRow.name == name,
                    )
                )
            ).scalar_one_or_none()
            if artifact is None:
                return None
            row = (
                await session.execute(
                    select(ArtifactVersionRow).where(
                        ArtifactVersionRow.artifact_id == artifact.id,
                        ArtifactVersionRow.version == artifact.latest_version,
                    )
                )
            ).scalar_one_or_none()
        return _row_to_version(row) if row is not None else None

    async def set_version_digest(self, *, version_id: UUID, size_bytes: int, sha256: str) -> None:
        async with self._sf() as session:
            await session.execute(
                update(ArtifactVersionRow)
                .where(ArtifactVersionRow.id == version_id)
                .values(size_bytes=size_bytes, sha256=sha256)
            )
            await session.commit()
