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

**观测**:`helix_llm_structured_finalize_total{outcome=conform|cache_hit|resend}`
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
- **RT-ADR-9 hide_from_ui 标记机制(Option A,deer-flow 验证)**:`additional_kwargs["helix_hide_from_ui"]` 标记 orchestrator 脚手架(CM-1 `<recovery-advisory>`)。**过滤只在 UI 服务边界,记录层永远忠实**——`read_turns(include_hidden=True)` 为安全默认(mirror sweep + 跨租户审计下钻拿完整记录,新持久/审计调用方忘传也不会静默丢审计),仅 UI 气泡视图(同租户自读)传 `include_hidden=False` 过滤。**绝不在 mirror sweep / 搜索层过滤**(那是审计+全文搜索源,#880-892)。参考:deer-flow `thread_runs.py:209-220` 在 gateway router UI 读路径判 `hide_from_ui`,checkpoint 原始读全部忠实;`journal.py:205` 仅语义用途(别把注入 advisory 误当用户首条消息记标题),持久/搜索层零过滤。顺修 CM-1 recovery-advisory 用户气泡泄漏(现存 bug)
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

| | deer-flow | Hermes | helix 现状 |
|---|---|---|---|
| 技能进系统提示 | `<skill><name><description><location></skill>` | compact index:name+description | **全 SKILL.md body 内联** |
| body 何时入 context | 模型按 `location` 路径 `read_file` 按需 | `skill_view` 按需 | 恒在(每轮) |
| 预算紧张 | — | **降级 names-only**(丢 description) | 无 |
| eager 全文 | 仅 `/slash` 显式激活当轮 | 无 | **默认** |
| 每技能固定成本 | ~40 tok | ~40 tok(或更低) | **~1.9k tok** |

出处:deer-flow `backend/packages/harness/deerflow/agents/lead_agent/prompt.py`(`get_skills_prompt_section` 渲染 name+description+location tag;系统提示教模型"call `read_file` on the skill's main file using the path attribute";仅 `/slash` 显式激活当轮注入全文);Hermes `agent/prompt_builder.py`(compact skill index,body 走 `skill_view`;`coding_context.py` 预算紧张时"demotes whole categories to a names-only line","only the descriptions are dropped")。

**两参考框架都是纯渐进式披露(= Anthropic skill 模型),从不默认内联 body**。helix `lazy_load=False` 默认与业界共识相反。helix 已建好 lazy 路(RT-ADR-7 skill_view 引用式 rescue,已 live 证),只差翻默认。

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
- **Anthropic cache_control 断点编排已有**:`providers/anthropic.py` `_apply_cache_control`(426-481)——system(1)+ 尾 2 非 system(`_CACHE_CONTROL_TAIL_COUNT=2`)+ ≤1 memory anchor(`per_session` 记忆块 index 1,`helix_cache_anchor`),预算≤4(Anthropic 4 断点上限)。`ModelSpec.cache_enabled` 门控。
- **cache 折扣定价列已有**:`models/model_rate_card.py:40-45` `cache_creation_per_mtok_micros`/`cache_read_per_mtok_micros`(server_default 0);rate card 页可编辑;rollup 已按之算成本。

**真实 gap = 前端透出**(捕获+定价+进 SDK,但表格不渲染):
- `SettingsUsage.tsx`:cost/token/kind 三表只 input/output/billed(93-203);cache 仅 2 个 headline tile(317-322),无 per-row/per-model/per-agent。
- `SettingsBillingChargeback.tsx`:**零 cache**(139-258)。
- 无 cache 命中率(`cache_read/(input+cache_read)`)展示,尽管四段数据齐。

**非 gap**:OpenAI/openai_compatible 无 cache_control 是**正确的**——OpenAI 是隐式自动缓存,只需读 `cached_tokens` 回来(已做),无可编排;不是缺陷。

### 10.2 决策

RT-3 收敛为**前端透出为主**(计划的 PR-1 后端编排大半已存在):
- **keep-warm 不做**(RT-ADR-13,用户拍板 2026-07-04):多租户 server 下 heartbeat 保温 = 为空闲租户付费维持缓存,成本模型不划算;Anthropic 隐式 5m TTL 已够常见长对话;openclaw 保温是单用户场景。
- **命中率前端算**(RT-ADR-14):四段已在 SDK,`cache_read/(input+cache_read)` 前端计算,不加后端 metric。
- **tool-def 断点推迟**(RT-ADR-15,backlog):deer-flow 断点花在 last tool def,helix 花在 memory anchor;两者都在 Anthropic ≤4 预算内,换 tool-def 会挤掉 memory anchor(per_session 记忆稳定性),收益边际,不在本轮。
- **cache 定价默认 0** 是运营数据(operator 设 rate card),非代码项。

### 10.3 Mini-ADR

- **RT-ADR-13 keep-warm 不做**:多租户成本模型不支持主动保温;维持 Anthropic 隐式 ephemeral(默认 5m),无 1h 长 TTL、无 heartbeat 刷新环。需求(超长空闲对话+缓存命中价值 >> 保温成本)出现再议。
- **RT-ADR-14 命中率纯前端派生**:`cache_read/(input+cache_read+cache_creation)` 由 SDK 四段前端算;不新增后端指标/API 字段(避免与 metrics.py validator 漂移)。
- **RT-ADR-15 tool-def 断点推迟**:Anthropic 4 断点预算已占满(system+2 tail+≤1 anchor);tool-def 断点需挤掉 memory anchor,得不偿失,backlog。

### 10.4 PR 切分

- **PR-0 设计**(本节 + ITERATION-PLAN)
- **PR-1 前端透出**(唯一实作 PR):`SettingsUsage.tsx` cost/token/kind 三表补 cache_read/cache_creation 列 + 命中率 + 节省金额估算(cache_read 相对按 input 价的差额);`SettingsBillingChargeback.tsx` 补 cache 列(平台侧同步);i18n 双语;前端审(tsc -b/axe/vitest,改 nav 无——纯表格列);envelope-vs-raw 对账(usage API 已返四段,SDK 已有字段,零后端改动)
- (无后端 PR——编排+计量+定价已存在;若 PR-1 发现 API 缺某派生字段再补,但预期零后端)

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

- **RT-ADR-16 agent 级 disable = 独立(tenant_id, agent_name)记录 + 缓存服务**:不塞进 `AgentSpecStatus`(disable 是可逆的紧急操作,与 lifecycle deprecated/deleted 正交,且 agent 按 (name,version) 存行——disable 要盖 name 下所有版本,需独立 scope)。新表 `agent_disable`(tenant_id, agent_name, disabled, reason, disabled_by, disabled_at)+ `AgentDisableService.is_disabled`(TTL 缓存,镜像 `TenantStatusService`)。gate:`_resolve_session`(拒新会话/run)+ `RunQueueWorker._claim_and_start`(拒 claim)+ 准入(`api/runs.py` 拒新 run)。
- **RT-ADR-17 bulk-cancel = 新 `list_running_runs(scope)` + 循环 `RunManager.cancel`**:disable agent → 枚举该 (tenant,agent) 的 RUNNING run → 逐个 `RunManager.cancel`(复用 abort_event 取消链,数秒内终止);tenant suspend 同样补此(枚举 tenant 全 RUNNING)。RunManager.cancel 已幂等(status CAS);每个 cancel 落审计。**跨副本**:running run 可能被别的 orchestrator 副本持有——cancel 经 `abort_event`(DB 持久 + 副本轮询 `record.abort_event.is_set()`,`sse.py:417+`),bulk 层只需 set flag,持有副本自会协作终止(与 9.4 HA failover 一致)。
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
