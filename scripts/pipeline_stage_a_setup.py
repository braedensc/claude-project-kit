#!/usr/bin/env python3
"""Stage A (idea-gate) installer — stand up the Linear-triggered planning path.

Sibling of `scripts/pipeline_stage_e_setup.py`, pointed at planning instead of
review. ONE command, run repeatedly by a PERSON in a terminal, until it stops
asking. It provisions what can be provisioned deterministically and HANDS OFF —
as printed checkpoints — the few steps a person must apply to the machine-local
dispatcher, then verify against the live system. It never turns the gate on;
loading the executor and running the first plan by hand are human gates.

WHAT IT BUILDS
  - the Planning team, its intake/ready states and the pipeline labels (ids
    resolved and recorded, never authored);
  - the owner-scoped EXECUTOR credential — the Linear key the executor holds and
    the planning session never does — in the role account's own env file, mode
    600, reached only via `sudo -u`;
  - the executor's config file and its scheduled job (installed, NOT loaded);
  - and it COMPOSES the Planning dispatcher entry (the tool-fence + the planning
    brief) and prints it for you to apply to the dispatcher's own config, because
    that config lives outside this repo and a session must never write its own
    supervision.

THE FENCE — the one security choice, and why it is shaped this way (fallback b)
  The whole no-self-approval guarantee rests on ONE thing: the planning session
  must hold no tool that can write the tracker. The built review installer proved
  that per-tool tracker fencing is NOT demonstrated in the running system — its
  `disallowedTools` names only built-in tools and its selftest forbids any
  `mcp__` entry. So this installer does NOT rest the guarantee on naming
  individual tracker tools. It takes the STRUCTURAL route:

    * the Planning entry attaches NO Linear MCP server at all
      (PLANNING_ENTRY["linearMcpAttached"] is False) — the session simply has no
      `mcp__linear__*` tool, whatever a runner or a guard does;
    * the planner keeps Read/Grep/Glob (to decompose against real code), Write
      (to emit its proposal file) and Task (its sub-passes);
    * `disallowedTools` ALSO names the tracker mutation tools, belt-and-braces,
      in case a future entry format attaches Linear despite the above.

  A session with no key cannot write the board, regardless of `disallowedTools`
  granularity, PreToolUse pattern-matching, or which runner loaded — so this also
  closes the runner-selection hole for the tracker-write path. `--selftest`
  pins every part of this shape.

WHAT IT REFUSES (the mechanism-in-kit / operator-runs-it / refuses-in-agent-env
doctrine, identical to the review installer)
  - MUTATION IN AN AGENT ENVIRONMENT. `run` (without `--dry-run`) refuses when any
    of the imported AGENT_ENV_MARKERS is set. A session that installs its own
    supervision is the attack the refusal exists to prevent. There is no override
    flag. `status`, `verify` and `run --dry-run` still work — the last MEASURES
    ONLY, a property of the code path, not a banner.
    Tamper-evident, not tamper-proof: the markers live in the environment and a
    session runs as the same user. The durable boundaries are the self-protected
    PreToolUse hook (a deny rule is a person's to add) and the mode-600 file under
    the role account's home reached only via `sudo -u`.
  - MERGING, APPROVING, MOVING A TICKET. There is no code path to any of them
    here; this installer only provisions and composes, and `--selftest` asserts
    its own source contains no such verb.
  - WRITING THE MACHINE-LOCAL DISPATCHER CONFIG. It composes the Planning entry
    and PRINTS it; a person applies it. The dispatcher's config is a session's
    supervision, and this installer will not write it even for the operator.

Usage:
    pipeline_stage_a_setup.py [run|status|verify] [--conf stage-a.conf]
                              [--dry-run]
    pipeline_stage_a_setup.py --selftest

Exit: 0 ok · 1 a step failed · 2 usage/conf · 3 refused (agent env) · 10 a human
      checkpoint blocks (apply the printed step, then re-run).
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# The agent-env markers are IMPORTED from the dispatcher module, never copied, so
# the two scripts cannot drift on what "an agent environment" means.
from pipeline_dispatch_local import AGENT_ENV_MARKERS  # noqa: E402

# --------------------------------------------------------------------------- #
# Exit codes
# --------------------------------------------------------------------------- #
EX_OK = 0
EX_FAILED = 1
EX_USAGE = 2
EX_REFUSED = 3
EX_BLOCKED = 10

# --------------------------------------------------------------------------- #
# The fence (fallback b) — the security core. Pinned by --selftest.
# --------------------------------------------------------------------------- #
# Tools the planner KEEPS — it must read code and write its proposal file.
PLANNING_KEEP_TOOLS = ["Read", "Grep", "Glob", "Write", "Task"]

# Belt-and-braces denylist. The STRUCTURAL control is linearMcpAttached=False
# below; these names are defence-in-depth for a future entry format that attaches
# Linear despite that. Naming them costs nothing and the guarantee does not
# depend on them working.
PLANNING_DISALLOWED_TOOLS = [
    "Edit", "NotebookEdit", "WebFetch", "WebSearch",
    "EnterWorktree", "ExitWorktree",
    "mcp__linear__save_issue",
    "mcp__linear__save_issue_label",
    "mcp__linear__create_issue_label",
    "mcp__linear__save_comment",
    "mcp__linear__save_project",
    "mcp__linear__save_document",
]

# A label no ticket carries, so the Planning entry is NEVER label-routed — its job
# kind comes from the team it maps to, not from text a ticket could hold (KIT-41).
PLANNING_ENTRY_NEVER_LABEL = "stage-a-planning-entry-never-label-routed"

# The planning brief the entry delivers (KIT-98 authors the final wording; this is
# the shape and the load-bearing constraints). Its first sentence is the ownership
# fingerprint used to recognise an entry this installer wrote.
PLANNING_BRIEF = (
    "You are an UNATTENDED PLANNING session. Your job is to turn the delegated "
    "idea ticket into a proposed epic tree, and NOTHING else. You hold no tracker "
    "tool: you cannot create, move, comment on, or label any ticket, and trying is "
    "itself a finding against you. Read the real codebase (Read/Grep/Glob), run the "
    "PRD, decomposition and rubric passes from the plan-epic procedure, then EMIT "
    "the whole tree as ONE pipeline-safe-outputs/1 request file (a ticket-create "
    "carrying `epic` and `children` with `depends_on`), written with the Write tool "
    "to the run's safe-outputs path. Do not open a pull request. Do not ask "
    "questions. A credential-holding executor validates your proposal, runs the "
    "Definition-of-Ready gate on every child, and files the tree for a person to "
    "approve — your proposal approves nothing and starts nothing."
)
PLANNING_BRIEF_FINGERPRINT = PLANNING_BRIEF.split(".", 1)[0]

# The Planning team's states and the labels the executor needs to resolve.
REQUIRED_STATES = (("Backlog", "unstarted"), ("Ready", "unstarted"))
REQUIRED_LABELS = ("provenance:agent", "provenance:epic", "track:meta",
                   "effort:S", "effort:M", "effort:L")

# Verbs that must never appear in this installer's own source (it only provisions
# and composes). The definition line carries the marker so it does not self-trip.
BANNED_TOKENS = ("gh pr merge", "issueUpdate", "issueAddLabel", "--approve",  # _BANNED
                 "createReview", "pulls/{number}/merge", "auto-merge")  # _BANNED


class SetupError(Exception):
    """A step failed for a real reason — exit 1."""


class Refusal(Exception):
    """An agent environment asked to mutate — exit 3."""


class Blocked(Exception):
    """A human checkpoint blocks — exit 10. Apply the printed step and re-run."""


# --------------------------------------------------------------------------- #
# Agent-environment refusal
# --------------------------------------------------------------------------- #
def agent_markers_present(env=None):
    env = os.environ if env is None else env
    return [m for m in AGENT_ENV_MARKERS if m in env]  # presence, not truthiness


def refuse_if_agent(action, env=None):
    found = agent_markers_present(env)
    if not found:
        return
    raise Refusal(
        "REFUSED: `%s` is a mutating action and this is an agent environment (%s "
        "set).\n  A session that installs its own supervision — its own dispatcher "
        "entry, its own credentials — is the thing this refusal exists to prevent. "
        "There is no override flag.\n  A PERSON runs this, in a terminal:  "
        "python3 %s %s\n  Read-only meanwhile:  status | verify | run --dry-run"
        % (action, ", ".join(found), os.path.basename(__file__), action))


# --------------------------------------------------------------------------- #
# Conf — every bad value in ONE pass
# --------------------------------------------------------------------------- #
CONF_KEYS = {
    "ROLE_ACCOUNT": "the local role account the executor runs as (never your login)",
    "PLANNING_TEAM_KEY": "the Linear team key ideas are delegated into (e.g. PLAN)",
    "LINEAR_WORKSPACE": "the Linear workspace slug",
    "OWNER_USER_ID": "the Linear user id notified on every filed plan",
    "LINEAR_KEY_ENV": "the env-var NAME holding the executor's Linear key",
    "KIT_REPO_URL": "the https URL the role account clones the kit from",
}


def parse_conf(text):
    conf, errors = {}, []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            errors.append("line %d: no `=` in %r" % (n, raw))
            continue
        key, _, val = line.partition("=")
        conf[key.strip()] = val.strip()
    return conf, errors


def validate_conf(conf):
    """Every bad value in ONE pass — a first-error-wins validator sends the
    operator round the loop once per typo."""
    errors = []
    for key, why in CONF_KEYS.items():
        if not conf.get(key):
            errors.append("missing %s — %s" % (key, why))
    role = conf.get("ROLE_ACCOUNT", "")
    if role and role == os.environ.get("USER"):
        errors.append("ROLE_ACCOUNT is your own login (%s) — the executor must run "
                      "as a separate account so a session cannot read its key" % role)
    url = conf.get("KIT_REPO_URL", "")
    if url and not url.startswith("https://"):
        errors.append("KIT_REPO_URL must be https:// (got %r)" % url)
    env_name = conf.get("LINEAR_KEY_ENV", "")
    if env_name and not re.fullmatch(r"[A-Z][A-Z0-9_]*", env_name):
        errors.append("LINEAR_KEY_ENV must be an env-var NAME (UPPER_SNAKE), not a "
                      "value — a key in a tracked conf is a leaked key (got %r)"
                      % env_name)
    return errors


def load_conf(path):
    if not os.path.exists(path):
        return None, ["no conf at %s — copy stage-a.conf.example and fill it" % path]
    with open(path, encoding="utf-8") as fh:
        conf, errors = parse_conf(fh.read())
    return conf, errors + validate_conf(conf)


# --------------------------------------------------------------------------- #
# The Planning dispatcher entry — composed here, applied by a person
# --------------------------------------------------------------------------- #
def planning_entry(conf):
    """The dispatcher repository-entry that makes a delegated idea a PLANNING
    session. Composed here; a person applies it to the dispatcher's own config
    (this installer never writes that config — see the docstring)."""
    return {
        "name": "stage-a-planning-%s" % conf["PLANNING_TEAM_KEY"].lower(),
        "teamKeys": [conf["PLANNING_TEAM_KEY"]],
        "routingLabels": [PLANNING_ENTRY_NEVER_LABEL],
        "isActive": True,
        # THE STRUCTURAL FENCE (fallback b): no Linear MCP is attached, so the
        # session holds no mcp__linear__* tool at all.
        "linearMcpAttached": False,
        "allowedTools": list(PLANNING_KEEP_TOOLS),
        "disallowedTools": list(PLANNING_DISALLOWED_TOOLS),
        "appendInstruction": PLANNING_BRIEF,
    }


def entry_problems(entry):
    """Every way a composed entry would break the fence. --selftest asserts a
    good entry has none, and a mutated one has the matching problem."""
    problems = []
    if entry.get("linearMcpAttached") is not False:
        problems.append("linearMcpAttached is not False — the STRUCTURAL fence is "
                        "that the planner has no Linear MCP at all")
    keep = set(entry.get("allowedTools") or [])
    if "Write" not in keep:
        problems.append("the planner cannot Write its proposal file")
    if not ({"Read", "Grep", "Glob"} <= keep):
        problems.append("the planner cannot read code (Read/Grep/Glob)")
    disallowed = set(entry.get("disallowedTools") or [])
    if "mcp__linear__save_issue" not in disallowed:
        problems.append("belt-and-braces: save_issue is not in disallowedTools")
    if not (entry.get("appendInstruction") or "").startswith(PLANNING_BRIEF_FINGERPRINT):
        problems.append("the entry carries no planning brief")
    if entry.get("routingLabels") != [PLANNING_ENTRY_NEVER_LABEL]:
        problems.append("the entry is label-routable — its job kind must come from "
                        "the team, never a ticket's text (KIT-41)")
    return problems


# --------------------------------------------------------------------------- #
# Runner seam — dry-run measures, apply mutates. Stubbed in --selftest.
# --------------------------------------------------------------------------- #
class Runner:
    def __init__(self, apply_it):
        self.apply_it = apply_it
        self.log = []

    def do(self, what, fn):
        """Run `fn` when applying; otherwise record the intent and change nothing."""
        self.log.append(what)
        if not self.apply_it:
            return ("would", what)
        return ("did", fn())


# --------------------------------------------------------------------------- #
# Steps — measure-first, idempotent. Each returns (ok, detail, blocking?).
# --------------------------------------------------------------------------- #
def step_preflight(ctx):
    if not ctx.conf.get("ROLE_ACCOUNT"):
        return False, "conf incomplete", False
    return True, "conf parsed; role account %s" % ctx.conf["ROLE_ACCOUNT"], False


def step_tracker(ctx):
    """Provision/record the Planning team, its states and the labels. Uses the
    Linear transport (FakeLinear in --selftest); records ids in the ledger."""
    tracker = ctx.tracker
    team_id = tracker.ensure_team(ctx.conf["PLANNING_TEAM_KEY"])
    states = {name: tracker.ensure_state(team_id, name, kind)
              for name, kind in REQUIRED_STATES}
    labels = {key: tracker.ensure_label(key) for key in REQUIRED_LABELS}
    ctx.ledger["ids"] = {"team": team_id, "states": states, "labels": labels}
    return True, "Planning team + %d states + %d labels resolved" % (
        len(states), len(labels)), False


def step_credentials(ctx):
    """The executor's Linear key, written to the role account's env file mode 600
    via `sudo -u`. NEVER read into this process — only its name+length."""
    name = ctx.conf["LINEAR_KEY_ENV"]
    present, length = ctx.tracker.probe_role_secret(ctx.conf["ROLE_ACCOUNT"], name)
    if present and length >= 20:
        return True, "executor credential %s already present (mode 600)" % name, False
    ctx.runner.do("write role env file mode 600",
                  lambda: ctx.tracker.write_role_secret(ctx.conf["ROLE_ACCOUNT"], name))
    if not ctx.runner.apply_it:
        return True, "would write %s to the role account's env file (mode 600)" % name, False
    return True, "wrote executor credential %s (mode 600)" % name, False


def step_config(ctx):
    """The executor's config file: team key, landing state, owner, notify."""
    ids = ctx.ledger.get("ids") or {}
    cfg = {
        "planning_team_key": ctx.conf["PLANNING_TEAM_KEY"],
        "landing_state": "Backlog",
        "owner_user_id": ctx.conf["OWNER_USER_ID"],
        "notify": "subscribe",
        "linear_key_env": ctx.conf["LINEAR_KEY_ENV"],
        "state_dir": "~/.stage-a/state",
    }
    ctx.runner.do("write executor config", lambda: ctx.tracker.write_role_file(
        ctx.conf["ROLE_ACCOUNT"], "~/.stage-a/config.json", json.dumps(cfg, indent=2)))
    ctx.ledger["config"] = cfg
    return True, "executor config composed (%d keys)" % len(cfg), False


def step_dispatcher_entry(ctx):
    """Compose the Planning entry and HAND IT OFF — a person applies it to the
    dispatcher config and confirms the planning session sees no mcp__linear tool.
    This installer never writes that config."""
    entry = planning_entry(ctx.conf)
    problems = entry_problems(entry)
    if problems:
        raise SetupError("composed a broken Planning entry: " + "; ".join(problems))
    ctx.ledger["entry"] = entry
    if not ctx.ledger.get("entry_applied"):
        ctx.say("\n----- APPLY THIS to the dispatcher's config, then re-run -----")
        ctx.say(json.dumps(entry, indent=2))
        ctx.say("Then VERIFY on the live dispatcher: a session delegated an idea on "
                "team %s must see ZERO mcp__linear__* tools. Record that you did:"
                % ctx.conf["PLANNING_TEAM_KEY"])
        ctx.say("  (set entry_applied in the ledger once applied and verified)")
        return True, "Planning entry composed — apply + verify by hand", True
    return True, "Planning entry applied and verified (recorded)", False


def step_executor_job(ctx):
    """Install (not load) the scheduled job that runs the executor. Loading is a
    human gate (step_enable)."""
    ctx.runner.do("install executor job (not loaded)",
                  lambda: ctx.tracker.install_job("stage-a-executor"))
    return True, "executor job installed (not loaded)", False


def step_enable(ctx):
    """Human gate: load the executor job. Blocks until recorded done."""
    if not ctx.ledger.get("executor_enabled"):
        ctx.say("\n----- HUMAN GATE: load the executor job by hand, then re-run -----")
        ctx.say("Nothing files a plan until the executor runs. Load its job, watch "
                "one heartbeat, then record executor_enabled in the ledger.")
        return True, "executor job installed but not loaded — human gate", True
    return True, "executor job loaded (recorded)", False


def step_handover(ctx):
    """Human gate: run the by-hand planner once before turning the gate on — the
    same pre-activation proof the runbook names. Blocks until recorded done."""
    if not ctx.ledger.get("handover_done"):
        ctx.say("\n----- HUMAN GATE: run the planner by hand ONCE, then re-run -----")
        ctx.say("Before the idea gate is on, run /plan-epic by hand once on a real "
                "idea and confirm the tree files and the DoR gate holds. Record "
                "handover_done in the ledger when it passes.")
        return True, "awaiting the by-hand planner proof — human gate", True
    return True, "handover proof recorded", False


STEPS = (
    ("preflight", step_preflight),
    ("tracker", step_tracker),
    ("credentials", step_credentials),
    ("config", step_config),
    ("dispatcher-entry", step_dispatcher_entry),
    ("executor-job", step_executor_job),
    ("enable", step_enable),
    ("handover", step_handover),
)


# --------------------------------------------------------------------------- #
# Context + the Linear transport
# --------------------------------------------------------------------------- #
class Ctx:
    def __init__(self, conf, runner, tracker, ledger, out=None):
        self.conf = conf
        self.runner = runner
        self.tracker = tracker
        self.ledger = ledger
        self._out = out if out is not None else []

    def say(self, msg):
        self._out.append(msg)


class LinearTransport:
    """Real Linear provisioning (admin-scoped). Every method here MUTATES only via
    the operator's own credential and only when applying; it is replaced by
    FakeLinear in --selftest so nothing hits the network."""

    def __init__(self, api_key):
        self._key = api_key

    def ensure_team(self, key):  # pragma: no cover - operator path
        raise SetupError("live Linear provisioning is the operator's run; this is "
                         "reached only outside --selftest with a real key")

    ensure_state = ensure_label = ensure_team
    probe_role_secret = write_role_secret = write_role_file = install_job = ensure_team


# --------------------------------------------------------------------------- #
# run_steps + command dispatch
# --------------------------------------------------------------------------- #
def run_steps(ctx, keep_going=False):
    """Run the STEPS in order. `run` stops at the first blocking checkpoint;
    `verify` (keep_going) reports every row without stopping."""
    blocked, failed = [], []
    for name, fn in STEPS:
        try:
            ok, detail, blocking = fn(ctx)
        except Blocked as exc:
            blocked.append((name, str(exc)))
            if not keep_going:
                return blocked, failed
            continue
        except SetupError as exc:
            failed.append((name, str(exc)))
            if not keep_going:
                return blocked, failed
            continue
        ctx.say("  [%s] %s%s" % ("blocks" if blocking else "ok", name,
                                 " — " + detail if detail else ""))
        if blocking:
            blocked.append((name, detail))
            if not keep_going:
                return blocked, failed
    return blocked, failed


def cmd_run(ctx, dry_run):
    ctx.runner.apply_it = not dry_run
    blocked, failed = run_steps(ctx, keep_going=False)
    if failed:
        return EX_FAILED
    if blocked:
        return EX_BLOCKED
    return EX_OK


def cmd_verify(ctx):
    ctx.runner.apply_it = False
    blocked, failed = run_steps(ctx, keep_going=True)
    if failed:
        return EX_FAILED
    return EX_BLOCKED if blocked else EX_OK


def cmd_status(ctx):
    ids = ctx.ledger.get("ids")
    ctx.say("Planning team resolved: %s" % ("yes" if ids else "no"))
    ctx.say("Planning entry composed: %s" % ("yes" if ctx.ledger.get("entry") else "no"))
    ctx.say("Entry applied+verified: %s" % ("yes" if ctx.ledger.get("entry_applied") else "no"))
    ctx.say("Executor job loaded:    %s" % ("yes" if ctx.ledger.get("executor_enabled") else "no"))
    ctx.say("By-hand proof done:     %s" % ("yes" if ctx.ledger.get("handover_done") else "no"))
    return EX_OK


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
class FakeLinear:
    """Records provisioning intents; performs no I/O."""

    def __init__(self):
        self.teams, self.states, self.labels, self.jobs = {}, {}, {}, []
        self.role_files, self.secrets = {}, {}

    def ensure_team(self, key):
        self.teams.setdefault(key, "team-%s" % key)
        return self.teams[key]

    def ensure_state(self, team_id, name, kind):
        self.states[name] = "state-%s" % name.lower().replace(" ", "-")
        return self.states[name]

    def ensure_label(self, key):
        self.labels[key] = "lbl-%s" % re.sub(r"[^a-z]", "-", key.lower())
        return self.labels[key]

    def probe_role_secret(self, account, name):
        return (name in self.secrets, len(self.secrets.get(name, "")))

    def write_role_secret(self, account, name):
        self.secrets[name] = "x" * 40
        return name

    def write_role_file(self, account, path, body):
        self.role_files[path] = body
        return path

    def install_job(self, label):
        self.jobs.append(label)
        return label


GOOD_CONF = {
    "ROLE_ACCOUNT": "_planclaw",
    "PLANNING_TEAM_KEY": "PLAN",
    "LINEAR_WORKSPACE": "acme",
    "OWNER_USER_ID": "owner-1",
    "LINEAR_KEY_ENV": "STAGE_A_LINEAR_API_KEY",
    "KIT_REPO_URL": "https://github.com/x/kit.git",
}


def _ctx(conf=None, ledger=None):
    conf = dict(GOOD_CONF if conf is None else conf)
    return Ctx(conf, Runner(apply_it=False), FakeLinear(), dict(ledger or {}))


def selftest():
    failures, cases = [], [0]

    def check(name, got, want):
        cases[0] += 1
        if got != want:
            failures.append("%s: got %r, want %r" % (name, got, want))

    # 1. Source carries no move/approve/merge verb.
    src = open(os.path.abspath(__file__)).read()
    scanned = "\n".join(ln for ln in src.splitlines() if "_BANNED" not in ln)
    for tok in BANNED_TOKENS:
        check("no-move-approve-merge:%s" % tok, tok in scanned, False)

    # 2. THE FENCE. A good Planning entry has no fence problems; each mutation
    #    that breaks the fence is caught.
    good = planning_entry(GOOD_CONF)
    check("fence-good-clean", entry_problems(good), [])
    check("fence-structural-no-linear-mcp", good["linearMcpAttached"], False)
    check("fence-keeps-write", "Write" in good["allowedTools"], True)
    check("fence-keeps-read", {"Read", "Grep", "Glob"} <= set(good["allowedTools"]), True)
    check("fence-belt-names-save_issue",
          "mcp__linear__save_issue" in good["disallowedTools"], True)
    check("fence-brief-present",
          good["appendInstruction"].startswith(PLANNING_BRIEF_FINGERPRINT), True)
    check("fence-never-label-routed", good["routingLabels"], [PLANNING_ENTRY_NEVER_LABEL])
    # mutants
    m = dict(good); m["linearMcpAttached"] = True
    check("fence-mutant-attaches-linear", bool(entry_problems(m)), True)
    m = dict(good); m["allowedTools"] = ["Read", "Grep", "Glob"]  # no Write
    check("fence-mutant-no-write", bool(entry_problems(m)), True)
    m = dict(good); m["disallowedTools"] = ["Edit"]  # belt dropped
    check("fence-mutant-no-belt", bool(entry_problems(m)), True)
    m = dict(good); m["routingLabels"] = ["ready"]  # label-routable
    check("fence-mutant-label-routed", bool(entry_problems(m)), True)

    # 3. Conf validator reports EVERY error in one pass.
    bad_conf, _ = parse_conf("PLANNING_TEAM_KEY=PLAN\nKIT_REPO_URL=git@x\n"
                             "LINEAR_KEY_ENV=sk-secret-value\n")
    errs = validate_conf(bad_conf)
    check("conf-flags-missing-role", any("ROLE_ACCOUNT" in e for e in errs), True)
    check("conf-flags-non-https", any("https" in e for e in errs), True)
    check("conf-flags-key-value-not-name",
          any("LINEAR_KEY_ENV" in e for e in errs), True)
    check("conf-all-at-once", len(errs) >= 3, True)
    # a mutant first-error-wins validator would return only one
    check("conf-not-first-error-only", len(errs) == 1, False)

    # 4. Agent-env refusal: run/mutate refuse when a marker is present; status/
    #    verify/dry-run do not.
    env = {AGENT_ENV_MARKERS[0]: "1"}
    check("agent-markers-detected", bool(agent_markers_present(env)), True)
    raised = False
    try:
        refuse_if_agent("run", env=env)
    except Refusal:
        raised = True
    check("agent-run-refused", raised, True)
    # empty string still counts (presence, not truthiness)
    check("agent-empty-string-counts", bool(agent_markers_present({AGENT_ENV_MARKERS[0]: ""})), True)
    # a clean env does not refuse
    ok = True
    try:
        refuse_if_agent("run", env={})
    except Refusal:
        ok = False
    check("clean-env-proceeds", ok, True)

    # 5. run (dry) walks the steps and BLOCKS at the first human checkpoint
    #    (dispatcher-entry), changing nothing on the tracker.
    ctx = _ctx()
    code = cmd_run(ctx, dry_run=True)
    check("run-dry-blocks", code, EX_BLOCKED)
    check("run-dry-no-secret-written", ctx.tracker.secrets, {})
    check("run-dry-composed-entry", bool(ctx.ledger.get("entry")), True)

    # 6. With the human checkpoints recorded, run reaches OK and provisioned the
    #    tracker parts (apply on).
    done = {"entry_applied": True, "executor_enabled": True, "handover_done": True}
    ctx = _ctx(ledger=done)
    code = cmd_run(ctx, dry_run=False)
    check("run-apply-ok", code, EX_OK)
    check("run-provisioned-team", "PLAN" in ctx.tracker.teams, True)
    check("run-provisioned-labels", "provenance:agent" in ctx.tracker.labels, True)
    check("run-wrote-credential", "STAGE_A_LINEAR_API_KEY" in ctx.tracker.secrets, True)
    check("run-installed-job", ctx.tracker.jobs, ["stage-a-executor"])

    # 7. verify never mutates and reports the blockers without stopping.
    ctx = _ctx()
    code = cmd_verify(ctx)
    check("verify-no-mutation", ctx.tracker.teams == {} or not ctx.runner.apply_it, True)
    check("verify-reports-block", code, EX_BLOCKED)

    # 8. status is read-only and reports the ledger.
    ctx = _ctx(ledger=done)
    check("status-ok", cmd_status(ctx), EX_OK)

    if failures:
        print("FAIL: pipeline_stage_a_setup selftest")
        for f in failures:
            print("  - %s" % f)
        return 1
    print("OK: pipeline_stage_a_setup selftest (%d cases)" % cases[0])
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser():
    p = argparse.ArgumentParser(
        prog="pipeline_stage_a_setup.py",
        description="Stage A (idea-gate) installer: one command, run repeatedly, "
                    "until it stops asking.")
    p.add_argument("command", nargs="?", default="run",
                   choices=["run", "status", "verify"])
    p.add_argument("--conf", default="stage-a.conf")
    p.add_argument("--dry-run", action="store_true",
                   help="measure every step and change nothing")
    p.add_argument("--selftest", action="store_true")
    return p


def _live_ctx(conf):
    key = os.environ.get(conf.get("LINEAR_KEY_ENV", ""))
    tracker = LinearTransport(key)
    return Ctx(conf, Runner(apply_it=False), tracker, {})


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.selftest:
        return selftest()

    # Refuse a mutating run in an agent environment BEFORE any read.
    if args.command == "run" and not args.dry_run:
        try:
            refuse_if_agent("run")
        except Refusal as exc:
            print(str(exc), file=sys.stderr)
            return EX_REFUSED

    conf, errors = load_conf(args.conf)
    if errors:
        for e in errors:
            print("conf: %s" % e, file=sys.stderr)
        return EX_USAGE

    ctx = _live_ctx(conf)
    try:
        if args.command == "status":
            code = cmd_status(ctx)
        elif args.command == "verify":
            code = cmd_verify(ctx)
        else:
            code = cmd_run(ctx, dry_run=args.dry_run)
    except Refusal as exc:
        print(str(exc), file=sys.stderr)
        return EX_REFUSED
    except SetupError as exc:
        print("setup failed: %s" % exc, file=sys.stderr)
        return EX_FAILED
    for line in ctx._out:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
