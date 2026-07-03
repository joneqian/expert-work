"""Unit tests for the LLM-judge providers — Stream J.13a."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

_EVAL_DIR = Path(__file__).resolve().parent
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from _judge import (  # noqa: E402
    JUDGE_MODEL,
    AnthropicHaikuJudge,
    ScriptedJudge,
    _parse_score,
    make_judge_from_env,
)


@pytest.mark.asyncio
async def test_scripted_judge_uses_case_map() -> None:
    judge = ScriptedJudge({"a": 5, "b": 3}, default_score=4)
    assert await judge.score(case_id="a", prompt="x") == 5
    assert await judge.score(case_id="b", prompt="x") == 3
    # Cases not in the map fall back to default_score.
    assert await judge.score(case_id="missing", prompt="x") == 4


def test_parse_score_extracts_first_digit() -> None:
    assert _parse_score("4") == 4
    assert _parse_score("The score is 5/5.") == 5
    assert _parse_score("definitely not a number") == 0


@pytest.mark.asyncio
async def test_anthropic_haiku_judge_parses_text_block() -> None:
    """Drive AnthropicHaikuJudge with a mock httpx client."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/messages"
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "5"}],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    judge = AnthropicHaikuJudge(api_key="sk-test", http_client=client)
    assert await judge.score(case_id="x", prompt="rate this") == 5
    await client.aclose()


@pytest.mark.asyncio
async def test_anthropic_haiku_judge_returns_zero_on_5xx() -> None:
    """Server error must not silently inflate the baseline."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server error")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    judge = AnthropicHaikuJudge(api_key="sk-test", http_client=client)
    assert await judge.score(case_id="x", prompt="rate this") == 0
    await client.aclose()


@pytest.mark.asyncio
async def test_anthropic_haiku_judge_returns_zero_on_malformed_envelope() -> None:
    """RT-1 PR-2 — pydantic envelope validation keeps the 0-on-failure
    semantics: a non-JSON body, a missing content list, and a non-text
    first block all score 0 (loud threshold trip, never inflation)."""
    bodies: list[httpx.Response] = [
        httpx.Response(200, text="not json at all"),
        httpx.Response(200, json={"content": []}),
        httpx.Response(200, json={"content": [{"type": "tool_use", "id": "t1"}]}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return bodies.pop(0)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    judge = AnthropicHaikuJudge(api_key="sk-test", http_client=client)
    assert await judge.score(case_id="a", prompt="rate this") == 0
    assert await judge.score(case_id="b", prompt="rate this") == 0
    assert await judge.score(case_id="c", prompt="rate this") == 0
    await client.aclose()


def test_judge_model_is_haiku_4_5() -> None:
    """Mini-ADR J-39 fixes the judge model id."""
    assert JUDGE_MODEL == "claude-haiku-4-5-20251001"


def test_make_judge_from_env_returns_scripted_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    judge = make_judge_from_env({"a": 5})
    assert isinstance(judge, ScriptedJudge)


def test_make_judge_from_env_returns_haiku_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    judge = make_judge_from_env()
    assert isinstance(judge, AnthropicHaikuJudge)
