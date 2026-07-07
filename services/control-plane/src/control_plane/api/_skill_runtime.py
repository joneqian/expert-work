"""Classify an imported skill's runtime needs (skill-runtime §5.2).

A non-blocking signal attached to the platform import response so an operator
learns *at import time* how a skill will run in expert_work's sandbox — instead
of discovering it fails at runtime.

The sandbox image bakes Python AND Node.js + npm (see
``infra/sandbox-image/Dockerfile``; the smoke test asserts ``node -e`` runs),
and runtime installs (pip / npm) flow through the audited per-agent egress
proxy (sandbox-egress §3.5). So **knowledge**, **Python** and **Node** skills
all run here; only **browser** skills don't (no browser binary in the image —
they belong to a browser MCP server, skill-runtime §4). This is advisory only:
the skill imports either way, the UI just sets expectations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from control_plane.api._skill_zip import SkillZipPayload

# Browser-automation markers — these need a real browser + network, which the
# sandbox forbids; they belong in a browser MCP server.
_BROWSER_RE = re.compile(r"\b(playwright|puppeteer|chromium|headless\s+browser)\b", re.IGNORECASE)
# Node-runtime markers in the SKILL.md body.
_NODE_BODY_RE = re.compile(r"\b(npx|npm\s+(install|run|i)\b|node\s+\w|pnpm|yarn)\b", re.IGNORECASE)

_NODE_EXTS = frozenset({".js", ".ts", ".mjs", ".cjs", ".jsx", ".tsx"})
_NODE_MANIFESTS = frozenset({"package.json", "pnpm-lock.yaml", "package-lock.json", "yarn.lock"})


@dataclass(frozen=True)
class SkillRuntime:
    """Advisory runtime classification of an imported skill."""

    kind: str  # "knowledge" | "python" | "node" | "browser" | "unknown"
    runnable: bool  # False → bundled scripts won't run in expert_work's sandbox
    hint: str

    def as_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "runnable": self.runnable, "hint": self.hint}


def _basenames(payload: SkillZipPayload) -> list[str]:
    return [path.rsplit("/", 1)[-1] for path in payload.supporting_files]


def classify_skill_runtime(payload: SkillZipPayload) -> SkillRuntime:
    """Best-effort runtime classification from the SKILL.md body + file set."""
    body = payload.prompt_fragment
    names = _basenames(payload)
    exts = {f".{n.rsplit('.', 1)[-1].lower()}" for n in names if "." in n}

    # Browser first — strongest "won't run here" signal.
    if _BROWSER_RE.search(body) or any("playwright" in n.lower() for n in names):
        return SkillRuntime(
            kind="browser",
            runnable=False,
            hint=(
                "This skill drives a browser — the expert_work sandbox has no browser "
                "binary. Use a browser MCP server instead of importing it as a skill."
            ),
        )

    has_py = ".py" in exts
    has_node_files = any(n in _NODE_MANIFESTS for n in names) or bool(exts & _NODE_EXTS)
    node_hint = (
        "Node.js skill — the sandbox bakes Node.js + npm, so bundled scripts run "
        "via the bash tool. Package installs (npm/npx) go through the audited "
        "egress proxy and may be limited by its policy."
    )

    # A real Node project (package.json / .js / .ts) with no Python fallback.
    if has_node_files and not has_py:
        return SkillRuntime(kind="node", runnable=True, hint=node_hint)

    # Python wins over a *mention* of Node: skills like Anthropic's ``pptx``
    # bundle ``.py`` scripts AND describe an optional PptxGenJS/``npx`` path in
    # prose — they run here via the Python scripts. Check ``.py`` before the
    # body-only Node marker so a documented alternative doesn't mask a runnable
    # Python skill (regression: live import flagged pptx as node).
    if has_py:
        return SkillRuntime(
            kind="python",
            runnable=True,
            hint="Python skill — runs in the sandbox (use the office image for doc libs).",
        )

    # Node only in prose (``npx``/``npm``) with no Python scripts — e.g. the
    # ``npx skills`` installer skills. Node itself is baked; whether the
    # registry fetch succeeds depends on the egress policy (same as pip).
    if _NODE_BODY_RE.search(body) is not None:
        return SkillRuntime(kind="node", runnable=True, hint=node_hint)

    if not payload.supporting_files:
        return SkillRuntime(
            kind="knowledge",
            runnable=True,
            hint="Instruction-only skill — runs as guidance, no execution needed.",
        )

    return SkillRuntime(
        kind="unknown",
        runnable=True,
        hint="No runtime-specific markers found; instructions are usable.",
    )
