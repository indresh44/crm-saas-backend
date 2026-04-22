"""
Centralized timezone helpers.

The app's source of truth for "today" and display-formatting. Every call site
that needs to know the user's local day/time goes through this module —
nothing else should call `date.today()` or `datetime.now(timezone.utc).date()`
for user-facing semantics.

All DB columns remain UTC (TIMESTAMPTZ). These helpers convert between UTC
and a business's local zone at the edges.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Union
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import settings

TzArg = Union[ZoneInfo, str, None]

# Fallback used only when the caller has no business context (rare — chat
# bootstrap, health checks, cron-like tasks). Stays lenient so a bad IANA
# string in one business row doesn't crash the service — it degrades to
# the default and logs upstream.
DEFAULT_TIMEZONE: ZoneInfo = ZoneInfo(
    getattr(settings, "DEFAULT_APP_TIMEZONE", None) or "Asia/Kolkata"
)


def _as_zone(tz: TzArg) -> ZoneInfo:
    """Coerce a str / ZoneInfo / None into a ZoneInfo. None uses the default."""
    if tz is None:
        return DEFAULT_TIMEZONE
    if isinstance(tz, ZoneInfo):
        return tz
    value = tz.strip()
    if not value:
        return DEFAULT_TIMEZONE
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError:
        return DEFAULT_TIMEZONE


def today_in(tz: TzArg = None) -> date:
    """Return the current local date in the given zone."""
    return datetime.now(_as_zone(tz)).date()


def now_in(tz: TzArg = None) -> datetime:
    """Return an aware datetime for 'now' in the given zone."""
    return datetime.now(_as_zone(tz))


def day_bounds_utc(day: date, tz: TzArg = None) -> tuple[datetime, datetime]:
    """
    Return [start_utc, end_utc) bounding the full local day `day` in zone `tz`,
    converted to UTC for querying TIMESTAMPTZ columns.

    Example: day_bounds_utc(date(2026, 4, 22), "Asia/Kolkata")
    -> (2026-04-21 18:30:00+00:00, 2026-04-22 18:30:00+00:00)
    """
    zone = _as_zone(tz)
    start_local = datetime.combine(day, time.min, tzinfo=zone)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def combine_local(day: date, at_time: time, tz: TzArg = None) -> datetime:
    """
    Combine `day` + `at_time` into an aware datetime in the given zone.

    Use this when the user has entered a wall-clock date+time and we need
    to store it as UTC in the DB. Example:
        combine_local(date(2026, 4, 22), time(9, 0), "Asia/Kolkata")
        -> 2026-04-22 09:00:00+05:30

    Handles DST automatically via zoneinfo.
    """
    return datetime.combine(day, at_time, tzinfo=_as_zone(tz))


def format_local(dt: datetime, tz: TzArg = None, fmt: str = "%d %b %Y") -> str:
    """
    Format a (typically UTC) aware datetime for display in the given zone.
    Use this anywhere you're rendering to the user — activity logs, emails,
    PDFs, chat context.

    Defensively handles naive datetimes by assuming UTC, since that matches
    our storage convention.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_as_zone(tz)).strftime(fmt)
