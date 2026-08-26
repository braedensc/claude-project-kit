#!/usr/bin/env python3
"""Tier 0 — local dispatch. Bind ONE local session to ONE ticket, by hand.

**THIS IS A HUMAN TOOL. AN AGENT MUST NEVER RUN IT FOR ITSELF.**

The pin is the pipeline's one piece of authority a session cannot write
(docs/PIPELINE-CONTRACT.md §3): the dispatcher places it outside every worktree
*before* the session starts, and every guard that matters reads it. A session
that could place its own pin could retarget itself at another ticket, widen its
own scope fence, or hand itself a budget nobody granted — which is the exact
attack the pin architecture exists to prevent. So this script refuses to run
when it detects a Claude Code / agent environment (see `AGENT_ENV_MARKERS`).

That refusal is **tamper-evident, not tamper-proof** — the same posture §3 takes
about `chmod 0444` — because a session's shell runs as the same user and can
scrub an environment variable. The real enforcement, if the project wants one,
belongs in the PreToolUse hook, which is self-protected and which this script
deliberately does not touch.

WHY THIS EXISTS

    Before this, the ONLY thing that wrote a pin was the GitHub Actions
    dispatcher (`templates/workflows/pipeline-dispatch.yml`). In a project that
    had just gained a `delivery.json`, `/work` therefore could not run at all:
    it resolved `pinsRoot`, computed the pin key, found no pin, and failed
    closed — correctly, per §2 (configured + `session_mode: ticket` + missing
    pin = BROKEN). The guard was right; the gap was that a human had no way to
    write the binding it wanted. That inverted the intended order, where a local
    loop proves the ticket → session → PR path cheaply before the whole
    credential and dispatch surface is stood up.

    This script is that missing half, and nothing more. It writes the same pin,
    at the same key, into the same store, with the same write protocol as §3's
    dispatcher steps 2–3. It never starts a session, never touches the tracker's
    state or labels, never creates a branch, and never merges anything.

WHAT IT IS NOT

    Not a dispatcher backend. `dispatch.backend` still names where unattended
    sessions run; a local dispatch is outside that accounting entirely. In
    particular there is NO dispatcher state record (§9) here, so a local
    dispatch consumes no `totalAttempts` slot and no `dailyUsd` reservation, and
    the `budget` block it pins is advisory: nothing meters a human's own
    session. `--attempt` exists so a re-run can say so honestly rather than
    reporting "attempt 1" three times.

READS

    `pinsRoot` and every other value come from the COMMITTED `delivery.json` on
    the default branch, resolved through the same ref list and the same
    fallback as `.claude/hooks/pre-tool-use.py::_read_delivery_config`. Matching
    the reader matters more than being stricter than it: if this script and the
    hook disagreed about which config to believe, they would disagree about
    where the pin lives, and every pin would read as absent.

KEY DERIVATION

    pin_key = sha256(realpath(<session root>))[:16]

    One derivation, three renderings — here, the hook's `_pin_path`, and the
    dispatch workflow's `printf '%s' "$root" | sha256sum | cut -c1-16`.
    `--selftest` asserts all three still agree.

USAGE

    pipeline_dispatch_local.py <TICKET-ID> [options]     # write the pin
    pipeline_dispatch_local.py --release [options]       # remove it (teardown)
    pipeline_dispatch_local.py --show [options]          # what is bound here?
    pipeline_dispatch_local.py --selftest

    Ticket source: the Linear API by default (the key is read from the
    environment variable named by --api-key-env, never passed as a flag), or
    --ticket-file for an offline dispatch. Both go through the Definition-of
    -Ready gate's parser — one description, one parser (§3).

EXIT

    0 = pin written / released / shown
    1 = refused (empty acceptance criteria, a pin already here, a bad config,
        an agent environment) — nothing was written
    2 = usage, I/O, or "the pipeline is not configured for this project"

    §2's silence rule ("absent is off — exit 0, emit nothing") governs guards,
    skills and workflows that run IMPLICITLY; it exists so an un-opted-in
    project is never bricked. This script blocks nothing and is invoked
    explicitly, so exiting 0 without writing the pin its caller asked for would
    just be a lie. It says so and exits 2 instead.
"""
import argparse
import getpass
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jsonschema_mini as jsm  # noqa: E402
from pipeline_labels import resolve_label_keys  # noqa: E402

DELIVERY_FILE = "delivery.json"
DELIVERY_VERSION = 1
PIN_VERSION = 1
PIN_SCHEMA_FILE = os.path.join("schemas", "pin.schema.json")
HOOK_FILE = os.path.join(".claude", "hooks", "pre-tool-use.py")
WORKFLOW_FILE = os.path.join("templates", "workflows", "pipeline-dispatch.yml")
LEDGER = "ledger.jsonl"
DEFAULT_PINS_ROOT = "~/.claude/pipeline/pins"

# Mirrors `_CONFIG_REFS` in .claude/hooks/pre-tool-use.py. Remote-tracking refs
# first — their only honest writer is `git fetch`; the local branches are the
# fallback for a repo with no remote.
CONFIG_REFS = ("origin/main", "origin/master", "main", "master")

# The environment this script refuses to run in. Any one of these present means
# a model is driving, and a session placing its own binding is the whole thing
# §3 forbids. No override flag: an escape hatch documented in --help is not a
# refusal, and the honest bypass (scrubbing the variable) at least shows up in
# the transcript.
AGENT_ENV_MARKERS = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_PROJECT_DIR", "AI_AGENT")

BRANCH_NAME_RE = re.compile(r"^(feat|fix|chore|refactor|docs)/[a-z0-9][a-z0-9-]*$")
TICKET_ID_RE = re.compile(r"^([A-Z][A-Z0-9]*)-([0-9]+)$")
MAX_EXPIRY_MINUTES = 24 * 60

LINEAR_API = "https://api.linear.app/graphql"
ISSUE_QUERY = """
query($team: String!, $number: Float!) {
  issues(filter: { team: { key: { eq: $team } }, number: { eq: $number } }, first: 1) {
    nodes { id identifier number title description url createdAt
            labels { nodes { id name } }
            parent { identifier } }
  }
}"""


def die(msg, code=2):
    print(msg, file=sys.stderr)
    sys.exit(code)


def git(root, *args):
    """stdout, or None when git failed. Never raises on a non-zero exit."""
    try:
        r = subprocess.run(["git", "-C", root, *args],
                           capture_output=True, text=True, timeout=15)
    except Exception:
        return None
    return r.stdout if r.returncode == 0 else None


# --------------------------------------------------------------------------- #
# Session root, config, pin key
# --------------------------------------------------------------------------- #
def resolve_session_root(explicit):
    """The worktree this pin will govern, realpath'd.

    The hook derives its own root independently and compares it to `worktree`,
    so this value is the SUBJECT of the pin, never its trust anchor: naming the
    wrong directory here produces a pin no reader will accept, not a pin that
    binds someone else's session.
    """
    start = os.path.abspath(explicit) if explicit else os.getcwd()
    if not os.path.isdir(start):
        die(f"--session-root {start} is not a directory")
    top = git(start, "rev-parse", "--show-toplevel")
    if not top:
        die(f"{start} is not inside a git worktree — a pin binds a worktree (§3)")
    return os.path.realpath(top.strip())


def read_committed_config(root):
    """(config, source). Values come from the COMMITTED copy on the default
    branch, never the working tree — §1's resolution table, and the same ref
    order the hook uses. `(None, source)` means BROKEN."""
    raw, source = None, None
    for ref in CONFIG_REFS:
        out = git(root, "show", f"{ref}:{DELIVERY_FILE}")
        if out and out.strip():
            raw, source = out, f"{ref}:{DELIVERY_FILE}"
            break
    if raw is None:
        # The adoption PR: no candidate ref carries the file yet, and nothing is
        # dispatching. The hook falls back the same way, and agreeing with the
        # reader beats being stricter than it.
        try:
            with open(os.path.join(root, DELIVERY_FILE), encoding="utf-8") as fh:
                raw, source = fh.read(), f"{DELIVERY_FILE} (working tree — adoption)"
        except OSError:
            return None, DELIVERY_FILE
    try:
        cfg = json.loads(raw)
    except ValueError:
        return None, source
    if not isinstance(cfg, dict) or cfg.get("version") != DELIVERY_VERSION:
        return None, source
    return cfg, source


def pin_key(session_root):
    """sha256(realpath(session root))[:16] — §3's path convention.

    Identical to `_pin_path` in the hook and to the dispatch workflow's
    `printf '%s' "$root" | sha256sum | cut -c1-16`. No trailing newline: the
    `printf '%s'` in the workflow is load-bearing, and so is its absence here.
    """
    return hashlib.sha256(os.path.realpath(session_root).encode("utf-8")).hexdigest()[:16]


def worktree_roots(root):
    for line in (git(root, "worktree", "list", "--porcelain") or "").splitlines():
        if line.startswith("worktree "):
            yield os.path.realpath(line[len("worktree "):].strip())


def resolve_pins_root(cfg, session_root):
    """The pin store, or die. Mirrors the hook's `_pins_root_inside_repo`: a
    pins directory the session can reach is not a pin store (§3; §7 makes it a
    validator hard-fail), and an unresolvable value counts as inside."""
    raw = (cfg.get("dispatch") or {}).get("pinsRoot") or DEFAULT_PINS_ROOT
    if not isinstance(raw, str) or not raw.strip():
        die("delivery.json dispatch.pinsRoot is empty or not a string — refusing "
            "to guess where the binding lives (contract §1, §7)", 1)
    expanded = os.path.expanduser(raw.strip())
    if not os.path.isabs(expanded):
        die(f"delivery.json dispatch.pinsRoot ({raw!r}) is relative. It must "
            f"resolve outside every worktree and outside the repo (§1).", 1)
    resolved = os.path.realpath(expanded)
    for other in [session_root, *worktree_roots(session_root)]:
        try:
            if os.path.commonpath([resolved, other]) == other:
                die(f"delivery.json dispatch.pinsRoot resolves to {resolved}, which is "
                    f"inside {other}. A pin the session can write is not a pin "
                    f"(§3; §7 hard-fail).", 1)
        except ValueError:
            continue  # different drives / no common path — not inside
    return resolved


def refuse_if_agent():
    present = [k for k in AGENT_ENV_MARKERS if os.environ.get(k)]
    if not present:
        return
    die("REFUSED: this looks like an agent session (%s set).\n"
        "\n"
        "Placing a pin is the dispatcher's action, and a session that can place "
        "its own binding can retarget itself at another ticket, widen its own "
        "scope fence, or grant itself a budget nobody approved — the exact "
        "attack docs/PIPELINE-CONTRACT.md §3 exists to prevent. A HUMAN runs "
        "this, at their own terminal.\n"
        "\n"
        "There is no override flag. This check is tamper-evident, not "
        "tamper-proof: it names what it saw so a bypass is visible."
        % ", ".join(present), 1)


# --------------------------------------------------------------------------- #
# Ticket → pin
# --------------------------------------------------------------------------- #
def load_pin_fields():
    """The Definition-of-Ready gate's own parser. ONE description, ONE parser
    (§3): a private second reader here could pin a criteria list no human ever
    reviewed. The dispatch workflow refuses to fall back, and so does this."""
    try:
        from check_ticket_dor import pin_fields
    except ImportError as exc:
        die(f"cannot import scripts/check_ticket_dor.py ({exc}). This shares the "
            f"Definition-of-Ready gate's ticket parser and will not fall back to "
            f"a private one (contract §3).")
    return pin_fields


def slug(text):
    """Byte-for-byte the dispatch workflow's `slug()`."""
    keep = "".join(c.lower() if c.isalnum() else "-" for c in text)
    while "--" in keep:
        keep = keep.replace("--", "-")
    return keep.strip("-")[:40].strip("-") or "work"


def graphql(query, variables, api_key):
    import urllib.error
    import urllib.request
    req = urllib.request.Request(
        LINEAR_API,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        # Linear personal API keys go in Authorization RAW (no "Bearer ").
        headers={"Content-Type": "application/json", "Authorization": api_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            payload = json.load(r)
    except urllib.error.HTTPError as e:
        die(f"Linear API HTTP {e.code}: {e.read()[:400]!r}")
    except Exception as e:
        die(f"Linear API unreachable: {e}")
    if payload.get("errors"):
        die(f"Linear API error: {json.dumps(payload['errors'])[:400]}")
    return payload["data"]


def fetch_ticket(ticket_id, team_key, number, args):
    """One issue node, in the shape the dispatch workflow's query returns."""
    if args.ticket_file:
        try:
            with open(args.ticket_file, encoding="utf-8") as fh:
                node = json.load(fh)
        except (OSError, ValueError) as exc:
            die(f"cannot read --ticket-file {args.ticket_file}: {exc}")
        if not isinstance(node, dict):
            die("--ticket-file must hold ONE issue object, in the shape the "
                "dispatcher's GraphQL query returns (identifier, number, title, "
                "description, url, labels.nodes[], parent).")
        if node.get("identifier") and node["identifier"] != ticket_id:
            die(f"--ticket-file describes {node['identifier']}, not {ticket_id}. "
                f"Refusing to pin a ticket the caller did not name.", 1)
        node.setdefault("identifier", ticket_id)
        node.setdefault("number", number)
        return node

    api_key = os.environ.get(args.api_key_env, "").strip()
    if not api_key:
        die(f"${args.api_key_env} is unset, and no --ticket-file was given. "
            f"Export the key (never pass it as a flag) or dispatch offline "
            f"with --ticket-file.")
    nodes = graphql(ISSUE_QUERY, {"team": team_key, "number": float(number)},
                    api_key)["issues"]["nodes"]
    if not nodes:
        die(f"{ticket_id} not found in team {team_key}.", 1)
    return nodes[0]


def build_pin(cfg, node, ticket_id, args, session_root, now):
    lin, budgets = cfg["linear"], cfg["budgets"]
    label_cfg = lin.get("labels") or {}
    keys, drift = resolve_label_keys(
        (node.get("labels") or {}).get("nodes") or [],
        label_cfg.get("ids") or {},
        label_cfg.get("required") or [])
    # A label that named a canonical key but did not resolve to one used to be
    # discarded, so a `delivery.json` holding the ids of deleted labels degraded
    # this pin instead of stopping it — effort to `M`, provenance to `human`.
    # Tier 0 binds ONE ticket for ONE human, so a required key that will not
    # resolve is a refusal: there is no queue here to contain the damage to.
    for row in drift:
        if row["severity"] != "error":
            print("warning: %s: %s" % (ticket_id, row["message"]), file=sys.stderr)
    fatal = [row["message"] for row in drift if row["severity"] == "error"]
    if fatal:
        die("REFUSED: %s carries a label this delivery.json cannot resolve.\n\n%s\n\n"
            "Each of those keys is in linear.labels.required — §7 says the "
            "pipeline may not dispatch until they resolve. Pinning anyway would "
            "silently default whatever the label carried, which is the failure "
            "this check exists to catch, not report.\n\n"
            "  python3 scripts/check_delivery_config.py    # what else is stale\n"
            "\n"
            "No pin was written."
            % (ticket_id, "\n".join("  - " + m for m in fatal)), 1)

    effort = args.effort or next(
        (k.split(":", 1)[1] for k in keys if k.startswith("effort:")), "M")
    per_effort = budgets["perEffort"]
    if effort not in per_effort:
        die(f"effort {effort!r} has no entry in budgets.perEffort "
            f"({', '.join(sorted(per_effort))}).", 1)
    eb = per_effort[effort]
    # §1: effective turns = min(perEffort, maxTurns) — a per-effort value may
    # LOWER the cap, never raise it.
    max_turns = min(int(eb["maxTurns"]), int(budgets["maxTurns"]))

    provenance = "human"
    if "provenance:epic" in keys and node.get("parent"):
        # §5 rule 4: the FULL value including the epic ID. The label carries only
        # the class, and guards match this, not the label.
        provenance = "epic/%s" % node["parent"]["identifier"]
    elif "provenance:epic" in keys:
        die(f"REFUSED: {ticket_id} carries `provenance:epic` but has no parent, so "
            f"there is no epic ID to record. §5 rule 4 wants the full "
            f"`epic/<EPIC-ID>` value, and the approve tier matches on it — a bare "
            f"class would quietly widen what auto-approves. Link the parent epic "
            f"(the Definition-of-Ready gate checks this too). No pin was written.", 1)
    else:
        for k in keys:
            if k.startswith("provenance:"):
                provenance = k.split(":", 1)[1]
    # None when the ticket carries no `track:*` label — a DoR failure (§7) that
    # the pin RECORDS rather than papers over with an invented default.
    track = next((k for k in keys if k.startswith("track:")), None)

    fields = load_pin_fields()(node.get("description"))
    if not fields["acceptance_criteria"]:
        die(
            f"REFUSED: {ticket_id} has no acceptance criteria, and this script "
            f"will not invent any.\n"
            f"\n"
            f"Contract §3: a missing or empty `## Acceptance criteria` section "
            f"yields an EMPTY list — never an inferred one. The criteria are the "
            f"grader for the run; criteria written by the thing being graded are "
            f"not a definition of done. This is a Definition-of-Ready failure "
            f"(§7) for a person to fix on the ticket.\n"
            f"\n"
            f"  python3 scripts/check_ticket_dor.py <ticket.json>   # what else is missing\n"
            f"\n"
            f"No pin was written.", 1)

    total = int(budgets["totalAttempts"])
    attempt = args.attempt
    if not 1 <= attempt <= total:
        die(f"--attempt {attempt} is outside 1..{total} (budgets.totalAttempts).", 1)

    branch = args.branch or "%s/%s-%s-%s" % (
        args.branch_type, lin["teamKey"].lower(), node["number"], slug(node["title"]))
    if not BRANCH_NAME_RE.match(branch):
        die(f"branch {branch!r} does not match the live branch-naming guard "
            f"(^(feat|fix|chore|refactor|docs)/[a-z0-9][a-z0-9-]*$). The team key "
            f"must be LOWER-CASED (§1). Pass --branch to set one by hand.", 1)
    if (cfg.get("branch") or {}).get("requireTicketId"):
        want = "%s-%s-" % (lin["teamKey"].lower(), node["number"])
        if not branch.split("/", 1)[1].startswith(want):
            die(f"branch.requireTicketId is on, so branch {branch!r} must be "
                f"<type>/{want}<short-kebab-desc>.", 1)

    minutes = args.expires_minutes or int(eb["maxMinutes"]) + 30
    if not 1 <= minutes <= MAX_EXPIRY_MINUTES:
        die(f"--expires-minutes {minutes} is outside 1..{MAX_EXPIRY_MINUTES}. A "
            f"long-lived pin is a binding nobody is watching.", 1)

    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    dispatch_id = "d_local_" + hashlib.sha256(
        ("%s|%s|%s|%s" % (ticket_id, session_root, stamp, attempt)).encode()
    ).hexdigest()[:16]

    return {
        "pin_version": PIN_VERSION,
        "dispatch_id": dispatch_id,
        "session_mode": args.session_mode,
        "worktree": session_root,
        "branch": branch,
        "base_branch": cfg["github"]["defaultBranch"],
        # A local dispatch IS an interactive, human-present session — that is
        # exactly what `auth.devSessions` names (§1).
        "auth_mode": cfg["auth"]["devSessions"],
        "budget": {"maxTurns": max_turns, "maxUsd": float(eb["maxUsd"]),
                   "maxMinutes": int(eb["maxMinutes"]),
                   "attempt": attempt, "of": total},
        "ticket": {
            "id": ticket_id,
            "team_key": lin["teamKey"],
            "url": node.get("url") or "https://linear.app/%s/issue/%s" % (
                lin["workspace"], ticket_id),
            "state_id": lin["stateIds"]["working"],
            "effort": effort,
            "track": track,
            "provenance": provenance,
            "title": node["title"],
            "acceptance_criteria": fields["acceptance_criteria"],
            "out_of_scope": fields["out_of_scope"],
            "snapshot_at": stamp,
        },
        "subject": None,
        "pinned_at": stamp,
        "pinned_by": "local-dispatch:%s@%s" % (getpass.getuser(), socket.gethostname()),
        "expires_at": (now + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def kit_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def validate_pin(pin):
    """Against schemas/pin.schema.json — §12's machine-readable form of §3.

    Shape only, and the schema says so itself; the semantic MUSTs (`worktree`
    is the derived root, `ticket` is present in `ticket` mode) are enforced
    above and by the hook that reads this back.

    The check itself is `check_schemas.document_problems`, which is also what the
    queue's dispatcher calls before it writes a pin. Tier 0 and tier 1 producing
    the same document must be held to it the same way — two hand-rolled copies
    of one check are two things free to drift, which is the whole reason the
    schemas exist.
    """
    try:
        from check_schemas import document_problems
    except ImportError as exc:
        die(f"cannot import scripts/check_schemas.py ({exc}) — it holds the pin "
            f"to contract §3's schema, and this script will not write an "
            f"unvalidated pin.")
    try:
        errors = document_problems(pin, "pin")
    except (OSError, ValueError) as exc:
        die(f"cannot read {PIN_SCHEMA_FILE} ({exc}) — it is the machine-readable "
            f"form of contract §3 and this script will not write an unvalidated pin.")
    if errors:
        die("REFUSED: the pin this run built violates %s:\n  - %s\n"
            "\nNo pin was written. A `ticket.*` complaint usually means the ticket "
            "is missing a field a pin requires (an empty title, no URL); anything "
            "else is a bug here."
            % (PIN_SCHEMA_FILE, "\n  - ".join(errors)), 1)


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #
def write_pin(pins_root, key, pin, force):
    """§3's write protocol, steps 2–3: temp file, fsync, 0444, atomic rename,
    then one ledger row."""
    dest = os.path.join(pins_root, key + ".json")
    if os.path.exists(dest) and not force:
        die(f"REFUSED: a pin already governs this session root.\n"
            f"  {dest}\n"
            f"Something already bound this worktree. Inspect it with --show, "
            f"then --release it (or pass --force to replace it).", 1)
    os.makedirs(pins_root, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".pin-", suffix=".tmp", dir=pins_root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(pin, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o444)
        if os.path.exists(dest):
            os.chmod(dest, 0o644)   # 0444 from a previous write
            os.remove(dest)
        os.rename(tmp, dest)        # atomic — a reader never sees a half-written pin
    except BaseException:
        # Never leave a half-written .pin-*.tmp in the store for a sweeper to
        # trip over. The rename is the only thing that publishes.
        if os.path.exists(tmp):
            os.chmod(tmp, 0o644)
            os.remove(tmp)
        raise
    append_ledger(pins_root, {
        "dispatch_id": pin["dispatch_id"],
        "ticket": (pin.get("ticket") or {}).get("id"),
        "worktree": pin["worktree"],
        "pinned_at": pin["pinned_at"],
        "pinned_by": pin["pinned_by"],
        # NOT a §4 telemetry stage (that enum is epic|dev|review|bounce|triage|
        # diagnosis|retro). This marks who wrote the row, so a local dispatch is
        # never mistaken for the queue's.
        "stage": "local-dispatch",
    })
    return dest


def append_ledger(pins_root, row):
    """Append-only, one row per pin ever written (§3). Best-effort: a ledger
    write that fails must not leave a placed pin looking un-placed."""
    try:
        with open(os.path.join(pins_root, LEDGER), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError as exc:
        print(f"warning: could not append to {LEDGER}: {exc}", file=sys.stderr)


def read_pin(path, session_root):
    """(pin, status) with the hook's own vocabulary: ok | absent | expired |
    malformed | mismatch. Kept in step with `_read_pin` so --show reports what
    the guard will actually conclude."""
    if not os.path.isfile(path):
        return None, "absent"
    try:
        with open(path, encoding="utf-8") as fh:
            pin = json.load(fh)
    except (OSError, ValueError):
        return None, "malformed"
    if not isinstance(pin, dict) or pin.get("pin_version") != PIN_VERSION:
        return None, "malformed"
    wt = pin.get("worktree")
    if not isinstance(wt, str) or os.path.realpath(wt) != session_root:
        return pin, "mismatch"
    try:
        exp = datetime.fromisoformat(str(pin.get("expires_at")).replace("Z", "+00:00"))
    except ValueError:
        return pin, "malformed"
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return pin, ("expired" if exp <= datetime.now(timezone.utc) else "ok")


# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #
def action_show(pins_root, key, path, session_root):
    pin, status = read_pin(path, session_root)
    print(f"session root : {session_root}")
    print(f"pins root    : {pins_root}")
    print(f"pin key      : {key}")
    print(f"pin file     : {path}")
    print(f"status       : {status}")
    if pin:
        t = pin.get("ticket") or {}
        print(f"ticket       : {t.get('id')} — {t.get('title')}")
        print(f"branch       : {pin.get('branch')}  (base {pin.get('base_branch')})")
        print(f"mode         : {pin.get('session_mode')}")
        print(f"expires      : {pin.get('expires_at')}")
        print(f"pinned by    : {pin.get('pinned_by')}")
        print(f"criteria     : {len(t.get('acceptance_criteria') or [])} item(s)")
    if status == "absent":
        print("\nThis session is UNBOUND. `/work` in `ticket` mode will fail closed (§2).")
    return 0


def action_release(pins_root, key, path, session_root):
    """§3 step 5: the dispatcher deletes the pin at session end. Here the human
    is the dispatcher, so this is their half of that step."""
    pin, status = read_pin(path, session_root)
    if status == "absent":
        print(f"nothing to release — no pin at {path}")
        return 0
    if status == "mismatch":
        die(f"REFUSED: {path} governs {(pin or {}).get('worktree')!r}, not this "
            f"session root. Refusing to delete another worktree's binding.", 1)
    try:
        os.chmod(path, 0o644)   # written 0444
        os.remove(path)
    except OSError as exc:
        die(f"could not remove {path}: {exc}")
    append_ledger(pins_root, {
        "dispatch_id": (pin or {}).get("dispatch_id"),
        "ticket": ((pin or {}).get("ticket") or {}).get("id"),
        "worktree": session_root,
        "released_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stage": "local-release",
    })
    print(f"released {path}")
    print("this session is now UNBOUND.")
    return 0


def action_dispatch(cfg, source, pins_root, key, path, session_root, ticket_id, args):
    m = TICKET_ID_RE.match(ticket_id)
    if not m:
        die(f"{ticket_id!r} is not a ticket ID (expected e.g. ENG-123 — uppercase "
            f"team key, then a number).")
    team_key, number = m.group(1), int(m.group(2))
    if team_key != cfg["linear"]["teamKey"]:
        die(f"{ticket_id} is team {team_key}; this project's delivery.json says "
            f"{cfg['linear']['teamKey']}.", 1)

    node = fetch_ticket(ticket_id, team_key, number, args)
    pin = build_pin(cfg, node, ticket_id, args, session_root, datetime.now(timezone.utc))
    validate_pin(pin)

    if args.dry_run:
        print(json.dumps(pin, indent=2))
        print(f"\n--dry-run: nothing written. The pin would go to {path}",
              file=sys.stderr)
        return 0

    dest = write_pin(pins_root, key, pin, args.force)
    t = pin["ticket"]
    print(f"pinned {t['id']} — {t['title']}")
    print(f"  config      : {source}")
    print(f"  session root: {session_root}")
    print(f"  pin key     : {key}")
    print(f"  pin file    : {dest}")
    print(f"  branch      : {pin['branch']}  (base {pin['base_branch']})")
    print(f"  criteria    : {len(t['acceptance_criteria'])} item(s)")
    print(f"  expires     : {pin['expires_at']}")
    current = (git(session_root, "rev-parse", "--abbrev-ref", "HEAD") or "").strip()
    if current != pin["branch"]:
        print(f"\nYou are on `{current}`. Create the pinned branch before working:")
        print(f"  git -C {session_root} checkout -b {pin['branch']}")
    print("\nThen, in a Claude Code session rooted at that worktree:")
    print(f"  /work {t['id']}")
    print("\nWhen the session ends, YOU are the dispatcher — tear the pin down:")
    print(f"  python3 {os.path.relpath(os.path.abspath(__file__))} --release")
    return 0


# --------------------------------------------------------------------------- #
def build_parser():
    p = argparse.ArgumentParser(
        add_help=True,
        description="Tier 0 local dispatch — a HUMAN binds one local session to one ticket.",
        epilog="An agent must never run this for itself: a session that can place "
               "its own pin can retarget its own scope (contract §3).")
    p.add_argument("ticket", nargs="?", help="ticket ID, e.g. ENG-123")
    p.add_argument("--session-root", help="worktree to bind (default: the repo root of cwd)")
    p.add_argument("--session-mode", default="ticket",
                   choices=["ticket", "planning", "diagnosis", "maintenance"])
    p.add_argument("--ticket-file", help="JSON issue object instead of the Linear API")
    p.add_argument("--api-key-env", default="LINEAR_API_KEY",
                   help="NAME of the env var holding the Linear key (never the key itself)")
    p.add_argument("--branch", help="override the derived branch name")
    p.add_argument("--branch-type", default="feat",
                   choices=["feat", "fix", "chore", "refactor", "docs"])
    p.add_argument("--effort", choices=["S", "M", "L"],
                   help="override the effort:* label's budget selection")
    p.add_argument("--attempt", type=int, default=1,
                   help="which attempt this is (local dispatch keeps no state record)")
    p.add_argument("--expires-minutes", type=int,
                   help=f"pin lifetime (default: maxMinutes + 30, max {MAX_EXPIRY_MINUTES})")
    p.add_argument("--force", action="store_true", help="replace an existing pin")
    p.add_argument("--dry-run", action="store_true", help="print the pin, write nothing")
    p.add_argument("--release", action="store_true", help="remove this session's pin")
    p.add_argument("--show", action="store_true", help="report what is bound here")
    p.add_argument("--selftest", action="store_true", help="run built-in fixtures and exit")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.selftest:
        return selftest()

    session_root = resolve_session_root(args.session_root)

    # §2, step 1: the existence test, and nothing that can fail runs ahead of it.
    if not os.path.isfile(os.path.join(session_root, DELIVERY_FILE)):
        die(f"The agentic delivery pipeline is not configured for {session_root} "
            f"(no {DELIVERY_FILE} at the repo root), so there is nothing to bind "
            f"a session to. Absence is OFF, not broken (contract §2) — most "
            f"projects using this kit never run a pipeline.")

    if not (args.show or args.selftest):
        refuse_if_agent()

    cfg, source = read_committed_config(session_root)
    if cfg is None:
        die(f"{DELIVERY_FILE} is present but BROKEN ({source}): unreadable, "
            f"unparseable, or not version {DELIVERY_VERSION}. Presence is a "
            f"promise (contract §2). Fix it before dispatching.", 1)

    pins_root = resolve_pins_root(cfg, session_root)
    key = pin_key(session_root)
    path = os.path.join(pins_root, key + ".json")

    if args.show:
        return action_show(pins_root, key, path, session_root)
    if args.release:
        return action_release(pins_root, key, path, session_root)
    if not args.ticket:
        die("a ticket ID is required (or --release / --show / --selftest). "
            "Try --help.")
    return action_dispatch(cfg, source, pins_root, key, path, session_root,
                           args.ticket.strip().upper(), args)


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
SELFTEST_TICKET = {
    "id": "uuid-1", "identifier": "ENG-123", "number": 123,
    "title": "Refresh tokens before expiry",
    "url": "https://example.invalid/ENG-123",
    "createdAt": "2026-08-24T10:00:00Z",
    "description": (
        "## Context\n\nTokens expire mid-flight.\n\n"
        "## Acceptance criteria\n\n"
        "- [ ] A token within 5 minutes of expiry is refreshed before the request is sent\n"
        "- [ ] `npm test` covers the near-expiry path and passes\n\n"
        "## Out of scope\n\n"
        "- Refresh-token rotation and reuse detection (separate ticket)\n"
    ),
    "labels": {"nodes": [{"id": "e-M", "name": "effort:M"},
                         {"id": "t-plat", "name": "track:platform"},
                         {"id": "p-epic", "name": "provenance:epic"}]},
    "parent": {"identifier": "ENG-100"},
}


def _st_cfg(pins_dir):
    return {
        "version": 1,
        "linear": {
            "teamKey": "ENG", "workspace": "selftest",
            "stateIds": {"raw": "s-raw", "ready": "s-ready", "working": "s-working",
                         "review": "s-review", "done": "s-done"},
            "labels": {"ids": {"effort:M": "e-M", "track:platform": "t-plat",
                               "provenance:epic": "p-epic"},
                       "required": []},
        },
        "github": {"owner": "acme", "repo": "app", "defaultBranch": "main"},
        "branch": {"types": ["feat", "fix", "chore", "refactor", "docs"],
                   "requireTicketId": True},
        "stack": {"kind": "node-ts", "securityNotes": [], "graderPaths": []},
        "commands": {"lint": None, "typecheck": None, "test": None,
                     "e2e": None, "preview": None},
        "budgets": {"perEffort": {"S": {"maxTurns": 25, "maxUsd": 2.0, "maxMinutes": 20},
                                  "M": {"maxTurns": 60, "maxUsd": 6.0, "maxMinutes": 45},
                                  "L": {"maxTurns": 120, "maxUsd": 15.0, "maxMinutes": 90}},
                    "maxTurns": 40, "wipLimit": 3, "maxBounces": 2, "fixIterations": 3,
                    "totalAttempts": 3, "dailyUsd": 50.0,
                    "reviewSeverityThreshold": "medium"},
        "auth": {"devSessions": "subscription", "scheduled": "api-key", "review": "api-key"},
        "autonomy": {"autoApproveProvenance": ["epic"], "autoMergeMaxLines": 0,
                     "riskPaths": [".claude/hooks/**", "delivery.json"]},
        "dispatch": {"backend": "local-daemon", "labelTrigger": "agent:queued",
                     "pauseOnCapacity": True, "pinsRoot": pins_dir, "statePath": None},
        "monitoring": {"provider": "none", "stormPerHour": 6},
    }


def _st_git(root, *a):
    subprocess.run(["git", "-C", root, *a], check=True,
                   capture_output=True, text=True)


def _st_repo(tmp, cfg=None, worktree_cfg=None, ticket=None):
    """A throwaway repo with delivery.json COMMITTED on main, plus a ticket file.

    `worktree_cfg` overwrites the working-tree copy after the commit — the
    adversarial case: values must come from the committed copy, so a config an
    agent edited in its own worktree must not move where the pin lands.
    """
    root = os.path.realpath(tempfile.mkdtemp(prefix="local-dispatch-repo-", dir=tmp))
    pins = os.path.realpath(tempfile.mkdtemp(prefix="local-dispatch-pins-", dir=tmp))
    _st_git(root, "init", "-q", "-b", "main")
    _st_git(root, "config", "user.name", "selftest")
    _st_git(root, "config", "user.email", "selftest@host.invalid")
    with open(os.path.join(root, DELIVERY_FILE), "w", encoding="utf-8") as fh:
        json.dump(cfg or _st_cfg(pins), fh, indent=2)
    _st_git(root, "add", "-A")
    _st_git(root, "commit", "-q", "-m", "seed")
    if worktree_cfg is not None:
        with open(os.path.join(root, DELIVERY_FILE), "w", encoding="utf-8") as fh:
            json.dump(worktree_cfg, fh, indent=2)
    tf = os.path.join(root, "ticket.json")
    with open(tf, "w", encoding="utf-8") as fh:
        json.dump(ticket if ticket is not None else SELFTEST_TICKET, fh)
    return root, pins, tf


def _wf_python_blocks(wf_src):
    """Every `python3 - <<'PY' … PY` heredoc in a workflow, dedented.

    The workflow's embedded Python is real Python; parsing it is how this
    selftest reasons about the dispatcher's behaviour without a GitHub runner.
    """
    import textwrap
    return [textwrap.dedent(body) for _, body in
            re.findall(r"\n( *)python3 - <<'PY'\n(.*?)\n\1PY\n", wf_src, re.S)]


def _wf_label_checks(wf_src):
    """[(ok, message)] — the dispatcher's two label READ sites, structurally.

    Four properties, each one a way the fix could rot:

      1. the step imports `resolve_label_keys` rather than resolving labels its
         own way (a private copy is how the two halves of the pipeline start
         disagreeing about whether a ticket is parked);
      2. and actually calls it — an unused import resolves nothing;
      3. both loops go through it — the `if not resolved:` guard appears twice,
         once per site;
      4. neither guard exits. Containment is the whole point: this runs
         mid-queue and part-way through writing labels, so one drifted ticket
         must cost one slot, not the run.

    A missing or unparseable step is itself a finding — these checks would
    otherwise silently assert nothing.

    `ast`, not grep, so a comment mentioning any of it cannot make this pass.
    """
    import ast
    step = next((b for b in _wf_python_blocks(wf_src) if "ready_nodes" in b), None)
    if step is None:
        return [(False, f"{WORKFLOW_FILE}: no embedded Python step builds "
                        f"`ready_nodes` — the enqueue/selection step moved or "
                        f"was renamed, and these checks now assert nothing.")]
    try:
        tree = ast.parse(step)
    except SyntaxError as exc:
        return [(False, f"{WORKFLOW_FILE}: the select-and-claim step is not "
                        f"valid Python ({exc}).")]

    imported = any(
        isinstance(n, ast.ImportFrom) and n.module == "pipeline_labels"
        and any(a.name == "resolve_label_keys" for a in n.names)
        for n in ast.walk(tree))
    called = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == "resolve_label_keys" for n in ast.walk(tree))
    guards = [n for n in ast.walk(tree)
              if isinstance(n, ast.If) and isinstance(n.test, ast.UnaryOp)
              and isinstance(n.test.op, ast.Not)
              and isinstance(n.test.operand, ast.Name)
              and n.test.operand.id == "resolved"]
    contained = all(
        guard.body and isinstance(guard.body[-1], ast.Continue)
        and not any(isinstance(c, ast.Call) and ast.unparse(c.func).endswith("exit")
                    for c in ast.walk(guard))
        for guard in guards)

    return [
        (called, f"{WORKFLOW_FILE} imports `resolve_label_keys` but never calls "
                 f"it — an unused import resolves nothing."),
        (imported, f"{WORKFLOW_FILE} no longer imports `resolve_label_keys` "
                   f"from scripts/pipeline_labels.py — an unresolvable label id "
                   f"is silently discarded again, and a parked ticket dispatches."),
        (len(guards) == 2, f"{WORKFLOW_FILE} has {len(guards)} `if not resolved:` "
                           f"guard(s), expected 2 — the enqueue loop and the "
                           f"selection loop each need one."),
        (contained, f"{WORKFLOW_FILE} handles label drift by exiting, or without "
                    f"a `continue`. It must contain the failure to the ONE "
                    f"ticket: this runs mid-queue and part-way through writing "
                    f"labels."),
    ]


def _wf_pin_gate_checks(wf_src):
    """[(ok, message)] — the dispatch workflow validates the pin BEFORE writing it.

    Tier 0 (this script) refuses to write a pin that fails §3's schema. Tier 1
    is a workflow, and until it did the same the queue could bind a session to
    a document no hook downstream can parse — the session then fails closed for
    a reason visible nowhere.

    Four properties, and the ORDERING one is the whole point: a check that runs
    after the write is a diagnosis, not a gate. Line numbers out of the parsed
    step are what makes that assertable at all.

    `ast`, not grep, so a comment about validating pins cannot make this pass.
    """
    import ast
    step = next((b for b in _wf_python_blocks(wf_src) if "ready_nodes" in b), None)
    if step is None:
        return [(False, f"{WORKFLOW_FILE}: no embedded Python step builds "
                        f"`ready_nodes` — the select-and-claim step moved or was "
                        f"renamed, and these checks now assert nothing.")]
    try:
        tree = ast.parse(step)
    except SyntaxError as exc:
        return [(False, f"{WORKFLOW_FILE}: the select-and-claim step is not "
                        f"valid Python ({exc}).")]

    imported = any(
        isinstance(n, ast.ImportFrom) and n.module == "check_schemas"
        and any(a.name == "document_problems" for a in n.names)
        for n in ast.walk(tree))
    checks = [n for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
              and n.func.id == "document_problems"]
    # The one line that publishes a pin: json.dump(pin, open(...)).
    writes = [n for n in ast.walk(tree)
              if isinstance(n, ast.Call) and ast.unparse(n.func) == "json.dump"
              and n.args and isinstance(n.args[0], ast.Name) and n.args[0].id == "pin"]
    guards = [n for n in ast.walk(tree)
              if isinstance(n, ast.If) and isinstance(n.test, ast.Name)
              and n.test.id == "pin_problems"]
    contained = bool(guards) and all(
        guard.body and isinstance(guard.body[-1], ast.Continue)
        and not any(isinstance(c, ast.Call) and ast.unparse(c.func).endswith("exit")
                    for c in ast.walk(guard))
        for guard in guards)
    ordered = bool(checks) and bool(writes) and max(
        n.lineno for n in checks) < min(n.lineno for n in writes)

    return [
        (imported, f"{WORKFLOW_FILE} no longer imports `document_problems` from "
                   f"scripts/check_schemas.py — the queue's dispatcher would be "
                   f"holding the pin to a private second idea of §3's shape, or "
                   f"to none at all."),
        (bool(checks), f"{WORKFLOW_FILE} imports `document_problems` but never "
                       f"calls it — an unused import validates nothing."),
        (bool(writes), f"{WORKFLOW_FILE}: no `json.dump(pin, …)` found in the "
                       f"select-and-claim step. The pin write moved, and the "
                       f"ordering check below now asserts nothing."),
        (ordered, f"{WORKFLOW_FILE} validates the pin AFTER writing it (or not in "
                  f"the same step). A check that runs after the write is a "
                  f"diagnosis, not a gate — the malformed pin is already on disk "
                  f"and already binding a session."),
        (contained, f"{WORKFLOW_FILE} handles a malformed pin by exiting, or "
                    f"without a `continue`. It must contain the failure to the ONE "
                    f"ticket: this runs mid-queue and part-way through writing "
                    f"labels."),
    ]


def _st_run(argv, agent_env=False):
    """Run this script as a subprocess. The default env is scrubbed of the agent
    markers, because the selftest simulates the HUMAN terminal this tool is for
    — and because CI (and a Claude Code session running the battery) would
    otherwise hit the refusal on every case."""
    env = dict(os.environ)
    for k in AGENT_ENV_MARKERS:
        env.pop(k, None)
    if agent_env:
        env["CLAUDECODE"] = "1"
    return subprocess.run([sys.executable, os.path.abspath(__file__), *argv],
                          capture_output=True, text=True, env=env, timeout=120)


def selftest():
    root_dir = kit_root()
    failures, counted = [], [0]

    def note():
        counted[0] += 1

    def expect(cond, msg):
        note()
        if not cond:
            failures.append(msg)

    # 0. ONE key derivation, THREE renderings. Widen or reword any of them and
    #    this names the other two, rather than every pin silently reading as
    #    absent because two files disagree about a hash.
    sample = os.path.realpath(tempfile.gettempdir())
    expect(pin_key(sample) == hashlib.sha256(sample.encode("utf-8")).hexdigest()[:16],
           "pin_key is not sha256(realpath)[:16]")
    try:
        with open(os.path.join(root_dir, HOOK_FILE), encoding="utf-8") as fh:
            hook_src = fh.read()
    except OSError as exc:
        hook_src = ""
        failures.append(f"cannot read {HOOK_FILE}: {exc}")
        counted[0] += 1
    expect(re.search(r"hashlib\.sha256\(PROJECT_ROOT\.encode\(\"utf-8\"\)\)"
                     r"\.hexdigest\(\)\[:16\]", hook_src) is not None,
           f"{HOOK_FILE} no longer derives the pin key as "
           f"sha256(PROJECT_ROOT)[:16] — this script would look in the wrong place")
    try:
        with open(os.path.join(root_dir, WORKFLOW_FILE), encoding="utf-8") as fh:
            wf_src = fh.read()
    except OSError as exc:
        wf_src = ""
        failures.append(f"cannot read {WORKFLOW_FILE}: {exc}")
        counted[0] += 1
    expect("sha256sum | cut -c1-16" in wf_src,
           f"{WORKFLOW_FILE} no longer computes the key as sha256sum|cut -c1-16")
    expect("printf '%s' \"$root\" | sha256sum" in wf_src,
           f"{WORKFLOW_FILE} no longer hashes the root WITHOUT a trailing newline "
           f"— printf '%s' is load-bearing and this script matches it")
    expect(re.search(r"os\.path\.realpath\(sys\.argv\[1\]\)", wf_src) is not None,
           f"{WORKFLOW_FILE} no longer realpaths the session root before hashing")
    # The hook's config ref order is the one this script mirrors.
    expect(re.search(r'_CONFIG_REFS = \("origin/main", "origin/master", '
                     r'"main", "master"\)', hook_src) is not None,
           f"{HOOK_FILE}'s _CONFIG_REFS changed — CONFIG_REFS here must match")

    # 0b. The dispatch workflow's two label READ sites. The behaviour itself is
    #     covered by section 10 below, against the SAME `resolve_label_keys`
    #     the workflow imports — so what is left to pin is that the workflow
    #     still imports it (rather than growing a private copy that drifts) and
    #     that it CONTAINS a drifted ticket instead of exiting mid-queue.
    #     Structural, via ast — a text grep would pass on a comment.
    for ok, msg in _wf_label_checks(wf_src):
        expect(ok, msg)

    # 0c. …and the pin it writes is schema-checked BEFORE it is written, by the
    #     same function this script uses. Both tiers produce the one document in
    #     this system that is pure authority; holding them to §3's shape two
    #     different ways is how they start disagreeing about what a pin is.
    for ok, msg in _wf_pin_gate_checks(wf_src):
        expect(ok, msg)

    with tempfile.TemporaryDirectory() as tmp:
        # 1. Round trip: dispatch → a schema-valid pin at the derived key → release.
        root, pins, tf = _st_repo(tmp)
        key = pin_key(root)
        dest = os.path.join(pins, key + ".json")
        r = _st_run(["ENG-123", "--session-root", root, "--ticket-file", tf])
        expect(r.returncode == 0, f"dispatch failed: {r.returncode} {r.stderr[-400:]}")
        expect(os.path.isfile(dest), f"no pin at {dest}: {r.stdout[-300:]}")
        pin = {}
        if os.path.isfile(dest):
            with open(dest, encoding="utf-8") as fh:
                pin = json.load(fh)
            expect(oct(os.stat(dest).st_mode & 0o777) == "0o444",
                   "the pin is not mode 0444 (§3 write protocol)")
        with open(os.path.join(root_dir, PIN_SCHEMA_FILE), encoding="utf-8") as fh:
            pin_schema = json.load(fh)
        expect(not list(jsm.validate(pin, pin_schema)),
               f"the written pin violates {PIN_SCHEMA_FILE}: "
               f"{[e['message'] for e in jsm.validate(pin, pin_schema)][:3]}")
        expect(pin.get("worktree") == root,
               "pin.worktree is not the derived session root — every reader "
               "compares it and would call this a mismatch")
        expect(pin.get("branch") == "feat/eng-123-refresh-tokens-before-expiry",
               f"branch derivation drifted: {pin.get('branch')!r}")
        expect(BRANCH_NAME_RE.match(pin.get("branch") or "") is not None,
               "the derived branch does not match the live branch-naming guard")
        expect(pin.get("session_mode") == "ticket", "session_mode is not ticket")
        expect(pin.get("auth_mode") == "subscription",
               "auth_mode must come from auth.devSessions — a local dispatch is "
               "an interactive, human-present session")
        expect(pin.get("budget", {}).get("maxTurns") == 40,
               "perEffort.maxTurns must be clamped by budgets.maxTurns (§1)")
        expect((pin.get("ticket") or {}).get("acceptance_criteria") == [
            "A token within 5 minutes of expiry is refreshed before the request is sent",
            "`npm test` covers the near-expiry path and passes"],
            f"criteria parse drifted: {(pin.get('ticket') or {}).get('acceptance_criteria')}")
        expect((pin.get("ticket") or {}).get("out_of_scope") == [
            "Refresh-token rotation and reuse detection (separate ticket)"],
            "out_of_scope parse drifted")
        expect((pin.get("ticket") or {}).get("provenance") == "epic/ENG-100",
               "provenance must carry the epic ID, not just the class (§5 rule 4)")
        expect((pin.get("ticket") or {}).get("state_id") == "s-working",
               "ticket.state_id must be the resolved `working` state ID")
        ledger = os.path.join(pins, LEDGER)
        rows = [json.loads(x) for x in open(ledger, encoding="utf-8")] \
            if os.path.isfile(ledger) else []
        expect(len(rows) == 1 and rows[0].get("stage") == "local-dispatch",
               f"expected one local-dispatch ledger row, got {rows}")
        expect(rows and rows[0].get("dispatch_id") == pin.get("dispatch_id"),
               "the ledger row does not join to the pin's dispatch_id")

        # 2. A second dispatch onto a bound root is refused, and --force replaces.
        r = _st_run(["ENG-123", "--session-root", root, "--ticket-file", tf])
        expect(r.returncode == 1 and "already governs" in r.stderr,
               f"a second dispatch must be refused: {r.returncode} {r.stderr[-200:]}")
        r = _st_run(["ENG-123", "--session-root", root, "--ticket-file", tf, "--force"])
        expect(r.returncode == 0 and os.path.isfile(dest),
               f"--force must replace the pin: {r.stderr[-200:]}")

        # 3. --show classifies bound, --release unbinds, --show says so.
        r = _st_run(["--session-root", root, "--show"])
        expect(r.returncode == 0 and "status       : ok" in r.stdout,
               f"--show should report a bound session: {r.stdout[-300:]}")
        r = _st_run(["--session-root", root, "--release"])
        expect(r.returncode == 0 and not os.path.exists(dest),
               f"--release must remove the pin: {r.returncode} {r.stderr[-200:]}")
        rows = [json.loads(x) for x in open(ledger, encoding="utf-8")]
        expect(rows[-1].get("stage") == "local-release",
               f"--release must append a ledger row: {rows[-1]}")
        r = _st_run(["--session-root", root, "--show"])
        expect("status       : absent" in r.stdout and "UNBOUND" in r.stdout,
               f"--show should report an unbound session: {r.stdout[-300:]}")
        r = _st_run(["--session-root", root, "--release"])
        expect(r.returncode == 0 and "nothing to release" in r.stdout,
               "--release must be idempotent")

        # 4. Empty acceptance criteria: refuse, and write NOTHING. §3 —
        #    a missing section yields an empty list, never an inferred one.
        bare = dict(SELFTEST_TICKET, description="## Context\n\nJust prose.\n")
        root2, pins2, tf2 = _st_repo(tmp, ticket=bare)
        r = _st_run(["ENG-123", "--session-root", root2, "--ticket-file", tf2])
        expect(r.returncode == 1 and "will not invent" in r.stderr,
               f"an empty criteria list must refuse: {r.returncode} {r.stderr[-300:]}")
        expect(not os.path.exists(os.path.join(pins2, pin_key(root2) + ".json")),
               "a refused dispatch must leave no pin behind")
        # An empty `## Acceptance criteria` HEADING is the same refusal.
        empty_sec = dict(SELFTEST_TICKET,
                         description="## Acceptance criteria\n\n## Out of scope\n\n- x\n")
        root3, pins3, tf3 = _st_repo(tmp, ticket=empty_sec)
        r = _st_run(["ENG-123", "--session-root", root3, "--ticket-file", tf3])
        expect(r.returncode == 1 and "will not invent" in r.stderr,
               "an empty criteria SECTION must refuse too")

        # 4b. `provenance:epic` with no parent has no epic ID to record, and §5
        #     rule 4 wants the full value. Refuse rather than pin a bare class —
        #     that is what the approve tier matches on.
        orphan = dict(SELFTEST_TICKET, parent=None)
        root3b, pins3b, tf3b = _st_repo(tmp, ticket=orphan)
        r = _st_run(["ENG-123", "--session-root", root3b, "--ticket-file", tf3b])
        expect(r.returncode == 1 and "no parent" in r.stderr,
               f"provenance:epic without a parent must refuse: {r.stderr[-300:]}")
        expect(not os.path.exists(os.path.join(pins3b, pin_key(root3b) + ".json")),
               "the provenance refusal must leave no pin behind")
        # A ticket with no provenance label at all pins `human` — a valid §5 value.
        plainprov = dict(SELFTEST_TICKET,
                         labels={"nodes": [{"id": "e-M", "name": "effort:M"}]})
        root3c, _, tf3c = _st_repo(tmp, ticket=plainprov)
        r = _st_run(["ENG-123", "--session-root", root3c, "--ticket-file", tf3c,
                     "--dry-run"])
        shown = json.loads(r.stdout) if r.returncode == 0 else {}
        expect(shown.get("ticket", {}).get("provenance") == "human",
               f"an unlabelled ticket should pin provenance `human`: {r.stderr[-200:]}")
        expect(shown.get("ticket", {}).get("track") is None,
               "a ticket with no track:* label pins null, never an invented track")

        # 5. Values come from the COMMITTED copy, not the working tree. The
        #    working-tree config points pinsRoot somewhere else entirely; the
        #    pin must land where the COMMITTED one says.
        real_pins = os.path.realpath(tempfile.mkdtemp(prefix="ld-committed-", dir=tmp))
        fake_pins = os.path.realpath(tempfile.mkdtemp(prefix="ld-worktree-", dir=tmp))
        root4, _, tf4 = _st_repo(tmp, cfg=_st_cfg(real_pins),
                                 worktree_cfg=_st_cfg(fake_pins))
        r = _st_run(["ENG-123", "--session-root", root4, "--ticket-file", tf4])
        expect(r.returncode == 0, f"committed-config dispatch failed: {r.stderr[-300:]}")
        expect(os.path.isfile(os.path.join(real_pins, pin_key(root4) + ".json")),
               "the pin must land in the COMMITTED pinsRoot (§1 resolution table)")
        expect(not os.path.exists(os.path.join(fake_pins, pin_key(root4) + ".json")),
               "a worktree-edited delivery.json must NOT move where the pin lands")

        # 6. pinsRoot inside the repo — §7 hard-fail, and the payload a poisoned
        #    config wants most: a pins dir the session can write is a pin it can forge.
        root5, _, tf5 = _st_repo(tmp)
        inside = _st_cfg(os.path.join(root5, ".pipeline", "pins"))
        with open(os.path.join(root5, DELIVERY_FILE), "w", encoding="utf-8") as fh:
            json.dump(inside, fh, indent=2)
        _st_git(root5, "commit", "-q", "-am", "point pinsRoot inside the repo")
        r = _st_run(["ENG-123", "--session-root", root5, "--ticket-file", tf5])
        expect(r.returncode == 1 and "inside" in r.stderr,
               f"a pinsRoot inside the repo must be refused: {r.returncode} {r.stderr[-300:]}")
        rel = _st_cfg("relative/pins")
        root6, _, tf6 = _st_repo(tmp, cfg=rel)
        r = _st_run(["ENG-123", "--session-root", root6, "--ticket-file", tf6])
        expect(r.returncode == 1 and "relative" in r.stderr,
               "a relative pinsRoot must be refused")

        # 7. §2's three states. Absent is OFF (exit 2, nothing written and no
        #    pin store touched); present-but-broken fails closed.
        plain = os.path.realpath(tempfile.mkdtemp(prefix="ld-nopipeline-", dir=tmp))
        _st_git(plain, "init", "-q", "-b", "main")
        r = _st_run(["ENG-123", "--session-root", plain])
        expect(r.returncode == 2 and "not configured" in r.stderr,
               f"no delivery.json must say so and exit 2: {r.returncode} {r.stderr[-200:]}")
        badver = _st_cfg(os.path.join(tmp, "unused"))
        badver["version"] = 99
        root7, _, tf7 = _st_repo(tmp, cfg=badver)
        r = _st_run(["ENG-123", "--session-root", root7, "--ticket-file", tf7])
        expect(r.returncode == 1 and "BROKEN" in r.stderr,
               f"an unrecognized version must refuse, not guess: {r.stderr[-200:]}")

        # 8. The agent refusal. Same arguments that just worked, plus one
        #    environment marker, and nothing is written.
        root8, pins8, tf8 = _st_repo(tmp)
        r = _st_run(["ENG-123", "--session-root", root8, "--ticket-file", tf8],
                    agent_env=True)
        expect(r.returncode == 1 and "REFUSED" in r.stderr and "CLAUDECODE" in r.stderr,
               f"an agent environment must be refused: {r.returncode} {r.stderr[-300:]}")
        expect(not os.path.exists(os.path.join(pins8, pin_key(root8) + ".json")),
               "the agent refusal must leave no pin behind")
        r = _st_run(["--session-root", root8, "--show"], agent_env=True)
        expect(r.returncode == 0,
               "--show is read-only and must stay available in any environment")

        # 9. Guards on the caller's own arguments.
        r = _st_run(["ENG-999", "--session-root", root8, "--ticket-file", tf8])
        expect(r.returncode != 0 and "not ENG-999" in r.stderr,
               f"a ticket file naming another ticket must be refused: {r.stderr[-200:]}")
        r = _st_run(["OPS-1", "--session-root", root8, "--ticket-file", tf8])
        expect(r.returncode == 1 and "team OPS" in r.stderr,
               "a foreign team key must be refused")
        r = _st_run(["ENG-123", "--session-root", root8, "--ticket-file", tf8,
                     "--branch", "feat/ENG-123-token-refresh"])
        expect(r.returncode == 1 and "LOWER-CASED" in r.stderr,
               "an uppercase branch must be refused before the first edit (§1)")
        r = _st_run(["ENG-123", "--session-root", root8, "--ticket-file", tf8,
                     "--branch", "feat/unrelated-slug"])
        expect(r.returncode == 1 and "requireTicketId" in r.stderr,
               "branch.requireTicketId must be enforced on an overridden branch")
        r = _st_run(["ENG-123", "--session-root", root8, "--ticket-file", tf8,
                     "--attempt", "9"])
        expect(r.returncode == 1 and "totalAttempts" in r.stderr,
               "--attempt beyond totalAttempts must be refused")
        r = _st_run(["ENG-123", "--session-root", root8, "--ticket-file", tf8,
                     "--expires-minutes", str(MAX_EXPIRY_MINUTES + 1)])
        expect(r.returncode == 1 and "nobody is watching" in r.stderr,
               "an over-long pin lifetime must be refused")
        r = _st_run(["ENG-123", "--session-root", root8, "--ticket-file", tf8,
                     "--dry-run"])
        expect(r.returncode == 0 and '"pin_version": 1' in r.stdout,
               "--dry-run must print the pin")
        expect(not os.path.exists(os.path.join(pins8, pin_key(root8) + ".json")),
               "--dry-run must write nothing")
        # A short effort still clamps against the hard ceiling, and the expiry
        # tracks the effort's own maxMinutes.
        r = _st_run(["ENG-123", "--session-root", root8, "--ticket-file", tf8,
                     "--effort", "S", "--dry-run"])
        shown = json.loads(r.stdout) if r.returncode == 0 else {}
        expect(shown.get("budget", {}).get("maxTurns") == 25,
               f"--effort S should select perEffort.S: {shown.get('budget')}")
        expect(shown.get("ticket", {}).get("effort") == "S",
               "--effort must be recorded on the pin")

        # 10. Label DRIFT on the read path. A label id that no longer resolves
        #     used to be discarded, so a delivery.json holding the ids of
        #     deleted labels degraded the pin instead of stopping it: effort
        #     silently back to `M`, provenance to `human`. Resolution is still
        #     by id (§1); the NAME is only the diagnostic that says which key
        #     the config has wrong.
        def _labelled(*nodes):
            return dict(SELFTEST_TICKET, labels={"nodes": list(nodes)})

        LIVE_M = {"id": "e-M-recreated", "name": "effort:M"}

        # 10a. Stale id for a REQUIRED key → refuse loudly, write nothing.
        req_cfg = _st_cfg(os.path.realpath(tempfile.mkdtemp(prefix="ld-drift-", dir=tmp)))
        req_cfg["linear"]["labels"]["required"] = ["effort:M"]
        root9, pins9, tf9 = _st_repo(
            tmp, cfg=req_cfg,
            ticket=_labelled(LIVE_M, {"id": "p-epic", "name": "provenance:epic"}))
        r = _st_run(["ENG-123", "--session-root", root9, "--ticket-file", tf9])
        expect(r.returncode == 1 and "STALE" in r.stderr and "effort:M" in r.stderr,
               f"a stale REQUIRED label id must refuse by name: "
               f"{r.returncode} {r.stderr[-400:]}")
        expect("e-M-recreated" in r.stderr and "'e-M'" in r.stderr,
               f"the refusal must show BOTH ids — live and recorded: {r.stderr[-400:]}")
        expect(not os.path.exists(os.path.join(pins9, pin_key(root9) + ".json")),
               "a stale required label must leave no pin behind")

        # 10b. Same drift on a NON-required key → warn, and dispatch proceeds.
        #      `track:*` is open-ended and project-named, so it can never be in
        #      `required`; fatal here would wedge the queue over something the
        #      DoR gate already rejects at intake.
        root10, _, tf10 = _st_repo(
            tmp, ticket=_labelled(
                {"id": "e-M", "name": "effort:M"},
                {"id": "t-plat-recreated", "name": "track:platform"},
                {"id": "p-epic", "name": "provenance:epic"}))
        r = _st_run(["ENG-123", "--session-root", root10, "--ticket-file", tf10,
                     "--dry-run"])
        expect(r.returncode == 0 and "STALE" in r.stderr and "track:platform" in r.stderr,
               f"a stale NON-required label must warn and proceed: "
               f"{r.returncode} {r.stderr[-400:]}")
        shown = json.loads(r.stdout) if r.returncode == 0 else {}
        expect(shown.get("ticket", {}).get("track") is None,
               "the degraded value is still pinned as null — now with a warning "
               "beside it, which is the whole difference")

        # 10c. A label that is none of ours stays none of our business — no
        #      warning, no refusal. This is the case the fail-open was FOR.
        root11, _, tf11 = _st_repo(
            tmp, ticket=_labelled(
                {"id": "e-M", "name": "effort:M"},
                {"id": "t-plat", "name": "track:platform"},
                {"id": "p-epic", "name": "provenance:epic"},
                {"id": "zz-design", "name": "needs-design"}))
        r = _st_run(["ENG-123", "--session-root", root11, "--ticket-file", tf11,
                     "--dry-run"])
        expect(r.returncode == 0 and "needs-design" not in r.stderr,
               f"a non-canonical label must be ignored in silence: {r.stderr[-300:]}")
        shown = json.loads(r.stdout) if r.returncode == 0 else {}
        expect(shown.get("ticket", {}).get("track") == "track:platform",
               "the canonical labels around it must still resolve normally")

        # 10d. A hand-written --ticket-file that omits `labels.nodes[].id`.
        #      Distinct diagnostic: the payload is at fault, not the config.
        nid_cfg = _st_cfg(os.path.realpath(tempfile.mkdtemp(prefix="ld-noid-", dir=tmp)))
        nid_cfg["linear"]["labels"]["required"] = ["effort:M"]
        root12, _, tf12 = _st_repo(
            tmp, cfg=nid_cfg,
            ticket=_labelled({"name": "effort:M"},
                             {"id": "p-epic", "name": "provenance:epic"}))
        r = _st_run(["ENG-123", "--session-root", root12, "--ticket-file", tf12])
        expect(r.returncode == 1 and "NO LABEL ID" in r.stderr
               and "--ticket-file" in r.stderr,
               f"a label with no id must blame the ticket file, not the config: "
               f"{r.returncode} {r.stderr[-400:]}")
        expect("STALE" not in r.stderr,
               "the no-id diagnostic must not masquerade as config drift")

        # 10e. The third kind: recorded as "" with a live label of that name.
        #      The config validator already hard-fails this for a required key,
        #      so reaching the dispatcher means it was bypassed — same ladder,
        #      its own message.
        blank_cfg = _st_cfg(os.path.realpath(tempfile.mkdtemp(prefix="ld-blank-", dir=tmp)))
        blank_cfg["linear"]["labels"]["ids"]["meta"] = ""
        root13, _, tf13 = _st_repo(
            tmp, cfg=blank_cfg,
            ticket=_labelled({"id": "e-M", "name": "effort:M"},
                             {"id": "p-epic", "name": "provenance:epic"},
                             {"id": "m-live", "name": "meta"}))
        r = _st_run(["ENG-123", "--session-root", root13, "--ticket-file", tf13,
                     "--dry-run"])
        expect(r.returncode == 0 and "NEVER RESOLVED" in r.stderr and "meta" in r.stderr,
               f"an unresolved-but-live label must say setup is unfinished: "
               f"{r.returncode} {r.stderr[-400:]}")

        # 10f. The resolver itself, at the seam the workflow shares. Same
        #      function, so these pin the two workflow sites' behaviour too.
        ids = {"effort:M": "e-M", "agent:blocked": "a-blocked", "meta": ""}
        keys, drift = resolve_label_keys(
            [{"id": "e-M", "name": "renamed in the ui"}], ids, ["effort:M"])
        expect(keys == {"effort:M"} and not drift,
               f"a RENAMED label must still resolve by id — that is why §1 "
               f"mandates ids: {keys} {drift}")
        keys, drift = resolve_label_keys(
            [{"id": "a-dead", "name": "agent:blocked"}], ids, ["agent:blocked"])
        expect(keys == set() and [d["kind"] for d in drift] == ["stale"]
               and drift[0]["severity"] == "error",
               f"a stale dispatcher-owned hold must be an ERROR, not a discard: "
               f"{keys} {drift}")
        keys, drift = resolve_label_keys(
            [{"id": "a-dead", "name": "agent:blocked"}], ids, [])
        expect(drift and drift[0]["severity"] == "warning",
               f"severity follows labels.required and nothing else: {drift}")
        keys, drift = resolve_label_keys([{"id": "x", "name": "flaky"}], ids, [])
        expect(keys == set() and not drift,
               f"an unknown label is the project's own business: {drift}")
        keys, drift = resolve_label_keys(
            [{"id": "m-live", "name": "meta"}], ids, [])
        expect([d["kind"] for d in drift] == ["unresolved"],
               f'a live label recorded as "" is `unresolved`, not `stale`: {drift}')
        keys, drift = resolve_label_keys([{"name": "effort:M"}], ids, [])
        expect([d["kind"] for d in drift] == ["no-id"],
               f"a label with no id is `no-id`, its own diagnostic: {drift}")
        # A label RENAMED and then recreated resolves by neither id nor name.
        # Documented blind spot, asserted so nobody assumes otherwise.
        keys, drift = resolve_label_keys(
            [{"id": "e-new", "name": "Effort — Medium"}], ids, ["effort:M"])
        expect(keys == set() and not drift,
               f"rename-plus-recreate is INVISIBLE to the name heuristic — "
               f"closing it needs an api liveness check in /setup-board: {drift}")

    if failures:
        print("FAIL: pipeline_dispatch_local selftest")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"OK: pipeline_dispatch_local selftest ({counted[0]} cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
