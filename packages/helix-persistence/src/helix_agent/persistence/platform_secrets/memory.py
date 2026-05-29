"""In-memory :class:`PlatformSecretStore` — Stream P (Mini-ADR P-7)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from helix_agent.persistence.platform_secrets.base import PlatformSecretStore
from helix_agent.protocol import (
    PlatformProviderSecretRecord,
    PlatformToolSecretRecord,
    Provider,
    Tool,
)


def _now() -> datetime:
    return datetime.now(tz=UTC)


class InMemoryPlatformSecretStore(PlatformSecretStore):
    """Dict-backed store; lock-guarded for asyncio safety."""

    def __init__(self) -> None:
        self._providers: dict[Provider, PlatformProviderSecretRecord] = {}
        self._tools: dict[Tool, PlatformToolSecretRecord] = {}
        self._lock = asyncio.Lock()

    async def list_providers(self) -> list[PlatformProviderSecretRecord]:
        async with self._lock:
            return list(self._providers.values())

    async def get_provider(self, provider: Provider) -> PlatformProviderSecretRecord | None:
        async with self._lock:
            return self._providers.get(provider)

    async def upsert_provider(
        self,
        *,
        provider: Provider,
        secret_ref: str,
        enabled: bool,
        actor_id: str,
    ) -> PlatformProviderSecretRecord:
        now = _now()
        async with self._lock:
            existing = self._providers.get(provider)
            created_at = existing.created_at if existing is not None else now
            record = PlatformProviderSecretRecord(
                provider=provider,
                secret_ref=secret_ref,
                enabled=enabled,
                created_at=created_at,
                updated_at=now,
                updated_by=actor_id,
            )
            self._providers[provider] = record
            return record

    async def delete_provider(self, provider: Provider) -> bool:
        async with self._lock:
            return self._providers.pop(provider, None) is not None

    async def list_tools(self) -> list[PlatformToolSecretRecord]:
        async with self._lock:
            return list(self._tools.values())

    async def get_tool(self, tool: Tool) -> PlatformToolSecretRecord | None:
        async with self._lock:
            return self._tools.get(tool)

    async def upsert_tool(
        self,
        *,
        tool: Tool,
        secret_ref: str,
        enabled: bool,
        actor_id: str,
    ) -> PlatformToolSecretRecord:
        now = _now()
        async with self._lock:
            existing = self._tools.get(tool)
            created_at = existing.created_at if existing is not None else now
            record = PlatformToolSecretRecord(
                tool=tool,
                secret_ref=secret_ref,
                enabled=enabled,
                created_at=created_at,
                updated_at=now,
                updated_by=actor_id,
            )
            self._tools[tool] = record
            return record

    async def delete_tool(self, tool: Tool) -> bool:
        async with self._lock:
            return self._tools.pop(tool, None) is not None
