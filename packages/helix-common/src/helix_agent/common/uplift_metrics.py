"""Prometheus counters for the Capability Uplift threat scanners.

See ``docs/streams/STREAM-UPLIFT-DESIGN.md`` § 1.3 + § 2.6 + § 3.7.

Lives in :mod:`helix_agent.common` so the orchestrator memory-recall
path (which has its own threat scan + drift redaction) and the
control-plane API + scheduler paths can all import without forming a
``orchestrator → control_plane`` cycle.

Counters are intentionally low-cardinality:

- ``scope`` ∈ ``{all, context, strict}`` (3 values)
- ``result`` ∈ ``{clean, blocked, warned}`` (3 values)
- ``phase`` ∈ ``{create, fire}`` (2 values)
- ``source`` ∈ ``{api, writeback, dlq}`` for memory writes (3 values)
- ``pattern_id`` is bounded by the pattern registry (currently ~30 IDs);
  if the registry grows past 100 we'll move to a histogram-of-counts
  per ``category`` instead.
"""

from __future__ import annotations

from collections.abc import Iterable

from helix_agent.common.observability import helix_counter
from helix_agent.common.threat_patterns import ThreatFinding

_scan_total = helix_counter(
    "helix_uplift_threat_scan_total",
    "Number of threat-scan invocations, partitioned by scope and result.",
    label_names=("scope", "result"),
)

_pattern_hit_total = helix_counter(
    "helix_uplift_threat_pattern_hit_total",
    "Per-pattern hit count from threat scans (used for tuning).",
    label_names=("pattern_id", "scope"),
)

_triggers_blocked_total = helix_counter(
    "helix_uplift_triggers_blocked_total",
    "Triggers refused by the prompt-injection scanner.",
    label_names=("phase",),
)

# Capability Uplift Sprint #2 — memory poisoning defense + drift detection.
_memory_blocked_total = helix_counter(
    "helix_uplift_memory_writes_blocked_total",
    "Memory writes refused by the strict prompt-injection scanner.",
    label_names=("source",),  # api / writeback / dlq
)

_memory_redacted_total = helix_counter(
    "helix_uplift_memory_recalls_redacted_total",
    "Memory items replaced with a [BLOCKED:<category>] placeholder at recall time.",
)

_memory_drift_total = helix_counter(
    "helix_uplift_memory_drift_total",
    "Memory rows whose recomputed content_hash diverged from the stored value.",
)

# Capability Uplift Sprint #6 — memory hybrid retrieval observability.
_memory_retrieval_total = helix_counter(
    "helix_uplift_memory_retrieval_total",
    "Memory recall invocations, partitioned by mode and result.",
    label_names=("mode", "result"),  # mode=hybrid|vector, result=hit|miss
)

# Capability Uplift Sprint #8 — memory frozen snapshot + Anthropic cache anchor.
_memory_inject_mode_total = helix_counter(
    "helix_uplift_memory_recall_inject_mode_total",
    "Memory recall injection mode chosen at agent_node render time.",
    label_names=("mode",),  # per_session | per_turn
)

_anthropic_cache_anchors_total = helix_counter(
    "helix_uplift_anthropic_cache_anchors_total",
    "Total cache_control anchor markers added by upstream injectors "
    "(currently only Sprint #8 memory frozen snapshot).",
)


def record_threat_scan(*, scope: str, result: str) -> None:
    """Bump ``helix_uplift_threat_scan_total``."""
    _scan_total.labels(scope=scope, result=result).inc()


def record_threat_pattern_hits(findings: Iterable[ThreatFinding], *, scope: str) -> None:
    """Bump ``helix_uplift_threat_pattern_hit_total`` once per finding."""
    for f in findings:
        _pattern_hit_total.labels(pattern_id=f.pattern_id, scope=scope).inc()


def record_trigger_blocked(*, phase: str) -> None:
    """Bump ``helix_uplift_triggers_blocked_total{phase}``."""
    _triggers_blocked_total.labels(phase=phase).inc()


def record_memory_blocked(*, source: str) -> None:
    """Bump ``helix_uplift_memory_writes_blocked_total{source}``.

    ``source`` ∈ ``{"api", "writeback", "dlq"}`` (Sprint #2 § 3.7).
    """
    _memory_blocked_total.labels(source=source).inc()


def record_memory_redacted() -> None:
    """Bump ``helix_uplift_memory_recalls_redacted_total`` once per redacted item."""
    _memory_redacted_total.inc()


def record_memory_drift() -> None:
    """Bump ``helix_uplift_memory_drift_total`` once per drift detection."""
    _memory_drift_total.inc()


def record_memory_retrieval(*, mode: str, result: str) -> None:
    """Bump ``helix_uplift_memory_retrieval_total{mode,result}``.

    ``mode`` ∈ ``{"hybrid", "vector"}`` (Sprint #6 § 7.7).
    ``result`` ∈ ``{"hit", "miss"}``.
    """
    _memory_retrieval_total.labels(mode=mode, result=result).inc()


def record_memory_inject_mode(*, mode: str) -> None:
    """Bump ``helix_uplift_memory_recall_inject_mode_total{mode}``.

    ``mode`` ∈ ``{"per_session", "per_turn"}`` (Sprint #8 § 9.8).
    """
    _memory_inject_mode_total.labels(mode=mode).inc()


def record_anthropic_cache_anchor() -> None:
    """Bump ``helix_uplift_anthropic_cache_anchors_total`` once per
    anchor marker applied on the outbound Anthropic request."""
    _anthropic_cache_anchors_total.inc()


__all__ = [
    "record_anthropic_cache_anchor",
    "record_memory_blocked",
    "record_memory_drift",
    "record_memory_inject_mode",
    "record_memory_redacted",
    "record_memory_retrieval",
    "record_threat_pattern_hits",
    "record_threat_scan",
    "record_trigger_blocked",
]
