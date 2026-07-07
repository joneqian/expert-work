"""Expert Work eval harness — Stream G.4.

A lightweight, Python-native prompt-evaluation harness (Mini-ADR G-3 —
no promptfoo, no Node toolchain). An *eval set* is a YAML file of cases;
each case pairs a ``prompt`` with machine-checkable ``assertions``.
:func:`run_eval` drives the cases through a pluggable
``complete(prompt) -> str`` provider and reports pass / fail per case.

M0 ships a deterministic mock provider (:func:`mock_provider` — returns
each case's ``mock_response``) so the harness runs end to end in CI with
no LLM credentials. A real LLM provider is a documented extension point:
supply any ``async def complete(prompt: str) -> str`` — see README.

Run:  ``python tools/eval/expert_work_eval.py tools/eval/datasets/example.yaml``
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

_ASSERTION_TYPES = ("contains", "not_contains", "regex", "equals")

CompletionFn = Callable[[str], Awaitable[str]]


# --------------------------------------------------------------------------
# eval-set model
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Assertion:
    """One machine-checkable expectation on a case's output."""

    type: str
    value: str

    def __post_init__(self) -> None:
        if self.type not in _ASSERTION_TYPES:
            msg = f"unknown assertion type {self.type!r}; expected one of {_ASSERTION_TYPES}"
            raise ValueError(msg)


@dataclass(frozen=True)
class EvalCase:
    """One prompt + the assertions its output must satisfy."""

    id: str
    prompt: str
    assertions: tuple[Assertion, ...]
    #: Deterministic stand-in output for the mock provider (M0 / CI).
    mock_response: str = ""


@dataclass(frozen=True)
class EvalSet:
    """A named collection of eval cases, loaded from one YAML file."""

    name: str
    cases: tuple[EvalCase, ...]


def load_eval_set(path: Path) -> EvalSet:
    """Parse an eval-set YAML file into an :class:`EvalSet`."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"eval set must be a YAML mapping: {path}"
        raise ValueError(msg)
    cases = tuple(
        EvalCase(
            id=str(case["id"]),
            prompt=str(case["prompt"]),
            mock_response=str(case.get("mock_response", "")),
            assertions=tuple(
                Assertion(type=str(a["type"]), value=str(a["value"]))
                for a in case.get("assertions", [])
            ),
        )
        for case in raw.get("cases", [])
    )
    return EvalSet(name=str(raw.get("name", path.stem)), cases=cases)


# --------------------------------------------------------------------------
# assertion evaluation
# --------------------------------------------------------------------------


def evaluate_assertion(assertion: Assertion, output: str) -> bool:
    """Return whether ``output`` satisfies ``assertion``."""
    if assertion.type == "contains":
        return assertion.value in output
    if assertion.type == "not_contains":
        return assertion.value not in output
    if assertion.type == "equals":
        return assertion.value == output
    # ``regex`` — the only remaining type (Assertion.__post_init__ guards).
    return re.search(assertion.value, output) is not None


# --------------------------------------------------------------------------
# runner
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CaseResult:
    """Outcome of one evaluated case."""

    case_id: str
    passed: bool
    #: Human-readable descriptions of the assertions that failed.
    failures: tuple[str, ...]


@dataclass(frozen=True)
class EvalReport:
    """The outcome of one :func:`run_eval` pass."""

    name: str
    results: tuple[CaseResult, ...]

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def ok(self) -> bool:
        """True when every case passed."""
        return all(r.passed for r in self.results)


async def run_eval(eval_set: EvalSet, complete: CompletionFn) -> EvalReport:
    """Run every case in ``eval_set`` through ``complete``; return a report."""
    results: list[CaseResult] = []
    for case in eval_set.cases:
        output = await complete(case.prompt)
        failures = tuple(
            f"{a.type}({a.value!r})" for a in case.assertions if not evaluate_assertion(a, output)
        )
        results.append(CaseResult(case_id=case.id, passed=not failures, failures=failures))
    return EvalReport(name=eval_set.name, results=tuple(results))


def mock_provider(eval_set: EvalSet) -> CompletionFn:
    """A deterministic provider — returns each case's ``mock_response``."""
    by_prompt = {case.prompt: case.mock_response for case in eval_set.cases}

    async def _complete(prompt: str) -> str:
        return by_prompt.get(prompt, "")

    return _complete


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def format_report(report: EvalReport) -> str:
    """Render an :class:`EvalReport` as plain text."""
    lines = [f"eval set: {report.name}  ({report.passed}/{report.total} passed)"]
    for result in report.results:
        lines.append(f"  [{'PASS' if result.passed else 'FAIL'}] {result.case_id}")
        lines.extend(f"         failed: {failure}" for failure in result.failures)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint — runs an eval set with the mock provider."""
    parser = argparse.ArgumentParser(description="Run a expert_work eval set (mock provider).")
    parser.add_argument("eval_set", type=Path, help="path to an eval-set YAML file")
    args = parser.parse_args(argv)

    eval_set = load_eval_set(args.eval_set)
    report = asyncio.run(run_eval(eval_set, mock_provider(eval_set)))
    sys.stdout.write(format_report(report) + "\n")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
