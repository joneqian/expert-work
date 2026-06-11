"""Unit tests for the offline prompt A/B harness — Stream HX-5 (§ 6.5-PR3).

Fake providers only (CI has no model credentials); the real-LLM path is
the manual ``--provider env`` run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_EVAL_DIR = Path(__file__).resolve().parent
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from helix_eval import Assertion, EvalCase, EvalSet  # type: ignore[import-not-found]  # noqa: E402
from prompt_ab import (  # type: ignore[import-not-found]  # noqa: E402
    Variant,
    load_variant,
    main,
    run_ab,
)

_EVAL_SET = EvalSet(
    name="greeting",
    cases=(
        EvalCase(
            id="polite",
            prompt="greet the user",
            assertions=(Assertion(type="contains", value="hello"),),
        ),
        EvalCase(
            id="formal",
            prompt="address the board",
            assertions=(Assertion(type="contains", value="esteemed"),),
        ),
    ),
)


def _factory_from_prompts(behaviour: dict[str, dict[str, str]]):
    """Provider factory: per-variant canned outputs keyed by case prompt."""

    def factory(variant: Variant):
        canned = behaviour[variant.label]

        async def complete(prompt: str) -> str:
            return canned.get(prompt, "")

        return complete

    return factory


@pytest.mark.asyncio
async def test_run_ab_pairs_verdicts_and_counts_discordants() -> None:
    factory = _factory_from_prompts(
        {
            # A passes only "polite"; B passes both → one discordant (B-only).
            "a": {"greet the user": "hello there", "address the board": "hi folks"},
            "b": {"greet the user": "hello there", "address the board": "esteemed members"},
        }
    )
    report = await run_ab(_EVAL_SET, Variant("a", "prompt A"), Variant("b", "prompt B"), factory)

    assert report.a_pass_rate == 0.5
    assert report.b_pass_rate == 1.0
    assert report.a_only == 0
    assert report.b_only == 1
    payload = report.to_json()
    assert payload["delta"] == 0.5
    assert payload["discordant"] == {"a_only": 0, "b_only": 1}
    assert [c["id"] for c in payload["cases"]] == ["polite", "formal"]


def test_load_variant_accepts_manifest_and_rejects_missing_prompt(tmp_path: Path) -> None:
    manifest = tmp_path / "variant_a.yaml"
    manifest.write_text(
        "apiVersion: helix.io/v1\nspec:\n  system_prompt:\n    template: 'be kind'\n",
        encoding="utf-8",
    )
    variant = load_variant(manifest)
    assert variant.label == "variant_a"
    assert variant.system_prompt == "be kind"

    bad = tmp_path / "broken.yaml"
    bad.write_text("spec: {model: {provider: anthropic}}", encoding="utf-8")
    with pytest.raises(ValueError, match="system_prompt"):
        load_variant(bad)


def test_cli_mock_provider_writes_artifact(tmp_path: Path) -> None:
    eval_yaml = tmp_path / "set.yaml"
    eval_yaml.write_text(
        "name: demo\n"
        "cases:\n"
        "  - id: c1\n"
        "    prompt: say hi\n"
        "    mock_response: hi\n"
        "    assertions:\n"
        "      - {type: contains, value: hi}\n",
        encoding="utf-8",
    )
    spec = "spec:\n  system_prompt:\n    template: 'x'\n"
    (tmp_path / "a.yaml").write_text(spec, encoding="utf-8")
    (tmp_path / "b.yaml").write_text(spec, encoding="utf-8")
    out = tmp_path / "report.json"

    rc = main(
        [
            "--eval-set",
            str(eval_yaml),
            "--spec-a",
            str(tmp_path / "a.yaml"),
            "--spec-b",
            str(tmp_path / "b.yaml"),
            "--out",
            str(out),
        ]
    )

    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["n"] == 1
    assert payload["a_pass_rate"] == 1.0
    assert payload["delta"] == 0.0
    assert payload["discordant"] == {"a_only": 0, "b_only": 0}
