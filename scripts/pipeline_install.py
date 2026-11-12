#!/usr/bin/env python3
"""The one command: bring this Mac's pipeline up to date, then install the idea gate.

    python3 scripts/pipeline_install.py

Run it in a terminal, as yourself, from the kit checkout you install from. It does
everything a machine can do and stops only for what a person must do. Run it again after
any stop: it picks up where it left off.

THE FIRST SCREEN, THE CONSENT AND THE RECORD (KIT-717; docs/adr/2026-11-11-one-consent-then-no-stops.md)
  After the three refusals it offers Automatic ("things just work") or Manual ("show me each
  change and ask"). The default is the mode in the consent record, or Automatic.
  Then one consent page, drawn from `pipeline-parts.json` for the parts this Mac runs (those
  whose settings file it finds): the background jobs it installs under a separate account,
  what may restart, what may be created in the tracker, the code host and the chat product,
  what it spends (the subscription only), what it never does, and the installers that may
  still ask their own question or show a card. You agree once by typing `yes`; anything else
  changes nothing and exits 10. Automatic then answers this command's own questions yes, out
  loud; Manual asks each one, as before.
  The consent is written to a record only root can write
  (/Library/Application Support/claude-pipeline/consent-record.json; `--consent-record` moves
  it): the mode, the page's words, each part on or off, each job's switch value, each ceiling.
  A later run trusts it only if the file and every folder above it are root's alone and nobody
  else can write them. A matching record asks nothing; a difference is shown, old and new, and
  asked again in either mode. A missing or untrusted record is never a match.

THE VALUES ONLY YOU HAVE (KIT-735; scripts/pipeline_ask.py)
  Between the first screen and the consent page, in both modes, it asks one plain question for
  each value that is still missing: the end date of each stored key that ends (`update.conf`'s
  CREDENTIAL_EXPIRIES; a key already inside its warning, or past, is asked again) and the tech
  lead's name (`bridge.conf`). Enter skips a date, or keeps the one on record; for the name it
  takes `Tech lead`. Nothing is written yet. The consent page then lists each answer as a file,
  a key, the old value and the new one, and your one yes covers them. Right after the yes, and
  before any installer, they are written and read back. Decline and nothing is written. A write
  that fails stops the run, naming the file. It does not ask again when it has started itself
  again on new code, and a second run with nothing missing asks nothing. `--update-conf` and
  `--bridge-conf` (hidden from the help) name the two files; a file that is absent asks nothing.

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
  In Automatic, a part whose installer ran with work to do, and that has steps only a person
  can do, keeps its real outcome and gains the note "its installer still asks its own
  questions; Automatic cannot answer them yet". The note changes no outcome, no exit code
  and not the short list.

  Two limits, plainly. "Off on this Mac" means the part's settings file is not in the kit
  folder (or the folder you named with --conf and its kin); settings kept elsewhere read as
  off. Checks that need the Mac password read "could not be measured", because they run
  without a terminal and cannot ask for it.

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
import json
import os
import re
import stat
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

    # Set once the person has agreed in Automatic: this command's OWN questions are then
    # answered yes, out loud, by the consent. Installers it runs are not touched by this.
    auto = False
    auto_answered = ()

    def ask(self, question):
        if self.auto:
            self.say("%s -> yes (you agreed to this in the consent)" % question)
            self.auto_answered = tuple(self.auto_answered) + (question,)
            return True
        return self.prompt(question)

    def prompt(self, question):
        try:
            return input("%s [y/N] " % question).strip().lower() in ("y", "yes")
        except EOFError:
            return False

    def line(self, prompt):
        """One typed line for the first screen or the consent, or None at the end of input."""
        try:
            return input(prompt)
        except EOFError:
            return None

    def read_record(self, path):
        """(state, data, why) for the consent record: see load_record."""
        return load_record(path)

    def read_conf(self, path):
        """{KEY: value} of a settings file, or None when it cannot be read."""
        try:
            with open(path, encoding="utf-8") as fh:
                return parse_conf(fh.read())
        except (OSError, ValueError):
            return None

    # The one administrator session of this run, kept so its keep-alive outlives the call.
    _sudo = None

    def make_sudo(self):
        """The same once-per-run password session the installers use (never a second way)."""
        import pipeline_stage_e_setup as se
        return se.SudoSession()

    def sudo_once(self, why):
        """True when administrator access is held for the rest of this run."""
        import pipeline_stage_e_setup as se
        if self._sudo is None:
            self._sudo = self.make_sudo()
        try:
            return bool(self._sudo.acquire(why, ""))
        except se.NoPrivilege:
            return False

    def write_record(self, path, text):
        """Install `text` at `path` as root:wheel 0644, in a root-owned folder made with sudo
        when it is missing. None when done, else why not. The text is written to a private
        temp file first; sudo only copies it."""
        import shutil
        import tempfile
        private = tempfile.mkdtemp(prefix="consent-record-")     # made 0700: only this user
        tmp = os.path.join(private, "record.json")
        try:
            os.chmod(private, 0o700)
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            for argv in record_install_argvs(os.path.isdir(os.path.dirname(path)), tmp, path):
                code, _out = self.run(argv)
                if code != 0:
                    return "%s ended with exit %d" % (" ".join(argv[:3]), code)
            # A program running as this user could have swapped the temp file before sudo
            # copied it. So the installed, root-owned file is read back and compared byte for
            # byte with what was meant to be written. Nothing is removed on a mismatch, and
            # nothing is trusted: the caller stops.
            with open(path, "rb") as fh:
                installed = fh.read(RECORD_MAX_BYTES + 1)
            if installed != text.encode("utf-8"):
                return ("the installed record does not match what was meant to be written "
                        "(the temp file may have been swapped), so it is not trusted")
            return None
        except OSError as exc:
            return str(exc)
        finally:
            shutil.rmtree(private, ignore_errors=True)

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

    # The values only a person has, and writing them (KIT-735). `kit` is the folder whose
    # parts list and settings files say which stored keys this Mac has.
    kit = KIT

    def today(self):
        """Today's date in UTC, from `now`, so a scripted clock moves it."""
        import datetime
        return datetime.date.fromisoformat(self.now()[:10])

    def missing_values(self, update_conf, bridge_conf):
        """The questions for every value still missing: a read of the two settings files that
        changes nothing (pipeline_ask.missing_values)."""
        import pipeline_ask
        return pipeline_ask.missing_values(update_conf, bridge_conf, self.kit, self.today())

    def write_settings(self, changes):
        """Write what the person typed and the consent page listed, and read it back. None when
        all of it is written, else why not, naming the file (pipeline_ask.write_answers)."""
        import pipeline_ask
        return pipeline_ask.write_answers(self, changes, self.kit)


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


def missing(path):
    """Where a settings file was looked for, in words: in the kit folder, or the folder it
    was named in (--conf and its kin can point elsewhere)."""
    here = os.path.dirname(os.path.abspath(path))
    where = "the kit folder" if here == KIT else here
    return "no %s in %s" % (os.path.basename(path), where)


def look_first(io, rep, key):
    """The exit code of the part's own read-only check, run before a step that can only
    `run`, so the step can say whether it changed anything. None when the parts list gave
    no check or it did not finish. Nothing here prints, asks or changes anything."""
    argv = rep.checks.get(key)
    if not argv or io.interrupted:
        return None
    code, _out = io.measure(argv)
    return code


ASKED_DETAIL = "its installer still asks its own questions; Automatic cannot answer them yet"


def note_run_ok(rep, key, before):
    """The installer's `run` ended at 0: everything holds. Whether it had to change
    anything is what the check before it said. An installer that had work to do (or whose
    work was not measured) is taken to have asked its own questions in this run: in Automatic
    its line says so, as a note only."""
    if before != 0:
        rep.asked.add(key)
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
        # Steps whose installer ran with work to do. In Automatic, such a step's parts that
        # have person steps are named as waiting on you: the installer still asks its own.
        self.asked = set()

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


def step_checkout(io, argv, no_pull, rep, pulled_from=None, mode=None):
    """None to carry on, or an exit code. May restart this program on the new code."""
    heading(io, "1. The kit checkout")
    io.say("Kit checkout: %s" % KIT)
    if no_pull:
        io.say("Not pulled (--no-pull).")
        if pulled_from:
            # The run this command started itself after a pull that moved the code.
            _code, now = git(io, "rev-parse", "--short", "HEAD")
            rep.note("checkout", OUT_UPDATED,
                     "moved from %s to %s" % (pulled_from[:7], now or "the latest"))
        else:
            # A person's own --no-pull: nothing compared this checkout with origin.
            rep.note("checkout", OUT_UNMEASURED, "used as it is, without pulling")
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
        io.restart([os.path.abspath(__file__)] + list(argv)
                   + ["--pulled-from", before, "--no-pull"]
                   + (["--chosen-mode", mode] if mode else []))
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
        rep.note("stage_e", OUT_OFF, missing(conf))
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
            rep.asked.add("stage_e")
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
        # stopped before the steps that set the review jobs up, so "the code is current" says
        # nothing about whether they are done. It loads the jobs it unloaded again on its way
        # out, and says NOT DONE for any it could not (KIT-661). The next pass's check tells
        # the two apart: if all that is left then is a card, it carries on.
        rep.note_code("stage_e", code, "its installer")
        return stop(io, STAGE_E_MEANS.get(code, "Stage E's installer stopped part way "
                                                "(exit %d). The lines above say where." % code)
                    + " It loads the review jobs it unloaded again on its way out. Any it says "
                      "NOT DONE for stays off until it is loaded: it prints the line. A notifier "
                      "or Slack bridge job it names as off stays off until it is loaded. Clear "
                      "what it names, then run this command again.",
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
        rep.note("sweep", OUT_OFF, missing(conf))
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
# The first screen, the one consent, and the record only root can write (KIT-717)
# --------------------------------------------------------------------------- #
MODE_AUTO, MODE_MANUAL = "automatic", "manual"
MODE_WORDS = {MODE_AUTO: "Automatic", MODE_MANUAL: "Manual"}
# Where the record lives is itself a setting (--consent-record, hidden from the help).
DEFAULT_RECORD = "/Library/Application Support/claude-pipeline/consent-record.json"
RECORD_VERSION = 1
RECORD_MAX_BYTES = 1000000
# A switch whose key looks like a secret is recorded as set or unset, never by value.
SECRET_KEY_NAME = re.compile(r"(^|_)(KEY|TOKEN|SECRET|PASSWORD|CREDENTIALS?)($|_)")
SPEND_LINE = "It spends your Claude subscription only. Nothing else is charged."
NEVER_LINE = ("It never merges, approves or labels a pull request, never changes a guard, "
              "and never reads a key back.")


def first_screen(io, default):
    """Automatic or Manual, asked right after the three refusals. The default is the mode in
    the trusted consent record, or Automatic. None when no usable answer came."""
    heading(io, "0b. How should this run go?")
    io.say("  1  Automatic: things just work. You agree once; after that it asks nothing of its own.")
    io.say("  2  Manual: show me each change and ask.")
    for _ in range(3):
        reply = io.line("Type 1 or 2, or press Enter for %s: " % MODE_WORDS[default])
        if reply is None:
            return None
        word = reply.strip().lower()
        if word == "":
            return default
        if word in ("1", "a", "automatic"):
            return MODE_AUTO
        if word in ("2", "m", "manual"):
            return MODE_MANUAL
        io.say("Please type 1 or 2.")
    return None


def step_ask(io, update_conf, bridge_conf):
    """The changes the person typed, as a list; nothing is written yet (KIT-735). One plain question
    for each value only a person has that is still missing, in both modes. A file it cannot ask
    about is said in a line. Prints nothing when nothing is missing and nothing could not be asked."""
    import pipeline_ask
    found = io.missing_values(update_conf, bridge_conf)
    notes = list(getattr(found, "notes", ()))
    if not found and not notes:
        return []
    heading(io, "0b2. Values only you have")
    for note in notes:
        io.say(note)
    if not found:
        return []
    io.say("I need %d thing%s from you. Nothing is written until you agree on the next page."
           % (len(found), "" if len(found) == 1 else "s"))
    changes, _skipped = pipeline_ask.ask_all(io, found)
    return changes


def parse_conf(text):
    """{KEY: value} from a KEY=value settings file. Comments and anything else are skipped."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, eq, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not eq or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            continue
        quoted = re.match(r"^([\"'])(.*?)\1(\s+#.*)?$", value)
        if quoted:
            value = quoted.group(2)
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        out[key] = value
    return out


def switch_value(key, conf):
    """What a job's switch key says in its settings file. None when absent. A key that looks
    like a secret is said as '(set)', never by value."""
    if conf is None or key not in conf:
        return None
    return "(set)" if SECRET_KEY_NAME.search(key.upper()) else conf[key]


def candidates(io, parts, files, skip_stage_e):
    """[(part, on)] for each part this command acts on or looks at, judged the way the end
    lines judge it: a step's part by its step, any other part with a check by its settings file
    being found. Parts with neither are not candidates."""
    out = []
    for part in parts:
        key = step_of(part)
        if key in ("checkout", "skills", "stage_a"):
            on = True
        elif key == "stage_e":
            on = (not skip_stage_e) and io.exists(files["stage-e.conf"])
        elif key == "sweep":
            on = io.exists(files["sweep.conf"])
        elif part.get("check"):
            path = settings_path(part, files)
            on = bool(path) and io.exists(path)
        else:
            continue
        out.append((part, bool(on)))
    return out


def snapshot(io, cands, files):
    """Each consent-relevant setting: {parts: {id: on}, jobs: {id/label: {key, value}},
    ceilings: {id: ceiling}}. Plain data, so it can be compared and recorded."""
    parts_state, jobs_state, ceilings, confs = {}, {}, {}, {}
    for part, on in cands:
        parts_state[part["id"]] = on
        ceilings[part["id"]] = part.get("ceiling")
        path = settings_path(part, files)
        for job in part.get("jobs") or []:
            switch = job.get("switch")
            value = None
            if switch and on and path and path.endswith(".conf"):
                if path not in confs:
                    confs[path] = io.read_conf(path)
                value = switch_value(switch["key"], confs[path])
            jobs_state["%s/%s" % (part["id"], job["label"])] = {
                "key": switch["key"] if switch else None, "value": value}
    return {"parts": parts_state, "jobs": jobs_state, "ceilings": ceilings}


def consent_lines(cands):
    """The consent page, as lines, drawn from the parts list for the parts that are on."""
    on = [part for part, is_on in cands if is_on]
    lines = ["WHAT YOU ARE AGREEING TO", ""]
    lines.append("Installed: background jobs, under a separate account of their own.")
    jobbed = [p for p in on if p.get("jobs")]
    for part in jobbed:
        lines.append("  - %s: %d job(s)" % (part["name"], len(part["jobs"])))
    if not jobbed:
        lines.append("  - none for the parts on this Mac")
    lines.append("")
    lines.append("May be restarted:")
    restarts = [(p["name"], r) for p in on for r in p.get("restarts") or []]
    for name, text in restarts:
        lines.append("  - %s: %s" % (name, text))
    if not restarts:
        lines.append("  - nothing")
    lines.append("")
    lines.append("May be created or changed in the tracker, the code host, the chat product "
                 "and this Mac:")
    made = [(p["name"], c) for p in on for c in p.get("consent") or []]
    for name, text in made:
        lines.append("  - %s: %s" % (name, text))
    if not made:
        lines.append("  - nothing")
    lines.append("")
    lines.append(SPEND_LINE)
    lines.append(NEVER_LINE)
    lines.append("")
    lines.append("These installers may still ask their own question or show a card to sign:")
    asking = {}
    for part in on:
        if part.get("person_steps") and part.get("installer"):
            names = asking.setdefault(part["installer"], [])
            if part["name"] not in names:
                names.append(part["name"])
    for installer in sorted(asking):
        lines.append("  - %s, for: %s" % (installer, "; ".join(asking[installer])))
    if not asking:
        lines.append("  - none for the parts on this Mac")
    lines.append("Whatever only a person can do stays on the short list at the end.")
    return lines


def build_record(mode, lines, snap, when):
    return {"version": RECORD_VERSION, "agreed_at": when, "mode": mode,
            "consent": list(lines), "settings": snap}


def show_value(group, value):
    if group == "parts":
        return "on" if value else "off"
    if group == "jobs":
        key = (value or {}).get("key")
        return "no switch" if not key else "%s=%s" % (key, (value or {}).get("value"))
    return "none" if value is None else str(value)


def record_diffs(rec, mode, snap, lines):
    """What differs from the record, in words, old and new. Empty means a match."""
    diffs = []
    if rec["mode"] != mode:
        diffs.append("mode: was %s, now %s" % (MODE_WORDS[rec["mode"]], MODE_WORDS[mode]))
    old = rec["settings"]
    for group, label in (("parts", "part"), ("jobs", "job"), ("ceilings", "ceiling")):
        was, now = old.get(group) or {}, snap[group]
        for key in sorted(set(was) | set(now)):
            if key not in was:
                diffs.append("%s %s: not in the record, now %s" % (label, key, show_value(group, now[key])))
            elif key not in now:
                diffs.append("%s %s: was %s, no longer listed" % (label, key, show_value(group, was[key])))
            elif was[key] != now[key]:
                diffs.append("%s %s: was %s, now %s" % (label, key, show_value(group, was[key]),
                                                       show_value(group, now[key])))
    if rec["consent"] != list(lines):
        diffs.append("the words of the consent page changed")
    return diffs


def record_install_argvs(folder_exists, tmp, path):
    """The commands that put the record in place: the folder (root:wheel 755) when it is
    missing, then the file (root:wheel 0644) copied from the private temp file."""
    install = "/usr/bin/install"
    argvs = []
    if not folder_exists:
        argvs.append(["sudo", install, "-d", "-o", "root", "-g", "wheel", "-m", "755",
                      os.path.dirname(path)])
    argvs.append(["sudo", install, "-o", "root", "-g", "wheel", "-m", "0644", tmp, path])
    return argvs


def _components(path):
    found, cur = [], path
    while True:
        found.append(cur)
        parent = os.path.dirname(cur)
        if parent == cur:
            return list(reversed(found))
        cur = parent


def untrusted_why(path, lstat=os.lstat, realpath=os.path.realpath):
    """None when the file and every folder above it belong to root and nobody else can
    write them; else why not. A link is followed only if root owns it and everything it
    leads through passes too. Raises FileNotFoundError when something is missing."""
    path = os.path.abspath(path)
    lstat(path)                       # a missing record is "none", whoever owns the folders above
    real = realpath(path)
    for walk in (path,) if real == path else (path, real):
        for cur in _components(walk):
            st = lstat(cur)
            final = cur == walk
            if st.st_uid != 0:
                return "%s is not owned by root" % cur
            if stat.S_ISLNK(st.st_mode):
                if final and walk == path:
                    return "%s is a link" % cur
                continue
            if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                return "%s can be written by someone other than root" % cur
            if final and not stat.S_ISREG(st.st_mode):
                return "%s is not a regular file" % cur
            if not final and not stat.S_ISDIR(st.st_mode):
                return "%s is not a folder" % cur
    return None


def _valid_record(data):
    try:
        return (data["version"] == RECORD_VERSION and data["mode"] in MODE_WORDS
                and isinstance(data["agreed_at"], str)
                and isinstance(data["consent"], list)
                and all(isinstance(x, str) for x in data["consent"])
                and all(isinstance(data["settings"].get(g), dict)
                        for g in ("parts", "jobs", "ceilings")))
    except (KeyError, TypeError, AttributeError):
        return False


def _read_small(path):
    with open(path, "rb") as fh:
        raw = fh.read(RECORD_MAX_BYTES + 1)
    if len(raw) > RECORD_MAX_BYTES:
        raise ValueError("larger than %d bytes" % RECORD_MAX_BYTES)
    return raw.decode("utf-8")


def load_record(path, lstat=os.lstat, realpath=os.path.realpath, read=_read_small):
    """(state, record, why). State is 'none' (no file), 'ok' (a record only root could have
    written) or 'unreadable' (anything else: never a match, so the consent is asked)."""
    try:
        why = untrusted_why(path, lstat, realpath)
    except FileNotFoundError:
        return "none", None, "no record yet"
    except OSError as exc:
        return "unreadable", None, "it could not be inspected (%s)" % exc
    if why:
        return "unreadable", None, why
    try:
        data = json.loads(read(path))
    except (OSError, ValueError) as exc:
        return "unreadable", None, "it could not be read (%s)" % exc
    if not _valid_record(data):
        return "unreadable", None, "it is not a consent record this command wrote"
    return "ok", data, ""


def step_consent(io, record_path, mode, state, rec, why, cands, files, unreadable, writes=()):
    """None to carry on, or an exit code. Asks the one consent unless the trusted record
    already matches this mode, these settings and these words; then writes the record.

    `writes` is what the person typed in the ask (KIT-735). The page lists it after its own lines,
    and the one yes covers it. It is no part of the page the record keeps, so a date or a name
    never makes the record differ. When the record matches and there is something to write, only
    that list is shown, and the yes is for it alone."""
    heading(io, "0c. What this run may do")
    if unreadable:
        return stop(io, "the list of parts could not be read, so there is no consent page to "
                        "agree to. Nothing was changed.", EX_UNMEASURED)
    lines = consent_lines(cands)
    snap = snapshot(io, cands, files)
    record_needed = True
    if state == "ok":
        diffs = record_diffs(rec, mode, snap, lines)
        if not diffs and not writes:
            io.say("This matches what you agreed to on %s: the same mode, settings and words. "
                   "Nothing to agree to again." % rec["agreed_at"])
            return None
        if not diffs:
            io.say("This matches what you agreed to on %s: the same mode, settings and words."
                   % rec["agreed_at"])
            record_needed = False
        else:
            io.say("Since you agreed (%s), this changed:" % rec["agreed_at"])
            for diff in diffs:
                io.say("  - %s" % diff)
    elif state == "unreadable":
        io.say("The record of your last consent cannot be trusted (%s), so you are asked "
               "again." % why)
    else:
        io.say("There is no record of a consent yet, so you are asked once.")
    io.say("")
    if record_needed:
        for line in lines:
            io.say(line)
        io.say("")
    if writes:
        import pipeline_ask
        for line in pipeline_ask.settings_lines(writes) + [""]:
            io.say(line)
    if record_needed:
        io.say("%s: %s" % (MODE_WORDS[mode], "after you agree it answers its own questions yes. "
                           "Each installer above may still ask its own."
                           if mode == MODE_AUTO else
                           "it shows each change and asks, as before."))
    reply = io.line("Type yes to agree. Anything else changes nothing: " if record_needed else
                    "Type yes to write these settings. Anything else changes nothing: ")
    if (reply or "").strip().lower() != "yes":
        return stop(io, "nothing was agreed to, so nothing was changed. Run this again when "
                        "you are ready.", EX_BLOCKED)
    if not record_needed:
        return None
    if not io.sudo_once("it saves the record of what you agreed to in a folder only the "
                        "administrator can write, so a Claude session cannot edit it."):
        return stop(io, "saving the record needs your Mac password and did not get it. "
                        "Nothing was changed. Run this again and type it at the prompt.", 5)
    failed = io.write_record(record_path, json.dumps(
        build_record(mode, lines, snap, io.now()), indent=2, sort_keys=True) + "\n")
    if failed:
        return stop(io, "the record of your consent could not be saved (%s). Nothing else was "
                        "changed." % failed, 1)
    state, _rec, why = io.read_record(record_path)
    if state != "ok":
        io.say("Saved, but it cannot be trusted yet (%s), so you will be asked again next "
               "time." % why)
    return None


def step_write_settings(io, writes):
    """None to carry on, or an exit code. Writes what the person typed and the consent page
    listed, now that they said yes, and reads it back (KIT-735). It runs before any installer."""
    if not writes:
        return None
    io.say("")
    io.say("Writing the settings listed above.")
    failed = io.write_settings(writes)
    if failed:
        return stop(io, "your settings were not all written: %s. Nothing after this ran. Fix "
                        "that, then run this again; it asks again for what is still missing."
                    % failed, 1)
    return None


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
    if code == 5 or (code is not None and re.search(r"password", out or "", re.I)
                     and re.search(r"need|requir|ask|enter|type", out or "", re.I)):
        return OUT_UNMEASURED, ("it needs your Mac password, which a check run without a "
                                "terminal cannot ask for")
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
            if (io.auto and key in rep.asked and part.get("person_steps")
                    and rec["outcome"] != OUT_NOT_REACHED):
                # A NOTE, never an outcome: the part keeps what its own result said, so a
                # clean run exits 0 and the short list holds only what is still left to do.
                rec["detail"] = "%s; %s" % (rec["detail"], ASKED_DETAIL) if rec["detail"] \
                    else ASKED_DETAIL
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
                rec["detail"] = missing(path)
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


def commit_id(value):
    """The hidden --pulled-from value: a commit id, nothing else."""
    if not re.match(r"^[0-9a-f]{7,64}$", value):
        raise argparse.ArgumentTypeError("not a commit id")
    return value


def step_consent_and_mode(io, record_path, mode, state, rec, why, parts, files, skip_stage_e,
                          unreadable, writes=()):
    """The consent, then what the person typed is written (KIT-735), then (in Automatic) this
    command's own questions are answered by it."""
    cands = [] if unreadable else candidates(io, parts, files, skip_stage_e)
    code = step_consent(io, record_path, mode, state, rec, why, cands, files, unreadable, writes)
    if code is None:
        code = step_write_settings(io, writes)
    if code is None:
        io.auto = mode == MODE_AUTO
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
    parser.add_argument("--pulled-from", default=None, help=argparse.SUPPRESS, type=commit_id)
    parser.add_argument("--chosen-mode", default=None, choices=sorted(MODE_WORDS),
                        help=argparse.SUPPRESS)
    parser.add_argument("--consent-record", default=DEFAULT_RECORD, help=argparse.SUPPRESS)
    parser.add_argument("--update-conf", default=os.path.join(KIT, "update.conf"),
                        help=argparse.SUPPRESS)
    parser.add_argument("--bridge-conf", default=os.path.join(KIT, "bridge.conf"),
                        help=argparse.SUPPRESS)
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
    # up front; if it cannot be read there is no consent page to agree to, so nothing runs and
    # the end says so, in words.
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

    # THE FIRST SCREEN. Only now, past the three refusals, is anything read: first the consent
    # record (its mode is the default), then the person is asked which way this run goes.
    record_path = os.path.abspath(args.consent_record)
    state, rec, why = io.read_record(record_path)
    default = rec["mode"] if state == "ok" else MODE_AUTO
    try:
        if args.pulled_from:
            # This command started itself again after a pull: the person already chose.
            mode = args.chosen_mode or default
            io.say("Carrying on as %s, the way you chose before the pull." % MODE_WORDS[mode])
        else:
            mode = first_screen(io, default)
    except KeyboardInterrupt:
        mode = None
    if mode is None:
        code = stop(io, "no way to run was chosen, so nothing was changed. Run it again and "
                        "type 1 or 2.", EX_BLOCKED)
        return finish(io, parts, rep, files, False, code, True, unreadable)

    # THE ASK (KIT-735). Right after the first screen and before the consent, in both modes: one
    # plain question for each value only a person has that is still missing. Nothing is written
    # here; what was typed is listed on the consent page and written after the yes. Not after a
    # restart on new code (the person answered before it), and not when the parts list is unread.
    writes = []
    if not args.pulled_from and not unreadable:
        try:
            writes = step_ask(io, os.path.abspath(args.update_conf),
                              os.path.abspath(args.bridge_conf))
        except KeyboardInterrupt:
            code = stop(io, "interrupted while asking, so nothing was changed. Run it again when "
                            "you are ready.", EX_BLOCKED)
            return finish(io, parts, rep, files, False, code, True, unreadable)

    steps = (lambda: step_consent_and_mode(io, record_path, mode, state, rec, why, parts, files,
                                           args.skip_stage_e, unreadable, writes),
             lambda: step_checkout(io, argv, args.no_pull, rep, args.pulled_from, mode),
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
                 steps=None, typed=None, record=None, conf=None):
        # The first screen and the consent read lines from `typed` (default: Manual, then
        # yes), so the cases written before them keep their meaning. `record` is what the
        # consent record reads as: (state, data, why). Everything the new seams do is logged.
        self.typed = ["2", "yes"] if typed is None else list(typed)
        self.line_log = []                 # (prompt, programs run so far, record reads so far)
        self.record = record or ("none", None, "no record yet")
        self.record_reads = []
        self.conf = dict(conf or {})
        self.conf_reads = []
        # The ask (KIT-735) finds nothing unless a case scripts it, so a machine's own settings
        # files can never reach a case written before it. Its battery (pipeline_ask.py) puts the
        # real seams back over files of its own.
        self.questions = []
        self.asked_files = []              # (update conf, bridge conf) it was asked to read
        self.settings_failure = None
        self.settings_log = []             # the changes it was asked to write
        self.events = []                   # "sudo", "write" and "settings", in order
        self.sudo_ok = True
        self.write_failure = None
        self.written = []                  # (path, text)
        self.write_at = None               # programs run when the record was written
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
                         "rev-parse --short": (0, "abc1234"),
                         "log": (0, "aaa the latest")}, **(git_state or {}))

    @staticmethod
    def key(argv):
        if argv[0] == "git":
            rest = argv[3:]
            if rest[:2] == ["rev-parse", "--abbrev-ref"]:
                return "rev-parse --abbrev-ref"
            if rest[:2] == ["rev-parse", "HEAD"]:
                return "rev-parse HEAD"
            if rest[:2] == ["rev-parse", "--short"]:
                return "rev-parse --short"
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

    def prompt(self, question):
        self.asked.append(question)
        return self.answers.pop(0) if self.answers else False

    def line(self, prompt):
        self.line_log.append((prompt, len(self.ran), len(self.record_reads)))
        return self.typed.pop(0) if self.typed else None

    def read_record(self, path):
        self.record_reads.append(path)
        return self.record

    def read_conf(self, path):
        self.conf_reads.append(path)
        return self.conf.get(path)

    def missing_values(self, update_conf, bridge_conf):
        self.asked_files.append((update_conf, bridge_conf))
        return self.questions

    def write_settings(self, changes):
        self.events.append("settings")
        self.settings_log.append(list(changes))
        return self.settings_failure

    def sudo_once(self, why):
        self.events.append("sudo")
        return self.sudo_ok

    def write_record(self, path, text):
        self.events.append("write")
        self.write_at = len(self.ran)
        if self.write_failure:
            return self.write_failure
        self.written.append((path, text))
        self.record = ("ok", json.loads(text), "")
        return None

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
          (io.restarted is not None and "--no-pull" in io.restarted,
           "sync_user_skills.py" in io.ran), (True, False))
    code, io = go(argv=["--no-pull"])
    check("no-pull-skips-git", ("pull" in io.ran, code), (False, EX_UNMEASURED))
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
    check("lines-off-detail-says-where-it-looked",
          [r["detail"] for r in io.records if r["id"] == "paging"], ["no notifier.conf in the kit folder"])
    check("lines-off-detail-outside-the-kit-names-the-folder",
          [r["detail"] for r in io.records if r["id"] == "cleanup"], ["no sweep.conf in %s" % tmp])
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
    io.typed = ["2", "yes"]
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
    for name, rc, words in (("exit-5", 5, ""), ("its-own-words", 1, "Mac password needed to continue")):
        io = FakeIo(codes=dict(healthy))
        io.present = {paging_conf}
        io.measure_codes = {"pipeline_notifier_setup.py run": rc}
        io.measure_out = {"pipeline_notifier_setup.py run": words}
        main(base, io=io)
        check("lines-password-%s-detail" % name,
              [r["detail"] for r in io.records if r["id"] == "paging"],
              ["it needs your Mac password, which a check run without a terminal cannot ask for"])
    code, io = go(argv=["--no-pull"])
    check("lines-own-no-pull-is-unmeasured",
          ([(r["outcome"], r["detail"]) for r in io.records if r["id"] == "one-command"], code),
          ([(OUT_UNMEASURED, "used as it is, without pulling")], EX_UNMEASURED))
    code, io = go(argv=["--pulled-from", "abcdef1234", "--no-pull"], typed=["yes"])
    check("lines-restarted-after-a-pull-is-updated",
          ([(r["outcome"], r["detail"]) for r in io.records if r["id"] == "one-command"], code),
          ([(OUT_UPDATED, "moved from abcdef1 to abc1234")], EX_OK))
    code, io = go(git_state={"rev-parse HEAD": [(0, "aaa"), (0, "bbb")]})
    check("lines-restart-carries-the-old-commit-in-a-hidden-flag",
          io.restarted[-5:], ["--pulled-from", "aaa", "--no-pull", "--chosen-mode", "manual"])
    import contextlib
    import io as io_module
    with contextlib.redirect_stderr(io_module.StringIO()):
        try:
            main(base + ["--pulled-from", "not a sha"], io=FakeIo(codes=healthy))
            bad = None
        except SystemExit as exc:
            bad = exc.code
    check("lines-hidden-flag-refuses-a-non-commit", bad, 2)
    check("lines-hidden-flag-is-not-in-the-help", "pulled-from" in subprocess.run(
        [sys.executable, os.path.abspath(__file__), "--help"],
        capture_output=True, text=True).stdout, False)
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

    # 11. THE FIRST SCREEN, THE ONE CONSENT, AND THE RECORD ONLY ROOT CAN WRITE (KIT-717).
    import collections
    import copy
    real_parts = Io().parts()
    cmd = lambda io: "\n".join(io.lines)                          # noqa: E731
    status_job = "status/" + [j for pt in real_parts if pt["id"] == "status"
                              for j in pt["jobs"]][0]["label"]

    # 11a. The refusals come first and read nothing, the record included (point 5).
    refusals = [("marker:%s" % m, {"env": {m: ""}}) for m in AGENT_ENV_MARKERS]
    refusals += [("root", {"uid": 0}), ("no-terminal", {"tty": False})]
    for label, kw in refusals:
        code, io = go(**kw)
        check("kit717-refusal-reads-nothing:%s" % label,
              (io.record_reads, io.conf_reads, io.line_log, io.events, io.written, io.ran),
              ([], [], [], [], [], []))

    # 11b. The first screen (point 1): right after the refusals, before any step.
    code, io = go(typed=["1", "yes"])
    check("kit717-first-screen-is-before-any-step",
          (io.line_log[0][0].startswith("Type 1 or 2"), io.line_log[0][1]), (True, 0))
    check("kit717-first-screen-says-both-ways-in-plain-words",
          ("Automatic: things just work" in cmd(io), "Manual: show me each change and ask" in cmd(io)),
          (True, True))
    code, io = go(typed=["", "yes"])
    check("kit717-default-is-automatic-with-no-record",
          (io.auto, json.loads(io.written[0][1])["mode"]), (True, MODE_AUTO))
    for typed_mode, want in (("1", True), ("a", True), ("automatic", True), ("2", False),
                             ("m", False), ("Manual", False)):
        code, io = go(typed=[typed_mode, "yes"])
        check("kit717-first-screen-takes:%s" % typed_mode, io.auto, want)
    prior = go(typed=["2", "yes"])[1].written[0][1]
    saved = json.loads(prior)
    code, io = go(typed=[""], record=("ok", saved, ""))
    check("kit717-default-is-the-records-mode", (io.auto, len(io.line_log)), (False, 1))
    code, io = go(typed=["", "yes"], record=("unreadable", None, "x is not owned by root"))
    check("kit717-an-untrusted-record-gives-no-default", io.auto, True)
    for name, typed in (("garbage", ["x", "y", "z"]), ("end-of-input", [])):
        code, io = go(typed=typed)
        check("kit717-no-usable-answer-changes-nothing:%s" % name,
              (code, io.ran, io.written, io.events), (EX_BLOCKED, [], [], []))
        check("kit717-no-usable-answer-still-prints-the-lines:%s" % name,
              "Where every part stands" in cmd(io), True)

    # 11c. One consent, typed yes (point 2). Anything else changes nothing and exits 10.
    for reply in ("y", "no", "", "yes please", "ok", "yes!", " n", None):
        code, io = go(typed=["1"] + ([reply] if reply is not None else []))
        check("kit717-consent-%r-changes-nothing" % reply,
              (code, io.ran, io.written, io.events), (EX_BLOCKED, [], [], []))
    for reply in ("yes", " YES "):
        code, io = go(typed=["1", reply])
        check("kit717-consent-%r-agrees" % reply, (code, len(io.written)), (EX_OK, 1))
    code, io = go(typed=["1", "yes"])
    page = cmd(io)
    review = [pt for pt in real_parts if pt["id"] == "review"][0]
    paging = [pt for pt in real_parts if pt["id"] == "paging"][0]
    check("kit717-page-has-each-parts-consent-lines",
          all(c in page for c in review["consent"]), True)
    check("kit717-page-has-what-may-restart", all(r in page for r in review["restarts"]), True)
    check("kit717-page-has-the-jobs-under-a-separate-account",
          ("background jobs, under a separate account" in page,
           "%s: %d job(s)" % (review["name"], len(review["jobs"])) in page), (True, True))
    check("kit717-page-has-the-fixed-lines",
          (SPEND_LINE in page, NEVER_LINE in page, "subscription only" in page,
           "never merges, approves or labels" in page), (True, True, True, True))
    check("kit717-page-names-the-installers-that-still-ask",
          "scripts/pipeline_stage_e_setup.py, for: " in page, True)
    check("kit717-page-leaves-out-a-part-that-is-off",
          any(c in page for c in paging["consent"]), False)
    io = FakeIo(codes=dict(healthy), typed=["1", "yes"])
    io.present = {kit_file("notifier.conf")}
    main(base, io=io)
    check("kit717-page-adds-a-part-whose-settings-are-found",
          all(c in cmd(io) for c in paging["consent"]), True)
    code, io = go(argv=["--skip-stage-e"], typed=["1", "yes"])
    check("kit717-skipped-stage-e-is-off-in-page-and-record",
          (any(c in cmd(io) for c in review["consent"]),
           json.loads(io.written[0][1])["settings"]["parts"]["review"]), (False, False))

    # 11d. Automatic asks none of its own; Manual asks with today's words (points 3, 4).
    drift = lambda: {"sync_user_skills.py": [1], "sync_user_skills.py --apply": 0,   # noqa: E731
                     "pipeline_stage_e_setup.py verify": 10, "pipeline_stage_e_setup.py run": 0}
    code, io = go(codes=drift(), typed=["1", "yes"])
    check("kit717-automatic-asks-nothing-of-its-own",
          (io.asked, code, "pipeline_stage_e_setup.py run" in io.ran,
           "sync_user_skills.py --apply" in io.ran), ([], EX_OK, True, True))
    # An installer that ran with work to do still asks its own questions. Its part keeps its
    # real outcome and gains a note; the note moves no outcome, exit code or short list.
    recs = dict((r["id"], r) for r in io.records)
    code2, io2 = go(codes=drift(), typed=["2", "yes"], answers=[True, True])
    recs2 = dict((r["id"], r) for r in io2.records)
    check("kit717-automatic-notes-a-part-whose-installer-still-asks",
          (recs["review"]["detail"], recs["skills"]["detail"]), (ASKED_DETAIL, "replaced with the kit's"))
    check("kit717-automatic-note-is-printed-on-the-part-line",
          any(ln.strip().startswith(OUT_UPDATED) and ASKED_DETAIL in ln for ln in io.lines), True)
    check("kit717-the-note-does-not-change-an-outcome",
          ([(r["id"], r["outcome"]) for r in io.records],
           recs["review"]["outcome"]),
          ([(r["id"], r["outcome"]) for r in io2.records], OUT_UPDATED))
    check("kit717-the-note-does-not-change-the-exit-code", (code, code2), (EX_OK, EX_OK))
    check("kit717-the-note-does-not-change-the-short-list",
          ([ln for ln in io.lines if ln.endswith("is waiting on you; its own lines above say what for.")
            or ln in ("Only you can do these:", "Sign-offs still asked for:")],
           "Nothing is waiting on you." in io.lines),
          ([ln for ln in io2.lines if ln.endswith("is waiting on you; its own lines above say what for.")
            or ln in ("Only you can do these:", "Sign-offs still asked for:")], True))
    check("kit717-manual-shows-no-note",
          (any(ASKED_DETAIL in ln for ln in io2.lines), recs2["review"]["detail"]), (False, ""))
    code3, io3 = go(typed=["1", "yes"])
    check("kit717-automatic-with-nothing-to-do-notes-nothing",
          (code3, any(ASKED_DETAIL in ln for ln in io3.lines)), (EX_OK, False))
    io4 = FakeIo(codes=dict(healthy), typed=["1", "yes"])
    io4.measure_codes = {"pipeline_stage_a_setup.py run": 10}
    code4 = main(base, io=io4)
    check("kit717-automatic-notes-the-idea-gate-when-its-run-had-work-and-it-finished",
          ({r["id"]: (r["outcome"], r["detail"]) for r in io4.records}["idea-gate"], code4),
          ((OUT_UPDATED, ASKED_DETAIL), EX_OK))
    check("kit717-the-note-says-exactly-what-the-ticket-says",
          ASKED_DETAIL, "its installer still asks its own questions; Automatic cannot answer them yet")
    io6 = FakeIo(codes=dict(healthy), typed=["1", "yes"])
    io6.measure_codes = {"pipeline_stage_a_setup.py run": None}
    code6 = main(base, io=io6)
    check("kit717-the-note-keeps-the-detail-the-part-already-had",
          ({r["id"]: r["detail"] for r in io6.records}["idea-gate"], code6),
          ("installed and proven; whether it changed anything was not measured; " + ASKED_DETAIL,
           EX_OK))
    code5, io5 = go(codes={"pipeline_stage_a_setup.py run": 10}, typed=["1", "yes"])
    check("kit717-automatic-an-installer-that-ends-waiting-is-still-waiting",
          ({r["id"]: r["outcome"] for r in io5.records}["idea-gate"], code5), (OUT_WAITING, EX_BLOCKED))
    check("kit717-automatic-answers-yes-out-loud",
          (io.auto_answered, any("agreed to this in the consent" in ln for ln in io.lines)),
          (("Replace them with the kit's now?", "Update Stage E now?"), True))
    code, io = go(codes=drift(), typed=["2", "yes"], answers=[True, True])
    check("kit717-manual-asks-with-todays-words",
          (io.asked, io.auto_answered, code),
          (["Replace them with the kit's now?", "Update Stage E now?"], (), EX_OK))
    code, io = go(codes=drift(), typed=["2", "yes"], answers=[True, False])
    check("kit717-manual-no-still-stops",
          (code, "pipeline_stage_e_setup.py run" in io.ran), (EX_BLOCKED, False))
    check("kit717-the-words-other-sessions-depend-on-stay",
          ("Update Stage E now?" in src, "Replace them with the kit's now?" in src), (True, True))

    # 11e. The record (point 6): written after the consent, once, as the settings stand.
    code, io = go(typed=["1", "yes"], conf={se_conf: {"STATUS_INTERVAL_SECONDS": "900"}})
    got = json.loads(io.written[0][1])
    check("kit717-record-is-written-once-after-the-consent-and-before-any-step",
          (io.events, io.write_at, len(io.written), io.written[0][0]),
          (["sudo", "write"], 0, 1, os.path.abspath(DEFAULT_RECORD)))
    check("kit717-record-holds-mode-page-and-settings",
          (got["version"], got["mode"], got["consent"][0], sorted(got["settings"]),
           got["agreed_at"]),
          (RECORD_VERSION, MODE_AUTO, "WHAT YOU ARE AGREEING TO",
           ["ceilings", "jobs", "parts"], "2026-10-03T00:00:00Z"))
    check("kit717-record-page-is-the-page-shown",
          all(ln in io.lines for ln in got["consent"] if ln), True)
    check("kit717-record-has-each-part-on-or-off",
          (got["settings"]["parts"]["review"], got["settings"]["parts"]["paging"],
           got["settings"]["parts"]["idea-gate"]), (True, False, True))
    check("kit717-record-has-each-jobs-switch-value-from-the-parts-list",
          (got["settings"]["jobs"][status_job],
           got["settings"]["jobs"]["review/" + review["jobs"][0]["label"]]),
          ({"key": "STATUS_INTERVAL_SECONDS", "value": "900"}, {"key": None, "value": None}))
    check("kit717-record-has-each-ceiling-all-null-today",
          (set(got["settings"]["ceilings"].values()), "review" in got["settings"]["ceilings"]),
          ({None}, True))
    check("kit717-a-switch-that-looks-like-a-secret-is-never-recorded-by-value",
          (switch_value("LINEAR_API_KEY", {"LINEAR_API_KEY": "abc"}),
           switch_value("LINEAR_API_KEY", {}), switch_value("STATUS_FILE", {"STATUS_FILE": "x"})),
          ("(set)", None, "x"))
    check("kit717-conf-reader",
          parse_conf("# c\nA=1\nexport B='two words'\nC=\"x\" # k\nD=3 # note\nbad line\n=z\n"),
          {"A": "1", "B": "two words", "C": "x", "D": "3"})
    code, io = go(typed=["1", "no"])
    check("kit717-no-record-without-the-consent", (io.written, io.events), ([], []))
    io = FakeIo(codes=dict(healthy), typed=["1", "yes"])
    io.sudo_ok = False
    code = main(base, io=io)
    check("kit717-no-password-changes-nothing-and-exits-5",
          (code, io.written, io.ran, io.events), (5, [], [], ["sudo"]))
    io = FakeIo(codes=dict(healthy), typed=["1", "yes"])
    io.write_failure = "disk full"
    code = main(base, io=io)
    check("kit717-a-record-that-cannot-be-saved-stops-everything-else",
          (code, io.ran, any("disk full" in ln for ln in io.lines)), (1, [], True))
    code, io = go(argv=["--consent-record", "/x/y/z.json"], typed=["1", "yes"])
    check("kit717-the-record-place-is-a-setting",
          (io.record_reads[0], io.written[0][0]), ("/x/y/z.json", "/x/y/z.json"))
    check("kit717-the-record-flag-is-hidden-and-the-default-is-the-library-folder",
          ("consent-record" in subprocess.run([sys.executable, os.path.abspath(__file__), "--help"],
                                              capture_output=True, text=True).stdout,
           DEFAULT_RECORD),
          (False, "/Library/Application Support/claude-pipeline/consent-record.json"))
    code, io = go(argv=["--pulled-from", "abcdef1234", "--no-pull", "--chosen-mode", "manual"],
                  typed=["yes"], record=("unreadable", None, "x"))
    check("kit717-a-restart-keeps-the-mode-chosen-before-it-when-the-record-is-untrusted",
          io.auto, False)
    check("kit717-the-chosen-mode-flag-is-hidden",
          "chosen-mode" in subprocess.run([sys.executable, os.path.abspath(__file__), "--help"],
                                          capture_output=True, text=True).stdout, False)
    code, io = go(argv=["--pulled-from", "abcdef1234", "--no-pull"], typed=["yes"])
    check("kit717-a-restart-does-not-ask-the-first-screen-again",
          [p[0].startswith("Type yes") for p in io.line_log], [True])

    # 11f. A later run reads it: a match asks nothing; any difference is shown and asked again,
    #      in either mode. A missing or untrusted record is never a match.
    for mode_word, typed_mode in ((MODE_MANUAL, "2"), (MODE_AUTO, "1")):
        base_rec = json.loads(go(typed=[typed_mode, "yes"])[1].written[0][1])
        code, io = go(typed=[typed_mode], record=("ok", base_rec, ""))
        check("kit717-a-matching-record-asks-no-consent:%s" % mode_word,
              (len(io.line_log), io.events, io.written, code),
              (1, [], [], EX_OK))
        check("kit717-a-matching-record-says-so:%s" % mode_word,
              any(ln.startswith("This matches what you agreed to on ") for ln in io.lines), True)

        def edited(fn):
            rec = copy.deepcopy(base_rec)
            fn(rec)
            return ("ok", rec, "")

        diff_cases = (
            ("a-part-turned-on", edited(lambda r: r["settings"]["parts"].update(review=False)),
             "part review: was off, now on", {}, set()),
            ("a-job-switch", edited(lambda r: r["settings"]["jobs"].update(
                {status_job: {"key": "STATUS_INTERVAL_SECONDS", "value": "900"}})),
             "was STATUS_INTERVAL_SECONDS=900, now STATUS_INTERVAL_SECONDS=None", {}, set()),
            ("a-ceiling", edited(lambda r: r["settings"]["ceilings"].update(review=5)),
             "ceiling review: was 5, now none", {}, set()),
            ("the-words", edited(lambda r: r["consent"].append("an extra line")),
             "the words of the consent page changed", {}, set()),
            ("a-part-missing-from-the-record",
             edited(lambda r: r["settings"]["parts"].pop("paging")),
             "part paging: not in the record, now off", {}, set()),
            ("a-part-found-now", ("ok", base_rec, ""), "part paging: was off, now on", {},
             {kit_file("notifier.conf")}),
            ("a-switch-changed-in-the-settings-file", ("ok", base_rec, ""),
             "now STATUS_INTERVAL_SECONDS=900", {se_conf: {"STATUS_INTERVAL_SECONDS": "900"}},
             set()),
            ("an-untrusted-record", ("unreadable", None, "/x is not owned by root"),
             "cannot be trusted (/x is not owned by root)", {}, set()),
            ("no-record", ("none", None, "no record yet"), "no record of a consent yet", {}, set()),
        )
        for name, rec, want_line, conf, present in diff_cases:
            io = FakeIo(codes=dict(healthy), typed=[typed_mode, "yes"], record=rec, conf=conf)
            io.present = set(present)
            code = main(base, io=io)
            check("kit717-%s-asks-the-consent-again:%s" % (name, mode_word),
                  (any(want_line in ln for ln in io.lines), len(io.line_log), len(io.written)),
                  (True, 2, 1))
    code, io = go(typed=["1", "yes"], record=("ok", dict(saved), ""))
    check("kit717-a-change-of-mode-alone-is-shown-and-asked",
          (any("mode: was Manual, now Automatic" in ln for ln in io.lines), len(io.line_log)),
          (True, 2))
    code, io = go(typed=["1", "no"], record=("ok", dict(saved), ""))
    check("kit717-a-changed-setting-declined-changes-nothing",
          (code, io.ran, io.written), (EX_BLOCKED, [], []))
    io = FakeIo(codes=dict(healthy), typed=["1"])
    io.parts_list = OSError("no list")
    code = main(base, io=io)
    check("kit717-no-parts-list-no-consent-no-run",
          (code, io.ran, io.written, io.events), (EX_UNMEASURED, [], [], []))

    # 11g. Trust (point 6): only root's own record, in folders only root can write.
    St = collections.namedtuple("St", "st_uid st_mode")
    dir_, reg, lnk = stat.S_IFDIR, stat.S_IFREG, stat.S_IFLNK
    where = DEFAULT_RECORD
    folders = ["/", "/Library", "/Library/Application Support",
               "/Library/Application Support/claude-pipeline"]
    good_text = json.dumps(build_record(MODE_AUTO, ["a"], {"parts": {}, "jobs": {}, "ceilings": {}},
                                        "2026-11-11T00:00:00Z"))

    def tree(over=None, drop=()):
        nodes = dict((f, St(0, dir_ | 0o755)) for f in folders)
        nodes[where] = St(0, reg | 0o644)
        nodes.update(over or {})
        for gone in drop:
            nodes.pop(gone, None)

        def lstat(path):
            if path not in nodes:
                raise FileNotFoundError(path)
            return nodes[path]
        return lstat

    def judged(over=None, text=good_text, drop=(), realpath=lambda p: p):
        def read(_p):
            if isinstance(text, Exception):
                raise text
            return text
        return load_record(where, tree(over, drop), realpath, read)

    check("kit717-trust-root-owned-chain-is-trusted", judged()[0], "ok")
    check("kit717-trust-the-record-comes-back-whole", judged()[1]["mode"], MODE_AUTO)
    for name, over in (
            ("file-owned-by-the-user", {where: St(501, reg | 0o644)}),
            ("file-group-writable", {where: St(0, reg | 0o664)}),
            ("file-other-writable", {where: St(0, reg | 0o646)}),
            ("file-is-a-link", {where: St(0, lnk | 0o755)}),
            ("file-is-a-folder", {where: St(0, dir_ | 0o755)}),
            ("own-folder-owned-by-the-user", {folders[3]: St(501, dir_ | 0o755)}),
            ("own-folder-group-writable", {folders[3]: St(0, dir_ | 0o775)}),
            ("a-folder-above-owned-by-the-user", {folders[2]: St(501, dir_ | 0o755)}),
            ("a-folder-above-world-writable-and-sticky", {folders[1]: St(0, dir_ | 0o1777)}),
            ("the-top-folder-group-writable", {"/": St(0, dir_ | 0o775)}),
            ("a-folder-above-is-a-link-the-user-made", {folders[2]: St(501, lnk | 0o755)})):
        check("kit717-trust-%s-is-unreadable" % name, judged(over)[0], "unreadable")
    check("kit717-trust-a-root-owned-link-above-is-followed-and-the-target-is-judged",
          judged({folders[2]: St(0, lnk | 0o755), "/priv": St(0, dir_ | 0o755),
                  "/priv/app": St(0, dir_ | 0o755), "/priv/app/claude-pipeline": St(0, dir_ | 0o755),
                  "/priv/app/claude-pipeline/consent-record.json": St(0, reg | 0o644)},
                 realpath=lambda p: "/priv/app/claude-pipeline/consent-record.json")[0], "ok")
    check("kit717-trust-a-root-owned-link-to-a-folder-the-user-owns-is-unreadable",
          judged({folders[2]: St(0, lnk | 0o755), "/priv": St(0, dir_ | 0o755),
                  "/priv/app": St(501, dir_ | 0o755), "/priv/app/claude-pipeline": St(0, dir_ | 0o755),
                  "/priv/app/claude-pipeline/consent-record.json": St(0, reg | 0o644)},
                 realpath=lambda p: "/priv/app/claude-pipeline/consent-record.json")[0],
          "unreadable")
    check("kit717-trust-no-file-is-none", judged(drop=[where])[0], "none")
    check("kit717-trust-no-folder-is-none", judged(drop=[where, folders[3]])[0], "none")
    for name, text in (("not-json", "{"), ("a-list", "[]"),
                       ("wrong-version", json.dumps(dict(json.loads(good_text), version=2))),
                       ("wrong-mode", json.dumps(dict(json.loads(good_text), mode="x"))),
                       ("consent-not-a-list", json.dumps(dict(json.loads(good_text), consent="x"))),
                       ("no-settings", json.dumps({k: v for k, v in json.loads(good_text).items()
                                                   if k != "settings"})),
                       ("too-big", ValueError("larger than a record can be"))):
        check("kit717-trust-%s-is-unreadable" % name, judged(text=text)[0], "unreadable")
    check("kit717-trust-an-error-inspecting-it-is-unreadable",
          load_record(where, lambda p: (_ for _ in ()).throw(PermissionError(p)),
                      lambda p: p)[0], "unreadable")
    mine = os.path.join(tmp, "mine.json")
    with open(mine, "w") as fh:
        fh.write(good_text)
    if os.getuid() != 0:
        check("kit717-trust-a-real-file-this-process-made-is-never-trusted",
              Io().read_record(mine)[0], "unreadable")
    check("kit717-trust-a-real-missing-file-is-none",
          Io().read_record(os.path.join(tmp, "absent", "r.json"))[0], "none")

    # 11h. Writing it: a private temp file, then sudo installs it root:wheel 0644.
    class Recorder(Io):
        def __init__(self, fail=None):
            self.argvs, self.copied, self.mode, self.fail, self.tmp = [], None, None, fail, None
            self.swap, self.folder_mode, self.corrupt = None, None, None

        def run(self, argv, capture=False):
            # Stands in for sudo install: -d makes the folder, the other copies the file.
            self.argvs.append(list(argv))
            if argv[-1].endswith("/r.json"):
                self.tmp = argv[-2]
                self.folder_mode = stat.S_IMODE(os.stat(os.path.dirname(self.tmp)).st_mode)
                if self.swap is not None:           # a program running as this user swaps it
                    with open(self.tmp, "w") as fh:
                        fh.write(self.swap)
                with open(self.tmp) as fh:
                    self.copied = fh.read()
                self.mode = stat.S_IMODE(os.stat(self.tmp).st_mode)
                if not self.fail:
                    os.makedirs(os.path.dirname(argv[-1]), exist_ok=True)
                    with open(argv[-1], "w") as fh:
                        fh.write(self.copied if self.corrupt is None else self.corrupt)
            return (self.fail if self.fail and argv[-1].endswith("/r.json") else 0), ""

    target = os.path.join(tmp, "newfolder", "r.json")
    rec_io = Recorder()
    check("kit717-write-returns-none-when-done", rec_io.write_record(target, "TEXT\n"), None)
    check("kit717-write-makes-the-folder-root-wheel-755-then-installs-the-file-root-wheel-0644",
          ([a[:-1] if a[2] == "-d" else a[:-2] for a in rec_io.argvs],
           rec_io.argvs[0][-1], rec_io.argvs[1][-1]),
          ([["sudo", "/usr/bin/install", "-d", "-o", "root", "-g", "wheel", "-m", "755"],
            ["sudo", "/usr/bin/install", "-o", "root", "-g", "wheel", "-m", "0644"]],
           os.path.dirname(target), target))
    check("kit717-write-uses-a-private-temp-file-that-is-removed",
          (rec_io.copied, rec_io.mode, rec_io.folder_mode, os.path.exists(rec_io.tmp),
           os.path.exists(os.path.dirname(rec_io.tmp))), ("TEXT\n", 0o600, 0o700, False, False))
    swapped = Recorder()
    swapped.swap = "SWAPPED\n"
    got_why = swapped.write_record(os.path.join(tmp, "swapped", "r.json"), "TEXT\n")
    check("kit717-write-a-swapped-temp-file-is-a-failure-that-says-so",
          ("does not match" in (got_why or ""), "not trusted" in (got_why or "")), (True, True))
    altered = Recorder()
    altered.corrupt = "NOT WHAT WAS COPIED\n"
    check("kit717-write-an-installed-file-that-differs-from-the-temp-file-is-a-failure",
          "does not match" in (altered.write_record(os.path.join(tmp, "altered", "r.json"),
                                                    "TEXT\n") or ""), True)
    check("kit717-write-a-swapped-file-removes-nothing",
          os.path.exists(os.path.join(tmp, "swapped", "r.json")), True)
    io = FakeIo(codes=dict(healthy), typed=["1", "yes"])
    io.write_failure = "the installed record does not match what was meant"
    check("kit717-a-mismatch-stops-the-run-before-anything-runs",
          (main(base, io=io), io.ran), (1, []))
    rec_io = Recorder()
    rec_io.write_record(os.path.join(tmp, "r.json"), "T")
    check("kit717-write-makes-no-folder-that-exists", len(rec_io.argvs), 1)
    check("kit717-write-says-why-when-sudo-fails",
          "exit 1" in (Recorder(fail=1).write_record(target, "T") or ""), True)

    # 11i. One password session, the one the installers use; a refusal is False.
    import pipeline_stage_e_setup as se_mod

    class Sudoer(Io):
        def __init__(self, validate):
            self.validate = validate

        def make_sudo(self):
            return se_mod.SudoSession(validate=self.validate, refresh=lambda: 0, interval=3600)

        def say(self, line=""):
            pass
    ok_io = Sudoer(lambda: 0)
    with contextlib.redirect_stdout(io_module.StringIO()):
        held = ok_io.sudo_once("why")
        again = ok_io.sudo_once("again")
        declined = Sudoer(lambda: 1).sudo_once("why")
    check("kit717-sudo-once-held", held, True)
    check("kit717-sudo-once-is-asked-once", (again, ok_io._sudo.acquisitions), (True, 1))
    ok_io._sudo.release()
    check("kit717-sudo-declined-is-false", declined, False)

    # 11j. The parts list: every part the command acts on, or installs a job for, has consent.
    check("kit717-every-part-acted-on-has-consent-lines",
          [pt["id"] for pt in real_parts if (step_of(pt) or pt.get("jobs"))
           and not pt.get("consent")], [])
    check("kit717-no-hard-coded-python-path", ("/usr/bin/" + "python3") in src, False)

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
