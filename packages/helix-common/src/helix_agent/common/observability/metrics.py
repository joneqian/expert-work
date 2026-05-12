"""Prometheus metrics — Stream A.9.

Design: subsystems/20-observability § 5.2 + § 3.1.

Two responsibilities:

1. **Naming-rule enforcement** at construction time. ``validate_metric_name``
   refuses anything that doesn't start with ``helix_``; ``validate_label_names``
   refuses high-cardinality labels (``session_id`` / ``trace_id`` /
   ``request_id`` etc.) that would blow up Prometheus storage.
2. **Convenience wrappers** — ``helix_counter`` / ``helix_histogram`` /
   ``helix_gauge`` — that run the validators upfront and then defer to
   :mod:`prometheus_client`. Call sites import these instead of the
   underlying ``Counter`` / ``Histogram`` / ``Gauge`` constructors so the
   contract is enforced uniformly.

The ``/metrics`` exposition handler stays HTTP-framework-agnostic:
:func:`metrics_text` returns the Prometheus exposition format as bytes
+ the right ``Content-Type`` so Stream B (FastAPI) can wire it however
it wants.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Final

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# § 5.2 + § 3.1: every Helix metric starts with ``helix_``. Mirror enums /
# values from the design doc into Python so the lint catches drift.
_METRIC_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"^helix_[a-z][a-z0-9_]*$")

# Labels banned by § 5.2 ("严格管控——session_id / trace_id 不进 label").
# Adding to this set is fine; removing items requires a design-doc PR.
BANNED_LABEL_NAMES: Final[frozenset[str]] = frozenset(
    {
        "session_id",
        "trace_id",
        "span_id",
        "request_id",
        # Free-text user input is the textbook cardinality-bomb.
        "user_input",
        "prompt",
    }
)


class MetricNamingError(ValueError):
    """Raised when a metric name / label set violates the § 5.2 rules."""


def validate_metric_name(name: str) -> None:
    """Reject ``name`` if it doesn't follow the § 5.2 convention.

    Rules:

    - Must start with ``helix_``
    - Lower-case alphanumerics + underscores only
    - Histograms (see :func:`helix_histogram`) additionally need the
      ``_seconds`` suffix for duration metrics; that check lives in the
      histogram constructor.

    :raises MetricNamingError: violations.
    """
    if not _METRIC_NAME_PATTERN.match(name):
        msg = (
            f"metric name must match {_METRIC_NAME_PATTERN.pattern!r}: {name!r}. "
            "See subsystems/20-observability § 5.2."
        )
        raise MetricNamingError(msg)


def validate_label_names(label_names: Iterable[str]) -> None:
    """Reject high-cardinality labels.

    Promethus stores one time-series per label combination — a high-cardinality
    label (UUID, free-text) yields one series per request and quickly OOMs the
    server.

    :raises MetricNamingError: any banned label appears in ``label_names``.
    """
    banned_present = [n for n in label_names if n in BANNED_LABEL_NAMES]
    if banned_present:
        msg = (
            f"banned high-cardinality label(s): {banned_present}. "
            "These belong in spans/logs, not in metric labels. "
            "See subsystems/20-observability § 5.2."
        )
        raise MetricNamingError(msg)


def helix_counter(
    name: str,
    documentation: str,
    label_names: Iterable[str] = (),
    *,
    registry: CollectorRegistry | None = None,
) -> Counter:
    """Build a validated :class:`Counter`.

    Wraps ``prometheus_client.Counter`` with our naming-rule checks. Use
    this instead of importing ``Counter`` directly so the contract is
    enforced at definition time, not at deploy time.
    """
    validate_metric_name(name)
    labels = list(label_names)
    validate_label_names(labels)
    return Counter(name, documentation, labels, registry=registry or REGISTRY)


def helix_gauge(
    name: str,
    documentation: str,
    label_names: Iterable[str] = (),
    *,
    registry: CollectorRegistry | None = None,
) -> Gauge:
    """Build a validated :class:`Gauge`."""
    validate_metric_name(name)
    labels = list(label_names)
    validate_label_names(labels)
    return Gauge(name, documentation, labels, registry=registry or REGISTRY)


def helix_histogram(
    name: str,
    documentation: str,
    label_names: Iterable[str] = (),
    *,
    buckets: Iterable[float] | None = None,
    registry: CollectorRegistry | None = None,
) -> Histogram:
    """Build a validated :class:`Histogram`.

    Additionally requires the ``_seconds`` suffix (subsystems/20 § 5.2 +
    § 7: ``helix_*_duration_seconds`` / ``helix_*_latency_seconds``).
    Anything that should be a histogram in Helix records a duration, and
    Prometheus best-practice is to keep duration units consistent across
    a deployment.
    """
    if not name.endswith("_seconds"):
        msg = (
            f"histogram metric must end in '_seconds': {name!r}. "
            "Prometheus best-practice (and subsystems/20 § 5.2) require "
            "duration metrics to use seconds, not milliseconds."
        )
        raise MetricNamingError(msg)
    validate_metric_name(name)
    labels = list(label_names)
    validate_label_names(labels)
    target_registry = registry or REGISTRY
    if buckets is None:
        return Histogram(name, documentation, labels, registry=target_registry)
    return Histogram(name, documentation, labels, buckets=tuple(buckets), registry=target_registry)


def metrics_text(registry: CollectorRegistry | None = None) -> tuple[bytes, str]:
    """Render the current registry in Prometheus exposition format.

    :returns: ``(body_bytes, content_type)`` — the framework-agnostic pair
        a FastAPI / aiohttp / Starlette ``/metrics`` handler can return.
    """
    body: bytes = generate_latest(registry or REGISTRY)
    return body, CONTENT_TYPE_LATEST
