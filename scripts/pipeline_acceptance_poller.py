#!/usr/bin/env python3
"""The acceptance poller, slice 1: when may a plan's acceptance check start, and never twice for one head
(KIT-551, a slice of KIT-324; docs/ACCEPTANCE-POLLER-OPERATOR.md).

    python3 scripts/pipeline_acceptance_poller.py decide --facts FILE --ledger FILE   # read-only: say what would start
    python3 scripts/pipeline_acceptance_poller.py --selftest

WHAT THIS SLICE IS.  The decision, not the doing. It reads the facts about each approved plan from a
facts file (fields only; a later slice reads them from the tracker and GitHub) and answers, for each plan,
START or a NAMED REASON why not. `run_pass()` also keeps the ledger, and calls the `start` function it is
handed. This slice hands it nothing, so the command line only ever decides. It calls no tracker, starts no
session, sends nothing, and writes nothing but the ledger, through `run_pass()`.

WHEN A CHECK MAY START (the design, docs/adr: before the last piece merges):
  acceptance is on for the plan's repository, AND every piece but one is Done, AND that one piece, the
  last, has a pull request that is open, green and reviewed.
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
                or not SHA_RE.match(str(r.get("head") or "")) or r.get("status") not in ("starting", "started", "failed"):
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
    """One pass. Returns [(plan, verdict, detail)]. `start(plan, head)` is called for each plan that may
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
        expect(got == want, f"{name}: got {got}, want {want}")
    expect(set(v for v in REASONS if v not in ("already-started", "start-unconfirmed", "start-failed")) ==
           {"acceptance-off", "no-pieces", "pieces-pending", "all-done", "no-pr", "pr-not-open", "checks-red",
            "checks-pending", "checks-unknown", "review-pending", "review-declined", "review-unknown"},
           "the closed words the docstring names are the ones decide_plan can return")

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

        # 5. a ledger that cannot be read stops the pass; absent is empty
        for text in ("{not json", json.dumps({"schema": "other/1", "rows": []}),
                     json.dumps({"schema": LEDGER_SCHEMA, "rows": [{"plan": "x"}]})):
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
    except (FactsError, LedgerError) as exc:
        print("COULD NOT DECIDE: %s. Nothing was started; this is not a pass." % exc, file=sys.stderr)
        return EXIT_ERROR
    print(render(results) if results else "no plans in the facts file")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
