"""Classify an imported skill's runtime needs (skill-runtime §5.2).

A non-blocking signal attached to the platform import response so an operator
learns *at import time* whether a skill can actually run in helix's sandbox
(Python-only, ``network=none``) — instead of discovering it fails at runtime.

helix runs **knowledge** + **Python compute** skills; **Node / browser /
network** skills belong to an MCP server (skill-runtime §4). This is advisory
only: the skill still imports (its instructions are readable even if bundled
scripts won't run), the UI just sets expectations.
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
    runnable: bool  # False → bundled scripts won't run in helix's sandbox
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
                "This skill drives a browser — helix sandboxes are Python-only with "
                "no network. Use a browser MCP server instead of importing it as a skill."
            ),
        )

    has_node = (
        any(n in _NODE_MANIFESTS for n in names)
        or bool(exts & _NODE_EXTS)
        or _NODE_BODY_RE.search(body) is not None
    )
    if has_node:
        return SkillRuntime(
            kind="node",
            runnable=False,
            hint=(
                "This skill needs a Node.js runtime, which the helix sandbox doesn't "
                "provide (Python-only, no runtime install). Its instructions are still "
                "usable, but bundled Node scripts won't run."
            ),
        )

    if ".py" in exts:
        return SkillRuntime(
            kind="python",
            runnable=True,
            hint="Python skill — runs in the sandbox (use the office image for doc libs).",
        )

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
