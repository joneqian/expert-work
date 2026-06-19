# Admin UI 导航 IA — 作用域驱动 + 平台/租户分离

> 起因:现有侧边栏把**平台治理**项(租户/平台凭证/平台管理员/MCP目录/平台技能/计价卡/成本分摊)和**租户设置**项(成员/凭证/API密钥/MCP服务器/审计/用量)铺在**同一个扁平「设置」组**里,且**无 nav 级门控**(无权限项也显示,点进去才挡)。system_admin 看着混,普通租户管理员看到一堆点不动的平台项。
>
> 行业共识(GitHub 个人↔组织、Vercel team、Grafana Server-Admin、Keycloak realm):① 不把平台治理和租户设置铺成一个扁平列表;② nav 级角色门控(无权限**隐藏**,非点进去才挡);③ 作用域切换器驱动相关性。
>
> 本稿采用 **②+③ 混合**:作用域切换器决定「操作层级」,侧边栏随层级换;平台项按 `isSystemAdmin` 门控。延伸 [admin-ui-philosophy](./admin-ui-philosophy.md) / [Stream N](../streams/STREAM-N-DESIGN.md)。

## 1. 核心模型:作用域 = 操作层级

TenantSwitcher 的语义从「跨租户数据聚合视图」**升格**为「我在哪个层级操作」(同 GitHub 个人↔组织上下文):

| 选中作用域 | 含义 | 谁可选 |
|-----------|------|--------|
| **具体租户**(home 或其它) | 租户层级:管这个租户的资源 + 设置 | 所有人(其它租户仅 system_admin) |
| **全部租户**(`scope=*`) | **平台层级**:平台治理 + 跨租户总览 | 仅 system_admin |

侧边栏内容是 `(scope, isSystemAdmin)` 的函数:**租户层级显示租户栏目,平台层级显示平台治理栏目**,互不混。

## 2. 侧边栏结构(三组,按层级显隐)

### 租户层级(scope = 具体租户)显示 A + B

**A. 工作区(Workspace)** — 租户的 agent 运转
- 智能体 / 运行记录 / 审批 / 评测样本 / Eval / 记忆 / 产物 / 知识库 / 技能 / 触发器 / Webhook

**B. 租户设置(Tenant Settings)**
- 成员 / 凭证 / API 密钥 / 服务账号 / MCP 服务器 / 审计 / 用量

### 平台层级(scope = 全部租户,system_admin)显示 C

**C. 平台治理(Platform Governance)**
- 租户(管理所有租户) / 平台管理员 / 平台凭证 / MCP 目录 / 平台技能 / 计价卡 / 成本分摊
- 跨租户总览(只读):成员(全部租户) / 运行 / 审计 —— 复用现有 `?tenant_id=*` 能力(P3 跨租户成员视图归入此处)

> 即:在某租户 → 看到 A+B(无平台项);切到全部租户 → 看到 C(无租户 CRUD 栏目)。要管某租户的 agent/成员,切到那个租户。

## 3. 门控规则

| 项 | 可见条件 |
|----|---------|
| 全部租户(scope 选项) | `isSystemAdmin`(TenantSwitcher 已强制) |
| 平台治理组(C) | 仅在平台层级渲染 ⇒ 隐含 system_admin;**再加 `isSystemAdmin` 兜底** |
| 工作区 / 租户设置(A/B) | 在租户层级渲染;**写操作页**仍由页面内 RBAC 控(成员/凭证/审计 等 viewer 只读) |

原则:**无权限 = 看不到(隐藏)**,不是「点进去才被挡」。后端 RBAC/`is_system_admin` 仍是真闸(纵深防御),nav 门控只是减噪。

## 4. 作用域 ↔ 路由对齐(deep-link 友好)

路由所属组隐含「操作层级」。**对齐 scope 到页面**,而非把深链/书签弹走:

- 平台路由 + system_admin 未在平台层级 → **自动切 `scope=*`,留在该页**(书签/直链平台页即用)。
- 平台路由 + 非 admin → 重定向 `/agents`(无权;页面也有服务端门控)。
- 在平台层级访问租户路由 → 自动切回 home 租户,留在该页。
- 已对齐的路由不动(每个分支让下一次渲染成 no-op,无抖动/无循环)。
- 逻辑集中在 `Shell.useScopeRedirect`。

> 早期版本是「弹到落地页」,但会破坏深链(书签平台页被弹走);改为自动对齐 scope。

## 5. 现有项归位(迁移对照)

| 现「设置」项 | 新归属 |
|---|---|
| 租户 | C 平台治理 |
| 平台凭证 | C |
| 平台管理员 | C |
| MCP 目录 | C |
| 平台技能 | C |
| 成本分摊 | C |
| 计价卡 | C |
| 成员 | B 租户设置(平台层级另有「成员(全部租户)」只读总览) |
| 凭证 | B |
| API 密钥 | B |
| 服务账号 | B |
| MCP 服务器 | B |
| 审计 | B |
| 用量 | B |
| 顶部 11 项资源 | A 工作区 |

## 6. 落地范围(本次)

- `Sidebar.tsx`:nav 数据加 `group`(workspace/tenant-settings/platform)+ `requiresPlatformScope`/`requiresSystemAdmin` 标记;渲染按 `(scope, isSystemAdmin)` 过滤分组 + 组标题。
- scope 联动重定向(SidebarLayout 或路由守卫)。
- i18n:加组标题 `nav.group_workspace` / `nav.group_tenant_settings` / `nav.group_platform`(zh+en)。
- CommandPalette 同步门控(平台项非 system_admin 不出)。
- 测试:vitest 覆盖 (租户 scope 见 A+B 不见 C / 平台 scope 见 C 不见 A+B / 非 admin 选不到全部租户也不见 C)。

## 7. 不做(本次范围外)

- 跨租户总览(运行/审计聚合)新页 —— 仅保留已有的成员 `?tenant_id=*`;运行/审计聚合留待后续。
- 独立超管子域/路由(模式①)—— 不拆两套界面,维持单控制台。
- 租户设置项的 nav 级 RBAC 细分(viewer 隐藏写页)—— 维持页面内门控,本次只做平台/租户层级分离。

## 8. 开放确认点

1. 平台层级是否**完全隐藏**工作区(A)?本稿是。备选:保留只读跨租户资源总览(成本更高)。
2. 「成员(全部租户)」只读总览放 C 组(本稿),还是从「租户」页下钻?本稿独立项,简单。
3. 落地页选 `/settings/tenants`(平台)/ `/agents`(租户)是否合适?
