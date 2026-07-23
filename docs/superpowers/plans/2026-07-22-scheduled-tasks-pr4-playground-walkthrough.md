# Spec 1 PR4 — 调试台端到端演练 + PR3 follow-up 收尾 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在调试台里把「对话建定时任务 → 立即触发 → 结果落回对话」跑通端到端,并收尾 PR3 遗留的三个 follow-up(投递幂等 / 搜索镜像滞后 / dead-letter 审计漏测)。

**Architecture:** 后端加一个认证的 `POST /v1/triggers/{trigger_id}:fire`「立即触发」端点(复用 `fire_trigger` 发射 + 有界轮询 run 到终态 + 复用共享投递函数落回原对话);把 scheduler 的 `_deliver` 抽成 `trigger_delivery.deliver_run_result` 供 scheduler 与端点共用(顺带做投递后镜像同步 FU2);`inject_delivery` 加 `source_run_id` 去重(FU1a),消除「端点投递」与「scheduler reconcile 投递」同进程竞争导致的重复贴。前端在 `manage_task` 工具卡上加「立即触发」按钮,把回传的结果文本渲染成对话里的「任务结果」卡 + created/fired/completed 生命周期 chip。

**Tech Stack:** Python(FastAPI / control-plane、orchestrator)、LangGraph checkpoint、dateutil.rrule(已在)、React + TypeScript + antd(admin-ui)、pytest、vitest。

## Global Constraints

- 提交**无署名**(禁 `Co-Authored-By` / 🤖);中文 conventional commits(`feat(...)`/`fix(...)`/`test(...)`)。
- 集成测试需 `export DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock`;纯内存/单测用 `DOCKER_HOST=` 置空。全部命令用 `uv run`(裸 `python` 会失败)。
- `ruff check` + `ruff format --check` 必须覆盖**所有 touched 文件,含新增/修改的测试文件**(PR1→PR2 两次踩过 CI blocker)。
- 改共享投递路径 / store / 端点后,跑 **CI 同款 pytest 范围**(含 control-plane);前端跑 `pnpm --filter admin-ui typecheck` + 相关组件测。
- **投递必须保持幂等**:同一 `source_run_id` 对同一原对话只贴一条结果(FU1a 之后由 `inject_delivery` 保证)。
- **投递 best-effort**:投递 / 镜像同步 / 审计任何异常都只 log,不阻断 trigger_run 状态转移(沿用 PR3 姿态)。
- **fire-now 端点 admin/owner-gated**:有主触发器仅 owner 或 admin(`resolve_target_user_id`);无主触发器仅 admin(`is_admin`)——与 `get_trigger`/`patch_trigger`/`delete_trigger` 逐字一致。
- 结果落回的 `AIMessage` 沿用 PR3 契约:`type=ai` + 非空 + **不设** `expert_work_hide_from_ui`(否则被用户视图过滤)+ 带 `additional_kwargs`(`expert_work_scheduled_delivery`/`expert_work_source_run_id`/`expert_work_trigger_id`)。
- 演示是 admin 单人操作;fire-now 同步端点的有界轮询默认封顶 `trigger_fire_now_timeout_s=60`,超时优雅返回 `delivery="pending"`(scheduler 后续 reconcile 兜底),不抛 5xx。

---

## File Structure

**后端(control-plane):**
- `services/control-plane/src/control_plane/trigger_delivery.py` — 改 `inject_delivery`(FU1a 去重);新增 `DeliveryOutcome` dataclass + `deliver_run_result(...)`(从 scheduler `_deliver` 抽出,含 FU2 镜像同步)。
- `services/control-plane/src/control_plane/scheduler.py` — `_deliver` 改为薄委托 `deliver_run_result`;ctor 加 `thread_message_store` 参;更新模块 docstring(投递已幂等)。
- `services/control-plane/src/control_plane/app.py` — `TriggerScheduler(...)` 传 `thread_message_store=resolved_thread_messages`。
- `services/control-plane/src/control_plane/api/triggers.py` — 新增 `POST /v1/triggers/{trigger_id}:fire` + 两个 dep-getter(`_get_run_store`/`_get_thread_message_store`)+ `_FireNowResponse`。
- `services/control-plane/src/control_plane/settings.py` — 新增 `trigger_fire_now_timeout_s`。

**测试(control-plane):**
- `services/control-plane/tests/test_trigger_delivery.py` — FU1a 幂等测(变异自证)+ `deliver_run_result` 单测(含镜像同步)。
- `services/control-plane/tests/test_scheduler.py` — FU3 dead-letter→TRIGGER_FAILED 测;确认 `_deliver` 委托后现有投递测全绿。
- `services/control-plane/tests/test_triggers_fire_now.py`(新)— fire-now 端点集成测。

**前端(admin-ui):**
- `apps/admin-ui/src/api/tool_timeline.ts` — `ToolCallEntry` 加 `triggerId`;ToolMessage 分支读 `m.artifact.trigger_id`。
- `apps/admin-ui/src/api/triggers.ts` — `fireTriggerNow(triggerId)` client + `FireNowResult` 类型。
- `apps/admin-ui/src/components/ToolTimeline.tsx` — `manage_task` 成功卡渲染「立即触发」按钮 + fire 结果状态。
- `apps/admin-ui/src/pages/agent_detail/PlaygroundTab.tsx` — 「任务结果」卡 + created/fired/completed chip。
- `apps/admin-ui/src/i18n/locales/{zh-CN,en}.ts` — 新键。

---

## Task 1 — FU1a 投递幂等 + FU3 dead-letter 审计测(PR3 收尾,地基)

**为什么先做:** T3 的 fire-now 端点会与 scheduler 的 reconcile 同进程竞争同一个 FIRED trigger_run(两边都投递)。FU1a 让 `inject_delivery` 按 `source_run_id` 去重,消除重复贴 —— 是 T3 的正确性前提。FU3 是纯补测,顺带。

**Files:**
- Modify: `services/control-plane/src/control_plane/trigger_delivery.py`(`inject_delivery` 去重 + docstring)
- Modify: `services/control-plane/src/control_plane/scheduler.py:24-34`(模块 docstring:投递已幂等)
- Test: `services/control-plane/tests/test_trigger_delivery.py`(新增幂等测)
- Test: `services/control-plane/tests/test_scheduler.py`(新增 dead-letter 测)

**Interfaces:**
- Consumes: 现有 `inject_delivery(graph, *, thread_id, tenant_id, result_text, source_run_id, trigger_id) -> None`;PR3 测 helper `_reuse_thread_trigger`/`_fired_run`/`_run_info`/`_build_scheduler(audit_store=)`(test_scheduler.py)。
- Produces: `inject_delivery` 语义升级为**幂等**(同 `source_run_id` 重复调用 → 第二次 no-op);行为契约不变(签名不变)。

- [ ] **Step 1: 写失败测 —— 幂等去重(test_trigger_delivery.py)**

在 `test_trigger_delivery.py` 末尾新增(复用文件已有的 `_build_graph`/内存 checkpointer fixture 与 `read_turns` 断言范式 —— 先 Read 该文件确认 helper 名):

```python
async def test_inject_delivery_is_idempotent_by_source_run_id() -> None:
    """同一 source_run_id 重复投递只落一条消息(FU1a)——消除 fire-now 端点与
    scheduler reconcile 同进程双投递导致的重复贴。"""
    graph, tenant_id, thread_id = await _build_graph_with_history()  # 见文件既有 helper
    src = uuid4()
    trig = uuid4()
    for _ in range(2):
        await inject_delivery(
            graph,
            thread_id=thread_id,
            tenant_id=tenant_id,
            result_text="今日 AI 新闻:...",
            source_run_id=src,
            trigger_id=trig,
        )
    turns = await read_turns(_checkpointer_of(graph), thread_id, include_hidden=True)
    delivered = [t for t in turns if t.content == "今日 AI 新闻:..."]
    assert len(delivered) == 1  # 两次调用,只贴一条
```

> 若文件里现成 helper 名不同(如 `_seed_thread`/`_graph`),按实际改用,别新造重复 fixture。

- [ ] **Step 2: 跑测确认失败**

Run: `DOCKER_HOST= uv run --project services/control-plane pytest services/control-plane/tests/test_trigger_delivery.py::test_inject_delivery_is_idempotent_by_source_run_id -q`
Expected: FAIL —— 当前实现无去重,`len(delivered) == 2`。

- [ ] **Step 3: 实现去重(inject_delivery)**

在 `trigger_delivery.py` 的 `inject_delivery` 里,`snapshot = await graph.aget_state(config)` 之后、构造 `message` 之前,插入去重扫描(复用已读的 `values["messages"]`,零额外 I/O):

```python
    snapshot = await graph.aget_state(config)
    values = snapshot.values if isinstance(snapshot.values, dict) else {}
    existing = values.get("messages") or []
    # FU1a — 幂等:同一 source_run_id 已投递过则跳过。fire-now 端点与 scheduler
    # reconcile 可能同进程各投递一次同一 run 的结果;去重保证只贴一条。
    tag = str(source_run_id)
    for m in existing:
        ak = getattr(m, "additional_kwargs", None)
        if isinstance(ak, dict) and ak.get("expert_work_source_run_id") == tag:
            return
    has_history = bool(existing)
    message = AIMessage(
        ...
    )
```

同时更新 `inject_delivery` docstring:点明「同一 `source_run_id` 只投递一次(幂等)」。

- [ ] **Step 4: 跑测确认通过 + 变异自证**

Run: `DOCKER_HOST= uv run --project services/control-plane pytest services/control-plane/tests/test_trigger_delivery.py -q`
Expected: 全绿(含新测 + PR3 3 测)。

**变异自证**(证测真挡去重回归):临时把 Step 3 的 `return` 改成 `pass`(或删去重 for 循环),重跑 → 确认 `test_inject_delivery_is_idempotent_by_source_run_id` **FAIL**(`len==2`),再 revert → 绿。报告记录变异证据。

- [ ] **Step 5: 更新 scheduler 模块 docstring(投递已幂等)**

`scheduler.py:24-34` 现声称「delivery ... is NOT [idempotent] ... before going multi-replica, delivery must be made idempotent — dedup by `expert_work_source_run_id` in `inject_delivery`, or a CAS claim」。FU1a 已落地去重,改为陈述现状:

```python
Stream 9.5 — the two run-spawning passes are CAS-guarded ...  (保留前半)
never double-spawn. The reconcile pass's status transition is idempotent (both
instances derive the same terminal status from the same run outcome). Its
result-delivery side effect (Spec 1 PR3/PR4 — appending a fired run's result
into the originating conversation) is now idempotent too: ``inject_delivery``
dedups by ``expert_work_source_run_id``, so two reconcilers — or the scheduler
racing the manual ``:fire`` endpoint (Spec 1 PR4) — append at most one copy.
(A duplicate reconcile still emits a redundant ``TRIGGER_COMPLETED`` audit
entry; a CAS claim gating the ``fired`` → ``succeeded`` transition would make
that exactly-once too — deferred, cosmetic.)
```

- [ ] **Step 6: 写失败测 —— FU3 dead-letter→TRIGGER_FAILED(test_scheduler.py)**

现有 `test_reconcile_exhausted_budget_dead_letters`(:341)只断 `row.status is DEAD_LETTER`,不断审计;`test_reconcile_interrupted_emits_trigger_failed`(:474)断审计但走 INTERRUPTED。合并二者补 dead-letter 审计缺口(复用 `_fired_run(attempt=5)` + `_run_info(status=ERROR)` + `_build_scheduler(audit_store=)`):

```python
async def test_reconcile_dead_letter_emits_trigger_failed() -> None:
    audit = InMemoryAuditLogStore()
    triggers = InMemoryTriggerStore()
    trigger_runs = InMemoryTriggerRunStore()
    runs = InMemoryRunStore()
    trig = _cron_trigger("t")
    await triggers.create(trig)
    run_id = uuid4()
    await trigger_runs.create(_fired_run(trigger_id=trig.id, run_id=run_id, attempt=5))
    await runs.create_from(_run_info(run_id, status=RunStatus.ERROR, error="boom"))  # 见文件 seed 范式
    scheduler, _ = await _build_scheduler(
        trigger_store=triggers, trigger_run_store=trigger_runs, run_store=runs, audit_store=audit,
    )
    await scheduler._reconcile_fired()
    rows = await trigger_runs.list_by_trigger(trigger_id=trig.id, tenant_id=_TENANT)
    assert rows[0].status is TriggerRunStatus.DEAD_LETTER
    entries = audit.query(AuditQuery(tenant_id=_TENANT, action=AuditAction.TRIGGER_FAILED)).entries
    assert len(entries) == 1
    assert entries[0].details["run_id"] == str(run_id)
```

> 先 Read `test_scheduler.py` 确认 seed run 到 InMemoryRunStore 的实际写法(`test_reconcile_exhausted_budget_dead_letters` 怎么塞 run),照抄。`_cron_trigger` 用文件既有 helper(:99 附近)。

- [ ] **Step 7: 跑测确认通过 + 无回归**

Run: `DOCKER_HOST= uv run --project services/control-plane pytest services/control-plane/tests/test_scheduler.py services/control-plane/tests/test_trigger_delivery.py -q`
Expected: 全绿,新增 2 测过,PR3 存量投递/reconcile 测 0 回归。

- [ ] **Step 8: lint + 提交**

Run: `uv run ruff check services/control-plane/src/control_plane/trigger_delivery.py services/control-plane/src/control_plane/scheduler.py services/control-plane/tests/test_trigger_delivery.py services/control-plane/tests/test_scheduler.py && uv run ruff format --check <同上四文件>`

```bash
git add services/control-plane/src/control_plane/trigger_delivery.py services/control-plane/src/control_plane/scheduler.py services/control-plane/tests/test_trigger_delivery.py services/control-plane/tests/test_scheduler.py
git commit -m "fix(triggers): 投递按 source_run_id 幂等去重 + dead-letter 审计补测(PR3 follow-up)"
```

---

## Task 2 — 抽共享投递函数 `deliver_run_result` + 镜像同步(FU2)

**目标:** 把 scheduler `_deliver` 的「读 run 结果 → 建原 Agent graph → inject_delivery」核心抽成 `trigger_delivery.deliver_run_result(...)`,供 scheduler 与 T3 的 fire-now 端点共用(DRY),并在投递成功后顺手 `sync_thread` 原对话,让结果立刻进全文搜索(FU2)。

**Files:**
- Modify: `services/control-plane/src/control_plane/trigger_delivery.py`(新增 `DeliveryOutcome` + `deliver_run_result`)
- Modify: `services/control-plane/src/control_plane/scheduler.py`(`_deliver` 委托;ctor 加 `thread_message_store`)
- Modify: `services/control-plane/src/control_plane/app.py:928`(传 `thread_message_store=resolved_thread_messages`)
- Test: `services/control-plane/tests/test_trigger_delivery.py`(`deliver_run_result` 单测)
- Test: `services/control-plane/tests/test_scheduler.py`(确认委托后投递测全绿)

**Interfaces:**
- Consumes: `read_turns(checkpointer, thread_id, include_hidden=) -> list[MessageTurn]`(transcript.py:30)、`ThreadMessageStore.sync_thread(*, thread_id, tenant_id, turns, synced_at)`(base.py:41)、`AgentRuntime.durable_checkpointer`/`.get_agent(...)`、`AgentSpecStore.get(...)`/`AgentSpecStatus.ACTIVE`、`TriggerRecord`/`RunInfo`、`inject_delivery`(T1 之后幂等)。
- Produces:
  ```python
  @dataclass(frozen=True)
  class DeliveryOutcome:
      status: str          # delivered/skipped/no_output/no_checkpointer/agent_unavailable/error
      text: str | None = None   # 投递的结果文本(status=="delivered" 时非空;供端点回显)

  async def deliver_run_result(
      *,
      trigger: TriggerRecord,
      run: RunInfo,
      runtime: AgentRuntime,
      agent_spec_store: AgentSpecStore,
      thread_message_store: ThreadMessageStore | None,
      now: datetime,
  ) -> DeliveryOutcome: ...
  ```
  **前置条件(docstring 写明):** 调用方须已进入 `trigger` 的租户 RLS scope(scheduler `_reconcile_one` 的 `_tenant_scope(row.tenant_id)`、端点的 `current_tenant_id_var`)。

- [ ] **Step 1: 写失败测 —— deliver_run_result 投递 + 镜像同步(test_trigger_delivery.py)**

```python
async def test_deliver_run_result_delivers_and_mirrors() -> None:
    """reuse_thread 触发器 + SUCCESS → 结果落原对话 checkpoint + 原对话被
    sync_thread 进搜索镜像(FU2)。"""
    # 复用 PR3 测里的真 graph(build_agent + provider_key_resolver)+ 内存 checkpointer;
    # 播种:scratch 线程末尾一条 assistant 结果 + 原对话有历史。
    ...
    mirror = InMemoryThreadMessageStore()
    outcome = await deliver_run_result(
        trigger=trigger,           # context_mode="reuse_thread", originating_thread_id=orig
        run=run_info,              # thread_id=scratch, status=SUCCESS
        runtime=runtime,
        agent_spec_store=agents,   # 含 ACTIVE spec
        thread_message_store=mirror,
        now=_NOW,
    )
    assert outcome.status == "delivered"
    assert outcome.text  # 非空,端点回显用
    # 原对话 checkpoint 落了结果
    turns = await read_turns(runtime.durable_checkpointer, orig, include_hidden=False)
    assert any(t.content == outcome.text for t in turns if t.role == "assistant")
    # 镜像也同步了原对话(pending 队列不再含 orig,或 mirror 有该 thread 的 turns)
    assert await _mirror_has_thread(mirror, orig)
```

> Read PR3 的 `test_trigger_delivery.py` + `test_scheduler.py` 的投递测,照搬其 `build_agent`/`provider_key_resolver`/`_platform_resolver` 与 spec/agents 播种 helper,别新造。`_mirror_has_thread` 用 `InMemoryThreadMessageStore` 的查询法(先看其 API)。

- [ ] **Step 2: 跑测确认失败**

Run: `DOCKER_HOST= uv run --project services/control-plane pytest services/control-plane/tests/test_trigger_delivery.py::test_deliver_run_result_delivers_and_mirrors -q`
Expected: FAIL —— `deliver_run_result` 未定义(ImportError)。

- [ ] **Step 3: 实现 deliver_run_result(trigger_delivery.py)**

把 scheduler `_deliver`(scheduler.py:405-452)的 try 体搬进模块函数,改用显式参数,末尾加 FU2 镜像同步:

```python
logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class DeliveryOutcome:
    status: str
    text: str | None = None

async def deliver_run_result(
    *,
    trigger: TriggerRecord,
    run: RunInfo,
    runtime: AgentRuntime,
    agent_spec_store: AgentSpecStore,
    thread_message_store: ThreadMessageStore | None,
    now: datetime,
) -> DeliveryOutcome:
    """把成功 run 的结果投递进 trigger 的原对话 + 刷新其搜索镜像。

    Best-effort:返回状态串(+ delivered 时的文本),永不抛(投递失败不得阻断
    trigger_run 的 SUCCEEDED 转移)。仅 reuse_thread + 有 originating_thread_id
    的对话建任务投递;后台任务跳过。前置:调用方须已进入 trigger 的租户 RLS scope。
    """
    try:
        if trigger.context_mode != "reuse_thread" or trigger.originating_thread_id is None:
            return DeliveryOutcome("skipped")
        checkpointer = runtime.durable_checkpointer
        if checkpointer is None:
            return DeliveryOutcome("no_checkpointer")
        turns = await read_turns(checkpointer, run.thread_id, include_hidden=False)
        result = next((t.content for t in reversed(turns) if t.role == "assistant"), None)
        if not result:
            return DeliveryOutcome("no_output")
        spec_record = await agent_spec_store.get(
            tenant_id=trigger.tenant_id, name=trigger.agent_name, version=trigger.agent_version,
        )
        if spec_record is None or spec_record.status is not AgentSpecStatus.ACTIVE:
            return DeliveryOutcome("agent_unavailable")
        built = await runtime.get_agent(
            tenant_id=trigger.tenant_id, name=trigger.agent_name,
            version=trigger.agent_version, spec=spec_record.spec,
        )
        await inject_delivery(
            built.graph,
            thread_id=trigger.originating_thread_id,
            tenant_id=trigger.tenant_id,
            result_text=result,
            source_run_id=run.run_id,
            trigger_id=trigger.id,
        )
        # FU2 — 投递是纯 checkpoint 注入,原对话无新 run 活动,镜像 sweep 不会重扫
        # (它只挑 agent_run.updated_at 前进的线程)→ 结果不即入全文搜索。这里直接
        # sync_thread 原对话补上。best-effort,在 try 内,失败不影响投递已成功。
        if thread_message_store is not None:
            mirror_turns = await read_turns(
                checkpointer, trigger.originating_thread_id, include_hidden=True
            )
            await thread_message_store.sync_thread(
                thread_id=trigger.originating_thread_id,
                tenant_id=trigger.tenant_id,
                turns=mirror_turns,
                synced_at=now,
            )
        return DeliveryOutcome("delivered", text=result)
    except Exception:
        logger.exception("trigger.delivery_failed", extra={"trigger_id": str(trigger.id)})
        return DeliveryOutcome("error")
```

补 imports:`dataclasses.dataclass`、`logging`、`datetime.datetime`、`AgentRuntime`、`AgentSpecStore`/`AgentSpecStatus`、`ThreadMessageStore`、`TriggerRecord`、`RunInfo`、`read_turns`。更新 `__all__` 加 `deliver_run_result`/`DeliveryOutcome`。

- [ ] **Step 4: scheduler `_deliver` 改薄委托**

`scheduler.py` `_deliver`(:405)改为:

```python
    async def _deliver(self, row: TriggerRunRecord, run: RunInfo) -> str:
        trigger = await self._triggers.get(trigger_id=row.trigger_id, tenant_id=row.tenant_id)
        if trigger is None:
            return "skipped"
        outcome = await deliver_run_result(
            trigger=trigger,
            run=run,
            runtime=self._runtime,
            agent_spec_store=self._agents,
            thread_message_store=self._thread_messages,
            now=datetime.now(UTC),
        )
        return outcome.status
```

import 从 `trigger_delivery` 改成 `from control_plane.trigger_delivery import deliver_run_result`(不再直接用 `inject_delivery`,若他处不再引用则移除)。

- [ ] **Step 5: scheduler ctor 加 thread_message_store**

`__init__` 加 keyword-only `thread_message_store: ThreadMessageStore | None = None`,存 `self._thread_messages = thread_message_store`。import `ThreadMessageStore`。放在现有可选参数区(与 `tenant_config_store` 同侧)。

- [ ] **Step 6: app.py 接线**

`app.py:928` `TriggerScheduler(...)` 加一行 `thread_message_store=resolved_thread_messages,`(该局部在 :583 已定义)。

- [ ] **Step 7: 跑测 —— 新单测 + scheduler 投递测无回归**

Run: `DOCKER_HOST= uv run --project services/control-plane pytest services/control-plane/tests/test_trigger_delivery.py services/control-plane/tests/test_scheduler.py -q`
Expected: 全绿。scheduler 现有投递测(reuse_thread→delivered / fresh→skipped)因委托后行为不变仍过;`_build_scheduler` 未传 `thread_message_store`(默认 None)→ 镜像同步分支跳过,不影响这些测。

- [ ] **Step 8: mypy + lint + 提交**

Run: `DOCKER_HOST= uv run --project services/control-plane mypy services/control-plane/src/control_plane/trigger_delivery.py services/control-plane/src/control_plane/scheduler.py` + `ruff check`/`ruff format --check` 覆盖全部 touched。

```bash
git add services/control-plane/src/control_plane/trigger_delivery.py services/control-plane/src/control_plane/scheduler.py services/control-plane/src/control_plane/app.py services/control-plane/tests/test_trigger_delivery.py
git commit -m "refactor(triggers): 抽 deliver_run_result 共享投递 + 投递后镜像同步(FU2)"
```

---

## Task 3 — fire-now 端点 `POST /v1/triggers/{trigger_id}:fire`

**目标:** 认证 + 所有权闸的「立即触发」端点:复用 `fire_trigger` 发射一次 → 有界轮询 run 到终态 → SUCCESS 则复用 `deliver_run_result` 落回原对话 → 回传 run/trigger_run/delivery 状态 + 结果文本。

**Files:**
- Modify: `services/control-plane/src/control_plane/api/triggers.py`(新路由 + 2 dep-getter + `_FireNowResponse`)
- Modify: `services/control-plane/src/control_plane/settings.py`(`trigger_fire_now_timeout_s`)
- Test: `services/control-plane/tests/test_triggers_fire_now.py`(新)

**Interfaces:**
- Consumes: `fire_trigger(...)`(trigger_firing.py:146,webhook handler triggers.py:573-585 是完整调用先例)、`deliver_run_result`(T2)、`RunStore.get(*, run_id, tenant_id) -> RunInfo|None`、`TERMINAL_RUN_STATUSES`/`RunStatus`(runs/schemas.py)、`resolve_target_user_id`/`is_admin`、`current_user_id_var`(webhook path 已用)、`TriggerRunStore.create/update`。
- Produces: `POST /v1/triggers/{trigger_id}:fire` → 200 `_FireNowResponse{run_id, thread_id, run_status, trigger_run_status, delivery, delivered_text?}`;404 无此触发器;403 越权;409 preflight 失败(agent 不可用 / kill switch);400 非 cron。

- [ ] **Step 1: 写失败测 —— fire-now 投递到原对话(集成,test_triggers_fire_now.py)**

用 control-plane 集成测惯例(真 app + 真 stores;参照 `tests/test_triggers.py` 的 app fixture 与认证 header)。核心用例:

```python
async def test_fire_now_delivers_result_to_originating_thread() -> None:
    # 建一个 reuse_thread cron 触发器(originating_thread_id=对话 T,config 含 rrule/seed_input),
    # 播种其 Agent spec=ACTIVE,且 stub runtime 让 fire 出的 run 落 SUCCESS + scratch 线程末尾
    # 有一条 assistant 结果。
    resp = await client.post(f"/v1/triggers/{trigger_id}:fire", headers=_admin_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["delivery"] == "delivered"
    assert body["delivered_text"]
    assert body["trigger_run_status"] == "succeeded"
    # 结果真落原对话 checkpoint
    msgs = await client.get(f"/v1/sessions/{T}/messages", headers=_admin_headers())
    assert any(body["delivered_text"] in m["content"] for m in msgs.json()["messages"])

async def test_fire_now_forbidden_for_non_owner() -> None:
    # 触发器属 user A;非 admin 的 user B → 403
    resp = await client.post(f"/v1/triggers/{trigger_id}:fire", headers=_user_b_headers())
    assert resp.status_code == 403

async def test_fire_now_agent_unavailable_returns_409() -> None:
    # spec 非 ACTIVE / kill switch → fire_trigger 返 None → 409
    ...
    assert resp.status_code == 409
```

> 集成测让 run 立即处于 SUCCESS:参照 `test_triggers.py` / `test_trigger_firing.py` 如何 stub runtime 使 `fire_trigger` 出的 run 直接终态(或用真轮询但 seed run store 为 SUCCESS)。这些用真 Docker → 需 `DOCKER_HOST=unix://...`。先 Read 现有 trigger 集成测的 app+fixture 骨架照搬。

- [ ] **Step 2: 跑测确认失败**

Run: `export DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock; uv run --project services/control-plane pytest services/control-plane/tests/test_triggers_fire_now.py -q`
Expected: FAIL —— 路由不存在(404 Not Found on the route,或 405)。

- [ ] **Step 3: 新增 setting**

`settings.py`(与 `trigger_scheduler_interval_s` 同段):

```python
    trigger_fire_now_timeout_s: int = Field(
        default=60, gt=0,
        description="调试台「立即触发」同步端点轮询 run 到终态的封顶秒数;超时返回 pending。",
    )
```

- [ ] **Step 4: 加 dep-getter(triggers.py)**

```python
def _get_run_store(request: Request) -> RunStore:
    return request.app.state.run_store  # type: ignore[no-any-return]

def _get_thread_message_store(request: Request) -> ThreadMessageStore:
    return request.app.state.thread_message_store  # type: ignore[no-any-return]
```

import `RunStore`、`ThreadMessageStore`、`RunStatus`/`TERMINAL_RUN_STATUSES`、`deliver_run_result`、`asyncio`、`timedelta`(部分已在)。

- [ ] **Step 5: 加响应模型(模块级)+ 路由(build_triggers_router 内)**

`_FireNowResponse` 放**模块级**,与 `_CreateTriggerBody`(:196)/`_PatchTriggerBody` 同处(不要嵌进 `build_triggers_router`):

```python
class _FireNowResponse(BaseModel):
    run_id: str
    thread_id: str
    run_status: str
    trigger_run_status: str
    delivery: str
    delivered_text: str | None = None
```

路由放 `build_triggers_router` 内(与 `create_trigger`/`get_trigger` 同级缩进):

```python
    @router.post("/{trigger_id}:fire", response_model=None)
    async def fire_trigger_now(
        trigger_id: UUID,
        request: Request,
        triggers: Annotated[TriggerStore, Depends(_get_trigger_store)],
        trigger_runs: Annotated[TriggerRunStore, Depends(_get_trigger_run_store)],
        runs: Annotated[RunStore, Depends(_get_run_store)],
        agents: Annotated[AgentSpecStore, Depends(_get_agent_spec_store)],
        threads: Annotated[ThreadMetaStore, Depends(_get_thread_store)],
        runtime: Annotated[AgentRuntime, Depends(_get_runtime)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
        approvals: Annotated[ApprovalStore, Depends(_get_approval_store)],
        tenant_configs: Annotated[TenantConfigStore, Depends(_get_tenant_config_store)],
        disable_service: Annotated[AgentDisableService, Depends(_get_agent_disable_service)],
        tenant_status: Annotated[TenantStatusService, Depends(_get_tenant_status_service)],
        thread_messages: Annotated[ThreadMessageStore, Depends(_get_thread_message_store)],
        users: Annotated[TenantUserStore, Depends(get_user_repo)],
        settings: Annotated[Settings, Depends(_get_settings)],
    ) -> _FireNowResponse:
        """调试台「立即触发」:发射一次 + 有界轮询到终态 + 成功则投递回原对话。"""
        tenant_id: UUID = request.state.tenant_id
        record = await triggers.get(trigger_id=trigger_id, tenant_id=tenant_id)
        if record is None:
            raise HTTPException(status_code=404, detail="trigger not found")
        # 所有权闸——与 get_trigger 逐字一致。
        if record.user_id is None:
            if not is_admin(request.state.principal):
                raise HTTPException(status_code=403, detail={
                    "code": "USER_SCOPE_FORBIDDEN",
                    "message": "only tenant admins may act on unowned triggers"})
        else:
            await resolve_target_user_id(request, users, requested=record.user_id)
        if record.kind != "cron":
            raise HTTPException(status_code=400, detail="only cron tasks support manual fire")

        now = datetime.now(UTC)
        # 在触发器自己的 user RLS scope 里发射(webhook path 先例:triggers.py:569-585)。
        user_tok = current_user_id_var.set(record.user_id)
        try:
            run_id = await fire_trigger(
                record, now=now, agent_spec_store=agents, runtime=runtime,
                thread_store=threads, audit_logger=audit, approval_store=approvals,
                trigger_store=triggers, tenant_config_store=tenant_configs,
                agent_disable_service=disable_service, tenant_status_service=tenant_status,
            )
            if run_id is None:
                raise HTTPException(status_code=409, detail="trigger agent unavailable")
            fired = TriggerRunRecord(
                id=uuid4(), tenant_id=tenant_id, trigger_id=record.id,
                run_id=run_id, status=TriggerRunStatus.FIRED, attempt=1, triggered_at=now,
            )
            await trigger_runs.create(fired)
        finally:
            current_user_id_var.reset(user_tok)

        # 有界轮询 run 到终态。
        deadline = now + timedelta(seconds=settings.trigger_fire_now_timeout_s)
        run: RunInfo | None = None
        while True:
            run = await runs.get(run_id=run_id, tenant_id=tenant_id)
            if run is not None and run.status in TERMINAL_RUN_STATUSES:
                break
            if datetime.now(UTC) >= deadline:
                return _FireNowResponse(
                    run_id=str(run_id),
                    thread_id=str(run.thread_id) if run else "",
                    run_status=run.status.value if run else "running",
                    trigger_run_status=TriggerRunStatus.FIRED.value,
                    delivery="pending",
                )
            await asyncio.sleep(1)

        # 终态处置——SUCCESS 投递,失败转终态(与 scheduler _reconcile_one 语义一致)。
        if run.status is RunStatus.SUCCESS:
            outcome = await deliver_run_result(
                trigger=record, run=run, runtime=runtime, agent_spec_store=agents,
                thread_message_store=thread_messages, now=datetime.now(UTC),
            )
            await trigger_runs.update(fired.model_copy(update={"status": TriggerRunStatus.SUCCEEDED}))
            await emit(audit, tenant_id=tenant_id, actor_id=request.state.actor_id,
                       action=AuditAction.TRIGGER_COMPLETED, resource_type="trigger",
                       resource_id=str(record.id), trace_id=current_trace_id_hex(),
                       details={"run_id": str(run_id), "delivery": outcome.status, "manual": True})
            return _FireNowResponse(
                run_id=str(run_id), thread_id=str(run.thread_id),
                run_status=run.status.value, trigger_run_status=TriggerRunStatus.SUCCEEDED.value,
                delivery=outcome.status, delivered_text=outcome.text,
            )
        # 失败:标 FAILED(fire-now 不做退避重试——一次性手动触发)。
        error = run.error or "run failed"
        await trigger_runs.update(fired.model_copy(update={"status": TriggerRunStatus.FAILED, "error": error}))
        await emit(audit, tenant_id=tenant_id, actor_id=request.state.actor_id,
                   action=AuditAction.TRIGGER_FAILED, resource_type="trigger",
                   resource_id=str(record.id), trace_id=current_trace_id_hex(),
                   details={"run_id": str(run_id), "error": error, "manual": True})
        return _FireNowResponse(
            run_id=str(run_id), thread_id=str(run.thread_id),
            run_status=run.status.value, trigger_run_status=TriggerRunStatus.FAILED.value,
            delivery="skipped",
        )
```

> 设计注:fire-now 建的 FIRED trigger_run 也在 scheduler 的 `list_fired` 视野内 → 轮询窗口里 scheduler 若同 sweep 也 reconcile 它,会各投递一次;**T1 的幂等去重保证只贴一条**(重复的仅一条 cosmetic `TRIGGER_COMPLETED` 审计)。这是 T1 作为 T3 前提的原因。

- [ ] **Step 6: 跑测确认通过**

Run: `export DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock; uv run --project services/control-plane pytest services/control-plane/tests/test_triggers_fire_now.py -q`
Expected: 3 测全绿。

- [ ] **Step 7: 回归 + mypy + lint + 提交**

Run: `export DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock; uv run --project services/control-plane pytest services/control-plane/tests/test_triggers.py services/control-plane/tests/test_scheduler.py services/control-plane/tests/test_trigger_delivery.py -q`(确认现有 trigger 测无回归)+ `mypy` triggers.py + `ruff check`/`format --check` 全 touched。

```bash
git add services/control-plane/src/control_plane/api/triggers.py services/control-plane/src/control_plane/settings.py services/control-plane/tests/test_triggers_fire_now.py
git commit -m "feat(triggers): 立即触发端点 POST /v1/triggers/{id}:fire —— 发射+有界轮询+投递回原对话"
```

---

## Task 4 — 前端:工具卡暴露 trigger_id + 「立即触发」按钮 + fire client

**目标:** 从 `manage_task` 创建结果里取出 `trigger_id`,在工具卡上渲染「立即触发」按钮,点击调 fire-now 端点并显示结果状态。

**Files:**
- Modify: `apps/admin-ui/src/api/tool_timeline.ts`(`ToolCallEntry.triggerId` + 解析)
- Modify: `apps/admin-ui/src/api/triggers.ts`(`fireTriggerNow` + `FireNowResult`)
- Modify: `apps/admin-ui/src/components/ToolTimeline.tsx`(按钮)
- Modify: `apps/admin-ui/src/i18n/locales/{zh-CN,en}.ts`
- Test: `apps/admin-ui/src/api/__tests__/tool_timeline.*`(若已有该测文件则扩展;否则加解析单测)

**Interfaces:**
- Consumes: 后端 `POST /v1/triggers/{id}:fire`(T3)、wire ToolMessage 的 `artifact.trigger_id`(源自 `ToolResult.meta`,builder.py:2819-2827 注释:artifact「surfaces in the raw event stream」)。
- Produces: `ToolCallEntry.triggerId?: string | null`;`fireTriggerNow(triggerId: string): Promise<FireNowResult>`。

- [ ] **Step 1: 验 artifact-on-wire(承重前置)**

先确认 SSE 原始事件流里的 ToolMessage 帧带 `artifact`(含 `trigger_id`)。手动:playground 里让 Agent 调一次 `manage_task` create,开 raw/exact 事件视图,查该 ToolMessage 帧的 JSON 是否有 `artifact: {trigger_id: ...}`;或直接在后端 SSE 帧序列化处确认 `artifact` 被 dump(builder.py:2827 的 `ToolMessage(..., artifact=...)`)。

- **若在 wire 上**(预期):走 Step 2 从 `m.artifact.trigger_id` 解析。
- **若不在 wire 上**(兜底):`manage_task` create 的 `content` 已含任务名;按钮改为「点击时以 `listTriggers({agentName, agentVersion})` 拉列表 + 按创建参数里的 `name` 匹配取 id」再 fire。将实际采用哪条在报告中说明。

- [ ] **Step 2: `ToolCallEntry` 加 triggerId + 解析(tool_timeline.ts)**

`ToolCallEntry` 接口加:

```typescript
  /** 定时任务工具(``manage_task`` create)回传的 trigger id —— 供「立即触发」按钮。取自 wire ToolMessage 的 ``artifact.trigger_id``。 */
  triggerId?: string | null;
```

在 ToolMessage 分支(tool_timeline.ts:249-285)读 artifact:

```typescript
        const art = (m as { artifact?: unknown }).artifact;
        if (art !== null && typeof art === "object") {
          const tid = (art as Record<string, unknown>).trigger_id;
          if (typeof tid === "string" && tid !== "") entry.triggerId = tid;
        }
```

(seed entry 的默认对象里 `triggerId: null`。)

- [ ] **Step 3: fire client(triggers.ts)**

```typescript
export interface FireNowResult {
  run_id: string;
  thread_id: string;
  run_status: string;
  trigger_run_status: string;
  delivery: string;
  delivered_text?: string | null;
}

export async function fireTriggerNow(triggerId: string): Promise<FireNowResult> {
  return apiFetch<FireNowResult>(`/v1/triggers/${encodeURIComponent(triggerId)}:fire`, {
    method: "POST",
  });
}
```

(照 triggers.ts 里现有 `createTrigger`/`patchTrigger` 的 `apiFetch` 范式;`:fire` 段 URL-encode 后冒号保留 —— 确认 apiFetch 不转义 `:`。)

- [ ] **Step 4: 「立即触发」按钮(ToolTimeline.tsx `ToolCallCard`)**

当 `entry.toolName === "manage_task"` && `entry.status === "success"` && `entry.triggerId` 时,渲染一个按钮:点击 → `setFiring(true)` → `fireTriggerNow(entry.triggerId)` → 成功后把 `FireNowResult` 经回调上抛给 PlaygroundTab(T5 渲染结果卡)+ 本地显示 `delivery` 状态(delivered/pending/skipped)。按钮在请求期间 loading(可能等到 ~60s)。用 antd `Button` + `App.useApp().message` 报错。

回调:`ToolCallCard` 通过 props 接 `onFireResult?: (r: FireNowResult) => void`;`StepTimeline`/`ToolTimeline` 逐层透传到 PlaygroundTab。若透传层级过深,改用 PlaygroundTab 提供的 context。

- [ ] **Step 5: i18n 键**

`zh-CN.ts` / `en.ts` 的 `tool_timeline`(或 `playground`)域加:`fire_now`("立即触发"/"Run now")、`firing`("触发中…"/"Running…")、`fire_delivered`("结果已落回对话"/"Result delivered")、`fire_pending`("已触发,运行中"/"Fired, still running")、`fire_failed`("触发失败"/"Fire failed")。**先 grep 确认键不与既有撞**(同 object 重复键 esbuild 静默覆盖 —— 教训在 [[llm-token-streaming-epic]])。

- [ ] **Step 6: typecheck + 解析单测**

Run: `pnpm --filter admin-ui typecheck` + `pnpm --filter admin-ui test -- tool_timeline`(若有)。
Expected: 绿;`artifact.trigger_id` 解析被覆盖(构造一个含 artifact 的 ToolMessage fixture → `entry.triggerId` 命中)。

- [ ] **Step 7: lint + 提交**

Run: `pnpm --filter admin-ui lint`(或仓库前端 lint 命令)。

```bash
git add apps/admin-ui/src/api/tool_timeline.ts apps/admin-ui/src/api/triggers.ts apps/admin-ui/src/components/ToolTimeline.tsx apps/admin-ui/src/i18n/locales/zh-CN.ts apps/admin-ui/src/i18n/locales/en.ts
git commit -m "feat(playground): manage_task 工具卡「立即触发」按钮 + fireTriggerNow client"
```

---

## Task 5 — 前端:结果落回对话卡 + created/fired/completed 生命周期

**目标:** fire-now 返回后,把 `delivered_text` 渲染成对话流里的「任务结果」卡(即时可见「结果落回原对话」);展示 created → fired → completed 三态。

**Files:**
- Modify: `apps/admin-ui/src/pages/agent_detail/PlaygroundTab.tsx`(结果卡 + chip + 接 fire 回调)
- Modify: `apps/admin-ui/src/i18n/locales/{zh-CN,en}.ts`

**Interfaces:**
- Consumes: T4 的 `FireNowResult`(经 `onFireResult` 回调上抛)。
- Produces: PlaygroundTab 状态里保存最近一次 fire 结果,渲染进当前对话流。

- [ ] **Step 1: PlaygroundTab 接 fire 结果 state**

加 `const [taskResults, setTaskResults] = useState<Array<{ triggerId: string; result: FireNowResult }>>([])`;`onFireResult` 回调 push。透传 `onFireResult` 到 `TurnCard`→`StepTimeline`→`ToolCallCard`(T4 已开 prop 链)。

- [ ] **Step 2: 渲染「任务结果」卡**

在对话流(TurnCard 之后,或 events 面板附近)渲染 `taskResults`:每条一张卡,标题 `t("playground.task_result")`("任务结果"),内容 `result.delivered_text` 经 `MarkdownView` 渲染;`delivery === "pending"` 时显示 `t("playground.fire_pending")` + 提示「稍后重开对话可见」。视觉复用现有 answer 卡样式。

> 说明:结果同时已写进对话 checkpoint(T3 投递),重开对话走 `getSessionMessages`(resume path)照样在;此卡是演示期的即时呈现,不替代 checkpoint 真相。

- [ ] **Step 3: 「查看运行」入口(spec §6:trace/步进复用现 run 视图)**

结果卡上加一个 `t("playground.view_run")`("查看运行详情")链接/按钮:用 `FireNowResult.thread_id`(fired run 的 scratch 线程)导航到现有会话详情 `/conversations/:threadId`(该视图已渲染 run 步进 / 工具 i-o / trace,与普通 run 一致 —— 复用,不在 playground 内重嵌)。这样「能力不弱化」由既有视图承接。`delivery === "pending"` 时同样可点(run 已在跑,详情页可看进度)。

- [ ] **Step 4: 生命周期 chip(created/fired/completed)**

在触发结果卡头部放三枚 antd `Tag`:created(工具调用已存在 → 常亮)、fired(fire 请求已发 → 常亮)、completed(`trigger_run_status === "succeeded"` → 绿;`"failed"` → 红;`"fired"`/pending → 灰「运行中」)。纯前端派生,不新拉审计接口。

- [ ] **Step 5: i18n 键**

`playground` 域加 `task_result`("任务结果")、`view_run`("查看运行详情")、`lifecycle_created`/`lifecycle_fired`/`lifecycle_completed`。同样 grep 防撞键。

- [ ] **Step 6: typecheck + 组件测**

Run: `pnpm --filter admin-ui typecheck` + 组件测(mock `fireTriggerNow` 返回 `delivery:"delivered"` + `delivered_text` → 断言「任务结果」卡渲染出文本 + completed chip 绿)。
Expected: 绿。

- [ ] **Step 7: lint + 提交**

```bash
git add apps/admin-ui/src/pages/agent_detail/PlaygroundTab.tsx apps/admin-ui/src/i18n/locales/zh-CN.ts apps/admin-ui/src/i18n/locales/en.ts
git commit -m "feat(playground): 立即触发结果落回对话卡 + created/fired/completed 生命周期"
```

---

## 端到端验收(全部任务后手动冒烟)

1. playground 与一个挂了 `manage_task` 的 Agent 对话:「每天早上3点搜AI新闻」→ Agent 调 `manage_task(create)` → 工具卡出现,带「立即触发」按钮。
2. 点「立即触发」→ 按钮 loading → 数秒后:对话里出现「任务结果」卡(AI 新闻结果)+ completed chip 绿。
3. 重开该对话(SessionHistoryDrawer resume)→ 结果消息仍在(checkpoint 真相)。
4. 全文搜索该对话内容 → 命中(FU2 镜像已同步)。
5. 非 admin 用别人的任务点 fire → 403(前端报错 toast)。

## 已知限制 / 明确后置(不在 PR4)

- fire-now 是**同步有界轮询**(默认封顶 60s);超时返回 `delivery="pending"`,scheduler 后续 reconcile 兜底投递。生产若前置代理超时更短,调 `trigger_fire_now_timeout_s` 或后续改异步(前端轮询)—— 留 follow-up,非本 PR。
- 竞争窗口内重复 `TRIGGER_COMPLETED` 审计(cosmetic;消息不重复,由 FU1a 保证)。exactly-once 审计需 CAS claim 门 `fired→succeeded` —— 已在 scheduler docstring 记,deferred。
- fire-now 失败**不做退避重试**(一次性手动触发);定时调度的重试/DLQ 仍走 scheduler。

## Global Constraints 自检(合并前)

- [ ] 无署名提交;中文 conventional commits。
- [ ] `ruff check` + `ruff format --check` 覆盖全部 touched(含新测 `test_triggers_fire_now.py`)。
- [ ] CI 同款 pytest 范围含 control-plane 跑绿;集成测带 `DOCKER_HOST`。
- [ ] `pnpm --filter admin-ui typecheck` 绿;i18n 新键无同 object 重复。
- [ ] 投递幂等(FU1a)+ best-effort(投递/镜像/审计异常不阻断转态)保持。
