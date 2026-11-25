# alarm — a terminal alarm clock

A small alarm clock that runs in your terminal. You can set one-shot and repeating alarms, snooze them, and run countdown timers.

- **CLI only.** It has no web UI and no database. Alarms are stored in a plain JSON file.
- **No dependencies.** It uses only the Python standard library (3.9+) and works on Windows, macOS and Linux.
- **Sound:** it beeps by default, or plays your own audio file.

```text
$ alarm add 7:30am --repeat weekdays --label "Wake up"
Added Alarm #1 'Wake up' 07:30 weekdays: rings Thu 26 Nov at 07:30 (in 19h 30m)

$ alarm list
ID  TIME   REPEAT             NEXT RING                      STATUS  LABEL
1   07:30  weekdays           Thu 26 Nov 07:30 (in 19h 30m)  on      Wake up
2   22:00  once (2026-11-25)  Wed 25 Nov 22:00 (in 10h 00m)  on      Sleep

$ alarm run
Alarm clock running - C:\Users\you\.alarmclock\alarms.json
  11:59:58   next: #3 Tea Wed 12:00 (in 2s)

==============================
   ALARM  12:00  Tea
==============================
  [Enter/d] dismiss   [s] snooze 5m
```

## Install

```bash
git clone https://github.com/Kapil7982/alarm-clock-cli.git
cd alarm-clock-cli
pip install .          # installs the `alarm` command
```

You can also skip installing it and run it straight from the folder with `python -m alarmclock <command>`.

## How it works

You add alarms with `alarm add`, and `alarm run` rings them. Keep `alarm run` open in a terminal window. Alarms only ring while it is running, just like a real alarm clock only rings while it has power.

You can add, edit or remove alarms from another terminal while `alarm run` is open. It notices changes to the alarm file right away.

## Commands

| Command | What it does |
|---|---|
| `alarm add TIME [options]` | Add an alarm at a time of day |
| `alarm add --in 20m` | Add an alarm that rings after a delay |
| `alarm list [--all]` | List active alarms (`--all` also shows disabled and expired ones) |
| `alarm next` | Show the next alarm that will ring |
| `alarm edit ID [options]` | Change an alarm's time, label, repeat, snooze or sound |
| `alarm enable ID...` / `alarm disable ID...` | Turn alarms on or off |
| `alarm remove ID...` (`rm`) | Delete alarms |
| `alarm clear [--yes]` | Delete all alarms |
| `alarm run` | Run the clock in the foreground and ring alarms |
| `alarm timer 10m [-l Tea]` | Count down, then ring |
| `alarm test-sound` | Play the alarm sound for 3 seconds |

### `add` options

| Option | Meaning |
|---|---|
| `TIME` | `07:30`, `7:30`, `730`, `19:30`, `7pm`, `7:30 am`, `12am` |
| `-l, --label TEXT` | The text shown when the alarm rings |
| `-r, --repeat DAYS` | `once` (default), `daily`, `weekdays`, `weekends`, or a list of days like `mon,wed,fri` (full names and short forms like `tu` and `th` also work) |
| `--date YYYY-MM-DD` | The day a one-shot alarm rings. Without it, the alarm rings the next time that time comes round: today if it hasn't passed yet, otherwise tomorrow. |
| `--in DURATION` | Ring after a delay instead of at a time: `90s`, `20m`, `1h30m`, or a plain number of minutes. It rounds up to the next whole minute. |
| `--snooze MIN` | How long a snooze lasts, in minutes (default 5) |
| `--sound FILE` | Audio file to play instead of beeps |

### `run` options

| Option | Meaning |
|---|---|
| `--ring-timeout SEC` | Stop ringing after this many seconds if nobody responds (default 300) |
| `--quiet` | Show alarms without sound |

When an alarm rings, press **Enter** (or `d`) to dismiss it, or **`s`** to snooze it.

## Examples

```bash
alarm add 06:45 -r mon,wed,fri -l "Gym" --snooze 10
alarm add 9am --date 2026-12-31 -l "Dentist"
alarm add --in 25m -l "Pomodoro break"
alarm edit 1 --time 7:00
alarm disable 1
alarm timer 3m -l "Tea"
alarm add 7:00 --sound ~/Music/rooster.wav
```

## Behaviour details

- **One-shot alarms** turn themselves off after they ring. Use `alarm enable ID` to arm one again for the next time its time comes round.
- **Missed alarms:** if the clock wasn't running, it doesn't ring old alarms when it starts. If the computer was asleep and wakes up within an hour of an alarm, the alarm still rings, with a "late by" note. If it wakes up more than an hour late, the alarm is reported as missed and doesn't ring.
- **Snoozes** are only kept in memory. If you quit `alarm run`, any pending snooze is lost. A snoozed alarm that you delete won't ring again.
- **Clock changes:** if the system clock jumps backwards (daylight saving time or an NTP correction), an alarm won't ring twice.
- **Sound backends:**
  - Windows: `winsound` beeps, or a WAV file
  - macOS: `afplay`
  - Linux: `paplay`, `aplay` or `ffplay`
  - Anywhere else: the terminal bell
  - Run `alarm test-sound` to check which one you get.

## Where alarms are stored

Alarms are saved in `~/.alarmclock/alarms.json`. To use a different folder, set `ALARMCLOCK_HOME`. To use a different file for a single command, pass `--file PATH`. The file is plain JSON. It is written atomically, so a crash never leaves it half-written.

## Project layout

```text
alarmclock/
  cli.py        argument parsing and the commands
  models.py     Alarm dataclass; parsing of times, durations and days; next-ring calculation
  store.py      JSON file storage (atomic writes)
  scheduler.py  decides what is due, handles snoozes and missed alarms
  sound.py      cross-platform ringer running on a background thread
  keys.py       non-blocking key presses (msvcrt on Windows, termios on POSIX)
  ui.py         the ringing banner, the live clock line and the countdown
tests/          unittest suite (no extra packages needed)
```

## Running the tests

```bash
python -m unittest discover -s tests -t . -v
```

The scheduler is tested with a fake clock. That covers repeating alarms, snooze, missed alarms, the clock moving backwards, and alarms added while the clock is running.

## License

MIT
