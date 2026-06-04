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
from helix_agent.common.observability.metrics import helix_gauge
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

# Capability Uplift Sprint #4 — Curator state machine (Mini-ADR U-31).
_curator_transition_total = helix_counter(
    "helix_uplift_curator_transition_total",
    "Skill lifecycle transitions driven by the Curator worker or by an "
    "activity-triggered auto-revive (Mini-ADRs U-26 / U-29). "
    "from_state ∈ {active, stale}; to_state ∈ {active, stale, archived}.",
    label_names=("from_state", "to_state"),
)

_curator_pinned_skills = helix_gauge(
    "helix_uplift_curator_pinned_skills",
    "Total number of pinned skills across all tenants (Curator escape "
    "hatch — Mini-ADR U-25). Refreshed at the end of each Curator sweep.",
)

_skill_view_archived_blocked_total = helix_counter(
    "helix_uplift_skill_view_archived_blocked_total",
    "skill_view calls that hit an archived skill — cold path (Mini-ADR "
    "U-29). Expected to be near-zero in steady state; a non-trivial rate "
    "means an agent's manifest references an auto-archived skill that "
    "should be unarchived or replaced.",
)

# Capability Uplift Sprint #7 — MemoryConsolidator (Mini-ADR U-41).
_memory_cluster_candidates_total = helix_counter(
    "helix_uplift_memory_cluster_candidates_total",
    "MemoryConsolidator cluster candidates surfaced by the embedding "
    "pre-filter (Mini-ADR U-35). Used to track candidate volume vs. "
    "actual consolidations (a low conversion ratio means embedding "
    "threshold is too permissive, or LLM is over-rejecting).",
)

_memory_consolidated_total = helix_counter(
    "helix_uplift_memory_consolidated_total",
    "Consolidated long-term memory items written by the consolidator "
    "(LLM verified the cluster + summary written + sources linked).",
)

_memory_cluster_rejected_total = helix_counter(
    "helix_uplift_memory_cluster_rejected_total",
    "Clusters rejected by the consolidator LLM. Reason label "
    "distinguishes anti-mislearn categories (env_failure | "
    "negative_tool | transient_error | one_off_narrative | "
    "time_bound | credential_shape) from false_cluster (embedding "
    "thought related, LLM disagrees).",
    label_names=("reason",),
)

_memory_purged_total = helix_counter(
    "helix_uplift_memory_purged_total",
    "Lone-item transient memories soft-deleted by the consolidator "
    "noise-purge sub-pass (Mini-ADR U-37). Category label mirrors "
    "the cluster_rejected reason enum so the same recording rule "
    "groups apply across both paths.",
    label_names=("category",),
)

_memory_reviewed_durable_total = helix_counter(
    "helix_uplift_memory_reviewed_durable_total",
    "Lone-item transient memories the consolidator reviewed and "
    "decided to keep — last_reviewed_at marked so future ticks skip "
    "(Mini-ADR U-37 protection #3).",
)

_consolidator_llm_tokens_total = helix_counter(
    "helix_uplift_consolidator_llm_tokens_total",
    "Aux model token consumption by the MemoryConsolidator. Cost "
    "telemetry is split from main-model accounting so the Sprint #7 "
    "spend can be attributed precisely (model + kind labels).",
    label_names=("model", "kind"),  # kind ∈ input | output
)

_consolidator_runs_total = helix_counter(
    "helix_uplift_consolidator_runs_total",
    "MemoryConsolidator worker sweeps that completed (1 per scheduled "
    "tick). The labeled outcome lets the alert distinguish 'sweep ran "
    "but had nothing to do' from 'sweep didn't run at all'.",
    label_names=("outcome",),  # outcome ∈ ok | error
)

# Stream O — credentials management (Mini-ADR O-8).
_credentials_resolve_total = helix_counter(
    "helix_uplift_credentials_resolve_total",
    "CredentialsResolver lookups, partitioned by mode, role, key, and "
    "result. ``role`` ∈ provider | tool; ``key`` is the provider name "
    "or tool name; ``result`` ∈ ok | missing_cred.",
    label_names=("mode", "role", "key", "result"),
)

_manifest_provider_rejected_total = helix_counter(
    "helix_uplift_manifest_provider_rejected_total",
    "Agent manifest publish attempts rejected because the referenced "
    "provider is not in the platform's supported_providers whitelist.",
    label_names=("provider",),
)

_legacy_credentials_fallback_total = helix_counter(
    "helix_uplift_legacy_credentials_fallback_total",
    "Stream O transition-period fallback: callers still reading the "
    "deprecated ``embedding_api_key_ref`` / ``rerank_api_key_ref`` / "
    "``tavily_api_key_ref`` env fields. Should drop to zero once ops "
    "migrate to platform_*_credentials; removal in M1 Q?.",
    label_names=("role",),
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


def record_curator_transition(*, from_state: str, to_state: str, count: int = 1) -> None:
    """Bump ``helix_uplift_curator_transition_total{from_state,to_state}``
    by ``count`` (Mini-ADR U-31). Used by the Curator worker for batch
    transitions and by :func:`SkillStore.bump_last_used_at` for the
    per-call ``stale → active`` auto-revive path."""
    if count <= 0:
        return
    _curator_transition_total.labels(from_state=from_state, to_state=to_state).inc(count)


def set_curator_pinned_skills(count: int) -> None:
    """Refresh the ``helix_uplift_curator_pinned_skills`` gauge — called
    at the end of each Curator sweep so the Admin dashboard tracks the
    operator-managed escape-hatch population (Mini-ADR U-31)."""
    _curator_pinned_skills.set(count)


def record_skill_view_archived_blocked() -> None:
    """Bump ``helix_uplift_skill_view_archived_blocked_total``. Fires
    when ``skill_view`` resolves to an archived skill — cold path
    (Mini-ADR U-29)."""
    _skill_view_archived_blocked_total.inc()


# Capability Uplift Sprint #7 — MemoryConsolidator recorders (Mini-ADR U-41).


def record_memory_cluster_candidates(count: int = 1) -> None:
    """Bump ``helix_uplift_memory_cluster_candidates_total`` by ``count``."""
    if count <= 0:
        return
    _memory_cluster_candidates_total.inc(count)


def record_memory_consolidated() -> None:
    """Bump ``helix_uplift_memory_consolidated_total`` once per
    consolidation commit (Mini-ADR U-34)."""
    _memory_consolidated_total.inc()


def record_memory_cluster_rejected(*, reason: str) -> None:
    """Bump ``helix_uplift_memory_cluster_rejected_total{reason}``.

    ``reason`` ∈ ``{"false_cluster", "env_failure", "negative_tool",
    "transient_error", "one_off_narrative", "time_bound",
    "credential_shape"}`` (Mini-ADR U-36).
    """
    _memory_cluster_rejected_total.labels(reason=reason).inc()


def record_memory_purged(*, category: str) -> None:
    """Bump ``helix_uplift_memory_purged_total{category}``. Category
    enum matches :func:`record_memory_cluster_rejected` reason enum
    minus ``false_cluster`` (single-item review has no false-cluster
    case)."""
    _memory_purged_total.labels(category=category).inc()


def record_memory_reviewed_durable() -> None:
    """Bump ``helix_uplift_memory_reviewed_durable_total`` once per
    lone-item review verdict = durable (Mini-ADR U-37 protection #3)."""
    _memory_reviewed_durable_total.inc()


def record_consolidator_llm_tokens(*, model: str, input_tokens: int, output_tokens: int) -> None:
    """Bump ``helix_uplift_consolidator_llm_tokens_total{model,kind}``.

    Aux model spend is tracked separately from main-model accounting
    so the Sprint #7 cost is unambiguous in the dashboards.
    """
    if input_tokens > 0:
        _consolidator_llm_tokens_total.labels(model=model, kind="input").inc(input_tokens)
    if output_tokens > 0:
        _consolidator_llm_tokens_total.labels(model=model, kind="output").inc(output_tokens)


def record_consolidator_run(*, outcome: str) -> None:
    """Bump ``helix_uplift_consolidator_runs_total{outcome}``.

    ``outcome`` ∈ ``{"ok", "error"}``. Called once per scheduled tick;
    ``ok`` covers "ran cleanly, possibly with nothing to do"; ``error``
    covers an unhandled exception escaping the worker entry."""
    _consolidator_runs_total.labels(outcome=outcome).inc()


# Stream O — credentials recorders (Mini-ADR O-8).


def record_credentials_resolve(*, mode: str, role: str, key: str, result: str) -> None:
    """Bump ``helix_uplift_credentials_resolve_total``.

    ``mode`` ∈ ``{"platform", "tenant"}``;
    ``role`` ∈ ``{"provider", "tool"}``;
    ``key``  is the provider name or tool name;
    ``result`` ∈ ``{"ok", "missing_cred"}``."""
    _credentials_resolve_total.labels(mode=mode, role=role, key=key, result=result).inc()


def record_manifest_provider_rejected(*, provider: str) -> None:
    """Bump ``helix_uplift_manifest_provider_rejected_total{provider}``."""
    _manifest_provider_rejected_total.labels(provider=provider).inc()


def record_legacy_credentials_fallback(*, role: str) -> None:
    """Bump ``helix_uplift_legacy_credentials_fallback_total{role}``.

    ``role`` ∈ ``{"embedding", "rerank", "tavily"}``."""
    _legacy_credentials_fallback_total.labels(role=role).inc()


__all__ = [
    "record_anthropic_cache_anchor",
    "record_consolidator_llm_tokens",
    "record_consolidator_run",
    "record_credentials_resolve",
    "record_curator_transition",
    "record_legacy_credentials_fallback",
    "record_manifest_provider_rejected",
    "record_mcp_call",
    "record_mcp_circuit_state",
    "record_memory_blocked",
    "record_memory_cluster_candidates",
    "record_memory_cluster_rejected",
    "record_memory_consolidated",
    "record_memory_drift",
    "record_memory_inject_mode",
    "record_memory_purged",
    "record_memory_redacted",
    "record_memory_retrieval",
    "record_memory_reviewed_durable",
    "record_skill_blocked",
    "record_skill_drift",
    "record_skill_high_risk_event",
    "record_skill_redacted",
    "record_skill_view",
    "record_skill_view_archived_blocked",
    "record_skill_zip_reject",
    "record_threat_pattern_hits",
    "record_threat_scan",
    "record_trigger_blocked",
    "set_curator_pinned_skills",
]
