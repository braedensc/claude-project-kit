#!/usr/bin/env python3
"""The validator approves a plan, in place of the owner's `approve` (KIT-544). Design: docs/adr/2026-
11-05-the-validator-approves-inside-limits-that-code-enforces.md, "What it approves".

    python3 scripts/pipeline_validator_plans.py run --config ~/.validator/config.json [--dry-run]
    python3 scripts/pipeline_validator_plans.py --selftest

WHAT IT JUDGES.  A plan that waits for the owner's `approve`: one the bridge asked him about, whose
question is still open. The candidates are read from the bridge's state, the file the queue's reader
names (`reader.bridge_state_file` in the queue's config), by the bridge's own reader
(pipeline_bridge_config.open_approvals). Nothing else: a plan he approved, declined or was never asked
about is not judged, and neither is one that has left the backlog (someone approved, started or closed
it). Not a piece starting (the piece pass), not a merge (the merge path).

ONE PASS, in order. Each step says what it did, or which nothing.
  1. The switches (pipeline_validator_settings.load): settings not read, or the plans switch off:
     nothing more, said (OFF).
  2. The watcher's hold: a hold, or a hold file not named or not readable: nothing more (HELD).
  3. The ledger, by its own reader. One that cannot be read or trusted: nothing more.
  4. The bridge's open approve questions. A state it cannot read: nothing more, said.
  5. Each plan, oldest ticket first:
       - its facts (pipeline_validator_facts.for_plan) and the hard limits. Its evidence is the
         bridge's own open question: only the owner's `approve` waits. An idea the facts cannot
         certify as one he filed is never approved: the limits hold it, and it waits for him;
       - a hold no later pass can settle (the safety-system floor, a human-only class he has not
         allowed) is a `held` row, once: the same hold on a plan unchanged since its row is not
         written again;
       - a plan the validator already approved, or its agent already judged, unchanged since its
         row, is not judged again;
       - any other wait writes nothing, and is said;
       - both passing: the ticket brief (the plan and every piece as written, against its idea's
         text: pipeline_validator_brief.build_ticket_brief, each fenced as data), ONE agent run, the
         route's decision, and its row. A send-back has no dev session yet, so it is an escalation.
         At most one agent run per pass. The plan is read again after the agent answers: one whose
         text, pieces or idea changed meanwhile gets no row, and a later pass judges it as it stands.

ITS LAST CHANGE.  The newest `updatedAt` of the plan and of its pieces, as the facts read them. A row
is stamped with the time the plan was read, before the agent ran, so a row newer than the plan's last
change is about the plan as it stands: any change made after the read makes it stale. The bridge
acts on a row by the same rule.

HOW A PLAN IS APPROVED.  Never here. The bridge approves it on its next pass
(scripts/pipeline_bridge_plans.py, validator_approvals): its own open question, an `approved` row
newer than the plan's last change, the plans switch still on, the plan's idea the owner's, and every
check his own `yes` gets. It records the validator as the one who approved, never the owner. With the
switch off, or the settings unreadable, his `approve` decides, as before.

WHAT IT NEVER DOES (asserted in --selftest): approve, edit or start a plan, file a ticket, write to the
tracker, or label. Its one write is the ledger's one writer.

EXIT CODES (contract §13):
  0  the pass ran: it judged, recorded, waited, or had nothing to do, and said which. Off and held
     are said too.
  1  a write failed: a ledger row. Said.
  2  the config is not valid, or this is an agent's environment.
  3  something could not be read: the hold, the ledger, or the bridge's questions.
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
    import pipeline_bridge_config as bridge_config
    import pipeline_queue_pool as pool
    import pipeline_queue_read as queue_read
    import pipeline_validator_brief as brief
    import pipeline_validator_facts as facts
    import pipeline_validator_ledger as ledger
    import pipeline_validator_limits as limits
    import pipeline_validator_pieces as pieces
    import pipeline_validator_route as route
    import pipeline_validator_run as vrun
    import pipeline_validator_settings as vsettings
except ImportError as exc:  # a packaging accident, never a state to run in
    sys.exit("FAIL: %s. The plan pass uses those files' own code and keeps no copy." % exc)

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_PARTIAL = 0, 1, 2, 3
# The validator's one config, read by the piece pass's own reader: the same file and sections.
config, load_config = pieces.config, pieces.load_config
KINDS = {"approved": "approved", "escalated": "escalated", "escalated-urgent": "urgently escalated",
         "held": "held"}


class Host(object):
    """The world as the plan pass meets it: the bridge's open questions, a plan's facts through the
    validator's read-only tracker key, and the agent's run. It writes nothing."""

    def __init__(self, cfg, environ=None, runner=None):
        self.cfg, self.runner = cfg, runner
        self.environ = os.environ if environ is None else environ

    def waiting(self):
        """{plan: when it was first asked}, the bridge's open approve questions, or facts.Unread."""
        rcfg, bad = queue_read.reader_config(facts._queue_raw(self.cfg["facts"]).get("reader") or {})
        if bad or not rcfg["bridge_state_file"]:
            raise facts.Unread("the queue's reader names no bridge state file "
                               "(reader.bridge_state_file), so no plan's question can be read")
        try:
            with open(rcfg["bridge_state_file"], encoding="utf-8") as fh:
                doc = json.load(fh)
            return bridge_config.open_approvals(doc)
        except (OSError, ValueError, RecursionError):
            raise facts.Unread("the bridge's state could not be read")

    def facts(self, plan, now):
        return facts.for_plan(self.cfg["facts"], plan, now)

    def run_agent(self, built):
        return vrun.run_agent(self.cfg["agent"], built, environ=self.environ, runner=self.runner)


def _latest(rows, plan):
    found = None
    for row in rows:
        if row["subject"] == "plan" and row["ref"] == plan:
            found = row
    return found


def _number(ticket):
    try:
        return (ticket.split("-")[0], int(ticket.split("-")[1]))
    except (IndexError, ValueError):
        return (ticket, 0)


def _waits(doc, words):
    """The closed words a plan waits on, with the facts' own fixed words for what was not read."""
    unread = ["%s: %s" % (k, v) for k, v in sorted((doc.get("unread") or {}).items())
              if k not in ("pr", "bounces", "diff", "acceptance", "last_piece")]
    return "; ".join(list(words) + unread)


def run_pass(cfg, host, now=None, dry=False, say=print, clock=time.time):
    """One pass. Returns its exit code (contract §13), having said what it did or which nothing."""
    now = clock() if now is None else now
    settings = vsettings.load(cfg["settings_file"])
    if settings["state"] != vsettings.READ:
        say("OFF: %s." % settings["why"])
        return EXIT_OK
    if settings["approve"]["plans"] is not True:
        say("OFF: the plans switch is off in the validator's settings.")
        return EXIT_OK
    hold = limits.hold_state(cfg["facts"]["hold_file"])
    if hold != "none":
        say("HELD: %s, so no plan is judged." % {
            "held": "the watcher's hold stands",
            None: "no hold file is named (facts.hold_file), and nothing can say there is no hold"
        }.get(hold, "the watcher's hold file could not be read (%s)" % hold))
        return EXIT_OK if hold == "held" else EXIT_PARTIAL
    got = ledger.read(cfg["ledger_file"])
    if got["rows"] is None:
        say("COULD NOT JUDGE: %s, so no plan is approved." % got["why"])
        return EXIT_PARTIAL
    rows = got["rows"]
    try:
        asked = host.waiting()
    except facts.Unread as exc:
        say("COULD NOT JUDGE: %s, so no plan is approved." % exc)
        return EXIT_PARTIAL
    if not asked:
        say("Nothing to judge: no plan waits for your approve.")
        return EXIT_OK
    say("Pass: %d plan(s) wait for your approve." % len(asked))
    failed = False
    for plan in sorted(asked, key=_number):
        read_at = clock()
        doc = host.facts(plan, now)
        if doc.get("state") not in facts.PLAN_WAITING:
            say("  %s: not judged: %s." % (plan, "its state could not be read" if doc.get("state")
                                            is None else "it has left the backlog (it is %s), so "
                                            "someone approved, started or closed it" % doc["state"]))
            continue
        l1 = limits.limits(doc["item"], settings, doc["limits"])
        l2 = {"verdict": "yes", "reasons": []}      # the bridge's own open question: only you wait
        decision = route.decide("plan", l1, l2, None, None)
        latest, last = _latest(rows, plan), doc.get("last_change")
        # A row about the plan as it stands: stamped after its last change.
        fresh = (latest is not None and last is not None
                 and (pool.epoch(latest["at"]) or 0) > last)
        if decision["record"]:
            if fresh and (latest["kind"], latest["reasons"]) == (decision["kind"], decision["reasons"]):
                say("  %s: still held for you (already recorded, unchanged since)." % plan)
                continue
            say("  %s: held for you: %s." % (plan, "; ".join(route.reason_words(decision["reasons"]))))
            failed |= not _record(cfg, plan, decision, settings, dry, say, read_at)
            continue
        if decision["wait"] != ["agent-not-run"]:
            say("  %s: waits: %s." % (plan, _waits(doc, decision["wait"])))
            continue
        if fresh and latest["kind"] == "approved":
            say("  %s: approved already, and unchanged since: the bridge approves it on its next "
                "pass." % plan)
            continue
        if fresh and latest["layers"]["agent"] != "not-run":
            say("  %s: still waits for you: the validator's agent judged it (%s), and it has not "
                "changed since." % (plan, latest["kind"]))
            continue
        try:
            built = brief.build_ticket_brief(doc["brief"].get("criteria"), doc["brief"].get("ticket"))
        except brief.BriefError as exc:
            say("  %s: waits: the agent's brief could not be built (%s)." % (plan, exc))
            continue
        if dry:
            say("  [dry-run] %s passes the hard limits: would run the agent on it." % plan)
            return EXIT_OK
        ran = host.run_agent(built)
        again = host.facts(plan, now)
        if (again.get("brief") != doc.get("brief") or again.get("last_change") != last
                or (again.get("limits") or {}).get("paths") != doc["limits"].get("paths")
                or again.get("state") != doc.get("state")):
            say("  %s: it changed while the agent judged it, so nothing is recorded: a later pass "
                "judges it as it stands. The pass ends: one agent run per pass." % plan)
            return EXIT_OK
        answer = route.answer_of(ran, built)
        decision = route.decide("plan", l1, l2, answer, None)
        if not decision["record"]:
            say("  %s: waits: %s. The pass ends: one agent run per pass."
                % (plan, "; ".join(decision["wait"])))
            return EXIT_OK
        say("  %s: the validator %s it." % (plan, KINDS.get(decision["kind"], decision["kind"])))
        failed |= not _record(cfg, plan, decision, settings, dry, say, read_at)
        say("The pass ends: one agent run per pass.")
        return EXIT_FAILED if failed else EXIT_OK
    say("Nothing approved: no plan passes yet.")
    return EXIT_FAILED if failed else EXIT_OK


def _record(cfg, plan, decision, settings, dry, say, read_at):
    """The decision's row, stamped with the time the plan was read: a change after that makes it
    stale (ITS LAST CHANGE)."""
    row = route.row_for("plan", plan, None, decision, settings)
    if row is None:
        return True
    if dry:
        say("  [dry-run] would record %s for %s" % (row["kind"], plan))
        return True
    try:
        ledger.append(cfg["ledger_file"], row, lambda: read_at)
    except (ledger.LedgerError, OSError) as exc:
        say("  %s: its %s could not be recorded (%s)" % (plan, row["kind"], exc))
        return False
    return True


def main(argv=None, environ=None):
    ap = argparse.ArgumentParser(description="The validator approves a plan, in place of the "
                                             "owner's approve.")
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run", help="one pass")
    r.add_argument("--config", default="~/.validator/config.json")
    r.add_argument("--dry-run", action="store_true",
                   help="read and judge, run no agent, and write nothing")
    args = ap.parse_args(argv)
    if args.selftest:
        from scratch_run import contained
        return contained(selftest, "validator-plans-selftest-")
    if args.cmd != "run":
        ap.print_help()
        return EXIT_USAGE
    environ = os.environ if environ is None else environ
    markers = vrun.agent_markers(environ)
    if markers:
        print("REFUSED: this is an agent's environment (%s set). Only the validator's own job "
              "judges a plan." % ", ".join(markers))
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
    """The bridge's questions and a plan's facts, stood in for. The hard limits, the route, the
    ledger, the brief and the agent's run are the real ones; the agent's CLI is the run's own Stub."""

    def __init__(self, agent_cfg, environ):
        self.agent_cfg, self.environ = agent_cfg, environ
        self.stub, self.log, self.during, self.unread = vrun.Stub(), [], None, None
        self.plans = {}              # plan -> {"paths", "origin", "state", "changed", "text", "idea"}

    def waiting(self):
        if self.unread:
            raise facts.Unread(self.unread)
        return dict((p, 1_700_000_000.0) for p in self.plans)

    def facts(self, plan, now):
        p = dict({"paths": ["src/app.py"], "origin": "owner", "state": "backlog",
                  "changed": 1_800_000_000 - 3600, "text": "Plan %s: Build it\n\nIn `src/app.py`.\n"
                  % plan, "idea": "Idea TOD-4: Build it\n\nBuild the app.\n"}, **self.plans[plan])
        return {"item": {"kind": "plan", "ref": plan, "head": None},
                "limits": {"origin": p["origin"], "risk_paths": [],
                           "paths": None if p["paths"] is None else list(p["paths"]),
                           "pr": None, "window": True, "queue": {"stops": [], "held": None},
                           "bounces": None, "watcher": "fresh", "hold": "none"},
                "evidence": {"last_piece": None, "acceptance": None},
                "brief": {"criteria": p["idea"], "diff": None, "ticket": p["text"]},
                "ticket": plan, "plan": plan, "idea": "TOD-4", "state": p["state"],
                "last_change": p["changed"],
                "unread": {} if p["origin"] else {"origin": "the idea behind %s is not certified as "
                                                            "one the owner filed" % plan}}

    def run_agent(self, built):
        self.log.append("agent")
        got = vrun.run_agent(self.agent_cfg, built, environ=self.environ, runner=self.stub)
        if self.during:
            self.during()           # someone edits the plan while the agent runs
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

    NOW = 1_800_000_000
    with tempfile.TemporaryDirectory() as tmp:
        os.chmod(tmp, 0o700)
        settings_path = os.path.join(tmp, "validator", "settings.json")
        ledger_path = os.path.join(tmp, "validator", "ledger.json")
        hold_path = os.path.join(tmp, "watcher", "hold.json")
        for folder in (os.path.dirname(settings_path), os.path.dirname(hold_path)):
            os.makedirs(folder, mode=0o755)
        on = {"schema": vsettings.SETTINGS_SCHEMA,
              "approve": {"plans": True, "pieces": False, "merges": False},
              "limits": {"max_changed_lines": 400, "max_files": 20},
              "allow": dict((k, False) for k in vsettings.ALLOW),
              "stage_e_login": None, "merge_repos": []}

        def put_settings(doc):
            with open(settings_path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(doc))
            os.chmod(settings_path, 0o600)

        raw = {"schema": pieces.CONFIG_SCHEMA, "settings_file": settings_path,
               "ledger_file": ledger_path, "facts": dict(facts.DEFAULTS, hold_file=hold_path),
               "agent": {"agent_bin": "/usr/local/bin/claude"},
               "merge": {"review_bar": "medium"}, "check": {"app_id": 1}}
        cfg, bad = config(raw)
        check("the validator's one config, read by the piece pass's own reader", bad, [])
        environ = {vrun.DEFAULTS["agent_token_env"]: vrun.voice.SETUP_TOKEN_PREFIX + "01-" + "t" * 40}
        tick = [NOW]

        def clock():
            tick[0] += 1
            return tick[0]

        def fresh(**plans):
            for path in (ledger_path, ledger_path + ".lock"):
                if os.path.exists(path):
                    os.unlink(path)
            host = _FakeHost(cfg["agent"], environ)
            for plan, over in plans.items():
                host.plans[plan] = dict(over)
            return host

        def go(host, dry=False):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = run_pass(cfg, host, now=NOW, dry=dry, say=lambda line: print(line), clock=clock)
            return code, out.getvalue()

        def kinds():
            return [(r["ref"], r["kind"], r["reasons"]) for r in ledger.read(ledger_path)["rows"] or []]
        FLOOR = [".claude/hooks/pre-tool-use.py"]

        # 1. Off, and held: nothing judged, said.
        for label, doc, needle in (("no settings file", None, "OFF: no settings file"),
                                   ("the plans switch off",
                                    dict(on, approve=dict(on["approve"], plans=False, pieces=True)),
                                    "OFF: the plans switch is off")):
            if doc is None:
                if os.path.exists(settings_path):
                    os.unlink(settings_path)
            else:
                put_settings(doc)
            host = fresh(**{"TOD-10": {}})
            code, said = go(host)
            check("off: %s judges nothing and says so" % label,
                  (code, needle in said, host.log, kinds()), (EXIT_OK, True, [], []))
        put_settings(on)
        host = fresh(**{"TOD-10": {}})
        limits.hold.write(hold_path, [("spend", "spend-day", "acme/app")], lambda: NOW)
        code, said = go(host)
        check("a hold stands: no plan is judged",
              (code, "HELD: the watcher's hold stands" in said, host.log, kinds()), (EXIT_OK, True, [], []))
        os.unlink(hold_path)
        host = fresh(**{"TOD-10": {}})
        host.unread = "the bridge's state could not be read"
        code, said = go(host)
        check("the bridge's questions not read: nothing judged, said, and the pass is partial",
              (code, "COULD NOT JUDGE: the bridge's state could not be read" in said, host.log),
              (EXIT_PARTIAL, True, []))
        code, said = go(fresh())
        check("no open question: nothing to judge, said",
              (code, "no plan waits for your approve" in said), (EXIT_OK, True))

        # 2. Two plans wait: the first is judged by the agent and approved; one run per pass.
        host = fresh(**{"TOD-10": {}, "TOD-20": {}})
        code, said = go(host)
        check("a plan that passes the limits and the agent is approved; one agent run a pass",
              (code, kinds(), host.log), (EXIT_OK, [("TOD-10", "approved", [])], ["agent"]))
        given = host.stub.calls[0]["input"] if host.stub.calls else ""
        check("...it judged the plan as written against its idea, each fenced as data",
              ("<untrusted-criteria>\nIdea TOD-4: Build it" in given,
               "<untrusted-ticket>\nPlan TOD-10: Build it" in given), (True, True))
        row = (ledger.read(ledger_path)["rows"] or [{}])[0]
        check("...its row is the validator's: subject plan, no head, its switch on, the plan's "
              "read time",
              (row.get("subject"), row.get("head"), row.get("settings_on"), row.get("at")),
              ("plan", None, ["approve-plans"], pool.iso(NOW + 1)))
        code, said = go(host)
        check("...an approved plan unchanged since waits for the bridge: no second run, and the next "
              "plan is judged",
              (kinds(), host.log, "TOD-10: approved already" in said),
              ([("TOD-10", "approved", []), ("TOD-20", "approved", [])], ["agent", "agent"], True))

        # 3. The provenance rule: an idea not certified as the owner's is never approved, whatever
        #    the switches, and no agent is run on it.
        put_settings(dict(on, allow=dict((k, True) for k in vsettings.ALLOW),
                          approve={"plans": True, "pieces": True, "merges": True}))
        host = fresh(**{"TOD-30": {"origin": None}})
        code, said = go(host)
        check("a plan from an idea not certified as yours: never approved, never run, said",
              (kinds(), host.log, "TOD-30: waits: provenance-unknown" in said,
               "not certified as one the owner filed" in said), ([], [], True, True))
        host = fresh(**{"TOD-31": {"origin": "agent-first"}})
        go(host)
        check("...and one an agent filed, certified so, is held for you", kinds(),
              [("TOD-31", "held", ["provenance-not-owner"])])
        put_settings(on)

        # 4. The floor, and a human-only class not allowed: a held row once, never the agent.
        host = fresh(**{"TOD-40": {"paths": FLOOR}})
        code, said = go(host)
        check("a plan whose pieces touch the safety system is held for you, without the agent",
              (kinds(), host.log, "TOD-40: held for you: it changes the safety system" in said),
              ([("TOD-40", "held", ["risk-path"])], [], True))
        code, said = go(host)
        check("...and an unchanged hold is said, not written again",
              (kinds(), "TOD-40: still held for you (already recorded, unchanged since)" in said),
              ([("TOD-40", "held", ["risk-path"])], True))
        host.plans["TOD-40"]["changed"] = tick[0] + 3600       # the plan changes after its row
        go(host)
        check("...but the same hold on a plan changed since its row is written again",
              kinds(), [("TOD-40", "held", ["risk-path"]), ("TOD-40", "held", ["risk-path"])])
        host = fresh(**{"TOD-41": {"paths": ["db/migrations/0002_drop.sql"]}})
        go(host)
        check("a plan that may delete data, with that class not allowed, is held for you",
              (kinds(), host.log), ([("TOD-41", "held", ["deleting-data"])], []))
        put_settings(dict(on, allow=dict(on["allow"], deleting_data=True)))
        host = fresh(**{"TOD-41": {"paths": ["db/migrations/0002_drop.sql"]}})
        go(host)
        check("...and with it allowed, the agent judges it", (kinds(), host.log),
              ([("TOD-41", "approved", [])], ["agent"]))
        put_settings(on)

        # 5. The agent's no: a send-back has no dev session, so it is an escalation; an answer
        #    that cannot be used is held; an escalated plan unchanged since is not judged again.
        host = fresh(**{"TOD-50": {}, "TOD-51": {}})
        host.stub.text = "SEND_BACK\nThe plan reaches past its idea."
        go(host)
        check("a send-back of a plan is an escalation to you", kinds(),
              [("TOD-50", "escalated", ["send-back-no-session"])])
        host.stub.text = "PASS\nIt asks for what its idea asks."
        code, said = go(host)
        check("...unchanged since, it waits for you with no second run, and the next plan is judged",
              (kinds()[1:], host.log, "TOD-50: still waits for you" in said),
              ([("TOD-51", "approved", [])], ["agent", "agent"], True))
        host = fresh(**{"TOD-52": {}})
        host.stub.text = "not two lines"
        go(host)
        check("an answer that cannot be used is held, never approved", kinds(),
              [("TOD-52", "held", ["answer-not-two-lines"])])

        # 6. An edit while the agent runs: nothing recorded, so no approval of text it never saw.
        for label, change in (("its text", {"text": "Plan TOD-60: More\n"}),
                              ("its newest change", {"changed": NOW + 99}),
                              ("its idea", {"idea": "Idea TOD-4: Something else\n"})):
            host = fresh(**{"TOD-60": {}})
            host.during = (lambda h=host, c=change: h.plans["TOD-60"].update(c))
            code, said = go(host)
            check("a plan whose %s changed while the agent judged it gets no row, and is said" % label,
                  (kinds(), host.log, "TOD-60: it changed while the agent judged it" in said),
                  ([], ["agent"], True))

        # 7. Not waiting any more, a dry run, and a wait that is not the agent's.
        host = fresh(**{"TOD-70": {"state": "unstarted"}, "TOD-71": {"state": None}})
        code, said = go(host)
        check("a plan that left the backlog, or whose state was not read, is not judged",
              (kinds(), host.log, "TOD-70: not judged: it has left the backlog" in said,
               "TOD-71: not judged: its state could not be read" in said), ([], [], True, True))
        host = fresh(**{"TOD-72": {}})
        code, said = go(host, dry=True)
        check("a dry run runs no agent and writes nothing",
              (code, host.log, kinds(), "[dry-run] TOD-72 passes the hard limits" in said),
              (EXIT_OK, [], [], True))
        host = fresh(**{"TOD-73": {"paths": None}})
        code, said = go(host)
        check("a fact not read waits, unwritten, and is said in closed words",
              (code, kinds(), host.log, "TOD-73: waits: paths-unknown" in said), (EXIT_OK, [], [], True))

    # 8. The config, and the refusal under an agent.
    _c, problems = config({"schema": "x", "settings_file": "rel.json", "agent": {}, "x": 1})
    check("a wrong config names every problem by key",
          [any(p.startswith(k) for p in problems) for k in (
              "'x'", "schema", "settings_file", "ledger_file", "agent.agent_bin")], [True] * 5)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(["run", "--config", "/nonexistent/config.json"], environ={"CLAUDECODE": "1"})
    check("refused in an agent's environment, before it reads anything",
          (code, out.getvalue().startswith("REFUSED")), (EXIT_USAGE, True))

    # 9. It judges and records, and approves nothing: no tracker write, no label, no start.
    with open(os.path.abspath(__file__), encoding="utf-8") as fh:
        src = fh.read()
    body = src.split("\n# SELFTEST: " + "nothing below", 1)[0].split('"""', 2)[2]
    banned = ("mutation", "save_issue", "issueUpdate", "hand_over", "delegate", "record_start",
              "act_approve", "approve_plan", "tracker.move", "addLabel", "labels", "subprocess",
              "os.system", "urlopen", "socket", "\"w\"", "'w'")
    check("it never approves, edits or starts a plan, writes to the tracker or labels",
          [w for w in banned if w in body], [])
    check("it is one of the agent's run's callers", os.path.basename(__file__) in vrun.CALLERS, True)
    check("its only write is the ledger's one writer",
          sorted(set(re.findall(r"\bledger\.([a-z_]+)\(", body))), ["append", "read"])

    if failures:
        for line in failures:
            print(line)
        print("validator plans selftest: %d of %d checks failed" % (len(failures), total[0]))
        return 1
    print("OK: validator plans selftest (%d checks)" % total[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
