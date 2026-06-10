"""Tool-result overflow externalization helpers (Stream CM-5).

When a tool truncates its output it may carry the complete rendering in
``ToolResult.full_content`` (Mini-ADR CM-F3 — only bash / exec_python /
http / mcp, whose overflow is otherwise unrecoverable). The tools node
writes that overflow to the user's workspace under
``.tool_results/<run_id>/<call_id>-<tool>.txt`` and appends a reference
footer to the ``ToolMessage``, turning lossy truncation into recoverable
compression (Manus "keep a reference, never lose the source").

This module is pure helpers only — the best-effort wiring (workspace
write via the CM-0 ``WorkspaceFileWriter``) lives in
``graph_builder.builder``.
"""

from __future__ import annotations

import re
from uuid import UUID

#: Workspace-relative directory all overflow files live under. Lifecycle
#: is owned by the existing workspace retention machinery (J.15 daily
#: backup / 90-day archive) — no bespoke cleanup (Mini-ADR CM-F6).
OVERFLOW_DIR = ".tool_results"

#: Hard cap on one externalized overflow file. The write travels through
#: the sandbox supervisor HTTP API as a snippet parameter — an unbounded
#: payload (e.g. a 500MB stdout) would stall the line (Mini-ADR CM-F5).
OVERFLOW_MAX_CHARS = 2_000_000

_CLAMP_NOTE = f"\n\n[overflow file truncated at {OVERFLOW_MAX_CHARS} chars]"

#: ``call_id`` comes from the model provider and ``tool_name`` from the
#: registry — sanitize both before they become path components so a
#: hostile value (``../../etc``) can never steer the write target.
_UNSAFE_COMPONENT_CHARS = re.compile(r"[^A-Za-z0-9_.-]")
_COMPONENT_MAX_CHARS = 80


def _safe_component(value: str) -> str:
    """Reduce ``value`` to a filesystem-safe path component."""
    cleaned = _UNSAFE_COMPONENT_CHARS.sub("_", value)
    while ".." in cleaned:
        cleaned = cleaned.replace("..", "_")
    cleaned = cleaned.strip(".") or "unknown"
    return cleaned[:_COMPONENT_MAX_CHARS]


def overflow_rel_path(*, run_id: UUID | None, call_id: str, tool_name: str) -> str:
    """Workspace-relative path for one tool call's overflow file."""
    run_part = str(run_id) if run_id is not None else "adhoc"
    return f"{OVERFLOW_DIR}/{run_part}/{_safe_component(call_id)}-{_safe_component(tool_name)}.txt"


def clamp_overflow(full_content: str) -> str:
    """Cap an overflow payload at :data:`OVERFLOW_MAX_CHARS`.

    Over-cap payloads keep the head plus an explicit trailing note —
    the file itself must never silently pretend to be complete.
    """
    if len(full_content) <= OVERFLOW_MAX_CHARS:
        return full_content
    return full_content[:OVERFLOW_MAX_CHARS] + _CLAMP_NOTE


def render_overflow_footer(*, rel: str, total_chars: int) -> str:
    """Reference footer appended to a ``ToolMessage`` after a successful write.

    Only call this once the workspace write landed (Mini-ADR CM-F5 — the
    footer must never point at a file that does not exist).
    """
    return (
        "\n\n<tool-result-overflow>\n"
        f"The output above was truncated. The full output ({total_chars} chars) was saved to "
        f"{rel} in your workspace. Use read_file / exec_python / bash to inspect it.\n"
        "</tool-result-overflow>"
    )
