"""Stream L.L2 — context compression for long-running agents.

The :func:`agent_node` preflight estimates the outbound prompt size
and dispatches to :class:`ContextCompressor` when it would exceed
``ModelSpec.context_window * policies.context_compression.threshold_pct``.
Compression preserves the first ``head_keep`` and last ``tail_keep``
non-system messages and replaces the middle with an LLM-generated
summary, keeping the run inside the upstream context window without
losing the conversation's salient points. See
[STREAM-L-DESIGN § 3.L2](../../../../../docs/streams/STREAM-L-DESIGN.md)
+ Mini-ADR L-2.
"""

from orchestrator.context.compressor import (
    ContextCompressor as ContextCompressor,
)
from orchestrator.context.compressor import (
    ContextOverflowError as ContextOverflowError,
)
from orchestrator.context.compressor import (
    estimate_tokens as estimate_tokens,
)

__all__ = [
    "ContextCompressor",
    "ContextOverflowError",
    "estimate_tokens",
]
