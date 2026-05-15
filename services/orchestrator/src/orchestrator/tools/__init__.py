"""Tool subsystem — Stream E.6 onwards.

Re-exports the public surface from :mod:`orchestrator.tools.registry`.
Concrete tool adapters land in their own modules (``web_search`` E.7,
``http`` E.8, ``mcp`` E.9) and register against
:class:`ToolRegistry` at orchestrator startup.
"""

from orchestrator.tools.registry import (
    Tool as Tool,
)
from orchestrator.tools.registry import (
    ToolNotFoundError as ToolNotFoundError,
)
from orchestrator.tools.registry import (
    ToolRegistry as ToolRegistry,
)
from orchestrator.tools.registry import (
    ToolResult as ToolResult,
)
from orchestrator.tools.registry import (
    ToolSpec as ToolSpec,
)

__all__ = [
    "Tool",
    "ToolNotFoundError",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
]
