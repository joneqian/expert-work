# ============================================================
# Adapted from bytedance/deer-flow @ 813d3c94efa7fdea6aafcb4f459304db91fcaed0
# Source: backend/packages/harness/deerflow/runtime/stream_bridge/async_provider.py
# License: MIT (see vendor LICENSE)
# Modifications:
#   - Drop DeerFlow's app_config / get_stream_bridge_config coupling;
#     accept backend + kwargs explicitly (DI pattern)
#   - Redis backend signalled as M1+ TODO via NotImplementedError
# Last sync: 2026-05-11
# ============================================================

"""Factory for ``StreamBridge`` instances."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator
from typing import Literal

from helix_agent.runtime.stream_bridge.base import StreamBridge

logger = logging.getLogger(__name__)

StreamBridgeBackend = Literal["memory", "redis"]


@contextlib.asynccontextmanager
async def make_stream_bridge(
    backend: StreamBridgeBackend = "memory",
    *,
    queue_maxsize: int = 256,
) -> AsyncIterator[StreamBridge]:
    """Yield a configured ``StreamBridge``; clean up on exit.

    :param backend: ``"memory"`` (M0 default) or ``"redis"`` (M1+ TODO).
    :param queue_maxsize: per-run event buffer cap; older events drop on overflow.
    """
    # Widen so the trailing "unknown backend" branch is reachable to mypy.
    bk: str = backend
    if bk == "memory":
        from helix_agent.runtime.stream_bridge.memory import InMemoryStreamBridge

        bridge: StreamBridge = InMemoryStreamBridge(queue_maxsize=queue_maxsize)
        logger.info("stream_bridge.memory.init queue_maxsize=%d", queue_maxsize)
        try:
            yield bridge
        finally:
            await bridge.close()
        return

    if bk == "redis":
        msg = "stream_bridge backend 'redis' is M1+ work; not yet implemented"
        raise NotImplementedError(msg)

    msg = f"unknown stream_bridge backend: {bk!r}"
    raise ValueError(msg)
