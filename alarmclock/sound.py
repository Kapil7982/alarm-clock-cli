"""Cross-platform alarm sound: beeps by default, or a custom audio file.

Backends, in order of preference:
  * custom file on Windows -> winsound.PlaySound (WAV only)
  * custom file elsewhere  -> afplay (macOS) / paplay / aplay / ffplay (Linux)
  * no file on Windows     -> winsound.Beep pattern
  * anything else          -> terminal bell (\\a)
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

try:  # Windows only
    import winsound  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - non-Windows
    winsound = None  # type: ignore[assignment]

BEEP_PATTERN = [(880, 180), (0, 80), (880, 180), (0, 80), (1175, 300)]


def _find_player() -> Optional[list[str]]:
    for cmd in (["afplay"], ["paplay"], ["aplay", "-q"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]):
        if shutil.which(cmd[0]):
            return cmd
    return None


class Ringer:
    """Plays the alarm sound on a background thread until stop() is called."""

    def __init__(self, sound: Optional[str] = None, quiet: bool = False):
        self.sound = sound if sound and Path(sound).is_file() else None
        self.quiet = quiet
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._proc: Optional[subprocess.Popen] = None

    @property
    def backend(self) -> str:
        if self.quiet:
            return "silent"
        if self.sound and winsound is not None:
            return "winsound-file"
        if self.sound and _find_player():
            return "player"
        if winsound is not None:
            return "winsound-beep"
        return "bell"

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="alarm-ringer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
        if self.backend == "winsound-file":
            winsound.PlaySound(None, winsound.SND_PURGE)
        if self._thread:
            self._thread.join(timeout=2)

    # --- backends ----------------------------------------------------------------

    def _run(self) -> None:
        backend = self.backend
        try:
            if backend == "silent":
                self._stop.wait()
            elif backend == "winsound-file":
                flags = winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP
                winsound.PlaySound(self.sound, flags)
                self._stop.wait()
            elif backend == "player":
                player = _find_player() or []
                while not self._stop.is_set():
                    self._proc = subprocess.Popen(
                        player + [self.sound],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    while self._proc.poll() is None and not self._stop.wait(0.1):
                        pass
                    self._stop.wait(0.3)
            elif backend == "winsound-beep":
                while not self._stop.is_set():
                    for freq, ms in BEEP_PATTERN:
                        if self._stop.is_set():
                            break
                        if freq:
                            winsound.Beep(freq, ms)
                        else:
                            self._stop.wait(ms / 1000)
                    self._stop.wait(0.6)
            else:
                while not self._stop.is_set():
                    sys.stdout.write("\a")
                    sys.stdout.flush()
                    self._stop.wait(1.0)
        except Exception:  # never let a sound failure kill the alarm loop
            while not self._stop.is_set():
                sys.stdout.write("\a")
                sys.stdout.flush()
                self._stop.wait(1.0)
