"""Per-(tenant, user) MCP OAuth pool — Stream MCP-OAUTH (OA-3b).

A user's ``connected`` :class:`mcp_oauth_connection` rows are built into a
per-user :class:`MCPServerPool` and reused across that user's agent builds.

Each OAuth connection becomes a **bearer**-style ``MCPServerConfig`` whose
``token_ref`` is the connection's stored ``access_token_ref`` — so the existing
``_build_mcp_client`` bearer path resolves the (already-refreshed-at-write-time)
access token from the secret store and injects ``Authorization: Bearer``. No
oauth2 client branch is needed.

Tokens that have expired are skipped (proactive refresh lands in OA-6); an
expired connector simply isn't attached, so the rest of the agent still builds.

Concurrency mirrors :class:`TenantMcpPoolService` (Stream V-D): a per-key lock
deduplicates builds, and a generation counter + rebuild-on-conflict closes the
lost-invalidation race without ever serving a closed pool.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID

from control_plane.tenant_scope import bypass_rls_session
from helix_agent.persistence import McpConnectorCatalogStore, McpOAuthConnectionStore
from helix_agent.protocol import McpOAuthConnectionRecord
from orchestrator.tools.mcp import (
    MCPClient,
    MCPServerConfig,
    MCPServerPool,
    MCPServerPoolLimitError,
)

logger = logging.getLogger("helix.control_plane.user_mcp_oauth_pool")

# (tenant_id, user_id) -> that user's connected-OAuth pool.
UserMcpOAuthPoolProvider = Callable[[UUID, str], Awaitable[MCPServerPool]]
McpClientFactory = Callable[[MCPServerConfig], Awaitable[MCPClient]]
Clock = Callable[[], datetime]

_MAX_BUILD_ATTEMPTS = 5


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class UserMcpOAuthPoolService:
    """Caches one :class:`MCPServerPool` per (tenant, user), from OAuth connections."""

    def __init__(
        self,
        *,
        oauth_store: McpOAuthConnectionStore,
        catalog_store: McpConnectorCatalogStore,
        client_factory: McpClientFactory,
        clock: Clock = _utc_now,
    ) -> None:
        self._oauth_store = oauth_store
        self._catalog_store = catalog_store
        self._client_factory = client_factory
        self._clock = clock
        self._pools: dict[tuple[UUID, str], MCPServerPool] = {}
        self._locks_guard = asyncio.Lock()
        self._key_locks: dict[tuple[UUID, str], asyncio.Lock] = {}
        self._generation: dict[tuple[UUID, str], int] = {}

    async def _key_lock(self, key: tuple[UUID, str]) -> asyncio.Lock:
        async with self._locks_guard:
            lock = self._key_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._key_locks[key] = lock
            return lock

    def _usable(self, record: McpOAuthConnectionRecord) -> bool:
        if record.status != "connected" or not record.access_token_ref:
            return False
        # Expired access token: skip (OA-6 adds proactive refresh). Without a
        # refresh path here, attaching it would just fail at call time.
        return not (
            record.token_expires_at is not None and record.token_expires_at <= self._clock()
        )

    async def _record_to_config(self, record: McpOAuthConnectionRecord) -> MCPServerConfig | None:
        # Transport comes from the catalog entry (NULL-tenant → bypass RLS). The
        # FK is ON DELETE CASCADE, so a live connection always has its entry.
        async with bypass_rls_session():
            entry = await self._catalog_store.get_by_id(record.catalog_id)
        if entry is None:
            return None
        return MCPServerConfig(
            name=record.name,
            transport=entry.transport,
            url=record.resolved_url,
            auth_type="bearer",
            auth_config={"token_ref": record.access_token_ref or ""},
            timeout_s=30.0,
        )

    async def get_or_build(self, tenant_id: UUID, user_id: str) -> MCPServerPool:
        """Return the user's OAuth pool, building (and caching) on miss."""
        key = (tenant_id, user_id)
        for attempt in range(_MAX_BUILD_ATTEMPTS):
            last_attempt = attempt == _MAX_BUILD_ATTEMPTS - 1
            lock = await self._key_lock(key)
            async with lock:
                cached = self._pools.get(key)
                if cached is not None:
                    return cached
                async with self._locks_guard:
                    gen = self._generation.get(key, 0)
                pool = MCPServerPool()
                records = await self._oauth_store.list_for_user(
                    tenant_id=tenant_id, user_id=user_id
                )
                for record in records:
                    if not self._usable(record):
                        continue
                    config = await self._record_to_config(record)
                    if config is None:
                        continue
                    try:
                        client = await self._client_factory(config)
                        await pool.add(record.name, client)
                    except MCPServerPoolLimitError:
                        try:
                            await client.close()
                        except Exception:
                            logger.warning("user_mcp_oauth_pool.cap_orphan_close_failed")
                        logger.warning("user_mcp_oauth_pool.server_cap_reached")
                        break
                    except Exception:
                        logger.warning("user_mcp_oauth_pool.server_build_failed")
                async with self._locks_guard:
                    if self._generation.get(key, 0) == gen:
                        self._pools[key] = pool
                        return pool
                    if last_attempt:
                        return pool
            try:
                await pool.close_all()
            except Exception:
                logger.warning("user_mcp_oauth_pool.stale_close_failed")
        raise RuntimeError("get_or_build: retry loop exited")  # pragma: no cover

    async def invalidate(self, tenant_id: UUID, user_id: str) -> None:
        """Close + drop the (tenant, user) cached pool (next build rebuilds)."""
        key = (tenant_id, user_id)
        async with self._locks_guard:
            self._generation[key] = self._generation.get(key, 0) + 1
            pool = self._pools.pop(key, None)
        if pool is not None:
            try:
                await pool.close_all()
            except Exception:
                logger.warning("user_mcp_oauth_pool.invalidate_close_failed")

    async def close_all(self) -> None:
        """Close every cached pool (app shutdown)."""
        async with self._locks_guard:
            pools = list(self._pools.values())
            self._pools.clear()
            for key in list(self._key_locks):
                self._generation[key] = self._generation.get(key, 0) + 1
            self._key_locks.clear()
        for pool in pools:
            try:
                await pool.close_all()
            except Exception as exc:
                logger.warning("user_mcp_oauth_pool.close_all_failed", exc_info=exc)
