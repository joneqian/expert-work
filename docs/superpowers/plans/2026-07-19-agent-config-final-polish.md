# Agent 配置页 PR8:收尾 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 全量核对清单(docs/superpowers/2026-07-19-agent-config-coverage-audit.md,已随本分支落库)裁定的收尾:补 2 个活字段控件(`workflow.type`/`dynamic_context.inject_current_date`)+ 4 处真相 note(extends+tenant_config/custom_reminders/内置工具 config/early_stop+builder)。

**Architecture:** T1 = RunBudgetSection 加 workflow.type select + workflow 保留字段 note(RunBudgetFields 扩展,复用既有 workflow 块 patch);T2 = FormView prompt section 加 inject_current_date 开关(老式控件风格,与该组一致)+ custom_reminders hint;T3 = basic/capabilities 两处 note + 终门。

**Spec:** docs/superpowers/specs/2026-07-18-agent-config-page-redesign-design.md(收尾段「全量核对」)

## Global Constraints

- PR1-PR7 契约全守(FieldRow/PolicyFieldList props 不变;YAML round-trip;i18n 三处先 grep 撞键;测试环境 en locale;IDE 诊断 stale 只认真 tsc/vitest;CJK grep -a)。
- `spec.workflow` 与 `spec.dynamic_context` 都是 default_factory 可选块——标准 mergeBlock drop-empty 语义(非 required/存在语义)。
- 老式 vs 新式控件:prompt 组是 stacked FormView 老式段,新开关按该段既有风格(Switch+hint 行,参考 af-prompt-jinja),不引入 FieldRow;budget 组是 curated pane,新 select 走 FieldDef。
- 文案对照真实运行期代码(下节),照抄 verbatim。

## 运行期语义事实(2026-07-19 溯源,文案依据)

- `workflow.type`(Literal react|plan_execute|custom = "react"):唯一消费 = agent_factory.py:744/:751,`== "plan_execute"` 时前置 planner 节点(用 routing 的 planning 规则模型,无规则用主模型);**`custom` 无任何消费分支,等同 react**;`workflow.early_stop`/`workflow.builder` 全库零消费者。
- `dynamic_context.inject_current_date`(bool=True):agent_factory.py:901——构建时把当天日期块注入系统提示词(按日历日缓存稳定,具体时刻由沙箱内取);关=Agent 不知道今天日期。
- `dynamic_context.custom_reminders[]`(source/template):活字段(DynamicContextSpec),结构化列表不建控件走 YAML。
- `spec.extends`:模板继承引用,表单只读它显示 defenses 提示,编辑走 YAML。
- `tenant_config`:`compliance_pack`/`isolation_level`/`data_residency` 全库零消费者(isolation 注释明说 M0 沙箱恒 shared);`audit_retention_days` 平台侧暂用全局默认(worker 注释"until D.3");`pii_fields` 有活消费者但经租户记录路径——**T3 实现者须 grep 验证 manifest 的 `spec.tenant_config` 块本身是否被任何运行时读取**,note 措辞只写验证过的事实。

---

### Task 1: workflow.type select + workflow 保留字段 note(运行预算组)

**Files:**
- Modify: `apps/admin-ui/src/components/manifest-editor/form_model.ts`(RunBudgetFields 扩 `workflowType?: string`;readRunBudget/patchRunBudget 增该键,workflow 块 mergeBlock 复用)
- Modify: `groups/RunBudgetSection.tsx`(FieldDef select + 底部 note,testid `budget-workflow-note`)
- Modify: locale interface + en.ts + zh-CN.ts(`run_budget` 命名空间追加)
- Test: `__tests__/form_model.test.ts` + `groups/__tests__/RunBudgetSection.test.tsx` 追加

FieldDef:

```ts
{ fieldId: "workflow.type", i18nKey: "run_budget.wf_type", valueKey: "workflowType",
  kind: "select", effectiveDefault: "react", options: ["react", "plan_execute", "custom"],
  optionLabelKey: "run_budget.wf_type_opt" }
```

文案(zh verbatim;en 忠实对译):
- `wf_type_label`「工作流类型」
- `wf_type_brief`「react=边想边做的经典循环;plan_execute=先由规划模型出完整计划再逐步执行」
- `wf_type_impact`「plan_execute 适合长链条任务(报告生成等),规划所用模型由模型组的 routing planning 规则指定,无规则时用主模型。custom 当前无专属实现,行为等同 react。」
- `wf_type_default`「react」
- `wf_type_opt_react`「react(边想边做)」/`wf_type_opt_plan_execute`「plan_execute(先规划后执行)」/`wf_type_opt_custom`「custom(未接线)」
- `workflow_note`(note):「workflow 的 early_stop 与 builder 为保留字段:通过校验但运行时不读取,留在 YAML 中无害。」

- [ ] **Step 1: 失败测试**(select 渲染 3 option;选 plan_execute → `spec.workflow.type === "plan_execute"`;选回 react(默认)→ 键删且 workflow 块内 max_iterations 共存不丢;round-trip 经 YAML;note testid 渲染)
- [ ] **Step 2: 实现 + i18n**
- [ ] **Step 3: scope vitest + typecheck;commit `feat(admin-ui): 运行预算组补 workflow.type + 保留字段 note`**

### Task 2: inject_current_date 开关 + custom_reminders hint(提示词组)

**Files:**
- Modify: `apps/admin-ui/src/components/manifest-editor/FormView.tsx`(prompt section 尾部加 Switch,testid `af-inject-current-date`,+ hint;再加 `af-dynamic-context-note` 一行说明 custom_reminders)
- Modify: `form_model.ts`(`readInjectCurrentDate(m): boolean | undefined` RAW / `setInjectCurrentDate(m, v: boolean): AgentManifest`——true(默认)删键、false 写入;dynamic_context 块 mergeBlock 标准语义;custom_reminders 等未知键保留)
- Modify: locale 三处(`agent_form` 命名空间追加,先 grep 撞键)
- Test: `__tests__/form_model.test.ts` + FormView 既有测试文件追加

文案(zh verbatim;en 忠实对译):
- `inject_date_label`「注入当前日期」
- `inject_date_hint`「构建时把当天日期写进系统提示词(默认开,按日缓存稳定)。关闭后 Agent 不知道今天几号——仅适合与日期无关的 Agent。」
- `dynamic_context_note`「自定义提醒(dynamic_context.custom_reminders)为结构化列表,请在 YAML 视图编辑。」

- [ ] **Step 1: 失败测试**(开关默认显示开(值缺省);拨关 → `spec.dynamic_context.inject_current_date === false`;拨回开 → 键删且 custom_reminders 未知键保留;round-trip;note 渲染)
- [ ] **Step 2: 实现 + i18n**
- [ ] **Step 3: scope vitest + typecheck;commit `feat(admin-ui): 提示词组补 inject_current_date 开关 + 动态上下文 note`**

### Task 3: basic/capabilities 真相 note + 终门

**Files:**
- Modify: `FormView.tsx`(basic section 尾部加 note testid `af-basic-yaml-note`;tools section 尾部加 note testid `af-tools-config-note`)
- Modify: locale 三处(`agent_form` 追加)
- Test: FormView 测试追加两 note 断言

**先验证**:grep `spec.tenant_config`/`tenant_config` 在 services/ 的运行时读取(排除 schema/测试)——若 manifest 块整体无读取,note 用下述措辞;若有读取,按实际改写(只写验证过的事实)并在报告里说明。

文案(zh verbatim,以验证结果为准;en 忠实对译):
- `basic_yaml_note`「extends(模板继承)在 YAML 视图编辑。tenant_config 的 compliance_pack / isolation_level / data_residency 为保留字段:通过校验但运行时不读取(沙箱隔离当前恒为 shared);audit_retention_days 暂由平台全局配置决定。」
- `tools_config_note`「内置工具的 per-tool 配置(tools[].config,如搜索引擎/结果数)与 web_search 之外的内置工具项请在 YAML 视图编辑。」

终门:
- [ ] **Step 1: 失败测试 → 实现 note + i18n;commit `feat(admin-ui): basic/capabilities 真相 note`**
- [ ] **Step 2: `pnpm typecheck && pnpm exec vitest run`(全量)+ `pnpm build` + storybook + Playwright manifest 2 spec;有修才 commit `test(admin-ui): PR8 终门`**

## Self-Review 已核

- workflow.type「custom 未接线」「planning 规则挂钩」均溯源实证;early_stop/builder 零消费者双 grep 确认 ✓
- inject_current_date 消费点+缓存语义照 agent_factory.py:895-903 注释 ✓
- tenant_config 措辞留验证门(pii_fields 歧义不乱写)✓
- 控件风格随组(curated=FieldDef/stacked=老式),不混 ✓
- 核对清单文档已随分支落库,PR8 完成后 epic 关账 ✓
