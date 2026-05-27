"""Shared search primitives — Capability Uplift Sprint #6 (Mini-ADR U-6).

Pure algorithms with no persistence dependencies. Both the J.5
``KnowledgeRetriever`` and the new memory hybrid recall path import
from here so the implementation is single-sourced.
"""

from helix_agent.common.search.rrf import rrf_fuse

__all__ = ["rrf_fuse"]
