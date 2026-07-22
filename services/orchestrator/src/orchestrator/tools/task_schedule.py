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
        raise ScheduleError(f"frequency must be one of {sorted(_FREQUENCIES)}, got {frequency!r}")
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


def next_fire(rrule: str, *, timezone: str, dtstart: datetime, after: datetime) -> datetime | None:
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
