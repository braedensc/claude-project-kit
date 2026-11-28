#!/usr/bin/env python3
"""The Slack bridge hands a piece over again, or starts one ticket, on your yes (KIT-352).

    python3 scripts/pipeline_bridge_handover.py --selftest

A MODULE OF ITS OWN, as each of the bridge's concerns is (KIT-312). pipeline_bridge
imports its mixin, and the bridge's selftest runs its cases, end to end through one pass.
Its own selftest runs them too, and pins that it holds no HTTP call and no GraphQL write
of its own: it hands over with the tracker's one hand-over write, and says who said yes
with the one comment write.

WHY IT EXISTS. The owner's decision 10 of 2 Nov: a lost hand-over and a plan's follow-up
both sent him to Linear. `retry <ticket>` hands a ticket the agent never started to it
again, and `start <ticket>` hands one ticket to it. Each asks first, and acts on a yes.

WHAT IS HERE
  The read    handover_read: the ticket, its agent sessions and the agent's account, and
              why it can't be retried or started now, or None. The question and the yes
              judge it the same way.
  retry       a ticket the agent holds, not done, with no session running, once Linear may
              no longer deliver its newest hand-over (KIT-289's HANDOVER_RETRY_SECONDS). One
              whose newest session ran and ended may be retried at once.
  start       a ticket nobody holds, on the bridge's teams, in a backlog or to-do state, no
              plan and no planning run, that passes the readiness gate (check_ticket_dor.py,
              its default tier: the bridge has no delivery.json, so the rules that read one
              are skipped, and the question says so).
  The act     act_handover: everything read again, the one hand-over write (for a retry,
              the agent taken off and put back, since Linear starts a session only for a
              real change), the sessions read back for a retry (none new: nothing was
              retried, and it says so), then the comment naming who said yes.
  Our runner  a ticket whose project codes on our own runner (KIT-645) is never handed to the
              agent. There `start`, `retry` and `resume` (KIT-961) ask a question of their own,
              and only a yes from a member the queue's settings name as the owner
              (`owner_slack_user_ids`, judged by the pool's own slack_owner, after every check
              of the sender a yes gets) acts. The yes is recorded, with its message (its
              workspace, its channel and its ts), in the bridge's own record `slack_starts`,
              and nothing is written to Linear. A start then goes the way a move into the ready
              state goes: the queue's reader reads the record, the pool admits the ticket on it,
              and the queue's order starts it inside its window, its lanes and the day's load,
              as a code request the runner judges with that same pool. A resume is asked of
              the runner by the queue's own pass, inside its window, for the ticket's newest
              stopped run. The runner's answer, and a hold the queue puts on the ticket, are
              said where the yes was given; the record is closed by the answer, so one yes
              starts one run at most. A runner whose settings cannot be read refuses it.
"""
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import json  # noqa: E402
import check_ticket_dor as dor  # noqa: E402
import pipeline_machine_tickets as machine  # noqa: E402
import pipeline_notify_local as notify  # noqa: E402
import pipeline_queue_pool as pool  # noqa: E402  (KIT-961: the owner's one check, slack_owner)
import pipeline_queue_start as starter  # noqa: E402  (KIT-961: the queue's settings, and its ledger)
import pipeline_runner_store as runner_store  # noqa: E402  (KIT-961: the runner's runs and requests)
from pipeline_bridge_config import (ASK_AGAIN_BIND, DONE, EXIT_USAGE,  # noqa: E402
                                    REFUSED, SLACK_USER_RE, credit_terms, epoch, iso,
                                    label_names, ts_float)
from pipeline_bridge_transport import BridgeError  # noqa: E402
from pipeline_bridge_plans import (HANDOVER_RETRY_SECONDS, RUNNER_REFUSED_KEEP,  # noqa: E402
                                   SESSION_RUNNING)
from pipeline_bridge_requests import (ACT_OFF_THEN_BIND, HANDOVER_KINDS,  # noqa: E402
                                      QUESTION_BIND, ticket_fingerprint)

# THE BIND SENTENCES (KIT-342), pinned word for word by the bridge's selftest: what a yes
# does, in a question's what-a-yes-does slot, and what to type instead or later.
RETRY_THEN_BIND = "I'll hand it over to the agent again"
START_THEN_BIND = "I'll hand it to the agent to start"
RETRY_AFTER_BIND = "Ask again with `retry %s` after %s."
START_INSTEAD_BIND = "To hand it to the agent, type `start %s`."
RETRY_INSTEAD_BIND = "If it never started, type `retry %s`."
APPROVE_INSTEAD_BIND = "To start its pieces, type `approve %s`."
HEADS = {"retry": "*Hand this over again?* ", "start": "*Start this?* "}
# KIT-961, on a project that codes on our own runner: what a yes does there, what to type instead,
# the heads of its questions, and the fixed words a resume asks the run with (never a ticket's).
RUNNER_START_THEN_BIND = ("I'll ask our own runner to start it once the queue has room for it: "
                          "inside its working window, its lanes and today's limits")
RUNNER_RESUME_THEN_BIND = ("I'll ask our own runner to resume its stopped run once the queue has "
                           "room, inside its working window")
RUNNER_START_INSTEAD_BIND = "To start it, type `start %s`."
RUNNER_RESUME_INSTEAD_BIND = "To resume its stopped run, type `resume %s`."
RUNNER_HEADS = {"retry": "*Resume this on our own runner?* ",
                "start": "*Start this on our own runner?* "}
RUNNER_RESUME_TEXT = "The owner said yes in Slack to resume this run. Carry on from where it stopped."
RUN_ENDS = {"ended": "ended", "stopped": "was stopped", "interrupted": "was interrupted",
            "failed": "never got going"}
# A ticket `start` takes: one in a backlog or to-do state, which nobody began. One still in
# triage was never accepted, and done or canceled is never handed over.
START_STATES = ("backlog", "unstarted")
ENDED_STATES = ("completed", "canceled")
# The readiness gate's rules, in plain words: its own messages name labels and fields the
# bridge never says in Slack (KIT-342).
GATE_WORDS = {"input": "how it reads", "title": "its title", "sections": "its sections",
              "acceptance-criteria": "its acceptance criteria",
              "no-placeholders": "template text left in it", "labels": "its labels",
              "labels-resolve": "its labels", "project": "its project",
              "provenance": "where it came from", "state": "its state"}


def gate_ticket(issue):
    """The ticket as the readiness gate reads one (check_ticket_dor.py): its title, text,
    label names, project, parent and state."""
    return {"id": issue.get("identifier") or "", "title": issue.get("title") or "",
            "description": issue.get("description") or "",
            "labels": sorted(n for n in label_names(issue) if n),
            "project": (issue.get("project") or {}).get("id"),
            "parent": (issue.get("parent") or {}).get("identifier") or "",
            "stateId": (issue.get("state") or {}).get("id")}


def gate_failures(issue):
    """(the rules it fails, in plain words, in the gate's order; the rules it skipped) by
    the readiness gate's default tier. The bridge has no delivery.json, so the rules that
    read one are skipped, and named; warnings never fail a ticket."""
    root = os.path.dirname(HERE)
    skipped = set()
    report = dor.check_ticket(gate_ticket(issue), 0, dor.template_sections(root), None,
                              False, root, skipped)
    failed = []
    for e in report.errors:
        words = GATE_WORDS.get(e.get("rule"), "its %s" % e.get("rule"))
        if words not in failed:
            failed.append(words)
    return failed, sorted(set(skipped))


def _in_words(items):
    return items[0] if len(items) == 1 else "%s and %s" % (", ".join(items[:-1]), items[-1])


def ticket_words(issue, requester, by_bot):
    """(title, creator, state, team, who asked, the body's first sentence) as a handover question
    says them, on either road."""
    title = notify.safe_title(issue.get("title") or "")
    creator = notify.safe_title(((issue.get("creator") or {}).get("displayName")
                                 or (issue.get("creator") or {}).get("name") or "someone"))
    state = notify.safe_title((issue.get("state") or {}).get("name") or "an unknown state")
    team = ((issue.get("team") or {}).get("key")) or "?"
    if by_bot:
        who = "a bot"            # the tech lead's own lines name it in their terms (KIT-492)
    else:
        who = ("<@%s>" % requester) if SLACK_USER_RE.match(requester or "") else "a bot"
    return title, creator, state, team, who, ("%s “%s”: in %s on %s, filed by %s."
                                              % (issue.get("identifier"), title, state, team,
                                                 creator))


def handover_parts(kind, issue, since, requester, cfg, by_bot=False, dm=False, terms=None):
    """(head, body, bind, facts) for a retry or start question (KIT-352), as question_parts
    makes a plan question's. `since` is when a retried ticket's newest hand-over was made,
    or None."""
    title, creator, state, team, who, body = ticket_words(issue, requester, by_bot)
    then = RETRY_THEN_BIND if kind == "retry" else START_THEN_BIND
    if not cfg["act"]:
        then = ACT_OFF_THEN_BIND
    if kind == "retry":
        body += (" It was handed to the agent at %s and hasn't started." % iso(since)
                 if since is not None else " Its dev stopped before it finished.")
    else:
        body += (" It passes the readiness gate; the rules that need the project's own "
                 "settings were skipped.")
    yes, credit = credit_terms(terms, who)
    bind = QUESTION_BIND % (yes, "here" if dm else "in this thread", cfg["question_ttl_hours"],
                            then, credit)
    facts = {"ticket": issue.get("identifier"), "title": title, "state": state, "team": team,
             "filed_by": creator, "needs": "his answer to the fixed line after this message"}
    return HEADS[kind], body, bind, facts


def runner_parts(kind, issue, run, requester, cfg, by_bot=False, dm=False, terms=None):
    """(head, body, bind, facts) for a start or a resume on our own runner (KIT-961). `run` is the
    ticket's newest code run for a resume, as the runner's own record holds it; None for a start."""
    title, creator, state, team, who, body = ticket_words(issue, requester, by_bot)
    if kind == "retry":
        body += " Its newest run on our own runner %s at %s." % (
            RUN_ENDS.get(run.get("state"), "ended"), run.get("ended_at") or "a time it did not record")
        then = RUNNER_RESUME_THEN_BIND
    else:
        body += (" It passes the readiness gate; the rules that need the project's own settings "
                 "were skipped.")
        then = RUNNER_START_THEN_BIND
    body += " Its project codes on our own runner, so only the owner's yes counts here."
    if not cfg["act"]:
        then = ACT_OFF_THEN_BIND
    yes, credit = credit_terms(terms, who)
    bind = QUESTION_BIND % (yes, "here" if dm else "in this thread", cfg["question_ttl_hours"],
                            then, credit)
    facts = {"ticket": issue.get("identifier"), "title": title, "state": state, "team": team,
             "filed_by": creator, "on": "our own runner",
             "needs": "the owner's answer to the fixed line after this message"}
    return RUNNER_HEADS[kind], body, bind, facts


class HandoverMixin(object):
    """Pass's `retry` and `start` (KIT-352)."""

    def handover_read(self, kind, ident):
        """(issue, sessions, agent id, refusal, road) for a retry or start of the ticket now: the
        refusal as handover_refusal gives it, or None when it may go ahead. KIT-961: with our own
        runner wired, the ticket is read first, and on a project that codes on it no agent is
        asked for: (issue, [], None, runner_refusal's refusal, road), `road` being (store,
        project) as it was read this once, and handed on so no later read of it can differ. On the
        old road `road` is None."""
        issue = None
        if (self.cfg.get("runner_lane") or {}).get("repos"):
            issue = self.tracker.issue(ident)
            if not issue or not issue.get("id"):
                return None, [], None, ("I can't find %s in Linear." % ident, "", "not found"), None
            road = self.runner_road(issue)
            if road:
                return issue, [], None, self.runner_refusal(kind, issue, road), road
        name = self.cfg["agent_user_name"].strip()
        if not name:
            return None, [], None, (
                "I can't hand anything to the agent: no agent account is named here.", "",
                "no agent named"), None
        agent_id, why = self.tracker.agent_user(name)
        if not agent_id:
            return None, [], None, ("I can't hand anything to the agent: %s." % why, "",
                                    "no agent"), None
        issue = issue or self.tracker.issue(ident)
        if not issue or not issue.get("id"):
            return None, [], agent_id, ("I can't find %s in Linear." % ident, "", "not found"), None
        node = self.tracker.epic(ident) or {}
        sessions = [n or {} for n in self.tracker.sessions_on(issue["id"])]
        return issue, sessions, agent_id, self.handover_refusal(kind, issue, node, sessions,
                                                                agent_id), None

    def handover_refusal(self, kind, issue, node, sessions, agent_id):
        """Why the ticket can't be retried or started now, as (text, bind, why), or None. A
        ticket with a session running is never handed over again: that would start a second
        one on it (KIT-352)."""
        ident = issue["identifier"]
        # KIT-528: the watcher's hold stops every start, a retry and an approved hand-over
        # included, in both the question and the yes. Read first: nothing else matters while held.
        blocked, why, clear = self.hold_gate()
        if blocked:
            return ("I won't hand %s to the agent: %s." % (ident, why), clear, "held by the watcher")
        team = (issue.get("team") or {}).get("key")
        if team not in self.cfg["team_keys"]:
            return ("%s is on %s, and I hand work over only on %s."
                    % (ident, team or "no team", ", ".join(self.cfg["team_keys"])), "", "team")
        state = issue.get("state") or {}
        if state.get("type") in ENDED_STATES:
            return ("%s is %s, so there's nothing to hand over."
                    % (ident, notify.safe_title(state.get("name") or "closed")), "", "closed")
        # KIT-645: a project switched to our own runner codes only through it, and the runner starts
        # a ticket only on its approver's move. A hand-over there would start the old dispatcher.
        route, held_why = self.runner_route_of(issue)
        if route == "runner":
            return ("%s's project codes on our own runner now. It starts a ticket only when its "
                    "approver moves it into %s, so I won't hand it to the agent. Move it there "
                    "yourself, and the queue asks the runner." % (ident, self.cfg["ready_state"]),
                    "", "runner lane")
        if route == "held":
            return ("I can't tell whether %s's project codes on our own runner (%s), so I won't "
                    "hand it over." % (ident, held_why), "", "runner lane")
        statuses = [n.get("status") for n in sessions]
        if any(s in SESSION_RUNNING for s in statuses):
            return ("A dev is working on %s now, so I won't hand it over again." % ident, "",
                    "running")
        holder = (node.get("delegate") or {}).get("id")
        if kind == "start":
            if holder:
                return ("%s is already handed to %s." % (
                    ident, "the agent" if holder == agent_id else "someone else"),
                    RETRY_INSTEAD_BIND % ident if holder == agent_id else "", "held")
            if sessions:
                return ("%s was handed to the agent before." % ident, RETRY_INSTEAD_BIND % ident,
                        "handed before")
            if state.get("type") not in START_STATES:
                return ("%s is in %s, so I won't start it."
                        % (ident, notify.safe_title(state.get("name") or "?")), "", "state")
            if ((node.get("children") or {}).get("nodes")):
                return ("%s is a plan." % ident, APPROVE_INSTEAD_BIND % ident, "a plan")
            if machine.is_planning_ticket(issue.get("description"), label_names(issue)):
                return ("%s is a planning run, which the planner starts itself." % ident, "",
                        "planning run")
            failed, _skipped = gate_failures(issue)
            if failed:
                return ("%s isn't ready to start: it fails the readiness gate on %s."
                        % (ident, _in_words(failed)), "", "not ready")
            return None
        if holder != agent_id:
            return ("%s isn't handed to the agent, so there's nothing to retry." % ident,
                    START_INSTEAD_BIND % ident if not holder else "", "not the agent's")
        if self.newest_ended(sessions):
            return None                         # it ran and ended: nothing is pending
        since = self.handed_since(sessions)
        if since is None:
            return ("I can't tell when %s was handed over, so I won't hand it over again. "
                    "Check it in Linear." % ident, "", "hand-over time unknown")
        safe = since + HANDOVER_RETRY_SECONDS
        if self.now < safe:
            return ("Linear may still deliver %s's hand-over from %s until %s, so a second "
                    "one now could start two devs on it." % (ident, iso(since), iso(safe)),
                    RETRY_AFTER_BIND % (ident, iso(safe)), "Linear may still deliver it")
        return None

    # -- our own runner (KIT-961) --------------------------------------------------------
    def runner_road(self, issue):
        """(store, project) when this ticket's project codes on our own runner now, else None:
        the runner's own switch for the project (runner_route_of), read afresh each time."""
        route, where = self.runner_route_of(issue)
        return where if route == "runner" else None

    def runner_owners(self):
        """(the owner's Slack ids, why): the queue's own `owner_slack_user_ids`, the one list the
        queue's judge and our own runner read too. ([], why) when it names none, or the queue's
        settings cannot be read: then no yes here starts work on our own runner."""
        try:
            with open(self.cfg["queue_config"], encoding="utf-8") as fh:
                qcfg, bad = starter.load(json.load(fh))
        except (OSError, ValueError, RecursionError) as exc:
            return [], "the queue's settings could not be read (%s)" % type(exc).__name__
        if bad:
            return [], "the queue's settings are not usable"
        owners = list(qcfg["pool"]["owner_slack_user_ids"])
        return owners, "" if owners else "the queue's settings name no Slack member as the owner"

    def runner_runs(self, where, ident):
        """(this ticket's runs on our own runner, why): every record the runner keeps of them, or
        (None, why) when the records cannot all be read, so a live run is never missed."""
        got = runner_store.read_runs(where)
        if got["state"] != "read":
            return None, self.clean(got["why"] or "they could not be read")
        return [r for r in got["runs"] if r["ticket"] == ident], ""

    def newest_code_run(self, runs):
        """The ticket's newest code run on our own runner, or None."""
        coded = [r for r in runs or () if r["kind"] == "code"]
        return max(coded, key=lambda r: (r["started_at"] or "", r["run"])) if coded else None

    def open_slack_start(self, issue_id):
        """The bridge's record of a yes given in Slack for this ticket that our own runner has not
        answered yet, or None (KIT-961)."""
        rec = (self.state.get("slack_starts") or {}).get(issue_id)
        return rec if isinstance(rec, dict) and rec.get("answered") is None else None

    def runner_refusal(self, kind, issue, road):
        """Why this ticket can't be started or resumed on our own runner now (KIT-961), as (text,
        bind, why), or None. The watcher's hold first, as on the old road; then the owner the queue
        names, the runner's own record of the ticket's runs, what already waits for it, and for a
        start the old road's checks of the ticket itself. `road` is (store, project) as
        handover_read read it."""
        ident = issue["identifier"]
        blocked, why, clear = self.hold_gate()
        if blocked:
            return ("I won't ask our own runner to %s %s: %s."
                    % ("start" if kind == "start" else "resume", ident, why), clear,
                    "held by the watcher")
        team = (issue.get("team") or {}).get("key")
        if team not in self.cfg["team_keys"]:
            return ("%s is on %s, and I start work only on %s."
                    % (ident, team or "no team", ", ".join(self.cfg["team_keys"])), "", "team")
        state = issue.get("state") or {}
        if state.get("type") in ENDED_STATES:
            return ("%s is %s, so there's nothing to start."
                    % (ident, notify.safe_title(state.get("name") or "closed")), "", "closed")
        owners, why = self.runner_owners()
        if not owners:
            return ("I can't take a yes to start %s on our own runner: %s." % (ident, why), "",
                    "no owner named")
        runs, why = self.runner_runs(road[0], ident)
        if runs is None:
            return ("I can't read our own runner's records of its runs (%s), so I won't ask it "
                    "about %s." % (why, ident), "", "runs unread")
        if any(r["state"] in runner_store.LIVE for r in runs):
            return ("A run is at work on %s on our own runner now, so I won't ask for another."
                    % ident, "", "running")
        if ident in (self.state.get("runner_asks") or {}):
            return ("%s already waits for our own runner's answer." % ident, "", "asked")
        if self.open_slack_start(issue["id"]):
            return ("Your yes for %s already waits for the queue to have room." % ident, "",
                    "approved already")
        newest = self.newest_code_run(runs)
        if kind != "start":
            if newest is None:
                return ("%s has no stopped run on our own runner to resume." % ident,
                        RUNNER_START_INSTEAD_BIND % ident, "no run")
            return None
        if newest is not None:
            return ("%s ran on our own runner before." % ident, RUNNER_RESUME_INSTEAD_BIND % ident,
                    "ran before")
        if state.get("type") not in START_STATES:
            return ("%s is in %s, so I won't start it."
                    % (ident, notify.safe_title(state.get("name") or "?")), "", "state")
        node = self.tracker.epic(ident) or {}
        if (node.get("delegate") or {}).get("id"):
            return ("%s is handed to someone already, so I won't ask our own runner to start it."
                    % ident, "", "held")
        if (node.get("children") or {}).get("nodes"):
            return ("%s is a plan." % ident, APPROVE_INSTEAD_BIND % ident, "a plan")
        if machine.is_planning_ticket(issue.get("description"), label_names(issue)):
            return ("%s is a planning run, which the planner starts itself." % ident, "",
                    "planning run")
        failed, _skipped = gate_failures(issue)
        if failed:
            return ("%s isn't ready to start: it fails the readiness gate on %s."
                    % (ident, _in_words(failed)), "", "not ready")
        return None

    def act_on_runner(self, channel, thread, q, msg):
        """The yes to a start or a resume on our own runner (KIT-961). Only a member the queue's
        settings name as the owner may give it: anyone else's answer is refused in words, and the
        question stays open for him. Everything is read again, as the question judged it and bound
        to what it showed. Then the yes is recorded with its message, for the queue to carry, and
        said. Nothing is written to Linear here: the queue starts it, and our own runner judges it."""
        kind, ident = q["kind"], q["ticket"]
        owners, _why = self.runner_owners()
        if not pool.slack_owner({"owner_slack_user_ids": owners}, msg.get("user")):
            self.post_refusal(channel, thread, "Only the owner's yes starts work on our own runner, "
                              "so I can't take that as an answer to the question about %s."
                              % ident, "not the owner", "answer-refused", ident)
            self.mark(channel, msg, "refused: not the owner")
            self.counts["refused"] += 1
            self.say("  refused an answer on %s: not the owner" % ident)
            return

        def finish(status, text, what, kind_="runner-refused", bind=""):
            # Posted first: the status changes only after the reply saying so lands.
            self.post(channel, thread, text, kind=kind_, bind=bind, join=" ",
                      facts={"ticket": ident})
            q.update(status=status, answered_by=msg["user"], answered_ts=msg.get("ts"))
            self.mark(channel, msg, what)
        if self.refused_over_limit(channel, thread, msg):
            return
        issue, _sessions, _agent, refusal, road = self.handover_read(kind, ident)
        if issue is not None and (not road or road[1] != q["runner"]["project"]):
            refusal = ("%s's project doesn't code on our own runner as it did when I asked, so "
                       "nothing was asked of it, and I have closed the question." % ident,
                       ASK_AGAIN_BIND % (kind, ident), "changed since asked")
        elif issue is not None and refusal is None:
            runs, _why = self.runner_runs(road[0], ident)
            if ticket_fingerprint(issue) != q.get("fingerprint"):
                refusal = ("%s changed since I asked (its title, text, team, labels or state), so "
                           "nothing was asked of our own runner, and I have closed the question."
                           % ident, ASK_AGAIN_BIND % (kind, ident), "changed since asked")
            elif kind == "retry" and (self.newest_code_run(runs) or {}).get("run") != q[
                    "runner"]["run"]:
                refusal = ("%s's runs on our own runner changed since I asked, so nothing was "
                           "asked of it, and I have closed the question." % ident,
                           ASK_AGAIN_BIND % (kind, ident), "changed since asked")
        if refusal:
            text, bind, why = refusal
            finish(REFUSED, "%s Nothing was asked of our own runner." % text if why != "changed "
                   "since asked" else text, "refused: %s" % why, bind=bind)
            return
        if self.dry:
            self.say("  [dry-run] would record the yes to %s %s on our own runner"
                     % ("start" if kind == "start" else "resume", ident))
            return
        ts = msg.get("ts")
        # Recorded before it is said: a reply that fails leaves the yes standing, and the next
        # pass says it waits already, never that nothing was done.
        self.state.setdefault("slack_starts", {})[issue["id"]] = {
            "ticket": ident, "kind": "start" if kind == "start" else "resume",
            "run": q["runner"]["run"], "project": road[1], "approved_by": msg["user"],
            "approved_at": iso(ts_float(ts)), "name": self.name_of(msg["user"]),
            "team": self.me["team_id"], "channel": channel, "thread": thread, "ts": ts,
            "answered": None, "said": None}
        self.save()
        if kind == "start":
            text = ("Approved by <@%s>. %s starts on our own runner once the queue has room for it: "
                    "inside its working window, its lanes and today's limits, as a move into %s "
                    "would. You'll hear here when it starts, or why it can't."
                    % (msg["user"], ident, self.cfg["ready_state"]))
        else:
            text = ("Approved by <@%s>. Our own runner resumes %s's stopped run once the queue has "
                    "room, inside its working window. You'll hear here when it does, or why it "
                    "can't." % (msg["user"], ident))
        finish(DONE, text, "done: approved %s on our own runner" % ident, kind_="runner-approved")
        self.say("  approved: %s %s on our own runner for %s"
                 % ("start" if kind == "start" else "resume", ident, msg["user"]))

    # -- the yes, carried by the queue (KIT-961) ------------------------------------------
    def slack_said(self, entry):
        """(the comment, the approval's time) for a start the queue's order made on the owner's
        yes in Slack (KIT-961): its entry names him at his message's second. None for any other."""
        rec = self.open_slack_start(entry.get("id"))
        if not rec or rec.get("kind") != "start" or (entry.get("by"), entry.get("at")) != (
                rec.get("approved_by"), rec.get("approved_at")):
            return None
        return ("Started by our own runner for **%s** (Slack user %s), who said yes to its start "
                "in Slack at %s. The queue started it in lane %s."
                % (rec.get("name") or rec["approved_by"], rec["approved_by"], rec["approved_at"],
                   entry.get("lane") or "?"), rec["approved_at"])

    def slack_answered(self, ident, got):
        """Our own runner's answer to a start or a resume the owner said yes to in Slack, said where
        he said it, and the record closed, so one yes never asks twice (KIT-961)."""
        for _issue_id, rec in sorted((self.state.get("slack_starts") or {}).items()):
            if rec.get("ticket") != ident or rec.get("answered") is not None:
                continue
            start = rec.get("kind") == "start"
            if got.get("ok"):
                text = ("Our own runner started %s on your yes." % ident if start else
                        "Our own runner resumed %s's run on your yes." % ident)
            elif got.get("mine"):
                text = ("I couldn't ask our own runner to %s %s: %s. Nothing was started."
                        % ("start" if start else "resume", ident,
                           self.clean(str(got.get("why") or "no reason"))[:300]))
            else:
                text = ("Our own runner refused to %s %s: %s. Nothing was started."
                        % ("start" if start else "resume", ident,
                           self.clean(str(got.get("why") or "it gave no reason"))[:300]))
            self.post(rec["channel"], rec.get("thread"), text, kind="runner-answer",
                      facts={"ticket": ident})
            rec["answered"] = {"ok": bool(got.get("ok")), "run": got.get("run"),
                               "at": iso(self.now)}
        self.save()

    def slack_holds(self, verdicts):
        """A start the owner said yes to that the queue's judge holds is said where he said yes, once
        for each reason: never left to wait in silence (KIT-961). One the judge admitted waits for a
        lane and the day's room like any start, and is not said. A decision that carries no
        verdicts judged nothing, and says nothing."""
        if verdicts is None:
            return
        judged = dict((v.get("ticket"), v) for v in verdicts if isinstance(v, dict))
        asked = self.state.get("runner_asks") or {}
        for _issue_id, rec in sorted((self.state.get("slack_starts") or {}).items()):
            # One already asked of our own runner waits for its answer: it may be at work now, and
            # out of the queue's states, which is no hold.
            if rec.get("kind") != "start" or rec.get("answered") is not None or rec.get(
                    "ticket") in asked:
                continue
            v = judged.get(rec.get("ticket"))
            why = None if v and v.get("admitted") else ((v or {}).get("reason") or "not read")
            if why is None or rec.get("said") == why:
                continue
            if why == "not read":
                text = "%s can't start on our own runner yet: the queue did not read it." % rec["ticket"]
            else:
                text = ("%s can't start on our own runner yet: the queue holds it (%s)."
                        % (rec["ticket"], why))
            self.post(rec["channel"], rec.get("thread"), text + " Your yes stands until our own "
                      "runner answers it.", kind="runner-held", facts={"ticket": rec["ticket"]})
            rec["said"] = why
        self.save()

    def slack_resumes(self, ledger):
        """Each resume the owner said yes to in Slack, asked of our own runner by the queue's own
        pass (KIT-961): inside the working window, with room, no hold and no runaway stop, as the
        queue's resumes are, its ledger row first. How many were asked, or None when this pass's
        starts end here, said."""
        asked = 0
        for issue_id, rec in sorted((self.state.get("slack_starts") or {}).items()):
            if rec.get("kind") != "resume" or rec.get("answered") is not None:
                continue
            ident = rec["ticket"]
            if ident in (self.state.get("runner_asks") or {}):
                continue
            if self.actions_left() <= 0:
                self.say("  action limit: resuming %s waits for the next hour" % ident)
                break
            lane, where, why = runner_store.caller_lane(
                (self.cfg.get("runner_lane") or {}).get("settings_file"), rec["project"], "code")
            if lane == "held":
                self.queue_failed("%s's resume cannot be asked: %s" % (ident, self.clean(why)))
                return None
            if lane != "on":
                self.slack_answered(ident, {"ok": False, "mine": True,
                                            "why": "its project's coding lane is off now"})
                continue
            if self.dry:
                self.say("  [dry-run] the queue would ask our runner to resume %s" % ident)
                continue
            try:
                starter.record_start(ledger, self.now, ident, "resume")
            except starter.QueueBusy as exc:       # KIT-681: another pass writes the ledger
                self.say("  QUEUE: %s; nothing resumes this pass" % exc)
                return None
            except (OSError, ValueError) as exc:
                self.queue_failed("its ledger refused a resume (%s)" % (
                    exc if isinstance(exc, ValueError) else type(exc).__name__))
                return None
            try:
                rid = runner_store.ask(where, "queue", "resume", "code", rec["project"], ident,
                                       run=rec["run"], text=RUNNER_RESUME_TEXT, now=self.now)
            except (ValueError, OSError) as exc:
                self.slack_answered(ident, {"ok": False, "mine": True,
                                            "why": "our own runner's store would not take it (%s)"
                                            % (self.clean(str(exc)) if isinstance(exc, ValueError)
                                               else type(exc).__name__)})
                continue
            self.state.setdefault("runner_asks", {})[ident] = {
                "request": rid, "store": where, "project": rec["project"], "issue_id": issue_id,
                "body": ("Resumed by our own runner for **%s** (Slack user %s), who said yes in "
                         "Slack at %s." % (rec.get("name") or rec["approved_by"],
                                           rec["approved_by"], rec["approved_at"])),
                "approved_at": rec["approved_at"], "at": self.now}
            self.state["acted_at"].append(self.now)
            self.save()
            asked += 1
            self.say("  acted: the queue asked our runner to resume %s (the owner's yes)" % ident)
        return asked

    def slack_prune(self):
        """Answered records of a yes in Slack are kept as long as a refusal is, then dropped."""
        starts = self.state.get("slack_starts") or {}
        for issue_id in [k for k, rec in starts.items() if rec.get("answered") and self.now - (
                epoch(rec["answered"].get("at")) or 0) > RUNNER_REFUSED_KEEP]:
            del starts[issue_id]

    def handed_since(self, sessions):
        """When the newest hand-over of a ticket the agent never started was made: its newest
        session's own time, as the tracker writes it, or None when it can't be told."""
        if not sessions or not self.tracker.sessions_read_whole():
            return None
        text = (self.newest(sessions) or {}).get("createdAt") or ""
        if not text.endswith("Z"):
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None

    def request_handover(self, channel, msg, thread, kind, ident, requester, by_bot, dm,
                         terms=None, set_up=None):
        """`retry <ticket>` or `start <ticket>` (KIT-352): every check, then the question. On a
        project that codes on our own runner (KIT-961), `resume <ticket>` comes here as a retry."""
        issue, sessions, _agent, refusal, road = self.handover_read(kind, ident)
        if refusal:
            text, bind, why = refusal
            self.refuse_request(channel, msg, thread, text, why, bind=bind, ticket=ident)
            return
        if road:
            runs, why = self.runner_runs(road[0], ident)
            run = self.newest_code_run(runs) if kind == "retry" else None
            if runs is None or (kind == "retry" and run is None):
                # Read once more for the question: a read that fails now is said, never guessed.
                self.refuse_request(channel, msg, thread, "I can't read our own runner's records of "
                                    "its runs (%s), so I won't ask it about %s." % (
                                        why or "its newest run is not there now", ident),
                                    "runs unread", ticket=ident)
                return
            self.ask(channel, msg, thread, kind, issue, requester,
                     runner_parts(kind, issue, run, requester, self.cfg, by_bot=by_bot, dm=dm,
                                  terms=terms),
                     extra={"runner": {"project": road[1], "run": run["run"] if run else None}},
                     terms=terms, set_up=set_up)
            return
        since = self.handed_since(sessions) if kind == "retry" and not self.newest_ended(
            sessions) else None
        self.ask(channel, msg, thread, kind, issue, requester,
                 handover_parts(kind, issue, since, requester, self.cfg, by_bot=by_bot, dm=dm,
                                terms=terms), terms=terms, set_up=set_up)

    def act_handover(self, channel, thread, q, msg):
        """The yes to a retry or start: everything read again, as the question judged it and
        bound to what it showed, then the one hand-over write, and the comment naming who
        said yes. A retry takes the agent off and puts it back, then reads its sessions back:
        if Linear made no new one, nothing was retried, and it says so."""
        kind, ident = q["kind"], q["ticket"]
        if q.get("runner"):
            self.act_on_runner(channel, thread, q, msg)     # KIT-961: our own runner's road
            return

        def finish(status, text, what, kind_="handover-refused", bind=""):
            # Posted first: the status changes only after the reply saying so lands.
            self.post(channel, thread, text, kind=kind_, bind=bind, join=" ",
                      facts={"ticket": ident})
            q.update(status=status, answered_by=msg["user"], answered_ts=msg.get("ts"))
            self.mark(channel, msg, what)
        if self.refused_over_limit(channel, thread, msg):
            return
        issue, sessions, agent_id, refusal, _road = self.handover_read(kind, ident)
        if refusal is None and agent_id is None:
            # KIT-961: asked on the old road, and its project codes on our own runner now.
            refusal = ("%s's project codes on our own runner now, so nothing was handed over, and "
                       "I have closed the question." % ident, ASK_AGAIN_BIND % (kind, ident),
                       "changed since asked")
        if issue is not None and ticket_fingerprint(issue) != q.get("fingerprint"):
            refusal = ("%s changed since I asked (its title, text, team, labels or state), so "
                       "nothing was handed over, and I have closed the question." % ident,
                       ASK_AGAIN_BIND % (kind, ident), "changed since asked")
        if refusal:
            text, bind, why = refusal
            finish(REFUSED, "%s Nothing was handed over." % text if why != "changed since "
                   "asked" else text, "refused: %s" % why, bind=bind)
            return
        if self.dry:
            self.say("  [dry-run] would hand %s to the agent%s"
                     % (ident, " again" if kind == "retry" else ""))
            return
        before = set(n.get("id") for n in sessions)
        if kind == "retry":
            # Linear starts a new session only for a real change of delegate, so a retry takes
            # the agent off the ticket, then puts it back, as a person would (Stage E's review
            # of #325; docs/STAGE-E-OPERATOR.md: remove the delegation and delegate the ticket
            # again). Both are the one hand-over write (KIT-454: delegate_to, shared with every
            # start); together they count as the one action.
            self.delegate_to(issue["id"], None)
        try:
            self.delegate_to(issue["id"], agent_id, count=kind != "retry")
        except BridgeError as exc:
            if kind != "retry":
                raise
            # Never left silent: nobody holds it now, and that is said where it was asked.
            finish(REFUSED, "I took the agent off %s to hand it over again, but couldn't put it "
                   "back on, so nobody holds it now. Hand it to the agent in Linear." % ident,
                   "refused: taken off, not put back", kind_="handover-half-done")
            self.say("  retry: %s was taken off the agent and not put back: %s" % (ident, exc))
            return
        if kind == "retry":
            self.tracker.forget_sessions()
            made = [n for n in self.tracker.sessions_on(issue["id"])
                    if (n or {}).get("id") not in before]
            if not made:
                finish(REFUSED, "I took the agent off %s and put it back on, but Linear made no "
                       "new hand-over for it, so nothing was retried. Check it in Linear."
                       % ident, "refused: no new hand-over", kind_="handover-not-made")
                self.say("  retry: Linear made no new hand-over for %s" % ident)
                return
        name = self.name_of(msg["user"])
        body = ("Handed to the agent%s by the tech lead for **%s** (Slack user %s), who "
                "said yes in Slack at %s." % (" again" if kind == "retry" else "", name,
                                              msg["user"], iso(ts_float(msg.get("ts")))))
        # The hand-over stands: it cannot be taken back. The record of who said yes is owed.
        owed = self.attribute(issue["id"], ident, body, done="handed over")
        finish(DONE, "Handed %s to the agent%s for <@%s>. Its dev should start within a few "
               "minutes.%s" % (ident, " again" if kind == "retry" else "", msg["user"], owed),
               "done: handed over %s" % ident, kind_="handed-over")
        self.counts["acted"] = self.counts.get("acted", 0) + 1
        self.say("  acted: handed %s to the agent%s for %s"
                 % (ident, " again" if kind == "retry" else "", msg["user"]))


# ══════════════════════════════════════════════════════════════════════════════════════
# Selftest — offline. This module's cases run in the bridge's own selftest, end to end
# through one pass, and in its own, which pins what the module may reach (KIT-407).
# ══════════════════════════════════════════════════════════════════════════════════════
def cases_handover(kit):
    """`retry <ticket>` and `start <ticket>` in a DM (KIT-352): each asks first, and hands
    the ticket to the agent only on a yes, never while a session runs on it."""
    import json
    from pipeline_bridge import ALICE, DM, state_path
    with kit as _tmp:
        check, now, outcome, run = kit.take("check", "now", "outcome", "run")
        dmworld, dm_lines, cfg_for = kit.take("dmworld", "dm_lines", "cfg_for")
        hour = 3600
        on = dict(act=True, agent_user_name="Agent")
        ready = ("## Context\nWhy it matters.\n\n## Acceptance criteria\n- [ ] The light theme "
                 "follows the system setting.\n\n"
                 "## Out of scope\n- Nothing else.\n\n## Test plan\n- A case.\n\n"
                 "## Pointers\n- `docs/BRIDGE-OPERATOR.md`\n")

        def created(at):
            return iso(at)[:-1] + ".000Z"

        def statuses(c):
            return sorted((q["ticket"], q["kind"], q["status"])
                          for q in json.load(open(state_path(c)))["questions"].values())

        def held(t, ident="PROD-6", sessions=(), at=None):
            """PROD-6 handed to the agent, with these sessions on it."""
            t.issues[ident]["delegate"] = {"id": "agent-1"}
            t.sessions[t.issues[ident]["id"]] = [dict(s) for s in sessions]

        def readied(t, ident="PROD-6"):
            """PROD-6 written to the ticket template, with the labels and project it needs."""
            t.issues[ident].update(description=ready, project={"id": "proj-1"}, labels={
                "nodes": [{"name": n} for n in ("effort:S", "track:ui", "provenance:human")]})

        def asked(name, text, setup=None, at=None, **over):
            c, s, t = dmworld(name, **dict(on, **over))
            if setup:
                setup(t)
            s.add(ALICE, text, channel=DM)
            run(c, s, t, at=at or now + 60)
            return c, s, t

        def answered(c, s, t, at, answer="yes"):
            s.add(ALICE, answer, channel=DM)
            run(c, s, t, at=at)
            return dm_lines(s)[-1]

        # 1. RETRY, past Linear's retries: one question, and on the yes the one hand-over
        #    write again, its new session read back, and the comment naming who said yes.
        lost = [{"id": "s-6", "status": "pending", "createdAt": created(now)}]
        late = now + HANDOVER_RETRY_SECONDS + 60

        def making(t):
            """Linear makes a new session only for a real change of delegate, as the docs'
            remedy assumes: off, then back on. Setting the agent it already has makes none."""
            real = t.delegate

            def delegate(issue_id, agent_id):
                held_by = next(((i.get("delegate") or {}).get("id") for i in t.issues.values()
                                if i["id"] == issue_id), None)
                real(issue_id, agent_id)
                if agent_id and agent_id != held_by:
                    t.sessions.setdefault(issue_id, []).append(
                        {"id": "s-new-%d" % len(t.delegations), "status": "pending",
                         "createdAt": created(late)})
            t.delegate = delegate

        def case_retried():
            c, s, t = asked("ho-retry", "retry PROD-6", lambda t_: (held(t_, sessions=lost),
                                                                   making(t_)), at=late)
            question = dm_lines(s)[-1]
            said = answered(c, s, t, late + 60)
            return (question.startswith("*Hand this over again?* PROD-6"),
                    "It was handed to the agent at %s and hasn't started." % iso(now)
                    in question, "and I'll hand it over to the agent again" in question,
                    t.delegations, said, len(t.comments),
                    "Handed to the agent again by the tech lead" in t.comments[0][1],
                    statuses(c), getattr(t, "forgot", 0))
        check("ho-retry-past-linears-retries-asks-then-hands-it-over-again-on-a-yes",
              outcome(case_retried),
              (True, True, True, [("uuid-6", None), ("uuid-6", "agent-1")],
               "Handed PROD-6 to the agent again for <@%s>. Its dev should start within a few "
               "minutes." % ALICE, 1, True, [("PROD-6", "retry", DONE)], 1))

        # 2. RETRY inside Linear's retries is refused, naming when it is safe; and one with
        #    a dev at work, or not the agent's, is refused too. None asks anything.
        def case_refused():
            safe = iso(now + HANDOVER_RETRY_SECONDS)
            _c, s1, t1 = asked("ho-early", "retry PROD-6", lambda t_: held(t_, sessions=lost),
                               at=now + 3 * hour)
            _c, s2, t2 = asked("ho-running", "retry PROD-6", lambda t_: held(
                t_, sessions=[{"id": "s-6", "status": "active", "createdAt": created(now)}]),
                at=late)
            _c, s3, t3 = asked("ho-not-held", "retry PROD-6", at=late)
            return (dm_lines(s1)[-1], dm_lines(s2)[-1], dm_lines(s3)[-1],
                    t1.delegations + t2.delegations + t3.delegations)
        check("ho-retry-is-refused-inside-linears-retries-while-a-dev-works-or-not-the-agents",
              outcome(case_refused),
              ("Linear may still deliver PROD-6's hand-over from %s until %s, so a second one "
               "now could start two devs on it. Ask again with `retry PROD-6` after %s."
               % (iso(now), iso(now + HANDOVER_RETRY_SECONDS), iso(now + HANDOVER_RETRY_SECONDS)),
               "A dev is working on PROD-6 now, so I won't hand it over again.",
               "PROD-6 isn't handed to the agent, so there's nothing to retry. To hand it to "
               "the agent, type `start PROD-6`.", []))

        # 3. RETRY, at the yes: a dev that started meanwhile hands nothing over. When Linear
        #    makes no new session, nothing was retried, and it says so, with no comment; its
        #    two writes still count as the one action (Stage E's review of #325). When the
        #    agent can't be put back on, nobody holds the ticket, and that is said.
        def case_at_the_yes():
            c, s, t = asked("ho-started", "retry PROD-6", lambda t_: held(t_, sessions=lost),
                            at=late)
            t.sessions["uuid-6"][0]["status"] = "active"
            started = answered(c, s, t, late + 60)
            c2, s2, t2 = asked("ho-no-new", "retry PROD-6", lambda t_: held(t_, sessions=lost),
                               at=late)
            unmade = answered(c2, s2, t2, late + 60)
            acted = len(json.load(open(state_path(c2))).get("acted_at") or [])

            def refusing_the_agent(t_):
                held(t_, sessions=lost)
                real = t_.delegate

                def delegate(issue_id, agent_id):
                    if agent_id:
                        raise BridgeError("the agent could not be set (simulated)")
                    real(issue_id, agent_id)
                t_.delegate = delegate
            c3, s3, t3 = asked("ho-half-done", "retry PROD-6", refusing_the_agent, at=late)
            half = answered(c3, s3, t3, late + 60)
            return (started, t.delegations, unmade, t2.delegations, t2.comments,
                    statuses(c2), acted, half, t3.delegations, t3.issues["PROD-6"]["delegate"],
                    statuses(c3))
        check("ho-retry-at-the-yes-reads-again-and-says-when-linear-made-no-new-hand-over",
              outcome(case_at_the_yes),
              ("A dev is working on PROD-6 now, so I won't hand it over again. Nothing was "
               "handed over.", [],
               "I took the agent off PROD-6 and put it back on, but Linear made no new "
               "hand-over for it, so nothing was retried. Check it in Linear.",
               [("uuid-6", None), ("uuid-6", "agent-1")], [], [("PROD-6", "retry", REFUSED)],
               1, "I took the agent off PROD-6 to hand it over again, but couldn't put it "
                  "back on, so nobody holds it now. Hand it to the agent in Linear.",
               [("uuid-6", None)], None, [("PROD-6", "retry", REFUSED)]))

        # 4. START: a ready ticket nobody holds gets one question, and the yes hands it over
        #    with the comment; one that fails the readiness gate is refused, naming the rules
        #    in plain words; a held one points at `retry`; a plan at `approve`.
        def case_started():
            c, s, t = asked("ho-start", "please start PROD-6", readied)
            question = dm_lines(s)[-1]
            said = answered(c, s, t, now + 120)
            return (question.startswith("*Start this?* PROD-6"),
                    "and I'll hand it to the agent to start" in question, t.delegations, said,
                    "Handed to the agent by the tech lead" in (t.comments or [("", "")])[0][1])
        check("ho-start-asks-then-hands-a-ready-ticket-to-the-agent-on-a-yes",
              outcome(case_started),
              (True, True, [("uuid-6", "agent-1")],
               "Handed PROD-6 to the agent for <@%s>. Its dev should start within a few "
               "minutes." % ALICE, True))

        def case_start_refused():
            _c, s1, t1 = asked("ho-unready", "start PROD-6")
            _c, s2, t2 = asked("ho-held", "start PROD-6", lambda t_: (readied(t_), held(t_)))
            _c, s3, t3 = asked("ho-plan", "start PROD-50", lambda t_: t_.add_epic(
                "PROD-50", (("PROD-51", ()),)))
            return (dm_lines(s1)[-1], dm_lines(s2)[-1], dm_lines(s3)[-1],
                    t1.delegations + t2.delegations + t3.delegations)
        check("ho-start-is-refused-for-an-unready-held-or-plan-ticket",
              outcome(case_start_refused),
              ("PROD-6 isn't ready to start: it fails the readiness gate on its sections, its "
               "labels, its project and where it came from.",
               "PROD-6 is already handed to the agent. If it never started, type `retry PROD-6`.",
               "PROD-50 is a plan. To start its pieces, type `approve PROD-50`.", []))

        # 4b. Every other check, each said once and asking nothing: what each says, and that
        #     none hands anything over. A dev that ran and ended may be retried at once.
        def other(t, ident="PROD-6", **fields):
            t.issues[ident].update(fields)

        ended = [{"id": "s-6", "status": "complete", "createdAt": created(now)}]
        # KIT-645: a project switched to our own runner. The queue's own settings route PROD to
        # acme/app, and its working window never opens here, so no pass starts anything.
        rfolder = os.path.join(_tmp, "runner-handover")
        os.makedirs(os.path.join(rfolder, "store"))
        qconf, rsettings = os.path.join(rfolder, "queue.json"), os.path.join(rfolder, "runner.json")
        with open(qconf, "w", encoding="utf-8") as fh:
            json.dump({"pool": {"owner_user_ids": ["owner-1"], "team_keys": ["PROD"],
                                "dispatcher_user_ids": ["agent-1"]},
                       "reader": {"repo_by_team": {"PROD": "acme/app"},
                                  "inputs_file": os.path.join(rfolder, "in.json")},
                       "window": {"days": dict((d, None) for d in (
                           "mon", "tue", "wed", "thu", "fri", "sat", "sun"))}}, fh)
        with open(rsettings, "w", encoding="utf-8") as fh:
            json.dump({"enabled": True, "store_dir": os.path.join(rfolder, "store"),
                       "projects": {"app": {"lanes": {"code": True}}}}, fh)
        switched = dict(queue_config=qconf, runner_lane={"settings_file": rsettings,
                                                         "repos": {"acme/app": "app"}})
        unread = dict(queue_config=qconf, runner_lane={
            "settings_file": os.path.join(rfolder, "gone.json"), "repos": {"acme/app": "app"}})
        rows = (
            # KIT-961: a switched project's start is the runner's road, and with no owner named in
            # the queue's settings no yes there can start anything: said, never asked.
            ("ho-runner-start", "start PROD-6", readied, switched,
             "I can't take a yes to start PROD-6 on our own runner: the queue's settings name no "
             "Slack member as the owner."),
            ("ho-runner-unread", "retry PROD-6", lambda t_: held(t_, sessions=lost), unread,
             "I can't tell whether PROD-6's project codes on our own runner (the runner's settings "
             "could not be read: it could not be opened (FileNotFoundError)), so I won't hand it "
             "over."),
            ("ho-no-agent", "start PROD-6", readied, dict(agent_user_name=""),
             "I can't hand anything to the agent: no agent account is named here."),
            ("ho-unknown-agent", "start PROD-6", readied, dict(agent_user_name="Nobody"),
             "I can't hand anything to the agent: no app user is called 'Nobody'."),
            ("ho-gone", "start PROD-9", None, {}, "I can't find PROD-9 in Linear."),
            ("ho-team", "start OPS-1", None, {},
             "OPS-1 is on OPS, and I hand work over only on PROD."),
            ("ho-done", "retry PROD-6", lambda t_: (held(t_, sessions=lost), other(
                t_, state={"id": "st-done", "name": "Done", "type": "completed"})), {},
             "PROD-6 is Done, so there's nothing to hand over."),
            ("ho-handed-before", "start PROD-6", lambda t_: (readied(t_), t_.sessions.update(
                {"uuid-6": list(lost)})), {},
             "PROD-6 was handed to the agent before. If it never started, type `retry PROD-6`."),
            ("ho-triage", "start PROD-6", lambda t_: (readied(t_), other(
                t_, state={"id": "st-triage", "name": "Triage", "type": "triage"})), {},
             "PROD-6 is in Triage, so I won't start it."),
            ("ho-started-lane", "start PROD-6", lambda t_: (readied(t_), other(
                t_, state={"id": "st-started", "name": "In Progress", "type": "started"})), {},
             "PROD-6 is in In Progress, so I won't start it."),
            ("ho-planning-run", "start PROD-6", lambda t_: (readied(t_), other(
                t_, description=machine.planning_tag(machine.PLANNING_PREFIX + "prod") + "\n"
                + ready)), {},
             "PROD-6 is a planning run, which the planner starts itself."),
            ("ho-someone-elses", "start PROD-6", lambda t_: (readied(t_), other(
                t_, delegate={"id": "someone"})), {}, "PROD-6 is already handed to someone else."),
            ("ho-retry-someone-elses", "retry PROD-6", lambda t_: other(
                t_, delegate={"id": "someone"}), {},
             "PROD-6 isn't handed to the agent, so there's nothing to retry."),
            ("ho-cut-list", "retry PROD-6", lambda t_: (held(t_, sessions=lost), setattr(
                t_, "sessions_whole", False)), {},
             "I can't tell when PROD-6 was handed over, so I won't hand it over again. Check "
             "it in Linear."))

        def case_each_check():
            got = []
            for name, text, setup, over, _want in rows:
                _c, s, t = asked(name, text, setup, **over)
                got.append((dm_lines(s)[-1], t.delegations))
            c, s, t = asked("ho-ended", "retry PROD-6", lambda t_: held(t_, sessions=ended),
                            at=now + 3 * hour)
            got.append((dm_lines(s)[-1].startswith("*Hand this over again?* PROD-6")
                        and "Its dev stopped before it finished." in dm_lines(s)[-1],
                        t.delegations))
            return got
        check("ho-each-check-refuses-once-with-no-hand-over-and-an-ended-dev-retries-at-once",
              outcome(case_each_check), [(want, []) for _n, _t, _s, _o, want in rows]
              + [(True, [])])

        # 4c. KIT-961: on a project that codes on our own runner, `start`, `retry` and `resume`
        #     ask, and only the owner's yes records the approval, with its message, for the
        #     queue. Nothing is handed over and nothing is written to Linear.
        from pipeline_bridge import TEAM
        from pipeline_bridge_config import OPEN

        def lane_conf(name, owners=(ALICE,), code=True, hold=None):
            folder = os.path.join(_tmp, "runner-961", name)
            os.makedirs(folder)
            store = os.path.join(folder, "store")
            runner_store.prepare(store)
            qpath, rpath = os.path.join(folder, "queue.json"), os.path.join(folder, "runner.json")
            queue = {"pool": {"owner_user_ids": ["owner-1"], "team_keys": ["PROD"],
                              "dispatcher_user_ids": ["agent-1"],
                              "owner_slack_user_ids": list(owners)},
                     "reader": {"repo_by_team": {"PROD": "acme/app"},
                                "inputs_file": os.path.join(folder, "in.json")},
                     "window": {"days": dict((d, None) for d in (
                         "mon", "tue", "wed", "thu", "fri", "sat", "sun"))}}
            if hold:
                queue["hold"] = {"file": hold}
            with open(qpath, "w", encoding="utf-8") as fh:
                json.dump(queue, fh)
            with open(rpath, "w", encoding="utf-8") as fh:
                json.dump({"enabled": True, "store_dir": store,
                           "projects": {"app": {"lanes": {"code": code}}}}, fh)
            return dict(queue_config=qpath, runner_lane={"settings_file": rpath,
                                                         "repos": {"acme/app": "app"}}), store

        def stopped(store, state="ended"):
            """PROD-6's code run on our own runner, as the runner records one."""
            rec = dict((k, None) for k in runner_store.RUN_KEYS)
            rec.update(schema=runner_store.RUN_SCHEMA, run="run-" + "c" * 32, request="rq-" + "d" * 32,
                       caller="queue", kind="code", project="app", ticket="PROD-6",
                       started_for="request", approved_by="owner-1", approved_at=iso(now - hour),
                       how="move", session_id="0b0e5e2c-1111-4222-8333-944445555666",
                       workspace=os.path.join(store, "w"), branch="feat/prod-6-x", state=state,
                       started_at=iso(now - hour))
            if state not in runner_store.LIVE:
                rec.update(ended_at=iso(now - 60), reason="ended", seconds=3540.0, exit=0,
                           why="the program ended with exit 0")
            runner_store.write_run(store, rec)
            return rec

        def slack_record(c):
            return (json.load(open(state_path(c))).get("slack_starts") or {}).get("uuid-6")

        def case_runner_start():
            over, _store = lane_conf("start")
            c, s, t = asked("ho961-start", "start PROD-6", readied, **over)
            question = dm_lines(s)[-1]
            said = answered(c, s, t, now + 120)
            rec = slack_record(c) or {}
            return (question.startswith("*Start this on our own runner?* PROD-6"),
                    "so only the owner's yes counts here" in question,
                    "and I'll ask our own runner to start it once the queue has room for it: inside "
                    "its working window, its lanes and today's limits." in question, said,
                    tuple(rec.get(k) for k in ("ticket", "kind", "approved_by", "team", "channel",
                                               "answered")),
                    rec.get("approved_at") == iso(ts_float(rec.get("ts"))), t.delegations,
                    t.comments, statuses(c))
        check("ho961-start-on-our-runner-asks-and-the-owners-yes-records-it-with-its-message",
              outcome(case_runner_start),
              (True, True, True,
               "Approved by <@%s>. PROD-6 starts on our own runner once the queue has room for it: "
               "inside its working window, its lanes and today's limits, as a move into Todo "
               "would. You'll hear here when it starts, or why it can't." % ALICE,
               ("PROD-6", "start", ALICE, TEAM, DM, None), True, [], [], [("PROD-6", "start", DONE)]))

        def case_runner_not_owner():
            over, _store = lane_conf("not-owner", owners=("U0NOTALICE1",))
            c, s, t = asked("ho961-not-owner", "start PROD-6", readied, **over)
            said = answered(c, s, t, now + 120)
            return said, slack_record(c), t.delegations, statuses(c)
        check("ho961-a-yes-from-anyone-but-the-owner-the-queue-names-is-refused-in-words",
              outcome(case_runner_not_owner),
              ("Only the owner's yes starts work on our own runner, so I can't take that as an "
               "answer to the question about PROD-6.", None, [], [("PROD-6", "start", OPEN)]))

        def case_runner_edited():
            over, _store = lane_conf("edited")
            c, s, t = asked("ho961-edited", "start PROD-6", readied, **over)
            s.add(ALICE, "yes", channel=DM, edited={"user": ALICE, "ts": "1"})
            run(c, s, t, at=now + 120)
            return ("it was edited after it was sent" in dm_lines(s)[-1], slack_record(c),
                    statuses(c))
        check("ho961-an-edited-yes-records-nothing", outcome(case_runner_edited),
              (True, None, [("PROD-6", "start", OPEN)]))

        def case_runner_replayed():
            over, _store = lane_conf("replayed")
            c, s, t = asked("ho961-replayed", "start PROD-6", readied, **over)
            answered(c, s, t, now + 120)
            before = (len(dm_lines(s)), slack_record(c))
            run(c, s, t, at=now + 180)
            run(c, s, t, at=now + 240)
            return before == (len(dm_lines(s)), slack_record(c)), before[1] is not None
        check("ho961-the-same-yes-read-again-records-and-says-nothing-more",
              outcome(case_runner_replayed), (True, True))

        def case_runner_resume(verb):
            over, store = lane_conf("resume-" + verb)
            ran = stopped(store)
            c, s, t = asked("ho961-" + verb, "%s PROD-6" % verb, None, **over)
            question = dm_lines(s)[-1]
            said = answered(c, s, t, now + 120)
            rec = slack_record(c) or {}
            return (question.startswith("*Resume this on our own runner?* PROD-6"),
                    "Its newest run on our own runner ended at %s." % ran["ended_at"] in question,
                    "and I'll ask our own runner to resume its stopped run once the queue has room, "
                    "inside its working window." in question, said,
                    (rec.get("kind"), rec.get("run")), t.delegations)
        for verb in ("retry", "resume"):
            check("ho961-%s-on-our-runner-asks-to-resume-its-newest-stopped-run" % verb,
                  outcome(lambda: case_runner_resume(verb)),
                  (True, True, True,
                   "Approved by <@%s>. Our own runner resumes PROD-6's stopped run once the queue has "
                   "room, inside its working window. You'll hear here when it does, or why it "
                   "can't." % ALICE, ("resume", "run-" + "c" * 32), []))

        def case_runner_refusals():
            got = []
            for name, text, setup, runs, want in (
                    ("ran-before", "start PROD-6", readied, stopped,
                     "PROD-6 ran on our own runner before. To resume its stopped run, type "
                     "`resume PROD-6`."),
                    ("no-run", "retry PROD-6", None, None,
                     "PROD-6 has no stopped run on our own runner to resume. To start it, type "
                     "`start PROD-6`."),
                    ("live", "retry PROD-6", None, lambda st: stopped(st, state="running"),
                     "A run is at work on PROD-6 on our own runner now, so I won't ask for "
                     "another.")):
                over, store = lane_conf("r-" + name)
                if runs:
                    runs(store)
                c, s, t = asked("ho961-" + name, text, setup, **over)
                got.append((dm_lines(s)[-1] == want, t.delegations, slack_record(c)))
            over, _store = lane_conf("r-waits")
            c, s, t = asked("ho961-waits", "start PROD-6", readied, **over)
            answered(c, s, t, now + 120)
            s.add(ALICE, "start PROD-6", channel=DM)
            run(c, s, t, at=now + 180)
            got.append(dm_lines(s)[-1])
            return got
        check("ho961-each-runner-check-is-said-once-and-records-nothing",
              outcome(case_runner_refusals),
              [(True, [], None)] * 3 + ["Your yes for PROD-6 already waits for the queue to have room."])

        def case_runner_held():
            import pipeline_hold as watcher_hold
            hold = os.path.join(_tmp, "runner-961-hold.json")
            watcher_hold.write(hold, [("labels", "protected-label", "acme/app#12")], lambda: now)
            over, store = lane_conf("held", hold=hold)
            stopped(store)
            c, s, t = asked("ho961-held", "resume PROD-6", None, **over)
            return dm_lines(s)[-1].split(". To clear it")[0], slack_record(c)
        check("ho961-a-watchers-hold-on-a-resume-is-said-as-one-and-records-nothing",
              outcome(case_runner_held),
              ("I won't ask our own runner to resume PROD-6: the watcher has held new starts: "
               "protected-label acme/app#12", None))

        def case_runner_switch():
            over, _store = lane_conf("back")
            c, s, t = asked("ho961-back", "start PROD-6", readied, **over)
            # The runner's own switch moves the project back to the old road before the yes.
            rpath = over["runner_lane"]["settings_file"]
            with open(rpath, encoding="utf-8") as fh:
                doc = json.load(fh)
            doc["projects"]["app"]["lanes"]["code"] = False
            with open(rpath, "w", encoding="utf-8") as fh:
                json.dump(doc, fh)
            said = answered(c, s, t, now + 120)
            old, _store2 = lane_conf("old", code=False)
            _c2, s2, t2 = asked("ho961-old", "start PROD-6", readied, **old)
            return (said, slack_record(c), t.delegations,
                    dm_lines(s2)[-1].startswith("*Start this?* PROD-6"))
        check("ho961-the-runners-own-lane-switch-moves-a-project-back-and-a-yes-asked-before-acts-on-nothing",
              outcome(case_runner_switch),
              ("PROD-6's project doesn't code on our own runner as it did when I asked, so nothing "
               "was asked of it, and I have closed the question. Ask again with `start PROD-6`.",
               None, [], True))

        # 5. START, at the yes: a ticket changed since the question hands nothing over.
        def case_changed():
            c, s, t = asked("ho-changed", "start PROD-6", readied)
            t.issues["PROD-6"]["title"] = "Something the member never saw"
            said = answered(c, s, t, now + 120)
            # The agent account the question named is a term of the yes, too.
            c2, s2, t2 = asked("ho-agent-changed", "start PROD-6", readied)
            t2.agents["Other"] = "agent-2"
            moved = cfg_for("ho-agent-changed", **dict(on, dm_user_ids=[ALICE],
                                                       agent_user_name="Other"))
            s2.add(ALICE, "yes", channel=DM)
            run(moved, s2, t2, at=now + 120)
            return said, t.delegations, dm_lines(s2)[-1].startswith(
                "My settings changed since I asked about PROD-6"), t2.delegations
        check("ho-start-of-a-ticket-or-agent-changed-since-the-question-hands-nothing-over",
              outcome(case_changed),
              ("PROD-6 changed since I asked (its title, text, team, labels or state), so "
               "nothing was handed over, and I have closed the question. Ask again with "
               "`start PROD-6`.", [], True, []))


        # 5. THE WATCHER'S HOLD (KIT-528). Found through the queue's config. A hold, a hold file
        #    that is not a hold or cannot be read, or a config that cannot be read, refuses a start
        #    or a retry, in the question and again in the yes, and says what the owner can do.
        #    Nothing is handed over. No queue_config means the bridge knows no hold file.
        import pipeline_queue_start as starter
        hold_doc = {"schema": "pipeline-hold/1", "set_at": "2025-10-09T06:00:00Z",
                    "holds": [{"monitor": "runs", "what": "runs-day", "ref": "PROD-12",
                               "since": "2025-10-09T06:00:00Z"}]}

        def queue_conf(name, hold=None, text=None):
            folder = os.path.join(_tmp, "hold-" + name)
            os.makedirs(folder)
            path = os.path.join(folder, "queue.json")
            with open(path, "w", encoding="utf-8") as fh:
                if text is not None:
                    fh.write(text)
                else:
                    json.dump({"pool": {"owner_user_ids": ["owner-1"], "team_keys": ["PROD"],
                                        "dispatcher_user_ids": ["agent-1"]},
                               "reader": {"ledger_file": os.path.join(folder, "ledger.json"),
                                          "inputs_file": os.path.join(folder, "in.json")},
                               "morning": {"usage_file": os.path.join(folder, "usage.json")},
                               "hold": {"file": os.path.join(folder, "hold.json")}}, fh)
            return path, os.path.join(folder, "hold.json")

        def put_hold(hold_path, how):
            if how == "held":
                with open(hold_path, "w", encoding="utf-8") as fh:
                    json.dump(hold_doc, fh)
            elif how == "junk":
                with open(hold_path, "w", encoding="utf-8") as fh:
                    fh.write("not json")
            elif how == "other":
                with open(hold_path, "w", encoding="utf-8") as fh:
                    json.dump(dict(hold_doc, holds=[]), fh)
            elif how == "folder":
                os.mkdir(hold_path)
            elif how == "locked":
                with open(hold_path, "w", encoding="utf-8") as fh:
                    json.dump(hold_doc, fh)
                os.chmod(hold_path, 0)

        def under_hold(name, how, line, setup, text=None, at=None):
            """The ask under a hold, as (the DM lines after the ask, the delegations)."""
            conf, hold_path = queue_conf(name, text=text)
            put_hold(hold_path, how)
            real = starter.decide
            starter.decide = lambda raw, gql, now_, clock=None, say=None: {
                "state": "closed", "why": "before-start", "decision": None}
            try:
                c, s, t = asked(name, line, setup, at=at, queue_config=conf)
                return dm_lines(s), t.delegations, hold_path
            finally:
                starter.decide = real

        def clear_words(hold_path):
            return ("To clear it, in a terminal: python3 scripts/pipeline_hold.py clear "
                    "--hold-file %s" % hold_path)

        def case_hold(how, line="start PROD-6", setup=readied, text=None, at=None):
            def go():
                lines, delegations, hold_path = under_hold("ho-hold-" + how + line[:2], how, line,
                                                           setup, text, at)
                return lines, delegations, clear_words(hold_path)
            return go
        for how, line, setup, why, at_ in (
                ("held", "start PROD-6", readied, "the watcher has held new starts: runs-day "
                 "PROD-12", None),
                ("held", "retry PROD-6", lambda t_: (held(t_, sessions=lost), making(t_)),
                 "the watcher has held new starts: runs-day PROD-12", late),
                ("junk", "start PROD-6", readied, "the hold file is not readable JSON, so "
                 "nothing starts", None),
                ("other", "start PROD-6", readied, "the hold file is not a hold: it lists no "
                 "hold, or too many, so nothing starts", None),
                ("folder", "start PROD-6", readied, "the queue's settings are not usable, so "
                 "I can't tell whether the watcher has held new starts", None),
                ("locked", "start PROD-6", readied, "the hold file could not be read "
                 "(Permission denied), so nothing starts", None)):
            if how == "locked" and os.geteuid() == 0:
                continue                    # root reads any file: the case cannot be made
            lines, delegations, clear = outcome(case_hold(how, line, setup, at=at_))
            check("ho-a-%s-hold-refuses-%s-and-says-how-to-clear-it" % (how, line.split()[0]),
                  (lines, delegations),
                  (["I won't hand PROD-6 to the agent: %s.%s" % (
                      why, "" if how == "folder" else " " + clear)], []))
        lines, delegations, hold_path = under_hold("ho-hold-config", "none", "start PROD-6",
                                                   readied, text="not json")
        check("ho-a-queue-config-that-cannot-be-read-refuses-a-start",
              (lines, delegations),
              (["I won't hand PROD-6 to the agent: the queue's settings could not be read "
                "(JSONDecodeError), so I can't tell whether the watcher has held new starts."],
               []))
        lines, delegations, hold_path = under_hold("ho-hold-none", "none", "start PROD-6",
                                                   readied)
        check("ho-no-hold-file-asks-as-before",
              (lines[0].startswith("*Start this?* PROD-6"), delegations), (True, []))

        def case_hold_after_the_ask():
            conf, hold_path = queue_conf("ho-hold-late")
            real = starter.decide
            starter.decide = lambda raw, gql, now_, clock=None, say=None: {
                "state": "closed", "why": "before-start", "decision": None}
            try:
                c, s, t = asked("ho-hold-late", "start PROD-6", readied, queue_config=conf)
                asked_line = dm_lines(s)[-1]
                put_hold(hold_path, "held")
                said = answered(c, s, t, now + 120)
                return (asked_line.startswith("*Start this?* PROD-6"), said, t.delegations,
                        statuses(c))
            finally:
                starter.decide = real
        check("ho-a-hold-set-after-the-question-refuses-the-yes-and-hands-nothing-over",
              outcome(case_hold_after_the_ask),
              (True, "I won't hand PROD-6 to the agent: the watcher has held new starts: "
                     "runs-day PROD-12. Nothing was handed over. To clear it, in a terminal: "
                     "python3 scripts/pipeline_hold.py clear --hold-file %s"
                     % os.path.join(_tmp, "hold-ho-hold-late", "hold.json"),
               [], [("PROD-6", "start", REFUSED)]))


def selftest():
    import pipeline_bridge
    return pipeline_bridge.split_module_selftest(os.path.abspath(__file__), [cases_handover])


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        # As the module the bridge imports, never a copy of it named __main__, so its
        # cases and the pass share one copy of it (KIT-312).
        import pipeline_bridge_handover
        sys.exit(pipeline_bridge_handover.selftest())
    print("usage: pipeline_bridge_handover.py --selftest (the bridge imports this module)")
    sys.exit(EXIT_USAGE)
