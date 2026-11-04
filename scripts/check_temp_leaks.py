#!/usr/bin/env python3
"""Temp-leak check — every selftest removes what it makes, even when it fails (KIT-450).

    python3 scripts/check_temp_leaks.py               # run every `test:*` battery, one at a time
    python3 scripts/check_temp_leaks.py --sigterm 1   # …and stop each with SIGTERM 1 s in
    python3 scripts/check_temp_leaks.py --only hooks,stage-e-setup
    python3 scripts/check_temp_leaks.py --list        # the batteries it would run
    python3 scripts/check_temp_leaks.py --selftest    # this check's own battery

Each battery in package.json runs once, in a plain loop, with TMPDIR (and TEMP, TMP)
pointed at its own new, empty folder. Whatever is in that folder when the battery exits
is a leak: the battery made it and left it. So is a process it started and left running
(the check stops it), and a new file in the repo that git does not track (a file you add to
the checkout while the check runs counts too). And so, in CI, is a
new top-level entry in /tmp from the battery's run: a child given a stripped environment
falls back to /tmp. On a person's machine other sessions write to /tmp all the time, so
there those entries are listed, never counted.

WHY THIS EXISTS. On 4 Nov 2026 the owner's Mac froze when its disk ran out. Its TMPDIR
held 2.8 GB the kit's own selftests had left there: 25,038 `stage-a-fakehome-*` folders,
about 4,300 each of two `hook-battery-*` kinds, 1,708 `stage-e-dry-*`. Sessions run these
batteries many times a day in many worktrees, and TMPDIR clears only at a restart. A leak
is silent in a battery's own output, so only a check that looks afterwards can see one.

A SIGTERM IS ITS OWN CASE (`--sigterm SECONDS`). A timeout here sends Ctrl-C first, so a
Python battery's `finally` and `with` blocks run, and a full pass leaves nothing anyway.
Neither shows what a SIGTERM does: Python's default handler ends the process without
running them, and a session's Bash timeout is what left thousands of folders on 4 Nov. With
`--sigterm`, each battery still running after SECONDS gets SIGTERM, to its whole process
group, and nothing else; whatever it leaves is a leak. A battery that does not end within
15 s of it fails too. Its own exit code is not judged: it was stopped on purpose.

THE CHECK REMOVES WHAT IT FINDS. Every folder it made is removed in a `finally`, leftovers
included, so running it never adds to the pile it measures. A leftover is reported, then
removed.

ONE AT A TIME, ON PURPOSE. The batteries are full Python processes; on 4 Nov fourteen at
once plus mutant jobs helped freeze the Mac. The loop is plain and sequential.

Exit 0 = every battery ran, passed, and left nothing behind.
Exit 1 = a battery left something behind, failed, or timed out (a battery that did not
         finish has not shown it cleans up — PIPELINE-CONTRACT §13: "could not do it" is
         never rendered as "nothing to do").
Exit 2 = the check itself could not run: no package.json, no batteries, a folder it could
         not make or could not remove.
"""
import json
import os
import re
import shlex
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMEOUT = 900
TERM_GRACE = 15                       # seconds a battery has to end after its SIGTERM
# The random tail tempfile (8 of [a-z0-9_]) or mktemp (6+ alphanumerics) adds after a
# prefix's separator, or after tempfile's own default prefix `tmp`.
RANDOM_TAIL = re.compile(r"(?:(?<=[._-])|(?<=^tmp))[A-Za-z0-9_]{6,}$")
# git status entries that are not leaks: Python's bytecode cache and installed packages.
NOT_LEAKS = re.compile(r"(^|/)(__pycache__|node_modules)(/|$)|\.pyc$")


def load_batteries(repo):
    """Every `test:*` script in package.json, in file order: [(name, command)]."""
    with open(os.path.join(repo, "package.json"), encoding="utf-8") as fh:
        scripts = json.load(fh).get("scripts") or {}
    return [(k, v) for k, v in scripts.items()
            if k.startswith("test:") and isinstance(v, str) and v.strip()]


def group(names):
    """Leftover names grouped by their prefix: {"stage-e-selftest.*": 2180, ...}."""
    counts = {}
    for name in names:
        m = RANDOM_TAIL.search(name)
        key = name[:m.start()] + "*" if m else name
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def tree_bytes(path):
    total = 0
    for root, dirs, files in os.walk(path):
        for name in files + dirs:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def remove_tree(path):
    """Remove a folder this check made, even one a battery left without permissions.

    Returns True when the path is gone. Symlinks inside are removed, never followed."""
    if not os.path.lexists(path):
        return True
    if os.path.islink(path) or not os.path.isdir(path):
        try:
            os.unlink(path)
        except OSError:
            pass
        return not os.path.lexists(path)
    try:
        os.chmod(path, stat.S_IRWXU)
    except OSError:
        pass
    # A battery that tested a refusal may leave a folder it cannot enter (mode 000):
    # give each folder its owner's rights back before the walk descends into it.
    for root, dirs, _ in os.walk(path):
        for d in dirs:
            full = os.path.join(root, d)
            if not os.path.islink(full):
                try:
                    os.chmod(full, stat.S_IRWXU)
                except OSError:
                    pass
    shutil.rmtree(path, ignore_errors=True)
    return not os.path.lexists(path)


def tmp_entries():
    """Top-level names in /tmp this user owns (another account's are not ours to judge)."""
    root = os.path.realpath("/tmp")
    uid = os.getuid()
    out = {}
    try:
        names = os.listdir(root)
    except OSError:
        return out
    for name in names:
        try:
            st = os.lstat(os.path.join(root, name))
        except OSError:
            continue
        if st.st_uid == uid:
            out[name] = st.st_ctime
    return out


def worktree_entries(repo):
    """Untracked and ignored paths in the repo, minus bytecode caches; None if not git."""
    r = subprocess.run(["git", "-C", repo, "status", "--porcelain=v1", "-z",
                        "--untracked-files=all", "--ignored"],
                       capture_output=True)
    if r.returncode != 0:
        return None
    out = set()
    for entry in r.stdout.decode("utf-8", "replace").split("\0"):
        if entry[:3] in ("?? ", "!! ") and not NOT_LEAKS.search(entry[3:]):
            out.add(entry[3:])
    return out


def _group_alive(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _stop(proc):
    """End a battery and everything it started (it runs in its own process group).

    SIGINT first, as Ctrl-C would: a Python battery unwinds, and its `finally` and `with`
    blocks remove what it made. Then SIGTERM, then SIGKILL, a few seconds apart — until no
    process of the group is left, a background child that ignores SIGINT included."""
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            break
        if _wait_group(proc, 5):
            return
    proc.poll()


def _wait_group(proc, seconds):
    """True once no process of the battery's group is left, within `seconds`."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        proc.poll()                         # reap the leader, or it reads as alive
        if not _group_alive(proc.pid):
            return True
        time.sleep(0.1)
    proc.poll()
    return not _group_alive(proc.pid)


def _term(proc, grace):
    """SIGTERM to the whole group, and nothing gentler first. True when it ended in time."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    if _wait_group(proc, grace):
        return True
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    _wait_group(proc, 5)
    return False


def run_battery(name, command, repo, work, timeout, term_after=None, term_grace=TERM_GRACE):
    """Run one battery with its own empty TMPDIR; return what it left behind.

    term_after: send SIGTERM this many seconds in, if it is still running (--sigterm)."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    tmp = os.path.join(work, "tmp", safe)
    os.makedirs(tmp)
    log_path = os.path.join(work, "logs", safe + ".log")
    env = dict(os.environ, TMPDIR=tmp, TEMP=tmp, TMP=tmp)
    # npm puts the project's own tools first on PATH; do the same without needing npm.
    env["PATH"] = os.pathsep.join([os.path.join(repo, "node_modules", ".bin"),
                                   env.get("PATH", "")])
    before_tmp = tmp_entries()
    before_tree = worktree_entries(repo)
    started = time.time()
    result = {"name": name, "rc": None, "timed_out": False, "running": False,
              "termed": False, "ignored_term": False}
    with open(log_path, "wb") as log:
        proc = subprocess.Popen(["/bin/sh", "-c", command], cwd=repo, env=env,
                                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True)
        try:
            if term_after is not None:
                try:
                    result["rc"] = proc.wait(timeout=term_after)
                except subprocess.TimeoutExpired:
                    result["termed"] = True
                    result["ignored_term"] = not _term(proc, term_grace)
                    result["rc"] = proc.returncode
            else:
                result["rc"] = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            result["timed_out"] = True
            _stop(proc)
            result["rc"] = proc.returncode
        except BaseException:
            _stop(proc)
            raise
        # A process the battery started and left running is left behind too: say so, end it.
        if _group_alive(proc.pid):
            result["running"] = True
            _stop(proc)
    result["seconds"] = time.time() - started
    left = sorted(os.listdir(tmp))
    result["left"] = left
    result["left_bytes"] = tree_bytes(tmp) if left else 0
    after_tmp = tmp_entries()
    result["in_tmp"] = sorted(n for n, ctime in after_tmp.items()
                              if n not in before_tmp and ctime >= started - 1)
    after_tree = worktree_entries(repo)
    result["in_repo"] = (sorted(after_tree - before_tree)
                         if before_tree is not None and after_tree is not None else [])
    if (result["rc"] not in (0, None) and not result["termed"]) or result["timed_out"]:
        with open(log_path, "rb") as fh:
            result["tail"] = fh.read()[-3000:].decode("utf-8", "replace")
    return result


def leaked(r, strict_tmp):
    return bool(r["left"] or r["in_repo"] or r["running"] or (strict_tmp and r["in_tmp"]))


def broke(r):
    """It did not finish as it should: timed out, failed, or did not end on its SIGTERM.
    A battery stopped on purpose is not judged by its exit code."""
    if r["termed"]:
        return r["ignored_term"]
    return r["timed_out"] or r["rc"] != 0


def describe(r, strict_tmp):
    if r["timed_out"]:
        verdict = "TIMEOUT"
    elif r["ignored_term"]:
        verdict = "NO-END"
    elif r["rc"] != 0 and not r["termed"]:
        verdict = "FAIL"
    elif leaked(r, strict_tmp):
        verdict = "LEAK"
    else:
        verdict = "ok"
    line = f"  {verdict:7} {r['name']:28} {r['seconds']:6.1f}s"
    parts = []
    if r["left"]:
        shown = ", ".join(f"{k} x{v}" for k, v in list(group(r["left"]).items())[:6])
        parts.append(f"{len(r['left'])} left in TMPDIR ({r['left_bytes'] / 1e6:.1f} MB): {shown}")
    if r["in_tmp"]:
        parts.append(f"{len(r['in_tmp'])} new in /tmp"
                     + ("" if strict_tmp else " (listed, not counted: other processes write there)")
                     + ": " + ", ".join(f"{k} x{v}" for k, v in list(group(r["in_tmp"]).items())[:6]))
    if r["in_repo"]:
        parts.append(f"{len(r['in_repo'])} new in the repo: " + ", ".join(r["in_repo"][:6]))
    if r["running"] and not r["timed_out"]:
        parts.append("left a process running after it ended (now stopped)")
    if r["ignored_term"]:
        parts.append(f"did not end within {TERM_GRACE} s of its SIGTERM (killed)")
    elif r["termed"]:
        parts.append("stopped by SIGTERM")
    elif r["rc"] != 0 and not r["timed_out"]:
        parts.append(f"exit {r['rc']}")
    return line + ("  " + "; ".join(parts) if parts else "")


def run_check(repo, batteries, timeout=TIMEOUT, parent=None, out=None, strict_tmp=None,
              term_after=None, term_grace=TERM_GRACE):
    """Run each battery in turn; return the exit code. Removes everything it made.

    strict_tmp: count new /tmp entries as leaks. Default: only in CI, where nothing else
    runs beside the battery; on a person's machine they are listed, not counted.
    term_after: stop each battery with SIGTERM this many seconds in (--sigterm)."""
    if out is None:
        def out(line):                  # a line at a time, so a redirected run shows progress
            print(line, flush=True)
    if strict_tmp is None:
        strict_tmp = bool(os.environ.get("CI"))
    if not batteries:
        out("check_temp_leaks: no `test:*` batteries in package.json, so nothing was "
            "checked — that is a broken setup, not a clean result.")
        return 2
    try:
        work = tempfile.mkdtemp(prefix="kit-temp-leaks-", dir=parent)
        os.makedirs(os.path.join(work, "tmp"))
        os.makedirs(os.path.join(work, "logs"))
    except OSError as exc:
        out(f"check_temp_leaks: could not make its own folder: {exc}")
        return 2
    results = []
    try:
        out(f"check_temp_leaks: {len(batteries)} batteries, one at a time, "
            "each with its own empty TMPDIR"
            + (f", each sent SIGTERM {term_after:g} s in." if term_after is not None else "."))
        for name, command in batteries:
            r = run_battery(name, command, repo, work, timeout, term_after, term_grace)
            results.append(r)
            out(describe(r, strict_tmp))
            if r.get("tail"):
                out("        last output:\n" + "\n".join(
                    "        | " + ln for ln in r["tail"].rstrip().splitlines()[-25:]))
            # Leftovers are reported, then removed: the check never adds to the pile.
            remove_tree(os.path.join(work, "tmp", re.sub(r"[^A-Za-z0-9_.-]", "_", name)))
    finally:
        gone = remove_tree(work)
    if not gone:
        out(f"check_temp_leaks: could not remove its own folder {work} — the result below "
            "stands, but this run left that folder behind.")
    leaks = [r for r in results if leaked(r, strict_tmp)]
    broken = [r for r in results if broke(r)]
    out("")
    if leaks:
        out(f"LEAKS: {len(leaks)} of {len(results)} batteries left something behind"
            + (" after a SIGTERM" if term_after is not None else "") + ": "
            + ", ".join(r["name"] for r in leaks) + ".")
        out("  Fix: run the battery inside contained() from scripts/scratch_run.py: one folder "
            "per run, removed in a `finally`, with a SIGTERM turned into a clean exit.")
    no_end = [r for r in broken if r["ignored_term"]]
    failed = [r for r in broken if not r["ignored_term"]]
    if failed:
        out(f"NOT CHECKED: {len(failed)} batteries failed or timed out, so they have not shown "
            "they clean up: " + ", ".join(r["name"] for r in failed) + ".")
    if no_end:
        out(f"NOT CHECKED: {len(no_end)} batteries did not end within {TERM_GRACE} s of their "
            "SIGTERM, so they have not shown they clean up: "
            + ", ".join(r["name"] for r in no_end) + ".")
    if not leaks and not broken:
        out(f"OK: all {len(results)} batteries "
            + ("left nothing behind after a SIGTERM, or ended before it." if term_after is not None
               else "passed and left nothing behind."))
    if not gone:
        return 2
    return 1 if leaks or broken else 0


# --------------------------------------------------------------------------- selftest

def selftest():
    failures = []

    def expect(name, got, want):
        ok = got == want
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))
        if not ok:
            failures.append(name)

    py = sys.executable
    with tempfile.TemporaryDirectory(prefix="temp-leaks-selftest-") as outer:
        outer = os.path.realpath(outer)
        repo = os.path.join(outer, "repo")
        os.makedirs(repo)
        subprocess.run(["git", "init", "-q", repo], check=True)
        parent = os.path.join(outer, "parent")
        os.makedirs(parent)

        def check(batteries, timeout=60, strict_tmp=False, term_after=None, term_grace=TERM_GRACE):
            lines = []
            code = run_check(repo, batteries, timeout=timeout, parent=parent,
                             out=lines.append, strict_tmp=strict_tmp, term_after=term_after,
                             term_grace=term_grace)
            return code, "\n".join(lines)

        def mk(prefix):
            return f"{py} -c \"import tempfile; tempfile.mkdtemp(prefix='{prefix}')\""

        code, text = check([("test:clean", f"{py} -c \"import tempfile\n"
                                           "with tempfile.TemporaryDirectory() as d: pass\"")])
        expect("a battery that cleans up passes", code, 0)
        expect("…and says so", "OK: all 1 batteries" in text, True)

        code, text = check([("test:leaky", mk("leaky-"))])
        expect("a battery that leaves a folder fails the check", code, 1)
        expect("…naming the battery and the folder's prefix",
               "LEAK" in text and "leaky-* x1" in text and "test:leaky" in text, True)

        code, text = check([("test:shell", "mktemp -d \"${TMPDIR:-/tmp}/shellleak.XXXXXX\" "
                                           ">/dev/null")])
        expect("a shell mktemp leak is caught too", (code, "shellleak.* x1" in text), (1, True))

        code, text = check([("test:fails-and-leaks",
                             f"{py} -c \"import tempfile, sys; tempfile.mkdtemp(prefix='oops-'); "
                             f"sys.exit(3)\"")])
        expect("a leak on the failure path is still a leak", "oops-* x1" in text, True)
        expect("…and the failure is said, not read as clean",
               (code, "exit 3" in text, "NOT CHECKED" in text), (1, True, True))

        code, text = check([("test:fails", f"{py} -c \"import sys; print('boom'); sys.exit(1)\"")])
        expect("a failing battery fails the check even with nothing left",
               (code, "NOT CHECKED" in text, "OK:" in text), (1, True, False))
        expect("…and shows its last output", "| boom" in text, True)

        code, text = check([("test:locked", f"{py} -c \"import tempfile, os; "
                                            "d = tempfile.mkdtemp(prefix='locked-'); "
                                            "os.makedirs(d + '/inner'); os.chmod(d + '/inner', 0); "
                                            "os.chmod(d, 0o500)\"")])
        expect("a leftover a battery locked is reported", "locked-* x1" in text, True)
        expect("…and the check still removes it", os.listdir(parent), [])

        start = time.time()
        code, text = check([("test:hangs", f"sleep 60 & {mk('slow-')}; wait")], timeout=2)
        expect("a battery past its timeout is stopped and fails the check",
               (code, "TIMEOUT" in text), (1, True))
        expect("…without waiting out its child", time.time() - start < 30, True)
        expect("…and what it left is still reported", "slow-* x1" in text, True)

        # --sigterm: SIGTERM straight to the battery's group, and nothing gentler first.
        here = os.path.dirname(os.path.abspath(__file__))
        wrapped = ("import sys, tempfile, time\n"
                   f"sys.path.insert(0, {here!r})\n"
                   "from scratch_run import contained\n"
                   "def battery():\n"
                   "    tempfile.mkdtemp(prefix='kept-')\n"
                   "    time.sleep(30)\n"
                   "contained(battery, 'wrapped-')\n")
        bare = ("import shutil, tempfile, time\n"
                "d = tempfile.mkdtemp(prefix='bare-')\n"
                "try:\n    time.sleep(30)\nfinally:\n    shutil.rmtree(d)\n")
        code, text = check([("test:wrapped", f"{py} -c {shlex.quote(wrapped)}")], term_after=1)
        expect("--sigterm: a battery inside contained() leaves nothing after its SIGTERM",
               (code, "stopped by SIGTERM" in text, "kept-" in text), (0, True, False))
        code, text = check([("test:bare", f"{py} -c {shlex.quote(bare)}")], term_after=1)
        expect("--sigterm: a bare mkdtemp and finally leaks on a SIGTERM, and that fails",
               (code, "bare-* x1" in text, "after a SIGTERM" in text), (1, True, True))
        code, text = check([("test:bare", f"{py} -c {shlex.quote(bare)}")], timeout=1)
        expect("…which a plain timeout cannot show: its Ctrl-C lets that same finally run",
               (code, "TIMEOUT" in text, "bare-" in text), (1, True, False))
        code, text = check([("test:stubborn", "trap '' TERM; sleep 30")], term_after=1,
                           term_grace=1)
        expect("--sigterm: a battery that does not end on its SIGTERM fails, and is killed",
               (code, "NO-END" in text, "did not end" in text), (1, True, True))
        code, text = check([("test:quick", "true")], term_after=5)
        expect("--sigterm: a battery that ends before the signal is judged as it ended",
               (code, "stopped by SIGTERM" in text), (0, False))
        code, text = check([("test:early-fail", "exit 2")], term_after=5)
        expect("--sigterm: one that fails before the signal is said to have failed, not hung",
               (code, "failed or timed out" in text, "did not end within" in text),
               (1, True, False))

        pidfile = os.path.join(outer, "bg.pid")
        code, text = check([("test:daemon", f"sleep 30 >/dev/null 2>&1 & echo $! > {pidfile}")])
        with open(pidfile) as fh:
            bg = int(fh.read().strip())
        try:
            os.kill(bg, 0)
            bg_alive = True
        except ProcessLookupError:
            bg_alive = False
        expect("a process left running after the battery ends is a leak, and is stopped",
               (code, "left a process running" in text, bg_alive), (1, True, False))

        code, text = check([("test:unwinds", f"{py} -c \"import tempfile, shutil, time\n"
                                             "d = tempfile.mkdtemp(prefix='unwinds-')\n"
                                             "try:\n    time.sleep(60)\n"
                                             "finally:\n    shutil.rmtree(d)\"")], timeout=2)
        expect("a timeout reaches a Python battery as Ctrl-C, so its own finally cleans up",
               (code, "TIMEOUT" in text, "unwinds-" in text), (1, True, False))

        stray = "temp-leaks-selftest-%d-%d.x" % (os.getpid(), int(time.time()))
        stray_path = os.path.join(os.path.realpath("/tmp"), stray)
        try:
            to_tmp = [("test:to-tmp", f"mkdir {stray_path}")]
            code, text = check(to_tmp, strict_tmp=True)
            expect("in CI, a new entry in /tmp is a leak", (code, stray in text), (1, True))
            os.rmdir(stray_path)
            code, text = check(to_tmp, strict_tmp=False)
            expect("on a person's machine it is listed, not counted",
                   (code, "not counted" in text and stray in text), (0, True))
        finally:
            if os.path.isdir(stray_path):
                os.rmdir(stray_path)

        code, text = check([("test:writes-repo", "touch stray.txt && mkdir -p a/__pycache__ "
                                                 "&& touch a/__pycache__/m.pyc")])
        expect("a file left in the repo is a leak; the bytecode cache is not",
               (code, "stray.txt" in text, "m.pyc" in text), (1, True, False))
        os.unlink(os.path.join(repo, "stray.txt"))

        code, text = check([("test:a", mk("first-")), ("test:b", f"{py} -c pass")])
        expect("each battery is judged on its own folder",
               ("test:a" in text.split("LEAKS:")[1], "test:b" in text.split("LEAKS:")[1]),
               (True, False))

        # The next battery runs after the last one's leftovers are gone, not at the very end.
        code, text = check([("test:a", mk("first-")),
                            ("test:b", "test ! -e \"$TMPDIR/../test_a\"")])
        expect("a battery's leftovers are removed before the next battery starts",
               "FAIL    test:b" in text, False)

        code, text = check([])
        expect("no batteries is a broken setup (exit 2), never a clean pass",
               (code, "nothing was checked" in text), (2, True))

        expect("the check leaves nothing behind itself", os.listdir(parent), [])

        with open(os.path.join(repo, "package.json"), "w") as fh:
            json.dump({"scripts": {"prepare": "husky", "test:x": "true", "lint:y": "true",
                                   "test:z": "false", "test:empty": ""}}, fh)
        expect("package.json: every test:* script with a command, in order",
               load_batteries(repo), [("test:x", "true"), ("test:z", "false")])
        r = subprocess.run([py, os.path.abspath(__file__), "--repo", parent],
                           capture_output=True, text=True)
        expect("a missing package.json is exit 2, said aloud",
               (r.returncode, "package.json" in r.stdout + r.stderr), (2, True))

        expect("prefix grouping strips tempfile's and mktemp's random tails",
               group(["stage-e-selftest.ab12_x9z", "stage-e-selftest.q1w2e3r4",
                      "hook-battery-pl-k3j4h5g6", "tmpk3j4h5g6", "README"]),
               {"stage-e-selftest.*": 2, "hook-battery-pl-*": 1, "tmp*": 1, "README": 1})

    expect("the selftest's own folder is gone", os.path.exists(outer), False)
    if failures:
        print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print("\nall checks passed")
    return 0


def main(argv):
    if "--selftest" in argv:
        from scratch_run import contained   # KIT-450: its temp folders go when it ends
        return contained(selftest, "temp-leaks-selftest-run-")
    repo = REPO
    if "--repo" in argv:
        repo = os.path.abspath(argv[argv.index("--repo") + 1])
    try:
        batteries = load_batteries(repo)
    except (OSError, ValueError) as exc:
        print(f"check_temp_leaks: could not read {repo}/package.json ({exc}), so nothing was "
              "checked.")
        return 2
    if "--only" in argv:
        wanted = {w if w.startswith("test:") else "test:" + w
                  for w in argv[argv.index("--only") + 1].split(",") if w}
        unknown = sorted(wanted - {n for n, _ in batteries})
        if unknown:
            print(f"check_temp_leaks: no such battery: {', '.join(unknown)}")
            return 2
        batteries = [(n, c) for n, c in batteries if n in wanted]
    if "--list" in argv:
        for name, command in batteries:
            print(f"{name:28} {command}")
        return 0
    timeout = TIMEOUT
    if "--timeout" in argv:
        timeout = int(argv[argv.index("--timeout") + 1])
    term_after = None
    if "--sigterm" in argv:
        term_after = float(argv[argv.index("--sigterm") + 1])

    def _on_sigterm(signum, frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, _on_sigterm)
    return run_check(repo, batteries, timeout=timeout, term_after=term_after)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
