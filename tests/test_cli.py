import contextlib
import io
import tempfile
import unittest
from unittest import mock
from datetime import datetime
from pathlib import Path

from alarmclock.cli import main
from alarmclock.models import Alarm
from alarmclock.store import AlarmStore

NOW = datetime(2026, 11, 25, 12, 0)  # Wednesday noon


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / "alarms.json"
        self.store = AlarmStore(self.file)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err),                 mock.patch("sys.stdin", io.StringIO()):
            code = main(["--file", str(self.file), *args], clock=lambda: NOW)
        return code, out.getvalue(), err.getvalue()

    def test_add_one_shot_later_today(self):
        code, out, _ = self.run_cli("add", "13:15", "-l", "Lunch")
        self.assertEqual(code, 0)
        alarm = self.store.get(1)
        self.assertEqual((alarm.time_str, alarm.date, alarm.label), ("13:15", "2026-11-25", "Lunch"))
        self.assertIn("in 1h 15m", out)

    def test_add_one_shot_rolls_to_tomorrow(self):
        self.run_cli("add", "7am")
        self.assertEqual(self.store.get(1).date, "2026-11-26")

    def test_add_repeating(self):
        self.run_cli("add", "06:30", "--repeat", "weekdays")
        alarm = self.store.get(1)
        self.assertEqual((alarm.days, alarm.date), ([0, 1, 2, 3, 4], None))

    def test_add_in_duration(self):
        self.run_cli("add", "--in", "90m")
        alarm = self.store.get(1)
        self.assertEqual((alarm.time_str, alarm.date), ("13:30", "2026-11-25"))

    def test_add_in_rounds_up_to_next_minute(self):
        with contextlib.redirect_stdout(io.StringIO()):
            main(["--file", str(self.file), "add", "--in", "1m"], clock=lambda: datetime(2026, 11, 25, 11, 59, 28))
        self.assertEqual(self.store.get(1).time_str, "12:01")

    def test_add_with_date(self):
        self.run_cli("add", "08:00", "--date", "2026-12-31")
        self.assertEqual(self.store.get(1).date, "2026-12-31")

    def test_add_errors(self):
        for args in (
            ["add"],
            ["add", "25:00"],
            ["add", "07:00", "--repeat", "funday"],
            ["add", "07:00", "--date", "2026-11-25"],  # in the past
            ["add", "07:00", "--date", "2026-12-01", "--repeat", "daily"],
            ["add", "07:00", "--in", "5m"],
            ["add", "07:00", "--snooze", "0"],
            ["add", "07:00", "--sound", "does-not-exist.wav"],
        ):
            with self.subTest(args=args):
                code, _, err = self.run_cli(*args)
                self.assertEqual(code, 2)
                self.assertIn("error", err)
        self.assertEqual(self.store.load(), [])

    def test_list_hides_inactive_unless_all(self):
        self.run_cli("add", "13:00", "-l", "visible")
        self.run_cli("add", "14:00", "-l", "hidden")
        self.run_cli("disable", "2")
        _, out, _ = self.run_cli("list")
        self.assertIn("visible", out)
        self.assertNotIn("hidden", out)
        _, out, _ = self.run_cli("list", "--all")
        self.assertIn("hidden", out)

    def test_list_empty(self):
        _, out, _ = self.run_cli("ls")
        self.assertIn("No active alarms", out)

    def test_enable_rearms_expired_one_shot(self):
        self.store.add(Alarm(id=0, hour=9, minute=0, date="2026-11-20", enabled=False))
        self.run_cli("enable", "1")
        alarm = self.store.get(1)
        self.assertTrue(alarm.enabled)
        self.assertEqual(alarm.date, "2026-11-26")

    def test_remove_and_missing_id(self):
        self.run_cli("add", "13:00")
        code, _, err = self.run_cli("rm", "5")
        self.assertEqual(code, 2)
        self.assertIn("no alarm with id 5", err)
        code, _, _ = self.run_cli("remove", "1")
        self.assertEqual((code, self.store.load()), (0, []))

    def test_clear_requires_yes_when_not_interactive(self):
        self.run_cli("add", "13:00")
        code, _, _ = self.run_cli("clear")
        self.assertEqual(code, 2)
        self.assertEqual(len(self.store.load()), 1)
        code, _, _ = self.run_cli("clear", "--yes")
        self.assertEqual((code, self.store.load()), (0, []))

    def test_edit(self):
        self.run_cli("add", "13:00", "-r", "mon,fri")
        self.run_cli("edit", "1", "--time", "6:45", "--label", "Gym", "--snooze", "10")
        alarm = self.store.get(1)
        self.assertEqual((alarm.time_str, alarm.days, alarm.label, alarm.snooze_minutes), ("06:45", [0, 4], "Gym", 10))
        self.run_cli("edit", "1", "--repeat", "once")
        alarm = self.store.get(1)
        self.assertEqual((alarm.days, alarm.date), ([], "2026-11-26"))

    def test_next(self):
        self.run_cli("add", "18:00", "-l", "later")
        self.run_cli("add", "12:30", "-l", "sooner")
        _, out, _ = self.run_cli("next")
        self.assertIn("sooner", out)
        self.assertIn("in 30m", out)

    def test_no_command_prints_help(self):
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("usage", out)


if __name__ == "__main__":
    unittest.main()
