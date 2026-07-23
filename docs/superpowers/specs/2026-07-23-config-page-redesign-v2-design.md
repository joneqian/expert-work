# Agent 配置页重设计 v2 —— 设计稿

日期:2026-07-23 · 分支:`config-page-redesign`(基于 main b843329f)· 纯前端,零后端改动

## 背景与目标

#1044 把预算/压缩两组改成了 `PolicyFieldTable`(说明列常显),实际效果是一面墙的字;记忆/安全两组仍是 FormView 内嵌 + 多层 Collapse + 底部技术注脚。用户(租户管理员,操作者为非技术人员)反馈六处问题:

1. 结构化输出没有可视化配置(只能去 YAML 手写)。
2. 记忆/预算/压缩/安全等模块配置项多,需要按「能力、成本、稳定」预置最优默认值,不然一个个配太麻烦。
3. 记忆配置界面设计不合理(多层折叠嵌套)。
4. 运行预算与超时界面离谱(说明列字墙)。
5. 上下文压缩界面同样离谱。
6. 安全与防护界面同样。

整体要求:右侧明细做 tab 方便查找;所有文案让非技术人员能读懂。

用户已拍板四个方向:**一行式字段行 + ⓘ 气泡** / **重区块内子 tab(左侧大导航不动)** / **预设档位一键应用** / **结构化输出做简易字段清单**。

## ① 字段行 v2(全局统一,替换 FieldRow 现约定 + 删除 PolicyFieldTable)

一行布局:

```
标签(min160px)  [控件]  一句大白话(常显,flex)  ⓘ  [已自定义] [恢复默认]
```

- **brief**(`_brief` 键):≤18 字大白话,常显在行内;窄屏 flex-wrap 掉到下一行。
- **ⓘ**:antd Popover(点击触发,非 hover——移动端/长文友好),内容 = `_impact` 键(长解释 + 至少一个具体场景)。`_impact` 不存在则不渲染 ⓘ。
- **默认徽章废除**:值 === 默认 → 行内零噪音(不再显示「默认 30」灰 Tag);值 ≠ 默认 → 「已自定义」蓝 Tag + 「恢复默认」小按钮(patch `undefined`,即从 manifest 删键回落平台默认;按钮 Tooltip 显示默认值文案,来源 `_default` 键或 `String(effectiveDefault)`)。
- `FieldRow` props 改为:`{fieldId, label, brief, help?, isDefault, onReset?, children}`;`impact`/`defaultValue` props 删除。
- `PolicyFieldList` 保留(FieldDef → FieldRow v2 映射器);**`PolicyFieldTable` 及其测试删除**;`FieldControl` 保留复用。
- FormView 手搭的字段块(memory / defenses / governance 的 label+FieldHelp+控件三件套)迁移到同一 FieldRow v2 视觉(手搭块可直接用 FieldRow 包 children,不强求全改 FieldDef 声明式)。
- 条件警告 Alert(如关防护出黄条)保留,渲染在对应行下方,不变。

## ② 各组重排(子 tab = antd Tabs size="small",左侧大导航不动)

### 记忆(MemorySection)

| 子 tab | 内容(字段) |
|---|---|
| **基本** | 长期记忆开关 · 每轮召回条数(top_k, 默认5) · 学习开关(write_back) |
| **检索细节** | 回答前核对(verify_reads) · 重要性过滤(write_min_importance) · 去重整理(reconcile_writes) · 记忆插入位置(recall_mode) · 改写问题(rewrite_reads) · 跳过弱匹配(abstain_threshold) |
| **预算与整理** | 注入预算 2 项(injection/correction_token_budget,记忆关时隐藏) · 后台整理开关(consolidation.enabled,与记忆开关无关、恒显) + aux_model_note 一行 |

- 记忆关 → 「检索细节」tab disabled;「预算与整理」仍可进(后台整理独立于记忆开关),注入预算两行隐藏(保持现 gating 语义:不渲染,防误触激活 long_term)。
- 「高级」Collapse、①②编号 Collapse 全部拆除。
- **`memory_group.reserved_note`(short_term 保留字段技术注脚)删除**(UI + 双 locale 键)。

### 上下文与压缩(ContextGatesSection)

- 顶部保留一行 intro(现 `group_intro` 压缩成 1-2 句:三道处理按顺序兜底,大多数情况前两道就够)。
- intro 下一行:工具输出预算总开关(tool_output_budget.enabled,单开关不值一个 tab)。
- 子 tab:**[①结果修剪](3 字段) [②滑动窗口](4 字段) [③上下文压缩](10 字段)**,序号保留传达顺序语义。

### 安全与防护(SecuritySection)

| 子 tab | 内容 |
|---|---|
| **防护开关** | 现 defenses section 全部:输入防护(prompt_injection)· 输出防护(output_screen / output_judge+on_error / output_dlp)· 工具行为防护(action_screen+on_error);extends 提示 Alert 保留 |
| **人工审批** | 审批工具勾选(7 个 GATEABLE_TOOLS)+ 审批超时(approval_timeout,从「高级」Collapse 提出来) |
| **子任务与网络** | 临时小助手开关(dynamic_workers)· 运行轨迹录制(trajectory_recording,从「高级」提出)· 网络出网 3 项(egress/allowlist/denylist)· 工具强制(tool_use_enforcement) |

- 「高级」「①网络出网」「②工具强制」Collapse 全拆。
- `security_gates.dict_note` 缩成一行:「限流、隐私脱敏、安全策略等高级项请在 YAML 视图配置」。

### 运行预算与超时(RunBudgetSection)

- 不加子 tab(一行式后 7 行一屏放下),两个小标题分组:
  - **步数与流程**:max_iterations · workflow.type · max_no_progress
  - **时间与花费**:run_deadline_s · token_budget · stream_deadline_s · idle_timeout_s
- **`run_budget.workflow_note`(early_stop/builder YAML-only 注脚)删除**(技术细节,非技术人员无感;YAML 用户看文档)。

### 沙箱与资源 / 触发器与可观测 / 模型与路由

- 行样式与文案统一到 v2,不动结构(字段少)。

## ③ 预设档位「运行策略」

- **位置**:「基础」组顶部卡片,三档 Radio:`均衡推荐`(默认)/ `成本优先` / `能力优先`;右侧状态 chip:当前匹配某档显示该档,否则「自定义」。
- **机制**:纯前端。新 `form_model` helper `applyRunProfile(m, profile)` 一次性批量写以下受管字段;`inferRunProfile(m)` 打开编辑器时反推(全部字段精确匹配某档 → 该档,否则 `custom`)。**不新增任何后端/manifest 字段**。
- **均衡档值 === effectiveDefault** ⇒ 应用均衡 = 全部受管键 patch `undefined`(manifest 回到最干净状态)。这是承重不变式。
- 换档若会改动 ≥1 个字段值 → confirm Modal「将调整 N 项配置」列明细;否则直接应用。
- **安全与防护字段一律不受管**(防护开关不允许被「成本优先」静默降级;其默认值本身已是推荐姿态)。run_deadline_s / stream_deadline_s / idle_timeout_s / token_budget 也不受管(安全阀/平台默认,不是调优旋钮)。

受管字段与三档草案值(实施计划中逐项复核):

| 字段 | 均衡(=默认) | 成本优先 | 能力优先 |
|---|---|---|---|
| memory.retrieve_top_k | 5 | 3 | 8 |
| injection_token_budget | 2000 | 1000 | 4000 |
| correction_token_budget | 500 | 300 | 800 |
| consolidation.enabled | true | false | true |
| workflow.max_iterations | 30 | 20 | 60 |
| policies.max_no_progress | 0 | 3 | 0 |
| tool_result_prune.threshold_pct | 0.7 | 0.6 | 0.8 |
| tool_result_prune.recent_kept | 4 | 2 | 8 |
| working_memory.threshold_pct | 0.7 | 0.6 | 0.8 |
| working_memory.max_recent_turns | 20 | 10 | 40 |
| context_compression.threshold_pct | 0.7 | 0.6 | 0.85 |
| context_compression.head_keep | 4 | 2 | 6 |
| context_compression.tail_keep | 6 | 4 | 10 |
| dynamic_workers on | true | false | true |

- 卡片文案:每档一句大白话(均衡「日常够用,费用适中」/ 成本「省 token,长对话砍得更狠」/ 能力「多记多想,复杂任务更稳,费用更高」)+ 一行「选档后下面模块自动配好;单项仍可改,改过会显示已自定义」。

## ④ 结构化输出编辑器(提示词与输出组,替换现 note-only)

- 「按模板回复」开关 + 字段清单表:**字段名**(校验 `^[A-Za-z_][A-Za-z0-9_]*$`)/ **类型**(文本 · 数字 · 整数 · 是否 · 文本列表 · 数字列表)/ **必填** / **说明**(→ `description`)+ [+ 添加字段] / 删行。
- 生成:`spec.output_schema = { json_schema: { type:"object", properties:{…}, required:[…], additionalProperties:false } }`;`name` 不出界面(缺省走后端默认 `final_response`,已有值原样保留);`strict` 不出界面(同样保留/缺省)。
- **可表示性护栏(命门)**:载入时判定现有 `json_schema` 是否平铺可表示(顶层 object;properties 值仅 `{type: 基本类型|array<基本类型>, description?}`;除 `type/properties/required/additionalProperties` 外无其他关键字;无嵌套 object / `$ref` / 组合子)。不可表示 → 只读卡片「已配置(复杂结构),请到 YAML 视图编辑」,**编辑器绝不改写该块**。
- 开关关闭且已有字段 → Popconfirm「关闭会清除模板定义」,确认后删除 `spec.output_schema` 整块。
- 承重不变式:可表示 schema → 清单 → 再生成,语义等价(round-trip 测试);不可表示 schema 经 Form 任意其他操作后 byte 级不变(沿用 patchSpec 未投影键保留机制)。

## ⑤ 文案规则(硬性,zh + en 双 locale 同步)

1. `_brief` / 手搭行 hint:≤18 字纯大白话,零术语。
2. `_impact` / ⓘ 内容:长解释 + 至少一个具体场景(#1044 已写的长文案大多可迁移复用,按「一句常显 / 其余进 ⓘ」重切)。
3. 技术注脚三删一缩:删 `memory_group.reserved_note`、删 `run_budget.workflow_note`、缩 `security_gates.dict_note` 为一行;`memory_group.aux_model_note` 保留但缩短。
4. 禁术语清单沿用 #1044(委托树/LLMStreamStaleError/routing 规则等),新增文案同样过审。
5. i18n 键集 zh↔en 一致性守卫测试沿用。

## 不做(明确后置)

- 后端改动(一切都是 manifest 投影)。
- 嵌套/组合子 schema 的可视化(YAML 逃生口)。
- 预设档位持久化(靠反推,不落库)。
- SettingsSearch 升级到字段级跳转(仍组级)。
- 其它 P1 覆盖缺口(tenant_config / observability trace / model base_url 等,单独排)。

## 测试要点

- FieldRow v2:brief 常显、ⓘ Popover 出 impact、非默认值出「已自定义 + 恢复默认」、恢复默认 patch undefined(变异证)。
- 各组子 tab:每个 tab 激活后字段可查可改;记忆关 → 检索细节 disabled、注入预算隐藏。
- 预设:apply 成本优先 → 14 字段全写入;apply 均衡 → 受管键全部删除(manifest 最干净);infer 精确反推;confirm 门槛(变异证)。
- schema 编辑器:round-trip 等价;不可表示护栏(嵌套 object → 只读、不改写);字段名校验;关闭清块。
- `npx tsc -b --noEmit` exit 0 + vitest 全绿;i18n 键集守卫。
