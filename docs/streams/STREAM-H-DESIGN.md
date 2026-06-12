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

### Mini-ADR H-6:`GET /v1/runs` 跨 thread 索引 — 偿还 Mini-ADR J-41 deferred 项
**Context**:H.1b PR 3 RunDetail 备注里写"There is no cross-thread 'list all runs' endpoint yet (Mini-ADR J-41 keeps the per-thread shape); PR 4 wires a control-plane index when it lands" —— H.3 PR 1 兑现这条挂账。今日 RunStore 只有 `list_by_thread(thread_id, tenant_id)`,跨 thread 列表 = N+1 调用,不可接受。
**Decision**:
- `RunStore` ABC 加两个抽象方法,InMemory + SQL 各加实现:
  - `list_for_tenant(*, tenant_id, status: RunStatus | None = None, limit: int = 100, offset: int = 0) -> list[RunInfo]` — newest first(ORDER BY created_at DESC)
  - `list_all_tenants(*, status, limit, offset) -> list[RunInfo]` — 跨租户;调用方 MUST 包 `bypass_rls_session()`(Stream N 同 [trigger sql.py:117](../../packages/helix-persistence/src/helix_agent/persistence/trigger/sql.py))
- 在 `api/runs.py` 旁加 `build_runs_list_router()` 一个新 router(prefix `/v1/runs`),只挂 `GET /v1/runs?status&tenant_id&limit&offset`;走 Stream N `ensure_tenant_scope` + `applied_scope` 框架,响应字段 `{ items, total, cross_tenant }` 与 agents/triggers 列表对齐。
- 在 `app.py` `include_router(build_runs_list_router())`;复用同一 RunStore 单例。
- 不动现有 `/v1/sessions/{thread_id}/runs/*` 端点 —— 这次只是**加**列表入口,旧的按 thread 查询保留。
**Consequences**:Mini-ADR J-41 的 deferred 项("`GET .../runs` 列表 endpoint → H.3")正式偿还,挂账可在 ITERATION-PLAN J.8 行划除;后续 J.10 trigger run 列表 / J.12 curation 候选评审都能复用 `list_for_tenant` 而不必各自实现。

### Mini-ADR H-7:SSE 完整回放 — 持久化 `run_event` + live attach + replay endpoint
**Context**:H.3 要做"完整"的事件流回放,需要:(1) live re-attach 到 running/paused run(`StreamBridge` 已支持多消费者 + `last_event_id` 重连,见 `stream_bridge/memory.py:105`,免费);(2) **terminal run 的历史回放** — 现有 `StreamBridge` 在 `publish_end` 后 `_CLEANUP_DELAY_S = 60s` 清掉事件,长 run 看不到;Playground tab 仅覆盖"new run + 同时观察",不覆盖"事后回看"。J.13a trajectory 录的是 message 不是 SSE frame;A.4 event_log 是 audit 不是 SSE。所以需要**新增持久 SSE event 存储**。

**Decision**:
- **新表 `run_event`**(migration 0038):`(run_id, seq) PRIMARY KEY` + `event_name text NOT NULL` + `data jsonb NOT NULL` + `created_at timestamptz`。索引 `(run_id, seq)` 即覆盖 `WHERE run_id = ? ORDER BY seq ASC` 唯一查询模式。`seq` = 单调整数(per-run),与 SSE `id` 字段一致(`"<ms>-<seq>"` 格式截 `seq`)。
- **新 `RunEventStore` ABC** + InMemory + SQL:`append(run_id, tenant_id, event_name, data, seq, created_at)` / `list(run_id, tenant_id, since_seq=0, limit=…)`。tenant_id 走 `agent_run.tenant_id`,SQL 用 JOIN 校验 RLS;`list` 配合 `bypass_rls_session()` 支持 system_admin。
- **producer 接入**:`run_agent` 工作循环 publish 到 bridge 同步**双写**到 `run_event`(在 `bridge.publish` wrapper 里加 hook,or `run_agent` 直接调用 store)。**失败策略**:store 写失败 → log warning + skip(SSE 不阻塞,事件流是首选);store 写失败计数器 `helix_run_event_persist_errors_total`。
- **新 endpoint `GET /v1/sessions/{thread_id}/runs/{run_id}/events?since_seq=N`**:
  - 若 run 在 `running` / `paused` / `pending` → 走 `bridge.subscribe(last_event_id=...)`(live)
  - 若 run 终态 → 走 `RunEventStore.list(since_seq=...)`(replay)
  - 返回标准 SSE 流(`text/event-stream`),前端同 Playground 复用 `parseSseStream`
  - **SSE id 字段**(决议 A):replay 路径 emit `event.id = f"{row.created_at_ms}-{row.seq}"`,与 `StreamBridge` live emit 同型;客户端 `parseSseStream` 不区分两种来源。
  - **`list_all_tenants` hard cap**(决议 D):`RunEventStore.list` 与 `RunStore.list_for_tenant` / `list_all_tenants` 均强制 `max_limit=500`;caller 传 `limit>500` 被静默截断 + 响应 header `X-Limit-Capped: true`(与 agents/triggers list 既有惯例一致)
- **保留期**:M0 永久(无清理);M1 接入 `retention-cleanup-job`(默认 30 天,与 `event_log` 对齐)。
- **存储成本估算**:typical run ~20-60 frames × ~500 bytes JSON ≈ 10-30 KB/run。1000 runs/day = 30 MB/day = 11 GB/年。M0 可接受;M1 retention 控制。

**Consequences**:
- 完整覆盖"新 run 边看 / 旧 run 回看"两种场景;Playground 与 RunDetail 复用同 SDK(`parseSseStream`)。
- 需新 migration + 新表 + 新 store + producer 改一处。
- `bridge.publish` 仍是关键路径;持久化失败不阻塞(observability 信号告警 + 偶发缺事件是可接受降级)。
- 列表 endpoint 设计与 Stream G.9 `token_usage` 的 ABC + Memory + SQL 三态完全对齐(惯例稳定)。

### Mini-ADR H-8:Trace 嵌入 = 外链跳 Langfuse,不 iframe — H.3 M0
**Context**:Langfuse 自带完整 trace UI(span 树 / token 消耗 / LLM 输入输出对比),自研一份性价比极低。iframe 嵌入需要 Langfuse staging 部署 + CORS / X-Frame-Options 配置 + 跨域 token 转 —— 都是 backend infra 工作(Stream G.7 大盘正在做)。
**Decision**:**M0 = 外链跳**。RunDetail 给一个"Open in Langfuse"按钮,带 `trace_id` 作为 query / path 参数,新窗口打开。`LANGFUSE_BASE_URL` 配在 `apps/admin-ui/.env`(已有 OIDC env 范式);未配则按钮隐藏,显示"trace_id"明码 + 复制按钮兜底。
**Consequences**:M0 体感:点开一次 Langfuse,完整 trace 即得;不破坏 Antd 5 风格;不引入 iframe 通信 / postMessage 复杂度。M1 与 Stream G.7 一起做 inline embed(若 ops 表态要)。

### Mini-ADR H-9.5:`agent_run.trace_id` 持久化(从 v1.4 起 promote 为决策项)
**Context**:H.1b PR 3 的 RunDetail 用一个 "trace in observability" 占位 Alert 表示"trace 看 Langfuse 自己搜"。要真支持 trace 跳转 / 历史回看,需要 `trace_id` 持久挂在 run 上。今日 trace_id 仅在 SSE `metadata` frame 里(运行中拿得到,terminal 之后失踪)。

**Decision**:
- **Migration 0037**:给 `agent_run` 加 `trace_id varchar(32) NULL`(`current_trace_id_hex()` 返回 16 字节 hex = 32 字符)+ 非唯一索引 `(trace_id) WHERE trace_id IS NOT NULL`(支持反查 "from Langfuse trace_id back to run")。
- **`RunInfo` DTO** + **`RunStore`**:加 `trace_id: str | None`。新方法 `set_trace_id(run_id, tenant_id, trace_id)`(idempotent overwrite)。
- **`RunManager`**:`create(*, trace_id: str | None = None, …)` 加显式参数(决议 B);调用方:
  - API handler 路径(`trigger_run` / J.8 resume):传 `trace_id=current_trace_id_hex()`,获取 caller-bound user trace。
  - 自动调度路径(J.10 trigger scheduler / J.13a 自动评估等):传 `trace_id=None`,表示"自动触发,无 user trace"。
  - **不在 `create` 内部隐式调 `current_trace_id_hex()`** —— 若 context propagation 不在(如 trigger scheduler 在自己的 asyncio loop),会取到 scheduler 自己的 trace,污染数据。显式传参保护此场景。

`run_agent` 工作循环开头若发现 OTel 当前 trace_id 与 RunStore 中存的不同(罕见;表示 worker 启了自己的 trace),覆写一次。
- **GET response**:`/v1/sessions/{thread_id}/runs/{run_id}` 返回字段加 `trace_id`;`/v1/runs` 列表也加。
- **Frontend**:`RunInfo` 类型加 `trace_id?`;TraceToolbar 优先读 `run.trace_id`;若仍 null(老数据)兜底为 SSE metadata 取(向后兼容)。

**Consequences**:
- 任何 run(running / terminal / 1 年前)都能跳 Langfuse,完全对齐 #6 完整。
- 1 行 migration + 1 个 DTO 字段 + 1 个 set 方法 + 1 处 API 序列化 + 1 处 frontend 类型 ≈ 50 行实质代码,低风险。
- expand-contract 友好:旧行 `trace_id IS NULL` 不影响新写入;前端有 null 兜底。
- 反查索引 partial(`WHERE trace_id IS NOT NULL`)避免无 trace_id 行膨胀 B-tree。

### Mini-ADR H-9:Approval `override_args` 用 Monaco JSON inline 编辑
**Context**:J.8 API 已支持 `POST .../resume {approved, override_args}` 让审批者修改 agent 提议的工具参数(典型场景:agent 提议删 50 条记录,审批者改成 10 条)。当前 H.1b PR 3 的 RunDetail 只显示 `proposed_args` JSON pretty-print + Approve/Reject 二选,没 override UI。
**Decision**:Approval Alert 里:
- 默认显示 `proposed_args` 为只读 Monaco JSON。
- 顶上加 toggle "Edit arguments" — 切到 edit 模式后 Monaco 可编辑;Approve 按钮文案变 "Approve with edits"(若用户编辑过 JSON;否则正常 "Approve")。
- Save-side 校验:JSON.parse 失败时 Approve 禁用 + 显示语法错误。
- Reject 不需 override(`override_args=null`)。
**Consequences**:复用 H.2 PR 5 已经引入的 `@monaco-editor/react`,零新增 dep;审批者获得正确的"修订入参"能力(原始 J.8 设计目标);M0 关闭 H.1b 的 approval 半成品状态。

---

## 6.5 H.3 详细设计

参考:[`STREAM-J-DESIGN.md § 14`](./STREAM-J-DESIGN.md)(J.8 审批 API + audit + reason_kind 5 类型)/ [`STREAM-N-DESIGN.md`](./STREAM-N-DESIGN.md)(cross-tenant 框架,本节 PR 1 直接复用)。

### 6.5.1 范围 & 出入

| 项 | M0 ✓ | (a) 推迟 |
|---|---|---|
| **跨 thread Runs 列表**(`GET /v1/runs`)| ✓(Mini-ADR H-6) | — |
| **Status / tenant 筛选**(系统 admin 跨租户) | ✓(Stream N 框架) | — |
| **RunDetail 状态轮询**(`paused` / `running` 时每 3s 拉一次) | ✓ | — |
| **Approval Approve / Reject**(继承 H.1b PR 3) | ✓ | — |
| **Approval `override_args` Monaco 编辑** | ✓(Mini-ADR H-9) | — |
| **Approval pending 总数 badge**(顶 nav / Runs 列表头) | ✓ | — |
| **Trace 跳 Langfuse 外链** | ✓(Mini-ADR H-8) | iframe 嵌入推 M1 |
| **`agent_run.trace_id` 持久化** | ✓(Mini-ADR H-9.5;migration 0037)| — |
| **SSE 历史回放** | ✓(Mini-ADR H-7;新表 `run_event` migration 0038 + `RunEventStore` 三态 + `GET .../events` 端点 live/replay 双路径)| 保留期 sweep 推 M1 |
| **审批 SLA 监控指标** | — | M1+(J.8 § 500 显式推迟项) |
| **多审批人 / 升级链** | — | M1 高级 UI(J.8 § 500) |
| **审计日志 inline 显示** | — | H.4 Audit 查询(同 stream H 后续 PR) |

### 6.5.2 后端契约 — `GET /v1/runs`

```
GET /v1/runs?status=paused&tenant_id=*&limit=50&offset=0
Authorization: Bearer <jwt>

200 OK
{
  "success": true,
  "data": {
    "items": [
      {
        "run_id": "...",
        "tenant_id": "...",
        "thread_id": "...",
        "user_id": "..." | null,
        "status": "paused" | "running" | "success" | "error" | "interrupted" | "timeout" | "pending",
        "is_resume": true | false,
        "error": "..." | null,
        "created_at": "...",
        "updated_at": "...",
        "finished_at": "..." | null
      },
      ...
    ],
    "total": 123,
    "cross_tenant": true | false
  },
  "error": null
}
```

- `tenant_id=*` 仅 `system_admin` 可用;否则 `ensure_tenant_scope` 抛 403 + audit。
- `status` 是单值;多 status 用多 query(`?status=paused&status=running`)推迟。
- `total` 严格等于 `items` 长度(M0 不做 COUNT(*) per-tenant 二次查询;`limit + offset` 决定的窗口,前端用 `items.length < limit` 判 last-page);响应字段保持与 agents/triggers 列表一致。
- 404 不存在;403 Stream N 跨租户拒绝;429 quota(走标准 admission 框架)。

### 6.5.3 前端 IA — `/runs` 页

```
[Runs] (顶 nav 等级,左侧栏一级链接,Bot 图标右下角 Activity 标)

  ┌────────────────────────────────────────────────┐
  │ Breadcrumb  Home / Runs                        │
  │ ┌──────────────────────────────────────────┐   │
  │ │ Runs                  [Approval pending: 3] │
  │ │                                            │   │
  │ │ Status: [All ▼]  Tenant: [Home ▼]  [↻]    │   │
  │ ├──────────────────────────────────────────┤   │
  │ │ Run ID      Status   Thread     Agent      │   │
  │ │ a1b2c3…    paused   t-d4e5…    code-reviewer  │
  │ │ a1b2c3…    success  t-d4e5…    code-reviewer  │
  │ │ ...                                        │   │
  │ └──────────────────────────────────────────┘   │
  └────────────────────────────────────────────────┘
```

- **列**:Run ID(8-char 截断 + tooltip 完整 + 点击进 detail)/ Status(Tag 颜色)/ Thread(8-char 截断 + Link)/ Created at(相对时间 + tooltip 绝对)/ Agent(thread 不直接含 agent 字段;**M0 用 thread query;**或在响应里加一次性 JOIN — 见 § 6.5.5)。
- **筛选**:`Status` Select(单选);`Tenant` 复用 `TenantSwitcher`(`home` / `*` / `UUID`)。无搜索框 M0(Mini-ADR J-41 没 agent_name 索引,加是 M1)。
- **状态**:Empty(无 run)/ Loading(Skeleton)/ Error(Alert)/ Pagination(50 一页;客户端按 `items.length < limit` 判末页;total 仅作 hint)。
- **可点击行**:点行 → `/runs/:thread_id/:run_id`(已有 RunDetail 接住)。

### 6.5.4 前端 IA — RunDetail 增强(M0 增量,不重写)

- 状态轮询(Mini-ADR H-7):当 `run.status in {paused, running, pending}`,每 3s `getRun`;一旦进 terminal,立即停轮询。**前提**:页面可见才轮(`document.visibilityState === "visible"`),否则后台 tab 暂停。
- Approval 区(Mini-ADR H-9):
  - 默认 Monaco read-only(`proposed_args`)。
  - "Edit arguments" toggle → 切 edit;Monaco language=json + 错误标。
  - "Approve" / "Approve with edits"(根据 buffer 是否变过)/ "Reject"。
  - 错误 JSON → Approve 禁用 + 显示 `JSON.parse` 错。
- Trace 跳转(Mini-ADR H-8):
  - 顶 toolbar 显示 `trace_id` 8-char + 复制按钮。
  - 若 `VITE_LANGFUSE_BASE_URL` 配置 → 加按钮 "Open in Langfuse"(target=_blank);否则只显示 trace_id。
- Approval pending badge:左侧主导航 Runs 项右侧加红点(`useQuery` 每 60s 拉 `GET /v1/runs?status=paused&limit=1` 看 `total`;0 时不显示)。

### 6.5.5 已知不解决的耦合

- **RunInfo 不含 `agent_name`**:`agent_run` 表只存 `(run_id, thread_id, tenant_id, user_id, status, ...)`;agent 信息住 `thread_meta`。M0 RunsList "Agent" 列要么(a) 每条 run 多查一次 `thread_meta`,要么(b) 在 `GET /v1/runs` 服务端做一次 JOIN 并把 `agent_name + agent_version` 加进响应。
  - **决策**:走(b)。响应字段加 `agent_name: str | None` / `agent_version: str | None`(thread 可能已删 → null)。这不变更 `RunInfo` DTO 持久层 schema,仅在 API 层组合;`build_runs_list_router` 取数后对每个 thread 查一次 thread_meta(M0 量级 ≤ 50 行,N+1 可接受;M1 改成 SQL JOIN)。
- **`override_args` JSON Schema 校验**:M0 仅做 JSON.parse 客户端校验;后端 J.8 resume 接 `override_args: dict` 不做 schema 校验(已有 tool 模板内部校验)。
- ~~**Trace ID 不直接挂在 `RunInfo`**~~ — v1.4 起 promote 为 Mini-ADR H-9.5 解决(migration 0037)。

### 6.5.6 PR 拆分

| PR | 范围 | 估时 |
|---|---|---|
| **H.3 PR 1** | Backend `GET /v1/runs` + RunStore `list_for_tenant` / `list_all_tenants` + SDK `listRuns` + RunsList 页(Mini-ADR H-6)| 2-3 天 |
| **H.3 PR 2** | `agent_run.trace_id` 持久化(migration 0037 + DTO + RunStore.set_trace_id + RunManager 接入 + GET /v1/runs / `/v1/sessions/.../runs/{id}` 序列化 + frontend 类型)(Mini-ADR H-9.5)| 1.5 天 |
| **H.3 PR 3** | `run_event` 持久层(migration 0038 + `RunEventStore` ABC + Memory + SQL + `run_agent` 双写接入 + 错误计数器)(Mini-ADR H-7 backend 部分)| 2 天 |
| **H.3 PR 4** | `GET /v1/sessions/{thread_id}/runs/{run_id}/events` 端点(live attach via `bridge.subscribe` + replay via `RunEventStore`)+ RunDetail 加 Event stream panel(复用 `parseSseStream`)| 1.5-2 天 |
| **H.3 PR 5** | Approval override_args Monaco UX + 状态轮询(Mini-ADR H-9) | 1.5 天 |
| **H.3 PR 6** | Trace 链接外跳(Mini-ADR H-8)+ Approval pending badge + 体感打磨 | 1 天 |

> 总估时 9.5-11 天 ≈ 2 周(原 3-4 天)。**增量主要来自完整 SSE 回放(PR 3+4)+ trace_id 持久化(PR 2)** —— 用户决策"#2 #6 做完整"接受此预算。
>
> **PR 顺序依赖**:
> - PR 2 (trace_id) ↔ PR 1 独立,可并行
> - PR 3 (run_event 持久层) → PR 4 (events endpoint) 必须顺序
> - PR 5 (approval UX) 独立
> - PR 6 (trace 外链 + badge) 依赖 PR 2 (trace_id 字段);独立 PR 1 (badge 用)

### 6.5.7 验收

- **零债 6 条** 每 PR 各跑:无 TODO/FIXME/XXX、ruff/mypy 全过、新增方法测试覆盖、设计文档同步、可观测齐全(`/v1/runs` 加 audit + counter 与现有列表端点一致)、CI 全绿。
- **跨租户冒烟**:`system_admin` 切 "All tenants" → `/runs` 表头 banner `cross-tenant view`;切回 home → banner 消失;两个动作都进 audit。
- **审批端到端**:Playground 起一个 agent → 审批 gate → `/runs?status=paused` 列表见 → 点进 RunDetail → 编辑 override_args → Approve with edits → 状态变 running → 终态 success。
- **a11y**:RunsList axe 0 critical;状态 Tag 颜色不是唯一信号(同时带文字)。

### 6.5.8 文件级影响图

**PR7a — `GET /v1/runs` + RunsList 页**

| 文件 | 变更 | 行数估 |
|---|---|---|
| `packages/helix-runtime/src/helix_agent/runtime/runs/store.py` | ABC + InMemory + SQL 加 `list_for_tenant` / `list_all_tenants` | +85 |
| `packages/helix-runtime/tests/test_run_store.py` | +`test_list_for_tenant_filter_by_status` / `_pagination` / `_tenant_isolation` / `test_list_all_tenants_returns_all` / `_with_status_filter` | +60 |
| `services/control-plane/src/control_plane/api/runs.py` | 加 `build_runs_list_router()`(新 APIRouter `prefix="/v1/runs"`)+ `_run_to_dict` helper + `_resolve_thread_meta` JOIN(N+1) | +120 |
| `services/control-plane/src/control_plane/app.py` | `include_router(build_runs_list_router())` 一行 | +1 |
| `services/control-plane/tests/test_runs_api.py` | `test_list_runs_home_tenant` / `_status_filter` / `_pagination` / `_cross_tenant_requires_system_admin` / `_cross_tenant_aggregate` / `_thread_meta_join` | +160 |
| `apps/admin-ui/src/api/runs.ts` | 加 `listRuns({ tenantScope, status, limit, offset })` 返回 `{ items, total, cross_tenant }`;`RunInfo` 类型增 `agent_name?` / `agent_version?` / `is_resume` / `created_at` / `updated_at` / `finished_at` | +50 |
| `apps/admin-ui/src/pages/RunsList.tsx` | **新文件** — 表格 + 筛选 + 分页 + cross-tenant banner | +220 |
| `apps/admin-ui/src/pages/__tests__/RunsList.test.tsx` | 5 单测(loading / empty / rows / status filter / cross-tenant) | +150 |
| `apps/admin-ui/src/router.tsx` | `<Route path="/runs">` 由 ComingSoon 换 RunsList | +/-2 |
| `apps/admin-ui/src/i18n/locales/{en,zh-CN}.ts` | 加 `runs_page.*`(13 keys)| +60 |

**PR7b — `agent_run.trace_id` 持久化**

| 文件 | 变更 | 行数估 |
|---|---|---|
| `packages/helix-persistence/migrations/versions/0037_agent_run_trace_id.py` | **新文件** — `ALTER TABLE agent_run ADD COLUMN trace_id varchar(32) NULL` + partial index `(trace_id) WHERE trace_id IS NOT NULL` | +35 |
| `packages/helix-persistence/src/helix_agent/persistence/models/agent_run.py` | 加 `trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=False)` | +2 |
| `packages/helix-runtime/src/helix_agent/runtime/runs/schemas.py` | `RunInfo` dataclass 加 `trace_id: str \| None = None` | +2 |
| `packages/helix-runtime/src/helix_agent/runtime/runs/store.py` | `RunStore.set_trace_id(*, run_id, tenant_id, trace_id)` 抽象 + 2 实现;`_row_to_dto` / `create` 透传 | +50 |
| `packages/helix-runtime/src/helix_agent/runtime/runs/manager.py` | `RunManager.create` 之后立刻调 `set_trace_id(record.run_id, tenant_id, current_trace_id_hex())` | +6 |
| `packages/helix-runtime/tests/test_run_store.py` | +`test_set_trace_id_writes_and_reads_back` / `_idempotent_overwrite` / `_tenant_isolation` | +50 |
| `services/control-plane/src/control_plane/api/runs.py` | `_run_to_dict` 加 `trace_id` 字段;两处响应序列化 | +/-6 |
| `services/control-plane/tests/test_runs_api.py` | +`test_get_run_includes_trace_id` / `test_list_runs_includes_trace_id` | +40 |
| `apps/admin-ui/src/api/runs.ts` | `RunInfo` 类型加 `trace_id?: string` | +1 |

**PR7c — `run_event` 持久层 + producer 接入**

| 文件 | 变更 | 行数估 |
|---|---|---|
| `packages/helix-persistence/migrations/versions/0038_run_event.py` | **新文件** — 新建 `run_event` 表(`run_id uuid` + `seq bigint` + `event_name text` + `data jsonb` + `created_at_ms bigint` + `created_at timestamptz`)+ `PRIMARY KEY (run_id, seq)` | +55 |
| `packages/helix-persistence/src/helix_agent/persistence/models/run_event.py` | **新文件** — SQLAlchemy `RunEventRow` model | +35 |
| `packages/helix-runtime/src/helix_agent/runtime/runs/event_store.py` | **新文件** — `RunEventStore` ABC + `InMemoryRunEventStore` + `SqlRunEventStore`;方法:`append(run_id, tenant_id, event_name, data, seq, created_at)` / `list(run_id, tenant_id, since_seq=0, limit=1000)` | +180 |
| `packages/helix-runtime/tests/test_run_event_store.py` | **新文件** — 8 测(append + list / pagination / tenant_isolation / since_seq / cross-tenant)| +150 |
| `services/orchestrator/src/orchestrator/sse.py` | `run_agent` worker 在 `bridge.publish` 处双写 `RunEventStore.append`;失败 → log warning + `helix_run_event_persist_errors_total.inc()`;不阻塞 | +30 |
| `services/orchestrator/src/orchestrator/runtime.py` | `AgentRuntime` 接 `run_event_store` 依赖注入 | +5 |
| `services/control-plane/src/control_plane/runtime.py` | 启动期组装 InMemory / SQL store 注入 AgentRuntime | +8 |
| `services/orchestrator/tests/test_sse_persistence.py` | **新文件** — 验证 `bridge.publish` 时 `RunEventStore.append` 被调 + store 错误不阻 SSE | +90 |
| Prometheus counter `helix_run_event_persist_errors_total` 已声明,只加 `.inc()` 调用 | — | +0 |

**PR7d — `GET .../events` endpoint + RunDetail Event stream panel**

| 文件 | 变更 | 行数估 |
|---|---|---|
| `services/control-plane/src/control_plane/api/runs.py` | 新 endpoint `GET /v1/sessions/{thread_id}/runs/{run_id}/events?since_seq=N`;若 run 在 active 状态 → `bridge.subscribe(last_event_id=...)`;若 terminal → `RunEventStore.list(...)`;响应 `text/event-stream`;复用 `format_sse` | +110 |
| `services/control-plane/tests/test_runs_api.py` | +`test_events_live_attach_running` / `_replay_terminal` / `_since_seq_cursor` / `_cross_tenant_rejected` / `_run_not_found` | +180 |
| `apps/admin-ui/src/api/runs.ts` | `streamRunEvents(threadId, runId, sinceSeq=0)` async generator,复用 `parseSseStream` | +35 |
| `apps/admin-ui/src/pages/run_detail/EventStreamPanel.tsx` | **新文件** — RunDetail Event stream panel(色彩分类同 Playground,自动滚屏);**默认折叠**(决议 E)— 顶 toolbar "Show events stream" 按钮,展开时才连 endpoint;状态用 localStorage 记 per-user 偏好 | +200 |
| `apps/admin-ui/src/pages/RunDetail.tsx` | wire EventStreamPanel | +/-10 |
| `apps/admin-ui/src/pages/__tests__/EventStreamPanel.test.tsx` | 5 测(默认折叠 / 展开后 live attach / replay / since_seq 继续 / 错误 alert)| +140 |
| `apps/admin-ui/src/i18n/locales/{en,zh-CN}.ts` | 加 `event_stream.*`(6 keys)| +20 |

**PR7e — Approval override_args Monaco + 状态轮询**

| 文件 | 变更 | 行数估 |
|---|---|---|
| `apps/admin-ui/src/pages/run_detail/ApprovalCard.tsx` | **新文件** — 抽出 RunDetail 里的 Approval Alert,加 Monaco 编辑 / "Edit arguments" toggle / Approve / Approve with edits / Reject 状态机 | +220 |
| `apps/admin-ui/src/pages/RunDetail.tsx` | 替换 inline approval Alert 为 `<ApprovalCard>`;加状态轮询 hook | +/-30 |
| `apps/admin-ui/src/hooks/useStatusPolling.ts` | **新文件** — 3s interval + visibilityState gate + terminal stop | +60 |
| `apps/admin-ui/src/pages/__tests__/ApprovalCard.test.tsx` | 6 单测(view-mode default / toggle 切 edit / parse 错误禁用 Approve / Approve / Approve with edits / Reject) | +180 |
| `apps/admin-ui/src/pages/__tests__/RunDetail.test.tsx` | **新文件** — polling 行为(running 时拉 / terminal 停 / hidden tab 暂停) | +120 |
| `apps/admin-ui/src/i18n/locales/{en,zh-CN}.ts` | 加 `approval_card.*`(8 keys)| +35 |

**PR7f — Trace 外链 + Approval pending badge + 体感打磨**

| 文件 | 变更 | 行数估 |
|---|---|---|
| `apps/admin-ui/src/pages/run_detail/TraceToolbar.tsx` | **新文件** — trace_id 显示(优先读 `run.trace_id`,fallback SSE metadata)+ 复制按钮 + (有 `VITE_LANGFUSE_BASE_URL` 时)Open in Langfuse 按钮 | +90 |
| `apps/admin-ui/src/config/env.ts` | 加 `LANGFUSE_BASE_URL` 读取 + 校验 | +15 |
| `apps/admin-ui/src/components/AppShell.tsx` | 主导航 Runs 项右侧加 `<ApprovalPendingBadge>` | +/-10 |
| `apps/admin-ui/src/components/ApprovalPendingBadge.tsx` | **新文件** — `listRuns({ status: "paused", limit: 1 })` 每 60s + 红点 + a11y label | +60 |
| `apps/admin-ui/src/pages/__tests__/TraceToolbar.test.tsx` | 3 单测(无 env / 有 env 显示按钮 / 复制 trace_id)| +90 |
| `apps/admin-ui/src/components/__tests__/ApprovalPendingBadge.test.tsx` | 3 单测(0 不显示 / N 显示 / 错误 swallow)| +80 |

### 6.5.9 状态机

#### RunsList 加载状态机

```
                ┌──────┐    apiTenantScope 变 / refresh 点击
                │ idle ├─────┐
                └──┬───┘     │
            on mount│       refresh()
                   v          │
              ┌─────────┐     │
              │ loading │<────┘
              └──┬──┬───┘
                 │  │
       success / │  │ \ error
                 v  │  v
            ┌──────┐│┌───────┐
            │ data ││ error │
            └──────┘└───────┘
```

#### RunDetail 轮询状态机

```
                          tab visible AND status ∈ {paused, running, pending}
                          ┌──────────────────────┐
                          │                      │
              ┌───────────v──────────┐           │
              │       polling        ├───────────┤  every 3 s: getRun()
              │   (interval active)  │
              └──────────┬───────────┘
                         │
       status ∈ terminal │     OR document hidden  OR component unmount
                         v
                  ┌──────────────┐
                  │     idle     │
                  │  (no timer)  │
                  └──────────────┘
```

#### Approval 编辑状态机

```
       ┌─────────┐  click "Edit"   ┌─────────────┐
       │  view   ├────────────────>│   editing   │
       │ (RO)    │                 │ (buffer ≠   │
       └────┬────┘                 │  pristine)  │
            │                      └──┬───┬──┬───┘
            │ click "Approve"         │   │  │
            │                         │   │  │ click "Cancel edit"
            │                         │   │  └──────────────┐
            │                         │   │                 │
            │     click "Approve with edits"                │
            │     OR "Reject"         │                     │
            v                         v                     v
       ┌─────────┐                ┌──────────┐         ┌──────┐
       │submitting├─ ok ─────────>│   done   │         │ view │
       └────┬─────┘                └──────────┘         └──────┘
            │                       (page reloads)
            │ error
            v
       ┌────────┐
       │  error │  alert; stay in editing(buffer 保留)
       └────────┘
```

JSON.parse 错误 = `editing` 子状态:Approve 按钮禁用 + 显示 syntax error 行号(Monaco 内置 marker)。

### 6.5.10 错误 / 边界场景矩阵

| 场景 | 后端响应 | 前端行为 |
|---|---|---|
| `?tenant_id=*` 但 caller 非 system_admin | 403 + audit `CROSS_TENANT_DENIED` | UI 不该让 caller 切到 "All tenants" — TenantScopeContext 已防;若手敲 URL,Alert 显示 "Forbidden — system admin required" |
| `?tenant_id=<other-uuid>` 但 caller 无 `allowed_tenants` | 403 + audit | 同上 — UI 不显示该 tenant 选项 |
| Thread 在 run 期间被删除 | `GET /v1/runs` 返回 `agent_name: null` | RunsList "Agent" 列显示 `—` 灰色;不阻断列表 |
| Approval 已超时,用户仍按 Approve | `POST .../resume` 409 + envelope `APPROVAL_TIMEOUT` | Alert + 自动 refresh detail(terminal 状态后停轮询) |
| Approval 在用户编辑 args 时被另一审批者处理 | `POST .../resume` 409 + envelope `APPROVAL_ALREADY_DECIDED` | Alert + refresh detail;buffer 不丢(显示出来供用户查看自己之前打算改成什么) |
| 后端 `agent_run` 行不存在(直接访问 `/runs/x/y`) | 404 | 已有 RunDetail Alert 路径 |
| `agent_name` JOIN 时 `thread_meta` 已删 | `agent_name: null` | RunsList "—";RunDetail 在 "Agent" 字段显示 "thread deleted" + 仍允许审批 |
| `VITE_LANGFUSE_BASE_URL` 未配 | — | TraceToolbar 只显示 trace_id + 复制 |
| `trace_id` 缺失(老数据,migration 之前的 run)| `run.trace_id = null` | TraceToolbar 显示 "trace_id 不可用(legacy run)" + 隐藏 Open in Langfuse 按钮 |
| `run_event` 持久化失败(DB 不可达) | bridge.publish 继续,event_store.append 抛 | log warning + `helix_run_event_persist_errors_total.inc()`;事件流可能漏帧但不阻 SSE |
| `GET .../events?since_seq=N` 但 `N > 最大 seq` | `RunEventStore.list` 返回空 | 直接 SSE `event: end` 不报错 |
| Active run 切到 terminal 当 events endpoint subscribe 中 | bridge 发 `END_SENTINEL` | endpoint 收到 end 后切到 replay store 拉剩余 seq(罕见;基本不会比 60s cleanup 慢) |
| `JSON.parse(buffer)` 失败 | — | Approve 禁用;Monaco 显示语法错;按 Cancel edit 可退出 |
| `override_args` 含未知字段 | 后端 J.8 resume 不做 schema 校验;tool 模板内部校验失败 → 工具 step 返回 `ToolError` → 该 run 状态变 `error` | terminal 状态;事后查 trace |
| 跨 tab 同时审批同一 run | 一个成功;另一个 409 | 错误流程同 "已被其他审批者处理" |
| Pending 红点 60s 轮询 cross-tenant 数据(home admin 但 OIDC 含 system_admin 角色) | API 按 caller 当前 scope(`home` / `*`)返回 | badge 始终基于当前 `apiTenantScope`;切换 tenant 时立即 refetch |

### 6.5.11 可观测 / 审计

`GET /v1/runs` 新端点遵循已有 list 端点惯例:

| 信号 | 何时 emit | 标签 / 维度 |
|---|---|---|
| audit `RUN_LIST_READ`(新 `AuditAction`)| 每次成功调用 | `actor_id`、`tenant_id`(home / `*` / target UUID)、`result=OK`、`details={"status": "...", "cross_tenant": true/false, "count": N}` |
| audit `CROSS_TENANT_DENIED` | `ensure_tenant_scope` 拒绝 | 同 Stream N 既有(`endpoint="GET /v1/runs"`)|
| Prometheus counter `helix_control_plane_run_list_total{tenant_scope}` | 每次调用 | `tenant_scope ∈ {home, cross, target}` |
| Prometheus histogram `helix_control_plane_run_list_seconds` | 每次调用 | (无标签 — 与现有列表端点 latency 系列对齐)|
| Prometheus counter `helix_run_event_persist_errors_total{backend}` | PR 3:`RunEventStore.append` 抛错 | `backend ∈ {memory, sql}` |
| Prometheus counter `helix_run_event_persist_total{backend, event_name}` | PR 3:`RunEventStore.append` 成功 | 关注热点 event_name 频次 |
| Prometheus histogram `helix_control_plane_run_events_endpoint_seconds{mode}` | PR 4:`GET .../events` 调用 | `mode ∈ {live, replay}` |
| 不加新 OTel span(沿用 ASGI 自动注入)| | |

前端无新增 telemetry。

### 6.5.12 i18n keys 全量

**`runs_page` (PR7a)**
```
page_title, subtitle, cross_tenant_banner, failed_to_load,
empty_home, empty_cross, column_run_id, column_status,
column_thread, column_agent, column_created, filter_status,
filter_status_all
```

**`approval_card` (PR7b)**
```
title, awaiting_human, edit_arguments, cancel_edit,
approve, approve_with_edits, reject, json_parse_error,
already_decided, timeout
```

**`event_stream` (PR7d)**
```
title, live_label, replay_label, empty, stream_failed, event_count
```

**`trace_toolbar` (PR7f)**
```
trace_id_label, copy_trace_id, open_in_langfuse, legacy_no_trace_id
```

**`approval_pending_badge` (PR7f)**
```
aria_label
```

### 6.5.13 测试计划

**PR7a — backend**
- `tests/test_run_store.py`(单元,InMemory + SQL 各一遍):
  - `list_for_tenant_empty`
  - `list_for_tenant_returns_only_matching_tenant`(2 tenants × 3 runs,验证只返回 caller 自己的)
  - `list_for_tenant_status_filter`(各 5 status,过滤一次只返该 status)
  - `list_for_tenant_pagination`(创建 250 行,limit=100 取 3 页 + 最后 1 页有 50 行)
  - `list_for_tenant_orders_newest_first`(created_at desc)
  - `list_all_tenants_returns_all_without_bypass_session`(InMemory)/ `_requires_bypass_rls`(SQL,验证 RLS 拒绝裸调用)
- `tests/test_runs_api.py`(集成):
  - `test_list_runs_home_tenant`(200 + cross_tenant=false)
  - `test_list_runs_status_filter`
  - `test_list_runs_pagination_offset_limit`
  - `test_list_runs_cross_tenant_requires_system_admin`(403 + audit)
  - `test_list_runs_cross_tenant_aggregate`(system_admin 看到 2 tenants 的 run)
  - `test_list_runs_includes_agent_name_via_thread_join`(thread_meta 已删时 agent_name=null)

**PR7a — frontend**
- `__tests__/RunsList.test.tsx`(5 单测):
  - `renders_loading_skeleton_first`
  - `renders_empty_state_when_no_runs`
  - `renders_rows_with_agent_name`
  - `status_filter_select_triggers_refetch`
  - `cross_tenant_banner_when_response_flag_true`

**PR7b — frontend**
- `__tests__/ApprovalCard.test.tsx`(6 单测,延 H.2 PR 1 Monaco mock 模式)
- `__tests__/RunDetail.test.tsx`(3 测 polling 行为,vi.useFakeTimers + `document.visibilityState` mock)
- `__tests__/useStatusPolling.test.ts`(单元 hook)

**PR7c — frontend**
- `__tests__/TraceToolbar.test.tsx`(3 测,vi.stubEnv 切 LANGFUSE_BASE_URL)
- `__tests__/ApprovalPendingBadge.test.tsx`(3 测;0 不显示 / N 显示 / SDK 报错 swallow)

**Storybook(决议 F)— 每 PR 加对应组件 story,延 H.1b `Login.stories.tsx` 模式**
- PR 7a:`RunsList.stories.tsx` — empty / loading / 5 rows / cross-tenant / error 5 个 story
- PR 7d:`EventStreamPanel.stories.tsx` — collapsed / expanded-empty / expanded-streaming / expanded-replay 4 个 story
- PR 7e:`ApprovalCard.stories.tsx` — view / editing / submitting / approved / error 5 个 story
- PR 7f:`TraceToolbar.stories.tsx`(env on/off 2 个)+ `ApprovalPendingBadge.stories.tsx`(0 / 5 / 99+ 3 个)

**E2E**(Playwright)
- PR 7a 落:`e2e/runs.spec.ts` — paste-login → /runs → 至少 1 row + axe 0 critical
- **不在 H.3 加 approval full E2E**(决议 F):需要 mock LLM 触发 approval,环境复杂度高;推 M0 dogfood 期间(canonical agent 跑起来后顺势加)。Storybook + 单元测覆盖等同 UI surface 验证

### 6.5.14 Mockup 引用

H.3 涉及的 3 张 mockup(已落地 H.1a PR 2,无需新增):
- [`mockups/04-run-trace.html`](../design/mockups/04-run-trace.html) — RunDetail 主视图,本 PR 增量按此规格补 Approval 编辑 + Trace 工具条 + 状态轮询
- **缺**:**RunsList 页 mockup**(`mockups/09-runs-list.html`)和 **ApprovalCard with override_args 编辑态 mockup**(可叠在 `04-run-trace.html` 同一文档加 § / 加新文件)。
  - **决策**:**先实现,不另出 mockup**。理由:本 PR 已用 ASCII layout(§ 6.5.3)+ 状态机(§ 6.5.9)锁定结构;`tokens.css` + Antd 5 + helix override 三层映射在 H.1a 已锁,RunsList 与 AgentsList 视觉同型(都是 cross-tenant aware 表格 + 顶 toolbar),无新视觉元素。
  - 但**这是债务**,要在 H.4 完工前补上 mockup 09(RunsList)和给 04 加 ApprovalCard 编辑态截图,作为基线引用;**已加入 H.4 收尾 PR 待办**。

### 6.5.15 Migration / Schema 影响

**2 个新 migration**(decision: 完整支持 SSE 回放 + trace_id 持久化):

**Migration 0038 — `run_event` 表**(PR 3,Mini-ADR H-7;依赖 0037 trace_id 已落)
```sql
CREATE TABLE run_event (
    run_id          uuid         NOT NULL REFERENCES agent_run(id) ON DELETE RESTRICT,
    seq             bigint       NOT NULL,
    event_name      text         NOT NULL,
    data            jsonb        NOT NULL,
    created_at_ms   bigint       NOT NULL,  -- millisecond epoch — feeds SSE id 重组
    created_at      timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, seq)
);
-- RLS via JOIN agent_run.tenant_id;list 查询模式 = (run_id, seq ASC)
-- 主键即覆盖,无额外索引。
```
- **FK = `ON DELETE RESTRICT`**(决议 C):锁定 M1 retention 灵活性 — agent_run 在 M1 走 archive-then-delete 流程,RESTRICT 强制 retention sweep 必须**先**清 `run_event` 再清 `agent_run`,M1 想把 events 归档到 ObjectStore 时不会被级联抢先删掉。M0 没 retention 时无影响。
- **`created_at_ms` 列**(决议 A):SSE wire id 字段格式 = `"{created_at_ms}-{seq}"`(与 `StreamBridge.memory.py:67-71` `_next_id` 同型)。replay endpoint emit 时按这个组装;**客户端 `parseSseStream` 不区分 live / replay**。`since_seq` 查询参数取 bigint(client 从最后一个 id 字符串截尾 split `-`)。
- 写量级:每 run ~20-60 行 × 500 bytes ≈ 10-30 KB
- 容量预算:1000 runs/day → 30 MB/day → 11 GB/年(M0 可接受;M1 retention sweep 30 天对齐 event_log)
- 索引选择:主键 `(run_id, seq)` 即 list 查询的最优 prefix;不加 `(created_at)` 索引(retention sweep 走 `JOIN agent_run ON finished_at < ?` 走 `agent_run.finished_at` 已有索引)

**Migration 0037 — `agent_run.trace_id`**(PR 2,Mini-ADR H-9.5;先于 0038 run_event)
```sql
ALTER TABLE agent_run ADD COLUMN trace_id varchar(32) NULL;
CREATE INDEX idx_agent_run_trace_id ON agent_run (trace_id) WHERE trace_id IS NOT NULL;
```
- 旧行 `trace_id IS NULL` — 前端兜底显示 "legacy run"
- partial index 跳过 NULL 避免膨胀 B-tree

**两个 migration 均 expand-only(向前;无 contract 步骤),与 STREAM-I-DESIGN expand-contract 纪律一致。**

- 既有 `agent_run` 表的 `(tenant_id, created_at DESC)` 索引覆盖 `list_for_tenant`;若发现慢,M1 加联合索引 `(tenant_id, status, created_at DESC)`。

### 6.5.16 Backwards Compat

- 既有 `GET /v1/sessions/{thread_id}/runs/{run_id}` / `.../resume` 路径不动。
- 既有 `RunStore.create` / `.set_status` / `.get` / `.list_by_thread` 签名不动。
- 既有 `apps/admin-ui/src/api/runs.ts` 的 `getRun` / `resumeRun` 接口不动;新增 `listRuns` 是纯增量。
- 既有 RunDetail 页用户路径不变;Approval 区从 Alert 包改 Card 组件是组件内重构,URL 不变。

### 6.5.17 安全 / 鉴权

| 端点 | authn | authz | 资源访问 |
|---|---|---|---|
| `GET /v1/runs?tenant_id=<UUID>` | Bearer JWT / API Key | caller 必须有 `allowed_tenants` 含 UUID | `list_for_tenant(tenant_id=UUID)` |
| `GET /v1/runs?tenant_id=*` | 同上 | caller 必须 `is_system_admin` | `bypass_rls_session()` + `list_all_tenants()` |
| `GET /v1/runs`(无 `tenant_id`)| 同上 | 自动用 caller 的 home tenant | `list_for_tenant(tenant_id=home)` |
| 前端 `VITE_LANGFUSE_BASE_URL` | env-only,非 secret(URL)| 外链通过 Langfuse 自己鉴权 | n/a |
| `override_args` 内嵌敏感数据 | — | 已在 audit `APPROVAL_DECIDED` 里有 `override_args_keys`(J.8 § 14.6);不记 values | 已有 |

无新增 secret;无新增 CSRF surface(都是 GET);RLS 策略不变。

### 6.5.18 H.3 收尾摘要(2026-05-26)

**PR 全部合入 main**:

| PR | 内容 | 合入 |
|----|------|------|
| H.3 PR 1 (#289) | `GET /v1/runs` 跨 thread 索引 + RunsList 页 + e2e | ✅ |
| H.3 PR 2 (#290) | `agent_run.trace_id` 持久化(migration 0037)| ✅ |
| H.3 PR 3 (#291) | `run_event` 持久层(migration 0038)+ producer 双写 | ✅ |
| H.3 PR 4 (#292) | `GET .../events` endpoint (live + replay) + EventStreamPanel | ✅ |
| H.3 PR 5 (#293) | Approval Monaco UX + `useStatusPolling` + 修了 `ResumeRunRequest` SDK 与后端契约 mismatch | ✅ |
| H.3 PR 6 (#294) | TraceToolbar + ApprovalPendingBadge + `config/env.ts` | ✅ |

**决议 A–F 实施核验**:
- **A** SSE wire id `"{created_at_ms}-{seq}"` — `run_event.created_at_ms bigint` 列 + replay endpoint emit 同型(`api/runs.py:_stream_replay`)。客户端 `parseSseStream` 不区分 live / replay ✅
- **B** `RunManager.create(trace_id=)` 显式参数 — `api/runs.py` trigger_run + resume_run 传 `current_trace_id_hex()`;scheduler 路径传 None ✅
- **C** `run_event` FK `ON DELETE RESTRICT` — migration 0038 落地 ✅
- **D** `MAX_LIST_LIMIT=500` 硬上限 + `X-Limit-Capped` header — `runs/store.py:_clamp_limit` ✅
- **E** EventStreamPanel 默认折叠 + localStorage `helix.runDetail.eventStream.expanded` 持久化 ✅
- **F** Storybook stories — RunsList / TraceToolbar / ApprovalPendingBadge 在 PR 链内已落;**EventStreamPanel + ApprovalCard 在收尾 PR 补齐**(原 PR 4 / PR 5 漏交);approval 完整 E2E 留 M0 dogfood ✅

**零技术债 6 条核验**(per `feedback_zero_tech_debt`):
1. **无 TODO/FIXME/XXX** — `git diff main~7..main` 涉及 51 文件,新增源码内无 TODO/FIXME/XXX 标记
2. **测试覆盖** — backend 新增 `test_run_event_store.py` / `test_sql_run_store.py` / `test_sse_persistence.py` / 扩展 `test_runs_api.py`;frontend 93/93 vitest 全过(从 H.2 完工时的 ~70 增长)+ Playwright `runs.spec.ts` 接入 axe
3. **文档同步** — 设计文档 v1.0→v1.7 共 8 版,实现 ↔ 设计映射齐;`ITERATION-PLAN.md` Stream H.3 行同步
4. **可观测齐全** — 新 metrics: `helix_runs_list_total` / `helix_run_event_persist_total` / `helix_run_event_persist_errors_total`;audit:`RUN_LIST_READ`(per § 6.5.11)
5. **CI 全绿** — #289–#294 每个 PR 11/11 检查通过(`gh pr checks` 验证)
6. **bug 不遗留** — PR 5 顺手修了 `ResumeRunRequest` SDK 字段 mismatch(`{approved}` → `{decision}`),这是 H.1b PR 3 旧 bug

**留给后续 stream 的待办**(债务不在 H.3,显式记账):
- **mockup 09 RunsList**(债务来源:§ 6.5.14)→ H.4 收尾 PR 补
- **`04-run-trace.html` ApprovalCard 编辑态截图**(债务来源:§ 6.5.14)→ H.4 收尾 PR 补
- **Approval full E2E**(决议 F)→ M0 dogfood 期间补
- **Trace 嵌入式时间线**(Mini-ADR H-8 deferred)→ H.4 范围

**capability gap**:无。H.3 范围内的"完整能力"(`/v1/runs` 跨 thread + trace 持久化 + SSE replay + Approval edit)全部落地,无"弱能力包装成设计选择"的 [[feedback_no_design_choice_disguise]] 风险。

---

## 6.6 H.4 详细设计

> 范式继承自 § 6.5(H.3)— PR0 设计先行,合入后才开实施 PR(PR1–PR8),PR9 收尾。覆盖 6 个治理子面 + 1 个新 backend Audit endpoint。复刻 § 6.5 的 18 子章节结构。

### 6.6.1 范围 & 出入

**In-scope**(7 个子面 + Stream H 整体收官):

| 子面 | UI 路径 | backend 状态 | UI 能力(完整 CRUD,per `[[feedback_complete_not_minimal]]`)|
|---|---|---|---|
| Curation+Eval | `/curation` | ✅ shipped J.12 | candidates list/filter/detail/promote/dismiss + eval_datasets full CRUD |
| Memory | `/memory` | ✅ shipped K.6 | list + kind filter + client-side search + edit content (Monaco) + delete |
| Skills | `/skills` 与 `/skills/:id` | ✅ shipped J.7 | list + cursor 加载 + create (Monaco YAML) + version list + ZIP import/export |
| Triggers | `/triggers` | ✅ shipped J.10 | list + 分类 Tab cron/webhook + enabled toggle + create (kind 切换) + webhook secret show-once + delete |
| Settings IAM | `/settings/service-accounts` + `/settings/role-bindings` | ✅ shipped C.3 | SA list + create + delete;RB list + create (platform_scope checkbox) + delete + cross-tenant 视图 |
| Settings Ops | `/settings/tenant-quotas` + `/settings/tenant-config` | ✅ shipped C.5/C.7 | per-tenant Quota list + create + delete;Config KV 视图 + Monaco JSON 编辑 + ETag 并发兜底 |
| Audit | `/audit` + `/audit/:id` | ⚠️ **backend 0 起点** | `GET /v1/audit` endpoint 新建(PR3)+ list + filter chips + cursor 加载 + 详情 drawer |

**Out-of-scope**(显式推迟):

- Audit CSV 导出 → M1(canonical agent 期间需求自然浮现再做)
- Skills marketplace / 公开仓库 → M1+
- Memory retention sweep UI(per-tenant)→ M1
- Trigger run 历史 list endpoint → M1(M0 走 `/v1/runs?trigger_id=<id>` 间接看)
- Webhook secret rotate endpoint → M1(M0 走删-重建)
- SLA monitoring / cost-threshold-auto-approval → M2

### 6.6.2 后端契约

**新 `GET /v1/audit`**(PR3 新建)

```http
GET /v1/audit?tenant_id=<UUID|*>&actor_id=<id>&action=<AuditAction>
            &resource_type=<resource_type>&resource_id=<id>&result=<SUCCESS|DENY|FAILURE>
            &from_ts=<ISO8601>&to_ts=<ISO8601>&cursor=<base64>&limit=<int,1-100>

200 OK
{
  "success": true,
  "data": {
    "items": [<AuditEntry>],
    "cursor": "<opaque-base64>",
    "has_more": true,
    "applied_scope": "home" | "cross_tenant" | "<UUID>"
  },
  "error": null
}
```

- **Cursor 分页**:opaque base64,客户端只能透传不能解析(per [[feedback_last_event_id_semantics]] 同理保守原则)
- **Cross-tenant**:`tenant_id=*` 需 `is_system_admin`;复用 Stream N `ensure_tenant_scope` + `applied_scope` + `bypass_rls_session` 三件套
- **自审计**:`GET /v1/audit` 调用自身写 `AuditAction.AUDIT_QUERY` 一行(actor_tenant_id = caller tenant;result = SUCCESS / FAILURE)
- **`actor_tenant_id` 必填**:用于 RLS 即使 cross-tenant 也保留 caller 的责任链
- **`from_ts < to_ts` 校验**:400 + clear error message
- **`AuditAction` enum 加 `AUDIT_QUERY = "audit:query"`**(单源 `packages/helix-protocol/src/helix_agent/protocol/audit.py`)
- **ResourceType Literal 加 `"audit"`**(必须双份改:`protocol/audit.py:146` + `control-plane/audit.py:111`,per [[project_audit_literal_drift]])

**`GET /v1/audit/{id}`**:单条详情(已包含 `redacted_keys` 字段)+ 自审计

**其余 6 个子面**链既有契约,本设计文档不复制 schema:
- Memory(K.6): `GET /v1/memory` / `PATCH /v1/memory/{id}` / `DELETE /v1/memory/{id}`
- Curation(J.12): `GET /v1/curation/candidates` + detail + promote + dismiss;`/v1/eval-datasets` full CRUD
- Skills(J.7): `/v1/skills` CRUD + versions + ZIP import/export
- Triggers(J.10): `/v1/triggers` CRUD + webhook ingest
- Settings IAM: `/v1/service_accounts` + `/v1/role_bindings`(含 `platform_scope`)
- Settings Ops: `/v1/tenants/{id}/quotas` + `/v1/tenants/{id}/config`

### 6.6.3 前端 IA — 6 子面

**Curation+Eval(2 个 sub-tab)**

```
/curation
├─ [Candidates Tab(默认)]
│  ├─ Filter chips: status(new/promoted/dismissed)/ signal kind / agent_name
│  ├─ Cross-tenant banner(scope="*"时)
│  ├─ Table: trajectory_summary / signal / agent / score / created_at / actions
│  └─ Row click → Drawer(trajectory 详情 + Promote button → Modal:选 eval_dataset)
└─ [Eval Datasets Tab]
   ├─ Toolbar: Create button + agent filter
   ├─ Table: name / agent / item_count / created_at / actions
   └─ Edit drawer: Monaco JSON(input + expected_output)+ Delete confirm
```

**Memory**

```
/memory
├─ Toolbar: Tenant scope banner + kind filter (Select) + 搜索框(client-side filter on content)
├─ Table: kind / content_preview / created_at / updated_at / actions
└─ Edit drawer: Monaco textarea (content,复用 ApprovalCard 范式)+ Save / Delete (Popconfirm)
```

**Skills(list + detail 双页)**

```
/skills
├─ Toolbar: Import ZIP button + Create drawer + status filter + category filter
├─ Table: name / status / category / version_count / updated_at
├─ "Load more" 按钮(cursor 分页)
└─ Row click → /skills/:id

/skills/:id
├─ Hero: name + status + category
├─ Metadata Card: description / created_by / created_at
└─ Versions Card: Table (version_n / created_at / Export ZIP button)
```

**Triggers**

```
/triggers
├─ Toolbar: Create button + Tabs (cron / webhook)
├─ Table: name / agent_name / enabled (toggle) / next_fire_at / last_fired_at / fired_count
├─ Row click → Detail drawer (config 只读 + Edit / Delete)
└─ Create drawer:
   ├─ kind 切换(Radio):cron / webhook
   ├─ cron → cron_expr 输入 + 校验
   └─ webhook → submit 后 secret show-once Card(复用 ApiKeyCreated 范式)
```

**Audit**

```
/audit
├─ Filter chips:
│  ├─ Actor (text)
│  ├─ Action (multi-Select,枚举 AuditAction 全部值)
│  ├─ ResourceType (multi-Select)
│  ├─ Result (radio: All / SUCCESS / DENY / FAILURE)
│  ├─ RangePicker (from_ts → to_ts)
│  └─ Tenant scope (TenantSwitcher)
├─ Timeline list: timestamp / actor / action / resource / result tag
├─ "Load more" 按钮(cursor;disabled 当 has_more=false)
└─ Row click → Drawer(完整 JSON viewer + redacted_keys 高亮)
```

**Settings IAM(2 个独立路由)**

```
/settings/service-accounts
├─ Toolbar: Create modal + scope banner
├─ Table: name / description / created_at / actions
└─ Create modal: name + description → 创建后跳 API Key sub-flow(可选)

/settings/role-bindings
├─ Toolbar: Create drawer + platform_scope filter + scope banner
├─ Table: subject_id / subject_type / role / platform_scope (badge) / created_at
└─ Create drawer:
   ├─ subject_type + subject_id
   ├─ role (Select)
   └─ platform_scope checkbox(仅 SYSTEM_ADMIN 可见,勾选时 confirm dialog)
```

**Settings Ops(2 个独立路由)**

```
/settings/tenant-quotas
├─ TenantSwitcher (system_admin 切看其它租户)
├─ Toolbar: Create drawer
├─ Table: resource / period / limit_int / used / actions
└─ Create drawer: resource (Select) + period + limit_int

/settings/tenant-config
├─ TenantSwitcher
├─ KV 视图(Monaco JSON 只读)
├─ Edit button → Monaco JSON 可编辑 + Save (确认 dialog) + ETag conflict banner
└─ pristine vs dirty 检测(复用 ApprovalCard 范式)
```

### 6.6.4 共享 pattern 复用清单

| Pattern | 来源 PR | H.4 复用位置 |
|---|---|---|
| `TenantScopeContext` / `TenantSwitcher` / `apiTenantScope` 线程到 SDK | H.1b PR 2a | 所有 list 页 |
| `useCallback(refresh) → listXXX({tenantScope}) → Antd Table + cross_tenant banner + empty state` | RunsList/AgentsList | 6 个 list 页全部 |
| `useStatusPolling` visibility-aware 3s 轮询 | H.3 PR 5 | Triggers enabled/fired 状态轮询 |
| ApprovalCard Monaco JSON 编辑 + pristine vs dirty 检测 | H.3 PR 5 | Memory edit / TenantConfig edit / EvalDataset input&expected edit |
| show-once Card (API Key) | H.1b PR 3 | Trigger webhook secret 创建后展示一次 |
| `ApprovalPendingBadge` 60s 轮询 + visibilitychange + soft-fail | H.3 PR 6 | (备选:Curation 待评审条数 badge,如果用户决策要做)|
| Cmd+K registration | H.1b PR 4 | 每子面注册 entity 跳转 |
| i18n 双语 zh-CN/en + 术语表保留不译 | H.1b PR 2a | 所有 7 个 namespace |
| Storybook stories 范式(decorators with mocked axios adapter)| H.3 各 PR | 所有新页面 ~33 stories |
| Stream N 跨租户三件套 `ensure_tenant_scope` + `applied_scope` + `bypass_rls_session` | Stream N | PR3 Audit endpoint |
| `_clamp_limit` / `MAX_LIST_LIMIT=500` + `X-Limit-Capped` header | H.3 PR 1 | PR3 Audit endpoint limit clamp |

### 6.6.5 已知不解决的耦合

- **Audit `actor_id` 不 JOIN 用户名**:M0 显示 raw subject_id(UUID 或 service-account ID);M1 加 user_index endpoint 时再 enrich
- **Skills cursor-only 无 total**:UI 显示"Load more"按钮而非分页器,与 AgentsList/RunsList 的 offset 风格不一致(Skills backend 仅支持 cursor)
- **Trigger webhook secret rotate endpoint 缺失**:M0 走删-重建路径;UI Show "Rotate" button 但调用是 delete+create 组合(显式 dialog 提示)
- **Audit `actor_tenant_id` 自审计开销**:每次 `GET /v1/audit` 自己写一条 audit_log,长期下产生 audit 自我繁殖;M1 加 `audit:query` 写入采样(每 N 条采 1 条)
- **Memory `EMBEDDER_UNCONFIGURED` 503**:某些部署没配 embedder,Memory 整面 503;UI 显示"backend not configured"清晰错误,不静默
- **TenantConfig 单租户 only**:无跨租户 list,system_admin 必须用 TenantSwitcher 一个一个看(批量编辑推 M1)

### 6.6.6 PR 拆分

详见 § 6 PR 链表(H.4 PR0–PR9)。

### 6.6.7 验收

每 PR 满足 `[[feedback_zero_tech_debt]]` 6 条:
1. 无 TODO/FIXME/XXX 标记
2. 测试覆盖:backend ≥ 8 测(PR3)+ frontend 5–8 单测 × 7 实施 PR + ≥ 4 Storybook stories
3. 文档同步:设计文档实施期变更落 § 6.6.x changelog;ITERATION-PLAN 行同步
4. 可观测齐全:PR3 新增 `helix_control_plane_audit_query_total` + `_seconds`;`AUDIT_QUERY` audit
5. CI 全绿:11/11 检查
6. bug 不遗留:实施期发现的非本 PR scope bug 显式立 issue 或同 PR 修复

**Stream H 整体验收**(PR9 完成时):
- 6 个治理子面 ComingSoon → 真页面
- system_admin 跨租户端到端可见(登录默认 "All tenants" → 切单 tenant → 切回 → 所有 audit 留痕)
- 性能:首屏 < 2s,Lighthouse Performance ≥ 90
- a11y axe 0 critical

### 6.6.8 文件级影响图

**PR0**(设计):
- `docs/streams/STREAM-H-DESIGN.md` § 6.6 新增 18 子章节
- `docs/design/mockups/{09-runs-list,10-skills,11-triggers,12-audit,13-settings-iam,14-settings-ops}.html` 新建
- `docs/design/mockups/README.md` 更新索引
- `docs/ITERATION-PLAN.md` Stream H.4 行展开

**PR1**(Curation+Eval):
- `apps/admin-ui/src/api/curation.ts` 补 6 个 mutation
- `apps/admin-ui/src/pages/CurationReview.tsx` 新建
- `apps/admin-ui/src/pages/EvalDatasets.tsx` 新建
- `apps/admin-ui/src/pages/__tests__/{CurationReview,EvalDatasets}.test.tsx` + stories
- `apps/admin-ui/src/i18n/locales/{en,zh-CN}.ts` 加 `curation.*` + `eval_datasets.*`
- `apps/admin-ui/src/router.tsx` `/curation` 解 ComingSoon

**PR2**(Memory):
- `apps/admin-ui/src/api/memory.ts` 补 update/delete
- `apps/admin-ui/src/pages/MemoryAdmin.tsx` 新建 + test + story
- i18n + router

**PR3**(Audit backend):
- `services/control-plane/src/control_plane/api/audit.py` 新建(`build_audit_router()`)
- `services/control-plane/src/control_plane/app.py` `include_router`
- `services/control-plane/src/control_plane/audit.py` ResourceType Literal 加 `"audit"`
- `services/control-plane/src/control_plane/api/__init__.py` 导出
- `packages/helix-protocol/src/helix_agent/protocol/audit.py` `AuditAction.AUDIT_QUERY` + ResourceType Literal 加 `"audit"`
- `services/control-plane/tests/test_audit_api.py` 新建(~10 测)

**PR4**(Audit UI):
- `apps/admin-ui/src/api/audit.ts` 新建
- `apps/admin-ui/src/pages/AuditLog.tsx` + test + story
- i18n + router(`/audit`)
- `apps/admin-ui/src/components/Sidebar.tsx` 加 Audit 主导航项

**PR5**(Skills):
- `apps/admin-ui/src/api/skills.ts` 补 7 个 mutation(含 multipart import + blob export)
- `apps/admin-ui/src/pages/{SkillsList,SkillDetail}.tsx` 新建
- test + stories + i18n + router(`/skills/:id`)

**PR6**(Triggers):
- `apps/admin-ui/src/api/triggers.ts` 补 4 个 mutation
- `apps/admin-ui/src/pages/TriggersList.tsx` 新建 + `triggers_list/{CreateTriggerDrawer,WebhookSecretShowOnce}.tsx`
- test + stories + i18n + router

**PR7**(Settings IAM):
- `apps/admin-ui/src/api/{service_accounts,role_bindings}.ts` 新建
- `apps/admin-ui/src/pages/{SettingsServiceAccounts,SettingsRoleBindings}.tsx` 新建
- test + stories + i18n + router(`/settings/service-accounts`,`/settings/role-bindings`)

**PR8**(Settings Ops):
- `apps/admin-ui/src/api/{tenant_quotas,tenant_config}.ts` 新建
- `apps/admin-ui/src/pages/{SettingsTenantQuotas,SettingsTenantConfig}.tsx` 新建
- test + stories + i18n + router

**PR9**(收尾):
- `docs/streams/STREAM-H-DESIGN.md` § 6.6.18 + § 7 Stream H 整体验收 ✅
- `docs/design/mockups/04-run-trace.html` 加 ApprovalCard 编辑态 section(H.3 留账)
- `docs/ITERATION-PLAN.md` Stream H ✅
- `apps/admin-ui/e2e/governance.spec.ts` 新建(7 happy-path 冒烟)

### 6.6.9 状态机

**AuditList cursor 加载**(PR4):

```
[idle] ──(初始 fetch)──→ [loading]
   ↑                         │
   │     ┌───────────────────┴───────────────────┐
   │     ▼                                       ▼
   │  [data + has_more=true]              [data + has_more=false]
   │     │                                       │
   │     │  Load more click                      │
   │     ▼                                       │
   │  [loadingMore]                              │
   │     │                                       │
   │     ▼                                       ▼
   └── [data appended] ────────────────→ [end-of-stream UI]

   Filter change → [idle] (重置 cursor)
   Error → [error banner + retry button]
```

**Skill ZIP import**(PR5):

```
[idle] ──(用户选文件)──→ [uploading]
                          │ (XHR multipart progress)
                          ▼
                       [validating] ──→ 后端 5xx → [error banner]
                          │
                          ▼
                       [success] ──→ 表格 prepend 新行 + 1s 后回到 [idle]
```

**Trigger create webhook secret show-once**(PR6):

```
[form] ──(submit)──→ [submitting]
                       │
                       ▼
                    [secret-shown-once Card]
                       │
                       ├─(用户 copy)──→ [copied toast]
                       │
                       └─(用户关闭 Card)──→ [done] (secret 永久不可再访问)
```

### 6.6.10 错误 / 边界场景矩阵

| 场景 | 触发 | UI 表现 |
|---|---|---|
| Memory `EMBEDDER_UNCONFIGURED` 503 | embedder 未配 | 整面 Alert "Memory backend not configured" + 不显示空表 |
| Trigger cron expr 无效 | 用户输入错 cron | 表单 inline 校验红字 + Submit 禁用 |
| Audit cursor 过期 (410) | 长时间停留 | "Cursor expired, refresh to start over" + 自动重置 cursor |
| Skill ZIP MIME mismatch | 上传非 .skill ZIP | 表单红字 + Submit 禁用;不发 XHR |
| Skill ZIP size > 10MB | 大文件上传 | 表单红字 + 提示 size limit;不发 XHR |
| RoleBinding 试图给自己加 SYSTEM_ADMIN | system_admin self-target | Frontend 确认 dialog;backend 接受(已有 platform_scope 双重保护即足够,见 PR0 spike 4) |
| TenantConfig 并发 PUT 412(ETag mismatch) | 两个 admin 同时编辑 | "Config changed by another user. Reload to see latest" + Reload button |
| Webhook secret 创建后未保存 | 用户关 Card 没 copy | 二次确认 dialog "Secret will never be shown again. Continue?" |
| Curation candidate trajectory ObjectStore 404 | trajectory 文件丢 | Drawer 显示 "Trajectory artifact missing" + 仍允许 promote/dismiss |
| Eval dataset JSON 解析失败 | 用户填非法 JSON | Monaco 红字 + Save 禁用 |
| Trigger fire history endpoint 缺失 | 用户想看历史 | 跳 `/runs?trigger_id=<id>` + 顶部提示 "Trigger fire history via Runs page (M0 limitation)" |
| Cross-tenant view 但 caller 非 system_admin | 普通 admin 误试 | 403 + redirect to home scope + toast "Cross-tenant view requires system_admin" |

### 6.6.11 可观测 / 审计

**新 Prometheus metrics**(PR3,通过 `helix_agent.common.observability.helix_counter` / `helix_histogram` helper,**不直接 import `prometheus_client`**):
- `helix_control_plane_audit_query_total{tenant_scope, result}` — counter
- `helix_control_plane_audit_query_seconds{tenant_scope}` — histogram

**新 AuditAction**(PR3):
- `AUDIT_QUERY = "audit:query"` — 自审计

**新 ResourceType**(PR3,**双份改**):
- `"audit"` — audit entry 作为资源被查询

**复用既有 audit**:Memory `MEMORY_UPDATE` / `MEMORY_FORGET`,Curation `CURATION_PROMOTE` / `CURATION_DISMISS`,Skills `SKILL_CREATE` / `SKILL_VERSION_CREATE` / `SKILL_STATUS_CHANGE`,Triggers `TRIGGER_CREATE` / `TRIGGER_UPDATE` / `TRIGGER_DELETE` / `TRIGGER_FIRE`,Settings `API_KEY_*` / `SERVICE_ACCOUNT_*` / `ROLE_BINDING_*` / `QUOTA_CONFIG_*` / `TENANT_CONFIG_READ` / `TENANT_CONFIG_WRITE`

### 6.6.12 i18n keys 全量

7 个 namespace × ~15-24 keys ≈ 130+ keys 总量:

- `curation.*` ~14 keys(filter/列名/promote/dismiss/empty/error)
- `eval_datasets.*` ~10 keys(列名/create/edit/delete)
- `memory.*` ~14 keys(filter/search/edit/delete/empty)
- `audit.*` ~18 keys(filter chips/load more/timeline/detail drawer/cursor-expired)
- `skills.*` ~20 keys(list/import/export/versions/status/category)
- `triggers.*` ~22 keys(cron/webhook/secret-show-once/enabled/fire-count)
- `settings_iam.*` ~14 keys(SA/RB/platform_scope/self-elevation-confirm)
- `settings_ops.*` ~10 keys(quotas/config/ETag-conflict)

术语表保留不译:Audit / AuditEntry / Curation / Eval / Memory / Skill / Trigger / Cron / Webhook / Service Account / Role Binding / Tenant Config / Tenant Quota / Cursor。

### 6.6.13 测试计划

**Backend**(PR3):
- `services/control-plane/tests/test_audit_api.py` ~10 测:
  - happy path:home scope list + cursor 加载
  - cross-tenant `tenant_id=*` 需 system_admin
  - 普通 admin 试 cross-tenant → 403
  - filter chips(action / resource_type / result / from_ts/to_ts)
  - `from_ts > to_ts` → 400
  - cursor 过期 → 410
  - 自审计:每次 query 写 `AUDIT_QUERY` 一行
  - `actor_tenant_id` 必填
  - cross-tenant 时 `applied_scope: "cross_tenant"` 在响应
  - limit clamp(>500 时 `X-Limit-Capped: true` header)

**Frontend**:
- 每实施 PR(1/2/4/5/6/7/8)5–8 单测,共 ~45 单测
- 涵盖:happy / empty / error / loading / cross-tenant scope 切换 / show-once (PR6) / Monaco edit pristine vs dirty (PR2/4/8)
- Storybook stories:每页 ≥ 4 stories (default / loading / empty / error),Triggers 加 cron vs webhook 区分,Settings IAM 加 system_admin vs tenant_admin 视图区分;共 ~33 stories

**E2E**(PR9):
- `apps/admin-ui/e2e/governance.spec.ts` 7 happy-path:
  - 登录 → /curation 看到列表
  - /memory list + edit drawer 打开
  - /skills + import button visible
  - /triggers + cron tab + webhook tab
  - /audit list + filter chip 至少 1 个交互
  - /settings/service-accounts list
  - /settings/tenant-config Edit 打开 Monaco
- 每 spec axe 0 critical

### 6.6.14 Mockup 引用

PR0 新增 mockup(5+ 张):

| # | 路径 | 用途 |
|---|---|---|
| 09 | `docs/design/mockups/09-runs-list.html` | 补 H.3 留账;RunsList 视觉基准(已实施,补 mockup 作为参考)|
| 10 | `docs/design/mockups/10-skills.html` | Skills list + Detail + Version + Import ZIP + Create drawer Monaco YAML |
| 11 | `docs/design/mockups/11-triggers.html` | Triggers list + cron/webhook Tab + Create drawer(双 kind 切换)+ Webhook secret show-once Card |
| 12 | `docs/design/mockups/12-audit.html` | Audit timeline + filter chips + Entry detail Drawer + cursor "Load more" |
| 13 | `docs/design/mockups/13-settings-iam.html` | Settings SA + RB list + Create drawer(platform_scope checkbox + 确认 dialog)|
| 14 | `docs/design/mockups/14-settings-ops.html` | Tenant Quotas table + Tenant Config Monaco JSON + ETag conflict banner |

复用既有 mockup:`05-curation-review.html`(PR1 Curation 直接用)+ `06-memory-admin.html`(PR2 Memory 直接用)+ `07-settings-api-keys.html`(已实施)。

PR9 补 `04-run-trace.html` 加 ApprovalCard 编辑态 section。

### 6.6.15 Migration / Schema 影响

**无 DDL 改动**:
- `audit_log` 表(migration 0008)已存在
- 既有 RLS 策略覆盖 audit 跨租户访问
- 仅新增 `AuditAction.AUDIT_QUERY` enum 值(Python 层,无 DDL)

### 6.6.16 Backwards Compat

- 既有 `listMemories` / `listSkills` / `listTriggers` / `listCandidates` / `listEvalDatasets` SDK 签名不变(仅补 mutation)
- 既有 `AuditLogger.query()` 接口不变(PR3 直接用)
- 既有 `audit.AuditAction` enum 仅 append `AUDIT_QUERY`,不改 existing 值
- 既有 router prefix(`/v1/memory` / `/v1/skills` / `/v1/triggers` / `/v1/curation` / `/v1/eval-datasets` / `/v1/service_accounts` / `/v1/role_bindings` / `/v1/tenants/{id}/*`)不动;`/v1/audit` 是新前缀

### 6.6.17 安全 / 鉴权

**Audit RBAC matrix**(PR3):

| 端点 | authn | authz | 资源访问 |
|---|---|---|---|
| `GET /v1/audit?tenant_id=<UUID>` | Bearer JWT / API Key | caller 必须 `allowed_tenants` 含 UUID | `AuditLogger.query(tenant_id=UUID)` |
| `GET /v1/audit?tenant_id=*` | 同上 | caller 必须 `is_system_admin` | `bypass_rls_session()` + `query()` |
| `GET /v1/audit`(无 tenant_id)| 同上 | 自动用 caller home tenant | `query(tenant_id=home)` |
| `GET /v1/audit/{id}` | 同上 | 走 RLS;cross-tenant 同上 | `AuditLogger.get(id)` |

**其余安全考量**:
- **Skills ZIP MIME + size limit**:复用既有 `_skill_zip.py`(J.7 已落),前端 SDK `importSkillZip` 用 multipart/form-data,backend 校验 MIME + size + ZIP slip
- **Trigger webhook secret 只在 create response 出现一次**:已在 backend 实现(triggers.py:86-87, 228;PR0 spike 1 确认)
- **RB platform_scope 写需 caller 为 SYSTEM_ADMIN**:已在 backend 实现(role_bindings.py:66;PR0 spike 4 确认);self-target 路径间接被 platform_scope ↔ SYSTEM_ADMIN ↔ caller `is_system_admin` 三重链堵死
- **TenantConfig ETag 并发兜底**:M0 用 If-Match;无 ETag 时 PUT 接受最后写者赢(M1 加 ETag header 才严格)
- 无新增 secret;无新增 CSRF surface(GET);所有 mutation 已被现有 AuthMiddleware 覆盖

### 6.6.18 H.4 收尾摘要(2026-05-26)

**PR 全部合入 main**:

| PR | 内容 | 合入 |
|----|------|------|
| H.4 PR 0 (#296) | 设计基线 § 6.6 + 6 mockup(09–14)+ 4 spike resolve | ✅ |
| H.4 PR 1 (#297) | Curation+Eval UI(Tabs / Promote Modal / Monaco JSON Edit Drawer)+ latent bug 修 1 | ✅ |
| H.4 PR 2 (#298) | Memory CRUD(ApprovalCard Monaco 复用 + client-side 搜索)+ patchJson helper | ✅ |
| H.4 PR 3 (#299) | Audit backend endpoint(`GET /v1/audit` + detail + `AuditLogger.get_by_id`)+ 11 e2e 测 | ✅ |
| H.4 PR 4 (#300) | Audit UI(Timeline + Filter Chips + Cursor Load more + Drawer JSON) | ✅ |
| H.4 PR 5 (#301) | Skills(List + Detail + ZIP import multipart + ZIP export blob)+ latent bug 修 2 | ✅ |
| H.4 PR 6 (#302) | Triggers(cron/webhook Tabs + Show-once Secret Drawer)+ latent bug 修 3 | ✅ |
| H.4 PR 7 (#303) | Settings IAM(SA + RB,platform_scope 三重防御 + type-to-confirm) | ✅ |
| H.4 PR 8 (#304) | Settings Ops(Quotas + Config,per-tenant + Monaco JSON Edit + last-writer-wins) | ✅ |

**PR0 4 个 spike 实施期核验**:
- **Spike 1**(Trigger webhook secret 回包 schema 已有)— ✅ PR 6 直接读 `webhook_secret`,backend 无改动
- **Spike 2**(`ResourceType` Literal 双份漂移 vs `AUDIT_QUERY`)— ✅ PR 3 spike 简化 — 既有 `AUDIT_READ` enum + 双 Literal 都已含 `"audit"`,**两条原计划改动取消**
- **Spike 3**(audit_logger fixture parity)— ✅ PR 3 遵循"每测试自建 `build_default_audit_logger`"pattern,不需改 conftest
- **Spike 4**(RoleBinding self-elevation 防御)— ✅ PR 7 frontend 加 type-to-confirm `CONFIRM PLATFORM ROLE` 为第三道防线(backend DTO + caller `is_system_admin` 已是前两道)

**实施期发现的 3 个 latent SDK bug**(同型,首次 H.4 PR 1 暴露):
- **PR 1** 修了 `listCandidates` / `listEvalDatasets` — backend 返回 raw,SDK 误用 `getJson` 期望 envelope
- **PR 5** 修了 `listSkills` 同型 bug + 顺手修 `current_version → latest_version` 字段名 + 删 `total` 字段(backend cursor 不是 offset)
- **PR 6** 修了 `listTriggers` 同型 bug
- **根因**:H.1b PR 3 (#280) 写 SDK skeleton 时 mock 了 enveloped 响应,test 通过但 backend 实际 raw,production 第一次真正调用就会在 `unwrap()` 处 500
- **教训**:见新 memory [[feedback_envelope_vs_raw_contract_check]]

**零技术债 6 条核验**(per `[[feedback_zero_tech_debt]]`):
1. **无 TODO/FIXME/XXX** — `git diff main~9..main` 9 个 PR 共 ~+9,500 行,无未来留账标记
2. **测试覆盖** — backend 11 e2e (audit_api);frontend 130/130(was 99 起步,加 31 新单测)+ ~30 Storybook stories;Playwright `governance.spec.ts` 7 happy-path(本 PR)
3. **文档同步** — 设计文档 v1.7→v1.9,实施 ↔ 设计完全映射,18 子章节全部 ready
4. **可观测齐全** — PR 3 加 2 Prometheus(`helix_control_plane_audit_query_total` + `_seconds`);所有 mutation 经既有 audit_emit(MEMORY_UPDATE / CURATION_PROMOTE / SKILL_CREATE / TRIGGER_CREATE / SERVICE_ACCOUNT_CREATE / ROLE_BINDING_CREATE / QUOTA_CONFIG_WRITE / TENANT_CONFIG_WRITE)
5. **CI 全绿** — 每 PR 11/11(`gh pr checks` 验证;PR 4 + PR 8 各 rerun 一次 flaky `test_react_graph_parallel` timing test)
6. **bug 不遗留** — 3 个 latent SDK bug 在 H.4 期间全部修了;`SkillRecord` 字段名 mismatch 一并修

**留给后续 stream 的债务**(显式记账):
- **Skills ZIP Marketplace**(公开 / cross-tenant 共享)→ M1
- **Memory retention sweep UI**(per-tenant)→ M1
- **Audit CSV 导出**(operator 需求出现时)→ M1
- **Tenant Config ETag 并发控制**(M0 last-writer-wins → M1 加 If-Match → 412)→ M1
- **Trigger webhook secret rotate endpoint**(M0 走删-重建)→ M1
- **Trace 嵌入式时间线**(Mini-ADR H-8 外链只是第一步)→ M1+(Tempo / Grafana 嵌入)
- **Trigger fire 历史 list endpoint** → M1(M0 走 `/v1/runs?trigger_id=` 间接)
- **Audit `actor_id` JOIN 用户名**(显示 raw subject_id)→ M1
- **Approval 完整 E2E**(H.3 留账)→ M0 dogfood
- **mockup 04 ApprovalCard 编辑态截图**(H.3 留账)→ 本 PR 补

**capability gap**:无。H.4 7 个治理子面 + Audit endpoint 全部完整 CRUD 落地,per `[[feedback_complete_not_minimal]]` 没有任何"弱能力包装成设计选择"的 [[feedback_no_design_choice_disguise]] 风险。

**Stream H 整体收官**(per § 7 整体验收清单):
- ✅ 6 个治理子面 + 7 个 Settings 子页 ComingSoon → 真页面
- ✅ system_admin 跨租户视角端到端可见(登录默认 "All tenants" → 切到具体 tenant → 切回 → 所有 audit 留痕)
- ✅ a11y axe 0 critical(Playwright + axe 在 CI 每 PR 跑)
- ✅ 性能:首屏 < 2s,Lighthouse Performance ≥ 90(H.1a a11y 自检 baseline + H.4 持续监控)
- ✅ `[[feedback_zero_tech_debt]]` 6 条 H.1 → H.4 全部满足
- ✅ ITERATION-PLAN Stream H 全部子项 ✅,Stream H 收官归档
- ✅ 接入的 B/E/J/K 能力面在 UI 上端到端可见(Agent CRUD / Sessions+Runs SSE / Memory CRUD / Curation+Eval / Skills / Triggers / API Keys / SA / RB / Tenant Quotas / Tenant Config / Audit)

---

## 6.7 H.6 详细设计 — AgentDetail 4 tab 真实现(Runs / Skills / Triggers / Memory)

> 2026-06-12 设计先行。H.6 是 Stream H 收官后回填的前端债(对账 #320 后列入 ITERATION-PLAN § Stream H 前端债 H.6–H.9)。
> 目标:AgentDetail 占位 tab(`AgentDetail.tsx:159` 渲染 `tab_coming_soon`)换成真实现,agent-中心 IA 兑现
> ([philosophy.md § 4](../design/admin-ui-philosophy.md))——看一个 agent 的 detail 页即可回答"它最近跑了什么 /
> 它创作了哪些 skill / 谁在触发它 / 它记住了什么"。

### 6.7.1 现状取证(2026-06-12,file:line)

| 事实 | 位置 | 影响 |
|---|---|---|
| 4 tab 已在 Tabs items 声明,占位渲染 | `AgentDetail.tsx:148-151` / `:159-165` | 前端只缺 tab 组件本体 |
| 4 个 list SDK 都已存在 | `api/runs.ts:127` / `api/skills.ts:137` / `api/triggers.ts:54` / `api/memory.ts:45` | SDK 只缺过滤参数 |
| `GET /v1/runs` **envelope** 响应,无 agent 过滤;display 层 N+1 JOIN thread_meta 取 agent_name/version(注释明示 "M0 = N+1; M1 = SQL JOIN") | `runs.py:921-1020` | 过滤参数是 H.6 前置 |
| `agent_run` 表**无** agent 列;归属在 `thread_meta.agent_name/agent_version` | `models/agent_run.py:29-43` / `models/thread_meta.py:30-31` | runs 过滤必须经 thread 维度 |
| `RunStore.list_for_tenant/list_all_tenants` 无 thread/agent 过滤 | `runtime/runs/store.py:77/92` | 要加 `thread_ids` 过滤 |
| `ThreadMetaStore.list_by_tenant` 有 status/user_id 过滤,无 agent 过滤 | `thread_meta/base.py:67` | 要加 agent_name/version 过滤 |
| skill 表已有 `created_by_agent_name`(Stream SE agent-authored) | `models/skill.py:81` | Skills tab 语义现成,store/端点缺过滤 |
| `GET /v1/skills` **raw** 响应(items+next_cursor+platform_items),store `list_skills` 有 created_by_user_id 无 created_by_agent_name | `skills.py:835` / `skill/base.py:145` | 加对称过滤参数 |
| `GET /v1/triggers` **raw** 响应,store/端点已有 `agent_name`,无 `agent_version` | `triggers.py:323` / `trigger/base.py:47` | 补 version 过滤(trigger 表本就有 agent_version 列) |
| `GET /v1/memory` **envelope** 响应,per-user scope(kind/limit),无 agent 维度 | `memory.py:166` | 见 Mini-ADR H-13 |
| 参照范式现成 | `RunsList.tsx` / `SkillsList.tsx` / `TriggersList.tsx` / `MemoryAdmin.tsx` | 4 tab 各有最近模板 |

envelope-vs-raw 对账([memory:envelope-vs-raw-contract-check]):runs/memory=envelope(SDK `getJson`),skills/triggers=raw(SDK `apiClient.get`)——现 SDK 已逐个对上,本次只加参数不动形态。

### 6.7.2 后端过滤(PR1)

**① Runs by agent — thread_ids 两段式(Mini-ADR H-10)**
- `ThreadMetaStore.list_by_tenant` / `list_all_tenants` 加 `agent_name: str | None = None` + `agent_version: str | None = None`(SQL where + in-memory filter;默认 None 全兼容)。
- `RunStore.list_for_tenant` / `list_all_tenants` 加 `thread_ids: Collection[UUID] | None = None`(SQL `WHERE thread_id IN`、in-memory filter——两 impl 语义精确同义,protocol 不破)。
- API `GET /v1/runs?agent_name=&agent_version=`:有 agent_name 时先 `threads.list_by_tenant(agent_name=…, limit=MAX_LIST_LIMIT)` 取 thread_ids(newest-first,cap 500),空 → 空页;非空 → `runs.list_*(thread_ids=…)`。response `data` 加 `thread_window_capped: bool`(thread 数撞 cap 时 true——诚实信号,老客户端忽略新字段无害)。`agent_version` 单给(无 agent_name)= 422。
- display 层 N+1 JOIN 不动(本就有);**不做 SQL JOIN**(见 H-10 理由)。

**② Skills by agent(Mini-ADR H-11)**
- `SkillStore.list_skills` 加 `created_by_agent_name: str | None = None`(SQL where + memory filter;`list_skills_all_tenants` 同加)。
- 端点 `GET /v1/skills?created_by_agent_name=`(与既有 `created_by_user_id` 对称)。

**③ Triggers 补 version**
- `TriggerStore.list_by_tenant` / `list_all_tenants` 加 `agent_version: str | None = None`;端点 `GET /v1/triggers?agent_version=`(单给无 agent_name = 422,与 runs 同规)。

**④ Memory 不改**(Mini-ADR H-13)。

协议签名 sweep:三个 store protocol 加 default kwargs——全仓 grep doubles(**含 tools/eval**,[memory:protocol-sweep-includes-tools-eval]);加默认值参数 doubles 兼容,但 sweep 仍跑确认无显式重写旧签名处。

### 6.7.3 前端 4 tab(PR2)

全部进 `apps/admin-ui/src/pages/agent_detail/`,AgentDetail.tsx 去占位接线(`:159` 的 fallback Empty 保留给未知 tab):

| Tab | 组件 | 数据 | 模板 | 备注 |
|---|---|---|---|---|
| Runs | `RunsTab.tsx` | `listRuns({agentName, agentVersion, status, limit, offset})` | RunsList | status 过滤 + 分页;行点击 → `/runs/:runId`;`thread_window_capped` 显 Alert 条 |
| Skills | `SkillsTab.tsx` | `listSkills({createdByAgentName})` | SkillsList | agent-authored 语义副标题;行点击 → skill detail;空态文案"该 agent 尚未创作 skill" |
| Triggers | `TriggersTab.tsx` | `listTriggers({agentName, agentVersion})` | TriggersList | enabled Badge;行点击 → trigger detail/编辑沿用 TriggersList 既有交互 |
| Memory | `MemoryTab.tsx` | `listMemories({kind, limit})` | MemoryAdmin | kind 过滤;**顶部 Alert 明示 user-scope 语义**(H-13);只读列表,治理操作留 MemoryAdmin 全局页 |

SDK 增参:`ListRunsParams + agentName/agentVersion`、`ListSkillsParams + createdByAgentName`、`ListTriggersParams + agentVersion`(`RunList` 类型 + `thread_window_capped?: boolean`)。

接线点(SE-8 清单适用子集):i18n zh-CN+en 双语(4 tab 各 ~8 key:列头/空态/过滤/cap 提示)、Storybook 4 stories、vitest 组件测(渲染+过滤调 SDK 参数断言+空态)、Playwright(`agent-detail-tabs.spec.ts`:4 tab 切换渲染冒烟)。无 Sidebar/CommandPalette 变更(tab 非顶级路由)。

### 6.7.4 Mini-ADR

- **H-10 runs agent 过滤 = thread_ids 两段式,不做 SQL JOIN**:`agent_run` 无 agent 列,归属在 thread_meta;SQL JOIN 只有 SqlRunStore 能实现,InMemoryRunStore 无 thread_meta 视野——protocol 方法必须两 impl 语义同义,`thread_ids` 集合过滤两边都精确可实现。两段式与 display 层既有 "M0 = N+1; M1 = SQL JOIN" 同精神;thread cap 500 + `thread_window_capped` 信号诚实暴露窗口截断。SQL JOIN 列 M2 优化路径(单查询 + 无 cap),接缝已留(store 参数化后 JOIN 只动 SqlRunStore 内部)。
- **H-11 Skills tab 语义 = agent-authored(created_by_agent_name)**:列已在(Stream SE);"agent 可用的 skills"= 整租户 active 池(skill curator 管理),那是 SkillsList 全局页的语义,塞进 per-agent tab 反而误导归属。不绑 agent_version(skill 不带版本归属)。
- **H-12 triggers/runs 的 `agent_version` 单给报 422**:version 无 name 无意义;fail-fast 防 SDK 误用。
- **H-13 Memory tab 不造 agent 维度**:memory 是 per-user 资产(K6),无 agent_name 列;per-user 持久 agent 产品形态下([memory:target-product-form])用户的 memory 即其 agent 实例的 memory——tab 复用现 per-user 数据路径 + UI 明示 user-scope,**不加假列不硬造过滤**([memory:no-design-choice-disguise]:这是语义事实,非能力缩水;跨 agent 共享 memory 是产品形态决定,改动属产品级需求变更)。

### 6.7.5 测试

- **PR1**:store 单测(thread_meta agent 过滤 × {name only, name+version, 不存在} / run store thread_ids × {空集, 子集, None 回归} / skill created_by_agent_name / trigger agent_version);端点集成(runs agent 过滤 happy + 422 + cap 信号 + 跨租户;skills/triggers 同型);全量回归(无新参数路径逐字节不变)。
- **PR2**:vitest 4 tab(渲染 / SDK 参数断言 / 空态 / cap Alert);Storybook 4;Playwright tab 切换冒烟;`tab_coming_soon` 占位 testid 断言移除后 e2e 同步更新。

### 6.7.6 PR 切分

| PR | 内容 | 验证 |
|---|---|---|
| PR1(backend) | 3 store 过滤参数 + doubles sweep + 3 端点 query params + `thread_window_capped` + 测试 | § 6.7.5-PR1 |
| PR2(frontend,收尾) | SDK 增参 + 4 tab 组件 + AgentDetail 接线 + i18n 双语 + Storybook + vitest + Playwright | § 6.7.5-PR2;零债 6 条 |

---

## 6.8 H.8 详细设计 — Artifacts 运行产物治理面

> 2026-06-12 设计先行。H.8 是 J.15 收尾时挂账的债("前端 inline preview → H.4 admin UI"至今未落)。
> 后端 `/v1/artifacts`(`artifacts.py`,Mini-ADR J-25)已全发;H.8 = 纯前端消费,后端零改。

### 6.8.1 现状取证(2026-06-12,file:line)

| 事实 | 位置 | 影响 |
|---|---|---|
| 5 端点全 **raw**(非 envelope) | `artifacts.py:107-364` | SDK 走 `apiClient.get/...`,不走 `getJson` |
| **全部端点 caller-user-scoped**:download/delete/patch/versions 按 `resolve_caller_user_id` 解析,跨用户/不存在统一 404 隐藏 | `:171-180/:256-265/:301-309/:346-352` | 租户 admin 只能操作**自己的**产物 |
| list 双态:home scope = caller 自己的(machine principal 返空);cross-tenant `*` = 全租户全用户聚合,行带 `tenant_id`/`user_id` | `:124-151` | 页面两态渲染;cross-tenant 行**无行级动作**(无 per-user 上下文) |
| list 响应 `{artifacts, items, cross_tenant}`(双 key 同值) | `:152-158` | SDK 读 `items` |
| download:quota admission(`artifact_download` 计数)→ supervisor 读 workspace 文件 → MIME 安全推断 + `Content-Disposition` + `nosniff`;懒回填 size/sha256;无 supervisor 503 | `:188-239` | 前端 axios blob + objectURL 下载(Bearer header 必须,裸 `window.open` 无 header 必 401);503/429 错误文案 |
| delete = soft-delete(metadata 隐藏,字节留 retention sweep;重存同名 un-delete) | `:241-278` | UI 文案讲清"软删" |
| patch 仅 `kind`(document/code/data/other);unchanged 409 | `:280-329` | 内联 Select,选同值不发请求 |
| versions newest-first;`size_bytes`/`sha256` 未下载前 NULL(懒回填);**无 per-version download 端点**(download 只取 latest) | `:331-364` | 版本历史只读展示,NULL 显 "—" |
| audit 已发:ARTIFACT_DELETE / ARTIFACT_UPDATE | `:271/:315` | 前端无需补 |

### 6.8.2 设计

**SDK `src/api/artifacts.ts`(新)**:`ArtifactKind` / `ArtifactListItem`(name/kind/latest_version + 可选 tenant_id/user_id)/ `ArtifactVersion` / `listArtifacts({tenantScope})` / `downloadArtifact(name)`(axios `responseType:"blob"` + objectURL + a.click,文件名取 `Content-Disposition`)/ `deleteArtifact(name)` / `patchArtifactKind(name, kind)` / `listArtifactVersions(name)`。全 raw 形态。

**页面 `/artifacts`(`ArtifactsList.tsx`)**:
- 列:name(strong)/ kind(内联 Select 即 patch 入口;cross-tenant 态退化为 Tag)/ latest_version / 行动作(download / versions 抽屉 / delete Popconfirm 带软删说明)。
- **双态**(Mini-ADR H-14):home = "我的产物"全功能;cross-tenant `*` = 只读聚合(多 tenant_id/user_id 两列,无行动作)。页头副标题随态切换,讲清归属语义。
- versions 抽屉:Table(version / path_in_workspace / size_bytes / sha256 截断 / created_in_thread / created_at),NULL 显 "—" + tooltip "首次下载后回填"。
- 错误映射:404(已删/不存在)/ 409(前端拦截不触发)/ 429(配额)/ 503(supervisor 未配置)各有文案。
- 空态:home "该账号还没有运行产物";cross-tenant "全平台暂无产物"。

**接线(SE-8 清单)**:router `/artifacts`;Sidebar 主区 memory 之后(`Package` icon);CommandPalette(`g f`);i18n zh/en `artifacts_page.*` + `nav.artifacts` + `cmdk.label_artifacts`;TenantScope 复用 `useTenantScope`;Storybook 3 stories;vitest;Playwright 冒烟。

### 6.8.3 Mini-ADR

- **H-14 治理面如实双态,不假装租户 admin 能管他人产物**:后端契约是 per-user 资产 + 404 隐藏(J-25 隐私语义),cross-tenant 仅聚合 list。前端债范围 = 消费已发能力;"租户 admin 代管他人产物"是后端能力变更(新端点 + 权限模型),记 **H.8-F1 follow-up** 不混入本期([memory:no-design-choice-disguise]:契约事实,非能力缩水)。
- **H-15 download = axios blob + objectURL**:auth 走 Bearer header;blob 路径同时拿到 `Content-Disposition` 文件名。大文件风险接受(产物有 quota cap)。
- **H-16 kind 内联 Select,前端拦截 no-op**:后端 409 语义是"别重试",前端选同值直接不发请求,409 留防御文案。

### 6.8.4 测试

- vitest:home 列表渲染 + 行动作(download 调 SDK / delete Popconfirm / kind Select 变更发 patch、同值不发)/ cross-tenant 态(tenant/user 列现身 + 动作列消失)/ versions 抽屉(NULL 显 "—")/ 空态。
- Storybook 3;Playwright:登录 → /artifacts 渲染 + Sidebar 入口冒烟。

### 6.8.5 PR 切分

| PR | 内容 | 验证 |
|----|------|------|
| PR0(本设计) | § 6.8 + ITERATION-PLAN H.8 细化 | 纯 docs,CI |
| PR1(实现,收尾) | SDK + ArtifactsList 双态 + versions 抽屉 + download/delete/patch + 全接线 + i18n + Storybook + vitest + Playwright | § 6.8.4;零债 6 条 |

---

## 6.9 H.7 详细设计 — Knowledge 知识库治理面

> 2026-06-12 设计先行(前端债第 3 项,顺序 H.8→H.7→H.9)。后端 `/v1/knowledge`(`knowledge.py`,Stream J.5)
> 已全发——bases + documents CRUD + 异步 ingest;H.7 = 纯前端消费,后端零改。

### 6.9.1 现状取证(2026-06-12,file:line)

| 事实 | 位置 | 影响 |
|---|---|---|
| 6 端点全 **raw** | `knowledge.py:92-201` | SDK 走 `apiClient`,不走 `getJson` |
| bases **租户级共享**(非 per-user)——docstring 明示 "tenant-scoped (shared, not per-user)" | `:3` | 治理面无 H.8 的 per-user 约束,租户 admin 全功能 |
| **router 不读 `tenant_id` query**,全部直接 `request.state.tenant_id`(JWT home 租户);无 Stream N 跨租户支持 | `:98/:126/:136/:149/:184/:196` | 前端 TenantScope 切换对此页**无效**——必须如实处理(H-19),不能让 `*` 视图静默显示 home 数据冒充全平台 |
| POST /bases:201;409 重名;400 `overlap >= max`;chunk 参数缺省走 DEFAULT_* | `:92-119` | create modal 两个可选数字字段 + 错误映射 |
| base dict:id/name/chunk_max_tokens/chunk_overlap_tokens/created_at | `:60-67` | 列表列 |
| POST documents:multipart `file`;**202 异步**——记 pending 交 IngestionRunner,调用方轮询;400 无文件名/扩展名不在白名单;**503 无 embedder** | `:141-176` | Upload 组件 + 轮询 + 503 文案(指向平台 embedder 配置) |
| 扩展白名单:`.pdf .docx .pptx .xlsx .md .markdown .txt .html .htm .csv` | `parsing.py:21-23` | Upload `accept` + 前端预校验 |
| document dict:id/filename/**status**(pending/ingesting/ready/failed)/error/chunk_count/created_at/updated_at | `:70-79` / `protocol/knowledge.py:31-34` | status Tag 色板 + failed 行 error tooltip |
| DELETE base/document:204;document 404 | `:130-139/:189-201` | Popconfirm(删库提示连带文档) |

### 6.9.2 设计

**SDK `src/api/knowledge.ts`(新,全 raw)**:`KnowledgeBase` / `KnowledgeDocument` / `DocumentStatus` / `listBases()` / `createBase({name, chunkMaxTokens?, chunkOverlapTokens?})` / `deleteBase(name)` / `uploadDocument(baseName, file)`(FormData)/ `listDocuments(baseName)` / `deleteDocument(baseName, documentId)`。

**页面 `/knowledge`(`KnowledgeAdmin.tsx`,单页 master-detail,H-17)**:
- 左:bases 表(name / chunk 参数 / created_at——列表端点无文档计数,不造假列)+ Create modal(name 必填 + 两个可选数字,400/409 错误映射)+ Delete Popconfirm(明示连带删除文档与向量)。
- 右(选中 base):documents 表(filename / status Tag:pending=default、ingesting=processing、ready=success、failed=error / chunk_count / updated_at / failed 行 error tooltip + 行删除)+ Upload(antd Upload,`accept` 白名单,beforeUpload 前端预校验,202 后立即入列表)。
- **轮询(H-18)**:选中 base 的 documents 含 pending/ingesting 时 5s 间隔刷新,全终态停;切 base/卸载清 timer。
- **租户语义(H-19)**:页面不传 TenantScope;副标题明示"知识库按当前登录租户";全局 scope=`*` 或他租户时顶部 info Alert 提示本页不随 scope 切换,操作仍指向 home 租户。
- 错误映射:409(重名)/ 400(overlap/扩展名)/ 503(embedder 未配置——指向平台配置)/ 404。

**接线(SE-8 清单)**:router `/knowledge`;Sidebar 主区 artifacts 后(`BookOpen` icon);CommandPalette(`g k`);i18n zh/en `knowledge_page.*` + `nav.knowledge` + `cmdk.label_knowledge`;Storybook 3 stories;vitest;Playwright 冒烟。

### 6.9.3 Mini-ADR

- **H-17 单页 master-detail,不设 base detail 路由**:base 本体仅 5 字段,核心信息是其 documents;两层路由对 5 字段实体是过度结构。
- **H-18 ingest 进度 = 条件轮询**:后端 202 + 无推送通道;有未终态文档才轮询(5s),全终态停——不常驻轮询、不造假 SSE。
- **H-19 如实声明单租户边界**:后端 router 无 `tenant_id` query 支持,前端把 scope=`*` 静默映射到 home 数据 = 冒充全平台视图。页面脱离 TenantScope + 明示语义;跨租户 knowledge 治理需后端支持,记 **H.7-F1 follow-up**(与 H.8-F1 同性质:后端能力变更另立项)。

### 6.9.4 测试

- vitest:bases 渲染 + create modal(成功/409 文案)+ delete 确认;documents 渲染(4 态 Tag + failed error tooltip)+ upload 调 SDK + 非白名单扩展前端拦截;轮询(未终态启动/全终态停,fake timers);scope Alert(`*` 时现身)。
- Storybook 3;Playwright:登录 → /knowledge 渲染冒烟。

### 6.9.5 PR 切分

| PR | 内容 | 验证 |
|----|------|------|
| PR0(本设计) | § 6.9 + ITERATION-PLAN H.7 细化 | 纯 docs,CI |
| PR1(实现,收尾) | SDK + KnowledgeAdmin(master-detail + upload + 轮询)+ 全接线 + i18n + Storybook + vitest + Playwright | § 6.9.4;零债 6 条 |

---

## 6.10 H.9 详细设计 — Rate Card 计价管理面(system_admin)

> 2026-06-12 设计先行(前端债末项)。后端 `/v1/platform/rate-card`(`rate_card.py`,Stream Y/Z 计费)已全发;
> H.9 = 纯前端消费,后端零改。与平台计量加价大方向([memory:platform-centralized-governance])直接相关。

### 6.10.1 现状取证(2026-06-12,file:line)

| 事实 | 位置 | 影响 |
|---|---|---|
| 5 端点全 **envelope**(`{success,data,error}`) | `rate_card.py:76-209` | SDK 走 `getJson`/`postJson`/`patchJson`(与 H.7/H.8 的 raw 相反) |
| 权限:`require("billing", read/write/delete)` + `principal.is_system_admin` 双门 | 各端点 | 前端 isSystemAdmin 门控(SettingsTenants 同款);**前端门控是 UX 非安全边界**(H-22) |
| `ModelRateCardRecord`:id/tenant_id(NULL=platform-global,唯一现状)/provider/model/4×`*_token_micros`/markup_bps/plan_tier/effective_from/effective_until | `protocol/billing.py:110-132` | 列表列 + 表单字段 |
| **Patch 不可变身份**:provider/model/plan_tier/effective_from 创建后不可改——"Reprice by inserting a new row, never by mutating" | `billing.py:168-174` docstring | 编辑面只开放 5 价格字段 + effective_until;重定价引导新建(H-20) |
| Upsert 校验:provider/model 结构校验 + `effective_until > effective_from` | `billing.py:160-165` | 表单前端预校验 + 422 映射 |
| list query:`provider` / `model` / `include_expired`(默认 false) | `rate_card.py:107-122` | 过滤条 + 过期开关 |
| 价格单位 = **micro-USD per token**(`*_token_micros`)+ `markup_bps`(基点) | 字段名 | 输入原值;只读换算提示 $/1M tokens(H-21) |
| DELETE 204 | `:176-209` | 行删 Popconfirm |

### 6.10.2 设计

**SDK `src/api/rate_card.ts`(新,全 envelope)**:`RateCardRecord` / `listRateCards({provider?, model?, includeExpired?})` / `createRateCard(upsert)` / `getRateCard(id)` / `patchRateCard(id, patch)` / `deleteRateCard(id)`。

**页面 `/settings/rate-card`(`SettingsRateCard.tsx`,Settings 子页)**:
- isSystemAdmin 门控:非 system_admin 显说明 Empty(SettingsTenants 同款),fetch 不发。
- 列表:provider / model / plan_tier(NULL 显 "all plans")/ input / output / cache(creation/read 合并列 tooltip)/ markup_bps / effective_from / effective_until(NULL 显 "open-ended")/ 过期行灰显。过滤:provider/model 输入 + include_expired Switch。
- Create modal:全字段;micros 字段旁只读换算($/1M tokens = micros × 1e6 ÷ 1e6 微美元换算提示);`effective_until > effective_from` 前端预校验。
- 编辑抽屉(H-20):**只开放** 4×micros + markup_bps + effective_until;provider/model/plan_tier/effective_from 灰显展示 + 文案"重定价请新建一行(身份字段不可变)"。
- Delete:Popconfirm;提示已生效区间的删除影响计费回溯(谨慎文案)。

**接线**:router `/settings/rate-card`;Sidebar SETTINGS_ITEMS(billing-chargeback 旁,`Banknote` icon);i18n zh/en `rate_card_page.*` + `nav.rate_card`;Storybook(列表/非 admin 2 stories);vitest;Playwright 冒烟。

### 6.10.3 Mini-ADR

- **H-20 编辑面 = Patch 可变字段集,镜像后端时间身份语义**:provider/model/plan_tier/effective_from 是行的 temporal+specificity 身份,后端拒改;UI 灰显 + "重定价新建行"引导,不给可编辑假象。
- **H-21 micros 原值输入,换算只读提示**:隐式单位转换(用户输 $/1M 前端换 micros)是计费数字的静默放大器;输入框收原始 micros,旁挂只读 $/1M 换算核对。
- **H-22 前端 isSystemAdmin 门控 = UX 非安全边界**:真边界是后端 `require("billing",·)` + `is_system_admin` 双门;前端门控只省一次 403 往返 + 不渲染无权 UI。

### 6.10.4 测试

- vitest:非 admin 门控(说明页 + 不发 fetch)/ 列表渲染 + include_expired 开关传参 / create 调 SDK(含换算提示渲染)/ 编辑抽屉不可变字段灰显 + patch 只发可变字段 / delete 确认。
- Storybook 2;Playwright:system_admin 登录 → /settings/rate-card 渲染冒烟。

### 6.10.5 PR 切分

| PR | 内容 | 验证 |
|----|------|------|
| PR0(本设计) | § 6.10 + ITERATION-PLAN H.9 细化 | 纯 docs,CI |
| PR1(实现,收尾) | SDK + SettingsRateCard(列表/create/编辑抽屉/delete + isSystemAdmin 门控)+ 全接线 + i18n + Storybook + vitest + Playwright | § 6.10.4;零债 6 条 |

---

## 6. PR 链(预估)

| PR | 内容 | 估时 |
|----|------|------|
| PR1 ✅ | H.1a 设计基线 PR1 — philosophy.md + language.md + STREAM-H-DESIGN.md + tokens.css + shell.css + ITERATION-PLAN 修订 #262 | 1.5 天 |
| PR2 ✅ | H.1a 设计基线 PR2 — 8 张 mockup HTML + mockups/README.md + brand glyph SVG + a11y 自检截图 #263 | 2-3 天 |
| PR3 ✅ | H.1b — `apps/admin-ui/` Vite 工程 scaffold + Antd 5 + i18n + 路由 + 鉴权 + Shell #264 #272 #274 | 3-4 天 |
| PR4 ✅ | H.1b — CommandPalette + TenantSwitcher + 主题切换 + Lucide 接入 + OIDC + 6 SDK + Storybook/E2E #277 #278 #279 #280 #281 | 1-2 天 |
| PR5 ✅ | H.2 — Agent 详情 Manifest tab Monaco YAML(view/edit/save)+ Create Agent drawer + POST/PUT /v1/agents #284 #285 | 3-4 天 |
| PR6 ✅ | H.2 — Agent 详情 Playground tab(fetch+ReadableStream SSE + 色彩分类事件日志 + Stop)#286;**(a) 推迟**:改 manifest 重跑(需后端 ad-hoc spec override)/ 工具调用语义化时间线 / 审批 mid-run UX(H.3) | 3-4 天 |
| PR7a ✅ | H.3 PR 1 — Backend `GET /v1/runs` 跨 thread 索引 + SDK + RunsList 页(Mini-ADR H-6)#289 | 2-3 天 |
| PR7b ✅ | H.3 PR 2 — `agent_run.trace_id` 持久化(migration 0037 + DTO + RunStore.set_trace_id + 序列化 + frontend 类型)(Mini-ADR H-9.5)#290 | 1.5 天 |
| PR7c ✅ | H.3 PR 3 — `run_event` 持久层(migration 0038 + RunEventStore + run_agent 双写接入)(Mini-ADR H-7 backend)#291 | 2 天 |
| PR7d ✅ | H.3 PR 4 — `GET .../events` endpoint (live + replay) + RunDetail Event stream panel(Mini-ADR H-7 frontend)#292 | 1.5-2 天 |
| PR7e ✅ | H.3 PR 5 — Approval `override_args` Monaco UX + 状态轮询(Mini-ADR H-9)#293 | 1.5 天 |
| PR7f ✅ | H.3 PR 6 — Trace 链接外跳(Mini-ADR H-8)+ Approval pending badge + 体感打磨 #294 | 1 天 |
| PR8 (H.4 PR0) ✅ | H.4 设计基线 — § 6.6 详细设计(18 子章节) + 5+ mockup(09-runs-list 补 H.3 留账 + 10-skills + 11-triggers + 12-audit + 13-settings-iam + 14-settings-ops) + mockups/README.md + ITERATION-PLAN 同步 #296 | 2.5-3 天 |
| PR9 (H.4 PR1) ✅ | H.4 PR 1 — Curation+Eval UI(`api/curation.ts` 补 mutation + `pages/CurationReview.tsx` + `pages/EvalDatasets.tsx`)#297 | 2 天 |
| PR10 (H.4 PR2) ✅ | H.4 PR 2 — Memory CRUD(`api/memory.ts` 补 update/delete + `pages/MemoryAdmin.tsx`,复用 ApprovalCard Monaco 范式)#298 | 1.5 天 |
| PR11 (H.4 PR3) ✅ | H.4 PR 3 — Audit backend endpoint(`build_audit_router()` + 复用既有 `AUDIT_READ` + ResourceType `"audit"` 双份已存在,无 enum/Literal 改动 + `test_audit_api.py` 11 测)#299 | 2 天 |
| PR12 (H.4 PR4) ✅ | H.4 PR 4 — Audit UI(`api/audit.ts` cursor 分页 + `pages/SettingsAudit.tsx` timeline + filter chips + entry drawer)#300 | 2 天 |
| PR13 (H.4 PR5) ✅ | H.4 PR 5 — Skills(create / update / version / import ZIP / export ZIP + `pages/SkillsList.tsx` + `pages/SkillDetail.tsx`)#301 | 2.5 天 |
| PR14 (H.4 PR6) ✅ | H.4 PR 6 — Triggers(CRUD + `pages/TriggersList.tsx` cron/webhook Tab + Create drawer + webhook secret show-once + Switch 内联 PATCH)#302 | 2.5 天 |
| PR15 (H.4 PR7) ✅ | H.4 PR 7 — Settings IAM(`api/{service_accounts,role_bindings}.ts` + `pages/SettingsServiceAccounts.tsx` + `pages/SettingsRoleBindings.tsx` + platform_scope type-to-confirm 三重防御)#303 | 2 天 |
| PR16 (H.4 PR8) ✅ | H.4 PR 8 — Settings Ops(`api/{tenant_quotas,tenant_config}.ts` + `pages/SettingsTenantQuotas.tsx` + `pages/SettingsTenantConfig.tsx` Monaco JSON Edit + last-writer-wins)#304 | 2 天 |
| PR17 (H.4 PR9) ✅ | H.4 收尾 + Stream H 整体收尾 — § 6.6.18 收尾摘要 + ITERATION-PLAN ✅ + Stream H § 7 全部验收 + `governance.spec.ts` 7 happy-path 冒烟 + 补 mockup 04 ApprovalCard 编辑态(H.3 留账)| 0.5-1 天 |

> 总估时(H.1a → H.4):**42-50 天**(H.1a/H.1b/H.2/H.3 实际 ~35 天 + H.4 14-18 天)。H.4 PR0 是阻塞 PR,合入后 PR1/2/5/6/7/8 可并行,PR3 → PR4 顺序。

---

## 7. 验证

### H.1a 验收(本规划 PR1+PR2 完成)
- philosophy.md 6 条原则 → language.md tokens 实现 → mockup 视觉呈现,三层链路自洽
- 8 张 mockup `open mockups/*.html` 在 Chrome / Safari / Firefox 渲染正常
- mockup 顶部主题切换按钮(dark ⇆ light)无破坏性视觉差异
- 至少 3 张 mockup 跑 axe DevTools 报告 0 critical;Lighthouse a11y ≥ 95
- 术语表覆盖所有 mockup 中可见英中字符串
- `tokens.css` + `shell.css` 无外部依赖,无 SCSS,无 build —— 浏览器直接渲染

### Stream H 整体验收(所有 PR 合并后)— **2026-05-26 全部 ✅**
1. ✅ UI/UX 设计基线文档先于实现合入(H.1a PR1+PR2 在最前 #262 #263)
2. ✅ 每个 H.* 子项产品级体验:响应式(≥1280px desktop)/ 键盘可达 / a11y(axe 0 critical,Playwright + axe CI 每 PR 跑)/ 性能(首屏 < 2s, Lighthouse Performance ≥ 90 — H.1a baseline + H.4 持续监控)
3. ✅ UI 集成测试覆盖 happy path(`@testing-library/react` 130/130 + Playwright governance.spec.ts 7 happy-path)
4. ✅ 接入的 B/E/J/K 能力面在 UI 上端到端可见(Agent CRUD / Sessions+Runs SSE / Memory CRUD / Curation+Eval / Skills(含 ZIP)/ Triggers(cron+webhook)/ API Keys / Service Accounts / Role Bindings(含 platform_scope)/ Tenant Quotas / Tenant Config / Audit)
5. ✅ system_admin 跨租户视角端到端可见(登录默认 "All tenants" → 切到具体 tenant → 切回 → 所有 audit 留痕,11 个 list endpoint 经 Stream N 改造)
6. ✅ `[[feedback_zero_tech_debt]]` 6 条 H.1 → H.4 全部满足,每 stage 收尾 PR 显式审计(§ 6.5.18 H.3 + § 6.6.18 H.4)
7. ✅ ITERATION-PLAN Stream H 全部子项 ✅,Stream H 收官归档

---

## 修订记录

| 日期 | 版本 | 说明 |
|---|---|---|
| 2026-05-25 | v1.0 | 初稿:设计基线 PR 链 + IA + 工程目录 + 5 个 Mini-ADR + 12 个 PR 估时;H.4 用户面取消,改为治理面;Playground 嵌 per-agent tab |
| 2026-05-25 | v1.1 | H.1a / H.1b / H.2 全部完成(PR1–6,合并 #262–264 / #272 / #274 / #277–281 / #284–286);PR 链表加 ✅ 标记 + #PR 引用;H.2 PR 6 显式推迟项落到 PR 行尾 |
| 2026-05-26 | v1.2 | 加 § 6.5 **H.3 详细设计** + Mini-ADR H-6 / H-7 / H-8 / H-9;原 PR7 拆为 PR7a/b/c 3 个;锁定:`GET /v1/runs` 跨 thread 索引兑现 Mini-ADR J-41 deferred / SSE 实时回放推 M1 / Trace = 外链跳 Langfuse / Approval `override_args` Monaco inline 编辑 |
| 2026-05-26 | v1.3 | § 6.5 补全实现期细节:§ 6.5.8 文件级影响图(每 PR 表)+ § 6.5.9 状态机(RunsList / RunDetail polling / Approval edit ASCII 图)+ § 6.5.10 错误/边界场景矩阵(12 条)+ § 6.5.11 audit + Prometheus 信号 + § 6.5.12 i18n keys 全量(4 namespace)+ § 6.5.13 测试计划(unit/integration/E2E)+ § 6.5.14 mockup 引用 + 缺 09-runs-list mockup 标作待办债务 + § 6.5.15 schema 影响(none)+ § 6.5.16 backwards compat + § 6.5.17 安全/鉴权矩阵 |
| 2026-05-26 | v1.4 | **用户决策"#2 SSE 实时回放 + #6 trace_id 持久化做完整,不推迟"**:Mini-ADR H-7 重写 — 新 `run_event` 表 + `RunEventStore` 三态 + producer 双写 + `GET .../events` 端点(live attach + replay 双路径);新 Mini-ADR H-9.5 — `agent_run.trace_id` 持久化(migration 0038)。PR 链拆 PR7a-c 为 PR7a-f(共 6 PR),总估时 9.5-11 天(原 4.5-5.5 天);§ 6.5.15 加 migration 0037 / 0038 详情;§ 6.5.8 加 PR 2/3/4 文件表;§ 6.5.11 加 3 个 Prometheus 信号;§ 6.5.12 加 `event_stream` 6 keys;§ 6.5.10 加 4 条新边界场景 |
| 2026-05-26 | v1.5 | review 第二轮决议 A-F 落地:(A) SSE id wire format `"{created_at_ms}-{seq}"` 一致,`run_event` 加 `created_at_ms bigint` 列 — replay endpoint emit 与 live 同型,客户端 `parseSseStream` 不区分 / (B) `RunManager.create(trace_id=)` 显式参数 — handler 路径传 `current_trace_id_hex()`,scheduler 路径传 None / (C) `run_event` FK 改 `ON DELETE RESTRICT` — 锁定 M1 archive-then-delete 灵活性 / (D) `list_for_tenant` / `list_all_tenants` / `RunEventStore.list` 均强制 `max_limit=500` + `X-Limit-Capped` header / (E) EventStreamPanel 默认折叠,展开才连;localStorage 记 per-user 偏好 / (F) 5 个新组件全加 Storybook story(共 19 个 story);approval 完整 E2E 推 M0 dogfood |
| 2026-05-26 | v1.6 | 实现期发现 PR 2 (trace_id) 必须先落、PR 3 (run_event) 后落,但原设计文档 migration 编号是反过来的 (PR 2=0038, PR 3=0037)。修正为 PR 2=0037, PR 3=0038 与 Alembic linear chain (down_revision 链)对齐。无功能变更。 |
| 2026-05-26 | v1.7 | **H.3 收尾**:6 个 PR 全部合入 main(#289–#294);新增 § 6.5.18 H.3 收尾摘要 — 决议 A–F 全部兑现、设计文档 § 6.5.13 漏交的 2 个 story(EventStreamPanel + ApprovalCard)在收尾 PR 补齐;遗留待办全部归类到 M0 dogfood / H.4 收尾(approval 完整 E2E、RunsList mockup、ApprovalCard 编辑态 mockup) |
| 2026-05-26 | v1.8 | **H.4 设计基线**:加 § 6.6 H.4 详细设计(18 子章节,复刻 § 6.5 范式)— 范围 7 子面(Curation+Eval / Memory / Skills / Triggers / Audit / Settings IAM / Settings Ops)+ Audit backend endpoint 新建 + 跨租户 RBAC matrix + 错误边界矩阵(12 条)+ i18n 8 namespace ~130 keys + 测试计划(backend 10 测 + frontend 45 单测 + 33 stories + E2E 7 happy-path)。锁定 4 个 spike 结果:(1) Trigger webhook secret 回包 schema 已有 — PR6 backend 不需改;(2) ResourceType Literal 双份漂移 — PR3 必须改 `protocol/audit.py:146` + `control-plane/audit.py:111` 两处;(3) audit_logger fixture pattern = 每测试自建,不走 conftest — PR3 遵循同型;(4) RoleBinding self-elevation 已被 DTO + caller 双重保护 — PR7 backend 不需改。PR 链拆 PR8-12(原 5 个 H.4 PR)为 PR8-17(10 个 PR:1 设计 + 8 实施 + 1 收尾),总估时 14-18 天。|
| 2026-06-12 | v2.0 | **H.6 详细设计**(前端债回填,用户拍板 H.6 起手,H.7–H.9 顺序后定):加 § 6.7 — AgentDetail 4 tab 真实现;现状取证 12 条 file:line(含 envelope-vs-raw 逐端点核实:runs/memory=envelope、skills/triggers=raw);后端过滤前置 = thread_ids 两段式(H-10,不做 SQL JOIN——InMemoryRunStore 无 thread_meta 视野,protocol 两 impl 同义优先)+ skills agent-authored 语义(H-11,created_by_agent_name 列已在)+ triggers 补 version + Memory 不造 agent 维度(H-13,per-user 资产语义事实);2-PR 切分(backend 过滤 / frontend 4 tab 收尾) |
| 2026-06-12 | v2.6 | **H.9 详细设计**(前端债末项):加 § 6.10 — Rate Card 计价管理面;取证 8 条(5 端点全 **envelope**(与 H.7/H.8 raw 相反)/ 双门权限 / **Patch 不可变身份字段**——后端 docstring 明示重定价=插新行);Mini-ADR H-20 编辑面=Patch 可变字段集+灰显身份字段 / H-21 micros 原值输入+换算只读提示(不做隐式单位转换) / H-22 前端门控=UX 非安全边界;纯前端 2-PR |
| 2026-06-12 | v2.5 | **H.7 收尾**:PR1 全交付——SDK `knowledge.ts`(6 方法全 raw + `SUPPORTED_DOCUMENT_EXTENSIONS`/`isSupportedDocument`)+ KnowledgeAdmin 单页 master-detail(H-17)+ Upload 白名单预校验 + 503 embedder 文案 + ingest 条件轮询(H-18:未终态 5s/全终态停)+ H-19 scope note + 全接线 + Storybook 2 + vitest 6 + Playwright `knowledge.spec.ts`。实现期修正:antd 静态 `message` 在测试环境不渲染 → KnowledgeAdmin/ArtifactsList 统一 `App.useApp()` 注入(house style,MemoryAdmin 同款);`userEvent.upload` 默认尊重 `accept` 需 `applyAccept:false` 才能测 beforeUpload 预校验。设计 § 6.9 全兑现无偏差;H.7-F1 留 follow-up |
| 2026-06-12 | v2.4 | **H.7 详细设计**(前端债第 3 项):加 § 6.9 — Knowledge 治理面;取证 9 条(6 端点全 raw / bases 租户级共享 / **router 不读 tenant_id query——TenantScope 对此页无效**);Mini-ADR H-17 单页 master-detail / H-18 ingest 条件轮询(202+无推送,不造假 SSE)/ H-19 如实声明单租户边界(scope=`*` 静默映射 home = 冒充全平台,记 H.7-F1 follow-up);纯前端 2-PR |
| 2026-06-12 | v2.3 | **H.8 收尾**:PR1 全交付——SDK `artifacts.ts`(5 方法全 raw + `filenameFromDisposition` RFC 5987)+ ArtifactsList 双态(H-14 兑现:cross-tenant 无行动作、kind 退化 Tag)+ versions 抽屉(NULL digest "—" + 懒回填 tooltip)+ download blob(H-15)/软删/kind 内联 Select(H-16 前端拦 no-op)+ 全接线(router/Sidebar/CommandPalette `g f`/i18n 双语)+ Storybook 3 + vitest 9(页面 5 + disposition 解析 4)+ Playwright `artifacts.spec.ts`。设计 § 6.8 全兑现无偏差;H.8-F1(租户 admin 代管)留 follow-up |
| 2026-06-12 | v2.2 | **H.8 详细设计**(前端债第 2 项,顺序 H.8→H.7→H.9):加 § 6.8 — Artifacts 治理面;现状取证 9 条(5 端点全 raw / **全端点 caller-user-scoped + 404 隐藏**是 J-25 契约事实);Mini-ADR H-14 如实双态(home=我的产物全功能 / cross-tenant=只读聚合无行动作),"租户 admin 代管他人产物"记 H.8-F1 follow-up 不混入;H-15 download=axios blob(Bearer header 约束);H-16 kind 内联 Select 前端拦 no-op;纯前端 2-PR(设计 + 实现收尾) |
| 2026-06-12 | v2.1 | **H.6 收尾**:PR1(#582)backend 过滤全交付(三 store 过滤 + thread_ids 空集≠None 语义 + 3 端点 + `thread_window_capped` + 422 防呆 + 测试 +15);PR2 frontend 全交付(SDK 增参 + RunsTab/SkillsTab/TriggersTab/MemoryTab + AgentDetail 去占位接线 + i18n zh/en 4 namespace + Storybook 5 stories + vitest 7 测 + Playwright `agent-detail-tabs.spec.ts`)。占位 Empty 仅余 unknown-tab fallback。设计 § 6.7 全兑现无偏差;遗留:H-10 SQL JOIN 仍是 M2 优化接缝(cap 500 生效中) |
| 2026-05-26 | v1.9 | **H.4 收尾 + Stream H 整体收官**:9 个 H.4 PR 全部合入 main(#296–#304),共 ~+9,500 行;§ 6.6.18 H.4 收尾摘要落地(决议核验 / 零债 6 条 / 留给后续 stream 的债务清单 / capability gap 声明);PR0 spike 简化兑现:(spike 2) `AUDIT_READ` enum + `"audit"` ResourceType 已存在 — 取消"新增 AUDIT_QUERY enum / 双份 Literal drift 修复"两条原计划改动;(spike 1/3/4) backend 不需改;实施期发现 3 个 latent SDK envelope-vs-raw bug 全部修复(`listCandidates` PR1 + `listSkills` PR5 + `listTriggers` PR6),根因写入新 memory `feedback_envelope_vs_raw_contract_check`;Stream H 整体验收(§ 7)全部 ✅(7 条全勾);ITERATION-PLAN Stream H 收官归档;Playwright `e2e/governance.spec.ts` 7 happy-path 冒烟;补 mockup `04-run-trace.html` ApprovalCard 编辑态截图(H.3 留账兑现)|
