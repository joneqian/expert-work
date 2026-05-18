"""Helpers to lift per-run objects out of ``RunnableConfig``.

Shared by the graph nodes (``builder``, ``planner``) — kept in its own
module so neither node module has to import the other (no import cycle).
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from helix_agent.runtime.cancellation import CANCELLATION_TOKEN_KEY, CancellationToken


def cancellation_token(config: RunnableConfig) -> CancellationToken:
    """Lift the run's :class:`CancellationToken` out of ``config``.

    The token travels via ``config["configurable"]`` (not ``AgentState``
    — a live :class:`asyncio.Event` is not checkpoint-serialisable).
    When absent — dev / unit-test path that never cancels — a fresh,
    never-cancelled token is returned so node code is uniform.
    """
    configurable = config.get("configurable") or {}
    token = configurable.get(CANCELLATION_TOKEN_KEY)
    if isinstance(token, CancellationToken):
        return token
    return CancellationToken()
