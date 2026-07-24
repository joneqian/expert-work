"""Integration: 0133 mcp_oauth_connection.catalog_id FK CASCADE → RESTRICT.

删除接口卫生修复第 2 批 Task 8。app 层的 ``mcp_catalog.py`` DELETE 端点已经
用 ``McpOAuthConnectionStore.count_for_catalog`` 挡住带活跃 OAuth 连接的目录
删除(见 ``test_mcp_catalog_api.py``)——这里验证 DB 级兜底本身:即使 app 闸
被绕过、直接调用 ``McpConnectorCatalogStore.delete()``,只要还有
``mcp_oauth_connection`` 行引用该目录条目,RESTRICT 约束也会让删除失败并映射
成 ``McpConnectorCatalogInUseError``(与既有 tenant_mcp_server RESTRICT 走同一
条 ``IntegrityError`` 捕获路径,见 ``mcp_connector_catalog/sql.py::delete``)。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.postgres import PostgresContainer

from expert_work.persistence import (
    DatabaseConfig,
    McpConnectorCatalogInUseError,
    create_async_engine_from_config,
    create_async_session_factory,
)
from expert_work.persistence.mcp_connector_catalog import SqlMcpConnectorCatalogStore
from expert_work.persistence.mcp_oauth_connection import SqlMcpOAuthConnectionStore
from expert_work.protocol import (
    McpConnectorAuthField,
    McpConnectorAuthSchema,
    McpConnectorCatalogUpsert,
    TenantPlan,
)

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

_Fixture = tuple[SqlMcpConnectorCatalogStore, SqlMcpOAuthConnectionStore, AsyncEngine]


def _sync_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+psycopg").replace("postgresql://", "postgresql+psycopg://", 1)


def _async_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture
def stores(postgres_container: PostgresContainer) -> Iterator[_Fixture]:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")

    engine = create_async_engine_from_config(DatabaseConfig(dsn=_async_dsn(postgres_container)))
    sf = create_async_session_factory(engine)
    yield SqlMcpConnectorCatalogStore(sf), SqlMcpOAuthConnectionStore(sf), engine


async def _make_catalog_entry(catalog_store: SqlMcpConnectorCatalogStore) -> UUID:
    upsert = McpConnectorCatalogUpsert(
        name="fk-restrict-probe",
        display_name="FK Restrict Probe",
        transport="streamable_http",
        url_template="https://mcp.example.com/{org}/sse",
        auth_type="bearer",
        auth_schema=McpConnectorAuthSchema(
            fields=[
                McpConnectorAuthField(key="token", label="API Token", kind="secret"),
                McpConnectorAuthField(key="org", label="Organization", kind="param"),
            ]
        ),
        required_tier=TenantPlan.FREE,
    )
    created = await catalog_store.create(upsert=upsert, actor_id="sysadmin")
    return created.id


@pytest.mark.asyncio
async def test_delete_blocked_while_oauth_connection_references_catalog(
    stores: _Fixture,
) -> None:
    catalog_store, oauth_store, engine = stores
    try:
        catalog_id = await _make_catalog_entry(catalog_store)
        connection = await oauth_store.create(
            tenant_id=UUID(int=1),
            user_id="u1",
            catalog_id=catalog_id,
            name="linear",
            resolved_url="https://mcp.linear.app/sse",
        )

        # App-level guard bypassed — a direct store.delete() must still be
        # blocked by the (post-0133) RESTRICT FK, not silently cascade.
        with pytest.raises(McpConnectorCatalogInUseError):
            await catalog_store.delete(catalog_id)

        # The catalog row survives the failed delete attempt.
        assert await catalog_store.get_by_id(catalog_id) is not None

        # Clearing the blocking connection unblocks the delete.
        await oauth_store.delete(connection_id=connection.id, tenant_id=UUID(int=1), user_id="u1")
        await catalog_store.delete(catalog_id)
        assert await catalog_store.get_by_id(catalog_id) is None
    finally:
        await engine.dispose()
