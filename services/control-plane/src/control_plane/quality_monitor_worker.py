"""Production quality monitor — Stream RT-5 (RT-ADR-22).

Resident pull worker: periodically scans successfully-finished runs past an
in-memory watermark, deterministically samples a fraction of them, judges the
sampled run's latest exchange (RT-ADR-23), and persists the verdict to the
per-agent ``quality_score`` time-series (RT-ADR-24). Judge token spend lands
in ``token_usage`` (``usage_kind='quality_sampling'``), same as the
consolidator's aux path.

Decoupled from the run hot path (RT-ADR-22 cost guardrail): no orchestrator
change, just a consumer-side scan. Cross-tenant scan runs under the RLS-bypass
scope (same as the transcript-mirror sweep); each verdict WRITE runs under its
own tenant scope for the FORCE-RLS ``WITH CHECK``.

Honest boundaries: sampling is best-effort, not exhaustive — the watermark is
in-memory (a restart resumes from ~now, so runs finishing during downtime are
skipped) and bounded by a per-tenant daily cap. There is no run→turn map, so
the judged input is the thread's latest user<->assistant exchange at sample
time; for a fast multi-turn thread that is a best-effort attribution to the
sampled run.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID

from control_plane.quality_judge import QualityJudge, QualityJudgeResult
from control_plane.runtime import AgentRuntime
from control_plane.transcript import read_turns
from helix_agent.common.observability import current_trace_id_hex, helix_counter
from helix_agent.persistence import (
    MessageTurn,
    QualityCandidateSource,
    QualityScoreStore,
)
from helix_agent.persistence.rls import bypass_rls_var, current_tenant_id_var
from helix_agent.persistence.token_usage_store import TokenUsageRecord, TokenUsageStore
from helix_agent.protocol import QualityScoreRecord

logger = logging.getLogger("helix.control_plane.quality_monitor")

#: Default cadence — a completed run is judged within a few minutes.
_DEFAULT_INTERVAL_S = 300.0

#: Max batches drained per cycle — bounds one run_once so it can never loop
#: unbounded; leftover backlog is picked up on the next cycle.
_MAX_DRAIN_BATCHES = 50

#: usage_kind for the judge's aux token spend (chargeback-visible, mirrors
#: memory_consolidation / skill_evolution).
_USAGE_KIND = "quality_sampling"
_USAGE_AGENT_NAME = "quality-monitor"

_sampled_total = helix_counter(
    "helix_control_plane_quality_sampled_total",
    "Finished runs selected by the deterministic quality sampler.",
)
_scored_total = helix_counter(
    "helix_control_plane_quality_scored_total",
    "Sampled runs judged and persisted to the quality time-series.",
)
_judge_errors = helix_counter(
    "helix_control_plane_quality_judge_errors_total",
    "Sampled runs the judge could not score (dropped).",
)
_cycle_errors = helix_counter(
    "helix_control_plane_quality_cycle_errors_total",
    "Quality monitor sweep cycles that ended in a caught exception.",
)


@contextmanager
def _bypass_rls() -> Iterator[None]:
    """RLS-bypass scope for the cross-tenant candidate scan."""
    bypass = bypass_rls_var.set(True)
    tenant = current_tenant_id_var.set(None)
    try:
        yield
    finally:
        current_tenant_id_var.reset(tenant)
        bypass_rls_var.reset(bypass)


@contextmanager
def _tenant_scope(tenant_id: UUID) -> Iterator[None]:
    """Scope one verdict write / count to its tenant (FORCE-RLS check)."""
    tenant = current_tenant_id_var.set(tenant_id)
    bypass = bypass_rls_var.set(False)
    try:
        yield
    finally:
        bypass_rls_var.reset(bypass)
        current_tenant_id_var.reset(tenant)


def _is_sampled(run_id: str, rate_pct: int) -> bool:
    """Deterministic hash-bucket sampling (SE-A45 pattern, RT-ADR-22)."""
    if rate_pct <= 0:
        return False
    if rate_pct >= 100:
        return True
    digest = hashlib.sha256(run_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % 100 < rate_pct


def _last_exchange(turns: Sequence[MessageTurn]) -> tuple[str | None, str | None]:
    """Last user prompt + last assistant reply from an ordered turn list."""
    prompt = next((t.content for t in reversed(turns) if t.role == "user"), None)
    reply = next((t.content for t in reversed(turns) if t.role == "assistant"), None)
    return prompt, reply


def _utc_day_start() -> datetime:
    now = datetime.now(tz=UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


class QualityMonitorWorker:
    """Background task: sample → judge → persist production quality verdicts."""

    def __init__(
        self,
        *,
        candidate_source: QualityCandidateSource,
        score_store: QualityScoreStore,
        judge: QualityJudge,
        runtime: AgentRuntime,
        usage_store: TokenUsageStore | None,
        sampling_rate_pct: int,
        daily_cap: int,
        interval_s: float = _DEFAULT_INTERVAL_S,
        batch_size: int = 200,
    ) -> None:
        if interval_s <= 0:
            msg = "interval_s must be positive"
            raise ValueError(msg)
        self._candidates = candidate_source
        self._scores = score_store
        self._judge = judge
        self._runtime = runtime
        self._usage = usage_store
        self._rate_pct = sampling_rate_pct
        self._daily_cap = daily_cap
        self._interval_s = interval_s
        self._batch_size = batch_size
        self._cursor: datetime | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Start the periodic loop. Idempotent."""
        if self.is_running:
            return
        # Watermark from ~now: only runs finishing after startup are sampled
        # (best-effort — a downtime gap is skipped, RT-ADR-22).
        if self._cursor is None:
            self._cursor = datetime.now(tz=UTC)
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="quality-monitor")

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
        # Sleep first (the platform likely just restarted). A failed cycle is
        # logged + counted, never fatal.
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_s)
                return  # stop event fired
            except TimeoutError:
                pass
            try:
                await self.run_once()
            except Exception:
                _cycle_errors.inc()
                logger.exception("quality_monitor.cycle_failed")

    async def run_once(self) -> int:
        """Sample + judge finished runs, draining the backlog; rows persisted.

        Loops over successive batches (the feed pages by ``updated_at > cursor``
        so a busy platform does not fall permanently behind a single fixed
        batch); bounded by ``_MAX_DRAIN_BATCHES`` per cycle so one call can never
        run unbounded — any remaining backlog is picked up next cycle.
        """
        checkpointer = self._runtime.durable_checkpointer
        if checkpointer is None or self._cursor is None:
            return 0
        day_start = _utc_day_start()
        daily: dict[UUID, int] = {}
        scored = 0
        for _ in range(_MAX_DRAIN_BATCHES):
            since = self._cursor
            with _bypass_rls():
                candidates = await self._candidates.list_candidates(
                    since=since, limit=self._batch_size
                )
            if not candidates:
                break
            cursor = since
            for cand in candidates:
                cursor = max(cursor, cand.updated_at)
                if not _is_sampled(str(cand.run_id), self._rate_pct):
                    continue
                _sampled_total.inc()
                with _tenant_scope(cand.tenant_id):
                    # Skip a run already judged (idempotent re-scan / updated_at
                    # re-bump) BEFORE spending judge tokens.
                    if await self._scores.exists(tenant_id=cand.tenant_id, run_id=cand.run_id):
                        continue
                    # Per-tenant daily cap (cost guardrail) — counted once per
                    # tenant per cycle.
                    if cand.tenant_id not in daily:
                        daily[cand.tenant_id] = await self._scores.count_since(
                            tenant_id=cand.tenant_id, since=day_start
                        )
                if daily[cand.tenant_id] >= self._daily_cap:
                    continue
                verdict = await self._judge_run(checkpointer, cand.thread_id, cand.tenant_id)
                if verdict is None:
                    continue
                record = QualityScoreRecord(
                    tenant_id=cand.tenant_id,
                    agent_name=cand.agent_name,
                    agent_version=cand.agent_version,
                    run_id=cand.run_id,
                    thread_id=cand.thread_id,
                    overall=verdict.overall,
                    dimensions=verdict.dimensions,
                    rationale=verdict.rationale,
                    judge_model=verdict.model,
                )
                with _tenant_scope(cand.tenant_id):
                    await self._scores.insert(record)
                daily[cand.tenant_id] += 1
                scored += 1
                _scored_total.inc()
                await self._record_aux_usage(verdict, tenant_id=cand.tenant_id)
            # Feed is strict ``> since`` so cursor advances on any non-empty
            # batch — no spin. A short batch means the backlog is drained.
            self._cursor = cursor
            if len(candidates) < self._batch_size:
                break
        if scored:
            logger.info("quality_monitor.scored", extra={"count": scored})
        return scored

    async def _judge_run(
        self, checkpointer: object, thread_id: UUID, tenant_id: UUID
    ) -> QualityJudgeResult | None:
        # Transcript read is checkpoint-level (no RLS); include_hidden=False so
        # orchestrator scaffolding does not pollute the judged exchange.
        try:
            turns = await read_turns(checkpointer, thread_id, include_hidden=False)
        except Exception:
            _judge_errors.inc()
            logger.warning(
                "quality_monitor.transcript_read_failed",
                extra={"thread_id": str(thread_id)},
                exc_info=True,
            )
            return None
        prompt, reply = _last_exchange(turns)
        if prompt is None or reply is None:
            return None
        verdict = await self._judge.score(tenant_id=tenant_id, prompt=prompt, reply=reply)
        if verdict is None:
            _judge_errors.inc()
        return verdict

    async def _record_aux_usage(self, verdict: QualityJudgeResult, *, tenant_id: UUID) -> None:
        """Chargeback the judge spend to ``token_usage``; never fatal."""
        if self._usage is None:
            return
        try:
            await self._usage.insert(
                TokenUsageRecord(
                    tenant_id=tenant_id,
                    agent_name=_USAGE_AGENT_NAME,
                    agent_version="-",
                    model=verdict.model,
                    usage_kind=_USAGE_KIND,
                    trace_id=current_trace_id_hex(),
                    input_tokens=verdict.input_tokens,
                    output_tokens=verdict.output_tokens,
                )
            )
        except Exception:
            logger.warning("quality_monitor.aux_usage_persist_failed", exc_info=True)


__all__ = ["QualityMonitorWorker"]
