#!/usr/bin/env python3
"""Acceptance installer: the acceptance check set up on the role account with one command (KIT-326).

    python3 scripts/pipeline_acceptance_setup.py run --dry-run    # read it first
    python3 scripts/pipeline_acceptance_setup.py run              # check, show, ask once for a typed yes, then apply
    python3 scripts/pipeline_acceptance_setup.py verify           # read-only: one row for each thing below

It puts five things in place, and `verify` checks each as its own row:

    browser      Playwright and its Chromium, installed as root at the conf's paths, owned by root, not writable by others
    profile      the look tool's seatbelt profile, in the role account's clone, level with this checkout
    mcp-config   the acceptance session's MCP config (the look tool, started with --config-dir), root-owned
    entry        one `acceptance-<repo>` dispatcher entry per repository, written beside the others, which it never edits
    job          the live pass's LaunchDaemon plist, installed and NEVER loaded here (card CK-A1)

The queue installer's contract: at something only a person can do it prints a card and exits 10; do that and run it
again. Exits: 0 done, 1 failed, 2 conf, 3 refused under a model, 4 not measured, 5 no sudo. Every path and name comes
from the conf, and nothing from this Mac. It never reads a credential (names only), never restarts the dispatcher, and
never merges, approves or labels. Under a model `run` is REFUSED before the conf is read.
"""
import argparse
import ast
import json
import os
import plistlib
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
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
import pipeline_acceptance_run as arun  # noqa: E402  (the live pass's own loader judges what this file composes)
import pipeline_look as look  # noqa: E402  (so does the look tool's)

credential_shape, scrub = ns.credential_shape, ns.scrub
_s = lambda text: scrub(text, ())      # noqa: E731  (everything printed passes through the notifier's scrub)
DEFAULT_CONF = "acceptance.conf"
RUN_SCRIPT = "pipeline_acceptance_run.py"
LOOK_SCRIPT = "pipeline_look.py"
SELFTESTS = (RUN_SCRIPT, "pipeline_acceptance_poller.py", LOOK_SCRIPT)
RUNNER_FILE = "look_runner.mjs"         # the look tool starts it; no script imports it
PROFILE_DIR, PROFILE_FILE = "templates/sandbox", "look.sb"
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
MCP_SERVER = "look"                      # its tool is then mcp__look__look_run
ENTRY_PREFIX = arun.ENTRY_PREFIX
DRY_OK = (0,)                            # the live pass's own: 0 ran

CONF_REQUIRED = ("ROLE_ACCOUNT", "TEAM_KEY", "AGENT_USER_ID", "OWNER_USER_ID", "ACCEPTANCE_REPOS", "ACCEPTANCE_JOB_LABEL",
                 "DISPATCHER_CONFIG", "DISPATCHER_SERVICE", "ACCEPTANCE_WORKSPACE_DIR", "ACCEPTANCE_NODE",
                 "ACCEPTANCE_PLAYWRIGHT_VERSION", "ACCEPTANCE_PLAYWRIGHT_DIR", "ACCEPTANCE_BROWSERS_PATH",
                 "ACCEPTANCE_MCP_CONFIG")
CONF_DEFAULTS = {
    "ROLE_KIT_CLONE": se.ROLE_KIT_CLONE_PATH, "ENV_FILE": "~/.stage-e/env", "LINEAR_KEY_ENV": "STAGE_E_LINEAR_API_KEY",
    "GITHUB_TOKEN_ENV": "GH_TOKEN", "ACCEPTANCE_STATE_DIR": "~/.stage-e/state",
    "ACCEPTANCE_CONFIG": "~/.stage-e/acceptance/acceptance.json", "ACCEPTANCE_LOOK_BASE": "~/.stage-e/acceptance/look-base.json",
    "ACCEPTANCE_LOOK_CONFIG_DIR": "~/.stage-e/acceptance/look-configs", "ACCEPTANCE_LOOK_RECORD_DIR": "~/.stage-e/look-records",
    "ACCEPTANCE_INTERVAL_SECONDS": "300", "ACCEPTANCE_TIME_LIMIT_MINUTES": "90", "ACCEPTANCE_MAX_STARTS": "2",
    "LOOK_MAX_RUNS": "6", "LOOK_TIMEOUT_SECONDS": "300", "BASE_REF": "origin/main", "BASE_GIT_DIR": "",
    "CONFIG_BACKUPS_KEPT": "5",
}
CONF_KEYS = set(CONF_REQUIRED) | set(CONF_DEFAULTS)
# Left invalid on purpose, so an unedited copy of the example cannot run.
CONF_UNEDITED = {"DISPATCHER_CONFIG": "/opt/example-dispatcher/config.json",
                 "ACCEPTANCE_WORKSPACE_DIR": "/opt/example-dispatcher/worktrees", "ACCEPTANCE_NODE": "/opt/example/bin/node",
                 "ACCEPTANCE_PLAYWRIGHT_DIR": "/opt/example/playwright", "ACCEPTANCE_BROWSERS_PATH": "/opt/example/ms-playwright",
                 "ACCEPTANCE_MCP_CONFIG": "/opt/example/acceptance-mcp.json"}
ROLE_PATH_KEYS = ("ROLE_KIT_CLONE", "ENV_FILE", "ACCEPTANCE_STATE_DIR", "ACCEPTANCE_CONFIG", "ACCEPTANCE_LOOK_BASE",
                  "ACCEPTANCE_LOOK_CONFIG_DIR", "ACCEPTANCE_LOOK_RECORD_DIR")
ABS_PATH_KEYS = ("DISPATCHER_CONFIG", "ACCEPTANCE_WORKSPACE_DIR", "ACCEPTANCE_NODE", "ACCEPTANCE_PLAYWRIGHT_DIR",
                 "ACCEPTANCE_BROWSERS_PATH", "ACCEPTANCE_MCP_CONFIG")
RANGES = (("ACCEPTANCE_INTERVAL_SECONDS", 60, 86400), ("ACCEPTANCE_TIME_LIMIT_MINUTES", 10, 1440), ("ACCEPTANCE_MAX_STARTS", 1, 20),
          ("LOOK_MAX_RUNS", 1, 50), ("LOOK_TIMEOUT_SECONDS", 30, 3600), ("CONFIG_BACKUPS_KEPT", 1, 50))
ID_RE = arun.ID_RE


def _imports(tree):
    """Module names a parsed script imports, outside its selftest functions: what its jobs load when they run."""
    skip = lambda n: isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and (                  # noqa: E731
        n.name == "selftest" or n.name.startswith(("_selftest", "_cases")))
    todo, out = [tree], []
    while todo:
        for node in ast.iter_child_nodes(todo.pop()):
            if skip(node):
                continue
            if isinstance(node, ast.Import):
                out.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                out.append(node.module)
            todo.append(node)
    return out


def required_scripts(here=None):
    """Every script the live pass, the poller and the look tool load from the role account's clone, transitively, taken from
    this checkout's own source (its selftests' imports left out) so the list cannot fall behind it, and the runner file."""
    here = here or HERE
    seen, todo = set(), list(SELFTESTS)
    while todo:
        name = todo.pop()
        if name in seen or not os.path.exists(os.path.join(here, name)):
            continue
        seen.add(name)
        with open(os.path.join(here, name), encoding="utf-8") as fh:
            todo.extend(m.split(".")[0] + ".py" for m in _imports(ast.parse(fh.read())))
    return tuple(sorted(seen)) + (RUNNER_FILE,)


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
    """(conf, errors): every fault of the conf's own in ONE pass, by key; the live pass's own loader judges the rest."""
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
    for k in ("ACCEPTANCE_JOB_LABEL", "DISPATCHER_SERVICE"):
        need(not c.get(k) or se._RDNS_RE.match(c[k]), "%s is not a reverse-DNS launchd label" % k)
    need(not c.get("TEAM_KEY") or re.match(r"^[A-Z][A-Z0-9]{0,9}$", c["TEAM_KEY"]), "TEAM_KEY must be a team KEY, e.g. ENG")
    for k in ("AGENT_USER_ID", "OWNER_USER_ID"):
        need(not c.get(k) or ID_RE.match(c[k]), "%s must be a tracker user id" % k)
    repos = split_list(c.get("ACCEPTANCE_REPOS"))
    need(not c.get("ACCEPTANCE_REPOS") or repos and all(arun.REPO_RE.match(r) for r in repos),
         "ACCEPTANCE_REPOS must list OWNER/NAME repositories")
    names = [ENTRY_PREFIX + r.split("/")[-1].lower() for r in repos]
    need(len(set(names)) == len(names), "ACCEPTANCE_REPOS holds two repositories with one name: their entries would share it")
    path_ok = lambda v: bool(ns._PATH_RE.match(v)) and "/../" not in v + "/" and v.rstrip("/") not in ("", "~")   # noqa: E731
    for k in ABS_PATH_KEYS:
        need(not c.get(k) or c[k].startswith("/") and path_ok(c[k]), "%s must be an absolute path with no `..`" % k)
    bad = [k for k in ROLE_PATH_KEYS if not path_ok(c[k])]
    e.extend("%s must be `~/…` (the role account's home) or an absolute path, with no `..`" % k for k in bad)
    base = c.get("BASE_GIT_DIR") or ""
    need(not base or base.startswith("/") and path_ok(base), "BASE_GIT_DIR must be empty or an absolute path with no `..`")
    for k in ("LINEAR_KEY_ENV", "GITHUB_TOKEN_ENV"):
        need(ns._UPPER_SNAKE.match(c.get(k) or ""), "%s must be the NAME of an environment variable" % k)
    need(re.match(r"^[0-9]+\.[0-9]+\.[0-9]+$", c.get("ACCEPTANCE_PLAYWRIGHT_VERSION") or "") or not c.get("ACCEPTANCE_PLAYWRIGHT_VERSION"),
         "ACCEPTANCE_PLAYWRIGHT_VERSION must be one exact version, like 1.49.0")
    need(look.BASE_REF_RE.match(c.get("BASE_REF") or "") and ".." not in (c.get("BASE_REF") or ""),
         "BASE_REF must be a branch name such as origin/main")
    for k, low, high in RANGES:
        need(low <= (whole(c.get(k)) or 0) <= high, "%s must be a whole number from %d to %d" % (k, low, high))
    roots = [(c.get(k) or "").rstrip("/") for k in ("ACCEPTANCE_PLAYWRIGHT_DIR", "ACCEPTANCE_BROWSERS_PATH")]
    need(not all(roots) or roots[0] != roots[1] and not ns._under(roots[0], roots[1]) and not ns._under(roots[1], roots[0]),
         "ACCEPTANCE_PLAYWRIGHT_DIR and ACCEPTANCE_BROWSERS_PATH must be two separate folders")
    mcp = (c.get("ACCEPTANCE_MCP_CONFIG") or "").rstrip("/")
    need(not mcp or not any(ns._under(mcp, r) for r in roots if r),
         "ACCEPTANCE_MCP_CONFIG is inside the Playwright or browser folder: it must be a file of its own")
    need(c.get("ACCEPTANCE_LOOK_CONFIG_DIR") != c.get("ACCEPTANCE_LOOK_RECORD_DIR"),
         "ACCEPTANCE_LOOK_CONFIG_DIR and ACCEPTANCE_LOOK_RECORD_DIR are one folder")
    need("ROLE_KIT_CLONE" in bad or c["ROLE_KIT_CLONE"].rstrip("/") == se.ROLE_KIT_CLONE_PATH,
         "ROLE_KIT_CLONE is not Stage E's clone, %s" % se.ROLE_KIT_CLONE_PATH)
    clone = ns.resolve_path(c["ROLE_KIT_CLONE"], ns.ROLE_HOME_SENTINEL) if "ROLE_KIT_CLONE" not in bad else None
    for k in ("ACCEPTANCE_CONFIG", "ACCEPTANCE_LOOK_BASE", "ACCEPTANCE_LOOK_CONFIG_DIR", "ACCEPTANCE_LOOK_RECORD_DIR",
              "ACCEPTANCE_STATE_DIR"):
        need(k in bad or clone is None or not ns._under(ns.resolve_path(c[k], ns.ROLE_HOME_SENTINEL), clone),
             "%s is inside ROLE_KIT_CLONE, a git working tree" % k)
    return c, e


def load_conf(path, user=None):
    try:
        with open(path, encoding="utf-8") as fh:
            values, errors = parse_conf(fh.read(), os.path.basename(path))
    except OSError as exc:
        raise SetupError("could not read %s (%s): cp acceptance.conf.example acceptance.conf && chmod 600 acceptance.conf"
                         % (path, exc.strerror))
    conf, more = validate_conf(values, user)
    return conf, errors + more


# ── what it composes: three documents and the entries, judged by the tools' own loaders ───────────────────────
def role(c, key, home):
    return ns.resolve_path(c[key].rstrip("/"), home)


def entry_name(repo):
    return ENTRY_PREFIX + repo.split("/")[-1]


def compose(c, home):
    """{"run": text, "look_base": text, "mcp": text}, `~` made absolute under the role's home. SetupError in the tools'
    own words when either refuses what this file composes."""
    base = {"node": c["ACCEPTANCE_NODE"], "browsers": c["ACCEPTANCE_BROWSERS_PATH"].rstrip("/"), "role_home": home,
            "playwright_dir": c["ACCEPTANCE_PLAYWRIGHT_DIR"].rstrip("/"), "base_ref": c["BASE_REF"],
            "max_runs": whole(c["LOOK_MAX_RUNS"]), "timeout_seconds": whole(c["LOOK_TIMEOUT_SECONDS"])}
    if c.get("BASE_GIT_DIR"):
        base["base_git_dir"] = c["BASE_GIT_DIR"].rstrip("/")
    unknown = sorted(set(base) - set(look.CONFIG_KEYS))
    if unknown:
        raise SetupError("the look tool no longer takes config key(s) %s: this installer is behind it" % ", ".join(unknown))
    run = {"state_dir": role(c, "ACCEPTANCE_STATE_DIR", home), "linear_key_env": c["LINEAR_KEY_ENV"],
           "github_token_env": c["GITHUB_TOKEN_ENV"], "team_key": c["TEAM_KEY"], "agent_user_id": c["AGENT_USER_ID"],
           "entries": {r: {"workspace_dir": c["ACCEPTANCE_WORKSPACE_DIR"].rstrip("/")} for r in split_list(c["ACCEPTANCE_REPOS"])},
           "look_base_config": role(c, "ACCEPTANCE_LOOK_BASE", home), "look_config_dir": role(c, "ACCEPTANCE_LOOK_CONFIG_DIR", home),
           "look_record_dir": role(c, "ACCEPTANCE_LOOK_RECORD_DIR", home),
           "time_limit_minutes": whole(c["ACCEPTANCE_TIME_LIMIT_MINUTES"]), "max_starts_per_pass": whole(c["ACCEPTANCE_MAX_STARTS"])}
    base_text = json.dumps(base, indent=2, sort_keys=True) + "\n"
    run_text = json.dumps(run, indent=2, sort_keys=True) + "\n"
    with tempfile.TemporaryDirectory(prefix="acceptance-setup-judge-") as tmp:
        copy = os.path.join(tmp, "look-base.json")           # the loader opens the base config: judge it where it can
        with open(copy, "w", encoding="utf-8") as fh:
            fh.write(base_text)
        path = os.path.join(tmp, "acceptance.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(run, look_base_config=copy), indent=2, sort_keys=True) + "\n")
        try:
            arun.load_config(path)
        except arun.RunError as exc:
            raise SetupError("REFUSED by the live pass's own loader: %s" % exc)
    mcp = {"mcpServers": {MCP_SERVER: {"command": "/usr/bin/python3", "args": [
        role(c, "ROLE_KIT_CLONE", home) + "/scripts/" + LOOK_SCRIPT, "--config-dir", run["look_config_dir"]]}}}
    return {"run": run_text, "look_base": base_text, "mcp": json.dumps(mcp, indent=2, sort_keys=True) + "\n"}


def run_command(c):
    script = c["ROLE_KIT_CLONE"].rstrip("/") + "/scripts/" + RUN_SCRIPT
    return ns.exec_prefix(c) + "%s run --config %s" % (ns._q(script), ns._q(c["ACCEPTANCE_CONFIG"]))


def job_spec(c, home):
    """The one job, with its plist's body. Stage E's own PLIST, filled in; launchd expands nothing."""
    cfg = ns.resolve_path(c["ACCEPTANCE_CONFIG"].rstrip("/"), home)
    job = {"name": "the acceptance live pass", "label": c["ACCEPTANCE_JOB_LABEL"],
           "plist": se._dispatcher_plist(c["ACCEPTANCE_JOB_LABEL"]), "script": RUN_SCRIPT,
           "interval": int(c["ACCEPTANCE_INTERVAL_SECONDS"]), "command": run_command(c),
           "log": os.path.dirname(cfg) + "/acceptance.log"}
    esc = _xml_escape
    return dict(job, body=se.PLIST.format(label=esc(job["label"]), account=esc(c["ROLE_ACCOUNT"]), home=esc(home),
                                          path=esc(se.DAEMON_PATH), command=esc(job["command"]),
                                          interval=job["interval"], log=esc(job["log"])))


def facts_py(path):
    """Fields of the dispatcher's config, run as the role account. It prints FACTS, never the file: the config holds
    tracker tokens, and this picks only the keys an acceptance entry is made of and compared by."""
    return (
        "import json\n"
        "c=json.load(open(%r))\n"
        "rs=c.get('repositories') or []\n"
        "keys=('id','name','repositoryPath','baseBranch','workspaceBaseDir','linearWorkspaceId','githubUrl','gitlabUrl',"
        "'teamKeys','projectKeys','routingLabels','isActive','disallowedTools','mcpConfigPath')\n"
        "print(json.dumps({'workspace_ids': sorted({r.get('linearWorkspaceId') for r in rs if r.get('linearWorkspaceId')}),\n"
        "  'workspace_base_dirs': sorted({r.get('workspaceBaseDir') for r in rs if r.get('workspaceBaseDir')}),\n"
        "  'entries': [dict({k: r.get(k) for k in keys}, allowedUsers=(r.get('userAccessControl') or {}).get('allowedUsers'))\n"
        "              for r in rs]}))\n" % path)


def wanted_entries(c, facts, known):
    """The acceptance entries this conf produces, one per repository, each copied from the entry that manages that
    repository: its clone and its branch. SetupError when a repository has no such entry."""
    repos = split_list(c["ACCEPTANCE_REPOS"])
    bases, spaces = facts.get("workspace_base_dirs") or [], facts.get("workspace_ids") or []
    if not known or not bases or not spaces:
        raise SetupError("the dispatcher's config named no repository it manages, no workspaceBaseDir or no workspace id")
    if len(bases) > 1 or len(spaces) > 1:
        raise SetupError("the dispatcher's entries disagree about workspaceBaseDir or the workspace id: refusing to guess")
    if bases[0].rstrip("/") != c["ACCEPTANCE_WORKSPACE_DIR"].rstrip("/"):
        raise SetupError("ACCEPTANCE_WORKSPACE_DIR is not the dispatcher's workspaceBaseDir: the conf says %s, the dispatcher's "
                         "config says %s. A check's worktree must be where the dispatcher cleans up." % (
                             c["ACCEPTANCE_WORKSPACE_DIR"], bases[0]))
    missing = [r for r in repos if not (known.get(r.lower()) or {}).get("repositoryPath")]
    if missing:
        raise SetupError("the dispatcher manages no clone of %s, so a check of it would have no checkout to build in. Give "
                         "the dispatcher an entry whose clone's `origin` is that repository, or drop it from "
                         "ACCEPTANCE_REPOS." % ", ".join(missing))
    out = []
    for repo in repos:
        model = known[repo.lower()]
        out.append({"id": entry_name(repo), "name": entry_name(repo), "repositoryPath": model["repositoryPath"],
                    "baseBranch": model.get("baseBranch") or "main", "workspaceBaseDir": bases[0], "linearWorkspaceId": spaces[0],
                    "routingLabels": [entry_name(repo)], "isActive": True,
                    "disallowedTools": list(model.get("disallowedTools") or []),
                    "mcpConfigPath": c["ACCEPTANCE_MCP_CONFIG"], "userAccessControl": {"allowedUsers": [c["OWNER_USER_ID"]]}})
    return out


LIST_KEYS = ("routingLabels", "disallowedTools")      # absent in the config reads as an empty list
ENTRY_KEYS = ("id", "name", "repositoryPath", "baseBranch", "workspaceBaseDir", "linearWorkspaceId", "routingLabels", "isActive",
              "disallowedTools", "mcpConfigPath")


def entry_matches(have, want):
    """The present entry is the one this conf produces, and routes by nothing else (no URL, team or project key)."""
    if not have or any((have.get(k) if k not in LIST_KEYS or have.get(k) is not None else []) != want.get(k) for k in ENTRY_KEYS):
        return False
    if any(have.get(k) for k in ("githubUrl", "gitlabUrl", "teamKeys", "projectKeys")):
        return False
    return (have.get("allowedUsers") or []) == want["userAccessControl"]["allowedUsers"]


# ── cards, and the agent policy ───────────────────────────────────────────────────────────────────────────────
CARDS = {
    "CK-A1": {
        "title": "Read the live pass's dry run, then load the job",
        "why": ("Loading starts the acceptance check. Each pass starts a check for every approved plan whose last piece is "
                "open, green and reviewed: it files an acceptance ticket and hands it to the dispatcher's agent. The dry run "
                "above says which plans that is right now. Nothing starts that the dry run did not list."),
        "do": ["1. Read the dry run's lines above. A plan listed that you do not want checked: stop here and fix that first.",
               "2. If the entry step wrote or changed an acceptance entry, restart the dispatcher when no session is running:",
               "       sudo launchctl kickstart -k system/${DISPATCHER_SERVICE}",
               "3. Enable, then load:",
               "       sudo launchctl enable system/${ACCEPTANCE_JOB_LABEL} && sudo launchctl bootstrap system ${ACCEPTANCE_PLIST}",
               "   If the plist changed after it was loaded, `sudo launchctl bootout system/${ACCEPTANCE_JOB_LABEL}` first.",
               "4. Run this installer again:  python3 ${SELF} run${FLAGS}"],
        "good": "`launchctl print` shows the job running its plist's command, and `run` exits 0"},
}


def _self_path():
    return os.path.relpath(os.path.abspath(__file__))


def print_card(cid, c, flags=""):
    card = CARDS[cid]
    values = dict(c, SELF=_self_path(), FLAGS=flags, ACCEPTANCE_PLIST=se._dispatcher_plist(c["ACCEPTANCE_JOB_LABEL"]))
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
                      "  A PERSON runs this, in a terminal:  python3 %s %s\n  Read-only meanwhile:  verify, or run --dry-run"
                      % (action, ", ".join(found), _self_path(), action))


class Ctx(qs.Ctx):
    def __init__(self, conf, runner, env=None, sudo=None):
        qs.Ctx.__init__(self, conf, runner, env, sudo)
        self.job_current = self.config_current = False
        self.dispatcher, self.origin_slugs, self.entry_changed = {}, {}, False


def acquire_privilege(ctx, dry_run, verb="run"):
    if ctx.agent:
        return False
    try:
        ctx.sudo.acquire("`%s%s` works as the %s role account and as root, for the browser, the MCP config and one plist. It "
                         "loads no job and restarts nothing." % (verb, " --dry-run" if dry_run else "", ctx.account), "")
    except NoPrivilege:
        raise NoPrivilege("NO ADMINISTRATOR ACCESS — nothing was attempted or changed. Fix that and run again:  "
                          "python3 %s %s%s%s" % (_self_path(), verb, " --dry-run" if dry_run else "", ctx.flags))
    return True


def waits_on_home(ctx):
    if not ctx.role_home:
        raise Unknown("waits on preflight: the role account's home is not known", "clear the preflight row first")


def clone_hashes(ctx, subdir, names):
    """{name: hash or None} the role's clone holds under `subdir`, and this checkout's; (theirs, ours) or Unknown."""
    clone = ctx.conf["ROLE_KIT_CLONE"].rstrip("/")
    res = ctx.as_role(ns.CLONE_HASH_SH.replace("@DIR@", ns._q(clone + "/" + subdir)).replace("@FILES@", " ".join(names)),
                      what="hashing the clone's %s" % subdir)
    if not res.ok:
        raise Unknown("the clone's %s could not be read as %s (exit %d)" % (subdir, ctx.account, res.rc),
                      "run the same command in your own terminal")
    theirs = ns.parse_clone_hashes(res.out)
    root = os.path.join(os.path.dirname(ctx.checkout_scripts), subdir)
    return [(n, theirs.get(n, ("",))[0] == "sha256" and theirs[n][1] == qs.checkout_hash(root, n)) for n in names]


# ── steps: each returns (ok, detail, notes) or raises Blocked / Unknown / SetupError ──────────────────────────
def step_preflight(ctx, apply_it):
    r, c, problems, unmeasured = ctx.runner, ctx.conf, [], []
    home = r.read(["dscl", ".", "-read", "/Users/" + ctx.account, "NFSHomeDirectory"])
    value = (home.out.split("NFSHomeDirectory:", 1) + [""])[1].strip().split("\n")[0].strip() if home.ok else ""
    if value.startswith("/") and value != "/" and ns._PATH_RE.match(value):
        ctx.role_home = value
    else:
        problems.append("the role account has no home (dscl): install Stage E first")
    failed = [n for n in SELFTESTS if not r.read([sys.executable, os.path.join(HERE, n), "--selftest"], timeout=900).ok]
    if failed:
        problems.append("selftest FAILED from this checkout: %s" % ", ".join(failed))
    if ctx.agent:
        unmeasured.append("the role account's clone and env file (they need `sudo -u %s`)" % ctx.account)
    else:
        try:
            names = required_scripts()
            off = [n for n, ok in clone_hashes(ctx, "scripts", names) if not ok]
            if off:
                problems.append("the clone at %s is not level with this checkout (%s): merge the change, then let Stage E's "
                                "`run` move it" % (c["ROLE_KIT_CLONE"].rstrip("/"), ", ".join(off[:6]) + (" and %d more" % (len(off) - 6) if len(off) > 6 else "")))
        except Unknown as exc:
            unmeasured.append(exc.what)
        for var in ("LINEAR_KEY_ENV", "GITHUB_TOKEN_ENV"):
            res = ctx.as_role(ns._cred_script(ns.CRED_PROBE_SH, dict(
                c, LINEAR_KEY_ENV=c[var], CHAT_TOKEN_ENV="ACCEPTANCE_SETUP_UNUSED", WRITER="no",
                WRITER_TOKEN_ENV="ACCEPTANCE_SETUP_UNUSED")), what="reading %s by name" % c["ENV_FILE"])
            facts = ns.parse_cred_probe(res.out)
            if res.rc == 9:
                problems.append("%s does not exist for %s: Stage E writes it" % (c["ENV_FILE"], ctx.account))
            elif not facts:
                unmeasured.append("%s could not be read as %s" % (c["ENV_FILE"], ctx.account))
            elif facts["mode"] != "600" or facts["key"] != "ok":
                problems.append("%s is mode %s, or has no usable %s" % (c["ENV_FILE"], facts["mode"], c[var]))
    if problems:
        raise SetupError("preflight found %d problem(s), all of them:\n  - %s" % (len(problems), "\n  - ".join(problems)))
    if unmeasured:
        raise Unknown("NOT MEASURED: " + "; ".join(sorted(set(unmeasured))), "run the same command in your own terminal")
    return True, "the role's home, the clone (%d files, level with this checkout), the env file's two names and the three " \
                 "selftests pass" % len(names), []


def step_profile(ctx, apply_it):
    """The seatbelt profile ships in the kit, and the look tool reads it from the role's clone: it is in place when the
    clone's copy is this checkout's, and macOS has the program that runs it."""
    waits_on_home(ctx)
    off = [n for n, ok in clone_hashes(ctx, PROFILE_DIR, (PROFILE_FILE,)) if not ok]
    if off:
        raise SetupError("the clone's %s/%s is not this checkout's: merge the change, then let Stage E's `run` move the clone"
                         % (PROFILE_DIR, PROFILE_FILE))
    if not ctx.runner.read(["test", "-x", SANDBOX_EXEC]).ok:
        raise SetupError("%s is not there: the look tool runs nothing without it" % SANDBOX_EXEC)
    return True, "%s/%s in the clone is this checkout's, and %s is there" % (PROFILE_DIR, PROFILE_FILE, SANDBOX_EXEC), []


def root_only(r, path):
    """'' when `path` and everything under it is root's and not writable by group or others; else the first offender.
    Unknown when find could not look."""
    got = r.read(["find", path, "(", "-not", "-user", "root", "-o", "-perm", "-g+w", "-o", "-perm", "-o+w", ")", "-print", "-quit"])
    if not got.ok:
        raise Unknown("%s could not be read whole (find exit %d)" % (path, got.rc), "run the same command as a person who can")
    return (got.out or "").strip().splitlines()[0] if (got.out or "").strip() else ""


def step_browser(ctx, apply_it):
    """Playwright (an exact version) and one Chromium, both as root, owned by root and not writable by others."""
    c, r = ctx.conf, ctx.runner
    pw, browsers, ver = c["ACCEPTANCE_PLAYWRIGHT_DIR"].rstrip("/"), c["ACCEPTANCE_BROWSERS_PATH"].rstrip("/"), c["ACCEPTANCE_PLAYWRIGHT_VERSION"]
    npm = os.path.join(os.path.dirname(c["ACCEPTANCE_NODE"]), "npm")
    gone = []
    if not r.read(["test", "-x", c["ACCEPTANCE_NODE"]]).ok:
        raise SetupError("ACCEPTANCE_NODE (%s) is not an executable file" % c["ACCEPTANCE_NODE"])
    pkg = r.read(["cat", pw + "/node_modules/playwright/package.json"])
    try:
        have = json.loads(pkg.out).get("version") if pkg.ok else None
    except (ValueError, AttributeError):
        have = None
    if have != ver:
        gone.append("Playwright %s (%s)" % (ver, "absent" if not pkg.ok else "version %s" % have))
    if not r.read(["test", "-d", pw + "/node_modules/playwright-core"]).ok:
        gone.append("playwright-core")
    listing = r.read(["ls", browsers])
    if not any(n.startswith("chromium") for n in (listing.out or "").split()):
        gone.append("the Chromium bundle")
    if not gone:
        for path in (pw, browsers):
            bad = root_only(r, path)
            if bad:
                raise SetupError("%s is not root's alone: %s is owned by someone else or writable by group or others. A session "
                                 "that could write the browser could say any check passed." % (path, bad))
        return True, "Playwright %s and the Chromium bundle are installed, root-owned, not writable by others" % ver, []
    if not apply_it:
        return False, "would install %s, as root" % ", ".join(gone), []
    for path in (pw, browsers):
        ctx.as_root(["install", "-d", "-o", "root", "-g", "wheel", "-m", "755", path], what="making " + path, why="make " + path)
    env = "PATH=%s:/usr/bin:/bin" % os.path.dirname(c["ACCEPTANCE_NODE"])
    steps = (("installing Playwright %s" % ver, ["env", env, npm, "install", "--prefix", pw, "--ignore-scripts", "--no-audit",
                                                   "--no-fund", "playwright@" + ver]),
             ("installing Chromium", ["env", env, "PLAYWRIGHT_BROWSERS_PATH=" + browsers, c["ACCEPTANCE_NODE"],
                                      pw + "/node_modules/playwright/cli.js", "install", "chromium"]),
             ("giving the folders to root", ["chown", "-R", "root:wheel", pw, browsers]),
             ("closing them to group and others", ["chmod", "-R", "go-w", pw, browsers]))
    for what, argv in steps:
        res = ctx.as_root(argv, what=what, why=what)
        if not res.ok and not res.skipped:
            raise SetupError("%s failed (exit %d): %s" % (what, res.rc, ns._last_line(_s((res.out or "") + "\n" + (res.err or "")))[:150]))
    again = step_browser(ctx, False)
    if not again[0]:
        raise SetupError("the browser was installed, and reading it back still finds it missing: %s" % again[1])
    return False, "installed Playwright %s and Chromium at %s and %s, owned by root" % (ver, pw, browsers), [
        "Playwright's postinstall scripts did not run (--ignore-scripts): the browser came from `playwright install` only."]


def step_config(ctx, apply_it):
    """The live pass's config and the look tool's base config, mode 600 as the role account, and the folders they name."""
    c = ctx.conf
    waits_on_home(ctx)
    docs = compose(c, ctx.role_home)
    files = ((c["ACCEPTANCE_CONFIG"], docs["run"]), (c["ACCEPTANCE_LOOK_BASE"], docs["look_base"]))
    todo = []
    for path, body in files:
        have, mode = qs.read_role_file(ctx, path)
        if have != body or mode != "600":
            todo.append((path, "absent" if have is None else "differs" if have != body else "mode " + mode, body))
    dirs = [role(c, k, ctx.role_home) for k in ("ACCEPTANCE_LOOK_CONFIG_DIR", "ACCEPTANCE_LOOK_RECORD_DIR", "ACCEPTANCE_STATE_DIR")]
    made = ctx.as_role("for d in %s; do [ -d \"$d\" ] || exit 3; done" % " ".join(ns._q(d) for d in dirs), what="looking for its folders")
    if made.ok and not todo:
        ctx.config_current = True
        return True, "both configs are current, mode 600, and accepted by the tools' own loaders; the folders are there", []
    if not apply_it:
        return False, "would write %s%s; the live pass's own loader accepts them" % (
            ", ".join("%s (%s)" % (p, w) for p, w, _b in todo) or "nothing",
            "" if made.ok else " and make the folders") , []
    ctx.as_role("umask 077; mkdir -p %s" % " ".join(ns._q(d) for d in dirs), what="making its folders", why="make its folders (mode 700)")
    for path, _why, body in todo:
        res = ctx.as_role(ns.CONFIG_WRITE_SH.replace("@F@", ns._q(path)), what="writing " + path, stdin=body,
                          why="write %s (mode 600)" % path)
        have, mode = qs.read_role_file(ctx, path) if res.ok else (None, "")
        if have != body or mode != "600":
            raise SetupError("could not write %s as %s, or reading it back does not match (exit %d, mode %s)"
                             % (path, ctx.account, res.rc, mode))
    ctx.config_current = True
    return False, "wrote %s, mode 600" % ", ".join(p for p, _w, _b in todo), []


def root_file_ok(text):
    """True for `uid:mode` (from `stat -f %u:%Lp`) of a file that is root's and that neither its group nor others can write:
    the mode is a number, and no write bit for group or others is set, whatever the other bits are."""
    m = re.match(r"^([0-9]+):([0-7]{1,4})$", (text or "").strip())
    return bool(m) and int(m.group(1)) == 0 and (int(m.group(2), 8) & 0o022) == 0


def step_mcp(ctx, apply_it):
    """The acceptance session's MCP config: root's, outside every worktree, listing the look tool once."""
    c, r = ctx.conf, ctx.runner
    waits_on_home(ctx)
    path, body = c["ACCEPTANCE_MCP_CONFIG"], compose(c, ctx.role_home)["mcp"]
    got = r.read(["cat", path])
    stat = r.read(["stat", "-f", "%u:%Lp", path]) if got.ok else None
    owner_ok = bool(stat and stat.ok and root_file_ok(stat.out))
    if got.ok and got.out == body and owner_ok:
        return True, "%s lists the look tool once, with --config-dir, and is root's, not writable by others" % path, []
    why = "absent" if not got.ok else "differs" if got.out != body else "not root's, or writable by others"
    if not apply_it:
        return False, "would write %s as root (%s)" % (path, why), []
    fd, tmp = tempfile.mkstemp(prefix="acceptance-mcp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        ctx.as_root(["install", "-d", "-o", "root", "-g", "wheel", "-m", "755", os.path.dirname(path)], what="making its folder",
                    why="make " + os.path.dirname(path))
        res = ctx.as_root(["install", "-o", "root", "-g", "wheel", "-m", "644", tmp, path], what="installing " + path,
                          why="install %s (root:wheel 644)" % path)
        if not res.ok and not res.skipped:
            raise SetupError("could not install %s: %s" % (path, (res.err or "").strip()[:200]))
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    if r.read(["cat", path]).out != body:
        raise SetupError("%s was installed, and reading it back does not match" % path)
    return False, "installed %s (root:wheel 644)" % path, []


def read_entries(ctx):
    """([acceptance entries], [every other entry]) read from the dispatcher's config as the role account, as FACTS: the
    config holds tracker tokens, and the program that reads it picks fields. Sets ctx.dispatcher to the facts, minus the
    entries this file owns, which never answer for a repository."""
    cfg = ctx.conf["DISPATCHER_CONFIG"]
    res = ctx.as_role("/usr/bin/python3 -c " + shlex.quote(facts_py(cfg)), what="reading " + cfg)
    try:
        facts = json.loads(res.out) if res.ok else None
    except ValueError:
        facts = None
    if not isinstance(facts, dict):
        raise SetupError("could not read %s as %s, or it did not read back as JSON (exit %d)" % (cfg, ctx.account, res.rc))
    ours = [e for e in facts.get("entries") or [] if str(e.get("id") or "").startswith(ENTRY_PREFIX)]
    others = [e for e in facts.get("entries") or [] if e not in ours]
    ctx.dispatcher = dict(facts, entries=others)
    return ours, others


def step_entry(ctx, apply_it):
    """One `acceptance-<repo>` entry for each repository, beside the dispatcher's other entries, which it never edits."""
    c = ctx.conf
    if ctx.agent:
        raise ctx.not_measured("reading the dispatcher's config")
    cfg = c["DISPATCHER_CONFIG"]
    ours, others = read_entries(ctx)
    want = wanted_entries(c, ctx.dispatcher, se._entries_by_repo(ctx))
    clash = se._tag_ambiguity(others, want)
    if clash:
        raise SetupError("an acceptance tag would reach another entry, and a session that joins two entries runs under only "
                         "the tools BOTH deny:\n  - " + "\n  - ".join(clash))
    have = {e.get("id"): e for e in ours}
    stale = sorted(i for i in have if i not in {w["id"] for w in want})
    todo = [w["id"] for w in want if not entry_matches(have.get(w["id"]), w)]
    if not todo and not stale:
        return True, "%d acceptance entr%s in the dispatcher's config, as this conf produces: %s" % (
            len(want), "y" if len(want) == 1 else "ies", ", ".join(w["id"] for w in want)), []
    what = "would write %s%s" % (", ".join(todo) or "nothing", "; would remove %s" % ", ".join(stale) if stale else "")
    if not apply_it:
        return False, what + " (the others are not touched)", []
    stage = ns.resolve_path("~/.stage-e", ctx.role_home)
    backup = ctx.as_role(se.CONFIG_BACKUP_SH % (stage, shlex.quote(os.path.basename(cfg)), shlex.quote(time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())),
                                                shlex.quote(c["CONFIG_BACKUPS_KEPT"]), shlex.quote(cfg)),
                         what="copying the dispatcher's config first", why="copy the dispatcher's config into the role's own home first")
    if not backup.ok:
        raise SetupError("refusing to write the dispatcher's config with no backup: %s" % (backup.err or "").strip()[:200])
    done = ctx.as_role("/usr/bin/python3 -c " + shlex.quote(se._reconcile_entries_py(cfg)), what="writing the entries",
                       stdin=json.dumps({"entries": want, "remove": stale}), why="write the acceptance entries")
    if not done.ok:
        raise SetupError("could not write the entries (exit %d): %s" % (done.rc, (done.err or "").strip()[:200]))
    ctx.entry_changed = True
    now = {e.get("id"): e for e in read_entries(ctx)[0]}
    if [w["id"] for w in want if not entry_matches(now.get(w["id"]), w)] or sorted(i for i in now if i in stale):
        raise SetupError("the entries were written, and reading the dispatcher's config back does not show them as this conf "
                         "produces them")
    return False, "%s: %s" % (what.replace("would ", "").replace("write ", "wrote "), (done.out or "").strip()[:150]), [
        "The dispatcher reads its entries when it starts. Restart it when no session is running (card CK-A1 says how)."]


def step_job(ctx, apply_it):
    waits_on_home(ctx)
    r, job = ctx.runner, job_spec(ctx.conf, ctx.role_home)
    have = None
    if r.read(["test", "-e", job["plist"]]).ok:
        got = r.read(["cat", job["plist"]])
        if not got.ok:
            raise Unknown("%s exists and could not be read" % job["plist"], "fix its permissions (root:wheel 644)")
        have = got.out
    foreign = qs.foreign_plist(have, job["script"]) if have != job["body"] else None
    if foreign:
        raise SetupError("%s is NOT the acceptance live pass: %s\n  Replacing it would destroy that job, with no backup. Pick a "
                         "label nothing else uses." % (job["plist"], foreign))
    if have == job["body"]:
        ctx.job_current = True
        return True, "the plist is installed and current (never loaded here: CK-A1)", []
    what = "absent" if have is None else "differs"
    if not apply_it:
        return False, "would install %s (%s), not loaded" % (job["plist"], what), []
    if ctx.agent:
        raise ctx.not_measured("installing %s" % job["plist"])
    se._install_plist(r, job["label"], job["body"])
    if r.read(["cat", job["plist"]]).out != job["body"]:
        raise SetupError("%s was installed, and reading it back does not match" % job["plist"])
    ctx.job_current = True
    return False, "installed %s, NOT loaded (CK-A1)" % job["plist"], []


def step_dry_run(ctx, apply_it):
    """The live pass once as the role account, through its own shell command plus --dry-run. It starts nothing, and its
    lines are the list the person reads at CK-A1."""
    if not (ctx.config_current and ctx.job_current):
        waits = " and ".join(n for n, ok in (("config", ctx.config_current), ("job", ctx.job_current)) if not ok)
        if apply_it:
            raise Unknown("waits on %s" % waits, "clear those rows first")
        return False, "waits on %s: a dry run runs the live pass against the config it reads" % waits, []
    if not apply_it:
        return False, "would run the live pass's dry run once as %s" % ctx.account, []
    res = ctx.as_role(run_command(ctx.conf) + " --dry-run", what="running the live pass's dry run", timeout=900)
    lines = [_s(l) for l in ((res.out or "") + "\n" + (res.err or "")).splitlines() if l.strip()]
    say("\n  -- the live pass's dry run, as %s --" % ctx.account)
    for line in lines[-60:]:
        say("    " + line[:200])
    if res.rc not in DRY_OK:
        raise SetupError("the live pass's dry run FAILED as %s (exit %d): %s" % (
            ctx.account, res.rc, ns._last_line(_s((res.out or "") + "\n" + (res.err or "")))[:150]))
    return False, "the live pass's dry run passed as %s (exit %d); its lines are above" % (ctx.account, res.rc), []


def step_enable(ctx, apply_it):
    """Loaded, and running THIS plist: a plist on disk is not what launchd holds."""
    waits_on_home(ctx)
    job = job_spec(ctx.conf, ctx.role_home)
    state, why = qs.loaded_state(ctx, job)
    if state in ("absent", "different"):
        raise Blocked("CK-A1", "system/%s is %s" % (job["label"], "not loaded (a job paused with `disable` reads the same)"
                                                    if state == "absent" else "loaded and running %s: unload it, then load it" % why))
    if state == "unclear":
        raise Unknown("system/%s is loaded, and launchd's print named neither its arguments nor its interval: whether it "
                      "runs THIS plist is not measured" % job["label"], "compare it with its plist:  sudo launchctl print system/" + job["label"])
    return True, "loaded, running its plist's own command: system/%s every %s s" % (job["label"], job["interval"]), []


STEPS = (("preflight", step_preflight), ("profile", step_profile), ("browser", step_browser), ("config", step_config),
         ("mcp-config", step_mcp), ("entry", step_entry), ("job", step_job), ("dry-run", step_dry_run),
         ("enable", step_enable))
_SEVERITY = (FAILED, UNKNOWN, BLOCKED, WOULD_CHANGE, DONE, ALREADY_DONE)
_EXIT = {FAILED: EX_FAILED, UNKNOWN: EX_UNKNOWN, BLOCKED: EX_BLOCKED, WOULD_CHANGE: EX_BLOCKED, DONE: EX_OK,
         ALREADY_DONE: EX_OK}


def run_steps(ctx, apply_it, keep_going, skip=()):
    """(exit code, rows). `run` stops at the first row a person must clear; a dry run and `verify` go on. A step's surprise
    is a FAILED row, never a traceback."""
    rows, held, notes = [], [], []
    for sid, fn in STEPS:
        if sid in skip:
            continue
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


WHAT_CHANGES = {
    "browser": "installs Playwright and Chromium as root",
    "config": "writes the live pass's config and the look tool's base config as the role account",
    "mcp-config": "writes the acceptance session's MCP config as root",
    "entry": "adds or changes acceptance entries in the dispatcher's config, after copying it",
    "job": "installs the live pass's plist",
}


def _ask(prompt):
    try:
        return input(prompt)
    except EOFError:                      # no terminal to answer on is a no
        return ""


def cmd_run(ctx, dry_run, ask=None):
    """Check, show, ask once, apply. Each step reads back what it wrote, and `verify` checks the whole later. A dry run
    changes nothing, and says so: its runner would record a write, and a recorded write is a defect."""
    ask = ask or _ask
    if not dry_run:
        refuse_if_agent("run", ctx.env)
    say("Acceptance installer — %s, role account %s (the job is never loaded here)%s" % (
        "DRY RUN: nothing will be changed" if dry_run else "the only command that changes anything", ctx.account,
        "\n  AGENT ENVIRONMENT (%s set): nothing needing sudo attempted, nothing written" % ", ".join(ctx.agent)
        if ctx.agent else ""))
    if not dry_run:
        say("\nWHAT IS TRUE NOW (nothing is changed yet):")
    code, rows = run_steps(ctx, False, True)
    if ctx.runner.writes:
        say("\nBUG: reading recorded %d write(s); that is a defect in this file." % len(ctx.runner.writes))
        return EX_FAILED
    if dry_run:
        say("\nDRY RUN: nothing was changed.")
        return code
    broken = [sid for sid, outcome, _d in rows if outcome in (FAILED, UNKNOWN)]
    if broken:
        say("\nNOT DONE — fix %s first (above). Nothing was changed." % ", ".join(broken))
        return code
    changing = [sid for sid, outcome, _d in rows if outcome == WOULD_CHANGE and sid in WHAT_CHANGES]
    if changing:
        say("\nThis will: %s. It loads no job and restarts nothing." % "; ".join(WHAT_CHANGES[sid] for sid in changing))
        if ask("Apply these changes? Type yes: ").strip().lower() != "yes":
            say("\nNOT DONE — you did not type yes. Nothing was changed.")
            return EX_BLOCKED
    say("\nAPPLYING:")
    code, rows = run_steps(ctx, True, False)
    say("\n" + ("DONE — every step holds. Nothing merged, approved or labelled by this installer." if code == EX_OK
                else "NOT DONE yet: see above."))
    return code


def cmd_verify(ctx):
    """Every row, read-only. DONE only when each holds; the five things are the rows browser, profile, mcp-config, entry and job."""
    say("Acceptance installer — VERIFY, read-only, role account %s%s" % (ctx.account, "\n  AGENT ENVIRONMENT (%s set): rows that need "
                                                                          "`sudo` are NOT MEASURED" % ", ".join(ctx.agent) if ctx.agent else ""))
    code, rows = run_steps(ctx, False, True, skip=("dry-run",))      # the dry run RUNS the job: it checks nothing here
    if ctx.runner.writes:
        say("\nBUG: verify recorded %d write(s); that is a defect in this file." % len(ctx.runner.writes))
        return EX_FAILED
    say("\n" + ("DONE — every row holds." if code == EX_OK else "NOT DONE — a row above does not hold."))
    return code


def main(argv=None):
    p = argparse.ArgumentParser(prog="pipeline_acceptance_setup.py", description="Install the acceptance check on the role account.")
    p.add_argument("command", nargs="?", default="run", choices=["run", "verify"])
    p.add_argument("--conf", default=DEFAULT_CONF)
    p.add_argument("--dry-run", action="store_true", help="measure every step and change nothing")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)
    if args.selftest:
        from scratch_run import contained   # KIT-450: its temp folders go when it ends
        return contained(selftest, "acceptance-setup-selftest-run-")
    dry = args.dry_run or args.command == "verify"
    try:
        if not dry:        # BEFORE the conf is read: a session must not discover what it would have installed
            refuse_if_agent("run")
        conf, errors = load_conf(args.conf, ns.invoking_user())
        if errors:
            say("Your conf has %d problem(s), all of them:\n  - %s\n\nFix them, then run the same command again."
                % (len(errors), "\n  - ".join(errors)))
            return EX_USAGE
        ctx = Ctx(conf, se.Runner(dry_run=dry))
        ctx.flags = " --conf " + shlex.quote(args.conf) if args.conf != DEFAULT_CONF else ""
        acquire_privilege(ctx, dry, args.command)
        return cmd_verify(ctx) if args.command == "verify" else cmd_run(ctx, args.dry_run)
    except (Refusal, NoPrivilege, SetupError) as exc:
        say(("FAILED: " if isinstance(exc, SetupError) else "") + str(exc))
        return EX_REFUSED if isinstance(exc, Refusal) else EX_NOPRIV if isinstance(exc, NoPrivilege) else EX_USAGE


# ── --selftest: offline; every command stood in for, a stand-in that writes names its path ──────────────────────
FAKE_RUN = (
    "import os, sys\n"
    "flag = os.path.join(os.environ['HOME'], '.fake-exit-run')\n"
    "code = int(open(flag).read()) if os.path.exists(flag) else 0\n"
    "key = os.environ.get('STAGE_E_LINEAR_API_KEY', '')\n"
    "print('acceptance: would start 1 check' if '--dry-run' in sys.argv else 'acceptance: REAL PASS')\n"
    "print('acceptance: %s' % ('done' if code == 0 else 'failed: Authorization: Bearer ' + key))\n"
    "sys.exit(code)\n")
BASE_CONF = {"ROLE_ACCOUNT": "_exacc", "TEAM_KEY": "ENG", "AGENT_USER_ID": "agent-0001", "OWNER_USER_ID": "owner-0001",
             "ACCEPTANCE_REPOS": "acme/app", "ACCEPTANCE_JOB_LABEL": "com.example.acceptance",
             "DISPATCHER_CONFIG": "@ROOT@/dispatcher/config.json", "DISPATCHER_SERVICE": "com.example.dispatcher",
             "ACCEPTANCE_WORKSPACE_DIR": "@ROOT@/dispatcher/worktrees", "ACCEPTANCE_NODE": "@ROOT@/opt/bin/node",
             "ACCEPTANCE_PLAYWRIGHT_VERSION": "1.49.0", "ACCEPTANCE_PLAYWRIGHT_DIR": "@ROOT@/opt/playwright",
             "ACCEPTANCE_BROWSERS_PATH": "@ROOT@/opt/ms-playwright", "ACCEPTANCE_MCP_CONFIG": "@ROOT@/opt/acceptance-mcp.json"}
DISPATCHER_TOKEN = "lin_oauth_" + "ACCDISPATCHERNOTREAL0123456789"        # built at runtime: no fixture looks like a credential


def _slurp(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _put(path, body, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(path, mode)


class FakeMachine(se.Runner):
    """A role home, a clone level with this checkout, an env file, a dispatcher config and a root-owned corner, all in a
    temporary folder. `sudo -u <role> -H /bin/sh -c …` runs through a REAL /bin/sh with HOME there; a root command acts only
    under the folder and records who owns what; the rest is scripted. Nothing outside the folder is touched."""

    def __init__(self, root, conf, key, token):
        se.Runner.__init__(self)
        self.root, self.home, self.checkout = root, root + "/role-home", root + "/checkout"
        self.scripts, self.plists, self.loaded, self.owner = self.checkout + "/scripts", {}, {}, {}
        self.failing, self.sudo_argv, self.role_scripts, self.args, self.sandbox = set(), [], [], True, True
        self.lie_reconcile = self.fail_backup = False
        self.target = se._dispatcher_plist(conf["ACCEPTANCE_JOB_LABEL"])
        for where in (self.home + "/.stage-e/kit", self.checkout):
            for name in required_scripts():
                _put(where + "/scripts/" + name, FAKE_RUN if name == RUN_SCRIPT else "# fake\n")
            _put("%s/%s/%s" % (where, PROFILE_DIR, PROFILE_FILE), "(version 1)\n")
        _put(self.home + "/.stage-e/env", "STAGE_E_LINEAR_API_KEY=%s\nGH_TOKEN=%s\n" % (key, token), 0o600)
        _put(conf["ACCEPTANCE_NODE"], "#!/bin/sh\n", 0o755)
        _put(os.path.dirname(conf["ACCEPTANCE_NODE"]) + "/npm", "#!/bin/sh\n", 0o755)
        _put(root + "/repos/app/.fake-origin", "https://github.com/acme/app.git\n")
        _put(root + "/bin/git", '#!/bin/sh\n[ "$1" = -C ] && [ "$3 $4 $5" = "remote get-url origin" ] && cat "$2/.fake-origin"\n', 0o755)
        self.set_dispatcher(conf, [
            {"id": "app", "name": "app", "repositoryPath": root + "/repos/app", "baseBranch": "main",
             "workspaceBaseDir": conf["ACCEPTANCE_WORKSPACE_DIR"], "linearWorkspaceId": "ws-1",
             "githubUrl": "https://github.com/acme/app", "teamKeys": ["ENG"], "disallowedTools": ["Bash(rm:*)"],
             "linearToken": DISPATCHER_TOKEN, "userAccessControl": {"allowedUsers": ["owner-0001"]}},
            {"id": "reviews-app", "name": "reviews-app", "repositoryPath": root + "/repos/app", "baseBranch": "main",
             "workspaceBaseDir": conf["ACCEPTANCE_WORKSPACE_DIR"], "linearWorkspaceId": "ws-1", "routingLabels": ["reviews-app"],
             "linearToken": DISPATCHER_TOKEN, "disallowedTools": ["Bash", "Write"]}])
        self.dispatcher_path = conf["DISPATCHER_CONFIG"]

    def set_dispatcher(self, conf, entries):
        _put(conf["DISPATCHER_CONFIG"], json.dumps({"repositories": entries, "promptDefaults": {}}), 0o600)

    def load(self):
        self.loaded[os.path.basename(self.target)[:-len(".plist")]] = self.plists[self.target]

    def _in(self, path):
        return path == self.root or path.startswith(self.root + "/")

    def _meta(self, path):
        return self.owner.get(path) or (501, stat.S_IMODE(os.stat(path).st_mode))

    def _walk(self, path):
        yield path
        for here, dirs, files in os.walk(path):
            for n in sorted(dirs + files):
                yield os.path.join(here, n)

    def _make(self, path, body="", uid=0, mode=0o664):
        _put(path, body, mode)
        self.owner[path] = (uid, mode)

    def _exec(self, argv, stdin, timeout, cwd=None, **kwargs):
        argv = list(argv)
        if argv[0] == "sudo":
            self.sudo_argv.append(argv)
        if argv[:2] == ["sudo", "-u"] and argv[3:6] == ["-H", "/bin/sh", "-c"]:
            outside = [p for p in re.findall(r'"(/[^"]*)"', argv[6]) if not p.startswith((self.root, "/tmp/claude-"))]
            if outside:
                return se.Result(99, "", "a role script names a path outside its home: %s" % outside[0])
            self.role_scripts.append(argv[6])
            if self.fail_backup and "/backups" in argv[6]:
                return se.Result(1, "", "the backup folder is full")
            if self.lie_reconcile and "d=json.load(sys.stdin)" in argv[6]:       # says it wrote, writes nothing
                return se.Result(0, "added acceptance-app\n")
            env = {"HOME": self.home, "PATH": self.root + "/bin:/usr/bin:/bin", "TMPDIR": self.root + "/role-tmp"}
            done = subprocess.run(["/bin/sh", "-c", argv[6].replace("/usr/bin/python3", sys.executable)], input=stdin,
                                  capture_output=True, text=True, timeout=timeout, env=env)
            return se.Result(done.returncode, done.stdout, done.stderr)
        if argv[0] == "dscl":
            return se.Result(0, "NFSHomeDirectory: %s\n" % self.home)
        if argv[2:] == ["--selftest"]:
            return se.Result(int(os.path.basename(argv[1]) in self.failing))
        if argv[0] == "plutil":
            return se.Result(0)
        if argv[0] == "test":
            path = argv[2]
            if path in self.plists:
                return se.Result(0)
            if path == SANDBOX_EXEC:
                return se.Result(int(not self.sandbox))
            ok = self._in(path) and {"-e": os.path.exists, "-d": os.path.isdir, "-x": lambda p: os.access(p, os.X_OK)}[argv[1]](path)
            return se.Result(int(not ok))
        if argv[0] == "cat":
            if argv[1] in self.plists:
                return se.Result(0, self.plists[argv[1]])
            if self._in(argv[1]) and os.path.isfile(argv[1]):
                with open(argv[1], encoding="utf-8") as fh:
                    return se.Result(0, fh.read())
            return se.Result(1)
        if argv[0] == "ls":
            return se.Result(0, "\n".join(sorted(os.listdir(argv[1])))) if self._in(argv[1]) and os.path.isdir(argv[1]) else se.Result(1)
        if argv[:2] == ["stat", "-f"]:
            uid, mode = self._meta(argv[3]) if self._in(argv[3]) and os.path.exists(argv[3]) else (None, None)
            return se.Result(0, "%d:%o\n" % (uid, mode)) if uid is not None else se.Result(1)
        if argv[0] == "find":
            if not self._in(argv[1]) or not os.path.exists(argv[1]):
                return se.Result(1, "", "No such file or directory")
            bad = [p for p in self._walk(argv[1]) if self._meta(p)[0] != 0 or self._meta(p)[1] & 0o022]
            return se.Result(0, (bad[0] + "\n") if bad else "")
        if argv[0] == "sudo":
            return self._root(argv[1:])
        return se.Result(1, "", "nothing scripted for %s" % se._fmt(argv)[:100])

    def _root(self, a):
        if a[:2] == ["launchctl", "print"]:
            label = a[2][len("system/"):]
            if label not in self.loaded:
                return se.Result(113, "", "Could not find service")
            doc = plistlib.loads(self.loaded[label].encode("utf-8"))
            shown = ["\targuments = {"] + ["\t\t" + x for x in doc["ProgramArguments"]] + [
                "\t}", "\trun interval = %d seconds" % doc["StartInterval"]] if self.args else []
            return se.Result(0, "\n".join(["system/%s = {" % label] + shown + ["}"]) + "\n")
        paths = [x for x in a if x.startswith("/")]
        if a[:1] == ["install"] and a[1] != "-d":
            paths = paths[:-2] + paths[-1:]                      # the source is the caller's own temp file
        if any(not self._in(p) and p != self.target for p in paths):
            return se.Result(99, "", "a root command names a path outside the fake: %s" % se._fmt(a)[:100])
        if a[:3] == ["install", "-d", "-o"]:
            os.makedirs(a[-1], exist_ok=True)
            self.owner[a[-1]] = (0, 0o755)
            return se.Result(0)
        if a[:1] == ["install"]:
            src, dst = a[-2], a[-1]
            if not src.startswith(tempfile.gettempdir()):
                return se.Result(1, "", "an install from %s" % src)
            with open(src, encoding="utf-8") as fh:
                body = fh.read()
            if dst == self.target:
                self.plists[dst] = body
            else:
                self._make(dst, body, 0, 0o644)
            return se.Result(0)
        if a[:1] == ["env"] and "install" in a and any(x.startswith("playwright@") for x in a):
            where, ver = a[a.index("--prefix") + 1], a[-1].split("@")[1]
            self._make(where + "/node_modules/playwright/package.json", json.dumps({"version": ver}))
            self._make(where + "/node_modules/playwright/cli.js", "// cli\n")
            self._make(where + "/node_modules/playwright-core/package.json", json.dumps({"version": ver}))
            return se.Result(0)
        if a[:1] == ["env"] and a[-2:] == ["install", "chromium"]:
            browsers = [x for x in a if x.startswith("PLAYWRIGHT_BROWSERS_PATH=")][0].split("=", 1)[1]
            self._make(browsers + "/chromium-1100/INSTALLATION_COMPLETE")
            return se.Result(0)
        if a[:3] == ["chown", "-R", "root:wheel"] or a[:3] == ["chmod", "-R", "go-w"]:
            for top in a[3:]:
                for p in self._walk(top):
                    uid, mode = self._meta(p)
                    self.owner[p] = (0, mode) if a[0] == "chown" else (uid, mode & ~0o022)
            return se.Result(0)
        return se.Result(1, "", "nothing scripted for sudo %s" % se._fmt(a)[:100])


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
        with tempfile.TemporaryDirectory(prefix="acceptance-setup-selftest-") as tmp:
            _cases(check, tmp)
    except Exception as exc:                                      # noqa: BLE001
        check("the battery ran to its end", "%s: %s" % (type(exc).__name__, _s(str(exc))[:300]), "")
    finally:
        os.environ.clear()
        os.environ.update(saved)
    print("\n%d/%d checks passed" % (ran[0] - len(fails), ran[0]))
    return 1 if fails else 0


def _cases(check, tmp):
    import ast
    import contextlib
    import io
    key = "lin_" + "api_" + "ACCSELFTESTNOTREAL0123456789abcdef"
    token = "gh" + "p_" + "ACCSELFTESTNOTREAL0123456789abcdefABCD"
    person = "selftest-person"
    ROWS = "(ALREADY-DONE|WOULD-CHANGE|DONE|FAILED|UNKNOWN|BLOCKED-ON-HUMAN)"

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
        return FakeMachine(root, conf, key, token), conf

    def ctx(fake, conf, env=None, dry=False):
        fake.dry_run = dry
        c = Ctx(conf, fake, {} if env is None else env, NoSudo())
        c.checkout_scripts = fake.scripts
        return c

    def rows_of(out):
        return dict(re.findall(r"^  (\S+) +%s " % ROWS, out, re.M))

    asked = []

    def do(fake, conf, what="run", env=None, answer="yes"):
        """One command, in a fake machine: (exit code, {row: outcome}, output). Each is a new process in life, so the
        runner's record of writes starts empty. `run` is asked once for a yes, and `answer` is the person's reply."""
        fake.writes = []
        c = ctx(fake, conf, env, dry=(what != "run"))
        reply = lambda prompt: (asked.append(prompt), answer)[1]                       # noqa: E731
        code, out = quiet(lambda: cmd_verify(c) if what == "verify" else cmd_run(c, what == "dry", reply))
        return code, rows_of(out), out

    def writes(fake):
        return [a for a in fake.sudo_argv if a[1] in ("install", "env", "chown", "chmod")]

    # the conf
    bad = ("ROLE_ACCOUNT=zq9\nROLE_ACCOUNT=zq9\nTEAM_KEY=eng\nACCEPTANCE_JOB_LABEL=zq9\nACCEPTANCE_WORKSPACE_DIR=zq9\n"
           "ACCEPTANCE_MAX_STARTS=zq9\nLOOK_MAX_RUNS=99\nLINEAR_KEY_ENV=zq9\nACCEPTANCE_CONFIG=zq9\nACCEPTANCE_REPOS=zq9\n"
           "ACCEPTANCE_PLAYWRIGHT_VERSION=zq9\nNOT_A_KEY=1\nno equals zq9\n")
    named = ("duplicate ROLE_ACCOUNT", "ACCEPTANCE_MCP_CONFIG is required", "TEAM_KEY must", "ACCEPTANCE_JOB_LABEL is not",
             "ACCEPTANCE_WORKSPACE_DIR must", "ACCEPTANCE_MAX_STARTS must", "LOOK_MAX_RUNS must", "LINEAR_KEY_ENV must",
             "ACCEPTANCE_CONFIG must", "ACCEPTANCE_REPOS must", "ACCEPTANCE_PLAYWRIGHT_VERSION must", "unknown key NOT_A_KEY",
             "not KEY=value")
    secret = parse_conf(text(LINEAR_KEY_ENV=key))[1]
    got = parse(bad)[1]
    check("conf: every fault is named in one pass, by key and never by value; a credential is never shown",
          ([n for n in named if not any(n in e for e in got)], [e for e in got if "zq9" in e], len(secret),
           "CREDENTIAL SHAPE" in secret[0], key in " ".join(secret)), ([], [], 1, True, False))
    check("conf: each rule refuses on its own", [
        refused("ROLE_ACCOUNT must be", ROLE_ACCOUNT="root"), refused("ROLE_ACCOUNT must be", ROLE_ACCOUNT=person),
        refused("ROLE_KIT_CLONE is not Stage E's", ROLE_KIT_CLONE="~/kit"),
        refused("ACCEPTANCE_CONFIG is inside ROLE_KIT_CLONE", ACCEPTANCE_CONFIG="~/.stage-e/kit/a.json"),
        refused("two separate folders", ACCEPTANCE_BROWSERS_PATH="@ROOT@/opt/playwright/browsers"),
        refused("a file of its own", ACCEPTANCE_MCP_CONFIG="@ROOT@/opt/ms-playwright/mcp.json"),
        refused("are one folder", ACCEPTANCE_LOOK_RECORD_DIR="~/.stage-e/acceptance/look-configs"),
        refused("two repositories with one name", ACCEPTANCE_REPOS="acme/app,other/app"),
        refused("ACCEPTANCE_INTERVAL_SECONDS must", ACCEPTANCE_INTERVAL_SECONDS="5"),
        refused("BASE_GIT_DIR must", BASE_GIT_DIR="relative/dir"), refused("BASE_REF must", BASE_REF="../x")], [True] * 11)

    def example():
        with open(os.path.join(os.path.dirname(HERE), "acceptance.conf.example"), encoding="utf-8") as fh:
            values, errs = parse_conf(fh.read())
        more = validate_conf(values, person)[1]
        return errs, sorted(CONF_KEYS ^ set(values)), len(more) >= 1 and all("still the example" in e or "is required" in e for e in more)
    check("conf: the committed example names every key, and only its placeholders are refused", example(), ([], [], True))

    # what it composes
    fake, conf = world()
    docs = compose(conf, fake.home)
    run_doc, base_doc, mcp_doc = (json.loads(docs[k]) for k in ("run", "look_base", "mcp"))
    check("compose: the live pass's config and the look tool's base config are accepted by their own loaders",
          (sorted(run_doc["entries"]), set(base_doc) <= set(look.CONFIG_KEYS), run_doc["look_base_config"].startswith(fake.home)),
          (["acme/app"], True, True))
    check("compose: the MCP config lists the look tool once, started from the clone with --config-dir; its tool is the one the "
          "live pass tells the session to use",
          (list(mcp_doc["mcpServers"]), mcp_doc["mcpServers"][MCP_SERVER]["args"][1:],
           "mcp__%s__%s" % (MCP_SERVER, look.TOOL["name"]) == "mcp__look__look_run"),
          ([MCP_SERVER], ["--config-dir", run_doc["look_config_dir"]], True))
    check("compose: a composition the live pass's loader refuses is refused in its words",
          str(quiet(lambda: compose(dict(conf, TEAM_KEY="x"), fake.home))[0])[:44], "REFUSED by the live pass's own loader: confi")

    # under a model
    fake, conf = world()
    with open(os.path.join(fake.root, "acceptance.conf"), "w", encoding="utf-8") as fh:
        fh.write(text(fake.root))
    os.environ["CLAUDECODE"] = "1"
    try:
        code, out = quiet(lambda: main(["run", "--conf", "no-such-file.conf"]))
        verify_code, vrows, vout = do(fake, conf, "verify", env={"CLAUDECODE": "1"})
    finally:
        del os.environ["CLAUDECODE"]
    check("agent: `run` is refused before the conf is read (even a conf that is not there), and names its markers",
          (code, "REFUSED" in out, "CLAUDECODE" in out, "no-such-file" in out), (EX_REFUSED, True, True, False))
    check("agent: `verify` changes nothing and attempts no sudo; the rows that need the role account are NOT MEASURED, the reads are made",
          (verify_code, fake.sudo_argv, fake.role_scripts, vrows.get("preflight"), vrows.get("browser"), vrows.get("entry")),
          (EX_UNKNOWN, [], [], "UNKNOWN", "WOULD-CHANGE", "UNKNOWN"))

    # a fresh machine
    fake, conf = world()
    code, rows, out = do(fake, conf, "dry")
    check("dry run: changes nothing and attempts no root command; each thing still to do is WOULD-CHANGE",
          (code, writes(fake), fake.plists, os.path.exists(conf["ACCEPTANCE_MCP_CONFIG"]),
           [rows.get(k) for k in ("preflight", "profile", "browser", "config", "mcp-config", "entry", "job")]),
          (EX_BLOCKED, [], {}, False, ["ALREADY-DONE", "ALREADY-DONE", "WOULD-CHANGE", "WOULD-CHANGE", "WOULD-CHANGE",
                                      "WOULD-CHANGE", "WOULD-CHANGE"]))
    before = _slurp(conf["DISPATCHER_CONFIG"])
    code, rows, out = do(fake, conf)
    after = json.loads(_slurp(conf["DISPATCHER_CONFIG"]))
    ours = [e for e in after["repositories"] if e["id"].startswith("acceptance-")]
    kept = json.dumps(after["repositories"][:2], sort_keys=True) == json.dumps(json.loads(before)["repositories"], sort_keys=True)
    check("run: a fresh machine gets all five things, each row DONE, and stops at the card that is a person's (exit 10)",
          (code, [rows.get(k) for k in ("preflight", "profile", "browser", "config", "mcp-config", "entry", "job", "dry-run", "enable")],
           "CK-A1" in out), (EX_BLOCKED, ["ALREADY-DONE", "ALREADY-DONE", "DONE", "DONE", "DONE", "DONE", "DONE", "DONE",
                                         "BLOCKED-ON-HUMAN"], True))
    check("run: the browser is root's and closed to others; the MCP config is root:wheel 644 and exact; the job is installed and "
          "never loaded",
          (root_only(fake, conf["ACCEPTANCE_PLAYWRIGHT_DIR"]), root_only(fake, conf["ACCEPTANCE_BROWSERS_PATH"]),
           fake.owner[conf["ACCEPTANCE_MCP_CONFIG"]], _slurp(conf["ACCEPTANCE_MCP_CONFIG"]) == compose(conf, fake.home)["mcp"],
           sorted(fake.plists) == [fake.target], fake.loaded),
          ("", "", (0, 0o644), True, True, {}))
    check("run: the config files are mode 600; one acceptance entry is added with the coding entry's clone, branch and fence, no URL "
          "or team key to route by; the other entries are untouched; a backup was made",
          (oct(os.stat(fake.home + "/.stage-e/acceptance/acceptance.json").st_mode & 0o777),
           oct(os.stat(fake.home + "/.stage-e/acceptance/look-base.json").st_mode & 0o777),
           [(e["id"], e["repositoryPath"] == fake.root + "/repos/app", e["disallowedTools"], e["routingLabels"], e["mcpConfigPath"] == conf["ACCEPTANCE_MCP_CONFIG"],
             e["userAccessControl"]["allowedUsers"], "githubUrl" in e or "teamKeys" in e) for e in ours], kept,
           len(os.listdir(fake.home + "/.stage-e/backups"))),
          ("0o600", "0o600", [("acceptance-app", True, ["Bash(rm:*)"], ["acceptance-app"], True, ["owner-0001"], False)], True, 1))
    check("run: no output shows a credential or the dispatcher's token, and the dispatcher's config was read as facts",
          (key in out, token in out, DISPATCHER_TOKEN in out, "[REDACTED" in out or "Bearer" not in out), (False, False, False, True))
    root_before = len(writes(fake))
    code2, rows2, out2 = do(fake, conf)
    check("idempotent: a second run attempts no root command and writes nothing; every row is already done up to the card",
          (code2, len(writes(fake)) - root_before, [v for k, v in rows2.items() if k not in ("dry-run", "enable")]),
          (EX_BLOCKED, 0, ["ALREADY-DONE"] * 7))
    fake.load()
    code3, rows3, out3 = do(fake, conf)
    vcode, vrows, vout = do(fake, conf, "verify")
    check("done: once the person loads the job, `run` ends DONE; `verify` reports the five things as rows and says DONE",
          (code3, "DONE — every step holds" in out3, vcode, [vrows.get(k) for k in ("browser", "profile", "mcp-config", "entry", "job")],
           "DONE — every row holds" in vout), (EX_OK, True, EX_OK, ["ALREADY-DONE"] * 5, True))

    # the one yes
    def declined(answer):
        f, cf = world()
        del asked[:]
        c, r, o = do(f, cf, answer=answer)
        return (c, len(asked), writes(f), f.plists, os.path.exists(cf["ACCEPTANCE_MCP_CONFIG"]), os.path.exists(cf["ACCEPTANCE_BROWSERS_PATH"]),
                _slurp(cf["DISPATCHER_CONFIG"]) == json.dumps({"repositories": json.loads(_slurp(cf["DISPATCHER_CONFIG"]))["repositories"],
                                                               "promptDefaults": {}}),
                "NOT DONE" in o, "Nothing was changed" in o, "APPLYING" in o, "WHAT IS TRUE NOW" in o)
    for answer in ("", "y", "no", "yes please", "nope", "ok"):
        check("yes: %r is not a yes: nothing is changed, it ends NOT DONE and says so, and it asked once, after showing what is true" % answer,
              declined(answer), (EX_BLOCKED, 1, [], {}, False, False, True, True, True, False, True))
    fake, conf = world()
    del asked[:]
    code, rows, out = do(fake, conf, answer=" Yes ")
    check("yes: a typed yes (any case) applies, after the plan is shown and the person is told what will change",
          (code, len(asked), rows.get("browser"), out.index("WHAT IS TRUE NOW") < out.index("This will:") < out.index("APPLYING"),
           "installs Playwright" in out and "adds or changes acceptance entries" in out and "loads no job" in out),
          (EX_BLOCKED, 1, "DONE", True, True))
    fake.load()
    del asked[:]
    code, rows, out = do(fake, conf)
    check("yes: with nothing to change it asks nothing, and still ends DONE", (code, asked, "APPLYING" in out), (EX_OK, [], True))
    fake, conf = world()
    do(fake, conf)
    fake.owner[conf["ACCEPTANCE_BROWSERS_PATH"] + "/chromium-1100/INSTALLATION_COMPLETE"] = (0, 0o666)    # a bundle a group could write
    fake.plists.clear()                       # and another row that would change, so the fixed-first rule has something to hold back
    del asked[:]
    done_before = len(writes(fake))
    code, rows, out = do(fake, conf)
    check("yes: a row that FAILED in the plan is fixed first: nothing is asked and nothing is changed",
          (code, asked, len(writes(fake)) - done_before, rows.get("browser")), (EX_FAILED, [], 0, "FAILED"))
    fake, conf = world()
    del asked[:]
    for what in ("verify", "dry"):
        do(fake, conf, what)
    check("yes: `verify` and `run --dry-run` never ask", asked, [])
    import builtins
    real_input = builtins.input

    def eof(_prompt=""):
        raise EOFError
    builtins.input = eof
    try:
        fake, conf = world()
        c = ctx(fake, conf)
        code, out = quiet(lambda: cmd_run(c, False))
    finally:
        builtins.input = real_input
    check("yes: no terminal to answer on (end of input) is a no: nothing is changed", (code, writes(fake), "NOT DONE" in out), (EX_BLOCKED, [], True))

    def broke(name, damage, row):
        f, cf = world()
        do(f, cf)
        f.load()
        damage(f, cf)
        c, r, _o = do(f, cf, "verify")
        others = [r.get(k) for k in ("browser", "profile", "mcp-config", "entry", "job") if k != row]
        check("verify: %s is its own row, and only it fails" % name, (c != EX_OK, r.get(row) not in (None, "ALREADY-DONE"), others),
              (True, True, ["ALREADY-DONE"] * 4))
    broke("the browser bundle", lambda f, cf: shutil.rmtree(cf["ACCEPTANCE_BROWSERS_PATH"] + "/chromium-1100"), "browser")
    broke("the profile", lambda f, cf: _put(f.home + "/.stage-e/kit/" + PROFILE_DIR + "/" + PROFILE_FILE, "(version 2)\n"), "profile")
    broke("the MCP config", lambda f, cf: _put(cf["ACCEPTANCE_MCP_CONFIG"], "{}\n"), "mcp-config")
    broke("the routing entry", lambda f, cf: f.set_dispatcher(cf, [e for e in json.loads(_slurp(cf["DISPATCHER_CONFIG"]))["repositories"]
                                                                   if not e["id"].startswith("acceptance-")]), "entry")
    broke("the job", lambda f, cf: f.plists.clear() or f.loaded.clear(), "job")

    def changed(name, damage, row):
        f, cf = world()
        do(f, cf)
        f.load()
        damage(f, cf)
        _c, r, _o = do(f, cf, "verify")
        check("verify: %s is found, not taken for current" % name, r.get(row), "WOULD-CHANGE")
    changed("an MCP config nobody but root should be able to write, owned by someone else",
            lambda f, cf: f.owner.__setitem__(cf["ACCEPTANCE_MCP_CONFIG"], (501, 0o644)), "mcp-config")
    changed("an MCP config a group can write", lambda f, cf: f.owner.__setitem__(cf["ACCEPTANCE_MCP_CONFIG"], (0, 0o664)), "mcp-config")
    for mode in (0o642, 0o624, 0o622, 0o666, 0o777):          # every mode with a write bit for group or others, not just the common ones
        changed("an MCP config at mode %o, which others or a group can write" % mode,
                lambda f, cf, m=mode: f.owner.__setitem__(cf["ACCEPTANCE_MCP_CONFIG"], (0, m)), "mcp-config")
    changed("a config the role account's others can read", lambda f, cf: os.chmod(f.home + "/.stage-e/acceptance/acceptance.json", 0o644), "config")
    changed("an acceptance entry that routes by a URL", lambda f, cf: f.set_dispatcher(cf, [
        dict(e, githubUrl="https://github.com/acme/app") if e["id"].startswith("acceptance-") else e
        for e in json.loads(_slurp(cf["DISPATCHER_CONFIG"]))["repositories"]]), "entry")

    # the browser row
    fake, conf = world()
    do(fake, conf)
    shutil.rmtree(conf["ACCEPTANCE_PLAYWRIGHT_DIR"] + "/node_modules/playwright-core")
    _c, r_core, _o = do(fake, conf, "verify")
    _put(conf["ACCEPTANCE_PLAYWRIGHT_DIR"] + "/node_modules/playwright-core/package.json", "{}")
    fake.owner[conf["ACCEPTANCE_BROWSERS_PATH"] + "/chromium-1100/INSTALLATION_COMPLETE"] = (0, 0o666)
    code, r_open, out_open = do(fake, conf)
    check("browser: a missing playwright-core is found; a bundle file a group could write is a FAILED row that names it and stops",
          (r_core.get("browser"), r_open.get("browser"), "INSTALLATION_COMPLETE" in out_open, code), ("WOULD-CHANGE", "FAILED", True, EX_FAILED))
    fake, conf = world()
    do(fake, conf)
    _put(conf["ACCEPTANCE_PLAYWRIGHT_DIR"] + "/node_modules/playwright/package.json", json.dumps({"version": "1.0.0"}))
    _c, r_old, o_old = do(fake, conf, "dry")
    check("browser: another Playwright version than the conf's is installed again", (r_old.get("browser"), "Playwright 1.49.0 (version 1.0.0)" in o_old),
          ("WOULD-CHANGE", True))
    fake, conf = world()
    do(fake, conf)
    argvs = [" ".join(a) for a in fake.sudo_argv if a[1] == "env"]
    check("browser: npm runs as root with the install scripts off, from the node's own folder; the browser install names its folder",
          ("--ignore-scripts" in argvs[0], os.path.dirname(conf["ACCEPTANCE_NODE"]) in argvs[0], "PLAYWRIGHT_BROWSERS_PATH=" + conf["ACCEPTANCE_BROWSERS_PATH"] in argvs[1]),
          (True, True, True))

    # the entry row
    def entry_error(entries=None, **over):
        f, cf = world(**over)
        if entries is not None:
            f.set_dispatcher(cf, entries(f, cf))
        return do(f, cf, "dry")[2]
    coding = lambda f, cf, **kw: dict({"id": "app", "name": "app", "repositoryPath": f.root + "/repos/app", "baseBranch": "main",   # noqa: E731
                                        "workspaceBaseDir": cf["ACCEPTANCE_WORKSPACE_DIR"], "linearWorkspaceId": "ws-1"}, **kw)
    check("entry: an entry another entry's name could answer to is refused, naming both",
          "would ALSO match the entry 'trap'" in entry_error(lambda f, cf: [coding(f, cf), {"id": "trap", "name": "ACCEPTANCE-app"}]), True)
    check("entry: a repository the dispatcher manages no clone of is refused", "manages no clone of acme/other" in entry_error(
        ACCEPTANCE_REPOS="acme/app,acme/other"), True)
    check("entry: a workspace folder that is not the dispatcher's is refused, naming both",
          "the conf says" in entry_error(lambda f, cf: [coding(f, cf, workspaceBaseDir=f.root + "/elsewhere")]), True)
    check("entry: two workspace ids are refused rather than guessed",
          "disagree about workspaceBaseDir or the workspace id" in entry_error(lambda f, cf: [coding(f, cf), coding(f, cf, id="b", name="b", linearWorkspaceId="ws-2")]), True)
    fake, conf = world()
    do(fake, conf)
    fake.set_dispatcher(conf, json.loads(_slurp(conf["DISPATCHER_CONFIG"]))["repositories"] + [
        {"id": "acceptance-gone", "name": "acceptance-gone", "repositoryPath": fake.root + "/repos/app"}])
    _c, r_stale, o_stale = do(fake, conf, "dry")
    do(fake, conf)
    left = [e["id"] for e in json.loads(_slurp(conf["DISPATCHER_CONFIG"]))["repositories"]]
    check("entry: an acceptance entry for a repository no longer in the conf is removed, and no other entry is",
          (r_stale.get("entry"), "would remove acceptance-gone" in o_stale, left), ("WOULD-CHANGE", True, ["app", "reviews-app", "acceptance-app"]))

    for what, flag, want in (("a reconcile that says it wrote and wrote nothing", "lie_reconcile", "reading the dispatcher's config back"),
                             ("a backup that cannot be made", "fail_backup", "no backup")):
        fake, conf = world()
        setattr(fake, flag, True)
        before = _slurp(conf["DISPATCHER_CONFIG"])
        _c, r_e, o_e = do(fake, conf)
        check("entry: %s fails the row and says why%s" % (what, "; the config is untouched" if flag == "fail_backup" else ""),
              (r_e.get("entry"), want in o_e, flag != "fail_backup" or _slurp(conf["DISPATCHER_CONFIG"]) == before), ("FAILED", True, True))

    # the preflight, the profile and the job
    fake, conf = world()
    _put(fake.home + "/.stage-e/kit/scripts/" + RUN_SCRIPT, "# older\n")
    fake.failing.add(LOOK_SCRIPT)
    _put(fake.home + "/.stage-e/env", "STAGE_E_LINEAR_API_KEY=%s\n" % key, 0o644)
    _c, r_pre, o_pre = do(fake, conf, "dry")
    check("preflight: every problem is named at once: a clone behind, a failing selftest, an env file open to others and missing a name",
          (r_pre.get("preflight"), "not level with this checkout (%s)" % RUN_SCRIPT in o_pre, "selftest FAILED from this checkout: %s" % LOOK_SCRIPT in o_pre,
           "is mode 644, or has no usable" in o_pre), ("FAILED", True, True, True))
    fake, conf = world()
    fake.sandbox = False
    _c, r_sb, o_sb = do(fake, conf, "dry")
    check("profile: a Mac with no sandbox-exec is a FAILED row", (r_sb.get("profile"), SANDBOX_EXEC in o_sb), ("FAILED", True))
    fake, conf = world()
    fake.plists[fake.target] = plistlib.dumps({"Label": conf["ACCEPTANCE_JOB_LABEL"], "ProgramArguments": ["/bin/other-job"]}).decode()
    code, r_for, o_for = do(fake, conf)
    check("job: a plist at the label that is not this job's is refused, never replaced", (r_for.get("job"), "is NOT the acceptance live pass" in o_for),
          ("FAILED", True))
    fake, conf = world()
    do(fake, conf)
    plist = plistlib.loads(fake.plists[fake.target].encode("utf-8"))
    check("job: the plist runs the live pass from the role's clone with the config, every this-Mac fact from the conf",
          (plist["Label"], plist["StartInterval"], any(RUN_SCRIPT in a for a in plist["ProgramArguments"]) or RUN_SCRIPT in " ".join(plist["ProgramArguments"]),
           "run --config" in " ".join(plist["ProgramArguments"])), (conf["ACCEPTANCE_JOB_LABEL"], 300, True, True))
    flag = fake.home + "/.fake-exit-run"
    _put(flag, "1\n")
    fake.load()
    code, r_dry, o_dry = do(fake, conf)
    check("dry-run row: a failing dry run is a FAILED row, and a credential in its output is scrubbed",
          (r_dry.get("dry-run"), key in o_dry, token in o_dry), ("FAILED", False, False))

    # pins
    names = required_scripts()
    check("pins: the clone check covers the live pass, the poller, the look tool, what they import and the runner file; the "
          "scripts' own selftests' imports (the bridge, the notifier, the reviewers) are left out",
          (all(n in names for n in (RUN_SCRIPT, "pipeline_acceptance_poller.py", LOOK_SCRIPT, RUNNER_FILE, "jsonschema_mini.py",
                                     "pipeline_routing_tags.py", "scratch_run.py")),
           [n for n in names if n.startswith(("pipeline_bridge", "pipeline_notify", "pipeline_review"))]), (True, []))
    check("pins: the entry name, the profile path and the tool's name are the live pass's and the look tool's own",
          (ENTRY_PREFIX == arun.ENTRY_PREFIX, look.PROFILE.endswith("/%s/%s" % (PROFILE_DIR, PROFILE_FILE)), look.TOOL["name"]),
          (True, True, "look_run"))


if __name__ == "__main__":
    sys.exit(main())
