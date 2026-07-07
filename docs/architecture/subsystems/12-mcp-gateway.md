# 12 MCP Gateway

> 多租户 **MCP server 连接池** + **allow_tools 过滤** + **OAuth 刷新**。MCP 工具是 Expert Work 主要的能力扩展机制；本子系统是其稳定性与隔离性兜底。
>
> **M0 调用方拓扑**：MCP Gateway 由 **orchestrator 进程**调用（不是 sandbox 内业务代码直接访问）；这影响 [21 网络策略](./21-network-policy.md) allowlist —— sandbox 不需要直达 MCP Gateway 的网络放行。

---

## 1. 职责 & 边界

### ✅ 做
- **三种 transport**：stdio / SSE / streamable-HTTP（基于 Anthropic 官方 `mcp` Python SDK）
- **per-tenant 连接池**：每租户独立连接到同一 MCP server（避免身份串号）
- **tool allow-list 过滤**：MCP server 暴露 100 个工具，manifest 只允许 5 个 → 在 `tools/list` 响应后过滤，**不让 LLM 看见多余的**
- **健康检查**：定期 `tools/list` ping（默认 30s），失败 → 标记 unhealthy + 触发重连
- **认证刷新**：MCP server OAuth token / API key 走 [11 Credential Proxy](./11-credential-proxy.md) 注入，过期前主动 refresh
- **超时 & 重试**：每次工具调用独立超时；失败按错误类型分类重试或 fast-fail
- **错误标准化**：MCP server 千奇百怪的错误格式 → 统一 `ToolErrorReply` 给 orchestrator
- **指标埋点**：每次工具调用都有 metric + trace

### ❌ 不做
- **不发现** MCP server（不做注册中心；server 端点由 manifest 显式声明，M2 才做 marketplace）
- **不缓存**工具结果（属于 orchestrator middleware 层，gateway 永远透传）
- **不写**业务逻辑（gateway 不解析 tool 输出语义）
- **不实现** stdio MCP server 的进程管理（M0 仅支持 SSE / streamable-HTTP；stdio 推到 M1）

---

## 2. 上下游依赖

```
        Orchestrator (LangGraph ToolNode)
                │
                │ tool.invoke({name, args})
                ▼
        ┌─────────────────────────┐
        │   MCP Gateway           │
        │   (此子系统)              │
        └──┬─────┬──────┬────┬────┘
           │     │      │    │
           │     │      │    └─→ [17 Audit Log]   工具调用审计
           │     │      └─────► [20 Observability] trace/metric
           │     └────────────► [11 Credential Proxy] 取 MCP server token
           │
           ▼
       MCP Servers (tenant-x 配置的)
       ├─ gitlab-mcp (SSE)
       ├─ slack-mcp (streamable-HTTP)
       └─ ...
```

调用关系：
- **M0 调用方 = orchestrator 进程**（关键决策）：orchestrator 通过本 gateway 提供的 `MCPToolNode`（LangGraph 节点封装）发起 MCP 调用；**sandbox 内业务代码不直接访问 MCP Gateway**，影响 [21 § 5 网络 allowlist](./21-network-policy.md)（sandbox → MCP Gateway 不放行）
- **manifest 加载** 时由 control plane 调用 `register_tenant_servers()` 注册 server 列表
- **gateway 自身** 通过 [11 Credential Proxy] 的 `/forward` 接口转发 MCP 协议消息（secret 由 proxy 注入）

---

## 3. 数据模型 / 状态机

### 3.1 Postgres DDL — server 配置 / 连接池状态 / 工具调用记录

```sql
-- MCP server 配置（来自 manifest 的 tools[].mcp，control plane 注册）
CREATE TABLE mcp_server (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    name            TEXT NOT NULL,            -- "gitlab-mcp"
    transport       TEXT NOT NULL,            -- sse / streamable_http / stdio
    endpoint        TEXT NOT NULL,            -- URL or stdio command
    auth_secret_ref TEXT,                     -- 引用 [11 Credential Proxy] 的 ref
    health_check_interval_s INT NOT NULL DEFAULT 30,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, name)
);

-- agent 维度的 allow_tools 配置（来自 manifest）
CREATE TABLE mcp_tool_allowlist (
    tenant_id      TEXT NOT NULL,
    agent_name     TEXT NOT NULL,
    agent_version  TEXT NOT NULL,
    server_name    TEXT NOT NULL,
    tool_name      TEXT NOT NULL,
    PRIMARY KEY (tenant_id, agent_name, agent_version, server_name, tool_name)
);
CREATE INDEX mcp_tool_allowlist_lookup ON mcp_tool_allowlist (tenant_id, agent_name, agent_version);

-- 工具调用记录（持久化，便于审计 + 调试）
CREATE TABLE mcp_tool_call (
    id             BIGSERIAL PRIMARY KEY,
    tenant_id      TEXT NOT NULL,
    agent_name     TEXT NOT NULL,
    session_id     UUID,
    run_id         UUID,
    server_name    TEXT NOT NULL,
    tool_name      TEXT NOT NULL,
    args_hash      TEXT NOT NULL,             -- 不存明文 args（PII 风险）
    status         TEXT NOT NULL,             -- ok / timeout / error / denied
    error_code     TEXT,
    duration_ms    INT,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX mcp_tool_call_session ON mcp_tool_call (session_id);
CREATE INDEX mcp_tool_call_tenant_time ON mcp_tool_call (tenant_id, started_at DESC);
```

### 3.2 Pydantic Schema — 内部模型

```python
# packages/expert-work-protocol/src/Expert Work/protocol/mcp.py
from pydantic import BaseModel
from typing import Literal

class MCPServerConfig(BaseModel):
    tenant: str
    name: str
    transport: Literal["sse", "streamable_http", "stdio"]
    endpoint: str
    auth_secret_ref: str | None = None
    health_check_interval_s: int = 30

class MCPToolDescriptor(BaseModel):
    server_name: str
    tool_name: str
    description: str
    input_schema: dict                        # JSON Schema
    # cache_control: 由 [10 LLM Gateway] prompt cache 策略决定，gateway 不在此处处理

class MCPToolInvocation(BaseModel):
    tenant: str
    agent: str
    agent_version: str
    session_id: str
    run_id: str
    server_name: str
    tool_name: str
    args: dict
    timeout_s: int = 30

class ToolReply(BaseModel):
    status: Literal["ok", "error", "timeout", "denied"]
    content: list[dict] = []                  # MCP 标准 content 数组
    error_code: str | None = None
    error_message: str | None = None
```

### 3.3 连接池状态机（per-(tenant, server)）

```
     ┌──────────────┐
     │ INITIALIZING │  启动 MCP session（initialize/initialized handshake）
     └──────┬───────┘
            │ ok
            ▼
     ┌──────────────┐  health check 失败 N 次
     │   READY      │ ───────────────────────► UNHEALTHY
     └──────┬───────┘                              │
            │ explicit close                        │ exponential backoff
            ▼                                      ▼
     ┌──────────────┐                       ┌──────────────┐
     │   CLOSED     │ ◄──────────────────── │ RECONNECTING │
     └──────────────┘    永久失败             └──────────────┘
```

---

## 4. 关键接口

### 4.1 LangGraph ToolNode 封装（业务侧不直接调）

```python
# services/mcp-gateway/src/mcp_gateway/tool_node.py
from langgraph.prebuilt import ToolNode
from expert_work.protocol.mcp import MCPToolDescriptor

class MCPToolNode(ToolNode):
    """把一组 MCP 工具暴露为 LangGraph ToolNode."""

    def __init__(self, tenant: str, agent: str, version: str,
                 allowed_tools: list[MCPToolDescriptor],
                 gateway: "MCPGateway"):
        ...

    async def _ainvoke_tool(self, name: str, args: dict, ctx) -> ToolReply: ...
```

### 4.2 Python Gateway API（orchestrator 编排时调用）

```python
# services/mcp-gateway/src/mcp_gateway/gateway.py
class MCPGateway:
    async def list_tools_for_agent(
        self, tenant: str, agent: str, version: str
    ) -> list[MCPToolDescriptor]:
        """聚合该 agent 配置的所有 server 的工具，按 allowlist 过滤后返回."""

    async def invoke(self, inv: MCPToolInvocation) -> ToolReply: ...

    async def register_server(self, cfg: MCPServerConfig) -> None: ...
    async def deregister_server(self, tenant: str, name: str) -> None: ...
    async def get_health(self, tenant: str) -> dict[str, str]: ...
```

### 4.3 内部管理 HTTP API（control plane 调用）

| Method | Path | 说明 |
|---|---|---|
| POST | `/v1/servers` | 注册 / 更新 MCP server |
| DELETE | `/v1/servers/{tenant}/{name}` | 注销 |
| POST | `/v1/allowlist` | 写入 (tenant, agent, version) 的 tool allowlist（manifest 加载时） |
| GET | `/v1/health/{tenant}` | 各 server 健康状态 |

---

## 5. 算法 / 关键决策

### 5.1 连接池策略（**关键决策**）

- **池粒度** = `(tenant, server_name)`：避免跨租户复用同一连接（防止 server 侧鉴权漂移）
- **每池连接数**：M0 = 1 持久连接（SSE 多路复用），M1 = 1-N 动态扩缩
- **连接复用**：SSE / streamable-HTTP 都是长连接 + JSON-RPC 多路复用，**单连接已能并发**
- **连接生命周期**：MCP 协议要求 `initialize → initialized` handshake；gateway 启动连接后缓存初始化结果（含 server 暴露的工具列表）
- **空闲回收**：默认 600s 无调用 → 标记 idle，下次调用懒重连（M1 才做主动关闭）

> 设计参考 [vendor 06](../06-OPEN-SOURCE-DEPS.md) DeerFlow `mcp/client.py`，但**去除 langchain 依赖**，直接基于 Anthropic 官方 mcp Python SDK 构建。

### 5.2 allow_tools 过滤策略（**关键决策**）

```python
async def list_tools_for_agent(...):
    raw = []
    for server in servers_of(tenant, agent, version):
        all_tools = await pool.get(server).session.list_tools()
        raw.extend((server.name, t) for t in all_tools.tools)

    allow = await load_allowlist(tenant, agent, version)
    filtered = [
        t for (s, t) in raw
        if (s, t.name) in allow
    ]
    return filtered
```

**两个核心约束**：
1. **过滤发生在 gateway 内**，LLM **永不**看到非 allowlist 工具——这是安全边界，不能下沉到 LLM prompt 层
2. **manifest lint 阶段**（[02 Manifest](../02-AGENT-MANIFEST.md) 静态校验）已经核对 allow_tools 全部存在于 server 工具集；运行时若 server 删了某工具，allowlist 命中失败 → 回退 `denied`

### 5.3 健康检查算法

```
每 30s 对 (tenant, server) 池：
  1. 已 READY → 发 tools/list ping
     - 成功：reset failure_count
     - 失败：failure_count++
       - failure_count >= 3 → state=UNHEALTHY，触发重连
  2. UNHEALTHY → 指数退避 (1s, 2s, 4s, ..., 60s 上限) 重连
     - initialize 成功 → READY
     - 失败 → 继续退避
  3. 在 UNHEALTHY 期间 invoke：
     - 若是 idempotent 类（read-only 标注）：立即重试连接 1 次再调
     - 否则：返回 ToolReply{status=error, error_code="server_unhealthy"}
```

### 5.4 OAuth / API key 注入路径（**关键决策：通过 Credential Proxy `/forward` 转发**）

MCP Gateway 启动 / 维持 MCP session 时**不直接读 Vault、不直接持有 secret 明文**，而是把 MCP 协议消息（initialize、tools/list、tools/call 等 JSON-RPC payload）通过 [11 Credential Proxy](./11-credential-proxy.md) 的 `/forward` 接口送出；proxy 在出站链路上注入 OAuth token / API key 等凭证，再转发到真实 MCP server。

```
MCP Gateway              [11 Credential Proxy] /forward             MCP server
─────────────            ──────────────────────────────             ──────────
JSON-RPC payload  ──►    inject Authorization (per secret_ref) ──►  receive
                          (按 server.auth_secret_ref 注入)
```

要点：
- gateway 进程**永远不持有 secret 明文**；只持有 `auth_secret_ref`（mcp_server.auth_secret_ref 字段）
- 收到 401 / OAuth refresh 标记 → 调 proxy 的 `/admin/cache/invalidate` 失效该 ref → 下次 `/forward` 自动取新 token → 重连
- 不在 gateway 内做 OAuth flow（PKCE 等）；**全部交由 Vault dynamic secret 后端处理**（M1）
- 不依赖任何"读 secret 明文"的 admin API（11 也明确不暴露此类接口）

### 5.5 工具调用超时（**关键决策**）

| 层级 | 默认 | 来源 |
|---|---|---|
| LLM 单次 tool_use 总超时 | 30s | manifest.tools[].timeout_s |
| MCP gateway invoke 内层 | timeout - 1s | 留 1s 给序列化/审计 |
| HTTP / SSE 底层超时 | 5s | 连接级，独立于业务超时 |

超时后行为：取消上游请求（mcp SDK 支持 cancel）+ 返回 `ToolReply{status=timeout}`；**不重试**（避免重复副作用）。

---

## 6. 失败模式 & 缓解

| 失败模式 | 触发场景 | 影响 | 缓解 |
|---|---|---|---|
| MCP server 完全不可达 | 网络 / 进程挂 | 该 server 所有工具失败 | 健康检查标 UNHEALTHY；返回结构化 error；指数退避重连 |
| MCP server 响应慢 | 上游慢 SQL | tool 调用堆积 | 严格 timeout；per-(tenant,server) 并发上限（默认 50）超出 fast-fail |
| 工具被偷偷删除 | server 升级 | allowlist 命中失败 | 返回 `denied` + 标记需要重新 lint manifest；告警 |
| 凭证过期 | OAuth token expire | 401 | gateway 失效 cache → 重取 → 重试一次；仍失败标 UNHEALTHY |
| 跨租户串号（同一 SSE 连接被多租户共享） | 池粒度配错 | 严重隔离破坏 | 强制 per-(tenant, server) 池；invariant 单元测试每 release 必跑 |
| MCP server 返回畸形 JSON-RPC | server bug | 解析失败 | 严格按 JSON-RPC 2.0 校验；不合法直接 error_code=`malformed_response` |
| stdio MCP 进程僵尸 | 子进程退出未回收 | 资源泄漏 | M1 实现 stdio supervisor + healthchecks（M0 不支持 stdio） |
| 工具列表过大 | server 暴露 100+ 工具 | LLM prompt 爆炸 | allowlist 强制；同时通过 [deferred_tool_filter middleware] 二级过滤（参见 [vendor 06] P1 中间件） |
| 工具响应过大 | server 返回 10MB | LLM context 爆 | gateway 强制 max_response_bytes（默认 256KB）；超出截断并标记 `truncated=true` |
| 重复调用 / 重放 | retries 触发副作用 | 数据重复 | gateway 不内置幂等（业务责任）；但生成 idempotency_key=hash(args+session+turn) 透传到 server 供其使用 |

---

## 7. 可观测性

> 日志完整字段遵循 [20 § 5.3](./20-observability.md)，此处仅展示子系统专属字段。
> Metric / span 命名遵循 [20 § 5.x 命名规范](./20-observability.md)：metric 统一 `expert_work_*` 前缀（snake_case），span 统一 `expert_work.{component}.{action}`。

### Prometheus metrics

```
expert_work_mcp_gateway_connections{tenant,server,state}                       gauge
expert_work_mcp_gateway_invoke_total{tenant,agent,server,tool,status}
expert_work_mcp_gateway_invoke_duration_seconds{tenant,server,tool}            histogram
expert_work_mcp_gateway_health_check_total{tenant,server,status}
expert_work_mcp_gateway_reconnects_total{tenant,server,reason}
expert_work_mcp_gateway_tools_filtered_total{tenant,agent,reason}              counter
  # reason ∈ allowlist_miss / server_removed / disabled
expert_work_mcp_gateway_response_truncated_total{tenant,server,tool}           counter
```

### OTel spans

```
expert_work.mcp_gateway.invoke
├── attrs: tenant, agent, server, tool, status, duration_seconds
├── span: expert_work.mcp_gateway.allowlist_check
├── span: expert_work.mcp_gateway.connection_acquire
├── span: expert_work.mcp_gateway.jsonrpc_call (传 W3C Trace Context 给 server)
└── span: expert_work.audit.write
```

### 日志（每条 invoke 对应 1 条结构化 log）

```json
{
  "trace_id": "...", "tenant": "tenant-x", "agent": "code-reviewer-agent",
  "server": "gitlab-mcp", "tool": "read_pr",
  "status": "ok", "duration_seconds": 0.412,
  "args_hash": "sha256:...", "response_size": 8192
}
```

---

## 8. 安全考虑

| 攻击面 | 防御 |
|---|---|
| 跨租户连接复用 | per-(tenant, server) 池；启动期 invariant 测试 |
| MCP server 拿到错误租户的请求 | 请求带 `X-Expert-Work-Tenant` header；server 侧用此做二次校验（manifest 强制） |
| 恶意 server 注入 prompt | server 返回的 content 经 [orchestrator output filter middleware] 处理；gateway 仅截断不解析语义 |
| 工具偷换（server 把 read_file 替换成 write_file） | allowlist 是 (server, tool_name)；语义偷换属于 server 上游攻击，由 [18 Manifest 供应链] cosign 签名约束 |
| stdio MCP 命令注入 | M0 不支持 stdio；M1 支持时仅允许 manifest 声明的 path，不允许 shell 字符串 |
| 工具结果含 PII | gateway 透传，由 [17 Audit Log] PII redactor 处理 log 字段；明文进 event_log（受租户 pii_fields 自动 redact） |
| MCP server outbound 网络 | gateway 自身 outbound 经 [21 Network Policy] allowlist；不依赖 sandbox 网络层 |
| auth token 泄漏到日志 | header 在写日志前 redact；只 log secret_ref 不 log token |
| 重放工具调用 | gateway 不防重放（业务侧职责）；但记录 idempotency_key 给 server 用 |

---

## 9. M0 / M1 / M2 演进

### M0 — MVP（单租户单 server）
- transport：仅 SSE + streamable-HTTP（**stdio 推到 M1**，进程管理复杂）
- 1 个 MCP server / 租户（多 server 配置可写但只跑 1 个，验证连接池正确性）
- allow_tools 过滤
- 健康检查（30s 轮询）
- 同步 OAuth / API key（[11 Credential Proxy] 静态注入）
- mcp_tool_call 审计入库

### M1 — per-tenant 连接池
- 每租户多 server，池粒度 (tenant, server)
- 动态连接数（1-N）
- stdio transport 支持 + 子进程 supervisor
- OAuth refresh（dynamic secret，与 [11] 联动）
- per-(tenant, server) 并发限制
- vendor `mcp/client.py` 借鉴的多 server 工厂

### M2 — 第三方 marketplace
- MCP server 注册中心（公开 + 内部市场）
- server 自动发现（DNS-SD / Consul）
- server 评分 / 调用统计 / SLO 大盘
- 签名校验（marketplace server 必须有 publisher 签名）

### M3 — 跨集群
- region-local server 优先；跨 region 白名单
- A2A 协议互通（agent 调用其他 agent 的 MCP server）

---

## 10. 开放问题

1. **streamable-HTTP vs SSE 选哪个为主：** Anthropic 官方主推 streamable-HTTP，但很多现有 server 还在用 SSE。M0 都支持，长期看 streamable-HTTP 更稳。
2. **per-(tenant, server) 还是 per-(tenant, agent, server)：** 当前 per-(tenant, server)；如果同租户多 agent 调用同 server 行为差异大，是否分池？倾向**保持当前粒度**，差异化通过 server 端 header 区分。
3. **工具结果 streaming：** MCP 协议支持 streaming content；orchestrator 是否支持透传给 LLM？M2 议题。
4. **stdio MCP 在 sandbox 内 vs gateway 进程内：** stdio MCP server 启在 sandbox 内（隔离）还是 gateway 进程内（管理简单）？倾向 sandbox 内，但需要 sandbox 与 gateway 间桥接。
5. **deferred_tool_filter 中间件与本子系统的边界：** allowlist 是静态过滤（manifest 配置），deferred_tool_filter 是运行时 LLM 工具数量优化。本子系统只做前者，后者属于 orchestrator middleware 层。
