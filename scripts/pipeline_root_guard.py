#!/usr/bin/env python3
"""One refusal for every installer that must not run as root (KIT-693).

    import pipeline_root_guard as rg
    msg = rg.refusal()          # None, or the one message to print before changing anything

WHY. An installer run as root writes settings and state owned by root, in the owner's or the
role account's folders. Later passes, run as that person or account, then cannot read or replace
them. Installers that need root for one step ask for it themselves (sudo, once, with a reason);
none of them is meant to START as root. So each refuses at its entry, before it reads its conf
or changes a thing, with this one message. The check is here and nowhere else: an installer
calls `refusal()`, so the rule, its wording and its test hook cannot drift between them.

THE TEST HOOK is `_uid`: a selftest replaces `pipeline_root_guard._uid` with `lambda: 0` to be
root, and puts it back. Nothing is read from the environment: a person cannot be argued out of
being themselves by a variable.

Standard library only. Run it bare for its own selftest: `python3 scripts/pipeline_root_guard.py --selftest`.
"""
import os
import sys

MESSAGE = ("REFUSED: do not run this as root or with sudo. Run it as yourself, in your own terminal: "
           "it asks for your Mac password only when a step needs it. Run as root it would write "
           "files that belong to root in your folders, which the later passes cannot read or "
           "replace. Nothing was changed.")


def _uid():
    return os.geteuid()


def refusal(uid=None):
    """None for a person, or MESSAGE when the user is root. `uid` is for an installer that
    already reads the uid through its own injectable seam; without it the process's own is read."""
    return MESSAGE if (_uid() if uid is None else uid) == 0 else None


# The installers that call `refusal()` with no argument, and one command each that parses without a
# conf. Run as root (the uid stubbed), each must exit 3 with MESSAGE and touch nothing. The ones that
# read the uid through their own injectable seam (pipeline_install, pipeline_model_switch,
# pipeline_stage_e_app, pipeline_conflict_waker_setup) pass it in, and their own selftests hold the
# root case; SEAMED below only makes sure they still call this helper.
INSTALLERS = (
    ("pipeline_sweep_setup", ["run"]),
    ("pipeline_queue_setup", ["run"]),
    ("pipeline_acceptance_setup", ["run"]),
    ("pipeline_watcher_setup", ["run"]),
    ("pipeline_bridge_setup", ["install"]),
    ("pipeline_notifier_setup", ["run"]),
    ("pipeline_stage_a_setup", ["run"]),
    ("pipeline_stage_e_setup", ["run"]),
    ("pipeline_chat_lane_setup", ["verify"]),
)
SEAMED = ("pipeline_install", "pipeline_model_switch", "pipeline_stage_e_app", "pipeline_conflict_waker_setup")


def _installers_as_root(expect):
    """Every installer, run as root from an empty folder with an empty home: it refuses, and the
    folder is still empty. Needs scratch_run (run from the scripts folder)."""
    import contextlib
    import importlib
    import io
    import tempfile
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    # The installers import this file by name, so the stub goes on THAT copy (run bare, this
    # file is __main__ and a second copy).
    shared = importlib.import_module("pipeline_root_guard")
    real_uid, cwd, home = shared._uid, os.getcwd(), os.environ.get("HOME")
    for name, argv in INSTALLERS:
        with tempfile.TemporaryDirectory(prefix="root-guard-") as tmp:
            mod = importlib.import_module(name)
            out, err = io.StringIO(), io.StringIO()
            os.chdir(tmp)
            os.environ["HOME"] = tmp
            shared._uid = lambda: 0
            try:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    rc = mod.main(argv)
            except SystemExit as e:
                rc = e.code
            finally:
                shared._uid = real_uid
                os.chdir(cwd)
                if home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = home
            expect("%s as root: exit 3" % name, rc, 3)
            expect("%s as root: says the one message" % name, MESSAGE in out.getvalue() + err.getvalue(), True)
            expect("%s as root: changed nothing" % name, os.listdir(tmp), [])
    for name in [n for n, _ in INSTALLERS] + list(SEAMED):
        with open(os.path.join(here, name + ".py"), encoding="utf-8") as f:
            expect("%s calls the shared refusal" % name, "pipeline_root_guard" in f.read(), True)


def selftest():
    global _uid
    failures = []

    def expect(name, got, want):
        if got != want:
            failures.append("%s (got %r, want %r)" % (name, got, want))

    real = _uid
    try:
        _uid = lambda: 501
        expect("a person is not refused", refusal(), None)
        _uid = lambda: 0
        expect("root is refused with the one message", refusal(), MESSAGE)
        expect("the message says nothing was changed", "Nothing was changed" in MESSAGE, True)
    finally:
        _uid = real
    expect("uid 0 passed in is refused", refusal(0), MESSAGE)
    expect("a person's uid passed in is not", refusal(501), None)
    _installers_as_root(expect)
    if failures:
        print("pipeline_root_guard selftest: FAIL")
        for f in failures:
            print("  -", f)
        return 1
    print("pipeline_root_guard selftest: OK")
    return 0


if __name__ == "__main__":
    if "--selftest" not in sys.argv[1:]:
        sys.exit(2)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from scratch_run import contained       # its temp folders go when it ends
    sys.exit(contained(selftest, "root-guard-selftest-run-"))
