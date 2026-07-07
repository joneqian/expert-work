"""Offline prompt A/B harness — Stream HX-5 (STREAM-HX-DESIGN § 6.2-③).

Compares two manifest variants' ``system_prompt`` over one eval set:
both variants answer the same cases, the report pairs the verdicts.
A tool, not platform state (Mini-ADR HX-E4): the artifact lands in
``eval-out/``; persisting a result means committing it, the CM-N5
baseline pattern.

The report gives numbers, not verdicts (Mini-ADR HX-E5): per-case
A/B matrix, pass-rate delta, and the McNemar discordant counts
(``a_only`` = A passed where B failed, ``b_only`` = the reverse).
At offline sample sizes a human reads those counts; the harness does
not bake in a significance threshold to declare a winner.

Variants are manifest YAML files (an operator exports a revision
snapshot via ``GET /v1/agents/{name}/{version}/revisions/{n}`` or the
History tab). Real-LLM runs are manual (``--provider env`` reads
``EXPERT_WORK_EVAL_LLM_*``, the run_longmem convention); CI exercises the
harness itself through the deterministic mock provider only.

Run::

    python tools/eval/prompt_ab.py \\
        --eval-set tools/eval/datasets/example.yaml \\
        --spec-a variant_a.yaml --spec-b variant_b.yaml \\
        --provider env --llm-model qwen3.5-plus
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:  # script-style execution from repo root
    sys.path.insert(0, str(_HERE))

from expert_work_eval import (  # type: ignore[import-not-found]  # noqa: E402
    CompletionFn,
    EvalReport,
    EvalSet,
    load_eval_set,
    mock_provider,
    run_eval,
)


@dataclass(frozen=True)
class Variant:
    """One side of the comparison: a label + its system prompt."""

    label: str
    system_prompt: str


@dataclass(frozen=True)
class CasePair:
    """One case's verdict under both variants."""

    case_id: str
    a_passed: bool
    b_passed: bool


@dataclass(frozen=True)
class AbReport:
    """Paired comparison of two variants over one eval set."""

    eval_set: str
    variant_a: str
    variant_b: str
    pairs: tuple[CasePair, ...]

    @property
    def a_pass_rate(self) -> float:
        return sum(1 for p in self.pairs if p.a_passed) / len(self.pairs) if self.pairs else 0.0

    @property
    def b_pass_rate(self) -> float:
        return sum(1 for p in self.pairs if p.b_passed) / len(self.pairs) if self.pairs else 0.0

    @property
    def a_only(self) -> int:
        """Discordant count: A passed where B failed (McNemar ``b``)."""
        return sum(1 for p in self.pairs if p.a_passed and not p.b_passed)

    @property
    def b_only(self) -> int:
        """Discordant count: B passed where A failed (McNemar ``c``)."""
        return sum(1 for p in self.pairs if p.b_passed and not p.a_passed)

    def to_json(self) -> dict[str, Any]:
        return {
            "eval_set": self.eval_set,
            "variant_a": self.variant_a,
            "variant_b": self.variant_b,
            "n": len(self.pairs),
            "a_pass_rate": round(self.a_pass_rate, 4),
            "b_pass_rate": round(self.b_pass_rate, 4),
            "delta": round(self.b_pass_rate - self.a_pass_rate, 4),
            "discordant": {"a_only": self.a_only, "b_only": self.b_only},
            "cases": [{"id": p.case_id, "a": p.a_passed, "b": p.b_passed} for p in self.pairs],
        }


def load_variant(path: Path) -> Variant:
    """Extract the ``system_prompt.template`` from a manifest YAML file.

    Accepts both a full manifest document and a bare revision-snapshot
    ``spec`` payload (the History API returns the manifest under
    ``record.spec``); fails with a pointed message otherwise.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"variant file is not a YAML mapping: {path}"
        raise ValueError(msg)
    body = raw.get("spec", raw)
    if isinstance(body, dict) and "spec" in body and "system_prompt" not in body:
        body = body["spec"]  # revision snapshot: {record: {spec: {spec: ...}}} pre-peeled
    prompt = body.get("system_prompt", {}).get("template") if isinstance(body, dict) else None
    if not isinstance(prompt, str) or not prompt.strip():
        msg = (
            f"no spec.system_prompt.template found in {path} — pass a manifest "
            "YAML or a revision snapshot's spec document"
        )
        raise ValueError(msg)
    return Variant(label=path.stem, system_prompt=prompt)


def pair_reports(eval_set: str, a: tuple[str, EvalReport], b: tuple[str, EvalReport]) -> AbReport:
    """Zip two single-variant reports into the paired comparison."""
    b_by_id = {r.case_id: r for r in b[1].results}
    pairs = tuple(
        CasePair(
            case_id=r.case_id,
            a_passed=r.passed,
            b_passed=b_by_id[r.case_id].passed if r.case_id in b_by_id else False,
        )
        for r in a[1].results
    )
    return AbReport(eval_set=eval_set, variant_a=a[0], variant_b=b[0], pairs=pairs)


# A factory so each variant gets a CompletionFn carrying its own
# system prompt; tests inject behaviour-diverging fakes through it.
ProviderFactory = Any  # Callable[[Variant], CompletionFn] — kept loose for test fakes


async def run_ab(
    eval_set: EvalSet,
    variant_a: Variant,
    variant_b: Variant,
    provider_for: ProviderFactory,
) -> AbReport:
    """Run both variants over ``eval_set`` and pair the verdicts."""
    report_a = await run_eval(eval_set, provider_for(variant_a))
    report_b = await run_eval(eval_set, provider_for(variant_b))
    return pair_reports(eval_set.name, (variant_a.label, report_a), (variant_b.label, report_b))


def _env_provider_factory(model: str, max_tokens: int) -> Any:
    """Real-LLM factory from ``EXPERT_WORK_EVAL_LLM_*`` env (run_longmem convention)."""
    api_key = os.environ.get("EXPERT_WORK_EVAL_LLM_API_KEY")
    if not api_key:
        raise SystemExit("--provider env needs EXPERT_WORK_EVAL_LLM_API_KEY")
    from langchain_core.messages import HumanMessage, SystemMessage
    from longmem.openai_client import (  # type: ignore[import-not-found]
        DASHSCOPE_COMPAT_BASE_URL,
        OpenAICompatCaller,
    )

    base_url = os.environ.get("EXPERT_WORK_EVAL_LLM_BASE_URL", DASHSCOPE_COMPAT_BASE_URL)
    caller = OpenAICompatCaller(
        api_key=api_key, model=model, base_url=base_url, max_tokens=max_tokens
    )

    def factory(variant: Variant) -> CompletionFn:
        async def complete(prompt: str) -> str:
            reply = await caller(
                messages=[SystemMessage(variant.system_prompt), HumanMessage(prompt)],
                tools=[],
            )
            content = reply.content
            return content if isinstance(content, str) else str(content)

        return complete

    return factory


def _mock_provider_factory(eval_set: EvalSet) -> Any:
    """CI factory — both variants get the deterministic mock provider.

    Exercises the full pairing pipeline without credentials; a real
    behavioural difference needs a real model (run manually).
    """

    def factory(variant: Variant) -> CompletionFn:
        del variant
        return mock_provider(eval_set)

    return factory


def format_ab_report(report: AbReport) -> str:
    lines = [
        f"eval set: {report.eval_set}  n={len(report.pairs)}",
        f"  A = {report.variant_a}: {report.a_pass_rate:.1%}",
        f"  B = {report.variant_b}: {report.b_pass_rate:.1%}",
        f"  delta (B - A): {report.b_pass_rate - report.a_pass_rate:+.1%}",
        f"  discordant: A-only {report.a_only}, B-only {report.b_only}",
        "  cases:",
    ]
    for p in report.pairs:
        mark_a = "P" if p.a_passed else "F"
        mark_b = "P" if p.b_passed else "F"
        flag = "" if p.a_passed == p.b_passed else "   <- discordant"
        lines.append(f"    [{mark_a}|{mark_b}] {p.case_id}{flag}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline prompt A/B over one eval set.")
    parser.add_argument("--eval-set", type=Path, required=True)
    parser.add_argument("--spec-a", type=Path, required=True, help="variant A manifest YAML")
    parser.add_argument("--spec-b", type=Path, required=True, help="variant B manifest YAML")
    parser.add_argument("--provider", choices=("mock", "env"), default="mock")
    parser.add_argument("--llm-model", default="qwen3.5-plus")
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="JSON artifact path (default eval-out/prompt_ab_<eval-set>.json)",
    )
    args = parser.parse_args(argv)

    eval_set = load_eval_set(args.eval_set)
    variant_a = load_variant(args.spec_a)
    variant_b = load_variant(args.spec_b)
    factory = (
        _env_provider_factory(args.llm_model, max(1, args.max_tokens))
        if args.provider == "env"
        else _mock_provider_factory(eval_set)
    )
    report = asyncio.run(run_ab(eval_set, variant_a, variant_b, factory))

    out = args.out or Path("eval-out") / f"prompt_ab_{eval_set.name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report.to_json(), indent=2, ensure_ascii=False), encoding="utf-8")

    sys.stdout.write(format_ab_report(report) + "\n")
    sys.stdout.write(f"artifact: {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
