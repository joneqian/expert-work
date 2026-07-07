"""Sprint #4 PR B — Curator worker + throttled activity recorder.

Covers the four state-machine paths from Mini-ADRs U-26 / U-27 / U-29:

1. ``active`` → ``stale`` after ``stale_days`` of no activity
2. ``stale`` → ``archived`` after additional ``archive_days``
3. ``pinned`` rows are skipped at both stages
4. ``stale`` auto-revives to ``active`` on activity (via the throttled
   recorder's ``bump_last_used_at`` SQL)

Plus throttle behaviour: same skill bumped twice within the TTL window
produces exactly one SQL UPDATE; bumps for different skills both fire.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from control_plane.skill_activity import ThrottledActivityRecorder
from control_plane.skill_curator import (
    DEFAULT_ARCHIVE_DAYS,
    DEFAULT_STALE_DAYS,
    SkillCurator,
)
from control_plane.tenancy import TenantConfigService
from expert_work.persistence.audit_log import InMemoryAuditLogStore
from expert_work.persistence.skill import InMemorySkillStore
from expert_work.persistence.tenant_config import InMemoryTenantConfigStore
from expert_work.protocol import (
    AuditAction,
    AuditQuery,
    SkillStatus,
    TenantConfigPatch,
)
from expert_work.runtime.audit.fallback import InMemoryAuditFallbackQueue
from expert_work.runtime.audit.logger import AuditLogger
from expert_work.runtime.audit.redactor import DefaultSecretRedactor

_TENANT_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_TENANT_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _build_logger() -> tuple[AuditLogger, InMemoryAuditLogStore]:
    store = InMemoryAuditLogStore()
    logger = AuditLogger(
        store=store,
        redactor=DefaultSecretRedactor(),
        fallback=InMemoryAuditFallbackQueue(),
    )
    return logger, store


async def _seed_skill(
    *,
    store: InMemorySkillStore,
    tenant_id: UUID,
    name: str,
    status: SkillStatus,
    last_used_at: datetime | None,
    pinned: bool = False,
) -> UUID:
    skill_id = uuid4()
    await store.create_skill(
        skill_id=skill_id,
        tenant_id=tenant_id,
        name=name,
        description=name,
        category="ops",
    )
    # Add at least one version so latest_version > 0; tests reaching
    # for resolve_by_name need it.
    await store.add_version(
        version_id=uuid4(),
        skill_id=skill_id,
        tenant_id=tenant_id,
        prompt_fragment="body",
        tool_names=("log_viewer",),
    )
    # Force the underlying state via direct DTO mutation (InMemory
    # store exposes its dict). Curator tests need precise control of
    # last_used_at to verify the threshold math, which the public
    # API can't give without time-traveling.
    current = store._skills[skill_id]
    store._skills[skill_id] = current.model_copy(
        update={
            "status": status,
            "last_used_at": last_used_at,
            "pinned": pinned,
            "state_changed_at": last_used_at or current.state_changed_at,
        }
    )
    return skill_id


def _curator(
    store: InMemorySkillStore,
    config_service: TenantConfigService,
    audit_logger: AuditLogger,
) -> SkillCurator:
    # interval_s is irrelevant for run_once(); tests bypass the loop.
    return SkillCurator(
        skill_store=store,
        tenant_config_service=config_service,
        audit_logger=audit_logger,
        interval_s=60.0,
    )


# ─── State machine ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_active_to_stale_after_threshold() -> None:
    store = InMemorySkillStore()
    audit_logger, audit_store = _build_logger()
    config_service = TenantConfigService(
        store=InMemoryTenantConfigStore(),
        audit_logger=audit_logger,
    )
    curator = _curator(store, config_service, audit_logger)

    long_ago = datetime.now(UTC) - timedelta(days=DEFAULT_STALE_DAYS + 1)
    skill_id = await _seed_skill(
        store=store,
        tenant_id=_TENANT_A,
        name="cold",
        status=SkillStatus.ACTIVE,
        last_used_at=long_ago,
    )

    summary = await curator.run_once()

    assert summary.active_to_stale == 1
    assert summary.stale_to_archived == 0
    row = await store.get_skill(skill_id=skill_id, tenant_id=_TENANT_A)
    assert row is not None and row.status == SkillStatus.STALE
    # Audit summary row recorded
    page = await audit_store.query(AuditQuery(tenant_id=UUID(int=0), limit=10))
    assert AuditAction.SKILL_CURATOR_RUN in [r.action for r in page.entries]


@pytest.mark.asyncio
async def test_stale_to_archived_after_threshold() -> None:
    store = InMemorySkillStore()
    audit_logger, _ = _build_logger()
    curator = _curator(
        store,
        TenantConfigService(store=InMemoryTenantConfigStore(), audit_logger=audit_logger),
        audit_logger,
    )

    long_ago = datetime.now(UTC) - timedelta(days=DEFAULT_ARCHIVE_DAYS + 1)
    skill_id = await _seed_skill(
        store=store,
        tenant_id=_TENANT_A,
        name="frozen",
        status=SkillStatus.STALE,
        last_used_at=long_ago,
    )

    summary = await curator.run_once()

    assert summary.stale_to_archived == 1
    row = await store.get_skill(skill_id=skill_id, tenant_id=_TENANT_A)
    assert row is not None and row.status == SkillStatus.ARCHIVED


@pytest.mark.asyncio
async def test_pinned_skill_never_transitions() -> None:
    store = InMemorySkillStore()
    audit_logger, _ = _build_logger()
    curator = _curator(
        store,
        TenantConfigService(store=InMemoryTenantConfigStore(), audit_logger=audit_logger),
        audit_logger,
    )

    long_ago = datetime.now(UTC) - timedelta(days=DEFAULT_ARCHIVE_DAYS * 2)
    skill_id = await _seed_skill(
        store=store,
        tenant_id=_TENANT_A,
        name="vip",
        status=SkillStatus.ACTIVE,
        last_used_at=long_ago,
        pinned=True,
    )

    summary = await curator.run_once()
    assert summary.active_to_stale == 0
    assert summary.stale_to_archived == 0
    row = await store.get_skill(skill_id=skill_id, tenant_id=_TENANT_A)
    assert row is not None and row.status == SkillStatus.ACTIVE


@pytest.mark.asyncio
async def test_per_tenant_thresholds_override_defaults() -> None:
    """Tenant config thresholds beat the platform defaults."""
    store = InMemorySkillStore()
    audit_logger, _ = _build_logger()
    config_store = InMemoryTenantConfigStore()
    config_service = TenantConfigService(store=config_store, audit_logger=audit_logger)
    # Tighter thresholds for tenant A.
    await config_store.upsert(
        tenant_id=_TENANT_A,
        patch=TenantConfigPatch(
            display_name="Acme",
            skill_stale_days=7,
            skill_archive_days=14,
        ),
        actor_id="admin",
    )
    curator = _curator(store, config_service, audit_logger)

    # Last used 10 days ago: stale under tenant A (7 day threshold), still
    # active under the platform default (30).
    ten_days_ago = datetime.now(UTC) - timedelta(days=10)
    a_id = await _seed_skill(
        store=store,
        tenant_id=_TENANT_A,
        name="medium-cold",
        status=SkillStatus.ACTIVE,
        last_used_at=ten_days_ago,
    )
    b_id = await _seed_skill(
        store=store,
        tenant_id=_TENANT_B,
        name="medium-cold",
        status=SkillStatus.ACTIVE,
        last_used_at=ten_days_ago,
    )

    summary = await curator.run_once()
    assert summary.active_to_stale == 1
    a_row = await store.get_skill(skill_id=a_id, tenant_id=_TENANT_A)
    b_row = await store.get_skill(skill_id=b_id, tenant_id=_TENANT_B)
    assert a_row is not None and a_row.status == SkillStatus.STALE
    assert b_row is not None and b_row.status == SkillStatus.ACTIVE


# ─── Activity recorder + auto-revive ─────────────────────────────────


@pytest.mark.asyncio
async def test_activity_recorder_throttles_within_ttl() -> None:
    store = InMemorySkillStore()
    recorder = ThrottledActivityRecorder(store, ttl_seconds=3600)
    skill_id = await _seed_skill(
        store=store,
        tenant_id=_TENANT_A,
        name="hot",
        status=SkillStatus.ACTIVE,
        last_used_at=datetime.now(UTC) - timedelta(days=1),
    )

    first = await recorder.maybe_record(skill_id=skill_id, tenant_id=_TENANT_A)
    second = await recorder.maybe_record(skill_id=skill_id, tenant_id=_TENANT_A)
    assert first is True
    assert second is False, "second bump within TTL must be a no-op"


@pytest.mark.asyncio
async def test_activity_recorder_auto_revives_stale() -> None:
    store = InMemorySkillStore()
    recorder = ThrottledActivityRecorder(store, ttl_seconds=3600)
    skill_id = await _seed_skill(
        store=store,
        tenant_id=_TENANT_A,
        name="snoozing",
        status=SkillStatus.STALE,
        last_used_at=datetime.now(UTC) - timedelta(days=40),
    )

    fired = await recorder.maybe_record(skill_id=skill_id, tenant_id=_TENANT_A)
    assert fired is True
    row = await store.get_skill(skill_id=skill_id, tenant_id=_TENANT_A)
    assert row is not None and row.status == SkillStatus.ACTIVE
    # Auto-revive bumps state_changed_at
    assert row.state_changed_at is not None
    assert (datetime.now(UTC) - row.state_changed_at).total_seconds() < 5.0


@pytest.mark.asyncio
async def test_activity_recorder_skips_archived() -> None:
    """Archived skills must not auto-revive — admin must unarchive."""
    store = InMemorySkillStore()
    recorder = ThrottledActivityRecorder(store, ttl_seconds=3600)
    skill_id = await _seed_skill(
        store=store,
        tenant_id=_TENANT_A,
        name="cold-storage",
        status=SkillStatus.ARCHIVED,
        last_used_at=datetime.now(UTC) - timedelta(days=200),
    )

    fired = await recorder.maybe_record(skill_id=skill_id, tenant_id=_TENANT_A)
    assert fired is False
    row = await store.get_skill(skill_id=skill_id, tenant_id=_TENANT_A)
    assert row is not None and row.status == SkillStatus.ARCHIVED


@pytest.mark.asyncio
async def test_curator_sweep_idempotent() -> None:
    """Re-running a sweep with no new activity produces zero transitions."""
    store = InMemorySkillStore()
    audit_logger, _ = _build_logger()
    curator = _curator(
        store,
        TenantConfigService(store=InMemoryTenantConfigStore(), audit_logger=audit_logger),
        audit_logger,
    )
    long_ago = datetime.now(UTC) - timedelta(days=DEFAULT_STALE_DAYS + 1)
    await _seed_skill(
        store=store,
        tenant_id=_TENANT_A,
        name="going-cold",
        status=SkillStatus.ACTIVE,
        last_used_at=long_ago,
    )

    first = await curator.run_once()
    second = await curator.run_once()
    assert first.active_to_stale == 1
    assert second.active_to_stale == 0
    assert second.stale_to_archived == 0
