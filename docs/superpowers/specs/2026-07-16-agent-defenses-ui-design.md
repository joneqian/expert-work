# Agent 防御守卫 UI — 设计文档

> 本文是端到端 LLM token 流式 epic 拆出的**子项目 1**(共 2 个)。子项目 2 = token 流式后端(见 `llm-token-streaming-epic` memory / 后续 spec)。两者独立可交付,唯一耦合点在本文 §7 说明。

**Goal:** 把 `DefenseSpec` 的全部防御开关暴露进 Agent 配置表单(可视化 Form 视图),让运维无需手改 manifest YAML 即可配置一个 agent 的安全姿态。

**Architecture:** 纯前端增量。整个 Agent 配置本就是 manifest-YAML 存法(整份 spec 作为一个 JSONB blob 存 `agent_spec.spec_json`),`defenses` 早已端到端跑通 —— create/update API、Pydantic 校验、持久化全支持。今天表单只是**从不生成 `defenses:` 那段 YAML**。因此本子项目**零后端/API/DB 改动**:只加 form_model 投影 + 一个 FormView "防御" section + ManifestEditor tab + i18n。

**Tech Stack:** React + TypeScript + antd(`Switch`/`Select`/`Alert`);现有 manifest-editor 框架(`specOf`/`patchSpec` 不可变投影范式);react-i18next(zh-CN + en)。

## Global Constraints

- **零后端改动。** 若发现需要改 control-plane / protocol / persistence,停下来上报 —— 说明设计前提被打破。
- **不可变更新。** 所有 `set*` 投影必须返回新 manifest 对象,不得原地改(镜像 `patchSpec`/`setReflectionEvaluator`)。
- **默认值省略。** 一个开关等于其 `DefenseSpec` 默认值时,**不写进 YAML**(保持 manifest 干净);仅非默认值落 key。
- **`form_model.ts` 被 `file(1)` 判为 `data`**(含非 UTF-8 多字节字符),裸 `grep` 会静默跳过 —— 用 `grep -a`。不是 bug,勿"修复"该文件编码。
- 前端语言:所有面向用户文案走 i18n,同时提供 zh-CN + en,不硬编码中文串。

---

## 1. 背景(为什么做)

`DefenseSpec`(`packages/expert-work-protocol/src/expert_work/protocol/agent_spec.py:1049`)是每个 agent 的安全姿态开关集,挂在 `AgentSpecBody.defenses`(:1113,`default_factory=DefenseSpec`)。今天要开关它们**只能手编 manifest YAML 的 `defenses:` 块**:可视化 Agent 表单(`FormView.tsx`)没有任何防御相关字段(搜 `grep -a` 确认 `FormView.tsx` + `form_model.ts` 对 `output_screen|output_judge|output_dlp|defenses` 零命中)。新建 agent 的种子 manifest(`defaults.ts:16` `BASE_MANIFEST_YAML`)也不含 `defenses:` 块,故新 agent 全落 `DefenseSpec` 默认值。

结果:运维要调一个 agent 的安全等级,得懂 YAML 语法 + 记住字段名。本子项目把这些开关可视化。

## 2. `DefenseSpec` 模型(要暴露的开关)

`extra="forbid"`,全 `Literal` 字段:

| 字段 | 取值 | 默认 | 语义 |
|---|---|---|---|
| `prompt_injection` | `spotlight` / `off` | `spotlight` | 对不可信通道内容加 spotlighting 标记(输入侧注入防护) |
| `output_screen` | `block` / `off` | **`block`** | 规则型输出筛查:拦截凭据/外泄形回复 |
| `output_judge` | `block` / `off` | `off` | 模型型 judge:每条终态回复一次 LLM 对齐/泄漏判定 |
| `output_judge_on_error` | `open` / `closed` | `open` | judge 失败(超时/宕)时 fail-open(放行)/ fail-closed(拦) |
| `action_screen` | `off` / `block` / `approval` | `off` | 每个 tool-call 前的 judge:off / 拦截 / 转人工审批 |
| `action_screen_on_error` | `open` / `closed` | `open` | action_screen 失败时的策略 |
| `output_dlp` | `redact` / `off` | `off` | 出站 PII 脱敏(email/手机/身份证/卡号 → `[redacted]`) |

注意:`output_screen` 默认 `block`(**开着**),其余默认关。

## 3. 组件设计

### 3.1 form_model.ts 投影(新增)

镜像现有 `readReflectionEvaluator`(:187)/`setReflectionEvaluator`(:335)范式:`read*` 用 `specOf(m)` 读出、`set*` 用不可变 patch 写回。新增一个 `patchDefenses(m, partial)` 助手(镜像 `patchLongTerm`),对 `spec.defenses` 做浅合并并在空对象时丢弃整个 `defenses` 键。

每字段一对读写(值类型即 §2 的 Literal 联合):

```
readPromptInjection / setPromptInjection            (spotlight | off)
readOutputScreen / setOutputScreen                  (block | off)
readOutputJudge / setOutputJudge                    (block | off)
readOutputJudgeOnError / setOutputJudgeOnError       (open | closed)
readActionScreen / setActionScreen                  (off | block | approval)
readActionScreenOnError / setActionScreenOnError     (open | closed)
readOutputDlp / setOutputDlp                        (redact | off)
readExtends(m): string | undefined                  (取 spec.extends,判是否挂提示)
```

**默认省略规则(Global Constraint):** `set*` 写入等于默认值时,从 `defenses` 删该键;`read*` 读不到键时返回默认值。这样运维把 `output_screen` 留在默认 `block` → YAML 无 `output_screen` 键;拨到 `off` → 写 `output_screen: off`。`patchDefenses` 合并后若 `defenses` 变空对象,则整个 `defenses` 键也丢弃。

**联动子字段:** `output_judge_on_error` 仅在 `output_judge==block` 时有意义;`action_screen_on_error` 仅在 `action_screen!=off` 时有意义。父开关关闭时,`set*` 一并清掉对应的 `_on_error` 键(避免留孤儿)。

### 3.2 FormView 新"防御"section

新增 `<section data-testid="af-defenses">`,挂进 FormView 的 section map 新键 `defenses`(镜像现有 `model`/`prompt` 等键返回的 JSX 片段;一个 tab 的 JSX 可含多个 `<section>` 子块)。结构:

```
[extends 提示]        ← readExtends(formData) 存在时,顶部 <Alert type="info">(§5 决策 2a)

输入防护 (子标题)
  prompt_injection    <Switch>  on=spotlight / off=off

输出防护 (子标题)
  output_screen       <Switch>  on=block / off=off        (默认开)
  output_judge        <Switch>  on=block / off=off
    └─ 开时展开: <Select> output_judge_on_error (open|closed) + <Alert type="warning">(§4)
  output_dlp          <Switch>  on=redact / off=off
    └─ 开时: <Alert type="info">(§4)

工具行为防护 (子标题)
  action_screen       <Select>  off | block | approval
    └─ 非 off 时展开: <Select> action_screen_on_error (open|closed) + <Alert type="info">(§4)
```

每个开关配 `<FieldHelp text={t(...)} testId="af-defenses-<field>" />`(讲清作用),沿用现有 `Heading`+`FieldHelp` 范式。子标题用现有 `<Text type="secondary">` 或轻量分隔,匹配现有 section 视觉。

### 3.3 影响告警(决策 1 重点:提示必须足够清晰)

除每开关的常驻 `FieldHelp` 外,以下"有代价/降安全"的状态额外挂条件式 `<Alert>`:

| 开关状态 | Alert 类型 | 文案要点 |
|---|---|---|
| `output_judge` 开 | warning | 每条回复额外一次 LLM 调用(↑延迟 ↑成本);**禁用该 agent 的逐-token 流式响应**(回复整条一次性返回);judge 用哪个模型在**平台设置**里配(非本页) |
| `output_screen` 拨 off | warning | 关闭后不再拦截凭据/外泄形回复(默认开,**不建议关**) |
| `output_dlp` 开 | info | 会改写含 PII 的合法回复(如"你的邮箱是 a@b.com"→"你的邮箱是[redacted]") |
| `action_screen` = block/approval | info | 每个工具调用前额外一次判定;工具轮↑延迟(approval 还会转人工) |
| `prompt_injection` 拨 off | warning | 关闭对不可信内容的 spotlighting 标记,**降低注入防护** |

文案全走 i18n。judge 那条含"禁用流式"—— 见 §7 排期说明。

### 3.4 ManifestEditor tab + i18n

- `ManifestEditor.tsx`:`MANIFEST_TABS`(:25)在 `governance` 后加 `{ value: "defenses", labelKey: "manifest_editor.tab_defenses" }`;`FormSection` 类型(`FormView.tsx` 导出)并入 `"defenses"`。`FORM_SECTIONS`/`isFormSection` 自动跟随(从 `MANIFEST_TABS` 派生)。
- i18n(`zh-CN.ts` + `en.ts`,`agent_form.*` / `manifest_editor.*` 命名空间):tab 标题、section 标题、三个子标题、7 个开关 label + help、5 条影响告警、extends 提示。

## 4. 数据流

表单态 = 解析后的 manifest 对象(`AgentManifest`)。`read*` 从 `spec.defenses` 投影出各控件值;控件 `onChange` → `set*` 不可变写回 → `ManifestEditor` 在 Form→YAML 切换/提交时序列化为 YAML → `onChange(yaml)` → 父组件 POST `/v1/agents`(create)/ PUT `/v1/agents/{name}/{version}`(update)。后端路径**完全不变**(`ManifestPayload{manifest_yaml}`)。同一底层对象也经由原始 YAML tab 双向 round-trip —— 手写 YAML 的 `defenses:` 块会正确回填 Form 控件。

## 5. 关键决策(已与用户确认)

1. **暴露全部 7 个开关**(非仅输出那 3 个),按输入/输出/工具行为分组。理由:运维应看到完整安全姿态,且都已在 YAML 里,搬 UI 只是可见化。
2. **judge 模型保持平台级**(`PlatformJudgeSection`,system-admin 配一次全平台共用);agent 表单**只管是否启用** `output_judge`,不做 per-agent 模型覆盖(那需改 schema + 后端,超本子项目范围)。启用时告警指向平台设置。
3. **`extends` 安全下限 = 轻量提示(决策 2a)。** agent 继承模板时 `defenses` 是 `SECURITY_FLOOR` 层(`agent_template_resolve.py:131` `_floor_defenses` 做逐开关取最严合并):表单能把开关调弱,但 resolver 会**静默收紧回模板下限**,造成"UI 显示 vs 实际生效"不一致。v1 处理:`spec.extends` 存在时,section 顶挂一条 `<Alert type="info">`"此 agent 继承模板,模板可能强制比这里更严的防御";**不**去解析计算真实 floor(那是 2b,推后)。

## 6. 明确排除(Out of Scope)

- token 流式行为本身(子项目 2 后端)。
- per-agent judge 模型覆盖(留平台级)。
- 计算并展示真实模板 floor / 禁用低于 floor 的选项(决策 2b)。
- 任何后端 / API / DB / protocol 改动。

## 7. 排期皱褶:judge 告警里的"禁用流式"句

先做本子项目(守卫 UI)、后做子项目 2(流式)。本 UI 上线时流式尚不存在,故 §3.3 judge 告警里"禁用逐-token 流式"一句在那一刻**指向即将上线的流式**;在流式落地前,judge 开启也无流式可禁(无害)。两个子项目将紧邻交付,该句一旦子项目 2 落地即字面为真。**用户已接受此措辞现在就写入。**

## 8. 测试

- **component 测试**(镜像现有 FormView 测试,antd + testing-library):
  - 渲染"防御" section,7 个控件出现且初值 = 当前 manifest 的 `defenses`(默认值正确回显)。
  - 逐开关拨动 → 断言 `onChange` 吐出的 manifest 对象 `spec.defenses.*` 正确(含**默认省略**:留默认 → 无该键;`defenses` 变空 → 无 `defenses` 键)。
  - 联动显隐:`output_judge` 开 → `output_judge_on_error` Select + warning Alert 可见;关 → 隐藏且 `_on_error` 键被清。`action_screen` 同理。
  - `extends` 提示:`spec.extends` 存在 → info Alert 出现;不存在 → 不出现。
  - YAML round-trip:手写含 `defenses:` 的 YAML → 切 Form → 控件正确回填。
- **typecheck**:`pnpm typecheck` 通过(`FormSection` 联合含 `defenses`,form_model 投影类型对齐 `AgentManifest`)。

## 9. 落地文件清单

- 改 `apps/admin-ui/src/components/manifest-editor/form_model.ts` —— 加 `patchDefenses` + 7×2 读写投影 + `readExtends`(**用 `grep -a` 操作该文件**)。
- 改 `apps/admin-ui/src/components/manifest-editor/FormView.tsx` —— `FormSection` 加 `defenses`;section map 加 `defenses` JSX(§3.2 结构 + §3.3 告警)。
- 改 `apps/admin-ui/src/components/manifest-editor/ManifestEditor.tsx` —— `MANIFEST_TABS` 加 `defenses` tab。
- 改 `apps/admin-ui/src/i18n/locales/zh-CN.ts` + `en.ts` —— 全部新 i18n 键。
- 加 `apps/admin-ui/src/components/manifest-editor/__tests__/`(或现有 FormView 测试同址)—— §8 component 测试。

## 10. 参考锚点

- `DefenseSpec`:`packages/expert-work-protocol/src/expert_work/protocol/agent_spec.py:1049`(字段 1057-1097),`AgentSpecBody.defenses`:1113,`extends`:1108。
- 表单范式:`FormView.tsx:258-323`(reflection-evaluator + vision section 范式);`form_model.ts:187/335`(read/set 投影);`ManifestEditor.tsx:25-47`(tabs)。
- 安全下限合并:`agent_template_resolve.py:131` `_floor_defenses`。
- 后端(不改,仅参考):`services/control-plane/src/control_plane/api/agents.py:454`(create)、`:986`(update)、`ManifestPayload`:78;DB `agent_spec.spec_json` JSONB。
