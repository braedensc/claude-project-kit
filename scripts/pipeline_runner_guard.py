#!/usr/bin/env python3
"""The launcher for a session's guards, and the check on the engine's folder (KIT-704;
docs/RUNNER.md, "Guards from the engine's own folder").

    /usr/bin/python3 -I scripts/pipeline_runner_guard.py --workspace <folder> --hook pre-tool-use
    python3 scripts/pipeline_runner_guard.py --selftest

WHY.  A session the runner starts loads nothing from its workspace (KIT-630, row r1), so the
kit's own guard has to come from somewhere the session cannot write: the engine's folder, the kit
copy the runner itself runs from. Run from there, the guard has a problem of its own. It anchors
on CLAUDE_PROJECT_DIR, and without it on its own file's folder: the engine, not the session's
workspace. Then it judges the wrong checkout and lets an edit through (a branch check outside a
repository fails open). So a guard that cannot tell which folder it guards must refuse.

WHAT IT DOES.  The runner writes this file's command line into the run's settings, outside the
workspace, with the workspace named in it. On each tool call the program runs it with the call on
stdin. It runs the kit's guard (`.claude/hooks/<hook>.py` in the engine's folder) with
CLAUDE_PROJECT_DIR set to that workspace and DEVELOPER_DIR removed, and passes on what the guard
said. It refuses the call (exit 2, which blocks it) when:
  - no workspace is named, or the one named is not an exact absolute path to the top of a git
    working tree;
  - CLAUDE_PROJECT_DIR names another folder: two answers to which folder it guards;
  - the guard is not there;
  - the guard ends in anything but an allow (0) or its own refusal (2), or does not end in time:
    the program would read a crash as "go ahead".

THE ENGINE'S FOLDER.  engine_problems() names each place a session's walls come from that the
account the sessions run as could change: the engine's folder and every folder above it, and every
level of its scripts, its kinds' data, its guards and its sandbox profiles. A file or folder that
account owns, a link, or one that other accounts may write (a folder above the engine with the
sticky bit excepted) is a problem. The runner starts no session while there is one. On this Mac
the engine is a root-owned copy of the kit, made by the runner's installer when a lane moves.

Standard library only.
"""
import argparse
import json
import os
import stat
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
EXIT_ALLOW, EXIT_BLOCK = 0, 2
HOOKS = {"pre-tool-use": "pre-tool-use.py"}
PYTHON = "/usr/bin/python3"
TIMEOUT = 8                          # under the hook's own 10 s, so this refusal is the one heard
PAYLOAD_MAX = 64 << 20
ENGINE_PARTS = ("scripts", "session-kinds", os.path.join(".claude", "hooks"),
                os.path.join("templates", "sandbox"))
SAID_MAX = 3
LOOK_MAX = 20000                     # entries looked at in the engine's parts; past it, a problem


def blocked(words):
    sys.stderr.write("[runner guard] BLOCKED: %s\n" % words)
    return EXIT_BLOCK


def workspace_problem(path):
    """Why `path` cannot be the folder a guard guards, or ''."""
    if not (isinstance(path, str) and os.path.isabs(path)):
        return "no absolute workspace was named"
    if os.path.realpath(path) != os.path.normpath(path):
        return "the workspace named is not its own real path"
    if not os.path.isdir(path):
        return "the workspace named is not a folder"
    if not os.path.lexists(os.path.join(path, ".git")):
        return "the workspace named is not the top of a git working tree"
    return ""


def main(argv=None, stdin=None, environ=None, engine=None, timeout=TIMEOUT):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--selftest"]:
        return selftest()
    environ = os.environ if environ is None else environ
    engine = engine or ENGINE
    ap = argparse.ArgumentParser(prog="pipeline_runner_guard.py", add_help=False)
    ap.add_argument("--workspace")
    ap.add_argument("--hook")
    try:
        args, rest = ap.parse_known_args(argv)
    except SystemExit:
        return blocked("its command line could not be read, so it cannot tell which folder it guards")
    if rest:
        return blocked("its command line holds words it does not know, so nothing was checked")
    if args.hook not in HOOKS:
        return blocked("its command line names no guard it knows, so nothing was checked")
    why = workspace_problem(args.workspace)
    if why:
        return blocked("it cannot tell which folder it guards: %s." % why)
    workspace = args.workspace
    named = environ.get("CLAUDE_PROJECT_DIR")
    if named and os.path.realpath(named) != workspace:
        return blocked("CLAUDE_PROJECT_DIR names another folder than the workspace it was given: "
                       "two answers to which folder it guards.")
    guard = os.path.join(engine, ".claude", "hooks", HOOKS[args.hook])
    if not os.path.isfile(guard):
        return blocked("the kit's guard is not in the engine's folder, so nothing was checked.")
    stream = stdin if stdin is not None else sys.stdin.buffer
    payload = stream.read(PAYLOAD_MAX + 1)
    if len(payload) > PAYLOAD_MAX:
        return blocked("the tool call is larger than it will hand on.")
    env = dict((k, v) for k, v in environ.items() if k != "DEVELOPER_DIR")
    env["CLAUDE_PROJECT_DIR"] = workspace
    try:
        done = subprocess.run([PYTHON, "-I", guard], input=payload, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return blocked("the kit's guard did not answer in %d seconds." % timeout)
    except OSError as exc:
        return blocked("the kit's guard could not start (%s)." % type(exc).__name__)
    if done.returncode not in (EXIT_ALLOW, EXIT_BLOCK):
        sys.stderr.write(done.stderr.decode("utf-8", "replace")[-2000:])
        return blocked("the kit's guard ended without an answer (exit %s), which counts as a refusal."
                       % done.returncode)
    _out(sys.stdout, done.stdout)
    _out(sys.stderr, done.stderr)
    return done.returncode


def _out(stream, data):
    buf = getattr(stream, "buffer", None)
    if buf is not None:
        buf.write(data)
        buf.flush()
    else:
        stream.write(data.decode("utf-8", "replace"))


# --------------------------------------------------------------------------- #
# The engine's folder
# --------------------------------------------------------------------------- #
def _why(path, st, uid, is_root_or_above):
    if stat.S_ISLNK(st.st_mode):
        return "is a link, so what it names can change"
    if st.st_uid == uid:
        return "is this account's own, so a session running as this account could change it"
    sticky_dir = stat.S_ISDIR(st.st_mode) and st.st_mode & stat.S_ISVTX
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH) and not (sticky_dir and is_root_or_above):
        return "may be written by other accounts"
    return ""


def engine_problems(root=None, uid=None):
    """Each place a session's walls come from that the sessions' account could change, as words.
    Empty when there is none."""
    root = os.path.realpath(root or ENGINE)
    uid = os.getuid() if uid is None else uid
    looks = []
    up = root
    while True:
        looks.append((up, True))
        parent = os.path.dirname(up)
        if parent == up:
            break
        up = parent
    for part in ENGINE_PARTS:
        folder = os.path.join(root, part)
        looks.append((folder, False))
        # Every level: a writable file one folder down (a __pycache__ the runner imports from, say)
        # is as open as one at the top. A link is looked at, never followed.
        for top, dirs, names in os.walk(folder, followlinks=False):
            dirs.sort()
            looks += [(os.path.join(top, n), False) for n in sorted(dirs) + sorted(names)]
            if len(looks) > LOOK_MAX:
                break
    if len(looks) > LOOK_MAX:
        return ["the engine's folder holds more than %d entries, too many to check" % LOOK_MAX]
    found = []
    for path, above in looks:
        try:
            st = os.lstat(path)
        except OSError as exc:
            found.append("%s could not be checked (%s)" % (path, type(exc).__name__))
            continue
        why = _why(path, st, uid, above)
        if why:
            found.append("%s %s" % (path, why))
    if len(found) > SAID_MAX:
        found = found[:SAID_MAX] + ["and %d more" % (len(found) - SAID_MAX)]
    return found


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
STANDIN_GUARD = r'''import json, os, sys
d = json.load(sys.stdin)
open(os.path.join(os.environ["STANDIN_OUT"], "seen.json"), "w").write(json.dumps(
    {"project": os.environ.get("CLAUDE_PROJECT_DIR"), "developer_dir": os.environ.get("DEVELOPER_DIR"),
     "tool": d.get("tool_name")}))
tool = d.get("tool_name")
if tool == "Edit":
    sys.stderr.write("STAND-IN GUARD: no\n")
    sys.exit(2)
if tool == "Crash":
    raise RuntimeError("boom")
if tool == "Sleep":
    import time
    time.sleep(30)
print("{}")
sys.exit(0)
'''


def selftest():
    from scratch_run import contained
    return contained(_selftest, "runner-guard-selftest-")


def _selftest():  # noqa: C901  (one battery, read top to bottom)
    import io
    import tempfile
    failures, counted = [], [0]

    def expect(name, got, want):
        counted[0] += 1
        if got != want:
            failures.append("%s: got %r, want %r" % (name, got, want))

    tmp = os.path.realpath(tempfile.mkdtemp(prefix="guard-"))
    engine = os.path.join(tmp, "engine")
    for part in ENGINE_PARTS:
        os.makedirs(os.path.join(engine, part))
    with open(os.path.join(engine, ".claude", "hooks", "pre-tool-use.py"), "w") as fh:
        fh.write(STANDIN_GUARD)
    with open(os.path.join(engine, "scripts", "pipeline_runner.py"), "w") as fh:
        fh.write("# a stand-in\n")
    with open(os.path.join(engine, "session-kinds", "code.json"), "w") as fh:
        fh.write("{}\n")
    for top, dirs, names in os.walk(engine):            # whatever the umask: no one else writes it
        os.chmod(top, 0o755)
        for n in names:
            os.chmod(os.path.join(top, n), 0o644)
    ws = os.path.join(tmp, "work", "code", "ENG-1")
    os.makedirs(os.path.join(ws, ".git"))
    genv = {"PATH": "/usr/bin:/bin", "STANDIN_OUT": tmp, "DEVELOPER_DIR": "/somewhere/else", "LANG": "C"}

    def call(argv, tool="Read", environ=None, eng=engine, timeout=TIMEOUT):
        err, out = io.StringIO(), io.StringIO()
        real = sys.stderr, sys.stdout
        sys.stderr, sys.stdout = err, out
        try:
            code = main(argv, stdin=io.BytesIO(json.dumps({"tool_name": tool}).encode()),
                        environ=dict(genv, **(environ or {})), engine=eng, timeout=timeout)
        finally:
            sys.stderr, sys.stdout = real
        return code, err.getvalue()

    def seen():
        with open(os.path.join(tmp, "seen.json")) as fh:
            return json.load(fh)

    good = ["--workspace", ws, "--hook", "pre-tool-use"]
    code, _ = call(good)
    expect("an allowed call: the guard ran, anchored on the workspace, with no DEVELOPER_DIR",
           (code, seen()), (0, {"project": ws, "developer_dir": None, "tool": "Read"}))
    code, err = call(good, tool="Edit")
    expect("the guard's own refusal is passed on", (code, "STAND-IN GUARD: no" in err), (2, True))
    code, _ = call(good, environ={"CLAUDE_PROJECT_DIR": ws})
    expect("CLAUDE_PROJECT_DIR naming the same workspace is fine", code, 0)
    for what, argv, environ, want in (
            ("no workspace named", ["--hook", "pre-tool-use"], None,
             "it cannot tell which folder it guards: no absolute workspace was named."),
            ("a relative workspace", ["--workspace", "work/code/ENG-1", "--hook", "pre-tool-use"], None,
             "it cannot tell which folder it guards: no absolute workspace was named."),
            ("a folder that is not a working tree's top", ["--workspace", os.path.dirname(ws), "--hook", "pre-tool-use"],
             None, "it cannot tell which folder it guards: the workspace named is not the top of a git working tree."),
            ("a workspace that is not there", ["--workspace", ws + "-gone", "--hook", "pre-tool-use"], None,
             "it cannot tell which folder it guards: the workspace named is not a folder."),
            ("two answers to which folder", good, {"CLAUDE_PROJECT_DIR": tmp},
             "CLAUDE_PROJECT_DIR names another folder than the workspace it was given: two answers to which "
             "folder it guards."),
            ("a guard it does not know", ["--workspace", ws, "--hook", "stop"], None,
             "its command line names no guard it knows, so nothing was checked"),
            ("a word it does not know", good + ["--loose"], None,
             "its command line holds words it does not know, so nothing was checked")):
        code, err = call(argv, environ=environ)
        expect("refused: %s" % what, (code, err.strip().splitlines()[-1]), (2, "[runner guard] BLOCKED: " + want))
    code, err = call(good, tool="Crash")
    expect("a guard that crashes is a refusal, never a go-ahead",
           (code, err.strip().splitlines()[-1]),
           (2, "[runner guard] BLOCKED: the kit's guard ended without an answer (exit 1), which counts as a refusal."))
    code, err = call(good, tool="Sleep", timeout=1)
    expect("a guard that does not answer in time is a refusal",
           (code, err.strip()), (2, "[runner guard] BLOCKED: the kit's guard did not answer in 1 seconds."))
    code, err = call(good, eng=os.path.join(tmp, "no-engine"))
    expect("no guard in the engine's folder: a refusal",
           (code, err.strip()), (2, "[runner guard] BLOCKED: the kit's guard is not in the engine's folder, so "
                                    "nothing was checked."))

    # The kit's own guard, through the launcher, against a workspace on main: refused, in its
    # own words. Without the launcher, run from a folder that is not the workspace, it looks at
    # the wrong checkout and lets the same edit through.
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    genv_git = {"PATH": "/usr/bin:/bin", "HOME": tmp, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "LANG": "C", "GIT_AUTHOR_NAME": "kit selftest", "GIT_AUTHOR_EMAIL": "selftest@example.invalid",
                "GIT_COMMITTER_NAME": "kit selftest", "GIT_COMMITTER_EMAIL": "selftest@example.invalid"}
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main", repo], env=genv_git, check=True)
    with open(os.path.join(repo, "README.md"), "w") as fh:
        fh.write("one\n")
    for argv in (["git", "-C", repo, "add", "README.md"], ["git", "-C", repo, "commit", "--quiet", "-m", "seed"]):
        subprocess.run(argv, env=genv_git, check=True, capture_output=True)
    payload = json.dumps({"tool_name": "Edit", "cwd": repo, "session_id": "s", "hook_event_name": "PreToolUse",
                          "tool_input": {"file_path": os.path.join(repo, "README.md"), "old_string": "one",
                                         "new_string": "two"}}).encode()
    kit_guard = os.path.join(ENGINE, ".claude", "hooks", "pre-tool-use.py")
    if os.path.isfile(kit_guard):
        err, out = io.StringIO(), io.StringIO()
        real = sys.stderr, sys.stdout
        sys.stderr, sys.stdout = err, out
        try:
            code = main(["--workspace", repo, "--hook", "pre-tool-use"], stdin=io.BytesIO(payload),
                        environ=genv_git, engine=ENGINE, timeout=30)
        finally:
            sys.stderr, sys.stdout = real
        words = err.getvalue()
        expect("through the launcher, the kit's guard refuses an edit on the workspace's main, in its own words",
               (code, "BLOCKED" in words and "main" in words), (2, True))
        direct = subprocess.run([PYTHON, "-I", kit_guard], input=payload, capture_output=True, env=genv_git,
                                cwd=tmp, timeout=30)
        expect("…while run on its own with no anchor it judges another folder, and lets the edit through",
               direct.returncode, 0)

        # KIT-886: a code, fix or conflict run holds the push token. On its own branch, through the
        # launcher, the kit's guard still refuses a push to main, a bare force push and a merge.
        subprocess.run(["git", "-C", repo, "checkout", "--quiet", "-b", "feat/eng-1-push-token"], env=genv_git,
                       check=True, capture_output=True)
        holding = dict(genv_git, GH_TOKEN="fake-push-token-for-the-selftest")
        for what, command, words in (
                ("refuses a push to main", "git push origin HEAD:main", "Pushing to main/master is not allowed"),
                ("refuses a bare force push", "git push --force origin HEAD", "Bare --force/-f push is blocked"),
                ("refuses a merge", "gh pr merge 7 --squash", "`gh pr merge` (including --auto) is not allowed"),
                ("lets the run's own push through", "git push -u origin feat/eng-1-push-token", None)):
            bash = json.dumps({"tool_name": "Bash", "cwd": repo, "session_id": "s", "hook_event_name": "PreToolUse",
                               "tool_input": {"command": command, "description": "x"}}).encode()
            err, out = io.StringIO(), io.StringIO()
            real = sys.stderr, sys.stdout
            sys.stderr, sys.stdout = err, out
            try:
                code = main(["--workspace", repo, "--hook", "pre-tool-use"], stdin=io.BytesIO(bash),
                            environ=holding, engine=ENGINE, timeout=30)
            finally:
                sys.stderr, sys.stdout = real
            expect("in a run that holds the push token, the kit's guard %s, in its own words" % what,
                   (code, words in err.getvalue()) if words else code, (2, True) if words else 0)
    else:
        print("  (skipped: no kit guard at %s)" % kit_guard)

    # The engine's folder.
    other = os.getuid() + 1
    expect("an engine no other account may write, owned by another account: no problem",
           engine_problems(engine, uid=other), [])
    mine = engine_problems(engine, uid=os.getuid())
    expect("an engine this account owns is a problem, said for each place, at most three and a count",
           (len(mine), mine[0].endswith("is this account's own, so a session running as this account could "
                                        "change it"), mine[-1].startswith("and ")), (4, True, True))
    os.chmod(os.path.join(engine, "session-kinds", "code.json"), 0o666)
    expect("a kind's data any account may write is a problem",
           engine_problems(engine, uid=other),
           ["%s may be written by other accounts" % os.path.join(engine, "session-kinds", "code.json")])
    os.chmod(os.path.join(engine, "session-kinds", "code.json"), 0o644)
    cache = os.path.join(engine, "scripts", "__pycache__")
    os.makedirs(cache)
    with open(os.path.join(cache, "pipeline_runner.cpython-39.pyc"), "w") as fh:
        fh.write("x")
    os.chmod(cache, 0o755)
    os.chmod(os.path.join(cache, "pipeline_runner.cpython-39.pyc"), 0o666)
    expect("a file one folder down, which the runner may import, is looked at too",
           engine_problems(engine, uid=other),
           ["%s may be written by other accounts" % os.path.join(cache, "pipeline_runner.cpython-39.pyc")])
    os.chmod(os.path.join(cache, "pipeline_runner.cpython-39.pyc"), 0o644)
    profile = os.path.join(engine, "templates", "sandbox", "look.sb")
    os.makedirs(os.path.dirname(profile), exist_ok=True)
    with open(profile, "w") as fh:
        fh.write("(version 1)\n")
    os.chmod(profile, 0o666)
    expect("…and so are the sandbox profiles",
           engine_problems(engine, uid=other), ["%s may be written by other accounts" % profile])
    os.chmod(profile, 0o644)
    os.symlink(os.path.join(tmp, "elsewhere.py"), os.path.join(engine, "scripts", "pipeline_harness_claude.py"))
    expect("a link in the engine's folder is a problem",
           engine_problems(engine, uid=other),
           ["%s is a link, so what it names can change" % os.path.join(engine, "scripts", "pipeline_harness_claude.py")])
    os.unlink(os.path.join(engine, "scripts", "pipeline_harness_claude.py"))
    os.rename(os.path.join(engine, "session-kinds"), os.path.join(tmp, "moved"))
    expect("a part that is not there could not be checked, so it is a problem",
           engine_problems(engine, uid=other),
           ["%s could not be checked (FileNotFoundError)" % os.path.join(engine, "session-kinds")])
    expect("a folder above the engine that this account owns is a problem",
           any(p.startswith(tmp + " is this account's own") for p in engine_problems(engine, uid=os.getuid())), True)

    if failures:
        print("FAIL: pipeline_runner_guard selftest")
        for f in failures:
            print("  - " + f)
        return 1
    print("OK: pipeline_runner_guard selftest (%d checks)" % counted[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
