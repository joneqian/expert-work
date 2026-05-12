"""OTel SDK initialization + ``helix_span`` helper — Stream A.8.

Design: subsystems/20-observability § 4.1 + § 5.1.

Two public surfaces:

- :func:`init_tracing` — process-wide setup. Wires the SDK to an OTLP
  exporter (default ``http://localhost:4318/v1/traces`` for the local
  OTel Collector); idempotent across re-invocations.
- :func:`helix_span` — context manager that creates a span with the
  ``helix.{component}.{action}`` naming contract + auto-injected
  ``tenant`` / ``service`` / ``env`` attributes.

W3C Trace Context extract/inject lives in :mod:`.propagation` so the
naming + propagation concerns stay independently testable.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from enum import StrEnum
from typing import Any, Final

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, Status, StatusCode, Tracer

from helix_agent.common import __version__
from helix_agent.common.context import get_current_tenant

logger = logging.getLogger("helix.observability.tracing")


class HelixComponent(StrEnum):
    """Fixed enum of components allowed in ``helix.{component}.{action}``.

    Source: subsystems/20-observability § 5.1. Adding a new component
    requires a design-doc update + a PR to this enum so the naming lint
    keeps catching typos.
    """

    CONTROL_PLANE = "control_plane"
    ORCHESTRATOR = "orchestrator"
    SANDBOX = "sandbox"
    CREDENTIAL_PROXY = "credential_proxy"
    MCP_GATEWAY = "mcp_gateway"
    LLM_GATEWAY = "llm_gateway"
    MEMORY = "memory"
    SUBAGENT = "subagent"
    HITL = "hitl"
    EVAL = "eval"
    DR = "dr"
    DB = "db"
    SESSION = "session"
    DURABLE = "durable"
    QUOTA = "quota"
    OBSERVABILITY = "observability"


_TRACER_NAME: Final[str] = "helix-agent"

# Mutable process-wide state populated by ``init_tracing``. The
# ``helix_span`` context manager reads these to auto-inject service / env
# attrs without each caller having to pass them.
_service_name: str | None = None
_env: str | None = None


def init_tracing(
    *,
    service_name: str,
    env: str,
    otlp_endpoint: str | None = None,
    span_processor: SpanProcessor | None = None,
) -> TracerProvider:
    """Install a :class:`TracerProvider` for the current process.

    **First call wins.** OTel's ``set_tracer_provider`` is a one-shot —
    subsequent calls only refresh ``service_name`` / ``env`` (so the
    JSON formatter's ``service`` / ``env`` log fields stay correct) and
    add the supplied processor to the existing provider; the provider
    object itself is not replaced.

    In production this matches the intended lifecycle: ``init_tracing``
    runs exactly once at app startup. In tests we use a session-scoped
    fixture for the same reason.

    :param service_name: Logical service (``control_plane`` /
        ``orchestrator``).
    :param env: ``dev`` / ``staging`` / ``prod``.
    :param otlp_endpoint: Override the OTLP HTTP endpoint. Default is
        ``$OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`` or
        ``http://localhost:4318/v1/traces``.
    :param span_processor: Inject a custom processor (tests pass an
        ``InMemorySpanExporter`` here). When ``None``, a
        :class:`BatchSpanProcessor` + OTLP HTTP exporter is built.
    :returns: The active provider (so callers can ``provider.shutdown()``
        on teardown).
    """
    global _service_name, _env
    _service_name = service_name
    _env = env

    if span_processor is None:
        # Lazy import — the OTLP exporter pulls in protobuf + requests,
        # which tests using in-memory exporters don't need.
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        endpoint = (
            otlp_endpoint
            or os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
            or "http://localhost:4318/v1/traces"
        )
        span_processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))

    existing = trace.get_tracer_provider()
    if isinstance(existing, TracerProvider):
        # Re-init path: attach the new processor to the live provider.
        existing.add_span_processor(span_processor)
        logger.info("tracing.reinit service=%s env=%s", service_name, env)
        return existing

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.namespace": "helix",
            "service.version": __version__,
            "deployment.environment": env,
        }
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(span_processor)
    trace.set_tracer_provider(provider)
    logger.info("tracing.initialized service=%s env=%s", service_name, env)
    return provider


def get_tracer() -> Tracer:
    """Return the canonical Helix tracer.

    Safe to call before :func:`init_tracing` — the API falls back to a
    no-op tracer so unit tests can import code that creates spans
    without a global provider.
    """
    return trace.get_tracer(_TRACER_NAME)


def helix_span(
    component: HelixComponent | str,
    action: str,
    *,
    attributes: Mapping[str, Any] | None = None,
) -> AbstractContextManager[Span]:
    """Open a span named ``helix.{component}.{action}``.

    Auto-injects ``tenant``, ``service``, ``env`` from contextvars + init
    parameters. Caller-supplied ``attributes`` win on key collision.

    The context manager sets the span status to ``ERROR`` automatically
    if the wrapped block raises — matching subsystems/20 § 5.5 (root
    span status carries the failure reason).

    :raises ValueError: if ``component`` is a string outside
        :class:`HelixComponent` (typos surface **at call time**, not at
        ``__enter__`` — so callers can ``pytest.raises(...)`` directly
        on the constructor).
    """
    comp_value = _validate_component(component)
    return _helix_span_cm(comp_value, action, attributes)


@contextmanager
def _helix_span_cm(
    comp_value: str,
    action: str,
    attributes: Mapping[str, Any] | None,
) -> Iterator[Span]:
    span_name = f"helix.{comp_value}.{action}"

    attrs: dict[str, Any] = {}
    tenant = get_current_tenant()
    if tenant is not None:
        attrs["tenant"] = str(tenant)
    if _service_name is not None:
        attrs["service"] = _service_name
    if _env is not None:
        attrs["env"] = _env
    if attributes:
        attrs.update(attributes)

    tracer = get_tracer()
    with tracer.start_as_current_span(span_name, attributes=attrs) as span:
        try:
            yield span
        except BaseException as exc:
            # Use BaseException so KeyboardInterrupt / SystemExit also
            # tag the span — operators want to see those, not just
            # regular exceptions.
            span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            span.record_exception(exc)
            raise


def _validate_component(component: HelixComponent | str) -> str:
    if isinstance(component, HelixComponent):
        return component.value
    try:
        return HelixComponent(component).value
    except ValueError as exc:
        valid = ", ".join(sorted(c.value for c in HelixComponent))
        msg = (
            f"unknown helix component {component!r}; must be one of: {valid}. "
            "Add a new component to HelixComponent + subsystems/20 § 5.1 first."
        )
        raise ValueError(msg) from exc
