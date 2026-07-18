# Agent 配置页重组(运维向全量可视化)— 设计

**状态**:设计定案(2026-07-18)
**类型**:前端重构 + 增量字段补齐(admin-ui manifest editor)
**决策人**:用户(四项拍板,见决策纪要)

## 背景 / 问题

1. **可视化缺口**:AgentSpec 共 39 block / ~150 叶子字段,现表单只覆盖一部分;运维要调的关键旋钮(`workflow.max_iterations`、`policies.max_no_progress` 等)只能切 YAML 手写。真实事故:worker 撞步数顶被截断,操作员在界面上找不到任何步数设置。
2. **结构混乱**:11 个横向 tab(basic/model/prompt/tools/mcp/knowledge/skills/subagents/memory/governance/defenses)+ yaml,分区按实现历史堆积而非运维心智;governance 一个 tab 混着步数预算、审批、worker 开关。
3. **说明缺失**:页面是给运维用的,每个设置/开关需要讲清**作用、影响、默认值、生效条件**;现状多数字段无解释或只有一句 tooltip。

## 决策纪要(用户拍板)

1. **结构重组**(非增量修补):按运维心智重新分区。
2. **全量 schema 可视化**:AgentSpec 每个字段最终都进表单;**但必须合理布局、不产生疲劳**。
3. **布局 = 左树 + 右详情 + 全局搜索**(VS Code 设置风):左侧分组导航树、右侧当前组字段、顶部搜索直达。
4. **分期交付**:PR1 = 布局骨架 + 现有字段迁入新分组 + 试点组;后续 PR 逐组补缺失字段与文案。

## 分组树(10 组)

| # | 组 | 收纳(schema 来源) | 旧 tab 映射 |
|---|---|---|---|
| 1 | 基础 | metadata(name/version/labels)、description、extends、tenant_config | basic |
| 2 | 模型与路由 | model 16 项(fallback/thinking/effort/cache/vision 开关)、routing.rules、reflection、vision(VL) | model |
| 3 | 提示词与输出 | system_prompt(jinja/variables)、dynamic_context、output_schema | prompt |
| 4 | 能力 | tools(builtin/http/mcp)、skills(+auto_attach_evolved_skills)、knowledge、subagents、dynamic_workers | tools+mcp+knowledge+skills+subagents+governance(部分) |
| 5 | 记忆 | memory.short_term/long_term(8 旋钮)、policies.memory_consolidation | memory |
| 6 | **运行预算与超时** | workflow.max_iterations、workflow.early_stop、policies.max_no_progress、policies.run_deadline_s、stream_deadline_s、idle_timeout_s(未来成本熔断落此) | governance(部分)+ 散落 |
| 7 | 上下文与压缩 | policies.context_compression(10)、working_memory(4)、tool_result_prune(3)、tool_output_budget | (无——现全缺) |
| 8 | 安全与防护 | defenses(7)、policies.approval_*、tool_use_enforcement、sandbox.network(egress/allow/deny)、policies.pii/safety/rate_limit | defenses+governance(部分) |
| 9 | 沙箱与资源 | sandbox(runtime/image/resources/filesystem)、code | (无——现全缺) |
| 10 | 触发器与可观测 | triggers、observability(trace/log_level/redact)、policies.trajectory_recording、cache | (无/散落) |

组内二级折叠子区(如"模型与路由"内:主模型 / 回退链 / 按条件路由 / 反思 / 视觉)。**默认展开首个子区,其余折叠**——防疲劳。

## 字段行契约(FieldRow)

每个字段统一渲染为:

```
[label]  [控件]           [默认值徽章(未改=灰"默认",已改=蓝值)]
└ 一行作用(永远可见)
  [展开] 影响说明:调大/调小后果、生效条件(如"仅 store_backend=sql 生效")、关联字段
```

- 文案三段式:**作用(一行)/ 影响 / 默认与条件**。zh + en 双 locale,i18n 键规范 `agent_form.<field>_label|_brief|_impact`。
- FieldRow 带 `data-field-id`(= manifest 路径,如 `workflow.max_iterations`)供搜索定位/高亮。

## 全局搜索

- 顶部搜索框;索引 = 静态注册表(分组/子区/字段的 label + brief 文案 + manifest 路径),纯前端。
- 命中 → 下拉列表(组 > 字段);选中 → 切组 + 滚动到 FieldRow + 短暂高亮。
- PR1 先做组/子区级 + 试点组字段级;字段级覆盖随各组 PR 落地自然扩展(注册表随组补齐)。

## 兼容约束(命门)

1. **YAML escape hatch 不变**:右上切换 Form↔YAML;round-trip 语义照旧(form_model 投影,**未投影字段原样保留不丢**——全量化完成前 YAML 仍是超集兜底)。
2. **LeadingTab 机制保留**:模板市场元数据页把自有 tab 前置 + 折叠一个 manifest 分区进自己 tab;新布局把 LeadingTab 渲染为树顶部特殊节点,内容常驻挂载(内嵌 antd Form 状态不丢)。
3. e2e(manifest-edit/manifest-editor.spec)与单测按新导航更新,**断言语义不降**(每个原断言在新 UI 有对应)。
4. FormView(996 行)按组拆 per-group 文件(顺带清掉 memory 里 DefensesSection 拆分旧债);拆分是**机械搬移**,字段控件逻辑不改。

## 分期

- **PR1(骨架)**:GroupNav 树 + 搜索(组级)+ YAML 切换 + LeadingTab 兼容 + 现 11 tab 内容按映射迁入 10 组(机械)+ **试点组"运行预算与超时"完整落地**(FieldRow 契约 + 新增 `workflow.max_iterations`、`policies.max_no_progress` 进表单 + 迁入 run_deadline/stream/idle + 三段式文案)。试点组同时立 per-group 范式。
- **PR2+**(每组一 PR,按运维价值排序):上下文与压缩 → 安全与防护(补 sandbox.network)→ 沙箱与资源 → 记忆(补 long_term 全旋钮)→ 模型与路由(补 rate_limit_rpm/context_window 覆盖等)→ 触发器与可观测 → 收尾全量核对(schema 字段 × 表单覆盖清单打勾)。
- 每组 PR 交付 = 字段 + 投影(form_model round-trip 测试)+ 三段式文案(zh/en)+ 注册表条目(搜索自动覆盖)。

## 测试

- 每 PR:`pnpm typecheck` + vitest(组件 + form_model round-trip:新字段设值→YAML→回读不丢、未知字段保留)+ 既有 e2e 更新后过。
- PR1 加导航/搜索/LeadingTab 专测。

## 非目标

- 平台级设置页(worker 步数夹等 env 项)——另一页面,不在本 spec。
- 成本熔断字段本身(B3 独立 spec;落地后进组 6)。
- 后端/schema 改动(纯前端投影;schema 缺的字段如成本熔断不在此加)。
