"""Thread metadata repository (per ADR-0002 + 06-OPEN-SOURCE-DEPS).

Algorithm vendored from bytedance/deer-flow persistence/thread_meta/* @
``813d3c94``; interface re-shaped to ADR-0002 (``tenant_id`` UUID required
instead of DeerFlow's user_id AUTO sentinel).
"""

from expert_work.persistence.thread_meta.base import ThreadMetaStore as ThreadMetaStore
from expert_work.persistence.thread_meta.memory import (
    InMemoryThreadMetaStore as InMemoryThreadMetaStore,
)
from expert_work.persistence.thread_meta.sql import (
    SqlThreadMetaStore as SqlThreadMetaStore,
)

__all__ = ["InMemoryThreadMetaStore", "SqlThreadMetaStore", "ThreadMetaStore"]
