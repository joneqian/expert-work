# billing-rollup-job — Stream Y.4

Offline rollup that derives the per-tenant monthly **billing ledger**
(`tenant_billing_ledger`) from the G.9 `token_usage` meter + the Y3
`model_rate_card`.

For a target month, the job iterates every tenant, reads that tenant's
`token_usage` rows for `[month_start, next_month_start)`, prices each row by the
rate effective at *its* `observed_at` (rates are temporally versioned), and
aggregates cost into `(tenant, month, provider, model, agent_name)` buckets,
which it **upserts**.

Pure derivation ⇒ **idempotent**: re-running for a month recomputes + overwrites
that month's buckets (`on_conflict_do_update`), never double-counts. Changing a
rate and re-running deterministically re-prices.

* Tenant `token_usage` reads + ledger writes run **under each tenant's RLS
  scope** (so the tenant-scoped ledger `WITH CHECK` passes).
* `model_rate_card` resolution runs under `bypass_rls` (it's a NULL-tenant
  platform table).
* A usage row whose provider can't be derived (unknown / ambiguous model) or
  for which no rate matches is recorded **unpriced** (`priced=false`, costs 0)
  under a `provider="unknown"` bucket — never silently dropped.

This job does **not** touch the LLM / quota hot path. Run hourly for the current
month; finalize once after month end.

## Run

```bash
uv run python -m billing_rollup_job          # default = current month
EXPERT_WORK_BILLING_ROLLUP_TARGET_MONTH=2026-05 uv run python -m billing_rollup_job
```
