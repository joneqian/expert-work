"""Helix-Agent observability primitives.

Stream A.7 — structured JSON logging.
Stream A.8 — OTel SDK / W3C Trace Context.
Stream A.9 — Prometheus metrics + CI lint (this batch).
"""

from helix_agent.common.observability.log import (
    ExtrasRedactor as ExtrasRedactor,
)
from helix_agent.common.observability.log import (
    HelixJsonFormatter as HelixJsonFormatter,
)
from helix_agent.common.observability.log import (
    get_logger as get_logger,
)
from helix_agent.common.observability.log import (
    init_logging as init_logging,
)
from helix_agent.common.observability.metrics import (
    BANNED_LABEL_NAMES as BANNED_LABEL_NAMES,
)
from helix_agent.common.observability.metrics import (
    MetricNamingError as MetricNamingError,
)
from helix_agent.common.observability.metrics import (
    helix_counter as helix_counter,
)
from helix_agent.common.observability.metrics import (
    helix_gauge as helix_gauge,
)
from helix_agent.common.observability.metrics import (
    helix_histogram as helix_histogram,
)
from helix_agent.common.observability.metrics import (
    metrics_text as metrics_text,
)
from helix_agent.common.observability.metrics import (
    validate_label_names as validate_label_names,
)
from helix_agent.common.observability.metrics import (
    validate_metric_name as validate_metric_name,
)
from helix_agent.common.observability.propagation import (
    TRACEPARENT_HEADER as TRACEPARENT_HEADER,
)
from helix_agent.common.observability.propagation import (
    TRACESTATE_HEADER as TRACESTATE_HEADER,
)
from helix_agent.common.observability.propagation import (
    current_span_id_hex as current_span_id_hex,
)
from helix_agent.common.observability.propagation import (
    current_trace_id_hex as current_trace_id_hex,
)
from helix_agent.common.observability.propagation import (
    extract_context as extract_context,
)
from helix_agent.common.observability.propagation import (
    inject_context as inject_context,
)
from helix_agent.common.observability.tracing import (
    HelixComponent as HelixComponent,
)
from helix_agent.common.observability.tracing import (
    get_tracer as get_tracer,
)
from helix_agent.common.observability.tracing import (
    helix_span as helix_span,
)
from helix_agent.common.observability.tracing import (
    init_tracing as init_tracing,
)

__all__ = [
    "BANNED_LABEL_NAMES",
    "TRACEPARENT_HEADER",
    "TRACESTATE_HEADER",
    "ExtrasRedactor",
    "HelixComponent",
    "HelixJsonFormatter",
    "MetricNamingError",
    "current_span_id_hex",
    "current_trace_id_hex",
    "extract_context",
    "get_logger",
    "get_tracer",
    "helix_counter",
    "helix_gauge",
    "helix_histogram",
    "helix_span",
    "init_logging",
    "init_tracing",
    "inject_context",
    "metrics_text",
    "metrics_text",
    "validate_label_names",
    "validate_metric_name",
]
