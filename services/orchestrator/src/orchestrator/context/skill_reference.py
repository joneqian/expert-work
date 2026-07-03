"""RT-2 PR-3 (RT-ADR-7) — skill reference lines for lazy-skill reads.

Lazy skills (every SE distilled skill included) enter the context at run
time through the ``skill_view`` tool: a ``ToolMessage(name="skill_view")``
whose content is the skill body (up to 20k chars) and whose ``artifact``
carries the ``skill_name`` + ``path`` + ``result`` (``ToolResult.meta``
surfaced by the dispatch layer). When a context gate has to shed that
content, the generic collapse ladder is the wrong shape — the skill row is
durable in the skill store and re-readable through ``skill_view`` at any
time, so the cheapest recoverable replacement is a one-line REFERENCE:
skill name + source path + the re-read hint. O(1) tokens; the model keeps
both the skill's existence and its retrieval handle. Upstream deer-flow
#3887 dropped the full-content preservation budgets for exactly this shape
(reference + re-read); helix follows suit (STREAM-RT-DESIGN §8, RT-ADR-7).

Only a SUCCESSFUL read (``result`` in ``ok`` / ``truncated``) gets a
reference: every ``skill_view`` failure branch also stamps ``skill_name``
into the meta, but a ``[BLOCKED]`` placeholder (drift / redacted /
archived — security stops) or a not_found / not_allowed echo has no
recoverable body, and rewriting it into "re-readable" would coax the model
into re-calling a skill the platform just flagged. Those fall through to
the caller's generic handling. Everything model- or store-controlled in
the line (name, path) is ``repr``-escaped and the name is length-capped,
so a crafted value cannot break out of the bracketed reference.

The line deliberately avoids tool-call syntax (no ``skill_view(...)``
expression): the L2 summariser prompt forbids tool-call syntax in its
output, so a call-shaped hint would be dropped from summaries. The summary
therefore keeps the skill NAME; the actual re-read handle is provided by
the available-skills list in the system prompt and by the CM-12 pruner
stub, not by a call expression inside a summary.

Consumed by the two prompt-view gates that previously ate skill content:

* CM-12 :mod:`orchestrator.context.tool_result_prune` — the stub left for a
  collapsed ``skill_view`` result is the reference line;
* L2 :mod:`orchestrator.context.compressor` — the summariser transcript
  renders a ``skill_view`` message as the reference line instead of feeding
  the whole body into the summary call.

The CM-2 WorkingWindow (whole-turn drop) is deliberately NOT special-cased:
when the turn containing a ``skill_view`` read is dropped, the model still
sees the available-skills list in its system prompt and can re-issue the
``skill_view`` call — per-message surgery inside the turn-based window would
add complexity for little over that. Eager skills live in the leading
SystemMessage block, which every gate preserves — no rescue needed.
"""

from __future__ import annotations

from langchain_core.messages import BaseMessage, ToolMessage

#: Tool name under which lazy skill content enters the context
#: (:class:`~orchestrator.tools.skill_view.SkillViewTool`).
SKILL_VIEW_TOOL_NAME = "skill_view"

#: ``ToolResult.meta`` keys surfaced in ``ToolMessage.artifact`` by the
#: dispatch layer. ``skill_name`` + ``result`` ride every skill_view
#: result; ``path`` (the source file inside the skill package) rides
#: successful reads (absent on messages checkpointed before RT-2 PR-3 —
#: the reference then degrades to name-only).
_SKILL_NAME_ARTIFACT_KEY = "skill_name"
_SKILL_PATH_ARTIFACT_KEY = "path"
_SKILL_RESULT_ARTIFACT_KEY = "result"

#: ``result`` values of a successful read — the only ones with a
#: recoverable body worth referencing. Failure/blocked placeholders
#: (not_allowed / not_found / archived / drift / redacted) keep the
#: generic handling: a security stop must never be rewritten into a
#: re-read encouragement.
_SUCCESS_RESULTS = frozenset({"ok", "truncated"})

#: Bound on the skill name echoed into the reference. Successful reads
#: resolve against the skill store so names are short in practice; the cap
#: keeps the O(1)-token contract even for a pathological value.
_SKILL_NAME_CHAR_CAP = 200


def skill_view_reference(message: BaseMessage) -> str | None:
    """One-line reference replacing a ``skill_view`` result's content.

    ``None`` when ``message`` is not a ``skill_view`` :class:`ToolMessage`,
    its artifact records anything but a successful read, or it carries no
    skill name — the caller falls back to its generic handling, keeping the
    default path of both consumers byte-identical.
    """
    if not isinstance(message, ToolMessage) or message.name != SKILL_VIEW_TOOL_NAME:
        return None
    artifact = message.artifact
    if not isinstance(artifact, dict):
        return None
    if artifact.get(_SKILL_RESULT_ARTIFACT_KEY) not in _SUCCESS_RESULTS:
        return None
    skill_name = artifact.get(_SKILL_NAME_ARTIFACT_KEY)
    if not isinstance(skill_name, str) or not skill_name:
        return None
    name = skill_name[:_SKILL_NAME_CHAR_CAP]
    path = artifact.get(_SKILL_PATH_ARTIFACT_KEY)
    if isinstance(path, str) and path:
        return (
            f"[skill {name!r} — content compressed; re-readable via the "
            f"skill_view tool (name {name!r}, path {path!r})]"
        )
    return (
        f"[skill {name!r} — content compressed; re-readable via the "
        f"skill_view tool (name {name!r})]"
    )
