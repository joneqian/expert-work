"""In-memory ``ArtifactStore`` for unit tests."""

from __future__ import annotations

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
            artifact = existing.model_copy(
                update={"latest_version": existing.latest_version + 1, "updated_at": now}
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

    async def list_for_user(self, *, tenant_id: UUID, user_id: UUID) -> list[Artifact]:
        rows = [
            a for a in self._artifacts.values() if a.tenant_id == tenant_id and a.user_id == user_id
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
                if a.tenant_id == tenant_id and a.user_id == user_id and a.name == name
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
