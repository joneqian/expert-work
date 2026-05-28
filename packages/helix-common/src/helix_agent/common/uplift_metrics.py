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
    "Number of threat-scan invocations, partitioned by scope, result, "
    "and (Sprint #3 U-22) which normalized variant matched. "
    "Variant enum: original | nfkc | collapsed | base64.",
    label_names=("scope", "result", "variant"),
)

_pattern_hit_total = helix_counter(
    "helix_uplift_threat_pattern_hit_total",
    "Per-pattern hit count from threat scans (used for tuning). "
    "Sprint #3 U-22 adds the ``variant`` label so SecOps can see which "
    "obfuscation pre-processing pass surfaced a finding.",
    label_names=("pattern_id", "scope", "variant"),
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

# Capability Uplift Sprint #5 — MCP client HTTP/SSE transport observability.
_mcp_call_total = helix_counter(
    "helix_uplift_mcp_call_total",
    "MCP tool-call attempts partitioned by transport / server / result. "
    "Result enum: ok | timeout | 4xx | 5xx | circuit_open | transport_err.",
    label_names=("transport", "server", "result"),
)

_mcp_circuit_state_total = helix_counter(
    "helix_uplift_mcp_circuit_state_total",
    "MCP per-server circuit-breaker transitions (Mini-ADR U-13). "
    "State enum: closed | half_open | open.",
    label_names=("server", "state"),
)

# Capability Uplift Sprint #3 — Skill supporting files + drift + publish gate.
_skill_view_total = helix_counter(
    "helix_uplift_skill_view_total",
    "skill_view tool invocations partitioned by result. Result enum: ok | not_found | truncated.",
    label_names=("result",),
)

_skill_zip_reject_total = helix_counter(
    "helix_uplift_skill_zip_reject_total",
    "Skill ZIP imports rejected at the boundary. "
    "Reason label is an allowlisted enum (no user paths) for Oracle defense.",
    label_names=("reason",),
)

_skill_blocked_total = helix_counter(
    "helix_uplift_skill_blocked_total",
    "Skill content rejected by the threat scanner (Mini-ADR U-21). "
    "Phase enum: zip_import | skill_view.",
    label_names=("phase",),
)

_skill_drift_total = helix_counter(
    "helix_uplift_skill_drift_total",
    "Skill rows whose recomputed content_hash diverged from the stored "
    "value (Mini-ADR U-21 — DB row mutated past the strict scan).",
)

_skill_redacted_total = helix_counter(
    "helix_uplift_skill_redacted_total",
    "skill_view results replaced with a [BLOCKED] placeholder at read time "
    "because context-scope re-scan matched (Mini-ADR U-21).",
)

_skill_high_risk_event_total = helix_counter(
    "helix_uplift_skill_high_risk_event_total",
    "High-risk skill publish gate events (Mini-ADR U-24). "
    "Event enum: activation_blocked | activated.",
    label_names=("event",),
)


def record_threat_scan(*, scope: str, result: str, variant: str = "original") -> None:
    """Bump ``helix_uplift_threat_scan_total``.

    ``variant`` defaults to ``"original"`` so Sprint #1 / Sprint #2
    callers that pre-date U-22 obfuscation pre-processing keep working
    without changes.
    """
    _scan_total.labels(scope=scope, result=result, variant=variant).inc()


def record_threat_pattern_hits(
    findings: Iterable[ThreatFinding], *, scope: str, variant: str = "original"
) -> None:
    """Bump ``helix_uplift_threat_pattern_hit_total`` once per finding.

    ``variant`` defaults to ``"original"`` for the same backward-compat
    reason as :func:`record_threat_scan`.
    """
    for f in findings:
        _pattern_hit_total.labels(pattern_id=f.pattern_id, scope=scope, variant=variant).inc()


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


def record_mcp_call(*, transport: str, server: str, result: str) -> None:
    """Bump ``helix_uplift_mcp_call_total{transport,server,result}``.

    ``transport`` ∈ ``{"stdio", "sse", "streamable_http"}``;
    ``result`` ∈ ``{"ok", "timeout", "4xx", "5xx", "circuit_open",
    "transport_err"}`` (Sprint #5 § 6.7).
    """
    _mcp_call_total.labels(transport=transport, server=server, result=result).inc()


def record_mcp_circuit_state(*, server: str, state: str) -> None:
    """Bump ``helix_uplift_mcp_circuit_state_total{server,state}`` once
    per state transition (Mini-ADR U-13). ``state`` ∈ ``{"closed",
    "half_open", "open"}``."""
    _mcp_circuit_state_total.labels(server=server, state=state).inc()


# Capability Uplift Sprint #3 — Skill subsystem.


def record_skill_view(*, result: str) -> None:
    """Bump ``helix_uplift_skill_view_total{result}``.

    ``result`` ∈ ``{"ok", "not_found", "truncated"}`` (Sprint #3 § 4.7).
    Drift / context-scope redaction emit through ``record_skill_drift`` /
    ``record_skill_redacted`` and do **not** double-count here.
    """
    _skill_view_total.labels(result=result).inc()


def record_skill_zip_reject(*, reason: str) -> None:
    """Bump ``helix_uplift_skill_zip_reject_total{reason}``.

    Reason must be one of the allowlisted enums (Sprint #3 § 4.7) —
    Oracle defense: we never use the user's path as a label value.
    """
    _skill_zip_reject_total.labels(reason=reason).inc()


def record_skill_blocked(*, phase: str) -> None:
    """Bump ``helix_uplift_skill_blocked_total{phase}`` (Mini-ADR U-21).

    ``phase`` ∈ ``{"zip_import", "skill_view", "supporting_file_api"}``.
    The third value extends the design's two-value enum to cover the
    single-file PUT/DELETE endpoints — same write-time strict-scan
    semantic as ``zip_import`` but a different actor / API surface so
    SecOps can split-out the block rate.
    """
    _skill_blocked_total.labels(phase=phase).inc()


def record_skill_drift() -> None:
    """Bump ``helix_uplift_skill_drift_total`` once per drift detection
    (Mini-ADR U-21)."""
    _skill_drift_total.inc()


def record_skill_redacted() -> None:
    """Bump ``helix_uplift_skill_redacted_total`` once per context-scope
    redaction in ``skill_view`` (Mini-ADR U-21)."""
    _skill_redacted_total.inc()


def record_skill_high_risk_event(*, event: str) -> None:
    """Bump ``helix_uplift_skill_high_risk_event_total{event}`` (Mini-ADR
    U-24). ``event`` ∈ ``{"activation_blocked", "activated"}``."""
    _skill_high_risk_event_total.labels(event=event).inc()


__all__ = [
    "record_anthropic_cache_anchor",
    "record_mcp_call",
    "record_mcp_circuit_state",
    "record_memory_blocked",
    "record_memory_drift",
    "record_memory_inject_mode",
    "record_memory_redacted",
    "record_memory_retrieval",
    "record_skill_blocked",
    "record_skill_drift",
    "record_skill_high_risk_event",
    "record_skill_redacted",
    "record_skill_view",
    "record_skill_zip_reject",
    "record_threat_pattern_hits",
    "record_threat_scan",
    "record_trigger_blocked",
]
