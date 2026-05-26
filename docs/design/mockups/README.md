# helix Admin UI — Mockups

14 张 helix Admin UI 关键页面的**可视化 mockup**(H.1a 初版 8 张 + H.4 PR 0 补 6 张),作为 [admin-ui-philosophy.md](../admin-ui-philosophy.md) + [admin-ui-language.md](../admin-ui-language.md) + [STREAM-H-DESIGN.md](../../streams/STREAM-H-DESIGN.md) 的视觉锚点。

H.1b 实施时,**临摹这些 mockup 作为视觉基线**;`shared/tokens.css` 可直接移植进 Vite 工程,所有 token 已固化。

---

## 如何看

```bash
# 任意浏览器打开,无 build 步骤
open docs/design/mockups/01-agents-list.html

# 或:启个本地静态服务器(支持相对路径)
cd docs/design/mockups && python3 -m http.server 8765
# 浏览器访 http://localhost:8765/01-agents-list.html
```

每张 mockup 右下角有 **dark / light 主题切换按钮**(部分页另有状态切换:空态 / 弹窗 / 流式中)。

---

## 索引

| # | 页面 | 演示重点 | 对应 H 子项 |
|---|---|---|---|
| **01** | [Agents 列表](./01-agents-list.html) | 表格 + 筛选 + 创建按钮 + **空状态切换**;状态 badge(active/draft/archived);失败率上色阈值 | H.2 |
| **02** | [Agent 详情 — Overview](./02-agent-detail-overview.html) | hero + tabs(7 个)+ stat grid(4 个 KPI)+ 配置摘要 dl/dt/dd + 最近 5 个 runs feed | H.2 |
| **03** | [Agent 详情 — **Playground**](./03-agent-detail-playground.html) | **核心 debug 能力**;三栏:input/manifest 左、消息流中、trace+step 右;tool call card;**streaming cursor** 动画 | H.2 |
| **04** | [Run 详情 + Trace](./04-run-trace.html) | spans 树(左) + trace timeline(右上) + Input/Output JSON 详情(右下) + **J.8 approval pending 行动卡** | H.3 |
| **05** | [Curation 候选评审](./05-curation-review.html) | 双栏:候选列表 + trajectory 回放 + **promote 到 eval dataset 表单**(目标 dataset 选 + 类型 + expected) | H.4(Curation) |
| **06** | [Memory admin](./06-memory-admin.html) | per-user memory 树 + JSON 编辑器 + history tabs + **type-to-confirm 删除对话框**(`profile.preferred_language` 输入确认) | H.4(Memory) |
| **07** | [Settings — API Keys](./07-settings-api-keys.html) | Settings 二级导航 + 列表(prefix / scopes / rotation grace) + 创建对话框(scopes 多选) + **show-once key 卡片**(只显示一次 + 复制) | H.4(Settings) |
| **08** | [Cmd+K 命令面板](./08-cmdk-palette.html) | 全局浮层 + 分组(Agents / 动作 / 跳转 / 最近) + 模糊匹配高亮 + 键盘 hint 底栏 | H.1b 全局 |
| **09** | [Runs 列表](./09-runs-list.html) | 跨 thread 索引;表格 + status 筛选 + cross-tenant banner;补 H.3 留账 | H.3 |
| **10** | [Skills](./10-skills.html) | Skill 库 + 版本管理 + Import ZIP + Create drawer Monaco YAML;cursor "Load more" 分页 | H.4 PR 5 |
| **11** | [Triggers](./11-triggers.html) | cron/webhook Tabs + Create drawer 双 kind 切换 + **Webhook secret show-once Card**;enabled toggle | H.4 PR 6 |
| **12** | [Audit](./12-audit.html) | Timeline + filter chips + Entry detail Drawer 含 `redacted_keys` 高亮 + cursor "Load more" | H.4 PR 3+4 |
| **13** | [Settings IAM](./13-settings-iam.html) | Service Accounts + Role Bindings 双子页;platform_scope checkbox + **self-elevation type-to-confirm dialog** | H.4 PR 7 |
| **14** | [Settings Ops](./14-settings-ops.html) | Tenant Quotas 进度条(色彩阈值)+ Tenant Config Monaco JSON + **ETag 412 conflict banner** + pristine/dirty 检测 | H.4 PR 8 |

---

## 文件结构

```
docs/design/mockups/
├── README.md                 (本文件)
├── 01-agents-list.html
├── 02-agent-detail-overview.html
├── 03-agent-detail-playground.html
├── 04-run-trace.html
├── 05-curation-review.html
├── 06-memory-admin.html
├── 07-settings-api-keys.html
├── 08-cmdk-palette.html
├── 09-runs-list.html        (H.4 PR 0 补 H.3 留账)
├── 10-skills.html           (H.4 PR 0)
├── 11-triggers.html         (H.4 PR 0)
├── 12-audit.html            (H.4 PR 0)
├── 13-settings-iam.html     (H.4 PR 0)
├── 14-settings-ops.html     (H.4 PR 0)
└── shared/
    ├── tokens.css            (所有 design token CSS custom properties,双主题语义层)
    ├── shell.css             (布局外壳 / 排版 / 17 组基础组件 — mockup 共用)
    └── brand-glyph.svg       (DNA 双螺旋 favicon glyph,2px 线宽,可缩放)
```

---

## 每张 mockup 顶部注释包含

```
Tokens used: …      列出该页主要用到的 CSS variable
Antd 5 components:  实施时映射的 Antd 组件 + override 要点
键盘:               该页主要快捷键
状态变体:           [data-state] 切换演示(空态 / 弹窗 / streaming 等)
```

---

## 视觉一致性自检

8 张 mockup 都应该:
- [ ] 双主题(dark / light)切换不破坏布局
- [ ] sidebar 都是 220px,topbar 都是 48px
- [ ] brand glyph 颜色 = `--hx-color-brand-500`(cyan)
- [ ] 主操作 button(创建 / 批准 / promote / 删除)都用 `.hx-btn--primary` 或 `.hx-btn--danger`
- [ ] 表格行高 36-40px,padding `--hx-space-2/3`
- [ ] 所有 mono 字段(id / version / model name)用 `font-family: var(--hx-font-mono)`
- [ ] 状态 badge 用 `.hx-badge--{success,warning,danger,info,neutral}`
- [ ] 顶部 Cmd+K 占位永远显示

如果发现 mockup 之间不一致,**先改 `shared/*.css`**(token / shell)— 因为不一致大概率是 token 用错,而不是 mockup 自己有问题。

---

## 与 language.md 的同步

`shared/tokens.css` 必须与 [admin-ui-language.md § 1](../admin-ui-language.md#1-设计-tokens) 完全对齐:
- 任一处改了 token 名 / 值,另一处必须同 PR 改
- `tokens.css` 是 source of truth(代码 > 文档)
- 出现冲突 → 以 `tokens.css` 为准

---

## H.1b 实施时的迁移

1. **移植 `shared/tokens.css`** → `apps/admin-ui/src/theme/tokens.css`(直接 copy,无改动)
2. **`shared/shell.css` 部分迁移**:Antd 能覆盖的部分用 ConfigProvider 实现;不能覆盖的(如 Shell grid 布局 / mockup-only theme toggle)迁移进 `apps/admin-ui/src/theme/shell.css`
3. **mockup HTML 不直接复用**:作为视觉参考,React 组件按 [language.md § 3](../admin-ui-language.md#3-核心组件-inventory-antd-5-映射--override-要点) 用 Antd 5 + helix override 重新实现

---

## a11y 自检

每张 mockup 在 PR review 时跑:
- **axe DevTools**(浏览器扩展)→ 0 critical issue
- **Lighthouse → Accessibility**(`>= 95` score)
- 双主题都过(dark / light 切换后再跑一遍)
- 键盘 Tab 遍历全部交互元素,focus ring 可见

---

## 修订记录

| 日期 | 版本 | 说明 |
|---|---|---|
| 2026-05-25 | v1.0 | 初版:8 张 mockup + shared/{tokens, shell}.css + brand-glyph.svg;对齐 [admin-ui-design-baseline] 10 条决策 |
| 2026-05-26 | v1.1 | H.4 PR 0 设计基线:补 6 张 mockup(09 Runs 补 H.3 留账 + 10 Skills + 11 Triggers + 12 Audit + 13 Settings IAM + 14 Settings Ops)。覆盖 H.4 PR 5/6/3+4/7/8 共 6 子面。每张延续 H.1a 视觉规则(220px sidebar / 48px topbar / cyan brand 500 / mono for ID/secret/JSON / 36-40px table row / `.hx-badge--*` 状态色)。新增 mockup-only 样式有:Webhook secret show-once Card(triggers)/ Quotas 进度条阈值上色(settings-ops)/ ETag conflict banner(settings-ops)/ Audit timeline dot 颜色编码 |
