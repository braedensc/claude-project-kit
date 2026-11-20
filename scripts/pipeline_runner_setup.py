#!/usr/bin/env python3
"""The runner's installer (KIT-859): its settings, with every lane off for every project, and its
always-on job, checked or applied with its answers given (KIT-655). Guide: docs/RUNNER.md.

    python3 scripts/pipeline_runner_setup.py check [--conf runner.conf]
    echo '{"schema": "runner-answers/1", "consent": "yes"}' | python3 scripts/pipeline_runner_setup.py apply
    python3 scripts/pipeline_runner_setup.py push-token|tracker-key|writer-key [--conf runner.conf] [--watch-conf update.conf]
    python3 scripts/pipeline_runner_setup.py --selftest

Both print one JSON document of rows on stdout, on every exit, and every human word on stderr, as the
notifier's installer does (KIT-850). `check` reads and changes nothing. It asks nothing: every sudo
call is `sudo -n`, so with no live sudo session, or under a model, a row that needs one says UNKNOWN,
never current. Its rows: the role account's home; the kit the job runs (the clone's commit, the
runner's scripts level with this checkout); the runner's settings as installed, with each project's
lanes; the root-owned copy the job runs from; and the job (its file, whether it starts at boot, what
launchd runs).

`apply` is refused under a model before it reads anything. Its answers come through a pipe from the
program that runs it, never a terminal, a file or an argument: {"schema": "runner-answers/1",
"consent": "yes"}. Answers it cannot read exit 2; anything but a yes changes nothing and exits 10.
With no live sudo it exits 5.

EVERY LANE OFF.  It writes only the settings it owns, from runner.conf, and gives a new project every
lane off. Switching one on is the owner's yes, per project, and never this file's: so a lane he
switched on, his approved states and every other key it does not write are kept as installed. A file
that is not one JSON object is never written over. `enabled` is the part's own switch, and once
installed it is kept: on stays on, and off (left off by an install, or switched off by him) is never
switched back on here. A first install counts its first pass: the requests waiting in its store and
its unfinished runs. At or below the part's ceiling in pipeline-parts.json it is switched on: `enabled`
true, `launchctl enable`, loaded. Above it, or with a ceiling it cannot read, it is installed and left
off: `enabled` false, `launchctl disable`, not loaded. The document's `left_off` says why, in one line.

THE KEYS IT CARRIES (KIT-919).  runner.conf also carries the runner keys the owner would otherwise write by
hand: the session tools' key file and writer, the queue's settings file, the look tool's folder, the push
token's file (PUSH_TOKEN_FILE, below), and each project's approved states and allowed hosts. A key the conf
names decides, and `off` writes the off itself. A key it leaves out keeps what is installed: the owner's own
value, or an off he set.

THE COPY (KIT-919).  The runner holds every start while the sessions' account could change the folder it
runs from (pipeline_runner_guard.engine_problems). So it runs from ENGINE_DIR: a copy of the kit at the
commit Stage E's clone is at, every entry root's and closed to group and others. `check` compares the copy
file by file with that commit's tree, and runs the runner's own check of the copy for the role account and
for you. `apply` makes or refreshes it, and nothing else does: the files come from this checkout's own git
objects at that commit, never from the role's clone, and only a commit on origin/main is copied. A root
program writes them beside the old copy and swaps the two, and the copy is read back. It never moves back
to an older commit. A job that runs from the copy is stopped before the swap: the `job` row loads it again,
and a refresh that fails loads it again at once.

ITS JOB.  One LaunchDaemon as the role account, kept alive, running the runner from the copy. Stage E's
`run` finds it there, stops it around the clone's move and loads it again; the copy moves only with `apply`
here. The file says Disabled, so the file alone
never starts at boot: only `launchctl enable` does. The command ends with a shell comment naming the
clone's commit, so a moved clone is a changed job, and launchd's own copy says which kit it runs.

PUSH-TOKEN (KIT-907).  The sessions' push token is a file the settings name (`push_token_file`, from the
conf's PUSH_TOKEN_FILE). `push-token` is the one owner step that makes it, at a terminal and never from a
model: a hidden prompt for the token, then a question for the day it ends; both are judged before sudo is
asked for. The role account writes the file, mode 0600, and the end date goes into the health watch's
CREDENTIAL_EXPIRIES under the name `runner-push-token`, where its yearly-credential warning reads it. The
token reaches the role account on standard input only: never an argument, never printed, never logged. It
prints no JSON document. `check` and `apply` carry a `push-token` row: a token file with no recorded end date
is NOT MEASURED, never current.

TRACKER-KEY and WRITER-KEY (KIT-928).  The same step, file and end date for the runner's own tracker key
(`tracker-key` in RUNNER_DIR, filed as `runner-tracker-key`) and the session writer's key (WRITER_KEY_FILE, else
the installed `session_tools.key_file`, filed as `runner-writer-key`). Each has its own `check` row, which says
the file's owner, mode and presence in words and never its text.

Exits: 0 done or current · 1 failed · 2 settings · 3 refused · 4 not measured · 5 no live sudo
(apply) · 10 work to do (check), or not agreed, or left off.
"""
import argparse
import hashlib
import io
import json
import os
import plistlib
import re
import stat
import subprocess
import sys
import tarfile
from xml.sax.saxutils import escape as _esc

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from pipeline_agent_env import AGENT_ENV_MARKERS  # noqa: E402  (the one list, imported, never copied)
from pipeline_stage_e_setup import (  # noqa: E402  (Stage E's outcomes, exits and errors)
    ALREADY_DONE, DONE, EX_BLOCKED, EX_FAILED, EX_NOPRIV, EX_OK, EX_REFUSED, EX_UNKNOWN, EX_USAGE,
    FAILED, UNKNOWN, WOULD_CHANGE, Refusal, SetupError, Unknown, say, split_list)
import pipeline_stage_e_setup as se  # noqa: E402
import pipeline_notifier_setup as ns  # noqa: E402  (its shell fragments, path rules and scrub)
import pipeline_queue_setup as qs  # noqa: E402  (the closure walk, the role-file read, `~` made a home)
import pipeline_bridge_push_setup as push  # noqa: E402  (the kept-alive plist, installed disabled)
import pipeline_runner as runner_job  # noqa: E402  (its own loader judges what this composes)
import pipeline_session_tools as session_tools  # noqa: E402  (which lanes hold the session writer's key)
import pipeline_tech_lead_tools as tl_tools  # noqa: E402  (the tracker key's one shape)
import pipeline_runner_guard as runner_guard  # noqa: E402  (its own check of the folder it runs from)
import pipeline_runner_store as store  # noqa: E402
import pipeline_queue_pool as pool  # noqa: E402
import pipeline_update as pu  # noqa: E402  (the health watch's conf and its credential dates)
import pipeline_watch as watch  # noqa: E402  (its own writer of CREDENTIAL_EXPIRIES, never a second one)

DEFAULT_CONF = "runner.conf"
RUNNER_SCRIPT = "pipeline_runner.py"
RUNNER_SCRIPTS = qs.runtime_closure((RUNNER_SCRIPT,))
ROWS_SCHEMA = "runner-setup-rows/1"
ANSWERS_SCHEMA = "runner-answers/1"
ANSWERS_KEYS = frozenset(("schema", "consent"))
ANSWERS_MAX_BYTES = 64 * 1024
CONF_REQUIRED = ("ROLE_ACCOUNT", "CLAUDE_BIN", "OWNER_USER_IDS", "DISPATCHER_USER_IDS", "PROJECTS")
CONF_DEFAULTS = {"ROLE_KIT_CLONE": se.ROLE_KIT_CLONE_PATH, "ENV_FILE": "~/.stage-e/env",
                 "JOB_LABEL": "local.pipeline-runner", "RUNNER_DIR": "~/.pipeline-runner",
                 "TOKEN_ENV": "CLAUDE_WRITER_OAUTH_TOKEN", "PUSH_TOKEN_FILE": "", "WRITER_KEY_FILE": ""}
PUSH_CLOCK = "runner-push-token"       # the name its end date is filed under in CREDENTIAL_EXPIRIES
TRACKER_CLOCK, WRITER_CLOCK = "runner-tracker-key", "runner-writer-key"
KEY_COMMANDS = ("push-token", "tracker-key", "writer-key")      # the owner's steps, one per key file (KIT-928)
PUSH_DATE_YEARS = 5                    # a date further off than this is a typo, not a token's end
# Left invalid on purpose, so an unedited copy of the example cannot run.
CONF_UNEDITED = {"OWNER_USER_IDS": "owner-user-id-1", "DISPATCHER_USER_IDS": "dispatcher-user-id-1"}
_s = lambda text: ns.scrub(text, ())      # noqa: E731  (everything printed passes the notifier's scrub)


def path_ok(v):
    return bool(ns._PATH_RE.match(v or "")) and "/../" not in v + "/" and v.rstrip("/") not in ("", "~")


def projects(text):
    """({key: (the runner's own clone, base)}, [numbers of items that are not KEY=PATH@BASE, or repeat a KEY])."""
    out, bad = {}, []
    for n, item in enumerate(split_list(text), 1):
        key, _eq, rest = (p.strip() for p in item.partition("="))
        repo, _at, base = rest.rpartition("@") if "@" in rest else (rest, "", "main")
        if key in out or not (store.PROJECT_RE.match(key) and path_ok(repo) and runner_job.BASE_RE.match(base)
                              and ".." not in base):
            bad.append(n)
        else:
            out[key] = (repo, base)
    return out, bad


def parse_conf(text, source=DEFAULT_CONF):
    """(values, errors): every bad line, none quoted (a line may hold a credential)."""
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
            errors.append("%s: duplicate %s (line %d)" % (where, key, seen[key]))
        elif not known_key(key):
            errors.append("%s: unknown key %s" % (where, key))
        elif ns.credential_shape(val):
            errors.append("%s: %s carries a CREDENTIAL SHAPE; no key here holds one (not shown)" % (where, key))
        else:
            seen[key], values[key] = n, val
    return values, errors


def validate_conf(values, user=None):
    """(conf, errors): every fault in one pass, by key; the runner's own loader judges the rest."""
    c, e = dict(CONF_DEFAULTS, **values), []
    c["PUSH_TOKEN_UNSET"] = not c["PUSH_TOKEN_FILE"]       # then the installed path, if any, is the owner's and kept
    if not c["PUSH_TOKEN_FILE"]:       # unset: beside the tracker key, in the runner's own folder
        c["PUSH_TOKEN_FILE"] = c["RUNNER_DIR"].rstrip("/") + "/push-token"
    c["WRITER_KEY_UNSET"] = not c["WRITER_KEY_FILE"]       # then the session writer's installed path is the owner's
    if not c["WRITER_KEY_FILE"]:
        c["WRITER_KEY_FILE"] = c["RUNNER_DIR"].rstrip("/") + "/writer-key"

    def need(ok, why):
        if not ok:
            e.append(why)
    for k in CONF_REQUIRED:
        need(c.get(k), "%s is required" % k)
    for k, v in CONF_UNEDITED.items():
        need(c.get(k) != v, "%s is still the example's value" % k)
    acct = c.get("ROLE_ACCOUNT") or ""
    need(not acct or se._ACCOUNT_RE.match(acct) and acct not in ("root", user),
         "ROLE_ACCOUNT must be a local account that is not root and not you")
    need(se._RDNS_RE.match(c["JOB_LABEL"]), "JOB_LABEL is not a reverse-DNS launchd label")
    need(ns._UPPER_SNAKE.match(c["TOKEN_ENV"]), "TOKEN_ENV must be the NAME of an environment variable")
    for k in ("OWNER_USER_IDS", "DISPATCHER_USER_IDS"):
        need(all(pool.ID_RE.match(x) for x in split_list(c.get(k))), "%s must be tracker user ids" % k)
    need(set(split_list(c.get("OWNER_USER_IDS"))).isdisjoint(split_list(c.get("DISPATCHER_USER_IDS"))),
         "OWNER_USER_IDS lists the dispatcher's own user, which never approves")
    need(not c.get("CLAUDE_BIN") or c["CLAUDE_BIN"].startswith("/") and path_ok(c["CLAUDE_BIN"]),
         "CLAUDE_BIN must be an absolute path")
    bad = [k for k in ("ROLE_KIT_CLONE", "ENV_FILE", "RUNNER_DIR", "PUSH_TOKEN_FILE", "WRITER_KEY_FILE")
           if not path_ok(c[k]) and not (k == "PUSH_TOKEN_FILE" and c[k] == OFF)]      # KIT-919: `off` is the off
    e.extend("%s must be `~/…` (the role account's home) or an absolute path, with no `..`" % k for k in bad)
    need("ROLE_KIT_CLONE" in bad or c["ROLE_KIT_CLONE"].rstrip("/") == se.ROLE_KIT_CLONE_PATH,
         "ROLE_KIT_CLONE is not Stage E's clone, %s" % se.ROLE_KIT_CLONE_PATH)
    items = projects(c.get("PROJECTS"))[1]
    need(not items, "PROJECTS item %s is not KEY=PATH@BASE (a lower-case key, the runner's own clone, its "
                    "base branch), or repeats a KEY" % ", ".join(map(str, items)))
    e.extend(path_problems(c, ns.ROLE_HOME_SENTINEL, bad))
    e.extend(engine_dir_problems(c["ENGINE_DIR"]) + carried(c)[2])     # KIT-919
    return c, e


def path_problems(c, home, skip=()):
    """RUNNER_DIR inside the clone, a git working tree a session can write, or holding the env file."""
    p = dict((k, ns.resolve_path(c[k], home))
             for k in ("ROLE_KIT_CLONE", "ENV_FILE", "RUNNER_DIR", "PUSH_TOKEN_FILE", "WRITER_KEY_FILE") if k not in skip)
    under = lambda a, b: a in p and b in p and ns._under(p[a], p[b])      # noqa: E731
    found = ["RUNNER_DIR %s" % why for bad, why in (
        (under("RUNNER_DIR", "ROLE_KIT_CLONE"), "is inside ROLE_KIT_CLONE, a git working tree"),
        (under("ENV_FILE", "RUNNER_DIR"), "holds ENV_FILE")) if bad]
    for key, other in (("PUSH_TOKEN_FILE", "WRITER_KEY_FILE"), ("WRITER_KEY_FILE", "PUSH_TOKEN_FILE")):
        if key in p and "RUNNER_DIR" in p:
            d = p["RUNNER_DIR"].rstrip("/")
            found += ["%s %s" % (key, why) for bad, why in (
                (under(key, "ROLE_KIT_CLONE"), "is inside ROLE_KIT_CLONE, a git working tree"),
                (p[key] in (p.get("ENV_FILE"), p.get(other), d + "/tracker-key", d + "/runner.json"),
                 "is the env file, the tracker key's file, the settings file or the other key file: it holds "
                 "one key alone"),
                (any(ns._under(p[key], d + "/" + sub) for sub in ("work", "home", "store")),
                 "is in the runner's work, home or store folder, where a session can write")) if bad]
    return found


def load_conf(path, user=None):
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        raise SetupError("could not read %s (%s): cp runner.conf.example runner.conf && chmod 600 runner.conf"
                         % (path, exc.strerror))
    values, errors = parse_conf(text, os.path.basename(path))
    conf, more = validate_conf(values, user)
    return conf, errors + more


# ── KIT-919: the copy the runner runs from, and the runner keys runner.conf carries ───────────────────────────
ENGINE_DEFAULT = "/usr/local/pipeline-runner/kit"
CONF_DEFAULTS["ENGINE_DIR"] = ENGINE_DEFAULT
# Where an account's own sessions can write: never the copy's place.
UNSAFE_PREFIXES = ("/Users/", "/tmp/", "/private/tmp/", "/var/tmp/", "/private/var/tmp/", "/var/folders/",
                   "/private/var/folders/")
OFF = "off"
CARRIED_PATHS = (("QUEUE_CONFIG", "queue_config"), ("LOOK_CONFIG_DIR", "look_config_dir"))
SESSION_KEY_FILE, SESSION_WRITER = "SESSION_TOOLS_KEY_FILE", "SESSION_TOOLS_WRITER_USER_ID"
CONF_UNEDITED[SESSION_WRITER] = "writer-user-id-1"
# Per project: APPROVAL_STATES_<KEY> and ALLOWED_HOSTS_<KEY>, the project's key in capitals with `-` as `_`.
PER_PROJECT = (("APPROVAL_STATES_", "approval_states"), ("ALLOWED_HOSTS_", "allowed_hosts"))


def known_key(key):
    return (key in CONF_REQUIRED or key in CONF_DEFAULTS or key in (SESSION_KEY_FILE, SESSION_WRITER)
            or key in dict(CARRIED_PATHS) or project_key(key) is not None)


def project_key(conf_key):
    """(the setting, the project) a per-project key names: ALLOWED_HOSTS_MY_APP is my-app's allowed_hosts."""
    for prefix, setting in PER_PROJECT:
        if conf_key.startswith(prefix) and len(conf_key) > len(prefix):
            return setting, conf_key[len(prefix):].lower().replace("_", "-")
    return None


def states_of(text):
    """{kind: [state id, ...]} from `KIND:STATE_ID, ...`, {} for off, None when any item is not one."""
    if text == OFF:
        return {}
    out = {}
    for item in split_list(text):
        kind, colon, state = (p.strip() for p in item.partition(":"))
        if not (colon and kind in store.STARTS and runner_job.STATE_ID_RE.match(state)):
            return None
        out[kind] = out.get(kind, []) + ([] if state in out.get(kind, []) else [state])
    return out or None


def hosts_of(text):
    """[host, ...], [] for off, None when any is not a host name."""
    if text == OFF:
        return []
    hosts = []
    for host in split_list(text):
        if not runner_job.HOST_RE.match(host):
            return None
        hosts += [] if host in hosts else [host]
    return hosts or None


def carried(c):
    """({setting: value}, {project: {setting: value}}, [problems]): what the conf names of the carried keys. A key it
    leaves out is in neither, so the installed value stays: the owner's own, or an off."""
    top, per, bad = {}, {}, []
    for key, setting in CARRIED_PATHS:
        v = c.get(key)
        if v == OFF:
            top[setting] = None
        elif v is not None and path_ok(v):
            top[setting] = v
        elif v is not None:
            bad.append("%s must be `~/…` (the role account's home), an absolute path with no `..`, or off" % key)
    kf, writer = c.get(SESSION_KEY_FILE), c.get(SESSION_WRITER)
    if kf == OFF and writer is None:
        top["session_tools"] = None
    elif kf is not None or writer is not None:
        if kf in (None, OFF) or not path_ok(kf):
            bad.append("%s must be the session writer's key file (`~/…` or an absolute path), set with %s; or off, "
                       "alone" % (SESSION_KEY_FILE, SESSION_WRITER))
        elif not (writer and pool.ID_RE.match(writer)):
            bad.append("%s must be the session writer's tracker user id" % SESSION_WRITER)
        elif writer in split_list(c.get("OWNER_USER_IDS")):
            bad.append("%s is an owner's id: a session never writes as the owner" % SESSION_WRITER)
        else:
            top["session_tools"] = {"key_file": kf, "writer_user_id": writer}
    names = projects(c.get("PROJECTS"))[0]
    for key in sorted(k for k in c if project_key(k)):
        setting, project = project_key(key)
        value = (states_of if setting == "approval_states" else hosts_of)(c[key])
        if project not in names:
            bad.append("%s names project %s, which PROJECTS does not list" % (key, project))
        elif value is None:
            bad.append("%s must be %s, or off" % (key, "host names" if setting == "allowed_hosts" else
                                                   "KIND:STATE_ID items, a kind being one of " + ", ".join(store.STARTS)))
        else:
            per.setdefault(project, {})[setting] = value
    return top, per, bad


def engine_dir_problems(v):
    """ENGINE_DIR: an absolute path of plain characters, in a folder of its own, never where a session can write."""
    if not (re.match(r"^/[A-Za-z0-9._/-]+$", v or "") and "/../" not in v + "/" and "/./" not in v + "/"
            and os.path.dirname(v.rstrip("/")) not in ("", "/")):
        return ["ENGINE_DIR must be an absolute path two folders deep at least, of letters, digits and . _ - /, "
                "with no `..`"]
    if (v.rstrip("/") + "/").startswith(UNSAFE_PREFIXES):
        return ["ENGINE_DIR is where an account's own sessions can write: pick a system folder, such as %s"
                % ENGINE_DEFAULT]
    return []


def part_ceiling(root=KIT):
    """The part's first-pass ceiling, from pipeline-parts.json in this checkout: the one place it is
    declared. None when it cannot be read, and no ceiling is never "no limit": the part is then left off."""
    import pipeline_parts
    try:
        doc, _schema = pipeline_parts.load(root)
    except (OSError, ValueError):
        return None
    got = [p.get("ceiling") for p in pipeline_parts.parts_of(doc) if p.get("id") == "runner"]
    ok = len(got) == 1 and isinstance(got[0], int) and not isinstance(got[0], bool) and got[0] >= 0
    return got[0] if ok else None


# ── what it composes: the runner's settings and its job, from the conf and what is installed ───────────────
# The keys this installer owns. Every other key in the installed settings is the owner's, and is kept.
OWNED = ("enabled", "store_dir", "work_dir", "session_home", "claude_bin", "token_env", "linear_key_file",
         "push_token_file", "owner_user_ids", "dispatcher_user_ids", "projects")
PROJECT_OWNED = ("repo", "base")


def push_path(c, was):
    """The push token's file as the settings should name it. A conf that sets PUSH_TOKEN_FILE decides, and `off`
    is the off itself, None (KIT-919). One that leaves it unset keeps what the owner installed by hand: a path
    (KIT-886), or an off. Only else does it use the default."""
    if c["PUSH_TOKEN_FILE"] == OFF:
        return None
    held = was.get("push_token_file") if isinstance(was, dict) else None
    if c.get("PUSH_TOKEN_UNSET") and isinstance(was, dict) and "push_token_file" in was and held is None:
        return None
    return held if c.get("PUSH_TOKEN_UNSET") and isinstance(held, str) and held else c["PUSH_TOKEN_FILE"]


def writer_path(c, was):
    """The session writer's key file: the conf's WRITER_KEY_FILE decides; unset, the `session_tools.key_file` the
    owner installed is kept, and only else the default beside the tracker key. Never written into the settings:
    `session_tools` is the owner's whole."""
    st = was.get("session_tools") if isinstance(was, dict) else None
    held = st.get("key_file") if isinstance(st, dict) else None
    return held if c.get("WRITER_KEY_UNSET") and isinstance(held, str) and held else c["WRITER_KEY_FILE"]


def compose(c, on, was=None):
    """The runner's settings: the keys it owns from the conf, the rest of `was` (the installed settings) as it
    is. A project the conf names keeps its own lanes, and its approved states and hosts unless the conf names
    them (KIT-919); a new one gets every lane off. No line here switches a lane on."""
    d, was = c["RUNNER_DIR"].rstrip("/"), was if isinstance(was, dict) else {}
    old = was.get("projects") if isinstance(was.get("projects"), dict) else {}
    doc = dict((k, v) for k, v in was.items() if k not in OWNED)
    doc.update({"enabled": bool(on), "store_dir": d + "/store", "work_dir": d + "/work", "session_home": d + "/home",
                "claude_bin": c["CLAUDE_BIN"], "token_env": c["TOKEN_ENV"], "linear_key_file": d + "/tracker-key",
                "push_token_file": push_path(c, was), "owner_user_ids": split_list(c["OWNER_USER_IDS"]),
                "dispatcher_user_ids": split_list(c["DISPATCHER_USER_IDS"]), "projects": {}})
    for k, (repo, base) in sorted(projects(c["PROJECTS"])[0].items()):
        mine = old.get(k) if isinstance(old.get(k), dict) else {}
        p = dict((pk, pv) for pk, pv in mine.items() if pk not in PROJECT_OWNED)
        p.setdefault("lanes", dict.fromkeys(store.KINDS, False))
        doc["projects"][k] = dict(p, repo=repo, base=base)
    return carry(c, doc)


def carry(c, doc):
    """`doc` with what the conf names of the carried keys: its value, or `off` as the off itself (KIT-919). A key it
    leaves out is not touched, so a rerun keeps the owner's own value and an off he set."""
    top, per, _bad = carried(c)
    doc.update(top)
    for key, settings in per.items():
        doc["projects"][key].update(settings)
    return doc


def settings_body(c, on, home, was=None):
    """The settings file's bytes, or SetupError when the runner's own loader refuses them."""
    doc = compose(c, on, was)
    _cfg, bad = runner_job.load_config(qs.with_home(doc, home))
    if bad:
        raise SetupError("the runner's own loader REFUSES these settings, so nothing was written: %s" % "; ".join(bad))
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def settings_path(c):
    return c["RUNNER_DIR"].rstrip("/") + "/runner.json"


def job_command(c, commit):
    """The runner from its root-owned copy (KIT-919), never from the role account's clone."""
    script = c["ENGINE_DIR"].rstrip("/") + "/scripts/" + RUNNER_SCRIPT
    return ns.exec_prefix(c) + "%s run --config %s # kit %s" % (ns._q(script), ns._q(settings_path(c)), commit)


def plist_body(c, home, commit):
    """launchd expands nothing, so the home and the log are absolute; `$HOME` in the command is /bin/sh's."""
    log = qs.with_home(c["RUNNER_DIR"].rstrip("/") + "/runner.log", home)
    return push.PLIST.format(label=_esc(c["JOB_LABEL"]), account=_esc(c["ROLE_ACCOUNT"]), home=_esc(home),
                             path=_esc(se.DAEMON_PATH), command=_esc(job_command(c, commit)), log=_esc(log))


def lanes_said(doc):
    """Each project's lanes as the installed settings hold them, in words."""
    if not isinstance(doc, dict) or not isinstance(doc.get("projects"), dict):
        return "no settings installed yet" if doc is None else "settings that are not the runner's"
    said = []
    for key, proj in sorted(doc["projects"].items()):
        lanes = proj.get("lanes") if isinstance(proj, dict) else None
        on = sorted(k for k, v in (lanes or {}).items() if v is True) if isinstance(lanes, dict) else ["?"]
        said.append("%s: %s" % (key, "lanes on: " + ", ".join(on) if on else "every lane off"))
    return "; ".join(said) or "no project"


# ── the context, and every sudo call made non-interactive ─────────────────────────────────────────────────
class Runner(se.Runner):
    """Stage E's runner, except that sudo never asks: `-n` fails at once with no live session (KIT-655)."""

    def as_role(self, account, script, stdin=None, why=None, timeout=300, secret_stdin=False):
        argv = ["sudo", "-n", "-u", account, "-H", "/bin/sh", "-c", "cd / && " + script]
        return self.read(argv, stdin, timeout, cwd="/") if why is None else \
            self.write(why, argv, stdin, timeout, secret_stdin, cwd="/")

    def as_root(self, argv, why=None, stdin=None, timeout=300):
        argv = ["sudo", "-n"] + list(argv)
        return self.read(argv, stdin, timeout) if why is None else self.write(why, argv, stdin, timeout)

    # KIT-919: the copy's files go to root as bytes, and the accounts' ids come from the account database.
    ROOT_UID = 0

    def _exec(self, argv, stdin, timeout, cwd=None):
        if not isinstance(stdin, bytes):
            return se.Runner._exec(self, argv, stdin, timeout, cwd=cwd)
        try:
            p = subprocess.run(argv, input=stdin, capture_output=True, timeout=timeout, cwd=cwd or "/")
        except FileNotFoundError as exc:
            return se.Result(127, "", str(exc))
        except subprocess.TimeoutExpired:
            return se.Result(124, "", "timed out after %ss" % timeout)
        return se.Result(p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace"))

    def uid_of(self, account):
        """The account's user id, or None when the account database has none."""
        import pwd
        try:
            return pwd.getpwnam(account).pw_uid
        except KeyError:
            return None

    def user_uid(self):
        return os.getuid()


def agent_markers(env):
    return [m for m in AGENT_ENV_MARKERS if m in env]      # presence, not truthiness


class Ctx(object):
    def __init__(self, conf, runner, env, live, ceiling, scripts=HERE):
        self.conf, self.runner, self.live, self.ceiling, self.scripts = conf, runner, live, ceiling, scripts
        self.agent = agent_markers(env)
        self.home = self.commit = self.on = self.left_off = self.first = None
        self.was = None
        self.watch_conf, self.today = os.path.join(KIT, "update.conf"), None      # the push token's end date

    account = property(lambda self: self.conf["ROLE_ACCOUNT"])

    def _may(self, what):
        if self.agent:
            raise Unknown("%s needs sudo, and this is an agent environment (%s set): no model runs it"
                          % (what, ", ".join(self.agent)), "run it in your own terminal")
        if not self.live:
            raise Unknown("%s needs a live sudo session, and this command asks for no password" % what,
                          "run it from the one command, or after `sudo -v` in this terminal")

    def as_role(self, script, what, stdin=None, why=None, timeout=300):
        self._may(what)
        return self.runner.as_role(self.account, script, stdin=stdin, why=why, timeout=timeout)

    def as_root(self, argv, what, why=None, stdin=None, timeout=300):
        self._may(what)
        return self.runner.as_root(argv, why=why, stdin=stdin, timeout=timeout)


# ── the steps: each returns (ok, detail) or raises Unknown / SetupError ───────────────────────────────────
# What waits in the store, as the role account: `count absent`, or `count <requests> <unfinished runs>`. A
# folder it cannot read is exit 8, never a count of 0. Runs are told by the store's own writer's spelling.
COUNT_SH = ('s=@S@; [ -e "$s" ] || { echo "count absent"; exit 0; }; r=0; u=0; '
            'for d in "$s" "$s/requests" "$s/runs"; do if [ -e "$d" ] && ! { [ -r "$d" ] && [ -x "$d" ]; }; '
            'then exit 8; fi; done; if [ -d "$s/requests" ]; then cd "$s/requests" || exit 8; '
            'for f in rq-*.json; do [ -f "$f" ] && r=$((r+1)); done; fi; '
            'if [ -d "$s/runs" ]; then cd "$s/runs" || exit 8; for f in run-*/run.json; do [ -f "$f" ] || continue; '
            '[ -r "$f" ] || exit 8; if grep -Eq \'"state": "(running|stopping|interrupted)"\' "$f"; '
            'then u=$((u+1)); fi; done; fi; echo "count $r $u"')


def step_home(ctx, apply_it):
    res = ctx.runner.read(["dscl", ".", "-read", "/Users/" + ctx.account, "NFSHomeDirectory"])
    home = (res.out.split("NFSHomeDirectory:", 1) + [""])[1].strip().split("\n")[0].strip() if res.ok else ""
    if not (home.startswith("/") and home != "/" and ns._PATH_RE.match(home)):
        raise SetupError("the role account %s has no home (dscl): install Stage E first" % ctx.account)
    bad = path_problems(ctx.conf, home)
    if bad:
        raise SetupError("; ".join(bad))
    ctx.home = home
    return True, "the role account %s, at %s" % (ctx.account, home)


def checkout_hash(root, name):
    try:
        with open(os.path.join(root, name), "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def step_kit(ctx, apply_it):
    """The kit the job runs: the clone's commit, and its runner scripts byte for byte this checkout's."""
    clone = ctx.conf["ROLE_KIT_CLONE"].rstrip("/")
    res = ctx.as_role(ns.CLONE_HASH_SH.replace("@DIR@", ns._q(clone + "/scripts")).replace(
        "@FILES@", " ".join(RUNNER_SCRIPTS)) + '; h=$(git -C %s rev-parse HEAD 2>/dev/null) || h=none; echo "head $h"'
        % ns._q(clone), "reading the kit clone %s" % clone)
    found, head = ns.parse_clone_hashes(res.out), re.search(r"^head ([0-9a-f]{40})$", res.out or "", re.M)
    unread = [n for n in RUNNER_SCRIPTS if found.get(n, ("",))[0] not in ("sha256", "missing")]
    if not res.ok or unread or not head:
        raise Unknown("the clone %s could not be read whole as %s (exit %d; %s)" % (
            clone, ctx.account, res.rc, ", ".join(unread[:3]) or "its commit"), "run it again")
    off = [n for n in RUNNER_SCRIPTS if found[n][0] != "sha256" or found[n][1] != checkout_hash(ctx.scripts, n)]
    if off:
        raise SetupError("the clone at %s is not level with this checkout (%s): merge the change, then let "
                         "Stage E's `run` move it" % (clone, ", ".join(off[:4])))
    ctx.commit = head.group(1)
    return True, "kit %s, from %s: its %d runner scripts are this checkout's" % (
        ctx.commit[:12], clone, len(RUNNER_SCRIPTS))


# ── KIT-919: the root-owned copy of the kit the runner runs from ──────────────────────────────────────────────
ENGINE_MARK = ".engine-commit"          # the commit the copy holds, written by the root program beside the files
ENGINE_TIMEOUT = 600
# Run as root: `python3 -I -c ENGINE_PY <copy> <uid> <gid> <commit>`, the kit's files as one tar on standard input.
# It takes plain files and folders only, writes them into <copy>.new beside the old copy, gives every entry to
# uid:gid, closed to group and others, then swaps the two. The folder above the copy must be root's and closed.
ENGINE_PY = r'''
import io, os, shutil, stat, sys, tarfile
dest, uid, gid, commit = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
top, new, old = os.path.dirname(dest), dest + ".new", dest + ".old"


def refuse(words):
    sys.stdout.write("refused: %s\n" % words)
    sys.exit(3)


def gone(path):
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    elif os.path.lexists(path):
        os.unlink(path)


try:
    os.umask(0o022)
    if not os.path.lexists(top):
        os.mkdir(top, 0o755)
        os.lchown(top, uid, gid)
    st = os.lstat(top)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != uid or st.st_mode & 0o022:
        refuse("the folder above the copy is not root's alone: %s" % top)
    data = sys.stdin.buffer.read()
    for path in (new, old):
        gone(path)
    os.mkdir(new, 0o700)
    count = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
        for m in tar:
            parts = m.name.split("/")
            if m.name.startswith("/") or any(p in ("", ".", "..") for p in parts):
                refuse("the files name a path outside their folder")
            path = os.path.join(new, *parts)
            if m.isdir():
                os.makedirs(path, 0o755, exist_ok=True)
                continue
            if not m.isreg():
                refuse("not a plain file: %s" % m.name)
            os.makedirs(os.path.dirname(path), 0o755, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o755 if m.mode & 0o100 else 0o644)
            with os.fdopen(fd, "wb") as fh:
                fh.write(tar.extractfile(m).read())
            count += 1
    with open(os.path.join(new, "@MARK@"), "w") as fh:
        fh.write(commit + "\n")
    for here, dirs, names in os.walk(new):
        for name in dirs + names:
            os.lchown(os.path.join(here, name), uid, gid)
    os.lchown(new, uid, gid)
    os.chmod(new, 0o755)
    if os.path.lexists(dest):
        os.rename(dest, old)
    os.rename(new, dest)
    gone(old)
except Exception as exc:
    sys.stdout.write("failed: %s: %s\n" % (type(exc).__name__, exc))
    sys.exit(1)
sys.stdout.write("copied %s %d\n" % (commit, count))
'''.replace("@MARK@", ENGINE_MARK)


def _git(checkout, args, stdin=None):
    """(exit, output bytes): git in this checkout, as you. It reads the checkout's own objects only."""
    env = dict((k, v) for k, v in os.environ.items() if not k.startswith("GIT_"))
    try:
        p = subprocess.run(["git", "-C", checkout] + list(args), input=stdin, capture_output=True, env=env,
                           timeout=300)
    except (OSError, subprocess.SubprocessError):
        return 127, b""
    return p.returncode, p.stdout


def blob_id(data, width=40):
    """git's own id of a file's bytes: sha1, or sha256 in a repository that uses it."""
    return (hashlib.sha1 if width == 40 else hashlib.sha256)(b"blob %d\0" % len(data) + data).hexdigest()


def plain_file(mode, kind):
    """A plain file in git's tree: never a link (120000) or a submodule (160000)."""
    return kind == b"blob" and mode in (b"100644", b"100755")


def kit_tree(checkout, commit):
    """{path: (executable, object id)} of every file at `commit`. A link or a submodule is refused: the copy takes
    plain files only."""
    rc, out = _git(checkout, ["ls-tree", "-r", "-z", "--full-tree", commit])
    tree = {}
    for entry in out.split(b"\0") if rc == 0 else ():
        meta, _tab, path = entry.partition(b"\t")
        mode, kind, oid = (meta.split(b" ") + [b"", b""])[:3]
        if entry and not plain_file(mode, kind):
            raise SetupError("kit %s holds %s, which is not a plain file: the runner's copy takes files only"
                             % (commit[:12], path.decode("utf-8", "replace")))
        if entry:
            tree[path.decode("utf-8", "surrogateescape")] = (mode == b"100755", oid.decode("ascii", "replace"))
    if not tree:
        raise Unknown("this checkout's git could not list the files of kit %s (exit %d)" % (commit[:12], rc),
                      "run it again")
    return tree


def kit_tar(checkout, tree):
    """The tree's files as one tar, from this checkout's objects (`git cat-file --batch`, which runs no filter), each
    checked against its own id."""
    oids = sorted(set(oid for _exe, oid in tree.values()))
    rc, out = _git(checkout, ["cat-file", "--batch"], ("\n".join(oids) + "\n").encode("ascii"))
    blobs, at = {}, 0
    for oid in oids if rc == 0 else ():
        end = out.find(b"\n", at)
        head = out[at:end].split(b" ") if end >= 0 else []
        size = int(head[2]) if len(head) == 3 and head[2].isdigit() else -1
        data = out[end + 1:end + 1 + size] if size >= 0 else b""
        if head[:2] != [oid.encode("ascii"), b"blob"] or blob_id(data, len(oid)) != oid:
            break
        blobs[oid], at = data, end + 2 + size
    if len(blobs) != len(oids):
        raise Unknown("this checkout's git did not give every file of the kit whole (exit %d)" % rc, "run it again")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name in sorted(tree):
            exe, oid = tree[name]
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(blobs[oid]), 0o755 if exe else 0o644
            tar.addfile(info, io.BytesIO(blobs[oid]))
    return buf.getvalue()


def entry_problem(st, root_uid):
    """Why an entry of the copy is not root's alone, or ''."""
    return ("is a link" if stat.S_ISLNK(st.st_mode) else "is not root's" if st.st_uid != root_uid else
            "may be written by group or others" if st.st_mode & 0o022 else "")


def copy_diff(tree, files):
    """(differ, missing, extra): the copy's files against the commit's tree, by content and executable bit."""
    return (sorted(n for n in tree if n in files and files[n] != tree[n]), sorted(set(tree) - set(files)),
            sorted(set(files) - set(tree)))


def read_copy(dest, root_uid, width=40):
    """(files, problems, mark), or None when there is no copy. files is {path: (executable, object id)}; a problem is
    an entry that is a link, not root's, or open to group or others, named inside the copy; mark is the commit the
    copy says it holds. Unknown when any of it cannot be read: a copy never read whole is never taken as fine."""
    files, problems, mark = {}, [], None

    def look(path, st):
        why = entry_problem(st, root_uid)
        if why:
            problems.append("%s %s" % ("its folder" if path == dest else os.path.relpath(path, dest), why))

    def unread(exc):
        raise exc
    try:
        look(dest, os.lstat(dest))
    except FileNotFoundError:
        return None
    try:
        for here, dirs, names in os.walk(dest, onerror=unread):
            for name in sorted(dirs) + sorted(names):
                path = os.path.join(here, name)
                st = os.lstat(path)
                look(path, st)
                if not stat.S_ISREG(st.st_mode):
                    continue
                with open(path, "rb") as fh:
                    data = fh.read()
                rel = os.path.relpath(path, dest)
                if rel == ENGINE_MARK:
                    mark = data.decode("ascii", "replace").strip()
                else:
                    files[rel] = (bool(st.st_mode & 0o100), blob_id(data, width))
    except OSError as exc:
        raise Unknown("the copy %s could not be read whole (%s)" % (dest, exc.strerror or type(exc).__name__),
                      "make it readable, or let `apply` make it again")
    return files, problems, mark if re.match(r"^[0-9a-f]{40}([0-9a-f]{24})?$", mark or "") else None


def engine_state(ctx):
    """(current, words, tree): the copy against the commit Stage E's clone is at, read as you, no sudo."""
    dest, commit, checkout = ctx.conf["ENGINE_DIR"].rstrip("/"), ctx.commit, os.path.dirname(ctx.scripts)
    if _git(checkout, ["cat-file", "-e", commit + "^{commit}"])[0] != 0:
        raise Unknown("this checkout (%s) does not have kit %s, the commit Stage E's clone is at" % (checkout, commit[:12]),
                      "git -C %s fetch origin, then run this again" % checkout)
    rc = _git(checkout, ["merge-base", "--is-ancestor", commit, "origin/main"])[0]
    if rc == 1:
        raise SetupError("kit %s, the commit Stage E's clone is at, is not on this checkout's origin/main: only a "
                         "reviewed and merged commit is copied for the runner" % commit[:12])
    if rc != 0:
        raise Unknown("whether kit %s is on origin/main could not be told (git exit %d)" % (commit[:12], rc),
                      "run it again")
    tree, have = kit_tree(checkout, commit), read_copy(dest, ctx.runner.ROOT_UID, len(commit))
    if have is None:
        return False, "no copy at %s yet; Stage E's clone is at kit %s" % (dest, commit[:12]), tree
    files, problems, mark = have
    if mark and mark != commit:
        rc = _git(checkout, ["merge-base", "--is-ancestor", commit, mark])[0]
        if rc == 0:
            raise SetupError("Stage E's clone is at kit %s, older than the copy's kit %s: the copy never moves back. "
                             "Let Stage E's `run` move the clone forward" % (commit[:12], mark[:12]))
        if rc != 1:
            raise Unknown("whether kit %s is older than the copy's kit %s could not be told (git exit %d)"
                          % (commit[:12], mark[:12], rc), "git -C %s fetch origin, then run this again" % checkout)
    for who, uid in (("the role account", ctx.runner.uid_of(ctx.account)), ("you", ctx.runner.user_uid())):
        if uid is None:
            raise Unknown("the role account %s has no user id" % ctx.account, "install Stage E first")
        problems += ["for %s: %s" % (who, p) for p in runner_guard.engine_problems(dest, uid=uid)]
    differ, missing, extra = copy_diff(tree, files)
    # The reason first and the copy's place last: a row's words are cut at a length, and a long path must
    # never push the reason past it.
    if not (differ or missing or extra or problems or mark != commit):
        return True, "the copy is kit %s, the commit Stage E's clone is at: %d files, each root's, and none of it " \
                     "or above it the role account or you can change; it is at %s" % (commit[:12], len(tree), dest), tree
    said = ["%d %s" % (len(v), w) for v, w in ((differ, "differ"), (missing, "missing"), (extra, "extra")) if v]
    return False, "the copy holds kit %s, and Stage E's clone is at kit %s%s%s; it is at %s" % (
        (mark or "unknown")[:12], commit[:12], ": " + ", ".join(said) + " file(s)" if said else "",
        "; " + "; ".join(problems[:3]) + (" and %d more" % (len(problems) - 3) if len(problems) > 3 else "")
        if problems else "", dest), tree


def step_engine(ctx, apply_it):
    """The root-owned copy of the kit the runner runs from. Only `apply` makes or refreshes it, as root."""
    if None in (ctx.home, ctx.commit):
        raise Unknown("waits on `home` and `kit`", "clear those rows first")
    dest = ctx.conf["ENGINE_DIR"].rstrip("/")
    if ns._under(os.path.realpath(dest), os.path.realpath(ctx.home)):
        raise SetupError("ENGINE_DIR %s is in the role account's home, which that account can change" % dest)
    current, words, tree = engine_state(ctx)
    if current or not apply_it:
        return current, words if current else "would copy kit %s there as root: %s" % (ctx.commit[:12], words)
    body = kit_tar(os.path.dirname(ctx.scripts), tree)
    stopped = stop_for_swap(ctx, dest)
    try:
        res = ctx.as_root(["/usr/bin/python3", "-I", "-c", ENGINE_PY, dest, "0", "0", ctx.commit],
                          "copying kit %s to %s" % (ctx.commit[:12], dest), why="copy kit %s to %s, root's"
                          % (ctx.commit[:12], dest), stdin=body, timeout=ENGINE_TIMEOUT)
        last = ((res.out or "").strip().splitlines() or [""])[-1]
        if not res.ok or last != "copied %s %d" % (ctx.commit, len(tree)):
            raise SetupError("the root program did not copy kit %s (exit %d): %s; the copy's place is %s" % (
                ctx.commit[:12], res.rc, _s(last or (res.err or "").strip())[:200] or "it said nothing", dest))
        again, words, _tree = engine_state(ctx)
        if not again:
            raise SetupError("kit %s was copied, and reading the copy back does not match: %s"
                             % (ctx.commit[:12], words))
    except SetupError as exc:           # never leave the runner stopped for a refresh that did not happen
        if stopped:
            raise SetupError("%s; %s" % (load_again(ctx), exc))       # the job's state first, then why
        raise
    except Unknown as exc:
        if stopped:
            raise Unknown("%s; %s" % (load_again(ctx), exc.what), exc.remedy)
        raise
    return False, "copied as root, then read back: %s%s" % (
        words, "; its job was stopped for the swap, and the `job` row loads it again" if stopped else "")


def stop_for_swap(ctx, dest):
    """Stop the runner's job before its copy is swapped, when launchd runs it from that copy: a late import, or the
    guard a running session starts for a tool call, never comes from a copy half moved. True when it stopped it."""
    label = ctx.conf["JOB_LABEL"]
    up, args = loaded(ctx, label)
    if not (up and any(dest + "/scripts/" in str(a) for a in args or ())):
        return False
    res = ctx.as_root(["launchctl", "bootout", "system/" + label], "stopping system/" + label,
                      why="stop system/%s while its copy is swapped" % label)
    if not (res.ok and se._wait_until_gone(ctx.runner, label, 60)[0]):
        raise SetupError("could not stop system/%s before its copy is swapped (exit %d): nothing was swapped"
                         % (label, res.rc))
    return True


def load_again(ctx):
    """Load the runner's job again, as its file is, after a refresh that failed: words that say what happened."""
    label = ctx.conf["JOB_LABEL"]
    res = ctx.as_root(["launchctl", "bootstrap", "system", se._dispatcher_plist(label)], "loading system/" + label,
                      why="load system/%s again" % label)
    return ("its job, stopped for the swap, was loaded again" if res.ok else
            "its job, stopped for the swap, could NOT be loaded again (exit %d): run `apply` again" % res.rc)


def first_pass(ctx):
    """What its first pass would act on: (requests waiting in its store, its unfinished runs)."""
    where = ctx.conf["RUNNER_DIR"].rstrip("/") + "/store"
    res = ctx.as_role(COUNT_SH.replace("@S@", ns._q(where)), "counting what waits in the runner's store")
    m = re.search(r"^count (absent|(\d+) (\d+))$", res.out or "", re.M) if res.ok else None
    if not m:
        raise Unknown("the runner's store %s could not be read as %s (exit %d)" % (where, ctx.account, res.rc),
                      "run it again")
    return (0, 0) if m.group(1) == "absent" else (int(m.group(2)), int(m.group(3)))


def installed(ctx, path):
    """(text, mode, document) of the settings the role account holds, or (None, mode, None). A file that is
    not one JSON object is never written over: what it holds may be the owner's."""
    have, mode = qs.read_role_file(ctx, path)
    if have is None:
        return None, mode, None
    try:
        was = json.loads(have)
    except ValueError:
        was = None
    if not isinstance(was, dict):
        raise SetupError("%s is not one JSON object, so nothing was written over it: fix it, or move it aside, "
                         "then run this again" % path)
    return have, mode, was


def decide(ctx, was, path):
    """(on, why). Installed, the switch is kept: on stays on, and off is never switched back on here. A first
    install is decided by its first pass against the part's ceiling. Sets ctx.left_off and ctx.first."""
    if was is not None:
        on = was.get("enabled") is True
        why = "on before, so it stays on" if on else (
            "its settings say enabled is false, as an earlier install left it or as you set it. Set it to true in "
            "%s to switch it on" % path)
    elif ctx.ceiling is None:
        on, why = False, "its first-pass ceiling could not be read"
    else:
        waiting, unfinished = first_pass(ctx)
        ctx.first = waiting + unfinished
        on = ctx.first <= ctx.ceiling
        why = "its first pass would act on %d waiting request(s) and %d unfinished run(s), %s its ceiling of %d" % (
            waiting, unfinished, "within" if on else "above", ctx.ceiling)
    ctx.left_off = None if on else "left off: " + why
    return on, why


def step_settings(ctx, apply_it):
    """The runner's settings as installed: what it owns from the conf, every lane off for a new project, the
    owner's own keys kept, and its switch."""
    if not ctx.home:
        raise Unknown("waits on `home`", "clear that row first")
    c, path = ctx.conf, settings_path(ctx.conf)
    have, mode, was = installed(ctx, path)
    ctx.on, why = decide(ctx, was, path)
    ctx.was = was
    body = settings_body(c, ctx.on, ctx.home, was)
    said = "enabled %s (%s); installed: %s" % ("true" if ctx.on else "false", why, lanes_said(was))
    if have == body and mode == "600":
        return True, "%s is current: %s" % (path, said)
    state = "absent" if have is None else "differs" if have != body else "mode " + mode
    keys = ", ".join(sorted(projects(c["PROJECTS"])[0]))
    if not apply_it:
        return False, "would write %s (%s) for %s, each lane kept as installed, every lane off for a new " \
                      "project; %s" % (path, state, keys, said)
    res = ctx.as_role(ns.CONFIG_WRITE_SH.replace("@F@", ns._q(path)), "writing " + path, stdin=body,
                      why="write %s (mode 600)" % path)
    back, mode = qs.read_role_file(ctx, path) if res.ok else (None, "")
    if back != body or mode != "600":
        raise SetupError("could not write %s as %s, or reading it back does not match (exit %d, mode %s)"
                         % (path, ctx.account, res.rc, mode))
    return False, "wrote %s for %s, each lane kept as installed, every lane off for a new project; enabled %s " \
                  "(%s)" % (path, keys, "true" if ctx.on else "false", why)


# ── the runner's three key files (KIT-907, KIT-928): each one's file, and the day it ends ───────────────────
# Run as the role account, with the file's path as typed (`~/` included: the program expands it with the
# account's own home). The file's text is never read here: only what kind of file it is.
TOKEN_STATE_PY = r'''
import os, stat, sys
try:
    st = os.lstat(os.path.expanduser(sys.argv[1]))
except FileNotFoundError:
    print("absent")
    sys.exit(0)
except OSError:
    print("unread")
    sys.exit(0)
if stat.S_ISLNK(st.st_mode):
    print("link")
elif not stat.S_ISREG(st.st_mode):
    print("not-file")
elif st.st_uid != os.getuid():
    print("other-owner")
elif st.st_mode & 0o077:
    print("bad-mode")
elif st.st_size == 0:
    print("empty")
else:
    print("ok")
'''
# The key is read from standard input and judged by the owner of its shape (argument 2) before a byte is
# written; argument 3 is what that shape is called, in words. It goes to a new file beside the target, mode 0600, and is moved into place.
TOKEN_WRITE_PY = r'''
import os, re, sys
path = os.path.expanduser(sys.argv[1])
token = sys.stdin.read().strip()
if not re.match(sys.argv[2], token):
    print("REFUSED: what was given is not %s. Nothing was written." % sys.argv[3])
    sys.exit(3)
if os.path.islink(path):
    print("REFUSED: %s is a link, which is never written through. Nothing was written." % path)
    sys.exit(3)
os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
tmp = "%s.new.%d" % (path, os.getpid())
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
try:
    with os.fdopen(fd, "w") as fh:
        fh.write(token + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
except BaseException:
    try:
        os.unlink(tmp)
    except OSError:
        pass
    raise
os.chmod(path, 0o600)
print("wrote %s (%d characters, mode 600)" % (path, len(token)))
'''
# The installed settings' `session_tools.key_file`, as typed, or nothing: what the runner reads the writer's key
# from. Run as the role account; the key file's own text is never touched.
SETTINGS_KEY_PY = r'''
import json, os, sys
try:
    with open(os.path.expanduser(sys.argv[1])) as fh:
        st = json.load(fh).get("session_tools")
    held = st.get("key_file") if isinstance(st, dict) else None
    print(held if isinstance(held, str) else "")
except (OSError, ValueError, AttributeError):
    print("")
'''
TOKEN_STATE_WORDS = {
    "link": "is a link, which the runner never reads",
    "not-file": "is not a regular file",
    "other-owner": "belongs to another account",
    "bad-mode": "can be read by others: it must be mode 0600",
    "empty": "is empty",
}


def key_spec(name):
    """What one key file is: its words, its shape, the name its end date is filed under, where its file is and
    which lanes cannot start without it. Looked up at each use, so a shape changed in its own module is the
    shape here. The path function is given the conf and the installed settings."""
    dirs = lambda c: c["RUNNER_DIR"].rstrip("/")      # noqa: E731
    if name == "push-token":
        return {"label": "push token", "clock": PUSH_CLOCK, "pattern": runner_job.PUSH_TOKEN_RE, "path": push_path,
                "shape": "one GitHub token", "prompt": "The push token (hidden): ",
                "make": "Make it on the code host first: one token that can push branches and open pull requests, "
                        "with an end date.",
                "kinds": [k for k in store.KINDS if runner_job.PUSH_TOKEN_ENV in (runner_job.KINDS.get(k) or {}).get("env", ())]}
    if name == "tracker-key":
        return {"label": "runner's tracker key", "clock": TRACKER_CLOCK, "pattern": tl_tools.KEY_RE,
                "path": lambda c, was: dirs(c) + "/tracker-key", "shape": "one tracker key",
                "prompt": "The runner's tracker key (hidden): ",
                "make": "Make a tracker API key for the runner's own tracker user first (it reads tickets and the "
                        "queue's records), with an end date if the tracker lets you set one.",
                "kinds": []}
    return {"label": "session writer's key", "clock": WRITER_CLOCK, "pattern": tl_tools.KEY_RE, "path": writer_path,
            "shape": "one tracker key", "prompt": "The session writer's key (hidden): ",
            "make": "Make a tracker API key for the user named in `session_tools.writer_user_id` first (never an "
                    "owner), with an end date if the tracker lets you set one.",
            "kinds": list(session_tools.STAGES)}


def installed_key_file(run, conf):
    """The `session_tools.key_file` the role account's installed settings name, or None. A read that fails is
    None too: the conf's path is then used, which `check` will say is not the runner's if it differs."""
    res = run.as_role(conf["ROLE_ACCOUNT"], py(SETTINGS_KEY_PY, conf["RUNNER_DIR"].rstrip("/") + "/runner.json"))
    word = (res.out or "").strip()
    return word if res.ok and word and "\n" not in word and path_ok(word) else None


def py(program, *args):
    """One program for the role account's shell, its arguments quoted; the token is never one of them."""
    import shlex
    return "/usr/bin/python3 -c %s %s" % (shlex.quote(program), " ".join(shlex.quote(a) for a in args))


def token_file_state(runner_call, path):
    """absent, ok, link, not-file, other-owner, bad-mode, empty, or unread: the word the role account's own
    program printed, and `unread` for anything else, so a read that failed is never taken for a good file."""
    res = runner_call(py(TOKEN_STATE_PY, path))
    word = (res.out or "").strip()
    return word if res.ok and (word in ("absent", "ok", "unread") or word in TOKEN_STATE_WORDS) else "unread"


def recorded_end(watch_conf, clock=PUSH_CLOCK):
    """(the date on record or None, why it is not known, the warn days) from the health watch's conf. A conf
    that is missing or will not read is a reason, never a quiet 'no date'; both leave the token without a
    date, and a row says which."""
    try:
        conf = pu.load_conf(watch_conf)
    except pu.ConfError as exc:
        return None, "the health watch's conf could not be read (%s)" % str(exc).split("\n")[0][:120], None
    clocks, problems = pu.credential_clocks(conf.get("CREDENTIAL_EXPIRIES"))
    if problems:
        return None, "CREDENTIAL_EXPIRIES could not be read", None
    warn = int(conf.get("CREDENTIAL_WARN_DAYS") or 30)
    return dict(clocks).get(clock), "", warn


def record_end(watch_conf, when, clock=PUSH_CLOCK):
    """Write `<clock>:<date>` into CREDENTIAL_EXPIRIES with the watch's own writer, every other line as it was,
    and read it back. None when it is on record; else the reason, in words."""
    try:
        conf = pu.load_conf(watch_conf)
        clocks, problems = pu.credential_clocks(conf.get("CREDENTIAL_EXPIRIES"))
        if problems:
            return "CREDENTIAL_EXPIRIES could not be read"
        value, merged = watch.merged_expiries(clocks, [(clock, when)])
        if len(merged) > pu.CREDENTIAL_ENTRIES_MAX:
            return "that would make more than %d entries in CREDENTIAL_EXPIRIES" % pu.CREDENTIAL_ENTRIES_MAX
        path = conf["__source__"]
        with open(path, "rb") as fh:
            data = watch.rewrite_expiries(fh.read(), value)
        watch.write_atomic(path, data)
        with open(path, "rb") as fh:
            back = fh.read()
        got, why = pu.credential_clocks(pu.load_conf(path).get("CREDENTIAL_EXPIRIES"))
    except (OSError, pu.ConfError) as exc:
        return "the health watch's conf could not be written, or read back (%s)" % type(exc).__name__
    if back != data or why or got != merged:
        return "the health watch's conf was written but reads back differently"
    return None


def pushing_lanes(was, kinds=None):
    """[(project, kind)] for each lane of `kinds` (default: those whose sessions hold the push token) that is on
    in the installed settings."""
    kinds = kinds if kinds is not None else key_spec("push-token")["kinds"]
    projs = was.get("projects") if isinstance(was, dict) and isinstance(was.get("projects"), dict) else {}
    return [(name, k) for name, proj in sorted(projs.items()) if isinstance(proj, dict)
            for k in kinds if isinstance(proj.get("lanes"), dict) and proj["lanes"].get(k) is True]


def step_key(name, ctx, apply_it):
    """One key file and its end date, measured and never changed here: the owner's step of its own name writes
    them. Its owner, mode and presence are said in words, never its text. A file that is there with no date on
    record is NOT MEASURED, never current; one that cannot be read is UNKNOWN."""
    if not ctx.home:
        raise Unknown("waits on `home`", "clear that row first")
    spec = key_spec(name)
    label, path = spec["label"], spec["path"](ctx.conf, ctx.was)
    if path is None:                    # off, in runner.conf or as installed (KIT-919): only the push token has it
        pushing = pushing_lanes(ctx.was)
        if pushing:
            raise SetupError("%s has a %s lane on, and the push token is off: every start of it is held. Set "
                             "PUSH_TOKEN_FILE, or switch the lane off" % (pushing[0][0], pushing[0][1]))
        return True, "the push token is off; a code, fix or conflict lane cannot be switched on without one"
    word = token_file_state(lambda script: ctx.as_role(script, "reading the %s file's kind" % label), path)
    if word == "absent":
        pushing = pushing_lanes(ctx.was, spec["kinds"])
        if pushing:
            raise SetupError("%s has a %s lane on, and no %s file at %s: every start of it is held. Run "
                             "`%s`, or switch the lane off" % (pushing[0][0], pushing[0][1], label, path, name))
        return True, ("no %s file at %s yet: run `%s`%s" % (label, path, name,
                      "; a code, fix or conflict lane cannot be switched on without it" if spec["kinds"] else ""))
    if word == "unread":
        raise Unknown("the %s file's kind could not be read" % label, "check the account's folder, then run this again")
    if word != "ok":
        raise SetupError("the %s file %s %s" % (label, path, TOKEN_STATE_WORDS[word]))
    when, why, warn = recorded_end(ctx.watch_conf, spec["clock"])
    if when is None:
        raise Unknown("the %s has no end date on record (%s)" % (label, why or "%s is not in CREDENTIAL_EXPIRIES"
                                                                 % spec["clock"]),
                      "run `python3 scripts/pipeline_runner_setup.py %s` to record it" % name)
    days = (when - (ctx.today or _today())).days
    if days < 0:
        raise SetupError("the %s ended on %s (%d day%s ago): make a new one and run `%s`"
                         % (label, when.isoformat(), -days, "" if days == -1 else "s", name))
    return True, "%s is present, the role account's own and closed to group and others, and ends on %s (in %d day%s); the health watch " \
                 "warns %d days ahead" % (path, when.isoformat(), days, "" if days == 1 else "s", warn)


def step_push_token(ctx, apply_it):
    return step_key("push-token", ctx, apply_it)


def step_tracker_key(ctx, apply_it):
    return step_key("tracker-key", ctx, apply_it)


def step_writer_key(ctx, apply_it):
    return step_key("writer-key", ctx, apply_it)


def _today():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).date()


def loaded(ctx, label):
    """(False, None) when launchd does not hold the job, else (True, its arguments)."""
    res = ctx.as_root(["launchctl", "print", "system/" + label], "asking launchd about " + label)
    text = (res.out or "") + (res.err or "")
    if not res.ok:
        if res.rc == 113 or "Could not find service" in text:
            return False, None
        raise Unknown("could not ask launchd about system/%s (exit %d)" % (label, res.rc), "run it again")
    job = ns.loaded_job(text)
    if not (job and job["arguments"]):
        raise Unknown("launchd holds system/%s and its print names no arguments: what it runs is not measured"
                      % label, "sudo launchctl print system/" + label)
    return True, job["arguments"]


def boot(ctx, label):
    """True when the job starts at the next boot: launchd's override says enabled. The file says Disabled."""
    res = ctx.as_root(["launchctl", "print-disabled", "system"], "asking launchd what starts at boot")
    m = re.search(r'^\s*"%s" => (\w+)\s*$' % re.escape(label), res.out or "", re.M) if res.ok else None
    word = {"enabled": True, "false": True, "disabled": False, "true": False}.get(m.group(1)) if m else None
    if not res.ok or (m and word is None):
        raise Unknown("could not read whether system/%s starts at boot (exit %d)" % (label, res.rc), "run it again")
    return bool(word)


def step_job(ctx, apply_it):
    """The always-on job: its file, whether it starts at boot, and what launchd runs, as the switch says."""
    if None in (ctx.home, ctx.commit, ctx.on):
        raise Unknown("waits on `home`, `kit` and `settings`", "clear those rows first")
    r, label = ctx.runner, ctx.conf["JOB_LABEL"]
    body, plist = plist_body(ctx.conf, ctx.home, ctx.commit), se._dispatcher_plist(label)
    have = r.read(["cat", plist]).out if r.read(["test", "-e", plist]).ok else None   # root:wheel 644: no sudo
    foreign = qs.foreign_plist(have, RUNNER_SCRIPT) if have not in (None, body) else None
    if foreign:
        raise SetupError("%s is NOT the runner's job: %s. Replacing it would destroy that job; pick a JOB_LABEL "
                         "nothing else uses" % (plist, foreign))
    want = [str(a) for a in plistlib.loads(body.encode("utf-8"))["ProgramArguments"]]

    def state():
        up, args = loaded(ctx, label)
        at_boot = boot(ctx, label)
        kit = re.search(r"# kit ([0-9a-f]{40})$", (args or [""])[-1])
        return up, args, at_boot, "%s; %s at boot; %s" % (
            "its file installed" if r.read(["test", "-e", plist]).ok else "no job file",
            "starts" if at_boot else "does not start",
            "loaded, running kit %s" % (kit.group(1)[:12] if kit else "unknown") if up else "not loaded")
    up, args, at_boot, now = state()
    stale = up and (args != want or have != body)          # launchd holds an older copy: load it again
    todo = (["install its file"] if have != body else []) + (
        (["enable it at boot"] if not at_boot else []) + (["load it"] if not up else ["load it again"] if stale else [])
        if ctx.on else (["disable it at boot"] if at_boot else []) + (["unload it"] if up else []))
    if not todo:
        return True, now
    if not apply_it:
        return False, "would %s; now: %s" % (", ".join(todo), now)
    if have != body:
        se._install_plist(r, label, body)
        if r.read(["cat", plist]).out != body:
            raise SetupError("%s was installed, and reading it back does not match" % plist)
    def must(argv, what):
        res = ctx.as_root(["launchctl"] + argv, what, why=what)
        if not res.ok:
            raise SetupError("could not %s (exit %d): %s" % (what, res.rc, _s((res.err or res.out).strip())[:160]))
    if up and (stale or not ctx.on):
        must(["bootout", "system/" + label], "stop system/" + label)
        if not se._wait_until_gone(r, label, 60)[0]:
            raise SetupError("system/%s was told to stop and launchd still holds it" % label)
    if at_boot != ctx.on:
        must(["enable" if ctx.on else "disable", "system/" + label],
             "%s system/%s at boot" % ("enable" if ctx.on else "disable", label))
    if ctx.on and (not up or stale):
        must(["bootstrap", "system", plist], "load system/" + label)
    up, args, at_boot, now = state()
    if not ((up and args == want and at_boot) if ctx.on else not (up or at_boot)):
        raise SetupError("after the change the job is not as its switch says: %s" % now)
    return False, "did: %s; now: %s" % (", ".join(todo), now)


# The copy comes right before the job: a refresh stops a job that runs from it, and the `job` row loads it again.
STEPS = (("home", step_home), ("kit", step_kit), ("settings", step_settings), ("engine", step_engine),
         ("job", step_job), ("push-token", step_push_token), ("tracker-key", step_tracker_key),
         ("writer-key", step_writer_key))
_SEVERITY = (FAILED, UNKNOWN, WOULD_CHANGE)


def run_steps(ctx, apply_it):
    """[(step, outcome, detail)]. A check reads every row; an apply stops at the first it could not clear."""
    rows = []
    for sid, fn in STEPS:
        try:
            ok, detail = fn(ctx, apply_it)
            outcome = ALREADY_DONE if ok else DONE if apply_it else WOULD_CHANGE
        except Unknown as exc:
            outcome, detail = UNKNOWN, "NOT MEASURED: %s (%s)" % (exc.what, exc.remedy)
        except Exception as exc:                                    # noqa: BLE001  (a surprise is a FAILED row)
            outcome = FAILED
            detail = str(exc) if isinstance(exc, (SetupError, Refusal)) else "unexpected %s: %s" % (
                type(exc).__name__, exc)
        rows.append((sid, outcome, _s(detail)))
        if apply_it and outcome in (FAILED, UNKNOWN):
            break
    return rows


def report(ctx, rows):
    """Say the rows, on stderr, and return the exit code: the worst row, then a part left off (10)."""
    outs = [o for _sid, o, _d in rows]
    code = next(({FAILED: EX_FAILED, UNKNOWN: EX_UNKNOWN, WOULD_CHANGE: EX_BLOCKED}[s] for s in _SEVERITY
                 if s in outs), EX_BLOCKED if ctx.left_off else EX_OK)
    say("\n-- steps --")
    for sid, outcome, detail in rows:
        say("  %-10s %-17s %s" % (sid, outcome, detail))
    if ctx.left_off and code == EX_BLOCKED:
        say("\n%s" % ctx.left_off)
    return code


def read_answers(stream):
    """(answers, None), or (None, why): one JSON object through a PIPE from the calling program, read once, as the
    notifier's installer reads its own. A terminal would make `apply` a question; a file is one a session could
    have written; an argument every process can see. No reason quotes what was read."""
    try:
        mode = os.fstat(stream.fileno()).st_mode
    except (OSError, ValueError, AttributeError):
        return None, "standard input could not be looked at, so no answers were read"
    if not stat.S_ISFIFO(mode):
        return None, ("standard input is not a pipe: `apply` takes its answers from the program that calls it, "
                      "never from a terminal, a file or an argument")
    try:
        raw = stream.read(ANSWERS_MAX_BYTES + 1)
        answers = json.loads(raw) if len(raw) <= ANSWERS_MAX_BYTES else None
    except (OSError, ValueError, UnicodeDecodeError):
        answers = None
    if not (isinstance(answers, dict) and set(answers) <= ANSWERS_KEYS and answers.get("schema") == ANSWERS_SCHEMA
            and isinstance(answers.get("consent"), str)):
        return None, "the answers are not one %s object of `schema` and `consent`" % ANSWERS_SCHEMA
    return answers, None


def parse_end_date(text, today):
    """(the date, "") or (None, why): YYYY-MM-DD, a real day, after today and within PUSH_DATE_YEARS. The
    reason never quotes the text: a person at a hidden prompt may have pasted the token into the wrong one."""
    import datetime
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)\Z", (text or "").strip())
    try:
        when = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None
    except ValueError:
        when = None
    if when is None:
        return None, "that is not a date written YYYY-MM-DD"
    if when <= today:
        return None, "that day is not after today: a token that has ended cannot be put in"
    if (when - today).days > 366 * PUSH_DATE_YEARS:
        return None, "that is more than %d years away: check the year" % PUSH_DATE_YEARS
    return when, ""


def cmd_key(name, args, env, machine=None, reader=None, date_reader=None, tty=None, today=None):
    """The owner's one step for a key file (KIT-907 for the push token, KIT-928 for the other two): a hidden
    prompt for the key, a question for its end date, the role account writing a 0600 file, the date recorded for
    the health watch. Human words on stderr, no JSON document; the key is never in an argument, an output or a
    log. `machine` is (runner, sudo session) in the selftest."""
    import getpass
    import pipeline_root_guard as _rg
    if _rg.refusal():
        say(_rg.refusal())
        return EX_REFUSED
    found = agent_markers(env)       # BEFORE anything is read: a session never handles the owner's credential
    if found:
        say("REFUSED: `%s` takes the owner's credential and writes it as the role account, and this is an "
            "agent environment (%s set). A person runs it, in a terminal." % (name, ", ".join(found)))
        return EX_REFUSED
    if not (tty if tty is not None else sys.stdin.isatty()):
        say("BLOCKED: `%s` asks for the key at a hidden prompt, and this is not a terminal." % name)
        return EX_BLOCKED
    try:
        conf, errors = load_conf(args.conf, ns.invoking_user())
    except SetupError as exc:
        say("FAILED: %s" % exc)
        return EX_USAGE
    if errors:
        say("Your conf has %d problem(s), all of them:\n  - %s" % (len(errors), "\n  - ".join(errors)))
        return EX_USAGE
    if name == "push-token" and conf["PUSH_TOKEN_FILE"] == OFF:
        say("REFUSED: PUSH_TOKEN_FILE is off in your conf. Set it to the file's path first. Nothing was written.")
        return EX_USAGE
    spec, today = key_spec(name), today or _today()
    label, path = spec["label"], spec["path"](conf, {})
    say("The %s: written to %s as %s, mode 0600, and its end date recorded for the health watch."
        % (label, path + (" (or the key file your installed settings name)" if name == "writer-key" and
                          conf.get("WRITER_KEY_UNSET") else ""), conf["ROLE_ACCOUNT"]))
    say(spec["make"])
    value = ((reader or getpass.getpass)(spec["prompt"]) or "").strip()
    if not spec["pattern"].match(value):
        say("REFUSED: that is not %s. Nothing was written." % spec["shape"])
        return EX_USAGE
    when, why = parse_end_date((date_reader or input)("The day it ends, as YYYY-MM-DD (the expiry you chose "
                                                       "when you made it): "), today)
    if when is None:
        say("REFUSED: %s. Nothing was written: a key with no end date on record is one nobody is warned about."
            % why)
        return EX_USAGE
    run, sudo = machine or (Runner(), se.SudoSession())
    try:
        sudo.acquire("`%s` writes the %s as %s." % (name, label, conf["ROLE_ACCOUNT"]), name)
    except se.NoPrivilege as exc:
        say(str(exc).split("\n")[0])
        say("NOT WRITTEN: no administrator access. Run this again.")
        return EX_NOPRIV
    run.dry_run = False
    if name == "writer-key" and conf.get("WRITER_KEY_UNSET"):       # the file the runner will read, as `check` does
        path = spec["path"](conf, {"session_tools": {"key_file": installed_key_file(run, conf)}})
    res = run.as_role(conf["ROLE_ACCOUNT"], py(TOKEN_WRITE_PY, path, spec["pattern"].pattern, spec["shape"]),
                      stdin=value + "\n", why="write the %s into %s" % (label, path), secret_stdin=True)
    for line in (res.out or "").splitlines():
        say("  " + _s(line))
    if res.rc == 3:
        return EX_REFUSED
    state = token_file_state(lambda script: run.as_role(conf["ROLE_ACCOUNT"], script), path) if res.ok else None
    if state != "ok":
        say("FAILED: the %s could not be written into %s as %s, or reading it back does not match (%s). "
            "No end date was recorded." % (label, path, conf["ROLE_ACCOUNT"], _s((res.err or "").strip())[:200] or state))
        return EX_FAILED
    failed = record_end(args.watch_conf, when, spec["clock"])
    if failed:
        say("FAILED: the key is in %s, but its end date is NOT on record: %s. Put `%s:%s` into CREDENTIAL_EXPIRIES "
            "in %s, or run this again." % (path, failed, spec["clock"], when.isoformat(), args.watch_conf))
        return EX_FAILED
    say("DONE: %s holds the key (mode 0600, the role account's own); it ends on %s, which the health watch "
        "has on record, and warns before." % (path, when.isoformat()))
    return EX_OK


def main(argv=None, env=None, stdin=None, machine=None, stand_ins=None):
    """`check` or `apply`: every human word on stderr, and ONE JSON document of rows on stdout, on every exit
    past the arguments, so a program never has to tell prose from rows. `machine` stands in for (runner, sudo
    live, this checkout's scripts, the folder its parts list is read from) in the selftest. `push-token`, `tracker-key`
    and `writer-key` are the owner's own steps: words only, no document; `stand_ins` replaces its prompts and sudo in the selftest."""
    p = argparse.ArgumentParser(prog="pipeline_runner_setup.py", description="Install the runner, every lane off.")
    p.add_argument("command", nargs="?", default="check", choices=["check", "apply"] + list(KEY_COMMANDS))
    p.add_argument("--conf", default=DEFAULT_CONF)
    p.add_argument("--watch-conf", default=os.path.join(KIT, "update.conf"),
                   help="the health watch's conf, where each key's end date is recorded")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)
    if args.selftest:
        from scratch_run import contained   # KIT-450: its temp folders go when it ends
        import pipeline_root_guard as _rg   # KIT-693: the root case ends every installer's own selftest
        return contained(selftest, "runner-setup-selftest-run-") or _rg.root_case_rc(main, ["check"])
    if args.command in KEY_COMMANDS:
        real_out, sys.stdout = sys.stdout, sys.stderr
        try:
            return cmd_key(args.command, args, os.environ if env is None else env, **(stand_ins or {}))
        finally:
            sys.stdout = real_out
    real_out, sys.stdout = sys.stdout, sys.stderr
    box = {"ctx": None, "rows": [], "why": None}
    try:
        code = _main(args, os.environ if env is None else env, sys.stdin if stdin is None else stdin, machine, box)
    except Exception as exc:                                      # noqa: BLE001  (still one document)
        say("BUG: %s raised %s: %s" % (args.command, type(exc).__name__, _s(str(exc))[:200]))
        code, box["why"] = EX_FAILED, "unexpected %s" % type(exc).__name__
    finally:
        sys.stdout = real_out
    ctx = box["ctx"]
    real_out.write(json.dumps({
        "schema": ROWS_SCHEMA, "command": args.command, "exit": code, "why": box["why"],
        "rows": [{"step": s, "outcome": o, "detail": d[:400], "card": None} for s, o, d in box["rows"]],
        "ceiling": ctx and ctx.ceiling, "first_pass": ctx and ctx.first, "left_off": ctx and ctx.left_off},
        sort_keys=True) + "\n")
    real_out.flush()
    return code


def _main(args, env, stdin, machine, box):
    """The exit code. The order is the contract: the root refusal, then (`apply`) the agent refusal and the
    answers, then the conf, so a session is refused before it reads anything and a "no" touches nothing."""
    import pipeline_root_guard as _rg   # KIT-693: the one refusal every installer shares
    if _rg.refusal():
        say(_rg.refusal())
        box["why"] = "refused: run as root"
        return EX_REFUSED
    if args.command == "apply":
        found = agent_markers(env)      # BEFORE anything is read: a session must not install its own supervisor
        if found:
            say("REFUSED: `apply` changes this Mac, and this is an agent environment (%s set). There is no "
                "override: a person runs the one command, which runs this. Read-only meanwhile: `check`."
                % ", ".join(found))
            box["why"] = "refused: an agent environment"
            return EX_REFUSED
        answers, why = read_answers(stdin)
        if answers is None:
            say("FAILED: %s. Nothing was changed." % why)
            box["why"] = why
            return EX_USAGE
        if answers["consent"] != "yes":
            say("NO CONSENT: the answers do not say yes, so nothing was changed.")
            box["why"] = "no consent: the answers do not say yes"
            return EX_BLOCKED
    try:
        conf, errors = load_conf(args.conf, ns.invoking_user())
    except SetupError as exc:
        say("FAILED: %s" % exc)
        box["why"] = str(exc).splitlines()[0][:200]
        return EX_USAGE
    if errors:
        say("Your conf has %d problem(s), all of them:\n  - %s" % (len(errors), "\n  - ".join(errors)))
        box["why"] = "%d problem(s) in the conf, the first: %s" % (len(errors), errors[0])
        return EX_USAGE
    run, live, scripts, root = machine or (Runner(dry_run=args.command == "check"), None, HERE, KIT)
    if live is None:
        live = not agent_markers(env) and se._sudo_refresh() == 0       # `sudo -n -v`: never a prompt
    if args.command == "apply" and not live:
        say("NO LIVE SUDO SESSION: `apply` asks for no password, so nothing was changed. The one command holds "
            "one; or run `sudo -v` in this terminal first.")
        box["why"] = "no live sudo session"
        return EX_NOPRIV
    ctx = box["ctx"] = Ctx(conf, run, env, live, part_ceiling(root), scripts)
    ctx.watch_conf = args.watch_conf
    say("The runner's installer: %s, role account %s" % (
        "CHECK, nothing is changed" if args.command == "check" else "APPLY", ctx.account))
    box["rows"] = run_steps(ctx, args.command == "apply")
    code = report(ctx, box["rows"])
    if args.command == "check" and run.writes:
        say("BUG: a check recorded %d write(s); that is a defect in this file." % len(run.writes))
        return EX_FAILED
    return code


# ── --selftest: offline; launchd and the role account stood in for; a mutant for each rule ─────────────────
BASE_CONF = {"ROLE_ACCOUNT": "_exrunner", "CLAUDE_BIN": "/opt/claude/bin/claude", "OWNER_USER_IDS": "owner-1",
             "DISPATCHER_USER_IDS": "disp-1", "PROJECTS": "app=~/repos/app@main, web=~/repos/web"}
_GIT_ENV = {"PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1"}


def _put(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)


class FakeMachine(Runner):
    """A role home in a folder: Stage E's clone (a real git repository) cloned from a fake checkout of the kit, and
    launchd as data. `sudo -n -u <role> -H /bin/sh -c` runs a REAL /bin/sh with HOME there, and the copy's root
    program runs for real, giving the files to this account, which stands in for root (KIT-919). A sudo without
    `-n` would ask for a password, so it fails here."""
    ROOT_UID = os.getuid()

    def __init__(self, root):
        Runner.__init__(self)
        self.root, self.home, self.checkout = root, root + "/role-home", root + "/checkout"
        self.scripts, self.clone, self.engine = self.checkout + "/scripts", self.home + "/.stage-e/kit", root + "/engine/kit"
        self.plists, self.loaded, self.boot, self.sudo, self.print_rc = {}, {}, {}, [], None
        for name in RUNNER_SCRIPTS:
            _put(self.scripts + "/" + name, "# a stand-in for %s\n" % name)
        for part in ("session-kinds/code.json", ".claude/hooks/" + runner_guard.HOOKS["pre-tool-use"],
                     "templates/sandbox/look.sb"):
            _put(self.checkout + "/" + part, "# a stand-in for %s\n" % part)
        self.git(self.checkout, "init", "-q")
        self.commit("first")

    def git(self, where, *argv):
        import subprocess
        return subprocess.run(["git", "-C", where] + list(argv), check=True, capture_output=True, text=True,
                              env=dict(_GIT_ENV, HOME=self.home)).stdout.strip()

    def commit(self, message, reviewed=True):
        """A commit in the checkout, on its origin/main when `reviewed`, and Stage E's clone moved to it."""
        self.git(self.checkout, "add", "-A")
        self.git(self.checkout, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c",
                 "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", message)
        if reviewed:
            self.git(self.checkout, "update-ref", "refs/remotes/origin/main", "HEAD")
        if os.path.isdir(self.clone + "/.git"):
            self.git(self.clone, "pull", "-q", "--ff-only")
        else:
            os.makedirs(os.path.dirname(self.clone), exist_ok=True)
            self.git(self.root, "clone", "-q", self.checkout, self.clone)
        return self.git(self.checkout, "rev-parse", "HEAD")

    def uid_of(self, account):
        return os.getuid() + 1          # never this account: the copy is checked against another one

    def user_uid(self):
        return os.getuid() + 2

    def _exec(self, argv, stdin, timeout, cwd=None):
        import subprocess
        argv = list(argv)
        if argv[0] != "sudo":
            if argv[0] == "dscl":
                return se.Result(0, "NFSHomeDirectory: %s\n" % self.home)
            if argv[:2] == ["test", "-e"]:
                return se.Result(int(argv[2] not in self.plists))
            if argv[0] == "cat":
                return se.Result(0, self.plists[argv[1]]) if argv[1] in self.plists else se.Result(1)
            return se.Result(0 if argv[0] == "plutil" else 1)
        self.sudo.append(argv)
        if argv[1] != "-n":
            return se.Result(1, "", "sudo: a password is required")
        argv = argv[2:]
        if argv[0] == "/usr/bin/python3":         # the copy's root program, run for real as this account
            if not argv[4].startswith(self.root + "/"):
                return se.Result(99, "", "the root program names a copy outside this machine's folder")
            done = subprocess.run([sys.executable] + argv[1:5] + [str(os.getuid()), str(os.getgid()), argv[7]],
                                  input=stdin, capture_output=True, timeout=timeout)
            return se.Result(done.returncode, done.stdout.decode("utf-8", "replace"),
                             done.stderr.decode("utf-8", "replace"))
        if argv[0] == "-u":
            if [p for p in re.findall(r'"(/[^"]*)"', argv[5]) if not p.startswith(self.root)]:
                return se.Result(99, "", "a role script names a path outside its home")
            done = subprocess.run(["/bin/sh", "-c", argv[5]], input=stdin, capture_output=True, text=True,
                                  timeout=timeout, env=dict(_GIT_ENV, HOME=self.home))
            return se.Result(done.returncode, done.stdout, done.stderr)
        label = argv[-1].replace("system/", "")
        if argv[0] == "install":
            with open(argv[-2], encoding="utf-8") as fh:
                self.plists[argv[-1]] = fh.read()
        elif argv[:2] == ["launchctl", "print"]:
            if self.print_rc is not None:
                return se.Result(self.print_rc, "", "launchctl: no answer")
            if label not in self.loaded:
                return se.Result(113, "", "Could not find service")
            args = plistlib.loads(self.loaded[label].encode("utf-8"))["ProgramArguments"]
            return se.Result(0, "system/%s = {\n\targuments = {\n%s\n\t}\n}\n" % (label, "\n".join("\t\t" + a for a in args)))
        elif argv[:2] == ["launchctl", "print-disabled"]:
            return se.Result(0, "disabled services = {\n%s}\n" % "".join('\t"%s" => %s\n' % kv for kv in self.boot.items()))
        elif argv[1] in ("enable", "disable"):
            self.boot[label] = argv[1] + "d"
        elif argv[1] == "bootstrap":
            label = os.path.basename(argv[-1])[:-len(".plist")]
            if self.boot.get(label) != "enabled" or label in self.loaded:
                return se.Result(119, "", "Bootstrap failed: the service is disabled or loaded")
            self.loaded[label] = self.plists[argv[-1]]
        elif argv[1] == "bootout":
            self.loaded.pop(label, None)
        else:
            return se.Result(1, "", "nothing scripted")
        return se.Result(0)


def selftest():
    import tempfile
    fails, ran = [], [0]

    def check(name, got, want):
        ran[0] += 1
        print("[%s] %s%s" % ("PASS" if got == want else "FAIL", name, "" if got == want else "  (got %r, want %r)"
                             % (got, want)))
        fails.extend([name] * (got != want))
    me, saved, pause = sys.modules[__name__], dict(os.environ), se._pause
    unsafe = me.UNSAFE_PREFIXES
    for marker in AGENT_ENV_MARKERS:                # the battery owns its environment
        os.environ.pop(marker, None)
    se._pause = lambda seconds: None
    me.UNSAFE_PREFIXES = ("/unsafe-selftest/",)     # its copies are made in temp folders; case 14 checks the rule
    try:
        with tempfile.TemporaryDirectory(prefix="runner-setup-selftest-") as tmp:
            _cases(check, tmp, unsafe)
    except Exception as exc:                                      # noqa: BLE001
        check("the battery ran to its end", "%s: %s" % (type(exc).__name__, str(exc)[:300]), "")
    finally:
        se._pause, me.UNSAFE_PREFIXES = pause, unsafe
        os.environ.clear()
        os.environ.update(saved)
    print("\n%d/%d checks passed" % (ran[0] - len(fails), ran[0]))
    return 1 if fails else 0


def _cases(check, tmp, unsafe):  # noqa: C901  (one battery, read top to bottom)
    import contextlib
    import io
    import tempfile
    me, label = sys.modules[__name__], CONF_DEFAULTS["JOB_LABEL"]
    plist = se._dispatcher_plist(label)
    yes = json.dumps({"schema": ANSWERS_SCHEMA, "consent": "yes"})

    def pipe_of(text):
        """Standard input from a program: a real pipe, its writer closed."""
        r, w = os.pipe()
        os.write(w, text.encode("utf-8"))
        os.close(w)
        return os.fdopen(r, encoding="utf-8")

    def text(**over):
        return "".join("%s=%s\n" % kv for kv in dict(BASE_CONF, **over).items() if kv[1] is not None)

    def world(**over):
        root = tempfile.mkdtemp(dir=tmp)
        _put(root + "/runner.conf", text(**dict({"ENGINE_DIR": root + "/engine/kit"}, **over)))
        return FakeMachine(root)

    def go(fake, command="check", answers=yes, env=None, live=True, stdin=None, parts=KIT):
        """(exit, the one document on stdout, its words on stderr, what is left unread on stdin)."""
        out, err, stream = io.StringIO(), io.StringIO(), stdin or pipe_of(answers)
        fake.dry_run, fake.writes = command == "check", []
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main([command, "--conf", fake.root + "/runner.conf", "--watch-conf", fake.root + "/update.conf"],
                        env=env or {}, stdin=stream,
                        machine=(fake, live, fake.scripts, parts))
        unread = stream.read()
        stream.close()
        return code, json.loads(out.getvalue()), err.getvalue(), unread

    def rows(doc):
        return dict((r["step"], r["outcome"]) for r in doc["rows"])

    def said(doc):
        return " | ".join(r["detail"] for r in doc["rows"])

    def settings(fake):
        try:
            with open(fake.home + "/.pipeline-runner/runner.json") as fh:
                doc = json.load(fh)
        except OSError:
            return None
        return doc["enabled"], sorted(doc["projects"]), set(v for p in doc["projects"].values()
                                                             for v in p["lanes"].values())

    def job(fake):
        """(the file says Disabled, starts at boot, loaded, the kit its command names)."""
        body = fake.plists.get(plist)
        kit = re.search(r"# kit ([0-9a-f]{12})", fake.loaded.get(label, ""))
        return (bool(body) and plistlib.loads(body.encode("utf-8")).get("Disabled") is True,
                fake.boot.get(label) == "enabled", label in fake.loaded, kit.group(1) if kit else None)

    def head(fake):
        import subprocess
        return subprocess.run(["git", "-C", fake.home + "/.stage-e/kit", "rev-parse", "HEAD"], capture_output=True,
                              text=True, env=dict(_GIT_ENV, HOME=fake.home)).stdout.strip()[:12]

    def store_with(fake, requests=0, running=0):
        for n in range(requests):
            _put("%s/.pipeline-runner/store/requests/rq-%032x.json" % (fake.home, n), "{}\n")
        for n in range(running):
            _put("%s/.pipeline-runner/store/runs/run-%032x/run.json" % (fake.home, n),
                 json.dumps({"state": "running"}, indent=1) + "\n")

    def case(name, thunk, want, *mutants):
        """Run the case, then each mutant against it: a check that cannot fail checks nothing."""
        got = thunk()
        check(name, got, want)
        for mutant, patches in mutants:
            undo = [(obj, attr, getattr(obj, attr)) for obj, attr, _v in patches]
            for obj, attr, value in patches:
                setattr(obj, attr, value)
            try:
                other = thunk()
            except Exception as exc:                              # noqa: BLE001
                other = ("raised", type(exc).__name__)
            finally:
                for obj, attr, value in reversed(undo):
                    setattr(obj, attr, value)
            check("mutant: %s is caught" % mutant, other != got, True)

    # 1. The conf: every fault at once, by key, never by value.
    secret = "lin_" + "api_" + "RUNNERSELFTESTNOTREAL0123456789abcdef"     # built at runtime: no fixture looks like one
    bad = text(ROLE_ACCOUNT="root", OWNER_USER_IDS="disp-1", PROJECTS="App=/x@main, app=../x, web=/w@a..b",
               JOB_LABEL="nodots", TOKEN_ENV=secret, CLAUDE_BIN="claude", RUNNER_DIR="~/.stage-e/kit/r") + "NOPE=1\nno\n"

    def faults():
        values, errors = parse_conf(bad)
        errors = errors + validate_conf(values, "selftest-person")[1]
        return [any(f in e for e in errors) for f in (
            "ROLE_ACCOUNT must", "lists the dispatcher's own user", "PROJECTS item 1, 2, 3", "JOB_LABEL is not",
            "TOKEN_ENV carries a CREDENTIAL SHAPE", "CLAUDE_BIN must", "RUNNER_DIR is inside ROLE_KIT_CLONE",
            "unknown key NOPE", "not KEY=value")] + [secret in " ".join(errors)]
    case("conf: every fault is named in one pass, by key, and a credential is never shown", faults, [True] * 9 + [False],
         ("the projects rule dropped", [(me, "projects", lambda t: ({}, []))]))

    # 2. A check changes nothing and asks nothing; a fresh machine has work. Its rows are one document on stdout.
    def fresh_check():
        fake = world()
        code, doc, err, _left = go(fake)
        return code, doc["exit"], rows(doc), "-- steps --" in err, fake.writes, settings(fake), [
            a for a in fake.sudo if a[2] not in ("-u", "launchctl") or a[3] not in ("print", "print-disabled", "_exrunner")]
    case("check on a fresh machine: exit 10 in one document, settings and job would change, nothing written",
         fresh_check, (10, 10, {"home": ALREADY_DONE, "kit": ALREADY_DONE, "engine": WOULD_CHANGE,
                                "settings": WOULD_CHANGE, "job": WOULD_CHANGE, "push-token": ALREADY_DONE,
                                "tracker-key": ALREADY_DONE, "writer-key": ALREADY_DONE}, True, [], None, []),
         ("a check that applies", [(me, "run_steps", lambda ctx, apply_it, real=run_steps: real(ctx, True))]))

    # 3. Apply on a fresh machine: within the ceiling, so on; every lane off; the job enabled and loaded.
    def fresh_apply():
        fake = world()
        code = go(fake, "apply")[0]
        again, look, _err, _left = go(fake)
        return code, settings(fake), job(fake)[:3], job(fake)[3] == head(fake), again, set(rows(look).values()), \
            "app: every lane off; web: every lane off" in said(look), all(a[1] == "-n" for a in fake.sudo)
    case("apply: settings with every lane off for every project, on within the ceiling, the job loaded; a check "
         "then finds it current, and every sudo call was `sudo -n`", fresh_apply,
         (0, (True, ["app", "web"], {False}), (True, True, True), True, 0, {ALREADY_DONE}, True, True),
         ("a lane written on", [(me, "compose", lambda c, on, was=None, real=compose: dict(real(c, on, was), projects=dict(
             (k, dict(p, lanes=dict(p["lanes"], review=True))) for k, p in real(c, on, was)["projects"].items())))]),
         ("never enabled at boot", [(me, "boot", lambda ctx, lab: True)]),
         ("a sudo that may ask", [(Runner, "as_root", lambda self, argv, why=None, stdin=None, timeout=300:
                                   self.read(["sudo"] + list(argv), stdin, timeout))]))

    # 4. Above the ceiling: installed and left off, its file never started at boot, and one line says why.
    def above():
        fake = world()
        store_with(fake, requests=2, running=1)
        fake.boot[label] = "enabled"                 # an earlier install left it enabled
        code, doc, _err, _left = go(fake, "apply")
        return code, settings(fake), job(fake), doc["left_off"], doc["first_pass"], doc["ceiling"]
    case("above the ceiling: left off, every lane off, not started at boot, and `left_off` says why", above,
         (10, (False, ["app", "web"], {False}), (True, False, False, None),
          "left off: its first pass would act on 2 waiting request(s) and 1 unfinished run(s), above its ceiling of 0",
          3, 0),
         ("the count ignored", [(me, "first_pass", lambda ctx: (0, 0))]),
         ("the ceiling ignored", [(me, "part_ceiling", lambda root=KIT: 99)]),
         ("never disabled at boot", [(me, "boot", lambda ctx, lab: False)]))

    def no_ceiling():
        fake = world()
        code, doc, _err, _left = go(fake, "apply", parts=tmp)          # no parts list there
        return code, doc["ceiling"], doc["left_off"], job(fake)[1:3]
    case("a ceiling it cannot read leaves it off", no_ceiling,
         (10, None, "left off: its first-pass ceiling could not be read", (False, False)),
         ("an unread ceiling taken as 0", [(me, "part_ceiling", lambda root=KIT: 0)]))

    # 5. A part already on stays on, whatever waits in its store.
    def stays_on():
        fake = world()
        go(fake, "apply")
        store_with(fake, requests=3)
        return go(fake, "apply")[0], settings(fake)[0], job(fake)[2]
    case("on before, so it stays on", stays_on, (0, True, True),
         ("the switch read afresh", [(qs, "read_role_file", lambda ctx, path: (None, ""))]))

    # 5b. The owner's own: a lane he switched on, his approved states and limits survive an apply, and an
    #     explicit off is never switched back on. A file that is not JSON is never written over.
    def hand_edit(fake, **changes):
        path = fake.home + "/.pipeline-runner/runner.json"
        with open(path) as fh:
            doc = json.load(fh)
        for key, value in changes.items():
            doc[key] = value
        doc["projects"]["app"]["lanes"]["review"] = changes.get("enabled", True) is not False
        doc["projects"]["app"]["approval_states"] = {"review": ["st-1"]}
        _put(path, json.dumps(doc, indent=2, sort_keys=True) + "\n")
        return path

    def kept():
        fake = world()
        go(fake, "apply")
        path = hand_edit(fake, limits={"wall_minutes": 120})
        code = go(fake, "apply")[0]
        with open(path) as fh:
            after = json.load(fh)
        app = after["projects"]["app"]
        return code, app["lanes"].get("review"), app.get("approval_states"), after.get("limits"), \
            set(after["projects"]["web"]["lanes"].values())
    case("a lane the owner switched on, his approved states and his limits survive an apply", kept,
         (0, True, {"review": ["st-1"]}, {"wall_minutes": 120}, {False}),
         ("the installed settings ignored", [(me, "compose", lambda c, on, was=None, real=compose: real(c, on))]))

    def paused():
        fake = world()
        go(fake, "apply")
        hand_edit(fake, enabled=False)
        code, doc, _err, _left = go(fake, "apply")
        return code, settings(fake)[0], job(fake)[1:3], (doc["left_off"] or "").startswith(
            "left off: its settings say enabled is false")
    case("an explicit enabled false is never switched back on, and its job is left off", paused,
         (10, False, (False, False), True),
         ("an installed off taken as a first install", [(me, "decide", lambda ctx, was, path, real=decide:
                                                         real(ctx, None, path))]))

    def not_json():
        fake = world()
        _put(fake.home + "/.pipeline-runner/runner.json", "my own notes\n")
        code, doc, _err, _left = go(fake, "apply")
        with open(fake.home + "/.pipeline-runner/runner.json") as fh:
            return code, rows(doc).get("settings"), fh.read()
    case("a settings file that is not JSON is never written over", not_json, (1, FAILED, "my own notes\n"),
         ("an unreadable file taken as none", [(me, "installed", lambda ctx, path:
                                                (lambda h, m: (h, m, None))(*qs.read_role_file(ctx, path)))]))

    # 6. The answers: one JSON object through a pipe, of `schema` and `consent`, and a yes.
    def answers():
        got = []
        for given in (json.dumps({"schema": ANSWERS_SCHEMA, "consent": "no"}), "yes", json.dumps({"consent": "yes"}),
                      json.dumps({"schema": ANSWERS_SCHEMA, "consent": "yes", "more": 1}), ""):
            fake = world()
            got.append((go(fake, "apply", given)[0], fake.sudo, settings(fake)))
        fake = world()
        _put(fake.root + "/answers.json", yes)
        with open(fake.root + "/answers.json", encoding="utf-8") as fh:      # a file a session could write
            got.append((go(fake, "apply", stdin=fh)[0], fake.sudo, settings(fake)))
        return got
    case("answers: a no exits 10; not JSON, no schema, an extra key, none or a file exit 2; nothing run or written",
         answers, [(10, [], None)] + [(2, [], None)] * 5,
         ("any answer taken as a yes", [(me, "read_answers", lambda stream: (json.loads(yes), None))]))

    # 7. Under a model: apply refused before it reads anything; check runs no sudo and says NOT MEASURED.
    def agent():
        fake = world()
        code, doc, _err, left = go(fake, "apply", env={AGENT_ENV_MARKERS[0]: ""})
        look_code, look, _err, _left = go(fake, env={AGENT_ENV_MARKERS[0]: "1"})
        return code, doc["why"], left == yes, look_code, rows(look)["kit"], fake.sudo, settings(fake)
    case("agent: apply refused before reading its answers; check runs no sudo and measures nothing as current",
         agent, (3, "refused: an agent environment", True, 4, UNKNOWN, [], None),
         ("no agent check", [(me, "agent_markers", lambda env: [])]))

    # 8. No live sudo session: check says NOT MEASURED, apply changes nothing and exits 5.
    def no_sudo():
        fake = world()
        code, doc, _err, _left = go(fake, live=False)
        return code, rows(doc)["settings"], go(fake, "apply", live=False)[0], fake.sudo, settings(fake)
    case("no live sudo: check is not measured, never current; apply exits 5 having changed nothing", no_sudo,
         (4, UNKNOWN, 5, [], None), ("sudo used without a live session", [(Ctx, "_may", lambda self, what: None)]))

    # 9. What could not be measured is never current.
    def unmeasured():
        fake = world()
        go(fake, "apply")
        fake.print_rc = 1
        code, doc, _err, _left = go(fake)
        return code, rows(doc)["job"]
    case("launchd that does not answer: the job is not measured, never current", unmeasured, (4, UNKNOWN),
         ("an unanswered print read as not loaded", [(me, "loaded", lambda ctx, lab: (False, None))]))

    # 10. The kit it runs: the clone's commit is in the job, and a moved clone loads it again.
    def moved():
        fake = world()
        go(fake, "apply")
        fake.commit("second")
        code, doc, _err, _left = go(fake)
        go(fake, "apply")
        return code, rows(doc)["job"], "kit %s" % head(fake) in said(doc), job(fake)[3] == head(fake)
    case("a moved clone: the check names its commit and would load the job again; apply does", moved,
         (10, WOULD_CHANGE, True, True), ("no commit in the job", [(me, "job_command", lambda c, commit, real=job_command:
                                                                    real(c, "0" * 40))]))

    # 11. A clone that is not level with this checkout fails, and nothing is written.
    def behind():
        fake = world()
        _put(fake.home + "/.stage-e/kit/scripts/" + RUNNER_SCRIPT, "# changed\n")
        code, doc, _err, _left = go(fake, "apply")
        return code, rows(doc).get("kit"), settings(fake)
    case("a clone behind this checkout: apply fails at `kit` and writes nothing", behind, (1, FAILED, None),
         ("no script compared", [(me, "RUNNER_SCRIPTS", ())]))

    # 12. A plist at the label that runs something else is never replaced.
    def foreign():
        fake = world()
        theirs = push.PLIST.format(label=label, account="x", home="/x", path="/x", command="exec other_job.py", log="/x")
        fake.plists[plist] = theirs
        code, doc, _err, _left = go(fake, "apply")
        return code, rows(doc).get("job"), fake.plists[plist] == theirs
    case("a plist at the label that runs another job: FAILED, and never replaced", foreign, (1, FAILED, True),
         ("the foreign check dropped", [(qs, "foreign_plist", lambda body, script: None)]))

    # 12b. The push token (KIT-907): the setting, the owner's one step, the end date, and the row that says
    #      a file with no date is not measured. The token is a stand-in built at runtime; no fixture holds one.
    import datetime
    today = _today()
    TOKEN = "ghp_" + "SelfTestStandInNotReal0123456789ab"
    ON = lambda days: (today + datetime.timedelta(days=days)).isoformat()      # noqa: E731
    pt_path = lambda fake: fake.home + "/.pipeline-runner/push-token"           # noqa: E731

    def watch_conf(fake, expiries=None, exists=True):
        """The health watch's conf: the example with its three example values edited, and the dates on record."""
        if not exists:
            return
        with open(os.path.join(KIT, "update.conf.example"), encoding="utf-8") as fh:
            body = fh.read()
        for k, v in (("DISPATCHER_SERVICE", "com.example.mine"), ("ROLE_ACCOUNT", "_exmine"),
                     ("DISPATCHER_ENV_FILE", "/opt/mine/.env")):
            body = re.sub(r"(?m)^%s=.*$" % k, "%s=%s" % (k, v), body)
        body = re.sub(r"(?m)^CREDENTIAL_EXPIRIES=.*$", "CREDENTIAL_EXPIRIES=%s" % (expiries or ""), body)
        _put(fake.root + "/update.conf", body)

    def watch_text(fake):
        try:
            with open(fake.root + "/update.conf", encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return None

    class FakeSudo(object):
        def __init__(self, ok=True):
            self.ok, self.asked = ok, 0

        def acquire(self, why, resume):
            self.asked += 1
            if not self.ok:
                raise se.NoPrivilege("no administrator access")

    def step(fake, token=TOKEN, date=None, tty=True, env=None, sudo=None, put_watch=True, expiries=None,
             cmd="push-token"):
        """(exit, its words, prompts asked, sudo asked) for one key step's run at a pretend terminal."""
        if put_watch:
            watch_conf(fake, expiries)
        asked, err, out, sudo = [], io.StringIO(), io.StringIO(), sudo or FakeSudo()
        fake.dry_run, fake.writes = False, []
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main([cmd, "--conf", fake.root + "/runner.conf", "--watch-conf", fake.root + "/update.conf"],
                        env=env or {}, stand_ins=dict(
                            machine=(fake, sudo), tty=tty, today=today,
                            reader=lambda prompt: asked.append("token") or token,
                            date_reader=lambda prompt: asked.append("date") or (date or ON(300))))
        return code, err.getvalue() + out.getvalue(), asked, sudo.asked

    def nothing_leaked(fake, words):
        return TOKEN not in words and TOKEN not in json.dumps(fake.writes) and TOKEN not in json.dumps(fake.sudo)

    def happy():
        fake = world()
        code, words, asked, sudo_asked = step(fake, date=ON(300))
        mode = oct(os.stat(pt_path(fake)).st_mode & 0o777) if os.path.exists(pt_path(fake)) else None
        body = open(pt_path(fake)).read() if os.path.exists(pt_path(fake)) else None
        fresh = world()
        watch_conf(fresh)
        others = lambda t: re.sub(r"(?m)^CREDENTIAL_EXPIRIES=.*\n", "", t)      # noqa: E731
        return (code, mode, body, "%s:%s" % (PUSH_CLOCK, ON(300)) in (watch_text(fake) or ""),
                others(watch_text(fake)) == others(watch_text(fresh)), asked, sudo_asked, nothing_leaked(fake, words),
                all(a[1] == "-n" for a in fake.sudo))
    case("push-token: a 0600 file with the token alone, the end date on record, every other conf line as it was, "
         "prompts asked once each, and the token in no argument, output or log", happy,
         (0, "0o600", TOKEN + "\n", True, True, ["token", "date"], 1, True, True),
         ("a file others can read", [(me, "TOKEN_WRITE_PY", TOKEN_WRITE_PY.replace(
             "O_EXCL | os.O_NOFOLLOW, 0o600)", "O_EXCL | os.O_NOFOLLOW, 0o644)").replace(
             "os.chmod(path, 0o600)", "pass"))]),
         ("the date not recorded", [(me, "record_end", lambda watch_conf_path, when, clock=None: None)]),
         ("the token printed", [(me, "TOKEN_WRITE_PY", TOKEN_WRITE_PY.replace(
             '"wrote %s (%d characters, mode 600)" % (path, len(token))', '"wrote %s (%s)" % (path, token)')),
                          (me, "_s", lambda text: text)]))

    def bad_answers():
        got = []
        for token, date in (("not-a-token", ON(300)), (TOKEN, ""), (TOKEN, "soon"), (TOKEN, "2026-02-30"),
                            (TOKEN, ON(-1)), (TOKEN, ON(0)), (TOKEN, ON(366 * PUSH_DATE_YEARS + 30))):
            fake = world()
            code, words, asked, sudo_asked = step(fake, token=token, date=date or " ")
            got.append((code, sudo_asked, os.path.exists(pt_path(fake)), "runner-push-token" in (watch_text(fake) or ""),
                        "not-a-token" not in words, fake.sudo))
        return got
    case("push-token: a token of no shape, or an end date that is missing, not a day, past, today or years off, "
         "is refused before sudo is asked for: nothing written, no date recorded", bad_answers,
         [(2, 0, False, False, True, [])] * 7,
         ("any token taken", [(runner_job, "PUSH_TOKEN_RE", re.compile(".*"))]),
         ("any date taken", [(me, "parse_end_date", lambda text, today: (today + datetime.timedelta(days=9), ""))]))

    def a_model():
        fake = world()
        code, words, asked, sudo_asked = step(fake, env={AGENT_ENV_MARKERS[0]: "1"})
        return code, asked, sudo_asked, os.path.exists(pt_path(fake)), PUSH_CLOCK in watch_text(fake), fake.sudo
    case("push-token under a model: refused before it prompts or asks sudo; nothing written, no date recorded",
         a_model, (3, [], 0, False, False, []), ("no agent check", [(me, "agent_markers", lambda env: [])]))

    def no_terminal():
        fake = world()
        code, words, asked, sudo_asked = step(fake, tty=False)
        return code, asked, sudo_asked, os.path.exists(pt_path(fake))
    case("push-token with no terminal: blocked before any prompt, as a pipe could hold the token in a file",
         no_terminal, (10, [], 0, False),
         ("a terminal never asked for", [(me, "cmd_key", lambda name, args, env, real=cmd_key, **kw:
                                          real(name, args, env, **dict(kw, tty=True)))]))

    def no_admin():
        fake = world()
        code, words, asked, sudo_asked = step(fake, sudo=FakeSudo(ok=False))
        return code, os.path.exists(pt_path(fake)), "runner-push-token" in (watch_text(fake) or ""), fake.sudo
    case("push-token without administrator access: exit 5, no file, no date", no_admin, (5, False, False, []),
         ("sudo not asked for", [(FakeSudo, "acquire", lambda self, why, resume: None)]))

    def no_date_home():
        fake = world()
        code, words, asked, sudo_asked = step(fake, put_watch=False)
        return code, os.path.exists(pt_path(fake)), "%s:%s" % (PUSH_CLOCK, ON(300)) in words, "NOT on record" in words
    case("push-token when the date cannot be recorded: FAILED, the file is said to be written, the exact entry "
         "is given to put in by hand", no_date_home, (1, True, True, True),
         ("a record that does not check", [(me, "record_end", lambda watch_conf_path, when, clock=None: None)]))

    def over_old():
        fake = world()
        step(fake, date=ON(100), expiries="gh-tok:%s,%s:%s" % (ON(200), PUSH_CLOCK, ON(5)))
        return re.search(r"(?m)^CREDENTIAL_EXPIRIES=(.*)$", watch_text(fake)).group(1)
    case("push-token again: its old date is replaced in place and a neighbour's entry is kept", over_old,
         "gh-tok:%s,%s:%s" % (ON(200), PUSH_CLOCK, ON(100)),
         ("an entry lost", [(watch, "merged_expiries", lambda existing, new: (",".join("%s:%s" % (n, d) for n, d in new), new))]))

    def refused_target():
        got = []
        for over in ({"PUSH_TOKEN_FILE": "~/.pipeline-runner/tracker-key"}, {"PUSH_TOKEN_FILE": "~/.pipeline-runner/work/t"},
                     {"PUSH_TOKEN_FILE": "~/.pipeline-runner/store/t"}, {"PUSH_TOKEN_FILE": "~/.stage-e/kit/t"},
                     {"PUSH_TOKEN_FILE": "~/.stage-e/env"}, {"PUSH_TOKEN_FILE": "rel/t"}):
            errors = validate_conf(parse_conf(text(**over))[0], "selftest-person")[1]
            got.append(any(e.startswith("PUSH_TOKEN_FILE ") or "PUSH_TOKEN_FILE must" in e for e in errors))
        return got + [validate_conf(parse_conf(text())[0], "p")[0]["PUSH_TOKEN_FILE"],
                      validate_conf(parse_conf(text(RUNNER_DIR="~/r"))[0], "p")[0]["PUSH_TOKEN_FILE"]]
    case("conf: a push token file that is the tracker key's, in a folder a session writes, in the kit clone, the "
         "env file or not absolute is refused by key; unset it is beside the tracker key", refused_target,
         [True] * 6 + ["~/.pipeline-runner/push-token", "~/r/push-token"],
         ("the path rules dropped", [(me, "path_problems", lambda c, home, skip=(): [])]))

    def composed():
        fake = world()
        go(fake, "apply")
        with open(fake.home + "/.pipeline-runner/runner.json") as fh:
            return json.load(fh).get("push_token_file")
    case("apply: the settings carry the push token's file from the conf", composed, "~/.pipeline-runner/push-token",
         ("the setting not written", [(me, "compose", lambda c, on, was=None, real=compose: dict(
             (k, v) for k, v in real(c, on, was).items() if k != "push_token_file"))]))

    def rows_for(fake_state, expiries=None, put_watch=True, **kw):
        fake = world()
        go(fake, "apply")
        if fake_state != "absent":
            _put(pt_path(fake), TOKEN + "\n")
            os.chmod(pt_path(fake), fake_state if isinstance(fake_state, int) else 0o600)
        if fake_state == "link":
            os.remove(pt_path(fake))
            os.symlink(fake.root + "/elsewhere", pt_path(fake))
        if put_watch:
            watch_conf(fake, expiries)
        code, doc, _err, _left = go(fake)
        row = [r for r in doc["rows"] if r["step"] == "push-token"][0]
        return code, row["outcome"], row["detail"], fake.writes

    def check_rows():
        absent = rows_for("absent")
        nodate = rows_for(0o600)
        dated = rows_for(0o600, expiries="%s:%s" % (PUSH_CLOCK, ON(200)))
        ended = rows_for(0o600, expiries="%s:%s" % (PUSH_CLOCK, ON(-3)))
        loose = rows_for(0o644, expiries="%s:%s" % (PUSH_CLOCK, ON(200)))
        link = rows_for("link", expiries="%s:%s" % (PUSH_CLOCK, ON(200)))
        nowatch = rows_for(0o600, put_watch=False)
        return ((absent[1], "no push token file" in absent[2]),
                (nodate[0], nodate[1], "no end date on record" in nodate[2], "push-token" in nodate[2]),
                (dated[0], dated[1], "ends on %s (in 200 days)" % ON(200) in dated[2]),
                (ended[1], "ended on" in ended[2]), (loose[1], "mode 0600" in loose[2]), (link[1], "is a link" in link[2]),
                (nowatch[1], "could not be read" in nowatch[2]), [x[3] for x in (absent, nodate, dated, ended)])
    case("check: a push token file with no end date on record, or no conf to read it from, is NOT MEASURED, never "
         "current; a dated one says its day; ended, loose or a link FAILS; no file is not a fault; nothing written",
         check_rows,
         ((ALREADY_DONE, True), (4, UNKNOWN, True, True), (0, ALREADY_DONE, True), (FAILED, True), (FAILED, True),
          (FAILED, True), (UNKNOWN, True), [[]] * 4),
         ("no date taken as fine", [(me, "recorded_end", lambda wc, clock=None: (today + datetime.timedelta(days=200), "", 30))]),
         ("an ended date taken as fine", [(me, "recorded_end", lambda wc, clock=None: (today + datetime.timedelta(days=1), "", 30))]),
         ("a loose file taken as fine", [(me, "token_file_state", lambda call, path: "ok")]))

    def edit_settings(fake, fn):
        path = fake.home + "/.pipeline-runner/runner.json"
        with open(path) as fh:
            doc = json.load(fh)
        fn(doc)
        _put(path, json.dumps(doc, indent=2, sort_keys=True) + "\n")
        os.chmod(path, 0o600)

    def lane_on_no_file():
        fake = world()
        go(fake, "apply")
        watch_conf(fake)
        edit_settings(fake, lambda d: d["projects"]["app"]["lanes"].update(code=True))
        code, doc, _err, _left = go(fake)
        row = [r for r in doc["rows"] if r["step"] == "push-token"][0]
        edit_settings(fake, lambda d: d["projects"]["app"]["lanes"].update(code=False, review=True))
        quiet = [r for r in go(fake)[1]["rows"] if r["step"] == "push-token"][0]["outcome"]
        return code, row["outcome"], "app has a code lane on" in row["detail"], quiet
    case("check: a code, fix or conflict lane on with no push token file is FAILED; a lane that holds no token is not",
         lane_on_no_file, (1, FAILED, True, ALREADY_DONE),
         ("lanes not looked at", [(me, "pushing_lanes", lambda was, kinds=None: [])]))

    def custom_path():
        fake = world()
        go(fake, "apply")
        edit_settings(fake, lambda d: d.update(push_token_file="~/.config/pt"))
        go(fake, "apply")
        with open(fake.home + "/.pipeline-runner/runner.json") as fh:
            kept = json.load(fh)["push_token_file"]
        _put(fake.home + "/.config/pt", TOKEN + "\n")
        os.chmod(fake.home + "/.config/pt", 0o600)
        watch_conf(fake, "%s:%s" % (PUSH_CLOCK, ON(200)))
        row = [r for r in go(fake)[1]["rows"] if r["step"] == "push-token"][0]
        fresh = world(PUSH_TOKEN_FILE="~/.config/other")
        go(fresh, "apply")
        with open(fresh.home + "/.pipeline-runner/runner.json") as fh:
            return kept, row["outcome"], "~/.config/pt is present" in row["detail"], json.load(fh)["push_token_file"]
    case("apply: an unset PUSH_TOKEN_FILE keeps a path the owner installed, and `check` measures that path; a "
         "conf that sets one decides", custom_path, ("~/.config/pt", ALREADY_DONE, True, "~/.config/other"),
         ("the installed path overwritten", [(me, "push_path", lambda c, was: c["PUSH_TOKEN_FILE"])]))

    def someone_elses():
        real, out = os.lstat, io.StringIO()
        target = tmp + "/some-token"
        _put(target, "x\n")
        os.chmod(target, 0o600)
        os.lstat = lambda p: (lambda r: os.stat_result((r.st_mode, r.st_ino, r.st_dev, r.st_nlink, r.st_uid + 1,
                                                        r.st_gid, r.st_size, 0, 0, 0)))(real(p))
        saved = sys.argv
        sys.argv = ["x", target]
        try:
            with contextlib.redirect_stdout(out):
                exec(TOKEN_STATE_PY, {"__name__": "state"})      # noqa: S102  (the role program, run in place)
        finally:
            os.lstat, sys.argv = real, saved
        return out.getvalue().strip()
    case("the push token file's kind: a 0600 file another account owns is `other-owner`, not unread", someone_elses,
         "other-owner",
         ("the owner never compared", [(me, "TOKEN_STATE_PY", TOKEN_STATE_PY.replace("elif st.st_uid != os.getuid():",
                                                                                      "elif False:"))]))

    def health_watch_warns():
        fake = world()
        step(fake, date=ON(300))
        conf = pu.load_conf(fake.root + "/update.conf")

        class At(object):
            def __init__(self, days):
                self.now = lambda: datetime.datetime.combine(
                    today + datetime.timedelta(days=days), datetime.time(12), datetime.timezone.utc).timestamp()
        far, near = watch.check_credentials(At(0), conf), watch.check_credentials(At(280), conf)
        return far["state"], near["state"], "runner-push-token expires on %s" % ON(300) in near["summary"]
    case("the health watch reads the recorded date: quiet far off, a warning inside its warn days that names it",
         health_watch_warns, (watch.OK, watch.FIRING, True),
         ("the date filed under another name", [(me, "PUSH_CLOCK", "other-name")]))

    def parts_record_it():
        import pipeline_parts
        doc = pipeline_parts.load(KIT)[0]
        mine = [s for p in pipeline_parts.parts_of(doc) if p["id"] == "runner" for s in p["secrets"]
                if s.get("clock") == PUSH_CLOCK]
        kit = tempfile.mkdtemp(dir=tmp)
        for name, source in (("pipeline-parts.json", "pipeline-parts.json"), ("runner.conf", "runner.conf.example")):
            with open(os.path.join(KIT, source), encoding="utf-8") as fh:
                _put(kit + "/" + name, fh.read())
        keys = [k for k in watch.listed_keys(kit) if k["clock"] == PUSH_CLOCK]
        no_date, code = watch.date_rows(keys, [], today, 30)
        return (len(mine), mine and mine[0]["ends"], len(keys), code, no_date[0][2], no_date[0][3])
    case("the parts list records the push token with its end date, and `dates` shows it missing and fails until "
         "one is on record", parts_record_it, (1, "set-when-made", 1, 10, None, None),
         ("another clock name", [(me, "PUSH_CLOCK", "other-name")]))

    # 12c. The tracker key and the session writer's key (KIT-928): the same step, the same file and the same
    #      end date as the push token, one rule at a time. The keys are stand-ins built at runtime.
    KEY = "lin_" + "api_" + "SelfTestStandInKey0123456789"
    NAMES = (("tracker-key", "runner-tracker-key", "tracker-key"), ("writer-key", "runner-writer-key", "writer-key"))
    kpath = lambda fake, leaf: fake.home + "/.pipeline-runner/" + leaf          # noqa: E731

    def leaked(fake, words):
        return KEY in words or KEY in json.dumps(fake.writes) or KEY in json.dumps(fake.sudo)

    def key_happy(cmd, clock, leaf):
        def run():
            fake = world()
            code, words, asked, sudo_asked = step(fake, token=KEY, date=ON(300), cmd=cmd)
            there = os.path.exists(kpath(fake, leaf))
            mode = oct(os.stat(kpath(fake, leaf)).st_mode & 0o777) if there else None
            body = open(kpath(fake, leaf)).read() if there else None
            return (code, mode, body, "%s:%s" % (clock, ON(300)) in (watch_text(fake) or ""), asked, sudo_asked,
                    leaked(fake, words), all(a[1] == "-n" for a in fake.sudo), os.path.exists(pt_path(fake)))
        return run

    def key_bad(cmd, leaf):
        def run():
            got = []
            for key, date in (("short", ON(300)), ("has a space in it 0123", ON(300)), (KEY, ""), (KEY, ON(-1)),
                              (KEY, ON(0)), (KEY, ON(366 * PUSH_DATE_YEARS + 30))):
                fake = world()
                code, words, asked, sudo_asked = step(fake, token=key, date=date or " ", cmd=cmd)
                got.append((code, sudo_asked, os.path.exists(kpath(fake, leaf)), fake.sudo))
            return got
        return run

    def key_guards(cmd, leaf):
        def run():
            got = []
            for kw in ({"env": {AGENT_ENV_MARKERS[0]: "1"}}, {"tty": False}, {"sudo": FakeSudo(ok=False)}):
                fake = world()
                code, words, asked, sudo_asked = step(fake, token=KEY, cmd=cmd, **kw)
                got.append((code, asked, os.path.exists(kpath(fake, leaf)), "runner-" in (watch_text(fake) or "")))
            return got
        return run
    for cmd, clock, leaf in NAMES:
        case("%s: a 0600 file with the key alone, the end date on record under its own name, the push token's file "
             "untouched, each prompt asked once, and the key in no argument, output or log" % cmd,
             key_happy(cmd, clock, leaf), (0, "0o600", KEY + "\n", True, ["token", "date"], 1, False, True, False),
             ("a file others can read", [(me, "TOKEN_WRITE_PY", TOKEN_WRITE_PY.replace(
                 "O_EXCL | os.O_NOFOLLOW, 0o600)", "O_EXCL | os.O_NOFOLLOW, 0o644)").replace(
                 "os.chmod(path, 0o600)", "pass"))]),
             ("the date not recorded", [(me, "record_end", lambda watch_conf_path, when, clock=None: None)]),
             ("the date filed under the push token's name", [(me, "TRACKER_CLOCK", PUSH_CLOCK),
                                                              (me, "WRITER_CLOCK", PUSH_CLOCK)]),
             ("the key printed", [(me, "TOKEN_WRITE_PY", TOKEN_WRITE_PY.replace(
                 '"wrote %s (%d characters, mode 600)" % (path, len(token))', '"wrote %s (%s)" % (path, token)')),
                                  (me, "_s", lambda text: text)]))
        case("%s: a key of no shape, or an end date that is missing, not a day, past, today or years off, is refused "
             "before sudo is asked for: nothing written" % cmd, key_bad(cmd, leaf), [(2, 0, False, [])] * 6,
             ("any key taken", [(tl_tools, "KEY_RE", re.compile(".*"))]),
             ("any date taken", [(me, "parse_end_date", lambda text, today: (today + datetime.timedelta(days=9), ""))]))
        case("%s: under a model it is refused, with no terminal it is blocked, without administrator access it is "
             "exit 5: each before any write and any date" % cmd, key_guards(cmd, leaf),
             [(3, [], False, False), (10, [], False, False), (5, ["token", "date"], False, False)],
             ("no agent check", [(me, "agent_markers", lambda env: [])]),
             ("a terminal never asked for", [(me, "cmd_key", lambda name, args, env, real=cmd_key, **kw:
                                              real(name, args, env, **dict(kw, tty=True)))]),
             ("sudo not asked for", [(FakeSudo, "acquire", lambda self, why, resume: None)]))

    def key_rows(cmd, clock, leaf):
        def run():
            def row(state=None, expiries=None, lanes=None, put_watch=True):
                fake = world()
                go(fake, "apply")
                if lanes:
                    edit_settings(fake, lambda d: (d["projects"]["app"]["lanes"].update(lanes), d.update(
                        session_tools={"key_file": "~/.pipeline-runner/writer-key", "writer_user_id": "writer-1"})))
                if state is not None:
                    _put(kpath(fake, leaf), KEY + "\n")
                    os.chmod(kpath(fake, leaf), state)
                if put_watch:
                    watch_conf(fake, expiries)
                code, doc, _e, _l = go(fake)
                r = [x for x in doc["rows"] if x["step"] == cmd][0]
                return r["outcome"], r["detail"], fake.writes
            dated = "%s:%s" % (clock, ON(200))
            absent, nodate, ok = row(), row(0o600), row(0o600, dated)
            ended, loose = row(0o600, "%s:%s" % (clock, ON(-3))), row(0o644, dated)
            nowatch = row(0o600, put_watch=False)
            lane = row(lanes={"code": True})
            return ((absent[0], "yet: run `%s`" % cmd in absent[1]),
                    (nodate[0], "no end date on record" in nodate[1]),
                    (ok[0], "present, the role account's own and closed to group and others, and ends on %s (in 200 days)" % ON(200) in ok[1]),
                    (ended[0], "ended on" in ended[1]), (loose[0], "mode 0600" in loose[1]),
                    (nowatch[0], "could not be read" in nowatch[1]), lane[0],
                    KEY in repr((absent, nodate, ok, ended, loose, lane)), [x[2] for x in (absent, nodate, ok)])
        return run
    for cmd, clock, leaf in NAMES:
        case("check: the %s file's presence, owner and mode are said in words, never its text; no end date on record "
             "or no conf to read it from is NOT MEASURED; ended or loose FAILS; a missing file is a fault only for "
             "the writer's key with a code lane on" % cmd, key_rows(cmd, clock, leaf),
             ((ALREADY_DONE, True), (UNKNOWN, True), (ALREADY_DONE, True), (FAILED, True), (FAILED, True),
              (UNKNOWN, True), FAILED if cmd == "writer-key" else ALREADY_DONE, False, [[]] * 3),
             ("no date taken as fine", [(me, "recorded_end", lambda wc, clock=None: (today + datetime.timedelta(days=200), "", 30))]),
             ("a loose file taken as fine", [(me, "token_file_state", lambda call, path: "ok")]),
             ("the contents shown", [(me, "TOKEN_STATE_PY", TOKEN_STATE_PY.replace(
                 'print("ok")', 'print("ok " + open(os.path.expanduser(sys.argv[1])).read())'))]),
             *([("lanes not looked at", [(me, "pushing_lanes", lambda was, kinds=None: [])])] if cmd == "writer-key" else []))

    def writer_paths():
        fake = world()
        go(fake, "apply")
        edit_settings(fake, lambda d: d.update(session_tools={"key_file": "~/.config/wk", "writer_user_id": "w-1"}))
        go(fake, "apply")
        with open(fake.home + "/.pipeline-runner/runner.json") as fh:
            kept = json.load(fh)["session_tools"]["key_file"]
        _put(fake.home + "/.config/wk", KEY + "\n")
        os.chmod(fake.home + "/.config/wk", 0o600)
        watch_conf(fake, "%s:%s" % (WRITER_CLOCK, ON(200)))
        row = [r for r in go(fake)[1]["rows"] if r["step"] == "writer-key"][0]
        got = []
        for over in ({"WRITER_KEY_FILE": "~/.pipeline-runner/tracker-key"}, {"WRITER_KEY_FILE": "~/.pipeline-runner/work/k"},
                     {"WRITER_KEY_FILE": "~/.pipeline-runner/home/k"}, {"WRITER_KEY_FILE": "~/.stage-e/kit/k"},
                     {"WRITER_KEY_FILE": "~/.stage-e/env"}, {"WRITER_KEY_FILE": "rel/k"},
                     {"WRITER_KEY_FILE": "~/.pipeline-runner/push-token"},
                     {"PUSH_TOKEN_FILE": "~/.pipeline-runner/writer-key"}):
            errors = validate_conf(parse_conf(text(**over))[0], "selftest-person")[1]
            got.append(any(e.startswith(("WRITER_KEY_FILE ", "PUSH_TOKEN_FILE ")) or "WRITER_KEY_FILE must" in e
                           for e in errors))
        return (kept, row["outcome"], "~/.config/wk is present" in row["detail"], got,
                validate_conf(parse_conf(text())[0], "p")[0]["WRITER_KEY_FILE"])
    case("conf: an unset WRITER_KEY_FILE keeps the key file the owner's `session_tools` names and `check` measures "
         "it; one that is the tracker key's, the push token's, in a folder a session writes, in the kit clone, the "
         "env file or not absolute is refused by key", writer_paths,
         ("~/.config/wk", ALREADY_DONE, True, [True] * 8, "~/.pipeline-runner/writer-key"),
         ("the installed path overwritten", [(me, "writer_path", lambda c, was: c["WRITER_KEY_FILE"])]),
         ("the path rules dropped", [(me, "path_problems", lambda c, home, skip=(): [])]))

    def writer_step_path():
        fake = world()
        go(fake, "apply")
        edit_settings(fake, lambda d: d.update(session_tools={"key_file": "~/.config/wk", "writer_user_id": "w-1"}))
        code, words, asked, sudo_asked = step(fake, token=KEY, date=ON(300), cmd="writer-key")
        there = lambda leaf: os.path.exists(fake.home + "/" + leaf)             # noqa: E731
        fresh = world()
        go(fresh, "apply")
        step(fresh, token=KEY, date=ON(300), cmd="writer-key")
        return (code, there(".config/wk"), there(".pipeline-runner/writer-key"), sudo_asked,
                os.path.exists(fresh.home + "/.pipeline-runner/writer-key"))
    case("writer-key: with WRITER_KEY_FILE unset the step writes the key file the installed settings name, the one "
         "`check` and the runner read; with none installed, the default", writer_step_path,
         (0, True, False, 1, True),
         ("the installed file ignored", [(me, "installed_key_file", lambda run, conf: None)]))

    def keys_warn():
        fake = world()
        for cmd, clock, leaf in NAMES:
            step(fake, token=KEY, date=ON(300), cmd=cmd, put_watch=cmd == NAMES[0][0])
        conf = pu.load_conf(fake.root + "/update.conf")

        class At(object):
            def __init__(self, days):
                self.now = lambda: datetime.datetime.combine(
                    today + datetime.timedelta(days=days), datetime.time(12), datetime.timezone.utc).timestamp()
        far, near = watch.check_credentials(At(0), conf), watch.check_credentials(At(280), conf)
        return far["state"], near["state"], all("%s expires on %s" % (c, ON(300)) in near["summary"]
                                                for _n, c, _l in NAMES)
    case("the health watch warns before the tracker key's and the session writer's end dates, naming each",
         keys_warn, (watch.OK, watch.FIRING, True),
         ("the dates filed under another name", [(me, "TRACKER_CLOCK", "other-name"), (me, "WRITER_CLOCK", "other-two")]))

    # 13. The parts list's own ceiling and commands.
    import pipeline_parts
    part = [p for p in pipeline_parts.parts_of(pipeline_parts.load(KIT)[0]) if p["id"] == "runner"][0]
    check("the parts list names this installer's check and apply, and a ceiling",
          (part["check"]["args"][0], part["apply"]["args"][0], part_ceiling() == part["ceiling"] is not None),
          ("check", "apply", True))

    # 14. KIT-919: the runner keys the conf carries, and the root-owned copy the runner runs from.
    def doc_of(fake):
        with open(fake.home + "/.pipeline-runner/runner.json") as fh:
            return json.load(fh)

    def reconf(fake, **over):
        _put(fake.root + "/runner.conf", text(**dict({"ENGINE_DIR": fake.engine}, **over)))

    def faults_of(**over):
        values, errors = parse_conf(text(**over))
        return errors + validate_conf(values, "selftest-person")[1]

    def carried_faults():
        found = [any(want in e for e in faults_of(**over)) for over, want in (
            ({SESSION_WRITER: "writer-1"}, "SESSION_TOOLS_KEY_FILE must"),
            ({SESSION_KEY_FILE: "~/k", SESSION_WRITER: "owner-1"}, "SESSION_TOOLS_WRITER_USER_ID is an owner's id"),
            ({"QUEUE_CONFIG": "rel/q.json"}, "QUEUE_CONFIG must"),
            ({"APPROVAL_STATES_APP": "code:s1, merge:s2"}, "APPROVAL_STATES_APP must"),
            ({"ALLOWED_HOSTS_WEB": "not a host"}, "ALLOWED_HOSTS_WEB must"),
            ({"ALLOWED_HOSTS_NOPE": "github.com"}, "ALLOWED_HOSTS_NOPE names project nope"),
            ({"ENGINE_DIR": "/unsafe-selftest/x/kit"}, "ENGINE_DIR is where"),
            ({"ENGINE_DIR": "/kit"}, "ENGINE_DIR must"))]
        good = faults_of(**{SESSION_KEY_FILE: "~/k", SESSION_WRITER: "writer-1", "QUEUE_CONFIG": "off",
                            "LOOK_CONFIG_DIR": "/var/looks", "APPROVAL_STATES_APP": "code:s-1, plan:s-2",
                            "ALLOWED_HOSTS_WEB": "off"})
        return found, good, all(p in unsafe for p in ("/Users/", "/tmp/", "/var/folders/", "/private/var/folders/"))
    case("conf: each carried key and the copy's place are judged by key; a good set has no fault; the places a "
         "session writes are refused", carried_faults, ([True] * 8, [], True),
         ("any states taken", [(me, "states_of", lambda t: {})]),
         ("any hosts taken", [(me, "hosts_of", lambda t: [])]),
         ("any path taken", [(me, "path_ok", lambda v: True)]),
         ("a key for a project not listed taken", [(me, "project_key", lambda k, real=project_key: (
             lambda r: r and (r[0], "app"))(real(k)))]),
         ("the places a session writes dropped", [(me, "UNSAFE_PREFIXES", ())]),
         ("the copy's place not judged", [(me, "engine_dir_problems", lambda v: [])]))

    def carried_written():
        fake = world(**{SESSION_KEY_FILE: "~/.keys/writer", SESSION_WRITER: "writer-1", "QUEUE_CONFIG": "~/.queue/q.json",
                        "LOOK_CONFIG_DIR": "~/.looks", "APPROVAL_STATES_APP": "code:ready-1, acceptance:ready-1, plan:plan-1",
                        "ALLOWED_HOSTS_APP": "github.com, api.github.com, registry.npmjs.org"})
        code = go(fake, "apply")[0]
        doc, again = doc_of(fake), go(fake)
        app = doc["projects"]["app"]
        return (code, doc.get("session_tools"), doc.get("queue_config"), doc.get("look_config_dir"),
                app.get("approval_states"), app.get("allowed_hosts"), "allowed_hosts" in doc["projects"]["web"],
                again[0], rows(again[1])["settings"])
    case("the carried keys: written into runner.json from the conf, and a rerun finds them current", carried_written,
         (0, {"key_file": "~/.keys/writer", "writer_user_id": "writer-1"}, "~/.queue/q.json", "~/.looks",
          {"code": ["ready-1"], "acceptance": ["ready-1"], "plan": ["plan-1"]},
          ["github.com", "api.github.com", "registry.npmjs.org"], False, 0, ALREADY_DONE),
         ("the conf's keys never written", [(me, "carry", lambda c, doc: doc)]))

    def kept_and_off():
        fake = world()
        go(fake, "apply")
        doc = doc_of(fake)
        doc.update(queue_config="~/.mine/q.json", session_tools=None)
        doc["projects"]["app"].update(allowed_hosts=[], approval_states={"review": ["st-1"]})
        _put(fake.home + "/.pipeline-runner/runner.json", json.dumps(doc, indent=2, sort_keys=True) + "\n")
        go(fake, "apply")                                            # the conf names none of them
        kept, app = doc_of(fake), doc_of(fake)["projects"]["app"]
        reconf(fake, QUEUE_CONFIG="off", APPROVAL_STATES_APP="off", ALLOWED_HOSTS_APP="github.com")
        go(fake, "apply")
        off = doc_of(fake)
        reconf(fake)                                                 # and none of them again
        code = go(fake, "apply")[0]
        return ((kept.get("queue_config"), "session_tools" in kept, kept.get("session_tools"), app.get("allowed_hosts"),
                 app.get("approval_states")), (off.get("queue_config"), "queue_config" in off,
                                               off["projects"]["app"].get("approval_states"),
                                               off["projects"]["app"].get("allowed_hosts")), code, doc_of(fake) == off)
    case("a rerun keeps the owner's own values and an off he set; `off` writes the off, and a rerun keeps it",
         kept_and_off, (("~/.mine/q.json", True, None, [], {"review": ["st-1"]}), (None, True, {}, ["github.com"]), 0,
                        True),
         ("the installed values dropped", [(me, "OWNED", OWNED + ("queue_config", "session_tools"))]),
         ("a project's installed hosts dropped", [(me, "PROJECT_OWNED", PROJECT_OWNED + ("allowed_hosts",))]),
         ("`off` read as a path", [(me, "OFF", "no-such-word")]))

    def mark_of(fake):
        with open(fake.engine + "/" + ENGINE_MARK) as fh:
            return fh.read().strip()

    def copy_made():
        fake = world()
        look = go(fake)[1]
        code = go(fake, "apply")[0]
        again = go(fake)[1]
        cmd = plistlib.loads(fake.plists[plist].encode("utf-8"))["ProgramArguments"][-1]
        return (rows(look)["engine"], code, rows(again)["engine"], mark_of(fake) == fake.git(fake.clone, "rev-parse", "HEAD"),
                sorted(os.listdir(fake.engine + "/scripts")) == sorted(RUNNER_SCRIPTS),
                fake.engine + "/scripts/" + RUNNER_SCRIPT in cmd, ".stage-e/kit/" in cmd)
    case("the copy: check says it would be made; apply makes it at the clone's commit and reads it back current; the "
         "job runs from it, never from the clone", copy_made, (WOULD_CHANGE, 0, ALREADY_DONE, True, True, True, False),
         ("the job run from the clone", [(me, "job_command", lambda c, commit: ns.exec_prefix(c) + "%s run # kit %s" % (
             c["ROLE_KIT_CLONE"] + "/scripts/" + RUNNER_SCRIPT, commit))]),
         ("a copy taken as current unread", [(me, "engine_state", lambda ctx, real=engine_state:
                                              (True, "unread", real(ctx)[2]))]))

    def copy_mended():
        fake = world()
        _put(fake.checkout + "/docs/notes.md", "# a file outside the walls\n")
        fake.commit("notes")
        go(fake, "apply")
        _put(fake.engine + "/scripts/" + RUNNER_SCRIPT, "# changed\n")
        _put(fake.engine + "/scripts/extra.py", "# extra\n")
        os.chmod(fake.engine + "/docs/notes.md", 0o666)
        code, doc, _err, _left = go(fake)
        words = said(doc)
        return (code, rows(doc)["engine"], ["1 differ" in words, "1 extra" in words, "notes.md may be written by group "
                                            "or others" in words], go(fake, "apply")[0], rows(go(fake)[1])["engine"])
    case("a changed, an extra and an open file in the copy: check names each, and apply makes it whole again",
         copy_mended, (10, WOULD_CHANGE, [True, True, True], 0, ALREADY_DONE),
         ("contents never compared", [(me, "copy_diff", lambda tree, files: ([], [], []))]),
         ("an open entry not seen", [(me, "entry_problem", lambda st, root_uid: "")]))

    def copy_moved():
        fake = world()
        go(fake, "apply")
        first, second = mark_of(fake), fake.commit("second")      # the same files, at a newer commit
        code, doc, _err, _left = go(fake)
        go(fake, "apply")
        return (code, rows(doc)["engine"], "holds kit %s, and Stage E's clone is at kit %s" % (first[:12], second[:12])
                in said(doc), mark_of(fake) == second, rows(go(fake)[1])["engine"])
    case("a moved clone: check says the copy holds the older kit; the same owner step refreshes it", copy_moved,
         (10, WOULD_CHANGE, True, True, ALREADY_DONE),
         ("an existing copy taken as current", [(me, "engine_state", lambda ctx, real=engine_state: (
             True, "x", None) if os.path.lexists(ctx.conf["ENGINE_DIR"]) else real(ctx))]),
         ("a root program that copies nothing and says it did", [(me, "ENGINE_PY", "import sys; print('copied %%s %d' %% "
                                                                   "sys.argv[4])" % (len(RUNNER_SCRIPTS) + 3))]))

    def reviewed_only():
        fake = world()
        fake.commit("not reviewed", reviewed=False)
        code, doc, _err, _left = go(fake, "apply")
        return code, rows(doc)["engine"], os.path.lexists(fake.engine), job(fake)
    case("only a commit on origin/main is copied: another fails at `engine`, no copy is made and no job loaded",
         reviewed_only, (1, FAILED, False, (False, False, False, None)),
         ("any commit copied", [(me, "_git", lambda checkout, args, stdin=None, real=_git: (0, b"") if args[:2] == [
             "merge-base", "--is-ancestor"] else real(checkout, args, stdin))]))

    def never_back():
        fake = world()
        first = fake.git(fake.clone, "rev-parse", "HEAD")
        fake.commit("second")
        go(fake, "apply")
        fake.git(fake.clone, "reset", "-q", "--hard", first)
        code, doc, _err, _left = go(fake, "apply")
        return code, rows(doc)["engine"], "never moves back" in said(doc), mark_of(fake) != first
    case("the copy never moves back: a clone moved to an older commit fails at `engine`, and the copy is kept",
         never_back, (1, FAILED, True, True),
         ("an older commit copied", [(me, "_git", lambda checkout, args, stdin=None, real=_git: (1, b"") if args[:2] == [
             "merge-base", "--is-ancestor"] and args[3] != "origin/main" else real(checkout, args, stdin))]))

    def unreadable():
        fake = world()
        go(fake, "apply")
        os.chmod(fake.engine + "/session-kinds", 0)
        try:
            code, doc, _err, _left = go(fake)
        finally:
            os.chmod(fake.engine + "/session-kinds", 0o755)
        return code, rows(doc)["engine"], "could not be read whole" in said(doc)
    case("a copy that cannot be read whole is not measured, never current", unreadable, (4, UNKNOWN, True),
         ("an unread copy taken as none", [(me, "read_copy", lambda dest, root_uid, width=40: None)]))

    def open_above():
        fake = world()
        os.makedirs(fake.root + "/engine")
        os.chmod(fake.root + "/engine", 0o777)
        try:
            code, doc, _err, _left = go(fake, "apply")
        finally:
            os.chmod(fake.root + "/engine", 0o755)
        return (code, rows(doc)["engine"], "the folder above the copy is not root's alone" in said(doc),
                os.path.lexists(fake.engine))
    case("a folder above the copy that others can write: the root program refuses in its words, and makes no copy",
         open_above, (1, FAILED, True, False),
         ("the refusal dropped", [(me, "ENGINE_PY", ENGINE_PY.replace("st.st_mode & 0o022", "False"))]))

    def kit_with_link():
        fake = world()
        os.symlink("look.sb", fake.checkout + "/templates/sandbox/other.sb")
        fake.commit("a link")
        code, doc, _err, _left = go(fake, "apply")
        return code, rows(doc)["engine"], "not a plain file" in said(doc), os.path.lexists(fake.engine)
    case("a kit commit that holds a link: refused, and never copied", kit_with_link, (1, FAILED, True, False),
         ("links taken", [(me, "plain_file", lambda mode, kind: True)]))

    def not_roots():
        fake = world()
        go(fake, "apply")
        fake.ROOT_UID = os.getuid() + 9                  # as if every entry were another account's
        code, doc, _err, _left = go(fake)
        return code, rows(doc)["engine"], "is not root's" in said(doc)
    case("a copy whose entries are not root's: never current, and said", not_roots, (10, WOULD_CHANGE, True),
         ("ownership never looked at", [(me, "entry_problem", lambda st, root_uid: "")]))

    def in_role_home():
        fake = world()
        _put(fake.root + "/runner.conf", text(ENGINE_DIR=fake.home + "/engine/kit"))
        code, doc, _err, _left = go(fake, "apply")
        return code, rows(doc)["engine"], os.path.lexists(fake.home + "/engine/kit")
    case("a copy's place in the role account's home: FAILED at `engine`, and no copy made", in_role_home,
         (1, FAILED, False), ("the role's home not refused", [(ns, "_under", lambda child, parent: False)]))

    def push_off():
        fake = world(PUSH_TOKEN_FILE="off")
        code = go(fake, "apply")[0]
        off = doc_of(fake)
        reconf(fake)                                                 # PUSH_TOKEN_FILE left out: the off stays
        again, look = go(fake, "apply")[0], go(fake)[1]
        return code, "push_token_file" in off, off.get("push_token_file"), again, doc_of(fake).get(
            "push_token_file", "absent"), rows(look)["push-token"]
    case("PUSH_TOKEN_FILE=off writes the push token off, and a rerun that leaves it out keeps the off", push_off,
         (0, True, None, 0, None, ALREADY_DONE),
         ("an installed off refilled with the default", [(me, "push_path", lambda c, was: None if c[
             "PUSH_TOKEN_FILE"] == OFF else c["PUSH_TOKEN_FILE"])]))

    # The real runner's byte path, the one that hands the copy's files to root: bytes in, its exit and its words
    # back, run from `/` (Stage E review).
    def bytes_through():
        real = Runner()
        echoed = real._exec(["/bin/sh", "-c", "cat; pwd"], b"tar\x00bytes\n", 30)
        failed = real._exec(["/bin/sh", "-c", "cat >/dev/null; echo no >&2; exit 3"], b"x", 30)
        return echoed.rc, echoed.out, failed.rc, failed.err
    case("the real runner hands bytes to a program and reads back its exit and words, from /", bytes_through,
         (0, "tar\x00bytes\n/\n", 3, "no\n"),
         ("bytes sent as text", [(Runner, "_exec", lambda self, argv, stdin, timeout, cwd=None:
                                  se.Runner._exec(self, argv, stdin, timeout, cwd=cwd))]))

    # A refresh never swaps the copy under a job that runs from it (Stage E review).
    def swap_stopped():
        fake = world()
        go(fake, "apply")                                          # the job, loaded, runs from the copy
        fake.commit("second")
        del fake.sudo[:]
        code = go(fake, "apply")[0]
        stop = [i for i, a in enumerate(fake.sudo) if a[2:4] == ["launchctl", "bootout"]]
        copy = [i for i, a in enumerate(fake.sudo) if a[2] == "/usr/bin/python3"]
        return code, bool(stop and copy and stop[0] < copy[0]), job(fake)[2:] == (True, head(fake))
    case("a refresh stops the job that runs from the copy before the swap, and the `job` row loads it again",
         swap_stopped, (0, True, True), ("swapped under a running job", [(me, "stop_for_swap", lambda ctx, dest: False)]))

    def swap_failed():
        fake = world()
        go(fake, "apply")
        fake.commit("second")
        os.chmod(fake.root + "/engine", 0o777)                     # the root program refuses this folder
        try:
            code, doc, _err, _left = go(fake, "apply")
        finally:
            os.chmod(fake.root + "/engine", 0o755)
        return code, rows(doc)["engine"], "was loaded again" in said(doc), job(fake)[2]
    case("a refresh that fails after the job was stopped loads the job again at once, and says so", swap_failed,
         (1, FAILED, True, True), ("left stopped", [(me, "load_again", lambda ctx: "not loaded")]))


if __name__ == "__main__":
    sys.exit(main())
