# Stream D — 高级数据保护（设计先行）

> 落实 [docs/ITERATION-PLAN.md](../ITERATION-PLAN.md) § Stream D；执行的是
> [subsystems/17 Audit Log § 5.2 / 5.3 / 6 / 9-M1](../architecture/subsystems/17-audit-log.md) 的
> M0→M1 演进 + WORM 备份 + per-tenant 保留期；
> [架构 07-INFRASTRUCTURE-GAPS § 3 + § 4 + § 6](../architecture/07-INFRASTRUCTURE-GAPS.md) 的
> P0 #6-#9、#19；
> [ADR-0004 Object Storage](../adr/0004-object-storage.md) 的 ObjectStore 扩展点。
> 本 Stream **不**重做 audit_log 表（A.4 已建）、不重做 ObjectStore Protocol
> （A.5 已建），只在它们之上做产品级数据保护。

设计先行规则（[memory:feedback_design_first_iteration.md](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_design_first_iteration.md)）：
所有架构 / 接口 / mini-ADR 必须在编码前就锁定，D.1 - D.4 PR 仅执行本文档。

---

## 1. 范围 & 边界

### 1.1 In-scope（D.1 – D.4）

| 子项 | 实现内容 | 关联子系统 |
|------|---------|-----------|
| **D.1a audit 写者降权** | migration 0008：新 `audit_writer` NOLOGIN 角色；`REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM PUBLIC / app_user`；应用写路径 `SET ROLE audit_writer`。**写入仍走应用层 redactor**，但 DB 层兜底防"操作人改 audit 抹痕"。 | 17 § 8 / 9-M1 |
| **D.1b ObjectStore Object Lock 语义** | `ObjectStore.put` 增加 `retain_until: datetime \| None` + `lock_mode: Literal["governance","compliance"] \| None` 形参；S3 实现走 `ObjectLockRetainUntilDate`/`ObjectLockMode` 头；MinIO 集成测试启用 Object Lock 桶；in-memory 实现走"软"语义（记录元信息，不真锁）。 | ADR-0004 / 17 § 9-M1 |
| **D.1c audit WORM 备份 worker** | audit_log 加 `backup_acked` bool + `backup_acked_at` 列；新增 `services/audit-backup-worker/`：cursor-poll 未备份行 → 写 `{audit_bucket}/{tenant}/{YYYY}/{MM}/{DD}/{id}.json`（compliance 模式锁 N 天）→ `UPDATE backup_acked = true`；失败重试 + 指数退避；指标 `helix_audit_archive_lag_seconds{tenant}`。 | 17 § 5.6 / 7.1 |
| **D.2 TenantAwareRedactor** | 扩展 `helix-runtime/audit/redactor.py`：在现有全局 secret pattern 之上加 **per-tenant `pii_fields` 递归 key-name 掩码**；从 `TenantConfigService` 拉 `pii_fields`；`AuditLogger` 注入工厂；`tenant_config` 缓存命中即 0 额外开销。 | 17 § 5.2 |
| **D.3 retention TTL + 配置** | migration 0009：`tenant_config` 加 `audit_retention_days` / `event_log_retention_days` （默认 90 / 30，HIPAA pack 调到 2555）；`services/retention-cleanup-job/`：每 tenant 一轮 `DELETE FROM audit_log WHERE tenant_id=? AND occurred_at < ? AND backup_acked=true LIMIT 10000`（事务批处理）；同样清 `event_log`、过期 `token_reservation`、过期 `jwt_blacklist`；保留 SLA 决策文档 [decisions/data-retention.md](../decisions/data-retention.md)。 | 17 § 5.3 / 9-M1；GAPS § 3 #8、§ 6 #19 |
| **D.4 at-rest 加密 + ADR** | `infra/docker-compose` Postgres / MinIO 卷挂到 LUKS 容器（dev fixture）；`infra/minio` 切 SSE-KMS 启用；ADR-0008：**M0 走 OS/卷层 + 云厂托管 RDS encryption（生产）**，不引入 `pg_tde`；密钥管理与 ADR-0007 SecretStore 对齐（dev 走 minio 自带 KES，prod 走云 KMS）。 | GAPS § 4 #9 |

### 1.2 Out-of-scope（明确推迟）

| 推迟项 | 落地 Stream | 备注 |
|-------|------------|------|
| 在 LLM middleware 链注册 PII redactor 固定 anchor | Stream E.2 锚点完成后挂上去 | D.2 只实现 redactor + audit 写路径接入；LLM 调用链路上的 in-flight redact 等 E |
| Postgres 月分区（按 tenant_short, month）+ partition detach 归档 | M1 / Stream G | 17 § 9-M1；M0 单表 + cron DELETE 已够 |
| Hash chain + 公证 | M2 | 17 § 5.4；高 QPS 需异步 chain builder |
| GDPR 数据导出 / 删除 API | M1 / J.x | GAPS § 3 #11、#12 |
| Athena / DuckDB 冷归档查询 | M2 | 17 § 9-M2 |
| Vault dynamic secrets 自动轮换 DB / API key | M1 / Stream G | GAPS § 4 #10 |
| `pg_tde` 扩展替换 OS/卷层加密 | 待 pg_tde GA 后评估 | M0 / M1 都不上 |
| WORM 桶 IaC（cosign 签名 + region replication）| M1 | D 阶段 Object Lock 启用即可；多 region 留 M1 |
| `audit:read` 自审计风险评分 | M1 | C 阶段已写自审计行；D 不动 |

### 1.3 验收门（来自 ITERATION-PLAN § Stream D Verification）

1. **插入含 PII 数据走 audit 写路径** — `details` 中含 tenant 配置的 `pii_fields` key（如 `patient_id_card`）→ 写入 DB 后该字段被 `***REDACTED***` 替换；`helix_audit_redact_hit_total{pattern="pii_field"}` +1。
2. **DB 直连尝试 `UPDATE audit_log SET ... WHERE id = X` 被拒** — `audit_writer` 角色不持有 UPDATE / DELETE 权限；普通 `app_user` 角色亦无；只有 superuser 例外（不暴露给应用）。
3. **WORM 桶禁止 overwrite** — backup worker 落盘后，对同一 key 再次 `put` 在 compliance 模式下返回 5xx；本地 MinIO 集成测试覆盖。
4. **TDE / SSE-KMS 启用后磁盘文件不可明文读取** — dev fixture：在 LUKS 卷未解锁时直接读 `pgdata/` 二进制；MinIO 落盘的对象在 KMS 加密下 `mc cat` 直接读裸文件不可读。
5. **retention job 不会误删未备份数据** — 测试：行 `backup_acked=false` 且超 retention 期，job 跳过该行 + 发 `helix_retention_skip_total` 计数（不报错、不阻塞）；只有 `backup_acked=true` 的行才能被删。

---

## 2. 架构

### 2.1 audit 写 + 备份 + 清理路径（D 完成后的最终形态）

```
                       ┌─────────────────────────────────┐
                       │  应用层 AuditLogger.write(entry) │
                       └────────────────┬─────────────────┘
                                        │
                            TenantAwareRedactor.redact
                            (全局 pattern + per-tenant pii_fields)
                                        │
                                        ▼
                       ┌─────────────────────────────────┐
                       │  PG conn AS app_user            │
                       │  SET ROLE audit_writer          │
                       │  INSERT INTO audit_log ...      │
                       │  RESET ROLE                     │
                       └────────────────┬─────────────────┘
                                        │ commit
                                        ▼
                       ┌─────────────────────────────────┐
                       │  audit_log (backup_acked=false) │
                       └────────────────┬─────────────────┘
                                        │
   ┌─── 异步 cron / interval ───────────┼─────────────────────────┐
   │                                    │                         │
   ▼                                    ▼                         ▼
audit-backup-worker             retention-cleanup-job        admin 查询
- SELECT WHERE NOT acked        - SELECT WHERE acked AND      （走 audit_reader
- ObjectStore.put with          old AND tenant cfg            BYPASSRLS role）
  Object Lock retain_until      - DELETE LIMIT 10000
- UPDATE SET backup_acked=true
```

**关键不变量**：
- audit 写入永不依赖 backup worker（worker 离线 → audit 仍然 INSERT；只是 `backup_acked` 一直 false）
- retention job 永不删 `backup_acked=false` 行（无 backup 的 row 永久保留 + 告警）
- backup worker 不动 retention（cleanup 是另一个 job）

### 2.2 audit_log schema delta（migration 0008 + 0009）

**0008（D.1）—— 写者降权 + backup_acked**：

```python
# 0008_audit_worm.py
op.add_column(
    "audit_log",
    sa.Column("backup_acked", sa.Boolean(), nullable=False, server_default=sa.false()),
)
op.add_column(
    "audit_log",
    sa.Column("backup_acked_at", sa.DateTime(timezone=True), nullable=True),
)
op.create_index(
    "audit_log_backup_pending_idx",
    "audit_log",
    ["occurred_at"],
    postgresql_where=sa.text("backup_acked = false"),
)  # 部分索引，仅未备份行；备份完后 false→true 自动出索引

op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='audit_writer') THEN
            CREATE ROLE audit_writer NOLOGIN BYPASSRLS;
            -- BYPASSRLS mirrors audit_reader (0005). The audit write path
            -- legitimately inserts rows for every tenant; without bypass
            -- the FORCE-RLS WITH CHECK policy on audit_log would require
            -- threading app.tenant_id into every write site even though
            -- the writer is trusted single-purpose code.
        END IF;
    END $$;
""")
op.execute("GRANT USAGE ON SCHEMA public TO audit_writer;")
op.execute("GRANT INSERT, SELECT ON TABLE audit_log TO audit_writer;")
op.execute("GRANT USAGE, SELECT ON SEQUENCE audit_log_id_seq TO audit_writer;")
op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON TABLE audit_log FROM PUBLIC;")
# 应用主连接角色（M0 默认 app_user，未来由 settings 注入）按需 GRANT INSERT 给它
# 然后由应用层 SET ROLE audit_writer 写入。
```

**0009（D.3）—— retention 字段 + cleanup 标识**：

```python
# 0009_retention_config.py
op.add_column(
    "tenant_config",
    sa.Column("audit_retention_days", sa.Integer(), nullable=False, server_default="90"),
)
op.add_column(
    "tenant_config",
    sa.Column("event_log_retention_days", sa.Integer(), nullable=False, server_default="30"),
)
op.create_check_constraint(
    "tenant_config_audit_retention_positive_ck",
    "tenant_config",
    "audit_retention_days > 0 AND audit_retention_days <= 3650",
)
# event_log 类似；range 上界 10y 兜底输入错误
```

### 2.3 ObjectStore Object Lock 扩展

新增形参，**不破坏现有调用**（旧调用 `put(key, data)` 不传 lock 参数 → 行为不变）：

```python
# helix_agent/runtime/storage/base.py
class ObjectStore(Protocol):
    async def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
        retain_until: datetime | None = None,
        lock_mode: Literal["governance", "compliance"] | None = None,
    ) -> None:
        ...
```

**S3 / MinIO 实现**：将 `retain_until` / `lock_mode` 翻译为 `ObjectLockRetainUntilDate` / `ObjectLockMode` `put_object` 参数。桶必须预启用 Object Lock（IaC 配置；ADR-0008 在部署文档落决策）。
**关键语义**：S3 Object Lock 是**版本级**保护，每次 put 创建新版本；compliance 模式拦截的是"在 retain_until 之前删除该版本"，**不**拦同 key 再 put。审计 WORM 的真正保证由"该版本不可删"提供，配合 D.1c worker 用单调递增的 `audit_log.id` 作 key 避免误写。
**In-memory 实现**：把元信息存进 `_records[key]`；如果同 key 再 put 且现存对象 `retain_until > now()` 且 `lock_mode == "compliance"` → 抛 `ObjectLockedError`（不模拟 versioning；这是测试 + worker 重试路径的"已写过"信号）。
**调用方约束**：`audit-backup-worker` 永远传 `lock_mode="compliance"`；幂等性由 DB 侧 `backup_acked` 列保证而非依赖 S3 put-side 拒绝。

### 2.4 audit WORM backup worker 状态机

`services/audit-backup-worker/`，独立进程，aiohttp/asyncpg 直连（**不走** PgBouncer transaction pooling，因为需要长事务 + cursor）：

```
loop forever:
    rows = SELECT id, tenant_id, occurred_at, <all columns>
           FROM audit_log
           WHERE backup_acked = false
           ORDER BY id
           LIMIT 100
           (用部分索引 audit_log_backup_pending_idx)
    if no rows:
        sleep(2s); continue
    for row in rows:
        key = f"{row.tenant_id}/{row.occurred_at.year}/{row.occurred_at.month:02d}/{row.occurred_at.day:02d}/{row.id}.json"
        retain_until = now + tenant_config.audit_retention_days
        try:
            await object_store.put(key, row_to_json_bytes(row),
                                   content_type="application/json",
                                   retain_until=retain_until,
                                   lock_mode="compliance")
            await db.execute("UPDATE audit_log SET backup_acked=true, backup_acked_at=now() WHERE id=:id",
                             {"id": row.id})
        except Exception:
            backoff(row.id)
            logger.error("audit.backup.failed id=%s", row.id, exc_info=True)
            # 不 update；下一轮重试。指数退避只针对 *单行* 持续失败的情况，
            # 默认间隔 max(2s, 2 ** retries * 1s)，capped at 60s。
```

**幂等性**：同 id 写两次 → S3 创建第二个版本（compliance 拦的是 delete，不拦 put）；唯一一致性源是 DB 侧 `backup_acked` 列。worker 在写之前先 SELECT `backup_acked`；写完 UPDATE。失败重启场景：上一轮 put 成功 + UPDATE 没成功 → 这一轮发现 backup_acked=false → put 再写一遍（新版本无害）+ UPDATE。多版本本身不计入合规可疑（每个版本都受 retention 保护）。

**指标**：
- `helix_audit_backup_processed_total{tenant,result}` counter
- `helix_audit_archive_lag_seconds{tenant}` gauge（最新未备份行的 `occurred_at` 距 now 的差）

### 2.5 TenantAwareRedactor 行为

签名变化：

```python
class TenantAwareRedactor:
    def __init__(self, *,
                 global_patterns: Mapping[str, re.Pattern[str]] | None = None,
                 tenant_config_service: TenantConfigService) -> None: ...

    async def redact(self, *, tenant_id: UUID,
                     details: Mapping[str, Any]) -> RedactionResult: ...
```

- **递归 key 匹配**：`pii_fields` 是字符串列表（如 `["patient_id_card", "ssn"]`）；遍历 dict 时如果 key 名（不区分大小写）∈ pii_fields → 整个 value 子树替换为 `***REDACTED***`。
- **不走内容匹配**：per-tenant pii 走 key 名而非 regex，因为身份证号 / 手机号格式跨地域；规则属业务侧，由 tenant 配置说明。
- **全局 secret pattern 仍跑**：order 不重要，二者独立累加 hit。
- **缓存命中无额外开销**：`TenantConfigService.get` 已 60s 缓存；redactor 每条 audit entry 一次 `get` 调用。
- **AuditLogger 接入**：`AuditLogger.__init__(...,  redactor: TenantAwareRedactor)` 改为接受 redactor + tenant_id（已经在 `entry.tenant_id`），write 时 `await redactor.redact(tenant_id=entry.tenant_id, details=entry.details)`。

**不兼容性**：`AuditLogger.write` 签名不变（参数同；redactor 协议从 sync `redact(details)` 改异步 `redact(tenant_id, details)`）—— 在 D.2 同 PR 内更新所有调用方。

### 2.6 retention cleanup job

`services/retention-cleanup-job/`，独立进程，连主库（**不走** audit_reader，反过来要 DELETE 权限）：

```
loop nightly (cron / interval):
    for tenant in SELECT tenant_id FROM tenant_config:
        cfg = await tenant_config_service.get(tenant)

        # 1) audit_log：只删 backup_acked=true 且超期的
        cutoff_audit = now - cfg.audit_retention_days days
        deleted, skipped = batch_delete(
            "DELETE FROM audit_log
             WHERE tenant_id=:t AND occurred_at < :cutoff
                   AND backup_acked = true
             LIMIT 10000",  # 用 ctid 子查询模拟 LIMIT
        )
        skipped_unacked = count(tenant_id=t, occurred_at<cutoff, backup_acked=false)
        if skipped_unacked > 0: emit metric + warn log

        # 2) event_log：直接按 event_log_retention_days 删
        cutoff_event = now - cfg.event_log_retention_days days
        ...

        # 3) token_reservation 过期：按 expires_at < now，不依赖 retention
        # 4) jwt_blacklist 过期：按 exp_at < now
```

**测试矩阵覆盖**："unacked row 不被删" 是核心断言。

**指标**：
- `helix_retention_deleted_total{table,tenant}` counter
- `helix_retention_skipped_unacked_total{table,tenant}` counter（应该 ≈ 0；持续 > 0 = backup worker 拖延）

### 2.7 at-rest 加密层（D.4）

| 组件 | dev (compose) | prod |
|------|---------------|------|
| Postgres data | LUKS-encrypted bind mount（compose fixture：宿主先 `cryptsetup luksFormat` 一个文件作 loop device，挂到容器；密码走 secret file）| 云厂 RDS 内置加密（AWS RDS / Aliyun RDS PostgreSQL 等都内置）|
| MinIO data | SSE-KMS via KES sidecar（minio operator 标准做法；dev 用单机 KES + filesystem keystore）| S3 SSE-KMS via AWS KMS / 阿里云 KMS |
| 备份对象（audit WORM）| 同 MinIO SSE-KMS + Object Lock | 同 prod S3 SSE-KMS + Object Lock |
| 密钥来源 | KES filesystem keystore（dev only）| 云 KMS / Vault |

**ADR-0008** 记录三件事：(1) 不引入 `pg_tde`；(2) M0 走 OS/卷层 + 云厂 RDS；(3) 密钥来源与 ADR-0007 SecretStore 一致。

---

## 3. Mini-ADRs

### D-1：audit 写者降权用 `GRANT/REVOKE` 而非 DB trigger

- **替代**：建一个 `BEFORE UPDATE OR DELETE` trigger 抛 exception。
- **选择**：角色 + REVOKE。
- **理由**：(1) trigger 可被 trigger 拥有者绕过且静默 disable；(2) `REVOKE` 在 `pg_class` ACL 上一眼可查；(3) trigger 影响连接级延迟；(4) trigger 不能阻止 `TRUNCATE`，REVOKE 可以。
- **代价**：应用代码每次写要 `SET ROLE audit_writer` + 写完 `RESET ROLE`；用 transaction 块包，单次延迟 < 50µs。

### D-2：ObjectStore `lock_mode` 限 `governance` / `compliance` 二选一

- **替代**：把所有 S3 头都开放给调用方。
- **选择**：只暴露这两个值。
- **理由**：(1) `governance` 允许特权角色覆盖（dev / 误删恢复）；`compliance` 不可解锁（含 root）—— 这两个是合规上唯一有意义的两档；(2) 限制语义可以让 in-memory mock 也实现一致行为；(3) 桶级 default lock 配置由 IaC 管，运行时只决定单个对象的保留期。

### D-3：backup worker 用 cursor-poll 而非 LISTEN/NOTIFY

- **替代**：Postgres `LISTEN audit_inserted`，trigger NOTIFY。
- **选择**：cursor-poll（部分索引 + 100 行 batch + 2s 间隔）。
- **理由**：(1) NOTIFY 与 PgBouncer transaction pooling 不兼容（pgbouncer 会丢 notify 通道）；(2) 部分索引把"未备份"扫描成 O(待备份数) 而非 O(全量)；(3) cursor-poll 重启即恢复，无需任何状态机 sync；(4) audit 写入速率（peak 几百 QPS）远低于 100 行 / 2s 的处理能力，不会积压。

### D-4：per-tenant PII 按 **key 名** 而非内容匹配

- **替代**：让 tenant 配 regex 列表。
- **选择**：key-name 列表。
- **理由**：(1) 业务侧只知道字段名，不知道身份证 / 手机号的多地域 regex；(2) 全局 secret pattern 已覆盖通用 regex 类（API key / JWT 等）；(3) key-name 匹配 O(树深度 × 字段数)，CPU 友好；(4) key-name 配置可走 admin UI 简单表单，不用 regex 编辑器。

### D-5：M0 retention 走 batch DELETE 而非 partition drop

- **替代**：从 M0 就上 Postgres 月分区，drop 分区清理。
- **选择**：M0 batch DELETE；M1 上分区。
- **理由**：(1) 17 § 9 明确把分区放 M1；(2) M0 数据量小，DELETE 速度足够；(3) 分区改动是 schema 大改，独立 PR / Stream 更稳；(4) batch DELETE 配 `LIMIT 10000 + autovacuum`，对 OLTP 影响可控。

### D-6：M0 不上 `pg_tde`；走云厂 RDS / OS LUKS

- **替代**：上 `pg_tde` 扩展，table 级加密。
- **选择**：M0 走 RDS / LUKS；pg_tde 待 GA 评估。
- **理由**：(1) pg_tde 还是 Percona/EDB beta 阶段，不进 community；(2) 云厂 RDS encryption-at-rest 是 prod 默认；(3) LUKS 对 dev fixture 够用、零代码改动；(4) 引入 pg_tde 增加密钥管理复杂度 + 数据迁移风险，不值。

---

## 4. 接口

### 4.1 ObjectStore.put 扩展签名

见 § 2.3。**契约**：

- `retain_until=None` 且 `lock_mode=None` → 行为与现有调用一致（不锁）。
- `retain_until` 非空 + `lock_mode=None` → 报 `ValueError`。
- `lock_mode="compliance"` + 桶未启用 Object Lock → S3 报 400，实现包装为 `ObjectStoreError`。
- 同 key 已存在且现行 `retain_until > now()` + `lock_mode="compliance"` → in-memory 抛 `ObjectLockedError`；S3 由后端 5xx 直接 surface。

### 4.2 AuditWormBackupWorker

```python
# services/audit-backup-worker/src/worker/main.py
class AuditWormBackupWorker:
    def __init__(self, *, db_session_factory, object_store: ObjectStore,
                 tenant_config_service: TenantConfigService,
                 audit_bucket_prefix: str,
                 batch_size: int = 100,
                 poll_interval_s: float = 2.0,
                 metrics: WorkerMetrics) -> None: ...

    async def run_one_batch(self) -> int:
        """Process up to ``batch_size`` rows. Returns # processed."""

    async def run_forever(self, *, stop: asyncio.Event) -> None: ...
```

### 4.3 TenantAwareRedactor

见 § 2.5。

### 4.4 RetentionCleanupJob

```python
# services/retention-cleanup-job/src/job/main.py
class RetentionCleanupJob:
    def __init__(self, *, db_session_factory,
                 tenant_config_service: TenantConfigService,
                 batch_size: int = 10000,
                 metrics: JobMetrics) -> None: ...

    async def run_once(self) -> CleanupReport:
        """One full sweep over all tenants. Idempotent."""

@dataclass(frozen=True)
class CleanupReport:
    deleted_by_table: dict[str, int]
    skipped_unacked_audit: int
    duration_seconds: float
```

### 4.5 新 AuditAction（D 阶段新增的几个，预先列出避免 PR 间踩）

```python
class AuditAction(StrEnum):
    # 已有...
    BACKUP_OBJECT_WRITTEN = "backup:object_written"   # D.1c worker 自审计可选
    RETENTION_CLEANUP_RUN = "retention:cleanup_run"   # D.3 job 启动结束摘要
```

二者写到一个特殊的"系统 tenant"行（actor_type=system；resource_type=`audit_log` / `event_log`）。

---

## 5. 测试矩阵

| 维度 | 覆盖 PR | 测试类型 | 关键 case |
|------|---------|---------|-----------|
| `audit_writer` 角色 REVOKE 生效 | D.1a | integration（pg fixture） | 用 `app_user` SET ROLE 之后试 `UPDATE audit_log` → SQLAlchemy raises `InsufficientPrivilege` |
| `backup_acked` 列默认 false、写入后初始 false | D.1a | unit | 写一条 audit，查行：`backup_acked=false` |
| ObjectStore.put 锁参数透传 | D.1b | unit + minio integration | mock S3 client 看 header；MinIO 桶开启 Object Lock，put 后 `head_object` 返回 retain_until |
| 同 key compliance 重写报错 | D.1b | minio integration | put 一次，再 put → 5xx；in-memory mock 同形语义 |
| Worker 把未备份行写到 WORM 桶 + UPDATE acked | D.1c | integration | seed 100 行 → run_one_batch → 桶里 100 个对象 + `backup_acked=true` |
| Worker 幂等（put 失败但 UPDATE 没跑）| D.1c | integration | mock put 第二次报"已存在"，worker 应认为已成功并 UPDATE |
| Worker fail-soft（put 抛错 → 行保留未备份）| D.1c | unit | 注入 raise，行 `backup_acked` 仍 false；不抛出 |
| Redactor per-tenant pii_fields 命中 | D.2 | unit | tenant cfg `pii_fields=["ssn"]`；details `{"ssn":"123-45-6789","x":1}` → `ssn` → `***REDACTED***`；x 不变 |
| Redactor 大小写不敏感 | D.2 | unit | `pii_fields=["ssn"]` + key `SSN` 也 mask |
| Redactor 全局 + 租户 hit 都记 | D.2 | unit | details 含 jwt + ssn → 两个 pattern 都 +1 |
| AuditLogger 注入 TenantAwareRedactor 后 write 路径不变 | D.2 | integration | 旧测试全过 + 新 case 加入 |
| Retention job 跳过 unacked | D.3 | integration | 一行 acked + 一行 unacked，都过期 → 删 1 留 1 + `skipped_unacked_total=1` |
| Retention job 按 tenant retention 不同清不同 | D.3 | integration | tenant A 7d / B 90d；同 occurred_at 10d 前的行 → A 删 / B 留 |
| event_log retention 同语义 | D.3 | integration | 类似 audit；但不要求 backup_acked（event_log 不走 WORM） |
| LUKS 卷未解锁时无法读 | D.4 | manual dev fixture / smoke | docs 落清楚步骤；自动化在 compose `up` 前要求 unlock |
| MinIO SSE-KMS 启用后裸文件不可读 | D.4 | manual smoke | mc 落盘后 `cat` 是密文 |

---

## 6. 风险 & 缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| backup worker 长期挂掉，audit_log 堆积无法清理 | 磁盘满 + 合规丢档 | `helix_audit_archive_lag_seconds` 告警 P1（lag > 1h）；retention job 跳过未备份并发 `skipped_unacked` 告警 P1（>0 即报） |
| 应用代码忘了 `SET ROLE audit_writer` → 用 app_user 写入失败 | audit 写丢 | AuditLogger 内部统一 `with set_role("audit_writer"):` 上下文管理器；CI lint 禁止跨过 AuditLogger 直接 `INSERT INTO audit_log`（grep pattern） |
| Object Lock compliance 模式锁错保留期 | 数据永远删不掉 / 合规违规 | retain_until 由 tenant_config.audit_retention_days 计算；compliance 模式下任何后续 retain_until 变短都会被 S3 拒绝（这是 by design）；测试矩阵覆盖"配置中途变短"的 case |
| pii_fields 配错 mask 了正常字段 | 用户调试看不到 details | tenant cfg 改动有 audit 行（已落 C.7）；redact_hit 指标按 pattern 拆 + 异常激增告警；docs 给 pii_fields 配置示例 |
| retention 误删未备份数据 | 永久丢失 | 测试矩阵 #11 守门；job 实现里"未 acked 即跳过"是单点不变量，单元测试 + 集成测试双保险 |
| pg_tde / LUKS / SSE-KMS 三选一后期再换会重做数据 | 迁移痛 | ADR-0008 把决策固化；M1 / M2 任何改动需新 ADR replace；M0 选最少代码的路径（LUKS dev / RDS prod） |
| audit_writer 角色被误授给应用普通流 | 写者降权失效 | migration 0008 只 GRANT INSERT, SELECT；不 GRANT UPDATE / DELETE；定期 SQL 巡检（M1 加 cron）|
| WORM 桶配错走 governance | 数据可被特权删 | IaC 强制 compliance；D.4 ADR-0008 在部署 checklist 列条目；M0 dev MinIO 默认 compliance |
| Redactor 异步化破坏老 sync 调用方 | D.2 PR 大 | D.2 在同 PR 内更新所有调用点；非 audit 写路径不引入 redactor（保持 sync 测试代码不变）|
| event_log 没有 backup_acked，retention 删了未冷归档数据 | event_log 冷归档丢失 | event_log 冷归档落到 M1 / Stream G；M0 阶段 event_log retention 默认 30 天且**无 WORM 要求**（与 audit_log 区分） |

---

## 7. 里程碑 / PR 切分

每个 D.x 一 PR；每个 PR 自给自足、可独立合入 main 且 CI 绿；每 PR 收尾必须满足
[零技术债规则](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_zero_tech_debt.md)。

```
D.0  docs(stream-d): 本设计文档（即将 PR）
     - docs/streams/STREAM-D-DESIGN.md

D.1a audit_writer 角色 + REVOKE + backup_acked 列
     - migration 0008
     - AuditLogger 写路径包 SET ROLE / RESET ROLE
     - 测试：app_user 写 UPDATE/DELETE 被拒；INSERT 经 audit_writer 通过
     - CI lint pattern：grep 禁止 INSERT INTO audit_log（除 AuditLogger.sql impl）

D.1b ObjectStore Object Lock 语义
     - base.py 扩签名（retain_until, lock_mode）
     - memory.py 实现"软"锁 + ObjectLockedError
     - s3_compatible.py 翻译为 S3 头
     - tests/test_object_store_object_lock.py（unit + minio integration）

D.1c audit WORM backup worker
     - services/audit-backup-worker/（pyproject + src + tests）
     - infra/docker-compose 加 worker container
     - infra/minio 配 audit bucket Object Lock enabled
     - metrics + healthcheck
     - integration test：seed 行 → 触发 → 验证桶 + acked 列

D.2  TenantAwareRedactor + AuditLogger 接入
     - redactor.py 扩展 TenantAwareRedactor（async, 接 tenant_config_service）
     - logger.py 改造（同 PR 内同步更新调用方）
     - audit 写路径 e2e：含 ssn + jwt 的 details → mask 双中
     - 旧 DefaultSecretRedactor 保留为兜底（在 tenant_config 不可用时 fallback）

D.3  retention TTL job + tenant_config 字段
     - migration 0009：audit_retention_days / event_log_retention_days + check
     - services/retention-cleanup-job/
     - protocol/tenant_config.py 增字段
     - decisions/data-retention.md（保留 SLA）
     - 测试矩阵 #11-#13

D.4  at-rest 加密 + ADR-0008
     - adr/0008-data-at-rest-encryption.md
     - infra/docker-compose Postgres 卷加密说明（README 步骤）
     - infra/minio 切 KES + SSE-KMS（compose service）
     - infra/README.md 增 "解锁 + 启动" 流程
```

---

## 8. 横切依赖回看（自下而上验证）

| Stream D 使用的下层能力 | 来源 | 状态 |
|------|------|------|
| audit_log 表 + AuditLogger | A.4 | ✅ |
| DefaultSecretRedactor 全局 pattern | A.4 | ✅（D.2 扩展，不重写） |
| ObjectStore Protocol + S3 / Memory 实现 | A.5 | ✅（D.1b 扩展签名）|
| tenant_config 表 + TenantConfigService 60s 缓存 | C.7 | ✅（D.2 / D.3 都消费）|
| RLS baseline + audit_reader BYPASSRLS | C.4 | ✅（D.1a 在此上加 audit_writer）|
| alembic env.py + async sessionmaker | A.3 / A.7 | ✅ |
| OTel metric / structured log | A.8 / A.4 | ✅ |
| infra/docker-compose 现有服务编排 | A.1 / C.1 / C.5 | ✅ |

**前向引用**：D.2 redactor 在 Stream E.2 锚点完成后挂到 LLM middleware 链固定位；D 阶段仅做 audit 路径接入。无反向边。

---

## 9. 与 ITERATION-PLAN 对照

| Plan 项 | 本文档 PR | 备注 |
|---------|----------|------|
| D.1 审计日志不可篡改（P0 #6）| D.1a + D.1b + D.1c | 写者降权 + Object Lock + 异步 WORM 备份三件套 |
| D.2 PII redactor 中间件（P0 #7）| D.2 | per-tenant pii_fields 接入；LLM 链注册留 Stream E.2 |
| D.3 数据保留策略 + TTL（P0 #8、#19）| D.3 | tenant_config 配置 + cleanup job + 保留 SLA 文档 |
| D.4 Postgres TDE（P0 #9 at-rest）| D.4 | 走 OS/卷层 + RDS-managed；不引入 pg_tde；ADR-0008 |

完成后 Stream D 4/4。
