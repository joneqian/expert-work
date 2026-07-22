# Spec 1 — 对话驱动定时任务:对话核心 设计文档

> **Program 背景**:本文是「触发器 user 维度」program 的第一个 Spec。program 把原 backlog「触发器 user 维度」扩成完整的「对话驱动定时任务 + 结果投递」产品,拆成 3~4 个 Spec:
> - **Spec 1(本文)— 对话核心**:地基 F + 对话工具 B + 结果回原对话 D1 + 调试台模拟。做出「聊天建任务 → 触发 → 结果回原对话 → playground 端到端演练」。
> - Spec 2 — 主动通知 D2(依赖 1,大概率拆 2a 通用事件 webhook + 2b 客户端长连接推送)。
> - Spec 3 — 后台管理面 A + manifest triggers 弃用 C(依赖 1)。
>
> 用户核心愿景:终端用户**主要在客户端对话里**下达/管理定时任务(如「每天3点搜AI新闻」),**结果回到那个对话**;后台管理面是辅助。对话优先、后台辅助。

**Goal(Spec 1)**:让终端用户在与 Agent 的对话里用自然语言创建/查看/修改/取消定时任务;任务按其时间规则触发,以该用户身份跑一次独立 run;成功结果追加回创建它的那个对话。整条链路能在后台调试台(playground)端到端演练。

---

## 1. 范围

### 做(Spec 1)
- **F 地基**:数据层支持 per-user 定时任务(唯一约束分 user、RRULE 调度 + IANA 时区、投递路由字段);scheduler 从 croniter 切到 RRULE;关闭现有触发器 API 的所有权安全洞;任务生命周期进事件总线。
- **B 对话工具**:Agent 侧内建工具 `manage_task`(create/list/update/cancel),LLM 在对话里可调;自排护栏;指引走 schema description。
- **D1 结果回原对话**:对话建的任务成功后,结果追加进原对话。
- **调试台模拟**:playground 里对话建任务 →「立即触发」→ 看 run 全过程 + 结果落回对话。

### 不做(明确后置)
- **D2 主动通知**(任务完成/失败推送给不在看的用户、邮件、客户端长连接推送)→ **Spec 2**。Spec 1 里失败仅记录在 `trigger_run`(dead_letter),用户面失败通知属 Spec 2。
- **A 后台管理面**(用户详情页任务 tab、admin 定向建/停/删、按 Agent 筛选列表)→ **Spec 3**。
- **C manifest triggers 弃用** → **Spec 3**。
- **admin 替他人建任务 / 后台新建选 Agent(never-run 拦截)**:属后台管理面,→ Spec 3。Spec 1 的对话路径里,任务天然跑在「用户正在对话的当前 Agent」上,不存在选 Agent / never-run 问题。

---

## 2. 设计决策(已与用户确认,锁定)

| # | 决策 | 理由 |
|---|------|------|
| D-1 | 调度存 **RRULE(RFC 5545)**,不用 cron | RRULE 原生表达 一次性 / 递归 / 每月第N天 / **有界窗口(UNTIL·COUNT)** / DST 安全本地墙钟。用户四类时间维度(「每天3点」「每周三下午1点」「从5月1到6月13」「每月2号」)全落地,cron+UTC 做不到。 |
| D-2 | RRULE 求值用 **`dateutil.rrule`** | 标准库实现,tz-aware DTSTART 保证本地墙钟正确、DST 安全。 |
| D-3 | 工具形态 = **单 `manage_task`**(action=create/list/update/cancel) | hermes 范式,一工具四动作,LLM 少在多工具间混淆。 |
| D-4 | 调度入参 = **结构化字段**(frequency/time/by_day/start/end),工具内部建 RRULE 并存 | LLM 可靠产出结构化 JSON,不可靠手写 RRULE 微语法。工具持有 RRULE 正确性。 |
| D-5 | 能力 = **内建 `Tool` 类,不是 skill** | 本仓 skill = prompt 片段 + 工具名 + 静态文件,**无 code 字段、拿不到 `ToolContext`**;`manage_task` 要 ToolContext + 写 store + 发事件,构造上只能是内建 Tool(与 `UpdatePlanTool` 同侧)。 |
| D-6 | 挂载 = **opt-in via manifest `tools:`**(进 `KNOWN_BUILTINS`),**不像 `update_plan` 无条件全挂** | 定时任务有 blast radius(建持久 job、会烧钱、会投递),本就该显式声明;对话面向用户的 Agent 声明它,纯后台 worker 不声明。 |
| D-7 | 指引住在**工具 schema `description`**,不烤进 system prompt(**铁律**) | schema description 是永久字段级文档,任何挂了工具的 Agent 都看到;未来 instructional skill 是叠加的富指引层,纯增量,不用从任何地方「搬出来」。 |
| D-8 | **冻结 `manage_task` 名 + 参数 schema**(**铁律**) | 未来 instructional skill 按名字引工具;工具名 + 字段契约 Spec 1 定死(名过 `^[a-zA-Z][a-zA-Z0-9_-]{,63}$` wire-safe),skill 后补零成本。 |
| D-9 | 投递做法 A:**对话建 → 结果回原对话**;**后台建 → 新会话**。按创建来源定,用 `context_mode` 编码 | 用户拍板。 |
| D-10 | **只投递**语义:每次触发 run 干净跑(不回放原对话历史),只把结果追加回原对话 | 定时任务 = 独立执行 + 回帖,不是越滚越长的对话;避免上下文无界增长 + 成本失控。 |
| D-11 | **反问**:用户没说具体时间点 → 工具拒绝(要求具体时间)→ Agent 自然反问「早上几点?」 | 对话场景反问最自然,不猜。借对话媒介实现研究里「无人自动化的追问流」。 |
| D-12 | Spec 1 **顺带修安全洞**:现有触发器 API 的 GET/PATCH/DELETE 无所有权校验(任一租户成员可改删他人触发器) | 打地基时不该一边发建任务工具、一边留着读写 API 大开。加 `trigger` RBAC 资源 + `resolve_target_user_id` 所有权校验。 |
| D-13 | **自排护栏**:被定时触发的 run 禁调 `manage_task` | hermes 用 toolset denylist 强制,防任务自己排任务无限套娃。 |

---

## 3. 组件 F — 地基(后端)

### 3.1 Schema 变更(persistence,新 migration)

现状(`packages/expert-work-persistence/.../models/agent_trigger.py`):
- `agent_trigger` 已有 `user_id UUID NULL`(无 FK)。
- 调度表达式存在 `config` JSONB 的 `"expr"` 键(cron 串)。
- 唯一约束 `agent_trigger_name_uniq = (tenant_id, agent_name, name)` —— **不分 user**。
- `kind IN ('cron','webhook')`,`source IN ('manifest','api')`。

变更:

1. **唯一约束分 user**。删 `agent_trigger_name_uniq`;加两个 partial unique index:
   - `(tenant_id, agent_name, user_id, name) WHERE user_id IS NOT NULL` —— 两个用户可同名任务。
   - `(tenant_id, agent_name, name) WHERE user_id IS NULL` —— manifest / legacy 无主任务仍按名唯一。

2. **调度载荷改 RRULE**。`config` JSONB 从 `{"expr": "<cron>"}` 改为 `{"rrule": "<RFC5545 RRULE>", "timezone": "<IANA>", "seed_input": "<任务指令>"}`。
   - 保留 `kind='cron'` 作为「时间调度」种类标签(历史命名;载荷已非 cron 表达式,migration/model 注释纠正)。
   - `seed_input` 键机制已存在(`fire_trigger` trigger_firing.py:236 已读),复用为「任务指令」——每次触发喂给 Agent 的 HumanMessage。

3. **新增投递路由列**(first-class,非埋 config):
   - `originating_thread_id UUID NULL` —— 对话建的任务存来源对话 thread;后台建的为 NULL。
   - `context_mode TEXT NOT NULL DEFAULT 'fresh_thread_per_run'`,CHECK `IN ('reuse_thread','fresh_thread_per_run')`。

   > `timezone` 暂留 config;若 Spec 3 admin UI 需按时区查询再提列。

### 3.2 Store 方法(`TriggerStore`)
- 新 `list_by_user(tenant_id, user_id, *, agent_name=None, limit, offset)` —— `manage_task` 的 list action + 未来 admin UI 用。
- create / update 覆盖新列(`originating_thread_id`、`context_mode`)与 config(`rrule`、`timezone`、`seed_input`)。
- **共享创建路径**:抽一个校验 + 建行的函数(RRULE 合法性、时区合法性、唯一冲突),**HTTP create 端点与 `manage_task` 工具都调它**,不各写一遍(DRY)。

### 3.3 Scheduler 切 RRULE(`control_plane/scheduler.py`)
- `_is_cron_due`(:95-107)→ `_is_rrule_due`:读 `config["rrule"]` + `config["timezone"]`,用 `dateutil.rrule` 以该 tz 的 tz-aware DTSTART 求 `after=last_fired_at or created_at` 之后的下一次;`<= now` 则 due。
- **有界窗口**:`dateutil.rrule` 对 `UNTIL`/`COUNT` 耗尽 → 无下一次 → 返回 None → 不 due;顺带把 `enabled` 置 False(自动停,避免每 sweep 重算已耗尽规则)。
- **时区正确**:`now` 仍取 UTC,但求值在 tz-aware 域进行(DTSTART 带 IANA tz),消除现 `datetime.now(UTC)` + 裸 croniter 的无时区隐患。
- CAS claim(`claim_cron_fire`)、DLQ 重试、reconcile 三 pass 结构不变。

### 3.4 安全洞修复(`control_plane/api/triggers.py`)
- RBAC `Resource` Literal 加 `trigger`;管理 API(GET/PATCH/DELETE/list)gate `require("trigger", read|write)`。
- GET/PATCH/DELETE 走 `resolve_target_user_id`(`api/_user_scope.py:53`,self / admin 定向他人 / 否则 403),关闭「任一成员改删他人触发器」的洞。
- 说明:`manage_task` 工具路径**天然自限**(用户建自己的任务,`user_id = ctx.user_id`),不经此 API;此项是修既有 HTTP API 的洞。现有顶层 `/triggers` 前端若受影响,按 admin-gating 调整;完整 user 维度管理 UI 属 Spec 3。

### 3.5 生命周期可观测(扩展 audit,非新总线)
> **plan 阶段核实的关键事实**:全库**无通用事件总线**(无 event_bus/publish_event);只有 ① audit(cross-cutting 可查)② event_log(per-thread 引擎遥测)③ run_event(per-run,FK 锁死 agent_run)。后两者带不了 trigger 级事件。故「生命周期事件」= **扩展 audit 通道**(YAGNI,不建总线)。
- 现有 audit action:`TRIGGER_CREATE`(API/工具建时发)/ `TRIGGER_UPDATE` / `TRIGGER_DELETE` / `TRIGGER_FIRE`(fire 时发)已存在。
- 补:`TRIGGER_COMPLETED` / `TRIGGER_FAILED`(reconcile pass 见 run outcome 时发,**属 PR3** —— 与 D1 投递同在 reconcile)。载荷含 trigger_id / run_id / status。
- 调试台可视 + 未来 D2 通知 **读 audit 行 + `trigger_run.status`**(firing 结果 DB 态),不订阅总线。

---

## 4. 组件 B — 对话工具 `manage_task`(orchestrator)

### 4.1 挂载
- 新增内建 `Tool` 类 `ManageTaskTool`,进 `KNOWN_BUILTINS`(`assembly.py`);Agent 在 manifest `tools:` 声明 `manage_task` 才挂(D-6)。
- 需注入 `TriggerStore`(仿 `skill_authoring` 工具注入 `SkillStore` 的范式,`agent_factory` gate on store 存在)。
- 从 `ToolContext` 取 `tenant_id` / `user_id` / `thread_id` / 当前 Agent name+version。

### 4.2 Actions
单工具,`action` 参数分支:

- **create**:入参结构化(见 4.3)。建一行 `agent_trigger`:
  - `agent_name/version` = ctx 当前 Agent(用户正在对话的 Agent,天然「跑过」)。
  - `user_id` = ctx.user_id。
  - `config.rrule` = 工具由结构化字段建的 RRULE 串;`config.timezone` = 解析出的 tz(账户默认或指令内 inline);`config.seed_input` = 任务指令文本。
  - `originating_thread_id` = ctx.thread_id;`context_mode` = `reuse_thread`。
  - 走 §3.2 共享创建路径(校验 + 唯一冲突)。
- **list**:`TriggerStore.list_by_user(tenant, ctx.user_id)`,可按当前 Agent 过滤。返回可读摘要(名、下次触发、规则人话化、enabled)。
- **update**:按 `id` 改(schedule 字段 / enabled);**所有权校验** id 属 ctx.user_id,否则拒。改 schedule 重建 RRULE。
- **cancel**:按 `id` 停用或删;同所有权校验。

### 4.3 结构化调度入参(D-4)
create/update 收(而非 RRULE 串):
- `frequency`:once | daily | weekly | monthly
- `time`:`{hour, minute}`(24h,必填 —— 缺则触发 D-11 反问)
- `by_day`(weekly 用):MO/TU/WE/…
- `by_month_day`(monthly 用):1–31
- `start_date` / `end_date`(可选,有界窗口)或 `count`(可选,跑 N 次)
- `timezone`(可选,缺用账户默认)

工具内部 → RRULE:如 daily+{3,0} → `FREQ=DAILY;BYHOUR=3;BYMINUTE=0`;weekly+WE+{13,0} → `FREQ=WEEKLY;BYDAY=WE;BYHOUR=13;BYMINUTE=0`;带 end_date → 追加 `;UNTIL=…`。

### 4.4 指引 in schema(D-7)
schema `description` 承载「何时用 / 字段语义 / 缺时间要反问 / 改任务传 id」,保持精炼(hermes 范式,类比 `update_plan` 的 `"3+ distinct steps"`)。不碰 system prompt。

### 4.5 自排护栏(D-13)
被定时触发的 run 构建时,`manage_task` **不进 registry**(deny)。机制:`fire_trigger` 构建 Agent 时标记「触发来源」,`agent_factory` / 构建路径据此剔除 `manage_task`。防止一个定时任务在被触发跑时再排新任务。

---

## 5. 组件 D1 — 结果回原对话(control-plane)

### 5.1 触发 run(不变)
`fire_trigger`(trigger_firing.py)保持在**独立 scratch thread** 跑(`thread_id = uuid4()`,seed = `config.seed_input`,无历史)—— 正是「只投递」要的干净执行(D-10)。run 上下文与投递目的地解耦。

### 5.2 投递步(reconcile pass 扩展)
scheduler reconcile pass(:282-317)已在 run 成功时把 `trigger_run` 转 SUCCEEDED —— 在此加投递:

- 条件:`trigger.context_mode == 'reuse_thread'` 且 `originating_thread_id` 非空 且 run 成功。
- 动作:取该 run 的最终 assistant 输出,**注入原对话 `originating_thread_id` 的 LangGraph checkpoint**(用 `graph.aupdate_state(config, {"messages":[AIMessage(...)]})`,无 LLM 轮、不回放历史)。**注意(plan 阶段核实的关键事实)**:`thread_message` 表是只读镜像(`TranscriptMirrorSweep` 从 checkpoint 拷),**无独立追加路径,不能直接插行**;消息真源在 checkpoint,故投递走 state update。投递消息标「定时投递」+ 携带源 run_id(便于下钻 trace)。checkpoint→thread_message 镜像扫描随后使读侧一致。具体 `aupdate_state` 接入方式(reconcile 侧如何取到原对话 Agent 的 graph)由 PR3 plan 定。
- 客户端下次拉取 / 打开该对话即见(Spec 1 = 消息落到对话;**实时推送 = Spec 2 D2**)。

### 5.3 失败与边界
- run 失败 → reconcile 照旧转 retrying / dead_letter(记录在 `trigger_run`)。Spec 1 **不**向对话投递失败(用户面失败通知 = Spec 2 D2);失败在调试台 / trigger_run 可见。
- `context_mode == 'fresh_thread_per_run'`(后台建,Spec 3 用)→ 无投递步,现行为。
- 投递并发:若原对话恰有 live run 在跑,追加消息只是一条 message 行(LangGraph checkpoint reducer 追加),排序交错可接受;plan 阶段注意。

---

## 6. 组件 调试台模拟(admin-ui playground)

- playground 里对话建任务:用户在调试台与 Agent 对话说「每天3点搜AI新闻」→ Agent 调 `manage_task` → 任务落库,调试台可见工具调用卡。
- **「立即触发」**:playground 加一个按钮,对刚建 / 选中的任务立即 `fire_trigger` 一次(不等调度),端到端看:run 全过程(步进 / 工具 / LLM)+ 成功后结果消息落回原对话。
- 能力不弱化:trace / 步进 / 工具 i-o 与普通 run 一致(复用现调试台 run 视图)。
- 生命周期事件(§3.5)在调试台呈现 created/fired/completed。

---

## 7. 端到端数据流

```
用户在对话 T 里说「每天早上3点帮我搜AI新闻」
  → Agent(挂了 manage_task)调 manage_task(action=create,
      frequency=daily, time={3,0}, seed="搜集AI新闻并整理")
  → 工具:结构化字段 → RRULE "FREQ=DAILY;BYHOUR=3;BYMINUTE=0";
      建 agent_trigger{user_id=ctx.user, agent=当前Agent,
      config={rrule, timezone, seed_input},
      originating_thread_id=T, context_mode=reuse_thread}
  → Agent 自然语言回执「好,每天3点搜AI新闻,已建」
  → [生命周期事件: task.created]

每天 03:00(该用户时区):
  scheduler _is_rrule_due → due → CAS claim → fire_trigger
    → 独立 scratch thread 跑 Agent,seed="搜集AI新闻并整理"(无 T 的历史)
    → [事件: task.fired] → run 产出结果
  reconcile pass 见 run SUCCESS:
    → trigger_run=SUCCEEDED
    → context_mode=reuse_thread → 结果消息追加进对话 T
    → [事件: task.completed]
  客户端下次打开对话 T → 见到当天 AI 新闻结果
```

用户后续在对话 T 里说「改成下午4点」→ Agent 调 manage_task(action=update, id=…, time={16,0})→ 校验 id 属该用户 → 重建 RRULE → 回执。

---

## 8. 错误处理

- **RRULE / 时区非法**:共享创建路径校验,拒绝并让工具返错给 LLM(Agent 转述给用户),不写坏行。
- **缺具体时间**:工具校验 `time` 必填;缺 → 返「需要具体时间」→ Agent 反问(D-11)。
- **update/cancel 越权**:`id` 不属 ctx.user_id → 拒。
- **触发时 Agent 不可用 / 被 kill switch**:`fire_trigger` 现有 preflight 返 None(不建 thread/run),照旧。
- **run 失败**:reconcile → retrying(退避)→ dead_letter(耗尽 `_MAX_ATTEMPTS=5`);Spec 1 记录不投递(D2 通知属 Spec 2)。
- **有界窗口耗尽**:`_is_rrule_due` 返 None → 自动 `enabled=False`。
- **坏行不阻断 sweep**:scheduler 每 trigger try/except(现有),一坏行不炸整轮。

---

## 9. 测试策略

- **persistence(integration,需 `DOCKER_HOST`)**:partial unique index 两分支(两用户同名放行 / 同用户同名 409 / null-user 按名唯一);`list_by_user`;新列 round-trip。CHECK 约束须真容器验(in-memory 不校验)。
- **scheduler 单测**:`_is_rrule_due` —— daily/weekly/monthly/一次性/有界(UNTIL、COUNT)/跨 DST 各正确;耗尽 → 自动停;时区正确(同一 wall-clock 在不同 tz 触发时刻不同)。
- **manage_task 工具单测**:四 action;结构化字段 → RRULE 映射;缺 time → 反问错误;update/cancel 越权拒;所有权自限(user_id=ctx)。用 monkeypatch 注入 fake `TriggerStore`。
- **自排护栏**:触发来源构建的 Agent registry 不含 `manage_task`(参 P3 `ToolRegistry.register` spy 范式)。
- **D1 投递(integration)**:reuse_thread + 成功 → 消息落 originating thread;fresh_thread_per_run → 不落;失败 → 不落、trigger_run=dead_letter。
- **安全洞**:非 admin 定向他人触发器 GET/PATCH/DELETE → 403;admin 放行;self 放行。
- **control-plane 契约**:改共享创建路径 / store 后跑全库 `ruff check` + CI 同款 pytest 范围(含 control-plane);`manage_task` 改工具签名跑 orchestrator 测。
- **playground**:`pnpm typecheck` + 组件测(对话建任务卡渲染、「立即触发」按钮、结果消息呈现)。
- **手动冒烟**:playground → 对话建任务 → 立即触发 → 看 run + 结果落对话;改时间;取消。

---

## 10. PR 拆分(3~4)

1. **F 地基后端**:migration(唯一约束分 user + rrule/timezone config + originating_thread_id/context_mode 列)+ `TriggerStore.list_by_user` + 共享创建路径 + scheduler 切 RRULE + 安全洞修复 + 生命周期事件。
2. **B 对话工具**:`ManageTaskTool` + `KNOWN_BUILTINS` 挂载 + `TriggerStore` 注入 + 结构化→RRULE + schema 指引 + 自排护栏。
3. **D1 投递**:reconcile pass 投递步(reuse_thread → 追加原对话)+ 失败不投递。
4. **调试台模拟**:playground 对话建任务可见 +「立即触发」+ 结果落回对话 + 生命周期事件呈现。

> ②③ 若体量小可合;①是地基,先合;②③④ 建其上。顺序 ① → ②/③ → ④(④ 依赖 ②③ 能建 + 能投递)。

---

## 11. 后续(Spec 2/3 衔接点,本 Spec 不做但已预留)

- **D2 通知(Spec 2)**:生命周期事件总线已就位;2a 接现有出站 webhook 基建(`webhook_endpoint`/`webhook_delivery`,HMAC+DLQ)做通用事件 webhook(任务通知只是一个 event type);2b 客户端长连接推送为全新基建。
- **A 后台管理面(Spec 3)**:`list_by_user` 已备;用户详情页 `/users/:userId` 任务 tab、按 Agent 筛选、admin 定向建(选 Agent 下拉只列跑过的 Agent、never-run 拦截)。安全洞在 Spec 1 已修,Spec 3 的管理 UI 建在已加固的 API 上。
- **C manifest triggers 弃用(Spec 3)**:manifest triggers 声明全惰性(零消费者);docstring + 字段标 deprecated + UI note。
- **instructional skill(backlog)**:多 Agent 复用 / 租户定制排期话术 / 指引长到需懒读时,发布 `task-scheduling` skill(prompt_fragment + `tool_names:[manage_task]`);因 D-7/D-8 铁律,纯增量,不回改 Spec 1 环境。
