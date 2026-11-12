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
     review installer — which can restart the dispatcher, so it says so first. The
     notifier and the Slack bridge run from the same clone as the review jobs, so a
     merge that changed one of them is outstanding too, on a Mac that runs it: the review
     installer moves the clone, and stops and reloads each of them around the move.
  3b. The sweep (`scripts/pipeline_sweep_setup.py run`), when this Mac has a `sweep.conf`:
     it installs the job that removes what finished dispatcher runs left, and never loads
     it. Its card (CK-S1) is yours and does not hold the idea gate back.
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

WHAT IT PRINTS AT THE END (every exit that gets past the three refusals, a stop part way
and Ctrl-C included; the refusals read nothing, so they print nothing)
  One line per part of the pipeline, read from `pipeline-parts.json`, each with exactly one
  of: up to date · updated · waiting on you · off on this Mac · could not be measured · not
  reached (the run stopped before that part). The steps above report their own result for
  the parts they act on. Every other part with a check is looked at once, read-only, with no
  input and no terminal to prompt on: its settings file absent is "off on this Mac" and its
  check does not run; exit 0 is up to date, 10 waiting on you, anything else (another code,
  a killed or timed-out child, a check that wanted to ask) could not be measured. A part
  with no check and no step is named on one closing line and changes no exit code.
  Then a short list: the cards (`CK-...`) a waiting part's own output names, split into
  "Only you can do these" and "Sign-offs still asked for", or "Nothing is waiting on you."
  The lines are built as data first, one record per part (id, name, outcome, detail).

Usage:
    pipeline_install.py [--conf stage-a.conf] [--stage-e-conf stage-e.conf]
                        [--no-pull] [--skip-stage-e]
    pipeline_install.py --selftest

Exit: 0 every part is up to date, updated or off · 4 a part could not be measured, or was
      not reached · 10 waiting on you (the lines above say what; run it again afterwards)
      · 2 this checkout or its settings need fixing first · 3 refused (an agent
      environment) · any other code is the installer's own, passed through with a sentence
      saying which one stopped.
      4 comes before 10 when both apply (contract section 13, and the installers' own
      order): a run that could not measure something has not found out what else is left, so
      "waiting on you" must not read as the whole answer. A stop part way keeps the code of
      the installer that stopped (10 stays 10; the parts it did not reach are on the
      lines); a stop that would exit 0 exits 4.
"""
import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
# The agent-env markers are IMPORTED from the dispatcher module, never copied, so every
# script in the kit means the same thing by "an agent environment".
from pipeline_dispatch_local import AGENT_ENV_MARKERS  # noqa: E402

EX_OK, EX_USAGE, EX_REFUSED, EX_BLOCKED = 0, 2, 3, 10
EX_UNMEASURED = 4

# The six things a part's line can say, and no others.
OUT_OK = "up to date"
OUT_UPDATED = "updated"
OUT_WAITING = "waiting on you"
OUT_OFF = "off on this Mac"
OUT_UNMEASURED = "could not be measured"
OUT_NOT_REACHED = "not reached"
OUTCOMES = (OUT_OK, OUT_UPDATED, OUT_WAITING, OUT_OFF, OUT_UNMEASURED, OUT_NOT_REACHED)
# A part's own check gets this long, once. Longer is "could not be measured".
CHECK_TIMEOUT = 120

SKILLS = os.path.join(HERE, "sync_user_skills.py")
STAGE_E = os.path.join(HERE, "pipeline_stage_e_setup.py")
STAGE_A = os.path.join(HERE, "pipeline_stage_a_setup.py")
SWEEP_SETUP = os.path.join(HERE, "pipeline_sweep_setup.py")

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
# Said with every Stage E stop that is not the person's to clear by running again.
STAGE_E_SKIP_HINT = " (To install the idea gate without touching Stage E: --skip-stage-e.)"
# The optional jobs whose card never stops Stage E's `run`, by step, and what each card waits
# for. A run that finished on one of these alone carries on to the idea gate.
OPTIONAL_JOB_CARDS = {
    "catch-up": "Its catch-up poller waits for you to read its first count (card CK-11, above)",
    "status": "Its status job waits for a dry run that reads the tracker (card CK-12, above)",
}
# Said when a person pressed Ctrl-C: the installer it reached finished its own clean-up,
# and nothing after it runs.
INTERRUPTED = ("interrupted. %s finished its own clean-up and stopped; the lines above say "
               "where. Nothing after it ran. Run this same command again to carry on.")
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

    # Set when the person pressed Ctrl-C while an installer ran: that installer finished
    # its own clean-up, and nothing after it may start.
    interrupted = False

    def now(self):
        """UTC now, to the second, in the form the installers stamp their records with."""
        import datetime
        return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def run(self, argv, capture=False):
        """(exit code, output). Streams are inherited unless `capture`: an installer's
        prompts and cards are for the person, not for this program.

        CTRL-C BELONGS TO THE INSTALLER, NOT TO THIS WRAPPER. Ctrl-C reaches every process
        on the terminal. Left alone, this wrapper would give the installer a quarter of a
        second and then kill it — in the middle of the drill putting Cyrus's settings back,
        or of a restart. So this process only NOTES it while an installer runs, the
        installer gets it with the default handling restored, and its own clean-up runs to
        the end. Noted, not ignored: an installer that stopped on a Ctrl-C must not be
        followed by the next one, as if the person had asked for that."""
        import signal
        try:
            if capture:
                proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      cwd=KIT, timeout=120)
                return proc.returncode, proc.stdout.decode("utf-8", "replace")
            def noted(_signum, _frame):
                self.interrupted = True
            previous = signal.signal(signal.SIGINT, noted)
            try:
                child = subprocess.Popen(argv, cwd=KIT, preexec_fn=lambda: signal.signal(
                    signal.SIGINT, signal.SIG_DFL))
                return child.wait(), ""
            finally:
                signal.signal(signal.SIGINT, previous)
        except (OSError, subprocess.SubprocessError) as exc:
            return 127, str(exc)

    def measure(self, argv, timeout=CHECK_TIMEOUT):
        """(exit code, output) of one read-only check, or (None, why) when it did not finish.

        NOTHING IN HERE CAN ASK A PERSON. Standard input is closed, and the child runs in a
        session of its own, so it has no terminal to open: a password prompt or an input()
        fails at once, and the caller reads that as "could not be measured". A child that
        outlives `timeout` is killed with everything it started."""
        import signal
        try:
            proc = subprocess.Popen(argv, cwd=KIT, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        except (OSError, subprocess.SubprocessError) as exc:
            return None, str(exc)

        def end_it():
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                proc.communicate(timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass
        try:
            out, _ = proc.communicate(timeout=timeout)
            return proc.returncode, out.decode("utf-8", "replace")
        except subprocess.TimeoutExpired:
            end_it()
            return None, "no answer in %d seconds" % timeout
        except KeyboardInterrupt:
            end_it()
            self.interrupted = True
            return None, "interrupted"

    def exists(self, path):
        return os.path.exists(path)

    def parts(self):
        """The pipeline's parts, in the list's order. Raises OSError or ValueError."""
        import pipeline_parts
        doc, _schema = pipeline_parts.load(KIT)
        return pipeline_parts.parts_of(doc)

    # The records the last run ended with, for whatever prints them another way later.
    records = None

    def stage_e_steps(self, since):
        """{step: outcome} for the steps the review installer's own record says were
        measured at or after `since` — or None when it cannot be read, or when its `code`
        step was not measured then. An older pass's row says nothing about now."""
        import json
        path = os.path.expanduser("~/.stage-e-setup/state.json")
        try:
            with open(path, encoding="utf-8") as fh:
                steps = json.load(fh).get("steps") or {}
            fresh = dict((k, (v or {}).get("outcome")) for k, v in steps.items()
                         if str((v or {}).get("at") or "") >= since)
        except (OSError, ValueError, AttributeError):
            return None
        return fresh if "code" in fresh else None

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


def look_first(io, rep, key):
    """The exit code of the part's own read-only check, run before a step that can only
    `run`, so the step can say whether it changed anything. None when the parts list gave
    no check or it did not finish. Nothing here prints, asks or changes anything."""
    argv = rep.checks.get(key)
    if not argv or io.interrupted:
        return None
    code, _out = io.measure(argv)
    return code


def note_run_ok(rep, key, before):
    """The installer's `run` ended at 0: everything holds. Whether it had to change
    anything is what the check before it said."""
    if before == 0:
        rep.note(key, OUT_OK)
    elif before is None:
        rep.note(key, OUT_OK, "installed and proven; whether it changed anything was not measured")
    else:
        rep.note(key, OUT_UPDATED)


class Report(object):
    """What each of this command's own steps did, by step key, for the lines at the end:
    {key: (outcome, detail)}. A step that was never reached has no entry. `checks` holds,
    by step key, the read-only check of the part the step acts on (from the parts list), so
    a step that can only `run` can still say whether it changed anything."""

    def __init__(self):
        self.steps = {}
        self.checks = {}
        # Steps that act on several parts and ended waiting on a card alone: their code is
        # current, so a part of theirs that names no card of its own is not itself behind.
        self.card_only = set()

    def note(self, key, outcome, detail="", card_only=False):
        self.steps[key] = (outcome, detail)
        if card_only:
            self.card_only.add(key)

    def note_code(self, key, code, what):
        """An installer ended with `code` and the step stops or carries on without success:
        a card or a missing password is waiting on the person; anything else is a thing
        that could not be measured or done."""
        if code == EX_BLOCKED:
            self.note(key, OUT_WAITING, what)
        elif code == 5:
            self.note(key, OUT_WAITING, "%s needs your Mac password" % what)
        else:
            self.note(key, OUT_UNMEASURED, "%s stopped with exit %d" % (what, code))


def step_checkout(io, argv, no_pull, rep):
    """None to carry on, or an exit code. May restart this program on the new code."""
    heading(io, "1. The kit checkout")
    io.say("Kit checkout: %s" % KIT)
    if no_pull:
        io.say("Not pulled (--no-pull).")
        rep.note("checkout", OUT_OK, "used as it is (--no-pull)")
        return None
    want = default_branch(io)
    code, branch = git(io, "rev-parse", "--abbrev-ref", "HEAD")
    if code != 0:
        rep.note("checkout", OUT_UNMEASURED, "git could not read it")
        return stop(io, "this is not a git checkout git can read (%s)." % branch, EX_USAGE)
    if branch != want:
        rep.note("checkout", OUT_WAITING, "on '%s', not %s" % (branch, want))
        return stop(io, "the kit checkout is on '%s'. The installers run from %s:\n"
                        "    git -C %s switch %s" % (branch, want, KIT, want), EX_USAGE)
    code, dirty = git(io, "status", "--porcelain", "--untracked-files=no")
    if code != 0 or dirty:
        rep.note("checkout", OUT_WAITING if code == 0 else OUT_UNMEASURED,
                 "uncommitted changes" if code == 0 else "git could not read its state")
        return stop(io, "the kit checkout has uncommitted changes. See them with:\n"
                        "    git -C %s status" % KIT, EX_USAGE)
    _code, before = git(io, "rev-parse", "HEAD")
    code, out = git(io, "pull", "--ff-only", "--quiet")
    if code != 0:
        rep.note("checkout", OUT_UNMEASURED, "git could not fast-forward")
        return stop(io, "git could not fast-forward %s:\n    %s" % (want, out[:300]), 1)
    _code, after = git(io, "rev-parse", "HEAD")
    _code, line = git(io, "log", "--oneline", "-1")
    io.say("Now at: %s" % line)
    rep.note("checkout", OUT_UPDATED if before != after else OUT_OK,
             "moved to the latest" if before != after else "already at the latest")
    if before != after:
        io.say("That moved the code, so this command starts again on it.")
        io.restart([os.path.abspath(__file__)] + list(argv) + ["--no-pull"])
        return EX_OK                 # reached only when `restart` returns (the battery)
    return None


def step_skills(io, rep):
    heading(io, "2. The skills in your home folder")
    code, _out = io.run([sys.executable, SKILLS])
    if io.interrupted:
        rep.note("skills", OUT_UNMEASURED, "interrupted")
        return stop(io, INTERRUPTED % "The skills check", code or EX_BLOCKED)
    if code == 0:
        rep.note("skills", OUT_OK)
        return None
    if code == 3:
        rep.note("skills", OUT_UNMEASURED, "its check refused: it saw a Claude session")
        return stop(io, "the skills check refused: it saw a Claude session.", EX_REFUSED)
    if code != 1:
        rep.note("skills", OUT_UNMEASURED, "its check failed (exit %d)" % code)
        return stop(io, "the skills check failed (exit %d). The lines above say why." % code,
                    code)
    io.say("Some skills in your home folder differ from the kit's (listed above).")
    io.say("Replacing them overwrites any change you made to those copies by hand.")
    if not io.ask("Replace them with the kit's now?"):
        io.say("Left as they are. Planning sessions may load an older procedure until you do.")
        rep.note("skills", OUT_WAITING, "differ from the kit's; you left them as they are")
        return None
    code, _out = io.run([sys.executable, SKILLS, "--apply"])
    if io.interrupted:
        rep.note("skills", OUT_UNMEASURED, "interrupted while replacing them")
        return stop(io, INTERRUPTED % "Replacing the skills", code or EX_BLOCKED)
    if code != 0:
        rep.note("skills", OUT_UNMEASURED, "they could not all be replaced (exit %d)" % code)
        return stop(io, "the skills could not all be replaced (exit %d)." % code, code)
    rep.note("skills", OUT_UPDATED, "replaced with the kit's")
    return None


def step_stage_e(io, conf, skip, rep):
    heading(io, "3. Stage E, the review jobs")
    if skip:
        io.say("Skipped (--skip-stage-e).")
        rep.note("stage_e", OUT_UNMEASURED, "skipped (--skip-stage-e), so not checked")
        return None
    if not os.path.exists(conf):
        rep.note("stage_e", OUT_OFF, "no %s" % os.path.basename(conf))
        io.say("Stage E is not set up on this Mac (no %s), so there is nothing to bring up "
               "to date." % os.path.relpath(conf, KIT))
        io.say("The idea gate asks for the dispatcher's details itself.")
        return None
    started = io.now()
    code, _out = io.run([sys.executable, STAGE_E, "verify", "--conf", conf])
    if io.interrupted:
        rep.note("stage_e", OUT_UNMEASURED, "interrupted during its check")
        return stop(io, INTERRUPTED % "Stage E's check", code or EX_BLOCKED)
    if code == 0:
        io.say("")
        io.say("Stage E is up to date. Nothing to do.")
        rep.note("stage_e", OUT_OK)
        return None
    if code != 10:
        rep.note_code("stage_e", code, "its check")
        # NOT "WORK OUTSTANDING": a step that FAILED (1), one that could not be measured
        # (4), a settings problem (2), a refusal (3) or no password (5). None of those is a
        # card a person signs, and none is something to carry on past.
        return stop(io, STAGE_E_MEANS.get(code, "Stage E's check stopped with exit %d. The "
                                                "lines above say why." % code)
                    + STAGE_E_SKIP_HINT, code)
    # WHAT IS OUTSTANDING DECIDES WHAT HAPPENS. The check records every step it measured,
    # and only rows THIS check wrote are read: an older pass's record says nothing about
    # now. A step `run` would change is work for the installer; a card is work for a
    # person, and running the installer again cannot clear it.
    steps = io.stage_e_steps(started)
    if steps is None or any(o == "WOULD-CHANGE" for o in steps.values()):
        io.say("")
        io.say("Stage E is not up to date: the check above names what is outstanding.")
        io.say("Updating it can restart the dispatcher, which cuts off any session it is "
               "running.")
        io.say("If it moves the clone, it also stops the notifier and the Slack bridge, where "
               "this Mac runs them, for a few seconds, and loads them again.")
        io.say("Check Linear first: nothing the dispatcher works on should be In Progress.")
        if not io.ask("Update Stage E now?"):
            rep.note("stage_e", OUT_WAITING, "behind; you did not update it")
            return stop(io, "nothing changed. Stage E comes first, because the same kit change "
                            "teaches its jobs to leave planning tickets alone. Run this again "
                            "when you are ready.", EX_BLOCKED)
        ran_at = io.now()
        code, _out = io.run([sys.executable, STAGE_E, "run", "--conf", conf])
        if io.interrupted:
            rep.note("stage_e", OUT_UNMEASURED, "interrupted during its installer")
            return stop(io, INTERRUPTED % "Stage E's installer", code or EX_BLOCKED)
        if code == 0:
            io.say("")
            io.say("Stage E is up to date.")
            rep.note("stage_e", OUT_UPDATED)
            return None
        # A RUN THAT FINISHED, ENDING ON AN OPTIONAL JOB'S CARD: the catch-up poller's (CK-11)
        # or the status job's (CK-12). Neither card stops `run`: every step ran, through its
        # last, and every other job is loaded. Only that optional job waits on a person, so the
        # idea gate is not held back for it.
        if code == 10:
            ran = io.stage_e_steps(ran_at) or {}
            carded = [st for st in OPTIONAL_JOB_CARDS if ran.get(st) == "BLOCKED-ON-HUMAN"]
            others = [o for st, o in ran.items() if st not in carded]
            if "handover" in ran and carded and ran.get("code") in ("DONE", "ALREADY-DONE") \
                    and all(o in ("DONE", "ALREADY-DONE") for o in others):
                io.say("")
                io.say("Stage E is up to date.")
                for st in carded:
                    io.say(OPTIONAL_JOB_CARDS[st] + "; that does not block the idea gate.")
                rep.note("stage_e", OUT_WAITING, "updated; an optional job still waits on a card",
                         card_only=True)
                return None
        # A RUN THAT STOPPED PART WAY IS NEVER CARRIED PAST. It may have moved the code and
        # stopped before reloading the review jobs, so "the code is current" says nothing
        # about whether they are running. The next pass's check tells the two apart: if
        # all that is left then is a card, it carries on.
        rep.note_code("stage_e", code, "its installer")
        return stop(io, STAGE_E_MEANS.get(code, "Stage E's installer stopped part way "
                                                "(exit %d). The lines above say where." % code)
                    + " If it said its jobs are unloaded, review, bounce, findings and catch-up stay off "
                      "until it finishes. A notifier or Slack bridge job it names as off stays off "
                      "until it is loaded. Clear what it names, then run this command again.",
                    code)
    # ONLY A CARD IS LEFT, and Stage E's code is current. The idea gate needs Stage E's
    # code (its jobs leave planning tickets alone), not its sign-offs; stopping here on a
    # card would keep the idea gate out of reach for as long as the card waits.
    if steps.get("code") in ("DONE", "ALREADY-DONE"):
        io.say("")
        io.say("Stage E's jobs run the current code. Stage E still has something for you,")
        io.say("named above, which does not block the idea gate. Carrying on; come back to it.")
        rep.note("stage_e", OUT_WAITING, "its code is current; a card is still yours",
                 card_only=True)
        return None
    rep.note_code("stage_e", code, "its check")
    return stop(io, "Stage E's check says its code is not current, and nothing `run` could "
                    "change was named." + STAGE_E_SKIP_HINT, code)


def step_sweep(io, conf, rep):
    """The sweep's installer, when `sweep.conf` exists. An optional job: its own card waits on a person (it reads the dry
    run, then loads the job), and that never holds back the idea gate."""
    heading(io, "3b. The sweep, what finished runs leave behind")
    if not os.path.exists(conf):
        rep.note("sweep", OUT_OFF, "no %s" % os.path.basename(conf))
        io.say("No %s, so the sweep is not set up on this Mac. Copy sweep.conf.example to switch it on."
               % os.path.relpath(conf, KIT))
        return None
    before = look_first(io, rep, "sweep")
    code, _out = io.run([sys.executable, SWEEP_SETUP, "run", "--conf", conf])
    if io.interrupted:
        rep.note("sweep", OUT_UNMEASURED, "interrupted during its installer")
        return stop(io, INTERRUPTED % "The sweep's installer", code or EX_BLOCKED)
    io.say("")
    if code == 0:
        io.say("The sweep is installed, loaded and under its leftover line.")
        note_run_ok(rep, "sweep", before)
        return None
    if code == 10:
        io.say("The sweep waits for you at the card above; that does not block the idea gate.")
        rep.note("sweep", OUT_WAITING, "a card is still yours")
        return None
    rep.note_code("sweep", code, "its installer")
    return stop(io, "the sweep's installer stopped with exit %d. The lines above say why." % code, code)


def step_stage_a(io, conf, stage_e_conf, rep):
    heading(io, "4. The idea gate")
    before = look_first(io, rep, "stage_a")
    code, _out = io.run([sys.executable, STAGE_A, "run", "--conf", conf,
                         "--stage-e-conf", stage_e_conf])
    if io.interrupted:
        rep.note("stage_a", OUT_UNMEASURED, "interrupted during its installer")
    elif code == 0:
        note_run_ok(rep, "stage_a", before)
    else:
        rep.note_code("stage_a", code, "its installer")
    io.say("")
    io.say(STAGE_A_MEANS.get(code, "The idea-gate installer stopped with exit %d. The lines "
                                   "above say why." % code))
    return code


# --------------------------------------------------------------------------- #
# The lines at the end: one record per part, then the short list
# --------------------------------------------------------------------------- #
# The step of this command that acts on a part is found from the part's own installer or
# check script, never from a list of part ids, so a new part is picked up from the list.
STEP_OF_SCRIPT = {"sync_user_skills.py": "skills", "pipeline_stage_e_setup.py": "stage_e",
                  "pipeline_sweep_setup.py": "sweep", "pipeline_stage_a_setup.py": "stage_a"}


def step_of(part):
    """The key of this command's step that acts on the part, or None."""
    if os.path.basename(part.get("installer") or "") == "pipeline_install.py":
        return "checkout"
    check = part.get("check")
    if not check:
        return None
    base = os.path.basename(check["script"])
    if base == "pipeline_stage_e_setup.py" and (check.get("args") or [])[:1] != ["verify"]:
        return None
    return STEP_OF_SCRIPT.get(base)


def conf_path(name, files):
    return files.get(name) or os.path.join(KIT, name)


def check_argv(part, files):
    check = part["check"]
    args = [conf_path(a, files) if a.endswith(".conf") else a for a in check.get("args") or []]
    return [sys.executable, os.path.join(KIT, check["script"])] + args


def settings_path(part, files):
    """Where the part's settings file lives on this Mac, or None when it has no file a
    person switches it on with."""
    name = (part.get("settings") or {}).get("file") or ""
    return conf_path(name, files) if name.endswith((".conf", ".json")) else None


def outcome_of_check(code, out):
    """A part's own read-only check, read the one way for every part: 0 is up to date, 10 is
    waiting on you, and anything else, a child that did not finish included, is a thing that
    could not be measured. Never a pass."""
    if code == 0:
        return OUT_OK, ""
    if code == EX_BLOCKED:
        return OUT_WAITING, "its check found something outstanding"
    if code is None:
        return OUT_UNMEASURED, "its check did not finish (%s)" % (out or "no reason given")[:120]
    return OUT_UNMEASURED, "its check stopped with exit %d" % code


def build_records(io, parts, rep, files, reached):
    """([record], [names with no check of their own], {part id: its check's output}).
    A record is {id, name, outcome, detail}. `reached` is False when a step stopped the
    run: the checks of the other parts are then not run, and say so."""
    try:
        import pipeline_parts
        ordered = pipeline_parts.order(parts)
    except (ImportError, ValueError):
        ordered = list(parts)
    records, nocheck, texts, seen, group = [], [], {}, {}, {}
    for part in ordered:
        rec = {"id": part["id"], "name": part["name"], "outcome": OUT_NOT_REACHED,
               "detail": "the run stopped before this part"}
        key = step_of(part)
        if key:
            if key in rep.steps:
                rec["outcome"], rec["detail"] = rep.steps[key]
            if rec["outcome"] == OUT_WAITING and part.get("check") and not io.interrupted:
                # The step ran its installer uncaptured, so its card ids are read from the
                # part's own check, once more, read-only.
                argv = check_argv(part, files)
                if tuple(argv) not in seen:
                    seen[tuple(argv)] = io.measure(argv)
                texts[part["id"]] = seen[tuple(argv)][1] or ""
                group.setdefault(key, []).append((rec, part))
        elif part.get("check"):
            path = settings_path(part, files)
            if path and not io.exists(path):
                rec["outcome"] = OUT_OFF
                rec["detail"] = "no %s" % os.path.basename(path)
            elif reached and not io.interrupted:
                argv = check_argv(part, files)
                if tuple(argv) not in seen:
                    seen[tuple(argv)] = io.measure(argv)
                code, out = seen[tuple(argv)]
                rec["outcome"], rec["detail"] = outcome_of_check(code, out)
                texts[part["id"]] = out or ""
        else:
            nocheck.append(part["name"])
            continue
        records.append(rec)
    # A step that ended on a card alone has its code current. Of the parts it acts on, only
    # those that name a card of their own are still waiting; that is said only when the
    # check's output names at least one card, so an output that names none keeps them all
    # waiting rather than calling an unread card a pass.
    for key in rep.card_only:
        rows = [(r, pt, any(cards_named(texts.get(r["id"], ""), st["card"])
                            for st in pt.get("person_steps") or [] if st.get("card")))
                for r, pt in group.get(key, []) if r["outcome"] == OUT_WAITING]
        if any(named for _r, _pt, named in rows):
            for r, _pt, named in rows:
                if not named:
                    r["outcome"] = OUT_OK
                    r["detail"] = "its code is current; no card of its own is waiting"
    return records, nocheck, texts


def end_code(code, records, stopped, unreadable=False):
    """The exit code. A step that stopped the run keeps its code, 10 included: the parts not
    reached are on the lines, and the person's next move is the same either way. Only a stop
    that would exit 0 gives way, to 4. A run that got to the end takes the idea gate's code
    when it is not 0, 4 or 10; otherwise 4 when a part could not be measured or was not
    reached, 10 when a part is waiting, 0 only when none is. 4 outranks 10 (contract
    section 13): never exit 0 with anything unfinished."""
    kinds = set(r["outcome"] for r in records)
    unmeasured = unreadable or OUT_UNMEASURED in kinds or OUT_NOT_REACHED in kinds
    waiting = OUT_WAITING in kinds
    if stopped:
        return code if code not in (None, EX_OK) else EX_UNMEASURED
    if code is None or code in (EX_OK, EX_UNMEASURED, EX_BLOCKED):
        if unmeasured or code == EX_UNMEASURED:
            return EX_UNMEASURED
        if waiting or code == EX_BLOCKED:
            return EX_BLOCKED
        return EX_OK
    return code


def cards_named(text, card):
    """True when the card id stands alone in the text (CK-1 is not found inside CK-11)."""
    return bool(re.search(r"(?<![A-Za-z0-9-])%s(?![A-Za-z0-9-])" % re.escape(card), text))


def short_list(parts, records, texts):
    """The cards a waiting part's own output names, and the parts that wait without one."""
    by_id = dict((p["id"], p) for p in parts)
    mine, signs, lone, done = [], [], [], set()
    for rec in records:
        if rec["outcome"] != OUT_WAITING:
            continue
        part = by_id[rec["id"]]
        text = texts.get(rec["id"])
        found = False
        for step in part.get("person_steps") or []:
            card = step.get("card")
            if card and cards_named(text or "", card) and card not in done:
                done.add(card)
                found = True
                row = "  %-8s %s (%s)" % (card, step["words"], part["name"])
                (mine if step["kind"] == "only-a-person" else signs).append(row)
            elif card and card in done:
                found = True
        if not found:
            lone.append(part["name"])
    return mine, signs, lone


def finish(io, parts, rep, files, reached, stop_code, stopped, unreadable=None):
    """Print the lines and the short list; return the exit code."""
    heading(io, "Where every part stands")
    if unreadable:
        io.say("The list of parts could not be read (%s), so no part was looked at." % unreadable)
        io.records = []
        return end_code(stop_code, [], stopped, unreadable=True)
    records, nocheck, texts = build_records(io, parts, rep, files, reached)
    io.records = records
    for rec in records:
        io.say("  %-22s %s%s" % (rec["outcome"], rec["name"],
                                 " (%s)" % rec["detail"] if rec["detail"] else ""))
    if nocheck:
        io.say("No check of their own: %s." % "; ".join(nocheck))
    code = end_code(stop_code, records, stopped)
    counts = dict((o, sum(1 for r in records if r["outcome"] == o)) for o in OUTCOMES)
    if counts[OUT_UNMEASURED] or counts[OUT_NOT_REACHED]:
        io.say("")
        io.say("%d part(s) could not be measured and %d were not reached, so this run is not "
               "a pass (exit %d)." % (counts[OUT_UNMEASURED], counts[OUT_NOT_REACHED], code))
    mine, signs, lone = short_list(parts, records, texts)
    io.say("")
    if not (mine or signs or lone):
        if counts[OUT_WAITING] == 0:
            io.say("Nothing is waiting on you.")
        return code
    if mine:
        io.say("Only you can do these:")
        for row in mine:
            io.say(row)
    if signs:
        io.say("Sign-offs still asked for:")
        for row in signs:
            io.say(row)
    for name in lone:
        io.say("%s is waiting on you; its own lines above say what for." % name)
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
    parser.add_argument("--sweep-conf", default=os.path.join(KIT, "sweep.conf"),
                        help="the sweep's settings (default: sweep.conf in the kit; absent skips it)")
    parser.add_argument("--skip-stage-e", action="store_true",
                        help="do not check or update the review jobs")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        from scratch_run import contained   # KIT-450: its temp folders go when it ends
        return contained(selftest, "install-selftest-run-")

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

    # Past the three refusals, every exit ends with the lines. The parts list is read once,
    # up front; if it cannot be read the steps still run and the end says so, in words.
    files = {"stage-a.conf": os.path.abspath(args.conf),
             "stage-e.conf": os.path.abspath(args.stage_e_conf),
             "sweep.conf": os.path.abspath(args.sweep_conf)}
    rep = Report()
    parts, unreadable = [], None
    try:
        parts = io.parts()
        for part in parts:
            key = step_of(part)
            if key and part.get("check") and key not in rep.checks:
                rep.checks[key] = check_argv(part, files)
    except (OSError, ValueError, ImportError, KeyError, TypeError) as exc:
        parts, unreadable = [], "%s: %s" % (type(exc).__name__, exc)

    steps = (lambda: step_checkout(io, argv, args.no_pull, rep),
             lambda: step_skills(io, rep),
             lambda: step_stage_e(io, files["stage-e.conf"], args.skip_stage_e, rep),
             lambda: step_sweep(io, files["sweep.conf"], rep),
             lambda: step_stage_a(io, files["stage-a.conf"], files["stage-e.conf"], rep))
    code, reached, stopped = None, True, False
    try:
        for n, step in enumerate(steps):
            code = step()
            if code is not None:
                reached = n == len(steps) - 1
                stopped = not reached
                break
    except KeyboardInterrupt:
        io.interrupted = True
        reached, stopped = False, True
        code = stop(io, INTERRUPTED % "The step you interrupted", EX_BLOCKED)
    return finish(io, parts, rep, files, reached, code, stopped, unreadable)


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
class FakeIo(Io):
    """The outside world, scripted. `codes` answers each program by a key naming it; a
    program this case did not script is a failure of the case, never a silent pass."""

    def __init__(self, codes=None, answers=(), env=None, uid=501, tty=True, git_state=None,
                 steps=None):
        self.steps = {"code": "WOULD-CHANGE"} if steps is None else steps
        self.since = []
        self.interrupt_on = set()          # program keys whose run the person Ctrl-C'd
        self.codes = dict(codes or {})
        self.answers = list(answers)
        self.asked = []
        self.ran = []
        self.argvs = []
        self.lines = []
        self.restarted = None
        # Each part's own read-only check: {program key: exit code, or a list of them}, with
        # {program key: output} beside it. A check this case did not script ends at 0, so
        # the cases written before the lines existed keep their meaning; the new cases
        # script what they mean. `None` is a check that did not finish.
        self.measure_codes = {}
        self.measure_out = {}
        self.measured = []
        self.present = set()               # settings files under the kit that "exist"
        self.parts_list = None             # None: the real list; an Exception: it cannot be read
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
        self.argvs.append(list(argv))
        if argv[0] == "git":
            got = self.git[k]
            if isinstance(got, list):
                return got.pop(0) if len(got) > 1 else got[0]
            return got
        if k not in self.codes:
            raise AssertionError("an unscripted program ran: %r" % k)
        if k in self.interrupt_on:
            self.interrupted = True
        got = self.codes[k]
        return (got.pop(0) if isinstance(got, list) else got), ""

    def measure(self, argv, timeout=CHECK_TIMEOUT):
        k = self.key(argv)
        self.measured.append(k)
        got = self.measure_codes.get(k, 0)
        code = got.pop(0) if isinstance(got, list) and len(got) > 1 else (
            got[0] if isinstance(got, list) else got)
        return code, self.measure_out.get(k, "" if code in (0, None) else "")

    def exists(self, path):
        if path.startswith(KIT + os.sep):
            return path in self.present
        return os.path.exists(path)

    def parts(self):
        if isinstance(self.parts_list, Exception):
            raise self.parts_list
        return Io.parts(self) if self.parts_list is None else self.parts_list

    def stage_e_steps(self, since):
        self.since.append(since)
        got = self.steps
        return got.pop(0) if isinstance(got, list) else got

    def now(self):
        return "2026-10-03T00:00:00Z"

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
    sweep_conf = os.path.join(tmp, "sweep.conf")                  # absent: the sweep is skipped
    base = ["--conf", a_conf, "--stage-e-conf", se_conf, "--sweep-conf", sweep_conf]
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
    check("stage-e-warns-the-notifier-and-bridge-stop-for-the-move",
          any("notifier" in line and "bridge" in line and "few seconds" in line
              for line in io.lines), True)
    code, io = go(codes={"pipeline_stage_e_setup.py verify": 10}, answers=[False])
    check("stage-e-declined-stops-before-the-idea-gate",
          (code, "pipeline_stage_e_setup.py run" in io.ran,
           "pipeline_stage_a_setup.py run" in io.ran), (EX_BLOCKED, False, False))
    # A RUN THAT FINISHED ON THE CATCH-UP POLLER'S CARD carries on; one that stopped on a
    # card part way does not (KIT-47).
    finished = {"code": "DONE", "enable": "DONE", "catch-up": "BLOCKED-ON-HUMAN",
                "heartbeat-monitor": "DONE", "handover": "ALREADY-DONE"}
    code, io = go(codes={"pipeline_stage_e_setup.py verify": 10,
                         "pipeline_stage_e_setup.py run": 10}, answers=[True],
                  steps=[{"code": "WOULD-CHANGE"}, finished])
    check("stage-e-run-ending-on-ck11-carries-on",
          ("pipeline_stage_a_setup.py run" in io.ran, code), (True, EX_BLOCKED))
    # …and so does one that finished on the status job's card, alone or with CK-11 (KIT-411).
    for name, cards in (("ck12", {"status": "BLOCKED-ON-HUMAN"}),
                        ("ck11-and-ck12", {"status": "BLOCKED-ON-HUMAN",
                                           "catch-up": "BLOCKED-ON-HUMAN"})):
        code, io = go(codes={"pipeline_stage_e_setup.py verify": 10,
                             "pipeline_stage_e_setup.py run": 10}, answers=[True],
                      steps=[{"code": "WOULD-CHANGE"},
                             dict(finished, **dict({"catch-up": "DONE"}, **cards))])
        check("stage-e-run-ending-on-%s-carries-on" % name,
              ("pipeline_stage_a_setup.py run" in io.ran, code,
               sum("does not block the idea gate" in line for line in io.lines)),
              (True, EX_BLOCKED, len(cards)))
    # Only the card is exempt: a status row with any other outcome is judged like every other
    # row, so a FAILED one never carries on (a real run with one also exits 1, not 10).
    code, io = go(codes={"pipeline_stage_e_setup.py verify": 10,
                         "pipeline_stage_e_setup.py run": 10}, answers=[True],
                  steps=[{"code": "WOULD-CHANGE"}, dict(finished, status="FAILED")])
    check("stage-e-run-with-the-status-job-failed-stops",
          ("pipeline_stage_a_setup.py run" in io.ran, code), (False, EX_BLOCKED))
    code, io = go(codes={"pipeline_stage_e_setup.py verify": 10,
                         "pipeline_stage_e_setup.py run": 10}, answers=[True],
                  steps=[{"code": "WOULD-CHANGE"}, {"code": "DONE", "dry-run": "BLOCKED-ON-HUMAN"}])
    check("stage-e-run-stopped-on-a-card-part-way-stops",
          ("pipeline_stage_a_setup.py run" in io.ran, code), (False, EX_BLOCKED))
    code, io = go(codes={"pipeline_stage_e_setup.py verify": 10,
                         "pipeline_stage_e_setup.py run": 10}, answers=[True],
                  steps=[{"code": "WOULD-CHANGE"}, {"code": "DONE", "catch-up": "BLOCKED-ON-HUMAN"}])
    check("stage-e-run-that-never-reached-its-last-step-stops",
          ("pipeline_stage_a_setup.py run" in io.ran, code), (False, EX_BLOCKED))
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
           io.asked, code), (False, True, [], EX_BLOCKED))
    # A RUN THAT STOPPED PART WAY IS NEVER CARRIED PAST — even with its code moved, it may
    # have stopped before reloading the review jobs (the review round's high finding).
    code, io = go(codes={"pipeline_stage_e_setup.py verify": 10,
                         "pipeline_stage_e_setup.py run": 10}, answers=[True],
                  steps=[{"code": "WOULD-CHANGE"}, {"code": "DONE", "daemons": "FAILED"}])
    check("stage-e-run-that-stopped-part-way-stops",
          (code, "pipeline_stage_a_setup.py run" in io.ran,
           any("unloaded" in line for line in io.lines)), (10, False, True))
    check("stage-e-run-that-stopped-part-way-names-the-notifier-and-bridge",
          any("notifier or Slack bridge job it names as off" in line for line in io.lines), True)
    for rc in (1, 4):
        code, io = go(codes={"pipeline_stage_e_setup.py verify": 10,
                             "pipeline_stage_e_setup.py run": rc}, answers=[True],
                      steps=[{"code": "WOULD-CHANGE"}, {"code": "DONE"}])
        check("stage-e-run-exit-%d-stops" % rc,
              (code, "pipeline_stage_a_setup.py run" in io.ran), (rc, False))
    # A CHECK THAT FAILED OR COULD NOT MEASURE IS NOT WORK OUTSTANDING: it stops, whatever
    # an older record says, and names the way to install without Stage E.
    for rc in (1, 2, 4):
        code, io = go(codes={"pipeline_stage_e_setup.py verify": rc},
                      steps={"code": "ALREADY-DONE"})
        check("stage-e-check-exit-%d-stops-without-running-anything" % rc,
              (code, "pipeline_stage_e_setup.py run" in io.ran,
               "pipeline_stage_a_setup.py run" in io.ran, io.asked), (rc, False, False, []))
    check("stage-e-check-failure-names-the-skip",
          any("--skip-stage-e" in line for line in io.lines), True)
    # ONLY THIS CHECK'S OWN ROWS ARE READ.
    code, io = go(codes={"pipeline_stage_e_setup.py verify": 10},
                  steps={"code": "ALREADY-DONE", "handover": "BLOCKED-ON-HUMAN"})
    check("stage-e-record-read-since-the-check-began", io.since, ["2026-10-03T00:00:00Z"])
    # CTRL-C: the installer it reached finishes its clean-up, and nothing after it runs.
    for prog in ("pipeline_stage_e_setup.py verify", "pipeline_stage_e_setup.py run"):
        io = FakeIo(codes=dict(healthy, **{"pipeline_stage_e_setup.py verify": 10,
                                           "pipeline_stage_e_setup.py run": 10}),
                    answers=[True], steps={"code": "WOULD-CHANGE"})
        io.interrupt_on.add(prog)
        code = main(base, io=io)
        check("ctrl-c-in-%s-stops-everything-after-it" % prog.split()[-1],
              ("pipeline_stage_a_setup.py run" in io.ran,
               any("interrupted" in line for line in io.lines), code != EX_OK),
              (False, True, True))
        if prog.endswith("verify"):
            # Stopped AT the check: nothing asked, and Stage E's installer never started.
            check("ctrl-c-in-the-check-asks-nothing-and-runs-nothing",
                  (io.asked, "pipeline_stage_e_setup.py run" in io.ran), ([], False))
    io = FakeIo(codes=dict(healthy, **{"sync_user_skills.py": 0}))
    io.interrupt_on.add("sync_user_skills.py")
    main(base, io=io)
    check("ctrl-c-in-the-skills-check-stops-everything-after-it",
          ("pipeline_stage_e_setup.py verify" in io.ran, "pipeline_stage_a_setup.py run" in io.ran),
          (False, False))
    # THE REAL RECORD READER keeps only rows stamped at or after the check began, and says
    # nothing at all when the `code` row is older.
    import json as _json
    home = os.path.join(tmp, "home")
    os.makedirs(os.path.join(home, ".stage-e-setup"))
    with open(os.path.join(home, ".stage-e-setup", "state.json"), "w") as fh:
        _json.dump({"steps": {"code": {"outcome": "ALREADY-DONE", "at": "2026-10-03T10:00:05Z"},
                              "old": {"outcome": "WOULD-CHANGE", "at": "2026-10-01T00:00:00Z"}}},
                   fh)
    saved_home = os.environ.get("HOME")
    os.environ["HOME"] = home
    try:
        check("record-reader-keeps-only-fresh-rows",
              Io().stage_e_steps("2026-10-03T10:00:00Z"), {"code": "ALREADY-DONE"})
        check("record-reader-refuses-a-stale-code-row",
              Io().stage_e_steps("2026-10-03T11:00:00Z"), None)
    finally:
        if saved_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = saved_home
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

    # 6b. THE SWEEP is run when its conf exists, between Stage E and the idea gate, and its card never holds the gate.
    code, io = go()
    check("no-sweep-conf-skips-the-sweep", "pipeline_sweep_setup.py run" in io.ran, False)
    with open(sweep_conf, "w") as fh:
        fh.write("ROLE_ACCOUNT=_x\n")
    code, io = go(codes={"pipeline_sweep_setup.py run": 0})
    check("sweep-runs-after-stage-e-before-the-idea-gate",
          (code, [k for k in io.ran if k.startswith("pipeline_s")]),
          (EX_OK, ["pipeline_stage_e_setup.py verify", "pipeline_sweep_setup.py run", "pipeline_stage_a_setup.py run"]))
    code, io = go(codes={"pipeline_sweep_setup.py run": 10})
    check("sweep-card-does-not-hold-the-idea-gate",
          (code, "pipeline_stage_a_setup.py run" in io.ran), (EX_BLOCKED, True))
    for rc in (1, 2, 3, 4, 5):
        code, io = go(codes={"pipeline_sweep_setup.py run": rc})
        check("sweep-exit-%d-stops-before-the-idea-gate" % rc,
              (code, "pipeline_stage_a_setup.py run" in io.ran), (rc, False))
    os.unlink(sweep_conf)

    # 7. CTRL-C REACHES THE INSTALLER AND ITS CLEAN-UP RUNS TO THE END. Real processes:
    #    a terminal's Ctrl-C goes to the whole process group, and a wrapper that did not
    #    step aside would kill the installer a quarter of a second in.
    import signal
    import time
    mark = os.path.join(tmp, "cleanup-done")
    child = ("import time,sys\ntry:\n    time.sleep(30)\nexcept KeyboardInterrupt:\n"
             "    time.sleep(1.5)\n    open(%r,'w').write('ok')\n    sys.exit(10)\n" % mark)
    driver = ("import sys; sys.path.insert(0, %r)\nimport pipeline_install as pi\n"
              "io = pi.Io()\ncode, _ = io.run([sys.executable, '-c', %r])\n"
              "sys.exit(0 if (code == 10 and io.interrupted) else 1)\n"
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

    # 9. THE LINES AT THE END (KIT-586): one record per part, exactly one of six outcomes, and an
    #    exit code that is 0 only when nothing is unfinished.
    kit_file = lambda name: os.path.join(KIT, name)           # noqa: E731

    def by_id(io):
        return dict((r["id"], r["outcome"]) for r in (io.records or []))

    code, io = go()
    got = by_id(io)
    check("lines-one-record-per-part-with-a-check-or-a-step",
          (code, got.get("one-command"), got.get("skills"), got.get("review"),
           got.get("idea-gate"), got.get("cleanup"), got.get("paging")),
          (EX_OK, OUT_OK, OUT_OK, OUT_OK, OUT_OK, OUT_OFF, OUT_OFF))
    check("lines-records-are-data",
          all(sorted(r) == ["detail", "id", "name", "outcome"] and r["outcome"] in OUTCOMES
              for r in io.records), True)
    check("lines-printed-once-per-record",
          sum(1 for ln in io.lines if ln.startswith("  ") and ln.split("  ")[2:3]
              and ln.strip().split("  ")[0] in OUTCOMES), len(io.records))
    check("lines-nothing-waiting-says-so", "Nothing is waiting on you." in io.lines, True)
    check("lines-name-the-parts-with-no-check",
          any(ln.startswith("No check of their own: ") for ln in io.lines), True)
    check("lines-parts-with-no-check-are-not-records",
          [r for r in io.records if r["id"] == "guards"], [])
    check("lines-a-part-off-never-runs-its-check",
          "pipeline_notifier_setup.py run" in io.measured, False)

    # up to date vs updated, from the step's own result
    code, io = go(codes={"sync_user_skills.py --apply": 0, "sync_user_skills.py": [1]},
                  answers=[True])
    check("lines-skills-replaced-is-updated", (by_id(io)["skills"], code), (OUT_UPDATED, EX_OK))
    code, io = go(codes={"pipeline_stage_e_setup.py verify": 10,
                         "pipeline_stage_e_setup.py run": 0}, answers=[True])
    check("lines-stage-e-updated-covers-its-parts",
          (by_id(io)["review"], by_id(io)["job-health"], code), (OUT_UPDATED, OUT_UPDATED, EX_OK))
    io = FakeIo(codes=dict(healthy))
    io.measure_codes = {"pipeline_stage_a_setup.py run": 10}
    code = main(base, io=io)
    check("lines-idea-gate-installed-after-work-is-updated",
          (by_id(io)["idea-gate"], code), (OUT_UPDATED, EX_OK))

    # waiting on you, and the short list
    code, io = go(codes={"sync_user_skills.py": 1}, answers=[False])
    check("lines-skills-left-differing-is-waiting",
          (by_id(io)["skills"], code), (OUT_WAITING, EX_BLOCKED))
    check("lines-waiting-without-a-card-names-the-part",
          any(ln.startswith("Written procedures a session runs as slash commands is waiting on "
                            "you; its own lines above") for ln in io.lines), True)
    check("lines-waiting-says-nothing-is-waiting-never",
          "Nothing is waiting on you." in io.lines, False)
    paging_conf = kit_file("notifier.conf")
    io = FakeIo(codes=dict(healthy))
    io.present = {paging_conf}
    io.measure_codes = {"pipeline_notifier_setup.py run": 10}
    io.measure_out = {"pipeline_notifier_setup.py run": "CK-N1 is yours\nthen CK-N3 and CK-N10"}
    code = main(base, io=io)
    check("lines-a-check-at-10-is-waiting",
          (by_id(io)["paging"], code), (OUT_WAITING, EX_BLOCKED))
    only = io.lines.index("Only you can do these:")
    signs = io.lines.index("Sign-offs still asked for:")
    check("lines-short-list-splits-by-kind-and-matches-whole-card-ids",
          ([ln.split()[0] for ln in io.lines[only + 1:signs]],
           [ln.split()[0] for ln in io.lines[signs + 1:signs + 2]],
           any("CK-N2" in ln or "CK-N4" in ln for ln in io.lines)),
          (["CK-N1"], ["CK-N3"], False))
    check("lines-card-id-prefix-is-not-a-match",
          (cards_named("see CK-N10", "CK-N1"), cards_named("see CK-N1.", "CK-N1")), (False, True))
    io.measure_out = {"pipeline_notifier_setup.py run": "something is outstanding"}
    io.lines[:] = []
    main(base, io=io)
    check("lines-waiting-with-no-card-in-its-output-names-the-part",
          any(ln.startswith("Messages you when only a person can act is waiting on you")
              for ln in io.lines), True)
    code, io = go(codes={"pipeline_stage_a_setup.py run": 10})
    io2 = FakeIo(codes=dict(healthy, **{"pipeline_stage_a_setup.py run": 10}))
    io2.measure_out = {"pipeline_stage_a_setup.py run": "waiting at CA-DELIVERY"}
    main(base, io=io2)
    check("lines-a-step-part-waiting-takes-its-cards-from-its-check",
          any(ln.startswith("  CA-DELIVERY") for ln in io2.lines), True)
    # a step that ended on a card alone: parts naming no card of their own are not waiting,
    # but only when the output names at least one card at all
    io = FakeIo(codes=dict(healthy, **{"pipeline_stage_e_setup.py verify": 10}),
                steps={"code": "ALREADY-DONE", "handover": "BLOCKED-ON-HUMAN"})
    io.measure_codes = {"pipeline_stage_e_setup.py verify": 10}
    io.measure_out = {"pipeline_stage_e_setup.py verify": "CK-11 waits"}
    code = main(base, io=io)
    check("lines-card-only-stage-e-waits-only-where-a-card-is-named",
          (by_id(io)["catch-up"], by_id(io)["findings"], code), (OUT_WAITING, OUT_OK, EX_BLOCKED))
    io = FakeIo(codes=dict(healthy, **{"pipeline_stage_e_setup.py verify": 10}),
                steps={"code": "ALREADY-DONE", "handover": "BLOCKED-ON-HUMAN"})
    io.measure_codes = {"pipeline_stage_e_setup.py verify": 10}
    main(base, io=io)
    check("lines-card-only-with-no-card-read-keeps-every-part-waiting",
          (by_id(io)["catch-up"], by_id(io)["findings"]), (OUT_WAITING, OUT_WAITING))

    # could not be measured, and 4 outranks 10
    for name, measured in (("exit-1", 1), ("exit-4", 4), ("exit-5-no-password", 5),
                           ("did-not-finish", None)):
        io = FakeIo(codes=dict(healthy))
        io.present = {paging_conf}
        io.measure_codes = {"pipeline_notifier_setup.py run": measured}
        code = main(base, io=io)
        check("lines-check-%s-is-unmeasured-and-exits-4" % name,
              (by_id(io)["paging"], code), (OUT_UNMEASURED, EX_UNMEASURED))
    code, io = go(argv=["--skip-stage-e"])
    check("lines-skipped-stage-e-is-unmeasured-not-up-to-date",
          (by_id(io)["review"], code), (OUT_UNMEASURED, EX_UNMEASURED))
    io = FakeIo(codes=dict(healthy))
    io.present = {paging_conf, kit_file("update.conf")}
    io.measure_codes = {"pipeline_notifier_setup.py run": 10, "pipeline_watch.py --conf": 1}
    code = main(base, io=io)
    check("lines-unmeasured-outranks-waiting", code, EX_UNMEASURED)
    code, io = go(codes={"pipeline_stage_a_setup.py run": 10})
    check("lines-the-idea-gate-at-10-still-exits-10", code, EX_BLOCKED)
    io = FakeIo(codes=dict(healthy, **{"pipeline_stage_a_setup.py run": 10}))
    io.present = {paging_conf}
    io.measure_codes = {"pipeline_notifier_setup.py run": 1}
    check("lines-the-idea-gate-at-10-with-an-unmeasured-part-exits-4",
          main(base, io=io), EX_UNMEASURED)
    check("lines-another-code-of-the-idea-gate-is-passed-through",
          go(codes={"pipeline_stage_a_setup.py run": 77})[0], 77)
    check("lines-end-code-table",
          [end_code(c, recs, st) for c, recs, st in (
              (0, [{"outcome": OUT_OK}], False), (0, [{"outcome": OUT_WAITING}], False),
              (0, [{"outcome": OUT_NOT_REACHED}], False), (0, [], True), (10, [], True),
              (None, [{"outcome": OUT_UNMEASURED}, {"outcome": OUT_WAITING}], False))],
          [0, 10, 4, 4, 10, 4])
    check("lines-an-unreadable-parts-list-is-never-a-pass",
          end_code(0, [], False, unreadable=True), EX_UNMEASURED)

    # not reached: a stop part way, and Ctrl-C, still print every line
    code, io = go(git_state={"status": (0, " M scripts/x.py")})
    got = by_id(io)
    check("lines-a-stop-prints-the-lines-and-keeps-its-code",
          (code, got["one-command"], got["skills"], got["review"], got["idea-gate"]),
          (EX_USAGE, OUT_WAITING, OUT_NOT_REACHED, OUT_NOT_REACHED, OUT_NOT_REACHED))
    code, io = go(codes={"pipeline_stage_e_setup.py verify": 10}, answers=[False])
    check("lines-a-stop-at-10-stays-10-and-says-what-was-not-reached",
          (code, by_id(io)["review"], by_id(io)["idea-gate"]),
          (EX_BLOCKED, OUT_WAITING, OUT_NOT_REACHED))
    io = FakeIo(codes=dict(healthy))
    io.present = {paging_conf}
    io.interrupt_on.add("sync_user_skills.py")
    code = main(base, io=io)
    got = by_id(io)
    check("lines-ctrl-c-prints-the-lines-and-runs-no-more-checks",
          (code != EX_OK, got["skills"], got["idea-gate"], got["paging"], io.measured),
          (True, OUT_UNMEASURED, OUT_NOT_REACHED, OUT_NOT_REACHED, []))
    io = FakeIo(codes=dict(healthy))
    io.present = {paging_conf}
    io.interrupt_on.add("pipeline_stage_a_setup.py run")
    code = main(base, io=io)
    check("lines-ctrl-c-in-the-last-step-runs-no-more-checks",
          (code != EX_OK, by_id(io)["idea-gate"], by_id(io)["paging"], "pipeline_notifier_setup.py run" in io.measured),
          (True, OUT_UNMEASURED, OUT_NOT_REACHED, False))
    code, io = go(git_state={"rev-parse HEAD": [(0, "aaa"), (0, "bbb")]})
    check("lines-a-restart-that-returns-is-not-a-pass", code, EX_UNMEASURED)
    for marker in AGENT_ENV_MARKERS[:1]:
        code, io = go(env={marker: ""})
        check("lines-refusals-print-no-lines",
              (io.records, any("Where every part stands" in ln for ln in io.lines),
               io.measured), (None, False, []))
    code, io = go(uid=0)
    check("lines-root-prints-no-lines", (io.records, io.measured), (None, []))
    code, io = go(tty=False)
    check("lines-no-terminal-prints-no-lines", (io.records, io.measured), (None, []))

    # the parts list: read, never hard-coded; an unreadable one is said, and is not a pass
    io = FakeIo(codes=dict(healthy))
    io.parts_list = OSError("no list")
    code = main(base, io=io)
    check("lines-unreadable-parts-list-is-said-and-exits-4",
          (code, io.records, any("could not be read" in ln for ln in io.lines)),
          (EX_UNMEASURED, [], True))
    real = Io().parts()
    keys = dict((pt["id"], step_of(pt)) for pt in real)
    check("lines-steps-are-found-from-the-list",
          (keys["one-command"], keys["skills"], keys["cleanup"], keys["idea-gate"],
           set(k for k in keys.values() if k) == set(["checkout", "skills", "stage_e",
                                                      "sweep", "stage_a"]),
           all(keys[pt["id"]] == "stage_e" for pt in real
               if pt["check"] and pt["check"]["script"].endswith("pipeline_stage_e_setup.py"))),
          ("checkout", "skills", "sweep", "stage_a", True, True))
    one = [pt for pt in real if pt["id"] == "review-identity"][0]
    check("lines-a-check-argv-uses-the-conf-given-on-the-command-line",
          check_argv(one, {"stage-e.conf": "/x/y.conf"})[-1], "/x/y.conf")

    # a part's check cannot ask: stdin closed, no terminal, and a time limit
    import subprocess as _sp
    spied, real_popen = {}, _sp.Popen

    def spy(*a, **k):
        spied.update(k)
        return real_popen(*a, **k)
    _sp.Popen = spy
    try:
        got = Io().measure([sys.executable, "-c", "import sys; sys.exit(0 if sys.stdin.read() == '' else 9)"])
    finally:
        _sp.Popen = real_popen
    check("measure-closes-stdin-and-leaves-the-terminal",
          (got[0], spied.get("stdin") == _sp.DEVNULL, spied.get("start_new_session")),
          (0, True, True))
    check("measure-returns-the-code-and-the-output",
          Io().measure([sys.executable, "-c", "print('hello'); raise SystemExit(10)"]),
          (10, "hello\n"))
    got = Io().measure([sys.executable, "-c", "import time; time.sleep(30)"], timeout=1)
    check("measure-kills-a-check-that-does-not-finish", got[0], None)
    check("measure-a-program-that-cannot-start-is-not-a-pass",
          Io().measure(["/no/such/program"])[0], None)

    # 10. NO CHAT-LANE STEP (KIT-492). The tech lead's rules went with the cutover, so the one
    #     command runs no chat-lane installer, and its old flag is refused, not ignored.
    code, io = go()
    check("kit492-no-chat-lane-step",
          (code, [r for r in io.ran if "pipeline_chat_lane_setup.py" in r],
           any("tech lead's rules" in ln for ln in io.lines)), (EX_OK, [], False))
    import contextlib
    import io as io_module
    with contextlib.redirect_stderr(io_module.StringIO()):
        try:
            main(base + ["--chat-lane-conf", "chat-lane.conf"], io=FakeIo(codes=healthy))
            refused = None
        except SystemExit as exc:
            refused = exc.code
    check("kit492-the-old-flag-is-refused", refused, 2)

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
