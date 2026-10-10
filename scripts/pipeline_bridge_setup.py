#!/usr/bin/env python3
"""Bridge installer — the Slack bridge (scripts/pipeline_bridge.py), composed for a person.

    python3 scripts/pipeline_bridge_setup.py compose  [--conf bridge.conf]
    python3 scripts/pipeline_bridge_setup.py token    [--conf bridge.conf]
    python3 scripts/pipeline_bridge_setup.py install  [--conf bridge.conf] [--apply]
    python3 scripts/pipeline_bridge_setup.py verify   [--conf bridge.conf]
    python3 scripts/pipeline_bridge_setup.py card CK-B1
    python3 scripts/pipeline_bridge_setup.py --selftest

WHAT IT INSTALLS (KIT-220, KIT-221). One job, run by launchd every INTERVAL_SECONDS as the
dispatcher's own role account (owner decision, 2026-10-10): it reads the chat channels,
checks each sender with Slack itself, and asks its own questions. This version acts on
nothing. It needs its OWN Slack app: if it posted as the chat bot, the chat bot could post a
question that looked like the bridge's.

    compose   prints the bridge app's manifest, the job's config, its LaunchDaemon plist and
              the order to do things in. Reads nothing live; runs anywhere, a model included.
    token     asks for the bridge app's bot token at a hidden prompt and writes it into the
              role account's env file, as that account, under SLACK_TOKEN_ENV. Refuses a
              token that is already in that file under another name (the chat bot's or the
              notifier's), and prints names and lengths only.
    install   writes the config (as the role account) and the plist (as root). Dry run by
              default; `--apply` writes. It never loads the job: that is card CK-B2.
    verify    read-only: the config and plist are the composed ones, the env file names both
              credentials, the job's own dry run passes as the role account, launchd holds
              the job, and its heartbeat is fresh.
    card      CK-B1 create the Slack app, CK-B2 load the job, CK-B3 turn the bridge off.

REFUSES IN AN AGENT ENVIRONMENT, before the conf is read: `token`, `install` and `verify`.
`verify` runs the job's own dry run, which reads the owner's tracker key; a model has no
business running it. `compose` and `card` print and read nothing live, so they are allowed.
The markers are environment variables a session could unset: tamper-evident, not
tamper-proof, as everywhere else in this kit.

EXIT CODES — the Stage E installer's (contract §13): 0 done or nothing to do, 1 failed,
2 usage or conf, 3 refused for safety, 4 could not measure, 5 no administrator access,
10 blocked on a person (a dry run that found something to write, or a row not yet applied).
"""
import argparse
import getpass
import hashlib
import json
import os
import re
import shlex
import sys
from xml.sax.saxutils import escape as _xml_escape

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from pipeline_dispatch_local import AGENT_ENV_MARKERS  # noqa: E402
import pipeline_stage_e_setup as se  # noqa: E402
import pipeline_bridge as bridge  # noqa: E402

EX_OK, EX_FAILED, EX_USAGE, EX_REFUSED, EX_UNKNOWN, EX_NOPRIV, EX_BLOCKED = (
    se.EX_OK, se.EX_FAILED, se.EX_USAGE, se.EX_REFUSED, se.EX_UNKNOWN, se.EX_NOPRIV,
    se.EX_BLOCKED)
say = se.say

DEFAULT_CONF = "bridge.conf"
BRIDGE_SCRIPT = "pipeline_bridge.py"
LOG_NAME = "bridge.log"
TOKEN_RE = re.compile(r"^xoxb-[A-Za-z0-9-]{20,}$")
LABEL_RE = re.compile(r"^[a-z][a-z0-9-]*(\.[a-z0-9][a-z0-9-]*){2,}$")
ACCOUNT_RE = re.compile(r"^_?[a-z][a-z0-9_-]{0,31}$")
PATH_RE = re.compile(r"^(~/|/)[A-Za-z0-9._/-]+$")
# The bridge app's scopes: EXACTLY what pipeline_bridge.py calls, each cited.
SCOPE_CITES = {
    "groups:history": "conversations.history and conversations.replies in a private "
                      "channel (SlackClient.history, SlackClient.replies)",
    "users:read": "users.info, the sender check (SlackClient.user)",
    "chat:write": "chat.postMessage, its questions and answers (SlackClient.post)",
}

CONF_KEYS = {
    "ROLE_ACCOUNT": ("", "the dispatcher's role account the job runs as"),
    "ROLE_KIT_CLONE": ("", "that account's clone of this kit, which the job runs from"),
    "ENV_FILE": ("~/.stage-e/env", "the role account's env file, which the job sources"),
    "SLACK_TOKEN_ENV": ("BRIDGE_SLACK_BOT_TOKEN", "the NAME the bridge app's token goes under"),
    "LINEAR_KEY_ENV": ("STAGE_E_LINEAR_API_KEY", "the NAME of the owner's tracker key"),
    "CHANNEL_IDS": ("", "the private channel id(s) it reads, comma-separated"),
    "OWNER_USER_ID": ("", "the tracker user id the key belongs to"),
    "BOT_USER_ID": ("", "the bridge app's own Slack bot user id (its member ID)"),
    "DISPATCHER_ENV_FILE": ("", "optional: the dispatcher's env file, where the chat bot's "
                                "token lives; `token` refuses that token too"),
    "TEAM_KEYS": ("", "the work teams it asks about, comma-separated"),
    "ALLOWED_SLACK_USERS": ("", "optional: the only Slack user ids who may answer"),
    "BRIDGE_CONFIG": ("~/.stage-e/bridge.json", "where the job's config is written"),
    "STATE_DIR": ("~/.stage-e/state", "where the job keeps its state and heartbeat"),
    "JOB_LABEL": ("", "the LaunchDaemon label, reverse-DNS, e.g. com.example.bridge"),
    "INTERVAL_SECONDS": ("60", "how often the job runs"),
    "APP_NAME": ("Pipeline bridge", "the Slack app's display name"),
}
REQUIRED = ("ROLE_ACCOUNT", "ROLE_KIT_CLONE", "CHANNEL_IDS", "OWNER_USER_ID", "TEAM_KEYS",
            "JOB_LABEL", "BOT_USER_ID")


# ══════════════════════════════════════════════════════════════════════════════════════
# The conf
# ══════════════════════════════════════════════════════════════════════════════════════

def parse_conf(text):
    values, errors, where = {}, [], {}
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            errors.append("line %d is not KEY=value" % n)
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key not in CONF_KEYS:
            errors.append("line %d: unknown key %s" % (n, key))
            continue
        if key in where:
            errors.append("%s is set twice, on lines %d and %d" % (key, where[key], n))
            continue
        if bridge.notify.secret_hits(value):
            errors.append("line %d: %s holds a value shaped like a credential. No key here "
                          "ever holds one: the token goes in at `token`'s hidden prompt"
                          % (n, key))
            continue
        where[key] = n
        values[key] = value
    return values, errors


def split_list(value):
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def validate_conf(values):
    conf = dict((k, values.get(k, d) or d) for k, (d, _doc) in CONF_KEYS.items())
    errors = ["%s is required" % k for k in REQUIRED if not conf.get(k)]
    if conf["ROLE_ACCOUNT"] and not ACCOUNT_RE.match(conf["ROLE_ACCOUNT"]):
        errors.append("ROLE_ACCOUNT %r is not an account name" % conf["ROLE_ACCOUNT"])
    for key in ("ROLE_KIT_CLONE", "ENV_FILE", "BRIDGE_CONFIG", "STATE_DIR",
                "DISPATCHER_ENV_FILE"):
        if conf[key] and not PATH_RE.match(conf[key]):
            errors.append("%s must be an absolute or ~/ path of plain characters, got %r"
                          % (key, conf[key]))
    if conf["JOB_LABEL"] and not LABEL_RE.match(conf["JOB_LABEL"]):
        errors.append("JOB_LABEL must be reverse-DNS like com.example.bridge, got %r"
                      % conf["JOB_LABEL"])
    if not conf["INTERVAL_SECONDS"].isdigit() or not 15 <= int(conf["INTERVAL_SECONDS"]) <= 3600:
        errors.append("INTERVAL_SECONDS must be a whole number from 15 to 3600")
    if not errors:
        try:
            bridge.validate_config(dict(compose_config(conf),
                                        state_dir="/nonexistent-%s" % os.getpid()))
        except bridge.BridgeError as exc:
            errors.extend(str(exc).split("\n  - ")[1:] or [str(exc)])
    return conf, errors


def load_conf(path):
    try:
        with open(path, encoding="utf-8") as fh:
            values, errors = parse_conf(fh.read())
    except OSError as exc:
        return None, ["could not read %s: %s" % (path, exc)]
    conf, more = validate_conf(values)
    return conf, errors + more


# ══════════════════════════════════════════════════════════════════════════════════════
# What it composes
# ══════════════════════════════════════════════════════════════════════════════════════

def compose_config(conf):
    """The job's config. `~` stays literal: the job expands it as the role account."""
    return {
        "schema": bridge.CONFIG_SCHEMA,
        "state_dir": conf["STATE_DIR"],
        "slack_token_env": conf["SLACK_TOKEN_ENV"],
        "linear_key_env": conf["LINEAR_KEY_ENV"],
        "channel_ids": split_list(conf["CHANNEL_IDS"]),
        "owner_user_id": conf["OWNER_USER_ID"],
        "bot_user_id": conf["BOT_USER_ID"],
        "team_keys": split_list(conf["TEAM_KEYS"]),
        "allowed_slack_users": split_list(conf["ALLOWED_SLACK_USERS"]),
        "act": False,
    }


def config_bytes(conf):
    return json.dumps(compose_config(conf), indent=2, sort_keys=True) + "\n"


def manifest(conf):
    return {
        "display_information": {"name": conf["APP_NAME"]},
        "features": {"bot_user": {"display_name": conf["APP_NAME"], "always_online": False}},
        "oauth_config": {"scopes": {"bot": sorted(SCOPE_CITES)}},
        "settings": {"org_deploy_enabled": False, "socket_mode_enabled": False,
                     "token_rotation_enabled": False},
    }


def sh_path(value):
    return "$HOME/" + value[2:] if value.startswith("~/") else value


def job_command(conf):
    """The job's shell command: the env file sourced, then the bridge, by full path."""
    script = conf["ROLE_KIT_CLONE"].rstrip("/") + "/scripts/" + BRIDGE_SCRIPT
    return ('set -a; . "%s"; set +a; exec /usr/bin/python3 "%s" run --config "%s"'
            % (sh_path(conf["ENV_FILE"]), sh_path(script), sh_path(conf["BRIDGE_CONFIG"])))


def plist_path(conf):
    return "/Library/LaunchDaemons/%s.plist" % conf["JOB_LABEL"]


def resolve(value, home):
    return os.path.join(home, value[2:]) if value.startswith("~/") else value


def render_plist(conf, home):
    """The Stage E installer's own plist template, filled in. launchd expands nothing."""
    log = os.path.dirname(resolve(conf["BRIDGE_CONFIG"], home)) + "/" + LOG_NAME
    return se.PLIST.format(
        label=_xml_escape(conf["JOB_LABEL"]), account=_xml_escape(conf["ROLE_ACCOUNT"]),
        home=_xml_escape(home), path=_xml_escape(se.DAEMON_PATH),
        command=_xml_escape(job_command(conf)), interval=int(conf["INTERVAL_SECONDS"]),
        log=_xml_escape(log))


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ══════════════════════════════════════════════════════════════════════════════════════
# The cards
# ══════════════════════════════════════════════════════════════════════════════════════

CARDS = {
    "CK-B1": ("Create the bridge's own Slack app", [
        "Slack: Create New App -> From a manifest -> paste the manifest `compose` prints.",
        "Install it to the workspace. Copy its Bot User OAuth Token (it starts xoxb-).",
        "In EACH channel in CHANNEL_IDS, type: /invite @{APP_NAME}",
        "Click the app's name in the channel, open its profile, and Copy member ID. Put it "
        "in the conf as BOT_USER_ID: the job refuses any token that is not this bot's.",
        "Then put the token in place, at a hidden prompt:  python3 {SELF} token --conf {CONF}",
    ], "token prints the env var name and the token's length, and nothing else.",
       "the chat bot's own app, or the notifier's. The bridge needs its own bot identity, "
       "or the chat bot could post a question that looks like the bridge's."),
    "CK-B2": ("Load the bridge job", [
        "sudo launchctl bootstrap system {PLIST}",
        "sudo launchctl enable system/{JOB_LABEL}",
        "Wait one interval, then:  python3 {SELF} verify --conf {CONF}",
    ], "verify says the job is loaded and its heartbeat is fresh.",
       "`Bootstrap failed: 5` — it is already loaded. Run verify instead."),
    "CK-B3": ("Turn the bridge off", [
        "sudo launchctl bootout system/{JOB_LABEL}",
        "sudo launchctl disable system/{JOB_LABEL}",
    ], "`launchctl print system/{JOB_LABEL}` says it could not find the service.",
       "removing the plist alone: launchd keeps a loaded job until it is booted out."),
}


def card_values(conf, conf_path):
    conf = conf or dict((k, d or "<%s>" % k) for k, (d, _doc) in CONF_KEYS.items())
    return {"APP_NAME": conf["APP_NAME"], "JOB_LABEL": conf["JOB_LABEL"],
            "PLIST": plist_path(conf), "SELF": os.path.abspath(__file__),
            "CONF": conf_path}


def print_card(cid, conf=None, conf_path=DEFAULT_CONF):
    title, steps, good, bad = CARDS[cid]
    vals = card_values(conf, conf_path)

    def fill(text):
        for k, v in vals.items():
            text = text.replace("{%s}" % k, v)
        return text
    say("%s — %s" % (cid, title))
    for n, step in enumerate(steps, 1):
        say("  %d. %s" % (n, fill(step)))
    say("  Good: %s" % fill(good))
    say("  Not that: %s" % fill(bad))
    say("")


# ══════════════════════════════════════════════════════════════════════════════════════
# The role account: one read program, two writer programs
# ══════════════════════════════════════════════════════════════════════════════════════

def agent_markers_present():
    return [m for m in AGENT_ENV_MARKERS if m in os.environ]


def refused(found):
    say("REFUSED: an agent environment is set (%s). This command reads or writes the role "
        "account's files and the owner's credentials; a person runs it, never a session."
        % ", ".join(found))
    return EX_REFUSED


# Reads the env file's NAMES (never a value), the config's bytes and the heartbeat. Every
# read is wrapped; an error is reported by its class.
FACTS_PY = r'''
import hashlib, json, os, re, sys
env_path, cfg_path, beat_path = [os.path.expanduser(a) for a in sys.argv[1:4]]
out = {}
try:
    names = []
    for line in open(env_path):
        m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if m and m.group(2).strip().strip("'\"").strip():
            names.append(m.group(1))
    out["env_names"] = sorted(set(names))
except Exception as exc:
    out["env_error"] = type(exc).__name__
try:
    raw = open(cfg_path).read()
    out["config_sha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
except Exception as exc:
    out["config_error"] = type(exc).__name__
try:
    beat = json.load(open(beat_path))
    out["heartbeat"] = dict((k, beat.get(k)) for k in ("schema", "at", "exit", "dry",
                                                       "summary"))
except Exception as exc:
    out["heartbeat_error"] = type(exc).__name__
print(json.dumps(out))
'''

# Writes the token: read from stdin, never argv. Refuses one that another name already holds.
TOKEN_WRITER_PY = r'''
import os, re, sys, time
env_path, name = os.path.expanduser(sys.argv[1]), sys.argv[2]
others = [os.path.expanduser(a) for a in sys.argv[3:] if a]
token = sys.stdin.read().strip()
lines = []
if os.path.exists(env_path):
    with open(env_path) as fh:
        lines = fh.read().splitlines()


def holds(path_lines, path, own):
    for line in path_lines:
        m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if m and not (own and m.group(1) == name) and m.group(2).strip().strip("'\"") == token:
            print("REFUSED: that token is already in %s under %s. The bridge needs its own "
                  "Slack app's token." % (path, m.group(1)))
            sys.exit(3)


holds(lines, env_path, True)
for other in others:
    try:
        with open(other) as fh:
            holds(fh.read().splitlines(), other, False)
    except OSError as exc:
        print("REFUSED: %s could not be read (%s), so the token could not be compared with "
              "the chat bot's. Nothing was written." % (other, type(exc).__name__))
        sys.exit(3)
backups = os.path.join(os.path.dirname(env_path), "backups")
os.makedirs(backups, mode=0o700, exist_ok=True)
if os.path.exists(env_path):
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    bk = os.path.join(backups, "env.bridge-setup.%s.%d" % (stamp, os.getpid()))
    fd = os.open(bk, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print("backed up the env file to %s" % bk)
    # Each backup is a whole copy of the file, the owner's key included: keep three.
    mine = sorted(n for n in os.listdir(backups) if n.startswith("env.bridge-setup."))
    for old in mine[:-3]:
        os.remove(os.path.join(backups, old))
kept = [l for l in lines if not re.match(r"^\s*(?:export\s+)?%s=" % re.escape(name), l)]
kept.append("%s=%s" % (name, token))
tmp = "%s.bridge-setup.%d" % (env_path, os.getpid())
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, "w") as fh:
    fh.write("\n".join(kept) + "\n")
os.replace(tmp, env_path)
os.chmod(env_path, 0o600)
print("wrote %s (%d characters) into %s, mode 600" % (name, len(token), env_path))
'''

# Writes the config from stdin, mode 600, creating its directory at 700.
CONFIG_WRITER_PY = r'''
import os, sys
path = os.path.expanduser(sys.argv[1])
body = sys.stdin.read()
os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
tmp = "%s.bridge-setup.%d" % (path, os.getpid())
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as fh:
    fh.write(body)
os.replace(tmp, path)
print("wrote %s (%d bytes), mode 600" % (path, len(body)))
'''


def py(program, *args):
    """One program for the role account's shell. Paths go as typed, `~/` included, and the
    program expands them itself: a quoted `$HOME` would reach it literally."""
    return "/usr/bin/python3 -c %s %s" % (shlex.quote(program),
                                         " ".join(shlex.quote(a) for a in args))


def home_of(runner, account):
    res = runner.as_role(account, 'printf "%s" "$HOME"')
    return res.out.strip() if res.ok and res.out.strip().startswith("/") else None


def read_facts(runner, conf):
    res = runner.as_role(conf["ROLE_ACCOUNT"], py(
        FACTS_PY, conf["ENV_FILE"], conf["BRIDGE_CONFIG"],
        conf["STATE_DIR"].rstrip("/") + "/bridge-heartbeat.json"))
    if not res.ok:
        return None
    try:
        return json.loads(res.out)
    except ValueError:
        return None


# ══════════════════════════════════════════════════════════════════════════════════════
# The subcommands
# ══════════════════════════════════════════════════════════════════════════════════════

def cmd_compose(conf, conf_path):
    say("THE SLACK BRIDGE — composed from %s. Nothing live was read; nothing was written."
        % conf_path)
    say("")
    say("PIECE 1 — the bridge app's manifest (its OWN app; card CK-B1):")
    for line in json.dumps(manifest(conf), indent=2).splitlines():
        say("    " + line)
    for scope, why in sorted(SCOPE_CITES.items()):
        say("    %-16s %s" % (scope, why))
    say("")
    say("PIECE 2 — the job's config, written by `install --apply` to %s:"
        % conf["BRIDGE_CONFIG"])
    for line in config_bytes(conf).splitlines():
        say("    " + line)
    say("")
    say("PIECE 3 — the job: %s, as %s, every %s seconds. Its command:"
        % (plist_path(conf), conf["ROLE_ACCOUNT"], conf["INTERVAL_SECONDS"]))
    say("    " + job_command(conf))
    say("")
    say("THE ORDER")
    say("  1. Card CK-B1: create the app, invite it, then `token`.")
    say("  2. `install`, then `install --apply`.")
    say("  3. Card CK-B2: load it. Then `verify`.")
    say("")
    say("This version acts on nothing: a verified yes is recorded and said, and nothing in "
        "the tracker changes.")
    return EX_OK


def cmd_token(conf, runner, sudo, reader=None, tty=None):
    found = agent_markers_present()
    if found:
        return refused(found)
    if not (tty if tty is not None else sys.stdin.isatty()):
        say("BLOCKED: `token` asks at a hidden prompt, and this is not a terminal.")
        return EX_BLOCKED
    try:
        sudo.acquire("`token` writes the bridge app's token into %s as %s."
                     % (conf["ENV_FILE"], conf["ROLE_ACCOUNT"]), "token")
    except se.NoPrivilege:
        return EX_NOPRIV
    value = (reader or getpass.getpass)("The bridge app's Bot User OAuth Token (hidden): ")
    value = (value or "").strip()
    if not TOKEN_RE.match(value):
        say("REFUSED: that is not a Slack bot token (it should start xoxb-). Nothing was "
            "written.")
        return EX_USAGE
    runner.dry_run = False
    res = runner.as_role(conf["ROLE_ACCOUNT"],
                         py(TOKEN_WRITER_PY, conf["ENV_FILE"], conf["SLACK_TOKEN_ENV"],
                            conf["DISPATCHER_ENV_FILE"]),
                         stdin=value + "\n", why="write %s into %s"
                         % (conf["SLACK_TOKEN_ENV"], conf["ENV_FILE"]), secret_stdin=True)
    for line in (res.out or "").splitlines():
        say("  " + line)
    if res.rc == 3:
        return EX_REFUSED
    return EX_OK if res.ok else EX_FAILED


def cmd_install(conf, runner, sudo, apply_it):
    found = agent_markers_present()
    if found:
        return refused(found)
    try:
        sudo.acquire("`install` reads the role account's home and files, and %s the job's "
                     "config and plist." % ("writes" if apply_it else "compares"), "install")
    except se.NoPrivilege:
        return EX_NOPRIV
    home = home_of(runner, conf["ROLE_ACCOUNT"])
    if not home:
        say("UNKNOWN: the home of %s could not be read." % conf["ROLE_ACCOUNT"])
        return EX_UNKNOWN
    facts = read_facts(runner, conf) or {}
    body, plist = config_bytes(conf), render_plist(conf, home)
    plist_now = runner.read(["/bin/cat", plist_path(conf)])
    todo = []
    if facts.get("config_sha256") != sha(body):
        todo.append("config")
    if not (plist_now.ok and plist_now.out == plist):
        todo.append("plist")
    if not todo:
        say("Nothing to change: the config and the plist are the composed ones.")
        print_card("CK-B2", conf)
        return EX_OK
    say("To write: %s." % ", ".join(todo))
    if not apply_it:
        say("DRY RUN: nothing was written. Run it again with --apply.")
        return EX_BLOCKED
    runner.dry_run = False
    if "config" in todo:
        res = runner.as_role(conf["ROLE_ACCOUNT"], py(CONFIG_WRITER_PY, conf["BRIDGE_CONFIG"]),
                             stdin=body, why="write the bridge config")
        if not res.ok:
            say("FAILED: the config could not be written (%s)." % (res.err or "").strip()[:200])
            return EX_FAILED
        say("  " + res.out.strip())
    if "plist" in todo:
        for argv, stdin in ((["/usr/bin/tee", plist_path(conf)], plist),
                            (["/usr/sbin/chown", "root:wheel", plist_path(conf)], None),
                            (["/bin/chmod", "644", plist_path(conf)], None)):
            res = runner.as_root(argv, why="install the bridge plist", stdin=stdin)
            if not res.ok:
                say("FAILED: %s" % (res.err or "").strip()[:200])
                return EX_FAILED
        say("  wrote %s (not loaded)" % plist_path(conf))
    say("")
    print_card("CK-B2", conf)
    return EX_OK


def cmd_verify(conf, runner, sudo, now=None):
    import time
    found = agent_markers_present()
    if found:
        return refused(found)
    try:
        sudo.acquire("`verify` reads the role account's files and runs the bridge's own dry "
                     "run as that account. It changes nothing.", "verify")
    except se.NoPrivilege:
        return EX_NOPRIV
    runner.dry_run = True
    rows = []
    home = home_of(runner, conf["ROLE_ACCOUNT"])
    facts = read_facts(runner, conf)
    if not home or facts is None:
        rows.append(("role-account", se.UNKNOWN, "the role account's files could not be read"))
    else:
        rows.append(("config", se.ALREADY_DONE if facts.get("config_sha256") ==
                     sha(config_bytes(conf)) else se.BLOCKED,
                     "the job's config is the composed one" if facts.get("config_sha256")
                     == sha(config_bytes(conf)) else "run `install --apply`"))
        names = facts.get("env_names") or []
        missing = [n for n in (conf["SLACK_TOKEN_ENV"], conf["LINEAR_KEY_ENV"]) if n not in names]
        rows.append(("credentials", se.UNKNOWN if facts.get("env_error") else
                     (se.BLOCKED if missing else se.ALREADY_DONE),
                     "the env file could not be read (%s)" % facts.get("env_error")
                     if facts.get("env_error") else ("missing by name: %s; run `token`"
                                                     % ", ".join(missing) if missing else
                                                     "both credentials are named")))
        plist_now = runner.read(["/bin/cat", plist_path(conf)])
        rows.append(("plist", se.ALREADY_DONE if plist_now.ok and plist_now.out ==
                     render_plist(conf, home) else se.BLOCKED,
                     "the plist is the composed one" if plist_now.ok and plist_now.out ==
                     render_plist(conf, home) else "run `install --apply`"))
        dry = runner.as_role(conf["ROLE_ACCOUNT"], job_command(conf) + " --dry-run")
        last = ((dry.out or "").strip().splitlines() or [""])[-1][:200]
        rows.append(("dry-run", se.ALREADY_DONE if dry.rc == 0 else se.FAILED,
                     last or "the job printed nothing"))
        loaded = runner.as_root(["/bin/launchctl", "print", "system/%s" % conf["JOB_LABEL"]])
        rows.append(("loaded", se.ALREADY_DONE if loaded.ok else se.BLOCKED,
                     "launchd holds the job" if loaded.ok else "not loaded: card CK-B2"))
        beat = facts.get("heartbeat") or {}
        age = None
        try:
            import calendar
            age = (now or time.time()) - calendar.timegm(time.strptime(beat.get("at") or "",
                                                                       "%Y-%m-%dT%H:%M:%SZ"))
        except (TypeError, ValueError):
            pass
        fresh = (age is not None and age <= 3 * int(conf["INTERVAL_SECONDS"]) + 60
                 and beat.get("exit") == 0 and not beat.get("dry"))
        rows.append(("heartbeat", se.ALREADY_DONE if fresh else
                     (se.BLOCKED if age is None else se.FAILED),
                     ("its last pass, %ds ago, exited 0" % age) if fresh else
                     ("no heartbeat yet" if age is None else
                      "its last pass was %ss ago with exit %s: %s"
                      % (int(age), beat.get("exit"), (beat.get("summary") or "")[:160]))))
    for check, outcome, detail in rows:
        say("  %-13s %-17s %s" % (check, outcome, detail))
    worst = [o for _c, o, _d in rows]
    for outcome, code in ((se.FAILED, EX_FAILED), (se.UNKNOWN, EX_UNKNOWN),
                          (se.BLOCKED, EX_BLOCKED)):
        if outcome in worst:
            return code
    say("Good: the bridge is installed, loaded and passing.")
    return EX_OK


# ══════════════════════════════════════════════════════════════════════════════════════
# Selftest
# ══════════════════════════════════════════════════════════════════════════════════════

GOOD_CONF = """
ROLE_ACCOUNT=_exdispatch
ROLE_KIT_CLONE=~/kit
CHANNEL_IDS=C0000000AA, C0000000BB
OWNER_USER_ID=00000000-0000-4000-8000-000000000001
TEAM_KEYS=PROD
JOB_LABEL=com.example.bridge
BOT_USER_ID=U0BRIDGE01
DISPATCHER_ENV_FILE=~/dispatcher.env
"""


class _FakeSudo(object):
    def __init__(self, ok=True):
        self.ok, self.acquisitions = ok, 0

    def acquire(self, why, resume):
        self.acquisitions += 1
        if not self.ok:
            raise se.NoPrivilege("declined")
        return True


class _RoleRunner(se.Runner):
    """Runs the role-account programs for real, through /bin/sh, with HOME a temp home;
    root commands write into a temp tree; launchctl answers from `loaded`."""

    def __init__(self, home, root, loaded=False, job_rc=0):
        se.Runner.__init__(self)
        self.home, self.root, self.loaded, self.job_rc = home, root, loaded, job_rc
        self.stdins = []

    def _exec(self, argv, stdin, timeout, cwd=None):
        import subprocess
        self.stdins.append(stdin)
        if argv[:2] == ["sudo", "-u"]:
            script = argv[-1]
            if "pipeline_bridge.py" in script and "--dry-run" in script:
                return se.Result(self.job_rc, "bridge: 0 message(s) examined (dry run)\n", "")
            p = subprocess.run(["/bin/sh", "-c", script], input=stdin, capture_output=True,
                               text=True, env={"HOME": self.home, "PATH": "/usr/bin:/bin"})
            return se.Result(p.returncode, p.stdout, p.stderr)
        if argv[:1] == ["sudo"]:
            argv = argv[1:]
        if argv[:2] == ["/bin/launchctl", "print"]:
            return se.Result(0 if self.loaded else 113, "", "")
        if argv[:1] == ["/usr/bin/tee"]:
            path = self.root + argv[1]
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                fh.write(stdin)
            return se.Result(0, stdin, "")
        if argv[:1] in (["/usr/sbin/chown"], ["/bin/chmod"]):
            return se.Result(0, "", "")
        if argv[:1] == ["/bin/cat"]:
            path = self.root + argv[1]
            if not os.path.exists(path):
                return se.Result(1, "", "No such file")
            return se.Result(0, open(path).read(), "")
        return se.Result(127, "", "unexpected: %r" % (argv,))


def selftest():
    import io
    import contextlib
    import tempfile
    failures, cases = [], [0]

    def check(name, got, want):
        cases[0] += 1
        if got != want:
            failures.append("%s: got %r, want %r" % (name, got, want))

    def cap(fn, *a, **kw):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = fn(*a, **kw)
        return rc, buf.getvalue()

    saved = dict(os.environ)
    for m in AGENT_ENV_MARKERS:
        os.environ.pop(m, None)
    try:
        values, perrs = parse_conf(GOOD_CONF)
        conf, errs = validate_conf(values)
        check("good-conf", (perrs, errs), ([], []))
        check("config-passes-the-jobs-own-loader",
              bridge.validate_config(dict(compose_config(conf), state_dir="/tmp/x-%d"
                                          % os.getpid()))["channel_ids"],
              ["C0000000AA", "C0000000BB"])
        check("config-acts-on-nothing", compose_config(conf)["act"], False)
        example = os.path.join(os.path.dirname(HERE), "bridge.conf.example")
        ex_conf, ex_errs = load_conf(example)
        check("example-conf-is-valid", (ex_errs, sorted(k for k in CONF_KEYS
                                                       if k not in open(example).read())),
              ([], []))
        _c, errs = validate_conf(dict(values, SLACK_TOKEN_ENV="SLACK_BOT_TOKEN",
                                      JOB_LABEL="nope", INTERVAL_SECONDS="5",
                                      CHANNEL_IDS="general"))
        joined = " | ".join(errs)
        for needle in ("JOB_LABEL", "INTERVAL_SECONDS"):
            check("conf-names:" + needle, needle in joined, True)
        _c, errs = validate_conf(dict(values, SLACK_TOKEN_ENV="SLACK_BOT_TOKEN"))
        check("conf-refuses-the-chat-bots-token-name", any("chat bot" in e for e in errs), True)
        _v, perrs = parse_conf("NOPE=1\nbroken line\nTEAM_KEYS=A\nTEAM_KEYS=B\n"
                               "OWNER_USER_ID=xoxb-%s\n" % ("z" * 30))
        check("conf-parse-errors", len(perrs), 4)
        check("conf-duplicate-names-both-lines", any("lines 3 and 4" in e for e in perrs), True)
        check("conf-credential-value-refused-by-key-only",
              any("OWNER_USER_ID holds a value shaped like a credential" in e for e in perrs)
              and not any("z" * 30 in e for e in perrs), True)
        m = manifest(conf)
        check("manifest-scopes-exact", m["oauth_config"]["scopes"]["bot"],
              ["chat:write", "groups:history", "users:read"])
        check("manifest-no-events-no-socket", ("event_subscriptions" in m["settings"],
                                               m["settings"]["socket_mode_enabled"]),
              (False, False))
        cmd = job_command(conf)
        check("job-sources-the-env-file-then-runs-the-bridge",
              cmd.startswith('set -a; . "$HOME/.stage-e/env"; set +a; exec /usr/bin/python3 '
                             '"$HOME/kit/scripts/pipeline_bridge.py" run --config '
                             '"$HOME/.stage-e/bridge.json"'), True)
        plist = render_plist(conf, "/srv/role-home")
        check("plist-runs-as-the-role-account",
              ("<string>_exdispatch</string>" in plist, "com.example.bridge" in plist,
               "<integer>60</integer>" in plist, "/srv/role-home/.stage-e/bridge.log"
               in plist), (True, True, True, True))
        check("cards-all-print", all(cap(print_card, c, conf)[1].startswith(c)
                                     for c in CARDS), True)
        rc, out = cap(cmd_compose, conf, "bridge.conf")
        check("compose-prints-every-piece", (rc, all(p in out for p in (
            "PIECE 1", "PIECE 2", "PIECE 3", "acts on nothing"))), (EX_OK, True))

        with tempfile.TemporaryDirectory() as tmp:
            home, root = os.path.join(tmp, "home"), os.path.join(tmp, "root")
            os.makedirs(os.path.join(home, ".stage-e"))
            envp = os.path.join(home, ".stage-e", "env")
            with open(envp, "w") as fh:
                fh.write("STAGE_E_LINEAR_API_KEY=lin_api_%s\nNOTIFIER_TOKEN=xoxb-%s\n"
                         % ("k" * 30, "n" * 30))
            os.chmod(envp, 0o600)

            # token: refused under a model; a non-token refused; the notifier's refused;
            # a good one written, its value never printed.
            os.environ[AGENT_ENV_MARKERS[0]] = ""
            r = _RoleRunner(home, root)
            check("token-refused-under-a-model",
                  cap(cmd_token, conf, r, _FakeSudo(), lambda _p: "x", True)[0], EX_REFUSED)
            check("install-refused-under-a-model",
                  cap(cmd_install, conf, r, _FakeSudo(), True)[0], EX_REFUSED)
            check("verify-refused-under-a-model",
                  cap(cmd_verify, conf, r, _FakeSudo())[0], EX_REFUSED)
            check("refusals-ran-nothing", r.reads + r.writes, [])
            os.environ.pop(AGENT_ENV_MARKERS[0])
            check("token-needs-a-terminal",
                  cap(cmd_token, conf, r, _FakeSudo(), lambda _p: "x", False)[0], EX_BLOCKED)
            check("token-not-a-token",
                  cap(cmd_token, conf, r, _FakeSudo(), lambda _p: "hello", True)[0], EX_USAGE)
            rc, out = cap(cmd_token, conf, r, _FakeSudo(), lambda _p: "xoxb-" + "n" * 30, True)
            check("token-already-held-by-another-name", (rc, "NOTIFIER_TOKEN" in out),
                  (EX_REFUSED, True))
            with open(os.path.join(home, "dispatcher.env"), "w") as fh:
                fh.write("SLACK_BOT_TOKEN=xoxb-%s\n" % ("c" * 30))
            rc, out = cap(cmd_token, conf, r, _FakeSudo(), lambda _p: "xoxb-" + "c" * 30, True)
            check("token-the-chat-bots-own-refused", (rc, "SLACK_BOT_TOKEN" in out,
                                                      "c" * 30 in out), (EX_REFUSED, True, False))
            good = "xoxb-" + "b" * 30
            rc, out = cap(cmd_token, conf, r, _FakeSudo(), lambda _p: good, True)
            env_text = open(envp).read()
            check("token-written", (rc, "BRIDGE_SLACK_BOT_TOKEN=%s" % good in env_text,
                                    "STAGE_E_LINEAR_API_KEY=" in env_text,
                                    oct(os.stat(envp).st_mode & 0o777)), (EX_OK, True, True,
                                                                          "0o600"))
            check("token-never-printed", good in out, False)
            check("token-went-on-stdin-only",
                  any(good in " ".join(w["argv"]) for w in r.writes), False)
            check("token-backed-up-first",
                  os.listdir(os.path.join(home, ".stage-e", "backups"))[0].startswith(
                      "env.bridge-setup."), True)
            for _ in range(4):
                cap(cmd_token, conf, r, _FakeSudo(), lambda _p: good, True)
            check("token-keeps-three-backups",
                  len([n for n in os.listdir(os.path.join(home, ".stage-e", "backups"))
                       if n.startswith("env.bridge-setup.")]), 3)
            check("no-sudo-declined-token",
                  cap(cmd_token, conf, r, _FakeSudo(ok=False), lambda _p: good, True)[0],
                  EX_NOPRIV)

            # install: dry run writes nothing; --apply writes the config and plist and loads
            # nothing; a second run has nothing to do.
            r = _RoleRunner(home, root)
            rc, out = cap(cmd_install, conf, r, _FakeSudo(), False)
            check("install-dry-run-writes-nothing",
                  (rc, os.path.exists(os.path.join(home, ".stage-e", "bridge.json")),
                   os.path.exists(root + plist_path(conf))), (EX_BLOCKED, False, False))
            rc, out = cap(cmd_install, conf, r, _FakeSudo(), True)
            cfg_written = open(os.path.join(home, ".stage-e", "bridge.json")).read()
            check("install-apply-writes-both", (rc, cfg_written == config_bytes(conf),
                                                open(root + plist_path(conf)).read() ==
                                                render_plist(conf, home)), (EX_OK, True, True))
            check("install-never-loads", any("launchctl" in " ".join(w["argv"]) and
                                             ("bootstrap" in " ".join(w["argv"]))
                                             for w in r.writes), False)
            rc, out = cap(cmd_install, conf, _RoleRunner(home, root), _FakeSudo(), True)
            check("install-again-nothing-to-do", (rc, "Nothing to change" in out),
                  (EX_OK, True))

            # verify: not loaded -> blocked; loaded with a fresh good heartbeat -> 0; a stale
            # or failing heartbeat -> failed; a dry run that fails -> failed.
            rc, out = cap(cmd_verify, conf, _RoleRunner(home, root, loaded=False), _FakeSudo())
            check("verify-not-loaded-blocks", (rc, "card CK-B2" in out), (EX_BLOCKED, True))
            os.makedirs(os.path.join(home, ".stage-e", "state"))
            beat = os.path.join(home, ".stage-e", "state", "bridge-heartbeat.json")
            with open(beat, "w") as fh:
                json.dump({"schema": bridge.HEARTBEAT_SCHEMA, "at": "2026-10-10T12:00:00Z",
                           "exit": 0, "dry": False, "summary": "ok"}, fh)
            import calendar
            import time as _t
            at = calendar.timegm(_t.strptime("2026-10-10T12:00:00Z", "%Y-%m-%dT%H:%M:%SZ"))
            rc, out = cap(cmd_verify, conf, _RoleRunner(home, root, loaded=True), _FakeSudo(),
                          now=at + 30)
            check("verify-clean", (rc, "Good:" in out), (EX_OK, True))
            check("verify-reads-no-credential-value", ("k" * 30 in out, "b" * 30 in out),
                  (False, False))
            rc, out = cap(cmd_verify, conf, _RoleRunner(home, root, loaded=False), _FakeSudo(),
                          now=at + 30)
            check("verify-fresh-beat-but-not-loaded-blocks",
                  (rc, bool(re.search(r"loaded\s+BLOCKED", out))), (EX_BLOCKED, True))
            rc, out = cap(cmd_verify, conf, _RoleRunner(home, root, loaded=True), _FakeSudo(),
                          now=at + 3600)
            check("verify-stale-heartbeat-fails", rc, EX_FAILED)
            rc, out = cap(cmd_verify, conf, _RoleRunner(home, root, loaded=True, job_rc=1),
                          _FakeSudo(), now=at + 30)
            check("verify-failing-dry-run-fails", rc, EX_FAILED)
            with open(envp, "w") as fh:
                fh.write("STAGE_E_LINEAR_API_KEY=x\n")
            rc, out = cap(cmd_verify, conf, _RoleRunner(home, root, loaded=True), _FakeSudo(),
                          now=at + 30)
            check("verify-missing-token-name-blocks",
                  (rc, "BRIDGE_SLACK_BOT_TOKEN" in out), (EX_BLOCKED, True))
    finally:
        os.environ.clear()
        os.environ.update(saved)

    # The source writes nothing to the tracker, and loads no job.
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    body = src[:src.index("# Selftest\n")]
    check("no-tracker-write", "mutation" in body.lower(), False)
    check("never-bootstraps-itself", '"bootstrap"' in body or "'bootstrap'" in body, False)

    if failures:
        print("FAIL: pipeline_bridge_setup selftest")
        for f in failures:
            print("  - %s" % f)
        return EX_FAILED
    print("OK: pipeline_bridge_setup selftest (%d cases)" % cases[0])
    return EX_OK


def main(argv=None):
    parser = argparse.ArgumentParser(description="Install the Slack bridge.")
    parser.add_argument("command", nargs="?",
                        choices=("compose", "token", "install", "verify", "card"))
    parser.add_argument("card_id", nargs="?")
    parser.add_argument("--conf", default=DEFAULT_CONF)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.command in ("token", "install", "verify"):
        found = agent_markers_present()
        if found:
            return refused(found)
    if args.command == "card":
        if args.card_id not in CARDS:
            say("usage: card %s" % "|".join(sorted(CARDS)))
            return EX_USAGE
        conf, errs = load_conf(args.conf)
        print_card(args.card_id, None if errs else conf, args.conf)
        return EX_OK
    if not args.command:
        parser.print_help()
        return EX_USAGE
    conf, errs = load_conf(args.conf)
    if errs:
        say("CONF PROBLEMS in %s:" % args.conf)
        for e in errs:
            say("  - %s" % e)
        return EX_USAGE
    if args.command == "compose":
        return cmd_compose(conf, args.conf)
    runner, sudo = se.Runner(), se.SudoSession()
    if args.command == "token":
        return cmd_token(conf, runner, sudo)
    if args.command == "install":
        return cmd_install(conf, runner, sudo, args.apply)
    return cmd_verify(conf, runner, sudo)


if __name__ == "__main__":
    sys.exit(main())
