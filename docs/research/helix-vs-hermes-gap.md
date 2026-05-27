# helix vs Hermes — 15 维度差距分析报告

> **报告作用**：基于前两份事实底稿，对 helix 与 Hermes 在 15 个维度上做客观对比 + 借鉴价值评级，帮 helix 团队规划 M1 / M2 backlog 取舍时知道"什么值得抽出来补、什么是设计性差异不该照搬"。
>
> **必须先读底稿**：本报告**不重复事实陈述**，所有事实细节请参考：
> - `docs/research/hermes-deep-dive.md`（Hermes 源码事实，3381 行，每论断带 `file:line`）
> - `docs/research/helix-current-state.md`（helix 当前实现 + ITERATION-PLAN 阶段标签，3484 行）
>
> **范围声明**：
> - **不写具体实现技术方案** —— 不给"helix 该怎么改 X"的代码细节、不贴 file:line、不列实施步骤。这些留给后续独立的"实施 plan"。
> - **带借鉴价值判定** —— 每维度给 gap 分类 + 高/中/低评级 + 关键决策点。
> - **不评判 helix / Hermes 设计的好坏** —— 评级框架只评"这条能力是否值得 helix 借鉴 Hermes"。
> - **不引用其他 agent 框架做横向对比**（LangGraph / AutoGen / CrewAI 等）。
>
> **报告生成日期**：2026-05-27
> **基于的底稿版本**：Hermes `bb4703c76` + helix `6e0e9ed`

## 评估框架

### Gap 类型四分类（A / B / C / D）

| 类型 | 含义 |
|------|------|
| **A — 已规划未实现** | helix 在 `docs/ITERATION-PLAN.md` 的 M1/M2/M3 已有显式 backlog；方向认可只是没排到时间。借鉴 Hermes 的"什么时候"+"如何避坑"。 |
| **B — 设计上不做** | helix 明确选择不做。原因可能是产品定位（业务无关多租户引擎）/ 多租户合规 / 安全审计 等。**谈借鉴没意义**。 |
| **C — 弱于 Hermes 的真 gap** | helix M0 已做但能力深度上明显弱于 Hermes。这一类是**最值得讨论的借鉴对象**。 |
| **D — helix 强 / 不同方向** | helix 比 Hermes 做得深 / 适合自己定位的不同方向。这一类是**反向的"Hermes 应该向 helix 学"**，对 helix 来说没有借鉴必要，但帮助理解"为什么 helix 这样设计"。 |

### 借鉴价值评级（高 / 中 / 低 / 不适用）

| 评级 | 判定准则 |
|------|---------|
| **高** | 满足以下任一：(a) Hermes 这一能力直接缓解 helix 在 ITERATION-PLAN 已记录的真实痛点；(b) 实现成本远小于带来的能力提升；(c) 是其他 gap 的前置依赖。 |
| **中** | 价值清晰但当前不阻塞；可以放进 M2 评估池，不必抢 M1。 |
| **低** | helix 已有功能等价物，差距是实现细节而不是能力本身；或者场景错配（helix 多租户企业场景不需要）。 |
| **不适用** | B 类设计性差异，谈"借鉴"没意义。 |

### "关键决策点"是什么

每个值得借鉴的维度，本报告列出 helix 团队**实际要拍板的问题**（如"如果上 Curator 自动状态机，pinned 字段怎么跟 manifest 自动重新发布协调？"）。报告**不替团队拍板**，只把决策面摆出来。

### 当前阶段背景

- helix 已基本完成 M0（14 个 stream 全 `[x]` 或主要子项 `[x]`），现在在 **M0→M1 Gate**（canonical agent + eval baseline + dogfood 平行 30 天）。
- 后面 12-16 个月会进入 M1（生产化）/ M2（Durable + Multi-Agent）/ M3（K8s + 生态）。
- **本报告的"借鉴清单"主要影响 M1 / M2 backlog 取舍**，不影响当前 M0→M1 Gate（Gate 已锁）。

---

# 第 0 章 — 整体定位差异（读 gap 分析的前提）

这一章不是 gap，是**读后面 15 维度判断的前提**。如果跳过这一章去看"维度 X 差距分类 = B 设计上不做"，可能会觉得"helix 不做这个怎么行" —— 但代入定位差异看，就明白为什么不做。

## 0.1 一句话差异

| | Hermes | helix |
|---|--------|-------|
| 产品形态 | **单进程 agent + 单 CLI + 直接挂消息平台** | **分布式 backend agent platform（7 服务）+ Admin UI**，业务系统经 API 消费 |
| 客户 | **个人 power user**（独立开发者 / SRE / dogfood) | **企业**（自己控制 agent 运行栈、不用 SaaS） |
| 部署形态 | 一个 binary + `~/.hermes/` 目录 | docker-compose 全栈 + K8s (M3) |
| 末端用户 | **直接是 Hermes 用户**（聊 telegram / slack / CLI） | **业务系统的用户**（helix 不见末端用户） |
| 工程哲学 | Agent autonomy + 自助扩展 + 文件系统配置 | Audit / governance / 多租户 RLS + 显式审核 + DB 存储 |

## 0.2 这个差异对 15 维度判断的影响

| 这个定位差异 | 决定了哪些维度的 gap 分类 |
|------|------|
| backend / 业务系统对接 | 维度 10 消息平台 = B；维度 13 末端用户 UI = B |
| 多租户 / RLS 强隔离 | 维度 2 自我改进的 agent 自动写 memory = B；维度 3 记忆全局共享 = B；维度 5 用户级 provider plugin = B |
| 显式审核 / audit-first | 维度 12 hook 热加载 = B；维度 14 跨用户 skill 共享 = B（除 marketplace） |
| 走 PR / ADR 流程 | 维度 5 provider plugin discovery = B；维度 12 用户级 tool plugin = B |
| 故意把训练流水线外推给客户 | 维度 15 trajectory 压缩 + 训练闭环 = B |

> **结论**：很多看起来"helix 缺 X，Hermes 有 X"的差异，进 B 类（设计性差异），不是真 gap。**真 gap 集中在维度 2 / 3 / 9 / 11 / 14 的局部细节**（详见后文）。

---

# 维度 1 — Agent 循环（ReAct）

## 1.1 能力快照

- **Hermes**：自研主循环 4306 行（`conversation_loop.py`），单 turn 维度做透 —— 工具并发（max 8 worker）、5 类重试矩阵、流式 stale 心跳、cancellation 全链路、多 fallback provider。详见 `hermes-deep-dive.md § 维度 1`。
- **helix**：LangGraph StateGraph 标准实现 754 行（`builder.py`）+ 8 条 **Hermes-derived** Stream L 能力（L1 Anthropic prompt caching / L2 context compression / L3 stream stale / L4 mutation advisory / L5 iteration refund / L6 阶段化并行 / L7 trajectory / L8 OAuth refresh），已 merge in main 通过零债 6 条核验。详见 `helix-current-state.md § 维度 1`。

## 1.2 差距分类

**D — helix 用 LangGraph 标准化路径，方向不同但已强**

Stream L 8 条单 turn 能力本来就是**直接从 Hermes 学的**（PR 链 #198-#206 显式 acknowledge `Hermes-derived`）。helix 在 ReAct 主循环这层**没有 C 类（弱于 Hermes 的）真 gap**。

唯一可以辩护是 "C-边际" 的：Hermes 主循环里**重试矩阵更细**（5 类计数器：invalid_tool / empty_content / thinking_prefill / incomplete_scratchpad / rate_limited），helix 的错误处理走 middleware chain（`LLMErrorHandlingMiddleware`），分类粒度可能略粗。但 helix 走 LangGraph 标准 + middleware chain 更易扩展，整体是不同方向不是"弱"。

## 1.3 借鉴价值评级

**低**。Stream L 已经把 Hermes 单 turn 8 条能力补齐，剩下的差距是 horizontal 实现风格选择（自研 vs LangGraph）。helix 走 LangGraph 是对的（生态成熟、checkpointer 标准化、子图复用）。

## 1.4 关键决策点

无可操作的借鉴决策。**这条不需要进 M1 评估池**。

---

# 维度 2 — 自我改进（闭环学习 + 技能自创建）

## 2.1 能力快照

- **Hermes**：会话结束 → 后台 fork agent（继承 cached system prompt 命中前缀缓存近免费）+ 工具白名单（仅 memory + skill_manage）+ daemon thread；prompt 100+ 行细致告诉 review agent "什么算可写信号、什么坚决别写"；**Curator 7 天周期 + 三态自动转移**（active → stale → archived）+ 4 整合动作（merge / new umbrella / demote to support / prune）。详见 `hermes-deep-dive.md § 维度 2`。
- **helix**：Trajectory + G.6 Feedback → Curation Worker（规则分类 negative > failed > positive）→ Curation Candidate（PENDING） → **管理员人工审 → promote 进 Eval Dataset**；agent 自创建 skill 在 M1-K J.7b-1 backlog。详见 `helix-current-state.md § 维度 2`。

## 2.2 差距分类

**复合**：

- **agent 自动写 skill / memory 不审核** → **B**（多租户合规决定，"agent 自己改自己的库没人审" 企业客户不接受）
- **Curator 自动状态机（active/stale/archived 时间启发式转移）** → **C**（helix 完全没有自动转移；Skill `DRAFT → ACTIVE → ARCHIVED` 全靠管理员手动 PATCH `status`）
- **Curator LLM 整合 4 动作（merge / new umbrella / demote / prune）** → **A**（"内部 marketplace" 在 M3 backlog；整合机制是 marketplace 的前置）
- **Skill review prompt 的"什么算可写信号 / 什么坚决别写"分类** → **C**（helix 即使将来上 agent 自创建 skill，这套防"学坏"约束也用得上）

## 2.3 借鉴价值评级

**中-高**。

- Curator 自动状态机：**高**。helix 的 skill 库随着 J.7b-1 上线后会快速膨胀，没有自动 stale / archive 机制几个月后会变得难用。Hermes 的"纯启发式（无 LLM）三态转移 + pinned 字段保护用户固定 skill"模式直接可移植到 helix 多租户场景。
- Skill review prompt 的"防学坏"约束：**中**。M1-K J.7b-1 上 `author_skill` / `refine_skill` 时必须有这套约束，否则 agent 会把"环境性偶发失败"固化成永久 skill（Hermes 已经踩过坑写在 prompt 里）。
- Curator LLM 整合 4 动作：**中**。M3 marketplace 之前不急。

## 2.4 关键决策点

- 自动状态机的时间阈值（Hermes 默认 30 天 stale / 90 天 archived）跟 helix 企业客户的会话频度匹不匹配？需不需要 per-tenant 可配？
- `pinned` 字段语义：是 per-tenant 自己 pin 自己的 skill，还是 system_admin 可以 pin 跨租户 skill？
- 自动状态机由谁触发：control-plane scheduler 复用？还是新独立 worker？（影响实施成本）
- agent 自创建 skill 的"防学坏"约束写在哪：manifest？hardcoded prompt？还是按 tenant 可定制？

---

# 维度 3 — 记忆系统

## 3.1 能力快照

- **Hermes**：5 种存储位（MEMORY.md / USER.md 系统提示快照 + Session DB + 跨会话 grep + 外挂 MemoryProvider + Skill）；MEMORY.md 走 **frozen snapshot 模式**（加载时冻结进系统提示 + 中毒条目替换为 `[BLOCKED:...]` 占位符 + live 状态保留原文让用户能看到 / 删除 + drift backup 防外部编辑被自动覆盖）；前缀缓存稳定性靠 snapshot 维持。详见 `hermes-deep-dive.md § 维度 3`。
- **helix**：三层架构（session messages / short-term / long-term pgvector）+ per-(tenant, user) RLS + DLQ 重试（5 次 backoff）+ dedup（content_hash UNIQUE）+ soft delete + Memory CRUD API + Memory Recall Eval Gate。**没有 frozen snapshot 概念**（每 turn 动态召回 top-k，前缀缓存命中天然低）。详见 `helix-current-state.md § 维度 3`。

## 3.2 差距分类

**复合**：

- **per-`$HERMES_HOME` 全局 MEMORY.md** → **B**（多租户隔离禁止，helix per-(tenant, user) 是产品根基）
- **frozen snapshot + 投毒条目占位符 + drift backup** → **C**（helix 完全没有这套防御；多租户 memory 同样面临"prompt injection 数据进 system context"风险）
- **MEMORY.md 进系统提示常驻**（前缀缓存友好） → **B-C 边界**：helix 选了"每 turn 动态召回"路径，跟前缀缓存有方向冲突，但这是 trade-off（多用户场景 frozen snapshot 跨 user 失效，反而 turn-level 召回更对）

## 3.3 借鉴价值评级

**中**。投毒防御 + drift backup 是 **helix M0 缺失的安全细节**，企业客户的合规会问"如果数据库被注入恶意 memory entry 会发生什么"。Hermes 已经踩过这个坑（中毒条目进 system prompt → LLM 被 jailbreak）。

但需要小心：Hermes 是单用户单 HERMES_HOME 的简单模型，helix 是多租户 + per-(tenant, user)，"frozen snapshot" 不能直接搬 —— 没法把所有 tenant 的 memory 都冻结进同一 system prompt。helix 要借鉴的是**机制**（"加载时扫一遍 + 中毒条目占位符替换 + live 保留原文给用户审"），不是**实现**。

## 3.4 关键决策点

- helix 的 memory 进 system context 是 turn-level 动态召回，"加载时冻结"在哪一层做？召回完 + LLM 调用前的临时 snapshot 还是别的？
- 中毒条目是 reject 写入（写时扫）还是接受写入但召回时过滤（读时扫）？**前者更紧，后者跟 Hermes 思路一致（保留用户能审）**。
- 威胁模式库由谁维护：内置硬编码还是 per-tenant 可配？
- drift backup 在 helix 场景意味着什么：管理员通过 API 改 vs agent 改 vs 直接改 DB？

---

# 维度 4 — 上下文管理（压缩 + 智能路由）

## 4.1 能力快照

- **Hermes**：preflight + summarise-the-middle + 多 pass（最多 3 轮）+ SUMMARY_PREFIX（防 LLM 把摘要当 active instruction 的 300+ 字符控制文本）+ image-aware token estimate（每图 1600 token）。详见 `hermes-deep-dive.md § 维度 4`。
- **helix**：L.L2 完全 Hermes-derived；同样 head/tail 保留 + middle summarise + max_passes = 3，**显式 `raise ContextOverflowError`**（无静默 fallback）；摘要 prompt 简短（5 行）vs Hermes SUMMARY_PREFIX 复杂；image token estimate 缺失。详见 `helix-current-state.md § 维度 4`。

## 4.2 差距分类

**复合**：

- **summarise-the-middle 主流程** → **D**（helix 已 derive + 显式 fail 更严格）
- **SUMMARY_PREFIX 的"防 LLM 把摘要当 instruction"控制文本** → **C-边际**（helix 摘要 prompt 比较简短，但 helix 把摘要放 `<context-summary>` SystemMessage 用 XML 包裹本身就有一定效果，可能没那么必要）
- **iterative summary preservation** → **B**（helix Mini-ADR L-2 显式选择不做 —— 跟 Hermes 一致）
- **image-aware token estimate** → **C**（helix 的 `estimate_tokens` 没专门处理多模态 message，J.6 多 image 场景可能误判）

## 4.3 借鉴价值评级

**低**。

- Image token estimate：**低-中**。J.6 上线后多图 message 进 messages list 可能让 `estimate_tokens` 偏低，触发 413 / 跑超预算的成本风险。但 helix 还可以等到 J.6 dogfood 报告问题再补，不必先动。
- SUMMARY_PREFIX 的控制文本：**低**。XML 包裹已经做了大部分防 jailbreak 工作，文本细节差异有限。

## 4.4 关键决策点

- 多图 message 的 token estimate 由谁负责：`ContextCompressor.estimate_tokens` 还是 `TokenUsageMiddleware` 后置校正？
- 不同 provider 的图像 token 计费规则差异大（Anthropic ≈ w×h/750、GPT-4o ~1700、Gemini 258/tile），helix 是用 Hermes 的 1600 保守常量，还是 per-provider 精确？

---

# 维度 5 — 模型 / Provider 抽象

## 5.1 能力快照

- **Hermes**：声明式 `ProviderProfile` dataclass（30 字段）+ 三层 plugin discovery（bundled → user `$HERMES_HOME/plugins/model-providers/` → legacy 单文件）+ 30 个 bundled provider + 别名机制 + `api_mode` 解耦 transport 协议（chat_completions / anthropic_messages / codex_responses）+ 4 个可覆盖钩子（prepare_messages / build_extra_body / build_api_kwargs_extras / fetch_models）。详见 `hermes-deep-dive.md § 维度 5`。
- **helix**：`runtime_checkable Protocol`（鸭子类型）+ 9 个 provider（含 5 个国内）+ tree-based fallback + per-provider 限流 + L.L3 stream stale + L.L8 OAuth refresh + step-class routing（J.11）+ codex_responses 协议不支持。详见 `helix-current-state.md § 维度 5`。

## 5.2 差距分类

**复合**：

- **三层 plugin discovery（含用户级 `$HERMES_HOME` 覆盖）** → **B**（helix 故意走 PR + ADR 流程，"用户级 provider 热加载"跟 helix 安全审计冲突）
- **30 个 bundled provider 数量** → **B-边际**（数量本身无产品价值；helix 9 个覆盖了主流，国内 5 个 + Anthropic / OpenAI / Azure / self-hosted）
- **`api_mode` 抽象（codex_responses 协议支持）** → **A-边界**（helix 当前不支持 Codex API；不在 M1 backlog，但如果客户场景需要可以加）
- **`prepare_messages` / `build_extra_body` 等可覆盖钩子** → **D**（helix 用 `runtime_checkable Protocol` + LLMError 分类驱动 fallback，路径不同但更声明式）
- **`default_aux_model` 字段（每 provider 内置便宜模型用于辅助任务）** → **C-边际**（helix 摘要 LLM 是单独配置，没有"按 provider 家族自动选 cheaper"机制）

## 5.3 借鉴价值评级

**低**。

- plugin discovery：**不适用**（B 设计性差异）
- 数量：**低**（场景未到）
- `default_aux_model`：**低**（helix 已有 ModelSpec.fallback 机制，aux model 单独配置只是显式 vs 隐式选择）
- `api_mode` Codex：**低**（M1 backlog 里没有 Codex 客户需求；遇到再加）

## 5.4 关键决策点

无紧迫决策。这条**不需要进 M1 评估池**。

---

# 维度 6 — 本地推理

## 6.1 能力快照

- **Hermes**：Tailscale CGNAT 网段识别（`100.64.0.0/10`）+ Ollama 专用 `query_ollama_num_ctx()` 探针（通过 `/api/show` 拿真实 context length）+ 本地端点 timeout 自动放大（stream_read 30s → 120s）。详见 `hermes-deep-dive.md § 维度 6`。
- **helix**：通过 `provider="self-hosted"` + `base_url` 接 vLLM / Ollama / llama.cpp，**无任何"本地特殊处理"**（timeout 全局值；context length 靠 manifest 写对；不识别本地端点）。详见 `helix-current-state.md § 维度 6`。

## 6.2 差距分类

**C — 弱于 Hermes 的真 gap，但场景错配**

helix 的 self-hosted provider 只是"路由到任意 OpenAI 兼容端点"，没有 Hermes 那种"识别本地 → 放大 timeout → 探针真实 context length"的智能。这是真 gap。

但 helix 的客户场景主要是企业云端 API（Anthropic / OpenAI / 国内云模型），**本地推理在企业生产场景占比低**；内部 dogfood / 私有化部署可能用得上，但不是主战场。

## 6.3 借鉴价值评级

**低**。

helix 多租户企业场景下"客户用自己的 vLLM 集群"是合理的，但场景占比低；ITERATION-PLAN 没列本地推理优化条目，说明团队判断同样。

## 6.4 关键决策点

- 是否有客户场景需要本地推理优化？如果有 ≥1 个企业客户用 vLLM 集群且报过 timeout / context length 误判问题，那借鉴价值升到**中**。

---

# 维度 7 — 沙箱执行

## 7.1 能力快照

- **Hermes**：6-8 后端（local / docker / ssh / modal / managed_modal / daytona / singularity / vercel_sandbox）+ 统一 `BaseEnvironment` 抽象 + 会话快照（export -p / declare -f / alias -p）+ stdin pipe vs heredoc 双模式 + 非阻塞 wait + 活动心跳。详见 `hermes-deep-dive.md § 维度 7`。
- **helix**：仅 Docker + gVisor (runsc)，独立 `services/sandbox-supervisor/` 4452 行；Brain-Hands **物理隔离**（sandbox 内无 LLM client / 无凭据 / 凭据走 credential-proxy）；`helix-sandbox-egress` Docker `--internal` 网络硬隔离；workspace 持久化（per-user named volume）；多租户 quota；F.8 自动化 5/7 安全门 + 2 个推 staging Linux。详见 `helix-current-state.md § 维度 7`。

## 7.2 差距分类

**D — helix 强 / 方向不同**

helix 走"少数后端 + 深度安全 + 多租户配额"路径；Hermes 走"多后端 horizontal diversity"路径。

| | Hermes | helix |
|---|--------|-------|
| 后端数量 | 6-8 | 1（Docker） |
| 隔离强度 | 看后端（docker 默认 / runc / 也支持 runsc） | gVisor (runsc) + readonly rootfs + cap-drop ALL + `--internal` 网络 |
| Brain-Hands 物理隔离 | ❌ | ✅ |
| 凭据进沙箱 | 看后端 / env vars | ❌ 永远不进，credential-proxy 注入 |
| 多租户 quota | ❌ | ✅ |
| 持久化 workspace | ❌（每后端自管） | ✅ J.15 per-user named volume |
| 多沙箱后端 | ✅ | ❌ M3 K8s 沙盒 |

helix 在产品定位下走对了 —— 多租户 + 合规要求让 Brain-Hands 物理隔离 + gVisor 比 horizontal diversity 重要得多。

唯一可借鉴 trace：**Hermes 的"会话快照 source"模式让 CWD / env / alias 跨命令持久化**（每次 docker run 是无状态的，sandbox 内连续命令的 CWD 跨调用会丢；Hermes 用 snapshot 文件解决）。helix 当前每次 `exec_python` 是新容器，跨 exec 状态只有 /workspace 文件持久化，没有 shell 环境持久化。

## 7.3 借鉴价值评级

**低**。

- 多后端：**不适用**（M3 K8s 沙盒已 backlog）
- 会话快照：**低**。helix 当前 `exec_python` Python 解释器是无状态的（每次新进程），跟 Hermes 的"连续 bash 命令"场景不一样；helix Python 工具的"持久化 state" 已经通过 /workspace 文件 + manifest 注入 module 解决。

## 7.4 关键决策点

- M1-A "Sandbox warm pool（P95 < 500ms）" 是否需要 Hermes 的 snapshot source 思路？warm pool = 多 exec 复用容器，那时候 CWD / env 持久化才有价值。

---

# 维度 8 — 子 Agent / 并行

## 8.1 能力快照

- **Hermes**：黑名单工具（DELEGATE_BLOCKED_TOOLS = {delegate_task, clarify, memory, send_message, execute_code}）+ 默认深度 1（可配 3）+ ThreadPoolExecutor 并行（max 3 concurrent）+ 子审批 auto-deny callback（防 worker thread input() 死锁）+ 子结果 JSON 摘要（默认 500 char）。详见 `hermes-deep-dive.md § 维度 8`。
- **helix**：MAX_SUBAGENT_DEPTH = 3 + 结构性递归终止（depth=3 时不注册 SubAgentTool）+ 构建期 DFS cycle detection + L.L6 阶段化并行（asyncio.gather + Semaphore(8)）+ deadline 全链路继承 + 6 态 SubagentStatus + sub-agent trajectory 单独写 + budget telemetry（iteration_used / llm_call_count / wall_clock_ms 回传父）。详见 `helix-current-state.md § 维度 8`。

## 8.2 差距分类

**D — helix 强 / 不同方向**

helix 在 J.4-补强-2（2026-05-21 取消 M2-B 推迟，M0 内交付并行 fan-out）后**全面强于 Hermes 的子 agent 实现**：

| | Hermes | helix |
|---|--------|-------|
| 黑名单 | 工具级（5 工具拒绝） | 同时有结构性深度终止 |
| 并行模式 | ThreadPoolExecutor | asyncio.gather + Semaphore（更高效） |
| Cycle detection | ❌（运行期才报错） | ✅ 构建期 DFS |
| Deadline 继承 | ❌ | ✅ ToolContext.deadline_at 全链路 |
| Trajectory | 跟父混 | 单独 ObjectStore key + 3 outcome 全 dispatch |
| Budget telemetry | ❌ | ✅ iteration_used / llm_call_count / wall_clock_ms |
| 子结果 | 500 char JSON | 完整 ToolMessage + meta + state_updates |

唯一 C-边际 是：**Hermes 的"工具黑名单"语义清晰**（防止子 agent 调 memory / clarify 等破坏共享状态的工具）；helix 默认子 agent 继承父的 toolset 没有显式黑名单，可能允许子调 memory（虽然 RLS 隔离了，但语义上是父的 user_id 在写）。

## 8.3 借鉴价值评级

**低**。

- helix 子 agent 实现整体强 + 已 deploy（J.4-补强-2 + L.L6 合并入 M0）
- 子 agent 工具黑名单：**低**。理论 gap，但 helix 已经有 audit / RLS / quota 三层防御，再加黑名单是 belt-and-suspenders。

## 8.4 关键决策点

- 子 agent 默认应该不应该禁用 `memory` 写入 / `send_message` / `clarify`？跟 helix 的"manifest 显式声明"哲学冲不冲突？

---

# 维度 9 — Cron 调度

## 9.1 能力快照

- **Hermes**：自带（jobs.json 文件）+ croniter + `@hourly` 等宏 + 自然语言（"every 30m"）+ **双层 prompt 注入扫描**（创建时严扫含隐形 Unicode `_CRON_INVISIBLE_CHARS` ZWJ/RTL 等；运行时拼完 skill 后宽扫）+ 60s tick 粒度 + 每 job 独立 task 起 AIAgent。详见 `hermes-deep-dive.md § 维度 9`。
- **helix**：自研 scheduler（control-plane 内嵌）+ DLQ retry（5 次 backoff 60s/5m/30m/2h/6h）+ per-tenant quota + cron / webhook 双 kind + audit + N system_admin 跨租户 + 三阶段 cycle（fire / reconcile / retry）。**无 prompt 注入扫描**（创建 trigger 时不扫 prompt，运行时拼 skill 后也不扫）。详见 `helix-current-state.md § 维度 9`。

## 9.2 差距分类

**复合**：

- **DLQ retry / per-tenant quota / cron + webhook / audit / 跨租户** → **D**（helix 全面强）
- **双层 prompt 注入扫描（含隐形 Unicode）** → **C**（真 gap，helix 完全没有）
- **60s tick vs helix 自研 scheduler 频率** → **D**（helix 三阶段 cycle 更灵活）
- **自然语言 schedule（"every 30m"）** → **D-边际**（helix UI 可以前端转换，后端只接 cron 5-field）

## 9.3 借鉴价值评级

**中-高**。

**Cron prompt 注入扫描（含隐形 Unicode）借鉴价值 = 高**。理由：

- **Cron 触发的 prompt 是 attack surface**。webhook trigger 走 HMAC 验证签名相对安全；cron trigger 的 `prompt` 字段是管理员配置 + 拼 skill 内容，**两端都可能被注入**（管理员被钓鱼 / skill 被恶意 PR）。Hermes 的双层扫描（严扫 + 宽扫）是踩过坑的设计。
- **隐形 Unicode 检测**（ZWJ / RTL / BOM 等 `_CRON_INVISIBLE_CHARS`）是常见 prompt injection 技术；helix 多租户场景 audit 会要求这种检测。
- **实施成本低**：一个 regex 表 + 一个 scan 函数，进 control-plane trigger CRUD + scheduler firing 路径。

## 9.4 关键决策点

- 严扫规则由谁维护：内置硬编码 + ADR 增减，还是 per-tenant 可调（容忍 false positive）？
- "运行时拼完 skill 后宽扫"是 helix 当前 trigger 路径的哪个 hook 点：`fire_trigger` 之前？scheduler 三阶段哪一阶段？
- 注入命中的处理策略：reject + audit 还是 accept + audit warning？（Hermes 直接 raise `CronPromptInjectionBlocked`）
- 多语言场景下规则是否需要 per-locale 拆分？

---

# 维度 10 — 消息平台

## 10.1 能力快照

- **Hermes**：22 个内置 `Platform` enum + 8 个 plugin 平台 = 30 个；统一 `BasePlatformAdapter`；流式输出（Telegram `sendMessageDraft` / 其他 edit_message）；6 个媒体方法；`SessionSource` 抽象。详见 `hermes-deep-dive.md § 维度 10`。
- **helix**：不内置任何消息平台 adapter；唯一入站通道 = webhook trigger（HMAC 验证）。详见 `helix-current-state.md § 维度 10`。

## 10.2 差距分类

**B — 设计上不做**

`docs/ITERATION-PLAN.md:328` 原文：

> "Business 系统通过 API 消费 helix 的 per-user 持久 agent；helix **不自带末端用户对话 UI**（末端用户通过 business 系统自己的 UI 与 agent 对话）"

末端消息平台对接是**业务系统的责任**。helix M0/M1/M2/M3 都不会内置 Slack/Telegram/飞书 adapter。

## 10.3 借鉴价值评级

**不适用**。

## 10.4 关键决策点

无。

> ⚠️ 如果未来 helix 走向"提供可选的消息平台 connector 库"（让客户业务系统更快上线），那是产品方向变化，不是借鉴 Hermes。

---

# 维度 11 — MCP 支持（Client + Server）

## 11.1 能力快照

- **Hermes**：Client（stdio + HTTP/StreamableHTTP + SSE 三 transport，per-server config）+ Server（暴露 10 工具：conversations_list / conversation_get / messages_read / attachments_fetch / events_poll / events_wait / messages_send / permissions_list_open / permissions_respond / channels_list 给 Claude Code / Cursor）+ Sampling（server 反向请求 LLM）+ OAuth manager。详见 `hermes-deep-dive.md § 维度 11`。
- **helix**：Client stdio only（M1+ 才有 HTTP/SSE，Mini-ADR E-5 明确）+ N=5 server cap + 20K 中间截断 + per-tenant 配置。**无 Server**（搜不到任何 `FastMCP` / `MCPServer`）。详见 `helix-current-state.md § 维度 11`。

## 11.2 差距分类

**复合**：

- **MCP Client HTTP/SSE transport** → **A**（M1+ backlog，Mini-ADR E-5 已规划；2026-05-27 提前到 capability uplift Sprint #5）
- **MCP Server（暴露 helix 能力给 Claude Code / Cursor）** → **B**（2026-05-27 复审从 "B-边界" 升级到明确 B，见 § 11.5）
- **Sampling（server 反向请求 LLM）** → **B**（与 MCP Server 同期取消 — Sampling 是 server-side capability，helix 不做 server 就不存在 Sampling 路径）
- **OAuth manager** → **A**（MCP client 调外部 server 时部分 server 走 OAuth；Mini-ADR L.L8-MCP 后续 sprint）

## 11.3 借鉴价值评级

**中**（仅指 MCP Client 方向）。

- HTTP/SSE transport：**中-高**。helix Mini-ADR E-5 已 backlog；2026-05-27 提前到 Sprint #5 — 2026 年大量公开 MCP server (GitHub / Postgres / Linear / Notion / Slack ...) 是 remote HTTP/SSE 形态，stdio-only 锁死本地 process，agent 沙箱无法触达。Hermes 实现路径（per-server `transport: sse` / `url:` 配置）可直接参考。
- MCP Server：**不适用**（2026-05-27 复审推翻，见 § 11.5）。

## 11.4 关键决策点

- MCP Client HTTP/SSE：per-tenant secret 隔离怎么做（HTTP header auth token 走 helix secret store？）
- MCP Client HTTP/SSE：远端 server 调用超时 + retry 策略（vs stdio 本地 process 完全不同的失败模式）
- OAuth：先存配置不实现 flow 是否够 M0→M1 Gate 阶段（哪些常用公开 MCP server 实际需要 OAuth？）

## 11.5 2026-05-27 复审记录 — MCP Server 评级翻新

原本 § 11.3 给 MCP Server 评 "**中-高(反直觉的高价值)**"，列了 4 条论据。Capability uplift Sprint #5 实施前复审，4 条论据逐条不立：

| 原论据 | 复审推翻 |
|------|---------|
| 企业开发者用 Claude Code / Cursor 写 manifest / debug agent 是真实场景 | 混了"写 manifest"(编辑器 + git + CLI，跟 MCP 无关) 和 "IDE 看 sessions"(Admin UI 已覆盖)两件事；真需要 IDE-as-helix-frontend 的人群没被验证 |
| 跟 backend platform 定位 compatible | "不冲突" ≠ "值得做"；REST API + Admin UI + Python SDK 已覆盖所有操作面，再加一层 MCP wrapper 没增量价值 |
| 实施成本中等(用 FastMCP 包装现有 API) | 列出的 6 工具 (conversations_list / messages_send / channels_list / events_poll ...)**是 Hermes 消息平台子系统术语**;helix 不是消息平台,直接 port 等于把 Hermes 产品形态 graft 到 helix,违反 [memory:general-platform-positioning] |
| Hermes-equivalent operator experience | Hermes 做 MCP server 是因为它是 local-first CLI(agent + IDE 都在用户本机,本地进程互通有意义);helix 是 server-side 多租户 backend,这个互通模型不适用 — 把 Hermes 设计当 baseline 是 [memory:no-design-choice-disguise] 的反面 |

**结论**:agent 平台的边界 = 消费外部 MCP 生态,不是再造一个被消费的 server。MCP Server 在 helix 永久 B 档,除非未来出现"reverse-MCP 是真用户群体最强需求"的硬证据。详见 [memory:mcp-direction-client-only]。

横断面 § B "5 条该补" 清单里原 #5 "MCP Server" 已被 "MCP Client HTTP/SSE transport" 替换。

---

# 维度 12 — 扩展机制（工具 / 技能 / MCP / 钩子）

## 12.1 能力快照

- **Hermes**：17 个 hook（pre_tool_call / post_tool_call / transform_llm_output / transform_terminal_output / 等）+ `plugin.yaml` manifest + entry point 入口（pip 包）+ 三层 plugin discovery（bundled → `$HERMES_HOME/plugins/` → entry points）+ AST 扫 `tools/*.py` 找 `registry.register(...)` 自我注册。详见 `hermes-deep-dive.md § 维度 12`。
- **helix**：声明式 manifest（YAML）+ Tool registry 显式装配 + 中间件链硬编码（8 个 built-in middleware 顺序固定）+ 无 hook + 无 plugin entry point + Skill 库（per-tenant Postgres）。M1-F2 Python 插槽（依赖 cosign 供应链）。详见 `helix-current-state.md § 维度 12`。

## 12.2 差距分类

**复合**：

- **17 个 hook 系统** → **B**（helix 故意走 PR + ADR 流程，"hook 热加载"跟安全审计冲突）
- **用户级 `$HERMES_HOME/plugins/` 覆盖** → **B**（同上）
- **AST 扫 + 自我注册** → **B**（helix 显式装配比"扫所有文件找 side effect"更安全可审）
- **`plugin.yaml` manifest 格式** → **B-边际**（helix 用 Python 声明 + 装配代码代替）
- **M1-F2 Python 插槽** → **A**（已 backlog，是 helix 选的"扩展机制"路径 —— 不是 hook，是 cosign-signed Python package + sandbox 隔离）

## 12.3 借鉴价值评级

**低 / 不适用**。

helix 的扩展机制路径**已经选定**（声明式 manifest + cosign Python 插槽），跟 Hermes hook 哲学是不同方向。如果硬要照 Hermes 上 hook 系统，会跟 helix 的 governance 设计冲突。

唯一可学的细节：**Hermes hook 返回值的 short-circuit 语义**（`transform_llm_output` 第一个返回非 None 的赢）。helix middleware chain 当前是顺序串联，没有 short-circuit；future 加 user middleware 时可参考。

## 12.4 关键决策点

- M1-F2 Python 插槽的"扩展点"范围多大：仅 tool？还是包括 graph / middleware / hook 等价物？（如果包括，hook 命名 / 语义可参考 Hermes 17 个 hook 的分类）

---

# 维度 13 — UI 形态

## 13.1 能力快照

- **Hermes**：CLI (`cli.py` 15089 行 prompt_toolkit + Rich) + 22+ 消息平台 = 末端用户 UI；REPL 模式 + 单 shot 模式 + slash 命令丰富（/model / /sessions / /memory / /cron / 等）。详见 `hermes-deep-dive.md § 维度 13`。
- **helix**：Admin UI（React 19 + Antd 5，17.5K LOC，单 SPA + Cmd+K + OIDC PKCE + i18n + dark/light + Storybook + Playwright + axe a11y）覆盖 Agent / Run / Playground / Curation / Skill / Trigger / Memory / Audit / Settings；**无 CLI**（M1-I `helix lint` / `helix run` backlog）；**无末端用户 UI**（业务系统责任）。详见 `helix-current-state.md § 维度 13`。

## 13.2 差距分类

**复合**：

- **末端用户消息平台 / CLI 形态作为 UI** → **B**（产品定位决定）
- **`helix lint` / `helix run` CLI** → **A**（M1-I backlog）
- **Slash 命令** → **B**（helix 经 Admin UI Cmd+K + REST API 替代，没有 slash 命令空间）
- **manifest JSON Schema 发布给 VS Code / IntelliJ** → **A**（M1-I backlog）

## 13.3 借鉴价值评级

**低 / 不适用**。

- 末端 UI：**不适用**
- CLI：**低**。M1-I 已 backlog；Hermes CLI 15089 行的 prompt_toolkit + Rich 技术栈跟 helix `helix lint` / `helix run` 的"本地跑 manifest"使用场景不一样（前者 REPL，后者 batch），照搬技术栈意义不大。
- JSON Schema：**低**（Pydantic v2 自带 schema 导出，实施细节而非能力 gap）

## 13.4 关键决策点

- `helix lint` 的范围：仅 manifest 语法 / 还是含 dry-run（不真起 service 但跑完所有验证 / 还是含模拟 LLM 调用？
- `helix run` 跟 control-plane 是什么关系：是 control-plane 的本地 mock 模式，还是真起一个简化 in-process？

---

# 维度 14 — 技能可移植（agentskills.io 风格）

## 14.1 能力快照

- **Hermes**：agentskills.io 标准 SKILL.md（YAML frontmatter + body）+ 四子目录（`references/` / `templates/` / `scripts/` / `assets/`）+ 渐进式披露三层 API（list metadata / view full / view linked file）+ 平台过滤（`platforms: [macos, linux]`）+ template vars（`${HERMES_SKILL_DIR}`）+ inline shell（`!`cmd``，默认关）+ agent 自创建（`skill_manage(action=create|patch|...)`）+ Curator 库级整合。详见 `hermes-deep-dive.md § 维度 14`。
- **helix**：name@version + DRAFT/ACTIVE/ARCHIVED + Postgres + prompt_fragment + tool_names + required_models + ZIP import/export + regex deny-list moderation + audit + `<skill>` XML 包裹防 injection。**无 supporting files / 无 platform 过滤 / 无 inline shell / 无 agent 自创建**（M1-K J.7b 8 项 backlog）。详见 `helix-current-state.md § 维度 14`。

## 14.2 差距分类

**复合**：

- **`name@version` Postgres vs SKILL.md 文件系统** → **B**（多租户 RLS 决定 helix 必须 DB 存储；agentskills.io 标准对齐推到 M3 marketplace 才有意义）
- **DRAFT/ACTIVE/ARCHIVED + admin moderation + audit** → **D**（helix 多租户企业治理需要，Hermes 没有）
- **Supporting files（references / templates / scripts / assets）** → **A**（M1-K J.7b-6 backlog）
- **渐进式披露三层 API** → **A**（M1-K J.7b-3 backlog "progressive / lazy skill loading"）
- **agent 自创建（`author_skill` / `refine_skill`）** → **A**（M1-K J.7b-1 backlog）
- **Curator 库级整合 4 动作** → **A**（"内部 marketplace" M3 backlog）
- **平台过滤 / inline shell / template vars** → **B**（helix 多租户 + sandbox 隔离决定，宿主 OS 不可见、`!`cmd`` 是 RCE 风险）
- **公开 hub（agentskills.io）** → **M3 内部 marketplace，不对齐外部标准**

## 14.3 借鉴价值评级

**中-高**（多个子项）。

- **Supporting files（references / templates / scripts）**：**高**。Hermes 已踩过坑 —— 单一 prompt_fragment 撑不住复杂工作流的 reference 材料 + boilerplate 模板 + 重复脚本。M1-K J.7b-6 上线时**目录约定 + 子目录用途分工**可直接借鉴 Hermes 的（不需要重新发明）。
- **agent 自创建（`author_skill` / `refine_skill`）**：**高**。M1-K J.7b-1 是 helix 已经认可的方向；Hermes 的 review prompt（什么算可写信号 / 什么坚决别写）+ 写入优先级 4 级（UPDATE LOADED → UPDATE EXISTING → ADD SUPPORT FILE → CREATE NEW UMBRELLA）+ 命名约束（class-level，禁 `fix-X-today` / `debug-Y-issue-123` 一次性命名）都是直接可借鉴的硬约束。
- **渐进式披露三层 API**：**中**。helix 当前 skill 内容在构建期一次性拼进 system prompt（M0 静态拼）；M1-K J.7b-3 progressive loading 上线时 Hermes 的"metadata → full → linked file" 三层 API 模式可直接抄。
- **Curator 库级整合 4 动作**：**中**。M3 marketplace 之前不急；但 M1-K agent 自创建上线后 skill 数量会快速膨胀，没有自动整合会让库变 N 个窄技能。这时候 Hermes 的"merge / new umbrella / demote to support / prune"四动作 + LLM judge 整合 prompt 直接可移植。

## 14.4 关键决策点

- Supporting files 子目录约定跟 helix Postgres 存储如何映射：是 Postgres 多列 + JSONB，还是 ZIP archive 在 ObjectStore + reference key？
- Progressive loading 的"按需"触发器是什么：LLM 显式调 `skill_view(linked_file)` 还是某种 retrieval signal？
- agent 自创建的"防学坏"约束（环境性失败别写 / 负面工具断言别写）放在哪：硬编码 prompt 还是 per-tenant 可调？
- Curator 整合 4 动作的 LLM judge 调用频率：M1 阶段每天 / 每周 / 每月？跟 helix 的"管理员人工审 candidate"路径如何协调（人审 vs 自动整合的 boundary）？

---

# 维度 15 — RL 训练 / 轨迹收集

## 15.1 能力快照

- **Hermes**：trajectory ShareGPT JSONL（4 outcome）+ `trajectory_compressor.py` 1508 行（用 OpenRouter `google/gemini-3-flash-preview` 摘要压缩超 token 轨迹 + `_compressed` 后缀 + 8 worker / 50 concurrent / 5min timeout）+ 提到 Atropos 但只是被动适配 + 无 reward 模型。详见 `hermes-deep-dive.md § 维度 15`。
- **helix**：trajectory ShareGPT JSONL（4 outcome，per-tenant prefix）+ Curation Worker（信号分类 negative > failed > positive）+ Eval Dataset（golden / trajectory / regression 三 source）+ Eval Gate（baseline 卡 PASS/FAIL）+ Feedback API（up/down）。**无轨迹压缩 / 无训练闭环**（用户自己 LlamaFactory / Axolotl / TRL）。详见 `helix-current-state.md § 维度 15`。

## 15.2 差距分类

**复合**：

- **ShareGPT JSONL 格式 + 4 outcome 分流** → **D**（helix 直接对齐，甚至 outcome 分类更细 —— success / failed / max_steps / cancelled）
- **trajectory 压缩** → **B**：helix 故意外推给客户。理由：(a) helix 客户的训练流水线（LlamaFactory / Axolotl / unsloth）自己有压缩 / sampling 工具；(b) 压缩需要调 OpenRouter / 外部 API，跟 helix backend 角色不符；(c) per-tenant 压缩策略差异大不适合通用方案。
- **Curation Worker + 人工审 + Eval Dataset** → **D**（helix 多租户治理强于 Hermes 的"用户自己看 trajectory_samples.jsonl"）
- **Reward 模型 / RL / SFT 闭环** → **B**（M2-D "持续改进 pipeline" 已 backlog，但训练闭环 helix 故意外推）
- **Atropos 集成** → **B**（helix 没有这个集成需求；如果客户用 Atropos 可以自己 export JSONL 喂）

## 15.3 借鉴价值评级

**低 / 不适用**。

- trajectory 压缩：**不适用**（B 外推决策）
- 训练闭环：**不适用**（B 外推决策）
- ShareGPT 兼容：**已对齐**

## 15.4 关键决策点

- 如果未来客户报"trajectory 太大下游训练吃不下"，是 helix 提供轻量压缩工具，还是文档化"推荐用 LlamaFactory 的 packing"？（这是产品决策不是技术决策）

---

# 横断面总结

## A. 15 维度 × 4 类型分布矩阵

| 维度 | 主要 gap 类型 | 借鉴价值评级 |
|------|---------------|----------|
| 1. Agent 循环 | D | 低 |
| 2. 自我改进 | A + B + **C** | **中-高** |
| 3. 记忆 | B + **C** | **中** |
| 4. 上下文管理 | D + C-边际 | 低 |
| 5. Provider | B + D | 低 |
| 6. 本地推理 | C | 低（场景错配） |
| 7. 沙箱 | D | 低 |
| 8. 子 Agent | D + C-边际 | 低 |
| 9. Cron 调度 | D + **C** | **中-高** |
| 10. 消息平台 | B | 不适用 |
| 11. MCP | **A + B** | **中-高**（MCP Client HTTP/SSE 真 gap；MCP Server 2026-05-27 复审推翻为 B，见 § 11.5） |
| 12. 扩展机制 | B + A | 低 |
| 13. UI | B + A | 低 |
| 14. 技能可移植 | **A** + B + D | **中-高**（多子项） |
| 15. RL / Trajectory | D + B | 不适用 |

**分布统计**：
- **A（已规划未实现）主导**：维度 11 (MCP Client HTTP/SSE)、维度 14 (Skill 多子项)
- **B（设计不做）主导**：维度 10 (消息平台)、维度 13 (末端 UI)、维度 15 (训练闭环)
- **C（真 gap）主导 / 含子项**：维度 2 (Curator 自动状态机)、维度 3 (memory 投毒防御)、维度 9 (Cron 注入扫描)、维度 14 (Skill supporting files / progressive loading)
- **D（helix 强 / 不同方向）主导**：维度 1、4、5、7、8 —— 大部分维度 helix 走对了自己的路径

**最关键观察**：helix 维度 1（Agent 循环）已经把 Hermes 单 turn 8 条能力（Stream L L1-L8）补完。**真 C 类 gap 集中在 5 个细分子项**：维度 2 / 3 / 9 / 14 + 维度 11 反直觉项。

## B. 实际值得抽出来补的 5 条（按优先级排序）

> **优先级排序原则**：(a) 是否 helix 已记录的真实痛点 / 阻塞 + (b) 实施成本 vs 价值比 + (c) 是否其他 gap 的前置依赖。
>
> **本清单只是"建议放进 M1 / M2 评估池"，不是排进 M1/M2 backlog**。最终时间排定由 helix 团队决定。

### #1 Cron prompt 注入扫描（含隐形 Unicode 检测）

- **是什么**：Hermes 的双层 prompt 注入扫描（创建 trigger 时严扫含 ZWJ/RTL/BOM 等隐形 Unicode、运行时拼完 skill 后宽扫）；helix 当前 trigger 完全没有这层。
- **为什么 #1**：(a) attack surface 真实存在（cron prompt 是管理员配置 + 拼 skill 内容两端可注入）；(b) 实施成本极低（regex 表 + 1 个 scan 函数 + 2 个 hook 点）；(c) 多租户企业 audit / 合规会问"prompt injection 怎么防"，这是显然的缺失。**M0→M1 Gate 收尾就该补，不用等 M1**。
- **实施时主要矛盾**：威胁模式库由谁维护（内置 vs per-tenant 可调）？运行时宽扫 hit 后是 reject + audit 还是 accept + warning？
- **建议**：**放进 M0→M1 Gate 清理项 或 M1 评估池靠前位置**。

### #2 Memory 投毒防御 + drift backup

- **是什么**：Hermes 的 MEMORY.md 加载时扫描威胁模式 → 中毒条目在系统提示快照里替换为 `[BLOCKED:...]` 占位符 + live 状态保留原文给用户审 + drift backup 防外部编辑被自动覆盖。
- **为什么 #2**：(a) helix 当前 memory 完全没有这层防御，企业客户合规审"如果 DB 被注入恶意 memory entry 会怎样"无法回答；(b) helix 的 memory 是 turn-level 动态召回不是 system prompt 常驻，所以借鉴的是**机制**而非**实现**（写时扫 + 读时占位符 + 用户能审）；(c) 是 M1-K J.7b-1 agent 自创建 skill 的前置（agent 写 memory 失控时这层是兜底）。
- **实施时主要矛盾**：write-time 扫还是 read-time 扫（前者更紧、后者跟 Hermes 思路一致）？威胁模式库的演进策略？
- **建议**：**放进 M1 评估池**。

### #3 Skill supporting files（references / templates / scripts）+ progressive loading

- **是什么**：Hermes Skill 目录约定 + 渐进式披露三层 API（metadata list → full SKILL.md → linked file on demand）。helix M1-K J.7b-3 / J.7b-6 已 backlog 但实施细节没定。
- **为什么 #3**：(a) M1-K J.7b 8 项是 helix 后续 Skill 进化的核心；(b) Hermes 的目录约定（references/ session 细节 + templates/ 可复用 + scripts/ 可执行）+ 三层 API 都是踩过坑的现成方案；(c) 借鉴可加速 J.7b 设计阶段，少走 1-2 轮 ADR。
- **实施时主要矛盾**：Postgres 存储 vs 文件系统对应关系（Hermes 是文件系统）？supporting files 是 JSONB 多列 vs ZIP archive 在 ObjectStore？
- **建议**：**M1 评估 J.7b 启动时直接参考 Hermes 设计**。

### #4 Curator 自动状态机（active / stale / archived 时间启发式转移）

- **是什么**：Hermes Curator 纯启发式（无 LLM）的三态自动转移 + `pinned` 字段保护 + 默认 30 天 stale / 90 天 archived。
- **为什么 #4**：(a) helix Skill 库 M1-K J.7b-1 上线 agent 自创建后会快速膨胀，没有自动 stale / archive 几个月后变得难用；(b) 实施简单（一个周期任务 + 时间戳判断 + state 更新），跟 helix 的 control-plane scheduler 模式同构；(c) M3 marketplace 之前必须有这层基础。
- **实施时主要矛盾**：时间阈值的 per-tenant 可配？跟 helix "manifest 显式声明"哲学协调（archived skill 不能引用，但 archive 是后台自动的）？
- **建议**：**M1 评估池**（J.7b-1 启动同期或稍晚）。

### #5 MCP Client HTTP/SSE transport（agent 沙箱接入外部 MCP 生态）

> **2026-05-27 复审重写**：原 #5 "MCP Server" 已推翻(详见 § 11.5)。本条是 reframe 后的真 gap。

- **是什么**：helix MCP client 当前 stdio only(M1+ Mini-ADR E-5 才有 HTTP/SSE)；2026 年大量公开 MCP server (GitHub / Postgres / Linear / Notion / Slack / filesystem 等)是 remote HTTP/SSE 形态,stdio-only 锁死本地 process,agent 沙箱无法触达 → agent 能力扩展面被卡。
- **为什么 #5(真 capability gap)**：(a) **agent 平台的核心竞争力 = 消费外部 MCP 生态**(详见 [memory:mcp-direction-client-only]),HTTP/SSE 不通就接入不了 → 直接卡 agent 能力面;(b) Mini-ADR E-5 已 backlog,只是顺位提前;(c) 实施成本 1.5 周(用 anthropic 官方 `mcp` Python SDK 而非自研);(d) 不动 helix 自身资源模型,纯加入"消费层"。
- **实施时主要矛盾**：per-tenant secret 隔离怎么做(HTTP header auth token 走 helix secret store)?远端 server 调用超时 + retry 策略?OAuth 配置先存还是先实现 flow(2026 公开 MCP server OAuth 使用率多高)?
- **建议**：**M0→M1 Gate capability uplift Sprint #5**(从 M1 评估池提前)。

## C. 明确不该照搬的清单（按理由分组）

### C.1 产品定位决定（helix backend platform vs Hermes end-user agent）

- **内置消息平台 adapter**（22+ Slack/Telegram/飞书/...）—— 业务系统责任
- **末端用户 CLI**（prompt_toolkit + Rich）—— 业务系统负责
- **末端用户 slash 命令** —— 业务系统的 UI 自己设计
- **end-user-facing 富媒体 / 卡片 / 按钮** —— 业务系统负责
- **手机 / 平板响应式 UI** —— Admin UI 是 desktop 操作员场景
- **MCP Server**（让 IDE 通过 MCP 反向调用 helix）—— 2026-05-27 复审推翻(详见 § 11.5)。agent 平台边界 = 消费 MCP 生态,不再造一个被消费的端点;helix REST API + Admin UI + SDK 已覆盖 operator/developer 场景;且 Hermes 那套 MCP server 工具集(conversations / messages / channels / events)是消息平台子系统术语,graft 到 helix 反向定义资源模型,违反 [memory:general-platform-positioning]。详见 [memory:mcp-direction-client-only]。

### C.2 多租户 / 合规决定

- **per-`$HERMES_HOME` 全局 MEMORY.md** —— helix per-(tenant, user) 是产品根基
- **agent 自动写 memory / skill 不审核**（M0 helix 完全不允许，M1-K 也仅是 DRAFT + 人审） —— 企业不接受
- **跨用户共享技能不审核** —— marketplace 在 M3 + 审核流程
- **inline shell `!`cmd``** —— sandbox + 多租户场景的 RCE 风险
- **平台过滤 `platforms: [macos, linux]`** —— helix sandbox 隔离了宿主 OS，无意义

### C.3 安全审计 / 走 PR 流程决定

- **用户级 `$HERMES_HOME/plugins/model-providers/` 热加载** —— 走 PR + ADR
- **用户级 hook plugin entry point** —— 同上
- **AST 扫 + 自我注册 tool** —— 显式装配更安全可审

### C.4 helix 已选择不同方向

- **多沙箱后端 horizontal diversity**（6-8 后端） —— helix 选 gVisor 深度 + Brain-Hands 隔离 + per-user workspace 持久化（M3 K8s 沙盒走 K8s 原生）
- **`ProviderProfile` dataclass + 30 字段** —— helix 用 `runtime_checkable Protocol` + LLMError 分类驱动 fallback
- **17 个 hook 系统** —— helix 走声明式 manifest + cosign Python 插槽（M1-F2）
- **自研 conversation_loop.py 4306 行** —— helix 用 LangGraph standard
- **iterative summary preservation** —— helix Mini-ADR L-2 显式选择不做（跟 Hermes 一致）

### C.5 helix 故意外推给客户

- **trajectory 压缩**（`trajectory_compressor.py`）—— 客户的训练流水线（LlamaFactory / Axolotl / unsloth）自己有 packing / sampling 工具
- **RL / SFT 训练闭环** —— 客户自己跑（helix 出 ShareGPT JSONL 是中性 contract）
- **Atropos 集成** —— 客户用 Atropos 自己 export JSONL 喂

### C.6 实现细节但价值边际

- **`default_aux_model` 字段**（每 provider 内置便宜模型）—— helix `ModelSpec.fallback` 已经能用
- **SUMMARY_PREFIX 300+ 字符控制文本** —— helix `<context-summary>` XML 包裹已做大部分工作
- **`api_mode = "codex_responses"`** —— helix 暂无 Codex 客户需求

## D. 给 helix 团队规划提示

1. **M0→M1 Gate 收尾期**：建议把 **#1 Cron prompt 注入扫描** 当作 K-stream 级别的 capability hardening 项纳入收尾。实施成本低、价值清晰、跟 Gate 的"7/7 沙盒安全用例"主题契合。

2. **M1 backlog 评估池建议加 4 条**：
   - #2 Memory 投毒防御 + drift backup
   - #3 Skill supporting files（同期跟 J.7b-6 一起做）
   - #4 Curator 自动状态机（同期或稍晚于 J.7b-1）
   - #5 MCP Server（同期跟 M1-I CLI 升级一起规划 operator experience）

3. **M2 backlog 不需要新增**。M2-A Durable / M2-B Multi-Agent fan-out 子 SSE / M2-C Memory archive / M2-D Eval Gate 持续改进 已经覆盖了剩余的"Hermes-derived 思路在 helix 的等价物"。

4. **M3 marketplace 之前必须有 #4 Curator 自动状态机**（否则 marketplace 上线后 skill 库会膨胀失控）。

---

# 附录 A — 评级标尺方法论

## A.1 4 分类（A/B/C/D）判定准则

### A 类判定

**满足全部**：
- helix 当前未实现（M0 没有）
- `docs/ITERATION-PLAN.md` 在 M1/M2/M3 有显式 backlog 条目（不是"可能要做"）
- helix 团队方向上认可（不是探索阶段）

### B 类判定

**满足任一**：
- 跟 helix 产品定位（业务无关多租户企业引擎）冲突
- 跟 helix 多租户 RLS 隔离冲突
- 跟 helix 合规审计 / 走 PR 流程的工程文化冲突
- helix 团队明确"外推给客户做"

### C 类判定

**满足全部**：
- helix M0 已实现核心路径
- 跟 Hermes 比能力深度有明显差距（不是实现细节差异）
- 不属于 A 类（M1+ backlog 没有这条）
- 不属于 B 类（没有设计上不做的理由）

### D 类判定

**满足任一**：
- helix 在这维度比 Hermes 做得深 / 完整
- helix 走不同方向但更适合自己定位
- 借鉴 Hermes 会让 helix 设计降级

## A.2 借鉴价值评级（高 / 中 / 低 / 不适用）判定准则

| 评级 | 触发条件 |
|------|---------|
| **高** | (a) 缓解 ITERATION-PLAN 已记录痛点 + (b) 实施成本远小于价值 + (c) 是其他 gap 前置依赖 中至少 2 条 |
| **中** | 价值清晰但当前不阻塞；M2 评估再上不会损失 |
| **低** | helix 已有功能等价物；差距是实现细节而非能力本身；或场景错配 |
| **不适用** | B 类设计性差异 |

# 附录 B — 与 ITERATION-PLAN 的对应

| 本报告 #N 建议条目 | helix ITERATION-PLAN 位置 | 状态 |
|------|--------------------------|------|
| #1 Cron prompt 注入扫描 | 未记录，建议进 M0→M1 Gate 或 M1 评估池 | **未有 backlog** |
| #2 Memory 投毒防御 + drift backup | 未记录，跟 J.3 + K.K6 + K.K7 同主题 | **未有 backlog** |
| #3 Skill supporting files | M1-K J.7b-6（已有 backlog，本报告建议参考 Hermes 加速设计） | 已 backlog |
| #3 Progressive loading | M1-K J.7b-3（已有 backlog） | 已 backlog |
| #4 Curator 自动状态机 | 未记录，跟 J.7a 同主题；跟 M3 marketplace 是前置 | **未有 backlog** |
| #4 Curator LLM 整合（4 动作） | M3 内部 marketplace 隐含需要 | M3 隐含 |
| #5 MCP Server | 未记录 | **未有 backlog** |
| 防学坏 prompt 约束 | M1-K J.7b-1（已 backlog，本报告建议参考 Hermes 的"什么算可写信号"分类） | 已 backlog |
| MCP Client HTTP/SSE | Mini-ADR E-5 M1+（已 backlog） | 已 backlog |

**未有 backlog 的 4 条建议**（#1 / #2 / #4 / #5）是本报告核心增值 —— 这些是 helix 团队当前规划里**完全没有的盲点**。

— EOF —
