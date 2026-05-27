# helix 能力提升迭代计划（8 项，最大化提前）

> **任务来源**：`helix-vs-hermes-gap.md` 5 条 + Memory 系统讨论 3 条新增 = **8 项**。
>
> **约束（用户已确认 + 修正）**：
> 1. **不重开 M0 阶段标签**
> 2. **能提前做的尽量提前 ── 不挑成本**，只看真正的依赖阻塞
> 3. Gate 收尾期可以扩展成一个 "capability uplift sprint"
>
> **重要修正**（vs 上一版）：上一版按"实施成本 ≤ 1 周"筛只塞了 1 项，是误读约束。本版按"真正硬依赖"重排，**6 项进 Gate sprint，2 项拆基础设施提前 + 启用等节奏**。
>
> **2026-05-27 复审修正**：原 #5 "MCP Server"（让 IDE 通过 MCP 调用 helix）被复审推翻 — gap doc 论据(企业开发者 / backend platform compatible / 实施成本中等 / Hermes-equivalent operator experience) 逐条不立。helix 是 server-side 多租户 backend 平台，不是 local-CLI，MCP server wrapper 没有真用户群体支撑；且 gap doc 列的工具集(conversations_list / messages_send / channels_list ...)是 Hermes 消息平台子系统术语，graft 过来违反 [memory:general-platform-positioning]。**#5 重定义为 "MCP Client HTTP/SSE transport"** — agent 平台的真正价值是消费外部 MCP 生态(GitHub / Postgres / Linear / Notion / filesystem 等)，不是反过来包装自己。详见 [memory:mcp-direction-client-only]。

---

## 8 项总览

| # | 能力 | 实施量 | 真正硬依赖 | 阶段 |
|---|------|--------|-----------|------|
| 1 | Cron prompt 注入扫描 | ~3 天 | 无 | **Gate sprint** |
| 2 | Memory 投毒防御 + drift backup | ~1.5 周 | 复用 #1 威胁模式库 | **Gate sprint** |
| 3 | Skill 附属文件 + Claude Code 标准 SKILL.md + Progressive Disclosure + 多层威胁防御（含 M1-K J.7b-3 / J.7b-6 提前 + 为 J.7b-1 高危 publish gate 预部署）| ~3.5 周 | 无 | **Gate sprint** |
| 4 | Curator 自动状态机 | ~1 周 | **真实价值要等 J.7b-1 agent 自创建上线** | **基础设施 Gate sprint + 启用 M1-K** |
| 5 | **MCP Client HTTP/SSE transport**（原"MCP Server"已推翻，见复审修正） | ~1.5 周 | 无 | **Gate sprint** |
| 6 | Memory hybrid retrieval（向量 + 全文 RRF） | ~1.5 周 | 无（直接 port J.5 现成代码） | **Gate sprint** |
| 7 | Memory 短期 → 长期自动凝结 | ~3-4 周 | **凝结策略调优要 M1 dogfood 数据** | **凝结引擎 Gate sprint + 策略 M1 调优** |
| 8 | Memory frozen snapshot / 前缀缓存优化 | ~1.5 周 | 无 | **Gate sprint** |

**结论**：
- **Gate sprint（capability uplift）= 6 项完整 + 2 项基础设施** = 8 项**全部启动**
- M1 阶段只剩 #4 启用 + #7 策略调优（属于"启用 + 调参"，不是新能力开发）

---

## 为什么这 8 项都能塞 Gate

我重新检查了每项的**真正阻塞**（不是"成本"）：

### 完全无依赖（5 项）

- **#1 Cron 注入扫描**：trigger 路径独立
- **#3 Skill 附属文件**：J.7a 已稳定，加 supporting_files 字段是独立 schema 变更
- **#5 MCP Client HTTP/SSE transport**：扩 MCP client transport(现 stdio only)，agent 沙箱可调远端 MCP server，Mini-ADR E-5 已 backlog
- **#6 Memory hybrid retrieval**：J.5 RAG 子系统的 hybrid + RRF + tsvector **已经 PR #161 落地**，是直接 port
- **#8 Memory frozen snapshot**：memory_recall_node 加一种模式 + manifest 配置

### 软依赖可解（1 项）

- **#2 Memory 投毒防御**：依赖威胁模式库 → 如果 #1 先做，把模式库抽到 `helix-common` 包，#2 直接复用。**就是 #1 → #2 的顺序而已，不是"等"**。

### 真硬依赖只有 2 项（拆 "基础设施 + 启用"）

- **#4 Curator 自动状态机**：自动 stale/archive 算法本身可以完成，但**没有 J.7b-1 agent 自创建上线，skill 库不会快速膨胀，Curator 没东西可整理**。
   - **基础设施部分（pinned 字段 + 状态机 + 周期 worker）可以提前做完**
   - 启用阈值调参（30天 stale / 90 天 archived）等 J.7b-1 上线后看真实数据调

- **#7 Memory 短期 → 长期凝结**：凝结引擎本体（识别反复事实 + LLM 总结 + 写 long-term）可以做完，但**"什么时候凝结" trigger 信号**要 M1 dogfood 数据反过来调，否则会大量误学。
   - **凝结引擎 + 防误学约束（参考 Hermes Skill review prompt）可以提前做完**
   - 触发器策略 + 阈值 M1 真实数据再调

---

## Gate sprint 内部排期

> 单人维护视角，并行做不动。按依赖顺序串行（部分项内部模块可并行）：

```
Week 1                  Week 2                  Week 3
─────────────────────────────────────────────────────
#1 Cron 注入扫描 ████
                ↓ 抽威胁模式库到 helix-common
#2 Memory 投毒防御              ████████

Week 4                  Week 5                  Week 6
─────────────────────────────────────────────────────
#5 MCP Client HTTP/SSE     ████████
                          (transport 扩展 + per-server config + OAuth 配置)
#6 Memory hybrid           ████████
                           (port J.5)
#8 Memory frozen snapshot          ████████

Week 7                  Week 8                  Week 9-10
─────────────────────────────────────────────────────
#3 Skill 附属文件          ████████████████
                          (含 ZIP 扩展)

Week 11                 Week 12                 Week 13
─────────────────────────────────────────────────────
#4 Curator 状态机           ████████
   (基础设施完整 + 启用走 J.7b-1 节奏)
#7 Memory 凝结引擎              ████████████████
   (引擎本体 + 防误学，触发策略 M1 调)
```

**总长**：单人节奏约 12-13 周；如果可以双人并行，可压到 6-8 周（独立项 #5 / #6 / #8 / #3 可同期）。

**最关键的依赖**：

- **#1 必须先做**（威胁模式库被 #2 复用）—— 顺序锁定
- **#5 / #6 / #8 / #3 完全并行**（无相互依赖）
- **#4 / #7 在 sprint 后期做基础设施**（启用按 M1-K J.7b-1 节奏，不挡前面项）

---

## 每项详细

### Week 1 — #1 Cron prompt 注入扫描

**实施面**：
- 新建 `helix-common/src/helix_common/threat_patterns.py`（威胁模式 regex + 隐形 Unicode 表，参考 Hermes `tools/cronjob_tools.py:68-200`）
- `services/control-plane/api/triggers.py` create/update 路径加严扫
- `services/control-plane/trigger_firing.py` 拼完 skill 后加宽扫
- 新 audit action `TRIGGER_PROMPT_INJECTION_BLOCKED`

**Risk**：误杀合法 prompt。**缓解**：strict / lax 两档分级（create 严扫 + runtime 宽扫，参考 Hermes 双层模式）。

### Week 2-3 — #2 Memory 投毒防御 + drift backup

**实施面**：
- `MemoryStore.write()` 写时扫（复用 #1 威胁模式库），命中拒 400 + audit
- `MemoryStore.recall()` 读时再扫，命中条目替换为 `[BLOCKED:...]` 占位符（live 状态保留原文供用户审）
- Drift backup：定期 hash + 备份；外部直接改 DB 时下次 recall 检测到 hash 不一致 → 触发 drift backup 流程
- 新 audit action `MEMORY_INJECTION_BLOCKED` / `MEMORY_DRIFT_DETECTED`

**Risk**：影响 J.3 已 deploy 的 write throughput。**缓解**：write 扫为可选 strict mode（per-tenant 配置），默认仅 read 扫做占位符。

### Week 4-5 — #5 MCP Client HTTP/SSE transport

> **2026-05-27 复审**：原 framing "MCP Server 暴露 helix 给 IDE" 已推翻(见文档头复审修正)。本节是 reframe 后的 scope。

**实施面**：
- 扩 MCP client transport：现 stdio only → 增 HTTP + SSE + StreamableHTTP 三种(对齐 Hermes / 公开 MCP server 标准形态)
- per-tenant manifest `mcp_servers[].transport`：`stdio | http | sse | streamable_http`，对应 config 字段(url / headers / etc)
- per-tenant secret 隔离：MCP server 的 auth header / token 走 helix secret store(复用 J.4 secret resolver)
- OAuth 配置层(只存配置不实现 flow)：`mcp_servers[].auth_type: none | bearer | oauth2` + OAuth client_id/scope 配置存储，OAuth refresh flow 留 Mini-ADR L.L8-MCP 后续
- 单元 + 集成测试：mock HTTP/SSE server 验协议合规，e2e 跑一个公开 MCP server(如 `mcp-server-time`)

**Risk**：HTTP/SSE MCP server 多种形态(rev draft 期协议变动)。**缓解**：用 anthropic `mcp` 官方 Python SDK 而非自研。**Risk**：tenant 配置 oauth 但 flow 未实现。**缓解**：oauth_type 字段先支持，运行时若选 oauth 则 400 "OAuth flow 未在本期实现"。

### Week 5 — #6 Memory hybrid retrieval（与 #5 并行）

**实施面**：
- `memory_item` 加 tsvector 列（迁移 0039）+ 自动 trigger 维护
- `MemoryStore.recall()` 改为 hybrid（向量 + 全文）+ RRF rerank（**直接 port `J.5 KnowledgeRetriever`**）
- K.K12 eval baseline 重新跑（向量 vs hybrid 对比 + 锁新 baseline）

**Risk**：某些 query 上 hybrid 比纯向量差。**缓解**：per-tenant manifest 可关闭 hybrid 回退纯向量。

### Week 5-6 — #8 Memory frozen snapshot（与 #5 / #6 并行）

**实施面**：
- `memory_recall_node` 加 "frozen snapshot" 模式：per-session 召回一次 + 整 session 复用（vs 默认 per-turn 召回）
- L.L1 prompt caching middleware 适配，cache_control 加在 memory block 末尾
- manifest `policies.memory_recall_mode: "per_turn" | "per_session"` 字段

**Risk**：session 内 memory 内容更新不及时（写完 memory 当前 session 看不到）。**缓解**：跟 Hermes 一样接受这个 trade-off（snapshot 是 frozen，下次 session 才生效）。

### Week 7-10 — #3 Skill 附属文件 + Claude Code 标准 SKILL.md + Progressive Disclosure + 威胁扫描

> **2026-05-27 复审**(三轮):
> - 第一轮:原 plan 漏了 ZIP 格式标准对齐 + progressive disclosure。补做这两件后,Sprint 范围接近"完整 Skill 子系统升级"。
> - 第二轮:发现 ZIP 内容威胁扫描完全没做(相比 Sprint #1 / #2 strict scan + drift 模式不对称)→ 追加 Mini-ADR U-21(写时 strict reject + 读时 drift / context-scope redact)。
> - 第三轮:用户追问"高风险或恶意 skill 能识别吗?" — 当前 scanner 漏 base64 / 空格 / Unicode homoglyph / 全角等一层混淆,中文 injection 完全裸奔,行为级 attack(skill 声明高危工具 + 含恶意脚本)只靠人审。→ 追加 C 方案 3 个 ADR:U-22 混淆防御 / U-23 中文 pattern / U-24 高危 publish gate(M0 几乎不触发,**为 M1-K J.7b-1 agent self-authored skill 提前布防**)。
>
> 详见 [STREAM-UPLIFT-DESIGN.md § 4](../streams/STREAM-UPLIFT-DESIGN.md)。

**实施面**:
- 对齐 Claude Code 标准:ZIP 用 `SKILL.md`(YAML frontmatter + body)+ 任意子目录;helix 字段放 `helix:` 命名空间
- `skill_version` 加 `supporting_files: JSONB` + `lazy_load: BOOL` + `content_hash: BYTEA`(迁移 0042);5MB cap;ObjectStore 不做(JSONB 够)
- 新 `skill_view(skill_name, path)` 工具:`SKILL.md` 跟 supporting files 对称访问;**读时 drift 检测 + context-scope re-scan + redact**
- agent_factory:**progressive disclosure 默认架构** —— system prompt 只注 skill summary;per-skill `lazy: false` 默认保留现有 eager 行为
- 完整 Admin UI:CodeMirror 6 + 5 mutation 路径(edit / upload / delete / rename / ZIP)+ diff 视图
- ZIP backward compat 双读:老 ZIP(skill.yaml + prompt.md + tools.txt)能 import + warn
- Path 校验:字符 + 扩展名 + 大小 allowlist;子目录命名自由(对齐 Claude 标准);Oracle defense(整 ZIP reject 不暴露细节)
- **U-21 写时威胁扫描**:ZIP import + 单文件 mutation 都走 Sprint #1 strict scan(基础设施 100% 复用);finding → reject + audit `SKILL_PROMPT_INJECTION_BLOCKED`
- **U-21 P0 alert**:`HelixUpliftSkillDriftDetected`(drift 几乎必是 SQL 注入或内部 actor,跟 memory drift 同级)
- **U-22 混淆攻击防御**(C 方案):`scan_for_threats` 内部加 base64 解码 + NFKC Unicode 归一 + 空格 collapse pre-processing — 跨 trigger / memory / skill 三子系统升级;K.K12 baseline 回归保护无误报
- **U-23 中文 prompt injection patterns**(C 方案):pattern 库追加 ~12 个中文模式(5 大类:直接 / 系统提示泄露 / 角色劫持 / 限制解除 / 反事实 / 权威伪装)
- **U-24 高危 publish gate**(C 方案):skill 含 `exec_python` / `http` / `exec_shell` 或 `scripts/*` → `high_risk: true`;PATCH status=active 时,高危 + 非 admin → 403 + audit `SKILL_HIGH_RISK_ACTIVATION_BLOCKED`;M0 透明(全 admin actor),**M1-K J.7b-1 上线 agent self-authored skill 自动启用**

**借鉴源**:
- Claude Code 标准 SKILL.md(`~/.claude/skills/` 实测)— 单文件 + 任意子目录
- Hermes 维度 14 — supporting files 思路(但 helix 子目录命名跟 Claude Code 对齐,不照搬 Hermes 强制 references/templates/scripts)
- Sprint #1 + #2 防线 — 威胁扫描 + drift 检测基础设施全复用,trigger / memory / skill 三条"内容进库"路径对称防御

### Week 11-12 — #4 Curator 自动状态机（基础设施完整）

**实施面**：
- `SkillRow` 加 `pinned: bool` + `last_activity_at: timestamptz`（迁移 0041）
- 新建 `services/control-plane/src/control_plane/skill_curator.py`：周期 worker，纯启发式（无 LLM）三态转移
- 默认阈值 30 天 → stale / 90 天 → archived，per-tenant 可配
- Admin UI Skills page 加 pin 操作 + stale/archived 状态显示

**完成 = 基础设施 + 默认阈值**。**启用 = J.7b-1 上线后跑一段看真实数据再调**（不是阻塞，而是"启用了但 30/90 阈值可能 J.7b-1 后调成 7/30"）。

### Week 11-13 — #7 Memory 短期 → 长期凝结引擎

**实施面**：
- 新建 `services/control-plane/src/control_plane/memory_consolidator.py`：识别"反复出现的事实" trigger 信号
- 凝结 LLM 调用：用辅助 model（如 Haiku）总结 N 轮对话窗口 → 写入 long-term memory
- 防误学约束（参考 Hermes Skill review prompt 的"什么坚决别写"4 条分类：环境性失败 / 负面工具断言 / session-specific transient errors / one-off task narratives）
- M2-C archive 流水线接口预留

**完成 = 引擎本体 + 默认 trigger 信号**。**调优 = M1 dogfood 数据反过来调"什么时候凝结" + "凝结多深"**。

---

## 留给 M1+ 的事项（仅启用 + 调参，非新开发）

| 项 | M1 要做什么 |
|---|-----------|
| #4 Curator 启用 | J.7b-1 agent 自创建上线后跑 2-4 周，按真实膨胀率调阈值（默认 30/90 可能改 7/30） |
| #5 MCP Client OAuth flow | OAuth refresh + 跨 session 持久 token，等公开 MCP 生态 OAuth 使用率上来再做(Mini-ADR L.L8-MCP) |
| #7 凝结策略调优 | M1 dogfood 跑完看哪些 trigger 信号误学率高，调防误学约束 |
| #8 frozen snapshot 启用条件 | 看客户 token 成本报告，memory recall cache miss > 15% 才让 per_session 成为默认 |

**M2 才出现的事项 = 0**。所有"能力面"都在 Gate sprint 启动；M2-C Memory archive 层（已规划）独立于本计划 8 项。

---

## 整体节奏

```
现在 ──── M0→M1 Gate（capability uplift sprint 12-13 周）──── M1 启动
                          │
                          │ 期间并行：
                          ├─ 8 项全部启动
                          ├─ Dogfood 平行 30 天持续跑
                          ├─ K stream（capability hardening）原有项收尾
                          └─ 各项 PR 按零债 6 条核验逐个 merge
                          │
                          ▼
                       M1 启动
                          │
                          ├─ M1-A Sandbox 池化（原 backlog）
                          ├─ M1-B 数据生命周期硬化（原 backlog）
                          ├─ M1-K J.7b 8 项（原 backlog，本计划 #3 / #4 已并入）
                          │     └─ J.7b-1 上线触发 #4 Curator 启用调参
                          ├─ M1-I CLI 升级（独立项，MCP Server 框架已确认不做，见 [memory:mcp-direction-client-only]）
                          └─ M1 dogfood 数据反过来调 #4 + #7 阈值
                          │
                          ▼
                       M2 启动
                          │
                          └─ M2-C Memory archive 层（原 backlog，#7 提前做完后 archive 流水线直接对接）
```

---

## 关键 Risk + 缓解

### Risk 1：12-13 周 Gate sprint 单人扛不住

- **缓解**：必要时拆"前 6 周（#1 #2 #5 #6 #8）+ 后 6-7 周（#3 #4 #7）"两个 sprint，中间留 1-2 周 dogfood observation 窗口
- 或者：双人并行（独立项 #5 #6 #8 + #3 之间无依赖）可压到 6-8 周

### Risk 2：Gate sprint 期间 dogfood 平行运行 30 天的"平行参考点"被搅动

- **缓解**：每项 PR merge 前 fresh staging 数据集回放 K.K12 eval baseline；任何 baseline 退化 ≥ 5% 卡 PR

### Risk 3：#4 / #7 的"基础设施先做"可能浪费时间（如果 J.7b-1 / M1 dogfood 数据出来后发现设计需要重做）

- **缓解**：#4 / #7 都按"模块化 + 可关闭" 设计（manifest 字段 enabled = true/false），即使阈值大改也不会推翻基础设施
- 接受 10-20% 重做风险换 6-12 个月的提前价值

### Risk 4：#3 Skill 附属文件改 schema（migration 0040）+ #4 改 schema（0041）同期跑

- **缓解**：先 #3 上 main 跑 1 周观察，再上 #4；schema 变更不并行

---

## Non-Goals（明确不做）

- 不动 ITERATION-PLAN 已有的 M1-A 到 M3 内部排序
- 不创造新 stream 名（K.K16 沿用 K stream 编号；其他直接以 # 编号）
- 不评估 ROI / 商业优先级（仅技术 + 依赖视角）
- 不替团队决定具体 PR 拆分（每项的"实施面"只到模块级，不到函数 / 行）
- 不承诺 12-13 周一定能完成（单人节奏 + 实际 dogfood 干扰因素无法精确预测）

— EOF —
