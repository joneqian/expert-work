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
from orchestrator.tools.web_search import (
    DEFAULT_CONTENT_CHAR_CAP as DEFAULT_CONTENT_CHAR_CAP,
)
from orchestrator.tools.web_search import (
    DEFAULT_MAX_RESULTS as DEFAULT_MAX_RESULTS,
)
from orchestrator.tools.web_search import (
    HTTPTavilyClient as HTTPTavilyClient,
)
from orchestrator.tools.web_search import (
    RecordingTavilyClient as RecordingTavilyClient,
)
from orchestrator.tools.web_search import (
    TavilyClient as TavilyClient,
)
from orchestrator.tools.web_search import (
    WebSearchTool as WebSearchTool,
)

__all__ = [
    "DEFAULT_CONTENT_CHAR_CAP",
    "DEFAULT_MAX_RESULTS",
    "HTTPTavilyClient",
    "RecordingTavilyClient",
    "TavilyClient",
    "Tool",
    "ToolNotFoundError",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "WebSearchTool",
]
