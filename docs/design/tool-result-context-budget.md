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
| Conversation-level | `context_compressor`: token-budget tail protection + prune-old-tool-results + LLM summary | `summarization_hook` | **two gates already exist** — CM-2 `WorkingWindow` (cheap, drops whole old turns) + L.L2 `ContextCompressor` (LLM summarise-the-middle, head/tail keep, CM-7 running summary, CM-3 pre-compaction memory flush). Missing: the cheap **mechanical tool-result prune** in between |

**Key finding (per-result):** helix already has deer-flow's externalization primitive
(CM-5, `tools/overflow.py` + `builder._externalize_tool_overflow`) — it is just
**opt-in per tool**. Generalizing it to a size threshold over *all* tool results is a
small, surgical change that directly kills the search-bloat case.

**Key finding (conversation-level) — corrects an earlier premise:** the
conversation-level layer is **not absent**. helix already ships a layered cascade in
`agent_node`: CM-2 `WorkingWindow` (LLM-free, token-gated, drops whole old turns) →
L.L2 `ContextCompressor` (LLM summarise-the-middle, head/tail keep, CM-7 running
summary, CM-3 flushes the discarded middle to long-term memory). Hermes' "tail
protection" and "LLM summary" rungs therefore **already exist**. The one genuinely
missing rung is the cheap, surgical **prune-old-tool-results** middle gate: helix
jumps straight from *drop-whole-turn* (coarse — also loses the assistant's reasoning)
to *LLM-summary* (an extra model call), with nothing in between that collapses only
the bulky **tool outputs** while keeping the full turn/reasoning structure intact.

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

### Phase 2 — mechanical tool-result prune gate (CM-12)

Phase 2 is **narrower than originally scoped**: the conversation-level cascade
already exists (CM-2 `WorkingWindow` + L.L2 `ContextCompressor` — see the corrected
prior-art finding above). Hermes' tail-protection and LLM-summary rungs are already
shipped. Phase 2 adds **only the one missing rung**: a cheap, LLM-free gate that
collapses *old* tool results to 1-line references while keeping every turn and the
assistant's reasoning intact — the graceful step between "drop the whole turn" and
"pay for an LLM summary".

New module `orchestrator/context/tool_result_prune.py` → `ToolResultPruner`,
mirroring `WorkingWindow`'s shape (frozen dataclass, token-gated, prompt-view-only).

**Ordering — runs FIRST, before `WorkingWindow`:** it is the least-lossy and
cheapest gate, so running it first means the later, coarser gates re-estimate against
a smaller prompt and fire less often (often not at all). Final cascade in `agent_node`:

```
messages = state["messages"]
→ ToolResultPruner.apply()   # CM-12 — collapse OLD tool results to references (new)
→ WorkingWindow.apply()      # CM-2  — drop whole old turns
→ inject plan / memory / advisory
→ ContextCompressor.compress()  # L.L2 — LLM summarise the middle
```

**Mechanism (single pass, no LLM):**

1. Token-gate exactly like the other two gates: no-op unless the estimate is
   `>= context_window * threshold_pct` (zero behaviour change for normal runs).
2. Protect the most-recent `recent_tool_results_kept` `ToolMessage`s (count-based,
   so it is robust to *many tool calls in one turn* — the actual search-bloat shape —
   which a turn-based window would miss). Collapse every older `ToolMessage`:
   - **Phase-1-externalized result** (carries the `<tool-result-overflow>` footer):
     replace the content with the **footer alone** — the full output is already on
     disk under `.tool_results/`, so this is **lossless** and the model can
     `read_file` it back. (Bonus: drops the untrusted spotlight-fenced preview,
     improving the trust posture.)
   - **non-externalized result** (small, no footer): replace with a short
     `<tool-result-pruned>[tool] N chars elided (older context)</tool-result-pruned>`
     stub — lossy, but it is old, and strictly *less* lossy than the whole-turn drop
     `WorkingWindow` would otherwise do to the same span.
3. Pairing-safe by construction: prune only **rewrites `ToolMessage` content**, never
   removes a message, so no `AIMessage.tool_calls ↔ ToolMessage` pair is ever split
   (no boundary logic needed, unlike `WorkingWindow`).
4. **Prompt-view only** — identical contract to `WorkingWindow` (CM-C4): the gate
   mutates the local prompt list `agent_node` sends to the LLM; the node returns only
   the new response tail, and the `add_messages` reducer never deletes, so the
   **checkpoint keeps full, un-pruned history**. The next turn reloads the full
   history and prunes afresh.
5. **Idempotent**: a content that already starts with `<tool-result-pruned>` (stub)
   or `<tool-result-overflow>` (footer-only ⇒ already pruned) is skipped.

Config (`ToolResultPrunePolicy`, per-agent, defaults zero-behaviour-change under
threshold):

| field | default | note |
|---|---|---|
| `enabled` | `true` | mirrors CM-2 / L.L2 — gated, so off-threshold runs are untouched |
| `threshold_pct` | `0.7` | same basis/estimator as the other gates (CM-C6) |
| `recent_tool_results_kept` | `4` | last N `ToolMessage`s kept full |

**Synergy with Phase 1:** because #859 already lands the full output of any large
result on disk, pruning an externalized result to its footer is *lossless*. Phase 2 is
the reclaim half of Phase 1's externalize half. Note that after Phase 1 the acute
single-result case rarely trips Phase 2 at all (each big result is already a ~3 KB
preview); Phase 2's niche is the **cumulative** case — many medium results summing
over the window.

**Out of scope for this pass** (was in the original Phase 2 sketch, now redundant or
deferred): dedup of identical results and stale-arg truncation (low value once
tool-output bulk is collapsed); externalize-on-prune for *small* non-externalized
results (would make the stub recoverable too, but couples the prune path to the
workspace writer — defer until a need appears). The LLM-summary fallback is **already
shipped** (L.L2) and unchanged.

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
6. **Phase 2 is the prune gate only, not a new conversation compactor.** Exploration
   corrected the premise: CM-2 `WorkingWindow` + L.L2 `ContextCompressor` already
   provide tail protection and LLM summary. Building a parallel Hermes-style
   compressor would duplicate shipped code. Phase 2 adds the one missing rung
   (mechanical tool-result prune) and runs it *first* (cheapest, least lossy).
7. **Count-based recent protection, not turn-based.** The bloat shape is *many tool
   calls* (possibly within one turn), which a turn-based window would not relieve;
   protecting the last N `ToolMessage`s directly bounds how many full tool outputs
   ride in context.

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

- **Unit (Phase 1)**: threshold boundary (just under / over), head+tail preview shape,
  exempt tools pass through, write-failure fallback truncation, footer idempotency,
  spotlight ordering preserved, per-tool override.
- **Unit (Phase 2 / CM-12)**: under threshold → no-op; over threshold → old tool
  results collapsed, last N protected full; externalized result → footer-only
  (lossless, full content unreferenced); non-externalized → stub with char count;
  pairing preserved (message count unchanged, every `tool_call_id` still has its
  `ToolMessage`); idempotent (prune twice == once); non-`ToolMessage`s untouched;
  multimodal (list) content skipped.
- **Integration (real PG/workspace)**: large `web_search` result → file written to
  `.tool_results/`, `ToolMessage` carries preview+ref, `read_file` returns the full
  content.
- **Live (verify_live)**: replay the 140k research task → input tokens per step
  stay bounded (no 90s/180s prefill stall); confirm the agent reads back a file
  when it needs detail.
- **Eval**: no task-success / recall regression vs baseline (the bloat fix must not
  cost answer quality).

## Rollout

1. Phase 1 behind `tool_output_budget.enabled` (default on; flip off to revert). ✅ #859
2. Ship + live-verify the research task.
3. Phase 2 = CM-12 mechanical prune gate (`policies.tool_result_prune`, default on,
   token-gated). Per-agent `enabled: false` reverts. Ships after Phase 1 is in main.
