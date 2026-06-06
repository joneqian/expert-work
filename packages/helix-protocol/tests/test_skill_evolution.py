"""Stream SE (SE-1) — self-evolving skill protocol DTO fields.

Covers the additive ownership / lineage columns on ``Skill``, the
evolution-provenance columns on ``SkillVersion``, and the new
``SkillEvalResult`` (replay-verification evidence) DTO. All additions are
backward-compatible: existing constructions keep working via defaults.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from helix_agent.protocol import Skill, SkillEvalResult, SkillVersion


def _skill(**over: object) -> Skill:
    base: dict[str, object] = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "name": "weekly-report",
        "status": "active",
        "latest_version": 1,
        "created_at": datetime.now(tz=UTC),
        "updated_at": datetime.now(tz=UTC),
    }
    base.update(over)
    return Skill(**base)  # type: ignore[arg-type]


def _version(**over: object) -> SkillVersion:
    base: dict[str, object] = {
        "id": uuid4(),
        "skill_id": uuid4(),
        "tenant_id": uuid4(),
        "version": 1,
        "prompt_fragment": "do the thing",
        "created_at": datetime.now(tz=UTC),
    }
    base.update(over)
    return SkillVersion(**base)  # type: ignore[arg-type]


def _eval_result(**over: object) -> SkillEvalResult:
    base: dict[str, object] = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "skill_id": uuid4(),
        "skill_version": 1,
        "baseline_score": 0.4,
        "skill_score": 0.8,
        "delta": 0.4,
        "n_cases": 10,
        "replay_source": "trajectory",
        "verdict": "pass",
        "created_at": datetime.now(tz=UTC),
    }
    base.update(over)
    return SkillEvalResult(**base)  # type: ignore[arg-type]


# ── Skill ownership / lineage (SE-A1) ─────────────────────────────────────


def test_skill_visibility_defaults_tenant() -> None:
    """Existing rows (no visibility) default to 'tenant' — additive, no behavior change."""
    s = _skill()
    assert s.visibility == "tenant"
    assert s.created_by_agent_id is None
    assert s.forked_from is None


def test_skill_visibility_agent_private() -> None:
    agent_id = uuid4()
    src = uuid4()
    s = _skill(visibility="agent_private", created_by_agent_id=agent_id, forked_from=src)
    assert s.visibility == "agent_private"
    assert s.created_by_agent_id == agent_id
    assert s.forked_from == src


def test_skill_visibility_rejects_unknown() -> None:
    with pytest.raises(ValidationError):
        _skill(visibility="public")


# ── SkillVersion evolution provenance (SE-A1) ─────────────────────────────


def test_version_evolution_defaults() -> None:
    v = _version()
    assert v.evolution_origin is None
    assert v.distilled_from_trajectory_key is None
    assert v.distilled_from_candidate_id is None
    assert v.evolution_round == 0


def test_version_distilled_provenance() -> None:
    cand = uuid4()
    v = _version(
        evolution_origin="distilled",
        distilled_from_trajectory_key="t/abc/success/2026/06/06/x.jsonl",
        distilled_from_candidate_id=cand,
        evolution_round=3,
    )
    assert v.evolution_origin == "distilled"
    assert v.distilled_from_trajectory_key.endswith("x.jsonl")
    assert v.distilled_from_candidate_id == cand
    assert v.evolution_round == 3


def test_version_in_session_origin() -> None:
    assert _version(evolution_origin="in_session").evolution_origin == "in_session"


def test_version_rejects_unknown_origin() -> None:
    with pytest.raises(ValidationError):
        _version(evolution_origin="hallucinated")


def test_version_round_non_negative() -> None:
    with pytest.raises(ValidationError):
        _version(evolution_round=-1)


# ── SkillEvalResult (SE-A2) ───────────────────────────────────────────────


def test_eval_result_roundtrip() -> None:
    r = _eval_result()
    assert r.delta == pytest.approx(0.4)
    assert r.verdict == "pass"
    assert r.replay_source == "trajectory"
    assert r.high_risk is False
    assert r.evolution_round == 0


def test_eval_result_platform_null_tenant() -> None:
    """Platform-skill eval results carry tenant_id=None (0057 NULL-tenant)."""
    assert _eval_result(tenant_id=None).tenant_id is None


def test_eval_result_rejects_unknown_verdict() -> None:
    with pytest.raises(ValidationError):
        _eval_result(verdict="maybe")


def test_eval_result_rejects_unknown_replay_source() -> None:
    with pytest.raises(ValidationError):
        _eval_result(replay_source="synthetic")


def test_eval_result_version_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        _eval_result(skill_version=0)


def test_eval_result_n_cases_non_negative() -> None:
    with pytest.raises(ValidationError):
        _eval_result(n_cases=-1)


def test_eval_result_frozen() -> None:
    r = _eval_result()
    with pytest.raises(ValidationError):
        r.verdict = "fail"  # type: ignore[misc]
