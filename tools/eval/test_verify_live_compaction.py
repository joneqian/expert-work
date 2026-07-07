"""Tests for the live compaction-depth harness — RT-2 PR-6, hardened PR-7.

The HTTP flow (pick agent → create session → seed → fill until compaction →
probe) runs against an ``httpx.MockTransport`` — no live stack, no model key —
so the harness logic is CI-covered. The real run against a strict domestic
backend is the manual ★5 step (see the module docstring). PR-7 adds coverage
for the two hardening pieces: fill auto-sizing (``_plan_fill``) and token
re-mint on 401 (``_RefreshingAuth``).
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

from verify_live_compaction import (  # noqa: E402
    _ASSUMED_OVERHEAD_TOKENS,
    _CHARS_PER_TOKEN,
    _MAX_INPUT_CHARS,
    _agent_context,
    _plan_fill,
    _RefreshingAuth,
    run_compaction_check,
)

_FACT = "EXPERT_WORK-TESTCODE"


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
#: The compressor's empty-middle fail-hard — a config issue, NOT RT-ADR-5.
_OVERFLOW = (
    'event: error\ndata: {"message":"context overflow: estimated 25845 tokens > '
    'threshold 22400 after 0 compression pass(es)","name":"ContextOverflowError"}'
)
_META = 'event: metadata\ndata: {"run_id":"r","thread_id":"t-1"}'
_END = "event: end\ndata: null"


@dataclass
class _Scenario:
    """Tunable mock behaviour for one run of the harness."""

    fire_compaction_on_fill: int = 2  # which fill turn emits a compaction (0 = never)
    error_after_compaction: bool = False
    overflow_error: bool = False  # emit a ContextOverflowError frame vs a system-message 400
    recall_fact: bool = True
    provider: str = "qwen"
    context_window: int = 8192


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
                                # Real /v1/agents shape: an AgentSpecRecord whose
                                # ``spec`` is a full AgentSpec = {metadata, spec}.
                                # The model/policies live under DOUBLE nesting.
                                "spec": {
                                    "metadata": {"name": "rt"},
                                    "spec": {
                                        "model": {
                                            "provider": sc.provider,
                                            "context_window": sc.context_window,
                                        }
                                    },
                                },
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
                        parts.append(_OVERFLOW if sc.overflow_error else _ERROR)
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


# --- PR-7 hardening --------------------------------------------------------


@pytest.mark.parametrize(
    "window,threshold_pct",
    [(8192, 0.7), (32000, 0.7), (128000, 0.7), (200000, 0.7), (1_000_000, 0.8)],
)
def test_plan_fill_leaves_a_fat_middle_before_crossing(window: int, threshold_pct: float) -> None:
    fill_chars, max_turns = _plan_fill(window, threshold_pct)
    threshold_tokens = int(window * threshold_pct)
    per_turn_tokens = fill_chars // _CHARS_PER_TOKEN
    # PR-8: size against the room (threshold minus the ~24k fixed overhead), and
    # cross it over many turns so head_keep(4)+tail_keep(6) hold a small share
    # and a fat middle exists to summarise — not an empty-middle fail-hard.
    room_tokens = max(threshold_tokens - _ASSUMED_OVERHEAD_TOKENS, threshold_tokens // 4)
    turns_to_cross = room_tokens / per_turn_tokens
    assert turns_to_cross >= 10
    # A single turn stays well under the threshold (never one giant msg).
    assert 0 < per_turn_tokens < threshold_tokens
    # …and the budget is enough to climb the room (overshoot).
    assert per_turn_tokens * max_turns > room_tokens


@pytest.mark.parametrize("window", [8192, 32000, 64000, 128000, 200000, 1_000_000, 10_000_000])
def test_plan_fill_never_exceeds_the_api_input_cap(window: int) -> None:
    # PR-8 / the 422 bug: a single fill turn must never exceed the server's
    # RunRequest.input max_length (MAX_RUN_INPUT_CHARS = 65536) no matter how
    # large the window; the harness stays well under it at _MAX_INPUT_CHARS.
    fill_chars, _ = _plan_fill(window, 0.7)
    assert 0 < fill_chars <= _MAX_INPUT_CHARS


@pytest.mark.asyncio
async def test_context_overflow_reported_as_config_not_rt_adr_5(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # PR-8: an empty-middle ContextOverflowError is a window/overhead config
    # issue — it must NOT be mislabelled as an RT-ADR-5 coalescing regression.
    async with _make_client(_Scenario(error_after_compaction=True, overflow_error=True)) as client:
        rc = await run_compaction_check(client, fact_code=_FACT, max_fill_turns=5)
    assert rc == 1
    out = capsys.readouterr().out
    assert "config issue" in out
    assert "NOT an RT-ADR-5" in out
    assert "regression):" not in out  # the RT-ADR-5 branch's phrasing must not fire


@pytest.mark.asyncio
async def test_agent_context_reads_double_nested_window_and_provider() -> None:
    # Guards CRITICAL 2: the model/policies live under rec["spec"]["spec"]…,
    # not rec["spec"]…. A single-nested read silently falls back to defaults.
    async with _make_client(_Scenario(provider="qwen", context_window=32000)) as client:
        window, threshold_pct, provider = await _agent_context(client, "rt", "1.0.0")
    assert window == 32000  # the configured window, NOT the 200_000 default
    assert provider == "qwen"  # NOT "" — proves the double-nested provider read
    assert threshold_pct == 0.7


@pytest.mark.asyncio
async def test_refreshing_auth_remints_on_401() -> None:
    calls = {"requests": 0, "mints": 0, "last_auth": ""}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["requests"] += 1
        calls["last_auth"] = request.headers.get("Authorization", "")
        if calls["requests"] == 1:
            return httpx.Response(401, json={"error": "token expired"})
        return httpx.Response(200, json={"ok": True})

    async def mint() -> str:
        calls["mints"] += 1
        return "fresh-token"

    auth = _RefreshingAuth("stale-token", mint)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test", auth=auth
    ) as client:
        resp = await client.get("/v1/me")

    assert resp.status_code == 200
    assert calls["mints"] == 1  # exactly one re-mint
    assert calls["requests"] == 2  # original + retry
    assert calls["last_auth"] == "Bearer fresh-token"  # retry carried the new token


@pytest.mark.asyncio
async def test_refreshing_auth_without_minter_passes_401_through() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "token expired"})

    auth = _RefreshingAuth("static-token", None)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test", auth=auth
    ) as client:
        resp = await client.get("/v1/me")

    assert resp.status_code == 401  # no minter → no retry, surfaces the 401
