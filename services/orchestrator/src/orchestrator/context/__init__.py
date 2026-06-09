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
from orchestrator.context.workspace_projection import (
    ProjectionResult as ProjectionResult,
)
from orchestrator.context.workspace_projection import (
    WorkspaceFileWriter as WorkspaceFileWriter,
)
from orchestrator.context.workspace_projection import (
    WorkspaceProjector as WorkspaceProjector,
)
from orchestrator.context.workspace_projection import (
    render_memory_md as render_memory_md,
)
from orchestrator.context.workspace_projection import (
    render_plan_md as render_plan_md,
)
from orchestrator.context.workspace_projection import (
    render_todo_md as render_todo_md,
)

__all__ = [
    "ContextCompressor",
    "ContextOverflowError",
    "ProjectionResult",
    "WorkspaceFileWriter",
    "WorkspaceProjector",
    "estimate_tokens",
    "render_memory_md",
    "render_plan_md",
    "render_todo_md",
]
