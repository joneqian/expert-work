# 22 Disaster Recovery（灾备）

## 1. 职责 & 边界

**是什么**：定义并执行 Helix 的备份、恢复、跨区域复制、PITR（Point-In-Time Recovery）策略，确保符合分阶段的 **RPO/RTO 目标**，并通过演练保证可用性。

**不是什么**：
- 不负责运行时高可用（HA：主从切换、负载均衡）→ [14 Sandbox Pool](./14-sandbox-pool.md) / 部署架构
- 不负责数据保留 / 合规归档 → [17 Audit Log](./17-audit-log.md)（保留是合规驱动；DR 是可用性驱动）
- 不负责 sandbox 状态恢复 → [19 Durable Execution](./19-durable-execution.md)（基于 event_log 重放）
- 不重复定义 schema / 性能优化 → [23 Postgres Scalability](./23-postgres-scalability.md)

**核心问题**：
- 单点 Postgres 数据丢失 → 整个平台无法恢复
- Vault secrets 丢失 → 所有租户凭证不可用
- 区域级故障（机房断电 / 光缆中断）→ 多小时不可用
- 备份「能跑」但「不能恢复」（never tested）

---

## 2. 上下游依赖

| 上下游 | 关系 |
|--------|------|
| [23 Postgres Scalability](./23-postgres-scalability.md) | 提供分区策略；DR 用 WAL-G / streaming replication |
| [17 Audit Log](./17-audit-log.md) | audit_log 是 Tier 0 备份对象 |
| [19 Durable Execution](./19-durable-execution.md) | 恢复后 event_log 完整性是 replay 前提 |
| [11 Credential Proxy](./11-credential-proxy.md) | Vault 是 Tier 0 备份对象（raft snapshot） |
| [18 Manifest 供应链](./18-manifest-supply-chain.md) | 签名表 + 公钥需备份；恢复后 verify 链路要可用 |
| 对象存储（S3 / OSS） | 备份落地处；需开启 Object Lock + 跨区域复制 |
| [20 Observability](./20-observability.md) | 暴露备份成功率 / 演练 RPO 实测 |

---

## 3. 数据模型 / 状态机

### 3.1 资产分级

| Tier | 资产 | RPO 目标（M2） | RTO 目标（M2） | 备注 |
|------|------|----------------|----------------|------|
| **Tier 0** 不可丢 | Postgres（event_log / manifest / audit_log / signature） | < 5 min | < 30 min | 数据丢失=合规违规 |
| **Tier 0** 不可丢 | Vault（secrets / cosign 私钥） | < 15 min | < 30 min | 丢失 = 全租户停摆 |
| **Tier 0** 不可丢 | Manifest source repo（git） | 同 git remote | < 10 min | git push 多 remote |
| **Tier 1** 可重建慢 | Sandbox image registry | < 1 h | < 4 h | 可从 CI 重新 build |
| **Tier 1** 可重建慢 | Object storage（用户 uploads） | < 1 h | < 4 h | 跨 region 复制 |
| **Tier 2** 一次性 | Sandbox 容器状态 / warm pool | 不备份 | < 5 min（重启） | 无状态设计 |
| **Tier 2** 一次性 | Redis 缓存 / 限流计数 | 不备份 | < 1 min | 重建即可 |

### 3.2 备份元数据表（Postgres）

```sql
-- 备份记录（用于恢复时定位 + 演练验证）
CREATE TABLE backup_record (
    id              BIGSERIAL PRIMARY KEY,
    asset_type      TEXT        NOT NULL,    -- 'postgres_full'|'postgres_wal'|'vault_snapshot'|'object_storage'
    asset_ref       TEXT        NOT NULL,    -- S3 URL / git ref
    started_at      TIMESTAMPTZ NOT NULL,
    finished_at     TIMESTAMPTZ,
    size_bytes      BIGINT,
    sha256          TEXT,
    status          TEXT        NOT NULL,    -- 'running'|'success'|'failed'
    error           TEXT,
    region          TEXT        NOT NULL,
    tier            SMALLINT    NOT NULL,
    UNIQUE (asset_type, asset_ref)
);
CREATE INDEX idx_backup_asset_time ON backup_record (asset_type, started_at DESC);

-- 演练记录（每季度必须 ≥1 条）
CREATE TABLE dr_drill (
    id            BIGSERIAL PRIMARY KEY,
    drill_type    TEXT        NOT NULL,      -- 'restore_postgres'|'failover_region'|'vault_restore'
    started_at    TIMESTAMPTZ NOT NULL,
    finished_at   TIMESTAMPTZ,
    rpo_actual_s  INT,                       -- 实测 RPO
    rto_actual_s  INT,                       -- 实测 RTO
    target_rpo_s  INT NOT NULL,
    target_rto_s  INT NOT NULL,
    passed        BOOLEAN,
    notes         TEXT
);
```

### 3.3 状态机：故障 → 恢复

```
[normal]  ── 备份 job 周期跑（无感知） ──► [normal]
   │
   │ 故障检测（health check / 监控）
   ▼
[degraded]  ── 评估影响范围 ──►  [need_restore?]
   │ 是
   ▼
[restoring]  ── 锁写入 / 拉取备份 / 恢复 / 校验 ──► [verifying]
   │ 通过 smoke test
   ▼
[recovered]  ── 解锁 / 切换流量 ──► [normal]
```

---

## 4. 关键接口

### 4.1 备份执行（每个 Tier 各自实现）

```python
class BackupJob(Protocol):
    asset_type: str
    schedule_cron: str
    target_region: str

    async def run(self) -> BackupRecord: ...
    async def verify(self, record: BackupRecord) -> bool: ...  # 校验 sha256 + 抽样 restore
```

实现：
- `PostgresFullBackup`（M0：pg_dump → S3；M1+：WAL-G）
- `PostgresWalBackup`（M1+：连续 WAL 推送）
- `VaultSnapshotBackup`（raft snapshot → S3）
- `ObjectStorageReplication`（S3 跨 region）
- `GitMirrorPush`（manifest repo 镜像）

**凭证来源约定**（D6）：

- **M0**：BackupJob 所需 S3 / KMS 凭证**统一从 [11 Credential Proxy](./11-credential-proxy.md) 取 `secret_ref`**（控制面以 service_account 身份调 proxy `/forward`，proxy 注入凭证）。**不直读环境变量、不从磁盘读 token**。
- **M1+**：控制面 worker 改用 IAM role（云原生短期凭证，无 secret 落地）；本文档显式声明该路径已切换；同期保留 `secret_ref` 兜底。
- 任意路径下，凭证来源**禁止**为：硬编码、未加密配置文件、`AWS_*` env var 直读。
- BackupJob 启动时 emit log：`backup.credential_source = proxy | iam_role`，便于审计追踪。

**备份 worker 的网络位置**（与 [21 § 1](./21-network-policy.md) 协调）：备份 worker **不属 sandbox 网络域**；其 outbound（S3 / KMS / Vault snapshot）走**控制面 egress allowlist**（独立策略，由 control plane 网关层实施），与 21 子系统不重叠。

### 4.2 恢复执行（命令行 + Runbook）

```python
class RestoreCommand:
    async def restore_postgres_pitr(
        self,
        target_time: datetime,
        target_db: str,         # 通常先恢复到 staging 验证
        *,
        actor_id: str,          # admin step-up auth 后的 actor
    ) -> None:
        """WAL-G base backup + WAL replay 到 target_time"""

    async def restore_vault(self, snapshot_ref: str, target_addr: str, *, actor_id: str) -> None: ...
    async def failover_to_standby(self, target_region: str, *, actor_id: str) -> None: ...
```

**强制审计 + step-up auth**（D4）：

- `restore` / `failover` / `drill` 三类**高敏感操作强制写 [17 Audit Log](./17-audit-log.md)**
- 操作前必须通过 admin **step-up auth**（再次 MFA / 二次确认；与 [15 AuthN/AuthZ](./15-authn-authz.md) 协调；failover 另需 2 人 MFA 见 § 8）
- 写入字段：
  - `actor_id`（执行者；由 [15](./15-authn-authz.md) 解出）
  - `action`：`dr:restore` / `dr:failover` / `dr:drill`（[17 § 5.1](./17-audit-log.md) 词表必含）
  - `target`：恢复目标（target_db / target_region / staging-dr cluster name）
  - `scope`：覆盖租户列表 / 时间窗口 / RPO 目标
  - `result`：`success` / `failed`（含 error message）
  - `correlation_id`：trace_id（与 OTel span `helix.dr.*` 关联）
- **审计写入失败 → 操作必须 abort**（fail-closed），避免无痕高敏感动作

### 4.3 演练执行（每季度强制）

```python
class DrillRunner:
    async def quarterly_drill(self, *, actor_id: str) -> DrillResult:
        """
        前置：admin step-up auth；写 audit_log action='dr:drill' 起始事件
        1. 选最近 backup_record（status=success）
        2. 恢复到 staging-dr 集群
        3. 跑 smoke test：Control Plane 起 / 加载 manifest / 1 个 session 跑通
        4. 测 RPO（备份时间 vs 业务最后写时间）/ RTO（开始恢复 vs smoke pass）
        5. 写 dr_drill 记录；不通过 → P1 告警
        6. 写 audit_log action='dr:drill' 结束事件（含 result + rpo_actual_s + rto_actual_s）
        """
```

---

## 5. 算法 / 关键决策

### 5.1 备份策略（按阶段）

| 阶段 | Postgres | Vault | RPO | RTO |
|------|----------|-------|-----|-----|
| **M0** | `pg_dump` 每日 → S3（versioning + Object Lock 7d） | 手动 raft snapshot 每周 | 24 h | 4 h |
| **M1** | **WAL-G** 全量周备 + WAL 连续推送 → S3 | 自动 raft snapshot 每日 | 15 min | 1 h |
| **M2** | WAL-G + 跨 region streaming replication（async） | Vault HA 集群（raft）+ 跨 region replica | < 5 min | < 30 min |

### 5.2 跨区域（M2）

- **主区域 → 备区域**：Postgres 异步 streaming replication（备区域延迟 < 30s）
- **同区域 AZ 间**：同步 replication（强一致；M2 可选）
- **failover 触发**：人工触发 + 多重二次确认（防误触）；不做完全自动 failover（split-brain 风险大）
- **数据驻留**（`tenant_config.data_residency`）：跨 region 复制需考虑——某些 tenant 数据**不可出 region**，备份目标必须在同一 region 的不同 AZ；新增 `backup_record.region` 字段做合规校验

### 5.3 备份验证

**「备份不验证 = 没备份」**：
- **每周自动 restore 到 staging-dr 集群**：跑 smoke test（Control Plane 启动、加载若干 manifest、跑 1 个 session）
- 不通过 → P1 告警 + 阻断当周新备份替换旧备份
- 校验内容：
  - sha256 完整性
  - Postgres `pg_verify_checksums`
  - 关键表行数与生产对比（容差 ±1%）
  - 索引可用 / 视图可查 / 简单查询返回结果

### 5.4 季度演练流程

每季度强制 1 次（季末前 2 周；演练时间 < 4h）：
1. 选最近 1 个有效备份
2. 在隔离环境（staging-dr）恢复
3. 切换 staging 流量到恢复实例 1 小时
4. 测 RPO/RTO，写 `dr_drill` 表
5. 写后置报告（什么慢了、什么文档不准），更新 Runbook

**演练不通过的常见原因**（提前规避）：
- 备份只跑过没恢复过 → 通过每周自动验证规避
- 恢复脚本依赖未备份的配置 → IaC 化
- 团队成员不熟悉 → 季度轮换演练操作员

### 5.5 不备份策略

明确**不备份**的资产，避免存储浪费 + 恢复混乱：
- Sandbox 容器状态：无状态设计；重启即可
- Warm pool：重启冷启动延迟 P95 < 3s（接受）
- Redis 缓存 / 限流计数：丢失只影响短窗口限流准确性
- LLM response cache：丢失只影响成本（重建即可）
- OTel trace / metric / log：保留按 [20 Observability](./20-observability.md) 自有策略；DR 不重复管

---

## 6. 失败模式 & 缓解

| 失败模式 | 影响 | 缓解 |
|---------|------|------|
| 备份 job 跑挂但无人感知 | RPO 超标 | 告警：`backup_record` 24h 内无 status=success 即 P1 |
| 备份完整但**恢复**步骤未演练 | 真灾难时手忙脚乱 | 每周自动 restore 验证 + 季度演练强制 |
| Object Lock 配置漏掉 | 备份被攻击者删除 | S3 bucket policy 强制 Object Lock 7d；CI 校验 |
| 跨 region 复制延迟漂移 | RPO 超标但未告警 | 监控 replica lag；> 60s 持续 5min 即 P1 |
| 演练在 prod 环境误操作 | 真损 prod | 演练只在 staging-dr 集群；演练账号无 prod 写权限 |
| Vault snapshot 加密密钥丢失 | snapshot 不可解密 | KMS 密钥多 region 复制 + 离线纸质备份（公司金库） |
| WAL-G 上传慢导致 RPO 超标 | 实测 RPO > 目标 | WAL-G 多线程上传；超 X 分钟无新 WAL 即 P1 |
| 备份占用空间膨胀 | 成本失控 | 压缩 + 增量；保留策略（M0：14 天；M1：30 天 + 月度归档） |
| 恢复后数据完整但**密钥不一致** | 解密失败 | KMS 密钥与备份元数据一同备份；恢复脚本先恢复 KMS |
| 跨区域恢复后 `tenant_config.data_residency` 校验失败 | 合规违规 | 备份元数据带 region；恢复前自动校验合规约束 |
| `manifest_signature` 公钥与 manifest 不同步恢复 | manifest 全部 verify 失败 | 同事务 / 同备份点恢复；smoke test 必须包含「加载 1 个签名 manifest」|
| event_log 与 audit_log 恢复时间点不一致 | session replay 与审计错位 | 同 PITR 时间点恢复；不允许选择性恢复部分表 |

---

## 7. 可观测性

> 命名规范、日志必填字段、span attrs 强制约定遵循 [20 Observability § 5.1 / § 5.3](./20-observability.md)；
> 本节只列本子系统特有的 metric / span / 日志事件。

**Metrics**（强制 `helix_dr_*` 前缀）：
- `helix_dr_backup_success_total{asset_type, region}` — 备份成功计数
- `helix_dr_backup_failure_total{asset_type, reason}` — 失败计数
- `helix_dr_backup_age_seconds{asset_type}` — 距上一次成功备份秒数（**核心 RPO SLI**；与 [20 § 5.2](./20-observability.md) 一致）
- `helix_dr_backup_size_bytes{asset_type}` — 监控膨胀
- `helix_dr_wal_replication_lag_seconds{region}` — 跨 region 延迟
- `helix_dr_drill_pass_total` / `helix_dr_drill_fail_total` — 演练统计
- `helix_dr_rpo_actual_seconds{drill_id}` / `helix_dr_rto_actual_seconds{drill_id}` — 实测对比目标

**Spans**（强制 `helix.dr.*` 前缀）：
- `helix.dr.backup_run`、`helix.dr.backup_verify`、`helix.dr.restore_run`、`helix.dr.drill_run`
- 必填 attrs（遵循 [20 § 5.1](./20-observability.md)）：`actor_id`, `asset_type`, `region`, `tier`；高敏感动作（restore / failover / drill）attrs 含 `helix.critical=true`（参 [20 § 5.5](./20-observability.md)）
- **注**：本子系统操作大多由控制面 worker 触发（无 agent 上下文），span attrs 中 `agent` / `agent_version` / `session_id` 可缺省；以 `actor_id` 作为主要主体

**Logs**（遵循 [20 § 5.3](./20-observability.md) 必填字段）：
- 高敏感动作（restore / failover / drill）写双份：[17 audit_log](./17-audit-log.md) + 结构化 INFO 日志（含 `actor_id`, `action`, `target`, `result`, `trace_id`）

**Dashboards**：
- 「Backup Health」: 各 asset_type 的 age / size / success rate
- 「DR Posture」: RPO/RTO 实测 vs 目标 + 上次演练时间
- 「Replication Lag」: 跨 region WAL lag 时序

**告警**（P0/P1）：
- P0：`helix_dr_backup_age_seconds > RPO_target * 2`
- P0：`helix_dr_wal_replication_lag_seconds > 60` 持续 5 min（M2）
- P1：连续 2 次备份失败
- P1：上次演练 > 100 天（季度演练超期）
- P1：`helix_dr_drill_fail_total` 任意

---

## 8. 安全考虑

**威胁模型**：

| 威胁 | 攻击路径 | 防御 |
|------|---------|------|
| 勒索软件加密备份 | 攻击者拿到 S3 key 删 / 加密 backups | S3 Object Lock（compliance mode）+ MFA delete + 备份账号最小权限 + 跨账号复制 |
| 备份内容被读取 | 攻击者拿到 S3 readonly | 备份**强制加密**（KMS SSE-KMS）；KMS 密钥独立 IAM；未授权 decrypt 失败 |
| Vault snapshot 解密泄漏 | 单密钥泄漏 | snapshot 用独立 KMS key（与 prod runtime 不同） |
| 恢复时凭证泄漏 | 临时 dump 在磁盘 | 恢复脚本内存中处理；落盘加密；演练完成立即清理 staging-dr |
| 演练数据泄漏 | staging-dr 用 prod 数据 | staging-dr 网络隔离 + 强制 PII redact（M2）+ 演练完成 24h 内清理 |
| 内部 rogue ops 触发恶意 failover | 单人切流量 | failover 命令需 2 人 MFA + 审计日志独立外发 SIEM |
| 高敏感 DR 动作无痕（restore / failover / drill） | 事后无法溯源 | **强制写 [17 Audit Log](./17-audit-log.md)**（actions：`dr:restore` / `dr:failover` / `dr:drill`），含 actor / target / 范围 / 结果；操作前 admin **step-up auth**；审计写失败则 abort（fail-closed） |

**密钥管理**：
- KMS key 用 **multi-region key**（M2）
- KMS key alias 命名清晰：`helix/backup/postgres-prod`
- 密钥轮换：年度；旧 key 保留 3 年用于历史备份解密

**合规**：
- 备份保留时长 ≥ `tenant_config.audit_retention_days` 中最大值（HIPAA 7 年）
- 数据驻留约束在备份元数据校验：跨 region 复制不得违反 `data_residency`

---

## 9. M0 / M1 / M2 演进

### M0（4-6 周）— 备份骨架 + 恢复脚本 + 1 次演练

**交付**：
- `backup_record` / `dr_drill` 表
- Postgres `pg_dump` 每日（cron）→ S3 + versioning + Object Lock 7d
- Vault raft snapshot 每周（手动 OK）
- manifest repo git push 双 remote（公司 + 备份）
- 恢复 Runbook（文档）
- M0 末跑 1 次完整演练（人工 OK）：RPO 目标 24h / RTO 目标 4h
- 备份失败告警

### M1（6-8 周）— PITR + 自动验证 + 季度演练

**交付**：
- 切 **WAL-G**：全量周备 + WAL 连续推送
- Vault 自动 raft snapshot（每日）+ S3 跨账号复制
- **每周自动 restore 验证**（staging-dr）
- 季度演练流程化（自动 → 报告）
- RPO 15 min / RTO 1 h 达标
- 备份元数据带 `region` / `tier` 字段（合规校验前置）

### M2（8-10 周）— 跨区域 + 自动 failover 准备 + 合规

**交付**：
- Postgres async streaming replication（跨 region；同 AZ 同步可选）
- Vault HA 集群（raft）+ 跨 region replica
- KMS multi-region key
- 数据驻留约束自动校验（违规备份阻断）
- failover 半自动化：监控发现 → 人工 2-MFA 触发 → 切换 < 30 min
- RPO < 5 min / RTO < 30 min 达标

---

## 10. 开放问题

1. **是否上 Patroni / pg_auto_failover 做自动 failover？** 自动 failover 风险高（split-brain）；M2 暂留人工触发。

2. **Vault 是否切 Cloud KMS（KMS auto-unseal）**：解决 unseal key 管理痛点，但增加云依赖。**当前倾向**：M1 评估；M2 必上。

3. **多 tenant 数据驻留 region 多于 2 个时如何 DR**：每 region 独立备份链 → 成本翻倍。**当前倾向**：仅高合规 tenant 启用 region pinning，其他统一主区域。

4. **演练频率**：季度 vs 月度？月度成本高但更可靠。**当前倾向**：季度全量演练 + 月度小规模（仅 Postgres restore）。

5. **冷归档**（event_log 超过 6 个月） 与 DR 备份是否同盘？**当前倾向**：分离——归档是合规 + 成本目的，DR 是可用性目的。详见 [23 Postgres Scalability](./23-postgres-scalability.md)。

6. **备份文件加密的密钥轮换** 期间，旧备份如何解密？**当前倾向**：旧 KMS key 设 `pending_deletion` 状态保留 3 年；恢复时按备份元数据 `kms_key_id` 选 key。
