#!/usr/bin/env python3
"""`delivery.json` validator — the contract §7 checklist, mechanized.

Deterministic, model-free, stdlib only. Without this, a project can adopt the
pipeline with a malformed config and only find out at dispatch time, when the
failure is expensive and remote.

TWO LAYERS, ONE DEFINITION OF SHAPE

    SHAPE comes from `schemas/delivery.schema.json` — a real JSON Schema, and
    the single machine-readable rendering of contract §1. This file no longer
    re-implements "stateIds is an object with exactly five string values" in
    Python; it validates against the schema and translates each violation into
    the rule vocabulary and tier §7 uses (via the schema's own `x-rule`,
    `x-tier` and `x-fix` annotations). `scripts/check_schemas.py` fails CI if
    §1 and the schema disagree about which fields exist, so the two cannot
    drift the way the contract and this validator once could.

    SEMANTICS stay here, because a schema structurally cannot express them:
    an ID that is RESOLVED rather than merely a string, a path that lands
    outside every worktree ON THIS DISK, `perEffort[e].maxTurns` compared
    against `budgets.maxTurns`, `statePath` against `backend`, the exact
    `riskPaths` and `labels.required` floors, and `branch.types` against the
    LIVE guard's own regex.

    A floor is the class a schema is furthest from expressing: the schema says
    `labels.required` is an array of strings and would accept `[]`, but the
    empty list is precisely the config that turns the parking brake off.

    This is defense in depth, not a handoff. A schema-valid config can still
    carry a UUID that resolves to nothing.

THE ONE RULE THAT OUTRANKS EVERY OTHER RULE HERE (contract §2):

    delivery.json ABSENT is OFF, not broken. This validator exits 0 and emits
    NOTHING — no diagnostics, no git, no network. Off must be indistinguishable
    from a kit checkout that never heard of the pipeline. So the existence test
    runs FIRST, ahead of anything that can fail: a fail-closed guard whose
    PRECONDITION is missing takes the project hostage, and because the guard
    machinery is self-protected the agent cannot repair it (docs/LESSONS.md).

    delivery.json PRESENT but unparseable, or failing a §7 rule, is BROKEN:
    fail with a reason naming the file and the fix. Presence is a promise.

The kit itself ships `delivery.example.json` and never a live `delivery.json`,
so in THIS repo the validator is inert by design — `--selftest` is what CI runs,
and it exercises every rule against synthetic configs.

Two tiers:

  ERRORS   — the §7 MUST-fail rows, plus the structural preconditions a §7 row
             cannot be evaluated without (e.g. `budgets.maxTurns` must exist and
             be an integer before "perEffort may only lower the cap" means
             anything). §7 is a floor, not a ceiling.
  WARNINGS — everything the contract specifies but does not make a MUST-fail:
             unresolved non-required label IDs, an unknown `dispatch.backend`,
             a non-zero `autonomy.autoMergeMaxLines`, and similar. `--strict`
             promotes them. Which tier a schema violation lands in is declared
             in the schema, as `x-tier` / `x-tier-<keyword>`.

`telemetry` is defined in the schema (it is part of §1) but DELIBERATELY not
judged here. Every §7 row gates autonomy; telemetry gates nothing, and a
project with a misconfigured sink loses dashboards, not supervision. Its own
consumer validates it at the point of use.

Every message cites the contract section it comes from, because the fix almost
always lives in the prose, not in the field name.

Config source: the WORKING-TREE copy. Contract §1 sends guard-relevant reads to
the committed copy on the default branch (`git show origin/main:delivery.json`)
precisely because a session can edit its own worktree — but this validator is
not a guard and grants a session nothing. It judges the config as authored in
this tree, which is the only useful thing to judge in the PR that changes it.
Same reasoning as `scripts/check_ticket_dor.py`.

Usage:
    check_delivery_config.py [--strict] [--json] [--config PATH] [--repo-root DIR]
    check_delivery_config.py --selftest

Exit: 0 = off, or valid (warnings alone do not fail)
      1 = broken (unparseable, or a rule failed)
      2 = usage/IO error (an explicit --config that does not exist, or a kit
          checkout whose delivery schema is missing or unreadable)
"""
import argparse
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jsonschema_mini as jsm  # noqa: E402

SUPPORTED_VERSION = 1  # §1: an unrecognized version refuses to run, it does not guess

SCHEMA_FILE = os.path.join("schemas", "delivery.schema.json")

# Fallback only. The live list is parsed out of the PreToolUse branch-naming
# guard; --selftest fails if this, that guard, and the schema's own enum ever
# disagree, so `branch.types` can never drift into accepting a type the guard
# blocks before the first edit.
FALLBACK_BRANCH_TYPES = ["feat", "fix", "chore", "refactor", "docs"]
HOOK_FILE = os.path.join(".claude", "hooks", "pre-tool-use.py")
HOOK_ASSIGN_RE = re.compile(r"BRANCH_NAME_RE\s*=\s*re\.compile\(")
# Every string literal in the call, so implicitly concatenated fragments
# reassemble into one pattern and any comment between them is ignored. A
# deliberately small lexer: it does not handle escaped quotes, and does not need
# to — if it ever fails to find the alternation the selftest goes red rather
# than falling back silently.
HOOK_STRING_RE = re.compile(r"""[rRbBuUfF]{0,2}(\"\"\"|'''|"|')(.*?)\1""", re.DOTALL)
# The branch-type alternation, capturing or non-capturing. Requires at least one
# `|` so it cannot latch onto `(?P<ticket>…)` or any other single-token group.
HOOK_TYPES_RE = re.compile(r"\((?:\?:)?([a-z]+(?:\|[a-z]+)+)\)")

STATE_KEYS = ("raw", "ready", "working", "review", "done")  # §1: closed, mandatory set
COMMAND_KEYS = ("lint", "typecheck", "test", "e2e", "preview")
AUTH_CONTEXTS = ("devSessions", "scheduled", "review")
# §9: the only backend with a durable store of its own (the `pipeline-state`
# Actions artifact), hence the only one whose `statePath` may be null.
SELF_STORING_BACKENDS = ("github-actions",)
# §6's dispatcher-owned label set, verbatim: the labels a session may never
# write and the dispatcher reads to decide whether a ticket moves. A project may
# require MORE keys; it may not require fewer. Validating only the keys a project
# happens to list makes the parking brake opt-in — empty the list and
# `agent:needs-human`, the label a person applies to STOP a ticket, degrades from
# "dispatch refuses" to a stderr warning nobody reads.
REQUIRED_LABEL_KEYS = (
    "agent:queued",
    "agent:working",
    "agent:blocked",
    "agent:needs-human",
    "blocked:capacity",
)
REQUIRED_RISK_PATHS = (
    ".claude/hooks/**",
    ".claude/settings*.json",
    "delivery.json",
)

# §1 defines `telemetry`, and the schema therefore does too — but it is not a
# §7 row and this validator says nothing about it. See the module docstring.
UNJUDGED_SUBTREES = ("telemetry",)

# An unfilled template token. Written with escapes so this file never itself
# contains a placeholder token and stays invisible to check_placeholders.py.
TOKEN_RE = re.compile(r"\{\{[A-Z0-9_]+\}\}")

MISSING = object()


def dig(config, *keys):
    """Nested lookup returning MISSING rather than raising on any gap."""
    node = config
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return MISSING
        node = node[key]
    return node


def is_int(value):
    """True for a real integer. `bool` is an `int` in Python and is not one here.

    Stricter than the schema's `type: integer`, which follows the spec and
    accepts `150.0`. A cap written as a float is almost certainly a typo, and
    the cross-field comparison below wants a literal.
    """
    return isinstance(value, int) and not isinstance(value, bool)


def unresolved(value):
    """An ID that is absent, blank, or still a template token — never usable."""
    if not isinstance(value, str):
        return True
    return not value.strip() or bool(TOKEN_RE.search(value))


def shape(value):
    """A short type name for messages: the reader needs to see what they wrote."""
    if value is MISSING:
        return "missing"
    return {
        type(None): "null",
        bool: "boolean",
        int: "integer",
        float: "number",
        str: "string",
        list: "array",
        dict: "object",
    }.get(type(value), type(value).__name__)


# --------------------------------------------------------------------------- #
# Repo, worktrees, paths
# --------------------------------------------------------------------------- #
def find_repo_root(start):
    """Walk up from `start` to the nearest directory holding a `.git` entry.

    Filesystem-only on purpose. Discovering the root by shelling out to git
    would put a failure-capable call AHEAD of the §2 existence test, and running
    from a subdirectory would otherwise resolve `./delivery.json` to nothing and
    report a configured project as OFF — the one false negative that matters.
    `.git` is a directory in the main checkout and a file in a worktree; both
    answer os.path.exists.
    """
    here = os.path.abspath(start)
    while True:
        if os.path.exists(os.path.join(here, ".git")):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            return os.path.abspath(start)
        here = parent


def worktree_roots(repo_root):
    """(roots, git_answered) — every worktree root, always including `repo_root`.

    Falls back to `[repo_root]` when git is unavailable or this is not a
    checkout. That narrows the pinsRoot/statePath containment test rather than
    disabling it, so the caller reports the narrowing instead of passing
    quietly. The flag is returned rather than inferred from `len(roots)`: a
    perfectly healthy single-worktree repo also yields exactly one root.
    """
    roots = [os.path.abspath(repo_root)]
    try:
        result = subprocess.run(
            ["git", "-C", repo_root, "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return roots, False
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                root = os.path.abspath(line[len("worktree "):].strip())
                if root not in roots:
                    roots.append(root)
        return roots, True
    except Exception:
        return roots, False


def expand_path(value, repo_root):
    """§1: `~` is expanded to $HOME by readers. A relative path is repo-relative
    — and therefore inside the repo, which is exactly what the caller tests."""
    path = os.path.expanduser(value)
    if not os.path.isabs(path):
        path = os.path.join(repo_root, path)
    return path


def containing_root(path, roots):
    """The first root in `roots` that contains `path`, or None.

    `path` need not exist yet — a pins directory is created at first dispatch.
    realpath still normalizes a non-existent path and resolves the symlinks in
    the ancestors that do exist, which is what makes `~/link-to-repo/pins`
    fail the test the way it should.
    """
    try:
        target = os.path.realpath(path)
    except OSError:
        return None
    for root in roots:
        try:
            resolved = os.path.realpath(root)
        except OSError:
            continue
        if target == resolved or target.startswith(resolved + os.sep):
            return resolved
    return None


def _balanced_call(text, open_paren):
    """The source between `re.compile(` and its matching `)`, or None.

    Escaped parens are skipped so a `\\(` inside the pattern cannot unbalance the
    scan. Parens inside the regex itself — `(?:`, `(?P<ticket>` — are balanced
    and simply nest.
    """
    depth = 0
    index = open_paren
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[open_paren + 1:index]
        index += 1
    return None


def hook_branch_types(repo_root):
    """Branch types the LIVE PreToolUse guard accepts, or None if unreadable.

    Reads the whole `re.compile(...)` call and reassembles its string literals,
    so the answer survives the guard being reformatted. Both of these parse to
    the same five types:

        BRANCH_NAME_RE = re.compile(r"^(feat|fix|chore|refactor|docs)/…$")

        BRANCH_NAME_RE = re.compile(
            r"^(?:feat|fix|chore|refactor|docs)/"
            r"(?:(?P<ticket>[a-z0-9]+-\\d+)-)?"
            r"[a-z0-9][a-z0-9-]*$"
        )

    Returning None on any surprise is deliberate: --selftest treats it as drift
    and fails, which is how a reformat that this parser cannot follow surfaces as
    a red build instead of a silent fallback to the hard-coded list.
    """
    try:
        text = open(os.path.join(repo_root, HOOK_FILE), encoding="utf-8").read()
    except OSError:
        return None
    assign = HOOK_ASSIGN_RE.search(text)
    if not assign:
        return None
    call = _balanced_call(text, assign.end() - 1)
    if call is None:
        return None
    pattern = "".join(m.group(2) for m in HOOK_STRING_RE.finditer(call))
    types = HOOK_TYPES_RE.search(pattern)
    if not types:
        return None
    return [t for t in types.group(1).split("|") if t] or None


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def load_schema(kit_root):
    """The §1 schema, or None when it cannot be read.

    A missing schema is a broken KIT, never a broken project — the caller exits
    2 rather than reporting a project's config as invalid on the strength of a
    file the project does not own.
    """
    try:
        with open(os.path.join(kit_root, SCHEMA_FILE), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def load_config(explicit, repo_root):
    """Return (config, path) or (None, None) when the pipeline is OFF.

    Exits 2 on an explicit --config that cannot be read; exits 1 on a present
    delivery.json that is unreadable or not an object (BROKEN, §2).
    """
    path = explicit or os.path.join(repo_root, "delivery.json")
    # THE DISCRIMINATOR (§2). os.path.exists swallows OSError and answers False,
    # so even a stat we are not allowed to make reads as "not opted in" — the
    # safe direction, since absence is never an error.
    if not os.path.exists(path):
        if explicit:
            print(f"FAIL: --config {path}: no such file", file=sys.stderr)
            sys.exit(2)
        return None, None  # OFF. Say nothing, do nothing, exit 0.
    try:
        with open(path, encoding="utf-8") as handle:
            config = json.load(handle)
    except OSError as e:
        print(
            f"FAIL: {path} is present but unreadable: {e}\n"
            f"      Present-but-broken is not off (contract §2). Fix the file's "
            f"permissions, or delete it to turn the pipeline off.",
            file=sys.stderr,
        )
        sys.exit(1)
    except ValueError as e:
        print(
            f"FAIL: {path} is present but is not valid JSON: {e}\n"
            f"      Present-but-broken is not off (contract §2). Fix the syntax, "
            f"or delete the file to turn the pipeline off.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not isinstance(config, dict):
        print(
            f"FAIL: {path} must be a JSON object, got {shape(config)} "
            f"(contract §1).",
            file=sys.stderr,
        )
        sys.exit(1)
    return config, path


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
class Report:
    def __init__(self, source):
        self.source = source
        self.errors = []
        self.warnings = []
        self.notes = []
        self.flagged = set()  # instance paths the schema pass already complained about

    def err(self, rule, message):
        self.errors.append({"rule": rule, "message": message})

    def warn(self, rule, message):
        self.warnings.append({"rule": rule, "message": message})

    def note(self, message):
        self.notes.append(message)


# --------------------------------------------------------------------------- #
# Layer 1 — shape, from schemas/delivery.schema.json
# --------------------------------------------------------------------------- #
def check_version(config, r):
    """§7: `version` unrecognized. Returns False to stop the run.

    Runs AHEAD of the schema pass, and stopping is the point. §1 says a reader
    that does not recognize the value must refuse to run, not guess — and
    guessing here is fail-OPEN: a v2 that relocates `linear.labels.ids` or
    `budgets` would leave those rules quietly inapplicable and the config would
    validate clean on rules it never ran. The schema pins `version` too; this
    check exists so the OTHER rules never run against a layout they do not
    describe.
    """
    version = config.get("version", MISSING)
    if version is MISSING:
        r.err(
            "version",
            "version is missing. Contract §1 requires an explicit integer "
            "(1 today); add \"version\": 1. The PreToolUse hook classifies a "
            "version-less config as BROKEN and fails closed, blocking every "
            "tool call until a human repairs the file by hand.",
        )
        return False
    if version != SUPPORTED_VERSION:
        r.err(
            "version",
            f"version {version!r} is unrecognized — this validator implements "
            f"contract version {SUPPORTED_VERSION} and will not guess at a "
            f"different layout (§1). No other rule was evaluated. Fix: set "
            f"\"version\": {SUPPORTED_VERSION}, or upgrade the validator.",
        )
        return False
    return True


def check_shape(config, schema, r):
    """Validate against §1's schema, translated into §7's rule vocabulary.

    The schema carries the vocabulary itself, so adding a field to §1 and the
    schema adds its rule name, its tier and its remediation prose in one place
    rather than three.
    """
    for error in jsm.validate(config, schema):
        path = error["path"]
        if path.split(".", 1)[0] in UNJUDGED_SUBTREES:
            continue
        rule = jsm.annotation(error, "x-rule") or path or "delivery.json"
        tier = (
            jsm.annotation(error, "x-tier-" + error["keyword"])
            or jsm.annotation(error, "x-tier")
            or "error"
        )
        fix = jsm.annotation(error, "x-fix")
        message = f"{path or 'delivery.json'} {error['message']}."
        if fix:
            message += f" {fix}"
        r.flagged.add(path)
        (r.warn if tier == "warning" else r.err)(rule, message)


# --------------------------------------------------------------------------- #
# Layer 2 — semantics a schema structurally cannot express
# --------------------------------------------------------------------------- #
def check_state_ids(config, r):
    """§7: any `linear.stateIds.*` empty or still a token.

    The schema already said "five keys, string values". Whether a string is a
    RESOLVED Linear ID is the part it cannot see.
    """
    state_ids = dig(config, "linear", "stateIds")
    if not isinstance(state_ids, dict):
        return
    for key in STATE_KEYS:
        value = state_ids.get(key, MISSING)
        if value is MISSING or not isinstance(value, str):
            continue  # shape; the schema reported it
        if not value.strip():
            r.err(
                "linear.stateIds",
                f"linear.stateIds.{key} is empty — resolve the real Linear "
                f"workflow-state ID (§1). Run /setup-board.",
            )
        elif TOKEN_RE.search(value):
            r.err(
                "linear.stateIds",
                f"linear.stateIds.{key} is still an unfilled template token "
                f"({value}) — this config was copied from delivery.example.json "
                f"and never filled in (§1).",
            )


def check_labels(config, r):
    """§7: the `labels.required` floor; required keys missing from `ids` or
    resolving to ""; no `track:*` key."""
    labels = dig(config, "linear", "labels")
    if not isinstance(labels, dict):
        return
    ids = labels.get("ids", MISSING)
    required = labels.get("required", MISSING)

    # The floor comes FIRST, and depends only on `required`: the rules below ask
    # whether each listed key resolves, which says nothing about what must be
    # listed. Same shape as the `autonomy.riskPaths` floor in check_autonomy.
    if isinstance(required, list):
        for needed in REQUIRED_LABEL_KEYS:
            if needed not in required:
                r.err(
                    "linear.labels.required",
                    f"linear.labels.required is missing '{needed}' (exact "
                    f"canonical key). §6: the dispatcher-owned lifecycle labels "
                    f"are the parking brake — a person applies "
                    f"`agent:needs-human` to stop a ticket, and only membership "
                    f"here makes a stale ID for it FAIL a read instead of "
                    f"warning. Dropping one silently re-arms dispatch on tickets "
                    f"somebody parked. Extend this list, never shorten it.",
                )

    if isinstance(ids, dict) and isinstance(required, list):
        for key in required:
            if not isinstance(key, str):
                continue  # shape; the schema reported it
            if key not in ids:
                r.err(
                    "linear.labels.required",
                    f"required label '{key}' has no entry in linear.labels.ids — "
                    f"a guard cannot resolve it to an ID, and guards compare IDs, "
                    f"never display names (§1, §6).",
                )
            elif unresolved(ids[key]):
                r.err(
                    "linear.labels.required",
                    f"required label '{key}' resolves to {ids[key]!r} — label IDs "
                    f"ship unresolved and a setup step fills them by looking each "
                    f"display name up once (§1 corollary). Run /setup-board.",
                )

    if not isinstance(ids, dict):
        return
    if not any(isinstance(k, str) and k.startswith("track:") for k in ids):
        r.err(
            "linear.labels.track",
            "no `track:*` key in linear.labels.ids — §6 requires at least one "
            "workstream track (e.g. `track:platform`) for routing.",
        )

    stale = sorted(
        k
        for k, v in ids.items()
        if isinstance(k, str)
        and unresolved(v)
        and (not isinstance(required, list) or k not in required)
    )
    if stale:
        r.warn(
            "linear.labels.ids",
            f"{len(stale)} non-required label key(s) still unresolved "
            f"({', '.join(stale[:5])}{'…' if len(stale) > 5 else ''}). Not a §7 "
            f"failure, but any guard reaching for one gets nothing (§1).",
        )


def check_branch(config, r, live_types, live_source):
    """§7: `branch.types` containing a type the LIVE branch guard rejects.

    The schema pins the DOCUMENTED five. This catches the other direction — a
    guard that has been narrowed below what §1 lists, which the schema alone
    would happily accept.
    """
    types = dig(config, "branch", "types")
    if not isinstance(types, list):
        return
    bad = [t for t in types
           if isinstance(t, str) and t in FALLBACK_BRANCH_TYPES and t not in live_types]
    if bad:
        r.err(
            "branch.types",
            f"branch.types contains {bad}, which the live branch-naming "
            f"guard rejects ({live_source} accepts only {live_types}). A "
            f"session on such a branch is blocked before its first edit "
            f"(§1, §7).",
        )


def check_commands(config, r):
    """The advisory half of §1's commands rules — an all-null local gate."""
    commands = config.get("commands", MISSING)
    if not isinstance(commands, dict):
        return
    if all(commands.get(k, MISSING) in (MISSING, None) for k in COMMAND_KEYS):
        r.warn(
            "commands",
            "every commands.* is null — the local gate before /ship runs nothing, "
            "so CI is the first thing that ever checks a session's work (§1).",
        )


def check_budgets(config, r):
    """§7: `perEffort[e].maxTurns > budgets.maxTurns` — a cross-field rule.

    "Effective turns = min(perEffort[e].maxTurns, maxTurns)" compares two
    fields, which no JSON Schema keyword reaches.
    """
    budgets = config.get("budgets", MISSING)
    if not isinstance(budgets, dict):
        return

    max_turns = budgets.get("maxTurns", MISSING)
    if is_int(max_turns) and max_turns >= 1:
        pass
    elif "budgets.maxTurns" in r.flagged:
        max_turns = None  # the schema already named it; do not say it twice
    else:
        # Reachable only for a value the schema accepts and this does not —
        # `150.0`, a number with no fractional part. §1 means a literal.
        r.err(
            "budgets.maxTurns",
            f"budgets.maxTurns is {max_turns!r} — §1 requires the hard turn "
            f"ceiling as a JSON integer literal ≥ 1, not a float. The §7 "
            f"per-effort rule cannot be evaluated without it.",
        )
        max_turns = None

    per_effort = budgets.get("perEffort", MISSING)
    if max_turns is None or not isinstance(per_effort, dict):
        return
    for effort in ("S", "M", "L"):
        band = per_effort.get(effort, MISSING)
        if not isinstance(band, dict):
            continue
        turns = band.get("maxTurns", MISSING)
        if is_int(turns) and turns > max_turns:
            r.err(
                "budgets.perEffort",
                f"budgets.perEffort.{effort}.maxTurns ({turns}) exceeds "
                f"budgets.maxTurns ({max_turns}). Effective turns = "
                f"min(perEffort[e].maxTurns, maxTurns) — a per-effort value "
                f"may only LOWER the cap, never raise it (§1, §7). Lower it "
                f"to ≤ {max_turns}, or raise budgets.maxTurns deliberately.",
            )


def check_autonomy(config, r):
    """§7: the `autonomy.riskPaths` floor, and the auto-merge advisory."""
    autonomy = config.get("autonomy", MISSING)
    if not isinstance(autonomy, dict):
        return

    risk_paths = autonomy.get("riskPaths", MISSING)
    if isinstance(risk_paths, list):
        for needed in REQUIRED_RISK_PATHS:
            if needed not in risk_paths:
                r.err(
                    "autonomy.riskPaths",
                    f"autonomy.riskPaths is missing '{needed}' (exact glob "
                    f"string). §1/§7: the guard machinery and delivery.json "
                    f"always need a human — a PR that edits supervision is not a "
                    f"routine edit.",
                )

    auto_merge = autonomy.get("autoMergeMaxLines", MISSING)
    if is_int(auto_merge) and auto_merge > 0:
        r.warn(
            "autonomy.autoMergeMaxLines",
            f"autonomy.autoMergeMaxLines is {auto_merge} (auto-merge enabled "
            f"under that diff size). §1: no Claude Code session may ever act on "
            f"this — the merge command is hook-blocked in every form including "
            f"--auto, so the merge must be performed by CI or a GitHub App.",
        )


def check_dispatch(config, r, repo_root, roots):
    """§7: pinsRoot/statePath inside the repo or a worktree; statePath vs backend.

    Both are questions about THIS DISK and about another field's value, which is
    exactly the class a schema cannot answer.
    """
    dispatch = config.get("dispatch", MISSING)
    if not isinstance(dispatch, dict):
        return

    backend = dispatch.get("backend", MISSING)

    pins_root = dispatch.get("pinsRoot", MISSING)
    if isinstance(pins_root, str) and pins_root.strip():
        owner = containing_root(expand_path(pins_root, repo_root), roots)
        if owner:
            r.err(
                "dispatch.pinsRoot",
                f"dispatch.pinsRoot ({pins_root}) resolves inside {owner}. §1: "
                f"pins must live outside the repo and every worktree — the pin is "
                f"the only authority binding a session to its ticket, and a pin "
                f"the session can write is not a pin. Use "
                f"~/.claude/pipeline/pins.",
            )

    state_path = dispatch.get("statePath", MISSING)
    if state_path is None:
        if backend not in SELF_STORING_BACKENDS:
            r.err(
                "dispatch.statePath",
                f"dispatch.statePath is null but dispatch.backend is "
                f"{backend!r}, which has no durable store of its own — the "
                f"attempt counter behind budgets.totalAttempts would have "
                f"nowhere to live (§7, §9). Only "
                f"{', '.join(SELF_STORING_BACKENDS)} may leave it null (the "
                f"`pipeline-state` artifact); every other backend must name a "
                f"path outside the repo and every worktree.",
            )
    elif isinstance(state_path, str) and state_path.strip():
        owner = containing_root(expand_path(state_path, repo_root), roots)
        if owner:
            r.err(
                "dispatch.statePath",
                f"dispatch.statePath ({state_path}) resolves inside {owner}. §9: "
                f"the state record is never read from inside a worktree and never "
                f"written by a session — a counter the agent can edit is not a "
                f"counter. Move it outside the repo and every worktree.",
            )
        if backend in SELF_STORING_BACKENDS:
            r.warn(
                "dispatch.statePath",
                f"dispatch.statePath is set but dispatch.backend is {backend!r}, "
                f"whose store is the `pipeline-state` artifact with a name fixed "
                f"by §9 — this value is ignored. Use null.",
            )

    label_trigger = dispatch.get("labelTrigger", MISSING)
    ids = dig(config, "linear", "labels", "ids")
    if isinstance(label_trigger, str) and label_trigger.strip():
        if isinstance(ids, dict) and label_trigger not in ids:
            r.warn(
                "dispatch.labelTrigger",
                f"dispatch.labelTrigger '{label_trigger}' has no entry in "
                f"linear.labels.ids, so it cannot be resolved to an ID and nothing "
                f"will ever queue (§1, §6).",
            )


def check_minor(config, r):
    """Unfilled template tokens, and the one casing rule §1 states in prose."""
    team_key = dig(config, "linear", "teamKey")
    if isinstance(team_key, str) and team_key.strip():
        if TOKEN_RE.search(team_key):
            r.warn(
                "linear.teamKey",
                f"linear.teamKey is still an unfilled template token "
                f"({team_key}) — §1 requires the uppercase ticket prefix "
                f"(e.g. ENG in ENG-123).",
            )
        elif team_key != team_key.upper():
            r.warn(
                "linear.teamKey",
                f"linear.teamKey '{team_key}' is not uppercase; §1 requires "
                f"uppercase. It is lowercased for the branch name — the branch "
                f"guard is [a-z0-9-] only — but the config carries the canonical "
                f"form.",
            )

    github = config.get("github", MISSING)
    if isinstance(github, dict):
        for key in ("owner", "repo", "defaultBranch"):
            value = github.get(key, MISSING)
            if isinstance(value, str) and TOKEN_RE.search(value):
                r.warn("github", f"github.{key} is still an unfilled token ({value}) (§1).")

    kind = dig(config, "stack", "kind")
    if isinstance(kind, str) and TOKEN_RE.search(kind):
        r.warn("stack", f"stack.kind is still an unfilled token ({kind}) (§1).")

    dsn_env = dig(config, "telemetry", "dsnEnv")
    if isinstance(dsn_env, str) and "://" in dsn_env:
        r.warn(
            "telemetry",
            "telemetry.dsnEnv looks like a connection string, not the NAME of "
            "an environment variable holding one. §1: a DSN carries a password "
            "and delivery.json is a tracked file. (Telemetry is otherwise not "
            "judged here — it gates nothing.)",
        )


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
def run(config, source, repo_root, roots, schema, strict=False,
        live_types=None, live_source=None):
    r = Report(source)
    if live_types is None:
        live_types, live_source = FALLBACK_BRANCH_TYPES, "the documented branch guard"

    if check_version(config, r):
        check_shape(config, schema, r)
        check_state_ids(config, r)
        check_labels(config, r)
        check_branch(config, r, live_types, live_source)
        check_commands(config, r)
        check_budgets(config, r)
        check_autonomy(config, r)
        check_dispatch(config, r, repo_root, roots)
        check_minor(config, r)

    ok = not r.errors and (not strict or not r.warnings)
    return {
        "ok": ok,
        "config": source,
        "strict": strict,
        "schema": SCHEMA_FILE,
        "branch_types": live_types,
        "worktree_roots": roots,
        "errors": r.errors,
        "warnings": r.warnings,
        "notes": r.notes,
        "summary": {"errors": len(r.errors), "warnings": len(r.warnings)},
    }


def print_text(result):
    summary = result["summary"]
    mark = "FAIL" if not result["ok"] else ("WARN" if summary["warnings"] else "OK  ")
    print(f"{mark}  {result['config']}")
    for finding in result["errors"]:
        print(f"      error   [{finding['rule']}] {finding['message']}")
    for finding in result["warnings"]:
        print(f"      warning [{finding['rule']}] {finding['message']}")
    for note in result["notes"]:
        print(f"note: {note}")
    print(
        f"{summary['errors']} error(s), {summary['warnings']} warning(s)"
        + ("" if result["ok"] else " — NOT valid (contract §7)")
    )


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
def good_config():
    """A minimal config that passes every rule with no warnings."""
    return {
        "version": 1,
        "linear": {
            "teamKey": "ENG",
            "workspace": "acme",
            "stateIds": {k: f"state-{k}" for k in STATE_KEYS},
            "labels": {
                "ids": {
                    "track:platform": "lbl-track",
                    "effort:S": "lbl-s",
                    "effort:M": "lbl-m",
                    "effort:L": "lbl-l",
                    "agent:queued": "lbl-queued",
                    "agent:working": "lbl-working",
                    "agent:blocked": "lbl-blocked",
                    "agent:needs-human": "lbl-needs-human",
                    "blocked:capacity": "lbl-capacity",
                    "provenance:epic": "lbl-prov-epic",
                    "provenance:human": "lbl-prov-human",
                    "hooks-change": "lbl-hooks",
                },
                # Built FROM the floor, so a widened floor cannot leave the
                # fixture quietly invalid. The floor's own contents are pinned
                # separately in selftest(), because a fixture derived from a
                # constant cannot notice that constant being emptied.
                "required": [
                    "effort:S", "effort:M", "effort:L",
                    *REQUIRED_LABEL_KEYS,
                    "provenance:epic", "provenance:human",
                    "hooks-change",
                ],
            },
        },
        "github": {"owner": "acme", "repo": "app", "defaultBranch": "main"},
        "branch": {"types": ["feat", "fix", "chore"], "requireTicketId": True},
        "stack": {"kind": "node-ts", "securityNotes": [], "graderPaths": []},
        "commands": {
            "lint": "npm run lint",
            "typecheck": "npm run typecheck",
            "test": "npm test",
            "e2e": None,
            "preview": None,
        },
        "budgets": {
            "perEffort": {
                "S": {"maxTurns": 25, "maxUsd": 2.0, "maxMinutes": 20},
                "M": {"maxTurns": 60, "maxUsd": 6.0, "maxMinutes": 45},
                "L": {"maxTurns": 120, "maxUsd": 15.0, "maxMinutes": 90},
            },
            "maxTurns": 150,
            "wipLimit": 3,
            "maxBounces": 2,
            "fixIterations": 3,
            "totalAttempts": 3,
            "dailyUsd": 50.0,
            "reviewSeverityThreshold": "medium",
        },
        "auth": {
            "devSessions": "subscription",
            "scheduled": "api-key",
            "review": "api-key",
        },
        "autonomy": {
            "autoApproveProvenance": ["epic"],
            "autoMergeMaxLines": 0,
            "riskPaths": list(REQUIRED_RISK_PATHS) + ["**/*.pem"],
        },
        "dispatch": {
            "backend": "github-actions",
            "labelTrigger": "agent:queued",
            "pauseOnCapacity": True,
            "pinsRoot": "~/.claude/pipeline/pins",
            "statePath": None,
        },
        "monitoring": {"provider": "github-actions", "stormPerHour": 6},
    }


def _set(config, dotted, value):
    node = config
    keys = dotted.split(".")
    for key in keys[:-1]:
        node = node[key]
    node[keys[-1]] = value


def _drop(config, dotted):
    node = config
    keys = dotted.split(".")
    for key in keys[:-1]:
        node = node[key]
    node.pop(keys[-1], None)


# (name, mutator, expected error rules). The mutator gets a fresh good_config().
CASES = [
    ("good", lambda c: None, []),

    # §7 — version
    ("version-missing", lambda c: _drop(c, "version"), ["version"]),
    ("version-future", lambda c: _set(c, "version", 2), ["version"]),
    ("version-string", lambda c: _set(c, "version", "1"), ["version"]),
    # An unrecognized version must SUPPRESS every other rule, not add to it.
    (
        "version-future-suppresses-the-rest",
        lambda c: (_set(c, "version", 2), _set(c, "auth.review", "oauth")),
        ["version"],
    ),

    # §7 — linear.stateIds
    ("stateid-empty", lambda c: _set(c, "linear.stateIds.ready", ""), ["linear.stateIds"]),
    ("stateid-blank", lambda c: _set(c, "linear.stateIds.done", "   "), ["linear.stateIds"]),
    (
        "stateid-token",
        lambda c: _set(c, "linear.stateIds.raw", "{" + "{LINEAR_STATE_ID_RAW}" + "}"),
        ["linear.stateIds"],
    ),
    ("stateid-missing", lambda c: _drop(c, "linear.stateIds.working"), ["linear.stateIds"]),
    ("stateid-null", lambda c: _set(c, "linear.stateIds.review", None), ["linear.stateIds"]),
    ("stateids-absent", lambda c: _drop(c, "linear.stateIds"), ["linear.stateIds"]),
    # The /setup-board regression, at field granularity: a state map keyed by
    # the DISPLAY NAME rather than the canonical key.
    (
        "stateids-keyed-by-display-name",
        lambda c: _set(c, "linear.stateIds",
                       {"Ideas": "a", "Ready": "b", "In Progress": "c",
                        "In Review": "d", "Done": "e"}),
        ["linear.stateIds"],
    ),

    # §7 — linear.labels: the floor. `required` is what makes a stale ID fatal
    # rather than advisory, so shortening it is a supervision change.
    (
        "labels-required-drops-a-hold",
        lambda c: c["linear"]["labels"]["required"].remove("agent:needs-human"),
        ["linear.labels.required"],
    ),
    (
        "labels-required-drops-capacity",
        lambda c: c["linear"]["labels"]["required"].remove("blocked:capacity"),
        ["linear.labels.required"],
    ),
    # The whole point of the floor: an EMPTY list resolves every key it lists
    # (vacuously) and used to pass clean.
    (
        "labels-required-empty",
        lambda c: _set(c, "linear.labels.required", []),
        ["linear.labels.required"],
    ),
    # A project may require MORE than the floor — extending is not an error, and
    # must stay silent so the floor adds no noise to a config that already meets it.
    (
        "labels-required-superset-is-fine",
        lambda c: (
            _set(c, "linear.labels.ids.provenance:monitor", "lbl-prov-monitor"),
            c["linear"]["labels"]["required"].append("provenance:monitor"),
        ),
        [],
    ),

    # §7 — linear.labels: resolution. Does each key the project DID list point
    # at a real ID?
    (
        "required-not-in-ids",
        lambda c: c["linear"]["labels"]["required"].append("effort:XL"),
        ["linear.labels.required"],
    ),
    (
        "required-unresolved",
        lambda c: _set(c, "linear.labels.ids.effort:M", ""),
        ["linear.labels.required"],
    ),
    (
        "required-token",
        lambda c: _set(c, "linear.labels.ids.hooks-change", "{" + "{LABEL_ID}" + "}"),
        ["linear.labels.required"],
    ),
    (
        "no-track-key",
        lambda c: _drop(c, "linear.labels.ids.track:platform"),
        ["linear.labels.track"],
    ),
    ("labels-absent", lambda c: _drop(c, "linear.labels"), ["linear.labels"]),
    # The other half of the /setup-board regression: label values as objects.
    (
        "label-id-is-an-object",
        lambda c: _set(c, "linear.labels.ids.agent:queued", {"id": "x", "name": "Queued"}),
        ["linear.labels.ids", "linear.labels.required"],
    ),

    # §7 — branch.types
    (
        "branch-type-guard-rejects",
        lambda c: c["branch"]["types"].append("hotfix"),
        ["branch.types"],
    ),
    ("branch-types-empty", lambda c: _set(c, "branch.types", []), ["branch.types"]),
    ("branch-types-all-live", lambda c: _set(c, "branch.types", FALLBACK_BRANCH_TYPES), []),

    # §7 — commands
    ("command-empty-string", lambda c: _set(c, "commands.lint", ""), ["commands"]),
    ("command-blank-string", lambda c: _set(c, "commands.test", "   "), ["commands"]),
    ("command-null-is-fine", lambda c: _set(c, "commands.lint", None), []),

    # §7 — budgets
    (
        "pereffort-above-cap",
        lambda c: _set(c, "budgets.perEffort.L.maxTurns", 200),
        ["budgets.perEffort"],
    ),
    (
        "pereffort-equal-cap-is-fine",
        lambda c: _set(c, "budgets.perEffort.L.maxTurns", 150),
        [],
    ),
    (
        "pereffort-above-cap-two-bands",
        lambda c: (
            _set(c, "budgets.perEffort.M.maxTurns", 999),
            _set(c, "budgets.perEffort.L.maxTurns", 999),
        ),
        ["budgets.perEffort"],
    ),
    ("maxturns-missing", lambda c: _drop(c, "budgets.maxTurns"), ["budgets.maxTurns"]),
    # The schema calls 150.0 an integer (it is, per JSON Schema). §1 means a
    # literal, so the semantic layer still catches it — and says so exactly once.
    ("maxturns-float", lambda c: _set(c, "budgets.maxTurns", 150.0), ["budgets.maxTurns"]),
    (
        "severity-unknown",
        lambda c: _set(c, "budgets.reviewSeverityThreshold", "blocker"),
        ["budgets.reviewSeverityThreshold"],
    ),
    (
        "severity-missing",
        lambda c: _drop(c, "budgets.reviewSeverityThreshold"),
        ["budgets.reviewSeverityThreshold"],
    ),
    (
        "fixiterations-missing",
        lambda c: _drop(c, "budgets.fixIterations"),
        ["budgets.fixIterations"],
    ),
    ("fixiterations-zero", lambda c: _set(c, "budgets.fixIterations", 0), ["budgets.fixIterations"]),
    (
        "fixiterations-bool",
        lambda c: _set(c, "budgets.fixIterations", True),
        ["budgets.fixIterations"],
    ),
    ("fixiterations-one", lambda c: _set(c, "budgets.fixIterations", 1), []),

    # §7 — auth
    ("auth-bad-value", lambda c: _set(c, "auth.review", "oauth"), ["auth"]),
    ("auth-missing-context", lambda c: _drop(c, "auth.scheduled"), ["auth"]),
    ("auth-absent", lambda c: _drop(c, "auth"), ["auth"]),

    # §7 — autonomy
    (
        "autoapprove-superset",
        lambda c: _set(c, "autonomy.autoApproveProvenance", ["epic", "human"]),
        ["autonomy.autoApproveProvenance"],
    ),
    (
        "autoapprove-monitor",
        lambda c: _set(c, "autonomy.autoApproveProvenance", ["monitor"]),
        ["autonomy.autoApproveProvenance"],
    ),
    ("autoapprove-empty-is-a-subset", lambda c: _set(c, "autonomy.autoApproveProvenance", []), []),
    (
        "autoapprove-not-a-list",
        lambda c: _set(c, "autonomy.autoApproveProvenance", "epic"),
        ["autonomy.autoApproveProvenance"],
    ),
    (
        "riskpaths-missing-delivery",
        lambda c: _set(c, "autonomy.riskPaths", [".claude/hooks/**", ".claude/settings*.json"]),
        ["autonomy.riskPaths"],
    ),
    (
        "riskpaths-missing-hooks-and-settings",
        lambda c: _set(c, "autonomy.riskPaths", ["delivery.json"]),
        ["autonomy.riskPaths"],
    ),
    ("riskpaths-absent", lambda c: _drop(c, "autonomy.riskPaths"), ["autonomy.riskPaths"]),
    (
        "automergemethod-unknown",
        lambda c: _set(c, "autonomy.autoMergeMethod", "fast-forward"),
        [],  # advisory: §1 calls it cosmetic
    ),

    # §7 — dispatch.pinsRoot
    ("pinsroot-missing", lambda c: _drop(c, "dispatch.pinsRoot"), ["dispatch.pinsRoot"]),
    ("pinsroot-empty", lambda c: _set(c, "dispatch.pinsRoot", ""), ["dispatch.pinsRoot"]),
    (
        "pinsroot-relative-is-in-repo",
        lambda c: _set(c, "dispatch.pinsRoot", ".claude/pipeline/pins"),
        ["dispatch.pinsRoot"],
    ),
    ("pinsroot-is-repo-root", lambda c: _set(c, "dispatch.pinsRoot", "REPO"), ["dispatch.pinsRoot"]),
    ("pinsroot-in-repo", lambda c: _set(c, "dispatch.pinsRoot", "REPO/pins"), ["dispatch.pinsRoot"]),
    (
        "pinsroot-in-sibling-worktree",
        lambda c: _set(c, "dispatch.pinsRoot", "WORKTREE/pins"),
        ["dispatch.pinsRoot"],
    ),

    # §7 — dispatch.statePath
    (
        "statepath-in-repo",
        lambda c: (
            _set(c, "dispatch.backend", "local-daemon"),
            _set(c, "dispatch.statePath", "REPO/.pipeline-state.json"),
        ),
        ["dispatch.statePath"],
    ),
    (
        "statepath-in-sibling-worktree",
        lambda c: (
            _set(c, "dispatch.backend", "cloud"),
            _set(c, "dispatch.statePath", "WORKTREE/state.json"),
        ),
        ["dispatch.statePath"],
    ),
    (
        "statepath-null-local-daemon",
        lambda c: _set(c, "dispatch.backend", "local-daemon"),
        ["dispatch.statePath"],
    ),
    ("statepath-null-cloud", lambda c: _set(c, "dispatch.backend", "cloud"), ["dispatch.statePath"]),
    (
        "statepath-null-unknown-backend",
        lambda c: _set(c, "dispatch.backend", "jenkins"),
        ["dispatch.statePath"],
    ),
    (
        "statepath-set-outside-repo-local-daemon",
        lambda c: (
            _set(c, "dispatch.backend", "local-daemon"),
            _set(c, "dispatch.statePath", "OUTSIDE/state.json"),
        ),
        [],
    ),
    ("statepath-missing-key", lambda c: _drop(c, "dispatch.statePath"), ["dispatch.statePath"]),
    ("dispatch-absent", lambda c: _drop(c, "dispatch"), ["dispatch"]),

    # Invented top-level fields — the shape `/setup-board` used to emit. Not a
    # §7 MUST-fail, but never silent: §1 defines the sections exactly.
    ("invented-top-level-field", lambda c: _set(c, "organizationId", "0000"), []),

    # §1's optional section, present and malformed. Telemetry gates nothing, so
    # the validator says nothing — its own consumer judges it at point of use.
    (
        "telemetry-malformed-is-not-this-validator's-business",
        lambda c: _set(c, "telemetry", {"store": "mysql", "lookbackDays": 0}),
        [],
    ),
]


def selftest():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    failures = []
    counted = [0]

    def note():
        counted[0] += 1

    schema = load_schema(root)
    if schema is None:
        print("FAIL: check_delivery_config selftest")
        print(f"  - cannot read {SCHEMA_FILE}; the §1 schema is the shape layer")
        return 1

    # PRECONDITION: the label floor, pinned to literals and spelled out by hand
    # on purpose — it must not be derivable from the thing it checks. Every case
    # below reaches the floor THROUGH the constant (good_config() builds
    # `required` from it), so an emptied REQUIRED_LABEL_KEYS would otherwise let
    # the whole battery pass while the guarantee quietly vanished. Checked here,
    # ahead of the fixtures, and fatal on its own: once the constant has drifted
    # nothing downstream is measuring what it claims to.
    floor = {
        "agent:queued",
        "agent:working",
        "agent:blocked",
        "agent:needs-human",
        "blocked:capacity",
    }
    if set(REQUIRED_LABEL_KEYS) != floor:
        print("FAIL: check_delivery_config selftest")
        print(
            f"  - REQUIRED_LABEL_KEYS drifted from §6's dispatcher-owned label "
            f"set: missing {sorted(floor - set(REQUIRED_LABEL_KEYS))}, "
            f"unexpected {sorted(set(REQUIRED_LABEL_KEYS) - floor)}. Shortening "
            f"this floor downgrades the parking brake from 'dispatch refuses' to "
            f"a warning; widening it is a §6/§7 contract amendment and must be "
            f"made in the contract too."
        )
        return 1

    # 0. The guard parser survives reformatting. Both of these have shipped in
    #    this repo — the second landed while this validator was being written and
    #    broke a parser that only understood the first, so both are pinned here.
    with tempfile.TemporaryDirectory() as tmp0:
        hooks = os.path.join(tmp0, ".claude", "hooks")
        os.makedirs(hooks)
        forms = {
            "one-line": (
                'BRANCH_NAME_RE = re.compile(r"^(feat|fix|chore|refactor|docs)/'
                '[a-z0-9][a-z0-9-]*$")\n'
            ),
            "multi-line-with-optional-ticket": (
                "BRANCH_NAME_RE = re.compile(\n"
                '    r"^(?:feat|fix|chore|refactor|docs)/"\n'
                "    # a comment between fragments must not confuse the parser\n"
                '    r"(?:(?P<ticket>[a-z0-9]+-\\d+)-)?"\n'
                '    r"[a-z0-9][a-z0-9-]*$"\n'
                ")\n"
            ),
        }
        for label, source in forms.items():
            note()
            with open(os.path.join(hooks, "pre-tool-use.py"), "w", encoding="utf-8") as fh:
                fh.write("import re\n" + source)
            got = hook_branch_types(tmp0)
            if got != FALLBACK_BRANCH_TYPES:
                failures.append(
                    f"guard parser/{label}: expected {FALLBACK_BRANCH_TYPES}, got {got}"
                )
        # A shape it genuinely cannot read must answer None, never a wrong list.
        note()
        with open(os.path.join(hooks, "pre-tool-use.py"), "w", encoding="utf-8") as fh:
            fh.write("import re\nBRANCH_NAME_RE = re.compile(SOME_CONSTANT)\n")
        if hook_branch_types(tmp0) is not None:
            failures.append("guard parser: an unreadable form must return None")

    # 1. Drift, THREE ways. The live PreToolUse guard's branch types, the
    #    fallback list here, and the schema's own enum must all agree — widen
    #    one and this names the other two, so `branch.types` can never validate
    #    against a list nothing else believes in.
    note()
    live = hook_branch_types(root)
    if live is None:
        failures.append(f"cannot parse BRANCH_NAME_RE out of {HOOK_FILE}")
    elif live != FALLBACK_BRANCH_TYPES:
        failures.append(
            f"branch-type drift: {HOOK_FILE} accepts {live}, fallback says "
            f"{FALLBACK_BRANCH_TYPES}"
        )
    note()
    schema_types = (
        schema["properties"]["branch"]["properties"]["types"]["items"].get("enum")
    )
    if schema_types != FALLBACK_BRANCH_TYPES:
        failures.append(
            f"branch-type drift: {SCHEMA_FILE} enumerates {schema_types}, "
            f"fallback says {FALLBACK_BRANCH_TYPES}"
        )
    live_types = live or FALLBACK_BRANCH_TYPES

    # 2. The schema is expressible by the vendored validator — no keyword that
    #    silently does nothing. (check_schemas.py asserts this for every schema;
    #    repeated here so this file's own dependency is checked where it is used.)
    note()
    for problem in jsm.check_schema(schema):
        failures.append(f"{SCHEMA_FILE}: {problem}")

    with tempfile.TemporaryDirectory() as tmp:
        repo = os.path.join(tmp, "repo")
        worktree = os.path.join(tmp, "wt")
        outside = os.path.join(tmp, "outside")
        for path in (repo, worktree, outside):
            os.makedirs(path)
        roots = [repo, worktree]

        def resolve(value):
            """Fixture paths name roots symbolically so the table stays readable."""
            for token, base in (("REPO", repo), ("WORKTREE", worktree), ("OUTSIDE", outside)):
                if value == token:
                    return base
                if value.startswith(token + "/"):
                    return os.path.join(base, value[len(token) + 1:])
            return value

        for name, mutate, want in CASES:
            note()
            config = good_config()
            mutate(config)
            for key in ("pinsRoot", "statePath"):
                value = config.get("dispatch", {}).get(key) if isinstance(config.get("dispatch"), dict) else None
                if isinstance(value, str):
                    config["dispatch"][key] = resolve(value)
            result = run(config, "delivery.json", repo, roots, schema,
                         live_types=live_types, live_source=HOOK_FILE)
            got = sorted({f["rule"] for f in result["errors"]})
            if got != sorted(want):
                failures.append(f"{name}: expected errors {sorted(want)}, got {got}")
            if not want and result["warnings"]:
                # A few cases are warning-tier ON PURPOSE; they say so by name.
                advisory = {
                    "automergemethod-unknown", "invented-top-level-field",
                }
                if name not in advisory:
                    failures.append(
                        f"{name}: expected a clean config, got warnings "
                        f"{sorted({f['rule'] for f in result['warnings']})}"
                    )

        # 2b. The advisory cases must actually WARN — a tier is only meaningful
        #     if the finding is still made.
        for name, rule in (("automergemethod-unknown", "autonomy.autoMergeMethod"),
                           ("invented-top-level-field", "organizationId")):
            note()
            config = good_config()
            dict(CASES_BY_NAME)[name](config)
            result = run(config, "delivery.json", repo, roots, schema,
                         live_types=live_types, live_source=HOOK_FILE)
            if rule not in {f["rule"] for f in result["warnings"]}:
                failures.append(
                    f"{name}: expected a warning on [{rule}], got "
                    f"{sorted({f['rule'] for f in result['warnings']})}"
                )

        # 3. Warnings alone do not fail; --strict promotes them.
        note()
        config = good_config()
        config["dispatch"]["pinsRoot"] = resolve("OUTSIDE/pins")
        config["autonomy"]["autoMergeMaxLines"] = 500
        loose = run(config, "delivery.json", repo, roots, schema, live_types=live_types)
        strict = run(config, "delivery.json", repo, roots, schema, strict=True,
                     live_types=live_types)
        if not loose["ok"] or loose["summary"]["warnings"] != 1:
            failures.append(
                f"warning tier: expected ok with 1 warning, got ok={loose['ok']} "
                f"warnings={loose['summary']['warnings']}"
            )
        if strict["ok"]:
            failures.append("--strict did not promote a warning to a failure")

    # 4. OFF is silent. The headline §2 semantic: no delivery.json → exit 0 and
    #    NOT ONE BYTE of output on either stream.
    with tempfile.TemporaryDirectory() as tmp:
        note()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            config, source = load_config(None, tmp)
        if (config, source) != (None, None):
            failures.append(f"absent delivery.json: expected OFF, got {source!r}")
        if out.getvalue() or err.getvalue():
            failures.append(
                f"absent delivery.json emitted output: "
                f"stdout={out.getvalue()!r} stderr={err.getvalue()!r}"
            )

        # 5. Present-but-broken fails, and the reason names the file.
        for label, body in (
            ("unparseable", "{not json"),
            ("array", "[]"),
            ("scalar", '"nope"'),
            ("empty", ""),
        ):
            note()
            path = os.path.join(tmp, "delivery.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(body)
            err = io.StringIO()
            code = None
            try:
                with contextlib.redirect_stderr(err):
                    load_config(None, tmp)
            except SystemExit as e:
                code = e.code
            if code != 1:
                failures.append(f"broken/{label}: expected exit 1, got {code!r}")
            if "delivery.json" not in err.getvalue():
                failures.append(f"broken/{label}: failure message does not name the file")
        os.remove(os.path.join(tmp, "delivery.json"))

        # 6. Repo-root discovery works from a subdirectory, so running the check
        #    from anywhere in the tree cannot report a configured project as OFF.
        note()
        nested = os.path.join(tmp, "a", "b", "c")
        os.makedirs(nested)
        open(os.path.join(tmp, ".git"), "w").close()
        if find_repo_root(nested) != os.path.abspath(tmp):
            failures.append(f"find_repo_root({nested}) != {tmp}")
        note()
        if find_repo_root(os.path.join(tmp, "a")) != os.path.abspath(tmp):
            failures.append("find_repo_root did not stop at the .git marker")

    # 7. worktree_roots() answers with the real repo, and always includes it.
    note()
    real_roots, answered = worktree_roots(root)
    if os.path.abspath(root) not in real_roots:
        failures.append(f"worktree_roots({root}) omits the repo root: {real_roots}")
    if not answered:
        failures.append("worktree_roots could not list worktrees in the kit checkout")
    note()
    absent_roots, absent_answered = worktree_roots(
        os.path.join(root, "definitely", "not", "a", "repo")
    )
    if absent_roots == [] or absent_answered:
        failures.append(
            f"worktree_roots off a checkout must degrade to one root and say so, "
            f"got {absent_roots} answered={absent_answered}"
        )

    # 8. The shipped example is the shape this validator expects. Its ONLY
    #    failures may be the two unresolved-placeholder rules — the example is a
    #    template, not a valid config, and CI must keep it that way (§1: the kit
    #    ships delivery.example.json and never a live delivery.json). Any other
    #    rule firing here means the example and the contract have drifted.
    note()
    example = os.path.join(root, "delivery.example.json")
    try:
        with open(example, encoding="utf-8") as handle:
            example_config = json.load(handle)
    except (OSError, ValueError) as e:
        failures.append(f"cannot read delivery.example.json: {e}")
    else:
        # The example must SATISFY the floor, not merely be judged by it. The
        # rule-set assertion below cannot see this: a floor violation reports
        # under `linear.labels.required`, which the example already expects for
        # its unfilled placeholders, so a dropped floor key would hide in plain
        # sight. The example seeds every project's config, so it is asked
        # directly.
        note()
        example_required = dig(example_config, "linear", "labels", "required")
        if not isinstance(example_required, list):
            failures.append(
                "delivery.example.json has no linear.labels.required list"
            )
        else:
            missing = [k for k in REQUIRED_LABEL_KEYS if k not in example_required]
            if missing:
                failures.append(
                    f"delivery.example.json's linear.labels.required omits "
                    f"{missing} — the example seeds every project's config and "
                    f"must meet the floor it will be validated against."
                )

        result = run(
            example_config, example, root, worktree_roots(root)[0], schema,
            live_types=live_types, live_source=HOOK_FILE,
        )
        got = sorted({f["rule"] for f in result["errors"]})
        want = ["linear.labels.required", "linear.stateIds"]
        if got != want:
            failures.append(
                f"delivery.example.json drift: expected exactly {want} "
                f"(unfilled placeholders), got {got}"
            )
        # And it must conform to the SCHEMA outright: every remaining complaint
        # is semantic. A shape failure here would mean the example drifted from
        # §1 itself, which is the class of bug this schema exists to close.
        note()
        for error in jsm.validate(example_config, schema):
            failures.append(
                f"delivery.example.json violates {SCHEMA_FILE}: "
                f"{error['path']} {error['message']}"
            )

    if failures:
        print("FAIL: check_delivery_config selftest")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(f"OK: check_delivery_config selftest ({counted[0]} cases)")
    return 0


CASES_BY_NAME = [(name, mutate) for name, mutate, _ in CASES]


# --------------------------------------------------------------------------- #
def main():
    parser = argparse.ArgumentParser(add_help=True, description=__doc__.splitlines()[0])
    parser.add_argument("--strict", action="store_true", help="promote warnings to errors")
    parser.add_argument("--json", action="store_true", dest="as_json", help="machine-readable output")
    parser.add_argument("--config", help="path to delivery.json (default: <repo-root>/delivery.json)")
    parser.add_argument("--repo-root", help="repo root (default: nearest ancestor with .git)")
    parser.add_argument("--selftest", action="store_true", help="run built-in fixtures and exit")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    repo_root = os.path.abspath(args.repo_root) if args.repo_root else find_repo_root(os.getcwd())

    # STEP 1 of the §2 order, and nothing that can fail runs ahead of it: git,
    # the schema read, the hook parse and every rule below all sit AFTER this
    # returns. Reading the schema first would put an I/O failure in front of the
    # existence test, which is the hostage failure §2 exists to prevent.
    config, source = load_config(args.config, repo_root)
    if config is None:
        return 0  # OFF — not configured, not broken. Silence is the contract.

    # The schema ships with the KIT, not with the project, so it is looked up
    # beside this script rather than under the project's repo root — a project
    # that vendored the scripts without the schemas gets a usage error, not a
    # verdict on its config.
    kit_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    schema = load_schema(kit_root)
    if schema is None:
        print(
            f"FAIL: cannot read {os.path.join(kit_root, SCHEMA_FILE)} — it is the "
            f"machine-readable form of contract §1 and this validator's shape "
            f"layer. Restore it from the kit; do not judge a config without it.",
            file=sys.stderr,
        )
        return 2

    roots, git_answered = worktree_roots(repo_root)
    live_types = hook_branch_types(repo_root)
    live_source = HOOK_FILE if live_types else "the documented branch guard"
    result = run(
        config, source, repo_root, roots, schema,
        strict=args.strict, live_types=live_types or FALLBACK_BRANCH_TYPES,
        live_source=live_source,
    )
    if live_types is None:
        result["notes"].append(
            f"could not read {HOOK_FILE}; branch.types was checked against the "
            f"documented list {FALLBACK_BRANCH_TYPES}"
        )
    if not git_answered:
        result["notes"].append(
            "git could not list worktrees here; pinsRoot/statePath containment "
            "was checked against the repo root only"
        )

    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        print_text(result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
