#!/usr/bin/env python3
"""The validator approves a piece starting, in the owner's place (KIT-543). Design: docs/adr/2026-11-05-
the-validator-approves-inside-limits-that-code-enforces.md, "What it approves".

    python3 scripts/pipeline_validator_pieces.py run --config ~/.validator/config.json [--dry-run]
    python3 scripts/pipeline_validator_pieces.py --selftest

WHAT IT JUDGES.  A ready piece of an approved plan that the queue holds only for the owner's `start`:
one added to the plan, or changed, after he approved the plan (the pool's `added-after-approval` and
`changed`). Nothing else: not a plan (KIT-544), not a merge (the merge path), not a ticket outside a
plan, and never a piece held for any other reason.

ONE PASS, in order. Each step says what it did, or which nothing.
  1. The switches (pipeline_validator_settings.load): settings not read, or the pieces switch off:
     nothing more, said (OFF).
  2. The watcher's hold: a hold, or a hold file not named or not readable: nothing more (HELD).
  3. The ledger, by its own reader. One that cannot be read or trusted: nothing more.
  4. The queue's own pass (pipeline_queue_start.decide), over the queue's config, through the
     validator's read-only tracker key: the pool's verdict on every candidate, and the order. A
     window closed, no room, or a tracker not read: nothing more, said.
  5. Each piece that waits only for the owner's `start`, oldest ticket first:
       - its facts (pipeline_validator_facts.for_piece), the hard limits, and its evidence: the
         queue's own verdict that its plan is approved and only its change since waits for him;
       - a hold no later pass can settle (the safety-system floor, a human-only class the owner has
         not allowed, an idea he did not file) is a `held` row, once: the same hold on a piece that
         has not changed since its row is not written again;
       - a piece the agent already judged, unchanged since, waits for the owner: no second run;
       - any other wait writes nothing, and is said;
       - both passing: the ticket brief (the piece as written, against its plan's criteria:
         pipeline_validator_brief.build_ticket_brief), ONE agent run, the route's decision, and its
         row. A send-back has no dev session yet, so it is an escalation. At most one agent run per
         pass. The piece is read again after the agent answers: one whose text or plan's criteria
         changed meanwhile gets no row, and a later pass judges it as it stands.

ITS LAST CHANGE.  The queue's own pass read each piece's creation and whole history; the pool's own
reader (read_history) gives the newest time of them. A row newer than that is about the piece as it
stands. The pool admits on an approval by the same rule.

ITS QUEUE FACT.  A held piece is not in the queue's order until it is admitted, so its queue fact is
the order's runaway stops for this pass, and no load of its own: the order judges its load when it
starts it, as for any piece.

HOW A PIECE STARTS.  Never here. The queue's starter starts it: its reader reads this ledger and the
settings (`reader.validator_ledger_file` and `reader.validator_settings_file` in the queue's config),
and its pool admits the piece `via` "validator" while the pieces switch is on and the approval is
newer than the piece's last change. The watcher's hold outranks it there too. With either file not
named, or the switch off, the owner's `start` decides, as before.

WHAT IT NEVER DOES (asserted in --selftest): start a piece, write to the tracker, label, or approve a
review. Its one write is the ledger's one writer.

EXIT CODES (contract §13):
  0  the pass ran: it judged, recorded, waited, or had nothing to do, and said which. Off and held
     are said too.
  1  a write failed: a ledger row. Said.
  2  the config is not valid, or this is an agent's environment.
  3  something could not be read: the hold, the ledger, or the queue's pass.
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    import pipeline_queue_pool as pool
    import pipeline_queue_start as queue_start
    import pipeline_status_local as status_job
    import pipeline_validator_brief as brief
    import pipeline_validator_facts as facts
    import pipeline_validator_ledger as ledger
    import pipeline_validator_limits as limits
    import pipeline_validator_route as route
    import pipeline_validator_run as vrun
    import pipeline_validator_settings as vsettings
except ImportError as exc:  # a packaging accident, never a state to run in
    sys.exit("FAIL: %s. The piece pass uses those files' own code and keeps no copy." % exc)

CONFIG_SCHEMA = "pipeline-validator-config/1"
EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_PARTIAL = 0, 1, 2, 3
# The validator's one config file; the sections this pass does not read are passed over unread.
TOP_KEYS = ("schema", "settings_file", "ledger_file", "merge", "facts", "agent", "check")
# The pool's holds a `start` of the owner's would settle, for a piece of a plan he approved.
WAITING = ("added-after-approval", "changed")


def config(raw):
    """(cfg, problems) for the validator's config, as the piece pass reads it: its paths, and the
    `facts` and `agent` sections by their owners' own rules. Every problem at once, by key."""
    if not isinstance(raw, dict):
        return None, ["the config is not a JSON object"]
    bad = ["%s: not a setting" % repr(k)[:40] for k in sorted(raw, key=repr) if k not in TOP_KEYS]
    if raw.get("schema") != CONFIG_SCHEMA:
        bad.append("schema: must be %s" % CONFIG_SCHEMA)
    cfg = {}
    for key in ("settings_file", "ledger_file"):
        v = raw.get(key)
        if not isinstance(v, str) or not v or "\0" in v or not os.path.isabs(os.path.expanduser(v)):
            bad.append("%s: must be an absolute path (~/ is allowed)" % key)
        else:
            cfg[key] = os.path.expanduser(v)
    fcfg, fbad = facts.config(raw.get("facts", {}))
    bad += ["facts.%s" % b for b in fbad]
    cfg["facts"] = fcfg
    _s, abad = vrun.settings(raw.get("agent"))
    if abad:
        bad.append("agent.%s: not valid" % abad if abad != "the config" else
                   "agent: must be an object naming agent_bin")
    cfg["agent"] = raw.get("agent")
    return (None if bad else cfg), bad


def load_config(path):
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return None, ["the config at %s could not be read as JSON" % path]
    return config(raw)


class Host(object):
    """The world as the piece pass meets it: the queue's own pass, through the validator's read-only
    tracker key; a piece's facts; the agent's run. It writes nothing."""

    def __init__(self, cfg, environ=None, runner=None):
        self.cfg, self.runner = cfg, runner
        self.environ = os.environ if environ is None else environ

    def queue(self, now, say):
        """The queue's own pass, or facts.Unread in fixed words."""
        key, why = status_job.credential(self.environ, self.cfg["facts"]["linear_key_env"])
        if key is None:
            raise facts.Unread("the tracker key: %s" % why)
        raw = facts._queue_raw(self.cfg["facts"])
        try:
            return queue_start.decide(raw, lambda q, v: status_job.linear_graphql(q, v, key), now,
                                      say=lambda line: None)
        except ValueError:
            raise facts.Unread("the queue's config is not usable")

    def facts(self, ticket, now, queue_result):
        return facts.for_piece(self.cfg["facts"], ticket, now, queue_result=queue_result)

    def run_agent(self, built):
        return vrun.run_agent(self.cfg["agent"], built, environ=self.environ, runner=self.runner)


def last_change(inputs, verdict):
    """The piece's last change, in seconds, as the queue's own pass read it: its creation, and every
    entry of its history, by the pool's own reader. None when it cannot be told."""
    if not isinstance(inputs, dict) or not isinstance(verdict, dict):
        return None
    ident = verdict.get("id")
    hist = pool.read_history((inputs.get("histories") or {}).get(ident))
    created = None
    for t in list(inputs.get("tickets") or []) + [k for e in inputs.get("epics") or []
                                                  for k in pool.children_of(e)]:
        if isinstance(t, dict) and t.get("id") == ident:
            created = pool.epoch(t.get("createdAt"))
    if hist is None or created is None:
        return None
    return max([created] + [when for when, _e in hist])


def _latest(rows, ticket):
    found = None
    for row in rows:
        if row["subject"] == "piece" and row["ref"] == ticket:
            found = row
    return found


def _number(ticket):
    try:
        return (ticket.split("-")[0], int(ticket.split("-")[1]))
    except (IndexError, ValueError):
        return (ticket, 0)


def run_pass(cfg, host, now=None, dry=False, say=print):
    """One pass. Returns its exit code (contract §13), having said what it did or which nothing."""
    now = time.time() if now is None else now
    settings = vsettings.load(cfg["settings_file"])
    if settings["state"] != vsettings.READ:
        say("OFF: %s." % settings["why"])
        return EXIT_OK
    if settings["approve"]["pieces"] is not True:
        say("OFF: the pieces switch is off in the validator's settings.")
        return EXIT_OK
    hold = limits.hold_state(cfg["facts"]["hold_file"])
    if hold != "none":
        say("HELD: %s, so no piece is judged." % {
            "held": "the watcher's hold stands",
            None: "no hold file is named (facts.hold_file), and nothing can say there is no hold"
        }.get(hold, "the watcher's hold file could not be read (%s)" % hold))
        return EXIT_OK if hold == "held" else EXIT_PARTIAL
    got = ledger.read(cfg["ledger_file"])
    if got["rows"] is None:
        say("COULD NOT JUDGE: %s, so no piece is approved." % got["why"])
        return EXIT_PARTIAL
    rows = got["rows"]
    try:
        res = host.queue(now, say)
    except facts.Unread as exc:
        say("COULD NOT JUDGE: the queue's own pass could not run (%s)." % exc)
        return EXIT_PARTIAL
    if res.get("state") != "decided":
        say("Nothing to judge: the queue's pass is %s (%s)." % (res.get("state"), res.get("why")))
        return EXIT_OK if res.get("state") in ("closed", "no-room") else EXIT_PARTIAL
    decision = res.get("decision") or {}
    inputs = res.get("inputs") or {}
    waiting = sorted((v for v in res.get("verdicts") or () if isinstance(v, dict)
                      and v.get("source") == "plan" and not v.get("admitted")
                      and v.get("reason") in WAITING and isinstance(v.get("ticket"), str)),
                     key=lambda v: _number(v["ticket"]))
    if not waiting:
        say("Nothing to judge: no piece waits for your start.")
        return EXIT_OK
    say("Pass: %d piece(s) wait for your start." % len(waiting))
    failed = False
    for v in waiting:
        ticket = v["ticket"]
        # A held piece is not in the order until admitted: the order's stops, and no load of its own.
        named = dict(decision, start=list(decision.get("start") or []) + [{"ticket": ticket}])
        doc = host.facts(ticket, now, named)
        l1 = limits.limits(doc["item"], settings, doc["limits"])
        l2 = {"verdict": "yes", "reasons": []}       # the queue's own verdict: it waits only for you
        decision_ = route.decide("piece", l1, l2, None, None)
        latest, last = _latest(rows, ticket), last_change(inputs, v)
        # A row about the piece as it stands: newer than its last change.
        fresh = (latest is not None and last is not None
                 and (pool.epoch(latest["at"]) or 0) > last)
        if decision_["record"]:
            if fresh and (latest["kind"], latest["reasons"]) == (decision_["kind"],
                                                                 decision_["reasons"]):
                say("  %s: still held for you (already recorded, unchanged since)." % ticket)
                continue
            say("  %s: held for you: %s." % (ticket, "; ".join(
                route.reason_words(decision_["reasons"]))))
            failed |= not _record(cfg, ticket, decision_, settings, dry, say)
            continue
        if decision_["wait"] != ["agent-not-run"]:
            say("  %s: waits: %s." % (ticket, "; ".join(decision_["wait"])))
            continue
        if fresh and latest["kind"] != "approved" and latest["layers"]["agent"] != "not-run":
            say("  %s: still waits for you: the validator's agent judged it (%s), and it has not "
                "changed since." % (ticket, latest["kind"]))
            continue
        try:
            built = brief.build_ticket_brief(doc["brief"].get("criteria"), doc["brief"].get("ticket"))
        except brief.BriefError as exc:
            say("  %s: waits: the agent's brief could not be built (%s)." % (ticket, exc))
            continue
        if dry:
            say("  [dry-run] %s passes the hard limits: would run the agent on it." % ticket)
            return EXIT_OK
        ran = host.run_agent(built)
        again = host.facts(ticket, now, named)
        if (again.get("brief") != doc.get("brief")
                or (again.get("limits") or {}).get("paths") != doc["limits"].get("paths")):
            say("  %s: it changed while the agent judged it, so nothing is recorded: a later pass "
                "judges it as it stands. The pass ends: one agent run per pass." % ticket)
            return EXIT_OK
        answer = route.answer_of(ran, built)
        decision_ = route.decide("piece", l1, l2, answer, None)
        if not decision_["record"]:
            say("  %s: waits: %s. The pass ends: one agent run per pass."
                % (ticket, "; ".join(decision_["wait"])))
            return EXIT_OK
        say("  %s: the validator %s its start." % (ticket, {
            "approved": "approved", "escalated": "escalated", "escalated-urgent": "urgently escalated",
            "held": "held"}.get(decision_["kind"], decision_["kind"])))
        failed |= not _record(cfg, ticket, decision_, settings, dry, say)
        say("The pass ends: one agent run per pass.")
        return EXIT_FAILED if failed else EXIT_OK
    say("Nothing approved: no piece passes yet.")
    return EXIT_FAILED if failed else EXIT_OK


def _record(cfg, ticket, decision, settings, dry, say):
    row = route.row_for("piece", ticket, None, decision, settings)
    if row is None:
        return True
    if dry:
        say("  [dry-run] would record %s for %s" % (row["kind"], ticket))
        return True
    try:
        ledger.append(cfg["ledger_file"], row, time.time)
    except (ledger.LedgerError, OSError) as exc:
        say("  %s: its %s could not be recorded (%s)" % (ticket, row["kind"], exc))
        return False
    return True


def main(argv=None, environ=None):
    ap = argparse.ArgumentParser(description="The validator approves a piece starting, in the "
                                             "owner's place.")
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run", help="one pass")
    r.add_argument("--config", default="~/.validator/config.json")
    r.add_argument("--dry-run", action="store_true",
                   help="read and judge, run no agent, and write nothing")
    args = ap.parse_args(argv)
    if args.selftest:
        from scratch_run import contained
        return contained(selftest, "validator-pieces-selftest-")
    if args.cmd != "run":
        ap.print_help()
        return EXIT_USAGE
    environ = os.environ if environ is None else environ
    markers = vrun.agent_markers(environ)
    if markers:
        print("REFUSED: this is an agent's environment (%s set). Only the validator's own job "
              "judges a piece." % ", ".join(markers))
        return EXIT_USAGE
    cfg, bad = load_config(args.config)
    if bad:
        print("COULD NOT RUN: %s" % "; ".join(bad))
        return EXIT_USAGE
    return run_pass(cfg, Host(cfg, environ), dry=args.dry_run)


# ======================================================================================
# SELFTEST: nothing below the line above is run by a caller, and the source scan stops there.
# ======================================================================================
class _FakeHost(object):
    """The queue's pass and a piece's facts, stood in for. The hard limits, the route, the ledger,
    the brief and the agent's run are the real ones; the agent's CLI is the run's own Stub."""

    def __init__(self, agent_cfg, environ):
        self.agent_cfg, self.environ = agent_cfg, environ
        self.stub, self.log, self.state = vrun.Stub(), [], "decided"
        self.waiting = {}            # ticket -> (reason, paths)
        self.changed, self.text, self.during = {}, {}, None    # ticket -> last change; its text

    def _hist(self, ticket):
        at = self.changed.get(ticket, pool.epoch("2026-11-01T00:00:00Z"))
        entry = dict((k, None) for k in pool.HISTORY_KEYS)
        entry.update(createdAt=pool.iso(at), actorId="someone")
        return [entry]

    def queue(self, now, say):
        verdicts = [{"ticket": t, "id": "id-" + t, "source": "plan", "admitted": False, "reason": r,
                     "reply": "start " + t} for t, (r, _p) in self.waiting.items()]
        verdicts.append({"ticket": "TOD-99", "source": "todo", "admitted": False, "reason": "changed"})
        # A piece of a paused plan has no start to give: never judged, though it sorts first.
        verdicts.append({"ticket": "TOD-5", "source": "plan", "admitted": False,
                         "reason": "plan-paused", "reply": None})
        inputs = {"tickets": [{"id": "id-" + t, "createdAt": "2026-10-30T00:00:00Z"}
                              for t in self.waiting], "epics": [],
                  "histories": dict(("id-" + t, self._hist(t)) for t in self.waiting)}
        return {"state": self.state, "why": "test", "verdicts": verdicts, "inputs": inputs,
                "decision": {"start": [], "held": [], "stops": [], "needs_ok": []}}

    def facts(self, ticket, now, queue_result):
        _r, paths = self.waiting.get(ticket, ("changed", ("src/app.py",)))
        q = limits.queue_fact(queue_result, ticket)
        return {"item": {"kind": "piece", "ref": ticket, "head": None},
                "limits": {"origin": "owner", "paths": list(paths), "risk_paths": [], "pr": None,
                           "window": True, "queue": q, "bounces": None, "watcher": "fresh",
                           "hold": "none"},
                "evidence": {"last_piece": None, "acceptance": None},
                "brief": {"criteria": "## Acceptance criteria\n\n- [ ] The pieces work together.\n",
                          "diff": None,
                          "ticket": self.text.get(ticket, "## Acceptance criteria\n\n- [ ] It works.\n")},
                "unread": {}}

    def run_agent(self, built):
        self.log.append("agent")
        got = vrun.run_agent(self.agent_cfg, built, environ=self.environ, runner=self.stub)
        if self.during:
            self.during()           # someone edits the piece while the agent runs
        return got


def selftest():
    import contextlib
    import io
    import re
    import tempfile
    failures, total = [], [0]

    def check(name, got, want):
        total[0] += 1
        if got != want:
            failures.append("FAIL %s: got %r, want %r" % (name, str(got)[:400], str(want)[:400]))

    with tempfile.TemporaryDirectory() as tmp:
        os.chmod(tmp, 0o700)
        settings_path = os.path.join(tmp, "validator", "settings.json")
        ledger_path = os.path.join(tmp, "validator", "ledger.json")
        hold_path = os.path.join(tmp, "watcher", "hold.json")
        for folder in (os.path.dirname(settings_path), os.path.dirname(hold_path)):
            os.makedirs(folder, mode=0o755)
        on = {"schema": vsettings.SETTINGS_SCHEMA,
              "approve": {"plans": False, "pieces": True, "merges": False},
              "limits": {"max_changed_lines": 400, "max_files": 20},
              "allow": dict((k, False) for k in vsettings.ALLOW),
              "stage_e_login": None, "merge_repos": []}

        def put_settings(doc):
            with open(settings_path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(doc))
            os.chmod(settings_path, 0o600)

        raw = {"schema": CONFIG_SCHEMA, "settings_file": settings_path, "ledger_file": ledger_path,
               "facts": dict(facts.DEFAULTS, hold_file=hold_path),
               "agent": {"agent_bin": "/usr/local/bin/claude"},
               "merge": {"review_bar": "medium"}, "check": {"app_id": 1}}
        cfg, bad = config(raw)
        check("the validator's one config, with the merge path's sections passed over unread", bad, [])
        environ = {vrun.DEFAULTS["agent_token_env"]: vrun.voice.SETUP_TOKEN_PREFIX + "01-" + "t" * 40}

        def fresh(**waiting):
            for path in (ledger_path, ledger_path + ".lock"):
                if os.path.exists(path):
                    os.unlink(path)
            host = _FakeHost(cfg["agent"], environ)
            host.waiting.update(waiting)
            return host

        def go(host, dry=False):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = run_pass(cfg, host, now=1_800_000_000, dry=dry, say=lambda line: print(line))
            return code, out.getvalue()

        def kinds():
            return [(r["ref"], r["kind"], r["reasons"]) for r in ledger.read(ledger_path)["rows"] or []]
        SAFE, FLOOR = ("src/app.py",), (".claude/hooks/pre-tool-use.py",)

        # 1. Off, and held: nothing judged, said.
        for label, doc, needle in (("no settings file", None, "OFF: no settings file"),
                                   ("the pieces switch off",
                                    dict(on, approve=dict(on["approve"], pieces=False)),
                                    "OFF: the pieces switch is off")):
            if doc is None:
                if os.path.exists(settings_path):
                    os.unlink(settings_path)
            else:
                put_settings(doc)
            host = fresh(**{"TOD-12": ("changed", SAFE)})
            code, said = go(host)
            check("off: %s judges nothing and says so" % label,
                  (code, needle in said, host.log, kinds()), (EXIT_OK, True, [], []))
        put_settings(on)
        host = fresh(**{"TOD-12": ("changed", SAFE)})
        limits.hold.write(hold_path, [("spend", "spend-day", "acme/app")], lambda: 1_800_000_000)
        code, said = go(host)
        check("a hold stands: no piece is judged",
              (code, "HELD: the watcher's hold stands" in said, host.log, kinds()), (EXIT_OK, True, [], []))
        os.unlink(hold_path)

        # 2. Two pieces wait: the first is judged by the agent and approved; one run per pass.
        host = fresh(**{"TOD-12": ("changed", SAFE), "TOD-13": ("added-after-approval", SAFE)})
        code, said = go(host)
        check("a piece that passes the limits and the agent is approved; one agent run a pass",
              (code, kinds(), host.log), (EXIT_OK, [("TOD-12", "approved", [])], ["agent"]))
        given = host.stub.calls[0]["input"] if host.stub.calls else ""
        check("...it judged the piece as written against its plan's criteria, a ticket brief",
              ("<untrusted-ticket>" in given, "The pieces work together." in given), (True, True))
        check("...and only pieces of an approved plan waiting for your start are judged",
              ("TOD-99" in said, "TOD-5:" in said), (False, False))

        # 3. The floor: a held row once, never the agent, and an unchanged hold not written again.
        host = fresh(**{"TOD-20": ("changed", FLOOR)})
        code, said = go(host)
        check("a piece on the safety-system floor is held for you, without the agent",
              (kinds(), host.log, "TOD-20: held for you: it changes the safety system" in said),
              ([("TOD-20", "held", ["risk-path"])], [], True))
        code, said = go(host)
        check("...and an unchanged hold is said, not written again",
              (kinds(), "TOD-20: still held for you (already recorded, unchanged since)" in said),
              ([("TOD-20", "held", ["risk-path"])], True))

        host.changed["TOD-20"] = time.time() + 3600         # the piece changes after its row
        code, said = go(host)
        check("...but the same hold on a piece changed since its row is written again",
              kinds(), [("TOD-20", "held", ["risk-path"]), ("TOD-20", "held", ["risk-path"])])

        # 3b. A piece the agent escalated, unchanged since, waits for you: no second run, and the
        #     next piece gets the pass's one run (Stage E on #618).
        host = fresh(**{"TOD-12": ("changed", SAFE), "TOD-13": ("changed", SAFE)})
        host.stub.text = "ESCALATE\nThe piece reaches past its plan."
        go(host)
        host.stub.text = "PASS\nIt asks for what its plan asks."
        code, said = go(host)
        check("an escalated piece, unchanged since, is not judged again, and the next one is",
              (kinds(), host.log, "TOD-12: still waits for you" in said),
              ([("TOD-12", "escalated", []), ("TOD-13", "approved", [])], ["agent", "agent"], True))

        # 3c. An edit while the agent runs: nothing recorded, so no approval of text it never saw.
        host = fresh(**{"TOD-14": ("changed", SAFE)})
        host.during = lambda: host.text.update({"TOD-14": "## Acceptance criteria\n\n- [ ] More.\n"})
        code, said = go(host)
        check("a piece edited while the agent judged it gets no row, and is said",
              (kinds(), host.log, "TOD-14: it changed while the agent judged it" in said),
              ([], ["agent"], True))

        # 4. The agent's no: a send-back has no dev session, so it is an escalation; a hold is a hold.
        host = fresh(**{"TOD-30": ("changed", SAFE)})
        host.stub.text = "SEND_BACK\nThe piece reaches past its plan."
        go(host)
        check("a send-back of a piece is an escalation to you", kinds(),
              [("TOD-30", "escalated", ["send-back-no-session"])])
        host = fresh(**{"TOD-31": ("changed", SAFE)})
        host.stub.text = "not two lines"
        go(host)
        check("an answer that cannot be used is held, never approved", kinds(),
              [("TOD-31", "held", ["answer-not-two-lines"])])

        # 5. A dry run runs no agent and writes nothing; a queue that is closed judges nothing.
        host = fresh(**{"TOD-40": ("changed", SAFE)})
        code, said = go(host, dry=True)
        check("a dry run runs no agent and writes nothing",
              (code, host.log, kinds(), "[dry-run] TOD-40 passes the hard limits" in said),
              (EXIT_OK, [], [], True))
        host = fresh(**{"TOD-41": ("changed", SAFE)})
        host.state = "closed"
        code, said = go(host)
        check("a closed window: nothing to judge, said", (code, host.log, "is closed" in said),
              (EXIT_OK, [], True))

    # 6. The config, and the refusal under an agent.
    _c, problems = config({"schema": "x", "settings_file": "rel.json", "agent": {}, "x": 1})
    check("a wrong config names every problem by key",
          [any(p.startswith(k) for p in problems) for k in (
              "'x'", "schema", "settings_file", "ledger_file", "agent.agent_bin")], [True] * 5)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(["run", "--config", "/nonexistent/config.json"], environ={"CLAUDECODE": "1"})
    check("refused in an agent's environment, before it reads anything",
          (code, out.getvalue().startswith("REFUSED")), (EXIT_USAGE, True))

    # 7. It judges and records, and starts nothing: no tracker write, no label, no start.
    with open(os.path.abspath(__file__), encoding="utf-8") as fh:
        src = fh.read()
    body = src.split("\n# SELFTEST: " + "nothing below", 1)[0].split('"""', 2)[2]
    banned = ("mutation", "save_issue", "issueUpdate", "hand_over", "delegate", "record_start",
              "addLabel", "labels", "subprocess", "os.system", "urlopen", "socket")
    check("it never starts a piece, writes to the tracker or labels", [w for w in banned if w in body], [])
    check("it is one of the agent's run's callers", os.path.basename(__file__) in vrun.CALLERS, True)
    check("its only write is the ledger's one writer",
          sorted(set(re.findall(r"\bledger\.([a-z_]+)\(", body))), ["append", "read"])

    if failures:
        for line in failures:
            print(line)
        print("validator pieces selftest: %d of %d checks failed" % (len(failures), total[0]))
        return 1
    print("OK: validator pieces selftest (%d checks)" % total[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
