"""B3 — TokenBudget / guard 帧纯函数单测."""

from __future__ import annotations

import json
from typing import Any

import pytest

from orchestrator.tools._guards import (
    TokenBudget,
    build_guard_frame,
    emit_guard_frame,
    usage_total,
)


def test_token_budget_thresholds() -> None:
    tb = TokenBudget(limit=1000)
    tb.add(799)
    assert not tb.warning and not tb.exhausted
    tb.add(1)  # 800 = 恰好 80%
    assert tb.warning and not tb.exhausted
    tb.add(199)
    assert not tb.exhausted
    tb.add(1)  # 1000 = 恰好 limit
    assert tb.exhausted
    assert tb.remaining == 0


def test_token_budget_remaining() -> None:
    tb = TokenBudget(limit=100)
    tb.add(30)
    assert tb.remaining == 70
    tb.add(200)
    assert tb.remaining == 0  # 不为负


def test_usage_total_sums_four_parts() -> None:
    assert (
        usage_total(
            {
                "input_tokens": 10,
                "output_tokens": 5,
                "input_token_details": {"cache_creation": 3, "cache_read": 2},
            }
        )
        == 20
    )


def test_usage_total_defensive() -> None:
    assert usage_total(None) == 0
    assert usage_total({}) == 0
    assert usage_total({"input_tokens": "bad", "output_tokens": 4}) == 4


def test_build_guard_frame_shape_json_safe() -> None:
    frame = build_guard_frame(
        kind="tripped", guard="token_budget", detail={"spent": 503_000, "limit": 500_000}
    )
    assert frame == {
        "kind": "tripped",
        "guard": "token_budget",
        "detail": {"spent": 503_000, "limit": 500_000},
    }
    json.dumps(frame)


@pytest.mark.asyncio
async def test_emit_guard_frame_best_effort() -> None:
    async def _boom(frame: dict[str, Any]) -> None:
        raise RuntimeError("down")

    await emit_guard_frame(_boom, {"kind": "warning"})  # 不抛
    await emit_guard_frame(None, {"kind": "warning"})  # 无 sink 零动作
