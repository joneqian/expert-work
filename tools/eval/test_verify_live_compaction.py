"""Tests for the live compaction-depth harness — RT-2 PR-6.

The HTTP flow (pick agent → create session → seed → fill until compaction →
probe) runs against an ``httpx.MockTransport`` — no live stack, no model key —
so the harness logic is CI-covered. The real run against a strict domestic
backend is the manual ★5 step (see the module docstring).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

_EVAL_DIR = Path(__file__).resolve().parent
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from verify_live_compaction import run_compaction_check  # noqa: E402

_FACT = "HELIX-TESTCODE"


def _frames(*frames: str) -> bytes:
    return ("\n\n".join(frames) + "\n\n").encode("utf-8")


def _ai(text: str) -> str:
    return "event: updates\ndata: " + json.dumps(
        {"agent": {"messages": [{"type": "ai", "content": text}]}}
    )


_COMPACTION = "event: compaction\ndata: " + json.dumps(
    {"passes": 1, "tokens_before": 12000, "tokens_after": 3400, "summary_chars": 890}
)
_ERROR = 'event: error\ndata: {"message":"System message must be at the beginning"}'
_META = 'event: metadata\ndata: {"run_id":"r","thread_id":"t-1"}'
_END = "event: end\ndata: null"


@dataclass
class _Scenario:
    """Tunable mock behaviour for one run of the harness."""

    fire_compaction_on_fill: int = 2  # which fill turn emits a compaction (0 = never)
    error_after_compaction: bool = False
    recall_fact: bool = True
    provider: str = "qwen"


def _make_client(sc: _Scenario) -> httpx.AsyncClient:
    fill_seen = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal fill_seen
        path = request.url.path
        if path == "/v1/agents":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "items": [
                            {
                                "name": "rt",
                                "version": "1.0.0",
                                "spec": {"model": {"provider": sc.provider}},
                            }
                        ],
                        "total": 1,
                    },
                    "error": None,
                },
            )
        if path == "/v1/sessions":
            return httpx.Response(
                201, json={"success": True, "data": {"thread_id": "t-1"}, "error": None}
            )
        if path.endswith("/runs"):
            body = json.loads(request.content)
            inp = body.get("input", "")
            if _FACT in inp and "remember" in inp.lower():
                content = _frames(_META, _ai("noted"), _END)  # seed turn
            elif inp.startswith("Context block"):
                fill_seen += 1
                if sc.fire_compaction_on_fill and fill_seen == sc.fire_compaction_on_fill:
                    parts = [_META, _COMPACTION]
                    if sc.error_after_compaction:
                        parts.append(_ERROR)
                    else:
                        parts.append(_ai("ok"))
                    parts.append(_END)
                    content = _frames(*parts)
                else:
                    content = _frames(_META, _ai("ok"), _END)
            else:  # probe turn
                reply = f"The code is {_FACT}." if sc.recall_fact else "I don't recall a code."
                content = _frames(_META, _ai(reply), _END)
            return httpx.Response(
                200, content=content, headers={"content-type": "text/event-stream"}
            )
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")


@pytest.mark.asyncio
async def test_pass_when_compaction_fires_and_fact_survives() -> None:
    async with _make_client(_Scenario()) as client:
        rc = await run_compaction_check(client, fact_code=_FACT, max_fill_turns=5)
    assert rc == 0


@pytest.mark.asyncio
async def test_fail_on_error_frame_after_compaction() -> None:
    # RT-ADR-5 regression: a strict backend 400s on the mid-conversation
    # summary → error frame right after compaction → hard fail.
    async with _make_client(_Scenario(error_after_compaction=True)) as client:
        rc = await run_compaction_check(client, fact_code=_FACT, max_fill_turns=5)
    assert rc == 1


@pytest.mark.asyncio
async def test_fail_when_no_compaction_fires() -> None:
    async with _make_client(_Scenario(fire_compaction_on_fill=0)) as client:
        rc = await run_compaction_check(client, fact_code=_FACT, max_fill_turns=3)
    assert rc == 1


@pytest.mark.asyncio
async def test_fail_when_fact_lost_across_compaction() -> None:
    async with _make_client(_Scenario(recall_fact=False)) as client:
        rc = await run_compaction_check(client, fact_code=_FACT, max_fill_turns=5)
    assert rc == 1


@pytest.mark.asyncio
async def test_warns_on_non_strict_backend_but_still_runs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with _make_client(_Scenario(provider="openai")) as client:
        rc = await run_compaction_check(client, fact_code=_FACT, max_fill_turns=5)
    assert rc == 0
    assert "strict-backend" in capsys.readouterr().out
