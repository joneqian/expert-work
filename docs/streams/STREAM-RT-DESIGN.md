# Stream RT — Runtime 对标缺口补齐(设计先行)

> **背景**:五方对标矩阵([research/2026-07-03-agent-runtime-five-way-benchmark.md](../research/2026-07-03-agent-runtime-five-way-benchmark.md),PR #904)确认 Expert Work 84%、9 格独占,但 4 格被压制、3 格有已知弱点。本 Stream 以价值为导向补齐对标缺口:7 项 × 4 Wave(~8-9 周),每项 PR-0 细设计先合再编码,每项显式列 admin-ui 前端工作。
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

- 挂点:`services/orchestrator/src/orchestrator/llm/router.py`(LLMProvider protocol)、provider 适配器、`packages/expert-work-protocol/.../agent_spec.py`
- PR-0 设计(协议形态/provider 能力矩阵/降级策略)→ PR-1 后端实现 → PR-2 内部链路迁移(`tools/eval/_judge.py`、MemoryConsolidator、skill evolution distiller/judge,删手工 JSON parse)→ PR-3 Tier3 暴露 output_schema + **前端 manifest 编辑器提示/校验 + i18n**
- 验证:坏 JSON 注入→重试回正;内部链路迁移后 eval 套件全绿

### RT-2 compaction 深水区补齐(W1)

**现状勘误(2026-07-03,实施前核实)**:L2 `ContextCompressor` 已存在(`services/orchestrator/src/orchestrator/context/compressor.py`,PR #206)——preflight 阈值触发、head/tail 保留、中段 LLM 摘要成 `<context-summary>` SystemMessage、max_passes 上限。对标矩阵 2.1 给 Expert Work 1 分**偏低,实为 ~2**(首轮探索漏扫 `orchestrator/context/`);ITERATION-PLAN M2-C 的"summarization 已在 L2 完成"表述**正确,不需修正**。RT-2 范围据此收敛:不是从零建 summarization,而是 **L2 深化 + [deer-flow alignment tracker](../decisions/deer-flow-context-mgmt-alignment.md) M2-C 必锁条款中真缺的部分**。

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

每 wave 收口对标矩阵对应格重打分:RT-2 后 2.1 应 ~2→3;RT-3 后 4.4 应 2→3;RT-1 后 4.6 应 0→2+;RT-5 后 6.4 应 1→2+;RT-6 后 1.3 弱点清除;RT-7 后 4.5 应 1→2+。全部完成 Expert Work 预期 109→~122/129(≈95%),压制格清零。

## 6. 勘误记录

- 2026-07-03:对标矩阵 2.1(context 压缩)Expert Work 评 1 分偏低,实为 ~2——L2 ContextCompressor(preflight 摘要压缩)已于 PR #206 交付,首轮能力探索漏扫 `services/orchestrator/src/orchestrator/context/`。缺口重定性为深水区条目(skill rescue/ID-swap/memory flush hook/2k 上限/压缩可观测)。教训并入既有方法论:能力扫描除 `runtime/middleware/` 与 `tools/eval/` 外,还须含 `orchestrator/context/` 等 graph 内组件。

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

**per-call opt-in**:`LLMProvider.complete()` 加可选参数 `output_schema: StructuredOutputSpec | None = None`;`StructuredOutputSpec`(expert-work-protocol 新类型)= `{ schema: dict(JSON Schema), name: str, strict: bool = True }`。返回 AIMessage 不变,新增 `parsed` 附加字段(additional_kwargs)携带校验通过的 dict——调用方拿 `parsed` 不再碰裸文本。

**aux 调用面**:`ConsolidatorAuxModel` 系协议(`memory_consolidator.py:111-131`、`skill_distiller.py:52-55`)同步扩展 `output_schema` 参数;pydantic 模型定义在各调用方(如 `JudgeVerdict`/`ClusterVerdict`/`DistilledSkill`),`model_json_schema()` 生成 schema 传入。

### 7.3 Mini-ADR

- **RT-ADR-1 校验重试独立于 E.4 failover**:schema 校验失败是模型行为不是 key/provider 故障——绝不触发 key 轮换/provider failover。校验循环在 provider.complete 外层(router `_attempt_call` 内):失败时把 ValidationError 摘要作追加 user message 重发,同 provider 同 key,max 2 次;仍失败抛 `LLMOutputValidationError`(新错误类,**不入** `_KEY_LEVEL_ERRORS`),调用方既有防守降级路径原样保留(judge→0 分、consolidator→None 等——RT-1 降低失败率,不改变失败语义)
- **RT-ADR-2 三级降级链,能力声明在适配器**:provider 适配器声明 `structured_output_capability: "native" | "tool_call" | "prompt"`——native=response_format/output_config json_schema(OpenAI 系 strict、支持的 openai_compatible 厂商);tool_call=强制单工具调用承载 schema(Anthropic 稳妥径);prompt=schema 注入系统提示+校验重试兜底(其余厂商)。选径对调用方透明
- **RT-ADR-3 内部链路迁移不改行为语义**:9 处 parse 点(除 LongMemEval 纯文本项)全迁 pydantic 模型+output_schema;每处原降级行为保留;迁移前后 eval 套件分数不得回归
- **RT-ADR-4 Tier3 暴露仅约束最终回复**:`AgentSpecBody.output_schema`(CAPABILITY 档,FIELD_TIERS 补一行)只应用于**无 tool_calls 的收尾 AIMessage**(中间轮次不约束);manifest JSON Schema 自动透出前端,编辑器零改动,仅补 i18n 描述文案

### 7.4 PR 切分(对 §3 RT-1 的细化)

1. **PR-1 后端核心**:StructuredOutputSpec 协议 + 三 provider 适配(native/tool_call/prompt 三径)+ 校验重试循环 + `LLMOutputValidationError` + 单测(每径:成功/坏 JSON 重试回正/2 次失败上抛)
2. **PR-2 内部链路迁移**:8 处迁 pydantic + 各功能回归测试 + eval 套件基线对比(分数不回归为合入门)
3. **PR-3 Tier3 + 前端**:`output_schema` 字段(CAPABILITY)+ agent loop 收尾应用 + manifest i18n 文案 + 文档。接线硬要求:agent loop 应用结构化收尾时,必须把 `StructuredOutputSpec` 实例放进 `before_llm_call` 与 `after_llm_call` 两个 anchor 的 `payload["output_schema"]`——E.13 LLM cache 键的 schema 指纹(PR-2 已实现,`output_schema=None` 时键与旧 canonical byte-identical、金测试锁死)自该接线起生效;两侧缺一则结构化条目只写不读

### 7.5 风险

- openai_compatible 六厂商 response_format 行为不一(json_object vs json_schema vs 不支持)——PR-1 逐厂商探测,不确定的一律归 prompt 径(保守)
- tool_call 径与 HX-13 allowed_tools 交互:强制结构化工具时 allowed_tools 必须让位——PR-1 显式测
- prompt 径注入 schema 增加 token 开销——schema 压缩(去 description)+ 只在 aux 短链用

### 7.6 PR-3 交付注记(2026-07-03)

**manifest 字段形态**(`AgentSpecBody.output_schema`,`OutputSchemaSpec`,FIELD_TIERS=CAPABILITY):

```yaml
spec:
  output_schema:            # None 默认 = 自由文本,零行为变化
    name: final_report      # wire 名(OpenAI json_schema name / Anthropic tool name),^[a-zA-Z0-9_-]{1,64}$
    json_schema:            # JSON Schema (draft 2020-12);顶层 type 必须是 object(或省略)
      type: object
      properties: { answer: { type: string } }
      required: [answer]
    strict: true            # OpenAI native strict;tool_call/prompt 径本地校验恒开(RT-ADR-1)
```

协议模型做形状校验(非空/顶层 object/name pattern);深度 JSON-Schema 合法性在 build 时
`Draft202012Validator.check_schema` 把关(坏 schema 挂 build,不挂首个收尾轮)。

**收尾应用机制(RT-ADR-4,三径统一两段式)**:主调用(含中间 tool 轮与收尾候选轮)一律
**不带** schema——tool_call 径强制单工具、prompt 径 JSON-only 指令都与工具使用互斥,且
router 的 RT-ADR-1 循环对每个结构化响应无条件校验(合法的 tool_calls 轮会被误纠错),
native 径虽 wire 层可与 tools 共存也救不了;fallback 链还可混径(RT-ADR-2 对调用方透明)。
因此:无 tool_calls 的收尾候选先**本地校验**(符合 = 零额外调用,`parsed` 挂
additional_kwargs);不符则**一次**结构化重发(候选 + correction 追加、`tools=[]`、
`output_schema` 下发,provider 按各自径强制 + router 重试兜底);仍不符
`LLMOutputValidationError` 上抛挂 run(不静默降级)。候选/correction 交换不落
checkpoint——状态只持久化最终响应(与 router 循环的 ephemerality 一致)。

**cache/计量接线(§7.4 硬要求落地)**:重发拥有自己的 before anchor pass(payload 带
spec **实例**→E.13 结构化 lookup)+ 底部 after pass 携同一实例与重发精确 prompt(store
侧);主调用在 helper 内单独走一遍 after pass——每次真实上游调用恰好一遍 after chain,
G.9 token 计量 exactly-once。测试双证:spy 断言两 anchor 实例 identity + 真
lookup/store 中间件 write→read 往返、异 schema fingerprint 必 miss。

**§7.5 prompt 径注入面防护**:Tier3 schema 系租户来源,`StructuredOutputSpec.fence_nonce`
(build 时注入,复用 spotlight nonce 或独立铸造)使 `schema_instruction` /
`correction_message` 把 schema 文本/校验错误摘要包进 «UNTRUSTED nonce=…» 围栏 + 内联
data-not-instructions 条款;**只 delimiting 不 datamark**——datamark 会把 ▁ 插进
属性名/枚举值,模型必须 byte-exact 复现这些键,datamark 等于保证校验失败。
`fence_nonce=None`(内部 aux schema)prompt 径指令与 PR-1 byte-identical。

**观测**:`expert_work_llm_structured_finalize_total{outcome=conform|cache_hit|resend}`
(cache_hit 只计**校验通过**的命中;投毒回落计 resend)。
已知取舍:reflection 配置时收尾强制先于 reflect 判定(reflect 打回则重走);PI-2/judge
拦截替换 refusal 时安全语义优先于 schema 契约;7.4 DLP 改写后 `parsed` 从改写文本重导出
(不再合法则丢弃,防止 parsed 泄漏未脱敏值)。

**结构化缓存投毒双侧防**(review 修复):重发合规后被输出防护改写(refusal/redact 破坏
约束字段)的响应若落结构化键,下次相同收尾轮命中→重校验失败→run 硬挂。修复
belt+suspenders:lookup 侧命中先 `validate_structured_output`,不合规忽略命中回落真重发
(自愈存量污染);store 侧响应无 `parsed`(已被改写)时 after payload 不打
`output_schema` 键,改写响应落非结构化键(结构化 lookup 永不派生该键)。红绿回归双测
锁死。附带:E.13 schema 指纹并入 `strict`(native 径 strict 切换 wire 强制,响应不可
互换;结构化条目 PR-3 才开始可写,零迁移成本),`output_schema=None` 金测试不受影响。

## 8. RT-2 PR-0 细设计:compaction 深水区补齐(2026-07-03 定稿)

### 8.1 取证结论摘要(Expert Work 现状 + deer-flow 新版复核)

取证基线:Expert Work@main;deer-flow 本地树 @b3c312b7(含 #3854/#3809/#3746/#3711),#3887 取自 GitHub merge sha 442248dd。

**Expert Work L2 现状**(`orchestrator/context/compressor.py`):触发 threshold_pct=0.7、head_keep=4/tail_keep=6、fresh/update 双模摘要(CM-7 running summary)、摘要包装 `<context-summary>` **SystemMessage 插 head 后**(:385-390)、PreCompactionHook(CM-3,摘要前 flush memory :365-369)、max_passes=3 后 ContextOverflowError fail-hard(Mini-ADR L-2)。接线:`graph_builder/builder.py:497-580` 四道闸手写内联(CM-12 pruner→CM-2 window→注入→compressor),无 order-pin 测试。**可观测:仅 3 条日志,零事件零指标,前端完全看不到压缩发生**。

**deer-flow 新版复核颠覆 tracker 三处**:
1. **skill rescue 三预算已被 #3887 整体废弃**——preserve_recent_skill_* 三字段删除,替代 = durable `skill_context` channel 只存引用(name/path/description≤500c,上限 8 条),需要时模型重读文件。照 tracker 实现 = 复刻弃案
2. **摘要写回 messages 已被 #3887 废弃**——改写 state channel + model call 时临时投影;Expert Work prompt-view-only 模式**本来就领先**,不要倒退
3. **ID-swap 已从 HumanMessage 升级为三元组 SystemMessage 角色分离**(#3630),且 #3746 修了递归注入(`id__user__user…` 无限增长)与孤儿压缩(三元组只救 tagged 成员)两坑——本质是"注入机制×压缩机制在组合处炸"

**新增关键事实(tracker 未收录)**:#3711 SystemMessageCoalescing——DynamicContext 把 SystemMessage 放对话中部后,严格 OpenAI-compatible 后端(vLLM/SGLang/Qwen)与 Anthropic 直接 400 "System message must be at the beginning"。**Expert Work `<context-summary>` 正是对话中部 SystemMessage,且 Expert Work 支持 qwen/glm/deepseek 等严格后端——疑似现存 live bug,PR-1 最高优先核证**。

**Expert Work 侧其余取证**:memory 注入零 token 上限(仅 top_k=5 条数,单条超长 memory 可撑爆注入块,`builder.py:1145-1192`);summariser prompt 自身无预算(`_format_middle_for_summary` 不 trim,一条 20k 工具输出撑爆摘要调用);skill 内容进 context 的 lazy 路径 = `ToolMessage(name="skill_view")`(20k 截断,artifact 带 skill_name);hide_from_ui 无先例,且 **CM-1 `<recovery-advisory>` HumanMessage 持久化后在会话详情显示为用户气泡**(已知泄漏);COMPACTION 事件全链无枚举闸门(SSE event_name 自由字符串,前端未知事件自动忽略,加渲染只需 EVENT_COLOR 一行)。

### 8.2 Mini-ADR

- **RT-ADR-5 摘要载体兼容(最高优先)**:mid-conversation SystemMessage 在严格后端 400(deer-flow #3711 实证)。PR-1 第一步核证 Expert Work 三 adapter(anthropic/openai/openai_compatible)对非首位 SystemMessage 的实际处理;默认解 = **adapter 层 per-request coalescing**(合并进首位 system,保 id 与 additional_kwargs,只改请求载荷)——通用,同时保护未来一切注入;备选 = 摘要改 HumanMessage+reference-only 围栏(spotlight 基建现成)。live 验证含 qwen 真后端
- **RT-ADR-6 summariser 失败语义修订**:L-2 绝对 fail-hard(摘要 LLM 一次失败 = run 失败)修订为——transient 失败**跳过本轮压缩** + metric + 下轮重试(吸收 #3887);连续 N=3 轮失败或 middle 为空仍超限,保留 ContextOverflowError fail-hard 兜底(保诊断性,不静默丢内容)
- **RT-ADR-7 skill rescue 走引用+重读,不做三预算**:CM-12 pruner / L2 compressor 处理 `ToolMessage(name="skill_view")` 时,stub 保留 `skill_name + path + "可用 skill_view 重读"`(artifact 字段现成,O(1) token);upstream #3887 已实证三预算全文保留是弃案
- **RT-ADR-8 不做 ID-swap、不改同步 flush**:Expert Work prompt-view-only(注入不进 checkpoint)无 ID-swap 需求;CM-3 摘要前同步 flush 丢失窗口为零,优于 deer-flow 异步队列,是架构分歧非缺陷。补的是 #3746 揭示的组合风险:**注入块×压缩组合测试**(cache-anchor HumanMessage 依赖 head_keep=4 覆盖——head/tail 参数变更时必须有测试拦截)
- **RT-ADR-9 hide_from_ui 标记机制(Option A,deer-flow 验证)**:`additional_kwargs["expert_work_hide_from_ui"]` 标记 orchestrator 脚手架(CM-1 `<recovery-advisory>`)。**过滤只在 UI 服务边界,记录层永远忠实**——`read_turns(include_hidden=True)` 为安全默认(mirror sweep + 跨租户审计下钻拿完整记录,新持久/审计调用方忘传也不会静默丢审计),仅 UI 气泡视图(同租户自读)传 `include_hidden=False` 过滤。**绝不在 mirror sweep / 搜索层过滤**(那是审计+全文搜索源,#880-892)。参考:deer-flow `thread_runs.py:209-220` 在 gateway router UI 读路径判 `hide_from_ui`,checkpoint 原始读全部忠实;`journal.py:205` 仅语义用途(别把注入 advisory 误当用户首条消息记标题),持久/搜索层零过滤。顺修 CM-1 recovery-advisory 用户气泡泄漏(现存 bug)
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

> **跟进注记(PR-2 组合测试发现,RT-ADR-8;PR-4 已落地)**:`head_keep=0`(协议 `ge=0` 允许)会把 per_session 注入在 messages[1] 的 cache-anchor 记忆块划进 middle 概括掉——cache anchor 与记忆指导静默全失。**拍板 = 运行期 floor(不 reject 合法配置)**:`orchestrator.context.floor_head_keep_for_injection` 在 `agent_factory` 构造 compressor 时,当 `memory.long_term` 存在且 `recall_mode=per_session` 且 `head_keep<1` 时把 `head_keep` 抬到 1(warning 日志),否则原样透传(非 memory agent 的 `head_keep=0` 不被 brick)。compressor 本身不知道 anchor,修在构造层。组合测试从「只锁现状」改为「保护生效」(floored 值保 block + 直接 `head_keep=0` 无 floor 仍丢块,证修在构造层)。

### 8.5 tracker 同步

`docs/decisions/deer-flow-context-mgmt-alignment.md` M2-C 必锁表按本次复核加注:skill rescue 三预算条目标注 upstream #3887 已废弃(本 PR 同步)。

---

## 9. RT-follow-up:技能渐进式披露默认化(fixed-overhead 调查产物,2026-07-04)

RT-2 ★5 live run 后遗留的"agent ~20-24k 固定开销"backlog 决策。经实测 + deer-flow/Hermes 源码对标收敛。

### 9.1 起因与取证

实测澄清(test-agent@qwen3.7-max,真 dev DB + tiktoken cl100k):

- **压缩估算只数 message 文本**——`orchestrator/context/compressor.py::estimate_tokens` = Σ `flatten_message` chars // 4(`_CHARS_PER_TOKEN=4`),**不含 `bind_tools` 工具 schema**。所以"固定开销"里被压缩看到的部分 = 首条 SystemMessage(系统提示)文本。
- test-agent 系统提示 ≈ **5.1k tok**,其中 **~92% 是 2 个 eager 技能 body**(pptx 2086 + xlsx 2605 tok,整个 SKILL.md 内联);base 模板 `"You are a helpful assistant."` 仅 7 tok;平台强制子句 ~300 tok(spotlight 144 + `<available-skills>` 头 85 + tool-use enforcement ~60 + current date ~10)。
- 恒 bound 工具 schema ≈ 3-4k tok(10 base capability + 6 技能创作 + find_tools + web_search + http + skill_view + remember)——**真实 API 成本,但不进压缩估算**;MCP(amap-maps)走 deferred → find_tools,不计。
- **"20-24k"是 PR-8 harness 的 `_ASSUMED_OVERHEAD_TOKENS=24000` 假设值,虚高**:把 `head_keep(4)+tail_keep(6)=10` 条保留 filler 消息(每条 8000 char ≈ 2000 tok = ~20k)误算进了"固定开销"。真实固定开销 ≈ 5k(系统提示)+ ~3-4k(工具 schema)≈ **9k**;对 qwen3.7-max 1M 窗口 = 0.9%,即便小窗口(64k)也 ~14%,**不紧张**。

**主因 = eager 技能 body 内联**。`SkillVersion.lazy_load` 默认 `False`(Mini-ADR U-15 的保守选择,注释:"preserve existing behavior so deployed agents do not regress"),每个绑定技能把整个 SKILL.md(926 真实技能实测中位 **1894 tok**,p90 4175,max 18465)灌进系统提示。`agent_factory._render_skill_fragment` 内联全 body;`_render_skill_summary` 仅一行 `<skill ... />`。

### 9.2 对标(deer-flow / Hermes 源码复核)

| | deer-flow | Hermes | Expert Work 现状 |
|---|---|---|---|
| 技能进系统提示 | `<skill><name><description><location></skill>` | compact index:name+description | **全 SKILL.md body 内联** |
| body 何时入 context | 模型按 `location` 路径 `read_file` 按需 | `skill_view` 按需 | 恒在(每轮) |
| 预算紧张 | — | **降级 names-only**(丢 description) | 无 |
| eager 全文 | 仅 `/slash` 显式激活当轮 | 无 | **默认** |
| 每技能固定成本 | ~40 tok | ~40 tok(或更低) | **~1.9k tok** |

出处:deer-flow `backend/packages/harness/deerflow/agents/lead_agent/prompt.py`(`get_skills_prompt_section` 渲染 name+description+location tag;系统提示教模型"call `read_file` on the skill's main file using the path attribute";仅 `/slash` 显式激活当轮注入全文);Hermes `agent/prompt_builder.py`(compact skill index,body 走 `skill_view`;`coding_context.py` 预算紧张时"demotes whole categories to a names-only line","only the descriptions are dropped")。

**两参考框架都是纯渐进式披露(= Anthropic skill 模型),从不默认内联 body**。Expert Work `lazy_load=False` 默认与业界共识相反。Expert Work 已建好 lazy 路(RT-ADR-7 skill_view 引用式 rescue,已 live 证),只差翻默认。

### 9.3 决策

**目标**:技能默认渐进式披露,对齐 deer-flow/Hermes/Anthropic,削减系统提示固定开销。

- **翻 authoring 默认 `lazy_load` False→True**——新建技能默认 lazy。
- **仅翻 curated 平台技能**存量(xlsx/pptx/docx/pdf 等,`tenant_id IS NULL`)到 lazy;**租户自建技能存量不动**(尊重 U-15 不回归)。用户拍板 2026-07-04。
- eager 保留为 opt-in(作者/导入显式 `lazy_load: false`)——给"单个恒相关"技能用(prompt-cache 稳定前缀 + 省一次 skill_view 往返;代价是每轮扛 body token + 更早触发压缩)。

### 9.4 Mini-ADR

- **RT-ADR-11 默认翻转,存量不回归**:`SkillVersion.lazy_load` 默认 `False→True`,统一改各 authoring 入口默认值(protocol DTO `protocol/skill.py` + DB `server_default` 迁移 + persistence `create_*` 签名 base/sql/memory + `author_skill`/`refine_skill`/`fork_skill` 工具 + GitHub 导入 + 平台技能编辑器默认)。**存量 `skill_version` 行存的是显式布尔值,翻 Python/DB 默认对它们零影响 → 零回归**。仅对 curated 平台技能加**定向 migration**(`UPDATE skill_version SET lazy_load=true WHERE skill_id IN (SELECT id FROM skill WHERE tenant_id IS NULL)`)翻 false→true——它们是渐进式披露样板(大流程体、按需读、跨 agent 共享,createdby 空=平台导入)。租户自建存量一律不碰;作者按需显式翻。
- **RT-ADR-12 描述质量是 lazy 的成败关键**:lazy 下模型仅凭 `<available-skills>` 摘要(name+description)判断是否 `skill_view` 拉 body。描述薄 → 漏调 → 回归。curated 技能翻 lazy 必须 eval 验证:agent 面对需该技能的任务仍主动 `skill_view` 并正确完成(不因 body 不预载而漏用)。deer-flow/Hermes 同样把 disclosure 成败押在描述质量上。

### 9.5 改动面 / PR 切分

- **PR-0 设计**:本文档 §9 + ITERATION-PLAN 条目(独立设计 PR 先合)
- **PR-1 后端:默认翻转 + curated 迁移**:protocol/persistence/authoring 各入口默认 False→True + 定向 migration(revision ID ≤32 字符,真 PG 集成测)+ 单测(①新建默认 lazy ②租户存量行不变 ③curated 行翻转)
- **PR-2 前端 + eval 验证**:平台技能编辑器/skill detail 页 `lazy_load` 开关默认态 + i18n 双语(前端审);eval——curated 技能 lazy 后 agent 仍正确经 skill_view 用 xlsx/pptx(RT-ADR-12 描述护栏)

### 9.6 验证

- test-agent 系统提示 before/after:**~5.1k → ~0.4k tok**(2 技能 body 4.7k 塌成 2 行摘要 ~60 tok + 平台子句 ~300)。
- `skill_view` 仍能加载 pptx/xlsx body(RT-ADR-7 引用式 rescue 路径)。
- eval:agent 面对需 xlsx/pptx 的任务仍主动 `skill_view` 拉 body 并正确完成(RT-ADR-12)。
- 新建技能默认 lazy;**租户存量技能行为字节不变**(显式测试钉死)。

### 9.7 范围外(backlog)

- **Hermes 式自适应分层降级**(body → name+desc → name-only,按预算/相关性)——需相关性信号 + 预算护栏,等技能规模上来再议。
- **per-binding manifest override**(`skills: list[str]` → 允许 `{name, disclosure}` 让 agent owner 逐绑定选 eager/lazy,不动共享技能版本)——YAGNI,出现"某 agent 要某共享技能 eager"真需求再加。
- **租户自建存量技能批量迁移**——尊重 U-15 不回归,作者显式翻。

---

## 10. RT-3 prompt cache 成本工程 PR-0 设计(2026-07-04,Wave 2)

### 10.1 起因与取证(现状核实——计划假设已过期)

对标矩阵 RT-3 排 2 vs 3×3。但 grounding 核实发现**后端 cache 工程基本已建好**,计划原设的"cache_control 编排 build"多为幻觉:

- **四段计量端到端已有**(input/output/cache_creation/cache_read):provider 解码(`anthropic.py:735-774` `cache_creation_input_tokens`/`cache_read_input_tokens`→`input_token_details`;`openai.py:552-554` `cached_tokens`→cache_read)→ 中间件(`runtime/middleware/token_usage.py:229-252` 四段 + Prometheus counter by type + 持久化)→ DB(`models/token_usage.py:45-50` migration 0036;`tenant_billing_ledger.py:47-50`)→ rollup(`billing-rollup-job/job.py:143-342` 聚合+定价)→ cost API(`api/usage.py`/`billing_admin.py` 全带四段)→ SDK(`api/usage.ts:25-26` `TokenCounts` 含 cache 两段)。
- **Anthropic cache_control 断点编排已有**:`providers/anthropic.py` `_apply_cache_control`(426-481)——system(1)+ 尾 2 非 system(`_CACHE_CONTROL_TAIL_COUNT=2`)+ ≤1 memory anchor(`per_session` 记忆块 index 1,`expert_work_cache_anchor`),预算≤4(Anthropic 4 断点上限)。`ModelSpec.cache_enabled` 门控。
- **cache 折扣定价列已有**:`models/model_rate_card.py:40-45` `cache_creation_per_mtok_micros`/`cache_read_per_mtok_micros`(server_default 0);rate card 页可编辑;rollup 已按之算成本。

**真实 gap = 前端透出**(捕获+定价+进 SDK,但表格不渲染):
- `SettingsUsage.tsx`:cost/token/kind 三表只 input/output/billed(93-203);cache 仅 2 个 headline tile(317-322),无 per-row/per-model/per-agent。
- `SettingsBillingChargeback.tsx`:**零 cache**(139-258)。
- 无 cache 命中率(`cache_read/(input+cache_read)`)展示,尽管四段数据齐。

**非 gap**:OpenAI/openai_compatible 无 cache_control 是**正确的**——OpenAI 是隐式自动缓存,只需读 `cached_tokens` 回来(已做),无可编排;不是缺陷。

### 10.2 决策

RT-3 收敛为**前端透出为主**(计划的 PR-1 后端编排大半已存在):
- **keep-warm 不做**(RT-ADR-13,用户拍板 2026-07-04):多租户 server 下 heartbeat 保温 = 为空闲租户付费维持缓存,成本模型不划算;Anthropic 隐式 5m TTL 已够常见长对话;openclaw 保温是单用户场景。
- **命中率前端算**(RT-ADR-14):四段已在 SDK,`cache_read/(input+cache_read+cache_creation)` 前端计算,不加后端 metric。
- **节省金额砍(PR-1 实作期 grounding 复议,用户拍板 2026-07-05,RT-ADR-14 修订)**:原设计 §10.4 的"节省金额估算"落地时撞两条硬约束——① 租户 Usage 页有**无泄漏规则**(只显 billed_cost+tokens,绝不露 base/markup/margin),任何 `cache_read×(input价−cache_read价)` 都泄漏价差;② 租户拿不到 per-mtok 定价(rate-card API 是 `require("billing")+is_system_admin`,租户 403);且 Chargeback/Usage 两侧 cost API 都只返聚合 `billed_cost`,无 per-segment/per-model 成本,连 system_admin 也无法纯前端算逐模型反事实。**结论:金额进 backlog,PR-1 只做命中率 + 缓存 token 列**(命中率是 token 派生量、非价格,两页皆合规;命中率即 COGS 有效性信号,满足 RT-3 毛利可见目标)。若日后要硬金额=后端 rollup 补 `cache_savings_micros`(有全部 per-segment 价,权威算),只上 system_admin Chargeback。
- **tool-def 断点推迟**(RT-ADR-15,backlog):deer-flow 断点花在 last tool def,Expert Work 花在 memory anchor;两者都在 Anthropic ≤4 预算内,换 tool-def 会挤掉 memory anchor(per_session 记忆稳定性),收益边际,不在本轮。
- **cache 定价默认 0** 是运营数据(operator 设 rate card),非代码项。

### 10.3 Mini-ADR

- **RT-ADR-13 keep-warm 不做**:多租户成本模型不支持主动保温;维持 Anthropic 隐式 ephemeral(默认 5m),无 1h 长 TTL、无 heartbeat 刷新环。需求(超长空闲对话+缓存命中价值 >> 保温成本)出现再议。
- **RT-ADR-14 命中率纯前端派生**:`cache_read/(input+cache_read+cache_creation)` 由 SDK 四段前端算;不新增后端指标/API 字段(避免与 metrics.py validator 漂移)。
- **RT-ADR-15 tool-def 断点推迟**:Anthropic 4 断点预算已占满(system+2 tail+≤1 anchor);tool-def 断点需挤掉 memory anchor,得不偿失,backlog。

### 10.4 PR 切分

- **PR-0 设计**(本节 + ITERATION-PLAN)
- **PR-1 前端透出**(唯一实作 PR):`SettingsUsage.tsx` cost/token/kind 三表补 cache_read/cache_creation 列 + 命中率列 + 头部命中率 Statistic;`SettingsBillingChargeback.tsx` 租户表 + per-agent 下钻表补 cache 列 + 命中率;共享 `utils/cache.ts`(`cacheHitRate`/`formatHitRate`,pure 单测);i18n 双语;前端审(tsc -b/axe/vitest,改 nav 无——纯表格列);envelope-vs-raw 对账(usage API 已返四段,SDK 已有字段,**零后端改动**)。**节省金额已砍**(见 §10.2,无泄漏规则+无 per-segment 价)。
- (无后端 PR——编排+计量+定价已存在;金额估算需后端权威算,进 backlog)

### 10.5 验证

- 长对话前后 cache_read 占比可量化(SettingsUsage 命中率列);计费 cache 折扣价对账(Chargeback cache 列 × rate card)。
- 纯前端改动:vitest 表格渲染 + axe;无后端回归风险。

### 10.6 范围外(backlog)

tool-def 断点(RT-ADR-15)、1h 长 TTL / keep-warm(RT-ADR-13)、cache 命中率后端 metric+告警(现前端派生够用)。

---

## 11. RT-4 全局 kill switch PR-0 设计(2026-07-04,Wave 2)

### 11.1 起因与取证(现状核实)

安全兜底(OWASP ASI08),企业审计必问。grounding 核实:

- **租户级 suspend 已有**:`TenantStatus = active|suspended`(`protocol/tenant_config.py:70`,migration 0053);`TenantStatusService.is_suspended`(`tenant_status.py:41`,TTL 缓存);enforcement 在 auth 中间件(`auth/middleware.py:168` 403)+ run 准入(`api/runs.py:840` `TENANT_SUSPENDED` 403)。API `api/tenants.py:302 deactivate`/`:321 activate`;前端 `SettingsTenants.tsx:137-151` Popconfirm+danger。**但只拒新 run,不杀运行中**。
- **agent 级 disable 缺**:`AgentSpecStatus = active|deprecated|deleted`(`agent_spec.py:1311`),`deprecated` 不在运行期门控;无 enable/disable API;`_resolve_session` 只要 ACTIVE(`api/agents.py:289-296`)。
- **bulk-cancel 缺**:`RunManager.cancel`(`runtime/runs/manager.py:371`,set `abort_event`)只按 run_id;无租户/agent 级批量取消。取消链:`RunManager.cancel`→`abort_event.set()`→`CancellationToken`(`runtime/cancellation.py`)→节点 `raise_if_cancelled`/`run_cancellable`→`RunCancelledError`→run 终态 INTERRUPTED。
- **queue claim**:`RunQueueWorker._claim_and_start`(`run_queue_worker.py:154`)→`claim_queued` CAS(`runs/store.py:287`);无 disable 检查。
- **可复用先例**:`KillSwitch` 模型(global/tenant scope,`skill.py:408`,`skill_evolution_kill_switch` 表)+ `get/set_kill_switch`;审计单一 `AuditAction` StrEnum(`protocol/audit.py:22`,含 `TENANT_DEACTIVATE`/`SESSION_CANCEL`)——`ResourceType` 才是双 Literal(`control-plane/audit.py:111` + protocol),加新 resource 要两处同步([memory:audit-literal-drift])。

### 11.2 决策

**两级紧急停止,拒新 + 终止运行中 + queue 拒 claim 对称**(用户拍板 2026-07-04):
- **agent 级 disable 净新**:新 disable 记录 +(tenant_id, agent_name)缓存服务 + 三 gate(准入/claim/_resolve_session)+ 反向 enable。
- **租户级补终止**:复用 suspend,补 bulk-cancel 运行中 run(现只拒新)。
- **恢复对称**:enable/activate 后正常。

### 11.3 Mini-ADR

- **RT-ADR-16 agent 级 disable = 独立(tenant_id, agent_name)记录 + 缓存服务**:不塞进 `AgentSpecStatus`(disable 是可逆的紧急操作,与 lifecycle deprecated/deleted 正交,且 agent 按 (name,version) 存行——disable 要盖 name 下所有版本,需独立 scope)。新表 `agent_disable`(tenant_id, agent_name, disabled, reason, disabled_by, disabled_at)+ `AgentDisableService.is_disabled`(TTL 缓存,镜像 `TenantStatusService`,kill-switch 专用 TTL 缩到 5s——急停传播不能等 60s)。**gate 必须盖每一条 run-spawn 路,不只前门 3 个**(独立 review 逮到 trigger/resume/orphan 三处绕过——3-gate 设计不完整):统一走共享 `kill_switch.run_block_reason(tenant_status, agent_disable, tenant_id, agent_name)`(调用者须已在 tenant RLS scope):① 准入 `api/runs.py`(拒新 run,sync+queue 两模式)② `_resolve_session`(拒新会话/run)③ `RunQueueWorker._is_killed`(拒 claim——**整个 body 含 is_suspended 读必须包 `_tenant_scope`,否则 FORCE-RLS 的 tenant_config 无 scope 读返 0 行→gate 静默失效+毒化缓存**)④ `trigger_firing.fire_trigger`(cron/webhook 触发,否则无人值守自驱 agent 无视急停)⑤ **审批延续 choke point** `resolve_approval_decision`(**在 `mark_decided` CAS 之前** gate——HTTP resume + batch decide + timeout sweep 三路都经此;放 CAS 后会消费决策却 403 spawn 成坏状态;放前则 disabled agent 的审批留 PENDING 可逆)⑥ `orphan_sweep._respawn`(重生的 reclaim run 也 gate;命中则转 INTERRUPTED 而非 skip,避免 re-orphan 死循环)。
- **RT-ADR-17 bulk-cancel = 新 `list_running_runs(scope)` + 本地 `RunManager.cancel` + 跨副本 `RunStore.request_cancel`**:disable agent → 枚举该 (tenant,agent) 的 RUNNING run(agent_run JOIN thread_meta,因 run 不存 agent_name)→ 逐个停;tenant suspend 补同(枚举 tenant 全 RUNNING,复用 `list_for_tenant`)。每个 cancel 落审计。**跨副本机制(实现期修正——原设想"abort_event DB 持久"有误)**:`abort_event` 是**进程内存** asyncio.Event,`RunManager.cancel(run_id)` 只对**本副本 registry** 里的 run 生效(别副本持有返 False 且零副作用)。真跨副本停法 = 本副本命中则即时 `RunManager.cancel`(set abort_event + status);未命中(别副本持有)则 `RunStore.request_cancel`——**guarded** `UPDATE ... status→interrupted WHERE status IN (running,pending)`(绝不覆盖刚 SUCCESS 的 run),持有副本下次 **lease heartbeat CAS**(`status='running' AND claimed_by=owner`,store.py:257)随即失败 → `_renew_lease` set 那个副本的 abort_event → run 在一个 heartbeat 间隔(lease_ttl_s/3)内停。即复用 9.4 HA 的 heartbeat CAS 做跨副本信号,无需改热执行循环。
- **RT-ADR-18 审计**:新 `AuditAction` 成员 `agent:disabled`/`agent:enabled`(`protocol/audit.py:22` 一处);新 resource_type `"agent"` 要加**两处** `ResourceType` Literal(`control-plane/audit.py:111` + protocol,[memory:audit-literal-drift]);tenant suspend bulk-cancel 复用 `TENANT_DEACTIVATE` + 每 run `SESSION_CANCEL`。

### 11.4 PR 切分

- **PR-0 设计**(本节 + ITERATION-PLAN;kill switch 是小项,设计可与 PR-1 同分支分 commit,但设计先合)
- **PR-1 后端**:`agent_disable` 表 + migration(revision ID ≤32,真 PG 集成测)+ `AgentDisableService` + 三 gate + `list_running_runs(scope)` + bulk-cancel(agent disable + tenant suspend 两路)+ disable/enable API(`POST /v1/agents/{name}/disable|enable`,镜像 tenants deactivate 形)+ 审计(RT-ADR-18)+ 单测(disable 时运行中数秒终止/queued 不被 claim/审计齐/恢复正常)
- **PR-2 前端**:`AgentDetail.tsx` 加禁用/启用 danger 按钮(净新,复用 `SettingsTenants` Popconfirm+danger 模式)+ 状态标识(现 STATUS_COLOR tag 补 disabled)+ 租户设置页紧急停止已有(suspend UI 在,bulk-cancel 是后端,前端可补"停止时终止运行中"提示)+ i18n 双语 + 前端审

### 11.5 验证

- E2E:禁用 agent 时运行中 run 数秒内 INTERRUPTED、queued run 不被 claim、准入拒新、审计条目齐;enable 后正常。
- 租户 suspend 补 bulk-cancel:suspend 时该租户全运行中 run 终止(之前只拒新)。
- 真 PG:`agent_disable` 表 + 跨副本 abort_event 终止(集成测)。

### 11.6 范围外(backlog)

按 run/session 粒度的选择性 kill(现两级够);disable 定时自动恢复(手动 enable 够);平台级"全租户暂停"总闸(tenant 逐个够,需求出现再议)。

---

## 12. RT-6 审批工件绑定 PR-0 设计(2026-07-06,Wave 3)

### 12.1 起因与取证(现状核实——计划前提部分已过期)

HITL 的 TOCTOU 弱点,企业安全卖点(approval 移植 OpenClaw approval-time binding)。**计划立项时的前提是"审批绑意图、args 会在批准与执行间漂移";实读源码发现 Expert Work 的 args 本就被 checkpoint 冻结,前提部分不成立**——据此收敛真实威胁面。grounding 核实:

- **argv/args 已隐式绑死**:`apply_resume_decision`(`graph_builder/_approval.py:189`,调用点 `graph_builder/builder.py:1013`)approve 时**原样派发 checkpoint 里的 tool_calls**;审批行 `ApprovalRecord.proposed_args`(`protocol/approval.py:139`)与派发源同出一个 checkpointed tool_call。→ "所见即所执行" 对 **args 本身**已隐式成立,正常流不存在漂移路径。
- **exec_python(`code` 内联,`sandbox.py:681`)/ http(`method/url/headers/body` 内联,`http.py:98`)→ 全参数内联入 args**,无外部可变工件面,args 冻结即完全绑定。
- **bash(`command` shell 串,`bash.py:120`)= 唯一真漂移面**:命令串本身内联冻结,但它**引用的 workspace 文件内容可变**(`./deploy.sh`)。且 **workspace 跨 run/跨副本按 user 共享**(bash.py:11-15 注释 + TE-8 `WorkspaceLock`,`tools/locks.py`)→ run A 挂起等审批时,同 user 的**并发 run** 可改 deploy.sh = 活 TOCTOU(非纯理论)。任意 shell 引用哪些文件**静态不可判定**。
- **modify 路径**:reviewer 改 `modified_args`(`ApprovalDecision`,`protocol/approval.py:87`)→ `apply_resume_decision` 用 resume payload 的 modified_args 重写 gated call → 执行的是改后 args。
- **落库无 binding 列**:`AgentApprovalRow`(`persistence/models/agent_approval.py`,migration 0031+0080)现只存 proposed_args/modified_args,无指纹/代际列;`SqlApprovalStore`(`persistence/approval/sql.py`)create/mark_decided。决策原子写走 Stream 13.2 的 `mark_decided` CAS(idempotency_key/continuation_run_id 已搭同一 UPDATE)。
- **审计**:`AuditAction` StrEnum(`protocol/audit.py:22`,一处);`ResourceType` 双 Literal(`control-plane/audit.py:111` + protocol,[memory:audit-literal-drift])。
- **前端**:`run_detail/ApprovalCard.tsx` Monaco 展 proposed_args + modify/approve/reject;`ApprovalsList.tsx` 队列;IM webhook 审批卡片走 payload_format(feishu/dingtalk/wecom)。

### 12.2 决策

**双层绑定:Tier A args 指纹硬拦 + Tier B bash workspace 漂移察觉(审计-only)**(用户拍板 2026-07-06,约束="不削弱 Agent 能力、不增加操作复杂度")。

约束排除了所有会闭合文件层 TOCTOU 的硬办法:声明式 bound_paths(agent/reviewer 补路径=增操作复杂度)、启发式抽取+硬拦(合法文件变动如追加 log 被误杀=spurious reject=削弱 agent)、全树 Merkle 硬拦(并发合法写全触发)。故切成:
- **Tier A(全 gated 工具,硬拦,零假阳)**:对 canonical args 算指纹,exec 前重算比对,漂移即拒+审计。正常流恒匹配,只在 checkpoint 层被篡改/replay/bug 时触发。价值=密码学"审批啥=执行啥"可证凭据 + checkpoint 层防篡改(隐式保证→显式可审计)。**拦得起是因为零假阳、零 agent/operator 改动**。
- **Tier B(仅 bash,审计-only,不拦)**:记 workspace 写代际,exec 时若前进→不拦,审计/事件打 `workspace_drift=true`+代际差,UI 徽标。闭合 bash TOCTOU 的**问责半**(永远可事后证明/察觉文件层是否被动过),零假阳、零摩擦。
- **诚实边界(不伪装,`feedback_no_design_choice_disguise`)**:Tier B **不阻止** approve-then-swap-script,只保证察觉+可证;真硬拦文件层必破上述约束,留 per-manifest opt-in backlog(默认关)。此切法即 [memory:audit-over-blocking]——"怕滥用的硬拦改 allow+全审计",文件层硬拦正是过度拦截,故意不做;args 层可证安全才硬拦。

### 12.3 Mini-ADR

- **RT-ADR-19 Tier A = canonical args 指纹,存**独立完整性域**,硬拦漂移**:mint 时(`build_approval_request`,`_approval.py:124`)对 gated tool_call 的 args 算 `binding_digest`=`sha256(canonical_json(args))`,canonical=`json.dumps(args, sort_keys=True, separators=(",",":"), ensure_ascii=False, default=str)`(确定性序列化,盖 dict 无序 + 嵌套 + 非 JSON 原生值)。**digest 必须存审批行(`agent_approval`,Postgres),绝不只放 checkpoint 的 `ApprovalRequest`**——若 digest 与 tool_calls 同存 checkpoint,攻击者篡改 checkpoint 可连 digest 一起改,防篡改失效;审批行是与 checkpoint **独立的完整性域**。校验挂 `builder.py:1013`(apply_resume_decision 派发前):对将派发的 tool_call args 重算 digest,与**从审批行经 resume 端点透入 `approval_resume` payload 的 `binding_digest`** 比对。不合 → 不派发,回 reject `ToolMessage`(status=error),`terminal=True`(平台完整性 veto,路由 END,区别于 reviewer reject)+ 审计 `approval:binding_drift`。resume 端点(`api/runs.py` 的 `resolve_approval_decision`,~:524)读 `ApprovalRecord.binding_digest` 塞进 `approval_resume`(现载 decision/modified_args/reason,加一字段)。零假阳论证:approve 时 exec 的 args 恒等于 mint 的 proposed_args,digest 必合。
- **RT-ADR-20 Tier B = workspace `last_write_at` 时间戳,审计-only 不拦(PR-1b 定案,PR-1 分拆)**:实现期核实两候选后**都不取**——① "复用 TE-2 审计 COUNT" 不可行(`AuditQuery` 无 user_id 过滤、actor 恒 "agent",无法按 user-workspace scope);② 独立 gen 计数器 + agent_approval 加 `workspace_gen` 列 + mint-read 三处接线偏重。**定案 = `user_workspace` 加 `last_write_at TIMESTAMPTZ`**(该表 per-(tenant,user) 且**无 RLS**,migration 0018 明注),`requested_at` 已在审批行做 mint 基线 → **免 mint-read、免 agent_approval 加列**。写路径:`PgWorkspaceLock.acquire`(write_file/edit_file/bash **三者共用**该锁)成功写后 bump `last_write_at=now()`——**在独立 best-effort txn**(不在 advisory-lock txn 内:表缺/瞬时错绝不毒化 lock 破坏 exclusion 契约;审计-only 漏 bump 可接受)。drift 判定在 `resolve_approval_decision`(control-plane 有 DB):`reason_kind=="policy_gate"` ∧ 有 user_id ∧ `last_write_at > requested_at` → APPROVAL_DECIDED 审计 detail 打 `workspace_drift=true`(不拦)。该 read 在 `mark_decided` CAS 后/spawn 前,**必须 try/except 吞错→False**(review MEDIUM:否则纯取证 read 抛错会在决策已消费后 500,retry 走"已决"replay 不再 spawn = 合法 run 永久卡死,违"永不拦")。**诚实边界**:①信号是**"写能力工具(write_file/edit_file/bash)执行过"的保守过近似**,非"确定改了"——只读 bash(ls/cat)也持锁也 bump(review MEDIUM);宁多报不漏(取证安全方向),前端徽标文案须如实"审批后有写工具执行/workspace 可能有改动",不写"确定改了";②粗粒度到 workspace 级(不精确到被批命令引用的文件),审计-only 故零误杀;③`NullWorkspaceLock`(单进程)不 bump → drift 恒 False,**连 in-process 并发 run 的 swap 也漏**(review LOW),仅 `PgWorkspaceLock`(多副本 prod)信号有效。前端徽标本身并入 PR-2(GET pending 面加 drift 字段 + 卡片渲染)。
- **RT-ADR-21 binding 字段模型 + modify 重铸 + 落库 + 审计**:`ApprovalRequest`/`ApprovalRecord`(frozen)加 `binding_digest: str` + `workspace_gen: int | None`;`AgentApprovalRow` 加两列 + migration(revision ID ≤32,如 `0120_approval_binding`,真 PG 集成测)。`SqlApprovalStore.create` 写入、`_row_to_dto` 读出。**modify 重铸**:reviewer modify → 执行 modified_args → 决策时**重算 digest 绑 modified_args**,与 `mark_decided` CAS **原子同写**(搭 Stream 13.2 已有的决策 UPDATE,不新开事务);审计记 modify + 新 digest。审计新成员 `approval:binding_verified`(detail 携 workspace_drift)/`approval:binding_drift`(`protocol/audit.py` **一处** StrEnum,不新增 ResourceType——复用现有 approval/run resource,避开双 Literal 漂移)。

### 12.4 PR 切分

- **PR-0 设计**(本节 §12 + ITERATION-PLAN;设计先合)
- **PR-1 后端**:协议 binding 字段(`ApprovalRequest`/`ApprovalRecord` 加 `binding_digest`/`workspace_gen`)+ canonical 指纹助手(`_approval.py`,pure)+ mint 算指纹 + workspace 代际记录(bash,RT-ADR-20 选定机制)+ exec 前校验硬拒(`builder.py:1013` + resume 端点透 digest)+ modify 重铸原子写(`mark_decided` CAS)+ migration(binding 两列,≤32,真 PG)+ 审计(RT-ADR-21)+ 单测(指纹匹配放行/篡改 digest 拒+审计/modify 重铸放行/bash workspace drift 审计标不拦/exec_python·http workspace_gen 为 None 且指纹全程匹配)
- **PR-2 前端(已交付)**:**后端小补** = GET run-detail pending 面加 `binding_digest`(Tier A receipt)+ `workspace_drift`(Tier B **GET-time 实时算**——resume 审计只在决策时太晚,pending 卡片要即时警示;抽 `_workspace_drift` helper 供 resolve+GET 复用,try/except 永不拦)。`ApprovalCard.tsx` 展 `binding_digest`(短指纹 receipt + Tooltip)+ "审批后 workspace 有改动"警告 Alert(Tier B,决策前警示,文案如实"写工具执行过/可能有改动")。IM webhook `approval.requested` payload 补 `action_summary`/`reason_kind`/`binding`(短指纹)——`_im_text` 通用渲染 scalar 字段自动进 feishu/dingtalk/wecom 卡片,零模板改。i18n 双语 + tsc-b/vitest 全量。**范围收敛(实施拍板)**:①`binding_drift` 硬拒态**不加 ApprovalCard 特殊 UI**——终态 reject message「[approval binding drift]」已在 run 事件流可见,卡片只在 PENDING 显;②`ApprovalsList` drift 标识**砍**——列表 drift 需 per-item N 次 workspace 读,成本/粒度不划算,pending 卡片实时警示已够。

### 12.5 验证

- E2E:① 审批 bash 后**并发改脚本** → exec 察觉 drift 审计标(不拦,Tier B);② **篡改 checkpoint tool_call args** → exec `binding_drift` 硬拒 + 审计(Tier A,terminal);③ modify 路径重铸绑定正常执行;④ exec_python/http 指纹全程匹配放行、workspace_gen 恒 None。
- 真 PG:binding 两列 migration + 跨副本 resume 校验(digest 从审批行独立域读)。

### 12.6 范围外(backlog)

文件层**硬拦**(声明式 bound_paths / 启发式路径抽取)= per-manifest opt-in,默认关(破"不削弱能力+不增复杂度"约束,需求出现再议);workspace 代际精确到 per-file(现 workspace 级信号够);env/cwd 绑定(cwd 恒 `/workspace`、env 平台控,无 per-call 变量面)。

## 13. RT-5 生产质量监控 PR-0 设计(2026-07-06,Wave 3,★5 需 live E2E)

### 13.1 起因与取证(现状核实——计划三处"复用"假设过期)

商业化卖点:租户可见的 per-agent 生产质量看板 + 质量漂移主动告警。计划立项前提是"复用 `eval_engine_live` 采样管道 + `_judge.py` 经 RT-1 结构化输出判分 + 复用 webhook 事件"。grounding 核实这三处**复用目标都不是计划描述的形态**(产品意图不变,只改"复用谁"):

- **① `eval_engine_live.py` 跑合成数据集 + 确定性判分,非真实流量 LLM 采样**(`control-plane/eval_engine_live.py`):`LiveEvalHarness`(:167)从 YAML 数据集(`tools/eval/datasets/adversarial|trace/m0_baseline.yaml`)取 prompt,in-process `AgentBuilder` 拉一次性无工具 eval agent(`_build_spec` :223)`graph.ainvoke`,判分是**确定性**的——`AdversarialEvalEngine`→`safety_verdict`(:124,canary/refusal 布尔)、`TraceEvalEngine`→`evaluate_trace`(:155,OTel span 结构断言),**不消费 `_judge`、不采真实流量**。可复用的仅**脚手架**:`EvalWorker`/`eval_run`/`eval_case_result` 队列-claim-persist 状态机、`DispatchEvalEngine` 按 suite 路由、in-process 拉 agent 模式。"采样真实流量 → LLM 判分 → 时序落库"链**不存在,需新建**。
- **② `_judge.py` 绕开 router + 正则抠单 int,未接 RT-1**(`tools/eval/_judge.py`):`JudgeProvider.score(...)->int`(:46,单个 [1,5]);`AnthropicHaikuJudge`(:65)**直连** `httpx.post("https://api.anthropic.com/v1/messages")`(:88)、`_DIGIT_RE=re.compile(r"[1-5]")` 正则抠首个数字(:149-152),仅 `_AnthropicMessagesReply`(:128)pydantic 校验响应外壳,**无 `response_format`/`json_schema`**。RT-1 结构化输出**确已落地但在 orchestrator router 侧**:`llm/router.py:384-424`(`output_schema` 校验重试 + `LLMOutputValidationError`)、`llm/structured_output.py`(`StructuredOutputCapability` native/tool_call/prompt + `validate_structured_output`)、provider 适配(openai native `response_format:{type:"json_schema"}`、anthropic `tool_call` 模拟、openai_compatible `prompt`);消费方=`aux_model_adapter`/`memory_consolidator`/`skill_distiller`,**不含 `_judge`**。→ RT-5 判分**走 RT-1-correct 的 router `output_schema` 路径,不复用 `_judge`**(`_judge` 是离线 eval CI 契约,单 int、绕 router,不适生产结构化质量分)。
- **③ webhook 全挂 run_event spine,`quality.drift` 无现成非-run 入队路径**(`protocol/webhook.py:32` 注释 "All ride the single `run_event` spine"):现 5 类事件(`run.completed`/`run.failed`/`approval.requested`/`artifact.saved`/`skill_promote.requested`)`event_id`=`{run_id}:{seq}`、从 run 事件帧派生(`webhook_delivery_worker.py:298-392`)。`quality.drift` **非 run 事件**、无 run_id/seq;协议 `run_id` 已 `UUID | None`、payload `dict[str,object]` → 需**新建脱 run_event spine 的 emitter**(run_id=None、自造 `event_id`)。`payload_format`(feishu/dingtalk/wecom)`render_channel_body`(`webhook_delivery_worker.py:149`)与事件类型**正交,可直接复用**。事件类型注册**分散 4 处需同步**:`protocol/webhook.py:34` Literal、`api/webhook_endpoints.py:47` `_EVENT_TYPES` frozenset、`webhook_delivery_worker.py:119` `_EVENT_TITLES`(缺失优雅回退)、`admin-ui/src/api/webhooks.ts:14+21`(union + 数组)。
- **④ 无现存质量监控脚手架 + 命名撞车**:搜 quality/drift/sampling/score 无生产质量监控/漂移/采样表模型 API(drift 命中全无关);相邻物(**勿混/勿当基座**)=SE-16 技能进化质量信号(`skill_evolution_assembly.py:36` 采样 + 👍👎 curation)、eval 评分(`eval_run`/`eval_case_result.scores` JSONB,suite 维度非 per-agent 连续)、**PI-3-A3 平台防御 judge**(`settings_platform/PlatformJudgeSection.tsx` + `platform_judge_config.py`,配 output_judge 防御模型,**非质量监控**)。**无 per-agent 质量分时序表**(`token_usage` 形状最近但语义=token/成本;`memory_item_scores` 无关)。
- **⑤ aux 计量惯例可复用**:表 `token_usage`(`persistence/models/token_usage.py:21`,`usage_kind` 于 `0110` 加入、默认 `'conversation'`,per-(tenant,agent_name,model) 时序索引);写账 `TokenUsageStore.insert(TokenUsageRecord)`;范例 `memory_consolidator.py:724 _record_aux_usage`(固定 `agent_name="memory-consolidator"`/`agent_version="-"`/`usage_kind="memory_consolidation"`,try/except never-fail)、`skill_evolution_metering.py:36`。**RT-1-correct 产 token 路径** `aux_model_adapter.py:53 LLMRouterAuxModelAdapter`(`CredentialsResolver`→`build_llm_router`→`router(messages, tools=[], output_schema=...)`→带 `input/output_tokens`)——已直通 RT-1 校验,但类型名 `Consolidator*` 语义耦合(定义在 `memory_consolidator.py`)。migration head=`0116_workspace_last_write`(确认),新表从 0116 起接。

### 13.2 决策

**产品意图不变**(真实流量采样 → LLM 判分 → per-agent 质量时序 → 滑窗漂移 → `quality.drift` 告警 + 质量看板);grounding 只改"复用谁"不改"做什么"。核心决策:

- **采样 = pull 式 worker + 水位,脱离 run 热路径**:新 `QualityMonitorWorker`(仿 `EvalWorker`/orphan_sweep/TranscriptMirrorSweep 的 claim-sweep 模式,advisory-lock/CAS 跨副本安全)按水位扫**已完成 run**,per-tenant **确定性 hash(run_id) vs 采样率** 选样(可复现、免随机、免改 orchestrator run 完成路径)。transcript 源(event_store vs thread_message 镜像)PR-1 落定。**成本护栏**=采样率(默认低,如 5%)+ per-tenant 每日样本上限(硬顶)。
- **判分 = RT-1 router `output_schema` + 结构化 rubric**(不复用 `_judge`):`QualityScore` schema(`overall: int 1-5` + `dimensions` + `rationale`)。判分 LLM 走 router `output_schema` 路径(credential-resolve→`build_llm_router`→`router(..., output_schema=QualityScore)`);**复用 primitive 不复用 `Consolidator*` 命名**(PR-1 二选一:轻量提取中性 aux caller / 直接用 router primitives 建专用 caller,避免 touch `memory_consolidator`)。rubric=**通用 agent 质量**(是否回应请求 / 连贯性 / 安全),默认 rubric + 后续可调(Expert Work 是通用平台,不绑当前业务域)。
- **落库 = 新 `quality_score` 表**(per-tenant/per-agent/per-run 时序 + `dimensions` JSONB + `judge_model` + `observed_at` + RLS),`token_usage` 形状参考、语义独立。
- **aux 计量** `usage_kind="quality_sampling"` + 固定 `agent_name="quality-monitor"`,`TokenUsageStore.insert`,与 memory_consolidation 对称(try/except never-fail)。
- **漂移 = `QualityDriftWorker` 滑窗基线偏离**(per-agent 近窗均值 vs 基线窗,相对跌幅超阈 ∧ 最小样本量),命中 → `quality.drift` 事件;`quality_drift_alert` 落库做 **cooldown/dedup**(不重复轰炸)。
- **`quality.drift` webhook = 新 emitter 脱 run_event spine**(run_id=None、自造 event_id),复用 `render_channel_body` payload_format;4 处注册点同步。
- **质量看板新页**(非复用防御 judge section),全套接线;质量配置(采样率/judge 模型/漂移阈值/日上限)= 平台默认 + 租户覆盖。
- **诚实边界(`feedback_no_design_choice_disguise`)**:①采样是**抽样非全量**(成本 vs 覆盖权衡,采样率+日上限是唯二旋钮),漏采的 run 无质量分,前端文案须如实(非"全量质检");②判分是 **LLM 主观分非 ground-truth**(rubric 决定语义,无金标),看板呈现"judge 评分"而非"客观质量";③漂移是**统计信号非因果**,告警=人看研判,**不自动处置**(自动降级/切模型耦合 RT-7,不并入);④`quality.drift` 仅在配了 webhook endpoint 的租户送达,未配=仅看板可见。

### 13.3 Mini-ADR

- **RT-ADR-22 采样管道 = pull worker + 水位 + 确定性选样**:新 `QualityMonitorWorker`(control-plane,门控 `enable_quality_monitor`,仿 `EvalWorker` app.py 接线)。跨副本安全靠 advisory-lock/水位 CAS(复用 9.5 队列/sweep 模式),扫"上次水位后已完成的 run",per-run 判 `deterministic_hash(run_id) % 10000 < rate*10000`(可复现;避随机→避副本重复采样),命中且未超**per-tenant 日上限**→取 transcript(user prompt + final agent reply)→ 判分。**不改 orchestrator run 完成路径**(纯消费侧,run 热路径零侵入=成本护栏之一)。transcript 源 PR-1 定(优先 `thread_message` 镜像,已 per-user 落账;event_store 兜底)。范围外:push 式(run 完成即入队,待规模信号)。
- **RT-ADR-23 判分 = RT-1 router `output_schema`(不碰 `_judge`)**:`QualityScore` = `StructuredOutputSpec`/pydantic:`{overall: int[1..5], dimensions: {addressed_request: int, coherence: int, safety: int}, rationale: str}`。判分 caller 走 `CredentialsResolver`→`build_llm_router`→`router(messages=[rubric_system, transcript], tools=[], output_schema=QualityScore)`→ RT-1 校验重试兜底坏结构。**不引 `ConsolidatorLLMReply/AuxModel` 类型**(语义耦合 memory 域);PR-1 二选一:(a) 从 `aux_model_adapter` 轻量提取中性 `AuxLLMCaller`(consolidator/quality 共用,DRY),(b) 用 router primitives 建独立 `QualityJudge`(surgical、零 touch consolidator)——**推荐 (b)**(surgical-changes 优先,提取重构留 backlog)。判 token 走 `usage_kind="quality_sampling"` aux 计量。rubric=通用 agent 默认(可调),judge 模型走**质量配置**(平台默认+租户覆盖),**与 PI-3-A3 防御 judge 配置分离**(语义不同,不复用同表避混淆)。
- **RT-ADR-24 落库 `quality_score` + 漂移 `QualityDriftWorker`**:`quality_score`(`tenant_id`/`agent_name`/`agent_version`/`run_id`/`overall`/`dimensions JSONB`/`judge_model`/`observed_at`,RLS per-tenant,索引 `(tenant_id, agent_name, observed_at)`)。漂移 worker(周期,per-agent):近窗均值 `recent` vs 基线窗均值 `baseline`,`(baseline-recent)/baseline > drift_threshold` ∧ `recent_count >= min_samples` → 漂移。`quality_drift_alert`(`tenant_id`/`agent_name`/`detected_at`/`window_stats JSONB`/`state`/`cooldown_until`)做 **cooldown**(同 agent 冷却窗内不重复告警)+ 历史。migration 0117(config+score,PR-1)/0118(drift_alert,PR-2),revision ID ≤32,真 PG 集成测。
- **RT-ADR-25 `quality.drift` webhook emitter 脱 run_event spine**:`WebhookEventType` 加 `"quality.drift"`——**4 处同步**(`protocol/webhook.py:34` Literal / `api/webhook_endpoints.py:47` `_EVENT_TYPES` frozenset + `_validate_event_types` / `webhook_delivery_worker.py:119` `_EVENT_TITLES` / `admin-ui webhooks.ts:14+21`)。新 emitter 构 `WebhookDeliveryRecord(run_id=None, event_id=f"quality.drift:{agent_name}:{alert_id}", payload={agent_name, overall_recent, overall_baseline, drift_pct, window, detected_at})`,投递复用现有 delivery worker + `render_channel_body`(feishu/dingtalk/wecom,与事件类型正交)。**诚实**:现有 delivery 假设 run_id/seq,emitter 侧需显式构造合成 id(PR-2 核实 delivery/去重路径不隐依赖 run_id 非空)。
- **RT-ADR-26 前端质量看板新页 + 配置**:新页(路由如 `/observability/quality` 或 `/quality`)=分数趋势图(per-agent 时序)+ 低分 run 下钻(链 `run_detail`)+ 漂移告警列表。**全套接线**(`router.tsx` / `navModel.ts` 三组之一 / `CommandPalette.tsx` / i18n en+zh `nav.*`+页面 key / SDK `api/quality.ts` / `*.stories.tsx` / form aria-label)。质量配置 UI(采样率/judge 模型/漂移阈值/日上限)=平台区块(仿 `SettingsPlatformConfig` 挂 section)+ 租户覆盖。`WebhooksList` event_types 数组加 `quality.drift`。envelope-vs-raw 对账后写 SDK。

### 13.4 PR 切分

- **PR-0 设计**(本节 §13 + ITERATION-PLAN;设计先合)
- **PR-1 后端 采样+判分+落库+计量**:质量配置(平台默认+租户覆盖,新表/service)+ `QualityMonitorWorker`(pull+水位+确定性采样+日上限护栏,app.py 门控接线)+ `QualityJudge`(RT-1 router `output_schema` + `QualityScore` rubric schema,不碰 `_judge`)+ `quality_score` 表 + migration 0117(config+score,RLS,真 PG)+ aux 计量(`usage_kind="quality_sampling"`)+ 单测(采样确定性/日上限截断/judge 结构化分/坏结构重试/aux 计量/真 PG round-trip + RLS)
- **PR-2 后端 漂移+告警**:`QualityDriftWorker`(滑窗基线偏离 + min_samples)+ `quality_drift_alert` 落库(cooldown/dedup/历史)+ `quality.drift` webhook 事件(4 同步点 + 新 emitter 脱 spine)+ migration 0118(drift_alert)+ 单测(漂移触发/cooldown 不重复/无样本不误报/payload_format 渲染/真 PG)
- **PR-3 前端 质量看板**(已交付):后端薄读 API(`/v1/quality/scores`+`/drift-alerts`,home-tenant RLS,raw payload)+ 新页(趋势图=依赖-free SVG sparkline/低分下钻链 run_detail/漂移列表)+ SDK `api/quality.ts` + 全套接线(router/navModel/CommandPalette/i18n 双语)+ Storybook + tsc-b/vitest 全量。`WebhooksList` 的 `quality.drift` 已于 PR-2 加。**配置 UI(平台+租户)拆 PR-3b**——config 表/service 地基在 PR-1 已延后,与 UI 一起单开,不捆入前端 PR(本次用户拍板)。
- **PR-3b 质量配置 UI**(后续):`quality_config` 表 + service + platform API + 租户覆盖 + 平台/租户配置 UI(采样率/judge 模型/漂移阈值/日上限)。
- **PR-4 live E2E(★5)**(harness `tools/eval/verify_live_quality.py` + `manifests/quality-test/`;**★5 live 已实证 2026-07-06**):真流量采样跑通(采样率生效 + 分落库 + aux 成本可见)+ 注入劣化 → 漂移触发 → IM 告警送达(飞书/钉钉/企微其一)。**两相全 PASS**:Phase 1 纯 API 驱动真 agent 验 sample→judge(deepseek-v4-pro)→persist→aux;Phase 2 因 drift 是时序信号、API 无法造陈年 baseline,经 `EXPERT_WORK_DB_DSN` 直 seed 陈年 baseline 高分 + 近窗低分 → drift worker 检出跌 60% → 断言 drift-alert。IM 真送达未验(未设 bot,emit 走全 webhook 共用 fan_out_event 属 HX-9 复用)。诚实边界:分层 seed 非 live 对话,被测 RT-5 代码(window→drift→alert→emit)真跑,仅陈年输入合成。**live 暴 2 坑**:compose env 透传 gap(#941 补 quality 组透传)+ judge 独立 provider(默认 anthropic 需配到有 key 的)。

### 13.5 验证

- PR-1:采样确定性(同 run_id 恒定选样)+ 日上限硬截断 + judge 结构化分落 `quality_score` + 坏 JSON 重试回正 + aux 成本按 `usage_kind` 可见;真 PG round-trip + RLS 跨租户隔离。
- PR-2:注入劣化分序列 → 漂移触发 `quality.drift` + cooldown 窗内不重复告警 + 无/少样本不误报 + payload_format 三渠道渲染。
- PR-3:tsc-b/vitest 全绿 + 趋势/下钻/漂移列表渲染 + form aria-label(axe);nav i18n 改动跑全量 vitest。
- **PR-4 ★5(CI 绿不算完)**:真流量端到端采样判分 + 人为注入劣化 → 漂移告警送达 IM,挂 `tools/eval/` 或 live harness。

### 13.6 范围外(backlog)

- **逐动作评分**(OpenHands critic 风格,动作成功概率)——先 run 级,逐动作待信号;
- **push 式采样**(run 完成即入队)——pull worker + 水位够,push 待规模;
- **漂移自动处置**(即降级/切模型)——告警=人研判,自动处置耦合 RT-7,不并入;
- **跨 agent 质量基准/租户排名**——单 agent 时序够,横比待;
- **judge 模型泛化提取**(consolidator/quality 共用中性 aux caller)——PR-1 走 surgical 独立 caller,DRY 重构留 backlog;
- **ground-truth 校准**(judge 分 vs 人工标注对齐)——先纯 LLM-judge,校准待。

## 14. RT-5 PR-3b 质量配置 UI 化(2026-07-06,live run 后拆出)

### 14.1 起因

RT-5 PR-1 把质量监控配置(采样率/judge 模型/漂移阈值/日上限等)全走 `Settings` env(`EXPERT_WORK_QUALITY_*`),配置表在 PR-1 延后。PR-3 看板交付时用户拍板**配置 UI 单开本 PR-3b**(前端 PR-3c)。★5 live run 又暴露:①`ENABLE_QUALITY_MONITOR` 是**启动期 env 门控**(改了要重启);②env 需同步补 compose 透传(#941)。用户追问「UI 可配后 ENABLE 能否删」→ 引出 worker 生命周期设计。

### 14.2 决策(用户 2026-07-06 拍板)

- **enable 模型 = worker 常起 + config `enabled` 标志**(非启动期 env 门控):worker 无条件常起(去 app.py env 门控),每周期 `run_once` 开头读 config,`enabled=false` 则廉价 no-op。**有效开关 = `settings.enable_quality_monitor` AND `config.enabled`**——env `ENABLE_QUALITY_MONITOR` 降级为 **deploy 级硬 off 兜底**(默认翻 **true**=子系统可用;显式 false 一票否决),`config.enabled`(DB,UI 控)默认 **false**(耗 judge token 故 opt-in)。→ 新部署 env true∧config false=关;UI 翻 config 即开、**不碰 .env**;deploy 硬关 env=false。
- **v1 只平台级配置**(租户覆盖/opt-out 留 backlog):质量监控是平台运营的(judge 平台付费+平台凭据),单行平台配置够。
- **worker 读 live config**:两 worker 从「构造期读 env 参数」改「注入 `load_config` 每周期读 `EffectiveQualityConfig`」;周期节奏即天然刷新(无需额外 TTL 缓存);`_loop` sleep interval 每轮读 config;`QualityJudge` provider/model 改 **per-call**(worker 每周期从 config 取传入 `score()`)。
- **effective-config 解析**:`resolve_effective_quality_config(settings, row)`——`enabled = settings.enable_quality_monitor AND (row.enabled if row else False)`(**无 row=关**,强制 UI opt-in);其余参数 `row` 值优先、无 row 回落 `settings`(env 仍作参数种子默认,平滑)。

### 14.3 迁移代价(显式)

现有 env `ENABLE_QUALITY_MONITOR=true` 的栈,PR-3b 后**无 config row → `enabled` 默认 false → 质量监控转空转**,须在平台配置 UI 翻一下 `enabled` 恢复(文档标注)。这是 opt-in-via-UI 的 GA 过渡,一次性、可接受。worker 常起但空转成本≈0(单行 config 读 + sleep)。

### 14.4 PR 切分

- **PR-3b 后端**:`platform_quality_config` 单行 singleton 表(tenant-less bypass RLS,照 `platform_judge_config`)+ 三件套 + migration 0119 + `EffectiveQualityConfig` 解析器 + loader + `GET/PUT /v1/platform-quality-config`(system_admin+审计)+ **worker 重构**(常起 + per-cycle config + judge per-call)+ app.py 去门控接线 + env `enable_quality_monitor` 默认翻 true + worker 单测改造 + 真 PG。
- **PR-3c 前端**:`PlatformQualitySection.tsx`(挂 `SettingsPlatformConfig`,照 `PlatformJudgeSection`)+ `api/platform_quality_config.ts` + i18n 双语 + Storybook + tsc-b/vitest。

### 14.5 范围外(backlog)

- **租户级覆盖/opt-out**(per-tenant config 层 + RLS + 租户 UI + worker 读时合并)——平台级够,租户覆盖待信号;
- **config 变更审计 diff**(记改了哪个 knob 旧→新)——先记 updated_by,细粒度 diff 待;
- **worker 热更 interval 的即时性**——本设计下改 interval 下周期生效(当前周期已 sleep),足够。
