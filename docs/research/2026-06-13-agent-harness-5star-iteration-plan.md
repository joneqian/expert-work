# Helix Harness 满分化迭代计划（价值优先排序）

> 日期：2026-06-13
> 配套：能力评估报告 `docs/research/2026-06-13-agent-harness-capability-assessment.md`

## Context

评估报告给出 86 项、均分 4.37/5（含 W0 修订）。目标：**23 个非满分项全部拉到 5★**。
本计划按 **ROI = 商业价值 + agent 能力提升** 降序排，**不**按实现成本/时间排。
多项与 helix 既有 M1 backlog 重叠，尽量复用既有 Stream ID 不另起炉灶。

**23 个待提升项**（现分→目标 5）：
1.3(3) 3.3(3) 4.1(3) 4.4(3) 7.2(2) 7.3(2) 7.4(3) 7.6(2) 8.5(3) 9.4(2) 9.5(2)
10.1(✅5) 10.5(3) 11.3(✅5) 11.4(✅4) 11.5(✅4) 11.6(✅5) 12.4(3) 13.1(✅) 13.2(3) 14.4(3) 16.3(3) 16.4(3)

---

## 排序口径

**ROI = 商业价值 + agent 能力提升**，工作量仅作排期参考列，不参与优先级排序。
商业价值锚点 = helix 产品形态：**per-user 持久 agent**（长程自主 + 长期记忆 + 持久工作区）
+ 平台中心化治理/变现。据此商业价值四驱动：
① agent 自主能力（自改进/长程）= 产品护城河 ② 企业信任（安全/治理）= 销售门槛
③ 变现（计量/计费）= 收入 ④ 规模可靠性（HA）= 让①在规模下成立。

## 价值矩阵（按商业价值+能力降序）

商业价值 BV / agent 能力 CAP：H/M/L。工作量 S/M/L/XL 仅参考。

| 项 | 现分 | BV | CAP | 优先级 | 工作量(参考) |
|---|---|---|---|---|---|
| 11.4 trace-based eval | 1 | H | H | **P1** | L |
| 11.6 生产 eval worker/scheduler | 2 | H | H | **P1** | M |
| 11.3 会话级结果指标 | 3→1** | H | M | **P1** | M(从零建) |
| 4.4 agent 自写 skill (J.7b-1) | 3→4* | H | H | **P1** | S(仅自动演化) |
| 1.3 Evaluator-Optimizer 编排 | 3→4* | M | H | **P1** | S(仅独立evaluator) |
| 11.5 对抗/injection eval 集 | 2→1** | M | H | **P1** | M(从零建) |
| 10.1 连接式 trace 实装 | ✅2→5 | M | M | done | 已交付 PR#612 |
| 9.4 自动 failover | 2 | H | H | **P2** | XL |
| 9.5 分布式任务队列 (J.10) | 2 | H | H | **P2** | XL |
| 4.1 廉价滑窗截断 | 3 | M | H | **P2** | S–M |
| 3.3 context awareness 反馈 | 3 | M | M | **P2** | S |
| 7.2 gVisor 生产强制 | 2 | H | L | **P3** | M(运维) |
| 8.5 资源级 RBAC-ABAC | 3 | H | L | **P3** | L |
| 7.3 injection 输入语义扫描 | 2 | H | M | **P3** | M–L |
| 7.4 DLP 输出分类 | 3 | M | L | **P4** | L |
| 7.6 runtime IDS (Falco/Tetragon) | 2 | M | L | **P4** | M |
| 14.4 MCP 独立沙箱隔离 | 3 | M | M | **P4** | L |
| 13.2 并发 resume 幂等加固 | 3 | M | M | **P5** | M |
| 16.4 基础设施级自愈 | 3 | M | L | **P5** | L |
| 16.3 应用层 backpressure | 3 | L | L | **P5** | M |
| 10.5 SLO burn rate recording rule | 3 | L | L | **P5** | S–M |
| 12.4 chargeback 计费 | 3 | H | L | **P5**(用户定后置) | L |
| 13.1 会话隔离 ✅ 完成 | 4→5 | M | M | done | — |

**与「成本ROI」排序相比的大变动**（说明价值口径如何改变次序）：
- **HA（9.4/9.5）：成本口径末位 → P2**。per-user 持久 agent 必须能可靠跑长任务，HA 是核心能力非纯运维。
- **agent 自写 skill（4.4）：→ P1**。自改进 = 能力护城河 + 产品差异化。
- **gVisor（7.2）：成本口径快赢 → P3**。仍重要（企业门槛），但非 agent 能力，价值口径降为中。
- **SLO/backpressure（10.5/16.3）：→ P5 末位**。纯运维，商业/能力价值最低。
- **变现 chargeback（12.4）：BV 高但用户拍板后置 → P5 末位**。

> `*` 2026-06-13 S0 核实：1.3/4.4 经读码上调 3→4★（reflect.py 已是 Evaluator-Optimizer；
> skill_authoring.py M0 已能自写 skill），P1 这两项从"新建"降为"小补"。详见
> `2026-06-13-p1-self-improving-flywheel-design.md`。
>
> `**` 2026-06-13 全量重核（手动代码核实）：11.3 会话级指标 3→1（`resolution_rate` grep 全空、
> 未编码）、11.5 对抗集 2→1（无 adversarial 目录）——原靠 subsystems/26 设计文档的"假 done"。
> P1-S2 eval 平台从「部分补强」改为「**从近零建**」，更印证 S2 价值。10.1 已交付（PR #612, ✅5★）。
> 评分可靠性元结论见评估报告顶部：静态读码不可靠，emission/wiring 类项需运行期验证。

---

## 前端对接审计（admin-ui，2026-06-14）

> **教训**：迭代项不能只做后端。`apps/admin-ui` 惯例 = 每个 operator 能力配页面
> （`Xxx.tsx` + `.stories.tsx` + `src/api/xxx.ts` SDK + Sidebar/router/CommandPalette，见 SE-8 接线点）。
> 但 ops/基础设施/安全后台项是 **Grafana/外部面**，不该塞进 admin-ui。逐项审一遍：

图例：✅已有 · ❌需建 admin-ui · 🔶可选增强 · ⬜无需(ops/Grafana)

| 项 | 前端 | 交付 / 说明 |
|---|---|---|
| 10.1 连接式 trace | ✅ | `RunDetail` 已有 `TraceToolbar` 按 trace_id deep-link Langfuse/Tempo |
| **11.6 eval worker** | ✅ | **Eval 页已建**（list+detail+触发，PR #620 worker / #622 list API / S2.5-FE）；`src/api/eval_runs.ts` client + nav `eval` |
| **11.3 会话级指标** | ✅ | 端到端（#624）：`session_metrics` 经 worker→API→Eval run 详情列显示 |
| **11.4 trace-based eval** | ⬜ | 模块已交付（#625）未接 worker suite，FE 暂无数据源；接线后经 detail 列显示 |
| **11.5 对抗集** | ⬜ | 模块已交付（#626）未接 worker suite，FE 暂无数据源；接线后经 detail 列显示 |
| 1.3 evaluator-optimizer | 🔶 | reflection verdict 可加进 RunDetail |
| 4.4 自写 skill | 🔶 | Skills 页已有；加「agent 自写」badge/筛选 |
| 9.4 failover / 9.5 分布式队列 | ⬜ | ops/Grafana |
| 4.1 滑窗 / 3.3 context awareness | 🔶 | context 用量% 可加进 RunDetail |
| 7.2 gVisor | ⬜ | sandbox runtime = ops 配置 |
| **8.5 资源级 RBAC-ABAC** | ❌ | 扩 `SettingsIam`/`SettingsRoleBindings`（资源 URI 级权限 UI） |
| 7.3 injection 扫描 | 🔶 | 安全事件可进 audit 视图 |
| 7.4 DLP / 7.6 IDS | 🔶 | 安全告警面（多为 Grafana） |
| 14.4 MCP 隔离 | 🔶 | `SettingsMcpServers` 已存；加隔离状态 |
| 13.2 并发 resume | ⬜ | 后端 |
| 16.4 自愈 / 16.3 backpressure / 10.5 SLO | ⬜ | **Grafana 非 admin-ui** |
| 12.4 chargeback | ✅ | `SettingsBillingChargeback`+`RateCard`+`Usage` 已有 |
| 13.1 隔离 ✅ | ⬜ | 仅测试 |

**真前端 gap 只有 2 个**：
1. **Eval 平台页**（一页覆盖 11.3/11.4/11.5/11.6）—— P1-S2 的前端交付，backend(S2.1a–d ✅) 落完即做。
   按 admin-ui 设计基线 + SE-8 接线点全套（router/Sidebar/CommandPalette/SDK/i18n 双语/Storybook/Playwright/TenantScope/envelope 对账）。
2. **8.5 RBAC 资源级**扩 IAM 页 —— 到 P3 随后端一起做。

其余：3 项已有前端（10.1/12.4/4.4 基础）、几项可选增强、约一半是 ops/Grafana 无需 admin-ui。

---

## W0 — 验证落地（已执行 2026-06-13）

⚠️ 项证据靠设计文档，先核实把分坐实。**核实结果：一升一降，揭出原评分高估。**

- **13.1 会话隔离 ✅ 完成 4→5**：核实 `runner.py:GraphRunner.compile` + LangGraph thread
  namespace 隔离真落地（`test_runner_unit.py:test_thread_isolation` 已有）。补两条并发隔离测试
  （`test_concurrent_two_threads_no_state_bleed` + `test_concurrent_long_sessions_no_pollution`），
  5 passed。坐实 5★。
- **10.1 连接式 trace ❌ 下调 4→2，移出 W0**：核实发现 W3C 传播层（`propagation.py`）真落地，
  但 `run_agent()` 无 `helix.session.run` 根 span、Span Link 零实现、LLM/tool 业务 span 缺——
  连接式 trace 核心未实装（~25-30h L 工作量，非数天验证）。**重归类至 P1**：它是 11.4
  trace-based eval 的前置（没有真 span 树就无法做 trace eval），与 eval 飞轮同批做。

## P1 — Agent 自改进飞轮（最高商业价值 + 能力护城河）

为什么第一：自改进 = 产品护城河。eval 平台是飞轮（测得准才改得动），evaluator-optimizer +
自写 skill 让 agent 自我提升。7 项共享 eval/trace 基础设施，一并起。

- **10.1 连接式 trace 实装（飞轮地基 / 11.4 前置）**：`run_agent()` 包 `helix.session.run`
  根 span；agent_node/tools_node 加 LLM/tool child span；`helix_span()` 扩 `links=`，
  subagent/durable-resume 接 Span Link。补完整 run trace 集成测试。2→5。
- **11.6 生产 eval worker** ✅ **(2→5, #618/#620/#622 + FE #623)**：`eval_run`/`eval_case_result` 表 + 常驻 `EvalWorker`（lifespan 门控）+ enqueue/read API + admin-ui Eval 页。端到端。
- **11.3 会话级结果指标** ✅ **(1→5, #624)**：`session_metrics_from_cases` 出 `goal_completion`，端到端 plumb 引擎→worker→API→FE；escalation 仅信号时出不零填。
- **11.4 trace-based eval** ✅ **(1→4, #625)**：`trace_eval.py` 纯断言引擎 + capture harness，断言调用链；脚本图 CI 实跑。★4=未接 worker suite（需 model-backed responder）。
- **11.5 对抗集** ✅ **(1→4, #626)**：`adversarial.py` + `datasets/adversarial/`，injection canary 不泄/jailbreak 拒答，硬门 safe_rate=1.0。★4=未接 worker suite。
- **1.3 Evaluator-Optimizer**：reflection 节点 + judge 组 evaluate→optimize 回环，agent 自我纠错，补 Anthropic 第五模式。3→5。
- **4.4 agent 自写 skill（J.7b-1）**：agent 运行中提案 skill→审核→active，程序记忆自增长。3→5。
- 验证：**飞轮闭环**——真实 run 产 trace → eval 打分 → 低分进对抗集 → evaluator-optimizer 重跑改善；自写 skill e2e。

## P2 — 长程自主 + 规模可靠（持久 agent 形态核心）

为什么第二：per-user 持久 agent 必须可靠跑长任务。长程连贯 + 崩溃不丢是产品形态地基，**非纯运维**。

- **9.4 自动 failover + 9.5 分布式队列（J.10）**：worker 崩溃后从 checkpoint+event_log 自动接力（非 human resume）；分布式 runtime 替 FastAPI background task。耦合做。2→5。
- **4.1 廉价滑窗**：compressor 前加"保留最近 N 轮+第一轮"无 LLM 截断闸（research A2 已定），长会话连贯且省。3→5。
- **3.3 context awareness**：system 注入 `context_pct`/`remaining_tokens`（复用 `runtime/tokens.py`），agent 知预算自调节。3→5。
- 验证：kill worker run 自动续跑；mid-tool 崩溃恢复；长会话 token 降耗；context_pct 入 prompt。

## P3 — 企业信任闸（销售门槛）

为什么第三：高商业价值但低 agent 能力。多租户 untrusted code 安全 + 治理是企业成交前提。

- **7.2 gVisor 生产强制**：settings 默认 `runsc` + 部署装 runtime（HX-10 已 CI 验、零代码债）。2→5。
- **8.5 资源级 RBAC-ABAC**：`PolicySpec` 扩资源 URI 级（工具可达路径/DB schema 白名单）+ 运行时 ABAC。3→5。
- **7.3 injection 输入语义扫描**：扩 `threat_patterns.py`/`sandbox_audit.py`，LLM 生成代码/入参语义检测。2→5。
- 验证：runsc prod 冒烟；资源级权限拒绝测；injection 红队样本被拦。

## P4 — 安全纵深

- **7.4 DLP 输出分类**：`pii_redact.py` 升内容分类驱动的条件输出。3→5。
- **7.6 runtime IDS**：沙箱接 Falco/Tetragon，seccomp violation/异常出站/cap 滥用告警。2→5。
- **14.4 MCP 独立沙箱**：MCP client 连接移入沙箱/独立进程 + 流量审计。3→5。
- 验证：DLP 分类用例；Falco 告警入面板；MCP 隔离测。

## P5 — 运维韧性 + 变现收尾（价值最低 / 用户定后置，最后）

- **13.2 并发 resume 幂等加固**：idempotency_key race + 并发 resume 冲突解决 + 压测。3→5。
- **16.4 基础设施自愈**：K8s HPA/探针自动重启 + sandbox warm-pool auto-evict。3→5。
- **16.3 backpressure**：池/队列饱和 fail-fast + 429。3→5。
- **10.5 SLO burn rate**：infra/ 落 Prometheus recording rule + 补齐 dashboard 至 6 个。3→5。
- **12.4 chargeback（用户定后置）**：rate card + 定价规则 + 月度对账/发票。BV 高但用户拍板放最后。3→5。
- 验证：并发 resume 压测无串话；chaos 自愈；饱和 backpressure；burn rate 面板出数；账单对账数对。

---

## 排序逻辑 & 已定决策

- **价值降序**：P1 自改进飞轮 → P2 长程自主+HA → P3 企业信任 → P4 安全纵深 → P5 运维+变现收尾。
- **注**：价值口径下 P1/P2 含 L/XL 大件（HA），排前是因价值高非好做。10.1 trace 已交付(✅PR#612)。P1 内最快见效=1.3/4.4 小补（已 4★，仅需独立 evaluator / 自动演化）；eval 平台(11.x)经重核为近零，需从头建（11.6 worker→11.3 指标→11.5 对抗集→11.4 trace-eval）。
- **已定决策（2026-06-13）**：
  1. ✅ **P1 飞轮优先**（先做自改进，提升单 agent 质量/产品力）。
  2. ✅ **变现（12.4）后置**至 P5 末位（虽 BV 高，用户拍板放最后）。

---

## 总览

- 全 23 项做完 = 全 5★，均分 5.0。
- **P1+P2 完成 → agent 核心能力（自改进 + 长程 + HA）满分，产品力最大跃升段**。
- 复用既有：reflection 节点(evaluator)、`memory_consolidator.py`/`webhook_delivery_worker.py`(worker)、`threat_patterns.py`(injection)、HX-10(gVisor)、J.10(队列)、J.7b-1(自写skill)。
