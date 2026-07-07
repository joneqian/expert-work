"""Unit tests for classify_skill_runtime — skill-runtime §5.2."""

from __future__ import annotations

from control_plane.api._skill_runtime import classify_skill_runtime
from control_plane.api._skill_zip import SkillZipPayload
from expert_work.protocol.skill import SkillSupportingFile


def _payload(*, body: str = "do the thing", files: list[str] | None = None) -> SkillZipPayload:
    supporting = {
        path: SkillSupportingFile(content="", size=0, mime="text/plain") for path in (files or [])
    }
    return SkillZipPayload(
        name="x",
        description="x",
        category=None,
        required_models=(),
        prompt_fragment=body,
        tool_names=(),
        supporting_files=supporting,
    )


def test_knowledge_only_is_runnable() -> None:
    rt = classify_skill_runtime(_payload(files=[]))
    assert rt.kind == "knowledge"
    assert rt.runnable is True


def test_python_scripts_runnable() -> None:
    rt = classify_skill_runtime(_payload(files=["scripts/run.py", "reference/notes.md"]))
    assert rt.kind == "python"
    assert rt.runnable is True


def test_node_files_not_runnable() -> None:
    rt = classify_skill_runtime(_payload(files=["package.json", "index.js"]))
    assert rt.kind == "node"
    assert rt.runnable is False


def test_node_body_marker_not_runnable() -> None:
    rt = classify_skill_runtime(_payload(body="Run `npx skills add ...` then continue."))
    assert rt.kind == "node"
    assert rt.runnable is False


def test_python_wins_over_node_mention_in_body() -> None:
    # Regression: Anthropic's pptx skill ships .py scripts AND mentions an
    # optional PptxGenJS/npx path in prose. It must classify python (runnable),
    # not node — a live import flagged it node before this fix.
    rt = classify_skill_runtime(
        _payload(
            body="Primary: python-pptx. Alternative: use PptxGenJS via `npx`.",
            files=["scripts/html2pptx.py", "pptxgenjs.md", "scripts/office/unpack.py"],
        )
    )
    assert rt.kind == "python"
    assert rt.runnable is True


def test_browser_marker_wins_over_node() -> None:
    rt = classify_skill_runtime(
        _payload(body="Uses Playwright to drive Chromium.", files=["index.js"])
    )
    assert rt.kind == "browser"
    assert rt.runnable is False
    assert "MCP" in rt.hint


def test_unknown_when_only_non_executable_assets() -> None:
    rt = classify_skill_runtime(_payload(files=["assets/logo.png", "data.csv"]))
    assert rt.kind == "unknown"
    assert rt.runnable is True
