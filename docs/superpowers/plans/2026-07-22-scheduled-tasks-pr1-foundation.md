# 对话驱动定时任务 — PR1 地基(F)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 `agent_trigger` 打 per-user 定时任务地基 —— 唯一约束分 user、RRULE + IANA 时区调度(向后兼容旧 cron)、投递路由字段、`list_by_user`、堵触发器 API 所有权安全洞。

**Architecture:** 纯后端(persistence + control-plane),不含对话工具/投递/前端(那是 PR2/3/4)。schema 迁移 + ORM/DTO 加字段 + 两 store 后端(SQL/memory)对称改 + scheduler 双路径(rrule 优先,回落 legacy cron)+ API ownership 校验。所有改动向后兼容:现存 `config['expr']` cron 触发器继续工作。

**Tech Stack:** Python 3.12、SQLAlchemy async + Alembic、Pydantic v2(frozen DTO)、`dateutil.rrule`(新增直接依赖)、`zoneinfo`、pytest(integration 走 testcontainers Postgres)。

## Global Constraints

- **Integration 测试须 Docker**:`export DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock`;所有测试用 `uv run`(裸 python 报 `No module named ...`)。
- **Alembic revision id ≤ 32 字符**(`version_num` 列上限)。本 PR 用 `revision = "0130_trigger_user_scope"`(23 字符),`down_revision = "0129_tenant_cfg_predictive"`。
- **`python-dateutil` 是新直接依赖** → 加到 `services/control-plane/pyproject.toml` 的 `dependencies` 数组(现只 croniter;dateutil 仅 transitive 在 uv.lock,直接 import 未声明会违规)。
- **向后兼容**:scheduler 必须继续支持 legacy `config['expr']` cron 触发器(双路径);不数据迁移旧行、不破坏现存触发器。
- **`kind` 保留 `'cron'`**:cron-expr 与 rrule 触发器都是 `kind='cron'`(时间调度类);调度载荷在 `config`。CHECK `kind IN ('cron','webhook')` 不变。
- **提交无署名**:提交信息末尾**不加** `Co-Authored-By` / 🤖 行(本仓全局禁署名)。
- **CI 范围**:mypy 扫 tests;ruff 跑全库。改完跑 CI 同款范围,勿只测单文件。
- **提交信息用中文 conventional commits**(`feat:` / `fix:` / `refactor:`),与仓库现有风格一致。

---

## File Structure

| 文件 | 职责 | 动作 |
|------|------|------|
| `packages/expert-work-persistence/migrations/versions/0130_trigger_user_scope.py` | schema 迁移:去旧唯一约束、加双 partial unique index、加 `originating_thread_id`/`context_mode` 列 + CHECK | Create |
| `packages/expert-work-persistence/src/expert_work/persistence/models/agent_trigger.py` | ORM 行模型 `__table_args__` 与新列(镜像迁移) | Modify |
| `packages/expert-work-protocol/src/expert_work/protocol/trigger.py` | `TriggerRecord` 加 `originating_thread_id`/`context_mode` 字段 | Modify |
| `packages/expert-work-persistence/src/expert_work/persistence/trigger/sql.py` | `_row_to_dto`/`create`/`update` 映射新字段;新 `list_by_user` | Modify |
| `packages/expert-work-persistence/src/expert_work/persistence/trigger/memory.py` | user-scoped 唯一;新 `list_by_user` | Modify |
| `packages/expert-work-persistence/src/expert_work/persistence/trigger/base.py` | `TriggerStore` ABC 加 `list_by_user` 抽象方法 | Modify |
| `services/control-plane/pyproject.toml` | 加 `python-dateutil` 直接依赖 | Modify |
| `services/control-plane/src/control_plane/scheduler.py` | `_next_occurrence` 双路径 rrule/cron + 耗尽自动停用 | Modify |
| `services/control-plane/src/control_plane/api/triggers.py` | GET/PATCH/DELETE ownership 校验;LIST 非 admin 只看自己 | Modify |
| `packages/expert-work-persistence/tests/test_migration_0130_trigger_user_scope.py` | 迁移测试 | Create |
| `packages/expert-work-persistence/tests/test_sql_trigger_store.py` | SQL store 新字段/唯一/list_by_user 测试 | Modify |
| `packages/expert-work-persistence/tests/test_in_memory_trigger_store.py` | memory store 同上 | Modify |
| `services/control-plane/tests/test_scheduler.py`(或现有 scheduler 测试文件) | `_next_occurrence` 单测 | Modify/Create |
| `services/control-plane/tests/test_triggers_api.py` | ownership/LIST 安全测试 | Modify |

---

### Task 1: 迁移 0130 —— 唯一约束分 user + 投递路由列

**Files:**
- Create: `packages/expert-work-persistence/migrations/versions/0130_trigger_user_scope.py`
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/models/agent_trigger.py`
- Test: `packages/expert-work-persistence/tests/test_migration_0130_trigger_user_scope.py`

**Interfaces:**
- Consumes: 现有 `agent_trigger` 表(migration 0033),head revision `0129_tenant_cfg_predictive`。
- Produces: `agent_trigger` 表新增列 `originating_thread_id UUID NULL`、`context_mode TEXT NOT NULL DEFAULT 'fresh_thread_per_run'`(CHECK in `('reuse_thread','fresh_thread_per_run')`);删 `agent_trigger_name_uniq`;新 partial unique index `ix_agent_trigger_user_name_uniq`(`user_id IS NOT NULL`)+ `ix_agent_trigger_null_user_name_uniq`(`user_id IS NULL`)。ORM `AgentTriggerRow` 镜像。

- [ ] **Step 1: 写迁移测试(失败)**

Create `packages/expert-work-persistence/tests/test_migration_0130_trigger_user_scope.py`:

```python
"""迁移 0130 —— agent_trigger user 维度唯一 + 投递路由列。"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"
_PRE = "0129_tenant_cfg_predictive"
_MIGRATION = "0130_trigger_user_scope"


def _sync_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql+psycopg://")


@pytest.mark.integration
def test_migration_0130_schema() -> None:
    with PostgresContainer("pgvector/pgvector:pg16") as container:
        dsn = _sync_dsn(str(container.get_connection_url()))
        cfg = Config(str(ALEMBIC_INI))
        cfg.set_main_option("sqlalchemy.url", dsn)
        command.upgrade(cfg, _MIGRATION)

        engine = sa.create_engine(dsn)
        with engine.connect() as conn:
            cols = {
                r[0]
                for r in conn.execute(
                    sa.text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'agent_trigger'"
                    )
                )
            }
            assert "originating_thread_id" in cols
            assert "context_mode" in cols

            indexes = {
                r[0]
                for r in conn.execute(
                    sa.text("SELECT indexname FROM pg_indexes WHERE tablename = 'agent_trigger'")
                )
            }
            assert "ix_agent_trigger_user_name_uniq" in indexes
            assert "ix_agent_trigger_null_user_name_uniq" in indexes

            constraints = {
                r[0]
                for r in conn.execute(
                    sa.text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conrelid = 'agent_trigger'::regclass"
                    )
                )
            }
            assert "agent_trigger_name_uniq" not in constraints  # 旧唯一约束已删
        engine.dispose()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock uv run --project packages/expert-work-persistence pytest packages/expert-work-persistence/tests/test_migration_0130_trigger_user_scope.py -v`
Expected: FAIL —— `alembic.util.exc.CommandError: Can't locate revision identified by '0130_trigger_user_scope'`。

- [ ] **Step 3: 写迁移**

Create `packages/expert-work-persistence/migrations/versions/0130_trigger_user_scope.py`:

```python
"""agent_trigger user 维度 —— 唯一约束分 user + 投递路由列(Spec 1 PR1).

Revision ID: 0130_trigger_user_scope
Revises: 0129_tenant_cfg_predictive
Create Date: 2026-07-22

去 (tenant, agent_name, name) 全局唯一 → 双 partial unique index:
非空 user_id 含 user_id(两用户可同名任务),空 user_id 保留按名唯一
(manifest/legacy 无主任务)。加 originating_thread_id / context_mode
(投递路由,PR3 D1 用),context_mode 默认 fresh_thread_per_run(现行为)。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0130_trigger_user_scope"
down_revision: str | Sequence[str] | None = "0129_tenant_cfg_predictive"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_CONTEXT_MODES = "('reuse_thread', 'fresh_thread_per_run')"


def upgrade() -> None:
    # 1. 投递路由列。
    op.add_column(
        "agent_trigger",
        sa.Column("originating_thread_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "agent_trigger",
        sa.Column(
            "context_mode",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'fresh_thread_per_run'"),
        ),
    )
    op.create_check_constraint(
        "agent_trigger_context_mode_valid",
        "agent_trigger",
        f"context_mode IN {_CONTEXT_MODES}",
    )

    # 2. 去全局唯一约束,换双 partial unique index。
    op.drop_constraint("agent_trigger_name_uniq", "agent_trigger", type_="unique")
    op.create_index(
        "ix_agent_trigger_user_name_uniq",
        "agent_trigger",
        ["tenant_id", "agent_name", "user_id", "name"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    op.create_index(
        "ix_agent_trigger_null_user_name_uniq",
        "agent_trigger",
        ["tenant_id", "agent_name", "name"],
        unique=True,
        postgresql_where=sa.text("user_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_agent_trigger_null_user_name_uniq", table_name="agent_trigger")
    op.drop_index("ix_agent_trigger_user_name_uniq", table_name="agent_trigger")
    op.create_unique_constraint(
        "agent_trigger_name_uniq", "agent_trigger", ["tenant_id", "agent_name", "name"]
    )
    op.drop_constraint("agent_trigger_context_mode_valid", "agent_trigger", type_="check")
    op.drop_column("agent_trigger", "context_mode")
    op.drop_column("agent_trigger", "originating_thread_id")
```

- [ ] **Step 4: 更新 ORM 行模型镜像迁移**

Modify `packages/expert-work-persistence/src/expert_work/persistence/models/agent_trigger.py` —— 在 `AgentTriggerRow` 加两列(放在 `webhook_secret_hash` 后、`last_fired_at` 前),并改 `__table_args__`。

加列(在 `webhook_secret_hash: Mapped[str | None] = ...` 行后):

```python
    originating_thread_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    context_mode: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'fresh_thread_per_run'")
    )
```

改 `__table_args__` —— 删 `UniqueConstraint("tenant_id", "agent_name", "name", ...)`,加 context_mode CHECK + 双 partial unique index:

```python
    __table_args__ = (
        CheckConstraint(f"kind IN {_KIND_VALUES}", name="agent_trigger_kind_valid"),
        CheckConstraint(f"source IN {_SOURCE_VALUES}", name="agent_trigger_source_valid"),
        CheckConstraint(
            "context_mode IN ('reuse_thread', 'fresh_thread_per_run')",
            name="agent_trigger_context_mode_valid",
        ),
        Index(
            "ix_agent_trigger_user_name_uniq",
            "tenant_id",
            "agent_name",
            "user_id",
            "name",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        Index(
            "ix_agent_trigger_null_user_name_uniq",
            "tenant_id",
            "agent_name",
            "name",
            unique=True,
            postgresql_where=text("user_id IS NULL"),
        ),
        Index("ix_agent_trigger_tenant_id", "tenant_id"),
        Index(
            "ix_agent_trigger_cron_enabled",
            "kind",
            "enabled",
            postgresql_where=text("kind = 'cron' AND enabled = true"),
        ),
    )
```

- [ ] **Step 5: 跑迁移测试确认通过**

Run: `DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock uv run --project packages/expert-work-persistence pytest packages/expert-work-persistence/tests/test_migration_0130_trigger_user_scope.py -v`
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add packages/expert-work-persistence/migrations/versions/0130_trigger_user_scope.py \
        packages/expert-work-persistence/src/expert_work/persistence/models/agent_trigger.py \
        packages/expert-work-persistence/tests/test_migration_0130_trigger_user_scope.py
git commit -m "feat(persistence): agent_trigger user 维度唯一 + 投递路由列(迁移 0130)"
```

---

### Task 2: TriggerRecord DTO + SQL/memory store 映射新字段

**Files:**
- Modify: `packages/expert-work-protocol/src/expert_work/protocol/trigger.py`
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/trigger/sql.py`
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/trigger/memory.py`
- Test: `packages/expert-work-persistence/tests/test_sql_trigger_store.py`、`.../test_in_memory_trigger_store.py`

**Interfaces:**
- Consumes: Task 1 的列。
- Produces: `TriggerRecord.originating_thread_id: UUID | None = None`、`TriggerRecord.context_mode: Literal["reuse_thread","fresh_thread_per_run"] = "fresh_thread_per_run"`;两 store 的 create/update round-trip 这两字段。

- [ ] **Step 1: 写 SQL store round-trip 测试(失败)**

在 `packages/expert-work-persistence/tests/test_sql_trigger_store.py` 末尾加(该文件已 `import` 好 `SqlTriggerStore`、`_record` 工厂、`trigger_store` fixture、`uuid4`):

```python
@pytest.mark.asyncio
async def test_create_roundtrips_delivery_routing(trigger_store: SqlTriggerStore) -> None:
    tid, tenant, thread = uuid4(), uuid4(), uuid4()
    rec = _record(trigger_id=tid, tenant_id=tenant).model_copy(
        update={"originating_thread_id": thread, "context_mode": "reuse_thread"}
    )
    await trigger_store.create(rec)
    got = await trigger_store.get(trigger_id=tid, tenant_id=tenant)
    assert got is not None
    assert got.originating_thread_id == thread
    assert got.context_mode == "reuse_thread"


@pytest.mark.asyncio
async def test_create_defaults_context_mode(trigger_store: SqlTriggerStore) -> None:
    tid, tenant = uuid4(), uuid4()
    await trigger_store.create(_record(trigger_id=tid, tenant_id=tenant))
    got = await trigger_store.get(trigger_id=tid, tenant_id=tenant)
    assert got is not None
    assert got.originating_thread_id is None
    assert got.context_mode == "fresh_thread_per_run"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock uv run --project packages/expert-work-persistence pytest packages/expert-work-persistence/tests/test_sql_trigger_store.py -k delivery_routing -v`
Expected: FAIL —— `TriggerRecord` 无 `originating_thread_id`(pydantic `ValidationError` / `AttributeError`)。

- [ ] **Step 3: TriggerRecord 加字段**

Modify `packages/expert-work-protocol/src/expert_work/protocol/trigger.py` —— `TriggerRecord`(`class TriggerRecord(BaseModel):`,frozen)加两字段。放在 `webhook_secret_hash: str | None = None` 后:

```python
    originating_thread_id: UUID | None = None
    context_mode: Literal["reuse_thread", "fresh_thread_per_run"] = "fresh_thread_per_run"
```

`Literal` 已在该文件 import(`TriggerKind = Literal[...]` 用了);`UUID` 已 import。

- [ ] **Step 4: SQL store 映射新字段**

Modify `packages/expert-work-persistence/src/expert_work/persistence/trigger/sql.py`:

`_row_to_dto`(在 `webhook_secret_hash=row.webhook_secret_hash,` 后加):
```python
        originating_thread_id=row.originating_thread_id,
        context_mode=cast(
            Literal["reuse_thread", "fresh_thread_per_run"], row.context_mode
        ),
```
(顶部 import 补 `from typing import Literal` 若未有;`cast` 已 import。)

`create` 的 `AgentTriggerRow(...)`(在 `webhook_secret_hash=record.webhook_secret_hash,` 后加):
```python
                originating_thread_id=record.originating_thread_id,
                context_mode=record.context_mode,
```

`update` 的 `.values(...)`(在 `webhook_secret_hash=record.webhook_secret_hash,` 后加):
```python
                originating_thread_id=record.originating_thread_id,
                context_mode=record.context_mode,
```

- [ ] **Step 5: memory store 无需改字段映射(整 record 存取)**

`InMemoryTriggerStore` 存的是完整 `TriggerRecord`(`self._rows[record.id] = record`),新字段自动随之。无需改。此步仅确认,不改码。

- [ ] **Step 6: 写 memory round-trip 测试**

在 `packages/expert-work-persistence/tests/test_in_memory_trigger_store.py` 末尾加:

```python
@pytest.mark.asyncio
async def test_memory_roundtrips_delivery_routing() -> None:
    store = InMemoryTriggerStore()
    tid, tenant, thread = uuid4(), uuid4(), uuid4()
    rec = _record(trigger_id=tid, tenant_id=tenant).model_copy(
        update={"originating_thread_id": thread, "context_mode": "reuse_thread"}
    )
    await store.create(rec)
    got = await store.get(trigger_id=tid, tenant_id=tenant)
    assert got is not None
    assert got.originating_thread_id == thread
    assert got.context_mode == "reuse_thread"
```

(该文件的 `_record` 工厂需支持默认;若其 `_record` 不构造完整 record,复用与 SQL 测同形工厂。核实 `test_in_memory_trigger_store.py` 顶部 `_record` 定义并复用。)

- [ ] **Step 7: 跑两 store 测试确认通过**

Run:
```
uv run --project packages/expert-work-persistence pytest packages/expert-work-persistence/tests/test_in_memory_trigger_store.py -k delivery_routing -v
DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock uv run --project packages/expert-work-persistence pytest packages/expert-work-persistence/tests/test_sql_trigger_store.py -k delivery_routing -v
```
Expected: PASS。

- [ ] **Step 8: 提交**

```bash
git add packages/expert-work-protocol/src/expert_work/protocol/trigger.py \
        packages/expert-work-persistence/src/expert_work/persistence/trigger/sql.py \
        packages/expert-work-persistence/tests/test_sql_trigger_store.py \
        packages/expert-work-persistence/tests/test_in_memory_trigger_store.py
git commit -m "feat(persistence): TriggerRecord 加 originating_thread_id / context_mode + store 映射"
```

---

### Task 3: user-scoped 唯一约束(SQL partial index + memory Python 校验)

**Files:**
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/trigger/memory.py`
- Test: `packages/expert-work-persistence/tests/test_sql_trigger_store.py`、`.../test_in_memory_trigger_store.py`

**Interfaces:**
- Consumes: Task 1 partial unique index、Task 2 DTO。
- Produces: SQL 靠 partial index(两用户同名放行、同用户同名 `IntegrityError`);memory `create` 唯一键 = `(tenant, agent_name, user_id, name)`(user_id 非空)或 `(tenant, agent_name, name)`(user_id 空)。

- [ ] **Step 1: 写唯一性测试(失败)**

SQL(`test_sql_trigger_store.py` 末尾)—— 靠 Task 1 的 partial index,本测验证 DB 行为:
```python
@pytest.mark.asyncio
async def test_two_users_same_name_allowed(trigger_store: SqlTriggerStore) -> None:
    tenant, u1, u2 = uuid4(), uuid4(), uuid4()
    r1 = _record(trigger_id=uuid4(), tenant_id=tenant).model_copy(
        update={"user_id": u1, "name": "daily"}
    )
    r2 = _record(trigger_id=uuid4(), tenant_id=tenant).model_copy(
        update={"user_id": u2, "name": "daily"}
    )
    await trigger_store.create(r1)
    await trigger_store.create(r2)  # 不同 user 同名 —— 放行
    assert (await trigger_store.get(trigger_id=r2.id, tenant_id=tenant)) is not None


@pytest.mark.asyncio
async def test_same_user_same_name_conflicts(trigger_store: SqlTriggerStore) -> None:
    from sqlalchemy.exc import IntegrityError

    tenant, u1 = uuid4(), uuid4()
    r1 = _record(trigger_id=uuid4(), tenant_id=tenant).model_copy(
        update={"user_id": u1, "name": "daily"}
    )
    r2 = _record(trigger_id=uuid4(), tenant_id=tenant).model_copy(
        update={"user_id": u1, "name": "daily"}
    )
    await trigger_store.create(r1)
    with pytest.raises(IntegrityError):
        await trigger_store.create(r2)
```

memory(`test_in_memory_trigger_store.py` 末尾):
```python
@pytest.mark.asyncio
async def test_memory_two_users_same_name_allowed() -> None:
    store = InMemoryTriggerStore()
    tenant, u1, u2 = uuid4(), uuid4(), uuid4()
    await store.create(
        _record(trigger_id=uuid4(), tenant_id=tenant).model_copy(
            update={"user_id": u1, "name": "daily"}
        )
    )
    await store.create(
        _record(trigger_id=uuid4(), tenant_id=tenant).model_copy(
            update={"user_id": u2, "name": "daily"}
        )
    )  # 不放异常即通过


@pytest.mark.asyncio
async def test_memory_same_user_same_name_conflicts() -> None:
    store = InMemoryTriggerStore()
    tenant, u1 = uuid4(), uuid4()
    await store.create(
        _record(trigger_id=uuid4(), tenant_id=tenant).model_copy(
            update={"user_id": u1, "name": "daily"}
        )
    )
    with pytest.raises(ValueError):
        await store.create(
            _record(trigger_id=uuid4(), tenant_id=tenant).model_copy(
                update={"user_id": u1, "name": "daily"}
            )
        )
```

- [ ] **Step 2: 跑测试确认失败**

Run:
```
uv run --project packages/expert-work-persistence pytest packages/expert-work-persistence/tests/test_in_memory_trigger_store.py -k "same_name" -v
```
Expected: FAIL —— `test_memory_two_users_same_name_allowed` 现报 `ValueError`(现 memory `create` 只比 `(tenant, agent_name, name)`,两用户同名被误拒)。

- [ ] **Step 3: 改 memory `create` 唯一键含 user_id**

Modify `packages/expert-work-persistence/src/expert_work/persistence/trigger/memory.py` `create`:

```python
    async def create(self, record: TriggerRecord) -> TriggerRecord:
        for existing in self._rows.values():
            same_scope = (
                existing.tenant_id == record.tenant_id
                and existing.agent_name == record.agent_name
                and existing.name == record.name
                and existing.user_id == record.user_id
            )
            if same_scope:
                msg = (
                    f"trigger {record.name!r} already exists for agent "
                    f"{record.agent_name!r} (user {record.user_id})"
                )
                raise ValueError(msg)
        self._rows[record.id] = record
        return record
```

(`existing.user_id == record.user_id` 对两者皆 `None` 也成立 → 保留「同租户同 agent 无主任务按名唯一」语义,与 SQL 的 `null_user` partial index 一致。)

- [ ] **Step 4: 跑测试确认通过**

Run:
```
uv run --project packages/expert-work-persistence pytest packages/expert-work-persistence/tests/test_in_memory_trigger_store.py -k "same_name" -v
DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock uv run --project packages/expert-work-persistence pytest packages/expert-work-persistence/tests/test_sql_trigger_store.py -k "same_name" -v
```
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add packages/expert-work-persistence/src/expert_work/persistence/trigger/memory.py \
        packages/expert-work-persistence/tests/test_sql_trigger_store.py \
        packages/expert-work-persistence/tests/test_in_memory_trigger_store.py
git commit -m "feat(persistence): 触发器唯一约束分 user(SQL partial index + memory 校验)"
```

---

### Task 4: `TriggerStore.list_by_user`

**Files:**
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/trigger/base.py`
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/trigger/sql.py`
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/trigger/memory.py`
- Test: 两 store 测试文件

**Interfaces:**
- Consumes: Task 2 DTO。
- Produces: `async def list_by_user(self, *, tenant_id: UUID, user_id: UUID, agent_name: str | None = None) -> list[TriggerRecord]:`(ABC + 两后端)。按 `created_at` 升序(SQL);过滤 `tenant_id` + `user_id`(+ 可选 `agent_name`)。PR2 对话工具 list action + Spec 3 admin UI 用。

- [ ] **Step 1: 写测试(失败)**

memory(`test_in_memory_trigger_store.py` 末尾):
```python
@pytest.mark.asyncio
async def test_memory_list_by_user_scopes() -> None:
    store = InMemoryTriggerStore()
    tenant, u1, u2 = uuid4(), uuid4(), uuid4()
    await store.create(
        _record(trigger_id=uuid4(), tenant_id=tenant).model_copy(
            update={"user_id": u1, "name": "a"}
        )
    )
    await store.create(
        _record(trigger_id=uuid4(), tenant_id=tenant).model_copy(
            update={"user_id": u1, "name": "b"}
        )
    )
    await store.create(
        _record(trigger_id=uuid4(), tenant_id=tenant).model_copy(
            update={"user_id": u2, "name": "c"}
        )
    )
    got = await store.list_by_user(tenant_id=tenant, user_id=u1)
    assert {t.name for t in got} == {"a", "b"}
```

SQL(`test_sql_trigger_store.py` 末尾)—— 同形,断言 `list_by_user` 只返 u1 两条 + `agent_name` 过滤。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --project packages/expert-work-persistence pytest packages/expert-work-persistence/tests/test_in_memory_trigger_store.py -k list_by_user -v`
Expected: FAIL —— `AttributeError: 'InMemoryTriggerStore' object has no attribute 'list_by_user'`。

- [ ] **Step 3: ABC 加抽象方法**

Modify `packages/expert-work-persistence/src/expert_work/persistence/trigger/base.py` —— 在 `list_by_tenant` 后加:
```python
    @abc.abstractmethod
    async def list_by_user(
        self, *, tenant_id: UUID, user_id: UUID, agent_name: str | None = None
    ) -> list[TriggerRecord]:
        """List a single user's triggers within a tenant (Spec 1 PR1).

        Ordered by ``created_at`` ascending. Optional ``agent_name`` filter.
        """
        raise NotImplementedError
```

- [ ] **Step 4: SQL 实现**

Modify `packages/expert-work-persistence/src/expert_work/persistence/trigger/sql.py` —— 仿 `list_by_tenant` 加:
```python
    async def list_by_user(
        self, *, tenant_id: UUID, user_id: UUID, agent_name: str | None = None
    ) -> list[TriggerRecord]:
        async with self._sf() as session:
            stmt = select(AgentTriggerRow).where(
                AgentTriggerRow.tenant_id == tenant_id,
                AgentTriggerRow.user_id == user_id,
            )
            if agent_name is not None:
                stmt = stmt.where(AgentTriggerRow.agent_name == agent_name)
            stmt = stmt.order_by(AgentTriggerRow.created_at.asc())
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_dto(r) for r in rows]
```

- [ ] **Step 5: memory 实现**

Modify `packages/expert-work-persistence/src/expert_work/persistence/trigger/memory.py`:
```python
    async def list_by_user(
        self, *, tenant_id: UUID, user_id: UUID, agent_name: str | None = None
    ) -> list[TriggerRecord]:
        rows = [
            r
            for r in self._rows.values()
            if r.tenant_id == tenant_id
            and r.user_id == user_id
            and (agent_name is None or r.agent_name == agent_name)
        ]
        return sorted(rows, key=lambda r: r.created_at)
```

- [ ] **Step 6: 跑测试确认通过**

Run:
```
uv run --project packages/expert-work-persistence pytest packages/expert-work-persistence/tests/test_in_memory_trigger_store.py -k list_by_user -v
DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock uv run --project packages/expert-work-persistence pytest packages/expert-work-persistence/tests/test_sql_trigger_store.py -k list_by_user -v
```
Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add packages/expert-work-persistence/src/expert_work/persistence/trigger/base.py \
        packages/expert-work-persistence/src/expert_work/persistence/trigger/sql.py \
        packages/expert-work-persistence/src/expert_work/persistence/trigger/memory.py \
        packages/expert-work-persistence/tests/test_sql_trigger_store.py \
        packages/expert-work-persistence/tests/test_in_memory_trigger_store.py
git commit -m "feat(persistence): TriggerStore.list_by_user(per-user 列表,PR2/Spec3 用)"
```

---

### Task 5: scheduler RRULE 双路径 + 有界窗口自动停用

**Files:**
- Modify: `services/control-plane/pyproject.toml`
- Modify: `services/control-plane/src/control_plane/scheduler.py`
- Test: `services/control-plane/tests/test_scheduler.py`(若无则 Create)

**Interfaces:**
- Consumes: `TriggerRecord.config`(可能含 `rrule`+`timezone` 或 legacy `expr`)。
- Produces: `_next_occurrence(trigger: TriggerRecord, *, after: datetime) -> datetime | None`(rrule 优先,回落 cron;耗尽返 None);`_fire_due_cron` 用它并对 rrule 耗尽者 `enabled=False`。

- [ ] **Step 1: 加 dateutil 依赖**

Modify `services/control-plane/pyproject.toml` —— 在 `dependencies = [...]` 数组里加(croniter 行附近):
```toml
    "python-dateutil>=2.9,<3",
```
然后同步锁:`uv lock --project services/control-plane`(或仓库约定的 lock 命令)。

- [ ] **Step 2: 写 `_next_occurrence` 单测(失败)**

Create/Modify `services/control-plane/tests/test_scheduler.py` —— 纯逻辑单测,不需 Docker。用 `TriggerRecord` 直构:
```python
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from control_plane.scheduler import _next_occurrence
from expert_work.protocol import TriggerRecord


def _trigger(config: dict[str, object], *, created: datetime, last_fired=None) -> TriggerRecord:
    return TriggerRecord(
        id=uuid4(),
        tenant_id=uuid4(),
        agent_name="a",
        agent_version="1.0.0",
        name="t",
        kind="cron",
        config=config,
        created_at=created,
        updated_at=created,
        last_fired_at=last_fired,
    )


def test_rrule_daily_next_occurrence() -> None:
    created = datetime(2026, 5, 1, 14, 0, tzinfo=UTC)
    trig = _trigger(
        {"rrule": "FREQ=DAILY;BYHOUR=3;BYMINUTE=0", "timezone": "UTC"}, created=created
    )
    nxt = _next_occurrence(trig, after=created)
    assert nxt == datetime(2026, 5, 2, 3, 0, tzinfo=UTC)


def test_rrule_timezone_shifts_utc_instant() -> None:
    created = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)
    # 上海时区早3点 = UTC 前一天 19:00。
    trig = _trigger(
        {"rrule": "FREQ=DAILY;BYHOUR=3;BYMINUTE=0", "timezone": "Asia/Shanghai"},
        created=created,
    )
    nxt = _next_occurrence(trig, after=created)
    assert nxt is not None
    assert nxt.astimezone(UTC).hour == 19


def test_rrule_bounded_count_exhausts_to_none() -> None:
    created = datetime(2026, 5, 1, 2, 0, tzinfo=UTC)
    trig = _trigger(
        {"rrule": "FREQ=DAILY;BYHOUR=3;BYMINUTE=0;COUNT=1", "timezone": "UTC"},
        created=created,
        last_fired=datetime(2026, 5, 1, 3, 0, tzinfo=UTC),  # 唯一一次已跑
    )
    assert _next_occurrence(trig, after=datetime(2026, 5, 1, 3, 0, tzinfo=UTC)) is None


def test_legacy_cron_still_works() -> None:
    created = datetime(2026, 5, 1, 14, 0, tzinfo=UTC)
    trig = _trigger({"expr": "0 3 * * *"}, created=created)
    nxt = _next_occurrence(trig, after=created)
    assert nxt == datetime(2026, 5, 2, 3, 0, tzinfo=UTC)
```

- [ ] **Step 3: 跑测试确认失败**

Run: `DOCKER_HOST= uv run --project services/control-plane pytest services/control-plane/tests/test_scheduler.py -k next_occurrence -v`
Expected: FAIL —— `ImportError: cannot import name '_next_occurrence'`。

- [ ] **Step 4: 实现 `_next_occurrence` + 改 `_fire_due_cron`**

Modify `services/control-plane/src/control_plane/scheduler.py`:

顶部 import 加:
```python
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr
```

替换 `_next_fire` / `_is_cron_due`(scheduler.py:89-107)为:
```python
def _next_occurrence(trigger: TriggerRecord, *, after: datetime) -> datetime | None:
    """Next scheduled fire strictly after ``after``, in UTC, or ``None`` if the
    schedule is exhausted (RRULE ``UNTIL``/``COUNT`` past their end).

    Dual-path: an RRULE (``config['rrule']``, evaluated in ``config['timezone']``
    for DST-safe local wall-clock) takes precedence; otherwise the legacy cron
    ``config['expr']`` path (backward compatible). A row with neither raises —
    the caller catches it per-trigger so one bad row never aborts the sweep.
    """
    rrule_str = trigger.config.get("rrule")
    if isinstance(rrule_str, str) and rrule_str:
        tz_name = trigger.config.get("timezone")
        tz = ZoneInfo(tz_name) if isinstance(tz_name, str) and tz_name else UTC
        dtstart = trigger.created_at.astimezone(tz)
        occurrence = rrulestr(rrule_str, dtstart=dtstart).after(after.astimezone(tz))
        return occurrence.astimezone(UTC) if occurrence is not None else None
    expr = trigger.config.get("expr")
    if isinstance(expr, str) and expr:
        result: datetime = croniter(expr, after).get_next(datetime)
        return result
    msg = f"trigger {trigger.id} has neither 'rrule' nor 'expr' in config"
    raise ValueError(msg)
```

改 `_fire_due_cron`(scheduler.py:217-229)—— 用 `_next_occurrence`,rrule 耗尽自动停用:
```python
    async def _fire_due_cron(self, now: datetime) -> int:
        with _bypass_rls():
            triggers = await self._triggers.list_enabled_cron()
        fired = 0
        for trigger in triggers[: self._batch_size]:
            try:
                base = trigger.last_fired_at or trigger.created_at
                nxt = _next_occurrence(trigger, after=base)
                if nxt is None:
                    # RRULE 有界窗口耗尽 —— 停用,下轮不再扫。
                    await self._disable_exhausted(trigger)
                    continue
                if nxt > now:
                    continue
                if await self._fire_cron(trigger, now=now):
                    fired += 1
            except Exception:
                logger.exception("scheduler.trigger_failed", extra={"trigger_id": str(trigger.id)})
        return fired

    async def _disable_exhausted(self, trigger: TriggerRecord) -> None:
        """RRULE 耗尽 → enabled=False(幂等:已 False 也无害)。"""
        with _tenant_scope(trigger.tenant_id, trigger.user_id):
            await self._triggers.update(
                trigger.model_copy(update={"enabled": False, "updated_at": datetime.now(UTC)})
            )
```

(`TriggerRecord` 已在 scheduler.py import;`UTC` 已 import;`_tenant_scope` 已在文件内。)

- [ ] **Step 5: 加 `_disable_exhausted` 测试**

在 `test_scheduler.py` 加一个用现有 scheduler 测试脚手架(in-memory stores)的测试:建一个 `COUNT=1` 已跑过的 rrule 触发器,`run_once` 后断言其 `enabled` 变 False。若文件已有 `TriggerScheduler` 集成测脚手架(in-memory `InMemoryTriggerStore` 等),复用;否则用最小 in-memory 装配:
```python
import pytest

from control_plane.scheduler import TriggerScheduler  # 若已有装配 fixture 则复用之


@pytest.mark.asyncio
async def test_exhausted_rrule_auto_disabled(scheduler_harness) -> None:
    """scheduler_harness = 现有 scheduler 集成 fixture(in-memory stores + stub runtime)。
    若不存在,按 test_scheduler.py 现有集成测试的装配方式建。"""
    store = scheduler_harness.trigger_store
    trig = _trigger(
        {"rrule": "FREQ=DAILY;BYHOUR=3;BYMINUTE=0;COUNT=1", "timezone": "UTC"},
        created=datetime(2026, 5, 1, 2, 0, tzinfo=UTC),
        last_fired=datetime(2026, 5, 1, 3, 0, tzinfo=UTC),
    )
    await store.create(trig)
    await scheduler_harness.scheduler.run_once()
    got = await store.get(trigger_id=trig.id, tenant_id=trig.tenant_id)
    assert got is not None
    assert got.enabled is False
```
> 实现者注:先看 `test_scheduler.py` 是否已有 `TriggerScheduler` 集成 fixture。有则复用其 store/runtime 句柄;无则此步降为直接单测 `_disable_exhausted`(构造 store+trigger,调 `_fire_due_cron` 前置或直接调 `_disable_exhausted` 断言 `update` 生效)。二者取其一,不要新造重脚手架。

- [ ] **Step 6: 跑测试确认通过 + 现有 scheduler 测试不回归**

Run:
```
DOCKER_HOST= uv run --project services/control-plane pytest services/control-plane/tests/test_scheduler.py -v
```
Expected: 新测 PASS,现有 scheduler 测试(cron 路径)全绿(向后兼容验证)。

- [ ] **Step 7: 提交**

```bash
git add services/control-plane/pyproject.toml services/control-plane/uv.lock \
        services/control-plane/src/control_plane/scheduler.py \
        services/control-plane/tests/test_scheduler.py
git commit -m "feat(scheduler): RRULE 双路径调度(per-task IANA 时区,有界窗口自动停;兼容 legacy cron)"
```

---

### Task 6: 堵触发器 API 所有权安全洞(ownership via resolve_target_user_id)

**Files:**
- Modify: `services/control-plane/src/control_plane/api/triggers.py`
- Test: `services/control-plane/tests/test_triggers_api.py`

**Interfaces:**
- Consumes: `resolve_target_user_id(request, users, *, requested: UUID | None) -> UUID | None`(`api/_user_scope.py:53`,self/admin-target-other/否则 403);`resolve_caller_user_id`;`get_user_repo`;`TriggerStore.list_by_user`(Task 4)。
- Produces: GET/PATCH/DELETE 对**有主**触发器(`record.user_id` 非空)做 ownership 校验(非 owner 非 admin → 403);LIST 非 admin 只返自己的。CREATE 不变(现已 self-stamp)。

> **安全姿态说明(plan 决策)**:Spec D-12 列了「RBAC `trigger` Resource + resolve_target_user_id」。核实发现 `resolve_target_user_id` 用 `is_admin(principal)`(查 Role.ADMIN),**不依赖 RBAC Resource**。故本 PR 堵洞只用 ownership(resolve_target_user_id);**RBAC `trigger` Resource 推迟到 Spec 3**(admin 管理端点加 `require("trigger",...)` 时才需),避免 Spec 1 留未用 Literal。堵洞不打折。

- [ ] **Step 1: 写安全测试(失败)**

在 `services/control-plane/tests/test_triggers_api.py` 加。该文件已有 `triggers_client` fixture(默认租户 JWT)、`make_test_jwt`、`_create_cron` helper。需要「另一个 user 身份」的 client 与「admin 身份」;参照该文件/`tests/auth_fixtures.py` 现有 JWT 构造能力(`make_test_jwt` 支持 subject / roles 参数,核实其签名后用)。

```python
@pytest.mark.asyncio
async def test_non_owner_cannot_delete_others_trigger(
    triggers_client: AsyncClient,
) -> None:
    """user A 建的触发器,user B(非 admin)删不了 —— 403。"""
    created = await _create_cron(triggers_client, name="a-owned")
    trigger_id = created["id"]

    # user B(不同 subject,非 admin)—— 参照 make_test_jwt 构造另一 user 的 header。
    app = triggers_client._transport.app  # type: ignore[attr-defined,union-attr]
    other = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://control-plane.test",
        headers={
            "Authorization": f"Bearer {make_test_jwt(tenant_id=_DEFAULT_TENANT, subject='user-b')}"
        },
    )
    async with other:
        resp = await other.delete(f"/v1/triggers/{trigger_id}")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_delete_others_trigger(triggers_client: AsyncClient) -> None:
    """admin 可删他人触发器 —— 200。"""
    created = await _create_cron(triggers_client, name="admin-target")
    trigger_id = created["id"]
    # triggers_client 默认 JWT 若已是 admin 角色,直接删自己建的即验证 owner 路径;
    # 另构造 admin-targeting-other 的场景需 admin JWT + 不同 owner。核实 make_test_jwt
    # 的 roles 参数后构造 admin header。
    resp = await triggers_client.delete(f"/v1/triggers/{trigger_id}")
    assert resp.status_code == 200
```

> 实现者注:先核实 `tests/auth_fixtures.py::make_test_jwt` 的确切签名(是否支持 `subject=` / `roles=`)。dev auth_mode 下角色如何注入需照该文件现有测试(如 members API 测试)构造 admin/非 admin principal。若 `make_test_jwt` 不支持指定 subject/roles,按 `test_members_api.py`(或任一用 `require("user",...)` 的 API 测试)里区分 admin/非 admin 的既有做法照搬。

- [ ] **Step 2: 跑测试确认失败**

Run: `DOCKER_HOST= uv run --project services/control-plane pytest services/control-plane/tests/test_triggers_api.py -k "owner" -v`
Expected: FAIL —— `test_non_owner_cannot_delete_others_trigger` 现返 200(无所有权校验,任一租户成员可删)。

- [ ] **Step 3: GET/PATCH/DELETE 加 ownership 校验**

Modify `services/control-plane/src/control_plane/api/triggers.py`:

顶部 import 改(现:`from control_plane.api._user_scope import get_user_repo, resolve_caller_user_id`)为加 `resolve_target_user_id`:
```python
from control_plane.api._user_scope import (
    get_user_repo,
    resolve_caller_user_id,
    resolve_target_user_id,
)
```

`get_trigger`(triggers.py:376-386)—— fetch 后校验 owner:
```python
    @router.get("/{trigger_id}", response_model=None)
    async def get_trigger(
        trigger_id: UUID,
        request: Request,
        triggers: Annotated[TriggerStore, Depends(_get_trigger_store)],
        users: Annotated[TenantUserStore, Depends(get_user_repo)],
    ) -> JSONResponse:
        tenant_id: UUID = request.state.tenant_id
        record = await triggers.get(trigger_id=trigger_id, tenant_id=tenant_id)
        if record is None:
            raise HTTPException(status_code=404, detail="trigger not found")
        # H.8-F1 所有权闸:有主触发器仅 owner / admin 可读;resolve 对越权抛 403。
        await resolve_target_user_id(request, users, requested=record.user_id)
        return JSONResponse(content=_trigger_dict(record))
```
`patch_trigger`(triggers.py:388-)与 `delete_trigger`(triggers.py:432-)同法:各自签名加 `users: Annotated[TenantUserStore, Depends(get_user_repo)],`,在 `record = await triggers.get(...)`(patch)/删除前(delete 先 get)后插:
```python
        await resolve_target_user_id(request, users, requested=record.user_id)
```
`delete_trigger` 现直接 `triggers.delete(...)` 无 get —— 改为先 get 校验 owner 再删:
```python
    @router.delete("/{trigger_id}", response_model=None)
    async def delete_trigger(
        trigger_id: UUID,
        request: Request,
        triggers: Annotated[TriggerStore, Depends(_get_trigger_store)],
        users: Annotated[TenantUserStore, Depends(get_user_repo)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
    ) -> JSONResponse:
        tenant_id: UUID = request.state.tenant_id
        actor_id: str = request.state.actor_id
        record = await triggers.get(trigger_id=trigger_id, tenant_id=tenant_id)
        if record is None:
            raise HTTPException(status_code=404, detail="trigger not found")
        await resolve_target_user_id(request, users, requested=record.user_id)
        deleted = await triggers.delete(trigger_id=trigger_id, tenant_id=tenant_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="trigger not found")
        await emit(
            audit,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=AuditAction.TRIGGER_DELETE,
            resource_type="trigger",
            resource_id=str(trigger_id),
            trace_id=current_trace_id_hex(),
        )
        return JSONResponse(content={"deleted": True})
```

- [ ] **Step 4: LIST 非 admin 只看自己**

Modify `list_triggers`(triggers.py:334-374)—— 签名加 `users` dep,非 admin 走 `list_by_user`:
```python
    @router.get("", response_model=None)
    async def list_triggers(
        request: Request,
        triggers: Annotated[TriggerStore, Depends(_get_trigger_store)],
        users: Annotated[TenantUserStore, Depends(get_user_repo)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
        agent_name: Annotated[str | None, Query(min_length=1)] = None,
        agent_version: Annotated[str | None, Query(min_length=1)] = None,
        tenant_id: Annotated[UUID | Literal["*"] | None, Query()] = None,
    ) -> JSONResponse:
        if agent_version is not None and agent_name is None:
            raise HTTPException(status_code=422, detail="agent_version requires agent_name")
        # H.8-F1:非 admin 只见自己的触发器;admin 走原租户/跨租户列表。
        from control_plane.auth.rbac import is_admin

        if not is_admin(request.state.principal):
            caller_user_id = await resolve_caller_user_id(request, users)
            if caller_user_id is None:
                return JSONResponse(content={"items": [], "total": 0, "cross_tenant": False})
            items = await triggers.list_by_user(
                tenant_id=request.state.tenant_id,
                user_id=caller_user_id,
                agent_name=agent_name,
            )
            return JSONResponse(
                content={
                    "items": [_trigger_dict(t) for t in items],
                    "total": len(items),
                    "cross_tenant": False,
                }
            )
        scope = await ensure_tenant_scope(
            request.state.principal,
            tenant_id,
            audit,
            trace_id=current_trace_id_hex(),
            endpoint="GET /v1/triggers",
            cross_tenant_enabled=cross_tenant_query_enabled(request),
        )
        async with applied_scope(scope):
            if isinstance(scope, CrossTenant):
                items = await triggers.list_all_tenants(
                    agent_name=agent_name, agent_version=agent_version
                )
            else:
                items = await triggers.list_by_tenant(
                    tenant_id=scope.tenant_id,
                    agent_name=agent_name,
                    agent_version=agent_version,
                )
        return JSONResponse(
            content={
                "items": [_trigger_dict(t) for t in items],
                "total": len(items),
                "cross_tenant": isinstance(scope, CrossTenant),
            }
        )
```
(`is_admin` 从 `control_plane.auth.rbac` import;放模块顶部 import 更佳,此处示内联仅说明来源 —— 实现时提到文件顶部 import 区。`TenantUserStore` 已在 triggers.py import 用于 `create_trigger`。)

- [ ] **Step 5: 跑测试确认通过 + 现有触发器测试不回归**

Run: `DOCKER_HOST= uv run --project services/control-plane pytest services/control-plane/tests/test_triggers_api.py -v`
Expected: 新安全测 PASS;现有 CRUD 测试全绿(注意:现有测试若以「同一 caller 建又删」跑,owner 路径通过;若有「跨 user」隐含假设需按新语义核对)。

- [ ] **Step 6: 提交**

```bash
git add services/control-plane/src/control_plane/api/triggers.py \
        services/control-plane/tests/test_triggers_api.py
git commit -m "fix(triggers): 堵所有权安全洞 —— GET/PATCH/DELETE owner 校验 + LIST 非 admin 只见自己"
```

---

## 收尾:全量检查

- [ ] **Step 1: 跑 PR 全量测试**

```bash
# persistence(含 integration)
DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock uv run --project packages/expert-work-persistence pytest packages/expert-work-persistence/tests/test_sql_trigger_store.py packages/expert-work-persistence/tests/test_in_memory_trigger_store.py packages/expert-work-persistence/tests/test_migration_0130_trigger_user_scope.py -v
# control-plane
DOCKER_HOST= uv run --project services/control-plane pytest services/control-plane/tests/test_scheduler.py services/control-plane/tests/test_triggers_api.py -v
```
Expected: 全绿。

- [ ] **Step 2: lint + typecheck(CI 同款范围)**

```bash
uv run ruff check .
uv run ruff format --check .
# mypy(含 tests;按仓库 CI 的 mypy 命令跑)
```
Expected: 无新错。

---

## Self-Review 结果(写完自查,已修)

**Spec 覆盖**(对 spec §3 F 地基):
- §3.1 唯一约束分 user → Task 1(迁移)+ Task 3(memory 校验)✅
- §3.1 config rrule/timezone → Task 5(scheduler 读)✅(config 是 JSONB 无需 schema 迁移;写入方是 PR2 工具)
- §3.1 originating_thread_id / context_mode 列 → Task 1 + Task 2 ✅
- §3.2 list_by_user → Task 4 ✅
- §3.2 共享创建路径 → **PR2**(工具需要时抽;PR1 的 HTTP create 未改契约,不预抽,避免 YAGNI)—— 记 PR2
- §3.3 scheduler 切 RRULE + 有界窗口自动停 → Task 5 ✅(双路径向后兼容)
- §3.4 安全洞修 → Task 6 ✅(ownership;RBAC Resource 推 Spec 3,见 Task 6 说明)
- §3.5 生命周期事件 → **PR3**(补 TRIGGER_COMPLETED/FAILED,与 reconcile 投递同处;现有 CREATE/UPDATE/DELETE/FIRE 已在)—— 记 PR3

**占位符扫描**:无 TBD/TODO;每步有实码或实测。两处「实现者注」是**指向已存在事实的核实指令**(make_test_jwt 签名 / scheduler 现有 fixture),非占位符 —— 因这两处依赖测试脚手架现状,实现者须先读现文件再照搬,不宜在 plan 里臆造。

**类型一致性**:`list_by_user(*, tenant_id, user_id, agent_name=None)` 签名 Task 4 定义、Task 6 消费一致;`_next_occurrence(trigger, *, after) -> datetime | None` Task 5 内自洽;`context_mode` Literal 值 `reuse_thread`/`fresh_thread_per_run` 在迁移 CHECK、ORM CHECK、DTO Literal、测试三处一致。

**PR1 不含(明确 → 后续 PR)**:共享创建路径(PR2)、TRIGGER_COMPLETED/FAILED 事件(PR3)、RBAC trigger Resource(Spec3)、对话工具/投递/前端(PR2/3/4)。

---

## Execution Handoff

PR1 plan 完成。执行选项见主对话(推荐 Subagent-Driven)。
