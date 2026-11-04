#!/usr/bin/env python3
"""One folder per run, removed at the end of the run — even when the run fails (KIT-450).

    from scratch_run import contained
    return contained(selftest, "stage-e-setup-selftest-")    # a battery, all of it

    from scratch_run import scratch_run
    with scratch_run("mutants-", parent=scratchpad) as run:   # a session's own copies
        ...

Everything a run makes with `tempfile` — folders, fake homes, copies of the repo — goes
inside the one folder, and the folder is removed in a `finally`: on a pass, a failure, an
exception, Ctrl-C, or a SIGTERM (a timeout's kill), which it turns into SystemExit for as
long as the run lasts. With `redirect_tmp=True` the run's own `tempfile` calls and every
child it starts (through TMPDIR, TEMP and TMP) land inside the folder too, so a case that
forgets to clean up still cleans up.

WHY. On 4 Nov 2026 the owner's Mac froze when its disk ran out. The kit's own selftests had
left 2.8 GB in TMPDIR — tens of thousands of folders, each run adding more — and TMPDIR
clears only at a restart. `scripts/check_temp_leaks.py` runs every battery in a private
TMPDIR and fails CI on anything left behind; this is the one way the batteries meet it.

Removal is in code, on the folder this run made and nothing else. That is the sanctioned
path: the PreToolUse guard blocks `rm -rf` typed in Bash on purpose, and a run that cleans
up its own folder never needs to type it.
"""
import contextlib
import os
import shutil
import signal
import stat
import tempfile
import threading

_TMP_VARS = ("TMPDIR", "TEMP", "TMP")
_SIGNALS = tuple(s for s in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGHUP", None))
                 if s is not None)


def remove_tree(path):
    """Remove a folder this run made, even one a case left without permissions.

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
    # A case that tested a refusal may leave a folder nobody can enter (mode 000): give
    # each folder its owner's rights back before the walk descends into it.
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


def _exit_on_signal(signum, frame):
    # SystemExit unwinds the stack, so every `finally` and `with` on the way out runs.
    raise SystemExit(128 + signum)


@contextlib.contextmanager
def scratch_run(prefix="scratch-run-", parent=None, redirect_tmp=False):
    """Make one folder for one run, yield its real path, and remove it at the end.

    prefix        names the folder (`<prefix><random>`), so a leftover says whose it is
    parent        where to make it; default: the current temp folder
    redirect_tmp  also point `tempfile` and TMPDIR/TEMP/TMP at the folder while it lasts
    """
    root = os.path.realpath(tempfile.mkdtemp(prefix=prefix, dir=parent))
    saved_tempdir = tempfile.tempdir
    saved_env = {k: os.environ.get(k) for k in _TMP_VARS}
    saved_handlers = {}
    # Only the main thread may set a handler, and a handler someone else installed stays.
    if threading.current_thread() is threading.main_thread():
        for sig in _SIGNALS:
            if signal.getsignal(sig) == signal.SIG_DFL:
                saved_handlers[sig] = signal.signal(sig, _exit_on_signal)
    try:
        if redirect_tmp:
            tempfile.tempdir = root
            for k in _TMP_VARS:
                os.environ[k] = root
        yield root
    finally:
        if redirect_tmp:
            tempfile.tempdir = saved_tempdir
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        # Hold a second SIGTERM until the folder is gone, so it cannot cut the removal short.
        blocked = bool(saved_handlers) and hasattr(signal, "pthread_sigmask")
        if blocked:
            signal.pthread_sigmask(signal.SIG_BLOCK, list(saved_handlers))
        try:
            remove_tree(root)
        finally:
            for sig, old in saved_handlers.items():
                signal.signal(sig, old)
            if blocked:
                signal.pthread_sigmask(signal.SIG_UNBLOCK, list(saved_handlers))


def contained(fn, prefix):
    """Run a battery inside one run folder, every temp folder it makes included; return
    what it returns. The folder goes when the battery ends, however it ends."""
    with scratch_run(prefix, redirect_tmp=True):
        return fn()


# --------------------------------------------------------------------------- selftest

def selftest():
    import subprocess
    import sys

    failures = []

    def expect(name, got, want):
        ok = got == want
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))
        if not ok:
            failures.append(name)

    here = os.path.dirname(os.path.abspath(__file__))
    with tempfile.TemporaryDirectory(prefix="scratch-run-selftest-") as outer:
        outer = os.path.realpath(outer)

        with scratch_run("passes-", parent=outer) as root:
            made = root
            expect("the folder exists while the run lasts", os.path.isdir(root), True)
            expect("…is named by its prefix, under its parent",
                   (os.path.dirname(root), os.path.basename(root).startswith("passes-")),
                   (outer, True))
        expect("…and is gone when the run passes", os.path.exists(made), False)

        try:
            with scratch_run("raises-", parent=outer) as root:
                made = root
                os.makedirs(os.path.join(root, "deep", "er"))
                raise ValueError("a failing case")
        except ValueError:
            pass
        expect("an exception still removes the folder", os.path.exists(made), False)

        try:
            with scratch_run("ctrl-c-", parent=outer) as root:
                made = root
                raise KeyboardInterrupt
        except KeyboardInterrupt:
            pass
        expect("Ctrl-C still removes the folder", os.path.exists(made), False)

        with scratch_run("locked-", parent=outer) as root:
            made = root
            os.makedirs(os.path.join(root, "a", "b"))
            open(os.path.join(root, "a", "b", "f"), "w").close()
            os.chmod(os.path.join(root, "a", "b"), 0)
            os.chmod(os.path.join(root, "a"), 0o500)
        expect("a leftover a case locked is removed too", os.path.exists(made), False)

        with scratch_run("link-", parent=outer) as root:
            made = root
            keep = os.path.join(outer, "keep-me")
            os.makedirs(keep)
            open(os.path.join(keep, "f"), "w").close()
            os.symlink(keep, os.path.join(root, "link"))
        expect("a symlink inside is removed, never followed",
               (os.path.exists(made), os.path.exists(os.path.join(keep, "f"))), (False, True))

        saved = (tempfile.tempdir, os.environ.get("TMPDIR"))
        with scratch_run("redirect-", parent=outer, redirect_tmp=True) as root:
            made = root
            inner = tempfile.mkdtemp(prefix="case-")
            child = subprocess.run([sys.executable, "-c",
                                    "import tempfile; print(tempfile.mkdtemp(prefix='child-'))"],
                                   capture_output=True, text=True).stdout.strip()
            expect("redirect_tmp: this process's tempfile lands inside",
                   os.path.dirname(inner), root)
            expect("redirect_tmp: a child's tempfile lands inside",
                   os.path.dirname(os.path.realpath(child)), root)
            with scratch_run("nested-") as nested:
                expect("a nested run is made inside the outer one", os.path.dirname(nested), root)
                nested_made = nested
            expect("…and is removed at its own end", os.path.exists(nested_made), False)
        expect("…and all of it goes with the folder", os.path.exists(made), False)
        expect("…and tempfile and TMPDIR are put back",
               (tempfile.tempdir, os.environ.get("TMPDIR")), saved)

        # contained(): a whole battery inside one run folder, its return value passed back.
        seen = {}

        def battery():
            seen["tmp"] = tempfile.gettempdir()
            seen["case"] = tempfile.mkdtemp(prefix="case-")
            return 7
        saved_tempdir = tempfile.tempdir
        tempfile.tempdir = outer
        try:
            got = contained(battery, "contained-")
        finally:
            tempfile.tempdir = saved_tempdir
        expect("contained() runs the battery inside its own run folder and returns its result",
               (got, os.path.basename(seen["tmp"]).startswith("contained-"),
                os.path.dirname(seen["case"]) == seen["tmp"]), (7, True, True))
        expect("…and that folder, with what the battery left in it, is gone after",
               (os.path.exists(seen["tmp"]), os.path.exists(seen["case"])), (False, False))

        # A SIGTERM (a timeout's kill) mid-run: the folder still goes, and the exit says why.
        probe = ("import os, signal, sys\n"
                 f"sys.path.insert(0, {here!r})\n"
                 "from scratch_run import scratch_run\n"
                 f"with scratch_run('term-', parent={outer!r}, redirect_tmp=True) as root:\n"
                 "    print(root, flush=True)\n"
                 "    os.makedirs(os.path.join(root, 'copy'))\n"
                 "    os.kill(os.getpid(), signal.SIGTERM)\n"
                 "    import time; time.sleep(10)\n"
                 "print('not reached')\n")
        r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                           timeout=60)
        made = r.stdout.split("\n")[0].strip()
        expect("a SIGTERM mid-run still removes the folder",
               (bool(made), os.path.exists(made) if made else None), (True, False))
        expect("…and the run exits 143, not as a pass",
               (r.returncode, "not reached" in r.stdout), (128 + signal.SIGTERM, False))

        # A handler the caller installed is theirs: kept while the run lasts, and after.
        def mine(signum, frame):
            pass
        old = signal.signal(signal.SIGTERM, mine)
        try:
            with scratch_run("theirs-", parent=outer):
                expect("a caller's own SIGTERM handler stays in place",
                       signal.getsignal(signal.SIGTERM), mine)
            expect("…and is still there after", signal.getsignal(signal.SIGTERM), mine)
        finally:
            signal.signal(signal.SIGTERM, old)
        with scratch_run("default-", parent=outer):
            pass
        expect("the default SIGTERM action is put back after a run",
               signal.getsignal(signal.SIGTERM), signal.SIG_DFL)

        expect("nothing is left under the parent but what the cases kept on purpose",
               sorted(os.listdir(outer)), ["keep-me"])

    if failures:
        print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(selftest() if "--selftest" in sys.argv[1:] else
             (print(__doc__) or 2))
