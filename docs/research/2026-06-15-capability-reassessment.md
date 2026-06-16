# 能力评估重核（2026-06-15）

> 重核 `2026-06-13-agent-harness-capability-assessment.md`：7 个 Explore agent 并行、逐项基于
> **运行代码 + 测试**重评（禁引设计文档作 ≥3★ 依据、评 gap 前先全仓 grep），我对每个变动项 +
> 簇间分歧项亲自抽样复核。

## 结论先行

重核动了 **7 项**（净 +7★）：**6 项上调 + 1 项下调**。

**根因不同于上次**：上次（W0/S0 深核 4 项 4/4 全错）是「careless 读设计文档当 done」的**判断错误**；
这次 6 个上调里 **5 个是 staleness——旧表 06-13 定稿，之后 merge 的 Stream PI(#630-643)/
HX-10 沙箱(#593-594)/可观测 A.8 收尾(#604)直接补了当时的 gap**，旧表没过期重核而已。唯一**真判断错**
是下调的 2.2（声称 ★5 但无 runtime schema 校验）。

**重核可靠性自检**：本轮抽样复核**驳回了 2 处 agent 过度修正 + 1 处 agent 旧分误记**（见 §3），
印证「agent 也会犯错、必须人核」。

## 1. 变动项（已抽样复核）

| 项 | 旧→新 | 方向 | code-evidence | 复核 |
|---|---|---|---|---|
| **2.2 schema 校验** | ★5→★4 | **高估** | orchestrator 无 runtime jsonschema 校验（`grep jsonschema` 全空）；tool_call args 不经 schema 检查直进 `tool.call()`，仅工具自校验 | ✅ 亲核 grep 确认 |
| **4.1 工作记忆** | ★3→**★5** | 低估 | CM-2 廉价滑窗 `context/working_window.py:WorkingWindow` 已接线（`agent_factory.py:559` 建 / `683` 传 / `builder.py:458` agent_node 应用）+ **默认激活**（`WorkingMemoryPolicy.enabled=True`，token-gated，threshold 下 no-op）+ 测（`test_working_window.py`/`_wiring.py`）+ metrics；旧"缺廉价滑窗→全走重武器"gap 已根除 | ✅ 亲核：T0 复核时发现重核自身保守标★4，实为 ★5（默认开） |
| **7.2 分级隔离** | ★2→★3 | 低估 | HX-10：`sandbox_supervisor/settings.py:oci_runtime`(runc/runsc) + seccomp pin + `test_gvisor_*`；但 `isolation_level` Literal 仍零实现 | ✅ 我知 #593/594 |
| **7.3 输入校验** | ★2→★4 | 低估 | Stream PI-1：`common/spotlight.py:spotlight_untrusted` 包裹不可信输入通道 + 系统提示 clause（`test_spotlight.py`/`test_agent_factory_spotlight.py`）；旧标"无 injection 语义扫描"已补 | ✅ 亲建 |
| **7.4 输出过滤** | ★3→★4 | 低估 | PI-2 `common/output_screen.py:screen_output`（凭证形态+exfil）+ PI-2b `output_judge.py:LLMOutputJudge`；旧标"缺 DLP"部分补 | ✅ 亲建 |
| **7.6 攻击监控** | ★2→★3 | 低估 | PI 攻击拦截指标 `_output_screen_blocked_total`/`_output_judge_total`/`_action_screen_total`（builder.py）;仍无 IDS/Falco 故止 ★3 | ✅ 亲建 |
| **10.1 连接式 trace** | ★2→★4 | 低估 | A.8：`sse.py:338 helix_span(SESSION,"run")` root span + `builder.py:607/1350` LLM/tool 业务 span + `propagation.py` 跨服务传播（`test_sandbox_trace_propagation.py`）；旧 W0「run_agent 无 span」已过期；缺口仅"外部 egress 故意不注"(设计决议) | ✅ 亲核 sse.py |

## 2. 维持原分（驳回 agent 修正 / 澄清）

| 项 | agent 建议 | 我的裁定 | 理由 |
|---|---|---|---|
| **6.3 Handoff** | ★5→★4.5 | **维持 ★5** | agent 以"call 时无深度预检"下调,但 build 时 `MAX_SUBAGENT_DEPTH=3` 强制 + `test_subagent.py` 39 测；深度 4 在 build 即 throw,非能力缺失,是边缘测试薄 |
| **15.3 中断一致** | ★5→★4 | **维持 ★5** | agent 以"跨递归 cancel / resume token 消毒测试薄"下调,但 `cancellation.py` + `sanitize_dangling_tool_calls` 主路径 code+wired+tested(14 单测+集成);flagged 的是测试覆盖边缘,非缺能力 → 记为测试债跟进,不改分 |
| **8.4 护栏双向** | (agent 误记旧分 ★4) | **维持 ★5** | 旧表实为 ★5;PI 还强化了输入侧护栏(spotlight+action judge),输出侧 DLP 缺口同旧 → 不动 |

> 半星(★4.5)不符 rubric 整数档;以上两项 agent3 系统性偏激进(对充分测试的 ★5 以边缘 case 测试薄为由下调),
> 抽样复核驳回——正是计划要求的"不盲信 agent"。

## 3. 新统计

| | 旧 | 新 | T0+T1 | +T2 | +8.5 | +13.2 | +14.4 | +9.4 | +16.3 |
|---|---|---|---|---|---|---|---|---|---|
| 总分 | 388/430 | **396/430** | **402/430** | **404/430** | **406/430** | **408/430** | **410/430** | **413/430** | **415/430** |
| 百分 | 90.2% | **92.1%** | **93.5%** | **93.95%** | **94.4%** | **94.9%** | **95.3%** | **96.0%** | **96.5%** |
| 均分 | 4.51 | **4.60** | **4.67** | **4.70** | **4.72** | **4.74** | **4.77** | **4.80** | **4.83** |

> **T2 廉价收割（#669）**：7.3 输入校验 ★4→★5（PI-1c 结构化 `untrusted_content` 通道，治内联注入根，
> **live 实证**：通道把 001/003 两条内联注入从 LEAK 翻 SAFE，负对照排除假阳性）+ 7.4 输出过滤 ★4→★5
> （出站 DLP 条件输出 redact PII，确定性单测+装配测）。两项均模型无关。**域7 沙箱安全 4.25→4.50**
> （7.3/7.4 各 +1）。共 +2★。
>
> **8.5 权限粒度 ★3→★5（#671）**：细粒度 RBAC-ABAC——RoleBinding 加 `conditions`（resource_ids URI 级 /
> labels 属性 / owner_only 归属），`authorize_resource` 加性语义实例级授权，agents 路由端到端采用。
> 集成实证：条件 operator 读/写实例级 allow/deny + admin 不受影响。**域8 企业控制 4.60→5.00**（8.5 +2）。
>
> **13.2 多租户确定性恢复 ★3→★5（#673）**：核代码发现 `mark_decided` 原子条件 UPDATE 已 exactly-once
> （assessment 提的「补悲观锁」实为次优——CAS 优于悲观锁）。真 gap=race 未证 + 重试不幂等；补 `idempotency_key`
> 确定性恢复（随 CAS 原子存 `continuation_run_id`，重试同 key 幂等返同续跑 run）+ **真 PG 16 并发证 DB 行锁
> exactly-once** + 全流程 seam 测。**域13 多租户 4.60→5.00**（13.2 +2）。
>
> **14.4 MCP 纵深防御 ★3→★5（#675）**：核代码发现「无流量审计」部分过期（TE-2 已审计每工具含 MCP，PI 补注入/
> 越权/输出防御）。真 gap=MCP 专属流量维度 + 隔离评估；补 MCP 流量审计（`tool:call` 审计补 `mcp_server`/`response_chars`
> exfil 体积信号/`mcp_is_error`）+ in-process 隔离威胁评估（爆炸半径由信任边界 operator 非租户界定，M0 终态）+
> 前端审计页 MCP 流量徽章（可扫体积）。**域14 扩展性 4.60→5.00**（14.4 +2）。
>
> **9.4 跨进程恢复 ★2→★5（#677+#678）**：Postgres 租约（`claimed_by`/`lease_until`/`heartbeat_at`）+ 孤儿
> sweep（reclaim CAS 恰一赢家）+ **自动热接力**——peer 实例 `adopt` 重认领的 run，`run_agent(graph_input=None)`
> 从 durable LangGraph checkpoint 续跑（13.2 幂等兜底）。**live 实证（蓝绿双实例真栈 kill blue mid-run）**：
> green sweep 检测过期租约→reclaim→续跑到 `status=success`，owner 易主 / `reclaim_count=1` / `run:failover`
> 审计 / metric 0→1，5 项全绿。live 暴露真依赖：failover resume 硬要 `checkpointer_backend=postgres`（dev 默认
> memory 进程内 saver→`EmptyInputError`，确定性单测 monkeypatch run_agent 漏判）+ 续跑 event seq 撞键（已修
> `RunEventStore.next_seq`）。**域9 持久化 4.00→4.50**（9.4 +3）。

> （4.1 在 T0 复核时由保守的 ★4 订正为 ★5——默认激活的廉价滑窗，使净变动 +8★、总分 396。
> 末列 = 重核后 396 起，B 路径/E2E 实证陆续收：11.4 + 11.5（各 ★4→★5，+2，→398）
> + 3.3（★3→★5，+2，→400）+ 4.4（★4→★5，+1，→401）+ 1.3（★4→★5，+1，→402）。共 +6★，T1 全清。
> 见 `2026-06-15-1145-live-eval-worker-design.md` §8 / `2026-06-15-33-...` / `2026-06-15-44-...` /
> `2026-06-16-1.3-orchestration-patterns-design.md`。
> 4.4 happy-path（飞轮 auto-promote）真模型 E2E 实证 delta=0.667→grounded→ACTIVE。
> 1.3：gap-① 独立评判者 staleness（J.11 routing 早具备，#666 补 UX）+ gap-② 动态 worker spawn
> （#667，live 实证父 spawn 3 worker→指标 3→父综合）。）

**域级变动**：域2 5.00→4.86(2.2↓) · **域4 4.57→4.86**(4.1 ★3→★5) · **域7 3.63→4.25→4.50**(7.2/3/4/6 ↑5 PI+HX-10,
再 7.3/7.4 ★4→★5 T2 收割 +2) · **域8 4.60→5.00**(8.5 ★3→★5 ABAC,#671) · 域10 4.17→4.50(10.1↑) ·
**域13 4.60→5.00**(13.2 ★3→★5 幂等,#673) · **域14 4.60→5.00**(14.4 ★3→★5 MCP 审计,#675) ·
**域9 持久化 4.00→4.50**(9.4 ★2→★5 failover,live 实证,#677+#678) ·
**域16 部署运维可靠性 4.20→4.60**(16.3 ★3→★5 应用层 backpressure 过载守卫)。其余 7 域不变。

**最大修正**：域7 沙箱安全（旧表最低分域）由 3.63→4.25——Stream PI 注入防御 + HX-10 沙箱把旧表三个 ★2
（7.2/7.3/7.6）和一个 ★3（7.4）全升档。旧表"沙箱隔离强度"作为头号短板的判断**已被后续工作部分填平**。

## 4. 诚实交代

- **本次差异规模**：7/86 项动（8.1%），净 +7★，均分 +0.08。比上次（深核 4 项全错）**小得多**——
  因为这次主要是 staleness（重核了过期表），不是判断不可靠。
- **方法仍有残余风险**：2 处 agent 过度下调 + 1 处旧分误记被人核拦下；说明纯 agent 重核仍需人核兜底。
- **真高估仅 1 项**（2.2）：是「有 schema 声明 ≠ 有 runtime 校验」的细分,属边界能力,非严重虚标。
- **未变的 design-only 风险项**：9.5(分布式队列,M1)/12.4(计费业务层)/16.4(基础设施级自愈,k8s 层)——均诚实标
  ≤★3 且有明确 M1/业务层归属,非静默缺失。（9.4 failover 已 ★2→★5 live 收口 #677+#678;16.3 应用层 backpressure
  已 ★3→★5;3.3/13.2/14.4 亦已先后清。）

## 5. 对计划的影响

旧表头号短板「域7 沙箱 3.63」已部分自愈（→4.25），**P1 优先级应下调沙箱、上调真实剩余 gap**：
- 2.2 runtime schema 校验（低成本补,纯防御纵深）
- 11.4/11.5 接 EvalWorker suite（旧标 ★4,< 1h 工作量）
- 3.3 context-pressure 反馈 / 13.2 并发 resume race 测试 / 15.3 跨递归 cancel 测试（测试债）
- M1 真大头：9.5 分布式队列、12.4 计费业务层、16.4 基础设施级自愈(k8s)、7.6 IDS、7.2 isolation_level(dedicated_node 那档 M1)（9.4 failover/14.4 MCP 审计/16.3 backpressure 已清）
