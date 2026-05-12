"""``/metrics`` endpoint — Prometheus text exposition from Stream A.9.

Mounted on the bare app (not under ``/v1``) so scrape configs need
exactly one path regardless of API versioning.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

from helix_agent.common.observability import metrics_text


def build_metrics_router() -> APIRouter:
    router = APIRouter(tags=["metrics"])

    @router.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        body, content_type = metrics_text()
        return Response(content=body, media_type=content_type)

    return router
