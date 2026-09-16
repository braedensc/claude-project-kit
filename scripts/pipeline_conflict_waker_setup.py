#!/usr/bin/env python3
"""Conflict waker installer — the local half of the conflict loop, as a supervised job.

    python3 scripts/pipeline_conflict_waker_setup.py run

Same contract as the Stage E installer (scripts/pipeline_stage_e_setup.py): one command,
run repeatedly, idempotent, stopping at a numbered CHECKPOINT CARD when a person must do
something, and carrying on when that person runs the same command again. The Stage E
installer runs these same steps as its own `conflict-waker` step, so a fresh Stage E
install brings the waker up with the others; this file is also usable on its own, on a
machine with no dispatcher at all.

MACOS ONLY — AND WHAT IS NOT.  Everything this file installs is launchd's, so on any other
platform its preflight names the platform and stops before it runs anything. The waker
itself is portable: `scripts/pr_conflict.py wake` is one pass of plain Python that shells
`gh` and `claude` and exits, so a systemd timer or cron runs it on Linux. What this file
would otherwise do for you is listed in docs/COLLABORATION.md, parallel-session item 8.

WHAT IT BUILDS.  For the PERSON running it, never for anyone else:

    ~/.pr-conflict-waker/code         a clone, level with origin, that the job execs
    ~/.pr-conflict-waker/state        the waker's heartbeat and per-PR locks
    ~/.pr-conflict-waker/setup        this installer's ledger and sign-offs
    ~/Library/LaunchAgents/<LABEL>.plist
                                      one LaunchAgent: `pr_conflict.py wake` every
                                      INTERVAL_SECONDS, one pass, then exit

WHY A LAUNCHAGENT FOR YOU, AND NOT A DAEMON FOR THE ROLE ACCOUNT.  The three Stage E jobs
run as the dispatcher's role account under system LaunchDaemons, because what they serve
— the dispatcher — runs at boot with nobody logged in. The waker serves the opposite
thing. It wakes LOCALLY SPAWNED sessions: yours, in your worktrees, under your hooks, with
your `claude` and `gh` logins, which on macOS live in your login keychain — and a system
daemon cannot open that keychain. So it is a user LaunchAgent, and it runs only while you
are logged in. That is the honest scope: when you are logged out, your local sessions are
not running either, and the conflict monitor's ack deadline turns an unclaimed request
into a page on the PR — never silence.

A waker as the ROLE ACCOUNT is refused, here and in the waker itself. Every PR that
account's sessions opened is a SANDBOXED session's, and a waker starts its fix outside
any sandbox. Those conflicts go back through the session's own tracker thread instead —
the Stage E bounce driver's `conflict` action. Both cases exist on a real machine; each
has exactly one lane, and neither lane can take the other's PR (see scripts/pr_conflict.py,
WHOSE PULL REQUEST THE WAKER MAY TAKE).

SUBCOMMANDS

    run                    do everything possible; stop at the first card
    run --dry-run          the same pass with apply OFF: measures, changes nothing
    status                 the recorded steps, sign-offs, and the waker's last heartbeat
    verify                 read-only drift check against the live machine
    card <CK-id>           print a checkpoint card
    attest <A-id> --initials YOUR-INITIALS --note "..."
    --selftest             offline battery; every transport stubbed

EXIT CODES — contract §13, the Stage E installer's own numbers.

    0   DONE / ALREADY-DONE
    1   FAILED — a step ran and failed; the first failure is named
    2   USAGE or CONFIG — nothing was attempted
    3   REFUSED — an agent environment asked for a mutating action
    4   UNKNOWN — a step could not measure itself (a loaded job with a stale heartbeat is
        this: NOT RUNNING is a different fact from nothing-to-do, and it blocks)
    10  BLOCKED-ON-HUMAN — a card is printed; do it and re-run

WHAT IT REFUSES.  `run` (not --dry-run) and `attest` under an agent environment — a
session that installs a job which starts sessions is the attack; the markers are the
local dispatcher's, imported, and like every use of them tamper-evident, not tamper-proof.
Running as root, or as a dispatcher's role account. And there is no code path here to
merge, approve, label, or ask for `sudo`: nothing it installs is outside your own home.

WHAT BOUNDS THE JOB IT INSTALLS.  Per session a spend cap and a timeout; per pass a
session cap whose product with the timeout must fit the monitor's result deadline (the
conf is refused otherwise); per PR the monitor's three requests, lifetime; and no loop —
launchd starts each pass. The daily worst case is therefore three sessions per
conflicted local PR, each capped, never an unbounded retry.
"""
import argparse
import json
import os
import plistlib
import re
import shlex
import sys
import tempfile
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# One exit-code table, one runner, one ledger shape, one placeholder list: the Stage E
# installer's. A second copy of any of them is a copy that drifts.
import pipeline_stage_e_setup as se  # noqa: E402
import pr_conflict as prc  # noqa: E402
from pipeline_dispatch_local import AGENT_ENV_MARKERS  # noqa: E402

SCHEMA = "conflict-waker-setup/1"
EX_OK, EX_FAILED, EX_USAGE, EX_REFUSED, EX_UNKNOWN, EX_BLOCKED = (
    se.EX_OK, se.EX_FAILED, se.EX_USAGE, se.EX_REFUSED, se.EX_UNKNOWN, se.EX_BLOCKED)
DONE, ALREADY_DONE, BLOCKED, FAILED, UNKNOWN, WOULD_CHANGE = (
    se.DONE, se.ALREADY_DONE, se.BLOCKED, se.FAILED, se.UNKNOWN, se.WOULD_CHANGE)
say = se.say

HOME_DIR = prc.DEFAULT_WAKER_HOME                     # ~/.pr-conflict-waker
DEFAULT_STATE_HOME = HOME_DIR + "/setup"
# What the job EXECS: pr_conflict.py and every scripts/ module it imports at load,
# transitively. The clone is `behind` only when one of these differs from origin — a merge
# that touches none of them leaves the job's code current. The selftest derives the closure
# from the sources and fails when this tuple drifts from it.
REQUIRED_SCRIPTS = ("pr_conflict.py", "pipeline_dispatch_local.py", "pipeline_labels.py", "jsonschema_mini.py")
DAEMON_PATH = se.DAEMON_PATH
HEARTBEAT_WAIT_SECONDS = 90

CONF_REQUIRED = ("REPO_DIRS",)
CONF_DEFAULTS = {
    "LABEL": "local.pr-conflict-waker",
    "CODE_REPO_URL": "",
    "INTERVAL_SECONDS": "300",
    "MAX_BUDGET_USD": str(int(prc.DEFAULT_BUDGET_USD)),
    "TIMEOUT_MIN": str(prc.DEFAULT_TIMEOUT_MIN),
    "MAX_SESSIONS_PER_PASS": str(prc.DEFAULT_MAX_SESSIONS),
    "RESUME_MODE": "from-pr",
    "CLAUDE_ARGS": "",
}
CONF_KEYS = set(CONF_REQUIRED) | set(CONF_DEFAULTS)
CONF_UNEDITED = {"REPO_DIRS": "/Users/you/src/your-project"}
RESUME_MODES = ("from-pr", "continue", "fresh")


class Refusal(Exception):
    """Exit 3. Nothing was written."""


class SetupError(Exception):
    """A read or a write failed for a reason worth naming."""


class Unsupported(SetupError):
    """This platform has no launchd. Nothing after preflight is measured."""


def platform_problem(platform):
    """None on macOS; otherwise what this installer cannot do here, and what runs instead."""
    if platform == "darwin":
        return None
    return ("this installer builds a macOS LaunchAgent, and this machine's platform is %r — it has no "
            "launchd, so nothing here can be installed on it. The waker itself is portable: run "
            "`python3 scripts/pr_conflict.py wake` (its flags: `wake --help`) from a systemd user timer "
            "or cron, every few minutes. docs/COLLABORATION.md, parallel-session item 8, lists what this "
            "installer would have done for you" % platform)


class Blocked(Exception):
    def __init__(self, card_id, extra=""):
        Exception.__init__(self, card_id)
        self.card_id, self.extra = card_id, extra


class Unknown(Exception):
    def __init__(self, what, remedy=""):
        Exception.__init__(self, what)
        self.what, self.remedy = what, remedy


def _self_path():
    try:
        return os.path.relpath(os.path.abspath(__file__), os.getcwd())
    except ValueError:
        return os.path.abspath(__file__)


def refuse_if_agent(action, env=None):
    env = os.environ if env is None else env
    found = [m for m in AGENT_ENV_MARKERS if m in env]
    if found:
        raise Refusal(
            "REFUSED: %s is a mutating action and this is an agent environment (%s set).\n"
            "  This installs a job that STARTS paid sessions; a session installing it is the thing\n"
            "  the refusal exists to prevent. A PERSON runs it, in a terminal:\n"
            "      python3 %s %s\n"
            "  Read-only meanwhile:  verify | status | card <CK-id> | run --dry-run"
            % (action, ", ".join(found), _self_path(), action))


# --------------------------------------------------------------------------- #
# The conf — parsed, never sourced, every error at once.
# --------------------------------------------------------------------------- #
def parse_conf(text, source="conflict-waker.conf"):
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
            errors.append("%s:%d: unknown key %s (see conflict-waker.conf.example)" % (source, n, key))
            continue
        # A long run of path characters is a path, not a blob: the blob test skips any token
        # carrying a `/`, or a checkout at a long dotless path would be refused as a secret.
        if any(p in val for p in se._CRED_PREFIXES) or any(
                se._BLOB_RE.match(t) for t in re.split(r"[\s,]+", val) if "/" not in t):
            errors.append("%s:%d: %s carries a CREDENTIAL SHAPE; no key here ever holds one (the value "
                          "is not shown)" % (source, n, key))
            continue
        seen[key] = n
        values[key] = val
    return values, errors


def validate_conf(values):
    conf, errors = dict(CONF_DEFAULTS), []
    conf.update(values)
    for key in CONF_REQUIRED:
        if not conf.get(key):
            errors.append("%s is required and is missing or empty" % key)
    for key, placeholder in CONF_UNEDITED.items():
        if conf.get(key) == placeholder:
            errors.append("%s is still the example value %r — this conf has not been edited" % (key, placeholder))
    dirs = split_list(conf.get("REPO_DIRS"))
    for d in dirs:
        if not d.startswith("/"):
            errors.append("REPO_DIRS entry %r must be an absolute path — launchd expands nothing" % d)
    if len(set(dirs)) != len(dirs):
        errors.append("REPO_DIRS names the same checkout twice")
    if not se._RDNS_RE.match(conf.get("LABEL", "")):
        errors.append("LABEL %r is not a reverse-DNS launchd label" % conf.get("LABEL"))
    ints = {}
    for key in ("INTERVAL_SECONDS", "TIMEOUT_MIN", "MAX_SESSIONS_PER_PASS", "MAX_BUDGET_USD"):
        val = conf.get(key, "")
        if not val.isdigit() or int(val) <= 0:
            errors.append("%s must be a positive whole number (got %r)" % (key, val))
        else:
            ints[key] = int(val)
    if len(ints) == 4:
        problem = prc.queue_problem(ints["MAX_SESSIONS_PER_PASS"], ints["TIMEOUT_MIN"], ints["MAX_BUDGET_USD"])
        if problem:
            errors.append("MAX_SESSIONS_PER_PASS x TIMEOUT_MIN is unboundable: " + problem.replace(
                "--max-sessions", "MAX_SESSIONS_PER_PASS").replace("--timeout-min", "TIMEOUT_MIN"))
        if ints["INTERVAL_SECONDS"] < 60:
            errors.append("INTERVAL_SECONDS below 60 would hammer the code host for nothing")
    if conf.get("RESUME_MODE") not in RESUME_MODES:
        errors.append("RESUME_MODE must be one of %s (got %r)" % ("|".join(RESUME_MODES), conf.get("RESUME_MODE")))
    try:
        shlex.split(conf.get("CLAUDE_ARGS", ""))
    except ValueError as exc:
        errors.append("CLAUDE_ARGS does not split into words: %s" % exc)
    return conf, errors


def split_list(value):
    return [p.strip() for p in (value or "").split(",") if p.strip()]


def load_conf(path):
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        raise SetupError("could not read %s: %s\n  cp conflict-waker.conf.example conflict-waker.conf "
                         "&& $EDITOR conflict-waker.conf" % (path, exc))
    values, errors = parse_conf(text, os.path.basename(path))
    conf, more = validate_conf(values)
    conf["__source__"] = path
    return conf, errors + more


# --------------------------------------------------------------------------- #
# Cards and sign-offs
# --------------------------------------------------------------------------- #
CARDS = {
    "CK-W1": {
        "title": "Read the conflict waker's dry run before it is turned on",
        "why": ("Once loaded, the waker's first pass starts a PAID Claude Code session for every "
                "unclaimed conflict fix request on a PR whose worktree one of your local sessions "
                "worked in — up to MAX_SESSIONS_PER_PASS per pass, each capped at MAX_BUDGET_USD. "
                "Nothing can decide for you whether that number is what you meant, or whether the "
                "fix session will be allowed the tools it needs: a headless session gets only the "
                "tools your settings and CLAUDE_ARGS allow. One that cannot run git ends `failed` "
                "and pages you; one that cannot run your local checks pushes a merge nobody tested, "
                "or ends `failed`."),
        "do": ["Read the dry run the installer just printed: `would wake [...]` is the count.",
               "Check CLAUDE_ARGS gives a fix session what its prompt asks for: git and gh, AND",
               "every runner your local gate uses (CLAUDE.md names it; conflict-waker.conf.example",
               "has a worked line). Change it and re-run if not. Then sign the count off and",
               "re-run — the installer loads the job:",
               "    python3 scripts/pipeline_conflict_waker_setup.py attest A-WAKE-DRY-RUN "
               "--initials " + se.INITIALS_PLACEHOLDER + " --note \"count read: N\"",
               "(YOUR initials, 2-4 letters, and N the count. Both the placeholder and an empty",
               "note are refused: this is the cost gate.)"],
        "good": "the count you signed is the count you meant",
        "attest": "A-WAKE-DRY-RUN",
    },
}
ATTESTATIONS = {"A-WAKE-DRY-RUN": "the conflict waker's dry-run count is the number you meant (CK-W1)"}
NEVER = ["Never merge a pull request, approve one, or apply a protected label.",
         "Never install the waker as a dispatcher's role account, or as root.",
         "Never reword a command a guard blocked. Say that it blocked you, and stop."]


def print_card(cid):
    card = CARDS.get(cid)
    if not card:
        raise SetupError("no such checkpoint card: %s (have %s)" % (cid, ", ".join(sorted(CARDS))))
    say("")
    say("=" * 74)
    say(" %s — %s" % (cid, card["title"]))
    say("=" * 74)
    say("")
    say("WHY THIS IS YOURS")
    for line in se._wrap(card["why"]):
        say("  " + line)
    say("")
    say("WHAT TO DO")
    for line in card["do"]:
        say("  " + line)
    say("")
    say("GOOD: %s" % card["good"])
    say("SIGN-OFF: %s — %s" % (card["attest"], ATTESTATIONS[card["attest"]]))
    say("")
    say("NEVER")
    for line in NEVER:
        say("  " + line)
    say("")


class State(se.State):
    def __init__(self, root):
        se.State.__init__(self, root)
        self.data["schema"] = SCHEMA


# --------------------------------------------------------------------------- #
# Context
# --------------------------------------------------------------------------- #
class Ctx(object):
    """Everything a step reads. `runner` is the Stage E Runner (or its fake): read() always
    runs, write() is the only thing a dry run suppresses."""

    def __init__(self, conf, runner, state, home=None, uid=None, env=None):
        self.conf, self.runner, self.state = conf, runner, state
        self.home = home or os.path.expanduser("~")
        self.uid = os.getuid() if uid is None else uid
        self.env = os.environ if env is None else env
        self.platform = sys.platform
        self.claude_bin = None
        self.gh_bin = None
        self.python_bin = None
        self.code_state = None
        self.origin_url = None
        self.plist_changed = False
        self.sleep = time.sleep
        self.clock = time.time

    def path(self, *parts):
        return os.path.join(self.home, HOME_DIR[2:], *parts)

    @property
    def code_dir(self):
        return self.path("code")

    @property
    def state_dir(self):
        return self.path("state")

    @property
    def log_path(self):
        return self.path("waker.log")

    @property
    def plist_path(self):
        return os.path.join(self.home, "Library", "LaunchAgents", self.conf["LABEL"] + ".plist")

    @property
    def domain(self):
        return "gui/%d" % self.uid

    @property
    def claude_dir(self):
        """Where the job will look for your sessions' transcripts: the CLAUDE_CONFIG_DIR this
        shell has NOW (the plist keeps it), else ~/.claude."""
        return self.env.get("CLAUDE_CONFIG_DIR") or os.path.join(self.home, ".claude")

    @property
    def repo_dirs(self):
        return split_list(self.conf["REPO_DIRS"])

    @property
    def code_url(self):
        return self.conf.get("CODE_REPO_URL") or self.origin_url or ""


def _first_line(res):
    lines = (res.out or "").strip().splitlines()
    return lines[0].strip() if res.ok and lines else ""


# --------------------------------------------------------------------------- #
# Steps. Each returns (ok, detail) or raises Blocked / Unknown / SetupError / Refusal.
# --------------------------------------------------------------------------- #
def step_preflight(ctx, apply_it):
    unsupported = platform_problem(ctx.platform)
    if unsupported:
        raise Unsupported(unsupported)
    r, problems = ctx.runner, []
    if ctx.uid == 0:
        problems.append("this is root. The waker runs as the person whose local sessions it wakes; "
                        "run the installer as that person, without sudo")
    role_env = os.path.join(ctx.home, prc.DISPATCHER_ENV_FILE[2:])
    if os.path.exists(role_env):
        problems.append("%s exists, so this is a dispatcher's role account. Its sessions are "
                        "sandboxed and the waker is not; their conflicts go back through the bounce "
                        "driver. Install the waker for the person who runs local sessions" % role_env)
    gui = r.read(["launchctl", "print", ctx.domain])
    if not gui.ok:
        problems.append("launchd has no %s domain — there is no GUI login for this user, so a "
                        "LaunchAgent cannot run. Log in at the console (not only over SSH) and re-run"
                        % ctx.domain)
    # The interpreter the job runs is the one YOUR shell resolves, proved to start — not a
    # fixed /usr/bin/python3, which on macOS may be a stub that refuses to run until a
    # license is accepted, and would fail every pass with nothing but a log line.
    # It asks the interpreter for its own path, so a version-manager shim (which needs that
    # manager's environment, absent under launchd) resolves to the binary behind it.
    python = _first_line(r.read(["/bin/sh", "-c", "command -v python3"]))
    real = _first_line(r.read([python, "-c", "import sys; assert sys.version_info >= (3, 8); "
                                             "print(sys.executable)"])) if python.startswith("/") else ""
    if not python.startswith("/"):
        problems.append("`python3` is not on your PATH")
    elif not real.startswith("/"):
        problems.append("`%s` does not run a Python 3.8+ program — the job would fail every pass "
                        "(on macOS, /usr/bin/python3 is a stub until the developer tools' license "
                        "is accepted)" % python)
    else:
        ctx.python_bin = real
    claude = _first_line(r.read(["/bin/sh", "-c", "command -v claude"]))
    if not claude.startswith("/"):
        problems.append("`claude` is not on your PATH, so there is nothing for the waker to start")
    elif not r.read([claude, "--version"]).ok:
        problems.append("`%s --version` failed — the waker would start a binary that does not run" % claude)
    else:
        ctx.claude_bin = claude
    # The waker proves a worktree is YOUR local session's by finding its transcripts here. A
    # config directory set only in some other shell reads as "no local session" on every
    # request, and every conflict pages you instead of being fixed.
    if not r.read(["test", "-d", os.path.join(ctx.claude_dir, "projects")]).ok:
        problems.append("%s has no projects/ directory, where Claude Code keeps session transcripts. The "
                        "waker takes a request only for a worktree whose transcripts it finds there, so "
                        "every conflict would page you. If your Claude Code uses another config directory, "
                        "export CLAUDE_CONFIG_DIR in this shell and re-run: the job keeps the value this "
                        "shell has now" % ctx.claude_dir)
    gh = _first_line(r.read(["/bin/sh", "-c", "command -v gh"]))
    if not gh.startswith("/"):
        problems.append("`gh` is not on your PATH; the waker reads and comments on PRs through it")
    else:
        ctx.gh_bin = gh
        if not r.read([gh, "auth", "status"]).ok:
            problems.append("`gh auth status` failed — log in with `gh auth login`")
    for d in ctx.repo_dirs:
        top = _first_line(r.read(["git", "-C", d, "rev-parse", "--show-toplevel"]))
        if not top:
            problems.append("REPO_DIRS entry %s is not a git checkout" % d)
            continue
        if os.path.realpath(top) != os.path.realpath(d):
            problems.append("REPO_DIRS entry %s is inside the checkout %s — name the checkout itself, "
                            "whose `git worktree list` holds your sessions' worktrees" % (d, top))
            continue
        origin = _first_line(r.read(["git", "-C", d, "remote", "get-url", "origin"]))
        if not se._repo_slug(origin):
            problems.append("REPO_DIRS entry %s has no GitHub-shaped origin remote" % d)
        elif ctx.origin_url is None:
            ctx.origin_url = origin
    if not ctx.code_url:
        problems.append("no CODE_REPO_URL, and no REPO_DIRS origin to default it to")
    if problems:
        raise SetupError("preflight found %d problem(s) — all of them, in one pass:\n%s"
                         % (len(problems), "\n".join("  - " + p for p in problems)))
    return True, "python3 %s, claude %s, gh %s, %d checkout(s), code from %s, transcripts from %s%s" % (
        ctx.python_bin, ctx.claude_bin, ctx.gh_bin, len(ctx.repo_dirs), ctx.code_url, ctx.claude_dir,
        " (CLAUDE_CONFIG_DIR as this shell has it — change it later and re-run `run`)"
        if ctx.env.get("CLAUDE_CONFIG_DIR") else "")


def _clone_status(ctx):
    """(state, detail): 'absent' | 'level' | 'behind' | 'missing' | 'dirty' | 'foreign' | 'unknown'.

    LEVEL MEANS THE JOB'S CODE IS CURRENT, not that the clone's HEAD is. The job execs
    REQUIRED_SCRIPTS and nothing else, so a merge that touches none of them leaves the clone
    level. Comparing whole HEADs read `behind` after every merge to origin, whatever it
    touched — noise that teaches a person to stop reading `verify`.

    The comparison fetches origin's HEAD into the clone's FETCH_HEAD. That moves no branch and
    no file the job execs; it is what lets `verify` see origin's tree at all."""
    r, code = ctx.runner, ctx.code_dir
    head = _first_line(r.read(["git", "-C", code, "rev-parse", "HEAD"]))
    if not head:
        return "absent", "no clone at %s" % code
    origin = _first_line(r.read(["git", "-C", code, "remote", "get-url", "origin"]))
    if origin != ctx.code_url:
        return "foreign", "the clone at %s is of %r, not %r" % (code, origin, ctx.code_url)
    status = r.read(["git", "-C", code, "status", "--porcelain"])
    if not status.ok:
        return "unknown", "`git status` failed in %s" % code
    if status.out.strip():
        return "dirty", "the clone at %s has local changes" % code
    if not r.read(["git", "-C", code, "fetch", "--quiet", "--no-tags", "origin", "HEAD"], timeout=120).ok:
        return "unknown", "could not fetch origin HEAD (no network, or no such remote)"
    remote = _first_line(r.read(["git", "-C", code, "rev-parse", "FETCH_HEAD"]))
    if not remote:
        return "unknown", "origin HEAD was fetched but cannot be named"
    paths = ["scripts/" + name for name in REQUIRED_SCRIPTS]
    missing = [p for p in paths if not r.read(["git", "-C", code, "cat-file", "-e", "FETCH_HEAD:" + p]).ok]
    if missing:
        return "missing", "origin HEAD carries no %s — the change that ships it is not merged" % ", ".join(missing)
    if head == remote:
        return "level", "clone at %s is level with origin HEAD (%s)" % (code, head[:12])
    changed = r.read(["git", "-C", code, "diff", "--name-only", head, remote, "--"] + paths)
    if not changed.ok:
        return "unknown", "could not compare the job's scripts between %s and origin HEAD %s" % (head[:12], remote[:12])
    if changed.out.strip():
        return "behind", "origin HEAD (%s) changed %s since the clone's %s" % (
            remote[:12], ", ".join(changed.out.split()), head[:12])
    return "level", ("the scripts the job runs are identical at origin HEAD (%s); the clone's other files are "
                     "older (%s), and the job never reads them" % (remote[:12], head[:12]))


def step_code(ctx, apply_it):
    """The clone the job execs — never a checkout your sessions work in. Your sessions run
    as you, so this is protected from them by the hooks and by `verify`'s drift check, not by
    file permissions: a local change here is FAILED, never discarded for you."""
    state, detail = _clone_status(ctx)
    ctx.code_state = state
    if state == "level":
        return True, detail
    if state == "unknown":
        raise Unknown("could not tell whether the waker's code is current: %s" % detail,
                      "prove this account can reach origin: git -C %s ls-remote origin HEAD" % ctx.code_dir)
    if state in ("dirty", "foreign"):
        raise SetupError("%s. The job would exec code nobody merged. Look before discarding anything:\n"
                         "    git -C %s status" % (detail, ctx.code_dir))
    if state == "missing":
        raise SetupError(detail)
    if not apply_it:
        return False, "would %s: %s" % ("clone" if state == "absent" else "fast-forward", detail)
    r = ctx.runner
    r.write("unload the waker before its code moves", ["launchctl", "bootout",
                                                        "%s/%s" % (ctx.domain, ctx.conf["LABEL"])])
    if state == "absent":
        r.write("create the waker's home, mode 700", ["mkdir", "-p", "-m", "700", ctx.path()])
        res = r.write("clone the waker's code", ["git", "clone", "--quiet", ctx.code_url, ctx.code_dir])
    else:
        res = r.write("fast-forward the waker's code", ["git", "-C", ctx.code_dir, "pull", "--ff-only", "--quiet"])
    if res.skipped:
        return False, "would place the clone"
    if not res.ok:
        raise SetupError("could not place the waker's code: %s" % (res.err or res.out).strip()[:300])
    state, detail = _clone_status(ctx)
    ctx.code_state = state
    if state != "level":
        raise SetupError("after placing it, the clone still reads %s: %s" % (state, detail))
    return False, "placed: " + detail


def render_plist(ctx):
    """Built with plistlib, never by string templating: a checkout path carrying `&` or `<`
    would otherwise produce a plist that parses as something else."""
    conf = ctx.conf
    argv = [ctx.python_bin or "python3", os.path.join(ctx.code_dir, "scripts", "pr_conflict.py"), "wake",
            "--state-dir", ctx.state_dir,
            "--max-budget-usd", conf["MAX_BUDGET_USD"], "--timeout-min", conf["TIMEOUT_MIN"],
            "--max-sessions", conf["MAX_SESSIONS_PER_PASS"], "--resume-mode", conf["RESUME_MODE"],
            "--claude-bin", ctx.claude_bin or "claude"]
    for d in ctx.repo_dirs:
        argv += ["--repo-dir", d]
    argv += ["--claude-arg=" + a for a in shlex.split(conf.get("CLAUDE_ARGS", ""))]
    path = []
    for p in [os.path.dirname(ctx.claude_bin or ""), os.path.dirname(ctx.gh_bin or "")] + DAEMON_PATH.split(":"):
        if p and p not in path:
            path.append(p)
    env = {"HOME": ctx.home, "PATH": ":".join(path)}
    if ctx.env.get("CLAUDE_CONFIG_DIR"):
        env["CLAUDE_CONFIG_DIR"] = ctx.env["CLAUDE_CONFIG_DIR"]
    doc = {"Label": conf["LABEL"], "ProgramArguments": argv, "EnvironmentVariables": env,
           "StartInterval": int(conf["INTERVAL_SECONDS"]), "RunAtLoad": True,
           "ProcessType": "Background", "WorkingDirectory": ctx.home,
           "StandardOutPath": ctx.log_path, "StandardErrorPath": ctx.log_path}
    return plistlib.dumps(doc, sort_keys=True).decode("utf-8")


def step_agent(ctx, apply_it):
    """Render and install the LaunchAgent plist — but do not load it. Loading is the moment
    paid sessions can start, and that is CK-W1's to authorise."""
    want = render_plist(ctx)
    got = ctx.runner.read(["cat", ctx.plist_path])
    if got.ok and got.out == want:
        return True, "%s is installed and current" % ctx.plist_path
    if not apply_it:
        return False, "would install %s" % ctx.plist_path
    plistlib.loads(want.encode("utf-8"))     # a plist this file cannot read back is never written
    fd, tmp = tempfile.mkstemp(prefix="conflict-waker-", suffix=".plist")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(want)
        ctx.runner.write("create ~/Library/LaunchAgents", ["mkdir", "-p", os.path.dirname(ctx.plist_path)])
        res = ctx.runner.write("install %s (mode 644)" % ctx.plist_path,
                               ["install", "-m", "644", tmp, ctx.plist_path])
        if not res.ok and not res.skipped:
            raise SetupError("could not install %s: %s" % (ctx.plist_path, (res.err or "").strip()[:200]))
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    ctx.plist_changed = True
    return False, "installed %s (not loaded yet — CK-W1)" % ctx.plist_path


def dry_run_argv(ctx):
    doc = plistlib.loads(render_plist(ctx).encode("utf-8"))
    return doc["ProgramArguments"] + ["--dry-run"]


_WOULD_RE = re.compile(r"would wake (\[[0-9, ]*\]|none)")


def step_dry_run(ctx, apply_it):
    """The waker's own dry run, exactly the arguments the job will pass, plus --dry-run. It
    writes nothing, starts nothing and leaves no heartbeat. This is what CK-W1 reads."""
    if ctx.code_state != "level":
        if not apply_it:
            return False, "waits on the `code` step: there is no current clone to run a dry run from"
        raise SetupError("the waker's code is not in place (%s), so its dry run cannot run" % ctx.code_state)
    res = ctx.runner.read(dry_run_argv(ctx), timeout=600)
    text = (res.out or "") + (res.err or "")
    say("")
    say("  -- the conflict waker's dry run --")
    for line in text.strip().splitlines()[-14:]:
        say("    " + line)
    say("")
    if res.rc == 1:
        raise Unknown("the waker's dry run could not tell (exit 1) — a checkout's repository or its PRs "
                      "could not be read", "read its output above; `gh auth status` and each REPO_DIRS origin")
    if res.rc != 0:
        raise SetupError("the waker's dry run failed (exit %d): %s" % (res.rc, text.strip()[-400:]))
    m = _WOULD_RE.search(text)
    if not m:
        raise Unknown("the waker's dry run never printed its `would wake` count, so no count was proved",
                      "read its output above")
    if not ctx.state.attested("A-WAKE-DRY-RUN"):
        raise Blocked("CK-W1", "would wake %s — not signed off" % m.group(1))
    return True, "dry run ran (would wake %s) and the count is signed off" % m.group(1)


def read_heartbeat(ctx):
    """(doc, why_not). Read as you, out of your own home."""
    got = ctx.runner.read(["cat", os.path.join(ctx.state_dir, "heartbeat.json")])
    if not got.ok or not got.out.strip():
        return None, "no heartbeat at %s" % os.path.join(ctx.state_dir, "heartbeat.json")
    try:
        doc = json.loads(got.out)
    except ValueError:
        return None, "the heartbeat is not JSON"
    if not isinstance(doc, dict) or doc.get("schema") != prc.WAKER_HEARTBEAT_SCHEMA:
        return None, "the heartbeat carries an unknown schema"
    return doc, None


def stale_after_seconds(conf):
    """Older than this, a loaded waker is NOT RUNNING: two intervals, plus the longest a pass
    may legitimately run (every capped session to its timeout), plus two minutes."""
    return (2 * int(conf["INTERVAL_SECONDS"]) + int(conf["MAX_SESSIONS_PER_PASS"])
            * int(conf["TIMEOUT_MIN"]) * 60 + 120)


def _age(ctx, doc):
    stamp = doc.get("finished_at") or doc.get("started_at") or doc.get("at") or ""
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ctx.clock() - when.timestamp()


def step_enable(ctx, apply_it):
    """Load the job and see it run. A loaded job whose heartbeat is stale is NOT RUNNING —
    UNKNOWN, never "installed" — and one that never wrote a heartbeat is untested, not working."""
    r, target = ctx.runner, "%s/%s" % (ctx.domain, ctx.conf["LABEL"])
    loaded = r.read(["launchctl", "print", target]).ok
    doc, why_not = read_heartbeat(ctx)
    age = _age(ctx, doc) if doc else None
    fresh = age is not None and age <= stale_after_seconds(ctx.conf)
    if loaded and fresh and not ctx.plist_changed:
        return True, "loaded; last pass %s, %d s ago" % (doc.get("result"), int(age))
    if loaded and doc and not fresh and not ctx.plist_changed:
        raise Unknown("the waker is loaded and NOT RUNNING: its last heartbeat is %s old, past the %d s "
                      "a pass may take" % ("?" if age is None else "%d s" % int(age), stale_after_seconds(ctx.conf)),
                      "read %s; `launchctl print %s` names the last exit" % (ctx.log_path, target))
    if not apply_it:
        return False, "would %s %s and wait for a heartbeat (%s)" % (
            "reload" if loaded else "load", target, why_not or "heartbeat %s" % doc.get("result"))
    say("")
    say("  LOADING the conflict waker. RunAtLoad starts a real pass within seconds.")
    say("")
    loaded_at = ctx.clock()
    if loaded:
        r.write("unload %s before loading the current plist" % target, ["launchctl", "bootout", target])
        for _ in range(15):
            if not r.read(["launchctl", "print", target]).ok:
                break
            ctx.sleep(2)
    res = r.write("load %s" % target, ["launchctl", "bootstrap", ctx.domain, ctx.plist_path])
    if res.skipped:
        return False, "would load %s" % target
    if not res.ok:
        raise SetupError("could not load %s: %s" % (target, (res.err or "").strip()[:200]))
    deadline = loaded_at + HEARTBEAT_WAIT_SECONDS
    while True:
        doc, _why = read_heartbeat(ctx)
        age = _age(ctx, doc) if doc else None
        if doc and age is not None and ctx.clock() - age >= loaded_at - 1:
            return False, "loaded; first pass wrote its heartbeat (%s)" % doc.get("result")
        if ctx.clock() >= deadline:
            break
        ctx.sleep(5)
    raise Unknown("the waker was loaded and wrote no heartbeat within %d s" % HEARTBEAT_WAIT_SECONDS,
                  "read %s — a pass that cannot start writes nothing there" % ctx.log_path)


STEPS = (
    ("preflight", "you, launchd, claude, gh and each checkout", step_preflight),
    ("code", "the clone the job execs, level with origin", step_code),
    ("agent", "the LaunchAgent plist, rendered and installed", step_agent),
    ("dry-run", "the waker's own dry run, read before it is on", step_dry_run),
    ("enable", "load the job and see its heartbeat", step_enable),
)


def measure(ctx, apply_it, keep_going=False):
    """[(step, outcome, detail, card_or_exc)] — every row as data, no printing of cards and
    no exception across the call. The Stage E installer embeds the waker through this, so
    its classes and its cards never have to be this file's."""
    rows = []
    for sid, _title, fn in STEPS:
        try:
            ok, detail = fn(ctx, apply_it)
        except Blocked as exc:
            rows.append((sid, BLOCKED, exc.extra or CARDS[exc.card_id]["title"], exc.card_id))
        except Unknown as exc:
            rows.append((sid, UNKNOWN, exc.what, exc))
        except (SetupError, Refusal) as exc:
            rows.append((sid, FAILED, str(exc), exc))
        else:
            rows.append((sid, ALREADY_DONE if ok else (DONE if apply_it else WOULD_CHANGE), detail, None))
            continue
        if not keep_going or isinstance(rows[-1][3], Unsupported):
            break
    return rows


def record_rows(state, rows):
    """Write measured rows into the ledger `status` replays, and save it. Every caller of
    `measure` must come through here — the Stage E installer included — or `status` reads a
    working install as never run. The ledger lives in the person's own home; recording it
    is not a change to the machine, so a dry run and `verify` record too."""
    for sid, outcome, detail, extra in rows:
        state.record(sid, outcome, detail if outcome != BLOCKED else extra)
    state.save()


def run_steps(ctx, apply_it, keep_going=False, resume="run"):
    rows = measure(ctx, apply_it, keep_going)
    record_rows(ctx.state, rows)
    say("")
    say("-- steps --")
    for sid, outcome, detail, _extra in rows:
        say("  %-10s %-13s %s" % (sid, outcome, str(detail).splitlines()[0][:100]))
    for sid, outcome, detail, extra in rows:
        if outcome == BLOCKED:
            print_card(extra)
            say("Do that, then run the same command again:  python3 %s %s" % (_self_path(), resume))
        elif outcome == UNKNOWN:
            say("")
            say("UNKNOWN at step `%s` — not a pass and not a failure; nothing here can tell." % sid)
            for line in se._wrap(detail):
                say("  " + line)
            if getattr(extra, "remedy", ""):
                say("  What clears it: " + extra.remedy)
        elif outcome == FAILED:
            say("")
            say("FAILED at step `%s`:" % sid)
            for line in str(detail).splitlines():
                say("  " + line)
    worst = next((s for s in (FAILED, UNKNOWN, BLOCKED, WOULD_CHANGE) if any(o == s for _, o, _, _ in rows)),
                 ALREADY_DONE)
    return {FAILED: EX_FAILED, UNKNOWN: EX_UNKNOWN, BLOCKED: EX_BLOCKED, WOULD_CHANGE: EX_BLOCKED}.get(worst, EX_OK), rows


def cmd_run(ctx, dry_run):
    if not dry_run:
        refuse_if_agent("run")
    say("Conflict waker installer — %s" % ("DRY RUN: nothing will be changed" if dry_run
                                          else "installs a LaunchAgent for YOU"))
    say("  conf     %s" % ctx.conf.get("__source__"))
    say("  job      %s/%s" % (ctx.domain, ctx.conf["LABEL"]))
    say("  bounds   %s session(s)/pass x $%s x %s min; 3 requests per PR (the monitor's)"
        % (ctx.conf["MAX_SESSIONS_PER_PASS"], ctx.conf["MAX_BUDGET_USD"], ctx.conf["TIMEOUT_MIN"]))
    code, rows = run_steps(ctx, apply_it=not dry_run, keep_going=dry_run,
                           resume="run --dry-run" if dry_run else "run")
    if dry_run and ctx.runner.writes:
        say("BUG: a dry run recorded %d mutation(s); that is a defect in this file." % len(ctx.runner.writes))
        return EX_FAILED
    if code == EX_OK and not dry_run:
        say("")
        say("DONE — the conflict waker is loaded and has written a heartbeat. Nothing merged, "
            "nothing approved, no label applied.")
    return code


def cmd_verify(ctx):
    say("Conflict waker verify — read-only. Nothing is changed and nothing is asked.")
    ctx.runner.dry_run = True
    code, rows = run_steps(ctx, apply_it=False, keep_going=True, resume="run")
    if ctx.runner.writes:
        say("BUG: verify recorded %d mutation(s); that is a defect in this file." % len(ctx.runner.writes))
        return EX_FAILED
    say("")
    say("No drift." if code == EX_OK else "Outstanding — the one command that moves it forward:  "
        "python3 %s run" % _self_path())
    return code


def cmd_status(ctx):
    st = ctx.state
    say("")
    say(" Conflict waker install status        %s" % se.now_iso())
    say("")
    worst = EX_OK
    for sid, title, _fn in STEPS:
        rec = st.data["steps"].get(sid) or {}
        outcome = rec.get("outcome") or "not run"
        say("  %-10s %-13s %s" % (sid, outcome, str(rec.get("detail") or title)[:90]))
        if outcome not in (DONE, ALREADY_DONE):
            worst = max(worst, {FAILED: EX_FAILED, UNKNOWN: EX_UNKNOWN}.get(outcome, EX_BLOCKED))
    rec = st.data["attestations"].get("A-WAKE-DRY-RUN")
    say("  A-WAKE-DRY-RUN  %s" % ("signed %s by %s  %s" % (rec["at"][:10], rec["initials"], rec.get("note", ""))
                                  if rec else "NOT SIGNED — " + ATTESTATIONS["A-WAKE-DRY-RUN"]))
    path = os.path.join(ctx.state_dir, "heartbeat.json")
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        age = _age(ctx, doc)
        verdict = ("fresh" if age is not None and age <= stale_after_seconds(ctx.conf)
                   else "STALE — the job is NOT RUNNING (or you were logged out)")
        say("  heartbeat       %s, result %s, %s" % (doc.get("finished_at") or doc.get("started_at"),
                                                     doc.get("result"), verdict))
    except (OSError, ValueError) as exc:
        say("  heartbeat       none readable at %s (%s) — never ran here" % (path, exc.__class__.__name__))
    say("")
    say("Re-measure against the live machine:  python3 %s verify" % _self_path())
    return worst


def cmd_attest(state, aid, initials, note):
    refuse_if_agent("attest")
    if aid not in ATTESTATIONS:
        raise SetupError("no such sign-off: %s (have %s)" % (aid, ", ".join(sorted(ATTESTATIONS))))
    initials, note = (initials or "").strip(), (note or "").strip()
    if initials.lower() in se.INITIALS_PLACEHOLDERS or not re.match(r"^[A-Za-z]{2,4}$", initials):
        raise SetupError("--initials wants YOUR OWN 2-4 letters (%r is not a person)" % initials)
    if not note:
        raise SetupError("%s needs a --note saying what you read, e.g. --note \"count read: 1\"" % aid)
    state.attest(aid, initials.lower(), note)
    state.save()
    say("recorded: %s  %s  %s  %s" % (aid, initials.lower(), se.now_iso(), note))
    return EX_OK


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
GOOD_CONF = "REPO_DIRS=/Users/you/src/app,/Users/you/src/kit\n"
HOME = "/Users/you"
CODE_URL = "https://github.com/example-org/app.git"
HEAD = "abc1234000000000000000000000000000000000"


def _fake_ctx(conf_text=GOOD_CONF, answers=(), dry_run=False, attested=True, uid=501):
    values, errors = parse_conf(conf_text)
    conf, more = validate_conf(values)
    assert not errors + more, errors + more
    conf["__source__"] = "conflict-waker.conf"
    state = State(tempfile.mkdtemp(prefix="waker-selftest."))
    if attested:
        state.attest("A-WAKE-DRY-RUN", "ab", "count read: 1")
    runner = se.FakeRunner(list(answers), dry_run=dry_run)
    ctx = Ctx(conf, runner, state, home=HOME, uid=uid, env={})
    ctx.platform = "darwin"
    ctx.sleep = lambda s: None
    return ctx, runner


def _settled_answers(ctx, heartbeat_age=30, loaded=True):
    """A machine on which every step holds."""
    ctx.claude_bin, ctx.gh_bin = "/Users/you/.local/bin/claude", "/opt/homebrew/bin/gh"
    ctx.python_bin = "/opt/homebrew/bin/python3"
    ctx.origin_url = CODE_URL
    now = ctx.clock()
    beat = {"schema": prc.WAKER_HEARTBEAT_SCHEMA, "result": "idle",
            "finished_at": datetime.fromtimestamp(now - heartbeat_age, timezone.utc).isoformat()}
    answers = [
        ("launchctl print gui/501/local.pr-conflict-waker", 0 if loaded else 113, "state = not running\n"),
        ("launchctl print gui/501", 0, "domain\n"),
        ("command -v python3", 0, ctx.python_bin + "\n"),
        ("bin/python3 -c", 0, ctx.python_bin + "\n"),
        ("command -v claude", 0, ctx.claude_bin + "\n"),
        ("claude --version", 0, "2.0.0\n"),
        ("test -d /Users/you/.claude/projects", 0, ""),
        ("command -v gh", 0, ctx.gh_bin + "\n"),
        ("gh auth status", 0, "Logged in\n"),
        ("src/app rev-parse --show-toplevel", 0, "/Users/you/src/app\n"),
        ("src/kit rev-parse --show-toplevel", 0, "/Users/you/src/kit\n"),
        ("src/app remote get-url origin", 0, CODE_URL + "\n"),
        ("src/kit remote get-url origin", 0, "https://github.com/example-org/kit\n"),
        ("code rev-parse HEAD", 0, HEAD + "\n"),
        ("code remote get-url origin", 0, CODE_URL + "\n"),
        ("code status --porcelain", 0, ""),
        ("code fetch --quiet --no-tags origin HEAD", 0, ""),
        ("code rev-parse FETCH_HEAD", 0, HEAD + "\n"),
        ("code cat-file -e FETCH_HEAD:scripts/", 0, ""),
        ("cat /Users/you/Library/LaunchAgents/local.pr-conflict-waker.plist", 0, render_plist(ctx)),
        ("pr_conflict.py wake", 0, "asked: …; pending 1 · would wake [7] · declined none\n"),
        ("heartbeat.json", 0, json.dumps(beat)),
    ]
    return answers


def selftest():
    saved = dict(os.environ)
    for m in AGENT_ENV_MARKERS:
        os.environ.pop(m, None)
    try:
        return _selftest_body()
    finally:
        os.environ.clear()
        os.environ.update(saved)


def _selftest_body():
    import contextlib
    import io
    failures = []

    def expect(name, cond, detail=""):
        if not cond:
            failures.append("%s: %s" % (name, detail))

    def quiet(fn):
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            value = fn()
        return value, buf.getvalue()

    # 1. every conf error at once, including an unboundable queue and a credential shape.
    values, errors = parse_conf("REPO_DIRS=relative/dir\nLABEL=nodots\nMAX_SESSIONS_PER_PASS=3\n"
                                "TIMEOUT_MIN=40\nRESUME_MODE=sometimes\nNOPE=1\n"
                                "CODE_REPO_URL=https://x:" + "ghp_" + "A" * 36 + "@github.com/o/r\n")
    conf, more = validate_conf(values)
    text = "\n".join(errors + more)
    for needle in ("absolute path", "reverse-DNS", "unboundable", "RESUME_MODE", "unknown key NOPE",
                   "CREDENTIAL SHAPE"):
        expect("conf-all-errors", needle in text, "missing %r in:\n%s" % (needle, text))
    expect("conf-unedited", any("not been edited" in e for e in validate_conf(
        parse_conf("REPO_DIRS=/Users/you/src/your-project\n")[0])[1]), "the example value must be refused")
    expect("conf-good", not validate_conf(parse_conf(GOOD_CONF)[0])[1], "the good conf must validate")
    long_path = "REPO_DIRS=/Users/you/" + "a-long-dotless-checkout-directory-name" * 2 + "\n"
    expect("conf-long-path-is-not-a-secret", not parse_conf(long_path)[1],
           "a long dotless checkout path was refused as a credential: %s" % parse_conf(long_path)[1])
    expect("conf-blob-still-refused", parse_conf("CLAUDE_ARGS=--x " + "Q" * 44 + "\n")[1],
           "a bare high-entropy token must still be refused")
    example = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "conflict-waker.conf.example")
    try:
        with open(example, encoding="utf-8") as fh:
            ex_values, ex_errors = parse_conf(fh.read())
        ex_more = validate_conf(ex_values)[1]
        expect("conf-example-complete", not ex_errors and set(ex_values) == CONF_KEYS
               and len(ex_more) == 1 and "not been edited" in ex_more[0],
               "example drift: parse %s, keys %s, validation %s"
               % (ex_errors, sorted(CONF_KEYS ^ set(ex_values)), ex_more))
    except OSError as exc:
        expect("conf-example-complete", False, "could not read %s: %s" % (example, exc))

    # 2. agent refusal: run and attest refuse; the refusal names the read-only commands.
    for action in ("run", "attest"):
        try:
            refuse_if_agent(action, env={"CLAUDECODE": ""})
            expect("agent-refused", False, "%s must refuse under an agent" % action)
        except Refusal as exc:
            expect("agent-refused", "run --dry-run" in str(exc), str(exc))
    expect("person-allowed", refuse_if_agent("run", env={}) is None, "a person's shell must not refuse")

    # 3. a settled machine: verify is clean, writes nothing, and every step measured.
    ctx, fake = _fake_ctx()
    fake.answers = _settled_answers(ctx)
    ctx.claude_bin = ctx.gh_bin = ctx.python_bin = ctx.origin_url = None   # preflight must find them
    code, out = quiet(lambda: cmd_verify(ctx))
    expect("verify-clean", code == EX_OK and not fake.writes, "code=%s writes=%s\n%s" % (code, fake.writes, out))
    expect("verify-all-steps", set(ctx.state.data["steps"]) == {s for s, _t, _f in STEPS}, out)

    # 4. the plist: a user agent (no UserName), code from the clone, every bound on argv,
    #    absolute binaries, and PATH carrying the directories claude and gh were found in.
    doc = plistlib.loads(render_plist(ctx).encode("utf-8"))
    argv = doc["ProgramArguments"]
    expect("plist-user-agent", "UserName" not in doc and doc["RunAtLoad"] is True
           and doc["StartInterval"] == 300, str(doc))
    expect("plist-code", argv[:3] == ["/opt/homebrew/bin/python3",
                                      "/Users/you/.pr-conflict-waker/code/scripts/pr_conflict.py", "wake"],
           "the job must run YOUR resolved python3 on the clone's waker: %s" % argv[:3])
    for flag, val in (("--max-sessions", "2"), ("--timeout-min", "40"), ("--max-budget-usd", "5"),
                      ("--claude-bin", "/Users/you/.local/bin/claude"),
                      ("--state-dir", "/Users/you/.pr-conflict-waker/state")):
        expect("plist-bounds", flag in argv and argv[argv.index(flag) + 1] == val, "%s in %s" % (flag, argv))
    expect("plist-repos", [argv[i + 1] for i, a in enumerate(argv) if a == "--repo-dir"]
           == ["/Users/you/src/app", "/Users/you/src/kit"], str(argv))
    expect("plist-path", doc["EnvironmentVariables"]["PATH"].startswith("/Users/you/.local/bin:/opt/homebrew/bin"),
           doc["EnvironmentVariables"]["PATH"])
    expect("plist-escaped", plistlib.loads(render_plist(_fake_ctx(
        "REPO_DIRS=/Users/you/a&b<c>\n")[0]).encode())["ProgramArguments"][-1] == "/Users/you/a&b<c>",
        "a path with markup characters must survive the plist")
    ctx_args, _f = _fake_ctx(GOOD_CONF + "CLAUDE_ARGS=--permission-mode acceptEdits\n")
    expect("plist-claude-args", "--claude-arg=--permission-mode" in plistlib.loads(
        render_plist(ctx_args).encode())["ProgramArguments"], "CLAUDE_ARGS must reach the waker verbatim")
    parsed = prc.main.__code__ is not None and dry_run_argv(ctx)[-1] == "--dry-run"
    expect("dry-run-argv", parsed and dry_run_argv(ctx)[:-1] == argv, "the dry run must pass the job's own argv")

    # 5. the dry run: unsigned blocks on CK-W1; exit 1 is UNKNOWN; exit 2 is FAILED.
    for rc, attested, want in ((0, False, BLOCKED), (1, True, UNKNOWN), (2, True, FAILED), (0, True, ALREADY_DONE)):
        c, f = _fake_ctx(attested=attested)
        f.answers = [a for a in _settled_answers(c) if a[0] != "pr_conflict.py wake"] + [
            ("pr_conflict.py wake", rc, "pending 1 · would wake [7]\n")]
        rows, _ = quiet(lambda: measure(c, apply_it=False, keep_going=True))
        got = dict((s, o) for s, o, _d, _e in rows)
        expect("dry-run-%s-%s" % (rc, attested), got.get("dry-run") == want, "%s" % rows)

    # 6. enable: stale heartbeat on a loaded job is UNKNOWN (NOT RUNNING); not loaded is
    #    WOULD-CHANGE under verify; an apply bootstraps into the GUI domain and waits for a beat.
    c, f = _fake_ctx()
    f.answers = _settled_answers(c, heartbeat_age=10 ** 6)
    rows, _ = quiet(lambda: measure(c, apply_it=False, keep_going=True))
    expect("enable-stale", [(o, "NOT RUNNING" in d) for s, o, d, _e in rows if s == "enable"] == [(UNKNOWN, True)],
           str(rows))
    c, f = _fake_ctx()
    f.answers = _settled_answers(c, loaded=False)
    rows, _ = quiet(lambda: measure(c, apply_it=False, keep_going=True))
    expect("enable-unloaded", [o for s, o, _d, _e in rows if s == "enable"] == [WOULD_CHANGE], str(rows))
    c, f = _fake_ctx()
    f.answers = [("launchctl bootstrap gui/501", 0, "")] + _settled_answers(c, heartbeat_age=0, loaded=False)
    clock = [c.clock()]
    c.clock = lambda: clock[0]
    rows, _ = quiet(lambda: measure(c, apply_it=True, keep_going=True))
    boot = [w["argv"] for w in f.writes if w["argv"][:2] == ["launchctl", "bootstrap"]]
    expect("enable-apply", boot == [["launchctl", "bootstrap", "gui/501",
                                     "/Users/you/Library/LaunchAgents/local.pr-conflict-waker.plist"]]
           and [o for s, o, _d, _e in rows if s == "enable"] == [DONE], "%s %s" % (boot, rows))

    # 7. run --dry-run on an empty machine: every step measured, nothing written, and the dry-run
    #    step WAITS on the missing clone instead of exec'ing a script that is not there.
    c, f = _fake_ctx(dry_run=True, attested=False)
    f.answers = [a for a in _settled_answers(c, loaded=False)
                 if not a[0].startswith(("code ", "cat ", "heartbeat", "pr_conflict"))]
    code, out = quiet(lambda: cmd_run(c, dry_run=True))
    expect("dry-run-writes-nothing", not f.writes and code != EX_OK, "code=%s writes=%s" % (code, f.writes))
    expect("dry-run-keeps-going", set(c.state.data["steps"]) == {s for s, _t, _f in STEPS}, out)
    expect("dry-run-waits-on-code", c.state.outcome("dry-run") == WOULD_CHANGE
           and not any("pr_conflict.py" in " ".join(a) for a in f.reads), out)

    # 8. preflight: root and a dispatcher's role account are refused, every problem at once.
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, ".stage-e"))
        open(os.path.join(tmp, ".stage-e", "env"), "w").close()
        c, f = _fake_ctx(uid=0)
        c.home = tmp
        f.answers = []
        rows, _ = quiet(lambda: measure(c, apply_it=False))
        detail = rows[0][2] if rows else ""
        expect("preflight-refuses", rows and rows[0][1] == FAILED and "root" in detail
               and "role account" in detail and "no gui/0 domain" in detail and "not on your PATH" in detail,
               detail)

    # 9. a dirty or foreign clone is FAILED, never discarded.
    for needle, out_, word in (("code status --porcelain", " M scripts/pr_conflict.py\n", "local changes"),
                               ("code remote get-url origin", "https://github.com/someone/else\n", "not")):
        c, f = _fake_ctx()
        f.answers = [(needle, 0, out_)] + _settled_answers(c)
        rows, _ = quiet(lambda: measure(c, apply_it=True, keep_going=True))
        code_row = [r for r in rows if r[0] == "code"]
        expect("clone-%s" % needle.split()[1], code_row and code_row[0][1] == FAILED and word in code_row[0][2]
               and not any("pull" in w["argv"] or "clone" in w["argv"] for w in f.writes), str(rows))

    # 10. sign-offs: the placeholder and an empty note are refused.
    st = State(tempfile.mkdtemp(prefix="waker-selftest."))
    for initials, note in ((se.INITIALS_PLACEHOLDER, "count read: 1"), ("xx", "count read: 1"), ("ab", "")):
        try:
            quiet(lambda: cmd_attest(st, "A-WAKE-DRY-RUN", initials, note))
            expect("attest-refused", False, "%r/%r must be refused" % (initials, note))
        except SetupError:
            pass

    # 12. not macOS: preflight names the platform and points at the portable waker, and NOTHING
    #     else is read or run — not launchctl, not the clone — even when asked to keep going.
    expect("platform-darwin", platform_problem("darwin") is None, "macOS must be supported")
    c, f = _fake_ctx()
    f.answers = _settled_answers(c)
    c.platform = "linux"
    rows, _ = quiet(lambda: measure(c, apply_it=False, keep_going=True))
    expect("platform-linux", len(rows) == 1 and rows[0][:2] == ("preflight", FAILED)
           and "'linux'" in rows[0][2] and "pr_conflict.py wake" in rows[0][2] and "systemd" in rows[0][2]
           and "gui/" not in rows[0][2] and not f.reads,
           "a non-macOS machine must be told so before anything runs: %s reads=%s" % (rows, f.reads))

    # 13. CLAUDE_CONFIG_DIR: preflight looks where the JOB will look — the value this shell has —
    #     refuses a directory with no transcripts, and the plist carries the value it proved.
    c, f = _fake_ctx()
    c.env = {"CLAUDE_CONFIG_DIR": "/Users/you/alt-claude"}
    f.answers = _settled_answers(c)
    rows, _ = quiet(lambda: measure(c, apply_it=False))
    expect("claude-dir-missing", rows[0][1] == FAILED and "/Users/you/alt-claude has no projects/" in rows[0][2]
           and "CLAUDE_CONFIG_DIR" in rows[0][2], str(rows))
    c, f = _fake_ctx()
    c.env = {"CLAUDE_CONFIG_DIR": "/Users/you/alt-claude"}
    f.answers = [("test -d /Users/you/alt-claude/projects", 0, "")] + _settled_answers(c)
    rows, _ = quiet(lambda: measure(c, apply_it=False, keep_going=True))
    env_w = plistlib.loads(render_plist(c).encode())["EnvironmentVariables"]
    expect("claude-dir-captured", rows[0][1] == ALREADY_DONE and "transcripts from /Users/you/alt-claude" in rows[0][2]
           and env_w.get("CLAUDE_CONFIG_DIR") == "/Users/you/alt-claude", "%s %s" % (rows[0], env_w))

    # 14. the clone is `behind` only when a script the job runs changed: a merge elsewhere leaves
    #     it level (said), a changed pr_conflict.py is named, and a missing import is FAILED.
    other = "def5678000000000000000000000000000000000"
    for diff_out, want, needle in (("", ALREADY_DONE, "identical"),
                                   ("scripts/pr_conflict.py\n", WOULD_CHANGE, "changed scripts/pr_conflict.py")):
        c, f = _fake_ctx()
        f.answers = [("code rev-parse FETCH_HEAD", 0, other + "\n"),
                     ("code diff --name-only %s %s --" % (HEAD, other), 0, diff_out)] + _settled_answers(c)
        rows, _ = quiet(lambda: measure(c, apply_it=False, keep_going=True))
        code_row = [r for r in rows if r[0] == "code"]
        expect("clone-%s" % ("unrelated-merge" if not diff_out else "job-script-merge"),
               code_row and code_row[0][1] == want and needle in code_row[0][2], str(code_row))
    c, f = _fake_ctx()
    f.answers = [("FETCH_HEAD:scripts/pipeline_labels.py", 1, "")] + _settled_answers(c)
    rows, _ = quiet(lambda: measure(c, apply_it=True, keep_going=True))
    code_row = [r for r in rows if r[0] == "code"]
    expect("clone-missing-import", code_row and code_row[0][1] == FAILED
           and "scripts/pipeline_labels.py" in code_row[0][2] and not f.writes, str(code_row))

    # 15. REQUIRED_SCRIPTS is exactly what the job execs: pr_conflict.py's module-level local
    #     imports, transitively. A new import that is not listed would make `behind` blind to it.
    import ast
    here = os.path.dirname(os.path.abspath(__file__))
    closure, todo = set(), ["pr_conflict.py"]
    while todo:
        name = todo.pop()
        if name in closure:
            continue
        closure.add(name)
        with open(os.path.join(here, name), encoding="utf-8") as fh:
            body = ast.parse(fh.read()).body
        for node in body:
            mods = ([a.name for a in node.names] if isinstance(node, ast.Import) else
                    [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            todo += [m.split(".")[0] + ".py" for m in mods if os.path.exists(os.path.join(here, m.split(".")[0] + ".py"))]
    expect("required-scripts-are-the-closure", closure == set(REQUIRED_SCRIPTS),
           "the job execs %s but REQUIRED_SCRIPTS is %s" % (sorted(closure), sorted(REQUIRED_SCRIPTS)))

    # 11. the source: no merge/approve/label path, no sudo, no role-account daemon.
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    for token in ("pr " + "merge", "--app" + "rove", "--add-" + "label", "enable-auto-" + "merge",
                  "\"su" + "do\"", "User" + "Name\":"):
        expect("banned-token", token not in src, "the source names %r" % token)

    if failures:
        say("FAIL pipeline_conflict_waker_setup selftest:")
        for f in failures:
            say("  - " + f)
        return 1
    say("ok — pipeline_conflict_waker_setup: every conf error at once (an unboundable queue and a "
        "credential shape among them); run/attest refused under an agent; a settled machine "
        "verifies clean with no writes; the LaunchAgent carries no UserName, execs the clone, "
        "passes every bound and survives markup in a path; the dry run passes the job's own argv, "
        "blocks on CK-W1 unsigned and separates could-not-tell from failed; a loaded job with a "
        "stale heartbeat is NOT RUNNING (UNKNOWN); loading targets the GUI domain and waits for a "
        "heartbeat; a dry run writes nothing; root and a role account are refused in one pass; a "
        "dirty or foreign clone is never discarded; placeholder sign-offs refused; no merge, "
        "approve, label or sudo path; a non-macOS machine is told so before anything runs; "
        "CLAUDE_CONFIG_DIR is proved where the job will look; the clone is behind only when a "
        "script the job execs changed, and that set is the import closure")
    return 0


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def build_parser():
    p = argparse.ArgumentParser(prog="pipeline_conflict_waker_setup.py",
                                description="Install the conflict waker as your own supervised LaunchAgent.")
    p.add_argument("command", nargs="?", default="run", choices=["run", "status", "verify", "card", "attest"])
    p.add_argument("target", nargs="?", help="a CK-id for `card`, an A-id for `attest`")
    p.add_argument("--conf", default="conflict-waker.conf")
    p.add_argument("--state", default=DEFAULT_STATE_HOME)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--initials", default="")
    p.add_argument("--note", default="")
    p.add_argument("--selftest", action="store_true")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.selftest:
        return selftest()
    try:
        if args.command == "attest" or (args.command == "run" and not args.dry_run):
            refuse_if_agent(args.command)
        if args.command == "card":
            print_card(args.target or "")
            return EX_OK
        unsupported = platform_problem(sys.platform) if args.command in ("run", "verify") else None
        if unsupported:  # said before a conf is asked for: no conf makes it installable here
            say("UNSUPPORTED PLATFORM: " + unsupported)
            return EX_USAGE
        state = State(args.state)
        if args.command == "attest":
            return cmd_attest(state, args.target or "", args.initials, args.note)
        conf, errors = load_conf(args.conf)
        if errors:
            say("Your conf has %d problem(s). Every one of them, in one pass:" % len(errors))
            for e in errors:
                say("  - " + e)
            return EX_USAGE
        ctx = Ctx(conf, se.Runner(dry_run=args.dry_run), state)
        if args.command == "status":
            return cmd_status(ctx)
        if args.command == "verify":
            return cmd_verify(ctx)
        return cmd_run(ctx, args.dry_run)
    except Refusal as exc:
        say(str(exc))
        return EX_REFUSED
    except SetupError as exc:
        say("FAILED: " + str(exc))
        return EX_USAGE


if __name__ == "__main__":
    sys.exit(main())
