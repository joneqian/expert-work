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
- **新表 `run_event`**(migration 0037):`(run_id, seq) PRIMARY KEY` + `event_name text NOT NULL` + `data jsonb NOT NULL` + `created_at timestamptz`。索引 `(run_id, seq)` 即覆盖 `WHERE run_id = ? ORDER BY seq ASC` 唯一查询模式。`seq` = 单调整数(per-run),与 SSE `id` 字段一致(`"<ms>-<seq>"` 格式截 `seq`)。
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
- **Migration 0038**:给 `agent_run` 加 `trace_id varchar(32) NULL`(`current_trace_id_hex()` 返回 16 字节 hex = 32 字符)+ 非唯一索引 `(trace_id) WHERE trace_id IS NOT NULL`(支持反查 "from Langfuse trace_id back to run")。
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
| **`agent_run.trace_id` 持久化** | ✓(Mini-ADR H-9.5;migration 0038)| — |
| **SSE 历史回放** | ✓(Mini-ADR H-7;新表 `run_event` migration 0037 + `RunEventStore` 三态 + `GET .../events` 端点 live/replay 双路径)| 保留期 sweep 推 M1 |
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
- ~~**Trace ID 不直接挂在 `RunInfo`**~~ — v1.4 起 promote 为 Mini-ADR H-9.5 解决(migration 0038)。

### 6.5.6 PR 拆分

| PR | 范围 | 估时 |
|---|---|---|
| **H.3 PR 1** | Backend `GET /v1/runs` + RunStore `list_for_tenant` / `list_all_tenants` + SDK `listRuns` + RunsList 页(Mini-ADR H-6)| 2-3 天 |
| **H.3 PR 2** | `agent_run.trace_id` 持久化(migration 0038 + DTO + RunStore.set_trace_id + RunManager 接入 + GET /v1/runs / `/v1/sessions/.../runs/{id}` 序列化 + frontend 类型)(Mini-ADR H-9.5)| 1.5 天 |
| **H.3 PR 3** | `run_event` 持久层(migration 0037 + `RunEventStore` ABC + Memory + SQL + `run_agent` 双写接入 + 错误计数器)(Mini-ADR H-7 backend 部分)| 2 天 |
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
| `packages/helix-persistence/migrations/versions/0038_agent_run_trace_id.py` | **新文件** — `ALTER TABLE agent_run ADD COLUMN trace_id varchar(32) NULL` + partial index `(trace_id) WHERE trace_id IS NOT NULL` | +35 |
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
| `packages/helix-persistence/migrations/versions/0037_run_event.py` | **新文件** — 新建 `run_event` 表(`run_id uuid` + `seq bigint` + `event_name text` + `data jsonb` + `created_at timestamptz`)+ `PRIMARY KEY (run_id, seq)` | +50 |
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

**Migration 0037 — `run_event` 表**(PR 3,Mini-ADR H-7)
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

**Migration 0038 — `agent_run.trace_id`**(PR 2,Mini-ADR H-9.5)
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
| PR7a | H.3 PR 1 — Backend `GET /v1/runs` 跨 thread 索引 + SDK + RunsList 页(Mini-ADR H-6)| 2-3 天 |
| PR7b | H.3 PR 2 — `agent_run.trace_id` 持久化(migration 0038 + DTO + RunStore.set_trace_id + 序列化 + frontend 类型)(Mini-ADR H-9.5)| 1.5 天 |
| PR7c | H.3 PR 3 — `run_event` 持久层(migration 0037 + RunEventStore + run_agent 双写接入)(Mini-ADR H-7 backend)| 2 天 |
| PR7d | H.3 PR 4 — `GET .../events` endpoint (live + replay) + RunDetail Event stream panel(Mini-ADR H-7 frontend) | 1.5-2 天 |
| PR7e | H.3 PR 5 — Approval `override_args` Monaco UX + 状态轮询(Mini-ADR H-9)| 1.5 天 |
| PR7f | H.3 PR 6 — Trace 链接外跳(Mini-ADR H-8)+ Approval pending badge + 体感打磨 | 1 天 |
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
| 2026-05-25 | v1.1 | H.1a / H.1b / H.2 全部完成(PR1–6,合并 #262–264 / #272 / #274 / #277–281 / #284–286);PR 链表加 ✅ 标记 + #PR 引用;H.2 PR 6 显式推迟项落到 PR 行尾 |
| 2026-05-26 | v1.2 | 加 § 6.5 **H.3 详细设计** + Mini-ADR H-6 / H-7 / H-8 / H-9;原 PR7 拆为 PR7a/b/c 3 个;锁定:`GET /v1/runs` 跨 thread 索引兑现 Mini-ADR J-41 deferred / SSE 实时回放推 M1 / Trace = 外链跳 Langfuse / Approval `override_args` Monaco inline 编辑 |
| 2026-05-26 | v1.3 | § 6.5 补全实现期细节:§ 6.5.8 文件级影响图(每 PR 表)+ § 6.5.9 状态机(RunsList / RunDetail polling / Approval edit ASCII 图)+ § 6.5.10 错误/边界场景矩阵(12 条)+ § 6.5.11 audit + Prometheus 信号 + § 6.5.12 i18n keys 全量(4 namespace)+ § 6.5.13 测试计划(unit/integration/E2E)+ § 6.5.14 mockup 引用 + 缺 09-runs-list mockup 标作待办债务 + § 6.5.15 schema 影响(none)+ § 6.5.16 backwards compat + § 6.5.17 安全/鉴权矩阵 |
| 2026-05-26 | v1.4 | **用户决策"#2 SSE 实时回放 + #6 trace_id 持久化做完整,不推迟"**:Mini-ADR H-7 重写 — 新 `run_event` 表 + `RunEventStore` 三态 + producer 双写 + `GET .../events` 端点(live attach + replay 双路径);新 Mini-ADR H-9.5 — `agent_run.trace_id` 持久化(migration 0038)。PR 链拆 PR7a-c 为 PR7a-f(共 6 PR),总估时 9.5-11 天(原 4.5-5.5 天);§ 6.5.15 加 migration 0037 / 0038 详情;§ 6.5.8 加 PR 2/3/4 文件表;§ 6.5.11 加 3 个 Prometheus 信号;§ 6.5.12 加 `event_stream` 6 keys;§ 6.5.10 加 4 条新边界场景 |
| 2026-05-26 | v1.5 | review 第二轮决议 A-F 落地:(A) SSE id wire format `"{created_at_ms}-{seq}"` 一致,`run_event` 加 `created_at_ms bigint` 列 — replay endpoint emit 与 live 同型,客户端 `parseSseStream` 不区分 / (B) `RunManager.create(trace_id=)` 显式参数 — handler 路径传 `current_trace_id_hex()`,scheduler 路径传 None / (C) `run_event` FK 改 `ON DELETE RESTRICT` — 锁定 M1 archive-then-delete 灵活性 / (D) `list_for_tenant` / `list_all_tenants` / `RunEventStore.list` 均强制 `max_limit=500` + `X-Limit-Capped` header / (E) EventStreamPanel 默认折叠,展开才连;localStorage 记 per-user 偏好 / (F) 5 个新组件全加 Storybook story(共 19 个 story);approval 完整 E2E 推 M0 dogfood |
