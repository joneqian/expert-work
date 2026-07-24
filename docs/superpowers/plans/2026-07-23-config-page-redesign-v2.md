# 配置页重设计 v2 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agent 配置页全面重排——一行式字段行+ⓘ气泡、重区块子 tab、预设档位一键应用、结构化输出可视化编辑器、工具默认全开档、文案大白话。

**Architecture:** 纯前端(admin-ui manifest-editor),一切改动都是 manifest 投影;FieldRow v2 是全局渲染契约,PolicyFieldTable 删除;记忆/治理/防护三个 section 从 FormView 搬进各自 Section 组件;预设档=form_model 纯函数批量 patch,不落新后端字段。

**Tech Stack:** React 18 + antd(Tabs/Popover/Radio/Popconfirm)+ react-i18next + vitest/@testing-library。

**Spec:** `docs/superpowers/specs/2026-07-23-config-page-redesign-v2-design.md`(冲突时 spec 为准)

## Global Constraints

- 纯前端,零后端改动;存量 agent 不动(种子只影响新建模板)。
- YAML↔Form round-trip:未投影键恒保留(沿用 patchSpec 机制);删除 UI ≠ 删除 manifest 能力。
- FieldRow v2 契约:`{fieldId, label, brief, help?, isDefault, onReset?, resetHint?, children}`;brief 常显 ≤18 字大白话;help(长解释+场景)进点击式 Popover;非默认值 →「已自定义」Tag +「恢复默认」按钮;默认值时零徽章零按钮。
- 预设 18 受管字段与三档值:见 spec §③ 表(max_no_progress = 4/3/6,模板种 4);安全防护字段一律不受管。
- 均衡档不变式:除 max_no_progress(写 4)外,应用均衡 = 受管键全部 patch undefined。
- 结构化输出护栏:不可平铺表示的 json_schema 只读降级,编辑器绝不改写。
- 工具种子(defaults.ts):基础9 + exec_python + bash + web_search + http + opt-in7 = 20 项全种入默认开;基础9 无界面开关,其余 11 有。
- i18n:zh-CN + en 键集一致(现有 parity 测试必须保持绿);对象内无重复键;禁术语(委托树/LLMStreamStaleError/routing 规则 等)。
- 终验:`npx tsc -b --noEmit` exit 0 + `npx vitest run` 全绿(admin-ui 目录)。
- 编辑器 harness 诊断常报 stale 假错——一律以真 tsc/vitest 定论。

---

### Task 1: FieldRow v2 + PolicyFieldList 改造 + 删 PolicyFieldTable + 预算/压缩回归列表

**Files:**
- Modify: `apps/admin-ui/src/components/manifest-editor/FieldRow.tsx`(整体重写)
- Modify: `apps/admin-ui/src/components/manifest-editor/groups/field_defs.tsx`(PolicyFieldList 适配;删 PolicyFieldTable/FieldGroup/PolicyFieldTableProps;FieldControl 不动)
- Modify: `apps/admin-ui/src/components/manifest-editor/groups/RunBudgetSection.tsx`(两小标题 + PolicyFieldList;删 workflow_note)
- Modify: `apps/admin-ui/src/components/manifest-editor/groups/ContextGatesSection.tsx`(PolicyFieldTable → 4×〔Text 标题+PolicyFieldList〕,结构 T3 再改 tab)
- Modify: `apps/admin-ui/src/components/manifest-editor/groups/MemorySection.tsx`、`ModelRoutingSection.tsx`(FieldRow 新 props 编译适配:`impact=` → `help=`)
- Modify: `apps/admin-ui/src/i18n/locales/zh-CN.ts`、`en.ts`
- Test: `apps/admin-ui/src/components/manifest-editor/__tests__/FieldRow.test.tsx`、`groups/__tests__/field_defs.test.tsx`、`groups/__tests__/RunBudgetSection.test.tsx`、`groups/__tests__/ContextGatesSection.test.tsx`

**Interfaces:**
- Produces(后续所有任务消费):

```tsx
export interface FieldRowProps {
  fieldId: string;
  label: string;
  /** 一句大白话,常显 */
  brief: string;
  /** 长解释+场景,点击 ⓘ 弹 Popover;缺省不渲染 ⓘ */
  help?: string;
  isDefault: boolean;
  /** 非默认时渲染「恢复默认」;点击回调(通常 patch undefined) */
  onReset?: () => void;
  /** 恢复默认按钮的 Tooltip:「恢复默认:{resetHint}」;缺省只显按钮 */
  resetHint?: string;
  children: ReactNode;
}
```

- [ ] **Step 1: 重写 FieldRow.tsx**

```tsx
/**
 * FieldRow v2 — 一行式字段行(配置页重设计 v2 Task 1)。
 * 布局:标签 | 控件 | 一句大白话(常显) | ⓘ(点击弹长解释) | 已自定义+恢复默认。
 * 默认徽章废除:值===默认 → 行内零噪音;非默认 → 蓝「已自定义」Tag + 恢复默认按钮。
 * 纯展示组件:文案由调用方翻好传入。
 */
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Button, Popover, Tag, Tooltip } from "antd";
import { HelpCircle } from "lucide-react";

export interface FieldRowProps {
  fieldId: string;
  label: string;
  brief: string;
  help?: string;
  isDefault: boolean;
  onReset?: () => void;
  resetHint?: string;
  children: ReactNode;
}

export function FieldRow({
  fieldId,
  label,
  brief,
  help,
  isDefault,
  onReset,
  resetHint,
  children,
}: FieldRowProps) {
  const { t } = useTranslation();

  const resetButton = (
    <Button
      type="link"
      size="small"
      data-testid={`field-reset-${fieldId}`}
      style={{ padding: 0, height: "auto" }}
      onClick={onReset}
    >
      {t("manifest_editor.field_reset")}
    </Button>
  );

  return (
    <div
      data-field-id={fieldId}
      style={{
        display: "flex",
        alignItems: "center",
        flexWrap: "wrap",
        columnGap: 12,
        rowGap: 4,
        marginBottom: 12,
      }}
    >
      <span style={{ minWidth: 160, flexShrink: 0 }}>{label}</span>
      <span style={{ flexShrink: 0 }}>{children}</span>
      <span
        style={{
          flex: "1 1 200px",
          fontSize: 12,
          color: "var(--ew-text-secondary)",
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
        }}
      >
        <span>{brief}</span>
        {help !== undefined && (
          <Popover
            trigger="click"
            content={
              <div style={{ maxWidth: 360, fontSize: 12, whiteSpace: "pre-line" }}>
                {help}
              </div>
            }
          >
            <button
              type="button"
              aria-label={t("common.field_help")}
              data-testid={`field-help-${fieldId}`}
              style={{
                display: "inline-flex",
                alignItems: "center",
                padding: 0,
                border: "none",
                background: "none",
                cursor: "help",
                color: "var(--ew-text-tertiary, #888)",
              }}
            >
              <HelpCircle size={13} strokeWidth={1.75} />
            </button>
          </Popover>
        )}
      </span>
      {!isDefault && (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <Tag color="blue" bordered={false} data-testid={`field-customized-${fieldId}`}>
            {t("manifest_editor.field_customized_badge")}
          </Tag>
          {onReset !== undefined &&
            (resetHint !== undefined ? (
              <Tooltip title={t("manifest_editor.field_reset_hint", { value: resetHint })}>
                {resetButton}
              </Tooltip>
            ) : (
              resetButton
            ))}
        </span>
      )}
    </div>
  );
}
```

- [ ] **Step 2: field_defs.tsx 适配**
  - `PolicyFieldList` map 内改为:

```tsx
const impactKey = `${def.i18nKey}_impact`;
const defaultKey = `${def.i18nKey}_default`;
const help = i18n.exists(impactKey) ? t(impactKey) : undefined;
const resetHint = i18n.exists(defaultKey)
  ? t(defaultKey)
  : def.effectiveDefault === null
    ? undefined
    : String(def.effectiveDefault);
return (
  <FieldRow
    key={def.fieldId}
    fieldId={def.fieldId}
    label={label}
    brief={t(`${def.i18nKey}_brief`)}
    help={help}
    isDefault={atDefault}
    onReset={() => onPatch({ [def.valueKey]: undefined } as Record<string, V | undefined>)}
    resetHint={resetHint}
  >
    <FieldControl def={def} raw={raw} label={label} onPatch={onPatch} />
  </FieldRow>
);
```

  - 删除 `PolicyFieldTable`、`FieldGroup`、`PolicyFieldTableProps` 及 `Fragment`/`Tag` 等因此不再用的 import;文件头注释同步。
- [ ] **Step 3: RunBudgetSection 改两小标题布局,删注脚**

```tsx
const STEP_DEFS = RUN_BUDGET_DEFS.filter((d) =>
  ["workflow.max_iterations", "workflow.type", "policies.max_no_progress"].includes(d.fieldId),
);
const TIME_DEFS = RUN_BUDGET_DEFS.filter((d) => !STEP_DEFS.includes(d));
// render:
<Text strong style={{ display: "block", marginBottom: 8 }}>{t("run_budget.subhead_steps")}</Text>
<PolicyFieldList defs={STEP_DEFS} values={...} onPatch={...} />
<Text strong style={{ display: "block", margin: "16px 0 8px" }}>{t("run_budget.subhead_time")}</Text>
<PolicyFieldList defs={TIME_DEFS} values={...} onPatch={...} />
```

  workflow_note 的 `<Text>` 块删除。
- [ ] **Step 4: ContextGatesSection 机械回归**:`PolicyFieldTable groups={[...]}` → 每组 `<Text strong>{t(group.titleKey)}</Text> + <PolicyFieldList defs=.../>`(顺序不变,tab 化留给 T3)。
- [ ] **Step 5: MemorySection/ModelRoutingSection 编译适配**:`impact={...}` prop 改 `help={...}`;`defaultValue`/旧徽章逻辑如有传参处删掉。
- [ ] **Step 6: i18n**(zh + en 同步):
  - 新增 `manifest_editor.field_customized_badge`:zh「已自定义」/ en "Customized";`manifest_editor.field_reset`:zh「恢复默认」/ en "Reset to default";`manifest_editor.field_reset_hint`:zh「恢复默认:{{value}}」/ en "Reset to default: {{value}}"。
  - 新增 `run_budget.subhead_steps`:zh「步数与流程」/ en "Steps & flow";`run_budget.subhead_time`:zh「时间与花费」/ en "Time & spend"。
  - 删除 `run_budget.workflow_note`(zh+en)与 `manifest_editor.field_default_badge` 若已无消费者(grep 确认;PolicyFieldList 不再用它)。
- [ ] **Step 7: 测试**(先写、跑红、实现、跑绿):
  - FieldRow:brief 常显;`help` 给了才有 ⓘ,点击 Popover 出现 help 文本;`isDefault=true` → 无「已自定义」无按钮;`isDefault=false` → Tag+按钮,点按钮触发 onReset(mock 断言,变异:去掉 onClick 测试必红)。
  - field_defs:非默认值行点恢复默认 → onPatch 收到 `{key: undefined}`。
  - RunBudget:7 字段可见、两小标题、workflow_note testid 不存在。
  - ContextGates:18 字段可见、4 标题;原 table 测试改写(`policy-field-table` testid 断言删除)。
- [ ] **Step 8: `npx tsc -b --noEmit` + `npx vitest run` 全绿,commit** `feat(config-ui): FieldRow v2 一行式+ⓘ气泡,删 PolicyFieldTable`

### Task 2: 记忆组子 tab 重排(字段搬出 FormView)

**Files:**
- Modify: `apps/admin-ui/src/components/manifest-editor/groups/MemorySection.tsx`(整体重写)
- Modify: `apps/admin-ui/src/components/manifest-editor/FormView.tsx`(删 memory section:`FormSection` 去 `"memory"`、sectionsRecord 删条目、清 orphan imports readTopK/setTopK 等仅 memory 用的)
- Modify: zh-CN.ts / en.ts
- Test: `groups/__tests__/MemorySection.test.tsx`

**Interfaces:**
- Consumes: FieldRow v2、PolicyFieldList、form_model 现有 readers/setters(readMemoryOn/setMemoryOn、readTopK/setTopK、readWriteBack/setWriteBack、readVerifyReads/setVerifyReads、readWriteMinImportance/setWriteMinImportance、readReconcileWrites/setReconcileWrites、readRecallMode/setRecallMode、readRewriteReads/setRewriteReads、readAbstainThreshold/setAbstainThreshold、readMemoryBudgets/patchMemoryBudgets、readConsolidation/patchConsolidation)——全部已存在,签名不动。
- Produces: MemorySection 三 tab 结构;`data-testid="memory-tab-basic|retrieval|budget"`。

**结构**(antd `Tabs size="small"`,`items` 三项):

| tab key | label 键 | 内容 |
|---|---|---|
| basic | memory_group.tab_basic(基本) | 长期记忆开关(hand FieldRow,presence 语义,brief=「跨会话记住用户信息,需平台已配 Embedding」)+ BASIC_DEFS:top_k(number, 默认5)、write_back(switch, 默认 true) |
| retrieval | memory_group.tab_retrieval(检索细节) | RETRIEVAL_DEFS 6 项:verify_reads(switch,true)、write_min_importance(percent,0.3)、reconcile_writes(switch,true)、recall_mode(select,per_session,options [per_session,per_turn])、rewrite_reads(switch,false)、abstain_threshold(percent,0);`disabled: !memoryOn` |
| budget | memory_group.tab_budget(预算与整理) | memoryOn 时渲染 BUDGET_DEFS(现有 2 项);恒渲染 CONSOLIDATION_DEFS + aux_model_note 一行 |

- [ ] **Step 1: 写失败测试**:三 tab 渲染;记忆关 → retrieval tab disabled(`.ant-tabs-tab-disabled`)且 budget tab 内无 injection 字段行(`data-field-id="memory.long_term.injection_token_budget"` 不存在)、consolidation 行存在;记忆开 → retrieval 内 6 行齐;`memory-reserved-note` testid 不存在;top_k 改值 → onChange manifest `spec.memory.long_term.retrieve_top_k` 生效。
- [ ] **Step 2: 跑红** `npx vitest run src/components/manifest-editor/groups/__tests__/MemorySection.test.tsx`
- [ ] **Step 3: 实现**:新 FieldDef 表(fieldId 用真 manifest 路径 `memory.long_term.retrieve_top_k` 等;i18nKey `memory_group.topk` 等;valueKey 对齐各 setter 的读写对——BASIC/RETRIEVAL 的 onPatch 写一个 dispatch:`(patch) => { for key: call对应 setter }`,或复用现有单字段 setters 逐个 FieldRow 包 FieldControl——**取简单者:每个字段一个 FieldRow + 直连 setter**,kind 语义照 FieldDef 但不强求过 PolicyFieldList)。reserved_note 块删除。FormView memory section 删除 + FormSection 收窄 + orphan import 清理。
- [ ] **Step 4: i18n**:新增 `memory_group.tab_basic/tab_retrieval/tab_budget`、`memory_group.on_label/on_brief/on_impact`、以及 6+2 字段的 `_label/_brief/_impact`(zh 文案:label=现 agent_form 中文名;brief=新写 ≤18 字,如 verify_reads「回答前再核一遍记忆,更准但更慢」→ 精简为「回答前核对记忆,更准更慢」;impact=现 `agent_form.memory_*_help` 长文案迁移+补场景)。删除 `agent_form.memory_*` 与 `agent_form.section_memory*` 全部 orphan 键、`memory_group.reserved_note`(zh+en)。en 同步全部新键(地道翻译,非机翻腔)。
- [ ] **Step 5: 跑绿 + 全量 vitest + tsc,commit** `feat(config-ui): 记忆组三子tab,字段搬出 FormView,删保留字段注脚`

### Task 3: 上下文与压缩子 tab

**Files:**
- Modify: `groups/ContextGatesSection.tsx`
- Modify: zh-CN.ts / en.ts(tab 直接复用现 `panel_*` 键;`group_intro` 缩成 ≤2 句)
- Test: `groups/__tests__/ContextGatesSection.test.tsx`

- [ ] **Step 1: 失败测试**:intro 一行;`data-field-id="policies.tool_output_budget.enabled"` 行在 Tabs 之外顶部;Tabs 三项 label 含 ①②③;切到各 tab 后对应字段行可见(修剪3/滑窗4/压缩10);默认激活 tab=①。
- [ ] **Step 2: 跑红。**
- [ ] **Step 3: 实现**:

```tsx
<Text type="secondary">{t("context_gates.group_intro")}</Text>
<PolicyFieldList defs={TOOL_OUTPUT_BUDGET_DEFS} values={values} onPatch={handlePatch} />
<Tabs size="small" defaultActiveKey="prune" items={[
  { key: "prune", label: t("context_gates.panel_tool_result_prune"), children: <PolicyFieldList defs={TOOL_RESULT_PRUNE_DEFS} .../> },
  { key: "window", label: t("context_gates.panel_working_memory"), children: <PolicyFieldList defs={WORKING_MEMORY_DEFS} .../> },
  { key: "compress", label: t("context_gates.panel_context_compression"), children: <PolicyFieldList defs={CONTEXT_COMPRESSION_DEFS} .../> },
]} />
```

- [ ] **Step 4: zh intro 改**:「内容太长装不下时,按 ①修剪旧工具结果 → ②只留最近对话 → ③模型总结中间段 的顺序兜底,大多数情况前两步就够。窗口大小以所选模型为准。」en 同步。
- [ ] **Step 5: 跑绿 + commit** `feat(config-ui): 压缩组三子tab`

### Task 4: 安全组三子 tab + defenses/governance 搬出 FormView + 轨迹录制移除

**Files:**
- Modify: `groups/SecuritySection.tsx`(整体重写:三 tab)
- Modify: `FormView.tsx`(删 defenses + governance section、FormSection 收窄、orphan imports 清理:readApprovalTools/setApprovalTools/readApprovalTimeout/setApprovalTimeout/readDynamicWorkersOn/setDynamicWorkersOn/readTrajectoryRecording/setTrajectoryRecording/防护 read/set 全套、GATEABLE_TOOLS 常量迁走)
- Modify: `form_model.ts`(删 readTrajectoryRecording/setTrajectoryRecording——本次改动使其 orphan)
- Modify: zh-CN.ts / en.ts
- Test: `groups/__tests__/SecuritySection.test.tsx`

**结构**:

| tab key | label | 内容 |
|---|---|---|
| defenses | security_gates.tab_defenses(防护开关) | 原 FormView defenses section JSX 整体迁入(extends Alert + 输入/输出/工具行为三小标题 + 各开关及条件警告),每块改 FieldRow v2 包装(brief 新写,help=原 `_help` 文案) |
| approval | security_gates.tab_approval(人工审批) | approval_hint 一行 + GATEABLE_TOOLS 7 Checkbox + approval_timeout FieldRow(默认 86400) |
| network | security_gates.tab_network(子任务与网络) | dynamic_workers FieldRow + NETWORK_DEFS 3 + ENFORCE_DEFS 1(PolicyFieldList) |

- 轨迹录制:UI 整体删除;`observability_group.declarative_note` 文案追加提及 trajectory_recording(T8 统一改文案,本任务先删 UI + form_model 函数 + i18n `agent_form.trajectory_recording*` 键)。
- `security_gates.dict_note` 改一行:zh「限流、隐私脱敏、安全策略等高级项请在 YAML 视图配置。」en 同步。
- [ ] **Step 1: 失败测试**:三 tab;defenses tab 内 7 个防护控件 testid 齐且关 screen 出警告 Alert(现测试迁移);approval tab 勾 bash → manifest `policies.approval.tools` 含 bash;network tab 内 egress select 存在;`af-trajectory-recording` 全树不存在;dict_note 仍在(缩短后)。
- [ ] **Step 2: 跑红。Step 3: 实现。Step 4: i18n**(新 tab 键 + defenses 各行 `_brief` 新写 zh/en;approval/dynamic_workers hint 迁移)。**Step 5: 跑绿+全量+commit** `feat(config-ui): 安全组三子tab,防护/审批/网络分家,移除轨迹录制死开关`

### Task 5: 能力组子 tab + 工具默认全开档 + 反思自检拆平

**Files:**
- Create: `groups/CapabilitiesSection.tsx`
- Modify: `ManifestEditor.tsx`(GROUP_COMPONENTS 加 `capabilities: CapabilitiesSection`)
- Modify: `defaults.ts`(BASE_MANIFEST_YAML tools 种子 11→20)
- Modify: `groups/ModelRoutingSection.tsx`(Collapse 拆平)
- Modify: zh-CN.ts / en.ts
- Test: `groups/__tests__/CapabilitiesSection.test.tsx`、`__tests__/defaults.test.ts`、`groups/__tests__/ModelRoutingSection.test.tsx`

- [ ] **Step 1: CapabilitiesSection**:

```tsx
export function CapabilitiesSection({ formData, onChange, mcpSource }: {...}) {
  const { t } = useTranslation();
  return (
    <Tabs size="small" defaultActiveKey="tools" items={[
      { key: "tools", label: t("manifest_editor.tab_tools"), children: <FormView formData={formData} onChange={onChange} section="tools" /> },
      { key: "mcp", label: t("manifest_editor.tab_mcp"), children: <FormView ... section="mcp" mcpSource={mcpSource} /> },
      { key: "knowledge", label: t("manifest_editor.tab_knowledge"), children: <FormView ... section="knowledge" /> },
      { key: "skills", label: t("manifest_editor.tab_skills"), children: <FormView ... section="skills" /> },
      { key: "subagents", label: t("manifest_editor.tab_subagents"), children: <FormView ... section="subagents" /> },
    ]} />
  );
}
```

  ManifestEditor 里 capabilities 组现走 `FormView sections={[...]}` 堆叠路径 → 改走 GROUP_COMPONENTS;`mcpSource` prop 沿 ManifestEditor 现有传参链透传(对照现调用点)。**注意 leadingTabs mergeSection 逻辑**(ManifestEditor:251-298):merge 的是 basic section,不影响 capabilities;确认后不动。
- [ ] **Step 2: defaults.ts 种子**:tools 块追加 `web_search: {}`、`http: {}`(注意这两个是**结构化条目**——对照现有 setTool webSearch/http 写入的形状,保持 round-trip 一致)+ opt-in7 名条目;`policies.max_no_progress: 4` 一并种入(T6 依赖,此处先落)。defaults 测试:断言 20 工具名精确集合 + max_no_progress===4。
- [ ] **Step 3: ModelRoutingSection 拆平**:Collapse 删掉,改 `<Text strong>{t("model_group.panel_reflection")}</Text>` + 反思 FieldRow(help prop)+ 开启时 TUNING_DEFS 列表。测试:无 `.ant-collapse`;反思开关直接可见可点。
- [ ] **Step 4: i18n**:`agent_form.tools_config_note` 重写:zh「文件读写、产物保存、请求人工确认这些基础能力默认开启,不在此列出;要调整请到 YAML 视图。网页搜索开箱可用(平台自带免费搜索服务)。」en 同步。web_search/http/opt-in7 的 checkbox `_help` 文案审一遍大白话(缺场景补场景)。
- [ ] **Step 5: 全部跑绿 + tsc + commit** `feat(config-ui): 能力组五子tab + 工具默认全开档(种子20) + 反思自检拆平`

### Task 6: 预设档位「运行策略」

**Files:**
- Modify: `form_model.ts`(RUN_PROFILES + applyRunProfile + inferRunProfile)
- Create: `groups/BasicSection.tsx`(RunProfileCard + `FormView section="basic"`)
- Modify: `ManifestEditor.tsx`(GROUP_COMPONENTS 加 `basic: BasicSection`;确认 leadingTabs mergeSection="basic" 路径仍走 FormView——模板表单不受影响,预设卡只出现在租户 agent 编辑器的 basic 组)
- Modify: zh-CN.ts / en.ts
- Test: `__tests__/form_model_profiles.test.ts`、`groups/__tests__/BasicSection.test.tsx`

**Interfaces:**

```ts
export type RunProfile = "balanced" | "cost" | "capability";
export type RunProfileState = RunProfile | "custom";
/** 18 受管字段一键写入;值===该字段后端默认 → patch undefined(删键) */
export function applyRunProfile(m: unknown, profile: RunProfile): AgentManifest;
/** 全 18 字段精确匹配某档 → 该档;否则 "custom" */
export function inferRunProfile(m: unknown): RunProfileState;
/** 目标档与当前值的差异字段数(确认框文案用) */
export function countProfileDiff(m: unknown, profile: RunProfile): number;
```

- [ ] **Step 1: 失败测试(form_model 纯函数,linchpin)**:

```ts
it("applyRunProfile(cost) 写入全部 18 字段", () => {
  const m = applyRunProfile(BASE, "cost");
  expect(readTopK(m)).toBe(3);
  expect(readVerifyReads(m)).toBe(false);
  expect(readRecallMode(m)).toBe("per_session");
  expect(readAbstainThreshold(m)).toBe(0.2);
  expect(readMemoryBudgets(m).injectionTokenBudget).toBe(1000);
  // …corr 300 / consolidation false / maxIter 20 / noProgress 3 /
  // pr 0.6+2 / wm 0.6+10 / cc 0.6+2+4 / dynamicWorkers false — 逐项断言
});
it("applyRunProfile(balanced) 清空受管键,唯 max_no_progress 显式 4", () => {
  const m = applyRunProfile(applyRunProfile(BASE, "capability"), "balanced");
  const spec = (m as { spec: Record<string, unknown> }).spec;
  expect((spec.policies as Record<string, unknown>).max_no_progress).toBe(4);
  expect((spec.memory as { long_term?: Record<string, unknown> })?.long_term?.retrieve_top_k).toBeUndefined();
  // …其余 16 键逐项 undefined/absent 断言(变异:漏一个 setter 必红)
});
it("inferRunProfile 反推:apply 后 infer 回同档;任改一字段 → custom", () => { ... });
```

- [ ] **Step 2: 跑红。**
- [ ] **Step 3: 实现 form_model**:RUN_PROFILES 表按 spec §③(18 字段三档值,max_no_progress 4/3/6);applyRunProfile 用现有 setters 链式:setTopK→setVerifyReads→setRewriteReads→setRecallMode→setAbstainThreshold→patchMemoryBudgets→patchConsolidation→patchRunBudget({maxIterations,maxNoProgress})→patchContextGates({6 键})→setDynamicWorkersOn;**注意各 setter「值===默认删键」语义已内建于 patch 约定的传 undefined——applyRunProfile 自己算:目标值===后端默认 ? undefined : 值**(后端默认单源:文件顶新 `PROFILE_BACKEND_DEFAULTS` 常量,与各 FieldDef effectiveDefault 数值一致,注释互指)。
- [ ] **Step 4: BasicSection + RunProfileCard**:Radio.Group(三档,竖排卡片式:label + 一句描述);当前态 `inferRunProfile`;`custom` 时三档均不选中并显示「自定义」Tag;点档位 → `countProfileDiff`>0 时 `Modal.confirm`(标题「应用{档名}?」内容「将调整 N 项配置,单项仍可再改。」)确认后 `onChange(applyRunProfile(...))`。data-testid:`run-profile-card` / `run-profile-{balanced|cost|capability}`。测试:点 cost + 确认 → onChange 收到 applyRunProfile 结果;取消 → 不调 onChange(变异证)。
- [ ] **Step 5: i18n**:`run_profile.title`「运行策略」、`.balanced`「均衡推荐」`.balanced_desc`「日常够用,费用适中(默认)」、`.cost`「成本优先」`.cost_desc`「省 token:少记少想、更早压缩,长对话更省钱」、`.capability`「能力优先」`.capability_desc`「多记多想:步数翻倍、记忆更全,复杂任务更稳,费用更高」、`.custom`「自定义」、`.confirm_title`「应用「{{name}}」?」、`.confirm_body`「将调整 {{count}} 项配置;每一项之后仍可单独修改。」、`.hint`「选一档,记忆、步数、压缩等参数自动配好;下面各组仍可逐项微调。」en 同步。
- [ ] **Step 6: 跑绿 + 全量 + commit** `feat(config-ui): 运行策略三档预设一键应用(18 受管字段)`

### Task 7: 结构化输出编辑器

**Files:**
- Modify: `form_model.ts`(output-schema 读写 + 可表示性)
- Create: `widgets/OutputSchemaEditor.tsx`
- Modify: `FormView.tsx` prompt section(注脚块 → `<OutputSchemaEditor formData={formData} onChange={onChange} />`;`readOutputSchemaName`/`output_schema_on_hint/off_hint` 键随之 orphan → 删)
- Modify: zh-CN.ts / en.ts
- Test: `__tests__/form_model_output_schema.test.ts`、`widgets/__tests__/OutputSchemaEditor.test.tsx`

**Interfaces:**

```ts
export type SchemaFieldType = "string" | "number" | "integer" | "boolean" | "array_string" | "array_number";
export interface SchemaFieldRow { name: string; type: SchemaFieldType; required: boolean; description: string; }
export function readOutputSchemaRows(m: unknown): SchemaFieldRow[] | "unrepresentable" | undefined;
// undefined = 未配置;"unrepresentable" = 已配置但非平铺(只读降级)
export function setOutputSchemaRows(m: unknown, rows: SchemaFieldRow[] | null): AgentManifest;
// null → 删除 spec.output_schema 整块;rows → 写 json_schema,保留既有 name/strict 键
```

**可表示性判定**(readOutputSchemaRows):json_schema 满足全部 → rows,否则 "unrepresentable":顶层键 ⊆ {type, properties, required, additionalProperties};type 缺省或 "object";每个 property 值的键 ⊆ {type, description, items};type ∈ 六类(array 时 items.type ∈ {string,number} 且 items 无其他键);required 是 properties 键子集。生成方向:`{type:"object", properties:{[name]:{type,…(array→{type:"array",items:{type}}), ...(description && {description})}}, required:[勾必填的], additionalProperties:false}`;rows 为空数组 → `{type:"object", properties:{}, additionalProperties:false}`(后端拒空 dict,此形非空,合法)。

- [ ] **Step 1: 失败测试(form_model 纯函数)**:平铺 schema round-trip 语义等价(rows→set→read 回同 rows;含 required/description/两种 array);嵌套 object property → "unrepresentable";含 `$ref`/`oneOf`/顶层多余键 → "unrepresentable";set(null) 删整块;set 保留既有 `name:"custom"`/`strict:false`;未配置 → undefined。
- [ ] **Step 2: 跑红。Step 3: 实现 form_model。**
- [ ] **Step 4: OutputSchemaEditor**:关态=Switch(off)+一句 hint;开态=Switch(on)+行表(每行:字段名 Input[status error 当 `!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)`,非法名不写入 manifest]、类型 Select 六项、必填 Checkbox、说明 Input、删行 Button)+「添加字段」Button;开关从 on→off 且 rows 非空 → Popconfirm(确认清除);"unrepresentable" → Alert info「已配置(复杂结构)——请到 YAML 视图编辑,此处不可修改」+ Switch 隐藏。组件内行编辑即时 `onChange(setOutputSchemaRows(...))`(与全表单其余控件同步风格一致)。测试:加两字段 → manifest json_schema 形状精确断言;非法字段名 → 不触发 onChange 且 Input 报错态;unrepresentable fixture → 只读 Alert 且任意点击不产生 onChange(变异证)。
- [ ] **Step 5: i18n**:`output_schema.on_label`「按模板回复」、`.hint_off`「关闭:自由文本回复。开启后可要求 Agent 按固定字段结构回复(如工单:标题+等级+摘要),对接程序或表格更方便。」、`.col_name`「字段名」`.col_type`「类型」`.col_required`「必填」`.col_desc`「说明」、类型六项(文本/数字/整数/是否/文本列表/数字列表)、`.add_field`「添加字段」、`.name_invalid`「字段名须以字母或下划线开头,只能含字母数字下划线」、`.complex_readonly`「已配置(复杂结构)——请到 YAML 视图编辑」、`.off_confirm`「关闭将清除已定义的回复模板,确定?」。en 同步。删 `agent_form.section_output_schema_help/output_schema_on_hint/output_schema_off_hint` 中 orphan 者(section 标题键保留)。
- [ ] **Step 6: 跑绿 + 全量 + commit** `feat(config-ui): 结构化输出字段清单编辑器(平铺 JSON Schema 可视化,复杂结构只读护栏)`

### Task 8: 文案总审 + 注脚清理 + en 全量同步

**Files:**
- Modify: zh-CN.ts / en.ts;涉及组组件如注脚块删除(SandboxSection/ObservabilitySection 文案键不动结构)
- Test: 现有 i18n parity 测试守卫;新增 brief 长度 lint 测试(可选,见 Step 3)

- [ ] **Step 1: brief 全量审**:`run_budget.*_brief`(7)、`context_gates.*_brief`(18)、`security_gates.*_brief`(4)、`model_group.*_brief`(3)、`sandbox_group.pw_brief`、`observability_group.resp_cache_brief` 逐条重写至 ≤18 字大白话(T2/T4/T6/T7 新键已达标,此处收尾存量);超长内容并入对应 `_impact`。`_impact` 逐条确认「长解释+至少一个场景」,缺场景补(如 stream_deadline:「例:模型服务偶发卡住不吐字,超过这个秒数就换备用模型或报错,而不是干等」)。
- [ ] **Step 2: 注脚终态**:`agent_form.basic_yaml_note`/`dynamic_context_note`/`model_group.yaml_note` 各缩成一行「高级项(…列举 2-3 个…)请到 YAML 视图配置」;`sandbox_group.pw_brief/_impact` 按 spec 工作区文案(关=文件不保留、产物不受影响;开=每使用者一块不自动回收的磁盘);`observability_group.declarative_note` 追加 trajectory_recording 提及、`triggers_note` 大白话化(「定时任务请在『触发器』页面管理」)。
- [ ] **Step 3(可选,时间允许)**:vitest 新测断言所有 `*_brief` 键值 zh 长度 ≤ 24 字符(留余量),防回归。
- [ ] **Step 4: en 全量同步**:所有本 PR 新增/修改键逐个复核 en 版(键集 parity 测试保证集合一致;人工保证质量:地道、含场景、非机翻腔)。
- [ ] **Step 5: 终验**:`npx tsc -b --noEmit` exit 0;`npx vitest run` 全绿;grep 确认禁术语(委托树/LLMStreamStaleError/routing 规则/DefenseSpec)不在 zh 用户可见文案;grep 确认被删键(workflow_note/reserved_note/trajectory_recording/output_schema_on_hint 等)零残留引用。commit `docs(config-ui): 帮助文案总审——brief 一句话/impact 场景化/注脚清理`

---

## Self-Review 记录

- Spec 覆盖:①字段行v2→T1;②记忆/压缩/安全/预算/能力/模型重排→T2/T3/T4/T1/T5;②′工具默认→T5;③预设→T6(种子 max_no_progress→T5 Step2 落、T6 消费);④schema 编辑器→T7;⑤文案→各任务自带+T8 收尾;轨迹录制移除→T4。无缺口。
- 类型一致:FieldRowProps 于 T1 定义,T2/T4/T5 消费同名 props;applyRunProfile/inferRunProfile 签名 T6 内自洽;SchemaFieldRow 六类型与 i18n 六项对齐。
- 顺序依赖:T1 先行(契约);T2-T5 依赖 T1;T6 依赖 T5 Step2(种子);T7 独立;T8 收尾。
