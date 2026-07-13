"""Langfuse trace normalizer — Batch 4b Task 1 (spec §2.3).

The debug console's "precise" trace view wants a stable, human-labelled
DTO instead of the raw Langfuse observation tree. ``normalize_trace`` is a
pure function — no IO, no network, no upstream dependency — so it can be
unit-tested against lightweight stubs and reused unchanged once the
control-plane wires an actual Langfuse client in a later task.

Normalization does three things to the raw ``observations`` list:

1. **Classify** each observation into a ``kind``/``label`` pair a human can
   read (GENERATION → "LLM 调用", ``*.tool_call`` → "工具调用", …).
2. **Merge** the internal ``*.orchestrator.llm_call`` SPAN wrapper into its
   single GENERATION child — that wrapper is an orchestrator implementation
   detail, not something a human debugging a run needs to see as a
   separate row.
3. **Elide** the ``*.http_request`` root — it is the FastAPI entry span,
   not part of the agent's own work — re-parenting its children so the
   remaining tree stays connected.

Real Langfuse SDK objects mix snake_case and camelCase attribute names
across SDK versions (``prompt_tokens`` vs ``promptTokens``); token count
extraction defends against both.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from langfuse.api import NotFoundError

__all__ = ["TraceSpan", "fetch_and_normalize", "normalize_trace"]

_NAME_PREFIX = "expert_work."
_TRUNCATION_SUFFIX = "…(已截断)"


@dataclass(frozen=True)
class TraceSpan:
    """One normalized row in the debug console's trace timeline."""

    id: str
    parent_id: str | None
    kind: str  # "session" | "llm" | "tool" | "span"
    label: str
    detail: str | None
    start_ms: int
    latency_ms: int
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    input: str | None
    output: str | None


@dataclass(frozen=True)
class _ParsedObs:
    """Per-observation working state before merge/elision + re-parenting."""

    id: str
    parent_id: str | None
    obs_type: str
    name: str
    kind: str
    label: str
    detail: str | None
    start_ms: int
    latency_ms: int
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    input: str | None
    output: str | None


def normalize_trace(trace: object, *, io_cap: int = 32768) -> dict[str, object]:
    """Normalize a Langfuse ``TraceWithFullDetails`` into a stable DTO.

    Returns ``{"status": "ok", "trace": {...}, "spans": [...]}`` where each
    span dict uses camelCase keys (``parentId``/``startMs``/``latencyMs``/
    ``inputTokens``/``outputTokens``/``costUsd``).
    """
    t: Any = trace
    raw_observations: list[Any] = list(t.observations or [])

    trace_name = str(t.name)
    trace_latency_ms = round((t.latency or 0) * 1000)
    trace_total_cost_usd = t.total_cost or None

    if not raw_observations:
        return {
            "status": "ok",
            "trace": {
                "name": trace_name,
                "latencyMs": trace_latency_ms,
                "totalCostUsd": trace_total_cost_usd,
                "spanCount": 0,
            },
            "spans": [],
        }

    start_times = [o.start_time for o in raw_observations if o.start_time is not None]
    trace_start = min(start_times) if start_times else None

    parsed_by_id: dict[str, _ParsedObs] = {}
    children_by_parent: dict[str | None, list[str]] = {}
    for o in raw_observations:
        parsed = _parse_observation(o, trace_start=trace_start, io_cap=io_cap)
        parsed_by_id[parsed.id] = parsed
        children_by_parent.setdefault(parsed.parent_id, []).append(parsed.id)

    omitted, latency_override = _resolve_omissions(parsed_by_id, children_by_parent)

    def resolve_parent(raw_parent_id: str | None) -> str | None:
        current = raw_parent_id
        visited: set[str] = set()
        # T1-reviewer follow-up: real Langfuse data flows through here now —
        # guard against a corrupted upstream parent cycle wedging the event loop.
        while current is not None and current in omitted:
            if current in visited:
                return None
            visited.add(current)
            current = omitted[current]
        return current

    spans = [
        TraceSpan(
            id=parsed.id,
            parent_id=resolve_parent(parsed.parent_id),
            kind=parsed.kind,
            label=parsed.label,
            detail=parsed.detail,
            start_ms=parsed.start_ms,
            latency_ms=latency_override.get(parsed.id, parsed.latency_ms),
            model=parsed.model,
            input_tokens=parsed.input_tokens,
            output_tokens=parsed.output_tokens,
            cost_usd=parsed.cost_usd,
            input=parsed.input,
            output=parsed.output,
        )
        for parsed in parsed_by_id.values()
        if parsed.id not in omitted
    ]

    return {
        "status": "ok",
        "trace": {
            "name": trace_name,
            "latencyMs": trace_latency_ms,
            "totalCostUsd": trace_total_cost_usd,
            "spanCount": len(spans),
        },
        "spans": [_span_as_dict(s) for s in spans],
    }


def fetch_and_normalize(client: Any, trace_id: str, *, io_cap: int = 32768) -> dict[str, object]:
    """Fetch one trace from Langfuse and normalize it — Batch 4b Task 2.

    Unlike :func:`normalize_trace` this DOES touch the network (via the
    injected read-only Langfuse SDK client) — the try/except below is a
    deliberate fail-soft degrade boundary, not a swallowed error: every
    branch returns an explicit ``status`` so the debug console can render
    "tracing off" vs "not ingested yet" vs a real trace, and a Langfuse
    outage never turns into a 500 for the caller.
    """
    if client is None:
        return {"status": "unavailable"}
    try:
        trace = client.api.trace.get(trace_id)
    except NotFoundError:
        # Genuinely unknown, or Langfuse's async ingestion pipeline just
        # hasn't landed it yet — distinct from tracing being disabled.
        return {"status": "not_ready"}
    except Exception:
        return {"status": "unavailable"}
    # Trace exists but Langfuse's aggregation hasn't finished yet (latency is
    # populated once the trace closes out) — tell the caller to retry rather
    # than rendering a bogus zero-latency row.
    if getattr(trace, "latency", None) is None:
        return {"status": "not_ready"}
    try:
        normalized = normalize_trace(trace, io_cap=io_cap)
    except Exception:
        # Belt-and-suspenders: no code path from a successful trace.get()
        # should ever reach an uncaught exception (硬约束「降级永不 500」).
        return {"status": "unavailable"}
    # Langfuse ingestion is NOT atomic under load: a multi-span run's child
    # observations can land in Langfuse BEFORE their session-root parent does
    # (the trace root closes → ``latency`` populated → passes the None-latency
    # guard above, yet the observation set is still partial). Two shapes hit
    # the waterfall as a bare time-axis with no bars:
    #   * zero renderable spans, or
    #   * spans present but NONE is a root / some reference a parent that
    #     hasn't been ingested (``parentId`` dangles outside the set) — the
    #     frontend's preorder tree walk starts from the roots, so an
    #     orphaned-only set renders nothing.
    # Either way the trace isn't fully ingested — degrade to ``not_ready`` so
    # the console shows the refresh card (and auto-polls) until it settles,
    # instead of a confusing empty ruler.
    spans = normalized.get("spans")
    if not isinstance(spans, list) or not _is_renderable_tree(spans):
        return {"status": "not_ready"}
    return normalized


def _is_renderable_tree(spans: list[Any]) -> bool:
    """A normalized span set renders iff it is non-empty, has at least one root
    (``parentId is None``), and every span's ``parentId`` resolves within the
    set (no dangling parent from a not-yet-ingested observation)."""
    if not spans:
        return False
    ids = {s.get("id") for s in spans}
    has_root = any(s.get("parentId") is None for s in spans)
    fully_connected = all(s.get("parentId") is None or s.get("parentId") in ids for s in spans)
    return has_root and fully_connected


def _parse_observation(o: Any, *, trace_start: Any, io_cap: int) -> _ParsedObs:
    obs_id = str(o.id)
    obs_type = str(o.type)
    name = str(o.name)
    parent_id = _clean_str(getattr(o, "parent_observation_id", None))
    if o.start_time is None or trace_start is None:
        start_ms = 0
    else:
        start_ms = round((o.start_time - trace_start).total_seconds() * 1000)
    latency_ms = round((o.latency or 0) * 1000)
    kind, label = _classify(obs_type, name)
    detail = _tool_detail(o) if kind == "tool" else None
    return _ParsedObs(
        id=obs_id,
        parent_id=parent_id,
        obs_type=obs_type,
        name=name,
        kind=kind,
        label=label,
        detail=detail,
        start_ms=start_ms,
        latency_ms=latency_ms,
        model=_model(o),
        input_tokens=_token_count(o, "prompt_tokens", "promptTokens"),
        output_tokens=_token_count(o, "completion_tokens", "completionTokens"),
        cost_usd=_cost_usd(o),
        input=_render_io(getattr(o, "input", None), io_cap),
        output=_render_io(getattr(o, "output", None), io_cap),
    )


def _resolve_omissions(
    parsed_by_id: dict[str, _ParsedObs],
    children_by_parent: dict[str | None, list[str]],
) -> tuple[dict[str, str | None], dict[str, int]]:
    """Find nodes to drop from the output tree + their reparent target.

    Two independent omission rules (spec §2.3 步骤 3-4):

    * A ``*.orchestrator.llm_call`` SPAN with exactly one GENERATION child
      is an orchestrator wrapper — omit the SPAN, and let the GENERATION
      inherit the SPAN's (more complete) latency.
    * The ROOT ``*.http_request`` observation (the FastAPI entry span, i.e.
      ``parent_id is None``) is omitted; its children re-parent past it. A
      non-root span whose name merely contains ``.http_request`` is kept.

    Returns ``(omitted, latency_override)`` where ``omitted`` maps an
    omitted node's id to the raw parent id its children should redirect
    through (chained by the caller until a surviving ancestor or ``None``).
    """
    omitted: dict[str, str | None] = {}
    latency_override: dict[str, int] = {}

    for parsed in parsed_by_id.values():
        if parsed.obs_type != "SPAN" or not parsed.name.endswith(".orchestrator.llm_call"):
            continue
        child_ids = children_by_parent.get(parsed.id, [])
        if len(child_ids) != 1:
            continue
        child = parsed_by_id[child_ids[0]]
        if child.obs_type != "GENERATION":
            continue
        omitted[parsed.id] = parsed.parent_id
        latency_override[child.id] = parsed.latency_ms

    for parsed in parsed_by_id.values():
        # Root-only: elide the FastAPI entry span (parent_id is None), NOT any
        # legitimately-named non-root span that happens to contain the substring.
        if parsed.parent_id is None and ".http_request" in parsed.name:
            omitted[parsed.id] = parsed.parent_id

    return omitted, latency_override


def _classify(obs_type: str, name: str) -> tuple[str, str]:
    """Map an observation's raw ``type``/``name`` to a human kind + label."""
    if obs_type == "GENERATION":
        return "llm", "LLM 调用"
    if ".tool_call" in name:
        return "tool", "工具调用"
    if ".session.run" in name:
        return "session", "会话运行"
    return "span", _clean_label(name)


def _clean_label(name: str) -> str:
    if name.startswith(_NAME_PREFIX):
        return name[len(_NAME_PREFIX) :]
    return name


def _clean_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _model(o: Any) -> str | None:
    model = getattr(o, "model", None)
    return str(model) if model else None


def _tool_detail(o: Any) -> str | None:
    direct = getattr(o, "tool_name", None) or getattr(o, "toolName", None)
    if direct:
        return str(direct)
    metadata = getattr(o, "metadata", None)
    if isinstance(metadata, dict):
        value = metadata.get("tool_name") or metadata.get("toolName")
        if value:
            return str(value)
    return None


def _token_count(o: Any, snake_name: str, camel_name: str) -> int | None:
    # Langfuse SDK versions mix snake_case + camelCase attribute names —
    # try both, then fall back to the (0-or-missing → None) convention.
    raw = getattr(o, snake_name, None) or getattr(o, camel_name, None)
    if raw is None:
        return None
    count = int(raw)
    return count if count > 0 else None


def _cost_usd(o: Any) -> float | None:
    raw = getattr(o, "calculated_total_cost", None)
    if not raw or raw <= 0:
        return None
    return float(raw)


def _render_io(value: Any, io_cap: int) -> str | None:
    """把 observation 的 input/output 渲染成人类可读文本再截断。

    - 消息 list(``[{role?, content}]``)→ 每条 ``role`` 行 + content(真换行),
      content 为 block-list(``[{type,text}]``)则取 text 拼接。
    - 其它 list/dict → ``json.dumps(ensure_ascii=False, indent=2)``(真换行、非 ASCII 不转义)。
    - str → 原样。None → None。
    """
    if value is None:
        return None
    if isinstance(value, str):
        return _cap(value, io_cap)
    is_message_list = (
        isinstance(value, list)
        and value
        and all(isinstance(m, dict) and "content" in m for m in value)
    )
    if is_message_list:
        parts: list[str] = []
        for m in value:
            role = str(m.get("role", "")) or "message"
            content = m.get("content")
            if isinstance(content, list):
                text = "".join(
                    b["text"]
                    for b in content
                    if isinstance(b, dict) and isinstance(b.get("text"), str)
                )
            else:
                text = (
                    content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
                )
            parts.append(f"[{role}]\n{text}")
        return _cap("\n\n".join(parts), io_cap)
    try:
        return _cap(json.dumps(value, ensure_ascii=False, indent=2), io_cap)
    except (TypeError, ValueError):
        return _cap(str(value), io_cap)


def _cap(value: Any, io_cap: int) -> str | None:
    if value is None:
        return None
    text = str(value)
    if len(text) > io_cap:
        return text[:io_cap] + _TRUNCATION_SUFFIX
    return text


def _span_as_dict(span: TraceSpan) -> dict[str, object]:
    return {
        "id": span.id,
        "parentId": span.parent_id,
        "kind": span.kind,
        "label": span.label,
        "detail": span.detail,
        "startMs": span.start_ms,
        "latencyMs": span.latency_ms,
        "model": span.model,
        "inputTokens": span.input_tokens,
        "outputTokens": span.output_tokens,
        "costUsd": span.cost_usd,
        "input": span.input,
        "output": span.output,
    }
