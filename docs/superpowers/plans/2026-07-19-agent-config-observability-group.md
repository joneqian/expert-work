# Agent 配置页 PR7:触发器与可观测组 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 最后一组(触发器与可观测)落地,照「只可视化活字段」拍板:唯一活字段 `spec.cache.enabled`(LLM 响应缓存)建开关;`spec.triggers`(manifest 接线未实现)与 observability 三死字段走真相 note;**纠偏既有 `af-trajectory-recording` 假开关文案**(标志位从未被读,录制只由部署 ObjectStore 配置决定)。

**Architecture:** T1 = form_model 投影 `spec.cache`(default_factory 可选块,标准语义);T2 = ObservabilitySection curated pane(1 switch FieldDef + 2 note)+ `CURATED_GROUP_PANES.observability` + groups.ts 补搜索词 + trajectory 文案纠偏;T3 = 终门。

**Spec:** docs/superpowers/specs/2026-07-18-agent-config-page-redesign-design.md(PR7 = 分期段末组;范围经溯源收窄,同 PR4 沙箱组先例)

## Global Constraints

- PR1-PR6 契约全守:FieldRow/PolicyFieldList props 不变;YAML round-trip 未投影键保留 + round-trip 测试;i18n 三处先 grep 撞键;e2e 选择器契约;测试环境解析 en locale。
- `spec.cache` 是 `default_factory=CacheSpec` 可选块(agent_spec.py:1182)——**标准 mergeBlock drop-empty 语义**(非 required/存在语义块;absent=默认全开,与 `{}` 等价,删空块无害)。
- observability 组现为 pending hint(sections: [],ManifestEditor 注释列为 Phase-2 占位)——本 PR 是**最后一个 pending 组**,落地后 pending-hint 路径失去所有静态空组:检查依赖该路径的测试(PR4 曾 repoint 到 observability,现在没地方可 repoint)——**pending-hint 分支成死代码即删除**(含其测试与相关注释;这是本 PR 的必要清理,非 drive-by)。
- 每任务:`cd apps/admin-ui && pnpm exec vitest run src/components/manifest-editor && pnpm typecheck`;终门全量 + build + storybook + Playwright manifest 2 spec。
- IDE 诊断常 stale——真 tsc/vitest 定论;CJK grep -a。
- 文案对照真实运行期代码(下节),照抄 verbatim。

## 运行期语义事实(2026-07-19 全库溯源,文案依据)

- **`spec.cache.enabled`(bool=True)唯一活字段**:middleware_assembly.py:126 `if env.response_cache is not None and spec.spec.cache.enabled:` → 挂 LLMCacheLookup/Store 双 middleware;命中时 builder.py:733 直接跳过 provider/router 调用(含回退链)。schema 注明时效敏感提示词的 Agent 须关。**⚠️ 与 `model.cache_enabled`(PR6 加的 Anthropic prompt caching)是两个字段两回事**:那个省输入 token,这个复用完整回答。
- **`spec.triggers` manifest 接线未实现**:schema/docstring 声称 deploy 时 reconcile 进 agent_trigger 表(source="manifest"),但该字面量只存在于 docstring——全库无消费者;唯一建行路径是 `/v1/triggers` API(source="api",triggers.py:299)。cron 调度与 webhook 触发本身是活的(scheduler.py:219/triggers.py:463),但只对 API 建的行生效。
- **observability 三字段全死**:`trace`/`log_level`/`redact_fields` 零消费者(追踪经 common.observability 无条件接线;各服务 log_level 是独立 env 设置;真 PII 脱敏走 env.redact_text→PIIRedactorMiddleware,与 redact_fields 无关)。
- **`policies.trajectory_recording`(bool=True)标志位从未被读**:TrajectoryRecorder 真在跑(sse.py:772,写 ShareGPT JSONL 到 ObjectStore),但只由 `recorder is None`(=部署 ObjectStore 配置)门控;sse.py:760 docstring 声称的 "manifest opt-out" 未接线。**既有 UI `af-trajectory-recording`(governance→Advanced)是假开关**——关了录制照跑,文案必须纠。
- 存量 manifest:5 个全带 `observability.log_level: info`(全部无效);无 triggers/cache 块。

---

### Task 1: form_model 投影 spec.cache

**Files:**
- Modify: `apps/admin-ui/src/components/manifest-editor/form_model.ts`
- Test: `__tests__/form_model.test.ts`(追加)

**Interfaces(Produces):**

```ts
// AgentManifest.spec 增:cache?: { enabled?: boolean; [k: string]: unknown }
export interface ResponseCacheFields {
  responseCacheEnabled?: boolean;
}
export function readResponseCache(m: unknown): ResponseCacheFields;   // RAW
export function patchResponseCache(m: unknown, patch: Partial<ResponseCacheFields>): AgentManifest;
```

- 标准可选块语义(镜像 patchConsolidation):`"responseCacheEnabled" in patch`;undefined 删键;cache 块空则删(default_factory,absent≡默认);未知键保留;不物化 absent+净空。

- [ ] **Step 1: 失败测试**(enabled=false round-trip 经 YAML;清空(undefined)→ cache 块删;cache 未知键 patch 后保留;absent+净空不物化;不可变;与 model.cache_enabled(ModelFields)互不干扰——同 manifest 双缓存字段共存测试)
- [ ] **Step 2: 实现**
- [ ] **Step 3: scope vitest + typecheck;commit `feat(admin-ui): form_model 投影 spec.cache(LLM 响应缓存)`**

### Task 2: ObservabilitySection pane + 真相 note + trajectory 文案纠偏 + pending-hint 清理

**Files:**
- Create: `groups/ObservabilitySection.tsx`
- Modify: `ManifestEditor.tsx`(`CURATED_GROUP_PANES` 加 `observability`;**pending-hint 分支删除**+注释同步)
- Modify: `groups.ts`(observability keywords 追加 "缓存","cache","响应缓存","录制")
- Modify: locale interface + `en.ts` + `zh-CN.ts`(命名空间 `observability_group` 新增 + **纠既有 trajectory 文案键**——先 grep `af-trajectory-recording` 周边找到其 label/hint 键名)
- Test: `groups/__tests__/ObservabilitySection.test.tsx` + `__tests__/ManifestEditor.test.tsx`(pending-hint 测试删除/改写 + observability 组断言)

结构(平铺,无 Collapse——内容少,沙箱组先例):①`PolicyFieldList<ResponseCacheFields>` 单字段 ②触发器真相 note(testid `observability-triggers-note`)③声明性字段 note(testid `observability-declarative-note`)。

FieldDef:

```ts
{ fieldId: "cache.enabled", i18nKey: "observability_group.resp_cache",
  valueKey: "responseCacheEnabled", kind: "switch", effectiveDefault: true }
```

文案(zh verbatim;en 忠实对译):

**cache.enabled**(`resp_cache_label`/`_brief`/`_impact`):
- label「LLM 响应缓存」
- brief「相同请求命中缓存直接复用完整回答,不再调用模型——省钱提速」
- impact「命中即跳过模型调用(含路由与回退链)。提示词含时效内容(当前日期、实时数据等)的 Agent 应关闭,否则可能返回过期答案。与模型组的「提示词缓存」(Anthropic prompt caching)是两回事:那个只省输入 token,本开关直接复用整条回答。」
- (switch 无默认徽章)

**触发器真相 note**(`triggers_note`):
「manifest 的 triggers 声明当前未接线:在清单里写 cron/webhook 触发器不会生效。定时与 webhook 自动化请通过触发器管理 API(/v1/triggers)创建,该路径的调度与触发正常工作。」

**声明性字段 note**(`declarative_note`):
「observability 的 trace / log_level / redact_fields 当前为声明性字段:通过校验但运行时不读取——追踪始终按平台配置开启,日志级别由各服务部署环境决定,PII 脱敏由平台防御链负责。轨迹录制同理:是否录制由部署的对象存储配置决定。」

**trajectory 文案纠偏**(既有键改写,zh verbatim;en 同步;键名以 grep 实际为准):
- hint/help 改为:「当前未接线:是否录制由平台对象存储配置决定,本开关暂不生效(字段保留待后端接线)。」
- label 保持不动(仍是字段名);若既有测试断言旧文案则同步更新(语义不降:仍断言 hint 渲染)。

**pending-hint 清理**:observability 落地后 `CONFIG_GROUPS` 再无 `sections: []` 且不在 `CURATED_GROUP_PANES` 的组 → ManifestEditor 的 pending-hint 渲染分支不可达,删除分支+其 i18n 键(若无他用)+ 相关测试(PR4 repoint 到 observability 的两个 pending-hint 测试删除——路径已不存在,非语义降级);ManifestEditor 注释同步。

- [ ] **Step 1: 失败测试**(pane 渲染 `data-field-id="cache.enabled"`;开关拨关 → manifest `spec.cache.enabled === false`;拨回开(默认)→ cache 块删;两 note testid 渲染;`cfg-nav-observability` → curated pane 非 pending hint;trajectory 新 hint 文案渲染(security 组内))
- [ ] **Step 2: 实现 + i18n(grep `observability_group` 撞键)+ pending-hint 删除 + 孤儿键清理**
- [ ] **Step 3: scope vitest + typecheck;commit `feat(admin-ui): 触发器与可观测组 —— 响应缓存开关+触发器/死字段真相+轨迹开关纠偏`**

### Task 3: 终门

- [ ] `pnpm typecheck && pnpm exec vitest run`(全量)+ `pnpm build` + storybook build + Playwright manifest-editor/manifest-edit 2 spec;有修才 commit `test(admin-ui): PR7 终门`

## Self-Review 已核

- cache.enabled 消费链实证(middleware_assembly.py:126→builder.py:733 短路)、双缓存字段区分在 impact 文案点名 ✓
- triggers「未接线」结论有双证(source="manifest" 仅存 docstring;唯一建行路径 API)✓
- trajectory 假开关纠偏:比死旋钮更危险(假控制感,涉录制合规),文案直说真相,开关保留(编辑真 schema 字段,待后端接线)✓
- pending-hint 删除是本 PR 必要后果(最后一个静态空组落地),非 drive-by;PR4 repoint 的测试路径消亡有明确交代 ✓
- spec.cache 可选块语义与 consolidation 同款,非 required/存在语义,三类块规则用对 ✓
- 无 TBD;新键 verbatim zh 齐 ✓
