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
from typing import Any
from uuid import UUID

from helix_agent.protocol import Skill, SkillStatus, SkillVersion
from helix_agent.protocol.tenant_config import TenantPlan


class DuplicateSkillError(Exception):
    """``(tenant_id, name)`` already exists — admin POST collision.

    ``tenant_id`` is ``None`` for a platform-skill (NULL-tenant) collision
    (Stream X, Mini-ADR X-1).
    """

    def __init__(self, *, tenant_id: UUID | None, name: str) -> None:
        scope = f"tenant {tenant_id}" if tenant_id is not None else "the platform library"
        super().__init__(f"skill {name!r} already exists for {scope}")
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
        required_tier: TenantPlan = TenantPlan.FREE,
    ) -> Skill:
        """Insert a new skill row (status=draft, latest_version=0).

        Raises :class:`DuplicateSkillError` when ``(tenant_id, name)``
        already exists. ``required_tier`` defaults to ``FREE`` — only
        meaningful for platform skills (a tenant's own skills are always
        usable), but accepted on the tenant path for symmetry (Mini-ADR X-2).
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
        # Capability Uplift Sprint #3 (Mini-ADR U-16 / U-15 / U-21 / U-24).
        # Default values keep Stream J.7a callers (M0 JSON-API path) working
        # without touching them; the ZIP / supporting-files paths populate
        # all four explicitly.
        supporting_files: dict[str, dict[str, Any]] | None = None,
        lazy_load: bool = False,
        content_hash: bytes = b"",
        high_risk: bool = False,
    ) -> SkillVersion:
        """Append the next version to a skill.

        ``version`` is auto-assigned = ``skill.latest_version + 1``.
        Updates ``skill.latest_version`` + mirrors ``description`` /
        ``category`` onto the parent skill row. Raises
        :class:`SkillNotFoundError` if the skill is unknown.

        ``supporting_files`` / ``lazy_load`` / ``content_hash`` / ``high_risk``
        default to the pre-Sprint #3 shape so the Stream J.7a JSON-API
        path doesn't need to compute them. Callers that DO compute them
        (ZIP import + supporting-files single-file mutation API) must
        pass all four to avoid leaving a row with an empty
        ``content_hash`` (which would fire a spurious drift alert on the
        first ``skill_view``).
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

    # -------------------------------------------------- platform (Stream X)
    #
    # Platform skills are NULL-tenant rows in the SAME ``skill`` /
    # ``skill_version`` tables (Mini-ADR X-1). They are addressed by these
    # explicit ``*_platform_*`` methods (which filter ``tenant_id IS NULL``)
    # rather than by passing ``tenant_id=None`` to the tenant methods — the
    # latter already means "all tenants" (cross-tenant system_admin reads).
    #
    # Every platform method MUST be invoked inside ``bypass_rls_session()``
    # (an UNSCOPED session): under the 0057 ``IS NOT DISTINCT FROM`` policy,
    # an unset ``app.tenant_id`` makes ``tenant_id IS NULL`` rows visible,
    # while any tenant-scoped session sees ZERO platform rows (the X-8 / W-8
    # isolation property). Wiring the bypass is the caller's job (X3 / X4).

    @abc.abstractmethod
    async def create_platform_skill(
        self,
        *,
        skill_id: UUID,
        name: str,
        description: str = "",
        category: str | None = None,
        required_tier: TenantPlan = TenantPlan.FREE,
    ) -> Skill:
        """Insert a platform (NULL-tenant) skill row.

        Records carry ``tenant_id=None``. Raises :class:`DuplicateSkillError`
        when a platform skill with ``name`` already exists. Caller MUST be
        inside ``bypass_rls_session()``.
        """

    @abc.abstractmethod
    async def get_platform_skill(self, *, skill_id: UUID) -> Skill | None:
        """Return the platform skill by id (``tenant_id IS NULL``) or ``None``.

        Caller MUST be inside ``bypass_rls_session()``.
        """

    @abc.abstractmethod
    async def get_platform_skill_by_name(self, *, name: str) -> Skill | None:
        """Return the platform skill by ``name`` or ``None``.

        Caller MUST be inside ``bypass_rls_session()``.
        """

    @abc.abstractmethod
    async def list_platform_skills(
        self,
        *,
        status: SkillStatus | None = None,
        category: str | None = None,
        cursor: UUID | None = None,
        limit: int = 50,
    ) -> tuple[list[Skill], UUID | None]:
        """Page through platform (NULL-tenant) skills.

        Same shape as :meth:`list_skills` but filters ``tenant_id IS NULL``.
        Caller MUST be inside ``bypass_rls_session()``.
        """

    @abc.abstractmethod
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
        """Append a version to a platform skill (``tenant_id=None``).

        Same semantics as :meth:`add_version`. Raises
        :class:`SkillNotFoundError` if the platform skill is unknown.
        Caller MUST be inside ``bypass_rls_session()``.
        """

    @abc.abstractmethod
    async def get_platform_version(self, *, version_id: UUID) -> SkillVersion | None:
        """Return a platform version row by id, or ``None``.

        Caller MUST be inside ``bypass_rls_session()``.
        """

    @abc.abstractmethod
    async def get_platform_version_by_number(
        self, *, skill_id: UUID, version: int
    ) -> SkillVersion | None:
        """Return a platform version by ``(skill_id, version)`` — pinned path.

        Caller MUST be inside ``bypass_rls_session()``.
        """

    @abc.abstractmethod
    async def list_platform_versions(self, *, skill_id: UUID) -> list[SkillVersion]:
        """All versions of a platform skill, ordered ``version DESC``.

        Caller MUST be inside ``bypass_rls_session()``.
        """

    @abc.abstractmethod
    async def set_platform_status(self, *, skill_id: UUID, status: SkillStatus) -> Skill:
        """Move a platform skill's lifecycle state. Raises
        :class:`SkillNotFoundError` if unknown. Caller MUST be inside
        ``bypass_rls_session()``.
        """

    @abc.abstractmethod
    async def set_platform_pinned(self, *, skill_id: UUID, pinned: bool) -> Skill:
        """Admin pin / unpin a platform skill. Raises
        :class:`SkillNotFoundError` if unknown. Caller MUST be inside
        ``bypass_rls_session()``.
        """

    @abc.abstractmethod
    async def resolve_platform_by_name(self, *, name: str) -> SkillVersion | None:
        """Bare-name resolution of a platform skill — current
        ``latest_version`` of an ``ACTIVE`` platform skill, else ``None``.
        Caller MUST be inside ``bypass_rls_session()``.
        """

    @abc.abstractmethod
    async def resolve_platform_pinned(self, *, name: str, version: int) -> SkillVersion | None:
        """Pinned ``name@version`` resolution of a platform skill (any
        lifecycle state). Returns ``None`` when name or version is absent.
        Caller MUST be inside ``bypass_rls_session()``.
        """

    # -------------------------------------------------- Curator (Sprint #4)

    @abc.abstractmethod
    async def bump_last_used_at(self, *, skill_id: UUID, tenant_id: UUID) -> tuple[bool, bool]:
        """Mark a skill as just-used by an agent build or skill_view call.

        Atomic semantics (Mini-ADR U-27 / U-29):
        * ``active`` → ``last_used_at = now()``, status unchanged
        * ``stale`` → ``last_used_at = now()`` AND status → ``active``
          (auto-revive) AND ``state_changed_at = now()``
        * ``archived`` → no-op (cold storage; admin must unarchive)
        * ``draft`` → no-op (draft skills don't have an "active" lifecycle
          to lapse out of)
        * pinned → ``last_used_at = now()`` regardless, status unchanged

        Returns ``(updated, auto_revived)``:
        * ``updated`` — ``True`` iff at least one row was changed
        * ``auto_revived`` — ``True`` iff the row transitioned
          ``stale → active`` as part of this call

        Caller is responsible for application-layer throttling
        (:class:`control_plane.skill_activity.ThrottledActivityRecorder`);
        the store itself always executes the SQL when invoked.
        """

    @abc.abstractmethod
    async def curator_promote_active_to_stale(self, *, tenant_id: UUID, stale_days: int) -> int:
        """Batch-transition: ``active`` skills with last_used_at older than
        ``stale_days`` (and not pinned) → ``stale``.

        Returns the number of rows transitioned.
        """

    @abc.abstractmethod
    async def curator_promote_stale_to_archived(self, *, tenant_id: UUID, archive_days: int) -> int:
        """Batch-transition: ``stale`` skills with last_used_at older than
        ``archive_days`` (and not pinned) → ``archived``.

        Returns the number of rows transitioned.
        """

    @abc.abstractmethod
    async def set_pinned(self, *, skill_id: UUID, tenant_id: UUID, pinned: bool) -> Skill:
        """Admin pin / unpin. Raises :class:`SkillNotFoundError` if the
        id is unknown for this tenant. ``updated_at`` advances;
        ``state_changed_at`` does NOT (pin is metadata, not a state
        transition)."""

    @abc.abstractmethod
    async def curator_distinct_tenant_ids(self) -> list[UUID]:
        """Tenants that have at least one ``active`` or ``stale`` skill.

        The Curator's sweep iterates this list (vs every tenant on the
        platform) so a fresh tenant with no skills doesn't burn cycles.
        Caller MUST be inside ``bypass_rls_session()`` — Curator is a
        platform background worker, not a tenant-scoped request.

        Stream X (Mini-ADR X-3): platform (NULL-tenant) skills are EXCLUDED
        (``WHERE tenant_id IS NOT NULL``) — they are shared resources whose
        lifecycle is system_admin-managed, not subject to any one tenant's
        inactivity sweep.
        """

    @abc.abstractmethod
    async def count_pinned(self) -> int:
        """Total pinned skills across all tenants — for the
        ``helix_uplift_skill_pinned_total`` gauge. Caller MUST be inside
        ``bypass_rls_session()``."""
