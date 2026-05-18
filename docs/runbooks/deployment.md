# 部署与发布 — Stream I

> 落实 P0 #32（服务发布策略）/ #33（服务回滚）。设计见 [STREAM-I-DESIGN § 6–8](../streams/STREAM-I-DESIGN.md)。
>
> 本文档当前覆盖 **I.3**：滚动发布与回滚流程、数据库迁移兼容（expand-contract）、发布检查清单。
> **I.4** 增补三环境（dev / staging / prod）矩阵、配置来源、首次部署步骤。

M0 在线栈跑在单台主机的 `docker compose --profile full up` 上。control-plane 在 ADR B-6（SQL store 切换）后**无状态** —— 蓝绿两色（`control-plane-blue` / `control-plane-green`）可并行连同一套数据库，发布与回滚都靠切换 nginx upstream 完成。

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

## 发布检查清单

每次发布前逐项确认：

- [ ] 新镜像对应的提交 CI 8/8 全绿。
- [ ] 本次迁移是否**纯 expand**？含 contract 变更则确认依赖旧 shape 的代码已下线 ≥ 1 个发布。
- [ ] `environments/<env>.yaml` 与目标环境已核对（I.4 增补环境矩阵后逐项对照）。
- [ ] 金丝雀阶段已观察 [Stream G 的 SLO 大盘](./slo.md)，错误率 / 延迟无异常再继续步进。
- [ ] 发布后旧色容器仍保留（`docker compose ps` 可见、状态 `exited`），回滚快路径可用。
- [ ] 回滚预案明确：知道用快路径还是 `--to-tag`，旧 tag 已记录。
