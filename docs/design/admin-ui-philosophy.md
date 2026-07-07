# Admin UI 设计哲学

> 本文档是 expert-work Admin UI(Stream H)**任何一行 React 代码落地之前**必须先确立的设计基线之一,另一篇是 [admin-ui-language.md](./admin-ui-language.md)。两者关系:**哲学定义"为谁、为什么、什么原则"**;**语言定义"用什么 token / 字号 / 组件去表达"**。Mockup 在 `mockups/` 目录给出 7-8 张关键页面的可视化形态。

---

## 1. 目的与读者

本文档面向 **Stream H 实施者、未来维护者、新加入的贡献者**,**不是面向末端用户的产品介绍**。

它回答三个问题:

1. **Admin UI 服务谁、什么场景?**
2. **它的设计要遵循哪些不可妥协的原则?**
3. **当遇到具体设计抉择时,该怎么判断哪种做法更对?**

实施者在写任何组件、做任何交互细节决定之前,先把这 6 条原则装进脑子。语言文档(tokens / 组件库 / 布局规则)是这些原则的物理落地。

---

## 2. 核心人群与场景

> **关键澄清(2026-05-25 用户确认)**:business 系统通过 API 消费 Expert Work 的 per-user 持久 agent 能力。**末端用户(agent 的真正消费方)永远不会看到 Expert Work 自身的 UI** —— 他们通过 business 系统自己的 UI 与 agent 对话。Expert Work 的 Admin UI 仅服务 **操作人群**。

### 人群

| 人群 | 主要诉求 | 在 Admin UI 中做的事 |
|---|---|---|
| **平台管理员** | 多租户治理、合规、配额、审计、密钥 | tenant 配置 / quota / API Key / role binding / audit / service account / 资源用量大盘 |
| **Agent 开发者** | 构建 agent、调试 prompt / tool、看 trace、改 manifest 重跑 | agent CRUD / Manifest 编辑 / **Playground**(per-agent tab 内 debug 会话)/ trace 详情 / skill / trigger / memory admin / curation 评审 |
| **运营 / SRE** | 看健康、查异常、处理审批、紧急介入 | run 列表 / 失败 run 重试 / J.8 审批面板 / 实时日志 |

三类人群共用一个 SPA、共用同一套 IA 与组件;权限通过 role binding 控制可见性,不通过另开界面区分。

### 不是 Admin UI 的事

- **末端用户对话** — 由 business 系统的 UI 自行处理,通过 Expert Work API 拉 SSE 流即可
- **登录注册 / 选购套餐 / billing** — 不在 M0 范围(后续若有营销站,独立产品)
- **跨租户的 SaaS 营销页 / 文档站** — 这是另一类资产

---

## 3. 设计 6 条原则

每条原则用一句话能说清,后跟一句"为什么"。设计抉择不确定时,回到这 6 条 —— 哪种做法更**直接、更清晰**就选哪种。

### 3.1 清晰胜过新颖

> **不为创新而创新**,优先复用熟悉的模式。

ops 工具的用户不是来"探索 UI"的,他们来完成工作。熟悉感降低认知负担,新颖的交互模式只在真的能省时间的场合用(如 Cmd+K 命令面板)。Linear / Anthropic Console / Stripe Dashboard 在密度、布局、组件库选择上有大量已验证可用的范式 —— 直接借鉴,不要自己想新花样。

**判断标准**:如果一个交互需要 tooltip 解释,先想想能不能用更熟悉的形态消除 tooltip。

### 3.2 密度服务效率

> **数据稠密但不拥挤**,whitespace 服务可读性、不服务"高级感"。

我们做的是 ops 工具,不是营销页。一屏装得下越多有用信息,操作越高效。但密度不是堆砌 —— **行高、padding、行间留白都要够手指/眼睛能区分**。中等密度(表格行高 36-40px,按钮 32px,主内容 padding 24)是平衡点,不要走极端密(像老式企业软件)也不要走极端松(像 SaaS 落地页)。

**判断标准**:同样一个数据表,数得清行数;同样一个详情页,首屏内能看到核心 stats 而不需要滚动。

### 3.3 键盘优先,鼠标补充

> Cmd+K 全局命令面板 + 全程 keyboard nav + **可见的 focus ring**。

ops / dev 人群习惯键盘。所有页面跳转 / 操作触发 / 资源搜索 都能用 Cmd+K 命令面板;表单 / 表格 / tab 切换 都能用 Tab + Enter + 方向键完成;**focus ring 必须可见**(brand 色 1px ring,无 outline:none)。鼠标是补充,不是必须。

**判断标准**:闭着眼睛只用键盘,能不能从 agents 列表跳到任一 agent 的 Playground tab 提交一条 prompt 并看到回复 —— 答案必须是"能"。

### 3.4 状态可见

> 每个长操作有 progress/trace;每个错误有上下文 + 重试 CTA。

LLM ops 充满了"看不见的等待"(模型推理、tool 调用、SSE 流、cron 触发、curation 后台扫描)。**任何超过 200ms 的操作,UI 必须告诉用户它在做什么 + 进度到哪 + 失败了能怎么办**。没有静默成功 / 静默失败 —— toast / banner / inline 反馈三选一,每次都给。

**判断标准**:LiveLog / SSE 流 / trace timeline 必须实时可见;一个 run 失败了,看错误一句话就能定位 step,点重试一键复跑。

### 3.5 多租户安全感

> tenant scope **永远可见**;destructive 操作二次确认 + 自动 audit。

多租户平台最致命的是"在错的 tenant 下做了正确的操作"。**当前 tenant / 当前 user** 永远显示在顶 bar(可点击切换);页面标题 + URL + breadcrumb 都带 scope 信息;destructive 操作(删除 / 旋转 key / 修改 quota / 强制结束 run)弹 confirm dialog 并标明影响范围 + 落 audit log。

**判断标准**:任何看到的数据都能立刻回答"是哪个 tenant 的、哪个 user 的"。删除任何东西都不能"嗖一下没了"。

### 3.6 黑白可读

> dark / light 双主题都过 WCAG AA;**不靠颜色单独传递语义**(辅以图标 + 文本)。

LLM ops 的日志、trace、code、JSON 都是长文本 —— dark 主题对眼睛友好,是默认。但 light 主题同样产品级(打印 / 截图分享 / 演示场景需要)。所有信息传递 **必须色 + 形 + 字 三者至少两者**(色觉障碍 / 黑白打印场景):一个 status badge 不能只用绿色,要绿 + ✓ + "success" 三者齐全。

**判断标准**:把界面截屏转黑白,所有信息仍可辨认。

---

## 4. IA 心智模型 —— Agent 是中心实体

Expert Work 的世界观里,**Agent 是一切的中心实体**:

- 一个 agent 有自己的 **manifest**(YAML 定义)
- 一个 agent 派发 **runs**(每次执行)
- 一个 agent 拥有 **skills**(可调用的工具/子能力)
- 一个 agent 接 **triggers**(cron / webhook)
- 一个 agent 在每个用户上累积 **memory**
- 一个 agent 的 **runs 串成 curation 候选** → 喂回 eval dataset → 反哺改进

所以 Admin UI 的 IA 应该是 **Agent-中心扁平**:

```
左导航(一级 IA,6-7 个):
  Agents        ← 主要工作面(80% 时间在此)
  Runs          ← 跨 agent 观察 / 失败追踪 / 审批队列
  Curation+Eval ← 学习闭环
  Memory        ← 跨 agent / 跨 user 记忆治理
  Skills        ← 跨 agent skill 库
  Triggers      ← 跨 agent 调度
  Settings      ← Tenant / API Key / Service Account / Role / Audit / Quota
```

**Agent 详情页内嵌 7 个 tab**(per-agent 视角,主要工作面):

```
Agent 详情:
  Overview      ← name/version/status/stats(本月 runs / 失败率 / p95 延迟)
  Manifest      ← Monaco YAML 编辑器(实时 Pydantic 校验回显)
  Playground    ← per-agent debug 会话(SSE 实时,改 manifest 重跑)
  Runs          ← 此 agent 的所有 runs + trace
  Skills        ← 此 agent 接入的 skill 配置
  Triggers      ← 此 agent 的 cron / webhook 触发器
  Memory        ← 此 agent 在各 user 上的 memory
```

**取舍逻辑**:跨 agent 的资源(skill / trigger / memory)既有 per-agent tab(开发者用),又有跨 agent 一级页(管理员用) —— 两套视图,**同一份数据,不同 scope**。这是有意的冗余,服务不同人群的不同 mental model。

---

## 5. Operator + Debug 双能力同面

> **关键设计**:Playground(debug 会话能力)在 per-agent tab 内,**不是独立产品面**。

理由:

1. **Debug 上下文 = agent 定义** —— 改 prompt / 改 tool / 改 model → 立即想看效果。Playground 与 Manifest tab 在同一详情页,切换零成本
2. **目标人群相同** —— Playground 用户 = agent 开发者 = 已经在看 agent 详情的人
3. **单面 SPA 简化** —— 一套 IA、一套设计系统、一套权限模型;不需要切换 "admin 模式 / debug 模式"
4. **Anthropic Console 同款范式** —— Console + Workbench/Playground 同面,各家 AI 平台都收敛到这个模式

Playground 的布局(详见 [language.md](./admin-ui-language.md) § 5 与 mockup 03):
- **左**:input 区(prompt / image / 上下文)+ manifest snippet(可改可重跑)
- **右**:消息流 + tool calls + trace timeline(SSE 实时,token streaming)

---

## 6. 无障碍承诺

| 维度 | 承诺 |
|---|---|
| **键盘可达** | 100% —— 全部交互可纯键盘完成;Tab 顺序合理;焦点可见 |
| **对比度** | dark / light 都 ≥ WCAG AA(正文 4.5:1 / 大字 3:1 / UI 控件 3:1) |
| **色觉无障碍** | 不靠颜色单独传递语义,色 + 形 + 字 至少两者 |
| **动效** | respects `prefers-reduced-motion`,无大幅 entrance / parallax |
| **屏幕阅读器** | aria 标签标准;表单 label 必关联;tab role 与 aria-selected 同步 |
| **i18n** | 默认 zh-CN,en 完整覆盖;技术术语(Agent/Run/Trace 等)保留 en |
| **字号** | 用户浏览器字号设置生效(rem-based,不是 px-only) |

无障碍是**实施期硬约束**,不是 nice-to-have。每张 mockup 在 PR 中要附 axe DevTools 通过截图;每个 H.* 子项验收时 a11y 复检。

---

## 7. 这份文档不回答什么

| 问题 | 去哪里 |
|---|---|
| 配色具体色值 / 字号像素值 / 间距数字 | [admin-ui-language.md](./admin-ui-language.md) § 1 |
| 用了哪些 Antd 组件 / 怎么 override | [admin-ui-language.md](./admin-ui-language.md) § 3 |
| 关键页面长什么样 | [mockups/](./mockups/) |
| Stream H 子项怎么排期 / PR 怎么切 | [../streams/STREAM-H-DESIGN.md](../streams/STREAM-H-DESIGN.md) |
| 后端 API 接口怎么用 | `services/control-plane/src/control_plane/api/*.py` + OpenAPI |

---

## 修订记录

| 日期 | 版本 | 说明 |
|---|---|---|
| 2026-05-25 | v1.0 | 初稿:6 条原则 + Agent-中心 IA + Operator+Debug 双能力同面 + WCAG AA 承诺(锁 [admin-ui-design-baseline] 10 条决策的产物之一) |
