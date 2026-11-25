"""Non-blocking single-key input for Windows and POSIX terminals."""

from __future__ import annotations

import contextlib
import sys
import time
from typing import Iterator, Optional

try:
    import msvcrt  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore[assignment]


def stdin_is_tty() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


@contextlib.contextmanager
def cbreak() -> Iterator[None]:
    """Put a POSIX terminal into cbreak mode so keys arrive without Enter."""
    if msvcrt is not None or not stdin_is_tty():
        yield
        return
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def read_key(timeout: float) -> Optional[str]:
    """Return one pressed key (lower-cased) within `timeout` seconds, else None.

    Ctrl+C is turned into KeyboardInterrupt on every platform.
    """
    if not stdin_is_tty():
        time.sleep(timeout)
        return None
    if msvcrt is not None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in ("\x00", "\xe0"):  # arrow / function key prefix
                    msvcrt.getwch()
                    continue
                if ch == "\x03":
                    raise KeyboardInterrupt
                return ch.lower()
            time.sleep(0.02)
        return None

    import select

    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    if not ready:
        return None
    ch = sys.stdin.read(1)
    if ch == "\x03":
        raise KeyboardInterrupt
    return ch.lower()


def drain() -> None:
    """Discard keys typed before the alarm started ringing."""
    if msvcrt is not None and stdin_is_tty():
        while msvcrt.kbhit():
            msvcrt.getwch()
