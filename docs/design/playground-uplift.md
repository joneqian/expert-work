# Playground 产品级补齐(user_id + 多轮 + 每轮观测 + 工作区检视)

## 背景

调试台(`PlaygroundTab.tsx`)目前:建会话 → 单条输入 → 图/文档附件 → 动态 prompt 变量 →
SSE 流(时间线/原始)→ 停止/新建。owner 实测发现两个硬伤,并要求按完整 playground 补齐。

现状核实:
- **多轮其实后端已支持**:`streamRun` 复用同 `thread_id` → checkpointer 自动续接历史。
  问题纯在 UX:`handleRun` 每轮 `setEvents([])` 清空 + 无对话转录 → 看着像单轮。
- **user_id 写死**:`create_session` 用 `resolve_caller_user_id(request)` = 登录管理员自己;
  `CreateSessionPayload` 只有 `agent_name/version`。无法指定别的 user_id → 永远跑 admin 的
  workspace/记忆/episodic,没法验证某用户的 VM/持久化。
- workspace 卷 = `workspace_volume_name(tenant_id, user_id)`(`workspace/base.py`)→ thread.user_id
  直接决定挂哪个卷;记忆/episodic 按 (agent,user) 键控。设 thread.user_id 即挂该用户真实卷。
- `/v1/members` `list_members` 已有(给 user picker);supervisor 有 `read_workspace_file` 无
  「列目录」端点;`UserWorkspaceStore` 有卷元数据(volume_name/size_bytes/deleted)。

## 范围(owner 拍板:做 #1–#4;user_id 两种语义都要)

1. **user_id 选择**(impersonation + 自由填)
2. **多轮对话转录**
3. **每轮用量/成本/延迟/step + 思考轨迹**
4. **工作区检视(v1)**

未选(backlog):审批闸内联 / 配置临时覆盖再跑 / 模拟触发器 payload / 全工作区文件树浏览。

**iter2(已交付,PR4)**:补 #4 余(每轮**延迟**=帧墙钟 / **step_count**=节点最大 /
**成本** ¥=按模型 rate card 算,#774 已砍 plan_tier 故 per-(provider,model)直查)+
**#8** 每轮跳 `/runs/{threadId}/{runId}`(run_id 取自 metadata 帧)+ **#6** 加载历史会话
续聊(`listSessions` 取本 agent 线程下拉 → resume,过去轮次不回放,banner 提示)。

## D1 — user_id override(impersonation + 自由填)

**后端**:`CreateSessionPayload` 加 `run_as_user_id: UUID | None`。
- 授权:仅 `tenant_admin` / `system_admin` 可设 ≠ 自己的 id;普通用户设别人 → 403。两种语义
  (选真实租户用户 / 自由填任意 UUID)**走同一后端路径** —— 都是「把 thread.user_id 设成它」,
  区别只在前端 UI。自由填的 id 不必是已知用户(= 沙箱命名空间);选真实用户 = 读写其真实
  workspace/记忆。
- 审计:`AuditAction.SESSION_IMPERSONATE`(或既有 create 审计带 `run_as_user_id` + `impersonated=true`),
  记 target user_id(audit-over-blocking:允许 admin,全程留痕)。
- 解析:`run_as_user_id` 给定且授权 → 用它;否则 `resolve_caller_user_id`(今天行为)。

**前端**:会话区加 user 控件 = combobox:`/v1/members` 真实用户(email + id)为选项 + 允许
自由输入 UUID。默认 = 自己。会话头显式展示当前生效 user_id(能「看见」在以谁的身份跑)。
换 user → 重建会话。

## D2 — 多轮对话转录(纯前端)

不再每轮清空。会话建模为 `Turn[]`:`{ input, attachments, events[], status, usage }`。
左栏渲对话转录(用户气泡 + assistant 终答气泡 + 每轮可折叠事件流);输入发送后清空,thread
保持;新建会话清空 turns。右栏事件流按「当前/选中轮」或全局 timeline/raw 切换(复用现有
ToolTimeline + EventCard)。

## D3 — 每轮观测(纯前端,数据已在 #847 落地)

从 `updates` 帧解析:
- `usage_metadata` → 每轮 token(in/out/total + cache_read + reasoning)+ 会话累计。
- `additional_kwargs.reasoning_content` → 每轮「思考」折叠块。
- ToolMessage `artifact`(ask_image `vl_usage`/`image_ref`)→ 时间线展示。
- 延迟 = 该轮首帧→end 墙钟。
成本:需 per-model rate,v1 先只显 token(成本入 backlog 或链到用量页)。

## D4 — 工作区检视(v1,证 VM 起了 + 持久化)

**后端**:新 `GET /v1/sessions/{thread_id}/workspace` → 该 thread 的 (tenant, user_id) workspace
元数据(`volume_name` / `size_bytes` / `created_at` / `last_used_at` / `deleted`)+ 该 (tenant,user)
artifacts 列表(复用 `UserWorkspaceStore` + artifacts store)。授权 = caller 拥有 thread 或 admin。
- 全文件树浏览(需 supervisor `list_dir` 端点)入 backlog;v1 用元数据 + artifacts 证 VM/持久化
  (跑一轮写文件后 size 增长 / artifacts 出现 = VM 起了)。

**前端**:playground 加「工作区」面板:volume 名 + size + artifacts;run 后刷新。

## 安全

impersonation + 自由填都受同一闸:仅 admin 角色可设,全审计(audit-over-blocking)。workspace
端点遵 thread 归属 / admin。

## 测试

- 后端:create_session 接受 `run_as_user_id`(admin 通过、普通用户 403、审计落)、workspace 端点
  (元数据 + artifacts、归属/admin 授权、404)。
- 前端:user picker(members + 自由填、默认自己、换 user 重建)、多轮转录(累计不清空、输入清空)、
  每轮 token/思考解析、工作区面板渲染。i18n 三写 + stories + e2e/axe。

## 实施顺序(分 PR)

- PR1 后端:`run_as_user_id` + 审计(已交付)。
- PR2 前端:多轮转录 + user picker + 每轮观测(已交付)。
- **PR3 D4 工作区检视(已交付)**:`UserWorkspaceStore.resolve` 是 upsert(读会建行),
  故新增**只读 `get`**(ABC+sql+memory,不 provision)+ bundle/`app.state` 接线 + 新端点
  `GET /v1/sessions/{thread_id}/workspace`(按 thread.user_id 取 workspace 元数据 +
  artifacts,沿用 `caller_owns_thread` 归属/admin 闸)。前端左栏「工作区」面板:卷名 +
  size + artifacts,thread 重绑/每轮后刷新 + 手动刷新。`workspace=null` 如实表示「该用户
  从未起过 VM」(只读不 provision,非弱 artifacts-only)。全文件树浏览仍 backlog(需
  supervisor `list_dir`)。
