"""``/metrics`` text-exposition smoke tests."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_prometheus_text(client: AsyncClient) -> None:
    # Drive at least one request through the observability middleware so
    # the histogram has a sample.
    await client.get("/healthz/live")
    response = await client.get("/metrics")
    assert response.status_code == 200
    # Prometheus text content-type per OpenMetrics spec.
    assert "text/plain" in response.headers["content-type"]
    body = response.text
    assert "helix_control_plane_http_requests_total" in body
    assert "helix_control_plane_http_request_duration_seconds" in body
