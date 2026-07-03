# Stream RT — Runtime 对标缺口补齐(设计先行)

> **背景**:五方对标矩阵([research/2026-07-03-agent-runtime-five-way-benchmark.md](../research/2026-07-03-agent-runtime-five-way-benchmark.md),PR #904)确认 helix 84%、9 格独占,但 4 格被压制、3 格有已知弱点。本 Stream 以价值为导向补齐对标缺口:7 项 × 4 Wave(~8-9 周),每项 PR-0 细设计先合再编码,每项显式列 admin-ui 前端工作。
> **对标纪律**:openclaw / deer-flow / OpenHands 作参考实现,校准"成熟长什么样"+ 找差距。**结论是独立设计,不照抄**。
> **已拍板**(2026-07-03):browser 自动化不纳入进 backlog(与 ITERATION-PLAN「显式不做」现有条目一致:浏览器归 MCP);全部四个 wave 一次排定;KEK rotation / 数据驻留 / 多区域 HA 等企业深水区等客户信号。

## 1. 价值排序原则

①直接影响毛利/成本 > ②per-user 持久 agent 核心体验 > ③商业化平台卖点(质量/安全可证) > ④能力差异化。依赖关系参与排序:RT-5 生产质量监控依赖 RT-1 的 judge 结构化输出。

## 2. 范围总览

| Wave | 项 | 价值论证 | 工期 | PR | 前端工作 |
|---|---|---|---|---|---|
| W1 | RT-1 结构化输出强制 | 内部 LLM 链路(judge/consolidator/evolution)可靠性地基;RT-5 前置;对标 0 分全场最低 | 1 周 | 4 | manifest 编辑器 YAML 提示/校验 + i18n |
| W1 | RT-2 compaction 深水区补齐 | 最大格差项;per-user 持久 agent 长对话体验+成本 | 2 周 | 6 | run 事件流 compaction 渲染 + hide_from_ui 过滤 |
| W2 | RT-3 prompt cache 成本工程 | 直接毛利(COGS);计量数据已齐差编排+透出 | 1 周 | 3 | SettingsUsage cache 列 + 节省估算 |
| W2 | RT-4 全局 kill switch | 安全兜底(OWASP ASI08);企业审计必问 | 1 周 | 3 | AgentDetail 禁用按钮 + 租户紧急停止 |
| W3 | RT-5 生产质量监控 | 商业化卖点(租户可见质量看板);复用 eval 基建 | 2 周 | 5 | 质量看板新页(全套挂点) |
| W3 | RT-6 审批工件绑定 | HITL TOCTOU 弱点;企业安全卖点 | 1 周 | 3 | ApprovalCard 工件展示 + IM 卡片字段 |
| W4 | RT-7 成本感知模型路由 | 毛利+差异化(全场皆 1,做了即领先) | 1.5 周 | 4 | PlatformRoutingSection 平台配置节 |

Backlog(不在本 Stream):browser 自动化(产品定位)、KEK rotation、数据驻留、多区域 HA、replay `fork()`。

## 3. 各项设计要点

### RT-1 结构化输出强制(W1)

LLM router 层加 structured output 能力:JSON Schema 声明 → 请求侧按 provider 能力选径(原生 response_format / tool-call 强制 / prompt+校验)→ 响应侧 schema 校验 → 失败带错误上下文有界重试。参考 OpenClaw llm-task(Schema 校验+模型白名单+timeout)。

- 挂点:`services/orchestrator/src/orchestrator/llm/router.py`(LLMProvider protocol)、provider 适配器、`packages/helix-protocol/.../agent_spec.py`
- PR-0 设计(协议形态/provider 能力矩阵/降级策略)→ PR-1 后端实现 → PR-2 内部链路迁移(`tools/eval/_judge.py`、MemoryConsolidator、skill evolution distiller/judge,删手工 JSON parse)→ PR-3 Tier3 暴露 output_schema + **前端 manifest 编辑器提示/校验 + i18n**
- 验证:坏 JSON 注入→重试回正;内部链路迁移后 eval 套件全绿

### RT-2 compaction 深水区补齐(W1)

**现状勘误(2026-07-03,实施前核实)**:L2 `ContextCompressor` 已存在(`services/orchestrator/src/orchestrator/context/compressor.py`,PR #206)——preflight 阈值触发、head/tail 保留、中段 LLM 摘要成 `<context-summary>` SystemMessage、max_passes 上限。对标矩阵 2.1 给 helix 1 分**偏低,实为 ~2**(首轮探索漏扫 `orchestrator/context/`);ITERATION-PLAN M2-C 的"summarization 已在 L2 完成"表述**正确,不需修正**。RT-2 范围据此收敛:不是从零建 summarization,而是 **L2 深化 + [deer-flow alignment tracker](../decisions/deer-flow-context-mgmt-alignment.md) M2-C 必锁条款中真缺的部分**。

> **[2026-07-03 PR-0 修正]** 本段为立项时的初版范围,PR-0 取证复核后已被 **§8 整体替代**——ID-swap/异步队列判定为不需要(RT-ADR-8)、skill rescue 三预算被上游 #3887 废弃改引用式(RT-ADR-7)、新增最高优先项 adapter 兼容核证(RT-ADR-5)。**以 §8 为准,本段仅留档**。

真缺清单(对照 7 条必锁):skill rescue 三预算、before_summarization hook(压缩前 memory flush)、ID-swap + `<system-reminder>` HumanMessage 注入(hide_from_ui)、memory 注入 2k 硬上限、memory 异步队列对齐、压缩可观测(COMPACTION 事件类型 + event_store 落账 + 前端渲染)。已有:触发阈值(L2 threshold_pct)、摘要+保留策略(L2 head/tail)。

- 架构决策点(PR-0 拍板):L2 是 graph 内 preflight(agent_node 入口),deer-flow 是 middleware——深化在 L2 原位做,还是迁 `before_llm_call` middleware 链与 13 个既有中间件统一?
- **PR-0 设计必须先按 deer-flow 新版复核 tracker**(上游 6 月后已修 ID-swap 递归注入 #3746、durable context #3887、SystemMessage 合并 #3711——直接抄坑)+ 顺带在对标报告 2.1 行加勘误脚注
- 验证:关键事实留存率断言;prefix cache 命中率不劣化;LoCoMo 段回归不掉分;M2-A"小时级 session 加固"未完成项与本项集成点在 PR-0 说明

### RT-3 prompt cache 成本工程(W2)

①provider 层显式 cache_control 断点编排(参考 deer-flow `claude_provider.py:193`:system/近期消息/最后 tool def 三断点,OAuth 4-cache-block 处理);②cache 命中计量透出(token_usage 已有 cache_creation/cache_read 四段,差 API+前端);③OpenClaw 保温三件套(TTL 剪枝/heartbeat keep-warm)在多租户 server 成本模型不同——**PR-0 评估后拍板,不默认做**。

- PR-0 设计(断点 per-provider 矩阵 + keep-warm 评估结论)→ PR-1 断点编排 + 命中率指标 → PR-2 usage API 透出 + **前端 SettingsUsage 加 cache 列 + 节省金额估算、BillingChargeback 平台侧同步、i18n**
- 验证:长对话 cache_read 占比 before/after 可量化;计费账目 cache 折扣价对账

### RT-4 全局 kill switch(W2)

两级紧急停止:agent 级(拒新 run + 终止运行中 run)+ 租户级(全租户暂停);复用 CancellationToken 取消链 + RunQueueWorker(claim 前检查);全程审计;恢复对称。

- 挂点:`tenant_config` / agent 表 disabled 标志、`control_plane/run_queue_worker.py`、AuditAction 扩展
- PR-0 设计(小项,设计先合)→ PR-1 后端 → PR-2 **前端:AgentDetail 禁用/启用按钮(危险操作确认模态,现无先例)+ 租户设置紧急停止 + 状态标识 + i18n**
- 验证:禁用时运行中 run 数秒内终止、queued 不被 claim、审计齐;恢复正常

### RT-5 生产质量监控(W3,依赖 RT-1)

真实流量采样(per-tenant 采样率,默认低)→ LLM-as-judge 评分(复用 `tools/eval/_judge.py`,经 RT-1 结构化输出)→ 分数落库(per-agent 时间序列)→ 漂移检测(滑窗基线偏离)→ 告警(webhook `quality.drift`,复用 payload_format 渠道)。参考 OpenHands critic,先做 run 级评分不做逐动作。**采样成本入 aux 计量**(`usage_kind` 惯例,同 memory_consolidation)。

- 挂点:`control_plane/eval_engine_live.py`、`api/webhooks.ts` event_types、`PlatformJudgeSection.tsx`(judge 配置已有)
- PR-0 设计(采样策略/rubric/漂移算法/成本护栏)→ PR-1 采样管道+评分+落库+aux 计量 → PR-2 漂移 worker + webhook 事件 → PR-3 **前端质量看板新页(router + navModel + CommandPalette + i18n + api/quality.ts + Storybook + aria-label):分数趋势/低分 run 下钻/漂移告警;WebhooksList 加 quality.drift** → PR-4 live 验证(★5:注入劣化对话触发告警送达 IM)
- 验证:采样率生效且 aux 成本可见;漂移注入 E2E 告警触达

### RT-6 审批工件绑定(W3)

OpenClaw approval-time binding 移植:审批对象从"意图"变"确切工件"——approval 绑定 canonical 命令(argv/cwd/env 指纹/脚本 content_hash);resume 执行前重算指纹,漂移即拒并落审计;绑定不唯一时拒绝铸造审批。适用 exec_python/沙箱命令/HTTP 工具。**modify 交互要设计**:审批人 Monaco 修改 proposed_args 后重铸绑定。

- 挂点:`persistence/approval/sql.py` + protocol ApprovalRequest(binding 字段)、`orchestrator/tools/approval.py` + resume 执行路径、`run_detail/ApprovalCard.tsx`
- PR-0 设计(binding 模型/指纹算法/modify 交互/漂移拒绝语义)→ PR-1 后端(协议+落库+执行前校验+审计;migration ID ≤32 字符)→ PR-2 **前端 ApprovalCard 工件展示 + 漂移拒绝状态、ApprovalsList 标识、IM webhook 审批卡片补绑定摘要、i18n**
- 验证:E2E 审批后篡改脚本→执行拒绝+审计;modify 重铸绑定正常

### RT-7 成本感知模型路由(W4)

按任务档位路由(强推理/常规/轻量三档):任务类型(主对话/子 agent/aux)→ 档位映射 → provider/model 选择;沿用 Y-MK 两级 failover;平台默认 + 租户覆盖;计费照实。借鉴 OpenClaw failover 来源契约:**用户/agent 显式指定模型 = strict 不降档**。aux_model 现状(consolidation 独立配置)收编进档位体系。

- 挂点:`orchestrator/llm/router.py`、`platform_billing_config`
- PR-0 设计(档位模型/映射规则/strict 语义/租户覆盖面)→ PR-1 路由引擎 → PR-2 平台配置 API + **前端 settings_platform/PlatformRoutingSection.tsx(SettingsPlatformConfig 挂节 + i18n + SDK)** → PR-3 计量对账 + eval 回归
- 验证:aux 切轻量档成本下降可量化;RT-5 质量分数不劣化

## 4. 横切纪律

- 设计 PR 先合再动代码;各 PR-0 细化为本文档新章节
- 每 PR:CI 全绿 + 零债 6 条;合入后同步 ITERATION-PLAN.md(checkbox+PR 号)
- 前端 PR:`tsc -b --noEmit`(含 stories);form 元素 aria-label(axe);vitest 全量(改 nav i18n 必跑);envelope-vs-raw 对账后写 SDK
- migration:alembic revision ID ≤32 字符;真 PG 集成测
- ★5 项(RT-2/RT-5)必须 live E2E,CI 绿不算完

## 5. 收口验证(计划级)

每 wave 收口对标矩阵对应格重打分:RT-2 后 2.1 应 ~2→3;RT-3 后 4.4 应 2→3;RT-1 后 4.6 应 0→2+;RT-5 后 6.4 应 1→2+;RT-6 后 1.3 弱点清除;RT-7 后 4.5 应 1→2+。全部完成 helix 预期 109→~122/129(≈95%),压制格清零。

## 6. 勘误记录

- 2026-07-03:对标矩阵 2.1(context 压缩)helix 评 1 分偏低,实为 ~2——L2 ContextCompressor(preflight 摘要压缩)已于 PR #206 交付,首轮能力探索漏扫 `services/orchestrator/src/orchestrator/context/`。缺口重定性为深水区条目(skill rescue/ID-swap/memory flush hook/2k 上限/压缩可观测)。教训并入既有方法论:能力扫描除 `runtime/middleware/` 与 `tools/eval/` 外,还须含 `orchestrator/context/` 等 graph 内组件。

## 7. RT-1 PR-0 细设计:结构化输出强制(2026-07-03 定稿)

### 7.1 现状取证结论(file:line 级,3 路验证)

- **provider 层零结构化输出代码**:`anthropic.py:332-366` 透传 model/system/messages/tools/max_tokens/thinking/output_config,无 response_format/json_schema;`openai.py:270-294` 有 tool_choice(HX-13 allowed_tools)无 response_format
- **全仓 9 处手工 JSON parse 点**(LLM 输出防守式解析,零重试零结构化):

| 位置 | 功能 | 失败处理 |
|---|---|---|
| `tools/eval/_judge.py:114-150` | eval judge 评分 | 正则 `[1-5]` 提取,不匹配 0 分 |
| `control_plane/memory_consolidator.py:306-322` | 集群验证 | None→降级 false_cluster |
| `control_plane/memory_consolidator.py:328-339` | 单项审查 | None→保守标记已审查 |
| `orchestrator/output_judge.py:120-139` | 输出安全对齐 | 正则抽 `{...}`,失败抛 ValueError |
| `orchestrator/output_judge.py:242-270` | 工具调用对齐 | 同上 |
| `control_plane/skill_distiller.py:123-132` | 技能蒸馏 | 宽松 `{...}` 截取,None→蒸馏失败 |
| `tools/eval/longmem/judge.py:164-171` | LoCoMo judge | 正则+json.loads,失败抛 |
| `tools/eval/longmem/judge.py:135-160` | LongMemEval judge | 纯文本 yes/no 子串匹配(**不迁**,无 JSON) |

- 可复用:E.4 `LLMErrorHandlingMiddleware`(重试/退避/断路器框架,`llm_error_handling.py:267-344`);pydantic `model_validate_json` 基建齐但无一处用于 LLM 输出;manifest schema 端点动态生成(`api/agent_schema.py:16`,新字段自动进前端 JSON Schema)

### 7.2 协议设计

**per-call opt-in**:`LLMProvider.complete()` 加可选参数 `output_schema: StructuredOutputSpec | None = None`;`StructuredOutputSpec`(helix-protocol 新类型)= `{ schema: dict(JSON Schema), name: str, strict: bool = True }`。返回 AIMessage 不变,新增 `parsed` 附加字段(additional_kwargs)携带校验通过的 dict——调用方拿 `parsed` 不再碰裸文本。

**aux 调用面**:`ConsolidatorAuxModel` 系协议(`memory_consolidator.py:111-131`、`skill_distiller.py:52-55`)同步扩展 `output_schema` 参数;pydantic 模型定义在各调用方(如 `JudgeVerdict`/`ClusterVerdict`/`DistilledSkill`),`model_json_schema()` 生成 schema 传入。

### 7.3 Mini-ADR

- **RT-ADR-1 校验重试独立于 E.4 failover**:schema 校验失败是模型行为不是 key/provider 故障——绝不触发 key 轮换/provider failover。校验循环在 provider.complete 外层(router `_attempt_call` 内):失败时把 ValidationError 摘要作追加 user message 重发,同 provider 同 key,max 2 次;仍失败抛 `LLMOutputValidationError`(新错误类,**不入** `_KEY_LEVEL_ERRORS`),调用方既有防守降级路径原样保留(judge→0 分、consolidator→None 等——RT-1 降低失败率,不改变失败语义)
- **RT-ADR-2 三级降级链,能力声明在适配器**:provider 适配器声明 `structured_output_capability: "native" | "tool_call" | "prompt"`——native=response_format/output_config json_schema(OpenAI 系 strict、支持的 openai_compatible 厂商);tool_call=强制单工具调用承载 schema(Anthropic 稳妥径);prompt=schema 注入系统提示+校验重试兜底(其余厂商)。选径对调用方透明
- **RT-ADR-3 内部链路迁移不改行为语义**:9 处 parse 点(除 LongMemEval 纯文本项)全迁 pydantic 模型+output_schema;每处原降级行为保留;迁移前后 eval 套件分数不得回归
- **RT-ADR-4 Tier3 暴露仅约束最终回复**:`AgentSpecBody.output_schema`(CAPABILITY 档,FIELD_TIERS 补一行)只应用于**无 tool_calls 的收尾 AIMessage**(中间轮次不约束);manifest JSON Schema 自动透出前端,编辑器零改动,仅补 i18n 描述文案

### 7.4 PR 切分(对 §3 RT-1 的细化)

1. **PR-1 后端核心**:StructuredOutputSpec 协议 + 三 provider 适配(native/tool_call/prompt 三径)+ 校验重试循环 + `LLMOutputValidationError` + 单测(每径:成功/坏 JSON 重试回正/2 次失败上抛)
2. **PR-2 内部链路迁移**:8 处迁 pydantic + 各功能回归测试 + eval 套件基线对比(分数不回归为合入门)
3. **PR-3 Tier3 + 前端**:`output_schema` 字段(CAPABILITY)+ agent loop 收尾应用 + manifest i18n 文案 + 文档

### 7.5 风险

- openai_compatible 六厂商 response_format 行为不一(json_object vs json_schema vs 不支持)——PR-1 逐厂商探测,不确定的一律归 prompt 径(保守)
- tool_call 径与 HX-13 allowed_tools 交互:强制结构化工具时 allowed_tools 必须让位——PR-1 显式测
- prompt 径注入 schema 增加 token 开销——schema 压缩(去 description)+ 只在 aux 短链用

## 8. RT-2 PR-0 细设计:compaction 深水区补齐(2026-07-03 定稿)

### 8.1 取证结论摘要(helix 现状 + deer-flow 新版复核)

取证基线:helix@main;deer-flow 本地树 @b3c312b7(含 #3854/#3809/#3746/#3711),#3887 取自 GitHub merge sha 442248dd。

**helix L2 现状**(`orchestrator/context/compressor.py`):触发 threshold_pct=0.7、head_keep=4/tail_keep=6、fresh/update 双模摘要(CM-7 running summary)、摘要包装 `<context-summary>` **SystemMessage 插 head 后**(:385-390)、PreCompactionHook(CM-3,摘要前 flush memory :365-369)、max_passes=3 后 ContextOverflowError fail-hard(Mini-ADR L-2)。接线:`graph_builder/builder.py:497-580` 四道闸手写内联(CM-12 pruner→CM-2 window→注入→compressor),无 order-pin 测试。**可观测:仅 3 条日志,零事件零指标,前端完全看不到压缩发生**。

**deer-flow 新版复核颠覆 tracker 三处**:
1. **skill rescue 三预算已被 #3887 整体废弃**——preserve_recent_skill_* 三字段删除,替代 = durable `skill_context` channel 只存引用(name/path/description≤500c,上限 8 条),需要时模型重读文件。照 tracker 实现 = 复刻弃案
2. **摘要写回 messages 已被 #3887 废弃**——改写 state channel + model call 时临时投影;helix prompt-view-only 模式**本来就领先**,不要倒退
3. **ID-swap 已从 HumanMessage 升级为三元组 SystemMessage 角色分离**(#3630),且 #3746 修了递归注入(`id__user__user…` 无限增长)与孤儿压缩(三元组只救 tagged 成员)两坑——本质是"注入机制×压缩机制在组合处炸"

**新增关键事实(tracker 未收录)**:#3711 SystemMessageCoalescing——DynamicContext 把 SystemMessage 放对话中部后,严格 OpenAI-compatible 后端(vLLM/SGLang/Qwen)与 Anthropic 直接 400 "System message must be at the beginning"。**helix `<context-summary>` 正是对话中部 SystemMessage,且 helix 支持 qwen/glm/deepseek 等严格后端——疑似现存 live bug,PR-1 最高优先核证**。

**helix 侧其余取证**:memory 注入零 token 上限(仅 top_k=5 条数,单条超长 memory 可撑爆注入块,`builder.py:1145-1192`);summariser prompt 自身无预算(`_format_middle_for_summary` 不 trim,一条 20k 工具输出撑爆摘要调用);skill 内容进 context 的 lazy 路径 = `ToolMessage(name="skill_view")`(20k 截断,artifact 带 skill_name);hide_from_ui 无先例,且 **CM-1 `<recovery-advisory>` HumanMessage 持久化后在会话详情显示为用户气泡**(已知泄漏);COMPACTION 事件全链无枚举闸门(SSE event_name 自由字符串,前端未知事件自动忽略,加渲染只需 EVENT_COLOR 一行)。

### 8.2 Mini-ADR

- **RT-ADR-5 摘要载体兼容(最高优先)**:mid-conversation SystemMessage 在严格后端 400(deer-flow #3711 实证)。PR-1 第一步核证 helix 三 adapter(anthropic/openai/openai_compatible)对非首位 SystemMessage 的实际处理;默认解 = **adapter 层 per-request coalescing**(合并进首位 system,保 id 与 additional_kwargs,只改请求载荷)——通用,同时保护未来一切注入;备选 = 摘要改 HumanMessage+reference-only 围栏(spotlight 基建现成)。live 验证含 qwen 真后端
- **RT-ADR-6 summariser 失败语义修订**:L-2 绝对 fail-hard(摘要 LLM 一次失败 = run 失败)修订为——transient 失败**跳过本轮压缩** + metric + 下轮重试(吸收 #3887);连续 N=3 轮失败或 middle 为空仍超限,保留 ContextOverflowError fail-hard 兜底(保诊断性,不静默丢内容)
- **RT-ADR-7 skill rescue 走引用+重读,不做三预算**:CM-12 pruner / L2 compressor 处理 `ToolMessage(name="skill_view")` 时,stub 保留 `skill_name + path + "可用 skill_view 重读"`(artifact 字段现成,O(1) token);upstream #3887 已实证三预算全文保留是弃案
- **RT-ADR-8 不做 ID-swap、不改同步 flush**:helix prompt-view-only(注入不进 checkpoint)无 ID-swap 需求;CM-3 摘要前同步 flush 丢失窗口为零,优于 deer-flow 异步队列,是架构分歧非缺陷。补的是 #3746 揭示的组合风险:**注入块×压缩组合测试**(cache-anchor HumanMessage 依赖 head_keep=4 覆盖——head/tail 参数变更时必须有测试拦截)
- **RT-ADR-9 hide_from_ui 标记机制**:`additional_kwargs["helix_hide_from_ui"]` + `transcript.py:48` 过滤豁免;顺修 CM-1 recovery-advisory 用户气泡泄漏(现存 bug)
- **RT-ADR-10 摘要 prompt 预算硬化**:`_format_middle_for_summary` 加 per-message 截断 + 总预算硬上限(fresh/update 双模各半分,抄 #3887 `_bound_text` 头 2/3+尾兜底);memory 注入加 token 预算(默认 2000,可配)+ correction 类保底(`MemoryItem.kind` 现成)

### 8.3 PR 切分(替代 §3 RT-2 原切分,依取证修正)

1. **PR-1 兼容与鲁棒**:RT-ADR-5 核证+修复(adapter coalescing)+ RT-ADR-6 失败语义 + RT-ADR-10 摘要 prompt 预算硬化 + 四道闸 order-pin 测试(#3809 教训:magic-index 顺序漂移)
2. **PR-2 memory 注入硬化**:token 预算 2000 + correction 保底 + 注入块×压缩组合测试(RT-ADR-8)
3. **PR-3 skill 引用式 rescue**:RT-ADR-7(pruner + compressor 两处 stub 逻辑)
4. **PR-4 可观测**:COMPACTION 事件(sse.py publish+persist;EventType 枚举补 COMPACTION)+ compressor pass/tokens metrics + PreCompactionHook 负载结构化(pass 序号/切片 token 数)+ RT-ADR-9 hide_from_ui 机制含 CM-1 泄漏修复
5. **PR-5 前端**:EventStreamPanel EVENT_COLOR + compaction 摘要卡片(压缩前后 token/pass 数)+ ToolTimeline + i18n 双语
6. **PR-6 live E2E(★5)**:长对话真跑(含 qwen 严格后端)——压缩触发→关键事实留存断言→prefix cache 命中不劣化→LoCoMo 段回归不掉分

### 8.4 范围外(取证后显式不做)

ID-swap 三元组(无需求,见 RT-ADR-8)、memory 异步队列化(同步 flush 更优)、多触发器 OR(threshold_pct 单触发够用,需求出现再议)、摘要写回 checkpoint(prompt-view-only 是优势)。

> **跟进注记(PR-2 组合测试发现,RT-ADR-8)**:`head_keep=0`(协议 `ge=0` 允许)会把 per_session 注入在 messages[1] 的 cache-anchor 记忆块划进 middle 概括掉——cache anchor 与记忆指导静默全失。现状已由 `test_memory_injection_compression_combo.py` 边界测试锁死(只锁不修);保护方案(per_session recall 激活时 head_keep 下限 1)归 **RT-2 PR-4 或 stream 收尾时拍板**。

### 8.5 tracker 同步

`docs/decisions/deer-flow-context-mgmt-alignment.md` M2-C 必锁表按本次复核加注:skill rescue 三预算条目标注 upstream #3887 已废弃(本 PR 同步)。
