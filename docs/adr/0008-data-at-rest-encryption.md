# ADR-0008：数据 at-rest 加密 — OS/卷层 + 云厂托管，不引入 pg_tde

- **状态**：✅ 已决策（M0；M1 不重评 — pg_tde 评估推到 GA 后）
- **日期**：2026-05-14
- **决策依据**：Stream D.4；[GAPS § 4 #9](../architecture/07-INFRASTRUCTURE-GAPS.md#4-安全基础设施层-大部分有但分散)；[STREAM-D-DESIGN § 2.7 + Mini-ADR D-6](../streams/STREAM-D-DESIGN.md)
- **背景**：Stream D 收尾覆盖 P0 #9（at-rest 加密）。Phase 0.1 决策 3 已选阿里云全套；M0 单人项目优先用托管服务/OS 层能力，不在应用栈引入额外依赖

---

## TL;DR

- **生产 Postgres**：阿里云 RDS PostgreSQL 内置 at-rest 加密（"实例加密"开关；后端走云 KMS）
- **生产 MinIO/OSS**：SSE-KMS（OSS 服务侧加密 + 阿里云 KMS）
- **本地 dev Postgres**：宿主机 LUKS overlay 卷 bind-mount 进容器
- **本地 dev MinIO**：MinIO KES sidecar（filesystem keystore） + `MINIO_KMS_KES_*` 配置开启 SSE-KMS
- **密钥来源**：与 [ADR-0007 SecretStore](./0007-secret-store.md) 对齐 — dev 走 KES filesystem，prod 走阿里云 KMS（同一套 KMS 实例托管 LLM key + SSE-KMS master key）
- **不引入**：`pg_tde` / Percona TDE 类应用层扩展

---

## 1. 上下文

### 1.1 P0 #9（GAPS § 4）的要求

| 数据 | At-rest 加密需求 | M0 方案 |
|------|---------------|--------|
| Postgres 数据文件 | 整盘加密 | 卷/磁盘层 |
| MinIO/OSS 对象 | 服务侧加密 + 可追溯密钥 | SSE-KMS |
| 备份（audit WORM）| Object Lock + 服务侧加密 | 同 MinIO/OSS SSE-KMS（D.1c 已落） |
| 日志 / 临时文件 | 整盘加密 | 同 Postgres 卷 |

### 1.2 候选方案

| 方案 | 范围 | 复杂度 | M0 适配 |
|------|------|--------|--------|
| **OS / 卷层（LUKS / RDS 内置）** | 整盘 | 低（dev 一次性配置；prod 一键开关） | ✅ 最优 |
| **Postgres pg_tde 扩展** | per-表 加密 | 高（扩展 GA 状态不明 + 密钥管理需自托管） | ❌ M0 不上 |
| **应用层透明加密（pgcrypto + 包装库）** | per-列 / per-行 | 高（schema 迁移；查询不可走索引；性能损耗） | ❌ M0 不上 |
| **客户端加密**（应用持密钥）| 任意字段 | 中（敏感字段才用）| 仅 [F.6 SecretStore](./0007-secret-store.md) 路径里用 |

### 1.3 不引入 pg_tde 的具体理由

- Percona/EDB 的 pg_tde 仍处 beta / 早期稳定阶段，未进 PostgreSQL community
- 应用层加密 + Postgres 内置 SSE 双层在合规 / 实际威胁建模上提供不显著的额外保护：攻击面（拿到磁盘镜像）已被卷层加密覆盖
- 引入会增加密钥管理复杂度（per-tenant master key 周期）+ 数据迁移风险（升级 / 备份恢复 / 复制流）
- 真正需要"特定列保密"的场景（如 LLM key、PII 字段）通过 Stream F.6 SecretStore + Stream D.2 redactor 解决，而不是 DB 列级 TDE

---

## 2. 决策

### 2.1 Postgres

- **生产**：使用阿里云 RDS PostgreSQL 创建时打开"实例加密"；后端走云 KMS（与 ADR-0007 同一实例）
- **本地 dev**：宿主机 LUKS overlay 加密 + bind-mount 入容器：

  ```bash
  # 一次性（host 侧）
  cd infra
  sudo bash scripts/setup-luks-volume.sh         # 创建并加密 loop file
  sudo bash scripts/unlock-luks-volume.sh        # 启动前解锁
  docker compose up -d postgres pgbouncer
  ```

  bind-mount 在 `docker-compose.yml` 的 postgres service `volumes:` 段。详细步骤见 [infra/README.md § At-rest encryption](../../infra/README.md#at-rest-encryption-d4)。

### 2.2 MinIO / 对象存储

- **生产**：阿里云 OSS 创建 Bucket 时启用 SSE-KMS（同一 KMS 主密钥）
- **本地 dev**：MinIO KES sidecar + SSE-KMS：

  ```bash
  # 启用加密 dev 配置
  docker compose --profile encrypted up -d
  ```

  `--profile encrypted` 拉起 `kes` 服务（filesystem keystore）并把 `MINIO_KMS_KES_*` env 注入 MinIO。WORM 备份 bucket（D.1b/D.1c）自动获得 SSE-KMS 保护。

### 2.3 密钥管理

- 与 [ADR-0007 SecretStore](./0007-secret-store.md) 对齐：
  - **prod**：阿里云 KMS 同一实例托管 LLM key + DB Secret + SSE-KMS master key
  - **dev**：KES filesystem keystore 模拟 KMS；密钥文件 gitignored
- 轮换：
  - Postgres LUKS 密码：dev fixture，不轮换；prod RDS 由阿里云托管
  - OSS / MinIO master key：与 ADR-0007 secrets 同一轮换周期（M1 起 90 天）

### 2.4 验收门（来自 STREAM-D-DESIGN § 5 #4）

- **dev**：LUKS 卷未解锁时 `head pgdata/pg_xlog/...` 是密文（不可直接读）
- **dev**：MinIO SSE-KMS 启用后，`mc cat` 文件系统的裸 object 是密文
- **prod**：阿里云 RDS / OSS 控制台显示 "已启用加密" + KMS 主密钥 ID

---

## 3. 关键决策点（Mini-ADR D-6 内联）

| 问题 | 决策 | 理由 |
|------|-----|------|
| 用 pg_tde？ | ❌ | beta + 密钥管理增重 + 数据迁移风险；卷/RDS 层够 P0 |
| 用 pgcrypto 列加密？| ❌（M0） | 性能 + 索引不可用；M1 仅 PII 必要列考虑 |
| dev 是否强制 LUKS？| 可选（默认未加密） | 不阻塞贡献者首次跑起来；CI 不依赖 |
| KMS 用 KES 还是 Vault Transit？| KES（dev） | 与 MinIO 官方栈对齐；prod 用阿里云 KMS |
| Backup（D.1c WORM）单独加密？| ❌ | 复用 MinIO SSE-KMS；Object Lock 提供 WORM 性质 |

---

## 4. 后果

### 4.1 正面

- 整盘 / 整 bucket 一刀切；零代码改动；合规审计 + 渗透测试边界清晰
- 与 ADR-0007 + Stream D 已落的 audit WORM / D.2 redactor 完美对齐
- prod 切到云厂托管：运维负担 ≈ 0

### 4.2 负面 / 已接受

- LUKS dev fixture 增加贡献者首次启动复杂度 → 通过"加密 profile 可选 + 默认走非加密 dev 模式"缓解
- 卷层加密不覆盖"PG 进程内存被 dump"威胁 → 由 OS 进程隔离 + sandbox 边界（Stream F）兜底
- 未来 GDPR right-to-erasure 不能靠"丢掉 KMS 密钥"实现 per-tenant 删除（密钥是整库共享）→ M1 GDPR 删除 API 走 application-layer DELETE 路径

---

## 5. 引用 / 验证

- [STREAM-D-DESIGN § 2.7 + Mini-ADR D-6](../streams/STREAM-D-DESIGN.md)
- [GAPS § 4 #9](../architecture/07-INFRASTRUCTURE-GAPS.md)
- [ADR-0004 ObjectStore](./0004-object-storage.md)
- [ADR-0007 SecretStore](./0007-secret-store.md)
- [infra/README.md § At-rest encryption](../../infra/README.md#at-rest-encryption-d4)
