"""Per-tenant remote MCP server pool — Stream V-D (Mini-ADR V-4).

A tenant's registered remote MCP servers (``tenant_mcp_server``) are built
into a per-tenant :class:`MCPServerPool` on first use and reused across agent
builds. The pool is invalidated (closed + dropped) when the tenant's registry
changes (the registration API calls :meth:`invalidate`) and all pools are
closed at app shutdown (:meth:`close_all`).

Decoupling: the orchestrator never imports this — the agent builder receives a
``Callable`` provider bound to this service (mirrors ``mcp_allowlist_provider``).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from uuid import UUID

from helix_agent.persistence import TenantMcpServerStore
from helix_agent.protocol import TenantMcpServerRecord
from helix_agent.runtime.secret_store import SecretStore
from orchestrator.tools.mcp import (
    MCPClient,
    MCPServerConfig,
    MCPServerPool,
    MCPServerPoolLimitError,
)

logger = logging.getLogger("helix.control_plane.tenant_mcp_pool")

# Provider handed to the agent builder: tenant_id -> that tenant's remote pool.
TenantMcpPoolProvider = Callable[[UUID], Awaitable[MCPServerPool]]

# Factory so tests can inject a RecordingMCPClient instead of real transports.
McpClientFactory = Callable[[MCPServerConfig], Awaitable[MCPClient]]


def _record_to_config(record: TenantMcpServerRecord) -> MCPServerConfig:
    """Map a registry record to an orchestrator :class:`MCPServerConfig`.

    Bearer auth carries the ``token_ref`` so the client builder resolves it
    via the SecretStore (the value never lives on the config — Mini-ADR U-11).
    """
    auth_config: dict[str, str] = {}
    if record.auth_type == "bearer" and record.token_secret_ref is not None:
        auth_config["token_ref"] = record.token_secret_ref
    return MCPServerConfig(
        name=record.name,
        transport=record.transport,
        url=record.url,
        auth_type=record.auth_type,
        auth_config=auth_config,
        timeout_s=record.timeout_s,
    )


class TenantMcpPoolService:
    """Caches one :class:`MCPServerPool` per tenant, built from the registry."""

    def __init__(
        self,
        *,
        store: TenantMcpServerStore,
        secret_store: SecretStore | None,
        client_factory: McpClientFactory,
    ) -> None:
        self._store = store
        self._secret_store = secret_store
        self._client_factory = client_factory
        self._pools: dict[UUID, MCPServerPool] = {}
        self._locks_guard = asyncio.Lock()  # guards _pools + _tenant_locks dict mutations
        self._tenant_locks: dict[UUID, asyncio.Lock] = {}

    async def _tenant_lock(self, tenant_id: UUID) -> asyncio.Lock:
        async with self._locks_guard:
            lock = self._tenant_locks.get(tenant_id)
            if lock is None:
                lock = asyncio.Lock()
                self._tenant_locks[tenant_id] = lock
            return lock

    async def get_or_build(self, tenant_id: UUID) -> MCPServerPool:
        """Return the tenant's remote pool, building (and caching) on miss.

        Uses a per-tenant lock so different tenants build in parallel while
        still deduplicating concurrent builds for the same tenant.  A server
        that fails to connect is skipped (logged, no tenant-derived values) so
        one bad server cannot break the whole agent build.  When the server cap
        is hit the just-opened client is closed before breaking — it was never
        added to the pool so ``pool.close_all`` cannot reach it.
        """
        lock = await self._tenant_lock(tenant_id)
        async with lock:
            cached = self._pools.get(tenant_id)
            if cached is not None:
                return cached
            pool = MCPServerPool()
            records = await self._store.list_for_tenant(tenant_id=tenant_id)
            for record in records:
                if not record.enabled:
                    continue
                try:
                    client = await self._client_factory(_record_to_config(record))
                    await pool.add(record.name, client)
                except MCPServerPoolLimitError:
                    # Cap reached: close the just-opened client (it was never
                    # added, so pool.close_all can't reach it) and stop —
                    # further adds would also be rejected.
                    try:
                        await client.close()
                    except Exception:
                        logger.warning("tenant_mcp_pool.cap_orphan_close_failed")
                    logger.warning("tenant_mcp_pool.server_cap_reached")
                    break
                except Exception:
                    logger.warning("tenant_mcp_pool.server_build_failed")
            self._pools[tenant_id] = pool
            return pool

    async def invalidate(self, tenant_id: UUID) -> None:
        """Close + drop the tenant's cached pool (next build rebuilds it)."""
        async with self._locks_guard:
            pool = self._pools.pop(tenant_id, None)
        if pool is not None:
            try:
                await pool.close_all()
            except Exception:
                logger.warning("tenant_mcp_pool.invalidate_close_failed")

    async def close_all(self) -> None:
        """Close every cached pool (app shutdown)."""
        async with self._locks_guard:
            pools = list(self._pools.values())
            self._pools.clear()
            self._tenant_locks.clear()
        for pool in pools:
            try:
                await pool.close_all()
            except Exception as exc:
                logger.warning(
                    "tenant_mcp_pool.close_all_failed pool_count=%d",
                    len(pools),
                    exc_info=exc,
                )
