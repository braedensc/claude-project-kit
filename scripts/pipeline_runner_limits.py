#!/usr/bin/env python3
"""The runner's limits: every run held to time, memory and disk, and no new start below a floor
(KIT-643; contract section 28).

    python3 scripts/pipeline_runner_limits.py --selftest

WHY.  On 4 Nov 2026 the owner's Mac froze, and he asked that the app never eat the computer's memory
or disk. The runner (scripts/pipeline_runner.py) knows every process it starts, so it holds the line.

THE LIMITS, from the runner's settings (`limits`), each far above any real run. A limit hit in
normal use is a defect (the owner's rule), so these stop a runaway and nothing else:

    wall_minutes   a run's time since its start                       default 360
    memory_mb      its whole process tree's resident memory            default 8192
    disk_mb        its own folders: its workspace and its run folder   default 20480
    room           the floor: the queue's own three lines, memory pressure, free disk and swap in
                   use (scripts/pipeline_queue_start.py, room_config)

Past a limit the run's whole tree is stopped: SIGTERM to its process group and to every process in
the tree, then SIGKILL to all of them after the grace. The reason is `limit:time`, `limit:memory` or
`limit:disk`. Below the floor no new session starts, and a running one finishes.

WHAT IT MEASURES, AND ONLY THAT.
  The run's tree: the processes in its process group, and every descendant of those by parent.
    They are found with pgrep and measured with ps, by pid. No other process is asked about.
  The run's folders: their files' blocks, walked without following a link. The walk stops as soon
    as the count passes the limit, or passes a cap on entries, which counts as past.
  The machine: the queue's own reader (read_room): three system totals, nothing per app.
A tree it cannot measure is said, once, and not stopped: a session is never killed because ps failed.

TELLING THE OWNER.  Each incident is recorded once, in plain words, in the runner's incidents file:
a run stopped at a limit, a tree it could not measure, or the floor holding new starts. The same
incident is never recorded twice. A floor incident ends when there is room again; the next one is
new. The health watch pages from that file once the runner is installed: it is a live job, so it
learns the runner with the runner's installer.

Standard library only, and the kit's own queue module.
"""
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    import pipeline_queue_start as queue_start
except ImportError as exc:  # a packaging accident, never a state to run in
    sys.exit("FAIL: %s. The limits use the queue's own room check and keep no copy." % exc)

DEFAULTS = {"wall_minutes": 360, "memory_mb": 8192, "disk_mb": 20480, "room": {}}
# The least each may be set to: below these a real run would hit them.
FLOORS = {"wall_minutes": 10, "memory_mb": 512, "disk_mb": 512}
CEILINGS = {"wall_minutes": 7 * 24 * 60, "memory_mb": 1 << 20, "disk_mb": 1 << 24}
MAX_ENTRIES = 500000          # more files than this in one run's folders counts as past its limit
MAX_TREE = 4000               # a tree of more processes than this counts as past its memory limit
TIMEOUT = 10                  # seconds, for each fixed command
PGREP, PS = "pgrep", "ps"
INCIDENTS_SCHEMA = "pipeline-runner-incidents/1"
KEPT = 200                    # incidents kept in the file, newest last
WORDS = {
    "limit:time": "it ran for %(minutes)d minutes, past its limit of %(limit)d",
    "limit:memory": "its processes held %(mb)d MB of memory, past its limit of %(limit)d MB",
    "limit:disk": "its folders held %(mb)d MB, past its limit of %(limit)d MB",
}


def load(raw):
    """(cfg, problems): the limits with defaults filled in, each problem named by its key."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        return None, ["limits: not an object"]
    bad = ["limits.%s: not a setting" % repr(k)[:40] for k in sorted(raw, key=repr) if k not in DEFAULTS]
    cfg = dict(DEFAULTS, **dict((k, v) for k, v in raw.items() if k in DEFAULTS))
    for key, low in FLOORS.items():
        v = cfg[key]
        if not (isinstance(v, int) and not isinstance(v, bool) and low <= v <= CEILINGS[key]):
            bad.append("limits.%s: must be a whole number from %d to %d" % (key, low, CEILINGS[key]))
    room, problems = queue_start.room_config(cfg["room"])
    bad += ["limits.room.%s" % p for p in problems]
    cfg["room"] = room
    return cfg, bad


# --------------------------------------------------------------------------- #
# Measuring: the run's own tree and folders, and nothing else
# --------------------------------------------------------------------------- #
def run_command(argv):
    """(exit code, output) of a fixed command, or (None, "") when it could not run."""
    try:
        done = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=TIMEOUT, check=False, universal_newlines=True)
    except (OSError, subprocess.SubprocessError):
        return None, ""
    return done.returncode, done.stdout


def _pids(code, out):
    """pgrep's answer: a list of pids, [] for none (exit 1), None when it failed."""
    if code not in (0, 1):
        return None
    try:
        return [int(x) for x in out.split()]
    except ValueError:
        return None


def tree(pgid, run=None):
    """The pids of the run's tree: its process group, and every descendant by parent. None when
    pgrep could not answer. Only these pids are ever asked about."""
    run = run or run_command
    found = _pids(*run([PGREP, "-g", str(pgid)]))
    if found is None:
        return None
    seen, todo = set(found), list(found)
    while todo and len(seen) <= MAX_TREE:
        kids = _pids(*run([PGREP, "-P", str(todo.pop())]))
        if kids is None:
            return None
        for k in kids:
            if k not in seen:
                seen.add(k)
                todo.append(k)
    return sorted(seen)


def resident_kb(pids, run=None):
    """The tree's resident memory in KB, or None when ps could not answer. A process that ended
    between the two questions simply is not counted."""
    if not pids:
        return 0
    run = run or run_command
    code, out = run([PS, "-o", "rss=", "-p", ",".join(str(p) for p in pids)])
    if code is None or (code != 0 and not out.strip()):
        return None if code is None else 0
    try:
        return sum(int(x) for x in out.split())
    except ValueError:
        return None


def folder_bytes(paths, cap):
    """(bytes, past): what the run's folders hold, by blocks, never following a link. It stops
    as soon as the count passes `cap` or MAX_ENTRIES entries, and then says past."""
    total, entries = 0, 0
    stack = [p for p in paths if p]
    while stack:
        path = stack.pop()
        try:
            st = os.lstat(path)
        except OSError:
            continue
        entries += 1
        total += getattr(st, "st_blocks", 0) * 512 or st.st_size
        if total > cap or entries > MAX_ENTRIES:
            return total, True
        if stat.S_ISDIR(st.st_mode):
            try:
                stack.extend(os.path.join(path, n) for n in os.listdir(path))
            except OSError:
                continue
    return total, False


def judge(cfg, started, now, mem_kb, disk):
    """None when the run is within every limit, else (reason, plain words). `disk` is
    (bytes, past) or None; `mem_kb` None when it could not be measured."""
    minutes = (now - started) / 60.0
    if minutes > cfg["wall_minutes"]:
        return "limit:time", WORDS["limit:time"] % {"minutes": minutes, "limit": cfg["wall_minutes"]}
    if mem_kb is not None and mem_kb > cfg["memory_mb"] * 1024:
        return "limit:memory", WORDS["limit:memory"] % {"mb": mem_kb // 1024, "limit": cfg["memory_mb"]}
    if disk is not None and (disk[1] or disk[0] > cfg["disk_mb"] << 20):
        return "limit:disk", WORDS["limit:disk"] % {"mb": disk[0] >> 20, "limit": cfg["disk_mb"]}
    return None


def check_run(cfg, pgid, started, folders, now, run=None):
    """One look at a live run: {"over": (reason, words) or None, "unmeasured": words or ""}."""
    pids = tree(pgid, run)
    mem = None if pids is None else resident_kb(pids, run)
    disk = folder_bytes(folders, cfg["disk_mb"] << 20)
    unmeasured = "" if mem is not None else "its processes could not be measured"
    return {"over": judge(cfg, started, now, mem, disk), "unmeasured": unmeasured, "pids": pids or []}


def signal_tree(pgid, pids, sig, kill=None, killpg=None):
    """Send `sig` to the run's group and to every pid of its tree. A process already gone is fine."""
    kill, killpg = kill or os.kill, killpg or os.killpg
    if isinstance(pgid, int) and pgid > 1:
        try:
            killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            pass
    for pid in pids or []:
        if isinstance(pid, int) and pid > 1:
            try:
                kill(pid, sig)
            except (ProcessLookupError, PermissionError):
                pass


def floor(cfg, read=None):
    """The lines the machine is under, as plain words: [] when a new session may start."""
    got = (read or queue_start.read_room)()
    return [words for _line, words in queue_start.room_short(cfg["room"], got)]


# --------------------------------------------------------------------------- #
# Incidents: each told once
# --------------------------------------------------------------------------- #
def _read_incidents(path):
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return {"schema": INCIDENTS_SCHEMA, "open": {}, "told": []}
    except (OSError, ValueError):
        return None
    if not (isinstance(doc, dict) and doc.get("schema") == INCIDENTS_SCHEMA
            and isinstance(doc.get("open"), dict) and isinstance(doc.get("told"), list)):
        return None
    return doc


def _write_incidents(path, doc):
    tmp = "%s.%d.tmp" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def tell(path, key, words, now):
    """Record an incident once. True when it is new and was recorded, False when it was told
    already. OSError when the file cannot be written; an unreadable file is replaced, so a
    broken record never hides a new incident."""
    doc = _read_incidents(path) or {"schema": INCIDENTS_SCHEMA, "open": {}, "told": []}
    if key in doc["open"]:
        return False
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    doc["open"][key] = {"at": at, "words": words}
    doc["told"] = (doc["told"] + [{"at": at, "key": key, "words": words}])[-KEPT:]
    _write_incidents(path, doc)
    return True


def clear(path, key):
    """End an open incident, so the next one is told. True when one was open."""
    doc = _read_incidents(path)
    if not doc or key not in doc["open"]:
        return False
    del doc["open"][key]
    _write_incidents(path, doc)
    return True


# --------------------------------------------------------------------------- #
# Selftest: stand-ins that grow without bound, each stopped
# --------------------------------------------------------------------------- #
GROWER = r'''
import os, sys, time
what, safety = sys.argv[1], int(sys.argv[2])
if what == "memory" and os.fork() == 0:
    os.setsid()                             # a child that left the group: found by its parent
    what = "memory-child"
held, step = [], 4 << 20
if what == "disk":
    target = os.path.join(sys.argv[3], "grow.bin")
    with open(target, "wb") as fh:
        while fh.tell() < safety:            # the safety stop: never reached when the limit works
            fh.write(b"x" * (1 << 20)); fh.flush(); os.fsync(fh.fileno()); time.sleep(0.02)
elif what.startswith("memory"):
    while len(held) * step < safety:
        held.append(bytearray(os.urandom(64)) * (step // 64)); time.sleep(0.05)
while True:
    time.sleep(0.05)
'''


def selftest():
    from scratch_run import contained
    return contained(_selftest, "runner-limits-selftest-")


def _selftest():  # noqa: C901  (one battery, read top to bottom)
    import tempfile
    failures, groups, counted = [], [], [0]

    def expect(name, got, want):
        counted[0] += 1
        if got != want:
            failures.append("%s: got %r, want %r" % (name, got, want))

    def start(*args):
        p = subprocess.Popen([sys.executable, "-I", "-c", GROWER] + [str(a) for a in args],
                             start_new_session=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        groups.append(p)
        return p

    def gone(pids):
        left = []
        for pid in pids:
            try:
                os.kill(pid, 0)
                left.append(pid)
            except ProcessLookupError:
                pass
            except PermissionError:
                left.append(pid)
        return left

    def watch(cfg, proc, folders, started, seconds=30):
        """The runner's own loop, small: look, and stop a run past a limit."""
        end, peak = time.time() + seconds, 0
        while time.time() < end:
            got = check_run(cfg, proc.pid, started, folders, time.time())
            peak = max(peak, resident_kb(got["pids"]) or 0)
            if got["over"]:
                signal_tree(proc.pid, got["pids"], signal.SIGTERM)
                time.sleep(0.3)
                signal_tree(proc.pid, tree(proc.pid) or got["pids"], signal.SIGKILL)
                proc.wait(timeout=10)
                return got["over"][0], got["pids"], peak
            time.sleep(0.1)
        return None, [], peak

    tmp = os.path.realpath(tempfile.mkdtemp(prefix="limits-"))
    try:
        # ---- settings ------------------------------------------------------ #
        cfg, bad = load(None)
        expect("no limits set: the defaults, far above any run",
               (bad, cfg["wall_minutes"], cfg["memory_mb"], cfg["disk_mb"]), ([], 360, 8192, 20480))
        for name, raw, want in (
                ("a memory limit a real run would hit", {"memory_mb": 64},
                 "limits.memory_mb: must be a whole number from 512 to 1048576"),
                ("a key it does not know", {"cpu": 1}, "limits.'cpu': not a setting"),
                ("a room line it does not know", {"room": {"min_free_ram": 1}},
                 "limits.room.'min_free_ram': not a setting (min_free_disk_gb, max_pressure, max_swap_gb)")):
            expect("settings: %s is refused" % name, load(raw)[1][:1], [want])

        # ---- the judge ----------------------------------------------------- #
        small = {"wall_minutes": 1, "memory_mb": 48, "disk_mb": 8}
        expect("within every limit: nothing", judge(small, 0, 30, 1024, (1 << 20, False)), None)
        expect("past the time", judge(small, 0, 61, 0, (0, False))[0], "limit:time")
        expect("past the memory", judge(small, 0, 1, 49 * 1024, (0, False))[0], "limit:memory")
        expect("past the disk", judge(small, 0, 1, 0, (9 << 20, False))[0], "limit:disk")
        expect("too many files counts as past the disk", judge(small, 0, 1, 0, (0, True))[0], "limit:disk")
        expect("memory it could not measure stops nothing", judge(small, 0, 1, None, (0, False)), None)
        expect("the words are plain", judge(small, 0, 1, 50 * 1024, (0, False))[1],
               "its processes held 50 MB of memory, past its limit of 48 MB")

        # ---- a runaway in memory, with a child: the whole tree stops ---------- #
        other = start("sleep", 0)            # not the run's: never measured, never touched
        grower = start("memory", 160 << 20)
        time.sleep(0.3)
        reason, pids, peak = watch(small, grower, [], time.time())
        expect("a tree that grows without bound is stopped at its memory limit", reason, "limit:memory")
        expect("the tree held the runaway and its child", len(pids) >= 2, True)
        expect("it was stopped well before its safety stop", 0 < peak < 200 * 1024, True)
        time.sleep(0.3)
        expect("every process of the tree is gone", gone(pids), [])
        expect("a process outside the run was never in its tree", other.pid in pids, False)
        expect("…and is untouched", gone([other.pid]), [other.pid])

        # ---- a runaway on disk ------------------------------------------------ #
        folder = os.path.join(tmp, "run")
        os.makedirs(folder)
        filler = start("disk", 128 << 20, folder)
        reason, pids, _ = watch(small, filler, [folder], time.time())
        expect("a run that fills its folder without bound is stopped at its disk limit",
               reason, "limit:disk")
        expect("…well before its safety stop", os.path.getsize(os.path.join(folder, "grow.bin")) < 64 << 20, True)

        # ---- a run past its time --------------------------------------------- #
        sleeper = start("sleep", 0)
        reason, _, _ = watch(small, sleeper, [], time.time() - 61)
        expect("a run past its wall-clock limit is stopped", reason, "limit:time")

        # ---- only its own: the tree by group and by parent --------------------- #
        calls = []

        def fake(argv):
            calls.append(argv)
            if argv[:2] == ["pgrep", "-g"]:
                return 0, "100\n101\n"
            if argv[:2] == ["pgrep", "-P"]:
                return (0, "300\n") if argv[2] == "101" else (1, "")
            return 0, "1000\n2000\n4000\n"
        expect("the tree is the group and every descendant", tree(100, fake), [100, 101, 300])
        expect("only the tree's pids are asked about",
               resident_kb([100, 101, 300], fake), 7000)
        expect("no command lists every process",
               [c for c in calls if c[0] == "ps" and "-p" not in c or "-A" in c or "-e" in c], [])
        expect("pgrep that fails: the tree is not measured", tree(100, lambda a: (2, "")), None)
        got = check_run(small, 100, time.time(), [], time.time(), run=lambda a: (None, ""))
        expect("a tree it cannot measure is said, and not stopped",
               (got["over"], got["unmeasured"]), (None, "its processes could not be measured"))

        # ---- the floor, and telling the owner once ------------------------------ #
        lcfg, _ = load(None)
        room = {"pressure": 1, "free_disk_gb": 400.0, "swap_gb": 0.0}
        expect("room: a new session may start", floor(lcfg, lambda: room), [])
        short = floor(lcfg, lambda: dict(room, free_disk_gb=3.0))
        expect("below the disk floor: no start, and why", short,
               ["free disk 3.0 GB is under the 25 GB line"])
        expect("memory it cannot read is no room", floor(lcfg, lambda: dict(room, pressure=None)),
               ["memory pressure could not be read"])
        path = os.path.join(tmp, "incidents.json")
        expect("an incident is told once", (tell(path, "floor", short[0], 1000.0),
                                            tell(path, "floor", short[0], 1001.0)), (True, False))
        expect("it ends when there is room again", clear(path, "floor"), True)
        expect("the next one is new", tell(path, "floor", short[0], 2000.0), True)
        with open(path) as fh:
            doc = json.load(fh)
        expect("the record holds the words, twice told, once open",
               (len(doc["told"]), list(doc["open"]), doc["told"][0]["words"]),
               (2, ["floor"], "free disk 3.0 GB is under the 25 GB line"))
        with open(path, "w") as fh:
            fh.write("{broken")
        expect("a broken record never hides a new incident", tell(path, "run-1:limit:memory", "w", 3000.0), True)
        expect("the incidents file is owner-only", stat.S_IMODE(os.stat(path).st_mode), 0o600)
    finally:
        for p in groups:
            signal_tree(p.pid, tree(p.pid) or [], signal.SIGKILL)
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
    if failures:
        print("FAIL: pipeline_runner_limits selftest")
        for f in failures:
            print("  - " + f)
        return 1
    print("OK: pipeline_runner_limits selftest (%d checks)" % counted[0])
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--selftest"]:
        return selftest()
    print(__doc__.split("\n\n")[0])
    print("usage: pipeline_runner_limits.py --selftest   (the runner imports it)")
    return 2


if __name__ == "__main__":
    sys.exit(main())
