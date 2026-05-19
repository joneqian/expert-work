"""Abstract ``ArtifactStore`` repository — Stream J.9.

Implementations:
- :class:`helix_agent.persistence.artifact.memory.InMemoryArtifactStore`
- :class:`helix_agent.persistence.artifact.sql.SqlArtifactStore`
"""

from __future__ import annotations

import abc
from uuid import UUID

from helix_agent.protocol import Artifact, ArtifactKind, ArtifactVersion


class ArtifactStore(abc.ABC):
    """Agent-artifact registry, scoped to ``(tenant_id, user_id)``."""

    @abc.abstractmethod
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
        """Register a new version of artifact ``name``.

        Creates the logical artifact at version 1 on first save, else
        appends the next version and bumps ``latest_version``. ``kind``
        is honoured only at creation — a later save never changes the
        kind of an existing artifact. Returns the new version row.
        """

    @abc.abstractmethod
    async def list_for_user(self, *, tenant_id: UUID, user_id: UUID) -> list[Artifact]:
        """The user's logical artifacts, most-recently-updated first."""

    @abc.abstractmethod
    async def get_latest_version(
        self, *, tenant_id: UUID, user_id: UUID, name: str
    ) -> ArtifactVersion | None:
        """Return the newest version of artifact ``name``, or ``None``.

        ``None`` when the user has no artifact under that name — never
        reveals a cross-user artifact.
        """

    @abc.abstractmethod
    async def set_version_digest(self, *, version_id: UUID, size_bytes: int, sha256: str) -> None:
        """Backfill a version's ``size_bytes`` / ``sha256``.

        Called the first time the content is read — the digest is not
        known at ``save_version`` time (the content lives in the
        workspace volume, which the persistence layer cannot read).
        """
