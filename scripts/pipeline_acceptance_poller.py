#!/usr/bin/env python3
"""The acceptance poller, slices 1 and 2: when may a plan's acceptance check start, and never twice for one
head (KIT-551); and what did the check find, read from the session's result and the look tool's own record
(KIT-552). Slices of KIT-324; docs/ACCEPTANCE-POLLER-OPERATOR.md.

    python3 scripts/pipeline_acceptance_poller.py decide --facts FILE --ledger FILE   # read-only: say what would start
    python3 scripts/pipeline_acceptance_poller.py --selftest

SLICE 2, READING WHAT THE CHECK FOUND.  `judge()` takes the session's `pipeline-acceptance/1` block (contract
section 21) as text, the plan's head, and the look tool's record folder, and returns one verdict. It
trusts the look tool's record for what ran and what failed mechanically, and the block only for what the
session judged on screen. Rules, each tested:
  * A block that is missing, does not parse, names another marker, fails the schema, names another plan or
    another head, or repeats a problem id is could-not-check, with the reason. Never a pass.
  * A record folder that cannot be read, a record that is malformed, or no record at all is could-not-check:
    a session cannot claim a check ran that the look tool never recorded.
  * A run that could not run, or a record that crashed, is could-not-run. It is never a pass. A session that
    says found-nothing while one exists gets could-not-check.
  * A run the record says failed is a mechanical failure whatever the session said: found-nothing becomes
    found-problems, and the disagreement is named.
  * A run the session says it ran, that the record does not hold, is a disagreement, said aloud. With
    found-nothing it is could-not-check: what ran cannot be confirmed.
  * A record not marked as the runner's alone, or marked session-code-in-runner, makes the verdict
    not-mechanical, and it says so. A record with no mark is treated as not mechanical.
The session's own text (a problem's `what`, a `reason`) is returned raw: it is data, and whoever embeds it in a
comment, a page or a request neutralizes it first (slices 3 and 4).

WHAT SLICE 1 IS.  The decision, not the doing. It reads the facts about each approved plan from a
facts file (fields only; a later slice reads them from the tracker and GitHub) and answers, for each plan,
START or a NAMED REASON why not. `run_pass()` also keeps the ledger, and calls the `start` function it is
handed. This slice hands it nothing, so the command line only ever decides. It calls no tracker, starts no
session, sends nothing, and writes nothing but the ledger, through `run_pass()`.

WHEN A CHECK MAY START (the design, docs/adr: before the last piece merges):
  acceptance is on for the plan's repository, AND every piece but one is Done, AND that one piece, the
  last, has a pull request that is open, green and reviewed. "The last" means the one piece still open,
  whichever it is: pieces merge in any order, and the plan's list order says nothing about which is last.
Anything else is a closed word, never a silent no: acceptance-off, no-pieces, pieces-pending (more than one
piece is not Done), all-done (nothing left to check before), no-pr, pr-not-open, checks-red,
checks-pending, checks-unknown, review-pending, review-declined, review-unknown.

THE RULES, each one tested.
  * A fact it cannot read or does not have is could-not-tell, with what was missing. It never starts.
    "Nothing to do" and "could not do it" are different answers (contract section 13).
  * One check per plan and PULL-REQUEST HEAD. The ledger holds a row for each; a head already in it never
    starts again, and a new head (a push) is a new row.
  * The row is written BEFORE anything starts, atomically (a temp file, fsync, a rename). A crash between the
    write and the start leaves a row marked `starting`, which never starts again by itself and is said as
    start-unconfirmed: the safe side of "never twice".
  * A ledger that exists and cannot be read stops the whole pass. Starting from an empty ledger would start
    every plan again.
  * Text from a ticket or a pull request is never in the facts, the ledger or the output: ids, numbers,
    closed words and times only.
"""
import argparse
import fcntl
import json
import os
import re
import sys
import tempfile
import time

FACTS_SCHEMA = "pipeline-acceptance-facts/1"
LEDGER_SCHEMA = "pipeline-acceptance-ledger/1"
MAX_BYTES = 2 * 1024 * 1024
MAX_PLANS = 200
TIME_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
TICKET_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}-[0-9]{1,6}\Z")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}\Z")
SHA_RE = re.compile(r"^[0-9a-f]{40}\Z")
PIECE_STATES = ("backlog", "ready", "working", "review", "done")
CHECKS = ("green", "red", "pending", "unknown")
REVIEWS = ("reviewed", "pending", "declined", "unknown")
EXIT_OK, EXIT_ERROR, EXIT_USAGE = 0, 1, 2

START = "start"
REASONS = ("acceptance-off", "no-pieces", "pieces-pending", "all-done", "no-pr", "pr-not-open", "checks-red",
           "checks-pending", "checks-unknown", "review-pending", "review-declined", "review-unknown",
           "already-started", "start-unconfirmed", "start-failed")


class LedgerError(Exception):
    """The ledger exists and cannot be read. The pass stops."""


class BusyError(Exception):
    """Another pass holds the ledger's lock. This pass does nothing and says so."""


class FactsError(Exception):
    """The facts file cannot be read at all."""


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


# --------------------------------------------------------------------------- facts

def load_facts(path):
    """The facts document, or FactsError. Shape problems are per plan (could-not-tell), not here."""
    try:
        size = os.path.getsize(path)
        if size > MAX_BYTES:
            raise FactsError("the facts file is larger than %d bytes" % MAX_BYTES)
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FactsError:
        raise
    except (OSError, ValueError) as exc:
        raise FactsError("the facts file cannot be read: %s" % exc)
    if not isinstance(doc, dict) or doc.get("schema") != FACTS_SCHEMA or not isinstance(doc.get("plans"), list):
        raise FactsError("the facts file is not a %s document" % FACTS_SCHEMA)
    if len(doc["plans"]) > MAX_PLANS:
        raise FactsError("the facts file lists more than %d plans" % MAX_PLANS)
    return doc


def decide_plan(p):
    """(verdict, detail, head): START or a closed reason, and the head it judged (None when not known).
    A plan is a dict: plan, repo, acceptance (bool), pieces [{id, state, pr?}]; pr is {number, head, open,
    checks, review}."""
    if not isinstance(p, dict) or not TICKET_RE.match(str(p.get("plan") or "")):
        return "could-not-tell", "a plan without a ticket id", None
    if not REPO_RE.match(str(p.get("repo") or "")):
        return "could-not-tell", "no repository named", None
    if not isinstance(p.get("acceptance"), bool):
        return "could-not-tell", "acceptance is neither on nor off", None
    if not p["acceptance"]:
        return "acceptance-off", "acceptance is off for %s" % p["repo"], None
    pieces = p.get("pieces")
    if not isinstance(pieces, list) or not pieces:
        return "no-pieces" if pieces == [] else "could-not-tell", "the plan lists no pieces", None
    for pc in pieces:
        if not isinstance(pc, dict) or not TICKET_RE.match(str(pc.get("id") or "")) or pc.get("state") not in PIECE_STATES:
            return "could-not-tell", "a piece with no id or a state it does not know", None
    open_pieces = [pc for pc in pieces if pc["state"] != "done"]
    if not open_pieces:
        return "all-done", "every piece is Done; nothing is left to check before", None
    if len(open_pieces) > 1:
        return "pieces-pending", "%d pieces are not Done yet" % len(open_pieces), None
    last = open_pieces[0]
    pr = last.get("pr")
    if pr is None:
        return "no-pr", "%s has no pull request yet" % last["id"], None
    if not isinstance(pr, dict) or not isinstance(pr.get("number"), int) or isinstance(pr.get("number"), bool) \
            or not SHA_RE.match(str(pr.get("head") or "")):
        return "could-not-tell", "%s's pull request has no number or no full head" % last["id"], None
    head = pr["head"]
    if not isinstance(pr.get("open"), bool):
        return "could-not-tell", "whether %s's pull request is open is not known" % last["id"], head
    if not pr["open"]:
        return "pr-not-open", "#%d is not open" % pr["number"], head
    checks, review = pr.get("checks"), pr.get("review")
    if checks not in CHECKS or review not in REVIEWS:
        return "could-not-tell", "#%d has a checks or review state it does not know" % pr["number"], head
    if checks != "green":
        return "checks-" + checks, "#%d's required checks are %s" % (pr["number"], checks), head
    if review != "reviewed":
        return "review-" + review, "#%d's review is %s" % (pr["number"], review), head
    return START, "#%d at %s is open, green and reviewed" % (pr["number"], head[:8]), head


# --------------------------------------------------------------------------- ledger

def load_ledger(path):
    """{(plan, head): row}. Absent is empty. Present and unreadable raises LedgerError."""
    if not os.path.exists(path):
        return {}
    try:
        if os.path.getsize(path) > MAX_BYTES:
            raise LedgerError("the ledger is larger than %d bytes" % MAX_BYTES)
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except LedgerError:
        raise
    except (OSError, ValueError) as exc:
        raise LedgerError("the ledger cannot be read: %s" % exc)
    if not isinstance(doc, dict) or doc.get("schema") != LEDGER_SCHEMA or not isinstance(doc.get("rows"), list):
        raise LedgerError("the ledger is not a %s document" % LEDGER_SCHEMA)
    rows = {}
    for r in doc["rows"]:
        if not isinstance(r, dict) or not TICKET_RE.match(str(r.get("plan") or "")) \
                or not SHA_RE.match(str(r.get("head") or "")) or r.get("status") not in ("starting", "started", "failed") \
                or not TIME_RE.match(str(r.get("at") or "")):
            raise LedgerError("a ledger row is malformed")
        rows[(r["plan"], r["head"])] = r
    return rows


def write_ledger(path, rows):
    """Atomically: a temp file in the same folder, fsynced, renamed over the ledger."""
    folder = os.path.dirname(os.path.abspath(path))
    doc = {"schema": LEDGER_SCHEMA,
           "rows": [rows[k] for k in sorted(rows)]}
    fd, tmp = tempfile.mkstemp(prefix=".ledger-", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------- the pass

def run_pass(facts_path, ledger_path, now, start=None):
    """One pass; with a `start` it holds the ledger's lock for the whole pass, so two passes never start the
    same head (BusyError for the second). A read-only pass takes no lock: the ledger is replaced atomically."""
    if start is None:
        return _pass(facts_path, ledger_path, now, None)
    lock = open(ledger_path + ".lock", "a")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise BusyError("another pass holds the ledger's lock")
        return _pass(facts_path, ledger_path, now, start)
    finally:
        lock.close()


def _pass(facts_path, ledger_path, now, start):
    """Returns [(plan, verdict, detail)]. `start(plan, head)` is called for each plan that may
    start, AFTER its ledger row is on disk; it raises to say it failed. With no `start` nothing is recorded
    and nothing starts (a read-only pass). Raises FactsError / LedgerError, which stop the pass."""
    facts = load_facts(facts_path)
    rows = load_ledger(ledger_path)
    out = []
    for p in facts["plans"]:
        verdict, detail, head = decide_plan(p)
        plan = p.get("plan") if isinstance(p, dict) else None
        if verdict == START:
            row = rows.get((plan, head))
            if row is not None:
                verdict = {"started": "already-started", "starting": "start-unconfirmed",
                           "failed": "start-failed"}[row["status"]]
                detail = "%s was already %s for %s (%s)" % (plan, row["status"], head[:8], row["at"])
            elif start is not None:
                rows[(plan, head)] = {"plan": plan, "head": head, "status": "starting", "at": _iso(now)}
                write_ledger(ledger_path, rows)                 # on disk before anything starts
                try:
                    start(plan, head)
                except Exception as exc:                        # noqa: BLE001 - any failure is a fact
                    rows[(plan, head)]["status"] = "failed"
                    write_ledger(ledger_path, rows)
                    verdict, detail = "start-failed", "starting %s failed: %s" % (plan, type(exc).__name__)
                else:
                    rows[(plan, head)]["status"] = "started"
                    write_ledger(ledger_path, rows)
        out.append((plan or "?", verdict, detail))
    return out


# --------------------------------------------------------------------------- slice 2: read the result

RESULT_SCHEMA_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schemas",
                                  "pipeline-acceptance.schema.json")
RECORD_SCHEMA = "pipeline-look-record/1"
RECORD_RESULTS = ("passed", "failed", "could-not-run", "crashed")
RUN_STATUSES = ("passed", "failed", "could-not-run")
MAX_RECORDS = 50


def read_block(text, plan, head):
    """(block, None) or (None, why-not). The block as text: parsed, held to the schema, and held to this plan
    and this head."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import jsonschema_mini as jsm
    if not isinstance(text, str) or not text.strip():
        return None, "the session left no result block"
    if len(text) > MAX_BYTES:
        return None, "the result block is larger than %d bytes" % MAX_BYTES
    try:
        block = json.loads(text)
    except ValueError:
        return None, "the result block does not parse"
    if not isinstance(block, dict) or block.get("schema") != "pipeline-acceptance/1":
        return None, "the result block names another schema"
    with open(RESULT_SCHEMA_FILE, encoding="utf-8") as fh:
        errors = jsm.validate(block, json.load(fh))
    if errors:
        return None, "the result block fails the schema (%s)" % errors[0]["message"]
    if block["plan"] != plan:
        return None, "the result block is about %s, not %s" % (block["plan"], plan)
    if block["head"] != head:
        return None, "the result block judged head %s, not %s" % (block["head"][:8], head[:8])
    ids = [pb["id"] for pb in block["problems"]]
    if len(ids) != len(set(ids)):
        return None, "the result block repeats a problem id"
    return block, None


def read_records(record_dir, plan):
    """([record, ...], None) or (None, why-not). The look tool's records for the plan, oldest first. A folder
    that is not there is no records; one that cannot be read, or a record that is malformed, is a reason."""
    folder = os.path.join(record_dir, plan)
    if not os.path.isdir(folder):
        return [], None
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith(".json"))
    except OSError as exc:
        return None, "the look tool's record folder cannot be read: %s" % exc
    if len(names) > MAX_RECORDS:
        return None, "the look tool's record folder holds more than %d records" % MAX_RECORDS
    out = []
    for name in names:
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            return None, "a look record (%s) cannot be read" % name
        if not isinstance(rec, dict) or rec.get("schema") != RECORD_SCHEMA or rec.get("result") not in RECORD_RESULTS \
                or not isinstance(rec.get("runs"), list):
            return None, "a look record (%s) is malformed" % name
        for run in rec["runs"]:
            if not isinstance(run, dict) or run.get("status") not in RUN_STATUSES \
                    or not all(isinstance(run.get(k), str) for k in ("check", "viewport", "mode")):
                return None, "a look record (%s) holds a run it cannot read" % name
        out.append(rec)
    return out, None


def _verdict(outcome, reason=None, **more):
    v = {"outcome": outcome, "reason": reason, "disagreements": [], "mechanical_failures": [],
         "could_not_run": [], "not_mechanical": True, "problems": [], "ran_in_record": 0}
    v.update(more)
    return v


def judge(block_text, plan, head, record_dir):
    """One verdict from the session's block and the look tool's records. outcome is found-nothing,
    found-problems or could-not-check; the rest of the dict says why, and is fields and raw session text
    only."""
    block, why = read_block(block_text, plan, head)
    if block is None:
        return _verdict("could-not-check", why)
    records, why = read_records(record_dir, plan)
    if records is None:
        return _verdict("could-not-check", why)
    if not records:
        return _verdict("could-not-check", "the look tool has no record for this plan, so what ran cannot be confirmed",
                        problems=block["problems"])
    runs = {}
    for rec in records:
        for run in rec["runs"]:
            runs[(run["check"], run["viewport"], run["mode"])] = run["status"]      # the latest record wins
    failed = sorted("%s/%s/%s" % k for k, st in runs.items() if st == "failed")
    cnr = sorted("%s/%s/%s" % k for k, st in runs.items() if st == "could-not-run")
    crashed = [rec for rec in records if rec["result"] == "crashed"]
    if crashed:
        cnr.append("%d crashed run(s)" % len(crashed))
    claimed = {(r["check"], r["viewport"], r["mode"]): r["result"] for r in block["ran"]}
    disagree = []
    for k, said in sorted(claimed.items()):
        if k not in runs:
            disagree.append("the session says it ran %s; the look tool's record holds no such run" % "/".join(k))
        elif said != runs[k]:
            disagree.append("%s: the session says %s, the look tool's record says %s" % ("/".join(k), said, runs[k]))
    not_mech = any(rec.get("outcome_trust") != "runner-only" and rec.get("outcome_trust") != "base-copy"
                   for rec in records)
    common = dict(disagreements=disagree, mechanical_failures=failed, could_not_run=cnr,
                  not_mechanical=not_mech, problems=block["problems"], ran_in_record=len(runs))
    if block["outcome"] == "could-not-check":
        return _verdict("could-not-check", block["reason"], **common)
    if block["outcome"] == "found-problems":
        return _verdict("found-problems", None, **common)
    # the session says found-nothing: only a clean, confirmed record lets that stand
    if failed:
        return _verdict("found-problems", "the look tool's record has failed runs the session did not report", **common)
    if cnr:
        return _verdict("could-not-check", "some runs could not run, so nothing can be called a pass", **common)
    if disagree:
        return _verdict("could-not-check", "what ran cannot be confirmed from the look tool's record", **common)
    return _verdict("found-nothing", None, **common)


def render(results):
    return "\n".join("%-10s %-18s %s" % (plan, verdict, detail) for plan, verdict, detail in results)


# --------------------------------------------------------------------------- selftest

def selftest():
    failures = []

    def expect(cond, msg):
        if not cond:
            failures.append(msg)

    H1, H2 = "a" * 40, "b" * 40

    def pr(**over):
        d = {"number": 7, "head": H1, "open": True, "checks": "green", "review": "reviewed"}
        d.update(over)
        return d

    def plan(pieces=None, **over):
        d = {"plan": "TOD-100", "repo": "acme/app", "acceptance": True,
             "pieces": pieces if pieces is not None else [{"id": "TOD-101", "state": "done"},
                                                          {"id": "TOD-102", "state": "review", "pr": pr()}]}
        d.update(over)
        return d

    # 1. every reason, and the one START
    returned = set()
    for name, p, want in (
        ("ready", plan(), START),
        ("acceptance off", plan(acceptance=False), "acceptance-off"),
        ("no pieces", plan(pieces=[]), "no-pieces"),
        ("two pieces open", plan(pieces=[{"id": "TOD-101", "state": "working"}, {"id": "TOD-102", "state": "review", "pr": pr()}]),
         "pieces-pending"),
        ("all done", plan(pieces=[{"id": "TOD-101", "state": "done"}]), "all-done"),
        ("no pull request", plan(pieces=[{"id": "TOD-101", "state": "done"}, {"id": "TOD-102", "state": "working"}]), "no-pr"),
        ("pull request closed", plan(pieces=[{"id": "TOD-101", "state": "done"}, {"id": "TOD-102", "state": "review", "pr": pr(open=False)}]),
         "pr-not-open"),
        ("checks red", plan(pieces=[{"id": "TOD-101", "state": "done"}, {"id": "TOD-102", "state": "review", "pr": pr(checks="red")}]),
         "checks-red"),
        ("checks pending", plan(pieces=[{"id": "TOD-101", "state": "done"}, {"id": "TOD-102", "state": "review", "pr": pr(checks="pending")}]),
         "checks-pending"),
        ("checks unknown", plan(pieces=[{"id": "TOD-101", "state": "done"}, {"id": "TOD-102", "state": "review", "pr": pr(checks="unknown")}]),
         "checks-unknown"),
        ("review pending", plan(pieces=[{"id": "TOD-101", "state": "done"}, {"id": "TOD-102", "state": "review", "pr": pr(review="pending")}]),
         "review-pending"),
        ("review declined", plan(pieces=[{"id": "TOD-101", "state": "done"}, {"id": "TOD-102", "state": "review", "pr": pr(review="declined")}]),
         "review-declined"),
        ("review unknown", plan(pieces=[{"id": "TOD-101", "state": "done"}, {"id": "TOD-102", "state": "review", "pr": pr(review="unknown")}]),
         "review-unknown"),
    ):
        got = decide_plan(p)[0]
        returned.add(got)
        expect(got == want, f"{name}: got {got}, want {want}")
    expect(returned == {START} | set(REASONS) - {"already-started", "start-unconfirmed", "start-failed"},
           f"the words decide_plan returned are the closed words REASONS lists (plus start): {sorted(returned)}")
    # "the last piece" is the one still open, wherever it sits in the list
    first_open = plan(pieces=[{"id": "TOD-101", "state": "review", "pr": pr()}, {"id": "TOD-102", "state": "done"}])
    expect(decide_plan(first_open)[0] == START, "the last piece is the one still open, not the last in the list")

    # 2. a fact it lacks or cannot read is could-not-tell, never START
    for name, p in (
        ("no ticket id", plan(plan="nonsense")), ("no repository", plan(repo="")),
        ("acceptance not a boolean", plan(acceptance="yes")), ("pieces missing", {**plan(), "pieces": None}),
        ("a piece with an unknown state", plan(pieces=[{"id": "TOD-101", "state": "limbo"}])),
        ("a pull request with no head", plan(pieces=[{"id": "TOD-101", "state": "done"}, {"id": "TOD-102", "state": "review", "pr": pr(head="abc")}])),
        ("a pull request with no number", plan(pieces=[{"id": "TOD-101", "state": "done"}, {"id": "TOD-102", "state": "review", "pr": pr(number="7")}])),
        ("openness unknown", plan(pieces=[{"id": "TOD-101", "state": "done"}, {"id": "TOD-102", "state": "review", "pr": pr(open=None)}])),
        ("a checks state it does not know", plan(pieces=[{"id": "TOD-101", "state": "done"}, {"id": "TOD-102", "state": "review", "pr": pr(checks="great")}])),
        ("not even a dict", "plan"),
    ):
        got = decide_plan(p)[0]
        expect(got == "could-not-tell", f"{name}: got {got}, want could-not-tell")

    with tempfile.TemporaryDirectory(prefix="acc-poller-") as tmp:
        facts_path, ledger_path = os.path.join(tmp, "facts.json"), os.path.join(tmp, "ledger.json")

        def facts(*plans):
            with open(facts_path, "w") as fh:
                json.dump({"schema": FACTS_SCHEMA, "plans": list(plans)}, fh)

        started = []

        def starter(p, h):
            started.append((p, h))

        # 3. the ledger: a row on disk before the start, one start per head, a new head is a new row
        facts(plan())
        expect(run_pass(facts_path, ledger_path, 1000) == [("TOD-100", START, "#7 at aaaaaaaa is open, green and reviewed")]
               and not os.path.exists(ledger_path), "a read-only pass decides and writes nothing")
        seen_on_disk = {}

        def spy(p, h):
            seen_on_disk["rows"] = load_ledger(ledger_path)
            started.append((p, h))
        r = run_pass(facts_path, ledger_path, 1000, spy)
        expect(r[0][1] == START and started == [("TOD-100", H1)], f"the first pass starts the plan: {r}")
        expect(seen_on_disk["rows"][("TOD-100", H1)]["status"] == "starting",
               "the row is on disk, marked starting, BEFORE the start runs")
        expect(load_ledger(ledger_path)[("TOD-100", H1)]["status"] == "started", "and `started` after it")
        r = run_pass(facts_path, ledger_path, 2000, starter)
        expect(r[0][1] == "already-started" and started == [("TOD-100", H1)], f"the same head never starts twice: {r}")
        facts(plan(pieces=[{"id": "TOD-101", "state": "done"}, {"id": "TOD-102", "state": "review", "pr": pr(head=H2)}]))
        r = run_pass(facts_path, ledger_path, 3000, starter)
        expect(r[0][1] == START and started[-1] == ("TOD-100", H2) and len(load_ledger(ledger_path)) == 2,
               f"a push gives a new head, which is a new row and a new start: {r}")

        # 4. a crash between the write and the start leaves `starting`, which never starts again by itself
        os.unlink(ledger_path)
        started.clear()
        facts(plan())

        def crashes(p, h):
            raise KeyboardInterrupt()
        try:
            run_pass(facts_path, ledger_path, 1000, crashes)
        except KeyboardInterrupt:
            pass
        expect(load_ledger(ledger_path)[("TOD-100", H1)]["status"] == "starting", "the crash left a `starting` row")
        r = run_pass(facts_path, ledger_path, 2000, starter)
        expect(r[0][1] == "start-unconfirmed" and not started, f"a `starting` row is not started again: {r}")
        # …and a start that FAILS (an ordinary error) is recorded as failed, said, and not retried by itself
        os.unlink(ledger_path)

        def fails(p, h):
            raise OSError("no")
        r = run_pass(facts_path, ledger_path, 1000, fails)
        expect(r[0][1] == "start-failed" and load_ledger(ledger_path)[("TOD-100", H1)]["status"] == "failed", f"{r}")
        expect(run_pass(facts_path, ledger_path, 2000, starter)[0][1] == "start-failed" and not started,
               "a failed row is not started again by itself")

        # 4b. two passes at once: the second does nothing and says so; a read-only pass takes no lock
        os.unlink(ledger_path)
        started.clear()
        holder = open(ledger_path + ".lock", "a")
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            try:
                run_pass(facts_path, ledger_path, 1, starter)
                expect(False, "a pass must not run while another holds the lock")
            except BusyError:
                pass
            expect(not started and not os.path.exists(ledger_path), "the busy pass started and wrote nothing")
            expect(run_pass(facts_path, ledger_path, 1)[0][1] == START, "a read-only pass still decides")
        finally:
            holder.close()
        expect(run_pass(facts_path, ledger_path, 1, starter)[0][1] == START and len(started) == 1,
               "and the pass runs once the lock is free")
        os.unlink(ledger_path)
        started.clear()

        # 5. a ledger that cannot be read stops the pass; absent is empty
        for text in ("{not json", json.dumps({"schema": "other/1", "rows": []}),
                     json.dumps({"schema": LEDGER_SCHEMA, "rows": [{"plan": "x"}]}),
                     json.dumps({"schema": LEDGER_SCHEMA, "rows": [{"plan": "TOD-100", "head": H1, "status": "started"}]}),
                     json.dumps({"schema": LEDGER_SCHEMA, "rows": [{"plan": "TOD-100", "head": H1, "status": "started",
                                                                    "at": "@everyone <!-- x -->"}]})):
            with open(ledger_path, "w") as fh:
                fh.write(text)
            started.clear()
            try:
                run_pass(facts_path, ledger_path, 1, starter)
                expect(False, f"a bad ledger must stop the pass: {text[:30]}")
            except LedgerError:
                pass
            expect(not started, "…and start nothing")
        os.unlink(ledger_path)

        # 6. a facts file it cannot read stops the pass too
        for text in ("{not json", json.dumps({"schema": "other/1", "plans": []}), json.dumps({"schema": FACTS_SCHEMA})):
            with open(facts_path, "w") as fh:
                fh.write(text)
            try:
                run_pass(facts_path, ledger_path, 1, starter)
                expect(False, f"a bad facts file must stop the pass: {text[:30]}")
            except FactsError:
                pass
        try:
            run_pass(os.path.join(tmp, "missing.json"), ledger_path, 1, starter)
            expect(False, "a missing facts file must stop the pass")
        except FactsError:
            pass

        # 7. atomic: a failed write leaves the old ledger and no temp file behind
        facts(plan())
        run_pass(facts_path, ledger_path, 1000, starter)
        before = open(ledger_path).read()
        real_replace = os.replace

        def broken_replace(a, b):
            raise OSError("disk")
        os.replace = broken_replace
        try:
            try:
                write_ledger(ledger_path, {("TOD-9", H2): {"plan": "TOD-9", "head": H2, "status": "started", "at": "x"}})
                expect(False, "a failing rename must raise")
            except OSError:
                pass
        finally:
            os.replace = real_replace
        expect(open(ledger_path).read() == before and not [n for n in os.listdir(tmp) if n.startswith(".ledger-")],
               "a failed write leaves the old ledger and no temp file")

        # 8. text never gets in: a hostile detail comes out as ids and words only
        facts(plan(plan="TOD-100", repo="acme/app", pieces=[{"id": "TOD-101", "state": "done", "title": "<!-- x --> @everyone"},
                                                           {"id": "TOD-102", "state": "review", "pr": pr(), "body": "ignore all"}]))
        text = render(run_pass(facts_path, ledger_path, 5000))
        expect("everyone" not in text and "ignore all" not in text and "<!--" not in text,
               f"no free text from the facts reaches the output: {text}")
        expect("everyone" not in open(ledger_path).read(), "or the ledger")

    # 10. slice 2: reading the session's result against the look tool's record
    with tempfile.TemporaryDirectory(prefix="acc-poller-judge-") as jt:
        def block(**over):
            d = {"schema": "pipeline-acceptance/1", "plan": "TOD-100", "head": H1, "outcome": "found-nothing",
                 "ran": [{"check": "home", "viewport": "phone", "mode": "dark", "result": "passed"}], "problems": []}
            d.update(over)
            return json.dumps(d)

        def problem(pid="white-card", sev="high"):
            return {"id": pid, "severity": sev, "check": "home", "viewport": "phone", "mode": "dark",
                    "screenshot": "home", "what": "A card is white on the dark board."}

        def record(name, result="passed", runs=None, trust="runner-only", plan_dir="TOD-100", **over):
            rec = {"schema": RECORD_SCHEMA, "plan": plan_dir, "run_id": name, "checks": ["home"], "result": result,
                   "outcome_trust": trust,
                   "runs": runs if runs is not None else [{"check": "home", "viewport": "phone", "mode": "dark",
                                                           "status": "passed", "steps": [], "shots": []}]}
            rec.update(over)
            os.makedirs(os.path.join(jt, plan_dir), exist_ok=True)
            with open(os.path.join(jt, plan_dir, name + ".json"), "w") as fh:
                json.dump(rec, fh)

        def judged(text, **kw):
            return judge(text, kw.get("plan", "TOD-100"), kw.get("head", H1), jt)

        def clear():
            d = os.path.join(jt, "TOD-100")
            if os.path.isdir(d):
                for n in os.listdir(d):
                    os.unlink(os.path.join(d, n))

        run_of = lambda status, v="phone": {"check": "home", "viewport": v, "mode": "dark", "status": status,   # noqa: E731
                                              "steps": [], "shots": []}

        # a clean pass is the one thing that reads as found-nothing
        record("001")
        v = judged(block())
        expect(v["outcome"] == "found-nothing" and not v["not_mechanical"] and v["ran_in_record"] == 1, f"clean pass: {v}")
        # the block is held to the schema, the plan and the head
        for name, text, needle in (
            ("missing", None, "no result block"), ("empty", "  ", "no result block"), ("unparseable", "{not json", "does not parse"),
            ("another marker", block(schema="pipeline-acceptance/2"), "another schema"),
            ("schema fails", block(outcome="found-nothing", problems=[problem()]), "fails the schema"),
            ("another plan", block(plan="TOD-200"), "not TOD-100"), ("another head", block(head=H2), "judged head"),
            ("repeated id", block(outcome="found-problems", problems=[problem("a"), problem("a", "low")]), "repeats a problem id"),
            ("huge", "x" * (MAX_BYTES + 1), "larger than"),
        ):
            v = judged(text)
            expect(v["outcome"] == "could-not-check" and needle in (v["reason"] or ""), f"{name}: {v}")
        # the record: none, unreadable, malformed
        clear()
        v = judged(block())
        expect(v["outcome"] == "could-not-check" and "no record" in v["reason"], f"no record at all is not a pass: {v}")
        with open(os.path.join(jt, "TOD-100", "bad.json"), "w") as fh:
            fh.write("{oops")
        v = judged(block())
        expect(v["outcome"] == "could-not-check" and "cannot be read" in v["reason"], f"unreadable record: {v}")
        clear()
        record("002", result="maybe")
        v = judged(block())
        expect(v["outcome"] == "could-not-check" and "malformed" in v["reason"], f"malformed record: {v}")
        clear()
        record("003", runs=[{"check": "home", "viewport": "phone", "mode": "dark", "status": "great"}])
        expect("cannot read" in judged(block())["reason"], "a run with an unknown status is unreadable")
        clear()
        # could-not-run is never a pass
        record("004", result="could-not-run", runs=[run_of("could-not-run")])
        v = judged(block())
        expect(v["outcome"] == "could-not-check" and v["could_not_run"] == ["home/phone/dark"], f"could-not-run: {v}")
        clear()
        record("005", result="crashed", runs=[])
        v = judged(block())
        expect(v["outcome"] == "could-not-check", f"a crashed record is not a pass: {v}")
        clear()
        # a run the session did not claim could not run: a found-nothing cannot stand over it
        record("004b", result="could-not-run", runs=[run_of("passed"), run_of("could-not-run", "desktop")])
        v = judged(block())
        expect(v["outcome"] == "could-not-check" and v["could_not_run"] == ["home/desktop/dark"] and not v["disagreements"],
               f"an unclaimed could-not-run still blocks a found-nothing: {v}")
        clear()
        # a crashed record beside a clean one: the crash is not forgotten
        record("004c", result="crashed", runs=[])
        record("004d")
        v = judged(block())
        expect(v["outcome"] == "could-not-check" and any("crashed" in x for x in v["could_not_run"]),
               f"a crash earlier in the plan blocks a found-nothing: {v}")
        clear()
        # a record folder that cannot be listed
        record("004e")
        os.chmod(os.path.join(jt, "TOD-100"), 0)
        try:
            v = judged(block())
        finally:
            os.chmod(os.path.join(jt, "TOD-100"), 0o755)
        expect(v["outcome"] == "could-not-check" and "cannot be read" in v["reason"], f"an unreadable folder: {v}")
        clear()
        # a mechanical failure beats the session's "found nothing"
        record("006", result="failed", runs=[run_of("failed")])
        v = judged(block())
        expect(v["outcome"] == "found-problems" and v["mechanical_failures"] == ["home/phone/dark"],
               f"the record's failure overrides the session's found-nothing: {v}")
        expect(any("passed" in d and "failed" in d for d in v["disagreements"]),
               f"…and a session that called it passed is named as disagreeing: {v}")
        # a session that admits the run failed while saying found-nothing is refused by the schema itself
        v = judged(block(ran=[{"check": "home", "viewport": "phone", "mode": "dark", "result": "failed"}]))
        expect(v["outcome"] == "could-not-check" and "fails the schema" in v["reason"], f"{v}")
        clear()
        # what the session says it ran must be in the record
        record("007")
        v = judged(block(ran=[{"check": "home", "viewport": "phone", "mode": "dark", "result": "passed"},
                              {"check": "home", "viewport": "desktop", "mode": "dark", "result": "passed"}]))
        expect(v["outcome"] == "could-not-check" and any("holds no such run" in d for d in v["disagreements"]),
               f"a run only the session claims is a disagreement, and not a pass: {v}")
        # problems stand as the session's word; a could-not-run does not erase them
        v = judged(block(outcome="found-problems", problems=[problem()],
                         ran=[{"check": "home", "viewport": "phone", "mode": "dark", "result": "passed"}]))
        expect(v["outcome"] == "found-problems" and v["problems"][0]["id"] == "white-card", f"problems pass through: {v}")
        clear()
        record("008", result="could-not-run", runs=[run_of("could-not-run"), run_of("passed", "desktop")])
        v = judged(block(outcome="found-problems", problems=[problem()],
                         ran=[{"check": "home", "viewport": "desktop", "mode": "dark", "result": "passed"}]))
        expect(v["outcome"] == "found-problems" and v["could_not_run"], f"problems are kept beside a could-not-run: {v}")
        # the session's own could-not-check stands, with its reason
        v = judged(block(outcome="could-not-check", ran=[], reason="The build failed."))
        expect(v["outcome"] == "could-not-check" and v["reason"] == "The build failed.", f"{v}")
        clear()
        # trust: only the runner's alone, or a base copy, is mechanical
        for trust, want in (("runner-only", False), ("base-copy", False), ("session-code-in-runner", True), (None, True)):
            record("t", trust=trust)
            if trust is None:
                path = os.path.join(jt, "TOD-100", "t.json")
                doc = json.load(open(path))
                del doc["outcome_trust"]
                json.dump(doc, open(path, "w"))
            v = judged(block())
            expect(v["outcome"] == "found-nothing" and v["not_mechanical"] is want, f"trust {trust}: {v}")
            clear()
        # the latest record wins for the same run: a failure that a later run cleared is not still a failure
        record("010", result="failed", runs=[run_of("failed")])
        record("011", result="passed", runs=[run_of("passed")])
        v = judged(block())
        expect(v["outcome"] == "found-nothing" and not v["mechanical_failures"], f"the latest record wins: {v}")

    # 9. the source starts no process, opens no socket, calls no tracker
    src = open(os.path.abspath(__file__)).read().split("# --------------------------------------------------------------------------- selftest", 1)[0]
    for token in ("subprocess", "socket", "urllib", "http.client", "os.system", "Popen"):
        expect(token not in src, f"slice 1 must not use {token!r}")

    if failures:
        print("pipeline_acceptance_poller selftest: FAIL")
        for f in failures:
            print("  -", f)
        return 1
    print("pipeline_acceptance_poller selftest: OK")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    d = sub.add_parser("decide", help="read-only: say which plans would start, and why the others do not")
    d.add_argument("--facts", required=True)
    d.add_argument("--ledger", required=True)
    args = ap.parse_args(argv)
    if args.selftest:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from scratch_run import contained   # its temp folders go when it ends
        return contained(selftest, "acceptance-poller-selftest-run-")
    if args.cmd != "decide":
        ap.print_help()
        return EXIT_USAGE
    try:
        results = run_pass(args.facts, args.ledger, time.time())
    except BusyError as exc:
        print("NOTHING DONE: %s" % exc, file=sys.stderr)
        return EXIT_OK
    except (FactsError, LedgerError) as exc:
        print("COULD NOT DECIDE: %s. Nothing was started; this is not a pass." % exc, file=sys.stderr)
        return EXIT_ERROR
    print(render(results) if results else "no plans in the facts file")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
