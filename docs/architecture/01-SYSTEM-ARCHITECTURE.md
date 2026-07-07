# 01 系统架构

## 系统架构图

```
   ┌─────────────────┐  ┌──────────────────┐  ┌────────────────┐
   │  Admin UI       │  │ Developer CLI    │  │  External Apps │
   │ React 19+Antd   │  │ expert-work-cli   │  │ (业务系统)      │
   └────────┬────────┘  └─────────┬────────┘  └────────┬───────┘
            └─────────────┬───────┴──────────────────────┘
                          ▼ HTTPS
   ┌──────────────────────────────────────────────────────────┐
   │   Control Plane (FastAPI)                                 │
   │   Agent Registry │ Session Mgr │ Auth/RBAC │ Manifest    │
   └────────┬─────────────────────────────────────────────────┘
            ▼ asyncio (M0/M1) → gRPC (K8s)
   ┌──────────────────────────────────────────────────────────┐
   │   Orchestrator (LangGraph Runtime, 多 worker)             │
   │   Harness Loop:                                           │
   │     LLM Call ──► Provider Router (Anthropic/OpenAI)      │
   │     Tool Call ─► Dispatcher                              │
   │                  ├─► HTTP ─► [Credential Proxy]          │
   │                  ├─► MCP  ─► [MCP Gateway]               │
   │                  ├─► Sandbox ─► [Sandbox Pool]           │
   │                  └─► SubAgent ─► (递归 spawn subgraph)   │
   │     Checkpoint ► PostgresSaver (event_log + state)       │
   └────┬───────┬───────┬───────┬────────┬───────────────────┘
        ▼       ▼       ▼       ▼        ▼
   ┌──────┐ ┌──────┐ ┌──────┐ ┌────────┐ ┌──────────────┐
   │Sandbox│ │ Cred │ │ MCP  │ │ Memory │ │ Event Store  │
   │ Pool │ │ Proxy│ │Gateway│ │ Store  │ │ (Postgres)   │
   │Docker│ │Envoy │ │MCP SDK│ │pgvector│ │ append-only │
   │+gVisor│ │+Vault│ │      │ │        │ │ + checkpoints│
   └──────┘ └──────┘ └──────┘ └────────┘ └──────────────┘

   横切：OpenTelemetry → Tempo │ Prometheus → Grafana │ Loki │ Vault
```

## 组件职责矩阵

| 组件 | 职责 | 选型 | 关键 API | 自研行数估算 |
|------|------|------|---------|--------|
| **Control Plane** | CRUD agents/sessions、鉴权、限流、manifest 校验 | FastAPI + Pydantic v2 + SQLAlchemy 2.0 | `POST /v1/agents`、`POST /v1/sessions`、`POST /v1/sessions/{id}/runs`（SSE）| ~3000 |
| **Orchestrator** | 执行 LangGraph、推进 Harness loop | langgraph 0.2+ + langgraph-checkpoint-postgres | 内部 SDK：`Runner.run(spec, input, session_id)` | ~5000 |
| **Sandbox Pool** | 池化、生命周期、Docker+runsc 调度 | Docker SDK + gVisor (runsc) | gRPC：`Sandbox.Exec`、`Sandbox.WriteFile`、`Sandbox.Stream` | ~4000 |
| **Credential Proxy** | 出站 HTTP 凭证注入 | M0：自研 aiohttp；M1+：Envoy + Lua | HTTP CONNECT + Header 改写 | ~1500 |
| **MCP Gateway** | 多租户 MCP server 复用、连接池 | Anthropic 官方 `mcp` Python SDK | `mcp_gateway.call(tenant, server, tool, args, ctx)` | ~1200 |
| **Memory Store** | 长短期记忆 | Postgres + pgvector + asyncpg | `Memory.store/recall/forget/list` | ~800 |
| **Event Store** | append-only log + LangGraph checkpoint | Postgres + langgraph-checkpoint-postgres | `EventLog.append/replay/snapshot` | ~500（vendor DeerFlow）|
| **Admin UI** | Agent/Session/Sandbox 管理 | React 19 + Vite + Antd（已有）| 调 Control Plane REST | 复用 |

**修正后总自研行数**：~12K（含 vendor DeerFlow 模块 ~2500 行 + 借鉴重写 ~1500 行）

---

## Event Log 表结构（关键基础）

> **权威 DDL 见 [ADR-0002](../adr/0002-state-layer-schema.md)**（含 `thread_id` + `tenant_id UUID` + `trace_id` 等扩展字段）。下面是 M0 早期高层示意，实际 schema 落地以 ADR-0002 为准；`audit_log` 表是分离的，DDL 同样在 ADR-0002。

```sql
-- 概念示意（非权威）
CREATE TABLE event_log (
  id          BIGSERIAL PRIMARY KEY,
  thread_id   UUID NOT NULL,
  tenant_id   UUID NOT NULL,
  seq         BIGINT NOT NULL,
  event_type  TEXT NOT NULL,   -- llm_call|tool_call|tool_result|state|error|checkpoint
  payload     JSONB NOT NULL,  -- 已经 PII redactor 处理
  trace_id    TEXT,             -- W3C
  created_at  TIMESTAMPTZ DEFAULT now(),
  UNIQUE (thread_id, seq)
);

-- DB 角色禁 UPDATE/DELETE，强制 append-only
-- 每 50 events 写一个 LangGraph checkpoint 加速冷启动
-- vendor 自 deer-flow runtime/events/store/db.py（FOR UPDATE 单锁分配 seq）
```

### Event Log 设计要点（vendor 自 DeerFlow `runtime/events/store/db.py`）

- **seq 严格单调**：用 `SELECT max(seq) FOR UPDATE` 串行分配，UNIQUE 约束兜底
- **批量写优化**：`put_batch()` 单次锁 + 整批分配 seq，高并发友好
- **内容截断**：trace 类事件超过阈值自动截断，防 JSONB 爆炸
- **双向游标分页**：支持 `before_seq` / `after_seq`，前端时间线 UI 友好

---

## 数据流（Session 生命周期）

```
1. 创建 Session
   POST /v1/sessions
     ↓
   Control Plane:
     - 鉴权（JWT + tenant 解析）
     - 加载 AgentSpec from Registry
     - 创建 thread_meta 行（with tenant_id）
     - 分配 sandbox handle（从 warm pool）
     - 写 event_log: { type: "session_start", payload: {...} }
   返回 session_id

2. 执行 Run
   POST /v1/sessions/{id}/runs   (SSE)
     ↓
   Orchestrator (LangGraph):
     for step in graph.astream(input, config={"configurable": {"thread_id": session_id}}):
       - LLM call → 写 event_log: { type: "llm_call", payload: {...} }
       - Tool dispatch:
           HTTP → Credential Proxy → 写 event_log: { type: "tool_call/result" }
           MCP  → MCP Gateway      → 写 event_log
           Sandbox → exec_python   → 写 event_log（含 stdout/stderr）
           SubAgent → spawn subgraph → 递归生成 sub-session
       - State update → checkpoint 到 PostgresSaver
       - SSE chunk 推给客户端: { event: "token|tool_call|tool_result|thinking|final" }

3. 故障恢复
   重启 Orchestrator → 从最近 checkpoint replay → 继续从中断处执行

4. 终止 Session
   - graceful shutdown sandbox
   - 写 event_log: { type: "session_end" }
   - 长期保留 event_log（审计/replay/eval）
```

---

## 与 Docker 单机部署的对接

### docker-compose 服务清单

```yaml
# deploy/docker-compose/dev.yml
services:
  postgres:
    image: pgvector/pgvector:pg16
    volumes: [pgdata:/var/lib/postgresql/data]

  vault:
    image: hashicorp/vault:1.18

  redis:                              # 限流 + 任务队列
    image: redis:7-alpine

  control-plane:
    build: ./services/control-plane
    depends_on: [postgres, vault, redis]
    ports: ["8000:8000"]

  orchestrator:
    build: ./services/orchestrator
    depends_on: [postgres, redis, sandbox-supervisor]
    deploy: { replicas: 2 }

  sandbox-supervisor:
    build: ./services/sandbox-supervisor
    privileged: true                  # 能调用 docker / runsc
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    environment:
      - SANDBOX_RUNTIME=runsc

  credential-proxy:
    build: ./services/credential-proxy
    depends_on: [vault]
    ports: ["8081:8081"]

  mcp-gateway:
    build: ./services/mcp-gateway

  admin-ui:
    build: ./services/admin-ui
    ports: ["3000:80"]

  otel-collector: { image: otel/opentelemetry-collector }
  prometheus:     { image: prom/prometheus }
  grafana:        { image: grafana/grafana }
  loki:           { image: grafana/loki }

volumes:
  pgdata: {}
```

---

## 扩展点（K8s 迁移时哪些会变）

| 组件 | Docker 单机 | K8s 模式 | 接口稳定性 |
|------|--------------|----------|-----------|
| `sandbox-supervisor` | privileged 容器 + 主机 docker.sock | Operator + RuntimeClass=gvisor 的 Pod | gRPC API 不变 |
| `orchestrator` | docker compose replicas | Deployment + HPA | 内部 SDK 不变 |
| `credential-proxy` | 中心化 Envoy | Sidecar 注入 + SPIFFE/SPIRE 身份 | HTTP CONNECT 协议不变 |
| Secrets | Vault 静态拉 | ExternalSecrets + Vault Agent Injector | Vault API 不变 |
| 网络 | Docker iptables 规则 | NetworkPolicy + Cilium | 策略 DSL 重写 |

---

## 关键架构约束

1. **LangGraph 接触限制**：所有 LangGraph 接触限制在 `services/orchestrator/graph_builder/` 一处目录；`AgentSpec` 是引擎自有 schema 不暴露 LangGraph 类型，保留逃生通道
2. **gVisor 平台限制**：不支持 macOS prod 环境；dev 用 OrbStack/Lima/Linux VM；prod 必须 Linux
3. **Sandbox 单机水位**：单机 100-200 sandbox 后 docker daemon 成瓶颈，70% 水位告警
4. **Manifest 签名**：生产 manifest 强制 admin 签名（cosign）+ CI 校验，防误改全租户故障
5. **Event log append-only 强制**：DB 角色 `expert_work_app` 禁 UPDATE/DELETE on event_log 表
