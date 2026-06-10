"""Judge prompt + verdict parser tests — Stream CM-N5 P1 (Mini-ADR CM-K6)."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from longmem.anthropic_client import render_payload
from longmem.judge import (
    locomo_judge_prompt,
    longmemeval_judge_prompt,
    parse_locomo_verdict,
    parse_longmemeval_verdict,
)

# ---------------------------------------------------------------------------
# LongMemEval — template routing per question type
# ---------------------------------------------------------------------------


def _prompt(question_type: str, *, abstention: bool = False) -> str:
    return longmemeval_judge_prompt(
        question_type=question_type,
        question="Q?",
        answer="A",
        hypothesis="H",
        abstention=abstention,
    )


def test_temporal_template_tolerates_off_by_one() -> None:
    assert "do not penalize off-by-one errors" in _prompt("temporal-reasoning")


def test_knowledge_update_template_accepts_updated_answer() -> None:
    assert "updated answer" in _prompt("knowledge-update")


def test_preference_template_uses_rubric() -> None:
    assert "Rubric: A" in _prompt("single-session-preference")


@pytest.mark.parametrize(
    "qtype", ["single-session-user", "single-session-assistant", "multi-session"]
)
def test_default_template_for_factual_types(qtype: str) -> None:
    prompt = _prompt(qtype)
    assert "Correct Answer: A" in prompt
    assert "Answer yes or no only." in prompt


def test_abstention_template_overrides_type() -> None:
    prompt = _prompt("multi-session", abstention=True)
    assert "unanswerable" in prompt


def test_unknown_type_raises() -> None:
    with pytest.raises(ValueError):
        _prompt("nonexistent-type")


def test_longmemeval_verdict_is_official_substring_rule() -> None:
    assert parse_longmemeval_verdict("Yes.") is True
    assert parse_longmemeval_verdict("  YES, correct") is True
    assert parse_longmemeval_verdict("no") is False
    # The official rule is a plain substring check — pinned as-is.
    assert parse_longmemeval_verdict("eyes") is True


# ---------------------------------------------------------------------------
# LoCoMo — Mem0 protocol
# ---------------------------------------------------------------------------


def test_locomo_prompt_carries_all_three_fields() -> None:
    prompt = locomo_judge_prompt(question="Q?", gold_answer="gold", generated_answer="gen")
    assert "Question: Q?" in prompt
    assert "Gold answer: gold" in prompt
    assert "Generated answer: gen" in prompt


def test_locomo_verdict_json_label() -> None:
    assert parse_locomo_verdict('{"label": "CORRECT"}') is True
    assert parse_locomo_verdict('{"label": "WRONG"}') is False
    assert parse_locomo_verdict('Reasoning first.\n{"label": "CORRECT"}') is True


def test_locomo_verdict_substring_fallback() -> None:
    assert parse_locomo_verdict("The answer is CORRECT") is True
    assert parse_locomo_verdict("WRONG — different topic") is False
    # The prompt forbids emitting both; ambiguity grades conservative.
    assert parse_locomo_verdict("CORRECT or WRONG") is False
    assert parse_locomo_verdict("") is False


# ---------------------------------------------------------------------------
# anthropic transport — payload rendering (no network)
# ---------------------------------------------------------------------------


def test_render_payload_roles_and_system() -> None:
    payload = render_payload(
        [
            SystemMessage(content="sys"),
            HumanMessage(content="hi"),
            AIMessage(content="reply"),
            HumanMessage(content="again"),
        ],
        model="claude-haiku-4-5-20251001",
        max_tokens=64,
    )
    assert payload["system"] == "sys"
    assert [(m["role"], m["content"]) for m in payload["messages"]] == [
        ("user", "hi"),
        ("assistant", "reply"),
        ("user", "again"),
    ]
    # haiku supports sampling -> deterministic grading temperature.
    assert payload["temperature"] == 0.0


def test_render_payload_omits_temperature_for_opus_4_8() -> None:
    """opus-4-7+ rejects temperature with a 400 (CM-9 finding)."""
    payload = render_payload([HumanMessage(content="hi")], model="claude-opus-4-8", max_tokens=64)
    assert "temperature" not in payload
    assert "system" not in payload
