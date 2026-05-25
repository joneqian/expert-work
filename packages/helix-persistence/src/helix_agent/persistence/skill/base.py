"""Abstract ``SkillStore`` repository — Stream J.7a (Mini-ADR J-23).

Two-table model:

* ``skill`` — named bundle with lifecycle state + latest_version pointer.
* ``skill_version`` — append-only version rows carrying prompt / tools.

Implementations:

* :class:`helix_agent.persistence.skill.memory.InMemorySkillStore`
* :class:`helix_agent.persistence.skill.sql.SqlSkillStore`

Tenant scoping is enforced by SQL RLS (migration 0029) + by the
``tenant_id`` argument every call carries. Cross-tenant probes return
``None`` rather than raise (same hides-cross-tenant rule as
ThreadMetaStore / ImageUploadStore).
"""

from __future__ import annotations

import abc
from collections.abc import Sequence
from uuid import UUID

from helix_agent.protocol import Skill, SkillStatus, SkillVersion


class DuplicateSkillError(Exception):
    """``(tenant_id, name)`` already exists — admin POST collision."""

    def __init__(self, *, tenant_id: UUID, name: str) -> None:
        super().__init__(f"skill {name!r} already exists for tenant {tenant_id}")
        self.tenant_id = tenant_id
        self.name = name


class SkillNotFoundError(KeyError):
    """Skill id is unknown for this tenant — admin GET/PATCH 404 path."""


class SkillVersionNotFoundError(KeyError):
    """``(skill_id, version)`` row is absent — pin / rollback failure path."""


class SkillStore(abc.ABC):
    """Per-tenant skill library + version log.

    Three groups of operations:

    1. **Lifecycle** — create / list / get / patch-status / archive
       skill rows.
    2. **Version log** — append a new version + read versions by id or
       by ``(skill_id, version)``.
    3. **Build-time resolution** — bare-name lookup
       (:meth:`resolve_by_name`) and pinned lookup
       (:meth:`resolve_pinned`) used by the orchestrator's skill loader.
    """

    # ------------------------------------------------------------ skill (lifecycle)

    @abc.abstractmethod
    async def create_skill(
        self,
        *,
        skill_id: UUID,
        tenant_id: UUID,
        name: str,
        description: str = "",
        category: str | None = None,
    ) -> Skill:
        """Insert a new skill row (status=draft, latest_version=0).

        Raises :class:`DuplicateSkillError` when ``(tenant_id, name)``
        already exists.
        """

    @abc.abstractmethod
    async def get_skill(self, *, skill_id: UUID, tenant_id: UUID) -> Skill | None:
        """Return the skill row by id or ``None`` (cross-tenant probes hide)."""

    @abc.abstractmethod
    async def get_skill_by_name(self, *, tenant_id: UUID, name: str) -> Skill | None:
        """Return the skill row by ``(tenant_id, name)`` or ``None``."""

    @abc.abstractmethod
    async def list_skills(
        self,
        *,
        tenant_id: UUID,
        status: SkillStatus | None = None,
        category: str | None = None,
        cursor: UUID | None = None,
        limit: int = 50,
    ) -> tuple[list[Skill], UUID | None]:
        """Page through tenant skills.

        Returns ``(rows, next_cursor)``. ``next_cursor`` is ``None``
        when the last page was returned. Cursor is the last row's
        ``id`` for keyset pagination (sorted by ``created_at DESC, id``).
        """

    @abc.abstractmethod
    async def list_skills_all_tenants(
        self,
        *,
        status: SkillStatus | None = None,
        category: str | None = None,
        cursor: UUID | None = None,
        limit: int = 50,
    ) -> tuple[list[Skill], UUID | None]:
        """Cross-tenant page through skills — Stream N (Mini-ADR N-4).

        Identical shape to :meth:`list_skills` minus the ``tenant_id``
        filter. Caller MUST be inside ``bypass_rls_session()``
        (or :func:`applied_scope` with a :class:`CrossTenant`).
        """

    @abc.abstractmethod
    async def set_status(self, *, skill_id: UUID, tenant_id: UUID, status: SkillStatus) -> Skill:
        """Move the lifecycle state forward (or back to archived).

        Raises :class:`SkillNotFoundError` when the id is unknown for
        this tenant. Implementations also bump ``updated_at``.
        """

    # ------------------------------------------------------------ skill_version

    @abc.abstractmethod
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
        """Append the next version to a skill.

        ``version`` is auto-assigned = ``skill.latest_version + 1``.
        Updates ``skill.latest_version`` + mirrors ``description`` /
        ``category`` onto the parent skill row. Raises
        :class:`SkillNotFoundError` if the skill is unknown.
        """

    @abc.abstractmethod
    async def get_version(self, *, version_id: UUID, tenant_id: UUID) -> SkillVersion | None:
        """Return version row by id, or ``None`` (cross-tenant hides)."""

    @abc.abstractmethod
    async def get_version_by_number(
        self, *, skill_id: UUID, tenant_id: UUID, version: int
    ) -> SkillVersion | None:
        """Return version row by ``(skill_id, version)`` — pinned ref path."""

    @abc.abstractmethod
    async def list_versions(self, *, skill_id: UUID, tenant_id: UUID) -> list[SkillVersion]:
        """All versions of a skill, ordered ``version DESC``."""

    # ------------------------------------------------------------ resolve (loader)

    @abc.abstractmethod
    async def resolve_by_name(self, *, tenant_id: UUID, name: str) -> SkillVersion | None:
        """Bare-name resolution — current ``latest_version`` of an
        ``ACTIVE`` skill. Returns ``None`` when:

        * The name is unknown for this tenant
        * The skill exists but is in ``DRAFT`` or ``ARCHIVED`` state
        * The skill has no versions (latest_version=0)

        The orchestrator's skill loader treats ``None`` as
        ``SkillNotFoundError`` at the build-time boundary; the helper
        returns ``None`` so cross-tenant probes don't raise.
        """

    @abc.abstractmethod
    async def resolve_pinned(
        self, *, tenant_id: UUID, name: str, version: int
    ) -> SkillVersion | None:
        """Pinned ``name@version`` resolution.

        Returns the matching ``skill_version`` row regardless of the
        parent skill's lifecycle state (draft / active / archived all
        allowed — pinning is the reproducibility escape hatch). Returns
        ``None`` when either the name or the version row is absent.
        """
