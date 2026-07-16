# End-to-End LLM Token Streaming — Design

**Status:** approved (brainstorm 2026-07-16)
**Goal:** Stream LLM output token-by-token end-to-end so (1) the provider deadline becomes a correct *idle* timeout instead of a blunt total-time cap that mis-fires on healthy-but-slow generations, and (2) external API clients and the playground render live tokens instead of blocking on a blank wait until a whole step completes.

**Architecture:** The SSE transport is already end-to-end (external agents API, sessions, playground all consume SSE). What is missing is *token granularity*: providers do a non-streaming single POST, the run emits LangGraph `updates` (one frame per completed step), and clients render per step. This design adds token granularity to the existing pipeline in phases, without breaking the current step-level contract.

**Tech stack:** Python (orchestrator provider adapters + LLM router + LangGraph graph), FastAPI SSE (control-plane), React/TS admin-ui playground. httpx streaming (`stream=True`) for provider SSE; LangGraph custom stream writer for token frames.

## Global Constraints

- **Backward compatible at every phase.** P1 changes no external/UI contract (`complete()` still returns a whole `AIMessage`). P2 adds token SSE frames *additively* — clients that ignore unknown frames keep working (they fall back to step rendering).
- **`mode: "queue"` and non-streaming callers unaffected.** The external `POST /v1/agents/{code}/runs` keeps its `mode` semantics; queue mode still returns the assembled result.
- **No silent behavior change on the deadline.** Reinterpreting `stream_deadline_s` must be strictly *more* permissive for existing agents (never newly kill a run that passes today).
- **Fallback policy is fixed (approved):** buffer-until-first-token. Before the first token a stall/error falls over to the next provider; after the first token the provider is committed and a stall ends the turn with the partial output preserved.
- **Provider order (approved):** implement the OpenAI Chat Completions SSE parser first (covers `openai`, `azure`, and the OpenAI-compatible regional vendors `kimi`/`glm`/`deepseek`/`qwen`/`doubao` — 7 of 8 providers, including the `glm` that caused the original run death), then the Anthropic Messages SSE parser.
- Follow repo conventions: many small files, immutable updates, ruff + mypy clean, per-provider unit tests with a mocked SSE transport, no direct commits to `main`, squash-merge per phase.

---

## Motivation

A production run (`thread aea9451e`, tenant `866c25e8`, agent on `glm-5.2`, single provider, no fallback) died at step 4 after ~8.5 minutes of apparent hang. Root chain: the `http` tool was blocked (empty allowlist) → a non-transient tool error tripped CM-11 effort escalation → the escalated `glm-5.2` generation on a 21874-token context exceeded `stream_deadline_s=180` → `LLMStreamStaleError` → `AllProvidersExhaustedError` (glm was the only provider) → transient retry → same 180s stall → failed.

Two follow-up PRs already landed: `#994` (http tool denylist, removes the trigger) and `#995` (fallback-chain form UI, so a single-provider agent is no longer the norm). Those reduce the *probability* of the failure. This epic fixes the *root cause* of the deadline mis-fire and closes a real product gap:

- **Deadline is the wrong instrument.** `stream_deadline_s` wraps the whole provider `complete()` in `asyncio.wait_for` (`router.py:_invoke_with_deadline`) — a total wall-clock cap. It cannot distinguish "provider is hung" from "provider is slow but generating," so a legitimately long generation trips it. The correct instrument is an idle timeout, which requires a progress signal, which requires streaming.
- **External clients block on a blank wait.** `POST /v1/agents/{code}/runs` already returns an SSE `StreamingResponse`, but the frames are LangGraph `updates` (one per completed step). For the common single-step Q&A (user asks, agent answers in one LLM call with no tools), the external client receives nothing until the entire answer is generated — the "long wait" every serious LLM API avoids with token streaming.

---

## Current State (grounded in code)

| Leg | Today | File |
| --- | --- | --- |
| Provider ↔ vendor | **Non-streaming** single POST (`await client.post(json=body)`, no `stream=true`), returns whole JSON | `orchestrator/llm/providers/openai.py:176`, `anthropic.py` |
| Provider interface | `async def complete(...) -> AIMessage` (single awaitable, not a generator) | `openai.py:269`, `anthropic.py:307` |
| Router deadline | `asyncio.wait_for(complete_coro, timeout=stream_deadline_s)` = total cap; `TimeoutError` → `LLMStreamStaleError` → fallback | `router.py:_invoke_with_deadline` (~605) |
| Fallback | Walks a flat handle chain, falls over on retryable errors; `AllProvidersExhaustedError` when all fail | `router.py:__call__` (~299) |
| Run → client | `graph.astream(stream_mode="updates")` → one SSE frame per completed node | `orchestrator/sse.py:160` |
| External API | `POST /v1/agents/{code}/runs` returns `StreamingResponse | JSONResponse`, `mode: "stream"|"queue"` — SSE already | `control-plane/api/agents.py:796,370` |
| Playground | `streamRun` → `parseSseStream` → frames pushed to `tn.events`; `summarizeTurn(frames)` derives the answer; plain text while `running`, markdown when settled | `admin-ui/.../PlaygroundTab.tsx:615,2131`, `api/sessions.ts:217` |

`stream_deadline_s` default is `180` (`agent_spec.py:1189`), floored at build to `180` (`agent_factory._CHAT_STREAM_DEADLINE_FLOOR_S`). The provider httpx client timeout is aligned to it.

---

## Core Mechanism

### Buffer-until-first-token + two-threshold idle timeout

The router consumes the provider's token stream and drives two timers:

| Threshold | Default | Applies | On expiry |
| --- | --- | --- | --- |
| `first_token_timeout_s` | 120–180s (generous) | from call start until the **first** delta | **before first token → fall over to the next provider** (clean) |
| `idle_timeout_s` | 30–45s (tight) | between consecutive deltas | **after first token → end the turn**, keep the partial output + count usage |

**"Progress" = any delta**: assistant text, reasoning/thinking content, or a tool-call argument fragment. Any of them resets `idle_timeout_s`. This is what lets `idle_timeout_s` be tight without killing reasoning models — a reasoning model streams `reasoning_content` deltas during its "thinking," so the inter-token gap stays small even mid-think.

Why two thresholds (not one): time-to-first-token (TTFT) for reasoning models is legitimately 30–120s+ (internal thinking, queueing, cold start), while inter-token gaps for a healthy stream are sub-second. A single threshold forces a bad compromise — small enough to catch stalls kills TTFT; large enough for TTFT detects mid-stream hangs slowly. Splitting them lets each be right.

This marries the fixed fallback policy exactly: `first_token_timeout_s` fires strictly before the first token, which is fallback-eligible; `idle_timeout_s` fires strictly after, which ends the turn.

### Deadline field migration

`stream_deadline_s` (current total cap) → **`first_token_timeout_s`** (the "how long we wait for the model to start" budget — the closest surviving meaning). Existing stored `stream_deadline_s=180` becomes a 180s TTFT budget: permissive, never newly kills a passing run. Add a **new `idle_timeout_s`** field with a tight default. Both are per-agent (`ModelSpec` or `AgentSpec` level — pin during planning) and platform-floored like the current field.

- Keep `stream_deadline_s` as a **deprecated alias** that maps to `first_token_timeout_s` for one release so stored manifests don't break, or migrate in the spec loader. (Pin the exact compat strategy in the P1 plan.)

### Token frame schema (P2a)

A new additive SSE frame type carried on the existing run stream, e.g.:

```
event: token
data: {"run_id": "...", "seq": N, "step": <step_index>, "channel": "content"|"reasoning"|"tool_args",
       "text": "<delta>", "tool_call_id": "<id, tool_args only>"}
```

- `step` attaches the delta to the active ReAct step / TurnCard.
- `channel` separates assistant text, live reasoning, and tool-call argument fragments so the UI can route them.
- The authoritative final message still arrives in the step-end `updates` frame (with tool_calls + usage); token frames are the *provisional* live view. (Pin exact field names + persistence stance in the P2a plan.)

---

## Phases (each a mergeable PR)

### P1 — OpenAI-wire internal streaming + idle timeout

**Deliverable:** the deadline root-cause fix, with zero external/UI change.

- Add a streaming path to the OpenAI-wire client: `chat_completions` issues `stream=true` (+ `stream_options: {include_usage: true}`), reads the httpx response as an SSE byte stream, and yields parsed deltas. Covers `openai`, `azure`, and the compat vendors (they share `OpenAIProvider` over a vendor-configured client, E.11.5).
- Assemble, from the delta stream: accumulated `content`, `reasoning_content` (vendor thinking), **tool_call fragments reassembled by `index`** into complete tool calls, and `usage` from the final chunk.
- `provider.complete()` **still returns a whole `AIMessage`** — internally it now drains the stream and assembles. External signature unchanged; the router, middleware, and graph see the same return type.
- Router: replace the single `asyncio.wait_for(total)` with the two-threshold driver over the delta stream. Expose whether the first token arrived so the fallback loop applies buffer-until-first-token (retryable before first token; terminal after).
- Mid-stream error mapping: an SSE `error` event (arriving after a 200 OK header) or a broken/truncated stream maps to the existing `LLMError` subclasses; retryable **only** while no delta has been emitted.
- Retry/breaker middleware (`around_llm_chain`, E.4): only retry when nothing has been emitted yet.
- Cache hit (E.13 / K.K4): a cached response has no stream — synthesize a single-delta "stream" (or bypass the idle driver) so cached responses still return whole and never trip a timeout.
- Config: introduce `first_token_timeout_s` + `idle_timeout_s`, migrate `stream_deadline_s` per the migration note.

**Verification:** per-vendor unit tests with a mocked SSE transport (multi-delta text, reassembled tool-call fragments, reasoning deltas, mid-stream `error` event, first-token stall, inter-token stall, `[DONE]`, usage chunk); router tests (idle fires on silence not slowness; `first_token_timeout` → fallback, `idle_timeout` → end-turn; buffer-until-first-token honored); cache-hit returns whole with no timeout; regression: assembled `AIMessage` byte-equals the old non-streaming result for a fixed fixture.

### P1' — Anthropic internal streaming

Same shape as P1 for the Anthropic Messages SSE format: `message_start` / `content_block_start|delta|stop` (`input_json_delta` for tool_use) / `message_delta` (usage) / `message_stop`. Separate parser, same assembly contract and router integration. Kept a distinct PR because the wire format and error mapping differ.

### P2a — Token SSE frames + external API

**Deliverable:** token deltas exposed on the existing SSE stream.

- `agent_node` emits deltas via a LangGraph custom stream writer as it drains the provider stream; `sse.py` publishes them as the new `token` frame type alongside `updates`.
- External `POST /v1/agents/{code}/runs` (already SSE) now carries token frames; document the new frame type + channels for external clients. `mode: "queue"` unaffected.
- **PII redaction moves pre-emit**: deltas are redacted before they leave the process (today redaction runs on the assembled message; streaming must redact each delta). This is the one genuinely harder cross-cut in P2a — pin the streaming-redaction approach in its plan.
- Trajectory / trace / usage: assemble the full message from deltas for persistence; stored shape unchanged. Token frames are ephemeral (not persisted as such); history/resume still renders the stored final message.

**Verification:** end-to-end token frames over SSE; old client ignoring `token` frames still renders per step; redaction masks a secret split across two deltas; usage counted once (not double on the assembled path); `mode: "queue"` returns whole.

### P2b — Playground streaming adaptation

**Deliverable:** the playground renders live tokens; the full adaptation, not just a typewriter.

- **a. Provisional + authoritative dual track.** Token frames accumulate a provisional answer (typewriter, plain text); the step-end `updates` frame replaces it with the authoritative message (markdown). Reuses the existing running-vs-settled split at `PlaygroundTab.tsx:2131`, now driven by real tokens.
- **b. Step attachment.** Token frames carry `step`; attach to the active TurnCard step; StepTimeline highlights the streaming step.
- **c. Reasoning channel.** `reasoning` deltas render a live, collapsible "thinking…" view (the debug console already has a thinking view; this makes it live).
- **d. Tool-call streaming.** Reassemble `tool_args` fragments; surface the tool call when complete (not char-by-char args).
- **e. Mid-stream stall/error.** `idle_timeout` (post-first-token) → show the partial answer + an interrupted badge; `first_token_timeout` fallback is transparent (optionally surface "retrying on <provider>"). Extend the existing turn `status: "error"` to a partial+interrupted state.
- **f. History/resume unchanged.** Token frames are live-only; historical turns reconstruct from stored final messages (existing path). Only live turns get the typewriter.
- **g. Observability additive.** StepTimeline / per-step timing / Langfuse trace unchanged; optionally add a TTFT metric (time to first token).
- **h. Cancellation.** Stop (AbortController) aborts the SSE; the backend `CancellationToken` stops the provider stream read; partial text is preserved. Verify the backend actually cancels the provider SSE read mid-stream.
- **i. Render performance.** Batch token application (rAF / ~50ms coalesce) to avoid a re-render per token; plain text while streaming (no markdown reflow), markdown when settled.

**Verification:** component tests — token frames accumulate into the answer; reasoning renders separately; a step-end frame finalizes to markdown; an interrupted stream shows partial + badge; unknown/absent token frames fall back to step rendering; batching coalesces bursts.

---

## Hard Sub-Problems (and how each is handled)

| Problem | Resolution |
| --- | --- |
| Tool-call arguments stream as fragments | Reassemble by `index` (OpenAI) / `input_json_delta` (Anthropic) into a complete tool call before dispatch |
| Error can arrive after a 200 OK (in-band SSE `error`) | The current `status >= 400 → classify` no longer suffices; parse in-band error events → `LLMError` subclasses; retryable only before first token |
| `usage` arrives in the final chunk | Read from the terminal delta; never double-count when a run is retried (usage attaches to the committed attempt only) |
| Mid-stream cancellation | Reuse `CancellationToken`; abort the httpx stream read; assemble whatever arrived; count partial usage |
| Cache hit has no stream | Synthesize a one-delta stream or bypass the idle driver; cached responses return whole and never time out |
| Structured output (RT-1) | Partial JSON streams fine; validate the assembled message at the end, unchanged |
| Streaming redaction (P2a) | Redact each delta before emit; handle a secret split across delta boundaries (buffer a small tail) |
| Fallback ⊗ streaming | Buffer-until-first-token (approved): fall over only before the first token |

---

## Backward Compatibility & Rollout

- P1: internal only — assembled `AIMessage` is byte-equivalent to today's for the same inputs; a regression fixture guards this. Deadline strictly more permissive.
- P2a: token frames additive; old clients unaffected.
- P2b: playground degrades to step rendering when token frames are absent (P1-only backend).
- `stream_deadline_s` compat alias for one release (or loader migration) — pinned in the P1 plan.
- Optional platform flag to gate token-frame emission (P2a) for staged rollout.

## Out of Scope / Deferred

- Char-by-char rendering of tool-call arguments (assemble-then-show is enough).
- Streaming the raw provider bytes to external clients (we emit normalized `token` frames, not vendor SSE).
- Per-vendor thinking-block presentation nuances beyond a single `reasoning` channel.
- Lowering the `idle_timeout_s` / `first_token_timeout_s` defaults aggressively — start conservative, tune later with telemetry.

## Open Items to Pin During Planning

- Exact home + names of `first_token_timeout_s` / `idle_timeout_s` (`ModelSpec` vs `AgentSpec`) and the `stream_deadline_s` compat mechanism.
- Exact `token` frame field names + whether any token data is persisted.
- Streaming-redaction buffering strategy for secrets spanning delta boundaries.
- LangGraph stream-writer wiring for token emission from `agent_node`.
