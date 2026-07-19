# Agent 配置页 PR6:模型与路由组 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 模型与路由组补齐:ModelSelect 高级面板补 3 个活字段(`effort`/`adaptive_thinking`/`cache_enabled`)+ 给 `max_tokens`/`rate_limit_rpm` 补真话 hint;新增 `spec.reflection` 块可视化(激活开关+budget+deadline_s——现有「反思评判者」控件只写 routing 不激活反思,文案误导要纠);模型组升级 curated pane + YAML 指引 note(planning 规则/vision.fallbacks/azure 接线/api_key_ref 废弃)。

**Architecture:** T1 = ModelSelect widget 增量(它已有 catalog gating 与 advanced panel,effort 沿用 thinking 开关同款目录门);T2 = form_model 投影 reflection(**存在语义块**,同 long_term 处理);T3 = ModelRoutingSection curated pane(内嵌 FormView sections=["model"] + 反思 panel + note)+ 评判者文案纠偏。

**Spec:** docs/superpowers/specs/2026-07-18-agent-config-page-redesign-design.md(PR6 = 分期段第五组)

## Global Constraints

- PR1-PR5 契约全守:FieldRow/PolicyFieldList props 不变;YAML round-trip 未投影键保留;新投影字段配 round-trip 测试;i18n 三处先 grep 撞键;e2e 选择器契约;测试环境解析 en locale。
- **存在语义块规则**:`spec.reflection` 存在即激活反思节点(reflection.py:38 docstring + agent_factory.py:755-764);`{}` 合法=激活(双字段全有默认)。`patchReflectionTuning` 清空保 `{}` 绝不删;删块=关反思,只允许显式开关(`setReflectionOn`)做。budget/deadline panel 仅在反思开启时渲染(MemorySection 渲染门同款,form_model 层无守卫)。
- ModelSelect 是共享 widget(af-model/af-fallback/af-vision/评判者都用)——新控件全放 advanced panel 内,对既有调用方纯增量;catalog 门逻辑复用不重写。
- 每任务:`cd apps/admin-ui && pnpm exec vitest run src/components/manifest-editor && pnpm typecheck`;终门全量 + build + storybook + Playwright manifest 2 spec。
- IDE 诊断常 stale——真 tsc/vitest 定论;CJK 文件 grep 需 -a。
- 文案对照真实运行期代码(下节),照抄 verbatim。

## 运行期语义事实(2026-07-19 全库溯源,文案依据)

- `effort`(Literal low|medium|high|max|None=None):Anthropic 走 `output_config.effort`(anthropic.py:629);compat 厂商走 thinking payload(agent_factory.py:1700-1716);**构建期门禁**:目录 `thinking is None` 的模型设了 effort/thinking_enabled → AgentFactoryError(:2098-2107/:2136-2149)。
- `adaptive_thinking`(bool=False):仅 Anthropic 消费(anthropic.py:628 `thinking:{type:adaptive}`);无构建门,非 Anthropic 静默忽略。
- `cache_enabled`(bool=True):仅 Anthropic(anthropic.py:605-609 cache_control 标记);关闭给时效敏感提示词 Agent 用。
- `max_tokens`(int=4096):**仅 Anthropic 路径传给 provider**(agent_factory.py:2120);qwen/doubao 借它推导思考预算(:1708/:1716);OpenAI 系 adapter 不接收=静默忽略。既有控件裸标签无文案。
- `rate_limit_rpm`(int=60):`RateLimitedProvider.with_rpm`(agent_factory.py:1905-1906)→ AsyncLimiter 令牌桶(llm/rate_limit.py:80-96),超限排队等待不报错。既有控件裸标签无文案。
- `spec.reflection`(reflection.py:37-59):**存在即激活** reflect 节点(agent_factory.py:755-764);`budget: int=2, gt=0`(单 run 反思调用上限);`deadline_s: int=30, gt=0, le=600`(单次反思墙钟,超时强制接受当前答案)。
- 评判模型:`routing.rules[when=="reflection"].model` 只选模型;**无规则时反思复用主模型**(agent_factory.py:1929-1931 "A class with no rule reuses the default")。既有 `af-reflection-evaluator` 控件写的就是这条规则——**不激活反思**;现文案「让 Agent 回答前先自我反思、打分」夸大,需纠。
- **YAML-only/废弃**:`routing.rules[when=="planning"]`(仅 workflow.type==plan_execute 时生效)、`vision.fallbacks`(VL 回退链)、`base_url`/`azure_deployment`/`azure_api_version`(azure/self-hosted 接线)均活但不建控件;`api_key_ref` manifest 路径强制忽略+告警(agent_factory.py:1867-1892)=废弃。
- ModelSelect 现状:advanced panel 有 max_tokens/rate_limit_rpm(裸标签)/context_window(有 hint);thinking 开关目录门=仅目录有 thinking 旋钮的模型显示(ModelSelect.tsx:110-140);i18n 命名空间 `model_select`,hint 范式=`context_window_hint`。

---

### Task 1: ModelSelect 补 effort/adaptive_thinking/cache_enabled + 双 hint

**Files:**
- Modify: `apps/admin-ui/src/components/manifest-editor/widgets/ModelSelect.tsx`
- Modify: locale interface + `en.ts` + `zh-CN.ts`(`model_select` 命名空间追加,先 grep 撞键)
- Test: ModelSelect 既有测试文件追加(先找到它;若无则建 `widgets/__tests__/ModelSelect.test.tsx` 按邻近测试风格)

全部进 advanced panel(既有 max_tokens/rate_limit_rpm/context_window 之后),对象直改范式沿用(`onChange({ ...value, effort: v ?? undefined })`):

- **effort**:Select options low/medium/high/max + allowClear;**渲染门=与 thinking 开关同一目录条件**(目录无 thinking 旋钮不显示——防构建报错);清除→删键;testid `model-select-effort`。
- **adaptive_thinking**:Switch;**仅 `value.provider === "anthropic"` 显示**;开→`adaptive_thinking: true`,关(=默认)→删键;testid `model-select-adaptive`。
- **cache_enabled**:Switch;仅 anthropic 显示;默认 true——开(=默认)→删键,关→写 `false`(`checked ? undefined : false`);testid `model-select-cache`。
- max_tokens/rate_limit_rpm 补 hint(既有 context_window_hint 同款 Text 形式)。

i18n(zh verbatim;en 忠实对译):
- `effort_label`「努力程度」`effort_hint`「推理深度档位。留空=提供商默认。仅模型目录标注支持思考的模型可设,否则构建报错。」
- `adaptive_label`「自适应思考」`adaptive_hint`「由模型按任务难度自行决定思考深度(Anthropic 4.6+)。仅 Anthropic 生效。」
- `cache_label`「提示词缓存」`cache_hint`「Anthropic prompt caching,长会话显著省钱。默认开;提示词含时效内容的 Agent 可关。仅 Anthropic 生效。」
- `max_tokens_hint`「单次回复输出 token 上限。仅 Anthropic 路径生效(qwen/doubao 借它推导思考预算);OpenAI 系提供商忽略此值。」
- `rate_limit_hint`「对该模型的请求速率上限(次/分钟)。超限请求排队等待,不报错。」

- [ ] **Step 1: 失败测试**(anthropic+目录有 thinking 的模型 → 三控件可见;effort 选 high → value.effort==="high",清除→键删;cache 关→false、开→键删;adaptive 开→true、关→键删;OpenAI 系模型 → effort/adaptive/cache 全不渲染;max_tokens/rate_limit hint 文案渲染)
- [ ] **Step 2: 实现 + i18n 三处**
- [ ] **Step 3: scope vitest + typecheck;commit `feat(admin-ui): ModelSelect 补 effort/自适应思考/提示词缓存 + max_tokens/限流真话 hint`**

### Task 2: form_model 投影 spec.reflection

**Files:**
- Modify: `apps/admin-ui/src/components/manifest-editor/form_model.ts`
- Test: `__tests__/form_model.test.ts`(追加)

**Interfaces(Produces):**

```ts
// AgentManifest.spec 增:reflection?: { budget?: number; deadline_s?: number; [k: string]: unknown } | null
export function readReflectionOn(m: unknown): boolean;          // reflection != null(null/undefined 均=关)
export function setReflectionOn(m: unknown, on: boolean): AgentManifest;
//   on: 已有块保留不动;absent/null → reflection: {}
//   off: 删除 reflection 键(存在即激活,删=关是本开关的语义)
export interface ReflectionTuningFields { budget?: number; deadlineS?: number }
export function readReflectionTuning(m: unknown): ReflectionTuningFields;   // RAW
export function patchReflectionTuning(m: unknown, patch: Partial<ReflectionTuningFields>): AgentManifest;
//   "key" in patch;undefined 删键;块清空保留 `{}`(mergeBlock ?? {});
//   reflection absent/null 且 patch 净空 → 不物化(UI 渲染门保证不会发生)
```

- [ ] **Step 1: 失败测试**(setReflectionOn(true) 于 absent → `{}`;于已有 {budget:5} → 保留不动;setReflectionOn(false) → 键删;budget/deadlineS round-trip 经 YAML;清空最后一键 → reflection 保留 `{}`;reflection 未知键 patch 后保留;absent+净空 patch 不物化;与 spec 其它键共存不干扰;不可变性)
- [ ] **Step 2: 实现**
- [ ] **Step 3: scope vitest + typecheck;commit `feat(admin-ui): form_model 投影 spec.reflection(存在语义块)`**

### Task 3: ModelRoutingSection curated pane + 反思 panel + 评判者文案纠偏

**Files:**
- Create: `groups/ModelRoutingSection.tsx`
- Modify: `ManifestEditor.tsx`(`CURATED_GROUP_PANES` 加 `model`;stale 注释同步纠正)
- Modify: locale interface + `en.ts` + `zh-CN.ts`(命名空间 `model_group` 新增 + **纠既有 `section_reflection_evaluator_help`/`reflection_evaluator_hint` 文案**)
- Test: `groups/__tests__/ModelRoutingSection.test.tsx` + `__tests__/ManifestEditor.test.tsx` 追加;既有依赖 model 组走 stacked 路径的测试 repoint(语义不降)

结构:`<FormView sections={["model"]} …>`(转发照抄 MemorySection)→ Collapse 1 panel(默认折叠)「反思自评」→ 底部 YAML 指引 note(testid `model-yaml-note`)。

**反思 panel**:激活开关(直接用 FieldRow 包 Switch,fieldId `reflection`,非 PolicyFieldList——它切块存在性)+ **开启时**才渲染 `PolicyFieldList<ReflectionTuningFields>` 2 个 number FieldDef(MemorySection 渲染门同款条件 spread):

```ts
{ fieldId: "reflection.budget", i18nKey: "model_group.rf_budget",
  valueKey: "budget", kind: "number", effectiveDefault: 2, min: 1 }
{ fieldId: "reflection.deadline_s", i18nKey: "model_group.rf_deadline",
  valueKey: "deadlineS", kind: "number", effectiveDefault: 30, min: 1, max: 600 }
```

文案(zh verbatim;en 忠实对译):

**激活开关**(`rf_enable_label`/`_brief`/`_impact`):
- label「反思自评」
- brief「回答前由评判模型自我反思打分,不达标自动重试改进」
- impact「每轮反思多一次 LLM 调用:质量更高,延迟与成本上升。评判所用模型由下方「反思评判模型」规则指定;未配置规则时复用主模型。」

**budget**:label「反思次数上限」brief「单次运行最多反思调用次数,到顶接受当前答案」impact「调大=多轮自我改进、成本延迟线性涨;1=只评一次。」`_default`「2」

**deadline_s**:label「单次反思时限(秒)」brief「每次反思调用的墙钟上限,超时强制接受当前答案」impact「防评判模型卡死拖垮整个运行。调大容忍慢模型,调小保响应速度。上限 600。」`_default`「30」

**评判者文案纠偏**(既有键改写,zh verbatim;en 同步):
- `section_reflection_evaluator_help` →「指定反思自评所用的评判模型(routing 规则)。仅在上方「反思自评」开启后生效;未指定时反思复用主模型。」
- `reflection_evaluator_hint` →「只选模型,不开启反思——开关在「反思自评」面板。」

**YAML 指引 note**(`yaml_note`,testid `model-yaml-note`):
「以下能力暂经 YAML 视图配置:routing.rules 的 planning 规则(仅 plan_execute 工作流生效)、vision.fallbacks(视觉模型回退链)、base_url / azure_deployment / azure_api_version(azure 与自托管接线)。api_key_ref 已废弃:manifest 中设置会被忽略并告警。」

- [ ] **Step 1: 失败测试**(既有 af-model 段在 pane 内可见(抽查 model-select-provider、af-fallback);反思开关拨开 → manifest `spec.reflection` 为 `{}` + budget/deadline FieldRow 出现;budget 改 5 → `reflection.budget===5`;开关拨关 → reflection 键删 + 调优行消失;关闭时(显式 `reflection: null` manifest)调优行不渲染;`model-yaml-note` 渲染;`cfg-nav-model` → curated pane 非 stacked)
- [ ] **Step 2: 实现 + i18n(grep `model_group` 撞键;评判者两键改写双 locale)**
- [ ] **Step 3: scope vitest + typecheck;commit `feat(admin-ui): 模型与路由组 —— 反思块可视化+评判者文案纠偏+YAML 指引`**

### Task 4: 终门

- [ ] `pnpm typecheck && pnpm exec vitest run`(全量)+ `pnpm build` + storybook build + Playwright manifest-editor/manifest-edit 2 spec;有修才 commit `test(admin-ui): PR6 终门`

## Self-Review 已核

- effort 构建门(目录 thinking is None → AgentFactoryError)对应 UI 渲染门=thinking 开关同条件,防用户造出必炸 manifest ✓
- reflection 存在语义块:开关删块=本意;调优 patch 保 `{}` + 渲染门,长_term 同款三层防护 ✓
- 评判者控件误导是溯源实锤(只写 routing 规则,无规则时反思复用主模型 agent_factory.py:1929-1931),纠偏文案与新开关互指 ✓
- max_tokens「仅 Anthropic」真相直说(OpenAI 系 adapter grep 无接收)✓
- ModelSelect 共享 widget 纯增量(advanced panel 内),fallback/vision/评判者调用方自动获得新旋钮且语义正确(各自 ModelSpec 独立)✓
- budget/deadline 边界照抄 schema(gt=0 → min 1;deadline le=600)✓
- 无 TBD;全部新键 verbatim zh 齐 ✓
