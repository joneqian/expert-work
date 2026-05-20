"""Stream L.L4 — :mod:`orchestrator.tools.mutation_classifier` unit tests.

Pins the classifier's contract: a ``save_artifact`` ``ToolMessage``
with ``status="error"`` becomes a ``MutationOutcome`` with
``landed=False`` (so the next agent step emits the advisory footer);
a success keeps ``landed=True``; unknown tool names return ``None``.
"""

from __future__ import annotations

from langchain_core.messages import ToolMessage

from orchestrator.tools.mutation_classifier import MutationOutcome, classify

# ---------------------------------------------------------------------------
# Known mutation tool: save_artifact
# ---------------------------------------------------------------------------


def test_save_artifact_success_lands() -> None:
    """A successful ``save_artifact`` dispatch (no explicit error
    status) lands — the file is registered, no advisory needed."""
    msg = ToolMessage(content="Saved artifact 'report.md' as version 1.", tool_call_id="tc-1")
    outcome = classify("save_artifact", {"name": "report.md"}, msg)
    assert outcome == MutationOutcome(
        tool_name="save_artifact", path="report.md", landed=True, error=None
    )


def test_save_artifact_error_does_not_land() -> None:
    """A ``status="error"`` ToolMessage flips ``landed`` to ``False``
    and captures the error body — the advisory needs both."""
    msg = ToolMessage(
        content="[tool error] OSError: disk full",
        tool_call_id="tc-1",
        status="error",
    )
    outcome = classify("save_artifact", {"name": "report.md"}, msg)
    assert outcome is not None
    assert outcome.landed is False
    assert outcome.path == "report.md"
    assert outcome.error is not None
    assert "disk full" in outcome.error


def test_save_artifact_missing_name_args_falls_back_to_placeholder() -> None:
    """If the LLM somehow omits the required ``name`` (the registry's
    JSON schema would normally reject this earlier), the classifier
    still produces a record with an explicit placeholder so the
    advisory line isn't silently empty."""
    msg = ToolMessage(content="dummy", tool_call_id="tc-1", status="error")
    outcome = classify("save_artifact", {}, msg)
    assert outcome is not None
    assert outcome.path == "<unknown>"


def test_save_artifact_strips_whitespace_from_path() -> None:
    msg = ToolMessage(content="ok", tool_call_id="tc-1")
    outcome = classify("save_artifact", {"name": "  report.md  "}, msg)
    assert outcome is not None
    assert outcome.path == "report.md"


def test_save_artifact_handles_non_string_name() -> None:
    """A bogus type for ``name`` (LLM hallucination) lands as the
    placeholder — same defensive shape as the missing-arg case."""
    msg = ToolMessage(content="ok", tool_call_id="tc-1", status="error")
    outcome = classify("save_artifact", {"name": 123}, msg)
    assert outcome is not None
    assert outcome.path == "<unknown>"


# ---------------------------------------------------------------------------
# Unknown tools — classifier opts out, returns None
# ---------------------------------------------------------------------------


def test_unknown_tool_returns_none() -> None:
    """Tools not in the L4 classifier's M0 set produce no outcome —
    Mini-ADR L-4 keeps the set tight (only ``save_artifact`` today)
    so spurious advisories never train the model to ignore them."""
    msg = ToolMessage(content="result", tool_call_id="tc-1")
    assert classify("web_search", {"q": "x"}, msg) is None
    assert classify("update_plan", {"steps": ["x"], "reason": "y"}, msg) is None
    assert classify("knowledge_search", {"query": "y"}, msg) is None


def test_unknown_tool_with_error_status_still_returns_none() -> None:
    """A non-mutation tool that errored doesn't need an advisory —
    the LLM already saw the error ToolMessage. L4 only adds value
    for mutation tools where the LLM might claim the change landed."""
    msg = ToolMessage(content="error", tool_call_id="tc-1", status="error")
    assert classify("http", {"url": "x"}, msg) is None
