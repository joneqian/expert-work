"""Run-level transient-failure retry — Stream HX-3 (STREAM-HX-DESIGN § 4).

A run whose graph execution dies on a *transient* infrastructure error
(today: the LLM fallback chain exhausting on the retryable ``LLMError``
family) gets one in-worker retry from its committed checkpoint instead
of going straight to ``RunStatus.ERROR``. Three pieces live here; the
retry loop itself is in :func:`orchestrator.sse.run_agent`:

* **Classification** (Mini-ADR HX-C1): a type registry, no text
  sniffing — the router already did the 4xx/5xx split when it decided
  whether to fall back. Unknown exceptions stay permanent: retrying a
  deterministic bug has no expected benefit.
* **Replay-safety guard** (Mini-ADR HX-C2): checkpoints commit per
  super-step, so committed history never re-executes on a continuation.
  The only replay window is the *failed* (uncommitted) step, and what
  it will re-run is fully determined by the checkpoint tail — a trailing
  ``AIMessage`` with unanswered ``tool_calls`` means exactly that batch
  re-dispatches. The guard applies the CM-B5 capability rule to that
  batch (every call ``read_only`` or ``idempotent``) and is
  **fail-closed**: repeating a side effect is beyond the fail-open
  axiom's "cost more tokens" ceiling.
* **Config** (axiom ③ — defensive parsing): env toggles clamp / fall
  back to defaults, never raise.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from expert_work.common.observability import expert_work_counter
from orchestrator.llm.router import AllProvidersExhaustedError

logger = logging.getLogger(__name__)

#: Exception types eligible for a run-level retry (Mini-ADR HX-C1).
#: ``AllProvidersExhaustedError`` is raised only after the fallback
#: chain exhausted on the transient ``LLMError`` family — a 4xx
#: ``LLMClientError`` re-raises immediately and never reaches it.
#: Extension = add a type here; the classifier does not change.
TRANSIENT_RUN_ERRORS: tuple[type[BaseException], ...] = (AllProvidersExhaustedError,)

#: At most one retry per run (§ 4.2-③). Two consecutive failures are
#: unlikely to be independent transient blips; multi-retry backoff
#: policy would be a separate feature surface.
MAX_RUN_RETRIES = 1

_ENABLED_ENV = "EXPERT_WORK_RUN_TRANSIENT_RETRY"
_BACKOFF_ENV = "EXPERT_WORK_RUN_RETRY_BACKOFF_S"
_DEFAULT_BACKOFF_S = 10.0
_BACKOFF_MIN_S = 1.0
_BACKOFF_MAX_S = 120.0
_FALSEY = frozenset({"0", "false", "no", "off"})

#: ``recovered`` — the retried run reached SUCCESS / PAUSED.
#: ``failed_again`` — the second attempt also died (any failure class).
#: A guard rejection emits no sample: the run takes the unchanged ERROR
#: path, already observable via ``agent_run.error``.
run_retry_total = expert_work_counter(
    "expert_work_orchestrator_run_retry_total",
    "Run-level transient retries by final outcome (Stream HX-3).",
    ("outcome",),
)


def retry_enabled() -> bool:
    """``EXPERT_WORK_RUN_TRANSIENT_RETRY`` — default on; explicit falsey disables."""
    raw = os.environ.get(_ENABLED_ENV)
    if raw is None:
        return True
    return raw.strip().lower() not in _FALSEY


def retry_backoff_s() -> float:
    """``EXPERT_WORK_RUN_RETRY_BACKOFF_S`` clamped to [1, 120]; bad parse → default."""
    raw = os.environ.get(_BACKOFF_ENV)
    if raw is None:
        return _DEFAULT_BACKOFF_S
    try:
        value = float(raw)
    except ValueError:
        logger.warning("run_retry.invalid_backoff value=%r — using default", raw)
        return _DEFAULT_BACKOFF_S
    return min(max(value, _BACKOFF_MIN_S), _BACKOFF_MAX_S)


def is_transient_run_error(exc: BaseException) -> bool:
    """Whether ``exc`` belongs to the run-level transient registry."""
    return isinstance(exc, TRANSIENT_RUN_ERRORS)


async def replay_is_safe(
    graph: Any,
    config: Any,
    tool_replay_safe: Callable[[str], bool] | None,
) -> bool:
    """Mini-ADR HX-C2 — checkpoint-tail replay-safety guard.

    No trailing dangling batch → safe (the continuation replays the
    agent node's pure LLM call, zero side effects). A dangling batch is
    safe only if every call resolves capability-safe through
    ``tool_replay_safe``. Everything else — including a missing resolver
    or a failed state fetch — is **not retried** (fail-closed).
    """
    try:
        snapshot = await graph.aget_state(config)
    except Exception:
        logger.warning("run_retry.guard_state_fetch_failed — not retrying", exc_info=True)
        return False
    dangling = _dangling_tool_calls(getattr(snapshot, "values", None))
    if not dangling:
        return True
    if tool_replay_safe is None:
        logger.warning(
            "run_retry.guard_rejected reason=no_capability_resolver tools=%s",
            sorted(dangling),
        )
        return False
    unsafe = sorted(name for name in dangling if not tool_replay_safe(name))
    if unsafe:
        logger.warning("run_retry.guard_rejected reason=unsafe_dangling_batch tools=%s", unsafe)
        return False
    return True


def _dangling_tool_calls(values: Any) -> list[str]:
    """Tool names in the trailing committed ``AIMessage`` batch that have
    no answering ``ToolMessage`` — i.e. exactly what a checkpoint
    continuation would re-dispatch."""
    if not isinstance(values, Mapping):
        return []
    messages = values.get("messages")
    if not isinstance(messages, Sequence):
        return []
    answered: set[str] = set()
    for msg in reversed(messages):
        if isinstance(msg, ToolMessage):
            if msg.tool_call_id:
                answered.add(msg.tool_call_id)
            continue
        if isinstance(msg, AIMessage):
            calls = msg.tool_calls or []
            # A missing name maps to "" — the resolver rejects it
            # (fail-closed), same as an unknown tool.
            return [str(c.get("name") or "") for c in calls if c.get("id") not in answered]
        # A trailing Human / System message → the tools step committed;
        # nothing dangles.
        return []
    return []
