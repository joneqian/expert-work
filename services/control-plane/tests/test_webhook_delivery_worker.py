"""Unit tests for the HX-9 webhook delivery worker (STREAM-HX § 13)."""

from __future__ import annotations

import hmac
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

import pytest

from control_plane.webhook_delivery_worker import WebhookDeliveryWorker
from helix_agent.persistence import (
    InMemoryWebhookDeliveryStore,
    InMemoryWebhookEndpointStore,
)
from helix_agent.protocol import (
    WebhookDeliveryRecord,
    WebhookDeliveryStatus,
    WebhookEndpointRecord,
)

_NOW = datetime(2026, 6, 13, 12, 0, 0, tzinfo=UTC)
_SECRET = "topsecret-value"


class _FakeSecretStore:
    """Minimal in-memory SecretStore for the worker test."""

    def __init__(self) -> None:
        self._d: dict[str, str] = {}

    async def get(self, name: str, *, version: str | None = None) -> str:
        if name not in self._d:
            from helix_agent.runtime.secret_store import SecretNotFoundError

            raise SecretNotFoundError(name)
        return self._d[name]

    async def put(self, name: str, value: str) -> None:
        self._d[name] = value

    async def list_versions(self, name: str) -> list[str]:
        return ["1"]


class _RecordingPost:
    """A pluggable ``http_post`` that records calls and returns a fixed status
    (or raises, to simulate a transport failure)."""

    def __init__(self, status: int | None = 200, *, raise_exc: bool = False) -> None:
        self.status = status
        self.raise_exc = raise_exc
        self.calls: list[tuple[str, bytes, dict[str, str]]] = []

    async def __call__(self, url: str, body: bytes, headers: dict[str, str]) -> int:
        self.calls.append((url, body, headers))
        if self.raise_exc:
            raise ConnectionError("boom")
        assert self.status is not None
        return self.status


async def _seed(
    *,
    endpoint_enabled: bool = True,
    status: WebhookDeliveryStatus = WebhookDeliveryStatus.PENDING,
    attempt: int = 0,
) -> tuple[
    InMemoryWebhookEndpointStore,
    InMemoryWebhookDeliveryStore,
    _FakeSecretStore,
    WebhookEndpointRecord,
    WebhookDeliveryRecord,
]:
    endpoints = InMemoryWebhookEndpointStore()
    deliveries = InMemoryWebhookDeliveryStore()
    secrets_store = _FakeSecretStore()
    tenant = uuid4()
    endpoint_id = uuid4()
    ref = f"webhook-endpoint/{endpoint_id}"
    await secrets_store.put(ref, _SECRET)
    endpoint = WebhookEndpointRecord(
        id=endpoint_id,
        tenant_id=tenant,
        name="ops",
        url="https://hooks.example.com/ingest",
        event_types=("run.completed",),
        agent_name=None,
        secret_ref=ref,
        enabled=endpoint_enabled,
        source="api",
        created_at=_NOW,
        updated_at=_NOW,
    )
    await endpoints.create(endpoint)
    delivery = WebhookDeliveryRecord(
        id=uuid4(),
        tenant_id=tenant,
        endpoint_id=endpoint_id,
        event_id="run:abc",
        event_type="run.completed",
        run_id=uuid4(),
        payload={"run_id": "abc", "status": "success"},
        status=status,
        attempt=attempt,
        created_at=_NOW,
        updated_at=_NOW,
    )
    await deliveries.create(delivery)
    return endpoints, deliveries, secrets_store, endpoint, delivery


def _worker(endpoints, deliveries, secrets_store, post, **kwargs) -> WebhookDeliveryWorker:
    return WebhookDeliveryWorker(
        delivery_store=deliveries,
        endpoint_store=endpoints,
        secret_store=secrets_store,
        http_post=post,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_2xx_marks_delivered_and_signs_body() -> None:
    endpoints, deliveries, secrets_store, _, delivery = await _seed()
    post = _RecordingPost(status=200)
    result = await _worker(endpoints, deliveries, secrets_store, post).run_once()
    assert result == (1, 0, 0)

    row = await deliveries.get(delivery_id=delivery.id, tenant_id=delivery.tenant_id)
    assert row is not None and row.status is WebhookDeliveryStatus.DELIVERED
    assert row.response_status == 200

    # The signature header is a correct HMAC-SHA256 of the exact body sent.
    url, body, headers = post.calls[0]
    assert url == "https://hooks.example.com/ingest"
    expected = "sha256=" + hmac.new(_SECRET.encode(), body, sha256).hexdigest()
    assert headers["X-Helix-Signature-256"] == expected
    assert headers["X-Helix-Event"] == "run.completed"


@pytest.mark.asyncio
async def test_5xx_schedules_retry_with_backoff() -> None:
    endpoints, deliveries, secrets_store, _, delivery = await _seed()
    post = _RecordingPost(status=503)
    result = await _worker(endpoints, deliveries, secrets_store, post).run_once()
    assert result == (0, 1, 0)

    row = await deliveries.get(delivery_id=delivery.id, tenant_id=delivery.tenant_id)
    assert row is not None
    assert row.status is WebhookDeliveryStatus.RETRYING
    assert row.next_retry_at is not None  # backoff scheduled


@pytest.mark.asyncio
async def test_4xx_dead_letters_without_retry() -> None:
    endpoints, deliveries, secrets_store, _, delivery = await _seed()
    post = _RecordingPost(status=400)
    result = await _worker(endpoints, deliveries, secrets_store, post).run_once()
    assert result == (0, 0, 1)

    row = await deliveries.get(delivery_id=delivery.id, tenant_id=delivery.tenant_id)
    assert row is not None and row.status is WebhookDeliveryStatus.DEAD_LETTER


@pytest.mark.asyncio
async def test_last_attempt_dead_letters() -> None:
    # attempt=4 with max_attempts=5 → next_attempt=5 → spent → dead.
    endpoints, deliveries, secrets_store, _, delivery = await _seed(attempt=4)
    post = _RecordingPost(status=503)
    result = await _worker(endpoints, deliveries, secrets_store, post, max_attempts=5).run_once()
    assert result == (0, 0, 1)
    row = await deliveries.get(delivery_id=delivery.id, tenant_id=delivery.tenant_id)
    assert row is not None and row.status is WebhookDeliveryStatus.DEAD_LETTER


@pytest.mark.asyncio
async def test_transport_error_is_retryable() -> None:
    endpoints, deliveries, secrets_store, _, _ = await _seed()
    post = _RecordingPost(raise_exc=True)
    result = await _worker(endpoints, deliveries, secrets_store, post).run_once()
    assert result == (0, 1, 0)


@pytest.mark.asyncio
async def test_disabled_endpoint_dead_letters() -> None:
    endpoints, deliveries, secrets_store, _, _ = await _seed(endpoint_enabled=False)
    post = _RecordingPost(status=200)
    result = await _worker(endpoints, deliveries, secrets_store, post).run_once()
    assert result == (0, 0, 1)
    assert post.calls == []  # never POSTed to a disabled endpoint


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_threshold() -> None:
    endpoints, deliveries, secrets_store, endpoint, _ = await _seed()
    post = _RecordingPost(status=503)
    worker = _worker(
        endpoints, deliveries, secrets_store, post, max_attempts=100, breaker_threshold=3
    )
    # Enqueue several deliveries to the same endpoint; after 3 failures the
    # breaker opens and further deliveries this run are skipped (no POST).
    for i in range(6):
        await deliveries.create(
            WebhookDeliveryRecord(
                id=uuid4(),
                tenant_id=endpoint.tenant_id,
                endpoint_id=endpoint.id,
                event_id=f"run:{i}",
                event_type="run.completed",
                payload={},
                status=WebhookDeliveryStatus.PENDING,
                attempt=0,
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
    await worker.run_once()
    # Breaker threshold 3 → at most 3 endpoints attempted before it trips;
    # the remaining are skipped, so far fewer than 7 POSTs happen.
    assert len(post.calls) <= 3
