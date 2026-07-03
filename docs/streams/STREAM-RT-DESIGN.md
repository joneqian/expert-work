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

真缺清单(对照 7 条必锁):skill rescue 三预算、before_summarization hook(压缩前 memory flush)、ID-swap + `<system-reminder>` HumanMessage 注入(hide_from_ui)、memory 注入 2k 硬上限、memory 异步队列对齐、压缩可观测(COMPACTION 事件类型 + event_store 落账 + 前端渲染)。已有:触发阈值(L2 threshold_pct)、摘要+保留策略(L2 head/tail)。

- 架构决策点(PR-0 拍板):L2 是 graph 内 preflight(agent_node 入口),deer-flow 是 middleware——深化在 L2 原位做,还是迁 `before_llm_call` middleware 链与 13 个既有中间件统一?
- **PR-0 设计必须先按 deer-flow 新版复核 tracker**(上游 6 月后已修 ID-swap 递归注入 #3746、durable context #3887、SystemMessage 合并 #3711——直接抄坑)+ 顺带在对标报告 2.1 行加勘误脚注
- PR-1 skill rescue + before_summarization hook → PR-2 ID-swap + system-reminder + 2k 上限 + 异步队列对齐 → PR-3 COMPACTION 事件(EventType + event_store + API)→ PR-4 **前端:`run_detail/EventStreamPanel.tsx` EVENT_COLOR + 摘要卡片、`ToolTimeline.tsx`、hide_from_ui 过滤、i18n** → PR-5 live E2E(★5:长对话真跑,压缩触发→关键事实留存断言,挂 `tools/eval/`)
- 验证:关键事实留存率断言;prefix cache 命中率不劣化(ID-swap 目的);LoCoMo 段回归不掉分;M2-A"小时级 session 加固"未完成项与本项集成点在 PR-0 说明

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
