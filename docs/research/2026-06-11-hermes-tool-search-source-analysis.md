# Hermes Tool Search 源码级分析 — 渐进式工具披露与 helix TE-6 对照

> 日期：2026-06-11
> 分析对象：`hermes-agent` 本地源码（`tools/tool_search.py` @ `7427b9d58`）
> 关联 PR：hermes #34493（`369075dc9` feature + `7427b9d58` scope 修复）
> 对照系统：helix Stream TE-6 / TE-6b（`orchestrator/tools/find_tools.py`、`tools/assembly.py`、`tools/registry.py`）
> 性质：纯研究文档，无代码改动。结论部分含对 helix 的可吸收点评估。

---

## 1. 问题背景

Agent 每轮请求要把所有注册工具的完整 JSON schema 发给模型。MCP 生态下工具数从个位数涨到几十上百个，schema 占用变成"上下文税"：Hermes 社区实测一个 session 的工具定义占用从 ~100k tokens 降到 ~19k（启用 Tool Search 前后）。Hermes 的回应是 **渐进式工具披露（progressive tool disclosure）**：超过阈值时把 MCP/插件工具从模型可见列表撤下，换成三个桥接工具按需搜索、按需加载、按需调用。

helix 在 Stream TE-6/TE-6b 已实现同类机制（`find_tools` 元工具 + MCP 永远延迟注册），但披露模型不同（见 §8）。本报告先把 Hermes 实现拆到源码层，再做两系统对照。

## 2. 架构总览

三层接线，职责分离：

```
model_tools.get_tool_definitions()        组装层：sanitize 之后、返回之前做折叠
  └─ tool_search.assemble_tool_defs()     纯函数：classify → 估算 → 门控 → 替换
model_tools.handle_function_call()        dispatch 层：search/describe 内联，tool_call 递归 unwrap
agent/tool_executor.py                    执行层：并发路径独立 unwrap + 第二道 scope gate
```

- `tools/tool_search.py`（735 行）：**零进程状态、纯函数**。配置解析、分类、token 估算、门控、BM25 目录检索、桥接 schema、dispatch、scope 工具函数全在此。
- `model_tools.py:506-534`：组装层接入点。刻意放在 schema sanitize **之后**、作为返回前最后一步；assembly 幂等（重复调用是 no-op）。
- `model_tools.py:922-985`：bridge dispatch + 第一道 scope gate。
- `agent/tool_executor.py:281-337`（并发路径）与 `:801+`（串行路径）：unwrap + 第二道 scope gate。
- `tests/tools/test_tool_search.py`：39 个测试。

### 模型可见的三个桥接工具

| 工具 | 职责 | 关键参数 |
|---|---|---|
| `tool_search(query, limit?)` | BM25 搜延迟目录，返回 name+description（截 400 chars） | limit 默认 5，clamp 到 max 20 |
| `tool_describe(name)` | 返回单个工具完整 parameters schema | — |
| `tool_call(name, arguments)` | 调用底层工具，执行链 unwrap | arguments 接受 object 或 JSON 字符串 |

三个名字是保留字（`BRIDGE_TOOL_NAMES` frozenset），registry 的 override 保护拒绝同名注册。

## 3. 机制详解（带源码证据）

### 3.1 分类：核心白名单公理

`toolsets._HERMES_CORE_TOOLS`（~30 个：terminal/read_file/write_file/patch/search_files/todo/memory/browser_*/web_search/skills_*/clarify/delegate_task 等）**永不延迟**。`is_deferrable_tool_name()`（`tool_search.py:163-186`）的判定顺序：

1. 桥接工具名 → 不可延迟（防自指）
2. 核心白名单 → 不可延迟（**注释明示：即使该工具技术上来自插件 toolset 也不延迟，防意外 shadowing**）
3. registry 查不到 → 不可延迟（unknown 留在可见列表，安全方向）
4. `mcp-` 前缀 toolset → 可延迟
5. 其余（非核心插件）→ 可延迟

设计公理（模块 docstring 原话）："Always-load means always-load. No exceptions." 配套测试 `test_core_tools_never_defer` / `test_unwrap_rejects_core_tool_attempt`（连通过 `tool_call` 绕道调核心工具都被拒——核心工具不在 deferrable 集合内，scope gate 直接挡）。

这条公理的来源是生产事故：OpenClaw 曾出现隔离 cron turn 中工具静默消失的回归，Hermes 的结论是**根本不写出 "drop core tool" 的代码路径**，而不是靠测试防回归。

### 3.2 阈值门控：auto / on / off 三态

`should_activate()`（`tool_search.py:234-258`）：

- `off` → 永不激活；`on` → 有≥1 个可延迟工具即激活
- `auto`（默认）→ 可延迟 schema 估算 tokens ≥ `context_length × threshold_pct(10%)` 才激活
- **拿不到 context length 时退化为固定 20k tokens 截断**——注释称这是 "Anthropic 和 OpenAI 都观察到质量下降的悬崖位置"

token 估算 = `len(json.dumps(td)) / 4`（`CHARS_PER_TOKEN = 4.0`，`tool_search.py:51-55`）。注释对方向性有明确论证：低估→该开没开（漏激活），高估→不该开开了（多余间接层）；4.0 偏向低估 = 更安全的默认。**门控只需要数量级精度，不需要真 tokenizer**——这个论证对 helix HX-1 的适用范围判断有参考价值（见 §8.3）。

门控是**双向自适应**的：每次 assembly 重新评估，MCP server 断开导致可延迟集缩水到阈值下，下次重建自动退出折叠模式。

### 3.3 catalog：无状态，每次重建

catalog（`build_catalog`，`tool_search.py:321-344`）不跨 turn、不跨 assembly 缓存，每次从当前 tool-defs 列表重建。模块 docstring 直接点名教训来源：

> "This is the lesson from OpenClaw's cron regression (openclaw/openclaw#84141): a session-keyed catalog that drifts out of sync with the live tool registry produces silent tool dropouts."

代价是每次 `tool_search`/`tool_describe` dispatch 都要重建 catalog 并对每个工具查一次全局 registry（`_classify_source`）。N<500 有界，注释认为可接受。**一致性优先于 CPU** 的取舍。

### 3.4 BM25 检索：手写内联 + 两个工程细节

`_bm25_score`（k1=1.5, b=0.75，~30 行）不引依赖。两个值得注意的细节：

1. **检索文本构造**（`_entry_search_text`，`tool_search.py:289-304`）：工具名拆词（snake_case/点/横线/冒号全部断开）+ description + **顶层参数名**。schema body 故意不索引——"indexing them adds noise without improving recall in our measurement"（有过实测）。
2. **零 IDF 兜底**（`search_catalog`，`tool_search.py:410-415`）：当目录里每个工具都含查询词（如全部叫 `github_*`，查 "github"），IDF 为零、BM25 全员零分。此时退化为工具名 substring 匹配（统一给 0.1 分）。没有这个兜底，最常见的"按服务名找工具"查询反而搜不到。

### 3.5 桥接 schema：每个字节都付费

`bridge_tool_schemas()`（`tool_search.py:426-510`）三个 schema 合计 ~300 tokens/轮（这是激活后的固定开销）。描述文案有两处防呆：

- `tool_search` 描述末尾："Tools listed at the top of this system prompt are already available and do not need to be searched."——防模型搜已直接暴露的工具。
- `tool_describe` 对非延迟名字的错误信息是教学式的："If you see it in the tools list already, call it directly; otherwise check the spelling against tool_search."

`deferred_count` 动态注入 search 描述（"Search 47 additional tools..."），给模型目录规模感。

### 3.6 dispatch 与 unwrap：透明性的实现

`tool_call` 的处理分两个站点，对应 Hermes 的两条执行路径：

**dispatch 层**（`model_tools.handle_function_call:922-985`）：`tool_search`/`tool_describe` 是纯目录读，内联返回 JSON。`tool_call` 经 `resolve_underlying_call()` 解析后**递归调用 `handle_function_call(underlying_name, ...)`**——所有 pre/post hook、guardrail、审批流对底层工具名照常触发，桥接层对治理面不可见。

**执行层**（`tool_executor.py:281-314`）：agent 主循环的工具执行在进入 checkpoint/hook 之前先 unwrap——`function_name`/`function_args` 直接换成底层工具，**但 `tool_call.function` 原始条目不动**（注释："the conversation transcript and the matching tool_call_id are preserved exactly as the model emitted them"）。结果：transcript 记录模型的原话（桥接调用），执行链、活动流、轨迹记录看到真实工具。两边都不撒谎。

`resolve_underlying_call`（`tool_search.py:680-710`）顺带处理三类畸形输入：arguments 是 JSON 字符串（解析）、`tool_call` 调桥接工具自身（拒绝递归）、非延迟名字（拒绝并教学）。

### 3.7 双重 scope gate：越权洞与纵深修复

原始版本的洞（`7427b9d58` 修复前）：bridge dispatch 从**进程全局 registry** 读目录。受限 toolset 的 session（subagent、kanban worker、curated gateway session）能通过 `tool_search` 看到、通过 `tool_call` 调到**整个进程注册的任何工具**——toolset 权限隔离被桥接层完全绕过。

修复是两道闸，不是一道：

1. **dispatch 层**（`model_tools.py:939-979`）：catalog 用 session 自己的 `enabled/disabled_toolsets` 重建（`get_tool_definitions(skip_tool_search_assembly=True)` 拿折叠前的真实 scoped 列表）；`tool_call` 递归前用 `scoped_deferrable_names()` 再验一次。注释明确这是 defense in depth："this gate additionally rejects any tool the session was not granted... even if the catalog scoping above regressed."
2. **执行层**（`tool_executor.py:292-314`）：unwrap 直接派发底层工具、**绕过了 dispatch 层的 bridge 分支及其检查**，所以在 unwrap 处独立再查一次。out-of-scope 的拒绝发生在 **checkpoint/hook/guardrail 之前**（`:324-331`），不留任何副作用。

执行层的 scope 集合缓存在 agent 对象上，缓存键 = `(registry._generation, enabled, disabled)`（`tool_executor.py:159-166`）——MCP server 重连使 registry generation 递增，缓存自动失效。常态是一次 dict 比较，不是每个 tool call 重建工具列表。

配套测试：`test_search_catalog_is_scoped_to_session_toolsets`、`test_tool_call_rejects_out_of_scope_tool`、`test_bridge_dispatch_does_not_pollute_global_resolved_names`（连"bridge dispatch 不得污染进程全局 `_last_resolved_tool_names`"这种侧信道都有断言）。

### 3.8 故障模式设计：处处 fail-open

每个集成点都把"功能故障"映射为"退回旧行为"，而不是"工具消失"：

| 故障点 | 行为 |
|---|---|
| 组装层 assembly 抛异常 | `logger.warning` + 直通不折叠（`model_tools.py:533-534`，"never break tool loading"） |
| `is_deferrable_tool_name` 内部异常 | 返回 False → 工具留在可见列表 |
| 配置 typo / 类型错误 | `ToolSearchConfig.from_raw` clamp + safe default，不 raise |
| 配置文件读不到 | 默认 auto/10% |
| classify 遇到 unknown 工具 | 留在 visible（不延迟） |

与 §3.1 的白名单公理同源：**这个功能的所有故障方向都被设计成"多花 token"，绝不是"少一个能力"**。

## 4. 测试覆盖（39 个，按组）

- 配置解析 7：bool/dict/缺省/非法值/clamp
- 分类 4：核心白名单、桥接保留名、unknown 留可见
- 门控 6：off/on/auto、阈值边界、无 context length 的 20k 兜底、估算比例性
- 检索 4：相关命中、无关空结果、substring 兜底、limit
- assembly 3：无延迟直通、低于阈值直通、**幂等**（桥接已在输入中时 no-op）
- dispatch 6：必填参数、非延迟拒绝、arguments 字符串/对象双形态、坏 JSON、拒自指递归
- 集成 4：经 `handle_function_call` 全链路、核心工具与海量 MCP 共存、unwrap 拒核心工具
- **scope 安全 4**：catalog 限 session、out-of-scope tool_call 拒绝、全局状态不污染、helper 语义

安全修复（`7427b9d58`）带着 4 个针对性测试合入——洞的修复和回归防护同 commit。

## 5. 源码层面的真实弱点

1. **tokenizer 是 `[A-Za-z0-9]+`（`tool_search.py:280`）——中文工具描述 BM25 完全失效**，只剩工具名 substring 兜底。对中文 MCP 生态是硬伤。
2. **chars/4 对 CJK 严重低估**（中文 ~1 char/token，被按 0.25 估）：中文 schema 重的目录可能该激活不激活。方向与其"偏低估更安全"的论证一致，但 CJK 下偏差幅度是 4 倍，不再是"slightly"。
3. **`tool_describe` 返回完整 parameters 不截断**（`tool_search.py:646-654`）：search 结果截 400 chars，describe 不截——话痨 MCP schema 照样灌进对话历史。官方 trade-off 文档承认此项。
4. **阈值用配置文件默认模型的 context length**（`_resolve_active_context_length`，`model_tools.py:539-558`），不是当前 session 实际模型——gateway 多模型场景下门控阈值可能错配。
5. **describe 结果进对话历史拿不到 prompt cache prefix 优化**（官方自列）；动态目录与历史中旧 schema 的一致性风险（MCP server 中途更新定义）。
6. catalog 每次 dispatch 重建 + 逐工具查 registry：一致性换 CPU，有界但非零。

## 6. helix 现状对照（含一处文档过期修正）

helix 在 Stream TE-6/TE-6b 已 ship 等价机制，但**披露模型不同**：

> **修正**：`find_tools.py` 模块 docstring 写"this module is the dormant mechanism only: no tool is deferred by default... Auto-deferral is Stream TE-6b"——**已过期**。`assembly.py:213-220` 显示 TE-6b 已实现为 **always-defer-MCP** 策略（deer-flow 同款）：平台/租户/用户 OAuth 三个 MCP 池注册全部 `deferred=True`（`assembly.py:489/517/539`），有延迟工具时自动挂 `find_tools`，无 MCP 的 agent 工具集与 pre-TE-6 字节一致。

### 两种披露模型

| 维度 | Hermes（describe 模型） | helix TE-6（promotion 模型） |
|---|---|---|
| schema 去向 | `tool_describe` 结果进对话历史 | `find_tools` 写 `promoted_tools` state channel，**下一 turn 进 tools bind** |
| 调用方式 | 永远经 `tool_call` 桥接 + unwrap | promotion 后直接调用，无桥接层 |
| 治理面（hook/审批/审计） | 需要 unwrap 才看到真名（#34493 一半复杂度在此，且出过越权洞） | **天然看到真名，无 unwrap 问题** |
| tools 数组稳定性 | 激活后恒定（核心+3 桥），prompt cache 友好 | 每次 promotion 改 bind，破 cache prefix |
| scope 隔离 | 全局 registry + 双重 gate 补救 | **per-run registry + LangGraph per-thread channel，天然隔离** |
| 门控 | auto 阈值（10% / 20k 兜底），双向自适应 | **无门控：MCP 永远延迟**，2 个工具也要 find_tools 一跳 |
| 检索 | BM25 + 拆词 + 参数名索引 + substring 兜底 | `registry.search`：`select:`/`+keyword`/substring/regex 精确语法，**无相关性排序** |
| 核心保护 | `_HERMES_CORE_TOOLS` 白名单 | 等价达成：builtin/subagent/knowledge 从不延迟，只 MCP 延迟 |
| 故障模式 | fail-open（故障→不折叠） | 注册期决定，无运行期折叠故障面 |

**结构性结论**：helix 的 promotion 模型在治理面和隔离性上天然优于 Hermes（Hermes 用 ~200 行 unwrap + 双重 gate 才补回 helix 免费拿到的性质）；Hermes 在门控自适应和检索质量上领先。两边的弱点恰好互补。

### helix 的真实差距（按价值排序）

1. **检索质量**（影响最大）：`find_tools` 的 query 语法是给"知道自己找什么"的调用方设计的（`select:`/`+`/regex），模型用自然语言描述能力（"create a github issue"）时命中靠运气。Hermes 验证了 name 拆词 + description + 参数名的 BM25 足够好；helix 还可以直接复用自家 embedder/pgvector 做语义检索——顺带解决 Hermes 的中文盲区（jieba+向量都现成）。
2. **无门控的反向代价**：always-defer 意味着小 MCP 目录（2-3 个工具，schema 远低于任何阈值）也付 find_tools 间接层（多一次模型交互 + 工具不在 bind 里模型可能根本想不到去搜）。Hermes 的 `auto` 阈值方向相反：小目录直通，大目录才折叠。**helix 缺的是"低于阈值直接全量暴露"的逃生门**。
3. **promotion 后 bind 漂移 vs cache**：每次 promotion 改 tools bind 破 prompt cache prefix。目录大时单次 promotion 仍远优于全量常驻，但与 Hermes "tools 数组恒定 + schema 进历史"的形态相比，cache 经济性逐 turn 劣化。量化对比需要 token 计量数据（G.9 已有管道）。

## 7. 可吸收点评估（候选，未立项）

| 候选 | 内容 | 依赖 | 评估 |
|---|---|---|---|
| TE-6c-a 阈值逃生门 | MCP schema 估算总量低于阈值（如上下文 10% 或 20k 兜底）时**不延迟**，直接全量进 bind；超阈值维持现状 | 无硬依赖；chars/4 即可（门控只需数量级，Hermes 已论证），HX-1 真 tokenizer 落地后换用更准 | **高价值低成本**。helix 当前对小目录的体验/成功率损失是真实的 |
| TE-6c-b 检索升级 | `registry.search` 增加自然语言 ranked 模式：名字拆词 + description 的 BM25，或复用平台 embedder 做语义检索 | embedder 路径需平台凭证；BM25 路径零依赖 | **高价值**。BM25 版可先行（中文用 jieba 分词，helix 已有依赖）；语义版作二期 |
| TE-6c-c describe 中间态 | search 结果只给 name+description，加 `describe` 一步再 promotion | — | **不建议**。promotion 模型一步到位是优点；目录截断（Hermes 的 400 chars）值得抄，分步披露不值得 |
| 文档修正 | `find_tools.py` docstring 与 assembly 现实对齐 | — | 顺手修，任何后续 PR 带上 |

Hermes 侧可直接搬运的设计资产：`should_activate` 的三态+兜底形态、"故障方向必须是多花 token 而非少一个能力"的公理、检索文本构造（拆词+参数名、不索引 schema body）、零 IDF substring 兜底、目录 description 截断。

## 8. 结论

1. Hermes Tool Search 是一份**工程质量很高的参考实现**：无状态 catalog（一致性优先）、双重 scope gate（纵深防御）、unwrap 透明性（transcript 与治理面各看各的真相）、处处 fail-open、39 测试随修复合入。其复杂度的一半（unwrap + scope）源于它的 describe 模型在全局 registry 上运行——这是 helix 的 promotion 模型 + per-run registry 天然不需要付的成本。
2. helix 的 TE-6b（always-defer-MCP）方向正确但**缺两块**：低于阈值的直通逃生门（小目录付了不必要的间接层）、自然语言 ranked 检索（现有 query 语法对模型不友好）。两者都有 Hermes 验证过的成熟形态可参照，且 helix 的向量栈能做得比 BM25 更好。
3. 建议处置：TE-6c-a/b 进 Stream HX backlog（与 HX-1 弱耦合，chars/4 版本可先行）；TE-6c-c 不做；`find_tools.py` docstring 过期问题顺手修。
