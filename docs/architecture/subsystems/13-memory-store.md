# 13 Memory Store

> Agent 记忆分层：**短期**（LangGraph PostgresSaver checkpoint，自动）+ **长期**（pgvector RAG，显式）。本子系统聚焦后者：分层语义、写入队列、向量索引、严格 tenant scope。
>
> **M0 网络拓扑（关键决策）**：业务侧 `MemoryClient` 通过 sandbox supervisor 提供的 **unix domain socket** 转发到 orchestrator 进程；**sandbox 内业务代码不直接持有 memory store 的数据库连接**，embed + insert/search 全部在 orchestrator 端执行（详见 § 2、§ 4.1）。这与 [21 网络策略](./21-network-policy.md) 的 sandbox egress allowlist 保持一致。
>
> **上下文压缩（summarization）**：本子系统**不**定义压缩算法/触发条件/接口；详见 [27 上下文压缩](./27-context-compression.md)（横切 middleware 层）。本子系统的 `history` 层只是被 27 调用的写入目的地。

---

## 1. 职责 & 边界

### ✅ 做
- **短期记忆**：透明集成 LangGraph PostgresSaver（vendor checkpointer/provider），无需用户感知
- **长期记忆**：显式 API `memory.put / memory.search / memory.delete`，落盘 Postgres + pgvector
- **分层语义**：work / personal / topOfMind / history / facts 五层（参考 vendor DeerFlow `agents/memory/storage.py`）
- **写入队列**：debounced batch（5s 或 100 items 触发 flush），避免热路径阻塞
- **向量索引**：HNSW（m=16, ef_construction=64）作为默认；GIN 倒排索引覆盖 metadata 过滤
- **强 tenant scope**：所有 search/put/delete 必须强制 `WHERE tenant = ?`；越权返回空集
- **embedding 抽象**：默认 `text-embedding-3-large`，manifest 可覆盖；统一经 [10 LLM Gateway](./10-llm-gateway.md) 路由
- **版本化**：每条 memory_item 带 version，支持后续重新嵌入（embedding 模型升级）

### ❌ 不做
- **不做** RAG 业务编排（chunk/rerank 是上层职责，本子系统只提供 KV-with-vector 抽象）
- **不做** 跨 session 知识自动沉淀（M2 议题；M0 仅手动 put）
- **不做** 模糊语义合并 / 去重（业务侧职责）
- **不替换** LangGraph 的 short-term checkpoint（直接 vendor，不重写）

---

## 2. 上下游依赖

```
   sandbox (业务代码: ctx.memory.put / search / delete)
            │
            │ M0：unix domain socket（sandbox supervisor 中转）
            ▼
   Orchestrator (LangGraph node + MemoryService)
            │
            │ embed + insert/search 在此进程执行
            ▼
        ┌──────────────────────────┐
        │   Memory Store           │
        │   (此子系统)              │
        │                           │
        │   ┌──────────┐            │
        │   │ short:   │            │
        │   │ checkpt  │ ──► Postgres (langgraph_checkpoints / writes)
        │   └──────────┘            │
        │   ┌──────────┐            │
        │   │ long:    │ ──► [10 LLM Gateway]   embed
        │   │ vector   │ ──► Postgres + pgvector
        │   │ + queue  │            │
        │   └──────────┘            │
        └──────────────────────────┘
```

调用关系：
- **M0 网络路径**：sandbox 内业务代码通过 `MemoryClient` SDK 调用 `put/search/delete`；SDK 实际把请求经 sandbox supervisor 提供的 **unix domain socket** 转发到 orchestrator 进程。**sandbox 不直连 Postgres / pgvector，不直连 [10 LLM Gateway]**。
- **orchestrator** 端的 MemoryService 持有 DB 连接，执行 embed + insert/search；同进程通过 `AgentContext.memory` 直接访问
- **embedding** 调用走 [10 LLM Gateway]（不绕过 quota）
- **manifest** 的 `memory.long_term.collection` 决定隔离粒度（默认 `tenant_${tenant}_${name}`）
- **summarization / context 压缩**：不在本子系统职责内；由 [27 上下文压缩](./27-context-compression.md) 触发后将摘要结果写入本子系统 `history` 层

---

## 3. 数据模型 / 状态机

### 3.1 Postgres DDL

```sql
CREATE EXTENSION IF NOT EXISTS vector;

-- 短期记忆 = LangGraph 自动管理（vendor checkpointer/provider）
-- 表：langgraph_checkpoints / langgraph_checkpoint_writes / langgraph_checkpoint_blobs
-- 不在此重复 DDL，参考 vendor

-- 长期记忆主表
-- 注：DB 列名统一为 tenant_id（与全库一致）；Pydantic 层保留 tenant 字段（见 § 3.2）
CREATE TABLE memory_item (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       TEXT        NOT NULL,
    agent_name      TEXT        NOT NULL,
    session_id      UUID,                              -- nullable: tenant/agent 全局记忆 session_id IS NULL
    layer           TEXT        NOT NULL,              -- work / personal / top_of_mind / history / facts
    key             TEXT        NOT NULL,              -- 业务定义，如 "user.preferences.tone"
    content_text    TEXT        NOT NULL,
    content_vector  vector(3072),                      -- text-embedding-3-large 维度
    embed_model     TEXT        NOT NULL,              -- 记录嵌入模型版本，支持后续 reembed
    metadata        JSONB       NOT NULL DEFAULT '{}',
    version         INT         NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ,                       -- TTL（NULL = 永久）
    UNIQUE (tenant_id, agent_name, COALESCE(session_id::text,''), layer, key, version)
);

-- 关键索引
CREATE INDEX memory_item_tenant_agent_layer ON memory_item (tenant_id, agent_name, layer);
CREATE INDEX memory_item_metadata_gin ON memory_item USING GIN (metadata jsonb_path_ops);
CREATE INDEX memory_item_expires ON memory_item (expires_at) WHERE expires_at IS NOT NULL;

-- HNSW 向量索引（M0 即建立）
CREATE INDEX memory_item_vector_hnsw ON memory_item
USING hnsw (content_vector vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- Postgres RLS 强制 tenant scope（[15 AuthN/AuthZ] 配合，统一会话变量名 app.tenant_id）
ALTER TABLE memory_item ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON memory_item
    USING (tenant_id = current_setting('app.tenant_id')::text);

-- 写入队列（debounced batch 失败时回退落库）
CREATE TABLE memory_write_queue (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    agent_name      TEXT NOT NULL,
    payload         JSONB NOT NULL,            -- 待写 item
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending / processing / failed
    attempts        INT NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX memory_write_queue_dispatch ON memory_write_queue (status, next_attempt_at);
```

### 3.2 Pydantic Schema

```python
# packages/expert-work-protocol/src/Expert Work/protocol/memory.py
from pydantic import BaseModel, Field
from typing import Literal, Any
from datetime import datetime

Layer = Literal["work", "personal", "top_of_mind", "history", "facts"]

class MemoryItem(BaseModel):
    tenant: str
    agent: str
    session_id: str | None = None              # None = tenant/agent 全局
    layer: Layer
    key: str
    content_text: str
    metadata: dict[str, Any] = {}
    expires_at: datetime | None = None

class MemoryItemRecord(MemoryItem):
    id: int
    version: int
    embed_model: str
    created_at: datetime
    updated_at: datetime
    score: float | None = None                 # search 命中时的相似度

class SearchQuery(BaseModel):
    tenant: str
    agent: str
    query: str
    layer: Layer | list[Layer] | None = None   # None = 全部层
    session_id: str | None = None              # None = 不限定 session
    metadata_filter: dict[str, Any] = {}
    top_k: int = 5
    min_score: float = 0.6                     # cosine 相似度下限
```

### 3.3 写入流程状态机（**PII redact 在 embed 之前**）

```
[memory.put(item)]
    │
    ▼
[validate metadata]   校验 metadata 不含 tenant_config.pii_fields 中列出的字段（违反 → 拒绝）
    │
    ▼
[PII redact content_text]   按 tenant pii_fields 配置 redact content_text；redact 在 embed 之前，避免敏感原文进入 vector
    │
    ▼
[in-process buffer]   按 (tenant_id, agent) 分桶
    │
    │ flush 触发 = (≥100 items 或 ≥5s 或 进程关闭信号)
    ▼
[batch embed]   一次性调 [10 LLM Gateway] embedding API（节省 RTT）；输入为 redacted content
    │
    │ 成功
    ▼
[batch insert into memory_item]
    │
    │ 失败（DB 异常）
    ▼
[fallback: insert into memory_write_queue]
    │
    │ 后台 worker 每 30s 拉 pending → 重试，指数退避，max attempts = 5
    ▼
[done / dead-letter]
```

---

## 4. 关键接口

### 4.1 Python SDK（业务侧使用）

> **M0 网络路径**：`MemoryClient` 在 sandbox 内不直连 DB / [10 LLM Gateway]；所有方法实际通过 sandbox supervisor 暴露的 unix domain socket（路径如 `/run/Expert Work/memory.sock`）转发到 orchestrator 进程，由 orchestrator 端的 MemoryService 完成 PII redact + embed + insert/search。这一约束由 [21 网络策略](./21-network-policy.md) 强制（sandbox egress 不放行 DB / LLM Gateway 直连）。

```python
# packages/expert-work-sdk/src/Expert Work/sdk/memory.py
from expert_work.protocol.memory import MemoryItem, SearchQuery, MemoryItemRecord, Layer

class MemoryClient:
    async def put(
        self,
        layer: Layer,
        key: str,
        content: str,
        *,
        metadata: dict | None = None,
        session_scope: bool = False,            # True = 仅本 session；False = agent 全局
        ttl_s: int | None = None,
    ) -> None:
        """放入写队列，立即返回。"""

    async def search(
        self,
        query: str,
        *,
        layer: Layer | list[Layer] | None = None,
        top_k: int = 5,
        metadata_filter: dict | None = None,
        min_score: float = 0.6,
    ) -> list[MemoryItemRecord]:
        """同步检索；强制 tenant + agent scope。"""

    async def delete(self, key: str, *, layer: Layer | None = None) -> int: ...

    async def flush(self) -> None:
        """强制 flush 写队列（用于 graceful shutdown / 测试）"""
```

### 4.2 短期记忆（LangGraph 透明集成）

业务侧**无需调用**，由 GraphBuilder 注入 PostgresSaver；运行时自动持久化每个 node 的 state。

```python
# packages/expert-work-runtime/src/Expert Work/runtime/checkpointer/factory.py
from langgraph.checkpoint.postgres import PostgresSaver

def build_checkpointer(spec: AgentSpec) -> PostgresSaver:
    """读取 spec.memory.short_term.window 决定保留多少 turn。"""
    saver = PostgresSaver(...)
    saver.setup()  # 创建 langgraph_checkpoints 表
    return saver
```

### 4.3 内部管理 API（admin / 运维）

| Method | Path | 说明 |
|---|---|---|
| GET | `/v1/memory/{tenant}/{agent}/stats` | 统计 item 数 / 占用空间 / 各 layer 分布 |
| POST | `/v1/memory/{tenant}/reembed` | 触发批量重嵌入（embedding 模型升级时） |
| POST | `/v1/memory/{tenant}/cleanup` | 立即清理过期 (expires_at < now) item |
| DELETE | `/v1/memory/{tenant}` | 租户全删（GDPR Article 17 配合 [17 Audit Log]） |

---

## 5. 算法 / 关键决策

### 5.1 分层语义（**关键决策**）

参考 vendor DeerFlow `agents/memory/storage.py`，但层名简化为 5 层：

| Layer | 语义 | 写入时机 | 检索时机 |
|---|---|---|---|
| `work` | 当前任务工作上下文 | 任务进行中 | 同任务持续读取 |
| `personal` | 用户个人偏好（语气、惯用术语） | 用户显式或长期累积 | 几乎每个 turn |
| `top_of_mind` | 最近高优先级提示 | 用户/系统标记 | 优先注入 prompt |
| `history` | 过往会话摘要 | 由 [27 上下文压缩](./27-context-compression.md) 触发后写入（详见 27 doc） | 检索式注入 |
| `facts` | 已确认的事实知识库 | 显式 put / M2 自动从 history 提取 | RAG 检索 |

**强制约束**：
- 同一 `(tenant, agent, key, layer)` 写入触发 version+1（保留历史版本，便于审计）
- `work` 默认 `session_scope=True`，session 结束清理；其他层默认 agent 全局
- `top_of_mind` 默认 TTL = 7 天

### 5.2 写入队列（**关键决策**）

> 设计源自 vendor DeerFlow `agents/memory/queue.py` + `updater.py`：debounced 写入避免热路径阻塞，特别是 LLM 调用前后的 latency 敏感期。

```python
# 简化伪代码
class MemoryWriteBuffer:
    def __init__(self):
        self.buckets: dict[tuple[str,str], list[MemoryItem]] = {}
        self.last_flush: dict[tuple[str,str], float] = {}

    async def put(self, item: MemoryItem):
        # 1. 校验 metadata 不含 pii_fields（manifest lint 已核对，运行时再断言）
        validate_metadata_no_pii(item.metadata, tenant_pii_fields(item.tenant))
        # 2. PII redact content_text（必须在 embed 之前，避免敏感原文进入 vector）
        item = item.copy(update={
            "content_text": pii_redact(item.content_text, tenant_pii_fields(item.tenant))
        })
        bucket = (item.tenant, item.agent)
        self.buckets.setdefault(bucket, []).append(item)
        if len(self.buckets[bucket]) >= 100:
            await self._flush(bucket)

    async def periodic_flush_loop(self):
        while True:
            await asyncio.sleep(1)
            for bucket, items in list(self.buckets.items()):
                if items and now() - self.last_flush[bucket] > 5:
                    await self._flush(bucket)

    async def _flush(self, bucket):
        items = self.buckets.pop(bucket, [])
        if not items: return
        # 批量 embed
        vectors = await llm_gateway.embed([i.content_text for i in items])
        # 批量 insert
        try:
            await db.bulk_insert(items, vectors)
        except DBError:
            await db.insert_into_queue(items)  # fallback
```

### 5.3 检索算法

```sql
-- search 实现：双段式（向量 + metadata 过滤）
WITH q AS (
  SELECT $1::vector AS qv
)
SELECT id, tenant_id, agent_name, session_id, layer, key, content_text, metadata,
       1 - (content_vector <=> q.qv) AS score
FROM memory_item, q
WHERE tenant_id = $tenant_id
  AND agent_name = $agent
  AND ($session_id IS NULL OR session_id = $session_id OR session_id IS NULL)
  AND ($layer_filter IS NULL OR layer = ANY($layer_filter))
  AND metadata @> $metadata_filter
  AND (expires_at IS NULL OR expires_at > NOW())
  AND 1 - (content_vector <=> q.qv) >= $min_score
ORDER BY content_vector <=> q.qv
LIMIT $top_k;
```

**HNSW 索引适用范围**：单个 collection（`tenant_${tenant}_${agent}` 维度）item 数 < 1000 万 → 召回率 > 95%。超过此规模 → 拆分多个 partition（M2 议题）。

### 5.4 embedding 模型升级路径

```
1. 新增 column content_vector_v2 vector(N)（不删 v1）
2. 后台 reembed worker：批处理（每批 1000 条）调新模型
3. 全部回填后切换默认 search 用 v2
4. 留 7 天观察窗 → drop v1 column + 索引
```

### 5.5 RLS（行级安全）配合

```python
# 每个数据库连接获取时绑定 tenant_id（统一 session 变量名 app.tenant_id）
async with db.connect() as conn:
    await conn.execute("SET LOCAL app.tenant_id = $1", [tenant])
    # 后续查询自动按 RLS 隔离，即使代码漏写 WHERE tenant_id
```

> 这是 [15 AuthN/AuthZ](./15-authn-authz.md) 提供的全局机制；session 变量名全库统一为 `app.tenant_id`，与 DB 列名 `tenant_id` 匹配；CI 有 lint 校验所有 RLS policy 使用同一变量名。本子系统是其受益方。

---

## 6. 失败模式 & 缓解

| 失败模式 | 触发场景 | 影响 | 缓解 |
|---|---|---|---|
| Embedding API 失败 | LLM Gateway / provider 故障 | 写入卡住 | put 落入 memory_write_queue；后台 worker 重试 |
| 写入风暴 | 业务逻辑 bug 大量调用 put | DB 压力 | (tenant, agent) 维度限速（默认 1000 items/min）；超出 drop + log warn |
| 查询超时 | HNSW 在大表退化 | 用户体验 | search 默认 statement_timeout=2s；返回部分结果 + warn |
| 跨租户数据泄漏 | 应用代码漏写 tenant filter | 严重隔离破坏 | RLS 兜底；CI 加一条单元测试模拟漏 filter，必须返回空 |
| embed 模型变更后不一致 | 同租户混合 v1/v2 vector | 检索质量下降 | reembed 期间 dual-write；search 强制选定一个版本 |
| TTL 累积导致 bloat | 大量过期 item 不清理 | 表膨胀 | cleanup job 每天跑（DELETE WHERE expires_at < NOW()）+ pg autovacuum 调优 |
| 写队列堆积 | DB 长期不可写 | 内存爆 | 队列长度上限 100K；溢出 fast-fail 返回 503 给上层 |
| version 冲突 | 并发 put 同 key | 死锁 | 用 ON CONFLICT DO UPDATE 实现 upsert；version 单调递增 |
| reembed 中途失败 | 模型 API 限流 | 部分 item 无新 vector | 记录 reembed 进度表（per-batch），可断点续跑 |
| metadata @> 大对象慢 | metadata 嵌套深 | 全表扫 | 限制 metadata 深度 ≤ 3 + key 数 ≤ 20；超出 lint 阶段拒绝 |

---

## 7. 可观测性

> 日志完整字段遵循 [20 § 5.3](./20-observability.md)，此处仅展示子系统专属字段。
> Metric / span 命名遵循 [20 § 5.x 命名规范](./20-observability.md)：metric 统一 `expert_work_*` 前缀（snake_case），span 统一 `expert_work.{component}.{action}`。

### Prometheus metrics

```
expert_work_memory_put_total{tenant,agent,layer,status}
expert_work_memory_search_total{tenant,agent,layer,status}
expert_work_memory_search_duration_seconds{tenant,agent}                       histogram
expert_work_memory_search_score_distribution{tenant,agent}                     histogram (0~1)
expert_work_memory_write_queue_depth{tenant}                                   gauge
expert_work_memory_write_queue_failures_total{tenant,reason}
expert_work_memory_embed_calls_total{model,status}
expert_work_memory_items_total{tenant,agent,layer}                             gauge (定时刷新)
expert_work_memory_storage_bytes{tenant}                                       gauge
expert_work_memory_reembed_progress{tenant,model_from,model_to}                gauge 0~1
```

### OTel spans

```
expert_work.memory.put              attrs: tenant, agent, layer, key, session_scope
expert_work.memory.search           attrs: tenant, agent, layer, top_k, hits
├── span: expert_work.memory.embed  → expert_work.llm_gateway.embed
└── span: expert_work.memory.db_query  attrs: rows_returned, query_duration_seconds

expert_work.memory.flush_buffer     attrs: bucket_size
└── span: expert_work.memory.batch_embed
└── span: expert_work.memory.batch_insert
```

### 关键告警

| 告警 | 条件 | 严重度 |
|---|---|---|
| 写队列积压 | `write_queue_depth > 10000` 持续 5min | P1 |
| 检索 P95 > 2s | search latency 超阈值 | P1 |
| Embedding 失败率 > 5% | 5min 滑窗 | P1 |
| 跨租户检索告警 | 任意 search 返回 tenant ≠ 调用 tenant | P0（应永不发生） |

---

## 8. 安全考虑

| 攻击面 | 防御 |
|---|---|
| 跨租户检索 | 三重防御：① 应用层强制 WHERE ② Postgres RLS ③ 单元测试 |
| Memory 注入攻击 | 业务通过 LLM 输出写 memory → metadata 过滤 + content 长度限制（默认 64KB / item）+ [output filter middleware] 校验 |
| PII 泄漏到 vector | content_text 经 [PII redactor middleware] 处理后再 embed；[tenant_config.pii_fields] 配置驱动 |
| 删除不彻底（GDPR） | DELETE 走硬删除（不软删）；reembed 进度表也清；[20 Observability] 中相关 trace 30 天后归档 |
| 嵌入模型 API key 泄漏 | embedding 调用经 [10 LLM Gateway] → [11 Credential Proxy] |
| metadata 中存敏感数据 | manifest lint 阶段警告：禁止在 metadata 写入 pii_fields 列出的字段 |
| 长期 memory 被对手污染 | put 操作记入 [17 Audit Log]；admin UI 可回溯 / 撤销最近 N 条 |
| 检索结果回吐 system prompt | 检索结果在拼 prompt 前由 dynamic_context middleware 处理；不与 system_prompt 混合（保 prompt cache 命中） |

---

## 9. M0 / M1 / M2 演进

### M0 — MVP（pgvector 基础）
- 短期 = LangGraph PostgresSaver（vendor 直接用）
- 长期 = `memory_item` 表 + HNSW 索引
- 5 层 layer 全支持
- 写入队列（in-process buffer + DB fallback）
- `text-embedding-3-large` 默认
- 强 tenant + RLS
- 基础 metric / span / log

### M1 — 分层语义完善
- 按 layer 差异化策略（top_of_mind 自动 TTL；history 写入由 [27 上下文压缩](./27-context-compression.md) 驱动）
- 后台 cleanup worker（过期清理 + write_queue retry）
- reembed pipeline（embedding 模型升级支持）
- per-(tenant, agent) 配额（item 数上限 / 存储字节上限），来自 [16 Quota]
- collection 隔离（每 (tenant, agent) 一张分区，参见 [23 Postgres Scalability]）
- 业务可选 backend：默认 pgvector，可切 Qdrant（manifest.memory.long_term.backend）

### M2 — 跨 session 知识沉淀
- 自动从 `history` 层提取 `facts`（LLM 抽取 → 去重 → 入库）
- session 摘要后回写 `history`（摘要算法/触发条件详见 [27 上下文压缩](./27-context-compression.md)）
- 跨 agent 共享 facts（同租户内）
- 业务大盘：每 agent 的 memory hit rate / 命中提升的回答质量

### M3 — 大规模优化
- 千万级 item / 租户 → 分区 + ANN 索引调优（IVF + 重排）
- 跨 region 复制（data residency）
- 联邦学习场景（不出租户）

---

## 10. 开放问题

1. **vector 维度选 1536 还是 3072？** text-embedding-3-large 原生 3072，可降维到 1536（性能更好，质量略降）。M0 选 3072，M1 评估降维。
2. **HNSW vs IVFFlat：** HNSW 召回率高但写入慢；千万级以下没问题，更大规模可能要 IVFFlat。M2 决策。
3. **reembed 时 dual-write 还是直接切换：** dual-write 安全但成本翻倍；倾向 dual-write，加切流量比例控制。
4. **是否引入 hybrid search（BM25 + vector）：** 对短文本 KV 场景（如用户偏好）BM25 可能更准。M1 评估。
5. **Memory hit 反馈回流：** 检索结果对最终回答的贡献度，是否回写 metadata（用作权重衰减）？属于 [26 Eval Framework] 议题，本子系统提供 score column 即可。
6. **同步还是异步 search：** 当前同步；高并发场景是否预取（speculative search）？M2 议题。
