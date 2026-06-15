# Helix 满分化迭代计划 v2（2026-06-15 重排）

> 取代 `2026-06-13-agent-harness-5star-iteration-plan.md`。依据 `2026-06-15-capability-reassessment.md`
> （重核后均分 4.51→4.59，395/430）重排。**排序口径不变**：ROI = 商业价值(BV) + agent 能力(CAP)，
> 工作量仅排期参考。

## 为什么重排

旧 v1 计划（06-13）之后 ship 了 Stream PI / HX-10 / 可观测 A.8 / eval 平台 S2，重核显示旧表多项已自愈：

- **eval 平台已落地**：11.3/11.6 → ★5（S2 全交付），不再是 P1。
- **沙箱安全自愈**：旧表头号短板域7（3.63）→ 4.25。7.3 输入校验 ★2→★4、7.4 输出过滤 ★3→★4
  （Stream PI）、7.2 分级隔离 ★2→★3（HX-10）、7.6 攻击监控 ★2→★3（PI 指标）。**旧 P3/P4 的安全项
  大半已升档**，不再是优先攻坚区。
- **可观测**：10.1 连接式 trace ★2→★4（A.8）。
- **新增非满分**：2.2 schema 校验 ★5→★4（重核发现无 runtime 校验）。

→ 优先级重心从「补沙箱安全」转向「**收割廉价 ★4→★5 + agent 能力项**」。

## 当前非满分项（16 项；2.2/4.1/11.4/11.5/3.3 已收为 ★5）

★4（一步之遥，多为低成本）：4.4 · 1.3 · 7.3 · 7.4 · 10.1
★3（真 gap）：7.2 · 8.5 · 7.6 · 14.4 · 13.2 · 16.4 · 16.3 · 10.5 · 12.4
★2（M1 大件）：9.4 · 9.5
✅ 已收（本轮 T0）：2.2（#647）· 4.1（确认）

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
> trace passed 3/3 / adversarial 5/6（机制证实，1 真防御观测非假阴）。**均分 4.60→4.63（+2★，398/430）**。

### T1 — Agent 能力护城河（★4/★3→★5，高 CAP）

| 项 | 现 | 内容 | BV·CAP |
|---|---|---|---|
| **1.3 Evaluator-Optimizer** | ★4 | 补 Orchestrator-Worker 完整模式（独立协调者，非同模型链式） | M·H |
| **4.4 agent 自写 skill** | ★4 | 自动演化 `skill_evolution.py`（代码在但 M1 门控）—— 自改进飞轮 | H·H |
| ~~3.3 context-pressure 反馈~~ | ✅★5 | **已交付**：`ContextPressureMiddleware` 量 prompt vs 解析 context_window，usage≥0.75 向末条消息注模型可见预算提示（保前缀缓存），agent 据此收敛。默认 ON 阈值门控。确定性，单测+装配测全证（无需真模型 E2E）。设计见 `2026-06-15-33-context-pressure-feedback-design.md` | M·M |

### T2 — 企业信任 + 安全纵深收尾（多已部分升档，补到 ★5）

| 项 | 现 | 内容 |
|---|---|---|
| 7.3 输入校验 | ★4 | → ★5 需结构化输入隔离（PI-1c untrusted_content 通道）/ 输入分类器（PI-3a） |
| 7.4 DLP 输出 | ★4 | → ★5 需 DLP 分类器 / 条件输出 |
| 7.2 gVisor | ★3 | 生产强制 + `isolation_level` 真实现（运维 + 代码） |
| 8.5 RBAC-ABAC | ★3 | 资源 URI 级 / ABAC（扩 IAM 页，后端+admin-ui） |
| 7.6 IDS | ★3 | Falco/Tetragon runtime IDS（运维） |
| 14.4 MCP 审计 | ★3 | MCP 流量审计日志 + in-process 隔离评估 |

### TX — 接受为终态（★4 但非工作项）

| 项 | 现 | 为何不推 ★5 |
|---|---|---|
| **10.1 连接式 trace** | ★4 | 内部跨服务 trace（orchestrator→sandbox）已全链 + root/业务 span 齐；剩余「★5」gap 仅"外部 egress 不注 traceparent"——这是**故意的安全决议**（防 trace_id 泄露给外部），推 ★5 = 安全反目标。**接受 ★4 为终态**，不列工作项。 |

### T3 — 长程可靠 HA（★2，M1 大件，per-user 持久 agent 核心）

| 项 | 现 | 内容 | 工作量 |
|---|---|---|---|
| **9.4 自动 failover** | ★2 | mid-run 热接力 / 跨进程恢复（J-41） | XL |
| **9.5 分布式任务队列** | ★2 | Celery 等替进程内 asyncio（J.10） | XL |

### T4 — 测试债 + 运维韧性 + 变现（价值最低 / 用户后置，最后）

| 项 | 现 | 内容 |
|---|---|---|
| 13.2 并发 resume 幂等 | ★3 | 补悲观锁 + 并发 resume race 测试 |
| 15.3 跨递归 cancel 测试 | ★5* | (重核维持★5)补跨递归/resume token 消毒测试——测试债非降分 |
| 16.4 基础设施自愈 | ★3 | k8s HPA/failover（基础设施层） |
| 16.3 应用层 backpressure | ★3 | 队列深度 fast-fail 429 |
| 10.5 SLO burn-rate | ★3 | recording rule 落 infra/ |
| 12.4 chargeback 计费 | ★3 | 定价引擎/发票（**用户拍板后置**） |

## 与 v1 的关键差异

1. **旧 P1 eval 平台已交付**（11.3/11.6 ★5）→ 移出优先攻坚；剩 11.4/11.5 仅"接线"降为 T0 廉价收割。
2. **旧 P3/P4 沙箱安全已自愈**（7.2/3/4/6 全升档）→ 从「攻坚区」降为「补到满分的收尾」。
3. **新增 T0 廉价收割层**：4 项 ★4→★5 全 ≤1 天，最高 ROI，旧 v1 没单列。
4. **HA（9.4/9.5）仍 M1 大头**，per-user 持久 agent 形态的核心，但成本 XL，放 T3。
5. **变现 12.4 仍用户后置**，T4 末位。

## 建议执行序

**T0 立即清**（4 项 ≤1 天，无依赖，均分快升）→ **T1 agent 能力**（护城河）→ T2 安全收尾 / T3 HA（看 M1 时间窗）→ T4 收尾。

满分化路径：21 项中 T0+T1 共 7 项是「近满分 + 高 ROI」，清完均分约 4.59→~4.75；剩 T2-T4 多为 M1/运维/业务层大件，按 M0→M1 gate 节奏推。
