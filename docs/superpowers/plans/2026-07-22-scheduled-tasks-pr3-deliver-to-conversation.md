# 结果回原对话(D1)实现计划(Spec 1 PR3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 定时任务成功跑完后,把结果作为一条 AI 消息追加回创建它的那个对话(D1「结果回原对话」),用户重开对话即见;并给触发生命周期补 `TRIGGER_COMPLETED`/`TRIGGER_FAILED` 审计。

**Architecture:** 投递发生在 **scheduler reconcile pass**(`_reconcile_one` 的 run 成功分支)—— 那里才知道触发跑已成功、结果可读。步骤:读该 run 临时线程 checkpoint 的最后 assistant 回复(`read_turns`)→ 取 trigger 的 `originating_thread_id`/`context_mode`(PR2 已落库)→ 若 `context_mode=='reuse_thread'` 且有原对话 id → 用原 Agent 的 `built.graph.aupdate_state` 把结果 `AIMessage` **追加**进原对话 checkpoint(复用仓库既有 resume/plan/sanitize 的 `aupdate_state` 机制,不跑 LLM、不回放历史)。投递 best-effort(失败记日志+审计,不回滚 run 结果)。用户重开对话走 `GET /v1/sessions/{id}/messages` 直读 checkpoint 即见(无镜像延迟)。

**Tech Stack:** Python 3.12,LangGraph `CompiledStateGraph.aupdate_state`(append via `add_messages` reducer),`langchain_core.messages.AIMessage`,control-plane scheduler。

## Global Constraints

- **提交无署名**:任何 commit / PR body 不带 `Co-Authored-By` 或 🤖 行。中文 conventional commits。
- **投递位置 = reconcile,不是 fire_trigger**:`fire_trigger` 发射即忘(结果未出);投递在 `_reconcile_one` 见 `run.status is RunStatus.SUCCESS` 时。
- **只投递(D-10)**:触发跑照旧在独立临时线程干净跑;投递只把**结果消息**追加进原对话,不回放原对话历史、不跑 LLM。用 `graph.aupdate_state(config, {"messages":[AIMessage]}, as_node=...)` —— `messages` 通道是 `add_messages` **append** reducer(state.py:183),追加不覆盖。
- **投递条件**:`trigger.context_mode == "reuse_thread"` **且** `trigger.originating_thread_id is not None` **且** run 成功。`fresh_thread_per_run`(后台建,Spec 3)→ 不投递(现行为)。
- **best-effort**:投递任何异常(原对话不存在 / Agent 不可用 / checkpointer None / 注入失败)只记日志 + 审计 delivery 状态,**不阻断** trigger_run 转 SUCCEEDED、不回滚 run 结果。用户面失败通知属 Spec 2。
- **消息可见性铁律**:投递的 `AIMessage` 必须 `type="ai"` + 非空文本 + **不设** `expert_work_hide_from_ui`(该标记会被用户视图过滤掉,transcript.py:63)。带 `additional_kwargs={"expert_work_scheduled_delivery": True, "expert_work_source_run_id": ..., "expert_work_trigger_id": ...}` 供溯源。
- **`as_node`**:照既有约定(plan.py:191-194)——原对话有历史用 `"agent"`,空线程用 `"__start__"`;节点名 `"agent"` 已确认存在(builder.py:1420)。投递的 AIMessage 无 tool_calls → agent 后的条件边路由到 END → 线程留在干净"待下轮输入"态。
- **审计**:`TRIGGER_COMPLETED`(每次 run 成功发,details 含 delivery 状态)/ `TRIGGER_FAILED`(仅**终态**失败发 —— DEAD_LETTER 或 INTERRUPTED→FAILED;RETRYING 是瞬态不发)。`resource_type="trigger"` 已白名单,无需改 ResourceType。
- **已知限制(文档化,不修)**:内容搜索镜像 `TranscriptMirrorSweep` 只在原对话有 `agent_run.updated_at` 推进时重扫;纯注入不推进 → 投递消息**不会立刻进全文搜索索引**(但"重开对话即见"的主读路径 runs.py:1298 直读 checkpoint 不受影响,spec §5 只要求后者)。留 follow-up。
- **CI 契约**:`ruff check` **和** `ruff format --check`(覆盖所有 touched 含新/改测试文件)+ CI-scope `mypy` + `uv run`。T3 端到端集成测需 `DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock`;T1/T2 纯/内存测用 `DOCKER_HOST=`(空)。

---

## 文件结构

| 文件 | 职责 | 任务 |
|------|------|------|
| `packages/expert-work-protocol/src/expert_work/protocol/audit.py` | `AuditAction` 加 `TRIGGER_COMPLETED`/`TRIGGER_FAILED` | T1 |
| `services/control-plane/src/control_plane/trigger_delivery.py` | **新** `inject_delivery(graph, ...)` —— aupdate_state 注入 AIMessage 进指定线程 | T2 |
| `services/control-plane/src/control_plane/scheduler.py` | `_reconcile_one` 接线投递 + `_deliver`/`_emit_lifecycle` 助手 + imports | T3 |

测试:`packages/expert-work-protocol/tests/test_audit_actions.py`(T1,就近扩或新)、`services/control-plane/tests/test_trigger_delivery.py`(T2,内存 checkpointer 真 graph)、`services/control-plane/tests/test_scheduler.py`(T3,扩,Docker 集成)。

---

## Task 1: `AuditAction` 加 `TRIGGER_COMPLETED` / `TRIGGER_FAILED`

**Files:**
- Modify: `packages/expert-work-protocol/src/expert_work/protocol/audit.py:231-238`
- Test: `packages/expert-work-protocol/tests/test_audit_actions.py`(若已有 audit 测试文件则就近扩)

**Interfaces:**
- Produces: `AuditAction.TRIGGER_COMPLETED = "trigger:completed"`、`AuditAction.TRIGGER_FAILED = "trigger:failed"` —— T3 reconcile 发这两个。

- [ ] **Step 1: 写失败测试**

`packages/expert-work-protocol/tests/test_audit_actions.py`(新;若同目录已有 audit enum 测试,加到那里并跳过重复 import):

```python
from __future__ import annotations

from expert_work.protocol import AuditAction


def test_trigger_completed_wire_value() -> None:
    assert AuditAction.TRIGGER_COMPLETED.value == "trigger:completed"


def test_trigger_failed_wire_value() -> None:
    assert AuditAction.TRIGGER_FAILED.value == "trigger:failed"
```

> **实现者注**:确认 `AuditAction` 从 `expert_work.protocol` 顶层可导入(Explore 证 audit.py 内枚举;若顶层未 re-export,用 `from expert_work.protocol.audit import AuditAction`,以真实存在者为准)。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && DOCKER_HOST= uv run --project packages/expert-work-protocol pytest packages/expert-work-protocol/tests/test_audit_actions.py -q`
Expected: FAIL(`AttributeError: TRIGGER_COMPLETED`)。

> 若该 package 无独立 pytest project,用仓库根 `DOCKER_HOST= uv run pytest packages/expert-work-protocol/tests/test_audit_actions.py -q`。

- [ ] **Step 3: 加枚举成员**

`audit.py` 的 trigger 段(现 :231-238,`TRIGGER_FIRE` 之后),加两行:

```python
    TRIGGER_FIRE = "trigger:fire"
    # triggers — Spec 1 PR3 (conversational scheduled tasks): reconcile-time
    # lifecycle outcomes emitted when a fired run reaches a terminal status.
    TRIGGER_COMPLETED = "trigger:completed"
    TRIGGER_FAILED = "trigger:failed"
```

(保持现有 `TRIGGER_PROMPT_INJECTION_*` 在其后不动。)

- [ ] **Step 4: 跑测试确认通过**

Run: 同 Step 2 命令。Expected: PASS。

- [ ] **Step 5: lint + type**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && uv run ruff check packages/expert-work-protocol/src/expert_work/protocol/audit.py packages/expert-work-protocol/tests/test_audit_actions.py && uv run ruff format --check packages/expert-work-protocol/src/expert_work/protocol/audit.py packages/expert-work-protocol/tests/test_audit_actions.py && uv run mypy packages/expert-work-protocol/src/expert_work/protocol/audit.py`
Expected: clean。

- [ ] **Step 6: 提交**

```bash
cd /Users/mac/src/github/jone_qian/expert-work
git add packages/expert-work-protocol/src/expert_work/protocol/audit.py packages/expert-work-protocol/tests/test_audit_actions.py
git commit -m "feat(audit): AuditAction 加 TRIGGER_COMPLETED/TRIGGER_FAILED(PR3 触发生命周期)"
```

---

## Task 2: 投递助手 `inject_delivery`(aupdate_state 注入 AIMessage)

**Files:**
- Create: `services/control-plane/src/control_plane/trigger_delivery.py`
- Test: `services/control-plane/tests/test_trigger_delivery.py`

**Interfaces:**
- Produces: `async def inject_delivery(graph, *, thread_id: UUID, tenant_id: UUID, result_text: str, source_run_id: UUID, trigger_id: UUID) -> None` —— 把 `result_text` 作为 `AIMessage` 追加进 `thread_id` 的 checkpoint。`graph` 是 `CompiledStateGraph`(来自 `BuiltAgent.graph`)。T3 调它。

- [ ] **Step 1: 写失败测试**

`services/control-plane/tests/test_trigger_delivery.py`。构造真 graph(内存 checkpointer,无 Docker),照 `services/orchestrator/tests/test_agent_factory.py` 的 `_spec()`/`_secret_store()` 复制一个最小 react AgentSpec 与 secret store:

```python
from __future__ import annotations

from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from control_plane.transcript import read_turns
from control_plane.trigger_delivery import inject_delivery
from expert_work.runtime.checkpointer import make_checkpointer
from orchestrator.agent_factory import build_agent

# --- Minimal graph build. Replicate from orchestrator/tests/test_agent_factory.py:
# ---   _MINIMAL_SPEC, _spec(), _secret_store() (LocalDevSecretStore + the
# ---   _ANTHROPIC_KEY_NAME/_OPENAI_KEY_NAME/_KIMI_KEY_NAME constants), and
# ---   _platform_resolver (build_agent REQUIRES provider_key_resolver — Stream
# ---   Y-2 — or it raises). Keep them local to this test file.


async def _built_graph(cp):
    spec = _spec()  # a single react agent, no tools needed
    built = await build_agent(
        spec,
        secret_store=_secret_store(),
        checkpointer=cp,
        provider_key_resolver=_platform_resolver,  # required — build_agent raises without it
    )
    return built.graph


@pytest.mark.asyncio
async def test_delivery_appends_ai_message_visible_to_reader() -> None:
    tenant, thread = uuid4(), uuid4()
    async with make_checkpointer("memory") as cp:
        graph = await _built_graph(cp)
        config = {"configurable": {"thread_id": str(thread), "tenant_id": str(tenant)}}
        # seed a prior exchange so the thread has history
        await graph.aupdate_state(
            config,
            {"messages": [HumanMessage(content="set up my task"), AIMessage(content="done, it's scheduled")]},
            as_node="agent",
        )
        await inject_delivery(
            graph,
            thread_id=thread,
            tenant_id=tenant,
            result_text="Today's AI news: ...",
            source_run_id=uuid4(),
            trigger_id=uuid4(),
        )
        # the real user-facing read path surfaces it as the last assistant turn
        turns = await read_turns(cp, thread, include_hidden=False)
        assert turns[-1].role == "assistant"
        assert turns[-1].content == "Today's AI news: ..."
        # graph left in a clean turn-complete state (no pending node → next user
        # turn starts fresh, delivery didn't leave the graph mid-execution)
        snap = await graph.aget_state(config)
        assert snap.next == ()


@pytest.mark.asyncio
async def test_delivery_metadata_tags_source() -> None:
    tenant, thread, run_id, trig = uuid4(), uuid4(), uuid4(), uuid4()
    async with make_checkpointer("memory") as cp:
        graph = await _built_graph(cp)
        config = {"configurable": {"thread_id": str(thread), "tenant_id": str(tenant)}}
        await graph.aupdate_state(
            config, {"messages": [HumanMessage(content="hi"), AIMessage(content="ok")]}, as_node="agent"
        )
        await inject_delivery(
            graph, thread_id=thread, tenant_id=tenant, result_text="result",
            source_run_id=run_id, trigger_id=trig,
        )
        snap = await graph.aget_state(config)
        last = snap.values["messages"][-1]
        assert last.type == "ai"
        assert last.additional_kwargs["expert_work_scheduled_delivery"] is True
        assert last.additional_kwargs["expert_work_source_run_id"] == str(run_id)
        assert last.additional_kwargs["expert_work_trigger_id"] == str(trig)
        # NOT hidden from the UI
        assert "expert_work_hide_from_ui" not in last.additional_kwargs


@pytest.mark.asyncio
async def test_delivery_into_empty_thread() -> None:
    """No prior history → as_node='__start__' path still lands the message."""
    tenant, thread = uuid4(), uuid4()
    async with make_checkpointer("memory") as cp:
        graph = await _built_graph(cp)
        await inject_delivery(
            graph, thread_id=thread, tenant_id=tenant, result_text="standalone",
            source_run_id=uuid4(), trigger_id=uuid4(),
        )
        turns = await read_turns(cp, thread, include_hidden=False)
        assert any(t.role == "assistant" and t.content == "standalone" for t in turns)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && DOCKER_HOST= uv run --project services/control-plane pytest services/control-plane/tests/test_trigger_delivery.py -q`
Expected: FAIL(`ModuleNotFoundError: control_plane.trigger_delivery`)。

- [ ] **Step 3: 写实现**

`services/control-plane/src/control_plane/trigger_delivery.py`:

```python
"""Deliver a fired trigger's result back into the originating conversation.

Spec 1 PR3 (conversational scheduled tasks) — component D1. A scheduled task
runs in its own scratch thread; on success the scheduler's reconcile pass calls
:func:`inject_delivery` to append the run's result as an ``AIMessage`` into the
conversation the task was created from. Reuses LangGraph's ``aupdate_state``
(the same mechanism resume / plan-injection / sanitize use): it writes a new
checkpoint version with the message appended via the ``messages`` add-reducer —
no LLM turn, no history replay. The user sees it the next time they open the
conversation (the ``/messages`` endpoint reads the checkpoint directly).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph


async def inject_delivery(
    graph: CompiledStateGraph[Any, Any, Any, Any],
    *,
    thread_id: UUID,
    tenant_id: UUID,
    result_text: str,
    source_run_id: UUID,
    trigger_id: UUID,
) -> None:
    """Append ``result_text`` as an ``AIMessage`` to ``thread_id``'s checkpoint.

    The message is tagged (but NOT hidden) so the UI shows it and callers can
    trace it back to the firing. ``as_node`` follows the codebase convention
    (``"agent"`` when the thread already has history, ``"__start__"`` otherwise)
    so the graph is left in a clean turn-complete state.
    """
    config: RunnableConfig = {
        "configurable": {"thread_id": str(thread_id), "tenant_id": str(tenant_id)}
    }
    snapshot = await graph.aget_state(config)
    values = snapshot.values if isinstance(snapshot.values, dict) else {}
    has_history = bool(values.get("messages"))
    message = AIMessage(
        content=result_text,
        additional_kwargs={
            "expert_work_scheduled_delivery": True,
            "expert_work_source_run_id": str(source_run_id),
            "expert_work_trigger_id": str(trigger_id),
        },
    )
    await graph.aupdate_state(
        config,
        {"messages": [message]},
        as_node="agent" if has_history else "__start__",
    )
```

> **实现者注**:`CompiledStateGraph` 的 import 路径以本仓实际为准(可能是 `langgraph.graph.state import CompiledStateGraph`,与 `BuiltAgent.graph` 注解一致 —— 见 orchestrator/agent_factory.py 的 `graph: CompiledStateGraph[...]` import;照它)。若类型参数化报 mypy 错,与 `BuiltAgent` 处写法对齐。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && DOCKER_HOST= uv run --project services/control-plane pytest services/control-plane/tests/test_trigger_delivery.py -q`
Expected: PASS(3/3)。若 `snap.next == ()` 断言失败,说明 as_node/路由假设与真 graph 不符 —— 读 builder.py 的 agent 后条件边确认(无 tool_calls→END),据实调整 as_node 或断言,核心不变式:投递消息作为最后 assistant turn 可读 + 线程不卡在半执行态。

- [ ] **Step 5: lint + type**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && uv run ruff check services/control-plane/src/control_plane/trigger_delivery.py services/control-plane/tests/test_trigger_delivery.py && uv run ruff format --check services/control-plane/src/control_plane/trigger_delivery.py services/control-plane/tests/test_trigger_delivery.py && uv run --project services/control-plane mypy services/control-plane/src/control_plane/trigger_delivery.py`
Expected: clean(control-plane mypy 非 CI-gated,但求净;若 CompiledStateGraph 泛型报错照 BuiltAgent 处写法)。

- [ ] **Step 6: 提交**

```bash
cd /Users/mac/src/github/jone_qian/expert-work
git add services/control-plane/src/control_plane/trigger_delivery.py services/control-plane/tests/test_trigger_delivery.py
git commit -m "feat(triggers): inject_delivery —— aupdate_state 把任务结果 AIMessage 注入原对话 checkpoint"
```

---

## Task 3: reconcile 接线投递 + 生命周期审计

**Files:**
- Modify: `services/control-plane/src/control_plane/scheduler.py`(imports + `_reconcile_one` + 新 `_deliver`/`_emit_lifecycle`)
- Test: `services/control-plane/tests/test_scheduler.py`(扩,Docker 集成)

**Interfaces:**
- Consumes: `inject_delivery`(T2);`AuditAction.TRIGGER_COMPLETED/FAILED`(T1);`read_turns`(transcript.py);已有 `self._runs`/`self._triggers`/`self._agents`/`self._runtime`/`self._audit`。
- Produces: reconcile 在 run 成功且 `context_mode=reuse_thread`+有 originating_thread_id 时投递结果回原对话;每 run 成功发 `TRIGGER_COMPLETED`(details 含 delivery 状态),终态失败发 `TRIGGER_FAILED`。

- [ ] **Step 1: 写失败测试**(扩 `test_scheduler.py`;**全内存,无 Docker**)

`test_scheduler.py` 全内存:`_build_scheduler`(:75)建 InMemory* store + `stub_agent_runtime()`(该 stub **无** 真 `durable_checkpointer`/`get_agent`);`_MANIFEST`(:35)+ `AgentSpec.model_validate(_MANIFEST)` 是 "reporter/1.0.0" agent;`test_reconcile_marks_succeeded`(:251)是 reconcile 模型;`_run_info`(:105)建 RunInfo。三处 harness 需小扩(见实现者注)。加三测:

**新 import(加到 test_scheduler.py 顶部,与现有 import 合并)**:`from langchain_core.messages import AIMessage, HumanMessage`、`from control_plane.transcript import read_turns`、`from expert_work.runtime.checkpointer import make_checkpointer`、`from orchestrator.agent_factory import build_agent`、`from expert_work.persistence.audit_log import InMemoryAuditLogStore`、`from expert_work.protocol import AuditAction, AuditQuery`;并复制 `test_agent_factory.py` 的 `_MINIMAL_SPEC`/`_secret_store()`/`_platform_resolver`(见注③)。

```python
def _reuse_thread_trigger(*, originating_thread_id: UUID) -> TriggerRecord:
    return TriggerRecord(
        id=uuid4(), tenant_id=_TENANT, agent_name="reporter", agent_version="1.0.0",
        name="nightly", kind="cron",
        config={"rrule": "FREQ=DAILY;BYHOUR=3;BYMINUTE=0;BYSECOND=0", "timezone": "UTC", "seed_input": "go"},
        enabled=True, source="api",
        originating_thread_id=originating_thread_id, context_mode="reuse_thread",
        created_at=_BASE, updated_at=_BASE,
    )


@pytest.mark.asyncio
async def test_reconcile_delivers_result_to_originating_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """reuse_thread trigger + SUCCESS run → the run's final assistant reply is
    appended to the originating conversation + TRIGGER_COMPLETED audited."""
    orig_thread, scratch_thread, run_id = uuid4(), uuid4(), uuid4()
    audit = InMemoryAuditLogStore()
    async with make_checkpointer("memory") as cp:
        built = await build_agent(
            AgentSpec.model_validate(_MANIFEST),
            secret_store=_secret_store(),
            checkpointer=cp,
            provider_key_resolver=_platform_resolver,  # required (Stream Y-2)
        )
        # seed originating conversation (has history) + the run's scratch thread (its result)
        await built.graph.aupdate_state(
            {"configurable": {"thread_id": str(orig_thread), "tenant_id": str(_TENANT)}},
            {"messages": [HumanMessage(content="make me a task"), AIMessage(content="scheduled")]},
            as_node="agent",
        )
        await built.graph.aupdate_state(
            {"configurable": {"thread_id": str(scratch_thread), "tenant_id": str(_TENANT)}},
            {"messages": [HumanMessage(content="go"), AIMessage(content="Today's AI news: X")]},
            as_node="agent",
        )
        triggers, trigger_runs, run_store = (
            InMemoryTriggerStore(), InMemoryTriggerRunStore(), InMemoryRunStore(),
        )
        trig = _reuse_thread_trigger(originating_thread_id=orig_thread)
        await triggers.create(trig)
        fired = await trigger_runs.create(_fired_run(trigger_id=trig.id, run_id=run_id))
        await run_store.create(_run_info(run_id, status=RunStatus.SUCCESS, thread_id=scratch_thread))
        scheduler, runtime = await _build_scheduler(
            trigger_store=triggers, trigger_run_store=trigger_runs, run_store=run_store,
            audit_store=audit,  # new optional param — hold the handle for assertions
        )
        runtime.durable_checkpointer = cp

        async def _get_agent(**_kwargs: Any) -> Any:
            return built

        monkeypatch.setattr(runtime, "get_agent", _get_agent)

        await scheduler._reconcile_fired()

        turns = await read_turns(cp, orig_thread, include_hidden=False)
        assert turns[-1].role == "assistant" and turns[-1].content == "Today's AI news: X"
        row = await trigger_runs.get(trigger_run_id=fired.id, tenant_id=_TENANT)
        assert row is not None and row.status is TriggerRunStatus.SUCCEEDED
        page = await audit.query(AuditQuery(tenant_id=_TENANT, action=AuditAction.TRIGGER_COMPLETED))
        assert page.entries and page.entries[0].details.get("delivery") == "delivered"


@pytest.mark.asyncio
async def test_reconcile_fresh_thread_does_not_deliver() -> None:
    """Default context_mode (fresh_thread_per_run) → no delivery; still SUCCEEDED
    + TRIGGER_COMPLETED with delivery='skipped'. No graph/checkpointer needed —
    _deliver short-circuits on the context_mode check before touching either."""
    run_id = uuid4()
    audit = InMemoryAuditLogStore()
    triggers, trigger_runs, run_store = (
        InMemoryTriggerStore(), InMemoryTriggerRunStore(), InMemoryRunStore(),
    )
    trig = _trigger()  # default context_mode=fresh_thread_per_run, no originating_thread_id
    await triggers.create(trig)
    await trigger_runs.create(_fired_run(trigger_id=trig.id, run_id=run_id))
    await run_store.create(_run_info(run_id, status=RunStatus.SUCCESS))
    scheduler, _runtime = await _build_scheduler(
        trigger_store=triggers, trigger_run_store=trigger_runs, run_store=run_store,
        audit_store=audit,
    )
    await scheduler._reconcile_fired()
    page = await audit.query(AuditQuery(tenant_id=_TENANT, action=AuditAction.TRIGGER_COMPLETED))
    assert page.entries and page.entries[0].details.get("delivery") == "skipped"


@pytest.mark.asyncio
async def test_reconcile_interrupted_emits_trigger_failed() -> None:
    """A terminal (INTERRUPTED→FAILED) firing emits TRIGGER_FAILED."""
    run_id = uuid4()
    audit = InMemoryAuditLogStore()
    triggers, trigger_runs, run_store = (
        InMemoryTriggerStore(), InMemoryTriggerRunStore(), InMemoryRunStore(),
    )
    trig = _trigger()
    await triggers.create(trig)
    await trigger_runs.create(_fired_run(trigger_id=trig.id, run_id=run_id))
    await run_store.create(_run_info(run_id, status=RunStatus.INTERRUPTED))
    scheduler, _runtime = await _build_scheduler(
        trigger_store=triggers, trigger_run_store=trigger_runs, run_store=run_store,
        audit_store=audit,
    )
    await scheduler._reconcile_fired()
    page = await audit.query(AuditQuery(tenant_id=_TENANT, action=AuditAction.TRIGGER_FAILED))
    assert page.entries
```

> **实现者注 — harness 小扩(全部向后兼容,零现有调用点 churn)**:
> ① **`_build_scheduler` 加可选 `audit_store` 参**:签名加 `audit_store: InMemoryAuditLogStore | None = None`;函数内 `store = audit_store or InMemoryAuditLogStore()`,`audit_logger=build_default_audit_logger(store)`。**返回签名不变(仍 `(scheduler, runtime)`)** → 现有 16 个调用点一个都不用改;新测传入自己的 `audit` 以持句柄断言。audit 读用 `audit.query(AuditQuery(tenant_id=_TENANT, action=...))` → `AuditPage.entries`(**已核** memory.py:46 `query`,AuditQuery 字段 tenant_id/action,AuditPage.entries)。
> ② **`_run_info` 加 `thread_id` 参**:现签名 `_run_info(run_id, *, status, error=None)`(:105,恒 `thread_id=uuid4()`)→ 加 `thread_id: UUID | None = None`,`thread_id=thread_id or uuid4()`。现有调用点省略即默认,零 churn。用现成 `_fired_run(*, trigger_id, run_id)`(:121)建 FIRED trigger_run、`run_store.create(_run_info(...))` 播种 run(**照** `test_reconcile_marks_succeeded`:251 路数)。
> ③ **build harness**:复制 `test_agent_factory.py` 的 `_secret_store()`(`LocalDevSecretStore` + 三个键名常量)**和** `_platform_resolver`(`build_agent` 必需 `provider_key_resolver`,缺则 raise)到本文件。`_MANIFEST`/`AgentSpec.model_validate(_MANIFEST)` 已在本文件(:35)。
> ④ **seeded agent 须 ACTIVE**:`_build_scheduler(seed_agent=True)` 往 `InMemoryAgentSpecStore` 建 "reporter/1.0.0";`_deliver` 查 `spec_record.status is AgentSpecStatus.ACTIVE`。确认 `InMemoryAgentSpecStore.create` 默认建 ACTIVE(否则 delivery 返 "agent_unavailable" 而非 "delivered",第一个测会挂 —— 那就在测里把它激活或确认默认态)。
> `build_agent` 仅编译 graph 不调 LLM。`_get_agent` monkeypatch 让 reconcile 的 `runtime.get_agent` 返真 built(stub runtime 无此)。全程内存,`DOCKER_HOST=` 空。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && DOCKER_HOST= uv run --project services/control-plane pytest services/control-plane/tests/test_scheduler.py -q -k "deliver or fresh_thread or interrupted"`
Expected: FAIL(投递未接线 / 无 TRIGGER_COMPLETED/FAILED)。

- [ ] **Step 3: 加 imports**

`scheduler.py` 顶部 import 段,加:

```python
from control_plane.audit import emit
from control_plane.transcript import read_turns
from control_plane.trigger_delivery import inject_delivery
from expert_work.protocol import AuditAction
from expert_work.protocol.agent_spec import AgentSpecStatus
```

> **实现者注**:`AuditAction`/`AgentSpecStatus` 的确切 import 源以真实存在为准(`AgentSpecStatus` 见 trigger_firing.py 的 import;`AuditAction` 见 trigger_firing.py `from ... import AuditAction`,照它同源)。`emit` 与 trigger_firing.py:31 同源(`from control_plane.audit import emit`)。

- [ ] **Step 4: 加 `_deliver` + `_emit_lifecycle` 助手**

在 `Scheduler` 类里(`_after_failure` 附近)加:

```python
    async def _emit_lifecycle(
        self, row: TriggerRunRecord, *, action: AuditAction, details: dict[str, object]
    ) -> None:
        """Best-effort trigger lifecycle audit — never let audit failure break
        reconcile."""
        try:
            await emit(
                self._audit,
                tenant_id=row.tenant_id,
                actor_id=f"trigger:{row.trigger_id}",
                action=action,
                resource_type="trigger",
                resource_id=str(row.trigger_id),
                details=details,
            )
        except Exception:
            logger.exception(
                "scheduler.audit_emit_failed", extra={"trigger_run_id": str(row.id)}
            )

    async def _deliver(self, row: TriggerRunRecord, run: RunInfo) -> str:
        """Deliver a successful run's result into its originating conversation.

        Best-effort: returns a short status for the TRIGGER_COMPLETED audit and
        never raises (a delivery failure must not block the SUCCEEDED
        transition). Only conversation-created tasks (context_mode=reuse_thread
        with an originating_thread_id) deliver; background tasks skip.
        """
        try:
            trigger = await self._triggers.get(
                trigger_id=row.trigger_id, tenant_id=row.tenant_id
            )
            if (
                trigger is None
                or trigger.context_mode != "reuse_thread"
                or trigger.originating_thread_id is None
            ):
                return "skipped"
            checkpointer = self._runtime.durable_checkpointer
            if checkpointer is None:
                return "no_checkpointer"
            turns = await read_turns(checkpointer, run.thread_id, include_hidden=False)
            result = next(
                (t.content for t in reversed(turns) if t.role == "assistant"), None
            )
            if not result:
                return "no_output"
            spec_record = await self._agents.get(
                tenant_id=trigger.tenant_id,
                name=trigger.agent_name,
                version=trigger.agent_version,
            )
            if spec_record is None or spec_record.status is not AgentSpecStatus.ACTIVE:
                return "agent_unavailable"
            built = await self._runtime.get_agent(
                tenant_id=trigger.tenant_id,
                name=trigger.agent_name,
                version=trigger.agent_version,
                spec=spec_record.spec,
            )
            await inject_delivery(
                built.graph,
                thread_id=trigger.originating_thread_id,
                tenant_id=trigger.tenant_id,
                result_text=result,
                source_run_id=run.run_id,
                trigger_id=trigger.id,
            )
            return "delivered"
        except Exception:
            logger.exception(
                "scheduler.delivery_failed", extra={"trigger_run_id": str(row.id)}
            )
            return "error"
```

> **实现者注**:`RunInfo` 已随 `self._runs.get` 返回;确认 `RunInfo` 的 import 在 scheduler(若未 import,加 `from expert_work.runtime.runs import RunInfo` 或与 `RunStatus`/`RunStore` 同行)。`_reconcile_one` 全程已在 `with _tenant_scope(row.tenant_id)` 内,`_deliver` 在其中调用,scope 已就绪。

- [ ] **Step 5: 接线 `_reconcile_one`**

把现有 `_reconcile_one`(Explore 证 :339-356)的分支改为投递 + 审计:

```python
    async def _reconcile_one(self, row: TriggerRunRecord, *, now: datetime) -> None:
        if row.run_id is None:
            return
        with _tenant_scope(row.tenant_id):
            run = await self._runs.get(run_id=row.run_id, tenant_id=row.tenant_id)
            if run is None:
                return
            if run.status is RunStatus.SUCCESS:
                delivery = await self._deliver(row, run)
                await self._trigger_runs.update(
                    row.model_copy(update={"status": TriggerRunStatus.SUCCEEDED})
                )
                await self._emit_lifecycle(
                    row,
                    action=AuditAction.TRIGGER_COMPLETED,
                    details={"run_id": str(row.run_id), "delivery": delivery},
                )
            elif run.status in _FAILED_RUN_STATUSES:
                new = self._after_failure(row, now=now, error=run.error)
                await self._trigger_runs.update(new)
                if new.status is TriggerRunStatus.DEAD_LETTER:
                    await self._emit_lifecycle(
                        row,
                        action=AuditAction.TRIGGER_FAILED,
                        details={"run_id": str(row.run_id), "error": run.error},
                    )
            elif run.status is RunStatus.INTERRUPTED:
                await self._trigger_runs.update(
                    row.model_copy(
                        update={
                            "status": TriggerRunStatus.FAILED,
                            "error": "run interrupted",
                        }
                    )
                )
                await self._emit_lifecycle(
                    row,
                    action=AuditAction.TRIGGER_FAILED,
                    details={"run_id": str(row.run_id), "error": "run interrupted"},
                )
            # PAUSED / RUNNING / PENDING — not terminal; reconcile next sweep.
```

(投递在转 SUCCEEDED **前**跑,但 best-effort 不抛 → 转态与审计恒发。`TRIGGER_FAILED` 仅 DEAD_LETTER + INTERRUPTED→FAILED 终态发,RETRYING 不发。)

- [ ] **Step 6: 跑测试确认通过**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && DOCKER_HOST= uv run --project services/control-plane pytest services/control-plane/tests/test_scheduler.py -q`
Expected: PASS(新三测 + 现有 reconcile/DLQ 测不回归 —— `_build_scheduler` 加可选 `audit_store` 参、返回签名不变,现有 16 调用点无需改)。

- [ ] **Step 7: lint + type + 回归**

Run:
```bash
cd /Users/mac/src/github/jone_qian/expert-work
uv run ruff check services/control-plane/src/control_plane/scheduler.py services/control-plane/tests/test_scheduler.py
uv run ruff format --check services/control-plane/src/control_plane/scheduler.py services/control-plane/tests/test_scheduler.py
uv run --project services/control-plane mypy services/control-plane/src/control_plane/scheduler.py
DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock uv run --project services/control-plane pytest services/control-plane/tests/test_scheduler.py services/control-plane/tests/test_trigger_firing.py -q
```
Expected: clean / 全绿。

- [ ] **Step 8: 提交**

```bash
cd /Users/mac/src/github/jone_qian/expert-work
git add services/control-plane/src/control_plane/scheduler.py services/control-plane/tests/test_scheduler.py
git commit -m "feat(triggers): reconcile 投递结果回原对话 + TRIGGER_COMPLETED/FAILED 审计(PR3 D1)"
```

---

## 收尾(全 3 task 后)

- [ ] control-plane 受影响测:`cd /Users/mac/src/github/jone_qian/expert-work && DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock uv run --project services/control-plane pytest services/control-plane/tests/test_scheduler.py services/control-plane/tests/test_trigger_firing.py services/control-plane/tests/test_trigger_delivery.py -q`
- [ ] protocol 测:`DOCKER_HOST= uv run pytest packages/expert-work-protocol/tests/test_audit_actions.py -q`
- [ ] 全库 lint:`uv run ruff check . && uv run ruff format --check .`
- [ ] 无署名核验:`git log main..HEAD --format='%an %ae%n%b' | grep -iE 'co-authored|claude|🤖'` 应无输出。
- [ ] opus 全分支终审。

---

## Self-Review(对照 spec §5)

- **§5.1 触发 run 不变**(独立 scratch thread)→ 本 PR 不碰 `fire_trigger` 的 run 构建,只在 reconcile 加投递。✅
- **§5.2 投递步(reconcile 扩展)**:条件 `context_mode==reuse_thread` + `originating_thread_id` 非空 + run 成功 → 取 run 最终 assistant 输出 → `aupdate_state` 注入原对话 checkpoint（无 LLM 轮）→ 标「定时投递」+ 携带源 run_id。→ T2 `inject_delivery` + T3 `_deliver`。✅ `aupdate_state` 接入方式(reconcile 侧取原对话 Agent graph)= `self._agents.get`→`self._runtime.get_agent`→`built.graph`,已定。
- **§5.3 失败与边界**:run 失败 → 照旧 retrying/dead_letter 不投递失败(T3 分支保留 `_after_failure`);`fresh_thread_per_run` → 不投递(`_deliver` 返 "skipped");投递并发 = append 一条 message 行(reducer 追加,交错可接受)。→ T3 + Global Constraints。✅
- **§3.5 生命周期**:补 `TRIGGER_COMPLETED`/`TRIGGER_FAILED`(reconcile 发)→ T1 枚举 + T3 emit。✅
- **不做**:实时推送 = Spec 2 D2;用户面失败通知 = Spec 2;内容搜索索引投递消息 = 文档化限制留 follow-up。✅

> **明确不在 PR3**:实时推送(客户端长连接)属 Spec 2;PR4 调试台端到端演练(对话建任务→立即触发→看结果落回对话)建在本 PR 之上。
