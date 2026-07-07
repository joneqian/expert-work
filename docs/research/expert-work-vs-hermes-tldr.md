# Expert Work vs Hermes — 5 分钟看懂版

> 这是 `expert-work-vs-hermes-gap.md`（754 行详细版）的精简结论。
> 看不懂或想要事实依据，回头翻详细版。

## 一图看完（15 维度）

| 维度 | Hermes 怎么做 | Expert Work 现在怎么做 | 该不该补 | 一句话理由 |
|------|---------------|------------------|---------|-----------|
| **1. Agent 主循环** | 自研 4000 行 ReAct + 5 类重试 + 工具并发 | LangGraph 标准 + Stream L 8 条单 turn 能力（直接从 Hermes 学来的） | ❌ 不用补 | 已经学完了 |
| **2. 自我改进** | 后台自动 fork agent 写 memory / skill + Curator 每 7 天自动整理库 | 走 trajectory → 人审 → 入 eval dataset；agent 自动写在 M1-K backlog | ⚠️ **该补** Curator 自动整理 | Expert Work 之后 agent 自动写 skill 上线后库会膨胀，没有自动整理几个月就乱了 |
| **3. 记忆系统** | MEMORY.md 加载时扫毒 + 中毒条目占位符 + 防外部编辑覆盖 | 三层 pgvector + 多租户 RLS + DLQ；**完全没有投毒防御** | ⚠️ **该补** 投毒防御 | 企业客户审"如果 DB 被注入恶意 memory 会怎样" 当前答不上 |
| **4. 上下文管理** | preflight + 中段摘要 + 多 pass | 同上（Stream L.L2 学的） | ❌ 不用补 | 已经学完了，差异在多模态 token 估算（J.6 dogfood 再看） |
| **5. Provider 抽象** | 30 个 bundled provider + 用户级 plugin 热加载 | 9 个 provider + tree fallback + 走 PR 流程加新 provider | ❌ 不用补 | 用户级热加载跟 Expert Work 安全审计冲突；30 个数字本身没意义 |
| **6. 本地推理** | 识别本地端点 + 自动放大 timeout + Ollama 探针 | 通过 self-hosted provider 接，无任何特殊处理 | ❌ 暂不补 | 企业客户主战场是云端 API，本地推理场景占比低 |
| **7. 沙箱** | 6-8 后端横向多样性 | 仅 Docker + gVisor + Brain-Hands 物理隔离 + 持久 workspace | ❌ 不用补 | Expert Work 选了"深度安全"路径，比 Hermes 强 |
| **8. 子 Agent / 并行** | 工具黑名单 + ThreadPool + 默认 1 层 | 结构性深度 cap + cycle detection + asyncio.gather + 6 态状态机 | ❌ 不用补 | Expert Work 全面强 |
| **9. Cron 调度** | 文件存储 + 简单调度 + **prompt 注入扫描含隐形 Unicode** | 数据库驱动 + DLQ 重试 + per-tenant quota + 跨租户 + **没有 prompt 注入扫描** | ⚠️ **该补** prompt 注入扫描 | cron prompt 是 attack surface（管理员配 + 拼 skill 两端可注入），Expert Work 完全没防御 |
| **10. 消息平台** | 内置 22+ Slack/Telegram/飞书/钉钉 等 | 不内置；业务系统经 webhook 接入 | ❌ 不该照搬 | Expert Work 是 backend，末端用户对接是业务系统责任 |
| **11. MCP** | Client（stdio/HTTP/SSE）+ Server（暴露给 Claude Code/Cursor） | Client（仅 stdio，HTTP/SSE 在 M1 backlog）；**无 Server** | ⚠️ **该补** MCP Server | 企业内部开发者用 Claude Code/Cursor 写 manifest 是真实场景，让他们在 IDE 里直接看 Expert Work sessions/runs 价值大 |
| **12. 扩展机制** | 17 个 hook + 用户级 plugin + AST 自我注册 | 声明式 manifest + 显式装配 + 中间件链硬编码；M1-F2 Python 插槽 | ❌ 不用补 | Expert Work 走"显式审核"路径，hook 热加载跟 governance 冲突 |
| **13. UI** | CLI（prompt_toolkit 15000 行）+ 末端消息平台 UI | Admin UI（React + Antd 17K 行）；CLI 在 M1-I backlog；无末端 UI | ❌ 不用补 | M1-I 已 backlog；末端 UI 是业务系统责任 |
| **14. Skill 可移植** | SKILL.md + references/templates/scripts 目录 + 渐进式加载 + agent 自创建 | name@version + Postgres + DRAFT/ACTIVE/ARCHIVED + 审计；M1-K 已规划 8 项 | ⚠️ **该补** Skill 附属文件 + 防"学坏"约束 | M1-K 上线时直接抄 Hermes 的目录约定 + "什么算可写信号 / 什么坚决别写"分类，少走 1-2 轮 ADR |
| **15. RL / 轨迹** | ShareGPT JSONL + 用 OpenRouter 压缩超大轨迹 + 提到 Atropos | ShareGPT JSONL（per-tenant prefix）+ Curation + Eval Dataset；无压缩、无训练闭环 | ❌ 不该照搬 | 训练闭环 Expert Work 故意外推给客户（用客户自己的 LlamaFactory/Axolotl），轨迹压缩同理 |

---

## 5 条该补，按优先级

> **优先级 = 是否真实痛点 + 实施成本 + 是否其他 gap 的前置**。
> 这只是"建议放进评估池"，最终时间排定由 Expert Work 团队定。

### #1 Cron 触发器加 prompt 注入扫描（含隐形 Unicode）

- **是什么**：cron 触发器的 prompt 字段（管理员配的 + 拼 skill 内容的）当前完全没扫描；Hermes 有双层扫描（创建严扫含 ZWJ / RTL 等隐形字符，运行时拼完 skill 后宽扫）。
- **为什么排 #1**：实施成本最低（一个 regex 表 + 两个 hook 点），但 attack surface 真实，企业客户审计必问。**建议 M0→M1 Gate 收尾就补，不用等 M1**。

### #2 Memory 系统加投毒防御

- **是什么**：Hermes 把 memory 加载时扫一遍威胁模式 → 中毒条目在系统提示里替换为 `[BLOCKED:...]` 占位符 → live 状态保留原文给用户看 + 删除。Expert Work 现在没这层。
- **为什么排 #2**：企业合规会问"如果 DB 被注入恶意 memory entry 会怎样"，Expert Work 当前答不上；而且是后续 agent 自动写 memory 上线后的兜底防御。建议进 M1 评估池。

### #3 Skill 加附属文件（references/templates/scripts）+ 渐进式加载

- **是什么**：现在 Expert Work Skill 只有一段 prompt 文字；Hermes 把 Skill 做成目录（SKILL.md 主文件 + references/ 参考材料 + templates/ 模板 + scripts/ 脚本），按需加载。
- **为什么排 #3**：Expert Work M1-K J.7b-6 / J.7b-3 已经规划了，直接抄 Hermes 设计能省 1-2 轮 ADR；复杂工作流单一 prompt 撑不住。

### #4 Skill 库加自动整理（active → stale → archived）

- **是什么**：纯启发式按时间转移状态（30 天没用 → stale，90 天没用 → archived），用户可 pin 保护固定 skill。Expert Work 现在 skill 状态机全靠管理员手动 PATCH。
- **为什么排 #4**：M1-K 一旦上 agent 自动写 skill，库会快速膨胀；没有自动整理几个月就乱。是 M3 marketplace 的前置。

### #5 暴露 MCP Server（让 Claude Code / Cursor 可以反向调 Expert Work）

- **是什么**：把 Expert Work 的 conversations / runs / approvals 通过 MCP 协议暴露给 IDE，让企业内部开发者在 Cursor / Claude Code 里直接查看 Expert Work 跑的 sessions、回复 approval、读 trajectory。
- **为什么排 #5（反直觉）**：企业内部开发者用 IDE 写 manifest / debug agent 是 Expert Work 真实使用场景；跟 Expert Work backend 定位完全不冲突（MCP Server 也是面向"专业用户"不是末端用户）；实施中等成本（FastMCP lib 包装现有 API）。

---

## 不该照搬的，按理由分组

### 因为 Expert Work 是 backend 平台（业务系统负责对末端用户）
- 内置 Slack / Telegram / 飞书 / 钉钉 / WhatsApp / 微信 等 22+ 消息平台 adapter
- 末端用户 CLI（prompt_toolkit + Rich）
- 末端用户的 slash 命令（/model / /memory 等）
- 富文本卡片 / 按钮 / 移动端响应式

### 因为多租户隔离 + 合规审计
- 全局共享 MEMORY.md（per-`$HERMES_HOME`）
- Agent 自动写 memory / skill 不经人审
- 跨用户共享技能不审核（marketplace 在 M3 + 必有审核流程）
- inline shell `!`cmd`` 预处理（多租户场景的 RCE 风险）
- 用户级 plugin / provider 热加载（绕过 PR + ADR 流程）

### 因为 Expert Work 已经走了别的路径（更适合自己定位）
- 多沙箱后端横向多样性（Expert Work 选 gVisor 深度 + Brain-Hands 物理隔离）
- 17 个 hook 系统（Expert Work 走声明式 manifest + Python 插槽）
- 自研 4000 行主循环（Expert Work 用 LangGraph 标准）
- AST 扫描自我注册 tool（Expert Work 显式装配更可审）

### 因为 Expert Work 故意外推给客户
- Trajectory 压缩（客户自己的 LlamaFactory / Axolotl 有 packing）
- RL / SFT 训练闭环（Expert Work 出 ShareGPT JSONL 是中性 contract，训练在客户侧）
- Atropos 集成

### 因为价值边际（实现细节差异，不是能力差异）
- `default_aux_model` 字段（Expert Work `ModelSpec.fallback` 已经能用）
- `SUMMARY_PREFIX` 300+ 字符控制文本（Expert Work `<context-summary>` XML 包裹已做大部分工作）
- 30 个 provider 数量本身（数字不等于价值）

---

## 整体观察

Expert Work 在大部分维度上要么**已经把 Hermes 能学的学完了**（Stream L 8 条），要么**走了更适合自己定位的路径**（沙箱 / 子 Agent / Provider / 多租户）。

**真 gap 集中在 4 个不起眼的细节**：

- 维度 9 **Cron prompt 注入扫描**（安全盲点）
- 维度 3 **Memory 投毒防御**（合规盲点）
- 维度 14 **Skill 附属文件**（J.7b 启动时设计盲点）
- 维度 2 **Curator 自动整理**（J.7b 上线后必然踩的坑）

加上 1 个反直觉的产品方向：
- 维度 11 **MCP Server**（让 IDE 用户接入 Expert Work 是真实场景）

——这 5 条就是"建议放进 M1 评估池"的全部，其他都是 noise。

---

— EOF（事实细节看 `expert-work-vs-hermes-gap.md` 详细版；事实底稿看 `hermes-deep-dive.md` + `expert-work-current-state.md`）—
