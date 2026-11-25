"""The scheduling loop: decides which alarms are due and handles snoozes.

The scheduler is stateless with respect to "next trigger" times: on every tick
it asks each alarm for its first trigger strictly after the *previous* tick. If
that moment is <= now, the alarm is due. This means an alarm can never fire
twice for the same moment, edits to the alarm file take effect immediately,
and nothing is missed between ticks even if a tick is slow.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional

from .models import Alarm
from .store import AlarmStore

DISMISS = "dismiss"
SNOOZE = "snooze"
TIMEOUT = "timeout"
MISSED = "missed"

RingFn = Callable[[Alarm, datetime], str]


@dataclass
class Snooze:
    alarm: Alarm
    at: datetime


@dataclass
class Due:
    alarm: Alarm
    when: datetime
    snoozed: bool = False


class Scheduler:
    def __init__(
        self,
        store: AlarmStore,
        ring: RingFn,
        clock: Callable[[], datetime] = datetime.now,
        missed_after: timedelta = timedelta(hours=1),
        on_missed: Optional[Callable[[Alarm, datetime], None]] = None,
    ):
        self.store = store
        self.ring = ring
        self.clock = clock
        self.missed_after = missed_after
        self.on_missed = on_missed
        self.last_check = clock()
        self.snoozes: list[Snooze] = []
        self._alarms: list[Alarm] = []
        self._mtime: Optional[float] = None

    # --- state ----------------------------------------------------------------

    def alarms(self) -> list[Alarm]:
        """Current alarms, re-read from disk whenever the file changes."""
        mtime = self.store.mtime()
        if mtime != self._mtime:
            self._alarms = self.store.load()
            self._mtime = mtime
        return self._alarms

    def due(self, now: datetime) -> list[Due]:
        if now <= self.last_check:  # clock moved backwards or no time passed
            return []
        found: list[Due] = []
        for alarm in self.alarms():
            when = alarm.next_trigger(self.last_check)
            if when is not None and when <= now:
                found.append(Due(alarm, when))
        live_ids = {a.id for a in self._alarms}
        for snooze in list(self.snoozes):
            if snooze.at <= now:
                self.snoozes.remove(snooze)
                if snooze.alarm.id in live_ids:  # deleted alarms don't come back
                    found.append(Due(snooze.alarm, snooze.at, snoozed=True))
        return sorted(found, key=lambda d: d.when)

    def upcoming(self, now: datetime) -> Optional[tuple[datetime, Alarm, bool]]:
        """(when, alarm, is_snooze) of the next thing that will ring, if any."""
        candidates: list[tuple[datetime, Alarm, bool]] = []
        for alarm in self.alarms():
            when = alarm.next_trigger(now)
            if when is not None:
                candidates.append((when, alarm, False))
        candidates.extend((s.at, s.alarm, True) for s in self.snoozes)
        return min(candidates, key=lambda c: c[0]) if candidates else None

    # --- loop -----------------------------------------------------------------

    def tick(self, now: Optional[datetime] = None) -> list[tuple[Alarm, str]]:
        """Ring everything due since the last tick. Returns (alarm, outcome) pairs."""
        now = now or self.clock()
        due = self.due(now)
        self.last_check = max(self.last_check, now)
        results: list[tuple[Alarm, str]] = []
        for item in due:
            alarm = item.alarm
            if now - item.when > self.missed_after:
                outcome = MISSED
                if self.on_missed:
                    self.on_missed(alarm, item.when)
            else:
                outcome = self.ring(alarm, item.when)
                if outcome == SNOOZE:
                    at = self.clock() + timedelta(minutes=alarm.snooze_minutes)
                    self.snoozes.append(Snooze(alarm, at))
            if not alarm.repeating and not item.snoozed:
                self._disable_one_shot(alarm.id)
            results.append((alarm, outcome))
        return results

    def _disable_one_shot(self, alarm_id: int) -> None:
        alarms = self.store.load()
        changed = False
        for alarm in alarms:
            if alarm.id == alarm_id and alarm.enabled:
                alarm.enabled = False
                changed = True
        if changed:
            self.store.save(alarms)
