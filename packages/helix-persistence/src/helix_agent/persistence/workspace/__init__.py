"""Per-user persistent-workspace repository — Stream J.15.

Registers the docker named volume backing each ``(tenant_id, user_id)``
pair's ``/workspace``. The volume outlives the ephemeral sandbox
containers that mount it. See ``docs/streams/STREAM-J-DESIGN.md`` § 9.
"""

from helix_agent.persistence.workspace.base import (
    UserWorkspaceStore as UserWorkspaceStore,
)
from helix_agent.persistence.workspace.base import (
    WorkspaceNotFoundError as WorkspaceNotFoundError,
)
from helix_agent.persistence.workspace.base import (
    workspace_volume_name as workspace_volume_name,
)
from helix_agent.persistence.workspace.memory import (
    InMemoryUserWorkspaceStore as InMemoryUserWorkspaceStore,
)
from helix_agent.persistence.workspace.sql import (
    SqlUserWorkspaceStore as SqlUserWorkspaceStore,
)

__all__ = [
    "InMemoryUserWorkspaceStore",
    "SqlUserWorkspaceStore",
    "UserWorkspaceStore",
    "WorkspaceNotFoundError",
    "workspace_volume_name",
]
