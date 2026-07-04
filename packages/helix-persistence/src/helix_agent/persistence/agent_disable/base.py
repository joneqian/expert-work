"""Abstract :class:`AgentDisableStore` — Stream RT-4 (RT-ADR-16)."""

from __future__ import annotations

import abc
from uuid import UUID

from helix_agent.protocol import AgentDisableRecord


class AgentDisableStore(abc.ABC):
    """Persistence for the per-(tenant, agent_name) kill-switch flag."""

    @abc.abstractmethod
    async def get(self, *, tenant_id: UUID, agent_name: str) -> AgentDisableRecord | None:
        """Return the row, or ``None`` when the agent was never disabled.

        A missing row reads as "not disabled" — the enforcement is a
        deliberate admin action, not a default-deny gate (fail-open, same
        rationale as ``tenant_config.status``).
        """

    @abc.abstractmethod
    async def set_disabled(
        self,
        *,
        tenant_id: UUID,
        agent_name: str,
        disabled: bool,
        reason: str | None,
        disabled_by: str | None,
    ) -> AgentDisableRecord:
        """Upsert the kill-switch flag for ``(tenant_id, agent_name)``.

        Insert-or-update: the first write for an agent inserts the row; later
        writes merge. When ``disabled`` is ``True`` the ``disabled_at`` /
        ``disabled_by`` / ``reason`` are stamped; when ``False`` they are
        cleared (a clean re-enable). Returns the resulting record.
        """
