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

#: Opening tag of the helix-owned reference footer appended after an
#: externalized result (see :func:`render_overflow_footer`). The footer is
#: appended last and stays trusted (outside the spotlight fence). Exported as
#: a named constant so the CM-12 prune gate
#: (``orchestrator.context.tool_result_prune``) can detect an externalized
#: ToolMessage and slice the footer back out without string-format drift.
OVERFLOW_FOOTER_TAG_OPEN = "<tool-result-overflow>"

#: Hard cap on one externalized overflow file. The write travels through
#: the sandbox supervisor HTTP API as a snippet parameter — an unbounded
#: payload (e.g. a 500MB stdout) would stall the line (Mini-ADR CM-F5).
OVERFLOW_MAX_CHARS = 2_000_000

_CLAMP_NOTE = f"\n\n[overflow file truncated at {OVERFLOW_MAX_CHARS} chars]"

#: Universal per-result externalization threshold (tool-result-context-budget).
#: A tool result whose rendered ``content`` exceeds this is externalized to the
#: workspace and replaced in-context with a head+tail preview + reference — even
#: when the tool itself didn't set ``full_content``. Mirrors deer-flow's
#: ``externalize_min_chars`` (12k). Without this a tool like ``web_search`` (no
#: ``full_content``, ~20-40k chars/call) accumulates across turns and blows the
#: context window.
EXTERNALIZE_MIN_CHARS = 12_000

#: Preview kept in-context when a result is externalized: head + tail so the
#: model sees the start (usually most relevant) and end without the bulk.
PREVIEW_HEAD_CHARS = 2_000
PREVIEW_TAIL_CHARS = 1_000

#: When the workspace write is unavailable (no writer / write failed) the
#: generalized path degrades to in-place head+tail truncation so context is
#: still bounded — but without a file reference (there is no file).
FALLBACK_MAX_CHARS = 30_000

#: Tools exempt from generalized externalization: the fetch-back readers whose
#: source is cheaply re-readable, so externalizing them would just create a
#: persist→read→persist loop (the original CM-F3 guard, narrowed from "every
#: read_only tool" to exactly these — ``web_search`` is read_only too but its
#: results are NOT cheaply re-readable, so it must still be externalized).
EXEMPT_TOOLS = frozenset({"read_document", "read_file", "list_dir"})


def _elide(text: str, *, head: int, tail: int, note: str) -> str:
    """Keep ``head`` chars from the front + ``tail`` from the back, ``note`` between."""
    if len(text) <= head + tail:
        return text
    elided = len(text) - head - tail
    marker = f"\n\n[... {note}: {elided:,} chars elided ...]\n\n"
    return text[:head] + marker + text[len(text) - tail :]


def make_preview(content: str) -> str:
    """Head+tail preview of an externalized result (full copy is in the workspace)."""
    return _elide(content, head=PREVIEW_HEAD_CHARS, tail=PREVIEW_TAIL_CHARS, note="preview")


def fallback_truncate(content: str) -> str:
    """In-place head+tail truncation when no workspace file could be written.

    Bounds context without a file reference — the footer must NOT be appended
    (there is nothing to point at). Keeps a larger budget than the preview since
    this is the only copy the model gets.
    """
    if len(content) <= FALLBACK_MAX_CHARS:
        return content
    head = FALLBACK_MAX_CHARS * 2 // 3
    tail = FALLBACK_MAX_CHARS - head
    return _elide(content, head=head, tail=tail, note="truncated, workspace unavailable")


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
        f"\n\n{OVERFLOW_FOOTER_TAG_OPEN}\n"
        f"The output above was truncated. The full output ({total_chars} chars) was saved to "
        f"{rel} in your workspace. Use read_file / exec_python / bash to inspect it.\n"
        "</tool-result-overflow>"
    )
