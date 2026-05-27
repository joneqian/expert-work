"""Re-export shim — counters moved to :mod:`helix_agent.common.uplift_metrics`.

The orchestrator memory-recall path (Sprint #2) needs to bump the same
counters but cannot import from control_plane without forming a cycle
(``orchestrator → control_plane → orchestrator``). The counter
definitions therefore live in helix-common; this module is kept as a
thin re-export so Sprint #1 callers (``api/triggers.py``,
``trigger_firing.py``) don't need to change their import paths.
"""

from __future__ import annotations

from helix_agent.common.uplift_metrics import (
    record_anthropic_cache_anchor,
    record_memory_blocked,
    record_memory_drift,
    record_memory_inject_mode,
    record_memory_redacted,
    record_memory_retrieval,
    record_threat_pattern_hits,
    record_threat_scan,
    record_trigger_blocked,
)

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
