"""Unit tests for the GitHub skill import resolver + scan-and-match (方案 A)."""

from __future__ import annotations

import io
import zipfile

import pytest

from control_plane.api._skill_github import (
    GithubImportError,
    resolve_github_source,
    select_skill_zip,
)
from control_plane.api._skill_zip import build_skill_zip, parse_skill_zip


def _skill_md(name: str, body: str = "do the thing") -> str:
    """A canonical SKILL.md (derived from build_skill_zip so it always parses)."""
    blob = build_skill_zip(
        name=name,
        description=f"{name} skill",
        category=None,
        required_models=(),
        prompt_fragment=body,
        tool_names=(),
    )
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        return z.read("SKILL.md").decode()


def _github_archive(repo: str, ref: str, files: dict[str, str]) -> bytes:
    """Build a GitHub-style archive: everything nested under ``<repo>-<ref>/``."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as z:
        for path, content in files.items():
            z.writestr(f"{repo}-{ref}/{path}", content)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# resolve_github_source
# ---------------------------------------------------------------------------


def test_resolve_owner_repo_shorthand() -> None:
    src = resolve_github_source("vercel-labs/skills")
    assert (src.owner, src.repo, src.ref, src.skill) == (
        "vercel-labs",
        "skills",
        "HEAD",
        None,
    )


def test_resolve_with_skill_and_ref() -> None:
    src = resolve_github_source("vercel-labs/skills", skill="find-skills", ref="v1.2")
    assert src.skill == "find-skills"
    assert src.ref == "v1.2"


def test_resolve_github_url() -> None:
    src = resolve_github_source("https://github.com/vercel-labs/skills")
    assert (src.owner, src.repo) == ("vercel-labs", "skills")


def test_resolve_github_tree_url_takes_ref() -> None:
    src = resolve_github_source(
        "https://github.com/vercel-labs/skills/tree/main/skills/find-skills"
    )
    assert src.ref == "main"


def test_resolve_github_url_strips_dot_git() -> None:
    src = resolve_github_source("https://github.com/vercel-labs/skills.git")
    assert src.repo == "skills"


def test_resolve_skills_sh_url_supplies_skill() -> None:
    src = resolve_github_source("https://www.skills.sh/vercel-labs/skills/find-skills")
    assert (src.owner, src.repo, src.skill) == ("vercel-labs", "skills", "find-skills")


def test_resolve_rejects_non_github_host() -> None:
    with pytest.raises(GithubImportError):
        resolve_github_source("https://evil.example.com/owner/repo")


def test_resolve_rejects_bad_shorthand() -> None:
    with pytest.raises(GithubImportError):
        resolve_github_source("not-a-repo")


def test_resolve_rejects_injection_chars() -> None:
    with pytest.raises(GithubImportError):
        resolve_github_source("../../etc/passwd")
    with pytest.raises(GithubImportError):
        resolve_github_source("owner/repo", ref="main;rm -rf")


def test_resolve_accepts_path_form_skill() -> None:
    # The picker / #728 disambiguation submit a relpath like "skills/<name>".
    src = resolve_github_source("owner/repo", skill="skills/self-improving-agent")
    assert src.skill == "skills/self-improving-agent"


def test_resolve_rejects_skill_path_traversal() -> None:
    for bad in ("../evil", "skills/../../etc", "/abs/path", "skills/"):
        with pytest.raises(GithubImportError):
            resolve_github_source("owner/repo", skill=bad)


# ---------------------------------------------------------------------------
# select_skill_zip
# ---------------------------------------------------------------------------


def test_select_named_skill_repacks_to_root() -> None:
    archive = _github_archive(
        "skills",
        "main",
        {
            "README.md": "top",
            "skills/find-skills/SKILL.md": _skill_md("find-skills"),
            "skills/find-skills/scripts/run.py": "print('hi')",
            "skills/other/SKILL.md": _skill_md("other"),
        },
    )
    blob = select_skill_zip(archive, skill="find-skills")
    # Repacked zip is root-anchored and parses as a canonical skill.
    payload = parse_skill_zip(blob)
    assert payload.name == "find-skills"
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names = set(z.namelist())
    assert "SKILL.md" in names
    assert "scripts/run.py" in names
    assert not any(n.startswith("skills/") for n in names)  # prefix stripped


def test_select_single_skill_without_selector() -> None:
    archive = _github_archive("repo", "main", {"my-skill/SKILL.md": _skill_md("my-skill")})
    payload = parse_skill_zip(select_skill_zip(archive, skill=None))
    assert payload.name == "my-skill"


def test_select_multi_skill_without_selector_errors_with_candidates() -> None:
    archive = _github_archive(
        "repo",
        "main",
        {
            "skills/a/SKILL.md": _skill_md("a"),
            "skills/b/SKILL.md": _skill_md("b"),
        },
    )
    with pytest.raises(GithubImportError) as ei:
        select_skill_zip(archive, skill=None)
    assert ei.value.status == 400
    assert ei.value.candidates == ["skills/a", "skills/b"]


def test_select_missing_skill_404() -> None:
    archive = _github_archive("repo", "main", {"skills/a/SKILL.md": _skill_md("a")})
    with pytest.raises(GithubImportError) as ei:
        select_skill_zip(archive, skill="nope")
    assert ei.value.status == 404


def test_select_no_skill_md_404() -> None:
    archive = _github_archive("repo", "main", {"README.md": "nothing here"})
    with pytest.raises(GithubImportError) as ei:
        select_skill_zip(archive, skill=None)
    assert ei.value.status == 404


def test_select_by_full_path() -> None:
    archive = _github_archive(
        "repo",
        "main",
        {
            "skills/a/SKILL.md": _skill_md("a"),
            "skills/b/SKILL.md": _skill_md("b"),
        },
    )
    payload = parse_skill_zip(select_skill_zip(archive, skill="skills/b"))
    assert payload.name == "b"


def test_select_duplicate_basename_disambiguated_by_path() -> None:
    # Two folders share basename "context-surfing" — the old global-uniqueness
    # index raised at scan time; now basename is ambiguous but the full path
    # resolves it.
    archive = _github_archive(
        "repo",
        "main",
        {
            "skills/context-surfing/SKILL.md": _skill_md("context-surfing"),
            "examples/context-surfing/SKILL.md": _skill_md("context-surfing"),
        },
    )
    # Bare basename → 400 listing both paths.
    with pytest.raises(GithubImportError) as ei:
        select_skill_zip(archive, skill="context-surfing")
    assert ei.value.status == 400
    assert ei.value.candidates == ["examples/context-surfing", "skills/context-surfing"]
    # Full path disambiguates.
    payload = parse_skill_zip(select_skill_zip(archive, skill="skills/context-surfing"))
    assert payload.name == "context-surfing"


def test_select_rejects_bad_zip() -> None:
    with pytest.raises(GithubImportError):
        select_skill_zip(b"not a zip", skill=None)
