#!/usr/bin/env python3
"""What a "no" does: the validator's decision for one item, its send-back and its escalation
(KIT-542). Design: docs/adr/2026-11-05-the-validator-approves-inside-limits-that-code-enforces.md,
"Layer 3", and the owner's decision V8: the agent chooses between sending a change back to the dev
session and escalating to the owner, and escalates urgently if it believes a dev is compromised.

    from pipeline_validator_route import decide, row_for, blocker, urgent_route
    decide(subject, layer1, layer2, answer, bounces)   THE ONE DECISION: what is recorded, if anything
    row_for(subject, ref, head, decision, settings)    the ledger row for it, for append() to write
    blocker(row)                                       the stand-up's line for an escalation or a hold
    urgent_route(status_file, now)                     whether the watcher can page an urgent one

    python3 scripts/pipeline_validator_route.py --selftest

WHAT A "NO" DOES.  The agent chooses, and code checks its choice:
  * SEND_BACK, on a pull request with send-backs left, is a `sent-back` row. Stage E's bounce driver
    (scripts/pipeline_bounce_local.py) reads that row from this ledger and re-prompts the session
    that opened the pull request, through its own ledger, so it counts against the bounce budget
    Stage E already keeps. This file keeps no count of its own.
  * Past that budget, a send-back is an escalation to the owner instead (`send-back-budget-spent`),
    and so is one whose budget could not be read (`send-back-budget-unknown`). A plan or a piece has
    no dev session yet, so a send-back there is an escalation too (`send-back-no-session`).
  * ESCALATE is an `escalated` row: a blocker for the owner, with its reason and the reply that
    clears it (blocker()).
  * ESCALATE_URGENT is an `escalated-urgent` row: the same blocker, marked urgent. The watcher reads
    the ledger and pages the owner on its own route. urgent_route() says whether it can: a watcher
    that is stale, unreadable, or not reading the ledger is said, never assumed to have paged.
  * A hold from layer 1 or 2 is a `held` row with the code's closed words in `reasons`, never the
    agent's words, and nothing goes to the dev. The agent's reason stays in `agent_says`, quoted as
    its own.

WHEN NOTHING IS RECORDED.  A head has one verdict (contract §24), so a hold that will settle by itself
writes no row: checks still running or red, a review not in yet, a fact that could not be read, the
working window closed. decide() gives `record: False` and the reasons it waits on, and the caller
says them in its own pass. Only a hold no later pass can settle at this head is recorded: one of
FINAL_HOLDS, an answer the agent could not give, or an urgent escalation.

WHAT IT NEVER DOES (asserted in --selftest): approve, merge, label, write a file, start a process or
send anything. It decides; the validator's job writes the row through the ledger's one writer.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    import pipeline_validator_brief as brief
    import pipeline_validator_evidence as evidence
    import pipeline_validator_ledger as ledger
    import pipeline_validator_limits as limits
except ImportError as exc:  # a packaging accident, never a state to run in
    sys.exit("FAIL: %s. The decision uses those files' own words and keeps no copy." % exc)

SUBJECTS = ledger.SUBJECTS
# The send-back's own reasons: why a send-back became an escalation.
ROUTE_REASONS = ("send-back-budget-spent", "send-back-budget-unknown", "send-back-no-session")
# Why nothing could be decided from what the caller gave: a layer that is not one.
INPUT_REASONS = ("limits-unreadable", "evidence-unreadable", "agent-not-run")
# The holds no later pass can settle at this head: the owner's. Every other layer-1 or layer-2
# reason waits, unrecorded, for a later pass: a head has one verdict, never spent on a wait.
FINAL_HOLDS = ("provenance-not-owner", "provenance-later-generation", "too-many-lines",
               "too-many-files", "risk-path", "credentials", "billing-and-accounts",
               "production-deploy", "deleting-data", "bounce-budget-spent", "review-edited",
               "review-partial", "review-declined", "acceptance-not-passed")
RUN_HOLD_RE = re.compile(r"run-[a-z][a-z-]{0,36}")
# Plain words for the reasons the owner most needs to read; any other closed word is shown as it is.
WORDS = {
    "send-back-budget-spent": "its send-backs are used up",
    "send-back-budget-unknown": "its send-backs could not be counted",
    "send-back-no-session": "it has no dev session to send it back to",
    "provenance-not-owner": "the idea was not yours",
    "provenance-later-generation": "it is agent-filed work past its first generation",
    "too-many-lines": "it changes too many lines",
    "too-many-files": "it changes too many files",
    "risk-path": "it changes the safety system or a risk path",
    "credentials": "it touches credentials",
    "billing-and-accounts": "it touches billing or accounts",
    "production-deploy": "it touches production deploy settings",
    "deleting-data": "it may delete data",
    "bounce-budget-spent": "Stage E's send-backs are used up",
    "review-edited": "its review was edited",
    "review-partial": "its review did not cover every file",
    "review-declined": "Stage E declined to review it",
    "acceptance-not-passed": "the acceptance check did not pass",
    "brief-over-cap": "the change is too big for the agent to read whole",
}
AGENT_SAID = "the validator's agent said"
GITHUB = "https://github.com"
# The watcher's monitor that reads this ledger: the urgent page goes through it.
WATCHER_MONITOR = "approval-rate"


def _whole(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _known(word):
    return (word in limits.REASONS or word in evidence.REASONS or word in brief.HOLDS
            or word in ROUTE_REASONS or word in INPUT_REASONS
            or (isinstance(word, str) and RUN_HOLD_RE.fullmatch(word) is not None))


def _layer(result, passing):
    """(verdict, closed reasons) for one layer's answer: `pass` only for exactly a passing answer,
    `fail` with its closed reasons for a failing one, and `unsure` for anything that is not one."""
    if not isinstance(result, dict) or not isinstance(result.get("reasons"), list):
        return "unsure", []
    words = [w for w in result["reasons"] if isinstance(w, str) and _known(w)]
    if result.get("verdict") in passing:
        return ("pass", []) if not result["reasons"] else ("unsure", words)
    if result.get("verdict") in ("fail", "no") and words and len(words) == len(result["reasons"]):
        return "fail", words
    return "unsure", words


def _unique(words):
    out = []
    for w in words:
        if w not in out:
            out.append(w)
    return out


def _says(answer):
    """The agent's own reason, when it gave one that may be kept: one plain line, never the
    stand-in for no reason."""
    reason = answer.get("reason") if isinstance(answer, dict) else None
    if (isinstance(reason, str) and reason != brief.NO_REASON and ledger.plain_line(reason)
            and (answer.get("decision") in brief.DECISIONS
                 or answer.get("hold") == "brief-over-cap")):
        return reason
    return None


def _decision(record, kind, l1, l2, agent, reasons, says, wait=()):
    return {"record": record, "kind": kind,
            "layers": {"hard_limits": l1, "evidence": l2, "agent": agent},
            "reasons": _unique(reasons), "agent_says": says if agent != "not-run" else None,
            "wait": _unique(wait)}


def decide(subject, layer1, layer2, answer, bounces=None):
    """THE ONE DECISION for one item. `layer1` is the hard limits' answer (`verdict` pass or fail,
    `reasons`); `layer2` the evidence's (`yes` or `no`, `reasons`) or the caller's own layer 2 in the
    same shape; `answer` the answer reader's (read_answer), a run that held as
    {"decision": "HOLD", "hold": <its closed word>}, or None when the agent was not run; `bounces`
    the limits' bounce_fact for a merge. Returns {"record", "kind", "layers", "reasons",
    "agent_says", "wait"}: `record` False means nothing is written and `wait` says why."""
    l1, r1 = _layer(layer1, ("pass",))
    l2, r2 = _layer(layer2, ("yes", "pass"))
    if l1 == "unsure" and not r1:
        r1 = ["limits-unreadable"]
    if l2 == "unsure" and not r2:
        r2 = ["evidence-unreadable"]
    ran = isinstance(answer, dict)
    choice = answer.get("decision") if ran else None
    if ran and choice not in brief.DECISIONS:
        hold = answer.get("hold")
        answer = {"decision": brief.HOLD, "reason": answer.get("reason"),
                  "hold": hold if isinstance(hold, str) and _known(hold) else "answer-unknown-decision"}
        choice = brief.HOLD
    kind, agent = brief.combine(l1, l2, answer if ran else None)
    if not ran:
        agent = "not-run"
    says = _says(answer) if ran else None
    code = r1 + r2
    if l1 != "pass" or l2 != "pass":
        if kind == "escalated-urgent":
            return _decision(True, kind, l1, l2, agent, code, says)
        if any(w in FINAL_HOLDS for w in code):
            return _decision(True, "held", l1, l2, agent, code, says)
        return _decision(False, None, l1, l2, agent, [], says, wait=code)
    if not ran:
        return _decision(False, None, l1, l2, agent, [], None, wait=["agent-not-run"])
    if choice == brief.HOLD:
        return _decision(True, "held", l1, l2, agent, [answer["hold"]], says)
    if choice == "SEND_BACK":
        why = None
        if subject != "merge":
            why = "send-back-no-session"
        elif (not isinstance(bounces, dict) or not _whole(bounces.get("spent"))
              or not _whole(bounces.get("budget"))
              or not isinstance(bounces.get("exhausted"), bool)):
            why = "send-back-budget-unknown"
        elif bounces["exhausted"] or bounces["spent"] >= bounces["budget"]:
            why = "send-back-budget-spent"
        if why:
            return _decision(True, "escalated", l1, l2, agent, [why], says)
    return _decision(True, kind, l1, l2, agent, [], says)


def answer_of(run, built):
    """The agent's answer from one run of it (run_agent's result: text, or a hold) and the brief it
    was given: the answer reader's, or a hold in the run's own closed word."""
    if not isinstance(run, dict) or run.get("hold") is not None or not isinstance(run.get("text"), str):
        hold = run.get("hold") if isinstance(run, dict) else None
        return {"decision": brief.HOLD, "reason": None,
                "hold": hold if isinstance(hold, str) and RUN_HOLD_RE.fullmatch(hold)
                else "run-unknown"}
    return brief.read_answer(run["text"], built)


def switches_on(settings):
    """The ledger's words for the validator's switches that are on, from the settings' load()."""
    if not isinstance(settings, dict) or settings.get("state") != "read":
        return []
    on = ["approve-%s" % k for k in ("plans", "pieces", "merges")
          if (settings.get("approve") or {}).get(k) is True]
    on += ["allow-%s" % k.replace("_", "-") for k in ("credentials", "billing_and_accounts",
                                                       "production_deploy", "deleting_data")
           if (settings.get("allow") or {}).get(k) is True]
    return on


def row_for(subject, ref, head, decision, settings):
    """The ledger row for a decision that is recorded, every field but `at`, which the writer
    stamps. None for one that is not. The ledger's own checks still judge it when it is written."""
    if not isinstance(decision, dict) or decision.get("record") is not True:
        return None
    lim = (settings or {}).get("limits") if isinstance(settings, dict) else None
    size = {"max_changed_lines": 0, "max_files": 0}
    if isinstance(settings, dict) and settings.get("state") == "read" and isinstance(lim, dict):
        size = {"max_changed_lines": lim.get("max_changed_lines", 0),
                "max_files": lim.get("max_files", 0)}
    return {"subject": subject, "ref": ref, "head": head if subject == "merge" else None,
            "kind": decision["kind"], "layers": dict(decision["layers"]),
            "settings_on": switches_on(settings), "size_limits": size,
            "reasons": list(decision["reasons"]), "agent_says": decision["agent_says"]}


def reason_words(reasons):
    """The reasons in plain words where they have them, each closed word as it is otherwise."""
    out = []
    for w in reasons or ():
        if w in WORDS:
            out.append(WORDS[w])
        elif isinstance(w, str) and w.startswith(("answer-", "brief-")):
            out.append("the agent's answer could not be used (%s)" % w)
        elif isinstance(w, str) and w.startswith("run-"):
            out.append("the agent could not answer (%s)" % w)
        elif isinstance(w, str) and ledger.REASON_RE.fullmatch(w):
            out.append(w)
    return _unique(out)


def _name(row):
    ref = row["ref"]
    if row["subject"] == "merge":
        repo, number = ref.split("#", 1)
        return "Pull request #%s in %s" % (number, repo)
    return ("Plan %s" % ref) if row["subject"] == "plan" else ref


def blocker(row):
    """The stand-up's line for one ledger row that waits on the owner: an escalation, or a hold the
    validator cannot settle. None for an approval or a send-back: those are the pipeline's own.
    {"key", "subject", "urgent", "what", "agent_says", "reply", "where"}: `reply` is the exact reply
    that clears it (`approve <plan>`, `start <piece>`), and `where` where to act when no reply can.
    `agent_says` is the agent's own line, to be quoted as its words and never as the report's."""
    if not isinstance(row, dict) or ledger.row_problems(dict(row, at=row.get("at") or
                                                             "2000-01-01T00:00:00Z")):
        return None
    kind = row["kind"]
    if kind not in ("escalated", "escalated-urgent", "held"):
        return None
    done = {"held": "was held by the validator", "escalated": "was sent to you by the validator",
            "escalated-urgent": "was sent to you by the validator, urgently"}[kind]
    words = reason_words(row["reasons"])
    what = "%s %s%s" % (_name(row), done, (": %s" % "; ".join(words)) if words else "")
    if kind == "escalated-urgent":
        what = "Urgent: " + what + ". It believes the dev session may be compromised"
    reply, where = "", ""
    if row["subject"] == "plan":
        reply = "approve %s" % row["ref"]
    elif row["subject"] == "piece":
        reply = "start %s" % row["ref"]
    else:
        repo, number = row["ref"].split("#", 1)
        where = "It's yours to merge or close: %s/%s/pull/%s" % (GITHUB, repo, number)
    return {"key": row["ref"].lower() if row["subject"] == "merge" else row["ref"],
            "subject": row["subject"], "urgent": kind == "escalated-urgent", "what": what,
            "agent_says": row["agent_says"], "reply": reply, "where": where}


def urgent_route(status_file, now, max_age=limits.DEFAULT_WATCHER_MAX_AGE):
    """{"route": "handed" | "could-not-page", "why"} for an urgent escalation. The watcher reads the
    ledger and pages on its own route, so this asks whether it is reading: its status file fresh and
    trusted, and its validator monitor on. Anything else is said, never assumed sent."""
    state = limits.watcher_state(status_file, now, max_age)
    if state is None:
        return {"route": "could-not-page", "why": "could not page: the watcher's status file could "
                                                  "not be read or trusted"}
    if state == "stale":
        return {"route": "could-not-page", "why": "could not page: the watcher has not written its "
                                                  "status file for over %d minutes" % (max_age // 60)}
    try:
        with open(status_file, encoding="utf-8") as fh:
            doc = json.loads(fh.read(limits.WATCHER_MAX_BYTES + 1))
        monitors = [m for m in doc.get("monitors") if isinstance(m, dict)]
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        return {"route": "could-not-page", "why": "could not page: the watcher's status file could "
                                                  "not be read"}
    mine = [m for m in monitors if m.get("name") == WATCHER_MONITOR]
    if len(mine) != 1 or mine[0].get("state") not in ("clear", "flagged", "blind"):
        return {"route": "could-not-page", "why": "could not page: the watcher's %s monitor is not "
                                                  "on, so nothing reads the ledger yet" % WATCHER_MONITOR}
    if mine[0]["state"] == "blind":
        return {"route": "could-not-page", "why": "could not page: the watcher's %s monitor is blind"
                                                  % WATCHER_MONITOR}
    return {"route": "handed", "why": "handed to the watcher: it reads the ledger and pages on its "
                                      "next pass"}


def main(argv=None):
    ap = argparse.ArgumentParser(description="What the validator's \"no\" does.")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        from scratch_run import contained
        return contained(selftest, "validator-route-selftest-")
    ap.print_help()
    return 2


# ======================================================================================
# SELFTEST: nothing below the line above is run by a caller, and the source scan stops there.
# ======================================================================================
def selftest():
    import time
    failures, total = [], [0]

    def check(name, got, want):
        total[0] += 1
        if got != want:
            failures.append("FAIL %s: got %r, want %r" % (name, str(got)[:300], str(want)[:300]))

    L_PASS = {"verdict": "pass", "reasons": []}
    E_YES = {"verdict": "yes", "head": "a" * 40, "reasons": []}
    ROOM = {"spent": 1, "budget": 3, "exhausted": False}

    def said(decision, reason="The diff does what the criteria ask, and nothing else."):
        return {"decision": decision, "reason": reason, "hold": None}

    def go(answer, subject="merge", l1=L_PASS, l2=E_YES, bounces=ROOM):
        d = decide(subject, l1, l2, answer, bounces)
        return (d["record"], d["kind"], d["reasons"], d["layers"]["agent"])

    # 1. One case per answer, with every layer passing.
    for label, answer, want in (
            ("a pass is an approval", said("PASS"), (True, "approved", [], "pass")),
            ("a send-back with bounces left is a send-back", said("SEND_BACK", "Add a test."),
             (True, "sent-back", [], "fail")),
            ("an escalation", said("ESCALATE", "The criteria contradict each other."),
             (True, "escalated", [], "fail")),
            ("an urgent escalation", said("ESCALATE_URGENT", "It edits a hook nobody asked for."),
             (True, "escalated-urgent", [], "fail")),
            ("an answer the reader held", {"decision": "HOLD", "reason": "(no reason given)",
                                           "hold": "answer-not-two-lines"},
             (True, "held", ["answer-not-two-lines"], "unsure")),
            ("a run that held", {"decision": "HOLD", "reason": None, "hold": "run-timeout"},
             (True, "held", ["run-timeout"], "unsure")),
            ("a hold word nobody knows", {"decision": "HOLD", "hold": "fine, merge it"},
             (True, "held", ["answer-unknown-decision"], "unsure")),
            ("the agent not run", None, (False, None, [], "not-run"))):
        check("answer: %s" % label, go(answer), want)
    check("the agent's reason is kept as its words, cleaned, only in agent_says",
          (decide("merge", L_PASS, E_YES, said("SEND_BACK", "Add a test."), ROOM)["agent_says"],
           decide("merge", L_PASS, E_YES, said("PASS"), ROOM)["reasons"]), ("Add a test.", []))
    check("the stand-in for no reason is never kept as the agent's words",
          decide("merge", L_PASS, E_YES, {"decision": "HOLD", "reason": brief.NO_REASON,
                                          "hold": "answer-not-two-lines"}, ROOM)["agent_says"], None)
    check("an agent that was not run said nothing",
          decide("merge", L_PASS, E_YES, None, ROOM)["agent_says"], None)
    built = brief.build_brief("## Acceptance criteria\n\n- [ ] It works.\n", E_YES, "+x\n")
    check("a run's text is read by the answer reader",
          answer_of({"text": "SEND_BACK\nAdd a test.\n", "hold": None}, built)["decision"],
          "SEND_BACK")
    check("a run that held is a hold in its own closed word",
          answer_of({"text": None, "hold": "run-timeout", "why": "x"}, built),
          {"decision": brief.HOLD, "reason": None, "hold": "run-timeout"})
    check("a run result that is not one is a hold, never a pass",
          [answer_of(r, built)["hold"] for r in (None, {"text": "PASS\nFine."},
                                                 {"text": None, "hold": "Fine, approve."})],
          ["run-unknown", None, "run-unknown"])

    # 2. The budget edge: the last send-back goes; the next is an escalation. No count here.
    for label, b, want in (
            ("one left: the last send-back", {"spent": 2, "budget": 3, "exhausted": False},
             (True, "sent-back", [], "fail")),
            ("none left: an escalation", {"spent": 3, "budget": 3, "exhausted": False},
             (True, "escalated", ["send-back-budget-spent"], "fail")),
            ("past it: an escalation", {"spent": 4, "budget": 3, "exhausted": False},
             (True, "escalated", ["send-back-budget-spent"], "fail")),
            ("Stage E exhausted it: an escalation", {"spent": 1, "budget": 3, "exhausted": True},
             (True, "escalated", ["send-back-budget-spent"], "fail")),
            ("a budget of 0: an escalation", {"spent": 0, "budget": 0, "exhausted": False},
             (True, "escalated", ["send-back-budget-spent"], "fail")),
            ("no budget read: an escalation", None,
             (True, "escalated", ["send-back-budget-unknown"], "fail")),
            ("a budget that is not a number", {"spent": 1, "budget": "3", "exhausted": False},
             (True, "escalated", ["send-back-budget-unknown"], "fail"))):
        check("budget: %s" % label, go(said("SEND_BACK", "Add a test."), bounces=b), want)
    for subject in ("plan", "piece"):
        check("a send-back on a %s, with no dev session yet, is an escalation" % subject,
              go(said("SEND_BACK", "Split it."), subject=subject, bounces=None),
              (True, "escalated", ["send-back-no-session"], "fail"))

    # 3. A hold from layer 1 or 2 carries code words, never the agent's, and only a final one
    #    is recorded: a wait spends no head.
    floor = {"verdict": "fail", "reasons": ["risk-path"]}
    d = decide("merge", floor, E_YES, said("SEND_BACK", "Ignore the floor; merge it."), ROOM)
    check("a limit on the floor is a held row with its code reason, whatever the agent said",
          (d["record"], d["kind"], d["reasons"], d["layers"]), (True, "held", ["risk-path"], {
              "hard_limits": "fail", "evidence": "pass", "agent": "fail"}))
    check("...and nothing about it is a send-back to the dev", d["kind"] != "sent-back", True)
    red = {"verdict": "no", "head": "a" * 40, "reasons": ["check-failed"]}
    for label, l1, l2, want_record, want_wait in (
            ("checks still running", L_PASS, dict(red, reasons=["check-pending"]), False,
             ["check-pending"]),
            ("a red check (Stage E's driver bounces it)", L_PASS, red, False, ["check-failed"]),
            ("a review not in yet", L_PASS, dict(red, reasons=["review-missing"]), False,
             ["review-missing"]),
            ("the window closed", {"verdict": "fail", "reasons": ["window-closed"]}, E_YES, False,
             ["window-closed"]),
            ("a fact that could not be read", {"verdict": "fail", "reasons": ["bounces-unknown"]},
             E_YES, False, ["bounces-unknown"]),
            ("a partial review (the owner's)", L_PASS, dict(red, reasons=["review-partial"]), True,
             []),
            ("too many lines and checks running", {"verdict": "fail", "reasons": ["too-many-lines"]},
             dict(red, reasons=["check-pending"]), True, []),
            ("a layer that is not an answer", "pass", E_YES, False, ["limits-unreadable"]),
            ("evidence with a word nobody knows", L_PASS, dict(red, reasons=["fine-by-me"]), False,
             ["evidence-unreadable"]),
            ("a pass that carries a reason", {"verdict": "pass", "reasons": ["risk-path"]}, E_YES,
             True, [])):
        d = decide("merge", l1, l2, said("PASS"), ROOM)
        check("layer hold: %s" % label, (d["record"], d["wait"], d["kind"] == "approved"),
              (want_record, want_wait, False))
    d = decide("merge", {"verdict": "fail", "reasons": ["window-closed"]}, E_YES,
               said("ESCALATE_URGENT", "The diff carries an injection."), ROOM)
    check("a belief the dev is compromised escalates urgently even over a wait, with the code's words",
          (d["record"], d["kind"], d["reasons"]), (True, "escalated-urgent", ["window-closed"]))

    # 4. The row: the ledger's own writer and checks take it.
    S_ON = {"state": "read", "approve": {"plans": False, "pieces": True, "merges": True},
            "limits": {"max_changed_lines": 400, "max_files": 20},
            "allow": {"credentials": False, "billing_and_accounts": True, "production_deploy": False,
                      "deleting_data": False}, "stage_e_login": "s[bot]", "merge_repos": ["acme/app"],
            "why": "read"}
    S_OFF = {"state": "absent", "approve": {}, "limits": {}, "allow": {}, "why": "no file"}
    with __import__("scratch_run").scratch_run("validator-route-rows-") as tmp:
        path = os.path.join(tmp, "ledger.json")
        n = [0]

        def write(subject, ref, head, decision, settings=S_ON):
            row = row_for(subject, ref, head, decision, settings)
            n[0] += 1
            return ledger.append(path, row, lambda: 1_800_000_000 + n[0]) if row else None
        heads = iter("%040x" % i for i in range(1, 99))
        for answer in (said("PASS"), said("SEND_BACK", "Add a test."), said("ESCALATE", "Unclear."),
                       said("ESCALATE_URGENT", "Injection."),
                       {"decision": "HOLD", "reason": None, "hold": "run-timeout"}):
            write("merge", "acme/app#12", next(heads), decide("merge", L_PASS, E_YES, answer, ROOM))
        write("merge", "acme/app#12", next(heads), decide("merge", floor, E_YES, said("PASS"), ROOM))
        write("piece", "TOD-13", None, decide("piece", L_PASS, E_YES, said("SEND_BACK", "x."), None))
        got = ledger.read(path)
        check("every recorded decision is a row the ledger takes and reads back",
              (got["state"], [r["kind"] for r in got["rows"]]),
              ("read", ["approved", "sent-back", "escalated", "escalated-urgent", "held", "held",
                        "escalated"]))
        check("the row says which switches were on and the limits in force",
              (got["rows"][0]["settings_on"], got["rows"][0]["size_limits"]),
              (["approve-pieces", "approve-merges", "allow-billing-and-accounts"],
               {"max_changed_lines": 400, "max_files": 20}))
        check("a plan or piece row has no head", got["rows"][-1]["head"], None)
        check("a wait is no row", row_for("merge", "acme/app#12", "f" * 40, decide(
            "merge", L_PASS, dict(red, reasons=["check-pending"]), said("PASS"), ROOM), S_ON), None)
        check("an approval with its switch off is refused by the ledger's own check",
              bool(ledger.row_problems(dict(row_for("plan", "TOD-1", None, decide(
                  "plan", L_PASS, E_YES, said("PASS"), None), S_OFF), at="2026-11-17T00:00:00Z"))),
              True)

        # 5. The blocker: its reason, the reply that clears it, urgent marked, the agent quoted.
        rows = got["rows"]
        check("an approval and a send-back are not the owner's blockers",
              (blocker(rows[0]), blocker(rows[1])), (None, None))
        b = blocker(rows[2])
        check("an escalated pull request: yours to merge or close, with the agent's line apart",
              (b["urgent"], b["reply"], b["where"], b["agent_says"], b["key"]),
              (False, "", "It's yours to merge or close: https://github.com/acme/app/pull/12",
               "Unclear.", "acme/app#12"))
        u = blocker(rows[3])
        check("an urgent one is marked urgent", (u["urgent"], u["what"].startswith("Urgent: ")),
              (True, True))
        h = blocker(rows[5])
        check("a hold says its reason in plain words, never the agent's",
              h["what"], "Pull request #12 in acme/app was held by the validator: it changes the "
                         "safety system or a risk path")
        p = blocker(rows[6])
        check("a piece's blocker is cleared by its start reply",
              (p["reply"], p["what"]), ("start TOD-13", "TOD-13 was sent to you by the validator: it "
                                                         "has no dev session to send it back to"))
        check("a plan's is cleared by its approve reply",
              blocker(dict(rows[6], subject="plan", ref="TOD-12", reasons=[]))["reply"],
              "approve TOD-12")
        check("a row that is not one gives no blocker",
              (blocker(None), blocker(dict(rows[2], kind="waved-through"))), (None, None))
        check("an agent that could not run is said as that",
              blocker(rows[4])["what"].endswith("the agent could not answer (run-timeout)"), True)

        # 6. The urgent route: said, never assumed.
        status = os.path.join(tmp, "watcher", "watcher-status.json")
        os.makedirs(os.path.dirname(status), mode=0o755)
        now = 1_800_000_000

        def watcher(state, written=now, schema="pipeline-watcher/1"):
            doc = {"schema": schema, "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                                 time.gmtime(written)),
                   "monitors": [{"name": "spend", "state": "clear"},
                                {"name": WATCHER_MONITOR, "state": state}]}
            with open(status, "w", encoding="utf-8") as fh:
                json.dump(doc, fh)
            os.chmod(status, 0o644)
            return urgent_route(status, now)["route"]
        check("a fresh watcher reading the ledger takes it", watcher("clear"), "handed")
        check("...and one already flagged", watcher("flagged"), "handed")
        check("a watcher whose monitor is off cannot page, and says so", watcher("off"),
              "could-not-page")
        check("...nor a blind one", watcher("blind"), "could-not-page")
        check("...nor a stale one", watcher("clear", written=now - 7200), "could-not-page")
        check("...nor a file of another schema", watcher("clear", schema="x/1"), "could-not-page")
        check("...nor no file at all",
              urgent_route(os.path.join(tmp, "none.json"), now)["route"], "could-not-page")
        watcher("off")
        check("the reason is said in words", urgent_route(status, now)["why"],
              "could not page: the watcher's approval-rate monitor is not on, so nothing reads the "
              "ledger yet")

    # 7. It decides, and does nothing else.
    with open(os.path.abspath(__file__), encoding="utf-8") as fh:
        src = fh.read()
    body = src.split("\n# SELFTEST: " + "nothing below", 1)[0].split('"""', 2)[2]
    banned = ("subprocess", "os.system", "urlopen", "socket", "ledger.append(", ".write(", "os.replace",
              "os.remove", "os.unlink", "label", "merge_pull", "enablePullRequestAutoMerge",
              "pipeline_validator_" + "run")
    check("it starts nothing, writes nothing, labels nothing and never names the run",
          [w for w in banned if w in body], [])
    check("every word it may record is a closed word the ledger takes",
          [w for w in FINAL_HOLDS + ROUTE_REASONS + INPUT_REASONS
           if not ledger.REASON_RE.fullmatch(w)], [])
    check("every final hold is a limit's or the evidence's own word",
          [w for w in FINAL_HOLDS if w not in limits.REASONS + evidence.REASONS], [])

    if failures:
        for line in failures:
            print(line)
        print("validator route selftest: %d of %d checks failed" % (len(failures), total[0]))
        return 1
    print("OK: validator route selftest (%d checks)" % total[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
