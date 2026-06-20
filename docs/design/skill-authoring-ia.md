# Skill 创作 IA — 导入为主 + 在线编辑器(平台/租户对齐)

> 状态:草案(待评审)。范围:admin-ui 技能创作/编辑信息架构 + 支撑它的后端端点。
> 延伸 [admin-ui-nav-ia](./admin-ui-nav-ia.md) / [admin-ui-philosophy](./admin-ui-philosophy.md) / Stream X(平台技能)。

## 0. 一句话

技能本质是**文件夹包**(`SKILL.md` + `scripts/` + `references/` + `assets/`)。因此:**创建 = ZIP 导入**;**手建空壳删除**;**在线编辑器只负责"迭代已导入技能"**(含 `SKILL.md` 可编);**平台补齐这套编辑器,与租户对齐**。

## 1. 背景与事实(为什么改)

### 1.1 技能是文件夹,不是一个 SKILL.md

Anthropic Agent Skill 官方结构:一个 kebab-case 文件夹,内含必需的 `SKILL.md`(YAML frontmatter: `name`≤64 / `description`≤1024 + markdown 正文)+ 可选 `scripts/`(可执行)/ `references/`(按需文档)/ `assets/`(模板)。核心机制是**渐进披露**三级(metadata → 正文 → 按需加载的 bundle 文件)。复杂能力靠 bundle 的脚本/参考文件撑起,简单 `SKILL.md` 撑不起。

helix `SkillVersion` 已对上:`prompt_fragment`≈正文、`tool_names`≈allowed-tools、`supporting_files`≈scripts/references/assets、`lazy_load`≈渐进披露、`.skill` ZIP≈文件夹打包。

### 1.2 现状缺陷(评估结论,带证据)

**手建是死胡同**:
- `新建`(`SkillsList.tsx:165` `createSkill`)只建 name/desc/category;后端 `create_skill`(`skills.py:292`)不建初始版本 → `latest_version=0`,无 version。
- 空壳详情页编辑器 + 加文件都门控在 `selectedVersion !== null`(`SkillDetail.tsx:468,530`)→ 新壳**编辑器整个不渲染**,只剩 "no versions"。
- **没有"加版本" UI**:`addSkillVersion` SDK 存在(`skills.ts:214`)但全仓无组件调用。
- **`SKILL.md`/提示词在编辑器里只读**(`FileEditor.tsx:5-9,303-311`)。

⇒ `新建` → 落到一个没版本、无法加版本、核心提示词只读的页面。手建产不出能用技能,最终仍需 ZIP 导入。

**平台比租户更弱**:平台技能页(`SettingsPlatformSkills.tsx`)只有字段表单 + Manage 抽屉(prompt-only 加版本),**完全没有在线文件编辑器**;后端 `/v1/platform/skills` 缺 supporting-file/export 端点。

**好的部分**:`FileEditor` 对 supporting 文件的编辑体验扎实(Monaco 10 语言高亮、diff 开关、二进制占位、未保存离开拦截 `SkillDetail.tsx:238`、每次保存出**不可变新版本**);`AddFileModal` / `Rename` / `Delete` 完整;版本历史 / 导出 / 治理面板(Governance/Lineage/EvalEvidence)齐全。这套的价值在**迭代已有技能**,不在从零创作。

### 1.3 决策推理

从零在浏览器里创作多文件 + 脚本技能,体验不如本地 IDE + 打 zip;且把手建补成"真创作器"(提示词可编 + 首版本创建 + 空树建文件)工作量大、收益低。真实创作路径 = 本地编写打包 / 从市场仓库取 / 飞轮程序化生成 → 都落到**导入**。而在线编辑器的真正价值 = 对已导入技能做**快速迭代**(改一个参考文档 / 修脚本一行 / 改提示词),免去全量重打 zip。

## 2. 决策

| # | 决策 |
|---|---|
| D1 | **删除手建(空壳创建)** —— 平台 + 租户两页都删。低 ROI 且是死胡同。 |
| D2 | **创建技能 = ZIP 导入** —— 两页主操作(primary 按钮 + 空态首推)。 |
| D3 | **`SKILL.md`/提示词在 UI 可编** —— `FileEditor` 编 `SKILL.md` 保存即出新版本(走 add_version 写 `prompt_fragment`)。 |
| D4 | **平台补齐在线编辑器** —— 复用租户详情页组件(经适配器),平台拿到与租户同款迭代能力。 |
| D5 | **从零创作不在浏览器做** —— 交给本地 IDE + zip / 飞轮 / 市场。 |

## 3. UX(两页一致)

### 3.1 列表页工具栏
- **导入 .skill**(`type=primary`,推荐)
- 刷新
- (移除「新建技能」按钮)

### 3.2 空状态
- 主 CTA = **导入 .skill**;副文案点明"从本地 `.skill` 包导入,或参考文档结构自建后导入"。
- 不再有「新建技能」按钮。

### 3.3 详情页(创作/迭代核心)
- 平台新增详情页,与租户 `SkillDetail` **同一套组件**:Hero+状态、Metadata、Governance/Lineage/EvalEvidence、版本选择器+导出、双栏(FileTree + FileEditor)。
- `FileEditor`:`SKILL.md` 由只读 → **可编**(D3),保存出新版本;supporting 文件编辑/加/改名/删 维持现状。
- 平台版的 lifecycle(draft/active/archived)+ pin + `required_tier` 进详情页(取代原 Manage 抽屉)。

## 4. 后端改动

### 4.1 平台补 4 个端点(镜像租户 `/v1/skills`)
| 端点 | 说明 |
|---|---|
| `GET /v1/platform/skills/{id}/versions/{v}/supporting-files/{path}` | 读单文件 body |
| `PUT …/supporting-files/{path}` | 增/改文件 → 新版本 |
| `DELETE …/supporting-files/{path}` | 删文件 → 新版本 |
| `GET …/versions/{v}/export` | 导出 .skill ZIP |

实现:照搬租户路由体,store 换平台版(`add_platform_version` / `get_platform_version_by_number` 已存在,import 路径已用),`bypass_rls_session()` 写 NULL-tenant + `is_system_admin` 闸;复用共享 `scan_for_threats` / `is_high_risk_skill_version` / `compute_content_hash`。约 350–400 行 + 测试。

### 4.2 SKILL.md 可编的写路径(D3,两侧)
`FileEditor` 保存 `SKILL.md` 时:以当前版本为基,`prompt_fragment` 换成新内容,其余字段(tool_names/supporting_files/...)沿用 → `add_version`/`add_platform_version` 出新版本。可复用现有 add-version 路径;前端把 `SKILL.md` 的保存路由到"加版本(仅改 prompt_fragment)"而非 supporting-file PUT。需确认后端 add_version 是否允许"只改 prompt 其余继承"——若否,前端读当前版本字段回填即可。

## 5. 前端架构

### 5.1 `skillApi` 适配器(解耦租户/平台)
现状:`SkillDetail` + `skill_detail/*` 直接 `import {...} from "../api/skills"`(写死租户)。引入接口:

```ts
interface SkillApi {
  getSkill, listVersions, getVersion,
  getSupportingFile, putSupportingFile, deleteSupportingFile, renameSupportingFile,
  exportVersion, patchStatus, addVersion, // addVersion 供 SKILL.md 保存
}
```

两实现:`tenantSkillApi`(skills.ts)/ `platformSkillApi`(platform-skills.ts + 4.1 新端点)。`SkillDetail` 与子组件经 prop/context 注入 `api`,不再直接 import。

### 5.2 路由 + 接线
- 平台技能详情路由(如 `/settings/platform-skills/:skillId`),`<SkillDetail api={platformSkillApi} backTo=.../>`。
- 租户保持 `/skills/:skillId`,`api={tenantSkillApi}`。
- nav/SE-8 接线:列表行点击进详情;退场平台 Manage 抽屉。

### 5.3 删除项
- 租户:`SkillsList` 的新建抽屉 + `createSkill` 入口(SDK 函数可留待清理)。
- 平台:`PlatformSkillCreateDrawer` + `PlatformSkillManageDrawer` 的 add-version 表单(lifecycle/pin 迁详情页);相关 SDK `createPlatformSkill`/`addPlatformSkillVersion(表单用法)` 视情清理。
- i18n:清理 `*.create_*` / add-version 表单相关死键(en+zh 同步,避免类型漂移)。

## 6. 分期(已全部交付,2026-06-20)

1. ✅ **Phase 0 — ADR 评审 + #709**(envelope-vs-raw 修复 + 平台导入按钮)。#710/#711 ADR 合入。
2. ✅ **Phase A — 后端**(#712):平台 4 端点(supporting-file GET/PUT/DELETE + export)+ SDK。
3. ✅ **Phase B — 前端解耦**(#713):抽 `SkillApi`(tenant/platform 两实现),`SkillDetail`+子组件改注入式;租户回归不变。
4. ✅ **Phase C — 平台详情页**(#714):路由 `/settings/platform-skills/:skillId`,退场 Manage 抽屉,`variant=platform` 隐藏飞轮面板/去 stale/显示 tier。
5. ✅ **Phase D — 删手建 + SKILL.md 可编**:D-1(#715)删两页手建空壳 + 导入提 primary + 空态 CTA;D-2(#717)SKILL.md 可编(后端继承端点 `PUT .../versions/{v}/prompt` 杜绝丢 supporting_files)。
6. ✅ **Phase E — 收尾**:删 rename 配对孤儿键 `detail_skill_md_readonly_hint`;ADR/分期状态同步;e2e 已随各 Phase 增量落地。**已知 cosmetic 债**:create-flow / 抽屉相关 i18n 键(`skills.create*` / `platform_skills.add*` / `*_required` 等,grep 验证零引用)暂留 —— 与活键交错、短名跨 namespace 共享,逐键删 churn 大且无 i18n-unused 门禁,延后批量清理。

> 旁注:dep-bump #716(langsmith/pydantic-settings/vcrpy 解 3 CVE)在 Phase D 期间解 pip-audit backlog 阻塞。

## 7. 风险

- **前端解耦面广**(8 组件 + 测试/stories)——机械但量大;有租户成熟实现做参照,风险中等。
- **删手建是反向变更**(此前 #709 思路是"加导入按钮保留新建")——需在 PR 显式标注需求级变更。
- **SKILL.md 可编**要确认 add_version 的"部分更新"语义,避免误丢 supporting_files。
- 平台 supporting-file 写入 NULL-tenant + RLS:必须 `bypass_rls_session` + system_admin 闸,集成测真 PG 验证。

## 8. 测试计划

- 后端:平台 4 端点单测(含 NULL-tenant RLS、threat scan 拒绝、content_hash 幂等、high_risk 重算);SKILL.md 保存出新版本且 supporting_files 不丢。
- 前端:`SkillApi` 双实现下 `SkillDetail` 行为一致(注入 mock api);平台详情页渲染/编辑/导出;空态/导入主操作;删手建后无残留入口。
- e2e:平台导入 ZIP → 详情编辑文件 → 改 SKILL.md → 导出,全链路。

## 9. 不做(本轮外)

- 浏览器内"从零建文件夹技能"的引导式向导(D5:交给本地 + zip)。
- 技能市场/目录浏览导入(另立)。
- 提示词的富文本/模板化编辑(纯文本 Monaco 足够)。

## 10. 确认点(已拍板 2026-06-19)

1. **平台详情页路径 = `/settings/platform-skills/:skillId`**。与租户 `/skills/:skillId` 对称、与平台页 `/settings/*` 前缀一致。退场 Manage 抽屉后,列表行「Manage」改为跳此页;TenantScope 守卫归 platform 组(`navModel.groupForPath`)。
2. **SKILL.md 保存 = 后端继承模式,复用 `put_supporting_file` 同款代码模式**:读当前版本 → 仅换 `prompt_fragment` → 其余字段(tool_names/supporting_files/required_models/lazy_load…)继承 → 重算 content_hash/high_risk → `add_version`。前端只发新 prompt,**继承逻辑集中在后端,杜绝丢 supporting_files**。Phase A 第一件事先核 `skills.py` 的 `add_version`/store 签名确认可继承(租户 `put_supporting_file` 已是此模式,可直接照搬)。
3. **不留"空白技能"快速创建**(D1 全删,创建一律导入)。理由:老手建是死胡同才删;配合 D3(SKILL.md 可编)+ 未来若加"从模板新建(新建即建 v1 + 跳详情)"则不再是死胡同。**先不背**,Phase D 删手建;真发现纯提示词小技能高频再补"从模板新建"(成本低、随时加)。
