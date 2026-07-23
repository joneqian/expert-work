# Agent 配置页:工具开关 + 去折叠 + 文案大白话 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 补齐 Agent 配置页(admin-ui manifest-editor)三块:①内置工具开关(基础工具默认开且不显示、exec/bash 默认开可关、opt-in 工具开关);②去掉折叠(预算·限额 + 上下文·压缩 改 table,其余组「影响说明」常显);③全配置页帮助文案改大白话 + 复杂字段加场景,zh + en 双 locale。

**Architecture:** 纯前端,不碰后端。工具"默认开"= 新建模板 `defaults.ts` 种入 `tools:`,form 只对带开关的那批做投影、基础那批靠 round-trip 原样保留(现有 `patchSpec` 已保留未投影键)。去折叠 = 改 `FieldRow`(impact 常显)+ 新 `PolicyFieldTable`(复用现有 `FieldDef` 声明,table 布局)。文案 = 重写 i18n `_brief`/`_impact`/intro 等键。

**Tech Stack:** React + TypeScript + antd;react-i18next(zh-CN.ts / en.ts);vitest;`npx tsc -b --noEmit`。

## Global Constraints

- **纯前端,零后端改动**。工具启用机制沿用现有:manifest `tools:` 列表驱动(后端 `_register_builtin` 按 entry 注册)。
- **存量 agent 不动**:种子只加进 `defaults.ts` 的新建模板,只影响**新建** agent;编辑存量 agent 时 form 不自动补种。
- **工具三分类**(钉死):
  - 基础(种入模板、默认开、form **不显开关**):`read_file` `write_file` `edit_file` `list_dir` `read_document` `save_artifact` `list_artifacts` `ask_for_approval` `remember`
  - 高危(种入模板、默认开、form **显开关可关**):`exec_python` `bash`
  - opt-in(**不种**、form 显开关默认关):`manage_task` `author_skill` `refine_skill` `fork_skill` `propose_skill_to_tenant` `note_behavior_patch` `clarify_tool_usage`
  - 保留现状:`web_search` `http`(现有开关不动)、MCP(现有 picker 不动)
- **工具 setter 保留兄弟条目 + 不冲 config**:新 `setBuiltinTool` 对已存在的 entry 原样保留(不像现有 `setTool` webSearch 分支那样 `on` 时重建 `{...config:{}}`),否则一点开关把隐藏的基础工具或该工具已有 config 冲没。
- **去折叠不可回退**:FieldRow 的 impact 由 `Collapse` 改常显;预算·限额 + 上下文·压缩 改 table 全铺开(上下文·压缩去掉外层 4 面板 `Collapse`)。
- **文案标准**:后台非运营人员也看 → 大白话、口语化、少术语(禁「委托树」「LLMStreamStaleError」「routing planning 规则」这类);特别复杂的(工作流类型、三道压缩门、防御开关)在 `_impact` 里带**一句具体场景**。
- **i18n 双 locale 同步**:每个新增/改动键必须 zh-CN.ts **和** en.ts 都有;**同一 object 内不得重复键**(esbuild 静默后者覆盖前者 —— 历史坑);改前 grep 确认键不撞既有。
- **提交**:conventional commits,type=`feat`/`fix`/`refactor`;**无 attribution**。
- **CI 门**:`cd apps/admin-ui && npx tsc -b --noEmit`(exit 0)+ `npx vitest run <相关测试>`。编辑器诊断可能 stale,以真 tsc/vitest 定论。eslint 本仓库无配置,不作门。

## File Structure

- `apps/admin-ui/src/components/manifest-editor/form_model.ts` — 加 `hasBuiltinTool` + `setBuiltinTool`(通用 builtin 读/写,保留兄弟+config)。
- `apps/admin-ui/src/components/manifest-editor/FormView.tsx` — tools section 加 exec/bash + opt-in 开关(:466-497 区)。
- `apps/admin-ui/src/components/manifest-editor/defaults.ts` — `BASE_MANIFEST_YAML` 加 `tools:` 种子块(基础9 + exec/bash)。
- `apps/admin-ui/src/components/manifest-editor/FieldRow.tsx` — impact 由 `Collapse` 改常显。
- `apps/admin-ui/src/components/manifest-editor/groups/field_defs.tsx` — 抽出共享 `FieldControl`;新增 `PolicyFieldTable`。
- `apps/admin-ui/src/components/manifest-editor/groups/RunBudgetSection.tsx` — 改用 `PolicyFieldTable`。
- `apps/admin-ui/src/components/manifest-editor/groups/ContextGatesSection.tsx` — 去外层 `Collapse`,改用 `PolicyFieldTable`(4 分组)。
- `apps/admin-ui/src/i18n/locales/zh-CN.ts` + `en.ts` — 新工具开关键 + 全配置页帮助文案重写。
- 测试:`__tests__/` 下对应组件测 + form_model 测 + defaults 测。

---

## Task 1: 内置工具开关(form_model + FormView + defaults + i18n)

**Files:**
- Modify: `form_model.ts`(`ToolFlags`/`readTools` 之后 ~line 363;`setTool` 之后 ~line 581)
- Modify: `FormView.tsx`(tools section :466-497)
- Modify: `defaults.ts`(`BASE_MANIFEST_YAML` :sandbox 之后)
- Modify: `i18n/locales/zh-CN.ts` + `en.ts`(`agent_form` object 内)
- Test: `form_model.test.ts`(或既有 tools 测文件)、`FormView.test.tsx`(或 stories 测)、`defaults.test.ts`

**Interfaces:**
- Produces: `hasBuiltinTool(m, name): boolean`、`setBuiltinTool(m, name, on): AgentManifest`(保留兄弟 + 已存在 entry 的 config)。

- [ ] **Step 1: 写失败测试(form_model.setBuiltinTool)**

参照现有 `form_model` 测(找 `setTool`/`readTools` 的既有测文件复用 fixture);加:

```ts
import { hasBuiltinTool, setBuiltinTool } from "../form_model";

const withTools = (names: string[]) => ({
  spec: { tools: names.map((name) => ({ type: "builtin", name })) },
});

test("setBuiltinTool adds without touching siblings", () => {
  const m = withTools(["read_file", "exec_python"]);
  const out = setBuiltinTool(m, "manage_task", true) as any;
  const names = out.spec.tools.map((t: any) => t.name);
  expect(names).toContain("manage_task");
  expect(names).toContain("read_file");   // sibling preserved
  expect(names).toContain("exec_python");
});

test("setBuiltinTool off removes only that tool", () => {
  const m = withTools(["read_file", "bash"]);
  const out = setBuiltinTool(m, "bash", false) as any;
  const names = out.spec.tools.map((t: any) => t.name);
  expect(names).toEqual(["read_file"]);
});

test("setBuiltinTool on when already present preserves its config", () => {
  const m = { spec: { tools: [{ type: "builtin", name: "manage_task", config: { x: 1 } }] } };
  const out = setBuiltinTool(m, "manage_task", true) as any;
  const entry = out.spec.tools.find((t: any) => t.name === "manage_task");
  expect(entry.config).toEqual({ x: 1 });   // NOT clobbered
});

test("hasBuiltinTool reflects presence", () => {
  expect(hasBuiltinTool(withTools(["exec_python"]), "exec_python")).toBe(true);
  expect(hasBuiltinTool(withTools(["exec_python"]), "bash")).toBe(false);
});
```

- [ ] **Step 2: 跑确认 fail**

Run: `cd apps/admin-ui && npx vitest run src/components/manifest-editor/__tests__/form_model.test.ts -t setBuiltinTool`
Expected: FAIL(函数未定义)

- [ ] **Step 3: 实现 form_model 的 hasBuiltinTool + setBuiltinTool**

在 `setTool` 之后加:

```ts
/** Whether a builtin tool by ``name`` is enabled (present in ``spec.tools``). */
export const hasBuiltinTool = (m: unknown, name: string): boolean =>
  (specOf(m).tools ?? []).some((t) => t.type === "builtin" && t.name === name);

/**
 * Toggle a builtin tool on/off by name. Unlike ``setTool``'s webSearch branch,
 * this NEVER rebuilds an already-present entry — so an existing entry's
 * ``config`` (or any of the sibling default-on essentials the form doesn't
 * show) survives a toggle untouched. ``on`` adds ``{type:"builtin", name}``
 * only when absent; ``off`` drops just that entry.
 */
export function setBuiltinTool(m: unknown, name: string, on: boolean): AgentManifest {
  const tools = specOf(m).tools ?? [];
  const has = tools.some((t) => t.type === "builtin" && t.name === name);
  if (on) {
    return has
      ? patchSpec(m, { tools })
      : patchSpec(m, { tools: [...tools, { type: "builtin", name }] });
  }
  return patchSpec(m, {
    tools: tools.filter((t) => !(t.type === "builtin" && t.name === name)),
  });
}
```

- [ ] **Step 4: 跑确认 pass**

Run: `cd apps/admin-ui && npx vitest run src/components/manifest-editor/__tests__/form_model.test.ts -t setBuiltinTool`
Expected: PASS

- [ ] **Step 5: FormView tools section 加开关(exec/bash + opt-in)**

在 `FormView.tsx` 顶部工具区定义开关表(靠近 line 154 的 `["web_search","http"]` 常量,或就近):

```tsx
// Builtin tools that get a form toggle. exec_python/bash are seeded default-ON
// (removable); the rest are opt-in default-OFF. The essential file/artifact/
// read/remember builtins are seeded but intentionally have NO toggle (edit YAML
// to change) — they are NOT in this list.
const BUILTIN_TOGGLES = [
  { name: "exec_python", key: "tool_exec_python" },
  { name: "bash", key: "tool_bash" },
  { name: "manage_task", key: "tool_manage_task" },
  { name: "author_skill", key: "tool_author_skill" },
  { name: "refine_skill", key: "tool_refine_skill" },
  { name: "fork_skill", key: "tool_fork_skill" },
  { name: "propose_skill_to_tenant", key: "tool_propose_skill" },
  { name: "note_behavior_patch", key: "tool_note_behavior_patch" },
  { name: "clarify_tool_usage", key: "tool_clarify_tool_usage" },
] as const;
```

在 tools section 的 flex 列里(http 那个 `</span>` 之后、`af-tools-config-note` 之前)追加:

```tsx
          {BUILTIN_TOGGLES.map((tool) => (
            <span key={tool.name}>
              <Checkbox
                data-testid={`af-tool-${tool.name}`}
                checked={hasBuiltinTool(formData, tool.name)}
                onChange={(e) =>
                  onChange(setBuiltinTool(formData, tool.name, e.target.checked))
                }
              >
                {t(`agent_form.${tool.key}`)}
              </Checkbox>
              <FieldHelp
                text={t(`agent_form.${tool.key}_help`)}
                testId={`af-tool-${tool.name}`}
              />
            </span>
          ))}
```

import 加 `hasBuiltinTool, setBuiltinTool`(:93 附近的 form_model import)。

- [ ] **Step 6: defaults.ts 种子模板加 tools 块**

`BASE_MANIFEST_YAML` 里 `sandbox:` 块**之前**(或之后,YAML 顺序无关)插入:

```yaml
  tools:
    - { type: builtin, name: read_file }
    - { type: builtin, name: write_file }
    - { type: builtin, name: edit_file }
    - { type: builtin, name: list_dir }
    - { type: builtin, name: read_document }
    - { type: builtin, name: save_artifact }
    - { type: builtin, name: list_artifacts }
    - { type: builtin, name: ask_for_approval }
    - { type: builtin, name: remember }
    - { type: builtin, name: exec_python }
    - { type: builtin, name: bash }
```

- [ ] **Step 7: i18n 加工具开关文案(zh + en)**

`agent_form` object 内加(zh-CN.ts + en.ts **都加**,键一致)。示例(zh):

```ts
    tool_exec_python: "运行 Python 代码",
    tool_exec_python_help: "让 Agent 在隔离沙箱里跑 Python 做计算、处理数据。默认开;纯聊天类 Agent 可关。",
    tool_bash: "运行 Shell 命令",
    tool_bash_help: "让 Agent 在隔离沙箱里跑命令行(如装依赖、跑脚本)。默认开;不需要动系统的 Agent 可关。",
    tool_manage_task: "定时任务",
    tool_manage_task_help: "让 Agent 能在对话里帮用户建定时任务(如「每天9点搜新闻」),到点自动跑、结果回到对话。",
    tool_author_skill: "创作技能",
    tool_author_skill_help: "让 Agent 能把一段可复用的做法沉淀成「技能」保存起来,以后自己或别的 Agent 直接用。",
    tool_refine_skill: "优化技能",
    tool_refine_skill_help: "让 Agent 能改进已有技能的内容。",
    tool_fork_skill: "复制技能",
    tool_fork_skill_help: "让 Agent 基于已有技能复制一份再改,不动原技能。",
    tool_propose_skill: "提交技能到团队",
    tool_propose_skill_help: "让 Agent 把自己的技能提交给团队共享(需审批)。",
    tool_note_behavior_patch: "记录行为修正",
    tool_note_behavior_patch_help: "让 Agent 能记下「以后这类情况该怎么做」的小修正,逐步改进自己的表现。",
    tool_clarify_tool_usage: "澄清工具用法",
    tool_clarify_tool_usage_help: "让 Agent 在用错工具时记下正确用法,下次不再犯。",
```

en.ts 对应英文版(同键)。

- [ ] **Step 8: 写 defaults + FormView 测试**

- `defaults.test.ts`:parse `BASE_MANIFEST_YAML` → `spec.tools` 含全部 11 个种子工具、且**不含** manage_task/skill 类(默认关)。
- `FormView.test.tsx`(或既有 stories 测):tools section 渲染 `af-tool-exec_python`/`af-tool-manage_task` 等 checkbox;点 manage_task → formData.spec.tools 出现 manage_task;新建默认态下 exec_python checked=true(种子在)、manage_task checked=false。

- [ ] **Step 9: 跑测试 + tsc + commit**

Run: `cd apps/admin-ui && npx vitest run src/components/manifest-editor/ && npx tsc -b --noEmit`
Expected: PASS + exit 0

```bash
git add apps/admin-ui/src/components/manifest-editor/{form_model.ts,FormView.tsx,defaults.ts} apps/admin-ui/src/components/manifest-editor/__tests__/ apps/admin-ui/src/i18n/locales/{zh-CN,en}.ts
git commit -m "feat(agent-config): 内置工具开关 —— 基础默认开藏 UI / exec-bash 默认开可关 / opt-in 开关

form 工具区加 exec_python·bash(种子默认开、可关)+ manage_task·skill创作·harness 工具
(默认关)开关;setBuiltinTool 保留兄弟条目与已有 config;新建模板种入基础9+exec/bash,
基础工具不上界面(要改直接改 YAML);存量 agent 不受影响。"
```

> ⚠️ **验证注意(给 implementer)**:种入 `read_file`/`exec_python` 等要求目标部署把这些工具的 ToolEnv 接线了(标准 full 栈有 sandbox-supervisor,OK)。若跑 e2e/手动冒烟时新建 agent build 报「tool not available」,说明该部署未接线某工具 —— 记录但不阻断(部署配置问题,非本改动 bug)。

---

## Task 2: FieldRow 去折叠 + PolicyFieldTable 组件

**Files:**
- Modify: `FieldRow.tsx`(impact 由 Collapse 改常显)
- Modify: `groups/field_defs.tsx`(抽 `FieldControl` + 新增 `PolicyFieldTable`)
- Test: `__tests__/field_defs.test.tsx`(或新建)、`FieldRow` 相关测

**Interfaces:**
- Consumes: `FieldDef`(既有)。
- Produces: `PolicyFieldTable`(props:`groups: readonly {titleKey?: string; defs: readonly FieldDef[]}[]` + `values` + `onPatch`),table 布局、说明列 = brief+impact 全显、无折叠。

- [ ] **Step 1: FieldRow impact 改常显(先改 + 测)**

`FieldRow.tsx` 把 `impact` 的 `<Collapse>...</Collapse>`(:63-75)换成:

```tsx
      {impact && (
        <div style={{ fontSize: 12, color: "var(--ew-text-secondary)", marginTop: 4 }}>
          {impact}
        </div>
      )}
```

删掉 `Collapse` import(改留 `Tag`)。`field_impact_label` i18n 键变为不再被 FieldRow 使用(留着无害,勿删以免撞其它引用 —— grep 确认没别处用再决定)。

测试(FieldRow 或消费它的测):给 `impact` prop → impact 文本**直接可见**(不需点开),无 `.ant-collapse`。

- [ ] **Step 2: 抽 FieldControl(把 PolicyFieldList 的控件渲染逻辑抽成共享)**

`field_defs.tsx` 里把 `PolicyFieldList` map 内那段 `const control = def.kind === "switch" ? ... : ...`(:133-187)抽成一个组件/函数:

```tsx
export function FieldControl<V extends FieldValue = FieldValue>({
  def,
  raw,
  label,
  onPatch,
}: {
  def: FieldDef;
  raw: V | undefined;
  label: string;
  onPatch: (patch: Record<string, V | undefined>) => void;
}): ReactNode {
  // ← 原 control 三元(switch/select/tags/number)整段搬进来,逐字不变
}
```

`PolicyFieldList` 改成 `<FieldControl def={def} raw={raw} label={label} onPatch={onPatch} />`。跑既有 PolicyFieldList/RunBudget/ContextGates 测确认零回归(纯重构)。

- [ ] **Step 3: 写 PolicyFieldTable 测试**

```tsx
const DEFS: FieldDef[] = [
  { fieldId: "a.enabled", i18nKey: "t.a", valueKey: "aEnabled", kind: "switch", effectiveDefault: true },
  { fieldId: "a.n", i18nKey: "t.n", valueKey: "aN", kind: "number", effectiveDefault: 4, min: 0 },
];
test("PolicyFieldTable renders rows with visible brief (no collapse)", () => {
  render(<PolicyFieldTable groups={[{ defs: DEFS }]} values={{}} onPatch={vi.fn()} />);
  // 每个 def 一行、说明文本直接可见(mock i18n brief)、无 .ant-collapse
});
test("PolicyFieldTable renders group headers", () => {
  render(<PolicyFieldTable groups={[{ titleKey: "t.grp", defs: DEFS }]} values={{}} onPatch={vi.fn()} />);
  // 组标题渲染
});
test("PolicyFieldTable control patches value", () => {
  const onPatch = vi.fn();
  render(<PolicyFieldTable groups={[{ defs: DEFS }]} values={{}} onPatch={onPatch} />);
  // 点 switch → onPatch 被调
});
```

- [ ] **Step 4: 跑确认 fail**

Run: `cd apps/admin-ui && npx vitest run src/components/manifest-editor/__tests__/field_defs.test.tsx -t PolicyFieldTable`
Expected: FAIL(未定义)

- [ ] **Step 5: 实现 PolicyFieldTable**

```tsx
export interface FieldGroup {
  titleKey?: string;
  defs: readonly FieldDef[];
}
export interface PolicyFieldTableProps<V extends FieldValue = FieldValue> {
  groups: readonly FieldGroup[];
  values: Record<string, V | undefined>;
  onPatch: (patch: Record<string, V | undefined>) => void;
}

export function PolicyFieldTable<V extends FieldValue = FieldValue>({
  groups,
  values,
  onPatch,
}: PolicyFieldTableProps<V>): ReactNode {
  const { t, i18n } = useTranslation();
  return (
    <div style={{ overflowX: "auto" }}>
      <table
        data-testid="policy-field-table"
        style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}
      >
        <colgroup>
          <col style={{ width: "22%" }} />
          <col style={{ width: "22%" }} />
          <col style={{ width: "10%" }} />
          <col />
        </colgroup>
        <tbody>
          {groups.map((group, gi) => (
            <Fragment key={group.titleKey ?? gi}>
              {group.titleKey && (
                <tr>
                  <td
                    colSpan={4}
                    style={{ fontWeight: 600, padding: "12px 8px 4px", borderTop: gi > 0 ? "1px solid var(--ew-border-subtle)" : undefined }}
                  >
                    {t(group.titleKey)}
                  </td>
                </tr>
              )}
              {group.defs.map((def) => {
                const raw = values[def.valueKey];
                const atDefault = isAtDefault(raw, def.effectiveDefault);
                const label = t(`${def.i18nKey}_label`);
                const brief = t(`${def.i18nKey}_brief`);
                const impactKey = `${def.i18nKey}_impact`;
                const impact = i18n.exists(impactKey) ? t(impactKey) : undefined;
                const defaultKey = `${def.i18nKey}_default`;
                const badge =
                  def.kind === "switch" || def.kind === "tags"
                    ? undefined
                    : atDefault
                      ? (i18n.exists(defaultKey) ? t(defaultKey) : undefined)
                      : String(raw);
                return (
                  <tr key={def.fieldId} data-field-id={def.fieldId} style={{ verticalAlign: "top" }}>
                    <td style={{ padding: "8px", color: "var(--ew-text-primary)" }}>{label}</td>
                    <td style={{ padding: "8px" }}>
                      <FieldControl def={def} raw={raw} label={label} onPatch={onPatch} />
                    </td>
                    <td style={{ padding: "8px" }}>
                      {badge !== undefined && (
                        <Tag color={atDefault ? undefined : "blue"} bordered={false}>
                          {atDefault ? t("manifest_editor.field_default_badge", { value: badge }) : badge}
                        </Tag>
                      )}
                    </td>
                    <td style={{ padding: "8px", color: "var(--ew-text-secondary)", fontSize: 12, lineHeight: 1.5 }}>
                      <div>{brief}</div>
                      {impact && <div style={{ marginTop: 2 }}>{impact}</div>}
                    </td>
                  </tr>
                );
              })}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

import 补 `Fragment`(react)、`Tag`(antd)、`isAtDefault`(同文件已有)、`FieldControl`(Step 2 抽的)。

- [ ] **Step 6: 跑测试 + tsc + commit**

Run: `cd apps/admin-ui && npx vitest run src/components/manifest-editor/ && npx tsc -b --noEmit`
Expected: PASS + exit 0

```bash
git add apps/admin-ui/src/components/manifest-editor/{FieldRow.tsx,groups/field_defs.tsx} apps/admin-ui/src/components/manifest-editor/__tests__/
git commit -m "refactor(agent-config): FieldRow 影响说明常显 + 新增 PolicyFieldTable

FieldRow 的 impact 由折叠改为常显(去掉 Collapse);抽出共享 FieldControl;新增
PolicyFieldTable(复用 FieldDef 声明,table 布局:配置项|值|默认|说明,说明列
brief+impact 全显、支持分组表头),供预算/上下文组改表格用。"
```

---

## Task 3: 预算·限额 + 上下文·压缩 改 table

**Files:**
- Modify: `groups/RunBudgetSection.tsx`
- Modify: `groups/ContextGatesSection.tsx`
- Test: 既有 `RunBudgetSection.test.tsx` / `ContextGatesSection.test.tsx`(找到后更新)

**Interfaces:** Consumes `PolicyFieldTable`(Task 2)。

- [ ] **Step 1: RunBudgetSection 改用 PolicyFieldTable**

把它现有的 `<PolicyFieldList defs={RUN_BUDGET_DEFS} .../>`(整个渲染)换成:

```tsx
<PolicyFieldTable groups={[{ defs: RUN_BUDGET_DEFS }]} values={values} onPatch={handlePatch} />
```

(单组,无表头;`workflow_note` 说明文字保留原位。)import 从 `./field_defs` 换 `PolicyFieldList` → `PolicyFieldTable`。

- [ ] **Step 2: ContextGatesSection 去 Collapse 改 PolicyFieldTable**

把 `<Collapse ...>`(:217-269,4 面板)整段换成:

```tsx
<PolicyFieldTable
  groups={[
    { titleKey: "context_gates.panel_tool_result_prune", defs: TOOL_RESULT_PRUNE_DEFS },
    { titleKey: "context_gates.panel_working_memory", defs: WORKING_MEMORY_DEFS },
    { titleKey: "context_gates.panel_context_compression", defs: CONTEXT_COMPRESSION_DEFS },
    { titleKey: "context_gates.panel_tool_output_budget", defs: TOOL_OUTPUT_BUDGET_DEFS },
  ]}
  values={values}
  onPatch={handlePatch}
/>
```

删掉 `Collapse` import(留 `Typography`);intro `Text`(group_intro)保留。四个 `*_DEFS` 常量不动。

- [ ] **Step 3: 更新两组测试**

现有测若断言 `.ant-collapse` 面板 / 点开展开,改为断言 table 行(`policy-field-table` + 各 `data-field-id` 行直接可见,无需展开)。ContextGates:4 组标题 + 18 字段全在 DOM 且可见(不再 forceRender-hidden)。RunBudget:7 字段成行。保留"改值 → onChange/patch"断言。

- [ ] **Step 4: 跑测试 + tsc + commit**

Run: `cd apps/admin-ui && npx vitest run src/components/manifest-editor/ && npx tsc -b --noEmit`
Expected: PASS + exit 0

```bash
git add apps/admin-ui/src/components/manifest-editor/groups/{RunBudgetSection,ContextGatesSection}.tsx apps/admin-ui/src/components/manifest-editor/**/__tests__/
git commit -m "refactor(agent-config): 预算·限额 + 上下文·压缩 改 table 全铺开

两组由折叠式 FieldRow / 4 面板 Collapse 改为 PolicyFieldTable 表格:配置项与说明
全部常显、对齐紧凑,不再点开;上下文·压缩的三道门+工具输出预算作为 table 分组一屏铺开。"
```

---

## Task 4: 全配置页帮助文案大白话重写(zh + en)

**Files:**
- Modify: `apps/admin-ui/src/i18n/locales/zh-CN.ts` + `en.ts`
- 无独立测试(文案),靠 `tsc` + 目视;跑全 vitest 确认无键引用断裂。

**范围(逐 object 重写其 `_brief`/`_impact`/intro/note/`_label`)**:
- `run_budget.*`(:612-654)—— max_iterations / wf_type / max_no_progress / run_deadline / stream_deadline / idle_timeout / token_budget
- `context_gates.*`(:655+)—— group_intro + 四面板名 + pr_/wm_/cc_/budget_ 全字段
- `memory_group.*` / memory 高级项文案
- `security_gates.*`(防御 7 开关 + network + enforce)
- `sandbox_*`、`observability.*`
- `model_group.*`(reflection)
- `agent_form.*` 的 tool_* 帮助文案(含 Task 1 新增的)+ section helps + `tools_config_note`
- `dynamic_context` note 等

**标准(硬性)**:
1. **大白话**:非技术运营看得懂。禁术语堆。反面例(现有 token_budget_brief)"本 run(主 Agent + 全部 worker)全委托树共享的 token 总上限" → 正面"这次运行(包括它调起的所有子任务)总共最多花多少 token"。
2. **复杂字段带一句场景**。示例改写:

| 键 | 现状(术语) | 改后(大白话+场景) |
|----|------|------|
| `wf_type_impact` | "react=按需自主规划:任务复杂时 Agent 调用 update_plan...routing planning 规则指定..." | "react(推荐):Agent 边做边想,简单任务直接答、复杂任务自己先列步骤——大部分场景用这个。plan_execute:每次都先写一份完整计划再照着做,像写长报告这种一步扣一步的活更稳,但简单问题也得先规划,会更慢更费钱。" |
| `stream_deadline_impact` | "...按 LLMStreamStaleError 处理,路由器会切到回退链..." | "从让模型开始答、到它吐出第一个字,最多等多少秒。等太久(比如模型卡住)就自动换备用模型,不会干等着。默认 180 秒,够长报告这种慢活用。" |
| `token_budget_impact` | "主循环每次 LLM 调用的 input/output/cache token 计入同一个池..." | "给这次运行定个花钱上限(按 token 算,包含所有子任务)。花到 80% 会提醒模型抓紧收尾,花光了就强制它用现有信息给结论。0 = 不限。" |

3. **en.ts 同步**:每个改动键的英文也改成对应大白话;**键名与 zh 完全一致**(不增不减不重)。

- [ ] **Step 1: 重写 zh-CN.ts 上述 object 的文案**（大白话+场景,逐条）
- [ ] **Step 2: 重写 en.ts 对应文案（同键、英文大白话）**
- [ ] **Step 3: 键一致性自检**：`grep` 两文件相关 object 的键,确认 zh/en 键集一致、object 内无重复键（esbuild 静默覆盖坑）。
- [ ] **Step 4: 跑全 vitest + tsc**

Run: `cd apps/admin-ui && npx vitest run src/components/manifest-editor/ && npx tsc -b --noEmit`
Expected: PASS + exit 0（无键引用断裂）

- [ ] **Step 5: commit**

```bash
git add apps/admin-ui/src/i18n/locales/{zh-CN,en}.ts
git commit -m "feat(agent-config): 配置页帮助文案改大白话 + 复杂字段加场景

全配置页(预算/上下文/记忆/安全/沙箱/工具等)的 brief/impact 文案重写:面向后台非
运营人员,去术语、口语化,复杂项(工作流类型、压缩三道门、超时等)带一句具体场景;
zh + en 双 locale 同步。"
```

---

## Self-Review

- **工具三分类**全覆盖(Task 1);去折叠 = FieldRow 常显 + 两组 table(Task 2/3);文案大白话+场景(Task 4)。= 用户拍板的三块全落地。
- **存量不动**:种子只在 defaults 模板;setBuiltinTool 只在 form 交互时改。
- **不冲兄弟/config**:setBuiltinTool 已存在 entry 原样保留(Task 1 测覆盖)。
- **类型一致**:`PolicyFieldTable` 复用 `FieldDef`/`FieldControl`/`isAtDefault`;`hasBuiltinTool`/`setBuiltinTool` 签名一致。
- **i18n 双 locale + 无重复键**(Task 1 Step 7 + Task 4 Step 3 硬性自检)。
- **纯前端**:零后端文件改动。
- **DEFER/不做**:tenant_config/output_schema/observability trace 等其它 P1 缺口(本轮不含,后续单独排);sandbox.resources 等 declarative(留 YAML)。

## Execution Handoff

Plan complete。执行走 **subagent-driven-development**:implementer/reviewer 用 sonnet,终审用 opus。Task 依赖:T2 → T3(T3 消费 PolicyFieldTable);T1 独立;T4 最后(重写最终键集)。
