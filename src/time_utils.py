"""Timezone, parsing and event-window helpers.

Everything the bot reasons about is local time in the configured zone
(America/New_York). The cloud runner works in UTC, so all conversion happens
here and nowhere else.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

WEEKDAY_KEYS = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]


def get_tz(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def now_local(tz: ZoneInfo, at: str | None = None) -> datetime:
    """Current local time, or a simulated one via --at for testing."""
    if at:
        parsed = datetime.fromisoformat(at)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tz)
        return parsed.astimezone(tz)
    return datetime.now(tz)


def parse_hhmm(value: str) -> time:
    hh, mm = value.strip().split(":")
    return time(int(hh), int(mm))


def combine(day: date, hhmm: str, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, parse_hhmm(hhmm), tzinfo=tz)


def parse_due(value: str, tz: ZoneInfo) -> datetime:
    """Parse a deadline written as 'YYYY-MM-DD HH:MM'."""
    parsed = datetime.fromisoformat(value.strip())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def weekday_key(value: date | datetime) -> str:
    return WEEKDAY_KEYS[value.weekday()]


def is_due(scheduled: datetime, now: datetime, grace_minutes: int) -> bool:
    """True when `scheduled` has arrived and is not yet stale.

    The grace window is what makes late GitHub Actions runs survivable: a 06:30
    message still goes out if the runner only wakes up at 06:52.
    """
    if now < scheduled:
        return False
    return (now - scheduled) <= timedelta(minutes=grace_minutes)


def in_quiet_hours(now: datetime, start: str | None, end: str | None) -> bool:
    """Quiet window wraps midnight, e.g. 23:30 -> 06:00."""
    if not start or not end:
        return False
    current = now.time()
    start_t, end_t = parse_hhmm(start), parse_hhmm(end)
    if start_t <= end_t:
        return start_t <= current < end_t
    return current >= start_t or current < end_t


def fmt_date(value: datetime | date) -> str:
    """'Monday, September 14' without platform-specific strftime flags."""
    return f"{value.strftime('%A')}, {value.strftime('%B')} {value.day}"


def fmt_time(value: datetime) -> str:
    hour = value.hour % 12 or 12
    suffix = "AM" if value.hour < 12 else "PM"
    return f"{hour}:{value.minute:02d} {suffix}"


def fmt_datetime(value: datetime) -> str:
    return f"{fmt_date(value)} at {fmt_time(value)}"


def describe_delta(due: datetime, now: datetime) -> str:
    """Human phrasing for urgency: 'TODAY', 'tomorrow', 'in 3 days', 'OVERDUE'."""
    days = (due.date() - now.date()).days
    if days < 0:
        overdue = abs(days)
        return "OVERDUE by 1 day" if overdue == 1 else f"OVERDUE by {overdue} days"
    if days == 0:
        return "TODAY"
    if days == 1:
        return "tomorrow"
    return f"in {days} days"
