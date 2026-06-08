"""SE-7d-3b-ii — the pure ``_bound_distilled_skills`` extraction helper.

Build-time snapshot of which distilled skill versions the run should attribute
its outcome to. Only distilled + tenant-owned versions; deterministically
ordered so the run-end emission is stable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from helix_agent.protocol.skill import EvolutionOrigin, SkillVersion
from orchestrator.agent_factory import _bound_distilled_skills

_TENANT = UUID("55555555-5555-5555-5555-555555555555")


def _version(
    *,
    skill_id: UUID,
    version: int = 1,
    origin: EvolutionOrigin | None,
    tenant_id: UUID | None = _TENANT,
) -> SkillVersion:
    return SkillVersion(
        id=uuid4(),
        skill_id=skill_id,
        tenant_id=tenant_id,
        version=version,
        prompt_fragment="do x",
        evolution_origin=origin,
        created_at=datetime.now(UTC),
    )


def test_extracts_only_distilled_tenant_versions() -> None:
    sid_d, sid_h, sid_s, sid_p = uuid4(), uuid4(), uuid4(), uuid4()
    resolved = {
        "distilled": _version(skill_id=sid_d, origin="distilled"),
        "human": _version(skill_id=sid_h, origin=None),
        "in_session": _version(skill_id=sid_s, origin="in_session"),
        "platform": _version(skill_id=sid_p, origin="distilled", tenant_id=None),
    }

    bound = _bound_distilled_skills(resolved, agent_name="assistant")

    assert len(bound) == 1
    assert bound[0].skill_id == sid_d
    assert bound[0].skill_version == 1
    assert bound[0].agent_name == "assistant"


def test_deterministic_order() -> None:
    ids = sorted((uuid4(), uuid4(), uuid4()), key=str)
    resolved = {
        f"s{i}": _version(skill_id=sid, origin="distilled") for i, sid in enumerate(reversed(ids))
    }

    bound = _bound_distilled_skills(resolved, agent_name="a")

    assert [b.skill_id for b in bound] == ids  # sorted by str(skill_id)


def test_empty_when_no_distilled() -> None:
    resolved = {"h": _version(skill_id=uuid4(), origin=None)}
    assert _bound_distilled_skills(resolved, agent_name="a") == ()
