# Agent 配置页 PR2:上下文与压缩组 + FieldRow 范式抽象 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ①终审 fast-follow 清尾(含 FieldRow config-array 范式抽象——必须在第二组复制范式之前);②「上下文与压缩」组 18 字段全可视化(4 个 policy 子区,三段式文案)。

**Architecture:** 纯前端。`FieldDef[]` 配置数组 + 渲染器取代手写 FieldRow 样板;RunBudgetSection 迁移到新范式作校验;ContextGatesSection 用同范式建 4 折叠子区(prune→window→compressor→budget,组成顺序即渲染顺序);ManifestEditor 的 budget 特例分支扩为 `{groupId: Component}` 映射。

**Tech Stack:** 同 PR1(React/antd/vitest/RTL/i18n 双 locale)。

**Spec:** docs/superpowers/specs/2026-07-18-agent-config-page-redesign-design.md(PR2 = 分期段第一组)

## Global Constraints

- PR1 契约全守:FieldRow props 不变(chrome 组件内 i18n / 字段文案 props);YAML round-trip 未投影键保留;新投影字段配 round-trip 测试;i18n 新键三处(interface+en+zh)先 grep 撞键;e2e 选择器契约不动。
- 文案对照真实代码(agent_spec.py 的 policy 定义 + orchestrator/context/*.py 行为),**不抄 stale docstring**;zh 文案按本计划 verbatim,en 忠实对译。
- 每任务:`cd apps/admin-ui && pnpm exec vitest run src/components/manifest-editor && pnpm typecheck`;终门全量 vitest+build。
- IDE 诊断常 stale——以真 tsc/vitest 定论。

---

### Task 1: fast-follow 清尾 + FieldDef 配置数组范式

**Files:**
- Create: `apps/admin-ui/src/components/manifest-editor/groups/field_defs.tsx`(FieldDef 类型 + PolicyFieldList 渲染器)
- Modify: `groups/RunBudgetSection.tsx`(迁移到 FieldDef 范式,行为零变)
- Modify: `FieldRow.tsx` 测试(补 raw===def 徽章分支)、`SettingsSearch.tsx`(选中闪 id 修复)、`FormView.tsx`(栈叠双标题修复)、`form_model.ts`(删孤儿 readRunDeadline/setRunDeadline + 其测试)、locale ×3(删孤儿 agent_form.section_run_deadline/section_run_deadline_help/run_deadline_hint;run_budget.run_deadline_s 默认徽章文案改「0(平台地板)」/「0 (platform floor)」)
- Test: `groups/__tests__/RunBudgetSection.test.tsx`(全过不改语义)、`__tests__/FieldRow.test.tsx`、`__tests__/SettingsSearch.test.tsx`

**Interfaces(Produces,Task 3 消费):**

```tsx
// field_defs.tsx
export interface FieldDef {
  /** manifest 路径,= FieldRow data-field-id,也是 i18n 键的后缀源 */
  fieldId: string;
  /** i18n 命名空间前缀,如 "run_budget.max_iterations" → label/_brief/_impact/_default 四键 */
  i18nKey: string;
  /** 读写键,对应 read/patch helper 返回对象的字段名 */
  valueKey: string;
  kind: "number" | "switch" | "percent";   // percent = 0–1 浮点,InputNumber step 0.05
  effectiveDefault: number | boolean | null; // 显示层 effective 默认(isDefault 判定 + 徽章)
  min?: number;
  max?: number;
}
export interface PolicyFieldListProps {
  defs: readonly FieldDef[];
  values: Record<string, number | boolean | undefined>;  // read helper 输出
  onPatch: (patch: Record<string, number | boolean | undefined>) => void;
  /** i18n 四键约定:`${i18nKey}_label` `_brief` `_impact` `_default`(_impact/_default 可缺省) */
}
export function PolicyFieldList(props: PolicyFieldListProps): ReactNode;
```

- number/percent → InputNumber(清空 null → patch 显式 undefined=回默认删键);switch → antd Switch(**关/开都写显式值**,与默认同值时也写?否——对齐 number 语义:切回默认值=删键。实现:onChange 值===effectiveDefault → patch undefined,否则写值。这保证 YAML 干净、isDefault 徽章一致)。
- isDefault = stored undefined || stored===effectiveDefault(补上 PR1 未测的后半支)。

- [ ] **Step 1: 写 PolicyFieldList 失败测试**(number 清空删键 / switch 切非默认写值、切回默认删键 / percent step / isDefault 两态徽章含 raw===def / data-field-id 渲染)
- [ ] **Step 2: 实现 field_defs.tsx;RunBudgetSection 改为 5 个 FieldDef + PolicyFieldList,现测试 9 个全过(断言不改)**
- [ ] **Step 3: 小修集**:①FieldRow.test 补 raw===def 用例 ②SettingsSearch 选中不闪 id(受控 value,onSelect 立即置空)③FormView 栈叠路径抑制 section 自带 Heading(仅留 data-section-id 子标题;单 section `section=` 路径不动)④删 form_model readRunDeadline/setRunDeadline + 关联测试(先 grep 全库确认无消费者)⑤locale 删 3 孤儿键 + run_deadline 徽章文案改「0(平台地板)」(en "0 (platform floor)"),RunBudgetSection 的 `_default` 值同步
- [ ] **Step 4: `pnpm exec vitest run src/components/manifest-editor` 全过 + typecheck;commit `refactor(admin-ui): FieldDef 配置数组范式 + PR1 fast-follow 清尾`**

### Task 2: form_model 投影 4 个 policy 块(18 字段)

**Files:**
- Modify: `apps/admin-ui/src/components/manifest-editor/form_model.ts`
- Test: `__tests__/form_model.test.ts`(追加)

**Interfaces(Produces,Task 3 消费):**

```ts
// policies 内四块类型化(全部 [k: string]: unknown 保留):
// context_compression?: { enabled?, threshold_pct?, head_keep?, tail_keep?,
//   flush_before_compaction?, max_passes?, max_turns?, max_tokens?,
//   pressure_feedback?, pressure_warn_pct?, [k]: unknown }
// working_memory?: { enabled?, threshold_pct?, max_recent_turns?, keep_first_turn?, [k]: unknown }
// tool_result_prune?: { enabled?, threshold_pct?, recent_tool_results_kept?, [k]: unknown }
// tool_output_budget?: { enabled?, [k]: unknown }
export interface ContextGatesFields {
  ccEnabled?: boolean; ccThresholdPct?: number; ccHeadKeep?: number; ccTailKeep?: number;
  ccFlushBeforeCompaction?: boolean; ccMaxPasses?: number; ccMaxTurns?: number; ccMaxTokens?: number;
  ccPressureFeedback?: boolean; ccPressureWarnPct?: number;
  wmEnabled?: boolean; wmThresholdPct?: number; wmMaxRecentTurns?: number; wmKeepFirstTurn?: boolean;
  prEnabled?: boolean; prThresholdPct?: number; prRecentKept?: number;
  budgetEnabled?: boolean;
}
export function readContextGates(m: AgentManifest): ContextGatesFields;
export function patchContextGates(m: AgentManifest, patch: Partial<ContextGatesFields>): AgentManifest;
```

- patch 语义与 patchRunBudget 同:`"key" in patch` 判定;显式 undefined 删键;块空则删块;不物化空父块;复用 `mergeBlock`。

- [ ] **Step 1: 失败测试**(每块至少一条 round-trip:设值→YAML 含之→回读;未知键保留(policies 下混 approval_required_tools 不丢);undefined 删键+空块删;absent 不物化)
- [ ] **Step 2: 实现(mergeBlock 复用,四块各一次)**
- [ ] **Step 3: vitest form_model 全过 + typecheck;commit `feat(admin-ui): form_model 投影上下文四 policy 块`**

### Task 3: ContextGatesSection(4 折叠子区 + 18 字段文案)

**Files:**
- Create: `groups/ContextGatesSection.tsx`
- Modify: `ManifestEditor.tsx`(budget 特例改 `{budget: RunBudgetSection, context: ContextGatesSection}` 映射)
- Modify: locale interface + en.ts + zh-CN.ts(新命名空间 `context_gates`,~80 键)
- Test: `groups/__tests__/ContextGatesSection.test.tsx`

结构:顶部一段组说明(Text)+ antd Collapse 4 panel(**首个默认展开**,spec 防疲劳):①结果修剪 ②滑动窗口 ③上下文压缩 ④工具输出预算。每 panel = PolicyFieldList(该块 defs)。

组说明(zh,verbatim):「三道门在提示词估算超过 上下文窗口×阈值 时依次介入:①结果修剪(最便宜,旧工具结果塌成引用)→ ②滑动窗口(免 LLM,裁旧轮次)→ ③上下文压缩(LLM 摘要中段)。多数轻超限在前两道解决;阈值分母是模型真实窗口(模型目录)。」

字段文案(zh verbatim;en 忠实对译;`_default` 未注明则为值本身):

**①结果修剪(tool_result_prune)**
- enabled:label「启用结果修剪」brief「超阈值时把旧工具结果塌成一行引用」impact「三道门中最便宜、损失最小的一道:保留全部轮次与推理,只把最近 N 条之外的工具结果换成引用(已外置到工作区的可无损恢复)。只影响本次发送的视图,历史记录不改写。关闭后轻超限将更早落到滑动窗口/压缩。」default true
- threshold_pct:label「触发阈值(窗口占比)」brief「估算提示词达到 上下文窗口×此值 才修剪,低于时零改动」impact「与滑动窗口/压缩共用同一比例形制(各自独立配置)。调低=更早修剪更省;调高=更保真但更依赖后两道门。范围 0–1。」default 0.7;percent
- recent_tool_results_kept:label「保留最近工具结果数」brief「最近 N 条工具结果保持完整,更早的才塌成引用」impact「调大=模型看到更多完整结果但上下文更大;0=超阈值时全部塌引用。」default 4;min 0

**②滑动窗口(working_memory)**
- enabled:label「启用滑动窗口」brief「超阈值时裁剪到首轮+最近 N 轮,免 LLM 调用」impact「只在 用户消息 边界切割,绝不拆散工具调用对。多数轻超限在此解决,省下压缩的摘要 LLM 调用。只影响本次视图,下轮从完整历史重新裁剪。」default true
- threshold_pct:同上形制,default 0.7;percent
- max_recent_turns:label「保留最近轮数」brief「窗口保留的最近用户轮数」impact「调小=更省但丢更多中程上下文(被丢的中段不进摘要——那是压缩的事);长多轮会话建议 ≥10。」default 20;min 1
- keep_first_turn:label「保留首轮」brief「裁剪时始终保留第一轮(任务目标锚)」impact「关闭后长会话可能丢失最初任务描述,一般不建议关。」default true

**③上下文压缩(context_compression)**
- enabled:label「启用上下文压缩」brief「超阈值时用 LLM 把对话中段摘要成一条背景总结」impact「三道门的最后一道。保留头 N 尾 M 条,中间换成〈context-summary〉;被丢中段先冲入长期记忆(若开)。关闭且前两道不够时,超大上下文将直接失败。」default true
- threshold_pct:同形制 default 0.7;percent
- head_keep:label「保留头部条数」brief「摘要时原样保留最前 N 条非系统消息」impact「含记忆注入锚点;调 0 且开启记忆注入时运行期会自动抬到 1。」default 4;min 0
- tail_keep:label「保留尾部条数」brief「摘要时原样保留最近 M 条」impact「尾部是当前工作现场,过小会丢近期细节。」default 6;min 0
- flush_before_compaction:label「压缩前冲入记忆」brief「中段被摘要丢弃前,先把要点写入长期记忆」impact「仅当长期记忆写回开启时生效,否则空转。保多次压缩后关键决策不丢。」default true
- max_passes:label「最大压缩轮数」brief「连续压缩仍超阈值时,最多再试 N 轮」impact「耗尽仍超限 → 运行以上下文溢出失败(不静默兜底)。摘要 LLM 瞬时失败会跳过本轮重试,连续 3 轮失败才失败。」default 3;min 1
- max_turns:label「粗粒度轮数上限」brief「每次调用直接截到最近 N 轮(旧裁剪层),留空=关闭」impact「早于三道门的老机制,默认关闭(留空)。设置后每次调用无条件截断——通常不需要,优先用上面的比例门。」default 留空(关闭);min 1
- max_tokens:label「粗粒度 token 上限」brief「同上,按 token 截,留空=关闭」impact「同 max_turns,设置即无条件生效,一般保持留空。」default 留空(关闭);min 1
- pressure_feedback:label「压力反馈提示」brief「接近窗口上限时,给模型附加一条预算提醒」impact「达 窗口×预警占比 时在最后一条消息附提醒(不动系统提示词,前缀缓存不失效),引导模型自行收敛。低于阈值零改动。」default true
- pressure_warn_pct:label「压力预警占比」brief「触发预算提醒的窗口占比」impact「应低于压缩阈值才有引导意义(默认 0.75 vs 压缩 0.7——注意默认预警晚于压缩,若要提前引导可调低)。」default 0.75;percent

**④工具输出预算(tool_output_budget)**
- enabled:label「启用工具输出预算」brief「本 agent 的大工具输出外置/持久化/修剪总开关」impact「实际生效 = 平台开关 AND 本开关(平台关则此处开也无效)。关闭后大输出不再外置到工作区,超长结果只能靠截断。早期的 bash/exec/http/mcp 溢出外置不受此开关影响。」default true

- [ ] **Step 1: 失败测试**(18 个 data-field-id 渲染于 4 panel/首 panel 默认展开其余折叠/改 threshold_pct InputNumber → manifest `policies.tool_result_prune.threshold_pct`/switch 关 enabled → 写 false、切回 true(=默认)删键/context 组不再渲染 pending hint)
- [ ] **Step 2: 实现 + i18n 三处(先 grep `context_gates` 撞键)**
- [ ] **Step 3: vitest scope 全过 + typecheck;commit `feat(admin-ui): 上下文与压缩组 —— 三道门 18 旋钮可视化`**

### Task 4: 终门

- [ ] **Step 1: `pnpm typecheck && pnpm exec vitest run`(全量)+ `pnpm build` + storybook build**
- [ ] **Step 2: Playwright `pnpm exec playwright test e2e/manifest-edit.spec.ts e2e/manifest-editor.spec.ts e2e/manifest-model-select.spec.ts e2e/agent-mcp-picker.spec.ts`(本地可跑则跑,不可跑明说)**
- [ ] **Step 3: commit(若有收尾修正)`test(admin-ui): PR2 终门`**

## Self-Review 已核

- fast-follow 6 项全落 Task 1(config-array/徽章测/徽章文案/闪 id/孤儿清理/双标题);drop 3 项不做 ✓
- FieldDef.kind 覆盖 PR2 全部控件形态(number/switch/percent);Task 3 的 defs 全部可用 Task 1 接口表达 ✓
- 18 字段 × 四键文案 zh verbatim 无 TBD;switch 删键语义在 Task 1 定义、Task 3 测试引用一致 ✓
- 文案事实性:flush 仅记忆写回开时生效(agent_spec:686-690)/max_turns None=关闭老中间件(HX-A5)/pressure 0.75>0.7 默认预警晚于压缩(如实写)/budget=platform AND agent ✓
