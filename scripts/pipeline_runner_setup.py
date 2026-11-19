#!/usr/bin/env python3
"""The runner's installer (KIT-859): its settings, with every lane off for every project, and its
always-on job, checked or applied with its answers given (KIT-655). Guide: docs/RUNNER.md.

    python3 scripts/pipeline_runner_setup.py check [--conf runner.conf]
    echo '{"schema": "runner-answers/1", "consent": "yes"}' | python3 scripts/pipeline_runner_setup.py apply
    python3 scripts/pipeline_runner_setup.py --selftest

Both print one JSON document of rows on stdout, on every exit, and every human word on stderr, as the
notifier's installer does (KIT-850). `check` reads and changes nothing. It asks nothing: every sudo
call is `sudo -n`, so with no live sudo session, or under a model, a row that needs one says UNKNOWN,
never current. Its rows: the role account's home; the kit the job runs (the clone's commit, the
runner's scripts level with this checkout); the runner's settings as installed, with each project's
lanes; and the job (its file, whether it starts at boot, what launchd runs).

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

ITS JOB.  One LaunchDaemon as the role account, kept alive, running the runner from Stage E's kit
clone, which Stage E's `run` moves and restarts it around. The file says Disabled, so the file alone
never starts at boot: only `launchctl enable` does. The command ends with a shell comment naming the
clone's commit, so a moved clone is a changed job, and launchd's own copy says which kit it runs.

Exits: 0 done or current · 1 failed · 2 settings · 3 refused · 4 not measured · 5 no live sudo
(apply) · 10 work to do (check), or not agreed, or left off.
"""
import argparse
import hashlib
import json
import os
import plistlib
import re
import stat
import sys
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
import pipeline_runner_store as store  # noqa: E402
import pipeline_queue_pool as pool  # noqa: E402

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
                 "TOKEN_ENV": "CLAUDE_WRITER_OAUTH_TOKEN"}
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
        elif key not in CONF_REQUIRED and key not in CONF_DEFAULTS:
            errors.append("%s: unknown key %s" % (where, key))
        elif ns.credential_shape(val):
            errors.append("%s: %s carries a CREDENTIAL SHAPE; no key here holds one (not shown)" % (where, key))
        else:
            seen[key], values[key] = n, val
    return values, errors


def validate_conf(values, user=None):
    """(conf, errors): every fault in one pass, by key; the runner's own loader judges the rest."""
    c, e = dict(CONF_DEFAULTS, **values), []

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
    bad = [k for k in ("ROLE_KIT_CLONE", "ENV_FILE", "RUNNER_DIR") if not path_ok(c[k])]
    e.extend("%s must be `~/…` (the role account's home) or an absolute path, with no `..`" % k for k in bad)
    need("ROLE_KIT_CLONE" in bad or c["ROLE_KIT_CLONE"].rstrip("/") == se.ROLE_KIT_CLONE_PATH,
         "ROLE_KIT_CLONE is not Stage E's clone, %s" % se.ROLE_KIT_CLONE_PATH)
    items = projects(c.get("PROJECTS"))[1]
    need(not items, "PROJECTS item %s is not KEY=PATH@BASE (a lower-case key, the runner's own clone, its "
                    "base branch), or repeats a KEY" % ", ".join(map(str, items)))
    e.extend(path_problems(c, ns.ROLE_HOME_SENTINEL, bad))
    return c, e


def path_problems(c, home, skip=()):
    """RUNNER_DIR inside the clone, a git working tree a session can write, or holding the env file."""
    p = dict((k, ns.resolve_path(c[k], home)) for k in ("ROLE_KIT_CLONE", "ENV_FILE", "RUNNER_DIR") if k not in skip)
    under = lambda a, b: a in p and b in p and ns._under(p[a], p[b])      # noqa: E731
    return ["RUNNER_DIR %s" % why for bad, why in (
        (under("RUNNER_DIR", "ROLE_KIT_CLONE"), "is inside ROLE_KIT_CLONE, a git working tree"),
        (under("ENV_FILE", "RUNNER_DIR"), "holds ENV_FILE")) if bad]


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
         "owner_user_ids", "dispatcher_user_ids", "projects")
PROJECT_OWNED = ("repo", "base")


def compose(c, on, was=None):
    """The runner's settings: the keys it owns from the conf, the rest of `was` (the installed settings) as it
    is. A project the conf names keeps its own lanes, approved states and hosts; a new one gets every lane off.
    No line here switches a lane on."""
    d, was = c["RUNNER_DIR"].rstrip("/"), was if isinstance(was, dict) else {}
    old = was.get("projects") if isinstance(was.get("projects"), dict) else {}
    doc = dict((k, v) for k, v in was.items() if k not in OWNED)
    doc.update({"enabled": bool(on), "store_dir": d + "/store", "work_dir": d + "/work", "session_home": d + "/home",
                "claude_bin": c["CLAUDE_BIN"], "token_env": c["TOKEN_ENV"], "linear_key_file": d + "/tracker-key",
                "owner_user_ids": split_list(c["OWNER_USER_IDS"]),
                "dispatcher_user_ids": split_list(c["DISPATCHER_USER_IDS"]), "projects": {}})
    for k, (repo, base) in sorted(projects(c["PROJECTS"])[0].items()):
        mine = old.get(k) if isinstance(old.get(k), dict) else {}
        p = dict((pk, pv) for pk, pv in mine.items() if pk not in PROJECT_OWNED)
        p.setdefault("lanes", dict.fromkeys(store.KINDS, False))
        doc["projects"][k] = dict(p, repo=repo, base=base)
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
    script = c["ROLE_KIT_CLONE"].rstrip("/") + "/scripts/" + RUNNER_SCRIPT
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


def agent_markers(env):
    return [m for m in AGENT_ENV_MARKERS if m in env]      # presence, not truthiness


class Ctx(object):
    def __init__(self, conf, runner, env, live, ceiling, scripts=HERE):
        self.conf, self.runner, self.live, self.ceiling, self.scripts = conf, runner, live, ceiling, scripts
        self.agent = agent_markers(env)
        self.home = self.commit = self.on = self.left_off = self.first = None

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

    def as_root(self, argv, what, why=None):
        self._may(what)
        return self.runner.as_root(argv, why=why)


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


STEPS = (("home", step_home), ("kit", step_kit), ("settings", step_settings), ("job", step_job))
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


def main(argv=None, env=None, stdin=None, machine=None):
    """`check` or `apply`: every human word on stderr, and ONE JSON document of rows on stdout, on every exit
    past the arguments, so a program never has to tell prose from rows. `machine` stands in for (runner, sudo
    live, this checkout's scripts, the folder its parts list is read from) in the selftest."""
    p = argparse.ArgumentParser(prog="pipeline_runner_setup.py", description="Install the runner, every lane off.")
    p.add_argument("command", nargs="?", default="check", choices=["check", "apply"])
    p.add_argument("--conf", default=DEFAULT_CONF)
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)
    if args.selftest:
        from scratch_run import contained   # KIT-450: its temp folders go when it ends
        import pipeline_root_guard as _rg   # KIT-693: the root case ends every installer's own selftest
        return contained(selftest, "runner-setup-selftest-run-") or _rg.root_case_rc(main, ["check"])
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
    """A role home in a folder: Stage E's clone (a real git repository) level with a fake checkout, and launchd
    as data. `sudo -n -u <role> -H /bin/sh -c` runs a REAL /bin/sh with HOME there. A sudo without `-n` would
    ask for a password, so it fails here."""

    def __init__(self, root):
        Runner.__init__(self)
        self.root, self.home, self.scripts = root, root + "/role-home", root + "/checkout"
        self.plists, self.loaded, self.boot, self.sudo, self.print_rc = {}, {}, {}, [], None
        for where in (self.home + "/.stage-e/kit/scripts", self.scripts):
            for name in RUNNER_SCRIPTS:
                _put(where + "/" + name, "# a stand-in for %s\n" % name)
        self.commit("first")

    def commit(self, message):
        import subprocess
        for argv in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@example.invalid",
                                                     "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
                                                     "commit", "-q", "--allow-empty", "-m", message]):
            subprocess.run(["git", "-C", self.home + "/.stage-e/kit"] + argv, check=True, capture_output=True,
                           env=dict(_GIT_ENV, HOME=self.home))

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
    saved, pause = dict(os.environ), se._pause
    for marker in AGENT_ENV_MARKERS:                # the battery owns its environment
        os.environ.pop(marker, None)
    se._pause = lambda seconds: None
    try:
        with tempfile.TemporaryDirectory(prefix="runner-setup-selftest-") as tmp:
            _cases(check, tmp)
    except Exception as exc:                                      # noqa: BLE001
        check("the battery ran to its end", "%s: %s" % (type(exc).__name__, str(exc)[:300]), "")
    finally:
        se._pause = pause
        os.environ.clear()
        os.environ.update(saved)
    print("\n%d/%d checks passed" % (ran[0] - len(fails), ran[0]))
    return 1 if fails else 0


def _cases(check, tmp):  # noqa: C901  (one battery, read top to bottom)
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
        _put(root + "/runner.conf", text(**over))
        return FakeMachine(root)

    def go(fake, command="check", answers=yes, env=None, live=True, stdin=None, parts=KIT):
        """(exit, the one document on stdout, its words on stderr, what is left unread on stdin)."""
        out, err, stream = io.StringIO(), io.StringIO(), stdin or pipe_of(answers)
        fake.dry_run, fake.writes = command == "check", []
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main([command, "--conf", fake.root + "/runner.conf"], env=env or {}, stdin=stream,
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
         fresh_check, (10, 10, {"home": ALREADY_DONE, "kit": ALREADY_DONE, "settings": WOULD_CHANGE,
                                "job": WOULD_CHANGE}, True, [], None, []),
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

    # 13. The parts list's own ceiling and commands.
    import pipeline_parts
    part = [p for p in pipeline_parts.parts_of(pipeline_parts.load(KIT)[0]) if p["id"] == "runner"][0]
    check("the parts list names this installer's check and apply, and a ceiling",
          (part["check"]["args"][0], part["apply"]["args"][0], part_ceiling() == part["ceiling"] is not None),
          ("check", "apply", True))


if __name__ == "__main__":
    sys.exit(main())
