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
  `--bridge-conf` (hidden from the help) name the two files. A file that is not there asks
  nothing and is said in one line, once, with where it looked (`Not asked: no bridge.conf in the
  kit folder, so the tech lead's name was not asked.`).

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
  3c. The notifier (`scripts/pipeline_notifier_setup.py`), when this Mac has a `notifier.conf`
     (`--notifier-conf` names another). In Manual it runs the installer's own `run --dry-run`,
     shown as it runs. Exit 0 is "up to date". A step table with a WOULD-CHANGE row is work:
     it asks, then runs `run` on a yes ("updated"). Only a card left (a job to load, a ping to
     watch) is "waiting on you": nothing is asked, because `run` cannot clear it. A dry run that
     printed no table, or ended with any other code, is never "up to date". A `run` that ends
     on a card stops the command. With no `notifier.conf` the part is "off on this Mac" and
     nothing runs.
     In Automatic (KIT-850) it holds the Mac password once, then runs the installer's `check`,
     whose rows come back as one JSON document while its words are shown. A WOULD-CHANGE row
     is work, so it runs the installer's `apply` with {"consent": "yes"} piped in. `apply` asks
     nothing and switches the job on itself when its first pass would send no more messages
     than the part's ceiling in `pipeline-parts.json`. Above it the job stays installed and
     off, and that one line ("left off: ...") is the part's line at the end. An `apply` that
     ends on a card is carried past. Rows it cannot read are never "up to date".
  3d. The Slack bridge and the stand-up (`scripts/pipeline_bridge_setup.py`), when this Mac has a
     `bridge.conf` (`--bridge-conf` names another). It runs `install`, the installer's compare
     without `--apply`: the job's config, its job file and the stand-up's settings file against
     what `bridge.conf` composes. Exit 0 means they are level; exit 10 means one would be
     written, so it asks, then runs `install --apply` on a yes. The stand-up's switch is one of
     those files, so the same step covers it. After either, it reads the installer's `verify`
     once for what only a person can do (the job is loaded, a token): 0 is "up to date" or
     "updated", 10 is "waiting on you" (the idea gate is not held back), anything else is
     never "up to date". A declined update reads "waiting on you". A `install --apply` that ends
     on a card stops the command in Manual and is carried past in Automatic. With no
     `bridge.conf` the part is "off on this Mac" and nothing runs. Instant replies keep their
     own check at the end.
  3e. The usage job and the queue's morning job (`scripts/pipeline_queue_setup.py`), when this Mac
     has a `queue.conf` (`--queue-conf` names another). It runs the installer's own
     `run --dry-run`, shown as it runs. Exit 0 is "up to date". A step table with a WOULD-CHANGE
     row is work: it asks, then runs `run` on a yes ("updated"); that writes the queue's settings
     and both job files as the role account, and loads nothing. Only a card left (loading the
     jobs is yours) reads "waiting on you" and asks nothing. A dry run that printed no table, or
     ended with any other code, is never "up to date". A `run` that ends on a card stops the
     command in Manual and is carried past in Automatic. With no `queue.conf` the part is "off
     on this Mac" and nothing runs.
  3f. The validator's job (`scripts/pipeline_validator_setup.py`, KIT-547), optional, when this Mac
     has a `validator.conf` (`--validator-conf` names another). It runs under your own login: no
     account, and no Mac password is held for it. In Manual it runs the installer's own
     `run --dry-run`, and on a yes its `run`, which shows its plan and asks its own typed yes. In
     Automatic it runs the installer's `check`, then its `apply` with {"consent": "yes"} piped in,
     as for the notifier. The settings are written once, with every switch off, and never again.
     The job is switched on only when its first pass would decide nothing (its ceiling, 0); above
     that it stays off, and its one line says why. With no `validator.conf` the part is "off on
     this Mac" and nothing runs. Under a model this command refuses before it reads anything, and
     so does the validator's installer.
  3g. The hourly health check (`scripts/pipeline_watch.py install`), when this Mac has an
     `update.conf`. It reads, and never changes, whether the LaunchAgent is there, names this
     kit, this settings file and this interval, and is held by launchd. Level is "up to date".
     Missing or behind asks, then runs the installer, which shows a dry pass and asks for its own
     typed yes; the job is looked at again afterwards, and one not seen loaded is "could not be
     measured", never "updated". A declined or untyped yes is "waiting on you"; a job that could
     not be looked at is "could not be measured". With no `update.conf` it is "off on this Mac".
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

  Two limits, plainly. "Off on this Mac" means the part's settings file is in neither the kit
  folder (or the folder you named with --conf and its kin) nor the place this Mac says it
  keeps it: /Library/Application Support/claude-pipeline/settings-places.json, a file only
  root can write, that maps a settings file's name to its full path (KIT-752). The kit folder
  wins when the file is there. Checks that need the Mac password read "could not be measured",
  because they run without a terminal and cannot ask for it.

TO ONLY LOOK: --check (KIT-736)
  `pipeline_install.py --check` looks at every part and changes nothing. It needs no terminal,
  so a run with no person at one can use it. The refusals come first and print only themselves:
  inside a Claude session it exits 3, as root it exits 2. After that it asks nothing: no first
  screen, no consent page, no password, and the consent record is neither read nor written.
  It writes no settings file, makes no folder, installs no job and runs no `git fetch`, `pull`
  or `checkout`. The only commands it runs are each part's own read-only check, run the way the
  end lines run them (no terminal, stdin closed, a time limit), and two read-only git calls:
  `git rev-parse HEAD` and `git ls-remote --symref origin HEAD`. (Python itself may write its
  bytecode cache next to the scripts; PYTHONDONTWRITEBYTECODE=1 stops that too.)
  The first line says it only looked. Then the same one line per part and the same short list
  as a real run, each part judged the way the lines at the end judge it:
    - The kit checkout is judged by its commit alone. The same commit as the tip of origin's
      default branch is "up to date"; any other is "waiting on you". A read of the tip cannot
      tell behind from ahead, so it never says behind. An origin that cannot be reached or
      read is "could not be measured", and the line says which. The branch the checkout is on
      is not asked, so a detached commit is measured like any other.
    - Every other part: its settings file absent is "off on this Mac"; otherwise its own check
      runs once and is read the one way for every part. One exception, because the lines at
      the end read it so too: the skills check exits 1 for "differ from the kit's", which is
      "waiting on you". The review jobs share one check, and a look cannot tell a card from
      work, so each of their parts reads "waiting on you" when it exits 10. A part with no
      check is named on the closing line.
  `--skip-stage-e` and the settings-file flags work. `--no-pull`, `--pulled-from`,
  `--chosen-mode` and `--consent-record` do nothing with it.
  Exit: 0 every part is up to date or off on this Mac · 10 something waits on you · 4 something
  could not be measured, or the list of parts could not be read (4 comes before 10).

Usage:
    pipeline_install.py [--conf stage-a.conf] [--stage-e-conf stage-e.conf]
                        [--no-pull] [--skip-stage-e] [--notifier-conf notifier.conf] [--queue-conf queue.conf]
                        [--validator-conf validator.conf]
    pipeline_install.py --check [--conf stage-a.conf] [--stage-e-conf stage-e.conf]
                        [--sweep-conf sweep.conf] [--queue-conf queue.conf] [--skip-stage-e]
                        [--validator-conf validator.conf]
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
BRIDGE_SETUP = os.path.join(HERE, "pipeline_bridge_setup.py")
NOTIFIER_SETUP = os.path.join(HERE, "pipeline_notifier_setup.py")
QUEUE_SETUP = os.path.join(HERE, "pipeline_queue_setup.py")
VALIDATOR_SETUP = os.path.join(HERE, "pipeline_validator_setup.py")
WATCH = os.path.join(HERE, "pipeline_watch.py")

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

    def run_seen(self, argv):
        """(exit code, output): the installer's lines go to the person as they come, and are
        also kept, so this command can read what the installer found. Standard input stays
        the person's terminal, so a password prompt still works. Ctrl-C belongs to the
        installer, as in `run`."""
        import signal
        kept = []
        try:
            previous = signal.signal(signal.SIGINT,
                                     lambda _s, _f: setattr(self, "interrupted", True))
            try:
                # The child writes into a pipe, which a Python child buffers by blocks. Unbuffered, so
                # an installer's words (why it needs the Mac password) arrive before sudo asks.
                child = subprocess.Popen(argv, cwd=KIT, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT,
                                         env=dict(os.environ, PYTHONUNBUFFERED="1"),
                                         preexec_fn=lambda: signal.signal(signal.SIGINT,
                                                                          signal.SIG_DFL))
                for raw in iter(child.stdout.readline, b""):
                    text = raw.decode("utf-8", "replace")
                    kept.append(text)
                    sys.stdout.write(text)
                    sys.stdout.flush()
                return child.wait(), "".join(kept)
            finally:
                signal.signal(signal.SIGINT, previous)
        except (OSError, subprocess.SubprocessError) as exc:
            return 127, str(exc)

    def run_rows(self, argv, stdin_text=None):
        """(exit code, its standard output) of an installer's `check` or `apply` (KIT-850). Its
        human words reach the person as they come, on the stderr it shares with this program;
        its standard output — one JSON document of rows — is kept for this program to read.
        `stdin_text`, when given, goes in through a pipe that is then closed: answers never
        travel in a file or an argument. Without it, standard input is closed. Ctrl-C belongs
        to the installer, as in `run`."""
        import signal
        try:
            previous = signal.signal(signal.SIGINT,
                                     lambda _s, _f: setattr(self, "interrupted", True))
            try:
                child = subprocess.Popen(
                    argv, cwd=KIT, stdout=subprocess.PIPE,
                    stdin=subprocess.DEVNULL if stdin_text is None else subprocess.PIPE,
                    env=dict(os.environ, PYTHONUNBUFFERED="1"),
                    preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))
                out, _err = child.communicate(
                    None if stdin_text is None else stdin_text.encode("utf-8"))
                return child.returncode, out.decode("utf-8", "replace")
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

    def watch_level(self, conf_path):
        """(state, why) of the hourly health check's job: see watch_level. Reads only."""
        return watch_level(conf_path, os.path.expanduser("~"), os.getuid(), self.measure)

    def host_state(self):
        """(state, why) of this Mac's host (KIT-756); see host_state()."""
        return host_state()
    def places(self, path):
        """({settings file name: where this Mac keeps it}, why it was ignored) (KIT-752)."""
        return load_places(path)

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

    def level_settings(self, update_conf, watcher_conf):
        """The health check's heartbeat lines that differ from the watcher's copies: a read that changes nothing
        (pipeline_ask.level_settings)."""
        import pipeline_ask
        return pipeline_ask.level_settings(update_conf, watcher_conf)

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
    name = os.path.basename(path)
    if PLACES_WHY[0]:
        where += " (the places file at %s was not used: %s)" % (DEFAULT_PLACES, PLACES_WHY[0])
    if name in PLACES and os.path.abspath(PLACES[name]) != os.path.abspath(path):
        where += " (or at %s, where this Mac says it is kept)" % PLACES[name]
    return "no %s in %s" % (name, where)


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
        # Answers of checks already run, by argv: build_records reuses one rather than run it
        # twice. Empty in a real run, whose checks run after its steps. --check fills it.
        self.seen = {}

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
        io.say("If it moves the clone, it also stops the notifier and the tech lead, where "
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
                      "or tech lead job it names as off stays off until it is loaded. Clear "
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


# What an installer's compare says when it would write something, as opposed to a block only a person
# can clear (which running it again cannot). The selftest pins the line against the installer's source.
def has_work(text, line):
    """True when the compare printed `line`, the installer's own words for "I would write"."""
    return line in (text or "")


# What an installer that prints the Stage E step table says: a row whose outcome is WOULD-CHANGE
# is work its `run` would do; a row that is only a card is work for a person, which running it
# again cannot clear. The notifier installer prints this table; this file's selftest pins the
# word against the installer's own constant.
STEP_TABLE = "-- steps --"
WOULD_CHANGE_ROW = re.compile(r"^\s+\S+\s+WOULD-CHANGE(\s|$)", re.M)


def would_change(text):
    """True when the dry run's step table has work its `run` would do. A dry run that printed
    no table says nothing about what it found, so that counts as work too: it is asked about,
    and never read as up to date."""
    return STEP_TABLE not in (text or "") or bool(WOULD_CHANGE_ROW.search(text or ""))


# The steps that run an optional job's installer when its settings file is on this Mac: look
# first with the installer's own read-only pass, ask only when there is work, run it on a yes.
# `note` is said before the question, for what a run of it may do.
JOB_STEPS = (
    {"key": "notifier", "number": "3c", "title": "The notifier, what pages you",
     "noun": "The notifier", "conf": "notifier.conf", "script": NOTIFIER_SETUP,
     "look": ["run", "--dry-run"], "apply": ["run"],
     "note": "It writes the notifier's settings and its job file as the role account; it "
             "never loads the job.",
     # KIT-850: in Automatic, the installer's program-facing pair instead. `check` prints its
     # rows as one JSON document and asks nothing; `apply` takes the consent through a pipe,
     # asks nothing, and loads the job itself at or below its first-pass ceiling.
     "auto_check": ["check"], "auto_apply": ["apply"],
     "rows_schema": "notifier-setup-rows/1",
     "answers": {"schema": "notifier-answers/1", "consent": "yes"},
     "auto_note": "It writes the notifier's settings and its job file as the role account. It "
                  "switches the job on itself only when the job's first pass would send no "
                  "more messages than its ceiling; above that the job stays off, and the end "
                  "says why."},
    {"key": "bridge", "number": "3d", "title": "The Slack bridge and the stand-up",
     "noun": "The bridge", "conf": "bridge.conf", "script": BRIDGE_SETUP,
     # `install` without --apply compares the job's config, its plist and the stand-up's settings
     # file with what bridge.conf composes. It exits 10 for two different things: it would write
     # something (and prints `work_line`), or a block only a person can clear, before any write.
     # `verify` is read afterwards for what only a person can do.
     "work_line": "DRY RUN: nothing was written",
     "look": ["install"], "apply": ["install", "--apply"], "then": ["verify"],
     "note": "It writes the bridge's config, its job file and the stand-up's settings file as the "
             "role account; it never loads the job."},
    {"key": "queue", "number": "3e", "title": "The usage job and the queue's morning job",
     "noun": "The usage job and the queue", "conf": "queue.conf", "script": QUEUE_SETUP,
     "look": ["run", "--dry-run"], "apply": ["run"],
     "note": "It writes the queue's settings and its two job files (the usage reader and the morning "
             "job) as the role account; it never loads them."},
    # KIT-547: optional. The validator's job, under your own login: no role account, so no Mac
    # password is held for it (`sudo`: False). Its program-facing pair is the notifier's shape.
    {"key": "validator", "number": "3f", "title": "The validator's job, every switch off",
     "noun": "The validator", "conf": "validator.conf", "script": VALIDATOR_SETUP,
     "look": ["run", "--dry-run"], "apply": ["run"],
     "note": "It writes the validator's settings (once, every switch off), its config and its job "
             "file, under your own login. It shows them and asks its own typed yes.",
     "auto_check": ["check"], "auto_apply": ["apply"],
     "rows_schema": "validator-setup-rows/1",
     "answers": {"schema": "validator-answers/1", "consent": "yes"},
     "sudo": False,
     "auto_note": "It writes the validator's settings (once, every switch off), its config and its "
                  "job file, under your own login. It switches the job on itself only when the "
                  "job's first pass would decide nothing; above that the job stays off, and the "
                  "end says why."},
)


def job_done(io, spec, base, conf, rep, updated):
    """The settings are level. What is left for a person is read once from the installer's own
    check (`then`); with none, this is the outcome. None to carry on, or an exit code."""
    key, noun = spec["key"], spec["noun"]
    if not spec.get("then"):
        rep.note(key, OUT_UPDATED if updated else OUT_OK)
        return None
    code, _out = io.run_seen(base + spec["then"] + ["--conf", conf])
    if io.interrupted:
        rep.note(key, OUT_UNMEASURED, "interrupted during its check")
        return stop(io, INTERRUPTED % (noun + "'s check"), code or EX_BLOCKED)
    if code == 0:
        rep.note(key, OUT_UPDATED if updated else OUT_OK)
        return None
    if code == EX_BLOCKED:
        io.say("")
        io.say("%s's settings are level; what is left is for you, named above." % noun)
        rep.note(key, OUT_WAITING, "updated; a card is still yours" if updated
                 else "a card is still yours", card_only=True)
        return None
    rep.note_code(key, code, "its check")
    return stop(io, "%s's check stopped with exit %d. The lines above say why." % (noun, code),
                code)


def rows_doc(text, schema):
    """The one JSON document an installer's `check` or `apply` printed on its standard output
    (KIT-850), or None. No document, anything around it, another schema or no rows is None,
    and None is never up to date."""
    try:
        doc = json.loads((text or "").strip())
    except ValueError:
        return None
    if (not isinstance(doc, dict) or doc.get("schema") != schema
            or not isinstance(doc.get("rows"), list)
            or not all(isinstance(r, dict) for r in doc["rows"])):
        return None
    return doc


def rows_with(doc, outcome):
    return [r for r in doc["rows"] if r.get("outcome") == outcome]


def cards_of(doc):
    """The cards its blocked rows name, one per line: what the short list reads for this part."""
    return "\n".join("%s %s" % (r.get("step"), r.get("card"))
                     for r in rows_with(doc, "BLOCKED-ON-HUMAN") if r.get("card"))


def step_job_auto(io, spec, base, conf, rep):
    """Automatic (KIT-850): the installer's own `check`, then its `apply` with the consent piped
    in, when a row is work. Neither asks anything: the Mac password is held here first, once for
    the run. A check or apply whose rows cannot be read is never up to date. None to carry on, or
    an exit code."""
    key, noun = spec["key"], spec["noun"]
    # A part that runs under your own login (the validator, KIT-547) needs no Mac password.
    if spec.get("sudo", True) and not io.sudo_once(
            "%s's check and apply read and change it as the role account and as root. They ask "
            "for nothing themselves." % noun):
        rep.note(key, OUT_WAITING, "it needs your Mac password")
        return stop(io, "%s needs your Mac password and did not get it. Nothing was changed. "
                        "Run this again and type it at the prompt." % noun, 5)
    seen = tuple(rep.checks.get(key) or (base + spec["auto_check"] + ["--conf", conf]))
    code, out = io.run_rows(base + spec["auto_check"] + ["--conf", conf])
    if io.interrupted:
        rep.note(key, OUT_UNMEASURED, "interrupted during its check")
        return stop(io, INTERRUPTED % (noun + "'s check"), code or EX_BLOCKED)
    doc = rows_doc(out, spec["rows_schema"])
    if doc is None:
        rep.note(key, OUT_UNMEASURED, "its check printed no rows (exit %s)" % code)
        return stop(io, "%s's check printed no rows this command could read (exit %s). The lines "
                        "above say why." % (noun, code), code if code not in (None, 0, EX_BLOCKED)
                    else EX_UNMEASURED)
    rep.seen[seen] = (code, cards_of(doc))
    if code == 0:
        io.say("")
        io.say("%s's settings are up to date. Nothing to change." % noun)
        rep.note(key, OUT_OK)
        return None
    if code != EX_BLOCKED:
        rep.note_code(key, code, "its check")
        return stop(io, "%s's check stopped with exit %d. The lines above say why." % (noun, code),
                    code)
    if not rows_with(doc, "WOULD-CHANGE"):
        io.say("")
        io.say("%s has nothing to change; what is left is for you, named above." % noun)
        rep.note(key, OUT_WAITING, "a card is still yours", card_only=True)
        return None
    io.say("")
    io.say("%s is not up to date: the rows above name what would change." % noun)
    io.say(spec["auto_note"])
    if not io.ask("Update %s now?" % noun.lower()):
        rep.note(key, OUT_WAITING, "behind; you did not update it")
        return None
    code, out = io.run_rows(base + spec["auto_apply"] + ["--conf", conf],
                            stdin_text=json.dumps(spec["answers"]))
    if io.interrupted:
        rep.note(key, OUT_UNMEASURED, "interrupted during its installer")
        return stop(io, INTERRUPTED % (noun + "'s installer"), code or EX_BLOCKED)
    doc = rows_doc(out, spec["rows_schema"])
    io.say("")
    if doc is None:
        rep.note(key, OUT_UNMEASURED, "its apply printed no rows (exit %s)" % code)
        return stop(io, "%s's apply printed no rows this command could read (exit %s). The lines "
                        "above say why." % (noun, code), code if code not in (None, 0, EX_BLOCKED)
                    else EX_UNMEASURED)
    rep.seen[seen] = (code, cards_of(doc))
    if code == 0:
        io.say("%s is up to date." % noun)
        rep.note(key, OUT_UPDATED)
        return None
    if code == EX_BLOCKED:
        # Above its ceiling the job is installed and left off: its one line is the end line's.
        changed = bool(rows_with(doc, "DONE"))
        detail = doc.get("left_off") or ("updated; a card is still yours" if changed
                                         else "a card is still yours")
        io.say("%s waits for you: %s. Carrying on; it is on the list at the end." % (noun, detail))
        rep.note(key, OUT_WAITING, detail)
        return None
    rep.note_code(key, code, "its apply")
    return stop(io, "%s's apply stopped with exit %d. The lines above say why; do what they say, "
                    "then run this command again." % (noun, code), code)


def step_job(io, spec, files, rep):
    """None to carry on, or an exit code. See JOB_STEPS."""
    key, noun = spec["key"], spec["noun"]
    heading(io, "%s. %s" % (spec["number"], spec["title"]))
    conf = files[spec["conf"]]
    if not io.exists(conf):
        rep.note(key, OUT_OFF, missing(conf))
        io.say("No %s, so %s is not set up on this Mac. Copy %s.example to switch it on."
               % (os.path.relpath(conf, KIT), noun.lower(), spec["conf"]))
        return None
    base = [sys.executable, spec["script"]]
    if io.auto and spec.get("auto_check"):
        return step_job_auto(io, spec, base, conf, rep)
    code, out = io.run_seen(base + spec["look"] + ["--conf", conf])
    if io.interrupted:
        rep.note(key, OUT_UNMEASURED, "interrupted during its check")
        return stop(io, INTERRUPTED % (noun + "'s check"), code or EX_BLOCKED)
    if code == 0:
        io.say("")
        io.say("%s's settings are up to date. Nothing to change." % noun)
        return job_done(io, spec, base, conf, rep, False)
    if code != EX_BLOCKED:
        rep.note_code(key, code, "its check")
        return stop(io, "%s's check stopped with exit %d. The lines above say why."
                    % (noun, code), code)
    if not (has_work(out, spec["work_line"]) if spec.get("work_line") else would_change(out)):
        io.say("")
        io.say("%s has nothing to change; what is left is for you, named above." % noun)
        rep.note(key, OUT_WAITING, "a card is still yours", card_only=True)
        return None
    io.say("")
    io.say("%s is not up to date: the check above names what would change." % noun)
    io.say(spec["note"])
    if not io.ask("Update %s now?" % noun.lower()):
        rep.note(key, OUT_WAITING, "behind; you did not update it")
        return None
    code, _out = io.run(base + spec["apply"] + ["--conf", conf])
    if io.interrupted:
        rep.note(key, OUT_UNMEASURED, "interrupted during its installer")
        return stop(io, INTERRUPTED % (noun + "'s installer"), code or EX_BLOCKED)
    io.say("")
    if code == 0:
        io.say("%s's settings are up to date." % noun)
        rep.asked.add(key)
        return job_done(io, spec, base, conf, rep, True)
    rep.note_code(key, code, "its installer")
    if code == EX_BLOCKED and io.auto:
        io.say("%s waits for you at the card above. Carrying on; come back to it." % noun)
        return None
    return stop(io, "%s's installer stopped with exit %d. The lines above say why; do what "
                    "they say, then run this command again." % (noun, code), code)


def watch_level(conf_path, home, uid, measure):
    """(state, why) of the hourly health check's job, read and never changed: "current" (its job
    file names this kit, this settings file and this interval, and launchd holds it), "missing"
    (no job file, or launchd does not hold it), "behind" (a job file for another kit, settings
    file or interval) or "unmeasured" (a file or a launchctl answer that could not be read)."""
    import plistlib
    try:
        import pipeline_update as pu
        conf = pu.load_conf(conf_path)
        label = conf["WATCH_LABEL"]
        want = [WATCH, "--conf", conf["__source__"], "run"]
        every = int(conf["WATCH_INTERVAL_SECONDS"])
    except Exception as exc:                                    # noqa: BLE001  (any failure is unmeasured)
        return "unmeasured", "its settings could not be read (%s)" % (
            str(exc).strip().splitlines() or [type(exc).__name__])[0][:100]
    plist = os.path.join(home, "Library", "LaunchAgents", label + ".plist")
    if not os.path.exists(plist):
        return "missing", "no job file for %s" % label
    try:
        with open(plist, "rb") as fh:
            doc = plistlib.load(fh)
        args, interval = list(doc.get("ProgramArguments") or []), doc.get("StartInterval")
    except Exception as exc:                                    # noqa: BLE001
        return "unmeasured", "its job file could not be read (%s)" % type(exc).__name__
    if args[1:] != want or interval != every:
        return "behind", "its job file names another kit, settings file or interval"
    code, out = measure(["launchctl", "print", "gui/%d/%s" % (uid, label)])
    if code is None:
        return "unmeasured", "launchctl did not answer (%s)" % (out or "no reason given")[:80]
    if code != 0:
        return "missing", "its job file is there, but launchd does not hold the job"
    return "current", ""


def step_watch(io, conf, rep):
    """The hourly health check's job, when `update.conf` exists. It is a LaunchAgent that runs as
    you; its own installer shows a dry pass and asks for a typed yes before it loads anything."""
    key = "watch"
    heading(io, "3g. The hourly health check")
    if not io.exists(conf):
        rep.note(key, OUT_OFF, missing(conf))
        io.say("No %s, so the health check is not set up on this Mac. Copy update.conf.example to "
               "switch it on." % os.path.relpath(conf, KIT))
        return None
    state, why = io.watch_level(conf)
    if state == "current":
        io.say("The hourly health check is installed and loaded, from this kit.")
        rep.note(key, OUT_OK)
        return None
    if state == "unmeasured":
        io.say("The hourly health check could not be looked at: %s." % why)
        rep.note(key, OUT_UNMEASURED, why)
        return None
    io.say("The hourly health check is %s: %s." % ("behind" if state == "behind" else "not running", why))
    io.say("Installing it writes one LaunchAgent in your own home and loads it. It runs as you and "
           "changes nothing it reports on. It shows a dry pass first and asks for a typed yes.")
    if not io.ask("Install the hourly health check now?"):
        rep.note(key, OUT_WAITING, "%s; you did not install it" % why)
        return None
    code, _out = io.run([sys.executable, WATCH, "--conf", conf, "install"])
    if io.interrupted:
        rep.note(key, OUT_UNMEASURED, "interrupted during its installer")
        return stop(io, INTERRUPTED % "The health check's installer", code or EX_BLOCKED)
    io.say("")
    if code == EX_BLOCKED:
        io.say("Nothing was installed: its own yes was not typed. Run this command again when you are ready.")
        rep.note(key, OUT_WAITING, "its own yes was not typed")
        return None
    if code != 0:
        rep.note_code(key, code, "its installer")
        return stop(io, "the health check's installer stopped with exit %d. The lines above say why."
                    % code, code)
    # Read it again: an installer that said done is not a job that was seen loaded.
    state, why = io.watch_level(conf)
    if state != "current":
        io.say("It said it was installed, but looking again: %s." % why)
        rep.note(key, OUT_UNMEASURED, "installed, but not seen loaded: %s" % why)
        return None
    io.say("The hourly health check is installed and loaded.")
    rep.note(key, OUT_UPDATED)
    return None


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
                  "pipeline_sweep_setup.py": "sweep", "pipeline_stage_a_setup.py": "stage_a",
                  "pipeline_bridge_setup.py": "bridge",
                  "pipeline_notifier_setup.py": "notifier",
                  "pipeline_queue_setup.py": "queue", "pipeline_watch.py": "watch",
                  "pipeline_validator_setup.py": "validator"}


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
    if base == "pipeline_bridge_setup.py" and ((part.get("apply") or {}).get("args") or [])[:1] != ["install"]:
        return None            # instant replies (`push`) keep their own check at the end
    return STEP_OF_SCRIPT.get(base)


# KIT-752: a settings file may live outside the kit folder. This Mac says where in ONE place,
# a file only root could have written, read like the consent record: a map of file name to the
# full path it is kept at. It is never read from the repository, and it holds paths, never
# values. The kit folder stays the default: a file found there wins.
DEFAULT_PLACES = "/Library/Application Support/claude-pipeline/settings-places.json"
PLACES = {}
PLACES_WHY = [""]          # why the places file, or part of it, was ignored; "" when it was not


def load_places(path, lstat=os.lstat, realpath=os.path.realpath, read=_read_small):
    """({file name: full path}, why). Empty, with the reason, when there is no such file, it is
    not one only root could have written, or it is not a plain map of names to absolute
    paths: the kit folder is then the only place, as before."""
    try:
        why = untrusted_why(path, lstat, realpath)
    except FileNotFoundError:
        return {}, ""
    except OSError as exc:
        return {}, "it could not be inspected (%s)" % exc
    if why:
        return {}, why
    try:
        data = json.loads(read(path))
    except (OSError, ValueError) as exc:
        return {}, "it could not be read (%s)" % exc
    if not isinstance(data, dict):
        return {}, "it is not a map of file names to paths"
    out, dropped = {}, []
    for name, where in data.items():
        if (isinstance(name, str) and isinstance(where, str) and name == os.path.basename(name)
                and name.endswith((".conf", ".json")) and os.path.isabs(where)
                and os.path.basename(where) == name):
            out[name] = where
        else:
            dropped.append(str(name)[:40])
    return out, ("ignored %d entr%s that are not a file name with its own full path (%s)"
                 % (len(dropped), "y" if len(dropped) == 1 else "ies", ", ".join(dropped))
                 if dropped else "")


def conf_path(name, files):
    if files.get(name):
        return files[name]
    here = os.path.join(KIT, name)
    if name in PLACES and not os.path.exists(here) and os.path.exists(PLACES[name]):
        return PLACES[name]
    return here


def check_argv(part, files):
    check = part["check"]
    args = [conf_path(a, files) if a.endswith(".conf") else a for a in check.get("args") or []]
    return [sys.executable, os.path.join(KIT, check["script"])] + args


def settings_path(part, files):
    """Where the part's settings file lives on this Mac, or None when it has no file a
    person switches it on with."""
    name = (part.get("settings") or {}).get("file") or ""
    return conf_path(name, files) if name.endswith((".conf", ".json")) else None


# KIT-756: does this Mac have a host? Read-only, no password, nothing changed. The lane marker
# folder (docs/HOST.md) is readable by everyone and holds one empty file named for the pipeline's
# account; the account must exist and have its home folder. Anything that cannot be told is
# "unknown": never "has one".
HOST_MARKERS = "/etc/claude-pipeline/dispatched-lane"
HOST_NONE = "no pipeline account was found on this Mac"
HOST_FIRST = ("Building the host takes hours, and the kit does not do it for you. Every other "
              "part needs it first; the steps are in docs/HOST.md.")


def host_state(listdir=os.listdir, getpwnam=None, isdir=os.path.isdir):
    """('yes' | 'none' | 'unknown', why)."""
    if getpwnam is None:
        try:
            import pwd
            getpwnam = pwd.getpwnam
        except ImportError:
            return "unknown", "this Mac's accounts could not be read"
    try:
        names = sorted(n for n in listdir(HOST_MARKERS) if n.startswith("_"))
    except (FileNotFoundError, NotADirectoryError):
        return "none", "%s: no lane marker folder at %s" % (HOST_NONE, HOST_MARKERS)
    except OSError as exc:
        return "unknown", "the lane marker folder could not be read (%s)" % exc
    for name in names:
        try:
            home = getpwnam(name).pw_dir
        except KeyError:
            continue
        except Exception as exc:                       # an unreadable account list is not "none"
            return "unknown", "the account %s could not be looked up (%s)" % (name, exc)
        if isdir(home):
            return "yes", "the pipeline's account %s and its home folder are here" % name
    return "none", "%s: no marker names an account that exists with a home folder" % HOST_NONE


def host_outcome(io):
    state, why = io.host_state()
    return {"yes": OUT_OK, "none": OUT_WAITING}.get(state, OUT_UNMEASURED), (
        why if state != "unknown" else "not measured: " + why)


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
    records, nocheck, texts, seen, group = [], [], {}, rep.seen, {}
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
        elif part["id"] == "host" and not io.interrupted:
            rec["outcome"], rec["detail"] = host_outcome(io)
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
    hosts = [r for r in records if r["id"] == "host" and r["outcome"] == OUT_WAITING]
    if hosts:
        # The host comes first, ahead of every other item (KIT-756), and is not named twice.
        lone = [n for n in lone if n != hosts[0]["name"]]
        io.say("Build this first. Nothing else here works without it (%s):" % hosts[0]["name"])
        for step in next(p for p in parts if p["id"] == "host").get("person_steps") or []:
            io.say("  - %s" % step["words"])
        io.say("  %s" % HOST_FIRST)
        io.say("")
    if not (mine or signs or lone or hosts):
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


# --------------------------------------------------------------------------- #
# --check: look at every part and change nothing (KIT-736)
# --------------------------------------------------------------------------- #
ONLY_LOOKED = ("This run only looked and changed nothing (--check). It asked no question and "
               "needed no terminal.")
BEHIND_DETAIL = "not at origin's tip; a real run would compare it and move it"
SKILLS_DIFFER = "differ from the kit's; a real run would ask to replace them"
COMMIT_ID = r"(?:[0-9a-f]{40}|[0-9a-f]{64})"


def git_read(repo, *args):
    """A read-only git command as the argv a look runs it with. It never goes through a shell."""
    return ["git", "-C", repo] + list(args)


def look_checkout(io, rep, repo=KIT):
    """The kit checkout's line, from two read-only git calls: this checkout's commit, and the
    tip of the branch origin calls its default. The same commit is up to date. Any other is
    waiting on you, and only that: a read of the tip cannot tell behind from ahead. Which branch
    this checkout is on is never asked, so a detached commit is measured like any other.
    Whatever cannot be read is could not be measured, and the detail says which. Git's own
    words are not repeated, because an error can carry the remote's address."""
    code, out = io.measure(git_read(repo, "rev-parse", "HEAD"))
    here = (out or "").strip()
    if code != 0 or not re.fullmatch(COMMIT_ID, here):
        rep.note("checkout", OUT_UNMEASURED, "git could not read this checkout's commit")
        return
    code, out = io.measure(git_read(repo, "ls-remote", "--symref", "origin", "HEAD"))
    if code != 0:
        why = ("git exited with %d" % code) if code is not None else (out or "no reason given")[:120]
        rep.note("checkout", OUT_UNMEASURED, "origin could not be reached (%s)" % why)
        return
    tips = re.findall(r"^(%s)[ \t]+HEAD[ \t]*$" % COMMIT_ID, out or "", re.M)
    if not tips:
        rep.note("checkout", OUT_UNMEASURED, "origin's answer could not be read")
    elif tips[0] == here:
        rep.note("checkout", OUT_OK, "at origin's tip")
    else:
        rep.note("checkout", OUT_WAITING, BEHIND_DETAIL)


def look_steps(io, parts, files, skip_stage_e, rep):
    """Give each step of a real run the result it would report, read-only: the kit checkout by
    git (look_checkout), every other step by its part's own check, run once. The settings file
    is looked at first: absent is "off on this Mac", and the check does not run. A step with
    no result reads "not reached" in build_records, so each one is given one. Each check's
    answer goes in rep.seen, so build_records does not run it again. A Ctrl-C stops the
    looking, and the steps it did not reach say so."""
    done = set()
    for part in parts:
        key = step_of(part)
        if not key or key in done or io.interrupted:
            continue
        done.add(key)
        if key == "checkout":
            look_checkout(io, rep)
            continue
        path = settings_path(part, files)
        if key == "stage_e" and skip_stage_e:
            rep.note(key, OUT_UNMEASURED, "skipped (--skip-stage-e), so not checked")
        elif path and not io.exists(path):
            rep.note(key, OUT_OFF, missing(path))
        elif key == "watch":
            # The step's own question is whether the job is installed, not what a pass finds.
            state, why = io.watch_level(path)
            rep.note(key, *{"current": (OUT_OK, ""), "unmeasured": (OUT_UNMEASURED, why)}.get(
                state, (OUT_WAITING, why)))
        else:
            argv = check_argv(part, files)
            answer = io.measure(argv)
            rep.seen[tuple(argv)] = answer
            code, out = answer
            if key == "skills" and code == 1:
                # The skills check exits 1 for "differ from the kit's", which the lines at the
                # end of a real run call waiting; read as outcome_of_check does, it would not be.
                rep.note(key, OUT_WAITING, SKILLS_DIFFER)
            else:
                rep.note(key, *outcome_of_check(code, out))


def look_only(io, args):
    """--check, run after the two refusals that apply to it (a Claude session, root). It needs
    no terminal and asks nothing: no first screen, no consent, no password, and the consent
    record is neither read nor written. It runs nothing but the checks and the two read-only
    git calls, all through io.measure, so none can ask a person or outlive its time limit.
    The first line says it only looked; the rest is finish(), the lines and the short list a
    real run ends with, and their exit code (0, 10, or 4 before 10)."""
    io.say(ONLY_LOOKED)
    files = {"stage-a.conf": os.path.abspath(args.conf),
             "stage-e.conf": os.path.abspath(args.stage_e_conf),
             "sweep.conf": os.path.abspath(args.sweep_conf),
             "bridge.conf": os.path.abspath(args.bridge_conf),
             "notifier.conf": os.path.abspath(args.notifier_conf),
             "queue.conf": os.path.abspath(args.queue_conf),
             "validator.conf": os.path.abspath(args.validator_conf),
             "update.conf": os.path.abspath(args.update_conf)}
    rep, parts, unreadable = Report(), [], None
    try:
        parts = io.parts()
        look_steps(io, parts, files, args.skip_stage_e, rep)
    except (OSError, ValueError, ImportError, KeyError, TypeError) as exc:
        parts, unreadable = [], "%s: %s" % (type(exc).__name__, exc)
    except KeyboardInterrupt:
        io.interrupted = True
    stopped = io.interrupted
    if stopped:
        io.say("Interrupted. Nothing was changed. The parts it did not reach say so below.")
    return finish(io, parts, rep, files, not stopped, EX_UNMEASURED if stopped else None,
                  stopped, unreadable)


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
    parser.add_argument("--notifier-conf", default=os.path.join(KIT, "notifier.conf"),
                        help="the notifier's settings (default: notifier.conf in the kit; "
                             "absent skips it)")
    parser.add_argument("--queue-conf", default=os.path.join(KIT, "queue.conf"),
                        help="the usage job and the queue's settings (default: queue.conf in the kit; "
                             "absent skips it)")
    parser.add_argument("--validator-conf", default=os.path.join(KIT, "validator.conf"),
                        help="the validator's job settings (default: validator.conf in the kit; "
                             "absent skips it)")
    parser.add_argument("--skip-stage-e", action="store_true",
                        help="do not check or update the review jobs")
    parser.add_argument("--check", action="store_true", help="only look: change nothing, ask nothing, no terminal needed")
    parser.add_argument("--pulled-from", default=None, help=argparse.SUPPRESS, type=commit_id)
    parser.add_argument("--chosen-mode", default=None, choices=sorted(MODE_WORDS),
                        help=argparse.SUPPRESS)
    parser.add_argument("--consent-record", default=DEFAULT_RECORD, help=argparse.SUPPRESS)
    parser.add_argument("--update-conf", default=os.path.join(KIT, "update.conf"),
                        help=argparse.SUPPRESS)
    parser.add_argument("--bridge-conf", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--watcher-conf", default=os.path.join(KIT, "watcher.conf"), help=argparse.SUPPRESS)
    parser.add_argument("--settings-places", default=DEFAULT_PLACES, help=argparse.SUPPRESS)
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
    import pipeline_root_guard as rg      # KIT-693: the one refusal every installer shares
    if rg.refusal(io.uid()):
        return stop(io, rg.BODY, EX_USAGE)
    # KIT-752: the one place that says where a settings file is kept, read only now, past the two
    # refusals. A file that was ignored says why on the lines of the parts it would have found.
    found_places, PLACES_WHY[0] = io.places(args.settings_places)
    PLACES.clear()
    PLACES.update(found_places)
    if args.bridge_conf is None:
        args.bridge_conf = conf_path("bridge.conf", {})
    if args.check:                   # KIT-736: it only looks, so it needs no terminal
        return look_only(io, args)
    if not io.is_tty():
        return stop(io, "this needs a person at a terminal: it asks before every change.",
                    EX_USAGE)
    io.say("A terminal, as yourself. Good.")

    # Past the three refusals, every exit ends with the lines. The parts list is read once,
    # up front; if it cannot be read there is no consent page to agree to, so nothing runs and
    # the end says so, in words.
    files = {"stage-a.conf": os.path.abspath(args.conf),
             "stage-e.conf": os.path.abspath(args.stage_e_conf),
             "sweep.conf": os.path.abspath(args.sweep_conf),
             "bridge.conf": os.path.abspath(args.bridge_conf),
             "notifier.conf": os.path.abspath(args.notifier_conf),
             "queue.conf": os.path.abspath(args.queue_conf),
             "validator.conf": os.path.abspath(args.validator_conf),
             "update.conf": os.path.abspath(args.update_conf)}
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
            writes += io.level_settings(os.path.abspath(args.update_conf), os.path.abspath(args.watcher_conf))
        except KeyboardInterrupt:
            code = stop(io, "interrupted while asking, so nothing was changed. Run it again when "
                            "you are ready.", EX_BLOCKED)
            return finish(io, parts, rep, files, False, code, True, unreadable)

    steps = (lambda: step_consent_and_mode(io, record_path, mode, state, rec, why, parts, files,
                                           args.skip_stage_e, unreadable, writes),
             lambda: step_checkout(io, argv, args.no_pull, rep, args.pulled_from, mode),
             lambda: step_skills(io, rep),
             lambda: step_stage_e(io, files["stage-e.conf"], args.skip_stage_e, rep),
             lambda: step_sweep(io, files["sweep.conf"], rep)) \
        + tuple((lambda spec=spec: step_job(io, spec, files, rep)) for spec in JOB_STEPS) \
        + (lambda: step_watch(io, files["update.conf"], rep),) \
        + (lambda: step_stage_a(io, files["stage-a.conf"], files["stage-e.conf"], rep),)
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
                 steps=None, typed=None, record=None, conf=None, host=None,
                 places=None, places_why=""):
        # The first screen and the consent read lines from `typed` (default: Manual, then
        # yes), so the cases written before them keep their meaning. `record` is what the
        # consent record reads as: (state, data, why). Everything the new seams do is logged.
        self.typed = ["2", "yes"] if typed is None else list(typed)
        self.line_log = []                 # (prompt, programs run so far, record reads so far)
        self.record = record or ("none", None, "no record yet")
        self.record_reads = []
        self.conf = dict(conf or {})
        self.conf_reads = []
        self.host = host or ("yes", "the pipeline's account _x and its home folder are here")
        self.placed = dict(places or {})
        self.places_why = places_why
        self.places_reads = []
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

    seen_out = {}                          # program key -> the lines run_seen returns for it
    # Program key -> what a `check` or `apply` printed. By default the notifier's check prints a
    # clean document, so the cases written before KIT-850 keep their meaning in Automatic.
    rows_out = {"pipeline_notifier_setup.py check": json.dumps(
        {"schema": [s for s in JOB_STEPS if s["key"] == "notifier"][0]["rows_schema"],
         "command": "check", "exit": 0, "rows": []})}
    piped = ()                             # (program key, the text piped into it), in order

    def run_seen(self, argv):
        """Scripted like `run`; the lines it returns are `seen_out[key]`."""
        code, _out = self.run(argv)
        return code, self.seen_out.get(self.ran[-1], "")

    def run_rows(self, argv, stdin_text=None):
        """Scripted like `run`; its standard output is `rows_out[key]`, and what was piped in is
        kept in `piped` (KIT-850)."""
        code, _out = self.run(argv)
        self.piped = tuple(self.piped) + ((self.ran[-1], stdin_text),)
        return code, self.rows_out.get(self.ran[-1], "")

    def run(self, argv, capture=False):
        k = self.key(argv)
        if argv[0] != "git":
            # A flag after the subcommand tells a look from the real thing: `run --dry-run`
            # from `run`, `install` from `install --apply`.
            for flag in ("--dry-run", "--apply"):
                if flag in argv[3:] and not k.endswith(flag):
                    k += " " + flag
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

    levels = ("current", "")                # what watch_level answers; a list is answered in turn
    level_asked = 0

    def watch_level(self, conf_path):
        self.level_asked += 1
        got = self.levels
        return got.pop(0) if isinstance(got, list) and len(got) > 1 else (got[0] if isinstance(got, list) else got)

    def host_state(self):
        return self.host
    def places(self, path):
        self.places_reads.append(path)
        return dict(self.placed), self.places_why

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

    heartbeat_changes = ()

    def level_settings(self, update_conf, watcher_conf):
        self.level_files = (update_conf, watcher_conf)
        return list(self.heartbeat_changes)

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


def selftest_look(check, base, se_conf, tmp):
    """Every case of the look-only mode, --check (KIT-736), and no other case. selftest() calls
    it with one line. `base` is its settings-file flags; `se_conf` exists and the other two do
    not; `tmp` is the run's own folder."""
    import builtins
    import contextlib
    import shutil
    import subprocess as sp

    files = {"stage-a.conf": base[1], "stage-e.conf": base[3], "sweep.conf": base[5]}
    kit = lambda name: os.path.join(KIT, name)                    # noqa: E731
    real = Io().parts()
    here, tip = "a" * 40, "b" * 40
    answer = lambda sha: "ref: refs/heads/main\tHEAD\n%s\tHEAD\n" % sha        # noqa: E731
    head_argv = ["git", "-C", KIT, "rev-parse", "HEAD"]
    tip_argv = ["git", "-C", KIT, "ls-remote", "--symref", "origin", "HEAD"]
    # A conf path may be any path, so a part's check is known by its argv with those blanked.
    norm = lambda argv: tuple("<conf>" if a.endswith(".conf") else a for a in argv)  # noqa: E731
    paging = "pipeline_notifier_setup.py check"

    class Untouchable(FakeIo):
        """A FakeIo on which everything that asks, writes, installs or restarts raises, and which
        measures only what a look may: each part's own check and the two read-only git calls."""

        def __init__(self, **kw):
            FakeIo.__init__(self, **kw)
            self.parts_reads = 0
            self.checks = set(norm(check_argv(p, files)) for p in real if p.get("check"))

        def parts(self):
            self.parts_reads += 1
            return FakeIo.parts(self)

        def measure(self, argv, timeout=CHECK_TIMEOUT):
            if list(argv) not in (head_argv, tip_argv) and norm(argv) not in self.checks:
                raise AssertionError("--check measured %r: neither a part's own check nor "
                                     "a read-only git call" % (argv,))
            return FakeIo.measure(self, argv, timeout)

    def refuses(name):
        def refused(self, *a, **k):
            raise AssertionError("--check called %s%r" % (name, a[:2]))
        return refused
    for name in ("ask", "prompt", "line", "sudo_once", "make_sudo", "read_record",
                 "write_record", "read_conf", "stage_e_steps", "restart", "run"):
        setattr(Untouchable, name, refuses(name))

    class Pressed(Untouchable):
        """Ctrl-C reaches the first check, and Io.measure notes it and returns."""
        def measure(self, argv, timeout=CHECK_TIMEOUT):
            Untouchable.measure(self, argv, timeout)
            self.interrupted = True
            return None, "interrupted"

    class Raised(Untouchable):
        """Ctrl-C reaches the look itself, as an exception."""
        def measure(self, argv, timeout=CHECK_TIMEOUT):
            raise KeyboardInterrupt

    @contextlib.contextmanager
    def no_writes():
        """While it lasts, a file opened for writing, a path made, moved or removed, and a
        process started outside the Io seam all raise."""
        real_open, saved = builtins.open, {}

        def guarded_open(file, mode="r", *a, **k):
            if set(str(mode)) & set("wax+"):
                raise AssertionError("--check opened %r for writing" % (file,))
            return real_open(file, mode, *a, **k)

        def blocked(label):
            def refused(*a, **k):
                raise AssertionError("--check called %s%r" % (label, a[:1]))
            return refused
        for mod, names in ((os, ("makedirs", "mkdir", "rename", "replace", "remove", "unlink",
                                 "rmdir", "system", "execv")),
                           (shutil, ("rmtree", "copy", "copyfile", "move")),
                           (sp, ("Popen", "run", "call", "check_call", "check_output"))):
            for name in names:
                saved[(mod, name)] = getattr(mod, name)
                setattr(mod, name, blocked("%s.%s" % (mod.__name__, name)))
        builtins.open = guarded_open
        try:
            yield
        finally:
            builtins.open = real_open
            for (mod, name), original in saved.items():
                setattr(mod, name, original)

    def look(argv=(), codes=None, out=None, present=(), cls=None, parts_list=None, **kw):
        """`--check` on an Untouchable, inside no_writes(): (exit code, io, why it raised)."""
        kw.setdefault("tty", False)                    # a look needs no terminal
        io = (cls or Untouchable)(**kw)
        io.measure_out = dict({"rev-parse HEAD": here + "\n", "ls-remote": answer(here)},
                              **(out or {}))
        io.measure_codes = dict(codes or {})
        io.present = set(present)
        io.parts_list = parts_list
        try:
            with no_writes():
                code = main(base + ["--check"] + list(argv), io=io)
        except Exception as exc:                       # a mutant's crash is a named failure too
            return None, io, (str(exc) if isinstance(exc, AssertionError)
                              else "%s: %s" % (type(exc).__name__, exc))
        except KeyboardInterrupt:
            if cls is not Raised:
                raise                                  # a person's own Ctrl-C is not swallowed
            return None, io, "Ctrl-C was not caught by the look"
        return code, io, None

    class Rows(dict):
        def __missing__(self, key):
            return ("no line for %s" % key, "")
    rec = lambda io: Rows((r["id"], (r["outcome"], r["detail"]))        # noqa: E731
                          for r in (io.records or []))
    text = lambda io: "\n".join(io.lines)                          # noqa: E731
    five = (OUT_OK, OUT_WAITING, OUT_OFF, OUT_UNMEASURED)

    # 1. THE REFUSALS COME FIRST, print only themselves, and nothing is read before them: not
    #    the parts list, not a check, not the record. A Claude session is 3; root is 2.
    def plain_run(extra=(), **kw):
        """The same flags with no --check, on a FakeIo that scripts no program: (code, io). A
        run that goes past its refusal and starts one is a code naming it, not a crash."""
        io = FakeIo(**kw)
        try:
            return main(base + list(extra), io=io), io
        except Exception as exc:
            return "went on: %s" % exc, io

    for label, kw, want in ([("marker:%s" % m, {"env": {m: ""}}, EX_REFUSED)
                             for m in AGENT_ENV_MARKERS] + [("root", {"uid": 0}, EX_USAGE)]):
        plain = plain_run(**kw)[1]                     # the same refusal with no --check
        code, io, err = look(**kw)
        check("kit736-refusal-first:%s" % label,
              (code, err, io.lines == plain.lines, ONLY_LOOKED in io.lines, io.parts_reads,
               io.measured, io.record_reads, io.records),
              (want, None, True, False, 0, [], [], None))

    # 2. NO TERMINAL IS NEEDED, and every other flow still needs one. And a look changes and
    #    asks nothing: on an Untouchable, a restart, a record, a question, a password, an
    #    installer, a pull, a written file or any other command raises, and `err` says which.
    code, io, err = look()
    check("kit736-changes-and-asks-nothing", err, None)
    check("kit736-no-terminal-needed", (code, err, io.is_tty(), io.records is not None),
          (EX_OK, None, False, True))
    for extra in ([], ["--skip-stage-e"], ["--no-pull"]):
        code, plain = plain_run(extra, tty=False)
        check("kit736-other-flows-still-need-a-terminal:%s" % (" ".join(extra) or "plain"),
              (code, plain.records, plain.ran, plain.measured), (EX_USAGE, None, [], []))

    # 3. A LEVEL MAC: exit 0; each part judged the way the lines at the end judge it; the
    #    only commands are each present part's own check (once) and the two git reads.
    code, io, err = look()
    got = rec(io)
    check("kit736-level-mac-exits-0-and-says-nothing-waits",
          (code, err, "Nothing is waiting on you." in io.lines), (EX_OK, None, True))
    check("kit736-level-mac-judges-each-part",
          [got[i] for i in ("one-command", "skills", "review", "findings", "review-identity",
                            "idea-gate", "cleanup", "paging")],
          [(OUT_OK, "at origin's tip"), (OUT_OK, ""), (OUT_OK, ""), (OUT_OK, ""), (OUT_OK, ""),
           (OUT_OFF, "no stage-a.conf in %s" % tmp), (OUT_OFF, "no sweep.conf in %s" % tmp),
           (OUT_OFF, "no notifier.conf in the kit folder")])
    check("kit736-runs-only-the-checks-and-the-git-reads-each-once",
          (sorted(io.measured), io.ran),
          (sorted(["sync_user_skills.py", "pipeline_stage_e_setup.py verify",
                   "pipeline_stage_e_app.py verify", "rev-parse HEAD", "ls-remote"]), []))
    check("kit736-asks-and-writes-nothing",
          (io.asked, io.line_log, io.events, io.written, io.record_reads, io.conf_reads,
           io.restarted, io.auto, io.auto_answered), ([], [], [], [], [], [], None, False, ()))
    check("kit736-the-first-line-says-it-only-looked",
          (io.lines[:3], "only looked" in ONLY_LOOKED, "changed nothing" in ONLY_LOOKED),
          (["", "== 0. Where this is running", ONLY_LOOKED], True, True))
    closing = [ln for ln in io.lines if ln.startswith("No check of their own: ")]
    bare = [p["name"] for p in real if not step_of(p) and not p.get("check") and p["id"] != "host"]
    line_of = lambda r: "  %-22s %s%s" % (r["outcome"], r["name"],                 # noqa: E731
                                          " (%s)" % r["detail"] if r["detail"] else "")
    check("kit736-one-line-per-part-in-the-parts-list",
          (sum(1 for r in (io.records or []) if io.lines.count(line_of(r)) == 1),
           len(io.records or []) + len(bare), closing),
          (len(io.records or []), len(real), ["No check of their own: %s." % "; ".join(bare)]))
    check("kit736-every-line-carries-one-of-the-four-words-and-none-is-not-reached",
          bool(io.records) and all(r["outcome"] in five for r in io.records), True)

    # 4. THE CHECKOUT LINE. Only the commit is compared: the same is up to date, any other is
    #    waiting on you (never "behind"), and what cannot be read says which it was.
    check("kit736-the-waiting-detail-is-the-tickets-words", BEHIND_DETAIL,
          "not at origin's tip; a real run would compare it and move it")
    one = lambda io: rec(io)["one-command"]                       # noqa: E731
    unreachable = lambda why: (OUT_UNMEASURED, "origin could not be reached (%s)" % why)  # noqa: E731
    unread = (OUT_UNMEASURED, "origin's answer could not be read")
    no_commit = (OUT_UNMEASURED, "git could not read this checkout's commit")
    for name, out, codes, want in (
            ("same", {}, {}, (OUT_OK, "at origin's tip")),
            ("different", {"ls-remote": answer(tip)}, {}, (OUT_WAITING, BEHIND_DETAIL)),
            ("noise-around-the-answer", {"ls-remote": "warning: x\n" + answer(tip) + "\nmore\n"},
             {}, (OUT_WAITING, BEHIND_DETAIL)),
            ("origin-refused", {"ls-remote": "fatal: unable to access GITS-OWN-WORDS"},
             {"ls-remote": 128}, unreachable("git exited with 128")),
            ("origin-did-not-answer", {"ls-remote": "no answer in 120 seconds"},
             {"ls-remote": None}, unreachable("no answer in 120 seconds")),
            ("answer-is-not-a-tip", {"ls-remote": "hello\n"}, {}, unread),
            ("answer-is-only-the-symref", {"ls-remote": "ref: refs/heads/main\tHEAD\n"}, {}, unread),
            ("answer-is-a-short-id", {"ls-remote": answer("abc123")}, {}, unread),
            ("answer-names-another-ref", {"ls-remote": "%s\trefs/heads/main\n" % tip}, {}, unread),
            ("answer-is-empty", {"ls-remote": ""}, {}, unread),
            ("this-checkout-cannot-be-read", {"rev-parse HEAD": "fatal: not a git repository"},
             {"rev-parse HEAD": 128}, no_commit),
            ("this-checkout-says-no-commit", {"rev-parse HEAD": "HEAD\n"}, {}, no_commit)):
        code, io, err = look(out=out, codes=codes)
        check("kit736-checkout-%s" % name, (err, one(io)), (None, want))
        if name == "origin-refused":
            check("kit736-checkout-never-repeats-gits-own-words", "GITS-OWN-WORDS" in text(io), False)
        if name == "this-checkout-cannot-be-read":
            check("kit736-checkout-unreadable-asks-origin-nothing", "ls-remote" in io.measured, False)
    # A detached commit or another branch is not a failure to measure, and is never mentioned.
    for branch in ("HEAD", "feat/x"):
        for sha, want in ((here, (OUT_OK, "at origin's tip")), (tip, (OUT_WAITING, BEHIND_DETAIL))):
            code, io, err = look(out={"rev-parse --abbrev-ref": branch, "ls-remote": answer(sha)})
            check("kit736-checkout-on-%s-with-tip-%s" % (branch, sha[0]),
                  (err, one(io), [k for k in io.measured if "abbrev" in k or "symbolic" in k],
                   any(w in one(io)[1] for w in ("detached", "branch", "behind", "ahead", "feat"))),
                  (None, want, [], False))

    # 5. EXIT CODES. 0 level; 10 waiting; 4 could not be measured; 4 before 10.
    waits = {paging: 10}
    cases = (("0-level", {}, {}, (), EX_OK),
             ("10-not-at-origins-tip", {"ls-remote": answer(tip)}, {}, (), EX_BLOCKED),
             ("10-skills-differ", {}, {"sync_user_skills.py": 1}, (), EX_BLOCKED),
             ("10-a-part-check-waits", {}, waits, (kit("notifier.conf"),), EX_BLOCKED),
             ("10-the-review-check-waits", {}, {"pipeline_stage_e_setup.py verify": 10}, (),
              EX_BLOCKED),
             ("4-origin-cannot-be-reached", {}, {"ls-remote": 128}, (), EX_UNMEASURED),
             ("4-a-check-stopped", {}, {paging: 1}, (kit("notifier.conf"),), EX_UNMEASURED),
             ("4-a-check-did-not-finish", {}, {paging: None}, (kit("notifier.conf"),),
              EX_UNMEASURED),
             ("4-a-check-needs-the-password", {}, {paging: 5}, (kit("notifier.conf"),),
              EX_UNMEASURED),
             ("4-a-step-check-stopped", {}, {"pipeline_stage_e_setup.py verify": 4}, (),
              EX_UNMEASURED),
             ("4-outranks-10-a-waiting-checkout", {"ls-remote": answer(tip)}, {paging: 1},
              (kit("notifier.conf"),), EX_UNMEASURED),
             ("4-outranks-10-a-waiting-part", {}, {"ls-remote": 128, paging: 10},
              (kit("notifier.conf"),), EX_UNMEASURED))
    for name, out, codes, present, want in cases:
        code, io, err = look(out=out, codes=codes, present=present)
        check("kit736-exit-%s" % name, (code, err), (want, None))
    code, io, err = look(codes={"sync_user_skills.py": 1})
    check("kit736-skills-differ-is-waiting-not-unmeasured",
          (rec(io)["skills"], any(r["outcome"] == OUT_UNMEASURED for r in (io.records or []))),
          ((OUT_WAITING, SKILLS_DIFFER), False))
    code, io, err = look(codes={"sync_user_skills.py": 2})
    check("kit736-another-skills-code-is-unmeasured",
          (rec(io)["skills"][0], code), (OUT_UNMEASURED, EX_UNMEASURED))

    # 6. THE SHORT LIST comes from the same reader as a real run's, and each check runs once.
    code, io, err = look(codes=waits, out={paging: "CK-N1 is yours\nthen CK-N3 and CK-N10"},
                         present=[kit("notifier.conf")])
    at = lambda s: io.lines.index(s) if s in io.lines else len(io.lines)       # noqa: E731
    only, signs = at("Only you can do these:"), at("Sign-offs still asked for:")
    first = lambda lines: [(ln.split() or [""])[0] for ln in lines]            # noqa: E731
    check("kit736-short-list-splits-by-kind-and-matches-whole-card-ids",
          (first(io.lines[only + 1:signs]), first(io.lines[signs + 1:signs + 2]), code,
           io.measured.count(paging)),
          (["CK-N1"], ["CK-N3"], EX_BLOCKED, 1))
    code, io, err = look(codes={"pipeline_stage_e_setup.py verify": 10},
                         out={"pipeline_stage_e_setup.py verify": "CK-11 waits"})
    check("kit736-a-waiting-step-part-takes-its-cards-from-its-check-run-once",
          (any(ln.startswith("  CK-11") for ln in io.lines), rec(io)["catch-up"][0],
           io.measured.count("pipeline_stage_e_setup.py verify"), code),
          (True, OUT_WAITING, 1, EX_BLOCKED))
    code, io, err = look(codes={"sync_user_skills.py": 1})
    check("kit736-a-waiting-part-with-no-card-is-named",
          (any(ln.endswith("is waiting on you; its own lines above say what for.") for ln in io.lines),
           io.measured.count("sync_user_skills.py")), (True, 1))

    # 7. THE FLAGS. The hidden four do nothing; --skip-stage-e and the settings flags work.
    level = look(out={"ls-remote": answer(tip)})
    for flags in (["--no-pull"], ["--pulled-from", "abcdef1234"], ["--chosen-mode", "manual"],
                  ["--chosen-mode", "automatic"], ["--consent-record", "/x/y/z.json"],
                  ["--no-pull", "--pulled-from", "abcdef1234", "--chosen-mode", "manual",
                   "--consent-record", "/x/y/z.json"]):
        got = look(flags, out={"ls-remote": answer(tip)})
        check("kit736-flags-do-nothing:%s" % " ".join(flags),
              (got[0], got[1].lines, got[1].records, got[2], got[1].record_reads),
              (level[0], level[1].lines, level[1].records, None, []))
    code, io, err = look(["--skip-stage-e"])
    check("kit736-skip-stage-e-is-unmeasured-and-not-checked",
          ([rec(io)[i] for i in ("review", "findings", "catch-up", "status", "job-health")],
           code, "pipeline_stage_e_setup.py verify" in io.measured),
          ([(OUT_UNMEASURED, "skipped (--skip-stage-e), so not checked")] * 5, EX_UNMEASURED, False))
    absent = os.path.join(tmp, "elsewhere", "stage-e.conf")
    code, io, err = look(["--stage-e-conf", absent])
    check("kit736-a-stage-e-conf-that-is-not-there-reads-off",
          (rec(io)["review"], rec(io)["review-identity"], "pipeline_stage_e_setup.py verify" in io.measured, code),
          ((OUT_OFF, "no stage-e.conf in %s" % os.path.dirname(absent)),
           (OUT_OFF, "no stage-e.conf in %s" % os.path.dirname(absent)), False, EX_OK))
    alt_a, alt_s = os.path.join(tmp, "alt-stage-a.conf"), os.path.join(tmp, "alt-sweep.conf")
    for path in (alt_a, alt_s):
        with open(path, "w") as fh:
            fh.write("X=1\n")
    code, io, err = look(["--conf", alt_a, "--sweep-conf", alt_s],
                         codes={"pipeline_stage_a_setup.py run": 10, "pipeline_sweep_setup.py run": 0})
    check("kit736-the-idea-gate-and-sweep-conf-flags-are-honoured",
          (rec(io)["idea-gate"], rec(io)["cleanup"], code),
          ((OUT_WAITING, "its check found something outstanding"), (OUT_OK, ""), EX_BLOCKED))
    os.unlink(alt_a)
    os.unlink(alt_s)

    # 8. CTRL-C stops the looking, prints the lines, and is never a pass.
    for label, cls, skills_line in (("noted-in-a-check", Pressed, OUT_UNMEASURED),
                                    ("raised", Raised, OUT_NOT_REACHED)):
        code, io, err = look(cls=cls)
        check("kit736-ctrl-c-%s" % label,
              (code, err, len(io.measured) <= 1, rec(io)["skills"][0], rec(io)["one-command"][0],
               rec(io)["review"][0], any(ln.startswith("Interrupted.") for ln in io.lines),
               "Where every part stands" in text(io)),
              (EX_UNMEASURED, None, True, skills_line, OUT_NOT_REACHED, OUT_NOT_REACHED, True, True))

    # 9. THE PARTS LIST cannot be read: said in words, exit 4, and nothing is looked at.
    for label, broken in (("cannot-be-opened", OSError("no list")),
                          ("holds-a-check-with-no-script", [{"id": "bad", "name": "Bad",
                                                            "check": {"args": []}}])):
        code, io, err = look(parts_list=broken)
        check("kit736-an-unreadable-parts-list-is-said-and-exits-4:%s" % label,
              (code, err, io.records, io.measured, any("could not be read" in ln for ln in io.lines)),
              (EX_UNMEASURED, None, [], [], True))

    # 10. THE TWO GIT READS, against real git and a repository made here.
    check("kit736-git-is-there", shutil.which("git") is not None, True)
    home = os.path.join(tmp, "look-git-home")
    os.makedirs(home)
    genv = dict(os.environ, HOME=home, XDG_CONFIG_HOME=home, GIT_CONFIG_NOSYSTEM="1",
                GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)

    def git(cwd, *args):
        sp.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
               + list(args), cwd=cwd, env=genv, check=True, stdout=sp.PIPE, stderr=sp.PIPE)

    def commit(repo, name):
        with open(os.path.join(repo, name), "w") as fh:
            fh.write(name)
        git(repo, "add", name)
        git(repo, "commit", "-m", name)

    root = os.path.join(tmp, "look-git")
    origin, work, other = (os.path.join(root, d) for d in ("origin.git", "work", "other"))
    os.makedirs(root)
    git(root, "init", "--bare", origin)
    git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    git(root, "init", work)
    commit(work, "one")
    git(work, "remote", "add", "origin", origin)
    git(work, "push", "origin", "HEAD:refs/heads/main")

    def seen_by_git(repo=work):
        rep = Report()
        look_checkout(Io(), rep, repo)                 # the real Io.measure: no terminal, a limit
        return rep.steps["checkout"]
    check("kit736-real-git-same-commit", seen_by_git(), (OUT_OK, "at origin's tip"))
    pushed = sp.run(["git", "-C", work, "rev-parse", "HEAD"], stdout=sp.PIPE, env=genv,
                    universal_newlines=True).stdout.strip()
    git(work, "checkout", "--detach", pushed)
    check("kit736-real-git-detached-at-the-tip-is-measured", seen_by_git(), (OUT_OK, "at origin's tip"))
    commit(work, "two")
    check("kit736-real-git-a-commit-origin-lacks", seen_by_git(), (OUT_WAITING, BEHIND_DETAIL))
    git(work, "checkout", "-b", "feat/x")
    check("kit736-real-git-another-branch-is-measured", seen_by_git(), (OUT_WAITING, BEHIND_DETAIL))
    git(root, "clone", origin, other)
    commit(other, "three")
    git(other, "push", "origin", "HEAD:refs/heads/main")
    git(work, "checkout", "--detach", pushed)
    check("kit736-real-git-origin-moved-on", seen_by_git(), (OUT_WAITING, BEHIND_DETAIL))
    git(work, "remote", "set-url", "origin", os.path.join(root, "gone.git"))
    check("kit736-real-git-origin-gone", seen_by_git(), unreachable("git exited with 128"))
    git(work, "remote", "remove", "origin")
    check("kit736-real-git-no-origin", seen_by_git(), unreachable("git exited with 128"))
    check("kit736-real-git-not-a-checkout",
          seen_by_git(os.path.join(root, "nothing-here")), no_commit)

    # 11. THE FLAG IS DOCUMENTED where a person looks: the help, and the docstring.
    helped = sp.run([sys.executable, os.path.abspath(__file__), "--help"], stdout=sp.PIPE,
                    stderr=sp.PIPE, universal_newlines=True).stdout
    check("kit736-the-flag-is-in-the-help-and-the-docstring",
          ("--check" in helped, "pipeline_install.py --check" in (__doc__ or ""),
           "TO ONLY LOOK" in (__doc__ or "")), (True, True, True))


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
    notifier_conf = os.path.join(tmp, "notifier.conf")           # absent: the notifier is off
    base = ["--conf", a_conf, "--stage-e-conf", se_conf, "--sweep-conf", sweep_conf]
    healthy = {"sync_user_skills.py": 0, "pipeline_stage_e_setup.py verify": 0,
               "pipeline_stage_a_setup.py run": 0,
               "pipeline_bridge_setup.py install": 0,         # asked only where bridge.conf is present
               "pipeline_bridge_setup.py verify": 0,
               "pipeline_notifier_setup.py run --dry-run": 0,      # asked only where notifier.conf is present
               "pipeline_notifier_setup.py check": 0,              # …in Automatic (KIT-850)
               "pipeline_queue_setup.py run --dry-run": 0}        # asked only where queue.conf is present

    def go(codes=None, answers=(), argv=None, **kw):
        io = FakeIo(codes=dict(healthy, **(codes or {})), answers=answers, **kw)
        code = main(base + list(argv or []), io=io)
        return code, io

    # 1. A CLAUDE SESSION IS REFUSED BEFORE ANYTHING RUNS. Every marker, alone.
    for marker in AGENT_ENV_MARKERS:
        code, io = go(env={marker: ""})
        check("refused-under:%s" % marker, (code, io.ran), (EX_REFUSED, []))
    code, io = go(uid=0)
    check("refused-as-root", (code, io.ran, __import__("pipeline_root_guard").BODY in "\n".join(io.lines)),
          (EX_USAGE, [], True))
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
    check("stage-e-warns-the-notifier-and-tech-lead-stop-for-the-move",
          any("notifier" in line and "tech lead" in line and "few seconds" in line
              and "bridge" not in line for line in io.lines), True)
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
    check("stage-e-run-that-stopped-part-way-names-the-notifier-and-tech-lead",
          any("notifier or tech lead job it names as off" in line for line in io.lines), True)
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

    # 6c. THE NOTIFIER (KIT-583): its own dry run first, a question only when there is work, its `run` on a yes,
    #     and an outcome by name. An unmeasured step is never up to date.
    import contextlib
    import io as stdio
    import pipeline_notifier_setup as notifier_setup
    look, apply_ = "pipeline_notifier_setup.py run --dry-run", "pipeline_notifier_setup.py run"

    def table(*rows):
        """A dry run's step table, printed by the notifier installer's own function."""
        buf = stdio.StringIO()
        with contextlib.redirect_stdout(buf):
            notifier_setup._print_rows(list(rows))
        return buf.getvalue()

    work = table(("job", notifier_setup.WOULD_CHANGE, "the plist is not installed"),
                 ("enable", notifier_setup.BLOCKED, "CK-N3: you load it"))
    cards = table(("job", notifier_setup.ALREADY_DONE, "installed"),
                  ("enable", notifier_setup.BLOCKED, "CK-N3: you load it"))
    check("notifier-pin-would-change-word", (notifier_setup.WOULD_CHANGE, would_change(work),
                                             would_change(cards), would_change("")),
          ("WOULD-CHANGE", True, False, True))

    def notifier(codes=None, seen=None, answers=(), interrupt=(), **kw):
        io = FakeIo(codes=dict(healthy, **(codes or {})), answers=answers, **kw)
        io.seen_out = dict(seen or {})
        io.interrupt_on = set(interrupt or ())
        return main(base + ["--notifier-conf", notifier_conf], io=io), io

    def outcome(io, part="paging"):
        return [(r["outcome"], r["detail"]) for r in io.records if r["id"] == part]

    code, io = notifier()
    check("notifier-no-conf-is-off-and-not-run",
          (code, [k for k in io.ran if "notifier" in k], outcome(io)),
          (EX_OK, [], [(OUT_OFF, "no notifier.conf in %s" % tmp)]))
    with open(notifier_conf, "w") as fh:
        fh.write("DM_USER_IDS=U1\n")
    code, io = notifier({look: 0})
    check("notifier-current-is-up-to-date-and-asks-nothing",
          (code, io.ran.count(apply_), io.asked, outcome(io)), (EX_OK, 0, [], [(OUT_OK, "")]))
    code, io = notifier({look: 10, apply_: 0}, {look: work}, answers=[True])
    check("notifier-work-asked-then-run-then-updated",
          (code, [k for k in io.ran if "notifier" in k or "stage_a" in k], len(io.asked), outcome(io)),
          (EX_OK, [look, apply_, "pipeline_stage_a_setup.py run"], 1, [(OUT_UPDATED, "")]))
    code, io = notifier({look: 10}, {look: work}, answers=[False])
    check("notifier-declined-is-waiting-and-changes-nothing",
          (code, io.ran.count(apply_), "pipeline_stage_a_setup.py run" in io.ran, outcome(io)),
          (EX_BLOCKED, 0, True, [(OUT_WAITING, "behind; you did not update it")]))
    code, io = notifier({look: 10}, {look: cards})
    check("notifier-cards-only-asks-nothing-runs-nothing-is-waiting",
          (code, io.asked, io.ran.count(apply_), "pipeline_stage_a_setup.py run" in io.ran, outcome(io)),
          (EX_BLOCKED, [], 0, True, [(OUT_WAITING, "a card is still yours")]))
    code, io = notifier({look: 10, apply_: 0}, {look: "no table here"}, answers=[True])
    check("notifier-a-dry-run-with-no-table-is-asked-about-never-current",
          (code, len(io.asked), outcome(io)), (EX_OK, 1, [(OUT_UPDATED, "")]))
    code, io = notifier({look: 10, apply_: 10}, {look: work}, answers=[True])
    check("notifier-a-run-ending-on-a-card-stops-in-manual",
          (code, "pipeline_stage_a_setup.py run" in io.ran, [o for o, _d in outcome(io)]),
          (EX_BLOCKED, False, [OUT_WAITING]))
    # KIT-850: in Automatic, the installer's own `check`, then its `apply` with the consent piped
    # in. Its documents are built by the installer's own function, so a change of shape there
    # turns these red.
    chk, app = "pipeline_notifier_setup.py check", "pipeline_notifier_setup.py apply"
    W, B, D, A = (notifier_setup.WOULD_CHANGE, notifier_setup.BLOCKED, notifier_setup.DONE,
                  notifier_setup.ALREADY_DONE)

    class Said(object):
        """What a pass of the installer knew, for its own rows_document."""
        def __init__(self, cards=None, first=None, left=None):
            self.row_cards, self.ceiling = dict(cards or {}), 5
            self.first_pass, self.left_off = first, left

    def doc(command, code_, rows_, **kw):
        return json.dumps(notifier_setup.rows_document(command, code_, rows_, Said(**kw)))

    left_line = "left off: its first pass would send 7 messages, above its ceiling of 5"
    check_work = doc("check", 10, [("job", W, "would install"), ("enable", W, "would switch it on"),
                                   ("first-ping", B, "")], cards={"first-ping": "CK-N4"})
    check_cards = doc("check", 10, [("job", A, "installed"), ("first-ping", B, "")],
                      cards={"first-ping": "CK-N4"})
    apply_on = doc("apply", 10, [("job", D, "installed"), ("enable", D, "switched it on"),
                                 ("first-ping", B, "")], cards={"first-ping": "CK-N4"}, first=(2, 0))
    apply_off = doc("apply", 10, [("job", D, "installed"), ("enable", B, left_line)],
                    cards={"enable": "CK-N3"}, first=(7, 0), left=left_line)
    apply_done = doc("apply", 0, [("job", D, "installed"), ("enable", D, "switched it on")])

    def auto(codes=None, rows_out=None, cls=FakeIo, interrupt=()):
        io = cls(codes=dict(healthy, **(codes or {})), typed=["1", "yes"])
        io.rows_out = dict(rows_out or {})
        io.interrupt_on = set(interrupt)
        return main(base + ["--notifier-conf", notifier_conf], io=io), io

    spec = [s for s in JOB_STEPS if s["key"] == "notifier"][0]
    check("notifier-auto-answers-and-rows-are-the-installers-own-schemas",
          (spec["answers"]["schema"], spec["rows_schema"], sorted(spec["answers"])),
          (notifier_setup.ANSWERS_SCHEMA, notifier_setup.ROWS_SCHEMA, ["consent", "schema"]))
    code, io = auto({chk: 10, app: 10}, {chk: check_work, app: apply_on})
    check("notifier-auto-work-runs-apply-with-only-the-consent-piped-in-and-never-run",
          ([k for k in io.ran if "notifier" in k], list(io.piped), outcome(io),
           "pipeline_stage_a_setup.py run" in io.ran, "sudo" in io.events),
          ([chk, app], [(chk, None), (app, json.dumps(spec["answers"]))],
           [(OUT_WAITING, "updated; a card is still yours")], True, True))
    signs_at = io.lines.index("Sign-offs still asked for:") if "Sign-offs still asked for:" in io.lines else -1
    check("notifier-auto-the-card-its-apply-stopped-on-is-on-the-short-list-with-no-asked-note",
          (signs_at > 0 and io.lines[signs_at + 1].split()[0] == "CK-N4",
           ASKED_DETAIL in outcome(io)[0][1]), (True, False))
    code, io = auto({chk: 10, app: 10}, {chk: check_work, app: apply_off})
    check("notifier-auto-above-the-ceiling-is-left-off-and-its-one-line-is-the-end-line",
          (outcome(io), "pipeline_stage_a_setup.py run" in io.ran, code,
           any(left_line in ln for ln in io.lines if "Messages you" in ln)),
          ([(OUT_WAITING, left_line)], True, EX_BLOCKED, True))
    code, io = auto({chk: 10, app: 0}, {chk: check_work, app: apply_done})
    check("notifier-auto-apply-that-holds-every-step-is-updated",
          (outcome(io), io.ran.count(app)), ([(OUT_UPDATED, "")], 1))
    code, io = auto({chk: 0}, {chk: doc("check", 0, [("job", A, "installed")])})
    check("notifier-auto-clean-check-is-up-to-date-and-applies-nothing",
          (outcome(io), io.ran.count(app)), ([(OUT_OK, "")], 0))
    code, io = auto({chk: 10}, {chk: check_cards})
    check("notifier-auto-cards-only-applies-nothing-and-is-waiting",
          (outcome(io), io.ran.count(app), "pipeline_stage_a_setup.py run" in io.ran),
          ([(OUT_WAITING, "a card is still yours")], 0, True))
    for name, codes, rows_out, want in (
            ("a-check-with-no-rows-at-exit-0", {chk: 0}, {chk: "all fine, trust me"}, EX_UNMEASURED),
            ("a-check-of-another-schema", {chk: 0}, {chk: json.dumps({"schema": "x/1", "rows": []})},
             EX_UNMEASURED),
            ("an-apply-with-no-rows-at-exit-0", {chk: 10, app: 0}, {chk: check_work, app: ""},
             EX_UNMEASURED),
            ("a-check-that-could-not-measure", {chk: 4},
             {chk: doc("check", 4, [("enable", "UNKNOWN", "NOT MEASURED")])}, 4),
            ("an-apply-that-was-refused", {chk: 10, app: 3}, {chk: check_work,
                                                              app: doc("apply", 3, [])}, 3)):
        code, io = auto(codes, rows_out)
        check("notifier-auto-%s-is-never-up-to-date-and-stops" % name,
              (code, [o for o, _d in outcome(io)], "pipeline_stage_a_setup.py run" in io.ran),
              (want, [OUT_UNMEASURED], False))

    class SecondSudoRefused(FakeIo):
        """The consent's record gets the password; the notifier's step does not."""
        def sudo_once(self, why):
            self.events.append("sudo")
            return self.events.count("sudo") < 2

    code, io = auto({chk: 10}, {chk: check_work}, cls=SecondSudoRefused)
    check("notifier-auto-without-the-mac-password-runs-nothing-and-is-waiting",
          (code, [k for k in io.ran if "notifier" in k], outcome(io)),
          (5, [], [(OUT_WAITING, "it needs your Mac password")]))
    code, io = auto({chk: 10, app: 10}, {chk: check_work, app: apply_on}, interrupt={chk})
    check("notifier-auto-ctrl-c-in-its-check-stops-before-any-apply",
          (code, io.ran.count(app), "pipeline_stage_a_setup.py run" in io.ran),
          (EX_BLOCKED, 0, False))
    code, io = notifier({look: 10, apply_: 10}, {look: work}, answers=[True])
    check("notifier-manual-still-runs-the-persons-own-dry-run-and-run-never-check-or-apply",
          ([k for k in io.ran if "notifier" in k], list(io.piped)), ([look, apply_], []))
    for rc in (1, 2, 3, 4, 5, 77):
        code, io = notifier({look: rc})
        check("notifier-check-exit-%d-stops-never-current" % rc,
              (code, "pipeline_stage_a_setup.py run" in io.ran, outcome(io)[0][0] != OUT_OK),
              (rc, False, True))
    for rc in (1, 4):
        code, io = notifier({look: 10, apply_: rc}, {look: work}, answers=[True])
        check("notifier-installer-exit-%d-stops-unmeasured" % rc,
              (code, "pipeline_stage_a_setup.py run" in io.ran, [o for o, _d in outcome(io)]),
              (rc, False, [OUT_UNMEASURED]))
    code, io = notifier({look: 0}, interrupt={look})
    check("notifier-ctrl-c-in-its-check-stops-the-run",
          (code, "pipeline_stage_a_setup.py run" in io.ran), (EX_BLOCKED, False))

    # 6d. THE VALIDATOR (KIT-547): optional, under your own login, so no Mac password is held for it.
    #     Manual: its own dry run, then its `run` on a yes. Automatic: its `check`, then its `apply`
    #     with only the consent piped in. Its documents are built by the installer's own function.
    import pipeline_validator_setup as validator_setup
    validator_conf = os.path.join(tmp, "validator.conf")              # absent: the validator is off
    vlook, vrun_ = "pipeline_validator_setup.py run --dry-run", "pipeline_validator_setup.py run"
    vchk, vapp = "pipeline_validator_setup.py check", "pipeline_validator_setup.py apply"
    vspec = [s for s in JOB_STEPS if s["key"] == "validator"][0]
    VW, VB, VD, VA = (validator_setup.WOULD_CHANGE, validator_setup.BLOCKED, validator_setup.DONE,
                      validator_setup.ALREADY_DONE)

    class OnePassword(FakeIo):
        """The consent's record gets the Mac password; any later ask is refused."""
        def sudo_once(self, why):
            self.events.append("sudo")
            return self.events.count("sudo") < 2

    def validator(codes=None, seen=None, rows_out=None, typed=None, answers=(), cls=OnePassword):
        io = cls(codes=dict(healthy, **(codes or {})), answers=answers, typed=typed)
        io.seen_out = dict(seen or {})
        io.rows_out = dict(io.rows_out, **(rows_out or {}))
        return main(base + ["--validator-conf", validator_conf], io=io), io

    def vdoc(command, code_, rows_, left=None):
        doc = validator_setup.rows_document(command, code_, rows_)
        doc["left_off"] = left
        return json.dumps(doc)

    def vout(io):
        return outcome(io, "validator")

    check("validator-spec-uses-the-installers-own-schemas-and-holds-no-mac-password",
          (vspec["rows_schema"], vspec["answers"]["schema"], sorted(vspec["answers"]), vspec["sudo"],
           vspec["number"], STEP_OF_SCRIPT[os.path.basename(vspec["script"])]),
          (validator_setup.ROWS_SCHEMA, validator_setup.ANSWERS_SCHEMA, ["consent", "schema"], False,
           "3f", "validator"))
    code, io = validator()
    check("validator-no-conf-is-off-and-not-run",
          (code, [k for k in io.ran if "validator" in k], vout(io)),
          (EX_OK, [], [(OUT_OFF, "no validator.conf in %s" % tmp)]))
    with open(validator_conf, "w") as fh:
        fh.write("HOLD_FILE=/w/hold.json\n")
    vwork = vdoc("check", 10, [("settings", VW, "would write it"), ("enable", VW, "would switch it on")])
    code, io = validator({vchk: 10, vapp: 0}, rows_out={
        vchk: vwork, vapp: vdoc("apply", 0, [("settings", VD, "wrote it"), ("enable", VD, "on")])},
        typed=["1", "yes"])
    check("validator-auto-check-then-apply-with-only-the-consent-and-no-mac-password",
          ([k for k in io.ran if "validator" in k], [x for x in io.piped if "validator" in x[0]],
           vout(io), io.events.count("sudo"), "pipeline_stage_a_setup.py run" in io.ran),
          ([vchk, vapp], [(vchk, None), (vapp, json.dumps(vspec["answers"]))], [(OUT_UPDATED, "")],
           1, True))
    left = "left off: its first pass would decide 2 things, above its ceiling of 0"
    code, io = validator({vchk: 10, vapp: 10}, rows_out={
        vchk: vwork, vapp: vdoc("apply", 10, [("settings", VD, "wrote it"), ("enable", VB, left)], left)},
        typed=["1", "yes"])
    check("validator-auto-above-its-ceiling-is-left-off-and-its-one-line-is-the-end-line",
          (vout(io), "pipeline_stage_a_setup.py run" in io.ran), ([(OUT_WAITING, left)], True))
    code, io = validator({vchk: 0}, rows_out={vchk: vdoc("check", 0, [("settings", VA, "kept")])},
                         typed=["1", "yes"])
    check("validator-auto-clean-check-is-up-to-date-and-applies-nothing",
          (vout(io), io.ran.count(vapp)), ([(OUT_OK, "")], 0))
    code, io = validator({vchk: 4}, rows_out={vchk: vdoc("check", 4, [("env-file", "UNKNOWN", "x")])},
                         typed=["1", "yes"])
    check("validator-auto-a-check-that-could-not-measure-is-never-up-to-date-and-stops",
          (code, [o for o, _d in vout(io)], "pipeline_stage_a_setup.py run" in io.ran),
          (4, [OUT_UNMEASURED], False))
    buf = stdio.StringIO()
    with contextlib.redirect_stdout(buf):
        validator_setup.se._print_rows([("settings", VW, "would write it"), ("enable", VW, "would")])
    vtable = buf.getvalue()
    code, io = validator({vlook: 10, vrun_: 0}, seen={vlook: vtable}, answers=[True])
    check("validator-manual-its-own-dry-run-then-its-run-on-a-yes-never-check-or-apply",
          ([k for k in io.ran if "validator" in k], [x for x in io.piped if "validator" in x[0]],
           vout(io)), ([vlook, vrun_], [], [(OUT_UPDATED, "")]))
    os.unlink(validator_conf)

    # 6e. THE USAGE JOB AND THE QUEUE (KIT-585): the installer's dry run first, a question only when a step would
    #     change something, its `run` on a yes. Unmeasured is never up to date.
    import inspect
    import pipeline_queue_setup as queue_setup
    queue_conf = os.path.join(tmp, "queue.conf")                  # absent: the queue is off
    look, apply_ = "pipeline_queue_setup.py run --dry-run", "pipeline_queue_setup.py run"

    def table(*rows):
        """The queue installer's step table, in the shape its run_steps prints (pinned below)."""
        return "\n-- steps --\n" + "".join("  %-10s %-17s %s\n" % row for row in rows)

    work = table(("configs", queue_setup.WOULD_CHANGE, "not written"),
                 ("enable", queue_setup.BLOCKED, "CK-Q2: you load them"))
    cards = table(("configs", queue_setup.ALREADY_DONE, "written"),
                  ("enable", queue_setup.BLOCKED, "CK-Q2: you load them"))
    check("queue-pin-would-change-word", (queue_setup.WOULD_CHANGE, would_change(work), would_change(cards)),
          ("WOULD-CHANGE", True, False))
    # The real installer's own table code, not a copy: its summary is built from rows of this shape.
    src = inspect.getsource(queue_setup.run_steps)
    check("queue-installer-prints-the-table-this-reads",
          ('say("\\n-- steps --")' in src, '"  %-10s %-17s %s"' in src), (True, True))

    def queue(codes=None, seen=None, answers=(), interrupt=(), **kw):
        io = FakeIo(codes=dict(healthy, **(codes or {})), answers=answers, **kw)
        io.seen_out = dict(seen or {})
        io.interrupt_on = set(interrupt or ())
        return main(base + ["--queue-conf", queue_conf], io=io), io

    def qout(io):
        return [(r["outcome"], r["detail"]) for r in io.records if r["id"] == "work-queue"]

    code, io = queue()
    check("queue-no-conf-is-off-and-not-run",
          (code, [k for k in io.ran if "queue" in k], qout(io)),
          (EX_OK, [], [(OUT_OFF, "no queue.conf in %s" % tmp)]))
    with open(queue_conf, "w") as fh:
        fh.write("ROLE_ACCOUNT=_x\n")
    code, io = queue()
    check("queue-current-is-up-to-date-and-asks-nothing",
          (code, io.ran.count(apply_), io.asked, qout(io)), (EX_OK, 0, [], [(OUT_OK, "")]))
    code, io = queue({look: 10, apply_: 0}, {look: work}, answers=[True])
    check("queue-work-asked-then-run-then-updated",
          (code, [k for k in io.ran if "queue" in k or "stage_a" in k], len(io.asked), qout(io)),
          (EX_OK, [look, apply_, "pipeline_stage_a_setup.py run"], 1, [(OUT_UPDATED, "")]))
    code, io = queue({look: 10}, {look: work}, answers=[False])
    check("queue-declined-is-waiting-and-changes-nothing",
          (code, io.ran.count(apply_), qout(io)),
          (EX_BLOCKED, 0, [(OUT_WAITING, "behind; you did not update it")]))
    code, io = queue({look: 10}, {look: cards})
    check("queue-cards-only-asks-nothing-is-waiting",
          (code, io.asked, io.ran.count(apply_), "pipeline_stage_a_setup.py run" in io.ran, qout(io)),
          (EX_BLOCKED, [], 0, True, [(OUT_WAITING, "a card is still yours")]))
    code, io = queue({look: 10, apply_: 0}, {look: "no table"}, answers=[True])
    check("queue-a-dry-run-with-no-table-is-asked-about-never-current",
          (code, len(io.asked), qout(io)), (EX_OK, 1, [(OUT_UPDATED, "")]))
    code, io = queue({look: 10, apply_: 10}, {look: work}, answers=[True])
    check("queue-a-run-ending-on-a-card-stops-in-manual",
          (code, "pipeline_stage_a_setup.py run" in io.ran, [o for o, _d in qout(io)]),
          (EX_BLOCKED, False, [OUT_WAITING]))
    code, io = queue({look: 10, apply_: 10}, {look: work}, answers=[True], typed=["1", "yes"])
    check("queue-a-run-ending-on-a-card-is-carried-past-in-automatic",
          (code, "pipeline_stage_a_setup.py run" in io.ran), (EX_BLOCKED, True))
    for rc in (1, 2, 3, 4, 5, 77):
        code, io = queue({look: rc})
        check("queue-check-exit-%d-stops-never-current" % rc,
              (code, "pipeline_stage_a_setup.py run" in io.ran, qout(io)[0][0] != OUT_OK), (rc, False, True))
    for rc in (1, 4):
        code, io = queue({look: 10, apply_: rc}, {look: work}, answers=[True])
        check("queue-installer-exit-%d-stops-unmeasured" % rc,
              (code, [o for o, _d in qout(io)]), (rc, [OUT_UNMEASURED]))
    code, io = queue({}, interrupt={look})
    check("queue-ctrl-c-stops-the-run", (code, "pipeline_stage_a_setup.py run" in io.ran), (EX_BLOCKED, False))
    os.unlink(queue_conf)

    # 6f. THE HOURLY HEALTH CHECK (KIT-585): read whether its job is level, ask, install, look again.
    update_conf = os.path.join(tmp, "update.conf")                # absent: the health check is off
    watch_run = "pipeline_watch.py --conf"

    def watch(levels=("current", ""), codes=None, answers=(), interrupt=(), **kw):
        io = FakeIo(codes=dict(healthy, **(codes or {})), answers=answers, **kw)
        io.levels = levels
        io.interrupt_on = set(interrupt or ())
        return main(base + ["--update-conf", update_conf], io=io), io

    def wout(io):
        return [(r["outcome"], r["detail"]) for r in io.records if r["id"] == "health-check"]

    code, io = watch()
    check("watch-no-conf-is-off-and-never-looked-at",
          (code, io.level_asked, wout(io)), (EX_OK, 0, [(OUT_OFF, "no update.conf in %s" % tmp)]))
    with open(update_conf, "w") as fh:
        fh.write("DISPATCHER_VERSION=1\n")
    code, io = watch()
    check("watch-level-is-up-to-date-and-asks-nothing",
          (code, io.asked, watch_run in io.ran, wout(io)), (EX_OK, [], False, [(OUT_OK, "")]))
    # --check judges the job the same way and runs nothing: one case per level.
    for level_name, answer, want in (("current", ("current", ""), (OUT_OK, "")),
                                     ("missing", ("missing", "no job file for x"), (OUT_WAITING, "no job file for x")),
                                     ("behind", ("behind", "another kit"), (OUT_WAITING, "another kit")),
                                     ("unmeasured", ("unmeasured", "launchctl did not answer"),
                                      (OUT_UNMEASURED, "launchctl did not answer"))):
        io = FakeIo(codes=dict(healthy))
        io.levels = answer
        main(["--check"] + base + ["--update-conf", update_conf], io=io)
        check("watch-check-mode-%s" % level_name, (wout(io), watch_run in io.ran), ([want], False))
    code, io = watch([("missing", "no job file for x"), ("current", "")], {watch_run: 0}, answers=[True])
    check("watch-missing-asked-installed-looked-at-again-updated",
          (code, len(io.asked), io.ran.count(watch_run), io.level_asked, wout(io)),
          (EX_OK, 1, 1, 2, [(OUT_UPDATED, "")]))
    check("watch-installer-argv-is-the-parts-list-apply",
          [a for a in io.argvs if a[1].endswith("pipeline_watch.py")][0][2:],
          ["--conf", update_conf, "install"])
    code, io = watch([("behind", "another kit"), ("current", "")], {watch_run: 0}, answers=[True])
    check("watch-behind-is-asked-and-installed", (code, wout(io)), (EX_OK, [(OUT_UPDATED, "")]))
    code, io = watch(("missing", "no job file for x"), answers=[False])
    check("watch-declined-is-waiting-and-installs-nothing",
          (code, watch_run in io.ran, wout(io)),
          (EX_BLOCKED, False, [(OUT_WAITING, "no job file for x; you did not install it")]))
    code, io = watch(("unmeasured", "launchctl did not answer"))
    check("watch-unread-is-never-up-to-date-and-asks-nothing",
          (code, io.asked, wout(io)), (EX_UNMEASURED, [], [(OUT_UNMEASURED, "launchctl did not answer")]))
    code, io = watch([("missing", "x"), ("missing", "its job file is there, but launchd does not hold the job")],
                     {watch_run: 0}, answers=[True])
    check("watch-installed-but-not-seen-loaded-is-not-updated",
          (code, [o for o, _d in wout(io)]), (EX_UNMEASURED, [OUT_UNMEASURED]))
    code, io = watch(("missing", "x"), {watch_run: 10}, answers=[True])
    check("watch-its-own-yes-not-typed-is-waiting-and-carries-on",
          (code, "pipeline_stage_a_setup.py run" in io.ran, [o for o, _d in wout(io)]),
          (EX_BLOCKED, True, [OUT_WAITING]))
    for rc in (1, 3, 4):
        code, io = watch(("missing", "x"), {watch_run: rc}, answers=[True])
        check("watch-installer-exit-%d-stops" % rc,
              (code, "pipeline_stage_a_setup.py run" in io.ran), (rc, False))
    code, io = watch(("missing", "x"), {watch_run: 0}, answers=[True], interrupt={watch_run})
    check("watch-ctrl-c-in-its-installer-stops-the-run",
          (code, "pipeline_stage_a_setup.py run" in io.ran), (EX_BLOCKED, False))

    # watch_level itself, against a made-up home and a made-up launchctl: it reads and changes nothing.
    import plistlib
    import pipeline_update as update_module
    home = os.path.join(tmp, "home")
    agents = os.path.join(home, "Library", "LaunchAgents")
    os.makedirs(agents)
    with open(os.path.join(KIT, "update.conf.example")) as fh:
        example = fh.read()
    with open(update_conf, "w") as fh:         # the example, with its three placeholder values edited
        fh.write(example.replace("com.example.dispatcher", "com.test.dispatcher")
                 .replace("_exdispatch", "_testdispatch")
                 .replace("/opt/example-dispatcher/.env", "/opt/test-dispatcher/.env"))
    conf =update_module.load_conf(update_conf)
    job_file = os.path.join(agents, conf["WATCH_LABEL"] + ".plist")
    asked = []

    def launchctl(code):
        def measure(argv):
            asked.append(argv)
            return code, ""
        return measure

    level = lambda code=0: watch_level(update_conf, home, 501, launchctl(code))       # noqa: E731
    check("watch-level-no-job-file-is-missing", level()[0], "missing")
    good = {"Label": conf["WATCH_LABEL"], "StartInterval": int(conf["WATCH_INTERVAL_SECONDS"]),
            "ProgramArguments": [sys.executable, WATCH, "--conf", conf["__source__"], "run"]}
    with open(job_file, "wb") as fh:
        plistlib.dump(good, fh)
    check("watch-level-matching-file-and-loaded-is-current", (level()[0], asked[-1][:2]),
          ("current", ["launchctl", "print"]))
    check("watch-level-matching-file-not-loaded-is-missing", level(113)[0], "missing")
    check("watch-level-launchctl-that-did-not-answer-is-unmeasured",
          watch_level(update_conf, home, 501, lambda argv: (None, "timed out"))[0], "unmeasured")
    for what, doc in (("another-interval", dict(good, StartInterval=1)),
                      ("another-kit", dict(good, ProgramArguments=[sys.executable, "/old/pipeline_watch.py",
                                                                  "--conf", conf["__source__"], "run"])),
                      ("another-conf", dict(good, ProgramArguments=[sys.executable, WATCH, "--conf",
                                                                   "/elsewhere/update.conf", "run"]))):
        with open(job_file, "wb") as fh:
            plistlib.dump(doc, fh)
        before = len(asked)
        check("watch-level-%s-is-behind-and-launchd-not-asked" % what, (level()[0], len(asked)),
              ("behind", before))
    with open(job_file, "wb") as fh:
        fh.write(b"not a plist")
    check("watch-level-an-unreadable-job-file-is-unmeasured", level()[0], "unmeasured")
    check("watch-level-unreadable-settings-are-unmeasured",
          watch_level(os.path.join(tmp, "nope.conf"), home, 501, launchctl(0))[0], "unmeasured")

    # A line an installer prints arrives while it is still running, not when it ends (a buffered child would
    # show sudo's bare prompt first). The child waits for the line to reach this program's own output.
    marker = os.path.join(tmp, "line-arrived")
    child_code = ("import os, time\nprint('early')\nfor _ in range(100):\n"
                  "    if os.path.exists(%r):\n        break\n    time.sleep(0.1)\n"
                  "print('late' if os.path.exists(%r) else 'never')\n" % (marker, marker))

    class Watching(object):
        def write(self, text):
            if "early" in text:
                open(marker, "w").close()
        def flush(self):
            pass
    real_out, sys.stdout = sys.stdout, Watching()
    try:
        got = Io().run_seen([sys.executable, "-c", child_code])
    finally:
        sys.stdout = real_out
    check("run-seen-passes-a-line-on-while-the-child-is-still-running", got, (0, "early\nlate\n"))
    os.unlink(notifier_conf)
    os.unlink(update_conf)

    # 6d. THE BRIDGE AND THE STAND-UP (KIT-584): `install` compares, a question only when it would write, `install
    #     --apply` on a yes, then `verify` once for what a person must do. Unmeasured is never up to date.
    import inspect
    import pipeline_bridge_setup as bridge_setup
    bridge_conf = os.path.join(tmp, "bridge.conf")                # absent: the bridge is off
    look, apply_, verify = ("pipeline_bridge_setup.py install", "pipeline_bridge_setup.py install --apply",
                            "pipeline_bridge_setup.py verify")
    spec = [sp for sp in JOB_STEPS if sp["key"] == "bridge"][0]
    parts_by_id = dict((pt["id"], pt) for pt in Io().parts())
    check("bridge-step-uses-the-parts-list-commands",
          (spec["apply"], parts_by_id["tech-lead"]["apply"]["args"][:2], spec["then"],
           parts_by_id["tech-lead"]["check"]["args"][:1], step_of(parts_by_id["tech-lead"]),
           step_of(parts_by_id["instant-replies"])),
          (["install", "--apply"], ["install", "--apply"], ["verify"], ["verify"], "bridge", None))
    install_source = inspect.getsource(bridge_setup.cmd_install)
    check("bridge-install-writes-the-standup-file-so-the-step-covers-it",
          ("standup_needs_writing(conf, facts)" in install_source, "write_standup(conf, runner)" in install_source),
          (True, True))

    check("bridge-pin-the-work-line-is-the-installers-own",
          spec["work_line"] in install_source, True)
    would = "To write: config.\n" + spec["work_line"] + ". Run it again with --apply.\n"
    blocked = "BLOCKED: the env file holds no tracker key under X. Run `token` first.\n"

    def bridge(codes=None, answers=(), interrupt=(), seen=None, **kw):
        io = FakeIo(codes=dict(healthy, **(codes or {})), answers=answers, **kw)
        io.seen_out = {look: would} if seen is None else dict(seen)
        io.interrupt_on = set(interrupt or ())
        return main(base + ["--bridge-conf", bridge_conf], io=io), io

    def bout(io):
        return [(r["outcome"], r["detail"]) for r in io.records if r["id"] == "tech-lead"]

    code, io = bridge()
    check("bridge-no-conf-is-off-and-not-run",
          (code, [k for k in io.ran if "bridge" in k], bout(io)),
          (EX_OK, [], [(OUT_OFF, "no bridge.conf in %s" % tmp)]))
    with open(bridge_conf, "w") as fh:
        fh.write("APP_NAME=x\n")
    code, io = bridge()
    check("bridge-level-and-verified-is-up-to-date-and-asks-nothing",
          (code, io.ran.count(apply_), io.asked, [k for k in io.ran if "bridge" in k], bout(io)),
          (EX_OK, 0, [], [look, verify], [(OUT_OK, "")]))
    code, io = bridge({look: 10, apply_: 0}, answers=[True])
    check("bridge-would-write-asked-then-applied-then-verified-updated",
          (code, [k for k in io.ran if "bridge" in k or "stage_a" in k], len(io.asked), bout(io)),
          (EX_OK, [look, apply_, verify, "pipeline_stage_a_setup.py run"], 1, [(OUT_UPDATED, "")]))
    code, io = bridge({look: 10}, seen={look: blocked})
    check("bridge-a-blocked-compare-asks-nothing-is-waiting-and-carries-on",
          (code, io.asked, apply_ in io.ran, "pipeline_stage_a_setup.py run" in io.ran, bout(io)),
          (EX_BLOCKED, [], False, True, [(OUT_WAITING, "a card is still yours")]))
    code, io = bridge({look: 10}, seen={look: ""})
    check("bridge-a-compare-that-printed-nothing-is-never-up-to-date-and-asks-nothing",
          (code, io.asked, bout(io)), (EX_BLOCKED, [], [(OUT_WAITING, "a card is still yours")]))
    code, io = bridge({look: 10}, answers=[False])
    check("bridge-declined-is-waiting-and-writes-nothing",
          (code, io.ran.count(apply_), "pipeline_stage_a_setup.py run" in io.ran, bout(io)),
          (EX_BLOCKED, 0, True, [(OUT_WAITING, "behind; you did not update it")]))
    code, io = bridge({verify: 10})
    check("bridge-level-but-a-card-is-left-is-waiting-and-carries-on",
          (code, io.asked, "pipeline_stage_a_setup.py run" in io.ran, bout(io)),
          (EX_BLOCKED, [], True, [(OUT_WAITING, "a card is still yours")]))
    code, io = bridge({look: 10, apply_: 0, verify: 10}, answers=[True])
    check("bridge-updated-with-a-card-left-says-both",
          (code, bout(io)), (EX_BLOCKED, [(OUT_WAITING, "updated; a card is still yours")]))
    for rc in (1, 2, 3, 4, 5, 77):
        code, io = bridge({look: rc})
        check("bridge-compare-exit-%d-stops-never-current" % rc,
              (code, "pipeline_stage_a_setup.py run" in io.ran, verify in io.ran, bout(io)[0][0] != OUT_OK),
              (rc, False, False, True))
    for rc in (1, 4, 77):
        code, io = bridge({verify: rc})
        check("bridge-verify-exit-%d-stops-unmeasured" % rc,
              (code, "pipeline_stage_a_setup.py run" in io.ran, [o for o, _d in bout(io)]),
              (rc, False, [OUT_UNMEASURED]))
    code, io = bridge({look: 10, apply_: 10}, answers=[True])
    check("bridge-apply-ending-on-a-blocked-card-stops-in-manual-and-never-verifies",
          (code, "pipeline_stage_a_setup.py run" in io.ran, verify in io.ran, [o for o, _d in bout(io)]),
          (EX_BLOCKED, False, False, [OUT_WAITING]))
    code, io = bridge({look: 10, apply_: 10}, answers=[True], typed=["1", "yes"])
    check("bridge-apply-ending-on-a-blocked-card-is-carried-past-in-automatic",
          (code, "pipeline_stage_a_setup.py run" in io.ran, [o for o, _d in bout(io)]),
          (EX_BLOCKED, True, [OUT_WAITING]))
    for rc in (1, 4):
        code, io = bridge({look: 10, apply_: rc}, answers=[True])
        check("bridge-apply-exit-%d-stops-unmeasured" % rc,
              (code, "pipeline_stage_a_setup.py run" in io.ran, [o for o, _d in bout(io)]),
              (rc, False, [OUT_UNMEASURED]))
    code, io = bridge({}, interrupt={look})
    check("bridge-ctrl-c-in-its-compare-stops-the-run",
          (code, "pipeline_stage_a_setup.py run" in io.ran), (EX_BLOCKED, False))
    code, io = bridge({}, interrupt={verify})
    check("bridge-ctrl-c-in-its-verify-stops-the-run",
          (code, "pipeline_stage_a_setup.py run" in io.ran), (EX_BLOCKED, False))
    os.unlink(bridge_conf)

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
          "pipeline_notifier_setup.py check" in io.measured, False)

    # 9c. KIT-756: the host. Read-only; a Mac with none puts building it first on the closing
    #     list; one that has it says nothing; one that cannot be told is never "has one".
    check("kit756-a-mac-with-a-host-reads-up-to-date-and-is-not-on-the-list",
          (by_id(go()[1])["host"], any("Build this first" in ln for ln in go()[1].lines)),
          (OUT_OK, False))
    HOST_NAME = [p["name"] for p in Io().parts() if p["id"] == "host"][0]
    code, io = go(host=("none", HOST_NONE + ": test"))
    lines = io.lines
    at = [i for i, ln in enumerate(lines) if ln.startswith("Build this first")]
    check("kit756-no-host-reads-waiting-and-exits-10",
          (by_id(io)["host"], code), (OUT_WAITING, EX_BLOCKED))
    check("kit756-no-host-is-the-first-item-with-its-steps-and-the-guide",
          (len(at), lines[at[0] + 1].startswith("  - Build the host"),
           any(ln == "  " + HOST_FIRST for ln in lines), "docs/HOST.md" in HOST_FIRST),
          (1, True, True, True))
    others = [i for i, ln in enumerate(lines) if ln.startswith(("Only you can do these:",
                                                                 "Sign-offs still asked for:"))
              or ln.endswith(" is waiting on you; its own lines above say what for.")]
    check("kit756-the-host-comes-before-every-other-list-item",
          all(at[0] < i for i in others), True)
    check("kit756-the-host-is-not-named-a-second-time",
          sum(1 for ln in lines if ln.startswith(HOST_NAME) and ln.endswith("is waiting on you; its own lines above say what for.")), 0)
    code, io = go(host=("unknown", "the lane marker folder could not be read (x)"))
    check("kit756-an-unreadable-host-is-not-measured-never-has-one",
          (by_id(io)["host"], code, [r["detail"] for r in io.records if r["id"] == "host"][0][:13]),
          (OUT_UNMEASURED, EX_UNMEASURED, "not measured:"))
    code, io = go(host=("none", HOST_NONE))
    check("kit756-the-host-is-no-longer-on-the-no-check-line",
          any("The always-on machine" in ln and ln.startswith("No check of their own") for ln in io.lines), False)
    Pw = type("Pw", (), {"pw_dir": "/srv/acct"})
    check("kit756-host-state-no-folder-is-none",
          host_state(listdir=lambda p: (_ for _ in ()).throw(FileNotFoundError()))[0], "none")
    check("kit756-host-state-unreadable-is-unknown",
          host_state(listdir=lambda p: (_ for _ in ()).throw(PermissionError()))[0], "unknown")
    check("kit756-host-state-marker-without-account-is-none",
          host_state(listdir=lambda p: ["_a"], getpwnam=lambda n: (_ for _ in ()).throw(KeyError(n)))[0], "none")
    check("kit756-host-state-account-without-home-is-none",
          host_state(listdir=lambda p: ["_a"], getpwnam=lambda n: Pw, isdir=lambda d: False)[0], "none")
    check("kit756-host-state-account-with-home-is-yes",
          host_state(listdir=lambda p: ["junk", "_a"], getpwnam=lambda n: Pw, isdir=lambda d: True)[0], "yes")
    # 9b. KIT-752: a settings file kept outside the kit folder, found from the one place on the
    #     machine that says so. The kit folder stays the default; a part with no file anywhere
    #     still reads off, and says every place it looked.
    ops = os.path.join(tmp, "ops")
    os.makedirs(ops)
    placed = os.path.join(ops, "bridge.conf")
    open(placed, "w").close()
    bridge_id = "tech-lead"
    code, io = go(argv=["--settings-places", "/x/places.json"], places={"bridge.conf": placed})
    check("kit752-the-one-place-is-read", io.places_reads, ["/x/places.json"])
    code, io2 = go()
    check("kit752-a-plain-run-reads-the-default-place", io2.places_reads, [DEFAULT_PLACES])
    code, io2 = go(env={AGENT_ENV_MARKERS[0]: ""})
    check("kit752-nothing-is-read-before-the-refusals", io2.places_reads, [])
    code, io2 = go(uid=0)
    check("kit752-nothing-is-read-as-root", io2.places_reads, [])
    code, io2 = go(argv=["--settings-places", "/x/places.json"], places_why="it is a link")
    got = dict((r["id"], r) for r in io2.records)
    check("kit752-an-ignored-places-file-says-why-on-the-line",
          ("the places file at" in got["tech-lead"]["detail"], "it is a link" in got["tech-lead"]["detail"]),
          (True, True))
    got = dict((r["id"], r) for r in io.records)
    check("kit752-a-placed-file-is-not-off",
          (got[bridge_id]["outcome"] != OUT_OFF, "pipeline_bridge_setup.py verify" in io.measured),
          (True, True))
    code, io = go(argv=["--settings-places", "/x/places.json"],
                  places={"bridge.conf": os.path.join(ops, "nowhere", "bridge.conf")})
    got = dict((r["id"], r) for r in io.records)
    check("kit752-a-placed-file-that-is-absent-reads-off-naming-both-places",
          (got[bridge_id]["outcome"], "the kit folder (or at " in got[bridge_id]["detail"],
           "nowhere" in got[bridge_id]["detail"]), (OUT_OFF, True, True))
    code, io = go()
    got = dict((r["id"], r) for r in io.records)
    check("kit752-no-place-no-file-reads-off-as-before",
          (got[bridge_id]["outcome"], got[bridge_id]["detail"]),
          (OUT_OFF, "no bridge.conf in the kit folder"))
    PLACES.clear()
    from collections import namedtuple
    PSt = namedtuple("PSt", "st_uid st_mode")
    _Root = PSt(0, stat.S_IFREG | 0o644)
    _User = PSt(501, stat.S_IFREG | 0o644)
    _Dir = PSt(0, stat.S_IFDIR | 0o755)
    fs = lambda who: (lambda q: _Dir if q == "/" else who)          # noqa: E731
    for name, where in (("nested", {"a/b.conf": "/x/b.conf"}), ("relative", {"b.conf": "b.conf"}),
                        ("other-name", {"b.conf": "/x/c.conf"}), ("not-a-conf", {"b.txt": "/x/b.txt"}),
                        ("not-a-string", {"b.conf": 5})):
        check("kit752-places-refuses-%s" % name,
              load_places("/p", fs(_Root), lambda q: q, lambda q: json.dumps(where))[0], {})
    check("kit752-places-takes-a-good-map",
          load_places("/p", fs(_Root), lambda q: q,
                      lambda q: json.dumps({"bridge.conf": "/ops/bridge.conf"}))[0],
          {"bridge.conf": "/ops/bridge.conf"})
    check("kit752-places-untrusted-reads-nothing",
          load_places("/p", fs(_User), lambda q: q,
                      lambda q: json.dumps({"bridge.conf": "/ops/bridge.conf"}))[0], {})

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
    waker_conf = kit_file("conflict-waker.conf")    # a part with a check and no step of its own
    # The notifier is a step now: its dry run ends on a card alone, and its own check is read once
    # more for the card ids.
    io = FakeIo(codes=dict(healthy, **{"pipeline_notifier_setup.py run --dry-run": 10}))
    io.seen_out = {"pipeline_notifier_setup.py run --dry-run": "-- steps --\n  enable   BLOCKED-ON-HUMAN  CK-N3\n"}
    io.present = {paging_conf}
    io.measure_codes = {"pipeline_notifier_setup.py check": 10}
    io.measure_out = {"pipeline_notifier_setup.py check": "CK-N1 is yours\nthen CK-N3 and CK-N10"}
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
    io.measure_out = {"pipeline_notifier_setup.py check": "something is outstanding"}
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
        io.present = {waker_conf}
        io.measure_codes = {"pipeline_conflict_waker_setup.py run": measured}
        code = main(base, io=io)
        check("lines-check-%s-is-unmeasured-and-exits-4" % name,
              (by_id(io)["conflict-fixer"], code), (OUT_UNMEASURED, EX_UNMEASURED))
    for name, rc, words in (("exit-5", 5, ""), ("its-own-words", 1, "Mac password needed to continue")):
        io = FakeIo(codes=dict(healthy))
        io.present = {waker_conf}
        io.measure_codes = {"pipeline_conflict_waker_setup.py run": rc}
        io.measure_out = {"pipeline_conflict_waker_setup.py run": words}
        main(base, io=io)
        check("lines-password-%s-detail" % name,
              [r["detail"] for r in io.records if r["id"] == "conflict-fixer"],
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
    io.present = {waker_conf, kit_file("update.conf")}
    io.measure_codes = {"pipeline_conflict_waker_setup.py run": 10, "pipeline_watch.py --conf": 1}
    io.levels = ("unmeasured", "its job could not be looked at")
    code = main(base, io=io)
    check("lines-unmeasured-outranks-waiting", code, EX_UNMEASURED)
    code, io = go(codes={"pipeline_stage_a_setup.py run": 10})
    check("lines-the-idea-gate-at-10-still-exits-10", code, EX_BLOCKED)
    io = FakeIo(codes=dict(healthy, **{"pipeline_stage_a_setup.py run": 10}))
    io.present = {waker_conf}
    io.measure_codes = {"pipeline_conflict_waker_setup.py run": 1}
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
    io.present = {waker_conf}
    io.interrupt_on.add("sync_user_skills.py")
    code = main(base, io=io)
    got = by_id(io)
    check("lines-ctrl-c-prints-the-lines-and-runs-no-more-checks",
          (code != EX_OK, got["skills"], got["idea-gate"], got["conflict-fixer"], io.measured),
          (True, OUT_UNMEASURED, OUT_NOT_REACHED, OUT_NOT_REACHED, []))
    io = FakeIo(codes=dict(healthy))
    io.present = {waker_conf}
    io.interrupt_on.add("pipeline_stage_a_setup.py run")
    code = main(base, io=io)
    check("lines-ctrl-c-in-the-last-step-runs-no-more-checks",
          (code != EX_OK, by_id(io)["idea-gate"], by_id(io)["conflict-fixer"], "pipeline_conflict_waker_setup.py run" in io.measured),
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
                                                      "sweep", "stage_a", "bridge", "notifier", "queue", "watch",
                                                      "validator"]),
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

    # 9b. THE LOOK-ONLY MODE, --check (KIT-736): every one of its cases is in selftest_look().
    selftest_look(check, base, se_conf, tmp)

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
    # KIT-850, KIT-547: the notifier's and the validator's parts declare one; the record keeps each
    # part's number from the list, and every other part's empty one.
    listed = dict((p["id"], p["ceiling"]) for p in real_parts)
    check("kit717-record-has-each-ceiling-from-the-parts-list",
          (got["settings"]["ceilings"]["paging"], isinstance(listed["paging"], int),
           dict((k, v) for k, v in got["settings"]["ceilings"].items() if v != listed.get(k, "none")),
           "review" in got["settings"]["ceilings"]),
          (listed["paging"], True, {}, True))
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
