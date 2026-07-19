# Agent 配置页 全量核对清单(schema × 表单覆盖)

**日期**:2026-07-19(配置页重组 epic 收尾,PR1-PR7 全合后盘点)
**方法**:AgentSpec 全叶子字段逐一对照 admin-ui 表单控件与真相 note;死字段结论均经全库消费者溯源。

## 覆盖状态图例

- **FORM** — 有活表单控件
- **NOTE** — 无控件但有 in-form YAML 指引/真相说明覆盖
- **INERT-DOC** — 已知死字段(运行时不读取),有说明覆盖
- **YAML-ONLY** — 仅 YAML 可编辑,无说明(收尾前的缺口,PR8 处理)

## 分组覆盖表

### 基础(basic)
| 字段 | 状态 |
|---|---|
| metadata.name / spec.description | FORM(af-name/af-description) |
| metadata.version / tenant / labels | YAML-ONLY(建号期身份/自由字典,合理)|
| spec.extends | YAML-ONLY → PR8 note |
| tenant_config.*(compliance_pack/isolation_level/data_residency 零消费者;audit_retention_days 平台侧暂用全局默认)| YAML-ONLY → PR8 note |

### 模型与路由(model,PR6)
model 全 16 字段 FORM(ModelSelect 含 advanced:max_tokens/rate_limit_rpm/context_window/thinking/effort/adaptive/cache;fallback 链逐项同);api_key_ref INERT-DOC、base_url/azure×2/planning 规则/vision.fallbacks NOTE(model-yaml-note);reflection 块 FORM(开关+budget+deadline_s);评判者=routing 规则 FORM(文案已纠偏)。

### 提示词与输出(prompt)
system_prompt(template/jinja/variables×4)FORM;output_schema×3 NOTE(af-output-schema hint);inject_memory INERT-DOC(memory-reserved-note);**inject_current_date YAML-ONLY → PR8 控件**;custom_reminders YAML-ONLY → PR8 note。

### 能力(capabilities)
tools(web_search/http/mcp.servers/allow_tools)、knowledge、skills(+auto_attach)、subagents×3、dynamic_workers.enabled 全 FORM;**内置工具 config 与非 web_search 内置项 YAML-ONLY → PR8 note**。

### 记忆(memory,PR5)
long_term 全 8 旋钮 + 存在开关 FORM;注入/纠正双预算 FORM;consolidation.enabled FORM、aux_model NOTE;short_term INERT-DOC。

### 运行预算(budget,PR1)
max_iterations/max_no_progress/run_deadline_s/stream_deadline_s/idle_timeout_s FORM;**workflow.type YAML-ONLY → PR8 控件**;early_stop/builder 零消费者 → PR8 note。

### 上下文与压缩(context,PR2)
context_compression×10 + working_memory×4 + tool_result_prune×3 + tool_output_budget 全 FORM。

### 安全与防护(security,PR3+#1017)
defenses×7 + approval×2 + sandbox.network×3 + tool_use_enforcement FORM;rate_limit/pii/safety 字典 NOTE(dict-note);trajectory_recording FORM(**收尾波已接线**:per-agent opt-out 经 BuiltAgent→run_agent 生效,开关是真的了)。

### 沙箱与资源(sandbox,PR4)
persistent_workspace FORM(唯一活字段);runtime/image/image_build/resources×4/readonly_root/writable/mounts/code 块 INERT-DOC(declarative-note + platform-note)。

### 触发器与可观测(observability,PR7)
cache.enabled FORM(唯一活字段);triggers×3 INERT-DOC(manifest 未接线,走 API);observability×3 INERT-DOC。

### 无组归属
spec.hooks(自由字典)YAML-ONLY(合理,不处理)。

## 收尾裁定(PR8)

- **补控件**:workflow.type(react/plan_execute select,custom 未接线真相入文案)、dynamic_context.inject_current_date(开关)
- **补 note**:spec.extends+tenant_config(basic)、custom_reminders(prompt)、内置工具 config(capabilities)、early_stop/builder(budget)
- **不处理**(合理 YAML-only):hooks/labels/metadata 身份字段/pii_fields(经租户记录路径有活消费者,manifest 块读法待后端定)

## 死字段总账(运行时零消费者,全部已 in-form 说明)

sandbox 13 字段、spec.code 块、memory.short_term、dynamic_context.inject_memory、observability×3、spec.triggers(manifest 路径)、model.api_key_ref(强制忽略)、workflow.early_stop/builder、workflow.type="custom" 分支、tenant_config.compliance_pack/isolation_level/data_residency。~~policies.trajectory_recording(标志位)~~——**收尾波已接线,移出死字段账**。

**Backlog(产品决策)**:triggers 走 user 维度重设计(独立 epic,brainstorm 中);自适应规划(react/plan_execute 死开关→规划作工具或复杂度路由);死字段批量处置(接线或 schema 弃用)。
