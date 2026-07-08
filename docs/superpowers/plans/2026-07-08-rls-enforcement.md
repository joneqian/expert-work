# RLS Enforcement Restoration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Postgres RLS a hard, enforced tenant-isolation backstop in all environments — runtime tenant sessions run as a non-superuser `app_user` role via per-transaction `SET LOCAL ROLE`, so a query missing its `WHERE tenant_id` cannot leak cross-tenant rows.

**Architecture:** The app keeps connecting as superuser `expert_work`. The RLS session listener (`_rls_after_begin`) issues `SET LOCAL ROLE app_user` for non-bypass transactions (before emitting the `app.tenant_id` GUC); bypass transactions stay superuser. `app_user` is `NOSUPERUSER NOBYPASSRLS`, so RLS applies. Existing `bypass_rls_var` semantics and audit-role paths are preserved via role membership — zero bypass call-site changes.

**Tech Stack:** Python 3.13, SQLAlchemy async, Alembic, Postgres (FORCE RLS), pgbouncer (transaction pooling), pytest + testcontainers.

**Reference spec:** `docs/superpowers/specs/2026-07-08-rls-enforcement-restoration-design.md`

## Global Constraints

- App connects as superuser `expert_work`; **do not** change the connection role or any DSN.
- Use `SET LOCAL ROLE` only (transaction-scoped) — never session-level `SET ROLE` (pgbouncer transaction pooling safety).
- **No runtime feature flag** for enforcement — it is hardwired. Cutover safety comes from the Detect phase, not a toggle.
- Do **not** change `bypass_rls_var` / `bypass_rls_session()` semantics or any bypass call site.
- Migrations: idempotent, reversible (`downgrade`), non-blocking (no table locks), zero-downtime.
- `packages/expert-work-persistence` does **not** depend on `expert-work-common` — the Detect signal in `rls.py` uses stdlib `logging` (a structured warning), not the common metrics API.
- New migration revision: `0121_app_user_role`, `down_revision = "0120_supporting_files_cap"`.

## File Structure

- Create `packages/expert-work-persistence/migrations/versions/0121_app_user_role.py` — the role + grants (unused until enforcement).
- Create `packages/expert-work-persistence/tests/test_sql_app_user_role.py` — role attributes/grants integration test.
- Create `packages/expert-work-persistence/tests/test_rls_policy_coverage.py` — guardrail: every `tenant_id` table has an RLS policy.
- Create `packages/expert-work-persistence/tests/test_rls_enforcement.py` — the RLS-enforcing integration suite.
- Modify `packages/expert-work-persistence/src/expert_work/persistence/rls.py` — Detect (Task 3) then Enforce (Task 6) then Harden (Task 7).
- Create `docs/security/rls-tenant-isolation.md` — threat model + rollback runbook (Task 8).

## Delivery Grouping (PRs & human gates)

- **PR A = Tasks 1–3** — role migration + guardrail test + Detect instrumentation. No enforcement; safe to deploy.
- **⛔ HUMAN GATE 1** — deploy PR A; run Detect in prod for the observation window; drive `rls.would_fail_closed` to zero by completing Task 4.
- **PR B = Tasks 5–6 (+ Task 4 fixes)** — RLS enforcement + integration suite. Merging enables enforcement in dev/CI.
- **⛔ HUMAN GATE 2** — deploy PR B to prod (Phase 3 cutover); monitor.
- **PR C = Tasks 7–8** — harden Detect to a permanent guard-rail; threat-model doc + runbook.

---

## Task 1: `app_user` role migration

**Files:**
- Create: `packages/expert-work-persistence/migrations/versions/0121_app_user_role.py`
- Test: `packages/expert-work-persistence/tests/test_sql_app_user_role.py`

**Interfaces:**
- Produces: a Postgres role `app_user` (NOLOGIN, NOSUPERUSER, NOBYPASSRLS) with DML on all `public` tables, USAGE/SELECT on sequences, matching default privileges, and membership in `audit_reader`/`audit_writer`. Consumed by Task 6 (`SET LOCAL ROLE app_user`) and the Task 5 suite.

- [ ] **Step 1: Write the failing test**

Mirror the testcontainers fixture from `tests/test_sql_tenant_member_store.py` (the `sql_store` fixture: alembic `command.upgrade(cfg, "head")` + `_sync_dsn`/`_async_dsn`). This test only needs the sync engine to introspect roles.

```python
# tests/test_sql_app_user_role.py
"""Integration: the ``app_user`` role exists with the right attributes/grants."""
from __future__ import annotations
from pathlib import Path
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from testcontainers.postgres import PostgresContainer

pytestmark = pytest.mark.integration
ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

def _sync_dsn(c: PostgresContainer) -> str:
    url = str(c.get_connection_url())
    return url.replace("+psycopg2", "+psycopg").replace("postgresql://", "postgresql+psycopg://", 1)

def test_app_user_role_provisioned(postgres_container: PostgresContainer) -> None:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")
    engine = create_engine(_sync_dsn(postgres_container), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            attrs = conn.execute(text(
                "SELECT rolsuper, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname='app_user'"
            )).one_or_none()
            assert attrs is not None, "app_user role missing"
            assert attrs == (False, False, False)  # NOSUPERUSER, NOBYPASSRLS, NOLOGIN
            # DML granted on a representative tenant table.
            has_select = conn.execute(text(
                "SELECT has_table_privilege('app_user', 'tenant_config', 'SELECT')"
            )).scalar()
            assert has_select is True
            # Member of audit_reader (so audit paths' SET ROLE works from app_user).
            is_member = conn.execute(text(
                "SELECT pg_has_role('app_user', 'audit_reader', 'MEMBER')"
            )).scalar()
            assert is_member is True
    finally:
        engine.dispose()
```

- [ ] **Step 2: Run it — expect FAIL** (role does not exist yet)

Run: `cd packages/expert-work-persistence && uv run pytest tests/test_sql_app_user_role.py -q`
Expected: FAIL — `app_user role missing`.

- [ ] **Step 3: Write the migration**

```python
# migrations/versions/0121_app_user_role.py
"""Create the ``app_user`` role — RLS enforcement (Phase 0).

Non-superuser, non-BYPASSRLS role that runtime tenant sessions ``SET LOCAL ROLE``
to, so the FORCE-RLS policies actually apply (the app connects as superuser
``expert_work``, which bypasses RLS). Created but UNUSED here — enforcement is
enabled later in ``rls.py``. Idempotent, reversible, no table locks.
"""
from __future__ import annotations
from collections.abc import Sequence
from alembic import op

revision: str = "0121_app_user_role"
down_revision: str | Sequence[str] | None = "0120_supporting_files_cap"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user') THEN
                CREATE ROLE app_user NOLOGIN NOSUPERUSER NOBYPASSRLS;
            END IF;
        END
        $$;
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO app_user;")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_user;")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_user;")
    # Future tables/sequences created by the migration role auto-grant to app_user,
    # else a new table is invisible to app_user (fail-closed) after cutover.
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_user;"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO app_user;"
    )
    # Membership → audit/billing paths' SET LOCAL ROLE audit_reader|writer works
    # from an app_user session. BYPASSRLS is NOT inherited ambiently (only on SET ROLE).
    op.execute("GRANT audit_reader, audit_writer TO app_user;")

def downgrade() -> None:
    # Revoke default privileges + grants BEFORE dropping the role (DROP ROLE fails
    # while dependent privileges exist).
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM app_user;"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE USAGE, SELECT ON SEQUENCES FROM app_user;"
    )
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM app_user;")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM app_user;")
    op.execute("REVOKE USAGE ON SCHEMA public FROM app_user;")
    op.execute("REVOKE audit_reader, audit_writer FROM app_user;")
    op.execute("DROP ROLE IF EXISTS app_user;")
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `cd packages/expert-work-persistence && uv run pytest tests/test_sql_app_user_role.py -q`
Expected: PASS.

- [ ] **Step 5: Verify downgrade is clean**

Add a second test `test_app_user_role_downgrade_drops_role` that runs `command.upgrade(cfg, "head")`, then `command.downgrade(cfg, "0120_supporting_files_cap")`, then asserts `SELECT 1 FROM pg_roles WHERE rolname='app_user'` returns nothing. Run it; expect PASS.

- [ ] **Step 6: mypy + ruff + commit**

Run: `uv run mypy migrations/versions/0121_app_user_role.py tests/test_sql_app_user_role.py` and `uv run ruff check ...` and `uv run ruff format --check ...` (from the package dir). All clean.
```bash
git add packages/expert-work-persistence/migrations/versions/0121_app_user_role.py packages/expert-work-persistence/tests/test_sql_app_user_role.py
git commit -m "feat(persistence): app_user role migration (RLS enforcement Phase 0)"
```

---

## Task 2: RLS-policy coverage guardrail test

**Files:**
- Test: `packages/expert-work-persistence/tests/test_rls_policy_coverage.py`

**Interfaces:**
- Consumes: the schema after `alembic upgrade head`. Produces nothing; it is a permanent CI guard that fails if a `tenant_id` table lacks an RLS policy.

- [ ] **Step 1: Write the test**

```python
# tests/test_rls_policy_coverage.py
"""Guard-rail: every table with a ``tenant_id`` column must have an RLS policy.

RLS is the enforced tenant-isolation backstop (see the RLS enforcement spec). A
new tenant table shipped without a policy would silently rejoin the leak surface;
this test fails CI when that happens.
"""
from __future__ import annotations
from pathlib import Path
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from testcontainers.postgres import PostgresContainer

pytestmark = pytest.mark.integration
ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

def _sync_dsn(c: PostgresContainer) -> str:
    url = str(c.get_connection_url())
    return url.replace("+psycopg2", "+psycopg").replace("postgresql://", "postgresql+psycopg://", 1)

# Tables with a ``tenant_id`` column that are intentionally NOT per-tenant
# RLS-scoped (platform-global / NULL-tenant). Keep this list tiny and justified.
_ALLOWED_WITHOUT_POLICY: frozenset[str] = frozenset()

def test_every_tenant_table_has_rls_policy(postgres_container: PostgresContainer) -> None:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")
    engine = create_engine(_sync_dsn(postgres_container), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(
                """
                SELECT c.relname
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_attribute a
                  ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped
                WHERE n.nspname = 'public' AND c.relkind = 'r'
                  AND NOT EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid)
                ORDER BY c.relname
                """
            )).scalars().all()
        missing = sorted(set(rows) - _ALLOWED_WITHOUT_POLICY)
        assert not missing, f"tenant_id tables without an RLS policy: {missing}"
    finally:
        engine.dispose()
```

- [ ] **Step 2: Run it**

Run: `cd packages/expert-work-persistence && uv run pytest tests/test_rls_policy_coverage.py -q`
Expected: PASS (all tenant tables already carry policies from the RLS migrations). If it FAILS, the listed tables are a real pre-existing gap — STOP and report them to the human before proceeding (they need a policy migration, out of this task's scope).

- [ ] **Step 3: ruff + commit**
```bash
git add packages/expert-work-persistence/tests/test_rls_policy_coverage.py
git commit -m "test(persistence): guard-rail — every tenant_id table has an RLS policy"
```

---

## Task 3: Detect instrumentation in `rls.py`

**Files:**
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/rls.py`
- Test: `packages/expert-work-persistence/tests/test_rls_detect.py`

**Interfaces:**
- Consumes: `bypass_rls_var`, `current_tenant_id_var` (existing). Produces: a structured `WARNING` log `rls.would_fail_closed` when a non-bypass session has no tenant context — the Phase-1 signal. No behavior change (no `SET LOCAL ROLE` yet).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_rls_detect.py
"""Detect: a non-bypass session with no tenant context is logged (Phase 1)."""
from __future__ import annotations
import logging
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from expert_work.persistence.rls import (
    build_rls_sessionmaker, bypass_rls_var, current_tenant_id_var,
)

@pytest.mark.asyncio
async def test_detects_would_fail_closed(caplog: pytest.LogCaptureFixture) -> None:
    engine = create_async_engine("sqlite+aiosqlite://")  # in-memory; listener runs on begin
    sf = build_rls_sessionmaker(async_sessionmaker(engine))
    tok_b = bypass_rls_var.set(False)
    tok_t = current_tenant_id_var.set(None)
    try:
        with caplog.at_level(logging.WARNING, logger="expert_work.persistence.rls"):
            async with sf() as s:
                await s.execute(text("SELECT 1"))
        assert any("rls.would_fail_closed" in r.message for r in caplog.records)
    finally:
        bypass_rls_var.reset(tok_b)
        current_tenant_id_var.reset(tok_t)
        await engine.dispose()
```
(If `aiosqlite` is not a dev dependency, mirror `test_rls_detect` against the testcontainers Postgres fixture instead and mark `pytestmark = pytest.mark.integration` — check `pyproject.toml` first.)

- [ ] **Step 2: Run it — expect FAIL** (no warning emitted yet)

Run: `cd packages/expert-work-persistence && uv run pytest tests/test_rls_detect.py -q`
Expected: FAIL.

- [ ] **Step 3: Add the Detect logging to `rls.py`**

Add a module logger near the top of `rls.py` (after the imports):
```python
import logging
logger = logging.getLogger("expert_work.persistence.rls")
```
In `_rls_after_begin`, after the `if bypass_rls_var.get(): return` guard:
```python
    tenant_id = current_tenant_id_var.get()
    if tenant_id is None:
        # Non-bypass session with no tenant context: under RLS enforcement this
        # fail-closes (zero rows). Phase-1 Detect signal — surfaces every path
        # that is neither tenant-scoped nor explicit-bypass so it can be fixed
        # BEFORE enforcement lands. No behavior change here.
        logger.warning("rls.would_fail_closed")
    if tenant_id is not None:
        _emit_set_config(connection, RLS_GUC_NAME, str(tenant_id))
    # (existing app.user_id emission unchanged, below)
```
Note: this replaces the current `tenant_id = current_tenant_id_var.get()` line — do not double-fetch.

- [ ] **Step 4: Run the test — expect PASS**

Run: `cd packages/expert-work-persistence && uv run pytest tests/test_rls_detect.py -q`
Expected: PASS.

- [ ] **Step 5: Full listener regression + mypy/ruff + commit**

Run the existing RLS + store fast tests to confirm no regression: `uv run pytest tests/test_rls_detect.py tests/test_in_memory_tenant_member_store.py -q`. mypy + ruff on `rls.py` + the new test.
```bash
git add packages/expert-work-persistence/src/expert_work/persistence/rls.py packages/expert-work-persistence/tests/test_rls_detect.py
git commit -m "feat(persistence): Detect non-bypass sessions with no tenant context (RLS Phase 1)"
```

> **PR A ends here (Tasks 1–3).** Open the PR; after CI green, this is deployable with no behavior change.

---

## ⛔ HUMAN GATE 1 — Detect phase (operational, not an agent task)

Deploy PR A. Run for the observation window (≥ one representative cycle incl. month-end billing rollup). Aggregate `rls.would_fail_closed` logs by endpoint/trace. For each offender, complete Task 4. Do **not** start Task 6 until the signal is at zero.

---

## Task 4: Cross-tenant path verification + fixes

**Files:** varies (the offending store/API call sites surfaced by Detect + the 2026-07-08 audit's by-design cross-tenant list).

**Method (not pre-codeable — investigation-driven):**

- [ ] **Step 1:** Enumerate candidates: the audit's by-design cross-tenant flags (`*_all_tenants` reads, worker scans, platform reads) **plus** every distinct source location in the Phase-1 `rls.would_fail_closed` logs.
- [ ] **Step 2:** For each, confirm it runs under bypass: either `applied_scope(CrossTenant)` (system_admin `*`), an explicit `bypass_rls_session()` wrap, or a worker that should wrap one. Reference `services/control-plane/src/control_plane/tenant_scope.py` (`bypass_rls_session`, `applied_scope`).
- [ ] **Step 3:** For any that do **not** bypass and legitimately need cross-tenant access, wrap them in `bypass_rls_session()` (or ensure the worker sets it). Each fix is small; give each its own commit + a focused test that the path returns rows under enforcement (add to Task 5's suite).
- [ ] **Step 4:** Re-run Detect until the signal is zero. Acceptance: `rls.would_fail_closed` at zero across the observation window; every candidate confirmed bypassed or fixed.

If Detect surfaces a path that should be **tenant-scoped** (not bypass) but wasn't setting the tenant context, that is a latent bug — fix it to set the scope, and note it for the human.

---

## Task 5: RLS-enforcing integration test suite

**Files:**
- Test: `packages/expert-work-persistence/tests/test_rls_enforcement.py`

**Interfaces:**
- Consumes: the `app_user` role (Task 1) + the (not-yet-added) `SET LOCAL ROLE` from Task 6. Written to FAIL now and PASS after Task 6.

- [ ] **Step 1: Write the enforcement tests (expected RED)**

Connect as the testcontainers superuser (like the store tests) and use `build_rls_sessionmaker(create_async_session_factory(engine))` so the real listener runs. Set `current_tenant_id_var` to scope; use `bypass_rls_session`-equivalent (`bypass_rls_var=True`) for the bypass case. Reference `tests/test_tenant_member_rls_integration.py` for fixture shape (but that file connects AS a login app-role; here we validate the runtime `SET LOCAL ROLE`-from-superuser path instead).

Cover, on a representative table (e.g. `tenant_config` or `token_usage`):
- **isolation:** seed tenant A + B; with `current_tenant_id_var = A` (non-bypass), a read returns only A's rows.
- **fail-closed:** with `current_tenant_id_var = None` (non-bypass), a read returns zero rows.
- **bypass sees all:** with `bypass_rls_var = True`, a read returns A's and B's rows.
- **audit path:** a store method that does `SET LOCAL ROLE audit_reader` succeeds from a non-bypass (app_user) session and reads cross-tenant (membership works).

(Full fixture + assertions to be written to mirror the existing integration tests; each of the four is one `@pytest.mark.asyncio` test.)

- [ ] **Step 2: Run — expect FAIL**

Run: `cd packages/expert-work-persistence && uv run pytest tests/test_rls_enforcement.py -q`
Expected: FAIL — without `SET LOCAL ROLE`, the session is superuser → isolation + fail-closed assertions fail (sees all rows).

- [ ] **Step 3: Commit the RED tests** (they gate Task 6)
```bash
git add packages/expert-work-persistence/tests/test_rls_enforcement.py
git commit -m "test(persistence): RLS enforcement suite (RED until SET LOCAL ROLE lands)"
```

---

## Task 6: Enable `SET LOCAL ROLE app_user`

**Files:**
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/rls.py`

**Interfaces:**
- Consumes: Task 1's `app_user` role. Produces: enforced RLS for non-bypass transactions.

- [ ] **Step 1: Add the `SET LOCAL ROLE` to `_rls_after_begin`**

Immediately after the `if bypass_rls_var.get(): return` guard, before the tenant GUC / Detect block:
```python
    # Drop to the non-superuser, non-BYPASSRLS role so the FORCE-RLS policies
    # apply for this transaction. Transaction-scoped (resets on COMMIT/ROLLBACK)
    # → pgbouncer-safe. Bypass transactions returned above and stay superuser.
    connection.execute(text("SET LOCAL ROLE app_user"))
```
Keep the Task-3 Detect block (a non-bypass session with no GUC now genuinely fail-closes; the warning still fires and is now a real guard-rail signal).

- [ ] **Step 2: Run the enforcement suite — expect PASS**

Run: `cd packages/expert-work-persistence && uv run pytest tests/test_rls_enforcement.py -q`
Expected: PASS (isolation + fail-closed + bypass + audit all hold).

- [ ] **Step 3: Run the full persistence + control-plane suites — check fallout**

Run the fast suites: `uv run pytest packages/expert-work-persistence/tests -q -m "not integration"` and the integration store tests. If any store test that connects superuser now fails-closed (because the listener SET-ROLEs and it has no GUC), add an **autouse** `bypass_rls_var=True` fixture to that test module (or a shared conftest) so app-layer store tests stay superuser — the enforcement is validated by Task 5, not by the app-layer suites. Do NOT weaken Task 5.

- [ ] **Step 4: mypy + ruff + commit**
```bash
git add packages/expert-work-persistence/src/expert_work/persistence/rls.py
git commit -m "feat(persistence): enforce RLS via SET LOCAL ROLE app_user (RLS Phase 2)"
```

> **PR B ends here (Tasks 5–6 + Task 4 fixes).** Merging enables enforcement in dev/CI.

---

## ⛔ HUMAN GATE 2 — Prod cutover (operational)

Deploy PR B to prod only after Gate 1 is clean. Monitor RLS-denial / error-rate / zero-row anomaly signals. Rollback = revert the Task-6 commit (single deploy).

---

## Task 7: Harden Detect into a permanent guard-rail

**Files:**
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/rls.py`
- Test: extend `tests/test_rls_detect.py`

- [ ] **Step 1:** Write a test: in a dev/test posture (an env signal, e.g. `EXPERT_WORK_ENV` in `{"dev","test"}` or a module flag), a non-bypass + no-tenant session **raises** a clear error (`RlsContextError`) instead of silently fail-closing; in prod posture it keeps the WARNING. Run — expect FAIL.
- [ ] **Step 2:** Implement: gate the raise-vs-warn on the env signal in `_rls_after_begin` (read once at import). Keep prod as warn (never break prod on this path). Run — expect PASS.
- [ ] **Step 3:** mypy + ruff + commit `feat(persistence): RLS context guard-rail (dev raises, prod warns) (Phase 4)`.

---

## Task 8: Threat-model doc + rollback runbook

**Files:**
- Create: `docs/security/rls-tenant-isolation.md`

- [ ] **Step 1:** Write the doc from spec §8–§9: the role model (`expert_work` / `app_user` / `audit_reader` / `audit_writer`), the enforcement mechanism, the guarantee statement, the four residual risks, the observability signals, and the per-phase rollback runbook (incl. the migration-downgrade ordering constraint — drop `app_user` only after enforcement is reverted).
- [ ] **Step 2:** Commit `docs(security): tenant-isolation RLS threat model + rollback runbook`.

> **PR C ends here (Tasks 7–8).**

---

## Post-plan: final whole-branch review

After all tasks, dispatch the final code review (superpowers:requesting-code-review) over the full branch, then finish via superpowers:finishing-a-development-branch. Pay special attention to: the `SET LOCAL ROLE` ordering vs the GUC, pgbouncer `server_reset_query` compatibility, and that no bypass call site was altered.
