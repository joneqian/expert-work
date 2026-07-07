# Runbook — Postgres

> Stream G.3 故障预案。Postgres 是全系统的状态层（event_log / audit_log /
> checkpoint / tenant 配置 / quota）。主库挂 = 全系统不可用 → **P0**。
>
> M0 连接拓扑：应用经 **PgBouncer**（6432，事务池化）连库；迁移 / psql
> 直连 Postgres（5432）。设计见 [subsystems/23](../architecture/subsystems/23-postgres-scalability.md)。

## 故障现象

- control-plane / sandbox-supervisor / credential-proxy 的 `/healthz/ready`
  报 DB 依赖失败。
- 应用日志大量 `connection refused` / `too many clients` /
  `canceling statement due to statement timeout`。
- 写操作全失败（event_log append、audit 落库）。

## 诊断

1. **存活**：`docker compose exec postgres pg_isready -U expert_work`。
2. **连接数**：
   `docker compose exec postgres psql -U expert_work -d expert_work_dev -c "SELECT count(*), state FROM pg_stat_activity GROUP BY state;"`
   —— 接近 `max_connections`（100）= 连接耗尽。
3. **PgBouncer**：`docker compose ps pgbouncer`；
   `psql -h localhost -p 6432 -U expert_work pgbouncer -c "SHOW POOLS;"` 看池水位。
4. **慢查询 / 锁**：
   `SELECT * FROM pg_stat_activity WHERE state != 'idle' ORDER BY query_start;`
   查长事务；`pg_locks` 查锁等待。
5. **磁盘**：`docker compose exec postgres df -h /var/lib/postgresql/data`。

## 处置

- **连接耗尽**：先查是谁占满 —— 多为应用侧泄漏连接或长事务。
  杀长事务：`SELECT pg_terminate_backend(pid) ...`；治本是修应用侧。
  PgBouncer 事务池化已把 1000 client 收敛到 50 backend，耗尽通常意味着
  慢查询堆积。
- **PgBouncer 挂**：`docker compose restart pgbouncer`（无状态，安全）。
- **磁盘满**：清理 / 扩容；event_log 暴涨参见 [event_log 冷归档（G.8）]。
- **Postgres 进程挂**：`docker compose restart postgres`；起不来看日志，
  可能需从备份恢复（见下）。

## 恢复（数据损坏 / 主库不可恢复）

按 [subsystems/22 灾备](../architecture/subsystems/22-disaster-recovery.md) /
Stream A.6：从最近一次自动备份 + WAL 重放恢复（RPO/RTO 见该文档）。
M0 本地 dev 数据卷损坏 = `docker compose down -v` 重建（dev 数据可弃）。

## 回滚

Postgres 本身不"回滚"。若故障由一次 schema 迁移引入 →
应用对应的 Alembic downgrade，并同步回滚依赖该 schema 的服务镜像。

## 升级

P0：主库不可用且 5min 未恢复 → 立即升级；
准备走恢复流程前先确认是连接层（PgBouncer 可重启）还是 Postgres 本体。
