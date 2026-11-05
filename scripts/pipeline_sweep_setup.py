#!/usr/bin/env python3
"""Sweep installer: the sweep set up on the role account with one command (KIT-467).

    python3 scripts/pipeline_sweep_setup.py run --dry-run    # read it first
    python3 scripts/pipeline_sweep_setup.py run              # the only command that changes anything

The queue installer's contract: at something only a person can do it prints a card and exits 10; do that and run it
again. Exits: 0 done, 1 failed, 2 conf, 3 refused under a model, 4 not measured, 5 no sudo. Beside a Stage E install it
writes the sweep's config (mode 600) as the role account and installs one LaunchDaemon plist. It NEVER loads it (CK-S1):
the sweep's first pass removes what finished runs left, so a person reads its dry run first. It never reads a credential
(names only) and never merges, approves or labels. Under a model `run` is REFUSED before the conf is read.

Its last row is the leftover check (docs/SWEEP.md): how much the role account's worktrees, Claude Code scratch, TMPDIR
and ~/.stage-e hold, against LEFTOVER_CEILING_GB. Past it, the row FAILS and names the largest folders; what could not
be read is said, never counted as empty.
"""
import argparse
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

from pipeline_dispatch_local import AGENT_ENV_MARKERS  # noqa: E402
from pipeline_stage_e_setup import (  # noqa: E402
    ALREADY_DONE, BLOCKED, DONE, EX_BLOCKED, EX_FAILED, EX_NOPRIV, EX_OK, EX_REFUSED, EX_UNKNOWN, EX_USAGE, FAILED,
    UNKNOWN, WOULD_CHANGE, Blocked, NoPrivilege, Refusal, SetupError, Unknown, say, split_list)
import pipeline_stage_e_setup as se  # noqa: E402
import pipeline_notifier_setup as ns  # noqa: E402
import pipeline_queue_setup as qs  # noqa: E402  (its context, role-file reader and launchd probe are not its alone)
import pipeline_sweep as sweep  # noqa: E402  (the sweep's own loader judges what this file composes)

credential_shape, scrub = ns.credential_shape, ns.scrub
_s = lambda text: scrub(text, ())      # noqa: E731  (everything printed passes through the notifier's scrub)
DEFAULT_CONF = "sweep.conf"
SWEEP_SCRIPT = "pipeline_sweep.py"
REQUIRED_SCRIPTS = (SWEEP_SCRIPT, "scratch_run.py")
SELFTESTS = (SWEEP_SCRIPT,)
DRY_OK = (0, 4)                  # the sweep's own: 0 ran, 4 ran and left something it could not judge

CONF_REQUIRED = ("ROLE_ACCOUNT", "TEAM_KEYS", "SWEEP_WORKSPACE_DIR", "SWEEP_DISPATCHER_HOME", "SWEEP_JOB_LABEL")
CONF_DEFAULTS = {
    "ROLE_KIT_CLONE": se.ROLE_KIT_CLONE_PATH, "ENV_FILE": "~/.stage-e/env", "LINEAR_KEY_ENV": "STAGE_E_LINEAR_API_KEY",
    "SWEEP_CONFIG": "~/.stage-e/sweep/sweep.json", "SWEEP_INTERVAL_SECONDS": "3600", "SWEEP_DONE_DAYS": "3",
    "SWEEP_IDLE_DAYS": "3", "SWEEP_LOG_MAX_MB": "10", "SWEEP_LOG_KEEP": "3", "LEFTOVER_CEILING_GB": "5",
    "SWEEP_LOGS": "~/.stage-e/*.log, ~/.stage-e/*/*.log, ~/.stage-a/*.log, ~/.stage-a/*/*.log",
}
CONF_KEYS = set(CONF_REQUIRED) | set(CONF_DEFAULTS)
# Left invalid on purpose, so an unedited copy of the example cannot run.
CONF_UNEDITED = {"SWEEP_WORKSPACE_DIR": "/opt/example-dispatcher/worktrees", "SWEEP_DISPATCHER_HOME": "/opt/example-dispatcher"}
PATH_KEYS = ("ROLE_KIT_CLONE", "ENV_FILE", "SWEEP_CONFIG")
RANGES = (("SWEEP_INTERVAL_SECONDS", 300, 86400), ("SWEEP_DONE_DAYS", 1, 365), ("SWEEP_IDLE_DAYS", 1, 365),
          ("SWEEP_LOG_MAX_MB", 1, 10240), ("SWEEP_LOG_KEEP", 1, 20), ("LEFTOVER_CEILING_GB", 1, 1000))


def whole(text):
    return int(text) if re.match(r"^[0-9]+$", text or "") else None


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
    """(conf, errors): every fault of the conf's own in ONE pass, by key; the sweep's own loader judges the rest."""
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
    label = c.get("SWEEP_JOB_LABEL") or ""
    need(not label or se._RDNS_RE.match(label), "SWEEP_JOB_LABEL is not a reverse-DNS launchd label")
    need(all(re.match(r"^[A-Z][A-Z0-9]*$", t) for t in split_list(c.get("TEAM_KEYS"))) and split_list(c.get("TEAM_KEYS")),
         "TEAM_KEYS must list team KEYS, e.g. ENG,OPS")
    path_ok = lambda v: bool(ns._PATH_RE.match(v)) and "/../" not in v + "/" and v.rstrip("/") not in ("", "~")   # noqa: E731
    for k in ("SWEEP_WORKSPACE_DIR", "SWEEP_DISPATCHER_HOME"):
        need(not c.get(k) or c[k].startswith("/") and path_ok(c[k]), "%s must be an absolute path with no `..`" % k)
    need(c.get("SWEEP_WORKSPACE_DIR") != c.get("SWEEP_DISPATCHER_HOME") or not c.get("SWEEP_WORKSPACE_DIR"),
         "SWEEP_WORKSPACE_DIR and SWEEP_DISPATCHER_HOME are one folder")
    need(ns._UPPER_SNAKE.match(c.get("LINEAR_KEY_ENV") or ""), "LINEAR_KEY_ENV must be the NAME of an environment variable")
    bad = [k for k in PATH_KEYS if not path_ok(c[k])]
    e.extend("%s must be `~/…` (the role account's home) or an absolute path, with no `..`" % k for k in bad)
    for k, low, high in RANGES:
        need(low <= (whole(c.get(k)) or 0) <= high, "%s must be a whole number from %d to %d" % (k, low, high))
    need("ROLE_KIT_CLONE" in bad or c["ROLE_KIT_CLONE"].rstrip("/") == se.ROLE_KIT_CLONE_PATH,
         "ROLE_KIT_CLONE is not Stage E's clone, %s" % se.ROLE_KIT_CLONE_PATH)
    need("SWEEP_CONFIG" in bad or not ns._under(ns.resolve_path(c["SWEEP_CONFIG"], ns.ROLE_HOME_SENTINEL),
                                                ns.resolve_path(c["ROLE_KIT_CLONE"], ns.ROLE_HOME_SENTINEL)),
         "SWEEP_CONFIG is inside ROLE_KIT_CLONE, a git working tree")
    return c, e


def load_conf(path, user=None):
    try:
        with open(path, encoding="utf-8") as fh:
            values, errors = parse_conf(fh.read(), os.path.basename(path))
    except OSError as exc:
        raise SetupError("could not read %s (%s): cp sweep.conf.example sweep.conf && chmod 600 sweep.conf"
                         % (path, exc.strerror))
    conf, more = validate_conf(values, user)
    return conf, errors + more


# ── what it composes: one document, judged by the sweep's own loader ──────────────────────────────────────────
def compose(c, home):
    """The sweep's config, `~` made absolute under the role's home. Raises SetupError in the sweep's own words."""
    doc = {"account": c["ROLE_ACCOUNT"], "workspace_dir": c["SWEEP_WORKSPACE_DIR"].rstrip("/"),
           "dispatcher_home": c["SWEEP_DISPATCHER_HOME"].rstrip("/"), "teams": split_list(c["TEAM_KEYS"]),
           "linear_key_env": c["LINEAR_KEY_ENV"], "done_days": whole(c["SWEEP_DONE_DAYS"]),
           "idle_days": whole(c["SWEEP_IDLE_DAYS"])}
    if "logs" in sweep.CONFIG_KEYS:          # a sweep from before KIT-465 cannot trim a log, and refuses the keys
        doc.update(logs=qs.with_home(split_list(c["SWEEP_LOGS"]), home), log_max_mb=whole(c["SWEEP_LOG_MAX_MB"]),
                   log_keep=whole(c["SWEEP_LOG_KEEP"]))
    body = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    with tempfile.TemporaryDirectory(prefix="sweep-setup-judge-") as tmp:
        path = os.path.join(tmp, "sweep.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        try:
            sweep.load_config(path)
        except sweep.SweepError as exc:
            raise SetupError("REFUSED by the sweep's own loader: %s" % exc)
    return body


def sweep_command(c):
    script = c["ROLE_KIT_CLONE"].rstrip("/") + "/scripts/" + SWEEP_SCRIPT
    return ns.exec_prefix(c) + "%s run --config %s" % (ns._q(script), ns._q(c["SWEEP_CONFIG"]))


def job_spec(c, home):
    """The one job, with its plist's body. Stage E's own PLIST, filled in; launchd expands nothing."""
    cfg = ns.resolve_path(c["SWEEP_CONFIG"].rstrip("/"), home)
    job = {"name": "the sweep", "label": c["SWEEP_JOB_LABEL"], "plist": se._dispatcher_plist(c["SWEEP_JOB_LABEL"]),
           "script": SWEEP_SCRIPT, "interval": int(c["SWEEP_INTERVAL_SECONDS"]), "command": sweep_command(c),
           "log": os.path.dirname(cfg) + "/sweep.log"}
    esc = _xml_escape
    return dict(job, body=se.PLIST.format(label=esc(job["label"]), account=esc(c["ROLE_ACCOUNT"]), home=esc(home),
                                          path=esc(se.DAEMON_PATH), command=esc(job["command"]),
                                          interval=job["interval"], log=esc(job["log"])))


# ── cards, and the agent policy ───────────────────────────────────────────────────────────────────────────────
CARDS = {
    "CK-S1": {
        "title": "Read the sweep's dry run, then load the job",
        "why": ("Loading starts the sweep. Its first pass removes what finished runs left on the role account: the worktree "
                "and the dispatcher's folder of every ticket done or deleted for ${SWEEP_DONE_DAYS} days or more, idle "
                "Claude Code scratch and kit temp folders, and it trims the job logs. The dry run above says what that is. "
                "Nothing is removed that the dry run did not list."),
        "do": ["1. Read the dry run's lines above. Anything listed that you want to keep: stop here and change the conf.",
               "2. Enable, then load:",
               "       sudo launchctl enable system/${SWEEP_JOB_LABEL} && sudo launchctl bootstrap system ${SWEEP_PLIST}",
               "   If the plist changed after it was loaded, `sudo launchctl bootout system/${SWEEP_JOB_LABEL}` first.",
               "3. Run this installer again:  python3 ${SELF} run${FLAGS}"],
        "good": "`launchctl print` shows the job running its plist's command, and `run` exits 0"},
}


def _self_path():
    return os.path.relpath(os.path.abspath(__file__))


def print_card(cid, c, flags=""):
    card = CARDS[cid]
    values = dict(c, SELF=_self_path(), FLAGS=flags, SWEEP_PLIST=se._dispatcher_plist(c["SWEEP_JOB_LABEL"]))
    say("\n" + "=" * 74 + "\n %s — %s\n" % (cid, card["title"]) + "=" * 74 + "\n\nWHY IT IS YOURS")
    for line in se._wrap(ns._fill(card["why"], values)):
        say("  " + line)
    say("\nWHAT TO DO")
    for line in card["do"]:
        say("  " + ns._fill(line, values))
    say("\nGOOD: %s\nNEVER: merge or approve a PR, apply a protected label, or reword a blocked command.\n"
        % ns._fill(card["good"], values))


def refuse_if_agent(action, env=None):
    found = qs.agent_markers(env)
    if found:
        raise Refusal("REFUSED: %s changes things and this is an agent environment (%s set). A session that installs its "
                      "own supervision is what this refusal prevents; there is no override.\n"
                      "  A PERSON runs this, in a terminal:  python3 %s %s\n  Read-only meanwhile:  run --dry-run"
                      % (action, ", ".join(found), _self_path(), action))


def acquire_privilege(ctx, dry_run):
    if ctx.agent:
        return False
    try:
        ctx.sudo.acquire("`run%s` works as the %s role account and asks launchd about one job. It loads none." % (
            " --dry-run" if dry_run else "", ctx.account), "")
    except NoPrivilege:
        raise NoPrivilege("NO ADMINISTRATOR ACCESS — nothing was attempted or changed. Fix that and run again:  "
                          "python3 %s run%s%s" % (_self_path(), " --dry-run" if dry_run else "", ctx.flags))
    return True


# ── steps: each returns (ok, detail, notes) or raises Blocked / Unknown / SetupError ──────────────────────────
def step_preflight(ctx, apply_it):
    r, c, problems, unmeasured = ctx.runner, ctx.conf, [], []
    home = r.read(["dscl", ".", "-read", "/Users/" + ctx.account, "NFSHomeDirectory"])
    value = (home.out.split("NFSHomeDirectory:", 1) + [""])[1].strip().split("\n")[0].strip() if home.ok else ""
    if value.startswith("/") and value != "/" and ns._PATH_RE.match(value):
        ctx.role_home = value
    else:
        problems.append("the role account has no home (dscl): install Stage E first")
    if [n for n in SELFTESTS if not r.read([sys.executable, os.path.join(HERE, n), "--selftest"], timeout=600).ok]:
        problems.append("selftest FAILED from this checkout: %s" % ", ".join(SELFTESTS))
    if ctx.agent:
        unmeasured.append("the role account's clone and env file (they need `sudo -u %s`)" % ctx.account)
    else:
        clone, env = c["ROLE_KIT_CLONE"].rstrip("/"), c["ENV_FILE"]
        res = ctx.as_role(ns.CLONE_HASH_SH.replace("@DIR@", ns._q(clone + "/scripts")).replace("@FILES@", " ".join(REQUIRED_SCRIPTS)),
                          what="hashing the clone's scripts")
        if res.ok:
            theirs = ns.parse_clone_hashes(res.out)
            off = [n for n in REQUIRED_SCRIPTS
                   if theirs.get(n, ("",))[0] != "sha256" or theirs[n][1] != qs.checkout_hash(ctx.checkout_scripts, n)]
            if off:
                problems.append("the clone at %s is not level with this checkout (%s): merge the change, then let Stage E's "
                                "`run` move it" % (clone, ", ".join(off)))
        else:
            unmeasured.append("the clone could not be read as %s (exit %d)" % (ctx.account, res.rc))
        res = ctx.as_role(ns._cred_script(ns.CRED_PROBE_SH, dict(
            c, CHAT_TOKEN_ENV="SWEEP_SETUP_UNUSED", WRITER="no", WRITER_TOKEN_ENV="SWEEP_SETUP_UNUSED")),
            what="reading %s by name" % env)
        facts = ns.parse_cred_probe(res.out)
        if res.rc == 9:
            problems.append("%s does not exist for %s: Stage E writes it" % (env, ctx.account))
        elif not facts:
            unmeasured.append("%s could not be read as %s" % (env, ctx.account))
        elif facts["mode"] != "600" or facts["key"] != "ok":
            problems.append("%s is mode %s, or has no usable %s (Stage E writes it)" % (env, facts["mode"], c["LINEAR_KEY_ENV"]))
    if problems:
        raise SetupError("preflight found %d problem(s), all of them:\n  - %s" % (len(problems), "\n  - ".join(problems)))
    if unmeasured:
        raise Unknown("NOT MEASURED: " + "; ".join(unmeasured), "run the same command in your own terminal")
    return True, "the role's home, clone (%d files, level with this checkout) and env file, and the sweep's selftest, pass" % len(
        REQUIRED_SCRIPTS), []


def step_config(ctx, apply_it):
    c = ctx.conf
    if not ctx.role_home:
        raise Unknown("waits on preflight: the role account's home is not known", "clear the preflight row first")
    body = compose(c, ctx.role_home)
    have, mode = qs.read_role_file(ctx, c["SWEEP_CONFIG"])
    if have == body and mode == "600":
        ctx.config_current = True
        return True, "the config is current, mode 600, and accepted by the sweep's own loader", []
    why = "absent" if have is None else "differs" if have != body else "mode " + mode
    if not apply_it:
        return False, "would write %s (%s); the sweep's own loader accepts it" % (c["SWEEP_CONFIG"], why), []
    res = ctx.as_role(ns.CONFIG_WRITE_SH.replace("@F@", ns._q(c["SWEEP_CONFIG"])), what="writing " + c["SWEEP_CONFIG"],
                      stdin=body, why="write %s (mode 600)" % c["SWEEP_CONFIG"])
    have, mode = qs.read_role_file(ctx, c["SWEEP_CONFIG"]) if res.ok else (None, "")
    if have != body or mode != "600":
        raise SetupError("could not write %s as %s, or reading it back does not match (exit %d, mode %s)"
                         % (c["SWEEP_CONFIG"], ctx.account, res.rc, mode))
    ctx.config_current = True
    return False, "wrote %s, mode 600" % c["SWEEP_CONFIG"], []


def step_job(ctx, apply_it):
    if not ctx.role_home:
        raise Unknown("waits on preflight: the role account's home is not known", "clear the preflight row first")
    r, job = ctx.runner, job_spec(ctx.conf, ctx.role_home)
    have = None
    if r.read(["test", "-e", job["plist"]]).ok:
        got = r.read(["cat", job["plist"]])
        if not got.ok:
            raise Unknown("%s exists and could not be read" % job["plist"], "fix its permissions (root:wheel 644)")
        have = got.out
    foreign = qs.foreign_plist(have, job["script"]) if have != job["body"] else None
    if foreign:
        raise SetupError("%s is NOT the sweep: %s\n  Replacing it would destroy that job, with no backup. Pick a label "
                         "nothing else uses." % (job["plist"], foreign))
    if have == job["body"]:
        ctx.job_current = True
        return True, "the plist is installed and current (never loaded here: CK-S1)", []
    what = "absent" if have is None else "differs"
    if not apply_it:
        return False, "would install %s (%s), not loaded" % (job["plist"], what), []
    if ctx.agent:
        raise ctx.not_measured("installing %s" % job["plist"])
    se._install_plist(r, job["label"], job["body"])
    if r.read(["cat", job["plist"]]).out != job["body"]:
        raise SetupError("%s was installed, and reading it back does not match" % job["plist"])
    ctx.job_current = True
    return False, "installed %s, NOT loaded (CK-S1)" % job["plist"], []


def step_dry_run(ctx, apply_it):
    """The sweep once as the role account, through its own shell command plus --dry-run. It removes nothing, and its
    lines are the list the person reads at CK-S1."""
    if not (ctx.config_current and ctx.job_current):
        waits = " and ".join(n for n, ok in (("config", ctx.config_current), ("job", ctx.job_current)) if not ok)
        if apply_it:
            raise Unknown("waits on %s" % waits, "clear those rows first")
        return False, "waits on %s: a dry run runs the sweep against the config it reads" % waits, []
    if not apply_it:
        return False, "would run the sweep's dry run once as %s" % ctx.account, []
    res = ctx.as_role(sweep_command(ctx.conf) + " --dry-run", what="running the sweep's dry run", timeout=1800)
    lines = [_s(l) for l in ((res.out or "") + "\n" + (res.err or "")).splitlines() if l.strip()]
    say("\n  -- the sweep's dry run, as %s --" % ctx.account)
    for line in lines[-60:]:
        say("    " + line[:200])
    if res.rc not in DRY_OK:
        raise SetupError("the sweep's dry run FAILED as %s (exit %d): %s" % (
            ctx.account, res.rc, ns._last_line(_s((res.out or "") + "\n" + (res.err or "")))[:150]))
    return False, "the sweep's dry run passed as %s (exit %d); its lines are above" % (ctx.account, res.rc), [
        "Exit 4 means: ran, and something could not be judged. Its lines, above, say what."]


def step_enable(ctx, apply_it):
    """Loaded, and running THIS plist: a plist on disk is not what launchd holds."""
    if not ctx.role_home:
        raise Unknown("waits on preflight: the role account's home is not known", "clear the preflight row first")
    job = job_spec(ctx.conf, ctx.role_home)
    state, why = qs.loaded_state(ctx, job)
    ctx.loaded = [] if state == "absent" else [job["label"]]
    if state in ("absent", "different"):
        raise Blocked("CK-S1", "system/%s is %s" % (job["label"], "not loaded (a job paused with `disable` reads the same)"
                                                    if state == "absent" else "loaded and running %s: unload it, then load it" % why))
    if state == "unclear":
        raise Unknown("system/%s is loaded, and launchd's print named neither its arguments nor its interval: whether it "
                      "runs THIS plist is not measured" % job["label"], "compare it with its plist:  sudo launchctl print system/" + job["label"])
    return True, "loaded, running its plist's own command: system/%s every %s s" % (job["label"], job["interval"]), []


# ── the leftover row: how much the role account's runs have left, against a line ──────────────────────────────
def area_script(path):
    return ('p=%s; [ -d "$p" ] || { echo ABSENT; exit 0; }; du -k -d 1 "$p" 2>&1' % ns._q(path))


def measure_leftovers(ctx):
    """([(label, path, kb or None, [(kb, child)])], unread): `du` of each area as the role account. A line du could not
    produce, or an area it failed on, is `unread`: never counted as empty."""
    c, home = ctx.conf, ctx.role_home
    out, unread = [], []
    res = ctx.as_role("id -u", what="reading the role account's id")
    uid = (res.out or "").strip()
    scratch = ("Claude Code scratch", "/tmp/claude-" + uid) if res.ok and uid.isdigit() else None
    if not scratch:
        unread.append("Claude Code scratch could not be located: the role account's id was not read")
    res = ctx.as_role('getconf DARWIN_USER_TEMP_DIR 2>/dev/null || { [ -n "$TMPDIR" ] && echo "$TMPDIR"; }',
                      what="asking for the role account's TMPDIR")
    tmp = (res.out or "").strip().rstrip("/")
    tmpdir = ("TMPDIR", tmp) if res.ok and tmp.startswith("/") and tmp != "/tmp" else None
    if not tmpdir:
        unread.append("TMPDIR could not be located: the role account's temp folder was not read")
    areas = [a for a in (("dispatcher worktrees", c["SWEEP_WORKSPACE_DIR"].rstrip("/")), scratch, tmpdir,
                         ("~/.stage-e", ns.resolve_path("~/.stage-e", home))) if a]
    for label, path in areas:
        res = ctx.as_role(area_script(path), what="measuring " + label)
        text = (res.out or "").strip()
        if text == "ABSENT":
            out.append((label, path, 0, []))
            continue
        total, kids, bad = None, [], 0
        for line in text.splitlines():
            m = re.match(r"^([0-9]+)\s+(.*)$", line)
            if not m:
                bad += 1
            elif m.group(2).rstrip("/") == path.rstrip("/"):
                total = int(m.group(1))
            else:
                kids.append((int(m.group(1)), m.group(2)))
        if total is None:
            unread.append("%s (%s) could not be measured" % (label, path))
        elif bad or not res.ok:
            unread.append("%s (%s) was only partly readable: at least %.1f GB" % (label, path, total / 1048576.0))
        out.append((label, path, total, sorted(kids, reverse=True)[:3]))
    return out, unread


def ceiling_kb(conf):
    """The line in KB, as du counts."""
    return whole(conf["LEFTOVER_CEILING_GB"]) * 1048576


def step_leftover(ctx, apply_it):
    if not ctx.role_home:
        raise Unknown("waits on preflight: the role account's home is not known", "clear the preflight row first")
    line = ceiling_kb(ctx.conf)
    areas, unread = measure_leftovers(ctx)
    total = sum(kb or 0 for _l, _p, kb, _k in areas)
    gb = total / 1048576.0
    top = sorted((kb, path) for _l, _p, _kb, kids in areas for kb, path in kids)[::-1][:3]
    if total > line:
        raise SetupError("the role account's runs have left %s%.1f GB, past the %s GB line (LEFTOVER_CEILING_GB). Largest: %s. "
                         "The sweep removes finished runs' folders once loaded (CK-S1); a folder it lists as kept is a run "
                         "still open or too young." % ("" if not unread else "at least ", gb, ctx.conf["LEFTOVER_CEILING_GB"],
                                                      ", ".join("%s (%.1f GB)" % (p, kb / 1048576.0) for kb, p in top)))
    if unread:
        raise Unknown("NOT MEASURED WHOLE: " + "; ".join(unread), "run the same command as a person who can read them")
    return True, "%.2f GB left behind in %d areas, under the %s GB line" % (gb, len(areas), ctx.conf["LEFTOVER_CEILING_GB"]), []


STEPS = (("preflight", step_preflight), ("config", step_config), ("job", step_job), ("dry-run", step_dry_run),
         ("enable", step_enable), ("leftover", step_leftover))
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
    say("Sweep installer — %s, role account %s (the job is never loaded here)%s" % (
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
    p = argparse.ArgumentParser(prog="pipeline_sweep_setup.py", description="Install the sweep on the role account.")
    p.add_argument("command", nargs="?", default="run", choices=["run"])
    p.add_argument("--conf", default=DEFAULT_CONF)
    p.add_argument("--dry-run", action="store_true", help="measure every step and change nothing")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)
    if args.selftest:
        from scratch_run import contained   # KIT-450: its temp folders go when it ends
        return contained(selftest, "sweep-setup-selftest-run-")
    try:
        if not args.dry_run:        # BEFORE the conf is read: a session must not discover what it would have installed
            refuse_if_agent("run")
        conf, errors = load_conf(args.conf, ns.invoking_user())
        if errors:
            say("Your conf has %d problem(s), all of them:\n  - %s\n\nFix them, then run the same command again."
                % (len(errors), "\n  - ".join(errors)))
            return EX_USAGE
        ctx = qs.Ctx(conf, se.Runner(dry_run=args.dry_run))
        ctx.job_current = False
        ctx.flags = " --conf " + shlex.quote(args.conf) if args.conf != DEFAULT_CONF else ""
        acquire_privilege(ctx, args.dry_run)
        return cmd_run(ctx, args.dry_run)
    except (Refusal, NoPrivilege, SetupError) as exc:
        say(("FAILED: " if isinstance(exc, SetupError) else "") + str(exc))
        return EX_REFUSED if isinstance(exc, Refusal) else EX_NOPRIV if isinstance(exc, NoPrivilege) else EX_USAGE


# ── --selftest: offline; every command stood in for, a stand-in that writes names its path, a mutant for each rule ──
FAKE_SWEEP = (
    "import os, sys\n"
    "flag = os.path.join(os.environ['HOME'], '.fake-exit-sweep')\n"
    "code = int(open(flag).read()) if os.path.exists(flag) else 0\n"
    "key = os.environ.get('STAGE_E_LINEAR_API_KEY', '')\n"
    "print('sweep: would remove 2 (0.1 MB), trimmed 1 logs, kept 3' if '--dry-run' in sys.argv else 'sweep: REAL PASS')\n"
    "print('sweep: %s' % ('done' if code == 0 else 'failed: Authorization: Bearer ' + key))\n"
    "sys.exit(code)\n")
BASE_CONF = {"ROLE_ACCOUNT": "_exsweep", "TEAM_KEYS": "ENG,OPS", "SWEEP_WORKSPACE_DIR": "@ROOT@/dispatcher/worktrees",
             "SWEEP_DISPATCHER_HOME": "@ROOT@/dispatcher", "SWEEP_JOB_LABEL": "com.example.sweep"}


def _put(path, body, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(path, mode)


class FakeMachine(se.Runner):
    """A role home in a temporary folder: a clone level with this checkout, an env file, a stand-in for the sweep.
    `sudo -u <role> -H /bin/sh -c …` runs through a REAL /bin/sh with HOME there; the rest is scripted."""

    def __init__(self, root, conf, key):
        se.Runner.__init__(self)
        self.root, self.home, self.scripts, self.plists, self.loaded = root, root + "/role-home", root + "/checkout", {}, {}
        self.target = se._dispatcher_plist(conf["SWEEP_JOB_LABEL"])
        self.failing, self.sudo_argv, self.role_scripts, self.args = set(), [], [], True
        for where in (self.home + "/.stage-e/kit/scripts", self.scripts):
            for name in REQUIRED_SCRIPTS:
                _put(where + "/" + name, FAKE_SWEEP if name == SWEEP_SCRIPT else "# fake\n")
        _put(self.home + "/.stage-e/env", "STAGE_E_LINEAR_API_KEY=%s\n" % key, 0o600)
        # `getconf DARWIN_USER_TEMP_DIR` would name the machine's real temp folder: a stand-in names the fake's own.
        _put(self.root + "/bin/getconf", "#!/bin/sh\n[ -f \"$HOME/.fail-getconf\" ] && exit 1\necho %s\n"
             % (self.root + "/role-tmp"), 0o755)
        os.makedirs(self.root + "/role-tmp")
        # A uid no machine has a Claude Code folder for: the selftest never reads the real /tmp/claude-<uid>.
        _put(self.root + "/bin/id", "#!/bin/sh\n[ -f \"$HOME/.fail-id\" ] && exit 1\necho 4242\n", 0o755)

    def load(self):
        self.loaded[os.path.basename(self.target)[:-len(".plist")]] = self.plists[self.target]

    def _exec(self, argv, stdin, timeout, cwd=None, **kwargs):
        argv = list(argv)
        if argv[0] == "sudo":
            self.sudo_argv.append(argv)
        if argv[:2] == ["sudo", "-u"] and argv[3:6] == ["-H", "/bin/sh", "-c"]:
            outside = [p for p in re.findall(r'"(/[^"]*)"', argv[6]) if not p.startswith((self.root, "/tmp/claude-"))]
            if outside:
                return se.Result(99, "", "a role script names a path outside its home: %s" % outside[0])
            self.role_scripts.append(argv[6])
            env = {"HOME": self.home, "PATH": self.root + "/bin:/usr/bin:/bin", "TMPDIR": self.root + "/role-tmp"}
            if os.path.exists(self.home + "/.fail-getconf"):      # neither getconf nor $TMPDIR answers
                del env["TMPDIR"]
            done = subprocess.run(["/bin/sh", "-c", argv[6].replace("/usr/bin/python3", sys.executable)], input=stdin,
                                  capture_output=True, text=True, timeout=timeout, env=env)
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
            if argv[-1] != self.target or not argv[-2].startswith(tempfile.gettempdir()):
                return se.Result(1, "", "an install named %s" % argv[-1])
            with open(argv[-2], encoding="utf-8") as fh:
                self.plists[argv[-1]] = fh.read()
            return se.Result(0)
        if argv[:3] == ["sudo", "launchctl", "print"]:
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
        with tempfile.TemporaryDirectory(prefix="sweep-setup-selftest-") as tmp:
            _cases(check, tmp)
    except Exception as exc:                                      # noqa: BLE001
        check("the battery ran to its end", "%s: %s" % (type(exc).__name__, _s(str(exc))[:300]), "")
    finally:
        os.environ.clear()
        os.environ.update(saved)
    print("\n%d/%d checks passed" % (ran[0] - len(fails), ran[0]))
    return 1 if fails else 0


def _cases(check, tmp):
    import contextlib
    import io
    me = sys.modules[__name__]
    key = "lin_" + "api_" + "SWEEPSELFTESTNOTREAL0123456789abcdef"      # built at runtime: no fixture looks like a key
    person = "selftest-person"

    def quiet(fn):
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                value = fn()
        except Exception as exc:                                  # noqa: BLE001
            value = exc
        return value, out.getvalue()

    def text(root="/r", **over):
        return "".join("%s=%s\n" % (k, v.replace("@ROOT@", root)) for k, v in dict(BASE_CONF, **over).items() if v is not None)

    def parse(body):
        values, errs = parse_conf(body)
        conf, more = validate_conf(values, person)
        return conf, errs + more

    def refused(fragment, **over):
        return any(fragment in e for e in parse(text(**over))[1])

    class NoSudo(object):
        def acquire(self, why, resume):
            pass

    def world(**over):
        root = os.path.realpath(tempfile.mkdtemp(dir=tmp))          # /var is /private/var on macOS: one spelling
        conf, errs = parse(text(root, **over))
        assert not errs, errs
        return FakeMachine(root, conf, key), conf

    def ctx(fake, conf, env=None, role=False, dry=False):
        fake.dry_run = dry
        c = qs.Ctx(conf, fake, {} if env is None else env, NoSudo())
        c.checkout_scripts, c.role_home, c.job_current = fake.scripts, fake.home if role else None, False
        return c

    def table(out):
        return dict(re.findall(r"^  (\S+) +(ALREADY-DONE|WOULD-CHANGE|DONE|FAILED|UNKNOWN|BLOCKED-ON-HUMAN) ", out, re.M))

    # the conf
    bad = ("ROLE_ACCOUNT=zq9\nROLE_ACCOUNT=zq9\nTEAM_KEYS=eng\nSWEEP_JOB_LABEL=zq9\nSWEEP_WORKSPACE_DIR=zq9\n"
           "SWEEP_DONE_DAYS=zq9\nSWEEP_LOG_KEEP=99\nLINEAR_KEY_ENV=zq9\nSWEEP_CONFIG=zq9\nNOT_A_KEY=1\nno equals zq9\n")
    named = ("duplicate ROLE_ACCOUNT", "SWEEP_DISPATCHER_HOME is required", "TEAM_KEYS must", "SWEEP_JOB_LABEL is not",
             "SWEEP_WORKSPACE_DIR must", "SWEEP_DONE_DAYS must", "SWEEP_LOG_KEEP must", "LINEAR_KEY_ENV must",
             "SWEEP_CONFIG must", "unknown key NOT_A_KEY", "not KEY=value")
    secret = parse_conf(text(LINEAR_KEY_ENV=key))[1]
    got = parse(bad)[1]
    check("conf: every fault is named in one pass, by key and never by value; a credential is never shown",
          ([n for n in named if not any(n in e for e in got)], [e for e in got if "zq9" in e], len(secret),
           "CREDENTIAL SHAPE" in secret[0], key in " ".join(secret)), ([], [], 1, True, False))
    check("conf: each rule refuses on its own", [
        refused("ROLE_ACCOUNT must be", ROLE_ACCOUNT="root"), refused("ROLE_ACCOUNT must be", ROLE_ACCOUNT=person),
        refused("ROLE_KIT_CLONE is not Stage E's", ROLE_KIT_CLONE="~/kit"),
        refused("SWEEP_CONFIG is inside ROLE_KIT_CLONE", SWEEP_CONFIG="~/.stage-e/kit/s.json"),
        refused("are one folder", SWEEP_DISPATCHER_HOME="@ROOT@/dispatcher/worktrees"),
        refused("SWEEP_INTERVAL_SECONDS must", SWEEP_INTERVAL_SECONDS="60"),
        refused("LEFTOVER_CEILING_GB must", LEFTOVER_CEILING_GB="0")], [True] * 7)

    def example():
        with open(os.path.join(os.path.dirname(HERE), "sweep.conf.example"), encoding="utf-8") as fh:
            values, errs = parse_conf(fh.read())
        more = validate_conf(values, person)[1]
        return errs, sorted(CONF_KEYS ^ set(values)), len(more), all("still the example" in e for e in more)
    check("conf: the committed example names every key, and only its placeholders are refused", example(), ([], [], 2, True))

    # what it composes
    fake, conf = world(TEAM_KEYS="ENG,OPS", SWEEP_DONE_DAYS="5")
    doc = json.loads(compose(conf, fake.home))
    knows = "logs" in sweep.CONFIG_KEYS
    check("compose: the config passes the sweep's own loader; with log trimming, the role's home is under every pattern",
          (doc["teams"], doc["done_days"], knows == ("logs" in doc),
           not knows or (all(p.startswith(fake.home + "/") for p in doc["logs"]) and len(doc["logs"]) == 4)),
          (["ENG", "OPS"], 5, True, True))
    real_keys, real_load = sweep.CONFIG_KEYS, sweep.load_config
    sweep.CONFIG_KEYS = dict(real_keys, logs="", log_max_mb="", log_keep="")
    sweep.load_config = lambda path: json.load(open(path))
    try:
        newer = json.loads(compose(conf, fake.home))
    finally:
        sweep.CONFIG_KEYS, sweep.load_config = real_keys, real_load
    check("compose: a sweep that can trim logs is given the log patterns, absolute, and the size line",
          (len(newer["logs"]), all(p.startswith(fake.home + "/") for p in newer["logs"]), newer["log_max_mb"], newer["log_keep"]),
          (4, True, 10, 3))
    check("compose: a composition the sweep's loader refuses is refused in its words",
          str(quiet(lambda: compose(dict(conf, SWEEP_WORKSPACE_DIR=conf["SWEEP_DISPATCHER_HOME"]), fake.home))[0])[:40],
          "REFUSED by the sweep's own loader: confi")

    # the config
    fake, conf = world()
    first = step_config(ctx(fake, conf, role=True), True)[0]
    again = step_config(ctx(fake, conf, role=True), False)[0]
    check("config: written as the role account at mode 600, read back, and then current",
          (first, again, oct(os.stat(fake.home + "/.stage-e/sweep/sweep.json").st_mode & 0o777)), (False, True, "0o600"))
    undo = ns.CONFIG_WRITE_SH
    ns.CONFIG_WRITE_SH = undo.replace("chmod 600", "chmod 644")
    try:
        fake2, conf2 = world()
        loose = str(quiet(lambda: step_config(ctx(fake2, conf2, role=True), True))[0])
    finally:
        ns.CONFIG_WRITE_SH = undo
    check("config: a file left readable by anyone is refused", "mode 644" in loose, True)

    # the plist
    fake, conf = world()
    job = job_spec(conf, fake.home)
    pl = plistlib.loads(job["body"].encode("utf-8"))
    check("plist: label, account, interval and command", (pl["Label"], pl["UserName"], pl["StartInterval"],
                                                          pl["ProgramArguments"][:2], SWEEP_SCRIPT in pl["ProgramArguments"][2],
                                                          'run --config "$HOME/.stage-e/sweep/sweep.json"' in pl["ProgramArguments"][2]),
          ("com.example.sweep", "_exsweep", 3600, ["/bin/sh", "-c"], True, True))
    check("plist: its log sits beside the config, where the sweep's own log patterns reach it",
          job["log"] == fake.home + "/.stage-e/sweep/sweep.log", True)

    # the job step: installed, never loaded; a foreign plist is never replaced
    def jobs():
        f, c = world()
        x = ctx(f, c, role=True)
        a = step_job(x, False)[0]
        b = step_job(x, True)[0]
        again = step_job(ctx(f, c, role=True), True)[0]
        return a, b, again, f.target in f.plists, [l for l in f.loaded]
    check("job: would install, then installs and loads nothing, then is current", jobs(), (False, False, True, True, []))
    f, c = world()
    f.plists[f.target] = f.plists.get(f.target) or plistlib.dumps({"Label": c["SWEEP_JOB_LABEL"], "ProgramArguments": [
        "/bin/sh", "-c", "exec /usr/bin/python3 /somewhere/else.py"]}).decode()
    err = str(quiet(lambda: step_job(ctx(f, c, role=True), True))[0])
    check("job: a plist that is another job is refused, and left as it was",
          ("is NOT the sweep" in err, "else.py" in f.plists[f.target]), (True, True))

    # the dry run
    f, c = world()
    x = ctx(f, c, role=True)
    waits = step_dry_run(x, False)[1]
    step_config(x, True)
    step_job(x, True)
    ok = quiet(lambda: step_dry_run(x, True))
    check("dry-run: waits on the config and the job; then runs as the role, prints the sweep's lines, and is a DRY run",
          ("waits on config and job" in waits, ok[0][0], "would remove 2" in ok[1], "REAL PASS" in ok[1]),
          (True, False, True, False))
    _put(f.home + "/.fake-exit-sweep", "1")
    bad_run = quiet(lambda: step_dry_run(x, True))
    check("dry-run: a failure names the exit and scrubs the key from the line it quotes",
          ("exit 1" in str(bad_run[0]), key in str(bad_run[0]) + bad_run[1]), (True, False))

    # enable
    f, c = world()
    x = ctx(f, c, role=True)
    step_job(x, True)
    absent = quiet(lambda: step_enable(x, False))[0]
    f.load()
    same = step_enable(ctx(f, c, role=True), False)[0]
    f.args = False
    unclear = quiet(lambda: step_enable(ctx(f, c, role=True), False))[0]
    check("enable: not loaded is CK-S1; loaded and running its plist is done; a bare print is not measured",
          (getattr(absent, "card_id", None), same, type(unclear).__name__), ("CK-S1", True, "Unknown"))

    # the leftover row
    def leftover(files, line_kb=None, locked=False, fail=()):
        f, c = world()
        x = ctx(f, c, role=True)
        for what in fail:
            _put(f.home + "/.fail-" + what, "")
        os.makedirs(f.home + "/.stage-e/state")
        for rel, kb in files:
            _put(c["SWEEP_WORKSPACE_DIR"] + "/" + rel, "x" * (kb * 1024))
        if locked:
            os.chmod(f.home + "/.stage-e/state", 0)
        real = me.ceiling_kb
        if line_kb is not None:
            me.ceiling_kb = lambda conf: line_kb
        try:
            return quiet(lambda: step_leftover(x, False))[0]
        finally:
            me.ceiling_kb = real
            os.chmod(f.home + "/.stage-e/state", 0o700)
    under = leftover([("ENG-1/big", 10)])
    check("leftover: under the line is done, and says how much it counted",
          (under[0], "left behind in 4 areas" in under[1]), (True, True))
    over = leftover([("ENG-1/small", 10), ("ENG-9/huge", 3000)], line_kb=1024)
    named = str(over).partition("Largest: ")[2]
    check("leftover: past the line fails and names the largest folder first",
          (type(over).__name__, named.startswith("/") and "ENG-9" in named.split(",")[0], "past the 5 GB line" in str(over)),
          ("SetupError", True, True))
    if os.geteuid() != 0:
        partly = leftover([("ENG-1/big", 10)], locked=True)
        check("leftover: a folder the role cannot read is NOT MEASURED WHOLE, never counted as empty",
              (type(partly).__name__, "partly readable" in str(partly)), ("Unknown", True))
    for what in ("id", "getconf"):
        blind = leftover([("ENG-1/big", 10)], fail=(what,))
        check("leftover: a failed `%s` probe is NOT MEASURED WHOLE, never a fallback folder or an empty area" % what,
              (type(blind).__name__, "could not be located" in str(blind)), ("Unknown", True))
    empty = leftover([])
    check("leftover: nothing there is done: absent areas count as nothing, and are not an error", empty[0], True)

    # the whole run: the agent policy, and a dry run that writes nothing
    f, c = world()
    code, out = quiet(lambda: cmd_run(ctx(f, c, env={"CLAUDECODE": "1"}, dry=True), True))
    check("run --dry-run under a model: every step needing sudo is NOT MEASURED, and nothing was written",
          (code, f.writes), (EX_UNKNOWN, []))
    try:
        refuse_if_agent("run", {"CLAUDECODE": ""})
        refusal = "no refusal"
    except Refusal:
        refusal = "refused"
    check("run: refused under a model, even with the marker set empty", refusal, "refused")
    f, c = world()
    code, out = quiet(lambda: cmd_run(ctx(f, c, role=False), False))
    check("run: a full pass to the card: config written, plist installed, dry run shown, then CK-S1 (exit 10)",
          (code, table(out).get("job"), table(out).get("dry-run"), "CK-S1" in out, f.writes != []),
          (EX_BLOCKED, "DONE", "DONE", True, True))

    # the source: it loads nothing, runs no rm, reads no credential value
    with open(os.path.abspath(__file__), encoding="utf-8") as fh:
        source = fh.read().split("\n# ── --selftest")[0]
    check("source: it never loads a job, runs rm, or prints a credential",
          ([w for w in ("bootstrap", '"rm"', "'rm'") if w in source.replace("sudo launchctl bootstrap", "")], "print(" in source),
          ([], False))
    check("source: all checks above ran the sweep's real loader (its selftest passes from this checkout)",
          subprocess.run([sys.executable, os.path.join(HERE, SWEEP_SCRIPT), "--selftest"], capture_output=True).returncode, 0)


if __name__ == "__main__":
    sys.exit(main())
