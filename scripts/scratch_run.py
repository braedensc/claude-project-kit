#!/usr/bin/env python3
"""One folder per run, removed at the end of the run — even when the run fails (KIT-450).

    from scratch_run import contained
    return contained(selftest, "stage-e-setup-selftest-")    # a battery, all of it

    from scratch_run import scratch_run
    with scratch_run("mutants-", parent=scratchpad) as run:   # a session's own copies
        ...

    # a session's mutant sweep or fail-first proof, from the shell (KIT-459):
    python3 scripts/scratch_run.py --label failfirst --copy origin/main -- python3 scripts/x.py --selftest
    python3 scripts/scratch_run.py --label mutants -- python3 my_mutants.py   # copies in $SCRATCH_RUN_DIR

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
        # This battery runs inside contained(), which holds the handler; the case starts from
        # the default, as a fresh process does.
        saved_term = signal.signal(signal.SIGTERM, signal.SIG_DFL)
        try:
            with scratch_run("default-", parent=outer):
                pass
            expect("the default SIGTERM action is put back after a run",
                   signal.getsignal(signal.SIGTERM), signal.SIG_DFL)
        finally:
            signal.signal(signal.SIGTERM, saved_term)

        # The command (KIT-459): one folder per run of a session's own command line.
        me = os.path.join(here, "scratch_run.py")
        kept = os.path.join(outer, "keep-me")

        def stop(p):
            """Ask a child still running to stop (a SIGTERM, which it passes to its command
            before removing its folder), and kill it only if it has not within 15 s."""
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()

        def cli(*args, cwd=None):
            p = subprocess.Popen([sys.executable, me, "--parent", outer] + list(args), cwd=cwd,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                out, err = p.communicate(timeout=120)
            finally:
                stop(p)
            return subprocess.CompletedProcess(p.args, p.returncode, out, err)

        r = cli("--label", "plain", "--", "sh", "-c",
                'mkdir "$SCRATCH_RUN_DIR/m01" && echo x > "$SCRATCH_RUN_DIR/m01/f" && '
                'echo "$SCRATCH_RUN_DIR" && "%s" -c "import tempfile; print(tempfile.mkdtemp())"'
                % sys.executable)
        made, child_tmp = (r.stdout.split() + ["", ""])[:2]
        expect("the command gets one folder, named by its label, with its TMPDIR inside",
               (r.returncode, os.path.basename(made).startswith("plain-run-"),
                os.path.dirname(made), child_tmp.startswith(made + os.sep)),
               (0, True, outer, True))
        expect("…and the folder, with all the command made in it, is gone after",
               (bool(made), os.path.exists(made)), (True, False))
        expect("the command's own exit code is passed back",
               cli("--", "sh", "-c", "exit 7").returncode, 7)

        repo = os.path.join(kept, "repo")
        os.makedirs(repo)
        subprocess.run(["git", "init", "-q", repo], check=True)
        open(os.path.join(repo, "a.txt"), "w").close()
        subprocess.run(["git", "-C", repo, "add", "a.txt"], check=True)
        subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@host.invalid",
                        "commit", "-q", "-m", "a"], check=True)
        r = cli("--label", "proof", "--copy", "HEAD", "--", "sh", "-c",
                'pwd; ls -a; test -d .git && test -z "$(ls -A .git)" && echo marker; '
                'echo "from=$SCRATCH_RUN_FROM"', cwd=repo)
        lines = r.stdout.split()
        expect("--copy runs the command in a copy of the ref, with an empty .git marker",
               (r.returncode, os.path.basename(lines[0]) if lines else None,
                "a.txt" in lines, "marker" in lines), (0, "tree", True, True))
        expect("…and says where it was started from, to bring a new test in",
               os.path.realpath(lines[-1][len("from="):]) if lines else None,
               os.path.realpath(repo))
        expect("…and the copy goes with the folder",
               os.path.exists(os.path.dirname(lines[0])) if lines else None, False)
        r = cli("--copy", "no-such-ref", "--", "true", cwd=repo)
        expect("a ref that cannot be copied is exit 2, said, and nothing is run or kept",
               (r.returncode, "could not copy" in r.stderr), (2, True))

        def stopped(sig, command, ready=None):
            """(exit, run folder, seconds from the signal to the end). With `ready`, the signal
            waits for the command to print that line: a trap it sets is then in place."""
            import time
            p = subprocess.Popen([sys.executable, me, "--parent", outer, "--"] + command,
                                 stderr=subprocess.PIPE, text=True)
            try:
                line = p.stderr.readline()
                run = line.split()[1] if line.startswith("scratch_run:") else ""
                while ready and p.stderr.readline().strip() not in (ready, ""):
                    pass
                sent = time.time()
                os.kill(p.pid, sig)
                p.communicate(timeout=60)
            finally:
                stop(p)
            return p.returncode, run, time.time() - sent
        running = ["sh", "-c", "echo ready >&2; exec sleep 30"]
        rc, run, took = stopped(signal.SIGTERM, running, ready="ready")
        expect("a SIGTERM is passed to the command at once, which ends, and then the folder goes",
               (rc, bool(run), os.path.exists(run), took < 5), (128 + signal.SIGTERM, True, False, True))
        rc, run, took = stopped(signal.SIGINT, running, ready="ready")
        expect("Ctrl-C the same", (rc, os.path.exists(run), took < 5), (128 + signal.SIGINT, False, True))

        # A signal while it copies: the command is never started, and the folder still goes.
        # A stand-in git marks when the copy starts, and takes three seconds over it.
        import time
        fake_bin = os.path.join(kept, "bin")
        os.makedirs(fake_bin)
        started, ran = os.path.join(kept, "copy-started"), os.path.join(kept, "command-ran")
        with open(os.path.join(fake_bin, "git"), "w") as fh:
            fh.write('#!/bin/sh\n: > "%s"\nsleep 3\nexec "%s" "$@"\n' % (started, shutil.which("git")))
        os.chmod(os.path.join(fake_bin, "git"), 0o755)
        p = subprocess.Popen([sys.executable, me, "--parent", outer, "--copy", "HEAD", "--",
                              "sh", "-c", ': > "%s"' % ran], cwd=repo,
                             env=dict(os.environ, PATH=fake_bin + os.pathsep + os.environ["PATH"]),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        err = ""
        try:
            deadline = time.time() + 30
            while not os.path.exists(started) and p.poll() is None and time.time() < deadline:
                time.sleep(0.05)
            os.kill(p.pid, signal.SIGTERM)
            err = p.communicate(timeout=60)[1]
        finally:
            stop(p)
        # The line it prints just before it starts the command is the sign it did not: a
        # command started now would be killed by the passed-on signal before it did a thing.
        expect("a signal while it copies starts nothing, and exits by that signal",
               (p.returncode, os.path.exists(started), "removed when the command ends" in err,
                os.path.exists(ran)), (128 + signal.SIGTERM, True, False, False))
        rc, run, took = stopped(signal.SIGTERM, ["sh", "-c", "trap '' TERM; echo ready >&2; sleep 30"],
                                ready="ready")
        expect("a command that ignores the signal is killed 10 s later, and the folder goes",
               (rc, os.path.exists(run), 10 <= took < 20), (128 + signal.SIGTERM, False, True))
        pidfile = os.path.join(kept, "bg.pid")
        r = cli("--", "sh", "-c", "sleep 30 >/dev/null 2>&1 & echo $! > %s" % pidfile)
        with open(pidfile) as fh:
            bg = int(fh.read())
        try:
            os.kill(bg, 0)
            bg_alive = True
        except ProcessLookupError:
            bg_alive = False
        expect("a process the command left running is stopped, and said",
               (r.returncode, bg_alive, "left a process running" in r.stderr), (0, False, True))
        expect("no command, no `--`, or a label that is not [a-z0-9-] is exit 2 with the usage, "
               "and makes nothing",
               [subprocess.run([sys.executable, me, "--parent", outer] + a, capture_output=True,
                               text=True).returncode
                for a in ([], ["--label", "x", "true"], ["--label", "--"],
                          ["--what", "x", "--", "true"], ["--label", "Fail_First", "--", "true"],
                          ["--label", "../up", "--", "true"])],
               [2, 2, 2, 2, 2, 2])

        expect("nothing is left under the parent but what the cases kept on purpose",
               sorted(os.listdir(outer)), ["keep-me"])

    if failures:
        print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print("\nall checks passed")
    return 0


# --------------------------------------------------------------------------- the command

USAGE = """usage: python3 scripts/scratch_run.py [--label NAME] [--parent DIR] [--copy REF] -- COMMAND...

  One folder for one run of COMMAND, removed when it ends, however it ends (KIT-459).
  COMMAND gets the folder as $SCRATCH_RUN_DIR, its own TMPDIR inside it, and the
  directory it was started from as $SCRATCH_RUN_FROM (to bring a new test into a copy).
  --label NAME   names the folder NAME-run-<random>, NAME in [a-z0-9-]   (default: scratch)
  --parent DIR   where to make it                           (default: the temp folder)
  --copy REF     put `git archive REF` of this repo in $SCRATCH_RUN_DIR/tree, with an
                 empty .git marker, and run COMMAND there   (a fail-first proof, a mutant)
Exit: COMMAND's own code; 128+N if a signal ended it; 2 for usage, a copy that failed,
or a folder that could not be removed."""


def _group_alive(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _stop_group(proc, first):
    """`first` to the command's whole group, then SIGKILL after 10 s. True if any was left."""
    import time
    if not _group_alive(proc.pid):
        proc.poll()
        return False
    for sig, grace in ((first, 10), (signal.SIGKILL, 5)):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            break
        deadline = time.time() + grace
        while time.time() < deadline:
            proc.poll()
            if not _group_alive(proc.pid):
                return True
            time.sleep(0.05)
    proc.poll()
    return True


def main(argv):
    import re
    import subprocess
    import sys

    if "--" not in argv or argv.index("--") == len(argv) - 1:
        print(USAGE, file=sys.stderr)
        return 2
    opts, command = argv[:argv.index("--")], argv[argv.index("--") + 1:]
    label, parent, ref = "scratch", None, None
    try:
        while opts:
            flag, value = opts[0], opts[1]
            if flag == "--label":
                # `-run-` and lower case: the shape the pipeline's sweep knows as the kit's own.
                if not re.match(r"^[a-z0-9][a-z0-9-]{0,40}$", value):
                    raise IndexError
                label = value
            elif flag == "--parent":
                parent = value
            elif flag == "--copy":
                ref = value
            else:
                raise IndexError
            opts = opts[2:]
    except IndexError:
        print(USAGE, file=sys.stderr)
        return 2
    received = []

    def forward(signum, frame):
        received.append(signum)
        if proc is not None:
            try:
                os.killpg(proc.pid, signum)
            except ProcessLookupError:
                pass
    proc = None
    with scratch_run(label + "-run-", parent=parent) as run:
        # Handlers of its own while the command runs: a signal is passed to the whole
        # command, which ends first, and only then does the folder go.
        old = {s: signal.signal(s, forward) for s in (signal.SIGINT,) + _SIGNALS}
        try:
            cwd = None
            if ref is not None:
                cwd = os.path.join(run, "tree")
                os.makedirs(cwd)
                archive = subprocess.run(["git", "archive", ref], capture_output=True)
                if archive.returncode != 0 or subprocess.run(
                        ["tar", "-x", "-C", cwd], input=archive.stdout).returncode != 0:
                    print("scratch_run: could not copy %s: %s" % (
                        ref, archive.stderr.decode("utf-8", "replace").strip()), file=sys.stderr)
                    return 2
                os.makedirs(os.path.join(cwd, ".git"))   # cases that refuse a worktree need one
            tmp = os.path.join(run, "tmp")
            os.makedirs(tmp)
            env = dict(os.environ, SCRATCH_RUN_DIR=run, SCRATCH_RUN_FROM=os.getcwd(),
                       TMPDIR=tmp, TEMP=tmp, TMP=tmp)
            if received:                       # stopped while it copied: start nothing
                return 128 + received[0]
            print("scratch_run: %s (removed when the command ends)" % run, file=sys.stderr,
                  flush=True)
            try:
                proc = subprocess.Popen(command, cwd=cwd, env=env, start_new_session=True)
            except OSError as exc:
                print("scratch_run: could not start %s: %s" % (command[0], exc), file=sys.stderr)
                return 2
            if received:                       # it came while the command started: pass it on
                try:
                    os.killpg(proc.pid, received[0])
                except ProcessLookupError:
                    pass
            # A command that ignores the signal it was passed is killed 10 s later, so the
            # folder never waits on it forever.
            import time
            signalled = None
            while True:
                try:
                    code = proc.wait(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    if received and signalled is None:
                        signalled = time.time()
                    if signalled is not None and time.time() - signalled > 10:
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
            if _stop_group(proc, signal.SIGTERM):
                print("scratch_run: the command left a process running; it was stopped",
                      file=sys.stderr)
        finally:
            for s, h in old.items():
                signal.signal(s, h)
    if os.path.lexists(run):
        print("scratch_run: could not remove %s" % run, file=sys.stderr)
        return code or 2
    if received:
        return 128 + received[0]
    return code if code >= 0 else 128 - code


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["--selftest"]:
        # Its own cases run inside one run folder too: a SIGTERM anywhere in them still
        # removes everything they made.
        sys.exit(contained(selftest, "scratch-run-selftest-run-"))
    sys.exit(main(sys.argv[1:]))
