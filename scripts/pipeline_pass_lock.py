#!/usr/bin/env python3
"""One lock for one pass of one job (KIT-681).

    import pipeline_pass_lock as pass_lock
    return pass_lock.exclusive(state_dir, "review-poller", lambda: run(...), busy_rc=3, say=log)

WHY. A job that the scheduler starts every few minutes can also be started by hand, and a hand-run
pass that overlaps the scheduled one would post a review twice, ping twice or start a piece twice.
Each job takes this lock for the whole of a pass that writes anything. A second pass that finds it
held does nothing, says so in one fixed sentence, and exits with the job's own "declined" code, the
one that already means "nothing was done, and nothing failed". It never waits, because the next
scheduled pass is minutes away and a queue of waiting passes is the same collision, later.

WHAT IT IS NOT. A pass that cannot make or open the lock file does not run: that is a failure
(exit through `fail_rc`), never a silent "no lock". Dry runs write nothing, so a job passes
`skip=True` for them and they run beside a live pass.

One lock per job, in that job's own state folder: `<state_dir>/<job>.pass.lock`. The lock is an
`fcntl.flock`, so it dies with its process: a pass that crashes leaves no stuck lock.

Standard library only. Run it bare for its own selftest: `python3 scripts/pipeline_pass_lock.py --selftest`.
"""
import os
import sys

BUSY_SAYS = "another pass of %s is already running, so this one did nothing"


def lock_path(state_dir, job):
    return os.path.join(state_dir, "%s.pass.lock" % job)


def take(state_dir, job):
    """The open, locked file, or None while another pass holds it. Raises OSError when the lock
    file cannot be made or opened (the caller treats that as a failure, not as free)."""
    import fcntl
    os.makedirs(state_dir, mode=0o700, exist_ok=True)
    handle = open(lock_path(state_dir, job), "a")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, PermissionError):
        handle.close()
        return None
    except OSError:
        handle.close()
        raise
    return handle


def release(handle):
    if handle is not None:
        handle.close()          # closing the file drops the flock


def exclusive(state_dir, job, fn, busy_rc, fail_rc=1, say=None, skip=False):
    """Run `fn()` holding the pass lock and return its result. While another pass holds it, run
    nothing, say BUSY_SAYS and return `busy_rc`. A lock that cannot be made returns `fail_rc`.
    `skip` runs `fn()` with no lock (a dry run, which writes nothing)."""
    say = say or (lambda line: sys.stderr.write(line + "\n"))
    if skip:
        return fn()
    try:
        handle = take(state_dir, job)
    except OSError as exc:
        say("FAIL: the pass lock of %s could not be taken (%s), so nothing ran" % (job, exc))
        return fail_rc
    if handle is None:
        say(BUSY_SAYS % job)
        return busy_rc
    try:
        return fn()
    finally:
        release(handle)


def selftest():
    import tempfile
    failures = []

    def expect(name, got, want):
        if got != want:
            failures.append("%s (got %r, want %r)" % (name, got, want))

    with tempfile.TemporaryDirectory(prefix="pass-lock-") as tmp:
        state = os.path.join(tmp, "state")
        said, ran = [], []
        first = take(state, "job")
        expect("a free lock is taken", first is not None, True)
        expect("the state folder is private", oct(os.stat(state).st_mode & 0o777), "0o700")
        expect("a second take while held is None", take(state, "job"), None)
        other = take(state, "other")
        expect("another job's lock is its own", other is not None, True)
        release(other)
        rc = exclusive(state, "job", lambda: ran.append(1) or 0, busy_rc=3, say=said.append)
        expect("a busy pass returns the busy code", rc, 3)
        expect("a busy pass runs nothing", ran, [])
        expect("a busy pass says the fixed sentence", said, [BUSY_SAYS % "job"])
        release(first)
        rc = exclusive(state, "job", lambda: ran.append(1) or 7, busy_rc=3, say=said.append)
        expect("a free pass runs and returns its own code", (rc, ran), (7, [1]))
        again = take(state, "job")
        expect("the lock is released after the pass", again is not None, True)
        # A pass whose function raises still lets go.
        release(again)
        try:
            exclusive(state, "job", lambda: 1 / 0, busy_rc=3, say=said.append)
        except ZeroDivisionError:
            pass
        again = take(state, "job")
        expect("the lock is released after a raise", again is not None, True)
        # skip: a dry run runs beside a live pass.
        rc = exclusive(state, "job", lambda: 5, busy_rc=3, say=said.append, skip=True)
        expect("a skipped lock runs while another holds it", rc, 5)
        release(again)
        # a lock that cannot be made is a failure, not a free pass.
        blocker = os.path.join(tmp, "file")
        with open(blocker, "w") as fh:
            fh.write("x")
        lines = []
        ran2 = []
        rc = exclusive(os.path.join(blocker, "sub"), "job", lambda: ran2.append(1) or 0, busy_rc=3,
                       fail_rc=1, say=lines.append)
        expect("an unmakeable lock is a failure that runs nothing", (rc, ran2), (1, []))
        expect("...and says why", len(lines) == 1 and lines[0].startswith("FAIL:"), True)
    if failures:
        print("pipeline_pass_lock selftest: FAIL")
        for f in failures:
            print("  -", f)
        return 1
    print("pipeline_pass_lock selftest: OK")
    return 0


if __name__ == "__main__":
    if "--selftest" not in sys.argv[1:]:
        sys.exit(2)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from scratch_run import contained       # its temp folders go when it ends
    sys.exit(contained(selftest, "pass-lock-selftest-run-"))
