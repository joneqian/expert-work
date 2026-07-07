"""Platform Agent template persistence — Stream Agent-Templates (M1)."""

from __future__ import annotations

from expert_work.persistence.platform_agent_template.base import (
    PlatformAgentTemplateAlreadyExistsError,
    PlatformAgentTemplateNotFoundError,
    PlatformAgentTemplateStore,
    compute_spec_sha256,
)
from expert_work.persistence.platform_agent_template.memory import (
    InMemoryPlatformAgentTemplateStore,
)
from expert_work.persistence.platform_agent_template.sql import (
    SqlPlatformAgentTemplateStore,
)

__all__ = [
    "InMemoryPlatformAgentTemplateStore",
    "PlatformAgentTemplateAlreadyExistsError",
    "PlatformAgentTemplateNotFoundError",
    "PlatformAgentTemplateStore",
    "SqlPlatformAgentTemplateStore",
    "compute_spec_sha256",
]
