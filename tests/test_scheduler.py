import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from alarmclock.models import Alarm
from alarmclock.scheduler import DISMISS, MISSED, SNOOZE, Scheduler
from alarmclock.store import AlarmStore, StoreError

START = datetime(2026, 11, 25, 6, 58)  # Wednesday


class FakeClock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)
        return self.now


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = AlarmStore(Path(self.tmp.name) / "alarms.json")
        self.clock = FakeClock(START)
        self.rang = []
        self.answer = DISMISS
        self.missed = []
        self.sched = Scheduler(
            self.store,
            self._ring,
            clock=self.clock,
            on_missed=lambda a, w: self.missed.append((a.id, w)),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _ring(self, alarm, when):
        self.rang.append((alarm.id, when))
        return self.answer

    def run_until(self, minutes, step_seconds=1):
        end = self.clock.now + timedelta(minutes=minutes)
        while self.clock.now < end:
            self.clock.advance(seconds=step_seconds)
            self.sched.tick()


class StoreTests(Harness):
    def test_add_assigns_incrementing_ids_and_persists(self):
        a = self.store.add(Alarm(id=0, hour=7, minute=0))
        b = self.store.add(Alarm(id=0, hour=8, minute=0, label="b"))
        self.assertEqual((a.id, b.id), (1, 2))
        self.assertEqual([x.label for x in AlarmStore(self.store.path).load()], ["", "b"])

    def test_ids_not_reused_after_removing_middle(self):
        for h in (7, 8, 9):
            self.store.add(Alarm(id=0, hour=h, minute=0))
        self.store.remove([2])
        self.assertEqual(self.store.add(Alarm(id=0, hour=10, minute=0)).id, 4)

    def test_remove_missing_raises_and_changes_nothing(self):
        self.store.add(Alarm(id=0, hour=7, minute=0))
        with self.assertRaises(StoreError):
            self.store.remove([1, 99])
        self.assertEqual(len(self.store.load()), 1)

    def test_corrupt_file(self):
        self.store.path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(StoreError):
            self.store.load()

    def test_missing_file_is_empty(self):
        self.assertEqual(self.store.load(), [])


class SchedulerTests(Harness):
    def test_one_shot_rings_once_then_disables(self):
        self.store.add(Alarm(id=0, hour=7, minute=0, date="2026-11-25"))
        self.run_until(minutes=5)
        self.assertEqual(self.rang, [(1, datetime(2026, 11, 25, 7, 0))])
        self.assertFalse(self.store.get(1).enabled)

    def test_daily_rings_each_day(self):
        self.store.add(Alarm(id=0, hour=7, minute=0, days=list(range(7))))
        self.run_until(minutes=3 * 24 * 60, step_seconds=60)
        self.assertEqual([w.day for _, w in self.rang], [25, 26, 27])
        self.assertTrue(self.store.get(1).enabled)

    def test_large_tick_gap_still_rings(self):
        self.store.add(Alarm(id=0, hour=7, minute=0, date="2026-11-25"))
        self.clock.advance(minutes=10)
        self.sched.tick()
        self.assertEqual(len(self.rang), 1)

    def test_very_late_alarm_is_reported_missed(self):
        self.store.add(Alarm(id=0, hour=7, minute=0, date="2026-11-25"))
        self.clock.advance(hours=3)  # e.g. laptop was asleep
        results = self.sched.tick()
        self.assertEqual(self.rang, [])
        self.assertEqual([o for _, o in results], [MISSED])
        self.assertEqual(len(self.missed), 1)
        self.assertFalse(self.store.get(1).enabled)

    def test_alarm_added_while_running_is_picked_up(self):
        self.run_until(minutes=1)
        self.store.add(Alarm(id=0, hour=7, minute=0, date="2026-11-25"))
        self.run_until(minutes=2)
        self.assertEqual(len(self.rang), 1)

    def test_alarm_already_past_at_startup_does_not_ring(self):
        self.store.add(Alarm(id=0, hour=6, minute=30, days=list(range(7))))
        self.run_until(minutes=5)
        self.assertEqual(self.rang, [])

    def test_snooze_rerings_after_snooze_minutes(self):
        self.store.add(Alarm(id=0, hour=7, minute=0, date="2026-11-25", snooze_minutes=5))
        self.answer = SNOOZE
        self.run_until(minutes=3)  # rings at 07:00 -> snoozed until 07:05
        self.answer = DISMISS
        self.run_until(minutes=10)
        self.assertEqual(
            [w for _, w in self.rang],
            [datetime(2026, 11, 25, 7, 0), datetime(2026, 11, 25, 7, 5, 0)],
        )
        self.assertEqual(self.sched.snoozes, [])

    def test_snooze_of_deleted_alarm_is_dropped(self):
        self.store.add(Alarm(id=0, hour=7, minute=0, days=list(range(7))))
        self.answer = SNOOZE
        self.run_until(minutes=3)
        self.store.remove([1])
        self.answer = DISMISS
        self.run_until(minutes=10)
        self.assertEqual(len(self.rang), 1)

    def test_disabled_alarm_does_not_ring(self):
        self.store.add(Alarm(id=0, hour=7, minute=0, days=list(range(7)), enabled=False))
        self.run_until(minutes=5)
        self.assertEqual(self.rang, [])

    def test_two_alarms_same_minute_both_ring_in_order(self):
        self.store.add(Alarm(id=0, hour=7, minute=1, date="2026-11-25"))
        self.store.add(Alarm(id=0, hour=7, minute=0, date="2026-11-25"))
        self.clock.advance(minutes=5)
        self.sched.tick()
        self.assertEqual([i for i, _ in self.rang], [2, 1])

    def test_clock_going_backwards_does_not_double_ring(self):
        self.store.add(Alarm(id=0, hour=7, minute=0, days=list(range(7))))
        self.run_until(minutes=3)
        self.clock.advance(minutes=-5)  # e.g. DST or NTP correction
        self.run_until(minutes=6)
        self.assertEqual(len(self.rang), 1)

    def test_upcoming(self):
        self.store.add(Alarm(id=0, hour=9, minute=0, days=[2]))
        self.store.add(Alarm(id=0, hour=7, minute=30, date="2026-11-25"))
        when, alarm, snoozed = self.sched.upcoming(self.clock())
        self.assertEqual((when, alarm.id, snoozed), (datetime(2026, 11, 25, 7, 30), 2, False))


if __name__ == "__main__":
    unittest.main()
