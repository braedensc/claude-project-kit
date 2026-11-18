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
    check_seconds  how often its tree and folders are measured; its time is checked every pass  60
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

WHAT IT REMEMBERS (KIT-739).  Each process seen in a run's tree is remembered with its start time,
so one that leaves the tree, or outlives its parent, is still killed when the run ends. The start
time tells a process from a later one that reused its number. It is read at the finest resolution
the system offers: from the kernel's own process record on macOS (to the microsecond), from /proc
on Linux (in clock ticks), and from `ps lstart` (to the second) only where neither is there. The
record names the one it used. Each look drops the processes that are gone, so a long run's churn
never fills it. More than REMEMBER_MAX alive at once is a runaway: the newest are kept, and the run
is past its memory limit, in words that name the cap. The runner keeps the record in its own store,
never in the run's folder, which the run's own processes write (scripts/pipeline_runner.py).

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
import struct
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

DEFAULTS = {"wall_minutes": 360, "memory_mb": 8192, "disk_mb": 20480, "check_seconds": 60, "room": {}}
# The least each may be set to: below these a real run would hit them.
FLOORS = {"wall_minutes": 10, "memory_mb": 512, "disk_mb": 512}
CEILINGS = {"wall_minutes": 7 * 24 * 60, "memory_mb": 1 << 20, "disk_mb": 1 << 24}
MAX_ENTRIES = 500000          # more files than this in one run's folders counts as past its limit
MAX_TREE = 512                # a tree of more processes than this counts as past its memory limit
REMEMBER_MAX = 4096           # a run's processes remembered alive at once; more counts as past its memory limit
TIMEOUT = 10                  # seconds, for each fixed command
PGREP, PS = "pgrep", "ps"
INCIDENTS_SCHEMA = "pipeline-runner-incidents/1"
TREE_SCHEMA = "pipeline-runner-tree/1"
# How a process's start time is read, finest first, and what each one resolves.
RESOLUTION = {"sysctl": "microsecond", "proc": "clock tick", "ps": "second"}
KEPT = 200                    # incidents kept in the file, newest last
WORDS = {
    "limit:time": "it ran for %(minutes)d minutes, past its limit of %(limit)d",
    "limit:memory": "its processes held %(mb)d MB of memory, past its limit of %(limit)d MB",
    "limit:disk": "its folders held %(mb)d MB, past its limit of %(limit)d MB",
    "tree": "its process tree passed %(n)d processes, past its limit",
    "remember": "more than %(n)d of its processes were alive at once, past its limit",
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
    v = cfg["check_seconds"]
    if not (isinstance(v, int) and not isinstance(v, bool) and 5 <= v <= 3600):
        bad.append("limits.check_seconds: must be a whole number from 5 to 3600")
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


def walk(pgid, run=None):
    """(pids, too_many): the run's tree, its process group and every descendant by parent, one
    pgrep a level. (None, False) when pgrep could not answer. Past MAX_TREE it stops, and says
    too many. Only these pids are ever asked about."""
    run = run or run_command
    found = _pids(*run([PGREP, "-g", str(pgid)]))
    if found is None:
        return None, False
    seen, level = set(found), sorted(found)
    while level:
        if len(seen) > MAX_TREE:
            return sorted(seen), True
        kids = _pids(*run([PGREP, "-P", ",".join(str(p) for p in level)]))
        if kids is None:
            return None, False
        level = sorted(k for k in kids if k not in seen)
        seen.update(level)
    return sorted(seen), len(seen) > MAX_TREE


def tree(pgid, run=None):
    """The pids of the run's tree, or None when it could not be read."""
    return walk(pgid, run)[0]


# --------------------------------------------------------------------------- #
# Start times, at the finest resolution the system offers (KIT-739)
# --------------------------------------------------------------------------- #
_SYSCTL = []                  # the kernel's sysctl call, found once
_KINFO_MAX = 4096             # room for one struct kinfo_proc (648 bytes on 64-bit macOS)
_METHOD = []                  # the finest method this system offers, found once


def _sysctl_call():
    """libc's sysctl, with its C types, or None where it cannot be loaded."""
    if not _SYSCTL:
        try:
            import ctypes
            import ctypes.util
            fn = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True).sysctl
            fn.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_void_p,
                           ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
            fn.restype = ctypes.c_int
            _SYSCTL.append(fn)
        except (ImportError, OSError, AttributeError, TypeError):
            _SYSCTL.append(None)
    return _SYSCTL[0]


def _sysctl_starts(pids):
    """{pid: "seconds.microseconds"} from the kernel's own record of each process (macOS: sysctl
    CTL_KERN.KERN_PROC.KERN_PROC_PID). That record is a struct kinfo_proc, and its first member,
    kp_proc.p_un.__p_starttime, is the start time as a timeval. A process that is gone is left
    out. None when the call is not there or fails: nothing it said is then trusted."""
    fn = _sysctl_call()
    if fn is None:
        return None
    import ctypes
    buf = ctypes.create_string_buffer(_KINFO_MAX)
    got = {}
    for pid in pids:
        mib = (ctypes.c_int * 4)(1, 14, 1, int(pid))      # CTL_KERN, KERN_PROC, KERN_PROC_PID
        size = ctypes.c_size_t(_KINFO_MAX)
        if fn(mib, 4, buf, ctypes.byref(size), None, 0) != 0:
            return None
        if size.value < 16:                           # 0: no such process
            continue
        sec, usec = struct.unpack_from("=qi", buf, 0)
        if sec <= 0 or not 0 <= usec < 1000000:
            return None
        got[str(pid)] = "%d.%06d" % (sec, usec)
    return got


def _proc_starts(pids):
    """{pid: clock ticks since boot} from /proc/<pid>/stat (Linux): field 22. The command's name,
    field 2, may hold spaces and parentheses, so the fields are counted after its last ')'. A
    process that is gone is left out. None when /proc is not there or a line cannot be read."""
    if not os.path.isdir("/proc/self"):
        return None
    got = {}
    for pid in pids:
        try:
            with open("/proc/%d/stat" % int(pid), "rb") as fh:
                data = fh.read()
        except (FileNotFoundError, ProcessLookupError):
            continue
        except OSError:
            return None
        rest = data[data.rfind(b")") + 1:].split()
        if len(rest) < 20 or not rest[19].isdigit():
            return None
        got[str(pid)] = rest[19].decode("ascii")      # field 22, counted from field 3
    return got


def _ps_starts(pids, run=None):
    """{pid: start} as `ps lstart` prints it: to the second. None when ps could not run."""
    run = run or run_command
    code, out = run([PS, "-o", "pid=,lstart=", "-p", ",".join(str(p) for p in pids)])
    if code is None:
        return None
    got = {}
    for line in out.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            got[parts[0]] = parts[1].strip()
    return got


def start_method():
    """The finest way this system offers to read a start time, found once by reading this
    process's own: "sysctl" on macOS, "proc" on Linux, else "ps" (to the second)."""
    if not _METHOD:
        me = str(os.getpid())
        tries = (("sysctl", _sysctl_starts),) if sys.platform == "darwin" else ()
        tries += (("proc", _proc_starts),)
        _METHOD.append(next((name for name, read in tries if (read([os.getpid()]) or {}).get(me)),
                            "ps"))
    return _METHOD[0]


def start_times(pids, method=None, run=None):
    """{pid: start} for the processes that are there, read by `method` (the finest this system
    offers when None); None when they could not be read. Compare two starts only when they were
    read by the same method."""
    pids = sorted(set(int(p) for p in pids if str(p).isdigit() and int(p) > 0))
    if not pids:
        return {}
    method = method or start_method()
    if method == "sysctl":
        return _sysctl_starts(pids)
    if method == "proc":
        return _proc_starts(pids)
    return _ps_starts(pids, run)


# --------------------------------------------------------------------------- #
# The remembered tree: what of a run is killed when it ends (KIT-739)
# --------------------------------------------------------------------------- #
def new_record(method=None):
    """An empty record of a run's tree. `leader` is the run's wrapper, whose number is its process
    group's; `procs` its processes seen alive, each [pid, start], first seen first."""
    method = method or start_method()
    return {"schema": TREE_SCHEMA, "method": method, "resolution": RESOLUTION[method],
            "leader": None, "procs": [], "dropped": 0}


def _entry(v):
    return (isinstance(v, list) and len(v) == 2 and all(isinstance(x, str) for x in v)
            and v[0].isdigit() and int(v[0]) > 1 and 0 < len(v[1]) <= 64)


def record_problems(doc):
    """What is wrong with a tree record's shape, in words: empty when it is one."""
    if not (isinstance(doc, dict) and doc.get("schema") == TREE_SCHEMA):
        return ["the record is not a %s" % TREE_SCHEMA]
    if doc.get("method") not in RESOLUTION or doc.get("resolution") != RESOLUTION.get(doc.get("method")):
        return ["the record names no way of reading start times"]
    if not (doc.get("leader") is None or _entry(doc["leader"])):
        return ["the record's leader is not a process"]
    procs = doc.get("procs")
    if not (isinstance(procs, list) and len(procs) <= REMEMBER_MAX and all(_entry(p) for p in procs)):
        return ["the record's processes are not a list of processes"]
    if not (isinstance(doc.get("dropped"), int) and not isinstance(doc["dropped"], bool)
            and doc["dropped"] >= 0):
        return ["the record's count of dropped processes is not one"]
    return []


def remember(record, pids, read=None):
    """(record, over, unread): `pids`, the run's tree as just seen, added to its record.

    Each look first drops the remembered processes that are gone, or whose number now belongs
    to a process that started at another time, so a long run's churn never fills the record.
    More than REMEMBER_MAX alive at once is a runaway: the newest are kept, the rest are counted
    in `dropped`, and `over` is (limit:memory, words naming the cap). It is never silent.
    `unread` is True when the start times could not be read: then nothing is dropped and
    nothing added. `read` stands in for start_times in tests."""
    record = record if record is not None and not record_problems(record) else new_record()
    read = read or (lambda p: start_times(p, record["method"]))
    known = [list(p) for p in record["procs"]]
    asked = set(int(p) for p, _ in known) | set(int(p) for p in pids
                                                if str(p).isdigit() and int(p) > 1)
    now = read(sorted(asked)) if asked else {}
    if now is None:
        return record, None, True
    kept = [p for p in known if now.get(p[0]) == p[1]]
    have = set(p[0] for p in kept)
    for pid in sorted(set(str(p) for p in pids if str(p).isdigit() and int(p) > 1)):
        if pid in now and pid not in have:
            kept.append([pid, now[pid]])
            have.add(pid)
    over = None
    if len(kept) > REMEMBER_MAX:
        cut = len(kept) - REMEMBER_MAX
        record["dropped"] += cut
        kept = kept[cut:]
        over = ("limit:memory", WORDS["remember"] % {"n": REMEMBER_MAX})
    record["procs"] = kept
    return record, over, False


def remembered_alive(record, read=None):
    """The remembered processes that are still the same process: the same pid with the same
    start time. None when their start times could not be read."""
    if not record or record_problems(record) or not record["procs"]:
        return []
    read = read or (lambda p: start_times(p, record["method"]))
    now = read([int(p) for p, _ in record["procs"]])
    if now is None:
        return None
    return [int(p) for p, s in record["procs"] if now.get(p) == s]


def kill_remembered(record, read=None, kill=None):
    """SIGKILL each remembered process that is still the same process. A process that left the
    tree, or outlived its parent, is still found this way, and a later process that reused a
    number never is. Returns how many were sent the signal; None when their start times could
    not be read, and then nothing is sent."""
    alive = remembered_alive(record, read)
    if alive is None:
        return None
    kill = kill or os.kill
    sent = 0
    for pid in alive:
        if pid > 1 and pid != os.getpid():
            try:
                kill(pid, signal.SIGKILL)
                sent += 1
            except (ProcessLookupError, PermissionError):
                pass
    return sent


def leader_is_gone_or_same(record, pid, read=None):
    """False only when the run's group number `pid` is now a later process's: a process holds
    that number and started at another time than the run's leader. Then the run's own group is
    long gone, and a signal to that group would reach a stranger. True otherwise, and True when
    nothing was recorded to tell them apart."""
    leader = (record or {}).get("leader") if not record_problems(record or {}) else None
    if not (leader and isinstance(pid, int) and leader[0] == str(pid)):
        return True
    read = read or (lambda p: start_times(p, record["method"]))
    now = read([pid])
    if now is None:
        return True
    return now.get(str(pid)) in (None, leader[1])


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


def judge(cfg, started, now, mem_kb, disk, too_many=False):
    """None when the run is within every limit, else (reason, plain words). `disk` is
    (bytes, past) or None; `mem_kb` None when it could not be measured; `too_many` when its tree
    passed MAX_TREE processes, which counts as past its memory limit."""
    minutes = (now - started) / 60.0
    if minutes > cfg["wall_minutes"]:
        return "limit:time", WORDS["limit:time"] % {"minutes": minutes, "limit": cfg["wall_minutes"]}
    if too_many:
        return "limit:memory", WORDS["tree"] % {"n": MAX_TREE}
    if mem_kb is not None and mem_kb > cfg["memory_mb"] * 1024:
        return "limit:memory", WORDS["limit:memory"] % {"mb": mem_kb // 1024, "limit": cfg["memory_mb"]}
    if disk is not None and (disk[1] or disk[0] > cfg["disk_mb"] << 20):
        return "limit:disk", WORDS["limit:disk"] % {"mb": disk[0] >> 20, "limit": cfg["disk_mb"]}
    return None


def check_run(cfg, pgid, started, folders, now, run=None):
    """One look at a live run: {"over": (reason, words) or None, "unmeasured": words or ""}."""
    pids, too_many = walk(pgid, run)
    mem = None if pids is None else resident_kb(pids, run)
    disk = folder_bytes(folders, cfg["disk_mb"] << 20)
    unmeasured = "" if mem is not None else "its processes could not be measured"
    return {"over": judge(cfg, started, now, mem, disk, too_many), "unmeasured": unmeasured,
            "pids": pids or []}


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
                return (0, "300\n") if "101" in argv[2].split(",") else (1, "")
            return 0, "1000\n2000\n4000\n"
        expect("the tree is the group and every descendant", tree(100, fake), [100, 101, 300])
        expect("only the tree's pids are asked about",
               resident_kb([100, 101, 300], fake), 7000)
        expect("no command lists every process",
               [c for c in calls if c[0] == "ps" and "-p" not in c or "-A" in c or "-e" in c], [])
        expect("one pgrep a level, not one a process",
               [c[2] for c in calls if c[:2] == ["pgrep", "-P"]], ["100,101", "300"])
        expect("pgrep that fails: the tree is not measured", tree(100, lambda a: (2, "")), None)

        def crowd(argv):
            if argv[:2] == ["pgrep", "-g"]:
                return 0, "\n".join(str(p) for p in range(1000, 1000 + MAX_TREE + 5))
            return (1, "") if argv[0] == "pgrep" else (0, "1\n" * 10)
        got = check_run(small, 100, time.time(), [], time.time(), run=crowd)
        expect("a tree of more processes than the cap is past its memory limit, however small",
               got["over"], ("limit:memory", "its process tree passed 512 processes, past its limit"))

        # ---- what it remembers (KIT-739) ------------------------------------ #
        method = start_method()
        want = {"darwin": "sysctl"}.get(sys.platform, "proc" if sys.platform.startswith("linux") else "ps")
        expect("start times are read the finest way this system offers", method, want)
        mine = start_times([os.getpid()])
        expect("…and this process's own is read that way, finer than a second where it can be",
               (sorted(mine), "." in mine[str(os.getpid())] if method == "sysctl" else True),
               ([str(os.getpid())], True))
        expect("the record names its resolution", (new_record()["method"], new_record()["resolution"]),
               (method, RESOLUTION[method]))
        expect("…and says so when one second is all there is",
               new_record("ps")["resolution"], "second")

        def ps_starts(argv):
            return 0, "100 Thu Nov 12 22:00:00 2026\n300 Thu Nov 12 22:05:00 2026\n"
        expect("ps's start times, to the second, when it is the only way",
               start_times([100, 300], "ps", ps_starts),
               {"100": "Thu Nov 12 22:00:00 2026", "300": "Thu Nov 12 22:05:00 2026"})
        alive = {"100": "1.000001", "300": "2.000002"}

        def reader(p):
            return dict((k, v) for k, v in alive.items() if int(k) in p)
        rec = dict(new_record("sysctl"), procs=[["100", "1.000001"], ["300", "9.000009"]])
        sent = []
        expect("a remembered process is killed only while it is the same process",
               (kill_remembered(rec, reader, lambda pid, sig: sent.append((pid, sig))), sent),
               (1, [(100, signal.SIGKILL)]))
        expect("…and nothing is killed when the start times cannot be read",
               kill_remembered(rec, lambda p: None, lambda pid, sig: sent.append(pid)), None)
        rec, over, unread = remember(dict(new_record("sysctl"), procs=[["100", "1.000001"],
                                                                       ["300", "9.000009"]]),
                                     [100, 300, 400], reader)
        expect("a look drops a process that is gone and one whose number a later process took, "
               "and adds the new", (rec["procs"], over, unread),
               ([["100", "1.000001"], ["300", "2.000002"]], None, False))
        expect("start times it cannot read change nothing, and say so",
               remember(dict(rec), [100, 999], lambda p: None)[1:], (None, True))
        real_max = globals()["REMEMBER_MAX"]
        globals()["REMEMBER_MAX"] = 3
        try:
            alive.update({"501": "5.1", "502": "5.2", "503": "5.3"})
            rec, over, _ = remember(rec, [100, 300, 501, 502, 503], reader)
            expect("past the cap the newest are kept, the rest counted, and the run is past its "
                   "memory limit, in words that name the cap",
                   ([p[0] for p in rec["procs"]], rec["dropped"], over),
                   (["501", "502", "503"], 2,
                    ("limit:memory", "more than 3 of its processes were alive at once, past its limit")))
            expect("…and the record that holds them is still one", record_problems(rec), [])
        finally:
            globals()["REMEMBER_MAX"] = real_max
        expect("a record that is not one is never read as one",
               (record_problems({"300": "Thu Nov 12 22:05:00 2026"})[:1],
                record_problems(dict(rec, procs=[["1", "x"]]))[:1]),
               (["the record is not a pipeline-runner-tree/1"],
                ["the record's processes are not a list of processes"]))
        expect("the run's group is signalled while its leader is gone or the same",
               (leader_is_gone_or_same(dict(rec, leader=["100", "1.000001"]), 100, reader),
                leader_is_gone_or_same(dict(rec, leader=["700", "7.0"]), 700, reader)), (True, True))
        expect("…and never once its number belongs to a process that started later",
               leader_is_gone_or_same(dict(rec, leader=["300", "9.000009"]), 300, reader), False)
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
        # Again until nothing is left: a kill can miss a process its group's leader is forking
        # at that instant (measured on CI, 12 Nov).
        for p in groups:
            end = time.time() + 10
            while time.time() < end:
                signal_tree(p.pid, tree(p.pid) or [], signal.SIGKILL)
                try:
                    p.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    continue
                if not tree(p.pid):
                    break
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
