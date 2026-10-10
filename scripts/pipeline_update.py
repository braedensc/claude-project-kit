#!/usr/bin/env python3
"""The review-and-update command: what this machine's pipeline needs updated, and a
guided update of each piece (KIT-238).

    python3 scripts/pipeline_update.py review              # read-only; changes nothing
    python3 scripts/pipeline_update.py update dispatcher   # stops for you at every change
    python3 scripts/pipeline_update.py update dispatcher --dry-run
    python3 scripts/pipeline_update.py --selftest

Settings: `update.conf` beside this kit's root (copy `update.conf.example`). Every value
is read from it; nothing about one machine lives in this file.

WHY IT EXISTS.  Nothing updated the pipeline's parts. On the reference machine on
2026-10-10 the dispatcher was three releases behind (its bundled Claude Code could not
run the newest model), Homebrew's Claude Code was four months old, and two always-on
daemons (the front door and the tunnel) were behind with nobody told. Each one of those
is a fact a person can act on in minutes, once something says it.

WHAT `review` CHECKS (this slice).  One table, one row per component:
  dispatcher   the version its own /version route reports, against the package
               registry's latest (or the version DISPATCHER_VERSION pins)
  brew         every cask in BREW_CASKS and formula in BREW_FORMULAE, against
               Homebrew's index; a formula in BREW_RESTARTS is a running daemon, and
               its update line says how to restart it
  kit          this kit checkout against its origin's default branch. The checkout
               the hooks run from is the one that matters (KIT-214).

WHAT IT DOES NOT CHECK YET.  Every review prints this list too, so a short table can
never read as "everything is current": the role account's Node, the Xcode command-line
tools behind /usr/bin/python3, macOS itself (KIT-229), the role account's kit clones and
the user-scope skills (the one command, scripts/pipeline_install.py, moves those), each
served repository's vendored contract (/sync-kit), the kit's npm dev dependencies, the
GitHub Action versions its workflows pin, and the credential clocks (KIT-60, KIT-143).

`update dispatcher`, IN ORDER.  It refuses in an agent environment, then:
  1. reads the installed and target versions; already current means done
  2. pre-flight, read-only: every name in DISPATCHER_FORBIDDEN_ENV must be absent from
     the dispatcher's env file (names only are read, never a value), and no token store
     may sit beside it. A hit stops here and changes nothing.
  3. shows the plan, says whether sessions are running, and asks you to type yes
  4. keeps a rollback copy of the installed package in the role account's home
  5. DISPATCHER_SENTRY=off: adds CYRUS_SENTRY_DISABLED=1 to the env file, if absent
  6. stops the dispatcher and waits until launchd has really let go
  7. installs the target version as the role account
  8. starts it, retrying only launchd's "still holding the old job" error, and waits
     for /version to answer with the target version
  9. records the result, and prints what to re-check: the review jobs, the chat lane
     and the idea gate's probe were all measured against the old version.
  Any failure after the stop ends with a banner that names the state the dispatcher is
  in, and the exact commands to start it or roll it back. It never rolls back by itself:
  a rollback starts nothing, and the choice is yours.

EXIT CODES — contract §13, the Stage E installer's numbers.
  0   review: everything checked is current · update: done, or already current
  1   FAILED — an update step ran and failed; the banner says what state it left
  2   USAGE or CONFIG — nothing was attempted
  3   REFUSED — an agent environment asked for an update
  4   UNKNOWN — review: a component could not be measured (that is not "current")
  10  review: something is behind · update: stopped for you (pre-flight, or no "yes")

WHAT IT NEVER DOES.  Merge, approve, label, read a credential value, or update anything
you did not name. `review` never asks for a password.
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import pipeline_stage_e_setup as se  # noqa: E402
from pipeline_dispatch_local import AGENT_ENV_MARKERS  # noqa: E402

EX_OK, EX_FAILED, EX_USAGE, EX_REFUSED, EX_UNKNOWN, EX_BLOCKED = 0, 1, 2, 3, 4, 10

CURRENT, BEHIND, AHEAD, UNKNOWN, ABSENT = "CURRENT", "BEHIND", "AHEAD", "UNKNOWN", "ABSENT"

NOT_CHECKED_YET = (
    "the role account's Node (KIT-238 slice 2)",
    "the Xcode command-line tools behind /usr/bin/python3 (KIT-238 slice 2)",
    "macOS updates (KIT-229)",
    "the role account's kit clones and the user-scope skills — run "
    "scripts/pipeline_install.py, which moves them",
    "each served repository's vendored kit contract (/sync-kit)",
    "the kit's npm dev dependencies and pinned GitHub Action versions (KIT-238 slice 2)",
    "credential expiry clocks (KIT-60, KIT-143)",
)

# Re-checks after a dispatcher update. Each was measured against the old version.
RECHECK_AFTER_DISPATCHER = (
    ("the review jobs", "python3 scripts/pipeline_stage_e_setup.py verify"),
    ("the reviewer's tool list", "python3 scripts/pipeline_stage_e_setup.py card CK-7"),
    ("the chat lane", "python3 scripts/pipeline_chat_lane_setup.py verify"),
    ("the idea gate's probe (a new version invalidates its signature)",
     "python3 scripts/pipeline_install.py"),
)

CONF_KEYS = (
    "DISPATCHER_SERVICE", "DISPATCHER_PORT", "ROLE_ACCOUNT", "DISPATCHER_ENV_FILE",
    "DISPATCHER_PACKAGE", "DISPATCHER_VERSION", "DISPATCHER_NODE_SETUP",
    "DISPATCHER_SENTRY", "DISPATCHER_FORBIDDEN_ENV",
    "BREW_CASKS", "BREW_FORMULAE", "BREW_RESTARTS", "KIT_CHECKOUT",
)
CONF_DEFAULTS = {
    "DISPATCHER_PORT": "3456",
    "DISPATCHER_PACKAGE": "cyrus-ai",
    "DISPATCHER_VERSION": "latest",
    "DISPATCHER_NODE_SETUP": '. "$HOME/.nvm/nvm.sh"',
    "DISPATCHER_SENTRY": "unchanged",
    "DISPATCHER_FORBIDDEN_ENV": "CYRUS_API_KEY,CYRUS_TEAM_ID,CYRUS_ENABLE_WARM_SESSIONS,ZULIP_*",
    "BREW_CASKS": "",
    "BREW_FORMULAE": "",
    "BREW_RESTARTS": "",
    "KIT_CHECKOUT": "",
}
DISPATCHER_KEYS = ("DISPATCHER_SERVICE", "ROLE_ACCOUNT", "DISPATCHER_ENV_FILE")
CONF_UNEDITED = {"DISPATCHER_SERVICE": "com.example.dispatcher",
                 "ROLE_ACCOUNT": "_exdispatch",
                 "DISPATCHER_ENV_FILE": "/opt/example-dispatcher/.env"}
# A token store the dispatcher reads at start-up and acts on (0.2.73: it rewrites the
# role account's git credential helper). Absent on a machine that never asked for it.
TOKEN_STORE = "github-tokens.json"
VERSION_RE = re.compile(r"^\d+(\.\d+)*$")
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9@._/+-]+$")
ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*\*?$")

DISPATCHER_ANSWER_WAIT_SECONDS = 90     # KIT-206 measured ~30 s; headroom for a cold start
DISPATCHER_ANSWER_POLL_SECONDS = 5
INSTALL_TIMEOUT_SECONDS = 900


def say(msg=""):
    se.say(msg)


class UpdateError(Exception):
    """A step ran and failed, or the conf is wrong; the message says which."""


class ConfError(UpdateError):
    """The conf is missing or wrong; nothing was attempted."""


class Refusal(Exception):
    pass


def refuse_if_agent(action, env=None):
    env = os.environ if env is None else env
    found = [m for m in AGENT_ENV_MARKERS if m in env]
    if found:
        raise Refusal(
            "REFUSED: `%s` changes this machine and this is an agent environment (%s set).\n"
            "  It stops and restarts the dispatcher that starts every session; a session\n"
            "  doing that to itself is exactly what the refusal is for. A PERSON runs it:\n"
            "      python3 scripts/pipeline_update.py %s\n"
            "  Read-only meanwhile:  python3 scripts/pipeline_update.py review"
            % (action, ", ".join(found), action))


# --------------------------------------------------------------------------- #
# The conf — parsed, never sourced, every error at once.
# --------------------------------------------------------------------------- #
def split_list(value):
    return [p.strip() for p in (value or "").split(",") if p.strip()]


def parse_conf(text, source="update.conf"):
    values, seen, errors = {}, {}, []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            errors.append("%s:%d: not KEY=value: %r" % (source, n, raw[:60]))
            continue
        key, val = (part.strip() for part in line.split("=", 1))
        if key in seen:
            errors.append("%s:%d: duplicate key %s, first set at line %d" % (source, n, key, seen[key]))
            continue
        if key not in CONF_KEYS:
            errors.append("%s:%d: unknown key %s (see update.conf.example)" % (source, n, key))
            continue
        if any(p in val for p in se._CRED_PREFIXES) or any(
                se._BLOB_RE.match(t) for t in re.split(r"[\s,]+", val) if "/" not in t):
            errors.append("%s:%d: %s carries a CREDENTIAL SHAPE; no key here ever holds one "
                          "(the value is not shown)" % (source, n, key))
            continue
        seen[key] = n
        values[key] = val
    return values, errors


def validate_conf(values):
    conf, errors = dict(CONF_DEFAULTS), []
    conf.update(values)
    on = [k for k in DISPATCHER_KEYS if conf.get(k)]
    if on and len(on) != len(DISPATCHER_KEYS):
        errors.append("the dispatcher needs all of %s, or none of them (got only %s)"
                      % (", ".join(DISPATCHER_KEYS), ", ".join(on)))
    for key, placeholder in CONF_UNEDITED.items():
        if conf.get(key) == placeholder:
            errors.append("%s is still the example value %r — this conf has not been edited"
                          % (key, placeholder))
    if conf.get("DISPATCHER_SERVICE") and not se._RDNS_RE.match(conf["DISPATCHER_SERVICE"]):
        errors.append("DISPATCHER_SERVICE %r is not a reverse-DNS launchd label"
                      % conf["DISPATCHER_SERVICE"])
    if not conf.get("DISPATCHER_PORT", "").isdigit():
        errors.append("DISPATCHER_PORT must be a port number (got %r)" % conf.get("DISPATCHER_PORT"))
    env_file = conf.get("DISPATCHER_ENV_FILE", "")
    if env_file and not env_file.startswith("/"):
        errors.append("DISPATCHER_ENV_FILE must be an absolute path (got %r)" % env_file)
    pkg = conf.get("DISPATCHER_PACKAGE", "")
    if not SAFE_NAME_RE.match(pkg):
        errors.append("DISPATCHER_PACKAGE %r is not a package name" % pkg)
    ver = conf.get("DISPATCHER_VERSION", "")
    if ver != "latest" and not VERSION_RE.match(ver):
        errors.append("DISPATCHER_VERSION must be `latest` or an exact version like 0.2.73 (got %r)" % ver)
    if conf.get("DISPATCHER_SENTRY") not in ("off", "unchanged"):
        errors.append("DISPATCHER_SENTRY must be `off` or `unchanged` (got %r)"
                      % conf.get("DISPATCHER_SENTRY"))
    for name in split_list(conf.get("DISPATCHER_FORBIDDEN_ENV")):
        if not ENV_NAME_RE.match(name):
            errors.append("DISPATCHER_FORBIDDEN_ENV entry %r is not an env name (a trailing * "
                          "matches a prefix)" % name)
    for key in ("BREW_CASKS", "BREW_FORMULAE"):
        for name in split_list(conf.get(key)):
            if not SAFE_NAME_RE.match(name):
                errors.append("%s entry %r is not a Homebrew name" % (key, name))
    for pair in split_list(conf.get("BREW_RESTARTS")):
        formula, _, label = pair.partition(":")
        if not formula or not se._RDNS_RE.match(label):
            errors.append("BREW_RESTARTS entry %r must be formula:launchd.label" % pair)
        elif formula not in split_list(conf.get("BREW_FORMULAE")):
            errors.append("BREW_RESTARTS names %r, which is not in BREW_FORMULAE" % formula)
    kit = conf.get("KIT_CHECKOUT", "")
    if kit and not kit.startswith("/"):
        errors.append("KIT_CHECKOUT must be an absolute path (got %r)" % kit)
    return conf, errors


def load_conf(path):
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        raise ConfError("could not read %s: %s\n  cp update.conf.example update.conf "
                          "&& $EDITOR update.conf" % (path, exc))
    values, errors = parse_conf(text, os.path.basename(path))
    conf, more = validate_conf(values)
    errors += more
    if errors:
        raise ConfError("update.conf has %d problem(s); nothing was attempted:\n  "
                          % len(errors) + "\n  ".join(errors))
    conf["__source__"] = path
    return conf


# --------------------------------------------------------------------------- #
# Versions
# --------------------------------------------------------------------------- #
def vkey(v):
    return tuple(int(p) for p in re.findall(r"\d+", v or ""))


def compare(installed, target):
    if not installed or not target:
        return UNKNOWN
    a, b = vkey(installed), vkey(target)
    if not a or not b:
        return UNKNOWN
    return CURRENT if a == b else (BEHIND if a < b else AHEAD)


def http_json(url, timeout=4):
    """GET a local JSON route; None when it does not answer. Loopback only."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (loopback)
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None


class Env(object):
    """Everything outside this process, in one place, so the battery replaces it whole."""

    def __init__(self, runner=None, http=None, sleep=None, ask=None, environ=None, home=None):
        self.runner = runner or se.Runner()
        self.http = http or http_json
        self.sleep = sleep or time.sleep
        self.ask = ask or input
        self.environ = os.environ if environ is None else environ
        self.home = home or os.path.expanduser("~")


def dispatcher_version(env, conf):
    got = env.http("http://127.0.0.1:%s/version" % conf["DISPATCHER_PORT"])
    v = got.get("cyrus_cli_version") if isinstance(got, dict) else None
    return v if isinstance(v, str) and v else None


def dispatcher_status(env, conf):
    got = env.http("http://127.0.0.1:%s/status" % conf["DISPATCHER_PORT"])
    s = got.get("status") if isinstance(got, dict) else None
    return s if isinstance(s, str) else None


def registry_latest(env, package):
    out = env.runner.read(["npm", "view", package, "version"], timeout=60)
    v = out.out.strip() if out.ok else ""
    return v if VERSION_RE.match(v) else None


def dispatcher_target(env, conf):
    if conf["DISPATCHER_VERSION"] != "latest":
        return conf["DISPATCHER_VERSION"]
    return registry_latest(env, conf["DISPATCHER_PACKAGE"])


# --------------------------------------------------------------------------- #
# review — read-only, everywhere
# --------------------------------------------------------------------------- #
def row(component, state, installed, target, command, note=""):
    return {"component": component, "state": state, "installed": installed or "?",
            "target": target or "?", "command": command, "note": note}


def review_dispatcher(env, conf):
    if not conf.get("DISPATCHER_SERVICE"):
        return [row("dispatcher", ABSENT, None, None, "",
                    "not configured in update.conf on this machine")]
    installed = dispatcher_version(env, conf)
    target = dispatcher_target(env, conf)
    pin = "" if conf["DISPATCHER_VERSION"] == "latest" else " (pinned)"
    note = ""
    if installed is None:
        note = ("the dispatcher did not answer on 127.0.0.1:%s — it may be down; that is "
                "not 'current'" % conf["DISPATCHER_PORT"])
    elif target is None:
        note = "the package registry did not answer; the latest version is unknown"
    return [row("dispatcher (%s)" % conf["DISPATCHER_PACKAGE"], compare(installed, target),
                installed, (target or "") + pin,
                "python3 scripts/pipeline_update.py update dispatcher", note)]


def review_brew(env, conf):
    casks, formulae = split_list(conf.get("BREW_CASKS")), split_list(conf.get("BREW_FORMULAE"))
    if not casks and not formulae:
        return []
    restarts = dict(p.split(":", 1) for p in split_list(conf.get("BREW_RESTARTS")))
    rows = []
    for kind, names in (("cask", casks), ("formula", formulae)):
        if not names:
            continue
        argv = ["brew", "info", "--json=v2"] + (["--cask"] if kind == "cask" else []) + names
        out = env.runner.read(argv, timeout=120)
        try:
            data = json.loads(out.out) if out.ok else None
        except ValueError:
            data = None
        if data is None:
            for n in names:
                rows.append(row("%s %s" % (kind, n), UNKNOWN, None, None, "",
                                "Homebrew did not answer: %s" % (out.err.strip()[:80] or "no output")))
            continue
        found = {}
        for item in data.get("casks", []) if kind == "cask" else data.get("formulae", []):
            if kind == "cask":
                found[item.get("token")] = (item.get("installed"), item.get("version"))
            else:
                inst = item.get("installed") or []
                found[item.get("name")] = (inst[-1].get("version") if inst else None,
                                           (item.get("versions") or {}).get("stable"))
        for n in names:
            installed, latest = found.get(n, (None, None))
            if not installed:
                rows.append(row("%s %s" % (kind, n), ABSENT, None, latest, "",
                                "not installed with Homebrew on this machine"))
                continue
            cmd = "brew upgrade %s%s" % ("--cask " if kind == "cask" else "", n)
            note = ""
            if n in restarts:
                cmd += " && sudo launchctl kickstart -k system/%s" % restarts[n]
                note = "a running daemon: the restart line picks up the new binary"
            rows.append(row("%s %s" % (kind, n), compare(installed, latest), installed, latest,
                            cmd, note))
    return rows


def review_kit(env, conf):
    kit = conf.get("KIT_CHECKOUT") or KIT
    r = env.runner
    head = r.read(["git", "-C", kit, "rev-parse", "HEAD"])
    branch = r.read(["git", "-C", kit, "rev-parse", "--abbrev-ref", "HEAD"])
    remote = r.read(["git", "-C", kit, "ls-remote", "--symref", "origin", "HEAD"], timeout=30)
    if not (head.ok and branch.ok and remote.ok):
        return [row("kit checkout", UNKNOWN, None, None, "",
                    "could not read %s or its origin" % kit)]
    tip = default = None
    for line in remote.out.splitlines():
        if line.startswith("ref: ") and line.endswith("\tHEAD"):
            default = line[len("ref: refs/heads/"):].split("\t")[0]
        elif line.endswith("\tHEAD"):
            tip = line.split("\t")[0]
    here = head.out.strip()
    name = "kit checkout"
    if not tip:
        return [row(name, UNKNOWN, here[:7], None, "", "origin named no default branch")]
    note = kit
    if default and branch.out.strip() != default:
        note += " — on %s, not %s: the one command fast-forwards only the default branch" % (
            branch.out.strip(), default)
    if here == tip:
        return [row(name, CURRENT, here[:7], tip[:7], "", note)]
    ahead = r.read(["git", "-C", kit, "merge-base", "--is-ancestor", tip, here])
    state = AHEAD if ahead.ok else BEHIND
    return [row(name, state, here[:7], tip[:7],
                "python3 scripts/pipeline_install.py   (fast-forwards it, then the rest)", note)]


def review(env, conf, as_json=False):
    rows = review_dispatcher(env, conf) + review_brew(env, conf) + review_kit(env, conf)
    states = [r["state"] for r in rows]
    code = EX_UNKNOWN if UNKNOWN in states else (EX_BLOCKED if BEHIND in states else EX_OK)
    if as_json:
        print(json.dumps({"rows": rows, "not_checked_yet": list(NOT_CHECKED_YET),
                          "exit": code}, indent=1))
        return code
    say("PIPELINE REVIEW — what this machine runs, and what is newer")
    say("")
    for r_ in rows:
        say("  %-8s %-46s %s -> %s" % (r_["state"], r_["component"][:46], r_["installed"],
                                       r_["target"]))
        if r_["note"]:
            say("           %s" % r_["note"])
        if r_["state"] == BEHIND and r_["command"]:
            say("           update: %s" % r_["command"])
    say("")
    say("NOT CHECKED YET (so this table is not the whole machine):")
    for item in NOT_CHECKED_YET:
        say("  - %s" % item)
    say("")
    say({EX_OK: "Everything checked is current.",
         EX_BLOCKED: "Something is behind: the update lines above say how.",
         EX_UNKNOWN: "Something could not be measured. Read its note: UNKNOWN is not current."}[code])
    return code


# --------------------------------------------------------------------------- #
# update dispatcher
# --------------------------------------------------------------------------- #
class UpdateSudo(se.SudoSession):
    """The Stage E installer's one-prompt sudo session, with this command's own words."""

    def acquire(self, why, resume):
        if self.held:
            return True
        self.acquisitions += 1
        say("")
        say("ADMINISTRATOR PASSWORD — asked for once, here. Nothing has been changed yet.")
        say("WHY: %s" % why)
        rc = self._validate()
        if rc != 0:
            raise se.NoPrivilege(
                "NO ADMINISTRATOR ACCESS — nothing was attempted, and nothing was changed.\n"
                "  Run the same command again in your own terminal:\n"
                "      python3 scripts/pipeline_update.py %s" % resume)
        self.held = True
        self._start_keepalive()
        return True


def _role(conf, script):
    """A script for the role account, run under bash as a login shell: the dispatcher's
    Node comes from nvm, which needs bash (the original install scripts did the same)."""
    inner = '%s >/dev/null 2>&1; cd "$HOME" || exit 1; %s' % (conf["DISPATCHER_NODE_SETUP"], script)
    return "exec /bin/bash -lc %s" % shlex.quote(inner)


def forbidden_present(env, conf):
    """The forbidden env NAMES present in the dispatcher's env file, and whether a token
    store sits beside it. Reads names only: the pattern stops at the `=`."""
    names = split_list(conf["DISPATCHER_FORBIDDEN_ENV"])
    alts = "|".join(re.escape(n[:-1]) + "[A-Z0-9_]*" if n.endswith("*") else re.escape(n)
                    for n in names)
    hits = []
    if alts:
        got = env.runner.as_root(["/usr/bin/grep", "-oE", "^(%s)=" % alts,
                                  conf["DISPATCHER_ENV_FILE"]])
        if got.rc not in (0, 1):
            raise UpdateError("could not read the dispatcher's env file %s (grep exit %d): %s"
                              % (conf["DISPATCHER_ENV_FILE"], got.rc, got.err.strip()[:120]))
        hits = sorted({h.rstrip("=") for h in got.out.split()})
    store = os.path.join(os.path.dirname(conf["DISPATCHER_ENV_FILE"]), TOKEN_STORE)
    has_store = env.runner.as_root(["/bin/test", "-e", store]).ok
    return hits, (store if has_store else None)


def env_has(env, conf, name):
    got = env.runner.as_root(["/usr/bin/grep", "-qE", "^%s=" % re.escape(name),
                              conf["DISPATCHER_ENV_FILE"]])
    return got.ok


def _start(env, conf, label, plist):
    """Bootstrap, retrying only EIO (launchd still holding the old job), then wait for
    /version to answer. Returns the version it answers with, or raises."""
    r = env.runner
    limit = se._exit_timeout(r, plist) + se.EXIT_TIMEOUT_HEADROOM
    said = ""
    for attempt in range(1, se.BOOTSTRAP_ATTEMPTS + 1):
        if attempt > 1:
            se._pause(se.BOOTSTRAP_RETRY_SECONDS)
            if not se._wait_until_gone(r, label, limit)[0]:
                said = "launchd still holds %s; another bootstrap would only answer EIO" % label
                break
        boot = r.as_root(["launchctl", "bootstrap", "system", plist],
                         why="start the dispatcher on the new version")
        if boot.skipped:
            return None
        if boot.ok and se._service_in_domain(r, label):
            break
        said = (boot.err or boot.out).strip() or "bootstrap said nothing"
        if not se._EIO.search(said):
            raise UpdateError("STOPPED: the dispatcher did not start. launchd said: %s"
                              % said.splitlines()[0][:160])
    else:
        raise UpdateError("STOPPED: the dispatcher did not start after %d attempts: %s"
                          % (se.BOOTSTRAP_ATTEMPTS, said[:160]))
    if said and not se._service_in_domain(r, label):
        raise UpdateError("STOPPED: %s" % said)
    waited = 0
    while True:
        v = dispatcher_version(env, conf)
        if v:
            return v
        if waited >= DISPATCHER_ANSWER_WAIT_SECONDS:
            raise UpdateError("RUNNING BUT SILENT: launchd started the dispatcher and it has not "
                              "answered /version in %d s" % waited)
        if waited == 0:
            say("  waiting for the dispatcher to answer ...")
        env.sleep(DISPATCHER_ANSWER_POLL_SECONDS)
        waited += DISPATCHER_ANSWER_POLL_SECONDS


def _banner(conf, state, frm, to, copy):
    label = conf["DISPATCHER_SERVICE"]
    plist = se._dispatcher_plist(label)
    pkg = conf["DISPATCHER_PACKAGE"]
    say("")
    say("=" * 74)
    say("THE DISPATCHER IS %s. Nothing else will start it." % state)
    say("=" * 74)
    say("Start it as it is now:")
    say("    sudo launchctl bootstrap system %s" % plist)
    if copy:
        say("Or roll back to %s first (the copy taken before the change):" % frm)
        say("    sudo launchctl bootout system/%s" % label)
        say("    sudo -u %s -H /bin/sh -c %s" % (conf["ROLE_ACCOUNT"], shlex.quote("cd / && " + _role(
            conf, 'R="$(npm root -g)"; mv "$R/%s" "$R/%s.%s.off" && ditto "%s/%s" "$R/%s"'
            % (pkg, pkg, to or "new", copy, pkg, pkg)))))
        say("    sudo launchctl bootstrap system %s" % plist)
    say("Then:  python3 scripts/pipeline_update.py review")
    say("=" * 74)


def record(env, entry):
    path = os.path.join(env.home, ".pipeline-update", "ledger.jsonl")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError as exc:
        say("  (could not record this in %s: %s)" % (path, exc))


def update_dispatcher(env, conf, dry_run=False, sudo=None):
    refuse_if_agent("update dispatcher" + (" --dry-run" if dry_run else ""), env.environ)
    if not conf.get("DISPATCHER_SERVICE"):
        raise UpdateError("no dispatcher is configured in update.conf (DISPATCHER_SERVICE is empty)")
    if sys.platform != "darwin" and not getattr(env, "any_platform", False):
        raise UpdateError("this update drives launchd, so it runs on macOS only")
    label, account = conf["DISPATCHER_SERVICE"], conf["ROLE_ACCOUNT"]
    plist, pkg = se._dispatcher_plist(label), conf["DISPATCHER_PACKAGE"]

    frm = dispatcher_version(env, conf)
    to = dispatcher_target(env, conf)
    if frm is None:
        raise UpdateError("the dispatcher did not answer on 127.0.0.1:%s, so its version is "
                          "unknown. Start it, or check it, before updating it." % conf["DISPATCHER_PORT"])
    if to is None:
        raise UpdateError("the package registry did not say what %s's latest version is" % pkg)
    sentry_off = conf["DISPATCHER_SENTRY"] == "off"

    (sudo or UpdateSudo()).acquire(
        "read the dispatcher's env file (names only) and, if you go ahead, stop, update "
        "and start the dispatcher as root and as %s" % account, "update dispatcher")

    need_sentry = sentry_off and not env_has(env, conf, "CYRUS_SENTRY_DISABLED")
    if compare(frm, to) == CURRENT and not need_sentry:
        say("ALREADY CURRENT: the dispatcher runs %s, the target. Nothing to do." % frm)
        return EX_OK

    hits, store = forbidden_present(env, conf)
    if hits or store:
        say("STOPPED BEFORE ANY CHANGE — the pre-flight found what an update would act on:")
        for h in hits:
            say("  - %s is set in %s" % (h, conf["DISPATCHER_ENV_FILE"]))
        if store:
            say("  - a token store exists: %s" % store)
        say("Each one changes what the new version does at start-up (see the KIT-235 review).")
        say("Remove it, or take it out of DISPATCHER_FORBIDDEN_ENV on purpose, and run again.")
        return EX_BLOCKED

    status = dispatcher_status(env, conf)
    say("")
    say("PLAN: update the dispatcher %s -> %s" % (frm, to))
    say("  1. keep a rollback copy of %s %s in %s's home" % (pkg, frm, account))
    if need_sentry:
        say("  2. add CYRUS_SENTRY_DISABLED=1 to %s" % conf["DISPATCHER_ENV_FILE"])
    say("  3. stop it (launchd label %s) and wait until it is really gone" % label)
    say("  4. install %s@%s as %s" % (pkg, to, account))
    say("  5. start it and wait for it to answer with %s" % to)
    if status and status != "idle":
        say("  NOTE: it reports `%s` now. Stopping it cuts off any session it is running." % status)
    elif not status:
        say("  NOTE: its /status did not answer; whether a session is running is unknown.")
    if dry_run:
        say("DRY RUN: the steps below say WOULD and change nothing.")
    elif env.ask("Type yes to go ahead: ").strip().lower() != "yes":
        say("Not confirmed. Nothing was changed.")
        return EX_BLOCKED

    r = env.runner
    copy = "$HOME/%s-rollback-%s" % (pkg.replace("/", "_"), frm)
    got = r.as_role(account, _role(conf, 'R="$(npm root -g)"; D="%s"; if [ -e "$D/%s" ]; then '
                    'echo kept; else mkdir -p "$D" && ditto "$R/%s" "$D/%s" && echo copied; fi'
                    % (copy, pkg, pkg, pkg)), why="keep a rollback copy of %s %s" % (pkg, frm))
    if not got.ok and not got.skipped:
        raise UpdateError("could not keep a rollback copy (nothing else was changed): %s"
                          % (got.err or got.out).strip()[:160])
    home = r.read(["/bin/sh", "-c", "echo ~%s" % shlex.quote(account)]).out.strip()
    copy_real = copy.replace("$HOME", home) if home and not home.startswith("~") else copy

    if need_sentry:
        line = "\\nCYRUS_SENTRY_DISABLED=1\\n"
        got = r.as_root(["/bin/sh", "-c", "printf '%s' >> %s" % (line, shlex.quote(
            conf["DISPATCHER_ENV_FILE"]))], why="turn the dispatcher's error reporting off")
        if not got.ok and not got.skipped:
            raise UpdateError("could not add CYRUS_SENTRY_DISABLED (nothing else was changed): %s"
                              % got.err.strip()[:160])

    stop = r.as_root(["launchctl", "bootout", "system/" + label], why="stop the dispatcher")
    if stop.skipped:
        say("DRY RUN complete. Nothing was changed.")
        return EX_OK
    gone, waited = se._wait_until_gone(r, label, se._exit_timeout(r, plist) + se.EXIT_TIMEOUT_HEADROOM)
    if not gone:
        _banner(conf, "IN AN UNKNOWN STATE (launchd still holds it after %ds)" % waited, frm, to, None)
        raise UpdateError("launchd still holds the dispatcher after %ds; nothing was installed" % waited)

    inst = r.as_role(account, _role(conf, "npm install -g %s@%s" % (pkg, to)),
                     why="install %s@%s" % (pkg, to), timeout=INSTALL_TIMEOUT_SECONDS)
    if not inst.ok:
        _banner(conf, "STOPPED (the install failed)", frm, to, copy_real)
        record(env, {"component": "dispatcher", "from": frm, "to": to, "result": "install-failed"})
        raise UpdateError("npm install failed: %s" % (inst.err or inst.out).strip()[-200:])

    try:
        now = _start(env, conf, label, plist)
    except UpdateError as exc:
        _banner(conf, "STOPPED" if "STOPPED" in str(exc) else "RUNNING BUT SILENT", frm, to, copy_real)
        record(env, {"component": "dispatcher", "from": frm, "to": to, "result": "start-failed"})
        raise
    if compare(now, to) != CURRENT:
        _banner(conf, "RUNNING %s, NOT %s" % (now, to), frm, to, copy_real)
        record(env, {"component": "dispatcher", "from": frm, "to": to, "result": "wrong-version",
                     "running": now})
        raise UpdateError("the dispatcher answers %s after installing %s" % (now, to))

    record(env, {"component": "dispatcher", "from": frm, "to": to, "result": "done",
                 "sentry_off": bool(need_sentry)})
    say("")
    say("DONE: the dispatcher runs %s (was %s). Rollback copy: %s" % (now, frm, copy_real))
    say("RE-CHECK NOW — each was measured against %s:" % frm)
    for what, cmd in RECHECK_AFTER_DISPATCHER:
        say("  %-58s %s" % (what, cmd))
    say("Then delegate one small ticket and confirm its session's model in the dispatcher log.")
    return EX_OK


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None, env=None):
    p = argparse.ArgumentParser(prog="pipeline_update.py", description=__doc__.split("\n")[0])
    p.add_argument("--conf", default=os.path.join(KIT, "update.conf"))
    p.add_argument("--selftest", action="store_true")
    sub = p.add_subparsers(dest="cmd")
    rv = sub.add_parser("review")
    rv.add_argument("--json", action="store_true")
    up = sub.add_parser("update")
    up.add_argument("component", choices=["dispatcher"])
    up.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.cmd:
        p.print_help()
        return EX_USAGE
    env = env or Env()
    try:
        if args.cmd == "update":
            refuse_if_agent("update %s" % args.component, env.environ)
        conf = load_conf(args.conf)
        if args.cmd == "review":
            return review(env, conf, as_json=args.json)
        if args.dry_run:
            env.runner.dry_run = True
        return update_dispatcher(env, conf, dry_run=args.dry_run)
    except Refusal as exc:
        say(str(exc))
        return EX_REFUSED
    except se.NoPrivilege as exc:
        say(str(exc))
        return EX_BLOCKED
    except ConfError as exc:
        say("CONFIG: %s" % exc)
        return EX_USAGE
    except UpdateError as exc:
        say("FAILED: %s" % exc)
        return EX_FAILED


# --------------------------------------------------------------------------- #
# --selftest — offline; every transport stubbed
# --------------------------------------------------------------------------- #
class _Sudo(object):
    def __init__(self):
        self.acquisitions = 0

    def acquire(self, why, resume):
        self.acquisitions += 1
        return True


class _Launchd(se.FakeLaunchd):
    """se.FakeLaunchd plus an npm table and a dispatcher whose version follows the install."""

    def __init__(self, answers=None, installed="0.2.69", install_rc=0, **kw):
        se.FakeLaunchd.__init__(self, answers, **kw)
        self.version = installed
        self.install_rc = install_rc

    def _exec(self, argv, stdin, timeout, cwd=None):
        line = se._fmt(argv)
        m = re.search(r"npm install -g [^@\s]+@([0-9][0-9.]*)", line)
        if m:
            if not self.install_rc:
                self.version = m.group(1)
            return se.Result(self.install_rc, "", "" if not self.install_rc else "npm ERR! boom")
        return se.FakeLaunchd._exec(self, argv, stdin, timeout, cwd)


def _conf(**over):
    values = {"DISPATCHER_SERVICE": "com.test.dispatcher", "ROLE_ACCOUNT": "_testdispatch",
              "DISPATCHER_ENV_FILE": "/opt/test-dispatch/.env", "DISPATCHER_VERSION": "0.2.73",
              "BREW_CASKS": "claude-code", "BREW_FORMULAE": "caddy,gh",
              "BREW_RESTARTS": "caddy:com.test.front-door", "KIT_CHECKOUT": "/kit"}
    values.update(over)
    conf, errors = validate_conf({k: v for k, v in values.items() if v is not None})
    assert not errors, errors
    return conf


def selftest():
    import tempfile
    fails, ran = [], [0]

    def check(name, got, want):
        ran[0] += 1
        ok = got == want
        print("[%s] %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  (got %r, want %r)" % (got, want)))
        if not ok:
            fails.append(name)

    se._pause = lambda s: None      # the restart helpers wait by polls, not by the clock
    quiet = []
    se.say = lambda msg="": quiet.append(msg)
    home = tempfile.mkdtemp(prefix="update-selftest-")

    # -- conf ---------------------------------------------------------------
    _, errs = parse_conf("NOPE=1\nDISPATCHER_PORT=1\nDISPATCHER_PORT=2\n")
    check("conf: unknown key and duplicate key are both reported", len(errs), 2)
    _, errs = parse_conf("BREW_CASKS=" + "gh" + "p_" + "A" * 36)
    check("conf: a credential shape is refused without echoing it",
          (len(errs), "ghp_" in errs[0]), (1, False))
    _, errs = validate_conf({"DISPATCHER_SERVICE": "com.x.d"})
    check("conf: a half-configured dispatcher is refused", any("all of" in e for e in errs), True)
    _, errs = validate_conf({"DISPATCHER_SERVICE": "com.example.dispatcher",
                             "ROLE_ACCOUNT": "_exdispatch",
                             "DISPATCHER_ENV_FILE": "/opt/example-dispatcher/.env"})
    check("conf: the unedited example values are refused", len([e for e in errs if "example value" in e]), 3)
    _, errs = validate_conf({"DISPATCHER_VERSION": "newest", "BREW_RESTARTS": "caddy:com.x.c"})
    check("conf: a vague version and a restart for an unlisted formula are refused", len(errs), 2)
    try:
        load_conf(os.path.join(KIT, "update.conf.example"))
        said = ""
    except ConfError as exc:
        said = str(exc)
    check("conf: the committed example parses, and is refused only for its 3 placeholders",
          (said.count("example value"), "unknown key" in said, "not KEY=value" in said),
          (3, False, False))

    # -- review ---------------------------------------------------------------
    brew_cask = json.dumps({"casks": [{"token": "claude-code", "installed": "2.1.153", "version": "2.1.287"}]})
    brew_form = json.dumps({"formulae": [
        {"name": "caddy", "installed": [{"version": "2.10.0"}], "versions": {"stable": "2.10.2"}},
        {"name": "gh", "installed": [{"version": "2.102.0"}], "versions": {"stable": "2.102.0"}}]})
    tip, mine = "b" * 40, "a" * 40
    table = [("brew info --json=v2 --cask claude-code", 0, brew_cask),
             ("brew info --json=v2 caddy gh", 0, brew_form),
             ("rev-parse HEAD", 0, mine + "\n"), ("rev-parse --abbrev-ref HEAD", 0, "main\n"),
             ("ls-remote", 0, "ref: refs/heads/main\tHEAD\n%s\tHEAD\n" % tip),
             ("merge-base --is-ancestor", 1, "")]

    def http(answers):
        return lambda url: answers.get(url.rsplit("/", 1)[-1])

    env = Env(runner=se.FakeRunner(table), http=http({"version": {"cyrus_cli_version": "0.2.69"}}),
              home=home)
    rows = {r["component"].split(" (")[0]: r["state"] for r in
            review_dispatcher(env, _conf()) + review_brew(env, _conf()) + review_kit(env, _conf())}
    check("review: dispatcher 0.2.69 against a 0.2.73 pin is BEHIND", rows.get("dispatcher"), BEHIND)
    check("review: an old cask is BEHIND", rows.get("cask claude-code"), BEHIND)
    check("review: a daemon formula one patch behind is BEHIND", rows.get("formula caddy"), BEHIND)
    check("review: an up-to-date formula is CURRENT", rows.get("formula gh"), CURRENT)
    check("review: a kit checkout behind origin is BEHIND", rows.get("kit checkout"), BEHIND)
    caddy = [r for r in review_brew(env, _conf()) if r["component"] == "formula caddy"][0]
    check("review: a daemon's update line restarts it", "kickstart -k system/com.test.front-door" in caddy["command"], True)
    check("review: BEHIND exits 10", review(env, _conf()), EX_BLOCKED)
    quiet[:] = []
    review(env, _conf())
    check("review: the not-checked-yet list is printed every time",
          sum(1 for q in quiet if q.startswith("  - ")), len(NOT_CHECKED_YET))
    silent = Env(runner=se.FakeRunner(table), http=http({}), home=home)
    check("review: a dispatcher that does not answer is UNKNOWN, not current",
          review_dispatcher(silent, _conf())[0]["state"], UNKNOWN)
    check("review: UNKNOWN outranks BEHIND in the exit code", review(silent, _conf()), EX_UNKNOWN)
    nobrew = Env(runner=se.FakeRunner([t for t in table if "brew" not in t[0]]),
                 http=http({"version": {"cyrus_cli_version": "0.2.73"}}), home=home)
    check("review: Homebrew not answering is UNKNOWN per package",
          [r["state"] for r in review_brew(nobrew, _conf())], [UNKNOWN] * 3)
    none = _conf(DISPATCHER_SERVICE="", ROLE_ACCOUNT="", DISPATCHER_ENV_FILE="")
    check("review: no dispatcher configured says so (ABSENT), it does not guess",
          review_dispatcher(env, none)[0]["state"], ABSENT)
    allcur = Env(runner=se.FakeRunner([("brew info --json=v2 --cask", 0, json.dumps(
        {"casks": [{"token": "claude-code", "installed": "2.1.287", "version": "2.1.287"}]})),
        ("brew info --json=v2 caddy gh", 0, json.dumps({"formulae": [
            {"name": "caddy", "installed": [{"version": "2.10.2"}], "versions": {"stable": "2.10.2"}},
            {"name": "gh", "installed": [{"version": "2.102.0"}], "versions": {"stable": "2.102.0"}}]})),
        ("rev-parse HEAD", 0, tip + "\n"), ("rev-parse --abbrev-ref HEAD", 0, "main\n"),
        ("ls-remote", 0, "ref: refs/heads/main\tHEAD\n%s\tHEAD\n" % tip)]),
        http=http({"version": {"cyrus_cli_version": "0.2.73"}}), home=home)
    check("review: everything current exits 0", review(allcur, _conf()), EX_OK)

    # -- update dispatcher ------------------------------------------------------
    def up_env(launchd, versions=("0.2.69",), answer="yes", agent=False):
        state = {"v": launchd}

        def ver(url):
            if url.endswith("/status"):
                return {"status": "idle"}
            return {"cyrus_cli_version": state["v"].version}
        return Env(runner=launchd, http=ver, sleep=lambda s: None,
                   ask=lambda prompt: answer,
                   environ={"CLAUDECODE": "1"} if agent else {}, home=home)

    base = [("grep -oE", 1, ""), ("/bin/test -e", 1, ""), ("grep -qE", 1, ""),
            ("echo ~", 0, "/var/selftest-home/_testdispatch\n"), ("npm root -g", 0, "copied\n"),
            ("PlistBuddy", 0, "120\n"), ("printf", 0, "")]
    ok_env = up_env(_Launchd(base))
    ok_env.any_platform = True
    try:
        refuse_if_agent("update dispatcher", {"CLAUDECODE": "1"})
        refused = False
    except Refusal:
        refused = True
    check("update: refused in an agent environment", refused, True)
    check("update: refused through main() before the conf is even read (exit 3)",
          main(["--conf", "/nonexistent", "update", "dispatcher"],
               env=Env(environ={"CLAUDECODE": "1"})), EX_REFUSED)
    check("review: a missing conf is a CONFIG problem (exit 2), not a failure",
          main(["--conf", "/nonexistent", "review"], env=Env(environ={})), EX_USAGE)

    code = update_dispatcher(ok_env, _conf(DISPATCHER_SENTRY="off"), sudo=_Sudo())
    order = [w["why"] for w in ok_env.runner.writes]
    check("update: the happy path ends DONE", code, EX_OK)
    want = ["keep a rollback copy of cyrus-ai 0.2.69", "turn the dispatcher's error reporting off",
            "stop the dispatcher", "install cyrus-ai@0.2.73", "start the dispatcher on the new version"]
    check("update: copy, Sentry off, stop, install, start — in that order", order, want)

    already = up_env(_Launchd(base, installed="0.2.73"))
    already.any_platform = True
    check("update: already current with Sentry unchanged changes nothing",
          (update_dispatcher(already, _conf(), sudo=_Sudo()), already.runner.writes), (EX_OK, []))

    pre = up_env(_Launchd([("grep -oE", 0, "CYRUS_API_KEY=\nZULIP_SITE=\n")] + base[1:]))
    pre.any_platform = True
    check("update: a forbidden env name stops before any change (exit 10, no writes)",
          (update_dispatcher(pre, _conf(), sudo=_Sudo()), pre.runner.writes), (EX_BLOCKED, []))
    store = up_env(_Launchd([base[0], ("/bin/test -e", 0, "")] + base[2:]))
    store.any_platform = True
    check("update: a token store beside the env file stops before any change",
          (update_dispatcher(store, _conf(), sudo=_Sudo()), store.runner.writes), (EX_BLOCKED, []))
    no = up_env(_Launchd(base), answer="no")
    no.any_platform = True
    check("update: anything but yes changes nothing (exit 10)",
          (update_dispatcher(no, _conf(), sudo=_Sudo()), no.runner.writes), (EX_BLOCKED, []))
    have = up_env(_Launchd([base[0], base[1], ("grep -qE", 0, "")] + base[3:]))
    have.any_platform = True
    update_dispatcher(have, _conf(DISPATCHER_SENTRY="off"), sudo=_Sudo())
    check("update: Sentry already off is not appended twice",
          "turn the dispatcher's error reporting off" in [w["why"] for w in have.runner.writes], False)

    bad = up_env(_Launchd(base, install_rc=1))
    bad.any_platform = True
    try:
        update_dispatcher(bad, _conf(), sudo=_Sudo())
        failed = False
    except UpdateError:
        failed = True
    whys = [w["why"] for w in bad.runner.writes]
    check("update: a failed install raises and never starts the dispatcher",
          (failed, "start the dispatcher on the new version" in whys), (True, False))
    check("update: a failed install's banner names the rollback copy",
          any("dispatcher-rollback" in q or "cyrus-ai-rollback-0.2.69" in q for q in quiet), True)

    stuck = up_env(_Launchd(base, stuck=True))
    stuck.any_platform = True
    try:
        update_dispatcher(stuck, _conf(), sudo=_Sudo())
        failed = False
    except UpdateError:
        failed = True
    check("update: a dispatcher launchd never releases is never installed over",
          (failed, any(w["why"].startswith("install") for w in stuck.runner.writes)), (True, False))

    eio = up_env(_Launchd(base, bootstrap=(5, 0), resurrect=False))
    eio.any_platform = True
    check("update: one EIO from bootstrap is retried and the update completes",
          update_dispatcher(eio, _conf(), sudo=_Sudo()), EX_OK)

    class Liar(_Launchd):
        def _exec(self, argv, stdin, timeout, cwd=None):
            res = _Launchd._exec(self, argv, stdin, timeout, cwd)
            if "npm install -g" in se._fmt(argv):
                self.version = "0.2.70"
            return res
    wrong = up_env(Liar(base))
    wrong.any_platform = True
    try:
        update_dispatcher(wrong, _conf(), sudo=_Sudo())
        failed = False
    except UpdateError as exc:
        failed = "0.2.70" in str(exc)
    check("update: a dispatcher answering the wrong version after start is a failure", failed, True)

    dry_runner = _Launchd(base)
    dry_runner.dry_run = True
    dry = up_env(dry_runner)
    dry.any_platform = True
    check("update --dry-run applies nothing",
          (update_dispatcher(dry, _conf(DISPATCHER_SENTRY="off"), dry_run=True, sudo=_Sudo()),
           dry.runner.applied), (EX_OK, []))

    ledger = os.path.join(home, ".pipeline-update", "ledger.jsonl")
    try:
        with open(ledger) as fh:
            results = [json.loads(l)["result"] for l in fh]
    except OSError:
        results = []
    check("update: every finished or failed update is in the ledger",
          ("done" in results and "install-failed" in results), True)

    print("\n%d/%d checks passed" % (ran[0] - len(fails), ran[0]))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
