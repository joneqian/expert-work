"""In-memory ``ArtifactStore`` for unit tests."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from helix_agent.persistence.artifact.base import ArtifactStore
from helix_agent.protocol import Artifact, ArtifactKind, ArtifactVersion

#: Aware sentinel so artifacts with no ``updated_at`` sort last without
#: a naive-vs-aware datetime comparison error.
_MIN_AWARE = datetime.min.replace(tzinfo=UTC)


class InMemoryArtifactStore(ArtifactStore):
    def __init__(self) -> None:
        self._artifacts: dict[UUID, Artifact] = {}
        self._versions: list[ArtifactVersion] = []

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
        existing = next(
            (
                a
                for a in self._artifacts.values()
                if a.tenant_id == tenant_id and a.user_id == user_id and a.name == name
            ),
            None,
        )
        if existing is None:
            artifact = Artifact(
                id=uuid4(),
                tenant_id=tenant_id,
                user_id=user_id,
                name=name,
                kind=kind,
                latest_version=1,
                created_at=now,
                updated_at=now,
            )
        else:
            # Re-save on a soft-deleted name un-deletes it (Mini-ADR J-25).
            artifact = existing.model_copy(
                update={
                    "latest_version": existing.latest_version + 1,
                    "updated_at": now,
                    "deleted_at": None,
                }
            )
        self._artifacts[artifact.id] = artifact

        version = ArtifactVersion(
            id=uuid4(),
            artifact_id=artifact.id,
            tenant_id=tenant_id,
            user_id=user_id,
            version=artifact.latest_version,
            path_in_workspace=path_in_workspace,
            created_in_thread=created_in_thread,
            created_at=now,
        )
        self._versions.append(version)
        return version

    async def list_for_user(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        include_deleted: bool = False,
    ) -> list[Artifact]:
        rows = [
            a
            for a in self._artifacts.values()
            if a.tenant_id == tenant_id
            and a.user_id == user_id
            and (include_deleted or a.deleted_at is None)
        ]
        rows.sort(key=lambda a: a.updated_at or _MIN_AWARE, reverse=True)
        return rows

    async def get_latest_version(
        self, *, tenant_id: UUID, user_id: UUID, name: str
    ) -> ArtifactVersion | None:
        artifact = next(
            (
                a
                for a in self._artifacts.values()
                if a.tenant_id == tenant_id
                and a.user_id == user_id
                and a.name == name
                and a.deleted_at is None
            ),
            None,
        )
        if artifact is None:
            return None
        return next(
            (
                v
                for v in self._versions
                if v.artifact_id == artifact.id and v.version == artifact.latest_version
            ),
            None,
        )

    async def set_version_digest(self, *, version_id: UUID, size_bytes: int, sha256: str) -> None:
        self._versions = [
            v.model_copy(update={"size_bytes": size_bytes, "sha256": sha256})
            if v.id == version_id
            else v
            for v in self._versions
        ]

    async def soft_delete(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        name: str,
        now: datetime,
    ) -> bool:
        for aid, a in list(self._artifacts.items()):
            if (
                a.tenant_id == tenant_id
                and a.user_id == user_id
                and a.name == name
                and a.deleted_at is None
            ):
                self._artifacts[aid] = a.model_copy(update={"deleted_at": now})
                return True
        return False

    async def list_expired(
        self,
        *,
        before: datetime,
        limit: int = 1000,
    ) -> list[Artifact]:
        rows = [
            a
            for a in self._artifacts.values()
            if a.deleted_at is not None and a.deleted_at < before
        ]
        rows.sort(key=lambda a: a.deleted_at or _MIN_AWARE)
        return rows[:limit]

    async def list_active_past_retention(
        self,
        *,
        before: datetime,
        limit: int = 1000,
    ) -> list[Artifact]:
        rows = [
            a
            for a in self._artifacts.values()
            if a.deleted_at is None and (a.updated_at or _MIN_AWARE) < before
        ]
        rows.sort(key=lambda a: a.updated_at or _MIN_AWARE)
        return rows[:limit]

    async def hard_delete(self, *, artifact_ids: Sequence[UUID]) -> int:
        ids = set(artifact_ids)
        removed = 0
        for aid in list(self._artifacts):
            if aid in ids:
                del self._artifacts[aid]
                removed += 1
        self._versions = [v for v in self._versions if v.artifact_id not in ids]
        return removed
