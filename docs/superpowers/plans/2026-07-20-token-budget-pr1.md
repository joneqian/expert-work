# B3 PR1 token 熔断全链 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** per-run 全委托树共享的 token 熔断(80% 预警→超限优雅收尾)+ 三闸触发 guard marker 可见化 + 配置页第 7 旋钮。

**Architecture:** `PolicySpec.token_budget`(0=关)→ `BuiltAgent` 派生 → `run_agent` 创建共享 `TokenBudget` 对象注入 configurable 并经 `ToolContext`/`_child_config` 下传全树;agent_node 每步累计 `usage_metadata`,超限并入现有 `budget_exhausted` 优雅收尾,80% 首跨发 guard warning 帧 + 每步 prompt 附注;token/max_steps/no_progress 超限各发 guard tripped 帧(compaction sink 同款发射,持久化回放同源);前端 MarkerItem 新 kind 渲染。

**Tech Stack:** Python 3.12 / pydantic / LangGraph / pytest;React + TS / vitest。

**Spec:** `docs/superpowers/specs/2026-07-20-token-budget-breaker-design.md`(权威;冲突以 spec 为准)。

## Global Constraints

- 口径精确:每步累计 = `usage_metadata` 的 `input_tokens + output_tokens + input_token_details.cache_creation + input_token_details.cache_read`(与 `TokenUsageMiddleware._extract_token_counts` 同源);cache hit 同计。
- `WARN_PCT = 0.8`;warning 帧**首跨发一条**(全树一次,flag 挂共享对象);tripped 帧每个触发实例各发。
- guard 帧格式精确:`{"kind": "warning"|"tripped", "guard": "token_budget"|"max_steps"|"no_progress", "detail": {...}}`;detail:token=`{spent, limit}`、max_steps=`{steps, max}`、no_progress=`{streak, max}`;SSE event 名 `"guard"`。
- 行为等价红线:limit=0 / 未注入 = 全链零行为变化;现有 max_steps/no_progress/graph 测试原样通过。
- seq 竞态红线:`_publish_guard` 在任何 await 前同步分配 seq(`_publish_worker` 同款)。
- 收尾轮自身不再触发第二次收尾(现有 budget_exhausted 机制天然如此,勿破坏)。
- 预算注/收尾指令只进 prompt(ephemeral,`_MAX_STEPS_WRAPUP_INSTRUCTION` 同款 `messages = [*messages, HumanMessage(...)]`),不碰 system 前缀,不持久化进 state。
- 分层红线:`orchestrator.tools` 不得 import `orchestrator.graph_builder`;新 keys/对象放 `tools/_guards.py` 叶子模块(`_worker_events.py` 先例)。
- BuiltAgent 加字段必同步 control-plane 4 个 SimpleNamespace 桩(#1023 教训):`test_run_queue_worker.py`、`test_orphan_sweep.py`、`test_resume_idempotency_flow.py`、`test_approval_timeout_sweep.py`。
- 前端 marker 文案:硬编码中文(`timeline.ts` 既有惯例,压缩/重试同款),**不加 i18n**;运行预算组旋钮文案走 i18n `run_budget.token_budget_*` 四键三处。
- 测试命令:`uv run pytest`;CI mypy 精确命令(Task 6);ruff 全库;admin-ui `pnpm exec vitest run` + `pnpm typecheck`。
- 提交 `feat:`/`test:`,无 attribution。

---

## File Structure

| 文件 | 职责 |
|---|---|
| Modify `packages/expert-work-protocol/src/expert_work/protocol/agent_spec.py`(PolicySpec :818 区) | `token_budget` 字段 |
| Modify `services/orchestrator/src/orchestrator/agent_factory.py` | BuiltAgent 字段 + 构造 |
| Create `services/orchestrator/src/orchestrator/tools/_guards.py` | `TokenBudget` + `TOKEN_BUDGET_KEY`/`GUARD_SINK_KEY`/`GuardSink` + `build_guard_frame` + `usage_total` |
| Modify `services/orchestrator/src/orchestrator/tools/registry.py` | ToolContext 加 `token_budget`/`guard_sink` |
| Modify `services/orchestrator/src/orchestrator/tools/_child_run.py`(`_child_config`) | 两 key 下传 |
| Modify `services/orchestrator/src/orchestrator/graph_builder/builder.py` | 累计/预警/超限/guard 发射 |
| Modify `services/orchestrator/src/orchestrator/sse.py` | run_agent 参数 + 建对象 + `_publish_guard` + 注入 |
| Modify 5 call sites + 4 control-plane 测试桩 | `token_budget=built.token_budget` |
| Modify `apps/admin-ui/.../groups/RunBudgetSection.tsx` + `form_model.ts` + locales | 第 7 旋钮 |
| Modify `apps/admin-ui/src/api/timeline.ts` + `.../StepTimeline.tsx` | guard marker |
| Test Create `services/orchestrator/tests/test_guards.py`、`test_token_budget_graph.py`、`test_sse_guard_events.py`;Modify 前端对应测试 | |

---

### Task 1: schema + BuiltAgent 派生

**Files:**
- Modify: `packages/expert-work-protocol/src/expert_work/protocol/agent_spec.py`(PolicySpec,`run_deadline_s` :857 之后)
- Modify: `services/orchestrator/src/orchestrator/agent_factory.py`(BuiltAgent dataclass 字段区 + `BuiltAgent(...)` 构造 ~:1056,`trajectory_recording` 旁)
- Test: 追加到 protocol 的 spec round-trip 测试文件(先 `grep -rln "run_deadline_s" packages/expert-work-protocol/tests` 找到 PolicySpec 字段测试所在文件,照其惯例)+ `services/orchestrator/tests/test_agent_factory.py`

**Interfaces:**
- Produces: `PolicySpec.token_budget: int`(default 0, ge=0);`BuiltAgent.token_budget: int = 0`。

- [ ] **Step 1: 写失败测试**

protocol 测试(照所在文件既有 round-trip 惯例适配):

```python
def test_token_budget_round_trips() -> None:
    spec = _minimal_spec_with(policies={"token_budget": 500_000})
    assert spec.spec.policies.token_budget == 500_000
    dumped = spec.model_dump(by_alias=True, exclude_none=True)
    assert dumped["spec"]["policies"]["token_budget"] == 500_000


def test_token_budget_defaults_zero_and_rejects_negative() -> None:
    assert _minimal_spec().spec.policies.token_budget == 0
    with pytest.raises(ValidationError):
        _minimal_spec_with(policies={"token_budget": -1})
```

test_agent_factory.py(照文件内 build 惯例,`trajectory_recording` 既有测试旁):

```python
def test_built_agent_carries_token_budget() -> None:
    built = _build_with_policies({"token_budget": 123_456})
    assert built.token_budget == 123_456


def test_built_agent_token_budget_defaults_zero() -> None:
    assert _build_minimal().token_budget == 0
```

(`_minimal_spec*` / `_build_*` = 各文件现成 helper 的占位名,用文件内实际 helper。)

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest packages/expert-work-protocol/tests services/orchestrator/tests/test_agent_factory.py -q -k token_budget`
Expected: FAIL(字段不存在)

- [ ] **Step 3: 实现**

`agent_spec.py` PolicySpec(`run_deadline_s` 字段后):

```python
    token_budget: int = Field(
        default=0,
        ge=0,
        description=(
            "Per-run total token cap across the whole delegation tree (main "
            "agent + static sub-agents + dynamic workers). Counts input + "
            "output + cache_creation + cache_read from each main-loop LLM "
            "call. 0 disables the breaker."
        ),
    )
```

`agent_factory.py` BuiltAgent 字段(`trajectory_recording` 旁,注释风格照它):

```python
    #: B3 — per-run token breaker limit (``policies.token_budget``). Callers
    #: pass it to ``sse.run_agent(token_budget=...)``; 0 disables (no budget
    #: object is created, zero behaviour change).
    token_budget: int = 0
```

构造处(`trajectory_recording=spec.spec.policies.trajectory_recording,` 旁):

```python
        token_budget=spec.spec.policies.token_budget,
```

- [ ] **Step 4: 跑测试确认通过**

Run: 同 Step 2 命令。Expected: PASS。再跑 `uv run pytest services/orchestrator/tests/test_agent_factory.py -q` 全绿。

- [ ] **Step 5: Commit**

```bash
git add packages/expert-work-protocol services/orchestrator/src/orchestrator/agent_factory.py services/orchestrator/tests/test_agent_factory.py
git commit -m "feat: B3 policies.token_budget schema 字段 + BuiltAgent 派生"
```

---

### Task 2: `tools/_guards.py` 叶子模块

**Files:**
- Create: `services/orchestrator/src/orchestrator/tools/_guards.py`
- Test: `services/orchestrator/tests/test_guards.py`

**Interfaces:**
- Produces(精确,后续任务消费):

```python
TOKEN_BUDGET_KEY = "token_budget"
GUARD_SINK_KEY = "guard_event_sink"
GuardSink = Callable[[dict[str, Any]], Awaitable[None]]

@dataclass
class TokenBudget:
    limit: int
    spent: int = 0
    warned: bool = False          # warning 帧已发(全树一次,挂共享对象)
    WARN_PCT: ClassVar[float] = 0.8
    def add(self, n: int) -> None
    @property
    def exhausted(self) -> bool   # spent >= limit
    @property
    def warning(self) -> bool     # spent >= limit * WARN_PCT
    @property
    def remaining(self) -> int    # max(0, limit - spent)

def usage_total(usage_metadata: Mapping[str, Any] | None) -> int
def build_guard_frame(*, kind: str, guard: str, detail: Mapping[str, Any]) -> dict[str, Any]
async def emit_guard_frame(sink: GuardSink | None, frame: dict[str, Any]) -> None  # best-effort 吞异常
```

- [ ] **Step 1: 写失败测试**

```python
"""B3 — TokenBudget / guard 帧纯函数单测."""

from __future__ import annotations

import json
from typing import Any

import pytest

from orchestrator.tools._guards import (
    TokenBudget,
    build_guard_frame,
    emit_guard_frame,
    usage_total,
)


def test_token_budget_thresholds() -> None:
    tb = TokenBudget(limit=1000)
    tb.add(799)
    assert not tb.warning and not tb.exhausted
    tb.add(1)  # 800 = 恰好 80%
    assert tb.warning and not tb.exhausted
    tb.add(199)
    assert not tb.exhausted
    tb.add(1)  # 1000 = 恰好 limit
    assert tb.exhausted
    assert tb.remaining == 0


def test_token_budget_remaining() -> None:
    tb = TokenBudget(limit=100)
    tb.add(30)
    assert tb.remaining == 70
    tb.add(200)
    assert tb.remaining == 0  # 不为负


def test_usage_total_sums_four_parts() -> None:
    assert (
        usage_total(
            {
                "input_tokens": 10,
                "output_tokens": 5,
                "input_token_details": {"cache_creation": 3, "cache_read": 2},
            }
        )
        == 20
    )


def test_usage_total_defensive() -> None:
    assert usage_total(None) == 0
    assert usage_total({}) == 0
    assert usage_total({"input_tokens": "bad", "output_tokens": 4}) == 4


def test_build_guard_frame_shape_json_safe() -> None:
    frame = build_guard_frame(
        kind="tripped", guard="token_budget", detail={"spent": 503_000, "limit": 500_000}
    )
    assert frame == {
        "kind": "tripped",
        "guard": "token_budget",
        "detail": {"spent": 503_000, "limit": 500_000},
    }
    json.dumps(frame)


@pytest.mark.asyncio
async def test_emit_guard_frame_best_effort() -> None:
    async def _boom(frame: dict[str, Any]) -> None:
        raise RuntimeError("down")

    await emit_guard_frame(_boom, {"kind": "warning"})  # 不抛
    await emit_guard_frame(None, {"kind": "warning"})  # 无 sink 零动作
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest services/orchestrator/tests/test_guards.py -q`
Expected: FAIL — 模块不存在

- [ ] **Step 3: 实现**

```python
"""B3 token 熔断 — 共享预算对象 + guard 帧契约(spec:
docs/superpowers/specs/2026-07-20-token-budget-breaker-design.md)。

keys 定义在 tools 层(``_worker_events.WORKER_EVENT_SINK_KEY`` 同款理由):
``orchestrator.tools`` 是 ``graph_builder`` 的下层,反向 import 成包环。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

logger = logging.getLogger(__name__)

#: config["configurable"] key — run_agent 注入的全树共享 TokenBudget。
TOKEN_BUDGET_KEY = "token_budget"  # noqa: S105 — config key, not a credential
#: config["configurable"] key — guard marker 帧 sink(compaction sink 同款)。
GUARD_SINK_KEY = "guard_event_sink"  # noqa: S105

GuardSink = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class TokenBudget:
    """全委托树共扣的 token 池 — 单事件循环,无锁。"""

    limit: int
    spent: int = 0
    #: warning 帧已发(挂共享对象 → 全树只发一次)。
    warned: bool = False

    WARN_PCT: ClassVar[float] = 0.8

    def add(self, n: int) -> None:
        self.spent += n

    @property
    def exhausted(self) -> bool:
        return self.spent >= self.limit

    @property
    def warning(self) -> bool:
        return self.spent >= self.limit * self.WARN_PCT

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.spent)


def usage_total(usage_metadata: Mapping[str, Any] | None) -> int:
    """input + output + cache_creation + cache_read(与 TokenUsageMiddleware
    的抽取同源口径);形状异常按 0 计,绝不抛。"""
    if not isinstance(usage_metadata, Mapping):
        return 0
    total = 0
    for key in ("input_tokens", "output_tokens"):
        v = usage_metadata.get(key)
        if isinstance(v, int):
            total += v
    details = usage_metadata.get("input_token_details")
    if isinstance(details, Mapping):
        for key in ("cache_creation", "cache_read"):
            v = details.get(key)
            if isinstance(v, int):
                total += v
    return total


def build_guard_frame(*, kind: str, guard: str, detail: Mapping[str, Any]) -> dict[str, Any]:
    return {"kind": kind, "guard": guard, "detail": dict(detail)}


async def emit_guard_frame(sink: GuardSink | None, frame: dict[str, Any]) -> None:
    """Best-effort — guard 可见化故障绝不影响 run 本体."""
    if sink is None:
        return
    try:
        await sink(frame)
    except Exception as exc:
        logger.warning(
            "guards.frame_failed guard=%s err=%s", frame.get("guard", "?"), type(exc).__name__
        )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest services/orchestrator/tests/test_guards.py -q`。Expected: PASS(6)。

- [ ] **Step 5: Commit**

```bash
git add services/orchestrator/src/orchestrator/tools/_guards.py services/orchestrator/tests/test_guards.py
git commit -m "feat: B3 TokenBudget 共享预算对象 + guard 帧契约(_guards.py)"
```

---

### Task 3: builder 接线(累计/预警/超限/发射)+ ToolContext 下传

**Files:**
- Modify: `services/orchestrator/src/orchestrator/graph_builder/builder.py`(:183 指令区、:528 判定区、:785-789 收尾区、:930-975 usage 区、`_tool_context` :2546 区)
- Modify: `services/orchestrator/src/orchestrator/tools/registry.py`(ToolContext)
- Modify: `services/orchestrator/src/orchestrator/tools/_child_run.py`(`_child_config`)
- Test: `services/orchestrator/tests/test_token_budget_graph.py`(新;harness 照 `test_no_progress_stop.py` / `test_react_graph.py` 惯例)

**Interfaces:**
- Consumes: Task 2 全部。
- Produces: agent_node 在 config 带 `TOKEN_BUDGET_KEY`/`GUARD_SINK_KEY` 时:每步累计、80% 首跨发 warning 帧+此后每步 prompt 附注、超限发 tripped 帧+token 版收尾;max_steps/no_progress 超限也发 tripped 帧;`ToolContext.token_budget`/`guard_sink` + `_child_config` 下传。

- [ ] **Step 1: 写失败测试(新文件;`_FakeLLM`/graph build 惯例照 `test_no_progress_stop.py`,fake LLM 的 AIMessage 带 `usage_metadata`)**

覆盖(每条独立测试,断言语义如下,fixture 形状适配现场 harness):

```python
# 1) 累计+超限:limit=100,fake LLM 每步 usage 60(带 tool_calls)。
#    第 1 步后 spent=60;第 2 步后 120 → 第 3 步进收尾:
#    - 收尾轮 prompt 含 "token budget"(_TOKEN_BUDGET_WRAPUP_INSTRUCTION 措辞)
#    - 收尾轮 bind 无工具、响应无 tool_calls → END,最终 state 正常
#    - guard sink 收到 {"kind":"tripped","guard":"token_budget","detail":{"spent":120,"limit":100}}
# 2) 80% 预警:limit=1000,每步 usage 300。第 3 步后 spent=900 跨 80% →
#    - guard sink 恰好一条 {"kind":"warning","guard":"token_budget",...}(再跑一步不重发)
#    - 跨后下一步的 prompt 最后一条消息含预算附注(已用/上限),system 前缀原样
# 3) 0=off:不注入 budget → sink 零帧、行为与现状 identical(复用 harness 正常跑通断言)
# 4) max_steps tripped:max_steps=2 跑满 → sink 收 {"kind":"tripped","guard":"max_steps","detail":{"steps":2,"max":2}};
#    no_progress 同款(照 test_no_progress_stop.py 触发法)→ guard="no_progress"
# 5) 收尾轮不二次触发:超限收尾后 graph END,sink 恰好一条 token tripped
# 6) sink 抛异常:run 照常完成
# 7) ToolContext/_child_config:构造 config 带两 key → _tool_context 读出;
#    _child_config(ctx) 的 configurable 含同一对象(is 同一引用)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest services/orchestrator/tests/test_token_budget_graph.py -q`
Expected: FAIL(key 无消费、无帧、无附注)

- [ ] **Step 3: 实现**

`builder.py` 指令区(`_MAX_STEPS_WRAPUP_INSTRUCTION` 后):

```python
#: B3 — token 预算耗尽的收尾指令(措辞镜像步数版,原因改为 token)。
_TOKEN_BUDGET_WRAPUP_INSTRUCTION = (
    "You have reached this task's token budget and can no longer call any tools. "
    "Using everything you have already gathered, produced, or written so far, "
    "write your best, complete final answer to the user's request now. "
    "Do not ask to continue and do not attempt to call any tools."
)
```

判定区(:528 `budget_exhausted = ...` 处):

```python
        # B3 — 全树共享 token 池。对象/sink 缺失(limit=0 或未注入)全为
        # None → 行为与引入前逐字节一致。
        token_budget = configurable.get(TOKEN_BUDGET_KEY)
        token_budget = token_budget if isinstance(token_budget, TokenBudget) else None
        guard_sink_raw = configurable.get(GUARD_SINK_KEY)
        guard_sink = guard_sink_raw if callable(guard_sink_raw) else None
        token_tripped = token_budget is not None and token_budget.exhausted
        budget_exhausted = (max_steps > 0 and step_count >= max_steps) or stuck or token_tripped
```

(agent_node 内 `configurable` 的现有取法照文件现场;import 追加 `from orchestrator.tools._guards import GUARD_SINK_KEY, TOKEN_BUDGET_KEY, TokenBudget, build_guard_frame, emit_guard_frame, usage_total`。)

收尾区(:785-789 `if budget_exhausted:` 块改造):

```python
        if budget_exhausted:
            tools = []
            cache_hit_response = None
            wrapup = (
                _TOKEN_BUDGET_WRAPUP_INSTRUCTION if token_tripped else _MAX_STEPS_WRAPUP_INSTRUCTION
            )
            messages = [*messages, HumanMessage(content=wrapup)]
            # B3 — guard 可见化:每个触发的闸各发一条 tripped 帧(老盲区一起治)。
            if token_tripped and token_budget is not None:
                await emit_guard_frame(
                    guard_sink,
                    build_guard_frame(
                        kind="tripped",
                        guard="token_budget",
                        detail={"spent": token_budget.spent, "limit": token_budget.limit},
                    ),
                )
                _token_budget_exhausted_total.inc()
            if max_steps > 0 and step_count >= max_steps:
                await emit_guard_frame(
                    guard_sink,
                    build_guard_frame(
                        kind="tripped", guard="max_steps",
                        detail={"steps": step_count, "max": max_steps},
                    ),
                )
            if stuck:
                await emit_guard_frame(
                    guard_sink,
                    build_guard_frame(
                        kind="tripped", guard="no_progress",
                        detail={"streak": no_progress_streak, "max": max_no_progress},
                    ),
                )
            logger.warning("agent.budget_graceful_wrapup step=%d max=%d", step_count, max_steps)
        elif token_budget is not None and token_budget.warning:
            # B3 — 80% 预警:首跨发一条 warning 帧(flag 挂共享对象,全树一次);
            # 此后每步 prompt 附预算注(ephemeral,不持久化,不碰 system 前缀)。
            if not token_budget.warned:
                token_budget.warned = True
                await emit_guard_frame(
                    guard_sink,
                    build_guard_frame(
                        kind="warning", guard="token_budget",
                        detail={"spent": token_budget.spent, "limit": token_budget.limit},
                    ),
                )
                logger.info(
                    "agent.token_budget_warning spent=%d limit=%d",
                    token_budget.spent, token_budget.limit,
                )
            messages = [
                *messages,
                HumanMessage(
                    content=(
                        f"[token budget notice] This run has used {token_budget.spent} of its "
                        f"{token_budget.limit} token budget ({token_budget.remaining} remaining). "
                        "Converge quickly: prefer finishing with what you have over further "
                        "tool exploration."
                    )
                ),
            ]
```

metric(模块级 counter 区,照文件现有 Counter 声明惯例):

```python
_token_budget_exhausted_total = Counter(
    "expert_work_token_budget_exhausted_total",
    "Runs (or workers) that hit the per-run token budget and wrapped up.",
)
```

usage 累计(response 定型后、after-chain 区之前,~:930):

```python
        # B3 — 每步累计(cache hit 同计,与 token_usage 行为一致)。
        if token_budget is not None:
            token_budget.add(usage_total(getattr(response, "usage_metadata", None)))
```

`registry.py` ToolContext(`worker_event_sink` 旁,import 补 `TokenBudget` 类型可用 `Any` 规避层次?不必——`_guards` 在 tools 层,registry 同层可直接 import):

```python
    #: B3 — 全树共享 token 池(run_agent 注入,None=未启用)。
    token_budget: TokenBudget | None = None
    #: B3 — guard marker 帧 sink(下传给子树,None=未接线)。
    guard_sink: Callable[[dict[str, Any]], Awaitable[None]] | None = None
```

`builder.py` `_tool_context`(:2546 区,worker_event_sink 读取旁):

```python
    tb_raw = configurable.get(TOKEN_BUDGET_KEY)
    guard_raw = configurable.get(GUARD_SINK_KEY)
    return ToolContext(
        ...,  # 现有字段原样
        token_budget=tb_raw if isinstance(tb_raw, TokenBudget) else None,
        guard_sink=guard_raw if callable(guard_raw) else None,
    )
```

`_child_run.py` `_child_config`(worker sink 下传旁):

```python
    # B3 — token 池 + guard sink 下传:全树共扣一个额度,guard 帧直达父流。
    if ctx.token_budget is not None:
        configurable[TOKEN_BUDGET_KEY] = ctx.token_budget
    if ctx.guard_sink is not None:
        configurable[GUARD_SINK_KEY] = ctx.guard_sink
```

- [ ] **Step 4: 跑测试确认通过 + 等价回归**

Run: `uv run pytest services/orchestrator/tests/test_token_budget_graph.py services/orchestrator/tests/test_react_graph.py services/orchestrator/tests/test_no_progress_stop.py services/orchestrator/tests/test_tool_context.py -q`
Expected: 全 PASS(现有测试零语义变更)

- [ ] **Step 5: Commit**

```bash
git add services/orchestrator/src/orchestrator/graph_builder/builder.py services/orchestrator/src/orchestrator/tools/registry.py services/orchestrator/src/orchestrator/tools/_child_run.py services/orchestrator/tests/test_token_budget_graph.py
git commit -m "feat: B3 agent_node token 累计/80%预警/超限收尾 + guard tripped 帧(老闸一起治)+ 全树下传"
```

---

### Task 4: sse 注入 + call sites + control-plane 桩同步

**Files:**
- Modify: `services/orchestrator/src/orchestrator/sse.py`(run_agent 签名 + `_publish_worker` 旁 + 注入区)
- Modify 5 call sites:`services/control-plane/src/control_plane/api/runs.py`(×2)、`run_queue_worker.py`、`trigger_firing.py`、`orphan_sweep.py`(`trajectory_enabled=built.trajectory_recording,` 旁各加一行)
- Modify 4 桩:`services/control-plane/tests/test_run_queue_worker.py`、`test_orphan_sweep.py`、`test_resume_idempotency_flow.py`、`test_approval_timeout_sweep.py`(SimpleNamespace BuiltAgent 加 `token_budget=0`)
- Test: `services/orchestrator/tests/test_sse_guard_events.py`(新;harness 照 `test_sse_worker_events.py`)

**Interfaces:**
- Consumes: Task 2 keys/类型。
- Produces: `run_agent(..., token_budget: int = 0)`;>0 时 configurable 带 `TOKEN_BUDGET_KEY`(新建对象)与 `GUARD_SINK_KEY`(`_publish_guard`);guard 帧 publish `"guard"` + 落 RunEventStore(seq 同步分配);`GUARD_SINK_KEY` **无条件注入**(max_steps/no_progress tripped 帧与 token 预算无关也要可见)。

- [ ] **Step 1: 写失败测试(照 `test_sse_worker_events.py` 的 `_new_record`/`_WorkerGraph` 惯例)**

```python
# 1) graph astream 内从 config 取 GUARD_SINK_KEY 发两帧 → RunEventStore 出现
#    event_name=="guard" 两行,data.kind 依序 ["warning","tripped"],seq 单调无重复。
# 2) run_agent(token_budget=1000) → configurable[TOKEN_BUDGET_KEY] 是 TokenBudget(limit=1000);
#    token_budget=0(默认)→ configurable 无 TOKEN_BUDGET_KEY,但 GUARD_SINK_KEY 仍在。
# 3) 并发 guard 帧(asyncio.gather ×4,yielding-bridge 真交错)seq 不撞
#    (InMemoryRunEventStore 对重复 seq raise)。
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest services/orchestrator/tests/test_sse_guard_events.py -q`
Expected: FAIL(KeyError guard_event_sink / 无 TokenBudget)

- [ ] **Step 3: 实现**

`sse.py`:签名加 `token_budget: int = 0`(`worker_spawn_budget` 参数旁);`_publish_worker` 后:

```python
    # B3 — guard marker 帧 sink(_publish_worker 同款:seq 在任何 await 前
    # 同步分配;worker 树里的 guard 也经它)。无条件注入 —— max_steps /
    # no_progress 的 tripped 可见化与 token 预算是否启用无关。
    async def _publish_guard(frame: dict[str, Any]) -> None:
        nonlocal event_seq
        seq = event_seq
        event_seq += 1
        await bridge.publish(run_id, "guard", frame)
        await _persist_event(
            event_store, run_id=run_id, seq=seq, event_name="guard", data=frame
        )

    effective_config["configurable"][GUARD_SINK_KEY] = _publish_guard
    if token_budget > 0:
        effective_config["configurable"][TOKEN_BUDGET_KEY] = TokenBudget(limit=token_budget)
```

import:`from orchestrator.tools._guards import GUARD_SINK_KEY, TOKEN_BUDGET_KEY, TokenBudget`。

5 个 call sites 各加 `token_budget=built.token_budget,`(`trajectory_enabled=` 行旁);4 个桩的 SimpleNamespace 各加 `token_budget=0,`(`trajectory_recording=True,` 旁)。

- [ ] **Step 4: 跑测试确认通过 + control-plane 回归**

Run: `uv run pytest services/orchestrator/tests/test_sse_guard_events.py -q && uv run pytest services/control-plane/tests -m "not integration" -q`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add services/orchestrator/src/orchestrator/sse.py services/control-plane
git commit -m "feat: B3 run_agent 注入 TokenBudget + guard sink(5 调用点 + 4 桩同步)"
```

---

### Task 5: UI 旋钮 + guard marker 前端

**Files:**
- Modify: `apps/admin-ui/src/components/manifest-editor/groups/RunBudgetSection.tsx`(RUN_BUDGET_DEFS)
- Modify: `apps/admin-ui/src/components/manifest-editor/form_model.ts`(RunBudgetFields/readRunBudget/patchRunBudget)
- Modify: `apps/admin-ui/src/i18n/locales/en.ts` + `zh-CN.ts`(`run_budget.token_budget_*` 四键;先 grep 撞键)
- Modify: `apps/admin-ui/src/api/timeline.ts`(MarkerItem kind + guard 分支)
- Modify: `apps/admin-ui/src/pages/agent_detail/playground/StepTimeline.tsx`(switch 加 `"guard"` case)
- Test: form_model round-trip 测试文件追加 + `apps/admin-ui/src/api/__tests__/timeline.test.ts` 追加

**Interfaces:**
- Consumes: 后端 guard 帧契约(Global Constraints);`policies.token_budget`。
- Produces: 第 7 旋钮;`MarkerItem.kind` 加 `"guard"`。

- [ ] **Step 1: 写失败测试**

form_model 测试(照 readRunBudget/patchRunBudget 既有用例追加):

```ts
it("reads and patches policies.token_budget", () => {
  const m = { spec: { policies: { token_budget: 500000 } } };
  expect(readRunBudget(m).tokenBudget).toBe(500000);
  const patched = patchRunBudget(m, { tokenBudget: undefined });
  expect((patched as any).spec.policies?.token_budget).toBeUndefined();
});
```

timeline 测试(`ev` fixture 惯例):

```ts
it("renders guard frames as markers", () => {
  const items = parseTimeline([
    ev("guard", { kind: "warning", guard: "token_budget", detail: { spent: 410000, limit: 500000 } }),
    ev("guard", { kind: "tripped", guard: "max_steps", detail: { steps: 30, max: 30 } }),
  ]);
  expect(items).toHaveLength(2);
  expect(items[0]).toMatchObject({ kind: "guard", tone: "warn" });
  expect(items[0].kind === "guard" && items[0].text).toContain("82%");
  expect(items[1]).toMatchObject({ kind: "guard", tone: "bad" });
  expect(items[1].kind === "guard" && items[1].text).toContain("步数耗尽");
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/admin-ui && pnpm exec vitest run src/api/__tests__/timeline.test.ts src/components/manifest-editor`
Expected: 新增用例 FAIL

- [ ] **Step 3: 实现**

`RunBudgetSection.tsx` RUN_BUDGET_DEFS 追加(`run_deadline` def 后):

```ts
  {
    fieldId: "policies.token_budget",
    i18nKey: "run_budget.token_budget",
    valueKey: "tokenBudget",
    kind: "number",
    effectiveDefault: 0,
    min: 0,
  },
```

`form_model.ts`:`RunBudgetFields` 加 `tokenBudget?: number;`;`readRunBudget` 加 `tokenBudget: specOf(m).policies?.token_budget,`;`patchRunBudget` 的 policies 段照 `maxNoProgress` 同款处理 `tokenBudget`(键名 `token_budget`)。

i18n 四键三处(interface + en 值 + zh-CN 值;en 措辞照组内既有风格):

```ts
    token_budget_label: "Token budget",
    token_budget_brief: "Per-run total token cap across the whole delegation tree (main agent + all workers).",
    token_budget_impact: "Counts input/output/cache tokens of every main-loop LLM call into one shared pool. At 80% the model is warned to converge; at the cap the run does one final tool-less wrap-up turn and completes normally. Guard markers appear on the timeline.",
    token_budget_default: "0 (disabled)",
```

```ts
    token_budget_label: "Token 预算",
    token_budget_brief: "本 run(主 Agent + 全部 worker)全委托树共享的 token 总上限。",
    token_budget_impact: "主循环每次 LLM 调用的 input/output/cache token 计入同一个池。80% 时提示模型收敛;耗尽时强制一轮无工具收尾并正常完成。触发在时间线上有 guard 标记。",
    token_budget_default: "0(关闭)",
```

`timeline.ts`:`MarkerItem.kind` 联合加 `"guard"`;marker 分支区(`approval` 分支旁)加:

```ts
    if (evt.event === "guard") {
      const d = obj(evt.data);
      const detail = obj(d.detail);
      const warn = str(d.kind) === "warning";
      const g = str(d.guard);
      const text =
        g === "token_budget"
          ? warn
            ? `token 预算已用 ${Math.round((int(detail.spent) / Math.max(1, int(detail.limit))) * 100)}%(${int(detail.spent)}/${int(detail.limit)})`
            : `token 预算耗尽(${int(detail.spent)}/${int(detail.limit)})→ 收尾轮`
          : g === "max_steps"
            ? `步数耗尽(${int(detail.steps)}/${int(detail.max)})→ 收尾轮`
            : `无进展 ${int(detail.streak)}/${int(detail.max)} → 收尾轮`;
      push({ kind: "guard", receivedAt: at, tone: warn ? "warn" : "bad", text });
      continue;
    }
```

`StepTimeline.tsx` switch(:84-88 marker kinds 列表)加 `case "guard":`。

- [ ] **Step 4: 跑测试确认通过**

Run: `pnpm exec vitest run src/api/__tests__/timeline.test.ts src/components/manifest-editor src/i18n && pnpm typecheck`
Expected: 全 PASS / 0 错误

- [ ] **Step 5: Commit**

```bash
git add apps/admin-ui
git commit -m "feat: B3 运行预算组 token_budget 旋钮 + 时间线 guard marker 渲染"
```

---

### Task 6: 整链验证

- [ ] **Step 1**: `uv run pytest services/orchestrator/tests -q`(DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock 环境下)— 全 PASS
- [ ] **Step 2**: `uv run pytest services/control-plane/tests -m "not integration" -q` — 全 PASS(pre-existing test_eval_engine_live 6 失败除外,须核对与 main 一致)
- [ ] **Step 3**: `uv run ruff check . && uv run ruff format --check .`
- [ ] **Step 4**: `uv run mypy packages services/audit-backup-worker/src services/billing-rollup-job/src services/event-log-archive-job/src services/orchestrator/src services/retention-cleanup-job/src`
- [ ] **Step 5**: `cd apps/admin-ui && pnpm exec vitest run src && pnpm typecheck && pnpm build`
- [ ] **Step 6**: 修复(如有)单独 commit `test: B3 PR1 整链验证修复`
