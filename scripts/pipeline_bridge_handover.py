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
  The act     act_handover: everything read again, the one hand-over write, the sessions
              read back for a retry (none new: nothing was retried, and it says so), then
              the comment naming who said yes.
"""
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import check_ticket_dor as dor  # noqa: E402
import pipeline_machine_tickets as machine  # noqa: E402
import pipeline_notify_local as notify  # noqa: E402
from pipeline_bridge_config import (ASK_AGAIN_BIND, DONE, EXIT_USAGE,  # noqa: E402
                                    REFUSED, SLACK_USER_RE, credit_terms, iso, label_names,
                                    ts_float)
from pipeline_bridge_transport import BridgeError  # noqa: E402
from pipeline_bridge_plans import HANDOVER_RETRY_SECONDS, SESSION_RUNNING  # noqa: E402
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
# A ticket `start` takes: one nobody began. Done or canceled is never handed over.
START_STATES = ("backlog", "unstarted", "triage")
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


def handover_parts(kind, issue, since, requester, cfg, by_bot=False, dm=False, terms=None):
    """(head, body, bind, facts) for a retry or start question (KIT-352), as question_parts
    makes a plan question's. `since` is when a retried ticket's newest hand-over was made,
    or None."""
    title = notify.safe_title(issue.get("title") or "")
    creator = notify.safe_title(((issue.get("creator") or {}).get("displayName")
                                 or (issue.get("creator") or {}).get("name") or "someone"))
    state = notify.safe_title((issue.get("state") or {}).get("name") or "an unknown state")
    team = ((issue.get("team") or {}).get("key")) or "?"
    if by_bot:
        who = "the tech lead" if requester == cfg.get("chat_bot_user_id") else "a bot"
    else:
        who = ("<@%s>" % requester) if SLACK_USER_RE.match(requester or "") else "a bot"
    then = RETRY_THEN_BIND if kind == "retry" else START_THEN_BIND
    if not cfg["act"]:
        then = ACT_OFF_THEN_BIND
    body = ("%s “%s”: in %s on %s, filed by %s."
            % (issue.get("identifier"), title, state, team, creator))
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


class HandoverMixin(object):
    """Pass's `retry` and `start` (KIT-352)."""

    def handover_read(self, kind, ident):
        """(issue, sessions, agent id, refusal) for a retry or start of the ticket now: the
        refusal as handover_refusal gives it, or None when it may go ahead."""
        name = self.cfg["agent_user_name"].strip()
        if not name:
            return None, [], None, (
                "I can't hand anything to the agent: no agent account is named here.", "",
                "no agent named")
        agent_id, why = self.tracker.agent_user(name)
        if not agent_id:
            return None, [], None, ("I can't hand anything to the agent: %s." % why, "",
                                    "no agent")
        issue = self.tracker.issue(ident)
        if not issue or not issue.get("id"):
            return None, [], agent_id, ("I can't find %s in Linear." % ident, "", "not found")
        node = self.tracker.epic(ident) or {}
        sessions = [n or {} for n in self.tracker.sessions_on(issue["id"])]
        return issue, sessions, agent_id, self.handover_refusal(kind, issue, node, sessions,
                                                                agent_id)

    def handover_refusal(self, kind, issue, node, sessions, agent_id):
        """Why the ticket can't be retried or started now, as (text, bind, why), or None. A
        ticket with a session running is never handed over again: that would start a second
        one on it (KIT-352)."""
        ident = issue["identifier"]
        team = (issue.get("team") or {}).get("key")
        if team not in self.cfg["team_keys"]:
            return ("%s is on %s, and I hand work over only on %s."
                    % (ident, team or "no team", ", ".join(self.cfg["team_keys"])), "", "team")
        state = issue.get("state") or {}
        if state.get("type") in ENDED_STATES:
            return ("%s is %s, so there's nothing to hand over."
                    % (ident, notify.safe_title(state.get("name") or "closed")), "", "closed")
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
        """`retry <ticket>` or `start <ticket>` (KIT-352): every check, then the question."""
        issue, sessions, _agent, refusal = self.handover_read(kind, ident)
        if refusal:
            text, bind, why = refusal
            self.refuse_request(channel, msg, thread, text, why, bind=bind, ticket=ident)
            return
        since = self.handed_since(sessions) if kind == "retry" and not self.newest_ended(
            sessions) else None
        self.ask(channel, msg, thread, kind, issue, requester,
                 handover_parts(kind, issue, since, requester, self.cfg, by_bot=by_bot, dm=dm,
                                terms=terms), terms=terms, set_up=set_up)

    def act_handover(self, channel, thread, q, msg):
        """The yes to a retry or start: everything read again, as the question judged it and
        bound to what it showed, then the one hand-over write, and the comment naming who
        said yes. A retry's sessions are read back: Linear may make no new one for a ticket
        already handed to the agent, and then nothing was retried, and it says so."""
        kind, ident = q["kind"], q["ticket"]

        def finish(status, text, what, kind_="handover-refused", bind=""):
            # Posted first: the status changes only after the reply saying so lands.
            self.post(channel, thread, text, kind=kind_, bind=bind, join=" ",
                      facts={"ticket": ident})
            q.update(status=status, answered_by=msg["user"], answered_ts=msg.get("ts"))
            self.mark(channel, msg, what)
        if self.refused_over_limit(channel, thread, msg):
            return
        issue, sessions, agent_id, refusal = self.handover_read(kind, ident)
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
        self.tracker.delegate(issue["id"], agent_id)
        self.state["acted_at"].append(self.now)
        if kind == "retry":
            self.tracker.forget_sessions()
            made = [n for n in self.tracker.sessions_on(issue["id"])
                    if (n or {}).get("id") not in before]
            if not made:
                finish(REFUSED, "I handed %s to the agent again, but Linear made no new "
                       "hand-over for it, so nothing was retried. In Linear, take the agent "
                       "off it and put it back." % ident, "refused: no new hand-over",
                       kind_="handover-not-made")
                self.say("  retry: Linear made no new hand-over for %s" % ident)
                return
        name = self.name_of(msg["user"])
        body = ("Handed to the agent%s by the Slack bridge for **%s** (Slack user %s), who "
                "said yes in Slack at %s." % (" again" if kind == "retry" else "", name,
                                              msg["user"], iso(ts_float(msg.get("ts")))))
        owed = ""
        try:
            self.tracker.comment(issue["id"], body)
        except BridgeError as exc:
            # The hand-over stands: it cannot be taken back. The record of who said yes is owed.
            self.say("  NOTE: %s was handed over, but its attribution comment failed: %s"
                     % (ident, exc))
            self.owe(issue["id"], ident, body)
            owed = (" The hand-over was done, but the comment naming who approved it could "
                    "not be written; I will try again.")
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
            """Linear makes a new session for a hand-over, as it does for a first one."""
            real = t.delegate

            def delegate(issue_id, agent_id):
                real(issue_id, agent_id)
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
                    "Handed to the agent again by the Slack bridge" in t.comments[0][1],
                    statuses(c), getattr(t, "forgot", 0))
        check("ho-retry-past-linears-retries-asks-then-hands-it-over-again-on-a-yes",
              outcome(case_retried),
              (True, True, True, [("uuid-6", "agent-1")],
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

        # 3. RETRY, at the yes: a dev that started meanwhile hands nothing over; and when
        #    Linear makes no new session for the hand-over, nothing was retried, and it says
        #    so, with no comment.
        def case_at_the_yes():
            c, s, t = asked("ho-started", "retry PROD-6", lambda t_: held(t_, sessions=lost),
                            at=late)
            t.sessions["uuid-6"][0]["status"] = "active"
            started = answered(c, s, t, late + 60)
            c2, s2, t2 = asked("ho-no-new", "retry PROD-6", lambda t_: held(t_, sessions=lost),
                               at=late)
            unmade = answered(c2, s2, t2, late + 60)
            return (started, t.delegations, unmade, t2.delegations, t2.comments,
                    statuses(c2))
        check("ho-retry-at-the-yes-reads-again-and-says-when-linear-made-no-new-hand-over",
              outcome(case_at_the_yes),
              ("A dev is working on PROD-6 now, so I won't hand it over again. Nothing was "
               "handed over.", [],
               "I handed PROD-6 to the agent again, but Linear made no new hand-over for it, "
               "so nothing was retried. In Linear, take the agent off it and put it back.",
               [("uuid-6", "agent-1")], [], [("PROD-6", "retry", REFUSED)]))

        # 4. START: a ready ticket nobody holds gets one question, and the yes hands it over
        #    with the comment; one that fails the readiness gate is refused, naming the rules
        #    in plain words; a held one points at `retry`; a plan at `approve`.
        def case_started():
            c, s, t = asked("ho-start", "please start PROD-6", readied)
            question = dm_lines(s)[-1]
            said = answered(c, s, t, now + 120)
            return (question.startswith("*Start this?* PROD-6"),
                    "and I'll hand it to the agent to start" in question, t.delegations, said,
                    "Handed to the agent by the Slack bridge" in (t.comments or [("", "")])[0][1])
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
        rows = (
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
