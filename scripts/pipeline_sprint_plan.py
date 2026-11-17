#!/usr/bin/env python3
"""Sprint planning at the reset: one proposal of the week's work, for the owner's yes (KIT-575).

    python3 scripts/pipeline_sprint_plan.py --selftest

A sprint is the plan's week (scripts/pipeline_sprint.py). On the first morning after the weekly reset,
the morning job (scripts/pipeline_morning.py) builds ONE proposal from what it already read for the
day: the pieces the pool admitted, in the queue's own order, as many as fit the week's budget, and
what does not fit and why. The stand-up posts it once, in the owner's DM. A reply that names the
sprint, `plan sprint 2026-11-11`, records the plan. Nothing else does.

THE FILE.  One JSON file (`pipeline-sprint-plan/1`), written whole by a rename. It holds the sprint
(its start, as the usage file gives it, and its first working day), the proposal, when it was posted,
and the plan once the owner said yes. A reader sees the old file or the new one.

NOTHING STARTS FROM THE PROPOSAL.  The pool decides who may start without the owner; this module only
READS its verdicts. A piece the pool did not admit is never offered. The yes records the proposal's
pieces as the sprint's plan, and the queue reads that file for one thing: a planned piece goes
before an unplanned one of the same admission. It admits nothing, and no reply changes nothing.

BUILT BY CODE.  Every sentence is fixed text with ids, numbers and dates put into it. A title is cleaned
as the status file cleans one. No model writes any of it.

NEVER GUESSED.  A budget that is not set, a usage file that cannot place the week, or an input the
morning job could not read gives no proposal and a line saying why. A sprint already proposed is not
proposed again.
"""
import argparse
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import pipeline_queue_order as order  # noqa: E402
import pipeline_sprint as sprint  # noqa: E402
import pipeline_status_local as status_job  # noqa: E402

SCHEMA = "pipeline-sprint-plan/1"
MAX_BYTES = 256 * 1024
MAX_PLANNED = 60                      # pieces a proposal carries
MAX_SHOWN = 12                        # pieces of each list the message shows; the rest are counted
TIME_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
REPLY_RE = re.compile(r"^plan sprint ([0-9]{4}-[0-9]{2}-[0-9]{2})\Z")
# Why the pool did not admit a piece, in plain words: its closed list (scripts/pipeline_queue_pool.py),
# each said without the pipeline's own terms. The selftest pins this equal to the pool's list.
WHY = {
    "malformed": "its record could not be read", "review-ticket": "it is a review",
    "other-team": "it is on a team not listed", "plan-ticket": "it is a plan itself",
    "planning-ticket": "it is a planning request", "marked-blocked": "it is marked blocked",
    "needs-person": "it needs a person", "hooks-change": "it changes a guarded path",
    "delegated": "an agent already has it", "running": "it is running", "finished": "it is finished",
    "wrong-state": "it is not in a ready state", "not-ready": "it is not ready to start",
    "plan-invalid": "its plan cannot be read", "plan-paused": "its plan is paused",
    "plan-waits": "it waits for another piece of its plan",
    "record-unreadable": "a record could not be read", "history-unreadable": "its history could not be read",
    "count-unknown": "today's count could not be read", "backlog-off": "the backlog is not on",
    "agent-off": "agent-filed work is off", "actor-unknown": "who moved it is not known",
    "not-owner": "someone else moved it", "changed": "it was changed after it was approved",
    "not-filed-by-owner": "you did not file it", "plan-not-approved": "its plan is not approved",
    "added-after-approval": "it was added after the plan was approved",
    "not-in-record": "it is not in the plan's record", "not-small": "it is not small",
    "guarded-path": "it touches a guarded path", "generation": "it is too far from a person's ticket",
    "daily-cap": "today's cap of agent-filed work is used",
}
NOT_PLANNED = ("over-budget", "no-size")


class SprintPlanError(Exception):
    """A proposal that cannot be made or a file that cannot be used, in words."""


def _iso(epoch):
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(text):
    return order.epoch(text) if isinstance(text, str) else None


def sprint_id(view):
    """The sprint's id: the usage file's week start, in UTC. It moves only when the plan's reset does."""
    return view["starts_at"].astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _money(value):
    value = round(float(value), 2)
    return ("%d" % value) if value == int(value) else ("%.2f" % value)


# --------------------------------------------------------------------------- #
# The proposal
# --------------------------------------------------------------------------- #
def propose(view, pieces, cfg, usage, now):
    """The proposal document, or raises SprintPlanError saying why there is none.
      view     scripts/pipeline_sprint.py's sprint_view, already known
      pieces   the pool's verdicts with the reader's extras merged in (title, effort, priority, ...)
      cfg      the queue's order config: `weekly_budget_usd` and `estimates`
      usage    the usage file (its `week.usd` is what the week has spent already)
      now      seconds since 1970"""
    if not view.get("known"):
        raise SprintPlanError("the sprint is not known: %s" % view.get("why"))
    budget = cfg.get("weekly_budget_usd")
    if not order.amount(budget) or budget <= 0:
        raise SprintPlanError("no weekly budget is set (weekly_budget_usd), so the week cannot be sized")
    week = usage.get("week") if isinstance(usage, dict) and isinstance(usage.get("week"), dict) else {}
    spent = week.get("usd")
    if not order.amount(spent) or spent < 0:
        raise SprintPlanError("what the week has spent so far could not be read from the usage file")
    estimates = cfg["estimates"]
    cleaned = [x for x in (order.clean(p) for p in pieces) if x]
    titles = dict((p.get("ticket"), p.get("title")) for p in pieces if isinstance(p, dict))
    dep = order.dependency_index(cleaned)
    ranked = sorted((p for p in cleaned if p["admitted"]), key=lambda p: order.sort_key(p, dep))
    left = budget - spent
    planned, other = [], []
    for p in ranked:
        usd = estimates.get(p["effort"]) if p["effort"] else None
        if usd is None:
            other.append({"ticket": p["ticket"], "why": "no-size", "reply": None})
        elif usd > left or len(planned) >= MAX_PLANNED:
            other.append({"ticket": p["ticket"], "why": "over-budget", "reply": None})
        else:
            left -= usd
            planned.append({"ticket": p["ticket"], "effort": p["effort"], "usd": usd,
                            "title": _title(titles.get(p["ticket"]))})
    for p in sorted((p for p in cleaned if not p["admitted"]), key=lambda p: order.sort_key(p, dep)):
        if p["reason"] in WHY:
            other.append({"ticket": p["ticket"], "why": p["reason"], "reply": p["reply"]})
    doc = {"schema": SCHEMA, "sprint": sprint_id(view), "first_day": view["first_day"].isoformat(),
           "resets_at": view["resets_at"].astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "proposed_at": _iso(now), "announced_at": None, "budget_usd": budget, "spent_usd": spent,
           "planned_usd": sum(x["usd"] for x in planned), "planned": planned, "not_planned": other,
           "plan": None}
    problems = check(doc)
    if problems:
        raise SprintPlanError("the proposal is not a valid sprint plan file: " + "; ".join(problems))
    return doc


def _title(text):
    return status_job.clean_title(text) if isinstance(text, str) and text.strip() else ""


# --------------------------------------------------------------------------- #
# The file: checked on the way in and out, written whole by a rename
# --------------------------------------------------------------------------- #
def check(doc):
    """What is wrong with a sprint plan document, as a list of plain sentences (empty when it is one)."""
    p = []
    if not isinstance(doc, dict):
        return ["the file holds no object"]
    keys = {"schema", "sprint", "first_day", "resets_at", "proposed_at", "announced_at", "budget_usd",
            "spent_usd", "planned_usd", "planned", "not_planned", "plan"}
    if set(doc) != keys:
        p.append("its keys are not the ones this version writes")
    if doc.get("schema") != SCHEMA:
        p.append("its schema is not %s" % SCHEMA)
    for k in ("sprint", "resets_at", "proposed_at"):
        if not (isinstance(doc.get(k), str) and TIME_RE.match(doc[k])):
            p.append("%s is not a time" % k)
    if not (isinstance(doc.get("first_day"), str) and DATE_RE.match(doc["first_day"])):
        p.append("first_day is not a date")
    if doc.get("announced_at") is not None and not (isinstance(doc["announced_at"], str)
                                                    and TIME_RE.match(doc["announced_at"])):
        p.append("announced_at is not a time or null")
    for k in ("budget_usd", "spent_usd", "planned_usd"):
        if not order.amount(doc.get(k)) or doc[k] < 0:
            p.append("%s is not a number" % k)
    planned = doc.get("planned")
    if not isinstance(planned, list) or len(planned) > MAX_PLANNED or not all(
            isinstance(x, dict) and set(x) == {"ticket", "effort", "usd", "title"}
            and isinstance(x["ticket"], str) and order.TICKET_RE.match(x["ticket"])
            and x["effort"] in order.EFFORTS and order.amount(x["usd"]) and isinstance(x["title"], str)
            for x in planned):
        p.append("planned is not a list of pieces")
    other = doc.get("not_planned")
    if not isinstance(other, list) or not all(
            isinstance(x, dict) and set(x) == {"ticket", "why", "reply"}
            and isinstance(x["ticket"], str) and order.TICKET_RE.match(x["ticket"])
            and (x["why"] in NOT_PLANNED or x["why"] in WHY)
            and (x["reply"] is None or (isinstance(x["reply"], str) and order.REPLY_RE.match(x["reply"])))
            for x in other):
        p.append("not_planned is not a list of pieces with a reason")
    plan = doc.get("plan")
    if plan is not None and not (isinstance(plan, dict) and set(plan) == {"recorded_at", "tickets"}
                                 and isinstance(plan["recorded_at"], str) and TIME_RE.match(plan["recorded_at"])
                                 and isinstance(plan["tickets"], list)
                                 and all(isinstance(t, str) and order.TICKET_RE.match(t) for t in plan["tickets"])):
        p.append("plan is not null or a recorded plan")
    return p


def load(path):
    """(doc, None) for a valid file, (None, None) when there is none, (None, why) when it cannot be used."""
    try:
        if os.path.getsize(path) > MAX_BYTES:
            return None, "the sprint plan file is larger than %d bytes" % MAX_BYTES
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError, RecursionError):
        return None, "the sprint plan file could not be read"
    problems = check(doc)
    return (None, "the sprint plan file is not usable: %s" % "; ".join(problems)) if problems else (doc, None)


def save(doc, path):
    """Checks the document, then writes it whole: a temporary file in the same folder, renamed over the old."""
    problems = check(doc)
    if problems:
        raise SprintPlanError("refusing to write a sprint plan that is not one: %s" % "; ".join(problems))
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, ".%s.%d.tmp" % (os.path.basename(path), os.getpid()))
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise SprintPlanError("the sprint plan file could not be written (%s)" % (exc.strerror or "error"))


def wants_proposal(path, view):
    """(True, None) when this sprint has no proposal yet; (False, why) when it has one or the file is unusable.
    An unusable file is never planned over: the owner's yes may be in it."""
    doc, why = load(path)
    if why:
        return False, why
    if doc is not None and doc["sprint"] == sprint_id(view):
        return False, "this sprint was proposed at %s" % doc["proposed_at"]
    return True, None


# --------------------------------------------------------------------------- #
# The message: fixed words, ids, numbers and dates
# --------------------------------------------------------------------------- #
def _safe(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def reply_for(doc):
    return "plan sprint %s" % doc["first_day"]


def text_of(doc):
    first = datetime.date.fromisoformat(doc["first_day"])
    lines = ["*The sprint plan for the week starting %s*" % first.strftime("%a %d %b"),
             "The week's budget is $%s and $%s is spent, so there is room for $%s. The plan holds %d piece%s, $%s of "
             "estimates." % (_money(doc["budget_usd"]), _money(doc["spent_usd"]),
                             _money(max(doc["budget_usd"] - doc["spent_usd"], 0)), len(doc["planned"]),
                             "" if len(doc["planned"]) == 1 else "s", _money(doc["planned_usd"]))]
    if doc["planned"]:
        lines.append("In the plan, in order:")
        for x in doc["planned"][:MAX_SHOWN]:
            lines.append("• %s (%s, $%s)%s" % (x["ticket"], x["effort"], _money(x["usd"]),
                                              ": " + _safe(x["title"]) if x["title"] else ""))
        if len(doc["planned"]) > MAX_SHOWN:
            lines.append("and %d more." % (len(doc["planned"]) - MAX_SHOWN))
    else:
        lines.append("No piece is ready to plan.")
    if doc["not_planned"]:
        lines.append("Not in the plan:")
        for x in doc["not_planned"][:MAX_SHOWN]:
            why = {"over-budget": "it does not fit the week's budget",
                   "no-size": "it has no size yet, so it has no estimate"}.get(x["why"]) or WHY[x["why"]]
            lines.append("• %s: %s%s" % (x["ticket"], why, " (reply `%s` to start it)" % x["reply"] if x["reply"] else ""))
        if len(doc["not_planned"]) > MAX_SHOWN:
            lines.append("and %d more." % (len(doc["not_planned"]) - MAX_SHOWN))
    lines.append("Reply `%s` to plan the sprint this way. Nothing starts from this message alone, and with no reply "
                 "nothing changes." % reply_for(doc))
    return "\n".join(lines)


def unknown_text(why):
    """What the owner is told when no proposal could be made: the reason, never a guess."""
    return "There is no sprint plan this week: %s." % _safe(why)


# --------------------------------------------------------------------------- #
# The yes
# --------------------------------------------------------------------------- #
def record_yes(path, date, now):
    """The owner's reply, `plan sprint <first working day>`. Returns (state, words): state is `recorded`,
    `already` or `refused`. Only the proposal's own pieces are recorded, and only for the sprint the date
    names while it is still current."""
    doc, why = load(path)
    if why:
        return "refused", "I can't record a sprint plan: %s." % _safe(why)
    if doc is None or doc["first_day"] != date:
        return "refused", ("I have no sprint plan to record for the week starting %s."
                           % (_safe(date) if DATE_RE.match(date or "") else "that date")
                           + (" The one I proposed starts %s." % doc["first_day"] if doc else ""))
    if doc["announced_at"] is None:
        return "refused", "I have not sent you that plan yet, so there is nothing to record."
    if doc["plan"] is not None:
        return "already", "That sprint is already planned (recorded %s). Nothing changed." % doc["plan"]["recorded_at"]
    ends = _epoch(doc["resets_at"])
    if ends is None or now >= ends:
        return "refused", "That sprint is over, so I did not record it."
    doc["plan"] = {"recorded_at": _iso(now), "tickets": [x["ticket"] for x in doc["planned"]]}
    try:
        save(doc, path)
    except SprintPlanError as exc:
        return "refused", "I could not record the plan: %s." % _safe(str(exc))
    return "recorded", ("Recorded: the sprint starting %s is planned with %d piece%s. The queue will put them "
                        "first; nothing starts that was not already allowed to." % (
                            doc["first_day"], len(doc["plan"]["tickets"]),
                            "" if len(doc["plan"]["tickets"]) == 1 else "s"))


def planned_tickets(path, week_start):
    """The recorded plan's tickets for the sprint the usage file's week start names, else []. A file that
    cannot be used, another sprint's plan, or no plan recorded reads as no plan: the queue then works as it did."""
    doc, why = load(path)
    if why or doc is None or doc["sprint"] != week_start or doc["plan"] is None:
        return []
    return list(doc["plan"]["tickets"])


def planned_for_usage(path, usage):
    """The recorded plan's tickets for the sprint the usage file names now, else []. The one place the morning job
    and the queue's starter ask: a path that is empty, a week the file cannot place, or anything the file will not
    say reads as no plan, so the order is what it was."""
    week = usage.get("week") if isinstance(usage, dict) and isinstance(usage.get("week"), dict) else {}
    if not path or week.get("known") is not True or not isinstance(week.get("starts_at"), str):
        return []
    return planned_tickets(path, week["starts_at"])


def announce(path, now):
    """Marks the proposal as sent (after it was posted). Raises SprintPlanError when the file cannot be written."""
    doc, why = load(path)
    if why or doc is None:
        raise SprintPlanError(why or "there is no sprint plan file to mark")
    doc["announced_at"] = _iso(now)
    save(doc, path)


def release(path):
    """Puts a proposal back to unsent, after a post that failed. Raises SprintPlanError when it cannot."""
    doc, why = load(path)
    if why or doc is None:
        raise SprintPlanError(why or "there is no sprint plan file to put back")
    doc["announced_at"] = None
    save(doc, path)


def due_announcement(path, now):
    """(doc, None) when a proposal waits to be sent, (None, None) when none does, (None, why) when the file is
    unusable. One whose sprint is over is not sent."""
    doc, why = load(path)
    if why:
        return None, why
    if doc is None or doc["announced_at"] is not None:
        return None, None
    ends = _epoch(doc["resets_at"])
    return (doc, None) if ends is not None and now < ends else (None, None)


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(description="Sprint planning at the reset.")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        from scratch_run import contained
        return contained(_selftest, "sprint-plan-selftest-run-")
    ap.print_usage()
    return 2


def _selftest():
    import copy
    import tempfile
    import pipeline_queue_pool as pool
    import pipeline_bridge as bridge
    failures, total = [], [0]

    def check_(name, got, want):
        total[0] += 1
        if got != want:
            failures.append("FAIL %s: got %r, want %r" % (name, str(got)[:300], str(want)[:300]))
    tz = datetime.timezone(datetime.timedelta(hours=-5))
    days = ["mon", "tue", "wed", "thu", "fri", "sat"]
    usage = {"result": "ok", "week": {"known": True, "starts_at": "2026-11-10T18:00:00Z",
                                      "resets_at": "2026-11-17T18:00:00Z", "usd": 10.0}}
    at = lambda d, h: datetime.datetime(2026, 11, d, h, tzinfo=tz)      # noqa: E731
    view = sprint.sprint_view(usage, at(11, 8), days, tz=tz)
    check_("the sprint reader places the test week", (view["known"], view["first_day"].isoformat()), (True, "2026-11-11"))
    now = at(11, 8).timestamp()
    cfg = {"weekly_budget_usd": 28, "estimates": {"S": 2.0, "M": 4.0, "L": 8.0}}

    def piece(n, effort="M", admitted=True, reason="admitted", prio=3, reply=None, title=None):
        return {"ticket": "KIT-%d" % n, "id": "id%d" % n, "admitted": admitted, "reason": reason, "reply": reply,
                "source": "todo", "plan": None, "by": None, "at": "2026-11-09T10:00:00Z", "priority": prio,
                "effort": effort, "repo": "o/r", "title": title if title is not None else "Piece %d" % n}
    pieces = [piece(1, "S", prio=1), piece(2, "M", prio=2), piece(3, "L", prio=3, title="A <b>big</b> & bold one"),
              piece(4, "L", prio=4), piece(5, None, prio=4), piece(6, "S", admitted=False, reason="not-ready", reply="start KIT-6"),
              piece(7, "S", admitted=False, reason="delegated")]
    doc = propose(view, pieces, cfg, usage, now)
    check_("the proposal fits the pieces by the queue's order inside the room left (28 - 10 spent)",
           ([x["ticket"] for x in doc["planned"]], doc["planned_usd"]), (["KIT-1", "KIT-2", "KIT-3"], 14.0))
    check_("what does not fit says why, and a held piece keeps its exact reply",
           [(x["ticket"], x["why"], x["reply"]) for x in doc["not_planned"]],
           [("KIT-4", "over-budget", None), ("KIT-5", "no-size", None),
            ("KIT-6", "not-ready", "start KIT-6"), ("KIT-7", "delegated", None)])
    big = propose(view, pieces, dict(cfg, weekly_budget_usd=16), usage, now)
    check_("a smaller week plans fewer pieces and says the rest do not fit",
           ([x["ticket"] for x in big["planned"]], [x["why"] for x in big["not_planned"] if x["ticket"] in ("KIT-3", "KIT-4")]),
           (["KIT-1", "KIT-2"], ["over-budget", "over-budget"]))
    check_("a piece with no effort is not sized, and is said so",
           [x["why"] for x in doc["not_planned"] if x["ticket"] == "KIT-5"], ["no-size"])
    check_("a piece the pool did not admit is never planned, however high its priority",
           all(t not in [x["ticket"] for x in doc["planned"]] for t in ("KIT-6", "KIT-7")), True)
    text = text_of(doc)
    check_("the message names the budget, the room, the plan, what is out, the one reply and that nothing starts alone",
           ("$28" in text, "$10" in text, "$18" in text, "KIT-1 (S, $2)" in text, "KIT-4: it does not fit" in text,
            "KIT-6: it is not ready to start (reply `start KIT-6` to start it)" in text, "`plan sprint 2026-11-11`" in text,
            "Nothing starts from this message alone" in text), (True,) * 8)
    check_("a title with angle brackets and an ampersand is cleaned as the status file cleans one, and shows safe",
           ("A b big /b bold one" in text and "<b>" not in text and "&lt;b" not in text and _safe("a<b>&") == "a&lt;b&gt;&amp;"), True)
    banned = sorted(set(w.lower() for w in bridge.BANNED_WORDS_RE.findall(text + text_of(big) + unknown_text("x") + " ".join(WHY.values()))))
    check_("no word the owner's DM keeps out", banned, [])
    check_("every reason the pool can give has a plain phrase, and no other", sorted(WHY), sorted(r for r in pool.REASONS if r != "admitted"))
    for label, over, needle in (("no budget", {"weekly_budget_usd": None}, "no weekly budget"),
                                ("a budget of zero", {"weekly_budget_usd": 0}, "no weekly budget")):
        try:
            propose(view, pieces, dict(cfg, **over), usage, now)
            got = "proposed"
        except SprintPlanError as exc:
            got = str(exc)
        check_("%s: no proposal, and it says so" % label, needle in got, True)
    for label, u in (("a week the file cannot place", {"result": "ok", "week": {"known": False}}),
                     ("a usage file with no week spend", {"result": "ok", "week": dict(usage["week"], usd=None)}),
                     ("a spend that is a string", {"result": "ok", "week": dict(usage["week"], usd="9")})):
        v = sprint.sprint_view(u, at(11, 8), days, tz=tz)
        try:
            propose(v, pieces, cfg, u, now)
            got = "proposed"
        except SprintPlanError as exc:
            got = str(exc)
        check_("%s: no proposal" % label, got != "proposed", True)
    # the file: valid in, valid out, refused when malformed
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "q", "sprint-plan.json")
        check_("no file is no proposal yet, and one is wanted", (load(path), wants_proposal(path, view)), (((None, None)), (True, None)))
        save(doc, path)
        check_("the saved file loads as it was", load(path)[0] == doc, True)
        check_("a sprint already proposed is not proposed again", wants_proposal(path, view)[0], False)
        check_("…but the next sprint is",
               wants_proposal(path, dict(view, starts_at=view["starts_at"] + datetime.timedelta(days=7)))[0], True)
        bad = copy.deepcopy(doc)
        bad["planned"][0]["effort"] = "XL"
        try:
            save(bad, path)
            got = "written"
        except SprintPlanError:
            got = "refused"
        check_("a document that is not a sprint plan is never written", (got, load(path)[0] == doc), ("refused", True))
        check_("no temporary file is left in the folder", sorted(os.listdir(os.path.dirname(path))), ["sprint-plan.json"])
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        check_("a file that is not JSON is never planned over: the yes may be in it",
               (wants_proposal(path, view)[0], load(path)[1]), (False, "the sprint plan file could not be read"))
        extra = dict(doc, extra=1)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(extra, fh)
        check_("a file with a key this version does not write is unusable", "keys are not" in (load(path)[1] or ""), True)
        save(doc, path)
        # announcing
        check_("a proposal not yet sent is due, and one past its sprint's end is not",
               (due_announcement(path, now)[0] is not None, due_announcement(path, _epoch(doc["resets_at"]) + 1)[0]), (True, None))
        announce(path, now)
        check_("once announced it is not due again", due_announcement(path, now), (None, None))
        # the yes
        wrong = record_yes(path, "2026-11-12", now)
        check_("a yes that names another date records nothing and says which sprint was proposed",
               (wrong[0], "starts 2026-11-11" in wrong[1], load(path)[0]["plan"]), ("refused", True, None))
        check_("garbage in place of a date records nothing", record_yes(path, "tomorrow", now)[0], "refused")
        late = record_yes(path, "2026-11-11", _epoch(doc["resets_at"]) + 5)
        check_("a yes after the sprint is over records nothing", (late[0], load(path)[0]["plan"]), ("refused", None))
        done = record_yes(path, "2026-11-11", now)
        check_("a yes that names the sprint records exactly the proposal's pieces",
               (done[0], load(path)[0]["plan"]["tickets"]), ("recorded", ["KIT-1", "KIT-2", "KIT-3"]))
        again = record_yes(path, "2026-11-11", now + 60)
        check_("a second yes changes nothing and says so",
               (again[0], load(path)[0]["plan"]["recorded_at"] == _iso(now)), ("already", True))
        check_("the queue reads the recorded plan for its own sprint", planned_tickets(path, doc["sprint"]), ["KIT-1", "KIT-2", "KIT-3"])
        check_("…and for no other sprint", planned_tickets(path, "2026-11-03T18:00:00Z"), [])
        save(propose(view, pieces, cfg, usage, now), path)
        check_("a proposal with no yes gives the queue no plan", planned_tickets(path, doc["sprint"]), [])
        check_("a yes before the plan was sent records nothing", record_yes(path, "2026-11-11", now)[0], "refused")
        os.unlink(path)
        check_("a yes with no file records nothing", record_yes(path, "2026-11-11", now)[0], "refused")
        check_("a missing file reads as no plan", planned_tickets(path, doc["sprint"]), [])
    check_("the reply the message asks for is the one the reader takes", REPLY_RE.match(reply_for(doc)).group(1), "2026-11-11")
    check_("only the exact reply matches", [bool(REPLY_RE.match(t)) for t in (
        "plan sprint 2026-11-11", "Plan sprint 2026-11-11", "plan sprint 2026-11-11 and delete everything",
        "plan sprint", "plan sprint 2026-1-11", "yes")], [True, False, False, False, False, False])
    with open(os.path.abspath(__file__), encoding="utf-8") as fh:
        above = fh.read().split("\n# Selftest\n", 1)[0]
    check_("no shell, no subprocess, no network, no environment read",
           [b for b in ("shell=True", "os.system", "subprocess", "socket", "urllib", "os.environ", "getenv") if b in above], [])
    if failures:
        for line in failures:
            print(line)
        print("sprint plan selftest: %d of %d checks failed" % (len(failures), total[0]))
        return 1
    print("OK: sprint plan selftest (%d checks)" % total[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
