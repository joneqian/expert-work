# Agent Runtime 八域理论基线 + 五方对标矩阵(2026-07-03)

> 类型:能力对标报告(理论基线 + 源码/文档取证级)
> 范围:产品级 Agent Runtime 八大能力域 43 探针;helix vs OpenHands / deer-flow / OpenClaw / hermes-agent 五方对标
> 方法:①业界理论综述(AWS AgentCore / 12-Factor Agents / durable execution 收敛 / OWASP Agentic Top 10 2026 / OTel GenAI);②helix 三路并行源码扫描(file:line 级)+ 5 处误报核实纠正;③竞品四路取证——Hermes 复用存量 754 行 file:line 底稿、deer-flow 存量+本地源码+gh API、OpenClaw 官方 repo docs+CHANGELOG、OpenHands SDK docs+arXiv 论文
> 纪律:每个分数带证据;竞品"定位不做"记 N/A 不记 0,避免自嗨;未自测的质量数字不声称;存量底稿时效如实标注

---

## 0. 执行摘要

**helix 总分 109/129 ≈ 84%(43 探针,0-3 分制)。商业化平台面(域5 安全隔离/域7 可靠性/域8 治理商业化)全场碾压且 9 格独占;肉搏区(域1 执行内核/域3 工具/域6 可观测)与 OpenHands 互有胜负;4 格被明显压制:context compaction(1 vs 3)、browser(1 vs 3)、prompt cache 成本工程(2 vs 3)、结构化输出(0,全场最低并列)。**

对标修正后的缺口排序(替代此前仅基于理论框架的排序):

| # | 缺口 | 格差 | 参考实现 |
|---|------|------|---------|
| 1 | **context compaction 智能化** | 1 vs 3/3/2/2,最大单格差 | deer-flow 全套(6 月后又落 durable context #3887、ID-swap #3746 可直接抄坑);M2-C 提级 |
| 2 | **结构化输出强制** | 0,全场最低 | OpenClaw llm-task(JSON Schema 校验+模型白名单+timeout) |
| 3 | **prompt cache 成本工程** | 2 vs 3×3 | deer-flow cache_control 断点编排 + OpenClaw 保温三件套 |
| 4 | **生产质量监控** | 1 vs OpenHands critic 2 | OpenHands rubric-supervised critic(动作成功概率+自动返工) |
| 5 | browser 自动化 | 1 vs 3×2 | OpenClaw 托管 profile+CDP;OpenHands browser-use+rrweb 录制 |
| 6 | 审批工件绑定 | HITL 3 分内的 TOCTOU 弱点 | OpenClaw approval-time binding(canonical argv/env/文件绑定,漂移即拒) |
| 7 | 成本感知路由 | 全场皆 1,做了即领先 | 无成熟参考,自研 |
| 8 | kill switch / provider 覆盖 / replay fork | 小格差 | OpenHands `fork()`;litellm 化评估 |

---

## 1. 理论基线:产品级 Agent Runtime 八大能力域

事实源(2025–2026 业界收敛):

- AWS Bedrock AgentCore 平台组件分解(Runtime/Memory/Gateway/Identity/Observability/内置工具;per-session microVM、8h 长任务):[docs.aws.amazon.com/bedrock-agentcore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html)
- 12-Factor Agents(Dex Horthy/HumanLayer,100+ 生产实现归纳):[humanlayer.dev/12-factor-agents](https://www.humanlayer.dev/12-factor-agents)
- Durable execution 行业收敛(Temporal/Restate/DBOS ↔ LangGraph/OpenAI Agents SDK/AutoGen):[Zylos Research](https://zylos.ai/research/2026-04-24-durable-execution-agent-runtimes/)
- OWASP Top 10 for Agentic Applications (2026),ASI01-ASI10:[aikido.dev/blog/owasp-top-10-agentic-applications](https://www.aikido.dev/blog/owasp-top-10-agentic-applications)
- OpenTelemetry GenAI 语义约定(GenAI SIG):[opentelemetry.io/blog/2026/genai-observability](https://opentelemetry.io/blog/2026/genai-observability/)

八域框架:

1. **执行内核**:agent loop + 确定性编排;durable execution(checkpoint/resume/副作用防重放/幂等键);HITL 暂停恢复;streaming(token 级 + 断线续传);长任务/取消;队列背压
2. **状态与记忆**:stateless reducer 状态外置;短期 context 管理 + compaction;长期记忆(episodic/semantic + 混合检索 + 写入过滤);工作区持久化
3. **工具与集成**:tool schema 校验 + 错误自愈;MCP/A2A/Gateway;per-session 沙箱(隔离档位 + egress 管控);browser;skills
4. **模型网关**:multi-provider、failover、多 key、限流退避断路器、prompt caching、成本感知路由、结构化输出
5. **身份安全隔离**:双向身份(inbound IdP + outbound 凭证代管);租户三层硬隔离(数据/运行/网络);注入纵深防御(硬边界在 infra 层);DLP;kill switch
6. **可观测评估**:OTel GenAI tracing;token/成本计量;审计;离线 benchmark + 在线采样 + LLM-as-judge;replay 调试
7. **可靠性规模**:HA 租约 failover、孤儿回收、分布式队列 CAS claim、幂等、断路器、水平扩展
8. **治理商业化**:RBAC/ABAC、配额 entitlement、计费 chargeback、版本化灰度回滚、admin 运营面、合规(保留/驻留)

关键理论共识(反直觉点):

1. 最成功生产 agent ≠ 最"agentic"——是精心工程化的软件,LLM 只用在受控决策点(12-factor 实证结论)
2. 可靠性核心 = durable execution,2025–2026 全行业向此收敛
3. 安全前提 = 假设 prompt injection 必然成功;防线必须是 runtime 硬边界(sandbox/egress/权限),不是 prompt 技巧
4. 可观测已有事实标准(OTel GenAI),但"质量评估"是标准之外的缺口,产品必须自建

---

## 2. helix 八域评分(源码取证,main@274f492b)

### 域1 执行内核 ~92%
- ✅ LangGraph ReAct loop + supervisor/pipeline/动态 spawn(`services/orchestrator/src/orchestrator/sse.py:257-678`)
- ✅ AsyncPostgresSaver checkpoint + resume(`packages/helix-runtime/src/helix_agent/runtime/checkpointer/factory.py:87-130`、`orchestrator/resume.py`);approval idempotency_key + continuation_run_id 防重放(migration 0080)
- ✅ HITL:ask_for_approval → PAUSED → resume,24h timeout(`orchestrator/tools/approval.py`)
- ✅ SSE token 流 + Last-Event-ID 续传 + event_store 重放(`stream_bridge/base.py`)
- ✅ CancellationToken 协作取消全链穿透、心跳租约(`runtime/cancellation.py`)
- ✅ 分布式队列 QUEUED + CAS claim + 背压 drop-oldest(`control_plane/run_queue_worker.py:158`)
- 🟡 workflow 显式版本化/迁移路径缺;per-tool timeout 不均(仅 sandbox 有 timeout_s)

### 域2 状态与记忆 ~80%
- ✅ thread_meta 状态全外置;长期记忆 episodic/semantic、tsvector+pgvector RRF 混合检索、decay、写入过滤(importance/confidence+content_hash)、读时验证、MemoryConsolidator(`persistence/memory/sql.py`)
- ✅ per-user 工作区持久卷(`persistence/workspace/`)
- 🟡 **短期 compaction 幼稚**:仅 turn/token trim + pressure note,无摘要式压缩(M2-C 已规划)(`runtime/middleware/dynamic_context.py`、`context_pressure.py`)

### 域3 工具与集成 ~85%
- ✅ ToolSpec discriminated union + 输出预算策略(`protocol/agent_spec.py:872`);MCP 三层 pool(平台 bearer/租户/per-user OAuth)+ 目录 + opt-in(migration 0063)
- ✅ 沙箱:gVisor(prod)+ read-only rootfs + seccomp + cap-drop;per-exec 临时容器;per-user 持久卷;egress 代理 + HMAC token(`runtime/sandbox/runtime_provider.py`)
- ✅ skills 全生命周期:导入/marketplace/订阅/进化飞轮/晋升闸/kill switch
- ❌ browser 自动化缺失(仅 ask_image 视觉 + HTTP 工具);❌ 结构化输出强制缺失(全仓 grep 零命中)

### 域4 模型网关 ~85%
- ✅ 9 provider 统一抽象(`orchestrator/llm/router.py:86`);两级 failover(key 级多 key 轮换 migration 0084 → provider 级);限流(Redis token bucket)+ 退避 + per-key 断路器(`llm_error_handling.py:149`)
- ✅ prompt cache 锚点 + 运行级 LLM cache;VL 单独路由;embedder/rerank 平台凭证
- ❌ 成本感知路由缺失(仅静态 aux_model);❌ 结构化输出(同域3)

### 域5 身份安全隔离 ~93%
- ✅ Keycloak IdP + OIDC + JWT + jti 撤销;RBAC deny-by-default(34 资源×6 动作)+ ABAC 条件 binding(`auth/rbac.py:66-150`、`auth/abac.py`);system_admin 平台域
- ✅ 三层隔离:FORCE RLS(migration 0005)+ SET LOCAL app.tenant_id + gVisor 沙箱 + egress 代理(SSRF 私网拦截)+ 全量 egress 审计
- ✅ 注入防御:spotlight nonce 围栏 + datamark;output screen(secret patterns/exfil/canary);威胁模式分类扫描(`common/spotlight.py`、`output_screen.py`、`threat_patterns.py`)
- ✅ DLP redact(入站+出站,出站默认 off)+ 审计 24+ 资源类型 + DB 故障 fallback 队列 + 跨租户审计读(audit_reader BYPASSRLS)
- 🟡 kill switch 仅 skill 晋升级,无全局租户/agent 级紧急停止;单 KEK 无 rotation

### 域6 可观测与评估 ~78%
- ✅ OTel TracerProvider + OTLP、W3C 传播、13 组件命名规范(`common/observability/tracing.py`);metrics catalog + 高基数 label 黑名单;token_usage 四段计量 + per-agent/per-user 钻取
- ✅ eval 体系:`tools/eval/` 全套 harness(`_judge.py` LLM-as-judge、`adversarial.py`、`longmem/`+`baselines/` LoCoMo/LongMemEval 基准)+ live eval worker(`control_plane/eval_engine_live.py`)
- ❌ 生产流量在线质量监控缺失(live eval = 拉起套件跑,非真实流量采样评分/漂移检测)
- 🟡 replay 有 event_store 重放,无确定性重放/fork

### 域7 可靠性规模 ~90%
- ✅ HA:claimed_by/lease_until/heartbeat 租约(migration 0081)+ OrphanSweep CAS 重取 + reclaim_count 上限;队列 QUEUED 状态机 + enqueued_input 持久化(migration 0082)
- ✅ webhook per-endpoint 断路器(HX-9 #595-599)+ DLQ 重试;control-plane stateless 多副本
- 🟡 工具调用级无断路器/幂等;单点:PG(无多区域)、单 KEK、credential-proxy

### 域8 治理商业化 ~88%
- ✅ 计费全链:token_usage → rate card(micro-USD)→ tenant_billing_ledger 三层(base/markup/billed)→ per-agent chargeback 钻取(`api/billing_admin.py`)
- ✅ 配额 dimension×scope + check/reserve/commit/release(`api/tenant_quotas.py`);plan 门控(entitlement 复用 tenant_config.plan)
- ✅ 版本化:skill immutable 版本、平台模板 spec_sha256 钉版、租户 fork lineage;admin 三层下钻 + 全文搜索 + webhook 通知(飞书/钉钉/企微)
- ✅ retention per-tenant 配置(D.3,`protocol/tenant_config.py:139-145`)
- ❌ 数据驻留无;🟡 无流量灰度

### 探索误报纠正记录(5 处)

首轮 3 路并行扫描误报缺失、经代码/记忆核实实际存在:ABAC(`auth/abac.py`)、live eval(`eval_engine_live.py`)、LLM-as-judge(`tools/eval/_judge.py`)、webhook per-endpoint 断路器(HX-9)、retention(D.3)。教训:**eval 引擎在 `tools/eval` 不在 `services/`,能力扫描必须包含**。

---

## 3. 竞品取证摘要

### OpenHands(SDK v1.31.0,2026-07-02;主仓 79.2k stars)

组织已从 All-Hands-AI 迁至 github.com/OpenHands;V0 monolith 2026-04 退役;microagents 在 V1 更名 skills。定位 = 编码 agent 平台 + SDK。证据:[software-agent-sdk](https://github.com/OpenHands/software-agent-sdk)、[docs.openhands.dev](https://docs.openhands.dev)、[arXiv:2511.03690](https://arxiv.org/abs/2511.03690)(MLSys 2026)、[benchmarks 仓 + 公开 Index](https://index.openhands.dev)。

- **标杆项**:event-sourcing 执行内核(逐事件落盘 persist 中位 0.20ms、确定性回放、`fork()` 分叉调试、生产实证 V1 系统性故障 -61%);eval 即基建(公开 Index 14 模型×5 基准、critic 实时质量打分驱动自动返工);LiteLLM 100+ provider;SecretRegistry 凭证代管(延迟绑定/热轮换/输出掩码)
- **短板**:长期记忆几乎空白(无向量记忆,写入治理 0);无分布式队列/租约 HA(每对话绑死单 sandbox pod);沙箱止步 Docker、**egress 无管控**(多租户安全审计论文自认 future work);治理商业化锁闭源层且粒度粗

### deer-flow(bytedance,@b3c312b7 2026-06-28)

定位 = 单租户自部署 SuperAgent harness(LangGraph lead_agent + 25 middleware)。证据:本地存量对标 4 份 + 本地源码树 + gh API(至 07-03)。

- **标杆项**:context 压缩深水区(LLM 摘要 + 抢救 hook + 落盘换入 + 防循环,6 月后又落 durable context #3887 / ID-swap #3746 / SystemMessage 合并 #3711);provider 层显式 prompt cache 断点编排(`claude_provider.py:193`,含 OAuth 4-cache-block 处理);token 计量到 step 归因 + TokenBudgetMiddleware per-run 预算闸(#3412);SSE 续传 O(1) seq(#3700)
- **短板**:模型 failover 0(无 fallback 链);eval 0;run 绑 worker 单机天花板(跨 worker 操作 409);多租户 N/A(仅 user_id)
- **6-09 后演进要点**:v2.0.0 钉版;安全三连(#3662 输入消毒/#3661 SystemMessage 角色隔离/#3506 OIDC);E2B 沙箱第三档(#3883);subagent delegation ledger(#3877);goal continuations(#3858)

### OpenClaw(@main 2026-07-03,~145k stars)

定位 = 单操作者本地/自托管个人 assistant runtime(Gateway + 20+ 聊天渠道);安全文档明文自认"非敌对多租户边界"。证据:[openclaw/openclaw](https://github.com/openclaw/openclaw) docs/ + CHANGELOG + Releases。

- **标杆项**:exec 审批五档 + **审批时工件绑定**(canonical cwd/argv/env/可执行路径/脚本文件绑定,执行前漂移即拒,防 TOCTOU);MCP 双向(serve + client,OAuth/mTLS/故障 server pause);browser 自动化(托管 profile + CDP + 沙箱浏览器容器);model failover"选择来源契约"(用户显式选型=strict 报错,平台默认=可降级 + 会话内可见通知 + 探活自动回切);prompt cache 保温三件套(cacheRetention 档位 × cache-ttl 剪枝 × heartbeat keep-warm 对齐 TTL 节拍)
- **短板**:无 token 级流(仅 block streaming);单 gateway 无 HA(定位如此);沙箱默认关、自认非硬边界
- **6-09 后演进要点**:安全 fail-closed wave(v2026.6.6);skills 供应链护栏(ClawHub provenance #93283、Workshop 强制写路径);capability profiles per-conversation 工具边界(#98536);`/fast auto` 成本分层第一步(#85104)

### hermes-agent(@bb4703c76,存量底稿 ~5 周旧)

定位 = Python 单用户 agent harness(4306 行自研 ReAct loop)。证据:存量 `helix-vs-hermes-gap.md`(754 行 15 维 file:line)+ `hermes-deep-dive.md` + 4 份专项分析,本轮未重新取证。

- **标杆项**:skills 生命周期 L3-L4(6 action + 后台 fork review + curator 7 天自动状态机 + pinned 保护);error-as-guidance(5 类重试矩阵 + recovery 注入同轮自纠);30+ provider
- **短板**:无 checkpoint(SQLite 存历史非状态恢复);无 HITL 审批;无 eval/replay/tracing 纵深;单用户定位大量 N/A
- **时效警示**:若进入 skills/记忆方向实施,先复核上游演进

---

## 4. 五方对标矩阵(43 探针,0-3 分;N/A=定位不做)

评估基线:helix@main 274f492b | OpenHands SDK v1.31.0 | deer-flow@b3c312b7 | OpenClaw@main 2026-07-03 | hermes-agent@bb4703c76

| 探针 | helix | OpenHands | deer-flow | OpenClaw | Hermes |
|---|---|---|---|---|---|
| 1.1 checkpoint 粒度/后端 | 3 | 3 | 2 | 2 | N/A |
| 1.2 crash resume 防重放 | 2 | 2 | 1 | 2 | 2 |
| 1.3 HITL 审批 | 3 | 3 | 2 | **3** | 0 |
| 1.4 streaming+续传 | 3 | 2 | 3 | 1 | 2 |
| 1.5 编排广度 | 3 | 2 | 2 | 3 | 1 |
| 1.6 取消超时 | 3 | 2 | 2 | 2 | 2 |
| 1.7 分布式队列 | **3** | 1 | 1 | 3* | 2 |
| 2.1 context 压缩 | **1** | 3 | 3 | 2 | 2 |
| 2.2 长期记忆检索 | 3 | 1 | 2 | 3 | 1 |
| 2.3 写入治理 | 3 | 0 | 2 | 2 | 2 |
| 2.4 跨 session 连续 | 3 | 2 | 2 | 2 | 1 |
| 2.5 记忆实证 | **3** | 2 | 0 | 1 | 0 |
| 3.1 schema+自愈 | 3 | 3 | 2 | 2 | 2 |
| 3.2 MCP | 3 | 3 | 2 | **3**(双向) | 1 |
| 3.3 沙箱+egress | **3** | 2 | 2 | 2 | 2 |
| 3.4 skills | **3**(效用验证) | 3 | 2 | 2 | 3 |
| 3.5 browser/多模态 | **1** | 3 | 2 | 3 | 0 |
| 4.1 provider 覆盖 | **2** | 3 | 2 | 3 | 3 |
| 4.2 failover | 3 | 2 | 0 | 3 | 2 |
| 4.3 限流退避断路 | 3 | 2 | 2 | 2 | 2 |
| 4.4 prompt cache | **2** | 3 | 3 | 3 | 2 |
| 4.5 成本路由 | 1 | 1 | 1 | 1 | 1 |
| 4.6 结构化输出 | **0** | 1 | 1 | 2 | 0 |
| 5.1 身份 RBAC | **3** | 2 | 2 | N/A | N/A |
| 5.2 凭证代管 | 3 | 3 | 1 | 2 | 1 |
| 5.3 多租户 | **3**(独占) | 2 | N/A | N/A | N/A |
| 5.4 注入防御 | **3** | 2 | 2 | 2 | 2 |
| 5.5 DLP/审计 | **3** | 2 | 1 | 2 | 2 |
| 5.6 kill switch | 1 | 2 | 1 | 1 | 0 |
| 6.1 tracing | 3 | 3 | 2 | 2 | 1 |
| 6.2 token/成本计量 | 3 | 3 | 3 | 2 | 1 |
| 6.3 eval harness | 3 | **3**(公开 Index) | 0 | 2 | 0 |
| 6.4 生产质量监控 | 1 | **2**(critic) | 1 | 1 | 0 |
| 6.5 replay 调试 | 2 | **3**(确定性+fork) | 1 | 2 | 0 |
| 7.1 HA 租约 | **3**(独占) | 1 | 1 | N/A | N/A |
| 7.2 队列加固 | **3** | 1 | 1 | 2 | N/A |
| 7.3 幂等重试 | 2 | 2 | 1 | 2 | 1 |
| 7.4 外呼断路 | **3** | 1 | 2 | 1 | 2 |
| 8.1 治理面 | **3** | 2 | 1 | N/A | N/A |
| 8.2 计费 chargeback | **3**(独占) | 2 | N/A | N/A | N/A |
| 8.3 配额 | **3** | 2 | 1 | N/A | N/A |
| 8.4 版本化灰度 | 2 | 1 | 1 | 1 | 1 |
| 8.5 合规 | 2 | 1 | N/A | N/A | N/A |

\* OpenClaw 1.7 = 单机 lane-aware 队列一线(steer/followup/collect/interrupt 四模式 + 溢出压缩注入),非分布式。

### helix 独占格(护城河)

多租户三层隔离、计费 chargeback、配额矩阵、租约 HA + orphan sweep、分布式队列加固、egress 管控、审计纵深、skills 效用验证(SPARK 式后验蒸馏,四竞品集体缺)、记忆效果实证(dense recall@5=0.906,CM-N5)。

### 与 OpenHands 的肉搏区判读

它赢:replay/fork、公开 eval Index、provider 覆盖、browser、凭证代管细节(热轮换)。helix 赢:分布式队列/HA、egress、审计、failover 层级、长期记忆全线。双方 checkpoint/HITL/MCP/skills/tracing/计量持平于一线。

---

## 5. 可直接移植的 6 个竞品设计

1. **deer-flow subagent delegation ledger**(#3877)——系统台账防 lead agent 重复委派同一子任务;helix 动态 worker spawn 编排已知痛点
2. **deer-flow goal continuations**(#3858)——线程级目标状态 + `/goal` 管理 + worker 侧目标评估续跑;与 helix per-user 持久 agent 产品形态同构
3. **OpenClaw 审批工件绑定**——批准"确切工件"而非"意图"(canonical cwd + argv + env + 钉住可执行路径 + 脚本文件绑定,执行前漂移即拒;绑定不唯一时拒绝铸造审批);可移植到 exec_python/沙箱命令审批
4. **OpenClaw failover 来源契约**——用户显式选型 = strict 如实报错;平台默认 = 允许降级链 + 会话内可见通知 + 定期探活自动回切;Y-MK 两级 key 切换缺这层 UX 契约
5. **OpenHands event-sourcing `fork()`**——保留审计轨的对话分叉,A/B 调试
6. **OpenClaw prompt cache 保温三件套**——cacheRetention 档位 × idle 超 TTL 剪枝防重缓存超大历史 × heartbeat keep-warm 对齐缓存 TTL 节拍(55m/1h);对长驻 per-user agent 成本直降

---

## 6. 时效与复核备注

- Hermes 底稿基于 bb4703c76(~5 周旧);进入 skills/记忆方向实施前建议复核上游
- deer-flow/OpenClaw/OpenHands 均为 2026-07 时点新证(gh API/官方 docs/release notes)
- `docs/decisions/deer-flow-context-mgmt-alignment.md` tracker 锁的是 6-09 前快照;M2-C 启动前应按 deer-flow 新版源码复核(上游已踩过并修复 ID-swap 递归注入坑 #3746)
- 本报告纸面对标;矩阵中 helix「深度不足」格若进入实施,按 ★5 标准补 live E2E 实证
