# Threat Scanner Tuning — Capability Uplift Sprint #1

> Pairs with `docs/streams/STREAM-UPLIFT-DESIGN.md` § 1 + § 2.
>
> Authoritative source for: pattern-set update process, false-positive
> triage, SecOps alert response, and per-tenant `block` mode opt-in.

The scanner lives in `packages/helix-common/src/helix_agent/common/threat_patterns.py`.
It runs in two phases (Mini-ADR U-2):

| Phase | Scope | On match | Where |
|-------|-------|----------|-------|
| Layer A: Create / Patch trigger | `strict` | 422 + audit `trigger:prompt_injection_blocked` | `services/control-plane/src/control_plane/api/triggers.py` |
| Layer B: Fire trigger (cron / webhook) | `context` | per `tenant_config.trigger_fire_scan_mode` (default `warn`) | `services/control-plane/src/control_plane/trigger_firing.py` |

Two findings live in different audit columns:

| Column | Where |
|--------|-------|
| `action` | `trigger:prompt_injection_blocked` or `..._warn` |
| `details.findings[i].pattern_id` | Pattern that matched (NOT in the HTTP response — oracle defense) |
| `details.findings[i].excerpt` | ≤ 200-char window around the match |
| `details.field` | Trigger field path (e.g. `config.seed_input`) — Layer A only |
| `details.mode` | `warn` / `block` — Layer B only |

## 1 — Updating the pattern set

The pattern set is a registry inside the threat module. A pattern is:

```python
(regex_str, pattern_id, category, scope)
# scope ∈ {"all", "context", "strict"}; "all" ⊂ "context" ⊂ "strict"
```

**Before opening a PR**:

1. Add the pattern with a clear `pattern_id` (lowercase snake_case).
2. Add ≥ 2 positive cases in `packages/helix-common/tests/test_threat_patterns.py`.
3. Add ≥ 2 negative (false-positive guard) cases pinning examples we
   would NOT want to match.
4. Run the 10-seed false-positive matrix
   (`test_legitimate_prompt_examples_pass_at_all`). If any new pattern
   trips on those legitimate seeds, **narrow the pattern** before
   committing — never widen the seed allowlist.
5. Run `uv run pytest packages/helix-common/tests/test_threat_patterns.py`.

**PR requirements**:

- Label: `security`
- Required reviewer: someone from the SecOps rotation
- Merge SLA: 24 hours (per [memory:zero-tech-debt](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_zero_tech_debt.md) —
  pattern drift windows are an attack surface; don't let PRs rot)

**Post-merge dogfood**:

- Watch `helix:uplift:threat_block_rate:5m` for 24h after deploy.
- If `> 0.5 blocks/sec` for 15min: `HelixUpliftThreatBlockRateSpike`
  fires; treat as "did the new pattern catch real attacks, or did we
  add a noisy pattern" — see § 3.

## 2 — False-positive triage

When a tenant reports "my legitimate prompt got blocked":

1. Pull the audit row:

   ```sh
   curl -s "/v1/audit?action=trigger:prompt_injection_blocked&actor_id=<tenant_actor>" \
     | jq '.entries[].details'
   ```

2. Read `findings[*].pattern_id` and `findings[*].excerpt`.
3. If the matched substring is genuinely legitimate (e.g. a code review
   prompt that contains "pretend you are a reviewer"):
   - Open a `security`-labelled PR that **narrows the pattern** (anchor
     on more attack-specific vocabulary).
   - Add the legitimate excerpt to
     `test_legitimate_prompt_examples_pass_at_all` (in
     `test_threat_patterns.py`) to lock the fix.
   - Backport not required (M0 is one ring) — push to main, follow
     standard rollout.
4. If the matched substring is genuinely suspicious — explain to the
   tenant which input to rewrite. **Never** quote the `pattern_id` to
   the tenant (oracle defense — see § 4).

## 3 — SecOps alert response

### `HelixUpliftThreatBlockRateSpike`

`helix:uplift:threat_block_rate:5m > 0.5` sustained 15 min.

1. Group recent blocks by tenant + actor_id:

   ```sh
   curl -s "/v1/audit?action=trigger:prompt_injection_blocked&from=now-1h" \
     | jq '.entries | group_by(.actor_id) | map({actor: .[0].actor_id, count: length})'
   ```

2. If concentrated on one actor_id: likely a probe / abuse — escalate
   per the abuse-response runbook.
3. If diffuse across many tenants right after a deploy: probably a
   noisy pattern from the latest pattern-set update — `git log` on
   `threat_patterns.py` for the past 24h, identify the new pattern, run
   § 2 triage.

### `HelixUpliftFireTimeWarn`

`helix:uplift:trigger_fire_warn_rate:1h > 0` sustained 15 min.

This is **always** worth a look — fire-time matches mean either:

- (a) A pattern-set update is retroactively matching historically-accepted
  triggers (benign drift), OR
- (b) A trigger row was mutated past the create-time strict scan via
  SQL injection / internal-actor DB write (real attack).

To distinguish:

1. Identify which triggers fired with WARN:

   ```sh
   curl -s "/v1/audit?action=trigger:prompt_injection_warn&from=now-1h" \
     | jq '.entries[] | {trigger_id: .resource_id, findings: .details.findings}'
   ```

2. For each `trigger_id`, fetch the current row + check `updated_at`:
   - If `updated_at` is very recent (< pattern-set deploy time) → DB
     drift suspect; pull `audit_log` history for that resource_id and
     look for unexpected actors.
   - If `updated_at` is old → benign retroactive match; either narrow
     the new pattern (§ 2) or accept the warn.

## 4 — Oracle defense (why response bodies are generic)

The HTTP 422 body intentionally reads "prompt blocked by injection
scanner; see audit log for details" — it never names `pattern_id`,
never echoes the matched substring, never includes
`finding.category`.

Reason: an attacker who can call `POST /v1/triggers` repeatedly can
probe response bodies to learn which pattern fired, then mutate the
prompt to bypass that pattern.

The full finding is available in the audit log to the tenant's admin +
SecOps. That's the right cardinality of disclosure: developers debugging
their own triggers can pull the audit row; attackers can't (the audit
API requires authenticated admin access scoped to the tenant).

**If a developer asks "what pattern fired?"** — point them at
`GET /v1/audit?action=trigger:prompt_injection_blocked&actor_id=me`,
do not paste the `pattern_id` in a chat / ticket.

## 5 — `tenant_config.trigger_fire_scan_mode` opt-in to `block`

Default is `warn` for all tenants — pattern-set updates can't
retroactively block historically-accepted triggers.

High-compliance tenants can opt-in to `block` via:

```sh
curl -X PUT "/v1/tenants/{tid}/config" \
  -d '{"trigger_fire_scan_mode": "block"}'
```

**Before flipping a tenant to `block`**:

- Pull their `trigger:prompt_injection_warn` audit rows from the past
  30 days — anything currently warning will start blocking on next
  fire.
- Confirm with the tenant they have an incident-response process for
  blocked fires (their cron will silently fail with a 503 — no run, no
  data).

**Reverting**:

```sh
curl -X PUT "/v1/tenants/{tid}/config" \
  -d '{"trigger_fire_scan_mode": "warn"}'
```

No backfill — only affects future fires.

## 6 — Field-size cap

Single `str` leaves in `trigger.config` over `MAX_FIELD_BYTES` (10 KB,
in `services/control-plane/src/control_plane/uplift/threat_scan.py`)
are rejected with 422 **before** the scan runs (DoS defense — a
million-char string could exhaust the regex engine on a
catastrophic-backtracking pattern).

If a tenant has a legitimate need for fields larger than 10 KB:

1. Do not raise `MAX_FIELD_BYTES` without first analyzing the regex
   pattern set for catastrophic-backtracking potential (re2-style
   compilation isn't currently in place).
2. Prefer splitting the prompt across multiple smaller fields or using
   a skill (which has its own pipeline + size budget).

## 7 — Pattern-set provenance

Patterns are adapted from `hermes-agent/tools/threat_patterns.py`
(commit hash captured in the module docstring). When a Hermes update
adds new attack signatures, we don't auto-sync; SecOps reviews the
upstream diff and ports relevant patterns through § 1's PR flow.

## 8 — Memory drift response (Capability Uplift Sprint #2)

Memory drift means a `memory_item.content` row was mutated past both
the API + writeback + DLQ strict scans, and the stored `content_hash`
no longer matches `sha256(lower(trim(content)))`. Legitimate writes
always update both atomically via `MemoryStore.write()` /
`update_content()`, so drift detection is a near-certain attack
signal — SQL injection, an internal actor with DB access, or a
restored-from-backup row that wasn't re-hashed.

### `HelixUpliftMemoryDriftDetected` alert (P0)

Fires when `helix:uplift:memory_drift_rate:1h > 0` sustained 15 min.

1. **Pull the audit history for drifted rows** (drift detection itself
   does not emit audit yet — see the M1 follow-up below; rely on the
   redact audit instead):

   ```sh
   curl -s "/v1/audit?action=memory:injection_redacted&from=now-1h" \
     | jq '.entries[] | {memory_id: .resource_id, ts: .occurred_at, details: .details}'
   ```

2. **Identify the affected rows and their owners** — every drift event
   ships with `resource_id` (memory row UUID), and the row is
   tenant-scoped. For each memory_id, pull who created it:

   ```sh
   curl -s "/v1/audit?action=memory:update&resource_id=<id>&from=-90d"
   ```

3. **Decide attack-vs-benign**:
   - If `memory_drift_rate:1h` matches a recent backup restore (check
     ops calendar): benign; trigger a re-hash sweep (see § 8.1) and
     close.
   - Otherwise treat as breach: lock the affected tenant's user API
     keys, capture the current `memory_item.content` for forensics,
     `SELECT * FROM audit_log WHERE resource_id=...` for the
     before/after content trail.
   - Drift on rows whose `created_by` is `agent:writeback` and which
     no admin user has touched is the worst case (suggests a
     write-time bypass, not just post-write drift) — escalate to
     security lead immediately.

4. **Contain**: while investigating, every `recall` of a drifted row
   already returns `[BLOCKED:drift_tampered]` to the agent — no further
   action needed to stop the poisoned content from reaching prompts.
   The user can `DELETE /v1/memory/{id}` once they're satisfied.

### 8.1 Re-hash sweep (for benign drift)

When ops have restored from backup or otherwise re-rolled content
through a path that left `content_hash` stale:

```sh
# Recompute and persist for the affected rows. The script is in
# tools/maintenance/rehash_memory_content.py (M1 follow-up — until
# then, run the SQL by hand against staging and have ops review).
psql -c "UPDATE memory_item
         SET content_hash = encode(digest(lower(trim(content)), 'sha256'), 'hex')
         WHERE id = ANY('{<id1>,<id2>,...}'::uuid[])"
```

### 8.2 `HelixUpliftMemoryRedactSpike` alert (P1)

Fires when `helix:uplift:memory_redact_rate:1h > 1` sustained 30 min.

Two possible causes:

- **Pattern-set update is catching pre-existing memories.** Check
  `git log` on `packages/helix-common/src/helix_agent/common/threat_patterns.py`
  for the past 24 h. If a new pattern landed: pull a sample of the
  matched memories' audit details (`category` field), validate they're
  genuinely suspicious or genuinely false-positives. If false-positive:
  open a `security`-labelled PR to narrow the pattern (per § 2).
- **Write-time strict scan miss.** If no recent pattern update and
  redact rate is elevated: dig into the affected `memory_id`s — the
  content reached the DB despite the write-time scan, which means
  either a bug in the scanner wiring or a code path that bypasses
  `MemoryStore.write()` entirely. File a P1 incident and re-audit
  every write call site against `MemoryStore.write()`.

### 8.3 Per-tenant `memory_recall_redact_mode` (M1 escape hatch)

Sprint #2 does **not** ship a tenant-configurable redaction mode —
every recall match is redacted. A future M1 escape hatch could add
`tenant_config.memory_recall_redact_mode: "redact" | "remove"` if a
high-compliance tenant decides they'd rather the agent never see
that a redacted memory exists. Not in scope today; reach out before
adding it because the trade-off is real (agent loses signal to
re-prompt the user).

### 8.4 M1 follow-up: audit row for the drift detection itself

`MemoryStore.retrieve()` currently sets `MemoryItem.drift=True` but
the audit emit happens at the redact site (recall node), not at
detection time. For M1 we should plumb an `AuditEmitter` Protocol into
the store so drift gets a dedicated `memory:drift_detected` audit row
the moment the hash mismatch is observed — useful for tenants who
only `list_for_user` without going through the recall node.

## 9 — Memory hybrid retrieval troubleshooting (Capability Uplift Sprint #6)

Sprint #6 added hybrid memory recall (vector + Postgres full-text + RRF
k=60). Default mode is `hybrid` for every tenant; per-tenant
`memory_recall_mode` is the opt-out to the pre-Sprint-#6 pure-vector
path. The signal that something has gone wrong is the K.K12 baseline
regressing OR the `helix:uplift:memory_retrieval_hit_ratio:1h` for
`mode="hybrid"` dropping below the `mode="vector"` ratio.

### 9.1 When to suspect hybrid degraded

- K.K12 eval baseline (`tools/eval/test_memory_recall.py`) reports
  `hybrid` recall@5 worse than `vector` recall@5 (test
  `test_hybrid_recall_does_not_regress_against_vector` catches it in CI).
- `helix:uplift:memory_hybrid_adoption_ratio:1h` is 1.0 (every tenant
  on hybrid) but the corresponding hit ratio drops by more than
  a few percent vs the historical vector-only baseline.
- A tenant reports "my agent's memory feels worse than last week" right
  after the Sprint #6 deploy.

### 9.2 Investigate

1. **Is the `content_tsv` column populated?** A tenant whose memory
   was written before the migration landed will have `NULL` for
   `content_tsv`, so the keyword side of the hybrid returns 0 rows
   and RRF degrades to pure vector. Run:

   ```sql
   SELECT count(*) AS rows,
          count(content_tsv) AS with_tsv
   FROM memory_item
   WHERE tenant_id = '<tid>' AND deleted_at IS NULL;
   ```

   If `with_tsv` is much smaller than `rows`, run the lazy backfill:
   any `update_content()` / re-write populates the column. A one-shot
   bulk backfill is on the M1 punch list.

2. **Is jieba tokenizing the query?** For CJK queries: print
   `tokenize_for_search(query)` (the helper in
   `packages/helix-persistence/src/helix_agent/persistence/knowledge/text_search.py`).
   If the tokenizer returns the whole sentence as one token, jieba's
   dictionary is missing or stale.

3. **Is the GIN index being used?** A `psql -c "EXPLAIN ANALYZE
   SELECT ... WHERE content_tsv @@ plainto_tsquery('simple', '...')"`
   should show `Bitmap Index Scan on memory_item_content_tsv_idx`.
   If you see `Seq Scan`, the planner stats need a refresh:

   ```sql
   ANALYZE memory_item;
   ```

4. **Are recall_limit / RRF k correct?** Sprint #6 locks
   `_HYBRID_RECALL_LIMIT=20` and `rrf_fuse k=60` per Mini-ADR U-5
   (matches J.5 knowledge subsystem). If those constants have been
   changed, revert the change before further investigation.

### 9.3 Switch a tenant back to `vector` mode

If a tenant is hurt by hybrid right now and you need an immediate
escape hatch:

```sh
curl -X PUT "/v1/tenants/{tid}/config" \
  -d '{"memory_recall_mode": "vector"}'
```

This takes effect on the next recall. The tenant's existing memory
data is untouched — only the retrieval path changes. Open a ticket
to investigate (the default-hybrid expectation is that almost no
tenants need this).

### 9.4 Re-tune RRF k or recall_limit per-tenant (M1)

Per § 7.2.2 these are out-of-scope for Sprint #6: M0 locks the
J.5-equivalent constants. M1 dogfood data will tell us whether memory
needs different tuning than knowledge — only adjust then, with eval
baseline numbers to back the change.

### 9.5 M1 follow-up: per-language eval baseline

The current eval `tools/eval/datasets/memory_recall/zh_en_seed.yaml`
mixes 4 zh + 4 en cases. M1 should split the baseline so we can detect
"hybrid regressed on Chinese but improved on English" cleanly — today
that signal is washed out by the mean.

## 10 — Memory frozen snapshot troubleshooting (Capability Uplift Sprint #8)

Sprint #8 lands ``LongTermMemorySpec.recall_mode`` (default
``per_session``) plus an Anthropic ``cache_control`` anchor on the
``per_session`` memory block. The expected effect: long sessions stop
paying full input-token price for the memory list on every turn.

### 10.1 Quick health check

After the Sprint #8 deploy, the following metrics should move:

```promql
# Should climb toward 1.0 — every active recall picks per_session
# unless the manifest explicitly opts out.
helix:uplift:memory_per_session_adoption_ratio:1h

# Should be > 0 once any per_session session runs against Anthropic.
helix:uplift:anthropic_cache_anchor_rate:5m

# Pre-Sprint-#8 baseline + the climb after deploy is the headline
# metric (L.L1 already wired it).
helix:llm:anthropic_cache_read_ratio:5m
```

A flat ``anthropic_cache_anchor_rate`` after deploy is the red flag
that something in the metadata propagation broke (see § 10.4).

### 10.2 When to suspect cache miss is back

- ``anthropic_cache_read_ratio:5m`` did NOT climb 24 h after deploy.
- A tenant's per-run input token cost stays flat across many turns
  of one session (no cache hits accumulating).
- ``anthropic_cache_anchor_rate:5m`` is 0 even though
  ``memory_per_session_adoption_ratio:1h`` is > 0.

### 10.3 Switch a tenant back to `per_turn`

The escape hatch for an agent that self-modifies its memory mid-session
and needs the next turn to see the change (M0 has no such tool;
reserved for M1):

```sh
# Currently set via manifest publish; agent_spec
# memory.long_term.recall_mode = "per_turn"
```

After publish the next agent build picks up the legacy tail-injection
behavior. The metric ``memory_per_session_adoption_ratio:1h`` will
fall as the tenant's traffic shifts modes.

### 10.4 Diagnose `anthropic_cache_anchor_rate` = 0

Walk the propagation path top-down:

1. **Did the protocol field land?** Pull the active manifest:

   ```sh
   curl /v1/agents/<name>/versions/<v> | jq '.spec.spec.memory.long_term.recall_mode'
   ```

   Expected: ``"per_session"``. Anything else → tenant opted out.

2. **Did the builder pick the per_session branch?** Grep agent logs
   for the structured line ``memory.recall count=N mode=per_session``
   (`graph_builder/memory.py memory_recall_node`). Missing or
   ``mode=per_turn`` → ``_build_memory_nodes`` lost the manifest field.

3. **Did the anchor flag survive into the message?** Add a one-shot
   log in ``_inject_memories`` to confirm
   ``additional_kwargs["helix_cache_anchor"] = True`` ends up on the
   built block.

4. **Did the Anthropic adapter see the flag?** Add a one-shot log in
   ``_to_anthropic_messages`` for the ``cache_anchor_indices`` it
   returns. ``[]`` → the propagation lost the metadata somewhere
   upstream (the agent_node middleware chain rewrites the message
   list; check for an unwrap that dropped ``additional_kwargs``).

5. **Did the marker land on the wire?** Inspect the outbound body for
   ``cache_control`` count via the
   ``test_cache_anchor_total_markers_within_anthropic_cap`` pattern:

   ```python
   any(b.get("cache_control") for m in body["messages"]
       for b in (m["content"] if isinstance(m["content"], list) else []))
   ```

   If False here, the anchor exists but the adapter dropped it —
   regression in ``_apply_cache_control``.

### 10.5 Cache breakpoint budget

Anthropic caps ``cache_control`` markers at **4 per request**.
Sprint #8 layout: system (1) + tail-2 (2) + memory anchor (≤ 1) ≤ 4.
A future feature that wants its own anchor will need either:

- to share the existing ``helix_cache_anchor`` marker slot (only one
  feature can use it at a time), OR
- to land alongside another tail-count reduction (e.g.
  ``_CACHE_CONTROL_TAIL_COUNT`` from 2 to 1) to free a slot.

Pre-merge any such feature, add a regression test in
``test_llm_provider_anthropic.py`` that exercises the worst-case
message list and asserts ``_count_cache_markers(call) <= 4`` to catch
the regression in CI.

### 10.6 M1 follow-up: per-tenant memory-cache budget tracking

Sprint #8 metrics report aggregate cache anchor application; M1 should
add per-tenant decomposition (``cache_anchor_total{tenant_id}``) so
SecOps can answer "which tenant's memory churn is breaking the cache
the most" (e.g. an agent that re-extracts memory every turn would
defeat the snapshot benefit even with the per_session mode).
