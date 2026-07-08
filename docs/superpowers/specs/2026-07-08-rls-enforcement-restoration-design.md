# RLS Enforcement Restoration — Design Spec

**Status:** Draft (awaiting review)
**Date:** 2026-07-08
**Owner:** platform / data-isolation
**Type:** Enterprise security remediation

---

## 1. Problem

Postgres row-level security (RLS) is **inert at runtime**. Every RLS-protected
tenant table carries a `FORCE ROW LEVEL SECURITY` policy
(`tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid`), and the
per-request middleware dutifully sets the `app.tenant_id` GUC — but the
application connects to Postgres as the **superuser role `expert_work`**
(`rolsuper=t, rolbypassrls=t`), and Postgres unconditionally bypasses RLS for
superusers. The general session wiring (`packages/expert-work-persistence/.../rls.py`,
`_rls_after_begin`) only emits the GUC; it never drops to a non-superuser role.
The `app_user` role that migration `0005_rls_baseline` intended sessions to
`SET ROLE` to **was never created**.

**Proven empirically** (2026-07-08): as `expert_work` with
`app.tenant_id = '<tenant A>'`, a `SELECT` over the FORCE-RLS `tenant_config`
table returns **every** tenant's rows, not just tenant A's.

**Net effect:** tenant isolation currently rests **entirely** on the
application-layer `WHERE tenant_id = :tenant_id` predicates inside the SQL
stores. RLS — the intended defense-in-depth backstop — provides **zero**
protection. A single query that forgets its tenant predicate (human error, a
new endpoint, a raw query, a mis-scoped join) leaks cross-tenant data with no
second line of defense. A coverage audit (2026-07-08) confirmed the codebase
**deliberately** delegates isolation to RLS in a whole class of stores
(`feedback`, `token_usage`, `billing/ledger`, `memory`, `thread_message`, …),
so the exposure is broad; the root cause is singular.

## 2. Product Rationale

Enterprise customers require **provable** tenant data isolation for security
review (SOC 2, ISO 27001) and contractual data-handling guarantees. Today the
honest statement is *"isolation depends on every query being written
correctly."* After this work it becomes *"even a query with a missing tenant
filter cannot return another tenant's rows — the database enforces it."* That
is a materially stronger, auditable control.

## 3. Goal & Success Criteria

**Goal:** Make Postgres RLS a **hard, enforced** tenant-isolation backstop in
**all** environments (dev, CI, staging, prod).

**Success criteria:**

1. Runtime tenant-scoped DB sessions execute as a **non-superuser, non-BYPASSRLS**
   role (`app_user`) — RLS policies apply.
2. A query over a tenant table that omits `app.tenant_id` (no tenant context)
   returns **zero rows** (fail-closed), not cross-tenant rows — and this is
   covered by an automated test.
3. Legitimate cross-tenant paths (system_admin `*` scope, background workers,
   platform reads, audit reads) continue to work, unchanged, via the existing
   bypass mechanism.
4. A regression guard fails CI if any new tenant table (has a `tenant_id`
   column) ships without an RLS policy.
5. Prod cutover is preceded by a **detect phase** that proves no live code path
   would fail-closed, and is **revertible** by reverting a single commit.

**Non-goals (this project):**

- Rewriting the app-layer `WHERE tenant_id` predicates (they stay as
  defense-in-depth; RLS is additive, not a replacement).
- Changing the connection role / DSN (we `SET LOCAL ROLE`, we do not re-auth as
  `app_user`). See §5 for why.
- RLS for non-tenant infrastructure tables (langgraph checkpoints, backup
  records) — they carry no `tenant_id` and are out of the isolation boundary.

## 4. Global Constraints

- App connects as superuser `expert_work`; **do not** change the connection
  role or any DSN in this project.
- `SET LOCAL ROLE` is transaction-scoped (resets on COMMIT/ROLLBACK) → **must**
  remain compatible with pgbouncer transaction pooling. Never use session-level
  `SET ROLE`.
- The change must be **hardwired** — no runtime feature flag that can disable
  enforcement (an off-switch reintroduces exactly the silent-gap failure mode
  being fixed). Cutover safety comes from the detect phase, not a toggle.
- Existing `bypass_rls_var` / `bypass_rls_session()` semantics **must not
  change** — bypass call sites are not touched.
- Migrations must be idempotent, reversible (`downgrade`), non-blocking (no
  long table locks), and zero-downtime.

## 5. Architecture

### 5.1 Approach: per-transaction `SET LOCAL ROLE app_user`

The runtime keeps connecting as `expert_work` (superuser). The RLS session
listener, for **non-bypass** transactions, issues `SET LOCAL ROLE app_user`
immediately after `BEGIN`, before the first statement. `app_user` is
`NOSUPERUSER NOBYPASSRLS`, so RLS policies apply for the remainder of that
transaction. On COMMIT/ROLLBACK the role resets (transaction-scoped), so
pgbouncer-pooled connections carry no residual role.

**Why not connect as `app_user` directly?** Connecting as a restricted role is
marginally stronger (a raw connection cannot bypass) but has a large blast
radius: change every service DSN, split the migration role from the runtime
role, provision pgbouncer auth, and — critically — it **breaks the existing
bypass mechanism**. Today `bypass_rls_session()` merely *skips* emitting the
GUC; under a non-superuser connection, skipping the GUC yields
`app.tenant_id = NULL` → the policy denies **all** rows (fail-closed), which is
the opposite of the intended cross-tenant access. Every bypass call site would
have to be rewritten to actively `SET ROLE` a BYPASSRLS role. The
`SET LOCAL ROLE` approach maps the existing semantics cleanly:

| Scope (from `applied_scope`) | `bypass_rls_var` | `current_tenant_id_var` | Listener action | Effective role | RLS |
|---|---|---|---|---|---|
| `SingleTenant(t)` | `False` | `t` | `SET LOCAL ROLE app_user` + set GUC | `app_user` | **enforced, scoped to `t`** |
| `CrossTenant` / `bypass_rls_session()` | `True` | `None` | skip both | `expert_work` (superuser) | bypassed (intentional) |
| neither (bug) | `False` | `None` | `SET LOCAL ROLE app_user`, no GUC | `app_user` | **fail-closed (zero rows)** |

The third row is the safety property: a session that is neither tenant-scoped
nor explicit-bypass sees nothing, instead of leaking.

### 5.2 The `app_user` role

Created by migration (§6.1):

- `CREATE ROLE app_user NOSUPERUSER NOBYPASSRLS NOLOGIN` — SET-ROLE-only; never
  connects directly (like `audit_reader`/`audit_writer`).
- `GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_user`
- `GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_user`
- `ALTER DEFAULT PRIVILEGES FOR ROLE expert_work IN SCHEMA public GRANT … TO app_user`
  (tables **and** sequences) — so future tables created by later migrations are
  auto-granted; without this, a new table would be invisible to `app_user`
  (fail-closed) after cutover.
- `GRANT audit_reader, audit_writer TO app_user` — membership so the audit /
  billing paths' explicit `SET LOCAL ROLE audit_reader|audit_writer` succeeds
  from an `app_user` session. Role **attributes** (BYPASSRLS) are **not**
  inherited through membership — they apply only when `app_user` explicitly
  `SET ROLE`s to the audit role — so this grants capability, not ambient bypass.

The superuser connection role (`expert_work`) can `SET ROLE app_user` without
membership (superusers bypass membership checks for `SET ROLE`).

### 5.3 Bypass & audit-path compatibility

- **Bypass** (`bypass_rls_session()`, `CrossTenant` scope): already set
  `bypass_rls_var=True`. The listener returns early → no `SET LOCAL ROLE` → the
  session stays superuser → cross-tenant reads work. **Zero call-site changes.**
- **Audit / billing** paths that `SET LOCAL ROLE audit_reader|audit_writer`:
  work from an `app_user` session via the membership grant (§5.2). **Zero
  call-site changes** — but every such path is verified in the detect phase.

## 6. Components

### 6.1 Migration `00XX_app_user_role`
Creates the role, grants, default privileges, and audit-role membership (§5.2).
`upgrade` is idempotent (guard `CREATE ROLE` with a `DO $$ … IF NOT EXISTS`
block). `downgrade` revokes and `DROP ROLE app_user` (safe only when enforcement
is off — documented in the runbook). No table data touched; no locks.

### 6.2 `rls.py` — `_rls_after_begin`
For a non-bypass transaction, emit `SET LOCAL ROLE app_user` **before** the
tenant GUC. The GUC (`set_config('app.tenant_id', …, is_local=true)`) is settable
by `app_user` (custom GUC, no special privilege). Bypass transactions are
unchanged (early return).

### 6.3 Detect instrumentation (Phase 1)
A separate, shippable-first step: in `_rls_after_begin`, when
`not bypass_rls_var.get()` **and** `current_tenant_id_var.get() is None` (a
session that would fail-closed under enforcement), emit a structured
warning + increment a metric (`rls.would_fail_closed`) tagged with endpoint /
trace context where available — **without** the `SET LOCAL ROLE` yet. This runs
in prod during Phase 1 to enumerate every live path that is neither scoped nor
bypassed, so they are fixed before enforcement. In Phase 4 it is retained as a
permanent guard-rail (dev: raise; prod: warn + metric).

### 6.4 RLS-enforcing integration test suite
New tests (testcontainers, connecting as the container superuser but exercising
the real listener with `app_user` created by `alembic upgrade head`):

- **Isolation:** two tenants' rows; a scoped (`app_user`, GUC=tenant A) read
  returns only A.
- **Fail-closed:** a non-bypass session with no GUC returns zero rows.
- **Bypass sees all:** `bypass_rls_session()` returns both tenants' rows.
- **Audit path:** an `audit_reader`/`audit_writer` `SET LOCAL ROLE` from an
  `app_user` session succeeds and reads/writes as intended.
- Representative coverage across table families (agent, knowledge, memory,
  billing, thread), not every table.

### 6.5 RLS-policy guardrail test
A test that introspects `information_schema` / `pg_policy`: every table with a
`tenant_id` column **must** have an RLS policy enabled. Fails CI when a new
tenant table ships without one — prevents silent regression of the isolation
boundary.

### 6.6 Cross-tenant path verification
Using the 2026-07-08 audit's enumerated by-design cross-tenant paths (~20–30
call sites: `*_all_tenants` reads, worker scans, platform reads), confirm each
sets `bypass_rls_var` (directly or via `applied_scope(CrossTenant)` /
`bypass_rls_session()`). Fix any that do not. The detect phase (§6.3) is the
empirical backstop that this manual pass is complete.

### 6.7 Test-harness posture
Regular SQL store tests (`test_sql_*_store.py`) test **app-layer** behavior and
should stay superuser. `create_async_session_factory` returns a plain
sessionmaker (no RLS wrap), and store tests do not wire `build_rls_sessionmaker`,
so the listener is not attached in those processes — they are unaffected. If any
store test process does attach the listener, an autouse `bypass_rls_var=True`
fixture keeps it superuser. RLS enforcement is exercised only by the dedicated
suite (§6.4).

## 7. Phased Rollout

| Phase | Change | Behavior | Exit criteria |
|---|---|---|---|
| **0 — Prepare** | Role migration; RLS integration + guardrail tests; cross-tenant path verification | None (role created, unused) | Migration applies + reverts cleanly; new tests green; audit paths confirmed bypassed |
| **1 — Detect** | Detect instrumentation (no `SET LOCAL ROLE`) | Observability only | `rls.would_fail_closed` metric at **zero** across a defined observation window in prod; every offender fixed |
| **2 — Enforce (non-prod)** | `SET LOCAL ROLE app_user` enabled | RLS enforced in dev + CI | Full suite green incl. RLS integration tests |
| **3 — Prod cutover** | Deploy enforcement to prod | RLS enforced in prod | RLS-denial / error-rate / zero-row metrics nominal post-deploy |
| **4 — Harden** | Detect → permanent guard-rail; docs | RLS enforced + guarded | Threat model + runbook merged; guard-rail active |

Rollback at any phase: revert the enabling commit (single deploy). Migration
`downgrade` (drop role) only after enforcement is reverted.

## 8. Threat Model & Guarantee

**Threat:** cross-tenant data disclosure via a DB query that omits its
`tenant_id` predicate (human error, new/edited code, raw SQL, mis-scoped join).

**Control (post-project):** RLS policy `tenant_id = current_setting('app.tenant_id')`
is **enforced** because runtime tenant sessions run as `app_user`
(NOSUPERUSER, NOBYPASSRLS). A missing app-layer predicate is contained by the
database.

**Residual risks (documented, not eliminated):**

1. **Bypass paths** — system_admin `*` scope, workers, platform reads run
   superuser. Gated at the app layer (`ensure_tenant_scope`, system_admin
   checks — hardened in #954/#956) and audited. RLS does not cover these by
   design.
2. **Superuser side-channels** — migration runner, langgraph checkpointer,
   sandbox DSNs connect superuser. Not tenant-request paths; their tables carry
   no `tenant_id`. Confirmed in Phase 0.
3. **New tenant table without a policy** — caught by the guardrail test (§6.5).
4. **GUC manipulation** — a query that sets `app.tenant_id` to a foreign tenant
   would scope to that tenant. Only reachable from server code that already
   holds the tenant context; not attacker-controlled. Out of scope.

## 9. Enterprise Operational Concerns

- **pgbouncer:** `SET LOCAL ROLE` is transaction-scoped; the default
  `server_reset_query` (`DISCARD ALL`) between transactions is compatible.
  Verify the deployed pgbouncer config in Phase 0.
- **Bypass DSNs:** confirm checkpointer / sandbox tables carry no `tenant_id`
  (Phase 0); if any tenant data flows there, handle separately (out of the
  current scope, flagged).
- **Observability:** `rls.would_fail_closed` (Phase 1 → permanent),
  RLS-denial / zero-row anomaly signals during cutover.
- **Rollback runbook:** per-phase revert steps; the migration downgrade
  ordering constraint (drop role only after enforcement off).
- **Auditability:** the role model, policies, and guarantee statement (§8) are
  documented for security review.

## 10. Open Questions (resolve during planning / Phase 0)

- Prod detect **observation-window** length (e.g. 7 days of representative
  traffic incl. month-end billing rollup, which exercises cross-tenant worker
  paths).
- Exact metric/alerting backend wiring for `rls.would_fail_closed`
  (existing observability stack — Langfuse/Grafana/Tempo per the platform).
- Whether `ALTER DEFAULT PRIVILEGES` must also target roles other than
  `expert_work` if any migration path creates tables as a different role.

## 11. Deliverables

Spec (this doc) → implementation plan (`writing-plans`) → migration + `rls.py`
change + detect instrumentation + RLS integration suite + guardrail test +
cross-tenant path fixes → threat-model doc + rollback runbook → executed via
subagent-driven-development with per-task review.
