"""A.8 — ``HTTPSupervisorClient`` injects W3C trace context on the wire.

The inject half of the cross-service propagation contract (subsystems/20
§ 5.8). The matching extract half lives in the sandbox-supervisor suite
(``test_trace_middleware.py``); the two meet at the ``traceparent`` wire
format, which helix-common's propagation tests validate independently.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import httpx
import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from helix_agent.common.observability import (
    TRACEPARENT_HEADER,
    HelixComponent,
    current_trace_id_hex,
    helix_span,
    init_tracing,
)
from orchestrator.tools.sandbox import HTTPSupervisorClient

pytestmark = pytest.mark.asyncio


@pytest.fixture
def tracing_setup() -> Iterator[None]:
    provider = init_tracing(
        service_name="sandbox-trace-test",
        env="test",
        span_processor=SimpleSpanProcessor(InMemorySpanExporter()),
    )
    try:
        yield
    finally:
        provider.shutdown()


def _capturing_client(seen: dict[str, httpx.Headers]) -> HTTPSupervisorClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        return httpx.Response(200, json={"sandbox_id": str(uuid4())})

    return HTTPSupervisorClient(
        base_url="http://supervisor", transport=httpx.MockTransport(handler)
    )


async def test_post_injects_traceparent_matching_active_span(tracing_setup: None) -> None:
    seen: dict[str, httpx.Headers] = {}
    client = _capturing_client(seen)

    with helix_span(HelixComponent.ORCHESTRATOR, "tool_call"):
        active_trace_id = current_trace_id_hex()
        await client.acquire(tenant_id=uuid4(), thread_id="t")

    assert active_trace_id is not None
    traceparent = seen["headers"].get(TRACEPARENT_HEADER)
    assert traceparent is not None
    # traceparent = "00-<32-hex trace_id>-<16-hex span_id>-<flags>"
    assert traceparent.split("-")[1] == active_trace_id


async def test_workspace_get_injects_traceparent(tracing_setup: None) -> None:
    seen: dict[str, httpx.Headers] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        return httpx.Response(200, content=b"file-bytes")

    client = HTTPSupervisorClient(
        base_url="http://supervisor", transport=httpx.MockTransport(handler)
    )
    with helix_span(HelixComponent.ORCHESTRATOR, "tool_call"):
        active_trace_id = current_trace_id_hex()
        await client.read_workspace_file(tenant_id=uuid4(), user_id=uuid4(), path="a.txt")

    assert active_trace_id is not None
    assert seen["headers"].get(TRACEPARENT_HEADER, "").split("-")[1] == active_trace_id


async def test_no_active_span_omits_traceparent(tracing_setup: None) -> None:
    # No surrounding span (OTel active but no span) → nothing to propagate;
    # external egress and uninstrumented call sites stay header-free.
    seen: dict[str, httpx.Headers] = {}
    client = _capturing_client(seen)

    await client.acquire(tenant_id=uuid4(), thread_id="t")

    assert TRACEPARENT_HEADER not in seen["headers"]
