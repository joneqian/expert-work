"""Tests for :mod:`expert_work.protocol.skill` — J.7a Skill 静态启用."""

from __future__ import annotations

import pytest

from expert_work.protocol import (
    SKILL_REF_PATTERN,
    SkillStatus,
    parse_skill_ref,
)


def test_skill_status_enum_values() -> None:
    assert SkillStatus.DRAFT.value == "draft"
    assert SkillStatus.ACTIVE.value == "active"
    assert SkillStatus.ARCHIVED.value == "archived"


@pytest.mark.parametrize(
    ("raw", "expected_name", "expected_version"),
    [
        ("foo", "foo", None),
        ("a", "a", None),
        ("knowledge-search", "knowledge-search", None),
        ("rag_v2", "rag_v2", None),
        ("foo@1", "foo", 1),
        ("foo@42", "foo", 42),
        ("bar-skill@10", "bar-skill", 10),
    ],
)
def test_parse_skill_ref_accepts_valid(
    raw: str, expected_name: str, expected_version: int | None
) -> None:
    ref = parse_skill_ref(raw)
    assert ref.name == expected_name
    assert ref.version == expected_version


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "Foo",  # uppercase
        "1foo",  # starts with digit
        "foo@0",  # version must be positive
        "foo@",  # missing version
        "foo@01",  # leading zero
        "foo@-1",  # negative
        "foo bar",  # space
        "foo/bar",  # slash
        "@2",  # missing name
        "x" * 65,  # 65 chars > 64
        "foo@1@2",  # double @
    ],
)
def test_parse_skill_ref_rejects_invalid(raw: str) -> None:
    with pytest.raises(ValueError, match="invalid"):
        parse_skill_ref(raw)


def test_skill_ref_pattern_constant_exposed() -> None:
    """The regex is the authoritative source for AgentSpec.skills validation."""
    import re

    assert re.fullmatch(SKILL_REF_PATTERN, "foo")
    assert re.fullmatch(SKILL_REF_PATTERN, "foo@3")
    assert not re.fullmatch(SKILL_REF_PATTERN, "foo@0")


def test_skill_ref_frozen_dto() -> None:
    """Pydantic ConfigDict(frozen=True) — sanity check immutability."""
    from pydantic import ValidationError

    ref = parse_skill_ref("foo@2")
    with pytest.raises(ValidationError):
        ref.name = "bar"


def test_agent_spec_skills_field_validator_accepts_mixed() -> None:
    """``AgentSpecBody.skills`` accepts a mix of pinned and unpinned refs."""
    from expert_work.protocol import AgentSpec

    spec = AgentSpec.model_validate(_spec_dict(skills=["foo", "bar@3", "baz"]))
    assert spec.spec.skills == ["foo", "bar@3", "baz"]


def test_agent_spec_skills_rejects_invalid_ref() -> None:
    from pydantic import ValidationError

    from expert_work.protocol import AgentSpec

    with pytest.raises(ValidationError):
        AgentSpec.model_validate(_spec_dict(skills=["Foo"]))  # uppercase


def test_agent_spec_skills_rejects_duplicate_names() -> None:
    """Same skill name twice — even with different pins — is a manifest defect."""
    from pydantic import ValidationError

    from expert_work.protocol import AgentSpec

    with pytest.raises(ValidationError):
        AgentSpec.model_validate(_spec_dict(skills=["foo", "foo@3"]))


def _spec_dict(**spec_overrides: object) -> dict[str, object]:
    """A minimal valid AgentSpec, with overrides folded into ``spec``."""
    body: dict[str, object] = {
        "tenant_config": {},
        "model": {
            "provider": "anthropic",
            "name": "claude-sonnet-4-6",
            "api_key_ref": "secret://test",
        },
        "system_prompt": {"template": "you are an agent"},
        "sandbox": {
            "resources": {"cpu": "1.0", "memory": "1Gi"},
            "network": {"egress": "proxy", "allowlist": ["api.anthropic.com"]},
            "filesystem": {"readonly_root": True, "writable": ["/workspace"]},
        },
    }
    body.update(spec_overrides)
    return {
        "apiVersion": "expert_work.io/v1",
        "kind": "Agent",
        "metadata": {"name": "test", "version": "1.0.0", "tenant": "test-tenant"},
        "spec": body,
    }
