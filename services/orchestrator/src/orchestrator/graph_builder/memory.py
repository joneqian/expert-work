"""Memory nodes — Stream J.3 (long-term memory recall + write-back).

When a manifest enables long-term memory (``memory.long_term``) the
factory adds two graph nodes around the agent loop:

::

    START → [memory_recall] → ... agent ⇄ tools ... → [memory_writeback] → END

- ``memory_recall`` embeds the user's task, fetches the top-k nearest
  past memories, and parks them on ``AgentState.recalled_memories`` —
  ``agent_node`` renders them into its system context every step.
- ``memory_writeback`` makes one LLM call that extracts new durable
  memories from the run's trajectory, embeds them, and persists them.

Both no-op when the run carries no per-user scope (no ``user_id``) —
long-term memory is per-user. Recall / write-back are best-effort: any
failure is logged and swallowed so it never fails the run (cancellation
still propagates).
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from helix_agent.common.search import mmr_select
from helix_agent.common.threat_patterns import scan_for_threats
from helix_agent.common.uplift_metrics import (
    record_memory_drift,
    record_memory_mmr,
    record_memory_reconcile,
    record_memory_redacted,
    record_memory_rerank,
    record_memory_retrieval,
)
from helix_agent.persistence import MemoryStore
from helix_agent.persistence.memory import MemoryWritebackDLQ
from helix_agent.persistence.memory.base import MemoryInjectionBlockedError
from helix_agent.persistence.tenant_config import TenantConfigStore
from helix_agent.protocol import MemoryItem, MemoryRecallMode
from helix_agent.runtime.cancellation import CancellationToken, RunCancelledError
from orchestrator.graph_builder._config import cancellation_token, configurable_uuid
from orchestrator.llm import Embedder, LLMCaller
from orchestrator.state import AgentState
from orchestrator.tools.knowledge import Reranker

logger = logging.getLogger(__name__)

#: A memory graph node: takes state + config, returns state updates.
MemoryNode = Callable[[AgentState, RunnableConfig], Awaitable[dict[str, Any]]]

#: Stream CM-4/CM-6 — candidate depth recalled before the re-ranking stages
#: (cross-encoder rerank, then MMR diversity selection), wider than the
#: final top-k so both have alternatives to choose from. Mirrors the
#: knowledge retriever's recall limit; always applied since CM-6 (the
#: stores fetch this many fusion candidates anyway — Mini-ADR CM-G5).
_MEMORY_RECALL_WIDE_LIMIT = 20

#: Per-message cap when rendering the trajectory for the extraction prompt.
_TRAJECTORY_CHAR_CAP = 1000

_EXTRACT_SYSTEM = (
    "You are a memory extraction module. From the conversation, extract "
    "durable, reusable memories worth recalling in future sessions — "
    'stable user facts or preferences ("fact"), and concise summaries of '
    'what was done or decided ("episodic"). Extract nothing trivial or '
    "ephemeral. For each memory also rate two scores in [0, 1]: "
    '"importance" (how reusable it is in future sessions — rare stable '
    'user facts high, one-off chatter low) and "confidence" (how sure '
    "you are it is true — explicit statements high, inferred or hedged "
    "low). Respond with ONLY a JSON object, no prose and no code "
    "fences:\n"
    '{"memories": [{"kind": "fact" | "episodic", "content": "<one concise '
    'sentence>", "importance": <0-1>, "confidence": <0-1>}]}\n'
    'If there is nothing worth remembering, return {"memories": []}.'
)


def _message_text(message: BaseMessage) -> str:
    content = message.content
    return content if isinstance(content, str) else str(content)


def _last_human_text(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return _message_text(message)
    return ""


def _render_trajectory(messages: list[BaseMessage]) -> str:
    lines: list[str] = []
    for message in messages:
        text = _message_text(message).strip()
        if len(text) > _TRAJECTORY_CHAR_CAP:
            text = text[:_TRAJECTORY_CHAR_CAP] + "...[truncated]"
        lines.append(f"[{message.type}] {text}")
    return "\n".join(lines)


def _extract_json_object(text: str) -> str | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    return text[start : end + 1]


@dataclass(frozen=True)
class ExtractedMemory:
    """One memory parsed from the extraction LLM reply — Stream Memory-Enhance
    (M-2). ``importance`` feeds the write-filter; ``confidence`` is persisted
    for downstream ranking / correction. Both default 0.5 when the model omits
    or malforms them (write-back is best-effort — a missing score never drops
    an otherwise-valid memory)."""

    kind: Literal["fact", "episodic"]
    content: str
    importance: float = 0.5
    confidence: float = 0.5


def _clamp_score(value: object) -> float:
    """Parse a model-supplied score into [0, 1], falling back to 0.5 (neutral)
    on anything non-numeric or out of range-ish."""
    try:
        score = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.5
    if math.isnan(score):
        return 0.5
    return min(1.0, max(0.0, score))


def parse_extracted_memories(text: str) -> list[ExtractedMemory]:
    """Parse the extraction LLM reply into :class:`ExtractedMemory` items.

    Tolerant — any malformed reply yields ``[]`` (write-back is
    best-effort). Duplicate contents within the batch are dropped. A missing
    or invalid ``importance`` / ``confidence`` defaults to 0.5 (neutral).
    """
    raw = _extract_json_object(text)
    if raw is None:
        return []
    try:
        data = json.loads(raw)
        rows = data["memories"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return []
    out: list[ExtractedMemory] = []
    seen: set[str] = set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind", "")).strip().lower()
        content = str(row.get("content", "")).strip()
        if kind in ("fact", "episodic") and content and content not in seen:
            seen.add(content)
            out.append(
                ExtractedMemory(
                    kind=cast(Literal["fact", "episodic"], kind),
                    content=content,
                    importance=_clamp_score(row.get("importance")),
                    confidence=_clamp_score(row.get("confidence")),
                )
            )
    return out


def _redact_memory(item: MemoryItem) -> MemoryItem:
    """Capability Uplift Sprint #2 (Mini-ADR U-3 Layer B) — replace
    poisoned / drifted content with a ``[BLOCKED:<category>]`` placeholder.

    ``drift=True`` wins over pattern matches: drifted content is no
    longer trusted regardless of what it now contains, so the agent
    is told the row was tampered with rather than which pattern fired.
    Pattern matches return the matched category (``injection`` / ``c2``
    / ``exfil`` / etc) so the agent has minimal signal to potentially
    re-ask the user; the ``pattern_id`` itself stays in the audit row
    (oracle defense — see ``docs/runbooks/threat-scanner-tuning.md`` § 4).
    """
    if item.drift:
        record_memory_drift()
        record_memory_redacted()
        return item.model_copy(update={"content": "[BLOCKED:drift_tampered]"})
    findings = scan_for_threats(item.content, scope="strict")
    if findings:
        record_memory_redacted()
        return item.model_copy(update={"content": f"[BLOCKED:{findings[0].category}]"})
    return item


async def _resolve_memory_recall_mode(
    *,
    tenant_id: Any,
    tenant_config_store: TenantConfigStore | None,
) -> MemoryRecallMode:
    """Read ``tenant_config.memory_recall_mode``; default ``hybrid``.

    No store wired or no tenant_config row → ``hybrid`` (platform-wide
    default, Mini-ADR U-5). Mirrors the trigger fire-scan-mode resolver
    in ``control_plane.trigger_firing``.
    """
    if tenant_config_store is None:
        return "hybrid"
    record = await tenant_config_store.get(tenant_id=tenant_id)
    if record is None:
        return "hybrid"
    return record.memory_recall_mode


async def _rerank_memories(
    *,
    reranker: Reranker,
    query: str,
    candidates: list[MemoryItem],
    top_k: int,
    tenant_id: UUID,
    token: CancellationToken,
) -> list[MemoryItem]:
    """Stream CM-4 — reorder recall candidates by cross-encoder relevance.

    Best-effort: the reranker's own implementations already degrade to the
    fused order (LLM parse failure / no credential); this wraps the call so
    any unexpected error still degrades to the RRF order (``candidates``
    truncated to ``top_k``) rather than dropping recall entirely. Cancellation
    propagates. Operates on raw content — redaction happens on the final set.
    """
    try:
        order = await token.run_cancellable(
            reranker.rerank(
                query=query,
                documents=[m.content for m in candidates],
                top_k=top_k,
                tenant_id=tenant_id,
            )
        )
    except RunCancelledError:
        raise
    except Exception:
        logger.warning("memory.rerank_failed — using RRF order", exc_info=True)
        record_memory_rerank(outcome="degraded")
        return candidates[:top_k]
    reranked = [candidates[i] for i in order if 0 <= i < len(candidates)]
    if not reranked:
        record_memory_rerank(outcome="degraded")
        return candidates[:top_k]
    record_memory_rerank(outcome="reranked")
    return reranked[:top_k]


def _mmr_memories(
    *,
    query_embedding: Sequence[float],
    candidates: list[MemoryItem],
    top_k: int,
) -> list[MemoryItem]:
    """Stream CM-6 — MMR diversity selection, the recall pipeline's last stage.

    Selects the final ``top_k`` from the (rerank- or RRF-ordered) wide
    candidate set, trading relevance against redundancy (λ=0.7). Best-effort
    mirror of the rerank contract (Mini-ADR CM-G6): any failure — or a
    selection thinned to nothing by dimension mismatches — degrades to the
    input order truncated to ``top_k``, never dropping recall entirely.
    """
    try:
        selected = mmr_select(
            query_embedding=query_embedding,
            candidates=[(m, m.embedding) for m in candidates],
            k=top_k,
        )
    except Exception:
        logger.warning("memory.mmr_failed — using input order", exc_info=True)
        record_memory_mmr(outcome="degraded")
        return candidates[:top_k]
    if not selected:
        record_memory_mmr(outcome="degraded")
        return candidates[:top_k]
    record_memory_mmr(outcome="applied")
    return selected


def make_memory_recall_node(
    *,
    memory_store: MemoryStore,
    embedder: Embedder,
    top_k: int,
    tenant_config_store: TenantConfigStore | None = None,
    reranker: Reranker | None = None,
    agent_name: str | None = None,
) -> MemoryNode:
    """Build the ``memory_recall`` node bound to the store + embedder.

    Capability Uplift Sprint #6 (Mini-ADR U-5): when
    ``tenant_config_store`` is wired and the tenant's
    ``memory_recall_mode`` is ``hybrid`` (the default), the user's task
    text is forwarded to ``MemoryStore.retrieve(query_text=...)`` for
    hybrid vector + full-text + RRF recall. ``vector`` mode keeps the
    pre-Sprint-#6 pure-pgvector path. No store wired → default hybrid
    (so test fixtures that omit the store still get the improved recall).

    Stream CM-4/CM-6 — the recall pipeline: wide recall
    (``max(top_k, _MEMORY_RECALL_WIDE_LIMIT)``, always — Mini-ADR CM-G5)
    → cross-encoder rerank when ``reranker`` is wired (full reorder, no
    cut, so the diversity stage still sees the whole pool) → MMR selects
    the final ``top_k`` (Mini-ADR CM-G1/G4) → redaction.
    """

    async def memory_recall_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        token = cancellation_token(config)
        token.raise_if_cancelled()

        tenant_id = configurable_uuid(config, "tenant_id")
        user_id = configurable_uuid(config, "user_id")
        if tenant_id is None or user_id is None:
            return {}
        task = _last_human_text(list(state["messages"]))
        if not task:
            return {}
        mode = await _resolve_memory_recall_mode(
            tenant_id=tenant_id, tenant_config_store=tenant_config_store
        )
        recall_limit = max(top_k, _MEMORY_RECALL_WIDE_LIMIT)
        try:
            vectors = await token.run_cancellable(embedder.embed([task], tenant_id=tenant_id))
            memories = await memory_store.retrieve(
                tenant_id=tenant_id,
                user_id=user_id,
                query_embedding=vectors[0],
                query_text=task if mode == "hybrid" else None,
                # Stream Agent-Templates (M1-5c) — scope episodic recall to this
                # agent; shared facts (agent_name NULL) are always included.
                agent_name=agent_name,
                limit=recall_limit,
            )
            if reranker is not None and memories:
                # Full reorder (top_k = pool size) — the MMR stage below
                # makes the final cut, so diversity can still swap in
                # candidates the relevance cut would have dropped.
                memories = await _rerank_memories(
                    reranker=reranker,
                    query=task,
                    candidates=memories,
                    top_k=len(memories),
                    tenant_id=tenant_id,
                    token=token,
                )
            if memories:
                memories = _mmr_memories(
                    query_embedding=vectors[0],
                    candidates=memories,
                    top_k=top_k,
                )
        except RunCancelledError:
            raise
        except Exception:
            logger.warning("memory.recall_failed — continuing without memories", exc_info=True)
            record_memory_retrieval(mode=mode, result="miss")
            return {}
        record_memory_retrieval(mode=mode, result="hit" if memories else "miss")
        redacted = [_redact_memory(m) for m in memories]
        logger.info("memory.recall count=%d mode=%s", len(redacted), mode)
        return {"recalled_memories": redacted}

    return memory_recall_node


#: Stream CM-7 (Mini-ADR CM-H4) — neighbours below this cosine similarity
#: are not "the same memory" candidates; the new item is ADDed without an
#: LLM decision (the cheap majority path).
_RECONCILE_SIM_THRESHOLD = 0.80
_RECONCILE_NEIGHBOR_LIMIT = 3

_RECONCILE_SYSTEM = (
    "You reconcile newly extracted memories against similar existing "
    "ones. For each candidate decide exactly one operation:\n"
    '- "ADD": genuinely new information\n'
    '- "UPDATE": supersedes or corrects ONE existing memory (set '
    "target_id; the existing memory is rewritten to the candidate)\n"
    '- "DELETE": the candidate retracts ONE existing memory (set '
    "target_id; the existing memory is removed and the candidate "
    "itself is NOT stored)\n"
    '- "NOOP": duplicate of an existing memory; store nothing\n'
    "Respond with ONLY a JSON object, no prose and no code fences:\n"
    '{"ops": [{"index": <candidate index>, "op": "ADD", '
    '"target_id": "<existing id, only for UPDATE/DELETE>"}]}'
)


def _reconcile_cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def parse_reconcile_ops(text: str) -> dict[int, tuple[str, str | None]]:
    """Parse the reconcile LLM reply into ``{index: (op, target_id)}``.

    Tolerant — malformed entries are simply absent, and the caller
    treats an absent decision as a degraded direct ADD (CM-H4: never
    lose a memory over a parse failure).
    """
    raw = _extract_json_object(text)
    if raw is None:
        return {}
    try:
        rows = json.loads(raw)["ops"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}
    out: dict[int, tuple[str, str | None]] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        op = str(row.get("op", "")).strip().upper()
        index = row.get("index")
        if op not in ("ADD", "UPDATE", "DELETE", "NOOP") or not isinstance(index, int):
            continue
        target = row.get("target_id")
        out[index] = (op, str(target) if target is not None else None)
    return out


async def _reconcile_and_apply(
    items: list[MemoryItem],
    *,
    memory_store: MemoryStore,
    llm_caller: LLMCaller,
    token: CancellationToken,
    log_label: str,
) -> list[MemoryItem]:
    """Stream CM-7 (Mini-ADR CM-H4) — Mem0-style extract→update.

    Returns the items to write directly; UPDATE / DELETE are applied
    in place against the store. Best-effort throughout: every failure
    path degrades to keeping the candidate in the direct-write list
    (never lose a memory), and only cancellation propagates.
    """
    direct: list[MemoryItem] = []
    pending: list[tuple[MemoryItem, list[MemoryItem]]] = []
    for item in items:
        try:
            neighbors = await token.run_cancellable(
                memory_store.retrieve(
                    tenant_id=item.tenant_id,
                    user_id=item.user_id,
                    query_embedding=item.embedding,
                    limit=_RECONCILE_NEIGHBOR_LIMIT,
                )
            )
        except RunCancelledError:
            raise
        except Exception:
            logger.warning("%s_reconcile_recall_failed — direct add", log_label, exc_info=True)
            record_memory_reconcile(op="degraded")
            direct.append(item)
            continue
        similar = [
            n
            for n in neighbors
            if _reconcile_cosine(item.embedding, n.embedding) >= _RECONCILE_SIM_THRESHOLD
        ]
        if similar:
            pending.append((item, similar))
        else:
            record_memory_reconcile(op="add")
            direct.append(item)
    if not pending:
        return direct

    payload = {
        "candidates": [
            {
                "index": idx,
                "kind": item.kind,
                "content": item.content,
                "existing": [{"id": str(n.id), "content": n.content} for n in similar],
            }
            for idx, (item, similar) in enumerate(pending)
        ]
    }
    ops: dict[int, tuple[str, str | None]] = {}
    try:
        reply = await token.run_cancellable(
            llm_caller(
                messages=[
                    SystemMessage(content=_RECONCILE_SYSTEM),
                    HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
                ],
                tools=[],
            )
        )
        ops = parse_reconcile_ops(_message_text(reply))
    except RunCancelledError:
        raise
    except Exception:
        logger.warning("%s_reconcile_llm_failed — direct add all", log_label, exc_info=True)

    for idx, (item, similar) in enumerate(pending):
        op, target_raw = ops.get(idx, ("", None))
        valid = {str(n.id): n.id for n in similar}
        target = valid.get(target_raw) if target_raw is not None else None
        if op == "NOOP":
            record_memory_reconcile(op="noop")
        elif op == "UPDATE" and target is not None:
            updated = await _apply_update(item, target, memory_store=memory_store, token=token)
            record_memory_reconcile(op="update" if updated else "degraded")
            if not updated:
                direct.append(item)
        elif op == "DELETE" and target is not None:
            deleted = await _apply_delete(item, target, memory_store=memory_store, token=token)
            # The candidate is the retraction event — not stored either way.
            record_memory_reconcile(op="delete" if deleted else "degraded")
        elif op == "ADD":
            record_memory_reconcile(op="add")
            direct.append(item)
        else:
            # Missing / malformed decision — never lose the memory.
            record_memory_reconcile(op="degraded")
            direct.append(item)
    return direct


async def _apply_update(
    item: MemoryItem, target: UUID, *, memory_store: MemoryStore, token: CancellationToken
) -> bool:
    try:
        updated = await token.run_cancellable(
            memory_store.update_content(
                tenant_id=item.tenant_id,
                user_id=item.user_id,
                memory_id=target,
                content=item.content,
                embedding=item.embedding,
                kind=item.kind,
            )
        )
    except RunCancelledError:
        raise
    except Exception:
        logger.warning("memory.reconcile_update_failed id=%s", target, exc_info=True)
        return False
    return updated is not None


async def _apply_delete(
    item: MemoryItem, target: UUID, *, memory_store: MemoryStore, token: CancellationToken
) -> bool:
    try:
        return await token.run_cancellable(
            memory_store.soft_delete(
                tenant_id=item.tenant_id, user_id=item.user_id, memory_id=target
            )
        )
    except RunCancelledError:
        raise
    except Exception:
        logger.warning("memory.reconcile_delete_failed id=%s", target, exc_info=True)
        return False


async def flush_messages_to_memory(
    messages: Sequence[BaseMessage],
    *,
    memory_store: MemoryStore,
    embedder: Embedder,
    llm_caller: LLMCaller,
    tenant_id: UUID,
    user_id: UUID,
    thread_id: UUID | None,
    token: CancellationToken,
    dlq: MemoryWritebackDLQ | None = None,
    log_label: str = "memory.writeback",
    reconcile: bool = False,
    agent_name: str | None = None,
    write_min_importance: float = 0.0,
) -> int:
    """Extract durable memories from ``messages``, embed, and persist them.

    The shared extraction core behind both the run-end ``memory_writeback``
    node and the Stream CM-3 pre-compaction flush. Makes one LLM extraction
    call, embeds the produced pairs, and writes :class:`MemoryItem`\\s tagged
    with ``source_thread_id``. Returns the number of memories written
    (``0`` on empty extraction / blocked content / any handled failure).

    Best-effort, mirroring the original write-back contract:

    * ``RunCancelledError`` propagates (cancellation is never swallowed).
    * ``MemoryInjectionBlockedError`` (strict scanner) → log + return 0;
      the store has already emitted the block audit.
    * Any other failure after a non-empty extraction → enqueue the pairs
      to ``dlq`` (Stream K.K7) when wired, else log-and-drop; return 0.

    ``log_label`` distinguishes the two call sites in logs
    (``memory.writeback`` vs ``memory.precompaction_flush``) while keeping
    the run-end node's log strings byte-identical.

    ``reconcile`` (Stream CM-7, Mini-ADR CM-H3/H4) — when set, extracted
    items are reconciled against similar existing memories
    (ADD / UPDATE / DELETE / NOOP) before persisting instead of written
    blindly. Only the run-end write-back sets it; the CM-3 pre-compaction
    flush stays a direct write (latency-sensitive, inside a turn).
    """
    prompt = [
        SystemMessage(content=_EXTRACT_SYSTEM),
        HumanMessage(content=_render_trajectory(list(messages))),
    ]
    extracted: list[ExtractedMemory] = []
    try:
        response = await token.run_cancellable(llm_caller(messages=prompt, tools=[]))
        extracted = parse_extracted_memories(_message_text(response))
        # Stream Memory-Enhance (M-2) — write-filter: drop low-value memories
        # before embedding (saves embed cost on dropped items). Applied here,
        # not in the store, so the threshold is a per-agent manifest knob.
        if write_min_importance > 0.0:
            kept = [m for m in extracted if m.importance >= write_min_importance]
            if len(kept) != len(extracted):
                logger.debug(
                    "%s_write_filter dropped=%d kept=%d min_importance=%.2f",
                    log_label,
                    len(extracted) - len(kept),
                    len(kept),
                    write_min_importance,
                )
            extracted = kept
        if not extracted:
            return 0
        vectors = await token.run_cancellable(
            embedder.embed([m.content for m in extracted], tenant_id=tenant_id)
        )
        items = [
            MemoryItem(
                id=uuid4(),
                tenant_id=tenant_id,
                user_id=user_id,
                kind=mem.kind,
                # Stream Agent-Templates (M1-5c) — tag episodic with the owning
                # agent (per-agent isolation); facts stay shared (agent_name None).
                agent_name=agent_name if mem.kind == "episodic" else None,
                content=mem.content,
                embedding=vector,
                # Stream Memory-Enhance (M-2) — carry the extraction scores.
                importance=mem.importance,
                confidence=mem.confidence,
                source_thread_id=str(thread_id) if thread_id is not None else None,
            )
            for mem, vector in zip(extracted, vectors, strict=True)
        ]
        if reconcile:
            items = await _reconcile_and_apply(
                items,
                memory_store=memory_store,
                llm_caller=llm_caller,
                token=token,
                log_label=log_label,
            )
        if items:
            await memory_store.write(items)
    except RunCancelledError:
        raise
    except MemoryInjectionBlockedError as exc:
        # Capability Uplift Sprint #2 — LLM extracted something the strict
        # scanner caught. The content is deterministic, so retrying will
        # fail identically; drop the batch + log. The store has emitted
        # MEMORY_INJECTION_BLOCKED audit(s) by the time we land here. Run
        # is not affected.
        logger.warning(
            "%s_blocked count=%d — content rejected by strict scanner",
            log_label,
            len(exc.blocked),
        )
        return 0
    except Exception as exc:
        # Stream K.K7 — don't lose the work the LLM already did. If the
        # extraction produced pairs, hand them to the DLQ for a retry
        # sweep; otherwise (parse / cancel-shaped failures below the
        # extraction line) log and drop as before.
        if dlq is not None and extracted:
            try:
                # DLQ enqueue takes ``Sequence[tuple[str, str]]``; widen the
                # Literal kind so mypy accepts the tuple element.
                await dlq.enqueue(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    source_thread_id=str(thread_id) if thread_id is not None else None,
                    extracted=[(str(m.kind), m.content) for m in extracted],
                    error=f"{type(exc).__name__}: {exc}",
                )
                logger.warning(
                    "%s_failed — enqueued %d pair(s) to DLQ",
                    log_label,
                    len(extracted),
                    exc_info=True,
                )
            except Exception:
                logger.error("%s_dlq_enqueue_failed — pairs lost", log_label, exc_info=True)
        else:
            logger.warning("%s_failed — run unaffected", log_label, exc_info=True)
        return 0
    logger.info("%s count=%d", log_label, len(items))
    return len(items)


#: Stream CM-3 — a config-bound pre-compaction flush callback. Awaited by
#: ``agent_node`` with the middle slice the compressor is about to discard,
#: the run's ``config`` (for tenant/user/thread scope) and its
#: ``CancellationToken``; returns the number of memories written.
PreCompactionFlush = Callable[
    [Sequence[BaseMessage], RunnableConfig, CancellationToken], Awaitable[int]
]


def make_pre_compaction_flush(
    *,
    memory_store: MemoryStore,
    embedder: Embedder,
    llm_caller: LLMCaller,
    dlq: MemoryWritebackDLQ | None = None,
    agent_name: str | None = None,
    write_min_importance: float = 0.0,
) -> PreCompactionFlush:
    """Build the Stream CM-3 pre-compaction flush callback.

    Resolves the run's per-user scope from ``config`` (no-op returning 0
    when absent — long-term memory is per-user) and hands the about-to-be-
    discarded middle slice to :func:`flush_messages_to_memory`, tagged as a
    ``memory.precompaction_flush`` so logs distinguish it from the run-end
    write-back. Shares the same store / embedder / DLQ as the write-back
    node, so flushed memories are indistinguishable downstream.
    """

    async def flush(
        messages: Sequence[BaseMessage],
        config: RunnableConfig,
        token: CancellationToken,
    ) -> int:
        tenant_id = configurable_uuid(config, "tenant_id")
        user_id = configurable_uuid(config, "user_id")
        if tenant_id is None or user_id is None:
            return 0
        thread_id = configurable_uuid(config, "thread_id")
        return await flush_messages_to_memory(
            messages,
            memory_store=memory_store,
            embedder=embedder,
            llm_caller=llm_caller,
            tenant_id=tenant_id,
            user_id=user_id,
            thread_id=thread_id,
            token=token,
            dlq=dlq,
            log_label="memory.precompaction_flush",
            agent_name=agent_name,
            write_min_importance=write_min_importance,
        )

    return flush


def make_memory_writeback_node(
    *,
    memory_store: MemoryStore,
    embedder: Embedder,
    llm_caller: LLMCaller,
    dlq: MemoryWritebackDLQ | None = None,
    reconcile: bool = False,
    agent_name: str | None = None,
    write_min_importance: float = 0.0,
) -> MemoryNode:
    """Build the ``memory_writeback`` node bound to the store + embedder.

    Stream K.K7 — when ``dlq`` is supplied, failures after the LLM
    extraction successfully produced memories enqueue the extracted
    pairs so a retry worker can re-do the embed + write later. Without
    a DLQ the previous best-effort log-and-drop behaviour is kept so
    unit tests that don't wire a queue still work.

    Stream CM-3 — the extraction core now lives in the shared
    :func:`flush_messages_to_memory`; this node is the thin run-end
    adapter (resolve config scope → flush the full trajectory).

    Stream CM-7 — ``reconcile`` forwards to the flush so run-end writes
    go through the Mem0-style ADD / UPDATE / DELETE / NOOP decision.
    """

    async def memory_writeback_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        token = cancellation_token(config)
        token.raise_if_cancelled()

        tenant_id = configurable_uuid(config, "tenant_id")
        user_id = configurable_uuid(config, "user_id")
        if tenant_id is None or user_id is None:
            return {}
        thread_id = configurable_uuid(config, "thread_id")

        await flush_messages_to_memory(
            list(state["messages"]),
            memory_store=memory_store,
            embedder=embedder,
            llm_caller=llm_caller,
            tenant_id=tenant_id,
            user_id=user_id,
            thread_id=thread_id,
            token=token,
            dlq=dlq,
            log_label="memory.writeback",
            reconcile=reconcile,
            agent_name=agent_name,
            write_min_importance=write_min_importance,
        )
        return {}

    return memory_writeback_node
