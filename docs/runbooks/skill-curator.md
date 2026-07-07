# Skill Curator — Capability Uplift Sprint #4

> Pairs with `docs/streams/STREAM-UPLIFT-DESIGN.md` § 5.
>
> Authoritative source for: Curator state machine (active / stale /
> archived / pinned), per-tenant threshold tuning, auto-revive
> behaviour, archived-skill read response, and the two Curator alerts
> (`ExpertWorkUpliftCuratorStaleSurge`, `ExpertWorkUpliftSkillArchivedAccessAttempts`).

The Curator lives across these components:

| Component | Path |
|-----------|------|
| State columns (`pinned`, `last_used_at`, `state_changed_at`) | `packages/expert-work-persistence/src/expert_work/persistence/models/skill.py` |
| Threshold columns (`skill_stale_days`, `skill_archive_days`) | `packages/expert-work-persistence/src/expert_work/persistence/models/tenant_config.py` |
| Worker (daily sweep) | `services/control-plane/src/control_plane/skill_curator.py` |
| Throttled activity recorder | `services/control-plane/src/control_plane/skill_activity.py` |
| Admin PATCH for pin / unpin | `services/control-plane/src/control_plane/api/skills.py` (`PATCH /v1/skills/{id}`) |
| Metrics / alerts | `tools/observability/rules/uplift.yml` (`expert_work_uplift_skill_curator` group) |

Five new audit actions track every operator-visible state change:

| Action | Fires when |
|--------|------------|
| `SKILL_CURATOR_RUN` | Once per Curator sweep per platform (summary row carries per-tenant counts) |
| `SKILL_AUTO_REVIVED` | A `stale` skill was bumped back to `active` by activity |
| `SKILL_VIEW_BLOCKED_ARCHIVED` | An agent's `skill_view` hit an archived skill (cold path) |
| `SKILL_PINNED` | Admin pinned a skill |
| `SKILL_UNPINNED` | Admin unpinned a skill |

---

## Section 1 — Threshold tuning

The defaults `skill_stale_days=30` / `skill_archive_days=90` come from
external skill-marketplace observations; they assume an agent that
binds a skill at least once per month. M1-K J.7b-1 (agent-self-
authored skills) is when the real distribution lands; **plan to revisit
2–4 weeks after J.7b-1 ships**.

To re-tune for a single tenant:

```bash
curl -X PUT https://Expert Work/v1/tenants/<TID>/config \
    -H "Authorization: Bearer <admin-jwt>" \
    -H "Content-Type: application/json" \
    -d '{"skill_stale_days": 7, "skill_archive_days": 30}'
```

Both fields are `optional`. The cross-field invariant
`skill_archive_days > skill_stale_days` is enforced **client-side**
(Pydantic `@model_validator`) and **server-side** (DB CHECK
`tenant_config_skill_archive_gt_stale_ck`). A patch that violates it
returns 422 with the literal message
`"skill_archive_days must be strictly greater than skill_stale_days"`.

When to tighten vs. relax:

| Symptom | Action |
|---------|--------|
| Operators complaining "useful skill keeps going stale" | Loosen `skill_stale_days` (e.g. 30 → 60) OR `pin` the skill |
| Skill library bloating with abandoned agent-self-authored skills | Tighten `skill_stale_days` (e.g. 30 → 7) — speeds up triage |
| Archived skills coming back through manual unarchive frequently | Loosen `skill_archive_days` — your archive threshold is too aggressive |

---

## Section 2 — Pin / unpin flow

Pin = "do not Curator-touch — forever, until I un-pin". Use cases:

- Foundation skill bound to every agent
- Skill that's bound but `skill_view`'d rarely (activity tracking
  underestimates its real value)
- Skill that's in a quiet seasonal window (e.g. only used during
  end-of-year tax processing)

```bash
# Pin
curl -X PATCH https://Expert Work/v1/skills/<SID> \
    -H "Authorization: Bearer <admin-jwt>" \
    -d '{"pinned": true}'

# Unpin
curl -X PATCH https://Expert Work/v1/skills/<SID> \
    -d '{"pinned": false}'
```

**High-risk pin restriction** (Mini-ADR U-30 defense): pinning a skill
whose latest version carries `high_risk = true` (`exec_python` /
`http` / `exec_shell` tool OR `scripts/*` supporting file) requires
the caller to be `Role.ADMIN` or `Role.SYSTEM_ADMIN`. A non-admin pin
attempt on a high-risk skill returns 403.

Rationale: combined with M1-K J.7b-1, an agent could create a
high-risk skill + auto-pin it → permanent RCE foothold. The role gate
forces operator review.

---

## Section 3 — `ExpertWorkUpliftCuratorStaleSurge` triage

**Alert fires when**: > 50 `active → stale` transitions in 24h
(sustained). Background is a slow trickle (low single-digits per
tenant per week in normal operation).

**Likely causes** (most → least likely):

1. **Activity tracking broke** — the orchestrator's `_load_skills`
   or `skill_view` stopped calling `ThrottledActivityRecorder.record`.
   Diagnose: query the audit log for `SKILL_AUTO_REVIVED` rate over
   the same window — if it's also zero, activity tracking is dead.
   Fix: check `app.state.skill_activity_recorder` is wired into
   `make_agent_builder` (M1-K backlog).

2. **Tenant set an aggressive `skill_stale_days`** — e.g. 1 day. Check
   `tenant_config` per tenant; if a single tenant dominates the
   transition count, talk to them.

3. **Real change** — a deprecated agent went offline so its skills
   genuinely stopped being used. Verify by sampling 3–5 of the
   transitioned skills + asking the operator. If real → no action;
   the Curator did its job.

To inspect last 24h transitions:

```sql
-- Per-tenant active → stale count (joins SKILL_CURATOR_RUN summary
-- to the per-tenant breakdown; the per_tenant list is JSONB).
SELECT
  jsonb_array_elements(details->'per_tenant') AS per_tenant,
  ts
FROM audit_log
WHERE action = 'skill:curator_run'
  AND ts > now() - interval '24 hours'
ORDER BY ts DESC;
```

To roll back a Curator sweep (rare — only when activity tracking was
broken AND the sweep transitioned legitimately-active skills):

```sql
-- Restore status from state_changed_at lookup. The Curator never
-- transitions twice in one day so the state_changed_at row contains
-- the moment of transition.
UPDATE skill
SET status = 'active', state_changed_at = now()
WHERE status = 'stale'
  AND state_changed_at > now() - interval '24 hours'
  AND tenant_id = '<TID>';
```

---

## Section 4 — `ExpertWorkUpliftSkillArchivedAccessAttempts` triage

**Alert fires when**: an agent's `skill_view` hit an archived skill
> 6 times / hour sustained 30 minutes. Cold path: expected to be
near-zero in steady state.

**What it means**: an active agent's manifest references a skill that
the Curator auto-archived. The skill_view returns
`[BLOCKED: skill X is archived — contact a tenant admin to unarchive]`
and the agent's run proceeds without that skill's content (the agent
just sees the blocked message; no hard failure).

**Triage**:

1. Pull the audit log for `SKILL_VIEW_BLOCKED_ARCHIVED` over the alert
   window — extract the unique `(tenant_id, skill_name)` pairs:
   ```sql
   SELECT DISTINCT details->>'skill_name' AS skill_name, tenant_id
   FROM audit_log
   WHERE action = 'skill:view_blocked_archived'
     AND ts > now() - interval '1 hour';
   ```
   (`SKILL_VIEW_BLOCKED_ARCHIVED` is logged by the orchestrator caller;
   if you don't see rows, check the orchestrator's
   `tool_call_audit_envelope` wiring.)

2. For each `(tenant, skill_name)` pair:
   - If the agent is still actively used → **unarchive** by setting
     status back to `active`:
     ```bash
     curl -X PATCH https://Expert Work/v1/skills/<SID> \
         -d '{"status": "active"}'
     ```
     This bumps `state_changed_at` and the Curator restarts the
     stale-day countdown.
   - If the agent should not use the skill anymore → update the
     manifest (`agents:create` or `agents:put`) and drop the skill
     reference.

---

## Section 5 — Auto-revive (`stale → active`)

Stale skills auto-revive to `active` the moment they're bound (via
`_load_skills`) or read (via `skill_view`). The transition is atomic
inside `SkillStore.bump_last_used_at`; the `ThrottledActivityRecorder`
emits `record_curator_transition(from_state="stale", to_state="active")`
on every auto-revive so dashboards split the counter cleanly.

Asymmetric design (per Mini-ADR U-29):

- `stale → active` auto-revives: stale is "asleep, wake on touch"
- `archived → active` requires manual admin: archive is "cold storage,
  needs operator decision"

This asymmetry defends against M1-K J.7b-1 agent-self-authored skills
silently re-activating their own archived skills.

---

## Section 6 — Worker schedule + cadence

- Default cadence: **daily at the platform-replica-local time set by
  `settings.skill_curator_interval_s`** (default 86400 = 24h since
  start). First sweep happens `interval_s` after process start (not
  immediately on boot) to avoid hammering the DB during restart
  storms.
- Single replica (same rationale as `TriggerScheduler`): the sweep is
  idempotent so multiple replicas wouldn't cause data corruption, but
  they'd produce duplicate audit rows + redundant SQL UPDATEs.
- The platform doesn't pin a specific replica; whichever one wins the
  startup race is the Curator host until it restarts. M2-A may add
  leader election if we observe pathological flap.

Manual sweep (operator-triggered, rare — only for one-off load
tests):

```python
# In a python -i shell with the control-plane app loaded:
await app.state.skill_curator.run_once()
```

No HTTP endpoint exposes this — manual operator runs go through the
shell so they're never accidentally hit.

---

## Section 7 — Sprint Exit verification (the 6+ list)

Per `[memory:zero-tech-debt]`, Sprint #4 close-out checks:

- [ ] migration 0043 (SkillRow) + 0044 (tenant_config) backfill smoke
- [ ] `_skill_dict` exposes `pinned` / `last_used_at` / `state_changed_at`
- [ ] PATCH skill carries either `status` or `pinned` (empty body 422)
- [ ] Cross-field invariant `skill_archive_days > skill_stale_days`
      rejected client + server side
- [ ] Curator 4 state-machine paths covered by tests
- [ ] Auto-revive flips `stale → active` on bump
- [ ] Archived `skill_view` returns BLOCKED + audit + does NOT bump
      activity
- [ ] All 5 new audit actions written into protocol's `AuditAction`
      StrEnum
- [ ] `expert_work_uplift_curator_transition_total{from_state, to_state}`
      counter + `expert_work_uplift_skill_view_archived_blocked_total`
      counter wired
- [ ] 4 recording rules + 2 alerts in `tools/observability/rules/uplift.yml`
- [ ] Admin UI: 📌 pin button + status filter + ETA hint
- [ ] CI green; `[memory:ruff-strict-lint-traps]` +
      `[memory:codeql-unused-global]` preflight
