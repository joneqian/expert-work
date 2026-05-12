"""Unit tests for :mod:`helix_agent.common.observability.tracing`.

OTel's ``set_tracer_provider`` is a process-wide one-shot, so we install
the provider once at session scope and reset the in-memory exporter at
each test.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from helix_agent.common.context import (
    reset_current_tenant,
    set_current_tenant,
)
from helix_agent.common.observability import (
    HelixComponent,
    helix_span,
    init_tracing,
)


@pytest.fixture(scope="session")
def _shared_exporter() -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = init_tracing(
        service_name="test-service",
        env="test",
        span_processor=SimpleSpanProcessor(exporter),
    )
    try:
        yield exporter
    finally:
        provider.shutdown()


@pytest.fixture
def exporter(_shared_exporter: InMemorySpanExporter) -> Iterator[InMemorySpanExporter]:
    """Fresh empty exporter view per test."""
    _shared_exporter.clear()
    yield _shared_exporter
    _shared_exporter.clear()


def test_helix_span_uses_canonical_naming(exporter: InMemorySpanExporter) -> None:
    with helix_span(HelixComponent.ORCHESTRATOR, "session_run"):
        pass
    spans = list(exporter.get_finished_spans())
    assert [s.name for s in spans] == ["helix.orchestrator.session_run"]


def test_helix_span_accepts_string_component(exporter: InMemorySpanExporter) -> None:
    with helix_span("control_plane", "manifest_create"):
        pass
    spans = list(exporter.get_finished_spans())
    assert [s.name for s in spans] == ["helix.control_plane.manifest_create"]


def test_helix_span_rejects_unknown_component(exporter: InMemorySpanExporter) -> None:
    # Validation is eager — the constructor raises, not __enter__. Callers
    # can pytest.raises() directly on helix_span(...) without entering the
    # context manager (also keeps CodeQL's unreachable-statement check happy).
    with pytest.raises(ValueError, match="unknown helix component"):
        helix_span("ufo", "anything")


def test_helix_span_injects_tenant_from_contextvar(exporter: InMemorySpanExporter) -> None:
    tenant = UUID("00000000-0000-0000-0000-000000000123")
    token = set_current_tenant(tenant)
    try:
        with helix_span(HelixComponent.ORCHESTRATOR, "session_run"):
            pass
    finally:
        reset_current_tenant(token)

    [span] = list(exporter.get_finished_spans())
    assert span.attributes is not None
    assert span.attributes["tenant"] == str(tenant)
    assert span.attributes["service"] == "test-service"
    assert span.attributes["env"] == "test"


def test_helix_span_caller_attrs_win_on_collision(exporter: InMemorySpanExporter) -> None:
    with helix_span(
        HelixComponent.LLM_GATEWAY,
        "provider_request",
        attributes={"service": "overridden", "model": "claude-opus-4-5"},
    ):
        pass

    [span] = list(exporter.get_finished_spans())
    assert span.attributes is not None
    assert span.attributes["service"] == "overridden"
    assert span.attributes["model"] == "claude-opus-4-5"


def test_helix_span_records_exception_and_sets_error_status(
    exporter: InMemorySpanExporter,
) -> None:
    with pytest.raises(RuntimeError, match="boom"):
        with helix_span(HelixComponent.ORCHESTRATOR, "session_run"):
            raise RuntimeError("boom")

    [span] = list(exporter.get_finished_spans())
    assert span.status.is_ok is False
    assert "RuntimeError" in (span.status.description or "")
    # OTel records the exception as a span event automatically.
    assert any("exception" in event.name for event in span.events)


def test_reinit_attaches_new_processor_to_existing_provider(
    exporter: InMemorySpanExporter,
) -> None:
    """A second ``init_tracing`` re-uses the live provider and adds the
    new processor — both exporters then see subsequent spans."""
    second_exporter = InMemorySpanExporter()
    init_tracing(
        service_name="test-service-reinit",
        env="test",
        span_processor=SimpleSpanProcessor(second_exporter),
    )

    with helix_span(HelixComponent.ORCHESTRATOR, "session_run"):
        pass

    assert len(exporter.get_finished_spans()) == 1
    assert len(second_exporter.get_finished_spans()) == 1
