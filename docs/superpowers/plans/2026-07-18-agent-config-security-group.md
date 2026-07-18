# Agent 配置页 PR3:安全与防护组 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 安全与防护组升级为 curated pane:保留既有 defenses/governance 控件(#998 已交付,不重造),新增 **sandbox.network 3 字段**(出网模式/白名单/黑名单)与 **tool_use_enforcement**;消除 tool_output_budget 同键双控件;rate_limit/pii/safety(后端为无 schema 的 permissive dict)不建控件、放 YAML 指引说明。

**Architecture:** field_defs 扩两种 kind(`select`/`tags`);SecuritySection curated pane = 内嵌 `<FormView sections={["defenses","governance"]}>` + 两个新 Collapse 子区(网络出网/工具强制)+ dict 说明行;ManifestEditor `CURATED_GROUP_PANES` 加 `security`。

**Spec:** docs/superpowers/specs/2026-07-18-agent-config-page-redesign-design.md(PR3 = 分期段第二组)

## Global Constraints

- PR1/PR2 契约全守(FieldRow props 不变;YAML round-trip 未投影键保留;新投影字段配 round-trip 测试;i18n 三处先 grep 撞键;文案对照真代码)。
- 既有 defenses/governance 控件**零重造**(#998 交付物,复用=内嵌 FormView);唯一删除 = governance-advanced 的 `af-tool-budget`(与 PR2 上下文组 `tool_output_budget.enabled` 同 manifest 键双控件,PR2 席位为正)。
- 每任务:`cd apps/admin-ui && pnpm exec vitest run src/components/manifest-editor && pnpm typecheck`;终门全量 + Playwright 4 spec。
- IDE 诊断常 stale——真 tsc/vitest 定论。

## Schema 事实(本会话对源核对)

- `spec.sandbox.network`(NetworkSpec):`egress: Literal["none","direct","proxy"]="proxy"`;`allowlist: list[str]=[]`(空=放行公网,SSRF/内网仍拦全审计;非空=strict 只允这些;**校验拒 `["*"]`**);`denylist: list[str]=[]`(**优先于 allowlist**,精确域或子域匹配)。
- `spec.policies.tool_use_enforcement: Literal["auto","on","off"]="auto"`(auto=除 Claude/GPT 等可靠自发调用工具家族外全启用)。
- `spec.policies.{rate_limit,pii,safety}: dict[str,Any]`(**permissive dict,无 schema**——"tightening deferred to owning Streams";不可视化)。

---

### Task 1: field_defs 扩 kind:"select" + kind:"tags"

**Files:**
- Modify: `apps/admin-ui/src/components/manifest-editor/groups/field_defs.tsx`
- Test: `groups/__tests__/field_defs.test.tsx`(追加)

**Interfaces(Produces):**

```ts
// FieldDef 增量(现有字段不动):
//   kind: "number" | "switch" | "percent" | "select" | "tags";
//   effectiveDefault: number | boolean | string | readonly string[] | null;
//   options?: readonly string[];              // select 专用,值即 option
//   optionLabelKey?: string;                  // select option 的 i18n 前缀:`${optionLabelKey}_${option}`
// PolicyFieldListProps.values 值域扩 string | readonly string[]
```

- select → antd `Select`(options 由 `options` 映射,label 走 `t(\`${optionLabelKey}_${opt}\`)`,无 optionLabelKey 则裸值);**选回 effectiveDefault → patch undefined(删键)**,与 switch 语义一致。
- tags → antd `Select mode="tags"`(自由输入串列表);**清空(空数组)→ patch undefined(删键)**——空数组即后端默认;isDefault 判定:undefined 或与 effectiveDefault 数组深比较相等(空数组场景即 length 0)。
- 徽章:select 同 number 语义(默认灰"Default <值>"/改动蓝值);tags 抑制徽章(列表值不宜塞 Tag,同 switch 处理)。

- [ ] **Step 1: 失败测试**(select 渲染 options+选非默认写值+选回默认删键+option label i18n;tags 输入两项写数组+清空删键+徽章抑制;values 类型扩展 typecheck)
- [ ] **Step 2: 实现**
- [ ] **Step 3: scope vitest + typecheck 全过;commit `feat(admin-ui): field_defs 扩 select/tags 控件形态`**

### Task 2: form_model 投影 sandbox.network + tool_use_enforcement

**Files:**
- Modify: `apps/admin-ui/src/components/manifest-editor/form_model.ts`
- Test: `__tests__/form_model.test.ts`(追加)

**Interfaces(Produces):**

```ts
// AgentManifest.spec 增:sandbox?: { network?: { egress?: string; allowlist?: string[];
//   denylist?: string[]; [k: string]: unknown }; [k: string]: unknown }
// policies 增:tool_use_enforcement?: string
export interface SecurityFields {
  egress?: string;
  allowlist?: string[];
  denylist?: string[];
  toolUseEnforcement?: string;
}
export function readSecurity(m: unknown): SecurityFields;
export function patchSecurity(m: unknown, patch: Partial<SecurityFields>): AgentManifest;
```

- patch 语义同 PR2(`"key" in patch`;undefined 删键;network 块空则删,network 删后 sandbox 若空且无未知键则删;不物化空父块;mergeBlock 复用——注意两层嵌套 sandbox→network,mergeSubBlock 需再包一层,镜像 policies 处理)。
- **sandbox 是既有 manifest 常见块**(runtime/image/resources 等未知键必须保留)——round-trip 测试必须含「sandbox 有 runtime+resources 未知键,patch egress 后全保留」。

- [ ] **Step 1: 失败测试**(egress round-trip;allowlist 数组 round-trip;sandbox 未知键保留;undefined 删键+network 空块删+sandbox 有未知键时不删 sandbox;tool_use_enforcement 在 policies 与 PR2 四块共存不干扰)
- [ ] **Step 2: 实现**
- [ ] **Step 3: vitest form_model + typecheck;commit `feat(admin-ui): form_model 投影 sandbox.network + tool_use_enforcement`**

### Task 3: SecuritySection curated pane + 文案 + 双控件消除

**Files:**
- Create: `groups/SecuritySection.tsx`
- Modify: `ManifestEditor.tsx`(CURATED_GROUP_PANES 加 security;确认该组不再走 FormView stacked 普通路径)
- Modify: `FormView.tsx`(governance-advanced **删 `af-tool-budget`** 控件;`readToolBudgetOn`/`setToolBudgetOn` 若成孤儿则删,先 grep)
- Modify: locale interface + en.ts + zh-CN.ts(命名空间 `security_gates`)
- Test: `groups/__tests__/SecuritySection.test.tsx` + 更新受影响 FormView/ManifestEditor 测试

结构:`<FormView sections={["defenses","governance"]} …>`(转发 SecuritySection 收到的 formData/onChange;FormView 其余 props 查 ManifestEditor 现调用照抄)→ Collapse 2 panel(**默认全折叠**——defenses/governance 是主内容):①网络出网 ②工具强制 → 底部 dict 说明行。

文案(zh verbatim;en 忠实对译):

组内说明(panel ①顶部):「沙箱内代码与工具的对外网络策略,三层判定:出网模式 → 黑名单(优先)→ 白名单。」

**①网络出网(sandbox.network)**
- egress:label「出网模式」brief「沙箱对外网络总闸:proxy=经凭据代理(默认)/direct=直连/none=断网」impact「none 下沙箱内一切外呼不可用;direct 绕过代理凭据注入与集中审计,一般不建议;proxy 经凭据代理出网、全审计。」`_default`「proxy」;select options ["proxy","direct","none"],optionLabelKey security_gates.egress_opt
- allowlist:label「域名白名单」brief「非空=只允许这些域名;留空=放行公网(SSRF/内网探测仍拦截,全审计)」impact「精确域或子域匹配。不允许通配 ['*'](提交校验会拒)。与黑名单同在时黑名单优先。」`_default`「留空(放行公网)」;tags
- denylist:label「域名黑名单」brief「无论白名单/默认放行,强制拦截这些域名」impact「优先级高于白名单;精确域或子域匹配。适合"放行公网但屏蔽个别坏目标"。」`_default`「留空」;tags

**②工具强制(policies.tool_use_enforcement)**
- label「工具调用强制」brief「向系统提示词追加强制块:必须用工具获取实时事实、立即行动、禁止编造工具输出」impact「auto(默认)=除 Claude/GPT 等可靠自发调用工具的模型家族外全部启用——新接入弱模型免改配置即获强制;on/off=无视模型强制开/关。」`_default`「auto」;select ["auto","on","off"],optionLabelKey security_gates.enforce_opt

**dict 说明行**(Text,testid `security-gates-dict-note`):「速率限制 / PII / 安全策略暂为自由字典(后端 schema 未定型),请在 YAML 视图编辑。」

- [ ] **Step 1: 失败测试**(defenses+governance 控件在 security pane 内可见(抽查 af-defenses-output-screen、af-approval)/4 新 data-field-id/egress 选 none → manifest sandbox.network.egress/allowlist 输入两域名 → 数组/tool_use_enforcement 选 on → policies/`af-tool-budget` 不再存在于 governance/dict 说明行渲染)
- [ ] **Step 2: 实现 + i18n 三处(grep `security_gates` 撞键)+ 孤儿 helper 处理**
- [ ] **Step 3: scope vitest + typecheck;commit `feat(admin-ui): 安全与防护组 —— 网络出网+工具强制可视化,消 tool-budget 双控件`**

### Task 4: 终门

- [ ] `pnpm typecheck && pnpm exec vitest run`(全量)+ `pnpm build` + storybook + Playwright 4 spec;有修才 commit `test(admin-ui): PR3 终门`

## Self-Review 已核

- rate_limit/pii/safety 不可视化的裁定有源依据(permissive dict 注释)✓
- tags 空数组=默认=删键,与 allowlist「空=放行公网」语义自洽 ✓
- 双控件消除方向:PR2 席位为正(上下文组 budget panel),governance-advanced 删 ✓
- sandbox 未知键保留是本 PR 最大风险点,round-trip 测试点名 ✓
- 无 TBD;四键文案齐(tags/select 的 `_default` 均给)✓
