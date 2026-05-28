"""Capability Uplift Sprint #3 — SKILL.md parser / serializer +
high_risk + content_hash helpers.

See ``docs/streams/STREAM-UPLIFT-DESIGN.md`` § 4.3.1 (frontmatter
schema) and § 4.3.12 (high_risk semantics).
"""

from __future__ import annotations

import pytest

from helix_agent.protocol.skill import (
    HIGH_RISK_TOOLS,
    SkillPackageLayoutError,
    canonicalize_skill_content,
    compute_content_hash,
    is_high_risk_skill_version,
)
from helix_agent.protocol.skill_package import (
    parse_skill_md,
    serialize_skill_md,
)

# ─── SKILL.md parser ─────────────────────────────────────────────────────


def test_parse_minimal_frontmatter() -> None:
    """Standard frontmatter only (no helix:) — must still parse with
    sensible defaults for the optional helix fields. But helix.version
    is required, so this should reject."""
    text = """---
name: my-skill
description: A test skill
---

# Body
hello
"""
    # Required helix.version missing → reject
    with pytest.raises(SkillPackageLayoutError, match=r"helix.version"):
        parse_skill_md(text)


def test_parse_with_helix_namespace() -> None:
    text = """---
name: api-debug
description: HTTP/gRPC API 调试
license: Apache-2.0
helix:
  version: 2
  category: ops
  required_models: [anthropic/claude-sonnet-4]
  tool_names: [http, exec_python]
  authored_by: human
  lazy: false
---

# API Debug Assistant
You are an API debug helper...
"""
    parsed = parse_skill_md(text)
    assert parsed.name == "api-debug"
    assert parsed.description == "HTTP/gRPC API 调试"
    assert parsed.license == "Apache-2.0"
    assert parsed.helix_version == 2
    assert parsed.helix_category == "ops"
    assert parsed.helix_required_models == ("anthropic/claude-sonnet-4",)
    assert parsed.helix_tool_names == ("http", "exec_python")
    assert parsed.helix_authored_by == "human"
    assert parsed.helix_lazy is False
    assert parsed.body.startswith("# API Debug Assistant")


def test_parse_missing_opening_delimiter_rejected() -> None:
    text = "name: x\ndescription: y\n"
    with pytest.raises(SkillPackageLayoutError, match="frontmatter"):
        parse_skill_md(text)


def test_parse_missing_closing_delimiter_rejected() -> None:
    text = "---\nname: x\ndescription: y\nbody...\n"
    with pytest.raises(SkillPackageLayoutError, match="closing"):
        parse_skill_md(text)


def test_parse_invalid_yaml_rejected() -> None:
    text = "---\nname: x: y: z\n---\nbody\n"
    with pytest.raises(SkillPackageLayoutError, match=r"valid YAML"):
        parse_skill_md(text)


def test_parse_helix_lazy_must_be_bool() -> None:
    text = """---
name: x
description: y
helix:
  version: 1
  lazy: "yes"
---
body
"""
    with pytest.raises(SkillPackageLayoutError, match="lazy"):
        parse_skill_md(text)


def test_parse_helix_authored_by_only_human_or_agent() -> None:
    text = """---
name: x
description: y
helix:
  version: 1
  authored_by: robot
---
body
"""
    with pytest.raises(SkillPackageLayoutError, match="authored_by"):
        parse_skill_md(text)


# ─── Serializer / round-trip ─────────────────────────────────────────────


def test_serialize_round_trip_preserves_fields() -> None:
    """Parse → serialize → re-parse should yield an equal ParsedSkillMd."""
    original = """---
name: api-debug
description: HTTP/gRPC 调试
license: Apache-2.0
helix:
  version: 3
  category: ops
  required_models: [anthropic/claude-sonnet-4]
  tool_names: [http]
  authored_by: agent
  lazy: true
---

# Body
content
"""
    parsed = parse_skill_md(original)
    serialized = serialize_skill_md(parsed)
    re_parsed = parse_skill_md(serialized)
    assert re_parsed == parsed


def test_serialize_omits_helix_defaults_for_clean_diff() -> None:
    """authored_by=human + lazy=false are defaults — should be omitted
    from output to keep diffs clean when these aren't customized."""
    parsed = parse_skill_md(
        """---
name: x
description: y
helix:
  version: 1
---
body
"""
    )
    out = serialize_skill_md(parsed)
    assert "authored_by" not in out
    assert "lazy" not in out


# ─── is_high_risk_skill_version (Mini-ADR U-24) ──────────────────────────


def test_high_risk_flagged_by_exec_python() -> None:
    assert is_high_risk_skill_version(
        tool_names=["exec_python", "log_viewer"], supporting_file_paths=[]
    )


def test_high_risk_flagged_by_http() -> None:
    assert is_high_risk_skill_version(tool_names=["http"], supporting_file_paths=[])


def test_high_risk_flagged_by_exec_shell() -> None:
    assert is_high_risk_skill_version(tool_names=["exec_shell"], supporting_file_paths=[])


def test_high_risk_flagged_by_scripts_subdir() -> None:
    assert is_high_risk_skill_version(
        tool_names=["log_viewer"],
        supporting_file_paths=["reference/notes.md", "scripts/diagnose.py"],
    )


def test_low_risk_when_neither_tools_nor_scripts() -> None:
    assert not is_high_risk_skill_version(
        tool_names=["log_viewer", "calculator"],
        supporting_file_paths=["reference/cheatsheet.md", "templates/email.txt"],
    )


def test_low_risk_when_no_tools_and_no_files() -> None:
    assert not is_high_risk_skill_version(tool_names=[], supporting_file_paths=[])


def test_high_risk_tools_constant_matches_design() -> None:
    """Mini-ADR U-24 lists exactly these three."""
    assert HIGH_RISK_TOOLS == frozenset({"exec_python", "exec_shell", "http"})


# ─── content_hash (Mini-ADR U-21) ────────────────────────────────────────


def test_hash_is_deterministic() -> None:
    assert compute_content_hash("hello", {"a": 1, "b": 2}) == compute_content_hash(
        "hello", {"a": 1, "b": 2}
    )


def test_hash_independent_of_supporting_files_dict_order() -> None:
    """JSONB round-trip can shuffle dict ordering — hash must not change."""
    h1 = compute_content_hash("body", {"reference/foo.md": "x", "scripts/bar.py": "y"})
    h2 = compute_content_hash("body", {"scripts/bar.py": "y", "reference/foo.md": "x"})
    assert h1 == h2


def test_hash_detects_prompt_change() -> None:
    h1 = compute_content_hash("hello", {})
    h2 = compute_content_hash("hello!", {})
    assert h1 != h2


def test_hash_detects_supporting_file_change() -> None:
    h1 = compute_content_hash("body", {"foo": "v1"})
    h2 = compute_content_hash("body", {"foo": "v2"})
    assert h1 != h2


def test_hash_distinguishes_empty_supporting_files_from_none() -> None:
    """`compute_content_hash(text, None)` and `(text, {})` should be the
    same — both seed with empty JSONB. Matches migration backfill."""
    assert compute_content_hash("text", None) == compute_content_hash("text", {})


def test_canonical_uses_null_separator() -> None:
    """The null byte between prompt_fragment and supporting_files makes
    boundary attacks (e.g., prompt ending in '{}' that looks like the
    supporting_files JSON) detectable."""
    raw = canonicalize_skill_content("hello", {})
    assert b"\x00" in raw
    assert raw.startswith(b"hello\x00")
