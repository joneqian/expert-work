# Memory Consolidator Runbook

> Capability Uplift Sprint #7 — `MemoryConsolidator` background worker
> (Mini-ADRs U-33 ~ U-42). Implementation:
> [`services/control-plane/src/control_plane/memory_consolidator.py`](../../services/control-plane/src/control_plane/memory_consolidator.py).
> Design context:
> [`docs/streams/STREAM-UPLIFT-DESIGN.md`](../streams/STREAM-UPLIFT-DESIGN.md) § 8.

## 1. What the consolidator does

The consolidator is a control-plane background worker that runs every
4 hours (configurable). Each sweep enumerates every tenant with live
`status='transient'` memory rows, then for each `(tenant, user)` pair
runs two passes:

* **SUB-PASS 1 (cluster → consolidate)** — embedding pre-filter finds
  groups of similar transient items; one LLM call per cluster verifies
  + summarises + applies the Hermes 4 + Expert Work 2 anti-mislearn rules in
  a single three-in-one prompt. On `keep=true` the consolidator writes
  a new `status='consolidated'` row with `consolidated_from=<source
  ids>` and links the sources back via `consolidated_into=<new id>`
  atomically. The default `MemoryStore.retrieve()` WHERE then skips
  the raw transient sources (prevents double-counting raw + summary).

* **SUB-PASS 2 (lone-item noise purge)** — sweeps aged transient items
  that have **never** been retrieved and **never** been reviewed; a
  single LLM call classifies each as `durable` or one of the noise
  categories. Noise rows are `soft_delete`-d and audited; durable rows
  get a `last_reviewed_at` stamp so future ticks skip them.

The worker is **single-replica** (same constraint as `TriggerScheduler`
+ `SkillCurator`). Re-running a sweep is safe — both passes filter on
columns the previous run sets, so duplicate ticks are wasted work but
not data corruption.

## 2. Default thresholds (per tenant)

Stored on `tenant_config`; mutable via `PUT /v1/tenants/{tid}/config`.
Defaults:

| Field | Default | Bounds | Tuning hints |
|-------|---------|--------|--------------|
| `memory_consolidation_min_cluster_size` | 3 | 2..20 | Below 3 = too eager; above 5 = real clusters missed |
| `memory_consolidation_similarity` | 0.85 | 0.7..0.99 | Below 0.7 = false positives; above 0.95 = only near-paraphrase |
| `memory_purge_enabled` | true | bool | Disable for high-compliance tenants; consolidate path still runs |
| `memory_purge_min_age_days` | 30 | 7..365 | 30 d = safety margin for fact-of-the-day to be retrieved at least once |

Worker cadence is process-wide:
`EXPERT_WORK_MEMORY_CONSOLIDATOR_INTERVAL_S` (default 14400 = 4 h).
Default aux model: `EXPERT_WORK_MEMORY_CONSOLIDATOR_DEFAULT_AUX_MODEL`
(default `claude-sonnet-4-6`).

## 3. Aux-model wiring (Sprint #7 ships a no-op default)

Sprint #7 ships the worker + schema + audit + observability **without
committing to a specific LLM client wire-up**. The default
`_NullConsolidatorAuxModel` returns valid-shape JSON for both prompt
families (`false_cluster` for cluster prompts; `durable` for
single-item review prompts), so the worker runs end-to-end but
**produces zero consolidations and zero purges until a real adapter
is wired**. This is intentional — the schema + worker + audit + metrics
are the production-grade infrastructure; the LLM client choice is M1
work (likely a thin wrapper over the orchestrator's `LLMRouter`).

When wiring a real aux model:

1. Implement `ConsolidatorAuxModel.__call__(*, prompt, model)` returning
   `ConsolidatorLLMReply(text, model, input_tokens, output_tokens)`.
2. Replace `make_null_consolidator_aux_model()` in
   `control_plane.app.create_app` with your factory.
3. Confirm `ExpertWorkUpliftConsolidatorIdle` alert clears within 3 days
   of deploy (the consolidator should start producing non-zero
   consolidations).

## 4. Diagnostic: `ExpertWorkUpliftConsolidatorIdle`

**Trigger**: `Expert Work:uplift:memory_consolidated_rate:1d == 0` for 3
consecutive days.

**Most likely causes** (in order):

1. **Aux model is the null default** — Sprint #7 ships with this on
   purpose. Check `control_plane.app` to confirm; wire a real aux model.
2. **`memory_purge_enabled` disabled tenant-wide** — only affects SUB-PASS 2;
   cluster pass still runs. Not a cause of `Idle` unless paired with #1.
3. **Embedding pipeline broken** — check `embedder` resolution in
   `control_plane.app`. `MemoryDLQWorker` similarly depends on it; if
   that's also failing, root cause is embedder, not consolidator.
4. **No transient data** — empty steady state; sometimes a tenant truly
   has nothing to consolidate. Check `SELECT count(*) FROM memory_item
   WHERE status='transient'` per tenant. If zero, suppress the alert
   for that tenant.
5. **All clusters fail anti-mislearn** — check
   `Expert Work:uplift:memory_cluster_rejection_rate:1d{reason}`. If
   dominated by `anti_mislearn:*`, the prompt may be over-rejecting
   (regression — see § 6).

## 5. Diagnostic: `ExpertWorkUpliftConsolidatorPurgeSurge`

**Trigger**: `sum(Expert Work:uplift:memory_purged_rate:1d) > 100` for 1 day.

**Most likely causes**:

1. **`memory_writeback_node` regression** — upstream is leaking
   environment failures / one-off narratives into long-term memory at
   a higher rate than before. Cross-reference
   `Expert Work:uplift:memory_purged_rate:1d{category}` — if dominated by
   `env_failure` or `transient_error`, root cause is upstream. Good
   catch by the consolidator, but file an issue against the writeback
   prompt.
2. **Anti-mislearn prompt regression** — recent change to
   `_ANTI_MISLEARN_RULES` is over-rejecting. Roll back the prompt
   change; re-tune in a separate PR with K.K12 baseline validation.
3. **`memory_purge_min_age_days` set too low** — tenant lowered the
   knob to e.g. 7 days; many recent-but-real facts get purged. Either
   raise the knob back to 30 or accept the higher rate for that tenant.

**Restore deleted memories** (if a purge was a false positive):

```sql
-- Look at the audit log for the specific item.
SELECT id, details FROM audit_log
WHERE action = 'memory:purged_as_noise'
  AND tenant_id = '<tid>'
  AND occurred_at > now() - INTERVAL '7 days';

-- The `details.content_snapshot` field carries the original content.
-- To restore: clear deleted_at on the row id.
UPDATE memory_item SET deleted_at = NULL WHERE id = '<mid>';
```

## 6. Diagnostic: `ExpertWorkUpliftConsolidatorRejectionDominant`

**Trigger**: anti-mislearn rejection ratio (excluding `false_cluster`)
exceeds 50 % of cluster decisions for 1 day.

**Most likely causes**:

1. **`memory_writeback_node` leaking noise** — see § 5 cause #1; same
   root cause, different surface. Anti-mislearn catching noise upstream
   is the **intended** behavior, so this alert is informational rather
   than an immediate page.
2. **Anti-mislearn prompt regression** — over-strict. Verify by sampling
   `MEMORY_CONSOLIDATION_REJECTED` audit rows: if the rejected clusters
   look like real durable facts (e.g. "user prefers dark UI"), regression.

## 7. Manual sweep (operator-only)

There is no operator endpoint to trigger a sweep in Sprint #7. To dump a
diagnostic sweep, attach a debugger / shell to the control-plane pod and
call:

```python
from control_plane.app import create_app
app = create_app()
consolidator = app.state.memory_consolidator
summary = await consolidator.run_once()
print(summary.as_audit_details())
```

The summary is also written as a `MEMORY_CONSOLIDATOR_RUN` audit row
once per sweep — query the audit log for the canonical record.

## 8. Schema reference

`memory_item` columns added by migration `0045_memory_consolidator`:

| Column | Type | Default | Purpose |
|--------|------|---------|---------|
| `status` | `VARCHAR(16)` | `'transient'` | Lifecycle: transient / consolidated / archived |
| `consolidated_into` | `UUID NULL` | NULL | Set on superseded raw transients |
| `consolidated_from` | `JSONB` | `'[]'::jsonb` | Reverse index (only consolidated rows) |
| `last_reviewed_at` | `TIMESTAMPTZ NULL` | NULL | Set by lone-item review pass |

`tenant_config` columns added by migration `0046_memory_consolid_cfg`:
see § 2 table.

Indexes:
* `ix_memory_item_consolidator_scan` (partial,
  `tenant_id, user_id, created_at` where `status='transient' AND
  deleted_at IS NULL`) — backs both SUB-PASS scans.
* `ix_memory_item_consolidated_into` (partial,
  `consolidated_into` where `IS NOT NULL`) — backs retrieve filter +
  reverse lookups.

## 9. Future work (out of Sprint #7)

* **M2-C archive pipeline** — implements `MemoryStore.archive()` and
  exercises the `status='archived'` retrieve filter that Sprint #7 ships.
* **Episodic memory consolidation** — Sprint #7 covers `kind='fact'` only.
* **Real aux-model adapter** — see § 3.
* **Per-tenant LLM cost cap** — `memory_purge_max_per_run` hard-coded
  at 100 today; tenant_config knob is a follow-on if needed.
