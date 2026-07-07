"""Unit tests for :mod:`expert_work.common.observability.log`."""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator, Mapping
from uuid import UUID

import pytest

from expert_work.common.context import (
    reset_current_tenant,
    reset_current_trace_id,
    set_current_tenant,
    set_current_trace_id,
)
from expert_work.common.observability import (
    ExpertWorkJsonFormatter,
    get_logger,
    init_logging,
)

_TENANT = UUID("00000000-0000-0000-0000-00000000abcd")


@pytest.fixture
def stream() -> io.StringIO:
    return io.StringIO()


@pytest.fixture
def initialized(stream: io.StringIO) -> Iterator[None]:
    """Install the JSON formatter on the root logger, then restore."""
    init_logging(service="control-plane", env="dev", stream=stream)
    try:
        yield
    finally:
        root = logging.getLogger()
        for handler in list(root.handlers):
            if isinstance(handler.formatter, ExpertWorkJsonFormatter):
                root.removeHandler(handler)


def _last_record(stream: io.StringIO) -> dict[str, object]:
    """Read the most recent JSON record from the capture stream."""
    lines = [ln for ln in stream.getvalue().splitlines() if ln.strip()]
    assert lines, "no log lines captured"
    record: dict[str, object] = json.loads(lines[-1])
    return record


def test_emits_mandatory_schema_fields(stream: io.StringIO, initialized: None) -> None:
    logger = get_logger("expert_work.test")
    logger.info("session.start")

    record = _last_record(stream)
    assert record["level"] == "INFO"
    assert record["logger"] == "expert_work.test"
    assert record["message"] == "session.start"
    assert record["service"] == "control-plane"
    assert record["env"] == "dev"
    assert record["tenant"] is None  # no context set in this test
    assert record["trace_id"] is None
    assert record["span_id"] is None  # populated by Stream A.8
    # Timestamp is ISO 8601 UTC with millis + Z suffix.
    assert isinstance(record["timestamp"], str)
    assert record["timestamp"].endswith("Z")


def test_injects_tenant_and_trace_id_from_contextvars(
    stream: io.StringIO, initialized: None
) -> None:
    token = set_current_tenant(_TENANT)
    trace_token = set_current_trace_id("abc123def456")
    try:
        get_logger("expert_work.test").info("session.start")
    finally:
        reset_current_trace_id(trace_token)
        reset_current_tenant(token)

    record = _last_record(stream)
    assert record["tenant"] == str(_TENANT)
    assert record["trace_id"] == "abc123def456"


def test_passes_extras_through(stream: io.StringIO, initialized: None) -> None:
    get_logger("expert_work.test").info(
        "session.start", extra={"session_id": "sess-1", "agent": "demo"}
    )
    record = _last_record(stream)
    assert record["session_id"] == "sess-1"
    assert record["agent"] == "demo"


def test_extras_cannot_override_mandatory_fields(stream: io.StringIO, initialized: None) -> None:
    """An ``extra={"service": "...", "tenant": "..."}`` must not clobber the
    mandatory operator metadata — those keys are owned by the formatter."""
    get_logger("expert_work.test").info(
        "evt", extra={"service": "EVIL", "tenant": "EVIL", "trace_id": "EVIL"}
    )
    record = _last_record(stream)
    assert record["service"] == "control-plane"
    assert record["tenant"] is None
    assert record["trace_id"] is None


def test_redactor_applied_to_extras(stream: io.StringIO) -> None:
    captured: list[dict[str, object]] = []

    def _fake_redactor(extras: Mapping[str, object]) -> dict[str, object]:
        # Mask any key called "secret".
        masked = {k: ("***" if k == "secret" else v) for k, v in extras.items()}
        captured.append(masked)
        return masked

    init_logging(service="cp", env="dev", redactor=_fake_redactor, stream=stream)
    try:
        get_logger("expert_work.test").info("evt", extra={"secret": "sk-abc", "session_id": "x"})
    finally:
        root = logging.getLogger()
        for h in list(root.handlers):
            if isinstance(h.formatter, ExpertWorkJsonFormatter):
                root.removeHandler(h)

    record = _last_record(stream)
    assert record["secret"] == "***"
    assert record["session_id"] == "x"
    assert captured  # redactor actually ran


def test_get_logger_rejects_non_expert_work_name() -> None:
    with pytest.raises(ValueError, match=r"must start with 'expert_work\.'"):
        get_logger("orchestrator")


def test_init_logging_is_idempotent(stream: io.StringIO) -> None:
    init_logging(service="cp", env="dev", stream=stream)
    init_logging(service="cp", env="dev", stream=stream)

    root = logging.getLogger()
    expert_work_handlers = [
        h for h in root.handlers if isinstance(h.formatter, ExpertWorkJsonFormatter)
    ]
    try:
        assert len(expert_work_handlers) == 1
    finally:
        for h in list(root.handlers):
            if isinstance(h.formatter, ExpertWorkJsonFormatter):
                root.removeHandler(h)


def test_exception_field_populated_when_logging_exception(
    stream: io.StringIO, initialized: None
) -> None:
    logger = get_logger("expert_work.test")
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        logger.exception("evt")
    record = _last_record(stream)
    assert "exception" in record
    assert "RuntimeError" in str(record["exception"])
    assert "boom" in str(record["exception"])


def test_uuid_in_extras_serializes_via_isoformat_fallback(
    stream: io.StringIO, initialized: None
) -> None:
    """The JSON default falls back to ``str()`` for UUIDs — they would
    otherwise raise ``TypeError`` during ``json.dumps``."""
    other = UUID("00000000-0000-0000-0000-00000000beef")
    get_logger("expert_work.test").info("evt", extra={"thread_id": other})
    record = _last_record(stream)
    assert record["thread_id"] == str(other)


def test_span_id_populated_inside_expert_work_span(stream: io.StringIO) -> None:
    """Stream A.8 — span_id flows from the active OTel span into the log."""
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from expert_work.common.observability import (
        ExpertWorkComponent,
        expert_work_span,
        init_tracing,
    )

    init_logging(service="cp", env="dev", stream=stream)
    provider = init_tracing(
        service_name="cp",
        env="dev",
        span_processor=SimpleSpanProcessor(InMemorySpanExporter()),
    )
    try:
        with expert_work_span(ExpertWorkComponent.ORCHESTRATOR, "evt"):
            get_logger("expert_work.test").info("inside_span")
    finally:
        provider.shutdown()
        root = logging.getLogger()
        for h in list(root.handlers):
            if isinstance(h.formatter, ExpertWorkJsonFormatter):
                root.removeHandler(h)

    record = _last_record(stream)
    # Both populated by OTel; lowercase hex of fixed lengths.
    assert isinstance(record["trace_id"], str)
    assert len(record["trace_id"]) == 32
    assert isinstance(record["span_id"], str)
    assert len(record["span_id"]) == 16


def test_run_id_null_outside_run_worker(stream: io.StringIO, initialized: None) -> None:
    """Stream HX-4 — ``run_id`` is a mandatory schema field, null outside
    a run worker (HTTP handlers, background sweeps)."""
    get_logger("expert_work.test").info("evt")
    assert _last_record(stream)["run_id"] is None


def test_run_id_injected_from_contextvar(stream: io.StringIO, initialized: None) -> None:
    from expert_work.common.context import reset_current_run_id, set_current_run_id

    run_id = UUID("00000000-0000-0000-0000-0000000000aa")
    token = set_current_run_id(run_id)
    try:
        get_logger("expert_work.test").info("evt")
    finally:
        reset_current_run_id(token)

    record = _last_record(stream)
    assert record["run_id"] == str(run_id)
    # An extra cannot shadow it (mandatory-field precedence).
    get_logger("expert_work.test").info("evt2", extra={"run_id": "EVIL"})
    assert _last_record(stream)["run_id"] is None
