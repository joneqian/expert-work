# 06 第三方依赖与 Vendor 清单

## 总览

**自研策略**：只做"粘合层 + 控制平面"
- 直接依赖（pip install）：成熟稳定的库
- Vendor（手工拷贝源码）：DeerFlow 的基础设施模块（避免 Deep Research 特化耦合）
- 不复用：DeerFlow 的端到端架构（lead_agent / 14 中间件链 / config 系统 / 应用层）

总自研行数：约 **12K** Python 代码（M0-M1）
- ~2500 行 vendor（直接复制）
- ~1500 行借鉴重写
- ~8000 行真自研（控制平面、graph_builder、sandbox supervisor、credential proxy 等）

---

## 直接依赖（pip / npm install）

| 用途 | 项目 | 许可 | 评估 |
|------|------|------|------|
| 编排核心 | **langgraph**（langchain-ai）| MIT | 引擎心脏 |
| 持久化 | **langgraph-checkpoint-postgres** | MIT | 配套 |
| MCP 协议 | **mcp**（Anthropic 官方 SDK）| MIT | 工具协议层 |
| 沙盒运行时 | **gVisor (runsc)**（Google）| Apache 2.0 | OCI 兼容 |
| API 框架 | **FastAPI** | MIT | 控制平面 |
| ORM | **SQLAlchemy 2.0** | MIT | 数据访问 |
| 校验 | **Pydantic v2** | MIT | schema |
| 数据库 | **PostgreSQL + pgvector** | PostgreSQL/Apache 2.0 | 事件日志+向量 |
| 凭证管理 | **HashiCorp Vault** | BSL/开源 | secrets 后端 |
| 凭证代理（M1+）| **Envoy** | Apache 2.0 | 出站代理 |
| Observability | **OpenTelemetry / Prometheus / Loki / Grafana** | Apache 2.0 | 全栈 |
| 配置 | **PyYAML + Jinja2** | MIT/BSD | manifest 渲染 |
| 任务队列 | **Redis + Celery / arq** | BSD/MIT | 限流 + 后台任务 |
| Admin UI | **React 19 + Vite + Antd**（已有）| MIT | 前端 |

---

## Vendor 清单（手工拷贝 deer-flow 源码）

> **⚠️ 2026-05-09 重大修正**：第三次源码深扫（详见 [research/05-deerflow-deeper-scan.md](../research/05-deerflow-deeper-scan.md)）发现 6 个被误判为"DeerFlow 特化"的通用中间件 + @Next/@Prev 锚点扩展系统。已升级清单。
>
> **修正后规模**：P0 ~3400 行（原 2500），P1 ~2020 行（原 1500），总 vendor ~5400 行。

### 🔴 P0 — 直接拷贝（~3400 行）

最高价值的基础设施层 + 生产必备中间件，自己写到生产级要踩 SQL 并发坑、seq 单调坑、prefix cache 不命中、API 成本爆炸的坑。

| DeerFlow 源码路径 | 我们存放路径 | 改造点 |
|------|----------|--------|
| `runtime/events/store/base.py` | `packages/expert-work-runtime/src/expert_work/runtime/event_log/base.py` | 🆕 **2026-05-11 修正**：原计划"接口原样保留"，但 [ADR-0002](../adr/0002-state-layer-schema.md) schema 已定（`thread_id` + `tenant_id` UUID + `payload` + `trace_id`，无 `run_id`/`category`/`content`/`event_metadata`/`user_id`）。改为**借鉴算法，自有接口**：保留 FOR UPDATE seq 分配、批量写、内容截断三大算法，接口对齐 ADR-0002。Vendor 头声明 "Algorithm-vendored, schema-our-own"。 |
| `runtime/events/store/db.py` | `.../event_log/db.py` | 同上；保留 FOR UPDATE 串行化 + 批量分配 seq + content_truncate；接口签名按 EventRecord（expert-work-protocol） |
| `runtime/events/store/memory.py` | `.../event_log/memory.py` | 同上；仅测试用 |
| `persistence/models/run_event.py` | `packages/expert-work-persistence/src/Expert Work/persistence/models/run_event.py` | 加 `tenant` 字段 + 索引 |
| `persistence/thread_meta/base.py` | `.../persistence/thread_meta/base.py` | 接口扩展 tenant |
| `persistence/thread_meta/sql.py` | `.../persistence/thread_meta/sql.py` | `resolve_user_id` → `resolve_tenant_id` |
| `persistence/thread_meta/memory.py` | `.../persistence/thread_meta/memory.py` | 测试用 |
| `persistence/thread_meta/model.py` | `.../persistence/models/thread_meta.py` | 加 tenant 字段 |
| `persistence/run/model.py` | `.../persistence/models/run.py` | 加 tenant 字段 |
| `persistence/run/sql.py` | `.../persistence/run/sql.py` | 同上 |
| `persistence/base.py` | `.../persistence/base.py` | 直接用 |
| `persistence/engine.py` | `.../persistence/engine.py` | 直接用 |
| `persistence/user/model.py` | `.../persistence/models/user.py` | 直接用 |
| `runtime/user_context.py` | `.../runtime/context.py` | 改名 + 改 `resolve_tenant_id` |
| `runtime/checkpointer/provider.py` | `.../runtime/checkpointer/provider.py` | 直接用，未来加 Redis |
| `runtime/checkpointer/async_provider.py` | `.../runtime/checkpointer/async_provider.py` | 直接用 |
| `runtime/store/provider.py` | `.../runtime/store/provider.py` | 直接用 |
| `runtime/store/async_provider.py` | `.../runtime/store/async_provider.py` | 直接用 |
| `runtime/store/_sqlite_utils.py` | `.../runtime/store/_sqlite_utils.py` | 直接用 |
| `runtime/stream_bridge/base.py` | `.../runtime/stream/base.py` | 直接用 |
| `runtime/runs/manager.py` | `.../runtime/runs/manager.py` | 加 tenant；注意：运行状态机与 checkpoint 是**正交维度**，分开实现 |
| **🆕 P0 新增（生产必备）— 6 个中间件 + 锚点系统** | | |
| `agents/factory.py:289-379`（@Next/@Prev 锚点算法 + 装饰器）| `packages/expert-work-sdk/.../middleware/anchor.py` | 把 _insert_extra 算法 + Next/Prev 装饰器抽出独立 SDK；让外部 middleware 可干净插入 |
| `agents/middlewares/dynamic_context_middleware.py` (193 行) | `services/orchestrator/.../middleware/dynamic_context.py` | **🔴 关键**：保持 system_prompt 静态最大化 prefix cache 命中（API 成本影响 ~10x）；午夜穿越检测；消息 ID swap 技术 |
| `agents/middlewares/llm_error_handling_middleware.py` (368 行) | `.../middleware/llm_error_handling.py` | **🔴 关键**：自动重试 + 错误分类 + 断路器（按 provider+key 维度），多租户级联故障防护 |
| `agents/middlewares/sandbox_audit_middleware.py` (363 行) | `.../middleware/sandbox_audit.py` | **🔴 关键**：LLM 生成命令的安全网（gVisor 之前的逻辑层防护），15 条高风险规则 + 5 条中风险规则 + quote-aware 命令拆分 |

**P0 收益**：自己写到生产级要 3-4 周（要踩 FOR UPDATE 锁、UNIQUE 兜底、批量分配 seq、内容截断防爆炸的坑）。直接 vendor → 节省 3-4 周。

---

### 🟠 P1 — 借鉴模式自重写（~2020 行）

模式参考，去 langchain 依赖，扩展我们的字段。

| DeerFlow 源码路径 | 我们存放路径 | 我们的做法 |
|------|----------|-----------|
| `agents/middlewares/tool_error_handling_middleware.py` | `services/orchestrator/src/orchestrator/middleware/error_handling.py` | 抄实现，去 langchain.agents.middleware 依赖 |
| `agents/middlewares/memory_middleware.py` | `.../middleware/memory.py` | 同上 |
| `agents/middlewares/token_usage_middleware.py` | `.../middleware/token_usage.py` | 同上 |
| `agents/middlewares/loop_detection_middleware.py` | `.../middleware/loop_detection.py` | 同上 |
| `agents/middlewares/dangling_tool_call_middleware.py` | `.../middleware/dangling_tool_call.py` | 同上 |
| `subagents/executor.py` | `services/orchestrator/.../subagent/executor.py` | 借鉴 6 状态机（PENDING/RUNNING/COMPLETED/FAILED/CANCELLED/TIMED_OUT）+ trace_id 父子链接，扩展 per-tenant quota 替代硬编码 MAX=3 |
| `subagents/registry.py` | `.../subagent/registry.py` | 借鉴 built-in / custom / per-agent override 三级解析 |
| `mcp/client.py` | `services/mcp-gateway/src/mcp_gateway/client.py` | 借鉴连接池/认证刷新 |
| `guardrails/builtin.py` | `services/orchestrator/.../guardrails/builtin.py` | 直接用 |
| `guardrails/provider.py` | `.../guardrails/provider.py` | 直接用 |
| `tracing/factory.py` | `packages/expert-work-common/src/Expert Work/common/tracing.py` | 借鉴 provider 切换，主用 OpenTelemetry |
| **AgentMiddleware 基类** | `packages/expert-work-sdk/src/Expert Work/sdk/middleware/base.py` | 抄 `wrap_tool_call` / `awrap_tool_call` 双 API 设计；不依赖 langchain，自建 |
| **🆕 P1 新增（M1 vendor）** | | |
| `agents/middlewares/thread_data_middleware.py` (118 行) | `services/orchestrator/.../middleware/thread_data.py` | **底层基础**：thread/tenant 元数据从 RunnableConfig 注入到 state，必须放 chain 最前（其他 middleware 都依赖它）|
| `agents/middlewares/uploads_middleware.py` (295 行) | `services/orchestrator/.../middleware/uploads.py` | 文件上传 + 大纲提取（heading + line numbers），LLM 可精准定位文件内容；适配我们的对象存储 |
| `agents/middlewares/deferred_tool_filter_middleware.py` (107 行) | `services/orchestrator/.../middleware/deferred_tool_filter.py` | 配合 tool_search 解决"工具数量爆炸"（MCP 多 server 场景，50+ 工具不能全塞 prompt）|
| `agents/middlewares/token_usage_middleware.py` (303 行) | `services/orchestrator/.../middleware/token_usage.py` | 步骤归因（按 todo/subagent 维度追踪 input/output/cache_creation/cache_read），dogfood 阶段成本分析必需 |
| `reflection/resolvers.py` (98 行) | `packages/expert-work-common/.../reflection.py` | `resolve_class()`/`resolve_variable()` — manifest 中字符串路径（如 `agents.foo.tools:bar`）的运行时解析 |

---

### 🟡 P2 — 思路参考（不直接抄）

| 模块 | 借鉴点 |
|------|--------|
| **Memory 分层结构** `agents/memory/storage.py` + `queue.py` + `updater.py` | 多层结构：`user.{workContext, personalContext, topOfMind}` + `history.{recentMonths, earlierContext, longTermBackground}` + `facts[]`；debounced 队列；版本化 schema。**实现层换成 Postgres JSONB + pgvector** |
| **AioSandbox HTTP API 包装** `community/aio_sandbox/aio_sandbox.py` | threading.Lock 串行化 + ErrorObservation 自动重试模式，改造为 gVisor 容器 HTTP 控制 |
| **Config 4 层覆盖** `config/app_config.py` | global default → model variant → agent override → runtime（RunnableConfig.configurable）四层覆盖逻辑，与我们 manifest 兼容（修正之前"不复用"的误判）|
| **Sandbox ABC 接口契约** `sandbox/sandbox.py` | execute_command/read_file/write_file/glob/grep 作为 sandbox 接口契约采纳（实现替换为 gVisor，**接口不重新设计**）|
| **MCP 完整模块** `mcp/` 5 文件 | 不只是 client.py：ExtensionsConfig（多 server 管理）、stdio/SSE/HTTP 三种 transport、OAuth refresh 都借鉴 |
| **Skill 元数据 parser** `skills/parser.py` + `skills/types.py` | 零依赖 frontmatter 解析；作为 manifest 之外的"工作流模板"补充机制（M2 阶段考虑）|
| **Model Factory** `models/` | 各 provider 实现的 thinking_enabled / vision_enabled / fallback 切换逻辑 |

---

### ❌ 不复用的部分

| DeerFlow 模块 | 不用的原因 |
|---------------|-----------|
| `agents/lead_agent/agent.py` | Deep Research 特化的 14 中间件链 |
| `agents/lead_agent/prompt.py` | 业务相关 prompt |
| `sandbox/sandbox.py` ABC | 接口设计偏 DeerFlow，我们重新定义包含 tenant/quota 的接口 |
| `sandbox/local/` | macOS dev fallback 我们用更简单的 docker default runtime |
| `config/` | 整套配置系统（我们的 manifest 模型完全不同）|
| `client.py` | 嵌入式客户端（我们走 HTTP/SSE）|
| `community/{firecrawl,exa,jina_ai,ddg_search,tavily,serper,infoquest,image_search}` | 这些是搜索工具，我们通过 MCP 接入更标准 |
| `app/` 应用层（gateway、channels）| 与我们 control-plane 完全不同 |
| `frontend/` Next.js | 我们用 React 19 + Antd（已有技术栈）|
| `langgraph.json` 单图注册 | 我们的 Orchestrator 自己管理多 graph |

---

## Vendor 流程

### 1. 初次拷贝（M0 启动时）

```bash
# 在 Expert Work 仓库中
git submodule add https://github.com/bytedance/deer-flow.git vendor/deer-flow
cd vendor/deer-flow && git checkout <stable_commit_sha>
cd ../..

# 拷贝 P0 文件（脚本化）
python tools/vendor_sync.py --plan vendor/p0-files.yaml
```

### 2. 文件头注释规范

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

### 3. 季度同步

```bash
# 每季度跑一次
git -C vendor/deer-flow fetch origin
python tools/vendor_sync.py --check-upstream-changes \
  --report docs/vendor-sync-2026-q3.md
```

输出：列出 vendor 文件在上游的 diff，由人工 review 是否需要 cherry-pick。

---

## License 合规

- 所有 vendor 文件 = MIT（与 deer-flow 一致）
- 我们的 license 选择：建议 **Apache 2.0**（兼容 MIT vendor + 给商业使用更明确的专利授权）
- `LICENSE` 文件并列声明：
  ```
  Expert Work — Apache License 2.0
  Includes code adapted from:
  - bytedance/deer-flow (MIT) — see vendor/deer-flow/LICENSE
  - 其他第三方依赖见 NOTICE
  ```
- `NOTICE` 文件列出所有 vendor 来源
