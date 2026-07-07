# DeerFlow 源码深度分析 + Vendor 清单

> 调研日期：2026-05
> 源码版本：本地 `/Users/mac/src/github/deer-flow`（基于源码而非网络资料）
> 结论：**整体不作基础库 fork，但可零依赖复用其中 ~10 个模块（~4000 行）**

---

## 1. 真实架构（基于源码事实）

### 代码组织

```
deer-flow/
├── backend/
│   ├── packages/harness/          # 独立 SDK 包：deerflow-harness（201 个 Python 文件）
│   │   └── deerflow/
│   │       ├── agents/            # lead_agent + 14 个 middleware + memory
│   │       ├── client.py
│   │       ├── community/         # 第三方集成（firecrawl/exa/jina/ddg/tavily/serper/infoquest/aio_sandbox/image_search）
│   │       ├── config/
│   │       ├── guardrails/
│   │       ├── mcp/
│   │       ├── models/
│   │       ├── persistence/       # migrations/user/feedback/thread_meta/run/models
│   │       ├── reflection/
│   │       ├── runtime/           # stream_bridge/events/runs/checkpointer/store/user_context
│   │       ├── sandbox/           # local/
│   │       ├── skills/            # storage/
│   │       ├── subagents/         # builtins/
│   │       ├── tools/             # builtins/
│   │       ├── tracing/
│   │       ├── uploads/
│   │       └── utils/
│   ├── app/                       # FastAPI 应用层（gateway + IM channels），41 个文件
│   └── tests/
└── frontend/                      # Next.js 应用，通过 /api/langgraph/* 调用后端
```

**关键发现 1**：`harness/` 是清晰的 SDK 包（pyproject.toml 单独发布），`app/` 是应用层，`frontend/` 完全解耦。
**关键发现 2**：harness 包内部仍紧耦合 `langchain.agents.middleware` 和 `lead_agent` 单图架构。

### 编排不是多 StateGraph，而是单一 lead_agent + middleware 链

**证据**：
- `langgraph.json:8-9`：只注册一个图：`"lead_agent": "deerflow.agents:make_lead_agent"`
- `agents/lead_agent/agent.py:240-318`：`_build_middlewares()` 顺序拼装 14 个 middleware

**14 个中间件**：
1. `ThreadDataMiddleware` — thread 元数据传播
2. `UploadsMiddleware` — 文件上传处理
3. `SandboxMiddleware` — sandbox 注入
4. `DanglingToolCallMiddleware` — 孤立工具调用修复
5. `GuardrailMiddleware`（可选）— 工具白名单 / 护栏
6. `ToolErrorHandlingMiddleware` — 异常 → ToolMessage
7. `SummarizationMiddleware`（可选）— 长上下文自动总结
8. `TodoMiddleware`（可选）— Plan 模式任务列表
9. `TitleMiddleware` — 线程标题生成
10. `MemoryMiddleware` — 会话结束后队列化内存更新
11. `ViewImageMiddleware`（可选）— 图像查看工具
12. `DeferredToolFilterMiddleware`（可选）— 延迟工具过滤
13. `SubagentLimitMiddleware`（可选）— **硬编码 MAX=3，clamp [2,4]**
14. `LoopDetectionMiddleware`（可选）— 无限循环检测
15. `ClarificationMiddleware`（必须最后）— 歧义澄清

### 多 Agent 是"配置切换"不是"并行编排"
- 通过 `RunnableConfig` 的 `agent_name` 参数选择 Agent 配置
- 每个 session 只能跑一个顶级 Agent，没有真正的多图并行

### 多租户确认缺失
- 全局 grep `tenant_id` / `org_id` / `workspace_id` → **0 命中**
- 仅 `user_id`：`runtime/user_context.py` 的 `get_effective_user_id()`
- per-user 存储：`.deer-flow/users/{user_id}/agents/{name}/SOUL.md`
- 凭证：API keys 在全局 `.env` / `config.yaml`，**无 credential proxy / per-tenant secret**

### 缺失的范式
- ❌ 没有 append-only event log（只有 LangGraph PostgresSaver checkpoint）
  - **修正**：实际上 `runtime/events/store/db.py` 就是 append-only event log，只是不叫这个名字
- ❌ 没有 Brain-Hands-Session 三层独立接口（混在 middleware + tool 里）
- ❌ 没有租户级凭证代理

---

## 2. 契合度评分

| 维度 | 分数 | 关键证据 |
|------|------|---------|
| 编排引擎可复用性 | 2/5 | 14 中间件中至少 7 个 DeerFlow 特有；架构是单 lead_agent，不适配多 Agent 并行 |
| Skill 系统借鉴价值 | 3.5/5 | `skills/parser.py` + `skills/types.py` 零依赖 |
| Sandbox 方案可借鉴 | 3/5 | ABC 设计干净；缺 tenant 字段 |
| 多租户支持 | 1.5/5 | 仅 user 隔离，企业级要重做 |
| 二次开发友好度 | 3/5 | SDK 边界清晰，但内部读全局 AppConfig 单例 |
| Brain-Hands-Session 契合度 | 2/5 | 三层耦合在 middleware 链里 |

---

## 3. harness/ 各子模块借鉴清单（按价值排序）

### 🔴 P0 — 直接拷贝（~2500 行）

#### 模块 1：Append-only Event Log ⭐⭐⭐
**路径**：`runtime/events/store/{base,db,memory}.py` + `persistence/models/run_event.py`

**功能**：
- 单一 `RunEventStore` 抽象，统一管理消息和执行痕迹
- 支持 category（message/trace/lifecycle）区分
- 单调递增 `seq`（按 thread 内）
- 双向游标分页（before_seq / after_seq）
- 批量写优化（`put_batch()` 单次锁）

**关键代码**（`runtime/events/store/base.py:17-110`）：
```python
class RunEventStore(abc.ABC):
    """Run event stream storage interface.
    All implementations must guarantee:
    1. put() events are retrievable in subsequent queries
    2. seq is strictly increasing within the same thread
    3. list_messages() only returns category="message" events
    4. list_events() returns all events for the specified run
    5. Returned dicts match the RunEvent field structure
    """
    @abc.abstractmethod
    async def put(self, *, thread_id, run_id, event_type, category,
                  content="", metadata=None, created_at=None) -> dict: ...

    @abc.abstractmethod
    async def put_batch(self, events: list[dict]) -> list[dict]: ...
```

**`db.py:89-155` 实现亮点**：
```python
async def put(self, *, thread_id, run_id, event_type, category, content="", ...):
    """Low-frequency path, acquires FOR UPDATE lock for seq assignment."""
    content, metadata = self._truncate_trace(category, content, metadata)
    user_id = self._user_id_from_context()
    async with self._sf() as session:
        async with session.begin():
            max_seq = await session.scalar(
                select(func.max(RunEventRow.seq))
                .where(RunEventRow.thread_id == thread_id)
                .with_for_update()
            )
            seq = (max_seq or 0) + 1
            row = RunEventRow(..., seq=seq, ...)
            session.add(row)
        return self._row_to_dict(row)
```

**对 Expert Work 借鉴**：直接 vendor，改造点：
- `user_id` contextvar → `tenant_id`
- 添加 `pipeline_id` 字段（追踪 Agent 执行链）
- 扩展 `event_type` enum（task_start, brain_invoke, hands_execute, checkpoint, audit）

---

#### 模块 2：多租户 ThreadMeta + Run ORM ⭐⭐⭐
**路径**：`persistence/thread_meta/{base,sql,memory,model}.py` + `persistence/run/{model,sql}.py`

**核心**（`thread_meta/sql.py:30-103`）：
```python
class ThreadMetaRepository(ThreadMetaStore):
    async def create(self, thread_id, *, assistant_id=None, user_id=AUTO,
                     display_name=None, metadata=None) -> dict:
        """Create thread, auto-stamp user_id from contextvar (or explicit None)."""
        resolved_user_id = resolve_user_id(user_id, method_name="...")
        ...

    async def check_access(self, thread_id: str, user_id: str,
                          *, require_existing: bool = False) -> bool:
        """Two modes:
        - require_existing=False: untracked threads are accessible (read-safe)
        - require_existing=True: deletion blocks future access (delete-safe)
        """
        row = await session.get(ThreadMetaRow, thread_id)
        if row is None:
            return not require_existing
        return row.user_id is None or row.user_id == user_id
```

**权限模型亮点**：
- `resolve_user_id()` 三态：`AUTO`（contextvar）/ 显式 `str` / 显式 `None`（绕过）
- `check_access(require_existing)` 双模式（读容错 vs 删严格）

**对 Expert Work 借鉴**：
- `user_id` → `tenant_id` + 增加 `org_id`
- `metadata` JSONB 字段（agent_version, config_hash）
- 实现 PG `@>` 操作符的 JSON 查询

---

#### 模块 3：Checkpointer 工厂 ⭐⭐⭐
**路径**：`runtime/checkpointer/{provider,async_provider}.py`

**核心**（`provider.py:49-94`）：
```python
@contextlib.contextmanager
def _sync_checkpointer_cm(config: CheckpointerConfig) -> Iterator[Checkpointer]:
    if config.type == "memory":
        yield InMemorySaver()
    elif config.type == "sqlite":
        with SqliteSaver.from_conn_string(conn_str) as saver:
            saver.setup()
            yield saver
    elif config.type == "postgres":
        with PostgresSaver.from_conn_string(config.connection_string) as saver:
            yield saver

def get_checkpointer() -> Checkpointer:
    """Singleton API."""
    global _checkpointer, _checkpointer_ctx
    if _checkpointer is None:
        config = get_checkpointer_config()
        _checkpointer_ctx = _sync_checkpointer_cm(config)
        _checkpointer = _checkpointer_ctx.__enter__()
    return _checkpointer

@contextlib.contextmanager
def checkpointer_context() -> Iterator[Checkpointer]:
    """Context manager API（一次性，绕过单例）"""
    config = get_app_config()
    with _sync_checkpointer_cm(config.checkpointer) as saver:
        yield saver
```

**对 Expert Work 借鉴**：直接用，单例 + ctx manager 双 API 兼顾生产单例和测试隔离。

---

#### 模块 4：Store 工厂（KV）⭐⭐⭐
**路径**：`runtime/store/{provider,async_provider,_sqlite_utils}.py`

设计同 Checkpointer，但操作 K-V 存储。直接 vendor。

---

#### 模块 5：SSE Stream Bridge ⭐⭐⭐
**路径**：`runtime/stream_bridge/base.py`

**核心**（`base.py:37-73`）：
```python
@dataclass(frozen=True)
class StreamEvent:
    id: str          # Monotonic, used as SSE id: field
    event: str       # SSE event name: "metadata", "updates", "events", "error", "end"
    data: Any

HEARTBEAT_SENTINEL = StreamEvent(id="", event="__heartbeat__", data=None)
END_SENTINEL = StreamEvent(id="", event="__end__", data=None)

class StreamBridge(abc.ABC):
    @abc.abstractmethod
    async def publish(self, run_id: str, event: str, data: Any) -> None: ...

    @abc.abstractmethod
    def subscribe(self, run_id: str, *, last_event_id: str | None = None,
                  heartbeat_interval: float = 15.0) -> AsyncIterator[StreamEvent]:
        """Yields HEARTBEAT_SENTINEL if no event in heartbeat_interval seconds.
        Yields END_SENTINEL once producer calls publish_end."""

    @abc.abstractmethod
    async def cleanup(self, run_id: str, *, delay: float = 0) -> None: ...
```

**对 Expert Work 借鉴**：直接用，Last-Event-ID 自动重连 + 心跳 + 清理延迟模式很完整。

---

#### 模块 6：Run Manager（运行时状态机）⭐⭐⭐
**路径**：`runtime/runs/manager.py`（1053 行）

注册表 + 6 状态机（PENDING/RUNNING/COMPLETED/FAILED/CANCELLED/TIMED_OUT）。

---

### 🟠 P1 — 借鉴模式自重写（~1500 行）

#### 模块 7：AgentMiddleware 基类 + 5 个核心中间件 ⭐⭐⭐
**路径**：`agents/middlewares/`（19 文件，3872 行）

**核心模式**（`tool_error_handling_middleware.py:21-67`）：
```python
class ToolErrorHandlingMiddleware(AgentMiddleware[AgentState]):
    """Convert tool exceptions into error ToolMessages so run continues."""

    @override
    def wrap_tool_call(self, request: ToolCallRequest,
                       handler: Callable) -> ToolMessage | Command:
        try:
            return handler(request)
        except GraphBubbleUp:  # 保留 LangGraph 控制流信号
            raise
        except Exception as exc:
            return ToolMessage(
                content=f"Error: Tool failed with {exc.__class__.__name__}: {detail}",
                tool_call_id=request.tool_call.get("id"),
                name=request.tool_call.get("name"),
                status="error",
            )

    @override
    async def awrap_tool_call(self, request, handler) -> ToolMessage | Command:
        # 同步和异步双支持
        ...
```

**对 Expert Work 借鉴**：
- 抽 `AgentMiddleware` 基类放进 `expert-work-sdk`，**去 langchain.agents.middleware 依赖**
- 直接采纳 5 个通用中间件：`tool_error_handling`、`memory`、`token_usage`、`loop_detection`、`dangling_tool_call`
- 不采纳：`thread_data` / `uploads` / `sandbox` / `summarization` / `todo` / `title` / `view_image` / `deferred_tool_filter` / `subagent_limit`（DeerFlow 特化或与我们模型不兼容）

---

#### 模块 8：Subagent 执行器 ⭐⭐
**路径**：`subagents/executor.py`、`subagents/registry.py`

**executor.py 亮点**（1-100）：
```python
class SubagentStatus(Enum):
    PENDING, RUNNING, COMPLETED, FAILED, CANCELLED, TIMED_OUT

@dataclass
class SubagentResult:
    task_id: str
    trace_id: str            # 分布式追踪父子链接
    status: SubagentStatus
    result: str | None
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None
    ai_messages: list[dict] | None
    cancel_event: threading.Event

# 全局存储 + 后台线程池
_background_tasks: dict[str, SubagentResult] = {}
_scheduler_pool = ThreadPoolExecutor(max_workers=3,
                                     thread_name_prefix="subagent-scheduler-")
_isolated_subagent_loop: asyncio.AbstractEventLoop | None = None  # 持久化事件循环
```

**registry.py 三级配置解析**（50-117）：
```python
def get_subagent_config(name: str, *, app_config: AppConfig | None = None) -> SubagentConfig | None:
    """Resolution order:
    1. Built-in subagents (general-purpose, bash)
    2. Custom subagents from config.yaml custom_agents section
    3. Per-agent overrides from config.yaml agents section
    """
    config = BUILTIN_SUBAGENTS.get(name)
    if config is None:
        config = _build_custom_subagent_config(name, app_config=app_config)

    agent_override = subagents_config.agents.get(name)
    overrides = {}
    if agent_override and agent_override.timeout_seconds:
        overrides["timeout_seconds"] = agent_override.timeout_seconds
    elif is_builtin and subagents_config.timeout_seconds:
        overrides["timeout_seconds"] = subagents_config.timeout_seconds
    # 同样处理 max_turns, model, skills...
    if overrides:
        config = replace(config, **overrides)
    return config
```

**对 Expert Work 借鉴**：
- 抄 6 状态机 + 后台线程池 + trace_id 父子链接
- 扩展 per-tenant quota 替代 SubagentLimitMiddleware 硬编码 MAX=3

---

#### 模块 9：MCP 客户端 ⭐⭐
**路径**：`mcp/client.py`（509 行）+ 配套 4 文件

借鉴：多 MCP server 连接池、OAuth/API key 认证刷新逻辑。放进 `services/mcp-gateway/`。

---

#### 模块 10：Guardrails ⭐⭐
**路径**：`guardrails/{builtin,provider}.py`（191 行）

工具白名单 + middleware 实现，简单直接。

---

### 🟡 P2 — 思路参考

#### 模块 11：分层 Memory ⭐⭐
**路径**：`agents/memory/{storage,queue,updater,message_processing,summarization_hook}.py`（1679 行）

**结构**：
- `storage.py` — `FileMemoryStorage`（JSON 文件，线程安全）
- `queue.py` — `ConversationQueue`（debounced 批处理）
- `updater.py` — 异步内存更新（LLM 总结）

**内存 schema**：
```python
{
    "version": "1.0",
    "lastUpdated": "2024-01-15T10:30:45Z",
    "user": {
        "workContext": {"summary": "...", "updatedAt": "..."},
        "personalContext": {"summary": "...", "updatedAt": "..."},
        "topOfMind": {"summary": "...", "updatedAt": "..."},
    },
    "history": {
        "recentMonths": {"summary": "...", "updatedAt": "..."},
        "earlierContext": {"summary": "...", "updatedAt": "..."},
        "longTermBackground": {"summary": "...", "updatedAt": "..."},
    },
    "facts": [],
}
```

**借鉴**：
- 多层结构（user.{work/personal/topOfMind} + history.{recent/earlier/longTerm} + facts[]）
- debounced 队列模式
- 版本化 schema
- **不要**直接复用：实现层换 Postgres JSONB + pgvector

---

#### 模块 12：AioSandbox HTTP API 包装 ⭐⭐
**路径**：`community/aio_sandbox/aio_sandbox.py`（1934 行）

**核心**（17-100）：
```python
class AioSandbox(Sandbox):
    """Connect to running AIO sandbox container via HTTP API.
    Threading lock serializes shell commands to prevent concurrent
    requests from corrupting single persistent session."""

    def __init__(self, id: str, base_url: str, home_dir: str | None = None):
        self._base_url = base_url
        self._client = AioSandboxClient(base_url=base_url, timeout=600)
        self._lock = threading.Lock()

    def execute_command(self, command: str) -> str:
        with self._lock:
            result = self._client.shell.exec_command(command=command, no_change_timeout=600)
            output = result.data.output if result.data else ""
            # 检测损坏，用新会话重试
            if output and _ERROR_OBSERVATION_SIGNATURE in output:
                fresh_id = str(uuid.uuid4())
                result = self._client.shell.exec_command(command=command, id=fresh_id, no_change_timeout=600)
                output = result.data.output if result.data else ""
            return output or "(no output)"
```

**借鉴**：
- HTTP API 包装容器（适合我们的 sandbox-supervisor）
- threading.Lock 串行化保护单一持久会话
- ErrorObservation 自动重试

---

#### 模块 13：Skill 元数据 parser ⭐
**路径**：`skills/parser.py`（35-111）+ `skills/types.py`（19-68）

零依赖 frontmatter 解析。M2 阶段考虑作为 manifest 之外的"工作流模板"补充。

---

#### 模块 14：Tracing Factory ⭐
**路径**：`tracing/factory.py`（57 行）

借鉴 LangSmith / Langfuse 切换 provider 模式，我们用 OpenTelemetry。

---

### ❌ 不复用

| 模块 | 不用原因 |
|------|---------|
| `agents/lead_agent/` | Deep Research 特化的 14 中间件链 |
| `sandbox/sandbox.py` ABC | 接口偏 DeerFlow，我们重新定义包含 tenant/quota |
| `sandbox/local/` | macOS dev 用 docker default runtime 即可 |
| `config/` | 整套配置系统（我们的 manifest 模型完全不同）|
| `client.py` | 嵌入式客户端（我们走 HTTP/SSE）|
| `community/{firecrawl,exa,jina_ai,ddg_search,tavily,serper,infoquest,image_search}` | 搜索工具，通过 MCP 接入更标准 |
| `app/`（在 backend/ 下，不在 harness 内）| FastAPI gateway 应用层 |
| `frontend/` | Next.js（我们用 React 19 + Antd）|
| `langgraph.json` 单图注册 | 我们的 Orchestrator 自己管理多 graph |

---

## 4. Vendor 文件清单（M0 第一批）

### 第一组：Event Log（最优先）

| 源路径（绝对）| 目标路径（Expert Work）|
|---------------|---------------------|
| `/Users/mac/src/github/deer-flow/backend/packages/harness/deerflow/runtime/events/store/base.py` | `Expert Work/packages/expert-work-runtime/src/Expert Work/runtime/event_log/base.py` |
| `.../runtime/events/store/db.py` | `.../event_log/db.py` |
| `.../runtime/events/store/memory.py` | `.../event_log/memory.py` |
| `.../persistence/models/run_event.py` | `Expert Work/packages/expert-work-persistence/src/Expert Work/persistence/models/run_event.py` |

### 第二组：权限 + 元数据

| 源 | 目标 |
|----|------|
| `.../persistence/thread_meta/base.py` | `.../persistence/thread_meta/base.py` |
| `.../persistence/thread_meta/sql.py` | `.../persistence/thread_meta/sql.py` |
| `.../persistence/thread_meta/model.py` | `.../persistence/models/thread_meta.py` |
| `.../persistence/run/{model,sql}.py` | `.../persistence/{models/run.py, run/sql.py}` |
| `.../persistence/{base,engine}.py` | `.../persistence/{base,engine}.py` |
| `.../persistence/user/model.py` | `.../persistence/models/user.py` |
| `.../runtime/user_context.py` | `Expert Work/packages/expert-work-runtime/src/Expert Work/runtime/context.py`（改 tenant）|

### 第三组：工厂模式

| 源 | 目标 |
|----|------|
| `.../runtime/checkpointer/{provider,async_provider}.py` | `.../runtime/checkpointer/{provider,async_provider}.py` |
| `.../runtime/store/{provider,async_provider,_sqlite_utils}.py` | `.../runtime/store/{provider,async_provider,_sqlite_utils}.py` |

### 第四组：流 + Run Manager

| 源 | 目标 |
|----|------|
| `.../runtime/stream_bridge/base.py` | `.../runtime/stream/base.py` |
| `.../runtime/runs/manager.py` | `.../runtime/runs/manager.py` |

### 第五组（M1）：核心中间件

| 源 | 目标 |
|----|------|
| `.../agents/middlewares/tool_error_handling_middleware.py` | `Expert Work/services/orchestrator/src/orchestrator/middleware/error_handling.py` |
| `.../agents/middlewares/memory_middleware.py` | `.../middleware/memory.py` |
| `.../agents/middlewares/token_usage_middleware.py` | `.../middleware/token_usage.py` |
| `.../agents/middlewares/loop_detection_middleware.py` | `.../middleware/loop_detection.py` |
| `.../agents/middlewares/dangling_tool_call_middleware.py` | `.../middleware/dangling_tool_call.py` |

### 第六组（M1+）：可选

| 源 | 目标 | 改造成本 |
|----|------|----------|
| `.../agents/memory/storage.py` | `Expert Work/services/orchestrator/.../memory/storage.py` | 大改（换 Postgres）|
| `.../subagents/executor.py` | `Expert Work/services/orchestrator/.../subagent/executor.py` | 中（适配 gVisor + tenant quota）|
| `.../guardrails/{builtin,provider}.py` | `Expert Work/services/orchestrator/.../guardrails/` | 小 |
| `.../mcp/client.py` | `Expert Work/services/mcp-gateway/src/mcp_gateway/client.py` | 小 |

---

## 5. 实施时间表

| 阶段 | 工作项 | 时间 | 优先级 |
|------|--------|------|--------|
| **M0 P0** | Event Log + ThreadMeta ORM | 2-3 天 | 🔴 关键 |
| **M0 P0** | Checkpointer / Store 工厂 | 1-2 天 | 🔴 关键 |
| **M0 P0** | Stream Bridge (SSE) | 1-2 天 | 🟠 高 |
| **M0 P0** | Run Manager | 1 天 | 🟠 高 |
| **M1 P1** | AgentMiddleware 基类 + 5 个 MW | 3-4 天 | 🟠 高 |
| **M1 P1** | Subagent 执行器（适配 gVisor）| 2-3 天 | 🟠 高 |
| **M1 P1** | MCP Gateway 客户端 | 1-2 天 | 🟠 高 |
| **M1 P1** | Guardrails | 1 天 | 🟡 中 |
| **M2 P2** | Memory 分层（Postgres 重写）| 3-4 天 | 🟡 中 |

**总计**：~15-22 天 vendor + 借鉴重写工作（分布在 M0/M1/M2 中）。

---

## 6. License 合规

- DeerFlow License：MIT
- 我们的 LICENSE 建议 **Apache 2.0**（兼容 MIT vendor）
- 每个 vendor 文件头注释：

```python
# ============================================================
# Adapted from bytedance/deer-flow @ <commit_sha>
# Source: backend/packages/harness/deerflow/runtime/events/store/db.py
# License: MIT (see vendor/deer-flow/LICENSE)
# Modifications:
#   - Replaced user_id contextvar with tenant_id
#   - Extended event_type enum with checkpoint/audit
#   - Added pipeline_id for multi-stage tracking
# Last sync: 2026-05-09
# ============================================================
```

- `NOTICE` 文件并列声明所有 vendor 来源
- 季度同步 deer-flow 上游 bug fix（脚本：`tools/vendor_sync.py`）

---

## 7. 总结

**DeerFlow harness SDK 的核心价值在于**：
1. **Append-only Event Log** — 直接用于 Expert Work 的审计日志
2. **多租户权限模型** — SQL 查询 + contextvar 标准组合
3. **中间件链架构** — composable + chainable + 同步异步双支持
4. **工厂模式** — 后端无关、配置驱动
5. **错误恢复文化** — 异常 → 消息，不中断流程

**净收益**：
- vendor ~2500 行 P0 + 借鉴重写 ~1500 行 P1
- **节省 4-5 周开发时间**（自己写到生产级要踩 SQL 并发/seq 单调/批量锁/ErrorObservation 重试的坑）
- **不引入 deerflow-harness PyPI 依赖**（避免被 14 中间件 + 应用层 + community 拖进来）
- **保持 LangGraph 自主可换**（vendor 模块都不绑定 lead_agent 单图架构）
