"""Command-line interface: `alarm <command> ...`."""

from __future__ import annotations

import argparse
import os
import sys
import time as _time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional, Sequence

from . import __version__, ui
from .models import WEEKDAY_NAMES, Alarm, ParseError, humanize, next_date_for, parse_days, parse_duration, parse_time
from .scheduler import Scheduler
from .sound import Ringer
from .store import AlarmStore, StoreError

Clock = Callable[[], datetime]


class CliError(Exception):
    pass


# --- argument parsing ---------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alarm",
        description="A terminal alarm clock. Add alarms, then keep `alarm run` open to ring them.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--file",
        type=Path,
        help="alarm file to use (default: ~/.alarmclock/alarms.json or $ALARMCLOCK_HOME)",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p = sub.add_parser("add", help="add an alarm")
    p.add_argument("time", nargs="?", help="time of day: 07:30, 7:30am, 19:30, 7pm")
    p.add_argument("--in", dest="in_", metavar="DURATION", help="ring after a delay instead, e.g. 20m, 1h30m")
    p.add_argument("-l", "--label", default="", help="text shown when the alarm rings")
    p.add_argument(
        "-r",
        "--repeat",
        default="once",
        help="once (default), daily, weekdays, weekends, or days like mon,wed,fri",
    )
    p.add_argument("--date", help="date for a one-shot alarm, YYYY-MM-DD (default: next occurrence)")
    p.add_argument("--snooze", type=int, default=5, metavar="MIN", help="snooze length in minutes (default 5)")
    p.add_argument("--sound", help="audio file to play instead of beeps (WAV on Windows)")

    p = sub.add_parser("list", aliases=["ls"], help="list alarms")
    p.add_argument("-a", "--all", action="store_true", help="include disabled and expired alarms")

    p = sub.add_parser("remove", aliases=["rm", "delete"], help="delete alarms by id")
    p.add_argument("ids", type=int, nargs="+", metavar="ID")

    p = sub.add_parser("clear", help="delete all alarms")
    p.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")

    for name, help_text in (("enable", "turn alarms on"), ("disable", "turn alarms off")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("ids", type=int, nargs="+", metavar="ID")

    p = sub.add_parser("edit", help="change an existing alarm")
    p.add_argument("id", type=int, metavar="ID")
    p.add_argument("-t", "--time", help="new time of day")
    p.add_argument("-l", "--label", help="new label")
    p.add_argument("-r", "--repeat", help="new repeat rule")
    p.add_argument("--snooze", type=int, metavar="MIN", help="new snooze length")
    p.add_argument("--sound", help="new sound file ('' to go back to beeps)")

    sub.add_parser("next", help="show when the next alarm rings")

    p = sub.add_parser("run", help="run the alarm clock in the foreground (rings alarms)")
    p.add_argument("--quiet", action="store_true", help="show alarms without sound")
    p.add_argument(
        "--ring-timeout",
        type=float,
        default=300,
        metavar="SEC",
        help="stop ringing after this many seconds if nobody responds (default 300)",
    )

    p = sub.add_parser("timer", help="countdown timer that rings when done")
    p.add_argument("duration", help="e.g. 90s, 10m, 1h30m")
    p.add_argument("-l", "--label", default="Timer")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--sound")
    p.add_argument("--ring-timeout", type=float, default=120, metavar="SEC")

    p = sub.add_parser("test-sound", help="play the alarm sound for a few seconds")
    p.add_argument("--sound")
    p.add_argument("--seconds", type=float, default=3)

    return parser


# --- helpers ------------------------------------------------------------------------------


def print_table(alarms: Sequence[Alarm], now: datetime) -> None:
    rows = [("ID", "TIME", "REPEAT", "NEXT RING", "STATUS", "LABEL")]
    for a in alarms:
        nxt = a.next_trigger(now)
        if nxt:
            next_str = f"{nxt:%a %d %b %H:%M} (in {humanize(nxt - now)})"
        else:
            next_str = "-"
        if a.enabled and a.is_expired(now):
            status = "expired"
        else:
            status = "on" if a.enabled else "off"
        rows.append((str(a.id), a.time_str, a.repeat_str, next_str, status, a.label))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    for i, row in enumerate(rows):
        line = "  ".join(cell.ljust(widths[j]) for j, cell in enumerate(row)).rstrip()
        print(ui.bold(line) if i == 0 else line)


def describe(alarm: Alarm, now: datetime) -> str:
    nxt = alarm.next_trigger(now)
    when = f"rings {nxt:%a %d %b at %H:%M} (in {humanize(nxt - now)})" if nxt else "will not ring"
    label = f" '{alarm.label}'" if alarm.label else ""
    return f"Alarm #{alarm.id}{label} {alarm.time_str} {alarm.repeat_str}: {when}"


def resolve_schedule(time_text: str, repeat: str, date_text: Optional[str], now: datetime):
    t = parse_time(time_text)
    days = parse_days(repeat)
    if days:
        if date_text:
            raise CliError("--date only applies to one-shot alarms (drop --repeat)")
        return t, days, None
    if date_text:
        try:
            day = datetime.strptime(date_text, "%Y-%m-%d").date()
        except ValueError as exc:
            raise CliError(f"invalid --date {date_text!r}, expected YYYY-MM-DD") from exc
        if datetime.combine(day, t) <= now:
            raise CliError(f"{day} {t:%H:%M} is in the past")
    else:
        day = next_date_for(t, now)
    return t, [], day.isoformat()


# --- commands -----------------------------------------------------------------------------


def cmd_add(args, store: AlarmStore, clock: Clock) -> int:
    now = clock()
    if args.in_ and args.time:
        raise CliError("give either a TIME or --in DURATION, not both")
    if args.in_:
        if args.repeat != "once" or args.date:
            raise CliError("--in creates a one-shot alarm; it can't be combined with --repeat/--date")
        # Alarms have minute precision: round up so it never rings early.
        exact = now + parse_duration(args.in_)
        target = exact.replace(second=0, microsecond=0)
        if target < exact:
            target += timedelta(minutes=1)
        hour, minute, days, day = target.hour, target.minute, [], target.date().isoformat()
    elif args.time:
        t, days, day = resolve_schedule(args.time, args.repeat, args.date, now)
        hour, minute = t.hour, t.minute
    else:
        raise CliError("missing TIME (e.g. `alarm add 07:30`) or --in DURATION")
    if args.snooze < 1:
        raise CliError("--snooze must be at least 1 minute")
    if args.sound and not Path(args.sound).is_file():
        raise CliError(f"sound file not found: {args.sound}")

    alarm = store.add(
        Alarm(
            id=0,
            hour=hour,
            minute=minute,
            label=args.label,
            days=days,
            date=day,
            snooze_minutes=args.snooze,
            sound=str(Path(args.sound).resolve()) if args.sound else None,
        )
    )
    print("Added " + describe(alarm, now))
    print("Keep `alarm run` open in a terminal so it can ring.")
    return 0


def cmd_list(args, store: AlarmStore, clock: Clock) -> int:
    now = clock()
    alarms = store.load()
    shown = alarms if args.all else [a for a in alarms if a.enabled and not a.is_expired(now)]
    if not shown:
        hidden = len(alarms) - len(shown)
        print("No active alarms." + (f" ({hidden} hidden, use --all)" if hidden else " Add one with `alarm add 07:30`."))
        return 0
    print_table(shown, now)
    return 0


def cmd_remove(args, store: AlarmStore, clock: Clock) -> int:
    removed = store.remove(args.ids)
    print("Removed alarm " + ", ".join(f"#{i}" for i in removed))
    return 0


def cmd_clear(args, store: AlarmStore, clock: Clock) -> int:
    alarms = store.load()
    if not alarms:
        print("No alarms to delete.")
        return 0
    if not args.yes:
        if not sys.stdin.isatty():
            raise CliError("refusing to delete all alarms without --yes")
        try:
            answer = input(f"Delete all {len(alarms)} alarms? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            print("Cancelled.")
            return 1
    store.save([])
    print(f"Deleted {len(alarms)} alarms.")
    return 0


def cmd_toggle(args, store: AlarmStore, clock: Clock) -> int:
    now = clock()
    enable = args.command == "enable"
    alarms = {a.id: a for a in store.load()}
    missing = [i for i in args.ids if i not in alarms]
    if missing:
        raise StoreError("no alarm with id " + ", ".join(map(str, missing)))
    for alarm_id in args.ids:
        alarm = alarms[alarm_id]
        alarm.enabled = enable
        if enable and alarm.is_expired(now):
            # Re-arm a one-shot alarm that already rang: next occurrence of its time.
            alarm.date = next_date_for(alarm.time, now).isoformat()
    store.save(alarms.values())
    for alarm_id in args.ids:
        alarm = alarms[alarm_id]
        print(("Enabled " if enable else "Disabled ") + describe(alarm, now))
    return 0


def cmd_edit(args, store: AlarmStore, clock: Clock) -> int:
    now = clock()
    alarm = store.get(args.id)
    if args.time is None and args.repeat is None and args.label is None and args.snooze is None and args.sound is None:
        raise CliError("nothing to change (use --time, --label, --repeat, --snooze or --sound)")
    if args.time is not None or args.repeat is not None:
        time_text = args.time if args.time is not None else alarm.time_str
        repeat = args.repeat if args.repeat is not None else (",".join(_day_names(alarm.days)) or "once")
        t, days, day = resolve_schedule(time_text, repeat, None, now)
        alarm.hour, alarm.minute, alarm.days, alarm.date = t.hour, t.minute, days, day
        alarm.enabled = True
    if args.label is not None:
        alarm.label = args.label
    if args.snooze is not None:
        if args.snooze < 1:
            raise CliError("--snooze must be at least 1 minute")
        alarm.snooze_minutes = args.snooze
    if args.sound is not None:
        if args.sound and not Path(args.sound).is_file():
            raise CliError(f"sound file not found: {args.sound}")
        alarm.sound = str(Path(args.sound).resolve()) if args.sound else None
    store.update(alarm)
    print("Updated " + describe(alarm, now))
    return 0


def _day_names(days: Sequence[int]) -> list[str]:
    return [WEEKDAY_NAMES[d] for d in days]


def cmd_next(args, store: AlarmStore, clock: Clock) -> int:
    now = clock()
    upcoming = [(a.next_trigger(now), a) for a in store.load()]
    upcoming = [(w, a) for w, a in upcoming if w is not None]
    if not upcoming:
        print("No upcoming alarms.")
        return 0
    when, alarm = min(upcoming, key=lambda x: x[0])
    print(describe(alarm, now))
    return 0


def cmd_run(args, store: AlarmStore, clock: Clock) -> int:
    def ring(alarm: Alarm, when: datetime) -> str:
        return ui.ring_interactive(alarm, when, quiet=args.quiet, timeout=args.ring_timeout, clock=clock)

    def missed(alarm: Alarm, when: datetime) -> None:
        ui.end_status_line()
        print(f"Missed alarm #{alarm.id} {alarm.display_name} at {when:%a %H:%M} (clock was not running)")

    scheduler = Scheduler(store, ring, clock=clock, on_missed=missed)
    print(ui.bold("Alarm clock running") + f" - {store.path}")
    print("Add or change alarms from another terminal at any time. Ctrl+C to quit.\n")
    try:
        while True:
            scheduler.tick()
            now = clock()
            nxt = scheduler.upcoming(now)
            if nxt:
                when, alarm, snoozed = nxt
                tag = "snoozed " if snoozed else ""
                info = f"next: {tag}#{alarm.id} {alarm.display_name} {when:%a %H:%M} (in {humanize(when - now)})"
            else:
                info = "no upcoming alarms"
            ui.status_line(f"  {now:%H:%M:%S}   {info}")
            # Sleep to just past the next whole second so the clock display is smooth.
            _time.sleep(max(0.05, 1.0 - now.microsecond / 1e6))
    except KeyboardInterrupt:
        ui.end_status_line()
        print("Alarm clock stopped.")
        return 0


def cmd_timer(args, store: AlarmStore, clock: Clock) -> int:
    duration = parse_duration(args.duration)
    if args.sound and not Path(args.sound).is_file():
        raise CliError(f"sound file not found: {args.sound}")
    started = clock()
    print(f"Timer '{args.label}' for {humanize(duration)}, done at {started + duration:%H:%M:%S}")
    try:
        ui.countdown(duration.total_seconds(), args.label)
    except KeyboardInterrupt:
        ui.end_status_line()
        print("Timer cancelled.")
        return 130
    done = started + duration
    alarm = Alarm(id=0, hour=done.hour, minute=done.minute, label=f"{args.label} done", sound=args.sound)
    try:
        ui.ring_interactive(alarm, clock(), quiet=args.quiet, timeout=args.ring_timeout, allow_snooze=False, clock=clock)
    except KeyboardInterrupt:
        pass
    return 0


def cmd_test_sound(args, store: AlarmStore, clock: Clock) -> int:
    if args.sound and not Path(args.sound).is_file():
        raise CliError(f"sound file not found: {args.sound}")
    ringer = Ringer(args.sound)
    print(f"Playing alarm sound ({ringer.backend}) for {args.seconds:g}s...")
    ringer.start()
    try:
        _time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        ringer.stop()
    return 0


COMMANDS = {
    "add": cmd_add,
    "list": cmd_list,
    "ls": cmd_list,
    "remove": cmd_remove,
    "rm": cmd_remove,
    "delete": cmd_remove,
    "clear": cmd_clear,
    "enable": cmd_toggle,
    "disable": cmd_toggle,
    "edit": cmd_edit,
    "next": cmd_next,
    "run": cmd_run,
    "timer": cmd_timer,
    "test-sound": cmd_test_sound,
}


def _enable_windows_ansi() -> None:
    if os.name == "nt":
        os.system("")  # turns on VT escape processing in classic consoles


def main(argv: Optional[Sequence[str]] = None, clock: Clock = datetime.now) -> int:
    _enable_windows_ansi()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    store = AlarmStore(args.file)
    try:
        return COMMANDS[args.command](args, store, clock)
    except (ParseError, CliError, StoreError) as exc:
        print(f"alarm: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
