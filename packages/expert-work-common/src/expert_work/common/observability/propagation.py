"""W3C Trace Context propagation helpers — Stream A.8.

Design: subsystems/20-observability § 1 + § 6 ("跨服务调用强制
traceparent header").

Thin facade over ``opentelemetry.propagators.textmap`` so call sites in
Stream B (FastAPI middleware) and Stream E (orchestrator outbound HTTP)
get a stable, mockable surface without importing the OTel propagator
package directly.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping

from opentelemetry import context, trace
from opentelemetry.propagate import extract, inject

# ``traceparent`` is the only header the W3C spec requires — Expert Work does
# not propagate baggage today (defer to M2 when tenant routing relies on
# it). Exporting the constant lets call sites assert on the exact header
# name without typo risk.
TRACEPARENT_HEADER = "traceparent"
TRACESTATE_HEADER = "tracestate"


def extract_context(headers: Mapping[str, str]) -> context.Context:
    """Pull a W3C trace context out of incoming HTTP headers.

    Returns the empty context when no ``traceparent`` is present — the
    caller (typically an ASGI middleware) then starts a fresh root span.
    """
    return extract(carrier=headers)


def inject_context(headers: MutableMapping[str, str]) -> None:
    """Write the current OTel context into ``headers`` in place.

    Use for every outbound HTTP / gRPC call: Control Plane → Orchestrator
    → Sandbox → MCP gateway → LLM gateway. The CI integration test in
    Stream B will assert that the header round-trips.
    """
    inject(carrier=headers)


def current_trace_id_hex() -> str | None:
    """Return the active span's trace_id as a lowercase 32-hex string.

    ``None`` when no span is active or the span is a no-op (a common
    state in tests that import :mod:`expert_work.common.observability`
    without calling :func:`init_tracing`).

    This is the field that :class:`ExpertWorkJsonFormatter` populates as
    ``trace_id`` once OTel is initialized — replaces the contextvar
    fallback from Stream A.7.
    """
    span = trace.get_current_span()
    span_context = span.get_span_context()
    if not span_context.is_valid:
        return None
    return format(span_context.trace_id, "032x")


def current_span_id_hex() -> str | None:
    """Return the active span's span_id as a lowercase 16-hex string.

    Same ``None`` semantics as :func:`current_trace_id_hex`. This is the
    field the formatter writes as ``span_id`` in the log JSON.
    """
    span = trace.get_current_span()
    span_context = span.get_span_context()
    if not span_context.is_valid:
        return None
    return format(span_context.span_id, "016x")
