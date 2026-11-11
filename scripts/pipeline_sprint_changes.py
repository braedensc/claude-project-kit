#!/usr/bin/env python3
"""Small configuration changes the sprint review proposes, each needing the owner's own yes (KIT-577).

    python3 scripts/pipeline_sprint_changes.py show --changes FILE
    python3 scripts/pipeline_sprint_changes.py apply --changes FILE --queue-config FILE KEY VALUE SPRINT_DATE [--yes]
    python3 scripts/pipeline_sprint_changes.py --selftest

WHAT IT DOES.  When the sprint review is built (scripts/pipeline_sprint_review.py), this module looks at what the
sprint spent against what it shipped and proposes AT MOST THREE changes to the queue's settings. Each is one fixed
question in the owner's DM, built by code from the key and the two values. Only the owner's exact reply,
`apply KEY VALUE SPRINT_DATE`, in the DM, makes a change, through apply_yes below; the same function backs the command
line, which shows the old and the new value and changes nothing without --yes.

WHAT IT NEVER DOES.
  - It proposes only a key in ALLOWED, a short list of the queue's own limits. Models per role, a grader and a guard are
    not on it, so they cannot be proposed, whatever a rule says.
  - It proposes only to TIGHTEN: a lower planned load, one lane fewer. It never raises a budget, a load or a lane count,
    so it cannot be made to spend more. Loosening a limit is the owner's own edit.
  - It changes nothing by itself. A proposal is a line in a file; a setting changes only on the owner's exact reply.
  - It writes nothing but the queue's settings file (and its own proposals file), and only the one key named.
  - No model writes a word of it: the rules and every sentence are fixed text with numbers put in.

THE CATCH, SAID EVERY TIME.  The queue's settings file is written from queue.conf when the queue is installed. A change
made here lasts until the next install; the reply says so and names the key in queue.conf to change as well.
"""
import argparse
import datetime
import json
import os
import re
import stat
import sys
import tempfile

SCHEMA = "pipeline-sprint-changes/1"
FILE_NAME = "changes.json"
MAX_PROPOSALS = 3
LIFETIME_DAYS = 10             # a proposal not answered in this long is too old to apply
# The only keys a review may propose, where each lives in the queue's settings file, and the queue.conf key that
# installs it. Every one is a limit on how much the queue starts, so a change here can only be to start less.
ALLOWED = {"planned_load": ("PLANNED_LOAD", 1, None),       # (queue.conf key, lowest value, whole numbers only: None)
           "lanes_per_repo": ("LANES_PER_REPO", 1, 3)}      # (…, lowest, highest)
QUEUE_SECTION = "order"
DATE_RE = re.compile(r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}\Z")
REPLY_RE = re.compile(r"^apply ([a-z_]{1,30}) ([0-9]{1,4}(?:\.[0-9]{1,2})?) (20[0-9]{2}-[0-9]{2}-[0-9]{2})\Z")
OVER_PLAN_SHARE = 1.0          # rule 1: the sprint spent more than the plan
NOTHING_SHIPPED_SHARE = 0.25   # rule 2: it spent at least this share of the plan and no pull request merged
TIGHTEN_TO = 0.75              # a lower planned load is this share of the old one


class ChangesError(Exception):
    """Something that stops this module, said in words that quote no value."""


def _iso(dt):
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(text):
    try:
        return datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return None


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and 0 <= v < 10 ** 6


def fmt(v):
    v = round(float(v), 2)
    return ("%d" % v) if v == int(v) else ("%.2f" % v).rstrip("0")


def money(v):
    v = round(float(v), 2)
    return ("%d" % v) if v == int(v) else ("%.2f" % v)


def changes_path(review_dir):
    return os.path.join(review_dir, FILE_NAME)


# --------------------------------------------------------------------------- #
# The queue's settings: read, and the one write
# --------------------------------------------------------------------------- #
def read_order(path):
    """({key: number}, why): the allowed keys' current values in the queue's settings file. `why` says it could not be
    read; the review then proposes nothing and says so."""
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None, "the queue's settings file could not be read"
    order = doc.get(QUEUE_SECTION) if isinstance(doc, dict) else None
    if not isinstance(order, dict):
        return None, "the queue's settings file has no %r section" % QUEUE_SECTION
    values = {}
    for key, (_conf, low, high) in ALLOWED.items():
        v = order.get(key)
        if not _num(v) or v < low or (high is not None and (v > high or v != int(v))):
            return None, "the queue's settings file gives no usable %s" % key
        values[key] = v
    return values, None


def _write_key(path, key, new):
    """Sets order[key] to `new` in the queue's settings file and nothing else: the file is read whole, changed in
    memory, written beside itself and moved into place, with the old file's mode. A link is refused."""
    path = os.path.expanduser(path)
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode):
        raise ChangesError("the queue's settings file is not a plain file")
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    if not isinstance(doc.get(QUEUE_SECTION), dict) or key not in ALLOWED:
        raise ChangesError("the queue's settings file has no %r section" % QUEUE_SECTION)
    doc[QUEUE_SECTION][key] = new
    fd, tmp = tempfile.mkstemp(prefix=".changes-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.chmod(tmp, stat.S_IMODE(st.st_mode))
        os.replace(tmp, path)
    except OSError:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# --------------------------------------------------------------------------- #
# Proposing
# --------------------------------------------------------------------------- #
def propose(spent, budget, merged, order, partial=False):
    """[{"key", "old", "new", "because"}], at most MAX_PROPOSALS, each a tightening of an allowed key, from numbers the
    review already read. `merged` is the pull requests merged in the sprint; `partial` says the count may be short, so
    a rule that rests on 'none merged' stays quiet. Nothing is proposed from a figure that was not given."""
    out = []
    if not _num(spent) or not _num(budget) or budget <= 0 or order is None:
        return out
    load, lanes = order["planned_load"], order["lanes_per_repo"]

    def lower_load(because):
        new = max(ALLOWED["planned_load"][1], int(load * TIGHTEN_TO))
        if new < load and not any(c["key"] == "planned_load" for c in out):
            out.append({"key": "planned_load", "old": load, "new": new, "because": because})
    share = spent / budget
    if share > OVER_PLAN_SHARE:
        lower_load("The sprint spent $%s of the $%s planned (%d%%)." % (money(spent), money(budget), round(100 * share)))
    if not partial and merged == 0 and share >= NOTHING_SHIPPED_SHARE:
        because = "The sprint spent $%s of the $%s planned and no pull request merged." % (money(spent), money(budget))
        if lanes > ALLOWED["lanes_per_repo"][1]:
            out.append({"key": "lanes_per_repo", "old": lanes, "new": lanes - 1, "because": because})
        else:
            lower_load(because)
    return out[:MAX_PROPOSALS]


def question(first_day, n, total, c):
    """The one fixed question for a proposal, built from the key and the two values."""
    conf = ALLOWED[c["key"]][0]
    return ("Sprint change %d of %d: lower %s from %s to %s. %s Reply `apply %s %s %s` to make this change in the "
            "queue's settings; nothing changes without that exact reply. The queue is installed from queue.conf, so the "
            "next install puts %s back unless you change %s there too."
            % (n, total, c["key"], fmt(c["old"]), fmt(c["new"]), c["because"], c["key"], fmt(c["new"]), first_day,
               fmt(c["old"]), conf))


def section_lines(doc, why):
    """The review document's lines on the proposals. `doc` is None when none were made."""
    if why:
        return ["No configuration change is proposed: %s." % why]
    if not doc or not doc["proposals"]:
        return ["No configuration change is proposed this sprint."]
    lines = ["Each is a separate question in your DM, and none changes anything without your exact reply:", ""]
    for c in doc["proposals"]:
        lines.append("- `%s`: %s to %s. %s" % (c["key"], fmt(c["old"]), fmt(c["new"]), c["because"]))
    return lines


# --------------------------------------------------------------------------- #
# The proposals file
# --------------------------------------------------------------------------- #
def new_doc(first_day, now, proposals):
    return {"schema": SCHEMA, "first_day": first_day, "made_at": _iso(now),
            "proposals": [dict(c, sent_at=None, applied_at=None) for c in proposals]}


def valid_doc(doc):
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA or not isinstance(doc.get("first_day"), str) \
            or not DATE_RE.match(doc["first_day"]) or _parse(doc.get("made_at")) is None \
            or not isinstance(doc.get("proposals"), list) or len(doc["proposals"]) > MAX_PROPOSALS:
        return False
    for c in doc["proposals"]:
        if not isinstance(c, dict) or c.get("key") not in ALLOWED or not _num(c.get("old")) or not _num(c.get("new")) \
                or not isinstance(c.get("because"), str) or not c["new"] < c["old"]:
            return False
        for stamp in ("sent_at", "applied_at"):
            if c.get(stamp) is not None and _parse(c.get(stamp)) is None:
                return False
    return True


def load(path):
    """(doc, why): the proposals file, or why it cannot be used. A missing file is no proposals, not a fault."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError):
        return None, "the sprint changes file cannot be read"
    if not valid_doc(doc):
        return None, "the sprint changes file is not in the shape this version writes"
    return doc, None


def save(doc, path):
    if not valid_doc(doc):
        raise ChangesError("the sprint changes file would not be valid, so it was not written")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".changes-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError as exc:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise ChangesError("the sprint changes file could not be written (%s)" % type(exc).__name__)


def unsent(doc):
    """[(index, question text)] for each proposal not yet sent, in order."""
    if not doc:
        return []
    total = len(doc["proposals"])
    return [(i, question(doc["first_day"], i + 1, total, c)) for i, c in enumerate(doc["proposals"])
            if c["sent_at"] is None and c["applied_at"] is None]


def mark_sent(path, index, now, sent=True):
    """Records that a proposal was sent, BEFORE it is posted, and puts the mark back (sent=False) when the post fails."""
    doc, why = load(path)
    if why or not doc or index >= len(doc["proposals"]):
        raise ChangesError(why or "the sprint changes file has no such proposal")
    doc["proposals"][index]["sent_at"] = _iso(now) if sent else None
    save(doc, path)


# --------------------------------------------------------------------------- #
# The yes
# --------------------------------------------------------------------------- #
def apply_yes(path, queue_path, key, value, date, now, dry=False):
    """(state, words): the owner's exact reply `apply KEY VALUE DATE`. It makes the change only when a proposal for that
    key, value and sprint was sent and is not too old, the key is allowed, the value is still a tightening of what the
    queue's settings now say, and those settings are still what the proposal read. `dry` shows the old and the new value
    and writes nothing. Fixed words; the old and the new value are always in them."""
    doc, why = load(path)
    if why:
        return "unusable", "I can't use the sprint changes file (%s), so nothing was changed." % why
    if doc is None or doc["first_day"] != date:
        return "none", "I proposed no change for the sprint starting %s, so nothing was changed." % date
    if key not in ALLOWED:
        return "refused", "%s is not a setting a sprint review may change, so nothing was changed." % key[:30]
    try:
        new = float(value)
    except ValueError:
        return "refused", "That is not a number, so nothing was changed."
    found = [(i, c) for i, c in enumerate(doc["proposals"]) if c["key"] == key and c["new"] == new]
    if not found:
        return "none", "I did not propose %s %s for the sprint starting %s, so nothing was changed." % (key, fmt(new), date)
    i, c = found[0]
    if c["applied_at"] is not None:
        return "already", "%s was already changed from %s to %s; nothing more was done." % (key, fmt(c["old"]), fmt(c["new"]))
    if c["sent_at"] is None:
        return "unsent", "I have not sent you that question yet, so nothing was changed."
    made = _parse(doc["made_at"])
    if now - made > datetime.timedelta(days=LIFETIME_DAYS):
        return "old", "That proposal is more than %d days old, so nothing was changed." % LIFETIME_DAYS
    current, why = read_order(queue_path)
    if why:
        return "unusable", "%s%s, so nothing was changed." % (why[0].upper(), why[1:])
    if current[key] != c["old"]:
        return "stale", ("%s is %s now, not the %s I read when I proposed this, so nothing was changed. "
                         "Ask for a new review if you still want it." % (key, fmt(current[key]), fmt(c["old"])))
    if not new < current[key] or new < ALLOWED[key][1]:
        return "refused", "%s %s would not be a lower setting than %s, so nothing was changed." % (key, fmt(new), fmt(current[key]))
    shows = "%s: %s -> %s" % (key, fmt(current[key]), fmt(new))
    if dry:
        return "dry", "Would change %s in the queue's settings. Nothing was changed." % shows
    doc["proposals"][i]["applied_at"] = _iso(now)
    try:
        save(doc, path)                     # the record first: a change that cannot be recorded is not made
        _write_key(queue_path, key, int(new) if new == int(new) else new)
    except (ChangesError, OSError, ValueError) as exc:
        doc["proposals"][i]["applied_at"] = None
        try:
            save(doc, path)
        except ChangesError:
            pass
        return "failed", "I could not change %s (%s), so it is as it was." % (key, type(exc).__name__)
    return "applied", ("Changed %s in the queue's settings. The queue reads it on its next pass. The next queue install "
                       "puts %s back unless you also set %s=%s in queue.conf."
                       % (shows, fmt(c["old"]), ALLOWED[key][0], fmt(new)))


# --------------------------------------------------------------------------- #
# Command line
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="command")
    sh = sub.add_parser("show")
    sh.add_argument("--changes", required=True)
    ap_ = sub.add_parser("apply")
    ap_.add_argument("--changes", required=True)
    ap_.add_argument("--queue-config", required=True)
    ap_.add_argument("--yes", action="store_true", help="make the change; without it the old and new value are shown only")
    ap_.add_argument("key")
    ap_.add_argument("value")
    ap_.add_argument("date")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    if args.command == "show":
        doc, why = load(os.path.expanduser(args.changes))
        if why or not doc:
            print(why or "No proposals.")
            return 0 if not why else 1
        for n, c in enumerate(doc["proposals"], 1):
            print("%d. %s: %s -> %s. %s%s" % (n, c["key"], fmt(c["old"]), fmt(c["new"]), c["because"],
                                              " (applied)" if c["applied_at"] else " (sent)" if c["sent_at"] else ""))
        return 0
    if args.command == "apply":
        state, words = apply_yes(os.path.expanduser(args.changes), os.path.expanduser(args.queue_config), args.key,
                                 args.value, args.date, datetime.datetime.now(datetime.timezone.utc), dry=not args.yes)
        print(words)
        return 0 if state in ("applied", "dry", "already") else 1
    ap.print_help()
    return 2


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
def _selftest():
    failures, total = [], [0]

    def check(name, got, want):
        total[0] += 1
        if got != want:
            failures.append("FAIL %s: got %r, want %r" % (name, str(got)[:300], str(want)[:300]))
    now = datetime.datetime(2026, 11, 17, 21, 0, tzinfo=datetime.timezone.utc)
    ORDER = {"planned_load": 40, "lanes_per_repo": 2}
    # the rules
    over = propose(130, 100, 5, ORDER)
    check("over the plan: one lower planned load, with the numbers", [(c["key"], c["old"], c["new"]) for c in over],
          [("planned_load", 40, 30)])
    check("…and its reason states them", over[0]["because"], "The sprint spent $130 of the $100 planned (130%).")
    check("on the plan or under it: nothing", (propose(100, 100, 5, ORDER), propose(40, 100, 9, ORDER)), ([], []))
    none = propose(40, 100, 0, ORDER)
    check("spent a quarter of the plan and nothing merged: one lane fewer", [(c["key"], c["old"], c["new"]) for c in none],
          [("lanes_per_repo", 2, 1)])
    check("…and with one lane already, the planned load goes down instead",
          [(c["key"], c["new"]) for c in propose(40, 100, 0, {"planned_load": 40, "lanes_per_repo": 1})], [("planned_load", 30)])
    check("under a quarter of the plan with nothing merged: nothing", propose(20, 100, 0, ORDER), [])
    check("a count that may be short never rests a rule on 'none merged'", propose(60, 100, 0, ORDER, partial=True), [])
    both = propose(130, 100, 0, {"planned_load": 40, "lanes_per_repo": 1})
    check("two rules naming the same key propose it once", [(c["key"], c["new"]) for c in both], [("planned_load", 30)])
    check("over the plan and nothing merged with two lanes: the load and a lane, two proposals",
          sorted(c["key"] for c in propose(130, 100, 0, ORDER)), ["lanes_per_repo", "planned_load"])
    check("a planned load of one cannot go lower", propose(130, 100, 5, {"planned_load": 1, "lanes_per_repo": 1}), [])
    check("a figure that was not given proposes nothing", (propose(None, 100, 5, ORDER), propose(130, None, 5, ORDER),
                                                           propose(130, 0, 5, ORDER), propose(130, 100, 5, None),
                                                           propose(True, 100, 5, ORDER)), ([], [], [], [], []))
    check("every proposal is a tightening of an allowed key, and at most three",
          all(c["new"] < c["old"] and c["key"] in ALLOWED for c in propose(130, 100, 0, ORDER)) and MAX_PROPOSALS == 3, True)
    saved_max = MAX_PROPOSALS
    globals()["MAX_PROPOSALS"] = 1
    try:
        capped = propose(130, 100, 0, ORDER)
    finally:
        globals()["MAX_PROPOSALS"] = saved_max
    check("the number of proposals is capped at the limit, whatever the rules give", len(capped), 1)
    check("the allowed list is short and holds no model, grader or guard key",
          (sorted(ALLOWED), [k for k in ALLOWED if "model" in k or "grader" in k or "guard" in k]), (["lanes_per_repo", "planned_load"], []))
    # the question
    c0 = {"key": "planned_load", "old": 40, "new": 30, "because": "The sprint spent $130 of the $100 planned (130%)."}
    q = question("2026-11-10", 1, 2, c0)
    check("the question is fixed words with the key and the two values, and says what the reply is",
          (q.startswith("Sprint change 1 of 2: lower planned_load from 40 to 30. "), "Reply `apply planned_load 30 2026-11-10`" in q,
           "puts 40 back unless you change PLANNED_LOAD there too" in q), (True, True, True))
    check("the reply it asks for matches the reply reader", bool(REPLY_RE.match("apply planned_load 30 2026-11-10")), True)
    for bad in ("apply planned_load 30 2026-11-10 please", " apply planned_load 30 2026-11-10x", "Apply planned_load 30 2026-11-10",
                "apply planned_load 30", "apply planned_load -30 2026-11-10", "apply planned_load 30 2026-11-1"):
        check("the reply reader refuses %r" % bad, bool(REPLY_RE.match(bad)), False)
    with tempfile.TemporaryDirectory() as tmp:
        qpath = os.path.join(tmp, "queue.json")
        cpath = os.path.join(tmp, "rev", FILE_NAME)

        def put_queue(order=None, extra=True):
            with open(qpath, "w", encoding="utf-8") as fh:
                json.dump({"order": dict(ORDER if order is None else order, day_stop=60), "other": {"keep": [1, 2]}} if extra else {}, fh)
            os.chmod(qpath, 0o640)

        def doc_(**kw):
            d = new_doc("2026-11-10", now - datetime.timedelta(days=1), over + none)
            for c in d["proposals"]:
                c["sent_at"] = _iso(now - datetime.timedelta(hours=20))
            for k, v in kw.items():
                d[k] = v
            return d
        put_queue()
        check("the queue's settings are read for the allowed keys only", read_order(qpath), ({"planned_load": 40, "lanes_per_repo": 2}, None))
        for label, order_ in (("a lane count of 9", {"planned_load": 40, "lanes_per_repo": 9}),
                              ("a fractional lane count", {"planned_load": 40, "lanes_per_repo": 1.5}),
                              ("a planned load that is text", {"planned_load": "40", "lanes_per_repo": 1}),
                              ("a planned load that is a boolean", {"planned_load": True, "lanes_per_repo": 1}),
                              ("no lanes key", {"planned_load": 40})):
            put_queue(order_)
            check("read: %s is not usable" % label, read_order(qpath)[0], None)
        put_queue(extra=False)
        check("read: a file with no order section is said", "no 'order' section" in (read_order(qpath)[1] or ""), True)
        check("read: a missing file is said", read_order(os.path.join(tmp, "nope.json"))[1], "the queue's settings file could not be read")
        put_queue()
        # the file
        save(doc_(), cpath)
        d1, why1 = load(cpath)
        check("the proposals file round-trips", (why1, [c["key"] for c in d1["proposals"]]), (None, ["planned_load", "lanes_per_repo"]))
        check("a missing proposals file is no proposals, not a fault", load(os.path.join(tmp, "none.json")), (None, None))
        with open(cpath, "w", encoding="utf-8") as fh:
            fh.write("{nope")
        check("a damaged proposals file is said", load(cpath)[1], "the sprint changes file cannot be read")
        for label, mutate in (("a key outside the list", lambda d: d["proposals"][0].update(key="model_for_review")),
                              ("a raise", lambda d: d["proposals"][0].update(new=50)),
                              ("four proposals", lambda d: d.update(proposals=d["proposals"] * 2)),
                              ("a bad stamp", lambda d: d["proposals"][0].update(sent_at="yesterday")),
                              ("another schema", lambda d: d.update(schema="x"))):
            bad_doc = doc_()
            mutate(bad_doc)
            try:
                save(bad_doc, cpath)
                got = "written"
            except ChangesError:
                got = "refused"
            check("a proposals file with %s is never written" % label, got, "refused")
        save(doc_(), cpath)
        # sending
        fresh = new_doc("2026-11-10", now, over)
        save(fresh, cpath)
        check("an unsent proposal is listed with its question", [(i, t[:34]) for i, t in unsent(load(cpath)[0])],
              [(0, "Sprint change 1 of 1: lower planne")])
        mark_sent(cpath, 0, now)
        check("a sent proposal is no longer listed", unsent(load(cpath)[0]), [])
        mark_sent(cpath, 0, now, sent=False)
        check("…and the mark is put back when the post fails", len(unsent(load(cpath)[0])), 1)
        # the yes
        save(doc_(), cpath)
        put_queue()
        st, words = apply_yes(cpath, qpath, "planned_load", "30", "2026-11-10", now, dry=True)
        check("a dry run shows the old and the new value and changes nothing",
              (st, "planned_load: 40 -> 30" in words, read_order(qpath)[0]["planned_load"]), ("dry", True, 40))
        st, words = apply_yes(cpath, qpath, "planned_load", "30", "2026-11-10", now)
        check("the exact reply changes that one key, and the words show both values and the catch",
              (st, "planned_load: 40 -> 30" in words, "PLANNED_LOAD=30 in queue.conf" in words), ("applied", True, True))
        after = json.load(open(qpath))
        check("only that key moved: the other keys, the other sections and the file's mode are as they were",
              (after["order"], after["other"], oct(os.stat(qpath).st_mode & 0o777)),
              ({"planned_load": 30, "lanes_per_repo": 2, "day_stop": 60}, {"keep": [1, 2]}, "0o640"))
        check("the proposal is marked applied", [c["applied_at"] is not None for c in load(cpath)[0]["proposals"]], [True, False])
        st, words = apply_yes(cpath, qpath, "planned_load", "30", "2026-11-10", now)
        check("a second yes changes nothing and says so", (st, read_order(qpath)[0]["planned_load"]), ("already", 30))
        st, _w = apply_yes(cpath, qpath, "lanes_per_repo", "1", "2026-11-10", now)
        check("a yes names one change: the other is still waiting until it is asked for", (st, read_order(qpath)[0]["lanes_per_repo"],
              [c["applied_at"] is not None for c in load(cpath)[0]["proposals"]]), ("applied", 1, [True, True]))
        put_queue()
        save(doc_(), cpath)
        for label, args, want in (("a key outside the list", ("model_for_review", "30", "2026-11-10"), "refused"),
                                  ("a value nobody proposed", ("planned_load", "31", "2026-11-10"), "none"),
                                  ("a sprint nobody proposed for", ("planned_load", "30", "2026-11-17"), "none"),
                                  ("a value that is not a number", ("planned_load", "abc", "2026-11-10"), "refused")):
            st, _w = apply_yes(cpath, qpath, args[0], args[1], args[2], now)
            check("a yes naming %s changes nothing" % label, (st, read_order(qpath)[0]), (want, {"planned_load": 40, "lanes_per_repo": 2}))
        # a proposal for a key that is not allowed, put into the file by hand, is refused at the file and never applied
        raw = doc_()
        raw["proposals"][0]["key"] = "models_for_review"
        with open(cpath, "w", encoding="utf-8") as fh:
            json.dump(raw, fh)
        st, _w = apply_yes(cpath, qpath, "models_for_review", "30", "2026-11-10", now)
        check("a key outside the list written into the file by hand is never applied", (st, read_order(qpath)[0]["planned_load"]), ("unusable", 40))
        save(doc_(), cpath)
        unsent_doc = doc_()
        unsent_doc["proposals"][0]["sent_at"] = None
        save(unsent_doc, cpath)
        st, _w = apply_yes(cpath, qpath, "planned_load", "30", "2026-11-10", now)
        check("a yes to a question that was never sent changes nothing", (st, read_order(qpath)[0]["planned_load"]), ("unsent", 40))
        save(doc_(made_at=_iso(now - datetime.timedelta(days=LIFETIME_DAYS + 1))), cpath)
        st, _w = apply_yes(cpath, qpath, "planned_load", "30", "2026-11-10", now)
        check("a proposal more than ten days old is not applied", (st, read_order(qpath)[0]["planned_load"]), ("old", 40))
        save(doc_(), cpath)
        put_queue({"planned_load": 35, "lanes_per_repo": 2})
        st, words = apply_yes(cpath, qpath, "planned_load", "30", "2026-11-10", now)
        check("settings that moved since the proposal read them are not overwritten",
              (st, "is 35 now, not the 40" in words, read_order(qpath)[0]["planned_load"]), ("stale", True, 35))
        put_queue()
        os.unlink(qpath)
        st, _w = apply_yes(cpath, qpath, "planned_load", "30", "2026-11-10", now)
        check("unreadable queue settings change nothing and are said", (st,), ("unusable",))
        put_queue()
        os.rename(qpath, qpath + ".real")
        os.symlink(qpath + ".real", qpath)
        st, _w = apply_yes(cpath, qpath, "planned_load", "30", "2026-11-10", now)
        check("a settings file that is a link is never written through",
              (st, json.load(open(qpath + ".real"))["order"]["planned_load"], [c["applied_at"] for c in load(cpath)[0]["proposals"]]),
              ("failed", 40, [None, None]))
        os.unlink(qpath)
        os.rename(qpath + ".real", qpath)
        # the command line
        import contextlib
        import io
        for argv, want in ((["show", "--changes", cpath], 0),
                           (["apply", "--changes", cpath, "--queue-config", qpath, "planned_load", "30", "2026-11-10"], 0),
                           (["apply", "--changes", cpath, "--queue-config", qpath, "planned_load", "31", "2026-11-10"], 1)):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(argv)
            check("command line %s exits %d" % (argv[0], want), code, want)
            if argv[0] == "apply" and want == 0:
                check("…and without --yes it shows the change and makes none", ("planned_load: 40 -> 30" in out.getvalue(),
                                                                                  read_order(qpath)[0]["planned_load"]), (True, 40))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["apply", "--changes", cpath, "--queue-config", qpath, "planned_load", "30", "2026-11-10", "--yes"])
        check("…and with --yes it makes it", read_order(qpath)[0]["planned_load"], 30)
        # the section in the document
        check("the document says plainly when nothing is proposed", section_lines(new_doc("2026-11-10", now, []), None),
              ["No configuration change is proposed this sprint."])
        check("…and when the settings could not be read", section_lines(None, "the queue's settings file could not be read"),
              ["No configuration change is proposed: the queue's settings file could not be read."])
        check("…and lists each proposal with its numbers", section_lines(new_doc("2026-11-10", now, over), None)[2],
              "- `planned_load`: 40 to 30. The sprint spent $130 of the $100 planned (130%).")
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    above = src.split("def _selftest():")[0]
    check("no model, no network, no shell: nothing of the kind is named above the selftest",
          [b for b in ("urllib", "http.client", "socket", "subprocess", "os.system", "shell=True", "os.getenv", "environ") if b in above], [])
    check("the only file it writes besides its own is the one queue key",
          above.count("_write_key(") , 2)
    if failures:
        for line in failures:
            print(line)
        print("sprint changes selftest: %d of %d checks failed" % (len(failures), total[0]))
        return 1
    print("OK: sprint changes selftest (%d checks)" % total[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
