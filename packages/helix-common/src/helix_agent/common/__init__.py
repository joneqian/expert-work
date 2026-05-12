"""Helix-Agent shared utilities: logging, telemetry, errors, version."""

from helix_agent.common.context import (
    get_current_tenant as get_current_tenant,
)
from helix_agent.common.context import (
    get_current_trace_id as get_current_trace_id,
)
from helix_agent.common.context import (
    require_current_tenant as require_current_tenant,
)
from helix_agent.common.context import (
    reset_current_tenant as reset_current_tenant,
)
from helix_agent.common.context import (
    reset_current_trace_id as reset_current_trace_id,
)
from helix_agent.common.context import (
    set_current_tenant as set_current_tenant,
)
from helix_agent.common.context import (
    set_current_trace_id as set_current_trace_id,
)

__version__ = "0.0.0"

__all__ = [
    "__version__",
    "get_current_tenant",
    "get_current_trace_id",
    "require_current_tenant",
    "reset_current_tenant",
    "reset_current_trace_id",
    "set_current_tenant",
    "set_current_trace_id",
]
