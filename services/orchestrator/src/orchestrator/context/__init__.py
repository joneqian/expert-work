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
    PreCompactionHook as PreCompactionHook,
)
from orchestrator.context.compressor import (
    estimate_tokens as estimate_tokens,
)
from orchestrator.context.tool_result_prune import (
    PruneResult as PruneResult,
)
from orchestrator.context.tool_result_prune import (
    ToolResultPruner as ToolResultPruner,
)
from orchestrator.context.tool_result_prune import (
    prune_old_tool_results as prune_old_tool_results,
)
from orchestrator.context.working_window import (
    TrimResult as TrimResult,
)
from orchestrator.context.working_window import (
    WorkingWindow as WorkingWindow,
)
from orchestrator.context.working_window import (
    trim_to_recent_turns as trim_to_recent_turns,
)
from orchestrator.context.workspace_projection import (
    ProjectionResult as ProjectionResult,
)
from orchestrator.context.workspace_projection import (
    WorkspaceFileReader as WorkspaceFileReader,
)
from orchestrator.context.workspace_projection import (
    WorkspaceFileWriter as WorkspaceFileWriter,
)
from orchestrator.context.workspace_projection import (
    WorkspaceIngester as WorkspaceIngester,
)
from orchestrator.context.workspace_projection import (
    WorkspaceProjector as WorkspaceProjector,
)
from orchestrator.context.workspace_projection import (
    parse_plan_md as parse_plan_md,
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
    "PreCompactionHook",
    "ProjectionResult",
    "PruneResult",
    "ToolResultPruner",
    "TrimResult",
    "WorkingWindow",
    "WorkspaceFileReader",
    "WorkspaceFileWriter",
    "WorkspaceIngester",
    "WorkspaceProjector",
    "estimate_tokens",
    "parse_plan_md",
    "prune_old_tool_results",
    "render_memory_md",
    "render_plan_md",
    "render_todo_md",
    "trim_to_recent_turns",
]
