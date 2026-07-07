# Expert Work 满分化迭代计划 v2（2026-06-15 重排）

> 取代 `2026-06-13-agent-harness-5star-iteration-plan.md`。依据 `2026-06-15-capability-reassessment.md`
> （重核后均分 4.51→4.59，395/430）重排。**排序口径不变**：ROI = 商业价值(BV) + agent 能力(CAP)，
> 工作量仅排期参考。

> ## 📊 当前状态（2026-06-17 更新）
>
> **421/430，均分 4.90（97.9%），共 +25★。本地可交付项已全部清空。**
>
> 收割链（按时间）：重核 396 → T0/T1/T2/T4 廉价收割 + agent 能力 → 8.5/13.2/14.4 ★5 →
> **HA 双大件 9.4+9.5 live 收口（T3 全清）** → 运维韧性三连（16.3/10.5/15.3）→
> **12.4 平台用量视图 ★3→★4（#690/#691）**。
>
> **剩余全部为 parked（本地开发环境测不了，需真 k8s / 外部依赖）**：
> - **7.2 gVisor 分级隔离**（★3）— 生产强制 gVisor + `isolation_level`，运维层，需真容器运行时
> - **7.6 IDS**（★3）— Falco/Tetragon runtime IDS，纯运维
> - **16.4 基础设施自愈**（★3）— k8s HPA/failover，基础设施层
> - **12.4 剩 ★5**（发票/对账单 + 支付 Stripe）— M2/外部，用户拍板排除
> - **10.1 连接式 trace**（★4）— 终态，推 ★5 = 安全反目标（外部 egress 不注 traceparent），不做
>
> → 下一步需用户起 **真 k8s / 运维环境**，或拍板新方向。纯本地满分化已到顶。

## 为什么重排

旧 v1 计划（06-13）之后 ship 了 Stream PI / HX-10 / 可观测 A.8 / eval 平台 S2，重核显示旧表多项已自愈：

- **eval 平台已落地**：11.3/11.6 → ★5（S2 全交付），不再是 P1。
- **沙箱安全自愈**：旧表头号短板域7（3.63）→ 4.25。7.3 输入校验 ★2→★4、7.4 输出过滤 ★3→★4
  （Stream PI）、7.2 分级隔离 ★2→★3（HX-10）、7.6 攻击监控 ★2→★3（PI 指标）。**旧 P3/P4 的安全项
  大半已升档**，不再是优先攻坚区。
- **可观测**：10.1 连接式 trace ★2→★4（A.8）。
- **新增非满分**：2.2 schema 校验 ★5→★4（重核发现无 runtime 校验）。

→ 优先级重心从「补沙箱安全」转向「**收割廉价 ★4→★5 + agent 能力项**」。

## 当前非满分项（5 项，全部 parked — 需真 k8s/外部，本地测不了）

★4（终态/部分）：10.1 连接式 trace（终态，推 ★5=安全反目标，不做）· 12.4 chargeback（★3→★4 #690/#691，剩发票/支付 M2/外部）
★3（M1 parked）：7.2 gVisor 分级隔离（运维）· 7.6 IDS（运维）· 16.4 基础设施自愈（k8s）
✅ 已 ★5 收割（本轮全清）：2.2 #647 / 4.1 确认 / 11.4·11.5 #649·#652 / 3.3 / 4.4 #655-658 / 1.3 #666·#667 /
7.3·7.4 #669 / 8.5 #671 / 13.2 #673 / 14.4 #675 / 16.3·10.5·15.3 #679-681 / **9.4 #677·#678** /
**9.5 #683-#687（含 worker 多副本 CAS 加固 + approval sweep）**

## 重排后的层级

### T0 — 廉价收割（★4→★5，≤1 天/项，最高 ROI）— **建议立即做**

| 项 | 现 | 内容 | BV·CAP | 工作量 |
|---|---|---|---|---|
| ~~2.2 schema 校验~~ | ✅★5 | **已交付（#647）**：tool_call args 派发前过 jsonschema Draft validator + invalid_arguments 归类 | M·M | — |
| ~~4.1 工作记忆~~ | ✅★5 | **确认（无需改码）**：CM-2 廉价滑窗默认激活（`WorkingMemoryPolicy.enabled=True`，token-gated）+ 测 + metrics；重核当时保守标★4，实为 ★5 | M·H | — |
| ~~11.4 trace-based eval~~ | ✅★5 | **B 路径已交付+E2E 实证**（#649/#652）：TraceEvalEngine 对真 agent+真模型跑，asserts 真 OTel span 树。本机真栈 **passed 3/3** | H·H | — |
| ~~11.5 对抗集~~ | ✅★5 | **B 路径已交付+E2E 实证**（#649/#652）：AdversarialEvalEngine 复用 safety_verdict 判真模型回复（输出防御链全开）。本机真栈 **5/6**（机制证实；1 真发现 injection-003 泄漏） | M·H | — |

> T0 全清：2.2 + 4.1 + 11.4 + 11.5 已 ★5。11.4/11.5 用户拍板走 **B（真 agent eval in worker，非廉价 A 确定性 runner）**，
> 见 `2026-06-15-1145-live-eval-worker-design.md` §8 E2E 实证；代码 #649/#652 已合，本机真栈（deepseek eval agent）
> trace passed 3/3 / adversarial 5/6（机制证实，1 真防御观测非假阴）。
>
> **累计进度（T0+T1+T2+T4 收割）**：重核 396 → 11.4/11.5（+2，398）→ 3.3（+2，400）→ 4.4（+1，401）→ 1.3（+1，402）→ 7.3/7.4（+2，404）→ 8.5（+2，406）→ 13.2（+2，408）→ 14.4（+2，410）→ 9.4（★2→★5 live，+3，413）→ 16.3（+2，415）→ 10.5（★3→★5，+2，417）→ 9.5（★2→★5 live，+3，420）→ **12.4（★3→★4，+1，421/430）**（+15.3 测试债清，★5 不改分）。
> **均分 4.60→4.90（97.9%），共 +25★。T1 全清；M1 大件 9.4+9.5 双双 live 收口；运维韧性三连（16.3/10.5/15.3，#679-681）。** 1.3 见 `2026-06-16-1.3-orchestration-patterns-design.md`。
> **9.5 worker 多副本完备（#686 TriggerScheduler cron+retry CAS / #687 approval timeout sweep）** 是已 ★5 范围内的加固，不改分——让所有 9.5 后台 worker（reaper/scheduler/queue/eval/approval）多副本 exactly-once，并补平 J.8 审批超时从没接线的缺失功能；设计见 `2026-06-16-9.5-distributed-run-queue-design.md` §2.2c/§2.2d。
> 16.3/10.5/15.3 见 `2026-06-15-capability-reassessment.md` §3「运维韧性三连」：16.3 应用层 backpressure 过载守卫（503 甩负）/ 10.5 SLO 多窗烧录率（Google SRE，promtool 双验）/ 15.3 sanitize 纯函数直接单测（含跨递归位置）。
> 9.4 见 `2026-06-16-9.4-9.5-ha-failover-design.md` §7（#677+#678）：Postgres 租约+孤儿 sweep+自动热接力,蓝绿双实例真栈 kill blue mid-run→green 续跑到 success(5 项断言全绿);live 暴露 failover 硬依赖 postgres checkpointer + 续跑 seq 撞键(已修)。
> 14.4 见 `2026-06-16-14.4-mcp-defense-audit-design.md`（#675）：核代码发现「无流量审计」部分过期，补 MCP 专属流量审计（server/response_chars/is_error）+ in-process 隔离威胁评估 + 前端流量徽章。
> 7.3/7.4 见 `2026-06-16-7.3-7.4-input-dlp-harvest-design.md`（#669）：7.3 PI-1c 结构化 `untrusted_content` 通道治内联注入根，**live 实证**通道把 001/003 内联注入从 LEAK 翻 SAFE（负对照排除假阳性）；7.4 出站 DLP 条件输出 redact PII。
> 8.5 见 `2026-06-16-8.5-rbac-abac-design.md`（#671）：细粒度 RBAC-ABAC（RoleBinding conditions=resource_ids/labels/owner_only，agents 路由实例级授权，集成实证条件 operator allow/deny）。
> 13.2 见 `2026-06-16-13.2-resume-idempotency-design.md`（#673）：并发 resume 幂等——核代码发现 CAS 已 exactly-once（不盲从「补悲观锁」），补 idempotency_key 确定性恢复 + 真 PG 16 并发证 + 全流程 seam 测。

### T1 — Agent 能力护城河（★4/★3→★5，高 CAP）

| 项 | 现 | 内容 | BV·CAP |
|---|---|---|---|
| ~~1.3 五大编排模式~~ | ✅★5 | **已交付+live 实证**（#666/#667）：gap-① 独立评判者是 staleness（J.11 routing 早支持 reflection 路由独立模型，#666 只补 manifest 编辑器友好控件）；gap-② Orchestrator-Worker「仅基础」→ **动态 worker spawn**（`spawn_worker` 工具，父现场建临时 worker 跑完即弃，复用 SubAgentTool child-run 核 + 沙箱/配额/深度隔离，#667）。**live E2E**：父真 spawn 3 worker→`spawned_total=3`→父综合。**明确不做** 630 行独立 coordinator（父本就是 orchestrator）。设计见 `2026-06-16-1.3-orchestration-patterns-design.md` | M·H |
| ~~4.4 agent 自写 skill~~ | ✅★5 | **B 路径已交付+E2E 实证**（#655-658）：飞轮 end-to-end 真模型跑通（轨迹→curation→distill 真调 deepseek→replay→判决）。**E2E 逮 5 坑**（整条 L7 轨迹录制没接 live run / candidate 无限重蒸 / 启动闸用错凭证源等，#656-658）—— 远非「代码完整只需 ungate」；评估过度乐观。**happy-path 已证**：真模型 E2E agent 自演化出有收益 skill（delta=0.667 → grounded → **AUTO_PROMOTE → ACTIVE**）。见 `2026-06-15-44-skill-evolution-enable-design.md` | H·H |
| ~~3.3 context-pressure 反馈~~ | ✅★5 | **已交付**：`ContextPressureMiddleware` 量 prompt vs 解析 context_window，usage≥0.75 向末条消息注模型可见预算提示（保前缀缓存），agent 据此收敛。默认 ON 阈值门控。确定性，单测+装配测全证（无需真模型 E2E）。设计见 `2026-06-15-33-context-pressure-feedback-design.md` | M·M |

### T2 — 企业信任 + 安全纵深收尾（多已部分升档，补到 ★5）

| 项 | 现 | 内容 |
|---|---|---|
| ~~7.3 输入校验~~ | ✅★5 | **已交付+live 实证（#669）**：PI-1c 结构化 `untrusted_content` 通道——业务系统结构化传待处理数据，Expert Work 用 build nonce spotlight 包裹治内联注入根。live：通道把 001/003 内联注入从 LEAK 翻 SAFE，负对照排除假阳性 |
| ~~7.4 DLP 输出~~ | ✅★5 | **已交付（#669）**：出站 DLP 条件输出——`common/dlp.py:scan_and_redact` 分类+脱敏 model 响应 PII（email/手机/身份证/卡号），`DefenseSpec.output_dlp` 开关 opt-in，确定性单测+装配测+指标 |
| 7.2 gVisor | ★3 | 生产强制 + `isolation_level` 真实现（运维 + 代码） |
| ~~8.5 RBAC-ABAC~~ | ✅★5 | **已交付+集成实证（#671）**：RoleBinding `conditions`（resource_ids URI 级 / labels 属性 / owner_only 归属）+ `authorize_resource` 加性语义 + agents 路由实例级采用 + IAM 页条件编辑器；关键修正=有条件 binding 不并入 `principal.roles` 防绕过；none_as_null JSONB 修 platform CHECK |
| 7.6 IDS | ★3 | Falco/Tetragon runtime IDS（运维） |
| ~~14.4 MCP 审计~~ | ✅★5 | **已交付（#675）**：核代码发现「无流量审计」部分过期（TE-2 已审计每工具含 MCP）；补 MCP 专属流量审计（`tool:call` 补 `mcp_server`/`response_chars` exfil 体积/`mcp_is_error`）+ in-process 隔离威胁评估（爆炸半径由信任边界 operator 非租户界定，M0 终态）+ 前端审计页 MCP 流量徽章（可扫体积）|

### TX — 接受为终态（★4 但非工作项）

| 项 | 现 | 为何不推 ★5 |
|---|---|---|
| **10.1 连接式 trace** | ★4 | 内部跨服务 trace（orchestrator→sandbox）已全链 + root/业务 span 齐；剩余「★5」gap 仅"外部 egress 不注 traceparent"——这是**故意的安全决议**（防 trace_id 泄露给外部），推 ★5 = 安全反目标。**接受 ★4 为终态**，不列工作项。 |

### T3 — 长程可靠 HA（✅ 全清 ★5，per-user 持久 agent 核心）

| 项 | 现 | 内容 | 工作量 |
|---|---|---|---|
| ~~9.4 自动 failover~~ | ✅★5 | **已交付+live 实证（#677+#678）**：Postgres 租约（`claimed_by`/`lease_until`/`heartbeat_at`）+ 孤儿 sweep（reclaim CAS 恰一赢家）+ 自动热接力（`adopt`+`run_agent(graph_input=None)` 从 durable checkpoint 续跑）。蓝绿双实例真栈 kill blue mid-run→green 续跑到 `status=success`，5 项断言全绿。house 风格（Postgres-first，零 Celery/broker）。live 暴露真依赖：failover 硬要 `checkpointer_backend=postgres`（dev 默认 memory→`EmptyInputError`）+ 续跑 seq 撞键（已修 `RunEventStore.next_seq`） |
| ~~9.5 分布式任务队列~~ | ✅★5 | **全收官+live 实证（#683–#687）**：house 风格分布式 run 队列（零 Celery/broker），`RunStatus.QUEUED`+`enqueued_input` 持久化 + `POST /runs mode=queue`（202 入队）+ 每实例 `RunQueueWorker` CAS-claim（`claim_queued` UPDATE…WHERE status='queued' RETURNING → 恰一赢家），与同步 SSE `mode=stream` 并行共存。**五段全交付**：① Phase 1 队列（#683）② 2a EvalWorker claim CAS（#683）③ **2b reaper/ledger exactly-once（#684）**——核代码发现真 gap 比 assessment 严重：`reserve`/`commit`/`release` 本身无行锁 read-check-write（READ COMMITTED ledger 双退款/丢失更新），reaper 每实例跑当前即真 bug，修 `FOR UPDATE` 串行化+`ON CONFLICT greatest` 原子算术+`expire_reserved` 返回赢家布尔（真 PG 16 并发证）④ **2c TriggerScheduler cron+retry CAS（#686）**——`claim_cron_fire`（CAS `last_fired_at IS NOT DISTINCT FROM`）+`claim_retry`（CAS retrying→fired），消多副本双触发⑤ **2d approval timeout sweep（#687）**——核出 `list_expired` 全仓无消费者=「24h 超时」从没接线（run 永不自动解，缺失功能非加固），抽 `resolve_approval_decision` request-free 核 + `ApprovalTimeoutSweep` worker（每实例，`mark_decided` CAS 保 exactly-once）。**live（#685）**：blue `mode=queue` 入队→kill blue→green RunQueueWorker 认领执行到 `success`，owner=green/`reclaim_count=0`/dequeued 0→1，4 项全绿。所有 9.5 后台 worker 多副本安全（reaper/scheduler/queue/eval/approval），单副本闸仅余 SkillCurator | XL |

### T4 — 测试债 + 运维韧性 + 变现（价值最低 / 用户后置，最后）

| 项 | 现 | 内容 |
|---|---|---|
| ~~13.2 并发 resume 幂等~~ | ✅★5 | **已交付+真 PG 集成实证（#673）**：核代码发现 `mark_decided` 原子条件 UPDATE 已 exactly-once（「补悲观锁」实为次优，CAS 更优）；补 `idempotency_key` 确定性恢复（随 CAS 原子存 `continuation_run_id`，重试同 key 幂等返同续跑非 409）+ 真 PG 16 并发证 DB 行锁 + 全流程 seam 测（真赢家存→replay→spawn-once）|
| ~~15.3 跨递归 cancel 测试~~ | ✅★5 | **测试债清（#681）**：`sanitize_dangling_tool_calls` 此前无直接单测,补纯函数边缘 9 例（含跨递归多 AIMessage 位置——cancel 深陷 subagent 递归→多处悬挂全修）+ `GraphRunner.sanitize_thread` fake-graph 3 例。subagent cancel 传播已由 `test_subagent` 覆盖（「跨递归 cancel 薄」部分过期）。★5 维持不改分 |
| 16.4 基础设施自愈 | ★3 | k8s HPA/failover（基础设施层） |
| ~~16.3 应用层 backpressure~~ | ✅★5 | **已交付+单测+全 app 集成（#679）**：`BackpressureMiddleware` 过载守卫——in-flight 深度（复用 `Lifecycle.in_flight`）超阈值 503+Retry-After fast-fail,置 Observability 内/Auth 外（shed 被 trace 但不进 JWT/DB）,免 health/metrics,默认开 cap 512。区别于 per-tenant rate-limit（429 公平）——这是全局过载（503 服务端）。补足「无应用层 backpressure/fast-fail 代码」gap |
| ~~10.5 SLO burn-rate~~ | ✅★5 | **已交付+promtool 双验（#680）**：`burn_rate.yml` Google SRE 多窗多烧录率——record `expert_work_slo_burn_rate{slo,window}`（可用性 SLO,5m/30m/1h/2h/6h/24h）+ 长窗确认/短窗加速告警（1h&5m>14.4 P0 / 6h&30m>6 P1 / 24h&2h>3 P2,`and ignoring(window)`）;`promtool check rules`+`test rules` 双 SUCCESS。关掉 sli.yml「Burn-rate rules are M1」deferral |
| 12.4 chargeback 计费 | ★3→★4 | **平台用量视图已交付（#690/#691）**：核代码发现计费链路（计量 G.9 → 定价 Y3 → rollup Y4 → ledger → 暴露 Z）已建，assessment「定价引擎/发票」中定价引擎早做完。用户拍板范围=平台 system_admin 看**每租户 + 每 agent** 的 token + 成本 + rollup enable 配置入口：Part A 扩 chargeback per-agent 钻取（ledger 已含 agent 分桶，零新计算）；Part B `platform_billing_config` 单行表 + admin-ui 开关 + 离线 rollup job 跑前读（cron 调度归 k8s）。**剩 ★5 gap=发票/月结对账单 + 支付（Stripe）**——用户明确排除（M2/外部），非静默缺失。设计见 `2026-06-17-12.4-platform-usage-view-design.md` |

## 与 v1 的关键差异

1. **旧 P1 eval 平台已交付**（11.3/11.6 ★5）→ 移出优先攻坚；剩 11.4/11.5 仅"接线"降为 T0 廉价收割。
2. **旧 P3/P4 沙箱安全已自愈**（7.2/3/4/6 全升档）→ 从「攻坚区」降为「补到满分的收尾」。
3. **新增 T0 廉价收割层**：4 项 ★4→★5 全 ≤1 天，最高 ROI，旧 v1 没单列。
4. **HA：9.4+9.5 双双 ★5 live 收口**（9.4 failover #677+#678；9.5 分布式队列 #683–#687，含全 worker 多副本 CAS 加固 + approval 超时 sweep 补缺）——均 house 风格复用现成 checkpoint+CAS，非传统 XL 大件。**T3 HA 层全清**。
5. **变现 12.4 ★3→★4**：平台用量视图（per-agent token+成本 + rollup enable）已交付（#690/#691）；剩发票/支付 = M2/外部，用户拍板排除。

## 建议执行序（已执行完毕）

~~T0 立即清 → T1 agent 能力 → T2 安全收尾 / T3 HA → T4 收尾~~ —— **全部 ★5 可达项已清空**。

**实际收口**：T0-T4 + HA 双大件 + 运维韧性三连 + 12.4 平台用量视图全交付，421/430（均分 4.90）。
剩 5 项均 parked（7.2/7.6/16.4 需真 k8s/运维；12.4 发票/支付 = M2/外部；10.1 终态不动）——
**下一步需用户起真 k8s/运维环境或拍板新方向**，纯本地满分化到顶。
