# 研究报告：OpenClaw / deer-flow / Hermes 的「上下文与记忆管理机制」对比

> 日期：2026-06-09 · 取证范围：`src/github/{openclaw, deer-flow, hermes-agent}` · 评测框架：Harness 工程系列文章的 8 维上下文/记忆管理模型

## Context（为什么做这个研究）

系列文章《揭秘 Agent Skills / Harness 工程》提出了一套「驾驭工程」的上下文与记忆管理框架，核心论点是：

- **Context Window = 昂贵且容量受限的 RAM**，防 OOM 优先于业务逻辑；
- 压缩要 **阶梯降级（Staged Degradation）**：丢冗余、保意图与逻辑链（绝不制造 ToolCall↔ToolResult 断层）；
- 具体手段是 **Observation Masking（远期工具结果掩码）+ Head-Tail Truncation（掐头去尾）**；
- 状态应 **外部化到文件系统**（PLAN.md / TODO.md / MEMORY.md），换取透明、可干预、断电持久化；
- 记忆是 **多层体系**（Working / State / Episodic / Long-term）+ Hybrid（向量+BM25）检索；
- 错误处理要做成 **「行动指南」**（error-as-guidance），而非裸 Error Log。

本报告把这套框架当成评测维度，对 `src/github` 下三个真实开源 Agent 做了源码级取证，看它们各自如何落地、印证了文章哪些观点、又在哪里偏离。**本报告只研究这三个外部项目，不涉及 expert-work 自身实现**（末尾仅附一节简短启示）。

### 三个项目的定位差异（决定了它们的架构取向）

| 项目 | 语言/路径 | 形态 | 内核 |
|---|---|---|---|
| **OpenClaw** | TS · `src/github/openclaw` | 多端 IM 接入的编码 Agent（类 Claude Code） | `@mariozechner/pi-coding-agent` 内核 + 文件系统优先 |
| **deer-flow** | Python · `src/github/deer-flow/backend/packages/harness/deerflow` | LangGraph 深度研究 Agent（lead + subagents） | LangGraph + Middleware 管线 + Checkpointer |
| **Hermes** | Python · `src/github/hermes-agent/agent` | 单机 CLI + IM 的 ReAct/编码 Agent | SQLite 中心化（state.db 即真相源） |

一句话概括三种哲学：**OpenClaw = 文件系统优先 + 进程文件锁**；**deer-flow = 框架化中间件管线 + checkpointer**；**Hermes = SQLite 中心化（DB-as-truth）**。

---

## 逐维度对比

### 维度 1：Session 隔离与历史存储

| | OpenClaw | deer-flow | Hermes |
|---|---|---|---|
| **隔离单元** | session key `agent:<id>:<provider>:<kind>:<userId>[:<threadId>]`（UUID-v4） | LangGraph `thread_id`（runtime context → configurable 回退） | `session_id`（TEXT PK），带 `source` 来源标记 |
| **历史存储** | `~/.openclaw/agents/<id>/sessions/<sid>.jsonl`（JSONL，0o600 私密权限）+ `sessions.json` 元数据 | Checkpointer：`memory` / `sqlite`（`.deer-flow/checkpoints.db`）/ `postgres` 三选一 | SQLite `messages` 表 + FTS5（全词 + CJK trigram） |
| **并发安全** | 文件系统级写锁 `session-write-lock.ts`：60s acquire timeout、5min watchdog、PID 活性检测 | LangGraph checkpointer 自带 | `threading.Lock` + WAL 模式 + 应用级重试（15 次，20–150ms 抖动） |
| **重启恢复** | ✅ JSONL 落盘 | ✅（sqlite/postgres） | ✅ state.db |

**关键引用**：
- OpenClaw：`src/config/sessions/transcript.ts:1-50`（`ensureSessionHeader`、JSONL 写入）、`src/agents/session-write-lock.ts:155-174`（watchdog/PID 检测）、`src/sessions/session-id.ts:1-5`
- deer-flow：`config/checkpointer_config.py:7-26`、`runtime/checkpointer/provider.py:105-148`（`get_checkpointer` 单例）、`agents/thread_state.py:87-96`（ThreadState 根状态）
- Hermes：`hermes_state.py:376-460`（`SessionDB`）、`:241`（`parent_session_id` 会话链）、`:320-372`（FTS5 虚表）

**对照小结**：
- 文章说"成熟引擎用 .json/.jsonl 落盘到工作区隐藏目录" —— **OpenClaw 几乎逐字命中**（`~/.openclaw/.../*.jsonl`）。
- 文章说 SessionManager 按来源（终端目录哈希/ChatID/OpenID）寻址 —— **三家都做了来源寻址**，OpenClaw 编码进 session key，Hermes 用 `source` 字段，deer-flow 用 `thread_id`。
- 文章用 `sync.RWMutex` 做并发 —— **三家是三条不同路线**：OpenClaw 进程级文件锁（适配多进程）、Hermes 线程锁 + WAL（适配单进程多线程）、deer-flow 交给框架。**最硬核的是 OpenClaw 的文件锁 watchdog + PID 检测**，这正是分布式/多端场景下 `RWMutex` 不够用的工程补强。
- **Hermes 独有的 `parent_session_id` 会话链**很值得注意：压缩后另起新 session 并指回父会话，把"压缩前后"显式建模成可追溯的链，比单纯覆盖历史更可观测。

---

### 维度 2：Working Memory 截取（上下文窗口管理）

| | OpenClaw | deer-flow | Hermes |
|---|---|---|---|
| **截取策略** | 按最近 N 个 **user 轮次**截取（保 user+配对 assistant） | Token/消息/比例三种触发器 + token 预算注入 | 拷贝 history → 预检 token 水位线 |
| **Token 计数** | `context-window-guard.ts` 多源解析窗口大小 + reserve | tiktoken（带字符回退） | `estimate_request_tokens_rough`（4 char≈1 tok，图片≈1500 tok） |
| **水位线** | reserve floor 20K token | `fraction=0.8`（80% model max） | 默认 50% 高水位线，最小 ~64K |
| **配置粒度** | per-DM / per-channel `historyLimit` 覆盖 | `SummarizationConfig` 三档触发 | `threshold_tokens` |

**关键引用**：
- OpenClaw：`src/agents/pi-embedded-runner/history.ts:17-38`（`limitHistoryTurns`）、`:45-118`（per-DM/channel 覆盖）、`src/agents/context-window-guard.ts:26`
- deer-flow：`config/summarization_config.py:21-73`、`agents/memory/prompt.py:163-183`（tiktoken 计数 + 预算化注入）、`agents/middlewares/dynamic_context_middleware.py:82-100`（注入冻结进首个 HumanMessage，利于 prefix cache）
- Hermes：`agent/model_metadata.py:1887-1908`、`agent/conversation_loop.py:603-651`（预检压缩）

**对照小结**：
- 文章说"截取最近 N 轮作为 Working Memory" —— **OpenClaw 最贴合**（显式按 user 轮次截取），deer-flow/Hermes 是"按 token 水位线触发压缩"而非硬截取，本质相同但 OpenClaw 更接近文章描述。
- **三家都引入了 token 预算/水位线**（文章只提到防 400，没细化阈值）；实测阈值：deer-flow 80%、Hermes 50%、OpenClaw reserve 20K floor。**Hermes/OpenClaw 都做了"预检主动压缩"**而非等 API 报 context-window-exceeded 才被动救火 —— 这是文章"物理防御优先"理念的工程升级。
- **deer-flow 的一个 cache 优化值得单列**：把记忆/日期注入"冻结"进首个 HumanMessage，保证 prefix 不变以复用 prompt cache（`dynamic_context_middleware.py`）。Hermes 也有同样意识（记忆只在会话开始注入，中途改文件不更新 system prompt 以稳住 prefix cache）。

---

### 维度 3：Context Compaction（上下文压缩）

| | OpenClaw | deer-flow | Hermes |
|---|---|---|---|
| **压缩方式** | **LLM 摘要式**（可配独立压缩模型） | **LLM 摘要式**（abstractive） | **混合**：先字符级预清理工具结果，再 LLM 摘要中段 |
| **触发** | `budget`/`overflow`/`manual` | messages/tokens/fraction | ≥ 阈值 token |
| **保留策略** | reserve token + keepRecentTokens | `keep` 策略 + 跳过工具调用 | 头尾保护 + 中段摘要 |
| **压缩前抢救** | before/after compaction hooks | **memory flush hook**（摘要丢弃消息前先落记忆）+ skill 文件保留 | 失败时静态回退摘要 |

**关键引用**：
- OpenClaw：`src/agents/pi-embedded-runner/compact.ts:415-456`、`pi-settings.ts:8`（reserve floor 20K）、`compaction-hooks.ts`（before/after + `rotateTranscriptAfterCompaction`）
- deer-flow：`agents/middlewares/summarization_middleware.py:126-150`、`agents/memory/summarization_hook.py`（**压缩前 flush 记忆**）、`:88-119`（skill 文件抢救：默认保最近 5 个 / 25K token）
- Hermes：`agent/context_compressor.py:522`（`ContextCompressor`）、`:1827`（`compress`）、`:37-61`（SUMMARY_PREFIX 告知模型"背景参考非指令"）、`:1001-1069`（静态回退摘要）、`:659-660`（二次压缩时更新前次摘要而非重生成）

**对照小结**：
- ⚠️ **重要偏离**：文章明确说"go-tiny-claw 不引入另一个大模型做对话摘要，只用字符级截断"。但**三个真实生产级项目全部采用了 LLM 摘要式压缩**作为主力。文章把 LLM summary 列为"工业界前沿做法之一（成本/延迟代价）"，现实是它已经是默认方案，字符截断只作为工具结果层的辅助。**这说明文章为教学极简做的取舍，与生产实践存在系统性差异**。
- **三家都意识到"压缩 = 信息有损"并各自做了抢救**，这是文章没充分展开的工程深水区：
  - deer-flow：**压缩前先触发 memory flush hook**，把会话要点落进长期记忆再丢消息 —— 几乎等同文章里描述的 OpenClaw "Compaction 前静默轮次落盘"！
  - deer-flow：**skill 文件抢救**（最近加载的技能文件不被摘要吞掉）。
  - Hermes：**摘要失败有确定性静态回退**（拼最近问题/动作/文件/命令/错误日志），保证压缩永不"开天窗"；并在 ≥2 次压缩时**警告用户考虑 /new**。
  - Hermes：**二次压缩更新前次摘要**而非重新生成，保上下文连贯。
- Hermes 的 **SUMMARY_PREFIX** 直接呼应文章 image 10 里"OpenClaw 用前缀告知模型压缩内容是背景参考非主动指令"，防止模型把历史摘要当新指令重复执行。

---

### 维度 4：Observation Masking / Head-Tail Truncation（工具结果掩码与截断）

| | OpenClaw | deer-flow | Hermes |
|---|---|---|---|
| **单条工具结果上限** | `DEFAULT_MAX_LIVE_TOOL_RESULT_CHARS=16K`，动态 = 30% context window | `externalize_min_chars=12K` 触发落盘 | `_CONTENT_MAX=6000` |
| **Head-Tail** | head-tail 截断，最小保 2K 字符，**尾部错误关键词检测** | preview = head 2K + tail 1K + 虚拟文件引用 | head 4000 + tail 1500，中插 `...[truncated]...` |
| **掩码占位符** | 聚合替换 | 落盘 `/mnt/.../.tool-results/{tool}-{uuid}` + 占位预览 | `[Old tool output cleared to save context space]` |
| **防循环** | — | **`read_file` 豁免**（防 persist→read→persist 死循环） | — |
| **多模态** | — | — | 截图 → `[screenshot removed] {summary[:200]}` |

**关键引用**：
- OpenClaw：`src/agents/pi-embedded-runner/tool-result-truncation.ts:139`（`truncateToolResultText`）、`:119-129`（尾部 error/exception/panic/JSON `}` 检测）、`:207`（`calculateMaxToolResultChars` = 30% 窗口）、`:376`（聚合按大小成比例缩减）
- deer-flow：`agents/middlewares/tool_output_budget_middleware.py:97-150`、`config/tool_output_config.py:36-50`（回退 30K=head8K+tail3K）、`:55-58`（`read_file` 豁免）
- Hermes：`agent/context_compressor.py:940-942`（6000/4000/1500）、`:965-966`（截断逻辑）、`:875`（截图占位）、`:92`（PRUNED 占位符）

**对照小结**：
- ✅ **这是文章观点印证最充分的维度**。Head-Tail Truncation 三家**全部命中**，且都遵循文章"开头说错因、结尾带堆栈，中间可弃"的直觉。具体参数：OpenClaw head-tail（保 2K）、deer-flow head2K+tail1K、Hermes head4K+tail1.5K。文章给的是"前500+后500"，三家实际保留量都更大。
- ✅ **远期工具结果掩码替换为占位符**三家也都有（文章描述的 `[早期工具输出已清理，原始长度 X]` 几乎是 Hermes `_PRUNED_TOOL_PLACEHOLDER` 的翻版）。
- **两个文章没提但生产必备的工程点**：
  1. **OpenClaw 的"尾部重要性检测"**：截断前先扫尾部 2K 是否含 error/exception/panic/JSON 闭合，命中就调整保留策略 —— 直接回应文章自己点出的局限性"核心堆栈恰在中间会丢线索"。
  2. **deer-flow 的"落盘 + 虚拟文件引用"**：超限工具结果不是丢掉而是写盘并在上下文留一个可被 `read_file` 换入的路径 —— 这是文章 image 5 里 "Memory Paging（换入换出）" 思想在工具结果层的落地，比纯掩码更优；并配套 `read_file` 豁免防死循环。
- **动态化程度**：OpenClaw 把上限做成"context window 的 30%"动态值（窗口越大留得越多），比 Hermes/deer-flow 的固定字符数更自适应。

---

### 维度 5：状态外部化（File-based Memory）

| | OpenClaw | deer-flow | Hermes |
|---|---|---|---|
| **外部状态文件** | `MEMORY.md`（工作区根）+ bootstrap 文件（PLAN.md/README 等扫描注入） | **不写文件**：Plan/Todo 存 ThreadState（checkpointer） | `~/.hermes/memories/MEMORY.md` + `USER.md`；工作区 `.hermes.md`/`AGENTS.md`/`CLAUDE.md`/`.cursorrules` |
| **Plan/Todo** | bootstrap 注入 system prompt | `TodoMiddleware`（仅 plan mode 启用），`todos` 带 `merge_todos` reducer | 文件型记忆 + 上下文文件 |
| **Human-in-loop** | 手改工作区文件，下轮重读 | 改 state（write_todos 工具） | 手改 MEMORY.md/USER.md，但中途不刷 prefix |
| **大小约束** | bootstrap maxChars 限制 | `max_facts=100` | MEMORY.md 2200 / USER.md 1375 字符 |

**关键引用**：
- OpenClaw：`src/memory/root-memory-files.ts:4`（`CANONICAL_ROOT_MEMORY_FILENAME="MEMORY.md"`）、`src/agents/bootstrap-files.ts`（`resolveBootstrapContextForRun`，扫描工作区及父目录 PLAN.md/MEMORY.md/README.md）
- deer-flow：`agents/thread_state.py:92`（`todos` + `merge_todos`）、`agents/lead_agent/agent.py:149-261`（`_create_todo_list_middleware(is_plan_mode)`）
- Hermes：`tools/memory_tool.py:55-57`（`~/.hermes/memories/`）、`agent/prompt_builder.py:1517-1522`（上下文文件发现顺序 `.hermes.md`→`AGENTS.md`→`CLAUDE.md`→`.cursorrules`）、`:1524`（每文件 20K 字符截断）

**对照小结**：
- ⚠️ **三家在"状态外部化"上分成两派，恰好对应文章的核心争论**：
  - **OpenClaw + Hermes = 文件系统派**（文章推崇的方向）。OpenClaw 的 `MEMORY.md` + bootstrap 扫描、Hermes 的 `MEMORY.md`/`USER.md` + `AGENTS.md`/`CLAUDE.md` 发现链，**几乎就是文章"状态外部化到肉眼可见 Markdown"的实现**。Hermes 还兼容 Claude/Cursor 生态的上下文文件，可移植性最强。
  - **deer-flow = 状态内化派**（文章批评的"藏在框架里"方向）。它的 Plan/Todo 存在 LangGraph ThreadState 里、靠 checkpointer 持久化，**不落地成人类可编辑的文件**。human-in-the-loop 要走 `write_todos` 工具而非直接改文本。这正是文章说的"黑盒内部状态，人类无法直观查看/编辑"—— 但 deer-flow 用 LangGraph Studio 之类可视化来补偿。
- 文章强调的"零成本人机协同（手改 PLAN.md）"在 **OpenClaw/Hermes 上成立，在 deer-flow 上不成立**。
- **共同的工程纪律：文件型记忆都加了硬字符上限**（Hermes 2200/1375、OpenClaw bootstrap maxChars、deer-flow max_facts=100），防止外部记忆无限膨胀反噬上下文 —— 文章没强调这点，但它是文件记忆能用的前提。
- **Hermes 的安全增强**：记忆内容注入前扫 injection/exfil 模式，命中条目不进 system prompt 但保留原文供用户审查 —— 外部记忆带来的 prompt injection 攻击面，是文章没覆盖的真实风险。

---

### 维度 6：多层记忆 / Episodic / 长期检索

| | OpenClaw | deer-flow | Hermes |
|---|---|---|---|
| **长期记忆** | `memory-search.ts`：SQLite + sqlite-vec | 文件型 JSON：三层结构 + facts（带 confidence） | 文件型 MEMORY.md/USER.md + 可插拔 provider |
| **检索方式** | **Hybrid**：向量 0.7 + BM25/FTS 0.3，MMR 重排 λ=0.7，时间衰减半衰期 30 天 | **无向量/无检索**：按 confidence 排序 token 预算化注入 | 内置无向量；honcho/mem0/supermemory 插件提供 |
| **Embedding** | 本地或远程（OpenAI/Gemini/Ollama），支持批处理 | 无 | 取决于插件 |
| **Episodic/Dreaming** | ❌ 未找到 Dreaming/按日期落盘 | ❌（但有压缩前 memory flush，近似情景沉淀） | ❌ 未找到 |
| **同步触发** | onSessionStart / onSearch / watch，增量 deltaBytes 100K | debounce 队列（默认 30s 批处理） | 压缩时 `on_session_switch` 通知 provider |

**关键引用**：
- OpenClaw：`src/agents/memory-search.ts:80-117`（hybrid 权重 0.7/0.3、MMR λ=0.7、temporalDecay halfLife 30 天）、`:106-107`（增量同步阈值）
- deer-flow：`agents/memory/storage.py:24-40`（三层结构）、`agents/memory/updater.py:644-683`（facts + confidence，max_facts=100）、`agents/memory/queue.py:28-288`（debounce 队列）
- Hermes：`agent/agent_init.py:1108-1164`（`load_memory_provider`，honcho/mem0/supermemory）、`conversation_compression.py:563-567`（压缩时 `on_session_switch`）

**对照小结**：
- ✅ **OpenClaw 是唯一完整实现文章"Hybrid Retrieval（向量+BM25 合并排序）"的**，甚至更进一步加了 MMR 去冗余重排 + 时间衰减（文章没提的两个高级特性）。文章 image 10 描述的"向量搜语义 + BM25 搜精确 ID/错误串/配置键名" —— OpenClaw `memory-search.ts` 的 0.7/0.3 加权正是它。
- **deer-flow 的长期记忆是"结构化事实库"而非检索库**：三层（用户上下文/历史/长期背景）+ 带 confidence 的 facts，靠 token 预算化注入而非按需检索。这是另一种思路 —— **用"全量精选注入"替代"按需大海捞针"**，适合记忆量可控的场景。
- **三家都没有文章描述的 "Dreaming（短期信号评分晋升长期）" 和 "按日期 2026-04-12.md 落盘的 Episodic Memory"** —— 这两个是文章把 OpenClaw 理想化/超前描述的部分，至少在当前 `src/github/openclaw` 树里没找到对应实现。deer-flow 的"压缩前 memory flush hook"是最接近"静默轮次落盘"的近似物。
- **Hermes 走插件化**：核心不内置检索，把长期记忆外包给 honcho/mem0/supermemory 等专业记忆中间件 —— 这是"不重造记忆轮子"的产品决策。

---

### 维度 7：Plan Mode / Thinking 慢思考

| | OpenClaw | deer-flow | Hermes |
|---|---|---|---|
| **Plan Mode** | 通过 thinking level + provider profile，识别 `EnterPlanMode`/`ExitPlanMode` 工具 | `is_plan_mode` 运行时参数 → 启用 TodoMiddleware + subagent 编排 | ❌ 无专门 Plan Mode |
| **Thinking 控制** | level：off/low/medium/high/xhigh，动态注入 extra params | 无独立 thinking 开关 | OpenRouter `reasoning_config`（effort none/medium） |
| **算力分配** | 按 provider profile 动态 | 按 subagent 并发（默认 3）拆分 | **IterationBudget**：父 90 / 子 50 次迭代，consume/refund + grace call |
| **慢思考触发** | thinking level 静态档位 | plan mode 显式开关 | 迭代预算耗尽通知 |

**关键引用**：
- OpenClaw：`src/auto-reply/thinking.ts:4-7`（5 档 level）、`src/agents/anthropic-transport-stream.ts`（`EnterPlanMode`/`ExitPlanMode` 工具）、`compact.ts:484`（`thinkLevel` 透传）
- deer-flow：`agents/lead_agent/agent.py:149-261`（plan mode 启用 TodoMiddleware）、`:754`（`max_concurrent_subagents=3`）、`subagents/executor.py:60-127`
- Hermes：`agent/iteration_budget.py:17-62`（`IterationBudget` 父子共享，consume/refund）、`agent/agent_init.py:462`（`reasoning_config`）、`:499`（grace call）

**对照小结**：
- 文章 image 8/9 的核心论点："Plan Mode 是可选开关（避免简单命令也走官僚流程）"+"Plan Mode（宏观导航）≠ Thinking（微观纠偏）" —— **deer-flow 的 `is_plan_mode` 显式参数最贴合"可选开关"**，OpenClaw 的 `EnterPlanMode/ExitPlanMode` 工具则把开关交给模型自己决定（更接近 Claude Code）。
- 文章设想的"动态算力分配（宏观触发：PLAN.md 变更；微观触发：工具异常）" —— **三家都没做到这种"按信号动态开慢思考"**。最接近的是：
  - OpenClaw：thinking level 是**静态档位**（文章批评的"开关一开每轮都思考"问题依然存在）；
  - Hermes：`IterationBudget` 是另一个维度的"算力上限"控制（防 ReAct 死循环），父 90/子 50 次，带 refund 和 grace call —— 这是文章没提但实战非常关键的"迭代预算"机制。
- **结论：文章描述的"动态 thinking budget 按触发条件分配"在三家都还是未来式**，现实里要么静态档位（OpenClaw）、要么显式开关（deer-flow）、要么管迭代次数而非 thinking（Hermes）。

---

### 维度 8：错误即行动指南（error-as-guidance）

| | OpenClaw | deer-flow | Hermes |
|---|---|---|---|
| **错误结构化** | `ToolErrorSummary`（toolName/timedOut/mutatingAction/fileTarget） | `ClassifiedError`：transient/quota/auth | `ClassifiedError`：auth/billing/rate_limit/context_overflow/format_error... |
| **恢复建议注入** | 警告 payload `⚠️ {tool} failed`，**无强倾向性建议** | retry 3 次 + 指数退避 + **熔断器**（closed/open/half-open） | **结构化恢复标志** retryable/should_compress/should_rotate_credential/should_fallback |
| **工具失败处理** | guard wrapper 包装错误日志 | 转 error ToolMessage（限 500 字符）让 run 继续 | **注入 recovery tool results 让模型自纠正** |
| **失败分类** | `isExecLikeToolName` 区分 exec/bash | 纠正信号检测 → 写记忆（confidence≥0.95） | 多模式：billing/rate_limit/context_overflow/image_too_large |

**关键引用**：
- OpenClaw：`src/agents/tool-error-summary.ts:1-19`、`src/agents/pi-embedded-runner/run/payloads.ts`（warning payload）、`failure-signal.ts:16`
- deer-flow：`agents/middlewares/llm_error_handling_middleware.py:66-160`（熔断器三态机 + 3 次重试）、`agents/middlewares/tool_error_handling_middleware.py:21-68`（错误转 ToolMessage 续跑）、`agents/memory/updater.py:397-420`（纠正→记忆）
- Hermes：`agent/error_classifier.py:69-89`（`ClassifiedError` + 恢复标志）、`agent/conversation_loop.py:3924-3949`（**为格式错误工具调用注入 recovery tool results 让模型同轮重试**）

**对照小结**：
- 文章 image 11 论点："工具失败仅返回原始 Error Log 不够，必须基于工具+错误类型注入带强烈倾向性的恢复建议（对抗模型瞎猜的最小阻力路径）"。**三家的实现程度差异很大**：
  - **Hermes 最强**：`ClassifiedError` 把错误映射成结构化恢复标志（`should_compress`/`should_rotate_credential`/`should_fallback`），并在工具调用失败时**主动注入 recovery tool results 让模型在同一轮自纠正**（保 role 交替）—— 这正是文章说的"注入倾向性恢复建议"。
  - **deer-flow 偏系统韧性**：熔断器 + 指数退避 + 错误转 ToolMessage 续跑，关注"不让单点错误拖垮整个 run"，但给模型的不是"该怎么改"的语义建议。它还有个亮点：**检测到用户纠正信号就写进长期记忆**（confidence≥0.95），把"纠错"沉淀为长期偏好。
  - **OpenClaw 最弱**：`ToolErrorSummary` 结构化了错误元数据，但取证显示**没有注入额外倾向性恢复建议**，只是包装原始日志为警告 payload。这一维度上 OpenClaw 反而离文章理想最远。
- **印证**：文章的"error-as-guidance"理念被 Hermes 较完整落地，但 OpenClaw/deer-flow 说明现实里更多停留在"错误分类 + 重试/熔断"层面，"语义恢复建议"仍是少数派。

---

## 横向对比矩阵（命中文章框架的程度）

图例：✅ 完整落地 · 🟡 部分/变体 · ❌ 缺失（取证未发现）

| 维度（文章框架） | OpenClaw | deer-flow | Hermes |
|---|:---:|:---:|:---:|
| 1. Session 隔离 + .jsonl 落盘 | ✅ JSONL+文件锁 | 🟡 checkpointer | ✅ SQLite+FTS+会话链 |
| 2. Working Memory 截取 | ✅ 按 user 轮次 | 🟡 token 水位线 | 🟡 token 水位线+预检 |
| 3. Compaction | 🟡 LLM 摘要 | 🟡 LLM 摘要+压缩前 flush | ✅ 混合+静态回退 |
| 4. Masking + Head-Tail | ✅ +尾部检测+动态30% | ✅ +落盘换入+防循环 | ✅ +多模态掩码 |
| 5. 状态外部化(文件) | ✅ MEMORY.md+bootstrap | ❌ 内化进 ThreadState | ✅ MEMORY.md/USER.md+多生态 |
| 6. 多层记忆+Hybrid 检索 | ✅ 向量+BM25+MMR+衰减 | 🟡 结构化事实库(无检索) | 🟡 插件化(honcho/mem0) |
| 7. Plan Mode + 动态 thinking | 🟡 模型决定+静态档位 | 🟡 显式开关 | 🟡 迭代预算(非 thinking) |
| 8. Error-as-guidance | ❌ 仅日志包装 | 🟡 熔断+重试 | ✅ 结构化恢复+自纠正注入 |
| Episodic/Dreaming（文章理想） | ❌ | 🟡 压缩前 flush 近似 | ❌ |

**一句话画像**：
- **OpenClaw** —— 最贴近文章"文件系统 + Hybrid 检索"理想的项目；强在 session 隔离（进程锁）和长期记忆检索（向量+BM25+MMR+时间衰减），弱在 error-as-guidance。
- **deer-flow** —— 框架化（LangGraph middleware）程度最高，工程韧性最好（熔断/重试/落盘换入/压缩前 flush），但状态内化、无文件外部化、无向量检索，恰好站在文章批评的"框架黑盒"一侧。
- **Hermes** —— SQLite 中心化 + 混合压缩 + 最强 error-as-guidance + 多生态上下文文件兼容；是"DB-as-truth"路线里把透明性（FTS 可查、文件记忆可改）做得最好的反例。

---

## 对文章观点的总评：印证与偏离

**强印证（三家普遍命中）**：
1. Head-Tail Truncation + 工具结果占位符掩码 —— 维度 4，三家全中，是文章最经得起检验的论断。
2. Session 按来源寻址 + 落盘持久化 —— 维度 1，OpenClaw 几乎逐字命中。
3. "压缩内容是背景参考非指令"的前缀告知 —— Hermes SUMMARY_PREFIX、OpenClaw 均有，防重复执行。
4. 上下文窗口 = 稀缺资源、防 OOM 优先 —— 三家都有 token 水位线/预检压缩。

**系统性偏离（文章为教学极简做的取舍 vs 生产现实）**：
1. **"不用额外 LLM 做摘要，只字符截断"** —— 现实里三家主力都是 LLM 摘要式压缩；字符截断退居工具结果层辅助。
2. **"Dreaming + 按日期 Episodic 落盘"** —— 三家都没有；属文章对 OpenClaw 的理想化/超前描述。
3. **"动态 thinking budget 按宏观/微观信号分配"** —— 三家都还是静态档位/显式开关；文章承认这是"更实用的引擎应该"，确属未来式。

**文章没覆盖但生产必备的工程深水区（值得反向补进认知框架）**：
- 压缩有损 → 必须配**压缩前抢救**（deer-flow memory flush / skill 抢救、Hermes 静态回退）。
- 工具结果超限 → **落盘 + 虚拟文件引用 + read_file 豁免**优于纯掩码（deer-flow）。
- 文件记忆 → **必须有硬字符上限 + injection/exfil 扫描**（Hermes）。
- prefix cache → **记忆/日期注入要"冻结"进固定位置**复用缓存（deer-flow/Hermes）。
- ReAct 死循环 → **IterationBudget 迭代次数预算**（Hermes，文章只提了 thinking budget，没提 iteration budget）。
- 多端并发 → **进程级文件锁 + watchdog + PID 检测**（OpenClaw，比 `RWMutex` 更适配真实部署）。

---

## 对 expert-work 的简短启示（仅供参考，非本报告主体）

- expert-work 的长期记忆（embedder 平台级 + Hybrid 检索方向）与 **OpenClaw memory-search 的 0.7/0.3 + MMR + 时间衰减**最可借鉴。
- expert-work 已有 Stream SE（带源失败报告/进化），其 **error-as-guidance** 可参考 **Hermes ClassifiedError + recovery tool results 注入**模式。
- 若 expert-work 走"状态外部化"，**OpenClaw bootstrap 扫描 + Hermes 多生态上下文文件发现链**是成熟范式；若走"状态内化"，**deer-flow ThreadState + checkpointer**是参照，但要补可视化以避免黑盒。

---

## 复核 / 验证方法（结论可证伪）

1. 抽查关键 file:line 引用是否准确，例如：
   - `rg -n "MAX_TOOL_RESULT_CONTEXT_SHARE|calculateMaxToolResultChars" src/github/openclaw/src/agents/pi-embedded-runner/tool-result-truncation.ts`
   - `rg -n "externalize_min_chars|read_file" src/github/deer-flow/backend/packages/harness/deerflow/config/tool_output_config.py`
   - `rg -n "_CONTENT_HEAD|_CONTENT_TAIL|SUMMARY_PREFIX" src/github/hermes-agent/agent/context_compressor.py`
2. 对"偏离"结论（如"三家都用 LLM 摘要"），确认各自压缩入口确实调用了模型（grep 压缩 middleware/compressor 里的 LLM invoke）。
3. 对"缺失"结论（Dreaming/Episodic/动态 thinking）做反向确认：在各自树内 grep `dream|episodic|YYYY-MM-DD` 等关键词应为空或无关。
