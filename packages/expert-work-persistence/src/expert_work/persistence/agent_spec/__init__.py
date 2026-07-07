"""``agent_spec`` repository — Stream B.5."""

from expert_work.persistence.agent_spec.base import (
    AgentSpecStore,
    AgentSpecUpdateResult,
    DuplicateAgentSpecError,
)
from expert_work.persistence.agent_spec.memory import InMemoryAgentSpecStore
from expert_work.persistence.agent_spec.sql import SqlAgentSpecStore

__all__ = [
    "AgentSpecStore",
    "AgentSpecUpdateResult",
    "DuplicateAgentSpecError",
    "InMemoryAgentSpecStore",
    "SqlAgentSpecStore",
]
