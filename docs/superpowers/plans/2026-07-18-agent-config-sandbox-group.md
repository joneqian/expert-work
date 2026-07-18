# Agent 配置页 PR4:沙箱与资源组 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 沙箱与资源组升级为 curated pane——但**只可视化真实生效字段**(用户 2026-07-18 拍板):`filesystem.persistent_workspace` 开关 + 「平台实际生效值」机制说明面板 + 声明性字段 YAML 指引。network 三字段已在安全与防护组(PR3),不重复。

**Architecture:** SandboxSection curated pane = 单 `PolicyFieldList`(1 个 switch FieldDef)+ 两段说明文案(平台机制 / 声明性字段);form_model 新增 `readSandboxFs`/`patchSandboxFs` 投影,**required 块语义与 #1017 一致**(清空保留 `{}` 不删块);ManifestEditor `CURATED_GROUP_PANES` 加 `sandbox`。

**Spec:** docs/superpowers/specs/2026-07-18-agent-config-page-redesign-design.md(PR4 = 分期段第三组;范围经运行期语义研究后收窄,用户拍板「只可视化活字段」)

## Global Constraints

- PR1-PR3 契约全守:FieldRow props 不变;YAML round-trip 未投影键保留;新投影字段配 round-trip 测试;i18n 三处(interface+en+zh-CN)先 grep 撞键;e2e 选择器 `cfg-nav-<id>`/`cfg-pane`。
- **required 块规则(#1017 教训)**:`SandboxSpec.filesystem` 与 `sandbox` 本身都是后端必填 pydantic 字段(agent_spec.py:334/:1126)。patch 清空 filesystem 后必须保留 `filesystem: {}`(合法——FilesystemSpec 全字段有默认),**绝不删块**;sandbox 本就不存在时不物化空父块。
- 每任务:`cd apps/admin-ui && pnpm exec vitest run src/components/manifest-editor && pnpm typecheck`;终门全量 + build + storybook + Playwright。
- IDE 诊断常 stale——真 tsc/vitest 定论。
- 文案已对照真实运行期代码(见下节),照抄 verbatim,不做"改进"。

## 运行期语义事实(2026-07-18 全库溯源,文案依据)

- `filesystem.persistent_workspace`(默认 false):**唯一生效点** `agent_factory.py:951`——只开 CM-0 计划投影(每轮结束写 PLAN.md/TODO.md/MEMORY.md 到用户工作区 + run 开始回读),且需部署接了 supervisor。**不控制持久性**:带 user_id 的 run 无条件挂该用户持久 /workspace 卷(`supervisor.py:769-788`),系统 run 恒 tmpfs,均与本开关无关。
- **声明性死字段(运行时零消费者)**:`runtime`/`image`/`image_build`/`image_variant`(deprecated)/`resources.cpu·memory·pids·timeout_s`/`filesystem.readonly_root·writable·mounts`/整个 `spec.code` 块。实际值全在 sandbox-supervisor env(`settings.py`):镜像恒 `expert-work-sandbox:dev`、默认 1.0 CPU / 1024 MB / 128 pids、单命令超时默认 30s 上限 300s(`orchestrator/tools/sandbox.py:754-758`)、rootfs 无条件 `--read-only`、可写恒 /workspace+/tmp(`runtime_provider.py:119-125`)、OCI runtime 由部署 env `EXPERT_WORK_SANDBOX_OCI_RUNTIME` 定。
- 模板现状:admin-ui 新建模板写 `resources: {cpu: "1.0", memory: "1Gi"}` + `readonly_root: true` + `writable: ["/workspace"]`(defaults.ts:34-42)——全是声明性,留 YAML 无害。

---

### Task 1: form_model 投影 sandbox.filesystem.persistent_workspace

**Files:**
- Modify: `apps/admin-ui/src/components/manifest-editor/form_model.ts`
- Test: `apps/admin-ui/src/components/manifest-editor/__tests__/form_model.test.ts`(追加)

**Interfaces(Produces):**

```ts
// AgentManifest.spec.sandbox 类型增:filesystem?: { persistent_workspace?: boolean; [k: string]: unknown }
export interface SandboxFsFields {
  persistentWorkspace?: boolean;
}
export function readSandboxFs(m: unknown): SandboxFsFields;
export function patchSandboxFs(m: unknown, patch: Partial<SandboxFsFields>): AgentManifest;
```

- reader 返回 RAW 存储值(undefined=未设,显示层给 effective 默认 false)。
- patch 语义:`"persistentWorkspace" in patch` 判定;undefined 删键;**filesystem 清空后保留 `{}`**(required 块,镜像 #1017 的 patchSecurity network 处理:`mergeBlock(...) ?? {}`);sandbox 未知键(runtime/resources/network 等)原样保留;sandbox 本就 absent 且 patch 结果净空 → 不物化。

- [ ] **Step 1: 失败测试**(设 true round-trip 经 YAML;清空(undefined)→ `filesystem` 保留为 `{}` 且 sandbox 未知键(runtime/resources)不动;filesystem 内未知键(readonly_root/writable)patch 后保留;absent sandbox + 净空 patch → 不物化 sandbox;与 patchSecurity 的 network 投影共存互不干扰)
- [ ] **Step 2: 实现**
- [ ] **Step 3: scope vitest + typecheck 全过;commit `feat(admin-ui): form_model 投影 sandbox.filesystem.persistent_workspace`**

### Task 2: SandboxSection curated pane + 文案

**Files:**
- Create: `apps/admin-ui/src/components/manifest-editor/groups/SandboxSection.tsx`
- Modify: `apps/admin-ui/src/components/manifest-editor/ManifestEditor.tsx`(`CURATED_GROUP_PANES` 加 `sandbox`)
- Modify: locale interface + `en.ts` + `zh-CN.ts`(命名空间 `sandbox_group`,先 grep 撞键)
- Test: `groups/__tests__/SandboxSection.test.tsx` + `__tests__/ManifestEditor.test.tsx`(追加 sandbox 组断言)

结构(无 Collapse,内容少直接平铺):①`PolicyFieldList` 单字段(persistent_workspace)②「平台实际生效值」说明块(testid `sandbox-platform-note`)③「声明性字段」说明块(testid `sandbox-declarative-note`)。

FieldDef:

```ts
{ fieldId: "sandbox.filesystem.persistent_workspace", i18nKey: "sandbox_group.pw",
  valueKey: "persistentWorkspace", kind: "switch", effectiveDefault: false }
```

文案(zh verbatim;en 忠实对译):

**persistent_workspace**
- `pw_label`「计划投影到工作区」
- `pw_brief`「每轮结束把 PLAN.md / TODO.md / MEMORY.md 写入用户工作区,run 开始时回读——沙箱内外共享任务进度」
- `pw_impact`「仅控制计划/状态投影,不控制文件持久性:带 user_id 的运行本就自动挂载该用户的持久 /workspace 卷(闲置回收后下次自动恢复),系统运行(无 user_id)恒为临时空间——均与本开关无关。开关生效还需部署接入 sandbox supervisor。」
- (switch 无默认徽章,与既有 switch 语义一致)

**平台实际生效值说明**(`platform_note_title`「平台实际生效值」+ `platform_note_body`,多行 Text):
「沙箱实际运行参数由平台部署决定,manifest 不参与:镜像=平台统一镜像(Python+办公/数据/媒体全量);资源=supervisor 环境配置(默认 1.0 CPU / 1024 MB 内存 / 128 进程);单命令超时默认 30 秒、工具调用可指定、上限 300 秒;根文件系统恒只读,可写路径恒为 /workspace 与 /tmp;容器运行时(gVisor/runc)由部署环境变量决定。」

**声明性字段说明**(`declarative_note`):
「manifest 中的 runtime / image / image_build / resources / readonly_root / writable / mounts 及 code 块当前为声明性字段:通过校验但运行时不读取,留在 YAML 中无害。调整实际资源限额请修改平台部署配置(sandbox-supervisor 环境变量)。」

- [ ] **Step 1: 失败测试**(pane 渲染 `data-field-id="sandbox.filesystem.persistent_workspace"`;开关拨 true → manifest `spec.sandbox.filesystem.persistent_workspace === true`;拨回 false(默认)→ 键删且 filesystem 保留 `{}`(存量 manifest 场景);两个 note testid 渲染;ManifestEditor 点 `cfg-nav-sandbox` → pane 出现且不再是 pending hint)
- [ ] **Step 2: 实现 + i18n 三处**
- [ ] **Step 3: scope vitest + typecheck;commit `feat(admin-ui): 沙箱与资源组 —— 计划投影开关 + 平台生效值说明(只可视化活字段)`**

### Task 3: 终门

- [ ] `pnpm typecheck && pnpm exec vitest run`(全量)+ `pnpm build` + storybook build + Playwright manifest 相关 spec;有修才 commit `test(admin-ui): PR4 终门`

## Self-Review 已核

- 「只可视化活字段」有用户拍板(2026-07-18 AskUserQuestion)✓
- persistent_workspace 文案对照真代码(agent_factory.py:951 + supervisor.py:769-788),纠正了字段名的误导(名叫 persistent 实为投影开关)✓
- required 块规则从 #1017 直接继承,测试点名 filesystem 保留 `{}` ✓
- 死字段清单与研究结论一致;image_variant deprecated 不进说明文案(避免鼓励使用)——declarative_note 列的是模板常见键 ✓
- 无 TBD;新增 i18n 键全给 verbatim zh ✓
