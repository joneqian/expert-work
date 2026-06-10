# helix Agent Harness 能力评估（2026-06-10）

> 类型：能力评估报告（源码取证级）
> 范围：helix 作为 Agent Harness 的十维能力——prompt、tools、context policy、hooks、sandbox、subagents、feedback loops、recovery paths + 可观测/治理、多租户隔离
> 方法：3 路并行源码扫描（file:line 级证据）+ Stream CM 实现期一手知识交叉修正 + 与 `2026-06-09-context-memory-management-comparison.md`（OpenClaw/deer-flow/Hermes 对比）的外部基准衔接
> 纪律：每个判断带源码证据；强项不夸大、弱项不回避；**未自测的质量数字一律不声称**（检索/记忆数字以 CM-N5 基线为准，本报告撰写时全量真跑进行中）

---

## 0. 执行摘要

helix 是一个**多租户、server-side、DB-中心**的 per-user 持久 agent 平台。截至 2026-06-10（Stream CM 全部 11 项收尾后），它在**上下文管理、运行时反馈回路、恢复路径、多租户治理**四个维度达到了同类开源 harness（OpenClaw/deer-flow/Hermes）的第一梯队或超出；主要短板集中在**扩展性**（租户级 hook 为零、prompt 无版本管理）、**学习闭环后段**（用户反馈只存不学、无 online A/B）和**执行面广度**（无浏览器/GPU/多语言运行时）。

| # | 维度 | 总判 | 一句话依据 |
|---|------|:----:|-----------|
| 1 | Prompt 工程 | **中强** | 组装分层 + advisory 红线 + 增量摘要规范强；版本管理/A/B 缺失 |
| 2 | Tools | **强** | 14+ 内建 + MCP 池/断路器 + 工具 RAG + 并行调度；无浏览器/GPU |
| 3 | Context policy | **强** | 五层级联 + 可恢复压缩 + 时间衰减/MMR/rerank 检索全链；token 估算粗糙 |
| 4 | Hooks/扩展点 | **中** | 4 锚点 + 8 内建 middleware 架构强；租户级自定义为零（编译期冻结） |
| 5 | Sandbox | **中强** | gVisor + per-user 持久卷 + 配额/备份/DLQ；冷启动、资源限额粒度粗 |
| 6 | Subagents | **中强** | 深度 3 + 并行安全 + deadline/取消跨递归 + 轨迹留痕；无 agent 间通信 |
| 7 | Feedback loops | **中强** | 运行时三环 + 跨 run 三环齐备；用户显式反馈→学习断链 |
| 8 | Recovery paths | **强** | LLM fallback 链 + checkpoint 消毒恢复 + 取消传播 + DLQ；无 run 级自动重试 |
| 9 | 可观测/治理 | **中强** | 80+ AuditAction + 三层配额 + Langfuse 框架；结构化日志/直方图覆盖薄 |
| 10 | 多租户隔离 | **强** | RLS default-deny + per-user 双层 + threat scan 写入门 |

---

## 1. Prompt 工程 —— 中强

### 机制与证据

| 子项 | 证据 | 判定 |
|------|------|:----:|
| system prompt 组装 | `agent_factory.py:899-971`：manifest template + eager skill 全文 + `<available-skills>` 摘要 + SE-10 行为补丁块，markdown 分节 + 每节 advisory 前导 | 强 |
| prompt cache | `providers/anthropic.py:255-394`：system 块 + 尾部 ≤4 条消息带 `cache_control: ephemeral`；`cache.enabled` per-agent 可关（K.K4 时间敏感豁免） | 中强 |
| 摘要 prompt 规范 | `context/compressor.py`：`<context-summary>` 包裹 + reference-only preamble（CM-H2，"非指令"强语义）+ Facts/Decisions/Pending 三段结构 + 二压**增量更新**前次摘要而非重生成（CM-7） | 强 |
| recitation | `graph_builder/planner.py:116-122` + `builder.py:161-165`：plan 渲染为 checkbox 块注入 prompt 尾（CM-0 N1），`helix_cm_recitation_chars` gauge 监控膨胀 | 强 |
| 版本管理 / A/B | 无 prompt registry、无 variant 表、无 diff/rollback；评测全离线（`tools/eval/`），无 online A/B | **缺失** |

### 评语

组装层是同类里讲究的：skill 注入有 advisory 红线防指令覆盖，摘要有"背景非指令"语义（Hermes SUMMARY_PREFIX 同范式），recitation 对抗 lost-in-the-middle（Manus/Claude Code 事实标准）。真实短板在**工程化管理**：prompt 是 manifest 里的一段字符串，改 prompt = 改 manifest 发版，没有版本对比、灰度、效果归因。对一个以"agent 即产品"为形态的平台，prompt 迭代会是高频操作，这条缺口随租户数量增长放大。

---

## 2. Tools —— 强

### 机制与证据

| 子项 | 证据 | 判定 |
|------|------|:----:|
| 内建工具面 | `orchestrator/tools/`：exec_python（沙盒）、bash（exec 隧道 + irreversible 自动门控）、file_ops（原子写 + realpath 双检 confinement）、http（租户 allowlist）、web_search、knowledge_search（hybrid+RRF+可选 rerank）、ask_image（独立 VL 模型分流，J.6）、save/list_artifacts、ask_for_approval、update_plan、subagent、find_tools、MCP | 强 |
| 工具元数据 | `registry.py:55-126`：JSON Schema + `is_read_only` / `path_args`（冲突检测）/ `is_parallel_safe` / `side_effect`（read_only/reversible/irreversible）/ `idempotent` | 强 |
| 并行调度 | Stream L.L6：按 side_effect + path_args 冲突分阶段，`asyncio.gather`（MAX_TOOL_WORKERS=8）；irreversible 强制串行 | 强 |
| 工具 RAG | TE-6：`register(deferred=True)` + `find_tools`（select:/regex/keyword 语法）+ `promoted_tools` 状态通道——大工具集不撑爆 prompt | 强 |
| MCP client | `mcp.py`：stdio/SSE/streamableHTTP 三传输、池上限 5、断路器（5 失败/30min，U-13）、防御性上限（256 工具/16k desc/65k schema）、中间截断可恢复 | 强 |
| 失败处理 | CM-1 `error_classifier.py`：8 类确定性分类（零 LLM）→ `<recovery-advisory>` 模板建议注入；L-4 mutation-not-landed 检测；L-5 失败计步 refund | 强 |
| 执行面广度 | 无浏览器/computer-use、无 GPU、运行时仅 Python（bash 为逃生舱）；MCP oauth2 声明未实现（U-12 推迟） | **缺失** |

### 评语

工具**基建**是 helix 最厚的一层：元数据驱动的并行调度、deferred 工具 RAG、MCP 断路器，这三样在对比过的三个开源 harness 里没有一家全有。真实缺口是**执行面广度**——没有浏览器意味着"查网页只能靠 web_search 摘要 + http 原文"，对需要交互式网页操作的任务无解；多语言运行时缺失限制了代码类 agent 场景。这两条属于"做不做"的产品取舍而非工程债，但应显式决策而非默认搁置。

---

## 3. Context policy —— 强

### 机制与证据（五层级联，自外向内）

| 层 | 机制 | 证据 | 判定 |
|----|------|------|:----:|
| ① 记忆召回 | hybrid 检索（pgvector + tsvector + RRF）→ 时间衰减（半衰期 30 天，CM-6）→ cross-encoder rerank 全序（CM-4）→ MMR 去冗余殿后（λ=0.7，CM-6）→ 宽召回 20 | `persistence/memory/sql.py`、`graph_builder/memory.py:182-250` | 强 |
| ② 滑动窗口 | 零 LLM 前置闸：保首 turn + 最近 N turns，HumanMessage 边界切割保 ToolCall↔ToolResult 配对，prompt-view-only 不改 checkpoint（CM-2） | `context/working_window.py:72-150` | 强 |
| ③ 压缩前抢救 | compressor 丢弃中段前回调 `flush_messages_to_memory` 中途落盘（CM-3），best-effort 不阻塞 | `compressor.py:368-372` | 强 |
| ④ LLM 压缩 | summarize-the-middle：head4/tail6 保留 + 中段摘要 + 最多 3 pass + `ContextOverflowError` 显式失败；二压增量更新（CM-7） | `compressor.py:268-412` | 强 |
| ⑤ 运行时护栏 | DynamicContextMiddleware 再 trim（max_turns/max_tokens），SystemMessage 永不裁（保 L-1 cache） | `runtime/middleware/dynamic_context.py:85-111` | 中 |
| 工具结果管控 | per-tool char cap（web 4k/http 20k/MCP 20k 中截）+ `truncated` 标记 + **超限全文外部化** workspace `.tool_results/`（CM-5：`full_content` → footer 虚拟引用 + read-only 工具豁免防循环） | `tools/overflow.py`、`builder.py:1302-1312` | 强 |
| token 估算 | `len // 4` 经验值，无真 tokenizer；CJK 偏激进 | `dynamic_context.py:26-38` | **弱** |
| 长上下文策略 | 无按 `context_window` 自适应窗口；1M 模型与 200K 模型同一套阈值参数 | — | 缺失 |

### 评语

这是 helix 当前**最完整**的维度——本周 Stream CM 收尾后，2026-06-09 对比报告里所有判"弱/缺失"的项（滑窗、可恢复压缩、衰减/MMR/rerank、压缩前抢救、增量摘要）已全部补齐，五层级联在对比的三家开源 harness 里无一家做到全栈。遗留两条真实弱项：`len//4` 估算误差 ±15% 会让压缩触发点漂移（接真 tokenizer 是小改动高确定性收益）；长上下文模型（qwen3.7-max 1M）没有差异化策略，等于花钱买了窗口没用上。检索链叠加增益数字待 CM-N5 基线（撰写时真跑进行中）。

---

## 4. Hooks / 扩展点 —— 中

### 机制与证据

| 子项 | 证据 | 判定 |
|------|------|:----:|
| 锚点 | `runtime/middleware/base.py:23-30`：`before_llm_call` / `around_llm_call` / `after_llm_call` / `before_tool_dispatch` 四锚点，未知锚点构建期报错 | 强 |
| 内建 middleware | 8 个：llm_error_handling（断路器+退避）、loop_detection、dynamic_context、sandbox_audit（AST 白名单）、token_usage、pii_redact（租户感知）、llm_cache（Redis 精确匹配）、langfuse；拓扑排序编译期组装 | 强 |
| 租户级自定义 | **零**——manifest 无 `middlewares:`/`hooks:` 字段，`MiddlewareChain.from_middlewares` 构建期冻结（STREAM-E §2.2 显式决策：M0 不暴露） | **缺失** |
| HITL approval | `protocol/approval.py:46-168`：Request/Decision/Status/Record 全 DTO + 策略门（`approval_required_tools`）+ agent 主动 `ask_for_approval` + 超时自动 TIMEOUT + modify（改 proposed_args 再提交，前端 ApprovalCard 完整）+ resume 经 ingest（CM-8 修复）+ 全程 audit | 强 |

### 评语

middleware 架构本身质量高（声明式锚点 + 拓扑排序 + always-on/env-gated 分级），HITL 是对比三家里最完整的（deer-flow 仅 interrupt 原语、Hermes 无）。核心张力在**"平台管一切" vs "租户可扩展"**：当前所有横切行为都是平台编译期决定，租户唯一的定制面是 manifest 声明（工具集/策略/模型）。对 M0 的中心化治理路线（平台独占 LLM/MCP 目录化）这是自洽的；但"agent 即产品"长期必然遇到租户要在工具调用前后挂自己的校验/通知逻辑的需求——届时这条是架构级缺口，建议在 M1 路线图上显式排期而非自然演化。

---

## 5. Sandbox —— 中强

### 机制与证据

| 子项 | 证据 | 判定 |
|------|------|:----:|
| 隔离架构 | sandbox-supervisor 独立服务 + gVisor 容器 + 持有管道传输（docker run -i + RunnerLink 隧道，F-4）；acquire/exec/release/destroy 全程 audit | 强 |
| per-user 持久工作区 | (tenant,user)→sandbox 温会话复用（J.15）+ named volume + 每日快照 + 软删除归档（tar.gz→ObjectStore，J-36）+ 备份 DLQ（5 档退避） | 强 |
| 配额 | QuotaEnforcer：workspace 体积上限 + 软删除拒新 acquire（J-29/J-36） | 中 |
| 文件边界 | realpath 双检（symlink 逃逸）+ 相对路径约束 + NUL 拒绝 + 10MB 读写上限 | 强 |
| 输出管控 | 20k char cap + truncated 标记 + CM-5 全文外部化；per-call timeout 1-300s | 强 |
| 冷启动 | M0 每 acquire 一次 docker run，无热池（设计上 M1） | 弱 |
| 资源限额粒度 | 无 per-call CPU/mem/IOPS 限制；超时全局默认 60s；无 seccomp/AppArmor 加固、无镜像 CVE 扫描 | **弱** |
| 执行面 | 仅 Python + bash；无浏览器驱动、无 GPU | 缺失 |

### 评语

per-user 持久工作区是 helix 的**形态级资产**（直接支撑"持久 agent"产品定义），生命周期管理（备份/归档/DLQ）完整度超出同类。安全侧依赖 gVisor 单边界——对多租户 SaaS 而言"gVisor + 无 seccomp 叠加 + 无镜像扫描"是可接受但偏薄的纵深，建议在对外开放租户自定义代码执行前补第二层。冷启动延迟是已知 M1 项。

---

## 6. Subagents —— 中强

### 机制与证据

| 子项 | 证据 | 判定 |
|------|------|:----:|
| spawn 与隔离 | `tools/subagent.py:118-250`：agent_ref（name@version）→ 独立 BuiltAgent + 独立 thread/sandbox session，结构性深度上限 MAX_SUBAGENT_DEPTH=3 | 强 |
| 并行 | `is_parallel_safe=True`（J-40）：兄弟委托各自隔离 → L.L6 gather 并行 | 强 |
| 预算与取消 | parent 的 `deadline_at` 跨递归**不重置**（K.K8），cancellation token 全程传播，启动前短路检查 | 强 |
| 结果与留痕 | 最终答案 + step_count + duration；max_steps 超限回传部分进度（非错误）；轨迹 fire-and-forget 落 ObjectStore（J-21，含 cancelled/超限分支） | 强 |
| 模型/工具独立性 | 子 agent 按自身 manifest 配模型与工具集 | 中 |
| agent 间通信 | 无 broker/队列，仅 task 文本下行 + 答案上行；子 agent 不可见 parent plan | **缺失** |
| 类型注册表 | 无专门化 agent 类型池/动态路由，agent_ref 固定在 manifest | 缺失 |

### 评语

单链委托（编排者→工人）做得扎实——deadline 不重置和取消传播两条是多数开源实现会漏的正确性细节。缺的是**多 agent 协作形态**：无法做平级 agent 协商、无法动态选择"哪个专家 agent 接这个子任务"。注意这与 Cognition "Don't Build Multi-Agents" 的工程共识相符——单链 + 显式委托是当前可靠性最优解，缺失项属于"暂不做"的合理选择而非欠债，但报告如实记录边界。

---

## 7. Feedback loops —— 中强

### 机制与证据

**运行时（turn/run 内）三环**：

| 环 | 证据 | 判定 |
|----|------|:----:|
| reflect 自评 | `graph_builder/reflect.py:151-220`：verdict accept/revise + 批评注入 + revised_steps 重写 plan；预算上限 + 30s 超时 force-accept + JSON 解析失败 fail-safe | 强 |
| error-as-guidance | CM-1：8 类确定性分类 → recovery advisory 注入（read_only/idempotent 可重试、irreversible 禁盲重）；失败工具计步 refund（L-5） | 强 |
| loop→升档 | E.10.5 指纹去重（3 连击清 tool_calls + reminder）→ CM-9/10 双信号升档（loop ∨ 75% 步数预算 → escalated caller，全 9 provider：effort 档/budget 换算/toggle 开思考） | 强 |

**跨 run 三环**：

| 环 | 证据 | 判定 |
|----|------|:----:|
| 记忆写回+调和 | run 末抽取 → reconcile（邻居检索 + ADD/UPDATE/DELETE/NOOP 显式操作，CM-7 Mem0 范式，失败降级直写）→ DLQ 兜底 | 强 |
| 记忆固化 | consolidator 4h 周期：聚类合并 + 反误学 6 类拒绝 + 孤项噪声清理（U-34/35） | 强 |
| skill 进化 | SE 全环：distill → replay 验证 → 失败归因（execution vs content error 分流防坍缩）→ 修订（≤3 轮）→ 门控自动晋升（SE-7c）→ 回滚监控（SE-8 按 run 结果归因到版本） | 强 |

**断链处**：

| 项 | 证据 | 判定 |
|----|------|:----:|
| 用户显式反馈 | `api/feedback.py:47-98`：rating+comment 入库 + audit——**无消费者**，不触发 skill 修订/记忆修正/任何学习 | **弱** |
| 评测回路 | `tools/eval/`：16 能力模块 + CI 确定性冒烟 + LongMemEval/LoCoMo 双层基线（CM-N5，真跑进行中）；无 online A/B | 中 |
| 自动 prompt 优化 | 无（无 DSPy 类、无 meta-LLM 调优） | 缺失 |

### 评语

运行时与跨 run 的**自动**回路是六环齐备的——特别是 skill 进化环（归因分流 + 防坍缩 + 自动回滚）在开源 harness 里没有对标物。讽刺的断点恰在**人**：用户点了 👎 之后什么都不会发生。这条修复成本不高（feedback consumer worker → 关联 run 的 skill 版本/记忆条目 → 进 SE 修订队列或记忆 review 标记），且与 per-user 持久 agent 的产品承诺直接相关，建议优先级高于 online A/B。

---

## 8. Recovery paths —— 强

### 机制与证据

| 层 | 证据 | 判定 |
|----|------|:----:|
| LLM 层 | fallback 链（manifest 声明，5xx/429/断路器开/auth 失败/流 stale 90s → 切下一家；4xx 不切直接上报）+ per-key 断路器（5 失败→OPEN→30s→HALF_OPEN）+ provider 限流 token bucket（限流 ≠ 故障不开闸） | 强 |
| 工具层 | 失败 fail-soft 续跑（错误转 ToolMessage + recovery advisory），不级联中止；irreversible 禁盲重 | 强 |
| run 层 | Postgres checkpointer 每节点落盘 + **resume 前消毒**（孤立 tool_calls 注占位 ToolMessage，`resume.py:31-62`）+ approval pause→resume（approve/reject/modify 三路 + resume 经 ingest 不丢暂停期人工编辑，CM-8）+ cancellation token 全链（in-flight LLM 调用真中断，≤200ms） | 强 |
| 撞限 | max_steps 显式失败 + loop 指纹 + 双信号升档 + run deadline 跨递归 | 强 |
| 数据层 | memory writeback DLQ（5 档退避 + 死信）+ workspace 快照/归档 | 中强 |
| 缺失 | 无 run 级自动重试策略（失败 run 由用户/上游重发）；无事务性工具/部分回滚（checkpoint 单版本前滚）；无工具级超时/重试预算配置 | **弱** |

### 评语

"前滚式恢复"（fail-soft + advisory + checkpoint resume）做得完整且有正确性细节（resume 消毒是多数实现会漏的坑）。缺失的三项里，**run 级自动重试**最值得补（瞬态故障 run 自动重发一次的 ROI 明确）；事务性工具/回滚是行业共同未解题，按"明确不做 + 依赖 irreversible 门控 + approval"的现行策略是诚实的工程选择。

---

## 9. 可观测 / 治理 —— 中强

| 子项 | 证据 | 判定 |
|------|------|:----:|
| audit | 80+ `AuditAction`（运行/工具/LLM/审批/配额/工作区/记忆/skill），redaction（全局+租户 PII 字段）+ 主写 SQL + JSONL fallback 队列 + 查询自审计 | 强 |
| metrics | `helix_` 前缀 + 低基数纪律；token_usage/cache/rate-limit/DLQ/CM 系列（recitation chars、overflow、escalation 等）；缺工具延迟直方图、run 成功率、approval 队列 gauge | 中 |
| tracing | Langfuse middleware 框架在位（W3C traceparent），M0 为 Recording stub，生产接线推后 | 中 |
| 计费/配额 | 三层（网关限流 → 租户配额 8 维度 → provider token bucket）+ G.9 token 计量 + rate card + chargeback | 强 |
| 结构化日志 | 关键事件有（run 状态机/audit 失败/链组装），无统一 span 字段贯穿 | 弱 |
| admin UI | RunDetail（事件流/trace 工具条/plan 面板/approval 卡）、audit 查询、配额、用量、账单在位；缺独立 approval 队列页、trace 可视化 | 中 |

### 评语

治理面（audit/配额/计费）是按多租户 SaaS 标准建的，超出"开源 harness"的常规范畴。可观测纵深不均：审计很厚、指标中等、日志和 trace 偏薄——排障长尾会先撞到这里。

---

## 10. 多租户隔离 —— 强

| 子项 | 证据 | 判定 |
|------|------|:----:|
| RLS | `persistence/rls.py`：每事务 `SET LOCAL app.tenant_id`（PgBouncer 安全），ContextVar 未设 → fail-closed 拒全部 | 强 |
| per-user 第二层 | memory/workspace/artifact 再加 `app.user_id` 谓词（纵深防御） | 强 |
| 注入防护 | 记忆写入 strict threat scan（注入/C2/外泄模式）+ drift 检测 + skill 发布门（高危工具需 admin 批准，U-24）+ trigger payload 扫描（U-2） | 强 |
| 已知薄点 | 平台 provider/tool 凭证 M0 全局不分租户；跨租户查询只记 audit 不 block | 中 |

---

## 11. 横向定位（对照 2026-06-09 外部研究）

以同一框架对照 OpenClaw / deer-flow / Hermes（详见对比报告）：

- **已反超的维度**：context policy（五层级联，三家各有 1-2 层）、记忆检索链（hybrid+衰减+rerank+MMR+consolidator+reconcile 全栈，三家最多 hybrid+MMR）、recovery（resume 消毒 + approval modify 三家皆无）、治理（多租户 audit/配额是形态差异，三家为单机工具无此需求）。
- **同梯队**：loop 防护（三家皆有指纹去重，helix 多升档联动）、subagent（与 deer-flow 相当，强于另两家）、error-as-guidance（与 Hermes ClassifiedError 同范式）。
- **落后/空白**：本地工具生态广度（OpenClaw 浏览器/多运行时）、用户可扩展性（三家作为本地工具天然"用户即开发者"，helix 编译期冻结）、prompt 工程化（皆弱，无人做好——helix 不落后但也没领先）。

注意形态差异使部分对比天然不对称：helix 的多租户/审计/配额成本换来了三家不需要付的复杂度；三家的"用户即 root"换来了 helix 给不了的扩展自由。

---

## 12. Top gaps 优先级（按缺口真实性 × 形态适配排序）

依"功能可少、能力不可弱"原则，每条给明确处置建议，不设"只记录不补"档：

| # | 缺口 | 维度 | 建议处置 | 理由 |
|---|------|------|---------|------|
| 1 | 用户反馈→学习断链 | ⑦ | **立项**（feedback consumer → SE 修订队列/记忆 review） | 数据已在收，闭环成本低，直接服务 per-user 持久 agent 承诺 |
| 2 | 真 tokenizer 替换 len//4 | ③ | **立项**（小） | 压缩触发点漂移影响全部长对话；改动小收益确定 |
| 3 | run 级自动重试（瞬态故障） | ⑧ | **立项**（小中） | 前滚体系已全，缺最后一跳；幂等性判定基础（side_effect 元数据）已在 |
| 4 | prompt 版本管理 + 离线 A/B | ① | **立项**（中，随租户增长提级） | CM-N5 评测基建已就位，variant 对比有跑道；online A/B 后置 |
| 5 | 租户级 hook 扩展点 | ④ | **M1 路线图显式排期** | 架构级，与中心化治理路线需统一设计，不宜自然演化 |
| 6 | 长上下文自适应策略 | ③ | **观望→低成本先行**（压缩阈值按 context_window 参数化） | 1M 模型已在目录；全策略待真实长对话画像 |
| 7 | sandbox 安全纵深（seccomp/镜像扫描） | ⑤ | **对外开放自定义代码前必做** | 当前单 gVisor 边界对内部租户可接受 |
| 8 | 浏览器执行面 | ② | **产品决策**（非工程债） | 取决于目标场景是否含交互式网页任务；做则体量大 |
| 9 | agent 间通信/类型注册表 | ⑥ | **明确不做（现阶段）** | 单链委托是可靠性最优解（业界共识）；需求出现再议 |
| 10 | 事务性工具/部分回滚 | ⑧ | **明确不做** | 行业未解题；irreversible 门控 + approval 是诚实替代 |

---

## 13. 结论

helix 当前是一个**核心循环扎实、治理面超配、扩展面收紧**的 server-side Agent Harness：上下文/记忆/恢复三大硬能力在同类中处第一梯队（其中检索链与五层上下文级联为撰写时所见最完整实现），多租户治理是形态级差异化资产；代价是把"可扩展性"几乎全部收进平台编译期——这与当前中心化治理路线自洽，但租户级 hook 与 prompt 工程化两条会随平台增长从"选择"变成"欠债"，建议趁早进路线图。学习闭环六环已通五环，断在用户显式反馈一环，是性价比最高的下一刀。

> 质量数字脚注：检索/记忆链的量化效果（LongMemEval/LoCoMo 双层基线）由 CM-N5 提供，撰写时全量真跑进行中，数字落 `tools/eval/baselines/longmem_baseline.yaml` 后以该文件为准——本报告遵守"组件级有据、叠加靠自测、不声称未测数字"的纪律。
