#!/usr/bin/env python3
"""Queue installer: the usage job and the morning job, set up with one command (KIT-456).

    python3 scripts/pipeline_queue_setup.py run --dry-run    # read it first
    python3 scripts/pipeline_queue_setup.py run              # the only command that changes anything

The notifier installer's contract: at something only a person can do it prints a card and exits 10; do that and run
it again. Exits: 0 done, 1 failed, 2 conf, 3 refused under a model, 4 not measured, 5 no sudo. Beside a Stage E install
it writes two configs (mode 600) as the role account and installs two LaunchDaemon plists. It never loads them (CK-Q2),
never writes the env file or the bridge's config, never reads a credential (names only), and never merges, approves or
labels. Under a model `run` is REFUSED before the conf is read; `run --dry-run` says NOT MEASURED where sudo is needed.
"""
import argparse
import hashlib
import json
import os
import plistlib
import re
import shlex
import subprocess
import sys
import tempfile
from xml.sax.saxutils import escape as _xml_escape

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from pipeline_dispatch_local import AGENT_ENV_MARKERS  # noqa: E402  (imported, never copied: a copy drifts)
from pipeline_stage_e_setup import (  # noqa: E402  (Stage E's exit codes, outcomes and errors)
    ALREADY_DONE, BLOCKED, DONE, EX_BLOCKED, EX_FAILED, EX_NOPRIV, EX_OK, EX_REFUSED, EX_UNKNOWN, EX_USAGE, FAILED,
    UNKNOWN, WOULD_CHANGE, Blocked, NoPrivilege, Refusal, SetupError, Unknown, say, split_list)
import pipeline_stage_e_setup as se  # noqa: E402
import pipeline_notifier_setup as ns  # noqa: E402  (its shell fragments, path rules and scrub are not its alone)
import pipeline_morning as morning  # noqa: E402  (the jobs: their own loaders judge what this file composes)
import pipeline_queue_read as reader  # noqa: E402
import pipeline_queue_start as starter  # noqa: E402
import pipeline_usage_local as usage_job  # noqa: E402
import pipeline_window as window  # noqa: E402

credential_shape, scrub = ns.credential_shape, ns.scrub
_s = lambda text: scrub(text, ())      # noqa: E731  (everything printed passes through the notifier's scrub)
DEFAULT_CONF = "queue.conf"
# Stage E's selftest finds the two jobs by the spelling in usage_command and morning_command (KIT-280).
USAGE_SCRIPT = "pipeline_usage_local.py"
MORNING_SCRIPT = "pipeline_morning.py"
READER_SCRIPT = "pipeline_queue_read.py"
SELFTESTS = ("pipeline_usage_local.py", "pipeline_queue_read.py", "pipeline_morning.py", "pipeline_queue_start.py",
             "pipeline_queue_pool.py", "pipeline_queue_order.py", "pipeline_day_planner.py", "pipeline_day_plan.py",
             "pipeline_window.py")
DRY_OK = (0, 3)                 # the jobs' own: 0 ran, 3 ran and says a source was not read whole
LABEL_KEYS = ("USAGE_JOB_LABEL", "MORNING_JOB_LABEL")
INTERVAL_KEYS = ("USAGE_INTERVAL_SECONDS", "MORNING_INTERVAL_SECONDS")


class _NotSelftest(object):
    def __contains__(self, name):                 # the functions a walk for what a script loads leaves out: Stage E's rule
        return name == "selftest" or name.startswith("_selftest")


def runtime_closure(roots):
    """The kit scripts these can load, transitively: DERIVED, never listed (Stage E's selftest holds SIBLING_JOBS to the
    same walk)."""
    seen, todo = set(), list(roots)
    while todo:
        name = todo.pop()
        if name not in seen:
            seen.add(name)
            todo.extend(ns._module_imports(os.path.join(HERE, name), _NotSelftest()))
    return tuple(sorted(seen))


REQUIRED_SCRIPTS = runtime_closure((USAGE_SCRIPT, MORNING_SCRIPT, READER_SCRIPT))

# ── the conf: parsed, never sourced; every fault at once, by key and never by value ───────────────────────────
CONF_REQUIRED = ("ROLE_ACCOUNT", "OWNER_USER_IDS", "DISPATCHER_USER_IDS", "TEAM_KEYS", "REVIEWS_TEAM_KEY",
                 "REPO_BY_TEAM") + LABEL_KEYS
CONF_DEFAULTS = {
    "ROLE_KIT_CLONE": se.ROLE_KIT_CLONE_PATH, "ENV_FILE": "~/.stage-e/env", "OWNER_SLACK_USER_IDS": "",
    "REPO_BY_LABEL": "", "BACKLOG": "no", "AGENT_FILED": "off", "PLANNED_LOAD": "40", "DAY_STOP": "60",
    "PACE_TO_RESET": "no", "WEEKLY_BUDGET_USD": "", "SPRINT_PLAN_FILE": "",
    "LANES_PER_REPO": "1", "WINDOW_TIME_ZONE": "", "WINDOW_DAYS": "mon-sat 09:00 6, sun off", "GRACE_MINUTES": "60",
    "PLANNER": "yes", "PLANNER_BIN": "", "PLANNER_TOKEN_ENV": morning.DEFAULTS["planner_token_env"],
    "LINEAR_KEY_ENV": reader.DEFAULTS["linear_key_env"], "BRIDGE_CONFIG": "~/.stage-e/bridge.json",
    "BRIDGE_STATE_FILE": "~/.stage-e/state/bridge-state.json", "FINDINGS_FILE": "~/.stage-e/finding/seen.json",
    "QUEUE_CONFIG": "~/.stage-e/queue/queue.json", "USAGE_CONFIG": "~/.stage-e/usage/usage-config.json",
    "USAGE_FILE": usage_job.DEFAULT_OUT, "USAGE_INTERVAL_SECONDS": "900", "MORNING_INTERVAL_SECONDS": "600",
}
CONF_KEYS = set(CONF_REQUIRED) | set(CONF_DEFAULTS)
# Left invalid on purpose, so an unedited copy of the example cannot run.
CONF_UNEDITED = {"OWNER_USER_IDS": "owner-user-id-1", "DISPATCHER_USER_IDS": "dispatcher-user-id-1",
                 "REPO_BY_TEAM": "ENG=acme/app"}
PATH_KEYS = ("ROLE_KIT_CLONE", "ENV_FILE", "QUEUE_CONFIG", "USAGE_CONFIG", "USAGE_FILE", "BRIDGE_CONFIG",
             "BRIDGE_STATE_FILE", "FINDINGS_FILE")      # the last two may be empty: that record is then off


def whole(text):
    return int(text) if re.match(r"^[0-9]+$", text or "") else None


def num(text):
    """A positive decimal as the number it is (40, 7.5), else None."""
    if not re.match(r"^[0-9]+(\.[0-9]+)?$", text or "") or float(text) <= 0:
        return None
    return int(float(text)) if float(text) == int(float(text)) else float(text)


def pairs(text):
    """({name: repo}, [numbers of the items that are not NAME=owner/name, or repeat a name]) from `A=x/y, B=z/w`."""
    out, bad = {}, []
    for n, item in enumerate(split_list(text), 1):
        name, _eq, repo = (p.strip() for p in item.rpartition("="))
        if not name or not reader.REPO_RE.match(repo) or name in out:
            bad.append(n)
        else:
            out[name] = repo
    return out, bad


def parse_window_days(text):
    """({weekday: {"start", "hours"} or None} for all seven, [faults]) from `mon-sat 09:00 6, sun off`. A weekday the
    list does not name has no window."""
    days, errors = {}, []
    for n, item in enumerate(split_list(text), 1):
        w = item.lower().split()
        first, dash, last = (w[0] if w else "").partition("-")
        span = window.DAYS[window.DAYS.index(first):window.DAYS.index(last or first) + 1] \
            if first in window.DAYS and (not dash or last in window.DAYS) else ()
        hours = num(w[2]) if len(w) == 3 else None
        spec = {"start": w[1], "hours": hours} if hours and window.valid_start(w[1]) and window.valid_hours(hours) else None
        if not span or not (w[1:] == ["off"] or spec):
            errors.append("WINDOW_DAYS item %d is not `mon-sat 09:00 6` or `sun off`" % n)
        elif any(d in days for d in span):
            errors.append("WINDOW_DAYS item %d repeats a weekday" % n)
        else:
            days.update((d, spec) for d in span)
    return dict((d, days.get(d)) for d in window.DAYS), errors


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
        elif key not in CONF_KEYS:
            errors.append("%s: unknown key %s" % (where, key))
        elif credential_shape(val):
            errors.append("%s: %s carries a CREDENTIAL SHAPE; no key here holds one (not shown)" % (where, key))
        else:
            seen[key], values[key] = n, val
    return values, errors


def validate_conf(values, invoking_user=None):
    """(conf, errors): every fault of the conf's own in ONE pass, by key; the jobs' own loaders judge the rest."""
    c, e = dict(CONF_DEFAULTS, **values), []

    def need(ok, why):
        if not ok:
            e.append(why)

    for k in CONF_REQUIRED:
        need(c.get(k), "%s is required" % k)
    for k, v in CONF_UNEDITED.items():
        need(c.get(k) != v, "%s is still the example's value" % k)
    acct = c.get("ROLE_ACCOUNT") or ""
    need(not acct or se._ACCOUNT_RE.match(acct) and acct not in ("root", invoking_user),
         "ROLE_ACCOUNT must be a local account that is not root and not you")
    labels = [c.get(k) or "" for k in LABEL_KEYS]
    for k, label in zip(LABEL_KEYS, labels):
        need(not label or se._RDNS_RE.match(label), "%s is not a reverse-DNS launchd label" % k)
    need(not all(labels) or labels[0] != labels[1], "USAGE_JOB_LABEL and MORNING_JOB_LABEL are one label")
    teams, by_team, by_label = split_list(c.get("TEAM_KEYS")), pairs(c.get("REPO_BY_TEAM")), pairs(c.get("REPO_BY_LABEL"))
    for k, (_found, bad) in (("REPO_BY_TEAM", by_team), ("REPO_BY_LABEL", by_label)):
        need(not bad, "%s item %s is not NAME=owner/name, or repeats a name" % (k, ", ".join(map(str, bad))))
    need(set(by_team[0]) <= set(teams), "REPO_BY_TEAM names a team TEAM_KEYS does not list")
    need(by_label[0] or set(teams) <= set(by_team[0]), "TEAM_KEYS lists a team REPO_BY_TEAM maps to no repository")
    for k, allowed in (("BACKLOG", ("yes", "no")), ("PLANNER", ("yes", "no")), ("AGENT_FILED", ("off", "limits"))):
        need(c.get(k) in allowed, "%s must be %s" % (k, " or ".join(allowed)))
    for k in ("PLANNED_LOAD", "DAY_STOP"):
        need(num(c.get(k)), "%s must be a number above 0" % k)
    need(c.get("PACE_TO_RESET") in ("yes", "no"), "PACE_TO_RESET must be yes or no")
    need(not c.get("WEEKLY_BUDGET_USD") or num(c.get("WEEKLY_BUDGET_USD")), "WEEKLY_BUDGET_USD must be empty or a number above 0")
    need(c.get("PACE_TO_RESET") != "yes" or c.get("WEEKLY_BUDGET_USD"), "WEEKLY_BUDGET_USD is required with PACE_TO_RESET=yes")
    need(not c.get("SPRINT_PLAN_FILE") or re.match(r"^[~/][A-Za-z0-9_./~-]{1,200}\Z", c["SPRINT_PLAN_FILE"]),
         "SPRINT_PLAN_FILE must be a path, or empty")
    need(not c.get("SPRINT_PLAN_FILE") or c.get("WEEKLY_BUDGET_USD"),
         "WEEKLY_BUDGET_USD is required with SPRINT_PLAN_FILE: a week cannot be sized without a budget")
    for k in ("LANES_PER_REPO", "GRACE_MINUTES"):
        need(whole(c.get(k)) is not None, "%s must be a whole number" % k)
    days, bad_days = parse_window_days(c.get("WINDOW_DAYS"))
    e.extend(bad_days)
    need(bad_days or any(days.values()), "WINDOW_DAYS names no day with a window")
    path_ok = lambda v: bool(ns._PATH_RE.match(v)) and "/../" not in v + "/" and v.rstrip("/") not in ("", "~")   # noqa: E731
    binary = c.get("PLANNER_BIN") or ""
    need(binary or c.get("PLANNER") != "yes", "PLANNER_BIN is required with PLANNER=yes")
    need(not binary or binary.startswith("/") and path_ok(binary), "PLANNER_BIN must be an absolute path")
    for k in ("PLANNER_TOKEN_ENV", "LINEAR_KEY_ENV"):
        need(ns._UPPER_SNAKE.match(c.get(k) or ""), "%s must be the NAME of an environment variable" % k)
    need(c.get("PLANNER_TOKEN_ENV") != c.get("LINEAR_KEY_ENV"), "PLANNER_TOKEN_ENV and LINEAR_KEY_ENV name one variable")
    bad = [k for k in PATH_KEYS if (c.get(k) or k in PATH_KEYS[:6]) and not path_ok(c[k])]
    e.extend("%s must be `~/…` (the role account's home) or an absolute path, with no `..`" % k for k in bad)
    for k in INTERVAL_KEYS:
        need(60 <= (whole(c.get(k)) or 0) <= 86400, "%s must be 60 to 86400 seconds" % k)
    need("ROLE_KIT_CLONE" in bad or c["ROLE_KIT_CLONE"].rstrip("/") == se.ROLE_KIT_CLONE_PATH,
         "ROLE_KIT_CLONE is not Stage E's clone, %s" % se.ROLE_KIT_CLONE_PATH)
    e.extend(path_problems(c, ns.ROLE_HOME_SENTINEL, bad))
    return c, e


def path_problems(c, home, skip=()):
    """Two keys naming one file, or a file inside the clone (a git working tree), against a home."""
    p = dict((k, ns.resolve_path(c[k], home)) for k in PATH_KEYS if c.get(k) and k not in skip)
    keys = sorted(k for k in p if k != "ROLE_KIT_CLONE")
    return ["%s and %s are one file" % (a, b) for i, a in enumerate(keys) for b in keys[i + 1:] if p[a] == p[b]] + [
        "%s is inside ROLE_KIT_CLONE, a git working tree" % k for k in keys
        if "ROLE_KIT_CLONE" in p and ns._under(p[k], p["ROLE_KIT_CLONE"])]


def load_conf(path, user=None):
    try:
        with open(path, encoding="utf-8") as fh:
            values, errors = parse_conf(fh.read(), os.path.basename(path))
    except OSError as exc:
        raise SetupError("could not read %s (%s): cp queue.conf.example queue.conf && chmod 600 queue.conf"
                         % (path, exc.strerror))
    conf, more = validate_conf(values, user)
    return conf, errors + more


# ── what it composes: two documents, each judged by the jobs' own loaders ─────────────────────────────────────
def compose(c):
    """(the queue's config, the usage job's config); `~` stays literal. The planner and the order get the same numbers,
    and the usage job's `out` is the file the queue reads."""
    load, lanes = num(c["PLANNED_LOAD"]), whole(c["LANES_PER_REPO"])
    queue = {
        "pool": {"owner_user_ids": split_list(c["OWNER_USER_IDS"]), "dispatcher_user_ids": split_list(c["DISPATCHER_USER_IDS"]),
                 "owner_slack_user_ids": split_list(c["OWNER_SLACK_USER_IDS"]), "team_keys": split_list(c["TEAM_KEYS"]),
                 "reviews_team_key": c["REVIEWS_TEAM_KEY"], "backlog": c["BACKLOG"] == "yes", "agent_filed": c["AGENT_FILED"]},
        "order": dict({"lanes_per_repo": lanes, "planned_load": load, "day_stop": num(c["DAY_STOP"])},
                      **({"pace_to_reset": True, "weekly_budget_usd": num(c["WEEKLY_BUDGET_USD"])}
                         if c["PACE_TO_RESET"] == "yes" else
                         {"weekly_budget_usd": num(c["WEEKLY_BUDGET_USD"])} if c["SPRINT_PLAN_FILE"] else {})),
        "planner": {"lanes_per_repo": lanes, "planned_load": load},
        "window": {"time_zone": c["WINDOW_TIME_ZONE"] or None, "days": parse_window_days(c["WINDOW_DAYS"])[0],
                   "grace_minutes": whole(c["GRACE_MINUTES"])},
        "reader": {"linear_key_env": c["LINEAR_KEY_ENV"], "repo_by_team": pairs(c["REPO_BY_TEAM"])[0],
                   "repo_by_label": pairs(c["REPO_BY_LABEL"])[0], "findings_file": c["FINDINGS_FILE"],
                   "bridge_state_file": c["BRIDGE_STATE_FILE"], "ledger_file": reader.DEFAULTS["ledger_file"],
                   "inputs_file": reader.DEFAULTS["inputs_file"]},
        "morning": dict({"planner": c["PLANNER"] == "yes", "planner_token_env": c["PLANNER_TOKEN_ENV"],
                         "usage_file": c["USAGE_FILE"], "day_plan_file": morning.DEFAULTS["day_plan_file"]},
                        **({"planner_bin": c["PLANNER_BIN"]} if c["PLANNER_BIN"] else {}),
                        **({"sprint_plan_file": c["SPRINT_PLAN_FILE"]} if c["SPRINT_PLAN_FILE"] else {})),
    }
    return queue, {"reviews_team_key": c["REVIEWS_TEAM_KEY"], "out": c["USAGE_FILE"]}


def with_home(obj, home):
    """`obj` with every `~/…` string made absolute under `home`: how the job, run as the role account, expands it."""
    if isinstance(obj, str):
        return home.rstrip("/") + "/" + obj[2:] if obj.startswith("~/") else obj
    if isinstance(obj, list):
        return [with_home(x, home) for x in obj]
    return dict((k, with_home(v, home)) for k, v in obj.items()) if isinstance(obj, dict) else obj


def validate_composed(queue, usage, role_home):
    """The two files' bytes, or SetupError before anything is written. The judges are the jobs' OWN loaders (the morning
    job's, the starter's, the usage job's), run from THIS checkout with `~` made the role account's home."""
    bodies = [json.dumps(doc, indent=2, sort_keys=True) + "\n" for doc in (queue, usage)]
    problems = []
    with tempfile.TemporaryDirectory(prefix="queue-setup-") as tmp:
        home = role_home or os.path.join(tmp, "role-home")
        paths = [os.path.join(tmp, name) for name in ("queue.json", "usage.json")]
        for path, doc in zip(paths, (queue, usage)):
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(with_home(doc, home), fh)
        problems += morning.load_config(paths[0])[1] + starter.load(with_home(queue, home))[1]
        try:
            usage_job.load_config(paths[1])
        except usage_job.ConfigError as exc:
            problems.append("usage config: %s" % exc)
    if problems:
        raise SetupError("the jobs' own loaders REFUSE this composition, so nothing was written:\n  "
                         + "\n  ".join(dict.fromkeys(problems)))
    return bodies


# ── the jobs: commands and plists, from the conf alone ────────────────────────────────────────────────────────
def usage_command(c):
    script = c["ROLE_KIT_CLONE"].rstrip("/") + "/scripts/" + USAGE_SCRIPT
    return ns.exec_prefix(c) + "%s run --config %s" % (ns._q(script), ns._q(c["USAGE_CONFIG"]))


def morning_command(c):
    script = c["ROLE_KIT_CLONE"].rstrip("/") + "/scripts/" + MORNING_SCRIPT
    return ns.exec_prefix(c) + "%s run --config %s" % (ns._q(script), ns._q(c["QUEUE_CONFIG"]))


def reader_command(c):
    script = "/".join((c["ROLE_KIT_CLONE"].rstrip("/"), "scripts", READER_SCRIPT))
    return ns.exec_prefix(c) + "%s --config %s" % (ns._q(script), ns._q(c["QUEUE_CONFIG"]))


def job_specs(c, home):
    """The two jobs in install order, each with its plist's body."""
    out = []
    for i, (name, script, command, config, log) in enumerate((
            ("the usage job", USAGE_SCRIPT, usage_command(c), "USAGE_CONFIG", "usage.log"),
            ("the morning job", MORNING_SCRIPT, morning_command(c), "QUEUE_CONFIG", "morning.log"))):
        job = {"name": name, "label": c[LABEL_KEYS[i]], "plist": se._dispatcher_plist(c[LABEL_KEYS[i]]), "script": script,
               "interval": int(c[INTERVAL_KEYS[i]]), "command": command,
               "log": os.path.dirname(ns.resolve_path(c[config].rstrip("/"), home)) + "/" + log}
        out.append(dict(job, body=render_plist(c, home, job)))
    return out


def render_plist(c, home, job):
    """Stage E's own PLIST, filled in. launchd expands nothing, so home and log are absolute; `$HOME` is /bin/sh's."""
    esc = _xml_escape
    return se.PLIST.format(label=esc(job["label"]), account=esc(c["ROLE_ACCOUNT"]), home=esc(home), path=esc(se.DAEMON_PATH),
                           command=esc(job["command"]), interval=job["interval"], log=esc(job["log"]))


def foreign_plist(body, script):
    """Why this plist is not the job that runs `script`, in its own words, or None."""
    if body is None:
        return None
    try:
        doc = plistlib.loads(body.encode("utf-8"))
    except Exception:                                             # noqa: BLE001
        return "it does not parse as a plist"
    runs = re.findall(r"[\w-]+\.(?:py|js|rb|sh)\b", " ".join(str(a) for a in doc.get("ProgramArguments") or []))
    return None if script in runs else "it runs %s, not %s" % (", ".join(dict.fromkeys(runs)) or "something else", script)


# ── cards, the agent policy, and the context every step is handed ─────────────────────────────────────────────
CARDS = {
    "CK-Q1": {
        "title": "Point the bridge at the queue",
        "why": ("The queue only decides; the Slack bridge starts work, and asks the queue only when its own config names "
                "the queue's config file. The bridge installer writes that config. This one only reads it back."),
        "do": ["1. In bridge.conf set the queue's config file, as the role account sees it:",
               "       QUEUE_CONFIG=${QUEUE_CONFIG}",
               "2. Write the bridge's config:  python3 scripts/pipeline_bridge_setup.py install --apply",
               "3. Run this installer again:  python3 ${SELF} run${FLAGS}"],
        "good": "the bridge's config, read as ${ROLE_ACCOUNT}, names ${QUEUE_CONFIG} as its queue_config"},
    "CK-Q2": {
        "title": "Load the usage job and the morning job",
        "why": ("Loading starts them. Inside the working window the morning job makes ONE capped model call on your "
                "subscription to plan the day. Neither job starts a session: the bridge does. This installer loads none."),
        "do": ["Enable, then load, both:",
               "    sudo launchctl enable system/${USAGE_JOB_LABEL} && sudo launchctl bootstrap system ${USAGE_PLIST}",
               "    sudo launchctl enable system/${MORNING_JOB_LABEL} && sudo launchctl bootstrap system ${MORNING_PLIST}",
               "If a plist changed after it was loaded, `sudo launchctl bootout system/<label>` first. Then run it again:",
               "    python3 ${SELF} run${FLAGS}"],
        "good": "`launchctl print` shows each job running its plist's command, and `run` exits 0"},
}


def _self_path():
    return os.path.relpath(os.path.abspath(__file__))


def print_card(cid, c, flags=""):
    card = CARDS[cid]
    values = dict(c, SELF=_self_path(), FLAGS=flags, USAGE_PLIST=se._dispatcher_plist(c[LABEL_KEYS[0]]),
                  MORNING_PLIST=se._dispatcher_plist(c[LABEL_KEYS[1]]))
    say("\n" + "=" * 74 + "\n %s — %s\n" % (cid, card["title"]) + "=" * 74 + "\n\nWHY IT IS YOURS")
    for line in se._wrap(card["why"]):
        say("  " + line)
    say("\nWHAT TO DO")
    for line in card["do"]:
        say("  " + ns._fill(line, values))
    say("\nGOOD: %s\nNEVER: merge or approve a PR, apply a protected label, or reword a blocked command.\n"
        % ns._fill(card["good"], values))


def agent_markers(env=None):
    """Presence, not truthiness: a marker set to the empty string is set."""
    return [m for m in AGENT_ENV_MARKERS if m in (os.environ if env is None else env)]


def refuse_if_agent(action, env=None):
    found = agent_markers(env)
    if found:
        raise Refusal("REFUSED: %s changes things and this is an agent environment (%s set). A session that installs its "
                      "own supervision is what this refusal prevents; there is no override.\n"
                      "  A PERSON runs this, in a terminal:  python3 %s %s\n  Read-only meanwhile:  run --dry-run"
                      % (action, ", ".join(found), _self_path(), action))


def acquire_privilege(ctx, dry_run):
    """Stage E's sudo session: the password once, before the first probe, never under a model. Its refusal names Stage
    E's command, so it is re-raised with this one."""
    if ctx.agent:
        return False
    try:
        ctx.sudo.acquire("`run%s` works as the %s role account and asks launchd about two jobs. It loads none." % (
            " --dry-run" if dry_run else "", ctx.account), "")
    except NoPrivilege:
        raise NoPrivilege("NO ADMINISTRATOR ACCESS — nothing was attempted or changed. Fix that and run again:  "
                          "python3 %s run%s%s" % (_self_path(), " --dry-run" if dry_run else "", ctx.flags))
    return True


class Ctx(object):
    def __init__(self, conf, runner, env=None, sudo=None):
        self.conf, self.runner = conf, runner
        self.env = os.environ if env is None else env
        self.agent, self.sudo = agent_markers(self.env), sudo or se.SudoSession()
        self.role_home = self.loaded = None
        self.config_current = self.jobs_current = False
        self.flags, self.checkout_scripts = "", HERE        # this checkout's scripts, which the clone is held to

    account = property(lambda self: self.conf["ROLE_ACCOUNT"])

    def not_measured(self, what):
        return Unknown("NOT MEASURED — an agent environment (%s set): %s needs `sudo`, which no model runs" % (
            ", ".join(self.agent), what), "run the same command in your own terminal")

    def as_role(self, script, what, stdin=None, why=None, timeout=300):
        if self.agent:
            raise self.not_measured(what)
        return self.runner.as_role(self.account, script, stdin=stdin, why=why, timeout=timeout)

    def as_root(self, argv, what, why=None):
        if self.agent:
            raise self.not_measured(what)
        return self.runner.as_root(argv, why=why)


# ── steps: each returns (ok, detail, notes) or raises Blocked / Unknown / SetupError ──────────────────────────
# ok=True is ALREADY-DONE; ok=False is DONE under `run` and WOULD-CHANGE otherwise.
def checkout_hash(root, name):
    try:
        with open(os.path.join(root, name), "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def role_problems(ctx):
    """(problems, unmeasured), from inside the role account's shell: its clone, byte for byte this checkout's (the
    selftests passed HERE), and its env file by NAME, with the notifier installer's probe: it never prints a value."""
    c, names = ctx.conf, REQUIRED_SCRIPTS
    clone, env = c["ROLE_KIT_CLONE"].rstrip("/"), c["ENV_FILE"]
    problems, unmeasured = [], []
    res = ctx.as_role(ns.CLONE_HASH_SH.replace("@DIR@", ns._q(clone + "/scripts")).replace("@FILES@", " ".join(names)),
                      what="hashing the clone's scripts")
    if res.ok:
        theirs = ns.parse_clone_hashes(res.out)
        off = [n for n in names if theirs.get(n, ("",))[0] != "sha256" or theirs[n][1] != checkout_hash(ctx.checkout_scripts, n)]
        if off:
            problems.append("the clone at %s is not level with this checkout (%s): merge the change, then let Stage E's "
                            "`run` move it" % (clone, ", ".join(off)))
    else:
        unmeasured.append("the clone could not be read as %s (exit %d)" % (ctx.account, res.rc))
    res = ctx.as_role(ns._cred_script(ns.CRED_PROBE_SH, dict(                 # its Slack-token arm is unused here
        c, CHAT_TOKEN_ENV="QUEUE_SETUP_UNUSED", WRITER=c["PLANNER"], WRITER_TOKEN_ENV=c["PLANNER_TOKEN_ENV"])),
        what="reading %s by name" % env)
    facts = ns.parse_cred_probe(res.out)
    if res.rc == 9:
        problems.append("%s does not exist for %s: Stage E writes it" % (env, ctx.account))
    elif not facts:
        unmeasured.append("%s could not be read as %s" % (env, ctx.account))
    else:
        problems += ["%s %s" % (env, why) for bad, why in (
            (facts["mode"] != "600", "is mode %s, want 600" % facts["mode"]),
            (facts["key"] != "ok", "has no usable %s (Stage E writes it)" % c["LINEAR_KEY_ENV"]),
            (c["PLANNER"] == "yes" and facts["writer"] != "set", "lacks %s, the planner's token (or set PLANNER=no)"
             % c["PLANNER_TOKEN_ENV"])) if bad]
    return problems, unmeasured


def step_preflight(ctx, apply_it):
    r, problems, unmeasured = ctx.runner, [], []
    home = r.read(["dscl", ".", "-read", "/Users/" + ctx.account, "NFSHomeDirectory"])
    value = (home.out.split("NFSHomeDirectory:", 1) + [""])[1].strip().split("\n")[0].strip() if home.ok else ""
    if value.startswith("/") and value != "/" and ns._PATH_RE.match(value):
        ctx.role_home = value
        problems += path_problems(ctx.conf, value)
    else:
        problems.append("the role account has no home (dscl): install Stage E first")
    failed = [n for n in SELFTESTS if not r.read([sys.executable, os.path.join(HERE, n), "--selftest"], timeout=300).ok]
    if failed:
        problems.append("selftest FAILED from this checkout: %s" % ", ".join(failed))
    if ctx.agent:
        unmeasured.append("the role account's clone and env file (they need `sudo -u %s`)" % ctx.account)
    else:
        found, unread = role_problems(ctx)
        problems, unmeasured = problems + found, unread
    if problems:
        raise SetupError("preflight found %d problem(s), all of them:\n  - %s" % (len(problems), "\n  - ".join(problems)))
    if unmeasured:
        raise Unknown("NOT MEASURED: " + "; ".join(unmeasured), "run the same command in your own terminal")
    return True, "the role's home, clone (%d files, level with this checkout) and env file, and %d selftests, all pass" % (
        len(REQUIRED_SCRIPTS), len(SELFTESTS)), []


def read_role_file(ctx, path):
    """(content, mode) as the role account holds the file; content None when it is absent."""
    res = ctx.as_role(ns.CONFIG_READ_SH.replace("@F@", ns._q(path)), what="reading %s" % path)
    if res.rc == 9:
        return None, ""
    if not res.ok:
        raise Unknown("%s could not be read as %s (exit %d)" % (path, ctx.account, res.rc), "run the same command again")
    first, _sep, rest = res.out.partition("\n")
    return rest, first.split("=", 1)[1] if first.startswith("mode=") else ""


def step_config(ctx, apply_it):
    c = ctx.conf
    bodies, stale = validate_composed(*compose(c), ctx.role_home), []
    for path, body in ((c["QUEUE_CONFIG"], bodies[0]), (c["USAGE_CONFIG"], bodies[1])):
        have, mode = read_role_file(ctx, path)
        if have != body or mode != "600":
            stale.append((path, body, "absent" if have is None else "differs" if have != body else "mode " + mode))
    if not stale:
        ctx.config_current = True
        return True, "both configs are current, mode 600, and accepted by the jobs' own loaders", []
    named = ", ".join("%s (%s)" % (p, why) for p, _b, why in stale)
    if not apply_it:
        return False, "would write %s; the jobs' own loaders accept it" % named, []
    for path, body, _why in stale:
        res = ctx.as_role(ns.CONFIG_WRITE_SH.replace("@F@", ns._q(path)), what="writing " + path, stdin=body,
                          why="write %s (mode 600)" % path)
        have, mode = read_role_file(ctx, path) if res.ok else (None, "")
        if have != body or mode != "600":
            raise SetupError("could not write %s as %s, or reading it back does not match (exit %d, mode %s)"
                             % (path, ctx.account, res.rc, mode))
    ctx.config_current = True
    return False, "wrote %s, mode 600" % named, []


def step_jobs(ctx, apply_it):
    if not ctx.role_home:
        raise Unknown("waits on preflight: the role account's home is not known", "clear the preflight row first")
    r, todo, refused = ctx.runner, [], []
    for job in job_specs(ctx.conf, ctx.role_home):
        have = None
        if r.read(["test", "-e", job["plist"]]).ok:       # root:wheel 644: readable without sudo, so measurable under a model
            got = r.read(["cat", job["plist"]])
            if not got.ok:
                raise Unknown("%s exists and could not be read" % job["plist"], "fix its permissions (root:wheel 644)")
            have = got.out
        # `install` as root replaces whatever is there and the label is free-typed: only this job's own may be replaced.
        foreign = foreign_plist(have, job["script"]) if have != job["body"] else None
        if foreign:
            refused.append("%s is NOT %s: %s" % (job["plist"], job["name"], foreign))
        elif have != job["body"]:
            todo.append((job, "absent" if have is None else "differs"))
    if refused:
        raise SetupError("\n".join(refused) + "\n  Replacing it would destroy that job, with no backup. Pick labels "
                         "nothing else uses.")
    if not todo:
        ctx.jobs_current = True
        return True, "both plists are installed and current (never loaded here: CK-Q2)", []
    named = ", ".join("%s (%s)" % (j["plist"], why) for j, why in todo)
    if not apply_it:
        return False, "would install %s, not loaded" % named, []
    if ctx.agent:
        raise ctx.not_measured("installing %s" % named)
    for job, _why in todo:
        se._install_plist(r, job["label"], job["body"])
        if r.read(["cat", job["plist"]]).out != job["body"]:
            raise SetupError("%s was installed, and reading it back does not match" % job["plist"])
    ctx.jobs_current = True
    return False, "installed %s, NOT loaded (CK-Q2)" % named, []


def step_dry_run(ctx, apply_it):
    """Each job once as the role account, through its own shell command plus --dry-run: exit 0 or 3 is done, anything else
    fails with the job's last line, scrubbed. They write nothing."""
    if not (ctx.config_current and ctx.jobs_current):
        waits = " and ".join(n for n, ok in (("config", ctx.config_current), ("jobs", ctx.jobs_current)) if not ok)
        if apply_it:
            raise Unknown("waits on %s" % waits, "clear those rows first")
        return False, "waits on %s: a dry run runs the jobs against the files they read" % waits, []
    if not apply_it:
        return False, "would run the usage job, the reader and the morning job once each as %s" % ctx.account, []
    say("\n  -- the dry runs, as %s --" % ctx.account)
    results = []
    for name, command in (("usage job", usage_command(ctx.conf)), ("queue reader", reader_command(ctx.conf)),
                          ("morning job", morning_command(ctx.conf))):
        res = ctx.as_role(command + " --dry-run", what="running the %s's dry run" % name, timeout=1000)
        last = ns._last_line(_s((res.out or "") + "\n" + (res.err or "")))[:150]
        say("    %-13s exit %-3d %s" % (name, res.rc, last))
        results.append((name, res.rc, last))
    failed = ["%s: exit %d — %s" % r for r in results if r[1] not in DRY_OK]
    if failed:
        raise SetupError("%d of the 3 dry runs FAILED, as %s:\n  %s" % (len(failed), ctx.account, "\n  ".join(failed)))
    return False, "the three dry runs passed as %s: %s" % (ctx.account, ", ".join(
        "%s exit %d" % (n, rc) for n, rc, _l in results)), [
        "Exit 3 means: ran, and a source was not read whole. Its last line, above, says which."]


def same_file(a, b, home):
    """Two spellings of one file, as the role account sees them (`~/x`, an absolute path, a link)."""
    return isinstance(a, str) and bool(a) and ns.resolve_path(a, home) == ns.resolve_path(b, home)


def step_bridge(ctx, apply_it):
    c = ctx.conf
    doc, why = ns.read_role_json(ctx, c["BRIDGE_CONFIG"], "the bridge's config")
    if doc is None and why != "absent":
        raise Unknown("the bridge's config %s could not be read: %s" % (c["BRIDGE_CONFIG"], why), "run it again")
    if doc is None:
        raise Blocked("CK-Q1", "the bridge's config %s does not exist for %s yet" % (c["BRIDGE_CONFIG"], ctx.account))
    if not same_file(doc.get("queue_config"), c["QUEUE_CONFIG"], ctx.role_home or ns.ROLE_HOME_SENTINEL):
        raise Blocked("CK-Q1", "the bridge's config %s names %s queue config: it starts nothing from this queue" % (
            c["BRIDGE_CONFIG"], "another" if doc.get("queue_config") else "no"))
    return True, "the bridge's config names this queue config (queue_config), read back as %s" % ctx.account, []


def loaded_state(ctx, job):
    """(state, why): "absent", "same" (this plist's command and interval), "different", or "unclear" (the print named
    neither)."""
    res = ctx.as_root(["launchctl", "print", "system/" + job["label"]], what="asking launchd about " + job["label"])
    text = (res.out or "") + (res.err or "")
    if not res.ok:
        if res.rc == 113 or "Could not find service" in text:
            return "absent", ""
        raise Unknown("could not ask launchd about system/%s (exit %d)" % (job["label"], res.rc), "run it again")
    running = ns.loaded_job(text)
    want = [str(a) for a in plistlib.loads(job["body"].encode("utf-8"))["ProgramArguments"]]
    if running is None:
        return "unclear", ""
    if running["arguments"] and running["arguments"] != want:
        return "different", "a DIFFERENT command from its plist"
    if running["interval"] is not None and running["interval"] != job["interval"]:
        return "different", "a %s s interval, and its plist says %s s" % (running["interval"], job["interval"])
    return "same", ""


def step_enable(ctx, apply_it):
    """Loaded, and running THIS plist, for both jobs: a plist on disk is not what launchd holds."""
    if not ctx.role_home:
        raise Unknown("waits on preflight: the role account's home is not known", "clear the preflight row first")
    jobs = job_specs(ctx.conf, ctx.role_home)
    states = [loaded_state(ctx, job) for job in jobs]
    ctx.loaded = [j["label"] for j, (state, _w) in zip(jobs, states) if state != "absent"]
    owed = ["system/%s is %s" % (j["label"], "not loaded (a job paused with `disable` reads the same)" if state == "absent"
                                 else "loaded and running %s: unload it, then load it" % why)
            for j, (state, why) in zip(jobs, states) if state in ("absent", "different")]
    if owed:
        raise Blocked("CK-Q2", "\n".join(owed))
    unclear = [j["label"] for j, (state, _w) in zip(jobs, states) if state == "unclear"]
    if unclear:
        raise Unknown("system/%s is loaded, and launchd's print named neither its arguments nor its interval: whether it "
                      "runs THIS plist is not measured" % ", system/".join(unclear),
                      "compare it with its plist:  sudo launchctl print system/" + unclear[0])
    return True, "both jobs are loaded, each running its plist's own command: %s" % ", ".join(
        "system/%s every %s s" % (j["label"], j["interval"]) for j in jobs), []


def step_handover(ctx, apply_it):
    """What is on, what is off by choice, and what nothing here has seen."""
    c = ctx.conf
    on = ["%s system/%s: %s" % (j["name"], j["label"], "NOT loaded (CK-Q2)" if ctx.loaded is not None and j["label"] not in
                                ctx.loaded else "loaded") for j in job_specs(c, ctx.role_home or ns.ROLE_HOME_SENTINEL)]
    off = ["%s=%s" % (k, c[k]) for k, v in (("BACKLOG", "no"), ("AGENT_FILED", "off"), ("PLANNER", "no")) if c[k] == v]
    unproven = ["the first live start: nothing here has seen the bridge start a ticket from this queue",
                "the morning job inside the window: a dry run outside it stops at the closed window"] + [
        "the planner's first real answer and its cost: a dry run calls no model"] * (c["PLANNER"] == "yes")
    return True, "%d jobs on, %d choices off, %d not proven" % (len(on), len(off), len(unproven)), (
        ["ON: " + x for x in on] + ["OFF (your choice): " + ", ".join(off)] * bool(off) + ["NOT PROVEN: " + x for x in unproven])


STEPS = (("preflight", step_preflight), ("config", step_config), ("jobs", step_jobs), ("dry-run", step_dry_run),
         ("bridge", step_bridge), ("enable", step_enable), ("handover", step_handover))
_SEVERITY = (FAILED, UNKNOWN, BLOCKED, WOULD_CHANGE, DONE, ALREADY_DONE)
_EXIT = {FAILED: EX_FAILED, UNKNOWN: EX_UNKNOWN, BLOCKED: EX_BLOCKED, WOULD_CHANGE: EX_BLOCKED, DONE: EX_OK,
         ALREADY_DONE: EX_OK}


def run_steps(ctx, apply_it, keep_going):
    """(exit code, rows). `run` stops at the first row a person must clear; a dry run goes on. A step's surprise is a
    FAILED row, never a traceback."""
    rows, held, notes = [], [], []
    for sid, fn in STEPS:
        card = problem = None
        try:
            ok, detail, more = fn(ctx, apply_it)
            outcome, detail = ALREADY_DONE if ok else DONE if apply_it else WOULD_CHANGE, _s(detail)
            notes += [(sid, _s(n)) for n in more]
        except Blocked as exc:
            outcome, card, detail = BLOCKED, exc.card_id, _s(exc.extra) or CARDS[exc.card_id]["title"]
        except Unknown as exc:
            outcome, detail = UNKNOWN, _s(exc.what)
            problem = Unknown(detail, _s(exc.remedy))
        except Exception as exc:                                  # noqa: BLE001
            outcome = FAILED
            detail = problem = _s(str(exc) if isinstance(exc, (SetupError, Refusal))
                                  else "unexpected %s: %s" % (type(exc).__name__, exc))
        rows.append((sid, outcome, detail))
        if (card and not keep_going) or problem:
            held.append((sid, card, problem))
        if (card or problem) and not keep_going:
            break
    say("\n-- steps --")
    for sid, outcome, detail in rows:
        say("  %-10s %-17s %s" % (sid, outcome, detail.splitlines()[0][:110] if detail else ""))
    for sid, note in notes:
        say("\n  note from `%s`:\n    %s" % (sid, "\n    ".join(se._wrap(note))))
    for sid, card, problem in held:
        if card:
            print_card(card, ctx.conf, ctx.flags)
            say("Do that, then run the same command again:\n    python3 %s run%s" % (_self_path(), ctx.flags))
        elif isinstance(problem, Unknown):
            se._print_unknown(sid, problem)
        else:
            say("\nFAILED at step `%s`:\n  %s" % (sid, problem.replace("\n", "\n  ")))
    return _EXIT[next((s for s in _SEVERITY if any(o == s for _sid, o, _d in rows)), ALREADY_DONE)], rows


def cmd_run(ctx, dry_run):
    """A dry run changes nothing, and says so: its runner would record a write, and a recorded write is a defect."""
    if not dry_run:
        refuse_if_agent("run", ctx.env)
    say("Queue installer — %s, role account %s (jobs are never loaded here)%s" % (
        "DRY RUN: nothing will be changed" if dry_run else "the only command that changes anything", ctx.account,
        "\n  AGENT ENVIRONMENT (%s set): nothing needing sudo attempted, nothing written" % ", ".join(ctx.agent)
        if ctx.agent else ""))
    code, rows = run_steps(ctx, not dry_run, dry_run)
    if dry_run and ctx.runner.writes:
        say("\nBUG: a dry run recorded %d write(s); that is a defect in this file." % len(ctx.runner.writes))
        return EX_FAILED
    say("\n" + ("DRY RUN: nothing was changed." if dry_run else "DONE — every step holds. Nothing merged, approved or "
                "labelled by this installer." if code == EX_OK else "Not done yet: see above."))
    return code


def main(argv=None):
    p = argparse.ArgumentParser(prog="pipeline_queue_setup.py", description="Activate the queue's two jobs.")
    p.add_argument("command", nargs="?", default="run", choices=["run"])
    p.add_argument("--conf", default=DEFAULT_CONF)
    p.add_argument("--dry-run", action="store_true", help="measure every step and change nothing")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)
    if args.selftest:
        from scratch_run import contained   # KIT-450: its temp folders go when it ends
        return contained(selftest, "queue-setup-selftest-run-")
    try:
        if not args.dry_run:        # BEFORE the conf is read: a session must not discover what it would have installed
            refuse_if_agent("run")
        conf, errors = load_conf(args.conf, ns.invoking_user())
        if errors:
            say("Your conf has %d problem(s), all of them:\n  - %s\n\nFix them, then run the same command again."
                % (len(errors), "\n  - ".join(errors)))
            return EX_USAGE
        ctx = Ctx(conf, se.Runner(dry_run=args.dry_run))
        ctx.flags = " --conf " + shlex.quote(args.conf) if args.conf != DEFAULT_CONF else ""
        acquire_privilege(ctx, args.dry_run)
        return cmd_run(ctx, args.dry_run)
    except (Refusal, NoPrivilege, SetupError) as exc:
        say(("FAILED: " if isinstance(exc, SetupError) else "") + str(exc))
        return EX_REFUSED if isinstance(exc, Refusal) else EX_NOPRIV if isinstance(exc, NoPrivilege) else EX_USAGE


# ── --selftest: offline; every command stood in for, a stand-in that writes names its path, a mutant for each rule ──
# Stands in for the three scripts: shows what it saw, and on a scripted failure ends with a line that leaks the key.
FAKE_JOB = (
    "import os, sys\n"
    "name = os.path.basename(sys.argv[0])\n"
    "flag = os.path.join(os.environ['HOME'], '.fake-exit-' + name)\n"
    "code = int(open(flag).read()) if os.path.exists(flag) else 0\n"
    "key = os.environ.get('STAGE_E_LINEAR_API_KEY', '')\n"
    "print('%s: key %d chars' % (name, len(key)))\n"
    "print('%s: %s' % (name, 'done' if code == 0 else 'failed: Authorization: Bearer ' + key))\n"
    "sys.exit(code)\n")
BASE_CONF = {"ROLE_ACCOUNT": "_exqueue", "OWNER_USER_IDS": "owner-1", "DISPATCHER_USER_IDS": "disp-1",
             "TEAM_KEYS": "ENG,OPS", "REVIEWS_TEAM_KEY": "REV", "REPO_BY_TEAM": "ENG=acme/app,OPS=acme/ops",
             "USAGE_JOB_LABEL": "com.example.queue-usage", "MORNING_JOB_LABEL": "com.example.queue-morning",
             "PLANNER_BIN": "/opt/claude/bin/claude"}


def _put(path, body, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(path, mode)


class FakeMachine(se.Runner):
    """A role home in a temporary folder: a clone level with this checkout, an env file, a stand-in for each job script.
    `sudo -u <role> -H /bin/sh -c …` runs through a REAL /bin/sh with HOME there; the rest is scripted."""

    def __init__(self, root, conf, key, token):
        se.Runner.__init__(self)
        self.root, self.home, self.scripts, self.plists, self.loaded = root, root + "/role-home", root + "/checkout", {}, {}
        self.targets = [se._dispatcher_plist(conf[k]) for k in LABEL_KEYS]
        self.failing, self.sudo_argv, self.role_scripts, self.args = set(), [], [], True
        for where in (self.home + "/.stage-e/kit/scripts", self.scripts):
            for name in REQUIRED_SCRIPTS:
                _put(where + "/" + name, FAKE_JOB if name in (USAGE_SCRIPT, MORNING_SCRIPT, READER_SCRIPT) else "# fake\n")
        _put(self.home + "/.stage-e/env", "STAGE_E_LINEAR_API_KEY=%s\nCLAUDE_WRITER_OAUTH_TOKEN=%s\n" % (key, token), 0o600)

    def load(self, only=None):
        """What a person does at CK-Q2: launchd takes a COPY of the file as it is now."""
        for path in self.targets[:only]:
            self.loaded[os.path.basename(path)[:-len(".plist")]] = self.plists[path]

    def _exec(self, argv, stdin, timeout, cwd=None, **kwargs):
        argv = list(argv)
        if argv[0] == "sudo":
            self.sudo_argv.append(argv)
        if argv[:2] == ["sudo", "-u"] and argv[3:6] == ["-H", "/bin/sh", "-c"]:
            if [p for p in re.findall(r'"(/[^"]*)"', argv[6]) if not p.startswith(self.root)]:
                return se.Result(99, "", "a role script names a path outside its home")
            self.role_scripts.append(argv[6])
            done = subprocess.run(["/bin/sh", "-c", argv[6].replace("/usr/bin/python3", sys.executable)], input=stdin,
                                  capture_output=True, text=True, timeout=timeout,
                                  env={"HOME": self.home, "PATH": "/usr/bin:/bin"})
            return se.Result(done.returncode, done.stdout, done.stderr)
        if argv[0] == "dscl":
            return se.Result(0, "NFSHomeDirectory: %s\n" % self.home)
        if argv[2:] == ["--selftest"]:
            return se.Result(int(os.path.basename(argv[1]) in self.failing))
        if argv[:2] == ["test", "-e"]:
            return se.Result(int(argv[2] not in self.plists))
        if argv[0] == "cat":
            return se.Result(0, self.plists[argv[1]]) if argv[1] in self.plists else se.Result(1)
        if argv[0] == "plutil":
            return se.Result(0)
        if argv[:2] == ["sudo", "install"]:
            if argv[-1] not in self.targets or not argv[-2].startswith(tempfile.gettempdir()):
                return se.Result(1, "", "an install named %s" % argv[-1])
            with open(argv[-2], encoding="utf-8") as fh:
                self.plists[argv[-1]] = fh.read()
            return se.Result(0)
        if argv[:3] == ["sudo", "launchctl", "print"]:                # as macOS prints it: the arguments, then the interval
            label = argv[3][len("system/"):]
            if label not in self.loaded:
                return se.Result(113, "", "Could not find service")
            doc = plistlib.loads(self.loaded[label].encode("utf-8"))
            shown = ["\targuments = {"] + ["\t\t" + a for a in doc["ProgramArguments"]] + [
                "\t}", "\trun interval = %d seconds" % doc["StartInterval"]] if self.args else []
            return se.Result(0, "\n".join(["system/%s = {" % label] + shown + ["}"]) + "\n")
        return se.Result(1, "", "nothing scripted for %s" % se._fmt(argv)[:100])


def selftest():
    fails, ran = [], [0]

    def check(name, got, want):
        ran[0] += 1
        print("[%s] %s%s" % ("PASS" if got == want else "FAIL", name, "" if got == want else "  (got %r, want %r)" % (got, want)))
        fails.extend([name] * (got != want))
    saved = dict(os.environ)
    for marker in AGENT_ENV_MARKERS:                # the battery owns its environment
        os.environ.pop(marker, None)
    try:
        with tempfile.TemporaryDirectory(prefix="queue-setup-selftest-") as tmp:
            _cases(check, tmp)
    except Exception as exc:                                      # noqa: BLE001
        check("the battery ran to its end", "%s: %s" % (type(exc).__name__, _s(str(exc))[:300]), "")
    finally:
        os.environ.clear()
        os.environ.update(saved)
    print("\n%d/%d checks passed" % (ran[0] - len(fails), ran[0]))
    return 1 if fails else 0


def _cases(check, tmp):
    import ast, contextlib, io  # noqa: E401
    me = sys.modules[__name__]
    o = dict((k, getattr(me, k)) for k in ("validate_conf", "compose", "render_plist", "runtime_closure"))
    key = "lin_" + "api_" + "QUEUESELFTESTNOTREAL0123456789abcdef"      # built at runtime: no fixture looks like a key
    token, person = "sk-ant-" + "oat01-" + "Q" * 40, "selftest-person"

    def quiet(fn):
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                value = fn()
        except Exception as exc:                                  # noqa: BLE001
            value = exc
        return value, out.getvalue()

    def text(**over):
        return "".join("%s=%s\n" % kv for kv in dict(BASE_CONF, **over).items() if kv[1] is not None)

    def parse(body):
        values, errs = parse_conf(body)
        conf, more = validate_conf(values, person)
        return conf, errs + more

    def refused(fragment, **over):
        return any(fragment in e for e in parse(text(**over))[1])

    def table(out):
        return dict(re.findall(r"^  (\S+) +(ALREADY-DONE|WOULD-CHANGE|DONE|FAILED|UNKNOWN|BLOCKED-ON-HUMAN) ", out, re.M))

    class NoSudo(object):
        asked = 0

        def acquire(self, why, resume):
            self.asked += 1

    def world(**over):
        conf, errs = parse(text(**over))
        assert not errs, errs
        return FakeMachine(tempfile.mkdtemp(dir=tmp), conf, key, token), conf

    def ctx(fake, conf, env=None, role=False, dry=False):
        fake.dry_run = dry
        c = Ctx(conf, fake, {} if env is None else env, NoSudo())
        c.checkout_scripts, c.role_home = fake.scripts, fake.home if role else None
        return c

    def case(name, thunk, want, *mutants):
        """Run the case, then each mutant against it: a check that cannot fail checks nothing."""
        def attempt():
            value = quiet(thunk)[0]
            return ("raised", type(value).__name__, str(value)[:160]) if isinstance(value, Exception) else value
        got = attempt()
        check(name, got, want)
        for mutant, patches in mutants:
            undo = []
            for dotted, value in patches.items():
                where, _dot, attr = dotted.rpartition(".")
                obj = {"se": se, "ns": ns}.get(where, me)
                undo.append((obj, attr, getattr(obj, attr)))
                setattr(obj, attr, value)
            try:
                other = attempt()
            finally:
                for obj, attr, value in reversed(undo):
                    setattr(obj, attr, value)
            check("mutant: %s is caught" % mutant, other != got, True)

    # the conf: every fault at once, by key, never by value; a credential refused by key; each rule alone
    bad = ("ROLE_ACCOUNT=zq9\nROLE_ACCOUNT=zq9\nTEAM_KEYS=ENG\nREVIEWS_TEAM_KEY=REV\nREPO_BY_TEAM=zq9\nUSAGE_JOB_LABEL=zq9\n"
           "MORNING_JOB_LABEL=zq9\nPLANNER_BIN=zq9\nLINEAR_KEY_ENV=zq9\nPLANNED_LOAD=zq9\nWINDOW_DAYS=zq9\nQUEUE_CONFIG=zq9\n"
           "NOT_A_KEY=1\nno equals zq9\nBACKLOG=zq9\n")
    named = ("duplicate ROLE_ACCOUNT", "OWNER_USER_IDS is required", "REPO_BY_TEAM item 1", "TEAM_KEYS lists a team",
             "USAGE_JOB_LABEL is not", "are one label", "PLANNER_BIN must be", "LINEAR_KEY_ENV must", "PLANNED_LOAD must",
             "WINDOW_DAYS item 1", "QUEUE_CONFIG must", "unknown key NOT_A_KEY", "not KEY=value", "BACKLOG must")
    secret = parse_conf(text(LINEAR_KEY_ENV=key))[1]
    case("conf: every fault is named in one pass, by key and never by value; a credential is never shown", lambda: (
        [n for n in named if not any(n in e for e in parse(bad)[1])], [e for e in parse(bad)[1] if "zq9" in e],
        len(secret), "CREDENTIAL SHAPE" in secret[0], key in " ".join(secret)), ([], [], 1, True, False),
         ("a validator that stops at the first fault", {"validate_conf": lambda v, u=None: (lambda c, e: (c, e[:1]))(
             *o["validate_conf"](v, u))}),
         ("an error that quotes the value it refused", {"validate_conf": lambda v, u=None: (lambda c, e: (c, e + [
             "bad " + v["PLANNER_BIN"]]))(*o["validate_conf"](v, u))}))
    case("conf: each rule refuses on its own", lambda: [
        refused("ROLE_ACCOUNT must be", ROLE_ACCOUNT="root"), refused("ROLE_ACCOUNT must be", ROLE_ACCOUNT=person),
        refused("ROLE_KIT_CLONE is not Stage E's", ROLE_KIT_CLONE="~/kit"),
        refused("PLANNER_BIN is required", PLANNER_BIN=None), not refused("PLANNER_BIN", PLANNER_BIN=None, PLANNER="no"),
        refused("QUEUE_CONFIG is inside ROLE_KIT_CLONE", QUEUE_CONFIG="~/.stage-e/kit/q.json"),
        refused("TEAM_KEYS lists a team", REPO_BY_TEAM="ENG=acme/app"),
        not refused("TEAM_KEYS lists a team", REPO_BY_TEAM="ENG=acme/app", REPO_BY_LABEL="repo:ops=acme/ops"),
        refused("repeats a weekday", WINDOW_DAYS="mon-fri 09:00 6, wed off")], [True] * 9)

    def example():
        with open(os.path.join(os.path.dirname(HERE), "queue.conf.example"), encoding="utf-8") as fh:
            values, errs = parse_conf(fh.read())
        more = validate_conf(values, person)[1]
        return errs, sorted(CONF_KEYS ^ set(values)), len(more), all("still the example" in e for e in more)
    case("conf: the committed example names every key, and only its placeholders are refused", example, ([], [], 3, True))

    # what it composes
    def composed():
        conf = parse(text(WINDOW_DAYS="mon-fri 08:30 7.5, sat 10:00 4"))[0]
        queue, usage = compose(conf)
        validate_composed(queue, usage, None)
        return (sorted(queue), queue["planner"] == {k: queue["order"][k] for k in ("lanes_per_repo", "planned_load")},
                usage["out"] == queue["morning"]["usage_file"] == conf["USAGE_FILE"],
                [d for d, v in queue["window"]["days"].items() if v], queue["window"]["days"]["sat"])
    case("compose: both configs pass the jobs' own loaders; two files share a value; a day not named has no window",
         composed, (["morning", "order", "planner", "pool", "reader", "window"], True, True,
                    ["mon", "tue", "wed", "thu", "fri", "sat"], {"start": "10:00", "hours": 4}),
         ("the usage job writes another file than the one the queue reads", {"compose": lambda conf: (lambda q, u: (q, dict(
             u, out="~/.stage-e/usage/other.json")))(*o["compose"](conf))}))

    def paced_conf(**over):
        conf, bad_ = parse(text(**over))
        return (bad_, compose(conf)[0]["order"]) if not bad_ else (bad_, None)
    check("pacing: off by default leaves the order's keys as they were; on adds the two, and the jobs' own loaders accept it",
          (paced_conf()[1].keys() == {"lanes_per_repo", "planned_load", "day_stop"},
           paced_conf(PACE_TO_RESET="yes", WEEKLY_BUDGET_USD="90")[1].get("weekly_budget_usd"),
           validate_composed(compose(parse(text(PACE_TO_RESET="yes", WEEKLY_BUDGET_USD="90"))[0])[0],
                             compose(parse(text())[0])[1], None)),
          (True, 90, validate_composed(compose(parse(text(PACE_TO_RESET="yes", WEEKLY_BUDGET_USD="90"))[0])[0],
                                       compose(parse(text())[0])[1], None)))
    check("pacing: a switch that is not yes or no, a budget that is not a number, and pacing with no budget are each refused by key",
          [[m for m in paced_conf(**kw)[0]] for kw in (dict(PACE_TO_RESET="maybe"), dict(WEEKLY_BUDGET_USD="lots"),
                                                      dict(PACE_TO_RESET="yes"))],
          [["PACE_TO_RESET must be yes or no"], ["WEEKLY_BUDGET_USD must be empty or a number above 0"],
           ["WEEKLY_BUDGET_USD is required with PACE_TO_RESET=yes"]])

    def sprint_conf(**over):
        conf, bad_ = parse(text(**over))
        return (bad_, compose(conf)[0]) if not bad_ else (bad_, None)
    ok_s = sprint_conf(WEEKLY_BUDGET_USD="90", SPRINT_PLAN_FILE="~/.stage-e/queue/sprint-plan.json")
    check("sprint planning (KIT-575): off by default adds nothing; on writes the file name and the budget, and the jobs' own loaders accept it",
          (sprint_conf()[1]["morning"].get("sprint_plan_file"), "weekly_budget_usd" in sprint_conf()[1]["order"],
           ok_s[0], ok_s[1]["morning"]["sprint_plan_file"], ok_s[1]["order"]["weekly_budget_usd"],
           ok_s[1]["order"].get("pace_to_reset"),
           validate_composed(ok_s[1], compose(parse(text())[0])[1], None)),
          (None, False, [], "~/.stage-e/queue/sprint-plan.json", 90, None,
           validate_composed(ok_s[1], compose(parse(text())[0])[1], None)))
    check("sprint planning: a plan file with no budget, and a file name that is not a path, are each refused by key",
          [sprint_conf(**kw)[0] for kw in (dict(SPRINT_PLAN_FILE="~/p.json"),
                                          dict(WEEKLY_BUDGET_USD="90", SPRINT_PLAN_FILE="bad name; echo hi"))],
          [["WEEKLY_BUDGET_USD is required with SPRINT_PLAN_FILE: a week cannot be sized without a budget"],
           ["SPRINT_PLAN_FILE must be a path, or empty"]])

    def loaders_refuse():
        fake, conf = world()
        queue, usage = compose(conf)
        a, b = (str(quiet(lambda: validate_composed(q, u, fake.home))[0]) for q, u in (
            (dict(queue, pool=dict(queue["pool"], owner_user_ids=[])), usage), (queue, dict(usage, reviews_team_key="x y"))))
        return "REFUSE" in a + b, "owner_user_ids" in a, "usage config" in b
    case("compose: a composition a job's own loader refuses is refused in its words", loaders_refuse, (True, True, True),
         ("a composition no loader looks at", {"validate_composed": lambda q, u, home: [json.dumps(q), json.dumps(u)]}))

    def config_writes():
        fake, conf = world()
        ok, again = step_config(ctx(fake, conf, role=True), True)[0], step_config(ctx(fake, conf, role=True), False)[0]
        return ok, again, [oct(os.stat(fake.home + "/.stage-e/" + p).st_mode & 0o777) for p in ("queue/queue.json",
                                                                                              "usage/usage-config.json")]
    case("config: written as the role account at mode 600, read back, and then current", config_writes,
         (False, True, ["0o600", "0o600"]), ("a config left readable by anyone", {"ns.CONFIG_WRITE_SH": ns.CONFIG_WRITE_SH.replace("chmod 600", "chmod 644")}))

    # the plists
    def plists():
        fake, conf = world()
        out = []
        for job in job_specs(conf, fake.home):
            doc = plistlib.loads(job["body"].encode("utf-8"))
            args = doc["ProgramArguments"]
            out.append((doc["Label"], doc["UserName"], doc["StartInterval"], args[:2], job["script"] in args[2],
                        'run --config "$HOME/.stage-e/' in args[2]))
        return out
    case("plists: each job's label, account, interval and command", plists, [
        ("com.example.queue-usage", "_exqueue", 900, ["/bin/sh", "-c"], True, True),
        ("com.example.queue-morning", "_exqueue", 600, ["/bin/sh", "-c"], True, True)],
         ("the morning job on the usage job's interval", {"render_plist": lambda conf, home, job: o["render_plist"](
             conf, home, dict(job, interval=900))}))

    def foreign():
        fake, conf = world()
        usage, early = job_specs(conf, fake.home)
        stranger = early["body"].replace(MORNING_SCRIPT, "pipeline_review_poller.py")
        fake.plists = {usage["plist"]: early["body"], early["plist"]: stranger}      # a swap, and a stranger
        before = dict(fake.plists)
        err = quiet(lambda: step_jobs(ctx(fake, conf, role=True), True))[0]
        return (isinstance(err, SetupError), "pipeline_review_poller.py" in str(err), "pipeline_morning.py" in str(err),
                fake.plists == before, [a for a in fake.sudo_argv if a[1] == "install"])
    case("plists: one already there for another job is never written over, and both are named", foreign,
         (True, True, True, True, []), ("a plist replaced whatever it was", {"foreign_plist": lambda body, script: None}))

    def installs():
        fake, conf = world()
        ok = step_jobs(ctx(fake, conf, role=True), True)[0]
        return (ok, sorted(fake.plists) == sorted(fake.targets), [a[2:8] for a in fake.sudo_argv if a[1] == "install"],
                step_jobs(ctx(fake, conf, role=True), False)[0], [a for a in fake.sudo_argv if "bootstrap" in a],
                fake._exec(["sudo", "install", "-m", "644", tmp + "/x.plist", "/Library/LaunchDaemons/other.plist"], None, 5).rc)
    case("jobs: installed root:wheel 644 at the exact path, current after, and never loaded", installs,
         (False, True, [["-o", "root", "-g", "wheel", "-m", "644"]] * 2, True, [], 1),
         ("a job loaded as it is installed", {"se._install_plist": lambda r, label, body: (
             r.as_root(["install", "-o", "root", "-g", "wheel", "-m", "644", "/tmp/x", se._dispatcher_plist(label)]),
             r.as_root(["launchctl", "bootstrap", "system", se._dispatcher_plist(label)], why="load"))}))

    # preflight, and the dry runs
    def preflight():
        fake, conf = world()
        fake.failing.add("pipeline_window.py")
        with open(fake.home + "/.stage-e/kit/scripts/pipeline_status_local.py", "a", encoding="utf-8") as fh:
            fh.write("# a line this checkout does not have\n")
        os.remove(fake.home + "/.stage-e/kit/scripts/pipeline_voice.py")
        _put(fake.home + "/.stage-e/env", "OTHER=1\n")
        msg = str(quiet(lambda: step_preflight(ctx(fake, conf), False))[0])
        return [f in msg for f in ("pipeline_window.py", "pipeline_status_local.py, pipeline_voice.py", "is mode 644",
                                   "no usable STAGE_E_LINEAR_API_KEY", "lacks CLAUDE_WRITER_OAUTH_TOKEN", "5 problem(s)")]
    case("preflight: a failing selftest, a drifted and a missing clone file, and a bad env file, all at once", preflight,
         [True] * 6, ("a clone and an env file nobody reads", {"role_problems": lambda ctx: ([], [])}))

    def dry(**exits):
        fake, conf = world()
        for script, rc in exits.items():
            _put(fake.home + "/.fake-exit-pipeline_%s.py" % script, "%d\n" % rc)
        c = ctx(fake, conf, role=True)
        quiet(lambda: (step_config(c, True), step_jobs(c, True)))
        value, out = quiet(lambda: step_dry_run(c, True))
        return "done" if isinstance(value, tuple) else "failed" if isinstance(value, SetupError) else repr(value), value, out

    def dry_runs():
        kind, _value, out = dry()
        leaked = dry(queue_read=1)
        return (re.findall(r"^    (\w+ \w+) +exit (\d)", out, re.M), kind, dry(usage_local=3)[0], leaked[0], dry(morning=2)[0],
                "queue reader: exit 1" in str(leaked[1]), "withheld here" in str(leaked[1]), key in str(leaked[1]) + leaked[2])
    case("dry run: each job once as the role account; exit 0 or 3 is done, any other fails; a leaked key is withheld",
         dry_runs, ([("usage job", "0"), ("queue reader", "0"), ("morning job", "0")], "done", "done", "failed", "failed",
                    True, True, False),
         ("a dry run that fails passes", {"DRY_OK": (0, 1, 2, 3)}),
         ("a last line printed as the job wrote it", {"scrub": lambda t, h=(): t}))

    # the bridge, and launchd
    def bridge(content):
        fake, conf = world()
        if content is not None:
            _put(fake.home + "/.stage-e/bridge.json", content.replace("HOME", fake.home))
        got = quiet(lambda: step_bridge(ctx(fake, conf, role=True), False))[0]
        return got.card_id if isinstance(got, Blocked) else "unknown" if isinstance(got, Unknown) else got[0]
    at = lambda path: json.dumps({"queue_config": path})                                                  # noqa: E731
    case("bridge: the card until the bridge's config names this queue config, in any spelling", lambda: [
        bridge(None), bridge("{}"), bridge(at("~/.stage-e/other.json")), bridge(at("~/.stage-e/queue/queue.json")),
        bridge(at("HOME/.stage-e/queue/../queue/queue.json")), bridge("{")], ["CK-Q1", "CK-Q1", "CK-Q1", True, True, "unknown"],
         ("any queue config, or none, passes", {"same_file": lambda a, b, home: True}))

    def enable(setup):
        fake, conf = world()
        usage, early = job_specs(conf, fake.home)
        fake.plists = {usage["plist"]: usage["body"], early["plist"]: early["body"]}
        setup(fake, early)
        got = quiet(lambda: step_enable(ctx(fake, conf, role=True), False))[0]
        return (got.card_id, [n for n in ("usage", "morning") if "queue-" + n in got.extra]) if isinstance(got, Blocked) \
            else "unknown" if isinstance(got, Unknown) else got[0]
    stale = lambda f, j: (f.load(), f.loaded.update({j["label"]: j["body"].replace(                    # noqa: E731
        "<integer>600</integer>", "<integer>601</integer>")}))
    case("enable: the card until launchd holds both, running this plist", lambda: [
        enable(lambda f, j: None), enable(lambda f, j: f.load(1)), enable(lambda f, j: f.load()), enable(stale),
        enable(lambda f, j: (f.load(), setattr(f, "args", False)))],
         [("CK-Q2", ["usage", "morning"]), ("CK-Q2", ["morning"]), True, ("CK-Q2", ["morning"]), "unknown"],
         ("a job that is not loaded passes", {"loaded_state": lambda ctx, job: ("same", "")}),
         ("a loaded job passes whatever it runs", {"ns.loaded_job": lambda t: {"arguments": [], "interval": None}}))

    # the whole command
    def flow():
        fake, conf = world()
        code1, out1 = quiet(lambda: cmd_run(ctx(fake, conf), False))
        _put(fake.home + "/.stage-e/bridge.json", at("~/.stage-e/queue/queue.json"))
        code2, out2 = quiet(lambda: cmd_run(ctx(fake, conf), False))
        fake.load()
        code3, out3 = quiet(lambda: cmd_run(ctx(fake, conf), False))
        return ((code1, table(out1), "QUEUE_CONFIG=~/.stage-e/queue/queue.json" in out1),
                (code2, table(out2).get("bridge"), table(out2).get("enable"), out2.count("sudo launchctl bootstrap system /Lib")),
                (code3, sorted(set(table(out3).values())), "NOT PROVEN" in out3 and "DONE — every step holds" in out3),
                [a for a in fake.sudo_argv if "bootstrap" in a], "${" in out1 + out2)
    case("run: through each gate, a card for each, and then every step holds", flow, (
        (10, {"preflight": "ALREADY-DONE", "config": "DONE", "jobs": "DONE", "dry-run": "DONE", "bridge": "BLOCKED-ON-HUMAN"}, True),
        (10, "ALREADY-DONE", "BLOCKED-ON-HUMAN", 2), (0, ["ALREADY-DONE", "DONE"], True), [], False))

    def dry_changes_nothing():
        fake, conf = world()

        def snap():
            return dict((os.path.join(b, f), open(os.path.join(b, f), "rb").read()) for b, _d, fs in os.walk(fake.root) for f in fs)
        before = snap()
        code, out = quiet(lambda: cmd_run(ctx(fake, conf, dry=True), True))
        return code, snap() == before, fake.writes, fake.plists, table(out).get("config")
    writes_anyway = tuple((s, (lambda c, a, f=f: f(c, True)) if s == "config" else f) for s, f in STEPS)
    case("run --dry-run: changes nothing, in the role home or in launchd", dry_changes_nothing,
         (10, True, [], {}, "WOULD-CHANGE"), ("a dry run whose config step writes", {"STEPS": writes_anyway}))

    # a model: refused, not measured, and no password
    def refusal():
        os.environ["CLAUDECODE"] = ""
        try:
            code, out = quiet(lambda: main(["run", "--conf", tmp + "/missing.conf"]))
        finally:
            os.environ.pop("CLAUDECODE", None)
        return code, "REFUSED" in out
    case("agent: `run` is refused before the conf is read", refusal, (EX_REFUSED, True),
         ("a refusal that does not refuse", {"refuse_if_agent": lambda action, env=None: None}))

    def measured():
        fake, conf = world()
        agent, person_ctx = ctx(fake, conf, env={"CLAUDECODE": ""}, dry=True), ctx(fake, conf)
        code, out = quiet(lambda: cmd_run(agent, True))
        acquire_privilege(person_ctx, True)
        acquire_privilege(agent, True)
        return (code, table(out).get("preflight"), "NOT MEASURED" in out, fake.role_scripts, fake.writes, fake.sudo_argv,
                (person_ctx.sudo.asked, agent.sudo.asked))
    case("agent: under a model every role-account row is NOT MEASURED, nothing is run or written, no password is asked",
         measured, (EX_UNKNOWN, "UNKNOWN", True, [], [], [], (1, 0)),
         ("a detector that finds no model", {"agent_markers": lambda env=None: []}))

    # Stage E, and what this file can never do
    with open(os.path.abspath(__file__), encoding="utf-8") as fh:
        source = fh.read()

    def registered():
        jobs = dict(se.SIBLING_JOBS)
        return [sorted(jobs.get(e, ())) == sorted(set(runtime_closure((e,))) - set(se.REQUIRED_SCRIPTS))
                for e in (USAGE_SCRIPT, MORNING_SCRIPT)], READER_SCRIPT in REQUIRED_SCRIPTS
    case("Stage E: SIBLING_JOBS lists what each job can load", registered, ([True, True], True),
         ("a closure that forgets a script", {"runtime_closure": lambda roots: (lambda c: tuple(
             x for x in c if x != [y for y in c if y not in se.REQUIRED_SCRIPTS][-1]))(o["runtime_closure"](roots))}))
    head = source.partition("\n# ── --selftest")[0]
    banned = ["pr " + "merge", "--app" + "rove", "--add-" + "label", "issue" + "Update", "state" + "Id", "muta" + "tion",
              "getpass", "os.environ["]
    loads = [n for n in ast.walk(ast.parse(head)) if isinstance(n, ast.List)
             and any(isinstance(e, ast.Constant) and e.value == "boot" + "strap" for e in n.elts)]
    check("source: it never merges, approves, labels or reads a credential, and no command loads a job",
          ([b for b in banned if b in head], loads), ([], []))


if __name__ == "__main__":
    sys.exit(main())
