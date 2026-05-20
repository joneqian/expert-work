# Postgres restore — Stream K.K15 entry point

> 把 [`docs/dr/RUNBOOK.md`](../dr/RUNBOOK.md) 的现有 procedure 放进
> `docs/runbooks/` 命名空间，加上 K15 引入的自动化演练入口。

## 谁是源真书？

* **`docs/dr/RUNBOOK.md`** —— Postgres 全量恢复的完整 procedure：
  pre-flight、识别 `backup_record` 行、SHA-256 校验、step-up auth、
  dev / prod 命令、验证清单、回滚到生产。**先读它**。本文件只是入口。
* **`docs/architecture/subsystems/22-disaster-recovery.md`** —— RPO/RTO
  目标（M0：24 h / 4 h；M1：15 min / 1 h with WAL-G）+ 备份频次设计。
* **`docs/runbooks/postgres.md`** —— 日常 PG 故障预案（非全量恢复路径）。

## K.K15 contribution

* **`tools/persistence/test_pg_restore_drill.py`** —— 自动化 pg_dump
  + pg_restore round-trip drill。CI 每次 `Test (integration)` job 跑：
  种子 → dump → drop → restore → 校验行数 + body intact + RTO ceiling。
  schema 回归（pg_dump 不能复原我们的表结构 / extensions 顺序错 /
  CREATE DATABASE 与 bootstrap init 之间排序错）会在 CI 红，不用等季度
  DR 演练。
* drill 本地能跑：`DOCKER_HOST=$(docker context inspect $(docker context show) -f '{{.Endpoints.docker.Host}}') uv run --frozen pytest tools/persistence/test_pg_restore_drill.py`
  ——需要 Docker daemon 在线（testcontainers 起 pgvector/pgvector:pg16）。

## RTO / RPO 实测

K.K15 drill 在 testcontainers + 本地 docker 上把 dump + restore 闭环
压在 **< 60 s**（drill 本身的 ceiling assertion）。生产 RTO 上界由 S3
传输时间主导 —— RTO target 4 h 的实测落地 staging-dr 数据库定期演练
（[subsystems/22 § 5.4](../architecture/subsystems/22-disaster-recovery.md#54-演练验证)），
本仓库 drill 锁定的是**数据路径正确性**。

## When to use

* Primary Postgres 不可恢复（disk loss / corrupt schema / 误 `DROP`）
* DR 演练（quarterly）—— 同样跑 `docs/dr/RUNBOOK.md` procedure，但
  在 staging-dr 库
* CI 自动跑 drill —— 不需要操作员介入，每 PR 都验证

## Related

* [`audit-restore.md`](audit-restore.md) —— audit_log 行的 WORM 恢复
  （K.K14）；audit 表本身先由本 procedure 恢复，再用 audit-restore
  再次写入更老的 WORM 行。
* [`deployment.md`](deployment.md) / [`tools/deploy/rollback.py`](../../tools/deploy/) ——
  代码层 rollback，恢复**之前**先确认是不是这条路。
