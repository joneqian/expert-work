"""Abstract curation stores — Stream J.12 (Mini-ADR J-43).

Two repositories backing the learning / feedback loop
(STREAM-J-DESIGN § 17):

* :class:`EvalDatasetStore` — the curated eval cases (the
  ``eval_dataset`` table), shared with J.13.
* :class:`CurationCandidateStore` — trajectories the curation worker
  flagged for human review (the ``curation_candidate`` table).

Both are tenant-scoped. The curation worker upserts candidates while
iterating trajectories cross-tenant; it re-scopes to each trajectory's
own tenant before the upsert, so these stores need no cross-tenant
method.

Implementations:
- :mod:`helix_agent.persistence.curation.memory`
- :mod:`helix_agent.persistence.curation.sql`
"""

from __future__ import annotations

import abc
from uuid import UUID

from helix_agent.protocol import (
    CandidateStatus,
    CurationCandidateRecord,
    CurationSignal,
    EvalDatasetRecord,
)


class EvalDatasetStore(abc.ABC):
    """Registry of curated eval cases — the ``eval_dataset`` table."""

    @abc.abstractmethod
    async def create(self, record: EvalDatasetRecord) -> EvalDatasetRecord:
        """Persist a new curated eval case."""

    @abc.abstractmethod
    async def get(self, *, dataset_id: UUID, tenant_id: UUID) -> EvalDatasetRecord | None:
        """Return the case row, or ``None`` when unknown / cross-tenant."""

    @abc.abstractmethod
    async def list_by_agent(self, *, tenant_id: UUID, agent_name: str) -> list[EvalDatasetRecord]:
        """Return every curated case for ``agent_name`` — backs the export CLI."""

    @abc.abstractmethod
    async def list_by_tenant(self, *, tenant_id: UUID) -> list[EvalDatasetRecord]:
        """Return every curated case under the tenant, newest first."""

    @abc.abstractmethod
    async def update(self, record: EvalDatasetRecord) -> bool:
        """Replace a case row (matched by ``id`` + ``tenant_id``); return hit."""

    @abc.abstractmethod
    async def delete(self, *, dataset_id: UUID, tenant_id: UUID) -> bool:
        """Delete a case row; return ``True`` iff it existed."""

    @abc.abstractmethod
    async def count_by_tenant(self, *, tenant_id: UUID) -> int:
        """Count a tenant's curated cases — backs the create-time quota."""


class CurationCandidateStore(abc.ABC):
    """Registry of curation candidates — the ``curation_candidate`` table."""

    @abc.abstractmethod
    async def upsert(self, record: CurationCandidateRecord) -> bool:
        """Insert ``record`` only if no candidate exists for its trajectory.

        Keyed by ``(tenant_id, trajectory_key)``. Returns ``True`` when a
        row was inserted, ``False`` when a candidate already existed —
        the worker re-scans trajectories every cycle and relies on this
        to flag each at most once (Mini-ADR J-43).
        """

    @abc.abstractmethod
    async def get(self, *, candidate_id: UUID, tenant_id: UUID) -> CurationCandidateRecord | None:
        """Return the candidate row, or ``None`` when unknown / cross-tenant."""

    @abc.abstractmethod
    async def get_by_trajectory_key(
        self, *, tenant_id: UUID, trajectory_key: str
    ) -> CurationCandidateRecord | None:
        """Return the candidate for a trajectory, or ``None`` when none exists."""

    @abc.abstractmethod
    async def list_for_review(
        self,
        *,
        tenant_id: UUID,
        agent_name: str | None = None,
        status: CandidateStatus | None = None,
        signal: CurationSignal | None = None,
    ) -> list[CurationCandidateRecord]:
        """Return candidates for the review UI, newest first.

        ``agent_name`` / ``status`` / ``signal`` are optional filters.
        """

    @abc.abstractmethod
    async def update(self, record: CurationCandidateRecord) -> bool:
        """Replace a candidate row (matched by ``id`` + ``tenant_id``); return hit.

        Used by the curation API to record promote / dismiss verdicts.
        """
