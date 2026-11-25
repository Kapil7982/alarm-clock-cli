"""JSON-file persistence for alarms (no database)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Iterable, Optional

from .models import Alarm

SCHEMA_VERSION = 1


def default_path() -> Path:
    """~/.alarmclock/alarms.json, or $ALARMCLOCK_HOME/alarms.json when set."""
    home = os.environ.get("ALARMCLOCK_HOME")
    base = Path(home) if home else Path.home() / ".alarmclock"
    return base / "alarms.json"


class StoreError(RuntimeError):
    pass


class AlarmStore:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else default_path()

    def load(self) -> list[Alarm]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError as exc:
            raise StoreError(f"alarm file {self.path} is corrupt: {exc}") from exc
        items = raw.get("alarms", []) if isinstance(raw, dict) else raw
        return sorted((Alarm.from_dict(item) for item in items), key=lambda a: a.id)

    def save(self, alarms: Iterable[Alarm]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": SCHEMA_VERSION,
            "alarms": [a.to_dict() for a in sorted(alarms, key=lambda a: a.id)],
        }
        # Write to a temp file and atomically replace, so a crash or a
        # concurrent reader never sees a half-written file.
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".alarms-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
                fh.write("\n")
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def mtime(self) -> float:
        try:
            return self.path.stat().st_mtime
        except FileNotFoundError:
            return 0.0

    # --- convenience operations -------------------------------------------------

    def add(self, alarm: Alarm) -> Alarm:
        alarms = self.load()
        alarm.id = max((a.id for a in alarms), default=0) + 1
        alarms.append(alarm)
        self.save(alarms)
        return alarm

    def get(self, alarm_id: int) -> Alarm:
        for alarm in self.load():
            if alarm.id == alarm_id:
                return alarm
        raise StoreError(f"no alarm with id {alarm_id}")

    def update(self, alarm: Alarm) -> None:
        alarms = self.load()
        for i, existing in enumerate(alarms):
            if existing.id == alarm.id:
                alarms[i] = alarm
                self.save(alarms)
                return
        raise StoreError(f"no alarm with id {alarm.id}")

    def remove(self, alarm_ids: Iterable[int]) -> list[int]:
        wanted = set(alarm_ids)
        alarms = self.load()
        missing = wanted - {a.id for a in alarms}
        if missing:
            raise StoreError("no alarm with id " + ", ".join(map(str, sorted(missing))))
        self.save([a for a in alarms if a.id not in wanted])
        return sorted(wanted)
