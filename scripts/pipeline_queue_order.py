#!/usr/bin/env python3
"""The queue's order and limits: what starts next, within each repository's lanes and the day's
allowance, and the runaway stops that hold everything (KIT-434, MVP 2 part 2b;
docs/adr/2026-11-03-autonomous-workers-within-a-budget-and-a-window.md).

    python3 scripts/pipeline_queue_order.py --selftest

A PURE FUNCTION. `order(...)` is given what part 2a (scripts/pipeline_queue_pool.py) said, what
runs, the usage file (contract §17) and today's counts, and says what may start now. It starts
nothing and writes nothing: no file is read, no socket opened, no process started, no
environment variable read. It imports no other part: 2a's verdicts arrive as plain data, so
each part is reviewed and merged alone. A later part wires it into the bridge's hand-over.

WHAT IT IS GIVEN.
  cfg      see `config()`: every number is here, each with a default sized for a runaway.
  now      seconds since 1970. Only its local date is read, in the machine's own time zone as the
           usage job names a day, to find today in the usage file and the ledger.
  pieces   one dict for each ticket 2a judged, admitted or held: its verdict (`ticket`,
           `admitted`, `reason`, `reply`, `source`, `plan`, `by`, `at`) and what the queue adds:
           `repo` ("owner/name"), `priority` (0 none, 1 Urgent .. 4 Low), `plan_priority`,
           `effort` ("S", "M", "L"), `blockers` ([{ticket, state, type, labels}], or None when
           they could not all be read) and, when declared, `footprint` (the files it expects to
           touch).
  running  what holds a lane, one entry for each piece: its live session ({kind: "session",
           repo, ref: ticket, effort, lane, footprint}) while it has one, else its pull request
           open and not merged ({kind: "pr", repo, ref: "owner/name#N"}). Pieces only: a review
           or a chat holds no lane. A `lane` ("owner/name:1") keeps its slot.
  usage    the usage file's document, or None. ledger: {"date", "starts", "agent_filed_starts"}.

THE ORDER.  Priority first (Urgent, High, Medium, Low, then none; a piece's own, else its plan's),
then the plan approved first, then the plan's dependency order (after the pieces that block it;
ties by ticket number), then ticket number. A piece with any open blocker never starts, and a
blocker that was canceled is open: only a completed one lets it go.

THE LIMITS.  A piece starts into a free lane of its repository: `lanes_per_repo` (default 1,
never above 3; `lanes_by_repo` names exceptions) counts sessions AND pull requests, because a
pull request waiting for a merge holds its lane. Lanes are `owner/name:<slot>`: the repository and
a slot number, the same tomorrow as today. Where a repository's merge gate cannot yet test open
pull requests together (`merge_gate_ready` lists those that can), a second lane needs every
footprint declared and none shared; without footprints it is one lane. At most `running_cap`
sessions at once. The day's load, `planned_load`, is today's spend from the usage file plus an
estimate (per effort, S, M, L) for each session running and each piece started: a piece that
does not fit is held `over-load`, and is not a stop.

THE RUNAWAY STOPS.  Each holds every start and is named in `stops`: the plan near its limit
(`plan-near-limit`) or at it (`plan-at-limit`), from the usage file's windows; today's spend at
or over `day_stop` (60); a piece in play whose spend this week is over `piece_stop` (15); more
running than the caps; starts today at or over `starts_cap` (20), or agent-filed starts over
`agent_filed_per_day`; the usage file missing, failed, more than `max_usage_age_seconds` from now
(older, or dated ahead) or not about today; today's counts unknown; any run signed in with an API
key. A window whose reset has passed no longer counts: the plan resumes by itself. A figure no
one can read is a stop, never a zero.

WHAT IT RETURNS.  `start` (in order, each with its lane and slot, its estimate, and who approved
it), `held` (admitted pieces that cannot start now, each with a reason from HELD), `stops`,
`needs_ok` (what only the owner can do, and how much of today's plan waits on it: kind, ref,
pieces, usd, the exact reply or None, and `urgent` when the top of the queue is Urgent or High
and waits on it), `order` (the whole queue, in order), `load` and `notes`. Ids, words from
closed lists and numbers only: a title or text never reaches the result.
"""
import argparse
import copy
import datetime
import math
import re
import sys
import time
import types

EXIT_USAGE = 2
SCHEMA = "pipeline-usage/1"
MAX_LANES = 3                        # the most lanes a repository may have, whatever the config
EFFORTS = ("S", "M", "L")
PRIORITIES = (1, 2, 3, 4)            # Urgent, High, Medium, Low: 0 is none, and sorts last
URGENT = (1, 2)                      # the ones the top of the queue pages for
WINDOWS = ("five_hour", "seven_day", "seven_day_opus", "seven_day_sonnet",
           "seven_day_overage_included", "overage", "other")
HELD = ("malformed", "no-repo", "no-estimate", "blockers-unreadable", "blocked", "stopped",
        "no-lane", "running-cap", "starts-cap", "over-load")
STOPS = ("usage-missing", "usage-failed", "usage-stale", "ledger-unknown", "plan-near-limit",
         "plan-at-limit", "day-hard-stop", "piece-cost", "too-many-running", "too-many-starts",
         "api-key")
KINDS = ("plan", "agent_filed", "changed", "approval", "merge", "question")
NOTES = ("usage-partial", "week-unknown")
SOURCES = ("todo", "backlog", "plan", "agent_filed", "none")
USAGE_KEYS = ("schema", "written_at", "result", "days", "pieces", "status", "sign_in")
OWNER_LABELS = ("agent:needs-human", "agent:blocked")
TICKET_RE = re.compile(r"^[A-Z][A-Z0-9]{0,15}-[0-9]{1,9}\Z")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}(/[A-Za-z0-9_.-]{1,100})?\Z")
REF_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}#[0-9]{1,9}\Z")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}\Z")
REPLY_RE = re.compile(r"^(start|approve) [A-Z][A-Z0-9]{0,15}-[0-9]{1,9}\Z")
LANE_RE = re.compile(r"^(.+):([0-9]{1,3})\Z")
TIME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?Z\Z")

DEFAULTS = {
    "lanes_per_repo": 1,                 # lanes in a repository: sessions and open pull requests
    "lanes_by_repo": {},                 # {"owner/name": n}: a repository's own, never above 3
    "merge_gate_ready": [],              # repositories whose gate tests open PRs together
    "running_cap": 5,                    # sessions at once, across every repository
    "planned_load": 40,                  # the day's estimated work, in dollars at API prices
    "day_stop": 60,                      # today's spend at or over this holds every start
    "piece_stop": 15,                    # a piece in play over this holds every start
    "starts_cap": 20,                    # starts in a day at or over this hold every start
    "agent_filed_per_day": 5,            # agent-filed starts in a day over this hold every start
    "estimates": {"S": 2.0, "M": 4.0, "L": 8.0},    # a piece's estimate, by effort
    "max_usage_age_seconds": 3600,       # a usage file older than this is stale
    "ignore_windows": [],                # usage windows that never hold a start
    "handoff_states": ["Needs Approval"],    # where a ticket's pull request waits for a merge
}


# --------------------------------------------------------------------------- #
# Small pure helpers
# --------------------------------------------------------------------------- #
def epoch(text):
    """A time in the usage file or a verdict (UTC, ending in Z) as seconds since 1970, or None."""
    m = TIME_RE.match(text) if isinstance(text, str) else None
    try:
        return datetime.datetime(*(int(g) for g in m.groups()),
                                 tzinfo=datetime.timezone.utc).timestamp() if m else None
    except (ValueError, OverflowError):
        return None


def local_date(now):
    """The machine's local date, as the usage job and the ledger name a day."""
    return time.strftime("%Y-%m-%d", time.localtime(now))


def dig(obj, *path):
    for key in path:
        obj = obj.get(key) if isinstance(obj, dict) else None
    return obj


def whole(v):
    return isinstance(v, int) and not isinstance(v, bool)


def amount(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def ticket_number(identifier):
    try:
        return int((identifier or "").rsplit("-", 1)[1])
    except (IndexError, ValueError, AttributeError):
        return 0


def lane_cap(cfg, repo):
    return cfg["lanes_by_repo"].get(repo, cfg["lanes_per_repo"])


def lane_id(repo, slot):
    """A lane's id: its repository and its slot number, so it is the same tomorrow as today."""
    return "%s:%d" % (repo, slot)


def config(raw):
    """(cfg, problems): the settings with defaults filled in, and everything wrong with them,
    named by key. Idempotent."""
    if not isinstance(raw, dict):
        return None, ["the config is not an object"]
    cfg = copy.deepcopy(dict((k, raw.get(k, d)) for k, d in DEFAULTS.items()))
    bad = ["%s: not a setting" % repr(k)[:40] for k in sorted(raw, key=repr) if k not in DEFAULTS]

    def need(key, ok, why):
        if not ok:
            bad.append("%s: %s" % (key, why))
    names = lambda v, rx=None: isinstance(v, list) and all(    # noqa: E731
        isinstance(x, str) and x.strip() and (rx is None or rx.match(x)) for x in v)
    for key, low, high in (("lanes_per_repo", 1, MAX_LANES), ("running_cap", 1, None),
                           ("starts_cap", 1, None), ("agent_filed_per_day", 0, None),
                           ("max_usage_age_seconds", 1, None)):
        need(key, whole(cfg[key]) and cfg[key] >= low and (high is None or cfg[key] <= high),
             "must be a whole number from %d%s" % (low, "" if high is None else " to %d" % high))
    for key in ("planned_load", "day_stop", "piece_stop"):
        need(key, amount(cfg[key]) and cfg[key] > 0, "must be a number above 0")
    need("lanes_by_repo", isinstance(cfg["lanes_by_repo"], dict) and all(
        isinstance(k, str) and REPO_RE.match(k) and whole(v) and 1 <= v <= MAX_LANES
        for k, v in cfg["lanes_by_repo"].items()), "must map repositories to 1 to %d" % MAX_LANES)
    need("merge_gate_ready", names(cfg["merge_gate_ready"], REPO_RE), "must list repositories")
    need("estimates", isinstance(cfg["estimates"], dict) and set(cfg["estimates"]) == set(EFFORTS)
         and all(amount(v) and v >= 0 for v in cfg["estimates"].values()),
         "must give S, M and L each a number, 0 or more")
    need("ignore_windows", isinstance(cfg["ignore_windows"], list) and all(
        w in WINDOWS for w in cfg["ignore_windows"]), "must name usage windows")
    need("handoff_states", names(cfg["handoff_states"]) and cfg["handoff_states"],
         "must list state names")
    if not bad:
        need("planned_load", cfg["planned_load"] <= cfg["day_stop"],
             "is above day_stop: the day could never be filled")
    return cfg, bad


def clean(p):
    """One piece, keeping only ids, words, numbers and times of a known shape; None when it
    cannot be named. Nothing else of it is read, so no title or text can reach the result."""
    if not isinstance(p, dict) or not isinstance(p.get("ticket"), str) \
            or not TICKET_RE.match(p["ticket"]):
        return None

    def pick(v, rx):
        return v if isinstance(v, str) and rx.match(v) else None

    def word(v, ok):
        return v if v in ok else None
    blockers, listed = p.get("blockers"), p.get("footprint")
    if isinstance(blockers, list) and all(isinstance(b, dict) and pick(b.get("ticket"), TICKET_RE)
                                          for b in blockers):
        blockers = [{"ticket": b["ticket"], "state": b["state"] if isinstance(b.get("state"), str)
                     else "", "type": b["type"] if isinstance(b.get("type"), str) else "",
                     "labels": set(x.lower() for x in b["labels"] if isinstance(x, str))
                     if isinstance(b.get("labels"), list) else set()} for b in blockers]
    else:
        blockers = None
    at = pick(p.get("at"), TIME_RE)
    prio = lambda v: v if whole(v) and v in PRIORITIES else 0    # noqa: E731
    return {"ticket": p["ticket"], "id": pick(p.get("id"), ID_RE), "admitted": p.get(
        "admitted") is True, "reason": p.get("reason") if isinstance(p.get("reason"), str) else "",
        "reply": pick(p.get("reply"), REPLY_RE), "source": word(p.get("source"), SOURCES),
        "plan": pick(p.get("plan"), TICKET_RE), "by": pick(p.get("by"), ID_RE), "at": at,
        "at_epoch": epoch(at), "repo": pick(p.get("repo"), REPO_RE),
        "priority": prio(p.get("priority")), "plan_priority": prio(p.get("plan_priority")),
        "effort": word(p.get("effort"), EFFORTS), "blockers": blockers,
        "footprint": [x for x in listed if isinstance(x, str) and x.strip()] or None
        if isinstance(listed, list) else None}


# --------------------------------------------------------------------------- #
# The order
# --------------------------------------------------------------------------- #
def dependency_index(pieces):
    """Each piece's place in its plan's dependency order: after the pieces of its plan that block
    it, ties by ticket number, a loop leaving the rest in ticket order. The bridge's own
    `in_order`, over tickets (the selftest pins the two equal)."""
    groups, out = {}, {}
    for p in pieces:
        groups.setdefault(p["plan"] or p["ticket"], []).append(p)
    for group in groups.values():
        ids = set(p["ticket"] for p in group)
        waits = dict((p["ticket"], set(b["ticket"] for b in p["blockers"] or []
                                       if b["ticket"] in ids)) for p in group)
        left, placed, seq = sorted(group, key=lambda p: ticket_number(p["ticket"])), set(), []
        while left:
            ready = [p for p in left if waits[p["ticket"]] <= placed]
            for p in ready[:1] or left:
                seq.append(p)
                placed.add(p["ticket"])
                left.remove(p)
        out.update((p["ticket"], i) for i, p in enumerate(seq))
    return out


def effective_priority(p):
    """A piece's own priority, else its plan's; 0 for none."""
    return p["priority"] or p["plan_priority"]


def sort_key(p, dep):
    rank = effective_priority(p)
    return (rank if rank else len(PRIORITIES) + 1,
            p["at_epoch"] if p["at_epoch"] is not None else math.inf,
            ticket_number(p["plan"] or p["ticket"]), p["plan"] or p["ticket"],
            dep.get(p["ticket"], 0))


# --------------------------------------------------------------------------- #
# Lanes
# --------------------------------------------------------------------------- #
def overlaps(a, b):
    """True when two footprints may share a file: any path of one is, or is under, or holds,
    a path of the other. A glob counts as the folder before its first wildcard."""
    def parts(path):
        path = path.strip()
        head = re.split(r"[*?\[]", path)[0]
        if head != path:                  # a glob: only the folder before its wildcard is sure
            head = head.rsplit("/", 1)[0] if "/" in head else ""
        return tuple(x for x in head.split("/") if x and x != ".")
    return any(parts(p)[:len(parts(q))] == parts(q) or parts(q)[:len(parts(p))] == parts(p)
               for p in a for q in b)


def occupants(cfg, running):
    """repo -> what holds its lanes, each with a slot: the lane it names, if that is a valid free
    one for its repository, else the lowest free."""
    by = {}
    for r in sorted((r for r in running if isinstance(r, dict) and isinstance(r.get("repo"), str)
                     and REPO_RE.match(r["repo"])), key=lambda r: (r["repo"], str(r.get("ref")))):
        ref = r.get("ref") if isinstance(r.get("ref"), str) else ""
        listed = r.get("footprint")
        by.setdefault(r["repo"], []).append({
            "kind": "pr" if r.get("kind") == "pr" else "session",
            "ref": ref if REF_RE.match(ref) or TICKET_RE.match(ref) else r["repo"],
            "effort": r.get("effort") if r.get("effort") in EFFORTS else None,
            "lane": r.get("lane"), "footprint": [x for x in listed if isinstance(x, str)
                                                 and x.strip()] or None
            if isinstance(listed, list) else None})
    for repo, occ in by.items():
        taken = set()
        for o in occ:
            m = LANE_RE.match(o["lane"]) if isinstance(o["lane"], str) else None
            slot = int(m.group(2)) if m and m.group(1) == repo else 0
            o["slot"] = slot if 1 <= slot <= lane_cap(cfg, repo) and slot not in taken else 0
            taken.add(o["slot"])
        for o in occ:
            if not o["slot"]:
                o["slot"] = min(s for s in range(1, len(occ) + 2) if s not in taken)
                taken.add(o["slot"])
    return by


def lane_for(cfg, repo, occ, footprint):
    """The lowest free slot among a repository's lanes for a piece, or None. Where the merge gate
    cannot yet test open pull requests together, a second lane needs every footprint declared
    and none shared: a holder with none declared keeps the repository to one lane."""
    taken = set(o["slot"] for o in occ)
    free = [s for s in range(1, lane_cap(cfg, repo) + 1) if s not in taken]
    if occ and repo not in cfg["merge_gate_ready"] and (footprint is None or any(
            o["footprint"] is None or overlaps(footprint, o["footprint"]) for o in occ)):
        return None
    return free[0] if free else None


# --------------------------------------------------------------------------- #
# The runaway stops: each reads what it is given, and says what it saw
# --------------------------------------------------------------------------- #
def stop(code, ref=None, value=None, limit=None):
    return {"stop": code, "ref": ref, "value": value, "limit": limit}


def shape_ok(u):
    """The usage file as this module reads it: the marker, and the parts it reads, each the
    kind it should be. Anything else is a file it cannot use."""
    return (isinstance(u, dict) and u.get("schema") == SCHEMA and all(k in u for k in USAGE_KEYS)
            and isinstance(u["days"], list) and isinstance(u["pieces"], list)
            and isinstance(dig(u, "status", "windows"), dict)
            and isinstance(dig(u, "sign_in", "api_key_runs"), list))


def stop_usage(cfg, now, c):
    if not shape_ok(c.usage):
        return [stop("usage-missing")]
    if c.usage["result"] == "failed":
        return [stop("usage-failed")]
    at = epoch(c.usage["written_at"])
    if at is None or abs(now - at) > cfg["max_usage_age_seconds"]:
        return [stop("usage-stale", value=None if at is None else int(now - at),
                     limit=cfg["max_usage_age_seconds"])]
    return [] if c.spent is not None else [stop("usage-stale")]       # not about today


def stop_ledger(cfg, now, c):
    return [] if c.ledger else [stop("ledger-unknown")]


def stop_plan(cfg, now, c):
    out = []
    for name, w in sorted((dig(c.usage, "status", "windows") or {}).items()):
        reset = epoch(dig(w, "resets_at"))
        if name in cfg["ignore_windows"] or (reset is not None and reset <= now):
            continue                             # not a window to heed, or one that has reset
        word = dig(w, "status")
        if word == "rejected":
            out.append(stop("plan-at-limit", ref=name))
        elif word != "allowed":                  # a warning, or a word this list does not have
            out.append(stop("plan-near-limit", ref=name))
    return out


def stop_spend(cfg, now, c):
    return [stop("day-hard-stop", value=c.spent, limit=cfg["day_stop"])] if (
        c.spent is not None and c.spent >= cfg["day_stop"]) else []


def stop_piece(cfg, now, c):
    return [stop("piece-cost", ref=p["id"], value=p["usd"], limit=cfg["piece_stop"])
            for p in dig(c.usage, "pieces") or [] if isinstance(p, dict)
            and isinstance(p.get("id"), str) and p["id"] in c.played
            and amount(p.get("usd")) and p["usd"] > cfg["piece_stop"]]


def stop_running(cfg, now, c):
    out = [stop("too-many-running", value=c.sessions, limit=cfg["running_cap"])] if (
        c.sessions > cfg["running_cap"]) else []
    return out + [stop("too-many-running", ref=repo, value=len(occ), limit=lane_cap(cfg, repo))
                  for repo, occ in sorted(c.occ.items()) if len(occ) > lane_cap(cfg, repo)]


def stop_starts(cfg, now, c):
    led = c.ledger or {}
    out = [stop("too-many-starts", value=led["starts"], limit=cfg["starts_cap"])] if (
        led and led["starts"] >= cfg["starts_cap"]) else []
    return out + ([stop("too-many-starts", ref="agent_filed", value=led["agent_filed_starts"],
                        limit=cfg["agent_filed_per_day"])]
                  if led and led["agent_filed_starts"] > cfg["agent_filed_per_day"] else [])


def stop_key(cfg, now, c):
    runs = dig(c.usage, "sign_in", "api_key_runs")
    return [stop("api-key", value=len(runs))] if runs else []


STOP_CHECKS = (stop_usage, stop_ledger, stop_plan, stop_spend, stop_piece, stop_running,
               stop_starts, stop_key)


def spent_today(u, today):
    """Today's spend from the usage file: its day entry for today's date, or None."""
    days = [d for d in (u["days"] if shape_ok(u) else []) if isinstance(d, dict)
            and d.get("date") == today and amount(d.get("usd")) and d["usd"] >= 0]
    return days[-1]["usd"] if days else None


def read_ledger(ledger, today):
    ok = isinstance(ledger, dict) and ledger.get("date") == today and all(
        whole(ledger.get(k)) and ledger[k] >= 0 for k in ("starts", "agent_filed_starts"))
    return ledger if ok else None


# --------------------------------------------------------------------------- #
# What only the owner can do
# --------------------------------------------------------------------------- #
def causes_of(cfg, p, by, memo, seen=()):
    """What a piece waits on that only the owner can do, as (kind, ref, reply): its own reply when
    the pool held it, what a blocker waits on when the blocker is a piece, and for any other open
    blocker a merge (it is in a hand-off state) or a question (a label says it needs a person)."""
    if p["ticket"] in memo:
        return memo[p["ticket"]]
    out = set()
    if not p["admitted"] and p["reply"]:
        verb, ref = p["reply"].split()
        kind = "plan" if verb == "approve" else (
            "changed" if p["reason"] == "changed" else (
                "agent_filed" if p["source"] == "agent_filed" else "approval"))
        out.add((kind, ref if verb == "approve" else p["ticket"], p["reply"]))
    for b in p["blockers"] or []:
        if b["type"] == "completed" or b["ticket"] in seen:
            continue
        if b["ticket"] in by:
            out |= causes_of(cfg, by[b["ticket"]], by, memo, seen + (p["ticket"],))
        elif b["state"] in cfg["handoff_states"]:
            out.add(("merge", b["ticket"], None))
        elif b["labels"] & set(OWNER_LABELS) or any(x.startswith("blocked:") for x in b["labels"]):
            out.add(("question", b["ticket"], None))
    memo[p["ticket"]] = out
    return out


# --------------------------------------------------------------------------- #
# The decision
# --------------------------------------------------------------------------- #
def order(cfg, now, pieces, running, usage, ledger):
    """What may start now. See the module's docstring for what goes in and what comes out."""
    cfg, bad = config(cfg)
    if bad:
        raise ValueError("the queue's config is not usable: " + "; ".join(bad))
    today = local_date(now)
    cleaned = [clean(p) for p in pieces]
    ps = [p for p in cleaned if p]
    occ = occupants(cfg, running)
    unread = len([r for r in running if not (isinstance(r, dict) and isinstance(r.get("repo"), str)
                                             and REPO_RE.match(r["repo"]))])
    sessions = sum(o["kind"] == "session" for repo in occ.values() for o in repo) + unread
    c = types.SimpleNamespace(      # a usage file that is not one is None to every check
        usage=usage if shape_ok(usage) else None, ledger=read_ledger(ledger, today), occ=occ,
        sessions=sessions, spent=spent_today(usage, today),
        played=set(p["ticket"] for p in ps) | set(o["ref"] for r in occ.values() for o in r))
    stops = [s for check in STOP_CHECKS for s in check(cfg, now, c)]
    notes = []
    if shape_ok(usage):
        if usage["result"] == "partial":
            notes.append("usage-partial")
        if dig(usage, "week", "known") is False:
            notes.append("week-unknown")
    dep, by = dependency_index(ps), dict((p["ticket"], p) for p in ps)
    queue = sorted((p for p in ps if p["admitted"] or p["reply"]), key=lambda p: sort_key(p, dep))
    # The estimate of what runs now counts toward the day: a session's cost is not in the usage
    # file until it ends. One that names no effort counts at the largest.
    worst = max(cfg["estimates"].values())
    committed = (c.spent or 0) + worst * unread + sum(
        cfg["estimates"].get(o["effort"], worst) for r in occ.values() for o in r
        if o["kind"] == "session")
    start, held, waits, running_now = [], [], {}, sessions
    for p in [q for q in queue if q["admitted"]]:
        est, why, on = cfg["estimates"].get(p["effort"]), None, []
        open_ = [b["ticket"] for b in p["blockers"] or [] if b["type"] != "completed"]
        slot = None
        if p["repo"] is None:
            why = "no-repo"
        elif est is None:
            why = "no-estimate"
        elif p["blockers"] is None:
            why = "blockers-unreadable"
        elif open_:
            why, on = "blocked", open_
        elif stops:
            why = "stopped"
        else:
            slot = lane_for(cfg, p["repo"], occ.get(p["repo"], []), p["footprint"])
            if slot is None:
                why = "no-lane"          # the owner's merge frees a lane a pull request holds
                prs = sorted(o["ref"] for o in occ.get(p["repo"], []) if o["kind"] == "pr")
                full = len(occ.get(p["repo"], [])) >= lane_cap(cfg, p["repo"])
                waits[p["ticket"]] = set([("merge", prs[0], None)]) if prs and full else set()
            elif running_now >= cfg["running_cap"]:
                why = "running-cap"
            elif (c.ledger or {"starts": 0})["starts"] + len(start) >= cfg["starts_cap"]:
                why = "starts-cap"
            elif committed + est > cfg["planned_load"]:
                why = "over-load"
        if why:
            held.append({"ticket": p["ticket"], "reason": why, "waits_on": on})
            continue
        occ.setdefault(p["repo"], []).append({"kind": "session", "ref": p["ticket"], "slot": slot,
                                              "effort": p["effort"], "lane": None,
                                              "footprint": p["footprint"]})
        committed, running_now = committed + est, running_now + 1
        start.append({"ticket": p["ticket"], "id": p["id"], "repo": p["repo"],
                      "lane": lane_id(p["repo"], slot), "slot": slot, "estimate": est,
                      "priority": effective_priority(p), "plan": p["plan"], "source": p["source"],
                      "by": p["by"], "at": p["at"]})
    memo, items = {}, {}
    for p in queue:
        for cause in causes_of(cfg, p, by, memo) | waits.get(p["ticket"], set()):
            it = items.setdefault(cause, {"kind": cause[0], "ref": cause[1], "pieces": 0,
                                          "usd": 0.0, "reply": cause[2], "urgent": False})
            it["pieces"] += 1
            it["usd"] = round(it["usd"] + (cfg["estimates"].get(p["effort"]) or 0), 2)
    if queue and effective_priority(queue[0]) in URGENT:
        for cause in causes_of(cfg, queue[0], by, memo) | waits.get(queue[0]["ticket"], set()):
            items[cause]["urgent"] = True
    held += [{"ticket": None, "reason": "malformed", "waits_on": []} for _ in cleaned if not _]
    return {"start": start, "held": held, "stops": stops, "notes": notes,
            "needs_ok": sorted(items.values(), key=lambda i: (
                not i["urgent"], -i["usd"], i["kind"], i["ref"])),
            "order": [p["ticket"] for p in queue],
            "load": {"spent": c.spent, "committed": round(committed, 2),
                     "planned": cfg["planned_load"]}}


def main(argv=None):
    p = argparse.ArgumentParser(prog="pipeline_queue_order.py", description=__doc__.split("\n")[0])
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
    import itertools
    import json
    import os
    import socket
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import check_schemas                            # the usage file's own schema and example
    import pipeline_bridge_plans as bridge          # what the dependency order is pinned to

    LEAK = "SENTINEL-LEAK-CHECK-9b2d"                # text no result may carry
    T1, T2 = "2026-11-02T10:00:00Z", "2026-11-03T10:00:00Z"
    NOW = epoch("2026-11-04T14:00:00Z")
    NODATA, R = object(), "acme/r"

    def stamp(delta, now=NOW):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + delta))

    def usage(now=NOW, spent=10.0, **over):
        """The schema's own valid example, made current: today's spend, nothing near a limit."""
        doc = copy.deepcopy(check_schemas.VALID_USAGE)
        doc.update(written_at=stamp(-300, now), started_at=stamp(-330, now), result="ok")
        doc["days"][0]["date"] = local_date(now - 86400)
        doc["days"][-1].update(date=local_date(now), usd=spent)
        for name, ahead in (("five_hour", 3600), ("seven_day", 86400)):
            doc["status"]["windows"][name].update(status="allowed", surpassed=None,
                                                  resets_at=stamp(ahead, now))
        doc["sign_in"] = {"counts": {"none": 150}, "api_key_runs": []}
        doc.update(over)
        return doc

    def tweak(*path, value):
        doc = node = usage()
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
        return doc

    def warn(reset, word="allowed_warning"):
        doc = usage()
        doc["status"]["windows"]["seven_day"].update(status=word, resets_at=reset)
        return doc

    def costly(usd, ident="ENG-1"):
        return usage(pieces=[{"id": ident, "kind": "coding", "usd": usd, "runs": 3,
                              "last_at": T2}])

    def ledger(now=NOW, starts=3, agent=1):
        return {"date": local_date(now), "starts": starts, "agent_filed_starts": agent}

    def piece(n, repo=None, prio=0, plan=None, plan_prio=0, at=T1, effort="S", blockers=(),
              admitted=True, **kw):
        p = {"ticket": "ENG-%d" % n, "id": "id-%d" % n, "admitted": admitted,
             "reason": "admitted" if admitted else "not-owner", "reply": None, "source": "todo",
             "plan": plan, "by": "owner-1", "at": at, "repo": repo or "acme/r%d" % n,
             "priority": prio, "plan_priority": plan_prio, "effort": effort,
             "blockers": None if blockers is None else list(blockers), "footprint": None,
             "title": "Fix " + LEAK,
             "description": LEAK}
        p.update(kw)
        return p

    def blocker(n, state="Todo", typ="unstarted", labels=()):
        return {"ticket": "ENG-%d" % n, "state": state, "type": typ, "labels": list(labels)}

    def sess(ref, lane=None, repo=R, **kw):
        return dict({"kind": "session", "repo": repo, "ref": ref, "lane": lane, "effort": "S"},
                    **kw)

    def go(pieces, running=(), cfg=None, u=None, led=None, now=NOW):
        def read(x, dflt):
            return dflt if x is None else (None if x is NODATA else x)
        return order(dict(cfg or {}), now, pieces, list(running), read(u, usage(now)),
                     read(led, ledger(now)))

    ST = lambda r: [s["ticket"] for s in r["start"]]                       # noqa: E731
    LN = lambda r: [(s["ticket"], s["lane"]) for s in r["start"]]          # noqa: E731
    HD = lambda r: [(h["ticket"], h["reason"]) for h in r["held"]]         # noqa: E731
    SP = lambda r: [s["stop"] for s in r["stops"]]                         # noqa: E731
    OD = lambda r: r["order"]                                              # noqa: E731
    OK = lambda r: [(i["kind"], i["ref"], i["pieces"], i["usd"], i["reply"],       # noqa: E731
                     i["urgent"]) for i in r["needs_ok"]]
    SC, RAW = [], []

    def S(name, want, proj, *args, **kw):
        def thunk():
            r = go(*args, **kw)
            RAW.append(r)
            return proj(r)
        SC.append((name, want, thunk))

    P, ONE, NINE = piece, [piece(1)], [blocker(9)]
    # -- the order ----------------------------------------------------------------------------
    prios = [P(1, prio=4), P(2, prio=1), P(3, prio=2), P(4, prio=3), P(5)]
    by_priority = ["ENG-2", "ENG-3", "ENG-4", "ENG-1", "ENG-5"]
    S("priority: Urgent, High, Medium, Low, then none: what starts", by_priority, ST, prios)
    S("…and the queue", by_priority, OD, prios)
    S("a piece's own priority, else its plan's", ["ENG-3", "ENG-2", "ENG-1"], OD,
      [P(1, prio=4, plan_prio=1, plan="ENG-50"), P(2, prio=2, plan="ENG-50"),
       P(3, plan_prio=1, plan="ENG-50")])
    S("then the plan approved first", ["ENG-2", "ENG-1"], OD,
      [P(1, plan="ENG-50", at=T2), P(2, plan="ENG-60", at=T1)])
    S("then a plan's pieces together, after what blocks them", ["ENG-3", "ENG-5", "ENG-9", "ENG-2",
                                                                "ENG-4"], OD,
      [P(9, plan="ENG-50"), P(2, plan="ENG-50", blockers=NINE), P(5, plan="ENG-50"),
       P(3, plan="ENG-50"), P(4, plan="ENG-60")])
    S("then ticket number, as a number", ["ENG-9", "ENG-10"], OD, [P(10), P(9)])
    held_pool = [P(1), P(2, admitted=False), P(3, admitted=False, reply="start ENG-3")]
    S("a piece the pool held with a reply is queued, never started: one without is neither",
      ["ENG-1", "ENG-3"], OD, held_pool)
    S("…started", ["ENG-1"], ST, held_pool)
    # -- blockers -------------------------------------------------------------------------------
    S("an open blocker holds a piece, and says what it waits on", [("ENG-1", "blocked")], HD,
      [P(1, blockers=NINE)])
    S("…the blocker named", ["ENG-9"], lambda r: [w for h in r["held"] for w in h["waits_on"]],
      [P(1, blockers=NINE)])
    S("a completed blocker lets it go", ["ENG-1"], ST,
      [P(1, blockers=[blocker(9, "Done", "completed")])])
    S("a canceled one does not", [("ENG-1", "blocked")], HD,
      [P(1, blockers=[blocker(9, "Canceled", "canceled")])])
    S("blockers that could not all be read", [("ENG-1", "blockers-unreadable")], HD,
      [P(1, blockers=None)])
    S("a blocker the pool held", [("ENG-1", "blocked")], HD,
      [P(1, blockers=[blocker(2)]), P(2, admitted=False, reply="start ENG-2")])
    S("a loop of blockers holds both, and goes round no more than once",
      ([("ENG-1", "blocked"), ("ENG-2", "blocked")], []), lambda r: (HD(r), OK(r)),
      [P(1, blockers=[blocker(2)]), P(2, blockers=[blocker(1)])])
    # -- what only the owner can do -------------------------------------------------------------
    handoff = [blocker(7, "Needs Approval", "started")]
    S("the top of the queue, Urgent, waits on a merge", [("merge", "ENG-7", 1, 2.0, None, True)],
      OK, [P(1, prio=1, blockers=handoff)])
    S("…at Medium it is listed, and does not page", [("merge", "ENG-7", 1, 2.0, None, False)],
      OK, [P(1, prio=3, blockers=handoff)])
    S("a blocker that needs a person", [("question", "ENG-7", 1, 2.0, None, True)], OK,
      [P(1, prio=2, blockers=[blocker(7, "In Progress", "started", ["agent:needs-human"])])])
    S("a blocker at work needs no one", [], OK,
      [P(1, prio=1, blockers=[blocker(7, "In Progress", "started")])])
    plan_piece = dict(admitted=False, reason="plan-not-approved", reply="approve ENG-10",
                      source="plan", plan="ENG-10", plan_prio=1)
    S("a plan to approve: its pieces, and what they are estimated at",
      [("plan", "ENG-10", 3, 14.0, "approve ENG-10", True)], OK,
      [P(11, effort="S", **plan_piece), P(12, effort="M", **plan_piece),
       P(13, effort="L", **plan_piece)])
    S("a ticket someone else changed", [("changed", "ENG-1", 1, 2.0, "start ENG-1", False)], OK,
      [P(1, admitted=False, reason="changed", reply="start ENG-1")])
    S("an agent-filed ticket over the limits, and what waits on it",
      [("agent_filed", "ENG-2", 2, 4.0, "start ENG-2", False)], OK,
      [P(2, admitted=False, reason="not-small", reply="start ENG-2", source="agent_filed"),
       P(1, blockers=[blocker(2)])])
    S("a ticket that needs the owner's yes", [("approval", "ENG-1", 1, 2.0, "start ENG-1", False)],
      OK, [P(1, admitted=False, reason="not-owner", reply="start ENG-1")])
    S("only what the top of the queue waits on pages", [("merge", "ENG-7", 1, 2.0, None, False)],
      OK, [P(1, prio=1), P(2, prio=3, blockers=handoff)])
    S("a pull request holds a lane: its merge is the owner's",
      [("merge", "acme/r#5", 1, 2.0, None, True)], OK, [P(1, repo=R, prio=2)],
      running=[{"kind": "pr", "repo": R, "ref": "acme/r#5"}])
    # -- lanes ----------------------------------------------------------------------------------
    TWO, NOGATE = {"lanes_per_repo": 2, "merge_gate_ready": [R]}, {"lanes_per_repo": 2}
    pair = [P(1, repo=R), P(2, repo=R)]
    S("one lane in a repository", [("ENG-1", "acme/r:1")], LN, pair)
    S("…the second piece waits for it", [("ENG-2", "no-lane")], HD, pair)
    S("two lanes where the merge gate is ready", [("ENG-1", "acme/r:1"), ("ENG-2", "acme/r:2")],
      LN, pair, cfg=TWO)
    S("…not ready: one lane, with no footprints", ["ENG-1"], ST, pair, cfg=NOGATE)
    S("…two on declared footprints that share nothing",
      [("ENG-1", "acme/r:1"), ("ENG-2", "acme/r:2")], LN,
      [P(1, repo=R, footprint=["src/a/"]), P(2, repo=R, footprint=["src/b/"])], cfg=NOGATE)
    S("…not on footprints that may share a file", ["ENG-1"], ST,
      [P(1, repo=R, footprint=["src/"]), P(2, repo=R, footprint=["src/b/x.py"])], cfg=NOGATE)
    S("…nor when only one declares", ["ENG-1"], ST,
      [P(1, repo=R, footprint=["src/a/"]), P(2, repo=R)], cfg=NOGATE)
    S("…nor beside a holder that declared none", [("ENG-1", "no-lane")], HD,
      [P(1, repo=R, footprint=["src/a/"])], cfg=NOGATE, running=[sess("ENG-9")])
    THREE = {"lanes_per_repo": 3, "merge_gate_ready": [R]}
    one = [P(1, repo=R)]
    S("a holder keeps its lane: the next piece takes a free one", [("ENG-1", "acme/r:1")], LN, one,
      cfg=THREE, running=[sess("ENG-9", "acme/r:2")])
    S("…and the other way", [("ENG-1", "acme/r:2")], LN, one, cfg=THREE,
      running=[sess("ENG-9", "acme/r:1")])
    S("a holder with no lane takes the lowest", [("ENG-1", "acme/r:2")], LN, one, cfg=THREE,
      running=[sess("ENG-9")])
    S("a lane named for another repository is not kept", [("ENG-1", "acme/r:2")], LN, one,
      cfg=THREE, running=[sess("ENG-9", "acme/x:1")])
    S("a pull request holds a lane while a second is free", [("ENG-1", "acme/r:2")], LN, one,
      cfg=TWO, running=[{"kind": "pr", "repo": R, "ref": "acme/r#5"}])
    S("a repository's own cap", [("ENG-1", "acme/a:1"), ("ENG-2", "acme/a:2"),
                                 ("ENG-3", "acme/b:1")], LN,
      [P(1, repo="acme/a"), P(2, repo="acme/a"), P(3, repo="acme/b"), P(4, repo="acme/b")],
      cfg={"lanes_by_repo": {"acme/a": 2}, "merge_gate_ready": ["acme/a", "acme/b"]})
    five = [sess("ENG-9%d" % i, None, "acme/x%d" % i) for i in range(5)]
    S("five sessions running: the next piece waits", [("ENG-1", "running-cap")], HD, ONE,
      running=five)
    S("…running entries it cannot read still count", [("ENG-1", "running-cap")], HD, ONE,
      running=["x", None, {"repo": 5}, {"kind": "pr"}, []])
    S("…and that is not a stop", [], SP, ONE, running=five)
    S("a lane's id is the same on another day", True,
      lambda r: LN(go(pair, cfg=TWO, now=NOW + 86400)) == LN(r), pair, cfg=TWO)
    # -- the day's load ---------------------------------------------------------------------------
    four = [P(1, effort="S"), P(2, effort="M"), P(3, effort="L"), P(4, effort="S")]
    busy30, big = usage(spent=30.0), [sess("ENG-90", None, "acme/x", effort="L")]
    S("the day's load: what does not fit waits, the rest goes on", ["ENG-1", "ENG-2", "ENG-4"], ST,
      four, u=busy30)
    S("…the one held says so", [("ENG-3", "over-load")], HD, four, u=busy30)
    S("…and a hold under the load is not a stop", [], SP, four, u=busy30)
    S("a session running counts at its estimate", ["ENG-1"], ST, ONE, running=big, u=busy30)
    S("…so a piece past it waits", [("ENG-1", "over-load")], HD, [P(1, effort="M")], running=big,
      u=busy30)
    S("…one that names no effort counts at the largest", [("ENG-1", "over-load")], HD,
      [P(1, effort="M")], running=[sess("ENG-90", None, "acme/x", effort=None)], u=busy30)
    S("a piece with no effort cannot be sized", [("ENG-1", "no-estimate")], HD,
      [P(1, effort=None)])
    S("a piece with no repository cannot be placed", [("ENG-1", "no-repo")], HD,
      [P(1, repo="a b")])
    S("a piece it cannot name is held, and never dropped", [(None, "malformed")] * 2, HD,
      [{"ticket": "nope"}, "junk"])
    S("fields of no shape are read as none", [("ENG-1", "no-repo")], HD,
      [{"ticket": "ENG-1", "admitted": True, "repo": ["x"], "blockers": "x", "effort": "XL",
        "priority": True, "footprint": 5}])
    # -- starts in a day --------------------------------------------------------------------------
    two = [P(1), P(2)]
    S("the day's starts: the last one under the cap goes", ["ENG-1"], ST, two,
      led=ledger(starts=19))
    S("…the other waits", [("ENG-2", "starts-cap")], HD, two, led=ledger(starts=19))
    S("starts at the cap are a stop that holds everything", (["too-many-starts"], [("ENG-1",
                                                                                  "stopped")]),
      lambda r: (SP(r), HD(r)), ONE, led=ledger(starts=20))
    S("agent-filed starts over their cap are a stop", ["too-many-starts"], SP, ONE,
      led=ledger(agent=6))
    S("…at it they are not", [], SP, ONE, led=ledger(agent=5))
    # -- the runaway stops ------------------------------------------------------------------------
    def stops_case(name, want, **kw):
        S(name, want, SP, ONE, **kw)
    stops_case("no usage file", ["usage-missing"], u=NODATA)
    stops_case("a file of another kind", ["usage-missing"], u=usage(schema="pipeline-usage/2"))
    stops_case("a file missing a part", ["usage-missing"],
               u=dict((k, v) for k, v in usage().items() if k != "sign_in"))
    stops_case("a file whose parts are the wrong kind", ["usage-missing"],
               u=usage(status={"windows": [], "events_per_day": 1}))
    stops_case("a failed file", ["usage-failed"], u=usage(result="failed"))
    stops_case("a stale file", ["usage-stale"], u=usage(written_at=stamp(-3601)))
    stops_case("…an hour old to the second is not", [], u=usage(written_at=stamp(-3600)))
    stops_case("…one from the future is no fresher", ["usage-stale"],
               u=usage(written_at=stamp(7200)))
    stops_case("a file about another day", ["usage-stale"],
               u=tweak("days", -1, "date", value=local_date(NOW - 86400)))
    stops_case("the plan near its limit", ["plan-near-limit"], u=warn(stamp(86400)))
    stops_case("…a warning whose reset has passed is over", [], u=warn(stamp(-60)))
    stops_case("…one that names no reset still holds", ["plan-near-limit"], u=warn(None))
    stops_case("…at its limit", ["plan-at-limit"], u=warn(stamp(86400), "rejected"))
    stops_case("…a word the list lacks", ["plan-near-limit"], u=warn(stamp(86400), "other"))
    stops_case("…a window the config ignores", [], u=warn(stamp(86400)),
               cfg={"ignore_windows": ["seven_day"]})
    stops_case("a day's spend at the hard stop", ["day-hard-stop"], u=usage(spent=60.0))
    stops_case("…just under it", [], u=usage(spent=59.99))
    stops_case("…a higher stop from the config", [], u=usage(spent=65.0), cfg={"day_stop": 70})
    stops_case("a piece in play over its cost", ["piece-cost"], u=costly(15.01))
    stops_case("…exactly at it is not over", [], u=costly(15.0))
    stops_case("…a piece not in play is history", [], u=costly(99.0, "ENG-301"))
    stops_case("…a higher stop from the config", [], u=costly(15.01), cfg={"piece_stop": 20})
    six = [sess("ENG-9%d" % i, None, "acme/x%d" % i) for i in range(6)]
    pair_held = [sess("ENG-91"), {"kind": "pr", "repo": R, "ref": "acme/r#5"}]
    stops_case("more sessions than the cap", ["too-many-running"], running=six)
    stops_case("…a larger cap from the config", [], running=six, cfg={"running_cap": 6})
    stops_case("more in a repository than its lanes", ["too-many-running"], running=pair_held)
    stops_case("…more lanes from the config", [], running=pair_held, cfg={"lanes_per_repo": 2})
    stops_case("a run signed in with an API key", ["api-key"],
               u=tweak("sign_in", "api_key_runs", value=[{"id": "ENG-307", "at": T2}]))
    stops_case("today's counts unknown", ["ledger-unknown"], led=NODATA)
    stops_case("…a ledger of another day", ["ledger-unknown"],
               led=dict(ledger(), date="1999-01-01"))
    stops_case("…counts that are not counts", ["ledger-unknown"],
               led={"date": local_date(NOW), "starts": "3", "agent_filed_starts": 1})
    S("every stop holds every start, and a blocker is still said", [("ENG-1", "stopped"),
                                                                    ("ENG-2", "blocked")], HD,
      [P(1), P(2, blockers=NINE)], u=NODATA)
    S("…and nothing starts", [], ST, ONE, u=NODATA)
    S("a partial file is said, not a stop", (["usage-partial"], []),
      lambda r: (r["notes"], SP(r)), ONE, u=usage(result="partial"))
    S("an unknown week is said", ["week-unknown"], lambda r: r["notes"], ONE,
      u=tweak("week", "known", value=False))
    # -- every number is the config's -------------------------------------------------------------
    S("planned_load is the config's", ["ENG-1", "ENG-2", "ENG-3", "ENG-4"], ST, four, u=busy30,
      cfg={"planned_load": 50})
    S("starts_cap is the config's", ["ENG-1", "ENG-2"], ST, two, led=ledger(starts=19),
      cfg={"starts_cap": 25})
    S("the estimates are the config's", [("ENG-1", "over-load")], HD, ONE, u=busy30,
      cfg={"estimates": {"S": 20, "M": 4, "L": 8}})
    S("the usage file's age limit is the config's", [], SP, ONE, u=usage(written_at=stamp(-7200)),
      cfg={"max_usage_age_seconds": 7200})
    ready = [P(1, prio=1, blockers=[blocker(7, "Ready to merge", "started")])]
    S("the hand-off states are the config's", [("merge", "ENG-7", 1, 2.0, None, True)], OK, ready,
      cfg={"handoff_states": ["Ready to merge"]})
    S("…the default does not name them", [], OK, ready)

    def battery():
        return [thunk() for _n, _w, thunk in SC]
    got = battery()
    for (name, want, _t), g in zip(SC, got):
        check(name, g, want)
    results = list(RAW[:len(SC)])
    check("the defaults are the brief's: 1 lane, 5 running, a load of 40, stops of 60, 15 and 20",
          (DEFAULTS["lanes_per_repo"], DEFAULTS["running_cap"], DEFAULTS["planned_load"],
           DEFAULTS["day_stop"], DEFAULTS["piece_stop"], DEFAULTS["starts_cap"]),
          (1, 5, 40, 60, 15, 20))
    check("the usage fixtures are documents the usage file's own schema accepts",
          check_schemas.document_problems(usage(), "pipeline-usage"), [])
    # -- the shape of what comes out --------------------------------------------------------------
    check("every reason, stop, kind and note is on its closed list, and every one is reached",
          (sorted(set(h["reason"] for r in results for h in r["held"])),
           sorted(set(s["stop"] for r in results for s in r["stops"])),
           sorted(set(i["kind"] for r in results for i in r["needs_ok"])),
           sorted(set(n for r in results for n in r["notes"]))),
          (sorted(HELD), sorted(STOPS), sorted(KINDS), sorted(NOTES)))
    check("every start is fields only, with the documented keys",
          set(tuple(sorted(s)) for r in results for s in r["start"]),
          set([tuple(sorted(("ticket", "id", "repo", "lane", "slot", "estimate", "priority",
                             "plan", "source", "by", "at")))]))
    check("no title or text reaches a result (the sentinel)", LEAK in json.dumps(results), False)
    # -- pinned to the bridge's own order, and what it never reads -------------------------------
    links = ((2, [9]), (3, []), (5, []), (9, []), (10, [11]), (11, [10]), (1, [3]))
    kids = [{"id": "id-%d" % n, "identifier": "ENG-%d" % n, "inverseRelations": {"nodes": [
        {"type": "blocks", "issue": {"id": "id-%d" % b, "identifier": "ENG-%d" % b}}
        for b in on]}} for n, on in links]
    mine = dependency_index([clean(P(n, plan="ENG-50", blockers=[blocker(b) for b in on]))
                             for n, on in links])
    check("a plan's dependency order is the bridge's own, a loop included",
          sorted(mine, key=mine.get), [k["identifier"] for k in bridge.in_order(kids)])
    source = inspect.getsource(sys.modules[__name__]).split("\ndef selftest():")[0]
    check("no file, socket, process or environment word is in the source",
          [w for w in ("import socket", "urllib", "http.client", "subprocess", "shutil",
                       "os.system", "os.popen", "os.environ", "getenv", "os.fork", "os.exec",
                       "os.spawn", "os.listdir", "os.scandir", "os.stat", "os.path", "import glob",
                       ".write(", "os.replace(", "os.unlink(", "os.remove(", "makedirs")
           if w in source] + re.findall(r"open\([^)]*[\"'][wax+]", source), [])
    tree = ast.parse(source)
    deciding = ("order", "occupants", "lane_for", "causes_of", "stop_usage", "stop_ledger",
                "stop_plan", "stop_spend", "stop_piece", "stop_running", "stop_starts", "stop_key")
    check("no number above 2 sits in a function that decides: each limit is the config's",
          sorted(set((fn.name, n.value) for fn in ast.walk(tree) if isinstance(
              fn, ast.FunctionDef) and fn.name in deciding for n in ast.walk(fn)
                     if isinstance(n, ast.Constant) and whole(n.value) and n.value > 2)), [])

    class Watch(object):
        """Counts every file, process, environment and socket touch while it is open."""
        def __enter__(self):
            self.hits, self.saved = [], []
            for owner, name in ((builtins, "open"), (os, "stat"), (os, "lstat"), (os, "listdir"),
                                (os, "scandir"), (os, "getenv"), (socket.socket, "connect"),
                                (subprocess.Popen, "__init__")):
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
    check("no command is a usage error: this part has none but --selftest", code, EXIT_USAGE)

    # -- the config -------------------------------------------------------------------------------
    good, why = config({})
    check("the defaults are a sound config, and config is idempotent", (why, config(good)),
          ([], (good, [])))
    check("three lanes are the most a config may give", config({"lanes_per_repo": 3})[1], [])
    for name, over in (("lanes above the ceiling", {"lanes_per_repo": 4}),
                       ("no lanes", {"lanes_per_repo": 0}),
                       ("a repository's lanes above the ceiling", {"lanes_by_repo": {"a/b": 4}}),
                       ("a running cap of 0", {"running_cap": 0}),
                       ("a load above the hard stop", {"planned_load": 61}),
                       ("a stop that is text", {"day_stop": "60"}),
                       ("estimates for the wrong efforts", {"estimates": {"S": 1, "M": 2}}),
                       ("a negative estimate", {"estimates": {"S": -1, "M": 2, "L": 3}}),
                       ("a window that is not one", {"ignore_windows": ["monthly"]}),
                       ("a setting that is not one", {"lane_cap": 2}),
                       ("a flag where a number goes", {"starts_cap": True}),
                       ("no hand-off states", {"handoff_states": []}),
                       ("a number that is not one", {"piece_stop": float("nan")})):
        check("a bad config is refused: %s" % name, bool(config(over)[1]), True)
    check("a config that is not an object is refused", config("x"),
          (None, ["the config is not an object"]))
    try:
        order({"day_stop": "x"}, NOW, [], [], usage(), ledger())
        refused = False
    except ValueError:
        refused = True
    check("order refuses to run on a bad config", refused, True)

    # -- mutants: a check that cannot fail checks nothing -----------------------------------------
    o = dict((k, globals()[k]) for k in (
        "effective_priority", "sort_key", "dependency_index", "lane_for", "occupants", "overlaps",
        "STOP_CHECKS", "causes_of", "lane_id", "order", "URGENT"))

    def caught(**patched):
        saved = dict((k, globals()[k]) for k in patched)
        globals().update(patched)
        try:
            return battery() != got
        except (IndexError, KeyError, TypeError, ValueError):
            return True                  # a mutant the scenarios cannot even read is caught too
        finally:
            globals().update(saved)

    def feed(change):
        """order(), with its pieces changed first: a rule about them, broken."""
        return lambda cfg, now, pieces, *rest: o["order"](
            cfg, now, [change(p) if isinstance(p, dict) else p for p in pieces], *rest)

    def blockers(fn):
        return lambda p: dict(p, blockers=fn(p["blockers"])) if isinstance(
            p.get("blockers"), list) else p

    def holders(change):
        """occupants(), with what is running changed first: a rule about it, broken."""
        return lambda cfg, running: o["occupants"](
            cfg, [change(r) for r in running if isinstance(r, dict)])
    counter = itertools.count()
    for name, patch in (
            ("priority ignored", dict(effective_priority=lambda p: 0)),
            ("a plan's priority ignored", dict(effective_priority=lambda p: p["priority"])),
            ("the plan's approval time ignored", dict(sort_key=lambda p, dep: o["sort_key"](
                dict(p, at_epoch=None), dep))),
            ("the dependency order ignored", dict(dependency_index=lambda ps: {})),
            ("blockers ignored", dict(order=feed(blockers(lambda bl: [])))),
            ("a canceled blocker taken for done", dict(order=feed(blockers(lambda bl: [
                dict(b, type="completed" if b.get("type") == "canceled" else b.get("type"))
                for b in bl])))),
            ("blockers that could not be read taken for none", dict(order=feed(
                lambda p: dict(p, blockers=[]) if p.get("blockers") is None else p))),
            ("the lane cap ignored", dict(lane_for=lambda cfg, repo, occ, fp: 1 + len(occ))),
            ("a pull request holding no lane", dict(occupants=lambda cfg, running: o["occupants"](
                cfg, [r for r in running if isinstance(r, dict) and r.get("kind") != "pr"]))),
            ("footprints never shared", dict(overlaps=lambda a, b: False)),
            ("every merge gate ready", dict(lane_for=lambda cfg, repo, occ, fp: o["lane_for"](
                dict(cfg, merge_gate_ready=[repo]), repo, occ, fp))),
            ("a named lane never kept", dict(occupants=holders(lambda r: dict(r, lane=None)))),
            ("ticket number ignored among equals", dict(sort_key=lambda p, dep: (
                lambda k: k[:2] + k[3:])(o["sort_key"](p, dep)))),
            ("a session with no effort counted at the smallest", dict(
                occupants=holders(lambda r: dict(r, effort=r.get("effort") or "S")))),
            ("the lane id changing", dict(lane_id=lambda repo, slot: "%s:%d:%d" % (
                repo, slot, next(counter)))),
            ("the running cap ignored", dict(order=lambda cfg, *r: o["order"](
                dict(cfg, running_cap=999), *r))),
            ("the day's load ignored", dict(order=lambda cfg, *r: o["order"](
                dict(cfg, planned_load=cfg.get("day_stop", DEFAULTS["day_stop"])), *r))),
            ("the day's starts cap ignored", dict(order=lambda cfg, *r: o["order"](
                dict(cfg, starts_cap=999), *r))),
            ("estimates of nothing", dict(order=lambda cfg, *r: o["order"](
                dict(cfg, estimates={"S": 0, "M": 0, "L": 0}), *r))),
            ("running sessions not counted", dict(order=lambda cfg, now, ps, running, *r: o[
                "order"](cfg, now, ps, [dict(x, kind="pr") if isinstance(x, dict) else x
                                        for x in running], *r))),
            ("urgency ignored", dict(URGENT=())),
            ("what a blocker waits on not followed", dict(
                causes_of=lambda cfg, p, by, memo, seen=(): set()))):
        check("mutant: %s is caught" % name, caught(**patch), True)
    for check_fn in o["STOP_CHECKS"]:
        off = tuple((lambda cfg, now, c: []) if f is check_fn else f for f in o["STOP_CHECKS"])
        check("mutant: %s never stopping is caught" % check_fn.__name__[len("stop_"):],
              caught(STOP_CHECKS=off), True)

    def resets_unheeded(cfg, now, c):
        """stop_plan, with every window's reset taken away: a warning never ends."""
        seen = copy.deepcopy(c.usage)
        for w in (dig(seen, "status", "windows") or {}).values():
            w["resets_at"] = None
        return stop_plan_now(cfg, now, types.SimpleNamespace(**dict(vars(c), usage=seen)))
    stop_plan_now = stop_plan
    check("mutant: a window's reset not heeded is caught",
          caught(STOP_CHECKS=tuple(resets_unheeded if f is stop_plan_now else f
                                   for f in o["STOP_CHECKS"])), True)
    check("…and with every mutant undone the queue is as it was", battery() == got, True)


if __name__ == "__main__":
    sys.exit(main())
