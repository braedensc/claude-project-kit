#!/usr/bin/env python3
"""The sweep: what a finished dispatcher run left on the role account goes (KIT-464).

WHAT THIS IS, AND WHY IT EXISTS

  Every session the pipeline runs is a dispatcher session as the role account: coding
  sessions, planning runs, re-prompts, the chat lane, the workers. Nobody watches them.
  Measured on 4 Nov 2026 (KIT-458), what they leave is small in bytes and grows with every
  run. The dispatcher had made 216 folders at the top of its home, one for every ticket it
  ever started (`<home>/<ID>/attachments`, made whether or not the ticket has attachments),
  and its code removes none of them. It force-removes a ticket's worktree when the ticket
  reaches a terminal state, but only if it sees that change: the worktree of a ticket that
  was deleted, or closed while the dispatcher was down, stays.

ONE PASS, run as the account its config names, judges five kinds of leftover:

  worktree  <workspace_dir>/<ID>, once its ticket has been done (completed or canceled) for
            done_days, or is gone (deleted, or no longer on its team) and nothing in the
            folder has changed for done_days. A git worktree goes through its own clone
            (`git worktree remove --force`), never as a plain folder; a folder with no `.git`
            at all goes as one.
  ticket    <dispatcher_home>/<ID>, the dispatcher's own folder for that ticket, on the same
            rule, once the ticket's worktree is gone.
  session   a Claude Code project folder under session_root, once nothing in it has changed
            for idle_days; otherwise each of its session folders that has not. Idle stands
            in for ended: a session keeps no process in its temp folder, so a live one cannot
            be told apart. One idle past idle_days loses only that temp folder, and the
            dispatcher's headless sessions leave theirs empty (measured 4 Nov 2026).
  temp      a temp folder the kit makes, in this account's TMPDIR, once nothing in it has
            changed for idle_days: a battery's run folder (`<name>-selftest-*`,
            `<name>-run-*`, from scratch_run's contained()), a scratch run, a job's own
            folder (`stage-e-dry-*`, `pipeline-morning-*`, `pipeline-voice-*`: a dry run's,
            or a model call's HOME). The selftest reads every prefix the kit's scripts use
            and checks this pattern takes it.
  look      <look_record_dir>/<ID>, the look tool's records and kept screenshots for one plan
            (KIT-572), on the worktree's rule: its plan's ticket done for done_days, or gone
            and nothing in the folder changed for done_days. Off while look_record_dir is
            unset, and the pass says so.

WHAT IT NEVER REMOVES (each a selftest case)

  A folder whose ticket is open. One a process of this account is working in. One another
  account owns. A symlink. A name that is not a ticket's, a session's or the kit's. A ticket
  on a team its config does not name, or on one the tracker key cannot see. Anything outside
  the five folders its config names. A source it could not read (the tracker, the list of
  folders in use, a folder) removes nothing that needs it, and the pass says so: it is never
  read as empty.

WHAT IT NEVER DOES (asserted in --selftest against this file's own source)

  Write to the tracker: its only tracker calls are two reads. Run `rm`: a plain folder goes
  through Python's own removal, a worktree through `git worktree remove`.

Usage:
    pipeline_sweep.py run --config <sweep.json> [--dry-run]
    pipeline_sweep.py --example-config
    pipeline_sweep.py --selftest
Exit: 0 = the pass ran, and everything due went (nothing may have been due); 1 = something
      due could not be removed, or the pass could not complete; 2 = usage, config or
      credential; 3 = refused: an agent environment asked for a real pass, or this is not the
      account the config names; 4 = the pass ran, but something could not be judged and was
      left.
"""
import argparse
import glob
import itertools
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_REFUSED, EXIT_UNKNOWN = 0, 1, 2, 3, 4
LINEAR_API = "https://api.linear.app/graphql"
USER_AGENT = "pipeline-sweep/1"             # Cloudflare refuses Python's default agent
DAY = 86400
DEFAULT_DONE_DAYS = 3
DEFAULT_IDLE_DAYS = 3
DEFAULT_LOG_MAX_MB = 10
DEFAULT_LOG_KEEP = 3
MAX_PAGES = 20                              # 2,000 tickets of one team in one pass
WALK_LIMIT = 500000                         # entries one folder may hold and still be judged
AGENT_ENV_MARKERS = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_PROJECT_DIR", "AI_AGENT")
TICKET_RE = re.compile(r"^([A-Z][A-Z0-9]*)-([1-9][0-9]{0,8})$")
SESSION_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# Claude Code names a project's temp folder after its working folder, each "/" a "-".
PROJECT_RE = re.compile(r"^-[^/]+$")
# A kit temp folder has the shape tempfile.mkdtemp gives it: a kit prefix, then eight
# characters from [a-z0-9_]. A battery's run folder ends `-selftest-` or `-run-`; a job's own
# folder (a dry run's, a model call's HOME) uses its job's prefix, named here with the
# script that makes it.
JOB_PREFIXES = {"stage-e-dry-": "pipeline_review_poller.py",
                "pipeline-morning-": "pipeline_morning.py",
                "pipeline-voice-": "pipeline_voice.py"}
KIT_TEMP_RE = re.compile(r"^(?:[a-z0-9-]+-(?:selftest|run)|%s)-[a-z0-9_]{8}$"
                         % "|".join(re.escape(p[:-1]) for p in sorted(JOB_PREFIXES)))
REMOVE, KEEP, UNKNOWN = "remove", "keep", "unknown"

CONFIG_KEYS = {
    "account": "the account the sweep runs as, and sweeps: the dispatcher's role account. A "
               "pass as any other account is refused",
    "workspace_dir": "the dispatcher's worktree folder (its workspaceBaseDir), absolute",
    "dispatcher_home": "the dispatcher's home (its --cyrus-home), where it keeps a folder per "
                       "ticket, absolute",
    "teams": "team KEYS whose tickets' leftovers may go, e.g. [\"ENG\"]. A folder of any other "
             "team is left",
    "linear_key_env": "ENV VAR NAME holding the tracker key (never the key)",
    "done_days": "days a ticket must have been done, or a gone ticket's folder untouched, before "
                 "its worktree and folder go (default %d)" % DEFAULT_DONE_DAYS,
    "idle_days": "days a session folder or a kit temp folder must be untouched before it goes "
                 "(default %d)" % DEFAULT_IDLE_DAYS,
    "session_root": "Claude Code's temp folder for this account; empty is /tmp/claude-<uid>",
    "temp_dir": "this account's TMPDIR; empty asks the system (getconf DARWIN_USER_TEMP_DIR on "
                "macOS, else $TMPDIR, else /tmp)",
    "logs": "absolute glob patterns of the job logs to keep small, each ending .log, e.g. "
            "[\"/Users/<role>/.stage-e/*.log\"]; empty trims none",
    "log_max_mb": "a log past this many MB is trimmed (default %d)" % DEFAULT_LOG_MAX_MB,
    "log_keep": "older copies kept of a trimmed log, log.1 to log.N (default %d)" % DEFAULT_LOG_KEEP,
    "look_record_dir": "the look tool's record folder (its record_dir), absolute: a plan's folder of "
                       "records and kept screenshots goes on the worktree's rule. Empty sweeps none",
}

EXAMPLE_CONFIG = {
    "account": "_exdispatch",
    "workspace_dir": "/opt/example-dispatcher/worktrees",
    "dispatcher_home": "/opt/example-dispatcher",
    "teams": ["ENG"],
    "linear_key_env": "STAGE_E_LINEAR_API_KEY",
    "done_days": DEFAULT_DONE_DAYS,
    "idle_days": DEFAULT_IDLE_DAYS,
    "session_root": "",
    "temp_dir": "",
    "logs": ["/Users/<role>/.stage-e/*.log", "/Users/<role>/.stage-e/*/*.log"],
    "log_max_mb": DEFAULT_LOG_MAX_MB,
    "log_keep": DEFAULT_LOG_KEEP,
    "look_record_dir": "",
}

# The sweep's only tracker calls, both reads. The selftest pins that no other exists. A team
# the first does not list is one the key cannot see, and its tickets are not judged.
Q_TEAMS = """
query SweepTeams {
  teams(first: 250) { nodes { key } }
}"""
Q_TICKETS = """
query SweepTickets($filter: IssueFilter!, $after: String) {
  issues(filter: $filter, first: 100, after: $after, includeArchived: true) {
    nodes { number trashed completedAt canceledAt state { type } team { key } }
    pageInfo { hasNextPage endCursor }
  }
}"""


class SweepError(Exception):
    """The pass could not complete (exit 1). Never confused with 'nothing to do'."""


class ConfigError(SweepError):
    """Bad arguments, config or credential (exit 2)."""


def log(msg):
    sys.stderr.write("[sweep] %s\n" % msg)
    sys.stderr.flush()


def parse_iso(text):
    """Seconds since the epoch for an ISO-8601 instant, or None."""
    if not isinstance(text, str) or not text.strip():
        return None
    raw = text.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


# --------------------------------------------------------------------------- #
# Config + credential
# --------------------------------------------------------------------------- #
def _folder(raw, key, required=True):
    value = raw.get(key) or ""
    if not value:
        if required:
            raise ConfigError("config needs %r: %s" % (key, CONFIG_KEYS[key]))
        return ""
    if not isinstance(value, str) or not os.path.isabs(value):
        raise ConfigError("config %r must be an absolute path" % key)
    norm = os.path.normpath(value)
    if norm.count(os.sep) < 2:
        raise ConfigError("config %r is %s: a sweep of a top-level folder is refused" % (key, norm))
    return norm


def _days(raw, key, default):
    value = raw.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 365:
        raise ConfigError("config %r must be a whole number of days from 1 to 365" % key)
    return value


def _log_patterns(raw):
    value = raw.get("logs", [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError("config 'logs' must be a list of absolute glob patterns")
    for pattern in value:
        norm = os.path.normpath(pattern)
        if (not os.path.isabs(pattern) or "**" in pattern or not norm.endswith(".log")
                or norm.count(os.sep) < 3 or "\x00" in pattern):
            raise ConfigError("config 'logs' entry: %r must be an absolute pattern ending .log, "
                              "at least two folders deep, without **" % pattern)
    return sorted(set(os.path.normpath(v) for v in value))


def _count(raw, key, default, low, high):
    value = raw.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise ConfigError("config %r must be a whole number from %d to %d" % (key, low, high))
    return value


def load_config(path):
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ConfigError("could not read --config %s: %s" % (path, exc))
    if not isinstance(raw, dict):
        raise ConfigError("--config must hold one JSON object")
    unknown = sorted(set(raw) - set(CONFIG_KEYS))
    if unknown:
        raise ConfigError("unknown config key(s): %s (see --example-config)" % ", ".join(unknown))
    account = raw.get("account")
    if not isinstance(account, str) or not re.match(r"^[A-Za-z_][A-Za-z0-9_.-]*$", account):
        raise ConfigError("config needs 'account': %s" % CONFIG_KEYS["account"])
    teams = raw.get("teams")
    if not isinstance(teams, list) or not teams or not all(
            isinstance(t, str) and re.match(r"^[A-Z][A-Z0-9]*$", t) for t in teams):
        raise ConfigError("config 'teams' must be a non-empty list of team KEYS, e.g. [\"ENG\"]")
    key_env = raw.get("linear_key_env") or "STAGE_E_LINEAR_API_KEY"
    if not isinstance(key_env, str) or not re.match(r"^[A-Z][A-Z0-9_]*$", key_env):
        raise ConfigError("config 'linear_key_env' must be an ENV VAR NAME, never a value")
    cfg = {
        "account": account,
        "workspace_dir": _folder(raw, "workspace_dir"),
        "dispatcher_home": _folder(raw, "dispatcher_home"),
        "session_root": _folder(raw, "session_root", required=False),
        "temp_dir": _folder(raw, "temp_dir", required=False),
        "teams": sorted(set(teams)),
        "linear_key_env": key_env,
        "done_days": _days(raw, "done_days", DEFAULT_DONE_DAYS),
        "idle_days": _days(raw, "idle_days", DEFAULT_IDLE_DAYS),
        "logs": _log_patterns(raw),
        "log_max_mb": _count(raw, "log_max_mb", DEFAULT_LOG_MAX_MB, 1, 10240),
        "log_keep": _count(raw, "log_keep", DEFAULT_LOG_KEEP, 1, 20),
        "look_record_dir": _folder(raw, "look_record_dir", required=False),
    }
    if cfg["workspace_dir"] == cfg["dispatcher_home"]:
        raise ConfigError("config 'workspace_dir' and 'dispatcher_home' must be two folders")
    if cfg["look_record_dir"] in (cfg["workspace_dir"], cfg["dispatcher_home"]):
        raise ConfigError("config 'look_record_dir' must be its own folder, not the worktree folder or the "
                          "dispatcher's home: one folder judged by two rules")
    return cfg


def credential(cfg, environ):
    value = (environ.get(cfg["linear_key_env"]) or "").strip()
    if not value:
        raise ConfigError("$%s is unset: it lives in the role account's own env file"
                          % cfg["linear_key_env"])
    return value


def agent_markers(environ):
    """Presence, not truthiness: a marker set to the empty string is set."""
    return [m for m in AGENT_ENV_MARKERS if m in environ]


# --------------------------------------------------------------------------- #
# The outside world, in one place so the selftest can stand in for each part
# --------------------------------------------------------------------------- #
class World(object):
    """The tracker, the account's processes, git, the clock and the account itself."""

    def __init__(self, api_key="", environ=None):
        self.api_key = api_key
        self.environ = os.environ if environ is None else environ
        self.uid = os.getuid()

    def now(self):
        return time.time()

    def owns(self, path, st):
        return st.st_uid == self.uid

    def gql(self, query, variables):
        req = urllib.request.Request(
            LINEAR_API, data=json.dumps({"query": query, "variables": variables}).encode(),
            headers={"Content-Type": "application/json", "Authorization": self.api_key,
                     "User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (fixed URL)
                payload = json.load(resp)
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read(4096).decode("utf-8", "replace")
            except OSError:
                body = ""
            if exc.code in (401, 403) or "AUTHENTICATION_ERROR" in body:
                raise ConfigError("the tracker refused the key (HTTP %d)" % exc.code)
            raise SweepError("the tracker answered HTTP %d" % exc.code)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise SweepError("the tracker could not be reached: %s" % getattr(exc, "reason", exc))
        if payload.get("errors"):
            if "AUTHENTICATION_ERROR" in json.dumps(payload["errors"]):
                raise ConfigError("the tracker refused the key")
            first = payload["errors"][0] if isinstance(payload["errors"], list) else {}
            raise SweepError("the tracker refused the read: %s"
                             % str((first or {}).get("message") or "no message")[:160])
        return payload.get("data") or {}

    def cwds(self):
        """The real working folders of this account's processes, or None when they could not
        be read. Never an empty list: lsof's own process is one of them."""
        try:
            r = subprocess.run(["lsof", "-a", "-u", str(self.uid), "-d", "cwd", "-F", "n"],
                               capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            return None
        found = [os.path.realpath(line[1:]) for line in r.stdout.splitlines()
                 if line.startswith("n/")]
        return found or None

    def git(self, args):
        """(exit code, stdout or stderr) of one git command, run from `/`."""
        try:
            r = subprocess.run(["git"] + args, capture_output=True, text=True, timeout=600,
                               cwd="/", env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
        except (OSError, subprocess.TimeoutExpired) as exc:
            return 1, str(exc)
        return r.returncode, (r.stdout if r.returncode == 0 else r.stderr).strip()

    def session_root(self):
        base = self.environ.get("CLAUDE_CODE_TMPDIR") or "/tmp"
        return os.path.realpath(os.path.join(base, "claude-%d" % self.uid))

    def temp_dir(self):
        if sys.platform == "darwin":
            # A launchd job may start with no TMPDIR: ask macOS for this account's own folder.
            try:
                r = subprocess.run(["getconf", "DARWIN_USER_TEMP_DIR"], capture_output=True,
                                   text=True, timeout=10)
                if r.returncode == 0 and r.stdout.strip():
                    return os.path.realpath(r.stdout.strip())
            except (OSError, subprocess.TimeoutExpired):
                pass
        return os.path.realpath(self.environ.get("TMPDIR") or "/tmp")


# --------------------------------------------------------------------------- #
# Reading: the tracker, and the folders themselves
# --------------------------------------------------------------------------- #
def read_tickets(world, idents):
    """({ident: (state, since)}, {ident: why not}): state is open, done or gone, and `since`
    when a done ticket was done. A team the key cannot see, or one whose read failed, is
    never read as having no tickets: its tickets are not judged."""
    by_team = {}
    for ident in idents:
        team, number = TICKET_RE.match(ident).groups()
        by_team.setdefault(team, set()).add(int(number))
    states, unknown = {}, {}
    try:
        seen = {n["key"] for n in world.gql(Q_TEAMS, {})["teams"]["nodes"]}
    except ConfigError:
        raise                    # a key the tracker refuses is a credential fault: exit 2
    except (SweepError, KeyError, TypeError) as exc:
        return states, {i: "the tracker could not be read (%s)" % exc for i in idents}
    for team, numbers in sorted(by_team.items()):
        names = ["%s-%d" % (team, n) for n in sorted(numbers)]
        if team not in seen:
            unknown.update((i, "the tracker key cannot see team %s" % team) for i in names)
            continue
        found, after = {}, None
        try:
            for _ in range(MAX_PAGES):
                conn = world.gql(Q_TICKETS, {"filter": {"team": {"key": {"eq": team}},
                                                        "number": {"in": sorted(numbers)}},
                                             "after": after})["issues"]
                for node in conn["nodes"]:
                    if (node.get("team") or {}).get("key") == team:
                        found[int(node["number"])] = node
                if not conn["pageInfo"]["hasNextPage"]:
                    break
                after = conn["pageInfo"]["endCursor"]
            else:
                raise SweepError("more than %d pages" % MAX_PAGES)
        except ConfigError:
            raise
        except (SweepError, KeyError, TypeError, ValueError) as exc:
            unknown.update((i, "the tracker could not be read for team %s (%s)" % (team, exc))
                           for i in names)
            continue
        for number, ident in zip(sorted(numbers), names):
            node = found.get(number)
            # A deleted ticket keeps its state, so `trashed` is read as gone.
            if node is None or node.get("trashed"):
                states[ident] = ("gone", None)
            elif (node.get("state") or {}).get("type") in ("completed", "canceled"):
                states[ident] = ("done", parse_iso(node.get("completedAt") or node.get("canceledAt")))
            else:
                states[ident] = ("open", None)
    return states, unknown


def walk(path, uid, limit=WALK_LIMIT):
    """(newest mtime, bytes, whole) for everything under `path`, symlinks never followed. Not
    whole when a folder could not be listed, or it held more than `limit` entries.

    A folder this account locked against itself (a tool's `chmod 000`, left by a run that
    was killed) cannot be listed, but with no search bit nothing in it can have changed since
    its last inode change: its ctime stands for everything inside it."""
    newest, total, seen, whole = 0.0, 0, 0, True
    stack = [path]
    while stack:
        p = stack.pop()
        try:
            st = os.lstat(p)
        except FileNotFoundError:
            continue
        except OSError:
            whole = False
            continue
        seen += 1
        if seen > limit:
            return newest, total, False
        newest = max(newest, st.st_mtime)
        total += getattr(st, "st_blocks", 0) * 512 or st.st_size
        if stat.S_ISDIR(st.st_mode):
            try:
                stack.extend(e.path for e in os.scandir(p))
            except FileNotFoundError:
                continue
            except PermissionError:
                if st.st_uid == uid and not st.st_mode & stat.S_IXUSR:
                    newest = max(newest, st.st_ctime)
                else:
                    whole = False
            except OSError:
                whole = False
    return newest, total, whole


def git_kind(path):
    """("plain", None) for a folder with no `.git`; ("worktree", common git dir) for one whose
    `.git` file names a worktree of a clone; ("clone", None) for one holding a repository of
    its own; ("odd", None) for a `.git` this sweep cannot follow."""
    gitpath = os.path.join(path, ".git")
    try:
        st = os.lstat(gitpath)
    except FileNotFoundError:
        return "plain", None
    except OSError:
        return "odd", None
    if stat.S_ISDIR(st.st_mode):
        return "clone", None
    if not stat.S_ISREG(st.st_mode):
        return "odd", None
    try:
        with open(gitpath, encoding="utf-8") as fh:
            line = fh.read(4096).strip()
        if not line.startswith("gitdir: "):
            return "odd", None
        admin = os.path.join(path, line[len("gitdir: "):].strip())
        with open(os.path.join(admin, "commondir"), encoding="utf-8") as fh:
            common = fh.read(4096).strip()
    except OSError:
        return "odd", None
    return "worktree", os.path.realpath(os.path.join(admin, common))


def listed(world, common):
    """{real path: locked} for each worktree the clone at `common` lists, or None."""
    code, out = world.git(["--git-dir=" + common, "worktree", "list", "--porcelain"])
    if code != 0:
        return None
    found, current = {}, None
    for line in out.splitlines():
        if line.startswith("worktree "):
            current = os.path.realpath(line[len("worktree "):])
            found[current] = False
        elif line.startswith("locked") and current:
            found[current] = True
    return found


def remove_plain(path):
    """Remove a folder this account owns, its permission bits first (a tool's `chmod 000`
    folder), never following a symlink. True once nothing is at `path`."""
    for root, dirs, _files in os.walk(path):
        for name in dirs:
            p = os.path.join(root, name)
            try:
                if not os.path.islink(p):
                    os.chmod(p, stat.S_IRWXU)
            except OSError:
                pass
    shutil.rmtree(path, ignore_errors=True)
    return not os.path.lexists(path)


# --------------------------------------------------------------------------- #
# One pass
# --------------------------------------------------------------------------- #
class Pass(object):
    """What one pass decided, item by item."""

    def __init__(self, dry_run):
        self.dry_run = dry_run
        self.removed, self.failed, self.notes = [], [], []
        self.trimmed = []
        self.kept, self.unknown = {}, {}

    def keep(self, why, name):
        self.kept.setdefault(why, []).append(name)

    def cannot(self, why, name):
        self.unknown.setdefault(why, []).append(name)


def own_entries(world, folder, pattern):
    """[(name, path)] for each entry of `folder` whose name fits `pattern`, sorted. The ones
    that are no real folder of this account are kept and said, never followed."""
    try:
        entries = sorted(os.scandir(folder), key=lambda e: e.name)
    except OSError as exc:
        raise SweepError("could not list %s: %s" % (folder, exc))
    out = []
    for e in entries:
        if not pattern.match(e.name):
            continue
        try:
            st = os.lstat(e.path)
        except FileNotFoundError:
            continue
        except OSError:
            out.append((e.name, None))
            continue
        if stat.S_ISLNK(st.st_mode):
            out.append((e.name, "a symlink"))
        elif not stat.S_ISDIR(st.st_mode):
            out.append((e.name, "not a folder"))
        elif not world.owns(e.path, st):
            out.append((e.name, "another account's folder"))
        else:
            out.append((e.name, e.path))
    return out


def resolve(world, cfg, key, p):
    """The folder `key` names, ready to read, or None when it is not (said). A session or temp
    folder that is not there is empty, not unknown: a restart clears both."""
    path = cfg[key] or {"session_root": world.session_root,
                        "temp_dir": world.temp_dir}[key]()
    if cfg[key] and os.path.realpath(path) != path:
        raise ConfigError("config %r %s goes through a symlink: name its real path, %s"
                          % (key, path, os.path.realpath(path)))
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        if key in ("session_root", "temp_dir", "look_record_dir"):   # each is made by its first use
            p.notes.append("%s %s is not there: nothing to sweep in it" % (key, path))
        else:
            p.cannot("%s is not there" % key, path)
        return None
    except OSError as exc:
        p.cannot("%s could not be read (%s)" % (key, exc), path)
        return None
    if not stat.S_ISDIR(st.st_mode) or not world.owns(path, st):
        p.cannot("%s is not a folder this account owns" % key, path)
        return None
    return path


def judge_ticket(ident, states, unknown, now, days, newest):
    if ident in unknown:
        return UNKNOWN, unknown[ident]
    state, since = states[ident]
    if state == "open":
        return KEEP, "its ticket is open"
    if state == "done":
        if since is None:
            return UNKNOWN, "its ticket is done, but the tracker gives no date"
        if now - since < days * DAY:
            return KEEP, "its ticket was done under %d days ago" % days
        return REMOVE, "%s was done %d days ago" % (ident, (now - since) // DAY)
    if now - newest < days * DAY:
        return KEEP, "its ticket is gone, but the folder changed under %d days ago" % days
    return REMOVE, "%s is gone from the tracker, and the folder has not changed for %d days" % (
        ident, (now - newest) // DAY)


def run_pass(world, cfg, dry_run=False):
    """(exit code, lines): one pass over the four kinds."""
    p = Pass(dry_run)
    now = world.now()
    folders = {key: resolve(world, cfg, key, p)
               for key in ("workspace_dir", "dispatcher_home", "session_root", "temp_dir")}
    folders["look_record_dir"] = resolve(world, cfg, "look_record_dir", p) if cfg.get("look_record_dir") else None
    cwds = world.cwds()
    if cwds is None:
        p.cannot("this account's working folders could not be read, so none is known to be "
                 "unused", "everything")
        trim_logs(world, cfg, p)         # needs neither the tracker nor the process list
        return finish(p)
    if not cfg.get("look_record_dir"):
        p.notes.append("look_record_dir is not set: no look-tool records are swept")

    def in_use(path):
        return any(c == path or c.startswith(path + os.sep) for c in cwds)

    tickets = {}
    for kind, key in (("worktree", "workspace_dir"), ("ticket", "dispatcher_home"), ("look", "look_record_dir")):
        tickets[kind] = own_entries(world, folders[key], TICKET_RE) if folders[key] else []
    idents = set()
    for kind in tickets:
        for ident, path in tickets[kind]:
            if TICKET_RE.match(ident).group(1) not in cfg["teams"]:
                continue
            idents.add(ident)
    states, unknown = read_tickets(world, sorted(idents)) if idents else ({}, {})

    going = set()
    for kind in ("worktree", "ticket"):
        for ident, path in tickets[kind]:
            if path is None or not path.startswith("/"):
                p.keep(path or "it could not be read", ident)
                continue
            if TICKET_RE.match(ident).group(1) not in cfg["teams"]:
                p.keep("its team is not one the config names", ident)
                continue
            if kind == "ticket" and ident not in going and os.path.lexists(
                    os.path.join(folders["workspace_dir"] or "/nonexistent", ident)):
                p.keep("its worktree is still there", ident)
                continue
            if ident in unknown or states[ident][0] == "open":
                (p.cannot if ident in unknown else p.keep)(
                    unknown.get(ident, "its ticket is open"), ident)
                continue                 # an open ticket's worktree is never walked
            newest, size, whole = walk(path, world.uid)
            if not whole:
                p.cannot("it could not be read whole", path)
                continue
            verdict, why = judge_ticket(ident, states, unknown, now, cfg["done_days"], newest)
            if verdict != REMOVE:
                (p.keep if verdict == KEEP else p.cannot)(why, ident)
                continue
            if in_use(path):
                p.keep("a process is working in it", ident)
                continue
            if kind == "ticket":
                removed = p.dry_run or remove_plain(path)
                record(p, kind, path, why, size, removed, "it is still there")
                continue
            how, common = git_kind(path)
            if how == "plain":
                removed = p.dry_run or remove_plain(path)
            elif how == "worktree":
                found = listed(world, common)
                if found is None:
                    p.cannot("its clone could not list its worktrees", path)
                    continue
                if os.path.realpath(path) not in found:
                    p.cannot("its clone does not list it as a worktree", path)
                    continue
                if found[os.path.realpath(path)]:
                    p.keep("its clone has it locked", ident)
                    continue
                why += ", through its clone %s" % common
                removed = True
                if not p.dry_run:
                    code, out = world.git(["--git-dir=" + common, "worktree", "remove",
                                           "--force", path])
                    removed = code == 0 and not os.path.lexists(path)
                    if not removed:
                        why += ": git said %s" % (out[:200] or "nothing")
            else:
                p.cannot("it holds a repository of its own, not a worktree" if how == "clone"
                         else "its .git is not one this sweep can follow", path)
                continue
            if record(p, kind, path, why, size, removed, "it is still there"):
                going.add(ident)

    # A plan's look-tool folder (KIT-572): the worktree's rule, a plain folder, named by its path so it is never
    # mistaken for the plan's worktree in what the pass says.
    for ident, path in tickets["look"]:
        name = os.path.join(folders["look_record_dir"], ident)
        if path is None or not path.startswith("/"):
            p.keep(path or "it could not be read", name)
            continue
        if TICKET_RE.match(ident).group(1) not in cfg["teams"]:
            p.keep("its team is not one the config names", name)
            continue
        if ident in unknown or states[ident][0] == "open":
            (p.cannot if ident in unknown else p.keep)(unknown.get(ident, "its plan's ticket is open"), name)
            continue
        newest, size, whole = walk(path, world.uid)
        if not whole:
            p.cannot("it could not be read whole", path)
            continue
        verdict, why = judge_ticket(ident, states, unknown, now, cfg["done_days"], newest)
        if verdict != REMOVE:
            (p.keep if verdict == KEEP else p.cannot)(why, name)
            continue
        if in_use(path):
            p.keep("a process is working in it", name)
            continue
        record(p, "look", path, why, size, p.dry_run or remove_plain(path), "it is still there")

    idle = cfg["idle_days"] * DAY
    root = folders["session_root"]
    for name, path in own_entries(world, root, PROJECT_RE) if root else []:
        if path is None or not path.startswith("/"):
            p.keep(path or "it could not be read", name)
            continue
        newest, size, whole = walk(path, world.uid)
        if whole and now - newest >= idle and not in_use(path):
            record(p, "session", path, "nothing in it changed for %d days" % ((now - newest) // DAY),
                   size, p.dry_run or remove_plain(path), "it is still there")
            continue
        for sid, spath in own_entries(world, path, SESSION_RE):
            idle_out(p, "session", sid, spath, now, idle, in_use, world.uid)

    tmp = folders["temp_dir"]
    for name, path in own_entries(world, tmp, KIT_TEMP_RE) if tmp else []:
        idle_out(p, "temp", name, path, now, idle, in_use, world.uid)
    trim_logs(world, cfg, p)             # last, so a pass that aborted earlier trimmed nothing
    return finish(p)


def trim_logs(world, cfg, p):
    """A job log past log_max_mb is copied to log.1 (older copies shifted up, the one past
    log_keep dropped) and then emptied in place, so the job that appends to it, which launchd
    opened for it, keeps writing to the same file. Plain files this account owns, and nothing
    else. What a job appends between the copy and the emptying is lost, and that is the
    price of never renaming a file under a writer."""
    limit = cfg["log_max_mb"] * 1024 * 1024
    for pattern in cfg["logs"]:
        fixed = os.sep.join(itertools.takewhile(lambda part: not glob.has_magic(part),
                                                pattern.split(os.sep)[:-1])) or os.sep
        try:
            os.listdir(fixed)
        except FileNotFoundError:
            p.notes.append("no folder %s: no log of %s to trim" % (fixed, pattern))
            continue
        except OSError as exc:
            p.cannot("a log folder could not be listed (%s)" % exc.strerror, fixed)
            continue
        for path in sorted(glob.glob(pattern)):
            try:
                st = os.lstat(path)
            except FileNotFoundError:
                continue
            except OSError:
                p.failed.append(("log", path, "it could not be read"))
                continue
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                p.keep("a log that is not a plain file", path)
            elif not world.owns(path, st):
                p.keep("a log another account owns", path)
            elif st.st_size > limit:
                if p.dry_run or _trim(path, cfg["log_keep"]):
                    p.trimmed.append((path, st.st_size))
                else:
                    p.failed.append(("log", path, "it could not be trimmed"))


def _trim(path, keep):
    try:
        tmp = "%s.1.tmp-%d" % (path, os.getpid())
        if os.path.lexists(tmp):
            os.remove(tmp)
        shutil.copyfile(path, tmp)             # first: a copy that fails drops nothing
        for i in range(keep - 1, 0, -1):
            if os.path.lexists("%s.%d" % (path, i)):
                os.replace("%s.%d" % (path, i), "%s.%d" % (path, i + 1))
        os.replace(tmp, path + ".1")
        with open(path, "r+b") as fh:
            fh.truncate(0)
        return True
    except OSError:
        return False


def idle_out(p, kind, name, path, now, idle, in_use, uid):
    """A session or temp folder goes once nothing in it has changed for `idle` seconds."""
    if path is None or not path.startswith("/"):
        p.keep(path or "it could not be read", name)
        return
    newest, size, whole = walk(path, uid)
    if not whole:
        p.cannot("it could not be read whole", path)
    elif now - newest < idle:
        p.keep("something in it changed under %d days ago" % (idle // DAY), name)
    elif in_use(path):
        p.keep("a process is working in it", name)
    else:
        record(p, kind, path, "nothing in it changed for %d days" % ((now - newest) // DAY),
               size, p.dry_run or remove_plain(path), "it is still there")


def record(p, kind, path, why, size, removed, failed_why):
    if removed:
        p.removed.append((kind, path, why, size))
    else:
        p.failed.append((kind, path, why if ": git said" in why else failed_why))
    return removed


def _names(names, most=8):
    shown = ", ".join(sorted(names)[:most])
    return shown + (", and %d more" % (len(names) - most) if len(names) > most else "")


def finish(p):
    verb = "would remove" if p.dry_run else "removed"
    lines = list(p.notes)
    for kind, path, why, size in p.removed:
        lines.append("%s %s %s: %s (%.1f MB)" % (verb, kind, path, why, size / 1e6))
    trim_verb = "would trim" if p.dry_run else "trimmed"
    for path, size in p.trimmed:
        lines.append("%s log %s: %.1f MB, the whole of it kept as %s.1" % (
            trim_verb, path, size / 1e6, path))
    for kind, path, why in p.failed:
        lines.append("COULD NOT %s %s %s: %s" % ("TRIM" if kind == "log" else "REMOVE", kind,
                                                  path, why))
    for why, names in sorted(p.unknown.items()):
        lines.append("could not judge %d: %s: %s" % (len(names), why, _names(names)))
    for why, names in sorted(p.kept.items()):
        lines.append("kept %d: %s: %s" % (len(names), why, _names(names)))
    lines.append("%s %d (%.1f MB), %s %d logs, kept %d, could not judge %d, failed %d%s" % (
        verb, len(p.removed), sum(r[3] for r in p.removed) / 1e6, trim_verb, len(p.trimmed),
        sum(len(v) for v in p.kept.values()), sum(len(v) for v in p.unknown.values()),
        len(p.failed), " (a dry run: nothing was removed)" if p.dry_run else ""))
    code = EXIT_FAILED if p.failed else EXIT_UNKNOWN if p.unknown else EXIT_OK
    return code, lines


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def account_name(uid):
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return "uid %d" % uid


def main(argv=None, environ=None, world_for=None):
    environ = os.environ if environ is None else environ
    ap = argparse.ArgumentParser(description="Remove what finished dispatcher runs left on the "
                                             "role account (KIT-464).")
    ap.add_argument("--selftest", action="store_true", help="run the offline selftest")
    ap.add_argument("--example-config", action="store_true", help="print an example config")
    sub = ap.add_subparsers(dest="cmd")
    run = sub.add_parser("run", help="one pass")
    run.add_argument("--config", required=True, help="the sweep's JSON config")
    run.add_argument("--dry-run", action="store_true", help="say what would go; remove nothing")
    args = ap.parse_args(argv)
    if args.selftest:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from scratch_run import contained   # KIT-450: its temp folders go when it ends
        return contained(selftest, "sweep-selftest-run-")
    if args.example_config:
        print(json.dumps(EXAMPLE_CONFIG, indent=2))
        return EXIT_OK
    if args.cmd != "run":
        ap.print_usage(sys.stderr)
        return EXIT_USAGE
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        log("CONFIG: %s" % exc)
        return EXIT_USAGE
    found = agent_markers(environ)
    if found and not args.dry_run:
        log("REFUSED: a real pass removes folders, and this is an agent environment (%s set). "
            "A session here would sweep the account it runs as. A person, or the installed "
            "job, runs it. A dry run is allowed: --dry-run." % ", ".join(found))
        return EXIT_REFUSED
    me = account_name(os.getuid())
    if me != cfg["account"]:
        log("REFUSED: this config sweeps the account %s, and this is %s. Run it as that "
            "account." % (cfg["account"], me))
        return EXIT_REFUSED
    try:
        key = credential(cfg, environ)
        world = (world_for or World)(key, environ)
        code, lines = run_pass(world, cfg, dry_run=args.dry_run)
    except ConfigError as exc:
        log("CONFIG: %s" % exc)
        return EXIT_USAGE
    except SweepError as exc:
        log("FAILED: the pass could not complete: %s" % exc)
        return EXIT_FAILED
    for line in lines:
        print("sweep: " + line)
    sys.stdout.flush()
    return code


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
class FakeWorld(World):
    """A stand-in tracker and process list; real folders and real git."""

    def __init__(self, now, tickets=None, teams=(), cwds=(), foreign=(), page=2,
                 tracker_fails=False, cwds_fail=False, git_fails=False, temp="", key_refused=False):
        World.__init__(self, "test-key", {})
        self._now, self.tickets, self.teams, self._cwds = now, dict(tickets or {}), set(teams), cwds
        self.foreign, self.page, self.temp = set(foreign), page, temp
        self.tracker_fails, self.cwds_fail, self.git_fails = tracker_fails, cwds_fail, git_fails
        self.key_refused = key_refused
        self.queries = []

    def now(self):
        return self._now

    def owns(self, path, st):
        return path not in self.foreign and World.owns(self, path, st)

    def cwds(self):
        return None if self.cwds_fail else [os.path.realpath(c) for c in self._cwds] + ["/"]

    def temp_dir(self):
        return self.temp

    def git(self, args):
        if self.git_fails and "remove" in args:
            return 1, "fatal: stand-in refusal"
        return World.git(self, args)

    def gql(self, query, variables):
        self.queries.append((query, variables))
        if self.key_refused:
            raise ConfigError("the tracker refused the key (HTTP 401)")
        if self.tracker_fails:
            raise SweepError("stand-in: the tracker did not answer")
        if "SweepTeams" in query:
            return {"teams": {"nodes": [{"key": k} for k in sorted(self.teams)]}}
        team = variables["filter"]["team"]["key"]["eq"]
        nodes = [dict(self.tickets["%s-%d" % (team, n)], number=n, team={"key": team})
                 for n in variables["filter"]["number"]["in"]
                 if "%s-%d" % (team, n) in self.tickets]
        start = int(variables["after"] or 0)
        return {"issues": {"nodes": nodes[start:start + self.page],
                           "pageInfo": {"hasNextPage": start + self.page < len(nodes),
                                        "endCursor": str(start + self.page)}}}


def selftest():
    import tempfile
    fails, ran = [], [0]

    def check(name, got, want):
        ran[0] += 1
        ok = got == want
        print("[%s] %s%s" % ("PASS" if ok else "FAIL", name,
                             "" if ok else "  (got %r, want %r)" % (got, want)))
        if not ok:
            fails.append(name)

    _cases(os.path.realpath(tempfile.mkdtemp(prefix="sweep-")), check)
    print("\n%d/%d checks passed" % (ran[0] - len(fails), ran[0]))
    return 1 if fails else 0


def _cases(top, check):
    T = time.time()
    OLD, YOUNG = T - 10 * DAY, T - DAY

    def iso(epoch):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))

    def done(at):
        return {"state": {"type": "completed"}, "completedAt": iso(at), "trashed": False}

    def age(path, when):
        for root, dirs, files in os.walk(path, topdown=False):
            for name in dirs + files:
                os.utime(os.path.join(root, name), (when, when), follow_symlinks=False)
        os.utime(path, (when, when), follow_symlinks=False)

    def git(*args):
        r = subprocess.run(["git", "-c", "user.email=sweep@example.com", "-c", "user.name=sweep",
                            "-c", "init.defaultBranch=main"] + list(args),
                           capture_output=True, text=True)
        assert r.returncode == 0, (args, r.stderr)
        return r.stdout

    def tree(base, **files):
        os.makedirs(base, exist_ok=True)
        for name, body in files.items():
            with open(os.path.join(base, name), "w") as fh:
                fh.write(body)
        return base

    def there(*paths):
        return [os.path.lexists(x) for x in paths]

    def build(name):
        """A dispatcher's layout under top/name: a clone, its worktrees, its home's per-ticket
        folders, a Claude Code temp root, and a TMPDIR."""
        base = os.path.join(top, name)
        clone, home = os.path.join(base, "clone"), os.path.join(base, "home")
        ws, sess, tmp = os.path.join(home, "worktrees"), os.path.join(base, "sess"), \
            os.path.join(base, "tmp")
        for d in (clone, ws, sess, tmp):
            os.makedirs(d)
        git("init", "-q", clone)
        git("-C", clone, "commit", "-q", "--allow-empty", "-m", "init")
        cfg = {"account": account_name(os.getuid()), "workspace_dir": ws, "dispatcher_home": home,
               "teams": ["KIT", "HID"], "linear_key_env": "SWEEP_TEST_KEY", "done_days": 3,
               "idle_days": 3, "session_root": sess, "temp_dir": tmp, "logs": [],
               "log_max_mb": 10, "log_keep": 3}
        return base, clone, home, ws, sess, tmp, cfg

    # -- the full layout: every rule and refusal in one dispatcher's folders ----------------
    base, clone, home, ws, sess, tmp, cfg = build("full")
    W = lambda name: os.path.join(ws, name)          # noqa: E731
    H = lambda name: os.path.join(home, name)        # noqa: E731
    for ident in ("KIT-1", "KIT-2", "KIT-3", "KIT-6", "KIT-7"):
        git("-C", clone, "worktree", "add", "-q", "-b", ident.lower(), W(ident))
    git("-C", clone, "worktree", "lock", W("KIT-7"))
    tree(W("KIT-4"), notes="a plain folder")                 # no .git: gone, old
    tree(W("KIT-5"), notes="a plain folder")                 # no .git: gone, young
    git("init", "-q", W("KIT-8"))                             # a clone of its own
    tree(W("KIT-10"))                                         # a .git its clone does not list
    admin = os.path.join(clone, ".git", "worktrees", "stray")
    tree(admin, commondir="../..\n", gitdir="/nowhere/.git\n", HEAD="ref: refs/heads/main\n")
    tree(W("KIT-10"), **{".git": "gitdir: %s\n" % admin})
    tree(W("KIT-11"), **{".git": "gitdir: /nowhere/at/all\n"})  # a .git it cannot follow
    tree(W("OPS-1"))                                          # a team the config does not name
    tree(W("HID-1"))                                          # a team the key cannot see
    tree(W("notes"))                                          # not a ticket's name
    target = tree(os.path.join(base, "elsewhere", "KIT-9"), keep="me")
    os.symlink(target, W("KIT-9"))                            # a symlink, never followed
    for ident in ("KIT-1", "KIT-2", "KIT-6", "KIT-12", "KIT-13", "KIT-14"):
        os.makedirs(os.path.join(H(ident), "attachments"))
    tree(os.path.join(H("KIT-12"), "attachments"), **{"shot.png": "x"})
    for d in (W("KIT-4"), W("KIT-10"), W("KIT-11"), W("OPS-1"), W("HID-1"), H("KIT-12"),
              H("KIT-13"), H("KIT-14"), W("KIT-9")):
        age(d, OLD)
    tickets = {"KIT-1": done(OLD), "KIT-2": {"state": {"type": "started"}},
               "KIT-3": done(YOUNG), "KIT-6": done(OLD), "KIT-7": done(OLD), "KIT-8": done(OLD),
               "KIT-9": done(OLD), "KIT-10": done(OLD), "KIT-11": done(OLD),
               "KIT-12": {"state": {"type": "canceled"}, "canceledAt": iso(OLD), "trashed": False},
               "KIT-13": dict(done(YOUNG), trashed=True),     # deleted: gone, its folder old
               "KIT-14": done(OLD), "KIT-5": None}
    tickets = {k: v for k, v in tickets.items() if v is not None}   # KIT-4, KIT-5: never existed
    # Sessions: an old project folder goes whole; a mixed one loses only its old session.
    p_old, p_mixed, p_empty = (os.path.join(sess, n) for n in ("-opt-x-KIT-1", "-opt-x-KIT-2",
                                                               "-opt-x-empty"))
    s_old, s_young = "11111111-2222-3333-4444-555555555555", "66666666-7777-8888-9999-aaaaaaaaaaaa"
    tree(os.path.join(p_old, s_old, "scratchpad"), a="1")
    tree(os.path.join(p_mixed, s_old, "tasks"), b="2")
    tree(os.path.join(p_mixed, s_young, "tasks"), c="3")
    os.makedirs(p_empty)
    tree(os.path.join(sess, "bash-edit-diff"))
    tree(sess, **{"cache-break-state-%s.json" % s_old: "{}"})
    for d in (p_old, os.path.join(p_mixed, s_old), p_empty, os.path.join(sess, "bash-edit-diff"),
              os.path.join(sess, "cache-break-state-%s.json" % s_old)):
        age(d, OLD)
    # Temp: the kit's run folders by name and age; anything else is not the sweep's.
    t_old = tree(os.path.join(tmp, "watch-selftest-run-ab12cd34"), x="1")
    t_setup = tree(os.path.join(tmp, "stage-e-setup-selftest-cd34ef56"), x="1")
    t_dry = tree(os.path.join(tmp, "stage-e-dry-zz99yy88"), y="2")
    t_voice = os.path.join(tmp, "pipeline-voice-vv11ww22")         # a model call's HOME
    tree(os.path.join(t_voice, ".claude"), y="2")
    t_young = tree(os.path.join(tmp, "scratch-run-qq11ww22"), z="3")
    t_other = tree(os.path.join(tmp, "tmpab12cd34"), w="4")
    t_short = tree(os.path.join(tmp, "build-run-abc"), w="4")      # not mkdtemp's shape
    t_foreign = tree(os.path.join(tmp, "pipeline-morning-aa11bb22"), v="5")
    os.symlink(target, os.path.join(tmp, "scratch-run-link1234"))
    for d in (t_old, t_setup, t_dry, t_voice, t_other, t_short, t_foreign):
        age(d, OLD)

    def world(**over):
        kw = dict(now=T, tickets=tickets, teams=("KIT",), cwds=(os.path.join(W("KIT-6"), "src"),),
                  foreign=(t_foreign,), temp=tmp)
        kw.update(over)
        return FakeWorld(**kw)

    gone_now = [W("KIT-1"), W("KIT-4"), H("KIT-1"), H("KIT-12"), H("KIT-13"), H("KIT-14"),
                p_old, p_empty, os.path.join(p_mixed, s_old), t_old, t_setup, t_dry, t_voice]
    stays = [W("KIT-2"), W("KIT-3"), W("KIT-5"), W("KIT-6"), W("KIT-7"), W("KIT-8"),
             W("KIT-9"), W("KIT-10"), W("KIT-11"), W("OPS-1"), W("HID-1"), W("notes"), H("KIT-2"),
             H("KIT-6"), os.path.join(p_mixed, s_young), os.path.join(sess, "bash-edit-diff"),
             os.path.join(sess, "cache-break-state-%s.json" % s_old), t_young, t_other, t_short,
             t_foreign, os.path.join(tmp, "scratch-run-link1234"), target]

    def removals(lines, verb):
        return sorted(m.group(1) for m in (re.match(
            r"^%s (?:worktree|ticket|session|temp) (/\S+?): " % verb, line) for line in lines) if m)

    w = world()
    code, lines = run_pass(w, cfg, dry_run=True)
    check("a dry run removes nothing", there(*gone_now + stays), [True] * len(gone_now + stays))
    check("…and says each thing it would remove", removals(lines, "would remove"),
          sorted(gone_now))
    check("…and what it could not judge, so it exits 4", code, EXIT_UNKNOWN)
    asked = [q[1]["filter"]["team"]["key"]["eq"] if q[1] else "teams" for q in w.queries]
    check("the tracker is asked which teams the key sees, then each seen team's tickets, a page "
          "at a time", (asked[0], sorted(set(asked[1:])), len(asked)), ("teams", ["KIT"], 7))

    w = world()
    code, lines = run_pass(w, cfg)
    said = "\n".join(lines)
    check("a real pass removes exactly what was due", (there(*gone_now), there(*stays)),
          ([False] * len(gone_now), [True] * len(stays)))
    check("a done ticket's worktree went through its clone, which no longer lists it",
          (os.path.realpath(W("KIT-1")) in git("-C", clone, "worktree", "list", "--porcelain"),
           "through its clone %s" % os.path.realpath(os.path.join(clone, ".git")) in said),
          (False, True))
    check("…and its branch is kept: a worktree's removal is not a branch's",
          "kit-1" in git("-C", clone, "branch", "--list", "kit-1"), True)
    for want in ("kept 1: its ticket is open: KIT-2",
                 "its ticket was done under 3 days ago: KIT-3",
                 "its ticket is gone, but the folder changed under 3 days ago: KIT-5",
                 "a process is working in it: KIT-6",
                 "its worktree is still there: KIT-2, KIT-6",
                 "its clone has it locked: KIT-7",
                 "a symlink: KIT-9, scratch-run-link1234",
                 "its team is not one the config names: OPS-1",
                 "another account's folder: pipeline-morning-aa11bb22",
                 "something in it changed under 3 days ago: %s, scratch-run-qq11ww22" % s_young,
                 "could not judge 1: it holds a repository of its own, not a worktree: %s" % W("KIT-8"),
                 "its clone does not list it as a worktree: %s" % W("KIT-10"),
                 "its .git is not one this sweep can follow: %s" % W("KIT-11"),
                 "the tracker key cannot see team HID: HID-1"):
        check("the pass says: %s" % want, want in said, True)
    check("a pass that left what it could not judge exits 4", code, EXIT_UNKNOWN)
    check("a deleted ticket keeps its state: trashed is read as gone",
          any("KIT-13 is gone from the tracker" in line for line in lines), True)

    code, lines = run_pass(world(), cfg)
    check("the next pass has nothing left to remove", (code, removals(lines, "removed")),
          (EXIT_UNKNOWN, []))

    # -- a folder a killed run locked against itself (chmod 000) -----------------------------
    base, clone, home, ws, sess, tmp, cfg_l = build("locked")
    t_locked = tree(os.path.join(tmp, "hook-selftest-run-lock1234"), x="1")
    os.makedirs(os.path.join(t_locked, "locked", "deep"))
    age(t_locked, OLD)
    os.chmod(os.path.join(t_locked, "locked"), 0)
    code, lines = run_pass(FakeWorld(T, temp=tmp), cfg_l)
    check("a locked folder's last change is when it was locked: just now, so it stays",
          (code, there(t_locked)), (EXIT_OK, [True]))
    code, lines = run_pass(FakeWorld(T + 10 * DAY, temp=tmp), cfg_l)
    check("…and once that is past the line, it goes, lock and all", (code, there(t_locked)),
          (EXIT_OK, [False]))

    # -- a source it could not read removes nothing that needs it ---------------------------
    base, clone, home, ws, sess, tmp, cfg2 = build("blind")
    for ident in ("KIT-1", "KIT-2"):
        git("-C", clone, "worktree", "add", "-q", "-b", ident.lower(), os.path.join(ws, ident))
        os.makedirs(os.path.join(home, ident))
        age(os.path.join(home, ident), OLD)
    old_tmp = tree(os.path.join(tmp, "scratch-run-old12345"), a="1")
    age(old_tmp, OLD)
    two = {"KIT-1": done(OLD), "KIT-2": done(OLD)}
    code, lines = run_pass(FakeWorld(T, two, ("KIT",), tracker_fails=True, temp=tmp), cfg2)
    check("a tracker that does not answer removes no worktree or ticket folder, and says so",
          (code, there(os.path.join(ws, "KIT-1"), os.path.join(home, "KIT-1")),
           any(line.startswith("could not judge 2: the tracker could not be read") for line in lines)),
          (EXIT_UNKNOWN, [True, True], True))
    check("…while a kit temp folder, which needs no tracker, still goes", there(old_tmp), [False])
    old_tmp = tree(os.path.join(tmp, "scratch-run-old67890"), a="1")
    age(old_tmp, OLD)
    code, lines = run_pass(FakeWorld(T, two, ("KIT",), cwds_fail=True, temp=tmp), cfg2)
    check("working folders that cannot be read remove nothing at all, and say so",
          (code, there(os.path.join(ws, "KIT-1"), old_tmp),
           "could not judge 1: this account's working folders could not be read" in lines[0]),
          (EXIT_UNKNOWN, [True, True], True))
    code, lines = run_pass(FakeWorld(T, two, ("KIT",), git_fails=True, temp=tmp), cfg2)
    check("a worktree git will not remove fails the pass: exit 1, said, and its ticket folder "
          "stays", (code, there(os.path.join(ws, "KIT-1"), os.path.join(home, "KIT-1")),
                    any(line.startswith("COULD NOT REMOVE worktree") and "stand-in refusal" in line
                        for line in lines)),
          (EXIT_FAILED, [True, True], True))
    code, lines = run_pass(FakeWorld(T, two, ("KIT",), page=1, temp=tmp), cfg2)
    check("the tracker is read page by page, and everything due goes: exit 0",
          (code, there(os.path.join(ws, "KIT-1"), os.path.join(ws, "KIT-2"),
                       os.path.join(home, "KIT-1"), os.path.join(home, "KIT-2"), old_tmp)),
          (EXIT_OK, [False] * 5))
    many = dict(("KIT-%d" % n, done(OLD)) for n in range(1, MAX_PAGES + 3))
    for n in range(1, MAX_PAGES + 3):
        os.makedirs(os.path.join(home, "KIT-%d" % n), exist_ok=True)
        age(os.path.join(home, "KIT-%d" % n), OLD)
    code, lines = run_pass(FakeWorld(T, many, ("KIT",), page=1, temp=tmp), cfg2)
    check("a read that runs past %d pages is not complete: nothing of that team goes" % MAX_PAGES,
          (code, os.path.exists(os.path.join(home, "KIT-3"))), (EXIT_UNKNOWN, True))
    gone = [os.path.join(home, "KIT-%d" % n) for n in range(1, MAX_PAGES + 3)]
    code, lines = run_pass(FakeWorld(T, {}, ("KIT",), temp=tmp), cfg2)
    check("tickets the team no longer has are gone, and their old folders go",
          (code, there(*gone)), (EXIT_OK, [False] * len(gone)))
    code, lines = run_pass(FakeWorld(T, {}, ("KIT",), temp=tmp), dict(cfg2, session_root=os.path.join(base, "absent")))
    check("a session root that is not there is only empty: exit 0, said",
          (code, any("is not there: nothing to sweep" in line for line in lines)), (EXIT_OK, True))
    code, lines = run_pass(FakeWorld(T, {}, ("KIT",), temp=tmp), dict(cfg2, workspace_dir=os.path.join(base, "absent")))
    check("a worktree folder that is not there is not judged as empty: exit 4",
          (code, any("workspace_dir is not there" in line for line in lines)), (EXIT_UNKNOWN, True))
    code, lines = run_pass(FakeWorld(T, {}, ("KIT",), foreign=(home,), temp=tmp), cfg2)
    check("a configured folder another account owns is not read", (code, any(
        "dispatcher_home is not a folder this account owns" in line for line in lines)),
        (EXIT_UNKNOWN, True))
    link = os.path.join(base, "home-link")
    os.symlink(home, link)
    try:
        run_pass(FakeWorld(T, {}, ("KIT",), temp=tmp), dict(cfg2, dispatcher_home=link))
        check("a configured folder through a symlink is a config error", "no error", "ConfigError")
    except ConfigError as exc:
        check("a configured folder through a symlink is a config error", "symlink" in str(exc), True)

    # -- job logs: past the line they are copied to log.1 and emptied in place ----------------
    gbase, _gclone, _ghome, _gws, _gsess, gtmp, cfg_g = build("logs")
    logs = os.path.join(gbase, "jobs")
    os.makedirs(os.path.join(logs, "sub"))
    cfg_g.update(logs=[os.path.join(logs, "*.log"), os.path.join(logs, "*", "*.log")],
                 log_max_mb=1, log_keep=2)
    big = b"x" * (1024 * 1024 + 500)

    def put(path, data):
        with open(path, "wb") as fh:
            fh.write(data)
    put(os.path.join(logs, "poller.log"), big)
    put(os.path.join(logs, "sub", "job.log"), big)
    put(os.path.join(logs, "small.log"), b"under the line")
    put(os.path.join(logs, "notes.txt"), big)                       # not a .log
    put(os.path.join(logs, "theirs.log"), big)                      # another account's
    put(os.path.join(gbase, "real.log"), big)
    os.symlink(os.path.join(gbase, "real.log"), os.path.join(logs, "link.log"))
    gw = lambda **kw: FakeWorld(T, temp=gtmp, foreign=(os.path.join(logs, "theirs.log"),), **kw)  # noqa: E731
    sizes = lambda: {n: os.path.getsize(os.path.join(logs, n)) for n in         # noqa: E731
                     ("poller.log", "small.log", "notes.txt", "theirs.log", "link.log")}
    before = sizes()
    code, lines = run_pass(gw(), dict(cfg_g), dry_run=True)
    check("a dry run says which logs it would trim, and changes none",
          (sizes() == before, sorted(l.split(" ")[3].rstrip(":") for l in lines
                                     if l.startswith("would trim log "))),
          (True, sorted([os.path.join(logs, "poller.log"), os.path.join(logs, "sub", "job.log")])))
    # A writer that holds the log open for appending, as launchd holds a job's.
    writer = open(os.path.join(logs, "poller.log"), "ab")
    code, lines = run_pass(gw(), dict(cfg_g))
    writer.write(b"after the trim\n")
    writer.close()
    check("a log past the line is copied whole to log.1 and emptied in place",
          (os.path.getsize(os.path.join(logs, "poller.log.1")) == len(big),
           open(os.path.join(logs, "poller.log"), "rb").read(),
           os.path.getsize(os.path.join(logs, "sub", "job.log")),
           os.path.exists(os.path.join(logs, "sub", "job.log.1"))),
          (True, b"after the trim\n", 0, True))
    check("…what is under the line, not a .log, another account's, or a symlink is left",
          {k: v for k, v in sizes().items() if k != "poller.log"},
          {k: v for k, v in before.items() if k != "poller.log"})
    check("…and a pass that trimmed all it should exits 0, saying what it did",
          (code, any(l.startswith("trimmed log %s" % os.path.join(logs, "poller.log")) for l in lines)),
          (EXIT_OK, True))
    for _ in range(2):                                   # keep=2: two older copies, no third
        put(os.path.join(logs, "poller.log"), big)
        run_pass(gw(), dict(cfg_g))
    check("only log_keep older copies are kept: the oldest is dropped",
          (os.path.exists(os.path.join(logs, "poller.log.1")),
           os.path.exists(os.path.join(logs, "poller.log.2")),
           os.path.exists(os.path.join(logs, "poller.log.3"))), (True, True, False))
    put(os.path.join(logs, "poller.log"), big)
    cfg_off = dict(cfg_g, logs=[])
    run_pass(gw(), cfg_off)
    check("with no logs configured, none is trimmed", os.path.getsize(os.path.join(logs, "poller.log")),
          len(big))
    os.chmod(logs, 0o500)                                # nothing can be written beside the log
    try:
        code, lines = run_pass(gw(), dict(cfg_g))
    finally:
        os.chmod(logs, 0o700)
    check("a log that cannot be trimmed fails the pass: exit 1, said",
          (code, any(l.startswith("COULD NOT TRIM log") for l in lines)), (EXIT_FAILED, True))
    put(os.path.join(logs, "poller.log.1"), b"older copy")
    put(os.path.join(logs, "poller.log"), big)
    real_copy = shutil.copyfile
    shutil.copyfile = lambda a, b: (_ for _ in ()).throw(OSError(28, "No space left on device"))
    try:
        code, lines = run_pass(gw(), dict(cfg_g))
    finally:
        shutil.copyfile = real_copy
    check("a copy that fails drops no older copy, and leaves the log as it was",
          (code, open(os.path.join(logs, "poller.log.1"), "rb").read(),
           os.path.getsize(os.path.join(logs, "poller.log")) == len(big)),
          (EXIT_FAILED, b"older copy", True))
    unreadable = os.path.join(logs, "poller.log")
    real_lstat = os.lstat

    def lstat_refusing(path, *a, **k):
        if path == unreadable:
            raise PermissionError(13, "Permission denied")
        return real_lstat(path, *a, **k)
    os.lstat = lstat_refusing
    try:
        code, lines = run_pass(gw(), dict(cfg_g))
    finally:
        os.lstat = real_lstat
    check("a log that cannot be read fails the pass: exit 1, said",
          (code, any(l.startswith("COULD NOT TRIM log %s: it could not be read" % unreadable)
                     for l in lines)), (EXIT_FAILED, True))
    locked_dir = os.path.join(gbase, "closed")
    os.makedirs(locked_dir)
    put(os.path.join(locked_dir, "x.log"), big)
    os.chmod(locked_dir, 0)
    try:
        code, lines = run_pass(gw(), dict(cfg_g, logs=[os.path.join(locked_dir, "*.log")]))
        locked_said = any("a log folder could not be listed" in l for l in lines)
    finally:
        os.chmod(locked_dir, 0o700)
    check("a log folder that cannot be listed is said and exits 4, not read as no logs",
          (code, locked_said), (EXIT_UNKNOWN, True))
    code, lines = run_pass(gw(), dict(cfg_g, logs=[os.path.join(gbase, "nothing", "*.log")]))
    check("a log folder that is not there is only said: exit 0",
          (code, any("no folder" in l for l in lines)), (EXIT_OK, True))

    # -- a plan's look-tool folder: the worktree's rule, by the plan's ticket (KIT-572) ---------
    lbase, _lclone, _lhome, _lws, _lsess, ltmp, cfg_lk = build("look")
    look_dir = os.path.join(lbase, "look-records")
    L = lambda name: os.path.join(look_dir, name)          # noqa: E731
    for ident in ("KIT-1", "KIT-2", "KIT-3", "KIT-4", "KIT-5", "KIT-6", "KIT-8", "OPS-1", "HID-1"):
        tree(os.path.join(L(ident), "001-20261110T000000Z"), **{"c1--desktop--default--home.png": "png"})
        tree(L(ident), **{"001-20261110T000000Z.json": "{}"})
    tree(L("notes"))                                                # not a ticket's name
    look_target = tree(os.path.join(lbase, "elsewhere", "KIT-9"), keep="me")
    os.symlink(look_target, L("KIT-9"))                            # a symlink, never followed
    for d in (L("KIT-1"), L("KIT-3"), L("KIT-4"), L("KIT-6"), L("KIT-8"), L("OPS-1"), L("HID-1"), L("notes")):
        age(d, OLD)
    lk_tickets = {"KIT-1": done(OLD), "KIT-2": {"state": {"type": "started"}}, "KIT-3": done(YOUNG),
                  "KIT-6": done(OLD), "KIT-8": done(OLD), "KIT-9": done(OLD)}       # KIT-4, KIT-5: gone
    cfg_lk = dict(cfg_lk, look_record_dir=look_dir)

    def lworld(**over):
        kw = dict(now=T, tickets=lk_tickets, teams=("KIT",), temp=ltmp, foreign=(L("KIT-8"),),
                  cwds=(os.path.join(L("KIT-6"), "001-20261110T000000Z"),))
        kw.update(over)
        return FakeWorld(**kw)
    look_gone = [L("KIT-1"), L("KIT-4")]
    look_stays = [L("KIT-2"), L("KIT-3"), L("KIT-5"), L("KIT-6"), L("KIT-8"), L("KIT-9"), L("OPS-1"), L("HID-1"),
                  L("notes"), look_target]
    code, lines = run_pass(lworld(), cfg_lk, dry_run=True)
    check("a dry run names each plan's look folder it would remove, and removes none",
          (sorted(l.split(" ")[3].rstrip(":") for l in lines if l.startswith("would remove look ")),
           there(*look_gone + look_stays)), (sorted(look_gone), [True] * len(look_gone + look_stays)))
    code, lines = run_pass(lworld(), cfg_lk)
    said = "\n".join(lines)
    check("a real pass removes a done plan's look folder, and a gone plan's untouched one, records and screenshots "
          "and all, and nothing else", (there(*look_gone), there(*look_stays)), ([False, False], [True] * len(look_stays)))
    for want in ("its plan's ticket is open: %s" % L("KIT-2"),
                 "its ticket was done under 3 days ago: %s" % L("KIT-3"),
                 "its ticket is gone, but the folder changed under 3 days ago: %s" % L("KIT-5"),
                 "a process is working in it: %s" % L("KIT-6"),
                 "another account's folder: %s" % L("KIT-8"),
                 "a symlink: %s" % L("KIT-9"),
                 "its team is not one the config names: %s" % L("OPS-1"),
                 "could not judge 1: the tracker key cannot see team HID: %s" % L("HID-1")):
        check("the pass says, of a look folder: %s" % want, want in said, True)
    check("…and a look folder it could not judge makes the pass exit 4", code, EXIT_UNKNOWN)
    if os.geteuid() != 0:
        shut = tree(os.path.join(L("KIT-12"), "001-20261110T000000Z"), x="1")
        age(L("KIT-12"), OLD)
        os.chmod(shut, 0o300)                                  # it can be entered, not listed: never judged as empty
        lk_tickets["KIT-12"] = done(OLD)
        try:
            code, lines = run_pass(lworld(), cfg_lk)
        finally:
            os.chmod(shut, 0o700)
        check("a look folder that cannot be read whole is not judged, and is said",
              (there(L("KIT-12")), any(l.startswith("could not judge") and "it could not be read whole" in l
                                        and L("KIT-12") in l for l in lines)), ([True], True))
    tree(L("KIT-11"), x="1")
    age(L("KIT-11"), OLD)
    lk_tickets["KIT-11"] = done(OLD)
    code, lines = run_pass(lworld(tracker_fails=True), cfg_lk)
    check("a tracker that does not answer removes no look folder, and says so",
          (code, there(L("KIT-11")), any(l.startswith("could not judge") and L("KIT-11") in l for l in lines)),
          (EXIT_UNKNOWN, [True], True))
    code, lines = run_pass(lworld(), dict(cfg_lk, look_record_dir=""))
    check("with no look_record_dir set no look folder is read, and the pass says so",
          (there(L("KIT-11")), "look_record_dir is not set: no look-tool records are swept" in lines), ([True], True))
    code, lines = run_pass(lworld(), dict(cfg_lk, look_record_dir=os.path.join(lbase, "no-records-yet")))
    check("a look_record_dir that is not there yet is only empty: exit 0, said",
          (code, any("look_record_dir %s is not there: nothing to sweep" % os.path.join(lbase, "no-records-yet") in l
                     for l in lines)), (EXIT_OK, True))
    look_link = os.path.join(lbase, "records-link")
    os.symlink(look_dir, look_link)
    try:
        run_pass(lworld(), dict(cfg_lk, look_record_dir=look_link))
        check("a look_record_dir through a symlink is a config error", "no error", "ConfigError")
    except ConfigError as exc:
        check("a look_record_dir through a symlink is a config error", "symlink" in str(exc), True)
    check("…and nothing was removed through it", there(L("KIT-11")), [True])

    # -- config ------------------------------------------------------------------------------
    conf = os.path.join(top, "sweep.json")

    def conf_error(**over):
        doc = dict(cfg2)
        doc.update(over)
        doc = {k: v for k, v in doc.items() if v is not None}
        with open(conf, "w") as fh:
            json.dump(doc, fh)
        try:
            load_config(conf)
            return None
        except ConfigError as exc:
            return str(exc).split(":")[0]

    check("the example config is one this script reads",
          conf_error(**dict(EXAMPLE_CONFIG)), None)
    check("each config fault is named", [conf_error(**bad) for bad in (
        {"workspace_dir": "relative/worktrees"}, {"dispatcher_home": "/"},
        {"workspace_dir": "/opt"}, {"teams": []}, {"teams": ["eng"]},
        {"linear_key_env": "lin_api_123"}, {"done_days": 0}, {"idle_days": True},
        {"surprise": 1}, {"account": None}, {"workspace_dir": cfg2["dispatcher_home"]},
        {"logs": ["relative/x.log"]}, {"logs": ["/a/b/x.txt"]}, {"logs": ["/a/**/x.log"]},
        {"logs": ["/x.log"]}, {"logs": "x"}, {"log_max_mb": 0}, {"log_keep": 21},
        {"look_record_dir": "relative/records"}, {"look_record_dir": "/opt"},
        {"look_record_dir": cfg2["workspace_dir"]}, {"look_record_dir": cfg2["dispatcher_home"]})],
        ["config 'workspace_dir' must be an absolute path",
         "config 'dispatcher_home' is /", "config 'workspace_dir' is /opt",
         "config 'teams' must be a non-empty list of team KEYS, e.g. [\"ENG\"]",
         "config 'teams' must be a non-empty list of team KEYS, e.g. [\"ENG\"]",
         "config 'linear_key_env' must be an ENV VAR NAME, never a value",
         "config 'done_days' must be a whole number of days from 1 to 365",
         "config 'idle_days' must be a whole number of days from 1 to 365",
         "unknown config key(s)", "config needs 'account'",
         "config 'workspace_dir' and 'dispatcher_home' must be two folders"]
        + ["config 'logs' entry"] * 4 + ["config 'logs' must be a list of absolute glob patterns",
                                          "config 'log_max_mb' must be a whole number from 1 to 10240",
                                          "config 'log_keep' must be a whole number from 1 to 20",
                                          "config 'look_record_dir' must be an absolute path",
                                          "config 'look_record_dir' is /opt"]
        + ["config 'look_record_dir' must be its own folder, not the worktree folder or the dispatcher's home"] * 2)

    # -- the command line: refusals, and a real run through main() --------------------------
    with open(conf, "w") as fh:
        json.dump(cfg2, fh)
    old_tmp = tree(os.path.join(tmp, "scratch-run-cli12345"), a="1")
    age(old_tmp, OLD)
    key = {"SWEEP_TEST_KEY": "k"}
    made = []

    def fake_world(api_key, environ):
        made.append(api_key)
        return FakeWorld(T, {}, ("KIT",), temp=tmp)

    for name, environ, argv, want in (
            ("an agent environment is refused a real pass",
             dict(key, CLAUDECODE=""), ["run", "--config", conf], EXIT_REFUSED),
            ("…every marker counts, even set empty",
             dict(key, AI_AGENT=""), ["run", "--config", conf], EXIT_REFUSED),
            ("…and may still dry-run", dict(key, CLAUDECODE="1"),
             ["run", "--config", conf, "--dry-run"], EXIT_OK),
            ("a missing tracker key is a config error", {}, ["run", "--config", conf], EXIT_USAGE),
            ("no command is a usage error", key, [], EXIT_USAGE)):
        quiet = _quietly(main, argv, environ, fake_world)
        check(name, quiet, want)
    check("…and the dry run left the folder", there(old_tmp), [True])
    with open(conf, "w") as fh:
        json.dump(dict(cfg2, account="_nobody_" + account_name(os.getuid())), fh)
    check("another account than the config names is refused, even a dry run",
          _quietly(main, ["run", "--config", conf, "--dry-run"], key, fake_world), EXIT_REFUSED)
    with open(conf, "w") as fh:
        json.dump(cfg2, fh)
    check("a real run as the named account removes what is due, with the key from its env var",
          (_quietly(main, ["run", "--config", conf], key, fake_world), there(old_tmp), made[-1]),
          (EXIT_OK, [False], "k"))
    os.makedirs(os.path.join(home, "KIT-77"))
    age(os.path.join(home, "KIT-77"), OLD)
    check("a key the tracker refuses is a credential fault, exit 2, and nothing goes",
          (_quietly(main, ["run", "--config", conf], key,
                    lambda api_key, environ: FakeWorld(T, {}, ("KIT",), temp=tmp, key_refused=True)),
           there(os.path.join(home, "KIT-77"))), (EXIT_USAGE, [True]))

    # The real client's reading of the tracker's answers, with urlopen stood in for.
    import io
    real_urlopen = urllib.request.urlopen

    def answered(how):
        def urlopen(req, timeout=None):
            if isinstance(how, int):
                raise urllib.error.HTTPError(LINEAR_API, how, "stand-in", {}, io.BytesIO(b""))
            return io.BytesIO(json.dumps(how).encode())
        urllib.request.urlopen = urlopen
        try:
            World("k").gql(Q_TEAMS, {})
            return "answered"
        except ConfigError:
            return "credential"
        except SweepError:
            return "could not read"
        finally:
            urllib.request.urlopen = real_urlopen
    check("the tracker's refusal of the key is a credential fault; any other failure is not",
          [answered(h) for h in (401, 403, {"errors": [{"message": "Authentication required",
                                                         "extensions": {"code": "AUTHENTICATION_ERROR"}}]},
                                 500, {"errors": [{"message": "rate limited"}]},
                                 {"data": {"teams": {"nodes": []}}})],
          ["credential", "credential", "credential", "could not read", "could not read", "answered"])

    # -- the kit's own temp folders: every prefix its scripts use is one the sweep takes ------
    here = os.path.dirname(os.path.abspath(__file__))
    kit = os.path.dirname(here)
    found = set()
    for folder in [here, os.path.join(kit, ".claude", "hooks")] + [
            os.path.join(kit, ".claude", "skills", s) for s in
            sorted(os.listdir(os.path.join(kit, ".claude", "skills")))
            if os.path.isdir(os.path.join(kit, ".claude", "skills", s))]:
        for name in sorted(os.listdir(folder)):
            if name.endswith(".py") and name != "scratch_run.py":   # its own cases nest
                with open(os.path.join(folder, name), encoding="utf-8") as fh:
                    found.update(re.findall(r'\bcontained\([^\n]*?"([a-z0-9.-]+-)"\)', fh.read()))
    check("every battery's run-folder prefix in the kit is one the temp rule takes (%d read)"
          % len(found), (len(found) > 40, sorted(p for p in found
                                                  if not KIT_TEMP_RE.match(p + "ab12cd34"))),
          (True, []))
    made_by = {}
    for prefix, script in JOB_PREFIXES.items():
        with open(os.path.join(here, script), encoding="utf-8") as fh:
            text = fh.read()
        made_by[prefix] = any(form % prefix in text
                              for form in ('mkdtemp(prefix="%s")', 'scratch_run("%s"'))
    check("each job's prefix is still the one its script makes, and the rule takes it",
          (made_by, [p for p in JOB_PREFIXES if not KIT_TEMP_RE.match(p + "ab12cd34")]),
          ({p: True for p in JOB_PREFIXES}, []))

    # -- what it never does ------------------------------------------------------------------
    with open(os.path.abspath(__file__), encoding="utf-8") as fh:
        source = fh.read()
    code_only = source.split("\ndef selftest():")[0]
    check("its only tracker calls are its two reads: no mutation anywhere",
          (re.findall(r"\bmutation\b", code_only), re.findall(r"world\.gql\((Q_[A-Z]+)", code_only)),
          ([], ["Q_TEAMS", "Q_TICKETS"]))
    check("it never runs rm: folders go through Python's removal or git's",
          re.findall(r"""["']rm["']""", code_only), [])


def _quietly(fn, *args):
    """fn(*args) with its output swallowed: the refusals are read by their exit codes."""
    out, err = sys.stdout, sys.stderr
    sink = open(os.devnull, "w")
    sys.stdout = sys.stderr = sink
    try:
        return fn(*args)
    finally:
        sys.stdout, sys.stderr = out, err
        sink.close()


if __name__ == "__main__":
    sys.exit(main())
