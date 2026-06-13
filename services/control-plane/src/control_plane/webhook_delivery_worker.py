"""HX-9 PR3a — outbound webhook delivery worker (STREAM-HX § 13).

A periodic loop, started from the control-plane lifespan, that drains the
``webhook_delivery`` queue: each ready row is signed (HMAC-SHA256 over the
JSON body) and POSTed to the tenant's registered URL. The signing secret
lives in the :class:`SecretStore` (the row carries only a ref); the request
never carries platform credentials (Mini-ADR HX-J5).

Outcome → state machine (Mini-ADR HX-J2):
- 2xx                       → ``delivered``
- 4xx (config error)        → ``dead_letter`` immediately (retry won't fix)
- 5xx / timeout / network   → exponential backoff retry; ``dead_letter`` once
                              the attempt budget is spent

Per-endpoint circuit breaker (Mini-ADR HX-J4): consecutive failures trip the
breaker so a sick endpoint is skipped for a cooldown rather than retried hot
every cycle — a slow / broken tenant endpoint never back-pressures others.
Per-tenant concurrency cap bounds in-flight deliveries per tenant.

Modelled after :class:`control_plane.memory.dlq_worker.MemoryDLQWorker`
(start / stop / run_once; the loop never raises so the process is never
crashed by the worker). The enqueue side — scanning the 3 source tables
into ``webhook_delivery`` — is HX-9 PR3b.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID

import httpx

from helix_agent.common.observability import helix_counter
from helix_agent.persistence import WebhookDeliveryStore, WebhookEndpointStore
from helix_agent.persistence.rls import bypass_rls_var
from helix_agent.protocol import WebhookDeliveryRecord, WebhookDeliveryStatus
from helix_agent.runtime.secret_store import SecretNotFoundError, SecretStore

logger = logging.getLogger("helix.control_plane.webhook_delivery_worker")

_BATCH_SIZE: int = 100
_MAX_ATTEMPTS: int = 5
#: 1 min → 5 min → 30 min → 2 h → 6 h (same shape as the memory DLQ worker).
_BACKOFF_SCHEDULE: tuple[int, ...] = (60, 5 * 60, 30 * 60, 2 * 3600, 6 * 3600)
#: Consecutive endpoint failures that trip the circuit breaker.
_BREAKER_THRESHOLD: int = 5
#: How long a tripped breaker stays open before a probe is allowed again.
_BREAKER_COOLDOWN_S: int = 300
#: HMAC signature header (mirrors GitHub's ``X-Hub-Signature-256``).
_SIGNATURE_HEADER = "X-Helix-Signature-256"

#: A pluggable POST: ``(url, body, headers) -> status_code``. The default is
#: httpx; tests inject a stub so no real network is touched. Raising signals
#: a transport failure (treated as retryable, like a 5xx).
HttpPost = Callable[[str, bytes, dict[str, str]], Awaitable[int]]

_delivered = helix_counter(
    "helix_webhook_deliveries_succeeded_total",
    "Webhook deliveries that received a 2xx.",
)
_dead_letters = helix_counter(
    "helix_webhook_deliveries_dead_lettered_total",
    "Webhook deliveries abandoned (4xx config error or retry budget spent).",
)
_retries = helix_counter(
    "helix_webhook_deliveries_retried_total",
    "Webhook deliveries scheduled for a backoff retry.",
)
_breaker_skips = helix_counter(
    "helix_webhook_breaker_skips_total",
    "Deliveries skipped because the endpoint's circuit breaker was open.",
)
_cycle_errors = helix_counter(
    "helix_webhook_delivery_cycle_errors_total",
    "Delivery worker cycles that ended in a caught exception.",
)


def _backoff_seconds(next_attempt: int) -> int:
    if next_attempt <= 0:
        return _BACKOFF_SCHEDULE[0]
    return _BACKOFF_SCHEDULE[min(next_attempt - 1, len(_BACKOFF_SCHEDULE) - 1)]


@contextlib.contextmanager
def _bypass_rls() -> Iterator[None]:
    token = bypass_rls_var.set(True)
    try:
        yield
    finally:
        bypass_rls_var.reset(token)


async def _httpx_post(url: str, body: bytes, headers: dict[str, str]) -> int:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, content=body, headers=headers)
        return resp.status_code


class WebhookDeliveryWorker:
    """Background task: sign + POST queued webhook deliveries."""

    def __init__(
        self,
        *,
        delivery_store: WebhookDeliveryStore,
        endpoint_store: WebhookEndpointStore,
        secret_store: SecretStore,
        interval_s: int = 15,
        batch_size: int = _BATCH_SIZE,
        max_attempts: int = _MAX_ATTEMPTS,
        per_tenant_concurrency: int = 4,
        breaker_threshold: int = _BREAKER_THRESHOLD,
        breaker_cooldown_s: int = _BREAKER_COOLDOWN_S,
        http_post: HttpPost | None = None,
    ) -> None:
        for name, value in (
            ("interval_s", interval_s),
            ("batch_size", batch_size),
            ("max_attempts", max_attempts),
            ("per_tenant_concurrency", per_tenant_concurrency),
        ):
            if value <= 0:
                msg = f"{name} must be positive"
                raise ValueError(msg)
        self._deliveries = delivery_store
        self._endpoints = endpoint_store
        self._secrets = secret_store
        self._interval_s = interval_s
        self._batch_size = batch_size
        self._max_attempts = max_attempts
        self._per_tenant_concurrency = per_tenant_concurrency
        self._breaker_threshold = breaker_threshold
        self._breaker_cooldown_s = breaker_cooldown_s
        self._post = http_post or _httpx_post
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        #: endpoint_id → (consecutive_failures, open_until). In-memory; a
        #: restart resets breakers (acceptable — they re-trip quickly).
        self._breaker: dict[UUID, tuple[int, datetime | None]] = {}

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.is_running:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="webhook-delivery-worker")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=self._interval_s + 5)
        except (TimeoutError, asyncio.CancelledError):
            self._task.cancel()
        finally:
            self._task = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception:
                _cycle_errors.inc()
                logger.exception("webhook_delivery.cycle_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_s)
            except TimeoutError:
                continue
            else:
                break

    async def run_once(self) -> tuple[int, int, int]:
        """Drain one batch. Returns ``(delivered, retried, dead_lettered)``."""
        now = datetime.now(UTC)
        with _bypass_rls():
            ready = await self._deliveries.list_ready(before=now, limit=self._batch_size)
        if not ready:
            return (0, 0, 0)

        # Per-tenant concurrency cap — one semaphore per tenant seen this batch.
        sems: dict[UUID, asyncio.Semaphore] = {}
        for row in ready:
            sems.setdefault(row.tenant_id, asyncio.Semaphore(self._per_tenant_concurrency))

        async def _guarded(row: WebhookDeliveryRecord) -> str:
            async with sems[row.tenant_id]:
                return await self._deliver_one(row, now=now)

        outcomes = await asyncio.gather(*(_guarded(row) for row in ready), return_exceptions=True)
        delivered = retried = dead = 0
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                # A delivery that raised past _deliver_one's own handling is
                # left untouched (status unchanged) → retried next sweep.
                logger.warning("webhook_delivery.unhandled err=%s", outcome)
                continue
            if outcome == "delivered":
                delivered += 1
            elif outcome == "retry":
                retried += 1
            elif outcome == "dead":
                dead += 1
        logger.info(
            "webhook_delivery.cycle batch=%d delivered=%d retry=%d dead=%d",
            len(ready),
            delivered,
            retried,
            dead,
        )
        return delivered, retried, dead

    async def _deliver_one(self, row: WebhookDeliveryRecord, *, now: datetime) -> str:
        """Sign + POST one delivery. Returns ``delivered`` / ``retry`` / ``dead`` / ``skip``."""
        if self._breaker_open(row.endpoint_id, now=now):
            _breaker_skips.inc()
            return "skip"

        with _bypass_rls():
            endpoint = await self._endpoints.get(
                endpoint_id=row.endpoint_id, tenant_id=row.tenant_id
            )
        if endpoint is None or not endpoint.enabled or endpoint.secret_ref is None:
            # Endpoint deleted / disabled / mis-provisioned since enqueue —
            # the delivery can never succeed; dead-letter it (no retry).
            return await self._finish(
                row, WebhookDeliveryStatus.DEAD_LETTER, now=now, error="endpoint unavailable"
            )

        try:
            secret = await self._secrets.get(endpoint.secret_ref)
        except SecretNotFoundError:
            return await self._finish(
                row, WebhookDeliveryStatus.DEAD_LETTER, now=now, error="signing secret missing"
            )

        body = self._envelope_bytes(row)
        headers = {
            "Content-Type": "application/json",
            _SIGNATURE_HEADER: "sha256=" + hmac.new(secret.encode(), body, sha256).hexdigest(),
            "X-Helix-Event": row.event_type,
            "X-Helix-Delivery": str(row.id),
        }
        try:
            status = await self._post(endpoint.url, body, headers)
        except Exception as exc:  # transport failure — retryable like a 5xx
            return await self._on_failure(row, now=now, error=f"{type(exc).__name__}: {exc}")

        if 200 <= status < 300:
            self._breaker_reset(row.endpoint_id)
            return await self._finish(
                row, WebhookDeliveryStatus.DELIVERED, now=now, response_status=status
            )
        if 400 <= status < 500 and status not in (408, 429):
            # Config error (bad URL / auth / payload) — retrying won't fix.
            self._breaker_reset(row.endpoint_id)  # not an endpoint-health fault
            return await self._finish(
                row,
                WebhookDeliveryStatus.DEAD_LETTER,
                now=now,
                response_status=status,
                error=f"non-retryable {status}",
            )
        # 5xx / 408 / 429 — retry with backoff.
        return await self._on_failure(
            row, now=now, response_status=status, error=f"retryable {status}"
        )

    def _envelope_bytes(self, row: WebhookDeliveryRecord) -> bytes:
        """Canonical JSON signed + sent. Stable key order so the signature
        the tenant recomputes matches byte-for-byte."""
        envelope = {
            "event_id": row.event_id,
            "event_type": row.event_type,
            "occurred_at": row.created_at.isoformat(),
            "tenant_id": str(row.tenant_id),
            "payload": row.payload,
        }
        return json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()

    async def _on_failure(
        self,
        row: WebhookDeliveryRecord,
        *,
        now: datetime,
        response_status: int | None = None,
        error: str,
    ) -> str:
        """Record a retryable failure; trip the breaker; dead-letter if spent."""
        self._breaker_fail(row.endpoint_id, now=now)
        next_attempt = row.attempt + 1
        if next_attempt >= self._max_attempts:
            return await self._finish(
                row,
                WebhookDeliveryStatus.DEAD_LETTER,
                now=now,
                response_status=response_status,
                error=error,
            )
        return await self._finish(
            row,
            WebhookDeliveryStatus.RETRYING,
            now=now,
            response_status=response_status,
            error=error,
            next_retry_at=now + timedelta(seconds=_backoff_seconds(next_attempt)),
        )

    async def _finish(
        self,
        row: WebhookDeliveryRecord,
        status: WebhookDeliveryStatus,
        *,
        now: datetime,
        response_status: int | None = None,
        error: str | None = None,
        next_retry_at: datetime | None = None,
    ) -> str:
        updated = row.model_copy(
            update={
                "status": status,
                "attempt": row.attempt + 1,
                "next_retry_at": next_retry_at,
                "response_status": response_status,
                "error": error,
                "updated_at": now,
            }
        )
        with _bypass_rls():
            await self._deliveries.update(updated)
        if status is WebhookDeliveryStatus.DELIVERED:
            _delivered.inc()
            return "delivered"
        if status is WebhookDeliveryStatus.RETRYING:
            _retries.inc()
            return "retry"
        _dead_letters.inc()
        return "dead"

    # ----------------------------------------------------------- breaker
    def _breaker_open(self, endpoint_id: UUID, *, now: datetime) -> bool:
        state = self._breaker.get(endpoint_id)
        if state is None:
            return False
        _, open_until = state
        if open_until is None:
            return False
        if now >= open_until:
            # Cooldown elapsed — allow a probe (half-open); keep the failure
            # count so a fresh failure re-opens immediately.
            self._breaker[endpoint_id] = (state[0], None)
            return False
        return True

    def _breaker_fail(self, endpoint_id: UUID, *, now: datetime) -> None:
        failures = self._breaker.get(endpoint_id, (0, None))[0] + 1
        open_until = (
            now + timedelta(seconds=self._breaker_cooldown_s)
            if failures >= self._breaker_threshold
            else None
        )
        self._breaker[endpoint_id] = (failures, open_until)

    def _breaker_reset(self, endpoint_id: UUID) -> None:
        self._breaker.pop(endpoint_id, None)


__all__ = ["WebhookDeliveryWorker"]
