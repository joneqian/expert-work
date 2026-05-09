# 23 Postgres Scalability（Postgres 扩展性）

## 1. 职责 & 边界

**是什么**：定义 Helix 主存（Postgres）的容量、分区、索引、连接池、读写分离、向量索引（pgvector）选型，让单库支撑 M0 单租户、M1 多租户分区、M2 读写分离及大规模向量检索。

**不是什么**：
- 不负责备份恢复 → [22 Disaster Recovery](./22-disaster-recovery.md)
- 不重复定义业务表 schema（event_log / audit_log / manifest 等已在各自子系统）
- 不负责 Redis / 对象存储扩展性（独立子系统）
- 不负责 Vault → 单独运维

**包含 LangGraph 自带表（B9）**：本子系统涵盖 LangGraph 默认的 `checkpoints` / `checkpoint_writes` / `checkpoint_blobs` 三张表的容量、分区与备份协调（§ 3.5）。

**核心问题**：
- 单 tenant 月新增 50GB 事件 → 6 个月单表 300GB → 慢
- 1k 并发 connection 直连 Postgres → backend 内存爆炸
- pgvector 索引选型错 → 召回慢或召回率低
- 写后立即读：replica 延迟造成读不到
- 旧分区怎么压缩 / 归档不影响热查询

---

## 2. 上下游依赖

| 上下游 | 关系 |
|--------|------|
| 所有持久化子系统 | 共享 Postgres；本子系统提供分区 / 索引规范 |
| [13 Memory Store](./13-memory-store.md) | 长期记忆使用 pgvector；本子系统提供索引选型 |
| [17 Audit Log](./17-audit-log.md) | audit_log 表索引 + 保留策略基于本子系统约定 |
| [19 Durable Execution](./19-durable-execution.md) | event_log 必须按 (session_id, seq) 高效定位 |
| [22 Disaster Recovery](./22-disaster-recovery.md) | 分区表的 PITR 与归档策略协调 |
| [20 Observability](./20-observability.md) | 暴露 connection / lag / autovacuum / 慢查询指标 |
| PgBouncer | 连接池前置；本子系统约定 mode + prepared statement 兼容 |

---

## 3. 数据模型 / 状态机

### 3.1 容量估算（**M0 → M2**）

单 tenant 假设：
- 10 万 session/月
- 100 events/session
- 5 KB/event（含 payload JSONB）

→ 单 tenant 50 GB / 月；10 tenant = 500 GB / 月；6 个月 = 3 TB（裸数据，未含索引）。

**M0 单实例**：8 vCPU / 32 GB / NVMe SSD 2 TB；单租户 dogfood OK
**M1 多租户分区**：保持单实例，分区降低单表大小；引入 PgBouncer
**M2 读写分离**：1 写 + 2 读 replica；旧分区冷归档 S3

### 3.2 分区方案（M1）

`event_log` 是写入热点，采用 **2 维分区**：

```sql
-- 父表：复合分区（PARTITION BY HASH then PARTITION BY RANGE）
CREATE TABLE event_log (
    session_id   UUID        NOT NULL,
    seq          BIGINT      NOT NULL,
    tenant_id    TEXT        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL,
    event_type   TEXT        NOT NULL,
    payload      JSONB       NOT NULL,
    PRIMARY KEY (tenant_id, session_id, seq, created_at)  -- 必须含分区 key
) PARTITION BY HASH (tenant_id);

-- 16 个 hash 分片
CREATE TABLE event_log_h00 PARTITION OF event_log FOR VALUES WITH (MODULUS 16, REMAINDER 0)
    PARTITION BY RANGE (created_at);
-- ... event_log_h01 … event_log_h15 同上

-- 每个 hash 分片再按月 RANGE
CREATE TABLE event_log_h00_2026_05 PARTITION OF event_log_h00
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
-- ... 每月自动创建（pg_partman 管理）
```

**为什么先 HASH(tenant) 再 RANGE(month)**：
- HASH(tenant) 分散写入热点（单 tenant 暴增不影响其他）
- RANGE(month) 老分区可冷归档 / 压缩 / detach
- 查询 `WHERE tenant_id=? AND session_id=? AND seq=?` 命中**单分区**（hash 定位 → 主键定位）
- 跨月 session 通过 `created_at` 过滤命中少量 range 分区

### 3.3 其他关键表索引

```sql
-- audit_log 索引由 [17 § 3.1](./17-audit-log.md) 主定义，本子系统不重复声明
-- （历史版本曾在此处重复定义；为避免列名漂移与维护重复，已删除）

-- manifest：按 name + version 唯一
CREATE UNIQUE INDEX uq_manifest_name_version ON manifest (name, version);
CREATE INDEX idx_manifest_tenant ON manifest (tenant_id, name);

-- payload JSONB GIN（按需，非默认）
CREATE INDEX CONCURRENTLY idx_event_payload_path
    ON event_log USING GIN (payload jsonb_path_ops);
```

### 3.4 pgvector 索引（M0 → M2）

**memory_long_term** 表：

```sql
CREATE TABLE memory_long_term (
    id           BIGSERIAL PRIMARY KEY,
    tenant_id    TEXT        NOT NULL,
    collection   TEXT        NOT NULL,
    content_hash TEXT        NOT NULL,
    content      TEXT        NOT NULL,
    embedding    VECTOR(1536) NOT NULL,        -- text-embedding-3-large 降到 1536（matryoshka）
    metadata     JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**索引选型决策**：

| 数据规模 | 索引 | 参数 | 召回率 | 查询延迟 | 构建时间 |
|---------|------|------|--------|---------|---------|
| < 1M 行 | **HNSW** | m=16, ef_construction=64, ef_search=40 | > 95% | < 10ms | 慢 |
| 1M – 10M | HNSW | m=24, ef_construction=128, ef_search=64 | > 95% | < 30ms | 较慢 |
| > 10M | **IVFFlat** | lists=sqrt(rows), probes=10 | ~90% | < 50ms | 快 |

```sql
-- M0：HNSW（中等规模、高召回）
CREATE INDEX idx_mem_emb_hnsw ON memory_long_term
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

SET hnsw.ef_search = 40;
```

**降维**：text-embedding-3-large 默认 3072 维；用 matryoshka 降到 1536 维降存储 50%、查询提速 ~30%、召回率损失 < 2%。

### 3.5 LangGraph 自带表的容量与分区策略（B9）

LangGraph PostgresSaver 自带迁移管理 `checkpoints` / `checkpoint_writes` / `checkpoint_blobs` 三张表，DDL 由库自管，本节只约定**容量与分区**策略。

| 阶段 | 策略 | 备注 |
|------|------|------|
| **M0** | 不分区 | 由 PostgresSaver 自管；M0 单租户 dogfood，单表预计 < 50 GB；监控 `pg_table_size_bytes{table=~'checkpoint.*'}` |
| **M1** | 评估按 `thread_id` HASH 分区（16 分片） | 与 [19 § 5.1](./19-durable-execution.md) checkpoint 节奏配合；分区前需 PostgresSaver 兼容性验证（库内查询是否带分区 key） |
| **M2** | 配合冷归档（对应 detach 旧 checkpoint）| 演进与 event_log 一致：detach + pg_squeeze + S3 |

**备份策略**：与 `event_log` 一致并入 [22 DR](./22-disaster-recovery.md)（同 PITR 时间点；smoke test 必须验证 `checkpoint resume`，避免 checkpoint 与业务表恢复时间错位导致 [19](./19-durable-execution.md) replay 异常）。

**关键约束**：
- 不允许应用代码直接对 `checkpoints` / `checkpoint_writes` / `checkpoint_blobs` 做 DDL；DDL 一律由 PostgresSaver 库迁移管理
- M1 分区前必须在 staging 验证 LangGraph 库版本是否支持分区表（部分版本对 PARTITION BY 表的 plan cache 有兼容问题）

---

## 4. 关键接口

### 4.1 分区管理（pg_partman + 自定义）

```python
class PartitionManager:
    async def ensure_future_partitions(self, ahead_months: int = 3) -> None:
        """月初自动建未来 3 个月分区，避免到月写入时 race"""

    async def detach_old_partitions(self, before: datetime) -> list[str]:
        """detach（不 drop）旧分区；返回归档清单"""

    async def archive_partition(self, partition_name: str, s3_target: str) -> None:
        """pg_dump 单分区 → S3；成功后 DROP"""
```

### 4.2 读写分离路由

```python
class DbRouter:
    async def writer(self) -> AsyncSession: ...
    async def reader(self, *, force_primary: bool = False) -> AsyncSession:
        """
        force_primary=True：写后立即读 / 一致性敏感场景
        默认走 replica；遇 replica lag > 1s 自动降级到 primary
        """
```

### 4.3 慢查询审查

```python
class SlowQueryReporter:
    async def collect(self, threshold_ms: int = 500) -> list[SlowQuery]:
        """从 pg_stat_statements 拉 mean_exec_time > threshold 的语句"""
```

---

## 5. 算法 / 关键决策

### 5.1 PgBouncer 模式选择

| 维度 | session mode | **transaction mode** | statement mode |
|------|-------------|---------------------|----------------|
| 连接复用 | 弱 | **强** | 最强 |
| prepared statement | ✅ | ⚠️ 需 PgBouncer 1.21+ | ❌ |
| advisory lock | ✅ | ❌ | ❌ |
| LISTEN/NOTIFY | ✅ | ❌ | ❌ |

**关键决策**：
- M0 / M1 用 **transaction mode**（1000 客户端连接 → 50 backend）
- 应用层禁止跨事务的 advisory lock / LISTEN（迁到 Redis pubsub）
- prepared statement 需 PgBouncer ≥ 1.21（M1 升级到位）

### 5.2 写后立即读的一致性

**问题**：API 端 POST 创建后立即 GET，replica lag 0.5s 内可能读不到。

**策略**：
- 应用层显式标记一致性级别：默认 `eventual`，`strong` 走 primary
- 同 session 写后 N 秒内的读自动走 primary（`session.last_write_at` 跟踪）
- 写完成后返回 `X-Read-Hint: primary` 给客户端，客户端下次请求带回提示

### 5.3 旧分区压缩 / 归档

`event_log` 6 个月前的分区：
1. **detach**（不影响在线查询；旧 session 引用通过 PartitionManager 解析路径）
2. **autovacuum off**（停止后台维护）
3. **CLUSTER 重排**（按主键物理顺序，提高压缩率）
4. **pg_squeeze 压缩**（在线压缩，节省 30-50% 空间）
5. 12 个月后：`pg_dump` 单分区 → S3（Glacier）→ DROP

查询历史数据：通过应用层路由——日期 < 12 个月走 Postgres；> 12 个月走 S3 + 临时表（M2 引入 query proxy）。

### 5.4 autovacuum 调优 + 索引原则

**热表**（当月分区）调激进参数：`autovacuum_vacuum_scale_factor=0.05`、`analyze_scale_factor=0.02`、`vacuum_cost_limit=2000`（默认过保守）；**冷分区** vacuum 关闭。

**索引设计原则**：大表加索引必须 **CONCURRENTLY**；JSONB GIN 按需开（写放大 5-10x）；复合索引按前缀法则避免单列冗余；月度 `pg_stat_user_indexes` 找膨胀 > 50% REINDEX。

### 5.5 pgvector 性能要点

- HNSW 用 **CONCURRENTLY** 创建（pgvector 0.6+）；`ef_search` 每查询可调做召回 / 延迟 trade-off
- IVFFlat：`lists = sqrt(rows)`，`probes ≈ lists/10`
- 距离函数与索引 op class 必须匹配（cosine / l2）
- 高基数过滤（`tenant_id`）前置 WHERE；`ORDER BY embedding <=> $1 LIMIT N` 让索引高效

---

## 6. 失败模式 & 缓解

| 失败模式 | 影响 | 缓解 |
|---------|------|------|
| 单 tenant 暴增写入压垮单分片 | hash 分片不均 | hash 16 分片足够分散；监控 per-partition write rate；超阈值告警 |
| 月初未提前建分区，写入失败 | 业务报错 | pg_partman 自动建未来 3 个月；监控未来分区数 < 2 即 P1 |
| pgvector 索引召回率下降（数据漂移） | 检索质量降 | 月度评估召回率（标注集）；触发阈值 → REINDEX |
| PgBouncer transaction mode + 应用用 prepared statement 不兼容 | 报错 | M0 应用层禁 prepared statement；M1 升 PgBouncer ≥ 1.21 |
| Replica lag 飙升（大 transaction） | 读到旧数据 | 长事务监控（> 30s 告警）；批量 DELETE 拆 batch；写后读自动 primary |
| autovacuum 跑不完导致表膨胀 | 查询变慢 | 热分区调激进参数；夜间窗口手动 VACUUM ANALYZE |
| 大量 OR / NOT IN 查询走 seq scan | CPU 100% | 慢查询审查 → 重写为 UNION ALL / EXISTS |
| connection pool 耗尽 | 应用 503 | PgBouncer pool_size 与 Postgres `max_connections` 协调；应用层 timeout 5s |
| pg_stat_statements 无数据（被重置） | 慢查询不可见 | 每日 dump pg_stat_statements 到独立表存档 |
| 跨分区聚合查询慢（COUNT、SUM） | dashboard 卡死 | 物化视图（每小时 refresh）；或用 Redis 计数器 |
| 索引膨胀（INSERT/UPDATE/DELETE 重） | 查询变慢 | 月度 REINDEX CONCURRENTLY；监控膨胀率 |
| 迁移期间锁表 | 业务卡死 | DDL 用 expand-contract pattern；CONCURRENTLY；高峰避开 |
| pgvector 维度变了（embed model 升级） | 历史数据召回崩 | 双写过渡：旧维度列保留；新列陪跑 30 天后切换 |
| 误删整表 | 灾难 | RLS + DML 限权；DROP/TRUNCATE 走 PR review；详见 [22 DR](./22-disaster-recovery.md) |

---

## 7. 可观测性

> 命名规范、日志必填字段、span attrs 强制约定遵循 [20 Observability § 5.1 / § 5.3](./20-observability.md)；
> 本节只列本子系统特有的 metric / span / 日志事件。

**Metrics**

`pg_exporter` 原生导出的 metric（命名以 `pg_*` / `pgbouncer_*` 开头）由 exporter 决定，**约定豁免** `helix_*` 前缀强制；
本子系统**自建**的 metric 一律加 `helix_*` 前缀。

```
# pg_exporter 原生（豁免）
pg_replication_lag_bytes
pg_replication_lag_seconds
pg_stat_statements_total_exec_time{queryid}
pg_table_size_bytes{schema, table} / pg_index_size_bytes
autovacuum_running / autovacuum_dead_tuples{table}
wal_bytes_per_second
pgbouncer_pool_size{pool} / pgbouncer_pool_waiting

# 自建（强制 helix_* 前缀）
helix_pg_connection_pool_in_use{db}                              gauge      # 与 [20 § 5.2](./20-observability.md) 共用同一命名（替代旧 pg_connections_active）
helix_pg_partition_count_future{table}                            gauge      # 未来分区数（< 2 告警）
helix_pgvector_search_latency_seconds{collection}                 histogram  # histogram 单位强制 _seconds（替代旧 _ms）
helix_pgvector_recall_at_10{collection}                           gauge      # 月度评估的召回率
```

**Spans**（强制 `helix.db.*` 前缀）：
- `helix.db.query`（所有 SQL）+ attrs `db.statement_class`、`db.partition`
- `helix.db.pgvector_search`

**Dashboards**：
- 「Postgres Health」: connection / lag / size / autovacuum
- 「Slow Queries Top-10」: 按 mean_exec_time 排序
- 「Partition Storage」: 各分区行数 + 大小
- 「Vector Search Quality」: latency P95 + recall@10

**告警**（P0/P1）：
- P0：`pg_replication_lag_seconds > 60` 持续 5 min
- P0：`helix_pg_connection_pool_in_use / max_connections > 0.85`
- P0：`helix_pg_partition_count_future < 2`
- P1：单查询 mean_exec_time > 1s 且 calls > 100/h
- P1：表膨胀率 > 50%
- P1：向量召回率月度环比下降 > 5%

---

## 8. 安全考虑

**威胁模型**：

| 威胁 | 攻击路径 | 防御 |
|------|---------|------|
| 跨 tenant 数据泄漏 | RLS 配置漏 | **强制 RLS**（每个表 `ENABLE ROW LEVEL SECURITY`）；CI 静态校验所有租户表均有 policy |
| SQL 注入 | 应用层拼接 SQL | SQLAlchemy 全用参数绑定；`text()` 加 `bindparams()`；CI 扫描 `f"SELECT ... {var}"` |
| 备份未加密 | 备份盘失窃 | 详见 [22 DR](./22-disaster-recovery.md) |
| 慢查询拖死库（DoS） | 攻击者构造昂贵查询 | `statement_timeout = 30s`（默认）；`idle_in_transaction_session_timeout = 60s` |
| pg_stat_statements 含 PII | 慢查询日志泄漏 | `pg_stat_statements.track = top`；参数 normalize；日志 redact 中间件 |
| 直连 Postgres 绕过 PgBouncer | 越权 | Postgres `pg_hba.conf` 限制 PgBouncer host 之外不允许 |
| 过宽的 GRANT | 普通服务账号能 DROP | 角色分离：`helix_app`（DML）/ `helix_migrate`（DDL）/ `helix_ro`（只读） |
| `CREATE EXTENSION` 滥用 | 引入未审核扩展 | 仅允许白名单（pgvector / pg_partman / pg_stat_statements）；CI 校验 |

**RLS 示例**（D1：session 变量名**统一为 `app.tenant_id`**）：
```sql
ALTER TABLE event_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON event_log
    USING (tenant_id = current_setting('app.tenant_id', true));
-- 应用每次请求 SET LOCAL app.tenant_id = '<tenant>';
```

**全局约束**：
- 所有租户表的 RLS policy **必须**引用 `current_setting('app.tenant_id', true)`，**禁止**使用其他命名（曾经出现过的 `app.current_tenant` / `helix.tenant` 一律视为 bug）
- **CI lint 校验**：扫描所有迁移脚本中的 `CREATE POLICY` 语句，匹配 `current_setting\(['"]([^'"]+)['"]` 捕获组必须为 `app.tenant_id`；不符合则 CI 阻断
- 应用层 SET LOCAL 也必须用 `app.tenant_id`，由 SQLAlchemy session bootstrap 中间件统一注入（不允许业务代码自己 SET）

**加密**：
- TLS in-transit（必须；`hostssl` 全启）
- TDE at-rest：M1 启用 cluster-level encryption（pg_tde 或文件系统 LUKS）
- pgcrypto 用于字段级加密（M2，针对 PII 字段）

---

## 9. M0 / M1 / M2 演进

### M0 — 单实例 + PgBouncer + HNSW

**交付**：
- Postgres 16 单实例（8 vCPU / 32 GB / 2 TB NVMe）
- PgBouncer transaction mode（1000 客户端 → 50 backend）
- pg_partman 安装（不分区，但准备脚手架）
- 关键索引就绪（详见 §3）
- pgvector HNSW（m=16, ef_construction=64）
- pg_stat_statements 启用
- 慢查询日志 > 500ms
- statement_timeout = 30s
- LangGraph `checkpoints` / `checkpoint_writes` / `checkpoint_blobs` **不分区**（由 PostgresSaver 自管），仅启用 size 监控（B9）

### M1 — 分区 + RLS + autovacuum 调优

**交付**：
- `event_log` HASH(tenant) × RANGE(month) 二维分区（16 hash × month）
- pg_partman 自动建未来 3 月分区
- 全租户表强制 RLS + CI 校验（含 `app.tenant_id` 命名 lint，见 § 8）
- autovacuum 热分区调激进参数
- PgBouncer ≥ 1.21（prepared statement）
- 月度索引 / 表膨胀报告自动化
- 角色分离（app / migrate / ro）
- LangGraph 自带表（`checkpoints` 等）**评估** thread_id HASH 分区（B9）；含 PostgresSaver 兼容性验证

### M2 — 读写分离 + 冷归档 + 加密

**交付**：
- 1 写 + 2 读 replica（streaming replication）
- DbRouter 路由 + 写后读 primary 策略
- 6 个月前分区 detach + pg_squeeze 压缩
- 12 个月前分区归档 S3 + DROP
- TDE at-rest 启用
- pg_tde / 字段级 pgcrypto（PII）
- pgvector 大规模评估：> 10M 切 IVFFlat
- 跨 region async replication（与 [22 DR](./22-disaster-recovery.md) 协调）

---

## 10. 开放问题

1. **Citus / 原生分区？** Citus 提供分布式但运维复杂；M2 容量评估后再定。**当前倾向**：先吃透原生分区，单实例 + 16 hash 分片支撑到 ~50 TB。

2. **TimescaleDB 是否更适合 event_log？** 对时序场景优势明显（自动 chunk + 压缩），但对 RLS / 多业务表混用支持弱。**当前倾向**：原生分区 + pg_partman + pg_squeeze 已够。

3. **pgvector vs Qdrant**：Qdrant 性能更好但额外组件；pgvector 与 RLS / 事务强一致。**当前倾向**：pgvector 直到 > 10M / collection，再评估迁移。

4. **冷归档查询路径**：S3 上的 parquet 用 DuckDB 还是临时 import 回 Postgres？**待 M2 评估**。

5. **读写分离 lag 容忍上限**：1s 还是 5s？业务容忍度调研后定。

6. **pg_squeeze 是否在线安全**：个别版本有索引重建短暂阻塞；需在 staging 充分演练。

7. **embed model 升级流程**（如 OpenAI 出新版）：双写过渡期、回滚策略、成本控制 → 待 [13 Memory Store](./13-memory-store.md) 协同细化。
