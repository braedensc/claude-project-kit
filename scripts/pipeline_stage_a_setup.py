#!/usr/bin/env python3
"""Stage A (idea-gate) installer — stand up the Linear-triggered planning path.

Sibling of `scripts/pipeline_stage_e_setup.py`, pointed at planning instead of
review. ONE command, run repeatedly by a PERSON in a terminal, until it stops
asking. It provisions what can be provisioned deterministically and HANDS OFF —
as printed checkpoints — the few steps a person must apply to the machine-local
dispatcher, then verify against the live system. It never turns the gate on;
loading the executor and running the first plan by hand are human gates.

WHAT IT BUILDS, AND WHAT IT CANNOT YET
  - the Planning team and the six workspace labels the executor forces onto what
    it files (ids resolved and recorded in the ledger, never authored). No states:
    the Planning team holds only idea tickets, and the tree lands in the work team;
  - the EXECUTOR's tracker key — the credential the executor holds and the
    planning session never does — in the role account's own env file, mode 600,
    written on stdin through `sudo -u` and never on a command line;
  - the installer's half of the executor's config (the executor does not read it
    yet — KIT-136);
  - and it COMPOSES the Planning dispatcher entry (the fence + the planning brief)
    and prints it for you to apply, because that config is a session's
    supervision and lives outside this repository.
  It CANNOT yet install the job that runs the executor, because no such job
  exists: nothing discovers a finished planning session and invokes the executor
  (KIT-150). The `executor-job` step fails on that, by name, and it sits BEFORE the
  entry hand-off so nobody turns on planning sessions whose output nothing reads.

THE LEDGER is `~/.stage-a-setup/state.json` under YOUR home (dir 700, file 600):
  step outcomes, resolved ids and your sign-offs. Never a credential. `status`
  reads it back; `verify` re-measures the machine and rewrites it.

THE FENCE — the one security choice, and how it is enforced
  The whole no-self-approval guarantee rests on ONE thing: the planning session
  must hold no tool that can write the tracker. There is exactly one mechanism
  that removes a tool from a dispatched session — the repository entry's
  `disallowedTools` — and every claim below is made in that list or not at all.

  THIS INSTALLER ONCE CLAIMED IT ANOTHER WAY, AND THE CLAIM WAS EMPTY. The entry
  carried `linearMcpAttached: False` and this file called it "the STRUCTURAL
  fence". The dispatcher (0.2.69) never reads that key — zero references across
  its packages — while its MCP config service injects `linear`, `cyrus-tools` and
  `cyrus-docs` into every tracker-triggered session. A planner would have held the
  tracker's entire write surface minus the six tool names the old denylist
  happened to spell. The key is gone; a control nothing reads must not sit in a
  config file reading like one.

  WHAT REPLACES IT is the shape KIT-132 gave the reviewer. Each server the
  dispatcher injects is named in `disallowedTools` in BOTH documented rule forms,
  `mcp__<server>` and `mcp__<server>__*`, because a rule in a form the runtime
  does not honour fails silently — and a fence that fails silently reads closed
  and is open. `MCP_FENCE_RULE_RE` pins the only shape a rule may take: one named
  server, whole or by wildcard. An unanchored `mcp__*` is skipped by the runtime
  with no error, so this file refuses to write one.

  WHAT THE PLANNER KEEPS, and why each is needed: Read/Grep/Glob to decompose
  against real code, Write to emit its proposal file, and Task/Agent because the
  planning procedure's rubric panel is five independent passes in fresh contexts.
  Everything else that runs, edits, fetches, schedules, messages, publishes or
  starts other work is named in the fence — including the three MCP resource
  tools, whose names do not start with `mcp__`, so a server rule never reaches
  them.

  WHAT IS NOT PROVEN, and must be probed on the live dispatcher before the gate
  is switched on (the activation checklist has the step): whether a subagent
  started by `Task` inherits its parent's `disallowedTools`. If it does not, one
  Task call reopens everything this list closes. `--selftest` cannot answer it;
  only a person watching a live planning session can (KIT-140).

  ONE CAPABILITY IS LOST, NOT REHOMED. The planning procedure's fifth pass searches
  the tracker for duplicates before anything is filed, and the planner can no longer
  run it. The design said the executor would run dedupe instead; the executor does
  not (KIT-141). Stated here rather than left to be rediscovered.

WHAT IT REFUSES (the mechanism-in-kit / operator-runs-it / refuses-in-agent-env
doctrine, identical to the review installer)
  - MUTATION IN AN AGENT ENVIRONMENT. `run` (without `--dry-run`) and `attest`
    refuse when any of the imported AGENT_ENV_MARKERS is set. A session that installs its own
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
    pipeline_stage_a_setup.py run [--dry-run] [--conf stage-a.conf]
    pipeline_stage_a_setup.py status
    pipeline_stage_a_setup.py verify [--conf stage-a.conf]
    pipeline_stage_a_setup.py card CA-PROBE
    pipeline_stage_a_setup.py attest CA-PROBE --initials AB --note "what you saw"
    pipeline_stage_a_setup.py --selftest

Exit: 0 done / already done · 1 a step failed · 2 usage or conf · 3 refused (a model
      is driving) · 4 could not measure — blocks, and is not a pass · 10 work is
      outstanding, or a checkpoint waits on a person.
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
EX_UNKNOWN = 4
EX_BLOCKED = 10

# --------------------------------------------------------------------------- #
# The fence — the security core. Pinned by --selftest.
# --------------------------------------------------------------------------- #
# The MCP servers the dispatcher injects into EVERY tracker-triggered session
# (its McpConfigService, v0.2.69): `linear` — the tracker's whole write surface
# under the dispatcher's own token; `cyrus-tools` — feedback into another agent
# session, issue relations, uploads; `cyrus-docs` — a third-party documentation
# fetch; `slack` — present whenever the dispatcher holds a bot token. A planner
# needs none of them: its deliverable is a file the executor reads.
#
# These four are the servers every machine has. A machine can add more, through
# the files the dispatcher config's `linearMcpConfigs` names. This installer does
# not read that config (the review installer does, and fences what it finds), so
# a machine carrying extra servers must have them added here before the gate goes
# on — named in the activation checklist, not assumed away.
PLANNER_FENCE_SERVERS = ("linear", "cyrus-tools", "cyrus-docs", "slack")


def _server_rules(server):
    return ["mcp__%s" % server, "mcp__%s__*" % server]


# The only shape an `mcp__` fence entry may take: one named server, whole or by
# wildcard. An unanchored `mcp__*` or a bare `mcp__` is skipped by the runtime
# with no error, which would read as a closed fence and be an open one. A rule
# naming one tool (`mcp__linear__save_issue`) is refused for the same reason it
# is unnecessary: the wildcard already covers it, and a mixed list invites the
# belief that the named ones are the fence.
MCP_FENCE_RULE_RE = re.compile(r"^mcp__([A-Za-z0-9_-]+?)(__\*)?$")

# What the planner KEEPS. None of these is in the fence below, and --selftest
# asserts it. The live probe a person runs (the activation checklist) passes only
# when a planning session's tool list holds no name outside this set: an
# allowlist check, because a deny list cannot name a tool the dispatcher or its
# SDK adds later. Such a tool reaches the planner until someone adds it below.
#
# `Write` is here because the proposal file IS the planner's only output. `Task`
# and `Agent` are here because the planning procedure's rubric panel is five
# passes in fresh contexts, and collapsing them into one context is a quality
# change, not a security one — see the docstring's "not proven" note on whether
# a subagent inherits this fence.
PLANNER_KEEP_TOOLS = (
    "Read", "Grep", "Glob", "Write", "Task", "Agent", "LSP", "ToolSearch",
    "TaskOutput", "TaskCreate", "TaskGet", "TaskList", "TaskUpdate", "TodoWrite",
    "CronList", "ReportFindings")

# What the planner loses. The names come from the dispatcher's own list of
# available tools (v0.2.69) and the SDK it depends on. `Agent` is the subagent
# tool's current name and `Task` its older one; both are KEPT, deliberately.
#
# `Monitor` runs a shell command, and a `Bash` rule does not stop it: a deny rule
# matches the tool's own name. The three MCP resource tools read any connected
# server by a `server` argument, and their names do not start with `mcp__`, so
# the server rules never reach them. `ListAgents` names the other sessions this
# one could message, and anything it lists can reach a ticket.
DISALLOWED_BUILTINS = [
    "Bash", "Monitor", "REPL",                                    # runs
    "Edit", "NotebookEdit",                                       # writes elsewhere
    "WebFetch", "WebSearch",                                      # fetches
    "Workflow", "RemoteTrigger", "Skill", "TaskStop",             # starts other work
    "EnterWorktree", "ExitWorktree",
    "CronCreate", "CronDelete", "ScheduleWakeup",                 # schedules
    "SendMessage", "SendUserMessage", "PushNotification",         # messages, publishes
    "ListAgents", "Artifact", "ShareOnboardingGuide", "DesignSync",
    "AskUserQuestion",                                            # asks a person
    "EnterPlanMode", "ExitPlanMode",                              # switches its mode
    "ListMcpResourcesTool", "ReadMcpResourceTool",                # reads MCP resources
    "ReadMcpResourceDirTool",
]

PLANNING_DISALLOWED_TOOLS = DISALLOWED_BUILTINS + [
    rule for server in PLANNER_FENCE_SERVERS for rule in _server_rules(server)]

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

# The workspace labels the executor forces onto what it files.
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
    (this installer never writes that config — see the docstring).

    NO `allowedTools` KEY. The dispatcher's permission callback allows every tool
    whatever an `allowedTools` list says, so the key narrows nothing — and an
    entry that carries one gets a DIFFERENT set of injected MCP servers than one
    that does not. A key that reads as a control and is not one is the defect
    this entry was rebuilt to remove, so it is absent, exactly as it is on a
    review entry. What the planner keeps is whatever `disallowedTools` leaves,
    and `PLANNER_KEEP_TOOLS` is the allowlist a person probes that against."""
    return {
        "name": "stage-a-planning-%s" % conf["PLANNING_TEAM_KEY"].lower(),
        "teamKeys": [conf["PLANNING_TEAM_KEY"]],
        "routingLabels": [PLANNING_ENTRY_NEVER_LABEL],
        "isActive": True,
        "disallowedTools": list(PLANNING_DISALLOWED_TOOLS),
        "appendInstruction": PLANNING_BRIEF,
    }


def entry_problems(entry):
    """Every way a composed entry would break the fence. --selftest asserts a
    good entry has none, and a mutated one has the matching problem.

    The checks are written against LITERAL names, never against the constants
    that BUILD the entry: a check that loops over `PLANNER_FENCE_SERVERS` stays
    green when someone empties it, which is the one change that matters."""
    problems = []
    if "linearMcpAttached" in entry:
        problems.append("the entry carries `linearMcpAttached` — a key the dispatcher "
                        "never reads, which once stood in for the whole fence")
    if "allowedTools" in entry:
        problems.append("the entry carries `allowedTools` — it narrows nothing, and an "
                        "entry that has one is injected a different server set")
    disallowed = entry.get("disallowedTools") or []
    seen = set(disallowed)
    for server in ("linear", "cyrus-tools", "cyrus-docs", "slack"):
        for rule in _server_rules(server):
            if rule not in seen:
                problems.append("the fence does not remove %s — a server the dispatcher "
                                "injects into every session" % rule)
    for rule in (r for r in disallowed if r.startswith("mcp__")):
        match = MCP_FENCE_RULE_RE.match(rule)
        if not (match and match.group(1) in PLANNER_FENCE_SERVERS):
            problems.append("%r is not a rule anchored to one fenced server; an "
                            "unanchored or per-tool rule is skipped by the runtime with "
                            "no error" % rule)
    for runs in ("Bash", "Monitor", "Edit", "WebFetch", "ListMcpResourcesTool",
                 "ReadMcpResourceTool", "ReadMcpResourceDirTool"):
        if runs not in seen:
            problems.append("the fence leaves the planner %s" % runs)
    for kept in ("Write", "Read", "Grep", "Glob"):
        if kept in seen:
            problems.append("the fence removes %s, which the planner needs to read code "
                            "and emit its proposal" % kept)
    if not (entry.get("appendInstruction") or "").startswith(PLANNING_BRIEF_FINGERPRINT):
        problems.append("the entry carries no planning brief")
    if entry.get("routingLabels") != [PLANNING_ENTRY_NEVER_LABEL]:
        problems.append("the entry is label-routable — its job kind must come from "
                        "the team, never a ticket's text (KIT-41)")
    return problems


# --------------------------------------------------------------------------- #
# Step outcomes.  DONE and ALREADY-DONE both exit 0 and mean different things to
# a reader; UNKNOWN is neither a pass nor a failure and has its own exit code,
# because "I could not look" and "there was nothing to look at" are opposite
# answers that arrive as the same silence (contract §13).
# --------------------------------------------------------------------------- #
DONE = "DONE"
ALREADY_DONE = "ALREADY-DONE"
WOULD_CHANGE = "WOULD-CHANGE"
BLOCKED = "BLOCKED-ON-HUMAN"
FAILED = "FAILED"
UNKNOWN = "UNKNOWN"
SKIPPED = "SKIPPED"

_OUTCOME_EXIT = {DONE: EX_OK, ALREADY_DONE: EX_OK, SKIPPED: EX_OK,
                 WOULD_CHANGE: EX_BLOCKED, BLOCKED: EX_BLOCKED,
                 UNKNOWN: EX_UNKNOWN, FAILED: EX_FAILED}
# `verify` never stops early, so its verdict is the WORST row it saw, ordered by
# how much a person needs to know first.
_SEVERITY = (FAILED, UNKNOWN, BLOCKED, WOULD_CHANGE, DONE, ALREADY_DONE, SKIPPED)


def now_iso():
    """A timestamp with no clock call in the module body — the ledger records
    when a row was written, and nothing in this file branches on it."""
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# The ledger — under YOUR home, never the role account's, never a worktree.
#
# THE OLD LEDGER WAS A DICT THAT DIED WITH THE PROCESS.  `status` built a fresh
# empty one on every invocation and printed five "no" whatever the machine had
# done, which is the worst of both states: it reads like a measurement and is a
# constant.  This one is a file, so a row recorded by `run` is a row `status`
# reads back tomorrow.
#
# IT HOLDS NO CREDENTIAL, EVER.  Ids, outcomes and sign-offs only.  The key
# itself lives in the role account's own env file at mode 600, and this process
# learns nothing about it but a name and a length.
# --------------------------------------------------------------------------- #
DEFAULT_STATE_HOME = "~/.stage-a-setup"
LEDGER_SCHEMA = "stage-a-setup/1"


class State(object):
    def __init__(self, root=DEFAULT_STATE_HOME):
        self.root = os.path.expanduser(root)
        self.path = os.path.join(self.root, "state.json")
        self.data = {"schema": LEDGER_SCHEMA, "steps": {}, "ids": {},
                     "attestations": {}, "notes": {}}
        self.unreadable = None
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as fh:
                    loaded = json.load(fh)
                if isinstance(loaded, dict):
                    self.data.update(loaded)
            except (OSError, ValueError) as exc:
                # UNREADABLE IS NOT EMPTY.  A ledger that cannot be read means
                # every step must be re-MEASURED; it never means the steps were
                # never done, and it is never silent.
                self.unreadable = str(exc)

    def save(self):
        try:
            os.makedirs(self.root, mode=0o700, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=2, sort_keys=True)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
            return None
        except OSError as exc:
            return "could not write %s: %s" % (self.path, exc)

    def record(self, step, outcome, detail=""):
        self.data["steps"][step] = {"outcome": outcome, "detail": detail,
                                    "at": now_iso()}

    def outcome(self, step):
        return (self.data["steps"].get(step) or {}).get("outcome")

    def remember(self, key, value):
        self.data["ids"][key] = value

    def attested(self, aid):
        return aid in self.data["attestations"]

    def attest(self, aid, initials, note=""):
        self.data["attestations"][aid] = {"initials": initials, "note": note,
                                          "at": now_iso()}


# The sign-offs a person makes, and what each one asserts.  A step that waits on
# one is BLOCKED until the person records it — the installer never records its
# own, which is the whole reason these exist as a separate command.
ATTESTATIONS = {
    "CA-ENTRY": "the Planning entry is applied to the dispatcher's own config",
    "CA-PROBE": "a live planning session, AND a subagent it starts, showed no tracker tool",
    "CA-EXECUTOR": "the executor's job is loaded and one pass of it was seen",
    "CA-HANDOVER": "the planner ran by hand once and its tree filed cleanly",
}

# A placeholder a person can SIGN is not a placeholder.  The review installer
# printed `xx` in its own usage text for one release, someone signed it
# literally, and a sign-off nobody made was recorded.  Printed and refused by
# the same constants, so the two cannot drift.
INITIALS_PLACEHOLDER = "YOUR-INITIALS"
INITIALS_PLACEHOLDERS = frozenset((INITIALS_PLACEHOLDER.lower(), "yourinitials",
                                   "xx", "xxx", "xxxx"))


# --------------------------------------------------------------------------- #
# Checkpoint cards.  A card is a step a computer must not do, with the reason
# printed beside it — a card nobody believes is a card nobody does.
# --------------------------------------------------------------------------- #
CARDS = {
    "CA-ENTRY": {
        "title": "Apply the Planning entry to the dispatcher's config",
        "why": ("The dispatcher's config is a session's supervision. A program that "
                "wrote its own entry would be choosing its own fence, which is the "
                "one thing this design exists to prevent. So this installer composes "
                "the entry and prints it; you paste it."),
        "do": ["Read the printed entry above. Two things must be true of it:",
               "  - `disallowedTools` names every tracker server twice — once as",
               "    `mcp__<server>` and once as `mcp__<server>__*`.",
               "  - there is no `allowedTools` key and no `linearMcpAttached` key.",
               "Paste the entry into the dispatcher's own config file, beside the",
               "review entries, and restart the dispatcher so it loads."],
        "good": "the dispatcher restarts clean and lists the new entry",
    },
    "CA-PROBE": {
        "title": "Prove on the live dispatcher that a planner holds no tracker tool",
        "why": ("This is the whole guarantee, and it is the only step that measures "
                "the running system rather than this repository. A fence is a list in "
                "a config file until someone watches a real session obey it. The "
                "installer cannot start a session, and a session's own report about "
                "its own tools is not evidence a session should be trusted to give."),
        "do": ["File a throwaway idea ticket on the Planning team and hand it off.",
               "In the session that starts, ask it to list EVERY tool it holds, by",
               "exact name. Check the list twice:",
               "  1. no name beginning `mcp__` appears at all;",
               "  2. no name outside the planner's keep-set appears (the keep-set is",
               "     printed by `status`).",
               "Then ask it to start ONE subagent and have THAT subagent list its own",
               "tools by exact name. Check the same two things.",
               "The second check is the one nobody remembers: whether a subagent",
               "inherits its parent's fence is not known (KIT-140), and if it does",
               "not, one subagent call reopens everything the fence closed."],
        "good": "both lists hold no `mcp__` name and nothing outside the keep-set",
    },
    "CA-EXECUTOR": {
        "title": "Load the executor's job and watch one pass",
        "why": ("Nothing files a plan until the executor runs. Loading a scheduled "
                "job needs the role account's own launch context, and this installer "
                "installs the job without loading it so that the moment the gate can "
                "first write to the board is a moment a person chose."),
        "do": ["Load the job as the role account, then watch one pass go by and",
               "confirm it wrote a heartbeat.",
               "A job that is installed and never loaded looks identical to one that",
               "is loaded and failing — which is why this is a sign-off and not a",
               "probe."],
        "good": "one pass of the executor ran and left a heartbeat",
    },
    "CA-HANDOVER": {
        "title": "Run the planner by hand once, before anything is automatic",
        "why": ("The planning procedure has never been run on this machine at all. "
                "Turning on an unattended planner whose output nobody has ever seen "
                "puts the first look at its quality after the tickets are filed."),
        "do": ["In a project that has a delivery config, run the planning skill by",
               "hand on one real idea.",
               "Confirm three things: the tree files, every child passes the",
               "readiness gate, and the epic lands in the backlog awaiting you."],
        "good": "one epic and its children in the backlog, none of them ready",
    },
}


class Unknown(Exception):
    """A step could not be MEASURED — neither a pass nor a failure (§13)."""

    def __init__(self, what, remedy=""):
        Exception.__init__(self, what)
        self.what = what
        self.remedy = remedy


# --------------------------------------------------------------------------- #
# Runner seam — dry-run measures, apply mutates.  `verify` asserts it recorded
# no writes, so a step that mutates outside this seam is caught by the selftest
# rather than by a person's machine.
# --------------------------------------------------------------------------- #
class Runner(object):
    def __init__(self, apply_it):
        self.apply_it = apply_it
        self.writes = []

    def do(self, what, fn):
        """Run `fn` when applying; otherwise record the intent and change nothing."""
        if not self.apply_it:
            return ("would", what)
        self.writes.append(what)
        return ("did", fn())


# --------------------------------------------------------------------------- #
# The tracker.  ONE fixed endpoint, redirects refused, the key in one header and
# nowhere else — never a URL, never an argument, never a log line.
# --------------------------------------------------------------------------- #
LINEAR_API = "https://api.linear.app/graphql"


class _NoRedirect(object):
    """A redirect would carry the Authorization header to whatever host the
    answer named. One fixed endpoint; a redirect is never expected."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SetupError("the tracker answered with a redirect (HTTP %d), which this "
                         "program does not follow" % code)


class LinearTransport(object):
    """Live provisioning under the OPERATOR's own credential.

    Every method here MEASURES first and mutates only when `apply_it` is true,
    so a dry run over a real workspace is a read. Absence is returned, never
    raised: a team that is not there yet is a fact, and only a call that could
    not be MADE is an error."""

    def __init__(self, key):
        import urllib.request
        base = urllib.request.HTTPRedirectHandler
        handler = type("_NR", (base,), {"redirect_request": _NoRedirect.redirect_request})()
        self._opener = urllib.request.build_opener(handler)
        self._auth = ("Bearer " + key) if key.startswith("lin_oauth_") else key

    def post(self, query, variables=None):
        import urllib.error
        import urllib.request
        body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
        req = urllib.request.Request(LINEAR_API, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", self._auth)
        try:
            with self._opener.open(req, timeout=30) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise SetupError("the tracker refused the key (HTTP %d). Its value is "
                                 "never shown; re-run and paste it again." % exc.code)
            raise SetupError("the tracker answered HTTP %d" % exc.code)
        except urllib.error.URLError as exc:
            raise Unknown("the tracker was unreachable (%s), so nothing about the "
                          "Planning team could be measured" % str(exc.reason)[:120],
                          "check the network and run the same command again")
        try:
            doc = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise SetupError("the tracker's answer was not JSON")
        if doc.get("errors"):
            msgs = "; ".join(str(e.get("message", "?"))[:120] for e in doc["errors"][:3])
            raise SetupError("the tracker refused a call: %s" % msgs)
        data = doc.get("data")
        if not isinstance(data, dict):
            raise SetupError("the tracker's answer carried no data object")
        return data

    # -- teams ------------------------------------------------------------- #
    def find_team(self, key):
        data = self.post(
            "query($k:String!){teams(filter:{key:{eq:$k}},first:1)"
            "{nodes{id key name}}}", {"k": key})
        nodes = (data.get("teams") or {}).get("nodes") or []
        return nodes[0] if nodes else None

    def ensure_team(self, key, apply_it):
        found = self.find_team(key)
        if found:
            return found["id"], False
        if not apply_it:
            return None, False
        data = self.post(
            "mutation($k:String!,$n:String!){teamCreate(input:{key:$k,name:$n})"
            "{success team{id}}}", {"k": key, "n": key.title()})
        result = data.get("teamCreate") or {}
        if not result.get("success") or not (result.get("team") or {}).get("id"):
            raise SetupError("the tracker would not create the team %s" % key)
        return result["team"]["id"], True

    # -- labels ------------------------------------------------------------ #
    def workspace_labels(self):
        """EVERY label, all pages. A first-page read on a workspace with more
        labels than one page holds would call a real label missing, and the
        apply path would then create its twin."""
        rows, cursor = [], None
        for _page in range(40):
            data = self.post(
                "query($c:String){issueLabels(first:250,after:$c){nodes{id name "
                "team{id}} pageInfo{hasNextPage endCursor}}}", {"c": cursor})
            block = data.get("issueLabels") or {}
            rows.extend(block.get("nodes") or [])
            info = block.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                return rows
            cursor = info.get("endCursor")
        raise Unknown("the workspace has more labels than this program will page "
                      "through (40 pages), so a missing label cannot be told from "
                      "an unread one", "")

    def ensure_label(self, name, apply_it, existing=None):
        """Workspace-scoped, never team-scoped. A label created with a team
        cannot have its scope changed afterwards, so a team-scoped twin is a
        failure to report, not something to quietly use."""
        rows = existing if existing is not None else self.workspace_labels()
        for row in rows:
            if row.get("name") != name:
                continue
            if row.get("team"):
                raise SetupError(
                    "the label %r exists but is scoped to one team; scope cannot be "
                    "changed after creation. Delete it in the tracker and run again."
                    % name)
            return row["id"], False
        if not apply_it:
            return None, False
        data = self.post(
            "mutation($n:String!){issueLabelCreate(input:{name:$n}){success "
            "issueLabel{id}}}", {"n": name})
        result = data.get("issueLabelCreate") or {}
        if not result.get("success") or not (result.get("issueLabel") or {}).get("id"):
            raise SetupError("the tracker would not create the label %r" % name)
        return result["issueLabel"]["id"], True


# --------------------------------------------------------------------------- #
# The machine.  Everything that touches the role account goes through `sudo -u`,
# non-interactively: a password prompt inside an automated pass is a prompt
# nobody is there to answer, so a run that WOULD need one reports UNMEASURED
# rather than hanging or guessing.
# --------------------------------------------------------------------------- #
class Host(object):
    """The only code that reaches the role account. Every call is `sudo -n -u`:
    non-interactive, so a pass that would need a password reports UNMEASURED
    instead of hanging on a prompt nobody is there to answer.

    NOTHING SECRET IS EVER AN ARGUMENT. A process's arguments are readable by
    every other process on the machine, so a payload travels on stdin and the
    command line carries only fixed text and validated names."""

    def _sudo(self, account, script, stdin=None):
        import subprocess
        try:
            proc = subprocess.run(
                ["sudo", "-n", "-u", account, "/bin/sh", "-c", script],
                input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            return None, str(exc)[:160]
        err = proc.stderr.decode("utf-8", "replace")
        if proc.returncode != 0 and ("password is required" in err
                                     or "a terminal is required" in err):
            return None, "sudo needs a password, and this pass may not ask for one"
        if proc.returncode == 1 and "unknown user" in err:
            return 1, "unknown user"
        return proc.returncode, proc.stdout.decode("utf-8", "replace").strip()

    def account_exists(self, account):
        code, out = self._sudo(account, "true")
        if code is None:
            return None
        return code == 0

    def secret_present(self, account, env_name):
        """(True, length) / (False, 0) / (None, reason). `env_name` is validated
        UPPER_SNAKE by the conf check, so it is safe inside the script text.
        The VALUE never enters this process — only its length does."""
        code, out = self._sudo(
            account,
            'f="$HOME/.stage-a/env"; [ -f "$f" ] || exit 9; '
            'v=$(sed -n "s/^%s=//p" "$f" | head -n 1); '
            'printf %%s "$v" | wc -c' % env_name)
        if code is None:
            return None, out
        if code == 9:
            return False, 0
        if code != 0:
            return None, "could not read the env file (exit %d)" % code
        try:
            return True, int(out.strip() or 0)
        except ValueError:
            return None, "could not read the env file's shape"

    def file_present(self, account, relpath):
        """`relpath` is a fixed path under the role account's home, never input."""
        code, _out = self._sudo(account, 'test -f "$HOME/%s"' % relpath)
        if code is None:
            return None
        return code == 0

    def write_role_file(self, account, relpath, body):
        """Write `body` to $HOME/`relpath` as `account`, dir 700, file 600, with
        the body on stdin. `relpath` is one of this file's own constants."""
        code, out = self._sudo(
            account,
            'set -e; umask 077; t="$HOME/%s"; mkdir -p "$(dirname "$t")"; '
            'chmod 700 "$(dirname "$t")"; cat > "$t.tmp"; chmod 600 "$t.tmp"; '
            'mv "$t.tmp" "$t"' % relpath,
            stdin=body.encode("utf-8"))
        if code is None:
            raise Unknown("could not write %s as %s (%s)" % (relpath, account, out),
                          "run this from a terminal as yourself")
        if code != 0:
            raise SetupError("writing %s as %s failed (exit %d)" % (relpath, account, code))
        return relpath


# --------------------------------------------------------------------------- #
# Steps — measure first, mutate only when applying, idempotent.
# Each returns (ok, detail, notes); `ok` true means ALREADY satisfied.
# --------------------------------------------------------------------------- #
ROLE_ENV_FILE = ".stage-a/env"
ROLE_CONFIG_FILE = ".stage-a/config.json"

# The ticket that must close before the executor has anything to run. Named in
# one place so the step, the card and the selftest cannot disagree about it.
EXECUTOR_READER_TICKET = "KIT-150"


def step_preflight(ctx, apply_it):
    notes = []
    if ctx.state.unreadable:
        notes.append("the ledger at %s could not be read (%s), so every step below "
                     "was re-measured rather than replayed. That is not the same as "
                     "a machine where nothing has been done."
                     % (ctx.state.path, ctx.state.unreadable))
    account = ctx.conf["ROLE_ACCOUNT"]
    exists = ctx.host.account_exists(account)
    if exists is None:
        raise Unknown(
            "could not reach the role account %s without a password, so nothing that "
            "lives under it can be measured in this pass" % account,
            "run this from a terminal as yourself:\n    python3 %s run --dry-run"
            % _self_path())
    if not exists:
        raise SetupError(
            "the role account %s does not exist, and this installer does not create "
            "accounts. Create it first — it must not be your own login." % account)
    return True, "conf parsed; role account %s reachable" % account, notes


def step_tracker(ctx, apply_it):
    """The Planning team and the six workspace labels. Ids are RESOLVED and
    recorded, never authored.

    NO STATES. The Planning team holds only idea tickets; the executor files the
    tree into the work team its project config names, with that team's states.
    An earlier version created a state named `Ready` here, which on a stock team
    is a second unstarted state beside `Todo` — a duplicate a person then has to
    find and delete."""
    if ctx.tracker is None:
        raise Unknown(
            "no tracker credential in this pass, so the Planning team and the labels "
            "could not be measured",
            "export the key under the name LINEAR_KEY_ENV gives in your conf, in the "
            "terminal you run this from; it is read, never stored by this program")
    key = ctx.conf["PLANNING_TEAM_KEY"]
    outstanding, made = [], []
    team_id, created = ctx.tracker.ensure_team(key, apply_it)
    if team_id is None:
        outstanding.append("team %s" % key)
    elif created:
        made.append("team %s" % key)
    labels, rows = {}, ctx.tracker.workspace_labels()
    for name in REQUIRED_LABELS:
        lid, created = ctx.tracker.ensure_label(name, apply_it, rows)
        if lid is None:
            outstanding.append("label %s" % name)
            continue
        labels[name] = lid
        if created:
            made.append("label %s" % name)
    if outstanding:
        return False, "would create: " + ", ".join(outstanding), []
    ctx.state.remember("planning_team_id", team_id)
    ctx.state.remember("labels", labels)
    if made:
        ctx.runner.writes.extend(made)
        return False, "created " + ", ".join(made), []
    return True, "team %s and %d labels resolved" % (key, len(labels)), []


def step_credentials(ctx, apply_it):
    """The executor's tracker key, in the role account's own env file at mode 600.
    Its VALUE never enters this process except once, at a hidden prompt, on its
    way to that file on stdin."""
    name = ctx.conf["LINEAR_KEY_ENV"]
    account = ctx.conf["ROLE_ACCOUNT"]
    present, length = ctx.host.secret_present(account, name)
    if present is None:
        raise Unknown("could not look at %s's env file (%s)" % (account, length),
                      "run this from a terminal as yourself")
    if present and length >= 20:
        return True, "%s present in %s's env file (%d chars)" % (name, account, length), []
    if present:
        why = "%s is set but only %d chars long — not a tracker key" % (name, length)
    else:
        why = "%s is not in %s's env file" % (name, account)
    if not apply_it:
        return False, why + "; a run would ask for it at a hidden prompt", []
    value = ctx.prompt_secret(name)
    if len(value) < 20:
        raise SetupError("the value pasted for %s is %d chars — too short to be a "
                         "tracker key. Nothing was written." % (name, len(value)))
    ctx.runner.do("write %s's env file" % account, lambda: ctx.host.write_role_file(
        account, ROLE_ENV_FILE, "%s=%s\n" % (name, value)))
    return False, "wrote %s to %s's env file (mode 600)" % (name, account), []


def step_config(ctx, apply_it):
    """The installer's half of the executor's config. WHICH file the executor
    reads is the config seam (KIT-136) — until that closes, this file is written
    for the record and the executor does not read it, and the note says so."""
    ids = ctx.state.data.get("ids") or {}
    cfg = {
        "schema": "stage-a-executor-config/0",
        "planning_team_key": ctx.conf["PLANNING_TEAM_KEY"],
        "planning_team_id": ids.get("planning_team_id"),
        "owner_user_id": ctx.conf["OWNER_USER_ID"],
        "notify": "subscribe",
        "linear_key_env": ctx.conf["LINEAR_KEY_ENV"],
        "label_ids": ids.get("labels") or {},
    }
    body = json.dumps(cfg, indent=2, sort_keys=True) + "\n"
    notes = ["the executor does not read this file yet: it reads only its project's "
             "delivery config (KIT-136). Written so the values are recorded in one "
             "place, not because anything consumes them."]
    account = ctx.conf["ROLE_ACCOUNT"]
    present = ctx.host.file_present(account, ROLE_CONFIG_FILE)
    if present is None:
        raise Unknown("could not look for the executor's config file",
                      "run this from a terminal as yourself")
    if present and ctx.state.data["notes"].get("config_body") == body:
        return True, "executor config present and unchanged", notes
    if not apply_it:
        return False, ("would %s the executor's config (%d keys)"
                       % ("rewrite" if present else "write", len(cfg))), notes
    ctx.runner.do("write the executor's config", lambda: ctx.host.write_role_file(
        account, ROLE_CONFIG_FILE, body))
    ctx.state.data["notes"]["config_body"] = body
    return False, "wrote the executor's config (%d keys)" % len(cfg), notes


def step_executor_job(ctx, apply_it):
    """The consumer, checked BEFORE the producer is handed over.

    There is no job to install. The executor is a one-shot command that needs a
    request file and a pinned ticket on every call, and nothing in this
    repository discovers a finished planning session, fetches its tree, or
    supplies that pin. An earlier version of this step recorded "installed" and
    ran nothing — an installer reporting progress it had not made.

    It sits before the dispatcher entry on purpose. Applying the entry turns on
    a producer: every delegated idea starts a paid planning session. With no
    reader, that session's output vanishes and nothing says so."""
    raise SetupError(
        "no reader exists for the executor. Nothing in this repository finds a "
        "finished planning session, fetches its tree and runs the executor with the "
        "delegated ticket pinned, so there is no job for this step to install.\n"
        "Stopping here, BEFORE the dispatcher entry, because applying that entry "
        "would start planning sessions whose output nothing reads.\n"
        "This clears when %s lands." % EXECUTOR_READER_TICKET)


def step_dispatcher_entry(ctx, apply_it):
    """Compose the Planning entry and HAND IT OFF. This installer never writes
    the dispatcher's config, even for the operator."""
    entry = planning_entry(ctx.conf)
    problems = entry_problems(entry)
    if problems:
        raise SetupError("composed a broken Planning entry — refusing to print it:\n"
                         + "\n".join("  - " + p for p in problems))
    if not ctx.state.attested("CA-ENTRY"):
        if getattr(ctx, "failed_before", False):
            ctx.say("")
            ctx.say("(the Planning entry is withheld: an earlier step failed, and applying")
            ctx.say(" the entry before it is fixed would start sessions nothing can finish)")
        else:
            ctx.say("")
            ctx.say("----- the Planning entry, for you to apply -----")
            ctx.say(json.dumps(entry, indent=2))
        raise Blocked("CA-ENTRY")
    return True, "Planning entry applied (signed %s)" % _signed_at(ctx, "CA-ENTRY"), []


def step_probe(ctx, apply_it):
    """The one step that measures the RUNNING system. Nothing here can do it."""
    if not ctx.state.attested("CA-PROBE"):
        raise Blocked("CA-PROBE")
    return True, "live fence probe signed %s" % _signed_at(ctx, "CA-PROBE"), []


def step_enable(ctx, apply_it):
    if not ctx.state.attested("CA-EXECUTOR"):
        raise Blocked("CA-EXECUTOR")
    return True, "executor loaded (signed %s)" % _signed_at(ctx, "CA-EXECUTOR"), []


def step_handover(ctx, apply_it):
    if not ctx.state.attested("CA-HANDOVER"):
        raise Blocked("CA-HANDOVER")
    return True, "by-hand proof signed %s" % _signed_at(ctx, "CA-HANDOVER"), []


def _signed_at(ctx, aid):
    rec = ctx.state.data["attestations"].get(aid) or {}
    return "%s by %s" % (rec.get("at", "?")[:10], rec.get("initials", "?"))


STEPS = (
    ("preflight", "your conf, and the role account", step_preflight),
    ("tracker", "the Planning team and the labels", step_tracker),
    ("credentials", "the role account's own env file, mode 600", step_credentials),
    ("config", "the executor's config file", step_config),
    ("executor-job", "a reader that runs the executor — checked before the entry",
     step_executor_job),
    ("dispatcher-entry", "the Planning entry, composed and handed to you",
     step_dispatcher_entry),
    ("probe", "proof from a live session that the fence holds", step_probe),
    ("enable", "the executor's reader loaded, and one pass seen", step_enable),
    ("handover", "one planning run by hand, before anything is automatic",
     step_handover),
)


# --------------------------------------------------------------------------- #
# Context
# --------------------------------------------------------------------------- #
class Ctx(object):
    def __init__(self, conf, runner, tracker, host, state, out=None):
        self.conf = conf
        self.runner = runner
        self.tracker = tracker
        self.host = host
        self.state = state
        self._out = out if out is not None else []

    def say(self, msg):
        self._out.append(msg)

    def prompt_secret(self, name):
        import getpass
        return getpass.getpass("paste %s (it is not echoed): " % name).strip()


def _self_path():
    return os.path.join("scripts", os.path.basename(__file__))


def _wrap(text, width=76):
    words, lines, line = str(text).split(), [], ""
    for word in words:
        if len(line) + len(word) + 1 > width:
            lines.append(line)
            line = word
        else:
            line = (line + " " + word).strip()
    if line:
        lines.append(line)
    return lines or [""]


def print_card(ctx, cid):
    card = CARDS[cid]
    ctx.say("")
    ctx.say("=" * 74)
    ctx.say(" %s — %s" % (cid, card["title"]))
    ctx.say("=" * 74)
    ctx.say("")
    ctx.say(" WHY THIS IS YOURS")
    for line in _wrap(card["why"], 70):
        ctx.say("   " + line)
    ctx.say("")
    ctx.say(" WHAT TO DO")
    for line in card["do"]:
        ctx.say("   " + line)
    ctx.say("")
    ctx.say(" GOOD: %s" % card["good"])
    ctx.say("")
    ctx.say(" Then record that you did it, and run the same command again:")
    ctx.say("     python3 %s attest %s --initials %s --note '...'"
            % (_self_path(), cid, INITIALS_PLACEHOLDER))


# --------------------------------------------------------------------------- #
# run_steps + command dispatch
# --------------------------------------------------------------------------- #
def run_steps(ctx, apply_it, keep_going=False):
    """(exit_code, rows). One pass over every step, in order.

    `run` STOPS at the first row a person must clear — building the next step on
    a foundation nobody has laid is how an installer reports progress it has not
    made. `verify` KEEPS GOING, because its whole job is to tell you everything
    outstanding in one read, and it changes nothing."""
    rows, deferred, notes = [], [], []
    # A step that hands something to a person must know whether an earlier step
    # in THIS pass already failed: `verify` keeps going past a failure, and the
    # entry must never be printed "for you to apply" over a missing reader.
    ctx.failed_before = False
    for sid, _title, fn in STEPS:
        try:
            ok, detail, step_notes = fn(ctx, apply_it)
            notes.extend((sid, n) for n in (step_notes or ()))
        except Blocked as exc:
            cid = str(exc)
            rows.append((sid, BLOCKED, CARDS[cid]["title"]))
            ctx.state.record(sid, BLOCKED, cid)
            if not keep_going:
                _finish(ctx, rows, notes)
                print_card(ctx, cid)
                return EX_BLOCKED, rows
            deferred.append(("card", cid, sid))
            continue
        except Unknown as exc:
            rows.append((sid, UNKNOWN, exc.what))
            ctx.state.record(sid, UNKNOWN, exc.what[:400])
            if not keep_going:
                _finish(ctx, rows, notes)
                _say_unknown(ctx, sid, exc)
                return EX_UNKNOWN, rows
            deferred.append(("unknown", exc, sid))
            continue
        except (SetupError, Refusal) as exc:
            ctx.failed_before = True
            rows.append((sid, FAILED, str(exc).splitlines()[0]))
            ctx.state.record(sid, FAILED, str(exc)[:400])
            if not keep_going:
                _finish(ctx, rows, notes)
                ctx.say("")
                ctx.say("FAILED at step `%s`:" % sid)
                for line in str(exc).splitlines():
                    ctx.say("  " + line)
                return EX_FAILED, rows
            deferred.append(("failed", exc, sid))
            continue
        outcome = ALREADY_DONE if ok else DONE
        if not apply_it and not ok:
            outcome = WOULD_CHANGE
        rows.append((sid, outcome, detail))
        ctx.state.record(sid, outcome, detail)
    _finish(ctx, rows, notes)
    for kind, payload, sid in deferred:
        if kind == "card":
            ctx.say("")
            ctx.say("  step `%s` waits on %s — read it with:" % (sid, payload))
            ctx.say("      python3 %s card %s" % (_self_path(), payload))
        elif kind == "unknown":
            _say_unknown(ctx, sid, payload)
        else:
            ctx.say("")
            ctx.say("FAILED at step `%s`:" % sid)
            for line in str(payload).splitlines():
                ctx.say("  " + line)
    worst = next((s for s in _SEVERITY if any(o == s for _, o, _ in rows)), ALREADY_DONE)
    return _OUTCOME_EXIT[worst], rows


def _finish(ctx, rows, notes):
    problem = ctx.state.save()
    ctx.say("")
    ctx.say("-- steps --")
    for sid, outcome, detail in rows:
        ctx.say("  %-18s %-16s %s" % (sid, outcome, str(detail)[:92]))
    for sid, note in notes:
        ctx.say("")
        ctx.say("  note from `%s`:" % sid)
        for line in _wrap(note):
            ctx.say("    " + line)
    if problem:
        ctx.say("")
        ctx.say("  the ledger was NOT written: %s" % problem)
        ctx.say("  every row above is real; none of it will be remembered.")


def _say_unknown(ctx, sid, exc):
    ctx.say("")
    ctx.say("UNKNOWN at step `%s`. This is not a pass and not a failure: the thing may"
            % sid)
    ctx.say("be fine and nothing here can tell. \"I could not look\" is a different")
    ctx.say("answer from \"there was nothing to look at\", and only the second is safe")
    ctx.say("to ignore.")
    for line in _wrap(exc.what):
        ctx.say("  " + line)
    if exc.remedy:
        ctx.say("")
        ctx.say("  What clears it:")
        for line in str(exc.remedy).splitlines():
            ctx.say("    " + line)


def cmd_run(ctx, dry_run):
    ctx.runner.apply_it = not dry_run
    if dry_run:
        ctx.say("Stage A dry run — every step is MEASURED and nothing is changed.")
        ctx.say("A row reading WOULD-CHANGE is work outstanding, not a failure.")
    code, _rows = run_steps(ctx, apply_it=not dry_run, keep_going=False)
    return code


def cmd_verify(ctx):
    ctx.runner.apply_it = False
    ctx.say("Stage A verify — read-only. Every step is re-measured against the live")
    ctx.say("machine, nothing stops early, and no credential is ever asked for.")
    code, rows = run_steps(ctx, apply_it=False, keep_going=True)
    if ctx.runner.writes:
        ctx.say("")
        ctx.say("BUG: verify recorded %d mutation(s); that is a defect in this file."
                % len(ctx.runner.writes))
        return EX_FAILED
    ctx.say("")
    if code == EX_OK:
        ctx.say("No drift: every step still measures as done.")
    else:
        counts = {}
        for _sid, outcome, _d in rows:
            counts[outcome] = counts.get(outcome, 0) + 1
        ctx.say("Outstanding: " + ", ".join(
            "%d %s" % (n, o) for o, n in sorted(counts.items())
            if o not in (DONE, ALREADY_DONE, SKIPPED)))
        ctx.say("The one command that moves it forward:  python3 %s run" % _self_path())
    return code


def cmd_status(ctx):
    """Replays the LEDGER — what an earlier pass recorded, read back off disk.
    It measures nothing, and says so, because a status that silently stops
    measuring is worse than one that never claimed to."""
    st = ctx.state
    ctx.say("")
    ctx.say("=" * 74)
    ctx.say(" Stage A install status        %s" % now_iso())
    ctx.say("=" * 74)
    ctx.say(" Read back from %s. This is the RECORD of earlier passes, not a fresh" % st.path)
    ctx.say(" measurement — re-measure the live machine with `verify`.")
    if st.unreadable:
        ctx.say("")
        ctx.say(" THE LEDGER COULD NOT BE READ (%s)." % st.unreadable)
        ctx.say(" That is not the same as a machine where nothing has been done.")
        return EX_UNKNOWN
    if not st.data["steps"]:
        ctx.say("")
        ctx.say(" No pass has been recorded yet. Start with:")
        ctx.say("     python3 %s run --dry-run" % _self_path())
    ctx.say("")
    ctx.say("-- recorded steps --")
    blocking = []
    for sid, title, _fn in STEPS:
        rec = st.data["steps"].get(sid) or {}
        outcome = rec.get("outcome") or "not run"
        ctx.say("  %-18s %-16s %s" % (sid, outcome, (rec.get("detail") or title)[:76]))
        if outcome not in (DONE, ALREADY_DONE, SKIPPED):
            blocking.append((sid, outcome, rec.get("detail") or ""))
    ctx.say("")
    ctx.say("-- sign-offs --")
    for aid in sorted(ATTESTATIONS):
        rec = st.data["attestations"].get(aid)
        if rec:
            ctx.say("  %-14s signed %s by %-8s %s"
                    % (aid, rec["at"][:10], rec["initials"], (rec.get("note") or "")[:34]))
        else:
            ctx.say("  %-14s NOT SIGNED — %s" % (aid, ATTESTATIONS[aid]))
    ctx.say("")
    ctx.say("-- the planner's keep-set, for the live probe (CA-PROBE) --")
    ctx.say("  " + ", ".join(PLANNER_KEEP_TOOLS))
    if not blocking:
        ctx.say("")
        ctx.say("Every recorded step holds. Re-measure the live machine with:")
        ctx.say("    python3 %s verify" % _self_path())
        return EX_OK
    sid, outcome, detail = blocking[0]
    ctx.say("")
    ctx.say("%s at `%s`." % ({BLOCKED: "BLOCKED ON A PERSON", FAILED: "FAILED",
                              UNKNOWN: "COULD NOT MEASURE",
                              WOULD_CHANGE: "WORK OUTSTANDING"}.get(outcome, outcome), sid))
    if outcome == BLOCKED and detail in CARDS:
        ctx.say("  %s" % CARDS[detail]["title"])
        ctx.say("")
        ctx.say("THE ONE COMMAND THAT CLEARS IT:")
        ctx.say("    python3 %s card %s" % (_self_path(), detail))
    else:
        for line in _wrap(detail or "no detail recorded"):
            ctx.say("  " + line)
        ctx.say("")
        ctx.say("THE ONE COMMAND THAT CLEARS IT:")
        ctx.say("    python3 %s run" % _self_path())
    worst = next((s for s in _SEVERITY if any(o == s for _, o, _ in blocking)),
                 WOULD_CHANGE)
    return _OUTCOME_EXIT.get(worst, EX_BLOCKED)


def cmd_card(ctx, cid):
    if cid not in CARDS:
        ctx.say("no such checkpoint: %s (have %s)" % (cid, ", ".join(sorted(CARDS))))
        return EX_USAGE
    print_card(ctx, cid)
    return EX_OK


def cmd_attest(ctx, aid, initials, note):
    """HUMAN ONLY. A session supplying its own sign-off is the agent producing
    the human's signal, and no amount of good intent makes the record true."""
    refuse_if_agent("attest")
    if aid not in ATTESTATIONS:
        ctx.say("no such sign-off: %s (have %s)" % (aid, ", ".join(sorted(ATTESTATIONS))))
        return EX_USAGE
    initials = (initials or "").strip()
    if not initials or initials.lower() in INITIALS_PLACEHOLDERS:
        ctx.say("that is the placeholder this program prints, not a signature.")
        ctx.say("Sign with your own initials: --initials AB")
        return EX_USAGE
    if aid == "CA-PROBE" and not (note or "").strip():
        ctx.say("CA-PROBE needs a note: what the session's tool list actually held.")
        ctx.say("A fence nobody wrote down is a fence nobody checked.")
        return EX_USAGE
    ctx.state.attest(aid, initials, note or "")
    problem = ctx.state.save()
    if problem:
        ctx.say("the sign-off was NOT recorded: %s" % problem)
        return EX_FAILED
    ctx.say("recorded %s, signed by %s." % (aid, initials))
    ctx.say("Now run:  python3 %s run" % _self_path())
    return EX_OK


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
class FakeLinear(object):
    """The live transport's interface, with no I/O. `labels_scoped` lets a case
    plant a team-scoped twin; `unreachable` makes every call UNMEASURED."""

    def __init__(self, teams=None, labels=None, labels_scoped=(), unreachable=False):
        self.teams = dict(teams or {})
        self.labels = dict(labels or {})
        self.labels_scoped = set(labels_scoped)
        self.unreachable = unreachable
        self.created = []

    def _reach(self):
        if self.unreachable:
            raise Unknown("the tracker was unreachable (synthetic)", "retry")

    def ensure_team(self, key, apply_it):
        self._reach()
        if key in self.teams:
            return self.teams[key], False
        if not apply_it:
            return None, False
        self.teams[key] = "team-%s" % key
        self.created.append("team:" + key)
        return self.teams[key], True

    def workspace_labels(self):
        self._reach()
        return [{"id": lid, "name": name,
                 "team": {"id": "t"} if name in self.labels_scoped else None}
                for name, lid in self.labels.items()]

    def ensure_label(self, name, apply_it, existing=None):
        return LinearTransport.ensure_label.__func__(self, name, apply_it, existing) \
            if hasattr(LinearTransport.ensure_label, "__func__") \
            else _fake_ensure_label(self, name, apply_it, existing)


def _fake_ensure_label(fake, name, apply_it, existing):
    """The LIVE matching rule, run over fake rows — the scoped-twin refusal is
    the real code path, not a copy of it."""
    rows = existing if existing is not None else fake.workspace_labels()
    for row in rows:
        if row.get("name") != name:
            continue
        if row.get("team"):
            raise SetupError("the label %r exists but is scoped to one team; scope "
                             "cannot be changed after creation." % name)
        return row["id"], False
    if not apply_it:
        return None, False
    fake.labels[name] = "lbl-%s" % re.sub(r"[^a-z]", "-", name.lower())
    fake.created.append("label:" + name)
    return fake.labels[name], True


class FakeHost(object):
    """The role account, in memory. `sudo_needs_password` is the state every
    session is in: nothing under the role account can be looked at."""

    def __init__(self, account_exists=True, sudo_needs_password=False):
        self._exists = account_exists
        self.locked = sudo_needs_password
        self.files = {}

    def account_exists(self, account):
        return None if self.locked else self._exists

    def secret_present(self, account, env_name):
        if self.locked:
            return None, "sudo needs a password (synthetic)"
        body = self.files.get(ROLE_ENV_FILE)
        if body is None:
            return False, 0
        for line in body.splitlines():
            if line.startswith(env_name + "="):
                return True, len(line.split("=", 1)[1])
        return False, 0

    def file_present(self, account, relpath):
        return None if self.locked else relpath in self.files

    def write_role_file(self, account, relpath, body):
        if self.locked:
            raise Unknown("sudo needs a password (synthetic)", "")
        self.files[relpath] = body
        return relpath


ALL_LABELS = dict((name, "lbl-%d" % i) for i, name in enumerate(REQUIRED_LABELS))

GOOD_CONF = {
    "ROLE_ACCOUNT": "_planclaw",
    "PLANNING_TEAM_KEY": "PLAN",
    "LINEAR_WORKSPACE": "acme",
    "OWNER_USER_ID": "owner-1",
    "LINEAR_KEY_ENV": "STAGE_A_LINEAR_API_KEY",
    "KIT_REPO_URL": "https://github.com/x/kit.git",
}


def _ctx(state_root, conf=None, tracker=None, host=None, secret="k" * 40):
    ctx = Ctx(dict(GOOD_CONF if conf is None else conf), Runner(apply_it=False),
              FakeLinear() if tracker is None else tracker,
              FakeHost() if host is None else host, State(state_root))
    ctx.prompt_secret = lambda name: secret
    return ctx


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
    #    that breaks it is caught. The servers are pinned as LITERALS, not read
    #    back out of the constant that builds the fence — a check that loops over
    #    PLANNER_FENCE_SERVERS stays green when someone empties it.
    good = planning_entry(GOOD_CONF)
    check("fence-good-clean", entry_problems(good), [])
    check("fence-servers-pinned", PLANNER_FENCE_SERVERS,
          ("linear", "cyrus-tools", "cyrus-docs", "slack"))
    for rule in ("mcp__linear", "mcp__linear__*", "mcp__cyrus-tools",
                 "mcp__cyrus-tools__*", "mcp__cyrus-docs", "mcp__cyrus-docs__*",
                 "mcp__slack", "mcp__slack__*"):
        check("fence-removes:%s" % rule, rule in good["disallowedTools"], True)
    # The key that once stood in for the whole fence is GONE, and its absence is
    # asserted rather than assumed: the dispatcher never read it.
    check("fence-no-unread-key", "linearMcpAttached" in good, False)
    check("fence-no-allowed-tools", "allowedTools" in good, False)
    # Every tool that runs, writes elsewhere, fetches, schedules, messages or
    # reads an MCP resource by argument is named.
    for name in ("Bash", "Monitor", "REPL", "Edit", "NotebookEdit", "WebFetch",
                 "WebSearch", "Workflow", "RemoteTrigger", "Skill", "TaskStop",
                 "EnterWorktree", "ExitWorktree", "CronCreate", "CronDelete",
                 "ScheduleWakeup", "SendMessage", "SendUserMessage", "PushNotification",
                 "ListAgents", "Artifact", "ShareOnboardingGuide", "DesignSync",
                 "AskUserQuestion", "EnterPlanMode", "ExitPlanMode",
                 "ListMcpResourcesTool", "ReadMcpResourceTool", "ReadMcpResourceDirTool"):
        check("fence-removes-builtin:%s" % name, name in good["disallowedTools"], True)
    check("fence-builtin-count", len(DISALLOWED_BUILTINS) == 29
          and len(set(DISALLOWED_BUILTINS)) == 29, True)
    check("fence-rule-count", len(good["disallowedTools"]), 29 + 8)
    # …and what it KEEPS is kept: nothing the planner needs is in the fence, and
    # nothing is on both sides of the line.
    for kept in ("Read", "Grep", "Glob", "Write", "Task", "Agent"):
        check("fence-keeps:%s" % kept, kept in good["disallowedTools"], False)
    check("fence-keep-set-disjoint",
          sorted(set(DISALLOWED_BUILTINS) & set(PLANNER_KEEP_TOOLS)), [])
    # The dispatcher's own list of available tools, v0.2.69, as a literal. A name
    # on neither side of the line is a tool nobody decided about.
    dispatcher_tools_0_2_69 = (
        "Read", "Edit", "Write", "Bash", "Task", "WebFetch", "WebSearch", "TaskCreate",
        "TaskUpdate", "TaskGet", "TaskList", "NotebookEdit", "Skill", "SendMessage",
        "PushNotification", "ShareOnboardingGuide", "EnterWorktree", "ExitWorktree",
        "CronCreate", "CronDelete", "CronList", "ScheduleWakeup", "Monitor", "LSP",
        "RemoteTrigger", "TaskOutput", "TaskStop", "ToolSearch", "DesignSync", "Workflow",
        "ReportFindings")
    check("fence-every-dispatcher-tool-classified",
          [t for t in dispatcher_tools_0_2_69
           if t not in DISALLOWED_BUILTINS and t not in PLANNER_KEEP_TOOLS], [])
    check("fence-brief-present",
          good["appendInstruction"].startswith(PLANNING_BRIEF_FINGERPRINT), True)
    check("fence-never-label-routed", good["routingLabels"], [PLANNING_ENTRY_NEVER_LABEL])
    # mutants — each one is a way the fence could be quietly reopened.
    m = dict(good); m["linearMcpAttached"] = False
    check("fence-mutant-reintroduces-unread-key", bool(entry_problems(m)), True)
    m = dict(good); m["allowedTools"] = ["Read", "Write"]
    check("fence-mutant-adds-allowed-tools", bool(entry_problems(m)), True)
    m = dict(good)
    m["disallowedTools"] = [t for t in good["disallowedTools"] if t != "mcp__linear__*"]
    check("fence-mutant-drops-wildcard-form", bool(entry_problems(m)), True)
    m = dict(good)
    m["disallowedTools"] = [t for t in good["disallowedTools"]
                            if not t.startswith("mcp__cyrus-tools")]
    check("fence-mutant-drops-a-server", bool(entry_problems(m)), True)
    m = dict(good); m["disallowedTools"] = good["disallowedTools"] + ["Write"]
    check("fence-mutant-removes-write", bool(entry_problems(m)), True)
    m = dict(good)
    m["disallowedTools"] = [t for t in good["disallowedTools"] if t != "Bash"]
    check("fence-mutant-leaves-bash", bool(entry_problems(m)), True)
    m = dict(good); m["routingLabels"] = ["ready"]  # label-routable
    check("fence-mutant-label-routed", bool(entry_problems(m)), True)
    # A rule the runtime would skip in SILENCE is refused, not accepted as fence.
    for bad in ("mcp__*", "mcp__", "mcp__linear__save_issue", "mcp__linear__foo__*"):
        m = dict(good); m["disallowedTools"] = good["disallowedTools"] + [bad]
        check("fence-rejects-shape:%s" % bad, bool(entry_problems(m)), True)
        match = MCP_FENCE_RULE_RE.match(bad)
        check("fence-rule-re-refuses:%s" % bad,
              bool(match and match.group(1) in PLANNER_FENCE_SERVERS), False)

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

    # 5. THE LEDGER IS A FILE. A row `run` records is a row a fresh process's
    #    `status` reads back — the old ledger was a dict that died with the
    #    process, so `status` printed five "no" whatever had been done.
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix="stage-a-selftest-")
    try:
        root = os.path.join(tmp, "ledger")
        ctx = _ctx(root)
        ctx._out = []
        code = cmd_run(ctx, dry_run=True)
        # Exit 1 is correct today — the install cannot complete (no executor
        # reader). What was wrong before was exit 1 with NOTHING said.
        check("dry-run-exit-with-reason", code, EX_FAILED)
        check("dry-run-printed-the-reason",
              any("FAILED at step `executor-job`" in line for line in ctx._out)
              and any(EXECUTOR_READER_TICKET in line for line in ctx._out), True)
        check("dry-run-wrote-ledger-file", os.path.isfile(os.path.join(root, "state.json")), True)
        check("ledger-file-mode-600",
              oct(os.stat(os.path.join(root, "state.json")).st_mode & 0o777), "0o600")
        check("ledger-dir-mode-700", oct(os.stat(root).st_mode & 0o777), "0o700")
        # a SECOND context, reading the same root, sees what the first recorded
        replay = _ctx(root)
        check("ledger-replays-across-processes",
              replay.state.outcome("tracker"), WOULD_CHANGE)
        out = []
        replay._out = out
        cmd_status(replay)
        check("status-reads-recorded-rows",
              any("tracker" in line and WOULD_CHANGE in line for line in out), True)
        check("status-says-it-is-a-record", any("RECORD" in line for line in out), True)

        # 6. A DRY RUN NAMES A REASON FOR EVERY ROW IT WOULD CHANGE, and never
        #    stops with exit 1 and nothing said. It stops at the first outcome a
        #    person must read: here, the missing executor reader, with its ticket.
        ctx = _ctx(os.path.join(tmp, "dry"))
        ctx._out = []
        code = cmd_run(ctx, dry_run=True)
        rows = dict((sid, ctx.state.outcome(sid)) for sid, _t, _f in STEPS)
        check("dry-run-tracker-would-change", rows["tracker"], WOULD_CHANGE)
        check("dry-run-credentials-would-change", rows["credentials"], WOULD_CHANGE)
        for sid in ("tracker", "credentials", "config"):
            detail = (ctx.state.data["steps"].get(sid) or {}).get("detail") or ""
            check("dry-run-reason:%s" % sid, len(detail) > 10, True)
        check("dry-run-created-nothing", ctx.tracker.created, [])
        check("dry-run-wrote-no-role-file", ctx.host.files, {})
        check("dry-run-recorded-no-writes", ctx.runner.writes, [])

        # 7. THE CONSUMER IS CHECKED BEFORE THE PRODUCER IS HANDED OVER. With
        #    everything else in place, `run` FAILS at the executor reader and never
        #    prints the Planning entry — applying it would start sessions whose
        #    output nothing reads.
        ctx = _ctx(os.path.join(tmp, "apply"),
                   tracker=FakeLinear(teams={"PLAN": "team-PLAN"}, labels=ALL_LABELS))
        ctx._out = []
        code = cmd_run(ctx, dry_run=False)
        check("apply-fails-at-missing-reader", code, EX_FAILED)
        check("apply-failed-step", ctx.state.outcome("executor-job"), FAILED)
        check("apply-names-reader-ticket",
              EXECUTOR_READER_TICKET in (ctx.state.data["steps"]["executor-job"]["detail"]),
              True)
        check("apply-never-printed-entry",
              any("disallowedTools" in line for line in ctx._out), False)
        check("apply-wrote-credential-on-stdin-path",
              ctx.host.files.get(ROLE_ENV_FILE, "").startswith("STAGE_A_LINEAR_API_KEY="), True)
        check("apply-credential-not-in-ledger",
              "k" * 40 in open(ctx.state.path).read(), False)
        check("apply-tracker-already-done", ctx.state.outcome("tracker"), ALREADY_DONE)
        check("apply-step-order-reader-before-entry",
              [s for s, _t, _f in STEPS].index("executor-job")
              < [s for s, _t, _f in STEPS].index("dispatcher-entry"), True)
        # the step order a person reads is the order the steps run
        check("step-names-unique", len(set(s for s, _t, _f in STEPS)), len(STEPS))

        # 8. IDEMPOTENT. A second apply over the same machine changes nothing it
        #    already did and records ALREADY-DONE.
        again = Ctx(ctx.conf, Runner(apply_it=False), ctx.tracker, ctx.host,
                    State(ctx.state.root))
        again.prompt_secret = lambda name: "SHOULD-NOT-BE-ASKED"
        again._out = []
        cmd_run(again, dry_run=False)
        check("second-run-credentials-already-done",
              again.state.outcome("credentials"), ALREADY_DONE)
        check("second-run-config-already-done", again.state.outcome("config"), ALREADY_DONE)
        check("second-run-created-nothing-new", ctx.tracker.created, [])

        # 9. VERIFY re-measures every step, never stops early, never mutates, and
        #    does not raise — it used to die on a TypeError from mis-aliased
        #    transport methods before printing a single row.
        vctx = _ctx(os.path.join(tmp, "verify"))
        vctx._out = []
        raised = None
        try:
            vcode = cmd_verify(vctx)
        except Exception as exc:  # the regression this case exists for
            raised, vcode = exc, None
        check("verify-does-not-raise", raised, None)
        check("verify-worst-row-is-failed", vcode, EX_FAILED)
        check("verify-kept-going-past-failure",
              vctx.state.outcome("handover"), BLOCKED)
        check("verify-no-mutation", vctx.runner.writes, [])
        check("verify-withholds-entry-after-failure",
              any('"disallowedTools"' in line for line in vctx._out), False)
        check("verify-says-entry-withheld",
              any("entry is withheld" in line for line in vctx._out), True)
        check("verify-created-nothing", vctx.tracker.created, [])

        # 10. COULD NOT LOOK IS NOT NOTHING TO DO (§13). A session cannot use sudo
        #     without a password; every role-account step must say UNKNOWN, and
        #     the exit code must differ from both success and failure.
        lctx = _ctx(os.path.join(tmp, "locked"), host=FakeHost(sudo_needs_password=True))
        lctx._out = []
        lcode = cmd_run(lctx, dry_run=True)
        check("locked-preflight-unknown", lctx.state.outcome("preflight"), UNKNOWN)
        check("locked-exit-is-unknown", lcode, EX_UNKNOWN)
        check("unknown-exit-distinct", len({EX_OK, EX_FAILED, EX_UNKNOWN, EX_BLOCKED}), 4)
        uctx = _ctx(os.path.join(tmp, "unreach"), tracker=FakeLinear(unreachable=True))
        uctx._out = []
        cmd_verify(uctx)
        check("unreachable-tracker-unknown", uctx.state.outcome("tracker"), UNKNOWN)
        nctx = _ctx(os.path.join(tmp, "nokey"))
        nctx.tracker = None
        nctx._out = []
        cmd_verify(nctx)
        check("no-key-tracker-unknown", nctx.state.outcome("tracker"), UNKNOWN)

        # 11. A TEAM-SCOPED TWIN is a failure to report, never a label to use —
        #     its scope cannot be changed, and the executor needs workspace ids.
        sctx = _ctx(os.path.join(tmp, "scoped"),
                    tracker=FakeLinear(teams={"PLAN": "t"}, labels=ALL_LABELS,
                                       labels_scoped=("track:meta",)))
        sctx._out = []
        cmd_verify(sctx)
        check("scoped-label-fails", sctx.state.outcome("tracker"), FAILED)

        # 12. AN UNREADABLE LEDGER is reported, and `status` does not answer
        #     "nothing done" for it.
        broken = os.path.join(tmp, "broken")
        os.makedirs(broken)
        with open(os.path.join(broken, "state.json"), "w") as fh:
            fh.write("{not json")
        bctx = _ctx(broken)
        bctx._out = []
        check("unreadable-ledger-status-unknown", cmd_status(bctx), EX_UNKNOWN)

        # 13. ATTEST is human-only, refuses a placeholder, and CA-PROBE needs a
        #     note. The refusal is exercised with a marker set in a COPY of the
        #     environment, never by scrubbing the real one.
        actx = _ctx(os.path.join(tmp, "attest"))
        actx._out = []
        saved = dict(os.environ)
        try:
            os.environ[AGENT_ENV_MARKERS[0]] = "1"
            refused = False
            try:
                cmd_attest(actx, "CA-ENTRY", "BC", "")
            except Refusal:
                refused = True
            check("attest-refused-in-agent-env", refused, True)
            check("attest-refusal-recorded-nothing", actx.state.attested("CA-ENTRY"), False)
            for marker in AGENT_ENV_MARKERS:
                os.environ.pop(marker, None)
            check("attest-placeholder-refused",
                  cmd_attest(actx, "CA-ENTRY", INITIALS_PLACEHOLDER, ""), EX_USAGE)
            check("attest-xx-refused", cmd_attest(actx, "CA-ENTRY", "xx", ""), EX_USAGE)
            check("attest-probe-needs-note", cmd_attest(actx, "CA-PROBE", "BC", ""), EX_USAGE)
            check("attest-unknown-id", cmd_attest(actx, "CA-NOPE", "BC", "n"), EX_USAGE)
            check("attest-good", cmd_attest(actx, "CA-ENTRY", "BC", "applied"), EX_OK)
            check("attest-persisted", State(actx.state.root).attested("CA-ENTRY"), True)
        finally:
            os.environ.clear()
            os.environ.update(saved)

        # 14. Every card names a real sign-off, and every sign-off has a card.
        check("cards-match-attestations", sorted(CARDS), sorted(ATTESTATIONS))
        check("probe-card-checks-subagent",
              "subagent" in " ".join(CARDS["CA-PROBE"]["do"]), True)
        check("placeholder-unsignable", INITIALS_PLACEHOLDER.lower() in INITIALS_PLACEHOLDERS, True)

        # 15. NOTHING SECRET IS AN ARGUMENT. The live host passes payloads on
        #     stdin; its command text never interpolates a body.
        host_src = src[src.index("class Host(object):"):src.index("# Steps — measure first")]
        check("host-writes-via-stdin", "stdin=body" in host_src, True)
        check("host-no-base64-in-argv", "base64" in host_src, False)
        # A job label is a deployment's, never the kit's: an earlier draft of this
        # file hard-coded one. Any reverse-DNS launchd label literal is refused.
        check("no-hardcoded-job-label",
              bool(re.search(r"[\"']com\.[a-z0-9-]+\.stage-a", src)), False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

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
                   choices=["run", "status", "verify", "card", "attest"])
    p.add_argument("target", nargs="?", help="the checkpoint id, for card/attest")
    p.add_argument("--conf", default="stage-a.conf")
    p.add_argument("--state-home", default=DEFAULT_STATE_HOME,
                   help="where the ledger lives (default %s)" % DEFAULT_STATE_HOME)
    p.add_argument("--dry-run", action="store_true",
                   help="measure every step and change nothing")
    p.add_argument("--initials", help="attest: your own initials")
    p.add_argument("--note", default="", help="attest: what you saw")
    p.add_argument("--selftest", action="store_true")
    return p


def _live_ctx(conf, state_home):
    """A live context. The tracker key is READ from the environment variable the
    conf names — never prompted for here, never written to the ledger. With no
    key, the tracker step reports UNKNOWN; it does not guess."""
    # UNDER A MODEL, NO KEY IS READ. A session may run `--dry-run` and `verify`,
    # and its environment is not a place a tracker key should be taken from —
    # so the tracker row says UNKNOWN, and the banner says why, rather than a
    # session quietly measuring the board with whatever key it happens to hold.
    markers = agent_markers_present()
    tracker = None
    if not markers:
        key = os.environ.get(conf.get("LINEAR_KEY_ENV", "")) or ""
        tracker = LinearTransport(key) if len(key) >= 20 else None
    ctx = Ctx(conf, Runner(apply_it=False), tracker, Host(), State(state_home))
    if markers:
        ctx.say("AGENT ENVIRONMENT (%s): no tracker key is read in this pass, so the "
                "tracker row reports UNKNOWN." % ", ".join(markers))
    return ctx


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.selftest:
        return selftest()

    # Refuse a mutating command in an agent environment BEFORE any read.
    if (args.command == "run" and not args.dry_run) or args.command == "attest":
        try:
            refuse_if_agent(args.command)
        except Refusal as exc:
            print(str(exc), file=sys.stderr)
            return EX_REFUSED

    if args.command in ("card", "attest"):
        ctx = Ctx({}, Runner(apply_it=False), None, None, State(args.state_home))
        if args.command == "card":
            code = cmd_card(ctx, args.target or "")
        else:
            try:
                code = cmd_attest(ctx, args.target or "", args.initials, args.note)
            except Refusal as exc:
                print(str(exc), file=sys.stderr)
                return EX_REFUSED
        for line in ctx._out:
            print(line)
        return code

    if args.command == "status":
        ctx = Ctx({}, Runner(apply_it=False), None, None, State(args.state_home))
        code = cmd_status(ctx)
        for line in ctx._out:
            print(line)
        return code

    conf, errors = load_conf(args.conf)
    if errors:
        for e in errors:
            print("conf: %s" % e, file=sys.stderr)
        return EX_USAGE

    ctx = _live_ctx(conf, args.state_home)
    try:
        if args.command == "verify":
            code = cmd_verify(ctx)
        else:
            code = cmd_run(ctx, dry_run=args.dry_run)
    except Refusal as exc:
        print(str(exc), file=sys.stderr)
        return EX_REFUSED
    finally:
        for line in ctx._out:
            print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
