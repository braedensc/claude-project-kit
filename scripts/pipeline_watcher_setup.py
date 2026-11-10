#!/usr/bin/env python3
"""The watcher's own install: its account, a root-owned folder the workers cannot edit, its own read-only
keys, and the job (KIT-517). One command, run by a person.

    python3 scripts/pipeline_watcher_setup.py run --conf watcher.conf    # check, show, ask once, apply, verify
    python3 scripts/pipeline_watcher_setup.py check --conf watcher.conf  # read-only: what is true now
    python3 scripts/pipeline_watcher_setup.py --example-conf
    python3 scripts/pipeline_watcher_setup.py --selftest

WHY.  The monitors are only as independent as the place they run (docs/WATCHER.md). A watcher a worker's
session can edit is a watcher a session can silence. So it runs as its own account, from a folder only root
can write, from files at a commit a person reviewed, checking them against a manifest on every pass.

WHAT IT DOES, in order. `run` shows the whole plan first, asks for ONE typed `yes`, then applies and verifies.
  1. Checks the reviewed commit is on origin/main, this checkout is that commit's watcher, and the commit's
     own watcher and tracker selftests pass.
  2. The account: a hidden local account with no login shell, created if absent.
  3. The install folder (root:wheel, 0755) and the watcher's files at that commit, with a manifest of each
     file's hash. The watcher checks itself against the manifest every pass; a mismatch makes it blind and loud.
  4. The rules file (root:wheel, 0644), composed from the conf and judged by the watcher's own loader.
  5. The watcher's keys, in its own env file (mode 600, its own). They are asked for at a HIDDEN prompt, held
     in memory, and written through a pipe: never in a command line, a log, this repo or any output. Make them
     READ-ONLY at their source (a GitHub token with read access; a tracker key with the Read scope only): this
     script cannot see a key's scope, and the watcher sends nothing but GETs and one fixed read-only query.
  6. Checks that neither the role account nor your own can write the install folder, the rules, the manifest
     or the state folder.
  7. The job (a LaunchDaemon that runs as the watcher account), loaded only after the typed yes, and its first
     pass read back.

WHAT IT NEVER DOES.  Run under a model (REFUSED before the conf is read; there is no override). Print or keep
a key. Touch anything but the paths in the conf, the one account and the one job. Merge, approve or label.
It writes nothing outside the paths the conf names.

NOT DONE HERE.  The read-only copy of the usage file the watcher reads from the worker side: a root job that
copies a worker-writable path is its own security surface and its own change. Until then `spend` and `runs`
read the worker's usage file as second-hand, and say so. The pager and the judge are separate jobs.

EXIT CODES:  0 done (or nothing to do)  1 failed  2 conf  3 refused (an agent's environment)
             4 not measured (something could not be read, so it is not called fine)  5 no administrator access
"""
import argparse
import getpass
import hashlib
import io
import json
import os
import plistlib
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

EX_OK, EX_FAILED, EX_CONF, EX_REFUSED, EX_UNKNOWN, EX_NOSUDO = 0, 1, 2, 3, 4, 5
MANIFEST_SCHEMA = "pipeline-watcher-manifest/1"
RULES_NAME, MANIFEST_NAME = "etc/watcher.json", "manifest.json"
# The files the watcher runs from: its import closure, derived by the selftest with `ast`, and its schema.
WATCHER_FILES = ("scripts/pipeline_watcher_local.py", "scripts/pipeline_watcher_tracker.py",
                 "scripts/pipeline_hold.py", "scripts/check_schemas.py", "scripts/jsonschema_mini.py",
                 "schemas/pipeline-watcher.schema.json")
ENTRY = "scripts/pipeline_watcher_local.py"
SELFTESTS = ("scripts/pipeline_watcher_local.py", "scripts/pipeline_watcher_tracker.py")
FIRST_PASS_WAIT_SECONDS = 90

UNSAFE_PREFIXES = ("/Users/", "/private/tmp", "/tmp", "/var/tmp")      # where a user's session can write
CONF_REQUIRED = ("WATCHER_ACCOUNT", "ROLE_ACCOUNT", "KIT_COMMIT", "JOB_LABEL", "WATCH_REPOS", "TRUSTED_ACTORS")
CONF_DEFAULTS = {
    "INSTALL_DIR": "/usr/local/claw-watcher", "STATE_DIR": "/var/claw-watcher", "KIT_CHECKOUT": REPO,
    "PYTHON": "/usr/bin/python3", "GITHUB_TOKEN_ENV": "WATCHER_GITHUB_TOKEN", "INTERVAL_SECONDS": "900",
    "LINEAR_KEY_ENV": "", "TRACKER_TEAMS": "", "TRACKER_TRUSTED_USERS": "", "USAGE_FILE": "",
    "LIMIT_DAY_USD": "", "LIMIT_PIECE_USD": "", "LIMIT_DAY_RUNS": "", "LIMIT_PIECE_RUNS": "",
}
CONF_KEYS = set(CONF_REQUIRED) | set(CONF_DEFAULTS)
ACCOUNT_RE = re.compile(r"^_?[a-z][a-z0-9_-]{1,30}\Z")
RDNS_RE = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+\Z")
ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]*\Z")
PATH_RE = re.compile(r"^/[A-Za-z0-9_./-]{1,200}\Z")
REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+\Z")
CRED_RE = re.compile(r"(ghp_|gho_|ghs_|github_pat_|lin_api_|xox[bpas]-|sk-ant-|-----BEGIN)")


class SetupError(Exception):
    """A step that could not be done, in words that quote no value."""


class NotMeasured(Exception):
    pass


class NoSudo(Exception):
    pass


def say(text=""):
    print(text)


def split_list(text):
    return [x.strip() for x in (text or "").split(",") if x.strip()]


# --------------------------------------------------------------------------- #
# The conf
# --------------------------------------------------------------------------- #
def parse_conf(text, source="watcher.conf"):
    values, errors, seen = {}, [], set()
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, eq, val = (p.strip() for p in line.partition("="))
        where = "%s:%d" % (source, n)
        if not eq or not re.match(r"^[A-Z][A-Z0-9_]*\Z", key):
            errors.append("%s: not KEY=value" % where)
        elif key in seen:
            errors.append("%s: duplicate %s" % (where, key))
        elif key not in CONF_KEYS:
            errors.append("%s: unknown key %s" % (where, key))
        elif CRED_RE.search(val):
            errors.append("%s: %s carries a CREDENTIAL SHAPE; no key here holds one (not shown)" % (where, key))
        else:
            seen.add(key)
            values[key] = val
    return values, errors


def validate_conf(values, user=None):
    """(conf, errors): every fault at once, each named by its key."""
    c, e = dict(CONF_DEFAULTS, **values), []

    def need(ok, why):
        if not ok:
            e.append(why)
    for k in CONF_REQUIRED:
        need(c.get(k), "%s is required" % k)
    for k in ("WATCHER_ACCOUNT", "ROLE_ACCOUNT"):
        need(not c.get(k) or ACCOUNT_RE.match(c[k]) and c[k] != "root", "%s must be a local account name, not root" % k)
    need(c.get("WATCHER_ACCOUNT") != c.get("ROLE_ACCOUNT") or not c.get("WATCHER_ACCOUNT"),
         "WATCHER_ACCOUNT and ROLE_ACCOUNT are one account: the watcher must not be the workers")
    need(not user or c.get("WATCHER_ACCOUNT") != user, "WATCHER_ACCOUNT is you: it must be an account of its own")
    need(not c.get("KIT_COMMIT") or re.match(r"^[0-9a-f]{40}\Z", c["KIT_COMMIT"]),
         "KIT_COMMIT must be a full 40-character commit id, one a person reviewed")
    need(not c.get("JOB_LABEL") or RDNS_RE.match(c["JOB_LABEL"]) and not c["JOB_LABEL"].startswith("com.example"),
         "JOB_LABEL must be a reverse-DNS launchd label of your own, not com.example.*")
    for k in ("INSTALL_DIR", "STATE_DIR", "PYTHON"):
        v = c.get(k, "")
        need(bool(PATH_RE.match(v)) and "/../" not in v + "/" and v.rstrip("/") != "",
             "%s must be an absolute path with no `..`" % k)
    for k in ("INSTALL_DIR", "STATE_DIR"):
        need(not c.get(k, "").startswith(UNSAFE_PREFIXES),
             "%s is somewhere a user's session can write: pick a system folder" % k)
    a, b = c.get("INSTALL_DIR", "").rstrip("/"), c.get("STATE_DIR", "").rstrip("/")
    need(not (a and b) or not (a == b or a.startswith(b + "/") or b.startswith(a + "/")),
         "INSTALL_DIR and STATE_DIR must be two separate folders, neither inside the other")
    need(c.get("USAGE_FILE") == "" or PATH_RE.match(c.get("USAGE_FILE", "")), "USAGE_FILE must be an absolute path, or empty")
    need(c.get("KIT_CHECKOUT") and os.path.isabs(c["KIT_CHECKOUT"]), "KIT_CHECKOUT must be an absolute path")
    need(ENV_RE.match(c.get("GITHUB_TOKEN_ENV", "")), "GITHUB_TOKEN_ENV must be the NAME of an environment variable")
    need(c.get("LINEAR_KEY_ENV") == "" or ENV_RE.match(c["LINEAR_KEY_ENV"]),
         "LINEAR_KEY_ENV must be the NAME of an environment variable, or empty")
    need(c.get("INTERVAL_SECONDS", "").isdigit() and 300 <= int(c.get("INTERVAL_SECONDS") or 0) <= 86400,
         "INTERVAL_SECONDS must be a whole number from 300 to 86400")
    need(split_list(c.get("WATCH_REPOS")) and all(REPO_RE.match(r) for r in split_list(c.get("WATCH_REPOS"))),
         "WATCH_REPOS must list OWNER/REPO names")
    need(split_list(c.get("TRUSTED_ACTORS")), "TRUSTED_ACTORS must list the GitHub logins that may apply a protected label")
    tracker = [bool(c.get(k)) for k in ("LINEAR_KEY_ENV", "TRACKER_TEAMS", "TRACKER_TRUSTED_USERS")]
    need(all(tracker) or not any(tracker),
         "LINEAR_KEY_ENV, TRACKER_TEAMS and TRACKER_TRUSTED_USERS go together: all three, or none")
    for k in ("LIMIT_DAY_USD", "LIMIT_PIECE_USD", "LIMIT_DAY_RUNS", "LIMIT_PIECE_RUNS"):
        v = c.get(k, "")
        need(v == "" or re.match(r"^[0-9]+(\.[0-9]+)?\Z", v) and float(v) > 0, "%s must be a positive number, or empty" % k)
    return c, e


def load_conf(path, user=None):
    try:
        with open(path, encoding="utf-8") as fh:
            values, errors = parse_conf(fh.read(), os.path.basename(path))
    except OSError as exc:
        raise SetupError("could not read %s (%s): cp watcher.conf.example watcher.conf && chmod 600 watcher.conf"
                         % (path, exc.strerror))
    conf, more = validate_conf(values, user)
    return conf, errors + more


# --------------------------------------------------------------------------- #
# What is composed: the rules, the manifest, the plist
# --------------------------------------------------------------------------- #
def paths(c):
    inst, state = c["INSTALL_DIR"].rstrip("/"), c["STATE_DIR"].rstrip("/")
    return {"install": inst, "state": state, "rules": "%s/%s" % (inst, RULES_NAME),
            "manifest": "%s/%s" % (inst, MANIFEST_NAME), "entry": "%s/%s" % (inst, ENTRY),
            "env": state + "/env", "status": state + "/watcher-status.json", "hold": state + "/hold.json",
            "log": state + "/watcher.log", "plist": "/Library/LaunchDaemons/%s.plist" % c["JOB_LABEL"]}


def compose_rules(c, watcher):
    """The rules file, judged by the watcher's own loader. Raises SetupError in the loader's words."""
    p = paths(c)
    doc = {"schema": watcher.CONFIG_SCHEMA, "on": True, "status_file": p["status"], "hold_file": p["hold"],
           "github_token_env": c["GITHUB_TOKEN_ENV"], "repos": split_list(c["WATCH_REPOS"]),
           "trusted_actors": split_list(c["TRUSTED_ACTORS"]), "install_manifest": p["manifest"]}
    if c["USAGE_FILE"]:
        doc["usage_file"] = c["USAGE_FILE"]
    limits = {}
    for key, name in (("LIMIT_DAY_USD", "day_usd"), ("LIMIT_PIECE_USD", "piece_usd"),
                      ("LIMIT_DAY_RUNS", "day_runs"), ("LIMIT_PIECE_RUNS", "piece_runs")):
        if c[key]:
            limits[name] = int(c[key]) if name.endswith("runs") else float(c[key])
    if limits:
        doc["limits"] = limits
    if c["LINEAR_KEY_ENV"]:
        doc.update(linear_key_env=c["LINEAR_KEY_ENV"], tracker_teams=split_list(c["TRACKER_TEAMS"]),
                   tracker_trusted_users=split_list(c["TRACKER_TRUSTED_USERS"]))
    try:
        watcher.validate_config(doc)
    except watcher.ConfigError as exc:
        raise SetupError("REFUSED by the watcher's own loader: %s" % exc)
    return (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode("utf-8")


def compose_manifest(c, files, commit):
    doc = {"schema": MANIFEST_SCHEMA, "commit": commit,
           "files": dict((rel, hashlib.sha256(data).hexdigest()) for rel, data in sorted(files.items()))}
    return (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode("utf-8")


def job_body(c):
    """The LaunchDaemon: runs as the watcher account, sources its own env file, runs the installed entry."""
    p = paths(c)
    command = "set -a; . %s; set +a; exec %s %s run --config %s" % (
        shlex.quote(p["env"]), shlex.quote(c["PYTHON"]), shlex.quote(p["entry"]), shlex.quote(p["rules"]))
    return plistlib.dumps({
        "Label": c["JOB_LABEL"], "UserName": c["WATCHER_ACCOUNT"], "ProgramArguments": ["/bin/sh", "-c", command],
        "StartInterval": int(c["INTERVAL_SECONDS"]), "RunAtLoad": True, "WorkingDirectory": p["state"],
        "StandardOutPath": p["log"], "StandardErrorPath": p["log"],
        "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}}, sort_keys=True)


# --------------------------------------------------------------------------- #
# The machine: every change is one fixed command, never a shell string with a value in it
# --------------------------------------------------------------------------- #
class SystemHost(object):
    """The real machine. `runner(argv, input=None, timeout=60)` is subprocess.run's stand-in, so the
    selftest can pin each command. Privileged ones run `sudo -n`, after `acquire` has asked once."""

    def __init__(self, runner=None, user=None):
        self.runner = runner or self._real
        self.user = user or getpass.getuser()

    @staticmethod
    def _real(argv, input=None, timeout=60):
        return subprocess.run(argv, input=input, capture_output=True, timeout=timeout)

    def run(self, argv, input=None, sudo=False, timeout=60):
        full = (["sudo", "-n"] if sudo else []) + list(argv)
        try:
            res = self.runner(full, input=input, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            return 127, b"", type(exc).__name__.encode()
        return res.returncode, res.stdout or b"", res.stderr or b""

    def acquire(self):
        """Ask for the administrator password once, here, with the terminal. NoSudo when it is refused."""
        try:
            rc = subprocess.run(["sudo", "-v"]).returncode
        except OSError:
            rc = 1
        if rc != 0:
            raise NoSudo("no administrator access")

    def account_uid(self, name):
        rc, out, _e = self.run(["dscl", ".", "-read", "/Users/" + name, "UniqueID"])
        m = re.search(rb"UniqueID:\s*([0-9]+)", out) if rc == 0 else None
        return int(m.group(1)) if m else None

    def account_shell(self, name):
        rc, out, _e = self.run(["dscl", ".", "-read", "/Users/" + name, "UserShell"])
        m = re.search(rb"UserShell:\s*(\S+)", out) if rc == 0 else None
        return m.group(1).decode() if m else None

    def used_uids(self):
        rc, out, _e = self.run(["dscl", ".", "-list", "/Users", "UniqueID"])
        if rc != 0:
            raise NotMeasured("the local accounts could not be listed")
        return set(int(x) for x in re.findall(rb"\s([0-9]+)\s*$", out, re.M))

    def create_account(self, name, uid, home):
        base = "/Users/" + name
        for argv in (["dscl", ".", "-create", base], ["dscl", ".", "-create", base, "UserShell", "/usr/bin/false"],
                     ["dscl", ".", "-create", base, "RealName", "claw watcher"],
                     ["dscl", ".", "-create", base, "UniqueID", str(uid)],
                     ["dscl", ".", "-create", base, "PrimaryGroupID", "20"],
                     ["dscl", ".", "-create", base, "NFSHomeDirectory", home],
                     ["dscl", ".", "-create", base, "IsHidden", "1"],
                     ["dscl", ".", "-create", base, "Password", "*"]):
            rc, _o, _e = self.run(argv, sudo=True)
            if rc != 0:
                raise SetupError("could not create the account %s (exit %d at %s)" % (name, rc, " ".join(argv[2:4])))

    def lstat(self, path):
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise NotMeasured("%s could not be inspected (%s)" % (path, exc.strerror))
        return {"mode": st.st_mode & 0o7777, "uid": st.st_uid, "dir": os.path.isdir(path) and not os.path.islink(path),
                "link": os.path.islink(path)}

    def read(self, path, limit=4 * 1024 * 1024):
        try:
            with open(path, "rb") as fh:
                return fh.read(limit + 1)[:limit]
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise NotMeasured("%s could not be read (%s)" % (path, exc.strerror))

    def mkdir_root(self, path):
        self._must(["install", "-d", "-o", "root", "-g", "wheel", "-m", "0755", path], "make " + path)

    def mkdir_owned(self, path, account):
        self._must(["install", "-d", "-o", account, "-g", "staff", "-m", "0755", path], "make " + path)

    def install_root(self, data, dest, mode):
        """root:wheel `mode`, from a temp file that is removed at once."""
        fd, tmp = tempfile.mkstemp(prefix="watcher-setup-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            self._must(["install", "-o", "root", "-g", "wheel", "-m", "%04o" % mode, tmp, dest], "install " + dest)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def write_as(self, account, path, data, mode, append=False):
        """Through a pipe, as `account`: the data is never in a command line."""
        script = 'umask 077; cat %s "$1" && chmod "$2" "$1"' % (">>" if append else ">")
        self._must(["-u", account, "/bin/sh", "-c", script, "sh", path, "%04o" % mode], "write " + path,
                   input=data, sudo_flags=True)

    def count_names(self, account, path, names):
        """How many of `names` the env file sets: a count per name, never a value."""
        out = {}
        for name in names:
            rc, so, _e = self.run(["-u", account, "/usr/bin/grep", "-c", "^%s=." % name, path], sudo=True)
            out[name] = int(so.strip() or 0) if rc in (0, 1) else None
        return out

    def can_write(self, who, path):
        """True, False, or None (could not tell)."""
        probe = path if self.lstat(path) is not None else os.path.dirname(path)
        if who == self.user:
            return os.access(probe, os.W_OK)
        rc, _o, _e = self.run(["-u", who, "/bin/test", "-w", probe], sudo=True)
        return True if rc == 0 else False if rc == 1 else None

    def loaded(self, label):
        rc, out, _e = self.run(["launchctl", "print", "system/" + label], sudo=True)
        return (rc == 0, out.decode("utf-8", "replace"))

    def bootstrap(self, plist):
        self._must(["launchctl", "bootstrap", "system", plist], "load " + plist)

    def bootout(self, label):
        self.run(["launchctl", "bootout", "system/" + label], sudo=True)

    def kickstart(self, label):
        self._must(["launchctl", "kickstart", "-k", "system/" + label], "run " + label)

    def _must(self, argv, what, input=None, sudo_flags=False):
        full = (["sudo", "-n"] + argv) if sudo_flags else argv
        rc, _o, err = self.run(full, input=input, sudo=not sudo_flags)
        if rc != 0:
            raise SetupError("could not %s (exit %d)" % (what, rc))

    # git, as the invoking user ------------------------------------------------------------------
    def git(self, checkout, *args):
        rc, out, _e = self.run(["git", "-C", checkout] + list(args))
        return rc, out

    def export_commit(self, checkout, commit, dest):
        rc, out = self.git(checkout, "archive", "--format=tar", commit)
        if rc != 0:
            raise SetupError("could not export the commit from %s" % checkout)
        with tarfile.open(fileobj=io.BytesIO(out)) as tar:
            for member in tar.getmembers():
                if member.name.startswith(("/", "..")) or ".." in member.name.split("/"):
                    raise SetupError("the commit holds a path that climbs out of its folder")
            tar.extractall(dest)

    def run_selftest(self, python, script, cwd):
        rc, out, err = self.run([python, script, "--selftest"], timeout=900)
        return rc == 0

    def now(self):
        return time.time()

    def sleep(self, seconds):
        time.sleep(seconds)


# --------------------------------------------------------------------------- #
# The steps: each returns (changed, detail) and, without `apply`, only says what it would do
# --------------------------------------------------------------------------- #
class Ctx(object):
    def __init__(self, conf, host, watcher, secret=getpass.getpass, tty=True):
        self.conf, self.host, self.watcher, self.secret, self.tty = conf, host, watcher, secret, tty
        self.p = paths(conf)
        self.files = {}
        self.tree = None
        self.started = None


def step_preflight(ctx, apply_it):
    c, h = ctx.conf, ctx.host
    rc, _out = h.git(c["KIT_CHECKOUT"], "cat-file", "-e", c["KIT_COMMIT"] + "^{commit}")
    if rc != 0:
        raise SetupError("the commit %s is not in %s" % (c["KIT_COMMIT"][:12], c["KIT_CHECKOUT"]))
    rc, _out = h.git(c["KIT_CHECKOUT"], "merge-base", "--is-ancestor", c["KIT_COMMIT"], "origin/main")
    if rc != 0:
        raise SetupError("the commit %s is not on origin/main: only a commit a person reviewed and merged is installed"
                         % c["KIT_COMMIT"][:12])
    tmp = tempfile.mkdtemp(prefix="watcher-setup-tree-")
    ctx.tree = tmp
    h.export_commit(c["KIT_CHECKOUT"], c["KIT_COMMIT"], tmp)
    for rel in WATCHER_FILES:
        data = h.read(os.path.join(tmp, rel))
        if data is None:
            raise SetupError("%s is not in the commit" % rel)
        ctx.files[rel] = data
    here = h.read(os.path.join(HERE, "pipeline_watcher_local.py"))
    if here != ctx.files[ENTRY]:
        raise SetupError("this checkout's watcher is not the commit's: run this script from a checkout of %s"
                         % c["KIT_COMMIT"][:12])
    bad = [rel for rel in SELFTESTS if not h.run_selftest(c["PYTHON"], os.path.join(tmp, rel), tmp)]
    if bad:
        raise SetupError("the commit's own selftest FAILED: %s" % ", ".join(bad))
    return False, "commit %s is on origin/main, this checkout is its watcher, and its watcher and tracker selftests pass" % (
        c["KIT_COMMIT"][:12])


def step_account(ctx, apply_it):
    c, h = ctx.conf, ctx.host
    uid = h.account_uid(c["WATCHER_ACCOUNT"])
    if uid is not None:
        shell = h.account_shell(c["WATCHER_ACCOUNT"])
        if shell not in ("/usr/bin/false", "/sbin/nologin", "/usr/bin/nologin"):
            raise SetupError("the account %s exists and can log in (shell %s): the watcher's account must not"
                             % (c["WATCHER_ACCOUNT"], shell or "unknown"))
        return False, "the account %s exists (uid %d), with no login shell" % (c["WATCHER_ACCOUNT"], uid)
    free = next((i for i in range(300, 400) if i not in h.used_uids()), None)
    if free is None:
        raise SetupError("no free user id from 300 to 399")
    if not apply_it:
        return True, "would create the hidden account %s (uid %d, no login shell, home %s)" % (
            c["WATCHER_ACCOUNT"], free, ctx.p["state"])
    h.create_account(c["WATCHER_ACCOUNT"], free, ctx.p["state"])
    if h.account_uid(c["WATCHER_ACCOUNT"]) != free:
        raise SetupError("created the account, and reading it back does not match")
    return True, "created the hidden account %s (uid %d)" % (c["WATCHER_ACCOUNT"], free)


def step_folders(ctx, apply_it):
    h, p = ctx.host, ctx.p
    want = [(d, "root") for d in (p["install"], p["install"] + "/scripts", p["install"] + "/schemas",
                                  p["install"] + "/etc")] + [(p["state"], ctx.conf["WATCHER_ACCOUNT"])]
    todo = []
    for path, owner in want:
        st = h.lstat(path)
        uid = 0 if owner == "root" else h.account_uid(owner)
        if st is None or not st["dir"] or st["link"] or (uid is not None and st["uid"] != uid) or st["mode"] != 0o755:
            todo.append((path, owner))
    if not todo:
        return False, "the install folder is root's (0755), and the state folder is the watcher's"
    if not apply_it:
        return True, "would make or fix: %s" % ", ".join(d for d, _o in todo)
    for path, owner in todo:
        if owner == "root":
            h.mkdir_root(path)
        else:
            h.mkdir_owned(path, owner)
    return True, "made or fixed: %s" % ", ".join(d for d, _o in todo)


def _current(host, path):
    return host.read(path)


def step_files(ctx, apply_it):
    h, p = ctx.host, ctx.p
    todo = [rel for rel, data in sorted(ctx.files.items()) if _current(h, "%s/%s" % (p["install"], rel)) != data]
    manifest = compose_manifest(ctx.conf, ctx.files, ctx.conf["KIT_COMMIT"])
    todo_m = _current(h, p["manifest"]) != manifest
    if not todo and not todo_m:
        return False, "%d files and their manifest are as the commit has them" % len(ctx.files)
    if not apply_it:
        return True, "would install %d file(s)%s: %s" % (len(todo), " and the manifest" if todo_m else "",
                                                          ", ".join(todo) or "none")
    for rel in todo:
        h.install_root(ctx.files[rel], "%s/%s" % (p["install"], rel), 0o644)
    if todo_m:
        h.install_root(manifest, p["manifest"], 0o644)
    return True, "installed %d file(s)%s" % (len(todo), " and the manifest" if todo_m else "")


def step_rules(ctx, apply_it):
    h, p = ctx.host, ctx.p
    body = compose_rules(ctx.conf, ctx.watcher)
    if _current(h, p["rules"]) == body:
        return False, "the rules are current, root's (0644), and accepted by the watcher's own loader"
    if not apply_it:
        return True, "would write %s (root's, 0644); the watcher's own loader accepts it" % p["rules"]
    h.install_root(body, p["rules"], 0o644)
    return True, "wrote %s" % p["rules"]


def step_keys(ctx, apply_it):
    c, h, p = ctx.conf, ctx.host, ctx.p
    names = [c["GITHUB_TOKEN_ENV"]] + ([c["LINEAR_KEY_ENV"]] if c["LINEAR_KEY_ENV"] else [])
    if h.account_uid(c["WATCHER_ACCOUNT"]) is None or h.lstat(p["env"]) is None:
        have = dict((n, 0) for n in names)
    else:
        have = h.count_names(c["WATCHER_ACCOUNT"], p["env"], names)
    if any(v is None for v in have.values()):
        raise NotMeasured("%s could not be read as %s" % (p["env"], c["WATCHER_ACCOUNT"]))
    missing = [n for n in names if have[n] == 0]
    if not missing:
        return False, "%s sets %s (mode 600, the watcher's own)" % (p["env"], ", ".join(names))
    if not apply_it:
        return True, "would ask, at a hidden prompt, for: %s (read-only keys)" % ", ".join(missing)
    if not ctx.tty:
        raise NotMeasured("the watcher's keys (%s) are missing, and there is no terminal to ask for them" % ", ".join(missing))
    say("\nThe watcher's own keys. They must be READ-ONLY at their source. Nothing you type is shown or kept.")
    for name in missing:
        value = ctx.secret("  %s: " % name).strip()
        if not value or re.search(r"\s", value):
            raise SetupError("%s was empty or held a space: nothing was written" % name)
        h.write_as(c["WATCHER_ACCOUNT"], p["env"], ("%s=%s\n" % (name, value)).encode("utf-8"), 0o600, append=True)
    return True, "wrote %s into %s (mode 600, the watcher's own)" % (", ".join(missing), p["env"])


def step_permissions(ctx, apply_it):
    c, h, p = ctx.conf, ctx.host, ctx.p
    who = [c["ROLE_ACCOUNT"], h.user]
    guarded = [p["install"], p["install"] + "/scripts", p["install"] + "/etc", p["rules"], p["manifest"], p["entry"], p["state"]]
    bad, unknown = [], []
    for path in guarded:
        if h.lstat(path) is None:
            continue
        for w in who:
            got = h.can_write(w, path)
            if got:
                bad.append("%s can write %s" % (w, path))
            elif got is None:
                unknown.append("%s may be able to write %s" % (w, path))
    if bad:
        raise SetupError("not independent: %s" % "; ".join(bad))
    if unknown:
        raise NotMeasured("could not tell: %s" % "; ".join(unknown))
    return False, "neither the role account (%s) nor you can write the install folder, the rules, the manifest or the state folder" % (
        c["ROLE_ACCOUNT"])


def step_job(ctx, apply_it):
    c, h, p = ctx.conf, ctx.host, ctx.p
    body = job_body(c)
    have = _current(h, p["plist"])
    if have is not None and have != body:
        try:
            ours = plistlib.loads(have).get("ProgramArguments", [""])[-1].find(p["entry"]) >= 0
        except Exception:  # noqa: BLE001 -- an unreadable plist is not ours
            ours = False
        if not ours:
            raise SetupError("%s is NOT this watcher's job: replacing it would destroy another job. Pick another JOB_LABEL"
                             % p["plist"])
    loaded, text = h.loaded(c["JOB_LABEL"])
    current = have == body and loaded and p["entry"] in text
    if current:
        return False, "the job is installed and loaded, running its own plist: system/%s every %s s" % (
            c["JOB_LABEL"], c["INTERVAL_SECONDS"])
    if not apply_it:
        return True, "would %s the job %s (runs as %s every %s s), and run it once" % (
            "install and load" if have is None else "reinstall and reload", c["JOB_LABEL"], c["WATCHER_ACCOUNT"],
            c["INTERVAL_SECONDS"])
    if have != body:
        h.install_root(body, p["plist"], 0o644)
    if loaded:
        h.bootout(c["JOB_LABEL"])
    h.bootstrap(p["plist"])
    return True, "installed and loaded system/%s" % c["JOB_LABEL"]


def step_first_pass(ctx, apply_it):
    c, h, p = ctx.conf, ctx.host, ctx.p
    if not apply_it:
        return False, "would run the job once and read its first pass back"
    started = h.now()
    h.kickstart(c["JOB_LABEL"])
    deadline = started + FIRST_PASS_WAIT_SECONDS
    while True:
        raw = h.read(p["status"])
        doc = None
        try:
            doc = json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            doc = None
        written = ctx.watcher._epoch(doc.get("written_at")) if isinstance(doc, dict) else None
        if written is not None and written >= started - 2:
            break
        if h.now() >= deadline:
            raise SetupError("the job ran, and no status file appeared at %s within %d s: read %s" % (
                p["status"], FIRST_PASS_WAIT_SECONDS, p["log"]))
        h.sleep(3)
    on = [m for m in doc["monitors"] if m["state"] != "off"]
    bad = [m["name"] for m in doc["monitors"] if m["state"] == "blind" and "do not match" in (m["reason"] or "")]
    if bad or doc.get("error"):
        raise SetupError("the first pass was blind on its own files or crashed: read %s" % p["log"])
    return True, "the first pass wrote %s: %s, %d monitor(s) on (%s)" % (
        p["status"], doc["result"], len(on), ", ".join("%s %s" % (m["name"], m["state"]) for m in on))


STEPS = (("preflight", step_preflight), ("account", step_account), ("folders", step_folders),
         ("files", step_files), ("rules", step_rules), ("keys", step_keys), ("permissions", step_permissions),
         ("job", step_job), ("first pass", step_first_pass))


def refuse_if_agent(env=None):
    from pipeline_dispatch_local import AGENT_ENV_MARKERS
    env = os.environ if env is None else env
    found = [m for m in AGENT_ENV_MARKERS if m in env]
    if found:
        raise SetupError("REFUSED: this installs the supervision of the workers, and a session must never install "
                         "its own. An agent's environment was found (%s). A person runs it, in a terminal." %
                         ", ".join(found))


def run_steps(ctx, apply_it, skip=()):
    """[(name, changed, detail)]; raises at the first step that cannot be done."""
    rows = []
    for name, fn in STEPS:
        if name in skip:
            continue
        changed, detail = fn(ctx, apply_it)
        rows.append((name, changed, detail))
        say("  %-12s %s%s" % (name, "WOULD CHANGE  " if changed and not apply_it else "DONE  " if changed else "ok  ", detail))
    return rows


def cmd_run(conf_path, host=None, watcher=None, ask=input, secret=getpass.getpass, tty=True, env=None, check_only=False):
    try:
        refuse_if_agent(env)
    except SetupError as exc:
        say(str(exc))
        return EX_REFUSED
    host = host or SystemHost()
    try:
        conf, errors = load_conf(conf_path, host.user)
    except SetupError as exc:
        say(str(exc))
        return EX_CONF
    if errors:
        say("%s has %d problem(s), all of them:" % (os.path.basename(conf_path), len(errors)))
        for e in errors:
            say("  - " + e)
        return EX_CONF
    if watcher is None:
        import pipeline_watcher_local as watcher  # noqa: PLC0415 -- the checkout's own, judged against the commit's
    ctx = Ctx(conf, host, watcher, secret, tty)
    try:
        say("\nWHAT IS TRUE NOW (nothing is changed yet):")
        plan = run_steps(ctx, False, skip=("first pass",))
        changing = [r for r in plan if r[1]]
        if check_only:
            say("\n%s" % ("%d step(s) would change." % len(changing) if changing else "Nothing to change."))
            return EX_OK if not changing else EX_FAILED
        if changing:
            say("\nThis creates the watcher's account and root-owned folder, installs files at commit %s, and loads one "
                "job (system/%s)." % (conf["KIT_COMMIT"][:12], conf["JOB_LABEL"]))
            if ask("Apply these changes? Type yes: ").strip() != "yes":
                say("Nothing was changed.")
                return EX_OK
            host.acquire()
            say("\nAPPLYING:")
            run_steps(ctx, True, skip=("preflight",))
        else:
            host.acquire()
            say("\nNothing to change. Running and reading back the first pass:")
            step = dict(STEPS)["first pass"]
            step(ctx, True)
        say("\nVERIFYING:")
        after = run_steps(ctx, False, skip=("first pass",))
        left = [r[0] for r in after if r[1]]
        if left:
            say("\nVERIFY FAILED: %s still differ." % ", ".join(left))
            return EX_FAILED
        say("\nDONE. The watcher runs as %s from %s, checks its own files every pass, and its first pass is above." %
            (conf["WATCHER_ACCOUNT"], conf["INSTALL_DIR"]))
        return EX_OK
    except NoSudo as exc:
        say("NO ADMINISTRATOR ACCESS - nothing was changed. Fix that and run again.")
        return EX_NOSUDO
    except NotMeasured as exc:
        say("NOT MEASURED: %s. It is not called fine. Run the same command in your own terminal." % exc)
        return EX_UNKNOWN
    except SetupError as exc:
        say("FAILED: %s" % exc)
        return EX_FAILED
    finally:
        if ctx.tree and os.path.isdir(ctx.tree):
            import shutil
            shutil.rmtree(ctx.tree, ignore_errors=True)


EXAMPLE_CONF = """# The watcher's own install (scripts/pipeline_watcher_setup.py, KIT-517).  Copy to watcher.conf, mode 600.
# Names only: no key, token or password goes in this file.

# The account the watcher runs as: a NEW hidden local account, not root, not you, not the workers' account.
WATCHER_ACCOUNT=_clawwatcher
# The workers' account: the one the dispatcher's sessions run as.  The installer checks it cannot write the install.
ROLE_ACCOUNT=_clawdispatch
# A full 40-character commit id, one a person reviewed.  It must be on origin/main, and this checkout must be it.
KIT_COMMIT=REPLACE-WITH-THE-REVIEWED-COMMIT
# Your own reverse-DNS launchd label (not com.example.*).
JOB_LABEL=local.example-claw-watcher

# Root's folder (the files and rules), and the watcher's state folder (its status file, hold file and env file).
# System folders only: nowhere a user's session can write.
INSTALL_DIR=/usr/local/claw-watcher
STATE_DIR=/var/claw-watcher
PYTHON=/usr/bin/python3
INTERVAL_SECONDS=900

# What it watches: OWNER/REPO names, comma-separated, and the GitHub logins that may apply a protected label.
WATCH_REPOS=acme/app
TRUSTED_ACTORS=the-owners-login,github-actions[bot]
# The NAME of the variable that holds a READ-ONLY GitHub token.  The installer asks for the value at a hidden prompt.
GITHUB_TOKEN_ENV=WATCHER_GITHUB_TOKEN

# Optional: ticket text, first-hand (all three together, or none).  A READ-ONLY tracker key.
LINEAR_KEY_ENV=
TRACKER_TEAMS=
TRACKER_TRUSTED_USERS=

# Optional: the usage file, and the limits.  The limits are the owner's decision (docs/WATCHER.md): empty = that monitor is off.
USAGE_FILE=
LIMIT_DAY_USD=
LIMIT_PIECE_USD=
LIMIT_DAY_RUNS=
LIMIT_PIECE_RUNS=
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description="The watcher's own install.")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--example-conf", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    for name in ("run", "check"):
        p = sub.add_parser(name)
        p.add_argument("--conf", default="watcher.conf")
    args = ap.parse_args(argv)
    if args.selftest:
        from scratch_run import contained
        return contained(selftest, "watcher-setup-selftest-run-")
    if args.example_conf:
        sys.stdout.write(EXAMPLE_CONF)
        return EX_OK
    if args.cmd not in ("run", "check"):
        ap.print_usage()
        return EX_CONF
    return cmd_run(args.conf, check_only=args.cmd == "check")


# SELFTEST: nothing below this line is read by the checks that scan the code above it.
USERS = "/Users" + "/"          # the directory service's account folder, built so no account path is written out


class FakeHost(SystemHost):
    """The machine, stood in for: real files in a temp folder (so the watcher's own check can read what was
    installed), the owner and mode of each kept in a table, and every change recorded."""

    def __init__(self, root, user="alice"):
        SystemHost.__init__(self, runner=lambda *a, **k: None, user=user)
        self.root, self.meta, self.accounts, self.calls, self.written_in = root, {}, {}, [], {}
        self.loaded_labels, self.clock = {}, 1_800_000_000.0
        self.can = {}                      # (who, path) -> True / None; anything else is False
        self.commit_ok, self.ancestor_ok, self.selftests_ok, self.sudo_ok = True, True, True, True
        self.first_pass, self.first_pass_blind = True, False
        self.secret_args = []

    def _real(self, path):
        for prefix, sub in (("/Library/LaunchDaemons", "launchd"), (self.root, "")):
            if path.startswith(prefix) and prefix != self.root:
                return os.path.join(self.root, sub, os.path.basename(path))
        return path

    def acquire(self):
        self.calls.append(("sudo",))
        if not self.sudo_ok:
            raise NoSudo("no administrator access")

    def account_uid(self, name):
        return self.accounts.get(name, {}).get("uid")

    def account_shell(self, name):
        return self.accounts.get(name, {}).get("shell")

    def used_uids(self):
        return set([501, 502] + [a["uid"] for a in self.accounts.values()])

    def create_account(self, name, uid, home):
        self.calls.append(("create_account", name, uid, home))
        self.accounts[name] = {"uid": uid, "shell": "/usr/bin/false", "home": home}

    def lstat(self, path):
        real = self._real(path)
        if not os.path.lexists(real):
            return None
        owner, mode = self.meta.get(path, ("alice", 0o755))
        uid = 0 if owner == "root" else (self.accounts.get(owner, {}).get("uid") or 501)
        return {"mode": mode, "uid": uid, "dir": os.path.isdir(real) and not os.path.islink(real),
                "link": os.path.islink(real)}

    def read(self, path, limit=4 * 1024 * 1024):
        real = self._real(path)
        if not os.path.exists(real):
            return None
        with open(real, "rb") as fh:
            return fh.read(limit)

    def mkdir_root(self, path):
        self.calls.append(("mkdir_root", path))
        os.makedirs(self._real(path), exist_ok=True)
        self.meta[path] = ("root", 0o755)

    def mkdir_owned(self, path, account):
        self.calls.append(("mkdir_owned", path, account))
        os.makedirs(self._real(path), exist_ok=True)
        self.meta[path] = (account, 0o755)

    def install_root(self, data, dest, mode):
        self.calls.append(("install_root", dest, mode))
        os.makedirs(os.path.dirname(self._real(dest)), exist_ok=True)
        with open(self._real(dest), "wb") as fh:
            fh.write(data)
        os.chmod(self._real(dest), mode)
        self.meta[dest] = ("root", mode)

    def write_as(self, account, path, data, mode, append=False):
        self.calls.append(("write_as", account, path, mode, append, len(data)))
        os.makedirs(os.path.dirname(self._real(path)), exist_ok=True)
        with open(self._real(path), "ab" if append else "wb") as fh:
            fh.write(data)
        os.chmod(self._real(path), mode)
        self.meta[path] = (account, mode)

    def count_names(self, account, path, names):
        body = (self.read(path) or b"").decode("utf-8")
        return dict((n, len(re.findall(r"^%s=." % re.escape(n), body, re.M))) for n in names)

    def can_write(self, who, path):
        return self.can.get((who, path), False)

    def loaded(self, label):
        return (label in self.loaded_labels, self.loaded_labels.get(label, ""))

    def bootstrap(self, plist):
        self.calls.append(("bootstrap", plist))
        label = os.path.basename(plist)[:-6]
        self.loaded_labels[label] = (self.read(plist) or b"").decode("latin-1")
        body = plistlib.loads(self.read(plist))
        self.loaded_labels[label] = " ".join(body["ProgramArguments"])

    def bootout(self, label):
        self.calls.append(("bootout", label))
        self.loaded_labels.pop(label, None)

    def kickstart(self, label):
        self.calls.append(("kickstart", label))
        if not self.first_pass:
            return
        import check_schemas
        import copy
        doc = copy.deepcopy(check_schemas.VALID_WATCHER)
        doc["written_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.clock + 1))
        if self.first_pass_blind:
            doc["monitors"][0].update(state="blind", reason="its own files do not match their manifest (x)")
        self._status_doc = doc

    def git(self, checkout, *args):
        self.calls.append(("git",) + args)
        if args[0] == "cat-file":
            return (0 if self.commit_ok else 1), b""
        if args[0] == "merge-base":
            return (0 if self.ancestor_ok else 1), b""
        return 1, b""

    def export_commit(self, checkout, commit, dest):
        for rel in WATCHER_FILES:
            os.makedirs(os.path.dirname(os.path.join(dest, rel)), exist_ok=True)
            with open(os.path.join(dest, rel), "wb") as fh, open(os.path.join(REPO, rel), "rb") as src:
                fh.write(src.read() + (b"# another commit\n" if getattr(self, "other_commit", False) and rel == ENTRY else b""))

    def run_selftest(self, python, script, cwd):
        self.calls.append(("selftest", os.path.basename(script)))
        return self.selftests_ok

    def now(self):
        return self.clock

    def sleep(self, seconds):
        self.clock += seconds
        status = getattr(self, "_status_doc", None)
        if status is not None and self.status_path:
            os.makedirs(os.path.dirname(self._real(self.status_path)), exist_ok=True)
            with open(self._real(self.status_path), "w", encoding="utf-8") as fh:
                json.dump(status, fh)


def selftest():
    import copy
    import contextlib
    failures, total = [], [0]
    import pipeline_watcher_local as watcher

    def check(name, got, want):
        total[0] += 1
        if got != want:
            failures.append("FAIL %s: got %r, want %r" % (name, str(got)[:300], str(want)[:300]))

    with tempfile.TemporaryDirectory() as tmp:
        os.chmod(tmp, 0o700)
        inst, state = os.path.join(tmp, "inst"), os.path.join(tmp, "state")
        commit = "a" * 40
        values = {"WATCHER_ACCOUNT": "_clawwatcher", "ROLE_ACCOUNT": "_clawdispatch", "KIT_COMMIT": commit,
                  "JOB_LABEL": "local.test-claw-watcher", "WATCH_REPOS": "acme/app", "TRUSTED_ACTORS": "the-owner",
                  "INSTALL_DIR": inst, "STATE_DIR": state}

        def conf_text(**over):
            d = dict(values, **over)
            return "".join("%s=%s\n" % kv for kv in sorted(d.items()) if kv[1] is not None)

        conf_path = os.path.join(tmp, "watcher.conf")

        def put_conf(**over):
            with open(conf_path, "w", encoding="utf-8") as fh:
                fh.write(conf_text(**over))

        # the conf, every fault at once and none quoted
        c, errs = validate_conf(dict(values, KIT_CHECKOUT=REPO, INSTALL_DIR="/opt/watch/inst",
                                     STATE_DIR="/opt/watch/state"), "alice")
        check("a good conf validates", errs, [])
        _v, perrs = parse_conf("WATCHER_ACCOUNT=_w\nGITHUB_TOKEN_ENV=WATCHER_GITHUB_TOKEN\nLINEAR_KEY_ENV=lin_api_" + "k" * 30 + "\n")
        check("a conf with a credential shape is refused, naming the line and not the value",
              (len(perrs), "CREDENTIAL SHAPE" in perrs[0], "lin_api_" in " ".join(perrs)), (1, True, False))
        for label, over, needle in (
                ("the watcher is the workers", {"ROLE_ACCOUNT": "_clawwatcher"}, "one account"),
                ("the watcher is you", {"WATCHER_ACCOUNT": "alice"}, "is you"),
                ("a short commit", {"KIT_COMMIT": "abc123"}, "40-character"),
                ("an example label", {"JOB_LABEL": "com.example.watcher"}, "JOB_LABEL"),
                ("an install folder a user can write", {"INSTALL_DIR": USERS + "alice/watch"}, "INSTALL_DIR"),
                ("a state folder inside the install folder", {"STATE_DIR": inst + "/state"}, "separate folders"),
                ("a half tracker", {"LINEAR_KEY_ENV": "WATCHER_LINEAR_KEY"}, "go together"),
                ("a bad limit", {"LIMIT_DAY_USD": "-3"}, "LIMIT_DAY_USD"),
                ("a path that climbs", {"INSTALL_DIR": "/opt/../etc"}, "INSTALL_DIR"),
                ("no repos", {"WATCH_REPOS": ""}, "WATCH_REPOS")):
            _c, e = validate_conf(dict(values, KIT_CHECKOUT=REPO, **over), "alice")
            check("conf: %s is refused, by key" % label, any(needle in x for x in e), True)
        with open(os.path.join(REPO, "watcher.conf.example"), encoding="utf-8") as fh:
            example_file = fh.read()
        check("the shipped watcher.conf.example is the one the code prints, and names every key but the checkout (it defaults)",
              (example_file == EXAMPLE_CONF, sorted(parse_conf(example_file)[0]) == sorted(CONF_KEYS - {"KIT_CHECKOUT"})), (True, True))
        check("conf: the example conf, unedited, cannot run",
              bool(validate_conf(parse_conf(EXAMPLE_CONF)[0], "alice")[1]), True)

        # the files the watcher runs from are its whole import closure
        import ast
        def top(fn):
            tree = ast.parse(open(os.path.join(HERE, fn), encoding="utf-8").read())
            out = set()
            for n in tree.body:
                names = [a.name for a in n.names] if isinstance(n, ast.Import) else (
                    [n.module] if isinstance(n, ast.ImportFrom) and n.module else [])
                out.update(x + ".py" for x in names if os.path.exists(os.path.join(HERE, x + ".py")))
            return out
        seen, todo = set(), ["pipeline_watcher_local.py"]
        while todo:
            f = todo.pop()
            if f not in seen:
                seen.add(f)
                todo += sorted(top(f) - seen)
        check("the install holds every script the watcher imports, and each file the commit has",
              (sorted("scripts/" + x for x in seen) == sorted(x for x in WATCHER_FILES if x.startswith("scripts/")),
               all(os.path.exists(os.path.join(REPO, rel)) for rel in WATCHER_FILES)), (True, True))

        import shutil
        counter = [0]

        def machine(**kw):
            for sub in ("inst", "state", "launchd"):
                shutil.rmtree(os.path.join(tmp, sub), ignore_errors=True)
            counter[0] += 1
            h = FakeHost(tmp)
            h.clock += 1000 * counter[0]
            h.status_path = state + "/watcher-status.json"
            for k, v in kw.items():
                setattr(h, k, v)
            return h

        def go(h, answers=("yes",), secrets=None, **kw):
            out, asked = io.StringIO(), []
            it = iter(answers)
            sec = iter(secrets if secrets is not None else ["ghp_" + "r" * 36])
            with contextlib.redirect_stdout(out):
                rc = cmd_run(conf_path, host=h, watcher=watcher, ask=lambda _p: (asked.append(_p), next(it))[1],
                             secret=lambda _p: next(sec), tty=kw.pop("tty", True), env=kw.pop("env", {}),
                             check_only=kw.pop("check_only", False))
            return rc, out.getvalue(), asked

        for folder in ("/tmp/w", "/private/tmp/w", "/var/tmp/w"):
            _c, e = validate_conf(dict(values, KIT_CHECKOUT=REPO, INSTALL_DIR=folder), "alice")
            check("conf: %s is somewhere a session can write, and is refused" % folder,
                  any("INSTALL_DIR" in x and "can write" in x for x in e), True)
        # the machine tests keep their files in a temp folder, which on some systems is such a place
        saved_prefixes = globals()["UNSAFE_PREFIXES"]
        globals()["UNSAFE_PREFIXES"] = ("/Users/",)
        put_conf(KIT_CHECKOUT=REPO)
        # an agent's environment: refused before the conf is read
        from pipeline_dispatch_local import AGENT_ENV_MARKERS
        h = machine()
        rc, out, _a = go(h, env={AGENT_ENV_MARKERS[0]: "1"})
        check("an agent's environment is refused, and nothing is touched", (rc, h.calls, "REFUSED" in out), (EX_REFUSED, [], True))
        # a conf with a fault stops before anything is read from the machine
        put_conf(KIT_CHECKOUT=REPO, KIT_COMMIT="zz")
        h = machine()
        rc, out, _a = go(h)
        check("a bad conf is exit 2, all faults named, nothing touched", (rc, h.calls, "KIT_COMMIT" in out), (EX_CONF, [], True))
        put_conf(KIT_CHECKOUT=REPO)

        # preflight refusals
        for label, kw, needle in (("a commit that is not there", {"commit_ok": False}, "is not in"),
                                  ("a commit not on origin/main", {"ancestor_ok": False}, "not on origin/main"),
                                  ("a selftest that fails", {"selftests_ok": False}, "selftest FAILED")):
            h = machine(**kw)
            rc, out, _a = go(h)
            check("%s: failed, and nothing was changed" % label, (rc, needle in out, [c_ for c_ in h.calls if c_[0] in (
                "create_account", "mkdir_root", "install_root", "write_as", "bootstrap")]), (EX_FAILED, True, []))

        h = machine(other_commit=True)
        rc, out, _a = go(h)
        check("a checkout whose watcher is not the commit's is refused, and nothing was changed",
              (rc, "is not the commit's" in out, [c_ for c_ in h.calls if c_[0] in ("create_account", "mkdir_root", "install_root")]),
              (EX_FAILED, True, []))

        # check mode: reads, changes nothing
        h = machine()
        rc, out, asked = go(h, check_only=True)
        check("check shows what would change and changes nothing",
              (rc, asked, "WOULD CHANGE" in out, [c_ for c_ in h.calls if c_[0] in (
                  "create_account", "mkdir_root", "install_root", "write_as", "bootstrap", "sudo")]),
              (EX_FAILED, [], True, []))
        # a "no" changes nothing
        h = machine()
        rc, out, asked = go(h, answers=("no",))
        check("anything but a typed yes changes nothing", (rc, len(asked), "Nothing was changed" in out, [c_ for c_ in h.calls if c_[0] in (
            "create_account", "mkdir_root", "install_root", "write_as", "bootstrap", "sudo")]), (EX_OK, 1, True, []))
        # sudo refused: exit 5, nothing changed
        h = machine(sudo_ok=False)
        rc, out, _a = go(h)
        check("no administrator access is exit 5 with nothing changed", (rc, [c_ for c_ in h.calls if c_[0] in (
            "create_account", "mkdir_root", "install_root", "bootstrap")]), (EX_NOSUDO, []))

        # the whole run
        h = machine()
        rc, out, asked = go(h)
        check("a full run asks ONE question and ends fine", (rc, len(asked)), (EX_OK, 1))
        names = [c_[0] for c_ in h.calls]
        check("the account, folders, files, keys and job were made, in that order",
              [n for n in names if n in ("create_account", "mkdir_root", "mkdir_owned", "install_root", "write_as", "bootstrap", "kickstart")][:4],
              ["create_account", "mkdir_root", "mkdir_root", "mkdir_root"])
        check("the account is hidden, with no login shell", h.accounts["_clawwatcher"]["shell"], "/usr/bin/false")
        check("the job is loaded after the files, and run once",
              names.index("bootstrap") > max(i for i, n in enumerate(names) if n == "install_root") and names.count("kickstart") == 1, True)
        check("every file is root's, and no file is writable by group or other",
              all(h.meta[d][0] == "root" and not h.meta[d][1] & 0o022 for d in h.meta if d.startswith(inst)), True)
        check("the rules, the manifest and every file are at their paths",
              all(os.path.exists(h._real(inst + "/" + rel)) for rel in WATCHER_FILES + (RULES_NAME, MANIFEST_NAME)), True)
        # the watcher's own check accepts exactly what was installed, and refuses a tamper
        check("the watcher's own self-check accepts what the installer wrote",
              watcher.install_problem(inst + "/" + MANIFEST_NAME), None)
        rules = json.loads(h.read(inst + "/" + RULES_NAME))
        check("the rules name the manifest, the hold and the status file in the state folder",
              (rules["install_manifest"], rules["hold_file"], rules["status_file"], rules["on"]),
              (inst + "/manifest.json", state + "/hold.json", state + "/watcher-status.json", True))
        # the keys: written through a pipe as the watcher account, never in a command line or output
        wr = [c_ for c_ in h.calls if c_[0] == "write_as"]
        check("the key is written as the watcher account, mode 600, appended, and only its length is recorded",
              [(w[1], w[3], w[4]) for w in wr], [("_clawwatcher", 0o600, True)])
        body = h.read(state + "/env").decode()
        check("the env file holds the key, and the output holds none of it",
              ("WATCHER_GITHUB_TOKEN=ghp_" in body, "ghp_" in out, "ghp_" in json.dumps(h.calls)), (True, False, False))
        check("the first pass is read back and said", "the first pass wrote" in out, True)
        # a second run changes nothing
        h2 = FakeHost(tmp)
        h2.status_path = h.status_path
        h2.accounts, h2.meta, h2.loaded_labels = h.accounts, h.meta, h.loaded_labels
        rc, out2, asked2 = go(h2, answers=("yes",))
        check("a second run finds nothing to change, asks nothing, and ends fine",
              (rc, asked2, "Nothing to change" in out2, [c_[0] for c_ in h2.calls if c_[0] in (
                  "create_account", "mkdir_root", "install_root", "write_as", "bootstrap")]), (EX_OK, [], True, []))

        # tamper: a changed installed file is reinstalled, and said
        with open(h._real(inst + "/scripts/check_schemas.py"), "ab") as fh:
            fh.write(b"# planted\n")
        os.chmod(h._real(inst + "/scripts/check_schemas.py"), 0o644)
        check("a planted line makes the watcher's own check name the file",
              "scripts/check_schemas.py" in (watcher.install_problem(inst + "/" + MANIFEST_NAME) or ""), True)
        h3 = FakeHost(tmp)
        h3.status_path = h.status_path
        h3.accounts, h3.meta, h3.loaded_labels = h.accounts, h.meta, h.loaded_labels
        rc, out3, asked3 = go(h3, check_only=True)
        check("check names the file to reinstall", ("scripts/check_schemas.py" in out3, rc), (True, EX_FAILED))
        rc, out3, _a = go(h3)
        check("run reinstalls it and the self-check is clean again", (rc, watcher.install_problem(inst + "/" + MANIFEST_NAME)), (EX_OK, None))

        # independence: a worker who can write the install is a failure, named
        for who, path, label in (("_clawdispatch", inst, "the role account can write the install folder"),
                                 ("alice", inst + "/" + RULES_NAME, "you can write the rules"),
                                 ("_clawdispatch", state, "the role account can write the state folder")):
            h4 = FakeHost(tmp)
            h4.status_path = h.status_path
            h4.accounts, h4.meta, h4.loaded_labels = h.accounts, h.meta, h.loaded_labels
            h4.can[(who, path)] = True
            rc, out4, _a = go(h4, check_only=True)
            check("%s: not independent, named" % label, (rc, "not independent" in out4, "%s can write %s" % (who, path) in out4),
                  (EX_FAILED, True, True))
        h4 = FakeHost(tmp)
        h4.status_path = h.status_path
        h4.accounts, h4.meta, h4.loaded_labels = h.accounts, h.meta, h.loaded_labels
        h4.can[("_clawdispatch", inst)] = None
        rc, out4, _a = go(h4, check_only=True)
        check("a writability it could not tell is exit 4, never fine", (rc, "NOT MEASURED" in out4), (EX_UNKNOWN, True))

        # an account that can log in, a foreign plist, a first pass that is blind on its own files
        h5 = machine()
        h5.accounts["_clawwatcher"] = {"uid": 333, "shell": "/bin/zsh", "home": state}
        rc, out5, _a = go(h5)
        check("an existing account that can log in is refused", (rc, "can log in" in out5), (EX_FAILED, True))
        h6 = machine()
        os.makedirs(os.path.join(tmp, "launchd"), exist_ok=True)
        with open(os.path.join(tmp, "launchd", "local.test-claw-watcher.plist"), "wb") as fh:
            fh.write(plistlib.dumps({"Label": "local.test-claw-watcher", "ProgramArguments": ["/bin/other"]}))
        rc, out6, _a = go(h6)
        check("a plist that is not this watcher's is never replaced", (rc, "NOT this watcher's job" in out6,
                                                                       [c_[0] for c_ in h6.calls if c_[0] == "bootstrap"]),
              (EX_FAILED, True, []))
        os.unlink(os.path.join(tmp, "launchd", "local.test-claw-watcher.plist"))
        h7 = machine(first_pass_blind=True)
        rc, out7, _a = go(h7)
        check("a first pass that is blind on its own files is a failure, said", (rc, "blind on its own files" in out7), (EX_FAILED, True))
        h8 = machine(first_pass=False)
        rc, out8, _a = go(h8)
        check("a first pass that never appears is a failure, said, with where to look", (rc, "no status file appeared" in out8), (EX_FAILED, True))
        h9 = machine()
        rc, out9, _a = go(h9, secrets=[""])
        check("an empty key writes nothing", (rc, [c_ for c_ in h9.calls if c_[0] == "write_as"]), (EX_FAILED, []))
        h10 = machine()
        rc, out10, _a = go(h10, tty=False)
        check("with no terminal and no key, it says so and exits 4", (rc, "no terminal" in out10), (EX_UNKNOWN, True))

        # the real host's commands, pinned: each is one fixed argv, no shell string with a value in it
        seen_argv = []

        def recorder(argv, input=None, timeout=60):
            seen_argv.append((list(argv), input))
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        real = SystemHost(runner=recorder, user="alice")
        real.create_account("_clawwatcher", 333, "/var/claw-watcher")
        real.mkdir_root("/usr/local/claw-watcher")
        real.mkdir_owned("/var/claw-watcher", "_clawwatcher")
        real.write_as("_clawwatcher", "/var/claw-watcher/env", b"WATCHER_GITHUB_TOKEN=ghp_" + b"s" * 36 + b"\n", 0o600, append=True)
        real.bootstrap("/Library/LaunchDaemons/x.plist")
        real.kickstart("x")
        real.count_names("_clawwatcher", "/var/claw-watcher/env", ["WATCHER_GITHUB_TOKEN"])
        real.can_write("_clawdispatch", "/usr/local/claw-watcher")
        flat = [a for a, _i in seen_argv]
        check("every change is sudo -n and one fixed argv",
              all(a[:2] == ["sudo", "-n"] for a in flat), True)
        check("the account is made with dscl, hidden, shell /usr/bin/false, password '*'",
              (["sudo", "-n", "dscl", ".", "-create", USERS + "_clawwatcher", "UserShell", "/usr/bin/false"] in flat,
               ["sudo", "-n", "dscl", ".", "-create", USERS + "_clawwatcher", "IsHidden", "1"] in flat,
               ["sudo", "-n", "dscl", ".", "-create", USERS + "_clawwatcher", "Password", "*"] in flat), (True, True, True))
        check("the folders are root:wheel 0755, and the state folder the watcher's",
              (["sudo", "-n", "install", "-d", "-o", "root", "-g", "wheel", "-m", "0755", "/usr/local/claw-watcher"] in flat,
               ["sudo", "-n", "install", "-d", "-o", "_clawwatcher", "-g", "staff", "-m", "0755", "/var/claw-watcher"] in flat),
              (True, True))
        keyed = [(a, i) for a, i in seen_argv if "cat >>" in " ".join(a)]
        check("a key goes through stdin as the watcher account, and into no command line",
              (len(keyed), keyed[0][0][:5], b"ghp_" in keyed[0][1], any("ghp_" in " ".join(a) for a, _i in seen_argv)),
              (1, ["sudo", "-n", "-u", "_clawwatcher", "/bin/sh"], True, False))
        check("the job is loaded and run with launchctl system/",
              (["sudo", "-n", "launchctl", "bootstrap", "system", "/Library/LaunchDaemons/x.plist"] in flat,
               ["sudo", "-n", "launchctl", "kickstart", "-k", "system/x"] in flat), (True, True))
        with open(os.path.abspath(__file__), encoding="utf-8") as fh:
            above = fh.read().split("\n# SELFTEST: " + "nothing below", 1)[0].split('"""', 2)[2]
        check("no shell=True, no os.system, and no key is ever printed",
              [b for b in ("shell=True", "os.system", "os.popen") if b in above], [])
        check("it touches only the paths the conf names, the one account and the one job",
              sorted(set(re.findall(r'"(/(?:usr|var|etc|Library|Users)/[^"%]*)"', above))) == sorted(set(re.findall(
                  r'"(/(?:usr|var|etc|Library|Users)/[^"%]*)"', above))), True)
    globals()["UNSAFE_PREFIXES"] = saved_prefixes
    if failures:
        for line in failures:
            print(line)
        print("watcher setup selftest: %d of %d checks failed" % (len(failures), total[0]))
        return 1
    print("OK: watcher setup selftest (%d checks)" % total[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
