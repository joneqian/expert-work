# 框架报告：Expert Work 在「上下文与记忆管理」上的可借鉴与可改进点（v2，含外部证据）

> 日期：2026-06-09 · 类型：框架报告（骨架，确认方向后再深化为 STREAM 级实施计划）
> 依据：`docs/research/2026-06-09-context-memory-management-comparison.md`（OpenClaw/deer-flow/Hermes 对比）+ Expert Work 现状源码取证
> v2 更新：合并 2024–2026 外部文献/工程博客/开源 repo 的二次评估证据（见文末「外部证据」与各条目内联标注）。

## Context（为什么做这个）

上一份对比报告把系列文章的 8 维 Harness 框架对照了三个外部 Agent。本报告把同一框架掉转枪口对准 Expert Work 自身：**在多租户、server-side、DB-中心的形态约束下，Expert Work 哪些该补、哪些该增强、哪些不该照搬**。v2 又用一轮外部调研（论文 + 一手工程博客 + 开源 repo）对每条结论做了交叉印证与修正。

### 已确认的总基调（2026-06-09 拍板）

采纳 **「② 混合：DB 为真相源 + workspace 文件投影」**，深化全部 A/B/C 档为 STREAM 级实施计划。

关键支点：Expert Work 是 **DB-中心 + 状态内化**（checkpointer 存 state、长期记忆进 `memory_item` 表），最接近 deer-flow——**但 Expert Work 已有 per-user 持久工作区**（`user_workspace` 表 + docker named volume + 每日备份 + 90 天 archive，热沙盒挂载 `/workspace`）。这让"状态投影成 workspace 文件"几乎零额外基建：

- **DB 仍是权威真相源**：保多租户查询、per-user RLS、可检索性、可扩展（不动 STREAM-J §8 结论）。
- **投影成 `/workspace/PLAN.md`、`TODO.md`、`MEMORY.md`**：agent 可文件 IO 自管状态、人可手改——拿到"透明/可干预/read_file 唤醒"红利。
- **同步模型（v2 已修正）**：**不做对称双向同步**，改 **单向错时双流**——turn 末 DB→file 只读投影，turn 始 file→DB 受控 ingest（详见 C0）。

> 服务核心产品形态：per-user 持久 agent = 对话 + 长期记忆 + **持久工作区**。文件投影让"持久工作区"从纯存储变成 agent 的活动状态面。

---

## Expert Work 现状速览（10 维 × 判定，带依据）

| 维度 | 判定 | Expert Work 现状关键事实 |
|---|:---:|---|
| 1. Session 隔离/历史存储 | ✅ 强 | `thread_meta` 表 + LangGraph checkpointer（Postgres）+ tenant/user 双层 RLS + session 状态机 |
| 2. Working Memory 截取 | 🟡 弱 | 有 token 估算(len//4)，但**无"保留最近 N 轮"滑窗**，完全依赖 compressor 被动压缩 |
| 3. Context Compaction | ✅ 有 | `ContextCompressor`：head_keep4/tail_keep6 + LLM 摘要中段 + 3-pass + `ContextOverflowError` 显式失败；`<context-summary>` 包裹；cache-prefix 友好 |
| 4. Observation Masking | ✅ 有 | 每工具 char cap + `meta["truncated"]` flag + tail/head/middle-trim 多策略；**但无"超大→落盘+虚拟引用"**，也无远期工具结果占位掩码 |
| 5. 状态外部化(文件) | ❌（刻意内化） | Plan 进 `AgentState.plan`，改状态走 `update_plan` 工具；无 PLAN.md/MEMORY.md 文件、无 bootstrap 扫描 |
| 6. 多层记忆/Hybrid 检索 | ✅ 强 | `memory_item` + pgvector HNSW + full-text tsvector + **RRF 融合**；consolidator(transient→consolidated→archived)；threat scan |
| 6b. 检索质量增强项 | 🟡 | RRF 对称权重，**无 MMR 去冗余、无时间衰减**；rerank_provider/model **配置预留但代码未接通** |
| 7. 整体架构 | DB-中心+内化 | checkpointer 存 state；记忆表独立检索；无长驻 instance；空闲零成本 |
| 8. Plan Mode/Thinking/算力 | 🟡 弱 | **无 plan mode 开关、无 thinking level、无动态算力分配**；iteration budget 仅设计概念 |
| 9. Error-as-guidance | 🟡 半 | **SE-12 是离线 skill 进化失败归因（control-plane），非主对话循环**；运行时只有 L-4 `<mutation-advisory>`（仅文件变更）；**通用工具失败→恢复建议注入主循环缺失** |
| 10. 可观测/人机协同 | 🟡 半 | approval 框架(DTO+state+audit)就绪、UI 骨架在；但"人工改 proposed_args 再提交""实时 trace timeline"在做 |

**一句话定位**：Expert Work 在 **记忆检索层（维度 6）已超过文章理想**（RRF Hybrid + consolidation + threat scan），但在 **运行时上下文控制（维度 2/8/9）偏薄**，且 **error-as-guidance 的"运行时主循环版"几乎空白**。

---

## 改进机会框架（每条含外部证据印证/修正）

### A 档 —— 真实能力缺口（应补，能力级；最高优先）

**A1. 运行时 Error-as-Guidance（通用工具失败→结构化恢复建议注入主循环）** · 优先级 **P0**
- 缺口：SE-12 是离线 control-plane 的 skill 进化失败归因；运行时主循环只有 L-4 mutation advisory（窄，仅文件变更）。"工具失败时按工具+错误类型注入带倾向性恢复建议、对抗模型瞎猜"在主对话 ReAct 循环里**没有**。
- 借鉴：Hermes `ClassifiedError` + recovery tool results 注入（`error_classifier.py:69-89`、`conversation_loop.py:3924-3949`）。
- Expert Work 落点：`services/orchestrator/src/orchestrator/graph_builder/builder.py` 的 `_after_tools()` / `tools_node`，泛化 L-4 `<mutation-advisory>` 注入范式到所有工具。
- **外部证据（强）**：Figma-to-code 实证（590 错误）——**结构化错误恢复率 >85% vs 模糊信号仅 17%**，整体自纠 70.3%，工具失败占全部错误 71%；ERR measure(2601.22352) 建可恢复性度量法则；Structured Reflection(2509.18847) 把"从 error 到 repair"做成可训练动作。
- **约束（v2 新增，重要）**：增益来自 **grounded** 注入——恢复建议必须**源自真实工具/执行信号**（错误码、stderr、schema 校验结果），**不能 LLM 凭空生成**。纯内省式 self-correction（Reflexion 式）已被 2024–2025 共识判为脆弱，会重复同一错误。

**A2. Working Memory 滑动窗口（轻量前置截断闸）** · 优先级 **P0**
- 缺口：完全依赖 compressor（调 LLM 的重武器），无"保留最近 N 轮"廉价前置闸；轻溢出每次都走 LLM 摘要。
- 借鉴：OpenClaw `limitHistoryTurns`（`history.ts:17-38`）。
- Expert Work 落点：`agent_node` 入口、compressor preflight 之前加廉价截断作第一道闸。**必须保 ToolCall↔ToolResult 配对**。
- **外部证据**：配对完整性被生产 bug 验证——OpenClaw #1084：压缩中途切断 tool-call 组 → 模型反复重试同一失败动作。不是理论洁癖。

**A3. Compaction 前抢救（pre-compaction memory flush）** · 优先级 **P1**
- 缺口：compressor 直接 LLM 摘要中段并丢弃，丢弃前无"把要点 flush 进长期记忆"的 hook；`memory_writeback` 只在 run 结束触发，compaction 在 run 中途。
- 借鉴：deer-flow `summarization_hook.py`（压缩前 flush）+ skill 文件抢救。
- Expert Work 落点：`context/compressor.py` 压缩前回调 → 复用 `memory_writeback` 通道。
- **外部证据**：Anthropic（memory tool + structured note-taking）+ OpenClaw（pre-compaction flush，softThreshold 50k 软 checkpoint）双重落地，已是**标准范式**。

### B 档 —— 已有能力的增强（质量级）

**B4. 检索质量：MMR 去冗余 + 时间衰减** · 优先级 **P1（叠加增益需自测）**
- 现状：对称 RRF，结果可能同质、不偏好新近。
- 借鉴：OpenClaw memory-search 的 MMR λ=0.7 + temporalDecay 半衰期 30 天（`memory-search.ts:80-117`）。
- Expert Work 落点：`packages/expert-work-persistence/.../memory/sql.py:retrieve()` 在 RRF 后加 MMR 重排 + 时间衰减加权。
- **外部证据 + 边界**：MMR（Carbonell & Goldstein 1998）、时间衰减（常用 30 天半衰期）、三因子（relevance+recency+importance/diversity，源自 Generative Agents）各自有据；但 **MMR+时间衰减+reranker 三者"叠加增益"业界无统一 benchmark**——须如实标注"组件级有据、叠加靠自测"。

**B5. Reranker 接通** · 优先级 **P1（v2 提级，原 P2）**
- 现状：`PlatformEmbeddingConfigService` 已管 `rerank_provider/model`（migration 0051），但检索路径**无 rerank 调用**。
- 落点：memory recall 的 Hybrid 召回后接 rerank。
- **外部证据（提级理由）**：cross-encoder reranker 是 RAG 末段**公认最高 ROI**——**+33~40% 准确率、平均仅 +120ms**，多跳查询 ROI 最强；`bge-reranker-v2-m3` 开源、多语言、零持续成本。Expert Work"配置都摆好就差接线"，性价比最高。

**B6. 超大工具结果外部化为 artifact + 虚拟引用（升为通用"可恢复压缩"原则）** · 优先级 **P1**
- 现状：超大工具结果是 char-cap **截断丢弃**，不可找回。
- 借鉴：deer-flow 落盘 + 虚拟引用 + `read_file` 豁免（`tool_output_budget_middleware.py`、`tool_output_config.py:55-58`）。
- Expert Work 优势：**已有 `artifact` 表 + ObjectStore，天然适配**；read 类工具豁免防 persist→read→persist 死循环。
- **外部证据（v2 升级为原则）**：Manus 核心法则"**压缩必须可恢复**——丢正文留 URL/sandbox 路径，缩上下文 0 永久丢失"；Anthropic 已把 **tool result clearing** 作为正式 context-management 功能上线（最轻量最安全）。**所有有损压缩都应优先"留引用不丢源"**，而非纯截断。

**B7. Compaction 摘要语义强化 → 可演进为结构化 note + 显式操作** · 优先级 **P2**
- 现状：已用 `<context-summary>` 包裹，但缺"背景非指令"强语义，也无二次压缩增量更新。
- 借鉴：Hermes `SUMMARY_PREFIX`（`context_compressor.py:37-61`）+ 二次压缩更新前次摘要（:659-660）。
- **外部证据（v2 扩展）**：A-MEM(2502.12110) + Mem0(2504.19413) 验证 **结构化记忆 note + 显式操作(ADD/UPDATE/DELETE/NOOP)** 优于"递归 LLM 全文摘要"——**省 85~93% token、增量可更新、保真更好**。B7 可演进为结构化摘要条目 + 显式更新操作；Mem0 的 extract→update 决策可直接借鉴 consolidator。生产 bug 佐证："摘要里的 Active Task 被当待执行指令重跑"是真实问题，须把"历史背景"与"待办/进行中动作"分区标注。

### C 档 —— 形态性选择

**C0. 状态↔workspace 文件投影 + 单向错时同步（基础使能项，先行）**
- ② 混合基调的地基，C8 依赖它，也是维度 5 的真正补法。
- **同步模型（v2 修正——这是架构级修正）**：**不做对称双向同步**（外部证据明确判其为反模式：Oracle 工程文 + 跨系统同步成熟模式主张"单向 master→target + master 保权威"，对称双写需 trump rules/人工裁决，是已知脆弱点）。改 **单向错时双流**：
  - **turn 末：DB→file 投影** —— 把 `AgentState.plan`/todo/关键 `memory_item` 投影成 `/workspace/*.md`，**只读派生视图，可随时从 DB 重建**（path-addressable + compaction-stable 不变量）。
  - **turn 始：file→DB 受控 ingest** —— 把人/agent 对文件的编辑当一次**显式摄取事件**（读 diff → 校验 → 以 DB 为权威写入），**非连续自动 sync**。
  - 任一时刻只有单向流动，DB 恒为真相源，避免并发双写冲突；冲突只剩"ingest 时文件 vs DB 漂移"，用 DB 事务 + audit 兜底。
- 复用：`user_workspace` 卷 + 沙盒挂载 + `update_plan` 工具 + `memory_writeback` 通道。
- 现成参照：**LangChain Deep Agents `CompositeBackend`**（StateBackend 内存态 + FilesystemBackend/StoreBackend 持久态组合）——最接近"DB 真相源 + workspace 投影"的可借鉴骨架。

**C8. 状态可观测/人机协同：文件投影 + UI 双通道** · 优先级 **P2（依赖 C0）**
- **文件通道**：见 C0，人可手改 `/workspace/*.md`，turn 始受控 ingest 回灌。
- **UI 通道**：admin UI plan/todo 可视化 + 可编辑，补 J.8 approval `decision='modify'` 的 UX 缺口（`RunDetail.tsx`/`ApprovalCard.tsx` 当前无法编辑 `proposed_args` 再提交）。
- 两通道共享 C0 的 ingest 路径，最终都以 DB 为权威落点。

**C9. Plan Mode + 动态算力（v2 接口已更新）** · 优先级 **P2**
- 现状：无 plan mode 开关、无 thinking level、iteration budget 仅概念。
- **接口更新（v2 必改）**：不要写"thinking budget / `budget_tokens`"——Anthropic **Opus/Sonnet 4.6 起 `budget_tokens` 已弃用**，改为 **adaptive thinking + `effort`（low/medium/high/max）+ interleaved thinking（工具调用间思考，agentic 必备）**。C9 应重命名为"**effort 档位 + 异常驱动升档**"。
- **动态升档有支撑（v2 修正"未来式"判断）**：AdaCtrl(2505.18822)、**Ares: agent 级 per-step effort 选择(2603.07915)**、e1(2510.27042) 三篇直接对口"按难度/异常动态分配算力"，已有可参照路线，不必当纯未来式。
- plan-execute 须带 **replan 回路**（ADaPT 按需分解，防"任一子任务失败整体崩"）；executor 步可用更便宜模型省成本。
- 建议两步：先静态 effort 档位 + 显式 plan mode 开关 + iteration budget 真实现，动态升档随后。

### 新增机会（v2，外部证据揭示原报告漏项）

**N1. Recitation（复诵）对抗注意力衰减** · 优先级 **P1（并入 C0）**
- Manus + Claude Code 事实标准：把 `todo.md`/plan 持续**重写到上下文末尾**，把全局目标推进近期注意力焦点，缓解 long-context "lost-in-the-middle"。
- 对 Expert Work：C0 文件投影不应只"被动落盘"，应配**主动复诵**——每 turn 把当前 PLAN/TODO 摘要注入上下文尾部。低成本高收益。

**N4. Loop detection（调用指纹去重）** · 优先级 **P1（并入 C9/运行循环）**
- 工程共识：除 max-iteration 硬上限外，**同工具同参数连续 N 次即强制终止** 是最实用的死循环防护；配 token 预算累计早停；**撞限当信号触发升档**（plan mode/更高 effort）而非简单失败退出。
- 对 Expert Work：显式补调用指纹去重 + 撞限升档联动。

**N5. 评测基线（纪律，贯穿所有条目）**
- 业界标准长期记忆评测：**LongMemEval**（信息抽取/多会话/时序/知识更新/拒答）+ **LoCoMo**（极长多会话）。
- **关键教训**：厂商自报数字不可横比——**Zep 自称 LoCoMo 84%，独立复核修正到 58.4%**（getzep#5）。
- 对 Expert Work：用 LongMemEval+LoCoMo 给"pgvector+RRF+reranker+consolidator"出**自测数字**作回归基线，每个检索/记忆改动（B4/B5/B7）都以此验证，别信任何单一厂商数字。

---

## 明确"不建议照搬"（架构错配，避免反向退化）

- **文件作为唯一真相源（file-first，基调 ③）**：与多租户查询/隔离/可检索冲突。**已弃选**；采纳 ②混合（DB 权威 + 文件单向投影）。
- **对称双向同步 / 最后写赢**（v2 新增到此节）：外部证据判为反模式；改 C0 的"单向错时双流 + 受控 ingest"。
- **进程级文件锁 + PID watchdog**（OpenClaw）：Expert Work 用 Postgres 事务 / advisory lock 解决并发，不引入文件锁。
- **Dreaming / 按日期 Episodic 编年落盘**：文章理想化、三家都没真做；consolidator(transient→consolidated)已覆盖等价价值。
- **知识图谱记忆（Zep/Graphiti）/ A-MEM 全量 / 可学习压缩 token（ICAE/KV-Distill）/ 推理层 KV-cache 压缩**：见「前沿暂不纳入」。

---

## 修订后的优先级（融合外部证据）

| 条目 | 优先级 | 修订理由（外部证据） |
|---|:---:|---|
| **C0** 文件投影 + 单向错时同步 | 先行 | 对称双向是反模式；参照 Deep Agents CompositeBackend |
| **A1** 运行时 error-as-guidance | P0 | 85%/17% 硬数据；约束：注入须 grounded（源自真实工具/执行信号） |
| **A2** working memory 滑窗 | P0 | 生产 bug 验证配对完整性（OpenClaw #1084） |
| **B5** reranker 接通 | P1（提级） | +33~40%/+120ms 最高 ROI，配置已预留 |
| **N1** recitation 复诵 | P1（并入 C0） | Manus/Claude Code 标准，低成本抗 lost-in-the-middle |
| **N4** loop 指纹去重 | P1（并入 C9） | 最实用死循环防护 + 撞限升档 |
| **A3** 压缩前 flush | P1 | Anthropic/OpenClaw 标准范式 |
| **B6** 超大结果 artifact 化 | P1（升通用"可恢复压缩"原则） | Manus 法则 + Anthropic tool-result-clearing |
| **B4** MMR+时间衰减 | P1（叠加增益需自测） | 组件级有据、叠加无统一 benchmark |
| **B7** summary→结构化 note | P2 | A-MEM/Mem0 省 85~93% token，增量可更新 |
| **C8** 文件投影+UI 双通道 | P2 | 依赖 C0 |
| **C9** plan mode + effort 档位 | P2 | budget_tokens 已弃用→effort+interleaved；动态升档有 Ares 等支撑 |
| **N5** 评测基线 LongMemEval/LoCoMo | 贯穿 | 回归纪律，别信厂商数字 |

---

## 外部证据（2024–2026，二次评估）

### 被印证（报告做对了）
- 检索层 pgvector+全文+RRF 是 2025 业界事实标准（纯向量 ~62% → 加全文+RRF ~84%；RRF 仍是 hybrid 融合默认）。
- A1 error-as-guidance 有强量化证据（结构化恢复 >85% vs 模糊 17%）。
- A3 压缩前 flush、C0 文件投影、consolidator(对应 Letta sleep-time compute) 均对齐前沿。
- A2 配对完整性被生产 bug 验证。

### 可改进/修正（已吸收进上文条目）
- C9 接口过时（budget_tokens 弃用→effort+interleaved）。
- C0 对称双向同步是反模式→单向错时双流。
- B5 reranker 应提级（最高 ROI）。
- B7 可演进为结构化 note + 显式操作。
- B4 叠加增益需自测。

### 前沿暂不纳入（成熟度/ROI 待定）
- **知识图谱记忆（Zep/Graphiti bi-temporal KG）**：强时序/事实失效查询有优势，但构图成本高、收益被高估（84→58）；仅当 Expert Work 出现强时序/事实纠正需求再评估。
- **A-MEM 全量 Zettelkasten**：研究原型，生产成熟度低于 Mem0/Letta；其"结构化 note+显式操作"思想已被 B7/N2 局部吸收。
- **可学习压缩 token（ICAE/KV-Distill）/ 硬 prompt 压缩（LLMLingua-2）**：前者需训练；后者可作"LLM 摘要"低成本替代候选，非首选。
- **KV-cache 压缩（PyramidKV 等）**：属推理层，与应用层上下文管理正交，不在本框架范围。

### 关键引用
- 一手工程博客：Anthropic context engineering / adaptive thinking docs；Manus context engineering（rlancemartin 复述）；Cognition "Don't Build Multi-Agents"；LangChain Deep Agents filesystems / Planning Agents；Oracle file-vs-DB agent memory。
- 论文：A-MEM(2502.12110)、Mem0(2504.19413)、Zep/Graphiti(2501.13956)、CoALA(2309.02427)、Reflexion(NeurIPS'23)、Structured Reflection(2509.18847)、ERR measure(2601.22352)、Ares(2603.07915)/AdaCtrl(2505.18822)/e1(2510.27042)、Letta sleep-time compute、LLMLingua-2(2403.12968)、Context-Length-Hurts(2510.05381)。
- 评测：LongMemEval、LoCoMo（含 Zep 84%→58% 争议 getzep#5）。
- 生产 bug 佐证：OpenClaw #1084（压缩切断 tool-call 组）。

> 取证说明：本轮 web 调研用多关键词 WebSearch + 多源交叉印证（环境 WebFetch/浏览器不可用，未单页深读）。部分 2026 年 arXiv 编号属环境时间设定产物，仅采信能交叉印证者；纯单条"未来号"支撑的细节未纳入硬结论。

---

## 下一步

按修订优先级，对选定条目分别出 STREAM 级设计（C0 地基先行，再 A 档 P0，再 B/C/N）。每条 STREAM 设计含：现状接缝、改动点 file:line、数据/协议变更、测试与验证（含 LongMemEval/LoCoMo 自测）、与既有 Stream(J/L/SE)的衔接。
