# 部署与发布 — Stream I

> 落实 P0 #32（服务发布策略）/ #33（服务回滚）。设计见 [STREAM-I-DESIGN § 6–8](../streams/STREAM-I-DESIGN.md)。
>
> 覆盖 Stream I 全程：三环境矩阵、配置来源、首次部署、滚动发布、回滚、迁移兼容、发布检查清单。

M0 在线栈跑在单台主机的 `docker compose --profile full up` 上。control-plane 在 ADR B-6（SQL store 切换）后**无状态** —— 蓝绿两色（`control-plane-blue` / `control-plane-green`）可并行连同一套数据库，发布与回滚都靠切换 nginx upstream 完成。

## 三环境矩阵

| 维度 | dev | staging | prod |
|------|-----|---------|------|
| 载体 | 本机 docker-compose（OrbStack/Lima）| 单台阿里云 ECS + docker-compose | 阿里云 ECS + docker-compose（独立实例）|
| Postgres | compose `postgres` 容器 | 阿里云 RDS（staging 库）| 阿里云 RDS（prod 库，独立实例）|
| 对象存储 | compose MinIO | 阿里云 OSS（staging bucket）| 阿里云 OSS（prod bucket）|
| Secret | `local_dev`（`infra/credential-proxy/secrets.env` 占位）| 阿里云 KMS Secrets Manager（ADR-0007）| 阿里云 KMS Secrets Manager |
| 镜像来源 | 本地 `docker build` | 阿里云 ACR | 阿里云 ACR |
| TLS 证书 | 自签（`tools/dev-certs/`）| 内部 CA / 真证书 | 真证书 |
| `HELIX_AGENT_STORE_BACKEND` | `sql` | `sql` | `sql` |
| `HELIX_AGENT_SINGLE_INSTANCE` | `true`（不蓝绿）| `false` + Redis（蓝绿需要）| `false` + Redis |
| 发布方式 | `compose up`（无蓝绿）| `deploy.py` 蓝绿 | `deploy.py` 蓝绿 + 金丝雀 |

**环境隔离（P0）**：三环境的 DB / 密钥 / bucket 完全分离，命名一律带环境后缀（`helix_agent_dev` / `_staging` / `_prod`）防误连；prod 凭据只存 KMS，不落盘、不进仓库。

## 配置来源与优先级

三处配置源，由结构化到敏感：

1. **`environments/<env>.yaml`** —— 结构化非密配置（DB host、OSS endpoint、observability、TLS 路径、`secrets.backend` 选型）。声明式，目前由 `tools/tls/check_tls_config.py`（`tls:` 段）和 SecretStore 工厂（`secrets.backend`）消费。
2. **`HELIX_AGENT_*` 环境变量** —— control-plane 的 pydantic `Settings` **只**读真实环境变量（不读 yaml）。compose 文件 / 部署脚本注入：`HELIX_AGENT_DB_DSN`、`HELIX_AGENT_STORE_BACKEND`、`HELIX_AGENT_SINGLE_INSTANCE`、`HELIX_AGENT_QUOTA_REDIS_URL` 等。
3. **阿里云 KMS Secrets Manager** —— 运行期密钥（模型 key、DB 密码、上游凭据），staging / prod 经 SecretStore（ADR-0007）拉取，永不落盘。dev 用 `local_dev` 占位文件。

## 首次部署

**dev** —— 见 [`infra/README.md`](../../infra/README.md)。预构建沙盒镜像后 `docker compose --profile full up`；要蓝绿 / nginx 再加 `--profile proxy`。

**staging / prod**：

1. 备好 `environments/<env>.yaml` + 注入 `HELIX_AGENT_*` 环境变量（DSN 指向 RDS；`STORE_BACKEND=sql`；`SINGLE_INSTANCE=false` + `QUOTA_REDIS_URL`）。
2. 从阿里云 ACR 拉 `helix-control-plane` / `helix-sandbox-supervisor` / `helix-credential-proxy` 镜像。
3. 起数据层（staging/prod 的 Postgres 是 RDS，不起 compose 的 `postgres` / `pgbouncer`；redis 仍由 compose 起）。
4. 跑 `migrate`（`alembic upgrade head`，纯 expand —— 见下）。
5. 蓝绿起 control-plane：首次直接 `docker compose up -d control-plane-blue` + 起 nginx；之后的版本走 `deploy.py`。
6. 起 sandbox-supervisor / credential-proxy。
7. 验证：`/healthz/ready` 全绿、nginx 经 8443 mTLS 可达、Stream G SLO 大盘有数据。

## 滚动发布（蓝绿）

```
python tools/deploy/deploy.py --tag <new-image-tag>
python tools/deploy/deploy.py --tag <new-image-tag> --canary 10,50 --canary-pause 60
```

`deploy.py` 的步骤：重建 idle 色（带新 tag）→ 等其 `/healthz/ready` → 可选加权金丝雀步进 → 翻 nginx upstream + `nginx -s reload` → drain 旧色（**停止但保留容器**，供下方快路径回滚）。

含 schema 变更的发布：先跑 `migrate`（只做 expand，见下）再 `deploy.py`。

## 回滚

```
python tools/deploy/rollback.py                  # 快路径
python tools/deploy/rollback.py --to-tag v1.2.2  # 兜底路径
```

- **快路径（默认）** —— 上一次 `deploy.py` 把旧色容器**停止保留**。`rollback.py` 重启该容器、等 `/healthz/ready`、把 nginx upstream 切回旧色 + reload、drain 当前（坏）色。秒级流量切换，不拉镜像、不重建。
- **兜底路径（`--to-tag`）** —— 旧色容器已被后续发布顶掉，或要回到任意更早版本。按指定 tag 跑一次完整蓝绿部署（复用 `deploy.py`）。

回滚之所以安全，依赖下面的迁移兼容纪律：旧 control-plane 镜像必须能跑在**当前** schema 上。

## 数据库迁移兼容（expand-contract）

回滚切回旧 control-plane 镜像后，旧代码要能跑在当前 schema 上。M0 用 **expand-contract** 纪律保证 —— 迁移**只向前**，不用 `alembic downgrade`：

- **expand（可随发布走）**：加列（nullable 或带 default）、加表、加索引（`CREATE INDEX CONCURRENTLY`）、加宽类型。旧代码看不见新列也能跑 → 对旧代码恒兼容。
- **contract（单独发布、滞后）**：删列、改名、加 `NOT NULL`、窄化类型。只能在「依赖旧 shape 的代码已全部下线」之后的**单独一个发布**里做。
- **推论**：任一发布 v(n) 的 schema 对 v(n−1) 代码恒兼容 → 蓝绿回滚到 v(n−1) 镜像永远安全。
- **不用 `alembic downgrade`**：生产降级迁移可能丢数据、且降级脚本几乎不被测；兼容性靠「只 expand」实现，不靠回滚 schema。

> 完整的零停机迁移规范（在线建索引、分批 backfill、迁移 linter / CI 自动拦 contract 迁移）属 M1-B。本阶段只立规则 + 在下方清单设强制检查点。

## 环境差异与坑

- **dev 的 redis 占宿主 6379** —— 若宿主已跑 redis，集成测试用 `HELIX_TEST_COMPOSE_OVERRIDE` 指向一个 `redis: {ports: !reset []}` 的 override 文件（CI 无此冲突）。
- **staging / prod 必须 `HELIX_AGENT_SINGLE_INSTANCE=false` + `HELIX_AGENT_QUOTA_REDIS_URL`** —— 蓝绿切换窗口内两个色并存，进程内限流器会各算一份；切到 Redis 后端后多进程计数才正确（STREAM-I-DESIGN § 6.4）。
- **staging / prod 的 Postgres 是 RDS** —— 不起 compose 的 `postgres` / `pgbouncer`；`HELIX_AGENT_DB_DSN` 直接指向 RDS endpoint，`environments/<env>.yaml` 的 `database.host` 同步。
- **prod 密钥只走 KMS** —— `secrets.backend: aliyun_kms`；不写 `.env`、不进仓库、不落容器磁盘。
- **沙盒镜像需预构建** —— `helix-sandbox:dev`（或对应 tag）由 sandbox-supervisor 运行期 `docker run`，compose 不替它构建，部署前先 `docker build`（见 STREAM-I-DESIGN § 2.4）。

## 发布检查清单

每次发布前逐项确认：

- [ ] 新镜像对应的提交 CI 8/8 全绿。
- [ ] 本次迁移是否**纯 expand**？含 contract 变更则确认依赖旧 shape 的代码已下线 ≥ 1 个发布。
- [ ] `environments/<env>.yaml` 与目标环境已核对（I.4 增补环境矩阵后逐项对照）。
- [ ] 金丝雀阶段已观察 [Stream G 的 SLO 大盘](./slo.md)，错误率 / 延迟无异常再继续步进。
- [ ] 发布后旧色容器仍保留（`docker compose ps` 可见、状态 `exited`），回滚快路径可用。
- [ ] 回滚预案明确：知道用快路径还是 `--to-tag`，旧 tag 已记录。
