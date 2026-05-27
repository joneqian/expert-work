# Hermes Agent 源码深度分析（15 维度）

> **目的**：基于 Hermes 真实源码，把外部对 Hermes 流传的"15 维度能力表"逐项落到代码上，给后续 helix-agent 的能力升级提供事实底稿。
>
> **范围声明**：本报告**只描述 Hermes 怎么做**，**不**做与 helix 的差距分析、不做与其他 agent 框架（LangGraph/AutoGen/CrewAI…）的横向对比、不给"helix 应该学什么"的建议。这些是另一份文档的工作。
>
> **源码版本**：`hermes-agent` @ `bb4703c761ea6687b6399aa2e61e0a08fabd3ca3`（HEAD on 2026-05-26）
> **报告生成日期**：2026-05-27
> **源码绝对路径**：`/Users/mac/src/github/hermes-agent`（下文 `file:line` 引用均相对此根）

## 阅读指引

- 全文按原图 15 个维度逐章展开，每章统一 5 节：**设计立场** / **关键代码路径** / **实现细节** / **运行时行为** / **局限与边界**。
- 章内所有论断都给 `file:line` 引用；想"读源码"的读者顺着引用走最快。
- 代码摘录是从源码直接复制（保留原注释和缩进），未做改写。
- 出现 trade-off 描述时是 Hermes 实际做出的取舍，不是"应该如何"。
- 三层分组（仅作导航用，对应原图的视觉聚类）：
  - **大脑层（1-4）**：Agent 循环、自我改进、记忆、上下文管理
  - **运行时层（5-9）**：模型抽象、本地推理、沙箱、子 Agent、Cron
  - **生态层（10-15）**：消息平台、MCP、扩展机制、UI、技能可移植、RL

## 报告勘误（与原图的差异）

原图把若干维度的具体数字"四舍五入"了，源码事实如下：

| 原图说法 | 源码事实 | 引用 |
|---------|---------|------|
| "消息平台 12 个" | `Platform` enum 22 个内置成员（包含 FEISHU/WECOM/WEIXIN/QQBOT/YUANBAO/BLUEBUBBLES 等），另支持 `plugins/platforms/` 动态插件平台 | `gateway/config.py:108-129` |
| "无锁定 18+ Provider" | `plugins/model-providers/` 下 30+ profile 目录 | 见维度 5 |
| "沙箱 6 种后端" | `tools/environments/` 下 `local/docker/ssh/modal/daytona/singularity/morph/runpod` 等 ≥8 后端文件 | 见维度 7 |
| "记忆 5 层" | 源码区分：MEMORY.md/USER.md（系统提示快照层）、Session DB（会话持久化）、跨会话历史（FTS 未在主代码暴露）、`MemoryProvider` 外挂提供商（Honcho/Hindsight）、`SkillStore`（过程性记忆） | 见维度 3 |

---

# 维度 1 — Agent 循环（ReAct）

## 1.1 设计立场

Hermes 把 ReAct 主循环单独抽到 `agent/conversation_loop.py`，长度 **4306 行**（单文件！），是整个工程最大的模块。它的设计立场是：

- **流式优先 + 非流式可降级**：每轮 API 调用默认走流式（更早拿到第一 token、能监听中断），但 provider 不支持时（如 `copilot-acp` 走 ACP subprocess）自动改非流式。
- **中断必须可恢复**：`agent._interrupt_requested` 是 hot signal，循环里每个 phase 都查；网络流被切断时插入"continue exactly where you left off"指令而非重启。
- **错误分类驱动重试**：把 API 异常分成 invalid_tool / empty_content / thinking_prefill_overlap / incomplete_scratchpad / rate_limited / non_retryable 等，不同类别走不同重试路径，避免无脑指数退避。
- **预算优先于无限循环**：每轮显式 `consume()` 一次 iteration budget（线程安全计数器），到顶就硬停，防止 LLM 把工具调用调成死循环。
- **工具并发 + 顺序两种模式并存**：模型一次给多个 tool_calls 时可并发执行（上限 8 worker），但保留顺序模式给前后有依赖的工具。

## 1.2 关键代码路径

| 角色 | 文件 | 关键符号 |
|------|------|---------|
| 主循环入口 | `agent/conversation_loop.py:263` | `run_conversation(agent, user_message, ...)` |
| 工具并发执行器 | `agent/tool_executor.py:65` | `execute_tool_calls_concurrent(...)` |
| 工具顺序执行器 | `agent/tool_executor.py`（同文件后段） | `execute_tool_calls_sequential(...)` |
| 单工具执行 | `agent/tool_executor.py:197` 附近 | `_run_tool(...)` |
| 迭代预算 | `agent/iteration_budget.py:17` | `class IterationBudget` |
| 中断恢复指令 | `agent/conversation_loop.py:240-260` | 续写 prompt（network truncation vs output length） |
| 系统提示构建 | `agent/conversation_loop.py:493-565` | `_compress_context()` 预检 + 系统提示重建 |
| Provider fallback | `agent/conversation_loop.py:1057+` | `agent._try_activate_fallback()` / `_restore_primary_runtime()` |
| Skill / memory write-origin 标记 | `agent/conversation_loop.py:316-324` | `set_current_write_origin()` |

主循环骨架（节选自 `agent/conversation_loop.py:263-329`，按发表顺序保留）：

```python
def run_conversation(
    agent,
    user_message: str,
    system_message: str = None,
    conversation_history: List[Dict[str, Any]] = None,
    task_id: str = None,
    stream_callback: Optional[callable] = None,
    persist_user_message: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run a complete conversation with tool calling until completion.
    ...
    """
    _install_safe_stdio()                      # 防 systemd/daemon 模式下 broken pipe 崩溃
    agent._ensure_db_session()

    # 告诉 auxiliary_client 当前主 provider / 主 model
    from agent.auxiliary_client import set_runtime_main
    set_runtime_main(getattr(agent, "provider", "") or "",
                     getattr(agent, "model", "") or "")

    # 给本线程的日志记录绑定 session_id，hermes logs --session 才能过滤
    from hermes_logging import set_session_context
    set_session_context(agent.session_id)

    # 标记本次写入来源（前台 vs 后台 review fork）
    from tools.skill_provenance import set_current_write_origin
    set_current_write_origin(getattr(agent, "_memory_write_origin", "assistant_tool"))

    # 上一轮如果激活了 fallback，这轮恢复 primary
    agent._restore_primary_runtime()
```

迭代预算的全部代码（`agent/iteration_budget.py`，整文件就 62 行）：

```python
class IterationBudget:
    """Thread-safe iteration counter for an agent.

    Each agent (parent or subagent) gets its own ``IterationBudget``.
    The parent's budget is capped at ``max_iterations`` (default 90).
    Each subagent gets an independent budget capped at
    ``delegation.max_iterations`` (default 50) — this means total
    iterations across parent + subagents can exceed the parent's cap.
    ...

    ``execute_code`` (programmatic tool calling) iterations are refunded via
    :meth:`refund` so they don't eat into the budget.
    """

    def __init__(self, max_total: int):
        self.max_total = max_total
        self._used = 0
        self._lock = threading.Lock()

    def consume(self) -> bool:
        with self._lock:
            if self._used >= self.max_total:
                return False
            self._used += 1
            return True

    def refund(self) -> None:
        with self._lock:
            if self._used > 0:
                self._used -= 1
```

两个细节值得注意：

1. **`refund()` 给 `execute_code` 用** — `execute_code` 工具内部还会再触发若干轮"程序化 LLM 调用"，这些不计入主预算，避免本来的"我让你写段脚本"突然吃光 90 轮预算。
2. **子 agent 各自一份预算** — 父 90，子 50，总轮数可以超过父的 cap；这是有意选择（详见维度 8）。

## 1.3 实现细节

### 1.3.1 消息清理管线

每轮 API 调用前，messages 数组要走一段比较重的清理管线（`agent/conversation_loop.py:819-959` 附近），主要做：

| 阶段 | 作用 |
|------|------|
| 复制 messages，避免 in-place 修改 | 保护持久化历史 |
| Reasoning content carry-over | Claude/Codex 把推理块挂在前一条 assistant 消息上，新一轮要保留 |
| 工具调用参数修复 | LLM 偶尔返回坏 JSON，做 prefix 修复 + JSON 标准化 |
| Role 交替修复 | 部分 provider 要求严格 user/assistant 交替 |
| API mode 字段裁剪 | `api_mode="chat_completions"` 时剥掉 Anthropic 风格的 thinking block 字段 |
| 内存前缀注入 | `memory_manager.prefetch_all()` 的结果只塞 API kwargs 不进持久化历史 |
| 孤立 tool_result 清理 | 上一轮断流可能留下没配 tool_call 的 tool_result，必须清掉 |
| 思考块拆分 | `<REASONING_SCRATCHPAD>` 跨轮处理 |
| 代理字符消毒 | 删 ` ` / `�` 等 |

注意"前缀缓存友好"是一个隐含的设计约束：所有不影响 LLM 语义的清理都允许，但**任何顺序或文本的变化都要稳定**，否则 Claude 的 cache_read 不命中。

### 1.3.2 流式 vs 非流式自适应

`agent/conversation_loop.py:1147-1176` 决定本轮走流式还是非流式：

- `agent._disable_streaming = True` → 强制非流式（用于诊断或特定 provider）
- `agent.provider == "copilot-acp"` → ACP 是 subprocess，没有流可消费，强制非流式
- 其他情况 → 流式，并把响应包成 `_interruptible_streaming_api_call` 返回的可中断对象

流式路径内部还有 **stale stream detector**（90 秒没数据视为半挂），用于在本地推理（Ollama / vLLM）慢响应场景下兜底。

### 1.3.3 重试矩阵

`conversation_loop.py:1034-1500` 段有一个非常细的重试逻辑，关键计数器：

| 计数器 | 触发 | 上限 |
|--------|------|------|
| `invalid_tool_retries` | LLM 返回了不合法的 tool_call（JSON 坏 / schema 缺字段） | 模型相关默认 ≤ 5 |
| `empty_content_retries` | API 返回空 content 且无 tool_call | 同上 |
| `thinking_prefill_retries` | Anthropic / Claude 的 reasoning 与 prefill 重叠 bug | 同上 |
| `incomplete_scratchpad_retries` | `<REASONING_SCRATCHPAD>` 截断 | 同上 |
| `compression_attempts` | 因 413 上下文超限而触发的运行时压缩 | 同上 |

到顶就抛 `_try_activate_fallback()` 把流量切到 `fallback_model`（如果配置了）。

### 1.3.4 工具并发执行

`agent/tool_executor.py:65` `execute_tool_calls_concurrent` 走 ThreadPoolExecutor，并发上限 `_MAX_TOOL_WORKERS = 8`（在文件常量区），每个 tool 单独 300s 超时。关键步骤：

1. **预检**：循环里先调用每个工具的 `before_call` guardrail，被拦的直接产出 `block_result`，**不再 submit 到线程池**。
2. **同步 ContextVar 传递**：用 `contextvars.copy_context()` 把当前线程的 `session_context` / `skill_write_origin` 复制到 worker，避免 worker 内部日志没 session id。
3. **结果按原顺序返回**：用 dict 收集 `tool_call_id → result`，最后按 `tool_calls` 原序输出，保证给回 LLM 的消息序列不乱。
4. **异常隔离**：单工具崩溃不影响其它工具，统一把 stack trace 序列化成工具结果文本回流。

## 1.4 运行时行为

```
run_conversation()
  │
  ├─ stdio 安全包装 + session_id 上下文 + write-origin 标记
  │
  ├─ [preflight] 长 messages → 估算 token → ContextCompressor.should_compress()?
  │     └─ 是：做 ≤3 轮压缩，每轮 messages 数减少就继续，否则停
  │
  └─ while api_call_count < max_iterations and iteration_budget.remaining > 0:
        │
        ├─ 中断检查：agent._interrupt_requested? → break
        ├─ 预算消费：iteration_budget.consume() → 满则 break
        │
        ├─ 消息清理管线（reasoning carry / JSON 修复 / 孤儿 tool_result 清理 …）
        ├─ 系统提示：首轮构建并 cache，后续轮重用（前缀缓存命中）
        ├─ Anthropic 缓存断点标记（apply_anthropic_cache_control）
        │
        ├─ API 调用：
        │     ├─ 流式 → 流可中断、有 90s stale 心跳
        │     └─ 非流式 → 一次拉完
        │
        ├─ 响应分类：
        │     ├─ 4xx terminal → fallback or 报错
        │     ├─ 429 → 退避重试
        │     ├─ 413 上下文超 → ContextCompressor.compress() → 同轮重试
        │     ├─ empty / invalid → retry++（按类计）
        │     └─ tool_calls 出现 → 进工具执行
        │
        ├─ 工具执行（顺序 or 并发） → tool_result 追加进 messages
        │
        └─ finish_reason == "stop" 且无 tool_calls → break

退出循环 → 把 messages 刷到 SQLite session DB → 后台 review fork
```

## 1.5 局限与边界

- **API 调用本身是严格线性的**：单轮内可以工具并发，但**轮与轮之间不重叠**；不存在"LLM 在生成下一轮的同时执行上一轮的工具"这种 pipeline。
- **工具结果体积无硬上限**：`ToolEntry.max_result_size_chars` 是可选字段（见 `tools/registry.py:98`），没设的工具返回 10MB 日志会直接挤进下一轮 prompt，吃掉上下文。
- **每个 provider 独立重试，无跨 provider 的智能路由**：fallback 是静态配置的，"主挂了切备"，没有"哪个最便宜还活着就用哪个"。
- **中断信号无 TTL**：`_interrupt_requested` 是布尔位，如果到达时正卡在某个 phase 没轮询，会被静默吞掉。
- **系统提示每会话首轮重建**：DB session 加载 + 系统提示构建有冷启动成本，没有"上次相同 agent profile 的系统提示" memoize 跨会话。

---

# 维度 2 — 自我改进（闭环学习 + 技能自创建）

## 2.1 设计立场

Hermes 把"自我改进"拆成**三层互不阻塞的回路**：

1. **会话内**：工具失败 → 即时分类重试（见维度 1 的重试矩阵）。
2. **会话末**：消息快照 → fork 一个 `AIAgent` 实例，在 daemon 线程里用同一缓存的系统提示重放对话 + 注入 `_MEMORY_REVIEW_PROMPT` / `_SKILL_REVIEW_PROMPT`，让"评论 agent"决定是否写 memory / 改 skill。前台用户**完全感知不到**这个 fork，但下一次会话开始就能用到新写入的 memory / skill。
3. **库级**：`agent/curator.py` 默认 7 天周期跑一次，把零散积累的 agent-created skill 合并成 class-level umbrella 技能。

核心理念**写在 review prompt 的 docstring 里**（`agent/background_review.py:1-17`）：

> "It runs with a tool whitelist limited to memory and skill management tools; everything else is denied at runtime."

——后台 review 的工具集是**白名单**，不是 deny list；只允许动 memory 和 skill。

## 2.2 关键代码路径

| 角色 | 文件 | 关键符号 |
|------|------|---------|
| 后台 review 入口 | `agent/background_review.py`（593 行） | `spawn_background_review_thread(...)` |
| Review prompt（记忆） | `agent/background_review.py:34-43` | `_MEMORY_REVIEW_PROMPT` |
| Review prompt（技能） | `agent/background_review.py:45-148` | `_SKILL_REVIEW_PROMPT` |
| Review prompt（合并） | `agent/background_review.py:150+` | `_COMBINED_REVIEW_PROMPT` |
| 技能管理工具 | `tools/skill_manager_tool.py`（1034 行） | `skill_manage(action="create"|"edit"|"patch"|"delete"|"write_file"|"remove_file")` |
| 技能 provenance | `tools/skill_provenance.py` | `set_current_write_origin()`：区分前台 vs 后台 fork 写入 |
| 安全扫描器 | `tools/skills_guard.py` | `scan_skill()` / `should_allow_install()` / `format_scan_report()` |
| Curator 调度门 | `agent/curator.py:199-249` | `should_run_now()` |
| 自动状态转移 | `agent/curator.py:256-296` | `apply_automatic_transitions()` |
| Curator review prompt | `agent/curator.py:299+` | `CURATOR_DRY_RUN_BANNER` / 整合 prompt |
| 技能使用记录 | `tools/skill_usage.py` | `agent_created_report()` / `set_state()` / `archive_skill()` |

## 2.3 实现细节

### 2.3.1 Skill 的 6 个 action

`tools/skill_manager_tool.py` 第 14-20 行明确列出技能管理工具的 6 个 action（这是 docstring 的原文）：

```text
  create     -- Create a new skill (SKILL.md + directory structure)
  edit       -- Replace the SKILL.md content of a user skill (full rewrite)
  patch      -- Targeted find-and-replace within SKILL.md or any supporting file
  delete     -- Remove a user skill entirely
  write_file -- Add/overwrite a supporting file (reference, template, script, asset)
  remove_file-- Remove a supporting file from a user skill
```

所有写操作都通过 `utils.atomic_replace`（tempfile + os.replace）落盘，避免半写状态污染下一轮 review。

### 2.3.2 安全扫描两段式

`tools/skill_manager_tool.py:50-75`：

- **外部 hub 安装的技能**：始终走 `scan_skill()`（恶意 token、可疑命令、隐藏 prompt injection）。
- **agent 自己创建的技能**：默认**不扫**（`guard_agent_created = False`），逻辑写在 docstring：

> "Off by default because the agent can already execute the same code paths via terminal() with no gate, so the scan adds friction without meaningful security."

也就是说，Hermes 的安全模型认定"agent 既然能跑 `terminal`，再扫它写的 skill 是双标"。但配置可以开关，企业部署可以打开。

### 2.3.3 后台 fork 的工具白名单 + 缓存继承

`agent/background_review.py:1-17` 的 module docstring 明确：

- fork 出来的子 agent **继承父 agent 的 cached system prompt** → 击中前缀缓存，几乎不花新 token；
- fork 只能用 `memory` + `skill_manage` 工具，**任何其他工具调用都在 runtime 被拒**；
- fork 在 **daemon thread** 里跑，不阻塞主进程退出；
- 主线程的 ContextVar **不会泄漏给 fork**（fork 在新线程获得全新 context）。

### 2.3.4 Skill review prompt 长达 100+ 行

`_SKILL_REVIEW_PROMPT`（`agent/background_review.py:45-148`）极其细致地告诉 review agent "什么算作可写信号、什么坚决别写"。摘录两段（原文）：

**算"该写"的信号（任一即可）**：

> - User corrected your style, tone, format, legibility, or verbosity. Frustration signals like 'stop doing X', 'this is too verbose', 'don't format like this', 'why are you explaining', 'just give me the answer', 'you always do Y and I hate it', or an explicit 'remember this' are FIRST-CLASS skill signals, not just memory signals.
> - User corrected your workflow, approach, or sequence of steps. Encode the correction as a pitfall or explicit step in the skill that governs that class of task.
> - Non-trivial technique, fix, workaround, debugging path, or tool-usage pattern emerged that a future session would benefit from.
> - A skill that got loaded or consulted this session turned out to be wrong, missing a step, or outdated. Patch it NOW.

**坚决"别写"的信号**（防止 agent 把环境性故障固化为永久信条）：

> - Environment-dependent failures: missing binaries, fresh-install errors, post-migration path mismatches, 'command not found', unconfigured credentials, uninstalled packages. The user can fix these — they are not durable rules.
> - Negative claims about tools or features ('browser tools do not work', 'X tool is broken', 'cannot use Y from execute_code'). These harden into refusals the agent cites against itself for months after the actual problem was fixed.
> - Session-specific transient errors that resolved before the conversation ended. If retrying worked, the lesson is the retry pattern, not the original failure.
> - One-off task narratives.

这段 prompt 是 Hermes 一直在迭代的"如何避免学坏"的核心约束。

### 2.3.5 Skill 写入优先级表（也写在 prompt 里）

prompt 里硬性规定了 4 级写入优先级（节选 `_SKILL_REVIEW_PROMPT` 中的 "Preference order"）：

1. **UPDATE A CURRENTLY-LOADED SKILL** — 本次对话里被 `/skill-name` 或 `skill_view` 用过的，优先 patch。
2. **UPDATE AN EXISTING UMBRELLA** — `skills_list` + `skill_view` 找一个 class-level umbrella，加 subsection / pitfall。
3. **ADD A SUPPORT FILE** under existing umbrella — `references/` / `templates/` / `scripts/` 三类子目录有严格分工：
   - `references/<topic>.md` — session-specific 细节 + 浓缩知识（quoted research、API docs）
   - `templates/<name>.<ext>` — 可复用的模板文件
   - `scripts/<name>.<ext>` — 可静态重跑的脚本
4. **CREATE A NEW CLASS-LEVEL UMBRELLA SKILL** — 实在没现存可用，新建。**名字必须是 class-level**，"fix-X / debug-Y / audit-Z-today" 这类一次性命名是禁止的。

### 2.3.6 Curator 7 天周期的状态机

`agent/curator.py:256-296` `apply_automatic_transitions`：**纯启发式、无 LLM**，只看时间戳：

```python
def apply_automatic_transitions(now: Optional[datetime] = None) -> Dict[str, int]:
    """Walk every agent-created skill and move active/stale/archived based on
    the latest real activity timestamp. Pinned skills are never touched."""
    if now is None:
        now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(days=get_stale_after_days())
    archive_cutoff = now - timedelta(days=get_archive_after_days())

    counts = {"marked_stale": 0, "archived": 0, "reactivated": 0, "checked": 0}

    for row in _u.agent_created_report():
        counts["checked"] += 1
        name = row["name"]
        if row.get("pinned"):
            continue

        last_activity = _parse_iso(row.get("last_activity_at"))
        anchor = last_activity or _parse_iso(row.get("created_at")) or now
        ...

        current = row.get("state", _u.STATE_ACTIVE)

        if anchor <= archive_cutoff and current != _u.STATE_ARCHIVED:
            ok, _msg = _u.archive_skill(name)
            if ok: counts["archived"] += 1
        elif anchor <= stale_cutoff and current == _u.STATE_ACTIVE:
            _u.set_state(name, _u.STATE_STALE)
            counts["marked_stale"] += 1
        elif anchor > stale_cutoff and current == _u.STATE_STALE:
            _u.set_state(name, _u.STATE_ACTIVE)
            counts["reactivated"] += 1

    return counts
```

状态机三态：`ACTIVE → STALE → ARCHIVED`，回流路径只有 `STALE → ACTIVE`（再次被用就反激活，但 `ARCHIVED` 不反激活）。**首次运行**会被 `should_run_now()`（`curator.py:199-249`）拒绝：

```python
if last is None:
    # Never run before. Seed state so we wait a full interval before the
    # first real pass. Report-only; do not auto-mutate the library the
    # very first time a gateway ticks after an update.
    ...
    state["last_run_at"] = now.isoformat()
    state["last_run_summary"] = (
        "deferred first run — curator seeded, will run after one "
        "interval; use `hermes curator run --dry-run` to preview now"
    )
    save_state(state)
    return False
```

即"`hermes update` 之后第一次 gateway tick 不要立刻乱动用户的技能库"，这是个有意的保守策略。

### 2.3.7 Curator 整合的"四种动作"

`agent/curator.py:299+` 那段 prompt（部分）告诉 LLM 整合 skill 库时只能选四个动作：

- **MERGE INTO EXISTING** — 兄弟 skill 内容 patch 进伞 skill
- **CREATE NEW UMBRELLA** — 没伞就创建一个 class-level umbrella
- **DEMOTE TO SUPPORT FILES** — 内容窄但有价值 → 移到伞下的 `references/` / `templates/` / `scripts/`
- **PRUNE** — 完全过时、无转移目标 → 删

输出格式是定好的 YAML（`consolidations` / `prunings`），后续 curator 逻辑解析 YAML 执行。

## 2.4 运行时行为

```
单轮对话结束 (run_conversation 返回前)
  │
  ├─ 计数：_turns_since_memory += 1, _iters_since_skill += 1
  │
  ├─ 阈值判断：
  │     _memory_nudge_interval > 0 and _turns_since_memory >= 阈值 → 要 memory review
  │     _skill_nudge_interval  > 0 and _iters_since_skill  >= 阈值 → 要 skill review
  │
  └─ 任一为真 → spawn_background_review_thread()
        │
        ├─ 新建 AIAgent(同 provider、同 model、同 credentials、同 cached system prompt)
        ├─ review_agent.tools = whitelist {memory, skill_manage}
        ├─ messages = list(parent_messages)  ← 父快照
        ├─ append({"role": "user", "content": _MEMORY/_SKILL/_COMBINED_REVIEW_PROMPT})
        │
        └─ daemon Thread.start():
              review_agent.run_conversation(...)
                ├─ 命中前缀缓存 (cache_read >> cache_creation)
                ├─ LLM 决定调用 memory(...) / skill_manage(...) / 或啥都不做
                └─ 写盘：~/.hermes/memories/MEMORY.md, ~/.hermes/skills/<name>/SKILL.md

curator 周期 (Gateway tick / 显式 hermes curator run)
  │
  ├─ should_run_now() → false 时直接 return
  │
  ├─ apply_automatic_transitions() — 纯启发式 active/stale/archived 转移
  │
  └─ 触发 curator review fork：再开一个 AIAgent
        └─ 用 CURATOR review prompt + skills_list + skill_view
              └─ LLM 给出 YAML(consolidations + prunings)
                    └─ curator 解析 YAML → 调 skill_manage(...) 执行
```

## 2.5 局限与边界

- **写入触发器是固定阈值**：`_memory_nudge_interval` / `_skill_nudge_interval` 默认 ∞（即关闭），开了之后也只是"每 N 轮一次"，**不会**根据任务复杂度自适应。
- **后台 fork 无反馈链路**：主会话不会被告知 fork 是否写了、写了什么；用户要靠 `hermes logs` 或显式 `memory(action=read)` / `skills_list` 才能知道。
- **Curator 可能误判 class-level**：prompt 里写了一堆判定准则，但本质是 LLM 主观判断，可能把用户故意保留的窄技能合并掉；`pinned` 字段是手动 escape hatch，但需要用户主动 `hermes curator pin`。
- **bundled / hub 技能不让后台 review 改**：见 prompt "Protected skills (DO NOT edit these): Bundled skills... Hub-installed skills..."；这意味着这些 skill 的 bug fix 只能升级 hermes 或重装。

---

# 维度 3 — 记忆系统

## 3.1 设计立场

外部图说"五层（FTS5 + 向量 + Honcho + 技能 + MEMORY.md）"，对应到源码里其实是**五个不同性质的存储位**，并不是统一抽象出来的"五层金字塔"。它们的**写入主权、生命周期、读取时机**都不一样：

| 存储位 | 谁写 | 谁读 | 生命周期 |
|--------|------|------|---------|
| **MEMORY.md / USER.md** | 后台 review fork、用户、agent 显式调 `memory` 工具 | 每次构建系统提示时静态注入 | 永久（手动 prune） |
| **Session DB**（SQLite，per-session messages） | 主循环结束 flush | 同一 session 恢复时 | 用户配置 retention |
| **Cross-session search**（基于 session DB 的 grep / FTS） | 同上 | 工具显式调（如 `transcripts` 工具，未在主循环常走） | 同上 |
| **External MemoryProvider**（Honcho / Hindsight 等） | `MemoryProvider.sync_turn()`（会话末异步） | `prefetch_all()`（每轮 user_message 进来前） | 由外部提供商决定 |
| **SkillStore** | `skill_manage(...)` | 用户 `/skill-name` 或 `skill_view` 工具显式调 | active → stale → archived（见维度 2） |

设计立场可以归纳为：
- **L1 优先做对**：MEMORY.md 用 frozen snapshot 保前缀缓存稳定，威胁扫描在 snapshot 构建时一次性完成。
- **L4 外挂可选**：通过 `MemoryProvider` 抽象把"长期跨会话语义搜索"挂出去，不在主流程内做向量索引。
- **没有强制"五层融合"**：源码里没有 hybrid retrieval / re-rank 的逻辑；prefetch 拼接 + 系统提示固定块就是上层全部能见。

## 3.2 关键代码路径

| 层 | 文件 | 关键符号 |
|----|------|---------|
| L1 工具 | `tools/memory_tool.py`（724 行） | `class MemoryStore` @ `:114`，`memory(action=add/replace/remove/read)` 入口 |
| L1 威胁扫描 | `tools/threat_patterns.py` | `scan_for_threats()` |
| L1 snapshot 渲染 | `tools/memory_tool.py:160-171` | `_system_prompt_snapshot` 字典 |
| L2 SessionDB | `hermes_state.py` | 集中 session 读写（140KB+ 单文件） |
| L4 抽象 | `agent/memory_provider.py:42+` | `class MemoryProvider(ABC)` + `prefetch / sync_turn / get_tool_schemas` |
| L4 管理器 | `agent/memory_manager.py` | `MemoryManager.add_provider()` / `build_system_prompt()` / `prefetch_all()` |
| L4 插件 | `plugins/memory-providers/<name>/` | Honcho / Hindsight / 自定义 |
| L5 技能 | `tools/skill_manager_tool.py` + `agent/curator.py` | 详见维度 2 |
| 前缀缓存 | `agent/prompt_caching.py` | `apply_anthropic_cache_control()` |

## 3.3 实现细节

### 3.3.1 `MemoryStore` 双状态：live vs frozen

`tools/memory_tool.py:114-131` 的 docstring 是这一层的核心约束：

```python
class MemoryStore:
    """
    Bounded curated memory with file persistence. One instance per AIAgent.

    Maintains two parallel states:
      - _system_prompt_snapshot: frozen at load time, used for system prompt injection.
        Never mutated mid-session. Keeps prefix cache stable.
      - memory_entries / user_entries: live state, mutated by tool calls, persisted to disk.
        Tool responses always reflect this live state.
    """

    def __init__(self, memory_char_limit: int = 2200, user_char_limit: int = 1375):
        self.memory_entries: List[str] = []
        self.user_entries: List[str] = []
        self.memory_char_limit = memory_char_limit
        self.user_char_limit = user_char_limit
        self._system_prompt_snapshot: Dict[str, str] = {"memory": "", "user": ""}
```

字符上限是**写在构造函数默认值**里的硬编码（2200 / 1375 字符），溢出会返回 warning 但不阻止写入；用户需要手动 prune。

### 3.3.2 加载时的"投毒防御"

`tools/memory_tool.py:133-171` `load_from_disk()`：

```python
def load_from_disk(self):
    """Load entries from MEMORY.md and USER.md, capture system prompt snapshot.

    The frozen snapshot is what enters the system prompt. We scan each
    entry for injection/promptware patterns at snapshot-build time —
    ANY hit replaces the entry text in the snapshot with a placeholder
    like ``[BLOCKED: …]``, so a poisoned-on-disk memory file (supply
    chain, compromised tool, sister-session write) cannot inject into
    the system prompt.

    The live ``memory_entries`` / ``user_entries`` lists keep the
    original text so the user can still SEE poisoned entries via
    ``memory(action=read)`` and remove them — silently dropping them
    would hide the attack from the user.

    Scanning is deterministic from disk bytes, so the snapshot remains
    stable for the entire session (prefix-cache invariant holds).
    """
    mem_dir = get_memory_dir()
    ...
    self.memory_entries = self._read_file(mem_dir / "MEMORY.md")
    self.user_entries = self._read_file(mem_dir / "USER.md")

    # Deduplicate entries (preserves order, keeps first occurrence)
    self.memory_entries = list(dict.fromkeys(self.memory_entries))
    self.user_entries = list(dict.fromkeys(self.user_entries))

    sanitized_memory = self._sanitize_entries_for_snapshot(self.memory_entries, "MEMORY.md")
    sanitized_user = self._sanitize_entries_for_snapshot(self.user_entries, "USER.md")

    self._system_prompt_snapshot = {
        "memory": self._render_block("memory", sanitized_memory),
        "user":   self._render_block("user", sanitized_user),
    }
```

注意三件事：
1. **live 状态保留原文**：用户能通过 `memory(action=read)` 看到中毒条目并删除；偷偷丢掉会"骗用户"。
2. **snapshot 中毒条目替换为占位符** `[BLOCKED: ...]`：进入系统提示的永远是 sanitize 过的版本。
3. **snapshot 是 deterministic from disk bytes**：意味着同样的 MEMORY.md → 同样的系统提示 → 前缀缓存稳定。

### 3.3.3 文件格式：`§` 分隔的纯文本块

`memory_tool.py` 内 `ENTRY_DELIMITER`（搜代码可见）是 `"\n§\n"`，MEMORY.md 形如：

```
用户周一希望详细的 PR 评论，不要点子菜单。
§
Python 项目的默认 pytest 配置在 pyproject.toml 中，而不是 setup.cfg。
§
```

写入是把 `entries` join 起来 atomic_replace 整文件，不是 append。

### 3.3.4 漂移检测（drift backup）

`memory_tool.py:80-111` 段（部分前文）：如果发现磁盘上的 MEMORY.md 跟 in-memory snapshot 不匹配（外部编辑器或 sister session 改过了），会**拒绝写入**并把当前内容备份到 `.bak`，返回 `drift_backup` 字段提示用户：

> "Open the .bak file, integrate the missing entries into the memory tool one at a time via memory(action=add, content=...), then remove or rewrite the original file to a clean state."

这个机制就是为了避免"agent 自动写入覆盖了用户手动改的内容"。

### 3.3.5 `MemoryProvider` 抽象

`agent/memory_provider.py:42+` 是外部记忆提供商的基类（Honcho、Hindsight、Mem0 等可以挂上来）。关键接口（节选概念，源码方法签名）：

- `name` — provider 名（"honcho" / "hindsight" / ...）
- `initialize(session_id, **kwargs)` — 启动时建立连接
- `prefetch(query, *, session_id)` — 同步检索；每轮 user_message 前由 `MemoryManager.prefetch_all()` 触发
- `sync_turn(user_content, assistant_content, ...)` — 会话末写入向量库
- `get_tool_schemas()` — 可暴露给 LLM 的额外工具（如 `honcho_search`）
- `build_system_prompt()` — 加进系统提示的固定块（可选）

`MemoryManager`（`agent/memory_manager.py`，方法名一致）：
- `add_provider(provider)` — **最多挂一个**（设计上限制）；
- `prefetch_all(query)` — 调用所有 provider 的 `prefetch`，拼到 user message 末尾（API kwargs 专用，不持久化）；
- `build_system_prompt()` — 调用所有 provider 的 `build_system_prompt`。

### 3.3.6 前缀缓存策略

`agent/prompt_caching.py` `apply_anthropic_cache_control` 给 Claude/Anthropic Messages API 的请求加 `cache_control: {"type": "ephemeral"}` 标记，命中点：
- 系统提示首条（总是）；
- messages 序列末尾 ≤3 个用户/助手消息（保最近上下文也在缓存范围）。

OpenRouter / Nous Portal 透传同一个 header；其它 chat_completions 兼容 provider 不发送。

## 3.4 运行时行为

```
[Session 启动]
  │
  ├─ MemoryStore.load_from_disk()
  │     ├─ 读 MEMORY.md / USER.md
  │     ├─ 去重 (dict.fromkeys 保序)
  │     ├─ 威胁扫描 → 中毒条目替换为 [BLOCKED]
  │     └─ 冻结 _system_prompt_snapshot
  │
  ├─ MemoryManager.initialize_providers()
  │     └─ 0 或 1 个外挂 provider initialize(session_id=...)
  │
  └─ 构建系统提示
        = "You are Hermes Agent..."
        + memory snapshot (block "memory:" + block "user:")
        + provider.build_system_prompt() (Honcho 等外部块)
        + 用户自定义 system_message
        + skill 内容 (如果用户 /skill-name)

[每轮对话]
  │
  ├─ user_message 进入 → prefetch_all(user_message)
  │     └─ 拼到 API message 末尾（不写持久化历史）
  │
  ├─ 主循环（见维度 1）
  │
  ├─ 工具调用：
  │     ├─ memory(action=add/replace/remove) → atomic_replace 写 MEMORY.md
  │     ├─ skill_manage(...) → 写 ~/.hermes/skills/
  │     └─ 外挂 provider 暴露的工具（如 honcho_search）→ provider 自处理
  │
  └─ 会话末
        ├─ Session DB flush (整 messages 写 SQLite)
        ├─ provider.sync_turn(user, asst) — 异步写向量库
        └─ background_review fork (见维度 2)
```

## 3.5 局限与边界

- **MEMORY.md 字符上限是硬编码 2200**：溢出后只警告不阻止；没有自动 summarize / sliding window 收敛机制。
- **L1 snapshot 不在会话内更新**：本轮 `memory(add)` 写入磁盘后，**本会话剩余轮次**不会重新加载（snapshot 是 frozen）。下次会话才能看到。这是为了保前缀缓存稳定。
- **`MemoryManager` 限定一个 provider**：你不能同时挂 Honcho + Hindsight + mem0，必须二选一。
- **FTS5 不在主流程**：源码里没有把"基于 SQLite FTS5 的全文搜索"做成主循环必经路径；查老对话需要走显式工具（grep transcripts / `hermes logs`）。
- **跨会话记忆隔离弱**：所有 session 公用同一 MEMORY.md（per-`HERMES_HOME`）；多用户必须靠不同 `HERMES_HOME` 或不同 `MemoryProvider` workspace 隔离。
- **没有 hybrid retrieval / re-rank**：prefetch 结果直接拼接，不做相关性融合或去重。

---

# 维度 4 — 上下文管理（四阶段压缩 + 智能路由）

## 4.1 设计立场

`agent/context_compressor.py` 1749 行，是 Hermes 第二大的"算法模块"（仅次于 curator）。设计立场：

- **懒压缩**：除非接近上限，否则不压；不预先减少历史。
- **保护头尾**：系统提示、第一条用户消息、最近 N 条对话不动；只压**中段**。
- **可迭代**：单次压缩不够再压一次，最多 3 轮，每轮必须有进展，否则停。
- **辅助模型生成 summary**：用 `default_aux_model`（每个 provider 在 `ProviderProfile.default_aux_model` 里指定，如 Anthropic 用 `claude-haiku-4-5-20251001`）。
- **summary 文本是 reference，不是 instruction**：通过明确措辞防止 LLM 把"被压缩的旧任务"误当成现在要做的事。

## 4.2 关键代码路径

| 角色 | 文件 | 关键符号 |
|------|------|---------|
| 压缩器 | `agent/context_compressor.py` | `class ContextCompressor` |
| Summary 提示前缀 | `agent/context_compressor.py:37-51` | `SUMMARY_PREFIX` |
| Legacy 兼容前缀 | `agent/context_compressor.py:52` | `LEGACY_SUMMARY_PREFIX` |
| Token 估算 | `agent/context_compressor.py:79-109` | `_content_length_for_budget()` |
| Token / 模型上下文长度 | `agent/model_metadata.py` | `get_model_context_length()` / `estimate_messages_tokens_rough()` / `query_ollama_num_ctx()` |
| 上下文引擎 | `agent/context_engine.py` | （从 `ContextEngine` 导入，详见 import @ `:27`） |
| 主循环触发预检 | `agent/conversation_loop.py:498-565` | `if agent.compression_enabled and len(messages) > ...` |
| 主循环触发运行时 | `agent/conversation_loop.py` 4xx/413 处理段 | API 错误时再压 |
| 辅助模型客户端 | `agent/auxiliary_client.py` | `call_llm(...)`、`_fixed_temperature_for_model()` |

## 4.3 实现细节

### 4.3.1 SUMMARY_PREFIX：reference-not-instruction 措辞

`agent/context_compressor.py:37-51` 是关键文本，所有压缩消息开头都加这段：

```text
[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted
into the summary below. This is a handoff from a previous context
window — treat it as background reference, NOT as active instructions.
Do NOT answer questions or fulfill requests mentioned in this summary;
they were already addressed.
Your current task is identified in the '## Active Task' section of the
summary — resume exactly from there.
IMPORTANT: Your persistent memory (MEMORY.md, USER.md) in the system
prompt is ALWAYS authoritative and active — never ignore or deprioritize
memory content due to this compaction note.
Respond ONLY to the latest user message
that appears AFTER this summary. The current session state (files,
config, etc.) may reflect work described here — avoid repeating it:
```

四个关键控制点：
1. "REFERENCE ONLY" + "NOT as active instructions" — 防止 LLM 把压缩段当成新任务执行。
2. "they were already addressed" — 显式告知里面问的问题/任务已经处理过。
3. "current task is in ## Active Task" — summary 模板里强制有这一节，LLM 只从这节恢复。
4. "Memory (MEMORY.md, USER.md) is ALWAYS authoritative" — 防止压缩注释削弱长期记忆的权重。

### 4.3.2 Token 估算的几个常量

`agent/context_compressor.py:54-76`：

```python
# Minimum tokens for the summary output
_MIN_SUMMARY_TOKENS = 2000
# Proportion of compressed content to allocate for summary
_SUMMARY_RATIO = 0.20
# Absolute ceiling for summary tokens
_SUMMARY_TOKENS_CEILING = 12_000

# Placeholder used when pruning old tool results
_PRUNED_TOOL_PLACEHOLDER = "[Old tool output cleared to save context space]"

# Chars per token rough estimate
_CHARS_PER_TOKEN = 4
# Flat token cost per attached image part. Real cost varies by provider and
# dimensions (Anthropic ≈ width×height/750, GPT-4o up to ~1700 for
# high-detail 2048×2048, Gemini 258/tile), but 1600 is a realistic ceiling
# that keeps compression budgeting honest for multi-image conversations.
_IMAGE_TOKEN_ESTIMATE = 1600
_IMAGE_CHAR_EQUIVALENT = _IMAGE_TOKEN_ESTIMATE * _CHARS_PER_TOKEN
_SUMMARY_FAILURE_COOLDOWN_SECONDS = 600
```

关键 trade-off：
- summary 目标 = `compressed * 20%`，下限 2000、上限 12000；
- 一张图算固定 1600 token（实际跟 provider 和尺寸有关，但用恒定值简化预算）；
- summary 调用失败有 **10 分钟冷却**（`_SUMMARY_FAILURE_COOLDOWN_SECONDS`），避免反复打 aux LLM 失败拖死主循环。

### 4.3.3 Image-aware content length

`agent/context_compressor.py:79-109` `_content_length_for_budget` —— 重要的是它**不只看文本长度**，多模态 content block 里每个 image 算 `_IMAGE_CHAR_EQUIVALENT`（6400 char）：

```python
def _content_length_for_budget(raw_content: Any) -> int:
    """Return the effective char-length of a message's content for token budgeting.

    Plain strings: ``len(content)``. Multimodal lists: sum of text-part
    ``len(text)`` plus a flat ``_IMAGE_CHAR_EQUIVALENT`` per image part
    (``image_url`` / ``input_image`` / Anthropic-style ``image``). This
    keeps the compressor from treating a turn with 5 attached images as
    near-zero tokens just because the text part is empty.
    """
    if isinstance(raw_content, str):
        return len(raw_content)
    if not isinstance(raw_content, list):
        return len(str(raw_content or ""))

    total = 0
    for p in raw_content:
        if isinstance(p, str):
            total += len(p); continue
        if not isinstance(p, dict):
            total += len(str(p)); continue
        ptype = p.get("type")
        if ptype in {"image_url", "input_image", "image"}:
            total += _IMAGE_CHAR_EQUIVALENT
        else:
            total += len(p.get("text", "") or "")
    return total
```

如果不考虑图像，多图对话会被误判为"上下文还很空"导致不触发压缩。

### 4.3.4 主循环触发预检（preflight）

`agent/conversation_loop.py:498-565`：

```python
if agent.compression_enabled and len(messages) > protect_first_n + protect_last_n + 1:
    _preflight_tokens = estimate_request_tokens_rough(messages, tools=agent.tools)
    if agent.context_compressor.should_compress(_preflight_tokens):
        # 最多 3 轮压缩；每轮 messages 数必须下降，否则 break
        for _pass in range(3):
            _orig_len = len(messages)
            messages, active_system_prompt = agent._compress_context(
                messages, system_message, approx_tokens=_preflight_tokens
            )
            if len(messages) >= _orig_len:
                break  # 已经无法进一步压缩
            _preflight_tokens = estimate_request_tokens_rough(...)
            if _preflight_tokens < agent.context_compressor.threshold_tokens:
                break  # 已经回到阈值以下
```

这个三轮限制是经验值：第一次大块压缩、第二次精修、第三次仍不收敛就放弃（让运行时阶段处理）。

### 4.3.5 运行时阶段（413 处理）

主循环 API 调用拿到 413 / context_length_exceeded 时，再调一次 `_compress_context` + 同轮重试。这个路径**比预检更激进**，因为预检模型不准、aux 估算也粗糙，413 是 ground truth。

### 4.3.6 "智能路由"在源码里的对应物

外部图说"智能路由"，源码里没有一个叫 `smart_router` 的模块；最接近的是两个机制：

1. **Aux model 自动选**：`ProviderProfile.default_aux_model` 字段（如 Anthropic 用 Haiku、Kimi 用 K2-think-mini、GMI 用 cheaper-30b）— 压缩 / vision 等辅助任务自动走便宜的同家族模型，主对话走主模型。
2. **模型上下文长度探针**：`agent/model_metadata.py` 的 `get_next_probe_tier()` 在主调用 413 后**调整对该模型的上下文估算缓存**，下次同一模型不会再撞同一个上限；`query_ollama_num_ctx()` 则直接调 Ollama `/api/show` 拿 `num_ctx` 实际值。

不存在"按 query 类型路由到不同主模型"的逻辑（用户必须显式 `/model` 切换）。

## 4.4 运行时行为

```
[每轮 API 调用前]
  │
  ├─ preflight 估算：estimate_request_tokens_rough(messages, tools)
  │     └─ < threshold (默认 context_length × 75%) → 不压
  │
  ├─ ≥ threshold → 进 compress loop
  │     for _pass in 1..3:
  │         ContextCompressor.compress(messages)
  │           ├─ 分段：head (protect_first_n) | middle (压) | tail (protect_last_n)
  │           ├─ middle 序列化文本 → aux LLM 调用
  │           ├─ aux LLM 返回 summary text
  │           ├─ 拼回 messages = head + [SUMMARY message] + tail
  │           └─ 如果 tail 首条是 user → 把 summary 前缀塞进它，省一条消息
  │         ↓
  │         未减少？break
  │         小于阈值？break
  │
  ├─ 调用 API
  │
  └─ 响应：
        ├─ 200 OK → 正常处理
        ├─ 413 / context exceeded →
        │     再压一次 → 同轮重试
        └─ 4xx 其它 → 错误分类（见维度 1）
```

## 4.5 局限与边界

- **Summary 由小模型生成**：信息保真度取决于 aux model 的概括能力；遇到长 tool 输出（如全文 stack trace）小模型可能丢掉关键 token。
- **三轮压缩无 cache 复用**：每轮都重新调 aux LLM；3 轮 = 3 倍 aux 成本。
- **`protect_first_n` / `protect_last_n` 是固定参数**：没有"根据对话复杂度自适应"逻辑。
- **失败 10 分钟冷却**：如果 aux model 不稳定，会有 10 分钟主循环无压缩可用，意味着可能被打到 413 重试链路。
- **"智能路由"在源码语义下=辅助任务的廉价模型选择**：不是"按 user query 类型选主模型"；切主模型仍是用户责任。

---

# 维度 5 — 模型锁定（"无锁定" + 多 Provider）

## 5.1 设计立场

Hermes 把"接入哪个模型供应商"完全做成**声明式的 Profile + 插件目录发现**，不是在主代码里写 `if provider == "anthropic": ...`。三层组合：

1. **`ProviderProfile` dataclass** 把供应商的所有 quirks（auth、endpoint、temperature 规则、aux model、自定义 headers、消息预处理钩子等）封装成 1 个对象。
2. **`providers/__init__.py` 的 `_REGISTRY` 单例 + 三层发现**：bundled plugin → user plugin (`$HERMES_HOME/plugins/model-providers/`) → legacy 单文件兜底；后写覆盖前写。
3. **`api_mode`** 字段把"OpenAI Chat Completions / Anthropic Messages / OpenAI Codex Responses" 三种协议方言抽出来，每个 mode 对应一组 transport 实现，主循环只关心 mode、不关心具体 provider。

**最重要的设计立场是 declarative-not-imperative**：基类 `ProviderProfile` 在 `providers/base.py:7` 的 module docstring 明确说：

> "Provider profiles are DECLARATIVE — they describe the provider's behavior. They do NOT own client construction, credential rotation, or streaming. Those stay on AIAgent."

也就是说 profile 只描述"我是什么"，**不持有**客户端、不轮换凭据、不管流式 —— 这些行为留给 `AIAgent` 主类。

## 5.2 关键代码路径

| 角色 | 文件 | 关键符号 |
|------|------|---------|
| Profile 基类 | `providers/base.py:38-184` | `class ProviderProfile` + 4 个 override 钩子 |
| OMIT 哨兵 | `providers/base.py:21` | `OMIT_TEMPERATURE = object()` |
| Default `fetch_models` | `providers/base.py:132-184` | Bearer auth + User-Agent + 解析 `{data:[...]}` |
| 注册表入口 | `providers/__init__.py:43-50` | `_REGISTRY` / `_ALIASES` / `_BUNDLED_PLUGINS_DIR` |
| 注册函数 | `providers/__init__.py:53-62` | `register_provider(profile)` |
| 查询函数 | `providers/__init__.py:65-88` | `get_provider_profile()` / `list_providers()` |
| 三层发现 | `providers/__init__.py:140-191` | `_discover_providers()` |
| 用户插件目录 | `providers/__init__.py:91-99` | `_user_plugins_dir()` → `$HERMES_HOME/plugins/model-providers/` |
| Profile 子类示例 | `plugins/model-providers/anthropic/__init__.py:13-52` | `AnthropicProfile`（覆盖 `fetch_models` 用 `x-api-key`） |
| Profile 普通示例 | `plugins/model-providers/ollama-cloud/__init__.py:6-13` | 直接实例化 `ProviderProfile`，不子类化 |
| 限流状态 | `agent/rate_limit_tracker.py` | `RateLimitState` / `RateLimitBucket` |

## 5.3 实现细节

### 5.3.1 `ProviderProfile` 的完整字段（30 个）

`providers/base.py:38-130`：

```python
@dataclass
class ProviderProfile:
    """Base provider profile — subclass or instantiate with overrides."""

    # ── Identity ─────────────────────────────────────────────
    name: str
    api_mode: str = "chat_completions"   # 关键！anthropic_messages | codex_responses 也可选
    aliases: tuple = ()

    # ── Human-readable metadata ───────────────────────────────
    display_name: str = ""               # e.g. "GMI Cloud"
    description: str = ""                # picker 副标题
    signup_url: str = ""                 # setup 时给的注册链接

    # ── Auth & endpoints ─────────────────────────────────────
    env_vars: tuple = ()                 # 凭据环境变量名列表（按序尝试）
    base_url: str = ""
    models_url: str = ""                 # 覆盖 base_url/models（OpenRouter 等用）
    auth_type: str = "api_key"           # api_key|oauth_device_code|oauth_external|copilot|aws_sdk
    supports_health_check: bool = True   # False → doctor skip /models 探针

    # ── Model catalog ─────────────────────────────────────────
    fallback_models: tuple = ()          # /model picker 离线 fallback
    hostname: str = ""                   # URL→provider 反查

    # ── Client-level quirks ─
    default_headers: dict[str, str] = field(default_factory=dict)

    # ── Request-level quirks ─────────────────────────────────
    fixed_temperature: Any = None        # None=用 caller 默认, OMIT_TEMPERATURE=完全不发
    default_max_tokens: int | None = None
    default_aux_model: str = ""          # 压缩/vision 等辅助任务用便宜模型
```

`api_mode` 是这套设计的核心枢纽 —— 它把"transport 协议"和"provider 身份"解耦：
- `chat_completions` → OpenAI 兼容（默认，最多 provider 走这条）
- `anthropic_messages` → Anthropic Messages API（用 `x-api-key` + `anthropic-version` header）
- `codex_responses` → OpenAI Codex Responses API（结构跟 chat completions 完全不同）

### 5.3.2 四个可覆盖的钩子

`providers/base.py:95-130`：

```python
def prepare_messages(self, messages):
    """Provider-specific message preprocessing.
    Called AFTER codex field sanitization, BEFORE developer role swap.
    Default: pass-through.
    """
    return messages

def build_extra_body(self, *, session_id=None, **context):
    """Provider-specific extra_body fields.
    Merged into the API kwargs extra_body. Default: empty dict.
    """
    return {}

def build_api_kwargs_extras(self, *, reasoning_config=None, **context):
    """Provider-specific kwargs split between extra_body and top-level api_kwargs.

    This split exists because some providers put reasoning config in
    extra_body (OpenRouter: extra_body.reasoning) while others put it
    as top-level api_kwargs (Kimi: api_kwargs.reasoning_effort).

    Default: ({}, {}).
    """
    return {}, {}

def fetch_models(self, *, api_key=None, timeout=8.0):
    """Fetch the live model list from the provider's models endpoint."""
    # 默认实现：Bearer auth + Accept: application/json + UA "hermes-cli/<ver>"
```

四个钩子按"出现频率"递减：`fetch_models` 是最常被覆盖的（每个非标 OpenAI provider 的 models 端点都不同），`build_extra_body` / `build_api_kwargs_extras` 用于 reasoning config 这类厂商特定字段，`prepare_messages` 最少用（codex 这类需要做角色重映射时才用）。

### 5.3.3 注册表的三层发现

`providers/__init__.py:140-191`：

```python
def _discover_providers() -> None:
    """Populate the registry by importing every provider plugin.

    Order:
      1. Bundled plugins at ``<repo>/plugins/model-providers/<name>/``
      2. User plugins at ``$HERMES_HOME/plugins/model-providers/<name>/``
      3. Legacy per-file modules at ``providers/<name>.py`` (back-compat)

    Each step imports its plugins, which call ``register_provider()`` at
    module-level. Later steps win on name collision.
    """
    global _discovered
    if _discovered:
        return
    _discovered = True

    # 1. Bundled plugins
    if _BUNDLED_PLUGINS_DIR.is_dir():
        for child in sorted(_BUNDLED_PLUGINS_DIR.iterdir()):
            if not child.is_dir() or child.name.startswith(("_", ".")):
                continue
            _import_plugin_dir(child, "bundled")

    # 2. User plugins under $HERMES_HOME
    user_dir = _user_plugins_dir()
    if user_dir is not None:
        for child in sorted(user_dir.iterdir()):
            if not child.is_dir() or child.name.startswith(("_", ".")):
                continue
            _import_plugin_dir(child, "user")

    # 3. Legacy providers/<name>.py
    try:
        import pkgutil
        import providers as _pkg
        for _importer, modname, _ispkg in pkgutil.iter_modules(_pkg.__path__):
            if modname.startswith("_") or modname == "base":
                continue
            try:
                importlib.import_module(f"providers.{modname}")
            except ImportError as exc:
                logger.warning("Failed to import legacy provider module %s: %s", modname, exc)
    except Exception:
        pass
```

`_import_plugin_dir`（`:102-137`）的关键巧思：
- **bundled** 用稳定 import 路径 `plugins.model_providers.<safe_name>`，相对 import 可用；
- **user** 用一次性模块名 `_hermes_user_provider_<safe_name>`，避免不同 `$HERMES_HOME` 路径下的同名 plugin 互相 alias。

### 5.3.4 已上架的 30 个 bundled provider

`plugins/model-providers/` 目录实际清单（截至该 commit）：

```
ai-gateway, alibaba, alibaba-coding-plan, anthropic, arcee, azure-foundry,
bedrock, copilot, copilot-acp, custom, deepseek, gemini, gmi, huggingface,
kilocode, kimi-coding, minimax, nous, novita, nvidia, ollama-cloud,
openai-codex, opencode-zen, openrouter, qwen-oauth, stepfun, xai, xiaomi,
zai
```

共 29 个目录 + 1 个 README → 实际 profile 数比图里 "18+" 多了近一倍。其中：
- 走 `auth_type="api_key"`（绝大多数）
- 走 `oauth_device_code`：`qwen-oauth` 这类
- 走 `oauth_external`：`openai-codex`（共享 OpenAI CLI 的 token）
- 走 `copilot`：`copilot` / `copilot-acp`（GitHub Copilot 协议）
- 走 `aws_sdk`：`bedrock`

### 5.3.5 子类示例 vs 直接实例化

**子类化的例子**（`plugins/model-providers/anthropic/__init__.py:13-52`）：

```python
class AnthropicProfile(ProviderProfile):
    """Native Anthropic — uses x-api-key header, not Bearer."""

    def fetch_models(self, *, api_key=None, timeout=8.0):
        """Anthropic uses x-api-key header and anthropic-version."""
        if not api_key: return None
        try:
            req = urllib.request.Request("https://api.anthropic.com/v1/models")
            req.add_header("x-api-key", api_key)
            req.add_header("anthropic-version", "2023-06-01")
            req.add_header("Accept", "application/json")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
            return [m["id"] for m in data.get("data", []) if isinstance(m, dict) and "id" in m]
        except Exception as exc:
            logger.debug("fetch_models(anthropic): %s", exc)
            return None

anthropic = AnthropicProfile(
    name="anthropic",
    aliases=("claude", "claude-oauth", "claude-code"),
    api_mode="anthropic_messages",
    env_vars=("ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"),
    base_url="https://api.anthropic.com",
    auth_type="api_key",
    default_aux_model="claude-haiku-4-5-20251001",
)
register_provider(anthropic)
```

**直接实例化的例子**（`plugins/model-providers/ollama-cloud/__init__.py:6-14`）：

```python
ollama_cloud = ProviderProfile(
    name="ollama-cloud",
    aliases=("ollama_cloud",),
    default_aux_model="nemotron-3-nano:30b",
    env_vars=("OLLAMA_API_KEY",),
    base_url="https://ollama.com/v1",
)
register_provider(ollama_cloud)
```

—— Ollama Cloud 跟标准 OpenAI 兼容、用 Bearer auth、没有 quirks，**整个 profile 8 行就完成接入**。

### 5.3.6 别名机制

`register_provider` 把 `name` 和每个 `alias` 都注册：

```python
def register_provider(profile: ProviderProfile) -> None:
    _REGISTRY[profile.name] = profile
    for alias in profile.aliases:
        _ALIASES[alias] = profile.name
```

`get_provider_profile()` 先查 `_ALIASES` 再查 `_REGISTRY` —— 用户在 CLI 里写 `--provider claude`、`--provider claude-code`、`--provider anthropic` 都能拿到 `AnthropicProfile`。

### 5.3.7 限流跟踪

`agent/rate_limit_tracker.py` 解析每次 API 响应的 `x-ratelimit-*` 系列响应头（OpenAI / Anthropic / OpenRouter 都用类似 header），把 TPM / RPM / 剩余 token / 剩余 request 维护进 `RateLimitState`。`RateLimitBucket.remaining_seconds_now` 计算"现在到 reset 还剩多少秒"，用于退避决策。这部分跟 Provider 解耦：所有 provider 共享同一个 tracker，只要返回的 header 名字识别得出来。

## 5.4 运行时行为

```
hermes 启动 / Agent init
  │
  ├─ runtime 决定 provider 名（命令行 --provider / config.yaml / 上次会话残留）
  │
  ├─ get_provider_profile(name)
  │     ├─ 首次调用触发 _discover_providers()
  │     │     ├─ 扫 bundled
  │     │     ├─ 扫 user $HERMES_HOME/plugins/model-providers/
  │     │     └─ 扫 legacy providers/*.py
  │     ├─ alias 解析
  │     └─ 返回 ProviderProfile 实例 (或 None)
  │
  ├─ 根据 profile.api_mode 选择 transport：
  │     ├─ chat_completions → OpenAI SDK / 兼容客户端
  │     ├─ anthropic_messages → anthropic SDK
  │     └─ codex_responses → Codex 专用 transport
  │
  └─ 主循环每轮 API 调用：
        ├─ profile.prepare_messages(messages) → 预处理
        ├─ profile.build_extra_body(...) → 拼 extra_body
        ├─ profile.build_api_kwargs_extras(reasoning_config=...) → 拼顶层 kwargs
        ├─ 加 profile.default_headers 到 client
        ├─ 选 fixed_temperature / OMIT_TEMPERATURE
        ├─ SDK.chat.completions.create(...) / SDK.messages.create(...)
        └─ RateLimitTracker.observe(response.headers) → 更新窗口
```

## 5.5 局限与边界

- **`fetch_models` 是同步的**：用 `urllib.request.urlopen` 阻塞调用，超时 8s。OpenRouter / Hugging Face 这种 100+ 模型的目录可能拖慢 `/model` 选择器加载（但只在用户首次进 picker 时一次）。
- **没有统一的 tool-calling 能力适配层**：小模型不支持 native tool calling 时，源码里没看到"自动切到 prompt-engineered JSON Mode"的兜底；这部分依赖各 SDK 的原生支持。
- **streaming delta 格式由 SDK 处理**：Anthropic `content_block_delta` 和 OpenAI `choices[0].delta.content` 是不同 schema，主循环要 case-by-case 处理（消息清理管线那段就是为这个）。
- **fallback 是静态配置**：`fallback_model` 在 config.yaml 写死；没有"根据 latency / cost / success rate 动态选 next 提供商"。
- **`MemoryManager` / `SessionDB` 写得是 provider 无关的**：但具体 provider 的成本/限额/响应特性差异要靠 dashboard 监控，源码没有 in-process 成本聚合视图。

---

# 维度 6 — 本地推理（Ollama / vLLM / llama.cpp）

## 6.1 设计立场

Hermes 对"本地推理"不做特殊的引擎层 —— **它把 Ollama / vLLM / llama.cpp 都当作"自带 OpenAI 兼容端点的远程 provider"**，区别只在两点：

1. **端点检测**：URL 是否落在本地 / Tailscale CGNAT 网段；
2. **超时放大**：本地模型慢，stream read timeout 和 stale detection 都拉长。

源码里没有"启动本地推理服务""自动 pull 模型""GPU 资源调度"这类逻辑；用户负责自己跑 `ollama serve` / `vllm serve` / `llama.cpp -ngl`，Hermes 只负责"接得上、配得当"。

## 6.2 关键代码路径

| 角色 | 文件 | 关键符号 |
|------|------|---------|
| 本地端点判定 | `agent/model_metadata.py:79-83` | `_TAILSCALE_CGNAT = ipaddress.IPv4Network("100.64.0.0/10")` |
| Ollama 上下文探针 | `agent/model_metadata.py` | `query_ollama_num_ctx(model, base_url, api_key)` |
| Ollama Cloud profile | `plugins/model-providers/ollama-cloud/__init__.py` | 接 `https://ollama.com/v1` |
| 自定义 provider | `plugins/model-providers/custom/__init__.py` | 用户填 base_url，常用来接本地 Ollama / vLLM |
| 流式 stale 检测 | `agent/conversation_loop.py` 流式段 | 90s 无数据视为半挂 |
| 辅助任务模型 | `agent/auxiliary_client.py` | aux model 走同 base_url，本地也享受 |

## 6.3 实现细节

### 6.3.1 本地网段识别

`agent/model_metadata.py:79-83` 单行常量定义：

```python
_TAILSCALE_CGNAT = ipaddress.IPv4Network("100.64.0.0/10")
```

`is_local_endpoint(base_url)` 函数（同文件）把以下情况都识别为本地：
- `localhost` / `127.0.0.0/8`
- 私有网段 `10.0.0.0/8` / `172.16.0.0/12` / `192.168.0.0/16`
- Tailscale CGNAT `100.64.0.0/10`

这让 mesh VPN 里的 GPU 节点（Tailscale 给的 100.x IP）也被当作本地，超时随之放宽。

### 6.3.2 Ollama 专用上下文探针

Ollama 有非标 API `GET /api/show`，返回模型的 `num_ctx` 字段（实际上下文长度）。Hermes 的 `query_ollama_num_ctx(model, base_url, api_key)` 在 agent init 阶段调用一次，把得到的真实 context 存到 model_metadata cache，后续 `get_model_context_length(model)` 就返回真实值，不用查表猜测。

vLLM 和 llama.cpp 都暴露 OpenAI 兼容的 `/v1/models`，返回的 metadata 里通常带 `context_length` 字段；Hermes 走 `fetch_models()` 默认路径就能拿到。

### 6.3.3 本地接入靠 `custom` provider

`plugins/model-providers/custom/__init__.py` 是一个"占位 profile" —— 它不写死 `base_url`，由用户在 `config.yaml` 或 CLI 命令里指定。本地 Ollama 的标准接法：

```yaml
# ~/.hermes/config.yaml
model:
  provider: custom
  model: qwen2.5-coder:32b
  base_url: http://localhost:11434/v1
  api_key: ollama  # Ollama 不验证，但要有非空
```

或者 vLLM：

```yaml
model:
  provider: custom
  model: meta-llama/Meta-Llama-3.1-70B-Instruct
  base_url: http://my-gpu-node:8000/v1
  api_key: not-needed
```

### 6.3.4 流式超时为本地放大

主循环流式段（`agent/conversation_loop.py` 流式调用包装）检测到 `is_local_endpoint(base_url)` 时把 `stream_read_timeout` 和 `stale_detect_seconds` 放大；具体倍数取决于 provider，但行为是"远程 30s 不响应判半挂、本地 120-240s 才判"。

### 6.3.5 本地推理的 tool calling 现状

源码里**没有**针对"小模型不支持 native function calling"的 prompt engineering fallback。`model_tools.py` 把工具描述编成 JSON Schema 直接给 LLM，期望 provider 原生支持。如果 LLM 返回的 message 没有结构化 `tool_calls` 字段，主循环的重试矩阵会进 `invalid_tool_retries` 路径，反复多轮可能消耗 budget。

实操中能跑 hermes 全部能力的本地模型，需要：
- ≥ 7B 参数；
- 经过 tool-calling fine-tune（如 Qwen2.5-Coder / Mistral / Llama3.1 instruct）；
- 走 Ollama / vLLM 的 OpenAI-compatible function calling 端点。

## 6.4 运行时行为

```
hermes --provider custom --base-url http://localhost:11434/v1 --model qwen2.5-coder:32b
  │
  ├─ Agent init
  │     ├─ get_provider_profile("custom") → 通用 OpenAI 兼容 profile
  │     ├─ is_local_endpoint("http://localhost:11434/v1") → True
  │     ├─ query_ollama_num_ctx("qwen2.5-coder:32b", ...) → 真实 num_ctx (如 32768)
  │     ├─ get_model_context_length("qwen2.5-coder:32b") → 32768 (cached)
  │     └─ 流式 timeout 放大到 120-240s
  │
  └─ 主循环（同维度 1，无特殊分支）
        └─ SDK.chat.completions.create(model="qwen2.5-coder:32b", ...)
              → POST http://localhost:11434/v1/chat/completions
```

## 6.5 局限与边界

- **不自动启动本地服务**：用户必须先跑 `ollama serve` / `vllm serve`；Hermes 不会 spawn 它们。
- **不自动下载模型**：`ollama pull qwen2.5-coder:32b` 也是用户责任；调用未下载的模型会返回 404。
- **本地 embedding 没集成**：MEMORY 系统不依赖向量；如果挂了外部 `MemoryProvider`（如 Honcho），embedding 仍走 provider 自己的策略，**不会用本地 Ollama embedding** 模型。
- **本地 GPU 调度无能力**：Ollama 的 `OLLAMA_NUM_GPU` / `OLLAMA_KEEP_ALIVE` 这些只能通过环境变量传给 Ollama 自己，Hermes 不管。
- **小模型无 tool-calling 兜底**：上面 6.3.5 说过，不支持 native function calling 的模型基本跑不动 agent loop。
- **vLLM / llama.cpp 没有专属 profile**：都走 `custom` provider，意味着 vLLM 的 LoRA adapter selection、llama.cpp 的 `grammar` 这类高级特性都用不上。

---

# 维度 7 — 沙箱执行（多后端）

## 7.1 设计立场

`tools/environments/` 目录提供**一套统一抽象的多后端执行环境**，让 agent 不管在本机、容器、远程 SSH、还是 serverless 函数里执行 bash，调用代码完全一致。原图说"6 种后端"，源码实际有 **7-8 个**：

```
local.py            -- 宿主机直接执行
docker.py           -- Docker 容器
ssh.py              -- 远程主机 SSH
modal.py            -- Modal serverless（Sandbox.exec）
managed_modal.py    -- Modal 的 hosted 版本
daytona.py          -- Daytona dev container
singularity.py      -- HPC Singularity 容器
vercel_sandbox.py   -- Vercel sandbox
```

加上 `file_sync.py`（多后端通用文件同步）和 `modal_utils.py`（modal 工具）。

设计立场：**统一的 `BaseEnvironment` 抽象 + 每个后端实现 `_run_bash()` 和 `cleanup()` 两个方法**。所有的"CWD 持久化、环境变量快照、stdin 嵌入、超时控制、活动心跳、中断处理"都在基类里，后端不重复造轮子。

## 7.2 关键代码路径

| 角色 | 文件 | 关键符号 |
|------|------|---------|
| 抽象基类 | `tools/environments/base.py:288-345` | `class BaseEnvironment(ABC)` |
| ProcessHandle protocol | `tools/environments/base.py:187-202` | duck typing：`poll/kill/wait/stdout/returncode` |
| 抽象方法 1 | `tools/environments/base.py:327-340` | `_run_bash(cmd_string, *, login, timeout, stdin_data)` |
| 抽象方法 2 | `tools/environments/base.py:342-345` | `cleanup()` |
| 会话快照 | `tools/environments/base.py:351-401` | `init_session()`：export -p / declare -f / alias -p |
| Stdin heredoc 嵌入 | `tools/environments/base.py:473-477` | `_embed_stdin_heredoc()` |
| 非阻塞等待 | `tools/environments/base.py:483+` | `_wait_for_process()`：select 100ms 轮询 + activity heartbeat |
| Local 后端 | `tools/environments/local.py:1-80+` | `_msys_to_windows_path`, `_resolve_safe_cwd`, env blocklist |
| Docker 后端 | `tools/environments/docker.py:1-100+` | 安全硬化默认 + env 验证 |
| 文件同步 | `tools/environments/file_sync.py` | `iter_sync_files()`：tar + base64 |

## 7.3 实现细节

### 7.3.1 `BaseEnvironment` 的核心约束

`tools/environments/base.py:288-321`：

```python
class BaseEnvironment(ABC):
    """Common interface and unified execution flow for all Hermes backends.

    Subclasses implement ``_run_bash()`` and ``cleanup()``.  The base class
    provides ``execute()`` with session snapshot sourcing, CWD tracking,
    interrupt handling, and timeout enforcement.
    """

    # Subclasses that embed stdin as a heredoc (Modal, Daytona) set this.
    _stdin_mode: str = "pipe"  # "pipe" or "heredoc"

    # Snapshot creation timeout (override for slow cold-starts).
    _snapshot_timeout: int = 30

    def get_temp_dir(self) -> str:
        """Return the backend temp directory used for session artifacts."""
        return "/tmp"

    def __init__(self, cwd: str, timeout: int, env: dict = None):
        self.cwd = cwd
        self.timeout = timeout
        self.env = env or {}

        self._session_id = uuid.uuid4().hex[:12]
        temp_dir = self.get_temp_dir().rstrip("/") or "/"
        self._snapshot_path = f"{temp_dir}/hermes-snap-{self._session_id}.sh"
        self._cwd_file = f"{temp_dir}/hermes-cwd-{self._session_id}.txt"
        self._cwd_marker = _cwd_marker(self._session_id)
        self._snapshot_ready = False
```

每个 environment 实例都有：
- `_session_id`：12-char hex，用于 snapshot/cwd 文件命名，**多个并发 environment 之间隔离**；
- `_snapshot_path`：`/tmp/hermes-snap-<sid>.sh`，第一次 `init_session()` 后写入；
- `_cwd_file`：`/tmp/hermes-cwd-<sid>.txt`，记录当前 CWD（每条命令更新）；
- `_cwd_marker`：用于在 stdout 里识别 CWD 输出的哨兵字符串。

### 7.3.2 会话快照机制

`tools/environments/base.py:351-401` `init_session()` 的策略是**捕获 login shell 的完整环境到 snapshot 文件，后续每条命令重新 source**。捕获的内容：

- `export -p` → 所有 export 的环境变量
- `declare -f | grep -vE '^_[^_]'` → shell 函数（过滤内部函数避免污染）
- `alias -p` → 所有 alias
- `shopt -s expand_aliases` → 让 alias 在非交互 shell 也展开

每条后续命令的 wrap 形如：

```bash
source /tmp/hermes-snap-<sid>.sh >/dev/null 2>&1
builtin cd -- '<quoted_cwd>'
eval '<escaped_command>'
pwd -P > /tmp/hermes-cwd-<sid>.txt
printf '\n__HERMES_CWD_<sid>__%s__HERMES_CWD_<sid>__\n' "$(pwd -P)"
```

效果：
- CWD 跨命令持久化（命令 A 里 `cd foo`，命令 B 自动从 `foo` 开始）；
- 环境变量 / alias / 函数跨命令保留；
- 命令结束后 `pwd -P` 输出到 stdout 末尾 + 写文件，主进程双通道拿到新 CWD。

**Trade-off**：每条命令引入 ~50ms 的 source overhead；对于密集小命令（如 git status × 100）开销可观，但保证状态正确。

### 7.3.3 stdin 双模式

`_stdin_mode = "pipe"` 或 `"heredoc"`：

- **pipe**（local / docker / ssh）：通过 `subprocess.Popen(..., stdin=PIPE)` 流式写；
- **heredoc**（modal / daytona）：因为 Modal `Sandbox.exec` 没有真正的 stdin 管道（async），把 stdin 数据直接嵌入到命令脚本里：

```python
def _embed_stdin_heredoc(command: str, stdin_data: str) -> str:
    delimiter = f"HERMES_STDIN_{uuid.uuid4().hex[:12]}"
    return f"{command} << '{delimiter}'\n{stdin_data}\n{delimiter}"
```

随机 delimiter 保证 stdin 数据里偶然含的 `EOF` 之类不会误终止 heredoc。

### 7.3.4 非阻塞 wait + 活动心跳

`tools/environments/base.py:483+` `_wait_for_process()` 是这层比较精细的部分：

- POSIX：`select.select([fd], [], [], 0.1)` 100ms 轮询，bash 退出后再多收 2 轮缓冲输出；
- Windows：`select()` 不支持 pipe FD，用守护线程做阻塞 read；
- **每轮调用 `touch_activity_if_due(state, "command running")`**：长跑命令周期性"打卡"，gateway 的 inactivity timeout 不会误杀；
- 主循环 `is_interrupted()` 为 True 时 `proc.kill()` 抛 `KeyboardInterrupt`。

### 7.3.5 Docker 后端的安全硬化

`tools/environments/docker.py` 的默认行为：

- **`--cap-drop ALL`** → 移除所有 Linux capability，需要单独 `--cap-add` 才能加回；
- **`--security-opt no-new-privileges`** → 禁止 setuid / setgid 提权；
- **`--pids-limit`** → 容器内进程数上限；
- **环境变量黑名单** `_HERMES_PROVIDER_ENV_BLOCKLIST`（在 `local.py:79+` 定义并被 docker 复用）→ 凭据 env vars 不会通过 `-e` 传入容器；
- 用户可配置：`docker_image_spec`、`docker_env`、`docker_forward_env`、`docker_network`、`docker_volumes`、`docker_resource_limits`、`docker_caps_add` / `docker_caps_drop`、`docker_privileged`、`docker_workdir`。

环境变量名验证用 `_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")`，非法名直接 warn 并丢弃，防止注入。

### 7.3.6 文件同步：tar + base64 over stdin

`tools/environments/file_sync.py` 把多个文件打 tar、base64 编码、通过命令注入到后端：

```text
echo '<base64>' | base64 -d | tar -xpf -
```

对 Modal / Daytona 这类"没有原生 file copy API"的 sandbox 有效。每个文件还会被恢复 mode：`chmod {mode}`。

### 7.3.7 Local 后端的两个细节

`tools/environments/local.py`：

- **`_msys_to_windows_path`**：在 Windows + Git Bash 环境，bash 返回 `/c/Users/x` 而 Windows Python 需要 `C:\Users\x`；正则一行转换，否则 `subprocess.Popen(cwd=...)` 抛 FileNotFoundError 整段后续 terminal 调用都崩。
- **`_resolve_safe_cwd`**：上一条 terminal 命令把自己 CWD 删了的常见场景，往上找最近存在的祖先目录；找不到回退 `tempfile.gettempdir()`。这是 issue #17558 的修复。

## 7.4 运行时行为

```
Agent 调用 terminal({command, cwd, timeout, stdin?, ...})
  │
  ├─ 选择 environment 后端（config.yaml 或 CLI flag）
  │     terminal_backend: local | docker | ssh | modal | daytona | singularity | vercel
  │
  ├─ Environment 实例（or 复用已有实例）：
  │     __init__(cwd, timeout, env)
  │         → 生成 _session_id
  │
  ├─ init_session() 首次调用
  │     ├─ 后端启动一个 bash -l 子进程
  │     ├─ export -p / declare -f / alias -p → 写 /tmp/hermes-snap-<sid>.sh
  │     └─ _snapshot_ready = True
  │
  └─ execute(command, cwd, timeout)
        ├─ _wrap_command(command, cwd) → 包 source snapshot + cd + eval
        ├─ _run_bash(...) → 后端特定的进程 spawn（subprocess / Sandbox.exec / SSH session / ...）
        ├─ _wait_for_process(proc, timeout):
        │     ├─ 100ms 轮询 select()
        │     ├─ touch_activity_if_due (心跳)
        │     ├─ 中断检查
        │     └─ 收集 stdout 直到 EOF
        ├─ 解析 stdout 末尾的 __HERMES_CWD_<sid>__ 哨兵
        ├─ self.cwd = 新 CWD（持久化到下次 execute）
        └─ 返回 {stdout, returncode, cwd_result}
```

## 7.5 局限与边界

- **快照 source overhead** 每条命令 ~50ms：密集小命令场景（脚本 loop 100 次 git status）开销显著。
- **Sandbox 内不能直接调 LLM**：sandbox 跑的是 bash，没有 hermes runtime 注入，LLM 调用必须经 stdout 回到主进程才能再触发。
- **sandbox 内调本地服务难**：本地 Ollama 在 docker 容器里要 `host.docker.internal` 或 `--network=host`，没有 magic。
- **背景进程的 stdout 泄漏未根除**：`cmd &` 继承 stdout pipe 可能让 bash exit 后还有数据流出；代码做了 2 轮缓冲收尾但极端场景仍可能丢/卡。
- **多 environment 实例并发**：每个 environment 有独立 `_session_id` 隔离，但底层 docker daemon / SSH 连接池有上限，并发量上去会触发 throttle。
- **没有跨后端 portable image**：Local 跟 Docker 的 PATH / libc / shell 版本可能差异显著，"local 跑通的 skill 复制到 docker 不工作"是已知摩擦点。

---

# 维度 8 — 子 Agent / 并行（delegate_tool）

## 8.1 设计立场

`tools/delegate_tool.py` 2801 行（单文件！），实现一个"父-子 agent 委托 + 并行 + 隔离"的子系统。核心设计立场：

- **完全隔离的子 agent 实例**：每个子 `AIAgent` 有独立对话历史、独立终端 session、独立工具子集、独立任务 ID；父只看到"delegate 调用 + 摘要返回"。
- **限制递归深度**：默认 1（父→子，孙子拒绝），可配到 3。
- **黑名单工具**：子 agent 不能 `delegate_task` / `clarify` / `memory` / `send_message` / `execute_code`。
- **审批自动化**：子 agent 在 worker 线程，没法弹 CLI 交互式审批，默认 auto-deny（安全），用户可 opt-in YOLO。
- **并发上限 + ThreadPoolExecutor**：批量委托 N 个并行，默认 worker 上限 3。

## 8.2 关键代码路径

| 角色 | 文件 | 关键符号 |
|------|------|---------|
| 入口工具 | `tools/delegate_tool.py:1-50` | `handle_delegate_task(...)` 入口（docstring 明确架构） |
| 黑名单 | `tools/delegate_tool.py:45-53` | `DELEGATE_BLOCKED_TOOLS` frozenset |
| Auto-deny / auto-approve 回调 | `tools/delegate_tool.py:73-111` | `_subagent_auto_deny` / `_subagent_auto_approve` |
| 子 toolset 列表 | `tools/delegate_tool.py:122-130` | `_SUBAGENT_TOOLSETS` 计算 |
| 默认并发上限 | `tools/delegate_tool.py:132` | `_DEFAULT_MAX_CONCURRENT_CHILDREN = 3` |
| 深度限制 | `tools/delegate_tool.py:133-137` | `MAX_DEPTH=1` / `_MIN_SPAWN_DEPTH=1` / `_MAX_SPAWN_DEPTH_CAP=3` |
| 全局暂停 | `tools/delegate_tool.py:149-150` | `_spawn_pause_lock` / `_spawn_paused` |
| 活跃 subagent 注册表 | `tools/delegate_tool.py:152-155` | `_active_subagents` dict |
| 子 agent 构造 | `tools/delegate_tool.py:600+` | `_build_child_agent(...)` |
| 单子运行 | `tools/delegate_tool.py:1200+` | `_run_single_child(...)` |
| 并行运行 | `tools/delegate_tool.py:850+` | `_run_parallel_children(...)` |
| 结果聚合 | `tools/delegate_tool.py:1500+` | `_aggregate_results(...)` |

## 8.3 实现细节

### 8.3.1 黑名单工具

`tools/delegate_tool.py:44-53`：

```python
# Tools that children must never have access to
DELEGATE_BLOCKED_TOOLS = frozenset(
    [
        "delegate_task",  # no recursive delegation
        "clarify",        # no user interaction
        "memory",         # no writes to shared MEMORY.md
        "send_message",   # no cross-platform side effects
        "execute_code",   # children should reason step-by-step, not write scripts
    ]
)
```

注释解释了每条的理由 —— 这是 Hermes 防止子 agent "破坏共享状态 / 阻塞 UI / 失控嵌套" 的核心硬规则。

### 8.3.2 审批回调的设计

`tools/delegate_tool.py:60-111`，原文 docstring 直接说明问题：

> "Subagents run inside a ThreadPoolExecutor worker. The CLI's interactive approval callback is stored in tools/terminal_tool.py's threading.local(), so worker threads do NOT inherit it. Without a callback, prompt_dangerous_approval() falls back to input() from the worker thread, which deadlocks against the parent's prompt_toolkit TUI that owns stdin."

解决方案：

```python
def _subagent_auto_deny(command: str, description: str, **kwargs) -> str:
    """Auto-deny dangerous commands in subagent threads (safe default)."""
    logger.warning("Subagent auto-denied dangerous command: %s (%s). "
                   "Set delegation.subagent_auto_approve: true to allow.",
                   command, description)
    return "deny"

def _subagent_auto_approve(command: str, description: str, **kwargs) -> str:
    """Auto-approve dangerous commands in subagent threads (opt-in YOLO)."""
    logger.warning("Subagent auto-approved dangerous command: %s (%s)",
                   command, description)
    return "once"

def _get_subagent_approval_callback():
    cfg = _load_config()
    val = cfg.get("subagent_auto_approve", False)
    if is_truthy_value(val):
        return _subagent_auto_approve
    return _subagent_auto_deny
```

ThreadPoolExecutor 用 `initializer=_set_subagent_approval_cb, initargs=(cb,)` 安装回调到 worker thread；安全默认是拒绝。

### 8.3.3 深度限制

`tools/delegate_tool.py:132-137`：

```python
_DEFAULT_MAX_CONCURRENT_CHILDREN = 3
MAX_DEPTH = 1  # flat by default: parent (0) -> child (1); grandchild rejected unless max_spawn_depth raised.
# Configurable depth cap consulted by _get_max_spawn_depth; MAX_DEPTH
# stays as the default fallback and is still the symbol tests import.
_MIN_SPAWN_DEPTH = 1
_MAX_SPAWN_DEPTH_CAP = 3
```

- **默认平坦**：parent (depth=0) 可以 spawn child (depth=1)，child 想 spawn grandchild 会被拒。
- **可调上限 3**：通过 `delegation.max_spawn_depth` 配置，最高 3 层；超过 3 在 `_get_max_spawn_depth()` 里 clamp 回 3。
- **默认并发 3**：单次 `batch=True, count=10` 也只会同时跑 3 个。

### 8.3.4 子 toolset 自动推导

`tools/delegate_tool.py:122-130`：

```python
_EXCLUDED_TOOLSET_NAMES = frozenset({"debugging", "safe", "delegation", "moa", "rl"})
_SUBAGENT_TOOLSETS = sorted(
    name
    for name, defn in TOOLSETS.items()
    if name not in _EXCLUDED_TOOLSET_NAMES
    and not name.startswith("hermes-")           # 排除 hermes-* 组合 toolset
    and not all(t in DELEGATE_BLOCKED_TOOLS for t in defn.get("tools", []))
)
_TOOLSET_LIST_STR = ", ".join(f"'{n}'" for n in _SUBAGENT_TOOLSETS)
```

策略：
- 排除 `delegation` toolset（递归通过 `role='orchestrator'` 显式开，不在 hint 字符串里暗示）；
- 排除 `hermes-*` 这类平台 / scenario 组合 toolset；
- 排除"所有工具都在黑名单"的 toolset（防止 LLM 选了一个空 toolset）。

`_TOOLSET_LIST_STR` 进 child agent 的系统提示，告诉它"你可以请求这些 toolset"。

### 8.3.5 全局暂停与 subagent 注册表

`tools/delegate_tool.py:149-155`：

```python
_spawn_pause_lock = threading.Lock()
_spawn_paused: bool = False

_active_subagents_lock = threading.Lock()
_active_subagents: Dict[str, Dict[str, Any]] = {}
```

`_spawn_paused` 是全局开关 —— TUI 或 gateway RPC `delegation.pause` 拍一下，新的 `delegate_task` 调用就会被阻塞（不影响进行中的子 agent）。`_active_subagents` 记录每个活着的子 agent（subagent_id → record），供 `delegation.status` / `subagent.interrupt` RPC 查询和中断。

### 8.3.6 token 预算不在子 agent 间分配

每个子 agent 用独立 `IterationBudget(max_total=delegation.max_iterations)`（默认 50），父用 90。也就是说 **父 90 + 3 子 × 50 = 240 轮总预算**；没有"父全局预算分配给子"的机制。这是有意取舍：

- 简单（一目了然）；
- 但理论上失控的并行 delegate 能集体烧很多 token。

`delegation.max_spawn_depth = 3` + `max_concurrent_children = 3` 这种配置下，最坏情况是 `3^3 * 50 = 1350` 轮。

## 8.4 运行时行为

```
父 Agent 主循环
  │
  └─ LLM 输出 tool_call: delegate_task({goal, context, toolset, batch, count, ...})
        │
        ├─ handle_delegate_task(args, task_id, ...)
        │     ├─ 检查全局 _spawn_paused → 阻塞
        │     ├─ 检查深度 ≤ _get_max_spawn_depth()
        │     ├─ 决定 child count（batch=False → 1, batch=True → count）
        │     │
        │     └─ if count == 1:
        │           build child + _run_single_child(child, timeout)
        │
        │     else:
        │           with ThreadPoolExecutor(max_workers=max_concurrent_children,
        │                                   initializer=_set_subagent_approval_cb,
        │                                   initargs=(_get_subagent_approval_callback(),)):
        │               futures = [executor.submit(_run_single_child, build_fn(i), timeout)
        │                          for i in range(count)]
        │               results = [as_completed(...) ...]
        │
        ├─ _run_single_child(child):
        │     ├─ _register_subagent(record)
        │     │
        │     ├─ child.run_iteration(timeout)  # 子的 main loop
        │     │     ├─ 独立 IterationBudget(50)
        │     │     ├─ 独立 message history（不继承父）
        │     │     ├─ 独立 task_id → 独立 terminal session
        │     │     ├─ 独立 toolset（受 _EXCLUDED 影响）
        │     │     └─ ephemeral_system_prompt（含 goal + context）
        │     │
        │     ├─ _summarize_child_result(child.messages, max_length=500)
        │     │
        │     └─ _unregister_subagent(record)
        │
        └─ _aggregate_results(results, count) → JSON 摘要回父 LLM
              ├─ count == 1 → 直接返回
              └─ count > 1 → 拼 {status, subagent_count, results: [...], summary: ...}
```

## 8.5 局限与边界

- **结果摘要有损**：父只看到 `summary` 字段（默认 500 字符）+ `final_message`；子的工具调用细节、中间推理对父不可见。
- **无全局 token 预算**：父子各自一份，集体可能超支；监控只能事后查 hermes logs。
- **共享资源无锁**：多个并发子 agent 写同一文件不会自动加锁；race condition 靠 skill 设计避免。
- **子模型固定**：子 agent 继承父的 model；不会"长任务用 Sonnet，总结用 Haiku"自动选。
- **审批硬性二元**：默认 auto-deny，YOLO mode auto-approve；没有"per-command 白名单 / 黑名单"中间层。
- **delegate 调用本身阻塞父**：父在等子完成期间，**父 LLM 不工作**；不是 fire-and-forget。

---

# 维度 9 — Cron 调度（内置）

## 9.1 设计立场

Hermes 的 cron 系统**完全自带、不依赖外部 daemon**：

- 配置存 `~/.hermes/cron/jobs.json`；
- 调度由 gateway 每 60 秒 tick 一次，到点的 job 在 ThreadPoolExecutor 里并行跑（默认 3 并发）；
- 每次 job 跑一个**独立的、隔离的 `AIAgent`** 实例（独立 session、独立 task_id、`skip_memory=True`、强制禁用 `cronjob/messaging/clarify` 工具集）；
- **Prompt injection 防御是双层的**：创建/更新 cron job 时严扫；运行时把 job prompt + skill 内容拼好后再扫一次。
- 输出按 `~/.hermes/cron/output/{job_id}/{timestamp}.md` 归档；可选 delivery 到 messaging 平台。

## 9.2 关键代码路径

| 角色 | 文件 | 关键符号 |
|------|------|---------|
| jobs.json 路径 | `cron/jobs.py:37-46` | `HERMES_DIR / CRON_DIR / JOBS_FILE / OUTPUT_DIR` |
| in-process lock | `cron/jobs.py:41-44` | `_jobs_file_lock = threading.Lock()` |
| 路径逃逸防护 | `cron/jobs.py:55-68` | `_job_output_dir(job_id)`：禁 `../` / `/` / 绝对路径 |
| Schedule 解析 | `cron/jobs.py:209` | `parse_schedule(schedule)` |
| 创建 job | `cron/jobs.py:531+` | `create_job(prompt, schedule, ...)` |
| 持久化 | `cron/jobs.py:455+` | `save_jobs(jobs)` |
| 标记执行 | `cron/jobs.py:891+` | `mark_job_run(job_id, success, error, ...)` |
| 推下次时间 | `cron/jobs.py:964+` | `advance_next_run(job_id)` |
| 到期查询 | `cron/jobs.py:993+` | `get_due_jobs()` |
| 输出归档 | `cron/jobs.py:1095+` | `save_job_output(job_id, output)` |
| Tick 入口 | `cron/scheduler.py:1857+` | `tick(verbose=True, adapters=None, loop=None)` |
| 单 job 运行 | `cron/scheduler.py:1204+` | `run_job(job)` |
| 拼 job prompt | `cron/scheduler.py:1004+` | `_build_job_prompt(job, prerun_script)` |
| Prompt 注入异常 | `cron/scheduler.py:47` | `class CronPromptInjectionBlocked(Exception)` |
| 严扫规则 | `tools/cronjob_tools.py:69` | `_CRON_THREAT_PATTERNS` |
| Skill 拼装后宽扫 | `tools/cronjob_tools.py:87` | `_CRON_SKILL_ASSEMBLED_PATTERNS` |
| 隐形 Unicode 表 | `tools/cronjob_tools.py:108` | `_CRON_INVISIBLE_CHARS` |
| 严扫入口 | `tools/cronjob_tools.py:186+` | `_scan_cron_prompt(prompt)` |

## 9.3 实现细节

### 9.3.1 jobs.json 结构

每个 job 一个 dict，存到 `JOBS_FILE = ~/.hermes/cron/jobs.json` 数组里。`cron/jobs.py:52-53` 定义了 immutable 字段：

```python
# Fields on a cron job that must never change after creation. ``id`` is used
# as a filesystem path component under ``OUTPUT_DIR``; allowing it to be
# updated lets an unsafe value (``../escape``, absolute path, nested) leak
# into output writes/deletes.
_IMMUTABLE_JOB_FIELDS = frozenset({"id"})
```

每个 job 字段示意（按源码各 helper 推断）：
- `id` (str, immutable) — 单段路径安全名
- `name`、`prompt`、`schedule`
- `skill` / `skills`（legacy 单字段 + 标准多字段）— 由 `_normalize_skill_list` 统一
- `enabled` (bool)、`state`（"scheduled"/"paused"/"completed"/"failed"）
- `enabled_toolsets` (list)、`model`、`provider`
- `timeout` (秒)、`profile`
- `next_run` (Unix ts)、`last_run`、`last_output`
- `created_at`、`platform`、`delivery`（可选）

### 9.3.2 路径逃逸防御

`cron/jobs.py:55-68` 强制 `job_id` 只能是单个安全路径段：

```python
def _job_output_dir(job_id: str) -> Path:
    """Resolve a job's output directory, rejecting any path-escape attempt.

    Job IDs are filesystem path components under ``OUTPUT_DIR``. A legacy or
    crafted ID containing ``..``, absolute paths, or nested separators would
    allow output writes/deletes to escape the cron output sandbox. Reject
    anything that isn't a single safe path component.
    """
    text = str(job_id or "").strip()
    if not text or text in {".", ".."} or "/" in text or "\\" in text:
        raise ValueError(f"Invalid cron job id for output path: {job_id!r}")
    if Path(text).is_absolute() or Path(text).drive:
        raise ValueError(f"Invalid cron job id for output path: {job_id!r}")
    return OUTPUT_DIR / text
```

这是把"用户能编辑的字段"和"会变成文件系统路径的字段"之间显式做了 trust boundary 检查。

### 9.3.3 文件锁

`cron/jobs.py:41-44`：

```python
# In-process lock protecting load_jobs→modify→save_jobs cycles.
# Required when tick() runs jobs in parallel threads — without this,
# concurrent mark_job_run / advance_next_run calls can clobber each other.
_jobs_file_lock = threading.Lock()
```

进程内锁；多个 hermes 进程同时操作同一 `$HERMES_HOME` 仍可能竞争（没有 fcntl 文件锁）。

### 9.3.4 Schedule 解析（依赖 `croniter`）

`cron/jobs.py:27-31`：

```python
try:
    from croniter import croniter
    HAS_CRONITER = True
except ImportError:
    HAS_CRONITER = False
```

`parse_schedule(schedule)` @ `:209` 支持：
- 5 字段标准 cron（`0 9 * * 1-5`）
- `@hourly` / `@daily` / `@weekly` / `@monthly`
- 自然语言（"every 30m" / "1d at 9:00 AM"）— 转成 cron

`croniter` 缺失时 `parse_schedule` 走 fallback（拒非 `@xxx` 之外的格式）。

### 9.3.5 Prompt 注入防御（严扫）

`tools/cronjob_tools.py:69-86` 创建/更新时的严格规则：

```python
_CRON_THREAT_PATTERNS = [
    (r'ignore\s+(?:previous|all|prior)\s+instructions', "prompt_injection"),
    (r'do\s+not\s+tell\s+the\s+user', "deception_hide"),
    (r'cat\s+[^\n]*(\.env|credentials|\.netrc|\.pgpass)', "read_secrets"),
    (r'rm\s+-rf\s+/', "destructive_root_rm"),
    (r'/etc/sudoers|visudo', "sudoers_mod"),
    (r'curl.*(-H|--header).*Authorization.*\$\w*(KEY|TOKEN|SECRET)', "exfil_auth_header"),
    ...
]
```

`_scan_cron_prompt(prompt)` @ `:186+` 把每条规则在 prompt 上 grep；任一命中返回威胁 ID 字符串，调用方决定如何阻止。

### 9.3.6 Prompt 注入防御（运行时宽扫）

`tools/cronjob_tools.py:87-106` 运行时（拼完 skill 内容）的宽松版：

```python
_CRON_SKILL_ASSEMBLED_PATTERNS = [
    (r'ignore\s+(?:previous|all|prior)\s+instructions', "prompt_injection"),
    (r'disregard\s+(your|all|any)\s+(instructions|rules)', "disregard_rules"),
    ...
]
```

为什么宽松？因为合法 skill 内容可能描述"如何 `rm -rf` 某个临时目录"或者"用 curl 带 Authorization header 调 API"，严扫会误杀；只挡明显的 jailbreak 指令。

### 9.3.7 隐形 Unicode 防御

`tools/cronjob_tools.py:108+`：

```python
_CRON_INVISIBLE_CHARS = {
    '​', '‌', '‍', '⁠', '﻿',  # ZWSP/ZWNJ/ZWJ/WJ/BOM
    '‪', '‫', '‬', '‭', '‮',  # 双向覆盖 LRO/RLO/...
    ...
}
```

`:180` 之后会扫 prompt 里的每个字符；命中即拒绝。**但 emoji 序列里的 `‍`（ZWJ）是允许的**（如 `👨‍👩‍👧`），是有特殊白名单处理（具体逻辑在 scan 函数内）。

### 9.3.8 Job 执行的 6 步流水

`cron/scheduler.py:1204+` `run_job(job)` 流程（推断结构 + `_build_job_prompt` 和 `_scan_cron_skill_assembled` 两个被 import 的细节）：

1. **拼 prompt**：`_build_job_prompt(job, prerun_script)` → 包含 job.prompt + 注入的 skill 内容 + prerun script。
2. **运行时扫一次**：`_scan_cron_skill_assembled(prompt)` 命中 → raise `CronPromptInjectionBlocked`。
3. **构造独立 agent**：`AIAgent(model=..., provider=..., skip_memory=True, platform="cron", enabled_toolsets=resolve_cron_enabled(...), disabled_toolsets=["cronjob", "messaging", "clarify", ...], max_iterations=100)`。
4. **跑 main loop**：`cron_agent.run(timeout=job["timeout"])`。
5. **写输出**：`save_job_output(job_id, output)`。
6. **mark + advance**：`mark_job_run(...)` 加 `advance_next_run(...)`。

`run_job` 返回 `(success: bool, output: str, error_summary: str, error_type: Optional[str])`。

### 9.3.9 Tick 入口

`cron/scheduler.py:1857+` `tick(verbose=True, adapters=None, loop=None)`：

- 获取文件锁；
- `get_due_jobs()` → 过滤 `next_run <= now` 的；
- ThreadPoolExecutor 并行（默认 3）；
- 每个 future 包 `run_job(job)`，超时按 `job["timeout"]`；
- `mark_job_run + advance_next_run + save_job_output` 序列化写回；
- 可选触发 `_deliver_job_output` 到 messaging 平台（如 Slack #channel）。

## 9.4 运行时行为

```
Gateway tick (每 60 秒一次)
  │
  └─ cron.scheduler.tick(adapters=gateway.adapters)
        ├─ 获取 _jobs_file_lock
        ├─ get_due_jobs() → [job1, job2, ...] (next_run <= now)
        │
        └─ ThreadPoolExecutor(max_workers=3):
              for job in due:
                  future = executor.submit(run_job, job)
                  ↓
                  run_job(job):
                    1. _build_job_prompt(job)              # 拼 skill 内容
                    2. _scan_cron_skill_assembled(prompt)  # 运行时宽扫
                    3. AIAgent(skip_memory=True, ...,
                               disabled_toolsets={cronjob, messaging, clarify})
                    4. cron_agent.run(timeout=job["timeout"])
                    5. save_job_output(job["id"], output)
                    6. mark_job_run + advance_next_run
                    7. (可选) _deliver_job_output → telegram/slack/...
                  ↓
                  return (success, output, err_summary, err_type)

create_job(prompt, schedule, ...)
  │
  ├─ _scan_cron_prompt(prompt)  → 严扫（含隐形 Unicode）
  ├─ parse_schedule(schedule) → croniter
  ├─ 生成安全 job_id（UUID hex）
  └─ save_jobs([..., new_job])
```

## 9.5 局限与边界

- **60 秒 tick 粒度**：精度上限 1 分钟；秒级 cron 不支持。
- **错过不补**：gateway 宕机期间到期的 job 不会 catch-up，只 advance `next_run` 跳过。
- **进程内锁，不跨进程**：多 hermes 进程同一 `$HERMES_HOME` 有竞争窗口。
- **无 job 依赖图**：不能"job B 在 job A 成功后跑"；要靠 job A 自己显式触发。
- **无全局 token 预算**：跟维度 8 一样，单 job 100 轮 cap，但并发 N 个就 N × 100。
- **`croniter` 缺失时降级**：导入失败只能用 `@hourly` 这类宏。
- **delivery 是 best-effort**：发送失败不会重排队（一次性投递）。
- **新建调度后第一次运行的最早时间是 `next_run`**：没有"现在就跑一次"的选项，要 `hermes cron run <id>` 显式触发。

---

# 维度 10 — 消息平台

## 10.1 设计立场

`gateway/platforms/` 实现了一套**统一抽象的 messenger 适配层**：

- **一个 `BasePlatformAdapter` 抽象基类**：所有平台子类只需实现 connect / send / handle_message 等少量抽象方法；
- **平台**用 `Platform` enum 枚举（22 个内置成员）+ `plugins/platforms/<name>/` 目录可动态加挂（如 IRC、LINE、Teams、Google Chat、ntfy）；
- 平台特定的差异（字符计数、流式 draft、媒体类型、UTF-16 长度）通过**可覆盖的 property / method** 暴露；
- **会话 (session)** 抽象由 `SessionSource` 数据类承担，跨平台统一 key 化（platform + chat_id + thread_id）；
- **流式输出**两种模式：edit_message 反复编辑（大部分平台）+ Telegram Bot API 9.5 `sendMessageDraft` 真·流式预览。

## 10.2 关键代码路径

| 角色 | 文件 | 关键符号 |
|------|------|---------|
| Platform enum | `gateway/config.py:100-129` | 22 个内置 + `_missing_()` 动态加载 |
| 动态发现 | `gateway/config.py:131-180` | `_missing_()` / `_scan_bundled_plugin_platforms()` |
| 适配器基类 | `gateway/platforms/base.py:1504-1576` | `class BasePlatformAdapter(ABC)` |
| `message_len_fn` 属性 | `gateway/platforms/base.py:1577-1584` | UTF-16 vs len |
| UTF-16 计数函数 | `gateway/platforms/base.py:125-137` | `utf16_len(s)` |
| Draft streaming 能力声明 | `gateway/platforms/base.py:1586-1603` | `supports_draft_streaming()` |
| Draft streaming 实现 | `gateway/platforms/base.py:1605-1664` | `send_draft(...)` |
| 抽象 connect/disconnect | `gateway/platforms/base.py:1789-1800` | `async connect()` / `disconnect()` |
| 抽象 send | `gateway/platforms/base.py:1803-1857` | `async send(...)` |
| edit_message | `gateway/platforms/base.py:1859-1965` | 支持 finalize 标记流完成 |
| 媒体发送族 | `gateway/platforms/base.py:2096-2325` | `send_multiple_images / send_image / send_animation / send_voice / send_video / send_document` |
| 重试 | `gateway/platforms/base.py:2775+` | `_send_with_retry()` |
| SessionSource | `gateway/session.py:70-157` | dataclass：platform/chat_id/chat_name/chat_type/user_id/user_name/thread_id/user_id_alt/chat_id_alt/message_id |
| Telegram | `gateway/platforms/telegram.py` | `TelegramAdapter`（含 polling / webhook 双模式） |
| 飞书 | `gateway/platforms/feishu.py` + `feishu_comment.py` + `feishu_comment_rules.py` | 三文件分工：消息 / 评论 / 评论规则 |
| 企业微信 | `gateway/platforms/wecom.py` + `wecom_callback.py` + `wecom_crypto.py` | 加密回调单独抽 |
| Signal | `gateway/platforms/signal.py` + `signal_rate_limit.py` | 限流单独抽 |
| 平台插件 | `plugins/platforms/<name>/` | discord, google_chat, irc, line, mattermost, ntfy, simplex, teams |

## 10.3 实现细节

### 10.3.1 `Platform` enum 与动态扩展

`gateway/config.py:100-130` 是 22 个枚举成员：

```python
class Platform(Enum):
    LOCAL = "local"
    TELEGRAM = "telegram"
    DISCORD = "discord"
    WHATSAPP = "whatsapp"
    SLACK = "slack"
    SIGNAL = "signal"
    MATTERMOST = "mattermost"
    MATRIX = "matrix"
    HOMEASSISTANT = "homeassistant"
    EMAIL = "email"
    SMS = "sms"
    DINGTALK = "dingtalk"
    API_SERVER = "api_server"
    WEBHOOK = "webhook"
    MSGRAPH_WEBHOOK = "msgraph_webhook"
    FEISHU = "feishu"
    WECOM = "wecom"
    WECOM_CALLBACK = "wecom_callback"
    WEIXIN = "weixin"
    BLUEBUBBLES = "bluebubbles"
    QQBOT = "qqbot"
    YUANBAO = "yuanbao"
```

`_missing_(cls, value)` (`:131-173`) 是 enum 的扩展点：

```python
@classmethod
def _missing_(cls, value):
    """Accept unknown platform names only for known plugin adapters.

    Creates a pseudo-member cached in ``_value2member_map_`` so that
    ``Platform("irc") is Platform("irc")`` holds True (identity-stable).
    Arbitrary strings are rejected to prevent enum pollution.
    """
    ...
    # 1. 已缓存？返回
    if value in cls._value2member_map_:
        return cls._value2member_map_[value]

    # 2. 是 bundled plugin platform？创建 pseudo-member，缓存
    if value in _Platform__bundled_plugin_names:
        pseudo = object.__new__(cls)
        pseudo._value_ = value
        pseudo._name_ = value.upper().replace("-", "_").replace(" ", "_")
        cls._value2member_map_[value] = pseudo
        cls._member_map_[pseudo._name_] = pseudo
        return pseudo

    # 3. 是 runtime-registered plugin？同上
    try:
        from gateway.platform_registry import platform_registry
        if platform_registry.is_registered(value):
            ...
    except Exception:
        pass

    return None
```

效果：`Platform("irc")` 第一次调用 → 扫 `plugins/platforms/` 发现有 `irc/` → 创建 pseudo-member 且 identity 稳定。**但任意字符串会被拒**（防止 `Platform("`<inject>`")` 污染 enum）。

`plugins/platforms/` 实际清单：`discord, google_chat, irc, line, mattermost, ntfy, simplex, teams` —— 8 个 → 总平台数 22 + 8 = **30 个**（其中 discord/mattermost 同时在 enum 和 plugins 出现，后者是 plugin 覆盖路径）。

### 10.3.2 `BasePlatformAdapter` 的核心字段

`gateway/platforms/base.py:1504-1576`：

```python
class BasePlatformAdapter(ABC):
    def __init__(self, config: PlatformConfig, platform: Platform):
        self.config = config
        self.platform = platform
        self._message_handler: Optional[MessageHandler] = None
        self._running = False

        # 致命错误（如认证失败）
        self._fatal_error_code: Optional[str] = None
        self._fatal_error_message: Optional[str] = None
        self._fatal_error_retryable = True
        self._fatal_error_handler: Optional[Callable[...]] = None

        # 中断支持的两本账：
        # - _active_sessions:    session → asyncio.Event（中断信号）
        # - _session_tasks:      session → 当前处理它的 Task（用于取消）
        # 没有 owner-task 映射的话，old task 的 finally 会清掉 new task 的 guard
        self._active_sessions: Dict[str, asyncio.Event] = {}
        self._pending_messages: Dict[str, MessageEvent] = {}
        self._session_tasks: Dict[str, asyncio.Task] = {}

        # 短促文本去抖：busy 模式 vs queue 模式 vs debounce 模式
        self._busy_text_mode: str = ...     # env: HERMES_GATEWAY_BUSY_TEXT_MODE
        self._busy_text_debounce_seconds: float = 0.35
        self._busy_text_hard_cap_seconds: float = 1.0
        self._text_debounce: dict[str, TextDebounceState] = {}

        # 后台任务集合，shutdown 时统一 cancel
        self._background_tasks: set[asyncio.Task] = set()

        # 主响应送达后的一次性回调（用于 deferred delivery）
        self._post_delivery_callbacks: Dict[str, Any] = {}

        # 预期被取消的 task 集合（不报错）
        self._expected_cancelled_tasks: set[asyncio.Task] = set()

        # 正在 busy 时的回调（决定排队 / 丢弃 / 立即处理）
        self._busy_session_handler: Optional[Callable[[MessageEvent, str], Awaitable[bool]]] = None

        # 语音转文字后是否自动 TTS 回应（per-chat 两套 set）
        self._auto_tts_default: bool = False
        self._auto_tts_enabled_chats: set = set()
        self._auto_tts_disabled_chats: set = set()

        # typing indicator 暂停（如在审批等待期）
        self._typing_paused: set = set()
```

这套字段告诉我们 Hermes 的**并发模型**：
- 每个 (platform, chat) 是一个 session；
- 同一 session 同一时刻只有一个 task 在处理；
- 新消息来时按 `_busy_session_handler` 决定：默认排队 / 可配置丢弃；
- session 中断（用户 `/stop`）通过 `_session_tasks[session].cancel()` 精确取消那个 task。

### 10.3.3 字符计数差异

`gateway/platforms/base.py:1577-1584`：

```python
@property
def message_len_fn(self) -> Callable[[str], int]:
    """Return the length function for measuring message size on this platform.

    Override in adapters whose platform counts characters differently from
    Python ``len`` (e.g. Telegram counts UTF-16 code units).
    """
    return len
```

Telegram 把消息长度按 **UTF-16 code units** 算（4096 上限），所以含 emoji（代理对 = 2 code units）的消息计数跟 Python `len()` 差异显著。Hermes 单独提供 `utf16_len(s)` 函数（`:125-137`），Telegram adapter override `message_len_fn` 返回它。

### 10.3.4 流式输出两条路径

**Path A — `send_draft`（Telegram Bot API 9.5+）** `gateway/platforms/base.py:1586-1664`：

```python
def supports_draft_streaming(self, chat_type=None, metadata=None) -> bool:
    """Telegram Bot API 9.5 introduced ``sendMessageDraft``, which renders an
    animated streaming preview as the bot calls it repeatedly with the
    same ``draft_id`` and growing text.  Adapters that implement
    ``send_draft`` should return True here for the chat types where the
    platform supports it (Telegram restricts drafts to private DMs).

    Default implementation returns False.  Stream consumers fall back to
    the edit-based path (``send`` + ``edit_message``) when this returns
    False or when ``send_draft`` raises.
    """
    return False

async def send_draft(self, chat_id, draft_id, content, metadata=None) -> SendResult:
    """Send or update an animated streaming-draft preview.

    Reuse the same ``draft_id`` (any non-zero int) across consecutive
    calls within a single response so the platform animates the preview
    rather than re-creating it.  Different responses must use different
    ``draft_id`` values within the same chat to avoid animating over a
    prior bubble.
    """
```

**Path B — `send` + `edit_message`**（其他平台）：每个 token 累积一段时间后调 `edit_message`，最后一次带 `finalize=True` 标记结束。Slack / Discord / 飞书 / 钉钉 都走这条。

### 10.3.5 媒体发送：6 个方法 + 平台特化

`gateway/platforms/base.py:2096-2325`：

| 方法 | 行 | 用途 |
|------|---|------|
| `send_multiple_images` | 2096-2152 | 一次发多图（如 album） |
| `send_image` | 2153-2171 | 单图 |
| `send_animation` | 2172-2242 | GIF / 短视频动画 |
| `send_voice` | 2243-2284 | 语音消息（opus / ogg） |
| `send_video` | 2285-2304 | 视频 |
| `send_document` | 2305-2325 | 任意文件附件 |

平台细节：
- `_TELEGRAM_AUDIO_ATTACHMENT_EXTS = {'.mp3', '.m4a'}`、`_TELEGRAM_VOICE_EXTS = {'.ogg', '.opus'}`（`:34-35`） —— Telegram 把 mp3 当 audio attachment、ogg/opus 当 voice message，UI 体验不同；
- `should_send_media_as_audio()` (`:102-122`) 按平台 + 扩展名决策；
- 富文本：基类**只支持纯文本**；卡片 / 按钮 / Slack Block Kit / InlineKeyboard 要在子类内部实现（不是统一抽象）。

### 10.3.6 SessionSource：跨平台 session 唯一性

`gateway/session.py:70-157`（dataclass 关键字段）：

```python
@dataclass
class SessionSource:
    platform: Platform
    chat_id: str
    chat_name: Optional[str]
    chat_type: str              # "dm" / "group" / "channel" / "thread"
    user_id: Optional[str]
    user_name: Optional[str]
    thread_id: Optional[str]    # Forum topics, Discord threads
    user_id_alt: Optional[str]  # Signal UUID, Feishu union_id 这类稳定 ID
    chat_id_alt: Optional[str]  # Signal group internal ID
    message_id: Optional[str]
```

`user_id_alt` 是关键设计：很多 IM 有"显示 ID"和"稳定 ID"两套（Signal 的 phone vs UUID；飞书的 open_id vs union_id），稳定 ID 才适合作为 memory 主键。

### 10.3.7 速率限制现状

- Signal：`signal_rate_limit.py` 实现 per-recipient 节流；
- Telegram：靠 `python-telegram-bot` SDK 自带的 rate limiter；
- 其他平台（飞书 / 企微 / Slack）：靠各自 SDK 内置或没有；
- **没有一个跨平台统一的 Hermes-side rate limiter**。

`_send_with_retry()`（`:2775`）是发送失败时的指数退避包装，所有平台共用。

## 10.4 运行时行为

```
GatewayRunner 启动
  │
  ├─ 读 ~/.hermes/config.yaml 的 platforms: 段
  │     for platform in enabled_platforms:
  │         adapter = AdapterClass(config, Platform(platform_name))
  │         await adapter.connect()
  │         adapter.set_message_handler(gateway.dispatch)
  │
  └─ 进 asyncio event loop

入站消息（platform-specific）
  │
  ├─ 平台 webhook / polling → 平台 SDK 解析 → MessageEvent
  │
  ├─ adapter.handle_message(event):
  │     ├─ session_key = build_session_key(event.source)
  │     ├─ session 已 busy? → _busy_session_handler 决策（queue / drop）
  │     ├─ 创建处理 Task → 注册到 _session_tasks
  │     └─ Task：调用 _message_handler(event) = gateway.dispatch

gateway.dispatch(event)
  │
  └─ pre_gateway_dispatch hook → 决定 skip / rewrite / allow
        │
        └─ AIAgent.run_conversation(event.text, session_id=...,
                                    stream_callback=adapter.send_or_draft, ...)
              │
              └─ 流式 callback:
                    if adapter.supports_draft_streaming(chat_type):
                        adapter.send_draft(chat_id, draft_id, content)
                    else:
                        adapter.send(chat_id, content); adapter.edit_message(...)
                    最后一次 edit_message(finalize=True)
```

## 10.5 局限与边界

- **没有统一 rate limiter**：跨平台共享上限的能力不存在；超限处理在每个 SDK 里。
- **富文本不统一**：卡片 / 按钮在基类没有抽象；要平台子类自己实现，跨平台复用度低。
- **多账号同一用户**：靠 `user_id_alt` 在 SessionSource 里手动维护，没有自动 cross-platform identity reconciliation。
- **`_busy_text_mode` 只有 queue / drop 两档**：不能 per-platform / per-chat 微调。
- **致命错误恢复需要外部 restart**：`_set_fatal_error` 抛出后，gateway 不自动重试连接（用户要 `hermes gateway restart`）。

---

# 维度 11 — MCP 支持（Client + Server）

## 11.1 设计立场

Hermes 是 **MCP 双向打通**：既是 Client（导入外部 MCP server 的工具），也是 Server（把自己的消息能力暴露给 Claude Code / Cursor / Codex）。

- **Client**：`tools/mcp_tool.py` 3593 行，专门跑一个**后台 asyncio loop** 维持所有 MCP server 的长连接；工具被注册进 `tools/registry.py`，对 LLM 与 native 工具一视同仁。
- **Server**：`mcp_serve.py` 897 行，用 FastMCP 暴露 10 个工具（OpenClaw 9-tool 标准 + 1 个 Hermes 特有的 `channels_list`）。
- **Transport**：stdio / HTTP (StreamableHTTP) / SSE 三选一，per-server 配置。
- **采样（sampling）**：MCP server 可反向请求 Hermes 跑一次 LLM 补全，作为"借用 host 的模型"机制。

## 11.2 关键代码路径

| 角色 | 文件 | 关键符号 |
|------|------|---------|
| Client 模块 docstring | `tools/mcp_tool.py:1-78` | 整体架构 + 配置示例 |
| MCP SDK lazy import | `tools/mcp_tool.py` 后段（`from mcp import ClientSession, StdioServerParameters`） | `_MCP_AVAILABLE` 标志 |
| Server 入口 | `mcp_serve.py:1-27` | 10 个工具列表 |
| FastMCP lazy import | `mcp_serve.py:46-55` | `_MCP_SERVER_AVAILABLE` |
| OAuth 流程 | `tools/mcp_oauth.py` | 不在本次摘录范围 |
| OAuth 凭证管理 | `tools/mcp_oauth_manager.py` | 同上 |
| MCP 配置规范 | `hermes_cli/mcp_config.py` | 读 `mcp_servers:` 配置 |

## 11.3 实现细节

### 11.3.1 配置示例（来自 `tools/mcp_tool.py:13-49`）

```yaml
mcp_servers:
  filesystem:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    env: {}
    timeout: 120         # per-tool-call timeout in seconds (default: 120)
    connect_timeout: 60  # initial connection timeout (default: 60)
  github:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "ghp_..."
    supports_parallel_tool_calls: true  # tools from this server may run concurrently
  remote_api:
    url: "https://my-mcp-server.example.com/mcp"
    headers:
      Authorization: "Bearer sk-..."
    timeout: 180
  searxng:
    url: "http://localhost:8000/sse"
    transport: sse       # use SSE transport instead of Streamable HTTP
    timeout: 180
    connect_timeout: 10
    command: "npx"
    args: ["-y", "analysis-server"]
    sampling:                    # server-initiated LLM requests
      enabled: true              # default: true
      model: "gemini-3-flash"    # override model (optional)
      max_tokens_cap: 4096       # max tokens per request
      timeout: 30                # LLM call timeout (seconds)
      max_rpm: 10                # max requests per minute
      allowed_models: []         # model whitelist (empty = all)
      max_tool_rounds: 5         # tool loop limit (0 = disable)
      log_level: "info"          # audit verbosity
```

### 11.3.2 Client 架构（`tools/mcp_tool.py:63-77` docstring 原文）

> A dedicated background event loop (_mcp_loop) runs in a daemon thread.
> Each MCP server runs as a long-lived asyncio Task on this loop, keeping
> its transport context alive. Tool call coroutines are scheduled onto the
> loop via ``run_coroutine_threadsafe()``.
>
> On shutdown, each server Task is signalled to exit its ``async with``
> block, ensuring the anyio cancel-scope cleanup happens in the *same*
> Task that opened the connection (required by anyio).

关键设计：
- **专用 daemon thread + 专用 event loop**：不污染主 thread 的 asyncio context；
- **每个 server = 长生命周期 Task**：transport 上下文不重连；
- **`run_coroutine_threadsafe()` 跨线程调用**：主线程拿 future，子线程跑 IO；
- **shutdown 在原 Task 关 connection**：anyio 强制要求 cancel scope 在打开它的 task 内关。

### 11.3.3 安全 / 隔离要点（`tools/mcp_tool.py:50-62`）

- **Environment variable filtering for stdio subprocesses (security)**：env 默认只透传安全白名单；
- **Credential stripping in error messages**：传回 LLM 的错误信息会做 secret 过滤（防止 LLM 把 token 写进对话里）；
- **指数退避自动重连**：up to 5 retries；
- **per-server timeout**：connect_timeout / tool call timeout 各自配；
- **subprocess stderr 重定向到 `~/.hermes/logs/mcp-stderr.log`**：避免子进程的报错把 TUI 弄花。

### 11.3.4 Parallel tool calls 选项

`supports_parallel_tool_calls: true` 是 per-server 配置；为 True 时，从该 server 来的多个 tool_call 在工具执行器里走并发路径；为 False（默认）时严格串行。是 server-side opt-in，因为有些 MCP server 的状态不耐并发（如 filesystem cwd）。

### 11.3.5 Server 端 10 个工具

`mcp_serve.py:1-27` docstring 原文：

> Matches OpenClaw's 9-tool MCP channel bridge surface:
>   conversations_list, conversation_get, messages_read, attachments_fetch,
>   events_poll, events_wait, messages_send, permissions_list_open,
>   permissions_respond
>
> Plus: channels_list (Hermes-specific extra)

— 实现一个 stdio MCP server；Claude Code 等客户端通过 `{ "command": "hermes", "args": ["mcp", "serve"] }` 接入，可以：
- 列出 hermes 在管的所有对话（`conversations_list`）；
- 读特定对话的完整历史（`conversation_get`）；
- 读 message 范围（`messages_read`）；
- 拿附件（`attachments_fetch`）；
- poll / wait 事件（`events_poll` / `events_wait`）；
- 跨平台发消息（`messages_send`）；
- 看 / 回应 待审批权限（`permissions_list_open` / `permissions_respond`）；
- 列连接的平台/通道（`channels_list`，Hermes 独有）。

### 11.3.6 采样（sampling）

MCP server 可以反向 `sampling/createMessage` 请求 host 帮它跑一次 LLM 补全。Hermes 客户端实现这个回流：

- `sampling.enabled: true` 是默认（per-server）；
- `model` 覆盖；
- `max_tokens_cap` 防止 server 让 host 烧太多；
- `max_rpm` 速率上限；
- `allowed_models` 白名单（限定能跑哪些 model）；
- `max_tool_rounds`：sampling 内部也可能再叫工具（嵌套），上限 5；
- `log_level` 审计粒度。

## 11.4 运行时行为

```
Agent init
  │
  ├─ tools.mcp_tool 加载（lazy import mcp SDK）
  ├─ 读 ~/.hermes/config.yaml mcp_servers
  │
  └─ for server_name, server_cfg in mcp_servers.items():
        ├─ 启动后台 event loop + daemon thread（如未启动）
        ├─ 创建长生命周期 Task：
        │     async with ClientSession(transport(server_cfg)) as session:
        │         await session.initialize()
        │         tools = await session.list_tools()
        │         for tool in tools:
        │             registry.register(
        │                 name=f"mcp-{server_name}.{tool.name}",
        │                 toolset=f"mcp-{server_name}",
        │                 schema=...,
        │                 handler=lambda args: call_mcp_tool(server_name, tool.name, args),
        │             )
        │         await asyncio.Event().wait()  # 保活
        │
        └─ 工具调用：
              registry.dispatch("mcp-github.create_issue", args)
                  ↓
              run_coroutine_threadsafe(
                  session.call_tool("create_issue", args),
                  _mcp_loop
              ).result(timeout=server_cfg.timeout)

MCP Server（hermes mcp serve）
  │
  ├─ FastMCP() 实例
  ├─ @mcp.tool() 装饰每个工具
  ├─ stdio transport
  │
  └─ 客户端调用：
        messages_send(platform="telegram", chat_id="123", content="hi")
            ↓
        从 hermes runtime 找对应 adapter
            ↓
        adapter.send(chat_id, content)
```

## 11.5 局限与边界

- **工具发现是启动时一次性**：MCP server 加新工具不会被 hermes 注意到（docstring 暗示有 `notifications/tools/list_changed` 支持但未在主代码暴露给 LLM 端）。
- **error transparency 不强**：subprocess crash 信息只在 `mcp-stderr.log`，LLM 看不到。
- **采样的 model 没法 per-call 切**：server 配置写死 `model: gemini-3-flash`，server 不能动态请求换。
- **events_poll 是轮询**：不是 push；MCP server 的客户端要主动 poll，吞延迟。
- **OAuth 流程对用户不透明**：需要走 `tools/mcp_oauth.py` 的交互流程，gateway 上不好做（需要浏览器 redirect）。

---

# 维度 12 — 扩展机制（工具 / 技能 / MCP / 钩子）

## 12.1 设计立场

Hermes 划清了**四种 "为 agent 加能力" 的边界**：

| 类型 | 形态 | 由谁开发 | 加载时机 |
|------|------|---------|---------|
| **工具（tool）** | Python 函数 + `registry.register(...)` | 内部 / 插件 | 启动时（一次） |
| **技能（skill）** | `SKILL.md` + 支撑文件目录 | 用户、agent 自己（后台 review）、agentskills.io hub | 用户 `/<skill-name>` 或 `skill_view` 触发 |
| **MCP 工具** | 外部 stdio/HTTP server | 第三方 | 启动时连接，list_tools 一次 |
| **插件钩子（hook）** | `plugin.yaml` + `register(ctx)` 函数，挂 hook 点 | 用户 / 第三方 | 启动时按 `plugins.enabled` 加载 |

设计立场是**显式区分** —— 工具是原子操作、技能是多步工作流、MCP 是外部服务、hook 是行为切片，不允许跨界（如不能用 skill 拦截 LLM 输出）。

## 12.2 关键代码路径

| 角色 | 文件 | 关键符号 |
|------|------|---------|
| 工具 registry | `tools/registry.py:151-172` | `ToolRegistry` 单例 |
| ToolEntry | `tools/registry.py:77-106` | 12 个字段 |
| 注册函数（推断） | `tools/registry.py` | `register(name, toolset, schema, handler, check_fn, ...)` |
| 内置工具发现 | `tools/registry.py:57-74` | `discover_builtin_tools()` AST 扫描 |
| check_fn TTL 缓存 | `tools/registry.py:109-148` | `_CHECK_FN_TTL_SECONDS=30` |
| 技能 docstring | `tools/skills_tool.py:1-67` | 完整目录结构和 frontmatter 示例 |
| 技能预处理 | `agent/skill_preprocessing.py` | `substitute_template_vars` / `expand_inline_shell` / `preprocess_skill_content` |
| 插件目录发现 | `hermes_cli/plugins.py:55-65` | `get_bundled_plugins_dir()` + `HERMES_BUNDLED_PLUGINS` 环境覆盖 |
| 插件 hook 名单 | `hermes_cli/plugins.py:128-168` | `VALID_HOOKS` 集合（17 个 hook） |
| 插件 enabled / disabled | `hermes_cli/plugins.py:180-208` | `_get_enabled_plugins()` / `_get_disabled_plugins()` |
| 入口点 | `hermes_cli/plugins.py:170` | `ENTRY_POINTS_GROUP = "hermes_agent.plugins"`（pip 包） |

## 12.3 实现细节

### 12.3.1 `ToolEntry`：12 字段定义工具

`tools/registry.py:77-106`：

```python
class ToolEntry:
    """Metadata for a single registered tool."""

    __slots__ = (
        "name", "toolset", "schema", "handler", "check_fn",
        "requires_env", "is_async", "description", "emoji",
        "max_result_size_chars", "dynamic_schema_overrides",
    )

    def __init__(self, name, toolset, schema, handler, check_fn,
                 requires_env, is_async, description, emoji,
                 max_result_size_chars=None, dynamic_schema_overrides=None):
        self.name = name
        self.toolset = toolset
        self.schema = schema                   # JSON Schema
        self.handler = handler                 # sync or async callable
        self.check_fn = check_fn               # 可用性检测（Docker 在? Playwright 装了?）
        self.requires_env = requires_env       # 必需 env vars 列表
        self.is_async = is_async
        self.description = description
        self.emoji = emoji                     # TUI 显示
        self.max_result_size_chars = max_result_size_chars  # 结果裁剪
        # 在每次 get_definitions() 调用时跑一次，结果 shallow merge
        # 覆盖到 schema 上（用于参数依赖 runtime 配置的情况，如 delegate_task
        # 的 max_concurrent_children / max_spawn_depth）
        self.dynamic_schema_overrides = dynamic_schema_overrides
```

`dynamic_schema_overrides` 是一个不太常见但很有用的设计：参数的合法范围跟用户当前配置有关时，每次 `get_definitions()` 都重算 schema，确保 LLM 看到的工具签名跟实际允许的一致。

### 12.3.2 内置工具发现：AST 扫描

`tools/registry.py:29-74`：

```python
def _is_registry_register_call(node: ast.AST) -> bool:
    """Return True when *node* is a ``registry.register(...)`` call expression."""
    if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
        return False
    func = node.value.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "register"
        and isinstance(func.value, ast.Name)
        and func.value.id == "registry"
    )


def _module_registers_tools(module_path: Path) -> bool:
    """Return True when the module contains a top-level ``registry.register(...)`` call.

    Only inspects module-body statements so that helper modules which happen
    to call ``registry.register()`` inside a function are not picked up.
    """
    try:
        source = module_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(module_path))
    except (OSError, SyntaxError):
        return False
    return any(_is_registry_register_call(stmt) for stmt in tree.body)


def discover_builtin_tools(tools_dir=None) -> List[str]:
    """Import built-in self-registering tool modules and return their module names."""
    tools_path = Path(tools_dir) if tools_dir is not None else Path(__file__).resolve().parent
    module_names = [
        f"tools.{path.stem}"
        for path in sorted(tools_path.glob("*.py"))
        if path.name not in {"__init__.py", "registry.py", "mcp_tool.py"}
        and _module_registers_tools(path)
    ]
    imported: List[str] = []
    for mod_name in module_names:
        try:
            importlib.import_module(mod_name)
            imported.append(mod_name)
        except Exception as e:
            logger.warning("Could not import tool module %s: %s", mod_name, e)
    return imported
```

策略：
- **AST 扫描 module body**：找有 top-level `registry.register(...)` 的文件；
- **排除 mcp_tool.py / registry.py 自己**；
- **import 触发 module-level 注册**：register 是有副作用的，import 一次就注册完成；
- 工具加载失败只 warning，不让一个坏工具拖垮整个 agent。

### 12.3.3 check_fn TTL 缓存

`tools/registry.py:109-142`：

```python
# check_fn callables like tools/terminal_tool.check_terminal_requirements
# probe external state (Docker daemon, Modal SDK install, playwright binary
# availability). For a long-lived CLI or gateway process, calling them on
# every get_definitions() is pure waste — external state changes on human
# timescales. Cache results for ~30 s so env-var flips via ``hermes tools``
# or live credential file changes propagate within a turn or two without
# requiring any explicit invalidation.

_CHECK_FN_TTL_SECONDS = 30.0
_check_fn_cache: Dict[Callable, tuple[float, bool]] = {}
_check_fn_cache_lock = threading.Lock()


def _check_fn_cached(fn: Callable) -> bool:
    """Return bool(fn()), TTL-cached across calls. Swallows exceptions as False."""
    now = time.monotonic()
    with _check_fn_cache_lock:
        cached = _check_fn_cache.get(fn)
        if cached is not None:
            ts, value = cached
            if now - ts < _CHECK_FN_TTL_SECONDS:
                return value
    try:
        value = bool(fn())
    except Exception:
        value = False
    with _check_fn_cache_lock:
        _check_fn_cache[fn] = (now, value)
    return value


def invalidate_check_fn_cache() -> None:
    """Drop all cached ``check_fn`` results. Call after config changes that
    affect tool availability (e.g. ``hermes tools enable``)."""
    with _check_fn_cache_lock:
        _check_fn_cache.clear()
```

30 秒缓存平衡"配置变更要快生效"和"Docker daemon 探针不能每次都跑"。`hermes tools enable foo` 这类命令会显式调 `invalidate_check_fn_cache()` 立即生效。

### 12.3.4 技能预处理两特性

`agent/skill_preprocessing.py:13-20` 定义两个正则：

```python
# Matches ${HERMES_SKILL_DIR} / ${HERMES_SESSION_ID} tokens in SKILL.md.
_SKILL_TEMPLATE_RE = re.compile(r"\$\{(HERMES_SKILL_DIR|HERMES_SESSION_ID)\}")

# Matches inline shell snippets like:  !`date +%Y-%m-%d`
# Non-greedy, single-line only -- no newlines inside the backticks.
_INLINE_SHELL_RE = re.compile(r"!`([^`\n]+)`")

# Cap inline-shell output so a runaway command can't blow out the context.
_INLINE_SHELL_MAX_OUTPUT = 4000
```

**模板变量**：`${HERMES_SKILL_DIR}` / `${HERMES_SESSION_ID}` 替换；解不出来保留原文便于排错。

**Inline shell**：`!`date +%Y-%m-%d`` 这种 backtick 片段在 skill 加载时执行，stdout 替换原文。控制：
- `skills.inline_shell` 配置项控制开关（默认 **off**，因为是潜在的安全风险）；
- `skills.inline_shell_timeout` 控制单个 shell 超时（默认 10s）；
- 输出最多 4000 字符；
- CWD = skill 目录；
- 失败返回 `[inline-shell error: ...]` 标记，不抛异常。

`preprocess_skill_content()`（`:123-139`）把两者按配置组合执行。

### 12.3.5 17 个有效 hook

`hermes_cli/plugins.py:128-168`：

```python
VALID_HOOKS: Set[str] = {
    "pre_tool_call",
    "post_tool_call",
    "transform_terminal_output",
    "transform_tool_result",
    # First non-None string wins. Useful for vocabulary/personality transformation.
    "transform_llm_output",
    "pre_llm_call",
    "post_llm_call",
    "pre_api_request",
    "post_api_request",
    "on_session_start",
    "on_session_end",
    "on_session_finalize",
    "on_session_reset",
    "subagent_stop",
    # Gateway pre-dispatch hook ...
    # Kwargs: event: MessageEvent, gateway: GatewayRunner, session_store.
    "pre_gateway_dispatch",
    # Approval lifecycle hooks ...
    "pre_approval_request",
    "post_approval_response",
}
```

四组语义：
- **工具调用切面**：`pre_tool_call` / `post_tool_call` — 修改 args / 修改 result（最常用）；
- **输出/请求转换**：`transform_terminal_output` / `transform_tool_result` / `transform_llm_output` / `pre_llm_call` / `post_llm_call` / `pre_api_request` / `post_api_request` — 拦截或重写各种数据流；
- **会话生命周期**：`on_session_start` / `on_session_end` / `on_session_finalize` / `on_session_reset` / `subagent_stop`；
- **特殊事件**：`pre_gateway_dispatch`（消息进 dispatcher 前可改写或丢弃）/ `pre_approval_request` / `post_approval_response`（审批生命周期观察）。

**`transform_llm_output` 的语义**：多个 plugin 都注册时**第一个返回非空 string 的赢**（"first non-None wins"）—— 其他 hook 是顺序串联，这个是 short-circuit。

**审批 hook 是 observer-only**：返回值被忽略，不能否决审批；要 veto 必须用 `pre_tool_call`。

### 12.3.6 插件发现路径与启用列表

`hermes_cli/plugins.py:55-65` `get_bundled_plugins_dir()`：

```python
def get_bundled_plugins_dir() -> Path:
    """Locate the bundled ``plugins/`` directory.

    Honours ``HERMES_BUNDLED_PLUGINS`` (set by the Nix wrapper / packaged
    installs) so read-only store paths are consulted first.  Falls back to
    the in-repo path used during development.
    """
    env_override = os.getenv("HERMES_BUNDLED_PLUGINS")
    if env_override:
        return Path(env_override)
    return Path(__file__).resolve().parent.parent / "plugins"
```

`ENTRY_POINTS_GROUP = "hermes_agent.plugins"` → 第三方 pip 包通过 entry point 注入。

`_get_enabled_plugins()` 默认 opt-in（即默认啥都不加载，必须显式在 `config.yaml` 写 `plugins.enabled: [...]`）—— 安全为本。`_get_disabled_plugins()` 是 deny-list，即使在 enabled 里也会被拒。

### 12.3.7 25 个 bundled skill 类别

`skills/` 顶层 25 个目录（截至该 commit）：

```
apple, autonomous-ai-agents, creative, data-science, devops, diagramming,
dogfood, domain, email, gaming, gifs, github, index-cache, inference-sh,
mcp, media, mlops, note-taking, productivity, red-teaming, research,
smart-home, social-media, software-development, yuanbao
```

每个类别下是若干 `<skill-name>/SKILL.md` 文件 + 可选 `references/`、`templates/`、`scripts/`、`assets/`。

## 12.4 运行时行为

```
hermes 启动
  │
  ├─ tools.registry 单例创建
  ├─ discover_builtin_tools()
  │     ├─ AST 扫 tools/*.py
  │     ├─ 找含 top-level registry.register(...) 的文件
  │     ├─ importlib.import_module → 触发注册
  │     └─ 返回 imported module 列表
  │
  ├─ plugins.load_plugins() (按 enabled allow-list)
  │     ├─ 扫 bundled_plugins_dir + entry points
  │     ├─ 解析 plugin.yaml manifest
  │     ├─ import + 调用 register(ctx)
  │     └─ ctx.register_tool(...) / ctx.register_hook(...)
  │
  └─ tools.mcp_tool 启动（如配置了 mcp_servers）
        ├─ 连接所有 server
        ├─ list_tools()
        └─ 每个 tool 注册成 toolset="mcp-<server>"

LLM tool_call 触发
  │
  ├─ registry.dispatch(name, args)
  ├─ pre_tool_call hooks 链式调用 → 可改 args
  ├─ entry = _tools[name]
  ├─ _check_fn_cached(entry.check_fn) → False 则返回不可用
  ├─ handler(args) 执行
  ├─ post_tool_call hooks 链式调用 → 可改 result
  └─ 返回结果

技能加载
  │
  ├─ 用户 /<skill-name> 或 LLM 调 skill_view
  ├─ 读 SKILL.md frontmatter + body
  ├─ 平台过滤：skill.platforms 不含当前 OS → 拒绝
  ├─ preprocess_skill_content():
  │     ├─ substitute_template_vars (HERMES_SKILL_DIR / SESSION_ID)
  │     └─ expand_inline_shell (如配置开了)
  └─ 注入到下一轮 system / user message
```

## 12.5 局限与边界

- **无热加载**：工具、技能、插件都需要重启 hermes 才能生效（MCP `notifications/tools/list_changed` 不在主路径）。
- **`requires_env` / `check_fn` 是工具自检**：用户没装的工具被自动隐藏，LLM 看不到（这是 feature，但也意味着工具的"为什么不能用"诊断要靠 `hermes tools list --include-disabled`）。
- **`prerequisites` 是 advisory**：技能 frontmatter 写 `commands: [curl, jq]`，但没装也不阻止加载；加载后跑失败才会暴露。
- **hook 返回语义参差**：`transform_llm_output` 是 first-non-None-wins，`pre_tool_call` 是链式，`pre_gateway_dispatch` 是 action-dict（skip/rewrite/allow） —— 三种语义，要看文档才知道。
- **插件无版本管理**：`registry.register(override=True)` 直接覆盖，不检查工具版本兼容。
- **MCP 工具崩溃没有自动恢复语义**：subprocess crash → 工具变 unavailable 但 agent 不知道，下次 list 才发现。

---

# 维度 13 — UI 形态（CLI + 多消息平台）

## 13.1 设计立场

Hermes 把 **CLI 当作"另一种 messenger adapter"**：一份 Agent 内核 + N 种入口（CLI / Telegram / Slack / 飞书 / ...）。所有入口共享同一个 `SessionDB`（`hermes_state.py`）、同一个 tools registry、同一份 skills / memory。**用户在 CLI 里的会话和在 Telegram 里的会话被同一套 session 路由识别**，不区分对待。

CLI 本身是个**重型 TUI**：`cli.py` 单文件 **15089 行**，是整个工程最大的模块（甚至比 conversation_loop.py 还大），主要因为它把所有 slash 命令、设置面板、模型选择器、技能浏览器、credentials 设置流程、doctor 诊断等都揉在了一起。

## 13.2 关键代码路径

| 角色 | 文件 | 关键符号 |
|------|------|---------|
| CLI 主类 | `cli.py:2882` | `class HermesCLI` |
| CLI main 入口 | `cli.py:14730` | `def main()` |
| prompt_toolkit 引入 | `cli.py:49-65` | `Application` / `Layout` / `HSplit` / `Window` / `KeyBindings` / `FileHistory` |
| Rich 引入 | `cli.py:765-769` | `Console` / `Panel` / `Markdown` / `Text` |
| 光标行为 | `cli.py:76` | `CursorShape.BLOCK` 防闪烁 |
| Session 存储 | `hermes_state.py`（140KB+） | session 持久化逻辑 |
| Gateway runner | `gateway/run.py` | 统一消息路由 |
| 启动文档 | `cli.py:4-10` docstring | 命令行用法示例 |

## 13.3 实现细节

### 13.3.1 启动模式（来自 `cli.py:4-10`）

```text
python cli.py                          # 交互模式（所有工具）
python cli.py --toolsets web,terminal  # 限制工具集
python cli.py --skills skill1,skill2   # 预加载技能
python cli.py --list-tools             # 列出工具后退出
```

实际安装后是 `hermes` 命令；上面是 dev 直跑的形式。

### 13.3.2 TUI 栈

- **prompt_toolkit** —— 主 TUI 框架（输入区在底部、输出在上方）；`Application` + `Layout` + `HSplit` + `Window` + `TextArea` + `KeyBindings` 全套使用；
- **Rich** —— 流式输出渲染（Markdown / Panel / Text，可彩色高亮）；
- **FileHistory** —— 命令历史持久化到 `~/.hermes/history`；
- `CursorShape.BLOCK` —— 显式锁定 cursor 形状，避免某些终端 cursor 跳形闪烁。

### 13.3.3 CLI 的并发模型

CLI 本身是单线程 asyncio loop（prompt_toolkit 默认）。LLM 调用和工具执行通过 thread pool 跑（同 main agent loop），完成后把 delta / result 推回 prompt_toolkit 的 application_loop。中断（Ctrl+C 或 Esc）走 `_interrupt_requested` 标志 + `asyncio.Task.cancel()` 组合。

### 13.3.4 CLI 与 Gateway 共享内核

`gateway/run.py` 的 `GatewayRunner` 启动后会拉起 N 个 `BasePlatformAdapter`；每个 adapter 的入站消息走相同的 dispatcher → `AIAgent.run_conversation(...)`。CLI 在这套架构里相当于一个 **"local" platform**（`Platform.LOCAL`），enum 第一个值就是它。

session 数据流向也统一：所有 platform（含 CLI）都把对话写到同一个 SessionDB。这意味着用户可以在 CLI 里 `/sessions list` 看到自己昨天在 Telegram 上的对话。

### 13.3.5 Slash 命令

CLI 内置的 slash 命令是**单独一组**，跟"gateway 平台支持的命令"有 overlap 但不完全相同。常见的：
- `/model` — 切模型
- `/provider` — 切 provider
- `/toolsets` — 启/禁工具集
- `/skills` — 列技能
- `/<skill-name>` — 加载技能（前缀触发）
- `/memory` — 看/编辑 MEMORY.md
- `/cron` — 管理 cron jobs
- `/sessions` — 切会话
- `/stop`, `/reset`, `/new` — 中断、重置、新会话
- `/doctor` — 诊断（凭据、网络、工具可用性）
- `/voice` — 语音相关
- `/help` — 帮助

## 13.4 运行时行为

```
hermes 启动（CLI 模式）
  │
  ├─ argparse 解析命令行
  ├─ load_config()
  ├─ discover_builtin_tools()
  ├─ load_plugins()
  ├─ create AIAgent(provider, model, toolsets, skills, ...)
  │     └─ MemoryStore.load_from_disk()
  │     └─ SkillStore + agentskills registry
  │     └─ ProviderProfile (从 plugins/model-providers)
  │
  └─ HermesCLI(agent).run()
        ├─ 创建 prompt_toolkit Application
        ├─ 注册 KeyBindings（Ctrl+C / Shift+Enter / Esc 等）
        ├─ FileHistory ~/.hermes/history 接管输入历史
        ├─ Rich Console 接管输出
        │
        └─ 主循环：
              ├─ 读用户输入（Shift+Enter / Ctrl+Enter 提交）
              ├─ if 输入是 /command:
              │     handle_slash_command(...)
              ├─ else:
              │     SessionSource(platform=LOCAL, chat_id=cli, ...)
              │     agent.run_conversation(text, ...)
              │       └─ 流式回调推到 Rich Console
              │       └─ 工具调用 emoji + 实时状态在 TUI 显示
              └─ SessionDB flush

hermes gateway （消息平台模式）
  │
  ├─ 不创 HermesCLI；改启 GatewayRunner
  ├─ GatewayRunner 拉起所有 enabled platform adapter
  ├─ adapter 入站消息 → dispatcher → AIAgent.run_conversation
  ├─ 流式回调推回 adapter.send / edit_message / send_draft
  └─ SessionDB 共享
```

## 13.5 局限与边界

- **`cli.py` 15000 行单文件**：维护代价高；新人很难快速定位某个 slash 命令的实现。
- **Rich 流式 vs adapter 流式不对齐**：CLI 看的是 token-by-token Rich 输出；Telegram 看的是 batch edit_message 更新。同一会话从 CLI 切到 Telegram 不会有 100% 一致的视觉体验。
- **会话历史在 SessionDB 但 CLI 输入历史在 `~/.hermes/history`**：两套；CLI 重启后只能 ↑ 翻最近 N 行输入，要看完整对话需要 `/sessions view <id>`。
- **`gateway` 和 CLI 不能同时跑同一 `$HERMES_HOME`**：会抢同一 cron lock / session DB 写锁。
- **没有 web UI**：仅 CLI + 消息平台；任何浏览器内体验都要靠第三方桥接（如 OpenWebUI + custom backend）。

---

# 维度 14 — 技能可移植（agentskills.io）

## 14.1 设计立场

Hermes 的技能格式**对齐 agentskills.io 标准**（一个跨工具的技能 hub 协议），让同一份 `SKILL.md` 可以在 Hermes / Claude Code / 其他兼容工具上跑。具体做法：

- SKILL.md 用 **YAML frontmatter + Markdown body** 格式；
- frontmatter 字段拆为"agentskills.io 标准字段（name/description/version/license/...）" + "Hermes 私有 metadata（在 `metadata.hermes` 嵌套下）"；
- 支撑文件分四类标准子目录：`references/` / `templates/` / `scripts/` / `assets/`；
- **渐进式披露**：`skills_list` 只返回 metadata（省 token），`skill_view` 加载完整内容，`skill_view(name, "references/xxx.md")` 加载指定支撑文件。

`tools/skills_tool.py:9-12` docstring 原文：

> "Inspired by Anthropic's Claude Skills system with progressive disclosure architecture:
>  - Metadata (name ≤64 chars, description ≤1024 chars) - shown in skills_list
>  - Full Instructions - loaded via skill_view when needed
>  - Linked Files (references, templates) - loaded on demand"

## 14.2 关键代码路径

| 角色 | 文件 | 关键符号 |
|------|------|---------|
| 文档规范 | `tools/skills_tool.py:14-46` | 目录结构 + frontmatter 完整示例 |
| skills_list / skill_view 工具 | `tools/skills_tool.py` 后段 | （没单独列函数行，docstring 说明 API） |
| 平台过滤 | `tools/skills_tool.py:99-103` + `:152-159` | 标准化 → `skill_matches_platform()` |
| Prerequisite 规范化 | `tools/skills_tool.py:162-179` | `_collect_prerequisite_values()`（推断函数名） |
| 技能管理 | `tools/skill_manager_tool.py` | 6 个 action（见维度 2） |
| 预处理 | `agent/skill_preprocessing.py` | template vars + inline shell |
| Curator | `agent/curator.py` | 库级维护 |

## 14.3 实现细节

### 14.3.1 SKILL.md 完整 frontmatter（`tools/skills_tool.py:28-46`）

```yaml
---
name: skill-name              # Required, max 64 chars
description: Brief description # Required, max 1024 chars
version: 1.0.0                # Optional
license: MIT                  # Optional (agentskills.io)
platforms: [macos]            # Optional — restrict to specific OS platforms
                              #   Valid: macos, linux, windows
                              #   Omit to load on all platforms (default)
prerequisites:                # Optional — legacy runtime requirements
  env_vars: [API_KEY]         #   Legacy env var names are normalized into
                              #   required_environment_variables on load.
  commands: [curl, jq]        #   Command checks remain advisory only.
compatibility: Requires X     # Optional (agentskills.io)
metadata:                     # Optional, arbitrary key-value (agentskills.io)
  hermes:
    tags: [fine-tuning, llm]
    related_skills: [peft, lora]
---
```

关键 trade-off：
- **`name` 64 char、`description` 1024 char**：硬上限，超出会被截或拒；
- **`platforms`** = OS 限制，agent 在不匹配的 OS 上不会显示该技能；
- **`prerequisites.env_vars`** 进 `required_environment_variables`（强约束）；
- **`prerequisites.commands`** = advisory（只警告不强制）；
- **`metadata.hermes`** 是 Hermes 私有命名空间，agentskills.io 标准里位置自由。

### 14.3.2 目录结构 4 子目录约定

`tools/skills_tool.py:14-26`：

```text
skills/
├── my-skill/
│   ├── SKILL.md           # Main instructions (required)
│   ├── references/        # Supporting documentation
│   │   ├── api.md
│   │   └── examples.md
│   ├── templates/         # Templates for output
│   │   └── template.md
│   └── assets/            # Supplementary files (agentskills.io standard)
└── category/              # Category folder for organization
    └── another-skill/
        └── SKILL.md
```

`assets/` 是 agentskills.io 的标准子目录（任意补充文件，如图片、二进制）；Hermes 的 background review 在写支撑文件时只允许 `references/` / `templates/` / `scripts/` 三种（`assets/` 由用户或 hub 安装写入）。

### 14.3.3 渐进式披露的三层 API

| 层 | API | 加载量 | 用途 |
|----|-----|-------|------|
| 1 | `skills_list()` | metadata 列表 | LLM 决定要不要加载某 skill |
| 2 | `skill_view("name")` | SKILL.md 全文（含 frontmatter） | LLM 实际执行该技能 |
| 3 | `skill_view("name", "references/api.md")` | 单个支撑文件 | LLM 需要参考材料时按需加载 |

这套 API 设计直接对应 prompt context 的 token 成本：list 阶段只花 ~50 token/skill，view 才花 1-5k token，linked file 看具体大小。

### 14.3.4 平台过滤

`tools/skills_tool.py:99-103`：

```python
# Platform normalization
# 把 macos / darwin / Darwin 统一映射，避免 frontmatter 写法分歧
PLATFORM_NORMALIZE = {"darwin": "darwin", "macos": "darwin", ...,
                      "win32": "win32", "windows": "win32", ...}
```

`skill_matches_platform()`（`:152-159`） 把 frontmatter `platforms` 列表里的每个 OS name 标准化后跟当前 `sys.platform` 比对，不匹配的技能从 `skills_list()` 结果中过滤掉。

### 14.3.5 prerequisites 规范化

`tools/skills_tool.py:162-179` 把 legacy 字段（`prerequisites.env_vars`）规范化成 `required_environment_variables`（更明确的命名）。`commands` 仍是 advisory，因为 hermes 没法 100% 知道用户系统装了啥（PATH 顺序、shell 别名）。

### 14.3.6 hub 安装链路

源代码中**没有** `hermes skills install <hub_url>` 的完整实现路径（细节散落在 `tools/skills_hub.py` 之类的辅助模块，未在本次主线源码中暴露）。`tools/skill_manager_tool.py:50-65` 提到"external hub installs always get scanned"（外部 hub 安装的技能必走 `tools/skills_guard.py` 的 `scan_skill()`），可推断：

- 安装路径：hub 下载 → 解压到 `~/.hermes/skills/` → 自动 `scan_skill()` → 不过则拒；
- 区分元数据：`skill_provenance.py` 记录来源（bundled / hub / agent-created / user-edit）；
- 撤销：bundled / hub-installed 技能 background review **禁止改**（见维度 2 prompt 中的 "Protected skills"）。

## 14.4 运行时行为

```
LLM 调 skills_list()
  │
  ├─ 扫 ~/.hermes/skills/ + bundled skills/
  ├─ 对每个 SKILL.md：
  │     ├─ 解 frontmatter
  │     ├─ skill_matches_platform(frontmatter.platforms)? 否 → 跳过
  │     ├─ 在 config disabled 列表里？ → 跳过
  │     └─ 收集 {name, description, version, tags, ...}
  └─ 返回 metadata 列表

LLM 调 skill_view("axolotl")
  │
  ├─ 找到 SKILL.md
  ├─ 读全文
  ├─ preprocess_skill_content():
  │     ├─ substitute_template_vars($HERMES_SKILL_DIR / $HERMES_SESSION_ID)
  │     └─ expand_inline_shell（如开启）
  └─ 返回 content（可能含 references/templates 链接）

LLM 调 skill_view("axolotl", "references/dataset-formats.md")
  │
  ├─ 拼路径：<skill_dir>/references/dataset-formats.md
  ├─ 安全检查：路径不能 .. 逃出 skill_dir
  ├─ 读 → 返回

agent 后台 review 调 skill_manage(action="create", ...)
  │
  └─ 见维度 2
```

## 14.5 局限与边界

- **公开 hub 流程在主代码不完全可见**：本报告基于本仓快照；hub 协议（discover / publish）需要看辅助模块或外部仓。
- **依赖声明弱**：`prerequisites.commands` 只警告不强制；技能跨机迁移可能因为缺 `jq` 之类悄无声息失败。
- **无版本兼容**：技能 frontmatter 的 `version` 只是字符串；没有"该技能需要 hermes >= X" 检查。
- **平台只到 OS**：不支持 "需要 Python 3.11+" / "需要 docker daemon" / "需要本地 8B+ 模型" 这类更细的能力维度。
- **`inline_shell` 默认关**：跨工具移植时如果原作者依赖 `!`cmd``，在新环境（hermes 默认配置）下不会展开。
- **`metadata.hermes` 仅 Hermes 看**：兼容工具忽略该块，所以"related_skills" 等导航能力到其它工具就丢了。

---

# 维度 15 — RL 训练（Atropos + 轨迹压缩）

## 15.1 设计立场

Hermes 在主代码里**只承担三件事**与 RL 相关：

1. **轨迹收集**：每次 agent 运行完写一份 ShareGPT 格式的 JSONL；
2. **轨迹压缩**（`trajectory_compressor.py` 单独脚本）：用 OpenRouter 上的廉价模型把超长轨迹压到 token 预算内，保留训练信号；
3. **Atropos 适配点**：提及多处"Called by Atropos environments before the agent loop"（如 `tools/terminal_tool.py`），说明 hermes 的 sandbox / terminal 工具被 Atropos（Nous Research 的 RL 训练环境）当作 environment 使用。

**主代码里没有**：
- 在线 RL（PPO/GRPO/DPO 实时更新）；
- reward model；
- 自动打分 / 自动选最优轨迹；
- 推理 ↔ 微调闭环。

这些都被设计在**外部训练流水线**里，Hermes 只负责"出训练样本"。

## 15.2 关键代码路径

| 角色 | 文件 | 关键符号 |
|------|------|---------|
| 轨迹保存 | `agent/trajectory.py:30-56` | `save_trajectory(trajectory, model, completed, filename)` |
| Scratchpad 标准化 | `agent/trajectory.py:16-27` | `convert_scratchpad_to_think` / `has_incomplete_scratchpad` |
| 压缩脚本 | `trajectory_compressor.py:1-32` | 主模块 docstring + CLI 用法 |
| 压缩配置 | `trajectory_compressor.py:82-150+` | `class CompressionConfig` |
| 压缩策略 | `trajectory_compressor.py:7-15` | 6 步说明（docstring 原文） |
| Atropos 接入点 | `tools/terminal_tool.py` 注释 | "Called by Atropos environments before the agent loop" |

## 15.3 实现细节

### 15.3.1 轨迹格式 = ShareGPT

`agent/trajectory.py:30-56`，单函数实现：

```python
def save_trajectory(trajectory: List[Dict[str, Any]], model: str,
                    completed: bool, filename: str = None):
    """Append a trajectory entry to a JSONL file.

    Args:
        trajectory: The ShareGPT-format conversation list.
        model: Model name for metadata.
        completed: Whether the conversation completed successfully.
        filename: Override output filename. Defaults to trajectory_samples.jsonl
                  or failed_trajectories.jsonl based on ``completed``.
    """
    if filename is None:
        filename = "trajectory_samples.jsonl" if completed else "failed_trajectories.jsonl"

    entry = {
        "conversations": trajectory,        # ShareGPT format
        "timestamp": datetime.now().isoformat(),
        "model": model,
        "completed": completed,
    }

    try:
        with open(filename, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        logger.info("Trajectory saved to %s", filename)
    except Exception as e:
        logger.warning("Failed to save trajectory: %s", e)
```

ShareGPT 是 HF 上常见格式 —— `conversations: [{"from": "human"|"gpt"|"tool"|"system", "value": "..."}, ...]`。Hermes 自己把 messages 转成这种格式（具体转换在 `AIAgent._convert_to_trajectory_format`，未在本文件内，但 `trajectory.py:1-6` 的 docstring 提到）。

### 15.3.2 Scratchpad 标准化

`agent/trajectory.py:16-27`：

```python
def convert_scratchpad_to_think(content: str) -> str:
    """Convert <REASONING_SCRATCHPAD> tags to <think> tags."""
    if not content or "<REASONING_SCRATCHPAD>" not in content:
        return content
    return content.replace("<REASONING_SCRATCHPAD>", "<think>").replace(
        "</REASONING_SCRATCHPAD>", "</think>")

def has_incomplete_scratchpad(content: str) -> bool:
    """Check if content has an opening <REASONING_SCRATCHPAD> without a closing tag."""
    if not content:
        return False
    return "<REASONING_SCRATCHPAD>" in content and "</REASONING_SCRATCHPAD>" not in content
```

为什么标准化：Hermes 内部用 `<REASONING_SCRATCHPAD>` 标签包推理块（让 model 区分思考 vs 输出），但训练时用 `<think>` 是更通用的约定（与 DeepSeek-R1 / o1 这类训练样本一致）。

### 15.3.3 压缩策略（`trajectory_compressor.py:7-15` 原文）

```text
Compression Strategy:
1. Protect first turns (system, human, first gpt, first tool)
2. Protect last N turns (final actions and conclusions)
3. Compress MIDDLE turns only, starting from 2nd tool response
4. Compress only as much as needed to fit under target
5. Replace compressed region with a single human summary message
6. Keep remaining tool calls intact (model continues working after summary)
```

跟主循环的 `ContextCompressor` 一样的 head/middle/tail 策略，但目的不同：
- `ContextCompressor` 是为了**让推理时 fit context window**；
- `trajectory_compressor` 是为了**让训练样本 fit token budget**，保高质量训练 signal。

### 15.3.4 `CompressionConfig` 完整字段（`trajectory_compressor.py:82-124`）

```python
@dataclass
class CompressionConfig:
    """Configuration for trajectory compression."""
    # Tokenizer
    tokenizer_name: str = "moonshotai/Kimi-K2-Thinking"
    trust_remote_code: bool = True

    # Compression targets
    target_max_tokens: int = 15250
    summary_target_tokens: int = 750

    # Protected turns
    protect_first_system: bool = True
    protect_first_human: bool = True
    protect_first_gpt: bool = True
    protect_first_tool: bool = True
    protect_last_n_turns: int = 4

    # Summarization (OpenRouter)
    summarization_model: str = "google/gemini-3-flash-preview"
    base_url: str = OPENROUTER_BASE_URL
    api_key_env: str = "OPENROUTER_API_KEY"
    temperature: float = 0.3
    max_retries: int = 3
    retry_delay: int = 2

    # Output
    add_summary_notice: bool = True
    summary_notice_text: str = "\n\nSome of your previous tool responses may be summarized to preserve context."
    output_suffix: str = "_compressed"

    # Processing
    num_workers: int = 4
    max_concurrent_requests: int = 50  # Max concurrent API calls for summarization
    skip_under_target: bool = True
    save_over_limit: bool = True
    per_trajectory_timeout: int = 300  # Timeout per trajectory in seconds (default: 5 min)

    # Metrics
    metrics_enabled: bool = True
    metrics_per_trajectory: bool = True
    metrics_output_file: str = "compression_metrics.json"
```

值得注意的默认值：
- **`tokenizer_name = "moonshotai/Kimi-K2-Thinking"`** —— 用 Kimi 的 tokenizer 计 token，可能因为目标是训出兼容 Kimi 系列的模型；
- **`target_max_tokens = 15250`** —— 大约 1 个 GPU server 一次能跑 SFT 的样本长度；
- **`summarization_model = "google/gemini-3-flash-preview"`** —— 廉价快速的 summarizer；
- **`max_concurrent_requests = 50`** —— 一次能让 OpenRouter 跑 50 个 summary 请求；
- **`skip_under_target = True`** —— 已经在 budget 内的不动；
- **`save_over_limit = True`** —— 实在压不下来也保留（不丢样本）；
- **`temperature = 0.3`** —— summarizer 温度低，让摘要稳定。

### 15.3.5 CLI 用法（`trajectory_compressor.py:16-30`）

```bash
# Compress a directory of JSONL files
python trajectory_compressor.py --input=data/my_run

# Compress a single JSONL file
python trajectory_compressor.py --input=data/trajectories.jsonl

# Compress 15% sample of a file
python trajectory_compressor.py --input=data/trajectories.jsonl --sample_percent=15

# Compress with custom output and token target
python trajectory_compressor.py --input=data/trajectories.jsonl --output=compressed.jsonl --target_max_tokens=16000

# Compress 10% sample from a directory
python trajectory_compressor.py --input=data/my_run --sample_percent=10
```

用 `fire` 库（`trajectory_compressor.py:45` `import fire`）暴露 CLI；支持单文件 / 目录 / 采样压缩。

### 15.3.6 Atropos 接入痕迹

源码搜不到 `import atropos`，但有几处注释提到：

- `tools/terminal_tool.py` 注释 "Called by Atropos environments before the agent loop"；
- `model_tools.py` 有 "Atropos 的 event loop" 类似注释；
- `agent/auxiliary_client.py` 通过 `_fixed_temperature_for_model` 处理一些 RL 训练目标模型的特殊温度（如 Kimi 强制 server-side temperature）。

可以推断：**Hermes 不是 Atropos 的依赖，而是 Atropos 把 Hermes 当作可调用的环境**。Atropos 通过外部 invoke 让 Hermes 跑某个任务 → 收集 trajectory → 离线 SFT/RL 训练。

## 15.4 运行时行为

```
agent 跑完一个任务（batch_runner 或 atropos rollout）
  │
  ├─ AIAgent._convert_to_trajectory_format(messages) → ShareGPT 列表
  ├─ save_trajectory(trajectory, model, completed=True/False)
  │     └─ append 到 trajectory_samples.jsonl 或 failed_trajectories.jsonl
  │
  └─ 离线 → 跑 trajectory_compressor.py
        │
        ├─ load JSONL → List[trajectory]
        ├─ for each trajectory:
        │     ├─ tokenize → 计 token
        │     ├─ skip_under_target? 跳过
        │     ├─ 分 head / middle / tail
        │     ├─ summarize middle via OpenRouter:
        │     │     summarization_model = "google/gemini-3-flash-preview"
        │     │     temperature = 0.3
        │     │     max_retries = 3
        │     ├─ 替换 middle 为 summary message
        │     ├─ 验证总 token ≤ target
        │     └─ 写 _compressed.jsonl
        │
        └─ 输出 compression_metrics.json

后续（不在 Hermes 代码内）：
  压缩后的 JSONL → HF datasets → TRL/Axolotl/LlamaFactory SFT 微调
  微调好的 model → 部署成 OpenAI 兼容端点
  通过 ProviderProfile(custom, base_url=...) 接回 Hermes 推理
```

## 15.5 局限与边界

- **无在线 RL**：不支持 PPO/GRPO/DPO 这类 step-by-step reward 更新。
- **`completed` 是唯一打分维度**：成功完成 vs 失败；没有 reward model 或 multi-dimensional 评分。
- **依赖外部 OpenRouter**：trajectory_compressor 必须有 `OPENROUTER_API_KEY`，否则压不动。
- **Atropos 集成靠注释，不是 first-class**：源码里没有"Atropos transport"或"Atropos protocol"实现，只是被动适配。
- **微调 → 部署回流不在代码内**：训完的 model 怎么再接回 Hermes 推理是用户责任（写一份 custom provider profile）。
- **Tokenizer 锁定**：默认用 Kimi tokenizer 计 token；用其他系列模型微调时要换 tokenizer，否则字数偏差。

---

# 附录 A — Hermes 顶层目录速查

```
hermes-agent/
├── cli.py                  15089 行  CLI 主入口（含所有 slash 命令）
├── batch_runner.py                  批量运行入口（生成 trajectories）
├── mini_swe_runner.py               精简版 SWE-Bench 跑分入口
├── mcp_serve.py             897 行  MCP Server 入口（hermes mcp serve）
├── hermes_bootstrap.py              启动引导
├── hermes_state.py                  SessionDB 主类
├── hermes_constants.py              全局常量
├── hermes_logging.py                日志
├── hermes_time.py                   时间工具
├── trajectory_compressor.py 1508 行  轨迹压缩脚本
│
├── agent/                          大脑层
│   ├── conversation_loop.py 4306 行  主循环
│   ├── tool_executor.py     912 行  工具执行
│   ├── iteration_budget.py   62 行  迭代预算
│   ├── background_review.py 593 行  后台 review fork
│   ├── curator.py          1781 行  Curator 库级维护
│   ├── context_compressor.py 1749 行  上下文压缩
│   ├── context_engine.py            上下文引擎
│   ├── memory_provider.py           外部记忆提供商抽象
│   ├── memory_manager.py            记忆管理器
│   ├── prompt_caching.py            前缀缓存
│   ├── model_metadata.py            模型上下文长度
│   ├── auxiliary_client.py          aux LLM 客户端
│   ├── trajectory.py         56 行  轨迹保存
│   ├── skill_preprocessing.py       skill 预处理
│   ├── *adapter.py                  各 API mode 适配器（anthropic/bedrock/codex/gemini/...）
│   └── ...
│
├── tools/                          工具层
│   ├── registry.py          589 行  工具 registry
│   ├── memory_tool.py       724 行  MEMORY.md / USER.md
│   ├── skill_manager_tool.py 1034 行 技能 CRUD
│   ├── skills_tool.py               技能加载（list / view）
│   ├── skill_provenance.py          来源追踪
│   ├── delegate_tool.py    2801 行  子 agent
│   ├── terminal_tool.py             shell 执行
│   ├── code_execution_tool.py       Python 执行
│   ├── mcp_tool.py         3593 行  MCP Client
│   ├── mcp_oauth.py / mcp_oauth_manager.py  MCP OAuth
│   ├── cronjob_tools.py     863 行  cron 工具 + prompt 注入扫描
│   ├── browser_*.py / computer_use* 浏览器自动化
│   ├── vision_tools.py / fal_*      视觉/媒体
│   ├── feishu_*.py                  飞书工具
│   ├── threat_patterns.py           威胁模式库
│   ├── skills_guard.py              技能安全扫描
│   ├── environments/                沙箱后端
│   │   ├── base.py          854 行  抽象基类
│   │   ├── local.py / docker.py / ssh.py / modal.py / managed_modal.py
│   │   ├── daytona.py / singularity.py / vercel_sandbox.py
│   │   ├── file_sync.py / modal_utils.py
│   │   └── ...
│   └── ...
│
├── gateway/                        消息平台层
│   ├── run.py                       GatewayRunner
│   ├── config.py           1920 行  Platform enum + 配置
│   ├── session.py          1348 行  SessionSource / session key
│   └── platforms/
│       ├── base.py         4241 行  BasePlatformAdapter
│       ├── telegram.py / slack.py / signal.py / discord(plugin)
│       ├── feishu.py / wecom.py / dingtalk.py / weixin.py / yuanbao.py
│       ├── whatsapp.py / matrix.py / email.py / sms.py
│       ├── api_server.py / webhook.py / msgraph_webhook.py
│       └── ...
│
├── cron/                           Cron
│   ├── jobs.py             1237 行  job CRUD
│   └── scheduler.py        2039 行  tick / run_job
│
├── providers/                      Provider 抽象
│   ├── base.py              184 行  ProviderProfile dataclass
│   └── __init__.py          191 行  注册表 + 三层发现
│
├── plugins/                        插件 + bundled 拓展
│   ├── model-providers/             30 个 provider profile
│   │   ├── anthropic/ openai-codex/ openrouter/ gemini/ ...
│   │   └── ollama-cloud/ bedrock/ azure-foundry/ ...
│   ├── platforms/                   8 个平台插件
│   │   ├── discord/ google_chat/ irc/ line/
│   │   └── mattermost/ ntfy/ simplex/ teams/
│   └── ...
│
├── skills/                         25 个 bundled skill 类别
│   ├── apple/ creative/ data-science/ devops/ diagramming/
│   ├── dogfood/ email/ github/ mcp/ mlops/ ...
│   └── yuanbao/
│
├── hermes_cli/                     CLI 工具层
│   ├── plugins.py                   插件加载 + 17 个 hook
│   ├── runtime_provider.py          运行时 provider 解析
│   ├── config.py / env_loader.py    配置 / .env 加载
│   ├── mcp_config.py                MCP 配置
│   └── ...
│
├── acp_adapter/ acp_registry/      Agent Communication Protocol
├── docker/ flake.nix / Dockerfile  打包
└── docs/ assets/ infographic/      文档与资源
```

## 附录 B — 关键文件索引（按维度）

| 维度 | 主要文件 |
|------|---------|
| 1. Agent 循环 | `agent/conversation_loop.py`, `agent/tool_executor.py`, `agent/iteration_budget.py` |
| 2. 自我改进 | `agent/background_review.py`, `tools/skill_manager_tool.py`, `agent/curator.py`, `tools/skill_usage.py`, `tools/skills_guard.py`, `tools/skill_provenance.py` |
| 3. 记忆 | `tools/memory_tool.py`, `tools/threat_patterns.py`, `agent/memory_provider.py`, `agent/memory_manager.py`, `agent/prompt_caching.py` |
| 4. 上下文管理 | `agent/context_compressor.py`, `agent/context_engine.py`, `agent/model_metadata.py`, `agent/auxiliary_client.py` |
| 5. Provider | `providers/base.py`, `providers/__init__.py`, `plugins/model-providers/<name>/__init__.py`（30 个）, `agent/rate_limit_tracker.py` |
| 6. 本地推理 | `agent/model_metadata.py`（`is_local_endpoint` / `query_ollama_num_ctx`）, `plugins/model-providers/ollama-cloud/`, `plugins/model-providers/custom/` |
| 7. 沙箱 | `tools/environments/base.py`, `tools/environments/{local,docker,ssh,modal,managed_modal,daytona,singularity,vercel_sandbox}.py`, `tools/environments/file_sync.py` |
| 8. 子 Agent | `tools/delegate_tool.py`, `tools/approval.py` |
| 9. Cron | `cron/jobs.py`, `cron/scheduler.py`, `tools/cronjob_tools.py` |
| 10. 平台 | `gateway/config.py`, `gateway/platforms/base.py`, `gateway/session.py`, `gateway/platforms/{telegram,slack,signal,...}.py`, `plugins/platforms/<name>/` |
| 11. MCP | `tools/mcp_tool.py`, `mcp_serve.py`, `tools/mcp_oauth.py`, `tools/mcp_oauth_manager.py`, `hermes_cli/mcp_config.py` |
| 12. 扩展机制 | `tools/registry.py`, `tools/skills_tool.py`, `agent/skill_preprocessing.py`, `hermes_cli/plugins.py` |
| 13. UI | `cli.py`, `hermes_state.py`, `gateway/run.py` |
| 14. 技能可移植 | `tools/skills_tool.py`, `tools/skill_manager_tool.py`, `agent/skill_preprocessing.py`, `tools/skills_guard.py` |
| 15. RL | `agent/trajectory.py`, `trajectory_compressor.py`, `batch_runner.py`, `mini_swe_runner.py` |

## 附录 C — 几个关键设计模式总结

1. **Declarative profile + plugin discovery**（Provider / Platform / Plugin 三处共用）
   - 关键属性放 dataclass，行为放抽象方法；
   - 三层发现（bundled → user → legacy）后写覆盖前写；
   - 别名机制方便用户。

2. **Frozen snapshot vs live state**（MEMORY、system prompt 用）
   - 写入面修改 live state；
   - 读取面读 frozen snapshot；
   - 投毒条目在 snapshot 替换为占位符，live 保留以便用户手动清理；
   - 前缀缓存稳定性靠 snapshot 维持。

3. **Background fork + tool whitelist + prefix-cache reuse**（后台 review）
   - 新 AIAgent 实例 + 继承 cached system prompt → 缓存命中近免费；
   - 工具白名单代替 deny-list；
   - daemon thread 不阻塞主进程退出。

4. **Two-tier prompt injection scan**（cron）
   - 创建时严扫（含隐形 Unicode）；
   - 运行时（拼完 skill 内容）宽扫；
   - 平衡安全和误杀。

5. **Snapshot-source-eval pattern**（sandbox）
   - 一次性 export -p / declare -f / alias -p → snapshot 文件；
   - 每条命令 source snapshot + cd + eval；
   - CWD/env 跨命令自动持久化。

6. **Progressive disclosure**（skill）
   - 三层 API（list / view / view-linked-file）；
   - Token 成本对应加载深度；
   - 适合大量 skill 共存。

7. **Lazy compression with multi-pass**（context）
   - 不到阈值不压；
   - 单次不够再压；
   - 失败 cooldown 防退化。

8. **Isolated subagent with auto-approval-callback**（delegate）
   - 子独立 IterationBudget；
   - 工具黑名单（含 delegate 自己防递归）；
   - worker thread 不能调 input()，安装 auto-deny 回调。

— EOF —
