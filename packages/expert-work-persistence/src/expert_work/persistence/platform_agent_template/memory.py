"""In-memory :class:`PlatformAgentTemplateStore` — Stream Agent-Templates (M1)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from expert_work.persistence.platform_agent_template.base import (
    PlatformAgentTemplateAlreadyExistsError,
    PlatformAgentTemplateNotFoundError,
    PlatformAgentTemplateStore,
    compute_spec_sha256,
)
from expert_work.protocol import (
    PlatformAgentTemplatePatch,
    PlatformAgentTemplateRecord,
    PlatformAgentTemplateStatus,
    PlatformAgentTemplateUpsert,
)
from expert_work.protocol.agent_spec import AgentSpec


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class InMemoryPlatformAgentTemplateStore(PlatformAgentTemplateStore):
    """Dict-backed template store keyed by ``(name, version)``; lock-guarded."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], PlatformAgentTemplateRecord] = {}
        self._lock = asyncio.Lock()

    async def create(
        self, *, upsert: PlatformAgentTemplateUpsert, created_by: str
    ) -> PlatformAgentTemplateRecord:
        name = upsert.spec.metadata.name
        version = upsert.spec.metadata.version
        async with self._lock:
            if (name, version) in self._rows:
                raise PlatformAgentTemplateAlreadyExistsError(name=name, version=version)
            now = _utc_now()
            record = PlatformAgentTemplateRecord(
                id=uuid4(),
                tenant_id=None,
                name=name,
                version=version,
                spec=upsert.spec,
                spec_sha256=compute_spec_sha256(upsert.spec),
                display_name=upsert.display_name,
                description=upsert.description,
                category=upsert.category,
                icon=upsert.icon,
                required_tier=upsert.required_tier,
                status=upsert.status,
                enabled=upsert.enabled,
                created_by=created_by,
                created_at=now,
                updated_at=now,
            )
            self._rows[(name, version)] = record
            return record

    async def get(self, *, name: str, version: str) -> PlatformAgentTemplateRecord | None:
        async with self._lock:
            return self._rows.get((name, version))

    async def get_latest(
        self, *, name: str, status: PlatformAgentTemplateStatus | None = None
    ) -> PlatformAgentTemplateRecord | None:
        async with self._lock:
            candidates = [
                r
                for r in self._rows.values()
                if r.name == name and (status is None or r.status == status)
            ]
        if not candidates:
            return None
        return max(candidates, key=lambda r: r.created_at)

    async def list_versions(self, *, name: str) -> list[PlatformAgentTemplateRecord]:
        async with self._lock:
            rows = [r for r in self._rows.values() if r.name == name]
        return sorted(rows, key=lambda r: r.created_at, reverse=True)

    async def list(
        self,
        *,
        category: str | None = None,
        status: PlatformAgentTemplateStatus | None = None,
    ) -> list[PlatformAgentTemplateRecord]:
        async with self._lock:
            rows = [
                r
                for r in self._rows.values()
                if (category is None or r.category == category)
                and (status is None or r.status == status)
            ]
        return sorted(rows, key=lambda r: (r.name, -r.created_at.timestamp()))

    async def update_spec(
        self,
        *,
        name: str,
        version: str,
        spec: AgentSpec,
        updated_by: str,
    ) -> PlatformAgentTemplateRecord | None:
        async with self._lock:
            existing = self._rows.get((name, version))
            if existing is None:
                return None
            updated = existing.model_copy(
                update={
                    "spec": spec,
                    "spec_sha256": compute_spec_sha256(spec),
                    "created_by": updated_by,
                    "updated_at": _utc_now(),
                }
            )
            self._rows[(name, version)] = updated
            return updated

    async def update_meta(
        self, *, name: str, version: str, patch: PlatformAgentTemplatePatch
    ) -> PlatformAgentTemplateRecord | None:
        async with self._lock:
            existing = self._rows.get((name, version))
            if existing is None:
                return None
            changes: dict[str, object] = {"updated_at": _utc_now()}
            if patch.display_name is not None:
                changes["display_name"] = patch.display_name
            if patch.description is not None:
                changes["description"] = patch.description
            if patch.category is not None:
                changes["category"] = patch.category
            if patch.icon is not None:
                changes["icon"] = patch.icon
            if patch.required_tier is not None:
                changes["required_tier"] = patch.required_tier
            if patch.status is not None:
                changes["status"] = patch.status
            if patch.enabled is not None:
                changes["enabled"] = patch.enabled
            # Re-validate the merged row (model_copy skips validators) for SQL parity.
            updated = PlatformAgentTemplateRecord.model_validate(
                existing.model_copy(update=changes).model_dump()
            )
            self._rows[(name, version)] = updated
            return updated

    async def delete(self, *, name: str, version: str) -> None:
        async with self._lock:
            if (name, version) not in self._rows:
                raise PlatformAgentTemplateNotFoundError(name=name, version=version)
            del self._rows[(name, version)]
