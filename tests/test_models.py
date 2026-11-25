import unittest
from datetime import datetime, time, timedelta

from alarmclock.models import (
    Alarm,
    ParseError,
    format_days,
    humanize,
    next_date_for,
    parse_days,
    parse_duration,
    parse_time,
)

# 2026-11-25 is a Wednesday.
WED_NOON = datetime(2026, 11, 25, 12, 0)


class ParseTimeTests(unittest.TestCase):
    def test_valid_forms(self):
        cases = {
            "07:30": time(7, 30),
            "7:30": time(7, 30),
            "730": time(7, 30),
            "1930": time(19, 30),
            "19:30": time(19, 30),
            "0:00": time(0, 0),
            "7": time(7, 0),
            "7pm": time(19, 0),
            "7:15 PM": time(19, 15),
            "12am": time(0, 0),
            "12:05am": time(0, 5),
            "12pm": time(12, 0),
            "11:59p": time(23, 59),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_time(text), expected)

    def test_invalid_forms(self):
        for text in ["", "24:00", "7:60", "13pm", "0am", "abc", "7:3", "07:30:00"]:
            with self.subTest(text=text):
                with self.assertRaises(ParseError):
                    parse_time(text)


class ParseDurationTests(unittest.TestCase):
    def test_valid(self):
        cases = {
            "10m": 600,
            "10": 600,
            "90s": 90,
            "1h": 3600,
            "1h30m": 5400,
            "1h 5m 10s": 3910,
            "2M": 120,
        }
        for text, secs in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_duration(text), timedelta(seconds=secs))

    def test_invalid(self):
        for text in ["", "0", "0m", "ten", "5x", "m5", "-5m"]:
            with self.subTest(text=text):
                with self.assertRaises(ParseError):
                    parse_duration(text)


class ParseDaysTests(unittest.TestCase):
    def test_aliases(self):
        self.assertEqual(parse_days("daily"), list(range(7)))
        self.assertEqual(parse_days("Weekdays"), [0, 1, 2, 3, 4])
        self.assertEqual(parse_days("weekends"), [5, 6])
        self.assertEqual(parse_days("once"), [])

    def test_lists(self):
        self.assertEqual(parse_days("mon,wed,fri"), [0, 2, 4])
        self.assertEqual(parse_days("friday monday"), [0, 4])
        self.assertEqual(parse_days("tu,th,sa,su"), [1, 3, 5, 6])
        self.assertEqual(parse_days("mon,mon"), [0])

    def test_invalid(self):
        for text in ["", "t", "s", "funday", ","]:
            with self.subTest(text=text):
                with self.assertRaises(ParseError):
                    parse_days(text)

    def test_format_round_trip(self):
        self.assertEqual(format_days([0, 1, 2, 3, 4]), "weekdays")
        self.assertEqual(format_days(list(range(7))), "daily")
        self.assertEqual(format_days([5, 6]), "weekends")
        self.assertEqual(format_days([0, 2]), "Mon,Wed")
        self.assertEqual(format_days([]), "once")


class HumanizeTests(unittest.TestCase):
    def test_humanize(self):
        self.assertEqual(humanize(timedelta(seconds=45)), "45s")
        self.assertEqual(humanize(timedelta(minutes=5, seconds=3)), "5m 03s")
        self.assertEqual(humanize(timedelta(minutes=25)), "25m")
        self.assertEqual(humanize(timedelta(hours=2, minutes=5)), "2h 05m")
        self.assertEqual(humanize(timedelta(days=3, hours=4)), "3d 4h")
        self.assertEqual(humanize(timedelta(seconds=-5)), "0s")


class NextTriggerTests(unittest.TestCase):
    def test_next_date_for(self):
        self.assertEqual(next_date_for(time(13, 0), WED_NOON), WED_NOON.date())
        self.assertEqual(next_date_for(time(12, 0), WED_NOON), WED_NOON.date() + timedelta(days=1))
        self.assertEqual(next_date_for(time(7, 0), WED_NOON), WED_NOON.date() + timedelta(days=1))

    def test_one_shot(self):
        alarm = Alarm(id=1, hour=13, minute=0, date="2026-11-25")
        self.assertEqual(alarm.next_trigger(WED_NOON), datetime(2026, 11, 25, 13, 0))
        self.assertIsNone(alarm.next_trigger(datetime(2026, 11, 25, 13, 0)))
        self.assertTrue(alarm.is_expired(datetime(2026, 11, 25, 13, 0)))
        self.assertFalse(alarm.is_expired(WED_NOON))

    def test_disabled_never_triggers(self):
        alarm = Alarm(id=1, hour=13, minute=0, date="2026-11-25", enabled=False)
        self.assertIsNone(alarm.next_trigger(WED_NOON))

    def test_daily(self):
        alarm = Alarm(id=1, hour=7, minute=0, days=list(range(7)))
        self.assertEqual(alarm.next_trigger(WED_NOON), datetime(2026, 11, 26, 7, 0))
        alarm.hour = 18
        self.assertEqual(alarm.next_trigger(WED_NOON), datetime(2026, 11, 25, 18, 0))

    def test_weekdays_skip_weekend(self):
        alarm = Alarm(id=1, hour=7, minute=0, days=[0, 1, 2, 3, 4])
        fri_evening = datetime(2026, 11, 27, 20, 0)
        self.assertEqual(alarm.next_trigger(fri_evening), datetime(2026, 11, 30, 7, 0))  # Monday

    def test_same_weekday_next_week(self):
        alarm = Alarm(id=1, hour=7, minute=0, days=[2])  # Wednesdays
        self.assertEqual(alarm.next_trigger(WED_NOON), datetime(2026, 12, 2, 7, 0))

    def test_round_trip_dict(self):
        alarm = Alarm(id=3, hour=6, minute=45, label="Gym", days=[0, 2], snooze_minutes=9)
        data = alarm.to_dict()
        data["unknown_future_field"] = 1
        self.assertEqual(Alarm.from_dict(data), alarm)


if __name__ == "__main__":
    unittest.main()
