"""Re-export of ``expert_work.common.context``.

The contextvar implementation lives in ``expert-work-common`` (Stream A.7
promotion) so the observability primitives can read it without a
``expert-work-common -> expert-work-runtime`` dependency reversal. This module
preserves the original import path that existing tests / call sites
use.
"""

from expert_work.common.context import (
    get_current_tenant as get_current_tenant,
)
from expert_work.common.context import (
    get_current_trace_id as get_current_trace_id,
)
from expert_work.common.context import (
    require_current_tenant as require_current_tenant,
)
from expert_work.common.context import (
    reset_current_tenant as reset_current_tenant,
)
from expert_work.common.context import (
    reset_current_trace_id as reset_current_trace_id,
)
from expert_work.common.context import (
    set_current_tenant as set_current_tenant,
)
from expert_work.common.context import (
    set_current_trace_id as set_current_trace_id,
)

__all__ = [
    "get_current_tenant",
    "get_current_trace_id",
    "require_current_tenant",
    "reset_current_tenant",
    "reset_current_trace_id",
    "set_current_tenant",
    "set_current_trace_id",
]
