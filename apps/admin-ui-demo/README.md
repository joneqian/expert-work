# helix Admin UI — 交互 Demo

> **临时验证 demo**(Stream H.1a 设计基线落地后、H.1b 正式工程之前的 sign-off prototype)。
>
> 目的:让用户在浏览器里真实点过 4 个核心页面,审核 **Antd 5 + helix design tokens 集成的视觉效果**、交互流程、键盘可达性,**全部 OK 再开 H.1b**(正式 `apps/admin-ui/`)。

---

## 范围

**做了**:Vite + React 19 + TypeScript + Antd 5 + helix tokens(`src/theme/tokens.css` 移植自 `docs/design/mockups/shared/`)+ 4 页面 + Cmd+K 命令面板 + dark/light 切换 + Lucide 图标 + react-router-dom 路由 + mock 数据(无后端)。

**没做(留 H.1b)**:鉴权 / 真后端 API SDK / i18n / Monaco YAML 编辑器 / 真 SSE 客户端 / Storybook / E2E 测试 / 单元测试 / 部署 CI / 营销页 / mobile 响应式 / a11y 完整审计。

---

## 4 个核心页面

| 路径 | 演示重点 |
|---|---|
| `/agents` | Antd Table + 筛选 + 创建对话框(form validation) + 跳详情 + **空状态切换 switch** |
| `/agents/:id/overview` | hero + 7 tabs 路由切换 + 4-KPI stat grid + 配置摘要 + recent runs feed |
| `/agents/:id/playground` | **核心 debug 能力** —— 左 prompt + manifest;右 SSE 流式 token(setInterval 模拟) + tool call card + trace timeline |
| `/runs/run_4c9b8e21f60d` | spans 树 + trace timeline + **J.8 approval pending 行动卡**(点"批准"→ 状态变 `ok` + 成功 banner;"拒绝" → `cancelled` + 错误 banner) |
| `/settings/api-keys` | 列表(rotation grace banner) + 创建对话框(scopes 多选 checkbox) + **show-once key 弹窗**(随机生成 + 复制到剪贴板) |

其他 IA 路径(`/runs` 列表、`/curation`、`/memory`、`/skills`、`/triggers`、`/settings/*`)走 `ComingSoon` 占位 —— **H.1b 实施时落地**。

---

## 如何跑

需要 **Node.js ≥ 18**(推荐 20+)与 **pnpm ≥ 10**(`package.json` 已声明 `packageManager: pnpm@10.14.0`)。
没装 pnpm:`npm install -g pnpm@10` 或 `corepack enable && corepack prepare pnpm@10.14.0 --activate`。

```bash
cd apps/admin-ui-demo
pnpm install       # 第一次:~30s 装 ~250 个 npm 包
pnpm dev           # 起 Vite dev server,自动开 http://localhost:5173
```

浏览器自动开 `http://localhost:5173/agents`。**右上角 Sun/Moon 图标切 dark/light**。**任意页面 Cmd+K(或 Ctrl+K on Windows/Linux)调出命令面板**。

```bash
# 生产构建预览(可选):
pnpm build         # tsc + vite build → dist/
pnpm preview       # 服务 dist/ 在 4173
```

---

## 关键交互(审核清单)

- [ ] **Cmd+K 命令面板**:全局 Cmd+K 调起 → 输入 "cust" → 模糊匹配到 `customer-support-bot` → Enter 跳详情。↑↓ 选,Esc 关。
- [ ] **dark / light 切换**:Topbar 右侧 Sun/Moon 切;切完保留到 localStorage;refresh 后保留;首次访问跟 `prefers-color-scheme`。
- [ ] **Agents 列表 → 详情 → Playground**:点表格行 → 跳详情 Overview;点 hero "Playground" 按钮 / 点 Playground tab → 跳 Playground;点 sidebar Agents → 回列表。
- [ ] **Playground 流式 token**:Playground 输入 prompt → 点"运行" → 看到 user 消息(右,紫色)+ 两个 tool call card(rag.knowledge / logistics.lookup)+ assistant 消息逐字浮现(brand-color cursor 闪烁)+ trace bars 出现。点"停止"(Esc kbd 标签)中断流。
- [ ] **Run 详情 + Approval**:`/runs/run_4c9b8e21f60d` 直接访问 → 看 spans 树(pending 状态 dot 脉冲)+ trace + approval 黄色行动卡 → 点 **批准** → banner 变绿色 "审批通过";点 **拒绝** → banner 变红色 "审批拒绝"。
- [ ] **创建 Agent 表单校验**:`/agents` → 点"创建 Agent" → 留空 name 点 OK → Form 校验红色错误;填完点 OK → toast "Agent 已创建"。
- [ ] **API Keys + show-once**:`/settings/api-keys` → 点"创建 API Key" → 填 name + scopes → 提交 → show-once modal 显示完整 key + 复制按钮(点复制 → toast);关掉后 key 不再可见 — 列表里仅 prefix。
- [ ] **键盘 Tab 遍历**:从 Topbar 起,Tab 应能依次访问 tenant switcher → Cmd+K → 主题切 → 通知 → 用户菜单 → sidebar 菜单项 → 主内容区交互元素。每个 focus 都有可见的 ring(brand 色)。
- [ ] **空态切换**:Agents 列表 toolbar 右侧 "默认 / 空" Switch 切到"空" → empty state(图标 + 标题 + 主操作)。
- [ ] **响应式**:浏览器 <1280px 宽时 layout 仍能渲染(M0 仅承诺 desktop;可能略局促)。

---

## 工程目录

```
apps/admin-ui-demo/
├── package.json              (依赖 pin 在 ^5.x Antd / ^19 React / ^6 react-router / lucide-react)
├── vite.config.ts
├── tsconfig{,.app,.node}.json
├── index.html
├── public/
│   └── favicon.svg           (DNA glyph)
└── src/
    ├── main.tsx              (root + ThemeProvider + BrowserRouter)
    ├── App.tsx               (ConfigProvider + CommandPaletteProvider + Shell + Router)
    ├── router.tsx            (Routes)
    ├── theme/
    │   ├── tokens.css        (从 docs/design/mockups/shared/tokens.css 移植 — source of truth)
    │   ├── global.css        (body reset + 共享细节)
    │   ├── antdTheme.ts      (dark/light 两套 ThemeConfig → ConfigProvider)
    │   └── ThemeContext.tsx  (useTheme hook + localStorage 持久化)
    ├── components/
    │   ├── Shell.tsx         (Layout grid)
    │   ├── Sidebar.tsx       (Antd Menu + Lucide icons)
    │   ├── Topbar.tsx        (tenant switcher + cmdk opener + theme toggle)
    │   └── CommandPalette.tsx (Cmd+K Modal + fuzzy filter + groups + 键盘导航)
    ├── icons/
    │   └── BrandGlyph.tsx
    ├── mock/
    │   ├── agents.ts         (5 个 agent + findAgent)
    │   ├── runs.ts           (run_4c9b8e21f60d w/ spans + approval)
    │   └── apiKeys.ts        (4 keys + generateMockKey)
    └── pages/
        ├── AgentsList.tsx
        ├── AgentDetail.tsx   (Overview + Playground inline)
        ├── RunDetail.tsx
        ├── SettingsApiKeys.tsx
        └── ComingSoon.tsx
```

---

## Antd 5 + helix tokens 集成 — 关键映射点(`src/theme/antdTheme.ts`)

- `colorPrimary` → `--hx-color-brand-500`(cyan)
- `colorBgContainer / colorBgLayout` → 双主题对应 surface-base / surface-bg
- `borderRadius` → 6 / 4 / 8(sm/xs/lg 三档)
- `controlHeight` → 32(中等密度)
- `Layout.headerBg / siderBg` → surface-base(顶 bar 与 sidebar 透明融入)
- `Menu.itemSelectedBg` → 12% alpha brand-500
- `Table.headerBg` → surface-raised + uppercase xs(global.css 内补 padding / 字号细节)
- `Tabs.inkBarColor` → brand-500
- Algorithm:`darkAlgorithm` / `defaultAlgorithm` 通过 ConfigProvider 切换

---

## 已知局限(demo 范围内,**不算 bug**)

- **没有真后端**:所有数据是 `src/mock/*.ts` 写死的;点"创建 Agent"只是前端塞条记录,refresh 丢失。
- **Playground SSE 是 setInterval 模拟**,不是真 EventSource;tool call card 是固定的两条。
- **Run 只有一条** `run_4c9b8e21f60d`;`/runs` 列表是 ComingSoon。
- **i18n 未启用**:UI 文案全中文 + 技术术语 en 内嵌(语言层 i18next 在 H.1b 接)。
- **响应式仅承诺 ≥1280px**(memory 文档已显式标注 M0 desktop-only)。
- **a11y 未做完整 axe 自动审计**(仅手测 Tab + focus ring);H.1b 实施期补 CI 集成。
- **Test 工程不存在**(demo 无 vitest / playwright);H.1b 实施期补。

---

## 审核完之后…

1. 用户确认 **视觉 + 交互 + dark/light + Cmd+K + Antd 集成** 4 个核心页面 OK
2. 删 `apps/admin-ui-demo/`(本 demo 一次性)
3. 开 H.1b PR:新建 `apps/admin-ui/`(正式工程,含 auth / i18n / API SDK / Storybook / 测试)
4. demo 的 token + Shell + CommandPalette / Sidebar / Topbar / 4 个页面的实现可作为 H.1b 起点(不是 copy-paste,但**视觉结构定锚**)

---

## 与设计基线(已合并的 PR1+PR2)的关系

| 设计基线产物 | 本 demo 落地位置 |
|---|---|
| `docs/design/admin-ui-philosophy.md`(原则) | 体现在交互(键盘可达 / 状态可见 / destructive confirm)与文案 |
| `docs/design/admin-ui-language.md` § 1 tokens | `src/theme/tokens.css`(直接 copy)+ `antdTheme.ts`(Antd token 硬编码 1:1) |
| `docs/design/admin-ui-language.md` § 3 组件 inventory | `src/components/*` + Antd 5 组件 |
| `docs/design/mockups/01..08-*.html` | `src/pages/*.tsx` 的视觉参考 |
| `docs/design/mockups/shared/brand-glyph.svg` | `public/favicon.svg` + `src/icons/BrandGlyph.tsx` |
