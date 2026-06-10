"""Binary QA judges — Stream CM-N5 P1 tier (Mini-ADR CM-K6).

Both benchmark protocols are transplanted **verbatim** from the
upstream evaluation code (fetched 2026-06-10) so helix numbers are
produced under the published grading rules:

- **LongMemEval** (``src/evaluation/evaluate_qa.py``,
  ``get_anscheck_prompt``): per-question-type prompt templates —
  temporal reasoning tolerates off-by-one day counts, knowledge-update
  accepts any response containing the updated answer, preference grades
  against a rubric, abstention checks the model recognised the question
  as unanswerable. Verdict = official substring rule
  (``'yes' in reply.lower()``), temperature 0.
- **LoCoMo** (Mem0 ``evaluation/metrics/llm_judge.py``,
  ``ACCURACY_PROMPT``): the community-standard judge (the official
  lexical F1 is deprecated); generous topical/temporal matching,
  CORRECT/WRONG JSON label, temperature 0.

The only deliberate divergence is the judge **model**: upstream uses
gpt-4o(-mini); helix uses the repo's fixed Anthropic Haiku judge
(Mini-ADR J-39 precedent). Stated on every report — numbers are for
self-regression first; cross-vendor comparisons must note the judge
difference (CM-K6).
"""

# ruff: noqa: E501, RUF001 — LOCOMO_ACCURACY_PROMPT is a verbatim upstream
# transplant (curly quotes, long lines included); editing it would change
# the grading protocol the numbers claim to follow.

from __future__ import annotations

import json
import re
from typing import Protocol

# ---------------------------------------------------------------------------
# LongMemEval — get_anscheck_prompt(), verbatim templates
# ---------------------------------------------------------------------------

_LME_DEFAULT = (
    "I will give you a question, a correct answer, and a response from a model. "
    "Please answer yes if the response contains the correct answer. Otherwise, answer no. "
    "If the response is equivalent to the correct answer or contains all the intermediate "
    "steps to get the correct answer, you should also answer yes. If the response only "
    "contains a subset of the information required by the answer, answer no. "
    "\n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\n"
    "Is the model response correct? Answer yes or no only."
)

_LME_TEMPORAL = (
    "I will give you a question, a correct answer, and a response from a model. "
    "Please answer yes if the response contains the correct answer. Otherwise, answer no. "
    "If the response is equivalent to the correct answer or contains all the intermediate "
    "steps to get the correct answer, you should also answer yes. If the response only "
    "contains a subset of the information required by the answer, answer no. In addition, "
    "do not penalize off-by-one errors for the number of days. If the question asks for "
    "the number of days/weeks/months, etc., and the model makes off-by-one errors "
    "(e.g., predicting 19 days when the answer is 18), the model's response is still "
    "correct. \n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\n"
    "Is the model response correct? Answer yes or no only."
)

_LME_KNOWLEDGE_UPDATE = (
    "I will give you a question, a correct answer, and a response from a model. "
    "Please answer yes if the response contains the correct answer. Otherwise, answer no. "
    "If the response contains some previous information along with an updated answer, "
    "the response should be considered as correct as long as the updated answer is the "
    "required answer.\n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\n"
    "Is the model response correct? Answer yes or no only."
)

_LME_PREFERENCE = (
    "I will give you a question, a rubric for desired personalized response, and a "
    "response from a model. Please answer yes if the response satisfies the desired "
    "response. Otherwise, answer no. The model does not need to reflect all the points "
    "in the rubric. The response is correct as long as it recalls and utilizes the "
    "user's personal information correctly.\n\nQuestion: {}\n\nRubric: {}\n\n"
    "Model Response: {}\n\nIs the model response correct? Answer yes or no only."
)

_LME_ABSTENTION = (
    "I will give you an unanswerable question, an explanation, and a response from a "
    "model. Please answer yes if the model correctly identifies the question as "
    "unanswerable. The model could say that the information is incomplete, or some "
    "other information is given but the asked information is not.\n\nQuestion: {}\n\n"
    "Explanation: {}\n\nModel Response: {}\n\n"
    "Does the model correctly identify the question as unanswerable? Answer yes or no only."
)


def longmemeval_judge_prompt(
    *,
    question_type: str,
    question: str,
    answer: str,
    hypothesis: str,
    abstention: bool,
) -> str:
    """Official ``get_anscheck_prompt`` port — template keyed on type."""
    if abstention:
        return _LME_ABSTENTION.format(question, answer, hypothesis)
    if question_type == "temporal-reasoning":
        return _LME_TEMPORAL.format(question, answer, hypothesis)
    if question_type == "knowledge-update":
        return _LME_KNOWLEDGE_UPDATE.format(question, answer, hypothesis)
    if question_type == "single-session-preference":
        return _LME_PREFERENCE.format(question, answer, hypothesis)
    if question_type in ("single-session-user", "single-session-assistant", "multi-session"):
        return _LME_DEFAULT.format(question, answer, hypothesis)
    raise ValueError(f"unknown LongMemEval question type: {question_type!r}")


def parse_longmemeval_verdict(reply: str) -> bool:
    """Official rule: ``'yes' in reply.lower()``."""
    return "yes" in reply.lower()


# ---------------------------------------------------------------------------
# LoCoMo — Mem0 ACCURACY_PROMPT, verbatim (upstream uses curly quotes)
# ---------------------------------------------------------------------------

LOCOMO_ACCURACY_PROMPT = """
Your task is to label an answer to a question as ’CORRECT’ or ’WRONG’. You will be given the following data:
    (1) a question (posed by one user to another user),
    (2) a ’gold’ (ground truth) answer,
    (3) a generated answer
which you will score as CORRECT/WRONG.

The point of the question is to ask about something one user should know about the other user based on their prior conversations.
The gold answer will usually be a concise and short answer that includes the referenced topic, for example:
Question: Do you remember what I got the last time I went to Hawaii?
Gold answer: A shell necklace
The generated answer might be much longer, but you should be generous with your grading - as long as it touches on the same topic as the gold answer, it should be counted as CORRECT.

For time related questions, the gold answer will be a specific date, month, year, etc. The generated answer might be much longer or use relative time references (like "last Tuesday" or "next month"), but you should be generous with your grading - as long as it refers to the same date or time period as the gold answer, it should be counted as CORRECT. Even if the format differs (e.g., "May 7th" vs "7 May"), consider it CORRECT if it's the same date.

Now it's time for the real question:
Question: {question}
Gold answer: {gold_answer}
Generated answer: {generated_answer}

First, provide a short (one sentence) explanation of your reasoning, then finish with CORRECT or WRONG.
Do NOT include both CORRECT and WRONG in your response, or it will break the evaluation script.

Just return the label CORRECT or WRONG in a json format with the key as "label".
"""


def locomo_judge_prompt(*, question: str, gold_answer: str, generated_answer: str) -> str:
    return LOCOMO_ACCURACY_PROMPT.format(
        question=question, gold_answer=gold_answer, generated_answer=generated_answer
    )


_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def parse_locomo_verdict(reply: str) -> bool:
    """Mem0 rule: JSON ``label == "CORRECT"`` — with a substring fallback.

    Upstream forces ``response_format=json_object``; the Anthropic judge
    has no such switch, so a reply that drops the JSON wrapper is graded
    by the label word itself (the prompt forbids emitting both labels).
    """
    match = _JSON_OBJECT.search(reply)
    if match is not None:
        try:
            label = json.loads(match.group(0)).get("label")
            if isinstance(label, str):
                return label.strip().upper() == "CORRECT"
        except json.JSONDecodeError:
            pass
    has_correct = "CORRECT" in reply.upper()
    has_wrong = "WRONG" in reply.upper()
    return has_correct and not has_wrong


# ---------------------------------------------------------------------------
# Judge transport — one text completion per verdict
# ---------------------------------------------------------------------------


class TextJudge(Protocol):
    """Returns the judge model's raw reply for one prompt."""

    async def complete(self, *, prompt: str) -> str:
        """One text completion for one judge prompt."""


class ScriptedTextJudge:
    """Deterministic CI double — replies looked up by substring match."""

    def __init__(self, replies: dict[str, str], *, default: str = "no") -> None:
        self._replies = dict(replies)
        self._default = default

    async def complete(self, *, prompt: str) -> str:
        for needle, reply in self._replies.items():
            if needle in prompt:
                return reply
        return self._default
