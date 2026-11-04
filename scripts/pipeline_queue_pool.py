#!/usr/bin/env python3
"""The queue's pool: which tickets may start without the owner, decided by code (KIT-433, MVP 2
part 2a; docs/adr/2026-11-03-autonomous-workers-within-a-budget-and-a-window.md).

    python3 scripts/pipeline_queue_pool.py --selftest

A PURE FUNCTION. `decide(...)` is given what the tracker and the records said, and returns one
verdict per candidate. It starts nothing and writes nothing: no file is read, no socket opened,
no process started, no environment variable read. Part 2b orders the admitted pieces and
applies the lanes and the budget; a later part wires both into the bridge's hand-over.

WHO MAY START.  The owner's own work, told by ACTOR in the ticket's history (KIT-161), never by
state alone, and never the dispatcher's user. Four sources:
  todo         a ticket the owner moved into the team's ready state: the newest move, and the
               move's actor. A history with no state change at all counts the creation as the
               move, but only for a ticket a person filed (`provenance:human`): every job files
               with the owner's key too (KIT-313), so a job's creation proves nothing.
  backlog      when the config turns the backlog on: a ticket the owner filed, not by a job. It
               is `provenance:human`, has no parent, and is not in the finding record.
  plan         a piece of a plan the owner approved, as it stood at the approval: in Slack (the
               bridge's record holds each piece's fingerprint and who approved) or by moving the
               plan's epic into the ready state. A piece added or changed since is held.
  agent_filed  a finding a session requested (contract §8): admitted by itself only when it is
               small (effort S), names no guarded path in its Pointers, came from a source the
               finding record does not itself list as agent-filed, and the day's count of such
               starts is under the cap. The stricter setting `agent_filed: off` turns it off.
The readiness gate (scripts/check_ticket_dor.py, strict) gates everything. A ticket anyone but
the owner changed after it was filed or approved (title, text, label, parent or relation) is
held until the owner touches it again: an edit or a move of the owner's own, later.

NEVER: a plan ticket itself, a review ticket (the reviews team), a planning ticket, a ticket
marked `agent:blocked`, `blocked:*`, `agent:needs-human` or `hooks-change`, one delegated or
running, one finished, one on a team the config does not list. These have no reply.

WHAT IT IS GIVEN (all of it data; nothing is fetched).
  cfg        see `config()`; owner and dispatcher user ids are required, never guessed.
  now        seconds since 1970. Only its local date is read, in the machine's own time zone as
             the usage job names a day: a ledger of another day is not today's, and is said so.
  tickets    tracker issues as the bridge and the planner read them. Each must carry every
             field in TICKET_KEYS, even as null (`title description createdAt state{id name
             type} team{key} labels{nodes{name}} children delegate{id} parent{id identifier}
             project{id} creator{id}`), and a piece of a plan its `inverseRelations`: a field
             nobody asked for would let a rule pass what it should hold, so the ticket is held
             as `malformed`. The bridge's `Q_EPIC` reads neither `createdAt` nor `creator`.
  epics      plans as the bridge reads one (`Q_EPIC`): the epic, with its children in full.
  histories  ticket id -> the ticket's whole IssueHistory, or None when it could not be read
             whole. Every entry must carry every key in HISTORY_KEYS, even as null: an entry
             without them cannot say who edited what, so the ticket is held, never guessed.
  busy       ids of tickets that ever had an agent session.
  findings   the finding poller's seen-set map, or None. Names each filed finding and its source.
  plans      the bridge's record of plans approved in Slack (`state["epics"]`), or None.
  ledger     today's counts, `{"date", "starts", "agent_filed_starts"}`, or None.
  A record that is None cannot be read, and what depends on it is held and said.

WHAT IT RETURNS.  A list of verdicts, fields only: `id`, `ticket`, `team`, `plan` (the epic's
id for a piece), `source` (SOURCES), `admitted`, `reason` (REASONS, a closed list), `by` and
`at` (who approved and when, when admitted; `by` is None for agent_filed), `via` (VIAS) and
`reply`: the exact words that approve a held ticket when one tap would (`start ENG-12`,
`approve ENG-10`), else None. A title, text or comment never reaches a verdict. With
`plans: false` a plan's pieces are ordinary tickets, and the owner's own move of one still
starts it; so it does with a plan that is paused or not approved.

WHERE IT CANNOT HELP.  Jobs that write with the owner's key look like the owner. The record
and label exclusions above cover the findings and plans; the list of jobs that file or move
tickets is pinned by a test in a later part. The gate's rule that a Pointers path exists on
disk is left out, because this module reads no disk; the rule that Pointers names a path stays.
A finding today carries only `provenance:agent` (the finding poller's `creation_input`), so the
gate holds it as `not-ready` until it also carries `effort:*` and `track:*`.

LIVE-TEST ITEMS (coded defensively; verify on the first real run and amend here).
  - The history's edit fields (`updatedDescription`, `fromTitle`, `toTitle`, `addedLabelIds`,
    `removedLabelIds`, `fromParentId`, `toParentId`, `relationChanges`) are read as the
    @linear/sdk typings name them. A live answer without them holds every ticket, never opens it.
  - A `start` reply must leave an owner-attributed entry in the history (a move, or an edit):
    that is what ends a hold. An owner's move into any state, or an edit by the owner, counts.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    import check_grader_paths as graders
    import check_ticket_dor as dor
    import pipeline_machine_tickets as machine
except ImportError as exc:  # a packaging accident, never a state to run in
    sys.exit("FAIL: %s. The pool uses those files' own definitions and keeps no copy." % exc)

EXIT_USAGE = 2
SOURCES = ("todo", "backlog", "plan", "agent_filed", "none")
VIAS = ("move", "creation", "slack", "limits")
REASONS = (
    "admitted",
    # never, whatever anyone approved or the owner taps
    "malformed", "review-ticket", "other-team", "plan-ticket", "planning-ticket",
    "marked-blocked", "needs-person", "hooks-change", "delegated", "running", "finished",
    "wrong-state", "not-ready", "plan-invalid", "plan-paused", "record-unreadable",
    "history-unreadable", "count-unknown",
    # held until the owner looks: a reply says how, where one tap would
    "backlog-off", "agent-off", "actor-unknown", "not-owner", "changed", "not-filed-by-owner",
    "plan-not-approved", "added-after-approval", "not-in-record", "not-small", "guarded-path",
    "generation", "daily-cap")
START, APPROVE = "start %s", "approve %s"
GATE_EPIC_LABEL, GATE_CHILD_LABEL = "provenance:agent", "provenance:epic"   # the idea gate's marks
HUMAN_LABEL = "provenance:human"
MODE_LABELS = ("orchestrator", "graphite")      # the bridge's: the agent would hand out pieces
FINISHED = ("completed", "canceled")
EDIT_FIELDS = ("fromTitle", "toTitle", "updatedDescription", "addedLabelIds", "removedLabelIds",
               "fromParentId", "toParentId", "relationChanges")
HISTORY_KEYS = ("createdAt", "actorId", "fromStateId", "toStateId") + EDIT_FIELDS
# Fields every ticket (and every epic) must be read with, even as null: a rule that reads a field
# nobody asked for would pass whatever it should have held.
TICKET_KEYS = ("title", "description", "createdAt", "state", "team", "labels", "children",
               "delegate", "parent", "project", "creator")
EPIC_KEYS = ("state", "team", "labels", "children", "delegate", "parent")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}\Z")
TICKET_RE = re.compile(r"^[A-Z][A-Z0-9]{0,15}-[0-9]{1,9}\Z")
TEAM_RE = re.compile(r"^[A-Z][A-Z0-9]{0,15}\Z")
SLACK_RE = re.compile(r"^[UW][A-Z0-9]{8,}\Z")
TIME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?Z\Z")

DEFAULTS = {
    "owner_user_ids": None,          # required: the owner's tracker user ids (normally one)
    "dispatcher_user_ids": None,     # required: the dispatcher's own users, never the owner
    "owner_slack_user_ids": [],      # the Slack members whose `approve` counts; none: none does
    "team_keys": None,               # required: the work teams, and no other
    "reviews_team_key": "",          # the reviews team, whose tickets are never work
    "ready_states": ["Todo"],        # states, by name, that mean "approved to start"
    "backlog_states": ["Backlog"],   # states, by name, a backlog ticket sits in
    "backlog": False,                # the owner's own backlog may start (off until turned on)
    "plans": True,                   # pieces of an approved plan may start
    "agent_filed": "off",            # "limits": small agent-filed tickets may start; "off"
    "agent_filed_per_day": 5,        # the day's cap on such starts, a runaway's only
    "agent_filed_efforts": ["S"],    # which effort labels count as small
    "guarded_globs_extra": [],       # added to the grader paths (check_grader_paths.FLOOR)
}


# --------------------------------------------------------------------------- #
# Small pure helpers
# --------------------------------------------------------------------------- #
def epoch(text):
    """A tracker time (UTC, ending in Z) as seconds since 1970, or None."""
    m = TIME_RE.match(text) if isinstance(text, str) else None
    try:
        return datetime.datetime(*(int(g) for g in m.groups()),
                                 tzinfo=datetime.timezone.utc).timestamp() if m else None
    except (ValueError, OverflowError):
        return None


def iso(seconds):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


def local_date(now):
    """The machine's local date, as the usage job and the ledger name a day."""
    return time.strftime("%Y-%m-%d", time.localtime(now))


def dig(obj, *path):
    for key in path:
        obj = obj.get(key) if isinstance(obj, dict) else None
    return obj


def label_names(t):
    nodes = dig(t, "labels", "nodes")
    return set(n["name"] for n in nodes if isinstance(n, dict) and isinstance(n.get("name"), str)
               ) if isinstance(nodes, list) else set()


def has_children(t):
    return bool(dig(t, "children", "nodes") or dig(t, "children", "pageInfo", "hasNextPage"))


def children_of(epic):
    nodes = dig(epic, "children", "nodes")
    return [k for k in nodes if isinstance(k, dict)] if isinstance(nodes, list) else []


def ticket_number(identifier):
    try:
        return int((identifier or "").rsplit("-", 1)[1])
    except (IndexError, ValueError, AttributeError):
        return 0


def blockers_of(child):
    """The tickets that BLOCK this one: the child's inverse `blocks` relations, as the idea gate
    links them and the bridge reads them."""
    return [dig(r, "issue") for r in dig(child, "inverseRelations", "nodes") or []
            if dig(r, "type") == "blocks" and isinstance(dig(r, "issue", "id"), str)]


def fingerprint(child):
    """What the owner approved of a plan's piece: its title, text, labels, project and what it
    waits on. The same function, byte for byte, as pipeline_bridge_plans.fingerprint, which
    wrote the approval record (the selftest pins the two equal)."""
    text = json.dumps([child.get("title") or "", child.get("description") or "",
                       sorted(label_names(child)), dig(child, "project", "id") or "",
                       sorted(b.get("id") or "" for b in blockers_of(child))])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# The config
# --------------------------------------------------------------------------- #
def config(raw):
    """(cfg, problems): the settings with defaults filled in, and everything wrong with them,
    named by key. Idempotent. A pool that cannot name its owner, its dispatcher and its teams
    decides nothing: those three have no default."""
    if not isinstance(raw, dict):
        return None, ["the config is not an object"]
    cfg = dict((k, raw.get(k, d)) for k, d in DEFAULTS.items())
    bad = ["%s: not a setting" % repr(k)[:40] for k in sorted(raw, key=repr) if k not in DEFAULTS]

    def strs(key, rx=None, need=False):
        v = cfg[key]
        if not (isinstance(v, list) and all(isinstance(x, str) and x.strip() and len(x) <= 80
                                            and (rx is None or rx.match(x)) for x in v)
                and (v or not need)):
            bad.append("%s: needs a list of %s" % (key, "valid values" if rx else "names"))
    strs("owner_user_ids", ID_RE, True)
    strs("dispatcher_user_ids", ID_RE, True)
    strs("owner_slack_user_ids", SLACK_RE)
    strs("team_keys", TEAM_RE, True)
    strs("ready_states", None, True)
    strs("backlog_states", None, True)
    strs("guarded_globs_extra")
    strs("agent_filed_efforts", re.compile(r"^[SML]\Z"), True)
    for key in ("backlog", "plans"):
        if not isinstance(cfg[key], bool):
            bad.append("%s: must be true or false" % key)
    if cfg["agent_filed"] not in ("off", "limits"):
        bad.append("agent_filed: must be \"off\" or \"limits\"")
    n = cfg["agent_filed_per_day"]
    if not isinstance(n, int) or isinstance(n, bool) or n < 0:
        bad.append("agent_filed_per_day: must be a whole number, 0 or more")
    if not (cfg["reviews_team_key"] == "" or (isinstance(cfg["reviews_team_key"], str)
                                              and TEAM_RE.match(cfg["reviews_team_key"]))):
        bad.append("reviews_team_key: must be a team key or empty")
    if not bad:
        if set(cfg["owner_user_ids"]) & set(cfg["dispatcher_user_ids"]):
            bad.append("owner_user_ids: lists the dispatcher's own user, which never approves")
        if cfg["reviews_team_key"] in cfg["team_keys"]:
            bad.append("team_keys: lists the reviews team, whose tickets are never work")
        if set(cfg["ready_states"]) & set(cfg["backlog_states"]):
            bad.append("ready_states: shares a state with backlog_states")
    return cfg, bad


# --------------------------------------------------------------------------- #
# History: who did what, and when
# --------------------------------------------------------------------------- #
def read_history(raw):
    """[(seconds, entry)] oldest first, or None when the history cannot be read whole: not a
    list, an entry without every field this pool reads, or a time it cannot place."""
    if not isinstance(raw, list):
        return None
    out = []
    for e in raw:
        at = epoch(e.get("createdAt")) if isinstance(e, dict) else None
        if at is None or any(k not in e for k in HISTORY_KEYS) or not (
                e["actorId"] is None or isinstance(e["actorId"], str)):
            return None
        out.append((at, e))
    return sorted(out, key=lambda p: p[0])


def is_edit(e):
    return any(e.get(f) for f in EDIT_FIELDS)


def is_owner(cfg, actor):
    """The owner, by tracker user id. Never the dispatcher's own user, never an unknown actor."""
    return (isinstance(actor, str) and actor in cfg["owner_user_ids"]
            and actor not in cfg["dispatcher_user_ids"])


def newest_move(hist, state_id, creator, created):
    """(actor, seconds, how) for what put a ticket in its state: the newest recorded state
    change, if it was into this state. With no state change recorded at all, the creation by
    its creator stands in for the move, because the tracker never records a change a creator
    makes in a ticket's first minutes (KIT-164). None when the history cannot say."""
    moves = [(at, e) for at, e in hist if e["toStateId"] or e["fromStateId"]]
    if not moves:
        return (creator, created, "creation") if creator and created is not None else None
    at, e = moves[-1]
    return (e["actorId"], at, "move") if state_id and e["toStateId"] == state_id else None


def changed_since(cfg, hist, baseline):
    """True when anyone but the owner changed the ticket's title, text, labels, parent or
    relations at or after `baseline`, and the owner has not touched it since: an edit of the
    owner's own, or a move, strictly later."""
    theirs = [at for at, e in hist if at >= baseline and is_edit(e)
              and not is_owner(cfg, e["actorId"])]
    last = max(theirs) if theirs else None
    return last is not None and not any(
        at > last and is_owner(cfg, e["actorId"]) and (is_edit(e) or e["toStateId"])
        for at, e in hist)


# --------------------------------------------------------------------------- #
# The rules about a ticket itself
# --------------------------------------------------------------------------- #
def pointer_lines(description):
    """The lines of a ticket's Pointers section, a wrapped bullet joined to its line (None when
    the ticket has no such section)."""
    lines = dor.body_of(dor.split_sections(description), "Pointers")
    return None if lines is None else dor.pointer_lines(lines)


def pointer_spans(description):
    """The paths Pointers names, as the gate reads them: the first code span of a line if it
    looks like a path, a later one only if it is unmistakably one."""
    return [s for line in pointer_lines(description) or [] for k, s in enumerate(
        dor.code_spans([line])) if (dor.looks_like_path if k == 0 else dor.unmistakably_path)(s)]


def draft_of(t, parent=None):
    """A tracker issue in the shape the readiness gate reads: label names, ids of the project
    and the parent (the epic's id, for a piece of a plan)."""
    return {"id": t.get("identifier"), "title": t.get("title") or "",
            "description": t.get("description") or "", "labels": sorted(label_names(t)),
            "projectId": dig(t, "project", "id") or "",
            "parentId": parent or dig(t, "parent", "identifier") or ""}


def ready_problems(t, parent=None):
    """The readiness gate's rules, strict, over one tracker issue: the names of those that fail.
    Every rule of scripts/check_ticket_dor.py but one: that a pointer's file exists needs a
    checkout, and this module reads no disk. That Pointers names a path stays."""
    draft, desc = draft_of(t, parent), t.get("description") or ""
    r = dor.Report(draft["id"], draft["title"])
    dor.check_title(draft, r)
    sections = dor.check_sections(draft, r, dor.FALLBACK_SECTIONS)
    dor.check_acceptance(sections, r, True)
    dor.check_scope_and_tests(sections, r, True)
    if pointer_lines(desc) is not None and not pointer_spans(desc):
        r.err("pointers", "names no path")
    dor.check_placeholders(draft, r)
    dor.check_links(draft, r, dor.check_labels(draft, r, None, set()))
    return sorted(set(e["rule"] for e in r.errors))


def names_guarded(cfg, description):
    """True when a ticket's Pointers name a path the grader gate guards (or one the config adds),
    in the file form or the directory form. Every code span there is tried, not only the ones
    the gate takes for paths: a bare dotfile is not one, and is guarded all the same."""
    rxs = [re.compile(graders.glob_to_re(g)) for g in list(graders.FLOOR)
           + cfg["guarded_globs_extra"]]
    for span in [s for line in pointer_lines(description) or [] for s in dor.code_spans([line])]:
        path = re.sub(r":\d+(-\d+)?$", "", span.split("#", 1)[0]).strip()
        path = (path[2:] if path.startswith("./") else path).rstrip("/")
        if path and any(rx.match(path) or rx.match(path + "/_") for rx in rxs):
            return True
    return False


def small(cfg, t):
    efforts = [n for n in label_names(t) if n.startswith("effort:")]
    return len(efforts) == 1 and efforts[0][len("effort:"):] in cfg["agent_filed_efforts"]


def first_generation(ctx, rec):
    """Its source is a ticket the finding record does not itself list as filed by a session."""
    return isinstance(rec.get("source"), str) and rec["source"] not in ctx.by_ident


def plan_problems(ctx, epic, kids):
    """Why this is not a plan whose pieces may start, as codes: the bridge's own checks
    (`epic_problems`, and that the epic is not handed to the agent). [] when it is sound."""
    out = set()
    if any(k not in epic for k in EPIC_KEYS):
        out.add("unreadable")
    if GATE_EPIC_LABEL not in label_names(epic):
        out.add("not-gate-epic")
    if dig(epic, "parent", "id"):
        out.add("has-parent")
    if not kids:
        out.add("no-pieces")
    if dig(epic, "children", "pageInfo", "hasNextPage"):
        out.add("too-many-pieces")
    if any(GATE_CHILD_LABEL not in label_names(k) for k in kids):
        out.add("stray-piece")
    if any(has_children(k) for k in kids):
        out.add("nested-piece")
    if any(dig(k, "team", "key") != dig(epic, "team", "key") for k in kids):
        out.add("other-team-piece")
    if any(n.lower() in MODE_LABELS for node in [epic] + list(kids) for n in label_names(node)):
        out.add("mode-label")
    if dig(epic, "state", "type") in FINISHED:
        out.add("finished")
    if dig(epic, "delegate", "id") or epic.get("id") in ctx.busy:
        out.add("delegated")
    return sorted(out)


def _lower(t):
    return set(n.lower() for n in label_names(t))


NEVER = (       # the first of these that holds is the reason, whatever anyone approved
    ("review-ticket", lambda cfg, t, ctx: bool(cfg["reviews_team_key"])
     and dig(t, "team", "key") == cfg["reviews_team_key"]),
    ("other-team", lambda cfg, t, ctx: dig(t, "team", "key") not in cfg["team_keys"]),
    ("plan-ticket", lambda cfg, t, ctx: has_children(t) or t.get("id") in ctx.epic_ids),
    ("planning-ticket", lambda cfg, t, ctx: machine.issue_is_planning_ticket(t)),
    ("marked-blocked", lambda cfg, t, ctx: any(n == "agent:blocked" or n.startswith("blocked:")
                                               for n in _lower(t))),
    ("needs-person", lambda cfg, t, ctx: "agent:needs-human" in _lower(t)),
    ("hooks-change", lambda cfg, t, ctx: "hooks-change" in _lower(t)),
    ("delegated", lambda cfg, t, ctx: bool(dig(t, "delegate", "id"))),
    ("running", lambda cfg, t, ctx: t.get("id") in ctx.busy
     or dig(t, "state", "type") == "started"),
    ("finished", lambda cfg, t, ctx: dig(t, "state", "type") in FINISHED),
)


def never(cfg, t, ctx):
    for reason, hit in NEVER:
        if hit(cfg, t, ctx):
            return reason
    return None


def lane_of(cfg, t, ctx, epic):
    """Which source a ticket would come from, or None when its state is none the config counts."""
    if epic is not None:
        return "plan"
    name = dig(t, "state", "name")
    if name in cfg["ready_states"]:
        return "todo"
    if name in cfg["backlog_states"]:
        return "agent_filed" if ctx.rec_of(t) or GATE_EPIC_LABEL in label_names(t) else "backlog"
    return None


# --------------------------------------------------------------------------- #
# The four sources. Each returns one verdict; `v(reason, ...)` builds it.
# --------------------------------------------------------------------------- #
def lane_todo(cfg, t, ctx, epic, hist, created, v):
    mv = newest_move(hist, dig(t, "state", "id"), dig(t, "creator", "id"), created)
    if mv is None or mv[0] is None or (mv[2] == "creation" and HUMAN_LABEL not in label_names(t)):
        return v("actor-unknown", tap=True)
    if not is_owner(cfg, mv[0]):
        return v("not-owner", tap=True)
    if changed_since(cfg, hist, mv[1]):
        return v("changed", tap=True)
    return v("admitted", by=mv[0], at=iso(mv[1]), via=mv[2])


def lane_backlog(cfg, t, ctx, epic, hist, created, v):
    creator = dig(t, "creator", "id")
    if not (is_owner(cfg, creator) and HUMAN_LABEL in label_names(t)
            and not dig(t, "parent", "id")):
        return v("not-filed-by-owner", tap=True)
    if changed_since(cfg, hist, created):
        return v("changed", tap=True)
    return v("admitted", by=creator, at=iso(created), via="creation")


def lane_agent(cfg, t, ctx, epic, hist, created, v):
    rec = ctx.rec_of(t)
    if rec is None:
        return v("not-in-record", tap=True)
    if not small(cfg, t):
        return v("not-small", tap=True)
    if names_guarded(cfg, t.get("description") or ""):
        return v("guarded-path", tap=True)
    if not first_generation(ctx, rec):
        return v("generation", tap=True)
    if ctx.agent_starts is None:
        return v("count-unknown")
    if changed_since(cfg, hist, created):
        return v("changed", tap=True)
    return v("admitted", at=iso(created), via="limits")      # no person approved it: `by` is None


def slack_owner(cfg, by):
    return isinstance(by, str) and by in cfg["owner_slack_user_ids"]


def plan_approval(cfg, ctx, epic, rec, approved):
    """How the owner approved a plan: (by, seconds, via), or (reason, None, tap) when not, where
    `tap` says whether the owner's `approve` would settle it. A Slack approval by anyone else is
    already on the bridge's record, which would only answer that it is approved."""
    if rec is not None:                      # the bridge's record of an approval in Slack
        at, by = epoch(rec.get("approved_at")), rec.get("approved_by")
        if at is None or approved is None:
            return "record-unreadable", None, False
        return (by, at, "slack") if slack_owner(cfg, by) else ("not-owner", None, False)
    ehist = read_history(ctx.histories.get(epic.get("id")))
    if ehist is None:
        return "history-unreadable", None, False
    mv = newest_move(ehist, dig(epic, "state", "id"), None, None) \
        if dig(epic, "state", "name") in cfg["ready_states"] else None
    if mv is None:
        return "plan-not-approved", None, True
    if mv[0] is None:
        return "actor-unknown", None, True
    return (mv[0], mv[1], "move") if is_owner(cfg, mv[0]) else ("not-owner", None, True)


def lane_plan(cfg, t, ctx, epic, hist, created, v):
    rec = ctx.plans.get(epic.get("id"))
    approved = rec.get("approved") if isinstance(rec, dict) else None
    approved = approved if isinstance(approved, dict) else None
    if plan_problems(ctx, epic, [k for k in children_of(epic)
                                 if approved is None or k.get("id") in approved]):
        return v("plan-invalid")
    by, at, via = plan_approval(cfg, ctx, epic, rec, approved)
    # The owner moved this very piece into the ready state after the plan's approval, or the plan
    # has none: that is a ticket the owner moved, whatever its plan says.
    mv = newest_move(hist, dig(t, "state", "id"), None, None) \
        if dig(t, "state", "name") in cfg["ready_states"] else None
    if (mv and is_owner(cfg, mv[0]) and (at is None or mv[1] > at)
            and not changed_since(cfg, hist, mv[1])):
        return v("admitted", by=mv[0], at=iso(mv[1]), via="move", source="todo")
    if rec is not None and dig(epic, "state", "type") in ("backlog", "triage"):
        return v("plan-paused")                  # the owner paused it by moving it back
    if at is None:
        return v(by, tap=APPROVE % epic.get("identifier") if via else None)
    if via == "slack":
        if t.get("id") not in approved:
            return v("added-after-approval", tap=True)
        if fingerprint(t) != approved[t["id"]]:
            return v("changed", tap=True)
    elif created > at or any(e["toParentId"] == epic.get("id") and when >= at
                             for when, e in hist):
        return v("added-after-approval", tap=True)
    if changed_since(cfg, hist, at):
        return v("changed", tap=True)
    return v("admitted", by=by, at=iso(at), via=via)


LANES = {"todo": lane_todo, "backlog": lane_backlog, "plan": lane_plan, "agent_filed": lane_agent}


# --------------------------------------------------------------------------- #
# The decision
# --------------------------------------------------------------------------- #
def facts(t, epic):
    """The ids a verdict carries, each only if it is shaped like one: never a title or any text."""
    def pick(value, rx):
        return value if isinstance(value, str) and rx.match(value) else None
    return {"id": pick(dig(t, "id"), ID_RE), "ticket": pick(dig(t, "identifier"), TICKET_RE),
            "team": pick(dig(t, "team", "key"), TEAM_RE),
            "plan": pick(dig(epic, "identifier"), TICKET_RE)}


def judge(cfg, t, ctx, epic):
    """One candidate's verdict: the first rule that holds it, or admitted. With plans switched
    off a plan's pieces are ordinary tickets, and judged as such."""
    epic = epic if cfg["plans"] else None
    f = facts(t, epic)
    lane = lane_of(cfg, t, ctx, epic) if f["id"] and f["ticket"] else None

    def v(reason, tap=None, by=None, at=None, via=None, source=None):
        reply = (START % f["ticket"] if tap is True else tap) if tap else None
        return dict(f, source=source or lane or "none", admitted=reason == "admitted",
                    reason=reason, by=by, at=at, via=via, reply=reply)
    if f["id"] is None or f["ticket"] is None or any(k not in t for k in TICKET_KEYS):
        return v("malformed")
    hit = never(cfg, t, ctx)
    if hit or lane is None:
        return v(hit or "wrong-state")
    if ready_problems(t, f["plan"]):
        return v("not-ready")
    if lane == "backlog" and not cfg["backlog"]:
        return v("backlog-off", tap=True)
    if lane == "agent_filed" and cfg["agent_filed"] == "off":
        return v("agent-off", tap=True)
    if (ctx.filed is None and lane in ("backlog", "agent_filed")) or (
            ctx.plans is None and lane == "plan"):
        return v("record-unreadable")
    created, hist = epoch(t.get("createdAt")), read_history(ctx.histories.get(f["id"]))
    if created is None or hist is None:
        return v("history-unreadable")
    return LANES[lane](cfg, t, ctx, epic, hist, created, v)


def apply_cap(cfg, ctx, verdicts):
    """The day's cap on agent-filed starts: of the tickets this pass would admit, the lowest
    numbers up to what is left of it. A ticket past it waits for the owner's tap."""
    left = cfg["agent_filed_per_day"] - (ctx.agent_starts or 0)
    for v in sorted((v for v in verdicts if v["admitted"] and v["source"] == "agent_filed"),
                    key=lambda v: ticket_number(v["ticket"])):
        if left > 0:
            left -= 1
        else:
            v.update(admitted=False, reason="daily-cap", at=None, via=None,
                     reply=START % v["ticket"])


def decide(cfg, now, tickets, epics, histories, busy, findings, plans, ledger):
    """One verdict per candidate, flat tickets first, then each epic's pieces in order. A
    candidate this module cannot read is held as `malformed`, never guessed and never dropped."""
    cfg, bad = config(cfg)
    if bad:
        raise ValueError("the pool's config is not usable: " + "; ".join(bad))
    by_ident, by_uuid = {}, {}
    for rec in findings.values() if isinstance(findings, dict) else ():
        if isinstance(rec, dict) and isinstance(rec.get("filed"), str):
            by_ident[rec["filed"]] = rec
            if isinstance(rec.get("filed_uuid"), str):
                by_uuid[rec["filed_uuid"]] = rec

    def sid(value):                     # an id that can be a key: only a string can
        return value if isinstance(value, str) else None
    tickets = tickets if isinstance(tickets, list) else []
    epics = [e for e in epics if sid(dig(e, "id"))] if isinstance(epics, list) else []
    starts = dig(ledger, "agent_filed_starts") if dig(ledger, "date") == local_date(now) else None
    ctx = types.SimpleNamespace(
        busy=set(b for b in busy if sid(b)), by_ident=by_ident, filed=findings
        if isinstance(findings, dict) else None, plans=plans if isinstance(plans, dict) else None,
        histories=histories if isinstance(histories, dict) else {},
        epic_ids=set(e["id"] for e in epics) | set(plans if isinstance(plans, dict) else ()),
        agent_starts=starts if isinstance(starts, int) and not isinstance(starts, bool)
        and starts >= 0 else None,
        rec_of=lambda t: by_ident.get(sid(dig(t, "identifier"))) or by_uuid.get(sid(dig(t, "id"))))
    flat = dict((t["id"], t) for t in tickets if sid(dig(t, "id")))
    by_epic = dict((e["id"], e) for e in epics)
    pieces, listed = [], set()
    for e in epics:
        for k in children_of(e):
            merged = dict(k)
            merged.update(flat.get(sid(k.get("id")), {}))
            merged["parent"] = {"id": e["id"], "identifier": e.get("identifier")}
            pieces.append((merged, e))
            if sid(k.get("id")):
                listed.add(k["id"])
    out = []
    for t, epic in [(t, by_epic.get(sid(dig(t, "parent", "id")))) for t in tickets
                    if sid(dig(t, "id")) not in listed] + pieces:
        try:
            out.append(judge(cfg, t, ctx, epic))
        except (AttributeError, TypeError, KeyError, ValueError):
            out.append(dict(facts(t, epic), source="none", admitted=False, reason="malformed",
                            by=None, at=None, via=None, reply=None))
    apply_cap(cfg, ctx, out)
    return out


def main(argv=None):
    p = argparse.ArgumentParser(prog="pipeline_queue_pool.py", description=__doc__.split("\n")[0])
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)
    if args.selftest:
        return selftest()
    p.print_help()
    return EXIT_USAGE


# --------------------------------------------------------------------------- #
# --selftest: nothing fetched, nothing written, the clock stood in for
# --------------------------------------------------------------------------- #
def selftest():
    fails, ran = [], [0]

    def check(name, got, want):
        ran[0] += 1
        good = got == want
        print("[%s] %s%s" % ("PASS" if good else "FAIL", name,
                             "" if good else "  (got %r, want %r)" % (got, want)))
        if not good:
            fails.append(name)

    _cases(check)
    print("\n%d/%d checks passed" % (ran[0] - len(fails), ran[0]))
    return 1 if fails else 0


def _cases(check):
    import ast
    import builtins
    import contextlib
    import inspect
    import io
    import socket
    import subprocess
    import tempfile
    import pipeline_bridge_plans as bridge          # what the pool's copies are pinned to

    OWNER, DISP, OTHER, SLACK = "owner-1", "disp-1", "other-1", "U0OWNER001"
    LEAK = "SENTINEL-LEAK-CHECK-7c1e"                # text no verdict may carry
    T0, T1, T2, T3 = ("2026-11-02T%s:00:00Z" % h for h in ("09", "10", "11", "12"))
    NOW = epoch("2026-11-03T14:00:00Z")
    LEDGER = {"date": local_date(NOW), "starts": 3, "agent_filed_starts": 0}
    CFG = {"owner_user_ids": [OWNER], "dispatcher_user_ids": [DISP], "team_keys": ["ENG"],
           "owner_slack_user_ids": [SLACK], "reviews_team_key": "REV", "backlog": True,
           "agent_filed": "limits"}
    GOOD = dor.GOOD_DESCRIPTION.replace("## Context\n\n", "## Context\n\nSee %s. " % LEAK)
    STATES = {"Todo": ("st-todo", "unstarted"), "Backlog": ("st-backlog", "backlog"),
              "Doing": ("st-doing", "started"), "Done": ("st-done", "completed"),
              "Triage": ("st-triage", "triage")}
    UNREAD = object()

    def issue(n=1, state="Todo", labels=("effort:S", "provenance:human"), **kw):
        sid, kind = STATES[state]
        names = ("track:platform", "note-" + LEAK) + labels
        t = {"id": "id-%d" % n, "identifier": "ENG-%d" % n, "title": "Refresh tokens " + LEAK,
             "description": GOOD, "createdAt": T0,
             "state": {"id": sid, "name": state, "type": kind}, "team": {"key": "ENG"},
             "creator": {"id": OWNER}, "delegate": None, "parent": None,
             "children": {"nodes": []}, "project": {"id": "p1"}, "inverseRelations": {"nodes": []},
             "labels": {"nodes": [{"name": x} for x in names]}}
        t.update(kw)
        return t

    def ev(at, actor, **kw):
        return dict(dict.fromkeys(HISTORY_KEYS), createdAt=at, actorId=actor, **kw)

    def to_todo(at, actor):
        return ev(at, actor, fromStateId="st-backlog", toStateId="st-todo")

    MOVE = to_todo(T1, OWNER)

    def run(ts, hists, cfg=None, epics=(), busy=(), findings={}, plans={}, ledger=LEDGER):
        def read(x):
            return None if x is UNREAD else x
        return decide(dict(CFG, **(cfg or {})), NOW, ts, list(epics), hists, busy,
                      read(findings), read(plans), read(ledger))

    def sig(v):
        return (v["source"], v["reason"], v["reply"])

    def without(t, key):
        return dict((k, x) for k, x in t.items() if k != key)

    SC = []

    def sc(name, want, t=None, h=None, **kw):
        SC.append((name, want, issue() if t is None else t, [MOVE] if h is None else h, kw))

    ok, CH, START1 = ("todo", "admitted", None), ("todo", "changed", "start ENG-1"), "start ENG-1"
    # -- a ticket the owner moved into the ready state, by actor ------------------------------
    sc("a ticket the owner moved into the ready state", ok)
    sc("moved by someone else", ("todo", "not-owner", START1), h=[to_todo(T1, OTHER)])
    sc("moved by the dispatcher's user", ("todo", "not-owner", START1), h=[to_todo(T1, DISP)])
    sc("a move whose actor cannot be read", ("todo", "actor-unknown", START1),
       h=[to_todo(T1, None)])
    sc("the newest move is the approval, not the oldest", ok, h=[to_todo(T0, OTHER), MOVE])
    sc("…and an owner's older move is not a newer one's", ("todo", "not-owner", START1),
       h=[MOVE, to_todo(T2, OTHER)])
    sc("a newest move out of the state cannot say who approved it",
       ("todo", "actor-unknown", START1),
       h=[MOVE, ev(T2, OWNER, fromStateId="st-todo", toStateId="st-backlog")])
    sc("no state change at all: a person's creation is the move", ok, h=[])
    sc("…but a job's creation is not", ("todo", "actor-unknown", START1),
       issue(labels=("effort:S", "provenance:monitor")), [])
    sc("…and another person's creation is not the owner's", ("todo", "not-owner", START1),
       issue(creator={"id": OTHER}), [])
    sc("a history that could not be read", ("todo", "history-unreadable", None), h=UNREAD)
    sc("an entry without every field cannot say who edited what",
       ("todo", "history-unreadable", None),
       h=[{"createdAt": T1, "actorId": OWNER, "toStateId": "st-todo"}])
    # -- anyone else's change since, until the owner touches it again --------------------------
    for field, val in (("fromTitle", "a"), ("toTitle", "b"), ("updatedDescription", True),
                       ("addedLabelIds", ["l1"]), ("removedLabelIds", ["l2"]),
                       ("fromParentId", "p8"), ("toParentId", "p9"),
                       ("relationChanges", [{"type": "blocks"}])):
        sc("a %s change by someone else since the move is held" % field, CH,
           h=[MOVE, ev(T2, OTHER, **{field: val})])
    sc("a change that is not an edit (priority, assignee) is not a hold", ok,
       h=[MOVE, ev(T2, OTHER)])
    sc("a change before the move was seen by the owner who moved it", ok,
       h=[ev(T0, OTHER, toTitle="x"), MOVE])
    sc("a change in the very second of the move counts as after it", CH,
       h=[MOVE, ev(T1, OTHER, toTitle="x")])
    sc("an actor nobody can name is not the owner", CH, h=[MOVE, ev(T2, None, toTitle="x")])
    sc("the owner touching it afterwards clears the hold", ok,
       h=[MOVE, ev(T2, OTHER, toTitle="x"), ev(T3, OWNER, toTitle="y")])
    sc("an owner's touch before the change does not", CH,
       h=[MOVE, ev(T2, OWNER, toTitle="a"), ev(T3, OTHER, toTitle="x")])
    sc("the dispatcher's user editing is not the owner touching", CH,
       h=[MOVE, ev(T2, OTHER, toTitle="x"), ev(T3, DISP, toTitle="y")])
    # -- never ----------------------------------------------------------------------------------
    sc("a ticket delegated to the agent", ("todo", "delegated", None),
       issue(delegate={"id": "agent-1"}))
    sc("a ticket that ever had a session", ("todo", "running", None), busy=("id-1",))
    sc("a started ticket", ("none", "running", None), issue(state="Doing"))
    sc("a finished ticket", ("none", "finished", None), issue(state="Done"))
    sc("a state the config does not count", ("none", "wrong-state", None), issue(state="Triage"))
    for label, why in (("agent:blocked", "marked-blocked"), ("Blocked:capacity", "marked-blocked"),
                       ("agent:needs-human", "needs-person"), ("hooks-change", "hooks-change")):
        sc("labelled %s" % label, ("todo", why, None),
           issue(labels=("effort:S", "provenance:human", label)))
    sc("another team's ticket", ("todo", "other-team", None), issue(team={"key": "OPS"}))
    sc("a review ticket", ("todo", "review-ticket", None), issue(team={"key": "REV"}))
    sc("a plan ticket itself", ("todo", "plan-ticket", None),
       issue(children={"nodes": [{"id": "id-2"}]}))
    sc("a planning ticket", ("todo", "planning-ticket", None),
       issue(description="[repo=stage-a-planning-demo]\n\n" + GOOD))
    sc("a candidate it cannot read", ("none", "malformed", None), issue(id=None))
    sc("a field of the wrong shape cannot crash the pass", ("none", "malformed", None),
       dict(issue(), labels=["x"]))
    sc("a ticket read without its children cannot be told from a plan's",
       ("todo", "malformed", None), without(issue(), "children"))
    sc("…nor one read without its labels or its delegate", ("todo", "malformed", None),
       without(without(issue(), "labels"), "delegate"))
    sc("a ticket that fails the readiness gate", ("todo", "not-ready", None),
       issue(description="## Context\n\nOnly this.\n"))
    sc("a stray lifecycle label fails the gate too", ("todo", "not-ready", None),
       issue(labels=("effort:S", "provenance:human", "agent:queued")))
    # -- the owner's backlog --------------------------------------------------------------------
    BK = dict(t=issue(state="Backlog"), h=[])
    sc("a backlog ticket the owner filed", ("backlog", "admitted", None), **BK)
    sc("…with the backlog switched off", ("backlog", "backlog-off", START1),
       cfg={"backlog": False}, **BK)
    sc("a backlog ticket another person filed", ("backlog", "not-filed-by-owner", START1),
       issue(state="Backlog", creator={"id": OTHER}), [])
    sc("…a job's, by its provenance label", ("backlog", "not-filed-by-owner", START1),
       issue(state="Backlog", labels=("effort:S", "provenance:monitor")), [])
    sc("…a piece with a parent", ("backlog", "not-filed-by-owner", START1),
       issue(state="Backlog", parent={"id": "id-9", "identifier": "ENG-9"}), [])
    sc("a finding record that could not be read", ("backlog", "record-unreadable", None),
       findings=UNREAD, **BK)
    sc("an edit by someone else since it was filed", ("backlog", "changed", START1),
       issue(state="Backlog"), [ev(T2, OTHER, updatedDescription=True)])
    # -- a finding a session requested ----------------------------------------------------------
    FIND = {"c1": {"filed": "ENG-1", "filed_uuid": "id-1", "source": "ENG-90"}}
    AGENT = ("effort:S", "provenance:agent")

    def ag(**over):
        return dict(dict(t=issue(state="Backlog", labels=AGENT), h=[], findings=FIND,
                         cfg={"backlog": False}), **over)

    def pointing(pointer, text="`session.ts` — the reactive refresh this replaces"):
        return GOOD.replace(text, pointer)
    FA = "agent_filed"
    sc("a small finding within the limits, the backlog off", (FA, "admitted", None), **ag())
    sc("…never by itself when agent_filed is off", (FA, "agent-off", START1),
       **ag(cfg={"backlog": True, "agent_filed": "off"}))
    sc("…a label is not the finding record", (FA, "not-in-record", START1), **ag(findings={}))
    sc("…an effort above S", (FA, "not-small", START1),
       **ag(t=issue(state="Backlog", labels=("effort:M", "provenance:agent"))))
    for what, pointer in (("a file", "`.claude/hooks/pre-tool-use.py` — a guard"),
                          ("a directory", "`.claude/hooks/` — the guards"),
                          ("a workflow, by line", "`.github/workflows/ci.yml:12` — the job"),
                          ("a grader, by anchor", "`scripts/check_x.py#L9` — the gate")):
        sc("…a guarded path in Pointers: %s" % what, (FA, "guarded-path", START1),
           **ag(t=issue(state="Backlog", labels=AGENT, description=pointing(pointer))))
    sc("…a guarded path outside Pointers is not named in them", (FA, "admitted", None),
       **ag(t=issue(state="Backlog", labels=AGENT, description=GOOD.replace(
           "- Refresh-token rotation", "- Edits to `.github/workflows/ci.yml`"))))
    sc("…a bare dotfile, which the gate does not take for a path", (FA, "guarded-path", START1),
       **ag(t=issue(state="Backlog", labels=AGENT, description=pointing(
           "`session.ts` — the refresh\n- `.secretlintrc` — the scanner's rules"))))
    sc("…a source the record lists as filed too", (FA, "generation", START1),
       **ag(findings=dict(FIND, c0={"filed": "ENG-90", "source": "ENG-80"})))
    sc("…a source the record does not name", (FA, "generation", START1),
       **ag(findings={"c1": {"filed": "ENG-1", "filed_uuid": "id-1"}}))
    sc("…today's count that could not be read", (FA, "count-unknown", None), **ag(ledger=UNREAD))
    sc("…a ledger of another day", (FA, "count-unknown", None),
       **ag(ledger=dict(LEDGER, date="1999-01-01")))
    sc("…an edit by the dispatcher's user since", (FA, "changed", START1),
       **ag(h=[ev(T2, DISP, updatedDescription=True)]))
    sc("…the owner's key linking its source at filing is no change", (FA, "admitted", None),
       **ag(h=[ev(T0, OWNER, relationChanges=[{"type": "related"}])]))
    sc("a finding the owner then moved to the ready state", ok,
       issue(labels=("effort:M", "provenance:agent")), [MOVE], findings=FIND)

    def cap(started, per_day=None):
        ts = [issue(n, state="Backlog", labels=AGENT) for n in (2, 1)]
        find = dict(("c%d" % n, {"filed": "ENG-%d" % n, "source": "ENG-90"}) for n in (1, 2))
        return run(ts, dict((t["id"], []) for t in ts), findings=find,
                   cfg={"backlog": False, "agent_filed_per_day": per_day or 5},
                   ledger=dict(LEDGER, agent_filed_starts=started))

    # -- the pieces of a plan the owner approved, as they stood --------------------------------
    def piece(n, state="Backlog", **kw):
        return issue(n, state=state, labels=("effort:S", "provenance:epic"), **kw)

    def planworld(epic_state="Todo", slack=True, approver=SLACK, epic_hist=None, pieces=None,
                  hists=None, approved_from=None, cfg=None, plans={}, tickets=()):
        kids = [piece(11), piece(12)] if pieces is None else pieces
        epic = issue(10, state=epic_state, labels=("provenance:agent",),
                     children={"nodes": kids, "pageInfo": {"hasNextPage": False}})
        shown = kids if approved_from is None else approved_from
        rec = {"id-10": {"ticket": "ENG-10", "approved_by": approver, "approved_at": T1,
                         "approved": dict((k["id"], fingerprint(k)) for k in shown)}}
        hs = {"id-10": [to_todo(T1, OWNER)] if epic_hist is None else epic_hist}
        hs.update(dict((k["id"], []) for k in kids))
        hs.update(hists or {})
        return run(list(tickets), hs, cfg=cfg, epics=[epic], plans=rec if slack else plans)

    PA, PLAN = ("plan", "admitted", None), []

    def P(name, want, **kw):
        PLAN.append((name, lambda: planworld(**kw), want))

    def tap(reason, reply, n=2):
        return [("plan", reason, reply)] * n
    P("approved in Slack by the owner", [PA, PA])
    P("…by a Slack member who is not the owner: the bridge has it, so no reply helps",
      tap("not-owner", None), approver="U0OTHER001")
    P("a piece changed since the approval", [("plan", "changed", "start ENG-11"), PA],
      pieces=[piece(11, description=GOOD + "\nMore."), piece(12)],
      approved_from=[piece(11), piece(12)])
    P("a piece added after the approval",
      [PA, PA, ("plan", "added-after-approval", "start ENG-13")],
      pieces=[piece(11), piece(12), piece(13)], approved_from=[piece(11), piece(12)])
    P("a plan the owner paused", tap("plan-paused", None), epic_state="Backlog")
    P("a piece the idea gate did not file", tap("plan-invalid", None),
      pieces=[piece(11), issue(12, state="Backlog")])
    P("plans switched off: its pieces are ordinary tickets",
      [("backlog", "not-filed-by-owner", "start ENG-11"),
       ("backlog", "not-filed-by-owner", "start ENG-12")], cfg={"plans": False})
    P("…and the owner's own move of one still starts it",
      [ok, ("backlog", "not-filed-by-owner", "start ENG-12")], cfg={"plans": False},
      pieces=[piece(11, state="Todo"), piece(12)], hists={"id-11": [to_todo(T3, OWNER)]})
    P("…a piece of a paused plan the owner moved to ready itself",
      [ok, ("plan", "plan-paused", None)], epic_state="Backlog",
      pieces=[piece(11, state="Todo"), piece(12)], hists={"id-11": [to_todo(T3, OWNER)]})
    P("the bridge's record could not be read", tap("record-unreadable", None), plans=UNREAD,
      slack=False)
    P("approved by moving the epic to the ready state", [PA, PA], slack=False)
    P("…by someone else", tap("not-owner", "approve ENG-10"), slack=False,
      epic_hist=[to_todo(T1, OTHER)])
    P("…the epic still in the backlog", tap("plan-not-approved", "approve ENG-10"), slack=False,
      epic_state="Backlog", epic_hist=[])
    P("…by an actor nobody can name", tap("actor-unknown", "approve ENG-10"), slack=False,
      epic_hist=[to_todo(T1, None)])
    P("…its history unreadable", tap("history-unreadable", None), slack=False, epic_hist=UNREAD)
    P("…a piece made after the approval", [PA, ("plan", "added-after-approval", "start ENG-12")],
      slack=False, pieces=[piece(11), piece(12, createdAt=T3)])
    P("…a piece moved under it after the approval",
      [PA, ("plan", "added-after-approval", "start ENG-12")], slack=False,
      hists={"id-12": [ev(T2, OWNER, toParentId="id-10")]})
    P("an edit by someone else since the approval", [("plan", "changed", "start ENG-11"), PA],
      hists={"id-11": [ev(T2, OTHER, toTitle="x")]})
    P("…and the owner's touch after it", [PA, PA],
      hists={"id-11": [ev(T2, OTHER, toTitle="x"), ev(T3, OWNER, toTitle="y")]})
    P("the owner moved a piece to ready after approving the plan", [ok, PA],
      pieces=[piece(11, state="Todo"), piece(12)], hists={"id-11": [to_todo(T3, OWNER)]},
      approved_from=[piece(11, description="x"), piece(12)])
    P("…or when the plan has no approval", [ok, ("plan", "plan-not-approved", "approve ENG-10")],
      slack=False, epic_state="Backlog", epic_hist=[], pieces=[piece(11, state="Todo"), piece(12)],
      hists={"id-11": [to_todo(T2, OWNER)]})
    P("a ticket attached under the plan, read apart from it",
      [("plan", "added-after-approval", "start ENG-13"), PA, PA], hists={"id-13": []},
      tickets=[piece(13, parent={"id": "id-10", "identifier": "ENG-10"})])

    def battery():
        """Every scenario's verdict, and the unit answers: what a mutant must change."""
        got, verdicts = [], []
        for _name, _want, t, h, kw in SC:
            v = run([t], {t["id"]: None if h is UNREAD else h}, **kw)[0]
            got.append(sig(v))
            verdicts.append(v)
        for _name, thunk, _want in PLAN:
            vs = thunk()
            got.append([sig(v) for v in vs])
            verdicts += vs
        for vs in (cap(4), cap(5), cap(4, 6), cap(4, 1)):
            got.append([(v["ticket"], v["reason"], v["reply"]) for v in vs])
            verdicts += vs
        both = {"owner_user_ids": [OWNER, DISP], "dispatcher_user_ids": [DISP]}
        got.append([is_owner(both, DISP), is_owner(both, OWNER), is_owner(both, None)])
        return got, verdicts

    WANT = [w for _n, w, _t, _h, _k in SC] + [w for _n, _v, w in PLAN]
    WANT += [[("ENG-2", "daily-cap", "start ENG-2"), ("ENG-1", "admitted", None)],
             [("ENG-2", "daily-cap", "start ENG-2"), ("ENG-1", "daily-cap", "start ENG-1")],
             [("ENG-2", "admitted", None), ("ENG-1", "admitted", None)],
             [("ENG-2", "daily-cap", "start ENG-2"), ("ENG-1", "daily-cap", "start ENG-1")],
             [False, True, False]]
    got, verdicts = battery()
    for (name, want, _t, _h, _k), g in zip(SC, got):
        check(name, g, want)
    for (name, _v, want), g in zip(PLAN, got[len(SC):]):
        check("plan: " + name, g, want)
    for name, g, want in zip(("the day's cap: the lowest numbers first", "…none left at the cap",
                              "…a larger cap from the config", "…a smaller one"),
                             got[-5:-1], WANT[-5:-1]):
        check("agent-filed " + name, g, want)
    check("the dispatcher's user is never the owner, even listed; an unknown actor neither",
          got[-1], WANT[-1])
    plan_ticket = planworld(tickets=[issue(10, state="Todo", children={"nodes": [{"id": "x"}]})])
    check("the plan's own ticket is never a piece", [sig(v) for v in plan_ticket][0],
          ("todo", "plan-ticket", None))

    # -- the shape of what comes out -------------------------------------------------------------
    check("every reason is on the closed list, and every one on it is reached",
          sorted(set(v["reason"] for v in verdicts)), sorted(REASONS))
    check("every verdict is fields only, with the documented keys",
          set(tuple(sorted(v)) for v in verdicts),
          set([tuple(sorted(("id", "ticket", "team", "plan", "source", "admitted", "reason", "by",
                             "at", "via", "reply")))]))
    check("sources and ways are closed words", (set(v["source"] for v in verdicts) <= set(SOURCES),
                                                set(v["via"] for v in verdicts) - {None} <=
                                                set(VIAS)), (True, True))
    check("a reply is one of two fixed sentences, with an id",
          [v["reply"] for v in verdicts if v["reply"]
           and not re.match(r"^(start|approve) [A-Z][A-Z0-9]*-[0-9]+\Z", v["reply"])], [])
    check("an admitted verdict names who approved it and when, except a finding by the limits",
          [v["ticket"] for v in verdicts if v["admitted"] and not (
              v["at"] and (v["by"] or v["via"] == "limits"))], [])
    check("no title, text or label name reaches a verdict (the sentinel)",
          LEAK in json.dumps(verdicts), False)
    hostile = [issue(2, identifier="not-an-id"), "junk", None,
               issue(3, labels=("effort:S",), team=5), dict(issue(4), id=["x"]),
               dict(issue(5), parent={"id": {}})]
    check("a pass over ids shaped like none holds them, never drops or crashes",
          [sig(v) for v in run(hostile, {})],
          [("none", "malformed", None)] * 3 + [("todo", "other-team", None),
                                               ("none", "malformed", None),
                                               ("todo", "history-unreadable", None)])

    # -- every number is the config's -----------------------------------------------------------
    check("the defaults are the brief's: backlog off, agent-filed off, 5 a day, small is S",
          (DEFAULTS["backlog"], DEFAULTS["agent_filed"], DEFAULTS["agent_filed_per_day"],
           DEFAULTS["agent_filed_efforts"], DEFAULTS["plans"]), (False, "off", 5, ["S"], True))
    tree = ast.parse(inspect.getsource(sys.modules[__name__]).split("\ndef selftest():")[0])
    numbers = sorted(set((fn.name, n.value) for fn in ast.walk(tree)
                         if isinstance(fn, ast.FunctionDef) and fn.name in (
                             "judge", "apply_cap", "decide", "changed_since", "newest_move",
                             "lane_todo", "lane_backlog", "lane_agent", "lane_plan",
                             "plan_approval", "small", "first_generation")
                         for n in ast.walk(fn) if isinstance(n, ast.Constant)
                         and isinstance(n.value, int) and not isinstance(n.value, bool)
                         and n.value > 2))
    check("no number above 2 sits in a decision function: each is the config's", numbers, [])

    # -- the config ------------------------------------------------------------------------------
    good, why = config(CFG)
    check("a sound config has no problem, and is filled in",
          (why, good["ready_states"], good["backlog_states"], good["guarded_globs_extra"]),
          ([], ["Todo"], ["Backlog"], []))
    check("config is idempotent", config(good), (good, []))
    check("the owner, the dispatcher and the teams have no default",
          sorted(w.split(":")[0] for w in config({})[1]),
          ["dispatcher_user_ids", "owner_user_ids", "team_keys"])
    for name, over in (("the dispatcher's user listed as an owner",
                        {"owner_user_ids": [OWNER, DISP]}),
                       ("no owner", {"owner_user_ids": []}), ("no teams", {"team_keys": []}),
                       ("a setting that is not one", {"owmer_user_ids": [OWNER]}),
                       ("agent_filed neither off nor limits", {"agent_filed": "yes"}),
                       ("a count that is a flag", {"agent_filed_per_day": True}),
                       ("a negative count", {"agent_filed_per_day": -1}),
                       ("ready and backlog sharing a state", {"backlog_states": ["Todo"]}),
                       ("the reviews team as work", {"team_keys": ["ENG", "REV"]}),
                       ("a Slack id that is not one", {"owner_slack_user_ids": ["me"]}),
                       ("the backlog as text", {"backlog": "on"}),
                       ("an effort that is not S, M or L", {"agent_filed_efforts": ["XL"]})):
        check("a bad config is refused: %s" % name, bool(config(dict(CFG, **over))[1]), True)
    check("a config that is not an object is refused", config("x"), (None, [
        "the config is not an object"]))
    try:
        decide({}, NOW, [], [], {}, (), {}, {}, LEDGER)
        refused = False
    except ValueError:
        refused = True
    check("decide refuses to run on a bad config", refused, True)
    check("with no tickets it answers with nothing", run([], {}), [])

    # -- pinned to what it copies, and to what it shares ----------------------------------------
    kids = [piece(11), piece(12, inverseRelations={"nodes": [{"type": "blocks", "issue": {
        "id": "id-11", "identifier": "ENG-11", "state": {"name": "Todo", "type": "unstarted"}}}]}),
        issue(3, project=None, labels=())]
    check("the fingerprint is the bridge's, byte for byte",
          [fingerprint(k) for k in kids], [bridge.fingerprint(k) for k in kids])
    ctx = types.SimpleNamespace(busy=set())

    def epic_with(kids=None, labels=("provenance:agent",), **kw):
        ks = [piece(11), piece(12)] if kids is None else kids
        epic = issue(10, labels=labels, children={"nodes": ks, "pageInfo": {"hasNextPage": False}})
        epic.update(kw)
        return epic, ks
    broken = {
        "not-gate-epic": epic_with(labels=("effort:S",)),
        "has-parent": epic_with(parent={"id": "x"}),
        "no-pieces": epic_with(kids=[]),
        "too-many-pieces": epic_with(children={"nodes": [piece(11)], "pageInfo": {
            "hasNextPage": True}}),
        "stray-piece": epic_with(kids=[piece(11), issue(12)]),
        "nested-piece": epic_with(kids=[piece(11, children={"nodes": [{"id": "z"}]})]),
        "other-team-piece": epic_with(kids=[piece(11, team={"key": "OPS"})]),
        "mode-label": epic_with(labels=("provenance:agent", "Orchestrator")),
        "finished": epic_with(state={"id": "st-done", "name": "Done", "type": "completed"})}
    check("a sound plan has no problem, here or in the bridge",
          (plan_problems(ctx, *epic_with()[:1], epic_with()[1]),
           bridge.epic_problems(*epic_with())), ([], []))
    for code, (e, ks) in sorted(broken.items()):
        check("plan problem %s is the bridge's too" % code,
              (plan_problems(ctx, e, ks), bool(bridge.epic_problems(e, ks))), ([code], True))
    check("a plan handed to the agent is a problem",
          plan_problems(ctx, epic_with(delegate={"id": "a"})[0], [piece(11)]), ["delegated"])
    check("an epic read without a field it is judged on is a problem",
          plan_problems(ctx, without(epic_with()[0], "delegate"), epic_with()[1]), ["unreadable"])
    with tempfile.TemporaryDirectory() as tmp:
        open(os.path.join(tmp, "session.ts"), "w").close()
        for name, t in (("a ready ticket", issue()), ("no sections", issue(description="x")),
                        ("an unfinished draft", issue(description=GOOD + "\nTBD")),
                        ("two efforts", issue(labels=("effort:S", "effort:M",
                                                      "provenance:human"))),
                        ("no project", issue(project=None)),
                        ("a long title", issue(title="x" * 99)),
                        ("a provenance and its parent", piece(5, parent={"identifier": "ENG-2"}))):
            gate = dor.run([draft_of(t)], dor.FALLBACK_SECTIONS, None, True, tmp)["tickets"][0]
            check("the gate's rules, strict, are the pool's: %s" % name, ready_problems(t),
                  sorted(set(e["rule"] for e in gate["errors"])))
        ghost = issue(description=pointing("`nowhere/missing.py` — a file that is not there"))
        check("the one rule it leaves out: a pointer's file on disk",
              (ready_problems(ghost), dor.run([draft_of(ghost)], dor.FALLBACK_SECTIONS, None,
                                              True, tmp)["ok"]), ([], False))
    cfg0 = config(CFG)[0]
    samples = [g.replace("**", "x").replace("*", "x") for g in graders.FLOOR]
    check("the guarded paths default to the grader gate's own set",
          [s for s in samples if not names_guarded(cfg0, "## Pointers\n\n- `%s` — x\n" % s)], [])
    check("…an ordinary path is not guarded, and the config adds to the floor",
          (names_guarded(cfg0, "## Pointers\n\n- `src/app.py` — x\n"),
           names_guarded(config(dict(CFG, guarded_globs_extra=["src/secret/**"]))[0],
                         "## Pointers\n\n- `src/secret/key.py` — x\n")), (False, True))

    # -- it touches nothing -----------------------------------------------------------------------
    source = inspect.getsource(sys.modules[__name__]).split("\ndef selftest():")[0]
    check("no file, socket, process or environment word is in the source",
          [w for w in ("import socket", "urllib", "http.client", "subprocess", "shutil",
                       "os.system", "os.popen", "os.environ", "getenv", "os.fork", "os.exec",
                       "os.spawn", "os.listdir", "os.scandir", "os.stat", "os.path.exists",
                       "import glob", ".write(", "os.replace(", "os.unlink(", "os.remove(",
                       "makedirs")
           if w in source] + re.findall(r"open\([^)]*[\"'][wax+]", source), [])

    class Watch(object):
        """Counts every file, process, environment and socket touch while it is open."""
        def __enter__(self):
            self.hits, self.saved = [], []
            targets = ((builtins, "open"), (os, "stat"), (os, "lstat"), (os, "listdir"),
                       (os, "scandir"), (os, "getenv"), (socket.socket, "connect"),
                       (subprocess.Popen, "__init__"))
            for owner, name in targets:
                real = getattr(owner, name)
                self.saved.append((owner, name, owner.__dict__.get(name, self), real))

                def seen(*a, _n=name, _r=real, **k):
                    self.hits.append(_n)
                    return _r(*a, **k)
                setattr(owner, name, seen)
            return self

        def __exit__(self, *exc):
            for owner, name, mine, real in self.saved:
                if mine is self:
                    delattr(owner, name)
                else:
                    setattr(owner, name, real)
    with Watch() as w:
        battery()
    check("a whole pass opens no file, runs no process, reads no environment, opens no socket",
          w.hits, [])
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main([])
    check("no command is a usage error: the pool has none but --selftest", code, EXIT_USAGE)

    # -- mutants: a check that cannot fail checks nothing ---------------------------------------
    o = dict((k, globals()[k]) for k in (
        "is_owner", "newest_move", "changed_since", "read_history", "lane_of", "judge", "LANES",
        "NEVER", "EDIT_FIELDS", "ready_problems", "plan_problems", "fingerprint", "names_guarded",
        "small", "first_generation", "apply_cap", "slack_owner", "decide"))

    def caught(**patched):
        saved = dict((k, globals()[k]) for k in patched)
        globals().update(patched)
        try:
            return battery()[0] != got
        finally:
            globals().update(saved)

    def lane(name, wrap):
        return dict(LANES, **{name: lambda cfg, t, *rest: wrap(o["LANES"][name], cfg, t, *rest)})

    def approve_all(f, cfg, t, ctx, epic, *rest):
        """The bridge's record, but with this piece among those approved as they stood."""
        seen = dict((k, dict(v, approved=dict(v["approved"], **{t["id"]: fingerprint(t)})))
                    for k, v in ctx.plans.items())
        return f(cfg, t, types.SimpleNamespace(**dict(vars(ctx), plans=seen)), epic, *rest)

    def no_actor_is_owner(*args):
        move = o["newest_move"](*args)
        return (OWNER,) + move[1:] if move and move[0] is None else move

    def dotfiles_unseen(cfg, description):
        named = "\n".join("- `%s` — x" % s for s in pointer_spans(description))
        return o["names_guarded"](cfg, "## Pointers\n\n" + named)
    for reason, _hit in NEVER:
        check("mutant: the %s rule dropped is caught" % reason,
              caught(NEVER=tuple((r, (lambda *a: False) if r == reason else f)
                                 for r, f in o["NEVER"])), True)
    families = {"title": ("fromTitle", "toTitle"), "text": ("updatedDescription",),
                "label": ("addedLabelIds", "removedLabelIds"), "parent": ("fromParentId",
                                                                         "toParentId"),
                "relation": ("relationChanges",)}
    for fam, names in sorted(families.items()):
        check("mutant: a %s change not counted as an edit is caught" % fam,
              caught(EDIT_FIELDS=tuple(f for f in o["EDIT_FIELDS"] if f not in names)), True)
    for name, patch in (
            ("anyone counted as the owner", dict(is_owner=lambda cfg, a: True)),
            ("the dispatcher's user counted as the owner",
             dict(is_owner=lambda cfg, a: a in cfg["owner_user_ids"])),
            ("the oldest move taken for the newest", dict(newest_move=lambda h, *a: o[
                "newest_move"](list(reversed(h)), *a))),
            ("a creation never the move", dict(newest_move=lambda h, s, c, d: o["newest_move"](
                h, s, None, None))),
            ("a change by anyone never a hold", dict(changed_since=lambda cfg, h, b: False)),
            ("the owner's later touch never counted", dict(changed_since=lambda cfg, h, b: o[
                "changed_since"](cfg, [(a, e) for a, e in h if not o["is_owner"](cfg, e[
                    "actorId"])], b))),
            ("the owner's own edit counted as a change", dict(changed_since=lambda cfg, h, b: o[
                "changed_since"](dict(cfg, owner_user_ids=[]), h, b))),
            ("a history that cannot be read taken for an empty one", dict(
                read_history=lambda raw: [] if not isinstance(raw, list) else o["read_history"](
                    raw))),
            ("the readiness gate skipped", dict(ready_problems=lambda t, parent=None: [])),
            ("a finding taken for the owner's backlog", dict(lane_of=lambda cfg, t, c, e: (
                "backlog" if o["lane_of"](cfg, t, c, e) == "agent_filed" else o["lane_of"](
                    cfg, t, c, e)))),
            ("any provenance label taken for a person's", dict(HUMAN_LABEL="track:platform")),
            ("a backlog ticket's creator not checked", dict(LANES=lane(
                "backlog", lambda f, cfg, t, *r: f(cfg, dict(t, creator={"id": OWNER}), *r)))),
            ("an effort above S taken for small", dict(small=lambda cfg, t: True)),
            ("a guarded path never named", dict(names_guarded=lambda cfg, d: False)),
            ("every source taken for a first generation", dict(first_generation=lambda c, r:
                                                              True)),
            ("the day's cap never applied", dict(apply_cap=lambda cfg, ctx, vs: None)),
            ("the off setting ignored", dict(judge=lambda cfg, *a: o["judge"](
                dict(cfg, agent_filed="limits"), *a))),
            ("a ledger of any day taken for today's", dict(local_date=lambda now: "1999-01-01")),
            ("the plan's fingerprint never differs", dict(fingerprint=lambda child: "same")),
            ("any Slack member taken for the owner", dict(slack_owner=lambda cfg, by: True)),
            ("a plan's problems never held", dict(plan_problems=lambda ctx, e, k: [])),
            ("the owner's own move of a piece not honoured", dict(LANES=lane(
                "plan", lambda f, cfg, t, *r: f(cfg, dict(t, state=dict(
                    t["state"], name="Backlog")), *r)))),
            ("a move with no actor taken for the owner's", dict(newest_move=no_actor_is_owner)),
            ("a backlog ticket's parent not checked", dict(LANES=lane(
                "backlog", lambda f, cfg, t, *r: f(cfg, dict(t, parent=None), *r)))),
            ("the backlog switch ignored", dict(judge=lambda cfg, *a: o["judge"](
                dict(cfg, backlog=True), *a))),
            ("plans switched off ignored", dict(judge=lambda cfg, *a: o["judge"](
                dict(cfg, plans=True), *a))),
            ("an unreadable record taken for an empty one", dict(decide=lambda c, n, t, e, h, b, f,
                                                                  p, led: o["decide"](
                c, n, t, e, h, b, {} if f is None else f, {} if p is None else p, led))),
            ("a label taken for the finding record", dict(LANES=lane(
                "agent_filed", lambda f, cfg, t, ctx, *r: f(cfg, t, types.SimpleNamespace(
                    **dict(vars(ctx), rec_of=lambda x: {"source": "ENG-90"})), *r)))),
            ("a paused plan not heeded", dict(LANES=lane(
                "plan", lambda f, cfg, t, ctx, epic, *r: f(cfg, t, ctx, dict(epic, state=dict(
                    epic["state"], type="unstarted")), *r)))),
            ("a piece made after the approval not noticed", dict(LANES=lane(
                "plan", lambda f, cfg, t, ctx, epic, hist, created, v: f(
                    cfg, t, ctx, epic, hist, 0, v)))),
            ("a piece added to an approved plan not noticed",
             dict(LANES=lane("plan", approve_all))),
            ("a bare dotfile not taken for a guarded path", dict(names_guarded=dotfiles_unseen)),
            ("a ticket read without the fields the rules judge on", dict(TICKET_KEYS=())),
            ("the reply's words changed", dict(START="begin %s"))):
        check("mutant: %s is caught" % name, caught(**patch), True)
    check("…and with every mutant undone the pool is as it was", battery()[0] == got, True)


if __name__ == "__main__":
    sys.exit(main())
