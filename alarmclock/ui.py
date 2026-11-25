"""Terminal presentation: the ringing prompt, the live clock line, the countdown."""

from __future__ import annotations

import shutil
import sys
import time as _time
from datetime import datetime, timedelta
from typing import Callable, Optional

from . import keys
from .models import Alarm, humanize
from .scheduler import DISMISS, SNOOZE, TIMEOUT
from .sound import Ringer

USE_COLOR = sys.stdout.isatty()


def color(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text


def bold(text: str) -> str:
    return color(text, "1")


def status_line(text: str) -> None:
    """Rewrite the current terminal line in place (no-op when not a terminal)."""
    if not sys.stdout.isatty():
        return
    width = shutil.get_terminal_size((80, 20)).columns - 1
    sys.stdout.write("\r" + text[:width].ljust(width))
    sys.stdout.flush()


def end_status_line() -> None:
    if not sys.stdout.isatty():
        return
    width = shutil.get_terminal_size((80, 20)).columns - 1
    sys.stdout.write("\r" + " " * width + "\r")
    sys.stdout.flush()


def ring_interactive(
    alarm: Alarm,
    when: datetime,
    *,
    quiet: bool = False,
    timeout: float = 300,
    allow_snooze: bool = True,
    clock: Callable[[], datetime] = datetime.now,
) -> str:
    """Ring until the user dismisses/snoozes, or `timeout` seconds pass."""
    end_status_line()
    late = clock() - when
    late_note = f"  (late by {humanize(late)})" if late > timedelta(minutes=1) else ""
    banner = f" ALARM  {alarm.time_str}  {alarm.display_name} "
    print()
    print(color("=" * (len(banner) + 4), "31;1"))
    print(color(f"  {banner}  ", "41;97;1") + late_note)
    print(color("=" * (len(banner) + 4), "31;1"))
    if keys.stdin_is_tty():
        hint = "[Enter/d] dismiss"
        if allow_snooze:
            hint += f"   [s] snooze {alarm.snooze_minutes}m"
        print("  " + hint)
    else:
        print(f"  (no interactive terminal: ringing for {int(timeout)}s)")

    ringer = Ringer(alarm.sound, quiet=quiet)
    keys.drain()
    ringer.start()
    outcome = TIMEOUT
    deadline = _time.monotonic() + timeout
    try:
        with keys.cbreak():
            while _time.monotonic() < deadline:
                key = keys.read_key(0.2)
                if key in ("\r", "\n", "d", " ", "q"):
                    outcome = DISMISS
                    break
                if key == "s" and allow_snooze:
                    outcome = SNOOZE
                    break
    finally:
        ringer.stop()

    if outcome == DISMISS:
        print(color("  Dismissed.", "32"))
    elif outcome == SNOOZE:
        print(color(f"  Snoozed for {alarm.snooze_minutes} min.", "33"))
    else:
        print(color(f"  Stopped ringing after {int(timeout)}s.", "33"))
    print()
    return outcome


def countdown(
    seconds: float,
    label: str,
    on_tick: Optional[Callable[[], None]] = None,
) -> None:
    """Show a live countdown; returns when it reaches zero."""
    end = _time.monotonic() + seconds
    while True:
        remaining = end - _time.monotonic()
        if remaining <= 0:
            break
        mins, secs = divmod(int(remaining + 0.999), 60)
        hours, mins = divmod(mins, 60)
        shown = f"{hours:d}:{mins:02d}:{secs:02d}" if hours else f"{mins:02d}:{secs:02d}"
        status_line(f"  {shown}  {label}   (Ctrl+C to cancel)")
        if on_tick:
            on_tick()
        _time.sleep(min(0.25, remaining))
    end_status_line()
