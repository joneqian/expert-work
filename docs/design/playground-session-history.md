# 会话历史面 — Playground 续聊从「单下拉」升级为「会话历史」

> 状态:已实现(PR1 后端 + PR2 前端,同 PR 交付)。待 CI + live 验。
> 决策人:owner(leyi)。三个 tradeoff 岔口由 owner 拍板,见 §3。

## 1. 目标

Playground(调试台)续聊当前是会话头一行里**一个 antd 小号 `<Select>`**,选项标签 = `<thread_id 前8位> · <created_at>`。8 位 hex 认不出哪个会话,靠时间猜;无搜索、无预览、无删除/改名;`limit 100` 静默截断。

把它升级为真正的「会话历史」面:**抽屉列表**,每行显示可读标题 + 最后活跃 + 状态 + run-as 属主,支持搜索、续聊、改名、软删/硬删。对标产品目标形态(per-user 持久 agent 带对话历史)。

## 2. 现状(已查实)

- `thread_meta` 列:`thread_id / tenant_id / user_id / created_by / status / agent_name / agent_version / created_at / updated_at`。**无 title**。status = Text,server_default `active`,**无 CHECK 约束**(加新枚举值安全)。
- `ThreadStatus`(protocol):`ACTIVE / PAUSED / COMPLETED / FAILED / CANCELLED`。**无 ARCHIVED**。
- `ThreadMetaStore`(base/sql/memory):已有 `create / get / list_by_tenant / list_all_tenants / update_status`;list 已支持 `status / user_id / agent_name / nonempty / limit / offset`。**无 title 读写、无 delete、无 title 搜索**。
- `sessions` API:`create / get / list / messages(#869)/ workspace 系列 / :pause :resume :cancel`。**无整会话 delete、无 PATCH(改名)**。
- run 触发 `trigger_run`(runs.py:810)持有 `meta` + `payload.input`(用户消息)+ tenant + `prior_runs` —— auto-title 的天然挂点。
- 前端 `PlaygroundTab`:`pastSessions` + `handleResume` + `<Select data-testid=playground-resume-select>`;`listSessions({limit:100})`;`ThreadMeta` SDK 类型无 title。

## 3. 三个 tradeoff 岔口(owner 拍板)

| 岔口 | 决策 | 理由 |
|------|------|------|
| 删除语义 | **软删 + 硬删都要** | 默认软删(归档)可逆、安全;另给「彻底删除」二次确认走硬删(连 checkpoint/run/artifact/workspace 抹) |
| 标题来源 | **首句自动 + 手动改名** | 首条用户消息截断做标题(零额外模型调用),用户可改名覆盖;LLM 摘要标题推迟(重、异步) |
| UI 形态 | **抽屉列表** | 点「会话历史」开 Drawer,搜索 + 长列表 + 行操作;不占常驻空间、扛量、可先浏览再续 |

## 4. PR1 — 后端

### 4.1 protocol
- `ThreadStatus` 加 `ARCHIVED = "archived"`(软删目标态)。
- `ThreadMeta` 加 `title: str | None = None`(frozen model,默认 None,加字段安全)。

### 4.2 model + migration
- `ThreadMetaRow` 加 `title: Mapped[str | None]`(Text,nullable)。
- migration **0103**:`ADD COLUMN title TEXT NULL`。搜索走 `title ILIKE` 全扫(单租户量小),暂不加 GIN trigram 索引(记为后续,量大再补)。

### 4.3 store(base + sql + memory)
- `update_title(thread_id, title, *, tenant_id) -> bool` —— 改名 + auto-title 共用。
- `delete(thread_id, *, tenant_id) -> bool` —— **硬删 thread_meta 行**(硬删的 checkpoint/run/artifact/workspace 由 API 编排,见 §4.6)。
- `list_by_tenant` / `list_all_tenants` 加:
  - `q: str | None = None` —— `title ILIKE %q%`(None = 不过滤)。
  - `include_archived: bool = False` —— False 时 `WHERE status != 'archived'`(列表默认不显示已归档)。
- auto-title:**不在 store 自动**,由 API 在首个 run 显式调 `update_title`(保持 store 纯粹)。

### 4.4 auto-title(control-plane)
`trigger_run`:若 `meta.title is None` 且 `payload.input`,置 `title = _title_from_input(payload.input)`(单行、strip、截断 ~80 字)。首个 run 生效;后续 run title 已存 → 跳过。改名手动覆盖后同样不再自动改(title 非 None)。

### 4.5 API — list / rename / 软删
- `GET /v1/sessions`:加 query `q`、`offset`;默认 `include_archived=False`;返回 items 含 `title`。去掉「100 静默截断」—— limit 仍有上界(如 200)但配 offset 分页。
- `PATCH /v1/sessions/{thread_id}` `{ title }` → `update_title`;422 校验 title 非空、长度上界。
- `DELETE /v1/sessions/{thread_id}` → **软删 = `update_status(ARCHIVED)`**(可逆,幂等)。

### 4.6 API — 硬删(:purge)
`POST /v1/sessions/{thread_id}:purge` → 不可逆,按依赖顺序 best-effort 编排:
1. `durable_checkpointer.adelete_thread(thread_id)` —— 清 checkpoint/blobs/writes。
2. agent_run 行删除(`run_manager` 按 thread 删;无则记 backlog)。
3. artifacts 软删(复用现有 `artifacts.soft_delete` 循环)+ workspace 卷删除(`supervisor` best-effort)。
4. `threads.delete(thread_id)` —— 删 thread_meta 行(**最后**,前面失败不留孤儿 meta)。

每步 try/except 记 warning 不致命(部分失败不阻断整体);返回删除清单。前端二次确认后才可调。

### 4.7 测试
- store 单测(in-memory + 真 PG):update_title / delete / list q / include_archived(归档不出现)。
- API 测:list q+offset+排除归档;PATCH 改名;DELETE 归档;:purge 编排(mock 各存储,断言调用顺序 + best-effort 容错);auto-title 首个 run 置标题、第二 run 不覆盖、改名后不覆盖。
- 跨租户:归档/改名/删除只作用本租户(RLS + tenant 过滤)。

## 5. PR2 — 前端

- SDK(`api/sessions.ts`):`SessionSummary`(含 `title`)、`renameSession` / `archiveSession` / `purgeSession`、`listSessions({ q, offset, limit })`。
- `SessionHistoryDrawer` 组件:antd Drawer;顶部搜索框(防抖 → `q`);列表行 = `标题 · 最后活跃(相对时间)· 状态徽章 · run-as 属主`;行操作 = 续聊(点行)/ 改名(inline 或 modal)/ 删除(软删)/ 彻底删除(Popconfirm 二次确认 → purge);底部 load-more(offset)。
- wire `PlaygroundTab`:小 `<Select>` → 「会话历史」按钮开 Drawer;`handleResume` 复用;「新会话」保留。
- i18n zh-CN + en;Storybook 故事;Playwright(开抽屉 → 搜索 → 续聊 / 改名 / 软删 / 硬删二次确认);vitest;`tsc -b --noEmit` + axe(表单元素 aria-label)。

## 6. 非目标(记为后续)

- LLM 摘要标题(§3 推迟)。
- title 的 GIN trigram 全文索引(量大再补)。
- 会话历史进入正式 per-user 产品端(本设计限 admin Playground;产品端复用同后端)。
