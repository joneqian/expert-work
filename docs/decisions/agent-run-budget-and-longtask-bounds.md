# Agent Run Budgeting & Long-Task Bounds

Status: **partially implemented** (2026-07-09) — long-task Gap #1 (wall-clock floor) + no-progress stop shipped; token/$ budget (方案 C) still deferred.
Related: [STREAM-E-DESIGN](../streams/STREAM-E-DESIGN.md) § 1.1 E.6 (ReAct loop guard); Mini-ADR J-40 (`run_deadline_s`); [deer-flow-context-mgmt-alignment](./deer-flow-context-mgmt-alignment.md).

## Update — 2026-07-09 (what shipped)

Two of the findings below were built (the rest stays deferred):

- **Long-task Gap #1 — platform-default wall-clock floor.** `settings.default_run_deadline_s` (default **3600s / 1h**, `le=86400`) is threaded through the lifespan → `make_agent_builder` → `build_agent`, where `_effective_run_deadline_s(manifest, default)` applies it **only when the manifest leaves `policies.run_deadline_s` at 0** (manifest always wins). The four deadline-setter sites are unchanged — `built.run_deadline_s` already carries the floor. So an unconfigured run now has a wall-clock ceiling; a genuinely long agent raises `run_deadline_s` explicitly.
- **No-progress stop (方案 ②).** `policies.max_no_progress` (default **0 = off**) + a new `AgentState.no_progress_streak`: `agent_node` increments the streak on each loop-detection trip and resets it on a clean turn; once it reaches `max_no_progress` the run routes into the **existing** tool-less graceful wrap-up (same gate `max_steps` uses). The first trip still arms one higher-effort turn (`escalate_next`); this caps consecutive unproductive turns before stopping.

**Still deferred:** token/$ per-run budget (方案 C, cost-motivated); Gap #2 (dedicated Reporter synthesis role); the deadline→graceful-wrap-up rewiring (deadline still enforced at delegation boundaries + cancellation, not the wrap-up gate).

## TL;DR / Decision

The question that started this: *"a hardcoded `max_steps` isn't optimal — should we add a token/$ budget and/or heartbeat/lease?"*

Conclusion after investigation + benchmarking (deer-flow, Hermes Agent, Temporal):

1. **Heartbeat/lease is a different axis from `max_steps`** — liveness/ownership, not productivity. We already have it (Stream 9.4). It does **not** replace `max_steps` (a heartbeating agent can still be a runaway).
2. **A token/$ per-run budget (方案 C) is cost-motivated.** Pre-cost stage → **YAGNI, deferred.** The mechanism is settled and cheap; build it when cost/billing becomes real pressure.
3. **The current suite is already industry-aligned** (matches deer-flow + Hermes, leads them on several points). It is adequate to ship on until cost matters.
4. **`max_steps` alone is *not* sufficient for genuinely long tasks** — but the *composite* of existing mechanisms is, with **two real gaps** (see § Long-Task Analysis). Gap #1 (the wall-clock floor) is now shipped; Gap #2 stays deferred.

Token/$ budget (方案 C) is the pickup point for the cost era; the wall-clock floor + no-progress stop shipped 2026-07-09 (see § Update).

---

## Two orthogonal axes

| Axis | Question | Instrument here | Analogue |
|------|----------|-----------------|----------|
| **A — Productivity** | "Doing useful work, or spinning?" | `max_steps` (step count) | LangGraph `recursion_limit`, Hermes `iteration_budget` |
| **B — Liveness/ownership** | "Is the process alive, or crashed?" | lease heartbeat + orphan reclaim | Temporal activity heartbeat, K8s lease, SQS visibility timeout |

**They do not substitute.** A live, heartbeating agent stuck in a useless tool-loop renews its lease forever while burning budget — only axis A stops it. A crashed process makes zero steps forever — only axis B detects + reclaims it.

---

## What expert-work already has (the suite)

Axis A (productivity):
- **Step cap** — `DEFAULT_MAX_STEPS = 20` (`state.py:43`), overridable per manifest via `workflow.max_iterations` (`agent_factory.py:994`); clamped for sub-workers.
- **Graceful wrap-up (not hard kill)** — `budget_exhausted` → force `tools=[]` + wrap-up instruction, preserving produced work (`builder.py:513, 770-774`). Cites Hermes #7915.
- **Effort escalation @ 75% budget** — `step_count*4 >= max_steps*3` → higher-effort model to converge (`builder.py:730, 763`).
- **Iteration refund** — overhead/tool steps can refund their iteration so they don't eat the budget (`state.py:114-121`).
- **Loop detection** — `LoopDetectionMiddleware` (window=3, SHA256 tool-call fingerprint): identical calls ×3 → `loop_detected` → `escalate_next` (`middleware/loop_detection.py`, `state.py:192-194`). Currently escalates effort; never stops.
- **Hard backstop** — `MaxStepsExceededError` if the wrap-up turn still demands tools (`errors.py:21`, `sse.py:606`). Distinct eval bucket `outcome="max_steps"`.
- **Wall-clock deadline** — `policies.run_deadline_s` (default **0 = off**, `le=86400`) → `deadline_at = monotonic()+run_deadline_s`, shared across the parent→child tree, checked at delegation boundaries (`agent_spec.py:857`, `tools/spawn_worker.py:170`, `tools/subagent.py:141`).
- **Sub-agent caps** — `dynamic_worker_max_iterations=16` (le=64), `dynamic_worker_max_per_run=16`, `dynamic_worker_max_concurrent=3` (`settings.py:593-603`).

Axis B (liveness):
- **Lease heartbeat** — `_heartbeat_loop` renews every `lease_ttl_s/3`; lost lease → `abort_event` (`sse.py:236`).
- **Orphan sweep + reclaim + resume** — scans `status=running` with expired lease → CAS reclaim → re-spawn `run_agent(graph_input=None)` resuming from durable LangGraph checkpoint; per-run reclaim cap (`orphan_sweep.py`).

Cost accounting (present, feeds the deferred budget):
- **Per-call token metering** — `TokenUsageMiddleware` → `TokenUsageStore` (`middleware/token_usage.py`).
- **Tenant monthly token budget** — `QuotaService.reserve_tokens/commit_tokens/release_tokens`, Postgres ledger + reaper (`quota/base.py`, `quota/reaper.py`).
- **Model price table** — `ModelRateCardRecord` (`api/rate_card.py`); usage rollups `GET /v1/usage/tokens` (`api/usage.py`).

---

## Benchmark comparison

| Concern | deer-flow | Hermes Agent | expert-work |
|---------|-----------|--------------|-------------|
| Loop bound | `AGENT_RECURSION_LIMIT` 30 / `recursion_limit`, `max_plan_iterations`, `max_step_num` | double gate: `max_iterations` + shared `iteration_budget` (90) | `max_steps` (20) + spawn/worker caps |
| Loop detection | warn @3 identical → hard-stop @5 (issue #1055) | warn on repeated/idempotent no-progress | `LoopDetectionMiddleware` window=3, warn→hard-stop — **same design as deer-flow #1055** |
| Budget-exhausted | strip tool_calls, wrap up | `_handle_max_iterations`: 1 toolless grace call (#7915) | `tools=[]` + wrap-up — **= Hermes #7915, but hard (leads Hermes #36239 "should be hard stop")** |
| Pressure warning | — | open feature #414 | ✅ effort escalation @75% — **leads Hermes #414** |
| Exhaustion safety | — | once silently killed process (#8049) | ✅ graceful wrap-up preserves work |
| Renewal authority | **human**: plan-approval + tool-approval interrupts (`auto_accepted_plan` bypass) | open "Loop Contract" #21172 (budget/stop/refresh/scope) | approval gates exist; no plan-as-renewal |
| Cost budget (token/$) | iteration-denominated | iteration-denominated | **none (deferred — 方案 C)** |
| Liveness/durability | LangGraph checkpoint | — | ✅ full lease+reclaim+resume (Temporal-class) |

Neither benchmark uses a token/$ budget — both gate on iterations. A per-run token/$ budget would put us **ahead of both** on the cost axis (industry precedent: PydanticAI `UsageLimits`).

---

## Long-Task Analysis: is `max_steps` enough?

"Long task" = legitimately needs many steps / long wall-clock (deer-flow's own scope: "minutes to hours").

### `max_steps` **alone** is insufficient for long tasks

1. **Poor progress proxy exactly where it matters.** For short tasks the cap rarely binds. Long tasks live *at* the boundary, where a 60th-productive-step and a 60th-spinning-step are indistinguishable to a counter.
2. **Unresolvable tuning tension.** Set the cap high enough for the longest legit run (say 100) and it is simultaneously too loose as a runaway guard for that same agent. One static number cannot be both a generous ceiling and a tight guard. Short tasks escape this (cap sits well above typical); long tasks cannot.
3. **`max_steps` bounds one node, not the task.** Long tasks fan out. The real envelope is composite:
   `parent max_steps (20) + spawn_budget (16) × worker max_iterations (16) = ~276 step-equivalents`, all under one shared wall-clock. `max_steps=20` is just the root loop's bound — it *understates* a fanning long task by ~14×.
4. **A step is a coarse time unit.** A step is 2 s (cheap call) or 5 min (sandbox build / deep crawl). 20 steps = 40 s or 100 min. For long tasks the per-step variance is huge, so step count says little about elapsed work.

### The **composite suite** is adequate

Long tasks are actually bounded by `max_steps` **+** spawn/worker caps **+** wall-clock **+** loop-detection **+** checkpoint-resume together. As a suite, termination and runaway-protection hold even when `max_steps` alone is a weak instrument. `max_steps` remains valid as the **structural termination backstop** (guarantees the root loop halts regardless of task length).

### Two real long-task gaps

1. **✅ RESOLVED — wall-clock floor shipped.** Was: `run_deadline_s` defaulted to 0 = OFF with no platform-level default, so a multi-hour agent relying only on `max_steps` had no wall-clock ceiling. Now: `settings.default_run_deadline_s` (1h default) supplies the floor when the manifest leaves it 0 (manifest wins); see § Update. Long agents still raise `run_deadline_s` explicitly.
2. **Single inline wrap-up may under-synthesize.** One toolless wrap-up turn must compress possibly hours of accumulated (and maybe compacted/lossy) state into one answer. deer-flow uses a dedicated **Reporter** role for synthesis. **Still deferred** — lower priority; revisit if long-task final-answer quality proves weak.

---

## Deferred: cost-era design (方案 C)

Build when cost/billing becomes real pressure.

**Mechanism** (additive, reuses the existing graceful-wrap-up gate — no new stop path):
```python
budget_exhausted = (max_steps > 0 and step_count >= max_steps)              # unchanged
                or (run_token_budget > 0 and token_count >= run_token_budget)  # new OR term
```
- New `AgentState.token_count` channel (mirror `step_count`; survives checkpoint → resume does not reset).
- Accumulate from each LLM response `usage_metadata` where `step_count` is bumped.
- New `policies.run_token_budget: int = 0` (mirror `run_deadline_s`).
- Distinct `outcome="token_budget"` bucket. Generalize the final `MaxStepsExceededError` hard-stop to "budget exhausted (step or token)".
- `max_steps` stays and keeps intercepting — the two catch different failure modes (many-cheap-turns vs few-expensive-turns). `max_steps` is also the loop-liveness floor when the token budget is off.

**Who / when / how-much** (the hard part — avoids re-creating the magic-number problem):
- **Who — layered, min-wins** (only tightens): platform default (safety net) → tenant monthly budget (`QuotaService`, exists) → per-agent `run_token_budget` (author, primary knob) → optional caller override. Same pattern as `min(workflow.max_iterations, ceiling)` (`subagent_runtime.py:104`).
- **Per-run is a new granularity**, not a duplicate: tenant-monthly stops sustained overspend; per-run stops a single runaway from eating the month in one loop.
- **When**: manifest at authoring time (versioned); resolved once at run start into `configurable` (like `deadline_at`). Optional v2: reserve from the monthly ledger (`reserve_tokens`) + reap on crash.
- **How much — measure, don't guess**: ship warn-only; read the per-agent spend distribution from `/v1/usage/tokens` (already collected); set at **p95/p99 × safety factor**. First-guess default derivable as `max_steps × per-turn-token estimate`. **Denominate the human knob in $** (via rate card) so it is a business decision, not an arbitrary count. Warn-rate telemetry self-tunes.
- Verify before building: does the token store key by `thread_id`/`run_id` for a true per-run distribution.

**Scope decisions when revived**: v1 = pure metering (not ledger reservation); main-run only (not tree-shared); wire streak→stop on the existing narrow loop-detector without widening it; `max_no_progress` default 2. Circuit-breaker (token-velocity), $ denomination, and tree-shared budget are v2.

---

## Triggers to revisit

- Cost/billing becomes a real constraint → build 方案 C (token budget), starting warn-only. **(still open)**
- Ship a long/multi-hour agent → set `run_deadline_s` to exceed the 1h platform floor if the task genuinely needs longer (gap #1 floor now shipped). ✅
- Long-task final-answer quality proves weak → consider a synthesis/Reporter role (gap #2). **(still open)**
- No-progress runs waste user time (not cost) → shipped: `policies.max_no_progress` (方案 ②). ✅ Follow-up: widen the loop-detector beyond identical-fingerprint (cyclic A→B→A→B, semantic stall), and generalize the final `MaxStepsExceededError` label to cover the stuck path.
