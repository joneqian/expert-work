# 自适应规划(P3)实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把"规划"从 build-time 强制节点改为模型自主调用的工具 —— 任意 agent 判断任务复杂时调 `update_plan` 自行规划,简单任务直接执行。

**Architecture:** 单工具 `update_plan` 改为 create-or-replace 语义 + 对所有 agent 默认注册(去掉 `if plan_execute` 注册闸)。`planner` node 保留但降级为 `plan_execute` 专属的"强制初始 plan"合规旁路(B)。中途重规划(C)已由 `update_plan` 全量替换内建。死开关 `custom`/`early_stop`/`builder` 轻量弃用标记。纯 orchestrator 后端为主 + 少量 schema docstring/前端 i18n copy。

**Tech Stack:** Python 3.12 / pydantic / LangGraph;pytest(`-m` 分类);前端 React + antd + i18next + vitest。

## Global Constraints

- 工具名保留 `update_plan`(**不改名** —— 改跨层命名要同步 builder metric/audit + 前端 tool_timeline/SettingsAudit 4 处 parser,收益不抵 churn;description 覆盖 "create or revise" 语义)。
- `update_plan` schema `required` 保持 `["steps", "reason"]` —— `goal` 为可选(否则破现有 plan_execute revise 调用)。
- 硬 caps 复用现有:`_MAX_STEPS = 20`、`_MAX_STEP_DESCRIPTION_CHARS = 500`,不新增。
- `render_plan`(`planner.py`)、`planner` node、`builder.py` 注入、压缩链、`TOOL_ALLOWED_STATE_KEYS`、reflect **不动**。
- 死开关字段 **deprecate ≠ 删**:schema 仍解析(`WorkflowSpec` 的 `extra="forbid"` 下删字段会破既有 YAML),只加 docstring/UI 弃用文案。
- protocol 包(`agent_spec.py`)改动后跑**全库** `ruff check` + CI 同款 pytest 范围。
- 前端只**改** i18n 现有值(不加/删 key)→ locale parity 不破。

---

### Task 1: `update_plan` 工具 create-or-replace + goal 参数 + 复杂度 guidance

把工具从"仅修订既有 plan"(plan 为 None 即报错)改为"创建或替换"(plan 为 None 即播种)。加可选 `goal` 参数,扩 description 覆盖 create + 复杂度自判。

**Files:**
- Modify: `services/orchestrator/src/orchestrator/tools/update_plan.py`
- Test: `services/orchestrator/tests/test_update_plan_tool.py`

**Interfaces:**
- Consumes: `ToolContext.plan: Plan | None`(`registry.py:183`,已 nullable);`Plan(goal: str, steps: tuple[PlanStep, ...])`;`PlanStep(id, description, status)`;`ToolResult(content, meta, state_updates, refund_iterations)`。
- Produces:
  - `UpdatePlanTool.call(args, *, ctx)` 语义:`ctx.plan is None` → 用 `args["goal"]`(缺省回落首个 step 描述)播种;`ctx.plan` 存在 → 保留既有 goal,除非 `args["goal"]` 非空则覆盖。返回 `ToolResult.state_updates["plan"]` 为新 `Plan`。
  - `UpdatePlanTool().spec.parameters["properties"]` 含 `goal`(optional string);`required == ["steps", "reason"]`。
  - `UpdatePlanTool().spec.description` 含复杂度提示子串 `"3+ "`。

- [ ] **Step 1: 改现有"无 plan 即报错"测试为"无 plan 即播种"**

`test_update_plan_tool.py:72-82` 现断言 `plan=None` raise `"nothing to revise"`。create-or-replace 后该行为作废 —— 替换整个测试函数:

```python
# ── 替换 test_update_plan_rejects_when_no_plan_in_context 整个函数 ──
@pytest.mark.asyncio
async def test_update_plan_seeds_a_new_plan_when_context_has_none() -> None:
    """P3 create-or-replace: a react-mode run has no seeded plan; the first
    ``update_plan`` call creates one from the supplied goal + steps rather
    than raising."""
    tool = UpdatePlanTool()
    result = await tool.call(
        {
            "steps": ["Scope the change", "Write the test", "Implement"],
            "reason": "Task turned out to need multiple steps",
            "goal": "Add adaptive planning to the agent",
        },
        ctx=_ctx_with_plan(plan=None),
    )
    new_plan = result.state_updates["plan"]
    assert isinstance(new_plan, Plan)
    assert new_plan.goal == "Add adaptive planning to the agent"
    assert [s.description for s in new_plan.steps] == [
        "Scope the change",
        "Write the test",
        "Implement",
    ]
    assert [s.id for s in new_plan.steps] == ["1", "2", "3"]
```

- [ ] **Step 2: 加 create-无-goal 回落 + explicit-goal-覆盖 两测**

追加到 `test_update_plan_tool.py`(happy-path 区之后):

```python
@pytest.mark.asyncio
async def test_update_plan_create_without_goal_falls_back_to_first_step() -> None:
    """Create path with no explicit goal: fall back to the first step's
    description so the recitation's ``Goal:`` line is never blank."""
    tool = UpdatePlanTool()
    result = await tool.call(
        {"steps": ["Investigate the bug", "Fix it"], "reason": "multi-step"},
        ctx=_ctx_with_plan(plan=None),
    )
    assert result.state_updates["plan"].goal == "Investigate the bug"


@pytest.mark.asyncio
async def test_update_plan_explicit_goal_overrides_existing_goal() -> None:
    """Revise path: an explicit ``goal`` renames the plan; without it the
    existing goal is preserved (covered by
    test_update_plan_replaces_steps_and_keeps_original_goal)."""
    tool = UpdatePlanTool()
    result = await tool.call(
        {"steps": ["redo"], "reason": "pivot", "goal": "Pursue the new direction"},
        ctx=_ctx_with_plan(),  # _INITIAL_PLAN present
    )
    assert result.state_updates["plan"].goal == "Pursue the new direction"


@pytest.mark.asyncio
async def test_update_plan_description_carries_complexity_guidance() -> None:
    """guidance-in-schema-only: the model learns *when* to plan from the
    tool description, not a system-prompt mutation."""
    spec = UpdatePlanTool().spec
    assert "3+ " in spec.description
    assert "goal" in spec.parameters["properties"]
    assert spec.parameters["required"] == ["steps", "reason"]
```

- [ ] **Step 3: 跑测试,确认失败**

Run: `cd services/orchestrator && DOCKER_HOST= python -m pytest tests/test_update_plan_tool.py -q`
Expected: FAIL —— `test_update_plan_seeds_a_new_plan_when_context_has_none` 报 `ValueError: ... nothing to revise`;`..._falls_back_to_first_step` 同 raise;`..._explicit_goal_overrides...` KeyError/goal 未覆盖;`..._complexity_guidance` 断言 `"3+ "`/`goal` 不在。

- [ ] **Step 4: 改 `update_plan.py` —— 松守卫 + goal 逻辑 + description**

改 `services/orchestrator/src/orchestrator/tools/update_plan.py`。

(a) 模块 docstring 与工具类 docstring 里"仅 plan_execute / 必须先有 plan"的表述已过时,但**本步只改行为代码 + `spec`/`call`**,docstring 收尾一并微调(见 Step 6 收尾)。先改 `spec.description` 与 `parameters`:

```python
# ── UpdatePlanTool.spec: description 扩为 create + 复杂度 guidance ──
            description=(
                "Create or replace your plan with an ordered list of steps. "
                "Use this when a task needs 3+ distinct steps or spans "
                "multiple tools; skip it for simple one-shot tasks. Mark "
                "steps completed / in_progress as you go so the recitation "
                "tracks progress."
            ),
```

在 `parameters["properties"]` 里(`reason` 之后)加 `goal`:

```python
                    "goal": {
                        "type": "string",
                        "description": (
                            "One-sentence restatement of what the plan "
                            "achieves. Provide it when first creating a plan; "
                            "on a later revise it is optional — the existing "
                            "goal is kept unless you pass a new one."
                        ),
                    },
```

`required` 保持不变(仍 `["steps", "reason"]`)。

(b) 改 `call` —— 删"无 plan 即 raise"块,加 goal 解析与三分支 goal 逻辑:

```python
    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        # P3 create-or-replace — a react-mode run reaches this tool with no
        # seeded plan (``ctx.plan is None``); the first call *creates* the
        # plan instead of raising. plan_execute runs still arrive with the
        # planner node's initial plan and this call *replaces* it.
        steps_raw = args.get("steps")
        reason = str(args.get("reason", "")).strip()
        goal_arg = str(args.get("goal", "")).strip()

        if not isinstance(steps_raw, list) or not steps_raw:
            msg = "update_plan requires a non-empty 'steps' array"
            raise ValueError(msg)
        if not reason:
            msg = "update_plan requires a non-empty 'reason' string"
            raise ValueError(msg)
```

(保留原有 `cleaned` 清洗循环与两个空校验 `if not cleaned` / `if len(cleaned) > _MAX_STEPS` 不变。)

把原 `new_plan = Plan(goal=ctx.plan.goal, steps=tuple(cleaned))` 那行替换为:

```python
        # Goal source (P3): an explicit ``goal`` arg wins (create, or rename
        # on revise); else keep the existing plan's goal (revise); else — a
        # create with no goal — fall back to the first step so the
        # recitation's ``Goal:`` line is never blank.
        if goal_arg:
            goal = goal_arg
        elif ctx.plan is not None:
            goal = ctx.plan.goal
        else:
            goal = cleaned[0].description
        new_plan = Plan(goal=goal, steps=tuple(cleaned))
```

- [ ] **Step 5: 跑测试,确认通过**

Run: `cd services/orchestrator && DOCKER_HOST= python -m pytest tests/test_update_plan_tool.py -q`
Expected: PASS(全部,含既有 `test_update_plan_replaces_steps_and_keeps_original_goal` 回归 —— revise 无 goal 仍保留 `_INITIAL_PLAN.goal`)。

- [ ] **Step 6: 收尾工具类 docstring 的过时表述**

`update_plan.py` 顶部模块 docstring(:19-22)与 `UpdatePlanTool` 类 docstring 说 "implicit — registered exactly when workflow.type == plan_execute" 及 `call` 内注释(现已删的 raise 附近)提到 "factory only registers update_plan for plan_execute"。把这些窄化表述改为反映 P3(默认对所有 agent 注册,react create / plan_execute replace)。示例改模块 docstring 末段:

```python
The tool is implicit — never declared in the manifest. Since P3 the
factory registers it for **every** agent (not just ``plan_execute``):
a react-mode agent calls it to *create* a plan on demand, a
plan_execute agent's planner node seeds an initial plan that this tool
then *replaces*.
```

(纯注释,无行为变化;确保无残留 "only registers ... plan_execute" 误导。)

- [ ] **Step 7: 提交**

```bash
git add services/orchestrator/src/orchestrator/tools/update_plan.py services/orchestrator/tests/test_update_plan_tool.py
git commit -m "feat(planning): update_plan create-or-replace + goal 参数 + 复杂度 guidance (P3)"
```

---

### Task 2: `update_plan` 对所有 agent 默认注册

去掉 `if workflow.type == "plan_execute"` 注册闸,让每个 agent 的工具集都含 `update_plan`。`planner` node 保持 `plan_execute` 专属(不动)—— 这是 B 合规旁路。

**Files:**
- Modify: `services/orchestrator/src/orchestrator/agent_factory.py:756-762`
- Test: `services/orchestrator/tests/test_agent_factory.py`

**Interfaces:**
- Consumes: Task 1 的 `UpdatePlanTool`(react 播种、plan_execute 替换);`build_agent` 局部 `registry`(`agent_factory.py:728`);`ToolRegistry.register(tool, *, deferred=False, source=None)`。
- Produces: 任意 `workflow.type` 的 build 都调 `registry.register(UpdatePlanTool())` 恰一次。`planner_node`(`:753-754`)仍 `plan_execute` 才建 —— 回归不变。

- [ ] **Step 1: 写失败测试(react 注册 update_plan + plan_execute 回归)**

`BuiltAgent` 不暴露 registry,用 monkeypatch spy 捕获注册的工具名。先在 `test_agent_factory.py` 顶部 import 区(`:33` 之后)加:

```python
from orchestrator.tools.registry import ToolRegistry
```

追加到 `test_agent_factory.py`(planner-node 两测 `:564-580` 附近):

```python
@pytest.mark.asyncio
async def test_build_agent_react_registers_update_plan_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P3 — update_plan is default-registered for every workflow, so a
    react agent can self-plan (previously gated to plan_execute)."""
    registered: list[str] = []
    original = ToolRegistry.register

    def _spy(self: ToolRegistry, tool: Any, *args: Any, **kwargs: Any) -> None:
        registered.append(tool.spec.name)
        return original(self, tool, *args, **kwargs)

    monkeypatch.setattr(ToolRegistry, "register", _spy)
    async with make_checkpointer("memory") as cp:
        await _build(_spec(), secret_store=_secret_store(), checkpointer=cp)
    assert "update_plan" in registered


@pytest.mark.asyncio
async def test_build_agent_plan_execute_still_registers_update_plan_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: plan_execute keeps update_plan (now via the same
    unconditional registration path)."""
    registered: list[str] = []
    original = ToolRegistry.register

    def _spy(self: ToolRegistry, tool: Any, *args: Any, **kwargs: Any) -> None:
        registered.append(tool.spec.name)
        return original(self, tool, *args, **kwargs)

    monkeypatch.setattr(ToolRegistry, "register", _spy)
    doc = deepcopy(_MINIMAL_SPEC)
    doc["spec"]["workflow"] = {"type": "plan_execute"}
    spec = AgentSpec.model_validate(doc)
    async with make_checkpointer("memory") as cp:
        await _build(spec, secret_store=_secret_store(), checkpointer=cp)
    assert "update_plan" in registered
```

- [ ] **Step 2: 跑测试,确认 react 那条失败**

Run: `cd services/orchestrator && DOCKER_HOST= python -m pytest tests/test_agent_factory.py -q -k update_plan`
Expected: `test_build_agent_react_registers_update_plan_tool` FAIL(`"update_plan" not in registered` —— 现仅 plan_execute 注册);`..._plan_execute_still_...` PASS。

- [ ] **Step 3: 去掉注册闸**

改 `services/orchestrator/src/orchestrator/agent_factory.py:756-762`。现:

```python
    # Stream K.K8 — the agent-initiated replan path. Closing the J.1
    # loop: planner sets the initial plan, ``update_plan`` lets the
    # agent revise it during the run. Implicit tool — never declared in
    # the manifest, registered exactly when the workflow is plan_execute
    # so react-mode runs do not see it.
    if spec.spec.workflow.type == "plan_execute":
        registry.register(UpdatePlanTool())
```

改为:

```python
    # Stream K.K8 + P3 — the plan tool. Implicit (never declared in the
    # manifest). Registered for EVERY workflow so any agent can self-plan
    # on demand (create-or-replace): a react agent calls it to create a
    # plan when a task is complex; a plan_execute agent's planner node
    # seeds an initial plan that this tool then revises. The planner node
    # itself stays plan_execute-only (below) — that is the forced-plan /
    # compliance path (B).
    registry.register(UpdatePlanTool())
```

(`planner_node` 定义 `:753-754` 不动。)

- [ ] **Step 4: 跑测试,确认通过**

Run: `cd services/orchestrator && DOCKER_HOST= python -m pytest tests/test_agent_factory.py -q -k "update_plan or planner"`
Expected: PASS —— 两新测 + `test_build_agent_react_has_no_planner_node`(react 仍无 planner node)+ `test_build_agent_plan_execute_adds_planner_node` 全绿。

- [ ] **Step 5: 提交**

```bash
git add services/orchestrator/src/orchestrator/agent_factory.py services/orchestrator/tests/test_agent_factory.py
git commit -m "feat(planning): update_plan 默认对所有 agent 注册,planner node 降级为 plan_execute 合规旁路 (P3)"
```

---

### Task 3: 死开关弃用标记 + 前端 workflow 文案反映 P3

后端 `WorkflowSpec` 加 docstring 标 `custom`/`early_stop`/`builder` deprecated。前端 `wf_type_impact` 现文案说"react 不能规划、要规划用 plan_execute" —— P3 后 react 能自主规划,该文案会误导,刷新之;`custom`/`early_stop`/`builder` 文案由"保留/未接线"改"已弃用"。

**Files:**
- Modify: `packages/expert-work-protocol/src/expert_work/protocol/agent_spec.py:580-591`
- Modify: `apps/admin-ui/src/i18n/locales/en.ts:3396-3403`
- Modify: `apps/admin-ui/src/i18n/locales/zh-CN.ts:620-627`
- Test: `apps/admin-ui/src/components/manifest-editor/groups/__tests__/RunBudgetSection.test.tsx:72-74`

**Interfaces:**
- Consumes: Task 2 的语义(react self-plan / plan_execute forced-plan)。
- Produces: `WorkflowSpec` 类 docstring;i18n 值刷新(不加/删 key);`RunBudgetSection.test.tsx` custom option 断言对齐新 copy。

- [ ] **Step 1: 加 `WorkflowSpec` docstring**

改 `packages/expert-work-protocol/src/expert_work/protocol/agent_spec.py`,在 `class WorkflowSpec(BaseModel):`(:580)与 `model_config`(:581)之间插 docstring:

```python
class WorkflowSpec(BaseModel):
    """Agent workflow shape.

    ``type`` selects the run loop. ``react`` (default) runs the ReAct
    loop; since P3 every agent can self-plan on demand by calling the
    implicit ``update_plan`` tool. ``plan_execute`` additionally
    front-loads a ``planner`` node that forces an upfront plan — the
    always-plan / compliance path.

    ``custom`` (a ``type`` value) and the ``early_stop`` / ``builder``
    fields are **deprecated**: they pass schema validation but the
    runtime has no branch for them, so they are inert. Retained (not
    removed) to avoid breaking existing manifests; do not author new
    agents with them.
    """

    model_config = ConfigDict(extra="forbid")
```

- [ ] **Step 2: 跑后端类型/lint,确认 protocol 干净**

Run: `cd packages/expert-work-protocol && python -c "from expert_work.protocol import WorkflowSpec; print(WorkflowSpec.__doc__.splitlines()[0])"`
Expected: 打印 `Agent workflow shape.`(import 成功、docstring 生效)。
Run: `ruff check packages/expert-work-protocol/src/expert_work/protocol/agent_spec.py`
Expected: `All checks passed!`(纯 docstring,无 lint 变化)。

- [ ] **Step 3: 刷新 en.ts workflow 文案**

改 `apps/admin-ui/src/i18n/locales/en.ts`:

`wf_type_impact`(:3396-3397)整值替换:

```typescript
    wf_type_impact:
      "react: the agent self-plans on demand — it calls the update_plan tool to lay out steps when a task is complex, and skips planning for simple ones (recommended for agents whose tasks vary widely in complexity). plan_execute: every task first gets a full plan before execution — more stable for long-chain tasks (e.g. report generation) and guarantees an upfront plan, but even trivial tasks can't skip the forced planning step (one extra planning-model call, slower and costlier). The planning model is set by the model group's routing planning rule; when no rule is set, the main model is used. custom is deprecated and has no dedicated implementation — it behaves the same as react.",
```

`wf_type_opt_custom`(:3401):

```typescript
    wf_type_opt_custom: "custom (deprecated — same as react)",
```

`workflow_note`(:3402-3403):

```typescript
    workflow_note:
      "workflow's early_stop and builder are deprecated fields: they pass validation but are never read at runtime — leaving them in the YAML is harmless.",
```

- [ ] **Step 4: 刷新 zh-CN.ts workflow 文案**

改 `apps/admin-ui/src/i18n/locales/zh-CN.ts`:

`wf_type_impact`(:620-621)整值替换:

```typescript
    wf_type_impact:
      "react=按需自主规划:任务复杂时 Agent 调用 update_plan 工具列出步骤,简单任务则直接执行(任务复杂度差异大的 Agent 推荐)。plan_execute=每个任务都先出完整计划再执行,长链条任务(报告生成等)更稳、保证有前置计划,但简单任务也逃不过强制规划(多一次规划模型调用,更慢更贵)。规划所用模型由模型组的 routing planning 规则指定,无规则时用主模型。custom 已弃用,无专属实现,行为等同 react。",
```

`wf_type_opt_custom`(:625):

```typescript
    wf_type_opt_custom: "custom(已弃用,等同 react)",
```

`workflow_note`(:626-627):

```typescript
    workflow_note:
      "workflow 的 early_stop 与 builder 为已弃用字段:通过校验但运行时不读取,留在 YAML 中无害。",
```

- [ ] **Step 5: 对齐 RunBudgetSection 测试的 custom option 断言**

改 `apps/admin-ui/src/components/manifest-editor/groups/__tests__/RunBudgetSection.test.tsx:72-74`,把断言文案改为新 copy:

```typescript
    expect(
      screen.getByText(optionContent("custom (deprecated — same as react)")),
    ).toBeInTheDocument();
```

- [ ] **Step 6: 跑前端 typecheck + 相关 vitest**

Run: `cd apps/admin-ui && pnpm typecheck`
Expected: exit 0。
Run: `cd apps/admin-ui && pnpm vitest run src/components/manifest-editor/groups/__tests__/RunBudgetSection.test.tsx`
Expected: PASS(含 "workflow.type select renders with 3 options" —— custom 新 copy 命中)。

- [ ] **Step 7: 提交**

```bash
git add packages/expert-work-protocol/src/expert_work/protocol/agent_spec.py apps/admin-ui/src/i18n/locales/en.ts apps/admin-ui/src/i18n/locales/zh-CN.ts apps/admin-ui/src/components/manifest-editor/groups/__tests__/RunBudgetSection.test.tsx
git commit -m "docs(planning): WorkflowSpec 死开关标弃用 + 前端 workflow 文案反映 react 自主规划 (P3)"
```

---

## 最终验证(全 task 后)

- [ ] **后端全量测试(CI 同款范围)**

Run: `cd services/orchestrator && DOCKER_HOST= python -m pytest -q -m "not integration"`
Expected: PASS(重点回归:`test_update_plan_tool.py` 全绿、`test_agent_factory.py` planner + update_plan、`test_step_count_refund.py`/`test_tool_scheduling.py` 的 update_plan 用例不受签名变化影响 —— 它们不传 `goal`,revise 路径行为不变)。

- [ ] **全库 lint(protocol 改动波及)**

Run: `ruff check . && ruff format --check .`(仓库根)
Expected: `All checks passed!`

- [ ] **后端类型**

Run: CI 同款 mypy 范围(见 `.github/workflows`;历史注:mypy 不含 control-plane,但含 orchestrator + protocol)。
Expected: 无新增错误。

- [ ] **前端**

Run: `cd apps/admin-ui && pnpm typecheck && pnpm vitest run src/components/manifest-editor`
Expected: typecheck exit 0;manifest-editor 套件全绿。

---

## Self-Review 记录(写计划时自查)

1. **Spec 覆盖:** §2.1 默认注册→Task 2;§2.2 create-or-replace + goal→Task 1;§2.3 复杂度 guidance→Task 1 Step 4/6;§2.4 渲染/压缩不动→Global Constraints(无 task,正确);§2.5 死开关弃用→Task 3;§5 明确不做(不抄压缩补丁/不做 anti-premature-exit)→无 task(正确,是"不做");§6 测试→各 task TDD + 最终验证。无缺口。
2. **占位扫描:** 无 TBD/TODO;每个改动步含完整代码与精确行号。
3. **类型一致:** `update_plan` 名贯穿三 task 一致;`goal` 参数 optional、`required=["steps","reason"]` Task 1 定义与 Task 1 测试断言一致;monkeypatch spy 的 `tool.spec.name` 对齐 `ToolSpec.name`;前端 `custom` copy 在 en.ts/zh-CN.ts/RunBudgetSection.test.tsx 三处同步(en 值 = 测试断言值)。
