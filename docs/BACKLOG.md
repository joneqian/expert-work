# 产品待办清单(Backlog)

> 建立:2026-07-19,配置页重组 epic(#1014-#1022)+ 收尾波(#1023/#1024)全清后立项。
> 每个 epic 开工前走 brainstorm → spec → 分 PR;完成后更新本表。

## 优先级总览

| 优先级 | 项目 | 状态 | 体量预估 |
|---|---|---|---|
| **P0** | B2 worker 可观测性 | **✅ 已完成**(#1026 后端桥 + #1027 前端子时间线,2026-07-20) | 2 PR |
| **P1** | B3 成本/token 熔断(+平台设置页 worker 步数夹) | **✅ 已完成**(PR1 主链 #1028 + PR2 dynamic_worker 平台配置节 #1029,2026-07-20) | 2 PR |
| **P2** | 触发器 user 维度重设计 | 待开工 | 2-3 PR |
| **P3** | 自适应规划 | 待开工 | spec 后定 |
| **P5** | 记忆模块补缺(溯源 + 新鲜度锚点) | 待开工 | 1 PR |

**排序逻辑:看得见 → 兜得住 → 放得开 → 跑得巧。**
可观测先行(所有后续 epic 的排障都受益);成本护栏必须在触发器之前(自动任务无人盯,放开创建入口前先有熔断兜底);自适应规划是体验优化非刚需,graph 改动风险最高,收益需实测,压尾。

**执行顺序(2026-07-20 用户定):B3 PR2 → P5 → P2 → P3。** P5 小而独立,且含正确性缺口(`last_used_at` 断锚使 consolidator 清除保护形同虚设),插在两个大 epic 之前;B3 先收尾避免半截 epic 挂着。

---

## P0 — B2:worker 可观测性

**问题**:`spawn_worker` 是黑箱——worker 的工具调用/步进事件不冒泡进父 run 事件流,调试台只见父 run 长时间零事件。已造成真实误判:run 141aa72d 被判"卡住",实际 worker 在正常干活 37 分钟后跑成功。

**目标**:worker 事件桥接进父 run 事件流(带 worker 标识/嵌套层级),调试台时间线可见 worker 在干什么。

**为什么 P0**:运维排障刚需;体量最可控;后续每个 epic(尤其触发器自动任务)的调试都建立在"看得见"之上。

**spec 要点**:事件格式(worker 标识字段)/嵌套深度(worker 再 spawn worker)/事件量控(worker 事件多,考虑采样或分级)/前端时间线渲染。

## P1 — B3:成本/token 熔断

**问题**:运行护栏三件套(步数 `max_iterations` / 时长 `run_deadline_s` / **成本**)缺成本这条——失控 run 只能靠步数或时长兜,token 烧穿无独立闸。

**目标**:per-run(或 per-agent)token/成本上限,超限优雅终止(非裸断);schema + runtime + 配置页 UI(运行预算组设计时已预留位置)。

**为什么 P1**:安全护栏;触发器 epic 放开"终端用户对话建自动任务"之前必须到位(自动任务无人盯,失控放大)。计量基础已有(`token_usage` 表)。

**捎带 small**:平台设置页补 `dynamic_worker_max_iterations`(worker 步数夹,现只 env 可调)——同为运行护栏 UI,搭车同波。

**spec 要点**:计量点(router 层累计?)/口径(token 数 vs 换算成本)/超限行为(优雅收尾轮次 vs 立即终止)/与既有三门(prune/window/compress)的关系。

## P2 — 触发器 user 维度重设计

**问题**:触发器该挂 user 维度(同一 Agent 每 user 隔离实例,任务要求各不同),现状半成品:`agent_trigger` 已有 `user_id` 列、触发时以该 user 身份跑 run;但同名唯一约束不分 user(两用户同名任务 409)、admin 无法替目标用户建、无对话建任务工具、无 per-user 管理 UI。

**目标**:①终端用户对话建任务(Agent 侧 schedule 工具)②后台按 user 管理(用户详情页任务 tab + TriggersList user 列/过滤 + admin 定向建/停/删)③唯一约束分 user;manifest 的 triggers 声明(agent 级无主粒度)随本 epic 弃用(docstring 纠正 + 字段 deprecated,`image_variant` 先例)。

**为什么 P2**:功能价值大,但"对话建任务"= 终端用户能创建自动消耗,须待 B3 熔断到位再放开;一期切法(管理面先 vs 对话工具先)brainstorm 时定。

## P3 — 自适应规划

**问题**:`workflow.type` 的 react/plan_execute 是构建时死开关:plan_execute 对简单任务也强制先出完整计划(多一次规划调用,更慢更贵);react 遇长链任务易跑偏。二者不该互斥。

**方向候选**(brainstorm 定):①规划作为工具(Agent 自判复杂度调 make_plan,Claude Code todo 思路)②入口轻量复杂度路由 ③react 中途重规划。全是 graph 构建后端改动。

**为什么 P3**:体验优化非刚需;graph 核心路径改动风险最高;收益需评测数据支撑。压尾,且可借 B2 落地后的可观测数据评估现状痛点大小。

## P5 — 记忆模块补缺(溯源 + 新鲜度锚点)

**问题**(2026-07-20 排查发现,均已代码实锤):
1. **`last_used_at` 检索后从不刷新**——只在写入时落一次。后果:①CM-6 时间衰减的"use keeps fresh"锚点失效,退化成纯创建年龄衰减;②consolidator 清除保护"从未被检索 = `last_used_at ≤ created_at + 1min`"恒为真,高频召回的记忆也进清除候选(仅剩年龄+未复核两道兜底)。
2. **记忆无 run 级溯源**——`MemoryItem` 只有 `source_thread_id`,追不到哪个 run 触发写入。语义定为 `source_run_id` = 触发抽取的 run(run-end writeback / 压缩前 flush / DLQ 重试三条写入路径都透传)。

**目标**:①检索命中后批量 bump `last_used_at`(一条 UPDATE);②protocol 加 `source_run_id` + migration 一列 + 三写入点透传 + 前端记忆 tab 可跳 run 详情。

**为什么插 P2 前**:体量小(1 PR)、独立;第 1 条是正确性缺口非增强。
