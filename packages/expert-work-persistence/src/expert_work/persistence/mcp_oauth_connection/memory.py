"""In-memory :class:`McpOAuthConnectionStore` — tests / dev (Stream MCP-OAUTH)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

from expert_work.persistence.mcp_oauth_connection.base import (
    McpOAuthConnectionAlreadyExistsError,
    McpOAuthConnectionNotFoundError,
    McpOAuthConnectionStore,
)
from expert_work.protocol import McpOAuthConnectionPatch, McpOAuthConnectionRecord


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class InMemoryMcpOAuthConnectionStore(McpOAuthConnectionStore):
    """Dict-backed per-user OAuth connection store."""

    def __init__(self) -> None:
        self._rows: dict[UUID, McpOAuthConnectionRecord] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        *,
        tenant_id: UUID,
        user_id: str,
        catalog_id: UUID,
        name: str,
        resolved_url: str,
        scopes: str = "",
        oauth_state: str | None = None,
        pkce_verifier: str | None = None,
        redirect_uri: str | None = None,
    ) -> McpOAuthConnectionRecord:
        async with self._lock:
            for r in self._rows.values():
                if r.tenant_id == tenant_id and r.user_id == user_id and r.catalog_id == catalog_id:
                    raise McpOAuthConnectionAlreadyExistsError(
                        tenant_id=tenant_id, user_id=user_id, catalog_id=catalog_id
                    )
            now = _utc_now()
            record = McpOAuthConnectionRecord(
                id=uuid4(),
                tenant_id=tenant_id,
                user_id=user_id,
                catalog_id=catalog_id,
                name=name,
                status="pending",
                resolved_url=resolved_url,
                scopes=scopes,
                oauth_state=oauth_state,
                pkce_verifier=pkce_verifier,
                redirect_uri=redirect_uri,
                created_at=now,
                updated_at=now,
            )
            self._rows[record.id] = record
            return record

    async def get(
        self, *, connection_id: UUID, tenant_id: UUID, user_id: str
    ) -> McpOAuthConnectionRecord | None:
        r = self._rows.get(connection_id)
        if r is None or r.tenant_id != tenant_id or r.user_id != user_id:
            return None
        return r

    async def get_for_connector(
        self, *, tenant_id: UUID, user_id: str, catalog_id: UUID
    ) -> McpOAuthConnectionRecord | None:
        for r in self._rows.values():
            if r.tenant_id == tenant_id and r.user_id == user_id and r.catalog_id == catalog_id:
                return r
        return None

    async def get_by_state(
        self, *, tenant_id: UUID, user_id: str, oauth_state: str
    ) -> McpOAuthConnectionRecord | None:
        for r in self._rows.values():
            if r.tenant_id == tenant_id and r.user_id == user_id and r.oauth_state == oauth_state:
                return r
        return None

    async def list_for_user(
        self, *, tenant_id: UUID, user_id: str
    ) -> list[McpOAuthConnectionRecord]:
        rows = [r for r in self._rows.values() if r.tenant_id == tenant_id and r.user_id == user_id]
        return sorted(rows, key=lambda r: r.name)

    async def update(
        self, *, connection_id: UUID, tenant_id: UUID, user_id: str, patch: McpOAuthConnectionPatch
    ) -> McpOAuthConnectionRecord:
        async with self._lock:
            existing = self._rows.get(connection_id)
            if existing is None or existing.tenant_id != tenant_id or existing.user_id != user_id:
                raise McpOAuthConnectionNotFoundError(connection_id=connection_id)
            updates: dict[str, object] = {"updated_at": _utc_now()}
            if patch.status is not None:
                updates["status"] = patch.status
            if patch.access_token_ref is not None:
                updates["access_token_ref"] = patch.access_token_ref
            if patch.refresh_token_ref is not None:
                updates["refresh_token_ref"] = patch.refresh_token_ref
            if patch.token_expires_at is not None:
                updates["token_expires_at"] = patch.token_expires_at
            if patch.scopes is not None:
                updates["scopes"] = patch.scopes
            if patch.last_refresh_at is not None:
                updates["last_refresh_at"] = patch.last_refresh_at
            if patch.last_error is not None:
                updates["last_error"] = patch.last_error
            if patch.clear_flow_state:
                updates["oauth_state"] = None
                updates["pkce_verifier"] = None
            if patch.clear_last_error:
                updates["last_error"] = None
            # Re-validate the merged row (model_copy doesn't run validators) so a
            # cross-field invariant violation rejects before persisting.
            updated = McpOAuthConnectionRecord.model_validate(
                existing.model_copy(update=updates).model_dump()
            )
            self._rows[connection_id] = updated
            return updated

    async def delete(self, *, connection_id: UUID, tenant_id: UUID, user_id: str) -> None:
        async with self._lock:
            existing = self._rows.get(connection_id)
            if existing is None or existing.tenant_id != tenant_id or existing.user_id != user_id:
                raise McpOAuthConnectionNotFoundError(connection_id=connection_id)
            del self._rows[connection_id]

    async def delete_all_for_user(self, *, tenant_id: UUID, user_id: str) -> int:
        async with self._lock:
            victims = [
                cid
                for cid, r in self._rows.items()
                if r.tenant_id == tenant_id and r.user_id == user_id
            ]
            for cid in victims:
                del self._rows[cid]
            return len(victims)
