# PR1 数据合规:软删生命周期闭环 — 设计文档

> 删除接口卫生修复计划(2026-07-24 全量审计)第 1 批,共 5 批。
> 本批主题:让"删了"最终真的等于"没了"——软删数据有确定的物理清除时刻,purge 用户不留孤儿资产。

## 背景(审计结论,均有代码证据)

1. **记忆"忘记"是永久软删**:`memory.py` 文件头承诺 "a future retention sweep
   hard-deletes 30+ days after",该 sweep 从未实现;全仓库没有任何代码物理删除
   `memory_item` 行。用户记忆的明文 + embedding 向量(同行同列)永久留库。
2. **purge_user 删图片行留不可自愈孤儿 blob**:`user_purge.py` 对 `image_upload`
   裸 DELETE 行,从不删对象存储 key;retention sweep 靠行内 `object_key` 定位
   blob,行没了 blob 永远无人认领。
3. **feedback 表漏网**:`feedback`(👍/👎 + 自由文本评论,按 thread_id 挂靠,无 FK)
   完全不在 purge_user 编排里,用户会话删了评论永留。
4. **工作区归档后 90 天硬删未实现**(即 Phase 3b):`lifecycle.py` 注释自认
   "physical hard-delete after the 90-day retention is M1 work"。归档 tar.gz
   在 ObjectStore 无限期留存,`user_workspace` 行永不清。
5. **图片 blob 清扫只锚 `created_at`**:用户手动删除的图片,blob 仍要躺到创建满
   `image_retention_days`(默认 90 天)。
6. **retention job 的 DB 授权欠账**:migration 0010 只给 `retention_cleanup_worker`
   授了 audit_log/event_log/jwt_blacklist;后来加的 image/artifact/approval pass
   从未补 grant(dev 用超级用户连接掩盖了这一点)。

## 用户拍板(2026-07-24)

| # | 决策 | 结论 |
|---|------|------|
| D1 | 记忆物理硬删宽限期 | **统一 90 天**,单旋钮。满足代码注释"30+ 天"承诺,保住 purge"90 天内可恢复"窗口 |
| D2 | feedback 处置 | **随会话硬删**(purge 枚举用户 thread 时连带删这些 thread 的 feedback 行) |
| D3 | 手动删除的图片 blob | **下次夜扫即清**(软删行不再等 created_at 满 90 天) |
| D4 | purge 后的 tenant_user 行 | **90 天后物理硬删**(恢复窗口过后 subject_id 这个 PII 不再留库;已验证全库零 FK 引用 tenant_user,硬删安全) |

维持不变的既有设计(不是 bug,勿"修"):

- purge_user 对 memory 走**软删**是 Phase 3a 拍板的"90 天可恢复"语义,保留;
  真正缺的是本批新增的 90 天后物理清扫。
- `tenant_user.resolve()` 清 `deleted_at` 的复活语义保留——sweep 只清扫
  仍处于 deleted 状态满 90 天的行,复活过的行 `deleted_at` 已为 NULL,天然不会命中。

## 设计

### A. MemoryStore 硬删能力(persistence)

新增抽象方法(SQL + in-memory 双实现,**谓词逐字节一致**——I-1 教训):

```python
async def hard_delete_expired(self, *, before: datetime, limit: int) -> int:
    """物理 DELETE 满足 deleted_at IS NOT NULL AND deleted_at < before 的行,
    跨租户(retention job 全局清扫),返回删除行数。"""
```

- 只看 `deleted_at`(单条 forget 与 purge_user 软删都写这一列),`expired_at`
  (transient 记忆过期)不属于本清扫,不碰。
- `memory.py` 文件头 docstring 更新为真实行为(90 天,由 retention job 执行)。
- purge_user 的 memory 步**不改**(见上,软删即 90 天恢复窗)。

### B. FeedbackStore 清理能力 + purge_user 接线

```python
async def delete_for_threads(self, *, tenant_id: UUID, thread_ids: Sequence[UUID]) -> int:
    """物理 DELETE 指定 thread 的全部 feedback 行,返回行数。"""
```

- SQL + in-memory 双实现。
- `user_purge.py` 的 `_purge_threads` 已枚举该用户全部 thread;在逐 thread
  删除**之前**先收集 thread_id 列表并调用 `delete_for_threads`(feedback 无 FK,
  顺序不影响正确性,先删是为了 thread 删除中途失败时不留"会话没了评论还在"的中间态)。
- `PurgeSummary.deleted["feedback"]` 计数;失败走既有 `_step` 容错模式。

### C. purge_user 图片 blob 清理

- `PurgeUserDeps` 新增 `object_store: ObjectStore | None` 字段;
  `agent_users.py` 组装 deps 时从 `app.state.object_store` 取(app.py:1240 已有)。
- image 步改为:`list_for_user` 取行(含 `object_key`)→ 逐 key
  `object_store.delete` best-effort(失败不阻断,计入 summary,模式照抄
  retention job `_delete_expired_images` 的 keys_ok/keys_failed)→ 再硬删行。
- `object_store` 为 None(未接对象存储的部署/测试)时保持只删行,但 summary
  里记 `image_blobs_skipped`,不再静默。

### D. 图片 blob 按 deleted_at 清(retention job)

- `ImageUploadStore.list_expired(before)` 谓词由 `created_at < before` 改为
  `(created_at < before) OR (deleted_at IS NOT NULL)`,方法更名 `list_reapable`
  以免旧语义误用(全仓调用点仅 retention job 一处 + 测试)。
- in-memory 实现同步改,谓词一致。
- 效果:软删行下一次夜扫即"blob + 行"一起走,未删行维持 90 天 created_at 视界。

### E. 工作区归档 90 天硬删(Phase 3b 主体)

`UserWorkspaceStore` 新增:

```python
async def list_archived_expired(self, *, before: datetime, limit: int) -> list[UserWorkspace]:
    """deleted_at IS NOT NULL AND deleted_at < before AND archived_object_key IS NOT NULL"""

async def hard_delete(self, *, workspace_id: UUID) -> bool:
    """物理 DELETE user_workspace 行。"""
```

retention job 新 pass `_sweep_workspaces`:

1. `list_archived_expired(before=now - workspace_archive_retention_days, limit=batch)`
2. 逐行:`object_store.delete(archived_object_key)` best-effort(失败计数、
   照 image pass 的取舍——孤儿 key 好过永远卡住的行,但失败当次**不删行**,
   下次夜扫重试,避免制造"行没了 key 找不到"的第 2 号问题)
3. key 删成功(或 ObjectStore 返回不存在)→ `hard_delete(workspace_id)`

- `workspace_archive_retention_days` 默认 90,settings + env 注入,照
  `artifact_hard_delete_grace_days` 先例。
- 软删但归档一直失败的行(`archived_object_key IS NULL`)不清、只计数上报
  (归档重试归 supervisor 的 DLQ 管,retention job 不越界)。
- 每日备份(`workspace_backup_prefix` 日期前缀)的滚动清理**不在本批**,维持
  runbook 手动 prune 现状。

### F. tenant_user 90 天后硬删(retention job)

`TenantUserStore` 新增:

```python
async def hard_delete_deactivated(self, *, before: datetime, limit: int) -> int:
    """物理 DELETE deleted_at IS NOT NULL AND deleted_at < before 的行,跨租户。"""
```

retention job 新 pass 调用之。只有 purge_user 会写 `tenant_user.deleted_at`,
所以清扫范围恰好 = 被清除且 90 天内未复活的用户。已验证:全库迁移与 ORM 模型
中**没有任何 FK 引用 tenant_user**,硬删无约束风险。

### G. grants 迁移(一次补齐)

新 migration,照 0010 的逐表单语句风格,授予 `retention_cleanup_worker`:

- 本批新增:`memory_item`(SELECT/DELETE)、`user_workspace`(SELECT/DELETE)、
  `tenant_user`(SELECT/DELETE)
- 补历史欠账:`image_upload`(SELECT/DELETE)、`artifact` + `artifact_version`
  (SELECT/UPDATE/DELETE,soft_delete 需 UPDATE)、`agent_approval`
  (SELECT/UPDATE,mark_decided 是 UPDATE)、`feedback` 不需要(purge 路径走
  control-plane 应用角色,不走 worker 角色)

### H. CleanupReport / PurgeSummary 扩展

- `CleanupReport` 新增:`memory_hard_deleted`、`workspaces_hard_deleted`、
  `workspace_archives_removed`、`workspace_archives_failed`、
  `workspaces_pending_archive`(卡住计数)、`tenant_users_hard_deleted`。
- `PurgeSummary` 新增:`feedback` 删除计数、`image_blobs_removed` /
  `image_blobs_failed` / `image_blobs_skipped`。
- 审计:retention job 现状无 AuditLogger 接线,本批**不引入**(与 Phase 3a plan
  提过的 `WORKSPACE_HARD_DELETE` 审计动作有出入——引入整套 audit 管线进一个
  夜间 job 是超范围工程,清扫结果以 CleanupReport 结构化日志留痕;后续要审计
  再单独立项)。purge_user 侧的 `USER_PURGE` 审计已有,summary 新字段自然入审计 details。

## 错误处理

- 所有 ObjectStore 删除:best-effort + 失败计数 + `logger.exception`,永不阻断行清理
  (workspace pass 例外:key 删失败当次保留行,下次重试——因为行是找回 key 的唯一线索)。
- 所有新 store 方法带 `limit` 批量上限,复用 job 的 `batch_size`,单次夜扫不长事务。

## 测试

- **persistence 集成测试(真 Postgres 容器,`DOCKER_HOST` 前置)**:
  `hard_delete_expired` / `list_reapable` / `delete_for_threads` /
  `list_archived_expired` + `hard_delete` / `hard_delete_deactivated`,
  每个方法含边界(恰好 90 天、复活行不命中、跨租户)。
- **SQL ↔ in-memory 平价测试**:同一 fixture 数据 + 同一断言集跑双实现(谓词
  一致性是历史命门)。
- **retention job 单测**:每个新 pass 用 in-memory store,含 ObjectStore 删失败
  分支(workspace 行保留 vs image 行照删的不同取舍各自断言)。
- **purge_user 测试**:blob 真删(memory ObjectStore 断言 key 消失)、feedback
  行消失、object_store=None 时 skipped 计数、summary 字段全核对。
- **变异自验**:实现后手动破坏关键谓词(如去掉 `deleted_at IS NOT NULL`)确认测试变红。

## 范围外(后续批次)

- PR2 安全洞(role_binding / SecretStore.delete / MCP OAuth 级联)
- PR3 单资源删除级联(trigger_run / webhook_delivery / purge_session / curation / knowledge 竞态)
- PR4 删除前置检查(模板 extends / agent 软删依赖 / MCP server 引用缝)
- PR5 成员页员工清除入口
- artifact 字节级 GC、skill 资产 GC、每日备份滚动清理(维持现状,已明确不做)
