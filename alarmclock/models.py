"""Alarm model and parsing helpers for times, durations and repeat days."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Optional

WEEKDAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_FULL_DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

DAY_ALIASES = {
    "daily": list(range(7)),
    "everyday": list(range(7)),
    "weekdays": [0, 1, 2, 3, 4],
    "weekends": [5, 6],
    "once": [],
}


class ParseError(ValueError):
    """Raised when user input (time, duration, days) cannot be understood."""


_TIME_RE = re.compile(r"^(\d{1,2})(?::?(\d{2}))?\s*(am|pm|a|p)?$", re.IGNORECASE)
_DURATION_RE = re.compile(r"(\d+)\s*([hms]?)", re.IGNORECASE)


def parse_time(text: str) -> time:
    """Parse '7:30', '07:30', '730', '7pm', '7:30 am', '19:30' into a time."""
    match = _TIME_RE.match(text.strip())
    if not match:
        raise ParseError(f"invalid time {text!r} (try 07:30, 7:30pm or 19:30)")
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    suffix = (match.group(3) or "").lower()

    if minute > 59:
        raise ParseError(f"invalid minute in {text!r}")
    if suffix:
        if not 1 <= hour <= 12:
            raise ParseError(f"hour must be 1-12 with am/pm in {text!r}")
        if suffix.startswith("p") and hour != 12:
            hour += 12
        elif suffix.startswith("a") and hour == 12:
            hour = 0
    elif hour > 23:
        raise ParseError(f"invalid hour in {text!r}")
    return time(hour, minute)


def parse_duration(text: str) -> timedelta:
    """Parse '10m', '1h30m', '90s', '1h 5m 10s' or a bare number of minutes."""
    cleaned = text.strip().lower().replace(" ", "")
    if not cleaned:
        raise ParseError("empty duration")
    pos = 0
    total = 0
    for match in _DURATION_RE.finditer(cleaned):
        if match.start() != pos:
            raise ParseError(f"invalid duration {text!r} (try 10m, 1h30m or 90s)")
        value, unit = int(match.group(1)), match.group(2) or "m"
        total += value * {"h": 3600, "m": 60, "s": 1}[unit]
        pos = match.end()
    if pos != len(cleaned):
        raise ParseError(f"invalid duration {text!r} (try 10m, 1h30m or 90s)")
    if total <= 0:
        raise ParseError("duration must be greater than zero")
    return timedelta(seconds=total)


def parse_days(text: str) -> list[int]:
    """Parse 'daily', 'weekdays', 'weekends', 'once' or 'mon,wed,fri' into weekday ints."""
    cleaned = text.strip().lower()
    if cleaned in DAY_ALIASES:
        return list(DAY_ALIASES[cleaned])
    days: set[int] = set()
    for part in re.split(r"[,\s]+", cleaned):
        if not part:
            continue
        matches = [i for i, full in enumerate(_FULL_DAY_NAMES) if len(part) >= 2 and full.startswith(part)]
        if len(matches) != 1:
            raise ParseError(
                f"unknown day {part!r} (use mon..sun, daily, weekdays, weekends or once)"
            )
        days.add(matches[0])
    if not days:
        raise ParseError("no days given")
    return sorted(days)


def format_days(days: list[int]) -> str:
    if not days:
        return "once"
    for name in ("daily", "weekdays", "weekends"):
        if sorted(days) == DAY_ALIASES[name]:
            return name
    return ",".join(WEEKDAY_NAMES[d].capitalize() for d in sorted(days))


def humanize(delta: timedelta) -> str:
    """Short human form of a positive timedelta: '45s', '5m', '2h 05m', '3d 4h'."""
    seconds = max(0, math.ceil(delta.total_seconds()))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s" if minutes < 10 else f"{minutes}m"
    return f"{secs}s"


def next_date_for(t: time, now: datetime) -> date:
    """The date on which time-of-day `t` next occurs strictly after `now`."""
    today = now.date()
    return today if datetime.combine(today, t) > now else today + timedelta(days=1)


@dataclass
class Alarm:
    id: int
    hour: int
    minute: int
    label: str = ""
    days: list[int] = field(default_factory=list)  # empty list = one-shot alarm
    date: Optional[str] = None  # ISO date, only used by one-shot alarms
    enabled: bool = True
    snooze_minutes: int = 5
    sound: Optional[str] = None

    @property
    def time(self) -> time:
        return time(self.hour, self.minute)

    @property
    def time_str(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"

    @property
    def repeating(self) -> bool:
        return bool(self.days)

    @property
    def repeat_str(self) -> str:
        if self.repeating:
            return format_days(self.days)
        return f"once ({self.date})" if self.date else "once"

    @property
    def display_name(self) -> str:
        return self.label or "Alarm"

    def next_trigger(self, after: datetime) -> Optional[datetime]:
        """First moment strictly after `after` at which this alarm should ring."""
        if not self.enabled:
            return None
        if not self.repeating:
            if not self.date:
                return None
            when = datetime.combine(date.fromisoformat(self.date), self.time)
            return when if when > after else None
        for offset in range(8):
            day = after.date() + timedelta(days=offset)
            if day.weekday() in self.days:
                when = datetime.combine(day, self.time)
                if when > after:
                    return when
        return None  # unreachable when days is non-empty

    def is_expired(self, now: datetime) -> bool:
        """A one-shot alarm whose moment has already passed."""
        if self.repeating or not self.date:
            return False
        return datetime.combine(date.fromisoformat(self.date), self.time) <= now

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Alarm":
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)
