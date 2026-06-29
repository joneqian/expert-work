# Tool-result context budget — universal externalization + conversation compaction

Status: proposed
Owner: agent-runtime
Related: Stream CM-5 (`tools/overflow.py`), workspace (J.15), `read_document` / `read_file`

## Problem

A live run (`254cdad8`) stalled in a retry loop. Root mechanism was a too-tight
stream deadline (fixed in #857/#858), but the *driver* was **context bloat**: the
agent ran ~8 `web_search` calls; each result rendering (5–10 hits × 4096 chars ≈
20–40k chars) stayed in the message history verbatim, and accumulated across turns
to ~**140k input tokens**. Every subsequent LLM call then had to prefill 140k
tokens → slow TTFT → deadline/timeout → expensive + fragile.

The bloat is two-dimensional:

1. **Per-result**: a single large tool result enters context at full size.
2. **Cumulative**: old results are never reclaimed; they ride every later turn.

## Prior art

| | Hermes | deer-flow | helix (today) |
|---|---|---|---|
| Oversized single result | not pre-capped; **old** results pruned to 1-liners on a threshold-triggered compaction pass (lazy) | **per-result, eager**: `> externalize_min_chars` (12k) → full to disk + head/tail preview + file ref (`tool_output_budget_middleware`) | **CM-5 externalization exists** but only fires when a tool sets `ToolResult.full_content` (bash / exec_python / http / mcp). `web_search` never sets it → not externalized |
| Heavy research isolation | inline search; `delegate_task` only for short subtasks | lead-agent + subagents that **return summaries** (Task tool) | `spawn_worker` exists but optional (prompt-decided) |
| Conversation-level | `context_compressor`: token-budget tail protection + prune-old-tool-results + LLM summary | `summarization_hook` | — (none) |

**Key finding:** helix already has deer-flow's externalization primitive (CM-5,
`tools/overflow.py` + `builder._externalize_tool_overflow`) — it is just **opt-in
per tool**. Generalizing it to a size threshold over *all* tool results is a small,
surgical change that directly kills the search-bloat case.

**Reliability principle (decided with owner):** prompt-driven mitigations
(method 1 "delegate research to a worker", method 2 "summarize as you go") are
**model-decision-dependent → unreliable** — exactly the failure seen in the live
run. The load-bearing fix must be **mechanical** (fires regardless of model
behavior). Worker delegation only becomes reliable under *structural* enforcement
(orchestrator has no direct search tool — a different architecture), and is
**redundant for bloat** once per-result externalization exists. So: mechanical
externalization is primary; prompt guidance is at most a minor complement.

## Design

Two phases. Phase 1 is the acute fix (eager per-result) and reuses CM-5
end-to-end. Phase 2 is the deeper cumulative layer.

### Phase 1 — universal per-result externalization (eager, mechanical)

Generalize the existing CM-5 path in `graph_builder/builder._externalize_tool_overflow`
so it triggers on **rendered-content size**, not only on `full_content` presence.

For each tool result the tools node produces:

1. Determine the source = `result.full_content` if set (the uncapped rendering),
   else `result.content`.
2. If `len(content_for_budget) <= externalize_min_chars` → pass through unchanged
   (no overhead for small results).
3. Else, and the tool is **not exempt** (see below):
   - Write the full source to the workspace at
     `.tool_results/<run_id>/<call_id>-<tool>.txt` (existing `overflow_rel_path`
     + `WorkspaceFileWriter`, `clamp_overflow` cap unchanged).
   - Replace the in-context `ToolMessage` content with a **head + tail preview**
     (`preview_head_chars` + `preview_tail_chars`) + the existing
     `render_overflow_footer` reference (extended to describe head/tail elision).
4. On write failure → **fall back to head+tail truncation in place** (never blow
   context; degrade, don't error — same best-effort contract as CM-5 today).

Config (`ToolOutputBudgetConfig`, settings-driven, per-tool overrides), defaults
mirror deer-flow:

| field | default | note |
|---|---|---|
| `enabled` | `true` | |
| `externalize_min_chars` | `12_000` | trigger threshold |
| `preview_head_chars` | `2_000` | kept from head |
| `preview_tail_chars` | `1_000` | kept from tail |
| `fallback_max_chars` | `30_000` | when workspace write unavailable |
| `exempt_tools` | `{read_document, read_file, list_dir}` | the fetch-back path — prevents persist→read→persist loops |

Exemptions: `read_only` tools are *already* skipped by CM-5 (CM-F3). Keep that,
and additionally never externalize the explicit fetch-back tools so the agent can
always pull the full file back into context on demand.

Secondary (same phase, optional): have `web_search` set `full_content` to the
**uncapped** results so the externalized file is complete rather than the
per-result-4096-capped rendering. Low-risk, makes the saved file authoritative.

**Spotlight (PI-1b) interaction:** the head+tail preview is still untrusted tool
output → it must stay inside the spotlight fence; the helix-owned overflow footer
stays trusted (outside), exactly as today. The generalization must preserve this
ordering (`spotlight_untrusted(preview) + footer`).

### Phase 2 — conversation-level compaction (cumulative, fallback)

When the *total* request still exceeds a budget (many medium results, long
history) even after Phase 1, compact the message history. Mirror Hermes
`context_compressor`:

1. **Tail protection by token budget** — keep the most recent messages verbatim
   (token-budgeted, with a small message-count floor). The model always has its
   recent working set intact.
2. **Cheap mechanical pre-pass** (no LLM): prune *old* tool results to per-tool
   1-line summaries (`[web_search] "<query>" → N results`), dedup identical
   results, truncate stale oversized tool-call args.
3. **LLM summary fallback** — only if still over budget, summarize head/middle via
   the existing aux model (`control_plane/aux_model_adapter`, `memory_consolidator`
   patterns). Full history stays in the checkpoint/workspace = recoverable.

Trigger = request tokens vs a percentage of the model context window
(`threshold_percent`). Interacts with the checkpointer (compaction rewrites
state) — design + eval carefully; **Phase 2 ships separately** after Phase 1 is
proven.

## Decisions

1. **Mechanical, not prompt.** Per-result externalization fires deterministically;
   no reliance on the model choosing to delegate/summarize.
2. **Eager (deer-flow), not lazy (Hermes).** Cap each result at entry so a single
   140k result never enters context, rather than letting it in and pruning later.
   Simpler reasoning + reuses CM-5.
3. **Reuse CM-5, don't build a parallel middleware.** Generalize the existing
   `_externalize_tool_overflow` trigger from `full_content`-present to
   size-threshold. Workspace target, footer, metrics, loop-guards already exist.
4. **Recoverable, never lossy.** Full content always lands in the workspace
   (`.tool_results/`), reachable via `read_file`/`read_document`/`bash`. The
   preview is head+tail, not a blind head cut.
5. **Worker delegation (method 1) is out of scope** as a bloat fix — unreliable
   (prompt-decided) and redundant once Phase 1 lands. Remains available for
   *parallelism*, not context hygiene.

## Risks

- **Information loss / extra round-trip.** The agent may need content beyond the
  head/tail preview → must `read_file` the externalized file. Mitigation: head+tail
  (not head-only); generous-enough preview; the footer states the full path + size.
- **Eval regression.** Externalizing could hurt task success if the model fails to
  fetch back. Mitigation: run the eval engine (LongMemEval / locomo harness) before
  and after; gate on no recall/accuracy regression.
- **Double counting with per-tool caps.** `web_search` already caps each result at
  4096 chars; externalization is a second layer. That is fine (the file holds the
  uncapped set once the secondary change lands); just avoid re-externalizing an
  already-externalized message (idempotency by detecting the footer marker).
- **Workspace dependency.** No `user_id` (ephemeral run) → no persistent
  workspace; fall back to in-place head+tail truncation (still bounds context).

## Testing / verification

- **Unit**: threshold boundary (just under / over), head+tail preview shape,
  exempt tools pass through, write-failure fallback truncation, footer idempotency,
  spotlight ordering preserved, per-tool override.
- **Integration (real PG/workspace)**: large `web_search` result → file written to
  `.tool_results/`, `ToolMessage` carries preview+ref, `read_file` returns the full
  content.
- **Live (verify_live)**: replay the 140k research task → input tokens per step
  stay bounded (no 90s/180s prefill stall); confirm the agent reads back a file
  when it needs detail.
- **Eval**: no task-success / recall regression vs baseline (the bloat fix must not
  cost answer quality).

## Rollout

1. Phase 1 behind `tool_output_budget.enabled` (default on; flip off to revert).
2. Ship + live-verify the research task.
3. Phase 2 (conversation compaction) as a separate design iteration once Phase 1
   is proven and eval'd.
