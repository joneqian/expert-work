# Stream H — Admin UI(设计先行)

> 落实 [docs/ITERATION-PLAN.md](../ITERATION-PLAN.md) § Stream H。
> Admin UI 是 helix-agent 给操作人群(平台 admin / agent 开发者 / 运营)的产品级前端入口。
> Business 系统通过 API 消费 helix 的 per-user 持久 agent 能力 —— **helix 自身不给末端用户做 UI**;
> 末端用户通过 business 系统自己的 UI 与 agent 对话(见 [memory:target-product-form](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/project_target_product_form.md))。

设计先行规则([memory:feedback_design_first_iteration](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_design_first_iteration.md)):
**任何一行 React 代码落地之前**,先完成 H.1a 的三件套(philosophy + language + mockups);
H.1b+ 的 PR 仅执行已锁定的设计基线,不再原地拍脑袋视觉决策。

---

## 0. 范围澄清(2026-05-25 用户确认)

**原 ITERATION-PLAN H.4(用户面 — per-user 持久 agent 形态)取消** —— Business 系统通过 API
消费 agent,helix 自身不需要给末端用户做对话/工作台 UI。原 H.4 的 memory CRUD / artifact 浏览
/ 沙盒会话查看 等 backend 能力**保留**(K6 memory / J.9 artifact / J.15 sandbox API 不动),
但**改由 business 系统的 UI 消费**;helix Admin UI 中作为 admin 视角的"跨 user / 跨 agent
记忆治理 / artifact 列表"出现在 Memory / Operations 区域。

debug 能力作为 **per-agent Playground tab**(嵌在 Agent 详情页的 tab 内),不是独立产品面。

---

## 1. 范围 & 边界

### 1.1 In-scope

| 子项 | 实现内容 | 关联 |
|------|---------|------|
| **H.1a 设计基线**(3-5 天) | `docs/design/admin-ui-philosophy.md` + `admin-ui-language.md` + `mockups/01..08-*.html` + `mockups/shared/{tokens.css, shell.css}`;**先于 H.1b 任何代码落地** | 本规划 PR1 |
| **H.1b React 19 + Vite + Antd 5 骨架**(5-7 天) | 仓库根 `apps/admin-ui/`(Vite 工程);Antd 5 ConfigProvider 把 `--hx-*` token 接入 design token;i18n(zh-CN/en);路由(`react-router-dom` v7);鉴权(API Key / JWT);CommandPalette(`kbar`);Lucide 图标;dark/light theme toggle | 接 control-plane `/v1/*` API |
| **H.2 Agent / Manifest 管理 + per-agent Playground tab** | Agents 列表 / Monaco YAML 编辑器(实时 Pydantic 校验回显)/ 版本对比 / 历史回滚 / **per-agent Playground tab(debug 会话:左 input + 可改 manifest snippet;右 SSE 消息流 + tool calls + trace timeline)** | 接 B.5 Agent CRUD + runs SSE + J.8 approval |
| **H.3 Runs + Trace + Approval** | thread / run 列表 / SSE 实时事件回放 / Langfuse trace 嵌入 / J.8 审批请求列表 + 批准/拒绝/修改入参面板 | 接 B.6/B.7 + E.5 + J.8 |
| **H.4 治理面 — Memory / Curation / Eval / Skills / Triggers / Settings** | 跨 agent / 跨 user 的治理视图:memory 列表+编辑+删除(接 K6)/ artifact 列表+下载(接 J.9)/ curation 候选评审(接 J.12)/ eval dataset CRUD(接 J.12)/ skill 库(接 J.7)/ trigger 列表(接 J.10)/ Settings(API Key / Service Account / Role Binding / Tenant Quota / Audit, 接 C/D/F/G) | 接 J.7/J.9/J.10/J.12/K.6 + C/D/F/G 治理面 |
| **H.5 docker-compose dev.yml** | 已在 I.1 `--profile full` 落地 | I.1 |

> **H.1a / H.1b 拆两个 PR**:H.1a 是纯文档 + CSS;H.1b 是真 Vite 工程。
> **H.2 / H.3 / H.4** 每个再切 2-3 个 PR,按子能力面切(每个 PR 接 1-2 个 backend router 端到端)。

### 1.2 Out-of-scope(明确推迟)

| 推迟项 | 落地 | 备注 |
|-------|------|------|
| 末端用户对话 UI / 客户工作台 | **不做** | Business 系统通过 API 自建(2026-05-25 用户确认) |
| 营销官网 / 落地页 / pricing | M2+(若有 GTM 时) | 独立产品 |
| 响应式 tablet/mobile | M1 | M0 仅支持 ≥1280px desktop(运营人群不在手机上做 ops) |
| logomark(独立 logo 图形) | 暂不做 | wordmark + DNA glyph favicon 已足够品牌识别 |
| 真实搜索后端(跨资源全文) | M1 | M0 CommandPalette 仅 client-side 模糊 |
| Storybook | 按需 | H.1b 视复用率而定;非 M0 强制 |
| E2E 测试(Playwright)| H.6(未排期) | M0 仅集成测试 happy path |

### 1.3 验收(Stream H Exit Criteria)

- **H.1a**:文档自洽(无 TBD / TODO);8 张 mockup `open *.html` Chrome/Safari/Firefox 渲染正常;dark/light 切换 OK;至少 3 张 mockup axe DevTools / Lighthouse contrast ≥ 4.5:1
- **H.1b**:Vite 工程 `npm run dev` 起 admin-ui 跑通,登录后能看到 Shell + 至少一个空页(Agents 列表 empty state)
- **H.2-H.4** 每个子项:
  - 产品级体验(不仅"能用") —— 键盘可达 / a11y 验证 / dark/light 双过 / i18n 双语完整
  - UI 集成测试 happy path(`@testing-library/react` + 真后端 mock)
  - 接入的 backend router 在 UI 上端到端可见(创建 → 列表 → 详情 → 修改 → 删除)
- **H 整体**:首屏 < 2s(在本地 dev compose 环境);Lighthouse Performance ≥ 90 / Accessibility ≥ 95

---

## 2. 架构

### 2.1 单 SPA,操作端唯一

```
                  ┌─────────────────────────────┐
                  │  apps/admin-ui (Vite SPA)   │
                  │  React 19 + Antd 5 + Lucide │
                  │  Shell: sidebar 220 + top 48│
                  └──────────────┬──────────────┘
                                 │  HTTPS + Bearer(API Key) / JWT
                                 │  SSE for runs / playground
                                 ▼
                  ┌─────────────────────────────┐
                  │  control-plane (FastAPI)    │
                  │  全部 /v1/* router(已 shipped)│
                  └─────────────────────────────┘
```

无 BFF,无 GraphQL,直接打 control-plane REST + SSE。

### 2.2 工程目录(H.1b 阶段建立)

```
apps/admin-ui/
├── package.json
├── vite.config.ts
├── tsconfig.json
├── index.html
├── src/
│   ├── main.tsx
│   ├── App.tsx
│   ├── router.tsx
│   ├── api/                  (control-plane SDK,每个 router 一个 file)
│   │   ├── client.ts         (axios + auth interceptor)
│   │   ├── agents.ts
│   │   ├── runs.ts
│   │   ├── skills.ts
│   │   ├── triggers.ts
│   │   ├── memory.ts
│   │   ├── curation.ts
│   │   ├── eval_datasets.ts
│   │   └── api_keys.ts
│   ├── components/           (helix 自研组件)
│   │   ├── Shell.tsx
│   │   ├── Sidebar.tsx
│   │   ├── Topbar.tsx
│   │   ├── CommandPalette.tsx
│   │   ├── TenantSwitcher.tsx
│   │   ├── TraceViewer.tsx
│   │   ├── MonacoYamlEditor.tsx
│   │   ├── SSELiveLog.tsx
│   │   └── ConfirmDialog.tsx
│   ├── pages/
│   │   ├── agents/           (List, Detail with tabs)
│   │   ├── runs/
│   │   ├── curation/
│   │   ├── memory/
│   │   ├── skills/
│   │   ├── triggers/
│   │   └── settings/
│   ├── theme/
│   │   ├── tokens.css        (从 docs/design/mockups/shared/tokens.css 移植)
│   │   ├── shell.css         (基础排版,Antd 不能覆盖的细节)
│   │   └── antdTheme.ts      (Antd ConfigProvider 主题,把 --hx-* 映射到 Antd token)
│   ├── i18n/
│   │   ├── index.ts          (i18next init)
│   │   └── locales/
│   │       ├── zh-CN.json
│   │       └── en.json
│   ├── icons/                (DNA glyph + 自定义 SVG)
│   └── utils/
└── tests/
```

### 2.3 设计基线 → 工程的映射

| 设计文档产物 | 工程对应位置 |
|---|---|
| `docs/design/mockups/shared/tokens.css` | `apps/admin-ui/src/theme/tokens.css`(直接 copy) |
| `docs/design/mockups/shared/shell.css` | 部分迁移至 `apps/admin-ui/src/theme/shell.css`;Antd 能覆盖的部分用 ConfigProvider |
| `docs/design/admin-ui-language.md` § 3 (component override) | `apps/admin-ui/src/theme/antdTheme.ts` |
| `docs/design/admin-ui-language.md` § 11 (术语表) | `apps/admin-ui/src/i18n/locales/{zh-CN,en}.json` 的 keys |
| `docs/design/mockups/0X-*.html` | `apps/admin-ui/src/pages/*` 的初始视觉参考 |

---

## 3. IA(对应 [philosophy.md § 4](../design/admin-ui-philosophy.md#4-ia-心智模型--agent-是中心实体))

### 一级导航(瘦左边栏,220px)

| Order | label | route | 主要 backend 端点 |
|---|---|---|---|
| 1 | Agents | `/agents` | `/v1/agents` |
| 2 | Runs | `/runs` | `/v1/sessions/*/runs`(跨 agent 视角) |
| 3 | Curation+Eval | `/curation` / `/eval-datasets` | `/v1/curation`, `/v1/eval-datasets` |
| 4 | Memory | `/memory` | `/v1/memory` |
| 5 | Skills | `/skills` | `/v1/skills` |
| 6 | Triggers | `/triggers` | `/v1/triggers` |
| 7 | Settings | `/settings/*` | `/v1/api_keys`, `/v1/service_accounts`, `/v1/role_bindings`, `/v1/tenants/*/quotas`, `/v1/tenants/*/config`, audit |

### Agent 详情页 7 个 tabs(per-agent 视角)

`/agents/:id/{overview,manifest,playground,runs,skills,triggers,memory}`

| Tab | 端点 |
|---|---|
| Overview | `/v1/agents/:id` + 衍生 stats |
| Manifest | `/v1/agents/:id/manifest`(YAML + 版本) |
| **Playground** | `/v1/sessions`(临时 thread)+ `/v1/sessions/:id/runs`(SSE) |
| Runs | `/v1/sessions/*/runs?agent_id=:id` |
| Skills | `/v1/agents/:id/skills`(per-agent skill bindings) |
| Triggers | `/v1/triggers?agent_id=:id` |
| Memory | `/v1/memory?agent_id=:id` |

---

## 4. 关键页面 mockup 索引(详见 [mockups/README.md](../design/mockups/README.md))

| # | 页面 | 对应 H 子项 |
|---|---|---|
| 01 | Agents 列表 | H.2 |
| 02 | Agent 详情 — Overview | H.2 |
| 03 | Agent 详情 — **Playground** | H.2 |
| 04 | Run 详情 + Trace | H.3 |
| 05 | Curation 候选评审 | H.4(Curation) |
| 06 | Memory admin | H.4(Memory) |
| 07 | Settings — API Keys | H.4(Settings) |
| 08 | Cmd+K 命令面板 | H.1b(全局) |

---

## 5. Mini-ADR

### Mini-ADR H-1:Antd 5 + 设计基线 over headless UI / 自研组件库
**Context**:M0 周期紧(2.5-3 周做完 Stream H),从零写组件库不现实;headless UI(Radix / Headless UI)灵活但要补很多无障碍 / state 细节。
**Decision**:用 Antd 5 + helix 设计基线 override —— Antd 自带 a11y / i18n / form / table 全套,我们只要 token + override 几个关键组件细节即可。
**Consequences**:与 Antd 5 升级节奏耦合(可接受);特化组件(CommandPalette / TraceViewer / SSELiveLog / MonacoEditor)仍要自研。

### Mini-ADR H-2:per-agent Playground 而非独立 Sessions 区
**Context**:debug 能力是 helix 操作人群的核心诉求;放哪里取决于谁需要看 + 看的时候手边有什么。
**Decision**:Playground 作为 Agent 详情页 tab,**不独立顶级导航**。理由见 [philosophy.md § 5](../design/admin-ui-philosophy.md#5-operator--debug-双能力同面)。
**Consequences**:跨 agent debug 比较只能开多 tab;后续若数据表明 ops 真有跨 agent 比较高频诉求,M1 再加顶级 Sessions 区(增量,不矛盾)。

### Mini-ADR H-3:dark-first + light 同样产品级
**Context**:LLM ops 长读 trace / log / JSON;dark 适眼。但 light 仍有 screenshot / 打印 / 演示场景。
**Decision**:两个主题都过 WCAG AA + 完整 token 双套;默认 dark;`html[data-theme]` 切换不依赖 `prefers-color-scheme`。
**Consequences**:实施成本 +30%(双 token + 双套对比度验证);收益:覆盖所有真实场景。

### Mini-ADR H-4:CSS variable + ConfigProvider over Tailwind / Styled-components
**Context**:与 Antd 集成 + 主题切换 + 实施成本三角。
**Decision**:用 CSS custom properties(`--hx-*`)+ Antd ConfigProvider 注入 token + 极少 inline override。**不引入 Tailwind**(与 Antd 默认样式冲突 + 学习成本);**不用 styled-components**(运行期成本 + 与 Antd 主题割裂)。
**Consequences**:主题切换 = 改 `<html data-theme>`,纯 CSS,零 JS;Antd token 通过 antdTheme.ts 一处映射;mockup 阶段 token 可直接复用。

### Mini-ADR H-5:Lucide,禁止混用 Antd IconFont
**Context**:Antd 5 自带 `@ant-design/icons`(800+ 图标);Lucide 1500+ 现代风格(线 1.5 / 圆角)。
**Decision**:**全栈用 Lucide**,禁止用 Antd IconFont。Antd 组件需 icon 时显式传 `icon={<LucideIcon />}`。
**Consequences**:bundle 多 ~50KB(可接受;tree-shake 后实际更小);视觉一致性 ↑↑(混用会显得拼接)。

---

## 6. PR 链(预估)

| PR | 内容 | 估时 |
|----|------|------|
| PR1 | H.1a 设计基线 PR1 — philosophy.md + language.md + STREAM-H-DESIGN.md + tokens.css + shell.css + ITERATION-PLAN 修订 | 1.5 天 |
| PR2 | H.1a 设计基线 PR2 — 8 张 mockup HTML + mockups/README.md + brand glyph SVG + a11y 自检截图 | 2-3 天 |
| PR3 | H.1b — `apps/admin-ui/` Vite 工程 scaffold + Antd 5 + i18n + 路由 + 鉴权 + Shell + Sidebar + Topbar + 空状态首页 | 3-4 天 |
| PR4 | H.1b — CommandPalette + TenantSwitcher + 主题切换 + Lucide 接入 | 1-2 天 |
| PR5 | H.2 — Agents 列表 + 创建 + 详情 Overview tab + Manifest tab(Monaco YAML)| 3-4 天 |
| PR6 | H.2 — Agent 详情 Playground tab(SSE 流 + tool calls + trace timeline + 改 manifest 重跑) | 3-4 天 |
| PR7 | H.3 — Runs 列表 + 详情 + Trace + Approval 面板 | 3-4 天 |
| PR8 | H.4(Curation+Eval)— 候选评审 + eval dataset CRUD | 2 天 |
| PR9 | H.4(Memory)— per-user memory 列表 + 编辑 + 删除 | 1.5 天 |
| PR10 | H.4(Skills+Triggers)— 跨 agent skill / trigger 列表 + CRUD | 2 天 |
| PR11 | H.4(Settings)— API Keys + Service Accounts + Role Bindings + Quotas + Tenant Config | 2-3 天 |
| PR12 | H.4(Audit)— audit 查询 + 过滤 + 详情 | 1 天 |

> 总估时 25-32 天 = 2.5-3 周(一个全职前端)。可与后端 / 其他 stream 并行。

---

## 7. 验证

### H.1a 验收(本规划 PR1+PR2 完成)
- philosophy.md 6 条原则 → language.md tokens 实现 → mockup 视觉呈现,三层链路自洽
- 8 张 mockup `open mockups/*.html` 在 Chrome / Safari / Firefox 渲染正常
- mockup 顶部主题切换按钮(dark ⇆ light)无破坏性视觉差异
- 至少 3 张 mockup 跑 axe DevTools 报告 0 critical;Lighthouse a11y ≥ 95
- 术语表覆盖所有 mockup 中可见英中字符串
- `tokens.css` + `shell.css` 无外部依赖,无 SCSS,无 build —— 浏览器直接渲染

### Stream H 整体验收(所有 PR 合并后)
1. UI/UX 设计基线文档先于实现合入(H.1a PR1+PR2 在最前) ✓
2. 每个 H.* 子项产品级体验:响应式(≥1280px desktop)/ 键盘可达 / a11y(axe 0 critical)/ 性能(首屏 < 2s, Lighthouse Performance ≥ 90)
3. UI 集成测试覆盖 happy path(`@testing-library/react`)
4. 接入的 B/E/J/K 能力面在 UI 上端到端可见

---

## 修订记录

| 日期 | 版本 | 说明 |
|---|---|---|
| 2026-05-25 | v1.0 | 初稿:设计基线 PR 链 + IA + 工程目录 + 5 个 Mini-ADR + 12 个 PR 估时;H.4 用户面取消,改为治理面;Playground 嵌 per-agent tab |
