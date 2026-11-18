#!/usr/bin/env python3
"""The validator's installer: its job under your own login, with every switch off (KIT-547).

    python3 scripts/pipeline_validator_setup.py run --dry-run   # read it first: changes nothing
    python3 scripts/pipeline_validator_setup.py run             # checks, shows, one typed yes, applies, checks again
    python3 scripts/pipeline_validator_setup.py verify          # the same look as `run --dry-run`
    python3 scripts/pipeline_validator_setup.py off             # every switch off at once, after one yes
    python3 scripts/pipeline_validator_setup.py card CK-V1      # print a card
    python3 scripts/pipeline_validator_setup.py check           # for a program: rows, asks nothing
    <answers> | python3 scripts/pipeline_validator_setup.py apply   # for a program, after your consent
    python3 scripts/pipeline_validator_setup.py --selftest

Design: docs/adr/2026-11-05-the-validator-approves-inside-limits-that-code-enforces.md, decision 7,
the owner's V1: the validator runs under the user's own login, with no new OS account, and posts
from its own GitHub app. Guide: docs/VALIDATOR-OPERATOR.md, "Install it".

WHAT IT BUILDS, for the person running it and nobody else. It makes no account and runs no sudo.

    ~/.validator/                   a folder only you can write, outside every repository
        settings.json               the switches. Written ONCE, when absent, with every switch off
        config.json                 the job's config, composed from validator.conf: the settings,
                                    the ledger, the watcher's hold and status, the facts' sources,
                                    the agent and the app's id. The job's own loader accepts it first
        validator.log               what each pass said: `OFF: ...` while every switch is off
        last-pass.json              the time and exit code of the job's last pass, written by the
                                    job's own shell line after each pass
        setup.json                  this installer's one fact: whether the job has been on
    ~/Library/LaunchAgents/<JOB_LABEL>.plist
                                    one LaunchAgent, in your own launchd domain (gui/<uid>): each
                                    pass in PASSES every INTERVAL_SECONDS, as you, after ENV_FILE
                                    is read into its environment

THE STEPS, in order. Each row says what it found, and what it did or would do.
  preflight  macOS; this checkout is a main checkout, never a linked worktree a session works in,
             and holds the job's passes; the Python, claude and gh the job runs, by full path
  folder     ~/.validator: made, mode 700, when absent. A folder others can write is FAILED and
             never changed here: tightening it could make a settings file inside it obeyed
  settings   absent: written with every switch off, then read back through the settings' own
             loader. Present: NEVER WRITTEN. What it says is reported, as it reads
  ledger     LEDGER_FILE's folder (made when absent) and the ledger, through the ledger's own
             reader. Whether other accounts can read it is said, never assumed
  app        the app's id, from pipeline_validator_app.py's own record, and APP_KEY_FILE by the
             check's own rule: a plain file only you can read, outside every repository. It is
             looked at with lstat and NEVER OPENED here
  env-file   ENV_FILE: yours, mode 600, outside every repository; and that it sets each NAME,
             judged by a shell that reads it and prints only `NAME=set` or `NAME=unset`. No value
             reaches this process. Under a model it is never read: NOT MEASURED
  hold       the watcher's hold, read as the job reads it (pipeline_validator_limits.hold_state).
             A hold it cannot read FAILS: every pass would hold. The status file is a note
  config     composed, accepted by the job's own loader, written mode 600 when it differs
  job        the plist, written when it differs, never over a job that runs something else.
             Written while the job is off, its label is disabled in the same step, so a new
             login does not start it before the next row decides
  enable     launchd's own answer: loaded, running THIS plist, and its last pass exited 0. Not
             loaded and never on: switched on only at or below the first-pass ceiling. A job
             that was on and is not loaded was paused by you, and is never switched back on

THE FIRST-PASS CEILING (the shape KIT-850 set). `pipeline-parts.json` gives the validator's part a
ceiling: the most things its first pass may decide with nobody reading the number first. It is 0.
Before it switches the job on, `run` and `apply` rehearse the job's own command with `--dry-run`
and count the `[dry-run]` lines: what a real pass would record, post or turn on. At or below the
ceiling, the job is enabled and loaded, then its first pass is waited for and judged. Above it,
or with a rehearsal that did not exit 0, or no ceiling it can read, the job stays installed,
disabled and off, and one line says why. With every switch off, the first pass decides nothing.

COMMANDS
    run --dry-run | verify   every row measured; nothing changed, recorded or asked
    run                      a person's: the look, then the plan, then ONE typed yes, then each
                             row applied in order, then the look again. A row only a person can
                             clear stops it and prints its card
    off                      every switch off at once: each `approve` and `allow` switch goes
                             false, after one typed yes, and is read back. Limits, Stage E's
                             login and the repository list stay. It reads no conf
    check                    a program's: the look. ONE JSON document of rows on stdout; every
                             human word on stderr. Asks nothing, changes and records nothing
    apply                    a program's, after the person's consent: one answers document on
                             stdin, through a PIPE only, `{"schema": "validator-answers/1",
                             "consent": "yes"}`. Anything but consent "yes" changes and records
                             nothing and exits 10. It asks nothing, stops at the first row only
                             a person can clear, and prints the same JSON document as `check`
    card <CK-id>             print a card

UNDER A MODEL (a Claude Code marker set, even to nothing: pipeline_agent_env.AGENT_ENV_MARKERS).
`run` without --dry-run, `apply` and `off` are REFUSED, with no override, before the conf is read;
`apply` before its standard input is read. A session that installs its own supervisor, or turns
its own approvals on or off, is what the refusal exists to stop. `check`, `verify`, `run --dry-run`
and `card` still work, read nothing secret, and never read ENV_FILE: that row is NOT MEASURED.

WHAT IT NEVER DOES (asserted in --selftest). Ask for or run sudo. Make an account. Write the
settings file when one is there, or with a switch on. Open the app's key file. Read a value out of
ENV_FILE into this process. Print, log or store a credential. Merge, approve or label anything.

EXIT CODES (contract section 13, the Stage E installer's numbers)
    0   every row holds (a second run says `Nothing to change`)
    1   a row FAILED
    2   the conf, or the command line, needs fixing; nothing was attempted
    3   REFUSED: an agent environment, or root
    4   a row could not measure itself (NOT MEASURED): never "up to date"
    10  waiting: a row would change something, a card is yours, or you did not type yes
"""
import argparse
import calendar
import json
import os
import plistlib
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    from pipeline_agent_env import AGENT_ENV_MARKERS     # one list, KIT-690
    import pipeline_hold as hold
    import pipeline_notifier_setup as ns                  # its pipe reader, ceiling, scrub, launchd words
    import pipeline_stage_e_setup as se                   # the exit codes, the outcome words, the table
    import pipeline_validator_app as vapp                 # the app's record, the key rule, the settings writer
    import pipeline_validator_check as vcheck
    import pipeline_validator_ledger as vledger
    import pipeline_validator_limits as limits
    import pipeline_validator_merge as vmerge             # the job's own config loader
    import pipeline_validator_settings as vsettings
except ImportError as exc:  # a packaging accident, never a state to run in
    sys.exit("FAIL: %s. The installer uses those files' own code and keeps no copy." % exc)

EX_OK, EX_FAILED, EX_USAGE, EX_REFUSED, EX_UNKNOWN, EX_BLOCKED = (
    se.EX_OK, se.EX_FAILED, se.EX_USAGE, se.EX_REFUSED, se.EX_UNKNOWN, se.EX_BLOCKED)
DONE, ALREADY_DONE, BLOCKED, FAILED, UNKNOWN, WOULD_CHANGE, SKIPPED = (
    se.DONE, se.ALREADY_DONE, se.BLOCKED, se.FAILED, se.UNKNOWN, se.WOULD_CHANGE, se.SKIPPED)
HOLDING = (ALREADY_DONE, DONE, WOULD_CHANGE)

DEFAULT_CONF = "validator.conf"
FOLDER = ".validator"
SETTINGS_NAME, CONFIG_NAME, LOG_NAME = "settings.json", "config.json", "validator.log"
BEAT_NAME, STATE_NAME = "last-pass.json", "setup.json"
# The NAME the job's config gives the app key file's path (pipeline_validator_check.credential).
# The plist sets it to APP_KEY_FILE: a path, never the key.
KEY_ENV = "VALIDATOR_APP_KEY_FILE"
# The passes the job runs, in order, each `run --config <config>`. The validator's piece pass
# (KIT-543) joins here when it lands; nothing else in this file changes for it.
PASSES = ("pipeline_validator_merge.py",)
ROWS_SCHEMA = "validator-setup-rows/1"
ANSWERS_SCHEMA = "validator-answers/1"
ANSWERS_KEYS = frozenset(("schema", "consent"))
STATE_SCHEMA = "validator-setup/1"
BEAT_SCHEMA = "validator-pass/1"
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
REHEARSAL_TIMEOUT = 900             # a pass with a switch on reads GitHub and the tracker
FIRST_PASS_POLL_SECONDS = 2
FIRST_PASS_WAIT_SECONDS = 120       # a pass with every switch off takes about a second
STALE_EXTRA_SECONDS = 900           # a loaded job's last pass may be this much past two intervals
CLOCK_SKEW_SECONDS = 120

CONF_DEFAULTS = {
    "JOB_LABEL": "local.validator", "INTERVAL_SECONDS": "300",
    "ENV_FILE": "~/.validator/env", "LINEAR_KEY_ENV": "VALIDATOR_LINEAR_API_KEY",
    "AGENT_TOKEN_ENV": "CLAUDE_WRITER_OAUTH_TOKEN", "APP_KEY_FILE": "~/.validator/app.pem",
    "LEDGER_FILE": "~/.validator/ledger.json", "HOLD_FILE": "", "WATCHER_STATUS": "",
    "PYTHON": "/usr/bin/python3", "AGENT_BIN": "", "GH_BIN": "", "TEAM_KEYS": "",
    "QUEUE_CONFIG": "", "STAGE_E_LEDGER": "", "SNAPSHOT_DIR": "", "ACCEPTANCE_LEDGER": "",
}
CONF_REQUIRED = ("HOLD_FILE", "WATCHER_STATUS")
PATH_KEYS = ("ENV_FILE", "APP_KEY_FILE", "LEDGER_FILE", "HOLD_FILE", "WATCHER_STATUS", "PYTHON")
OPTIONAL_PATH_KEYS = ("AGENT_BIN", "GH_BIN", "QUEUE_CONFIG", "STAGE_E_LEDGER", "SNAPSHOT_DIR",
                      "ACCEPTANCE_LEDGER")
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
TEAM_RE = re.compile(r"^[A-Z][A-Z0-9]{0,9}$")
RESERVED_NAMES = ("PATH", "HOME", "LANG", KEY_ENV)

# A small script a shell runs to say whether the env file sets each NAME, and nothing else. The
# file is read in that shell; only `NAME=set` or `NAME=unset` comes back. Its own words are never
# printed: the answer is parsed, and anything else is NOT MEASURED.
ENV_PROBE = ('f=$1; shift; set -a; . "$f" >/dev/null 2>&1 || exit 3; set +a; '
             'for n in "$@"; do eval "v=\\${$n:-}"; '
             'if [ -n "$v" ]; then echo "$n=set"; else echo "$n=unset"; fi; done')
# After the passes, the job's shell line writes its own last-pass file: the time and the exit.
BEAT_PRINTF = ("printf '{\"schema\": \"" + BEAT_SCHEMA + "\", \"at\": \"%s\", \"exit\": %d}\\n' "
               "\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\" \"$rc\"")

say = se.say


# --------------------------------------------------------------------------- #
# The cards: what only a person can do, each with its words
# --------------------------------------------------------------------------- #
CARDS = {
    "CK-V1": {
        "title": "Make the validator's own app first",
        "why": ("The job posts its Validator check, and turns GitHub's own auto-merge on, as the "
                "validator's own app. This installer reads the app's id from that script's own "
                "record. Making the app and its ruleset is that script's, run once by you."),
        "do": ["Make the app and its key file (docs/VALIDATOR-OPERATOR.md, 'Make the check",
               "required'). Use the same key file this conf names:",
               "    python3 scripts/pipeline_validator_app.py run --repo OWNER/NAME --key-file ${APP_KEY_FILE}",
               "Then run this installer again."],
    },
    "CK-V2": {
        "title": "Write the validator's env file",
        "why": ("Before each pass, the job reads its keys from a file only you can read. This "
                "installer never writes, prints or keeps a key: it only asks a shell that reads "
                "the file whether each NAME is set."),
        "do": ["Make the file, mode 600, and write one line per name in an editor:",
               "    install -m 600 /dev/null ${ENV_FILE}     (only when it is not there yet)",
               "    $EDITOR ${ENV_FILE}",
               "${NAME_LINES}",
               "Make the tracker key read-only where you create it. Then run this again."],
    },
    "CK-V3": {
        "title": "Switch the validator's job on yourself, or resume it",
        "why": ("The installer switches the job on itself only when its first pass would decide no "
                "more than its ceiling. It left the job off: above the ceiling, or because you "
                "paused it, which it never undoes."),
        "do": ["Read the rehearsal first: python3 scripts/pipeline_validator_setup.py run --dry-run",
               "Then switch it on. The enable line comes first: a disabled job will not load.",
               "    launchctl enable gui/$(id -u)/${JOB_LABEL}",
               "    launchctl bootstrap gui/$(id -u) ${PLIST}",
               "To pause it again, so that a new login does not start it either:",
               "    launchctl bootout gui/$(id -u)/${JOB_LABEL}",
               "    launchctl disable gui/$(id -u)/${JOB_LABEL}"],
    },
}


def card_lines(card_id, values):
    """The card's words, with ${NAME} filled from `values`. A name with no value stays as written."""
    card = CARDS[card_id]

    def fill(text):
        return re.sub(r"\$\{([A-Z_]+)\}", lambda m: str(values.get(m.group(1), m.group(0))), text)
    out = ["", "=" * 72, "%s  %s" % (card_id, card["title"]), "=" * 72, "", "WHY: " + card["why"],
           "", "WHAT TO DO:"]
    for line in card["do"]:
        out.extend(fill(line).splitlines())
    return out + [""]


# --------------------------------------------------------------------------- #
# The conf: KEY=value, every problem at once, by key, never quoting a value
# --------------------------------------------------------------------------- #
def parse_conf(text, source=DEFAULT_CONF):
    """(values, errors). Every bad line is named; no line is quoted (one may hold a credential)."""
    values, seen, errors = {}, {}, []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, eq, val = (p.strip() for p in line.partition("="))
        where = "%s:%d" % (source, n)
        if not eq or not re.match(r"^[A-Z][A-Z0-9_]*$", key):
            errors.append("%s: not KEY=value" % where)
        elif key in seen:
            errors.append("%s: %s is given twice (first on line %d)" % (where, key, seen[key]))
        elif key not in CONF_DEFAULTS:
            errors.append("%s: %s is not a setting of this installer" % (where, key))
        elif ns.credential_shape(val):
            errors.append("%s: %s carries a CREDENTIAL SHAPE; no key here holds one (not shown)"
                          % (where, key))
        else:
            seen[key], values[key] = n, val
    return values, errors


def expand(home, value):
    """`~/x` against `home` (the account's own, never $HOME); anything else as it is."""
    if value == "~":
        return home
    if value.startswith("~/"):
        return os.path.join(home, value[2:])
    return value


def validate_conf(values, home):
    """(conf, errors): the defaults filled in, `~/` expanded against `home`, and every fault named."""
    c = dict(CONF_DEFAULTS, **values)
    errors = []

    def need(ok, why):
        if not ok:
            errors.append(why)
    for k in CONF_REQUIRED:
        need(c[k], "%s is required: the watcher's own file (watcher.conf's STATE_DIR)" % k)
    need(se._RDNS_RE.match(c["JOB_LABEL"] or ""), "JOB_LABEL must be a reverse-DNS launchd label")
    iv = c["INTERVAL_SECONDS"]
    need(re.match(r"^[0-9]{1,5}$", iv or "") and 60 <= int(iv) <= 3600,
         "INTERVAL_SECONDS must be a whole number of seconds from 60 to 3600")
    for k in PATH_KEYS + OPTIONAL_PATH_KEYS:
        v = c[k]
        if not v:
            continue
        need(ns._PATH_RE.match(v) and "/../" not in v + "/" and v.rstrip("/") not in ("", "~"),
             "%s must be an absolute path (or start with ~/), with no `..`" % k)
        c[k] = expand(home, v).rstrip("/") if ns._PATH_RE.match(v) else v
    for k in ("AGENT_TOKEN_ENV",):
        need(ENV_NAME_RE.match(c[k] or ""), "%s must be the NAME of an environment variable" % k)
    need(not c["LINEAR_KEY_ENV"] or ENV_NAME_RE.match(c["LINEAR_KEY_ENV"]),
         "LINEAR_KEY_ENV must be empty, or the NAME of an environment variable")
    for k in ("LINEAR_KEY_ENV", "AGENT_TOKEN_ENV"):
        need(c[k] not in RESERVED_NAMES, "%s may not be %s: the job sets that name itself" % (k, c[k]))
    need(not c["LINEAR_KEY_ENV"] or c["LINEAR_KEY_ENV"] != c["AGENT_TOKEN_ENV"],
         "LINEAR_KEY_ENV and AGENT_TOKEN_ENV must name two different variables")
    teams = se.split_list(c["TEAM_KEYS"])
    need(all(TEAM_RE.match(t) for t in teams), "TEAM_KEYS must list team KEYS, such as ENG,OPS")
    folder = os.path.join(home, FOLDER)
    fixed = dict((os.path.join(folder, n), n) for n in
                 (SETTINGS_NAME, CONFIG_NAME, LOG_NAME, BEAT_NAME, STATE_NAME))
    named = {}
    for k in ("ENV_FILE", "APP_KEY_FILE", "LEDGER_FILE", "HOLD_FILE", "WATCHER_STATUS"):
        v = c[k]
        if not v or not os.path.isabs(v):
            continue
        need(v not in fixed, "%s may not be %s: the installer keeps its own %s there" % (k, v, fixed.get(v)))
        need(v not in named, "%s and %s name the same file" % (named.get(v), k))
        named.setdefault(v, k)
    for k in ("HOLD_FILE", "WATCHER_STATUS"):
        v = c[k]
        need(not v or not (v == home or v.startswith(home.rstrip("/") + "/")),
             "%s must be the watcher's own file, outside your home: a hold you could remove "
             "is no hold" % k)
    return c, errors


def load_conf(path, home):
    """(conf, errors). A conf that cannot be read is one error, with the command that makes it."""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        return None, ["could not read %s (%s): cp validator.conf.example validator.conf && chmod "
                      "600 validator.conf" % (path, exc.strerror or type(exc).__name__)]
    values, errors = parse_conf(text, os.path.basename(path))
    conf, more = validate_conf(values, home)
    conf["__source__"] = path
    return conf, errors + more


# --------------------------------------------------------------------------- #
# The runner: every command and every write goes through here
# --------------------------------------------------------------------------- #
class Runner(object):
    """`read` always runs: a look that cannot look would guess. `write` and `do` change something,
    and do nothing on a dry pass; every one is recorded, done or not. Nothing here runs sudo."""

    def __init__(self, dry=True):
        self.dry = dry
        self.writes = []
        self.reads = []

    def read(self, argv, timeout=60, env=None):
        self.reads.append(list(argv))
        return self._exec(argv, timeout, env)

    def write(self, why, argv, timeout=60, env=None):
        self.writes.append({"why": why, "argv": list(argv)})
        if self.dry:
            return se.Result(0, skipped=True)
        return self._exec(argv, timeout, env)

    def do(self, why, path, fn):
        """A change to a file or a folder, made by `fn`. True when it ran."""
        self.writes.append({"why": why, "path": path})
        if self.dry:
            return False
        fn()
        return True

    def _exec(self, argv, timeout, env):
        try:
            p = subprocess.run(argv, capture_output=True, text=True, errors="replace",
                               timeout=timeout, env=env, stdin=subprocess.DEVNULL, cwd="/")
        except FileNotFoundError as exc:
            return se.Result(127, "", "not found: %s" % exc.filename)
        except subprocess.TimeoutExpired:
            return se.Result(124, "", "timed out after %d s" % timeout)
        except OSError as exc:
            return se.Result(126, "", exc.strerror or type(exc).__name__)
        return se.Result(p.returncode, p.stdout, p.stderr)


def write_text(path, text, mode):
    """`text` to `path` by one rename, at `mode` from its first byte."""
    tmp = "%s.%d.tmp" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# --------------------------------------------------------------------------- #
# The context one pass is run in
# --------------------------------------------------------------------------- #
def account_home():
    """The account's own home, from the account database: never $HOME, which a shell can set."""
    import pwd
    return pwd.getpwuid(os.getuid()).pw_dir


class Ctx(object):
    def __init__(self, conf, home, runner, env, kit=KIT, uid=None, platform=None, which=None,
                 clock=None, pause=None, tty=False, ask=None):
        self.conf, self.home, self.runner, self.env, self.kit = conf, home, runner, env, kit
        self.agent = [m for m in AGENT_ENV_MARKERS if m in env]
        self.uid = os.getuid() if uid is None else uid
        self.platform = platform or sys.platform
        self.which = which or shutil.which
        self.clock = clock or time.time
        self.pause = pause or se._pause
        self.tty, self.ask = tty, ask
        self.command = "verify"
        self.apply_it = False
        self.ceiling, self.ceiling_why = None, "not read on this pass"
        self.first_pass = None          # (things decided, exit) from a rehearsal in THIS pass
        self.left_off = None            # the one line saying why the job stays off
        self.row_cards = {}
        self.app_id = None
        self.bins = {}
        self.plist_changed = False
        self.flags = ""                 # the --conf this run was given, repeated in what it prints

    @property
    def folder(self):
        return os.path.join(self.home, FOLDER)

    def at(self, name):
        return os.path.join(self.folder, name)

    @property
    def settings_path(self):
        return self.at(SETTINGS_NAME)

    @property
    def config_path(self):
        return self.at(CONFIG_NAME)

    @property
    def plist_path(self):
        return os.path.join(self.home, "Library", "LaunchAgents", self.conf["JOB_LABEL"] + ".plist")

    @property
    def domain(self):
        return "gui/%d" % self.uid

    @property
    def target(self):
        return "%s/%s" % (self.domain, self.conf["JOB_LABEL"])

    @property
    def app_record(self):
        return os.path.join(self.home, *vapp.LEDGER.split("/")[1:])

    def card_values(self):
        names = [n for n in (self.conf.get("LINEAR_KEY_ENV"), self.conf.get("AGENT_TOKEN_ENV")) if n]
        return dict(self.conf, PLIST=self.plist_path, NAME_LINES="\n".join(
            "    %s=<%s>" % (n, "a READ-ONLY tracker key" if n == self.conf.get("LINEAR_KEY_ENV")
                            else "a token from `claude setup-token`") for n in names))


def clean(text):
    """What a child said, made safe to print: control characters and any line with a credential
    shape withheld, by the notifier's own scrub."""
    return ns.scrub(text or "", ())


def open_above(folder):
    """The first folder from `folder` up that group or other can write (the settings' own rule), or
    the folder that could not be looked at: either way, not a place to trust."""
    try:
        return vsettings.open_folder(folder)
    except OSError:
        return folder


def owned_plain(ctx, path, st):
    """Why the file or folder `st` describes cannot be yours alone to trust, or None."""
    if stat.S_ISLNK(st.st_mode):
        return "is a symbolic link"
    if st.st_uid != ctx.uid:
        return "is owned by another account"
    if vledger.inside_git_worktree(os.path.dirname(path)):
        return "is inside a git working tree, where a session can write"
    return None


# --------------------------------------------------------------------------- #
# The job's command line and its job file
# --------------------------------------------------------------------------- #
def job_line(ctx, dry=False):
    """The one shell line the job runs: read ENV_FILE into the environment, run each pass, keep the
    worst exit, then (not in a rehearsal) write the last-pass file and exit with that code."""
    q = shlex.quote
    passes = " ".join("%s %s run --config %s%s || rc=$?;" % (
        q(ctx.conf["PYTHON"]), q(os.path.join(ctx.kit, "scripts", p)), q(ctx.config_path),
        " --dry-run" if dry else "") for p in PASSES)
    line = "set -a; . %s; set +a; rc=0; %s" % (q(ctx.conf["ENV_FILE"]), passes)
    if not dry:
        beat = ctx.at(BEAT_NAME)
        line += " %s > %s && mv -f %s %s;" % (BEAT_PRINTF, q(beat + ".tmp"), q(beat + ".tmp"), q(beat))
    return line + " exit $rc"


def job_env(ctx):
    dirs = []
    for path in (ctx.bins.get("gh"), ctx.bins.get("claude")):
        if path and os.path.dirname(path) not in dirs:
            dirs.append(os.path.dirname(path))
    return {"PATH": ":".join(dirs + [SYSTEM_PATH]), "HOME": ctx.home, "LANG": "en_US.UTF-8",
            KEY_ENV: ctx.conf["APP_KEY_FILE"]}


def render_plist(ctx):
    log = ctx.at(LOG_NAME)
    return {"Label": ctx.conf["JOB_LABEL"],
            "ProgramArguments": ["/bin/sh", "-c", job_line(ctx)],
            "StartInterval": int(ctx.conf["INTERVAL_SECONDS"]),
            "RunAtLoad": True,
            "EnvironmentVariables": job_env(ctx),
            "StandardOutPath": log, "StandardErrorPath": log,
            "ProcessType": "Background"}


def ours(doc):
    """True when a job file already at our label runs the validator's passes."""
    args = doc.get("ProgramArguments") if isinstance(doc, dict) else None
    text = " ".join(str(a) for a in args) if isinstance(args, list) else ""
    return any(p in text for p in PASSES)


# --------------------------------------------------------------------------- #
# launchd, in your own domain. Every call goes through the runner; none needs sudo.
# --------------------------------------------------------------------------- #
def launchd_holds(ctx):
    """(held, launchd's own words). A print that failed is never "not loaded": it raises."""
    res = ctx.runner.read(["launchctl", "print", ctx.target])
    text = (res.out or "") + (res.err or "")
    if res.ok:
        return True, text
    if res.rc == 113 or "Could not find service" in text:
        return False, text
    raise se.Unknown("could not ask launchd about %s (exit %d): %s"
                     % (ctx.target, res.rc, clean(text.strip())[:160]), "run the same command again")


def label_disabled(ctx):
    res = ctx.runner.read(["launchctl", "print-disabled", ctx.domain])
    if not res.ok:
        raise se.Unknown("could not ask launchd whether a new login starts %s (exit %d)"
                         % (ctx.target, res.rc), "run the same command again")
    return bool(re.search(ns._DISABLED_LINE % re.escape(ctx.conf["JOB_LABEL"]), res.out or ""))


def disable(ctx):
    """Disable the label, so the job file cannot start at the next login while the job is off, and
    ask launchd again: a disable that does not show is a failure."""
    res = ctx.runner.write("disable %s, so a new login does not start it while it is off"
                           % ctx.target, ["launchctl", "disable", ctx.target])
    if res.skipped:
        return
    if not res.ok or not label_disabled(ctx):
        raise se.SetupError("could not disable %s (exit %d): a new login would start it"
                            % (ctx.target, res.rc))


def _bootstrap(ctx):
    """'' once launchd holds the job, else launchd's words. An Input/output error is what a domain
    still letting an older job go answers, so that one is tried again."""
    said = ""
    for attempt in range(1, se.BOOTSTRAP_ATTEMPTS + 1):
        if attempt > 1:
            ctx.pause(se.BOOTSTRAP_RETRY_SECONDS)
        boot = ctx.runner.write("load %s from %s" % (ctx.target, ctx.plist_path),
                                ["launchctl", "bootstrap", ctx.domain, ctx.plist_path])
        if boot.skipped or launchd_holds(ctx)[0]:
            return ""
        said = clean((boot.err or boot.out).strip() or "launchctl accepted the load, and launchd "
                     "does not hold the job")[:160]
        if not se._EIO.search(said):
            break
    return said


def load_job(ctx, reload_it=False):
    """THE ONE PLACE THE JOB IS LOADED. Reached only from the `enable` row, when `run` or `apply`
    applies after a person's yes. A reload unloads the older job and waits for launchd to let it
    go. Enable first: a label disabled while the job was off will not load without it. A load that
    does not take is disabled again: off stays off, through a new login too."""
    if not ctx.apply_it:
        raise se.SetupError("BUG: a look never loads the job")
    if reload_it:
        out = ctx.runner.write("unload %s, which holds an older job file" % ctx.target,
                               ["launchctl", "bootout", ctx.target])
        if not out.ok and not out.skipped:
            raise se.SetupError("could not unload %s to load the new job file (exit %d)"
                                % (ctx.target, out.rc))
        polls = int((se.DEFAULT_EXIT_TIMEOUT + se.EXIT_TIMEOUT_HEADROOM) / se.BOOTOUT_POLL_SECONDS)
        for n in range(polls + 1):
            if not launchd_holds(ctx)[0]:
                break
            if n == polls:
                raise se.SetupError("%s was unloaded, and launchd still held it after %d s, so it "
                                    "was not loaded again" % (ctx.target, polls * se.BOOTOUT_POLL_SECONDS))
            ctx.pause(se.BOOTOUT_POLL_SECONDS)
    en = ctx.runner.write("enable %s, so launchd loads it now and at each login" % ctx.target,
                          ["launchctl", "enable", ctx.target])
    if not en.ok and not en.skipped:
        raise se.SetupError("could not enable %s (exit %d)" % (ctx.target, en.rc))
    said = _bootstrap(ctx)
    if said:
        disable(ctx)
        raise se.SetupError("launchd would not load %s: %s. It is disabled again, so a new login "
                            "does not start it either" % (ctx.target, said))


# --------------------------------------------------------------------------- #
# The job's own record of its passes, and this installer's one fact
# --------------------------------------------------------------------------- #
def read_beat(ctx):
    """({"at": epoch, "exit": int}, None), or (None, why): the job's last-pass file."""
    path = ctx.at(BEAT_NAME)
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.loads(fh.read(4096))
        when = calendar.timegm(time.strptime(doc["at"], "%Y-%m-%dT%H:%M:%SZ"))
        code = doc["exit"]
    except FileNotFoundError:
        return None, "no pass of it has finished yet"
    except (OSError, ValueError, KeyError, TypeError):
        return None, "its last-pass file %s could not be read" % path
    if doc.get("schema") != BEAT_SCHEMA or not isinstance(code, int) or isinstance(code, bool):
        return None, "its last-pass file %s is not one" % path
    return {"at": when, "exit": code}, None


def log_size(ctx):
    try:
        return os.path.getsize(ctx.at(LOG_NAME))
    except OSError:
        return 0


def last_said(ctx, since=0):
    """The last line the job wrote to its log after byte `since`, made safe to print."""
    try:
        with open(ctx.at(LOG_NAME), "rb") as fh:
            fh.seek(max(since, os.path.getsize(ctx.at(LOG_NAME)) - 4096))
            lines = [l for l in fh.read().decode("utf-8", "replace").splitlines() if l.strip()]
    except OSError:
        return "(its log could not be read)"
    return clean(lines[-1].strip())[:200] if lines else "(it wrote nothing to its log)"


def read_state(ctx):
    """This installer's one fact, or None when it cannot be trusted: a record that is not yours
    alone reads as "the job was on", so nothing switches on what a person may have paused."""
    path = ctx.at(STATE_NAME)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return {}
    except OSError:
        return None
    if (not stat.S_ISREG(st.st_mode) or st.st_uid != ctx.uid
            or st.st_mode & (stat.S_IWGRP | stat.S_IWOTH)):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.loads(fh.read(4096))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and doc.get("schema") == STATE_SCHEMA else None


def was_on(ctx):
    doc = read_state(ctx)
    return doc is None or bool(doc.get("switched_on"))


def record_on(ctx, by):
    """Recorded once: a later unload then reads as a person's pause, which nothing here undoes."""
    doc = read_state(ctx)
    if doc is None or doc.get("switched_on"):
        return
    text = json.dumps({"schema": STATE_SCHEMA, "switched_on": {"at": se.now_iso(), "by": by}},
                      sort_keys=True) + "\n"
    path = ctx.at(STATE_NAME)
    ctx.runner.do("record that the job has been on", path, lambda: write_text(path, text, 0o600))


# --------------------------------------------------------------------------- #
# The steps. Each returns (holds, detail, notes) or raises se.Blocked / se.Unknown / SetupError.
# holds True is ALREADY-DONE; False is WOULD-CHANGE on a look and DONE when applied.
# --------------------------------------------------------------------------- #
def kit_problem(kit):
    """Why the job may not run this checkout's code, or None. A linked worktree is where a session
    works: its files change without a merge. A main checkout changes when you pull."""
    if not os.path.isdir(os.path.join(kit, ".git")):
        return ("%s is not a main checkout (its .git is not a folder). A linked worktree is where a "
                "session works. Run this from the main checkout you pull into" % kit)
    gone = [p for p in PASSES if not os.path.isfile(os.path.join(kit, "scripts", p))]
    if gone:
        return "%s holds no %s" % (kit, ", ".join(gone))
    return None


def step_preflight(ctx, apply_it):
    c, problems = ctx.conf, []
    if ctx.platform != "darwin":
        raise se.SetupError("the job is a launchd LaunchAgent: macOS only. Elsewhere, run each pass "
                            "from cron or a systemd timer: python3 scripts/pipeline_validator_merge.py "
                            "run --config ~/.validator/config.json")
    why = kit_problem(ctx.kit)
    if why:
        problems.append(why)
    if not (os.path.isfile(c["PYTHON"]) and os.access(c["PYTHON"], os.X_OK)):
        problems.append("PYTHON %s is not a program this account can run" % c["PYTHON"])
    for tool, key in (("claude", "AGENT_BIN"), ("gh", "GH_BIN")):
        path = c[key] or ctx.which(tool) or ""
        if not (os.path.isabs(path) and os.path.isfile(path) and os.access(path, os.X_OK)):
            problems.append("%s: %s" % (key, "%s is not a program this account can run" % path if c[key]
                                        else "no `%s` on your PATH: set %s to its full path" % (tool, key)))
        else:
            ctx.bins[tool] = os.path.abspath(path)
    if problems:
        raise se.SetupError("; ".join(problems))
    return True, ("macOS; the job runs %s; python %s, claude %s, gh %s"
                  % (ctx.kit, c["PYTHON"], ctx.bins["claude"], ctx.bins["gh"])), []


def step_folder(ctx, apply_it):
    path = ctx.folder
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        if not apply_it:
            return False, "would make %s, mode 700" % path, []

        def make():
            os.mkdir(path, 0o700)
            os.chmod(path, 0o700)
        ctx.runner.do("make %s, mode 700" % path, path, make)
        return False, "made %s, mode 700" % path, []
    except OSError as exc:
        raise se.Unknown("could not look at %s (%s)" % (path, exc.strerror), "run the same command again")
    why = owned_plain(ctx, path, st) or (None if stat.S_ISDIR(st.st_mode) else "is not a folder")
    if not why and st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        why = ("can be written by group or other: run `chmod 700 %s` yourself. The installer never "
               "changes it: with a settings file inside, tightening it could make switches count "
               "that read off today" % path)
    if not why:
        opened = open_above(path)
        why = "sits under %s, which group or other can write" % opened if opened else None
    if why:
        raise se.SetupError("%s %s" % (path, why))
    notes = (["%s can be read by other accounts (mode %o). `chmod 700 %s` keeps your settings and "
              "log to you" % (path, st.st_mode & 0o777, path)] if st.st_mode & 0o077 else [])
    return True, "%s: yours, outside every repository" % path, notes


def all_off_doc():
    """The settings a fresh install writes: validator-settings.example.json's own content."""
    return {"schema": vsettings.SETTINGS_SCHEMA,
            "approve": dict((k, False) for k in vsettings.APPROVE),
            "limits": dict((k, 0) for k, _top in vsettings.LIMITS),
            "allow": dict((k, False) for k in vsettings.ALLOW),
            vsettings.LOGIN_KEY: None, vsettings.REPOS_KEY: []}


def switches_on(s):
    return (["approve %s" % k for k in vsettings.APPROVE if s["approve"][k]]
            + ["allow %s" % k.replace("_", " ") for k in vsettings.ALLOW if s["allow"][k]])


def step_settings(ctx, apply_it):
    """Absent: written once, every switch off, read back. Present: NEVER written, only reported."""
    path = ctx.settings_path
    got = vsettings.load(path)
    if got["state"] == vsettings.ABSENT:
        if not apply_it:
            return False, ("would write %s: every switch off, every limit 0, no repository listed"
                           % path), []
        try:
            ctx.runner.do("write the settings, every switch off", path,
                          lambda: vapp.write_settings(path, all_off_doc()))
        except (vapp.StepFailed, OSError) as exc:
            raise se.SetupError("could not write %s: %s" % (path, exc))
        back = vsettings.load(path)
        if back["state"] != vsettings.READ or switches_on(back):
            raise se.SetupError("%s did not read back as every switch off: %s" % (path, back["why"]))
        return False, "wrote %s: every switch off, read back by the settings' own loader" % path, []
    if got["state"] == vsettings.READ:
        on = switches_on(got)
        return True, "%s: kept as you left it: %s" % (path, "on: %s" % ", ".join(on) if on
                                                       else "every switch off"), []
    return True, "%s: kept as it is. It is not obeyed, so every switch reads off" % path, [
        "the settings file is not obeyed: %s. The installer never writes a settings file that is "
        "there. Fix it yourself, or run `off` to replace it with one that has every switch off"
        % got["why"]]


def others_can_read(path):
    """'' when every account can reach `path` (each folder above it lets others in, and the file,
    when it is there, lets others read it), else the first place that stops them."""
    target = path if os.path.exists(path) else os.path.dirname(path)
    cur, chain = os.path.realpath(target), []
    while True:
        chain.append(cur)
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    for folder in reversed(chain):
        try:
            st = os.stat(folder)
        except OSError:
            return folder
        need = stat.S_IROTH if (folder == os.path.realpath(path) and not stat.S_ISDIR(st.st_mode)) \
            else stat.S_IXOTH
        if not st.st_mode & need:
            return folder
    return ""


def step_ledger(ctx, apply_it):
    path = ctx.conf["LEDGER_FILE"]
    folder = os.path.dirname(path)
    made = None
    if folder != ctx.folder:
        try:
            st = os.lstat(folder)
        except FileNotFoundError:
            if not os.path.isdir(os.path.dirname(folder)):
                raise se.SetupError("the ledger's folder %s, and the folder above it, are not there: "
                                    "make the one above, or name another LEDGER_FILE" % folder)
            made = folder
            st = None
        except OSError as exc:
            raise se.Unknown("could not look at %s (%s)" % (folder, exc.strerror), "run it again")
        if st is not None:
            why = owned_plain(ctx, folder, st) or (None if stat.S_ISDIR(st.st_mode) else "is not a folder")
            if not why and (st.st_mode & (stat.S_IWGRP | stat.S_IWOTH) or open_above(folder)):
                why = "can be written by group or other, or sits under a folder that can"
            if why:
                raise se.SetupError("the ledger's folder %s %s" % (folder, why))
    got = vledger.read(path)
    if got["rows"] is None:
        raise se.SetupError("the ledger %s cannot be trusted: %s" % (path, got["why"]))
    stops = others_can_read(path) if not made else others_can_read(os.path.dirname(folder))
    notes = ["the watcher's approval check and the stand-up read the ledger from their own "
             "accounts. %s" % ("Every account can read it." if not stops else
                               "Other accounts cannot reach it: %s stops them. Name a LEDGER_FILE "
                               "they can reach, and the same path in their settings" % stops)]
    said = "no ledger yet: nothing has been decided" if got["state"] == vledger.ABSENT else \
        "%d row(s), read by its own reader" % len(got["rows"])
    if made:
        if not apply_it:
            return False, "would make %s, mode 755, for the ledger; %s" % (folder, said), notes

        def make():
            os.mkdir(folder, 0o755)
            os.chmod(folder, 0o755)
        ctx.runner.do("make %s, mode 755, for the ledger" % folder, folder, make)
        return False, "made %s, mode 755, for the ledger; %s" % (folder, said), notes
    return True, "%s: %s" % (path, said), notes


def step_app(ctx, apply_it):
    doc, why = vapp.read_ledger(ctx.app_record)
    if why:
        raise se.SetupError(why)
    app_id = doc.get("app_id")
    if isinstance(app_id, bool) or not isinstance(app_id, int) or app_id < 1:
        raise se.Blocked("CK-V1", "the validator's app is not made yet: %s holds no app id"
                         % ctx.app_record)
    key = ctx.conf["APP_KEY_FILE"]
    recorded = doc.get("key_file")
    if isinstance(recorded, str) and recorded and os.path.abspath(expand(ctx.home, recorded)) != key:
        raise se.SetupError("the app's own record names the key file %s, and APP_KEY_FILE is %s: "
                            "set APP_KEY_FILE to the app's" % (recorded, key))
    # The job's own rule, by lstat: the key is never opened here.
    problem = vapp.key_problem(key)
    _id, _path, rule = vcheck.credential({"app_id": app_id, "key_file_env": KEY_ENV}, {KEY_ENV: key})
    if problem or rule:
        raise se.SetupError(problem or rule)
    ctx.app_id = app_id
    return True, ("app %d; its key %s is a plain file only you can read, outside every repository "
                  "(never opened here)" % (app_id, key)), []


def parse_probe(res, names):
    """{name: "set" | "unset"} when the probe answered for exactly `names`, else None."""
    if res.rc != 0:
        return None
    lines = (res.out or "").splitlines()
    if len(lines) != len(names):
        return None
    got = {}
    for line, name in zip(lines, names):
        if line not in ("%s=set" % name, "%s=unset" % name):
            return None
        got[name] = line.split("=", 1)[1]
    return got


def step_env_file(ctx, apply_it):
    path = ctx.conf["ENV_FILE"]
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise se.Blocked("CK-V2", "there is no env file at %s" % path)
    except OSError as exc:
        raise se.Unknown("could not look at %s (%s)" % (path, exc.strerror), "run it again")
    why = owned_plain(ctx, path, st) or (None if stat.S_ISREG(st.st_mode) else "is not a plain file")
    if not why and st.st_mode & 0o077:
        why = "can be read or written by others (mode %o): run `chmod 600 %s` yourself" % (
            st.st_mode & 0o777, path)
    if why:
        raise se.SetupError("%s %s" % (path, why))
    names = [n for n in (ctx.conf["LINEAR_KEY_ENV"], ctx.conf["AGENT_TOKEN_ENV"]) if n]
    if ctx.agent:
        raise se.Unknown("NOT MEASURED — this is an agent environment (%s set), and the env file "
                         "is never read under a model. A person running the same command "
                         "measures it" % ", ".join(ctx.agent), "run the same command in your own terminal")
    res = ctx.runner.read(["/bin/sh", "-c", ENV_PROBE, "sh", path] + names, timeout=30,
                          env={"PATH": SYSTEM_PATH})
    got = parse_probe(res, names)
    if got is None:
        raise se.Unknown("a shell could not read %s (exit %d), so whether it sets %s is NOT "
                         "MEASURED" % (path, res.rc, ", ".join(names)), "check the file, then run it again")
    unset = [n for n in names if got[n] != "set"]
    if unset:
        raise se.Blocked("CK-V2", "%s does not set %s" % (path, ", ".join(unset)))
    return True, ("%s: yours, mode 600; it sets %s (a shell read it; no value came here)"
                  % (path, ", ".join(names))), []


def step_hold(ctx, apply_it):
    path, status = ctx.conf["HOLD_FILE"], ctx.conf["WATCHER_STATUS"]
    folder = os.path.dirname(path)
    try:
        os.stat(folder)
    except FileNotFoundError:
        raise se.SetupError("the hold's folder %s is not there. Install the watcher first "
                            "(docs/WATCHER.md), or name its hold file in HOLD_FILE" % folder)
    except OSError as exc:
        raise se.SetupError("the job cannot reach %s (%s), so it cannot read the watcher's hold"
                            % (folder, exc.strerror or type(exc).__name__))
    state = limits.hold_state(path)
    if state is None:
        raise se.SetupError("%s is inside a git working tree, so nothing can say there is no hold" % path)
    if state not in (hold.NONE, hold.HELD):
        raise se.SetupError("the job cannot read the watcher's hold %s: %s. Every pass with a switch "
                            "on would hold" % (path, hold.read(path)["why"]))
    seen = limits.watcher_state(status, ctx.clock())
    notes = [{"fresh": "the watcher's status file %s is fresh" % status,
              "stale": "the watcher's status file %s is stale, so approvals wait (watcher-stale) "
                       "until the watcher runs again" % status}.get(
        seen, "the watcher's status file %s could not be read and trusted from here, so approvals "
              "wait until it can" % status)]
    if state == hold.HELD:
        return True, ("%s: the job can read it, and a hold stands: no pass judges anything until you "
                      "clear it (python3 scripts/pipeline_hold.py show --hold-file %s)" % (path, path)), notes
    return True, "%s: the job can read it; no hold stands" % path, notes


def compose_config(ctx):
    c = ctx.conf
    return {"schema": vmerge.CONFIG_SCHEMA,
            "settings_file": ctx.settings_path,
            "ledger_file": c["LEDGER_FILE"],
            "merge": dict(vmerge.MERGE_DEFAULTS),
            "facts": {"linear_key_env": c["LINEAR_KEY_ENV"], "team_keys": se.split_list(c["TEAM_KEYS"]),
                      "queue_config": c["QUEUE_CONFIG"], "stage_e_ledger": c["STAGE_E_LEDGER"],
                      "snapshot_dir": c["SNAPSHOT_DIR"], "acceptance_ledger": c["ACCEPTANCE_LEDGER"],
                      "watcher_status": c["WATCHER_STATUS"], "hold_file": c["HOLD_FILE"]},
            "agent": {"agent_bin": ctx.bins.get("claude"), "agent_token_env": c["AGENT_TOKEN_ENV"]},
            "check": {"app_id": ctx.app_id, "key_file_env": KEY_ENV}}


def read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def step_config(ctx, apply_it):
    path, doc = ctx.config_path, compose_config(ctx)
    _cfg, bad = vmerge.config(doc)
    if bad:
        raise se.SetupError("the job's own loader would refuse the config: %s" % "; ".join(bad[:3]))
    have = read_json(path)
    if have == doc:
        return True, "%s: current; it names the hold %s and the ledger %s" % (
            path, doc["facts"]["hold_file"], doc["ledger_file"]), []
    why = "absent" if not os.path.lexists(path) else "it differs in %s" % ", ".join(
        sorted(k for k in doc if not isinstance(have, dict) or have.get(k) != doc[k]) or ["its shape"])
    if not apply_it:
        return False, "would write %s (%s)" % (path, why), []
    text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    ctx.runner.do("write the job's config", path, lambda: write_text(path, text, 0o600))
    _back, bad = vmerge.load_config(path)
    if bad or read_json(path) != doc:
        raise se.SetupError("%s was written, and the job's own loader does not read it back as "
                            "written: %s" % (path, "; ".join(bad[:2]) or "it differs"))
    return False, "wrote %s (%s), mode 600; read back by the job's own loader" % (path, why), []


def read_plist(path):
    """The job file as a dict, None when there is none. One that cannot be read raises."""
    try:
        with open(path, "rb") as fh:
            return plistlib.load(fh)
    except FileNotFoundError:
        return None
    except Exception as exc:                                       # noqa: BLE001
        raise se.Unknown("%s is there and could not be read as a job file (%s)"
                         % (path, type(exc).__name__), "look at it, then run the same command again")


def step_job(ctx, apply_it):
    path, want = ctx.plist_path, render_plist(ctx)
    have = read_plist(path)
    if have is not None and not ours(have):
        raise se.SetupError("%s holds a job that runs something else. It is never replaced: pick a "
                            "JOB_LABEL nothing else uses" % path)
    if have == want:
        return True, "%s: current" % path, []
    why = "absent" if have is None else "differs"
    if not apply_it:
        return False, "would write %s (%s)" % (path, why), []
    folder = os.path.dirname(path)
    ctx.runner.do("make %s" % folder, folder, lambda: os.makedirs(folder, exist_ok=True))
    text = plistlib.dumps(want).decode("utf-8")
    ctx.runner.do("write the job file", path, lambda: write_text(path, text, 0o644))
    ctx.plist_changed = True
    if launchd_holds(ctx)[0]:
        return False, ("wrote %s (%s). The job is loaded: the next row compares what launchd holds"
                       % (path, why)), []
    if not label_disabled(ctx):
        disable(ctx)
    return False, ("wrote %s (%s), mode 644: not loaded, and disabled, so a new login does not start "
                   "it before the next row decides" % (path, why)), []


def rehearse(ctx):
    """The job's own command, once, with --dry-run on each pass: (its lines, made safe; its exit).
    What a real first pass would decide is counted from its `[dry-run]` lines into ctx.first_pass."""
    plist = render_plist(ctx)
    res = ctx.runner.read(["/bin/sh", "-c", job_line(ctx, dry=True)], timeout=REHEARSAL_TIMEOUT,
                          env=plist["EnvironmentVariables"])
    lines = [l for l in clean((res.out or "") + (res.err or "")).splitlines() if l.strip()]
    ctx.first_pass = (sum(1 for l in lines if "[dry-run]" in l), res.rc)
    say("")
    say("  The rehearsal: the job's own command, with --dry-run, as you. It wrote nothing.")
    for line in lines[-20:]:
        say("    | " + line[:160])
    return lines, res.rc


def beat_mark(ctx):
    """Which last-pass file is there now: each pass writes a new one by a rename, so a pass that
    finished after this mark leaves another. None when there is none."""
    try:
        st = os.stat(ctx.at(BEAT_NAME))
    except OSError:
        return None
    return (st.st_ino, st.st_mtime_ns, st.st_size)


def await_beat(ctx, before):
    """The last-pass file of a pass that finished after `before` was taken, or None in time."""
    polls = max(1, int(FIRST_PASS_WAIT_SECONDS / FIRST_PASS_POLL_SECONDS) + 1)
    for n in range(polls):
        mark = beat_mark(ctx)
        if mark is not None and mark != before:
            beat, _why = read_beat(ctx)
            if beat:
                return beat
        if n < polls - 1:
            ctx.pause(FIRST_PASS_POLL_SECONDS)
    return None


def switch_on(ctx, what, reload_it):
    """Load the job, then wait for its first pass and judge it. A pass that has not finished in
    time is NOT MEASURED, never "on and working"."""
    since_bytes, before = log_size(ctx), beat_mark(ctx)
    load_job(ctx, reload_it=reload_it)
    record_on(ctx, ctx.command)
    say("")
    say("  %s. Waiting for its first pass, which starts at load: up to %d s"
        % (what, FIRST_PASS_WAIT_SECONDS))
    beat = await_beat(ctx, before)
    if beat is None:
        raise se.Unknown("%s; launchd holds it, and no pass of it finished within %d s, so whether "
                         "its passes work is NOT MEASURED" % (what, FIRST_PASS_WAIT_SECONDS),
                         "read %s, then run the same command again" % ctx.at(LOG_NAME))
    said = last_said(ctx, since_bytes)
    if beat["exit"] != 0:
        raise se.SetupError("%s; its first pass exited %d and said: %s" % (what, beat["exit"], said))
    return False, "%s. Its first pass exited 0 and said: %s" % (what, said), []


def judge_last_pass(ctx, apply_it):
    beat, why = read_beat(ctx)
    if beat is None:
        raise se.Unknown("launchd holds %s, and %s" % (ctx.target, why),
                         "wait one interval, then run the same command again")
    age = ctx.clock() - beat["at"]
    limit = 2 * int(ctx.conf["INTERVAL_SECONDS"]) + STALE_EXTRA_SECONDS
    if age < -CLOCK_SKEW_SECONDS:
        raise se.Unknown("its last-pass file is dated %d s ahead of this clock" % -age, "run it again")
    if age > limit:
        raise se.SetupError("launchd holds %s, and its last pass finished %d s ago, more than two "
                            "intervals: its passes are not running. Read %s"
                            % (ctx.target, age, ctx.at(LOG_NAME)))
    said = last_said(ctx)
    if beat["exit"] != 0:
        raise se.SetupError("its last pass, %d s ago, exited %d and said: %s" % (age, beat["exit"], said))
    if apply_it:
        record_on(ctx, "seen loaded")
    return True, ("loaded as you, every %s s; its last pass, %d s ago, exited 0 and said: %s"
                  % (ctx.conf["INTERVAL_SECONDS"], max(0, age), said)), []


def step_enable(ctx, apply_it):
    loaded, text = launchd_holds(ctx)
    want = render_plist(ctx)
    if loaded:
        running = ns.loaded_job(text)
        if running is None:
            raise se.Unknown("launchd holds %s, and its print shows neither the job's arguments nor "
                             "its interval" % ctx.target, "read it yourself: launchctl print %s" % ctx.target)
        stale = None
        if running["arguments"] and running["arguments"] != want["ProgramArguments"]:
            stale = "launchd holds an older command than the job file's"
        elif running["interval"] is not None and running["interval"] != want["StartInterval"]:
            stale = "launchd runs it every %s s, and the job file says %s s" % (
                running["interval"], want["StartInterval"])
        elif ctx.plist_changed:
            stale = "the job file was rewritten in this pass"
        elif not apply_it and read_plist(ctx.plist_path) != want:
            stale = "the job file is about to be rewritten"
        if stale:
            if not apply_it:
                return False, "would reload it: %s" % stale, []
            return switch_on(ctx, "reloaded it, because %s" % stale, reload_it=True)
        return judge_last_pass(ctx, apply_it)
    if was_on(ctx):
        raise se.Blocked("CK-V3", "%s is not loaded, and it was on before: you paused it. Neither "
                                  "`run` nor `apply` switches a paused job back on" % ctx.target)
    ceiling = ctx.ceiling
    if not apply_it:
        if ceiling is None:
            raise se.Unknown("its first-pass ceiling could not be read (%s), so `apply` would leave "
                             "it off" % ctx.ceiling_why,
                             "fix pipeline-parts.json in this checkout, then run the same command again")
        return False, ("would switch it on if its first pass decides at most %d thing%s, its "
                       "ceiling, and leave it off above that" % (ceiling, "" if ceiling == 1 else "s")), []
    if not label_disabled(ctx):
        disable(ctx)
    if ceiling is None:
        ctx.left_off = "left off: its first-pass ceiling could not be read"
        raise se.Unknown("%s (%s). It is installed and disabled, so a new login does not start it"
                         % (ctx.left_off, ctx.ceiling_why),
                         "fix pipeline-parts.json in this checkout, then run the same command again")
    _lines, rc = rehearse(ctx)
    count = ctx.first_pass[0]
    if rc != 0:
        ctx.left_off = ("left off: its rehearsal exited %d, so what its first pass would decide is "
                        "not known" % rc)
    elif count > ceiling:
        ctx.left_off = ("left off: its first pass would decide %d thing%s, above its ceiling of %d"
                        % (count, "" if count == 1 else "s", ceiling))
    if ctx.left_off:
        raise se.Blocked("CK-V3", "%s. It is installed and disabled, so a new login does not start "
                                  "it. Read the rehearsal's lines above; card CK-V3 switches it on "
                                  "yourself" % ctx.left_off)
    return switch_on(ctx, "switched it on: its first pass would decide %d thing%s, within its "
                          "ceiling of %d" % (count, "" if count == 1 else "s", ceiling), reload_it=False)


# (id, the rows it needs to hold first, what it is, the step)
STEPS = (
    ("preflight", (), "macOS, a main checkout, and the programs the job runs", step_preflight),
    ("folder", (), "~/.validator, yours alone", step_folder),
    ("settings", ("folder",), "the switches: written once, every switch off", step_settings),
    ("ledger", ("folder",), "the ledger's folder, and the ledger by its own reader", step_ledger),
    ("app", (), "the app's id, and its key file by lstat", step_app),
    ("env-file", (), "the env file sets each name, judged in a shell", step_env_file),
    ("hold", (), "the watcher's hold, read as the job reads it", step_hold),
    ("config", ("preflight", "settings", "ledger", "app"), "the job's config, by its own loader", step_config),
    ("job", ("config", "env-file"), "the LaunchAgent's file", step_job),
    ("enable", ("job", "hold"), "loaded, current, and its last pass exited 0", step_enable),
)


def run_steps(ctx, apply_it):
    """(code, rows, notes). A look measures every row; a row whose needs did not hold is SKIPPED.
    Applying stops at the first row that does not hold: a card, a failure or NOT MEASURED."""
    ctx.apply_it = apply_it
    ctx.runner.dry = not apply_it
    ctx.plist_changed = False
    ctx.row_cards = {}
    rows, notes, outcome = [], [], {}
    for sid, needs, _what, fn in STEPS:
        held = [n for n in needs if outcome.get(n) not in HOLDING]
        if held:
            rows.append((sid, SKIPPED, "not measured: it waits for `%s`" % held[0]))
            outcome[sid] = SKIPPED
            continue
        try:
            ok, detail, more = fn(ctx, apply_it)
            got = ALREADY_DONE if ok else (DONE if apply_it else WOULD_CHANGE)
            notes.extend((sid, n) for n in more)
        except se.Blocked as exc:
            got, detail = BLOCKED, exc.extra or CARDS[exc.card_id]["title"]
            ctx.row_cards[sid] = exc.card_id
        except se.Unknown as exc:
            got, detail = UNKNOWN, exc.what
            if exc.remedy:
                notes.append((sid, "What clears it: " + exc.remedy))
        except se.SetupError as exc:
            got, detail = FAILED, str(exc)
        # A detail is this file's own words and the conf's paths, which hold no credential shape
        # (parse_conf refuses one). What a child said is made safe where it is read: the
        # rehearsal, the log, launchd's answers. The env probe's words are never printed.
        rows.append((sid, got, detail))
        outcome[sid] = got
        if apply_it and got not in HOLDING:
            break
    return exit_of(rows), rows, notes


def exit_of(rows):
    seen = set(o for _s, o, _d in rows)
    if FAILED in seen:
        return EX_FAILED
    if UNKNOWN in seen:
        return EX_UNKNOWN
    if seen & set((BLOCKED, WOULD_CHANGE, SKIPPED)):
        return EX_BLOCKED
    return EX_OK


# --------------------------------------------------------------------------- #
# The commands
# --------------------------------------------------------------------------- #
def banner(ctx, title):
    say(title)
    say("  conf          %s" % ctx.conf.get("__source__", DEFAULT_CONF))
    say("  under         your own login (uid %d): no account is made, and nothing runs sudo" % ctx.uid)
    say("  job           %s (%s)" % (ctx.target, ctx.plist_path))
    say("  ceiling       %s" % ("its first pass may decide at most %d thing%s before it is switched "
                                "on" % (ctx.ceiling, "" if ctx.ceiling == 1 else "s")
                                if ctx.ceiling is not None else "not read: " + ctx.ceiling_why))


def name_cards(ctx, rows):
    for sid, outcome, _d in rows:
        card = ctx.row_cards.get(sid) if outcome == BLOCKED else None
        if card:
            say("Step `%s` waits on you: card %s, %s. Print it with:" % (sid, card, CARDS[card]["title"]))
            say("    python3 %s card %s%s" % (os.path.relpath(__file__), card, ctx.flags))


def cmd_verify(ctx):
    """The look: every row measured; nothing changed, recorded or asked."""
    ctx.command = "verify"
    banner(ctx, "Validator install, looked at: nothing is changed, recorded or asked.")
    writes = len(ctx.runner.writes)
    code, rows, notes = run_steps(ctx, apply_it=False)
    se._print_rows(rows, notes)
    say("")
    name_cards(ctx, rows)
    say({EX_OK: "Nothing to change: every row holds.",
         EX_BLOCKED: "Waiting: the rows above say what would change, or what is yours.",
         EX_UNKNOWN: "NOT MEASURED: a row could not look. It is never read as up to date.",
         EX_FAILED: "FAILED: the first FAILED row above says why."}[code])
    if len(ctx.runner.writes) > writes:
        say("BUG: a look recorded %d write(s)." % (len(ctx.runner.writes) - writes))
        return EX_FAILED
    return code


def show_plan(ctx, rows):
    say("")
    say("What it will change, in this order:")
    for sid, outcome, detail in rows:
        if outcome == WOULD_CHANGE:
            say("  %-10s %s" % (sid, detail))
    if any(s == "settings" and o == WOULD_CHANGE for s, o, _d in rows):
        say("")
        say("The settings file it writes, every switch off:")
        for line in json.dumps(all_off_doc(), indent=2).splitlines():
            say("    " + line)
    say("")
    say("The job runs, every %s s, as you:" % ctx.conf["INTERVAL_SECONDS"])
    say("    /bin/sh -c " + job_line(ctx)[:400])
    say("It switches the job on only when a rehearsal shows its first pass would decide nothing "
        "more than its ceiling. It never writes a settings file that is there.")


def cmd_run(ctx):
    """A person's: the look, the plan, one typed yes, each row applied, then the look again."""
    se_refuse(ctx, "run")
    ctx.command = "run"
    banner(ctx, "Validator install: it looks, shows, asks one typed yes, applies, and looks again.")
    code, rows, notes = run_steps(ctx, apply_it=False)
    se._print_rows(rows, notes)
    if code in (EX_FAILED, EX_UNKNOWN):
        say("")
        say("Nothing was changed. Clear the %s row above first, then run this again."
            % ("FAILED" if code == EX_FAILED else "NOT MEASURED"))
        return code
    if not any(o == WOULD_CHANGE for _s, o, _d in rows):
        say("")
        name_cards(ctx, rows)
        say("Nothing to change%s." % (": every row holds" if code == EX_OK else
                                      "; what is left is yours, named above"))
        return code
    show_plan(ctx, rows)
    if not ctx.tty or ctx.ask is None:
        say("")
        say("This asks for a typed yes at a terminal, and there is none. Nothing was changed. A "
            "program uses `apply` instead.")
        return EX_USAGE
    if (ctx.ask("Type yes to apply these changes: ") or "").strip() != "yes":
        say("Not confirmed. Nothing was changed.")
        return EX_BLOCKED
    code, rows, notes = run_steps(ctx, apply_it=True)
    se._print_rows(rows, notes)
    if code != EX_OK:
        say("")
        if rows and rows[-1][1] == BLOCKED:
            card = ctx.row_cards.get(rows[-1][0])
            for line in card_lines(card, ctx.card_values()):
                say(line)
            say("Do that, then run the same command again: it checks your work and carries on.")
        return code
    say("")
    say("Checked again:")
    code, rows, notes = run_steps(ctx, apply_it=False)
    se._print_rows(rows, notes)
    say("")
    say("DONE: every row holds. Every switch you have not turned on is off; nothing is merged, "
        "approved or labelled by this installer." if code == EX_OK else
        "Applied; the look above says what is left.")
    return code


def cmd_check(ctx):
    """For a program: the look, with nothing asked. (code, rows)."""
    ctx.command = "check"
    banner(ctx, "Validator check, for a program: nothing is changed, recorded or asked. The rows go "
                "to standard output as one JSON document.")
    writes = len(ctx.runner.writes)
    code, rows, notes = run_steps(ctx, apply_it=False)
    se._print_rows(rows, notes)
    if len(ctx.runner.writes) > writes:
        say("BUG: check recorded %d write(s)." % (len(ctx.runner.writes) - writes))
        return EX_FAILED, rows
    return code, rows


def cmd_apply(ctx):
    """For a program, after the person's consent: each row applied in order, nothing asked. The
    first row a person must clear stops it, named by its card. (code, rows)."""
    se_refuse(ctx, "apply")
    ctx.command = "apply"
    banner(ctx, "Validator apply: the answers came through a pipe, after your consent. It asks nothing.")
    code, rows, notes = run_steps(ctx, apply_it=True)
    se._print_rows(rows, notes)
    say("")
    if all(o == ALREADY_DONE for _s, o, _d in rows):
        say("Nothing to change: every row holds.")
    name_cards(ctx, rows)
    return code, rows


def se_refuse(ctx, action):
    if ctx.agent:
        raise se.Refusal(refusal_words(action, ctx.agent))


def refusal_words(action, found):
    return ("REFUSED: `%s` changes what supervises you, and this is an agent environment (%s set).\n"
            "  A session that installs its own supervisor, or turns its own approvals on or off, is\n"
            "  what this refusal exists to stop. There is no override flag.\n"
            "  A PERSON runs it, in a terminal:  python3 %s %s\n"
            "  Read-only meanwhile:  verify | run --dry-run | check | card <CK-id>"
            % (action, ", ".join(found), os.path.relpath(__file__), action))


def cmd_off(home, env, ask, tty):
    """Every switch off at once: each `approve` and `allow` switch false, after one typed yes, read
    back. It reads no conf: the settings file is always ~/.validator/settings.json."""
    found = [m for m in AGENT_ENV_MARKERS if m in env]
    if found:
        say(refusal_words("off", found))
        return EX_REFUSED
    path = os.path.join(home, FOLDER, SETTINGS_NAME)
    got = vsettings.load(path)
    if got["state"] == vsettings.ABSENT:
        say("Nothing to do: there is no settings file at %s, so every switch is off." % path)
        return EX_OK
    if got["state"] == vsettings.READ:
        on = switches_on(got)
        if not on:
            say("Nothing to do: every switch in %s is already off." % path)
            return EX_OK
        doc = {"schema": vsettings.SETTINGS_SCHEMA, "approve": dict(got["approve"]),
               "limits": dict(got["limits"]), "allow": dict(got["allow"]),
               vsettings.LOGIN_KEY: got[vsettings.LOGIN_KEY],
               vsettings.REPOS_KEY: list(got[vsettings.REPOS_KEY])}
        for k in vsettings.APPROVE:
            doc["approve"][k] = False
        for k in vsettings.ALLOW:
            doc["allow"][k] = False
        say("These switches go off: %s." % ", ".join(on))
        say("Its limits, Stage E's login and the repository list stay as they are. With every switch "
            "off, they approve nothing.")
    else:
        doc = all_off_doc()
        say("The settings file %s is not obeyed (%s), so every switch already reads off." % (path, got["why"]))
        say("If it is fixed, its switches count again. This replaces it with one that has every "
            "switch off. What it held now is gone.")
    if not tty or ask is None:
        say("This asks for a typed yes at a terminal, and there is none. Nothing was changed.")
        return EX_USAGE
    if (ask("Type yes to turn every switch off: ") or "").strip() != "yes":
        say("Not confirmed. Nothing was changed.")
        return EX_BLOCKED
    try:
        vapp.write_settings(path, doc)
    except (vapp.StepFailed, OSError) as exc:
        say("FAILED: %s was not changed: %s" % (path, exc))
        return EX_FAILED
    back = vsettings.load(path)
    if back["state"] != vsettings.READ or switches_on(back):
        say("FAILED: %s did not read back as every switch off: %s" % (path, back["why"]))
        return EX_FAILED
    say("Done: every switch in %s is off, read back by the settings' own loader." % path)
    say("The job keeps running, and says OFF on every pass.")
    if got["state"] == vsettings.READ and got["approve"]["merges"]:
        say("A pull request already in flight may still merge: GitHub's own auto-merge stays on for "
            "it. To turn that off too, for each project:")
        for repo in got[vsettings.REPOS_KEY] or ["OWNER/NAME"]:
            say("    python3 scripts/pipeline_validator_app.py merges off --repo %s" % repo)
    return EX_OK


def rows_document(command, code, rows=(), ctx=None, why=None):
    """The one JSON document `check` and `apply` print on stdout, whatever their exit: each row's
    step, outcome word, detail (made safe, as printed) and, when blocked, its card."""
    cards = ctx.row_cards if ctx is not None else {}
    return {"schema": ROWS_SCHEMA, "command": command, "exit": code,
            "rows": [{"step": sid, "outcome": outcome, "detail": (detail or "")[:400],
                      "card": cards.get(sid) if outcome == BLOCKED else None}
                     for sid, outcome, detail in rows],
            "ceiling": ctx.ceiling if ctx is not None else None,
            "first_pass": ctx.first_pass[0] if ctx is not None and ctx.first_pass else None,
            "left_off": ctx.left_off if ctx is not None else None,
            "why": why}


def machine_command(args, stdin, env, make):
    """`check` and `apply`: every human word on stderr, and ONE JSON document on stdout, on every
    exit past argument parsing, a refusal included."""
    real_out = sys.stdout
    sys.stdout = sys.stderr
    box = {"ctx": None}
    try:
        code, rows, why = _machine(args, stdin, env, make, box)
    except Exception as exc:                                      # noqa: BLE001
        say("BUG: %s raised %s: %s" % (args.command, type(exc).__name__, clean(str(exc))[:300]))
        code, rows, why = EX_FAILED, [], "unexpected %s" % type(exc).__name__
    finally:
        sys.stdout = real_out
    real_out.write(json.dumps(rows_document(args.command, code, rows, box["ctx"], why),
                              sort_keys=True) + "\n")
    real_out.flush()
    return code


def _machine(args, stdin, env, make, box):
    """(code, rows, why). The order is the contract: root, then an agent (`apply`), then standard
    input and its consent (`apply`), then the conf. A "no" touches nothing."""
    import pipeline_root_guard as rg   # KIT-693: the one refusal every installer shares
    if rg.refusal():
        say(rg.MESSAGE)
        return EX_REFUSED, [], "refused: run as root"
    found = [m for m in AGENT_ENV_MARKERS if m in env]
    if args.command == "apply" and found:
        say(refusal_words("apply", found))
        return EX_REFUSED, [], "refused: an agent environment"
    if args.dry_run:
        say("FAILED: %s takes no --dry-run: `check` is the read-only one. Nothing was done." % args.command)
        return EX_USAGE, [], "%s takes no --dry-run" % args.command
    if args.command == "apply":
        answers, why = ns.read_answers(stdin, schema=ANSWERS_SCHEMA, keys=ANSWERS_KEYS)
        if answers is None:
            say("FAILED: %s. Nothing was changed." % why)
            return EX_USAGE, [], why
        if answers.get("consent") != "yes":
            say("NO CONSENT: the answers do not say yes, so nothing was changed or recorded.")
            return EX_BLOCKED, [], "no consent: the answers do not say yes"
    ctx, errors = make(args, env)
    if errors:
        say("Your conf has %d problem(s). Every one of them, in one pass:" % len(errors))
        for e in errors:
            say("  - " + e)
        return EX_USAGE, [], "%d problem(s) in the conf, the first: %s" % (len(errors), errors[0])
    box["ctx"] = ctx
    try:
        if args.command == "check":
            code, rows = cmd_check(ctx)
        else:
            code, rows = cmd_apply(ctx)
    except se.Refusal as exc:
        say(str(exc))
        return EX_REFUSED, [], "refused: an agent environment"
    return code, rows, None


def make_ctx(args, env, home=None, runner=None, **kw):
    """(ctx, conf errors) for this command line. The battery hands in its own home and runner."""
    home = home or account_home()
    conf, errors = load_conf(os.path.abspath(args.conf), home)
    if errors:
        return None, errors
    ctx = Ctx(conf, home, runner or Runner(), env, **kw)
    ctx.flags = "" if args.conf == DEFAULT_CONF else " --conf " + shlex.quote(args.conf)
    ctx.ceiling, ctx.ceiling_why = ns.first_pass_ceiling(installer=__file__)
    return ctx, []


def build_parser():
    p = argparse.ArgumentParser(prog="pipeline_validator_setup.py",
                                description="Install the validator's job under your own login, "
                                            "with every switch off.")
    p.add_argument("command", nargs="?", default="verify",
                   choices=["run", "verify", "off", "card", "check", "apply"])
    p.add_argument("target", nargs="?", help="a CK-id for `card`")
    p.add_argument("--conf", default=DEFAULT_CONF)
    p.add_argument("--dry-run", action="store_true", help="`run` only: look, change nothing")
    p.add_argument("--selftest", action="store_true")
    return p


def main(argv=None, stdin=None, env=None, make=None, ask=None, tty=None, home=None):
    args = build_parser().parse_args(argv)
    if args.selftest:
        from scratch_run import contained   # KIT-450: its temp folders go when it ends
        import pipeline_root_guard as _rg   # KIT-693: the root case ends every installer's own selftest
        return contained(selftest, "validator-setup-selftest-run-") or _rg.root_case_rc(main, ["run"])
    env = dict(os.environ) if env is None else env
    make = make or make_ctx
    if args.command in ("check", "apply"):
        return machine_command(args, stdin, env, make)
    import pipeline_root_guard as _rg   # KIT-693: the one refusal every installer shares
    if _rg.refusal():
        say(_rg.MESSAGE)
        return EX_REFUSED
    found = [m for m in AGENT_ENV_MARKERS if m in env]
    mutating = args.command == "off" or (args.command == "run" and not args.dry_run)
    if mutating and found:
        say(refusal_words(args.command, found))
        return EX_REFUSED
    tty = (sys.stdin.isatty() and sys.stdout.isatty()) if tty is None else tty
    ask = ask or (lambda prompt: input(prompt))
    if args.command == "off":
        return cmd_off(home or account_home(), env, ask, tty)
    if args.command == "card":
        if args.target not in CARDS:
            say("FAILED: name a card: %s" % ", ".join(sorted(CARDS)))
            return EX_USAGE
        ctx, errors = make(args, env)
        values = ctx.card_values() if ctx else dict(CONF_DEFAULTS, PLIST="~/Library/LaunchAgents/"
                                                     "local.validator.plist", NAME_LINES="    NAME=<value>")
        if errors:
            say("(%s could not be read cleanly, so the card shows the example's values: %s)"
                % (args.conf, errors[0]))
        for line in card_lines(args.target, values):
            say(line)
        return EX_OK
    ctx, errors = make(args, env)
    if errors:
        say("Your conf has %d problem(s). Every one of them, in one pass:" % len(errors))
        for e in errors:
            say("  - " + e)
        return EX_USAGE
    ctx.tty, ctx.ask = tty, ask
    try:
        if args.command == "verify" or (args.command == "run" and args.dry_run):
            return cmd_verify(ctx)
        return cmd_run(ctx)
    except se.Refusal as exc:
        say(str(exc))
        return EX_REFUSED


# ======================================================================================
# SELFTEST: nothing below the line above is run by a caller, and the source scan stops there.
# ======================================================================================
class _FakeLaunchd(Runner):
    """Every `launchctl` call answered by a launchd kept here; `/bin/sh` runs for real, so the env
    probe, the rehearsal and the job's own shell line are the real ones. A load runs the job's
    line once, as launchd would: its arguments, its environment and nothing else, its output
    appended to its log. Any other program is a failure of the case: no real launchctl, no sudo."""

    def __init__(self):
        Runner.__init__(self, dry=True)
        self.held = None            # the job file launchd holds, as a dict
        self.disabled = {}          # label -> True when disabled
        self.calls = []             # every launchctl argv, in order
        self.boot_answer = None     # (rc, err) a load answers instead of loading
        self.run_at_load = True
        self.shells = []            # every /bin/sh argv

    def verbs(self, *only):
        return [a[1] for a in self.calls if not only or a[1] in only]

    def _exec(self, argv, timeout, env):
        if argv[0] == "launchctl":
            self.calls.append(list(argv))
            return self._launchctl(argv)
        if argv[0] == "/bin/sh":
            self.shells.append(list(argv))
            return Runner._exec(self, argv, timeout, env)
        raise AssertionError("the fake launchd has nothing for %r" % argv[0])

    def _launchctl(self, argv):
        verb = argv[1]
        if verb == "print":
            if self.held is None:
                return se.Result(113, "", "Could not find service in domain for user gui")
            d = self.held
            text = ("%s = {\n\tstate = not running\n\targuments = {\n%s\t}\n\trun interval = %d "
                    "seconds\n}\n" % (argv[2], "".join("\t\t%s\n" % a for a in d["ProgramArguments"]),
                                      d["StartInterval"]))
            return se.Result(0, text)
        if verb == "print-disabled":
            return se.Result(0, "disabled services = {\n%s}\n" % "".join(
                '\t"%s" => %s\n' % (k, "disabled" if v else "enabled")
                for k, v in sorted(self.disabled.items())))
        if verb in ("disable", "enable"):
            self.disabled[argv[2].split("/")[-1]] = verb == "disable"
            return se.Result(0)
        if verb == "bootout":
            self.held = None
            return se.Result(0)
        if verb == "bootstrap":
            if self.boot_answer:
                return se.Result(*self.boot_answer)
            if self.disabled.get(os.path.basename(argv[3])[:-len(".plist")]):
                return se.Result(5, "", "Bootstrap failed: 5: Input/output error")
            with open(argv[3], "rb") as fh:
                self.held = plistlib.load(fh)
            if self.run_at_load:
                self.run_once()
            return se.Result(0)
        return se.Result(1, "", "the fake launchd has no verb %s" % verb)

    def run_once(self):
        d = self.held
        with open(d["StandardOutPath"], "ab") as log:
            subprocess.run(d["ProgramArguments"], env=d["EnvironmentVariables"], stdout=log,
                           stderr=log, stdin=subprocess.DEVNULL, cwd="/", timeout=300)


def selftest():
    import ast
    import contextlib
    import io
    import tempfile
    import pipeline_root_guard as rg
    failures, total = [], [0]

    def ok(name, cond, detail=""):
        total[0] += 1
        if not cond:
            failures.append("FAIL %s%s" % (name, (": " + str(detail)[:900]) if detail != "" else ""))

    def quiet(fn):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            value = fn()
        return value, out.getvalue(), err.getvalue()

    def pipe_of(text):
        rfd, wfd = os.pipe()
        os.write(wfd, text.encode("utf-8"))
        os.close(wfd)
        return os.fdopen(rfd, "r", encoding="utf-8")

    def by_step(rows):
        return dict((s, o) for s, o, _d in rows)

    def detail(rows, sid):
        return dict((s, d) for s, _o, d in rows).get(sid, "")

    root = tempfile.mkdtemp(prefix="validator-setup-")
    os.chmod(root, 0o700)
    # Values that must never be printed, logged or kept anywhere but their own files. No shape of
    # a real credential, so no scan can be what hides them: the design must.
    secret_a = "SELFTEST-TRACKER-VALUE-" + "A" * 12
    secret_b = "SELFTEST-SETUP-TOKEN-" + "B" * 12
    key_mark = "SELFTEST-KEY-MATERIAL-NEVER-OPENED"
    answers = json.dumps({"schema": ANSWERS_SCHEMA, "consent": "yes"})

    def write(path, text, mode=0o600):
        folder = os.path.dirname(path)
        if not os.path.isdir(folder):
            os.makedirs(folder, mode=0o700)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(path, mode)

    def place(name, stub=None, app=True, env_text=None, held=False, conf_extra="", vfolder=True):
        """A home the validator's app script already set up (its folder, its key, its record), an
        env file, the watcher's folder, two stand-in programs, a main checkout whose scripts are
        the real ones (or a stub merge pass), and a conf naming all of them."""
        base = os.path.join(root, name)
        home = os.path.join(base, "home")
        os.makedirs(home, mode=0o700)
        keydir = os.path.join(home, FOLDER) if vfolder else os.path.join(home, ".keys")
        key = os.path.join(keydir, "app.pem")
        write(key, key_mark + "\n", 0o000)      # mode 000: anything that opened it would fail
        if app:
            write(os.path.join(home, ".validator-app", "state.json"), json.dumps(
                {"schema": vapp.SCHEMA, "app_id": 4242, "slug": "validator-acme", "key_file": key,
                 "installation_ids": {}}))
        envf = os.path.join(home, ".validator-env")
        if env_text is not False:
            write(envf, env_text if env_text is not None else
                  "VALIDATOR_LINEAR_API_KEY=%s\nCLAUDE_WRITER_OAUTH_TOKEN=%s\n" % (secret_a, secret_b))
        watcher = os.path.join(base, "watcher")
        os.makedirs(watcher, mode=0o755)
        hold_file = os.path.join(watcher, "hold.json")
        if held:
            hold.write(hold_file, [("spend", "spend-day", "acme/app#1")], time.time)
        bins = os.path.join(base, "bin")
        for tool in ("claude", "gh"):
            write(os.path.join(bins, tool), "#!/bin/sh\nexit 0\n", 0o755)
        kit = os.path.join(base, "kit")
        os.makedirs(os.path.join(kit, ".git"))
        if stub is None:
            os.symlink(HERE, os.path.join(kit, "scripts"))
        else:
            n, rc = stub
            write(os.path.join(kit, "scripts", PASSES[0]), "import sys\n%ssys.exit(%d)\n" % (
                "".join("print('  [dry-run] would record approved for acme/app#%d at 0123456789ab')\n"
                        % (i + 1) for i in range(n)), rc), 0o644)
        conf = os.path.join(base, "validator.conf")
        write(conf, "HOLD_FILE=%s\nWATCHER_STATUS=%s\nAGENT_BIN=%s\nGH_BIN=%s\nPYTHON=%s\n"
              "ENV_FILE=%s\nAPP_KEY_FILE=%s\n%s" % (
                  hold_file, os.path.join(watcher, "watcher-status.json"),
                  os.path.join(bins, "claude"), os.path.join(bins, "gh"), sys.executable, envf, key,
                  conf_extra))
        return {"base": base, "home": home, "conf": conf, "kit": kit, "key": key, "env": envf,
                "hold": hold_file}

    def make_for(p, fake=None, ceiling=0):
        fake = fake if fake is not None else _FakeLaunchd()
        made = []

        def make(args, env):
            c, errors = make_ctx(args, env, home=p["home"], runner=fake, kit=p["kit"], platform="darwin",
                                 which=lambda _t: None, pause=lambda _s: None)
            if c is not None:
                c.ceiling = ceiling
                c.ceiling_why = None if ceiling is not None else "the parts list could not be read"
                made.append(c)
            return c, errors
        make.fake, make.made = fake, made
        return make

    def ctx_for(p, fake=None, ceiling=0, env=None, command="verify"):
        make = make_for(p, fake, ceiling)
        c, errors = make(build_parser().parse_args([command, "--conf", p["conf"]]),
                         {} if env is None else env)
        if errors:
            raise AssertionError(errors)
        return c, make.fake

    def files_of(p):
        """Every file under the case's folder, path -> bytes (the stand-in kit's scripts excepted)."""
        out = {}
        for top, dirs, names in os.walk(p["base"]):
            dirs[:] = [d for d in dirs if d != "scripts"]
            for n in names:
                path = os.path.join(top, n)
                try:
                    with open(path, "rb") as fh:
                        out[path] = fh.read()
                except OSError:
                    out[path] = None
        return out

    def machine(p, argv, stdin=None, env=None, make=None):
        make = make or make_for(p)
        code, out, err = quiet(lambda: main(argv + ["--conf", p["conf"]], stdin=stdin,
                                            env={} if env is None else env, make=make))
        try:
            doc = json.loads(out)
        except ValueError:
            doc = None
        return code, doc, out, err, make

    # ── 1. The conf: every fault at once, by key, never a value ─────────────────────────────
    with open(os.path.join(KIT, "validator.conf.example"), encoding="utf-8") as fh:
        example_text = fh.read()
    values, errs = parse_conf(example_text)
    conf, more = validate_conf(values, "/home/you")
    ok("conf: the committed example parses and is valid as it stands", errs == [] and more == [],
       errs + more)
    ok("conf: no key of it is a switch: the switches live only in the settings file",
       not [k for k in CONF_DEFAULTS for w in ("approve", "allow", "merges", "pieces", "plans")
            if w.upper() in k], sorted(CONF_DEFAULTS))
    shape = "gh" + "p_" + "x" * 36
    bad = ("JOB_LABEL=no-dots\nINTERVAL_SECONDS=5\nENV_FILE=relative/env\nLINEAR_KEY_ENV=PATH\n"
           "AGENT_TOKEN_ENV=lower\nHOLD_FILE=/home/you/hold.json\nTEAM_KEYS=eng\nNOPE=1\n"
           "GH_BIN=%s\nJOB_LABEL=a.b\n" % shape)
    values, errs = parse_conf(bad)
    _c, more = validate_conf(values, "/home/you")
    every = errs + more
    for needle in ("not a setting", "given twice", "CREDENTIAL SHAPE", "reverse-DNS",
                   "from 60 to 3600", "ENV_FILE must be an absolute path", "may not be PATH",
                   "AGENT_TOKEN_ENV must be the NAME", "outside your home", "TEAM_KEYS",
                   "WATCHER_STATUS is required"):
        ok("conf: every fault is named in one pass: %s" % needle,
           any(needle in e for e in every), every)
    ok("conf: no fault quotes a value", shape not in " ".join(every), every)
    values, errs = parse_conf("HOLD_FILE=/w/h.json\nWATCHER_STATUS=/w/h.json\n")
    _c, more = validate_conf(values, "/home/you")
    ok("conf: two keys naming one file is a fault", any("name the same file" in e for e in more), more)

    # ── 2. The settings a fresh install writes, and the ceiling it is held to ───────────────
    with open(os.path.join(KIT, "validator-settings.example.json"), encoding="utf-8") as fh:
        ok("settings: a fresh install writes validator-settings.example.json's own content",
           json.load(fh) == all_off_doc())
    real = ns.first_pass_ceiling(installer=__file__)
    ok("ceiling: the parts list gives the validator's part a whole-number ceiling of 0",
       real == (0, None), real)

    # ── 3. A fresh install, applied: every switch off, the job on, its first pass said OFF ──
    p = place("fresh")
    before = files_of(p)
    code, doc, out, err, make = machine(p, ["apply"], stdin=pipe_of(answers))
    fake, c = make.fake, make.made[-1]
    rows = [(r["step"], r["outcome"], r["detail"]) for r in (doc or {}).get("rows") or []]
    got = by_step(rows)
    ok("apply: a fresh install exits 0, with every row holding",
       code == EX_OK and doc and doc["exit"] == EX_OK and doc["schema"] == ROWS_SCHEMA
       and [s for s, _o, _d in rows] == [s for s, _n, _w, _f in STEPS]
       and all(o in (DONE, ALREADY_DONE) for o in got.values()), "%s %s\n%s" % (code, got, err[-1500:]))
    s = vsettings.load(os.path.join(p["home"], FOLDER, SETTINGS_NAME))
    ok("apply: the settings file is written, and reads back as every switch off",
       got.get("settings") == DONE and s["state"] == vsettings.READ and not switches_on(s)
       and s[vsettings.REPOS_KEY] == [] and not any(s["limits"].values()), s)
    cfg, bad_cfg = vmerge.load_config(os.path.join(p["home"], FOLDER, CONFIG_NAME))
    ok("apply: the config holds the hold file and the ledger, and the job's own loader accepts it",
       not bad_cfg and cfg["facts"]["hold_file"] == p["hold"]
       and cfg["ledger_file"] == os.path.join(p["home"], FOLDER, "ledger.json")
       and cfg["check"] == {"app_id": 4242, "key_file_env": KEY_ENV}, bad_cfg or cfg)
    plist_path = os.path.join(p["home"], "Library", "LaunchAgents", "local.validator.plist")
    with open(plist_path, "rb") as fh:
        pl = plistlib.load(fh)
    ok("apply: one LaunchAgent in your own domain, every 300 s, its key file by path only",
       pl["Label"] == "local.validator" and pl["StartInterval"] == 300 and pl["RunAtLoad"] is True
       and pl["EnvironmentVariables"][KEY_ENV] == p["key"] and pl["ProgramArguments"][:2] == ["/bin/sh", "-c"]
       and all(a[2].startswith("gui/%d" % os.getuid()) for a in fake.calls if len(a) > 2
               and a[1] not in ("bootstrap", "print-disabled"))
       and not os.path.exists(os.path.join(p["home"], "Library", "LaunchDaemons")), pl)
    ok("apply: the job file is disabled when written, then enabled and loaded, and nothing else",
       fake.verbs("disable", "enable", "bootstrap", "bootout") == ["disable", "enable", "bootstrap"]
       and fake.held is not None and fake.disabled.get("local.validator") is False, fake.calls)
    with open(os.path.join(p["home"], FOLDER, LOG_NAME), encoding="utf-8") as fh:
        log = fh.read()
    beat, _why = read_beat(c)
    ok("apply: the job's first pass ran its real command and said every switch is off",
       "OFF: the merges switch is off" in log and beat and beat["exit"] == 0
       and "exited 0" in detail(rows, "enable") and c.first_pass == (0, 0), log + str(beat))
    state = read_state(c)
    ok("apply: it records once that the job was switched on, by apply",
       state and state["switched_on"]["by"] == "apply", state)
    ok("apply: no OS account, no sudo: the only programs were launchctl and /bin/sh",
       not [a for a in fake.reads + [w.get("argv") for w in fake.writes if w.get("argv")]
            if a and a[0] not in ("launchctl", "/bin/sh")], fake.reads[:3])

    # ── 4. No credential anywhere: printed, logged, kept, or in any command line ────────────
    after = files_of(p)
    leaked = [path for path, data in after.items() if data and path not in (p["env"], p["key"])
              and any(v.encode() in data for v in (secret_a, secret_b, key_mark))]
    argvs = [" ".join(a) for a in fake.reads + fake.calls + fake.shells]
    ok("secret: no value from the env file or the key reaches stdout, stderr or any file but its own",
       not leaked and not any(v in out + err for v in (secret_a, secret_b, key_mark)), leaked)
    ok("secret: no value is in any command line run, and the key file, mode 000, was left so",
       not [a for a in argvs if secret_a in a or secret_b in a or key_mark in a]
       and os.stat(p["key"]).st_mode & 0o777 == 0, argvs[:2])
    ok("secret: the env probe asked by NAME, and its shell ran with no environment of yours",
       any(a[2] == ENV_PROBE and a[-2:] == ["VALIDATOR_LINEAR_API_KEY", "CLAUDE_WRITER_OAUTH_TOKEN"]
           for a in fake.shells), fake.shells[:1])

    # ── 5. A second run changes nothing, and says so ─────────────────────────────────────────
    calls_before = len(fake.calls)
    code, doc, out, err, make2 = machine(p, ["apply"], stdin=pipe_of(answers), make=make_for(p, fake))
    ok("again: a second apply holds every row, writes nothing, loads nothing, and says so",
       code == EX_OK and all(r["outcome"] == ALREADY_DONE for r in doc["rows"])
       and files_of(p) == after and not [a for a in fake.calls[calls_before:]
                                         if a[1] in ("disable", "enable", "bootstrap", "bootout")]
       and "Nothing to change" in err, "%s %s\n%s" % (code, [r["outcome"] for r in doc["rows"]], err[-800:]))
    code, doc, out, err, _m = machine(p, ["check"], make=make_for(p, fake))
    ok("again: check after an install is up to date, exit 0", code == EX_OK and doc["exit"] == 0
       and all(r["outcome"] == ALREADY_DONE for r in doc["rows"]), doc)

    # ── 6. check: one JSON document, nothing changed, recorded or asked ─────────────────────
    q = place("check")
    snap = files_of(q)
    code, doc, out, err, make = machine(q, ["check"])
    outcomes = dict((r["step"], r["outcome"]) for r in doc["rows"]) if doc else {}
    ok("check: stdout is ONE JSON document, and every word went to stderr",
       doc is not None and out.count("\n") == 1 and doc["command"] == "check" and doc["exit"] == code
       and "Validator check" in err and "-- steps --" in err, out[:300])
    ok("check: a fresh Mac is work, exit 10: settings, config, job and the switch-on would change",
       code == EX_BLOCKED and all(outcomes.get(s) == WOULD_CHANGE for s in ("settings", "config", "job", "enable"))
       and "at most 0 things" in [r["detail"] for r in doc["rows"] if r["step"] == "enable"][0], outcomes)
    ok("check: it wrote nothing, recorded nothing and changed nothing in launchd",
       files_of(q) == snap and not make.fake.writes
       and not make.fake.verbs("disable", "enable", "bootstrap", "bootout"), make.fake.writes[:3])

    # ── 7. An update never turns a switch on; a file that is there is never written ─────────
    r = place("switch-on")
    on = all_off_doc()
    on["approve"]["merges"], on[vsettings.REPOS_KEY] = True, ["acme/app"]
    write(os.path.join(r["home"], FOLDER, SETTINGS_NAME), json.dumps(on))
    sfile = os.path.join(r["home"], FOLDER, SETTINGS_NAME)
    with open(sfile, "rb") as fh:
        was = fh.read()
    c, fake = ctx_for(r)
    (code, rows), out, _e = quiet(lambda: cmd_apply(c))
    with open(sfile, "rb") as fh:
        ok("update: a settings file with a switch on is reported and never written; a first pass "
           "that would not exit 0 here leaves the job off",
           fh.read() == was and "on: approve merges" in detail(rows, "settings")
           and by_step(rows)["settings"] == ALREADY_DONE and by_step(rows).get("enable") == BLOCKED
           and fake.held is None, "%s\n%s" % (rows, out[-600:]))
    os.chmod(sfile, 0o664)
    c, _f = ctx_for(r)
    code, rows, notes = quiet(lambda: run_steps(c, apply_it=True))[0]
    with open(sfile, "rb") as fh:
        ok("update: a settings file that is not obeyed is kept as it is, and said",
           fh.read() == was and by_step(rows)["settings"] == ALREADY_DONE
           and "not obeyed" in detail(rows, "settings") and os.stat(sfile).st_mode & 0o777 == 0o664,
           detail(rows, "settings"))
    with open(os.path.abspath(__file__), encoding="utf-8") as fh:
        body = fh.read().split("\n# SELFTEST: " + "nothing below", 1)[0]
    tree = ast.parse(body)
    funcs = dict((n.name, n) for n in tree.body if isinstance(n, ast.FunctionDef))

    def callers(attr):
        found = []
        for name, node in funcs.items():
            for n in ast.walk(node):
                if (isinstance(n, ast.Attribute) and n.attr == attr) or (
                        isinstance(n, ast.Name) and n.id == attr):
                    found.append(name)
                    break
        return sorted(found)
    ok("source: the settings file is written in two places only: absent, at install, with every "
       "switch off; and by `off`", callers("write_settings") == ["cmd_off", "step_settings"],
       callers("write_settings"))

    # ── 8. Under a model: what changes is refused first; a look reads nothing secret ─────────
    agent = {"CLAUDECODE": ""}
    t = place("agent")
    code, doc, out, err, make = machine(t, ["check"], env=agent)
    outcomes = dict((x["step"], x["outcome"]) for x in doc["rows"]) if doc else {}
    ok("agent: check is allowed: its rows, the env file NOT MEASURED and never read, exit 4",
       code == EX_UNKNOWN and doc and doc["exit"] == EX_UNKNOWN and outcomes.get("env-file") == UNKNOWN
       and not [a for a in make.fake.shells if a[2] == ENV_PROBE], outcomes)
    stream = pipe_of(answers)
    code, doc, out, err, make = machine(t, ["apply"], stdin=stream, env=agent)
    left = stream.read()
    stream.close()
    ok("agent: apply is refused with exit 3 before its standard input or the conf is read",
       code == EX_REFUSED and doc and doc["exit"] == EX_REFUSED and left == answers and not make.made
       and "REFUSED" in err and "no override" in err, "%s %s" % (code, err[-300:]))
    for argv in (["run"], ["off"]):
        code, out, err = quiet(lambda: main(argv + ["--conf", "/nonexistent/validator.conf"], env=agent))
        ok("agent: `%s` is refused with exit 3, before the conf is read" % argv[0],
           code == EX_REFUSED and "REFUSED" in out and "could not read" not in out, out[-300:])
    code, out, err = quiet(lambda: main(["run", "--dry-run", "--conf", t["conf"]], env=agent,
                                        make=make_for(t)))
    ok("agent: `run --dry-run` is a look, allowed, and its env-file row is NOT MEASURED",
       code == EX_UNKNOWN and "NOT MEASURED" in out, out[-500:])

    # ── 9. apply's answers: a pipe, a yes, and nothing else ─────────────────────────────────
    u = place("answers")
    as_file = os.path.join(u["base"], "answers.json")
    write(as_file, answers)
    snap = files_of(u)
    with open(as_file, encoding="utf-8") as fh:
        code, doc, out, err, make = machine(u, ["apply"], stdin=fh)
    ok("answers: a FILE on standard input is refused, exit 2, nothing made",
       code == EX_USAGE and "is a file" in err and not make.made, err[-300:])
    code, doc, out, err, make = machine(u, ["apply"], stdin=pipe_of(json.dumps(
        {"schema": ANSWERS_SCHEMA, "consent": "no"})))
    ok("answers: a consent that is not yes changes and records nothing, exit 10",
       code == EX_BLOCKED and doc["rows"] == [] and not make.made and "no consent" in doc["why"], doc)
    for name, text, needle in (
            ("another schema", json.dumps({"schema": "notifier-answers/1", "consent": "yes"}),
             "not a validator-answers/1"),
            ("an unknown key", json.dumps({"schema": ANSWERS_SCHEMA, "consent": "yes", "token": "x"}),
             "does not take: token")):
        code, doc, out, err, make = machine(u, ["apply"], stdin=pipe_of(text))
        ok("answers: %s is refused, exit 2, nothing made" % name,
           code == EX_USAGE and needle in err and not make.made, err[-300:])
    for sub in ("check", "apply"):
        code, doc, out, err, make = machine(u, [sub, "--dry-run"], stdin=pipe_of(answers))
        ok("%s takes no --dry-run: exit 2, nothing made" % sub,
           code == EX_USAGE and not make.made and doc["exit"] == EX_USAGE, err[-200:])
    ok("answers: none of the above changed a file", files_of(u) == snap)

    # ── 10. The ceiling: at or below it the job is on; above it, off and disabled ───────────
    v = place("above", stub=(2, 0))
    c, fake = ctx_for(v)
    (code, rows), out, err = quiet(lambda: cmd_apply(c))
    doc = rows_document("apply", code, rows, c)
    en = [x for x in doc["rows"] if x["step"] == "enable"][0]
    ok("ceiling: a first pass that would decide 2 things, above 0, is left off and disabled, never "
       "loaded, and one line says why",
       code == EX_BLOCKED and en["outcome"] == BLOCKED and en["card"] == "CK-V3" and fake.held is None
       and fake.disabled.get("local.validator") is True and "bootstrap" not in fake.verbs()
       and doc["left_off"] == "left off: its first pass would decide 2 things, above its ceiling of 0"
       and doc["first_pass"] == 2 and en["detail"].startswith(doc["left_off"])
       and "[dry-run] would record" in out, "%s\n%s" % (json.dumps(doc)[:900], out[-600:]))
    w = place("declined", stub=(0, 3))
    c, fake = ctx_for(w)
    (code, rows), out, err = quiet(lambda: cmd_apply(c))
    ok("ceiling: a rehearsal that did not exit 0 leaves it off: what it would decide is not known",
       by_step(rows).get("enable") == BLOCKED and "rehearsal exited 3" in (c.left_off or "")
       and fake.held is None and fake.disabled.get("local.validator") is True, c.left_off)
    x = place("noceiling")
    c, fake = ctx_for(x, ceiling=None)
    (code, rows), out, err = quiet(lambda: cmd_apply(c))
    ok("ceiling: no ceiling it can read is never 'no limit': left off, disabled, NOT MEASURED",
       code == EX_UNKNOWN and by_step(rows).get("enable") == UNKNOWN and fake.held is None
       and fake.disabled.get("local.validator") is True, rows)

    # ── 11. A job you paused stays paused; a stale one is reloaded; a stranger's is never replaced
    c, fake = ctx_for(p)
    fake.held = None                       # a person's bootout, after the install switched it on
    fake.disabled["local.validator"] = True
    (code, rows), out, err = quiet(lambda: cmd_apply(c))
    ok("pause: a job that was on and is not loaded is never switched back on",
       code == EX_BLOCKED and by_step(rows).get("enable") == BLOCKED and c.row_cards.get("enable") == "CK-V3"
       and "paused" in detail(rows, "enable") and not fake.verbs("enable", "bootstrap", "disable"),
       "%s %s" % (rows, fake.calls))
    y = place("reload")
    c, fake = ctx_for(y)
    quiet(lambda: cmd_apply(c))
    held_before = dict(fake.held)
    with open(y["conf"], "a", encoding="utf-8") as fh:
        fh.write("INTERVAL_SECONDS=600\n")
    c, _f = ctx_for(y, fake=fake)
    (code, rows), out, err = quiet(lambda: cmd_apply(c))
    seq = [v_ for v_ in fake.verbs("bootout", "enable", "bootstrap", "disable")][-3:]
    ok("reload: a loaded job holding an older job file is reloaded (unload, enable, load) and its "
       "next pass judged",
       code == EX_OK and by_step(rows).get("enable") == DONE and seq == ["bootout", "enable", "bootstrap"]
       and fake.held["StartInterval"] == 600 and held_before["StartInterval"] == 300
       and "reloaded it" in detail(rows, "enable"), "%s %s %s" % (code, rows, seq))
    z = place("stranger")
    write(os.path.join(z["home"], "Library", "LaunchAgents", "local.validator.plist"),
          plistlib.dumps({"Label": "local.validator", "ProgramArguments": ["/usr/bin/true"]}).decode(),
          0o644)
    c, fake = ctx_for(z)
    (code, rows), out, err = quiet(lambda: cmd_apply(c))
    with open(os.path.join(z["home"], "Library", "LaunchAgents", "local.validator.plist"), "rb") as fh:
        ok("job: a job file at the label that runs something else is FAILED and never replaced",
           code == EX_FAILED and by_step(rows).get("job") == FAILED
           and plistlib.load(fh)["ProgramArguments"] == ["/usr/bin/true"], rows)

    # ── 12. Each row's own refusals ──────────────────────────────────────────────────────────
    def one_row(name, sid, **kw):
        q_ = place(name, **{k: v_ for k, v_ in kw.items() if k != "prep"})
        if kw.get("prep"):
            kw["prep"](q_)
        c_, f_ = ctx_for(q_)
        (code_, rows_), out_, _e = quiet(lambda: cmd_apply(c_))
        return code_, by_step(rows_).get(sid), detail(rows_, sid), c_, f_, q_

    code, got_, words, c, f, _q = one_row("held", "hold", held=True)
    ok("hold: a hold that stands is readable: the row holds, and says no pass judges until you clear it",
       got_ == ALREADY_DONE and "a hold stands" in words and code == EX_OK, words)
    code, got_, words, c, f, _q = one_row("hold-unreadable", "hold", prep=lambda q_: write(q_["hold"], "{}", 0o000))
    ok("hold: a hold the job cannot read FAILS, and nothing after it is written",
       got_ == FAILED and "cannot read the watcher's hold" in words and not os.path.exists(c.config_path)
       or os.geteuid() == 0, words)
    code, got_, words, c, f, _q = one_row("hold-folder", "hold", prep=lambda q_: os.rmdir(os.path.dirname(q_["hold"])))
    ok("hold: no watcher folder FAILS and names the watcher's install", got_ == FAILED
       and "Install the watcher first" in words, words)
    code, got_, words, c, f, _q = one_row("no-env", "env-file", env_text=False)
    ok("env: no env file is card CK-V2", got_ == BLOCKED and c.row_cards.get("env-file") == "CK-V2", words)
    code, got_, words, c, f, _q = one_row("open-env", "env-file", prep=lambda q_: os.chmod(q_["env"], 0o644))
    ok("env: an env file others can read FAILS and names chmod 600", got_ == FAILED and "chmod 600" in words, words)
    code, got_, words, c, f, _q = one_row("half-env", "env-file",
                                          env_text="VALIDATOR_LINEAR_API_KEY=%s\n" % secret_a)
    ok("env: a name it does not set is card CK-V2, by name, never by value",
       got_ == BLOCKED and "does not set CLAUDE_WRITER_OAUTH_TOKEN" in words and secret_a not in words, words)
    code, got_, words, c, f, _q = one_row("no-app", "app", app=False)
    ok("app: no app made yet is card CK-V1", got_ == BLOCKED and c.row_cards.get("app") == "CK-V1", words)
    code, got_, words, c, f, _q = one_row("open-key", "app", prep=lambda q_: os.chmod(q_["key"], 0o644))
    ok("app: a key file others can read FAILS", got_ == FAILED and "only you can read" in words, words)
    code, got_, words, c, f, _q = one_row("open-folder", "folder",
                                          prep=lambda q_: os.chmod(os.path.join(q_["home"], FOLDER), 0o775))
    ok("folder: a folder others can write FAILS and is never changed",
       got_ == FAILED and "chmod 700" in words
       and os.stat(os.path.join(_q["home"], FOLDER)).st_mode & 0o777 == 0o775, words)
    code, got_, words, c, f, q2 = one_row("no-folder", "folder", vfolder=False)
    ok("folder: an absent folder is made, mode 700",
       got_ == DONE and os.stat(os.path.join(q2["home"], FOLDER)).st_mode & 0o777 == 0o700, words)

    r2 = place("refused-load")
    c, fake = ctx_for(r2)
    fake.boot_answer = (1, "Bootstrap failed: 1: Operation not permitted")
    (code, rows), out, err = quiet(lambda: cmd_apply(c))
    ok("load: a load launchd refuses is FAILED, and the job is disabled again",
       code == EX_FAILED and by_step(rows).get("enable") == FAILED and fake.held is None
       and fake.disabled.get("local.validator") is True and "disabled again" in detail(rows, "enable"), rows)
    r3 = place("no-pass")
    c, fake = ctx_for(r3)
    fake.run_at_load = False
    (code, rows), out, err = quiet(lambda: cmd_apply(c))
    ok("load: a job loaded whose first pass never finishes is NOT MEASURED, never 'on'",
       code == EX_UNKNOWN and by_step(rows).get("enable") == UNKNOWN and fake.held is not None, rows)

    # ── 13. A person's `run`: the look, the plan, one typed yes ─────────────────────────────
    r4 = place("person")
    snap = files_of(r4)
    code, out, err = quiet(lambda: main(["run", "--conf", r4["conf"]], env={}, make=make_for(r4),
                                        tty=True, ask=lambda _p: "no"))
    ok("run: without a typed yes nothing is changed, exit 10",
       code == EX_BLOCKED and files_of(r4) == snap and "Not confirmed" in out, out[-400:])
    code, out, err = quiet(lambda: main(["run", "--conf", r4["conf"]], env={}, make=make_for(r4),
                                        tty=False))
    ok("run: with no terminal to ask at, nothing is changed, exit 2",
       code == EX_USAGE and files_of(r4) == snap, out[-300:])
    code, out, err = quiet(lambda: main(["run", "--conf", r4["conf"]], env={}, make=make_for(r4),
                                        tty=True, ask=lambda _p: "yes"))
    ok("run: with a typed yes it shows the settings it writes, applies, and looks again: every row holds",
       code == EX_OK and '"merges": false' in out and "Checked again:" in out and "DONE: every row holds" in out,
       out[-1200:])

    # ── 14. `off`: every switch off at once ─────────────────────────────────────────────────
    o = place("off")
    path = os.path.join(o["home"], FOLDER, SETTINGS_NAME)
    code, out, err = quiet(lambda: main(["off"], env={}, home=o["home"], tty=True, ask=lambda _p: "yes"))
    ok("off: no settings file is already every switch off: nothing to do, exit 0",
       code == EX_OK and "Nothing to do" in out and not os.path.exists(path), out)
    on = all_off_doc()
    on["approve"].update(merges=True, plans=True)
    on["allow"]["credentials"] = True
    on["limits"]["max_files"] = 20
    on[vsettings.REPOS_KEY] = ["acme/app"]
    on[vsettings.LOGIN_KEY] = "acme-stage-e[bot]"
    write(path, json.dumps(on))
    code, out, err = quiet(lambda: main(["off"], env={}, home=o["home"], tty=True, ask=lambda _p: "no"))
    ok("off: without a typed yes nothing changes, exit 10",
       code == EX_BLOCKED and switches_on(vsettings.load(path)), out)
    code, out, err = quiet(lambda: main(["off"], env={}, home=o["home"], tty=True, ask=lambda _p: "yes"))
    s = vsettings.load(path)
    ok("off: every switch goes off and reads back so; the limits, the login and the list stay",
       code == EX_OK and s["state"] == vsettings.READ and not switches_on(s) and s["limits"]["max_files"] == 20
       and s[vsettings.REPOS_KEY] == ["acme/app"] and s[vsettings.LOGIN_KEY] == "acme-stage-e[bot]"
       and "merges off --repo acme/app" in out, out)
    os.chmod(path, 0o666)
    write(path, json.dumps(on), 0o666)
    code, out, err = quiet(lambda: main(["off"], env={}, home=o["home"], tty=True, ask=lambda _p: "yes"))
    ok("off: a file that is not obeyed is replaced, after the yes, by one with every switch off",
       code == EX_OK and vsettings.load(path)["state"] == vsettings.READ
       and not switches_on(vsettings.load(path)) and "not obeyed" in out, out)

    # ── 15. What this file can never do, read from its own source ───────────────────────────
    consts = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    ok("source: no command anywhere names sudo, dscl or sysadminctl",
       not [x_ for x_ in consts if x_ in ("sudo", "dscl", "sysadminctl")])
    lists = [n for n in ast.walk(tree) if isinstance(n, ast.List)
             and any(isinstance(e, ast.Constant) and e.value == "boot" + "strap" for e in n.elts)]
    ok("source: one command list loads the job, inside _bootstrap, reached only through load_job, "
       "itself reached only from switch_on",
       len(lists) == 1 and [n for n in ast.walk(funcs["_bootstrap"]) if n in lists] == lists
       and callers("_bootstrap") == ["load_job"] and callers("load_job") == ["switch_on"],
       (len(lists), callers("_bootstrap"), callers("load_job")))
    ok("source: no open() call names the key file: it is looked at by lstat only",
       "APP_KEY_FILE" not in "".join(
           ast.get_source_segment(body, n) or "" for n in ast.walk(tree)
           if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "open"))

    # ── 16. Root: refused first, with the shared words, and still one JSON document ─────────
    saved = rg._uid
    rg._uid = lambda: 0
    try:
        code, doc, out, err, make = machine(p, ["apply"], stdin=pipe_of(answers))
    finally:
        rg._uid = saved
    ok("root: apply as root is refused first, with the one message, and prints its document",
       code == EX_REFUSED and doc and doc["exit"] == EX_REFUSED and rg.MESSAGE in err and not make.made, err[-300:])

    for line in failures:
        print(line)
    print("%s: %d of %d checks passed" % ("FAIL" if failures else "OK", total[0] - len(failures), total[0]))
    return EX_FAILED if failures else EX_OK


if __name__ == "__main__":
    sys.exit(main())
