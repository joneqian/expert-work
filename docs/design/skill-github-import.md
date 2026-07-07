# 技能从 GitHub 导入 — 方案 A(URL/命令式安装)

> 状态:设计(待评审)。范围:平台侧"从 GitHub 拉取技能并导入平台库"端点 + resolver + SSRF 收窄。
> 延伸 [skill-marketplace-ia](./skill-marketplace-ia.md)(订阅链已交付 #722-725)/ [skill-authoring-ia](./skill-authoring-ia.md)(ZIP 导入管线 #709-718)。

## 0. 一句话

把现有"上传 `.skill` ZIP 导入平台库"的入口,扩一个**从 GitHub 拉取**的变体:给 `owner/repo`(或 GitHub URL,或 `skills.sh` URL 当糖)+ 一个 `skill` 选择器(技能名)→ 后端下 GitHub 归档 → **扫描仓里所有 `SKILL.md` 建 name→path 索引,按 `skill` 匹配**抽出该子树 → 复用**全部**现有导入管线(解包/审核/strict 扫描/建版本)。唯一新代码 = **取字节 + resolver + scan-and-match 抽取**;唯一新风险 = **SSRF**,靠"只允许 GitHub"把面塌缩成单域名。

> 设计借鉴 `npx skills add <repo> --skill <name>`:精准定位靠**「扫仓里 SKILL.md + 按名匹配」**,而非要求用户给精确内部路径。monorepo(一仓多技能,如 `vercel-labs/skills` 下 `skills/<name>/`)对用户透明。

## 1. 背景:为什么这条 URL 不能直接 fetch

`npx skills add owner/repo`(Vercel skills 生态)能装,是因为 CLI 把**注册表名 → git → clone 子目录**。`https://www.skills.sh/<owner>/<repo>/<skill>` 是注册表**网页(HTML)**,不是包;真包在 `github.com/<owner>/<repo>` 子目录 `skills/<skill>/`(monorepo 一仓多技能)。直接 fetch 网页 → 拿 HTML → `parse_skill_zip` 解不了。

**format 兼容已确认**:`parse_skill_zip` 的 canonical 布局就是 `SKILL.md`(YAML frontmatter + Markdown 正文),正是 Vercel/Anthropic 通用格式。⇒ 缺口纯在"取 + 抽路径",不在格式。

## 2. 决策

| # | 决策 |
|---|---|
| G1 | **只支持 GitHub 源(M0)**,不做任意 URL。SSRF 面从"任意 host"塌缩成固定 `codeload.github.com`(host allowlist),安全红利显著。任意 URL / 其他 forge(GitLab…)留后续。 |
| G2 | **平台侧限定**(system_admin → 平台 NULL-tenant 库)。不开放给租户自取(不可信内容拉取风险大);租户经 marketplace 订阅平台库即可。 |
| G3 | **复用全部下游管线**:resolver 产出一个 `.skill` 风格 zip blob → 灌进与 ZIP 上传**同一条** parse→moderate→strict-scan→create/version 路径(抽成共享 helper)。 |
| G4 | **skills.sh URL 仅当语法糖**:纯字符串映射成 `owner/repo` + `skills/<skill>` 子路径,**不爬该站**。 |
| G5 | **仅公开仓(M0)**。私有仓需 GitHub token(经 SecretStore),留后续。 |

## 3. Resolver

入参 `source: str` + 可选 `skill: str`(技能名选择器)+ 可选 `ref`(默认仓库默认分支)。source 归一化成 `(owner, repo, ref)`,**不再要求内部 subpath**——具体技能由 §5 的 scan-and-match 用 `skill` 定位:

```
① owner/repo                                  → (owner, repo)
② https://github.com/owner/repo               → ①
③ https://github.com/owner/repo/tree/<ref>/…  → ① + ref 从 URL 取
④ https://www.skills.sh/owner/repo/<name>     → source=(owner,repo) + skill="<name>" (G4 糖)
```

校验(防注入):`owner`/`repo` 匹配 `^[A-Za-z0-9._-]+$`;`ref` 匹配 `^[A-Za-z0-9._/-]+$`;`skill` 匹配 `^[A-Za-z0-9._-]+$`。任一不过 → 400(通用文案,Oracle 防御)。

## 4. 取字节(SSRF 收窄)

`GET https://codeload.github.com/<owner>/<repo>/zip/<ref>` → zip 字节(用 zip 不用 tar,留在 `zipfile` 内,复用 zip-slip 心智)。

- **host 钉死**:URL 由我们用校验过的片段拼,host 恒为 `codeload.github.com`;再过 `validate_remote_url`(挡私网,见 [expert_work.common.url_validation])兜底。即"allowlist 单域名 + 私网兜底"双保险。
- **下载体积上限**:复用现有 skill zip size cap(`_MAX_*`),流式读到上限即断(防 zip 炸弹/超大仓)。
- **超时**:固定短超时(如 15s)。
- 404 / 非 200 → 404「repo/ref/skill 未找到」。

## 5. scan-and-match + 重打包

GitHub zip 根是 `<repo>-<ref>/`。流程(借鉴 `npx skills --skill <name>`):
1. 解 zip,**遍历所有 `SKILL.md`** → 列出每个技能 `(relpath, basename, prefix)`(relpath = 剥掉 `<repo>-<ref>/` 根的目录路径,如 `skills/find-skills`;basename = 末段,即 `npx --skill <name>` 约定)。**允许同名**(一仓可在两路径下放同名,故用列表不去重)。
2. 按 `skill` 选择器匹配(**路径或 basename**):
   - 先精确 relpath 匹配(`skills/find-skills`),再退回 basename 匹配(`find-skills`)。
   - **未给 `skill` 且仓内仅一个技能** → 默认装它。
   - 未给 `skill` 且多技能 → 400「请用 `skill=` 指定(名或路径)」+ 列候选 relpath。
   - 给了 `skill` 但 0 命中 → 404 列候选 relpath。
   - basename 撞多个路径 → 400「请用完整路径指定」+ 列冲突 relpath(用户改填 `skills/<name>`)。
3. 把命中子树**重打包**成 `.skill` 风格 zip blob(`build_skill_zip` 或等价),路径剥到技能根。
4. zip-slip 防护:抽取时拒绝任何归一化后逃出根的条目;`SKILL.md` 扫描限制最大遍历条目数(防超大仓 DoS)。

## 6. 端点 + 管线复用

新端点 `POST /v1/platform/skills/import-from-github`(system_admin,`_principal` 闸):
```
body: { source: str, skill?: str, ref?: str }
 → resolver(§3) → fetch(§4) → scan-and-match 抽取+重打包(§5) → blob
 → _ingest_platform_skill_blob(blob, source_label="github:<owner>/<repo>@<ref>#<skill>")
```
**重构**:把现有 `import_platform_skill` 的「parse→name 校验→moderate→strict scan→idempotent create/version」抽成共享 `_ingest_platform_skill_blob(blob, *, request, source)`;ZIP 上传端点与本端点都调它。审计 `SKILL_CREATE` 的 `details.source` 标 `github`(不新增 AuditAction;复用 `skill` 资源)。idempotency 同现状(content_hash 命中 → 200 created=false)。

## 7. 前端(Phase 2)

admin-ui 平台技能页:ZIP 导入按钮旁加「从 GitHub 导入」——弹窗粘 `source` + `skill`(+ 可选 ref),调新端点。后端返「多技能请指定 + 候选列表」时,前端可把候选渲成可选项再提交。复用现有 import 结果提示。SE-8 接线点同平台技能页既有面(无新路由)。

## 8. 分期

1. **Phase 1 — 后端**:resolver + GitHub fetch(SSRF allowlist + size cap + timeout)+ 子目录抽取/重打包 + 端点 + 共享 ingest helper 重构 + 单测(resolver 各形态 / SSRF 拒非 GitHub / 缺 SKILL.md / monorepo 子路径 / 重打包正确 / 复用管线 happy+审计)。fetch 用可注入的 client,单测 mock,不真打网。
2. **Phase 2 — 前端**:平台技能页「从 GitHub 导入」入口 + e2e。

## 9. 不做(本轮外)

- 任意 URL / 非 GitHub forge(G1)。
- 私有仓 token(G5)。
- 租户侧自取(G2)。
- skills.sh 站 API 爬取(G4 只做 URL 映射糖)。
- 自动同步/升级(装一次即快照,与平台技能现状一致;升级 = 再导入出新版本)。

## 10. 风险

- **供应链(不可信内容)**:GitHub 技能 = 不可信。已被现有 **strict 威胁扫描 + high_risk 闸**覆盖(与 ZIP 导入同管线),复用即得防护;平台侧 system_admin 操作再加一层人审语义。
- **SSRF**:G1 单域名 allowlist + `validate_remote_url` 私网兜底;URL 全由校验片段拼,不接受用户给的任意 host。
- **zip 炸弹/超大仓**:size cap 流式断 + 子树抽取只取目标技能。
- **format 漂移**:Vercel/Anthropic SKILL.md 与我们 canonical 一致;若某仓用非标布局 → parse 阶段 400,不污染库。
