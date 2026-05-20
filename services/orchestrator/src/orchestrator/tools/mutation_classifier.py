"""Stream L.L4 — file-mutation outcome classifier.

Hermes guards against the "agent hallucinates a write that didn't land"
class of failure by aggregating per-tool mutation outcomes across a
turn and injecting an advisory footer back to the model
(``conversation_loop.py:3916-3939`` +
``tool_result_classification.py:9-26``). The agent cannot then claim
"I wrote a/b/c" when ``b`` actually failed — the next prompt carries
the explicit list of unsuccessful mutations.

Our M0 mutation surface is just ``save_artifact`` (Stream J.9's artifact
write path). The classifier is deliberately tool-specific: writing a
classifier stub for tools that don't exist yet would violate the
"don't write speculative code" rule. Future mutation tools (J.7 skill
file edits, J.8 HITL diffs) extend :func:`classify` with their own
entries when they ship.

Mini-ADR L-4 anchors:

* **Tool-specific by name, not by capability flag** — keeps the
  detection logic close to the tool's failure signal (a ``status``
  attribute or a specific meta key) instead of trying to standardise
  every tool.
* **Conservative on unknowns** — :func:`classify` returns ``None``
  for any tool name it doesn't recognise; the runtime simply omits
  it from the advisory. False negatives (no advisory when one
  should fire) are preferable to false positives (spurious advisories
  that train the model to ignore them).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import ToolMessage


@dataclass(frozen=True)
class MutationOutcome:
    """One file-mutation tool call's outcome.

    ``tool_name`` and ``path`` identify the mutation in the advisory
    footer. ``landed=False`` means the file change is NOT in effect —
    the model must not assume the path has the content it requested.
    ``error`` carries the failure summary when available.
    """

    tool_name: str
    path: str
    landed: bool
    error: str | None = None


def classify(
    tool_name: str,
    args: Mapping[str, Any],
    tool_message: ToolMessage,
) -> MutationOutcome | None:
    """Return a :class:`MutationOutcome` iff ``tool_name`` is a known
    file-mutation tool, else ``None``.

    Currently only ``save_artifact`` (J.9 artifact write) is tracked.
    New mutation tools register here when they ship; the M0 spec
    intentionally keeps this set tight to avoid speculative entries.
    """
    if tool_name == "save_artifact":
        return _classify_save_artifact(args, tool_message)
    return None


def _classify_save_artifact(
    args: Mapping[str, Any],
    tool_message: ToolMessage,
) -> MutationOutcome:
    """``save_artifact`` lands iff the dispatch wrapper produced a
    non-error :class:`ToolMessage`. Errors are translated by
    :func:`_invoke_tool` into ``ToolMessage(status="error")`` already,
    so we don't need to re-parse the body — the status flag is the
    canonical "did this work" signal."""
    name_raw = args.get("name")
    path = str(name_raw).strip() if isinstance(name_raw, str) else "<unknown>"
    landed = _tool_message_succeeded(tool_message)
    error = None if landed else _tool_message_error_summary(tool_message)
    return MutationOutcome(
        tool_name="save_artifact",
        path=path or "<unknown>",
        landed=landed,
        error=error,
    )


def _tool_message_succeeded(message: ToolMessage) -> bool:
    """A ``ToolMessage`` with explicit ``status="error"`` did not land.

    LangChain's ``ToolMessage.status`` defaults to ``"success"`` so
    only an explicit failure flag here means the tool surfaced an
    error to the model. The error-path branches in
    :func:`~orchestrator.graph_builder.builder._dispatch_tool` and
    :func:`~orchestrator.graph_builder.builder._invoke_tool` both set
    ``status="error"`` explicitly.
    """
    return getattr(message, "status", "success") != "error"


def _tool_message_error_summary(message: ToolMessage) -> str:
    """Extract a short error summary for the advisory footer."""
    content = message.content
    if isinstance(content, str):
        return content.strip() or "no error message"
    return repr(content)
