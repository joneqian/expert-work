"""Platform-configured MCP server pool — Stream MCP platform-servers (P1b).

The platform-curated catalog (``mcp_connector_catalog``, NULL-tenant) now holds
fully-configured **shared** MCP servers (P1a): ``none`` / ``bearer`` entries the
platform admin set up once (a ``bearer`` entry carries the platform's own token
via ``bearer_token_ref``). This service builds those rows into a single,
process-global :class:`MCPServerPool` that the agent builder layers at platform
precedence (above tenant + per-user pools), gated per-tenant by
``tenant_config.mcp_allowlist`` exactly like the operator file pool.

``oauth2`` catalog entries are **not** here — they are per-user and flow through
``user_mcp_oauth_pool`` after each user authorizes.

The pool is rebuilt lazily and invalidated (closed + dropped) when the catalog
changes (the catalog admin API calls :meth:`invalidate`); it is closed at app
shutdown (:meth:`close_all`). Catalog rows are NULL-tenant, so the store call
runs inside ``bypass_rls_session()`` (the W-8 trap — a scoped session hides
them). Decoupling mirrors ``tenant_mcp_pool``: the orchestrator never imports
this; the agent builder receives a ``Callable`` provider bound to the service.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from control_plane.tenant_scope import bypass_rls_session
from helix_agent.persistence import McpConnectorCatalogStore
from helix_agent.protocol import McpConnectorCatalogRecord
from orchestrator.tools.mcp import (
    MCPClient,
    MCPServerConfig,
    MCPServerPool,
    MCPServerPoolLimitError,
)

logger = logging.getLogger("helix.control_plane.platform_mcp_pool")

# Bounded rebuild attempts when invalidation keeps landing mid-build.
_MAX_BUILD_ATTEMPTS = 5

# Provider handed to the agent builder: () -> the platform pool.
PlatformMcpPoolProvider = Callable[[], Awaitable[MCPServerPool]]

# Factory so tests can inject a RecordingMCPClient instead of real transports.
McpClientFactory = Callable[[MCPServerConfig], Awaitable[MCPClient]]


def _record_to_config(record: McpConnectorCatalogRecord) -> MCPServerConfig:
    """Map a catalog row to an orchestrator :class:`MCPServerConfig`.

    ``bearer`` carries the platform token's ``token_ref`` so the client builder
    resolves it via the SecretStore (the value never lives on the config). The
    catalog ``url_template`` is a concrete URL in the platform-server model.
    """
    auth_config: dict[str, str] = {}
    if record.auth_type == "bearer" and record.bearer_token_ref is not None:
        auth_config["token_ref"] = record.bearer_token_ref
    # Runtime tuning (NULL = orchestrator defaults). Only override timeout_s when
    # the row carries one, so MCPServerConfig's own default still applies.
    extra: dict[str, float] = {}
    if record.timeout_s is not None:
        extra["timeout_s"] = record.timeout_s
    return MCPServerConfig(
        name=record.name,
        transport=record.transport,
        url=record.url_template,
        auth_type=record.auth_type,
        auth_config=auth_config,
        sse_read_timeout_s=record.sse_read_timeout_s,
        **extra,
    )


def _is_shared_server(record: McpConnectorCatalogRecord) -> bool:
    """A catalog row that belongs in the shared platform pool.

    Enabled + ``none``/``bearer`` (oauth2 is per-user). A ``bearer`` entry must
    carry a platform ``bearer_token_ref`` — a legacy tenant-fills bearer (one
    ``auth_schema`` secret field, no platform token) is *not* a shared server
    and is skipped here (it instantiates per-tenant the old way until P2).
    """
    if not record.enabled:
        return False
    if record.auth_type == "none":
        return True
    if record.auth_type == "bearer":
        return record.bearer_token_ref is not None
    return False  # oauth2 → user_mcp_oauth_pool


class PlatformMcpPoolService:
    """Caches the single process-global platform MCP server pool."""

    def __init__(
        self,
        *,
        store: McpConnectorCatalogStore,
        client_factory: McpClientFactory,
    ) -> None:
        self._store = store
        self._client_factory = client_factory
        self._pool: MCPServerPool | None = None
        self._guard = asyncio.Lock()  # guards _pool + _generation
        self._build_lock = asyncio.Lock()  # dedups concurrent builds
        self._generation = 0

    async def get_or_build(self) -> MCPServerPool:
        """Return the platform pool, building (and caching) on miss.

        A server that fails to connect is skipped (logged, no row-derived values)
        so one bad server cannot break the agent build. When the server cap is
        hit the just-opened client is closed before breaking. A generation
        counter guards the lost-invalidation race (parity with tenant pool):
        ``invalidate`` bumps it under ``_guard`` while a build may be in flight;
        the build captures it before building and re-checks before caching, and
        retries (bounded) if it changed.
        """
        for attempt in range(_MAX_BUILD_ATTEMPTS):
            last_attempt = attempt == _MAX_BUILD_ATTEMPTS - 1
            async with self._build_lock:
                cached = self._pool
                if cached is not None:
                    return cached
                async with self._guard:
                    gen = self._generation
                pool = MCPServerPool()
                async with bypass_rls_session():
                    records = await self._store.list()
                for record in records:
                    if not _is_shared_server(record):
                        continue
                    try:
                        client = await self._client_factory(_record_to_config(record))
                        await pool.add(record.name, client)
                    except MCPServerPoolLimitError:
                        try:
                            await client.close()
                        except Exception:
                            logger.warning("platform_mcp_pool.cap_orphan_close_failed")
                        logger.warning("platform_mcp_pool.server_cap_reached")
                        break
                    except Exception:
                        logger.warning("platform_mcp_pool.server_build_failed")
                async with self._guard:
                    if self._generation == gen:
                        self._pool = pool
                        return pool
                    if last_attempt:
                        return pool  # serve fresh-but-uncached; next caller rebuilds
            # Invalidation landed mid-build: close the orphan and retry.
            try:
                await pool.close_all()
            except Exception:
                logger.warning("platform_mcp_pool.stale_close_failed")
        raise RuntimeError("get_or_build: retry loop exited without returning")  # pragma: no cover

    async def invalidate(self) -> None:
        """Close + drop the cached pool (next build rebuilds from the catalog)."""
        async with self._guard:
            self._generation += 1
            pool = self._pool
            self._pool = None
        if pool is not None:
            try:
                await pool.close_all()
            except Exception:
                logger.warning("platform_mcp_pool.invalidate_close_failed")

    async def close_all(self) -> None:
        """Close the cached pool (app shutdown)."""
        async with self._guard:
            self._generation += 1
            pool = self._pool
            self._pool = None
        if pool is not None:
            try:
                await pool.close_all()
            except Exception:
                logger.warning("platform_mcp_pool.close_all_failed")
