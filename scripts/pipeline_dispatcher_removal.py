#!/usr/bin/env python3
"""The old dispatcher leaves this Mac: the owner's script (KIT-646). Guide: docs/DISPATCHER-REMOVAL.md.

    python3 scripts/pipeline_dispatcher_removal.py check [--conf-dir DIR]   # read only: what it would remove
    python3 scripts/pipeline_dispatcher_removal.py run   [--conf-dir DIR]   # check, show, one typed yes, remove, read back
    python3 scripts/pipeline_dispatcher_removal.py --selftest

WHY. Once every lane of every project runs on our own runner, the old dispatcher and the host pieces
that exist only for it can go: its job and its package, its state backup job, the front door, the
tunnel job and its token, the firewall rule and its boot job, and the tracker application's secrets
and stored tokens with their backup copies. Then nothing on the Mac answers from outside it.

WHERE THE NAMES COME FROM. Nothing here names a path or a label of one Mac. Each value comes from the
part file that already holds it: stage-e.conf (the pipeline's account, the dispatcher's label, its
settings file and port), update.conf (its env file, its package, the line that puts its Node on PATH),
chat-lane.conf (the front door's label and settings file, when it names them) and runner.conf, read by
the runner's own loader. dispatcher-removal.conf holds only what no part file names: the state backup
job and its copies, the tunnel's job, token file and page, and any other job that exists only for the
old dispatcher. Each says `none` when this Mac has no such piece. Every file that names a value must
name the same one. The firewall rule's anchor, rules file and boot job are the chat-lane composer's own
constants, and the lane marker's folder is the review installer's. `--conf-dir` names the folder the
part files are in. Without it: the settings folder's PARTS_DIR and any file it keeps elsewhere, else
this kit folder.

THE ORDER, AND WHY. The tracker renews the session writer's key with a renewal token and hands back a
new one each time, so of two renewers the second is refused. The old dispatcher renews that key today,
and the runner reads it from the dispatcher's settings file (WRITER_KEY_SOURCE=other-program). So:
  1. Stop the old dispatcher: boot it out and disable it, so a restart cannot bring it back. It renews
     nothing more. Its port must then be closed.
  2. Hand the key over by the runner's own path: runner.conf's WRITER_KEY_SOURCE goes from
     other-program to renew (that one line; every other byte stays), and the runner's installer
     applies it, its answer through a pipe. The runner's next pass takes the renewal token from the
     dispatcher's settings file, once, and renews. The app's client details are already in the
     runner's own file: you put them there with `pipeline_runner_setup.py writer-client` beforehand.
  3. Wait until the runner says it renewed the key and the key answered as the session writer. Then
     the runner's own `check` must say the same.
  4. Only then remove, each piece read back, in this order: the dispatcher's job file, the state backup
     job, any other job named, the front door, the tunnel, the firewall rule, the package, the lane
     marker, and the secrets and stored tokens with their backup copies.
A failure in 1 to 3 removes nothing, and the end says how to start the old dispatcher again, or why not
to. A failure in 4 stops there: what comes after it is untouched, and the end says what was done and what
was not. Run it again after a fix: each step reads where it stands first, and a piece already gone is
said.

NO KEY EVER REACHES THIS SCRIPT. A file that holds one is looked at with lstat, never read. A program
run as the pipeline's account prints names, counts and paths only: the env file's key NAMES (for the
steps outside the Mac), whether the renewal token's JSON key is in the settings file. The app's client
details are typed into the runner's own `writer-client`, never here. It never revokes a key. The renewal
retires the old copies, since a renewal token works once. Revoking the app's authorization would end the
runner's key too.

THE ANSWERS (`check`, and the first half of `run`)
  READY          every gate holds, and something is left to remove
  NOTHING LEFT   every piece is already gone
  NOT READY      a gate does not hold: each reason is said. Nothing changed
  UNKNOWN        something could not be read. Never READY
Both commands ask for your Mac password once (`sudo -v`). Every later sudo call is `sudo -n`.

Refused under a model (any agent-environment marker) and as root, before it reads anything.
Exit: 0 ready, done, or nothing left · 1 not ready, or not done · 2 usage or settings · 3 refused ·
4 something could not be read; nothing changed · 5 no administrator access; nothing changed ·
10 you did not type yes; nothing changed.
"""
import argparse
import json
import os
import plistlib
import re
import shlex
import stat
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from pipeline_agent_env import AGENT_ENV_MARKERS  # noqa: E402  (the one list, imported, never copied)
import pipeline_root_guard as rg  # noqa: E402  (KIT-693: the one root refusal every owner script shares)
import pipeline_stage_e_setup as se  # noqa: E402  (its label and account rules, the lane marker's folder)
import pipeline_update as pu  # noqa: E402  (its conf defaults, the token store's name, the role's Node line)
import pipeline_chat_lane_setup as cl  # noqa: E402  (the firewall rule it composed: anchor, files, boot job)
import pipeline_runner_setup as rs  # noqa: E402  (runner.conf's own loader)
import pipeline_runner_store as store  # noqa: E402  (the lanes the runner says it takes)
import pipeline_writer_key as wkey  # noqa: E402  (the runner's status of the writer's key, and its shapes)
import pipeline_settings as ps  # noqa: E402  (the settings folder, and its one writer of a conf line)

CONF_NAME = "dispatcher-removal.conf"
STAGE_E, UPDATE, CHAT, RUNNER = "stage-e.conf", "update.conf", "chat-lane.conf", "runner.conf"
PART_FILES = (STAGE_E, UPDATE, CHAT, RUNNER)
REQUIRED_PARTS = (STAGE_E, UPDATE, RUNNER)
NONE = "none"
LAUNCH_DAEMONS = "/Library/LaunchDaemons"
MARKER_DIR = se.LANE_SYSTEM_DIR
OLD_MARKER_DIR = se.DEFAULT_PINS_ROOT + "/" + se.LANE_MARKER_DIR      # `~` is the pipeline account's home
BACKUPS_DIR = os.path.dirname(se.ROLE_KIT_CLONE_PATH) + "/backups"    # the review installer's config copies
PF_ANCHOR, PF_RULES_DIR, PF_RULES = cl.PF_ANCHOR, cl.PF_RULES_DIR, cl.PF_RULES_PATH
PF_LABEL, PF_LOG = cl.PF_DAEMON_LABEL, cl.PF_DAEMON_LOG
TRACKER_APPS_PAGE = "https://linear.app/settings/api/applications"
CODE_HOST_TOKENS_PAGE = "https://github.com/settings/tokens"
ANSWERS = '{"schema": "runner-answers/1", "consent": "yes"}'
GONE_WAIT = 150          # the dispatcher's 120-second exit timeout (docs/HOST.md), and launchd's teardown
GONE_POLL = 2
HANDOVER_WAIT = 12 * 60  # two of the runner's five-minute retries after a renewal that did not complete
HANDOVER_POLL = 5        # the runner's own pass
SLACK = 5
ROOT_UID = 0             # who owns the tunnel's token: root (the selftest stands in for root)
HARD_REASONS = ("renewal-refused", "client-refused", "no-renewal-token", "no-client", "other-user",
                "refused", "unreadable")
READY, NOTHING, NOT_READY, UNKNOWN = "READY", "NOTHING LEFT", "NOT READY", "UNKNOWN"
OK, BLOCKED = "OK", "BLOCKED"
PRESENT, GONE = "PRESENT", "GONE"
EXIT_OK, EXIT_NOT, EXIT_USAGE, EXIT_REFUSED, EXIT_UNKNOWN, EXIT_NOPRIV, EXIT_NOT_YES = 0, 1, 2, 3, 4, 5, 10
ANSWER_EXIT = {READY: EXIT_OK, NOTHING: EXIT_OK, NOT_READY: EXIT_NOT, UNKNOWN: EXIT_UNKNOWN}
# The example's values, refused as typed, so an unedited copy of the example cannot run.
CONF_UNEDITED = {"STATE_BACKUP_SERVICE": "com.example.dispatcher-backup",
                 "STATE_BACKUP_COPIES": "/opt/example-dispatcher/state/state.json.bak-",
                 "TUNNEL_SERVICE": "com.example.tunnel", "TUNNEL_TOKEN_FILE": "/etc/example-tunnel/token",
                 "TUNNEL_PAGE": "https://dash.example.com/tunnels"}
CONF_KEYS = ("STATE_BACKUP_SERVICE", "STATE_BACKUP_COPIES", "TUNNEL_SERVICE", "TUNNEL_TOKEN_FILE",
             "TUNNEL_PAGE", "OTHER_SERVICES", "FRONT_DOOR_SERVICE", "FRONT_DOOR_CONFIG")
CONF_OPTIONAL = ("FRONT_DOOR_SERVICE", "FRONT_DOOR_CONFIG")       # empty: chat-lane.conf's
# Each shared value, and the part files that name it. Every one that names it must name the same.
SHARED = (("ROLE_ACCOUNT", (STAGE_E, UPDATE, CHAT, RUNNER)), ("DISPATCHER_SERVICE", (STAGE_E, UPDATE, CHAT)),
          ("DISPATCHER_CONFIG", (STAGE_E, CHAT)), ("DISPATCHER_PORT", (STAGE_E, UPDATE, CHAT)),
          ("DISPATCHER_ENV_FILE", (UPDATE, CHAT)))
PATH_RE = re.compile(r"^/[^\x00-\x1f\x7f]*\Z")
URL_RE = re.compile(r"^https://[^\s\x00-\x1f\x7f]{3,300}\Z")
PREFIX_RE = re.compile(r"^[^/\x00-\x1f\x7f]{1,120}\Z")
GITHUB_RE = re.compile(r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
                       r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?\Z")


class Res(object):
    def __init__(self, rc, out="", err=""):
        self.rc, self.out, self.err = rc, out or "", err or ""

    ok = property(lambda self: self.rc == 0)


class IO(object):
    """Everything this script asks of the world: the environment, the clock, processes, files and you."""

    def env(self):
        return dict(os.environ)

    def uid(self):
        return os.geteuid()

    def now(self):
        return time.time()

    def sleep(self, seconds):
        time.sleep(seconds)

    def say(self, text):
        print(text)
        sys.stdout.flush()

    def ask(self, prompt):
        try:
            return input(prompt)
        except EOFError:
            return ""

    def run(self, argv, stdin=None, timeout=120):
        """(rc, out, err) of one program. Standard input is closed unless text is given: nothing asks."""
        feed = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
        try:
            p = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout, cwd="/", **feed)
        except FileNotFoundError:
            return Res(127, "", "%s is not on this Mac" % argv[0])
        except subprocess.TimeoutExpired:
            return Res(124, "", "it did not end within %d seconds" % timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            return Res(126, "", "it could not be run (%s)" % type(exc).__name__)
        return Res(p.returncode, p.stdout, p.stderr)

    def sudo(self, why):
        """Your Mac password, once, here; kept fresh while this runs. Raises se.NoPrivilege."""
        se.SudoSession().acquire(why, "run")

    def read_text(self, path):
        """A small file of yours, never through a link. Raises OSError or ValueError."""
        return ps.read_text(path)

    def put_file(self, path, text, mode):
        """One of your conf files, swapped in whole beside itself, never through a link."""
        ps.put_file(path, text, mode)

    def read_plist(self, path):
        """A job file, which everyone may read. None when there is none; raises OSError or ValueError."""
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "rb") as fh:
            return plistlib.load(fh)

    def installer(self, args, answers=None):
        """(exit, its rows document or None) of the runner's own installer, run by the interpreter running this.
        Its words go to your terminal; its one JSON document is read here. Its answer, when it takes one, goes
        through a pipe, the only way that installer takes one."""
        argv = [sys.executable, os.path.join(HERE, "pipeline_runner_setup.py")] + list(args)
        feed = {"input": answers} if answers is not None else {"stdin": subprocess.DEVNULL}
        try:
            p = subprocess.run(argv, stdout=subprocess.PIPE, text=True, timeout=1800, **feed)
        except (OSError, subprocess.SubprocessError):
            return 126, None
        try:
            doc = json.loads((p.stdout or "").strip().splitlines()[-1])
        except (ValueError, IndexError):
            doc = None
        return p.returncode, doc if isinstance(doc, dict) else None


def refused(io):
    """The one refusal line, or None. Read before anything else, so a refusal reads nothing."""
    if any(m in io.env() for m in AGENT_ENV_MARKERS):
        return ("REFUSED: an agent's environment. This script is yours to run, in your own terminal. "
                "Nothing was changed.")
    return rg.refusal(io.uid())


# --------------------------------------------------------------------------- the names, from the confs
def part_paths(conf_dir, environ, kit=KIT):
    """({file name: path}, where they came from, None), or (None, None, why): the part files and this
    script's own conf. A settings folder that is there and cannot be read is said, never passed over."""
    if conf_dir:
        base = os.path.abspath(os.path.expanduser(conf_dir))
        return dict((n, os.path.join(base, n)) for n in PART_FILES + (CONF_NAME,)), "--conf-dir", None
    sdir, why = ps.settings_dir_of(None, environ)
    if not sdir:
        return None, None, "the settings folder's place is not a full path (%s): pass --conf-dir" % why
    try:
        kit_parts = ps.Kit(kit)
        state, folder, _problems, _notes = ps.load(sdir, kit_parts, os.getuid())
    except (OSError, ValueError) as exc:
        return None, None, "the settings folder %s could not be read (%s): pass --conf-dir" % (sdir, type(exc).__name__)
    if state != "read" or folder is None:
        return dict((n, os.path.join(kit, n)) for n in PART_FILES + (CONF_NAME,)), "this kit folder", None
    out = dict((n, ps.part_path(folder, kit_parts, n)[0]) for n in PART_FILES)
    out[CONF_NAME] = os.path.join(folder.places.get("PARTS_DIR") or kit, CONF_NAME)
    return out, "the settings folder %s" % sdir, None


def _abs(value):
    return bool(PATH_RE.match(value or "")) and "/../" not in value + "/" and value.rstrip("/") != ""


def _label(value):
    return bool(se._RDNS_RE.match(value or ""))


def read_conf(io, path):
    """(values, None) or (None, why): KEY=value lines, as the part files' own parsers read them."""
    try:
        return ps.conf_values(io.read_text(path)), None
    except FileNotFoundError:
        return None, "absent"
    except (OSError, ValueError) as exc:
        return None, "could not be read (%s)" % type(exc).__name__


def load_names(io, paths):
    """(names, errors): every value this script acts on, and every fault, all in one pass. Values only:
    no conf holds a credential."""
    errors, parts = [], {}
    for name in PART_FILES:
        values, why = read_conf(io, paths[name])
        if values is None and (name in REQUIRED_PARTS or why != "absent"):
            errors.append("%s %s: %s" % (name, why, paths[name]))
        parts[name] = values
    own, why = read_conf(io, paths[CONF_NAME])
    if own is None:
        errors.append("%s %s: %s (cp %s.example %s, and fill it in)" % (CONF_NAME, why, paths[CONF_NAME],
                                                                       CONF_NAME, CONF_NAME))
        own = {}
    if errors:
        return None, errors
    n = {"paths": paths}
    for key, files in SHARED:
        named = [(f, parts[f].get(key)) for f in files if parts.get(f) is not None and parts[f].get(key)]
        values = sorted(set(v for _f, v in named))
        if len(values) > 1:
            errors.append("%s differs between %s: every file that names it must name the same one"
                          % (key, ", ".join(f for f, _v in named)))
        elif not values:
            errors.append("%s is named by none of %s" % (key, ", ".join(files)))
        n[key] = values[0] if values else ""
    upd = dict(pu.CONF_DEFAULTS, **dict((k, v) for k, v in parts[UPDATE].items() if v))
    n["DISPATCHER_PACKAGE"], n["DISPATCHER_NODE_SETUP"] = upd.get("DISPATCHER_PACKAGE", ""), upd.get(
        "DISPATCHER_NODE_SETUP", "")
    chat = parts.get(CHAT) or {}
    for key in ("FRONT_DOOR_SERVICE", "FRONT_DOOR_CONFIG"):
        mine, theirs = own.get(key, ""), chat.get(key, "")
        if mine and theirs and mine != theirs:
            errors.append("%s differs between %s and %s" % (key, CONF_NAME, CHAT))
        n[key] = mine or theirs
        if not n[key]:
            errors.append("%s is named by neither %s nor %s: name it in %s, or say `none`" % (key, CHAT, CONF_NAME,
                                                                                           CONF_NAME))
    n["FRONT_DOOR_HOST"] = chat.get("FRONT_DOOR_HOST", "")
    for key in CONF_KEYS:
        if key in CONF_OPTIONAL:
            continue
        if not own.get(key):
            errors.append("%s is required in %s: its value, or `none` when this Mac has no such piece"
                          % (key, CONF_NAME))
        elif CONF_UNEDITED.get(key) == own[key]:
            errors.append("%s is still the example's value" % key)
        n[key] = own.get(key, "")
    for key in sorted(k for k in own if k not in CONF_KEYS):
        errors.append("%s names %s, which it does not know" % (CONF_NAME, key))
    errors.extend(judge_names(n))
    try:
        runner, more = rs.load_conf(paths[RUNNER], None)
    except rs.SetupError as exc:
        runner, more = None, [str(exc).splitlines()[0]]
    errors.extend("%s: %s" % (RUNNER, e) for e in more)
    n["runner"] = runner
    return (n if not errors else None), errors


def judge_names(n):
    """The rules each value keeps: its owner's rules where it has one."""
    bad = []
    if not se._ACCOUNT_RE.match(n["ROLE_ACCOUNT"] or "") or n["ROLE_ACCOUNT"] == "root":
        bad.append("ROLE_ACCOUNT is not a local account other than root")
    for key in ("DISPATCHER_SERVICE",):
        if not _label(n[key]):
            bad.append("%s is not a reverse-DNS launchd label" % key)
    for key in ("DISPATCHER_CONFIG", "DISPATCHER_ENV_FILE"):
        if not _abs(n[key]):
            bad.append("%s must be an absolute path" % key)
    if not (n["DISPATCHER_PORT"] or "").isdigit() or not 0 < int(n["DISPATCHER_PORT"]) < 65536:
        bad.append("DISPATCHER_PORT must be a port number")
    pkg = n["DISPATCHER_PACKAGE"]
    if not pu.SAFE_NAME_RE.match(pkg or "") or ".." in pkg or pkg.startswith(("/", "-")):
        bad.append("DISPATCHER_PACKAGE (update.conf) is not a package name")
    if not n["DISPATCHER_NODE_SETUP"] or "\n" in n["DISPATCHER_NODE_SETUP"]:
        bad.append("DISPATCHER_NODE_SETUP (update.conf) must be one shell line")
    for key, rule in (("STATE_BACKUP_SERVICE", _label), ("TUNNEL_SERVICE", _label), ("FRONT_DOOR_SERVICE", _label),
                      ("TUNNEL_TOKEN_FILE", _abs), ("FRONT_DOOR_CONFIG", _abs),
                      ("TUNNEL_PAGE", lambda v: bool(URL_RE.match(v)))):
        if n.get(key) and n[key] != NONE and not rule(n[key]):
            bad.append("%s is not %s, or `none`" % (key, "a reverse-DNS launchd label" if rule is _label else
                                                      "an https page" if key == "TUNNEL_PAGE" else "an absolute path"))
    copies = n.get("STATE_BACKUP_COPIES") or ""
    if copies and copies != NONE and not (_abs(os.path.dirname(copies)) and PREFIX_RE.match(os.path.basename(copies))):
        bad.append("STATE_BACKUP_COPIES must be <its folder>/<the start of each copy's name>, or `none`")
    others = [x for x in (n.get("OTHER_SERVICES") or "").replace(" ", "").split(",") if x]
    if others != [NONE] and not all(_label(x) for x in others):
        bad.append("OTHER_SERVICES must be launchd labels, comma-separated, or `none`")
    labels = [n[k] for k in ("DISPATCHER_SERVICE", "STATE_BACKUP_SERVICE", "TUNNEL_SERVICE", "FRONT_DOOR_SERVICE")
              if n.get(k) and n[k] != NONE] + [x for x in others if x != NONE]
    if len(set(labels)) != len(labels) or PF_LABEL in labels:
        bad.append("one launchd label is named for two pieces")
    return bad


def opt(n, key):
    """A removal-conf value, or None when this Mac has no such piece."""
    v = n.get(key) or ""
    return None if v in ("", NONE) else v


def other_services(n):
    return [x for x in (n.get("OTHER_SERVICES") or "").replace(" ", "").split(",") if x and x != NONE]


# --------------------------------------------------------------------------- the reads
def as_root(io, argv, timeout=120):
    return io.run(["sudo", "-n"] + list(argv), timeout=timeout)


def as_role(io, account, script, timeout=300):
    """A /bin/sh line as the pipeline's account, from `/`: that account cannot enter your folders."""
    return io.run(["sudo", "-n", "-u", account, "-H", "/bin/sh", "-c", "cd / && " + script], timeout=timeout)


def _doc(res):
    if not res.ok:
        return None
    try:
        doc = json.loads(res.out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None
    return doc if isinstance(doc, dict) else None


def role_py(io, account, program, args):
    """One of the programs below, as the pipeline's account: its one JSON line, or None."""
    return _doc(as_role(io, account, "/usr/bin/python3 -I -c %s %s" % (
        shlex.quote(program), " ".join(shlex.quote(a) for a in args))))


def root_py(io, program, args):
    return _doc(as_root(io, ["/usr/bin/python3", "-I", "-c", program] + list(args)))


# What each path is: never what it holds. `~/` is the account's own home. `real` is where it resolves.
KINDS_PY = r'''
import json, os, stat, sys
out = {}
for p in sys.argv[1:]:
    q = os.path.expanduser(p) if p.startswith("~/") or p == "~" else p
    row = {"real": os.path.realpath(q)}
    try:
        st = os.lstat(q)
    except FileNotFoundError:
        row["kind"] = "absent"
    except OSError as e:
        row["kind"] = "unread"
    else:
        row["kind"] = ("link" if stat.S_ISLNK(st.st_mode) else "file" if stat.S_ISREG(st.st_mode)
                       else "dir" if stat.S_ISDIR(st.st_mode) else "other")
        row.update(uid=st.st_uid, mode=stat.S_IMODE(st.st_mode), size=st.st_size)
    out[p] = row
print(json.dumps(out))
'''

# The names in one folder that start with one prefix: files and links only.
MATCHES_PY = r'''
import json, os, stat, sys
d = os.path.expanduser(sys.argv[1]) if sys.argv[1].startswith("~/") else sys.argv[1]
try:
    names = sorted(os.listdir(d))
except FileNotFoundError:
    print(json.dumps({"state": "absent", "names": [], "real": os.path.realpath(d)}))
    sys.exit(0)
except OSError:
    print(json.dumps({"state": "unread", "names": [], "real": os.path.realpath(d)}))
    sys.exit(0)
out = []
for n in names:
    if n.startswith(sys.argv[2]):
        try:
            m = os.lstat(os.path.join(d, n)).st_mode
        except OSError:
            continue
        if stat.S_ISREG(m) or stat.S_ISLNK(m):
            out.append(n)
print(json.dumps({"state": "read", "names": out, "real": os.path.realpath(d)}))
'''

# Remove each path: a file or a link (never what a link points at). A path ending in `/` is a folder,
# removed only when it is empty. Prints each one's result, never a file's text.
REMOVE_PY = r'''
import errno, json, os, stat, sys
out = {}
for p in sys.argv[1:]:
    q = os.path.expanduser(p) if p.startswith("~/") else p
    try:
        if q.endswith("/"):
            os.rmdir(q.rstrip("/"))
            out[p] = "removed"
            continue
        m = os.lstat(q).st_mode
        if not (stat.S_ISREG(m) or stat.S_ISLNK(m)):
            out[p] = "refused: not a file"
            continue
        os.unlink(q)
        out[p] = "removed"
    except FileNotFoundError:
        out[p] = "absent"
    except OSError as e:
        out[p] = "kept: not empty" if e.errno in (errno.ENOTEMPTY, errno.EEXIST) else "failed: %s" % errno.errorcode.get(e.errno, "error")
print(json.dumps(out))
'''

# The old dispatcher's entries: ids, addresses, paths and a switch. Never a token.
ENTRIES_PY = r'''
import json, re, sys
try:
    with open(sys.argv[1]) as fh:
        c = json.load(fh)
except FileNotFoundError:
    print(json.dumps({"state": "absent"}))
    sys.exit(0)
except (OSError, ValueError):
    print(json.dumps({"state": "unread"}))
    sys.exit(0)
rs = c.get("repositories") if isinstance(c, dict) else None
if not isinstance(rs, list):
    print(json.dumps({"state": "unread"}))
    sys.exit(0)
s = lambda v: v if isinstance(v, str) and 0 < len(v) < 500 else None
u = lambda v: re.sub(r"//[^/@]*@", "//", s(v)) if s(v) else None
print(json.dumps({"state": "read", "entries": [
    {"id": s(r.get("id")), "githubUrl": u(r.get("githubUrl")), "repositoryPath": s(r.get("repositoryPath")),
     "workspaceBaseDir": s(r.get("workspaceBaseDir")), "active": r.get("isActive") is not False}
    for r in rs if isinstance(r, dict)]}))
'''

# Where one JSON key sits in a settings file, at any depth: how many places, whether they agree, and
# whether each is shaped like the token the runner takes. Never the value.
FIELD_PY = r'''
import json, re, sys
try:
    with open(sys.argv[1]) as fh:
        doc = json.load(fh)
except FileNotFoundError:
    print(json.dumps({"state": "absent"}))
    sys.exit(0)
except (OSError, ValueError):
    print(json.dumps({"state": "unread"}))
    sys.exit(0)
found = []
def walk(o):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == sys.argv[2]:
                found.append(v)
            walk(v)
    elif isinstance(o, list):
        for v in o:
            walk(v)
walk(doc)
pat = re.compile(sys.argv[3])
print(json.dumps({"state": "read", "places": len(found), "same": len(set(map(repr, found))) <= 1,
                  "shaped": bool(found) and all(isinstance(v, str) and bool(pat.match(v)) for v in found)}))
'''

# The NAMES in an env file, for the steps outside the Mac. The value after `=` is never read out.
NAMES_PY = r'''
import json, re, sys
try:
    with open(sys.argv[1]) as fh:
        lines = fh.read(1 << 20).splitlines()
except FileNotFoundError:
    print(json.dumps({"state": "absent", "names": []}))
    sys.exit(0)
except (OSError, ValueError):
    print(json.dumps({"state": "unread", "names": []}))
    sys.exit(0)
names = []
for line in lines:
    m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=", line)
    if m and m.group(1) not in names:
        names.append(m.group(1))
print(json.dumps({"state": "read", "names": names}))
'''

# The JSON documents the runner writes about itself, which hold no key: its lanes and its writer status.
# For its settings, only the fields this script needs.
JSON_PY = r'''
import json, os, sys
p = os.path.expanduser(sys.argv[1])
try:
    with open(p) as fh:
        doc = json.load(fh)
except FileNotFoundError:
    print(json.dumps({"state": "absent"}))
    sys.exit(0)
except (OSError, ValueError):
    print(json.dumps({"state": "unread"}))
    sys.exit(0)
if sys.argv[2] == "settings":
    st = doc.get("session_tools") if isinstance(doc, dict) else None
    src = (st or {}).get("key_source") if isinstance(st, dict) else None
    keep = ("kind", "file", "field", "renewal_file", "client_file", "adopt_file", "adopt_field")
    doc = {"enabled": doc.get("enabled") if isinstance(doc, dict) else None,
           "projects": dict((k, (v or {}).get("repo")) for k, v in ((doc.get("projects") or {}).items()
                            if isinstance(doc, dict) and isinstance(doc.get("projects"), dict) else ())
                            if isinstance(v, dict)),
           "key_source": dict((k, src.get(k)) for k in keep) if isinstance(src, dict) else None,
           "has_session_tools": isinstance(st, dict)}
print(json.dumps({"state": "read", "doc": doc}))
'''

# Each clone's origin, as OWNER/NAME's address: git's own config, read by the account that owns the clone.
ORIGINS_PY = r'''
import json, os, re, subprocess, sys
out = {}
for p in sys.argv[1:]:
    q = os.path.expanduser(p) if p.startswith("~/") else p
    try:
        r = subprocess.run(["git", "-C", q, "config", "--get", "remote.origin.url"], capture_output=True,
                           text=True, timeout=30)
        out[p] = re.sub(r"//[^/@]*@", "//", r.stdout.strip()) if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        out[p] = None
print(json.dumps(out))
'''


def job_held(io, label):
    """True while launchd holds the job, False once it does not, None when launchd could not be asked."""
    res = as_root(io, ["/bin/launchctl", "print", "system/" + label])
    if res.ok:
        return True
    if res.rc == 113 or "could not find service" in (res.out + res.err).lower():
        return False
    return None


def job_running(io, label):
    res = as_root(io, ["/bin/launchctl", "print", "system/" + label])
    if not res.ok:
        return False if res.rc == 113 or "could not find service" in (res.out + res.err).lower() else None
    return "state = running" in res.out


def plist_path(label):
    return "%s/%s.plist" % (LAUNCH_DAEMONS, label)


def plist_strings(io, label):
    """Every string a job file holds, or None when it has none; raises OSError or ValueError when it cannot
    be read. Used only to bind a token file to the job that names it."""
    doc = io.read_plist(plist_path(label))
    if doc is None:
        return None
    out = []

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str):
            out.append(o)
    walk(doc)
    return out


def names_file(text, path):
    return any(s == path or s.endswith("=" + path) for s in text)


def pf_rules(io):
    """The rules loaded in the firewall rule's anchor: a list, or None when pfctl could not be asked."""
    res = as_root(io, ["/sbin/pfctl", "-a", PF_ANCHOR, "-s", "rules"])
    if not res.ok:
        return None
    return [ln for ln in res.out.splitlines() if ln.strip()]


def port_state(io, number):
    """closed, open, or unknown: whether anything listens on the port, on any interface. Its firewall rule
    keeps the port from other machines, so it goes only once nothing listens there at all."""
    res = as_root(io, ["/usr/sbin/lsof", "-nP", "-iTCP:%d" % number, "-sTCP:LISTEN", "-Fn"])
    if res.rc == 1 and not res.out.strip() and not res.err.strip():
        return "closed"
    return "open" if res.ok and res.out.strip() else "unknown"


def role_cwds(io, account):
    """The working folder of each of the account's processes, or None when it could not be listed."""
    res = as_root(io, ["/usr/sbin/lsof", "-a", "-u", account, "-d", "cwd", "-Fn"])
    if res.rc == 1 and not res.out.strip() and not res.err.strip():
        return []           # lsof's exit 1 is also its error: only a silent one means the account has none
    if not res.ok:
        return None
    return [ln[1:] for ln in res.out.splitlines() if ln.startswith("n")]


def package_state(io, n):
    """present, absent or None: the package in the account's global npm folder."""
    pkg = n["DISPATCHER_PACKAGE"]
    res = as_role(io, n["ROLE_ACCOUNT"], pu._role(n, 'R="$(npm root -g 2>/dev/null)"; [ -n "$R" ] || exit 7; '
                                                     'if [ -e "$R/%s" ]; then echo present; else echo absent; fi'
                                                     % pkg))
    word = res.out.strip().splitlines()[-1] if res.ok and res.out.strip() else ""
    return word if word in ("present", "absent") else None


def under(path, top):
    return path == top or path.startswith(top.rstrip("/") + "/")


def repo_of(url):
    m = GITHUB_RE.match(url or "")
    return m.group(1).lower() if m else None


# --------------------------------------------------------------------------- the survey
class Survey(object):
    """What `check` shows and `run` acts on. Built by reading only."""

    def __init__(self):
        self.gates, self.pieces, self.facts, self.handover = [], [], {}, None

    def gate(self, gid, state, words):
        self.gates.append((gid, state, words))

    def answer(self):
        states = [s for _g, s, _w in self.gates] + [p["state"] for p in self.pieces]
        if UNKNOWN in states:
            return UNKNOWN
        if BLOCKED in states:
            return NOT_READY
        return READY if any(p["state"] == PRESENT for p in self.pieces) else NOTHING


def survey(io, n):
    """Every gate and every piece, read. Nothing here changes anything."""
    sv, acct, runner = Survey(), n["ROLE_ACCOUNT"], n["runner"]
    rdir = runner["RUNNER_DIR"].rstrip("/")
    home = role_py(io, acct, KINDS_PY, ["~"])
    sv.facts["home"] = home and (home.get("~") or {}).get("real")
    entries = role_py(io, acct, ENTRIES_PY, [n["DISPATCHER_CONFIG"]])
    sv.facts["entries"] = entries
    sv.facts["dispatcher"] = job_held(io, n["DISPATCHER_SERVICE"])
    pieces(io, n, sv)
    if not any(p["state"] == PRESENT for p in sv.pieces) and all(p["state"] == GONE for p in sv.pieces):
        return sv
    gate_lanes(io, n, sv, rdir, entries)
    gate_sessions(io, n, sv, entries)
    gate_files(io, n, sv)
    gate_runner(io, n, sv, rdir)
    return sv


def gate_lanes(io, n, sv, rdir, entries):
    """Every lane of every runner project is on, and every repository the old dispatcher serves is one."""
    acct = n["ROLE_ACCOUNT"]
    settings = role_py(io, acct, JSON_PY, [rdir + "/runner.json", "settings"])
    lanes = role_py(io, acct, JSON_PY, [rdir + "/store/" + store.LANES_FILE, "doc"])
    sv.facts["settings"] = settings and settings.get("doc")
    if settings is None or lanes is None or settings.get("state") == "unread" or lanes.get("state") == "unread":
        return sv.gate("lanes", UNKNOWN, "the runner's settings or its lanes record could not be read as %s" % acct)
    if settings["state"] == "absent":
        return sv.gate("lanes", BLOCKED, "the runner is not installed (no %s/runner.json)" % rdir)
    if lanes["state"] == "absent":
        return sv.gate("lanes", BLOCKED, "the runner has not said which lanes it takes (no lanes record): "
                                         "its job is not running, or never ran")
    bad = store.lanes_problems(lanes["doc"])
    if bad:
        return sv.gate("lanes", UNKNOWN, "the runner's lanes record is not in its shape (%s)" % bad[0])
    doc, problems = lanes["doc"], []
    projects = settings["doc"].get("projects") or {}
    if doc.get("enabled") is not True:
        problems.append("the runner is switched off, so every lane still goes the old way")
    for key in sorted(projects):
        off = [k for k in store.KINDS if not store.lane_on(doc, key, k)]
        if off:
            problems.append("%s: %s still off on the runner" % (key, ", ".join(off)))
    if entries is None or entries.get("state") == "unread":
        return sv.gate("lanes", UNKNOWN, "the old dispatcher's settings could not be read as %s, so the "
                                         "repositories it serves are not known" % acct)
    serves = []
    if entries["state"] == "read":
        serves = [e for e in entries["entries"] if e["active"]]
    elif sv.facts["dispatcher"] is not False:
        return sv.gate("lanes", UNKNOWN, "the old dispatcher's settings file is gone but its job is not, so "
                                         "what it serves is not known")
    clones = sorted(set(v for v in projects.values() if isinstance(v, str)))
    paths = clones + sorted(set(e["repositoryPath"] for e in serves if e["repositoryPath"] and not e["githubUrl"]))
    origins = role_py(io, acct, ORIGINS_PY, paths) if paths else {}
    if origins is None:
        return sv.gate("lanes", UNKNOWN, "the clones' origins could not be read as %s" % acct)
    ours = set(filter(None, (repo_of(origins.get(c)) for c in clones)))
    for e in serves:
        repo = repo_of(e["githubUrl"]) or repo_of(origins.get(e["repositoryPath"]))
        if repo is None:
            return sv.gate("lanes", UNKNOWN, "which repository the old dispatcher's entry %s serves could not "
                                             "be read" % (e["id"] or e["repositoryPath"] or "?"))
        if repo not in ours:
            problems.append("the old dispatcher serves %s (entry %s), which no runner project is" % (repo, e["id"]))
    if problems:
        return sv.gate("lanes", BLOCKED, "; ".join(problems))
    sv.gate("lanes", OK, "every lane of %d project(s) runs on the runner (%s), and the old dispatcher serves no "
                         "other repository" % (len(projects), ", ".join(sorted(projects)) or "none"))


def gate_sessions(io, n, sv, entries):
    """No session of the old dispatcher works in its folders, so none is cut off, and the lane marker
    outlives every one of them."""
    dirs = sorted(set(e["workspaceBaseDir"] for e in (entries or {}).get("entries") or [] if e["workspaceBaseDir"]))
    sv.facts["workspaces"] = dirs
    if entries is None or entries.get("state") != "read":
        sv.facts["sessions"] = None
        return sv.gate("sessions", OK if sv.facts["dispatcher"] is False else UNKNOWN,
                       "the old dispatcher's job is gone" if sv.facts["dispatcher"] is False else
                       "the old dispatcher's work folders are not known, so its sessions cannot be looked for")
    cwds = role_cwds(io, n["ROLE_ACCOUNT"])
    if cwds is None:
        sv.facts["sessions"] = None
        return sv.gate("sessions", UNKNOWN, "the processes of %s could not be listed" % n["ROLE_ACCOUNT"])
    home = sv.facts.get("home") or ""
    real = [d.replace("~", home, 1) if d.startswith("~/") and home else d for d in dirs]
    busy = sorted(set(c for c in cwds for d in real if under(c, d)))
    sv.facts["sessions"] = busy
    if busy:
        return sv.gate("sessions", BLOCKED, "%d process(es) of the old dispatcher still work in %s: wait until "
                                            "they end" % (len(busy), ", ".join(real)))
    sv.gate("sessions", OK, "no process of %s works in the old dispatcher's folders (%s)" % (
        n["ROLE_ACCOUNT"], ", ".join(real) or "none named"))


def secret_paths(n):
    env_dir = os.path.dirname(n["DISPATCHER_ENV_FILE"])
    return [n["DISPATCHER_ENV_FILE"], n["DISPATCHER_CONFIG"], env_dir + "/" + pu.TOKEN_STORE]


def gate_files(io, n, sv):
    """None of the files this removes is one of the runner's or the kit's own, by where it resolves."""
    r = n["runner"]
    own = [r["RUNNER_DIR"], r["ENV_FILE"], r["ROLE_KIT_CLONE"], r["WRITER_KEY_FILE"], r["WRITER_RENEWAL_FILE"],
           r["WRITER_CLIENT_FILE"], "~/.stage-e/env"] + ([r["PUSH_TOKEN_FILE"]] if r["PUSH_TOKEN_FILE"] != "off" else [])
    theirs = secret_paths(n) + [p for p in (opt(n, "FRONT_DOOR_CONFIG"),) if p]
    copies = opt(n, "STATE_BACKUP_COPIES")
    if copies:
        theirs.append(os.path.dirname(copies))
    got = role_py(io, n["ROLE_ACCOUNT"], KINDS_PY, own + theirs + [os.path.dirname(n["DISPATCHER_CONFIG"])])
    if got is None:
        return sv.gate("files", UNKNOWN, "where the files resolve could not be read as %s" % n["ROLE_ACCOUNT"])
    mine = [got[p]["real"] for p in own]
    clash = sorted(p for p in theirs if any(under(got[p]["real"], m) or under(m, got[p]["real"]) for m in mine))
    if copies and not under(got[os.path.dirname(copies)]["real"], got[os.path.dirname(n["DISPATCHER_CONFIG"])]["real"]):
        clash.append("STATE_BACKUP_COPIES's folder, which is outside the old dispatcher's folder")
    if clash:
        return sv.gate("files", BLOCKED, "these would remove the runner's or the kit's own files: %s" % ", ".join(clash))
    sv.gate("files", OK, "none of the files it removes is the runner's or the kit's own")


def gate_runner(io, n, sv, rdir):
    """The runner is running, and the session writer's key does not depend on the old dispatcher after this."""
    r, acct = n["runner"], n["ROLE_ACCOUNT"]
    if job_running(io, r["JOB_LABEL"]) is not True:
        return sv.gate("runner", BLOCKED, "the runner's job %s is not running: its installer's `check` says why"
                       % r["JOB_LABEL"])
    sv.gate("runner", OK, "its job %s is running" % r["JOB_LABEL"])
    settings = sv.facts.get("settings")
    if settings is None:
        return sv.gate("writer", UNKNOWN, "the runner's settings could not be read")
    src = settings.get("key_source") or {"kind": "file"}
    kind, conf_kind = src.get("kind") or "file", r.get("WRITER_KEY_SOURCE") or ""
    status = role_py(io, acct, JSON_PY, [rdir + "/store/" + wkey.STATUS_FILE, "doc"])
    doc = status.get("doc") if status and status.get("state") == "read" else None
    if doc is not None and wkey.status_problems(doc):
        doc = None
    sv.facts["status"] = doc
    if kind == "file":
        if conf_kind == "other-program":
            return sv.gate("writer", BLOCKED, "runner.conf says WRITER_KEY_SOURCE=other-program, but the runner reads "
                                              "its own key file: the next `apply` would point it at a file this "
                                              "removes. Set the conf to what is installed first")
        sv.handover = "file"
        return sv.gate("writer", OK, "the session writer's key is the runner's own file, so nothing hands over")
    if kind == "renew":
        if conf_kind not in ("", "renew"):
            return sv.gate("writer", BLOCKED, "the runner renews the key itself, but runner.conf says %s: the next "
                                              "`apply` would undo that" % conf_kind)
        renewed = doc and (doc.get("renewal") or {}).get("result") == "renewed" and doc["state"] == "ok"
        if renewed and doc.get("check", {}).get("result") == "writer":
            sv.handover = "done"
            return sv.gate("writer", OK, "the runner renews the session writer's key itself, and it answered as "
                                         "the session writer at %s" % doc.get("answered_at"))
        reason = (doc or {}).get("reason")
        if reason in HARD_REASONS:
            return sv.gate("writer", BLOCKED, "the runner renews the key, and says: %s" % wkey.REASONS[reason])
        if sv.facts["dispatcher"] is not False:
            return sv.gate("writer", BLOCKED, "runner and old dispatcher would both renew the key: the runner is set "
                                              "to renew and has not yet, and the old dispatcher still runs")
        sv.handover = "wait"
        return sv.gate("writer", OK, "the old dispatcher is stopped and the runner is set to renew: `run` waits "
                                     "for its first renewal")
    return gate_handover(io, n, sv, src, conf_kind)


def gate_handover(io, n, sv, src, conf_kind):
    """The runner reads the key from another program's file today: that file must be the old dispatcher's,
    and everything the runner's renewal needs must be in place before the old dispatcher stops."""
    r, acct, problems = n["runner"], n["ROLE_ACCOUNT"], []
    if conf_kind not in ("other-program", "renew"):
        problems.append("runner.conf does not say WRITER_KEY_SOURCE=other-program, as installed: set it, so the "
                        "switch is that one line")
    if not r.get("WRITER_RENEWAL_FROM_FIELD"):
        problems.append("runner.conf has no WRITER_RENEWAL_FROM_FIELD: the JSON key in the old dispatcher's settings "
                        "that holds its renewal token")
    look = [src.get("file") or "", r.get("WRITER_KEY_FROM") or "", n["DISPATCHER_CONFIG"], r["WRITER_CLIENT_FILE"],
            r["WRITER_RENEWAL_FILE"]]
    got = role_py(io, acct, KINDS_PY, [p for p in look if p])
    if got is None:
        return sv.gate("writer", UNKNOWN, "the writer's key files could not be looked at as %s" % acct)
    real = lambda p: (got.get(p) or {}).get("real")       # noqa: E731
    if not (real(src.get("file")) == real(r.get("WRITER_KEY_FROM")) == real(n["DISPATCHER_CONFIG"])):
        problems.append("the runner reads the key from %s, and runner.conf's WRITER_KEY_FROM and the old dispatcher's "
                        "settings %s are not all that one file" % (src.get("file"), n["DISPATCHER_CONFIG"]))
    client = got.get(r["WRITER_CLIENT_FILE"]) or {}
    if client.get("kind") != "file" or client.get("mode", 0) & 0o077 or not client.get("size"):
        problems.append("the app's client details are not in %s (mode 0600, the account's own): run `python3 "
                        "scripts/pipeline_runner_setup.py writer-client` first" % r["WRITER_CLIENT_FILE"])
    if (got.get(r["WRITER_RENEWAL_FILE"]) or {}).get("kind") != "absent":
        problems.append("%s is there already, so the runner would renew from it and not take the old dispatcher's "
                        "renewal token: move it aside if it is from an earlier try" % r["WRITER_RENEWAL_FILE"])
    if r.get("WRITER_RENEWAL_FROM_FIELD"):
        f = role_py(io, acct, FIELD_PY, [n["DISPATCHER_CONFIG"], r["WRITER_RENEWAL_FROM_FIELD"], wkey.RENEWAL_RE.pattern])
        if f is None or f.get("state") != "read":
            return sv.gate("writer", UNKNOWN, "the old dispatcher's settings could not be read for its renewal token's key")
        if not (f["places"] and f["same"] and f["shaped"]):
            problems.append("the old dispatcher's settings hold no one renewal token under %s (%d place(s)%s)" % (
                r["WRITER_RENEWAL_FROM_FIELD"], f["places"], "" if f["same"] else ", not all the same"))
    undo = folder_undoes(io)
    if undo:
        problems.append(undo)
    if problems:
        return sv.gate("writer", BLOCKED, "; ".join(problems))
    sv.handover = "switch" if conf_kind == "other-program" else "apply"
    sv.gate("writer", OK, "the runner reads the key from the old dispatcher's settings today; `run` stops the old "
                          "dispatcher, then switches the runner to renew it, from %s under %s" % (
                              n["DISPATCHER_CONFIG"], r["WRITER_RENEWAL_FROM_FIELD"]))


def folder_undoes(io):
    """Why the settings folder would set runner.conf's WRITER_KEY_SOURCE back on its next `write`, or None."""
    sdir, _source = ps.settings_dir_of(None, io.env())
    if not sdir:
        return None
    try:
        state, folder, _problems, _notes = ps.load(sdir, ps.Kit(KIT), os.getuid())
    except (OSError, ValueError):
        return "the settings folder %s could not be read, so whether it would undo the switch is not known" % sdir
    if state == "read" and folder is not None and "WRITER_KEY_SOURCE" in folder.parts.get(RUNNER, {}):
        return ("the settings folder sets runner.conf's WRITER_KEY_SOURCE, so its next `write` would set it back: take "
                "that line out of machine.conf's [runner.conf] section first")
    return None


# --------------------------------------------------------------------------- the pieces
def piece(sv, pid, what, state, detail, **act):
    sv.pieces.append(dict(id=pid, what=what, state=state, detail=detail, **act))


def files_state(rows, paths):
    kinds = [(rows.get(p) or {}).get("kind") for p in paths]
    if any(k in (None, "unread") for k in kinds):
        return UNKNOWN
    return PRESENT if any(k != "absent" for k in kinds) else GONE


def job_piece(io, sv, pid, what, label, root_files=(), role_files=(), before=()):
    """A system job: launchd's hold on it, its job file, and the files that go with it."""
    held = job_held(io, label)
    try:
        has_plist = os.path.lexists(plist_path(label))
    except OSError:
        has_plist = None
    rows = {}
    if role_files:
        rows.update(role_py(io, sv.facts_account, KINDS_PY, list(role_files)) or {})
    if root_files:
        rows.update(root_py(io, KINDS_PY, list(root_files)) or {})
    fstate = files_state(rows, list(role_files) + list(root_files)) if (role_files or root_files) else GONE
    if held is None or has_plist is None or fstate == UNKNOWN:
        state = UNKNOWN
    else:
        state = PRESENT if held or has_plist or fstate == PRESENT else GONE
    words = "%s: %s; job file %s%s" % (label, "loaded" if held else "not loaded" if held is False else "launchd unread",
                                       plist_path(label), "" if has_plist else " (gone)")
    for p in list(root_files) + list(role_files):
        words += "; %s (%s)" % (p, (rows.get(p) or {}).get("kind", "unread"))
    piece(sv, pid, what, state, words, kind="job", label=label, root_files=list(root_files),
          role_files=list(role_files), before=list(before))


def pieces(io, n, sv):
    acct = sv.facts_account = n["ROLE_ACCOUNT"]
    job_piece(io, sv, "dispatcher", "the old dispatcher's job", n["DISPATCHER_SERVICE"])
    if opt(n, "STATE_BACKUP_SERVICE"):
        job_piece(io, sv, "backup-job", "its state backup job", n["STATE_BACKUP_SERVICE"])
    for label in other_services(n):
        job_piece(io, sv, "job:" + label, "a job only the old dispatcher needs", label)
    if opt(n, "FRONT_DOOR_SERVICE"):
        job_piece(io, sv, "front-door", "the front door: its job and its settings file", n["FRONT_DOOR_SERVICE"],
                  role_files=[p for p in (opt(n, "FRONT_DOOR_CONFIG"),) if p])
    if opt(n, "TUNNEL_SERVICE"):
        token = opt(n, "TUNNEL_TOKEN_FILE")
        job_piece(io, sv, "tunnel", "the tunnel: its job and its token file", n["TUNNEL_SERVICE"],
                  root_files=[token] if token else [])
        tunnel_token_rule(io, n, sv)
    pf_piece(io, sv)
    pkg = package_state(io, n)
    piece(sv, "package", "the old dispatcher's package", {"present": PRESENT, "absent": GONE}.get(pkg, UNKNOWN),
          "%s, in %s's global npm folder (%s)" % (n["DISPATCHER_PACKAGE"], acct, pkg or "unread"), kind="package")
    markers = [MARKER_DIR + "/" + acct]
    home = sv.facts.get("home")
    if home:
        markers.append(OLD_MARKER_DIR.replace("~", home, 1) + "/" + acct)
    rows = root_py(io, KINDS_PY, markers) or {}
    piece(sv, "marker", "the lane marker", files_state(rows, markers) if home else UNKNOWN,
          "; ".join("%s (%s)" % (p, (rows.get(p) or {}).get("kind", "unread")) for p in markers), kind="root-files",
          paths=markers)
    secrets_piece(io, n, sv)


def tunnel_token_rule(io, n, sv):
    """The tunnel's token file is removed as root, so it must be the file the tunnel's own job names, and
    root's alone. Anything else is not proven the token, and stays."""
    token, p = opt(n, "TUNNEL_TOKEN_FILE"), sv.pieces[-1]
    if not token or p["state"] != PRESENT:
        return
    rows = root_py(io, KINDS_PY, [token]) or {}
    row = rows.get(token) or {}
    if row.get("kind") == "absent":
        return
    try:
        strings = plist_strings(io, n["TUNNEL_SERVICE"])
    except (OSError, ValueError):
        strings = None
    why = None
    if strings is None:
        why = "the tunnel's job file could not be read, so %s is not proven its token: remove it by hand" % token
    elif not names_file(strings, token):
        why = "the tunnel's job file does not name %s, so it is not proven the token: check TUNNEL_TOKEN_FILE" % token
    elif row.get("kind") != "file" or row.get("uid") != ROOT_UID or row.get("mode", 0) & 0o077:
        why = "%s is not a file of root's alone (mode 600), as the tunnel's token is" % token
    if why:
        p["state"], p["detail"] = BLOCKED, why


def pf_piece(io, sv):
    held = job_held(io, PF_LABEL)
    rules = pf_rules(io)
    rows = root_py(io, KINDS_PY, [plist_path(PF_LABEL), PF_RULES, PF_LOG]) or {}
    fstate = files_state(rows, [plist_path(PF_LABEL), PF_RULES, PF_LOG])
    if held is None or rules is None or fstate == UNKNOWN:
        state = UNKNOWN
    else:
        state = PRESENT if held or rules or fstate == PRESENT else GONE
    piece(sv, "firewall", "the firewall rule and its boot job", state,
          "anchor %s (%s); boot job %s (%s); %s" % (PF_ANCHOR, "unread" if rules is None else "%d rule(s)" % len(rules),
                                                    PF_LABEL, "loaded" if held else "not loaded",
                                                    ", ".join("%s (%s)" % (k, (rows.get(k) or {}).get("kind", "unread"))
                                                              for k in (PF_RULES, PF_LOG))),
          kind="pf")


def secrets_piece(io, n, sv):
    """The tracker application's secrets and stored tokens, with their backup copies."""
    acct, paths = n["ROLE_ACCOUNT"], secret_paths(n)
    rows = role_py(io, acct, KINDS_PY, paths) or {}
    matches = [(BACKUPS_DIR, os.path.basename(n["DISPATCHER_CONFIG"]) + ".")]
    copies = opt(n, "STATE_BACKUP_COPIES")
    if copies:
        matches.append((os.path.dirname(copies), os.path.basename(copies)))
    found, unread = [], False
    for folder, prefix in matches:
        got = role_py(io, acct, MATCHES_PY, [folder, prefix])
        if got is None or got.get("state") == "unread":
            unread = True
            continue
        found += [folder.rstrip("/") + "/" + name for name in got["names"]]
    names = role_py(io, acct, NAMES_PY, [n["DISPATCHER_ENV_FILE"]])
    sv.facts["env_names"] = names.get("names") if names else None
    state = files_state(rows, paths)
    if unread:
        state = UNKNOWN
    elif state == GONE and found:
        state = PRESENT
    words = "; ".join("%s (%s)" % (p, (rows.get(p) or {}).get("kind", "unread")) for p in paths)
    words += "; %d backup cop%s" % (len(found), "y" if len(found) == 1 else "ies")
    piece(sv, "secrets", "the tracker application's secrets and stored tokens, with their backup copies", state, words,
          kind="role-files", paths=paths + found)


# --------------------------------------------------------------------------- saying it
def say_survey(io, sv):
    io.say("THE GATES")
    for gid, state, words in sv.gates:
        io.say("  %-9s %-9s %s" % (gid, state, words))
    io.say("THE PIECES")
    for p in sv.pieces:
        io.say("  %-12s %-9s %s: %s" % (p["id"][:12], p["state"], p["what"], p["detail"]))


def show_plan(io, n, sv):
    """Exactly what `run` does next, in its order, before the one question."""
    r, label = n["runner"], n["DISPATCHER_SERVICE"]
    io.say("")
    io.say("WHAT `run` DOES NOW, IN THIS ORDER. One yes covers all of it.")
    io.say("  1. Stop the old dispatcher, %s: disable it, so no restart brings it back, and boot it out. It renews "
           "the session writer's key no more. Its port, %s, must then be closed." % (label, n["DISPATCHER_PORT"]))
    how = {"switch": "In %s, WRITER_KEY_SOURCE goes from other-program to renew: that one line, every other byte kept. "
                     "Then the runner's installer applies it, its answer through a pipe. The runner's next pass takes "
                     "the renewal token from %s, under %s, once, and renews the key." % (
                         n["paths"][RUNNER], n["DISPATCHER_CONFIG"], r.get("WRITER_RENEWAL_FROM_FIELD")),
           "apply": "runner.conf already says renew: the runner's installer applies it, its answer through a pipe, and "
                    "the runner's next pass takes the renewal token from %s, once." % n["DISPATCHER_CONFIG"],
           "wait": "The runner is already set to renew the key: nothing to switch.",
           "done": "The runner already renews the key itself: nothing to hand over.",
           "file": "The runner reads the key from its own file: nothing to hand over."}[sv.handover]
    io.say("  2. %s" % how)
    io.say("  3. %s Then the runner's own `check` must say the same." % (
        "Wait, up to %d minutes, for the runner to say it renewed the key and the key answered as the session writer."
        % (HANDOVER_WAIT // 60) if sv.handover in ("switch", "apply", "wait") else "No wait."))
    io.say("     If 1 to 3 fail, nothing below is touched, and the end says how to start the old dispatcher again.")
    io.say("  4. Then remove, each one read back, stopping at the first that fails:")
    for p in sv.pieces:
        if p["state"] == PRESENT:
            io.say("     - %s: %s" % (p["what"], p["detail"]))
    io.say("WHAT STAYS:")
    io.say("  - the old dispatcher's folder, %s, with its logs and work folders: the jobs that still read its logs need "
           "them. Remove it by hand once nothing does." % os.path.dirname(n["DISPATCHER_CONFIG"]))
    io.say("  - the tracker application itself, and the tracker key the review jobs use for tickets.")
    if n.get("FRONT_DOOR_BIN") or opt(n, "FRONT_DOOR_SERVICE"):
        io.say("  - the front door's and the tunnel's programs: remove them where you installed them, if nothing "
               "else of yours uses them.")
    io.say("AFTER THIS: the review installer's first step still asks for a running old dispatcher and its settings "
           "file, so its `verify` and `run` stop there until that step changes.")


def outside_steps(io, n, sv):
    names = sv.facts.get("env_names")
    io.say("")
    io.say("OUTSIDE THIS MAC, BY HAND. Nothing here does these.")
    io.say("  1. Delete the tunnel, on %s. Its token ends with it.%s" % (
        opt(n, "TUNNEL_PAGE") or "its provider's own page",
        " Then take the public name %s out of its domain's records, and let the domain lapse at its registrar if "
        "nothing else uses it." % n["FRONT_DOOR_HOST"] if n.get("FRONT_DOOR_HOST") else ""))
    io.say("  2. Turn the tracker application's webhook off, on the application's own page: %s. Keep the application "
           "itself: the session writer is its user, and the runner renews that user's key with its client details."
           % TRACKER_APPS_PAGE)
    io.say("  3. The keys nothing uses now. Rotate or revoke each one by hand, on its own page:")
    if names:
        io.say("     - The old dispatcher's env file held these names: %s. Each one nothing else uses can go where it "
               "was made (a code host token: %s). Keep the application's client id and secret: the runner renews "
               "with them." % (", ".join(names), CODE_HOST_TOKENS_PAGE))
    io.say("     - A key you made for the tunnel's provider, if you made one.")
    io.say("     - Never revoke the tracker application's authorization or its tokens: that is the session writer's "
           "key, which the runner renews now. The copies removed here ended when the runner renewed (a renewal "
           "token works once), and the old key ends within a day.")
    io.say("  4. Close the kit's own tickets that close with the old dispatcher: docs/DISPATCHER-REMOVAL.md, "
           "\"Tickets that close\".")


# --------------------------------------------------------------------------- doing it
class Stop(Exception):
    """A step that did not complete. Its words say what stands, and nothing after it is tried."""


def wait_gone(io, label):
    for _n in range(GONE_WAIT // GONE_POLL + 1):
        held = job_held(io, label)
        if held is False:
            return True
        if held is None:
            return False
        io.sleep(GONE_POLL)
    return False


def results(got, paths):
    """The paths a removal did not take: anything but removed or absent."""
    return [p for p in paths if (got or {}).get(p) not in ("removed", "absent")]


def kinds_gone(io, acct, paths, root=False):
    if not paths:
        return []
    rows = (root_py(io, KINDS_PY, paths) if root else role_py(io, acct, KINDS_PY, paths)) or {}
    return [p for p in paths if (rows.get(p) or {}).get("kind") != "absent"]


def sessions_busy(io, n, sv):
    """A fresh look: the old dispatcher's processes in its work folders. None when it could not be listed."""
    cwds = role_cwds(io, n["ROLE_ACCOUNT"])
    if cwds is None:
        return None
    home = sv.facts.get("home") or ""
    real = [d.replace("~", home, 1) if d.startswith("~/") and home else d for d in sv.facts.get("workspaces") or []]
    return sorted(set(c for c in cwds for d in real if under(c, d)))


def step_stop(io, n, sv):
    label = n["DISPATCHER_SERVICE"]
    held = job_held(io, label)
    if held is None:
        raise Stop("launchd could not be asked about %s. Nothing was changed." % label)
    busy = sessions_busy(io, n, sv)
    if busy is None or busy:
        raise Stop("a session of the old dispatcher %s. Nothing was changed." % (
            "may still be working (its processes could not be listed)" if busy is None else "is working in " +
            ", ".join(busy)))
    if not as_root(io, ["/bin/launchctl", "disable", "system/" + label]).ok:
        raise Stop("launchd would not disable %s. Nothing was changed." % label)
    if held:
        as_root(io, ["/bin/launchctl", "bootout", "system/" + label], timeout=GONE_WAIT + 30)
        if not wait_gone(io, label):
            raise Stop("launchd still holds %s after %d seconds, or could not be asked. It is disabled, so a restart "
                       "will not start it." % (label, GONE_WAIT))
    port = port_state(io, int(n["DISPATCHER_PORT"]))
    if port != "closed":
        raise Stop("the old dispatcher is stopped, but its port %s is %s: something else may listen there"
                   % (n["DISPATCHER_PORT"], "open" if port == "open" else "not readable"))
    return "stopped and disabled; its port %s is closed" % n["DISPATCHER_PORT"]


def settings_kind(io, n):
    got = role_py(io, n["ROLE_ACCOUNT"], JSON_PY, [n["runner"]["RUNNER_DIR"].rstrip("/") + "/runner.json", "settings"])
    src = ((got or {}).get("doc") or {}).get("key_source") if got and got.get("state") == "read" else None
    return (src or {}).get("kind") or ("file" if got and got.get("state") == "read" else None)


def step_switch(io, n, sv, box):
    """runner.conf's one line, then the runner's installer. A conf the runner's own loader refuses afterwards
    is put back as it was."""
    path = n["paths"][RUNNER]
    if sv.handover == "switch":
        try:
            text = io.read_text(path)
            mode = stat.S_IMODE(os.lstat(path).st_mode)
        except (OSError, ValueError) as exc:
            raise Stop("%s could not be read again (%s)" % (path, type(exc).__name__))
        if ps.conf_values(text).get("WRITER_KEY_SOURCE") != "other-program":
            raise Stop("%s changed after it was read: it no longer says WRITER_KEY_SOURCE=other-program" % path)
        try:
            io.put_file(path, ps.set_line(text, "WRITER_KEY_SOURCE", "renew"), mode)
            conf, errors = rs.load_conf(path, None)
        except (OSError, rs.SetupError) as exc:
            conf, errors = None, [str(exc)]
        if errors or conf.get("WRITER_KEY_SOURCE") != "renew":
            try:
                io.put_file(path, text, mode)
            except OSError:
                raise Stop("%s could not be written whole, and could not be put back: set WRITER_KEY_SOURCE="
                           "other-program in it by hand" % path)
            raise Stop("the runner's own loader refused %s with WRITER_KEY_SOURCE=renew (%s), so it was put back as "
                       "it was" % (path, (errors or ["it did not read back"])[0]))
        box["conf_changed"] = (path, text, mode)
    box["switched_at"] = io.now()
    rc, doc = io.installer(["apply", "--conf", path, "--watch-conf", n["paths"][UPDATE]], answers=ANSWERS)
    rows = dict((r.get("step"), r) for r in ((doc or {}).get("rows") or []) if isinstance(r, dict))
    applied = (rows.get("settings") or {}).get("outcome") in (se.DONE, se.ALREADY_DONE)
    if not applied or settings_kind(io, n) != "renew":
        if box.get("conf_changed"):
            try:
                io.put_file(path, box["conf_changed"][1], box["conf_changed"][2])
                box.pop("conf_changed")
            except OSError:
                pass
        row = rows.get("settings") or {}
        raise Stop("the runner's installer did not install the renew source (its exit %s; its settings row: %s)%s" % (
            rc, "%s, %s" % (row.get("outcome"), row.get("detail")) if row else "none",
            "; runner.conf was put back as it was" if sv.handover == "switch" and not box.get("conf_changed") else ""))
    return "the runner's installer applied WRITER_KEY_SOURCE=renew (its exit %d)" % rc


def step_wait(io, n, sv, box):
    """The runner's own word: it renewed the key, and the key answered as the session writer."""
    since = box.get("switched_at", 0) - SLACK if sv.handover in ("switch", "apply") else 0
    path = n["runner"]["RUNNER_DIR"].rstrip("/") + "/store/" + wkey.STATUS_FILE
    deadline = io.now() + HANDOVER_WAIT
    while sv.handover in ("switch", "apply", "wait"):
        got = role_py(io, n["ROLE_ACCOUNT"], JSON_PY, [path, "doc"])
        doc = got.get("doc") if got and got.get("state") == "read" else None
        if doc is not None and not wkey.status_problems(doc):
            box["status"] = doc
            fresh = lambda part: (wkey.epoch((doc.get(part) or {}).get("at")) or 0) >= since    # noqa: E731
            if doc["source"] == "renew" and fresh("renewal") and fresh("check") and doc["state"] == "ok" and \
                    doc["renewal"]["result"] == "renewed" and doc["check"]["result"] == "writer":
                break
            if doc["source"] == "renew" and doc["reason"] in HARD_REASONS and (wkey.epoch(doc["at"]) or 0) >= since:
                raise Stop("the runner says: %s" % wkey.REASONS[doc["reason"]])
        if io.now() >= deadline:
            raise Stop("the runner has not said it renewed the key and the key answered as the session writer, "
                       "within %d minutes%s" % (HANDOVER_WAIT // 60, "" if doc is None else " (it says %s: %s)" % (
                           doc["state"], wkey.REASONS.get(doc["reason"], "no reason given"))))
        io.sleep(HANDOVER_POLL)
    rc, doc = io.installer(["check", "--conf", n["paths"][RUNNER], "--watch-conf", n["paths"][UPDATE]])
    row = next((r for r in ((doc or {}).get("rows") or []) if isinstance(r, dict) and r.get("step") == "writer-key"),
               None)
    if not row or row.get("outcome") != se.ALREADY_DONE:
        raise Stop("the runner's own check does not say the session writer's key is working (%s)" % (
            "its writer-key row: %s, %s" % (row.get("outcome"), row.get("detail")) if row else
            "it gave no writer-key row; exit %s" % rc))
    return "the runner's check says: %s" % row.get("detail")


def remove_job(io, n, sv, p):
    label, acct = p["label"], n["ROLE_ACCOUNT"]
    held = job_held(io, label)
    if held is None:
        raise Stop("launchd could not be asked about %s" % label)
    if held:
        as_root(io, ["/bin/launchctl", "bootout", "system/" + label], timeout=GONE_WAIT + 30)
        if not wait_gone(io, label):
            raise Stop("launchd still holds %s after %d seconds" % (label, GONE_WAIT))
    left = []
    if p["root_files"]:
        left += results(root_py(io, REMOVE_PY, p["root_files"]), p["root_files"])
    if p["role_files"]:
        left += results(role_py(io, acct, REMOVE_PY, p["role_files"]), p["role_files"])
    if left:
        raise Stop("%s is stopped, but these could not be removed: %s" % (label, ", ".join(left)))
    left = results(root_py(io, REMOVE_PY, [plist_path(label)]), [plist_path(label)])
    if left:
        raise Stop("%s is stopped, but its job file %s could not be removed" % (label, plist_path(label)))
    gone = (job_held(io, label) is False and not os.path.lexists(plist_path(label))
            and not kinds_gone(io, acct, p["root_files"], root=True) and not kinds_gone(io, acct, p["role_files"]))
    if not gone:
        raise Stop("NOT VERIFIED: %s, its job file or its files still read as there" % label)
    return "gone: %s and its job file%s" % (label, "".join(", " + f for f in p["root_files"] + p["role_files"]))


def remove_pf(io, n, sv, p):
    if port_state(io, int(n["DISPATCHER_PORT"])) != "closed":
        raise Stop("the old dispatcher's port %s is not closed, so its firewall rule stays" % n["DISPATCHER_PORT"])
    held = job_held(io, PF_LABEL)
    if held is None:
        raise Stop("launchd could not be asked about %s" % PF_LABEL)
    if held:
        as_root(io, ["/bin/launchctl", "bootout", "system/" + PF_LABEL])
        if not wait_gone(io, PF_LABEL):
            raise Stop("launchd still holds %s" % PF_LABEL)
    if not as_root(io, ["/sbin/pfctl", "-a", PF_ANCHOR, "-F", "rules"]).ok:
        raise Stop("pfctl would not empty the anchor %s" % PF_ANCHOR)
    files = [plist_path(PF_LABEL), PF_RULES, PF_LOG]
    got = root_py(io, REMOVE_PY, files + [PF_RULES_DIR + "/"])
    left = results(got, files)
    if left:
        raise Stop("the anchor is empty, but these could not be removed: %s" % ", ".join(left))
    if pf_rules(io) != [] or job_held(io, PF_LABEL) is not False or kinds_gone(io, None, files, root=True):
        raise Stop("NOT VERIFIED: the anchor %s, its boot job or its files still read as there" % PF_ANCHOR)
    kept = (got or {}).get(PF_RULES_DIR + "/") == "kept: not empty"
    return "gone: the anchor %s is empty, and its boot job and files are removed%s" % (
        PF_ANCHOR, "; %s holds other files, so it stays" % PF_RULES_DIR if kept else "")


def remove_package(io, n, sv, p):
    pkg = n["DISPATCHER_PACKAGE"]
    res = as_role(io, n["ROLE_ACCOUNT"], pu._role(n, "npm uninstall -g %s" % shlex.quote(pkg)), timeout=900)
    if package_state(io, n) != "absent":
        raise Stop("npm uninstall -g %s did not remove it (exit %d), or it could not be read back" % (pkg, res.rc))
    return "gone: %s, from %s's global npm folder" % (pkg, n["ROLE_ACCOUNT"])


def remove_marker(io, n, sv, p):
    busy = sessions_busy(io, n, sv)
    if busy is None or busy:
        raise Stop("the lane marker stays: %s" % ("the old dispatcher's processes could not be listed" if busy is None
                                                   else "a process still works in " + ", ".join(busy)))
    left = results(root_py(io, REMOVE_PY, p["paths"]), p["paths"])
    if left or kinds_gone(io, None, p["paths"], root=True):
        raise Stop("NOT VERIFIED: the lane marker still reads as there: %s" % ", ".join(left or p["paths"]))
    return "gone: %s" % ", ".join(p["paths"])


def remove_files(io, n, sv, p):
    acct = n["ROLE_ACCOUNT"]
    left = results(role_py(io, acct, REMOVE_PY, p["paths"]), p["paths"])
    if left or kinds_gone(io, acct, p["paths"]):
        raise Stop("NOT VERIFIED: these could not be removed, or still read as there: %s" % ", ".join(
            left or kinds_gone(io, acct, p["paths"])))
    return "gone: %d file(s), each read back as absent" % len(p["paths"])


REMOVERS = {"job": remove_job, "pf": remove_pf, "package": remove_package, "root-files": remove_marker,
            "role-files": remove_files}


def restart_words(io, n, box):
    label, path = n["DISPATCHER_SERVICE"], n["paths"][RUNNER]
    doc = box.get("status") or {}
    renewed = (doc.get("source") == "renew" and (doc.get("renewal") or {}).get("result") == "renewed"
               and (wkey.epoch((doc.get("renewal") or {}).get("at")) or 0) >= box.get("switched_at", 0) - SLACK)
    io.say("NOTHING WAS REMOVED. The old dispatcher's files, its job file and every host piece are as they were.")
    if renewed:
        io.say("Do NOT start the old dispatcher again: the runner has renewed the key, so the renewal token is the "
               "runner's now, and the old dispatcher's next renewal would be refused. Fix what the runner says, then "
               "run this again: it starts from where it stopped.")
        return
    io.say("The runner has not renewed the key, so the old dispatcher's own renewal token still works. Either fix what "
           "is said above and run this again (it starts from where it stopped), or start the old dispatcher again:")
    try:
        says = ps.conf_values(io.read_text(path)).get("WRITER_KEY_SOURCE")
    except (OSError, ValueError):
        says = None
    if box.get("conf_changed") or says != "other-program":
        io.say("    set WRITER_KEY_SOURCE=other-program in %s, then:" % path)
        io.say("    echo '%s' | python3 scripts/pipeline_runner_setup.py apply --conf %s" % (ANSWERS, path))
    io.say("    sudo launchctl enable system/%s" % label)
    io.say("    sudo launchctl bootstrap system %s" % plist_path(label))


def apply(io, n, sv):
    """Steps 1 to 4, in order. Returns the exit code, having said what was and was not done."""
    box, done, order = {}, [], [p for p in sv.pieces if p["state"] == PRESENT]
    steps = [("stop", "stop the old dispatcher", lambda: step_stop(io, n, sv))]
    if sv.handover in ("switch", "apply"):
        steps.append(("switch", "switch the runner to renew the key", lambda: step_switch(io, n, sv, box)))
    steps.append(("verify", "the runner answers as the session writer", lambda: step_wait(io, n, sv, box)))
    for sid, what, fn in steps:
        try:
            done.append((what, fn()))
            io.say("  DONE      %s: %s" % (what, done[-1][1]))
        except Stop as exc:
            io.say("  NOT DONE  %s: %s" % (what, exc))
            io.say("")
            restart_words(io, n, box)
            return EXIT_NOT
    for i, p in enumerate(order):
        try:
            words = REMOVERS[p["kind"]](io, n, sv, p)
            done.append((p["what"], words))
            io.say("  DONE      %s: %s" % (p["what"], words))
        except Stop as exc:
            io.say("  NOT DONE  %s: %s" % (p["what"], exc))
            for q in order[i + 1:]:
                io.say("  NOT TRIED %s" % q["what"])
            io.say("")
            io.say("STOPPED. What is DONE above is gone, and read back as gone; nothing after the first NOT DONE was "
                   "touched. Fix what it says, and run this again: it starts from what is left.")
            return EXIT_NOT
    io.say("")
    io.say("DONE: the old dispatcher and its host pieces are gone from this Mac, each read back as gone.")
    outside_steps(io, n, sv)
    return EXIT_OK


# --------------------------------------------------------------------------- the commands
def cmd(io, mode, conf_dir):
    why = refused(io)
    if why:
        io.say(why)
        return EXIT_REFUSED
    paths, source, why = part_paths(conf_dir, io.env())
    if why:
        io.say("Nothing was read or changed: %s." % why)
        return EXIT_USAGE
    n, errors = load_names(io, paths)
    if errors:
        io.say("The settings have %d problem(s), all of them (the part files from %s):" % (len(errors), source))
        for e in errors:
            io.say("  - %s" % e)
        io.say("Nothing was read or changed.")
        return EXIT_USAGE
    try:
        io.sudo("it reads the old dispatcher's jobs and files as root and as %s%s." % (
            n["ROLE_ACCOUNT"], "" if mode == "check" else ", and then stops and removes them"))
    except se.NoPrivilege as exc:
        io.say(str(exc).splitlines()[0])
        return EXIT_NOPRIV
    sv = survey(io, n)
    say_survey(io, sv)
    answer = sv.answer()
    io.say("ANSWER: %s" % answer)
    if answer == READY:
        show_plan(io, n, sv)
    if answer == NOTHING:
        outside_steps(io, n, sv)
    if mode == "check" or answer != READY:
        if mode == "run":
            io.say("Nothing changed.")
        return ANSWER_EXIT[answer]
    if io.ask("Type yes to go on: ").strip() != "yes":
        io.say("You did not type yes. Nothing changed.")
        return EXIT_NOT_YES
    again = survey(io, n)
    if again.answer() != READY or [p["id"] for p in again.pieces if p["state"] == PRESENT] != \
            [p["id"] for p in sv.pieces if p["state"] == PRESENT] or again.handover != sv.handover:
        say_survey(io, again)
        io.say("Nothing changed: what is there changed while the question waited. Run it again to see it as it is now.")
        return EXIT_UNKNOWN
    return apply(io, n, again)


def main(argv=None, io=None):
    ap = argparse.ArgumentParser(description="The old dispatcher leaves this Mac (KIT-646).")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("cmd", nargs="?", choices=("check", "run"))
    ap.add_argument("--conf-dir", help="the folder the part files and %s are in" % CONF_NAME)
    args = ap.parse_args(argv)
    if args.selftest:
        from scratch_run import contained
        return contained(selftest, "dispatcher-removal-selftest-")
    if not args.cmd:
        ap.print_help()
        return EXIT_USAGE
    return cmd(io or IO(), args.cmd, args.conf_dir)


# ======================================================================================
# SELFTEST: nothing below this line runs outside --selftest.
# ======================================================================================
ACCT, DISP, FRONT, TUNNEL = "_exdispatch", "com.acme.dispatcher", "com.acme.front-door", "com.acme.tunnel"
BACKUP_JOB, CANARY, RUNNER_JOB = "com.acme.dispatcher-backup", "com.acme.canary", "local.pipeline-runner"
ACCESS, REFRESH = "accesskey" + "A" * 31, "renewaltoken" + "R" * 30     # built here, so no file holds one
ENV_VALUE = "envvalue" + "E" * 32
ENV_NAME = "." + "env"                    # the old dispatcher's env file, as its hosts name it


def _write(path, text, mode=0o600):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(path, mode)


def _status(source, now, renewal=None, check="writer", state="ok", reason=None):
    iso = wkey.iso
    return {"schema": wkey.STATUS_SCHEMA, "at": iso(now), "source": source, "writer_user_id": "writer-1",
            "state": state, "reason": reason, "detail": None,
            "check": {"at": iso(now), "result": check} if check else None,
            "answered_at": iso(now) if check == "writer" else None,
            "writer_name": "Writer" if check == "writer" else None,
            "renewal": {"at": iso(now), "result": renewal} if renewal else None,
            "renewed_at": iso(now) if renewal == "renewed" else None,
            "ends_at": iso(now + 86400) if renewal == "renewed" else None}


class _World(object):
    """One Mac with the old dispatcher on it, in a folder. Files are real; launchd, pfctl, lsof, npm and the
    runner's installer are data. The pipeline account's programs run for real, with its home in the folder."""

    def __init__(self, root):
        self.root, self.home, self.disp, self.kit = root, root + "/home", root + "/opt/disp", root + "/kit"
        self.clock, self.uid, self.answer, self.no_sudo, self.no_role = 1795000000.0, 501, "yes", False, False
        self.env = {"PATH": "/usr/bin:/bin", ps.DIR_ENV: root + "/settings"}
        self.jobs = dict((k, "running") for k in (DISP, BACKUP_JOB, FRONT, TUNNEL, CANARY, RUNNER_JOB))
        self.jobs[PF_LABEL] = "not running"
        self.stuck, self.disabled, self.log = set(), set(), []
        self.cwds = ["/", root + "/home/.pipeline-runner/work/x"]
        self.pf = ["block return in quick on ! lo0 inet proto tcp from any to any port = 3456",
                   "block return in quick on ! lo0 inet6 proto tcp from any to any port = 3456"]
        self.package, self.npm_keeps, self.port_open, self.renewal = True, False, False, "renewed"
        self.apply_fails, self.renewed_once, self.renewed_while_held, self.on_ask = False, False, False, None
        self.runner_json = self.home + "/.pipeline-runner/runner.json"
        self.status_json = self.home + "/.pipeline-runner/store/" + wkey.STATUS_FILE

    def run(self, argv):
        assert argv[:2] == ["sudo", "-n"], "a sudo call without -n: %r" % (argv,)
        if argv[2] == "-u":
            assert argv[3] == ACCT and argv[4] == "-H" and argv[5:7] == ["/bin/sh", "-c"], argv
            if self.no_role:
                return Res(1, "", "sudo: a password is required")
            script = argv[7]
            assert script.startswith("cd / && "), script
            if "npm " in script:
                return self.npm(script)
            toks = shlex.split(script[len("cd / && "):])
            assert toks[:3] == ["/usr/bin/python3", "-I", "-c"], toks
            return self.py(toks[3], toks[4:], self.home)
        if argv[2] == "/usr/bin/python3":
            assert argv[3:5] == ["-I", "-c"], argv
            return self.py(argv[5], argv[6:], self.root + "/roothome")
        if argv[2] == "/bin/launchctl":
            return self.launchctl(argv[3], argv[4].split("/", 1)[1])
        if argv[2] == "/sbin/pfctl":
            assert argv[3:5] == ["-a", PF_ANCHOR], argv
            if argv[5:] == ["-F", "rules"]:
                self.pf = []
                self.log.append(("pf-flush",))
            return Res(0, "\n".join(self.pf) + "\n")
        if argv[2] == "/usr/sbin/lsof" and argv[3] == "-nP":
            assert argv[4:] == ["-iTCP:3456", "-sTCP:LISTEN", "-Fn"], argv
            return Res(0, "p7\nf9\nn*:3456\n") if self.port_open or DISP in self.jobs else Res(1)
        if argv[2] == "/usr/sbin/lsof":
            if self.cwds is None:
                return Res(1, "", "lsof: it could not list them")
            return Res(0 if self.cwds else 1, "".join("p%d\nfcwd\nn%s\n" % (i + 10, c) for i, c in enumerate(self.cwds)))
        raise AssertionError("a command the stand-in does not know: %r" % (argv,))

    def py(self, prog, args, home):
        for a in args:          # a stand-in that wrote outside its own folder would be the test's own defect
            if a.startswith("/") and not (a == self.root or a.startswith(self.root + "/")):
                raise AssertionError("a path outside the stand-in's folder: %s" % a)
        if prog == REMOVE_PY:
            self.log.append(("remove",) + tuple(args))
        p = subprocess.run([sys.executable, "-I", "-c", prog] + list(args), capture_output=True, text=True,
                           env={"HOME": home, "PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1"}, cwd="/", timeout=60)
        return Res(p.returncode, p.stdout, p.stderr)

    def launchctl(self, verb, label):
        if verb == "print":
            if label in self.jobs:
                return Res(0, "system/%s = {\n\tstate = %s\n}\n" % (label, self.jobs[label]))
            return Res(113, "", 'Could not find service "%s" in domain for system' % label)
        self.log.append((verb, label))
        if verb == "disable":
            self.disabled.add(label)
            return Res(0)
        assert verb == "bootout", verb
        if label in self.stuck:
            return Res(0)
        if label not in self.jobs:
            return Res(3, "", "Boot-out failed: 3: No such process")
        del self.jobs[label]
        return Res(0)

    def npm(self, script):
        if "npm root -g" in script:
            return Res(0, "present\n" if self.package else "absent\n")
        assert "npm uninstall -g disp-pkg" in script, script
        self.log.append(("npm-uninstall",))
        self.package = self.package and self.npm_keeps
        return Res(0)

    def installer(self, args, answers):
        self.log.append(("installer", args[0]))
        if args[0] == "apply":
            assert answers == ANSWERS, answers
            if self.apply_fails:
                return 1, {"rows": [{"step": "settings", "outcome": "FAILED", "detail": "a stand-in's refusal"}]}
            conf, errors = rs.load_conf(args[args.index("--conf") + 1], None)
            assert not errors, errors
            with open(self.runner_json) as fh:
                doc = json.load(fh)
            doc["session_tools"]["key_source"] = rs.writer_source(conf)
            _write(self.runner_json, json.dumps(doc))
            return 4, {"rows": [{"step": "settings", "outcome": "DONE", "detail": "written"},
                                {"step": "writer-key", "outcome": "UNKNOWN", "detail": "waits"}]}
        with open(self.status_json) as fh:
            problems, said = wkey.judge(json.load(fh), self.clock)
        return (1 if problems else 0), {"rows": [{"step": "writer-key", "outcome": "FAILED" if problems else
                                                  "ALREADY-DONE", "detail": "; ".join(problems) or said}]}

    def tick(self):
        """The runner's pass: its first renewal after the renew source is installed, as the case says."""
        with open(self.runner_json) as fh:
            src = json.load(fh)["session_tools"]["key_source"]
        if src.get("kind") != "renew" or self.renewed_once:
            return
        self.renewed_once = True
        if self.renewal == "silent":
            return
        self.renewed_while_held = self.renewed_while_held or DISP in self.jobs
        self.log.append(("renewal", self.renewal))
        doc = {"renewed": _status("renew", self.clock, renewal="renewed"),
               "client-refused": _status("renew", self.clock, renewal="client-refused", check=None, state="failed",
                                         reason="client-refused"),
               "other-user": _status("renew", self.clock, renewal="renewed", check="other-user", state="failed",
                                     reason="other-user")}[self.renewal]
        _write(self.status_json, json.dumps(doc))
        if self.renewal != "client-refused":
            _write(self.home + "/.pipeline-runner/writer-renewal-key", REFRESH[::-1])


class _FakeIO(IO):
    def __init__(self, w):
        self.w, self.said, self.asked = w, [], []

    def env(self):
        return dict(self.w.env)

    def uid(self):
        return self.w.uid

    def now(self):
        return self.w.clock

    def sleep(self, seconds):
        self.w.clock += seconds
        self.w.tick()

    def say(self, text):
        self.said.append(str(text))

    def ask(self, prompt):
        self.asked.append(prompt)
        if self.w.on_ask:
            self.w.on_ask(self.w)
        return self.w.answer

    def run(self, argv, stdin=None, timeout=120):
        self.w.log.append(("run", tuple(argv)))
        return self.w.run(list(argv))

    def sudo(self, why):
        if self.w.no_sudo:
            raise se.NoPrivilege("NO ADMINISTRATOR ACCESS: nothing was attempted, and nothing was changed.")

    def put_file(self, path, text, mode):
        self.w.log.append(("put", path))
        ps.put_file(path, text, mode)

    def installer(self, args, answers=None):
        return self.w.installer(args, answers)


def _git(path, url):
    env = {"PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1", "HOME": path}
    subprocess.run(["git", "init", "-q", path], check=True, env=env, capture_output=True)
    subprocess.run(["git", "-C", path, "remote", "add", "origin", url], check=True, env=env, capture_output=True)


def _build(tmp, name):
    """A whole old-dispatcher host in its own folder, every gate holding."""
    root = os.path.join(tmp, name)
    w = _World(root)
    d, h, k = w.disp, w.home, w.kit
    for c in ("app", "web"):
        _git(h + "/repos/" + c, "https://github.com/acme/%s.git" % c)
    entries = [{"id": c, "githubUrl": "https://github.com/acme/%s" % c, "repositoryPath": d + "/repos/" + c,
                "workspaceBaseDir": d + "/worktrees", "isActive": True} for c in ("app", "web")]
    _write(d + "/config.json", json.dumps({"repositories": entries,
                                           "linearWorkspaces": {"w1": {"linearToken": ACCESS, "refreshToken": REFRESH}}}))
    _write(d + "/" + ENV_NAME, "LINEAR_CLIENT_ID=%s\nLINEAR_CLIENT_SECRET=%s\nexport GH_TOKEN=%s\n" % ((ENV_VALUE,) * 3))
    _write(d + "/" + pu.TOKEN_STORE, json.dumps({"token": ENV_VALUE}))
    _write(d + "/Caddyfile", "@dispatcher path /webhook\n")
    for s in ("state.json", "state.json.bak-20261101T000000Z", "state.json.bak-20261102T000000Z"):
        _write(d + "/state/" + s, "{}")
    os.makedirs(d + "/worktrees")
    for s in ("config.json.20261101T000000Z", "config.json.20261102T000000Z", "operator-notes.txt"):
        _write(h + "/.stage-e/backups/" + s, "{}")
    _write(h + "/.stage-e/" + ENV_NAME[1:], "STAGE_E_LINEAR_API_KEY=%s\n" % ENV_VALUE)
    _write(h + "/.pipeline-runner/writer-client", "clientid1234:clientsecret5678\n")
    _write(w.runner_json, json.dumps({"enabled": True, "projects": dict((c, {"repo": h + "/repos/" + c})
                                                                        for c in ("app", "web")),
                                      "session_tools": {"key_source": {"kind": "other-program", "file": d + "/config.json",
                                                                       "field": "linearToken"}}}))
    _write(h + "/.pipeline-runner/store/" + store.LANES_FILE, json.dumps(
        {"schema": store.LANES_SCHEMA, "enabled": True, "work_dir": h + "/.pipeline-runner/work", "look_config_dir": None,
         "projects": {"app": list(store.KINDS), "web": list(store.KINDS)}}))
    _write(w.status_json, json.dumps(_status("other-program", w.clock)))
    _write(h + "/.claude/pipeline/pins/dispatched-lane/" + ACCT, "", 0o444)
    _write(root + "/etc/claude-pipeline/dispatched-lane/" + ACCT, "", 0o444)
    _write(root + "/etc/tunnel/token", "tunneltoken\n")
    _write(root + "/pf/pf.rules", "\n".join(w.pf) + "\n", 0o644)
    _write(root + "/log/pf.log", "", 0o644)
    os.makedirs(root + "/LaunchDaemons")
    for label in (DISP, BACKUP_JOB, FRONT, TUNNEL, CANARY, PF_LABEL):
        body = {"Label": label, "RunAtLoad": True}
        if label == TUNNEL:
            body["Command"] = ["/opt/tunnel/bin/tunnel", "run", "--token-file", root + "/etc/tunnel/token"]
        with open(root + "/LaunchDaemons/%s.plist" % label, "wb") as fh:
            plistlib.dump(body, fh)
    shared = "ROLE_ACCOUNT=%s\nDISPATCHER_SERVICE=%s\nDISPATCHER_PORT=3456\n" % (ACCT, DISP)
    _write(k + "/" + STAGE_E, shared + "DISPATCHER_CONFIG=%s/config.json\n" % d)
    _write(k + "/" + UPDATE, shared + "DISPATCHER_ENV_FILE=%s/%s\nDISPATCHER_PACKAGE=disp-pkg\n" % (d, ENV_NAME))
    _write(k + "/" + CHAT, shared + "DISPATCHER_CONFIG=%s/config.json\nDISPATCHER_ENV_FILE=%s/%s\nFRONT_DOOR_SERVICE=%s\n"
           "FRONT_DOOR_CONFIG=%s/Caddyfile\nFRONT_DOOR_HOST=hook.acme.test\n" % (d, d, ENV_NAME, FRONT, d))
    _write(k + "/" + RUNNER, "# the runner's settings\nROLE_ACCOUNT=%s\nCLAUDE_BIN=/opt/claude/bin/claude\n"
           "OWNER_USER_IDS=owner-1\nDISPATCHER_USER_IDS=disp-1\nPROJECTS=app=~/repos/app@main, web=~/repos/web\n"
           "WRITER_KEY_SOURCE=other-program\nWRITER_KEY_FROM=%s/config.json\nWRITER_KEY_FROM_FIELD=linearToken\n"
           "WRITER_RENEWAL_FROM_FIELD=refreshToken\n" % (ACCT, d))
    _write(k + "/" + CONF_NAME, "STATE_BACKUP_SERVICE=%s\nSTATE_BACKUP_COPIES=%s/state/state.json.bak-\n"
           "TUNNEL_SERVICE=%s\nTUNNEL_TOKEN_FILE=%s/etc/tunnel/token\nTUNNEL_PAGE=https://tunnels.example.test/\n"
           "OTHER_SERVICES=%s\n" % (BACKUP_JOB, d, TUNNEL, root, CANARY))
    return w


def _edit(path, old, new):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    assert old in text, (path, old)
    _write(path, text.replace(old, new))


def _first(log, item):
    return next((i for i, e in enumerate(log) if e[:len(item)] == item), None)


def _left(w):
    """What of the old dispatcher is still on the stand-in Mac."""
    out = sorted(label for label in (DISP, BACKUP_JOB, FRONT, TUNNEL, CANARY, PF_LABEL)
                 if label in w.jobs or os.path.lexists(w.root + "/LaunchDaemons/%s.plist" % label))
    out += sorted(p[len(w.root):] for p in (
        w.disp + "/config.json", w.disp + "/" + ENV_NAME, w.disp + "/" + pu.TOKEN_STORE, w.disp + "/Caddyfile",
        w.disp + "/state/state.json.bak-20261101T000000Z", w.home + "/.stage-e/backups/config.json.20261101T000000Z",
        w.home + "/.claude/pipeline/pins/dispatched-lane/" + ACCT, w.root + "/etc/claude-pipeline/dispatched-lane/" + ACCT,
        w.root + "/etc/tunnel/token", w.root + "/pf/pf.rules", w.root + "/log/pf.log") if os.path.lexists(p))
    return out + (["pf rules"] if w.pf else []) + (["package"] if w.package else [])


def _place(w):
    """The system folders this script names, moved into the world's own folder for its run."""
    global LAUNCH_DAEMONS, MARKER_DIR, PF_RULES_DIR, PF_RULES, PF_LOG, ROOT_UID
    r = w.root
    LAUNCH_DAEMONS, MARKER_DIR, ROOT_UID = r + "/LaunchDaemons", r + "/etc/claude-pipeline/dispatched-lane", os.getuid()
    PF_RULES_DIR, PF_RULES, PF_LOG = r + "/pf", r + "/pf/pf.rules", r + "/log/pf.log"


def _cases(expect, tmp):  # noqa: C901  (one battery, read top to bottom)
    seq = [0]

    def run(mode="check", fix=None, world=None):
        seq[0] += 1
        w = world or _build(tmp, "w%d" % seq[0])
        if fix:
            fix(w)
        _place(w)
        io = _FakeIO(w)
        rc = cmd(io, mode, w.kit)
        return rc, "\n".join(io.said), w, io

    def changes(w):
        return [e for e in w.log if e[0] in ("disable", "bootout", "put", "remove", "pf-flush", "npm-uninstall")
                or e == ("installer", "apply")]

    def secret_free(name, text, w):
        argv = " ".join(" ".join(e[1]) for e in w.log if e[0] == "run")
        expect(name + ": no key in what it said or in any command line",
               [s for s in (ACCESS, REFRESH, ENV_VALUE) if s in text or s in argv], [])

    everything = sorted([BACKUP_JOB, CANARY, DISP, FRONT, PF_LABEL, TUNNEL]) + sorted([
        "/etc/claude-pipeline/dispatched-lane/" + ACCT, "/etc/tunnel/token",
        "/home/.claude/pipeline/pins/dispatched-lane/" + ACCT, "/home/.stage-e/backups/config.json.20261101T000000Z",
        "/log/pf.log", "/opt/disp/" + ENV_NAME, "/opt/disp/Caddyfile", "/opt/disp/config.json",
        "/opt/disp/" + pu.TOKEN_STORE, "/opt/disp/state/state.json.bak-20261101T000000Z", "/pf/pf.rules"]) + [
        "pf rules", "package"]

    # -- refusals, before anything is read ------------------------------------------------------
    rc, text, w, io = run(fix=lambda w: w.env.update({AGENT_ENV_MARKERS[0]: "1"}))
    expect("under a model: refused with exit 3, nothing run", (rc, "an agent's environment" in text, w.log), (3, True, []))
    rc, text, w, io = run("run", fix=lambda w: setattr(w, "uid", 0))
    expect("as root: refused with exit 3, the one message, nothing run", (rc, rg.MESSAGE in text, w.log), (3, True, []))

    # -- the settings -----------------------------------------------------------------------------
    rc, text, w, io = run(fix=lambda w: os.unlink(w.kit + "/" + CONF_NAME))
    expect("no dispatcher-removal.conf: exit 2, it is named, nothing run", (rc, CONF_NAME + " absent" in text, w.log),
           (2, True, []))
    rc, text, w, io = run(fix=lambda w: _edit(w.kit + "/" + UPDATE, "DISPATCHER_SERVICE=" + DISP,
                                              "DISPATCHER_SERVICE=com.acme.other"))
    expect("two files name two dispatchers: exit 2, said", (rc, "DISPATCHER_SERVICE differs between" in text), (2, True))
    rc, text, w, io = run(fix=lambda w: _edit(w.kit + "/" + CONF_NAME, "TUNNEL_SERVICE=" + TUNNEL,
                                              "TUNNEL_SERVICE=" + CONF_UNEDITED["TUNNEL_SERVICE"]))
    expect("an example's value as typed: exit 2", (rc, "still the example's value" in text), (2, True))
    rc, text, w, io = run(fix=lambda w: _edit(w.kit + "/" + CONF_NAME, "OTHER_SERVICES=" + CANARY, "OTHER_SERVICES="))
    expect("a key left empty: exit 2, it asks for `none`", (rc, "or `none`" in text), (2, True))
    rc, text, w, io = run(fix=lambda w: _edit(w.kit + "/" + CONF_NAME, "OTHER_SERVICES=", "MYSTERY=1\nOTHER_SERVICES="))
    expect("a key it does not know: exit 2", (rc, "names MYSTERY, which it does not know" in text), (2, True))
    rc, text, w, io = run(fix=lambda w: setattr(w, "no_sudo", True))
    expect("no administrator access: exit 5, nothing run", (rc, w.log), (5, []))

    with open(os.path.join(KIT, CONF_NAME + ".example"), encoding="utf-8") as fh:
        example = fh.read()
    keys = sorted(set(m.group(1) for m in map(ps.EXAMPLE_KEY_RE.match, example.splitlines()) if m))
    expect("the example names every key the script reads, and no other", keys, sorted(CONF_KEYS))

    def unedited(w):
        _write(w.kit + "/" + CONF_NAME, example)
    rc, text, w, io = run(fix=unedited)
    expect("the example copied as it is cannot run: each of its values is refused as typed",
           (rc, text.count("is still the example's value")), (2, len(CONF_UNEDITED)))

    # -- check, on a host where every gate holds ---------------------------------------------------
    rc, text, w, io = run()
    expect("check: READY, exit 0", (rc, "ANSWER: READY" in text), (0, True))
    expect("check: it shows each piece it would remove, and the handover", [s for s in (
        "WHAT `run` DOES NOW", "WRITER_KEY_SOURCE goes from other-program to renew", "the tunnel: its job and its token",
        "the firewall rule and its boot job", "the lane marker", "with their backup copies", CANARY) if s not in text], [])
    expect("check: it changes nothing and asks nothing", (changes(w), io.asked, w.renewed_once), ([], [], False))
    secret_free("check", text, w)

    # -- the gates ----------------------------------------------------------------------------------
    def lane_off(w):
        _write(w.home + "/.pipeline-runner/store/" + store.LANES_FILE, json.dumps(
            {"schema": store.LANES_SCHEMA, "enabled": True, "work_dir": w.home + "/.pipeline-runner/work",
             "look_config_dir": None, "projects": {"app": list(store.KINDS), "web": ["code", "fix"]}}))
    rc, text, w, io = run("run", fix=lane_off)
    expect("a lane off on the runner: NOT READY, exit 1, the project and lanes named, nothing changed",
           (rc, "web: conflict, review, plan, acceptance still off" in text, changes(w), io.asked), (1, True, [], []))

    def serves_more(w):
        with open(w.disp + "/config.json") as fh:
            doc = json.load(fh)
        doc["repositories"].append({"id": "old", "githubUrl": "https://github.com/acme/old.git", "isActive": True})
        doc["repositories"].append({"id": "off", "githubUrl": "https://github.com/acme/gone.git", "isActive": False})
        _write(w.disp + "/config.json", json.dumps(doc))
    rc, text, w, io = run(fix=serves_more)
    expect("the old dispatcher serves a repository the runner does not: NOT READY; an inactive entry counts for nothing",
           (rc, "serves acme/old (entry old)" in text, "acme/gone" in text), (1, True, False))
    def token_in_address(w):
        with open(w.disp + "/config.json") as fh:
            doc = json.load(fh)
        doc["repositories"][0]["githubUrl"] = "https://x-access-token:%s@github.com/acme/app.git" % ACCESS
        _write(w.disp + "/config.json", json.dumps(doc))
    rc, text, w, io = run(fix=token_in_address)
    expect("a token in an entry's address: dropped by the account's own program, the entry still read",
           (rc, "ANSWER: READY" in text), (0, True))
    secret_free("a token in an address", text, w)
    rc, text, w, io = run(fix=lambda w: _write(w.home + "/.pipeline-runner/store/" + store.LANES_FILE, "{not json"))
    expect("a lanes record it cannot read: UNKNOWN, exit 4, never READY", (rc, "ANSWER: UNKNOWN" in text), (4, True))
    rc, text, w, io = run(fix=lambda w: setattr(w, "no_role", True))
    expect("the pipeline's account cannot be reached: UNKNOWN, exit 4", (rc, "ANSWER: UNKNOWN" in text), (4, True))
    rc, text, w, io = run(fix=lambda w: w.cwds.append(w.disp + "/worktrees/app-12"))
    expect("a session of the old dispatcher still works: NOT READY", (rc, "still work in" in text), (1, True))
    rc, text, w, io = run(fix=lambda w: setattr(w, "cwds", None))
    expect("its processes cannot be listed: UNKNOWN", (rc, "could not be listed" in text), (4, True))
    rc, text, w, io = run(fix=lambda w: os.unlink(w.home + "/.pipeline-runner/writer-client"))
    expect("no client details in the runner's file: NOT READY, writer-client named",
           (rc, "writer-client` first" in text), (1, True))
    rc, text, w, io = run(fix=lambda w: _write(w.home + "/.pipeline-runner/writer-renewal-key", "x" * 20))
    expect("a renewal token of the runner's already there: NOT READY", (rc, "move it aside" in text), (1, True))
    rc, text, w, io = run(fix=lambda w: _edit(w.kit + "/" + RUNNER, "WRITER_RENEWAL_FROM_FIELD=refreshToken",
                                              "WRITER_RENEWAL_FROM_FIELD=noSuchKey"))
    expect("no renewal token under that key in the old dispatcher's settings: NOT READY",
           (rc, "hold no one renewal token under noSuchKey (0 place(s))" in text), (1, True))
    rc, text, w, io = run(fix=lambda w: _edit(w.kit + "/" + RUNNER, "WRITER_KEY_FROM=%s/config.json" % w.disp,
                                              "WRITER_KEY_FROM=%s/other.json" % w.disp))
    expect("the runner adopts from another file than the old dispatcher's settings: NOT READY",
           (rc, "are not all that one file" in text), (1, True))

    def folder_holds_it(w):
        _write(w.root + "/settings/" + ps.MACHINE, "[runner.conf]\nWRITER_KEY_SOURCE=other-program\n")
        os.chmod(w.root + "/settings", 0o700)
    rc, text, w, io = run(fix=folder_holds_it)
    expect("the settings folder would set the line back: NOT READY",
           (rc, "take that line out of machine.conf" in text), (1, True))

    def kit_env(w):
        for f in (UPDATE, CHAT):
            _edit(w.kit + "/" + f, "DISPATCHER_ENV_FILE=%s/%s" % (w.disp, ENV_NAME),
                  "DISPATCHER_ENV_FILE=%s/.stage-e/%s" % (w.home, ENV_NAME[1:]))
    rc, text, w, io = run(fix=kit_env)
    expect("a dispatcher file that is the kit's own: NOT READY",
           (rc, "would remove the runner's or the kit's own files" in text), (1, True))

    def other_token(w):
        _edit(w.kit + "/" + CONF_NAME, "TUNNEL_TOKEN_FILE=%s/etc/tunnel/token" % w.root,
              "TUNNEL_TOKEN_FILE=%s/etc/other-token" % w.root)
        _write(w.root + "/etc/other-token", "x")
    rc, text, w, io = run(fix=other_token)
    expect("a token file the tunnel's job does not name: NOT READY, it stays", (rc, "not proven the token" in text),
           (1, True))
    rc, text, w, io = run(fix=lambda w: w.jobs.pop(RUNNER_JOB))
    expect("the runner's job not running: NOT READY", (rc, "is not running" in text), (1, True))

    # -- run ------------------------------------------------------------------------------------
    rc, text, w, io = run("run", fix=lambda w: setattr(w, "answer", "y"))
    expect("anything but a typed yes: exit 10, nothing changed", (rc, changes(w)), (10, []))
    rc, text, w, io = run("run", fix=lambda w: setattr(w, "on_ask", lambda w: w.cwds.append(w.disp + "/worktrees/b")))
    expect("a change while the question waited: exit 4, nothing changed", (rc, changes(w)), (4, []))

    w = _build(tmp, "happy")
    with open(w.kit + "/" + RUNNER) as fh:
        conf_before = fh.read()
    rc, text, w, io = run("run", world=w)
    expect("run: exit 0, DONE, every piece gone and read back", (rc, "DONE: the old dispatcher" in text, _left(w)),
           (0, True, []))
    order = [_first(w.log, e) for e in (("disable", DISP), ("bootout", DISP), ("put",), ("installer", "apply"),
                                        ("renewal", "renewed"), ("installer", "check"), ("remove",))]
    expect("run: stop, then switch, then the renewal, then the runner's check, then the first removal",
           (order, None not in order and order == sorted(order)), (order, True))
    expect("run: the runner renewed only once the old dispatcher was gone: never two renewers", w.renewed_while_held,
           False)
    with open(w.kit + "/" + RUNNER) as fh:
        expect("run: runner.conf changed in that one line, every other byte kept", fh.read(),
               conf_before.replace("WRITER_KEY_SOURCE=other-program", "WRITER_KEY_SOURCE=renew"))
    expect("run: what is not the old dispatcher's stays", [os.path.exists(p) for p in (
        w.home + "/.stage-e/backups/operator-notes.txt", w.home + "/.stage-e/" + ENV_NAME[1:],
        w.disp + "/state/state.json", w.home + "/.pipeline-runner/writer-client")], [True] * 4)
    expect("run: the runner's job still runs", w.jobs.get(RUNNER_JOB), "running")
    expect("run: the steps outside the Mac, each with its page", [s for s in (
        "https://tunnels.example.test/", TRACKER_APPS_PAGE, "LINEAR_CLIENT_ID, LINEAR_CLIENT_SECRET, GH_TOKEN",
        "Keep the application", "Never revoke", "Tickets that close", "hook.acme.test") if s not in text], [])
    secret_free("run", text, w)
    rc, text, w, io = run(world=w)
    expect("run again after it: NOTHING LEFT, exit 0", (rc, "ANSWER: NOTHING LEFT" in text), (0, True))

    # -- the handover fails: nothing is removed ----------------------------------------------------
    rc, text, w, io = run("run", fix=lambda w: setattr(w, "renewal", "client-refused"))
    expect("a refused renewal: exit 1, nothing removed, the old dispatcher disabled",
           (rc, _left(w) == everything, DISP in w.disabled,
            _first(w.log, ("remove",))), (1, True, True, None))
    expect("a refused renewal: it says how to start the old dispatcher again", [s for s in (
        "NOTHING WAS REMOVED", "sudo launchctl enable system/" + DISP, "sudo launchctl bootstrap system",
        "set WRITER_KEY_SOURCE=other-program") if s not in text], [])
    rc, text, w, io = run("run", fix=lambda w: setattr(w, "renewal", "other-user"))
    expect("renewed, but the key answers as another user: exit 1, nothing removed, never start the old one",
           (rc, _first(w.log, ("remove",)), "Do NOT start the old dispatcher" in text), (1, None, True))
    rc, text, w, io = run("run", fix=lambda w: setattr(w, "renewal", "silent"))
    expect("the runner says nothing: exit 1 after the wait, nothing removed",
           (rc, _first(w.log, ("remove",)), "within %d minutes" % (HANDOVER_WAIT // 60) in text), (1, None, True))
    rc, text, w, io = run("run", fix=lambda w: setattr(w, "apply_fails", True))
    with open(w.kit + "/" + RUNNER) as fh:
        expect("the runner's installer fails: exit 1, nothing removed, runner.conf put back",
               (rc, _first(w.log, ("remove",)), "WRITER_KEY_SOURCE=other-program" in fh.read()), (1, None, True))
    rc, text, w, io = run("run", fix=lambda w: w.stuck.add(DISP))
    expect("launchd keeps the old dispatcher: exit 1, nothing switched or removed",
           (rc, _first(w.log, ("put",)), _first(w.log, ("remove",))), (1, None, None))
    rc, text, w, io = run("run", fix=lambda w: setattr(w, "port_open", True))
    expect("its port still open after the stop: exit 1, nothing switched", (rc, _first(w.log, ("put",))), (1, None))

    # -- a removal fails part way: it stops, and a second run takes the rest ------------------------
    w = _build(tmp, "partway")
    w.npm_keeps = True
    rc, text, w, io = run("run", world=w)
    expect("the package stays: exit 1, what came before is gone, what comes after untouched",
           (rc, "NOT TRIED the lane marker" in text, [x for x in _left(w) if x in (DISP, FRONT, "package")],
            "/opt/disp/config.json" in _left(w)), (1, True, ["package"], True))
    w.npm_keeps = False
    rc, text, w, io = run("run", world=w)
    expect("run again once npm removes it: exit 0, the rest gone, the key handed over once",
           (rc, _left(w), [e for e in w.log if e == ("installer", "apply")]), (0, [], [("installer", "apply")]))


def selftest():
    import tempfile
    global LAUNCH_DAEMONS, MARKER_DIR, PF_RULES_DIR, PF_RULES, PF_LOG, ROOT_UID
    failures = []

    def expect(name, got, want):
        ok = got == want
        print("%s %s" % ("PASS" if ok else "FAIL", name) + ("" if ok else "\n     got  %r\n     want %r" % (got, want)))
        if not ok:
            failures.append(name)

    saved = (LAUNCH_DAEMONS, MARKER_DIR, PF_RULES_DIR, PF_RULES, PF_LOG, ROOT_UID)
    try:
        with tempfile.TemporaryDirectory(prefix="dispatcher-removal-") as tmp:
            _cases(expect, os.path.realpath(tmp))
    finally:
        LAUNCH_DAEMONS, MARKER_DIR, PF_RULES_DIR, PF_RULES, PF_LOG, ROOT_UID = saved
    if failures:
        print("\npipeline_dispatcher_removal selftest: %d FAILED" % len(failures))
        return 1
    print("\npipeline_dispatcher_removal selftest: all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
