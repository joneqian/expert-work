"""``GET /v1/agents/schema`` — the AgentSpec JSON Schema (Stream S, Mini-ADR S-1).

The visual manifest editor renders its form straight from this schema, so the
form never drifts from the backend contract. Read-only; ``by_alias=True`` emits
``apiVersion`` (the manifest's camelCase root field). Computed once at import.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from helix_agent.protocol import AgentSpec

_AGENT_SPEC_SCHEMA: dict[str, Any] = AgentSpec.model_json_schema(by_alias=True)


def build_agent_schema_router() -> APIRouter:
    router = APIRouter(prefix="/v1/agents", tags=["agents"])

    @router.get("/schema")
    async def get_agent_schema() -> dict[str, object]:
        return {"success": True, "data": _AGENT_SPEC_SCHEMA, "error": None}

    return router
