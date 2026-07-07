"""Unit tests for :mod:`expert_work.common.observability.metrics`."""

from __future__ import annotations

import pytest
from prometheus_client import CollectorRegistry

from expert_work.common.observability import (
    MetricNamingError,
    expert_work_counter,
    expert_work_gauge,
    expert_work_histogram,
    metrics_text,
    validate_label_names,
    validate_metric_name,
)


@pytest.fixture
def registry() -> CollectorRegistry:
    """Isolated registry per test — avoids leaking metric names across tests."""
    return CollectorRegistry()


# --------------------------------------------------------------------------
# Name validators
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "expert_work_session_duration_seconds",
        "expert_work_llm_tokens_total",
        "expert_work_pg_connection_pool_in_use",
    ],
)
def test_valid_metric_names_pass(name: str) -> None:
    validate_metric_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "session_duration_seconds",  # missing prefix
        "Expert Work_session_duration_seconds",  # uppercase H
        "expert_work_",  # empty after prefix
        "expert_work_Foo",  # uppercase
        "expert_work_foo-bar",  # hyphen not allowed
    ],
)
def test_invalid_metric_names_rejected(name: str) -> None:
    with pytest.raises(MetricNamingError):
        validate_metric_name(name)


@pytest.mark.parametrize(
    "labels",
    [
        ["tenant"],
        ["tenant", "agent"],
        ["provider", "model", "outcome"],
    ],
)
def test_safe_label_sets_pass(labels: list[str]) -> None:
    validate_label_names(labels)


@pytest.mark.parametrize(
    "labels",
    [
        ["session_id"],
        ["tenant", "trace_id"],
        ["tenant", "agent", "request_id"],
        ["prompt"],  # free-text user input
    ],
)
def test_banned_label_sets_rejected(labels: list[str]) -> None:
    with pytest.raises(MetricNamingError, match="banned high-cardinality"):
        validate_label_names(labels)


# --------------------------------------------------------------------------
# expert_work_counter / expert_work_gauge / expert_work_histogram
# --------------------------------------------------------------------------


def test_expert_work_counter_records_increments(registry: CollectorRegistry) -> None:
    counter = expert_work_counter(
        "expert_work_test_events_total",
        "Test counter",
        ["outcome"],
        registry=registry,
    )
    counter.labels(outcome="ok").inc()
    counter.labels(outcome="ok").inc(2)

    body, content_type = metrics_text(registry)
    assert b'expert_work_test_events_total{outcome="ok"} 3.0' in body
    assert "text/plain" in content_type


def test_expert_work_gauge_set(registry: CollectorRegistry) -> None:
    gauge = expert_work_gauge(
        "expert_work_test_inflight", "Inflight gauge", ["queue"], registry=registry
    )
    gauge.labels(queue="default").set(42)

    body, _ = metrics_text(registry)
    assert b'expert_work_test_inflight{queue="default"} 42.0' in body


def test_expert_work_histogram_observes(registry: CollectorRegistry) -> None:
    histogram = expert_work_histogram(
        "expert_work_test_latency_seconds",
        "Test histogram",
        ["op"],
        buckets=(0.01, 0.1, 1.0),
        registry=registry,
    )
    histogram.labels(op="x").observe(0.05)

    body, _ = metrics_text(registry)
    assert b"expert_work_test_latency_seconds_bucket" in body
    assert b"expert_work_test_latency_seconds_count" in body


def test_expert_work_histogram_requires_seconds_suffix(registry: CollectorRegistry) -> None:
    with pytest.raises(MetricNamingError, match="end in '_seconds'"):
        expert_work_histogram(
            "expert_work_test_latency_ms",  # wrong unit
            "bad",
            ["op"],
            registry=registry,
        )


def test_expert_work_counter_rejects_bad_name(registry: CollectorRegistry) -> None:
    with pytest.raises(MetricNamingError):
        expert_work_counter("test_events_total", "bad", registry=registry)


def test_expert_work_counter_rejects_banned_label(registry: CollectorRegistry) -> None:
    with pytest.raises(MetricNamingError, match="banned high-cardinality"):
        expert_work_counter(
            "expert_work_test_events_total", "bad", ["tenant", "session_id"], registry=registry
        )


def test_metrics_text_returns_prometheus_content_type(registry: CollectorRegistry) -> None:
    _, content_type = metrics_text(registry)
    # Prometheus exposition format media type — Stream B mounts this verbatim.
    # The exact version depends on the prometheus_client release; just
    # assert the structural shape.
    assert content_type.startswith("text/plain")
    assert "version=" in content_type
    assert "charset=utf-8" in content_type
