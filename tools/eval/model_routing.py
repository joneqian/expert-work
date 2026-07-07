"""J.11 model routing eval — Stream J.13a (M0 baseline).

Two scenarios share the dataset:

* ``resolution`` — given a manifest ``RoutingSpec`` + the agent's default
  model, the matching step-class router resolves to the expected
  ``ModelSpec``. Tests the J.11 step→model decision logic in pure form
  (no provider construction needed; mirrors
  :func:`orchestrator.agent_factory.build_step_routers` resolution).
* ``fallback`` — given a synthetic provider chain that raises a scripted
  sequence of :class:`LLMError` subclasses, :class:`LLMRouter` exercises
  the E.11 fallback semantics (Stream J.11 / E.11 share the router).
  Verifies which provider's response wins, or which
  :class:`AllProvidersExhaustedError` is finally raised.

Per Mini-ADR J-37, J.11 metric is ``pass-rate`` (deterministic — no
LLM-judge needed). Threshold ≥ 0.95 (see § 18.3).
"""

from __future__ import annotations

import sys as _sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from pathlib import Path as _Path
from typing import Any, Literal, cast

import yaml
from langchain_core.messages import AIMessage, BaseMessage

from expert_work.runtime.middleware import (
    LLMClientError,
    LLMNetworkError,
    LLMRateLimitError,
    LLMServerError,
)
from orchestrator.llm.router import (
    AllProvidersExhaustedError,
    LLMProvider,
    LLMRouter,
    ProviderHandle,
)
from orchestrator.tools.registry import ToolSpec

_EVAL_DIR = _Path(__file__).resolve().parent
if str(_EVAL_DIR) not in _sys.path:
    _sys.path.insert(0, str(_EVAL_DIR))

from _capability import (  # type: ignore[import-not-found]  # noqa: E402
    CapabilityCaseResult,
    CapabilityReport,
    JudgeCompletionFn,
)

CAPABILITY = "J.11_model_routing"
METRIC_TYPE = "pass-rate"
THRESHOLD = {"pass_rate": 0.95}


@dataclass(frozen=True)
class ResolutionCase:
    """One step-class resolution case.

    The decision under test is "given these rules + this default + this
    step class, which model key wins?". We carry the *keys* (provider:
    model strings) rather than full :class:`ModelSpec` objects so cases
    stay pure YAML — the resolution helper only needs identity.
    """

    case_id: str
    routing_rules: tuple[tuple[str, str], ...]  # (when, model_key)
    default_model_key: str
    step_class: str
    expected_model_key: str


@dataclass(frozen=True)
class FallbackProviderScript:
    """One provider's scripted behaviour in a fallback chain."""

    key: str
    #: ``"success"`` returns an AIMessage; otherwise the error type that
    #: ``complete()`` raises (mapped via :data:`_ERROR_BY_NAME`).
    action: Literal[
        "success",
        "server_error",
        "client_error",
        "rate_limit",
        "network_error",
    ]


@dataclass(frozen=True)
class FallbackCase:
    """One fallback-chain case.

    ``expected_outcome`` is either ``"success"`` (with
    ``expected_winner_key`` naming which provider's response wins) or
    ``"exhausted"`` (the chain runs out and raises
    :class:`AllProvidersExhaustedError`) or ``"client_error"``
    (LLMClientError re-raises immediately, no fallback).
    """

    case_id: str
    providers: tuple[FallbackProviderScript, ...]
    expected_outcome: Literal["success", "exhausted", "client_error"]
    expected_winner_key: str = ""


def _resolve_step_model_key(
    *,
    rules: Sequence[tuple[str, str]],
    default_model_key: str,
    step_class: str,
) -> str:
    """Mirror ``build_step_routers`` decision logic — pure form.

    For each rule in declaration order, the first ``when == step_class``
    match wins. A class with no matching rule falls back to the agent
    default. This is the only routing decision that matters for J.11; the
    actual provider construction (``build_llm_router``) is tested by E.11
    integration tests, not here.
    """
    for when, model_key in rules:
        if when == step_class:
            return model_key
    return default_model_key


_ERROR_BY_NAME: dict[str, type[BaseException]] = {
    "server_error": LLMServerError,
    "client_error": LLMClientError,
    "rate_limit": LLMRateLimitError,
    "network_error": LLMNetworkError,
}


@dataclass
class _ScriptedProvider:
    """Mock :class:`LLMProvider` — raises a scripted error or returns a tag."""

    key: str
    action: str

    async def complete(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
    ) -> AIMessage:
        if self.action == "success":
            return AIMessage(content=f"ok:{self.key}")
        err_cls = _ERROR_BY_NAME[self.action]
        raise err_cls(f"scripted {self.action} from {self.key}")


def _build_router(providers: Sequence[FallbackProviderScript]) -> LLMRouter:
    handles = [
        ProviderHandle(
            provider=cast(LLMProvider, _ScriptedProvider(key=p.key, action=p.action)),
            key=p.key,
        )
        for p in providers
    ]
    return LLMRouter(providers=handles)


async def _run_resolution_case(case: ResolutionCase) -> CapabilityCaseResult:
    """Deterministic pure-function check."""
    got = _resolve_step_model_key(
        rules=case.routing_rules,
        default_model_key=case.default_model_key,
        step_class=case.step_class,
    )
    passed = got == case.expected_model_key
    notes: tuple[str, ...] = ()
    if not passed:
        notes = (f"expected_model_key={case.expected_model_key!r} got={got!r}",)
    return CapabilityCaseResult(case_id=case.case_id, passed=passed, notes=notes)


async def _run_fallback_case(case: FallbackCase) -> CapabilityCaseResult:
    """Drive the real :class:`LLMRouter` against a scripted provider chain."""
    router = _build_router(case.providers)
    notes: tuple[str, ...] = ()
    try:
        response = await router(messages=(), tools=())
    except AllProvidersExhaustedError as exc:
        passed = case.expected_outcome == "exhausted"
        if not passed:
            notes = (f"unexpected exhaustion: last={type(exc.last_exc).__name__}",)
        return CapabilityCaseResult(case_id=case.case_id, passed=passed, notes=notes)
    except LLMClientError as exc:
        passed = case.expected_outcome == "client_error"
        if not passed:
            notes = (f"unexpected LLMClientError: {exc}",)
        return CapabilityCaseResult(case_id=case.case_id, passed=passed, notes=notes)

    if case.expected_outcome != "success":
        return CapabilityCaseResult(
            case_id=case.case_id,
            passed=False,
            notes=(f"expected_outcome={case.expected_outcome!r} but call succeeded",),
        )
    got_winner = str(response.content).removeprefix("ok:")
    passed = got_winner == case.expected_winner_key
    if not passed:
        notes = (f"expected_winner_key={case.expected_winner_key!r} got={got_winner!r}",)
    return CapabilityCaseResult(case_id=case.case_id, passed=passed, notes=notes)


_AnyCase = ResolutionCase | FallbackCase


async def evaluate_set(
    cases: Sequence[_AnyCase],
    *,
    judge: JudgeCompletionFn | None = None,
    rerun_count: int = 3,
) -> CapabilityReport:
    """Run every case; aggregate into a :class:`CapabilityReport`.

    Mini-ADR J-39 ``rerun_count`` and the optional ``judge`` are kept
    in the signature for protocol uniformity across capability modules
    (so :func:`run_baseline.run_baseline` can call every module the same
    way), but J.11 ignores both — its metric is deterministic.
    """
    per_case: list[CapabilityCaseResult] = []
    for case in cases:
        if isinstance(case, ResolutionCase):
            per_case.append(await _run_resolution_case(case))
        else:
            per_case.append(await _run_fallback_case(case))
    sample = len(per_case)
    pass_rate = sum(1 for r in per_case if r.passed) / sample if sample else 0.0
    status = "PASS" if pass_rate >= THRESHOLD["pass_rate"] else "FAIL"
    return CapabilityReport(
        capability=CAPABILITY,
        metric_type=METRIC_TYPE,
        sample_size=sample,
        threshold=THRESHOLD,
        aggregate_score={"pass_rate": pass_rate},
        status=cast(Any, status),
        per_case=tuple(per_case),
    )


def load_cases(path: Path) -> list[_AnyCase]:
    """Parse the YAML dataset; dispatch on ``scenario``."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    out: list[_AnyCase] = []
    for entry in raw.get("cases", []):
        scenario = entry.get("scenario", "resolution")
        if scenario == "resolution":
            out.append(_parse_resolution_case(entry))
        elif scenario == "fallback":
            out.append(_parse_fallback_case(entry))
        else:
            msg = f"unknown scenario {scenario!r} in case {entry.get('id')!r}"
            raise ValueError(msg)
    return out


def _parse_resolution_case(entry: dict[str, Any]) -> ResolutionCase:
    return ResolutionCase(
        case_id=str(entry["id"]),
        routing_rules=tuple((str(r["when"]), str(r["model_key"])) for r in entry["routing_rules"]),
        default_model_key=str(entry["default_model_key"]),
        step_class=str(entry["step_class"]),
        expected_model_key=str(entry["expected_model_key"]),
    )


def _parse_fallback_case(entry: dict[str, Any]) -> FallbackCase:
    return FallbackCase(
        case_id=str(entry["id"]),
        providers=tuple(
            FallbackProviderScript(key=str(p["key"]), action=str(p["action"]))  # type: ignore[arg-type]
            for p in entry["providers"]
        ),
        expected_outcome=cast(Any, entry["expected_outcome"]),
        expected_winner_key=str(entry.get("expected_winner_key", "")),
    )


__all__ = [
    "CAPABILITY",
    "METRIC_TYPE",
    "THRESHOLD",
    "FallbackCase",
    "FallbackProviderScript",
    "ResolutionCase",
    "evaluate_set",
    "load_cases",
]
