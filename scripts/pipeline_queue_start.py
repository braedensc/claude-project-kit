#!/usr/bin/env python3
"""The queue's starter: what may start now, and the ledger row written before each start (KIT-452,
MVP 2 part 3a; docs/adr/2026-11-03-autonomous-workers-within-a-budget-and-a-window.md, decisions
2, 4, 5 and 9).

    python3 scripts/pipeline_queue_start.py preview --config <queue.json>
    python3 scripts/pipeline_queue_start.py --selftest

Through the day the queue starts the next ready piece whenever a lane and the budget have room.
The bridge does the starting (part 3b): it holds the tracker key and delegates. This file is what
it asks first, and what it writes before each start.

  decide(raw_config, gql, now)   What may start now, as the order says it. The working window is
                                 asked first: closed, nothing is read and nothing starts. Open,
                                 the queue's reader gathers (scripts/pipeline_queue_read.py,
                                 through `gql`), then the pool judges and the order decides, as
                                 merged. A read that failed starts nothing, and says why.
  record_start(path, now, ticket, source)
                                 The ledger row, written BEFORE a start: today's starts plus one,
                                 agent-filed starts plus one for that source, and a row naming the
                                 ticket, its source and the time. The reader counts the day from
                                 this file, so a start it never recorded would let the day's caps
                                 drift. A row for a start that then failed only holds one more
                                 back: the safe side.
  attribution(entry)             The comment a start leaves on its ticket: a fixed sentence for
                                 each source, filled with ids, a lane and a time, never text.

ONLY THE ORDER'S `start` LIST STARTS.  What the reader found of a ticket is merged into its verdict
by the morning job's seven EXTRAS keys and no others, so nothing the reader wrote can admit a
ticket: `admitted` comes from the pool alone.

THE LEDGER.  One JSON object of today, `{"date", "starts", "agent_filed_starts", "rows"}`. A file
dated before today is a new day at zero, and a missing one means no start yet, as the reader reads
it. An unreadable one, one dated after today, or one whose counts are not whole numbers refuses:
nothing starts until a person looks. It is written as the reader writes its file: whole or not at
all, mode 0600, in a folder outside every git working tree, where a session could write.

THE CONFIG.  The queue's one file (see scripts/pipeline_queue_read.py): the reader checks `pool`,
`order` and `reader`, `window` is pipeline_window's, and the usage file is the one the `morning`
section names. The ledger is the reader's `reader.ledger_file`, so both read the same file.

WHERE IT CANNOT HELP.  It starts nothing itself: the bridge's delegation is part 3b. Lanes keep no
footprint between passes yet, so a repository runs one lane unless its merge gate is ready: the
safe side. The preview reads, prints counts, ids and words, and writes nothing.
"""
import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    import pipeline_queue_order as order
    import pipeline_queue_pool as pool
    import pipeline_queue_read as reader
    import pipeline_status_local as status_job
    import pipeline_window as window
except ImportError as exc:  # a packaging accident, never a state to run in
    sys.exit("FAIL: %s. The starter runs those files' own code and keeps no copy." % exc)

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_PARTIAL = 0, 1, 2, 3
# What the reader found of a ticket that the order reads: the morning job's own list
# (scripts/pipeline_morning.py), pinned equal by the selftest.
EXTRAS = ("repo", "priority", "plan_priority", "effort", "blockers", "title", "pointers")
SOURCES = ("todo", "backlog", "plan", "agent_filed")
DEFAULT_USAGE = "~/.stage-e/usage/usage.json"      # the morning job's own default
TICKET_RE = re.compile(r"^[A-Z][A-Z0-9]{0,15}-[0-9]{1,9}\Z")
LANE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}:[0-9]{1,3}\Z")
TIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}):\d{2}(?:\.\d{1,9})?Z\Z")
WHY = {"todo": "you moved it into the ready state{at}",
       "backlog": "you filed it in the backlog, and the backlog may start",
       "plan": "it is a piece of {plan}, which you approved{at}",
       "agent_filed": "it is an agent-filed finding small enough to start by itself"}
write = reader.write        # whole or not at all, mode 0600, never inside a git working tree


# --------------------------------------------------------------------------- #
# The config
# --------------------------------------------------------------------------- #
def load(raw):
    """(cfg, problems): the reader's sections as it checks them, the window, and the usage file
    the morning section names; every problem at once. cfg is None while there is any."""
    if not isinstance(raw, dict):
        return None, ["config: not an object"]
    cfg, bad = reader.read_config(raw)
    wcfg, wbad = (window.config(raw.get("window", {})) if isinstance(raw.get("window", {}), dict)
                  else (None, ["not an object"]))
    bad = list(bad) + ["window: %s" % w for w in wbad]
    morning = raw.get("morning", {})
    usage = None
    try:
        usage = reader.resolve_path(morning.get("usage_file", DEFAULT_USAGE) if isinstance(
            morning, dict) else None, "morning.usage_file")
    except ValueError as exc:
        bad.append(str(exc))
    return (None, bad) if bad else (dict(cfg, window=wcfg, usage_file=usage), [])


# --------------------------------------------------------------------------- #
# What may start now
# --------------------------------------------------------------------------- #
def with_extras(verdicts, extras):
    """Each verdict with what the reader found of its ticket, by EXTRAS only."""
    out = []
    for v in verdicts:
        found = extras.get(v.get("ticket")) if isinstance(extras, dict) else None
        out.append(dict(v, **dict((k, found[k]) for k in EXTRAS if k in found))
                   if isinstance(found, dict) else dict(v))
    return out


def read_usage(path):
    """The usage file, or None: the order stops on one it cannot read, and says so."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError, RecursionError):
        return None


def decide(raw_config, gql, now, clock=time.time, say=None):
    """{"state", "why", "window", "inputs", "decision"}. `state` is "closed" (`why` is the
    window's word), "unread" (the reader's pass failed; `why` is its reason) or "decided"
    (`decision` is the order's; `why` is "partial" when a source was read in part, else None).
    Raises ValueError for a config that is not usable."""
    cfg, bad = load(raw_config)
    if bad:
        raise ValueError("the queue's config is not usable: " + "; ".join(bad))
    win = window.window_at(cfg["window"], now)
    out = {"state": "closed", "why": win["why"], "window": win, "inputs": None, "decision": None}
    if not win["open"]:
        return out
    inputs = out["inputs"] = reader.gather(raw_config, gql, now, clock, say)
    if inputs["result"] == "failed":
        return dict(out, state="unread", why=inputs["sources"]["tracker"]["reason"]
                    or "the tracker was not read")
    verdicts = pool.decide(cfg["pool"], now, inputs["tickets"], inputs["epics"],
                           inputs["histories"], inputs["busy"], inputs["findings"],
                           inputs["plans"], inputs["ledger"])
    decision = order.order(cfg["order"], now, with_extras(verdicts, inputs["extras"]),
                           inputs["running"], read_usage(cfg["usage_file"]), inputs["ledger"])
    return dict(out, state="decided", decision=decision,
                why="partial" if inputs["result"] == "partial" else None)


# --------------------------------------------------------------------------- #
# The ledger, written before each start
# --------------------------------------------------------------------------- #
def read_ledger(path, today):
    """Today's ledger as written, or a new day's at zero. Raises ValueError for one that cannot
    be counted on, OSError for a file that cannot be read."""
    zero = {"date": today, "starts": 0, "agent_filed_starts": 0, "rows": []}
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return zero
    except (ValueError, RecursionError):
        raise ValueError("the ledger is not JSON")
    rows = doc.get("rows", []) if isinstance(doc, dict) else None
    if not (isinstance(doc, dict) and reader.is_day(doc.get("date"))
            and reader.whole_count(doc.get("starts"))
            and reader.whole_count(doc.get("agent_filed_starts"))
            and isinstance(rows, list) and all(isinstance(r, dict) for r in rows)):
        raise ValueError("the ledger is not a ledger of starts")
    if doc["date"] > today:
        raise ValueError("the ledger is dated after today")
    if doc["date"] < today:
        return zero
    return {"date": today, "starts": doc["starts"],
            "agent_filed_starts": doc["agent_filed_starts"], "rows": list(rows)}


def counted(led, ticket, source, now):
    """The ledger with one more start of `source`."""
    return dict(led, starts=led["starts"] + 1,
                agent_filed_starts=led["agent_filed_starts"] + (source == "agent_filed"),
                rows=led["rows"] + [{"ticket": ticket, "source": source, "at": pool.iso(now)}])


def record_start(path, now, ticket, source):
    """Write today's row for a start about to be made, and return the ledger as written. Raises
    ValueError (a ledger that cannot be counted on, a ticket or source of the wrong shape, a
    folder inside a git working tree) or OSError (the disk would not take it): either way,
    nothing may start."""
    if not (isinstance(ticket, str) and TICKET_RE.match(ticket)) or source not in SOURCES:
        raise ValueError("a start names a ticket id and one of %s" % ", ".join(SOURCES))
    led = counted(read_ledger(path, pool.local_date(now)), ticket, source, now)
    write(led, path)
    return led


# --------------------------------------------------------------------------- #
# The comment a start leaves
# --------------------------------------------------------------------------- #
def attribution(entry):
    """Fixed words for the start's source, with ids, its lane and a time; never ticket text.
    Raises ValueError for an entry that is not one of the order's starts."""
    ticket, source, lane = (entry.get(k) for k in ("ticket", "source", "lane"))
    if not (isinstance(ticket, str) and TICKET_RE.match(ticket) and source in SOURCES
            and isinstance(lane, str) and LANE_RE.match(lane)):
        raise ValueError("not a start the order made")
    at = TIME_RE.match(entry["at"]) if isinstance(entry.get("at"), str) else None
    plan = entry.get("plan")
    return "Started by the queue, in lane %s: %s." % (lane, WHY[source].format(
        at=" at %s %s UTC" % at.groups() if at else "",
        plan=plan if isinstance(plan, str) and TICKET_RE.match(plan) else "a plan"))


# --------------------------------------------------------------------------- #
# The preview: what would start now, read and never written
# --------------------------------------------------------------------------- #
def summary(result):
    """Counts, ids and words: never a title or text."""
    if result["state"] == "closed":
        return ["the working window is closed (%s): nothing would start" % result["why"]]
    if result["state"] == "unread":
        return ["the queue could not be read (%s): nothing would start" % result["why"]]
    d = result["decision"]
    held = {}
    for h in d["held"]:
        held[h["reason"]] = held.get(h["reason"], 0) + 1
    lines = ["would start %d: %s" % (len(d["start"]), ", ".join(
        "%s (%s, %s)" % (s["ticket"], s["lane"], s["source"]) for s in d["start"]) or "none")]
    lines.append("held %d%s" % (len(d["held"]), ": " + ", ".join(
        "%s %d" % kv for kv in sorted(held.items())) if held else ""))
    lines.append("stops: %s" % (", ".join(s["stop"] for s in d["stops"]) or "none"))
    if result["why"] == "partial":
        lines.append("the queue was read in part")
    return lines


def preview(path, environ=None, now=None, out=print):
    """The exit code: 0 decided or closed, 3 read in part, 1 could not read, 2 config."""
    environ = os.environ if environ is None else environ
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError, RecursionError) as exc:
        out("CONFIG: could not read --config (%s)" % type(exc).__name__)
        return EXIT_USAGE
    cfg, bad = load(raw)
    if bad:
        for line in bad:
            out("CONFIG: %s" % line)
        return EXIT_USAGE
    key, why = status_job.credential(environ, cfg["reader"]["linear_key_env"])
    if key is None:
        out("the tracker key is not usable (%s): nothing was read" % why)
        return EXIT_ERROR
    result = decide(raw, lambda q, v: status_job.linear_graphql(q, v, key),
                    time.time() if now is None else now, time.monotonic,
                    lambda line: sys.stderr.write(line.replace(key, "[key]") + "\n"))
    for line in summary(result):
        out(line.replace(key, "[key]"))
    return {"unread": EXIT_ERROR}.get(result["state"], EXIT_PARTIAL if result["why"] == "partial"
                                      else EXIT_OK)


def main(argv=None):
    p = argparse.ArgumentParser(prog="pipeline_queue_start.py", description=__doc__.split("\n")[0])
    p.add_argument("--selftest", action="store_true")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("preview").add_argument("--config", required=True)
    args = p.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.cmd != "preview":
        p.print_help()
        return EXIT_USAGE
    return preview(args.config)


# --------------------------------------------------------------------------- #
# --selftest: no tracker, no network, files only in a temp folder
# --------------------------------------------------------------------------- #
def selftest():
    import socket
    import tempfile
    fails, ran = [], [0]

    def check(name, got, want):
        ran[0] += 1
        good = got == want
        print("[%s] %s%s" % ("PASS" if good else "FAIL", name,
                             "" if good else "  (got %r, want %r)" % (got, want)))
        if not good:
            fails.append(name)

    def refuse(*a, **k):
        raise OSError("the selftest opens no socket")
    saved = (socket.socket, os.environ.get("TZ"), reader.account_home, reader.gather)
    socket.socket = refuse
    os.environ["TZ"] = "TST-3"          # the local day is UTC+3
    time.tzset()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(os.path.realpath(tmp), "home")
            os.mkdir(home)
            reader.account_home = lambda: home
            _cases(check, os.path.realpath(tmp), home)
    finally:
        socket.socket, zone, reader.account_home, reader.gather = saved
        if zone is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = zone
        time.tzset()
    print("\n%d/%d checks passed" % (ran[0] - len(fails), ran[0]))
    return 1 if fails else 0


def _cases(check, tmp, home):
    import check_ticket_dor as dor
    import pipeline_morning as morning
    OWNER, OTHER, LEAK = "owner-1", "other-1", "SENTINEL-TITLE-61b2"
    NOW = pool.epoch("2026-11-04T11:00:00Z")            # a Wednesday, 14:00 local
    TODAY = pool.local_date(NOW)
    GIT = os.path.join(tmp, "tree")
    os.makedirs(os.path.join(GIT, ".git"))
    calls = []

    def move(actor):
        return dict(dict.fromkeys(pool.HISTORY_KEYS), createdAt="2026-11-03T10:00:00Z",
                    actorId=actor, fromStateId="st-backlog", toStateId="st-todo")

    def issue(n):
        return {"id": "id-%d" % n, "identifier": "ENG-%d" % n, "title": "Title %s" % LEAK,
                "description": dor.GOOD_DESCRIPTION, "createdAt": "2026-11-02T09:00:00Z",
                "team": {"key": "ENG"}, "creator": {"id": OWNER}, "delegate": None, "parent": None,
                "project": {"id": "p"}, "state": {"id": "st-todo", "name": "Todo",
                                                  "type": "unstarted"},
                "children": {"nodes": []}, "inverseRelations": {"nodes": []},
                "labels": {"nodes": [{"name": x} for x in (
                    "track:platform", "effort:S", "provenance:human")]}}

    def extra(n, **more):
        return dict({"repo": "acme/app", "priority": 0, "plan_priority": 0, "effort": "S",
                     "blockers": [], "title": "Title %s" % LEAK, "pointers": []}, **more)

    def inputs(result="ok", **over):
        doc = {"schema": "pipeline-queue-inputs/1", "result": result,
               "sources": {"tracker": {"state": "read", "reason": None}},
               "tickets": [issue(1), issue(2)], "epics": [], "busy": [], "running": [],
               "histories": {"id-1": [move(OWNER)], "id-2": [move(OTHER)]},
               "findings": {}, "plans": {},
               "ledger": {"date": TODAY, "starts": 0, "agent_filed_starts": 0},
               "extras": {"ENG-1": extra(1), "ENG-2": extra(2, repo="acme/web", admitted=True,
                                                            reason="admitted")}}
        doc.update(over)
        return doc
    usage = os.path.join(tmp, "usage.json")
    with open(usage, "w", encoding="utf-8") as fh:
        json.dump({"schema": "pipeline-usage/1", "written_at": pool.iso(NOW - 300),
                   "result": "ok", "days": [{"date": TODAY, "usd": 6.5}], "pieces": [],
                   "status": {"windows": {}}, "sign_in": {"api_key_runs": []}}, fh)
    RAW = {"pool": {"owner_user_ids": [OWNER], "dispatcher_user_ids": ["disp-1"],
                    "team_keys": ["ENG"], "reviews_team_key": "REV"},
           "window": {"time_zone": "UTC", "days": {"wed": {"start": "09:00", "hours": 6}}},
           "reader": {"repo_by_team": {"ENG": "acme/app"},
                      "ledger_file": os.path.join(tmp, "q", "ledger.json")},
           "morning": {"usage_file": usage}}

    def run(doc=None, now=NOW, raw=None):
        calls[:] = []

        def stub(raw_, gql, now_, clock=None, say=None):
            calls.append(now_)
            return doc or inputs()
        reader.gather = stub
        r = decide(raw or RAW, None, now)
        return (r["state"], r["why"], len(calls),
                [s["ticket"] for s in (r["decision"] or {}).get("start", [])],
                [s["stop"] for s in (r["decision"] or {}).get("stops", [])])

    def ledger_case(seed, *starts):
        path = os.path.join(tempfile_dir(), "ledger.json")
        if seed is not None:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(seed if isinstance(seed, str) else json.dumps(seed))
        before, got = open(path).read() if seed is not None else None, None
        try:
            for ticket, source in starts:
                got = record_start(path, NOW, ticket, source)
        except ValueError as exc:
            return ("refused", str(exc), open(path).read() == before if seed is not None
                    else not os.path.exists(path))
        mode = oct(os.stat(path).st_mode & 0o777)
        return (got["starts"], got["agent_filed_starts"],
                [(r["ticket"], r["source"], r["at"]) for r in got["rows"]], mode,
                reader.read_ledger(path, TODAY, lambda line: None)[0]["starts"])
    counter = [0]

    def tempfile_dir():
        counter[0] += 1
        path = os.path.join(tmp, "l%d" % counter[0])
        os.makedirs(path)
        return path
    AT = pool.iso(NOW)
    ENTRY = {"ticket": "ENG-1", "lane": "acme/app:1", "source": "todo", "plan": None,
             "at": "2026-11-03T10:00:00Z", "title": LEAK}

    def say(**kw):
        try:
            return attribution(dict(ENTRY, **kw))
        except ValueError:
            return "refused"

    def battery():
        return [
            ("config: a good one loads, and every fault is named at once",
             (load(RAW)[1], len(load(dict(RAW, oops=1, window={"days": {"wed": "9"}},
                                          morning={"usage_file": "u.json"}))[1])), ([], 3)),
            ("closed windows read nothing and start nothing",
             [run(now=pool.epoch("2026-11-04T08:00:00Z"))[:3],
              run(now=pool.epoch("2026-11-08T11:00:00Z"))[:3]],
             [("closed", "before-start", 0), ("closed", "day-off", 0)]),
            ("open: the owner's move starts, another's is held, extras admit nothing",
             run(), ("decided", None, 1, ["ENG-1"], [])),
            ("a failed read starts nothing and says why",
             run(inputs("failed", sources={"tracker": {"state": "could-not-read",
                                                       "reason": "the tracker did not answer"}})),
             ("unread", "the tracker did not answer", 1, [], [])),
            ("a partial read decides, and says so", run(inputs("partial"))[:2],
             ("decided", "partial")),
            ("a usage file it cannot read stops every start",
             run(raw=dict(RAW, morning={"usage_file": os.path.join(tmp, "none.json")}))[3:],
             ([], ["usage-missing"])),
            ("the extras are the morning job's seven", EXTRAS, morning.EXTRAS),
            ("ledger: none yet, then the same day, then an agent-filed start",
             ledger_case(None, ("ENG-1", "todo"), ("ENG-2", "plan"), ("ENG-3", "agent_filed")),
             (3, 1, [("ENG-1", "todo", AT), ("ENG-2", "plan", AT), ("ENG-3", "agent_filed", AT)],
              "0o600", 3)),
            ("ledger: a day gone by starts again at zero",
             ledger_case({"date": "2026-11-03", "starts": 9, "agent_filed_starts": 4, "rows": []},
                         ("ENG-1", "todo"))[:3], (1, 0, [("ENG-1", "todo", AT)])),
            ("ledger: today's counts go on",
             ledger_case({"date": TODAY, "starts": 2, "agent_filed_starts": 1},
                         ("ENG-1", "backlog"))[:2], (3, 1)),
            ("ledger: dated ahead, not JSON, or not whole counts refuses, and leaves it",
             [ledger_case(s, ("ENG-1", "todo")) for s in (
                 {"date": "2026-11-05", "starts": 0, "agent_filed_starts": 0}, "{not json",
                 {"date": TODAY, "starts": 1.5, "agent_filed_starts": 0},
                 {"date": TODAY, "starts": 1, "agent_filed_starts": 0, "rows": ["x"]})],
             [("refused", "the ledger is dated after today", True),
              ("refused", "the ledger is not JSON", True),
              ("refused", "the ledger is not a ledger of starts", True),
              ("refused", "the ledger is not a ledger of starts", True)]),
            ("ledger: a start of no source or no ticket refuses, and writes nothing",
             [ledger_case(None, s) for s in (("ENG-1", "none"), ("eng-1", "todo"))],
             [("refused", "a start names a ticket id and one of todo, backlog, plan, "
               "agent_filed", True)] * 2),
            ("ledger: a folder inside a git working tree is refused",
             _refused(lambda: record_start(os.path.join(GIT, "l.json"), NOW, "ENG-1", "todo")),
             True),
            ("attribution: a fixed sentence for each source, ids and a time only",
             [say(), say(at=None), say(source="plan", plan="ENG-10"),
              say(source="backlog"), say(source="agent_filed"), say(source="plan", plan="x")],
             ["Started by the queue, in lane acme/app:1: you moved it into the ready state at "
              "2026-11-03 10:00 UTC.",
              "Started by the queue, in lane acme/app:1: you moved it into the ready state.",
              "Started by the queue, in lane acme/app:1: it is a piece of ENG-10, which you "
              "approved at 2026-11-03 10:00 UTC.",
              "Started by the queue, in lane acme/app:1: you filed it in the backlog, and the "
              "backlog may start.",
              "Started by the queue, in lane acme/app:1: it is an agent-filed finding small "
              "enough to start by itself.",
              "Started by the queue, in lane acme/app:1: it is a piece of a plan, which you "
              "approved at 2026-11-03 10:00 UTC."]),
            ("attribution: not one of the order's starts is refused",
             [say(source="none"), say(lane="acme"), say(ticket="ENG-1 " + LEAK)],
             ["refused"] * 3),
            ("no title or text reaches a sentence or a summary",
             any(LEAK in x for x in [say(), say(source="plan", plan="ENG-10")]
                 + summary(decide_view())), False),
            ("preview: a config it cannot use is exit 2, a missing key exit 1",
             (_preview({"oops": 1}), _preview(RAW)), (2, 1)),
        ]

    def decide_view():
        reader.gather = lambda *a, **k: inputs()
        return decide(RAW, None, NOW)

    def _preview(raw):
        path = os.path.join(tempfile_dir(), "q.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(raw, fh)
        return preview(path, environ={}, now=NOW, out=lambda line: None)
    got = battery()
    for name, g, want in got:
        check(name, g, want)
    # -- mutants: a check that cannot fail checks nothing
    g = globals()
    o = dict((k, g[k]) for k in ("with_extras", "read_ledger", "counted", "attribution",
                                 "write", "SOURCES", "load"))
    base = [x[1] for x in got]

    def caught(**patched):
        g.update(patched)
        try:
            return [x[1] for x in battery()] != base
        except Exception:  # noqa: BLE001 — a mutant the cases cannot even run is caught too
            return True
        finally:
            g.update(o)
    for name, patch in (
            ("extras admit a ticket", dict(with_extras=lambda vs, ex: [
                dict(v, **(ex.get(v.get("ticket")) or {})) for v in vs])),
            ("a ledger of any day taken for today's",
             dict(read_ledger=lambda path, today: o["read_ledger"](path, "2999-12-31"))),
            ("every ledger read as zero", dict(read_ledger=lambda path, today: {
                "date": today, "starts": 0, "agent_filed_starts": 0, "rows": []})),
            ("an agent-filed start not counted", dict(counted=lambda led, t, s, n: o["counted"](
                led, t, "todo" if s == "agent_filed" else s, n))),
            ("the row left out", dict(counted=lambda led, t, s, n: dict(
                o["counted"](led, t, s, n), rows=led["rows"]))),
            ("the ledger readable by anyone", dict(write=lambda doc, path: (
                o["write"](doc, path), os.chmod(path, 0o644)))),
            ("any source started", dict(SOURCES=SOURCES + ("none",))),
            ("the title copied into the comment", dict(attribution=lambda e: o["attribution"](
                e) + " " + str(e.get("title")))),
            ("a closed window read anyway", dict(load=lambda raw: (lambda c: (c[0] and dict(
                c[0], window=window.config({"days": dict.fromkeys(
                    window.DAYS, {"start": "00:00", "hours": 24})})[0]), c[1]))(o["load"](raw))))):
        check("mutant: %s is caught" % name, caught(**patch), True)
    check("…and with every mutant undone the starter is as it was",
          [x[1] for x in battery()] == base, True)


def _refused(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


if __name__ == "__main__":
    sys.exit(main())
