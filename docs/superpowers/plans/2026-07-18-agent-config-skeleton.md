# Agent 配置页骨架(PR1)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地配置页新布局骨架:左树(10 组)+ 右详情 + 组级搜索 + YAML 切换 + LeadingTab 兼容;现 11 个 form section 机械迁入新分组;试点组「运行预算与超时」按 FieldRow 契约完整落地(新增 `workflow.max_iterations`、`policies.max_no_progress` 可视化)。

**Architecture:** 纯前端(apps/admin-ui)。`ManifestEditor` 由横向 tab 换为 GroupNav 树 + 内容面板;`FormView` 的 11 个 section 保持原实现,新增 `sections: FormSection[]` 堆叠渲染供分组复用;试点组用新 `FieldRow` 组件立 per-group 范式。`form_model` 投影加两字段,round-trip 不丢未知字段。

**Tech Stack:** React + antd + vitest + @testing-library/react;i18n 双 locale(en.ts/zh-CN.ts + 接口类型);Playwright e2e。

**Spec:** docs/superpowers/specs/2026-07-18-agent-config-page-redesign-design.md

## Global Constraints

- YAML escape hatch 语义不变:Form↔YAML round-trip 照旧,未投影字段**原样保留**(`[k: string]: unknown` 范式)。
- LeadingTab 机制保留:内容常驻挂载(隐藏不卸载),`foldSection` 语义不变。
- 现有字段控件逻辑**零改动**(迁移是机械搬移);每个被移除的旧断言在新 UI 有对应新断言。
- i18n 三处齐改:locale 接口类型 + en.ts + zh-CN.ts;新键先查重(同 object 重复键 esbuild 静默覆盖)。
- 每任务验证:`cd apps/admin-ui && pnpm typecheck && pnpm exec vitest run <涉及文件>`;终门跑全量 vitest + typecheck。
- 编辑器对新文件的诊断可能大面积 stale——以真 `pnpm typecheck`(tsc -b)+ vitest 定论。

---

### Task 1: 分组注册表 groups.ts + GroupNav 组件

**Files:**
- Create: `apps/admin-ui/src/components/manifest-editor/groups.ts`
- Create: `apps/admin-ui/src/components/manifest-editor/GroupNav.tsx`
- Test: `apps/admin-ui/src/components/manifest-editor/__tests__/GroupNav.test.tsx`

**Interfaces:**
- Produces: `ConfigGroup`、`CONFIG_GROUPS`(10 组,含旧 section 映射 + 搜索关键词)、`GroupNav`(props: `active: string; onSelect: (id: string) => void; leading?: { value: string; label: string }`)。
- Task 2/3 消费 `CONFIG_GROUPS`;Task 3 消费 `searchGroups`。

- [ ] **Step 1: 写注册表**

```ts
// groups.ts
import type { FormSection } from "./FormView";

export interface ConfigGroup {
  id: string;                 // 稳定 id,亦作树节点 key
  labelKey: string;           // i18n: manifest_editor.group_<id>
  /** 该组堆叠渲染的既有 FormView sections(迁移映射,机械)。 */
  sections: readonly FormSection[];
  /** 组级搜索关键词(中英混合,小写匹配)。 */
  keywords: readonly string[];
}

export const CONFIG_GROUPS: readonly ConfigGroup[] = [
  { id: "basic", labelKey: "manifest_editor.group_basic", sections: ["basic"], keywords: ["名称", "版本", "描述", "name", "version", "extends"] },
  { id: "model", labelKey: "manifest_editor.group_model", sections: ["model"], keywords: ["模型", "回退", "路由", "思考", "model", "fallback", "thinking", "vision"] },
  { id: "prompt", labelKey: "manifest_editor.group_prompt", sections: ["prompt"], keywords: ["提示词", "输出", "jinja", "prompt", "schema"] },
  { id: "capabilities", labelKey: "manifest_editor.group_capabilities", sections: ["tools", "mcp", "knowledge", "skills", "subagents"], keywords: ["工具", "技能", "知识库", "子agent", "worker", "tools", "mcp", "skills"] },
  { id: "memory", labelKey: "manifest_editor.group_memory", sections: ["memory"], keywords: ["记忆", "memory", "recall"] },
  { id: "budget", labelKey: "manifest_editor.group_budget", sections: [], keywords: ["步数", "超时", "预算", "max_iterations", "deadline", "idle", "no_progress"] },
  { id: "context", labelKey: "manifest_editor.group_context", sections: [], keywords: ["压缩", "上下文", "compression", "working memory", "prune"] },
  { id: "security", labelKey: "manifest_editor.group_security", sections: ["defenses", "governance"], keywords: ["防护", "审批", "安全", "defense", "approval", "egress"] },
  { id: "sandbox", labelKey: "manifest_editor.group_sandbox", sections: [], keywords: ["沙箱", "资源", "镜像", "sandbox", "cpu", "image"] },
  { id: "observability", labelKey: "manifest_editor.group_observability", sections: [], keywords: ["触发器", "可观测", "trigger", "trace", "log"] },
];
```

注:`budget` 组 sections 空——其内容由 Task 6 的试点 RunBudgetSection 直渲(非 FormView section);`context`/`sandbox`/`observability` PR1 为空组,树节点显示但右侧渲染"本组设置将在后续版本可视化,当前请用 YAML 编辑"占位(i18n `manifest_editor.group_pending_hint`)。governance 现含 run_deadline/dynamic_workers/approval 混合内容,PR1 整体挂 security 组(dynamic_workers 归属能力组的挪动随 PR2 拆 governance 时做——机械迁移优先,不拆内容)。

- [ ] **Step 2: 写 GroupNav 失败测试**(渲染 10+树节点、点击回调、active 高亮、leading 节点前置)
- [ ] **Step 3: 实现 GroupNav**(antd `Menu` mode="inline",leading 节点在最上,数据全来自 `CONFIG_GROUPS`,`data-testid="cfg-nav-<id>"`)
- [ ] **Step 4: `pnpm exec vitest run src/components/manifest-editor/__tests__/GroupNav.test.tsx` → PASS;commit `feat(admin-ui): 配置页分组注册表 + GroupNav`**

### Task 2: ManifestEditor 布局切换(树+面板+YAML+LeadingTab)

**Files:**
- Modify: `apps/admin-ui/src/components/manifest-editor/ManifestEditor.tsx`(MANIFEST_TABS 行退役,换 GroupNav 布局)
- Modify: `apps/admin-ui/src/components/manifest-editor/FormView.tsx`(props 加 `sections?: FormSection[]`,与既有 `section` 互斥,堆叠渲染 `sections.map(s => sectionsRecord[s])`,每段前加子区标题 anchor `data-section-id`)
- Modify: `apps/admin-ui/src/i18n/locales/en.ts`、`zh-CN.ts` + locale 接口(`group_basic`…`group_observability`、`group_pending_hint`,YAML 键沿用 `tab_yaml`)
- Test: 更新 `__tests__/ManifestEditor.test.tsx`

**Interfaces:**
- Consumes: Task 1 `CONFIG_GROUPS`/`GroupNav`。
- Produces: 新布局 DOM 契约——`data-testid="cfg-nav"`(左树)、`data-testid="cfg-pane"`(右面板)、`data-testid="cfg-yaml-toggle"`(右上 YAML 按钮);LeadingTab 内容常驻于面板顶部节点。

- [ ] **Step 1: 更新 ManifestEditor 测试为新契约**(树存在、默认组 basic、点 capabilities 组右侧渲染 tools+mcp+…五段、YAML 切换 round-trip 保持、LeadingTab 显示且切组不卸载、foldSection 折叠语义保留)→ RED
- [ ] **Step 2: 实现布局**:左 `GroupNav` 固定宽 200,右面板 `FormView sections={group.sections}`;空组渲染 pending hint;`yaml` 由树节点改为右上角切换按钮(状态语义与原 tab 相同:切换时序列化/解析+校验失败回弹,逻辑原样搬);LeadingTab 渲染为树顶部节点+面板常驻(`display:none` 隐藏非 active,复用原实现)。
- [ ] **Step 3: vitest ManifestEditor + FormView 相关 → PASS**
- [ ] **Step 4: `pnpm typecheck` → PASS;commit `feat(admin-ui): 配置页左树+右详情布局(替换横向 tab)`**

### Task 3: 组级搜索 SettingsSearch

**Files:**
- Create: `apps/admin-ui/src/components/manifest-editor/SettingsSearch.tsx`
- Modify: `groups.ts`(加 `searchGroups(q: string): ConfigGroup[]`——labelKey 解析文案 + keywords 小写包含匹配)
- Modify: `ManifestEditor.tsx`(顶部挂搜索框,选中 → `onSelect(group.id)`)
- Test: `__tests__/SettingsSearch.test.tsx`

- [ ] **Step 1: 失败测试**(输入"步数"→ 命中 budget 组;输入"mcp"→ capabilities;选中触发切组;空查询无下拉)
- [ ] **Step 2: 实现**(antd `AutoComplete`,`data-testid="cfg-search"`;i18n placeholder `manifest_editor.search_placeholder` 双 locale)
- [ ] **Step 3: vitest → PASS;commit `feat(admin-ui): 配置页组级搜索`**

### Task 4: FieldRow 组件(字段行契约)

**Files:**
- Create: `apps/admin-ui/src/components/manifest-editor/FieldRow.tsx`
- Test: `__tests__/FieldRow.test.tsx`

**Interfaces:**
- Produces:

```tsx
export interface FieldRowProps {
  fieldId: string;          // manifest 路径,如 "workflow.max_iterations" → data-field-id
  label: string;
  brief: string;            // 一行作用,永远可见
  impact?: string;          // 展开的影响说明(调大/调小后果、生效条件)
  defaultValue?: string;    // 徽章文案;当前值===默认 → 灰"默认 <v>",否则蓝当前值
  isDefault: boolean;
  children: ReactNode;      // 控件本体
}
```

- [ ] **Step 1: 失败测试**(label/brief 渲染、impact 初始折叠点击展开、默认徽章两态、`data-field-id` 透出)
- [ ] **Step 2: 实现**(布局按 spec「字段行契约」;展开用 antd `Collapse` ghost;徽章 `Tag`)
- [ ] **Step 3: vitest → PASS;commit `feat(admin-ui): FieldRow 字段行契约组件`**

### Task 5: form_model 投影 max_iterations + max_no_progress

**Files:**
- Modify: `apps/admin-ui/src/components/manifest-editor/form_model.ts`
- Test: `__tests__/form_model.test.ts`(追加)

**Interfaces:**
- Produces: `AgentManifest.spec.workflow?: { max_iterations?: number; [k: string]: unknown }`;`policies` 内加 `max_no_progress?: number`;读写 helper `readRunBudget(m): { maxIterations?: number; maxNoProgress?: number; runDeadlineS?: number; streamDeadlineS?: number; idleTimeoutS?: number }` + `patchRunBudget(m, patch): AgentManifest`(不可变更新,保留未知键)。run_deadline/stream/idle 三字段沿用现有读写点(如已有则 helper 只聚合)。

- [ ] **Step 1: 失败测试**——round-trip 三连:
```ts
it("workflow.max_iterations projects and round-trips", () => {
  const m = parse(`spec:\n  workflow:\n    max_iterations: 40\n    type: react\n`);
  expect(readRunBudget(m).maxIterations).toBe(40);
  const next = patchRunBudget(m, { maxIterations: 50 });
  expect(next.spec?.workflow?.max_iterations).toBe(50);
  expect(next.spec?.workflow?.type).toBe("react"); // 未投影键保留
});
it("max_no_progress round-trips under policies", () => { /* 同型 */ });
it("absent workflow stays absent until set", () => { /* patch 未设值不产生空块 */ });
```
- [ ] **Step 2: 实现投影 + helper(不可变,spread 保留 `[k]: unknown`)**
- [ ] **Step 3: vitest form_model → PASS;commit `feat(admin-ui): form_model 投影 max_iterations/max_no_progress`**

### Task 6: 试点组「运行预算与超时」RunBudgetSection

**Files:**
- Create: `apps/admin-ui/src/components/manifest-editor/groups/RunBudgetSection.tsx`
- Modify: `ManifestEditor.tsx`(budget 组渲染 RunBudgetSection)
- Modify: `FormView.tsx`(从 governance section **移除** run_deadline 块——搬入试点组,旧位置删)
- Modify: locale 接口 + `en.ts` + `zh-CN.ts`
- Test: `__tests__/RunBudgetSection.test.tsx`

五个字段全用 FieldRow,文案三段式(zh 例,en 对译):
- `workflow.max_iterations`:label「最大步数」;brief「一次运行最多执行的思考+动作步数」;impact「超限后强制收尾:模型被要求直接总结、不再调用工具,产出可能不完整。调大适合研究/多工具长任务(参考:重任务 40-60),调小控制成本。子 worker 实际步数 = min(本值, 平台 worker 上限)」;default「30」。
- `policies.max_no_progress`:label「无进展停机」;brief「连续 N 步无实质进展即提前收尾,0 = 关闭」;impact「防模型原地打转烧步数;过小可能误伤合法重试。建议 3-5」;default「0(关闭)」。
- `policies.run_deadline_s`:label「运行墙钟上限」;brief「整次运行(含子 agent)的秒数上限,0 = 用平台地板(默认 1 小时)」;impact(从现 governance 文案迁移+补条件说明)。
- `stream_deadline_s` / `idle_timeout_s`:沿用既有文案迁入,brief 补「LLM 单次调用首 token / token 间隔超时」。

- [ ] **Step 1: 失败测试**(五 FieldRow 渲染、改最大步数 onChange 产出 `spec.workflow.max_iterations`、默认徽章、governance 组不再含 run_deadline)
- [ ] **Step 2: 实现 + 迁移 + 文案三处 i18n**
- [ ] **Step 3: vitest RunBudgetSection + FormView + ManifestEditor → PASS;commit `feat(admin-ui): 运行预算与超时试点组(max_iterations/max_no_progress 可视化)`**

### Task 7: e2e 更新 + 终门

**Files:**
- Modify: `apps/admin-ui/e2e/manifest-edit.spec.ts`、`apps/admin-ui/e2e/manifest-editor.spec.ts`(tab 选择器 → `cfg-nav-*` / `cfg-yaml-toggle`;逐断言对应迁移,不减语义)
- Modify: 受影响 stories(`FormView.stories.tsx` 若引用 tab)

- [ ] **Step 1: 按新 DOM 契约更新 e2e 选择器与断言**
- [ ] **Step 2: `pnpm typecheck && pnpm exec vitest run` 全量 → PASS**
- [ ] **Step 3: `pnpm build` + storybook build 本地过;commit `test(admin-ui): e2e 适配配置页新布局`**

## Self-Review 已核

- spec 覆盖:骨架(T1-3)/FieldRow(T4)/试点组含两新字段(T5-6)/兼容三约束(T2 LeadingTab+YAML;T7 e2e)✓
- 类型一致:`ConfigGroup.sections: FormSection[]` ↔ FormView `sections` prop;`readRunBudget/patchRunBudget` 消费点在 T6 ✓
- 无占位符;PR2+ 各组不在本计划(见 spec 分期)✓
