#!/usr/bin/env python3
"""Notifier installer — the human-action notifier, activated with one command, run until it stops asking.

    python3 scripts/pipeline_notifier_setup.py run --dry-run    # read it first
    python3 scripts/pipeline_notifier_setup.py run              # the only command that changes anything

Same contract as the Stage E installer (scripts/pipeline_stage_e_setup.py), whose exit codes,
step outcomes, runner, ledger shape, placeholder list and plist shape it imports rather than
copies: one command, run repeatedly, idempotent. When it reaches something only a person can
do it stops, prints a numbered CHECKPOINT CARD, and exits 10. Do that one thing and run the
same command again: it checks your work and carries on.

WHAT IT BUILDS, AS THE DISPATCHER'S ROLE ACCOUNT, BESIDE AN EXISTING STAGE E INSTALL

    <ENV_FILE>          one line added or replaced: <CHAT_TOKEN_ENV>=xoxb-…, mode 600.
                        Every other line in the file is kept.
    <NOTIFIER_CONFIG>   the notifier's JSON config, mode 600. Env var NAMES and ids only.
    /Library/LaunchDaemons/<JOB_LABEL>.plist
                        one system LaunchDaemon running `pipeline_notify_local.py run
                        --config <NOTIFIER_CONFIG>` every INTERVAL_SECONDS. INSTALLED, NEVER
                        LOADED: loading is the moment real pings start, and that is card CK-N3.

It needs Stage E first: the role account, its clone of this kit, and its env file holding the
tracker key. It creates none of them.

THE STEPS, IN ORDER — each reports exactly one outcome from the Stage E vocabulary

    preflight    the conf, the role account's home, the notifier in its clone, and the
                 notifier's own --selftest run from THIS checkout
    slack-app    CK-N1: a Slack app for the notifier alone, and a PRIVATE channel
    credentials  the Slack token in the role account's env file — its shape measured in that
                 account's shell, its value never read into this process
    labels       agent:blocked and agent:needs-human resolved by exact name, and `self`
    config       the notifier's config, composed and passed through the notifier's OWN
                 loader before anything is written
    job          the LaunchDaemon plist, installed and not loaded
    dry-run      the notifier run once as the role account with --dry-run, through the job's
                 own shell command, so the env-file sourcing is exercised
    enable       CK-N3: you load it; this measures whether it is loaded
    first-ping   CK-N4: one throwaway escalation watched end to end
    handover     what is on, what is off, and what is not proven — each with a ticket id

SUBCOMMANDS

    run                 do everything possible; stop at the first card
    run --dry-run       the same pass with apply OFF: measures, names what would change,
                        changes nothing, and asks for nothing
    status              replays the ledger from disk. It probes nothing
    verify              re-measures EVERY step, never stops early, changes nothing, writes
                        nothing (not even the ledger), asks for no credential, and never raises
    card <CK-id>        print a checkpoint card, at any time, with or without a conf
    attest <A-id> --initials YOUR-INITIALS --note "..."
                        record something no computer can check. YOUR-INITIALS is a placeholder
                        and is refused as typed
    --selftest          offline battery; every transport stubbed

EXIT CODES — the Stage E installer's own numbers (contract §13)

    0   DONE / ALREADY-DONE
    1   FAILED — a step ran and failed; the failure is named
    2   USAGE or CONFIG — nothing was attempted
    3   REFUSED — an agent environment asked for a mutating action
    4   UNKNOWN — a step could not measure itself. Not a pass and not a failure: it blocks
    5   NO ADMINISTRATOR ACCESS — `sudo` absent or declined, so nothing was attempted
    10  BLOCKED-ON-HUMAN — a card is printed (or, for `verify` and a dry run, work is
        outstanding)

THE AGENT-ENVIRONMENT POLICY — the Stage E installer's, stated here in full

  The markers are `pipeline_dispatch_local.AGENT_ENV_MARKERS`, IMPORTED, never copied, and a
  marker set to the empty string counts as set.

  * REFUSED under a model, with no override flag: `run` without --dry-run, and `attest`. A
    session that installs its own supervision — or signs a person's sign-off — is the thing
    the refusal exists to prevent. The refusal happens before the conf is read.
  * ALLOWED under a model: `run --dry-run`, `verify`, `status`, `card`.
  * Under a model NOTHING IS ACQUIRED OR ASKED FOR: no administrator password, no credential
    prompt.
  * Under a model NOTHING NEEDING THE ROLE ACCOUNT OR ROOT IS ATTEMPTED. Every such measurement
    is reported NOT MEASURED — an UNKNOWN row, exit 4, naming what was not looked at and why —
    never DONE and never FAILED.
  * Under a model NO TRACKER REQUEST IS MADE, with any key. The Stage E installer learned this
    the hard way: a model-run `verify` once read the owner's key and made eight authenticated
    requests with it.
  * Under a model NOTHING IS WRITTEN, the ledger included. An agent's measurement is mostly
    NOT MEASURED rows by construction; recording it would make `status` report an installed
    machine as unmeasured.
  * TAMPER-EVIDENT, NOT TAMPER-PROOF. The markers are environment variables and a session's
    shell can unset its own. That stops the accident and makes the deliberate act visible; it
    is not a boundary. The boundaries are the self-protected PreToolUse hook and the role
    account's file permissions.

WHAT IT NEVER DOES

  It never merges, approves or enables auto-merge. It never writes a label, moves a ticket,
  creates a ticket or posts a comment: its tracker transport sends read queries only, to one
  fixed HTTPS endpoint, and refuses a redirect, which would carry the key wherever it named.
  It never creates a label — a missing one is a failure naming the setup that creates it. It
  never loads the job. It never reads the Slack token into this process: the token's length
  and shape are judged inside the role account's shell, which prints a verdict. A token typed
  at the hidden prompt goes to that shell on stdin, never argv. `--selftest` asserts all of
  this against this file's own source.

THE LEDGER

  `~/.notifier-setup/state.json` under YOUR home (directory 700, file 600): step outcomes, the
  ids the labels step resolved, and your sign-offs. Never a credential. `run` and a person's
  `run --dry-run` record into it; `verify` and anything under a model record nothing. `status`
  replays it in a fresh process.

TWO SIGN-OFFS ARE BOUND TO WHAT THEY SIGNED

  A-PRIVATE-CHANNEL records the channel id in the conf when it was signed; change the channel
  and CK-N1 comes back. A-FIRST-PING records the config the job ran under; change the config
  and CK-N4 comes back. A sign-off that outlives the thing it vouched for vouches for nothing.
"""
import argparse
import atexit
import getpass
import hashlib
import json
import os
import plistlib
import re
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from xml.sax.saxutils import escape as _xml_escape

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# The refusal markers are the local dispatcher's. A copied tuple is a tuple that drifts, and a
# detector that drifts stops detecting — so a failed import is fatal on purpose.
from pipeline_dispatch_local import AGENT_ENV_MARKERS  # noqa: E402
# One exit-code table, one outcome vocabulary, one runner, one ledger shape, one placeholder
# list and one plist shape: the Stage E installer's.
import pipeline_stage_e_setup as se  # noqa: E402
# The notifier itself — its loader validates the composed config before anything is written,
# and its own redaction and shape scan run over everything this file prints or records.
import pipeline_notify_local as notify  # noqa: E402

SCHEMA = "notifier-setup/1"
DEFAULT_STATE_HOME = "~/.notifier-setup"
DEFAULT_CONF = "notifier.conf"
LINEAR_API = se.LINEAR_API

EX_OK, EX_FAILED, EX_USAGE, EX_REFUSED, EX_UNKNOWN, EX_NOPRIV, EX_BLOCKED = (
    se.EX_OK, se.EX_FAILED, se.EX_USAGE, se.EX_REFUSED, se.EX_UNKNOWN, se.EX_NOPRIV,
    se.EX_BLOCKED)
DONE, ALREADY_DONE, BLOCKED, FAILED, UNKNOWN, SKIPPED, WOULD_CHANGE = (
    se.DONE, se.ALREADY_DONE, se.BLOCKED, se.FAILED, se.UNKNOWN, se.SKIPPED, se.WOULD_CHANGE)
SetupError, Blocked, Unknown, Refusal, NoPrivilege = (
    se.SetupError, se.Blocked, se.Unknown, se.Refusal, se.NoPrivilege)
say, warn = se.say, se.warn

NOTIFIER_SCRIPT = "pipeline_notify_local.py"
# What the job execs: the notifier, and every script it imports at run time, transitively. The
# selftest derives this closure from the sources and fails when the tuple drifts from it.
REQUIRED_SCRIPTS = ("pipeline_notify_local.py", "pipeline_review_local.py", "check_schemas.py",
                    "jsonschema_mini.py")
# The two lifecycle labels the notifier applies, read out of its own marks table.
LABEL_KEYS = tuple(sorted({m["label"] for m in notify.MARKS.values() if m["label"]}))
EXECUTOR_SELF = "self"
# The name the dispatcher's own Slack lane reads out of the dispatcher's environment — which is
# copied into every session. The notifier's token may never live under it (owner, 2026-09-17).
DISPATCHER_CHAT_TOKEN_ENV = "SLACK_BOT_TOKEN"
TOKEN_PREFIX = "xoxb-"
TOKEN_MIN_LEN = 20
_TOKEN_RE = re.compile(r"^xoxb-[A-Za-z0-9-]+$")
# The notifier's exit vocabulary, read from the notifier: 0 ran, 3 ran and declined something.
DRY_RUN_OK = (notify.EXIT_OK, notify.EXIT_DECLINED)
LOG_NAME = "notifier.log"
A_PRIVATE = "A-PRIVATE-CHANNEL"
A_FIRST_PING = "A-FIRST-PING"
NOTE_CONFIG = "config_sha256"
NOTE_DRY_RUN = "dry_run"


# --------------------------------------------------------------------------- #
# The conf — parsed, never sourced, every error at once
# --------------------------------------------------------------------------- #
CONF_REQUIRED = ("ROLE_ACCOUNT", "ROLE_KIT_CLONE", "TEAM_KEYS", "TICKET_URL_TEMPLATE",
                 "CHAT_CHANNEL_ID", "EXECUTOR_ACTOR_IDS", "JOB_LABEL")
CONF_DEFAULTS = {
    "CHAT_TOKEN_ENV": "NOTIFIER_SLACK_BOT_TOKEN",
    "LINEAR_KEY_ENV": "STAGE_E_LINEAR_API_KEY",
    "ENV_FILE": "~/.stage-e/env",
    "NOTIFIER_CONFIG": "~/.stage-e/notifier.json",
    "STATE_DIR": "~/.stage-e/state",
    "INTERVAL_SECONDS": "300",
    "MAX_EVENTS_PER_PASS": "25",
    "LOOKBACK_COMMENTS": "20",
    "RUN_TIMEOUT_SECONDS": "240",
    "CHAT_API_BASE": "https://slack.com/api",
    "DISPATCHER_ENV_FILE": "",
    "DISPATCHER_STATE_ROOT": "",
}
CONF_KEYS = set(CONF_REQUIRED) | set(CONF_DEFAULTS)
# Left invalid on purpose so an unedited copy of the example cannot run.
CONF_UNEDITED = {"CHAT_CHANNEL_ID": "C0123456789",
                 "TICKET_URL_TEMPLATE": "https://linear.app/example-org/issue/{id}"}
# What a card shows when no conf is loaded: the example file's own values.
CARD_EXAMPLE = dict(CONF_DEFAULTS, ROLE_ACCOUNT="_exdispatch", ROLE_KIT_CLONE="~/.stage-e/kit",
                    TEAM_KEYS="KIT", TICKET_URL_TEMPLATE=CONF_UNEDITED["TICKET_URL_TEMPLATE"],
                    CHAT_CHANNEL_ID=CONF_UNEDITED["CHAT_CHANNEL_ID"], EXECUTOR_ACTOR_IDS="self",
                    JOB_LABEL="com.example.notifier")
ENV_NAME_KEYS = ("CHAT_TOKEN_ENV", "LINEAR_KEY_ENV")
PATH_KEYS = ("ROLE_KIT_CLONE", "ENV_FILE", "NOTIFIER_CONFIG", "STATE_DIR")
OPTIONAL_PATH_KEYS = ("DISPATCHER_ENV_FILE", "DISPATCHER_STATE_ROOT")
NUMERIC_KEYS = ("INTERVAL_SECONDS", "MAX_EVENTS_PER_PASS", "LOOKBACK_COMMENTS",
                "RUN_TIMEOUT_SECONDS")

_CONF_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_UPPER_SNAKE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$")
# A path this file will put inside double quotes in a shell command and inside a plist: `~/…`
# or absolute, and no character either reader gives a meaning to.
_PATH_RE = re.compile(r"^(?:~/|/)[A-Za-z0-9._/ -]*$")
_CHANNEL_RE = re.compile(r"^[CG][A-Z0-9]{8,}$")
_ACTOR_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
# Path comparisons made before the role account's home is known expand `~` to this.
ROLE_HOME_SENTINEL = "/__role_home__"


def split_list(value):
    return se.split_list(value)


def credential_shape(value):
    """True when a conf value carries a credential shape. Checked per token, so a label like
    `com.example.task-notifier` is not refused for containing `sk-`, and a long path segment
    is not refused as a blob."""
    for tok in re.split(r"[\s,:/@=]+", value or ""):
        if tok.startswith(se._CRED_PREFIXES):
            return True
    return any(se._BLOB_RE.match(t) for t in re.split(r"[\s,]+", value or "") if "/" not in t)


def parse_conf(text, source=DEFAULT_CONF):
    """(values, errors). Every bad line is reported, not the first."""
    values, seen, errors = {}, {}, []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            errors.append("%s:%d: not KEY=value: %r" % (source, n, raw[:60]))
            continue
        key, val = (part.strip() for part in line.split("=", 1))
        if not _CONF_KEY_RE.match(key):
            errors.append("%s:%d: %r is not a KEY (upper-case, digits, underscore)"
                          % (source, n, key[:40]))
            continue
        if key in seen:
            errors.append("%s:%d: duplicate key %s, first set at line %d — last-wins would hide "
                          "the effective value" % (source, n, key, seen[key]))
            continue
        if key not in CONF_KEYS:
            errors.append("%s:%d: unknown key %s (see notifier.conf.example)" % (source, n, key))
            continue
        if credential_shape(val):
            errors.append("%s:%d: %s carries a CREDENTIAL SHAPE. No key in this file ever holds "
                          "one: the Slack token is typed at a hidden prompt and written to the "
                          "role account's env file. (The value is not shown.)" % (source, n, key))
            continue
        seen[key] = n
        values[key] = val
    return values, errors


def resolve_path(value, home):
    """A conf path as the role account sees it: `~/x` under `home`, anything else as written."""
    if value.startswith("~/"):
        return os.path.normpath(home.rstrip("/") + "/" + value[2:])
    return os.path.normpath(value)


def _under(child, parent):
    return child == parent or child.startswith(parent.rstrip("/") + "/")


def path_problems(conf, home, skip=()):
    """The path rules, against one home. Called at conf time with a sentinel home, and again in
    preflight with the real one: `~/x` against an absolute path can only be compared once the
    role account's home is known. A key named in `skip` is malformed and already reported, so
    no rule compares against it — and every rule that does not need it still runs."""
    p = dict((k, resolve_path(conf[k], home)) for k in PATH_KEYS if k not in skip)
    out = []
    if "ENV_FILE" in p and p.get("ENV_FILE") == p.get("NOTIFIER_CONFIG"):
        out.append("ENV_FILE and NOTIFIER_CONFIG are the same file (%s) — writing the config "
                   "would destroy the credentials" % conf["ENV_FILE"])
    for key in ("ENV_FILE", "NOTIFIER_CONFIG", "STATE_DIR"):
        if key in p and "ROLE_KIT_CLONE" in p and _under(p[key], p["ROLE_KIT_CLONE"]):
            out.append("%s (%s) is inside ROLE_KIT_CLONE (%s), a git working tree: a pull or a "
                       "clean can take it, and the notifier refuses a state dir inside a worktree"
                       % (key, conf[key], conf["ROLE_KIT_CLONE"]))
    denv = conf.get("DISPATCHER_ENV_FILE") or ""
    if denv and "DISPATCHER_ENV_FILE" not in skip and resolve_path(denv, home) == p.get("ENV_FILE"):
        out.append("ENV_FILE (%s) is the dispatcher's own env file (DISPATCHER_ENV_FILE). That "
                   "file is copied unscrubbed into every session, so a token in it is a token "
                   "every session holds" % conf["ENV_FILE"])
    root = conf.get("DISPATCHER_STATE_ROOT") or ""
    if root and "DISPATCHER_STATE_ROOT" not in skip:
        r = resolve_path(root, home)
        for key in PATH_KEYS:
            if key in p and _under(p[key], r):
                out.append("%s (%s) is under DISPATCHER_STATE_ROOT (%s), where what a session can "
                           "read is unmeasured" % (key, conf[key], root))
    return out


def validate_conf(values, invoking_user=None):
    """Every bad value in ONE pass."""
    conf = dict(CONF_DEFAULTS)
    conf.update(values)
    errors = []
    for key in CONF_REQUIRED:
        if not conf.get(key):
            errors.append("%s is required and is missing or empty" % key)
    for key, placeholder in CONF_UNEDITED.items():
        if conf.get(key) == placeholder:
            errors.append("%s is still the example value %r — this conf has not been edited"
                          % (key, placeholder))

    account = conf.get("ROLE_ACCOUNT") or ""
    if account:
        if not se._ACCOUNT_RE.match(account):
            errors.append("ROLE_ACCOUNT %r is not a local account name" % account)
        elif account == "root":
            errors.append("ROLE_ACCOUNT is root. The notifier runs as the dispatcher's role "
                          "account, never as root")
        elif invoking_user and account == invoking_user:
            errors.append("ROLE_ACCOUNT %r is YOU, the account running this installer. The "
                          "notifier runs as the dispatcher's role account, beside the Stage E "
                          "daemons, so it survives a reboot with nobody logged in and its token "
                          "lives under a home your own sessions do not run in" % account)

    label = conf.get("JOB_LABEL") or ""
    if label and not se._RDNS_RE.match(label):
        errors.append("JOB_LABEL %r is not a reverse-DNS launchd label" % label)

    teams = split_list(conf.get("TEAM_KEYS"))
    if conf.get("TEAM_KEYS") and not teams:
        errors.append("TEAM_KEYS names no team key")
    for team in teams:
        if not se._TEAM_KEY_RE.match(team):
            errors.append("TEAM_KEYS entry %r is not a team key (1-5 upper-case letters or "
                          "digits)" % team)
    if len(set(teams)) != len(teams):
        errors.append("TEAM_KEYS repeats a key — one pass would read that team twice")

    url = conf.get("TICKET_URL_TEMPLATE") or ""
    if url:
        if "{id}" not in url:
            errors.append("TICKET_URL_TEMPLATE must contain {id} (got %r)" % url)
        if not url.startswith("https://"):
            errors.append("TICKET_URL_TEMPLATE must be an https URL (got %r)" % url)

    channel = conf.get("CHAT_CHANNEL_ID") or ""
    if channel and not _CHANNEL_RE.match(channel):
        errors.append("CHAT_CHANNEL_ID %r is not a Slack channel id (C… or G…, upper-case "
                      "letters and digits)" % channel)

    actors = split_list(conf.get("EXECUTOR_ACTOR_IDS"))
    if conf.get("EXECUTOR_ACTOR_IDS") and not actors:
        errors.append("EXECUTOR_ACTOR_IDS names nobody")
    for actor in actors:
        if actor != EXECUTOR_SELF and not _ACTOR_RE.match(actor):
            errors.append("EXECUTOR_ACTOR_IDS entry %r is neither `self` nor a tracker actor id"
                          % actor)
    if len(set(actors)) != len(actors):
        errors.append("EXECUTOR_ACTOR_IDS repeats an entry")

    for key in ENV_NAME_KEYS:
        if not _UPPER_SNAKE.match(conf.get(key) or ""):
            errors.append("%s must be the NAME of an environment variable in UPPER_SNAKE — "
                          "letters, digits, single underscores — never a value (got %r)"
                          % (key, conf.get(key)))
    if conf.get("CHAT_TOKEN_ENV") == DISPATCHER_CHAT_TOKEN_ENV:
        errors.append(
            "CHAT_TOKEN_ENV may not be %s. That is the name the dispatcher's own Slack lane "
            "reads from the dispatcher's environment, which is copied into every session. The "
            "notifier uses a SEPARATE Slack app with its own token (owner decision, 2026-09-17), "
            "so a chat token leaked from a session cannot post a ping that looks like the "
            "notifier's — and a different variable name is what keeps the two tokens from ever "
            "being the same value. Keep the default, NOTIFIER_SLACK_BOT_TOKEN"
            % DISPATCHER_CHAT_TOKEN_ENV)
    if conf.get("CHAT_TOKEN_ENV") and conf.get("CHAT_TOKEN_ENV") == conf.get("LINEAR_KEY_ENV"):
        errors.append("CHAT_TOKEN_ENV and LINEAR_KEY_ENV name the same variable — one of the two "
                      "credentials would overwrite the other")

    if not (conf.get("CHAT_API_BASE") or "").startswith("https://"):
        errors.append("CHAT_API_BASE must be an https URL (got %r)" % conf.get("CHAT_API_BASE"))

    malformed = []
    for key in PATH_KEYS + OPTIONAL_PATH_KEYS:
        value = conf.get(key) or ""
        if not value and key in OPTIONAL_PATH_KEYS:
            continue
        if not _PATH_RE.match(value) or "/../" in value + "/" or value.rstrip("/") in ("", "~"):
            malformed.append(key)
            errors.append("%s must be `~/…` (the role account's home) or an absolute path, of "
                          "letters, digits, `.`, `_`, `-`, `/` and spaces, with no `..` (got %r)"
                          % (key, value))

    ints = {}
    for key in NUMERIC_KEYS:
        value = conf.get(key) or ""
        if not value.isdigit() or int(value) <= 0:
            errors.append("%s must be a positive integer (got %r)" % (key, value))
        else:
            ints[key] = int(value)
    if len(ints) == len(NUMERIC_KEYS) and ints["RUN_TIMEOUT_SECONDS"] >= ints["INTERVAL_SECONDS"]:
        errors.append("RUN_TIMEOUT_SECONDS (%d) must be shorter than INTERVAL_SECONDS (%d) — a "
                      "pass allowed the whole interval leaves no gap between passes"
                      % (ints["RUN_TIMEOUT_SECONDS"], ints["INTERVAL_SECONDS"]))

    errors.extend(path_problems(conf, ROLE_HOME_SENTINEL, skip=malformed))
    return conf, errors


def invoking_user():
    import pwd
    return pwd.getpwuid(os.getuid()).pw_name


def load_conf(path, user=None):
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        raise SetupError("could not read %s: %s\n  cp notifier.conf.example notifier.conf && "
                         "chmod 600 notifier.conf" % (path, exc))
    values, errors = parse_conf(text, os.path.basename(path))
    conf, more = validate_conf(values, user)
    conf["__source__"] = path
    return conf, errors + more


# --------------------------------------------------------------------------- #
# Rendering — the shell command and the plist, both from the conf alone
# --------------------------------------------------------------------------- #
def sh_path(value):
    """A conf path as /bin/sh reads it inside double quotes. `~/x` becomes `$HOME/x`, resolved
    when the job runs, in the role account's home. Paths are validated to carry no character
    double quotes give a meaning to."""
    return "$HOME/" + value[2:] if value.startswith("~/") else value


def _q(value):
    return '"%s"' % sh_path(value)


def exec_prefix(conf):
    """How every Stage E daemon's command starts (`DAEMON_EXEC`): the env file sourced with
    `set -a`, then Apple's interpreter by full path. With the default ENV_FILE this is
    byte-identical to the Stage E installer's constant, and the selftest pins that."""
    return 'set -a; . %s; set +a; exec /usr/bin/python3 ' % _q(conf["ENV_FILE"])


def job_command(conf):
    """The job's whole shell command. The dry-run step runs exactly this, plus `--dry-run`."""
    script = conf["ROLE_KIT_CLONE"].rstrip("/") + "/scripts/" + NOTIFIER_SCRIPT
    return exec_prefix(conf) + "%s run --config %s" % (_q(script), _q(conf["NOTIFIER_CONFIG"]))


def plist_path(conf):
    return se._dispatcher_plist(conf["JOB_LABEL"])


def log_path(conf, home):
    return resolve_path(os.path.dirname(conf["NOTIFIER_CONFIG"].rstrip("/")), home) + "/" + LOG_NAME


def render_plist(conf, home):
    """The Stage E installer's own PLIST template, filled in. launchd expands nothing, so the
    home and the log are absolute; `$HOME` inside the command is /bin/sh's, at run time."""
    return se.PLIST.format(
        label=_xml_escape(conf["JOB_LABEL"]), account=_xml_escape(conf["ROLE_ACCOUNT"]),
        home=_xml_escape(home), path=_xml_escape(se.DAEMON_PATH),
        command=_xml_escape(job_command(conf)), interval=int(conf["INTERVAL_SECONDS"]),
        log=_xml_escape(log_path(conf, home)))


def compose_config(conf, label_ids, actor_ids):
    """The notifier's config, from the conf and the resolved ids. `~` stays literal: the notifier
    expands it when it runs, as the role account."""
    return {
        "state_dir": conf["STATE_DIR"],
        "linear_key_env": conf["LINEAR_KEY_ENV"],
        "chat_token_env": conf["CHAT_TOKEN_ENV"],
        "chat_channel_id": conf["CHAT_CHANNEL_ID"],
        "chat_api_base": conf["CHAT_API_BASE"],
        "team_keys": split_list(conf["TEAM_KEYS"]),
        "ticket_url_template": conf["TICKET_URL_TEMPLATE"],
        "label_ids": dict((k, label_ids[k]) for k in LABEL_KEYS if label_ids.get(k)),
        "executor_actor_ids": list(actor_ids),
        "max_events_per_pass": int(conf["MAX_EVENTS_PER_PASS"]),
        "lookback_comments": int(conf["LOOKBACK_COMMENTS"]),
        "run_timeout_seconds": int(conf["RUN_TIMEOUT_SECONDS"]),
    }


def validate_composed(doc, role_home):
    """The bytes to write — or SetupError, before anything is written.

    The judge is the notifier's OWN loader (`load_config_from`), not a second opinion kept here:
    a composition it would refuse is a failure now, not a daemon that exits 2 every interval.
    `~` is resolved against the role account's home for the loader's worktree check, because
    that is where the daemon resolves it. This validates against THIS checkout's notifier; the
    dry-run step then proves the file against the role account's clone."""
    body = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    probe = json.loads(body)
    with tempfile.TemporaryDirectory(prefix="notifier-setup-") as tmp:
        home = role_home or os.path.join(tmp, "role-home")
        if str(probe.get("state_dir", "")).startswith("~/"):
            probe["state_dir"] = os.path.join(home, probe["state_dir"][2:])
        try:
            notify.load_config_from(probe, tmp)
        except notify.NotifierError as exc:
            raise SetupError("the notifier's own loader REFUSES this composition, so nothing was "
                             "written:\n  %s" % str(exc).replace("\n", "\n  "))
    return body


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Scrubbing — everything printed or recorded passes through here
# --------------------------------------------------------------------------- #
def scrub(text, held=()):
    """Any credential VALUE this process holds is replaced, by the notifier's own `redact`; then
    any line still carrying a credential SHAPE, by the notifier's own scan, is withheld whole.
    The second half matters because the Slack token is never held here: if a component echoes
    it, only its shape can catch it. A scan that cannot run withholds everything it was given."""
    out = notify.redact(text or "", [h for h in held if h])
    lines = []
    for line in out.split("\n"):
        try:
            hits = notify.secret_hits(line)
        except Exception:                                         # noqa: BLE001
            return "(output withheld: the shared secret scan could not run)"
        lines.append("(a line was withheld here: it carries a %s shape)" % ", ".join(hits)
                     if hits else line)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# The tracker — read queries only, one endpoint, no redirect
# --------------------------------------------------------------------------- #
class TrackerError(SetupError):
    def __init__(self, message, rejected=False):
        SetupError.__init__(self, message)
        self.rejected = rejected


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise TrackerError("the tracker answered with a redirect (HTTP %d), which this program "
                           "never follows: a redirect would carry the key to whatever address "
                           "it named" % code)


class TrackerReader(object):
    """Two questions, asked of one fixed HTTPS endpoint, and nothing else.

    Every document goes through `_ask`, which refuses anything that is not a `query`, so there is
    no call site here that could write. The key is in one request header and nowhere else."""

    _ENDPOINT = LINEAR_API
    _EXTRA_HANDLERS = ()
    Q_VIEWER = "query NotifierSetupViewer { viewer { id } }"
    Q_LABELS = ("query NotifierSetupLabelByName($name: String!) { issueLabels(filter: "
                "{ name: { eq: $name } }, first: 50) { nodes { id name team { id key } } } }")

    def __init__(self, key):
        handlers = [_RefuseRedirect()] + [h() for h in self._EXTRA_HANDLERS]
        self._opener = urllib.request.build_opener(*handlers)
        self._auth = ("Bearer " + key) if key.startswith("lin_oauth_") else key

    def _ask(self, document, variables=None):
        if not re.match(r"^\s*query\s", document):
            raise TrackerError("refusing to send a document that is not a read query")
        body = json.dumps({"query": document, "variables": variables or {}}).encode("utf-8")
        req = urllib.request.Request(self._ENDPOINT, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", self._auth)
        try:
            with self._opener.open(req, timeout=30) as resp:
                raw = resp.read()
        except TrackerError:
            raise
        except urllib.error.HTTPError as exc:
            if 300 <= exc.code < 400:
                raise TrackerError("the tracker answered with a redirect (HTTP %d), which this "
                                   "program never follows" % exc.code)
            try:
                text = exc.read().decode("utf-8", "replace")
            except Exception:                                     # noqa: BLE001
                text = ""
            if exc.code in (401, 403) or "authenticat" in text.lower():
                raise TrackerError("the tracker refused the key (HTTP %d)" % exc.code,
                                   rejected=True)
            raise TrackerError("the tracker answered HTTP %d" % exc.code)
        except (urllib.error.URLError, OSError) as exc:
            raise TrackerError("the tracker was unreachable (%s)"
                               % str(getattr(exc, "reason", exc))[:120])
        try:
            doc = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise TrackerError("the tracker's answer was not JSON")
        if isinstance(doc, dict) and doc.get("errors"):
            msgs = "; ".join(str(e.get("message", "?"))[:120] for e in doc["errors"][:3]
                             if isinstance(e, dict))
            raise TrackerError("the tracker returned errors: %s" % msgs,
                               rejected="authenticat" in msgs.lower())
        data = doc.get("data") if isinstance(doc, dict) else None
        if not isinstance(data, dict):
            raise TrackerError("the tracker's answer carried no data object")
        return data

    def viewer_id(self):
        """The id of the user the key belongs to — what `self` means in EXECUTOR_ACTOR_IDS."""
        data = self._ask(self.Q_VIEWER)
        return str(((data.get("viewer") or {}).get("id")) or "")

    def labels_named(self, name):
        """Every label the tracker returns for this exact name, with its team key or None."""
        data = self._ask(self.Q_LABELS, {"name": name})
        nodes = ((data.get("issueLabels") or {}).get("nodes")) or []
        return [{"id": n.get("id"), "name": n.get("name"),
                 "team": ((n.get("team") or {}).get("key")) or None}
                for n in nodes if isinstance(n, dict)]


# --------------------------------------------------------------------------- #
# The ledger
# --------------------------------------------------------------------------- #
class State(se.State):
    """The Stage E ledger shape, under its own schema, saved at directory 700 / file 600."""

    def __init__(self, root):
        se.State.__init__(self, root)
        self.data["schema"] = SCHEMA

    def save(self):
        try:
            os.makedirs(self.root, mode=0o700, exist_ok=True)
            if os.path.realpath(self.root) != os.path.realpath(os.path.expanduser("~")):
                os.chmod(self.root, 0o700)
            tmp = self.path + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=2, sort_keys=True)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
            return True
        except OSError as exc:
            warn("could not write the ledger %s: %s — `status` will not show this" % (self.path, exc))
            return False

    def notes(self):
        return self.data.setdefault("notes", {})


# --------------------------------------------------------------------------- #
# Cards and sign-offs
# --------------------------------------------------------------------------- #
INITIALS_PLACEHOLDER = se.INITIALS_PLACEHOLDER

CARDS = {
    "CK-N1": {
        "title": "Create the notifier's own Slack app and a PRIVATE channel",
        "why": ("The notifier posts as one bot into one channel, and both are yours to create: a "
                "Slack app is made in Slack's UI, and nothing this installer can reach sees whether "
                "a channel is private. Private, because on the free plan channel membership is the "
                "only access control there is: anyone who can join the channel reads every ping. "
                "A SEPARATE app, because the dispatcher's own Slack lane reads its token as "
                "SLACK_BOT_TOKEN from the dispatcher's environment, which is copied into every "
                "session. If the notifier shared that app, a token leaked from a session could "
                "post a ping that looks exactly like the notifier's. Two apps, two tokens, and a "
                "different variable name keep them apart."),
        "do": ["1. Slack API site -> Your apps -> Create New App -> From scratch. Name it for the",
               "   notifier alone. Do not reuse the dispatcher's app.",
               "2. OAuth & Permissions -> Bot Token Scopes -> add chat:write. Only that one.",
               "3. Install to Workspace. Keep the Bot User OAuth Token (xoxb-...) for the",
               "   credentials step, which asks for it at a hidden prompt. Paste it nowhere else.",
               "4. In Slack, create a channel and make it Private.",
               "5. In that channel:  /invite @<the app's name>",
               "6. Channel details -> copy the channel ID (C...) into notifier.conf:",
               "       CHAT_CHANNEL_ID=${CHAT_CHANNEL_ID}",
               "7. From a SECOND Slack account in the same workspace, search for the channel.",
               "   Good: it does not appear, and it cannot be joined.",
               "8. Sign it off, then run the installer again:",
               "       python3 scripts/pipeline_notifier_setup.py attest A-PRIVATE-CHANNEL \\",
               "         --initials " + INITIALS_PLACEHOLDER + " --note \"private; a second "
               "account could not join\"",
               "   (YOUR initials, 2-4 letters. The placeholder is refused as typed.)",
               "   The sign-off records this channel id. Change the channel and this card",
               "   comes back."],
        "good": "a private channel holding you and the notifier's bot, invisible to a second account",
        "attest": A_PRIVATE,
    },
    "CK-N2": {
        "title": "Paste the notifier's Slack token at a terminal",
        "why": ("A credential is typed by a person at a hidden prompt, and goes straight to the role "
                "account's shell on stdin, which writes it into that account's env file at mode 600. "
                "It is never an argument (argv is visible to every process), never a file in this "
                "repository, and never echoed. That needs a terminal, and this run has none."),
        "do": ["Run the installer again from a real terminal:",
               "    python3 scripts/pipeline_notifier_setup.py run",
               "It asks for ${CHAT_TOKEN_ENV} by name and shows back only its length.",
               "",
               "Or add the one line by hand, as the role account:",
               "    sudo -u ${ROLE_ACCOUNT} -H /bin/sh -c 'cd / && umask 077 && vi ${ENV_FILE}'",
               "    ${CHAT_TOKEN_ENV}=xoxb-...",
               "Never in the dispatcher's env file, which is copied unscrubbed into every",
               "session. Never under the dispatcher's state root."],
        "good": "the credentials row says the token is set, mode 600, owned by the role account",
        "attest": None,
    },
    "CK-N3": {
        "title": "Load the notifier job",
        "why": ("Loading is the moment real pings start and real labels land on real tickets. The "
                "dry run above is what you read first; turning it on is yours. This installer "
                "measures whether the job is loaded and never loads it."),
        "do": ["Read the dry-run row above. Then load the job:",
               "    sudo launchctl bootstrap system ${PLIST}",
               "If it was already loaded and this run changed its plist, unload it first, wait",
               "a few seconds, then load it. A load straight after an unload can answer",
               "`Input/output error`; wait and run the load again:",
               "    sudo launchctl bootout system/${JOB_LABEL}",
               "Check it, then run the installer again:",
               "    sudo launchctl print system/${JOB_LABEL}",
               "    python3 scripts/pipeline_notifier_setup.py run"],
        "good": "`sudo launchctl print system/${JOB_LABEL}` finds the job",
        "attest": None,
    },
    "CK-N4": {
        "title": "Watch one throwaway escalation become a ping",
        "why": ("Every row above measured one part. Only a real mark on a real ticket proves the "
                "parts work together: the token posts to the channel, the link opens the ticket, "
                "and the label lands. Only a person can look at the channel."),
        "do": ["1. Create a throwaway ticket on a scanned team (TEAM_KEYS: ${TEAM_KEYS}).",
               "2. Post a comment on it whose FIRST line is exactly:",
               "       <!-- pipeline-escalation: agent:blocked -->",
               "   Anything may follow on the lines below it.",
               "3. Wait one interval (${INTERVAL_SECONDS} s), and a little more.",
               "4. In the private channel: ONE ping naming that ticket. Open its link.",
               "   It must open that ticket.",
               "5. On the ticket: the agent:blocked label is there.",
               "6. Remove the label, then cancel the ticket.",
               "Nothing arrived? Open the comment. If the tracker's editor stored `<!--` as",
               "`&lt;!--`, the notifier was right not to page: a neutralised mark never pages.",
               "docs/NOTIFIER-OPERATOR.md says how to post the comment another way.",
               "",
               "Do not use a planning mark for this test. The four planning marks are accepted",
               "only from the executor's author id (EXECUTOR_ACTOR_IDS), so this test cannot",
               "exercise them. The first real planning run is their test.",
               "",
               "Then sign it off:",
               "    python3 scripts/pipeline_notifier_setup.py attest A-FIRST-PING \\",
               "      --initials " + INITIALS_PLACEHOLDER + " --note \"<ticket id>: pinged, link "
               "opened, label landed\"",
               "(YOUR initials, 2-4 letters. The placeholder is refused as typed.)",
               "The sign-off records the config the job ran under. Change the config and",
               "this card comes back."],
        "good": "one ping in the private channel, its link opens the ticket, and the label landed",
        "attest": A_FIRST_PING,
    },
}

ATTESTATIONS = {
    A_PRIVATE: "the notifier has its own Slack app, and its channel is private: a second account "
               "could not find or join it (CK-N1)",
    A_FIRST_PING: "one throwaway agent:blocked mark became one ping whose link opened the ticket, "
                  "and the label landed (CK-N4)",
}

NEVER = [
    "Never merge a pull request, and never approve one.",
    "Never apply or remove a protected label, and never move a ticket to ready.",
    "Never put the notifier's token in the dispatcher's env file, or under its state root.",
    "Never reword a command a guard blocked. Say that it blocked you, and stop.",
]


def _card_values(conf):
    values = dict(CARD_EXAMPLE if conf is None else conf)
    values["PLIST"] = plist_path(values)
    return values


def print_card(cid, conf=None):
    card = CARDS.get(cid)
    if not card:
        raise SetupError("no such checkpoint card: %r (have %s)" % (cid, ", ".join(sorted(CARDS))))
    values = _card_values(conf)
    say("")
    say("=" * 74)
    say(" %s — %s" % (cid, card["title"]))
    say("=" * 74)
    if conf is None:
        say(" (no notifier.conf loaded: the values below are the example ones)")
    say("")
    say("WHY THIS IS YOURS")
    for line in se._wrap(card["why"]):
        say("  " + line)
    say("")
    say("WHAT TO DO")
    for line in card["do"]:
        for key, val in values.items():
            if isinstance(val, str):
                line = line.replace("${%s}" % key, val)
        say("  " + line)
    say("")
    say("GOOD: %s" % card["good"].replace("${JOB_LABEL}", values["JOB_LABEL"]))
    if card["attest"]:
        say("SIGN-OFF: %s — %s" % (card["attest"], ATTESTATIONS[card["attest"]]))
    say("")
    say("NEVER")
    for line in NEVER:
        say("  " + line)
    say("")


# --------------------------------------------------------------------------- #
# Agent environment, and administrator access
# --------------------------------------------------------------------------- #
def agent_markers(env=None):
    """Presence, not truthiness: a marker set to the empty string is set."""
    env = os.environ if env is None else env
    return [m for m in AGENT_ENV_MARKERS if m in env]


def _self_path():
    try:
        return os.path.relpath(os.path.abspath(__file__), os.getcwd())
    except ValueError:
        return os.path.abspath(__file__)


def refuse_if_agent(action, env=None):
    found = agent_markers(env)
    if not found:
        return None
    raise Refusal(
        "REFUSED: %s is a mutating action and this is an agent environment (%s set).\n"
        "  A session that installs its own supervision, or signs a person's sign-off, is the\n"
        "  thing this refusal exists to prevent. There is no override flag. (Scrubbing these\n"
        "  variables is not an override either: it is the deliberate act this refusal makes\n"
        "  visible.)\n"
        "  A PERSON runs this, in a terminal:  python3 %s %s\n"
        "  Read-only meanwhile:  verify | status | card <CK-id> | run --dry-run"
        % (action, ", ".join(found), _self_path(), action))


def _sudo_validate():
    try:
        return subprocess.run(["sudo", "-v"]).returncode
    except FileNotFoundError:
        return 127


def _sudo_refresh():
    try:
        return subprocess.run(["sudo", "-n", "-v"], capture_output=True).returncode
    except FileNotFoundError:
        return 127


class SudoOnce(object):
    """Administrator access, asked for ONCE, with the reason on the line above the prompt, and
    kept fresh by a daemon thread (`sudo -n -v`, which can never prompt) until this process
    exits. The Stage E installer's shape, with this installer's own words."""

    def __init__(self, validate=None, refresh=None, interval=60):
        self._validate = validate or (lambda: _sudo_validate())
        self._refresh = refresh or _sudo_refresh
        self._interval = interval
        self.acquisitions = 0
        self.held = False
        self._stop = None

    def acquire(self, why, resume):
        if self.held:
            return True
        self.acquisitions += 1
        say("")
        say("-" * 74)
        say("ADMINISTRATOR PASSWORD — asked for ONCE, here, and not again this run.")
        say("-" * 74)
        say("This command reads the machine as the role account (sudo -u) and asks launchd")
        say("about the job as root. Asked as it went, it would interrupt you mid-run —")
        say("including right after the hidden prompt for the Slack token, where two password")
        say("boxes in a row look alike. Nothing has been changed yet.")
        say("")
        say("WHY IT IS NEEDED: %s" % why)
        rc = self._validate()
        if rc != 0:
            raise NoPrivilege(
                "NO ADMINISTRATOR ACCESS — nothing was attempted, and nothing was changed.\n"
                "  %s\n"
                "  Fix that and run the same command again:\n"
                "      python3 %s %s"
                % ("`sudo` is not on this machine." if rc == 127 else
                   "`sudo` refused (exit %d): no password, a wrong one, or not an administrator."
                   % rc, _self_path(), resume))
        self.held = True
        self._stop = threading.Event()
        thread = threading.Thread(target=self._keepalive, name="sudo-keepalive")
        thread.daemon = True
        thread.start()
        atexit.register(self.release)
        return True

    def _keepalive(self):
        while not self._stop.wait(self._interval):
            if self._refresh() != 0:
                return

    def release(self):
        if self._stop is not None:
            self._stop.set()


PRIVILEGED_COMMANDS = ("run", "verify")


def acquire_privilege(ctx, command, dry_run):
    if command not in PRIVILEGED_COMMANDS or ctx.agent:
        return False
    if command == "verify":
        why = "`verify` re-measures the notifier as the %s role account and as root. It changes nothing."
    elif dry_run:
        why = "`run --dry-run` measures the notifier as the %s role account and as root. It changes nothing."
    else:
        why = ("`run` writes the token and the config as the %s role account, and installs one "
               "system LaunchDaemon. It never loads it.")
    ctx.sudo.acquire(why % ctx.account, command + (" --dry-run" if dry_run else ""))
    return True


# --------------------------------------------------------------------------- #
# The context every step is handed
# --------------------------------------------------------------------------- #
class Ctx(object):
    def __init__(self, conf, runner, state, env=None, tty=False, transport_factory=None,
                 token_reader=None, sudo=None):
        self.conf = conf
        self.runner = runner
        self.state = state
        self.env = os.environ if env is None else env
        self.agent = agent_markers(self.env)
        self.tty = bool(tty) and not self.agent
        self.transport_factory = transport_factory or TrackerReader
        self.token_reader = token_reader or (lambda prompt: getpass.getpass(prompt))
        self.sudo = sudo or SudoOnce()
        self.may_prompt = True
        self.record = not self.agent
        self.role_home = None
        self.ids = json.loads(json.dumps(state.data.get("ids") or {}))
        self.notes_out = {}
        self.held = []
        self.composed = None
        self.config_current = False
        self.job_current = False
        self.plist_changed = False
        self.loaded = None

    @property
    def account(self):
        return self.conf["ROLE_ACCOUNT"]

    def scrub(self, text):
        return scrub(text, self.held)

    def not_measured(self, what):
        return Unknown(
            "NOT MEASURED — this is an agent environment (%s set), and %s needs `sudo`, which "
            "nothing here runs under a model. A person running the same command measures it."
            % (", ".join(self.agent), what),
            "run the same command in your own terminal")

    def as_role(self, script, what, stdin=None, why=None, secret_stdin=False, timeout=300):
        if self.agent:
            raise self.not_measured(what)
        return self.runner.as_role(self.account, script, stdin=stdin, why=why, timeout=timeout,
                                   secret_stdin=secret_stdin)

    def as_root(self, argv, what, why=None):
        if self.agent:
            raise self.not_measured(what)
        return self.runner.as_root(argv, why=why)


def _last_line(text):
    lines = [l.strip() for l in (text or "").strip().splitlines() if l.strip()]
    return lines[-1] if lines else "(no output)"


# --------------------------------------------------------------------------- #
# Steps. Each returns (ok, detail, notes) or raises Blocked / Unknown / SetupError.
# ok=True is ALREADY-DONE; ok=False is DONE under `run` and WOULD-CHANGE otherwise.
# --------------------------------------------------------------------------- #
def step_preflight(ctx, apply_it):
    conf, r = ctx.conf, ctx.runner
    problems, measured, unmeasured, notes = [], ["conf valid"], [], []

    home = r.read(["dscl", ".", "-read", "/Users/" + ctx.account, "NFSHomeDirectory"])
    if home.ok and "NFSHomeDirectory:" in home.out:
        value = home.out.split("NFSHomeDirectory:", 1)[1].strip().splitlines()[0].strip()
        if value.startswith("/") and value != "/" and _PATH_RE.match(value):
            ctx.role_home = value
            measured.append("role home %s" % value)
            problems.extend(path_problems(conf, value))
        else:
            problems.append("the directory service names %r as %s's home, which this installer "
                            "will not put in a plist" % (value, ctx.account))
    else:
        problems.append("the role account %r does not resolve (`dscl . -read /Users/%s`). This "
                        "installer creates no account: install the dispatcher and Stage E first"
                        % (ctx.account, ctx.account))

    selftest_run = r.read([sys.executable, os.path.join(HERE, NOTIFIER_SCRIPT), "--selftest"],
                          timeout=300)
    last = _last_line((selftest_run.out or "") + "\n" + (selftest_run.err or ""))
    if selftest_run.ok:
        measured.append("the notifier's own selftest passed from this checkout (%s)" % last[:60])
    else:
        problems.append("the notifier's own selftest FAILED from this checkout (exit %d): %s — "
                        "the job would run code that does not pass its own battery"
                        % (selftest_run.rc, last[:160]))

    for key, rule in (("DISPATCHER_ENV_FILE", "ENV_FILE is not the dispatcher's own env file"),
                      ("DISPATCHER_STATE_ROOT", "no notifier path is under the dispatcher's "
                                                "state root")):
        if not conf.get(key):
            notes.append("%s is empty, so the rule '%s' was NOT checked against a path. Set it in "
                         "notifier.conf to have it checked." % (key, rule))

    clone = conf["ROLE_KIT_CLONE"].rstrip("/")
    if ctx.agent:
        unmeasured.append("whether %s/scripts carries %s, and whether %s can run /usr/bin/python3 "
                          "— both need `sudo -u %s`"
                          % (clone, ", ".join(REQUIRED_SCRIPTS), ctx.account, ctx.account))
    else:
        script = ('c=%s; for s in %s; do if [ -f "$c/$s" ]; then echo "have $s"; '
                  'else echo "missing $s"; fi; done'
                  % (_q(clone + "/scripts"), " ".join(REQUIRED_SCRIPTS)))
        res = r.as_role(ctx.account, script)
        if not res.ok:
            unmeasured.append("the clone could not be read as %s (exit %d): %s"
                              % (ctx.account, res.rc, (res.err or res.out).strip()[:160]))
        else:
            missing = [l.split(" ", 1)[1] for l in res.out.splitlines() if l.startswith("missing ")]
            if missing:
                problems.append("the role account's clone at %s lacks scripts/%s. The change that "
                                "ships the notifier is not in that clone yet: merge it, then let "
                                "the Stage E installer's `run` move the clone (its `code` step)"
                                % (clone, ", scripts/".join(missing)))
            else:
                measured.append("the clone carries the notifier and what it imports")
        py = r.as_role(ctx.account, "/usr/bin/python3 -V")
        if not py.ok:
            problems.append("%s cannot run /usr/bin/python3 (exit %d): %s — the job's command uses "
                            "Apple's interpreter by full path"
                            % (ctx.account, py.rc, (py.err or py.out).strip()[:160]))

    # A row that fails or cannot measure still says what it DID measure, and what it did not
    # check: the notes would otherwise die with the exception.
    if problems:
        raise SetupError("preflight found %d problem(s) — all of them, in one pass:\n%s%s\n"
                         "  MEASURED: %s%s"
                         % (len(problems), "\n".join("  - " + p for p in problems),
                            "".join("\n  NOT MEASURED: " + u for u in unmeasured),
                            "; ".join(measured), "".join("\n  NOT CHECKED: " + n for n in notes)))
    if unmeasured:
        raise Unknown("measured: %s. NOT MEASURED%s: %s.%s"
                      % ("; ".join(measured),
                         " (agent environment: %s set)" % ", ".join(ctx.agent) if ctx.agent else "",
                         "; ".join(unmeasured), "".join(" NOT CHECKED: " + n for n in notes)),
                      "a person runs the same command in their own terminal")
    return True, "; ".join(measured), notes


def step_slack_app(ctx, apply_it):
    rec = (ctx.state.data.get("attestations") or {}).get(A_PRIVATE)
    if not rec:
        raise Blocked("CK-N1")
    if rec.get("channel_id") != ctx.conf["CHAT_CHANNEL_ID"]:
        raise Blocked("CK-N1", "the private-channel sign-off names channel %s and the conf now "
                               "names %s — check the channel you mean and sign again"
                      % (rec.get("channel_id"), ctx.conf["CHAT_CHANNEL_ID"]))
    return True, ("signed %s by %s: a separate app, and channel %s is private"
                  % (str(rec.get("at"))[:10], rec.get("initials"), rec.get("channel_id"))), []


# The env file, judged INSIDE the role account's shell. It prints a verdict per name — ok,
# short, wrong-shape or absent — and never a value, so no credential reaches this process, its
# stdout or a transcript. `stat` is branched on `uname` (BSD `-f`, GNU `-c`), as in the Stage E
# installer, because the selftest runs this exact text through /bin/sh where CI runs.
CRED_PROBE_SH = (
    'f=@ENV@; [ -f "$f" ] || exit 9; '
    'case "$(uname)" in '
    'Darwin) m=$(stat -f %Lp "$f"); o=$(stat -f %Su "$f");; '
    '*) m=$(stat -c %a "$f"); o=$(stat -c %U "$f");; esac; '
    'chat=absent; key=absent; '
    'while IFS= read -r line || [ -n "$line" ]; do '
    'line=${line#export }; k=${line%%=*}; v=${line#*=}; '
    '[ "$k" = "$line" ] && continue; '
    'case "$k" in '
    '@CHAT@) case "$v" in xoxb-*) if [ ${#v} -ge @MIN@ ]; then chat=ok; else chat=short; fi;; '
    '*) chat=wrong-shape;; esac;; '
    '@KEY@) if [ ${#v} -ge @MIN@ ]; then key=ok; else key=short; fi;; '
    'esac; done < "$f"; '
    'printf "mode=%s owner=%s chat=%s key=%s\\n" "$m" "$o" "$chat" "$key"'
)

# The token, written by the role account's shell from STDIN. Every other line of the file is
# kept; every line for this name is replaced by one. `printf` and `read` are shell builtins, so
# the value is never in any process's argv. Exit 6 is a value the shell itself refused, 7 is no
# value on stdin, 5 is a file it could not write.
CRED_WRITE_SH = (
    'umask 077; f=@ENV@; d=$(dirname "$f"); '
    '[ -d "$d" ] || mkdir -p -m 700 "$d" || exit 5; '
    'IFS= read -r v || [ -n "$v" ] || exit 7; '
    'case "$v" in xoxb-*) ;; *) exit 6;; esac; [ ${#v} -ge @MIN@ ] || exit 6; '
    't="$f.notifier-setup.$$"; '
    '( if [ -f "$f" ]; then grep -v -E "^(export[[:space:]]+)?@CHAT@=" "$f"; '
    '[ $? -le 1 ] || exit 5; fi; printf "%s=%s\\n" "@CHAT@" "$v" ) > "$t" '
    '|| { rm -f "$t"; exit 5; }; '
    'chmod 600 "$t" && mv -f "$t" "$f" || { rm -f "$t"; exit 5; }'
)


def _cred_script(template, conf):
    return (template.replace("@ENV@", _q(conf["ENV_FILE"]))
            .replace("@CHAT@", conf["CHAT_TOKEN_ENV"])
            .replace("@KEY@", conf["LINEAR_KEY_ENV"])
            .replace("@MIN@", str(TOKEN_MIN_LEN)))


def parse_cred_probe(text):
    for line in (text or "").splitlines():
        if line.startswith("mode="):
            return dict(p.split("=", 1) for p in line.split() if "=" in p)
    return None


_VERDICT = {"absent": "is not in the file", "short": "is under %d characters" % TOKEN_MIN_LEN,
            "wrong-shape": "does not start %s — a bot token does; a user token would post as a "
                           "person" % TOKEN_PREFIX}


def _probe_credentials(ctx):
    conf = ctx.conf
    res = ctx.as_role(_cred_script(CRED_PROBE_SH, conf),
                      what="reading %s (names, lengths and shapes — never a value)"
                           % conf["ENV_FILE"])
    if res.rc == 9:
        return {"mode": "", "owner": "", "chat": "absent", "key": "absent", "exists": False}
    if not res.ok:
        raise Unknown("the env file %s could not be read as %s (exit %d): %s"
                      % (conf["ENV_FILE"], ctx.account, res.rc,
                         ctx.scrub((res.err or res.out).strip())[:200]),
                      "fix that and run the same command again. Nothing was written: an "
                      "unreadable env file is not an absent one.")
    facts = parse_cred_probe(res.out)
    if not facts:
        raise Unknown("the env-file probe printed no verdict", "run the same command again")
    facts["exists"] = True
    return facts


def step_credentials(ctx, apply_it):
    conf = ctx.conf
    chat_env, key_env = conf["CHAT_TOKEN_ENV"], conf["LINEAR_KEY_ENV"]
    facts = _probe_credentials(ctx)
    if facts.get("key") != "ok":
        raise SetupError(
            "%s %s %s. The tracker key belongs to the Stage E installer, which writes that file: "
            "run `python3 scripts/pipeline_stage_e_setup.py run` first. This installer never asks "
            "for it and never writes it."
            % (key_env, "is not in" if facts.get("key") == "absent" else "is too short in",
               conf["ENV_FILE"] if facts["exists"] else conf["ENV_FILE"] + " (there is no such file)"))
    if facts.get("owner") != ctx.account:
        raise SetupError("%s is owned by %r, not %r. A chmod does not fix an owner, and rewriting "
                         "someone else's credential file is not this installer's to do"
                         % (conf["ENV_FILE"], facts.get("owner"), ctx.account))
    if facts.get("chat") == "ok":
        if facts.get("mode") == "600":
            return True, ("%s: mode 600, owned by %s; %s and %s both set — shapes judged in that "
                          "account's shell, no value read" % (conf["ENV_FILE"], ctx.account,
                                                              chat_env, key_env)), []
        if not apply_it:
            return False, "%s is mode %s, want 600" % (conf["ENV_FILE"], facts.get("mode")), []
        res = ctx.as_role("chmod 600 %s" % _q(conf["ENV_FILE"]), what="tightening the env file",
                          why="tighten %s to mode 600" % conf["ENV_FILE"])
        if not res.ok:
            raise SetupError("could not tighten %s to mode 600 (exit %d): %s"
                             % (conf["ENV_FILE"], res.rc, (res.err or "").strip()[:200]))
        return False, "%s re-tightened to mode 600" % conf["ENV_FILE"], []

    verdict = _VERDICT.get(facts.get("chat"), "is not usable")
    if not apply_it:
        return False, ("%s %s — `run` would ask for it at a hidden prompt and write it, mode 600"
                       % (chat_env, verdict)), []
    if not (ctx.tty and ctx.may_prompt):
        raise Blocked("CK-N2", "%s %s, and this run has no terminal to ask at" % (chat_env, verdict))

    say("")
    say("  %s %s. It goes to the role account's shell on stdin, which writes it into" % (chat_env, verdict))
    say("  %s at mode 600 and keeps every other line. Never into the dispatcher's own env" % conf["ENV_FILE"])
    say("  file, which is copied unscrubbed into every session; never under the dispatcher's")
    say("  state root, where session readability is unmeasured. Nothing is echoed or kept here.")
    val = (ctx.token_reader("  paste %s — the notifier's OWN bot token, xoxb-... (hidden): "
                            % chat_env) or "").strip()
    say("  %s: %d chars, %s" % (chat_env, len(val), "starts %s" % TOKEN_PREFIX
                                if val.startswith(TOKEN_PREFIX) else "does NOT start %s" % TOKEN_PREFIX))
    if len(val) < TOKEN_MIN_LEN or not _TOKEN_RE.match(val):
        raise SetupError("%s is %d characters and %s. Nothing was written. (The value is not shown.)"
                         % (chat_env, len(val), "is not an xoxb- bot token of letters, digits and "
                                                "dashes" if len(val) >= TOKEN_MIN_LEN else "too short"))
    ctx.held.append(val)
    res = ctx.as_role(_cred_script(CRED_WRITE_SH, conf), what="writing the token",
                      stdin=val + "\n", secret_stdin=True,
                      why="write %s into %s (mode 600), replacing any line for that name"
                          % (chat_env, conf["ENV_FILE"]))
    del val
    if res.skipped:
        return False, "would write %s into %s" % (chat_env, conf["ENV_FILE"]), []
    if not res.ok:
        raise SetupError("could not write %s into %s as %s (exit %d: %s). The file was not replaced."
                         % (chat_env, conf["ENV_FILE"], ctx.account, res.rc,
                            {5: "the file could not be written", 6: "the shell refused the value",
                             7: "no value arrived on stdin"}.get(res.rc, "see above")))
    again = _probe_credentials(ctx)
    if again.get("chat") != "ok" or again.get("mode") != "600":
        raise SetupError("%s was written, and reading it back as %s says %s, mode %s"
                         % (chat_env, ctx.account, again.get("chat"), again.get("mode")))
    return False, "%s written into %s, mode 600; every other line kept" % (chat_env, conf["ENV_FILE"]), []


def step_labels(ctx, apply_it):
    conf = ctx.conf
    actors_conf = split_list(conf["EXECUTOR_ACTOR_IDS"])
    wants_self = EXECUTOR_SELF in actors_conf
    if ctx.agent:
        raise Unknown(
            "NOT MEASURED — agent environment (%s set): no tracker request is made from a model's "
            "process, with any key, so %s%s were not resolved.%s"
            % (", ".join(ctx.agent), " and ".join(LABEL_KEYS),
               " and EXECUTOR_ACTOR_IDS=self" if wants_self else "",
               " Ids an earlier run recorded are kept, unchecked." if ctx.ids.get("label_ids") else ""),
            "a person runs the same command with $%s exported in their shell" % conf["LINEAR_KEY_ENV"])
    name = conf["LINEAR_KEY_ENV"]
    key = (ctx.env.get(name) or "").strip()
    if not key:
        raise Unknown(
            "NOT MEASURED — $%s is not set in your shell. This step reads the tracker key from YOUR "
            "environment, for this one command; it never reads the role account's file." % name,
            "export it for this shell, then run the same command again:\n"
            "    read -rs %s && export %s" % (name, name))
    ctx.held.append(key)
    api = ctx.transport_factory(key)
    try:
        viewer = api.viewer_id() if wants_self else ""
        found = dict((label, api.labels_named(label)) for label in LABEL_KEYS)
    except TrackerError as exc:
        if exc.rejected:
            raise SetupError("the tracker refused the key in $%s (%s). Nothing was resolved. (The "
                             "value is not shown.)" % (name, ctx.scrub(str(exc))))
        raise Unknown("could not ask the tracker: %s" % ctx.scrub(str(exc)),
                      "run the same command again when the tracker can be reached")

    problems, label_ids = [], {}
    for label in LABEL_KEYS:
        exact = [n for n in found[label] if n.get("name") == label]
        workspace = [n for n in exact if not n.get("team")]
        if len(workspace) == 1 and workspace[0].get("id"):
            label_ids[label] = str(workspace[0]["id"])
        elif len(workspace) > 1:
            problems.append("%d workspace labels are named %s; the notifier needs exactly one id"
                            % (len(workspace), label))
        elif exact:
            problems.append("%s exists only as a TEAM label (%s). The pipeline contract wants it "
                            "workspace-scoped (§6), and a team label cannot land on another team's "
                            "ticket; `/setup-board` reports it as a rescope"
                            % (label, ", ".join(sorted(str(n.get("team")) for n in exact))))
        else:
            problems.append("%s does not exist in this workspace. `/setup-board` creates it (the "
                            "pipeline contract's §6 labels); this installer never creates a label"
                            % label)
    actors = []
    for actor in actors_conf:
        value = viewer if actor == EXECUTOR_SELF else actor
        if not value:
            problems.append("EXECUTOR_ACTOR_IDS=self, and the tracker named no user for the key")
        elif value not in actors:
            actors.append(value)
    if problems:
        raise SetupError("the labels step found %d problem(s):\n%s"
                         % (len(problems), "\n".join("  - " + p for p in problems)))
    before = ctx.ids.get("label_ids") or {}
    changed = [k for k in LABEL_KEYS if before.get(k) and before.get(k) != label_ids[k]]
    ctx.ids = {"label_ids": label_ids, "executor_actor_ids": actors}
    detail = ("%s resolved by exact name at workspace scope (%s); %d executor author id(s)%s"
              % (" and ".join(LABEL_KEYS),
                 ", ".join("%s…" % label_ids[k][:8] for k in LABEL_KEYS), len(actors),
                 ", `self` looked up" if wants_self else ""))
    if changed:
        detail += "; CHANGED since the ledger: %s" % ", ".join(changed)
    return True, detail, []


CONFIG_READ_SH = ('f=@F@; [ -f "$f" ] || exit 9; case "$(uname)" in '
                  'Darwin) m=$(stat -f %Lp "$f");; *) m=$(stat -c %a "$f");; esac; '
                  'printf "mode=%s\\n" "$m"; cat "$f"')
CONFIG_WRITE_SH = ('umask 077; f=@F@; d=$(dirname "$f"); [ -d "$d" ] || mkdir -p -m 700 "$d" || exit 5; '
                   't="$f.notifier-setup.$$"; '
                   '{ cat > "$t" && chmod 600 "$t" && mv -f "$t" "$f"; } || { rm -f "$t"; exit 5; }')


def _read_config_file(ctx):
    """(content, mode) — content None when the file is absent."""
    res = ctx.as_role(CONFIG_READ_SH.replace("@F@", _q(ctx.conf["NOTIFIER_CONFIG"])),
                      what="reading %s to compare it byte for byte" % ctx.conf["NOTIFIER_CONFIG"])
    if res.rc == 9:
        return None, ""
    if not res.ok:
        raise Unknown("%s could not be read as %s (exit %d): %s"
                      % (ctx.conf["NOTIFIER_CONFIG"], ctx.account, res.rc,
                         ctx.scrub((res.err or "").strip())[:160]),
                      "fix that and run the same command again")
    first, _sep, rest = res.out.partition("\n")
    return rest, first.split("=", 1)[1] if first.startswith("mode=") else ""


def step_config(ctx, apply_it):
    conf = ctx.conf
    label_ids = ctx.ids.get("label_ids") or {}
    actors = ctx.ids.get("executor_actor_ids") or []
    missing = [k for k in LABEL_KEYS if not label_ids.get(k)] + ([] if actors else ["the executor ids"])
    if missing:
        what = "waits on the labels step: %s not resolved yet" % ", ".join(missing)
        if apply_it:
            raise Unknown(what, "clear the labels row first")
        return False, what, []
    body = validate_composed(compose_config(conf, label_ids, actors), ctx.role_home)
    sha = _sha(body)
    ctx.composed = {"body": body, "sha256": sha}
    have, mode = _read_config_file(ctx)
    if have == body and mode == "600":
        ctx.config_current = True
        ctx.notes_out[NOTE_CONFIG] = sha
        return True, ("%s is current (sha256 %s…), mode 600, and the notifier's own loader accepts "
                      "it" % (conf["NOTIFIER_CONFIG"], sha[:12])), []
    why = "absent" if have is None else ("differs" if have != body else "mode %s, want 600" % mode)
    if not apply_it:
        return False, ("would write %s (%s); the composition passes the notifier's own loader"
                       % (conf["NOTIFIER_CONFIG"], why)), []
    res = ctx.as_role(CONFIG_WRITE_SH.replace("@F@", _q(conf["NOTIFIER_CONFIG"])),
                      what="writing the notifier's config", stdin=body,
                      why="write the notifier's config %s (mode 600)" % conf["NOTIFIER_CONFIG"])
    if res.skipped:
        return False, "would write %s" % conf["NOTIFIER_CONFIG"], []
    if not res.ok:
        raise SetupError("could not write %s as %s (exit %d): %s"
                         % (conf["NOTIFIER_CONFIG"], ctx.account, res.rc,
                            (res.err or "").strip()[:200]))
    have, mode = _read_config_file(ctx)
    if have != body or mode != "600":
        raise SetupError("%s was written, and reading it back as %s does not match (mode %s)"
                         % (conf["NOTIFIER_CONFIG"], ctx.account, mode))
    ctx.config_current = True
    ctx.notes_out[NOTE_CONFIG] = sha
    return False, "wrote %s (%s; sha256 %s…, mode 600)" % (conf["NOTIFIER_CONFIG"], why, sha[:12]), []


def step_job(ctx, apply_it):
    conf = ctx.conf
    if not ctx.role_home:
        what = "waits on preflight: the role account's home is not known, and the plist names it"
        if apply_it:
            raise Unknown(what, "clear the preflight row first")
        return False, what, []
    body = render_plist(conf, ctx.role_home)
    try:
        plistlib.loads(body.encode("utf-8"))
    except Exception as exc:                                      # noqa: BLE001
        raise SetupError("the plist this installer rendered does not parse (%s); nothing was "
                         "installed" % exc)
    path = plist_path(conf)
    # A LaunchDaemon plist is root:wheel 644, so reading it needs no sudo — which is also why
    # this row is measurable under a model.
    exists = ctx.runner.read(["test", "-e", path])
    have = None
    if exists.ok:
        got = ctx.runner.read(["cat", path])
        if not got.ok:
            raise Unknown("%s exists and could not be read (exit %d)" % (path, got.rc),
                          "fix its permissions (root:wheel 644) and run the same command again")
        have = got.out
    elif exists.rc != 1:
        raise Unknown("could not tell whether %s exists (exit %d)" % (path, exists.rc),
                      "run the same command again")
    if have == body:
        ctx.job_current = True
        return True, "%s is installed and current; this installer never loads it (CK-N3)" % path, []
    why = "absent" if have is None else "differs"
    if not apply_it:
        return False, "would install %s (%s), not loaded" % (path, why), []
    if ctx.agent:
        raise ctx.not_measured("installing %s" % path)
    se._install_plist(ctx.runner, conf["JOB_LABEL"], body)
    got = ctx.runner.read(["cat", path])
    if not got.ok or got.out != body:
        raise SetupError("%s was installed, and reading it back does not match" % path)
    ctx.job_current = True
    ctx.plist_changed = True
    return False, "installed %s (%s), root:wheel 644 — NOT loaded (CK-N3)" % (path, why), []


def heartbeat_file(conf):
    return notify.heartbeat_path({"state_dir": conf["STATE_DIR"]})


def read_heartbeat(ctx):
    """(doc, why_not) — `why_not` is "absent" for no file, anything else for could-not-read."""
    res = ctx.as_role('f=%s; [ -f "$f" ] || exit 9; cat "$f"' % _q(heartbeat_file(ctx.conf)),
                      what="reading the notifier's heartbeat")
    if res.rc == 9:
        return None, "absent"
    if not res.ok:
        return None, "unreadable as %s (exit %d)" % (ctx.account, res.rc)
    try:
        doc = json.loads(res.out)
    except ValueError:
        return None, "not JSON"
    if not isinstance(doc, dict) or doc.get("schema") != notify.HEARTBEAT_SCHEMA:
        return None, "not a %s document" % notify.HEARTBEAT_SCHEMA
    return doc, None


def step_dry_run(ctx, apply_it):
    """The notifier, once, as the role account, through the job's own command plus `--dry-run`.
    Exit 0 or 3 is done; 1, 2 or 4 is a failure carrying the notifier's own words, scrubbed.

    A pass that already ran against this exact config and command is not re-run: its binding is
    in the ledger, and the notifier's latest heartbeat is read instead. Re-running it on every
    `run` would overwrite a loaded job's real heartbeat with a rehearsal's. `verify` and a dry run
    never run it at all — they read that heartbeat, and a failing latest pass is a failed row."""
    conf = ctx.conf
    command = job_command(conf) + " --dry-run"
    if not (ctx.config_current and ctx.job_current):
        waits = [n for n, ok in (("config", ctx.config_current), ("job", ctx.job_current)) if not ok]
        what = ("waits on %s: the dry run runs the job's own command against the files they write"
                % " and ".join(waits))
        if apply_it:
            raise Unknown(what, "clear those rows first")
        return False, what, []
    bind = {"config_sha256": ctx.composed["sha256"], "command_sha256": _sha(command)}
    note = (ctx.state.data.get("notes") or {}).get(NOTE_DRY_RUN) or {}
    bound = all(note.get(k) == v for k, v in bind.items()) and note.get("exit") in DRY_RUN_OK
    if bound:
        beat, why_not = read_heartbeat(ctx)
        if beat is not None:
            kind = "a dry run" if beat.get("dry") else "a real pass"
            if beat.get("exit") in DRY_RUN_OK:
                return True, ("passed as %s at %s (exit %s); the notifier's latest pass was %s at "
                              "%s, exit %s" % (ctx.account, str(note.get("at"))[:16], note.get("exit"),
                                              kind, str(beat.get("at"))[:16], beat.get("exit"))), []
            if not apply_it:
                raise SetupError("the notifier's latest pass (%s, at %s) exited %s: %s"
                                 % (kind, beat.get("at"), beat.get("exit"),
                                    ctx.scrub(str(beat.get("summary")))[:300]))
        elif why_not != "absent":
            raise Unknown("the notifier's heartbeat is %s" % why_not, "run the same command again")
    if not apply_it:
        return False, "would run the notifier once as %s: %s" % (ctx.account, command), []
    res = ctx.as_role(command, what="running the notifier's dry run",
                      timeout=int(conf["RUN_TIMEOUT_SECONDS"]) + 60)
    lines = ctx.scrub(((res.out or "") + (res.err or "")).strip()).splitlines()
    say("")
    say("  -- the notifier's dry run, as %s (exit %d) --" % (ctx.account, res.rc))
    for line in lines[-14:]:
        say("    " + line[:200])
    if res.rc in DRY_RUN_OK:
        if res.rc == notify.EXIT_DECLINED:
            say("  Exit 3 is a DECLINE the notifier names in its summary above: a title withheld")
            say("  for a credential shape, or events over the per-pass cap. It ran; read them.")
        ctx.notes_out[NOTE_DRY_RUN] = dict(bind, exit=res.rc, at=se.now_iso())
        return False, ("the notifier's dry run exited %d as %s: %s"
                       % (res.rc, ctx.account, (lines[-1] if lines else "(no output)")[:120])), []
    raise SetupError("the notifier's dry run FAILED (exit %d) as %s. Its own words:\n%s"
                     % (res.rc, ctx.account,
                        "\n".join("  " + l[:200] for l in lines[-8:]) or "  (it printed nothing)"))


def step_enable(ctx, apply_it):
    label = ctx.conf["JOB_LABEL"]
    res = ctx.as_root(["launchctl", "print", "system/" + label],
                      what="asking launchd whether system/%s is loaded" % label)
    text = (res.out or "") + (res.err or "")
    if res.ok:
        loaded = True
    elif res.rc == 113 or "Could not find service" in text:
        loaded = False
    else:
        raise Unknown("could not ask launchd about system/%s (exit %d): %s"
                      % (label, res.rc, text.strip()[:160]), "run the same command again")
    ctx.loaded = loaded
    if loaded and ctx.plist_changed:
        raise Blocked("CK-N3", "system/%s is loaded and this run changed its plist, so the loaded "
                               "job still runs the old one — unload it, then load it" % label)
    if loaded:
        return True, "system/%s is loaded: a pass every %s s as %s" % (
            label, ctx.conf["INTERVAL_SECONDS"], ctx.account), []
    raise Blocked("CK-N3", "system/%s is not loaded; this installer never loads it" % label)


def step_first_ping(ctx, apply_it):
    rec = (ctx.state.data.get("attestations") or {}).get(A_FIRST_PING)
    if not rec:
        raise Blocked("CK-N4")
    sha = (ctx.composed or {}).get("sha256")
    if not sha:
        raise Unknown("the config step did not compose the notifier's config on this pass, so the "
                      "first-ping sign-off cannot be matched to it. A match an earlier pass made "
                      "says nothing about this one.", "clear the config row, then run again")
    if rec.get("config_sha256") != sha:
        raise Blocked("CK-N4", "the config changed since the first ping was watched (signed under "
                               "%s…, the config is now %s…) — watch one again and sign again"
                      % (str(rec.get("config_sha256"))[:12], sha[:12]))
    return True, ("one throwaway escalation was watched end to end (signed %s by %s), under the "
                  "config the job runs" % (str(rec.get("at"))[:10], rec.get("initials"))), []


def handover_lines(ctx):
    conf = ctx.conf
    loaded = {True: "LOADED — one pass every %s s as %s, paging channel %s"
                    % (conf["INTERVAL_SECONDS"], ctx.account, conf["CHAT_CHANNEL_ID"]),
              False: "NOT loaded (card CK-N3)"}.get(ctx.loaded, "not measured on this pass")
    on = ["the notifier job system/%s: %s (KIT-116)" % (conf["JOB_LABEL"], loaded),
          "its only tracker write: %s, added on the marks that name them (KIT-116)"
          % " and ".join(LABEL_KEYS)]
    off = ["the daemon-health page: not built, so a stopped Stage E daemon still pages nobody "
           "here (KIT-156)",
           "replies: no component reads the channel, so a reply there reaches nobody; answer in "
           "the tracker (KIT-118)"]
    unproven = ["nothing watches the notifier's own heartbeat: a dead notifier is silent (KIT-156)",
                "what a session can reach with the dispatcher's own Slack token, and the "
                "copy-it-out residual no fence closes (KIT-157)"]
    return on, off, unproven


def step_handover(ctx, apply_it):
    on, off, unproven = handover_lines(ctx)
    notes = (["ON: " + l for l in on] + ["OFF: " + l for l in off]
             + ["NOT PROVEN: " + l for l in unproven])
    return True, "%d on, %d off, %d not proven — each named below" % (
        len(on), len(off), len(unproven)), notes


STEPS = (
    ("preflight", "the conf, the role account, the clone and the notifier's selftest", step_preflight),
    ("slack-app", "a separate Slack app and a private channel (CK-N1)", step_slack_app),
    ("credentials", "the Slack token in the role account's env file", step_credentials),
    ("labels", "the two lifecycle label ids, and `self`", step_labels),
    ("config", "the notifier's config, accepted by its own loader", step_config),
    ("job", "the LaunchDaemon plist, installed and not loaded", step_job),
    ("dry-run", "the notifier once, as the role account, with --dry-run", step_dry_run),
    ("enable", "the job loaded — by you (CK-N3)", step_enable),
    ("first-ping", "one throwaway escalation, watched (CK-N4)", step_first_ping),
    ("handover", "what is on, off, and not proven", step_handover),
)

_SEVERITY = (FAILED, UNKNOWN, BLOCKED, WOULD_CHANGE, DONE, ALREADY_DONE, SKIPPED)
_EXIT = {FAILED: EX_FAILED, UNKNOWN: EX_UNKNOWN, BLOCKED: EX_BLOCKED, WOULD_CHANGE: EX_BLOCKED,
         DONE: EX_OK, ALREADY_DONE: EX_OK, SKIPPED: EX_OK}


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def _record(ctx, sid, outcome, detail, card=None):
    ctx.state.data.setdefault("steps", {})[sid] = {
        "outcome": outcome, "detail": detail[:400], "card": card, "at": se.now_iso()}


def _finish(ctx, command):
    if not ctx.record:
        return
    ctx.state.data["ids"] = ctx.ids
    ctx.state.notes().update(ctx.notes_out)
    ctx.state.data["recorded_by"] = {"command": command, "at": se.now_iso()}
    ctx.state.save()


def _print_rows(rows, notes=()):
    say("")
    say("-- steps --")
    for sid, outcome, detail in rows:
        say("  %-12s %-17s %s" % (sid, outcome, detail.splitlines()[0][:110] if detail else ""))
    for sid, note in notes:
        say("")
        say("  note from `%s`:" % sid)
        for line in se._wrap(note):
            say("    " + line)


def run_steps(ctx, apply_it, keep_going=False, resume="run", steps=None):
    """(exit_code, rows). `run` stops at the first row a person must clear; a dry run and `verify`
    keep going. An exception no step anticipated is a FAILED row, never a traceback."""
    rows, deferred, row_notes = [], [], []
    for sid, _title, fn in (steps or STEPS):
        try:
            ok, detail, notes = fn(ctx, apply_it)
            detail = ctx.scrub(detail)
            row_notes.extend((sid, ctx.scrub(str(n))) for n in (notes or ()))
        except Blocked as exc:
            extra = ctx.scrub(exc.extra) if exc.extra else CARDS[exc.card_id]["title"]
            rows.append((sid, BLOCKED, extra))
            _record(ctx, sid, BLOCKED, extra, exc.card_id)
            if keep_going:
                deferred.append(("card", exc.card_id, sid))
                continue
            _finish(ctx, resume)
            _print_rows(rows, row_notes)
            print_card(exc.card_id, ctx.conf)
            say("Do that, then run the same command again — it checks your work and carries on:")
            say("    python3 %s %s" % (_self_path(), resume))
            return EX_BLOCKED, rows
        except Unknown as exc:
            what = ctx.scrub(exc.what)
            safe = Unknown(what, ctx.scrub(exc.remedy))
            rows.append((sid, UNKNOWN, what))
            _record(ctx, sid, UNKNOWN, what)
            if keep_going:
                deferred.append(("unknown", safe, sid))
                continue
            _finish(ctx, resume)
            _print_rows(rows, row_notes)
            se._print_unknown(sid, safe)
            return EX_UNKNOWN, rows
        except Exception as exc:                                  # noqa: BLE001
            if isinstance(exc, (SetupError, Refusal)):
                message = ctx.scrub(str(exc))
            else:
                message = ctx.scrub("unexpected %s: %s" % (type(exc).__name__, exc))
            rows.append((sid, FAILED, message.splitlines()[0] if message else ""))
            _record(ctx, sid, FAILED, message)
            if keep_going:
                deferred.append(("failed", message, sid))
                continue
            _finish(ctx, resume)
            _print_rows(rows, row_notes)
            say("")
            say("FAILED at step `%s`:" % sid)
            for line in message.splitlines():
                say("  " + line)
            return EX_FAILED, rows
        outcome = ALREADY_DONE if ok else (DONE if apply_it else WOULD_CHANGE)
        rows.append((sid, outcome, detail))
        _record(ctx, sid, outcome, detail)
    _finish(ctx, resume)
    _print_rows(rows, row_notes)
    for kind, payload, sid in deferred:
        say("")
        if kind == "card":
            say("  step `%s` waits on checkpoint %s — read it with:" % (sid, payload))
            say("      python3 %s card %s" % (_self_path(), payload))
        elif kind == "unknown":
            se._print_unknown(sid, payload)
        else:
            say("FAILED at step `%s`:" % sid)
            for line in payload.splitlines():
                say("  " + line)
    worst = next((s for s in _SEVERITY if any(o == s for _sid, o, _d in rows)), ALREADY_DONE)
    return _EXIT[worst], rows


def _banner(ctx, title):
    conf = ctx.conf
    say(title)
    say("  conf          %s" % conf.get("__source__", DEFAULT_CONF))
    say("  role account  %s" % ctx.account)
    say("  job           system/%s (%s) — never loaded by this installer" % (conf["JOB_LABEL"], plist_path(conf)))
    say("  channel       %s" % conf["CHAT_CHANNEL_ID"])
    say("  token         $%s, in %s as %s" % (conf["CHAT_TOKEN_ENV"], conf["ENV_FILE"], ctx.account))
    say("  tracker key   $%s from YOUR shell, for the labels step only" % conf["LINEAR_KEY_ENV"])
    if ctx.agent:
        say("  AGENT ENVIRONMENT (%s set): nothing is asked for, nothing needing sudo is" % ", ".join(ctx.agent))
        say("  attempted, no tracker request is made, and nothing is recorded. Those rows say")
        say("  NOT MEASURED. A person's shell measures them.")
    say("  utc           %s" % se.now_iso())


def cmd_run(ctx, dry_run):
    if not dry_run:
        refuse_if_agent("run", ctx.env)
    if dry_run:
        ctx.may_prompt = False
    _banner(ctx, "Notifier installer — %s" % ("DRY RUN: nothing will be changed" if dry_run
                                             else "the only command that changes anything"))
    code, rows = run_steps(ctx, apply_it=not dry_run, keep_going=dry_run,
                           resume="run --dry-run" if dry_run else "run")
    if dry_run:
        say("")
        if ctx.runner.writes:
            say("BUG: a dry run recorded %d write(s); that is a defect in this file."
                % len(ctx.runner.writes))
            return EX_FAILED
        would = [s for s, o, _d in rows if o == WOULD_CHANGE]
        unmeasured = [s for s, o, _d in rows if o == UNKNOWN]
        failed = [s for s, o, _d in rows if o == FAILED]
        blocked = [s for s, o, _d in rows if o == BLOCKED]
        say("DRY RUN: nothing was changed — not as the role account, not in launchd, not in the tracker.")
        say("%d of %d step(s) would change something%s." % (len(would), len(STEPS),
                                                          " (%s)" % ", ".join(would) if would else ""))
        if blocked:
            say("%d step(s) wait on a person (%s) — each names its card." % (len(blocked), ", ".join(blocked)))
        if unmeasured:
            say("%d step(s) could NOT be measured (%s) — each row says why; not measured is not "
                "passed." % (len(unmeasured), ", ".join(unmeasured)))
        if failed:
            say("%d step(s) FAILED (%s) — the reasons are printed above." % (len(failed), ", ".join(failed)))
        if not ctx.record:
            say("Nothing was recorded: an agent environment writes no ledger.")
        say("Nothing here asked for a credential. Run the same command again after clearing a row:")
        say("    python3 %s run --dry-run" % _self_path())
        return code
    if code == EX_OK:
        say("")
        say("DONE — every step holds. Nothing merged, nothing approved, no label applied by this installer.")
    return code


def cmd_verify(ctx):
    """Re-measures EVERY step, never stops early, changes nothing, records nothing, and never
    raises: an escaped exception becomes exit 1 with its name, not a traceback."""
    try:
        ctx.may_prompt = False
        ctx.record = False
        ctx.runner.dry_run = True
        _banner(ctx, "Notifier verify — read-only. Nothing is changed or recorded, and no credential "
                     "is asked for.")
        code, rows = run_steps(ctx, apply_it=False, keep_going=True, resume="run")
        say("")
        if ctx.runner.writes:
            say("BUG: verify recorded %d write(s); that is a defect in this file." % len(ctx.runner.writes))
            return EX_FAILED
        if code == EX_OK:
            say("No drift: every step still measures as done.")
        else:
            counts = {}
            for _sid, outcome, _d in rows:
                counts[outcome] = counts.get(outcome, 0) + 1
            say("Outstanding: " + ", ".join("%d %s" % (n, o) for o, n in sorted(counts.items())
                                            if o not in (DONE, ALREADY_DONE, SKIPPED)))
            say("The one command that moves it forward:  python3 %s run" % _self_path())
        return code
    except Exception as exc:                                      # noqa: BLE001
        say("BUG: verify raised %s: %s" % (type(exc).__name__, scrub(str(exc), ctx.held)))
        return EX_FAILED


def cmd_status(state):
    """Replays the ledger. It probes nothing."""
    say("")
    say("=" * 74)
    say(" Notifier install status        %s" % se.now_iso())
    say("=" * 74)
    if not os.path.exists(state.path):
        say("")
        say("No ledger at %s: no `run` or `run --dry-run` has recorded anything here." % state.path)
    rec = state.data.get("recorded_by") or {}
    if rec:
        say("  last recorded by `%s` at %s. `verify` re-measures and records nothing."
            % (rec.get("command"), rec.get("at")))
    say("")
    say("-- recorded steps --")
    blocking = []
    for sid, title, _fn in STEPS:
        row = (state.data.get("steps") or {}).get(sid) or {}
        outcome = row.get("outcome") or "not run"
        say("  %-12s %-17s %s" % (sid, outcome, str(row.get("detail") or title)[:100]))
        if outcome not in (DONE, ALREADY_DONE, SKIPPED):
            blocking.append((sid, outcome, row))
    say("")
    say("-- sign-offs --")
    for aid, what in sorted(ATTESTATIONS.items()):
        signed = (state.data.get("attestations") or {}).get(aid)
        if signed:
            bound = signed.get("channel_id") or ("config %s…" % str(signed.get("config_sha256"))[:12])
            say("  %-18s signed %s by %s, for %s" % (aid, str(signed.get("at"))[:10],
                                                     signed.get("initials"), bound))
        else:
            say("  %-18s NOT SIGNED — %s" % (aid, what))
    ids = state.data.get("ids") or {}
    if ids.get("label_ids"):
        say("")
        say("-- resolved ids --")
        for key, value in sorted((ids.get("label_ids") or {}).items()):
            say("  %-18s %s…" % (key, str(value)[:8]))
        say("  %-18s %d" % ("executor ids", len(ids.get("executor_actor_ids") or [])))
    say("")
    if not blocking:
        say("Every recorded step holds. Re-measure against the live machine with:")
        say("    python3 %s verify" % _self_path())
        return EX_OK
    sid, outcome, row = blocking[0]
    say("%s at `%s`." % ({BLOCKED: "BLOCKED ON A PERSON", FAILED: "FAILED", UNKNOWN: "COULD NOT MEASURE",
                          WOULD_CHANGE: "WORK OUTSTANDING"}.get(outcome, "NOT RUN"), sid))
    if outcome == BLOCKED and row.get("card") in CARDS:
        say("  %s" % CARDS[row["card"]]["title"])
        say("")
        say("THE ONE COMMAND THAT CLEARS IT:")
        say("    python3 %s card %s" % (_self_path(), row["card"]))
    else:
        for line in se._wrap(str(row.get("detail") or "nothing recorded yet")):
            say("  " + line)
        say("")
        say("THE ONE COMMAND THAT MOVES IT:")
        say("    python3 %s run" % _self_path())
    worst = next((s for s in _SEVERITY if any(o == s for _s, o, _r in blocking)), WOULD_CHANGE)
    return _EXIT.get(worst, EX_BLOCKED)


def cmd_attest(state, aid, initials, note, conf=None, conf_problem=None, env=None):
    """HUMAN ONLY. A session supplying its own sign-off is the agent producing a person's signal."""
    refuse_if_agent("attest", env)
    if aid not in ATTESTATIONS:
        raise SetupError("no such sign-off: %r (have %s)" % (aid, ", ".join(sorted(ATTESTATIONS))))
    initials = (initials or "").strip()
    if initials.lower() in se.INITIALS_PLACEHOLDERS:
        raise SetupError("--initials %r is the placeholder this file prints, not a person. Sign with "
                         "YOUR OWN 2-4 letters." % initials)
    if not re.match(r"^[A-Za-z]{2,4}$", initials):
        raise SetupError("--initials wants 2-4 letters, your own (got %r)" % initials)
    note = (note or "").strip()
    if not note:
        raise SetupError("%s needs a --note saying what you saw: %s. A sign-off with nothing in it "
                         "records a click, not a look." % (aid, ATTESTATIONS[aid]))
    bound = {}
    if aid == A_PRIVATE:
        if conf is None:
            raise SetupError("%s records the channel id in your conf, and the conf did not load: %s"
                             % (aid, conf_problem or "fix it and sign again"))
        bound["channel_id"] = conf["CHAT_CHANNEL_ID"]
    if aid == A_FIRST_PING:
        sha = (state.data.get("notes") or {}).get(NOTE_CONFIG)
        if not sha:
            raise SetupError("%s records the config the job ran under, and no config is recorded yet. "
                             "Run the installer until the config step holds, watch the ping (card "
                             "CK-N4), then sign." % aid)
        bound["config_sha256"] = sha
    state.attest(aid, initials.lower(), note, **bound)
    if not state.save():
        raise SetupError("the sign-off was NOT recorded: the ledger could not be written")
    say("recorded: %s  %s  %s  %s" % (aid, initials.lower(), se.now_iso(), note))
    say("  (%s)" % ATTESTATIONS[aid])
    if bound.get("channel_id"):
        say("  for channel %s — change the channel and this sign-off stops counting" % bound["channel_id"])
    if bound.get("config_sha256"):
        say("  under config %s… — change the config and this sign-off stops counting"
            % bound["config_sha256"][:12])
    return EX_OK


# --------------------------------------------------------------------------- #
# --selftest — offline, every transport stubbed
# --------------------------------------------------------------------------- #
def selftest():
    """The battery owns its environment: agent markers are scrubbed for the run and restored after,
    so CI and a session get the same verdict. Every case that needs a marker sets one itself."""
    saved = dict(os.environ)
    for marker in AGENT_ENV_MARKERS:
        os.environ.pop(marker, None)
    try:
        return _selftest_body()
    finally:
        os.environ.clear()
        os.environ.update(saved)


def _selftest_body():
    import contextlib
    import io
    import shutil

    checks = []

    def ok(name, cond, detail=""):
        checks.append((bool(cond), name, detail))

    def quiet(fn):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            value = fn()
        return value, out.getvalue() + err.getvalue()

    person = "selftest-person"
    # Built at runtime, never as source literals: the pre-commit scan and the PreToolUse hook read
    # this file's text, and a fixture that looks like a credential would block its own commit.
    token = "xo" + "xb-" + "4242424242-SELFTESTNOTAREALTOKEN"
    key = "lin_" + "api_" + "SELFTESTKEYNOTREAL0123456789abcdef"
    gh = "gh" + "p_" + "SELFTESTGHTOKENNOTREAL0123456789abcd"
    good_text = ("ROLE_ACCOUNT=_exnotify\nROLE_KIT_CLONE=~/.stage-e/kit\nTEAM_KEYS=KIT,TOD\n"
                 "TICKET_URL_TEMPLATE=https://linear.app/synthetic-org/issue/{id}\n"
                 "CHAT_CHANNEL_ID=C0SYNTHETIC1\nEXECUTOR_ACTOR_IDS=self\n"
                 "JOB_LABEL=com.example.notifier\n")

    def good_conf(extra=""):
        values, errors = parse_conf(good_text + extra)
        conf, more = validate_conf(values, person)
        assert not errors + more, errors + more
        conf["__source__"] = "notifier.conf"
        return conf

    # ── 1. every conf error in ONE pass, and a first-error-wins mutant turns it red ──────
    bad = ("ROLE_ACCOUNT=%s\nROLE_ACCOUNT=twice\nROLE_KIT_CLONE=relative/kit\nTEAM_KEYS=kit,KIT\n"
           "TICKET_URL_TEMPLATE=http://x/no-placeholder\nCHAT_CHANNEL_ID=C0123456789\n"
           "EXECUTOR_ACTOR_IDS=a b\nJOB_LABEL=nodots\nCHAT_TOKEN_ENV=SLACK_BOT_TOKEN\n"
           "LINEAR_KEY_ENV=lower_case\nINTERVAL_SECONDS=0\nMAX_EVENTS_PER_PASS=many\n"
           "NOT_A_KEY=1\nno equals sign\nENV_FILE=~/d/env\nDISPATCHER_ENV_FILE=~/d/env\n"
           "STATE_DIR=~/dispatch/state\nDISPATCHER_STATE_ROOT=~/dispatch\n" % person)
    values, errs = parse_conf(bad)
    _conf, more = validate_conf(values, person)
    allerr = errs + more
    fragments = ["duplicate key", "not KEY=value", "unknown key NOT_A_KEY", "is YOU",
                 "ROLE_KIT_CLONE", "TEAM_KEYS entry 'kit'", "must contain {id}", "https URL",
                 "has not been edited", "EXECUTOR_ACTOR_IDS entry", "JOB_LABEL",
                 "CHAT_TOKEN_ENV may not be SLACK_BOT_TOKEN", "LINEAR_KEY_ENV must be the NAME",
                 "INTERVAL_SECONDS must be a positive integer", "MAX_EVENTS_PER_PASS",
                 "the dispatcher's own env file", "under DISPATCHER_STATE_ROOT"]

    def all_reported(errors):
        return [f for f in fragments if not any(f in e for e in errors)]

    ok("conf: every error is reported in one pass", not all_reported(allerr),
       "missing %s in:\n%s" % (all_reported(allerr), "\n".join(allerr)))
    ok("conf: a first-error-wins mutant turns the all-at-once check red",
       all_reported(allerr[:1]), "the check would pass a validator that stops at the first error")

    # ── 2. each refusal, alone, with its plain reason ───────────────────────────────────
    def refused(extra, fragment, user=person):
        values, errors = parse_conf(good_text + extra)
        return any(fragment in e for e in errors + validate_conf(values, user)[1])

    ok("refuse: ROLE_ACCOUNT equal to the invoking user",
       refused("", "is YOU", user="_exnotify"))
    reason = [e for e in validate_conf(parse_conf(good_text.replace(
        "JOB_LABEL", "CHAT_TOKEN_ENV=SLACK_BOT_TOKEN\nJOB_LABEL"))[0], person)[1]
        if "SLACK_BOT_TOKEN" in e]
    ok("refuse: CHAT_TOKEN_ENV=SLACK_BOT_TOKEN, saying why (two apps, copied into every session)",
       reason and "SEPARATE Slack app" in reason[0] and "copied into every session" in reason[0],
       repr(reason))
    ok("refuse: an env-var-name key that is not UPPER_SNAKE",
       refused("CHAT_TOKEN_ENV=Notifier_Token\n", "UPPER_SNAKE")
       and refused("LINEAR_KEY_ENV=A__B\n", "UPPER_SNAKE"))
    ok("refuse: ENV_FILE equal to DISPATCHER_ENV_FILE",
       refused("DISPATCHER_ENV_FILE=~/.stage-e/env\n", "the dispatcher's own env file"))
    ok("refuse: a notifier path under DISPATCHER_STATE_ROOT",
       refused("DISPATCHER_STATE_ROOT=~/.stage-e\n", "under DISPATCHER_STATE_ROOT"))
    ok("refuse: non-positive and non-integer numbers",
       refused("LOOKBACK_COMMENTS=-3\n", "LOOKBACK_COMMENTS must be a positive integer")
       and refused("RUN_TIMEOUT_SECONDS=2.5\n", "RUN_TIMEOUT_SECONDS must be a positive integer"))
    ok("refuse: a credential value in the conf, by key name only",
       refused("CHAT_TOKEN_ENV=%s\n" % token, "CREDENTIAL SHAPE")
       and not any(token in e for e in parse_conf(good_text + "CHAT_TOKEN_ENV=%s\n" % token)[1]))
    ok("conf: a label containing `sk-` is not a credential",
       not credential_shape("com.example.task-notifier"))
    ok("conf: the good conf validates", validate_conf(parse_conf(good_text)[0], person)[1] == [])

    example = os.path.join(os.path.dirname(HERE), "notifier.conf.example")
    try:
        with open(example, encoding="utf-8") as fh:
            ex_values, ex_errors = parse_conf(fh.read())
        ex_more = validate_conf(ex_values, person)[1]
        ok("conf: the committed example names every key, and only its placeholders are refused",
           not ex_errors and set(ex_values) == CONF_KEYS and len(ex_more) == 2
           and all("not been edited" in e for e in ex_more)
           and all(ex_values[k] == CARD_EXAMPLE[k] for k in ex_values),
           "parse %s; keys %s; validation %s" % (ex_errors, sorted(CONF_KEYS ^ set(ex_values)), ex_more))
    except OSError as exc:
        ok("conf: the committed example is readable", False, str(exc))

    # ── a fake machine: real /bin/sh as the "role account", launchd and dscl scripted ────
    fake_notifier = (
        "import json, os, sys\n"
        "home = os.environ['HOME']\n"
        "tok = os.environ.get('NOTIFIER_SLACK_BOT_TOKEN', '')\n"
        "cfg = sys.argv[sys.argv.index('--config') + 1]\n"
        "p = os.path.join(home, '.fake-exit')\n"
        "code = int(open(p).read().strip()) if os.path.exists(p) else 0\n"
        "print('fake notifier: token sourced, %d chars; config %s; dry %s' % (len(tok), "
        "'found' if os.path.exists(cfg) else 'MISSING', '--dry-run' in sys.argv))\n"
        "print('a leaky transport would echo: Authorization: Bearer %s' % tok)\n"
        "state = os.path.join(home, '.stage-e', 'state')\n"
        "os.makedirs(state, exist_ok=True)\n"
        "summary = 'fake pass summary, exit %d' % code\n"
        "json.dump({'schema': " + repr(notify.HEARTBEAT_SCHEMA) + ", 'at': '2026-01-01T00:00:00Z', "
        "'dry': '--dry-run' in sys.argv, 'exit': code, 'summary': summary}, "
        "open(os.path.join(state, 'notifier-heartbeat.json'), 'w'))\n"
        "print(summary)\n"
        "sys.exit(code)\n")

    real_user = invoking_user()

    class FakeMachine(se.Runner):
        """`sudo -u <role> -H /bin/sh -c …` runs the script through a REAL /bin/sh with HOME set to
        a temporary role home; launchd, dscl, plutil and `sudo install` are emulated. Files the
        battery creates are owned by whoever runs it, and a real `sudo -u` would show the role
        account there, so the probe's `owner=` field is translated — nothing else is."""

        def __init__(self, home, role, dry_run=False):
            se.Runner.__init__(self, dry_run=dry_run)
            self.home = home
            self.role = role
            self.plists = {}
            self.loaded = False
            self.selftest_rc = 0
            self.role_scripts = []
            self.sudo_argv = []

        def _exec(self, argv, stdin, timeout):
            argv = list(argv)
            if argv[0] == "sudo":
                self.sudo_argv.append(argv)
            if argv[:2] == ["sudo", "-u"] and argv[3:6] == ["-H", "/bin/sh", "-c"]:
                script = argv[6].replace("/usr/bin/python3", sys.executable)
                self.role_scripts.append(argv[6])
                p = subprocess.run(["/bin/sh", "-c", script], input=stdin, capture_output=True,
                                   text=True, timeout=timeout,
                                   env={"HOME": self.home, "PATH": "/usr/bin:/bin"})
                out = p.stdout.replace("owner=%s " % real_user, "owner=%s " % self.role)
                return se.Result(p.returncode, out, p.stderr)
            if argv[:1] == ["dscl"]:
                return se.Result(0, "NFSHomeDirectory: %s\n" % self.home)
            if len(argv) == 3 and argv[2] == "--selftest":
                return se.Result(self.selftest_rc, "90 checks, %d failed\n" % self.selftest_rc)
            if argv[:2] == ["test", "-e"]:
                return se.Result(0 if argv[2] in self.plists else 1)
            if argv[:1] == ["cat"]:
                if argv[1] in self.plists:
                    return se.Result(0, self.plists[argv[1]])
                return se.Result(1, "", "No such file")
            if argv[:2] == ["plutil", "-lint"]:
                with open(argv[2], "rb") as fh:
                    try:
                        plistlib.load(fh)
                        return se.Result(0, "OK")
                    except Exception as exc:                      # noqa: BLE001
                        return se.Result(1, str(exc))
            if argv[:2] == ["sudo", "install"]:
                with open(argv[-2], encoding="utf-8") as fh:
                    self.plists[argv[-1]] = fh.read()
                return se.Result(0)
            if argv[:3] == ["sudo", "launchctl", "print"]:
                return (se.Result(0, "state = not running\n") if self.loaded
                        else se.Result(113, "", "Could not find service"))
            return se.Result(1, "", "FakeMachine: nothing scripted for %s" % se._fmt(argv)[:120])

    class FakeTracker(object):
        def __init__(self, labels=None, viewer="viewer-uuid-0001", reject=False, unreachable=False):
            self.labels = labels if labels is not None else [
                {"id": "11111111-aaaa-bbbb-cccc-000000000001", "name": "agent:blocked", "team": None},
                {"id": "11111111-aaaa-bbbb-cccc-000000000002", "name": "agent:needs-human", "team": None}]
            self.viewer, self.reject, self.unreachable = viewer, reject, unreachable
            self.built_with = []

        def __call__(self, key_value):
            self.built_with.append(key_value)
            return self

        def _gate(self):
            if self.reject:
                raise TrackerError("the tracker refused the key %s (HTTP 401)" % key, rejected=True)
            if self.unreachable:
                raise TrackerError("the tracker was unreachable (offline)")

        def viewer_id(self):
            self._gate()
            return self.viewer

        def labels_named(self, name):
            self._gate()
            return [dict(l) for l in self.labels if l["name"] == name]

    def new_home(root, with_token=True, with_key=True, extra_lines=()):
        home = os.path.join(root, "role-home")
        scripts = os.path.join(home, ".stage-e", "kit", "scripts")
        os.makedirs(scripts)
        os.chmod(os.path.join(home, ".stage-e"), 0o700)
        for name in REQUIRED_SCRIPTS:
            with open(os.path.join(scripts, name), "w", encoding="utf-8") as fh:
                fh.write(fake_notifier if name == NOTIFIER_SCRIPT else "# fake\n")
        lines = list(extra_lines)
        if with_key:
            lines.append("STAGE_E_LINEAR_API_KEY=" + key)
        lines.append("GH_TOKEN=" + gh)
        if with_token:
            lines.append("NOTIFIER_SLACK_BOT_TOKEN=" + token)
        env_file = os.path.join(home, ".stage-e", "env")
        with open(env_file, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        os.chmod(env_file, 0o600)
        return home

    def machine_ctx(root, conf=None, home=None, env=None, tty=False, reader=None, tracker=None,
                    dry_run=False, state=None):
        home = home or new_home(root)
        conf = conf or good_conf()
        fake = FakeMachine(home, conf["ROLE_ACCOUNT"], dry_run=dry_run)
        st = state or State(os.path.join(root, "ledger"))
        ctx = Ctx(conf, fake, st,
                  env={"STAGE_E_LINEAR_API_KEY": key} if env is None else env, tty=tty,
                  transport_factory=tracker or FakeTracker(), token_reader=reader)
        return ctx, fake

    def with_env(extra, fn):
        """Run fn with these variables set in the real environment, and remove them after."""
        os.environ.update(extra)
        try:
            return fn()
        finally:
            for name in extra:
                os.environ.pop(name, None)

    def rows_of(rows):
        return dict((s, o) for s, o, _d in rows)

    def snapshot(path):
        snap = {}
        for base, _dirs, files in os.walk(path):
            for name in files:
                full = os.path.join(base, name)
                info = os.stat(full)
                with open(full, "rb") as fh:
                    # Inode and mtime too: a rewrite within the same second can be byte-identical.
                    snap[full] = (fh.read(), info.st_mode & 0o777, info.st_ino, info.st_mtime_ns)
        return snap

    captured = []
    tmp_root = tempfile.mkdtemp(prefix="notifier-setup-selftest.")
    try:
        # ── 3. the install, pass by pass, through each human gate, across fresh ledgers ──
        root = os.path.join(tmp_root, "install")
        os.makedirs(root)
        ledger = os.path.join(root, "ledger")
        home = new_home(root)
        ctx, fake = machine_ctx(root, home=home)
        code, out = quiet(lambda: cmd_run(ctx, dry_run=False))
        captured.append(out)
        got = rows_of(run_steps_rows(ctx))
        ok("run: a fresh machine passes preflight and stops at CK-N1 with exit 10",
           code == EX_BLOCKED and got.get("preflight") == ALREADY_DONE
           and got.get("slack-app") == BLOCKED and "CK-N1" in out, "%s\n%s" % (got, out[-900:]))
        ok("ledger: written to disk at directory 700 and file 600",
           os.path.exists(os.path.join(ledger, "state.json"))
           and os.stat(ledger).st_mode & 0o777 == 0o700
           and os.stat(os.path.join(ledger, "state.json")).st_mode & 0o777 == 0o600)
        fresh = subprocess.run([sys.executable, os.path.abspath(__file__), "status", "--state", ledger],
                               capture_output=True, text=True, timeout=120)
        captured.append(fresh.stdout + fresh.stderr)
        ok("status: a FRESH PROCESS replays the ledger from disk",
           "slack-app" in fresh.stdout and BLOCKED in fresh.stdout and "card CK-N1" in fresh.stdout
           and fresh.returncode == EX_BLOCKED, fresh.stdout[-800:] + fresh.stderr[-400:])

        # a second context, from the same temporary home, signs the gate
        _v, out = quiet(lambda: cmd_attest(State(ledger), A_PRIVATE, "pq", "private; second account "
                                           "could not join", conf=good_conf(), env={}))
        captured.append(out)
        ok("attest: a sign-off in one context is on disk for the next",
           (State(ledger).data["attestations"].get(A_PRIVATE) or {}).get("channel_id") == "C0SYNTHETIC1")

        ctx, fake = machine_ctx(root, home=home, state=State(ledger))
        code, out = quiet(lambda: cmd_run(ctx, dry_run=False))
        captured.append(out)
        got = dict((s, o) for s, o, _d in run_steps_rows(ctx))
        ok("run: past the first human gate, it writes config and job, runs the dry run, stops at CK-N3",
           code == EX_BLOCKED and got.get("slack-app") == ALREADY_DONE
           and got.get("credentials") == ALREADY_DONE and got.get("config") == DONE
           and got.get("job") == DONE and got.get("dry-run") == DONE and got.get("enable") == BLOCKED,
           "%s\n%s" % (got, out[-1500:]))
        cfg_path = os.path.join(home, ".stage-e", "notifier.json")
        ok("config: written as the role account at mode 600",
           os.path.exists(cfg_path) and os.stat(cfg_path).st_mode & 0o777 == 0o600)
        dry_scripts = [s for s in fake.role_scripts if s.endswith(" --dry-run")]
        plist_file = plist_path(ctx.conf)
        plist_doc = plistlib.loads(fake.plists.get(plist_file, "").encode("utf-8") or b"<plist/>") \
            if plist_file in fake.plists else {}
        ok("dry run: the step runs the plist's OWN command, plus --dry-run",
           len(dry_scripts) == 1 and plist_doc
           and dry_scripts[0] == "cd / && " + plist_doc["ProgramArguments"][2] + " --dry-run",
           "%s | %s" % (dry_scripts, plist_doc.get("ProgramArguments")))
        ok("dry run: the env file really was sourced (the fake notifier saw the token's length)",
           "token sourced, %d chars" % len(token) in out and "config found" in out, out[-1200:])
        ok("secret: a token a component echoes is withheld by shape, never printed",
           token not in out and "withheld" in out, out[-1200:])

        fake.loaded = True
        ctx, fake2 = machine_ctx(root, home=home, state=State(ledger))
        fake2.plists, fake2.loaded = fake.plists, True
        code, out = quiet(lambda: cmd_run(ctx, dry_run=False))
        captured.append(out)
        ok("run: loaded, it stops at CK-N4 and does NOT re-run the dry run",
           code == EX_BLOCKED and "CK-N4" in out
           and not [s for s in fake2.role_scripts if s.endswith(" --dry-run")], out[-900:])
        ok("never loads: no launchctl bootstrap was ever issued",
           not [a for a in fake.sudo_argv + fake2.sudo_argv if "bootstrap" in a])

        _v, out = quiet(lambda: cmd_attest(State(ledger), A_FIRST_PING, "pq", "KIT-0: pinged, link "
                                           "opened, label landed", env={}))
        captured.append(out)
        ctx, fake3 = machine_ctx(root, home=home, state=State(ledger))
        fake3.plists, fake3.loaded = fake.plists, True
        code, out = quiet(lambda: cmd_run(ctx, dry_run=False))
        captured.append(out)
        ok("run: a settled machine exits 0 and says what is on, off and not proven",
           code == EX_OK and "KIT-156" in out and "KIT-118" in out and "KIT-157" in out
           and "NOT PROVEN" in out and not fake3.writes, out[-1500:])

        # ── 4. verify never writes, never records, and on a settled machine exits 0 ──────
        before_home, before_ledger = snapshot(home), snapshot(ledger)
        ctx, fake4 = machine_ctx(root, home=home, state=State(ledger))
        fake4.plists, fake4.loaded = fake.plists, True
        code, out = quiet(lambda: cmd_verify(ctx))
        captured.append(out)
        ok("verify: a settled machine exits 0", code == EX_OK, out[-1200:])
        ok("verify: no write, and the role home and the ledger are byte-identical after",
           not fake4.writes and snapshot(home) == before_home and snapshot(ledger) == before_ledger)
        ok("verify: the dry run is read from the heartbeat, never re-run",
           not [s for s in fake4.role_scripts if "--dry-run" in s])

        def boom(_ctx, _apply):
            raise RuntimeError("an escaped bug\ncarrying %s" % key)

        ctx, fake5 = machine_ctx(root, home=home, state=State(ledger))
        fake5.plists, fake5.loaded = fake.plists, True
        broken = tuple((s, t, boom if s == "labels" else f) for s, t, f in STEPS)
        saved_steps = globals()["STEPS"]
        globals()["STEPS"] = broken
        try:
            code, out = quiet(lambda: cmd_verify(ctx))
        finally:
            globals()["STEPS"] = saved_steps
        captured.append(out)
        ok("verify: an escaped exception is a FAILED row, the pass keeps going, and it never raises",
           code == EX_FAILED and "unexpected RuntimeError" in out and "handover" in out
           and key not in out, out[-900:])
        partial = dict(good_conf())
        partial.pop("JOB_LABEL")
        ctx, _f = machine_ctx(os.path.join(root, "partial"), conf=partial)
        try:
            code, out = quiet(lambda: cmd_verify(ctx))
            ok("verify: an exception outside every step is exit 1 and a named BUG, not a traceback",
               code == EX_FAILED and "BUG: verify raised KeyError" in out, out[-400:])
        except Exception as exc:                                  # noqa: BLE001
            ok("verify: an exception outside every step is exit 1 and a named BUG, not a traceback",
               False, "it raised %r" % exc)

        # ── 5. no secret anywhere: every captured output, and the ledger file ────────────
        with open(os.path.join(ledger, "state.json"), encoding="utf-8") as fh:
            ledger_text = fh.read()
        joined = "\n".join(captured)
        ok("secret: the planted token appears in no output and nowhere in the ledger",
           token not in joined and token not in ledger_text)
        ok("secret: the planted tracker key appears in no output and nowhere in the ledger",
           key not in joined and key not in ledger_text)

        # ── 6. the typed token: stdin only, other lines kept, mode 600, CK-N2 without a tty ─
        root6 = os.path.join(tmp_root, "typed")
        os.makedirs(root6)
        home6 = new_home(root6, with_token=False,
                         extra_lines=("NOTIFIER_SLACK_BOT_TOKEN=not-a-bot-token", "OTHER_NAME=kept"))
        ctx, fake6 = machine_ctx(root6, home=home6, tty=True, reader=lambda _p: token)
        res, out = quiet(lambda: step_credentials(ctx, True))
        with open(os.path.join(home6, ".stage-e", "env"), encoding="utf-8") as fh:
            env_text = fh.read()
        ok("credentials: a typed token is written, replacing the bad line and keeping every other",
           res[0] is False and env_text.count("NOTIFIER_SLACK_BOT_TOKEN=") == 1
           and ("NOTIFIER_SLACK_BOT_TOKEN=" + token) in env_text and "OTHER_NAME=kept" in env_text
           and ("STAGE_E_LINEAR_API_KEY=" + key) in env_text
           and os.stat(os.path.join(home6, ".stage-e", "env")).st_mode & 0o777 == 0o600, env_text)
        ok("credentials: the value travelled on stdin, never argv, and was never printed",
           token not in out and all(token not in " ".join(w["argv"]) for w in fake6.writes)
           and all(w["stdin"] == "<hidden>" for w in fake6.writes), out)
        probe6 = fake6._exec(["sudo", "-u", "_exnotify", "-H", "/bin/sh", "-c",
                              "cd / && " + _cred_script(CRED_PROBE_SH, good_conf())], None, 60)
        ok("credentials: the probe's raw output carries a verdict and never the value",
           token not in probe6.out + probe6.err and (parse_cred_probe(probe6.out) or {}).get("chat") == "ok",
           probe6.out + probe6.err)

        # Inside the role account's shell the value may only meet BUILTINS: an external command
        # (`/usr/bin/printf`, `echo` from PATH on some shells, `test`) would put it in that
        # process's argv, where any process on the machine can read it.
        def value_commands(script):
            words = []
            for segment in re.split(r";|&&|\|\||\||\(|\)|\{|\}|\bthen\b|\bdo\b|\belse\b", script):
                if '"$v"' in segment or "$v " in segment:
                    parts = [w for w in segment.split() if w not in ("if", "!")]
                    words.append(parts[0] if parts else "")
            return words

        used = value_commands(CRED_PROBE_SH) + value_commands(CRED_WRITE_SH)
        ok("credentials: the value meets only shell builtins (printf, case, [), never an external command",
           used and all(w in ("printf", "case", "[") for w in used), repr(used))
        ctx, _f = machine_ctx(root6, home=new_home(os.path.join(root6, "b"), with_token=False))
        try:
            quiet(lambda: step_credentials(ctx, True))
            ok("credentials: no terminal blocks on CK-N2", False)
        except Blocked as exc:
            ok("credentials: no terminal blocks on CK-N2", exc.card_id == "CK-N2")
        ctx, f7 = machine_ctx(root6, home=new_home(os.path.join(root6, "c"), with_token=False,
                                                    with_key=False), tty=True,
                              reader=lambda _p: token)
        try:
            quiet(lambda: step_credentials(ctx, True))
            ok("credentials: a missing tracker key fails, naming Stage E, and asks nothing", False)
        except SetupError as exc:
            ok("credentials: a missing tracker key fails, naming Stage E, and asks nothing",
               "pipeline_stage_e_setup.py run" in str(exc) and not f7.writes, str(exc))

        # ── 7. the agent environment ────────────────────────────────────────────────────
        agent_env = {"CLAUDECODE": "", "STAGE_E_LINEAR_API_KEY": key}
        code, out = quiet(lambda: with_env({"CLAUDECODE": ""}, lambda: main(
            ["run", "--conf", os.path.join(root, "absent.conf"),
             "--state", os.path.join(root, "agent-ledger")])))
        ok("agent: `run` is refused with exit 3 before the conf is read, with no override",
           code == EX_REFUSED and "REFUSED" in out and "could not read" not in out
           and not os.path.exists(os.path.join(root, "agent-ledger")), out)
        code, out = quiet(lambda: with_env({"AI_AGENT": ""}, lambda: main(
            ["attest", A_PRIVATE, "--initials", "pq", "--note", "x", "--state", ledger])))
        ok("agent: `attest` is refused with exit 3", code == EX_REFUSED and "REFUSED" in out, out)
        try:
            quiet(lambda: cmd_attest(State(ledger), A_PRIVATE, "pq", "x", conf=good_conf(),
                                     env={"AI_AGENT": ""}))
            ok("agent: `attest` is refused inside the command too", False)
        except Refusal:
            ok("agent: `attest` is refused inside the command too", True)
        root7 = os.path.join(tmp_root, "agent")
        os.makedirs(root7)
        tracker7 = FakeTracker()
        ctx, fake7 = machine_ctx(root7, env=agent_env, tracker=tracker7, dry_run=True)
        code, out = quiet(lambda: cmd_run(ctx, dry_run=True))
        got = rows_of(run_steps_rows(ctx))
        ok("agent: `run --dry-run` runs, and every sudo-needing row is NOT MEASURED (UNKNOWN), exit 4",
           code == EX_UNKNOWN and all(got.get(s) == UNKNOWN for s in
                                      ("preflight", "credentials", "labels", "enable"))
           and "NOT MEASURED" in out, "%s\n%s" % (got, out[-1500:]))
        ok("agent: no sudo is attempted, no tracker transport is built, nothing is recorded",
           not fake7.sudo_argv and not tracker7.built_with
           and not os.path.exists(os.path.join(root7, "ledger", "state.json")),
           "%s %s" % (fake7.sudo_argv, tracker7.built_with))
        code, out = quiet(lambda: with_env({"CLAUDECODE": ""}, lambda: main(["card", "CK-N1"])))
        ok("agent: `card` still prints", code == EX_OK and "CK-N1" in out, out[-400:])
        code, out = quiet(lambda: with_env({"AI_AGENT": ""}, lambda: main(
            ["status", "--state", ledger])))
        ok("agent: `status` still replays", code in (EX_OK, EX_BLOCKED) and "slack-app" in out,
           out[-400:])

        # ── 8. a dry run in a person's shell: no write, every row has its reason, recorded ──
        root8 = os.path.join(tmp_root, "person-dry")
        os.makedirs(root8)
        ctx, fake8 = machine_ctx(root8, dry_run=True)
        code, out = quiet(lambda: cmd_run(ctx, dry_run=True))
        rows8 = run_steps_rows(ctx)
        ok("dry run: exits non-zero only with a reason on every row, and writes nothing",
           code != EX_OK and not fake8.writes and all(d for _s, o, d in rows8 if o != ALREADY_DONE)
           and "DRY RUN: nothing was changed" in out, out[-1500:])
        ok("dry run: a person's dry run is recorded for `status`",
           os.path.exists(os.path.join(root8, "ledger", "state.json")))

        # ── 9. the exit code of each outcome ────────────────────────────────────────────
        def one(fn):
            return (("only", "one step", fn),)

        def raises(exc):
            def fn(_ctx, _apply):
                raise exc
            return fn

        cases = [(lambda c, a: (False, "did it", []), True, EX_OK, DONE),
                 (lambda c, a: (True, "already", []), True, EX_OK, ALREADY_DONE),
                 (lambda c, a: (False, "would", []), False, EX_BLOCKED, WOULD_CHANGE),
                 (raises(Blocked("CK-N3")), True, EX_BLOCKED, BLOCKED),
                 (raises(SetupError("broke")), True, EX_FAILED, FAILED),
                 (raises(Unknown("could not look")), True, EX_UNKNOWN, UNKNOWN)]
        for fn, apply_it, want, outcome in cases:
            ctx, _f = machine_ctx(os.path.join(tmp_root, "exit-%s" % outcome))
            (code, rows), _o = quiet(lambda: run_steps(ctx, apply_it, steps=one(fn)))
            ok("exit: %s is %d" % (outcome, want), code == want and rows[0][1] == outcome,
               "%s %s" % (code, rows))
        code, out = quiet(lambda: main(["run", "--conf", os.path.join(root, "absent.conf"),
                                        "--state", os.path.join(root, "x")]))
        ok("exit: an unreadable conf is 2", code == EX_USAGE, out)
        conf_file = os.path.join(root, "good.conf")
        with open(conf_file, "w", encoding="utf-8") as fh:
            fh.write(good_text)
        saved_validate = globals()["_sudo_validate"]
        globals()["_sudo_validate"] = lambda: 1
        try:
            code, out = quiet(lambda: main(["verify", "--conf", conf_file, "--state",
                                            os.path.join(root, "nopriv")]))
        finally:
            globals()["_sudo_validate"] = saved_validate
        ok("exit: declined administrator access is 5, with nothing attempted",
           code == EX_NOPRIV and "NO ADMINISTRATOR ACCESS" in out and "-- steps --" not in out, out[-500:])

        # ── 10. sign-offs: placeholders refused; bound to what they signed ───────────────
        st10 = State(os.path.join(tmp_root, "attest"))
        for initials, note in ((INITIALS_PLACEHOLDER, "n"), ("xx", "n"), ("pq", ""), ("p1", "n")):
            try:
                quiet(lambda: cmd_attest(st10, A_PRIVATE, initials, note, conf=good_conf(), env={}))
                ok("attest: %r / %r refused" % (initials, note), False)
            except SetupError:
                ok("attest: %r / %r refused" % (initials, note), True)
        ok("attest: nothing a card prints as initials is signable",
           all(INITIALS_PLACEHOLDER in "".join(c["do"]) for c in CARDS.values() if c["attest"])
           and INITIALS_PLACEHOLDER.lower() in se.INITIALS_PLACEHOLDERS)
        try:
            quiet(lambda: cmd_attest(st10, A_FIRST_PING, "pq", "n", env={}))
            ok("attest: A-FIRST-PING refuses before any config is recorded", False)
        except SetupError:
            ok("attest: A-FIRST-PING refuses before any config is recorded", True)
        ctx, _f = machine_ctx(os.path.join(tmp_root, "rebind"), state=State(ledger),
                              conf=good_conf().copy())
        ctx.conf["CHAT_CHANNEL_ID"] = "C0SOMEOTHER9"
        try:
            step_slack_app(ctx, True)
            ok("attest: a channel change brings CK-N1 back", False)
        except Blocked as exc:
            ok("attest: a channel change brings CK-N1 back", exc.card_id == "CK-N1" and "C0SOMEOTHER9" in exc.extra)

        # ── 11. the composition: accepted by the notifier's loader; a broken one never written ─
        ids = {"agent:blocked": "id-blocked-0001", "agent:needs-human": "id-needs-0002"}
        doc = compose_config(good_conf(), ids, ["actor-0001"])
        try:
            validate_composed(doc, None)
            ok("config: the composition is accepted by the notifier's own loader", True)
        except SetupError as exc:
            ok("config: the composition is accepted by the notifier's own loader", False, str(exc))
        ok("config: it carries the conf's token NAME, never SLACK_BOT_TOKEN",
           doc["chat_token_env"] == "NOTIFIER_SLACK_BOT_TOKEN" and doc["state_dir"] == "~/.stage-e/state")
        root11 = os.path.join(tmp_root, "broken")
        os.makedirs(root11)
        ctx, fake11 = machine_ctx(root11)
        ctx.ids = {"label_ids": ids, "executor_actor_ids": ["actor-0001"]}
        saved_compose = globals()["compose_config"]

        def dropping(conf, label_ids, actor_ids):
            broken_doc = saved_compose(conf, label_ids, actor_ids)
            broken_doc["label_ids"].pop("agent:needs-human")
            return broken_doc

        globals()["compose_config"] = dropping
        try:
            (code, rows), out = quiet(lambda: run_steps(ctx, True, steps=(("config", "", step_config),)))
        finally:
            globals()["compose_config"] = saved_compose
        ok("config: a composition missing a label id is refused BEFORE any write",
           rows[0][1] == FAILED and "REFUSES" in out and not fake11.writes
           and not os.path.exists(os.path.join(fake11.home, ".stage-e", "notifier.json")), out[-600:])

        # ── 12. the plist ───────────────────────────────────────────────────────────────
        conf12 = good_conf()
        pdoc = plistlib.loads(render_plist(conf12, "/private/var/_exnotify").encode("utf-8"))
        cmd = pdoc["ProgramArguments"][2]
        ok("plist: runs /bin/sh -c as the role account, every interval, logging beside the config",
           pdoc["ProgramArguments"][:2] == ["/bin/sh", "-c"] and pdoc["UserName"] == "_exnotify"
           and pdoc["StartInterval"] == 300 and pdoc["RunAtLoad"] is True
           and pdoc["EnvironmentVariables"]["HOME"] == "/private/var/_exnotify"
           and pdoc["StandardOutPath"] == "/private/var/_exnotify/.stage-e/notifier.log", repr(pdoc))
        ok("plist: sources the env file exactly as the Stage E daemons do",
           cmd.startswith(se.DAEMON_EXEC) and exec_prefix(conf12) == se.DAEMON_EXEC, cmd)
        ok("plist: names the notifier in the clone, with its config, and no --dry-run",
           cmd.endswith('"$HOME/.stage-e/kit/scripts/pipeline_notify_local.py" run --config '
                        '"$HOME/.stage-e/notifier.json"'), cmd)
        custom = good_conf("ENV_FILE=/var/example role/env\n")
        ok("plist: an absolute ENV_FILE is sourced, quoted",
           job_command(custom).startswith('set -a; . "/var/example role/env"; set +a;'), job_command(custom))

        # ── 13. the dry run: 0 and 3 done; 1, 2 and 4 failed with the notifier's words ───
        for rc in (0, 3, 1, 2, 4):
            r13 = os.path.join(tmp_root, "dry-%d" % rc)
            os.makedirs(r13)
            h13 = new_home(r13)
            with open(os.path.join(h13, ".fake-exit"), "w") as fh:
                fh.write(str(rc))
            ctx, _f = machine_ctx(r13, home=h13)
            ctx.ids = {"label_ids": ids, "executor_actor_ids": ["actor-0001"]}
            steps13 = tuple((s, t, f) for s, t, f in STEPS if s in ("preflight", "config", "job", "dry-run"))
            (code, rows), out = quiet(lambda: run_steps(ctx, True, keep_going=True, steps=steps13))
            got = rows_of(rows).get("dry-run")
            want = DONE if rc in (0, 3) else FAILED
            ok("dry run: notifier exit %d is %s" % (rc, want),
               got == want and (rc in (0, 3) or "fake pass summary, exit %d" % rc in out)
               and token not in out, "%s\n%s" % (rows, out[-800:]))

        # ── 14. labels: exact name, workspace scope, never created; key from YOUR shell ──
        def labels_case(tracker, env=None):
            ctx, _f = machine_ctx(os.path.join(tmp_root, "labels-%d" % len(checks)), tracker=tracker,
                                  env=env)
            try:
                return step_labels(ctx, True), ctx
            except Exception as exc:                              # noqa: BLE001
                return exc, ctx

        res, ctx14 = labels_case(FakeTracker())
        ok("labels: both resolved, and `self` is the key's own user",
           isinstance(res, tuple) and ctx14.ids["executor_actor_ids"] == ["viewer-uuid-0001"]
           and set(ctx14.ids["label_ids"]) == set(LABEL_KEYS), repr(res))
        res, _c = labels_case(FakeTracker(labels=[{"id": "x1", "name": "agent:blocked", "team": None}]))
        ok("labels: a missing label fails and names the setup that creates it, creating nothing",
           isinstance(res, SetupError) and "agent:needs-human does not exist" in str(res)
           and "/setup-board" in str(res), repr(res))
        res, _c = labels_case(FakeTracker(labels=[
            {"id": "x1", "name": "agent:blocked", "team": "KIT"},
            {"id": "x2", "name": "agent:needs-human", "team": None}]))
        ok("labels: a team-scoped label is refused", isinstance(res, SetupError)
           and "only as a TEAM label" in str(res), repr(res))
        res, _c = labels_case(FakeTracker(reject=True))
        ok("labels: a refused key is FAILED, and the key is not in the message",
           isinstance(res, SetupError) and key not in str(_c.scrub(str(res))), repr(res))
        res, _c = labels_case(FakeTracker(unreachable=True))
        ok("labels: an unreachable tracker is UNKNOWN", isinstance(res, Unknown))
        res, _c = labels_case(FakeTracker(), env={})
        ok("labels: no key in your shell is UNKNOWN, naming the export",
           isinstance(res, Unknown) and "read -rs STAGE_E_LINEAR_API_KEY" in res.remedy)

        # ── 15. the real transport: real requests, and a redirect refused ────────────────
        import http.server

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length)
                self.server.hits.append((self.path, self.headers.get("Authorization")))
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "http://127.0.0.1:%d/stolen" % self.server.server_address[1])
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                doc = json.loads(body.decode("utf-8"))
                if "viewer" in doc["query"]:
                    data = {"data": {"viewer": {"id": "viewer-from-server"}}}
                else:
                    data = {"data": {"issueLabels": {"nodes": [
                        {"id": "lab-1", "name": doc["variables"]["name"], "team": None}]}}}
                out_b = json.dumps(data).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out_b)))
                self.end_headers()
                self.wfile.write(out_b)

            def do_GET(self):
                self.server.hits.append((self.path, self.headers.get("Authorization")))
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *_args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        server.hits = []
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()
        try:
            port = server.server_address[1]
            no_proxy = (lambda: urllib.request.ProxyHandler({}),)
            local = type("_Local", (TrackerReader,), {"_ENDPOINT": "http://127.0.0.1:%d/graphql" % port,
                                                       "_EXTRA_HANDLERS": no_proxy})
            reader = local(key)
            viewer = reader.viewer_id()
            labels = reader.labels_named("agent:blocked")
            ok("transport: viewer_id and labels_named are real requests with the key in one header",
               viewer == "viewer-from-server" and labels and labels[0]["id"] == "lab-1"
               and [h for h in server.hits if h[0] == "/graphql" and h[1] == key],
               "%s %s %s" % (viewer, labels, server.hits))
            server.hits[:] = []
            redirecting = type("_Redirect", (TrackerReader,), {
                "_ENDPOINT": "http://127.0.0.1:%d/redirect" % port, "_EXTRA_HANDLERS": no_proxy})
            try:
                redirecting(key).viewer_id()
                ok("transport: a redirect is refused and the key never reaches its target", False)
            except TrackerError as exc:
                ok("transport: a redirect is refused and the key never reaches its target",
                   "redirect" in str(exc) and not [h for h in server.hits if h[0] == "/stolen"],
                   "%s %s" % (exc, server.hits))
            try:
                reader._ask("mut" + "ation { x }")
                ok("transport: a document that is not a query is refused before sending", False)
            except TrackerError:
                ok("transport: a document that is not a query is refused before sending", True)
        finally:
            server.shutdown()
            server.server_close()
        ok("transport: the production endpoint is the one fixed HTTPS address",
           TrackerReader._ENDPOINT == "https://api.linear.app/graphql" and not TrackerReader._EXTRA_HANDLERS)

        # ── 16. drift guards: the import closure and the label keys ──────────────────────
        import ast

        def imports(path, skip_functions):
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            found = set()

            def walk(node, inside_skipped):
                for child in ast.iter_child_nodes(node):
                    skipped = inside_skipped or (isinstance(child, ast.FunctionDef)
                                                 and child.name in skip_functions)
                    if not skipped and isinstance(child, ast.Import):
                        found.update(a.name.split(".")[0] for a in child.names)
                    elif not skipped and isinstance(child, ast.ImportFrom) and child.module:
                        found.add(child.module.split(".")[0])
                    walk(child, skipped)

            if skip_functions is None:
                for child in tree.body:
                    if isinstance(child, ast.Import):
                        found.update(a.name.split(".")[0] for a in child.names)
                    elif isinstance(child, ast.ImportFrom) and child.module:
                        found.add(child.module.split(".")[0])
            else:
                walk(tree, False)
            return set(m + ".py" for m in found if os.path.exists(os.path.join(HERE, m + ".py")))

        closure = set([NOTIFIER_SCRIPT])
        todo = list(imports(os.path.join(HERE, NOTIFIER_SCRIPT), ("selftest",)))
        while todo:
            name = todo.pop()
            if name not in closure:
                closure.add(name)
                todo.extend(imports(os.path.join(HERE, name), None))
        ok("drift: REQUIRED_SCRIPTS is exactly what the notifier imports when it runs",
           closure == set(REQUIRED_SCRIPTS), "%s vs %s" % (sorted(closure), sorted(REQUIRED_SCRIPTS)))
        ok("drift: the notifier's labelled marks are exactly agent:blocked and agent:needs-human",
           LABEL_KEYS == ("agent:blocked", "agent:needs-human"), repr(LABEL_KEYS))
        ok("drift: the dry run's done codes are the notifier's 0 and 3",
           DRY_RUN_OK == (0, 3))

        # ── 17. what this file can never do, read from its own source ──────────────────
        with open(os.path.abspath(__file__), encoding="utf-8") as fh:
            source = fh.read()
        head, _sep, rest = source.partition("\ndef selftest():")
        body_only = head + "\ndef build_parser():" + rest.partition("\ndef build_parser():")[2]
        banned = ["pr " + "merge", "--app" + "rove", "APP" + "ROVE", "enable-auto-" + "merge",
                  "--au" + "to", "merge" + "PullRequest", "--add-" + "label", "--remove-" + "label",
                  "issue" + "AddLabel", "issueLabel" + "Create", "added" + "LabelIds", "label" + "Ids",
                  "issue" + "Update", "state" + "Id", "comment" + "Create", "issue" + "Create",
                  "muta" + "tion"]
        for token_ in banned:
            ok("source: never names %r" % token_, token_ not in body_only)
        tree = ast.parse(body_only)
        lists = [n for n in ast.walk(tree) if isinstance(n, ast.List)
                 and any(isinstance(e, ast.Constant) and e.value == "boot" + "strap" for e in n.elts)]
        ok("source: no command list that loads a job", not lists)
        queries = [v for k, v in vars(TrackerReader).items() if k.startswith("Q_")]
        ok("source: every tracker document is a read query",
           queries and all(re.match(r"^\s*query\s", q) for q in queries))
    except Exception:                                             # noqa: BLE001
        # A case that raised is a failure of this battery, and it must still print its count
        # line: a traceback in place of `N checks, M failed` is the silent shape §13 forbids.
        import traceback
        ok("the battery ran to its end without an escaped exception", False,
           scrub(traceback.format_exc()[-1500:], (token, key)))
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    failed = [c for c in checks if not c[0]]
    for passed, name, detail in checks:
        if not passed:
            sys.stderr.write("FAIL  %s\n      %s\n" % (name, str(detail)[:2000]))
    print("%d checks, %d failed" % (len(checks), len(failed)))
    return EX_OK if not failed else EX_FAILED


def run_steps_rows(ctx):
    """The rows the last pass recorded on this context, as (step, outcome, detail)."""
    steps = ctx.state.data.get("steps") or {}
    return [(sid, steps[sid]["outcome"], steps[sid]["detail"]) for sid, _t, _f in STEPS if sid in steps]


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def build_parser():
    p = argparse.ArgumentParser(
        prog="pipeline_notifier_setup.py",
        description="Activate the human-action notifier: one command, run until it stops asking.")
    p.add_argument("command", nargs="?", default="run",
                   choices=["run", "status", "verify", "card", "attest"])
    p.add_argument("target", nargs="?", help="a CK-id for `card`, an A-id for `attest`")
    p.add_argument("--conf", default=DEFAULT_CONF)
    p.add_argument("--state", default=DEFAULT_STATE_HOME)
    p.add_argument("--dry-run", action="store_true",
                   help="measure every step and change nothing")
    p.add_argument("--initials", default="",
                   help="attest: 2-4 letters, YOUR OWN — the placeholder %s is refused"
                        % INITIALS_PLACEHOLDER)
    p.add_argument("--note", default="", help="attest: what you saw")
    p.add_argument("--selftest", action="store_true")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.selftest:
        return selftest()
    try:
        # BEFORE the conf is read and before the ledger is touched: a session must not get as far
        # as discovering what it would have installed.
        if args.command == "attest" or (args.command == "run" and not args.dry_run):
            refuse_if_agent(args.command)
    except Refusal as exc:
        say(str(exc))
        return EX_REFUSED
    try:
        state = State(args.state)
        if args.command == "status":
            return cmd_status(state)
        conf, errors, problem = None, [], None
        try:
            conf, errors = load_conf(args.conf, invoking_user())
        except SetupError as exc:
            if args.command not in ("card", "attest"):
                raise
            problem = str(exc).splitlines()[0]
        if errors:
            problem = "%d problem(s), the first: %s" % (len(errors), errors[0])
        if args.command == "card":
            print_card(args.target or "", conf if conf is not None and not errors else None)
            return EX_OK
        if args.command == "attest":
            return cmd_attest(state, args.target or "", args.initials, args.note,
                              conf=conf if conf is not None and not errors else None,
                              conf_problem=problem)
        if errors:
            say("Your conf has %d problem(s). Every one of them, in one pass:" % len(errors))
            for e in errors:
                say("  - " + e)
            say("")
            say("Fix them all, then run the same command again.")
            return EX_USAGE
        ctx = Ctx(conf, se.Runner(dry_run=args.dry_run or args.command == "verify"), state,
                  tty=sys.stdin.isatty() and sys.stdout.isatty())
        acquire_privilege(ctx, args.command, args.dry_run)
        if args.command == "verify":
            return cmd_verify(ctx)
        return cmd_run(ctx, args.dry_run)
    except Refusal as exc:
        say(str(exc))
        return EX_REFUSED
    except NoPrivilege as exc:
        say("")
        say(str(exc))
        return EX_NOPRIV
    except SetupError as exc:
        say("FAILED: " + str(exc))
        return EX_USAGE
    except KeyboardInterrupt:
        say("")
        say("interrupted — nothing further was attempted. Re-run to resume.")
        return EX_USAGE


if __name__ == "__main__":
    sys.exit(main())
