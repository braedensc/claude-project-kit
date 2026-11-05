#!/usr/bin/env python3
"""The queue's starter: what may start now, and the ledger row written before each start (KIT-452,
MVP 2 part 3a; docs/adr/2026-11-03-autonomous-workers-within-a-budget-and-a-window.md, decisions
2, 4, 5 and 9).

    python3 scripts/pipeline_queue_start.py preview --config <queue.json>
    python3 scripts/pipeline_queue_start.py --selftest

Through the day the queue starts the next ready piece whenever a lane and the budget have room.
The bridge does the starting (part 3b): it holds the tracker key and delegates. This file is what
it asks first, and what it writes before each start.

  decide(raw_config, gql, now)   What may start now, as the order says it. The working window is
                                 asked first: closed, nothing is read and nothing starts. Then
                                 the room (KIT-457, below): short, nothing is read and nothing
                                 starts. Open and with room,
                                 the queue's reader gathers (scripts/pipeline_queue_read.py,
                                 through `gql`), then the pool judges and the order decides, as
                                 merged. A read that failed starts nothing, and says why.
  record_start(path, now, ticket, source)
                                 The ledger row, written BEFORE a start: today's starts plus one,
                                 agent-filed starts plus one for that source, and a row naming the
                                 ticket, its source and the time. The reader counts the day from
                                 this file, so a start it never recorded would let the day's caps
                                 drift. A row for a start that then failed only holds one more
                                 back: the safe side.
  attribution(entry)             The comment a start leaves on its ticket: a fixed sentence for
                                 each source, filled with ids, a lane and a time, never text.
  transport(fn, errors)          A tracker client's query function as the reader's transport:
                                 the bridge's (part 3b) reads through its own client.

THE ROOM (KIT-457).  On 4 Nov the owner's Mac froze: the disk filled while memory was tight. The
queue starts sessions with nobody watching, so it backs off before the machine runs short. Before
any start, and before the tracker is read, three system-wide numbers are read through ONE
injectable reader (`read_room`; fixed commands, a short timeout each, nothing private): memory
pressure (`sysctl -n kern.memorystatus_vm_pressure_level`: 1 normal, 2 warn, 4 critical), free
space on the data volume (`os.statvfs`), and swap in use (`sysctl -n vm.swapusage`). A line over
its limit, or a number that cannot be read, is state "no-room": nothing starts, and `why` says
which line and what it read. A reading that cannot be taken is never taken as fine. Running work is
never stopped; only new starts wait. The limits are the config's optional `room` section:
`min_free_disk_gb` (default 25), `max_pressure` ("warn" or "critical", default "critical": the
level at which starts stop) and `max_swap_gb` (default 4).

ONLY THE ORDER'S `start` LIST STARTS.  What the reader found of a ticket is merged into its verdict
by the morning job's seven EXTRAS keys and no others, so nothing the reader wrote can admit a
ticket: `admitted` comes from the pool alone.

THE LEDGER.  One JSON object of today, `{"date", "starts", "agent_filed_starts", "rows"}`. A file
dated before today is a new day at zero, and a missing one means no start yet, as the reader reads
it. An unreadable one, one dated after today, or one whose counts are not whole numbers refuses:
nothing starts until a person looks. It is written as the reader writes its file: whole or not at
all, mode 0600, in a folder outside every git working tree, where a session could write. ONE
WRITER AT A TIME: it takes no lock, so its one caller, the bridge's pass, starts one ticket after
another; two callers at once could both count the same start.

THE CONFIG.  The queue's one file (see scripts/pipeline_queue_read.py): the reader checks `pool`,
`order` and `reader`, `window` is pipeline_window's, and the usage file is the one the `morning`
section names. The ledger is the reader's `reader.ledger_file`, so both read the same file.

WHERE IT CANNOT HELP.  It starts nothing itself: the bridge's delegation is part 3b. Lanes keep no
footprint between passes yet, so a repository runs one lane unless its merge gate is ready: the
safe side. The preview reads, prints counts, ids and words, and writes nothing.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    import pipeline_queue_order as order
    import pipeline_queue_pool as pool
    import pipeline_queue_read as reader
    import pipeline_status_local as status_job
    import pipeline_window as window
except ImportError as exc:  # a packaging accident, never a state to run in
    sys.exit("FAIL: %s. The starter runs those files' own code and keeps no copy." % exc)

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_PARTIAL = 0, 1, 2, 3
# What the reader found of a ticket that the order reads: the morning job's own list
# (scripts/pipeline_morning.py), pinned equal by the selftest.
EXTRAS = ("repo", "priority", "plan_priority", "effort", "blockers", "title", "pointers")
SOURCES = ("todo", "backlog", "plan", "agent_filed")
LEDGER_SOURCES = SOURCES + ("resume",)      # a piece the plan's limit stopped, handed over once more
RESUME_LAPSE = 2 * 86400                    # a reset older than this is said, not resumed
USAGE_FRESH = 3600                          # the usage file is rewritten every 15 minutes
WINDOWS = ("five_hour", "seven_day", "other")
DEFAULT_USAGE = "~/.stage-e/usage/usage.json"      # the morning job's own default
TICKET_RE = re.compile(r"^[A-Z][A-Z0-9]{0,15}-[0-9]{1,9}\Z")
LANE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}:[0-9]{1,3}\Z")
TIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}):\d{2}(?:\.\d{1,9})?Z\Z")
WHY = {"todo": "you moved it into the ready state{at}",
       "backlog": "you filed it in the backlog, and the backlog may start",
       "plan": "it is a piece of {plan}, which you approved{at}",
       "agent_filed": "it is an agent-filed finding small enough to start by itself"}
# The room (KIT-457): the settings' defaults, the pressure words, and the fixed commands.
ROOM_DEFAULTS = {"min_free_disk_gb": 25, "max_pressure": "critical", "max_swap_gb": 4}
PRESSURE_LEVEL = {"warn": 2, "critical": 4}         # kern.memorystatus_vm_pressure_level
PRESSURE_WORD = {1: "normal", 2: "warn", 4: "critical"}
SYSCTL = "/usr/sbin/sysctl"
ROOM_TIMEOUT = 5                                    # seconds, for each command
DATA_VOLUME = "/System/Volumes/Data"
GIB = 1024 ** 3
SWAP_RE = re.compile(r"\bused = ([0-9]+(?:\.[0-9]+)?)([KMGT])\b")
SWAP_UNIT = {"K": 1 / 1048576, "M": 1 / 1024, "G": 1, "T": 1024}
write = reader.write        # whole or not at all, mode 0600, never inside a git working tree


# --------------------------------------------------------------------------- #
# The config
# --------------------------------------------------------------------------- #
def load(raw):
    """(cfg, problems): the reader's sections as it checks them, the window, and the usage file
    the morning section names; every problem at once. cfg is None while there is any."""
    if not isinstance(raw, dict):
        return None, ["config: not an object"]
    cfg, bad = reader.read_config(raw)
    wcfg, wbad = (window.config(raw.get("window", {})) if isinstance(raw.get("window", {}), dict)
                  else (None, ["not an object"]))
    bad = list(bad) + ["window: %s" % w for w in wbad]
    morning = raw.get("morning", {})
    usage = None
    try:
        usage = reader.resolve_path(morning.get("usage_file", DEFAULT_USAGE) if isinstance(
            morning, dict) else None, "morning.usage_file")
    except ValueError as exc:
        bad.append(str(exc))
    rcfg, rbad = room_config(raw.get("room", {}))
    bad += ["room: %s" % w for w in rbad]
    return (None, bad) if bad else (dict(cfg, window=wcfg, usage_file=usage, room=rcfg), [])


def room_config(raw):
    """(settings, problems): the room's three limits with the defaults under what `raw` leaves
    out, and everything wrong with `raw`, each fault named by its key."""
    if not isinstance(raw, dict):
        return None, ["not an object"]
    bad = ["%s: not a setting (%s)" % (repr(k)[:40], ", ".join(ROOM_DEFAULTS))
           for k in sorted(raw, key=repr) if k not in ROOM_DEFAULTS]
    cfg = dict(ROOM_DEFAULTS, **dict((k, v) for k, v in raw.items() if k in ROOM_DEFAULTS))
    for key in ("min_free_disk_gb", "max_swap_gb"):
        v = cfg[key]
        if not (isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 100000):
            bad.append("%s: must be a number of gigabytes, 0 or more" % key)
    if cfg["max_pressure"] not in PRESSURE_LEVEL:
        bad.append("max_pressure: must be \"warn\" or \"critical\"")
    return (None, bad) if bad else (cfg, [])


# --------------------------------------------------------------------------- #
# The room: three numbers about the machine, and the lines they are held to
# --------------------------------------------------------------------------- #
def run_command(argv):
    """A fixed command's output, or None: it failed, was not there, or took too long."""
    try:
        done = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=ROOM_TIMEOUT, check=False, universal_newlines=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def read_room(run=None, statvfs=None, volume_exists=None):
    """{"pressure", "free_disk_gb", "swap_gb"}: each a number, or None when it could not be read.
    The one place the machine is asked; every argument is for the selftest."""
    run = run or run_command
    statvfs = statvfs or os.statvfs
    exists = volume_exists or os.path.isdir
    out = {"pressure": None, "free_disk_gb": None, "swap_gb": None}
    text = run([SYSCTL, "-n", "kern.memorystatus_vm_pressure_level"])
    if text is not None and text.strip().isdigit():
        out["pressure"] = int(text.strip())
    try:
        st = statvfs(DATA_VOLUME if exists(DATA_VOLUME) else "/")
        out["free_disk_gb"] = st.f_bavail * st.f_frsize / GIB
    except (OSError, AttributeError, TypeError):
        pass
    found = SWAP_RE.search(run([SYSCTL, "-n", "vm.swapusage"]) or "")
    if found:
        out["swap_gb"] = float(found.group(1)) * SWAP_UNIT[found.group(2)]
    return out


REAL_READ_ROOM = read_room      # the selftest stands read_room in; its own cases need this one


def room_short(cfg, got):
    """The lines the machine is over, as words with their readings: [] when there is room. A
    number that is None or not a number is a line over, said as unread."""
    def num(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    lines = []
    p, hold = got.get("pressure"), PRESSURE_LEVEL[cfg["max_pressure"]]
    if not num(p):
        lines.append(("pressure", "memory pressure could not be read"))
    elif p >= hold:
        lines.append(("pressure", "memory pressure is %s, at the %s line" % (
            PRESSURE_WORD.get(p, "level %d" % p), cfg["max_pressure"])))
    d = got.get("free_disk_gb")
    if not num(d):
        lines.append(("disk", "free disk could not be read"))
    elif d < cfg["min_free_disk_gb"]:
        lines.append(("disk", "free disk %.1f GB is under the %g GB line" % (
            d, cfg["min_free_disk_gb"])))
    w = got.get("swap_gb")
    if not num(w):
        lines.append(("swap", "swap in use could not be read"))
    elif w > cfg["max_swap_gb"]:
        lines.append(("swap", "swap in use %.1f GB is over the %g GB line" % (
            w, cfg["max_swap_gb"])))
    return lines


# --------------------------------------------------------------------------- #
# What may start now
# --------------------------------------------------------------------------- #
def with_extras(verdicts, extras):
    """Each verdict with what the reader found of its ticket, by EXTRAS only."""
    out = []
    for v in verdicts:
        found = extras.get(v.get("ticket")) if isinstance(extras, dict) else None
        out.append(dict(v, **dict((k, found[k]) for k in EXTRAS if k in found))
                   if isinstance(found, dict) else dict(v))
    return out


def read_failed(inputs):
    """True when the reader's pass could not read the tracker: nothing may start from it."""
    return inputs["result"] == "failed"


def transport(fn, errors):
    """`fn(query, variables) -> data` as the reader's transport: an error of the kinds `errors`
    names becomes a SourceError, which the reader takes as the tracker being down for the pass."""
    def gql(query, variables):
        try:
            return fn(query, variables)
        except errors as exc:
            raise status_job.SourceError(str(exc))
    return gql


def read_usage(path):
    """The usage file, or None: the order stops on one it cannot read, and says so."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError, RecursionError):
        return None


def decide(raw_config, gql, now, clock=time.time, say=None, room=None):
    """{"state", "why", "window", "inputs", "decision"}. `state` is "closed" (`why` is the
    window's word), "no-room" (`why` names each line the machine is over, or could not be read
    for, and `lines` holds their names: "pressure", "disk", "swap"), "unread" (the reader's pass failed; `why` is its reason) or "decided"
    (`decision` is the order's; `why` is "partial" when a source was read in part, else None).
    Raises ValueError for a config that is not usable."""
    cfg, bad = load(raw_config)
    if bad:
        raise ValueError("the queue's config is not usable: " + "; ".join(bad))
    win = window.window_at(cfg["window"], now)
    out = {"state": "closed", "why": win["why"], "window": win, "inputs": None, "decision": None}
    if not win["open"]:
        return out
    short = room_short(cfg["room"], (room or read_room)())
    if short:
        return dict(out, state="no-room", why="; ".join(w for _, w in short),
                    lines=[n for n, _ in short])
    inputs = out["inputs"] = reader.gather(raw_config, gql, now, clock, say)
    if read_failed(inputs):
        return dict(out, state="unread", why=inputs["sources"]["tracker"]["reason"]
                    or "the tracker was not read")
    verdicts = pool.decide(cfg["pool"], now, inputs["tickets"], inputs["epics"],
                           inputs["histories"], inputs["busy"], inputs["findings"],
                           inputs["plans"], inputs["ledger"])
    usage = read_usage(cfg["usage_file"])
    paced, why_paced = order.pace(cfg["order"], usage, now, lambda until: window.windows_left(
        cfg["window"], now, until))
    for line in why_paced:
        (say or (lambda _line: None))("queue: " + line)
    decision = order.order(paced, now, with_extras(verdicts, inputs["extras"]),
                           inputs["running"], usage, inputs["ledger"])
    return dict(out, state="decided", decision=decision,
                why="partial" if inputs["result"] == "partial" else None)


# --------------------------------------------------------------------------- #
# The ledger, written before each start
# --------------------------------------------------------------------------- #
def read_ledger(path, today):
    """Today's ledger as written, or a new day's at zero. Raises ValueError for one that cannot
    be counted on, OSError for a file that cannot be read."""
    zero = {"date": today, "starts": 0, "agent_filed_starts": 0, "rows": []}
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return zero
    except (ValueError, RecursionError):
        raise ValueError("the ledger is not JSON")
    rows = doc.get("rows", []) if isinstance(doc, dict) else None
    if not (isinstance(doc, dict) and reader.is_day(doc.get("date"))
            and reader.whole_count(doc.get("starts"))
            and reader.whole_count(doc.get("agent_filed_starts"))
            and isinstance(rows, list) and all(isinstance(r, dict) for r in rows)):
        raise ValueError("the ledger is not a ledger of starts")
    if doc["date"] > today:
        raise ValueError("the ledger is dated after today")
    if doc["date"] < today:
        return zero
    return {"date": today, "starts": doc["starts"],
            "agent_filed_starts": doc["agent_filed_starts"], "rows": list(rows)}


def counted(led, ticket, source, now):
    """The ledger with one more start of `source`."""
    return dict(led, starts=led["starts"] + 1,
                agent_filed_starts=led["agent_filed_starts"] + (source == "agent_filed"),
                rows=led["rows"] + [{"ticket": ticket, "source": source, "at": pool.iso(now)}])


def record_start(path, now, ticket, source):
    """Write today's row for a start about to be made, and return the ledger as written. Raises
    ValueError (a ledger that cannot be counted on, a ticket or source of the wrong shape, a
    folder inside a git working tree) or OSError (the disk would not take it): either way,
    nothing may start."""
    if not (isinstance(ticket, str) and TICKET_RE.match(ticket)) or source not in LEDGER_SOURCES:
        raise ValueError("a start names a ticket id and one of %s" % ", ".join(LEDGER_SOURCES))
    led = counted(read_ledger(path, pool.local_date(now)), ticket, source, now)
    write(led, path)
    return led


# --------------------------------------------------------------------------- #
# The comment a start leaves
# --------------------------------------------------------------------------- #
def attribution(entry):
    """Fixed words for the start's source, with ids, its lane and a time; never ticket text.
    Raises ValueError for an entry that is not one of the order's starts."""
    ticket, source, lane = (entry.get(k) for k in ("ticket", "source", "lane"))
    if not (isinstance(ticket, str) and TICKET_RE.match(ticket) and source in SOURCES
            and isinstance(lane, str) and LANE_RE.match(lane)):
        raise ValueError("not a start the order made")
    at = TIME_RE.match(entry["at"]) if isinstance(entry.get("at"), str) else None
    plan = entry.get("plan")
    return "Started by the queue, in lane %s: %s." % (lane, WHY[source].format(
        at=" at %s %s UTC" % at.groups() if at else "",
        plan=plan if isinstance(plan, str) and TICKET_RE.match(plan) else "a plan"))


# --------------------------------------------------------------------------- #
# A piece the plan's limit stopped (KIT-472)
# --------------------------------------------------------------------------- #
def resumable(usage, now, done):
    """{"resume", "waiting", "said"} from the usage file's `limit_stops` (contract §17):
    "resume" lists each coding piece whose window has reset, to be handed over once more;
    "waiting" the tickets whose window has not; "said" what cannot be placed, each
    {"ticket", "why", "text"} in fixed words: a usage file it cannot trust (ticket None), a
    time it cannot read, a reset long gone, a piece stopped a second time. `done` is what was
    handled already, {ticket: {"ended_at", "result", "again"?}}: a stop no newer than the one
    handled is not met again, so each stop resumes once. Nothing in a stop but its closed words
    and times reaches a line, and nothing is resumed on a guess."""
    out = {"resume": [], "waiting": [], "said": []}

    def said(ticket, why, text):
        out["said"].append({"ticket": ticket, "why": why, "text": text})
    written = pool.epoch(usage.get("written_at")) if isinstance(usage, dict) else None
    stops = usage.get("limit_stops") if isinstance(usage, dict) else None
    if not (isinstance(usage, dict) and usage.get("schema") == "pipeline-usage/1"
            and written is not None and now - written <= USAGE_FRESH
            and usage.get("result") != "failed" and isinstance(stops, list)
            and isinstance(usage.get("limit_stops_over_cap"), int)):
        said(None, "usage", "the usage file cannot be trusted to say which runs the limit "
                            "stopped, so nothing is resumed")
        return out
    done = done if isinstance(done, dict) else {}
    seen = set()
    for stop in stops:                                  # newest first: the newest of a piece
        ticket = stop.get("ticket") if isinstance(stop, dict) else None
        if not (isinstance(ticket, str) and TICKET_RE.match(ticket)
                and stop.get("kind") == "coding") or ticket in seen:
            continue
        seen.add(ticket)
        ended, reset = pool.epoch(stop.get("ended_at")), pool.epoch(stop.get("resets_at"))
        if ended is None:
            said(ticket, "time", "%s's limit stop has no time I can read" % ticket)
            continue
        prior = done.get(ticket) if isinstance(done.get(ticket), dict) else None
        handled = pool.epoch(prior.get("ended_at")) if prior else None
        if handled is not None and ended <= handled:
            continue                                    # the stop handled: once, never again
        if reset is None:
            said(ticket, "reset", "%s's limit stop names no reset time I can read" % ticket)
        elif now < reset:
            out["waiting"].append(ticket)
        elif now - reset > RESUME_LAPSE:
            said(ticket, "lapsed", "%s was stopped by a window that reset more than two days "
                                   "ago, so I won't resume it on my own" % ticket)
        elif prior:
            if prior.get("again") != stop["ended_at"]:
                said(ticket, "again", "%s was stopped by the limit again after a resume, so I "
                                      "won't resume it a second time on my own" % ticket)
                out["said"][-1]["ended_at"] = stop["ended_at"]
        else:
            window = stop.get("window")
            out["resume"].append({"ticket": ticket, "ended_at": stop["ended_at"],
                                  "window": window if window in WINDOWS else "other",
                                  "resets_at": stop["resets_at"]})
    return out


def resume_words(stop):
    """The comment a resume leaves: fixed words, a closed window word and two times."""
    def at(text):
        m = TIME_RE.match(text) if isinstance(text, str) else None
        return "%s %s UTC" % m.groups() if m else "an unreadable time"
    return ("Handed to the agent again by the queue: the plan's %s limit stopped it at %s, and "
            "that window reset at %s." % (stop["window"] if stop.get("window") in WINDOWS
                                          else "other", at(stop.get("ended_at")),
                                          at(stop.get("resets_at"))))


# --------------------------------------------------------------------------- #
# The preview: what would start now, read and never written
# --------------------------------------------------------------------------- #
def summary(result):
    """Counts, ids and words: never a title or text."""
    if result["state"] == "closed":
        return ["the working window is closed (%s): nothing would start" % result["why"]]
    if result["state"] == "no-room":
        return ["the machine is short of room (%s): nothing would start" % result["why"]]
    if result["state"] == "unread":
        return ["the queue could not be read (%s): nothing would start" % result["why"]]
    d = result["decision"]
    held = {}
    for h in d["held"]:
        held[h["reason"]] = held.get(h["reason"], 0) + 1
    lines = ["would start %d: %s" % (len(d["start"]), ", ".join(
        "%s (%s, %s)" % (s["ticket"], s["lane"], s["source"]) for s in d["start"]) or "none")]
    lines.append("held %d%s" % (len(d["held"]), ": " + ", ".join(
        "%s %d" % kv for kv in sorted(held.items())) if held else ""))
    lines.append("stops: %s" % (", ".join(s["stop"] for s in d["stops"]) or "none"))
    if result["why"] == "partial":
        lines.append("the queue was read in part")
    return lines


def preview(path, environ=None, now=None, out=print):
    """The exit code: 0 decided or closed, 3 read in part, 1 could not read, 2 config."""
    environ = os.environ if environ is None else environ
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError, RecursionError) as exc:
        out("CONFIG: could not read --config (%s)" % type(exc).__name__)
        return EXIT_USAGE
    cfg, bad = load(raw)
    if bad:
        for line in bad:
            out("CONFIG: %s" % line)
        return EXIT_USAGE
    key, why = status_job.credential(environ, cfg["reader"]["linear_key_env"])
    if key is None:
        out("the tracker key is not usable (%s): nothing was read" % why)
        return EXIT_ERROR
    result = decide(raw, lambda q, v: status_job.linear_graphql(q, v, key),
                    time.time() if now is None else now, time.monotonic,
                    lambda line: sys.stderr.write(line.replace(key, "[key]") + "\n"))
    for line in summary(result):
        out(line.replace(key, "[key]"))
    return {"unread": EXIT_ERROR, "no-room": EXIT_ERROR}.get(result["state"], EXIT_PARTIAL if result["why"] == "partial"
                                      else EXIT_OK)


def main(argv=None):
    p = argparse.ArgumentParser(prog="pipeline_queue_start.py", description=__doc__.split("\n")[0])
    p.add_argument("--selftest", action="store_true")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("preview").add_argument("--config", required=True)
    args = p.parse_args(argv)
    if args.selftest:
        from scratch_run import contained   # KIT-450: its temp folders go when it ends
        return contained(selftest, "queue-start-selftest-run-")
    if args.cmd != "preview":
        p.print_help()
        return EXIT_USAGE
    return preview(args.config)


# --------------------------------------------------------------------------- #
# --selftest: no tracker, no network, files only in a temp folder
# --------------------------------------------------------------------------- #
def selftest():
    import socket
    import tempfile
    fails, ran = [], [0]

    def check(name, got, want):
        ran[0] += 1
        good = got == want
        print("[%s] %s%s" % ("PASS" if good else "FAIL", name,
                             "" if good else "  (got %r, want %r)" % (got, want)))
        if not good:
            fails.append(name)

    def refuse(*a, **k):
        raise OSError("the selftest opens no socket")
    saved = (socket.socket, os.environ.get("TZ"), reader.account_home, reader.gather)
    real_room = globals()["read_room"]
    globals()["read_room"] = lambda: dict(pressure=1, free_disk_gb=100.0, swap_gb=0.0)   # no command runs
    socket.socket = refuse
    os.environ["TZ"] = "TST-3"          # the local day is UTC+3
    time.tzset()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(os.path.realpath(tmp), "home")
            os.mkdir(home)
            reader.account_home = lambda: home
            _cases(check, os.path.realpath(tmp), home)
    finally:
        globals()["read_room"] = real_room
        socket.socket, zone, reader.account_home, reader.gather = saved
        if zone is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = zone
        time.tzset()
    print("\n%d/%d checks passed" % (ran[0] - len(fails), ran[0]))
    return 1 if fails else 0


def _cases(check, tmp, home):
    import ast
    import check_ticket_dor as dor

    def morning_extras():
        """The morning job's EXTRAS, read from its source: importing it would make the bridge,
        which loads this file, look as if it loads the morning job too (the Stage E
        installer's job list counts every import)."""
        with open(os.path.join(HERE, "pipeline_morning.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                    getattr(t, "id", None) == "EXTRAS" for t in node.targets):
                return ast.literal_eval(node.value)
        return None
    OWNER, OTHER, LEAK = "owner-1", "other-1", "SENTINEL-TITLE-61b2"
    NOW = pool.epoch("2026-11-04T11:00:00Z")            # a Wednesday, 14:00 local
    TODAY = pool.local_date(NOW)
    GIT = os.path.join(tmp, "tree")
    os.makedirs(os.path.join(GIT, ".git"))
    calls = []

    def move(actor):
        return dict(dict.fromkeys(pool.HISTORY_KEYS), createdAt="2026-11-03T10:00:00Z",
                    actorId=actor, fromStateId="st-backlog", toStateId="st-todo")

    def issue(n):
        return {"id": "id-%d" % n, "identifier": "ENG-%d" % n, "title": "Title %s" % LEAK,
                "description": dor.GOOD_DESCRIPTION, "createdAt": "2026-11-02T09:00:00Z",
                "team": {"key": "ENG"}, "creator": {"id": OWNER}, "delegate": None, "parent": None,
                "project": {"id": "p"}, "state": {"id": "st-todo", "name": "Todo",
                                                  "type": "unstarted"},
                "children": {"nodes": []}, "inverseRelations": {"nodes": []},
                "labels": {"nodes": [{"name": x} for x in (
                    "track:platform", "effort:S", "provenance:human")]}}

    def extra(n, **more):
        return dict({"repo": "acme/app", "priority": 0, "plan_priority": 0, "effort": "S",
                     "blockers": [], "title": "Title %s" % LEAK, "pointers": []}, **more)

    def inputs(result="ok", **over):
        doc = {"schema": "pipeline-queue-inputs/1", "result": result,
               "sources": {"tracker": {"state": "read", "reason": None}},
               "tickets": [issue(1), issue(2)], "epics": [], "busy": [], "running": [],
               "histories": {"id-1": [move(OWNER)], "id-2": [move(OTHER)]},
               "findings": {}, "plans": {},
               "ledger": {"date": TODAY, "starts": 0, "agent_filed_starts": 0},
               "extras": {"ENG-1": extra(1), "ENG-2": extra(2, repo="acme/web", admitted=True,
                                                            reason="admitted")}}
        doc.update(over)
        return doc
    usage = os.path.join(tmp, "usage.json")
    with open(usage, "w", encoding="utf-8") as fh:
        json.dump({"schema": "pipeline-usage/1", "written_at": pool.iso(NOW - 300),
                   "result": "ok", "days": [{"date": TODAY, "usd": 6.5}], "pieces": [],
                   "status": {"windows": {}}, "sign_in": {"api_key_runs": []}}, fh)
    RAW = {"pool": {"owner_user_ids": [OWNER], "dispatcher_user_ids": ["disp-1"],
                    "team_keys": ["ENG"], "reviews_team_key": "REV"},
           "window": {"time_zone": "UTC", "days": {"wed": {"start": "09:00", "hours": 6}}},
           "reader": {"repo_by_team": {"ENG": "acme/app"},
                      "ledger_file": os.path.join(tmp, "q", "ledger.json")},
           "morning": {"usage_file": usage}}

    FINE = {"pressure": 1, "free_disk_gb": 100.0, "swap_gb": 0.0}
    asked = []

    def run(doc=None, now=NOW, raw=None, room=None):
        calls[:] = []
        asked[:] = []

        def stub(raw_, gql, now_, clock=None, say=None):
            calls.append(now_)
            return doc or inputs()
        reader.gather = stub
        r = decide(raw or RAW, None, now, room=lambda: (asked.append(1), room or FINE)[1])
        return (r["state"], r["why"], len(calls),
                [s["ticket"] for s in (r["decision"] or {}).get("start", [])],
                [s["stop"] for s in (r["decision"] or {}).get("stops", [])])

    def ledger_case(seed, *starts):
        path = os.path.join(tempfile_dir(), "ledger.json")
        if seed is not None:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(seed if isinstance(seed, str) else json.dumps(seed))
        before, got = open(path).read() if seed is not None else None, None
        try:
            for ticket, source in starts:
                got = record_start(path, NOW, ticket, source)
        except ValueError as exc:
            return ("refused", str(exc), open(path).read() == before if seed is not None
                    else not os.path.exists(path))
        mode = oct(os.stat(path).st_mode & 0o777)
        return (got["starts"], got["agent_filed_starts"],
                [(r["ticket"], r["source"], r["at"]) for r in got["rows"]], mode,
                reader.read_ledger(path, TODAY, lambda line: None)[0]["starts"])
    counter = [0]

    def tempfile_dir():
        counter[0] += 1
        path = os.path.join(tmp, "l%d" % counter[0])
        os.makedirs(path)
        return path
    AT = pool.iso(NOW)
    ENTRY = {"ticket": "ENG-1", "lane": "acme/app:1", "source": "todo", "plan": None,
             "at": "2026-11-03T10:00:00Z", "title": LEAK}

    def say(**kw):
        try:
            return attribution(dict(ENTRY, **kw))
        except ValueError:
            return "refused"

    def room_at(reading, **settings):
        """(state, why, tracker reads, lines): the queue with the machine in this state."""
        raw = dict(RAW, room=settings) if settings else RAW
        calls[:] = []

        def stub(raw_, gql, now_, clock=None, say=None):
            calls.append(now_)
            return inputs()
        reader.gather = stub
        r = decide(raw, None, NOW, room=lambda: dict(FINE, **reading))
        return r["state"], r["why"], len(calls), r.get("lines")

    class Stat:
        def __init__(self, free, frsize=4096):
            self.f_bavail, self.f_frsize = free, frsize

    def machine(pressure="1\n", swap="vm.swapusage: total = 4096.00M  used = 2048.50M  free = "
                "2047.50M  (encrypted)\n", volume=True, free=GIB // 4096 * 30):
        """read_room against a stand-in machine; records what it was asked."""
        seen = []

        def run(argv):
            seen.append(tuple(argv))
            text = pressure if "pressure" in argv[-1] else swap
            return text
        def stat(path):
            seen.append(path)
            if free is None:
                raise OSError("no")
            return Stat(free)
        got = REAL_READ_ROOM(run, stat, lambda path: volume)
        return got["pressure"], got["free_disk_gb"], got["swap_gb"], seen

    END = pool.iso(NOW - 7200)
    RESUME_STOP = {"ticket": "ENG-5", "kind": "coding", "ended_at": "2026-11-04T06:00:00Z",
                   "window": "five_hour", "resets_at": "2026-11-04T10:00:00Z"}

    def stop(ticket, ended=None, resets=NOW - 3600, kind="coding", window="five_hour"):
        at = lambda v: pool.iso(v) if isinstance(v, (int, float)) else v   # noqa: E731
        return {"ticket": ticket, "kind": kind, "window": window,
                "ended_at": at(NOW - 7200 if ended is None else ended), "resets_at": at(resets)}

    def usage_doc(*stops, **over):
        return dict({"schema": "pipeline-usage/1", "written_at": pool.iso(NOW - 300),
                     "result": "ok", "limit_stops": list(stops), "limit_stops_over_cap": 0},
                    **over)

    def resume_view(*stops, done=None, usage_over=None, usage=0):
        """(who to resume with the window, who waits, what is said as (ticket, why))."""
        doc = usage_doc(*stops, **(usage_over or {})) if usage == 0 else usage
        r = resumable(doc, NOW, done or {})
        return ([(x["ticket"], x["window"]) for x in r["resume"]], r["waiting"],
                [(x["ticket"], x["why"]) for x in r["said"]])

    PACE_USAGE = os.path.join(tmp, "pace-usage.json")

    def pace_view(order=None, known=True):
        """(the day's planned load, what was said) of a pass on a Wednesday with four working
        windows to the weekly reset, the usage file's week spent at 44 of a budget of 100."""
        doc = json.load(open(usage))
        doc.update(week={"known": known, "usd": 44.0 if known else None, "runs": 3})
        doc["status"] = {"windows": {"seven_day": {
            "status": "allowed", "utilization": 0.2, "surpassed": None,
            "resets_at": pool.iso(pool.epoch("2026-11-07T16:00:00Z")),
            "seen_at": pool.iso(NOW - 60)}}}
        with open(PACE_USAGE, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        said = []
        reader.gather = lambda *a, **k: inputs()
        four = {"time_zone": "UTC", "days": dict((d, {"start": "09:00", "hours": 6})
                                                  for d in ("wed", "thu", "fri", "sat"))}
        r = decide(dict(RAW, morning={"usage_file": PACE_USAGE}, window=four,
                        **({"order": order} if order else {})), None, NOW,
                   say=said.append, room=lambda: FINE)
        return r["decision"]["load"]["planned"], said

    def battery():
        return [
            ("config: a good one loads, and every fault is named at once",
             (load(RAW)[1], len(load(dict(RAW, oops=1, window={"days": {"wed": "9"}},
                                          morning={"usage_file": "u.json"}))[1])), ([], 3)),
            ("closed windows read nothing and start nothing",
             [run(now=pool.epoch("2026-11-04T08:00:00Z"))[:3],
              run(now=pool.epoch("2026-11-08T11:00:00Z"))[:3]],
             [("closed", "before-start", 0), ("closed", "day-off", 0)]),
            ("open: the owner's move starts, another's is held, extras admit nothing",
             run(), ("decided", None, 1, ["ENG-1"], [])),
            ("a failed read starts nothing and says why",
             run(inputs("failed", sources={"tracker": {"state": "could-not-read",
                                                       "reason": "the tracker did not answer"}})),
             ("unread", "the tracker did not answer", 1, [], [])),
            ("a partial read decides, and says so", run(inputs("partial"))[:2],
             ("decided", "partial")),
            ("a usage file it cannot read stops every start",
             run(raw=dict(RAW, morning={"usage_file": os.path.join(tmp, "none.json")}))[3:],
             ([], ["usage-missing"])),
            ("the extras are the morning job's seven", EXTRAS, morning_extras()),
            ("ledger: none yet, then the same day, then an agent-filed start",
             ledger_case(None, ("ENG-1", "todo"), ("ENG-2", "plan"), ("ENG-3", "agent_filed")),
             (3, 1, [("ENG-1", "todo", AT), ("ENG-2", "plan", AT), ("ENG-3", "agent_filed", AT)],
              "0o600", 3)),
            ("ledger: a day gone by starts again at zero",
             ledger_case({"date": "2026-11-03", "starts": 9, "agent_filed_starts": 4, "rows": []},
                         ("ENG-1", "todo"))[:3], (1, 0, [("ENG-1", "todo", AT)])),
            ("ledger: today's counts go on",
             ledger_case({"date": TODAY, "starts": 2, "agent_filed_starts": 1},
                         ("ENG-1", "backlog"))[:2], (3, 1)),
            ("ledger: dated ahead, not JSON, or not whole counts refuses, and leaves it",
             [ledger_case(s, ("ENG-1", "todo")) for s in (
                 {"date": "2026-11-05", "starts": 0, "agent_filed_starts": 0}, "{not json",
                 {"date": TODAY, "starts": 1.5, "agent_filed_starts": 0},
                 {"date": TODAY, "starts": 1, "agent_filed_starts": 0, "rows": ["x"]})],
             [("refused", "the ledger is dated after today", True),
              ("refused", "the ledger is not JSON", True),
              ("refused", "the ledger is not a ledger of starts", True),
              ("refused", "the ledger is not a ledger of starts", True)]),
            ("ledger: a start of no source or no ticket refuses, and writes nothing",
             [ledger_case(None, s) for s in (("ENG-1", "none"), ("eng-1", "todo"))],
             [("refused", "a start names a ticket id and one of todo, backlog, plan, "
               "agent_filed, resume", True)] * 2),
            ("ledger: a folder inside a git working tree is refused",
             _refused(lambda: record_start(os.path.join(GIT, "l.json"), NOW, "ENG-1", "todo")),
             True),
            ("attribution: a fixed sentence for each source, ids and a time only",
             [say(), say(at=None), say(source="plan", plan="ENG-10"),
              say(source="backlog"), say(source="agent_filed"), say(source="plan", plan="x")],
             ["Started by the queue, in lane acme/app:1: you moved it into the ready state at "
              "2026-11-03 10:00 UTC.",
              "Started by the queue, in lane acme/app:1: you moved it into the ready state.",
              "Started by the queue, in lane acme/app:1: it is a piece of ENG-10, which you "
              "approved at 2026-11-03 10:00 UTC.",
              "Started by the queue, in lane acme/app:1: you filed it in the backlog, and the "
              "backlog may start.",
              "Started by the queue, in lane acme/app:1: it is an agent-filed finding small "
              "enough to start by itself.",
              "Started by the queue, in lane acme/app:1: it is a piece of a plan, which you "
              "approved at 2026-11-03 10:00 UTC."]),
            ("attribution: not one of the order's starts is refused",
             [say(source="none"), say(lane="acme"), say(ticket="ENG-1 " + LEAK)],
             ["refused"] * 3),
            ("no title or text reaches a sentence or a summary",
             any(LEAK in x for x in [say(), say(source="plan", plan="ENG-10")]
                 + summary(decide_view())), False),
            ("room: a good section loads over the defaults, and every fault is named at once",
             (load(dict(RAW, room={"max_swap_gb": 8}))[0]["room"],
              load(dict(RAW, room={"min_free_disk_gb": -1, "max_pressure": "high",
                                   "max_swap_gb": True, "oops": 1}))[1],
              load(dict(RAW, room=[]))[1], ROOM_DEFAULTS),
             ({"min_free_disk_gb": 25, "max_pressure": "critical", "max_swap_gb": 8},
              ["room: 'oops': not a setting (min_free_disk_gb, max_pressure, max_swap_gb)",
               "room: min_free_disk_gb: must be a number of gigabytes, 0 or more",
               "room: max_swap_gb: must be a number of gigabytes, 0 or more",
               "room: max_pressure: must be \"warn\" or \"critical\""],
              ["room: not an object"],
              {"min_free_disk_gb": 25, "max_pressure": "critical", "max_swap_gb": 4})),
            ("room: plenty of room decides, and the machine is asked once",
             (run()[:3], len(asked)), (("decided", None, 1), 1)),
            ("room: free disk exactly on the line is room, a hair under holds before the tracker",
             [room_at({"free_disk_gb": 25.0}), room_at({"free_disk_gb": 24.96}),
              room_at({"free_disk_gb": 18.2})],
             [("decided", None, 1, None),
              ("no-room", "free disk 25.0 GB is under the 25 GB line", 0, ["disk"]),
              ("no-room", "free disk 18.2 GB is under the 25 GB line", 0, ["disk"])]),
            ("room: memory pressure warn is room by default, critical holds; warn holds when set",
             [room_at({"pressure": 2}), room_at({"pressure": 4}),
              room_at({"pressure": 2}, max_pressure="warn")],
             [("decided", None, 1, None),
              ("no-room", "memory pressure is critical, at the critical line", 0, ["pressure"]),
              ("no-room", "memory pressure is warn, at the warn line", 0, ["pressure"])]),
            ("room: swap exactly on the line is room, over it holds",
             [room_at({"swap_gb": 4.0}), room_at({"swap_gb": 4.1}),
              room_at({"swap_gb": 4.1}, max_swap_gb=5)],
             [("decided", None, 1, None),
              ("no-room", "swap in use 4.1 GB is over the 4 GB line", 0, ["swap"]),
              ("decided", None, 1, None)]),
            ("room: the settings move the lines",
             room_at({"free_disk_gb": 18.2}, min_free_disk_gb=10)[:2], ("decided", None)),
            ("room: every line over is named at once",
             room_at({"pressure": 4, "free_disk_gb": 1.0, "swap_gb": 9.0})[1:],
             ("memory pressure is critical, at the critical line; free disk 1.0 GB is under the "
              "25 GB line; swap in use 9.0 GB is over the 4 GB line", 0,
              ["pressure", "disk", "swap"])),
            ("room: a reading that could not be taken holds, and names which",
             [room_at({k: None}) for k in ("pressure", "free_disk_gb", "swap_gb")],
             [("no-room", "memory pressure could not be read", 0, ["pressure"]),
              ("no-room", "free disk could not be read", 0, ["disk"]),
              ("no-room", "swap in use could not be read", 0, ["swap"])]),
            ("room: a closed window asks nothing of the machine",
             (run(now=pool.epoch("2026-11-04T08:00:00Z"))[:3], len(asked)),
             (("closed", "before-start", 0), 0)),
            ("room: a machine with no room reads nothing of the tracker",
             run(room=dict(FINE, free_disk_gb=1.0))[:3],
             ("no-room", "free disk 1.0 GB is under the 25 GB line", 0)),
            ("pace: off, nothing changes; on, the day's load is the budget left over the working "
             "days left; unreadable, the configured figures and a line saying why",
             [pace_view(), pace_view(order=dict(pace_to_reset=True, weekly_budget_usd=100)),
              pace_view(order=dict(pace_to_reset=True, weekly_budget_usd=100), known=False)],
             [(40, []),
              (14.0, ["queue: paced to the weekly reset: 56.0 left of 100 over 4 working "
                              "windows, so today plans 14.0 and stops at 21.0"]),
              (40, ["queue: pacing is on, but the usage file cannot place the plan's "
                          "week, so the day stays at its configured figures"])]),
            ("room: the readings, parsed from the fixed commands",
             (machine(), machine(pressure="4\n", volume=False, swap="used = 512.00K"),
              machine(swap="total = 1.00G  used = 1.50G  free = 0"),
              machine(pressure="", swap="nothing", free=None)[:3],
              machine(pressure="x")[0]),
             ((1, 30.0, 2048.5 / 1024, [(SYSCTL, "-n", "kern.memorystatus_vm_pressure_level"), DATA_VOLUME,
                              (SYSCTL, "-n", "vm.swapusage")]),
              (4, 30.0, 512 / 1048576, [(SYSCTL, "-n", "kern.memorystatus_vm_pressure_level"), "/",
                                         (SYSCTL, "-n", "vm.swapusage")]),
              (1, 30.0, 1.5, [(SYSCTL, "-n", "kern.memorystatus_vm_pressure_level"), DATA_VOLUME,
                              (SYSCTL, "-n", "vm.swapusage")]),
              (None, None, None), None)),
            ("room: a command that fails or is not there reads as None, with a timeout set",
             (run_command([os.path.join(tmp, "no-such-command")]), ROOM_TIMEOUT <= 10,
              run_command([sys.executable, "-c", "print(7)"]).strip()), (None, True, "7")),
            ("room: the summary says it, with readings and no text",
             summary(dict(state="no-room", why="free disk 1.0 GB is under the 25 GB line")),
             ["the machine is short of room (free disk 1.0 GB is under the 25 GB line): "
              "nothing would start"]),
            ("resume: a coding piece whose window has reset is to be handed over once more",
             [resume_view(stop("ENG-5")), resume_view(stop("ENG-5", resets=NOW - 1))],
             [([("ENG-5", "five_hour")], [], [])] * 2),
            ("resume: a stop handled already, resumed or failed, is not met again or said again",
             [resume_view(stop("ENG-5"), done={"ENG-5": {"ended_at": END, "result": r}})
              for r in ("resumed", "failed")],
             [([], [], [])] * 2),
            ("resume: a second stop of a piece resumed before is said once, never resumed",
             [resume_view(stop("ENG-5", ended=NOW - 600),
                          done={"ENG-5": {"ended_at": END, "result": "resumed"}}),
              resume_view(stop("ENG-5", ended=NOW - 600),
                          done={"ENG-5": {"ended_at": END, "result": "resumed",
                                          "again": pool.iso(NOW - 600)}})],
             [([], [], [("ENG-5", "again")]), ([], [], [])]),
            ("resume: a window not yet reset waits, and a reset not known or not a time is said",
             [resume_view(stop("ENG-5", resets=NOW + 600)),
              resume_view(stop("ENG-5", resets=None)),
              resume_view(stop("ENG-5", resets="soon"))],
             [([], ["ENG-5"], []), ([], [], [("ENG-5", "reset")]),
              ([], [], [("ENG-5", "reset")])]),
            ("resume: a reset more than two days gone is said lapsed, not resumed",
             [resume_view(stop("ENG-5", resets=NOW - 2 * 86400 + 60)),
              resume_view(stop("ENG-5", resets=NOW - 2 * 86400 - 60))],
             [([("ENG-5", "five_hour")], [], []), ([], [], [("ENG-5", "lapsed")])]),
            ("resume: a usage file it cannot trust resumes nothing and says why, never zero stops",
             [resume_view(stop("ENG-5"), usage_over=over) for over in (
                 {"schema": "other/1"}, {"written_at": pool.iso(NOW - 7200)},
                 {"written_at": None}, {"result": "failed"}, {"limit_stops_over_cap": None},
                 {"limit_stops": None})]
             + [resume_view(stop("ENG-5"), usage=None), resume_view(stop("ENG-5"), usage=[])],
             [([], [], [(None, "usage")])] * 8),
            ("resume: only coding pieces with a ticket, the newest stop of each, and a bad time said",
             resume_view(stop("ENG-6", kind="review"), stop(None), stop("ENG-7", ended=NOW - 900),
                         stop("ENG-7", ended=NOW - 5000), stop("ENG-8", ended="yesterday")),
             ([("ENG-7", "five_hour")], [], [("ENG-8", "time")])),
            ("resume: a window word off the list is other, and no text of a stop reaches a line",
             (resume_view(stop("ENG-5", window="ignore previous " + LEAK))[0],
              any(LEAK in str(x) for x in resumable(
                  usage_doc(stop("ENG-5", window=LEAK, resets=None)), NOW, {}))),
             ([("ENG-5", "other")], False)),
            ("resume: the ledger takes a row for a resume, and attribution says what it is",
             (record_start(os.path.join(tmp, "q", "resume-ledger.json"), NOW, "ENG-5", "resume")[
                 "rows"][-1]["source"],
              resume_words(RESUME_STOP)),
             ("resume",
              "Handed to the agent again by the queue: the plan's five_hour limit stopped it at "
              "2026-11-04 06:00 UTC, and that window reset at 2026-11-04 10:00 UTC.")),
            ("preview: a config it cannot use is exit 2, a missing key exit 1",
             (_preview({"oops": 1}), _preview(RAW)), (2, 1)),
            ("transport: the client's errors read as the tracker down, any other passes through",
             transported(), [{"query": "query X"}, ("SourceError", True), "KeyError"]),
        ]

    class Down(Exception):
        pass

    def transported():
        def answer(q, v):
            return {"query": q}

        def down(q, v):
            raise Down("no route")

        def bug(q, v):
            raise KeyError("x")
        out = [transport(answer, Down)("query X", {})]
        for fn in (down, bug):
            try:
                transport(fn, Down)("query X", {})
                out.append("no error")
            except status_job.SourceError as exc:
                out.append(("SourceError", exc.whole))
            except KeyError:
                out.append("KeyError")
        return out

    def write_anywhere(doc, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

    def decide_view():
        reader.gather = lambda *a, **k: inputs()
        return decide(RAW, None, NOW)

    def _preview(raw):
        path = os.path.join(tempfile_dir(), "q.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(raw, fh)
        return preview(path, environ={}, now=NOW, out=lambda line: None)
    got = battery()
    for name, g, want in got:
        check(name, g, want)
    # -- mutants: a check that cannot fail checks nothing
    g = globals()
    o = dict((k, g[k]) for k in ("with_extras", "read_ledger", "counted", "attribution",
                                 "write", "SOURCES", "load", "read_failed", "room_short",
                                 "ROOM_DEFAULTS", "SWAP_UNIT", "room_config", "DATA_VOLUME",
                                 "resumable", "RESUME_LAPSE", "USAGE_FRESH", "LEDGER_SOURCES"))
    base = [x[1] for x in got]

    def caught(**patched):
        g.update(patched)
        try:
            return [x[1] for x in battery()] != base
        except Exception:  # noqa: BLE001 — a mutant the cases cannot even run is caught too
            return True
        finally:
            g.update(o)
    for name, patch in (
            ("extras admit a ticket", dict(with_extras=lambda vs, ex: [
                dict(v, **(ex.get(v.get("ticket")) or {})) for v in vs])),
            ("a ledger of any day taken for today's",
             dict(read_ledger=lambda path, today: o["read_ledger"](path, "2999-12-31"))),
            ("every ledger read as zero", dict(read_ledger=lambda path, today: {
                "date": today, "starts": 0, "agent_filed_starts": 0, "rows": []})),
            ("an agent-filed start not counted", dict(counted=lambda led, t, s, n: o["counted"](
                led, t, "todo" if s == "agent_filed" else s, n))),
            ("the row left out", dict(counted=lambda led, t, s, n: dict(
                o["counted"](led, t, s, n), rows=led["rows"]))),
            ("the ledger readable by anyone", dict(write=lambda doc, path: (
                o["write"](doc, path), os.chmod(path, 0o644)))),
            ("a folder inside a git working tree accepted", dict(write=write_anywhere)),
            ("a failed read decided anyway", dict(read_failed=lambda inputs: False)),
            ("any source started", dict(SOURCES=SOURCES + ("none",))),
            ("the title copied into the comment", dict(attribution=lambda e: o["attribution"](
                e) + " " + str(e.get("title")))),
            ("the room never checked", dict(room_short=lambda cfg, got: [])),
            ("a reading that could not be taken passed as fine", dict(
                room_short=lambda cfg, got: o["room_short"](cfg, dict(
                    (k, FINE[k] if v is None else v) for k, v in got.items())))),
            ("free disk on the line held", dict(room_short=lambda cfg, got: o["room_short"](
                cfg, dict(got, free_disk_gb=got["free_disk_gb"] - 1e-6)))),
            ("swap on the line held", dict(room_short=lambda cfg, got: o["room_short"](
                cfg, dict(got, swap_gb=got["swap_gb"] + 1e-6)))),
            ("warn always held", dict(room_short=lambda cfg, got: o["room_short"](
                dict(cfg, max_pressure="warn"), got))),
            ("the settings ignored", dict(room_short=lambda cfg, got: o["room_short"](
                dict(o["ROOM_DEFAULTS"]), got))),
            ("a different default for the disk", dict(ROOM_DEFAULTS=dict(
                o["ROOM_DEFAULTS"], min_free_disk_gb=10))),
            ("a room section nobody checks", dict(room_config=lambda raw: (
                dict(o["ROOM_DEFAULTS"], **raw) if isinstance(raw, dict) else None, []))),
            ("swap read in the wrong unit", dict(SWAP_UNIT=dict(o["SWAP_UNIT"], M=1))),
            ("the data volume not asked", dict(DATA_VOLUME="/")),
            ("a stop met again after it was handled", dict(resumable=lambda u, n, d: o[
                "resumable"](u, n, {}))),
            ("a window that has not reset resumed", dict(resumable=lambda u, n, d: o[
                "resumable"](u, n + 86400, d))),
            ("a reset long gone resumed", dict(RESUME_LAPSE=10 ** 9)),
            ("a stale usage file trusted", dict(USAGE_FRESH=10 ** 9)),
            ("a resume not counted in the ledger", dict(LEDGER_SOURCES=SOURCES)),
            ("a closed window read anyway", dict(load=lambda raw: (lambda c: (c[0] and dict(
                c[0], window=window.config({"days": dict.fromkeys(
                    window.DAYS, {"start": "00:00", "hours": 24})})[0]), c[1]))(o["load"](raw))))):
        check("mutant: %s is caught" % name, caught(**patch), True)
    check("…and with every mutant undone the starter is as it was",
          [x[1] for x in battery()] == base, True)


def _refused(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


if __name__ == "__main__":
    sys.exit(main())
