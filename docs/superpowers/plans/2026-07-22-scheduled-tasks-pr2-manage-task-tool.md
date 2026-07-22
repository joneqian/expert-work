# 对话工具 `manage_task` 实现计划(Spec 1 PR2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增内建工具 `manage_task`,让对话中的 Agent 用结构化入参创建/查看/修改/取消该用户的定时任务;opt-in via manifest `tools:`;结构化字段→RRULE;被定时触发的 run 禁用此工具(自排护栏)。

**Architecture:** 内建 `Tool` 类 `ManageTaskTool`(照 `UpdatePlanTool` + skill-authoring 范式),注入 `TriggerStore` + baked `agent_name`/`agent_version`,从 `ToolContext` 取 `tenant_id`/`user_id`/`thread_id`。新增纯函数模块把结构化调度字段编译成 scheduler 能消费的 RRULE 串(不含 DTSTART)。自排护栏走 **per-run**(不碰 build 缓存):`fire_trigger` 在 `configurable` 打 `trigger_origin=True` → 提进 `ToolContext` → agent_node 绑定 LLM 工具时过滤掉 `manage_task` + 工具 `call` 见此旗标抛 `ToolBlockedError`。

**Tech Stack:** Python 3.12,`dateutil.rrule`/`zoneinfo`,pydantic(TriggerRecord),pytest。orchestrator + control-plane 两服务。

## Global Constraints

- **提交无署名**:任何 commit / PR body 都不带 `Co-Authored-By` 或 🤖 行。中文 conventional commits(`feat:` / `test:` / `refactor:`)。
- **工具名冻结 `manage_task`**(D-8):须过 `^[a-zA-Z][a-zA-Z0-9_-]{,63}$`(wire-safe);参数 schema 一并冻结(未来 instructional skill 按名引)。
- **指引住在 schema `description`**(D-7):工具级 + 字段级 `description` 承载「何时用 / 缺时间要反问 / update-cancel 传 id」;**绝不写进 system prompt**。
- **RRULE 串不含 `DTSTART`**:scheduler `_next_occurrence`(scheduler.py:113-134,已合并)从 `trigger.created_at.astimezone(tz)` 注入 dtstart;串只带 `FREQ=...;BYHOUR=...;BYMINUTE=...[;BYDAY=|;BYMONTHDAY=][;UNTIL=|;COUNT=]`。时区独立存 `config['timezone']`(IANA 名)。
- **scheduler 消费契约**:`config['rrule']`(非空 str)、`config['timezone']`(IANA)、`config['seed_input']`(每次触发的首条 HumanMessage)。本 PR 另写 `config['summary']`(list 用的人话摘要)。`kind` 保留 `'cron'`,`source='api'`。
- **对话建的任务**:`context_mode='reuse_thread'`、`originating_thread_id=ctx.thread_id`(D-9;投递逻辑属 PR3,本 PR 只落这两个字段)。
- **自排护栏 = per-run(方案 A),不动 build 缓存**:`runtime.get_agent` 按 `(tenant,name,version)` 缓存,构建期剔除会与普通 run 撞键;故走 configurable 旗标 + 绑定期过滤 + 调用期挡。
- **不碰已合并的 HTTP create 端点**:`api/triggers.py` 的 `create_trigger` 保持 expr-only 不动;共享创建路径 + 端点接受 rrule **推迟到 Spec 3**(admin UI 才是第二个 rrule 创建者;现在抽=过早抽象,YAGNI)。
- **`trigger_store` 只线进主 `make_agent_builder`**(app.py:1450),不进 child/worker 构建器(子代理/worker 不排任务;子代理 manifest 若声明 `manage_task` 而无 store → `AgentFactoryError`,与声明任何未接线 builtin 同种失败,可接受)。
- **依赖**:orchestrator 新增 `python-dateutil>=2.9,<3`(现无);root `pyproject.toml` 的 `module = "dateutil.*"` mypy override 已存在(覆盖全库,不用改)。
- **CI 契约**:提交前跑 `ruff check` **和** `ruff format --check`(PR1 教训:只跑 check 漏 format debt 阻塞 CI)+ CI-scope `mypy`(扫 tests)+ `uv run`(裸 python 炸)。改工具签名 / ToolContext 跑 orchestrator 全测;改 fire_trigger / app 接线跑 control-plane 测。

---

## 文件结构

| 文件 | 职责 | 任务 |
|------|------|------|
| `services/orchestrator/src/orchestrator/tools/task_schedule.py` | **新** 纯函数:结构化字段→RRULE 串(`build_rrule`)、人话摘要(`summarize_schedule`)、算下次触发(`next_fire`,镜像 scheduler) | T1 |
| `services/orchestrator/pyproject.toml` | 加 `python-dateutil` 依赖 | T1 |
| `services/orchestrator/src/orchestrator/tools/registry.py` | `ToolContext` 加 `thread_id` + `trigger_origin` 两字段 | T2 |
| `services/orchestrator/src/orchestrator/graph_builder/builder.py` | `_build_tool_context` 提取 `thread_id`/`trigger_origin`;agent_node 绑定期过滤 `manage_task` | T2(提取)、T5(过滤) |
| `services/orchestrator/src/orchestrator/tools/manage_task.py` | **新** `ManageTaskTool`(4 action + 调用期护栏) | T3 |
| `services/orchestrator/src/orchestrator/tools/assembly.py` | `KNOWN_BUILTINS` 加 `manage_task` + `_register_builtin` no-op 分支 | T4 |
| `services/orchestrator/src/orchestrator/agent_factory.py` | `build_agent` 加 `trigger_store` kwarg + gated 注册(bake name/version) | T4 |
| `services/control-plane/src/control_plane/runtime.py` | `make_agent_builder` 加 `trigger_store` 参 + 转发进 `build_agent` | T4 |
| `services/control-plane/src/control_plane/app.py` | 主 `make_agent_builder(...)` 调用传 `trigger_store=resolved_trigger_store` | T4 |
| `services/control-plane/src/control_plane/trigger_firing.py` | `fire_trigger` 的 `configurable` 加 `trigger_origin=True` | T5 |

测试文件:`tests/tools/test_task_schedule.py`(T1)、`tests/test_tool_context.py` 或就近扩(T2)、`tests/tools/test_manage_task.py`(T3)、`tests/test_agent_factory.py` 扩(T4)、`tests/test_trigger_firing.py` + `tests/test_agent_factory.py` 扩(T5)。orchestrator 测在 `services/orchestrator/tests/`,control-plane 测在 `services/control-plane/tests/`。

---

## Task 1: RRULE 编译模块 `task_schedule.py`(纯函数,地基)

**Files:**
- Create: `services/orchestrator/src/orchestrator/tools/task_schedule.py`
- Modify: `services/orchestrator/pyproject.toml`(加依赖)
- Test: `services/orchestrator/tests/tools/test_task_schedule.py`

**Interfaces:**
- Produces:
  - `build_rrule(*, frequency: str, hour: int, minute: int, by_day: Sequence[str] | None = None, by_month_day: int | None = None, start_date: date | None = None, end_date: date | None = None, count: int | None = None, timezone: str = "UTC") -> str` —— 返不含 DTSTART 的 RRULE 串;非法入参抛 `ScheduleError`。
  - `summarize_schedule(*, frequency, hour, minute, by_day=None, by_month_day=None, start_date=None, end_date=None, count=None, timezone="UTC") -> str` —— 人话摘要(list 展示用)。
  - `next_fire(rrule: str, *, timezone: str, dtstart: datetime, after: datetime) -> datetime | None` —— 镜像 scheduler `_next_occurrence` 的 rrule 分支:`rrulestr(rrule, dtstart=dtstart.astimezone(tz)).after(after.astimezone(tz))` → UTC;耗尽返 None。
  - `class ScheduleError(ValueError)` —— 非法调度入参(工具转成给 LLM 的错误)。

- [ ] **Step 1: 加依赖**

`services/orchestrator/pyproject.toml` 的 `dependencies` 数组加一行(与其它依赖同格式,按字母序就近插入):

```toml
    "python-dateutil>=2.9,<3",
```

跑 `cd /Users/mac/src/github/jone_qian/expert-work && uv lock` 更新 lockfile。

- [ ] **Step 2: 写失败测试**

`services/orchestrator/tests/tools/test_task_schedule.py`:

```python
from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from dateutil.rrule import rrulestr

from orchestrator.tools.task_schedule import (
    ScheduleError,
    build_rrule,
    next_fire,
    summarize_schedule,
)


def _scheduler_next(rrule: str, *, tz: str, created_at: datetime, after: datetime) -> datetime | None:
    """Reproduce scheduler._next_occurrence's rrule branch verbatim so we prove
    the string we build is consumed by the *real* scheduler the same way."""
    zone = ZoneInfo(tz)
    dtstart = created_at.astimezone(zone)
    occ = rrulestr(rrule, dtstart=dtstart).after(after.astimezone(zone))
    return occ.astimezone(UTC) if occ is not None else None


# ---- build_rrule: shape ----

def test_daily_shape() -> None:
    assert build_rrule(frequency="daily", hour=3, minute=0) == (
        "FREQ=DAILY;BYHOUR=3;BYMINUTE=0;BYSECOND=0"
    )


def test_weekly_shape() -> None:
    assert build_rrule(frequency="weekly", hour=13, minute=30, by_day=["WE"]) == (
        "FREQ=WEEKLY;BYDAY=WE;BYHOUR=13;BYMINUTE=30;BYSECOND=0"
    )


def test_weekly_multi_day() -> None:
    assert build_rrule(frequency="weekly", hour=9, minute=0, by_day=["MO", "TH"]) == (
        "FREQ=WEEKLY;BYDAY=MO,TH;BYHOUR=9;BYMINUTE=0;BYSECOND=0"
    )


def test_monthly_shape() -> None:
    assert build_rrule(frequency="monthly", hour=9, minute=0, by_month_day=2) == (
        "FREQ=MONTHLY;BYMONTHDAY=2;BYHOUR=9;BYMINUTE=0;BYSECOND=0"
    )


def test_once_shape() -> None:
    assert build_rrule(frequency="once", hour=3, minute=0, start_date=date(2026, 8, 1)) == (
        "FREQ=YEARLY;COUNT=1;BYMONTH=8;BYMONTHDAY=1;BYHOUR=3;BYMINUTE=0;BYSECOND=0"
    )


def test_daily_with_count() -> None:
    assert build_rrule(frequency="daily", hour=3, minute=0, count=5) == (
        "FREQ=DAILY;BYHOUR=3;BYMINUTE=0;BYSECOND=0;COUNT=5"
    )


def test_daily_with_until() -> None:
    # end_date → UNTIL at end-of-day in tz, expressed as UTC Z form
    out = build_rrule(
        frequency="daily", hour=3, minute=0, end_date=date(2026, 6, 13), timezone="UTC"
    )
    assert out == "FREQ=DAILY;BYHOUR=3;BYMINUTE=0;BYSECOND=0;UNTIL=20260613T235959Z"


# ---- build_rrule: validation ----

def test_bad_frequency() -> None:
    with pytest.raises(ScheduleError):
        build_rrule(frequency="hourly", hour=3, minute=0)


def test_bad_hour() -> None:
    with pytest.raises(ScheduleError):
        build_rrule(frequency="daily", hour=24, minute=0)


def test_bad_minute() -> None:
    with pytest.raises(ScheduleError):
        build_rrule(frequency="daily", hour=3, minute=60)


def test_weekly_requires_by_day() -> None:
    with pytest.raises(ScheduleError):
        build_rrule(frequency="weekly", hour=3, minute=0)


def test_weekly_bad_day() -> None:
    with pytest.raises(ScheduleError):
        build_rrule(frequency="weekly", hour=3, minute=0, by_day=["XX"])


def test_monthly_requires_month_day() -> None:
    with pytest.raises(ScheduleError):
        build_rrule(frequency="monthly", hour=3, minute=0)


def test_monthly_bad_month_day() -> None:
    with pytest.raises(ScheduleError):
        build_rrule(frequency="monthly", hour=3, minute=0, by_month_day=32)


def test_once_requires_start_date() -> None:
    with pytest.raises(ScheduleError):
        build_rrule(frequency="once", hour=3, minute=0)


def test_count_and_end_date_mutually_exclusive() -> None:
    with pytest.raises(ScheduleError):
        build_rrule(
            frequency="daily", hour=3, minute=0, count=3, end_date=date(2026, 6, 1)
        )


def test_bad_timezone() -> None:
    with pytest.raises(ScheduleError):
        build_rrule(frequency="daily", hour=3, minute=0, timezone="Mars/Phobos")


# ---- next_fire: scheduler-compatible round-trip (the load-bearing test) ----

def test_daily_next_fire_matches_scheduler() -> None:
    created = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
    after = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
    rrule = build_rrule(frequency="daily", hour=3, minute=0)
    ours = next_fire(rrule, timezone="UTC", dtstart=created, after=after)
    theirs = _scheduler_next(rrule, tz="UTC", created_at=created, after=after)
    assert ours == theirs == datetime(2026, 7, 23, 3, 0, tzinfo=UTC)


def test_timezone_wall_clock() -> None:
    # "daily at 03:00 America/New_York" fires at 07:00 or 08:00 UTC depending on DST.
    created = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)  # summer → EDT (UTC-4)
    rrule = build_rrule(frequency="daily", hour=3, minute=0, timezone="America/New_York")
    nxt = next_fire(rrule, timezone="America/New_York", dtstart=created, after=created)
    assert nxt == datetime(2026, 7, 23, 7, 0, tzinfo=UTC)


def test_weekly_next_fire() -> None:
    created = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)  # 2026-07-22 is a Wednesday
    rrule = build_rrule(frequency="weekly", hour=13, minute=0, by_day=["WE"])
    nxt = next_fire(rrule, timezone="UTC", dtstart=created, after=created)
    assert nxt == datetime(2026, 7, 22, 13, 0, tzinfo=UTC)  # same-day Wed, 13:00 > 12:00


def test_once_next_fire_then_exhausted() -> None:
    created = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
    rrule = build_rrule(frequency="once", hour=3, minute=0, start_date=date(2026, 8, 1))
    first = next_fire(rrule, timezone="UTC", dtstart=created, after=created)
    assert first == datetime(2026, 8, 1, 3, 0, tzinfo=UTC)
    # after it fires, no next occurrence (COUNT=1 exhausted)
    assert next_fire(rrule, timezone="UTC", dtstart=created, after=first) is None


def test_count_bounded_exhausts() -> None:
    created = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
    rrule = build_rrule(frequency="daily", hour=3, minute=0, count=2)
    f1 = next_fire(rrule, timezone="UTC", dtstart=created, after=created)
    f2 = next_fire(rrule, timezone="UTC", dtstart=created, after=f1)
    assert f1 == datetime(2026, 7, 23, 3, 0, tzinfo=UTC)
    assert f2 == datetime(2026, 7, 24, 3, 0, tzinfo=UTC)
    assert next_fire(rrule, timezone="UTC", dtstart=created, after=f2) is None


# ---- summarize_schedule ----

def test_summary_daily() -> None:
    assert summarize_schedule(frequency="daily", hour=3, minute=0) == "daily at 03:00 (UTC)"


def test_summary_weekly() -> None:
    assert (
        summarize_schedule(frequency="weekly", hour=13, minute=0, by_day=["WE"])
        == "weekly on WE at 13:00 (UTC)"
    )


def test_summary_monthly_with_count() -> None:
    assert (
        summarize_schedule(frequency="monthly", hour=9, minute=0, by_month_day=2, count=3)
        == "monthly on day 2 at 09:00 (UTC), 3 times"
    )


def test_summary_once() -> None:
    assert (
        summarize_schedule(frequency="once", hour=3, minute=0, start_date=date(2026, 8, 1))
        == "once on 2026-08-01 at 03:00 (UTC)"
    )
```

- [ ] **Step 3: 跑测试确认失败**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && DOCKER_HOST= uv run --project services/orchestrator pytest services/orchestrator/tests/tools/test_task_schedule.py -q`
Expected: FAIL(`ModuleNotFoundError: orchestrator.tools.task_schedule`)。

- [ ] **Step 4: 写实现**

`services/orchestrator/src/orchestrator/tools/task_schedule.py`:

```python
"""Structured schedule fields → RFC 5545 RRULE, and the inverse read helpers.

The scheduler (``control_plane.scheduler._next_occurrence``) consumes
``config['rrule']`` with ``dtstart`` injected from ``trigger.created_at`` in
``config['timezone']``. Therefore the strings built here MUST NOT carry a
``DTSTART`` and MUST express the wall-clock via ``BYHOUR``/``BYMINUTE`` so the
tz-aware dtstart pins the correct local time. ``manage_task`` is the only
producer of these strings (Spec 1 PR2).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrulestr

_FREQUENCIES = frozenset({"once", "daily", "weekly", "monthly"})
_WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
_WEEKDAY_SET = frozenset(_WEEKDAYS)


class ScheduleError(ValueError):
    """Invalid schedule input — surfaced to the LLM as a tool error message."""


def _zone(timezone: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ScheduleError(f"unknown timezone {timezone!r}") from exc


def _check_common(frequency: str, hour: int, minute: int, count: int | None) -> None:
    if frequency not in _FREQUENCIES:
        raise ScheduleError(
            f"frequency must be one of {sorted(_FREQUENCIES)}, got {frequency!r}"
        )
    if not isinstance(hour, int) or not 0 <= hour <= 23:
        raise ScheduleError("hour must be an integer 0-23")
    if not isinstance(minute, int) or not 0 <= minute <= 59:
        raise ScheduleError("minute must be an integer 0-59")
    if count is not None and (not isinstance(count, int) or count < 1):
        raise ScheduleError("count must be a positive integer")


def _until_token(end_date: date, tz: ZoneInfo) -> str:
    """RFC 5545 UNTIL — end-of-day in the schedule's tz, expressed as UTC ``Z``."""
    end = datetime.combine(end_date, time(23, 59, 59), tzinfo=tz).astimezone(UTC)
    return end.strftime("%Y%m%dT%H%M%SZ")


def build_rrule(
    *,
    frequency: str,
    hour: int,
    minute: int,
    by_day: Sequence[str] | None = None,
    by_month_day: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    count: int | None = None,
    timezone: str = "UTC",
) -> str:
    """Compile structured fields to a DTSTART-less RRULE string. Raises
    :class:`ScheduleError` on any invalid combination."""
    _check_common(frequency, hour, minute, count)
    tz = _zone(timezone)

    if count is not None and end_date is not None:
        raise ScheduleError("give either count or end_date, not both")

    parts: list[str]
    if frequency == "daily":
        parts = ["FREQ=DAILY"]
    elif frequency == "weekly":
        if not by_day:
            raise ScheduleError("weekly schedule needs at least one day (by_day)")
        days = [d.upper() for d in by_day]
        for d in days:
            if d not in _WEEKDAY_SET:
                raise ScheduleError(f"by_day entries must be one of {list(_WEEKDAYS)}")
        parts = ["FREQ=WEEKLY", f"BYDAY={','.join(days)}"]
    elif frequency == "monthly":
        if by_month_day is None or not 1 <= by_month_day <= 31:
            raise ScheduleError("monthly schedule needs by_month_day (1-31)")
        parts = ["FREQ=MONTHLY", f"BYMONTHDAY={by_month_day}"]
    else:  # once
        if start_date is None:
            raise ScheduleError("a one-off task needs a start_date")
        parts = [
            "FREQ=YEARLY",
            "COUNT=1",
            f"BYMONTH={start_date.month}",
            f"BYMONTHDAY={start_date.day}",
        ]

    parts += [f"BYHOUR={hour}", f"BYMINUTE={minute}", "BYSECOND=0"]

    if frequency != "once":
        if count is not None:
            parts.append(f"COUNT={count}")
        if end_date is not None:
            parts.append(f"UNTIL={_until_token(end_date, tz)}")

    return ";".join(parts)


def next_fire(
    rrule: str, *, timezone: str, dtstart: datetime, after: datetime
) -> datetime | None:
    """Next fire strictly after ``after`` in UTC, or None if exhausted — a
    faithful mirror of ``scheduler._next_occurrence`` so create-time preview and
    the real scheduler agree."""
    tz = _zone(timezone)
    occ = rrulestr(rrule, dtstart=dtstart.astimezone(tz)).after(after.astimezone(tz))
    return occ.astimezone(UTC) if occ is not None else None


def summarize_schedule(
    *,
    frequency: str,
    hour: int,
    minute: int,
    by_day: Sequence[str] | None = None,
    by_month_day: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    count: int | None = None,
    timezone: str = "UTC",
) -> str:
    """A short human-readable line for the list action (stored as config['summary'])."""
    hm = f"{hour:02d}:{minute:02d}"
    if frequency == "daily":
        base = f"daily at {hm}"
    elif frequency == "weekly":
        days = ",".join(d.upper() for d in (by_day or []))
        base = f"weekly on {days} at {hm}"
    elif frequency == "monthly":
        base = f"monthly on day {by_month_day} at {hm}"
    else:  # once
        base = f"once on {start_date.isoformat() if start_date else '?'} at {hm}"
    out = f"{base} ({timezone})"
    if frequency != "once":
        if count is not None:
            out += f", {count} times"
        elif end_date is not None:
            out += f", until {end_date.isoformat()}"
    return out
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && DOCKER_HOST= uv run --project services/orchestrator pytest services/orchestrator/tests/tools/test_task_schedule.py -q`
Expected: PASS(全绿)。

- [ ] **Step 6: lint + type**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && uv run ruff check services/orchestrator/src/orchestrator/tools/task_schedule.py services/orchestrator/tests/tools/test_task_schedule.py && uv run ruff format --check services/orchestrator/src/orchestrator/tools/task_schedule.py services/orchestrator/tests/tools/test_task_schedule.py && uv run --project services/orchestrator mypy services/orchestrator/src/orchestrator/tools/task_schedule.py`
Expected: All clean(如 format 不干净,跑 `uv run ruff format <file>` 收敛后重跑)。

- [ ] **Step 7: 提交**

```bash
cd /Users/mac/src/github/jone_qian/expert-work
git add services/orchestrator/src/orchestrator/tools/task_schedule.py services/orchestrator/tests/tools/test_task_schedule.py services/orchestrator/pyproject.toml uv.lock
git commit -m "feat(tools): RRULE 编译模块 task_schedule(结构化字段→RRULE + 下次触发 + 人话摘要)"
```

---

## Task 2: `ToolContext` 加 `thread_id` + `trigger_origin`

**Files:**
- Modify: `services/orchestrator/src/orchestrator/tools/registry.py:154-213`(`ToolContext` dataclass)
- Modify: `services/orchestrator/src/orchestrator/graph_builder/builder.py:2638-2688`(`_build_tool_context`)
- Test: `services/orchestrator/tests/graph_builder/test_build_tool_context.py`(新;若已有就近文件则扩)

**Interfaces:**
- Consumes: `config["configurable"]` 里已存在的 `thread_id`(runs.py:850 / trigger_firing.py:262 两处 run 路径都写),以及 T5 将写入的 `trigger_origin`。
- Produces:
  - `ToolContext.thread_id: UUID | None = None` —— 供 `ManageTaskTool` 设 `originating_thread_id`(T3)。
  - `ToolContext.trigger_origin: bool = False` —— 定时触发的 run 标记(T3 调用期护栏 + T5 绑定期过滤读它)。

- [ ] **Step 1: 写失败测试**

`services/orchestrator/tests/graph_builder/test_build_tool_context.py`:

```python
from __future__ import annotations

from uuid import uuid4

from orchestrator.graph_builder.builder import _build_tool_context


def test_lifts_thread_id() -> None:
    thread_id = uuid4()
    ctx = _build_tool_context(
        {"configurable": {"thread_id": str(thread_id), "tenant_id": str(uuid4())}}
    )
    assert ctx.thread_id == thread_id


def test_trigger_origin_defaults_false() -> None:
    ctx = _build_tool_context({"configurable": {"thread_id": str(uuid4())}})
    assert ctx.trigger_origin is False


def test_trigger_origin_true_when_flagged() -> None:
    ctx = _build_tool_context(
        {"configurable": {"thread_id": str(uuid4()), "trigger_origin": True}}
    )
    assert ctx.trigger_origin is True


def test_missing_thread_id_is_none() -> None:
    ctx = _build_tool_context({"configurable": {"tenant_id": str(uuid4())}})
    assert ctx.thread_id is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && DOCKER_HOST= uv run --project services/orchestrator pytest services/orchestrator/tests/graph_builder/test_build_tool_context.py -q`
Expected: FAIL(`AttributeError: ... 'thread_id'` / `'trigger_origin'`)。

- [ ] **Step 3: `ToolContext` 加字段**

`registry.py` 的 `ToolContext`(:154-213),在现有字段旁加两行(放在 `user_id` 之后、`oauth_user_id` 之前或紧随其后即可,匹配现有风格):

```python
    #: originating conversation thread — set on the normal + trigger run paths;
    #: manage_task stamps it as a task's delivery target (Spec 1).
    thread_id: UUID | None = None
    #: True when this run was started by the scheduler (fire_trigger). The
    #: manage_task tool is filtered from the LLM bind list and refuses to run
    #: under it (self-scheduling guardrail, Spec 1 D-13).
    trigger_origin: bool = False
```

- [ ] **Step 4: `_build_tool_context` 提取**

`builder.py` 的 `_build_tool_context`(:2638-2688),在读 `tenant_id`/`run_id`/`user_id` 附近加提取,并在返回的 `ToolContext(...)` 里传入:

```python
    thread_id = _parse_uuid(configurable.get("thread_id"))
    trigger_origin = bool(configurable.get("trigger_origin", False))
```

返回处加:

```python
        thread_id=thread_id,
        trigger_origin=trigger_origin,
```

(`_parse_uuid` 已在该文件用于 tenant_id/run_id,直接复用。)

- [ ] **Step 5: 跑测试确认通过**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && DOCKER_HOST= uv run --project services/orchestrator pytest services/orchestrator/tests/graph_builder/test_build_tool_context.py -q`
Expected: PASS。

- [ ] **Step 6: lint + type + 回归**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && uv run ruff check services/orchestrator/src/orchestrator/tools/registry.py services/orchestrator/src/orchestrator/graph_builder/builder.py && uv run ruff format --check services/orchestrator/src/orchestrator/tools/registry.py services/orchestrator/src/orchestrator/graph_builder/builder.py && uv run --project services/orchestrator mypy services/orchestrator/src/orchestrator/tools/registry.py services/orchestrator/src/orchestrator/graph_builder/builder.py`
Expected: clean。

- [ ] **Step 7: 提交**

```bash
cd /Users/mac/src/github/jone_qian/expert-work
git add services/orchestrator/src/orchestrator/tools/registry.py services/orchestrator/src/orchestrator/graph_builder/builder.py services/orchestrator/tests/graph_builder/test_build_tool_context.py
git commit -m "feat(tools): ToolContext 加 thread_id + trigger_origin(供 manage_task 与自排护栏)"
```

---

## Task 3: `ManageTaskTool`(4 action + 调用期护栏)

**Files:**
- Create: `services/orchestrator/src/orchestrator/tools/manage_task.py`
- Test: `services/orchestrator/tests/tools/test_manage_task.py`

**Interfaces:**
- Consumes: `build_rrule`/`summarize_schedule`/`next_fire`/`ScheduleError`(T1);`ToolContext.thread_id`/`trigger_origin`(T2);`Tool`/`ToolSpec`/`ToolResult`/`ToolContext`/`ToolBlockedError`(registry.py);`TriggerStore`(`expert_work.persistence.trigger.base`);`TriggerRecord`(`expert_work.protocol`)。
- Produces: `class ManageTaskTool`(`@dataclass(frozen=True)`,字段 `store: TriggerStore`、`agent_name: str`、`agent_version: str`),满足 `Tool` Protocol(`spec` property + `async call`)。供 T4 注册。

- [ ] **Step 1: 写失败测试**

`services/orchestrator/tests/tools/test_manage_task.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from expert_work.protocol import TriggerRecord
from orchestrator.tools.manage_task import ManageTaskTool
from orchestrator.tools.registry import ToolBlockedError, ToolContext


class _FakeStore:
    """In-memory stand-in for TriggerStore — only the methods manage_task uses."""

    def __init__(self) -> None:
        self.rows: dict[UUID, TriggerRecord] = {}

    async def create(self, record: TriggerRecord) -> TriggerRecord:
        for r in self.rows.values():
            if (r.tenant_id, r.agent_name, r.user_id, r.name) == (
                record.tenant_id, record.agent_name, record.user_id, record.name
            ):
                raise ValueError("duplicate")
        self.rows[record.id] = record
        return record

    async def list_by_user(self, *, tenant_id, user_id, agent_name=None):
        return sorted(
            (
                r
                for r in self.rows.values()
                if r.tenant_id == tenant_id
                and r.user_id == user_id
                and (agent_name is None or r.agent_name == agent_name)
            ),
            key=lambda r: r.created_at,
        )

    async def get(self, *, trigger_id, tenant_id):
        r = self.rows.get(trigger_id)
        return r if r is not None and r.tenant_id == tenant_id else None

    async def update(self, record: TriggerRecord) -> bool:
        if record.id in self.rows:
            self.rows[record.id] = record
            return True
        return False

    async def delete(self, *, trigger_id, tenant_id) -> bool:
        r = self.rows.get(trigger_id)
        if r is not None and r.tenant_id == tenant_id:
            del self.rows[trigger_id]
            return True
        return False


def _tool(store: _FakeStore) -> ManageTaskTool:
    return ManageTaskTool(store=store, agent_name="news-bot", agent_version="1")  # type: ignore[arg-type]


def _ctx(*, tenant_id: UUID, user_id: UUID | None, thread_id: UUID | None = None, trigger_origin: bool = False) -> ToolContext:
    return ToolContext(
        tenant_id=tenant_id, user_id=user_id, thread_id=thread_id, trigger_origin=trigger_origin
    )


# ---- spec / contract ----

def test_spec_name_frozen() -> None:
    assert _tool(_FakeStore()).spec.name == "manage_task"


def test_spec_has_field_descriptions() -> None:
    props = _tool(_FakeStore()).spec.parameters["properties"]
    # guidance lives in the schema, not the system prompt (D-7)
    assert props["action"]["description"]
    assert props["time"]["description"]


# ---- create ----

@pytest.mark.asyncio
async def test_create_persists_reuse_thread_row() -> None:
    store = _FakeStore()
    tenant, user, thread = uuid4(), uuid4(), uuid4()
    res = await _tool(store).call(
        {
            "action": "create",
            "instruction": "summarize AI news",
            "frequency": "daily",
            "time": {"hour": 3, "minute": 0},
        },
        ctx=_ctx(tenant_id=tenant, user_id=user, thread_id=thread),
    )
    (row,) = store.rows.values()
    assert row.user_id == user
    assert row.agent_name == "news-bot" and row.agent_version == "1"
    assert row.kind == "cron" and row.source == "api"
    assert row.context_mode == "reuse_thread"
    assert row.originating_thread_id == thread
    assert row.config["rrule"] == "FREQ=DAILY;BYHOUR=3;BYMINUTE=0;BYSECOND=0"
    assert row.config["seed_input"] == "summarize AI news"
    assert row.config["timezone"] == "UTC"
    assert "daily at 03:00" in row.config["summary"]
    assert "news" in res.content.lower() or "created" in res.content.lower()


@pytest.mark.asyncio
async def test_create_missing_time_errors_for_reask() -> None:
    store = _FakeStore()
    with pytest.raises(ValueError, match="time"):
        await _tool(store).call(
            {"action": "create", "instruction": "x", "frequency": "daily"},
            ctx=_ctx(tenant_id=uuid4(), user_id=uuid4()),
        )
    assert not store.rows  # nothing written


@pytest.mark.asyncio
async def test_create_requires_user() -> None:
    with pytest.raises(ValueError, match="user"):
        await _tool(_FakeStore()).call(
            {
                "action": "create",
                "instruction": "x",
                "frequency": "daily",
                "time": {"hour": 3, "minute": 0},
            },
            ctx=_ctx(tenant_id=uuid4(), user_id=None),
        )


@pytest.mark.asyncio
async def test_create_duplicate_name_friendly_error() -> None:
    store = _FakeStore()
    tenant, user = uuid4(), uuid4()
    args = {
        "action": "create",
        "instruction": "same name job",
        "name": "digest",
        "frequency": "daily",
        "time": {"hour": 3, "minute": 0},
    }
    await _tool(store).call(args, ctx=_ctx(tenant_id=tenant, user_id=user))
    with pytest.raises(ValueError, match="already exists"):
        await _tool(store).call(args, ctx=_ctx(tenant_id=tenant, user_id=user))


@pytest.mark.asyncio
async def test_create_once_in_past_rejected() -> None:
    with pytest.raises(ValueError, match="past"):
        await _tool(_FakeStore()).call(
            {
                "action": "create",
                "instruction": "x",
                "frequency": "once",
                "time": {"hour": 3, "minute": 0},
                "start_date": "2000-01-01",
            },
            ctx=_ctx(tenant_id=uuid4(), user_id=uuid4()),
        )


# ---- list ----

@pytest.mark.asyncio
async def test_list_shows_user_tasks() -> None:
    store = _FakeStore()
    tenant, user = uuid4(), uuid4()
    await _tool(store).call(
        {
            "action": "create",
            "instruction": "job A",
            "name": "aaa",
            "frequency": "daily",
            "time": {"hour": 3, "minute": 0},
        },
        ctx=_ctx(tenant_id=tenant, user_id=user),
    )
    res = await _tool(store).call({"action": "list"}, ctx=_ctx(tenant_id=tenant, user_id=user))
    assert "aaa" in res.content


@pytest.mark.asyncio
async def test_list_empty() -> None:
    res = await _tool(_FakeStore()).call(
        {"action": "list"}, ctx=_ctx(tenant_id=uuid4(), user_id=uuid4())
    )
    assert "no scheduled tasks" in res.content.lower()


# ---- update / cancel ownership ----

@pytest.mark.asyncio
async def test_update_other_users_task_rejected() -> None:
    store = _FakeStore()
    tenant, owner, attacker = uuid4(), uuid4(), uuid4()
    await _tool(store).call(
        {
            "action": "create",
            "instruction": "owned",
            "name": "owned",
            "frequency": "daily",
            "time": {"hour": 3, "minute": 0},
        },
        ctx=_ctx(tenant_id=tenant, user_id=owner),
    )
    (tid,) = store.rows.keys()
    with pytest.raises(ValueError, match="no such task"):
        await _tool(store).call(
            {"action": "cancel", "task_id": str(tid)},
            ctx=_ctx(tenant_id=tenant, user_id=attacker),
        )
    assert store.rows  # not deleted


@pytest.mark.asyncio
async def test_update_enabled_toggle() -> None:
    store = _FakeStore()
    tenant, user = uuid4(), uuid4()
    await _tool(store).call(
        {
            "action": "create",
            "instruction": "job",
            "name": "j",
            "frequency": "daily",
            "time": {"hour": 3, "minute": 0},
        },
        ctx=_ctx(tenant_id=tenant, user_id=user),
    )
    (tid,) = store.rows.keys()
    await _tool(store).call(
        {"action": "update", "task_id": str(tid), "enabled": False},
        ctx=_ctx(tenant_id=tenant, user_id=user),
    )
    assert store.rows[tid].enabled is False


@pytest.mark.asyncio
async def test_cancel_removes() -> None:
    store = _FakeStore()
    tenant, user = uuid4(), uuid4()
    await _tool(store).call(
        {
            "action": "create",
            "instruction": "job",
            "name": "j",
            "frequency": "daily",
            "time": {"hour": 3, "minute": 0},
        },
        ctx=_ctx(tenant_id=tenant, user_id=user),
    )
    (tid,) = store.rows.keys()
    await _tool(store).call(
        {"action": "cancel", "task_id": str(tid)}, ctx=_ctx(tenant_id=tenant, user_id=user)
    )
    assert not store.rows


# ---- self-scheduling guardrail (D-13) ----

@pytest.mark.asyncio
async def test_blocked_under_trigger_origin() -> None:
    with pytest.raises(ToolBlockedError):
        await _tool(_FakeStore()).call(
            {"action": "list"},
            ctx=_ctx(tenant_id=uuid4(), user_id=uuid4(), trigger_origin=True),
        )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && DOCKER_HOST= uv run --project services/orchestrator pytest services/orchestrator/tests/tools/test_manage_task.py -q`
Expected: FAIL(`ModuleNotFoundError: orchestrator.tools.manage_task`)。

- [ ] **Step 3: 写实现**

`services/orchestrator/src/orchestrator/tools/manage_task.py`:

```python
"""``manage_task`` — conversational scheduled-task management (Spec 1 PR2).

A single tool with an ``action`` switch (create/list/update/cancel). It writes
per-user ``agent_trigger`` rows through an injected ``TriggerStore``; the
scheduler later fires them and (PR3) delivers results back into the originating
conversation. Guidance for the model lives entirely in this tool's schema
``description`` fields (D-7) — never in the system prompt. The tool name and
parameter schema are frozen (D-8) so a future instructional skill can reference
them by name.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from expert_work.persistence.trigger.base import TriggerStore
from expert_work.protocol import TriggerRecord
from orchestrator.tools.registry import (
    ToolBlockedError,
    ToolContext,
    ToolResult,
    ToolSpec,
)
from orchestrator.tools.task_schedule import (
    ScheduleError,
    build_rrule,
    next_fire,
    summarize_schedule,
)

_SCHEDULE_KEYS = frozenset(
    {"frequency", "time", "by_day", "by_month_day", "start_date", "end_date", "count", "timezone"}
)

_DESCRIPTION = (
    "Create and manage the user's scheduled tasks — recurring or one-off jobs "
    "that run this agent automatically at a set time and deliver results back "
    "into this conversation. action=create sets one up (e.g. 'every day at 3pm, "
    "summarize AI news'); list shows the user's tasks; update changes one; "
    "cancel removes one. For create/update you MUST have a concrete time — if "
    "the user hasn't given one, ASK them first, don't guess. Pass update/cancel "
    "the task_id from a prior list."
)

_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "enum": ["create", "list", "update", "cancel"],
            "description": "Which operation to perform.",
        },
        "task_id": {
            "type": "string",
            "description": "The task's id (from list). Required for update and cancel.",
        },
        "name": {
            "type": "string",
            "description": (
                "Optional short label for the task (create/update). If omitted "
                "on create, one is derived from the instruction. Must be unique "
                "among the user's tasks for this agent."
            ),
        },
        "instruction": {
            "type": "string",
            "description": (
                "What the task should do each time it runs — becomes the agent's "
                "prompt for that run. Required on create."
            ),
        },
        "frequency": {
            "enum": ["once", "daily", "weekly", "monthly"],
            "description": "How often the task runs. Required on create.",
        },
        "time": {
            "type": "object",
            "properties": {
                "hour": {"type": "integer", "minimum": 0, "maximum": 23},
                "minute": {"type": "integer", "minimum": 0, "maximum": 59},
            },
            "required": ["hour", "minute"],
            "description": (
                "Time of day in 24h form. REQUIRED on create/update. If the user "
                "did not give a specific time, ask them before calling this tool."
            ),
        },
        "by_day": {
            "type": "array",
            "items": {"enum": ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]},
            "description": "Days of week, for frequency=weekly (e.g. ['WE']).",
        },
        "by_month_day": {
            "type": "integer",
            "minimum": 1,
            "maximum": 31,
            "description": "Day of month 1-31, for frequency=monthly.",
        },
        "start_date": {
            "type": "string",
            "description": "ISO date YYYY-MM-DD. Required for frequency=once (the day it runs).",
        },
        "end_date": {
            "type": "string",
            "description": "ISO date YYYY-MM-DD. Optional bound — the task stops after this day.",
        },
        "count": {
            "type": "integer",
            "minimum": 1,
            "description": "Optional — run this many times then stop (mutually exclusive with end_date).",
        },
        "timezone": {
            "type": "string",
            "description": "IANA timezone (e.g. America/New_York). Optional; defaults to UTC.",
        },
        "enabled": {
            "type": "boolean",
            "description": "update only — pause (false) or resume (true) a task.",
        },
    },
    "required": ["action"],
}


def _parse_uuid(raw: Any) -> UUID | None:
    if isinstance(raw, UUID):
        return raw
    if isinstance(raw, str) and raw:
        try:
            return UUID(raw)
        except ValueError:
            return None
    return None


def _parse_date(raw: Any) -> date | None:
    if isinstance(raw, str) and raw:
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(f"bad date {raw!r} — use YYYY-MM-DD") from exc
    return None


def _time_fields(args: Mapping[str, Any]) -> tuple[int, int]:
    time = args.get("time")
    if not isinstance(time, Mapping) or time.get("hour") is None or time.get("minute") is None:
        raise ValueError(
            "I need a specific time (hour and minute) — ask the user what time, e.g. 3pm."
        )
    return int(time["hour"]), int(time["minute"])


def _compile(args: Mapping[str, Any]) -> tuple[str, str, str]:
    """(rrule, timezone, summary) from the schedule args. Raises ValueError on bad input."""
    frequency = args.get("frequency")
    if frequency is None:
        raise ValueError("a task needs a frequency (once/daily/weekly/monthly)")
    hour, minute = _time_fields(args)
    tz = str(args.get("timezone") or "UTC")
    by_day = args.get("by_day")
    by_month_day = args.get("by_month_day")
    start_date = _parse_date(args.get("start_date"))
    end_date = _parse_date(args.get("end_date"))
    count = args.get("count")
    try:
        rrule = build_rrule(
            frequency=frequency, hour=hour, minute=minute, by_day=by_day,
            by_month_day=by_month_day, start_date=start_date, end_date=end_date,
            count=count, timezone=tz,
        )
        summary = summarize_schedule(
            frequency=frequency, hour=hour, minute=minute, by_day=by_day,
            by_month_day=by_month_day, start_date=start_date, end_date=end_date,
            count=count, timezone=tz,
        )
    except ScheduleError as exc:
        raise ValueError(str(exc)) from exc
    return rrule, tz, summary


@dataclass(frozen=True)
class ManageTaskTool:
    """``manage_task(action, ...)`` — conversational scheduled tasks."""

    store: TriggerStore
    agent_name: str
    agent_version: str

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="manage_task",
            description=_DESCRIPTION,
            parameters=_PARAMETERS,
            side_effect="reversible",
        )

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        if ctx.trigger_origin:
            raise ToolBlockedError(
                "scheduled runs cannot create or change tasks (self-scheduling guardrail)"
            )
        action = args.get("action")
        if action == "create":
            return await self._create(args, ctx)
        if action == "list":
            return await self._list(ctx)
        if action == "update":
            return await self._update(args, ctx)
        if action == "cancel":
            return await self._cancel(args, ctx)
        raise ValueError(f"unknown action {action!r} (use create/list/update/cancel)")

    def _scope(self, ctx: ToolContext) -> tuple[UUID, UUID]:
        if ctx.tenant_id is None or ctx.user_id is None:
            raise ValueError("managing tasks requires a user-bound conversation")
        return ctx.tenant_id, ctx.user_id

    async def _create(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        tenant_id, user_id = self._scope(ctx)
        instruction = str(args.get("instruction", "")).strip()
        if not instruction:
            raise ValueError("a task needs an instruction (what to do each run)")
        rrule, tz, summary = _compile(args)
        now = datetime.now(UTC)

        # once must be in the future — a past date/time would silently roll a year.
        if args.get("frequency") == "once":
            nxt = next_fire(rrule, timezone=tz, dtstart=now, after=now)
            if nxt is None or nxt <= now:
                raise ValueError("that date/time is in the past — pick a future time")
        else:
            nxt = next_fire(rrule, timezone=tz, dtstart=now, after=now)
            if nxt is None:
                raise ValueError("that schedule has no upcoming run — check the dates")

        raw_name = str(args.get("name", "")).strip() or instruction
        name = raw_name[:60]

        # friendly pre-check (single-user conversational tool — TOCTOU race is
        # caught by the create() call below and surfaces as a generic error).
        existing = await self.store.list_by_user(
            tenant_id=tenant_id, user_id=user_id, agent_name=self.agent_name
        )
        if any(r.name == name for r in existing):
            raise ValueError(f"a task named {name!r} already exists — use a different name")

        record = TriggerRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            agent_name=self.agent_name,
            agent_version=self.agent_version,
            name=name,
            kind="cron",
            config={"rrule": rrule, "timezone": tz, "seed_input": instruction, "summary": summary},
            enabled=True,
            source="api",
            originating_thread_id=ctx.thread_id,
            context_mode="reuse_thread",
            created_at=now,
            updated_at=now,
        )
        try:
            await self.store.create(record)
        except ValueError as exc:
            raise ValueError(f"a task named {name!r} already exists — use a different name") from exc
        return ToolResult(
            content=(
                f"Created task {name!r}: {summary}. Next run "
                f"{nxt:%Y-%m-%d %H:%M UTC}. Results will appear here when it runs."
            ),
            meta={"trigger_id": str(record.id)},
        )

    async def _list(self, ctx: ToolContext) -> ToolResult:
        tenant_id, user_id = self._scope(ctx)
        rows = await self.store.list_by_user(
            tenant_id=tenant_id, user_id=user_id, agent_name=self.agent_name
        )
        if not rows:
            return ToolResult(content="You have no scheduled tasks.")
        now = datetime.now(UTC)
        lines: list[str] = []
        for r in rows:
            summary = str(r.config.get("summary") or "(schedule)")
            state = "" if r.enabled else " [paused]"
            nxt_s = ""
            if r.enabled:
                rrule = str(r.config.get("rrule") or "")
                tz = str(r.config.get("timezone") or "UTC")
                if rrule:
                    nxt = next_fire(rrule, timezone=tz, dtstart=r.created_at, after=now)
                    if nxt is not None:
                        nxt_s = f", next {nxt:%Y-%m-%d %H:%M UTC}"
            lines.append(f"- {r.name}{state}: {summary}{nxt_s} (id {r.id})")
        return ToolResult(content="Your scheduled tasks:\n" + "\n".join(lines))

    async def _get_owned(self, args: Mapping[str, Any], ctx: ToolContext) -> TriggerRecord:
        tenant_id, user_id = self._scope(ctx)
        tid = _parse_uuid(args.get("task_id"))
        if tid is None:
            raise ValueError("that action needs a task_id (from list)")
        rec = await self.store.get(trigger_id=tid, tenant_id=tenant_id)
        if rec is None or rec.user_id != user_id:
            # Do not distinguish "missing" from "someone else's" — no ownership leak.
            raise ValueError("no such task")
        return rec

    async def _update(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        rec = await self._get_owned(args, ctx)
        updates: dict[str, Any] = {"updated_at": datetime.now(UTC)}
        config = dict(rec.config)

        if isinstance(args.get("enabled"), bool):
            updates["enabled"] = args["enabled"]

        if any(k in args for k in _SCHEDULE_KEYS):
            rrule, tz, summary = _compile(args)
            config["rrule"] = rrule
            config["timezone"] = tz
            config["summary"] = summary

        instruction = args.get("instruction")
        if isinstance(instruction, str) and instruction.strip():
            config["seed_input"] = instruction.strip()

        updates["config"] = config
        ok = await self.store.update(rec.model_copy(update=updates))
        if not ok:
            raise ValueError("couldn't update the task")
        return ToolResult(
            content=f"Updated task {rec.name!r}.", meta={"trigger_id": str(rec.id)}
        )

    async def _cancel(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        rec = await self._get_owned(args, ctx)
        ok = await self.store.delete(trigger_id=rec.id, tenant_id=rec.tenant_id)
        if not ok:
            raise ValueError("couldn't cancel the task")
        return ToolResult(content=f"Cancelled task {rec.name!r}.")
```

> **实现者注**:`ToolBlockedError` 确在 `orchestrator.tools.registry`(Explore 实锤 registry.py:318)。`ToolSpec.side_effect` 取值 `"read_only"|"reversible"|"irreversible"`(registry.py 定义);`manage_task` 用 `"reversible"`。若 `TriggerStore` 的 import 路径在本仓是 `expert_work.persistence`(顶层 re-export)而非 `.trigger.base`,以真实存在者为准(两者皆可,择 orchestrator 现有同类 import 风格)。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && DOCKER_HOST= uv run --project services/orchestrator pytest services/orchestrator/tests/tools/test_manage_task.py -q`
Expected: PASS(全绿)。

- [ ] **Step 5: lint + type**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && uv run ruff check services/orchestrator/src/orchestrator/tools/manage_task.py services/orchestrator/tests/tools/test_manage_task.py && uv run ruff format --check services/orchestrator/src/orchestrator/tools/manage_task.py services/orchestrator/tests/tools/test_manage_task.py && uv run --project services/orchestrator mypy services/orchestrator/src/orchestrator/tools/manage_task.py`
Expected: clean。

- [ ] **Step 6: 提交**

```bash
cd /Users/mac/src/github/jone_qian/expert-work
git add services/orchestrator/src/orchestrator/tools/manage_task.py services/orchestrator/tests/tools/test_manage_task.py
git commit -m "feat(tools): ManageTaskTool(create/list/update/cancel + 所有权校验 + 调用期自排护栏)"
```

---

## Task 4: 挂载 —— `KNOWN_BUILTINS` + `build_agent` 注册 + 全链路接线

**Files:**
- Modify: `services/orchestrator/src/orchestrator/tools/assembly.py:73-98`(`KNOWN_BUILTINS`)、`:448-479`(`_register_builtin`)
- Modify: `services/orchestrator/src/orchestrator/agent_factory.py`(`build_agent` 加 `trigger_store` kwarg + gated 注册,照 skill-authoring block :880-903)
- Modify: `services/control-plane/src/control_plane/runtime.py`(`make_agent_builder` 加 `trigger_store` 参 + 转发进 `build_agent`,照 `skill_store`)
- Modify: `services/control-plane/src/control_plane/app.py:1450`(主 `make_agent_builder(...)` 调用加 `trigger_store=resolved_trigger_store`)
- Test: `services/orchestrator/tests/test_agent_factory.py`(扩 register-spy 测)

**Interfaces:**
- Consumes: `ManageTaskTool`(T3);`resolved_trigger_store`(app.py:637 已存在);`spec.metadata.name`/`spec.metadata.version`(agent_spec.py:48-49)。
- Produces: manifest `tools:` 声明 `manage_task` + `trigger_store` 已接线 → `build_agent` 注册 `ManageTaskTool(store, name, version)`;声明但 store 未接线 → `AgentFactoryError`;未声明 → 不注册。

- [ ] **Step 1: 写失败测试**(扩 `test_agent_factory.py`,复用其 `_spy_on_register`/`_build`/`_spec` 现有 fixture)

```python
# 追加到 services/orchestrator/tests/test_agent_factory.py

def _spec_with_manage_task():
    """A minimal spec whose manifest declares the manage_task builtin."""
    # 照本文件 _spec()/_MINIMAL_SPEC 的构造,tools 里加 BuiltinToolSpec(name="manage_task")。
    # 实现者:复制现有 _spec() helper,往 tools 列表塞一个 BuiltinToolSpec(name="manage_task")。
    ...


class _FakeTriggerStore:
    async def create(self, record):  # pragma: no cover - unused in build test
        return record


@pytest.mark.asyncio
async def test_build_agent_registers_manage_task_when_declared_and_wired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registered = _spy_on_register(monkeypatch)
    async with make_checkpointer("memory") as cp:
        await _build(
            _spec_with_manage_task(),
            secret_store=_secret_store(),
            checkpointer=cp,
            trigger_store=_FakeTriggerStore(),
        )
    assert "manage_task" in registered


@pytest.mark.asyncio
async def test_build_agent_manage_task_declared_without_store_raises() -> None:
    async with make_checkpointer("memory") as cp:
        with pytest.raises(AgentFactoryError, match="manage_task"):
            await _build(
                _spec_with_manage_task(),
                secret_store=_secret_store(),
                checkpointer=cp,
                trigger_store=None,
            )


@pytest.mark.asyncio
async def test_build_agent_no_manage_task_when_not_declared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registered = _spy_on_register(monkeypatch)
    async with make_checkpointer("memory") as cp:
        await _build(_spec(), secret_store=_secret_store(), checkpointer=cp)
    assert "manage_task" not in registered
```

> **实现者注**:`_build` 是本文件包装 `build_agent` 的 helper(见 test_agent_factory.py:584-621 上下文)。若 `_build` 不透传任意 kwarg,给它加 `trigger_store` 透传参。`_spec_with_manage_task` 照现有 `_spec()` 复制,`tools=[BuiltinToolSpec(name="manage_task")]`(`BuiltinToolSpec` 从 agent_spec/protocol import,照文件现有 import)。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && DOCKER_HOST= uv run --project services/orchestrator pytest services/orchestrator/tests/test_agent_factory.py -q -k manage_task`
Expected: FAIL(未知 builtin / `trigger_store` 参不存在)。

- [ ] **Step 3: `KNOWN_BUILTINS` + `_register_builtin`**

`assembly.py` 的 `KNOWN_BUILTINS`(:73-98)加一项(注释说明它在 `build_agent` 注册):

```python
        # Spec 1 PR2 — conversational scheduled tasks. Registered in
        # ``agent_factory.build_agent`` (it has agent_name/version + TriggerStore);
        # ``_register_builtin`` treats it as a no-op.
        "manage_task",
```

`_register_builtin`(:448-479)末尾加分支(在 `SKILL_AUTHORING_BUILTINS` 分支旁):

```python
    elif entry.name == "manage_task":
        # Spec 1 PR2 — registered in build_agent (store + agent_name/version there).
        pass
```

- [ ] **Step 4: `build_agent` 加 kwarg + gated 注册**

`agent_factory.py`:import `ManageTaskTool`(顶部,照 `from orchestrator.tools.update_plan import UpdatePlanTool`):

```python
from orchestrator.tools.manage_task import ManageTaskTool
```

`build_agent` 签名(:489-545)加 kwarg(放在 `skill_store` 旁):

```python
    #: Spec 1 PR2 — the TriggerStore backing the manage_task builtin. ``None`` →
    #: a manifest that declares manage_task raises AgentFactoryError.
    trigger_store: TriggerStore | None = None,
```

(顶部 import `from expert_work.persistence.trigger.base import TriggerStore`,或用与 skill_store 同处的 persistence import 风格。)

在 skill-authoring 注册 block(:880-903)之后,加同构 block:

```python
    # Spec 1 PR2 — conversational scheduled-task management. Opt-in: a manifest
    # declares "manage_task" in tools:. It WRITES, so it needs the TriggerStore +
    # the owning agent's name/version (baked here — ToolContext carries neither).
    declared_manage_task = any(
        isinstance(e, BuiltinToolSpec) and e.name == "manage_task" for e in spec.spec.tools
    )
    if declared_manage_task:
        if trigger_store is None:
            raise AgentFactoryError(
                "manifest declares the manage_task builtin but no TriggerStore is "
                "configured (build_agent trigger_store)"
            )
        registry.register(
            ManageTaskTool(
                store=trigger_store,
                agent_name=spec.metadata.name,
                agent_version=spec.metadata.version,
            )
        )
```

- [ ] **Step 5: control-plane 接线**

`runtime.py`:`make_agent_builder`(:593 附近,`skill_store` 参处)加:

```python
    trigger_store: TriggerStore | None = None,
```

(import `from expert_work.persistence.trigger.base import TriggerStore` 若未 import。)

其内部 `build_agent(...)` 调用(:755-769,`skill_store=skill_store` 处)加:

```python
            trigger_store=trigger_store,
```

`app.py`:主 `make_agent_builder(...)` 调用(:1450,`skill_store=resolved_skill_store` 在 :1493)并排加:

```python
                    trigger_store=resolved_trigger_store,
```

> **实现者注**:`resolved_trigger_store` 已在 app.py:637 定义、已在 :929 与 :1931 消费,直接引用。**只改主 `make_agent_builder`(:1450)**;child(`make_child_agent_builder` :1391)与 worker 构建器不接线(见 Global Constraints)。

- [ ] **Step 6: 跑测试确认通过**

Run: `cd /Users/mac/src/github/jone_qian/expert-work && DOCKER_HOST= uv run --project services/orchestrator pytest services/orchestrator/tests/test_agent_factory.py -q -k manage_task`
Expected: PASS。

- [ ] **Step 7: 回归 + lint + type**

Run:
```bash
cd /Users/mac/src/github/jone_qian/expert-work
DOCKER_HOST= uv run --project services/orchestrator pytest services/orchestrator/tests/test_agent_factory.py services/orchestrator/tests/tools/ -q
uv run ruff check services/orchestrator/src/orchestrator/tools/assembly.py services/orchestrator/src/orchestrator/agent_factory.py services/control-plane/src/control_plane/runtime.py services/control-plane/src/control_plane/app.py
uv run ruff format --check services/orchestrator/src/orchestrator/tools/assembly.py services/orchestrator/src/orchestrator/agent_factory.py services/control-plane/src/control_plane/runtime.py services/control-plane/src/control_plane/app.py
uv run --project services/orchestrator mypy services/orchestrator/src/orchestrator/agent_factory.py services/orchestrator/src/orchestrator/tools/assembly.py
```
Expected: 全绿 / clean。

- [ ] **Step 8: 提交**

```bash
cd /Users/mac/src/github/jone_qian/expert-work
git add services/orchestrator/src/orchestrator/tools/assembly.py services/orchestrator/src/orchestrator/agent_factory.py services/control-plane/src/control_plane/runtime.py services/control-plane/src/control_plane/app.py services/orchestrator/tests/test_agent_factory.py
git commit -m "feat(tools): manage_task opt-in 挂载(KNOWN_BUILTINS + build_agent gated 注册 + control-plane 接线 TriggerStore)"
```

---

## Task 5: 自排护栏接线(`trigger_origin` 旗标 + 绑定期过滤)

**Files:**
- Modify: `services/control-plane/src/control_plane/trigger_firing.py:262-271`(`configurable` 加旗标)
- Modify: `services/orchestrator/src/orchestrator/graph_builder/builder.py:597`(agent_node 绑定期过滤)
- Test: `services/control-plane/tests/test_trigger_firing.py`(扩,断 configurable 带旗标)+ `services/orchestrator/tests/graph_builder/`(扩,断绑定列表剔除)

**Interfaces:**
- Consumes: `ToolContext.trigger_origin`(T2);`ManageTaskTool` 调用期 block(T3)。
- Produces: 定时触发的 run 里 —— ① LLM 工具清单不含 `manage_task`(绑定期过滤)② 纵调 `manage_task` 亦被 `ToolBlockedError` 挡(T3 已有)。

- [ ] **Step 1: 写失败测试**

control-plane —— `test_trigger_firing.py` 扩(断 `fire_trigger` 构造的 configurable 带 `trigger_origin=True`)。实现者定位现有断言 `graph_input`/`configurable` 的测试(fire_trigger 用 fake runtime 捕获传给 `run_agent` 的 config),加断言:

```python
    # 在现有捕获 run_agent 调用 config 的测试里追加:
    assert captured_config["configurable"]["trigger_origin"] is True
```

orchestrator —— 绑定期过滤测试。加到 `services/orchestrator/tests/graph_builder/test_tool_bind_filter.py`(新):

```python
from __future__ import annotations

from orchestrator.graph_builder.builder import _filter_scheduling_tools
from orchestrator.tools.registry import ToolSpec


def _specs() -> list[ToolSpec]:
    return [
        ToolSpec(name="web_search", description="", parameters={}),
        ToolSpec(name="manage_task", description="", parameters={}),
    ]


def test_normal_run_keeps_manage_task() -> None:
    names = [s.name for s in _filter_scheduling_tools(_specs(), trigger_origin=False)]
    assert "manage_task" in names and "web_search" in names


def test_trigger_run_drops_manage_task() -> None:
    names = [s.name for s in _filter_scheduling_tools(_specs(), trigger_origin=True)]
    assert "manage_task" not in names
    assert "web_search" in names
```

> **实现者注**:bind 列表在 builder.py 由若干分支(native_search / allowed_tools / promoted 重排 / 默认)各自拼出 `tools`(读 :560-600 上下文,结构比单行 `[*specs(), *deferred_specs(promoted)]` 复杂,有 `active`/`still_deferred`/`promoted_set`)。**别去抽装配**——加一个分支无关的纯过滤函数,在 if/elif/else 拼完 `tools`、递给 LLM 绑定**之前**统一过一道。核心不变式:`trigger_origin` 为真时 `manage_task` 不进 LLM 绑定列表。

- [ ] **Step 2: 跑测试确认失败**

Run:
```bash
cd /Users/mac/src/github/jone_qian/expert-work
DOCKER_HOST= uv run --project services/orchestrator pytest services/orchestrator/tests/graph_builder/test_tool_bind_filter.py -q
DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock uv run --project services/control-plane pytest services/control-plane/tests/test_trigger_firing.py -q -k origin
```
Expected: FAIL(`_bind_tools_for_run` 不存在 / configurable 无 `trigger_origin`)。

- [ ] **Step 3: `fire_trigger` 打旗标**

`trigger_firing.py` 的 `configurable`(:262-271),在组装后加:

```python
    configurable["trigger_origin"] = True
```

(放在 `configurable: dict[str, Any] = {...}` 之后、`config: RunnableConfig = {"configurable": configurable}` 之前。)

- [ ] **Step 4: 绑定期过滤**

`builder.py`。加分支无关的纯过滤函数(放在 `_build_tool_context` 附近):

```python
def _filter_scheduling_tools(
    tools: list[ToolSpec], *, trigger_origin: bool
) -> list[ToolSpec]:
    """Withhold manage_task from the LLM under a scheduler-triggered run so the
    model can't self-schedule (Spec 1 D-13). Applied to the final bind list,
    independent of which branch built it."""
    if not trigger_origin:
        return tools
    return [s for s in tools if s.name != "manage_task"]
```

在 agent_node 里 if/elif/else 拼完 `tools`(:560-600 区间,各分支都赋 `tools`)、`tools` 递给 LLM 绑定**之前**,统一过一道:

```python
        tools = _filter_scheduling_tools(
            tools,
            trigger_origin=bool((config.get("configurable") or {}).get("trigger_origin", False)),
        )
```

> **实现者注**:定位 if/elif/else 之后、`tools` 首次被消费(传给 caller.bind / astream)之前的汇合点插入这一句。`ToolSpec` 已在 builder.py import(`_filter_scheduling_tools` 签名用它)。不动各分支内部装配逻辑。

- [ ] **Step 5: 跑测试确认通过**

Run:
```bash
cd /Users/mac/src/github/jone_qian/expert-work
DOCKER_HOST= uv run --project services/orchestrator pytest services/orchestrator/tests/graph_builder/test_tool_bind_filter.py -q
DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock uv run --project services/control-plane pytest services/control-plane/tests/test_trigger_firing.py -q -k origin
```
Expected: PASS。

- [ ] **Step 6: lint + type + 回归**

Run:
```bash
cd /Users/mac/src/github/jone_qian/expert-work
uv run ruff check services/control-plane/src/control_plane/trigger_firing.py services/orchestrator/src/orchestrator/graph_builder/builder.py
uv run ruff format --check services/control-plane/src/control_plane/trigger_firing.py services/orchestrator/src/orchestrator/graph_builder/builder.py
uv run --project services/orchestrator mypy services/orchestrator/src/orchestrator/graph_builder/builder.py
DOCKER_HOST= uv run --project services/orchestrator pytest services/orchestrator/tests/graph_builder/ -q
```
Expected: clean / 全绿。

- [ ] **Step 7: 提交**

```bash
cd /Users/mac/src/github/jone_qian/expert-work
git add services/control-plane/src/control_plane/trigger_firing.py services/orchestrator/src/orchestrator/graph_builder/builder.py services/orchestrator/tests/graph_builder/test_tool_bind_filter.py services/control-plane/tests/test_trigger_firing.py
git commit -m "feat(tools): 自排护栏接线(fire_trigger 打 trigger_origin + 绑定期过滤 manage_task)"
```

---

## 收尾(全 5 task 后)

- [ ] 全 orchestrator 测:`cd /Users/mac/src/github/jone_qian/expert-work && DOCKER_HOST= uv run --project services/orchestrator pytest services/orchestrator/tests/ -q`
- [ ] control-plane 受影响测(经真 run 的接线):`DOCKER_HOST=unix:///Users/mac/.docker/run/docker.sock uv run --project services/control-plane pytest services/control-plane/tests/test_trigger_firing.py services/control-plane/tests/test_runtime.py -q`(若 test_runtime 存在)
- [ ] 全库 lint:`uv run ruff check . && uv run ruff format --check .`
- [ ] 无署名核验:`git log main..HEAD --format='%an %ae%n%b' | grep -iE 'co-authored|claude|🤖'` 应无输出。
- [ ] opus 全分支终审(SDD 终审步)。

---

## Self-Review(写完计划回看 spec §4 对照)

- **spec §4.1 挂载**(内建 Tool + KNOWN_BUILTINS + 注入 TriggerStore + 从 ctx 取 tenant/user/thread + agent name/version)→ T3(工具)+ T4(挂载/注入)+ T2(ctx.thread_id)。✅ 差异:agent name/version 非从 ctx 取(ctx 无),bake 进构造(Explore 实锤),已在 T4 体现。
- **spec §4.2 actions**(create/list/update/cancel + 所有权校验)→ T3 全覆盖(update/cancel 走 `_get_owned` 所有权闸)。✅
- **spec §4.3 结构化入参**(frequency/time/by_day/by_month_day/start/end/count/timezone)→ T1 `build_rrule` 参 + T3 schema。✅
- **spec §4.4 指引 in schema**(D-7)→ T3 `_DESCRIPTION` + 字段 `description`;不碰 system prompt。✅ 测 `test_spec_has_field_descriptions` 守。
- **spec §4.5 自排护栏**(D-13)→ 方案 A:T5 绑定期过滤 + T3 调用期 block(缓存不动,已与用户确认)。✅ 偏离 spec 字面「构建期不进 registry」已记录理由(build 缓存冲突)。
- **spec §8 错误处理**(RRULE/tz 非法→拒、缺时间→反问、越权→拒、有界耗尽)→ T1 `ScheduleError` + T3 `_time_fields` 反问 + `_get_owned` 越权 + T1 `next_fire` 耗尽。✅
- **D-8 名+schema 冻结**、**wire-safe 名** → `manage_task` 过 `^[a-zA-Z][a-zA-Z0-9_-]{,63}$`;测 `test_spec_name_frozen`。✅
- **不做**(投递 D1 = PR3;调试台 = PR4;共享创建路径/端点 rrule = Spec 3)→ 均未纳入本计划,Global Constraints 明列。✅

> **明确不在 PR2**:结果回原对话投递(reconcile pass `aupdate_state` + `TRIGGER_COMPLETED/FAILED` audit)属 PR3;工具建的任务此刻会落库、会被 scheduler 触发跑,但成功结果**尚不会**追加回原对话(originating_thread_id/context_mode 字段已写好等 PR3 消费)。playground 端到端演练属 PR4。
