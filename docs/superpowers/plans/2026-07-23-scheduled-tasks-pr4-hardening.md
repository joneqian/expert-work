# PR4 加固(投递 exactly-once + UI 小修)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 收尾 Spec 1 PR4 遗留 follow-up —— 后端投递路径 CAS-finalize(消除「立即触发」端点与后台 scheduler reconcile 竞争同一 FIRED 触发运行时的重复审计 + 失败路径状态发散),外加 3 个前端/工具小修(按钮 gate 收紧为 create-only、重触发前清态、timeout 空 thread_id 兜底)。

**Architecture:** 两条投递路径(scheduler `_reconcile_one` 与 fire-now 端点)今天各自 **无条件** `update()` 触发运行终态 + 无条件 emit 审计;并发处理同一 FIRED 行时产生双份审计,且失败时端点写 FAILED、scheduler 写 RETRYING 互相覆盖(last-writer-wins 撕裂)。改法:**deliver-first + CAS-finalize**——先投递(沿用 PR4 的 `inject_delivery` 幂等去重,消息不重),再用新 store 方法 `claim_reconcile`(CAS `FIRED → 终态`)原子转移状态,**仅 CAS 赢家 emit 生命周期审计**。这保 crash-safety(投递在 CAS 前,进程崩在中间 → 行仍 FIRED → 下轮 sweep 幂等重投),给审计 exactly-once,并让失败路径收敛到唯一确定值(先 CAS 者胜,输家 no-op 不覆盖)。

**Tech Stack:** Python 3.13 / asyncio / SQLAlchemy async(control-plane + expert-work-persistence);FastAPI(fire-now 端点);React + TypeScript + antd(admin-ui);orchestrator tools(manage_task)。

## Global Constraints

- **SQL ↔ in-memory 谓词 byte-identical**:`TriggerRunStore` 有 sql + memory 两实现;`claim_reconcile` 的 CAS 谓词(`id` + `tenant_id` + `status == FIRED`)与更新字段集必须在两处语义完全一致(命门:两 store 谓词分歧 = 集成测过、in-memory 单测过、生产炸)。base.py 抽象方法同步声明。
- **crash-safety 不可回退**:投递必须发生在状态终态转移**之前**(deliver-first)。任何"先标终态再投递"的顺序都会重新引入 PR4 修掉的 PAUSED 类静默丢投递(list_fired 只捞 FIRED,标了终态就再也不投)。
- **审计 gate 在 CAS 赢家**:`_emit_lifecycle` / `emit(...)` 仅当 `claim_reconcile` 返回 True 才调用。
- **消息幂等靠现有 dedup**:`inject_delivery` 的 `expert_work_source_run_id` 去重不动;CAS 不替代它(两路仍可能各 deliver 一次,dedup 兜)。跨副本真并行下 dedup 的 read-then-write TOCTOU **不在本 PR 范围**(单副本 asyncio 协作调度下窗口极窄;真多副本前须补原子 inject 或中间态,标记 DEFER)。
- **PAUSED / timeout → pending 不动**:两处的 `return ... delivery="pending"` 留 FIRED,**不** claim_reconcile(交后台兜底)。本 PR 只改终态(SUCCESS/失败/INTERRUPTED)分支。
- **提交**:conventional commits,type=`fix`/`feat`;**无 attribution**(不加 Co-Authored-By / 🤖)。commit body 正常英文/中文,不用 caveman。
- **集成测须 DOCKER_HOST**:`export DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock` + `uv run`(裸 python 失败);SQL store 改动必跑真容器集成测(in-memory 不校验 CHECK/并发)。
- **CI scope**:改共享 store 后跑全库 `ruff check`;后端 CI-scope `mypy`;前端 `npx tsc -b --noEmit` + vitest。编辑器诊断可能 stale——以真 tsc/pytest 定论。

---

## File Structure

**后端(Task 1-3):**
- `packages/expert-work-persistence/src/expert_work/persistence/trigger/base.py` — `TriggerRunStore` 抽象加 `claim_reconcile` 声明。
- `.../trigger/sql.py` — SQL 实现(CAS UPDATE WHERE status==fired)。
- `.../trigger/memory.py` — in-memory 实现(status is FIRED gate)。
- `packages/expert-work-persistence/tests/test_sql_trigger_store.py` / `test_in_memory_trigger_store.py` — store CAS 单/集成测。
- `services/control-plane/src/control_plane/scheduler.py` — `_reconcile_one` 三终态分支改 CAS-finalize + audit-gate。
- `services/control-plane/tests/test_scheduler.py` — 并发/收敛测。
- `services/control-plane/src/control_plane/api/triggers.py` — fire-now 端点终态分支改 CAS-finalize + audit-gate + 输家读实际状态。
- `services/control-plane/tests/test_triggers_fire_now.py` — 端点收敛测。

**前端 + 工具小修(Task 4):**
- `services/orchestrator/src/orchestrator/tools/manage_task.py` — `_create`/`_update` 的 `meta` 加 `"action"`。
- `services/orchestrator/tests/tools/test_manage_task.py` — meta.action 断言。
- `apps/admin-ui/src/api/tool_timeline.ts` — 解析 `artifact.action` 进 `ToolCallEntry.action`。
- `apps/admin-ui/src/api/__tests__/tool_timeline.test.ts` — action 解析测。
- `apps/admin-ui/src/components/ToolTimeline.tsx` — 按钮 gate 加 `action === "create"`;`FireNowButton.handleFire` 起手 `setDelivery(null)`。
- `apps/admin-ui/src/components/__tests__/ToolTimeline.test.tsx` — gate + 清态测。
- `apps/admin-ui/src/pages/agent_detail/PlaygroundTab.tsx` — `TaskResultCard` 「查看运行」按钮 gate 在 `result.thread_id` 非空。
- `apps/admin-ui/src/pages/__tests__/PlaygroundTab.test.tsx` — 空 thread_id 不渲染链接测。

**文档(与代码一同提交):**
- `docs/superpowers/plans/2026-07-21-memory-p5b-2b.md` / `2026-07-21-memory-p5b-2c.md` — 两个既有未提交的记忆 epic 计划文档,git add 收进本分支(用户要求"这次一起提交")。
- 本计划文档。

---

## Task 1: `claim_reconcile` store 方法(CAS `FIRED → 终态`)

**Files:**
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/trigger/base.py`(`TriggerRunStore` 抽象,`claim_retry` 声明之后 ~line 182)
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/trigger/sql.py`(`SqlTriggerRunStore`,`claim_retry` 之后 ~line 359)
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/trigger/memory.py`(`InMemoryTriggerRunStore`,`claim_retry` 之后 ~line 178)
- Test: `packages/expert-work-persistence/tests/test_in_memory_trigger_store.py` + `packages/expert-work-persistence/tests/test_sql_trigger_store.py`

**Interfaces:**
- Consumes: `TriggerRunRecord`(`expert_work.protocol`),`TriggerRunStatus`(`.FIRED/.SUCCEEDED/.FAILED/.RETRYING/.DEAD_LETTER`)。
- Produces: `async def claim_reconcile(self, record: TriggerRunRecord) -> bool` —— CAS 当前 `status == "fired"` → `record.status`,更新 `run_id/status/attempt/next_retry_at/error`,返回 `True` 当且仅当本调用执行了转移(行仍 FIRED);输家(已被别的路径终态化)→ `False`。Task 2/3 消费。

- [ ] **Step 1: 写失败测试(in-memory)**

在 `test_in_memory_trigger_store.py` 末尾追加(参照文件里既有 `claim_retry` 测试的 fixture 风格;若无则用下方自足写法):

```python
import pytest
from uuid import uuid4
from datetime import datetime, UTC

from expert_work.persistence.trigger.memory import InMemoryTriggerRunStore
from expert_work.protocol import TriggerRunRecord, TriggerRunStatus


def _fired(tenant_id, **over):
    base = dict(
        id=uuid4(),
        tenant_id=tenant_id,
        trigger_id=uuid4(),
        run_id=uuid4(),
        status=TriggerRunStatus.FIRED,
        attempt=1,
        triggered_at=datetime.now(UTC),
    )
    base.update(over)
    return TriggerRunRecord(**base)


@pytest.mark.asyncio
async def test_claim_reconcile_wins_from_fired():
    tenant = uuid4()
    store = InMemoryTriggerRunStore()
    row = _fired(tenant)
    await store.create(row)
    won = await store.claim_reconcile(row.model_copy(update={"status": TriggerRunStatus.SUCCEEDED}))
    assert won is True
    got = await store.get(trigger_run_id=row.id, tenant_id=tenant)
    assert got.status is TriggerRunStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_claim_reconcile_loses_when_already_terminal():
    tenant = uuid4()
    store = InMemoryTriggerRunStore()
    row = _fired(tenant)
    await store.create(row)
    first = await store.claim_reconcile(row.model_copy(update={"status": TriggerRunStatus.SUCCEEDED}))
    second = await store.claim_reconcile(row.model_copy(update={"status": TriggerRunStatus.FAILED, "error": "x"}))
    assert first is True
    assert second is False
    got = await store.get(trigger_run_id=row.id, tenant_id=tenant)
    assert got.status is TriggerRunStatus.SUCCEEDED  # 输家不覆盖


@pytest.mark.asyncio
async def test_claim_reconcile_cross_tenant_miss():
    tenant = uuid4()
    store = InMemoryTriggerRunStore()
    row = _fired(tenant)
    await store.create(row)
    won = await store.claim_reconcile(
        row.model_copy(update={"tenant_id": uuid4(), "status": TriggerRunStatus.SUCCEEDED})
    )
    assert won is False
```

- [ ] **Step 2: 跑测试确认 fail**

Run: `cd packages/expert-work-persistence && uv run pytest tests/test_in_memory_trigger_store.py -k claim_reconcile -v`
Expected: FAIL — `AttributeError: 'InMemoryTriggerRunStore' object has no attribute 'claim_reconcile'`

- [ ] **Step 3: base.py 加抽象声明**

在 `claim_retry` 抽象方法之后加:

```python
    @abc.abstractmethod
    async def claim_reconcile(self, record: TriggerRunRecord) -> bool:
        """Atomically finalize a ``fired`` firing; ``True`` iff this call won.

        Spec 1 PR4 加固 — CAS ``status == 'fired'`` → ``record.status`` (setting
        ``run_id/attempt/next_retry_at/error`` from ``record``). The scheduler's
        reconcile sweep and the manual fire-now endpoint can both observe the
        same ``fired`` firing after its run reaches a terminal state; routing
        the finalize through this CAS means exactly one of them performs the
        transition (and emits the lifecycle audit), the loser is a no-op. Fixes
        duplicate ``TRIGGER_COMPLETED`` audits and the failure-path split where
        the endpoint wrote ``failed`` while the scheduler wrote ``retrying``.

        Callers MUST deliver the result BEFORE calling this (deliver-first): a
        crash between deliver and the CAS leaves the row ``fired`` for the next
        sweep to redeliver idempotently; flipping status first would re-open the
        silent-delivery-loss window PR4 closed.
        """
```

- [ ] **Step 4: memory.py 实现**

在 `claim_retry` 之后加(镜像 `update()` 的整行替换 + FIRED gate):

```python
    async def claim_reconcile(self, record: TriggerRunRecord) -> bool:
        existing = self._rows.get(record.id)
        if existing is None or existing.tenant_id != record.tenant_id:
            return False
        if existing.status is not TriggerRunStatus.FIRED:
            # A peer already finalized this firing — loser, no-op (no overwrite).
            return False
        self._rows[record.id] = record
        return True
```

- [ ] **Step 5: sql.py 实现**

在 `claim_retry` 之后加(镜像 `update()` 的 values 集 + `status == fired` 谓词):

```python
    async def claim_reconcile(self, record: TriggerRunRecord) -> bool:
        # CAS ``fired`` → ``record.status`` so exactly one of {scheduler
        # reconcile, fire-now endpoint} finalizes this firing; the loser's
        # UPDATE matches no row (status already moved off ``fired``).
        async with self._sf() as session:
            result = await session.execute(
                sa_update(TriggerRunRow)
                .where(
                    TriggerRunRow.id == record.id,
                    TriggerRunRow.tenant_id == record.tenant_id,
                    TriggerRunRow.status == TriggerRunStatus.FIRED.value,
                )
                .values(
                    run_id=record.run_id,
                    status=record.status.value,
                    attempt=record.attempt,
                    next_retry_at=record.next_retry_at,
                    error=record.error,
                )
            )
            await session.commit()
        return int(getattr(result, "rowcount", 0) or 0) > 0
```

- [ ] **Step 6: 跑 in-memory 测试确认 pass**

Run: `cd packages/expert-work-persistence && uv run pytest tests/test_in_memory_trigger_store.py -k claim_reconcile -v`
Expected: PASS(3 测)

- [ ] **Step 7: 写 SQL 集成测试(真容器)**

在 `test_sql_trigger_store.py` 加(参照文件里既有 SQL store fixture / `claim_retry` 集成测的建行 helper):三例——赢家转 SUCCEEDED、输家(第二次 claim)返 False 且不覆盖、非 FIRED 起点(先手动 update 到 RETRYING 再 claim_reconcile)返 False。断言 `rowcount` 语义:

```python
@pytest.mark.integration
@pytest.mark.asyncio
async def test_sql_claim_reconcile_cas(sql_trigger_run_store, tenant_id):
    store = sql_trigger_run_store
    row = _make_fired_row(tenant_id)  # 复用文件里既有 helper;无则内联建 FIRED 行
    await store.create(row)
    won = await store.claim_reconcile(row.model_copy(update={"status": TriggerRunStatus.SUCCEEDED}))
    lost = await store.claim_reconcile(row.model_copy(update={"status": TriggerRunStatus.FAILED, "error": "x"}))
    assert won is True and lost is False
    got = await store.get(trigger_run_id=row.id, tenant_id=tenant_id)
    assert got.status is TriggerRunStatus.SUCCEEDED
```

- [ ] **Step 8: 跑 SQL 集成测试确认 pass**

Run: `export DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock && cd packages/expert-work-persistence && uv run pytest tests/test_sql_trigger_store.py -k claim_reconcile -v`
Expected: PASS

- [ ] **Step 9: ruff + commit**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && uv run ruff check packages/expert-work-persistence/`
Expected: 净

```bash
git add packages/expert-work-persistence/
git commit -m "feat(triggers): claim_reconcile CAS — 原子终态化 fired 触发运行

投递路径终态转移改 CAS gating(status==fired):scheduler reconcile 与
fire-now 端点可并发处理同一 fired 行,经此 CAS 仅一方执行转移。base/sql/
memory 三处谓词一致。为 PR4 加固(审计 exactly-once + 失败路径收敛)铺底。"
```

---

## Task 2: scheduler `_reconcile_one` 改 CAS-finalize + audit-gate

**Files:**
- Modify: `services/control-plane/src/control_plane/scheduler.py`(`_reconcile_one`,line 346-387)
- Test: `services/control-plane/tests/test_scheduler.py`

**Interfaces:**
- Consumes: `self._trigger_runs.claim_reconcile(record) -> bool`(Task 1)。
- Produces: 无新对外接口;`_reconcile_one` 行为变更——SUCCESS/失败/INTERRUPTED 三分支改 deliver/compute-first → `claim_reconcile` → `if won: emit`。

- [ ] **Step 1: 写失败测试——双 reconcile 只审计一次**

在 `test_scheduler.py` 加(参照文件既有 scheduler fixture:in-memory stores + fake runs + audit spy)。核心:同一 FIRED 行、SUCCESS run,连调两次 `_reconcile_one` → 只 1 条 TRIGGER_COMPLETED 审计,行终态 SUCCEEDED:

```python
@pytest.mark.asyncio
async def test_reconcile_success_audits_exactly_once(scheduler_fixture):
    sched, stores, audit = scheduler_fixture
    row = await _seed_fired_success(stores)          # FIRED 行 + 其 run 置 SUCCESS
    now = datetime.now(UTC)
    await sched._reconcile_one(row, now=now)
    await sched._reconcile_one(row, now=now)          # 第二遍模拟端点/重复 sweep
    completed = [a for a in audit.entries if a.action is AuditAction.TRIGGER_COMPLETED]
    assert len(completed) == 1
    got = await stores.trigger_runs.get(trigger_run_id=row.id, tenant_id=row.tenant_id)
    assert got.status is TriggerRunStatus.SUCCEEDED
```

- [ ] **Step 2: 写失败测试——失败路径收敛(先胜者定终态)**

```python
@pytest.mark.asyncio
async def test_reconcile_failure_converges_no_double_transition(scheduler_fixture):
    sched, stores, audit = scheduler_fixture
    row = await _seed_fired_error(stores)             # FIRED 行 + run 置 ERROR
    now = datetime.now(UTC)
    await sched._reconcile_one(row, now=now)          # 第一遍 → RETRYING(attempt<max)
    got1 = await stores.trigger_runs.get(trigger_run_id=row.id, tenant_id=row.tenant_id)
    await sched._reconcile_one(row, now=now)          # 第二遍:行已非 FIRED → CAS 输 → no-op
    got2 = await stores.trigger_runs.get(trigger_run_id=row.id, tenant_id=row.tenant_id)
    assert got1.status is TriggerRunStatus.RETRYING
    assert got2.status is TriggerRunStatus.RETRYING   # 未被第二遍覆盖
```

- [ ] **Step 3: 跑测试确认 fail**

Run: `cd services/control-plane && DOCKER_HOST= uv run pytest tests/test_scheduler.py -k "audits_exactly_once or converges" -v`
Expected: FAIL —— 当前无条件 update+emit,第一测得 2 条 COMPLETED。

- [ ] **Step 4: 改 `_reconcile_one`**

把 line 353-386 三分支改成(deliver/compute-first → claim_reconcile → gate audit):

```python
            if run.status is RunStatus.SUCCESS:
                delivery = await self._deliver(row, run)
                won = await self._trigger_runs.claim_reconcile(
                    row.model_copy(update={"status": TriggerRunStatus.SUCCEEDED})
                )
                if won:
                    await self._emit_lifecycle(
                        row,
                        action=AuditAction.TRIGGER_COMPLETED,
                        details={"run_id": str(row.run_id), "delivery": delivery},
                    )
            elif run.status in _FAILED_RUN_STATUSES:
                new = self._after_failure(row, now=now, error=run.error)
                won = await self._trigger_runs.claim_reconcile(new)
                if won and new.status is TriggerRunStatus.DEAD_LETTER:
                    await self._emit_lifecycle(
                        row,
                        action=AuditAction.TRIGGER_FAILED,
                        details={"run_id": str(row.run_id), "error": run.error},
                    )
            elif run.status is RunStatus.INTERRUPTED:
                # A deliberately-cancelled run is a terminal failure — no retry.
                won = await self._trigger_runs.claim_reconcile(
                    row.model_copy(
                        update={
                            "status": TriggerRunStatus.FAILED,
                            "error": "run interrupted",
                        }
                    )
                )
                if won:
                    await self._emit_lifecycle(
                        row,
                        action=AuditAction.TRIGGER_FAILED,
                        details={"run_id": str(row.run_id), "error": "run interrupted"},
                    )
            # PAUSED / RUNNING / PENDING — not terminal; reconcile next sweep.
```

注意:`_deliver` 保持在 CAS **之前**(deliver-first,crash-safe);`delivery` 结果照旧进 COMPLETED 审计 detail。

- [ ] **Step 5: 跑测试确认 pass**

Run: `cd services/control-plane && DOCKER_HOST= uv run pytest tests/test_scheduler.py -v`
Expected: PASS(含既有测——确认未回归)

- [ ] **Step 6: mypy + ruff + commit**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && uv run ruff check services/control-plane/ && cd services/control-plane && DOCKER_HOST= uv run mypy src/`
Expected: 净

```bash
git add services/control-plane/src/control_plane/scheduler.py services/control-plane/tests/test_scheduler.py
git commit -m "fix(triggers): scheduler reconcile 改 CAS-finalize + audit-gate

_reconcile_one 三终态分支(SUCCESS/失败/INTERRUPTED)由无条件 update+emit
改为 deliver/compute-first → claim_reconcile → 仅 CAS 赢家 emit 生命周期审计。
deliver 仍在 CAS 前(crash-safe)。消除与 fire-now 端点并发时的双份审计与
失败路径状态撕裂。"
```

---

## Task 3: fire-now 端点改 CAS-finalize + audit-gate + 输家读实际状态

**Files:**
- Modify: `services/control-plane/src/control_plane/api/triggers.py`(fire-now 终态分支,line 646-698)
- Test: `services/control-plane/tests/test_triggers_fire_now.py`

**Interfaces:**
- Consumes: `trigger_runs.claim_reconcile(record) -> bool`(Task 1);`trigger_runs.get(...)`(既有)。
- Produces: 端点行为变更——SUCCESS:deliver → `claim_reconcile(SUCCEEDED)` → gate COMPLETED 审计,返回照旧 SUCCEEDED+delivery+text。失败:`claim_reconcile(FAILED)` → gate FAILED 审计;**输家读实际状态**填 `trigger_run_status`(scheduler 可能已置 RETRYING)。

- [ ] **Step 1: 写失败测试——端点 SUCCESS 与既存终态收敛(不双审计)**

在 `test_triggers_fire_now.py` 加(参照文件既有 fire-now fixture:TestClient + in-memory stores + 预置 SUCCESS run)。模拟 scheduler 先赢:测试里先把 trigger_run 手动 `update(SUCCEEDED)`,再打端点 → 端点 CAS 输 → 不 emit COMPLETED,但仍返回 delivery=delivered + text(deliver 幂等):

```python
def test_fire_now_success_audit_gated_when_already_finalized(fire_now_client):
    client, stores, audit = fire_now_client
    trig = _seed_cron_trigger(stores)
    # 端点会 fire 建 FIRED 行;这里用一次端点调用走完,再断言只 1 条 COMPLETED
    resp = client.post(f"/v1/triggers/{trig.id}:fire")
    assert resp.status_code == 200
    body = resp.json()
    assert body["trigger_run_status"] == "succeeded"
    assert body["delivery"] == "delivered"
    completed = [a for a in audit.entries if a.action is AuditAction.TRIGGER_COMPLETED]
    assert len(completed) == 1  # 端点自身一条;CAS 保证不会有第二条来源
```

> 注:端点与 scheduler 的真并发在单测里难精确编排;本测锁定"端点走 claim_reconcile 路径且只 emit 一次"。跨路径收敛由 Task 2 的双-reconcile 测 + 本测的 CAS 语义共同保证(两路都走同一 `claim_reconcile`)。

- [ ] **Step 2: 写失败测试——失败路径输家读实际状态**

```python
def test_fire_now_failure_reports_scheduler_status_when_lost(fire_now_client, monkeypatch):
    client, stores, audit = fire_now_client
    trig = _seed_cron_trigger_failing(stores)  # 其 run 置 ERROR
    # 编排:令端点 claim_reconcile(FAILED) 返回 False,并预置 DB 为 RETRYING,
    # 断言响应 trigger_run_status == "retrying"(读实际),且端点不 emit FAILED。
    ...
    resp = client.post(f"/v1/triggers/{trig.id}:fire")
    body = resp.json()
    assert body["trigger_run_status"] == "retrying"
    failed = [a for a in audit.entries if a.action is AuditAction.TRIGGER_FAILED]
    assert len(failed) == 0
```

> 若 monkeypatch `claim_reconcile` 在此 fixture 下过繁,退而用直接单测端点的失败分支辅助函数;实现者按 fixture 能力择简。关键断言:输家不 emit + 读实际状态。

- [ ] **Step 3: 跑测试确认 fail**

Run: `cd services/control-plane && DOCKER_HOST= uv run pytest tests/test_triggers_fire_now.py -k "audit_gated or reports_scheduler_status" -v`
Expected: FAIL

- [ ] **Step 4: 改端点 SUCCESS 分支**

line 647-676,把无条件 `trigger_runs.update(SUCCEEDED)` + 无条件 emit 改成:

```python
        if run.status is RunStatus.SUCCESS:
            outcome = await deliver_run_result(
                trigger=record,
                run=run,
                runtime=runtime,
                agent_spec_store=agents,
                thread_message_store=thread_messages,
                now=datetime.now(UTC),
            )
            won = await trigger_runs.claim_reconcile(
                fired.model_copy(update={"status": TriggerRunStatus.SUCCEEDED})
            )
            if won:
                await emit(
                    audit,
                    tenant_id=tenant_id,
                    actor_id=request.state.actor_id,
                    action=AuditAction.TRIGGER_COMPLETED,
                    resource_type="trigger",
                    resource_id=str(record.id),
                    trace_id=current_trace_id_hex(),
                    details={"run_id": str(run_id), "delivery": outcome.status, "manual": True},
                )
            return _FireNowResponse(
                run_id=str(run_id),
                thread_id=str(run.thread_id),
                run_status=run.status.value,
                trigger_run_status=TriggerRunStatus.SUCCEEDED.value,
                delivery=outcome.status,
                delivered_text=outcome.text,
            )
```

- [ ] **Step 5: 改端点失败分支**

line 677-698,改成 CAS + 输家读实际:

```python
        # 失败:一次性手动触发不做退避重试,标 FAILED。经 claim_reconcile 与
        # scheduler reconcile 收敛——先 CAS 者定终态;输家读实际状态回填响应。
        error = run.error or "run failed"
        won = await trigger_runs.claim_reconcile(
            fired.model_copy(update={"status": TriggerRunStatus.FAILED, "error": error})
        )
        if won:
            final_status = TriggerRunStatus.FAILED.value
            await emit(
                audit,
                tenant_id=tenant_id,
                actor_id=request.state.actor_id,
                action=AuditAction.TRIGGER_FAILED,
                resource_type="trigger",
                resource_id=str(record.id),
                trace_id=current_trace_id_hex(),
                details={"run_id": str(run_id), "error": error, "manual": True},
            )
        else:
            # scheduler 已先终态化(如置 RETRYING);报实际状态,不重复审计。
            current = await trigger_runs.get(trigger_run_id=fired.id, tenant_id=tenant_id)
            final_status = current.status.value if current else TriggerRunStatus.FAILED.value
        return _FireNowResponse(
            run_id=str(run_id),
            thread_id=str(run.thread_id),
            run_status=run.status.value,
            trigger_run_status=final_status,
            delivery="skipped",
        )
```

- [ ] **Step 6: 跑测试确认 pass**

Run: `cd services/control-plane && DOCKER_HOST= uv run pytest tests/test_triggers_fire_now.py -v`
Expected: PASS(含既有 delivered/403/409/PAUSED/timeout 测——确认未回归)

- [ ] **Step 7: mypy + ruff + commit**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && uv run ruff check services/control-plane/ && cd services/control-plane && DOCKER_HOST= uv run mypy src/`
Expected: 净

```bash
git add services/control-plane/src/control_plane/api/triggers.py services/control-plane/tests/test_triggers_fire_now.py
git commit -m "fix(triggers): fire-now 端点改 CAS-finalize + audit-gate

终态分支由无条件 update+emit 改为 claim_reconcile → 仅赢家 emit。SUCCESS
仍 deliver-first(dedup 兜);失败输家读实际 trigger_run 状态回填响应(scheduler
可能已置 retrying),避免响应与 DB 撕裂。与 scheduler reconcile 收敛到唯一终态。"
```

---

## Task 4: UI 小修 —— 按钮 gate create-only + 重触发清态 + timeout thread_id 兜底

**Files:**
- Modify: `services/orchestrator/src/orchestrator/tools/manage_task.py`(`_create` line 296-302 / `_update` line 361)
- Test: `services/orchestrator/tests/tools/test_manage_task.py`
- Modify: `apps/admin-ui/src/api/tool_timeline.ts`(`ToolCallEntry` + artifact 解析 line 41-42 / 289-297)
- Test: `apps/admin-ui/src/api/__tests__/tool_timeline.test.ts`
- Modify: `apps/admin-ui/src/components/ToolTimeline.tsx`(按钮 gate line 199 / `FireNowButton.handleFire` line 233)
- Test: `apps/admin-ui/src/components/__tests__/ToolTimeline.test.tsx`
- Modify: `apps/admin-ui/src/pages/agent_detail/PlaygroundTab.tsx`(`TaskResultCard` line 1634-1645)
- Test: `apps/admin-ui/src/pages/__tests__/PlaygroundTab.test.tsx`

**Interfaces:**
- Consumes: 既有 wire artifact 管道(`ToolResult.meta` → `ToolMessage.artifact` → 前端 `m.artifact.*`)。
- Produces: `ToolCallEntry.action?: string | null`(取自 `artifact.action`);「立即触发」按钮 gate 增 `entry.action === "create"`;`TaskResultCard` 「查看运行」按钮 gate 增 `result.thread_id !== ""`。

### 4A — 后端:manage_task meta 带 action

- [ ] **Step 1: 写失败测试**

在 `test_manage_task.py` 加(参照既有 create/update 测):

```python
@pytest.mark.asyncio
async def test_create_meta_carries_action(manage_task_ctx):
    tool, ctx = manage_task_ctx
    res = await tool.call({"action": "create", ...}, ctx=ctx)  # 复用既有 create 入参
    assert res.meta.get("action") == "create"
    assert "trigger_id" in res.meta


@pytest.mark.asyncio
async def test_update_meta_carries_action(manage_task_ctx):
    tool, ctx = manage_task_ctx
    # 先 create 得 task_id 再 update(复用既有 update 测的编排)
    ...
    res = await tool.call({"action": "update", "task_id": ..., ...}, ctx=ctx)
    assert res.meta.get("action") == "update"
```

- [ ] **Step 2: 跑测试确认 fail**

Run: `cd services/orchestrator && uv run pytest tests/tools/test_manage_task.py -k "meta_carries_action" -v`
Expected: FAIL — `res.meta.get("action") is None`

- [ ] **Step 3: 改 `_create` / `_update` 的 meta**

`_create`(line 296-302):

```python
        return ToolResult(
            content=...,   # 原文不动
            meta={"trigger_id": str(record.id), "action": "create"},
        )
```

`_update`(line 361):

```python
        return ToolResult(content=f"Updated task {rec.name!r}.", meta={"trigger_id": str(rec.id), "action": "update"})
```

- [ ] **Step 4: 跑测试确认 pass + 既有回归**

Run: `cd services/orchestrator && uv run pytest tests/tools/test_manage_task.py -v`
Expected: PASS

### 4B — 前端:tool_timeline 解析 action

- [ ] **Step 5: 写失败测试(tool_timeline.test.ts)**

参照既有 triggerId 解析测,加 action 用例:artifact `{trigger_id, action:"create"}` → `entry.action === "create"`;`{trigger_id, action:"update"}` → `"update"`;无 action → `undefined`/`null`。

- [ ] **Step 6: 跑确认 fail**

Run: `cd apps/admin-ui && npx vitest run src/api/__tests__/tool_timeline.test.ts -t action`
Expected: FAIL

- [ ] **Step 7: 改 tool_timeline.ts**

`ToolCallEntry` 接口(line 41-42 附近)加:

```typescript
  /** ``manage_task`` 动作(create/update/…),取自 wire ``artifact.action``。
   *  「立即触发」按钮据此收紧为仅 create 卡。 */
  action?: string | null;
```

两处初始化(line 239 / 268 附近 `triggerId: null,` 旁)加 `action: null,`。artifact 解析(line 293-297)扩展:

```typescript
        const art = (m as { artifact?: unknown }).artifact;
        if (art !== null && typeof art === "object") {
          const rec = art as Record<string, unknown>;
          const tid = rec.trigger_id;
          if (typeof tid === "string" && tid !== "") entry.triggerId = tid;
          const act = rec.action;
          if (typeof act === "string" && act !== "") entry.action = act;
        }
```

- [ ] **Step 8: 跑确认 pass**

Run: `cd apps/admin-ui && npx vitest run src/api/__tests__/tool_timeline.test.ts`
Expected: PASS

### 4C — 前端:按钮 gate create-only + 重触发清态

- [ ] **Step 9: 写失败测试(ToolTimeline.test.tsx)**

两例:(a) `manage_task` success + triggerId + `action:"update"` → 无「立即触发」按钮(`queryByTestId("tool-fire-now")` 为 null);既有 `action:"create"` 用例仍有按钮。(b) 清态:fire 得 delivered Tag 后,再点 → loading 起手 Tag 消失(断言 `handleFire` 起手 `setDelivery(null)`——通过"再次点击后 status Tag 短暂清空"或直接断言点击后同步无旧 Tag)。

- [ ] **Step 10: 跑确认 fail**

Run: `cd apps/admin-ui && npx vitest run src/components/__tests__/ToolTimeline.test.tsx`
Expected: FAIL(update 卡仍渲染按钮)

- [ ] **Step 11: 改 ToolTimeline.tsx**

按钮 gate(line 199-201):

```tsx
        {entry.toolName === "manage_task" &&
        entry.status === "success" &&
        entry.action === "create" &&
        entry.triggerId ? (
          <FireNowButton triggerId={entry.triggerId} onFireResult={onFireResult} />
        ) : null}
```

`FireNowButton.handleFire`(line 233-245)起手清态:

```tsx
  const handleFire = useCallback(async () => {
    setFiring(true);
    setDelivery(null);   // 重触发前清旧投递态,避免上次结果 Tag 残留
    try {
      const result = await fireTriggerNow(triggerId);
      ...
```

- [ ] **Step 12: 跑确认 pass**

Run: `cd apps/admin-ui && npx vitest run src/components/__tests__/ToolTimeline.test.tsx`
Expected: PASS

### 4D — 前端:TaskResultCard timeout thread_id 兜底

- [ ] **Step 13: 写失败测试(PlaygroundTab.test.tsx)**

参照既有 TaskResultCard 渲染测,加:`FireNowResult` 带 `thread_id: ""`(timeout 情形)→ `queryByTestId("playground-task-result-view-run")` 为 null(不渲染坏链接);`thread_id` 非空 → 渲染按钮。

- [ ] **Step 14: 跑确认 fail**

Run: `cd apps/admin-ui && npx vitest run src/pages/__tests__/PlaygroundTab.test.tsx -t "view.run"`
Expected: FAIL(空 thread_id 仍渲染)

- [ ] **Step 15: 改 TaskResultCard**

line 1634-1645,「查看运行」按钮包一层 thread_id 非空 gate:

```tsx
        {result.thread_id !== "" ? (
          <Button
            size="small"
            type="link"
            icon={<ExternalLink size={12} strokeWidth={1.75} />}
            onClick={() =>
              navigate(`/conversations/${encodeURIComponent(result.thread_id)}`)
            }
            style={{ marginLeft: "auto" }}
            data-testid="playground-task-result-view-run"
          >
            {t("playground.view_run")}
          </Button>
        ) : null}
```

- [ ] **Step 16: 跑确认 pass + typecheck**

Run: `cd apps/admin-ui && npx vitest run src/pages/__tests__/PlaygroundTab.test.tsx && npx tsc -b --noEmit`
Expected: PASS + tsc exit 0

- [ ] **Step 17: 前端 lint + 后端 ruff + commit**

Run: `cd apps/admin-ui && npx eslint src/api/tool_timeline.ts src/components/ToolTimeline.tsx src/pages/agent_detail/PlaygroundTab.tsx` + `cd /Users/mac/src/github/jone_qian/expert-work && uv run ruff check services/orchestrator/`
Expected: 净

```bash
git add services/orchestrator/ apps/admin-ui/src/
git commit -m "fix(playground): 立即触发按钮 create-only + 重触发清态 + timeout 兜底

- manage_task create/update 的 meta 带 action;前端解析 artifact.action
- 「立即触发」按钮 gate 收紧为仅 create 卡(update 卡不再误显)
- FireNowButton 重触发前 setDelivery(null),清上次投递态残留
- TaskResultCard 「查看运行」仅在 thread_id 非空时渲染(timeout 时 run
  未可读、thread_id 为空,避免坏链接)"
```

---

## Self-Review

- **Spec coverage**:CAS exactly-once(Task 1-3)+ T4① 按钮 gate(4A-4C)+ T4② 清态(4C)+ N3 thread_id 兜底(4D)= 用户选定 Option 1 全覆盖。live-e2e-smoke 明确 DEFER(需真栈)。
- **crash-safety**:三处终态分支均 deliver/compute-first → CAS(Global Constraints 硬约束),未引入"先标终态"顺序。
- **byte-identical**:`claim_reconcile` 谓词 = `id + tenant_id + status==FIRED`,sql/memory 两处一致,base 声明同步。
- **类型一致**:`claim_reconcile(record: TriggerRunRecord) -> bool` 三处签名一致;`ToolCallEntry.action` 前端解析与 gate 同名。
- **无 placeholder**:各步含完整代码;测试 fixture 处标注"参照既有"(实现者读现有测试文件复用 fixture),非 TODO。
- **DEFER(文档化,不做)**:跨副本 dedup TOCTOU 真原子化(需原子 inject 或中间态 + 崩溃 reclaim);manual-fire 是否应被 scheduler 重试(需 trigger_run 加 manual 列 = migration)。二者单副本无风险,多副本迁移前处理。

## Execution Handoff

Plan complete。执行走 **subagent-driven-development**(fresh implementer/task + task reviewer/task + opus 全分支终审),模型:implementer/reviewer 用 sonnet,终审 opus。
