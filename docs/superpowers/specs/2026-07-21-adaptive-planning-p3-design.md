# 自适应规划(P3)设计

> Backlog P3。将"规划"从 build-time 强制节点改为**模型自主调用的工具**(planning-as-a-tool),让任意 agent 在判断任务复杂时自行规划,简单任务直接执行。骨架取 **A 极简为骨干**。

**状态:** 设计定稿,待写实现计划。
**前置:** 无(纯 orchestrator 后端 + 少量 schema/UI 弃用标记)。
**顺序:** backlog B3→P5→P2→P3;P5 已全交付,本为 P3。

---

## 1. 背景与动机

平台现有三条运行护栏(步数 `max_iterations`、时长 `run_deadline_s`、token 熔断 B3),规划能力却是 **build-time 死开关**:

- `WorkflowSpec.type` = `react` | `plan_execute` | `custom`。
- `react`:无规划,直接进 ReAct 循环。
- `plan_execute`:前置一个 `planner` node,运行开始强制播种一份初始 `Plan`,每轮 recite;`update_plan` 工具**仅此模式注册**,让 agent 中途修订。
- `custom`:schema 暴露,`agent_factory` 无 runtime 分支 → 死。
- `WorkflowSpec.early_stop` / `builder`:同样 schema 暴露、无消费 → 死。

问题:规划要不要做,**建 agent 时就钉死**了。一个 react agent 遇到需要多步分解的复杂任务时无从规划;一个 plan_execute agent 遇到简单一步任务仍被强制先出一份 plan。规划频率无法随任务自适应 —— 这正是 P3 要解的。

**理论依据(探索阶段结论):**
- **planning-as-a-tool 是 2025-26 行业收敛点**:Claude Code(TodoWrite/Task)、LangChain DeepAgents(`write_todos`)、Anthropic think-tool(+54%)、Cursor Plan Mode,以及本仓参考的 `deer-flow`(`write_todos` via TodoListMiddleware)与 `hermes-agent`(`todo_tool`)。两个参考实现都**主选 A**、放弃入口分类器(B)、把中途重规划(C)折进"模型自主编辑 todo"。
- **Learning When to Plan(arXiv 2509.03581)**:正确的控制对象是随任务变化的规划*频率*(Goldilocks),不是一次性门控。入口复杂度路由(B)本质 = 现有死开关往前挪,理论上严格更弱。

---

## 2. 方案:A 极简为骨干

**一个变化撑起整个 epic** —— `update_plan` 工具从"仅 plan_execute 注册"改为"**所有 agent 默认注册**",配合两处小松绑,让模型自判复杂度、按需规划。B、C 折进 A,死开关轻量弃用。

三个方向的落地:

| 方向 | 落地 |
|---|---|
| **A 规划即工具** | `update_plan` 默认对所有 agent 可用;模型经 schema guidance 自判何时规划 |
| **B 入口强制规划** | `plan_execute` 降级为"合规强制规划旁路"——保留 planner node 强制播种初始 plan,但不再是唯一规划路径 |
| **C 中途重规划** | 已内建:`update_plan` 全量替换 + 单 `in_progress` 状态机即模型自主重规划;reflect 的 revise 路径(J.2)仍在 |

### 2.1 工具默认可用(核心)

`update_plan` 现为 implicit tool,注册在 `if workflow.type == "plan_execute"` 内。改为无条件注册:

```python
# agent_factory.py — 现状(:761-762)
if spec.spec.workflow.type == "plan_execute":
    registry.register(UpdatePlanTool())

# 改后 —— 去掉 gate,所有 agent 都注册
registry.register(UpdatePlanTool())
```

`planner_node`(:753-754)**不动** —— 仍 `if plan_execute` 才建。这就是 B 旁路:plan_execute = "强制先出 plan",react = "自主"。

### 2.2 create-or-replace 语义

`update_plan` 现要求先有 plan(`ctx.plan is None → raise`,:112-119),因为 react 模式从无 planner 播种。改为 **create-or-replace**:

- `ctx.plan is None`(react 首次调用)→ **播种**新 plan(不再报错)。
- `ctx.plan` 存在(revise)→ 全量替换,行为不变。

`ToolContext.plan: Plan | None`(registry.py:183)**已 nullable**,docstring 明说 "None in react-mode runs" —— 零新管道,只松守卫。plan 经 `TOOL_ALLOWED_STATE_KEYS = {"plan", ...}` 状态通道持久化,下一轮 `ctx.plan` 自然反映 —— plan_execute 路径已验证此链路,react 只是起点为 None。

**goal 来源:** 现 `new_plan = Plan(goal=ctx.plan.goal, ...)`(:160)靠既有 plan 取 goal;create 分支无既有 goal。→ 加**可选 `goal` 参数**:
- create(plan is None):用模型给的 `goal`;缺省时兜底(取首个 step 描述或空串占位)。
- revise(plan 存在):保留既有 goal;仅当模型显式给 `goal` 时覆盖。

schema 加一个 optional string 字段,`required` 保持 `["steps", "reason"]`(goal 不强制,兼容既有 plan_execute 调用)。

### 2.3 复杂度自判(guidance-in-schema-only)

模型何时该规划 —— **只放进 tool schema description,不碰 system prompt**(hermes-agent 范式:行为指导全在工具描述里)。`update_plan` 的 description 补自判措辞,大致:

> "Replace or create your plan with a revised ordered list of steps. Use this when the task needs 3+ distinct steps or spans multiple tools; skip it for simple one-shot tasks."

(现 description 只讲"execution has diverged"——扩为覆盖 create + 复杂度提示。)

deer-flow / hermes 都用 "3+ steps / not for simple tasks" 措辞,直接沿用行业收敛表述。

### 2.4 渲染与压缩:不动

- **渲染:** `render_plan`(planner.py:122)每轮全量 recite,带状态框 `[x]`/`[~]`/`[ ]`(完成项显 `[x]`)。plan 是 checkpointed **state 通道**,经 `_inject_plan`(builder.py:1480)渲成尾部 HumanMessage。react 无 plan 时不注入(现有行为),模型一旦调 `update_plan`,plan 出现并每轮 recite。
- **压缩:** plan 在 state 通道,**不进被压缩的 message history** → 压缩碰不到它 → 每轮从 state 新鲜重渲,完成/未完成都在。

**因此不采纳** deer-flow(`todo_middleware.py:115-151`)/ hermes(`conversation_compression.py:1015-1021`)的"压缩后只重注入未完成项"。那是 **todo-in-message-stream** 架构的补丁:它们把 todo 放消息流,压缩会吃掉,故事后重注入且只放未完成(完成项在消息流里是噪声,诱导模型重做)。我们的 state-channel 设计天然免疫,且完成项以 `[x]` 每轮 recite,模型不会失忆(细节 §5)。

### 2.5 死开关轻量弃用

`custom` / `early_stop` / `builder` 真死(schema+UI 暴露,`agent_factory` 无分支)。本 epic **轻量弃用**,不删(deprecate ≠ 删,不破存量配置):

- `WorkflowSpec.type` 的 `custom`、`early_stop`(:590)、`builder`(:591):docstring 标 deprecated;schema 仍解析(`extra="forbid"` 下删字段会破既有 YAML,故保留)。
- 前端 `RunBudgetSection.tsx:57-65` 的 type 下拉 + i18n(`en.ts:3401-3403`):`custom` 选项 + early_stop/builder 标「已弃用」。
- `agent_factory`:无改(本就忽略这些)。

---

## 3. 改动面(文件清单)

| 文件 | 改动 |
|---|---|
| `services/orchestrator/src/orchestrator/agent_factory.py:761-762` | 去 `if plan_execute` gate,无条件 `register(UpdatePlanTool())` |
| `services/orchestrator/src/orchestrator/tools/update_plan.py` | ①`ctx.plan is None` 守卫 raise→播种;②加可选 `goal` 参数 + create/revise goal 逻辑;③description 扩为 create + 复杂度 guidance |
| `packages/expert-work-protocol/src/expert_work/protocol/agent_spec.py:583,590,591` | `WorkflowSpec` docstring 标 `custom`/`early_stop`/`builder` deprecated |
| `apps/admin-ui/src/components/manifest-editor/groups/RunBudgetSection.tsx:57-65` | type 下拉 + 弃用标记 |
| `apps/admin-ui/src/i18n/en.ts` / `zh-CN.ts`(:3401-3403 附近) | 弃用文案 |
| 测试 | orchestrator 工具测 + 回归(见 §6) |

**不动:** `render_plan`、`planner.py` node、`builder.py` 注入、压缩链、`TOOL_ALLOWED_STATE_KEYS`、reflect。

预估 ~400-600 行(含测试),纯后端为主。

---

## 4. 分解为独立单元

1. **工具行为**(`update_plan.py`):create-or-replace + goal 参数 + description。自成一测循环。
2. **默认注册**(`agent_factory.py`):去 gate。react agent 见工具的集成测。
3. **死开关弃用**(schema docstring + 前端标记):独立,与规划机制正交。

三单元弱耦合,可分 task。规模不大,单 PR 可容(实现计划阶段定 task 切分)。

---

## 5. 明确不做(借鉴细节取舍)

| 借鉴细节(来自 deer-flow / hermes) | 决定 | 理由 |
|---|---|---|
| 计划骑穿压缩:只重注入未完成项 | **不抄** | 我们 plan 是 state 通道,非 message-stream;压缩碰不到,完成项以 `[x]` 每轮 recite。他们的补丁解他们的架构问题 |
| guidance-in-schema-only | **采纳** | 行为指导全放 tool description,不污染 system prompt |
| 单 `in_progress` 状态机 | **已有** | `{description, status}` 已支持;不新增强制"单一 in_progress"校验(YAGNI) |
| 硬 caps | **复用现有** | `_MAX_STEPS=20`、`_MAX_STEP_DESCRIPTION_CHARS=500` 已在 update_plan.py |
| anti-premature-exit nudge(有未完成 step 阻止退出) | **不做**(backlog) | YAGNI;`max_iterations` 已兜底;plan 定位是**建议非契约**(`render_plan` 结尾就说 "adapt it if you discover something that requires a different approach"),强制"跑完才准退"会把建议性 plan 变半强制,复杂度涨 |
| 入口复杂度分类器(B 的完整形态) | **不做** | 理论更弱(arXiv 2509.03581);plan_execute 已是"强制规划"的够用旁路 |
| 独立重规划节点 / failure 触发器(C 的完整形态) | **不做** | `update_plan` 全量替换即模型自主重规划;reflect revise 已覆盖反射式重规划 |
| kanban / 跨 agent 持久看板(hermes) | **不做** | 远超 P3 scope |

**关于"模型会不会不知道自己做了什么"**(压缩后失忆担忧的正式回答):不会。两份"做过什么"记录互补且都存活 ——
- **plan 的 `[x]` recite**:粗粒度(哪步 done),state 通道每轮全量重渲。
- **压缩摘要**:细粒度(结果/发现),压缩是总结非删除,保留。

残留 tradeoff(诚实):plan `[x]` 只说"step 3 done"不说其**结果**;结果活在 message history,压缩可能有损 —— 但这与 plan 设计无关,正是压缩摘要的职责。plan = 进度骨架,摘要 = 血肉,两者都在,无 gap。

---

## 6. 测试

**orchestrator 工具单测**(`update_plan`):
- react agent(plan is None):`update_plan` 首调 → 播种新 plan,`state_updates["plan"]` 含 goal + steps。
- create 分支 goal:给 `goal` → 用之;不给 → 兜底(首 step 或占位)。
- revise 分支:既有 plan + 不给 goal → 保留既有 goal;给 goal → 覆盖。
- description 含复杂度 guidance 文案。
- 现有 revise/refund/caps 回归不破。

**集成测**:
- react 类型 agent 的工具集合**含** `update_plan`(改后默认)。
- plan_execute 类型仍强制播种初始 plan(planner node 回归)+ 工具仍在。
- 全量 recite 渲染含 `[x]` 完成项(render_plan 回归)。

**前端**:type 下拉渲染 + 弃用标记;`pnpm typecheck`。

**CI 范围**:改共享 protocol(agent_spec)后跑全库 `ruff check` + CI 同款 pytest 范围(orchestrator + 若触发 control-plane 经 run_agent 路径则并入)。

---

## 7. 风险

- **create 分支 goal 缺失** → 可选参数 + 兜底,不 raise。
- **工具名 `update_plan` 语义略窄**(现也能 create)→ **保留原名**。改名要同步 4 处 parser(builder metric/audit + 前端 tool_timeline / SettingsAudit,见 mcp-tool-name-wire-safe 教训),收益不抵 churn;description 覆盖 "create or revise" 语义即可。
- **默认注册扩大工具面** → 所有 agent 多一个工具。风险低:implicit tool 不进 manifest、不占用户 allow_tools 配额;模型经 guidance 自判,简单任务不调。
- **既有 plan_execute agent** → 不破:仍得强制初始 plan,额外多了 default 工具(本就有)。

---

## 8. 后续(backlog,不在本 epic)

- anti-premature-exit completion nudge(若观测到 agent 频繁丢弃未完成 plan 提前退出,再评估)。
- 死开关 `custom`/`early_stop`/`builder` 的最终移除(经一个 deprecation 周期后)。
- 规划质量观测(plan 步数分布、修订频率、完成率)—— 若要量化"规划频率是否 Goldilocks"。
