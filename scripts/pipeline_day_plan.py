#!/usr/bin/env python3
"""The day plan file: what is planned today, written from the queue's decision (KIT-438, MVP 2
part 5a, contract §18; docs/adr/2026-11-03-autonomous-workers-within-a-budget-and-a-window.md,
decision 6).

    python3 scripts/pipeline_day_plan.py --selftest

TWO FUNCTIONS.  `build(...)` is pure: given what the queue and the pool said, it returns the
document, and reads no file, opens no socket, starts no process and reads no environment variable.
`write(doc, path)` checks the document against schemas/pipeline-day-plan.schema.json and writes it
atomically (a temporary file, then a rename), so a reader sees the old file or the new one, and a
document that does not conform is never a file. Running them each morning is a later part.

WHAT `build` IS GIVEN (data only; nothing is fetched).
  date, now    the local date `YYYY-MM-DD`, and seconds since 1970 for `written_at`.
  window       {"state", "opens_at", "closes_at"} (WINDOW_STATES); a time is seconds since 1970, a
               time ending in Z, or None.
  decision     what scripts/pipeline_queue_order.py `order()` returned: `start` (each with its
               lane), `held` (each with a reason), `needs_ok`, `stops` and `order`.
  verdicts     the list `order()` was given. Only `ticket`, `repo`, `effort` and `source` are read.
  spent        today's spend in dollars from the usage file, or None when it could not be read.
  planned_load, hard_stop, planned_by, why    the queue's numbers; "agent" and None, or "queue"
               and a reason from WHY.
  titles       {ticket id: title as the tracker gave it}: the only ticket text in the file, cleaned
               as the status file cleans one (contract §16).
  estimates, filed_from, progress, dropped   optional: the queue's dollars per effort; {agent-filed
               ticket: the ticket its finding came from}; {ticket: {"state": "started" or "done",
               "lane": a lane id or None}}, which the decision alone does not say; and the picks
               the planner's checks dropped, [{"id", "reason"}] (scripts/pipeline_day_planner.py).

FIELDS ONLY.  Ids, cleaned titles, numbers, words from closed lists, times, and replies built here
from two fixed patterns, `start <ID>` and `approve <ID>`. The queue's own reply is read only to see
that it names this item's ticket and the verb its kind takes; everything else of every record, a
description, a verdict's words, an extra key, is not read. A held reason, kind or stop that is not
on this file's lists means the queue is newer than the writer, so `build` raises ValueError. A held
piece that names no repository is counted in `counts.unplaced`, never dropped quietly.

WHERE IT CANNOT HELP.  The decision alone says `planned` and `held`: `started` and `done` come only
from `progress`. A held piece sits in its repository's first lane, as the queue gives a piece a
slot only when it starts it. The window's own words (KIT-437) are not merged yet.
"""
import argparse
import datetime
import json
import math
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    import check_schemas
    import pipeline_status_local as status_job
except ImportError as exc:  # a packaging accident, never a state to run in
    sys.exit("FAIL: %s. The day plan uses those files' own definitions and keeps no copy." % exc)

EXIT_USAGE = 2
SCHEMA = "pipeline-day-plan/1"
SCHEMA_NAME = "pipeline-day-plan"
EFFORTS = ("S", "M", "L")
ESTIMATES = {"S": 2.0, "M": 4.0, "L": 8.0}      # the queue's own defaults
WHO = ("agent", "queue")
# Why the agent's answer was not used, and why a pick was dropped. The planner (scripts/
# pipeline_day_planner.py) keeps the same two lists: each file holds its own copy, and each
# selftest pins that copy to the spelling the schema carries.
WHY = ("no-answer", "too-long", "no-block", "several-blocks", "unreadable", "nothing-valid",
       "nothing-admitted", "off")       # the last two: no answer was asked for (the morning job)
DROPS = ("not-in-pool", "duplicate", "wrong-repo", "blocker-order", "lane-cap", "overlap",
         "bad-footprint", "over-load")
WINDOW_STATES = ("open", "closed", "unknown")
# The queue's closed lists (scripts/pipeline_queue_order.py): the selftest pins these equal.
HELD = ("malformed", "no-repo", "no-estimate", "blockers-unreadable", "blocked", "stopped",
        "no-lane", "running-cap", "starts-cap", "agent-filed-cap", "over-load")
KINDS = ("plan", "agent_filed", "changed", "approval", "merge", "question")
STOPS = ("usage-missing", "usage-failed", "usage-stale", "ledger-unknown", "plan-near-limit",
         "plan-at-limit", "day-hard-stop", "piece-cost", "too-many-running", "too-many-starts",
         "api-key")
# The two fixed replies, and the verb each kind takes. A merge is done on GitHub, and a question
# is answered in its thread: neither has a reply.
REPLY = {"approve": "approve %s", "start": "start %s"}
REPLY_VERB = {"plan": "approve", "agent_filed": "start", "changed": "start", "approval": "start"}

TICKET_RE = re.compile(r"^[A-Z][A-Z0-9]{0,15}-[0-9]{1,9}\Z")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}(/[A-Za-z0-9_.-]{1,100})?\Z")
REF_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}(/[A-Za-z0-9_.-]{1,100})?(#[0-9]{1,9})?\Z")
LANE_RE = re.compile(r"^([A-Za-z0-9_.-]{1,100}(?:/[A-Za-z0-9_.-]{1,100})?):([0-9]{1,3})\Z")
TIME_RE = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})"
                     r"(?:\.[0-9]{1,9})?Z\Z")
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
WORD_RE = re.compile(r"^[A-Za-z0-9_./#:-]{1,100}\Z")
REPLY_RE = re.compile(r"^(start|approve) ([A-Z][A-Z0-9]{0,15}-[0-9]{1,9})\Z")


# --------------------------------------------------------------------------- #
# Small pure helpers
# --------------------------------------------------------------------------- #
def refuse(why):
    raise ValueError("the day plan cannot be built: " + why)


def pick(value, rx):
    return value if isinstance(value, str) and rx.match(value) else None


def whole(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def amount(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def money(v):
    """A dollar figure to a millionth, or None when it is not a finite number of 0 or more."""
    return round(float(v), 6) if amount(v) and v >= 0 else None


def figure(v):
    return round(float(v), 6) if amount(v) else None


def iso(seconds):
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))
    except (OverflowError, OSError, ValueError):
        return None


def epoch(text):
    """A time ending in Z (UTC) as seconds since 1970, or None."""
    m = TIME_RE.match(text) if isinstance(text, str) else None
    try:
        return datetime.datetime(*(int(g) for g in m.groups()),
                                 tzinfo=datetime.timezone.utc).timestamp() if m else None
    except (ValueError, OverflowError):
        return None


def read_time(v, what):
    """A window time as UTC to the second, from seconds since 1970 or a time ending in Z. None
    stays None. Anything else is refused: a time the writer guessed at would be a wrong one."""
    if v is None:
        return None
    seconds = epoch(v) if isinstance(v, str) else (v if amount(v) and v >= 0 else None)
    out = iso(seconds) if seconds is not None else None
    if out is None:
        refuse("%s is not a time" % what)
    return out


def number(ticket):
    return int(ticket.rsplit("-", 1)[1])


def title_of(titles, ref):
    """A ticket's title, cleaned as the status file cleans one (contract §16): one line, no
    markup, nothing hidden, 120 characters at most. None when there is none, or `ref` is no
    ticket (a pull request has no title here)."""
    raw = titles.get(ref) if isinstance(titles, dict) and pick(ref, TICKET_RE) else None
    return (status_job.clean_title(raw) or None) if isinstance(raw, str) else None


def reply_for(kind, ref, said):
    """The reply that approves a needs-OK item, built from a fixed pattern, or None. `said` is
    what the queue named: it counts only as a yes that a reply exists, for this ticket and the
    verb the kind takes. Its words are never copied."""
    verb = REPLY_VERB.get(kind)
    m = REPLY_RE.match(said) if isinstance(said, str) else None
    if verb and m and m.group(1) == verb and m.group(2) == ref and TICKET_RE.match(ref):
        return REPLY[verb] % ref
    return None


# --------------------------------------------------------------------------- #
# The document
# --------------------------------------------------------------------------- #
def build(date, window, decision, verdicts, spent, planned_load, hard_stop, planned_by, why, now,
          titles=None, estimates=None, filed_from=None, progress=None, dropped=None):
    """The day plan, from what the queue and the pool said. See the module's docstring. Raises
    ValueError when what it is given cannot make a document: a bad date, number or window, or a
    record or a word it cannot read."""
    if not (isinstance(date, str) and DATE_RE.match(date)):
        refuse("the date is not YYYY-MM-DD")
    try:
        datetime.date(*(int(p) for p in date.split("-")))
    except ValueError:
        refuse("the date is not a real day")
    if (planned_by, why) not in [("agent", None)] + [("queue", w) for w in WHY]:
        refuse("the agent plans with no reason, and the queue with one from WHY")
    if not isinstance(window, dict) or window.get("state") not in WINDOW_STATES:
        refuse("the window has no state from WINDOW_STATES")
    opens = read_time(window.get("opens_at"), "opens_at")
    closes = read_time(window.get("closes_at"), "closes_at")
    load, stop = money(planned_load), money(hard_stop)
    if not load or not stop:
        refuse("planned_load and hard_stop are numbers above 0")
    if spent is not None and money(spent) is None:
        refuse("spent is a number of 0 or more, or None")
    written = iso(now) if amount(now) and now >= 0 else None
    if written is None:
        refuse("now is not a time")
    rates = dict(ESTIMATES) if estimates is None else estimates
    if not (isinstance(rates, dict) and set(rates) == set(EFFORTS)
            and all(money(v) is not None for v in rates.values())):
        refuse("estimates give S, M and L each a number of 0 or more")
    rates = dict((k, money(v)) for k, v in rates.items())
    worst = max(rates.values())
    for what, value in (("titles", titles), ("filed_from", filed_from), ("progress", progress)):
        if value is not None and not isinstance(value, dict):
            refuse("%s is a mapping" % what)
    if dropped is not None and not isinstance(dropped, list):
        refuse("dropped is a list")

    def listed(key):
        v = decision.get(key) if isinstance(decision, dict) else None
        if not isinstance(v, list) or not all(isinstance(x, dict) for x in v):
            refuse("the decision's %r is not a list of records" % key)
        return v
    start, held, needs, halts = (listed(k) for k in ("start", "held", "needs_ok", "stops"))
    order = decision.get("order")
    if not isinstance(order, list):
        refuse("the decision's 'order' is not a list")
    where = dict((t, i) for i, t in enumerate(order) if isinstance(t, str))

    known = {}                      # ticket -> what the pool's verdict says of it
    for v in verdicts if isinstance(verdicts, list) else ():
        t = pick(v.get("ticket"), TICKET_RE) if isinstance(v, dict) else None
        if t and t not in known:
            known[t] = {"repo": pick(v.get("repo"), REPO_RE),
                        "effort": v.get("effort") if v.get("effort") in EFFORTS else None,
                        "agent": v.get("source") == "agent_filed"}

    def guess(t):
        return rates.get(known.get(t, {}).get("effort"))

    slots, lost, nameless = {}, set(), 0
    for e in start:
        t, repo = pick(e.get("ticket"), TICKET_RE), pick(e.get("repo"), REPO_RE)
        lane = e.get("lane")
        m = LANE_RE.match(lane) if isinstance(lane, str) else None
        if not (t and repo and m and m.group(1) == repo):
            refuse("a start has no ticket, repository and lane that fit together")
        own = money(e.get("estimate"))
        slots[t] = {"lane": lane, "state": "planned", "held": None,
                    "estimate": own if own is not None else guess(t)}
    for e in held:
        if e.get("reason") not in HELD:
            refuse("a held piece has a reason this writer does not know")
        t = pick(e.get("ticket"), TICKET_RE)
        repo = known[t]["repo"] if t in known else None
        if t is None:
            nameless += 1
        elif repo is None:
            lost.add(t)
        elif t not in slots:
            slots[t] = {"lane": "%s:1" % repo, "state": "held", "held": e["reason"],
                        "estimate": guess(t)}
    for t, p in sorted((progress or {}).items(), key=lambda kv: str(kv[0])):
        lane = p.get("lane") if isinstance(p, dict) else None
        m = LANE_RE.match(lane) if isinstance(lane, str) else None
        if not (pick(t, TICKET_RE) and isinstance(p, dict) and p.get("state") in ("started", "done")
                and (lane is None or m)):
            refuse("progress names a ticket, a state or a lane this writer cannot read")
        repo = known.get(t, {}).get("repo")
        lane = lane or (slots[t]["lane"] if t in slots else ("%s:1" % repo if repo else None))
        if lane is None:
            lost.add(t)
            continue
        slots[t] = {"lane": lane, "state": p["state"], "held": None,
                    "estimate": slots[t]["estimate"] if t in slots else guess(t)}

    rank = {"done": 0, "started": 1, "planned": 2, "held": 2}
    grouped = {}
    for t in sorted(slots, key=lambda t: (rank[slots[t]["state"]], where.get(t, len(where)),
                                          number(t))):
        s = slots[t]
        grouped.setdefault(s["lane"], []).append({
            "id": t, "title": title_of(titles, t), "estimate": s["estimate"],
            "state": s["state"], "held": s["held"]})
    lanes = [{"id": lane, "repo": LANE_RE.match(lane).group(1), "pieces": pieces}
             for lane, pieces in sorted(grouped.items(), key=lambda kv: (
                 LANE_RE.match(kv[0]).group(1), int(LANE_RE.match(kv[0]).group(2))))]
    flat = [p for lane in lanes for p in lane["pieces"]]

    asks = []
    for n in needs:
        kind, ref = n.get("kind"), pick(n.get("ref"), REF_RE)
        if kind not in KINDS or ref is None or not whole(n.get("pieces")) \
                or money(n.get("usd")) is None:
            refuse("a needs_ok item has a kind, a ref, a count or a sum this writer cannot read")
        asks.append({"kind": kind, "ref": ref, "title": title_of(titles, ref),
                     "holds_up": {"pieces": n["pieces"], "usd": money(n["usd"])},
                     "reply": reply_for(kind, ref, n.get("reply")),
                     "urgent": n.get("urgent") is True})
    halted = []
    for s in halts:
        if s.get("stop") not in STOPS:
            refuse("a stop this writer does not know")
        halted.append({"stop": s["stop"], "ref": pick(s.get("ref"), WORD_RE),
                       "value": figure(s.get("value")), "limit": figure(s.get("limit"))})
    sources = filed_from or {}
    filed = [{"id": t, "source": pick(sources.get(t), TICKET_RE)}
             for t in sorted(slots, key=number)
             if known.get(t, {}).get("agent") and slots[t]["state"] != "held"]
    cut = []
    for d in dropped or ():
        if not isinstance(d, dict) or d.get("reason") not in DROPS:
            refuse("a dropped pick has a reason this writer does not know")
        cut.append({"id": pick(d.get("id"), TICKET_RE), "reason": d["reason"]})
    n_of = lambda state: sum(1 for p in flat if p["state"] == state)       # noqa: E731
    return {
        "schema": SCHEMA, "written_at": written, "date": date,
        "planned_by": {"who": planned_by, "why": why},
        "window": {"state": window["state"], "opens_at": opens, "closes_at": closes},
        "budget": {"planned": load, "stop": stop,
                   "spent": None if spent is None else money(spent),
                   "estimated": money(sum(worst if p["estimate"] is None else p["estimate"]
                                          for p in flat if p["state"] in ("planned", "started")))},
        "lanes": lanes, "needs_ok": asks, "agent_filed": filed, "dropped": cut, "stops": halted,
        "counts": {"lanes": len(lanes), "pieces": len(flat), "planned": n_of("planned"),
                   "started": n_of("started"), "done": n_of("done"), "held": n_of("held"),
                   "unplaced": len(lost - set(slots)) + nameless, "needs_ok": len(asks),
                   "agent_filed": len(filed), "stops": len(halted), "dropped": len(cut)}}


# --------------------------------------------------------------------------- #
# THE ONE PLACE THIS FILE TOUCHES DISK
# --------------------------------------------------------------------------- #
def problems_of(doc):
    """What is wrong with a document, by where and by which rule: never the value, because the
    schema's message about a value quotes it, and a value can be a ticket's words."""
    try:
        found = check_schemas.document_problems(doc, SCHEMA_NAME)
    except Exception as exc:  # noqa: BLE001 — a value that is not JSON ends the check only
        return ["<root> [%s]" % type(exc).__name__]
    out = []
    for p in found[:10]:
        where, _, rest = p.partition(" ")
        rule = re.search(r"\[([A-Za-z]+)\]\Z", rest)
        out.append("%s [%s]" % (where[:120], rule.group(1) if rule else "?"))
    return out


def write(doc, path):
    """Write `doc` to `path` if and only if it conforms to the schema, and in one piece: a
    temporary file beside it, then a rename. A refused or failed write leaves what was there.
    Raises ValueError for a document that does not conform, or a folder inside a git working
    tree, where a session can write; OSError when the disk will not take it. Returns the path."""
    problems = problems_of(doc)
    if problems:
        raise ValueError("the day plan does not conform to schemas/%s.schema.json, so nothing "
                         "was written: %s" % (SCHEMA_NAME, "; ".join(problems)))
    path = os.path.abspath(path)
    folder = os.path.dirname(path)
    if status_job.inside_git_worktree(folder):
        raise ValueError("the day plan's folder is inside a git working tree, where a session "
                         "can write: %s" % folder)
    if not os.path.isdir(folder):
        os.makedirs(folder, mode=0o755)
    tmp = "%s.tmp-%d" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, sort_keys=True, allow_nan=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path


def main(argv=None):
    p = argparse.ArgumentParser(prog="pipeline_day_plan.py", description=__doc__.split("\n")[0])
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)
    if args.selftest:
        from scratch_run import contained   # KIT-450: its temp folders go when it ends
        return contained(selftest, "day-plan-selftest-run-")
    p.print_help()
    return EXIT_USAGE


# --------------------------------------------------------------------------- #
# --selftest: nothing fetched, the clock stood in for, files only in a temporary folder
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
    import builtins
    import contextlib
    import copy
    import inspect
    import io
    import socket
    import subprocess
    import tempfile
    import pipeline_queue_order as queue            # what the closed lists are pinned to

    LEAK, TEXT = "SENTINEL-TITLE-3f9c", "SENTINEL-TEXT-3f9c"    # a title's, and what none carries
    NOW = queue.epoch("2026-11-04T14:05:00Z")
    WINDOW = {"state": "open", "opens_at": "2026-11-04T14:00:00Z",
              "closes_at": "2026-11-04T20:00:00Z"}
    TITLES = dict(("ENG-%d" % n, "Title %d %s" % (n, LEAK)) for n in range(1, 30))
    REFUSED = "REFUSED"

    def vd(n, repo="acme/app", effort="S", source="todo"):
        return {"ticket": "ENG-%d" % n, "id": "id-%d" % n, "admitted": True, "repo": repo,
                "effort": effort, "source": source, "title": "Raw %d %s" % (n, TEXT),
                "description": TEXT}

    def st(n, repo="acme/app", slot=1, est=2.0, source="todo"):
        return {"ticket": "ENG-%d" % n, "id": "id-%d" % n, "repo": repo, "slot": slot,
                "lane": "%s:%d" % (repo, slot), "estimate": est, "source": source, "note": TEXT}

    def hd(n, why="blocked"):
        return {"ticket": "ENG-%d" % n, "reason": why, "waits_on": [], "note": TEXT}

    def nd(kind, ref, pieces=1, usd=2.0, reply=None, urgent=False):
        return {"kind": kind, "ref": ref, "pieces": pieces, "usd": usd, "reply": reply,
                "urgent": urgent, "note": TEXT}

    def dec(start=(), held=(), needs=(), stops=(), order=()):
        return {"start": list(start), "held": list(held), "needs_ok": list(needs),
                "stops": list(stops), "notes": [], "order": list(order),
                "load": {"spent": 6.5, "committed": 18.5, "planned": 40}}

    DECISION = dec(
        start=[st(1), st(3, slot=2, source="agent_filed"), st(4, "acme/web", est=8.0)],
        held=[hd(2), hd(5, "over-load"), hd(6, "no-lane"), hd(7, "no-repo"),
              {"ticket": None, "reason": "malformed", "waits_on": []}],
        needs=[nd("plan", "ENG-10", 3, 14.0, "approve ENG-10", True),
               nd("agent_filed", "ENG-11", reply="start ENG-11"),
               nd("changed", "ENG-12", reply="start ENG-12"),
               nd("approval", "ENG-13", reply="start ENG-13"),
               nd("merge", "acme/app#5"), nd("question", "ENG-14")],
        order=["ENG-1", "ENG-2", "ENG-3", "ENG-4", "ENG-5", "ENG-6", "ENG-7"])
    BASE = dict(
        date="2026-11-04", window=WINDOW, decision=DECISION, spent=6.5, planned_load=40,
        hard_stop=60, planned_by="agent", why=None, now=NOW, titles=TITLES,
        verdicts=[vd(1), vd(2, effort="M"), vd(3, source="agent_filed"), vd(4, "acme/web", "L"),
                  vd(5, "acme/web"), vd(6), vd(7, repo=None), vd(8, effort="M"), vd(9)],
        filed_from={"ENG-3": "ENG-90"},
        progress={"ENG-8": {"state": "started", "lane": "acme/app:2"},
                  "ENG-9": {"state": "done", "lane": None}},
        dropped=[{"id": "ENG-15", "reason": "over-load"}, {"id": None, "reason": "not-in-pool"}])

    def make(**over):
        return build(**dict(BASE, **over))

    def attempt(**over):
        try:
            return make(**over)
        except ValueError:
            return REFUSED

    def lane_view(d):
        return [(la["id"], [(p["id"], p["state"], p["held"], p["estimate"]) for p in la["pieces"]])
                for la in d["lanes"]]

    def problems(d):
        return check_schemas.document_problems(d, SCHEMA_NAME)

    def reply_of(kind, ref, said):
        return make(decision=dec(needs=[nd(kind, ref, reply=said)]))["needs_ok"][0]["reply"]

    def only(**over):                          # a document from one decision, nothing else carried
        return make(**dict(dict(verdicts=[], progress=None, dropped=None, filed_from=None), **over))

    doc = make()
    # -- the document, from one decision ---------------------------------------------------------
    check("the document conforms to the schema", problems(doc), [])
    check("lanes are the queue's ids by repository and slot; a held piece sits in the first",
          lane_view(doc),
          [("acme/app:1", [("ENG-9", "done", None, 2.0), ("ENG-1", "planned", None, 2.0),
                           ("ENG-2", "held", "blocked", 4.0), ("ENG-6", "held", "no-lane", 2.0)]),
           ("acme/app:2", [("ENG-8", "started", None, 4.0), ("ENG-3", "planned", None, 2.0)]),
           ("acme/web:1", [("ENG-4", "planned", None, 8.0), ("ENG-5", "held", "over-load", 2.0)])])
    check("the budget, who planned it, the window, the day and the counts",
          (doc["budget"], doc["planned_by"], doc["window"], doc["date"], doc["written_at"],
           doc["counts"]),
          ({"planned": 40.0, "stop": 60.0, "spent": 6.5, "estimated": 16.0},
           {"who": "agent", "why": None}, WINDOW, "2026-11-04", "2026-11-04T14:05:00Z",
           {"lanes": 3, "pieces": 8, "planned": 3, "started": 1, "done": 1, "held": 3,
            "unplaced": 2, "needs_ok": 6, "agent_filed": 1, "stops": 0, "dropped": 2}))
    check("agent-filed: with its source, never a held one, null where none is recorded",
          (doc["agent_filed"],
           make(decision=dec(held=[hd(3)], order=["ENG-3"]), progress=None)["agent_filed"],
           make(filed_from=None)["agent_filed"]),
          ([{"id": "ENG-3", "source": "ENG-90"}], [], [{"id": "ENG-3", "source": None}]))
    check("the planner's drops are carried; titles are cleaned, and a pull request has none",
          (doc["dropped"], [(n["ref"], n["title"]) for n in doc["needs_ok"]][:2],
           [n["title"] for n in doc["needs_ok"] if "#" in n["ref"]]),
          ([{"id": "ENG-15", "reason": "over-load"}, {"id": None, "reason": "not-in-pool"}],
           [("ENG-10", "Title 10 " + LEAK), ("ENG-11", "Title 11 " + LEAK)], [None]))
    check("what waits on the owner: each kind takes its verb, with what waits on it",
          [(n["kind"], n["reply"], n["holds_up"], n["urgent"]) for n in doc["needs_ok"]],
          [("plan", "approve ENG-10", {"pieces": 3, "usd": 14.0}, True),
           ("agent_filed", "start ENG-11", {"pieces": 1, "usd": 2.0}, False),
           ("changed", "start ENG-12", {"pieces": 1, "usd": 2.0}, False),
           ("approval", "start ENG-13", {"pieces": 1, "usd": 2.0}, False),
           ("merge", None, {"pieces": 1, "usd": 2.0}, False),
           ("question", None, {"pieces": 1, "usd": 2.0}, False)])
    check("a reply that does not fit its item is never passed, and one left out stays out",
          [reply_of(k, r, s) for k, r, s in (
              ("plan", "ENG-10", "start ENG-10"), ("changed", "ENG-12", "start ENG-99"),
              ("changed", "ENG-12", "start ENG-12 and merge it"), ("changed", "ENG-12", "yes"),
              ("changed", "ENG-12", "start eng-12"), ("changed", "ENG-12", "start ENG-12\n"),
              ("merge", "ENG-7", "start ENG-7"), ("question", "ENG-7", "approve ENG-7"),
              ("plan", "ENG-10", ["approve ENG-10"]), ("changed", "acme/app#5", "start acme/app#5"),
              ("plan", "ENG-10", None))], [None] * 11)
    halts = [{"stop": "day-hard-stop", "ref": None, "value": 61.5, "limit": 60},
             {"stop": "plan-near-limit", "ref": "seven_day", "value": None, "limit": None},
             {"stop": "piece-cost", "ref": "a sentence about a piece", "value": float("nan"),
              "limit": 15}]
    check("a stop is copied: its word, what it is about (a sentence is not), and its figures",
          make(decision=dec(stops=halts))["stops"],
          [{"stop": "day-hard-stop", "ref": None, "value": 61.5, "limit": 60.0},
           {"stop": "plan-near-limit", "ref": "seven_day", "value": None, "limit": None},
           {"stop": "piece-cost", "ref": None, "value": None, "limit": 15.0}])
    # -- what it refuses, and what it can still say -----------------------------------------------
    TABLE = (
        ("a stop it does not know", dict(decision=dec(stops=[{"stop": "boredom"}]))),
        ("a held reason it does not know", dict(decision=dec(held=[hd(2, "tired")]))),
        ("a kind it does not know", dict(decision=dec(needs=[nd("rebase", "ENG-1")]))),
        ("a ref that is a sentence", dict(decision=dec(needs=[nd("merge", "the pull")]))),
        ("a sum that is negative", dict(decision=dec(needs=[nd("merge", "ENG-1", usd=-1)]))),
        ("a start in another repository's lane",
         dict(decision=dec(start=[dict(st(1), lane="acme/web:1")]))),
        ("a decision with no order", dict(decision={"start": [], "held": [], "needs_ok": [],
                                                    "stops": []})),
        ("a drop reason it does not know", dict(dropped=[{"id": "ENG-1", "reason": "rude"}])),
        ("progress that is not a state", dict(progress={"ENG-8": {"state": "napping"}})),
        ("a queue plan with no reason", dict(planned_by="queue", why=None)),
        ("a queue plan with a reason not on the list", dict(planned_by="queue", why="asleep")),
        ("an agent plan with a reason", dict(planned_by="agent", why="no-block")),
        ("a window state not on the list", dict(window=dict(WINDOW, state="ajar"))),
        ("a window time that is not one", dict(window=dict(WINDOW, opens_at="at nine"))),
        ("a date that is not a day", dict(date="2026-02-30")), ("a negative spend", dict(spent=-1)),
        ("a load of nothing", dict(planned_load=0)),
        ("estimates for two efforts", dict(estimates={"S": 1, "M": 2})))
    check("refused, each by name", [n for n, over in TABLE if attempt(**over) != REFUSED], [])
    check("a queue's reason; a spend nobody could read; a window nobody could read; seconds or Z",
          (make(planned_by="queue", why="several-blocks")["planned_by"],
           make(spent=None)["budget"]["spent"],
           make(window={"state": "unknown", "opens_at": None, "closes_at": None})["window"],
           make(window={"state": "closed", "opens_at": NOW + 3600,
                        "closes_at": "2026-11-04T20:00:00.250Z"})["window"]),
          ({"who": "queue", "why": "several-blocks"}, None,
           {"state": "unknown", "opens_at": None, "closes_at": None},
           {"state": "closed", "opens_at": "2026-11-04T15:05:00Z",
            "closes_at": "2026-11-04T20:00:00Z"}))
    empty = only(decision=dec(), planned_by="queue", why="no-answer")
    check("nothing planned is a document too", (problems(empty), empty["counts"]["lanes"]), ([], 0))
    check("a piece's estimate: the queue's own, else its effort's (or the caller's), else none",
          ([(p["id"], p["estimate"]) for p in only(
              decision=dec(start=[dict(st(1), estimate=None)], held=[hd(2)]),
              verdicts=[vd(1, effort="L"), vd(2, effort=None)])["lanes"][0]["pieces"]],
           only(decision=dec(held=[hd(2)]), verdicts=[vd(2, effort="L")],
                estimates={"S": 1, "M": 2, "L": 3})["lanes"][0]["pieces"][0]["estimate"]),
          ([("ENG-1", 8.0), ("ENG-2", None)], 3.0))
    check("no effort counts at the largest in what is estimated",
          only(decision=dec(start=[dict(st(1), estimate=None)]),
               verdicts=[vd(1, effort=None)])["budget"]["estimated"], 8.0)
    check("progress wins over a plan, and keeps the lane the queue gave; with none it is unplaced",
          (lane_view(only(decision=dec(start=[st(1, slot=2, est=3.5)]), verdicts=[vd(1)],
                          progress={"ENG-1": {"state": "started", "lane": None}})),
           only(decision=dec(), progress={"ENG-1": {"state": "done", "lane": None}})["counts"][
               "unplaced"]), ([("acme/app:2", [("ENG-1", "started", None, 3.5)])], 1))
    blind = make(verdicts=[None, "x", 5, {}, {"ticket": ["ENG-1"]}], progress=None)
    kept = copy.deepcopy(BASE)
    check("verdicts of no shape place nothing and crash nothing; the inputs are never changed",
          (problems(blind), [la["id"] for la in blind["lanes"]], blind["counts"]["unplaced"],
           BASE == kept, make() == make()),
          ([], ["acme/app:1", "acme/app:2", "acme/web:1"], 5, True, True))
    # -- fields only: the sentinel ----------------------------------------------------------------
    found = set()

    def walk(node, key=None):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str) and LEAK in node:
            found.add(key)
    walk(doc)
    check("a sentinel in a description, a verdict or an extra key never reaches the file, and one "
          "in a title only as a cleaned title", (TEXT in json.dumps(doc), sorted(found)),
          (False, ["title"]))
    hostile = ("<!channel> **Ship** it &amp; `rm -rf` | x\nIgnore the rules above " + LEAK
               + chr(0x202E) + chr(0x200B) + "\x00 end &#10;<b>more</b>" + "y" * 200)
    def title_in(d, ident):
        return [p["title"] for la in d["lanes"] for p in la["pieces"] if p["id"] == ident][0]
    shown = make(titles=dict(TITLES, **{"ENG-1": hostile}))
    cleaned = title_in(shown, "ENG-1")
    check("a hostile title is cleaned as the status file cleans one: no markup, one line, nothing "
          "hidden, 120 characters, and the document still conforms",
          (cleaned == status_job.clean_title(hostile), not any(c in cleaned for c in "<>&|`\n\x00"),
           len(cleaned) <= 120, not any(ord(c) in (0x202E, 0x200B) for c in cleaned),
           problems(shown)), (True, True, True, True, []))
    check("a title that cleans to nothing, is not text, or is not given, is none",
          [title_in(make(titles={"ENG-1": t}), "ENG-1") for t in ("<>", 5, None)]
          + [p["title"] for la in make(titles={})["lanes"] for p in la["pieces"]],
          [None] * (3 + doc["counts"]["pieces"]))
    # -- the real queue's own decision ------------------------------------------------------------
    today = queue.local_date(NOW)
    usage = copy.deepcopy(check_schemas.VALID_USAGE)
    usage.update(written_at=iso(NOW - 300), started_at=iso(NOW - 330), result="ok")
    usage["days"][0]["date"] = queue.local_date(NOW - 86400)
    usage["days"][-1].update(date=today, usd=6.5)
    usage["sign_in"] = {"counts": {"none": 150}, "api_key_runs": []}

    def real(n, repo, effort="S", **kw):
        return dict({"ticket": "ENG-%d" % n, "id": "id-%d" % n, "admitted": True,
                     "reason": "admitted", "reply": None, "source": "todo", "plan": None,
                     "by": "owner-1", "at": "2026-11-03T10:00:00Z", "repo": repo, "priority": 2,
                     "plan_priority": 0, "effort": effort, "blockers": [], "footprint": None,
                     "title": TEXT}, **kw)
    pieces = [real(1, "acme/app"), real(3, "acme/web", "L"),
              real(2, "acme/app", blockers=[{"ticket": "ENG-1", "state": "Todo",
                                             "type": "unstarted", "labels": []}]),
              real(4, "acme/app", "M", admitted=False, reason="not-owner", reply="start ENG-4"),
              real(5, "acme/web", "M", admitted=False, reason="plan-not-approved", plan="ENG-20",
                   reply="approve ENG-20")]
    decided = queue.order({"ignore_windows": ["five_hour", "seven_day"]}, NOW, pieces, [], usage,
                          {"date": today, "starts": 1, "agent_filed_starts": 0})
    real_doc = build("2026-11-04", WINDOW, decided, pieces, decided["load"]["spent"], 40, 60,
                     "queue", "no-block", NOW, titles={"ENG-20": "A plan " + LEAK})
    check("the real order() output builds a document the schema accepts, with its lanes, its "
          "holds, what waits on the owner in the queue's order, and no stray text",
          (problems(real_doc), lane_view(real_doc), [(n["kind"], n["ref"], n["reply"])
                                                     for n in real_doc["needs_ok"]],
           real_doc["budget"], TEXT in json.dumps(real_doc)),
          ([], [("acme/app:1", [("ENG-1", "planned", None, 2.0),
                                ("ENG-2", "held", "blocked", 2.0)]),
                ("acme/web:1", [("ENG-3", "planned", None, 8.0)])],
           [("approval", "ENG-4", "start ENG-4"), ("plan", "ENG-20", "approve ENG-20")],
           {"planned": 40.0, "stop": 60.0, "spent": 6.5, "estimated": 10.0}, False))

    # -- pinned to the queue, the schema and the status file --------------------------------------
    schema = check_schemas.load_schema(SCHEMA_NAME)
    props, defs = schema["properties"], schema["$defs"]
    check("the closed lists and estimates are the queue's own",
          (HELD, KINDS, STOPS, ESTIMATES), (queue.HELD, queue.KINDS, queue.STOPS,
                                           queue.DEFAULTS["estimates"]))
    check("every list is the schema's, word for word",
          (list(WHY), list(DROPS), list(STOPS), list(KINDS), list(HELD), list(WINDOW_STATES)),
          (props["planned_by"]["oneOf"][1]["properties"]["why"]["enum"],
           props["dropped"]["items"]["properties"]["reason"]["enum"],
           props["stops"]["items"]["properties"]["stop"]["enum"],
           defs["need"]["properties"]["kind"]["enum"],
           defs["piece"]["oneOf"][1]["properties"]["held"]["enum"],
           props["window"]["properties"]["state"]["enum"]))
    status = check_schemas.load_schema("pipeline-status")["$defs"]["title"]
    pairs = (
        (defs["reply_or_null"]["pattern"], REPLY_RE,
         ("start ENG-1", "approve ENG-12", "merge ENG-1", "start eng-1", "start ENG-1\n", "")),
        (defs["ticket_id"]["pattern"], TICKET_RE,
         ("ENG-1", "eng-1", "ENG-1\n", "ENG-", "A" * 17 + "-1", "ENG-1234567890")),
        (defs["lane"]["properties"]["id"]["pattern"], LANE_RE,
         ("acme/app:1", "acme/app", "acme/app:", "acme/app:1000", "acme/app:1\n")),
        (defs["need"]["properties"]["ref"]["pattern"], REF_RE,
         ("ENG-1", "acme/app#40", "acme/app", "acme/app#", "a b", "acme/app#40\n")))
    check("a cleaned title is the status file's; the patterns are the queue's; its lane ids and "
          "replies are ones this file reads",
          ((defs["title_or_null"]["pattern"], defs["title_or_null"]["maxLength"]),
           (TICKET_RE.pattern, REPO_RE.pattern), bool(LANE_RE.match(queue.lane_id("acme/app", 2)))),
          ((status["pattern"], status["maxLength"]), (queue.TICKET_RE.pattern,
                                                      queue.REPO_RE.pattern), True))
    check("the schema's patterns and this file's take and refuse the same strings",
          [bool(re.search(p, s)) == bool(rx.match(s)) for p, rx, tests in pairs for s in tests],
          [True] * sum(len(t) for _p, _r, t in pairs))

    # -- it touches nothing -----------------------------------------------------------------------
    source = inspect.getsource(sys.modules[__name__])
    marker = "\n# THE ONE PLACE THIS FILE TOUCHES DISK"
    pure, rest = source.split(marker)[0], source.split(marker)[1].split("\ndef selftest():")[0]
    check("no file, socket, process or environment word is in what builds the document",
          [w for w in ("import socket", "urllib", "subprocess", "shutil", "os.system", "os.popen",
                       "os.environ", "getenv", "os.listdir", "os.scandir", "os.stat",
                       "os.path.exists", ".write(", "os.replace(", "os.unlink(", "makedirs",
                       "tempfile") if w in pure] + re.findall(r"open\([^)]*[\"'][wax+]", pure), [])
    check("the one writer: one open for writing, one rename, and the folder check first",
          (len(re.findall(r"open\([^)]*\"w\"", rest)), rest.count("os.replace("),
           0 <= rest.find("inside_git_worktree") < rest.find("open(")), (1, 1, True))

    class Watch(object):
        """Counts every file, process, environment and socket touch while it is open."""
        def __enter__(self):
            self.hits, self.saved = [], []
            for owner, name in ((builtins, "open"), (os, "stat"), (os, "listdir"),
                                (os, "getenv"), (socket.socket, "connect"),
                                (subprocess.Popen, "__init__")):
                real_fn = getattr(owner, name)
                self.saved.append((owner, name, owner.__dict__.get(name, self), real_fn))

                def seen(*a, _n=name, _r=real_fn, **k):
                    self.hits.append(_n)
                    return _r(*a, **k)
                setattr(owner, name, seen)
            return self

        def __exit__(self, *exc):
            for owner, name, mine, real_fn in self.saved:
                if mine is self:
                    delattr(owner, name)
                else:
                    setattr(owner, name, real_fn)
    with Watch() as w:
        make()
        attempt(decision=None)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main([])
    check("building opens no file, runs no process, reads no environment, opens no socket; and "
          "no command is a usage error", (w.hits, code), ([], EXIT_USAGE))

    # -- the writer: validated first, in one piece, never where a session can write ---------------
    def disk():
        """What the writer does, seen from its folder: a list of observations."""
        seen = []

        def tried(document, path):
            try:
                return ("ok", write(document, path))
            except Exception as exc:  # noqa: BLE001 — what it raises is what is observed
                return (type(exc).__name__, str(exc))
        old_mask = os.umask(0o077)               # so a mode the writer did not set would show
        try:
            with tempfile.TemporaryDirectory() as tmp:
                target = os.path.join(tmp, "plans", "day-plan.json")
                folder = os.path.dirname(target)

                def stands():
                    try:
                        with open(target) as fh:
                            return json.load(fh)
                    except (OSError, ValueError):
                        return None
                said = tried(doc, target)
                seen.append(("written", said[0], said[1] == target, stands() == doc,
                             oct(os.stat(target).st_mode & 0o777), sorted(os.listdir(folder))))
                said = tried(dict(doc, date=TEXT), target)
                seen.append(("a bad document", said[0], TEXT in said[1], "date" in said[1],
                             stands() == doc))
                nan = dict(doc, stops=[{"stop": "day-hard-stop", "ref": None,
                                        "value": float("nan"), "limit": None}])
                seen.append(("a number that is not one", tried(nan, target)[0], stands() == doc,
                             sorted(os.listdir(folder))))
                real_replace = os.replace

                def refuse_rename(*a, **k):
                    raise OSError("disk says no")
                os.replace = refuse_rename
                try:
                    said = tried(dict(doc, date="2026-11-05"), target)
                finally:
                    os.replace = real_replace
                seen.append(("a failed rename", said[0], (stands() or {}).get("date"),
                             sorted(os.listdir(folder))))
                checkout = os.path.join(tmp, "checkout")
                os.makedirs(os.path.join(checkout, ".git"))
                said = tried(doc, os.path.join(checkout, "out", "day-plan.json"))
                seen.append(("a working tree", said[0],
                             os.path.exists(os.path.join(checkout, "out"))))
                seen.append(("a value that is not JSON", tried(object(), target)[0]))
        finally:
            os.umask(old_mask)
        return seen
    check("write: in one piece at mode 0644, never a bad document or a number that is not one, "
          "never in a working tree, a failed rename leaves the old file, no message quotes a value",
          disk(), [("written", "ok", True, True, "0o644", ["day-plan.json"]),
                   ("a bad document", "ValueError", False, True, True),
                   ("a number that is not one", "ValueError", True, ["day-plan.json"]),
                   ("a failed rename", "OSError", "2026-11-04", ["day-plan.json"]),
                   ("a working tree", "ValueError", False),
                   ("a value that is not JSON", "ValueError")])

    # -- mutants: a check that cannot fail checks nothing -----------------------------------------
    class Anything(object):
        def __contains__(self, _):
            return True

    def battery():
        runs = [lane_view(make()), make()["budget"], make()["counts"], make()["agent_filed"],
                [n["reply"] for n in make()["needs_ok"]], [n["title"] for n in make()["needs_ok"]],
                [p["title"] for la in make(titles=dict(TITLES, **{"ENG-1": hostile}))["lanes"]
                 for p in la["pieces"]], TEXT in json.dumps(make())]
        runs += [n for n, over in TABLE if attempt(**over) != REFUSED]
        runs += [reply_of(k, r, s) for k, r, s in (("plan", "ENG-10", "start ENG-10"),
                                                   ("merge", "ENG-7", "start ENG-7"),
                                                   ("changed", "ENG-12", "start ENG-99"),
                                                   ("plan", "ENG-10", "approve ENG-10"))]
        return runs + disk()
    got = battery()
    o = dict((k, globals()[k]) for k in ("title_of", "reply_for", "money", "problems_of", "pick"))

    def caught(**patched):
        saved = dict((k, globals()[k]) for k in patched)
        globals().update(patched)
        try:
            return battery() != got
        except (IndexError, KeyError, TypeError, ValueError, OSError):
            return True
        finally:
            globals().update(saved)

    def naive_write(doc_, path):
        """The same writer with one thing changed: the document goes straight to the file."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(doc_, fh, indent=1, sort_keys=True, allow_nan=False)
            fh.write("\n")
        os.chmod(path, 0o644)
        return os.path.abspath(path)
    for name, patch in (
            ("a title never cleaned", dict(title_of=lambda titles, ref: titles.get(ref) if (
                isinstance(titles, dict) and o["pick"](ref, TICKET_RE)) else None)),
            ("a reply for every item", dict(reply_for=lambda kind, ref, said: "start %s" % ref)),
            ("a reply never built", dict(reply_for=lambda kind, ref, said: None)),
            ("the queue's own words passed on", dict(reply_for=lambda kind, ref, said: said if (
                isinstance(said, str) and said.endswith(ref)) else None)),
            ("every held reason welcome", dict(HELD=Anything())),
            ("every kind welcome", dict(KINDS=Anything())),
            ("every stop welcome", dict(STOPS=Anything())),
            ("every drop reason welcome", dict(DROPS=Anything())),
            ("every reason the queue plans for welcome", dict(WHY=Anything())),
            ("a negative sum welcome", dict(money=lambda v: round(float(v), 6) if o["money"](
                abs(v)) is not None else None)),
            ("validation skipped before a write", dict(problems_of=lambda doc_: [])),
            ("a write that is not atomic", dict(write=naive_write))):
        check("mutant: %s is caught" % name, caught(**patch), True)
    check("…and with every mutant undone the writer is as it was", battery() == got, True)


if __name__ == "__main__":
    sys.exit(main())
