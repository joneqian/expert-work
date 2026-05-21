"""Unit tests for the J.13a baseline aggregator — Stream J.13a."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

_EVAL_DIR = Path(__file__).resolve().parent
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from run_baseline import (  # noqa: E402
    _RUNNERS,
    run_baseline,
)


def test_runner_registry_has_fourteen_capabilities() -> None:
    """Stream J ships 15 sub-items; J.13 is the harness itself and is excluded.

    The shipped registry covers J.1-J.12 + J.14 + J.15 = 14.
    """
    assert len(_RUNNERS) == 14
    seen = {r.capability for r in _RUNNERS}
    assert seen == {
        "J.1_plan_execute",
        "J.2_reflect",
        "J.3_memory_recall",
        "J.4_sub_agent",
        "J.5_rag",
        "J.6_multimodal",
        "J.7_skill",
        "J.8_hitl",
        "J.9_artifact",
        "J.10_trigger",
        "J.11_model_routing",
        "J.12_learning",
        "J.14_per_user_isolation",
        "J.15_persistent_volume",
    }


@pytest.mark.asyncio
async def test_run_baseline_produces_three_pass_eleven_deferred(tmp_path: Path) -> None:
    """J.13a-1 scope: J.3 / J.11 / J.14 PASS; everything else DEFERRED."""
    out = tmp_path / "baseline.yaml"
    reports = await run_baseline(out_path=out)

    pass_caps = sorted(c for c, r in reports.items() if r.status == "PASS")
    deferred_caps = sorted(c for c, r in reports.items() if r.status == "DEFERRED")
    assert pass_caps == [
        "J.11_model_routing",
        "J.14_per_user_isolation",
        "J.3_memory_recall",
    ]
    assert len(deferred_caps) == 11
    assert all(r.status != "FAIL" for r in reports.values())


@pytest.mark.asyncio
async def test_baseline_yaml_layout_is_stable(tmp_path: Path) -> None:
    """The written file has the documented top-level shape."""
    out = tmp_path / "baseline.yaml"
    await run_baseline(out_path=out)
    payload = yaml.safe_load(out.read_text(encoding="utf-8"))

    assert set(payload) == {"metadata", "capabilities"}
    meta = payload["metadata"]
    assert meta["judge_model"] == "claude-haiku-4-5-20251001"
    assert meta["judge_temperature"] == 0.0
    assert meta["rerun_count"] == 3
    assert meta["embedder"] == "helix-fake-keyword-embedder-v1"

    j3 = payload["capabilities"]["J.3_memory_recall"]
    assert j3["status"] == "PASS"
    assert j3["sample_size"] == 32
    assert j3["score"]["recall_at_5"] >= 0.70
    assert j3["score"]["mrr_at_5"] >= 0.55

    j4 = payload["capabilities"]["J.4_sub_agent"]
    assert j4["status"] == "DEFERRED"
    assert j4["score"] == {}
    assert "deferred_reason" in j4
