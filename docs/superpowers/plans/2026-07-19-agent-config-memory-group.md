# Agent 配置页 PR5:记忆组 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 记忆组升级为 curated pane:保留既有 af-memory-* 控件(内嵌 FormView,零重造),补齐两个缺失的活字段(`injection_token_budget`/`correction_token_budget`)+ 后台记忆整理开关(`policies.memory_consolidation.enabled`)+ aux_model YAML 指引 + 保留字段说明(`short_term`/`inject_memory` 零消费者)。

**Architecture:** MemorySection curated pane = 内嵌 `<FormView sections={["memory"]}>` + 两个默认折叠 Collapse panel(注入预算 / 后台记忆整理)+ 保留字段 note;form_model 新增 `readMemoryBudgets`/`patchMemoryBudgets`(**long_term 是存在语义块,清空保 `{}` 绝不删**)与 `readConsolidation`/`patchConsolidation`(可选块,标准 drop-empty);ManifestEditor `CURATED_GROUP_PANES` 加 `memory`。

**Spec:** docs/superpowers/specs/2026-07-18-agent-config-page-redesign-design.md(PR5 = 分期段第四组;范围经运行期溯源核定)

## Global Constraints

- PR1-PR4 契约全守:FieldRow/PolicyFieldList props 不变;YAML round-trip 未投影键保留;新投影字段配 round-trip 测试;i18n 三处先 grep 撞键;e2e 选择器 `cfg-nav-<id>`/`cfg-pane`;测试环境解析 en locale。
- **存在语义块规则(#1017 变体)**:`memory.long_term` 的存在与否即记忆开关(`agent_factory.py:1998-2001` 只判 `long_term is not None`;`{}` = 开、absent = 关)。`patchMemoryBudgets` 清空后必须保留 `long_term: {}`,**删块=静默关记忆**。`memory` 外层块同理不删(`{}`≡absent 语义同为关,但不做无谓删除)。`policies.memory_consolidation` 是 default_factory 可选块,标准 mergeBlock drop-empty 即可;patch enabled 时 `aux_model` 未知键必须保留。
- 既有 af-memory-* 控件**零重造**(toggle/topk/writeback/verify_reads/min_importance/reconcile/recall_mode 已存在,FormView.tsx:485-655)——复用=内嵌 FormView,不迁移不改动。
- 每任务:`cd apps/admin-ui && pnpm exec vitest run src/components/manifest-editor && pnpm typecheck`;终门全量 + build + storybook + Playwright manifest 2 spec。
- IDE 诊断常 stale——真 tsc/vitest 定论。
- 文案已对照真实运行期代码(见下节),照抄 verbatim。

## 运行期语义事实(2026-07-19 全库溯源,文案依据)

- `injection_token_budget`(默认 2000,[100,100000]):`builder.py:615` 渲染召回记忆按相关性序贪心装入,到预算截断(边界条目加截断标记)。`retrieve_top_k` 只限条数,单条超长记忆靠本预算兜底。
- `correction_token_budget`(默认 500,[0,100000]):`builder.py:616`——用户明确纠正类记忆(confidence==1.0)优先占用的专属配额,保证普通记忆挤不掉用户纠正;0=不保底。
- `policies.memory_consolidation`(agent_spec.py:790-815,仅 `enabled: bool=True` + `aux_model: ModelSpec|None`):**控制面后台 worker**(`memory_consolidator.py`,默认每 14400s=4h 一轮),两趟:①聚类相似临时记忆→辅助 LLM 合并沉淀 ②孤条噪音清理。非运行路径,不影响会话延迟;按 `usage_kind='memory_consolidation'` 计费;聚类阈值(min_cluster_size/similarity)是**租户级配置**不在 manifest;aux_model 缺省用平台配置(`EXPERT_WORK_MEMORY_CONSOLIDATOR_DEFAULT_AUX_MODEL`,默认 claude-sonnet-4-6)。
- **保留死字段(零消费者,全库 grep 确认)**:`memory.short_term`(free-form dict,唯一命中=schema 声明)、`dynamic_context.inject_memory`(唯一命中=schema+默认值测试;兄弟键 inject_current_date 有消费者 agent_factory.py:901,它没有;M2-C 占位)。记忆是否启用**只**由 `memory.long_term` 声明决定。
- 既有 UI 盘点:af-memory-toggle(long_term 开关,开时播种 {retrieve_top_k:5, write_back:true, recall_mode:"per_session"})/topk/writeback + advanced 内 verify-reads/min-importance/reconcile/recall-mode;form_model 已有 `readMemoryOn`(:260)/`patchLongTerm`(:413)。两预算字段无控件无 reader。

---

### Task 1: form_model 投影 记忆预算 + 后台整理

**Files:**
- Modify: `apps/admin-ui/src/components/manifest-editor/form_model.ts`
- Test: `apps/admin-ui/src/components/manifest-editor/__tests__/form_model.test.ts`(追加)

**Interfaces(Produces):**

```ts
// AgentManifest.spec 类型增:
//   memory?.long_term 增 injection_token_budget?: number; correction_token_budget?: number
//   policies 增 memory_consolidation?: { enabled?: boolean; [k: string]: unknown }
export interface MemoryBudgetFields {
  injectionTokenBudget?: number;
  correctionTokenBudget?: number;
}
export function readMemoryBudgets(m: unknown): MemoryBudgetFields;
export function patchMemoryBudgets(m: unknown, patch: Partial<MemoryBudgetFields>): AgentManifest;

export interface ConsolidationFields {
  consolidationEnabled?: boolean;
}
export function readConsolidation(m: unknown): ConsolidationFields;
export function patchConsolidation(m: unknown, patch: Partial<ConsolidationFields>): AgentManifest;
```

- readers 返回 RAW 存储值(undefined=未设;不学老 readers 的 `?? default` 内嵌——显示层给 effective 默认)。
- `patchMemoryBudgets`:`"key" in patch` 判定;undefined 删键;**long_term 清空保留 `{}`(存在语义块——删=关记忆)**:`mergeBlock(longTerm, patch) ?? {}`;`memory` 外层用 `{...(memory ?? {}), long_term: merged}`;memory 本就 absent 且 patch 净空 → 不物化(absent 时预算面板本就不该渲染)。long_term 现有键(retrieve_top_k 等)与 memory 未知键原样保留。**不改动既有 `patchLongTerm`/`readMemoryOn`。**
- `patchConsolidation`:policies.memory_consolidation 两层嵌套,镜像 PR2 context-gates 的 policies 子块 idiom(标准 mergeBlock,空块删——default_factory 可选块,非 required);patch enabled 时块内 `aux_model` 未知键保留。

- [ ] **Step 1: 失败测试**(预算双键设值 round-trip 经 YAML;清空(undefined)且 long_term 只剩预算键 → `long_term` 保留为 `{}` 且 retrieve_top_k 等兄弟键场景各自保留;memory absent + 净空 patch → 不物化;consolidationEnabled=false round-trip;enabled 清空(undefined)→ memory_consolidation 空块删、policies 若空亦删;块内 aux_model 未知键 patch enabled 后保留;与 readMemoryOn/patchLongTerm 共存不干扰)
- [ ] **Step 2: 实现**
- [ ] **Step 3: scope vitest + typecheck 全过;commit `feat(admin-ui): form_model 投影 记忆注入预算 + memory_consolidation`**

### Task 2: MemorySection curated pane + 文案

**Files:**
- Create: `apps/admin-ui/src/components/manifest-editor/groups/MemorySection.tsx`
- Modify: `apps/admin-ui/src/components/manifest-editor/ManifestEditor.tsx`(`CURATED_GROUP_PANES` 加 `memory`;若有注释列 memory 为普通 stacked 组则同步纠正)
- Modify: locale interface + `en.ts` + `zh-CN.ts`(命名空间 `memory_group`,先 grep 撞键)
- Test: `groups/__tests__/MemorySection.test.tsx` + `__tests__/ManifestEditor.test.tsx`(追加 memory 组断言;若既有测试依赖 memory 组走 stacked FormView 路径需同步 repoint)

结构:`<FormView sections={["memory"]} …>`(props 转发照抄 SecuritySection 对 FormView 的最小转发)→ Collapse 2 panel(**默认全折叠**)①注入预算 ②后台记忆整理 → 底部保留字段 note(testid `memory-reserved-note`)。**注入预算 panel 仅在 `readMemoryOn(formData)` 为 true 时渲染**(记忆关着时预算无意义,且避免向 absent long_term 写键)。

FieldDefs:

```ts
// panel ①(PolicyFieldList<MemoryBudgetFields>)
{ fieldId: "memory.long_term.injection_token_budget", i18nKey: "memory_group.inj_budget",
  valueKey: "injectionTokenBudget", kind: "number", effectiveDefault: 2000, min: 100, max: 100000 }
{ fieldId: "memory.long_term.correction_token_budget", i18nKey: "memory_group.corr_budget",
  valueKey: "correctionTokenBudget", kind: "number", effectiveDefault: 500, min: 0, max: 100000 }
// panel ②(PolicyFieldList<ConsolidationFields>)
{ fieldId: "policies.memory_consolidation.enabled", i18nKey: "memory_group.consolidation",
  valueKey: "consolidationEnabled", kind: "switch", effectiveDefault: true }
```

文案(zh verbatim;en 忠实对译):

**injection_token_budget**
- `inj_budget_label`「记忆注入 token 预算」
- `inj_budget_brief`「召回记忆渲染进提示词的 token 上限——按相关性顺序贪心装入,超出截断」
- `inj_budget_impact`「召回条数由 retrieve_top_k 限制,但单条超长记忆可能撑爆注入块,本预算兜底(边界条目截断并加标记)。调大=更多记忆上下文、每轮更贵;调小=省 token 但长记忆可能被截。」
- `inj_budget_default`「2000」

**correction_token_budget**
- `corr_budget_label`「用户纠正保底预算」
- `corr_budget_brief`「用户明确纠正类记忆(confidence=1.0)优先占用的专属 token 配额」
- `corr_budget_impact`「保证普通记忆挤不掉用户的明确纠正:纠正类条目优先划拨最多这些 token,再轮到普通记忆分配剩余预算。设 0 = 不保底。」
- `corr_budget_default`「500」

**consolidation enabled**
- `consolidation_label`「后台记忆整理」
- `consolidation_brief`「控制面后台任务(默认每 4 小时一轮)聚类合并相似临时记忆、清理噪音条目——非运行路径,不影响会话延迟」
- `consolidation_impact`「关闭后该 Agent 的长期记忆不再自动去重、沉淀与降噪,临时记忆会持续堆积。整理由辅助模型执行并按 memory_consolidation 用途计费;聚类阈值为租户级配置,不在本 manifest。」
- (switch 无默认徽章)

**panel ② 底部 aux_model 说明**(`aux_model_note`,Text):
「整理所用辅助模型默认为平台配置(claude-sonnet-4-6);如需为本 Agent 单独指定,请在 YAML 视图编辑 policies.memory_consolidation.aux_model(完整 ModelSpec 块)。」

**保留字段 note**(`reserved_note`,testid `memory-reserved-note`):
「memory.short_term 与 dynamic_context.inject_memory 当前为保留字段:通过校验但运行时不读取。记忆是否启用只由 memory.long_term 是否声明决定(上方开关)。」

- [ ] **Step 1: 失败测试**(既有 af-memory 控件在 memory pane 内可见(抽查 af-memory-toggle、af-memory-recall-mode);3 新 `data-field-id`;注入预算改 3000 → manifest `memory.long_term.injection_token_budget === 3000`;记忆关(long_term: null)时注入预算 panel 不渲染;consolidation 拨 false → `policies.memory_consolidation.enabled === false`;拨回 true(默认)→ 键删;`memory-reserved-note` 渲染;ManifestEditor 点 `cfg-nav-memory` → curated pane 而非 stacked FormView 路径)
- [ ] **Step 2: 实现 + i18n 三处(grep `memory_group` 撞键)**
- [ ] **Step 3: scope vitest + typecheck;commit `feat(admin-ui): 记忆组 —— 注入预算+后台整理可视化,保留字段说明`**

### Task 3: 终门

- [ ] `pnpm typecheck && pnpm exec vitest run`(全量)+ `pnpm build` + storybook build + Playwright manifest-editor/manifest-edit 2 spec;有修才 commit `test(admin-ui): PR5 终门`

## Self-Review 已核

- 两预算字段消费者实证(builder.py:615-616)、consolidation worker 实证(memory_consolidator.py)、死字段双确认(short_term/inject_memory 全库唯一命中=schema)✓
- 存在语义块风险(long_term 删=关记忆)在约束+patch 语义+测试三处点名 ✓
- 零重造:既有 7 控件走内嵌 FormView,新增仅 3 FieldDef + 2 note ✓
- aux_model 是完整 ModelSpec 块,不做残缺投影,走 YAML 指引(与 PR3 dict-note 同范式)✓
- 预算 panel 门在 readMemoryOn,规避 absent long_term 写键 ✓
- 无 TBD;三字段四键文案齐(switch 省 default 徽章)✓
