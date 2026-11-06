#!/usr/bin/env python3
"""The working window: when the system may start new work (KIT-437, MVP 2 part 4;
docs/adr/2026-11-03-autonomous-workers-within-a-budget-and-a-window.md, decision 4).

    python3 scripts/pipeline_window.py now [--config FILE] [--at 2026-11-04T14:00:00Z]
    python3 scripts/pipeline_window.py --selftest

ONE SHARED CHECK.  The dispatcher has no clock of its own, so every job that starts work asks
this module whether the window is open. It decides nothing else. Calling it from the queue,
Stage E's jobs and the planner is later work, with each file's owner.

PURE.  The time is always passed in. Above the command line, nothing in this file opens a file,
reads an environment variable or the clock, opens a socket or starts a process: the selftest
reads the source to prove it. The one outside read is `config`'s, for a zone it is given by
name: the standard library loads that zone's rules. Given what `config` made, `window_at` and
`day_window` read only their arguments. A config with no zone means the machine's own, whatever
the C library says it is at the moment they run.

THE CONFIG.  {"time_zone": null or an IANA name, "days": {"mon": {"start": "09:00", "hours": 6}
or null, ... "sun": ...}, "grace_minutes": 0 or more}. The kit's default is Monday to Saturday,
09:00 for 6 hours, Sunday off, the machine's zone, 60 minutes' grace. A `days` that is supplied
overrides only the days it names. `hours` is above 0 and up to 24, and is rounded to the second.
`config(raw)` returns (cfg, errors): every unknown key and bad value is named, all at once, and
cfg is None while there is any.

THE WINDOW.  A window begins at its day's local start and ends `hours` of REAL time later, so
one across a daylight-saving change ends an hour from its wall-clock end, and a 24-hour window on
the day the clocks go back ends an hour before the next day's begins. It may cross midnight:
early on Tuesday can be inside Monday's. It is open from its start up to, not at, its end. A
start the clocks make twice (they go back) is its first time. A start that falls in a
spring-forward gap, a time that never happens, begins at the first valid instant after it: 02:30
on the day the clocks jump from 02:00 to 03:00 begins at 03:00.

WHAT IT RETURNS.  window_at(cfg, now_epoch) -> {"open", "why", "opened_at", "closes_at",
"next_opens_at", "grace_ends_at"}. Times are UTC, as 2026-11-04T14:00:00Z, or None.
  why            "open"; else, by the local date it is: "day-off" (it has no window),
                 "before-start" or "after-close"; or "no-window" (no day has one at all).
  opened_at,     the current window's start and end; closed, the most recent one's. Where
  closes_at      windows overlap, the one that closes last.
  next_opens_at  the next window's start after now. Days with no window are skipped.
  grace_ends_at  the most recent close plus the grace (an earlier window's, while one is open):
                 a run still going past it gets one note.
day_window(cfg, date) -> (start, end) in seconds since 1970 of the window that begins on a local
date (a datetime.date, or 2026-11-04), or None. The stand-up reads its times from it.

The command line prints the state as JSON. Exit 0: printed. 1: the config would not do, or its
file could not be read. 2: a usage error.
"""
import argparse
import copy
import datetime
import json
import math
import re
import sys
import time
import zoneinfo

EXIT_OK, EXIT_ERROR, EXIT_USAGE = 0, 1, 2
DAY = 86400
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")        # datetime.date.weekday() order
WHYS = ("open", "day-off", "before-start", "after-close", "no-window")
KEYS = ("time_zone", "days", "grace_minutes")
DAY_KEYS = ("start", "hours")
DEFAULTS = {
    "time_zone": None,                                   # the machine's own zone
    "days": dict((d, None if d == "sun" else {"start": "09:00", "hours": 6}) for d in DAYS),
    "grace_minutes": 60,
}
LOOK_BACK = LOOK_AHEAD = 8              # days searched around today: a weekday comes round in 7
EPOCH = datetime.date(1970, 1, 1)
START_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]\Z")
TIME_RE = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})[T ]([0-9]{2}):([0-9]{2})(?::([0-9]{2}))?"
                     r"(Z|[+-][0-9]{2}:?[0-9]{2})\Z")


# --------------------------------------------------------------------------- #
# Small pure helpers
# --------------------------------------------------------------------------- #
def whole(v):
    return isinstance(v, int) and not isinstance(v, bool)


def number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def seconds(hours):
    """A window's length in whole seconds."""
    return int(round(hours * 3600))


def iso(seconds):
    """Seconds since 1970 as 2026-11-04T14:00:00Z."""
    return datetime.datetime.fromtimestamp(
        seconds, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch(text):
    """A time with its zone, 2026-11-04T14:00:00Z or 2026-11-04T09:00-05:00, as seconds since
    1970; None for anything else. A time with no zone is not one: it could be two instants."""
    m = TIME_RE.match(text) if isinstance(text, str) else None
    if not m:
        return None
    year, month, day, hour, minute, second, zone = m.groups()
    if zone != "Z" and (int(zone[1:3]) > 23 or int(zone[-2:]) > 59):
        return None
    shift = 0 if zone == "Z" else (1 if zone[0] == "+" else -1) * (
        int(zone[1:3]) * 3600 + int(zone[-2:]) * 60)
    try:
        read = datetime.datetime(int(year), int(month), int(day), int(hour), int(minute),
                                 int(second or 0), tzinfo=datetime.timezone.utc)
    except ValueError:
        return None
    return int(read.timestamp()) - shift


def as_date(date):
    """A local date: a datetime.date, or 2026-11-04. A time is refused: which day is it on?"""
    if isinstance(date, str) and re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\Z", date):
        try:
            return datetime.date.fromisoformat(date)
        except ValueError:
            pass
    elif isinstance(date, datetime.date) and not isinstance(date, datetime.datetime):
        return date
    raise ValueError("not a date: %r" % (date,))


def day_name(date):
    return DAYS[date.weekday()]


# --------------------------------------------------------------------------- #
# Zones: an offset function, and the one conversion that needs it
# --------------------------------------------------------------------------- #
def machine_offset(epoch_seconds):
    """Seconds east of UTC on the machine's own clock at an instant. It is given the instant:
    this never asks what time it is."""
    return time.localtime(epoch_seconds).tm_gmtoff


def zone_offset(name):
    """A function from an instant to seconds east of UTC, for the IANA zone `name`, or for the
    machine's own zone when it is None. A name this machine cannot load is a ValueError."""
    if name is None:
        return machine_offset
    try:
        zone = zoneinfo.ZoneInfo(name)
    except (KeyError, ValueError, OSError):
        raise ValueError("not a zone this machine knows: %r" % (name,))
    return lambda seconds: int(
        datetime.datetime.fromtimestamp(seconds, zone).utcoffset().total_seconds())


def instant(offset, wall):
    """The instant a zone's clock reads `wall` (seconds since 1970, as if the zone were UTC).
    A reading the clocks make twice, when they go back, is its first. A reading they never
    make, in the gap when they go forward, is the first valid instant after it: 02:30 on the day
    the clocks jump from 02:00 to 03:00 is 03:00."""
    before, after = offset(wall - DAY), offset(wall + DAY)
    reads = sorted(e for e in set((wall - before, wall - after)) if e + offset(e) == wall)
    if reads:
        return reads[0]
    lo, hi = sorted((wall - before, wall - after))        # the clocks jumped over `wall` in here
    while hi - lo > 1:
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if offset(mid) == offset(lo) else (lo, mid)
    return hi


# --------------------------------------------------------------------------- #
# The config
# --------------------------------------------------------------------------- #
class Config(dict):
    """What `config` makes: a plain dict of the three settings, every weekday named, and
    `offset`, the zone as a function from an instant to seconds east of UTC. It is not a
    setting, so it is not in the dict."""
    offset = None


def unknown(obj, allowed, where, what):
    return ["%s%s: %s (%s)" % (where, repr(k)[:40], what, ", ".join(allowed))
            for k in sorted(obj, key=repr) if k not in allowed]


def valid_start(text):
    return isinstance(text, str) and START_RE.match(text) is not None


def valid_hours(hours):
    return number(hours) and 0 < hours <= 24 and seconds(hours) >= 1


def valid_grace(minutes):
    return whole(minutes) and minutes >= 0


def check_day(name, spec):
    """What is wrong with one weekday's setting: nothing, or a start and a length."""
    where = "days.%s" % name
    if spec is None:
        return []
    if not isinstance(spec, dict):
        return ["%s: must be null (no window) or an object with start and hours" % where]
    bad = unknown(spec, DAY_KEYS, where + ".", "not a setting")
    bad += ["%s.%s: missing" % (where, k) for k in DAY_KEYS if k not in spec]
    if "start" in spec and not valid_start(spec["start"]):
        bad.append('%s.start: must be a 24-hour time such as "09:00"' % where)
    if "hours" in spec and not valid_hours(spec["hours"]):
        bad.append("%s.hours: must be a number above 0, up to 24 (a second at the least)" % where)
    return bad


def check_days(days):
    """(days, errors): all seven weekdays, the kit's own under any `days` does not name."""
    if not isinstance(days, dict):
        return None, ["days: must be an object naming weekdays (%s)" % ", ".join(DAYS)]
    bad = unknown(days, DAYS, "days.", "not a weekday")
    out = dict((d, copy.deepcopy(DEFAULTS["days"][d])) for d in DAYS)
    for name in DAYS:
        if name in days:
            bad += check_day(name, days[name])
            out[name] = dict(days[name]) if isinstance(days[name], dict) else None
    return out, bad


def config(raw):
    """(cfg, errors): the settings with the kit's defaults under whatever `raw` leaves out, and
    everything wrong with `raw`, each fault named by its key. cfg is None while there is any,
    so a config that was refused cannot be used by mistake. Idempotent, and `raw` is not
    changed."""
    if not isinstance(raw, dict):
        return None, ["the config is not an object"]
    bad = unknown(raw, KEYS, "", "not a setting")
    zone, days, grace = (raw.get(k, DEFAULTS[k]) for k in KEYS)
    offset = None
    try:
        if zone is not None and not isinstance(zone, str):
            raise ValueError("not a name")
        offset = zone_offset(zone)
    except ValueError:
        bad.append("time_zone: must be null (this machine's own zone) or a zone it knows, "
                   "such as America/New_York")
    days, wrong = check_days(days)
    bad += wrong
    if not valid_grace(grace):
        bad.append("grace_minutes: must be a whole number of minutes, 0 or more")
    if bad:
        return None, bad
    cfg = Config(time_zone=zone, days=days, grace_minutes=grace)
    cfg.offset = offset
    return cfg, []


def ready(cfg):
    """`cfg` as `config` makes it: one that is, as it is; anything else read through `config`
    first (a ValueError, naming each fault, when it will not do)."""
    if isinstance(cfg, Config) and cfg.offset is not None:
        return cfg
    made, bad = config(cfg)
    if bad:
        raise ValueError("the window's config is not usable: " + "; ".join(bad))
    return made


# --------------------------------------------------------------------------- #
# The window
# --------------------------------------------------------------------------- #
def window_end(offset, wall, start, hours):
    """A window ends `hours` of real time after its start, whatever the clocks do between."""
    return start + seconds(hours)


def day_window(cfg, date):
    """(start, end) of the window that begins on a local date, in seconds since 1970, or None.
    The end is on a later date when the window crosses midnight."""
    cfg, date = ready(cfg), as_date(date)
    spec = cfg["days"][day_name(date)]
    if spec is None:
        return None
    hour, minute = spec["start"].split(":")
    wall = (date - EPOCH).days * DAY + int(hour) * 3600 + int(minute) * 60
    start = instant(cfg.offset, wall)
    return start, window_end(cfg.offset, wall, start, spec["hours"])


def inside(win, now):
    return win[0] <= now < win[1]


def opens_after(win, now):
    return win[0] > now


def plus_grace(close, minutes):
    return close + minutes * 60


def last_to_close(wins):
    """The window that closes last, the earliest-starting of those that close together."""
    return max(wins, key=lambda w: (w[1], -w[0])) if wins else None


def no_windows(cfg):
    return not any(cfg["days"].values())


def closed_why(today, now):
    """Why a closed window is closed, by today's own window: it has none, it has not begun, or
    it is over."""
    if today is None:
        return "day-off"
    return "before-start" if now < today[0] else "after-close"


def around(cfg, today):
    """Every window that begins within LOOK_BACK days before a local date and LOOK_AHEAD after."""
    days = (today + datetime.timedelta(days=n) for n in range(-LOOK_BACK, LOOK_AHEAD + 1))
    return sorted(w for w in (day_window(cfg, d) for d in days) if w)


def windows_left(cfg, now_epoch, until):
    """How many working windows are left from an instant to another, today's included: each
    that ends after `now_epoch` and begins before `until`, both in seconds since 1970. A window
    open now counts, and one that begins at `until` does not. 0 when none is. The searched
    days are LOOK_AHEAD around today, so `until` should be no more than that far off (the
    queue's pacing reads a weekly reset, at most eight days away)."""
    cfg = ready(cfg)
    if not (number(now_epoch) and number(until)):
        raise ValueError("times must be seconds since 1970")
    today = EPOCH + datetime.timedelta(days=int((now_epoch + cfg.offset(now_epoch)) // DAY))
    return sum(1 for w in around(cfg, today) if w[1] > now_epoch and w[0] < until)


def window_at(cfg, now_epoch):
    """The window's state at an instant, in seconds since 1970. See the module's docstring."""
    cfg = ready(cfg)
    if not number(now_epoch):
        raise ValueError("now must be seconds since 1970, not %r" % (now_epoch,))
    state = {"open": False, "why": "no-window", "opened_at": None, "closes_at": None,
             "next_opens_at": None, "grace_ends_at": None}
    if no_windows(cfg):
        return state
    today = EPOCH + datetime.timedelta(days=int((now_epoch + cfg.offset(now_epoch)) // DAY))
    wins = around(cfg, today)
    live = [w for w in wins if inside(w, now_epoch)]
    gone = [w for w in wins if w[1] <= now_epoch]
    ahead = [w[0] for w in wins if opens_after(w, now_epoch)]
    current, last = last_to_close(live or gone), last_to_close(gone)
    state["open"] = bool(live)
    state["why"] = "open" if live else closed_why(day_window(cfg, today), now_epoch)
    if current:
        state["opened_at"], state["closes_at"] = iso(current[0]), iso(current[1])
    if ahead:
        state["next_opens_at"] = iso(min(ahead))
    if last:
        state["grace_ends_at"] = iso(plus_grace(last[1], cfg["grace_minutes"]))
    return state


# --------------------------------------------------------------------------- #
# THE COMMAND LINE: the one part that reads a file or the clock. Nothing above does.
# --------------------------------------------------------------------------- #
def read_file(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def clock():
    return time.time()


def main(argv=None):
    p = argparse.ArgumentParser(prog="pipeline_window.py", description=__doc__.split("\n")[0])
    p.add_argument("--selftest", action="store_true")
    sub = p.add_subparsers(dest="cmd")
    n = sub.add_parser("now", help="print the window's state as JSON")
    n.add_argument("--config", help="a JSON config file; the kit's default when left out")
    n.add_argument("--at", help="the time to ask about, as 2026-11-04T14:00:00Z; now when left out")
    args = p.parse_args(argv)
    if args.selftest:
        from scratch_run import contained   # KIT-450: its temp folders go when it ends
        return contained(selftest, "window-selftest-run-")
    if args.cmd != "now":
        p.print_help()
        return EXIT_USAGE
    at = clock() if args.at is None else epoch(args.at)
    if at is None:
        sys.stderr.write("REFUSED: --at must look like 2026-11-04T14:00:00Z, with Z or an offset\n")
        return EXIT_USAGE
    raw = {}
    if args.config:
        try:
            raw = json.loads(read_file(args.config))
        except (OSError, ValueError) as exc:
            sys.stderr.write("REFUSED: %s is not a readable JSON file (%s)\n" % (
                args.config, type(exc).__name__))
            return EXIT_ERROR
    cfg, bad = config(raw)
    for fault in bad:
        sys.stderr.write("REFUSED: %s\n" % fault)
    if bad:
        return EXIT_ERROR
    print(json.dumps(window_at(cfg, at), indent=2, sort_keys=True))
    return EXIT_OK


# --------------------------------------------------------------------------- #
# --selftest: nothing fetched, nothing written, the time and the machine's zone stood in for
# --------------------------------------------------------------------------- #
def selftest():
    import contextlib
    import os
    fails, ran = [], [0]

    def check(name, got, want):
        ran[0] += 1
        good = got == want
        print("[%s] %s%s" % ("PASS" if good else "FAIL", name,
                             "" if good else "  (got %r, want %r)" % (got, want)))
        if not good:
            fails.append(name)

    stack = [os.environ.get("TZ")]        # the machine's zone as it was, then as each block sets it
    before = time.localtime(0).tm_gmtoff

    def put(zone):
        if zone is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = zone
        time.tzset()

    @contextlib.contextmanager
    def machine(zone):
        """The machine's own zone, set for a block and put back after it."""
        stack.append(zone)
        put(zone)
        try:
            yield
        finally:
            stack.pop()
            put(stack[-1])
    with machine("America/New_York"):
        _cases(check, machine)
    check("the machine's own zone is as the selftest found it",
          (os.environ.get("TZ"), time.localtime(0).tm_gmtoff), (stack[0], before))
    print("\n%d/%d checks passed" % (ran[0] - len(fails), ran[0]))
    return 1 if fails else 0


def _cases(check, machine):
    import ast
    import builtins
    import contextlib
    import inspect
    import io
    import os
    import socket
    import subprocess
    import tempfile
    NY, SYD, TOKYO = "America/New_York", "Australia/Sydney", "Asia/Tokyo"

    def Z(s):
        """'11-04 14:00' as 2026-11-04T14:00:00Z: every instant here is in 2026, in UTC."""
        return "2026-%s%sZ" % (s.replace(" ", "T"), "" if s.count(":") > 1 else ":00")

    def T(s):
        return epoch(Z(s))

    def days(**spec):
        return dict((k, v and {"start": v[0], "hours": v[1]}) for k, v in spec.items())
    ALL, OFF = dict.fromkeys(DAYS, ("00:00", 24)), dict.fromkeys(DAYS)
    RAW = {
        "ny": {"time_zone": NY}, "syd": {"time_zone": SYD}, "local": {},
        "grace0": {"time_zone": NY, "grace_minutes": 0},
        "grace90": {"time_zone": NY, "grace_minutes": 90},
        "sun_mid": {"time_zone": NY, "days": days(sun=("00:00", 6))},
        "sun_gap": {"time_zone": NY, "days": days(sun=("02:30", 2))},
        "sun_twice": {"time_zone": NY, "days": days(sun=("01:30", 2))},
        "sun_9": {"time_zone": NY, "days": days(sun=("09:00", 3))},
        "syd_mid": {"time_zone": SYD, "days": days(sun=("00:00", 6))},
        "syd_gap": {"time_zone": SYD, "days": days(sun=("02:30", 2))},
        "local_gap": {"days": days(sun=("02:30", 2))},
        "late_mon": {"time_zone": NY, "days": days(mon=("22:00", 6))},
        "sat_late": {"time_zone": NY, "days": days(sat=("20:00", 8))},
        "tie": {"time_zone": NY, "days": days(sat=("20:00", 12), sun=("04:00", 4))},
        "odd": {"time_zone": NY, "days": days(mon=("09:00", 0.50015))},
        "all24": {"time_zone": NY, "days": days(**ALL)},
        "override": {"time_zone": NY, "days": days(sun=("10:00", 2), sat=None)},
        "only_wed": {"time_zone": NY, "days": days(**dict.fromkeys(set(DAYS) - set(["wed"])))},
        "none": {"time_zone": NY, "days": days(**OFF)},
    }
    SC = []

    def S(name, want, thunk):
        SC.append((name, want, thunk))

    def view(r):
        return (r["open"], r["why"], r["opened_at"], r["closes_at"], r["next_opens_at"],
                r["grace_ends_at"])

    def row(name, key, now, why, opened=None, closes=None, nxt=None, grace=None, zone=None):
        """One instant, asked about under one config: the state, its times as 'MM-DD HH:MM' UTC."""
        want = (why == "open", why) + tuple(t and Z(t) for t in (opened, closes, nxt, grace))

        def ask(cfgs):
            with machine(zone or NY):
                return view(window_at(cfgs[key], T(now)))
        S("%s: %s" % (key, name), want, ask)

    def dw(name, key, date, want):
        def ask(cfgs):
            w = day_window(cfgs[key], date)
            return w and (iso(w[0]), iso(w[1]))
        S("day_window, %s: %s" % (key, name), want and (Z(want[0]), Z(want[1])), ask)
    # -- Monday to Saturday open, Sunday off; before, inside and after; the next opening ----------
    row("before the start", "ny", "11-04 13:59:59", "before-start",
        "11-03 14:00", "11-03 20:00", "11-04 14:00", "11-03 21:00")
    row("the start itself is open", "ny", "11-04 14:00", "open",
        "11-04 14:00", "11-04 20:00", "11-05 14:00", "11-03 21:00")
    row("a second before the close is open", "ny", "11-04 19:59:59", "open",
        "11-04 14:00", "11-04 20:00", "11-05 14:00", "11-03 21:00")
    row("the close itself is closed", "ny", "11-04 20:00", "after-close",
        "11-04 14:00", "11-04 20:00", "11-05 14:00", "11-04 21:00")
    row("a second before the grace ends is closed still", "ny", "11-04 20:59:59", "after-close",
        "11-04 14:00", "11-04 20:00", "11-05 14:00", "11-04 21:00")
    row("Saturday after 3pm: the next opening is Monday at 9am", "ny", "11-07 20:00:01",
        "after-close", "11-07 14:00", "11-07 20:00", "11-09 14:00", "11-07 21:00")
    row("Sunday is off all day", "ny", "11-08 17:00", "day-off",
        "11-07 14:00", "11-07 20:00", "11-09 14:00", "11-07 21:00")
    row("Monday before the start", "ny", "11-09 13:00", "before-start",
        "11-07 14:00", "11-07 20:00", "11-09 14:00", "11-07 21:00")
    S("Monday to Saturday are open at noon and Sunday is off", ["open"] * 6 + ["day-off"],
      lambda cfgs: [window_at(cfgs["ny"], T("11-0%d 17:00" % d))["why"] for d in range(2, 9)])
    # -- the grace, a per-day override, no window, one weekday ------------------------------------
    row("a grace of 0 ends at the close", "grace0", "11-04 20:30", "after-close",
        "11-04 14:00", "11-04 20:00", "11-05 14:00", "11-04 20:00")
    row("a grace of 90 minutes", "grace90", "11-04 20:30", "after-close",
        "11-04 14:00", "11-04 20:00", "11-05 14:00", "11-04 21:30")
    row("an override turns Sunday on", "override", "11-08 16:00", "open",
        "11-08 15:00", "11-08 17:00", "11-09 14:00", "11-06 21:00")
    row("...and Saturday off", "override", "11-07 17:00", "day-off",
        "11-06 14:00", "11-06 20:00", "11-08 15:00", "11-06 21:00")
    row("...and the days it does not name keep the default", "override", "11-04 17:00", "open",
        "11-04 14:00", "11-04 20:00", "11-05 14:00", "11-03 21:00")
    row("a length is rounded to the second: 0.50015 hours is 30 minutes and 1 second", "odd",
        "11-09 14:15", "open", "11-09 14:00", "11-09 14:30:01", "11-10 14:00", "11-07 21:00")
    row("no day has a window", "none", "11-04 17:00", "no-window")
    row("one weekday: the last window a week back, before its start", "only_wed", "11-04 13:00",
        "before-start", "10-28 13:00", "10-28 19:00", "11-04 14:00", "10-28 20:00")
    row("...the next a week on, after its close", "only_wed", "11-04 20:30", "after-close",
        "11-04 14:00", "11-04 20:00", "11-11 14:00", "11-04 21:00")
    # -- daylight saving in New York: 8 March, clocks forward; 1 November, clocks back -----------
    row("spring: Saturday evening to Monday morning is an hour sooner", "ny", "03-07 20:00:01",
        "after-close", "03-07 14:00", "03-07 20:00", "03-09 13:00", "03-07 21:00")
    row("spring: Monday opens at 09:00 EDT", "ny", "03-09 13:00", "open",
        "03-09 13:00", "03-09 19:00", "03-10 13:00", "03-07 21:00")
    row("spring: a window across the change lasts its six real hours", "sun_mid", "03-08 10:30",
        "open", "03-08 05:00", "03-08 11:00", "03-09 13:00", "03-07 21:00")
    row("spring: ...and is over at its real end", "sun_mid", "03-08 11:00", "after-close",
        "03-08 05:00", "03-08 11:00", "03-09 13:00", "03-08 12:00")
    row("spring: a start in the gap is not yet", "sun_gap", "03-08 06:59:59", "before-start",
        "03-07 14:00", "03-07 20:00", "03-08 07:00", "03-07 21:00")
    row("spring: ...it begins at 03:00, the first valid instant after", "sun_gap", "03-08 07:00",
        "open", "03-08 07:00", "03-08 09:00", "03-09 13:00", "03-07 21:00")
    row("fall: Saturday evening to Monday morning is an hour later", "ny", "10-31 19:00:01",
        "after-close", "10-31 13:00", "10-31 19:00", "11-02 14:00", "10-31 20:00")
    row("fall: a window across the change lasts its six real hours", "sun_mid", "11-01 09:59:59",
        "open", "11-01 04:00", "11-01 10:00", "11-02 14:00", "10-31 20:00")
    row("fall: ...and is over at its real end", "sun_mid", "11-01 10:00", "after-close",
        "11-01 04:00", "11-01 10:00", "11-02 14:00", "11-01 11:00")
    row("fall: a start the clocks make twice is its first time", "sun_twice", "11-01 06:30",
        "open", "11-01 05:30", "11-01 07:30", "11-02 14:00", "10-31 20:00")
    dw("spring, a start after the change", "sun_9", datetime.date(2026, 3, 8),
       ("03-08 13:00", "03-08 16:00"))
    dw("fall, a start after the change", "sun_9", datetime.date(2026, 11, 1),
       ("11-01 14:00", "11-01 17:00"))
    dw("spring, a start in the gap", "sun_gap", datetime.date(2026, 3, 8),
       ("03-08 07:00", "03-08 09:00"))
    dw("fall, the same start is after the double hour", "sun_gap", datetime.date(2026, 11, 1),
       ("11-01 07:30", "11-01 09:30"))
    dw("fall, a start in the double hour", "sun_twice", datetime.date(2026, 11, 1),
       ("11-01 05:30", "11-01 07:30"))
    # -- daylight saving in Sydney: 4 October, clocks forward; 5 April, clocks back ---------------
    row("Sydney, clocks forward: Saturday to Monday is an hour sooner", "syd", "10-03 05:00:01",
        "after-close", "10-02 23:00", "10-03 05:00", "10-04 22:00", "10-03 06:00")
    row("Sydney, clocks back: Saturday to Monday is an hour later", "syd", "04-04 04:00:01",
        "after-close", "04-03 22:00", "04-04 04:00", "04-05 23:00", "04-04 05:00")
    dw("Sydney, a window across the change, forward", "syd_mid", datetime.date(2026, 10, 4),
       ("10-03 14:00", "10-03 20:00"))
    dw("Sydney, a window across the change, back", "syd_mid", datetime.date(2026, 4, 5),
       ("04-04 13:00", "04-04 19:00"))
    dw("Sydney, a start in the gap", "syd_gap", datetime.date(2026, 10, 4),
       ("10-03 16:00", "10-03 18:00"))
    dw("Sydney, a start the clocks make twice", "syd_gap", datetime.date(2026, 4, 5),
       ("04-04 15:30", "04-04 17:30"))
    # -- a window that crosses midnight -----------------------------------------------------------
    row("Tuesday at 2am is inside Monday's window", "late_mon", "11-03 07:00", "open",
        "11-03 03:00", "11-03 09:00", "11-03 14:00", "10-31 20:00")
    row("...then it is over, and Tuesday's own has not begun", "late_mon", "11-03 10:00",
        "before-start", "11-03 03:00", "11-03 09:00", "11-03 14:00", "11-03 10:00")
    row("a window into a day off is open on it", "sat_late", "11-08 07:00", "open",
        "11-08 01:00", "11-08 09:00", "11-09 14:00", "11-06 21:00")
    row("...and then it is a day off", "sat_late", "11-08 09:00", "day-off",
        "11-08 01:00", "11-08 09:00", "11-09 14:00", "11-08 10:00")
    dw("a window across midnight ends on the next date", "late_mon", datetime.date(2026, 11, 2),
       ("11-03 03:00", "11-03 09:00"))
    row("two windows closing together: the earlier start is when it opened", "tie", "11-08 10:00",
        "open", "11-08 01:00", "11-08 13:00", "11-09 14:00", "11-06 21:00")
    # -- a 24-hour window -------------------------------------------------------------------------
    row("a 24-hour window is open all day", "all24", "11-04 17:00", "open",
        "11-04 05:00", "11-05 05:00", "11-05 05:00", "11-04 06:00")
    row("...from its first instant, when the day before's has just ended", "all24", "11-04 05:00",
        "open", "11-04 05:00", "11-05 05:00", "11-05 05:00", "11-04 06:00")
    row("...24 real hours: the day the clocks go back it ends an hour early", "all24",
        "11-02 04:30", "after-close", "11-01 04:00", "11-02 04:00", "11-02 05:00", "11-02 05:00")
    row("...and the day they go forward two overlap: the one that closes last", "all24",
        "03-09 04:30", "open", "03-09 04:00", "03-10 04:00", "03-10 04:00", "03-08 06:00")
    # -- the machine's own zone, when a config names none: the selftest pins New York -------------
    row("a config with no zone: New York here, 09:00 EST is open", "local", "11-04 14:00", "open",
        "11-04 14:00", "11-04 20:00", "11-05 14:00", "11-03 21:00")
    row("...and an hour before the start there is not UTC's", "local", "11-04 13:30",
        "before-start", "11-03 14:00", "11-03 20:00", "11-04 14:00", "11-03 21:00")
    row("...the zone is read when asked: a machine in Tokyo, the same config", "local",
        "11-04 14:00", "after-close", "11-04 00:00", "11-04 06:00", "11-05 00:00", "11-04 07:00",
        zone=TOKYO)
    dw("the machine's own zone has the gap rule too", "local_gap", datetime.date(2026, 3, 8),
       ("03-08 07:00", "03-08 09:00"))
    # -- day_window -------------------------------------------------------------------------------
    dw("Monday's window", "ny", datetime.date(2026, 11, 2), ("11-02 14:00", "11-02 20:00"))
    dw("Sunday has none", "ny", datetime.date(2026, 11, 8), None)
    dw("a date as text", "ny", "2026-11-02", ("11-02 14:00", "11-02 20:00"))
    npure = len(SC)                          # all of the above takes a made config and no more
    # -- the config: the defaults, an override, and every fault named -----------------------------
    nan, inf = float("nan"), float("inf")
    kit = dict((d, {"start": "09:00", "hours": 6}) for d in DAYS[:6])
    kit["sun"] = None
    bad = lambda raw: [e.split(":")[0] for e in config(raw)[1]]                  # noqa: E731
    mon = lambda **kw: {"days": {"mon": kw}}                                     # noqa: E731

    def cases(name, values, make, want):
        S(name, [want] * len(values), lambda cfgs: [bad(make(v)) for v in values])
    S("the defaults: Monday to Saturday 09:00 for 6 hours, Sunday off, no zone, 60 minutes grace",
      {"time_zone": None, "days": kit, "grace_minutes": 60}, lambda cfgs: dict(config({})[0]))
    S("a supplied days overrides only the days it names", dict(kit, sun={"start": "10:00",
      "hours": 2}, mon=None), lambda cfgs: config({"days": {"sun": {"start": "10:00", "hours": 2},
                                                           "mon": None}})[0]["days"])
    def untouched(cfgs):
        raw = {"days": {"sun": {"start": "10:00", "hours": 2}}}
        made = config(raw)[0]
        return (raw == {"days": {"sun": {"start": "10:00", "hours": 2}}}, config(made)[1],
                config(made)[0] == made)
    S("a config is left as it was, and is idempotent", (True, [], True), untouched)

    def shares(cfgs):
        raw = {"days": {"sun": {"start": "10:00", "hours": 2}}}
        made = config(raw)[0]
        made["days"]["mon"]["hours"] = 1
        made["days"]["sun"]["hours"] = 1
        return (config({})[0]["days"]["mon"]["hours"], raw["days"]["sun"]["hours"])
    S("what config makes shares nothing with the defaults or with its input", (6, 2), shares)
    S("a config that is not an object", [["the config is not an object"]] * 3,
      lambda cfgs: [bad(v) for v in ([], "x", None)])
    S("an unknown setting is named, whichever", [["'zone'"], ["'hours'"]],
      lambda cfgs: [bad({"zone": 1}), bad({"hours": 6})])
    cases("a time zone that is not one", [5, True, "", "Mars/Base", "America/New York",
                                          "../etc/passwd"], lambda v: {"time_zone": v},
          ["time_zone"])
    cases("days that are not an object", [[], "mon", None], lambda v: {"days": v}, ["days"])
    S("a weekday that is not one, named", [["days.'monday'"], ["days.'MON'"]],
      lambda cfgs: [bad({"days": {"monday": None}}), bad({"days": {"MON": None}})])
    cases("a day that is neither null nor an object", [5, "09:00", [], False],
          lambda v: {"days": {"mon": v}}, ["days.mon"])
    S("a day missing its start or its length", [["days.mon.start", "days.mon.hours"],
                                               ["days.mon.hours"], ["days.mon.start"]],
      lambda cfgs: [bad(mon()), bad(mon(start="09:00")), bad(mon(hours=6))])
    S("a day with a setting that is not one", [["days.mon.'x'"]],
      lambda cfgs: [bad(mon(start="09:00", hours=6, x=1))])
    cases("a start that is not HH:MM", ["9:00", "24:00", "09:60", 900, "09:00:00", "", None, "9am"],
          lambda v: mon(start=v, hours=6), ["days.mon.start"])
    cases("a length not above 0 and up to 24", [0, -1, 24.5, 25, "6", True, nan, inf, None, 0.0001],
          lambda v: mon(start="09:00", hours=v), ["days.mon.hours"])
    cases("a grace that is not a whole number of minutes, 0 or more", [-1, 1.5, "60", True, None],
          lambda v: {"grace_minutes": v}, ["grace_minutes"])
    S("every fault at once, each named, in order",
      ["'zone'", "time_zone", "days.mon.start", "days.mon.hours", "days.sat", "grace_minutes"],
      lambda cfgs: bad({"zone": 1, "time_zone": "Mars", "days": {
          "mon": {"start": "x", "hours": 0}, "sat": 7}, "grace_minutes": -5}))
    good = [{}, {"time_zone": None}, {"time_zone": "UTC"}, {"days": {}}, {"days": {"mon": None}},
            mon(start="00:00", hours=24), mon(start="23:59", hours=0.5), {"grace_minutes": 0},
            {"days": dict.fromkeys(DAYS)}, {"time_zone": NY, "grace_minutes": 1440}]
    S("configs that are fine, among them 24 hours, half an hour and none at all",
      [[]] * len(good), lambda cfgs: [bad(v) for v in good])

    def battery():
        cfgs = dict((k, config(v)[0]) for k, v in RAW.items())     # config() is what loads a zone
        return [thunk(cfgs) for _n, _w, thunk in SC]
    got = battery()
    for (name, want, _t), g in zip(SC, got):
        check(name, g, want)
    # -- the shape of what comes out --------------------------------------------------------------
    ny = config(RAW["ny"])[0]
    states = [g for g in got if isinstance(g, tuple) and len(g) == 6 and isinstance(g[0], bool)]
    check("every state has a bool, a why from the closed list, and times as UTC or None",
          len(states) > 30 and all(g[1] in WHYS and all(t is None or re.match(
              r"^[0-9]{4}(-[0-9]{2}){2}T([0-9]{2}:){2}[0-9]{2}Z\Z", t) for t in g[2:])
              for g in states), True)
    check("every why is reached", sorted(set(g[1] for g in states)), sorted(WHYS))
    check("a state has exactly the six keys", sorted(window_at(ny, T("11-04 14:00"))),
          ["closes_at", "grace_ends_at", "next_opens_at", "open", "opened_at", "why"])
    check("a time in fractions of a second is read", window_at(ny, T("11-04 14:00") + 0.5)["why"],
          "open")

    def refuses(fn, *args):
        try:
            fn(*args)
        except ValueError as exc:
            return str(exc)
        return None
    check("windows left: the open one counts, one beginning at the end does not, a gone one never",
          [windows_left(ny, T("11-04 14:00"), T("11-06 00:00")),
           windows_left(ny, T("11-04 14:00"), T("11-09 14:00")),
           windows_left(ny, T("11-04 21:00"), T("11-05 20:00")),
           windows_left(ny, T("11-07 21:00"), T("11-08 14:00")),
           windows_left(ny, T("11-04 14:00"), T("11-04 14:00"))], [2, 4, 1, 0, 0])
    check("windows left: days off are skipped, and a time that is not seconds is refused",
          (windows_left(ny, T("11-07 15:00"), T("11-10 00:00")),
           bool(refuses(windows_left, ny, "x", 1))), (2, True))
    check("a time that is not seconds since 1970 is refused",
          [bool(refuses(window_at, ny, v)) for v in (True, "11-04", None, nan, inf)], [True] * 5)
    check("a date that is not one is refused, a time of day too",
          [bool(refuses(day_window, ny, v)) for v in (datetime.datetime(2026, 11, 2, 9),
                                                      "2026-13-45", "11/02/2026", 20261102, None)],
          [True] * 5)
    check("settings that were not made by config are read through it",
          window_at(RAW["ny"], T("11-04 14:00")), window_at(ny, T("11-04 14:00")))
    check("...and refused, naming the fault, when they will not do",
          "'zone'" in (refuses(window_at, {"zone": 1}, T("11-04 14:00")) or ""), True)
    check("a refused config is None, never half a config", config({"zone": 1})[0], None)
    check("the error says what is allowed",
          config({"zone": 1})[1], ["'zone': not a setting (time_zone, days, grace_minutes)"])
    check("epoch reads Z and an offset, and refuses a time with no zone",
          [epoch("2026-11-04T14:00:00Z"), epoch("2026-11-04T09:00-05:00"),
           epoch("2026-11-04 19:30:00+0530"), epoch("2026-11-04T14:00:00"), epoch("x"),
           epoch("2026-13-04T14:00:00Z"), epoch("2026-11-04T14:00:00+24:00"), epoch(5)],
          [T("11-04 14:00")] * 3 + [None] * 5)
    check("the module and instant() both say what a start in a gap does",
          [("first valid instant after" in d) for d in (__doc__, instant.__doc__)], [True] * 2)
    # -- every quarter-hour start, on each date around each change, against the standard library
    spans = {NY: (datetime.date(2026, 3, 1), datetime.date(2026, 10, 27)),
             SYD: (datetime.date(2026, 3, 28), datetime.date(2026, 9, 27))}

    def sweep(named, reference):
        """day_window under a config naming `named` (None: the machine's zone), against the
        standard library's conversion in the zone `reference`; the faults, if any."""
        z, wrong = zoneinfo.ZoneInfo(reference), []
        for hour in range(24):
            for minute in (0, 15, 30, 45):
                start = "%02d:%02d" % (hour, minute)
                cfg = config({"time_zone": named, "days": days(**dict.fromkeys(
                    DAYS, (start, 5)))})[0]
                for first in spans[reference]:
                    for n in range(14):
                        date = first + datetime.timedelta(days=n)
                        naive = datetime.datetime(date.year, date.month, date.day, hour, minute)
                        want = naive.replace(tzinfo=z).timestamp()
                        if datetime.datetime.fromtimestamp(want, z).replace(tzinfo=None) != naive:
                            want = datetime.datetime(date.year, date.month, date.day, 3,
                                                     tzinfo=z).timestamp()    # skipped: 03:00
                        if day_window(cfg, date) != (want, want + 5 * 3600):
                            wrong.append((str(date), start))
        return wrong
    check("New York: every quarter-hour start around both changes is the standard library's",
          sweep(NY, NY), [])
    check("Sydney: the same, around both of its changes", sweep(SYD, SYD), [])
    check("the machine's zone, here New York: the same, through time.localtime",
          sweep(None, NY), [])

    # -- pure: the source holds no file, environment, socket, process or clock call ---------------
    banned = set(["open", "os", "socket", "subprocess", "urllib", "http", "shutil", "pathlib",
                  "tempfile", "glob", "input"])
    banned_attr = set(["now", "utcnow", "today", "environ", "getenv", "time_ns", "monotonic",
                       "perf_counter", "process_time"])

    def touches(node):
        found = set()
        for n in ast.walk(node):
            if isinstance(n, ast.Name) and n.id in banned:
                found.add(n.id)
            elif isinstance(n, ast.Attribute):
                if n.attr in banned_attr:
                    found.add(n.attr)
                if n.attr == "time" and isinstance(n.value, ast.Name) and n.value.id == "time":
                    found.add("time.time")
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                names = [n.module or ""] if isinstance(n, ast.ImportFrom) else [
                    a.name for a in n.names]
                found.update("import " + x for x in (y.split(".")[0] for y in names) if x in banned)
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and not n.args
                    and n.func.attr in ("localtime", "gmtime", "ctime", "asctime")):
                found.add(n.func.attr + "()")
        return found

    def scan(source):
        """Function by function, what of the above it touches; the selftest itself is exempt."""
        seen = {}
        for node in ast.parse(source).body:
            if isinstance(node, ast.FunctionDef) and node.name in ("selftest", "_cases"):
                continue
            seen.setdefault(node.name if isinstance(node, ast.FunctionDef) else "<module>",
                            set()).update(touches(node))
        return dict((k, sorted(v)) for k, v in seen.items() if v)
    check("only the command line opens a file or asks the clock: nothing else in the module does",
          scan(inspect.getsource(sys.modules[__name__])),
          {"clock": ["time.time"], "read_file": ["open"]})
    sample = ("import os, socket, subprocess, time, datetime\n"
              "def f():\n    open('x'); os.environ['A']; os.getenv('A'); socket.socket()\n"
              "    subprocess.run(['x']); time.time(); time.localtime(); time.monotonic()\n"
              "    datetime.datetime.now(); datetime.date.today(); time.gmtime()\n"
              "def g():\n    return time.localtime(0).tm_gmtoff\n")
    check("the scan is not blind: each kind of touch planted in a sample is found, an epoch is not",
          scan(sample), {"<module>": ["import os", "import socket", "import subprocess"],
                         "f": ["environ", "getenv", "gmtime()", "localtime()", "monotonic", "now",
                               "open", "os", "socket", "subprocess", "time.time", "today"]})

    class Watch(object):
        """Counts every file, process, environment, socket and clock touch while it is open."""
        def __enter__(self):
            self.hits, self.saved = [], []
            for owner, name in ((builtins, "open"), (os, "stat"), (os, "lstat"), (os, "listdir"),
                                (os, "scandir"), (os, "getenv"), (time, "time"),
                                (time, "localtime"), (time, "gmtime"), (socket.socket, "connect"),
                                (subprocess.Popen, "__init__")):
                real = getattr(owner, name)
                self.saved.append((owner, name, owner.__dict__.get(name, self), real))

                def seen(*a, _n=name, _r=real, **k):
                    # localtime(instant) asks a zone about an instant; localtime() asks the clock
                    if _n not in ("localtime", "gmtime") or not a or a[0] is None:
                        self.hits.append(_n)
                    return _r(*a, **k)
                setattr(owner, name, seen)
            return self

        def __exit__(self, *exc):
            for owner, name, mine, real in self.saved:
                if mine is self:
                    delattr(owner, name)
                else:
                    setattr(owner, name, real)
    with Watch() as w:
        open(os.devnull).close()
        os.stat(os.devnull)
        os.getenv("TZ")
        time.time()
        time.localtime()
        sock = socket.socket()
        try:
            sock.connect("not an address")
        except (TypeError, OSError):
            pass
        sock.close()
        try:
            subprocess.Popen(["/nonexistent/program"])
        except OSError:
            pass
    check("the watch is not blind: it sees each kind of touch",
          set(["open", "stat", "getenv", "time", "localtime", "connect", "__init__"]) <= set(
              w.hits), True)
    cfgs = dict((k, config(v)[0]) for k, v in RAW.items())
    with Watch() as w:
        for _n, _w, thunk in SC[:npure]:
            thunk(cfgs)
    check("a whole pass over a made config opens no file, process, socket or environment variable"
          " and asks no clock", w.hits, [])

    # -- the command line -------------------------------------------------------------------------
    def cli(argv, at=None):
        out, err, real = io.StringIO(), io.StringIO(), globals()["clock"]
        if at is not None:
            globals()["clock"] = lambda: at
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(argv)
        finally:
            globals()["clock"] = real
        return code, out.getvalue(), err.getvalue()
    state = window_at({}, T("11-04 14:00"))
    code, out, err = cli(["now", "--at", Z("11-04 14:00")])
    check("now --at prints the state as JSON, in the machine's zone for the default",
          (code, json.loads(out), err), (0, state, ""))
    check("--at reads an offset: 09:00 in New York",
          json.loads(cli(["now", "--at", "2026-11-04T09:00:00-05:00"])[1]), state)
    check("without --at it asks the clock, here stood in for",
          json.loads(cli(["now"], at=T("11-04 14:00"))[1]), state)
    check("a time with no zone is refused as a usage error, naming --at",
          [(c, "--at" in e) for c, _o, e in (cli(["now", "--at", "2026-11-04T14:00:00"]),
                                             cli(["now", "--at", "soon"]))], [(2, True)] * 2)
    check("no command is a usage error", cli([])[0], EXIT_USAGE)
    with tempfile.TemporaryDirectory() as tmp:
        def write(text):
            path = os.path.join(tmp, "window.json")
            with open(path, "w") as handle:
                handle.write(text)
            return path
        code, out, err = cli(["now", "--at", Z("11-04 14:00"),
                              "--config", write('{"time_zone": "Australia/Sydney"}')])
        check("--config reads a JSON file", (code, json.loads(out), err),
              (0, window_at(config({"time_zone": SYD})[0], T("11-04 14:00")), ""))
        check("a config with an unknown key is refused, the key named",
              [(c, "'zone'" in e) for c, _o, e in [cli(["now", "--config", write('{"zone": 1}')])]],
              [(1, True)])
        check("a missing file, and text that is not JSON, are refused",
              [cli(["now", "--config", p])[0] for p in (os.path.join(tmp, "none.json"),
                                                         write("not json"))], [1, 1])

    # -- mutants: a check that cannot fail checks nothing -----------------------------------------
    o = dict((k, globals()[k]) for k in ("instant", "window_end", "check_days", "check_day",
                                         "zone_offset", "machine_offset", "closed_why"))

    def caught(**patched):
        saved = dict((k, globals()[k]) for k in patched)
        globals().update(patched)
        try:
            return battery() != got
        except Exception:                # a mutant the scenarios cannot even read is caught too
            return True
        finally:
            globals().update(saved)

    def reads(off, wall):
        return sorted(e for e in set((wall - off(wall - DAY), wall - off(wall + DAY)))
                      if e + off(e) == wall)

    def lenient(name):
        try:
            return o["zone_offset"](name)
        except ValueError:
            return o["machine_offset"]

    def dflt(**kw):
        return dict(DEFAULTS, days=dict((d, kw[d] if d in kw else v) for d, v in
                                        DEFAULTS["days"].items()))

    def every(**kw):
        return dict(DEFAULTS, days=dict((d, v and dict(v, **kw)) for d, v in
                                        DEFAULTS["days"].items()))

    def replacing(days):
        out, why = o["check_days"](days)
        return dict((d, v if d in days else None) for d, v in out.items()) if out else out, why
    for name, patch in (
            ("Sunday on by default", dict(DEFAULTS=dflt(sun={"start": "09:00", "hours": 6}))),
            ("Saturday off by default", dict(DEFAULTS=dflt(sat=None))),
            ("the default start at 10:00", dict(DEFAULTS=every(start="10:00"))),
            ("the default length of 5 hours", dict(DEFAULTS=every(hours=5))),
            ("the default grace of 30 minutes", dict(DEFAULTS=dict(DEFAULTS, grace_minutes=30))),
            ("a supplied days replacing every day", dict(check_days=replacing)),
            ("a supplied day ignored", dict(check_days=lambda days: o["check_days"]({}))),
            ("weekdays one off", dict(day_name=lambda d: DAYS[(d.weekday() + 1) % 7])),
            ("the start not in the window", dict(inside=lambda w, now: w[0] < now < w[1])),
            ("the close in the window", dict(inside=lambda w, now: w[0] <= now <= w[1])),
            ("a window starting now taken for the next", dict(
                opens_after=lambda w, now: w[0] >= now)),
            ("a window's length in minutes", dict(
                window_end=lambda off, wall, start, hours: start + int(round(hours * 60)))),
            ("a window ending by the wall clock", dict(
                window_end=lambda off, wall, start, hours: o["instant"](
                    off, wall + seconds(hours)))),
            ("a window cut off at midnight", dict(
                window_end=lambda off, wall, start, hours: min(o["window_end"](
                    off, wall, start, hours), o["instant"](off, (wall // DAY + 1) * DAY)))),
            ("the next opening no further than tomorrow", dict(LOOK_AHEAD=1)),
            ("no look back to the last window", dict(LOOK_BACK=0)),
            ("overlapping windows: the first to start, not the last to close", dict(
                last_to_close=lambda ws: min(ws, key=lambda w: w[0]) if ws else None)),
            ("windows closing together: the later start", dict(
                last_to_close=lambda ws: max(ws, key=lambda w: (w[1], w[0])) if ws else None)),
            ("no grace", dict(plus_grace=lambda close, minutes: close)),
            ("grace in hours", dict(plus_grace=lambda close, minutes: close + minutes * 3600)),
            ("a day off said as after the close", dict(closed_why=lambda today, now: (
                "after-close" if today is None else o["closed_why"](today, now)))),
            ("before the start never said", dict(closed_why=lambda today, now: (
                "day-off" if today is None else "after-close"))),
            ("after the close never said", dict(closed_why=lambda today, now: (
                "day-off" if today is None else "before-start"))),
            ("no window not told from a day off", dict(no_windows=lambda cfg: False)),
            ("the zone's January offset all year", dict(
                instant=lambda off, wall: wall - off(T("01-15 12:00")))),
            ("the offset read at the start of the day", dict(
                instant=lambda off, wall: wall - off(wall - wall % DAY))),
            ("a start the clocks make twice taken at its second", dict(
                instant=lambda off, wall: (reads(off, wall) or [o["instant"](off, wall)])[-1])),
            ("a start in the gap moved on by the gap", dict(
                instant=lambda off, wall: (reads(off, wall) or [wall - off(wall - DAY)])[0])),
            ("a start in the gap taken as just before it", dict(
                instant=lambda off, wall: o["instant"](off, wall) - (not reads(off, wall)))),
            ("every zone New York's", dict(zone_offset=lambda name: o["machine_offset"] if name is
                                           None else o["zone_offset"](NY))),
            ("a named zone ignored", dict(zone_offset=lambda name: o["machine_offset"])),
            ("a zone it cannot load accepted", dict(zone_offset=lenient)),
            ("the machine's zone taken as UTC", dict(machine_offset=lambda seconds: 0)),
            ("unknown settings accepted", dict(unknown=lambda obj, allowed, where, what: [])),
            ("a length of 0 accepted", dict(valid_hours=lambda h: number(h) and 0 <= h <= 24)),
            ("a length over 24 accepted", dict(valid_hours=lambda h: number(h) and 0 < h <= 48)),
            ("a length of 24 refused", dict(valid_hours=lambda h: number(h) and 0 < h < 24)),
            ("a flag taken for a length", dict(
                valid_hours=lambda h: isinstance(h, (int, float)) and 0 < h <= 24)),
            ("a start of 24:00 accepted", dict(valid_start=lambda s: isinstance(s, str) and bool(
                re.match(r"^([01][0-9]|2[0-4]):[0-5][0-9]\Z", s)))),
            ("a negative grace accepted", dict(valid_grace=lambda g: whole(g))),
            ("a fractional grace accepted", dict(valid_grace=lambda g: number(g) and g >= 0)),
            ("a missing start or length accepted", dict(check_day=lambda name, spec: [
                e for e in o["check_day"](name, spec) if not e.endswith(": missing")]))):
        check("mutant: %s is caught" % name, caught(**patch), True)
    check("...and with every mutant undone the module is as it was", battery() == got, True)


if __name__ == "__main__":
    sys.exit(main())
