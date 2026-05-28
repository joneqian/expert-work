"""Phase 4 — ``_skill_zip.py`` v2 unit tests (Sprint #3 § 4.6).

Covers happy paths + structural rejects of the rewritten ZIP parser.
Poison / obfuscation attacks live in sibling files
``test_skill_zip_poison.py`` and ``test_skill_obfuscation_attacks.py``.

Each test stays at the parser boundary (no FastAPI / DB) so a failure
isolates the defect to this layer.
"""

from __future__ import annotations

import base64
import io
import logging
import zipfile
from typing import Final

import pytest
import yaml

from control_plane.api._skill_zip import (
    SkillPackageError,
    SkillZipPayload,
    build_skill_zip,
    parse_skill_zip,
)
from helix_agent.protocol.skill import (
    SkillPackageLayoutError,
    compute_content_hash,
    supporting_files_to_jsonable,
)

_MIN_BODY: Final[str] = "be helpful with X"


def _build_skill_md(
    *,
    name: str = "foo",
    description: str = "imported skill",
    body: str = _MIN_BODY,
    helix_version: int = 1,
    helix_category: str | None = None,
    helix_tool_names: list[str] | None = None,
    helix_required_models: list[str] | None = None,
    helix_authored_by: str | None = None,
    helix_lazy: bool | None = None,
    helix_version_override: str | None = None,
    license_str: str | None = None,
) -> str:
    """Build a minimal-valid SKILL.md text body. ``*_override`` lets callers
    inject a malformed value (e.g. a string where int is expected)."""
    fm: dict[str, object] = {"name": name, "description": description}
    if license_str is not None:
        fm["license"] = license_str
    helix: dict[str, object] = {}
    if helix_version_override is not None:
        helix["version"] = helix_version_override
    else:
        helix["version"] = helix_version
    if helix_category is not None:
        helix["category"] = helix_category
    if helix_tool_names is not None:
        helix["tool_names"] = helix_tool_names
    if helix_required_models is not None:
        helix["required_models"] = helix_required_models
    if helix_authored_by is not None:
        helix["authored_by"] = helix_authored_by
    if helix_lazy is not None:
        helix["lazy"] = helix_lazy
    fm["helix"] = helix
    rendered = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True)
    return f"---\n{rendered}---\n\n{body}"


def _zip_with(files: dict[str, bytes]) -> bytes:
    """Build a ZIP from {path: raw_bytes}. Uses STORED so per-entry sizes
    are exact (deflate compression makes ``zipfile`` round-trips noisy
    on tiny inputs)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_STORED) as archive:
        for path, raw in files.items():
            archive.writestr(path, raw)
    return buf.getvalue()


# ─── New-format happy path ───────────────────────────────────────────────


def test_new_format_round_trip() -> None:
    """build → parse round-trip preserves all fields."""
    blob = build_skill_zip(
        name="foo",
        description="my foo skill",
        category="data",
        required_models=("claude-sonnet-4-6",),
        prompt_fragment="be very helpful",
        tool_names=("web_search",),
        license="Apache-2.0",
        authored_by="human",
        lazy=False,
    )
    payload = parse_skill_zip(blob)
    assert payload.name == "foo"
    assert payload.description == "my foo skill"
    assert payload.category == "data"
    assert payload.required_models == ("claude-sonnet-4-6",)
    assert payload.prompt_fragment == "be very helpful"
    assert payload.tool_names == ("web_search",)
    assert payload.license == "Apache-2.0"
    assert payload.lazy_load is False
    assert payload.layout == "new"


def test_new_format_with_supporting_file() -> None:
    """SKILL.md + supporting file is parsed + supporting_files populated."""
    skill_md = _build_skill_md(name="api-debug", body="follow these steps")
    files = {
        "SKILL.md": skill_md.encode("utf-8"),
        "reference/codes.md": b"# error codes\n\n401 = unauthorized",
    }
    payload = parse_skill_zip(_zip_with(files))
    assert payload.name == "api-debug"
    assert "reference/codes.md" in payload.supporting_files
    sf = payload.supporting_files["reference/codes.md"]
    assert base64.b64decode(sf.content) == files["reference/codes.md"]
    assert sf.size == len(files["reference/codes.md"])
    assert sf.mime == "text/markdown"
    assert payload.layout == "new"


def test_content_hash_matches_standalone() -> None:
    """The hash baked into the payload equals what ``compute_content_hash``
    produces from the same inputs — caller can trust the field."""
    skill_md = _build_skill_md(body="hello world")
    files = {
        "SKILL.md": skill_md.encode("utf-8"),
        "reference/a.md": b"alpha",
    }
    payload = parse_skill_zip(_zip_with(files))
    expected = compute_content_hash(
        "hello world",
        supporting_files_to_jsonable(payload.supporting_files),
    )
    assert payload.content_hash == expected
    assert len(payload.content_hash) == 32  # blake2b digest_size=32


def test_high_risk_set_by_tool_names() -> None:
    """tool_names ∩ HIGH_RISK_TOOLS triggers ``high_risk = True``."""
    skill_md = _build_skill_md(helix_tool_names=["exec_python"])
    payload = parse_skill_zip(_zip_with({"SKILL.md": skill_md.encode("utf-8")}))
    assert payload.high_risk is True


def test_high_risk_set_by_scripts_path() -> None:
    """A supporting file under ``scripts/`` flips ``high_risk = True``."""
    skill_md = _build_skill_md(helix_tool_names=["web_search"])
    files = {
        "SKILL.md": skill_md.encode("utf-8"),
        "scripts/diagnose.py": b"print('hello')\n",
    }
    payload = parse_skill_zip(_zip_with(files))
    assert payload.high_risk is True


def test_low_risk_when_no_dangerous_inputs() -> None:
    """Sanity: a vanilla skill is low-risk."""
    skill_md = _build_skill_md(helix_tool_names=["web_search"])
    payload = parse_skill_zip(_zip_with({"SKILL.md": skill_md.encode("utf-8")}))
    assert payload.high_risk is False


# ─── Legacy backward compat ──────────────────────────────────────────────


def _legacy_zip(
    *,
    name: str = "foo",
    prompt: str = _MIN_BODY,
    tool_names: tuple[str, ...] = ("web_search",),
) -> bytes:
    return _zip_with(
        {
            "skill.yaml": yaml.safe_dump({"name": name, "description": "legacy"}).encode("utf-8"),
            "prompt.md": prompt.encode("utf-8"),
            "tools.txt": ("\n".join(tool_names)).encode("utf-8"),
        }
    )


def test_legacy_format_parses_and_warns(caplog: pytest.LogCaptureFixture) -> None:
    """Old skill.yaml + prompt.md + tools.txt layout still imports + logs a
    deprecation warning (Mini-ADR U-19)."""
    with caplog.at_level(logging.WARNING, logger="helix.control_plane.skill_zip"):
        payload = parse_skill_zip(_legacy_zip())
    assert payload.name == "foo"
    assert payload.prompt_fragment == _MIN_BODY
    assert payload.tool_names == ("web_search",)
    assert payload.layout == "legacy"
    assert any("legacy" in r.getMessage().lower() for r in caplog.records)


def test_legacy_format_rejects_extra_entry() -> None:
    """Legacy layout doesn't support supporting files; an extra entry
    rejects the whole ZIP. The user-facing error stays generic."""
    files = {
        "skill.yaml": yaml.safe_dump({"name": "foo"}).encode("utf-8"),
        "prompt.md": b"hello",
        "tools.txt": b"web_search",
        "extra.md": b"nope",
    }
    with pytest.raises(SkillPackageLayoutError):
        parse_skill_zip(_zip_with(files))


# ─── Structural rejects ─────────────────────────────────────────────────


def test_missing_skill_md_rejects() -> None:
    """No SKILL.md and not a legacy 3-file layout → reject."""
    files = {"reference/codes.md": b"not enough"}
    with pytest.raises(SkillPackageLayoutError) as exc_info:
        parse_skill_zip(_zip_with(files))
    # Caller-facing message is generic (Oracle defense).
    assert "invalid skill package" in str(exc_info.value)
    assert isinstance(exc_info.value, SkillPackageError)
    assert exc_info.value.reason == "missing_skill_md"


def test_empty_zip_rejects() -> None:
    """A ZIP with zero entries is treated as missing_skill_md."""
    with pytest.raises(SkillPackageLayoutError):
        parse_skill_zip(_zip_with({}))


def test_not_a_zip_rejects() -> None:
    """Random bytes are not a ZIP — fail open-archive with a generic msg."""
    with pytest.raises(SkillPackageLayoutError) as exc_info:
        parse_skill_zip(b"not a zip file at all")
    assert isinstance(exc_info.value, SkillPackageError)
    assert exc_info.value.reason == "bad_zip"


def test_frontmatter_wrong_type_rejects() -> None:
    """A frontmatter where ``helix.version`` is a string fails layout parse."""
    skill_md = _build_skill_md(helix_version_override="two")
    with pytest.raises(SkillPackageLayoutError) as exc_info:
        parse_skill_zip(_zip_with({"SKILL.md": skill_md.encode("utf-8")}))
    assert isinstance(exc_info.value, SkillPackageError)
    assert exc_info.value.reason == "invalid_frontmatter"


def test_frontmatter_missing_name_rejects() -> None:
    """Frontmatter without ``name`` is structurally invalid."""
    skill_md = "---\ndescription: hi\nhelix:\n  version: 1\n---\n\nhello"
    with pytest.raises(SkillPackageLayoutError) as exc_info:
        parse_skill_zip(_zip_with({"SKILL.md": skill_md.encode("utf-8")}))
    assert exc_info.value.reason == "invalid_frontmatter"  # type: ignore[attr-defined]


# ─── Payload typing sanity ───────────────────────────────────────────────


def test_parse_returns_skillzippayload() -> None:
    """Return type for static + dynamic callers (test_skills_api hits)."""
    blob = build_skill_zip(
        name="foo",
        description="x",
        category=None,
        required_models=(),
        prompt_fragment="x",
        tool_names=(),
    )
    payload = parse_skill_zip(blob)
    assert isinstance(payload, SkillZipPayload)
