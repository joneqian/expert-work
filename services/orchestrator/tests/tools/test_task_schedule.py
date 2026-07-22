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


def _scheduler_next(
    rrule: str, *, tz: str, created_at: datetime, after: datetime
) -> datetime | None:
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
        build_rrule(frequency="daily", hour=3, minute=0, count=3, end_date=date(2026, 6, 1))


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
