# Admin UI 设计语言

> 配套阅读:[admin-ui-philosophy.md](./admin-ui-philosophy.md)(为什么/为谁/什么原则)、[mockups/](./mockups/)(关键页面可视化)。本文档定义**用什么 token、字号、间距、组件去表达哲学**;所有 token 已落地为 [mockups/shared/tokens.css](./mockups/shared/tokens.css),mockup 共用样式落地为 [mockups/shared/shell.css](./mockups/shared/shell.css)。
>
> H.1b 实施(React + Vite + Antd 5)时,把 `tokens.css` 直接 `import` 进工程,Antd 通过 ConfigProvider 把 `--ew-*` CSS variable 映射进 design token,本文档列的 override 是关键映射点。

---

## 0. 文档使用说明

| 段 | 内容 | 落地形态 |
|---|---|---|
| § 1 | 设计 tokens 全量值表 | [tokens.css](./mockups/shared/tokens.css) |
| § 2 | 品牌 wordmark / DNA glyph 几何 | `mockups/shared/brand-glyph.svg`(H.1a 阶段产出) |
| § 3 | 组件库 inventory + Antd 5 映射 + override 要点 | H.1b 实施参考 |
| § 4 | 布局栅格 | `tokens.css` + `shell.css` |
| § 5 | 页面骨架 | `mockups/0X-*.html`(每张 mockup 顶部注释列出用到的骨架) |
| § 6 | 图标系统 | `lucide-react` 包 |
| § 7 | 键盘 + Cmd+K | `kbar` 或自研 |
| § 8 | i18n | `i18next` + `react-i18next` |
| § 9 | 无障碍 | 实施期硬性 lint |
| § 10 | dark/light 实现 | `html[data-theme]` 切换 |
| § 11 | 术语表 zh-CN ⇄ en | i18n 锚定来源 |

---

## 1. 设计 Tokens

> 全部值已固化在 [tokens.css](./mockups/shared/tokens.css);本节是**人读版**,有冲突时以 CSS 为准(代码是 source of truth)。

### 1.1 颜色

#### 原色阶(theme-invariant — 不随 dark/light 切换变化)

| 色族 | 阶数 | 用途 | 例 |
|---|---|---|---|
| `neutral` | 13 阶(0/50/100/200/300/400/500/600/700/800/900/950/1000) | 文本、背景、border、表面 | `--ew-color-neutral-100` = `#f4f5f7` |
| `brand`(cyan) | 12 阶(50~950) | primary CTA、链接、selected、focus ring | `--ew-color-brand-500` = `#06b6d4` |
| `accent`(violet) | 12 阶(50~950) | 强调元素、progress bar、可视化第二色 | `--ew-color-accent-500` = `#a855f7` |
| `success` | 5 阶(100/300/500/700/900) | 状态-成功 | `--ew-color-success-500` = `#22c55e` |
| `warning` | 5 阶 | 状态-警告 | `--ew-color-warning-500` = `#f59e0b` |
| `danger` | 5 阶 | 状态-危险 / 错误 | `--ew-color-danger-500` = `#ef4444` |
| `info` | 5 阶 | 状态-信息(= brand 同色族,避免引入第 5 色相) | `--ew-color-info-500` = `var(--ew-color-brand-500)` |

#### 语义层(theme-dependent — 跟 `html[data-theme]` 切换)

| Token | dark 值 | light 值 | 用途 |
|---|---|---|---|
| `--ew-surface-bg` | `neutral-950` | `neutral-50` | 整页背景 |
| `--ew-surface-base` | `neutral-900` | `neutral-0` | 主内容容器 / sidebar / topbar |
| `--ew-surface-raised` | `neutral-800` | `neutral-0`+shadow | 卡片头 / 表头 / 标签 |
| `--ew-surface-overlay` | `neutral-700` | `neutral-0`+strong shadow | 下拉 / popover |
| `--ew-surface-hover` | rgba(white, 0.04) | rgba(black, 0.04) | hover 态 |
| `--ew-surface-active` | rgba(white, 0.08) | rgba(black, 0.08) | active / pressed |
| `--ew-surface-selected` | rgba(brand-500, 0.12) | rgba(brand-500, 0.08) | 列表选中 |
| `--ew-border-subtle` | `neutral-800` | `neutral-200` | 分隔线 |
| `--ew-border-default` | `neutral-700` | `neutral-300` | 控件 border |
| `--ew-border-strong` | `neutral-600` | `neutral-400` | hover 后控件 border |
| `--ew-border-focus` | `brand-500` | `brand-600` | focus ring(键盘) |
| `--ew-text-primary` | `neutral-100` | `neutral-900` | 主文 |
| `--ew-text-secondary` | `neutral-400` | `neutral-600` | 副文 |
| `--ew-text-tertiary` | `neutral-500` | `neutral-500` | 占位 / 弱提示 |
| `--ew-text-disabled` | `neutral-700` | `neutral-300` | 禁用 |
| `--ew-text-link` | `brand-400` | `brand-700` | 链接 |
| `--ew-action-primary-{bg, fg, bg-hover}` | brand-500/950/400 | brand-600/0/700 | primary 按钮 |
| `--ew-action-danger-{...}` | danger-500/0/700 | 同左 | 危险按钮 |
| `--ew-status-{success/warning/danger/info}-{bg, fg}` | alpha 12% / brand-300 调 | semantic-100 / semantic-700 | badge / banner |
| `--ew-elevation-{1, 2, 3}` | border tint(无 shadow) | 真实 shadow | 卡片 / modal |

> **dark 模式特殊处理**:不依赖 box-shadow 区分层级(暗色下 shadow 不显眼),用 1px border 高光 + 不同 surface 色阶区分。

#### 对比度(WCAG AA 自查)

| 组合 | 对比度 | 通过 |
|---|---|---|
| dark `text-primary` on `surface-bg` | 16.8:1 | AAA |
| dark `text-secondary` on `surface-bg` | 7.4:1 | AAA |
| dark `text-tertiary` on `surface-bg` | 4.7:1 | AA |
| dark `action-primary-fg` on `action-primary-bg` | 8.1:1 | AAA |
| light `text-primary` on `surface-bg` | 14.5:1 | AAA |
| light `text-tertiary` on `surface-bg` | 4.5:1 | AA |

实施期用 `npm run a11y` 跑 axe 复检每个组件的实际对比度。

### 1.2 字体

| Token | 值 | 用途 |
|---|---|---|
| `--ew-font-sans` | `Inter, -apple-system, 'PingFang SC', 'Microsoft YaHei', 'Noto Sans SC', ...` | 默认 UI 字体 |
| `--ew-font-mono` | `'JetBrains Mono', 'SF Mono', Consolas, ...` | code / JSON / SSE log / number cell |

字号(rem-based,1rem = 16px):

| Token | 值 | 用途 |
|---|---|---|
| `--ew-font-size-xs`   | `0.75rem`   = 12px | caption / tag / kbd / table 表头标签 |
| `--ew-font-size-sm`   | `0.8125rem` = 13px | table cell / form field / button(default) |
| `--ew-font-size-base` | `0.875rem`  = 14px | body text(UI 默认) |
| `--ew-font-size-md`   | `1rem`      = 16px | emphasized body / card title |
| `--ew-font-size-lg`   | `1.25rem`   = 20px | section heading(h3) |
| `--ew-font-size-xl`   | `1.5rem`    = 24px | page heading(h2) |
| `--ew-font-size-2xl`  | `2rem`      = 32px | hero / display(h1) |

行高 / 字重:

| Token | 值 | 用途 |
|---|---|---|
| `--ew-line-height-tight`   | 1.2 | heading |
| `--ew-line-height-snug`    | 1.4 | UI / table cell |
| `--ew-line-height-normal`  | 1.5 | body |
| `--ew-line-height-relaxed` | 1.7 | long-form prose(philosophy.md 渲染场景) |
| `--ew-font-weight-regular`  | 400 | 正文 |
| `--ew-font-weight-medium`   | 500 | UI 标签 / 链接 / 按钮 / 强调 |
| `--ew-font-weight-semibold` | 600 | heading / page title |

### 1.3 间距(4px base)

`--ew-space-{1, 2, 3, 4, 6, 8, 12, 16}` = 4 / 8 / 12 / 16 / 24 / 32 / 48 / 64 px。

**典型用法**:
- 控件内 padding 横向 = `--ew-space-3`(12px)
- 控件间隙 = `--ew-space-2`(8px)
- 卡片 padding = `--ew-space-4`(16px)
- 段落间距 / section 间距 = `--ew-space-6`(24px)
- 页面 main padding = `--ew-space-6`
- 大区块分隔 = `--ew-space-8`(32px)

### 1.4 圆角

| Token | 值 | 用途 |
|---|---|---|
| `--ew-radius-xs`   | 2px  | badge / tag / kbd |
| `--ew-radius-sm`   | 6px  | button / input / select / card |
| `--ew-radius-md`   | 8px  | modal / drawer |
| `--ew-radius-lg`   | 12px | hero card / 头像组 |
| `--ew-radius-full` | 9999px | 圆形 avatar / pill |

### 1.5 阴影 / 立体

| Token | dark | light | 用途 |
|---|---|---|---|
| `--ew-elevation-1` | 1px border tint | 1px / 2px subtle | 卡片(默认) |
| `--ew-elevation-2` | 4px shadow + border | 2px / 6px medium | dropdown / popover |
| `--ew-elevation-3` | 16px shadow + strong border | 8px / 24px strong | modal / drawer |

### 1.6 动效

| Token | 值 | 用途 |
|---|---|---|
| `--ew-duration-fast` | 100ms | hover / focus 反馈 |
| `--ew-duration-base` | 150ms | 默认过渡 |
| `--ew-duration-slow` | 200ms | dropdown 出场 / dialog 出场 |
| `--ew-ease-standard` | `cubic-bezier(0.2, 0, 0.2, 1)` ≈ ease-out | 默认 |
| `--ew-ease-emphasized` | `cubic-bezier(0.3, 0, 0, 1)` | 强调出场 |
| `--ew-ease-linear` | linear | progress bar / skeleton pulse |

`prefers-reduced-motion: reduce` 时所有 duration 归 0(已在 tokens.css 实现)。

### 1.7 z-index

| Token | 值 |
|---|---|
| `--ew-z-base` | 0 |
| `--ew-z-sticky` | 10 |
| `--ew-z-dropdown` | 100 |
| `--ew-z-overlay`(modal backdrop) | 900 |
| `--ew-z-modal` | 1000 |
| `--ew-z-toast` | 10000 |
| `--ew-z-tooltip` | 20000 |

---

## 2. 品牌与 wordmark

### 2.1 wordmark "Expert Work"

- 全小写:**Expert Work**
- 字体:Inter Semibold(600)
- 字间距:`letter-spacing: -0.02em`(略紧凑)
- 颜色:dark = `--ew-text-primary`(neutral-100);light = `--ew-text-primary`(neutral-900);可选 brand 色版本用于 splash / 加载页

### 2.2 DNA glyph(favicon + sidebar 左上角)

- 几何:两条相对扭曲的曲线交叉一次,共 4 个交叉点(简化的 DNA 双螺旋)
- 视觉:线宽 2px,描边色 = brand-500(cyan),无填充
- 尺寸:16 / 20 / 32 / 180(favicon 全套)
- 文件位置:`mockups/shared/brand-glyph.svg`(H.1a 阶段产出,作为占位 SVG)
- **不做独立 logomark** —— glyph 仅作 favicon 与 sidebar 装饰

### 2.3 favicon 集

| 文件 | 尺寸 | 用途 |
|---|---|---|
| `favicon.ico` | 16/32 双尺寸 | 浏览器标签 |
| `favicon-180.png` | 180px | iOS home screen |
| `favicon.svg` | scalable | 现代浏览器 |

---

## 3. 核心组件 inventory(+ Antd 5 映射 + override 要点)

> H.1b 实施时,**所有组件 = Antd 5 基础组件 + Expert Work theme override**。本节列每个组件需 override 哪些 Antd token / className。
>
> 表格 "shell.css 类名" 列指向 mockup 阶段的 CSS class(无 Antd 依赖,直接渲染),H.1b 转 Antd 时取代。

### 3.1 表单

| 组件 | Antd 5 | shell.css 类 | 关键 override |
|---|---|---|---|
| Button | `<Button>` | `.ew-btn` + `.ew-btn--{primary,secondary,ghost,danger}` | `colorPrimary` → brand-500;`borderRadius` → 6;`controlHeight` → 32;`fontWeight` → 500 |
| Input | `<Input>` | `.ew-input` | `colorBgContainer` → surface-base;`colorBorder` → border-default;`controlOutline` → border-focus |
| Select | `<Select>` | `.ew-select` | 同 Input;dropdown surface = surface-overlay |
| Checkbox | `<Checkbox>` | — | `colorPrimary` → brand-500 |
| Radio | `<Radio>` | — | 同 Checkbox |
| Switch | `<Switch>` | — | track on = brand-500,off = neutral-700 |
| Slider | `<Slider>` | — | track = brand-500;handle 圆形 |
| DatePicker | `<DatePicker>` | — | 调整为 dark/light theme adapter |
| Upload | `<Upload>` | — | 拖拽区 border-dashed border-default |
| Form | `<Form>` | — | label 字号 `--ew-font-size-sm`;requireMark 用 brand-500 |

### 3.2 容器

| 组件 | Antd 5 | shell.css | 关键 override |
|---|---|---|---|
| Card | `<Card>` | `.ew-card` | 默认 border + radius-sm;`hoverable` 仅在显式需要时启用 |
| Modal | `<Modal>` | `.ew-modal` | radius-md;width 默认 520;header/footer padding 16 |
| Drawer | `<Drawer>` | — | radius-md(左 0);width 480 / 640 |
| Popover | `<Popover>` | — | surface-overlay + elevation-2 |
| Tooltip | `<Tooltip>` | — | surface-overlay text-primary;最小 padding |
| Tabs | `<Tabs>` | `.ew-tabs` + `.ew-tab` | border-bottom 单线;active 用 brand-500 underline + text-primary |
| Collapse | `<Collapse>` | — | bordered=false;箭头 lucide chevron |

### 3.3 数据

| 组件 | Antd 5 | shell.css | 关键 override |
|---|---|---|---|
| Table | `<Table>` | `.ew-table` | 表头 surface-raised + uppercase xs;行高 36-40px;hover surface-hover;斑马纹 = none(干净 ops 风) |
| List | `<List>` | — | 同 Table 风格,适合长 feed |
| Tree | `<Tree>` | — | 树线 border-subtle;节点 hover surface-hover |
| Badge | `<Badge>` + `<Tag>` | `.ew-badge--{...}` | bg 12% alpha + fg 浅色阶;含 dot variant |
| Tag | `<Tag>` | `.ew-badge--neutral` | bordered=false |
| Avatar | `<Avatar>` | — | 圆形(`radius-full`);默认色根据 user_id hash 选 |
| Statistic | `<Statistic>` | `.ew-stat` | 数值用 mono 字体;label uppercase xs;delta 用 success/danger 色 |

### 3.4 反馈

| 组件 | Antd 5 | shell.css | 关键 override |
|---|---|---|---|
| Alert(内联) | `<Alert>` | `.ew-banner--{...}` | banner 形态(无关闭按钮 / 占整行) |
| Banner(顶部 system) | 自研 wrapper of `<Alert banner>` | `.ew-banner` | sticky top,跨页面 |
| Toast | `<message>` / `<notification>` | — | top-right;auto-dismiss 3-5s;非 destructive 操作用 toast |
| Skeleton | `<Skeleton>` | `.ew-skeleton` | 用于 loading;respects reduced-motion |
| Spin | `<Spin>` | — | 仅在 >800ms 不确定耗时场景 |
| Progress | `<Progress>` | — | track = brand-500;height 6;不带百分比文字时仅 bar |
| Result(空 / 错误页) | `<Result>` | `.ew-empty` | icon + 1 title + 1 desc + 1 主 CTA |

### 3.5 导航

| 组件 | Antd 5 | shell.css | 关键 override |
|---|---|---|---|
| Menu(sidebar) | `<Menu mode="inline">` | `.ew-sidebar__item` | 无图标边框;active 用 surface-selected + brand 色文字 |
| Breadcrumb | `<Breadcrumb>` | `.ew-breadcrumb` | 分隔符 `chevron-right` lucide;颜色 text-tertiary |
| Pagination | `<Pagination>` | — | cursor-based;隐藏快速跳页(用 Cmd+K) |
| Steps | `<Steps>` | — | dot 模式;active dot = brand-500 |

### 3.6 特化组件(Expert Work 自研,无 Antd 直接对等)

| 组件 | 用途 | 实现要点 |
|---|---|---|
| **CommandPalette(Cmd+K)** | 全局跳转 / 命令 / 搜索 | 用 `kbar` 或自研;模糊搜索 agents/runs/skills/triggers/settings;聚焦后 esc 关闭 |
| **TenantSwitcher** | 顶 bar 上切 tenant + user 的视角 | dropdown,显示当前 tenant 全名 + 切换列表(只展示当前 user 有权访问的) |
| **TraceViewer** | run trace 时间线 | 横向时间轴 + 嵌套 spans 树;每条 span 显示 name / duration / status;点 → 右侧详情面板 |
| **MonacoEditor wrapper** | YAML manifest 编辑器 | `@monaco-editor/react`;YAML 语法高亮 + Pydantic schema 校验实时回显;主题跟随 dark/light |
| **SSELiveLog** | Playground / Run 详情的 SSE 实时流 | EventSource;自动滚到底;virtualized list(>1000 行) |
| **JsonViewer** | 任何 JSON 详情显示 | `react-json-tree` 风格;可折叠;字段高亮 |
| **DateRangePicker(短) preset** | 时间区间筛选 | 默认 preset:1h/24h/7d/30d/custom |
| **CopyButton** | code / id / token 复制 | 内联按钮 hover 出现;成功 1s toast |
| **ConfirmDialog**(destructive) | 删除 / rotate / 强结束 | 必须输入 confirm 关键字(目标 name)才能提交;落 audit |

---

## 4. 布局 / 栅格

- 顶 bar 高:**48px**(`--ew-topbar-height`)
- Sidebar 宽:**220px**(`--ew-sidebar-width`)
- 主内容 padding:**24px**(`--ew-content-padding`)
- 主内容 max-width:**1280px**(超过居中)
- 12 列栅格,gutter 16px(用 CSS Grid 实现)
- 响应式:**M0 仅支持 ≥1280px desktop**;< 1280 显示 banner 提示用 desktop。tablet/mobile 推 M1(运营人群不在手机上做 ops)

```
+----------------------------------------------------------+
| topbar 48px(brand glyph + tenant + Cmd+K + user)        |
+----------+-----------------------------------------------+
| sidebar  | main(max 1280, padding 24)                  |
| 220px    |                                               |
|          |                                               |
|          |                                               |
+----------+-----------------------------------------------+
```

---

## 5. 页面骨架

### Shell
所有页面用同一个 Shell:`<sidebar>` + `<topbar>` + `<main>`(CSS Grid `grid-template-areas`)。
见 [shell.css](./mockups/shared/shell.css) § 3。

### PageHeader
- breadcrumb(tenant / area / resource id) — 顶 4px
- title row:title(h2) + 状态 badge + 右侧 actions
- subtitle(optional)
- 底部 24px margin

### EmptyState
- 居中图标(32px lucide,40% opacity)
- title(md semibold)
- desc(sm secondary)
- 主 CTA(primary button)

### ErrorBoundary
- 同 EmptyState 视觉
- 错误信息 mono 字体(可复制)
- "重试" 按钮 + "联系支持" link

---

## 6. 图标系统

- 库:**Lucide**(`lucide-react`)
- 与 Linear / Vercel 同源,~1500 个图标
- 尺寸标准:14 / 16 / 20(line-width 1.5 默认)
- color:继承 currentColor;状态色用 badge 包裹
- 自定义图标(brand glyph 等)放 `src/icons/`

**严禁**混用 Antd 自带 IconFont(`@ant-design/icons`)与 Lucide —— 风格不一致。Antd 组件需 icon 时用 `lucide-react` 显式传 prop。

---

## 7. 键盘 + Cmd+K

### 全局快捷键

| 快捷键 | 行为 |
|---|---|
| `Cmd/Ctrl + K` | 打开 CommandPalette |
| `Cmd/Ctrl + /` | 显示当前页快捷键帮助 |
| `Esc` | 关闭最顶 modal / dropdown / palette |
| `g` then `a` | 跳 Agents |
| `g` then `r` | 跳 Runs |
| `g` then `s` | 跳 Settings |
| `?` | 弹出全局快捷键参考 |

### CommandPalette 模型

- 触发:Cmd+K 全局
- 模式:
  - **跳转**:输入 "agents" / "skill" / 资源名 → 模糊匹配并跳转
  - **动作**:输入 "create agent" / "rotate api key" / "promote candidate" → 执行
  - **搜索**:输入任意文本 → 跨资源全文搜索(M0 仅 client-side 模糊,M1 接后端搜索)
- 键盘:↑↓ 选择,Enter 执行,Esc 关闭

---

## 8. 国际化

- 库:`i18next` + `react-i18next`
- 默认 locale:`zh-CN`
- 完整覆盖 locale:`en`
- 切换:用户偏好(`html[lang]`),也可通过 user settings 覆盖
- **禁止字符串拼接**:必须用 `t('key', { var })` 形式
- 复数 / 性别:`ICU MessageFormat`(`i18next-icu`)
- **技术术语保留 en 不译**(见 § 11 术语表):Agent / Run / Thread / Trace / Span / Manifest / Skill / Trigger / Memory / Curation / Eval / Tenant / Service Account / API Key / Quota / Audit / Artifact / Sandbox / Volume

---

## 9. 无障碍 — H.1b 实施期硬约束

### lint 期(CI 强制)
- `eslint-plugin-jsx-a11y` 全规则 error
- 任何 `outline: none` 必须配对 `:focus-visible` 自定义 ring
- `<img>` 必须有 `alt`(装饰图用 `alt=""`)

### 测试期
- `@testing-library/react` + `axe-core`:每个 page-level test 跑 `expect(await axe(container)).toHaveNoViolations()`
- 关键页 mockup 阶段已用 axe DevTools 抽样

### 运行期
- focus ring 必可见(brand-500 1px + 1px offset)
- aria 标签全:`<button aria-label>`、`<input aria-describedby>`、`<table>` 必有 `<caption>` 或 aria-labelledby
- 表单 label 必关联 `htmlFor` + id
- tab role 与 `aria-selected` 同步
- toast / modal 用 `role="alert"` / `role="dialog"` + `aria-modal="true"`

---

## 10. dark / light 切换实现

```css
/* default = dark */
:root { ... dark tokens ... }

/* explicit override */
html[data-theme="light"] { ... light tokens ... }
```

JS 切换:
```js
document.documentElement.setAttribute('data-theme', userTheme);
localStorage.setItem('ew-theme', userTheme);
```

**不依赖 `prefers-color-scheme`**(避免用户显式选择与系统设置冲突)。但 **首次访问**:若 user setting 无,读 `prefers-color-scheme` 作为初始默认,之后用户选择 override。

---

## 11. 术语表(zh-CN ⇄ en)

> i18n key 锚定来源:UI 中所有可见字符串必须出自此表(对应 key 在 `locales/zh-CN.json` / `locales/en.json`)。

| en | zh-CN | 说明 |
|---|---|---|
| Agent | Agent(不译) | 一个 Expert Work 智能体,有 manifest / runs / skills / triggers / memory |
| Manifest | Manifest(不译) | Agent 的 YAML 定义 |
| Run | Run(不译) | Agent 的一次执行 |
| Thread | Thread / 会话(上下文) | 一组连续 runs 的共享上下文 |
| Trace | Trace(不译) | 一次 Run 的可观测时间线 |
| Span | Span(不译) | Trace 中的一段执行片段 |
| Skill | Skill(不译) | Agent 可调用的能力 / 工具 |
| Trigger | Trigger(不译) | Cron / Webhook 等触发器 |
| Memory | Memory / 记忆 | 长期记忆 |
| Curation | Curation / 策划 | 学习闭环候选 |
| Eval Dataset | Eval Dataset / 评测集 | 黄金集 / 回归集 |
| Tenant | Tenant / 租户 | 多租户隔离边界 |
| User | User / 用户 | tenant 内的人 |
| Service Account | Service Account / 服务账户 | 机器身份 |
| API Key | API Key(不译) | 服务身份的密钥 |
| Role Binding | Role Binding / 角色绑定 | RBAC 授权 |
| Quota | Quota / 配额 | 资源 / 调用限额 |
| Audit | Audit / 审计 | 操作日志 |
| Artifact | Artifact / 产物 | Run 产生的文件 |
| Sandbox | Sandbox / 沙盒 | 隔离执行容器 |
| Volume | Volume / 卷 | 持久工作区 |
| Approval | Approval / 审批 | HITL 人在回路批准 |
| Webhook | Webhook(不译) | HTTP 触发器 |
| Cron | Cron(不译) | 定时触发器 |
| Playground | Playground(不译) | per-agent 调试会话 |

---

## 修订记录

| 日期 | 版本 | 说明 |
|---|---|---|
| 2026-05-25 | v1.0 | 初稿:tokens(13+12+12+5×4 + 双主题语义层)+ 字体 + 间距 + 圆角 + 阴影 + 动效 + z-index + 28 个 Antd override 要点 + 9 个特化组件 + a11y 硬约束 + 术语表(锁 [admin-ui-design-baseline] 10 条决策的产物之一) |
