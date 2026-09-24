#!/usr/bin/env python3
"""The one command: bring this Mac's pipeline up to date, then install the idea gate.

    python3 scripts/pipeline_install.py

Run it in a terminal, as yourself, from the kit checkout you install from. It does
everything a machine can do and stops only for what a person must do. Run it again after
any stop: it picks up where it left off.

WHAT IT DOES, IN ORDER
  1. The kit checkout. It must be on its default branch with no uncommitted changes to
     tracked files. It fast-forwards it to origin, and when that moved the code it starts
     itself again, so the rest runs on what you merged.
  2. The skills. Claude Code loads skills from your home folder for any project without
     its own, and nothing updates that copy (`scripts/sync_user_skills.py`). When they
     differ from the kit's, it asks before replacing them.
  3. Stage E, the review jobs, when this Mac has them (`stage-e.conf`). Its read-only
     check runs first. Only when something is outstanding does it ask, then run the
     review installer — which can restart the dispatcher, so it says so first.
  4. The idea gate (`scripts/pipeline_stage_a_setup.py run`). On a first run it writes
     its own settings from what the review installer's settings already say. Then it
     works through its steps, asking before each one that changes something a person
     should see: the pull request that switches plans on in each planned repository,
     the dispatcher's settings, the probe tickets, starting the job, the routing drill.

WHAT IT NEVER DOES
  It never merges, approves or labels anything, and never signs a sign-off you did not
  type. Each installer it runs refuses in an agent environment; so does this one, before
  it reads anything. A Claude session that could run it could install its own
  supervisor. That is the whole reason for the refusal, and there is no override.

  Where it runs: the macOS Terminal app works, and so does the Claude desktop app's
  Terminal tab (measured 2026-09-24: none of the markers below is set there). The `!`
  prefix inside a Claude Code session does NOT: that shell is the session's own.

Usage:
    pipeline_install.py [--conf stage-a.conf] [--stage-e-conf stage-e.conf]
                        [--no-pull] [--skip-stage-e]
    pipeline_install.py --selftest

Exit: 0 everything is installed and proven · 10 waiting on you (the lines above say
      what; run it again afterwards) · 2 this checkout or its settings need fixing first
      · 3 refused (an agent environment) · any other code is the installer's own,
      passed through with a sentence saying which one stopped.
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
# The agent-env markers are IMPORTED from the dispatcher module, never copied, so every
# script in the kit means the same thing by "an agent environment".
from pipeline_dispatch_local import AGENT_ENV_MARKERS  # noqa: E402

EX_OK, EX_USAGE, EX_REFUSED, EX_BLOCKED = 0, 2, 3, 10

SKILLS = os.path.join(HERE, "sync_user_skills.py")
STAGE_E = os.path.join(HERE, "pipeline_stage_e_setup.py")
STAGE_A = os.path.join(HERE, "pipeline_stage_a_setup.py")

# What each installer's exit code means, in a sentence a person can act on. The codes are
# the installers' own (their docstrings); an unknown one is passed through as it is.
STAGE_E_MEANS = {
    10: "the review installer printed a card: one thing only you can do. Do what it says, "
        "then run this command again.",
    3: "the review installer refused: it saw a Claude session. Use the Terminal app.",
    4: "a review step could not measure itself. Do what its 'What clears it' lines say, "
       "then run this command again.",
    5: "the review installer needs your Mac password and did not get it. Run this again "
       "and type it at the prompt.",
    2: "the review installer's settings (stage-e.conf) have a problem; every bad value is "
       "listed above.",
}
STAGE_A_MEANS = {
    0: "The idea gate is installed, running and proven.",
    10: "The idea gate is waiting on you. What for is printed just above. Do it, then run "
        "this same command again: it carries on from there.",
    4: "An idea-gate step could not measure itself. Do what its 'What clears it' lines say, "
       "then run this same command again.",
    3: "The idea-gate installer refused: it saw a Claude session. Use the Terminal app.",
    2: "The idea gate's settings file has a problem. Every bad value is listed above.",
    5: "The idea-gate installer needs your Mac password and did not get it. Run this again "
       "and type it at the prompt.",
    1: "An idea-gate step failed. The FAILED lines above say why; read them before running "
       "anything else.",
}


class Io(object):
    """Everything this command does to the outside world, in one seam, so --selftest can
    walk every path with no machine: running a program, asking a person, restarting."""

    def run(self, argv, capture=False):
        """(exit code, output). Streams are inherited unless `capture`: an installer's
        prompts and cards are for the person, not for this program.

        CTRL-C BELONGS TO THE INSTALLER, NOT TO THIS WRAPPER. Ctrl-C reaches every process
        on the terminal. Left alone, this wrapper would give the installer a quarter of a
        second and then kill it — in the middle of the drill putting Cyrus's settings back,
        or of a restart. So this process ignores it while an installer runs, the installer
        gets it with the default handling restored, and its own clean-up runs to the end."""
        import signal
        try:
            if capture:
                proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      cwd=KIT, timeout=120)
                return proc.returncode, proc.stdout.decode("utf-8", "replace")
            previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
            try:
                child = subprocess.Popen(argv, cwd=KIT, preexec_fn=lambda: signal.signal(
                    signal.SIGINT, signal.SIG_DFL))
                return child.wait(), ""
            finally:
                signal.signal(signal.SIGINT, previous)
        except (OSError, subprocess.SubprocessError) as exc:
            return 127, str(exc)

    def stage_e_steps(self):
        """{step: outcome} from the review installer's own record, which its `verify`
        rewrites on every pass — or None when it cannot be read."""
        import json
        path = os.path.expanduser("~/.stage-e-setup/state.json")
        try:
            with open(path, encoding="utf-8") as fh:
                steps = json.load(fh).get("steps") or {}
            return dict((k, (v or {}).get("outcome")) for k, v in steps.items())
        except (OSError, ValueError, AttributeError):
            return None

    def ask(self, question):
        try:
            return input("%s [y/N] " % question).strip().lower() in ("y", "yes")
        except EOFError:
            return False

    def say(self, line=""):
        print(line, flush=True)

    def restart(self, argv):
        os.execv(sys.executable, [sys.executable] + argv)

    def env(self):
        return os.environ

    def uid(self):
        return os.getuid()

    def is_tty(self):
        return sys.stdin.isatty()


def heading(io, text):
    io.say("")
    io.say("== %s" % text)


def stop(io, why, code):
    io.say("")
    io.say("Stopped: %s" % why)
    return code


def git(io, *args):
    code, out = io.run(["git", "-C", KIT] + list(args), capture=True)
    return code, (out or "").strip()


def default_branch(io):
    """The branch origin calls its default, or `main` when origin does not say."""
    code, out = git(io, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    if code == 0 and out.startswith("refs/remotes/origin/"):
        return out[len("refs/remotes/origin/"):]
    return "main"


def step_checkout(io, argv, no_pull):
    """None to carry on, or an exit code. May restart this program on the new code."""
    heading(io, "1. The kit checkout")
    io.say("Kit checkout: %s" % KIT)
    if no_pull:
        io.say("Not pulled (--no-pull).")
        return None
    want = default_branch(io)
    code, branch = git(io, "rev-parse", "--abbrev-ref", "HEAD")
    if code != 0:
        return stop(io, "this is not a git checkout git can read (%s)." % branch, EX_USAGE)
    if branch != want:
        return stop(io, "the kit checkout is on '%s'. The installers run from %s:\n"
                        "    git -C %s switch %s" % (branch, want, KIT, want), EX_USAGE)
    code, dirty = git(io, "status", "--porcelain", "--untracked-files=no")
    if code != 0 or dirty:
        return stop(io, "the kit checkout has uncommitted changes. See them with:\n"
                        "    git -C %s status" % KIT, EX_USAGE)
    _code, before = git(io, "rev-parse", "HEAD")
    code, out = git(io, "pull", "--ff-only", "--quiet")
    if code != 0:
        return stop(io, "git could not fast-forward %s:\n    %s" % (want, out[:300]), 1)
    _code, after = git(io, "rev-parse", "HEAD")
    _code, line = git(io, "log", "--oneline", "-1")
    io.say("Now at: %s" % line)
    if before != after:
        io.say("That moved the code, so this command starts again on it.")
        io.restart([os.path.abspath(__file__)] + list(argv) + ["--no-pull"])
        return EX_OK                 # reached only when `restart` returns (the battery)
    return None


def step_skills(io):
    heading(io, "2. The skills in your home folder")
    code, _out = io.run([sys.executable, SKILLS])
    if code == 0:
        return None
    if code == 3:
        return stop(io, "the skills check refused: it saw a Claude session.", EX_REFUSED)
    if code != 1:
        return stop(io, "the skills check failed (exit %d). The lines above say why." % code,
                    code)
    io.say("Some skills in your home folder differ from the kit's (listed above).")
    io.say("Replacing them overwrites any change you made to those copies by hand.")
    if not io.ask("Replace them with the kit's now?"):
        io.say("Left as they are. Planning sessions may load an older procedure until you do.")
        return None
    code, _out = io.run([sys.executable, SKILLS, "--apply"])
    if code != 0:
        return stop(io, "the skills could not all be replaced (exit %d)." % code, code)
    return None


def step_stage_e(io, conf, skip):
    heading(io, "3. Stage E, the review jobs")
    if skip:
        io.say("Skipped (--skip-stage-e).")
        return None
    if not os.path.exists(conf):
        io.say("Stage E is not set up on this Mac (no %s), so there is nothing to bring up "
               "to date." % os.path.relpath(conf, KIT))
        io.say("The idea gate asks for the dispatcher's details itself.")
        return None
    code, _out = io.run([sys.executable, STAGE_E, "verify", "--conf", conf])
    if code == 0:
        io.say("")
        io.say("Stage E is up to date. Nothing to do.")
        return None
    if code in (3, 5):
        return stop(io, STAGE_E_MEANS[code], code)
    # WHAT IS OUTSTANDING DECIDES WHAT HAPPENS. The check records every step it measured.
    # A step `run` would change is work for the installer; a card is work for a person,
    # and running the installer again cannot clear it. Only the first needs a run.
    steps = io.stage_e_steps()
    if steps is None or any(o == "WOULD-CHANGE" for o in steps.values()):
        io.say("")
        io.say("Stage E is not up to date: the check above names what is outstanding.")
        io.say("Updating it can restart the dispatcher, which cuts off any session it is "
               "running.")
        io.say("Check Linear first: nothing the dispatcher works on should be In Progress.")
        if not io.ask("Update Stage E now?"):
            return stop(io, "nothing changed. Stage E comes first, because the same kit change "
                            "teaches its jobs to leave planning tickets alone. Run this again "
                            "when you are ready.", EX_BLOCKED)
        code, _out = io.run([sys.executable, STAGE_E, "run", "--conf", conf])
        if code == 0:
            io.say("")
            io.say("Stage E is up to date.")
            return None
        if code in (3, 5):
            return stop(io, STAGE_E_MEANS[code], code)
        steps = io.stage_e_steps()
    # THE IDEA GATE NEEDS STAGE E'S CODE, NOT ITS SIGN-OFFS. Its jobs must run the code
    # that leaves planning tickets alone; a Stage E card waiting on a person does not
    # change that, and stopping here on one would keep the idea gate out of reach for as
    # long as the card waits.
    if steps and steps.get("code") in ("DONE", "ALREADY-DONE"):
        io.say("")
        io.say("Stage E's jobs run the current code. Stage E still has something for you,")
        io.say("named above, which does not block the idea gate. Carrying on; come back to it.")
        return None
    return stop(io, STAGE_E_MEANS.get(code, "the review installer stopped with exit %d. Its "
                                            "FAILED lines above say why." % code)
                + " (To install the idea gate without touching Stage E: --skip-stage-e.)", code)


def step_stage_a(io, conf, stage_e_conf):
    heading(io, "4. The idea gate")
    code, _out = io.run([sys.executable, STAGE_A, "run", "--conf", conf,
                         "--stage-e-conf", stage_e_conf])
    io.say("")
    io.say(STAGE_A_MEANS.get(code, "The idea-gate installer stopped with exit %d. The lines "
                                   "above say why." % code))
    return code


def main(argv=None, io=None):
    io = io or Io()
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="pipeline_install.py",
                                     description="Bring this Mac up to date and install the "
                                                 "idea gate: one command, run again after "
                                                 "any stop.")
    parser.add_argument("--conf", default=os.path.join(KIT, "stage-a.conf"),
                        help="the idea gate's settings (default: stage-a.conf in the kit)")
    parser.add_argument("--stage-e-conf", default=os.path.join(KIT, "stage-e.conf"),
                        help="the review jobs' settings (default: stage-e.conf in the kit)")
    parser.add_argument("--no-pull", action="store_true",
                        help="use this checkout as it is, without pulling")
    parser.add_argument("--skip-stage-e", action="store_true",
                        help="do not check or update the review jobs")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()

    heading(io, "0. Where this is running")
    found = [m for m in AGENT_ENV_MARKERS if m in io.env()]
    if found:
        return stop(io, "this shell belongs to a Claude session (%s is set). The installers "
                        "refuse to change anything from one, on purpose: a session that "
                        "could run them could install its own supervisor. Open the Terminal "
                        "app, or this app's Terminal tab, and run it there." % ", ".join(found),
                    EX_REFUSED)
    if io.uid() == 0:
        return stop(io, "do not run this with sudo. Run it as yourself; it asks for your Mac "
                        "password when it needs it.", EX_USAGE)
    if not io.is_tty():
        return stop(io, "this needs a person at a terminal: it asks before every change.",
                    EX_USAGE)
    io.say("A terminal, as yourself. Good.")

    for step in (lambda: step_checkout(io, argv, args.no_pull),
                 lambda: step_skills(io),
                 lambda: step_stage_e(io, os.path.abspath(args.stage_e_conf), args.skip_stage_e)):
        code = step()
        if code is not None:
            return code
    return step_stage_a(io, os.path.abspath(args.conf), os.path.abspath(args.stage_e_conf))


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
class FakeIo(Io):
    """The outside world, scripted. `codes` answers each program by a key naming it; a
    program this case did not script is a failure of the case, never a silent pass."""

    def __init__(self, codes=None, answers=(), env=None, uid=501, tty=True, git_state=None,
                 steps=None):
        self.steps = {"code": "WOULD-CHANGE"} if steps is None else steps
        self.codes = dict(codes or {})
        self.answers = list(answers)
        self.asked = []
        self.ran = []
        self.lines = []
        self.restarted = None
        self._env = dict(env or {})
        self._uid = uid
        self._tty = tty
        self.git = dict({"symbolic-ref": (0, "refs/remotes/origin/main"),
                         "rev-parse --abbrev-ref": (0, "main"), "status": (0, ""),
                         "rev-parse HEAD": [(0, "aaa"), (0, "aaa")], "pull": (0, ""),
                         "log": (0, "aaa the latest")}, **(git_state or {}))

    @staticmethod
    def key(argv):
        if argv[0] == "git":
            rest = argv[3:]
            if rest[:2] == ["rev-parse", "--abbrev-ref"]:
                return "rev-parse --abbrev-ref"
            if rest[:2] == ["rev-parse", "HEAD"]:
                return "rev-parse HEAD"
            return rest[0]
        script = os.path.basename(argv[1])
        return " ".join([script] + [a for a in argv[2:3] if not a.startswith("/")])

    def run(self, argv, capture=False):
        k = self.key(argv)
        self.ran.append(k)
        if argv[0] == "git":
            got = self.git[k]
            if isinstance(got, list):
                return got.pop(0) if len(got) > 1 else got[0]
            return got
        if k not in self.codes:
            raise AssertionError("an unscripted program ran: %r" % k)
        got = self.codes[k]
        return (got.pop(0) if isinstance(got, list) else got), ""

    def stage_e_steps(self):
        got = self.steps
        return got.pop(0) if isinstance(got, list) else got

    def ask(self, question):
        self.asked.append(question)
        return self.answers.pop(0) if self.answers else False

    def say(self, line=""):
        self.lines.append(line)

    def restart(self, argv):
        self.restarted = argv

    def env(self):
        return self._env

    def uid(self):
        return self._uid

    def is_tty(self):
        return self._tty


def selftest():
    failures, cases = [], [0]

    def check(name, got, want):
        cases[0] += 1
        if got != want:
            failures.append("%s: got %r, want %r" % (name, got, want))

    import tempfile
    tmp = tempfile.mkdtemp(prefix="pipeline-install-selftest-")
    se_conf = os.path.join(tmp, "stage-e.conf")
    with open(se_conf, "w") as fh:
        fh.write("ROLE_ACCOUNT=_x\n")
    a_conf = os.path.join(tmp, "stage-a.conf")
    base = ["--conf", a_conf, "--stage-e-conf", se_conf]
    healthy = {"sync_user_skills.py": 0, "pipeline_stage_e_setup.py verify": 0,
               "pipeline_stage_a_setup.py run": 0}

    def go(codes=None, answers=(), argv=None, **kw):
        io = FakeIo(codes=dict(healthy, **(codes or {})), answers=answers, **kw)
        code = main(base + list(argv or []), io=io)
        return code, io

    # 1. A CLAUDE SESSION IS REFUSED BEFORE ANYTHING RUNS. Every marker, alone.
    for marker in AGENT_ENV_MARKERS:
        code, io = go(env={marker: ""})
        check("refused-under:%s" % marker, (code, io.ran), (EX_REFUSED, []))
    code, io = go(uid=0)
    check("refused-as-root", (code, io.ran), (EX_USAGE, []))
    code, io = go(tty=False)
    check("refused-without-a-person", (code, io.ran), (EX_USAGE, []))

    # 2. THE HAPPY PATH: pull, skills level, Stage E current, the idea gate done.
    code, io = go()
    check("all-done", code, EX_OK)
    check("order", [k for k in io.ran if not k.startswith(("rev-parse", "symbolic", "status",
                                                           "log", "pull"))],
          ["sync_user_skills.py", "pipeline_stage_e_setup.py verify",
           "pipeline_stage_a_setup.py run"])
    check("pulled-fast-forward-only", "pull" in io.ran, True)
    check("stage-e-current-is-not-rerun", "pipeline_stage_e_setup.py run" in io.ran, False)
    check("asked-nothing-when-nothing-to-do", io.asked, [])

    # 3. THE CHECKOUT: the default branch, clean, and a restart onto new code.
    code, io = go(git_state={"rev-parse --abbrev-ref": (0, "feat/x")})
    check("wrong-branch-stops", (code, "sync_user_skills.py" in io.ran), (EX_USAGE, False))
    code, io = go(git_state={"status": (0, " M scripts/x.py")})
    check("dirty-stops", (code, "sync_user_skills.py" in io.ran), (EX_USAGE, False))
    code, io = go(git_state={"pull": (1, "diverged")})
    check("pull-failure-stops", (code, "sync_user_skills.py" in io.ran), (1, False))
    code, io = go(git_state={"rev-parse HEAD": [(0, "aaa"), (0, "bbb")]})
    check("new-code-restarts-itself",
          (io.restarted is not None and io.restarted[-1] == "--no-pull",
           "sync_user_skills.py" in io.ran), (True, False))
    code, io = go(argv=["--no-pull"])
    check("no-pull-skips-git", ("pull" in io.ran, code), (False, EX_OK))
    code, io = go(git_state={"symbolic-ref": (1, "")})
    check("default-branch-falls-back-to-main", code, EX_OK)

    # 4. SKILLS: asked before replacing, and a no carries on.
    code, io = go(codes={"sync_user_skills.py --apply": 0,
                         "sync_user_skills.py": [1]}, answers=[True])
    check("skills-drift-asked-then-applied",
          ("sync_user_skills.py --apply" in io.ran, len(io.asked), code), (True, 1, EX_OK))
    code, io = go(codes={"sync_user_skills.py": 1}, answers=[False])
    check("skills-declined-carries-on",
          ("sync_user_skills.py --apply" in io.ran, "pipeline_stage_a_setup.py run" in io.ran),
          (False, True))
    code, io = go(codes={"sync_user_skills.py": 3})
    check("skills-refusal-stops", (code, "pipeline_stage_a_setup.py run" in io.ran),
          (EX_REFUSED, False))

    # 5. STAGE E: checked, run only after a yes, and first.
    code, io = go(codes={"pipeline_stage_e_setup.py verify": 10,
                         "pipeline_stage_e_setup.py run": 0}, answers=[True])
    check("stage-e-outstanding-asked-then-run",
          ("pipeline_stage_e_setup.py run" in io.ran, code), (True, EX_OK))
    check("stage-e-runs-before-the-idea-gate",
          io.ran.index("pipeline_stage_e_setup.py run")
          < io.ran.index("pipeline_stage_a_setup.py run"), True)
    check("stage-e-warns-about-the-restart",
          any("restart the dispatcher" in line for line in io.lines), True)
    code, io = go(codes={"pipeline_stage_e_setup.py verify": 10}, answers=[False])
    check("stage-e-declined-stops-before-the-idea-gate",
          (code, "pipeline_stage_e_setup.py run" in io.ran,
           "pipeline_stage_a_setup.py run" in io.ran), (EX_BLOCKED, False, False))
    code, io = go(codes={"pipeline_stage_e_setup.py verify": 1,
                         "pipeline_stage_e_setup.py run": 1}, answers=[True])
    check("stage-e-failure-stops-before-the-idea-gate",
          (code, "pipeline_stage_a_setup.py run" in io.ran), (1, False))
    code, io = go(codes={"pipeline_stage_e_setup.py verify": 5})
    check("stage-e-no-password-stops", (code, io.asked), (5, []))
    # A CARD IN STAGE E DOES NOT KEEP THE IDEA GATE OUT: its code is current, and running
    # the review installer again could not clear a card anyway.
    code, io = go(codes={"pipeline_stage_e_setup.py verify": 10},
                  steps={"code": "ALREADY-DONE", "handover": "BLOCKED-ON-HUMAN"})
    check("stage-e-card-only-is-not-rerun-and-does-not-block",
          ("pipeline_stage_e_setup.py run" in io.ran, "pipeline_stage_a_setup.py run" in io.ran,
           io.asked, code), (False, True, [], EX_OK))
    code, io = go(codes={"pipeline_stage_e_setup.py verify": 10,
                         "pipeline_stage_e_setup.py run": 10}, answers=[True],
                  steps=[{"code": "WOULD-CHANGE"}, {"code": "DONE", "handover": "BLOCKED-ON-HUMAN"}])
    check("stage-e-run-then-card-carries-on",
          ("pipeline_stage_a_setup.py run" in io.ran, code), (True, EX_OK))
    code, io = go(codes={"pipeline_stage_e_setup.py verify": 10,
                         "pipeline_stage_e_setup.py run": 10}, answers=[True],
                  steps=[{"code": "WOULD-CHANGE"}, {"code": "WOULD-CHANGE"}])
    check("stage-e-code-still-behind-stops",
          (code, "pipeline_stage_a_setup.py run" in io.ran), (10, False))
    code, io = go(codes={"pipeline_stage_e_setup.py verify": 1}, steps={"code": "FAILED"})
    check("stage-e-code-failed-stops-and-names-the-skip",
          (code, any("--skip-stage-e" in line for line in io.lines)), (1, True))
    os.unlink(se_conf)
    code, io = go()
    check("no-stage-e-skips-to-the-idea-gate",
          ("pipeline_stage_e_setup.py verify" in io.ran, code), (False, EX_OK))
    with open(se_conf, "w") as fh:
        fh.write("ROLE_ACCOUNT=_x\n")
    code, io = go(argv=["--skip-stage-e"])
    check("skip-stage-e", "pipeline_stage_e_setup.py verify" in io.ran, False)

    # 6. THE IDEA GATE'S OWN CODE IS PASSED THROUGH, with the sentence that goes with it.
    for rc in (10, 4, 1, 2, 3, 5, 77):
        code, io = go(codes={"pipeline_stage_a_setup.py run": rc})
        check("stage-a-exit-%d-passed-through" % rc, code, rc)
        check("stage-a-exit-%d-said" % rc, bool(io.lines and io.lines[-1]), True)
    check("waiting-says-run-again", "run this same command again" in STAGE_A_MEANS[10], True)

    # 7. CTRL-C REACHES THE INSTALLER AND ITS CLEAN-UP RUNS TO THE END. Real processes:
    #    a terminal's Ctrl-C goes to the whole process group, and a wrapper that did not
    #    step aside would kill the installer a quarter of a second in.
    import signal
    import time
    mark = os.path.join(tmp, "cleanup-done")
    child = ("import time,sys\ntry:\n    time.sleep(30)\nexcept KeyboardInterrupt:\n"
             "    time.sleep(1.5)\n    open(%r,'w').write('ok')\n    sys.exit(10)\n" % mark)
    driver = ("import sys; sys.path.insert(0, %r)\nimport pipeline_install as pi\n"
              "code, _ = pi.Io().run([sys.executable, '-c', %r])\nsys.exit(0 if code == 10 else 1)\n"
              % (HERE, child))
    proc = subprocess.Popen([sys.executable, "-c", driver], start_new_session=True)
    time.sleep(1.0)
    os.killpg(proc.pid, signal.SIGINT)
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
    check("ctrl-c-lets-the-installer-finish-its-clean-up",
          (proc.returncode, os.path.exists(mark)), (0, True))

    # 8. IT NEVER MERGES, APPROVES OR LABELS: none of those verbs is in this file.
    src = open(os.path.abspath(__file__)).read()
    for verb in ("pr " + "merge", "--" + "approve", "--add-" + "label", "auto-" + "merge"):
        check("no-verb:%s" % verb, verb in src, False)
    check("markers-imported-not-copied",
          "from pipeline_dispatch_local import AGENT_ENV_MARKERS" in src, True)

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print("FAIL: pipeline_install selftest")
        for f in failures:
            print("  - %s" % f)
        return 1
    print("OK: pipeline_install selftest (%d cases)" % cases[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
