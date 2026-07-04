"""Unit tests for the J.7a Skill eval — Stream J.13a closeout."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_EVAL_DIR = Path(__file__).resolve().parent
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from skill import (  # type: ignore[import-not-found]  # noqa: E402
    SkillCase,
    evaluate_set,
    load_cases,
)


def test_load_cases_parses_twelve() -> None:
    cases = load_cases(_EVAL_DIR / "datasets" / "skill" / "m0_baseline.yaml")
    assert len(cases) == 12


@pytest.mark.asyncio
async def test_baseline_dataset_passes() -> None:
    cases = load_cases(_EVAL_DIR / "datasets" / "skill" / "m0_baseline.yaml")
    report = await evaluate_set(cases)
    assert report.status == "PASS"
    assert report.aggregate_score["pass_rate"] >= 0.80


@pytest.mark.asyncio
async def test_contradictory_resolve_case_fails() -> None:
    """A resolve case asserting prompt content that won't be there is marked failed."""
    from skill import ScriptedSkillVersion  # type: ignore[import-not-found]

    case = SkillCase(
        case_id="contradictory",
        scenario="resolve_bare",
        seed=(
            ScriptedSkillVersion(
                name="foo",
                version=1,
                prompt_fragment="real body",
            ),
        ),
        skills=("foo",),
        # Resolves cleanly but the assertion is wrong on purpose.
        expected_prompt_contains=("this string is absent",),
    )
    report = await evaluate_set([case])
    assert report.status == "FAIL"
    assert not report.per_case[0].passed


@pytest.mark.asyncio
async def test_lazy_skill_summary_present_but_body_withheld() -> None:
    """RT-ADR-12 — a lazy skill contributes only its ``<available-skills>``
    summary; its body is NOT inlined (it is loaded on demand via skill_view).
    This is the progressive-disclosure guardrail: the summary must carry enough
    for the model to decide to load, and the body must not bloat the prompt."""
    from skill import ScriptedSkillVersion  # type: ignore[import-not-found]

    case = SkillCase(
        case_id="lazy-progressive-disclosure",
        scenario="resolve_bare",
        seed=(
            ScriptedSkillVersion(
                name="lazyskill",
                version=1,
                prompt_fragment="SECRET-LAZY-BODY-MARKER",
                lazy_load=True,
            ),
        ),
        skills=("lazyskill",),
        # Summary (name in the <available-skills> block) present…
        expected_prompt_contains=('name="lazyskill"',),
        # …but the eager body must be absent.
        expected_prompt_absent=("SECRET-LAZY-BODY-MARKER",),
    )
    report = await evaluate_set([case])
    assert report.status == "PASS", report.per_case[0].notes
    assert report.per_case[0].passed
