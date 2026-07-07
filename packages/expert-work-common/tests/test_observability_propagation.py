"""Unit tests for W3C Trace Context propagation helpers."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from expert_work.common.observability import (
    TRACEPARENT_HEADER,
    ExpertWorkComponent,
    current_span_id_hex,
    current_trace_id_hex,
    expert_work_span,
    extract_context,
    init_tracing,
    inject_context,
)


@pytest.fixture
def tracing_setup() -> Iterator[None]:
    provider = init_tracing(
        service_name="prop-test",
        env="test",
        span_processor=SimpleSpanProcessor(InMemorySpanExporter()),
    )
    try:
        yield
    finally:
        provider.shutdown()


def test_inject_writes_traceparent_into_headers(tracing_setup: None) -> None:
    """Outbound calls must carry a ``traceparent`` so downstream services
    see the same trace."""
    with expert_work_span(ExpertWorkComponent.ORCHESTRATOR, "outbound_request"):
        headers: dict[str, str] = {}
        inject_context(headers)
        assert TRACEPARENT_HEADER in headers
        # W3C traceparent is "00-<32-hex>-<16-hex>-<2-hex>".
        parts = headers[TRACEPARENT_HEADER].split("-")
        assert parts[0] == "00"
        assert len(parts[1]) == 32
        assert len(parts[2]) == 16


def test_extract_round_trip_preserves_trace_id(tracing_setup: None) -> None:
    """A child request that receives a ``traceparent`` joins the parent
    trace — its trace_id matches the incoming header."""
    # Phase 1: pretend we're the upstream service emitting a request.
    with expert_work_span(ExpertWorkComponent.CONTROL_PLANE, "request_in"):
        upstream_trace_id = current_trace_id_hex()
        headers: dict[str, str] = {}
        inject_context(headers)

    assert upstream_trace_id is not None

    # Phase 2: a *downstream* service receives those headers. extract()
    # returns an OTel ``Context`` we must attach before starting our
    # local span — otherwise the new span starts a fresh trace.
    from opentelemetry import context as otel_context

    ctx = extract_context(headers)
    token = otel_context.attach(ctx)
    try:
        with expert_work_span(ExpertWorkComponent.ORCHESTRATOR, "downstream_handler"):
            assert current_trace_id_hex() == upstream_trace_id
    finally:
        otel_context.detach(token)


def test_extract_with_no_traceparent_returns_empty_context(tracing_setup: None) -> None:
    """Missing header → empty context; the caller starts a fresh root."""
    ctx = extract_context({})
    # Empty context has no spans attached — when we start a new span the
    # trace_id should be brand new (not zero, not matching anything).
    with expert_work_span(ExpertWorkComponent.CONTROL_PLANE, "fresh_root"):
        trace_id = current_trace_id_hex()
        assert trace_id is not None and trace_id != "0" * 32
    # ctx itself is just a Context; nothing else to assert on its
    # structure (OTel's empty Context is a private type).
    assert ctx is not None


def test_current_ids_return_none_when_no_span_active(tracing_setup: None) -> None:
    assert current_trace_id_hex() is None
    assert current_span_id_hex() is None


def test_current_ids_format() -> None:
    """trace_id is 32 lowercase hex; span_id is 16 lowercase hex."""
    provider = init_tracing(
        service_name="fmt-test",
        env="test",
        span_processor=SimpleSpanProcessor(InMemorySpanExporter()),
    )
    try:
        with expert_work_span(ExpertWorkComponent.ORCHESTRATOR, "evt"):
            t = current_trace_id_hex()
            s = current_span_id_hex()
            assert t is not None and len(t) == 32
            assert s is not None and len(s) == 16
            assert t == t.lower() and s == s.lower()
    finally:
        provider.shutdown()
