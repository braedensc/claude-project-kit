#!/usr/bin/env python3
"""The Slack bridge's re-plan (KIT-244): `replan <ticket id>`, from the request to the end.

    python3 scripts/pipeline_bridge_replan.py --selftest

A MODULE OF ITS OWN (KIT-312, part 2: KIT-407). scripts/pipeline_bridge.py was one file
of more than 12,000 lines, so Stage E withheld it from every review it was in, and every
bridge feature conflicted with every other. It is split by concern, with no change in
behaviour: each function, constant and method here is main's own text, moved unchanged.
pipeline_bridge imports each one back, so `pipeline_bridge.X` is still the very same
object. Its cases sit below its selftest marker, moved from the bridge's (KIT-312, part
3), and the bridge's selftest runs them too, most of them end to end through one pass.
Its own selftest runs them as well, and pins that it holds no HTTP call and no GraphQL
write of its own, so Slack and the tracker stay behind the bridge's two clients.

WHAT IS HERE
  The request    asked only for a fresh idea in Plan it whose planning runs have all
                 ended, none of which filed a plan that still stands.
  The action     a yes moves the idea out to its team's first backlog state and straight
                 back in, as the owner, so the planner starts a new run; then the comment
                 naming who said yes, and the idea's history read the planner's own way.
  Recovery       a re-plan a pass left midway is finished by the next pass, and one that
                 cannot be is said.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pipeline_notify_local as notify  # noqa: E402
import pipeline_plan_poller as poller  # noqa: E402
from pipeline_bridge_transport import BridgeError  # noqa: E402
from pipeline_bridge_config import (ASK_AGAIN_BIND, DECLINED, DONE,  # noqa: E402
                                    EXIT_USAGE, EXPIRED, OPEN, REFUSED, epoch, iso,
                                    run_idea, ticket_number, ts_float)
from pipeline_bridge_requests import (ALREADY_ASKED_BIND, newest_run,  # noqa: E402
                                      question_parts, ticket_fingerprint)

# A re-plan (KIT-244): a planning ticket in one of these state types has ended, and a move
# into Plan it this recent with no run yet may be the one the planner is starting on now.
REPLAN_RUN_ENDED = ("completed", "canceled")
REPLAN_SETTLE_SECONDS = 15 * 60
# Said after a re-plan's outcome while its attribution comment is still owed (KIT-244).
REPLAN_COMMENT_OWED = (" The idea was moved, but the comment naming who approved it could not "
                       "be written; I will try again.")
# The bind sentences of a re-plan (KIT-342), pinned word for word by the bridge's selftest.
PLAN_IT_BIND = "To plan it, type `plan %s`."
PLAN_AGAIN_BIND = "To plan it again, type `plan %s`."
SETTLE_BIND = "Give it %d minutes, then ask again."
RUN_OPEN_BIND = "Ask again once that run has finished."
PLAN_FILED_BIND = "Approve that plan, or cancel it before you plan the idea again."
REPLAN_ACT_OFF_BIND = ("Switch acting on and reply `yes %s` to finish it, or reply `no %s` to "
                       "leave it there.")
REPLAN_LEAVE_BIND = "Reply `no %s` to leave it there; then, to plan it, type `plan %s`."
REPLAN_FIX_BIND = "Fix that, then reply `yes %s`, or reply `no %s` to leave it there."
REPLAN_FINISH_BIND = "To finish, type `plan %s`."
REPLAN_LOOK_BIND = "Look at %s in Linear. If it isn't back with the planner, type `plan %s`."
REPLAN_BY_HAND_BIND = ("If no planning run starts within ten minutes, move %s out of planning "
                       "in Linear and back in by hand.")


def history_epoch(text):
    """A tracker time (`2026-11-02T00:03:22.164Z`) as seconds, or None."""
    return epoch((text or "")[:19] + "Z") if text else None


def replan_yes(q, msg):
    """(who said yes, when): the yes that began a re-plan, as recorded when it began, so a
    later pass that finishes it names that person, whichever message brings it back."""
    rec = q.get("replanning") or {}
    return rec.get("by") or msg["user"], rec.get("said_at") or msg.get("ts")


class ReplanMixin(object):
    """Pass's re-plan, from the request to the end (KIT-312)."""

    def request_replan(self, channel, msg, thread, requester, ident, by_bot=False, dm=False):
        """A `replan` request (KIT-244): an idea in Plan it whose planning ended without a
        plan that stands. A planning ticket of the planner's is read as its idea, and the
        question names the idea."""
        issue = self.tracker.issue(ident)
        if not issue or not issue.get("identifier"):
            self.refuse_request(channel, msg, thread, "I can't find %s in Linear."
                                % ident, "not found", ticket=ident)
            return
        idea, named = run_idea(issue, self.cfg["owner_user_id"]), ""
        if idea:
            named = issue["identifier"]
            issue = self.tracker.issue(idea)
            if not issue or not issue.get("identifier"):
                self.refuse_request(channel, msg, thread, "%s is a planning run for %s, and "
                                    "I can't find %s in Linear." % (named, idea, idea),
                                    "not found", ticket=named)
                return
            if any(q.get("status") == OPEN and q.get("ticket") == issue["identifier"]
                   for q in self.state["questions"].values()):
                self.refuse_request(channel, msg, thread, "%s already has an open question."
                                    % issue["identifier"], "already asked",
                                    bind=ALREADY_ASKED_BIND, ticket=issue["identifier"])
                return
        team = (issue.get("team") or {}).get("key")
        if team not in self.cfg["team_keys"]:
            self.refuse_request(channel, msg, thread, "%s is on %s, and I plan only on %s."
                                % (issue["identifier"], team or "no team",
                                   ", ".join(self.cfg["team_keys"])),
                                "team not planned", ticket=issue["identifier"])
            return
        refusal, runs, newest = self.replan_refusal(issue, team)
        if refusal:
            text, bind, why = refusal
            self.refuse_request(channel, msg, thread, text, why, bind=bind,
                                ticket=issue["identifier"])
            return
        self.ask(channel, msg, thread, "replan", issue, requester,
                 question_parts(issue, requester, self.cfg, self.now, by_bot=by_bot, dm=dm,
                                runs=runs, via=named),
                 extra={"last_run": newest_run(runs), "newest_move": newest})

    def replan_refusal(self, issue, team):
        """((text, bind, why) or None, the idea's planning runs, the id of its newest move into
        Plan it or None): why this idea may not be planned again now (KIT-244). Read before
        asking and again before moving, since a run may have started, or a plan been filed,
        in between. Each reason is the planner's or the executor's own record, never a guess.

        A run and a plan count only when the owner's key filed them, as the planner and the
        executor do: a session can write a ticket that looks like either."""
        ident, plan_it = issue["identifier"], self.cfg["plan_it_state"]
        if (issue.get("state") or {}).get("name") != plan_it:
            return ("%s isn't in %s, so there is no planning to repeat." % (ident, plan_it),
                    PLAN_IT_BIND % ident, "not in plan it"), [], None
        refusal, runs = self.replan_work_refusal(issue, team)
        if refusal:
            return refusal, runs, None
        state_id, why = self.tracker.team_state(team, plan_it)
        if not state_id:
            return ("I can't plan %s again: %s." % (ident, why), "", "no state"), runs, None
        newest = self.tracker.plan_trigger(issue["id"], state_id)
        if newest and not any(poller.TRIGGER_PREFIX + newest["id"] in (r.get("description")
                                                                         or "") for r in runs):
            moved = history_epoch(newest.get("createdAt"))
            if moved is None or self.now - moved < REPLAN_SETTLE_SECONDS:
                # The planner may be starting on that move right now: a second move would
                # give the idea two runs at once. Older than this, the planner refused it or
                # is held (a cap, a stop), and the planner only ever acts on the newest move.
                return ("%s was moved to %s moments ago, and the planner has not started on "
                        "it yet." % (ident, plan_it),
                        SETTLE_BIND % (REPLAN_SETTLE_SECONDS // 60),
                        "planner not started"), runs, None
        return None, runs, (newest or {}).get("id")

    def replan_work_refusal(self, issue, team):
        """((text, bind, why) or None, the idea's planning runs): live work, a planning run
        still going, or a plan that stands. Read before a fresh re-plan moves anything, and
        again before a re-plan under way moves its idea back into Plan it, since any of them
        may have appeared since its yes (KIT-358)."""
        ident, owner = issue["identifier"], self.cfg["owner_user_id"]
        live = self.tracker.live_work(issue["id"])
        if live:
            return ("%s looks like live work, so I won't plan it again: %s."
                    % (ident, "; ".join(live)), "", "live work"), []
        runs = self.tracker.planning_runs(team, ident, owner)
        going = sorted((r["identifier"] for r in runs if ((r.get("state") or {}).get("type"))
                        not in REPLAN_RUN_ENDED), key=ticket_number)
        if going:
            return ("%s is still being planned, in %s." % (ident, ", ".join(going)),
                    RUN_OPEN_BIND, "run open"), runs
        names = [r["identifier"] for r in runs]
        standing = sorted((p for p in self.tracker.filed_plans(team, names, owner)
                           if ((p.get("state") or {}).get("type")) != "canceled"),
                          key=lambda p: ticket_number(p.get("identifier")))
        if standing:
            return ("%s already has a plan: %s." % (ident, ", ".join(
                "%s (%s)" % (p.get("identifier") or "?", (p.get("state") or {}).get("name")
                             or "?") for p in standing)),
                    PLAN_FILED_BIND, "plan filed"), runs
        return None, runs

    def settle_replan(self, q):
        """True when a re-plan under way may have its idea outside Plan it: its first move
        was recorded, or the tracker shows the idea where that move sent it (the move landed
        and its record was cut, so the record is healed). False when the first move never
        landed: the record is dropped, and the question is an ordinary one again, judged
        like any other (KIT-358). A tracker that can't be read raises, so the caller says
        so rather than guess."""
        rec = q.get("replanning") or {}
        if rec.get("parked"):
            return True
        state = ((self.tracker.issue(q["ticket"]) or {}).get("state") or {})
        if state.get("id") == rec.get("park"):
            rec["parked"] = True
            return True
        if state.get("name") == self.cfg["plan_it_state"]:
            q.pop("replanning", None)
            return False
        # Moved on, or gone: whether the first move landed can't be told, so the re-plan
        # stays, and what comes next says where the idea is (KIT-358 review).
        return True

    def explain_replan_move(self, issue, q):
        """The comment naming who said yes, for a re-plan whose first move landed while its
        reply was lost, so the comment was never written (KIT-358 review). Written as soon
        as the bridge learns the move landed, or owed like any attribution. Its words claim
        no move back: that may never come. True when the record changed; the caller saves
        it at once, so a pass cut later never writes it twice."""
        rec = q.get("replanning") or {}
        if (not rec.get("parked") or rec.get("commented") or not (issue or {}).get("id")
                or self.dry):
            return False
        by = rec.get("by")
        name = self.name_of(by)
        who = "**%s** (Slack user %s)" % (name, by) if name != by else "Slack user %s" % by
        body = ("The Slack bridge moved this idea out of %s to %s for %s, who said yes in "
                "Slack at %s, to plan it again."
                % (self.cfg["plan_it_state"], rec.get("park_name") or "the backlog", who,
                   iso(ts_float(rec.get("said_at")))))
        try:
            self.tracker.comment(issue["id"], body)
        except BridgeError as exc:
            self.say("  NOTE: %s was moved, but its attribution comment failed: %s"
                     % (q["ticket"], exc))
            self.owe(issue["id"], q["ticket"], body)
        rec["commented"] = True
        return True

    def stranded_line(self, q):
        """What every pass says of a re-plan left unfinished: what is true, and the answer
        that can end it now (KIT-358 review). With acting off, or the settings changed since
        it began, no yes can finish it, so only a `no` is offered."""
        ticket = q["ticket"]
        head = ("%s: a re-plan began for a yes and has not finished, and the idea may be "
                "waiting in %s." % (ticket, q["replanning"].get("park_name") or "the backlog"))
        if not self.cfg["act"]:
            return head + (" Acting is switched off, so reply `no %s` where it was asked to "
                           "leave it there, or switch acting on and reply `yes %s`"
                           % (ticket, ticket))
        if q.get("promised") != self.promised(q["kind"], q.get("team")):
            return head + (" My settings changed since it began, so reply `no %s` where it "
                           "was asked to leave it there; then, to plan it, type `plan %s`"
                           % (ticket, ticket))
        if q["replanning"].get("blocked"):
            return head + (" I can't move it back into %s: %s. Fix that, then reply `yes %s` "
                           "where it was asked, or `no %s` to leave it there"
                           % (self.cfg["plan_it_state"], q["replanning"]["blocked"], ticket,
                              ticket))
        return head + (" Reply `yes %s` where it was asked to finish it, or `no %s` to leave "
                       "it there" % (ticket, ticket))

    def answer_replan_under_way(self, channel, thread, q, msg, is_yes, hint, where, word):
        """An answer to a re-plan a yes began and a pass left unfinished, its idea maybe
        waiting outside Plan it (KIT-244). The yes that began it, read again, finishes it:
        it was judged when it began. Any other message is judged now, as an answer to any
        question is (KIT-358). A refusal leaves the re-plan open and loud, never closed. A
        `no` never moves the idea, and acting switched off moves nothing."""
        rec = q["replanning"]
        began_it = (msg.get("user") == rec.get("by")
                    and msg.get("ts") == rec.get("said_at"))
        if not began_it:
            if not hint and self.refused_foreign(channel, thread, q, msg, where, word):
                return
            if self.refused_sender(channel, thread, q, msg):
                return
        park_name = rec.get("park_name") or "the backlog"
        plan_it_name = self.cfg["plan_it_state"]
        issue = self.tracker.issue(q["ticket"]) or {}
        if self.explain_replan_move(issue, q):
            self.save()
        if not is_yes:
            # Never a move: a `no` leaves the idea where it is now, and says where.
            where_now = issue.get("state") or {}
            if where_now.get("id") == rec.get("park"):
                text = ("OK, I won't move %s back into %s. It stays in %s, where this re-plan "
                        "put it, so no planning run starts."
                        % (q["ticket"], plan_it_name, park_name))
                bind = PLAN_AGAIN_BIND % q["ticket"]
            else:
                text = "OK, I won't do anything more on %s. It's in %s now." % (
                    q["ticket"], notify.safe_title(where_now.get("name") or "another state"))
                bind = ""
            self.post(channel, thread, text, kind="declined", bind=bind, join=" ",
                      facts={"ticket": q["ticket"]})
            q.update(status=DECLINED, answered_by=msg["user"], answered_ts=msg.get("ts"))
            self.mark(channel, msg, "declined: %s mid re-plan" % q["ticket"])
            self.counts["declined"] += 1
            return
        # A yes: act_replan decides on its own read of the idea, the read any move would
        # follow, so acting off or changed settings are judged against where the idea is
        # then, never against this earlier read (KIT-358 review, round 3).
        self.act_replan(channel, thread, q, msg)

    def replan_waits(self, channel, thread, q, msg):
        """A yes to a re-plan whose idea waits in the park state, while acting is off or the
        settings changed since it began: nothing moves, and the re-plan stays open, so
        every pass says it until a yes with acting on finishes it, or a `no` ends it. A move
        by hand is never offered: a later yes would read it as the bridge's own. True when
        it refused."""
        if not self.cfg["act"]:
            why = "acting is switched off here"
            bind = REPLAN_ACT_OFF_BIND % (q["ticket"], q["ticket"])
        elif q.get("promised") != self.promised(q["kind"], q.get("team")):
            why = "my settings changed since this re-plan began"
            bind = REPLAN_LEAVE_BIND % (q["ticket"], q["ticket"])
        else:
            return False
        self.post(channel, thread, "I can't finish planning %s again: %s, so I moved nothing. "
                  "It may be waiting in %s." % (q["ticket"], why,
                                                q["replanning"].get("park_name")
                                                or "the backlog"),
                  kind="replan-waits", bind=bind, join=" ", facts={"ticket": q["ticket"]})
        self.mark(channel, msg, "refused: %s, mid re-plan" % why)
        self.counts["refused"] += 1
        return True

    def act_replan(self, channel, thread, q, msg):
        """Move the idea out of Plan it to its team's first backlog state and straight back
        in, as the owner, so the planner sees a new move and starts a new run (KIT-244).

        A fresh yes is re-read and every check made again first, including what the question
        showed: the idea's newest planning run and its newest move. The re-plan is recorded as
        under way, and saved, BEFORE the first move, and the comment naming who said yes is
        written as soon as that move lands. So a pass cut anywhere after that (a deadline, a
        lost reply, a crash) is finished by the next pass from where it stopped
        (resume_replan), never judged again as a fresh question. A move back that did not
        land is said with the one line that finishes it, one whose landing can't be read is
        said as unknown, and a move the history does not show is said; each keeps that pass
        loud (exit 1)."""
        def finish(status, text, what, kind="replan-refused", bind=""):
            # Posted first: the status changes only after the reply saying so lands.
            self.post(channel, thread, text, kind=kind, bind=bind, join=" ",
                      facts={"ticket": q["ticket"]})
            by, said_at = replan_yes(q, msg)
            q.update(status=status, answered_by=by, answered_ts=said_at)
            self.mark(channel, msg, what)
        under_way = q.get("replanning")
        # A re-plan already under way is the same action, counted when it began.
        if not under_way and self.refused_over_limit(channel, thread, msg):
            return
        plan_it_name = self.cfg["plan_it_state"]
        issue = self.tracker.issue(q["ticket"])
        # A re-plan under way that ends here may leave its idea in the park state, and no
        # later pass names it, so that pass is kept loud, naming the ticket (KIT-365).
        park = (under_way or {}).get("park_name") or "the backlog"
        if not issue or not issue.get("id"):
            finish(REFUSED, "I can't find %s in Linear any more, so nothing %s moved."
                   % (q["ticket"], "more was" if under_way else "was"), "refused: gone")
            if under_way:
                self.replan_errors.append(
                    "%s: a re-plan under way ended, since the idea can't be found in the "
                    "tracker any more. If it still exists, it may be waiting in %s"
                    % (q["ticket"], park))
            return
        team = (issue.get("team") or {}).get("key")
        if team not in self.cfg["team_keys"]:
            finish(REFUSED, "%s is now on %s, which I don't plan on, so nothing %s moved."
                   % (q["ticket"], team or "no team",
                                  "more was" if under_way else "was"), "refused: team")
            if under_way:
                self.replan_errors.append(
                    "%s: a re-plan under way ended, since the idea is now on %s, which the "
                    "bridge does not plan on. It may be waiting in %s"
                    % (q["ticket"], team or "no team", park))
            return
        plan_it, why = self.tracker.team_state(team, plan_it_name)
        parked_now = (issue.get("state") or {}).get("id") == (under_way or {}).get("park")
        if under_way and parked_now and self.replan_waits(channel, thread, q, msg):
            # Judged on this read, the one the move back would follow (KIT-358 review).
            return
        if not plan_it and under_way and parked_now:
            # Its idea waits outside Plan it: said, and kept open and loud, never closed
            # quietly. The pass's line names the cause until it is fixed (KIT-358).
            under_way["blocked"] = why
            self.save()
            self.post(channel, thread, "I can't move %s back into %s: %s. It may be waiting in "
                      "%s." % (q["ticket"], plan_it_name, why,
                               under_way.get("park_name") or "the backlog"),
                      kind="replan-waits", bind=REPLAN_FIX_BIND % (q["ticket"], q["ticket"]),
                      join=" ",
                      facts={"ticket": q["ticket"]})
            self.mark(channel, msg, "refused: no state, mid re-plan")
            self.counts["refused"] += 1
            return
        if not plan_it and not under_way:
            finish(REFUSED, "I can't move %s: %s." % (q["ticket"], why), "refused: no state")
            return
        if under_way:
            # Its idea elsewhere needs no Plan it state: resume_replan leaves it where it is.
            under_way.pop("blocked", None)
            self.resume_replan(issue, team, plan_it, q, msg, finish)
            return
        if ticket_fingerprint(issue) != q.get("fingerprint"):
            finish(REFUSED, "%s changed since I asked (its title, text, team, labels or "
                   "state), so nothing was moved, and I have closed the question." % q["ticket"],
                   "refused: changed since asked", bind=ASK_AGAIN_BIND % ("replan", q["ticket"]))
            return
        refusal, runs, newest = self.replan_refusal(issue, team)
        if refusal:
            text, bind, why = refusal
            finish(REFUSED, "%s Nothing was moved." % text, "refused: " + why, bind=bind)
            return
        if (newest_run(runs), newest) != (q.get("last_run"), q.get("newest_move")):
            # The question named the idea's newest run, and stood on its newest move. A run
            # or a move since then is a new situation, so this yes moves nothing.
            finish(REFUSED, "%s's planning changed since I asked (a new run or a new move), so "
                   "nothing was moved, and I have closed the question." % q["ticket"],
                   "refused: planning changed since asked",
                   bind=ASK_AGAIN_BIND % ("replan", q["ticket"]))
            return
        park, park_name, why = self.tracker.backlog_state(team)
        if not park:
            finish(REFUSED, "I can't plan %s again: %s, so it has nowhere to wait for a "
                   "moment. Nothing was moved." % (q["ticket"], why), "refused: no backlog")
            return
        if self.dry:
            self.say("  [dry-run] would move %s to %s and back to %s"
                     % (q["ticket"], park_name, plan_it_name))
            return
        # Recorded, and saved, before anything moves: from here on a retried yes resumes.
        q["replanning"] = {"park": park, "park_name": park_name, "before": newest,
                           "parked": False, "commented": False, "at": self.now,
                           "by": msg["user"], "said_at": msg.get("ts")}
        self.save()
        self.replan_moves(issue, plan_it, q, msg, finish)

    def replan_moves(self, issue, plan_it, q, msg, finish):
        """Both moves: out to the backlog, the comment, then back into Plan it (KIT-244)."""
        rec = q["replanning"]
        self.tracker.move(issue["id"], rec["park"])
        self.state["acted_at"].append(self.now)
        rec["parked"] = True
        self.save()
        owed = self.replan_attribution(issue, q, msg)
        self.replan_back(issue, plan_it, q, msg, finish, owed)

    def replan_attribution(self, issue, q, msg):
        """The comment naming who said yes, written as soon as the idea leaves Plan it, so
        the owner's moves are never unexplained, and owed when it fails (KIT-244). Its
        words say what this yes does, so they hold however the move back turns out."""
        rec = q["replanning"]
        if rec.get("commented"):
            # Written earlier, or owed: say so while it is still owed (KIT-358 review), since
            # explain_replan_move may have owed it before this pass got here.
            return REPLAN_COMMENT_OWED if any(
                o.get("ticket") == q["ticket"] for o in self.state["owed"]) else ""
        by, said_at = replan_yes(q, msg)
        body = ("The Slack bridge is planning this idea again for **%s** (Slack user %s), "
                "who said yes in Slack at %s. It moves the idea out of %s to %s and straight "
                "back in, so the planner starts a new planning run."
                % (self.name_of(by), by, iso(ts_float(said_at)),
                   self.cfg["plan_it_state"], rec.get("park_name") or "the backlog"))
        owed = ""
        try:
            self.tracker.comment(issue["id"], body)
        except BridgeError as exc:
            # The move stands: it cannot be taken back. The record of who said yes is owed.
            self.say("  NOTE: %s was moved, but its attribution comment failed: %s"
                     % (q["ticket"], exc))
            self.owe(issue["id"], q["ticket"], body)
            owed = REPLAN_COMMENT_OWED
        rec["commented"] = True
        self.save()
        return owed

    def replan_back(self, issue, plan_it, q, msg, finish, owed):
        """The move back into Plan it, then the history check. A move back whose reply
        failed is re-read before anything is said, since it may have landed (KIT-244)."""
        rec, plan_it_name = q["replanning"], self.cfg["plan_it_state"]
        park_name = rec.get("park_name") or "the backlog"
        try:
            self.tracker.move(issue["id"], plan_it)
        except BridgeError as exc:
            try:
                now_in = ((self.tracker.issue(q["ticket"]) or {}).get("state") or {}).get("id")
            except BridgeError:
                now_in = None
            if now_in == rec["park"]:
                # Parked and not back: say where it waits, and the one line that finishes
                # the move, since `plan` asks about an idea outside Plan it.
                self.replan_errors.append("%s was left in %s: %s" % (q["ticket"], park_name,
                                                                     exc))
                finish(DONE, "I moved %s to %s, but moving it back into %s failed, so no "
                       "planning run starts.%s" % (q["ticket"], park_name, plan_it_name, owed),
                       "partial: left %s in %s" % (q["ticket"], park_name),
                       kind="replan-partial", bind=REPLAN_FINISH_BIND % q["ticket"])
                return
            if now_in != plan_it:
                self.replan_errors.append("%s: the move back may not have landed: %s"
                                          % (q["ticket"], exc))
                finish(DONE, "I moved %s to %s, and I can't tell whether the move back into %s "
                       "landed.%s" % (q["ticket"], park_name, plan_it_name, owed),
                       "partial: %s unknown" % q["ticket"], kind="replan-partial",
                       bind=REPLAN_LOOK_BIND % (q["ticket"], q["ticket"]))
                return
            # It landed, and only the reply was lost: the history says the rest.
        self.replan_verify(issue, plan_it, q, msg, finish, owed)

    def replan_verify(self, issue, plan_it, q, msg, finish, owed, acted=True):
        """The planner starts a run only on a move its own reading of the history shows: a
        new move into Plan it, by the owner, since the one the question stood on. `acted` is
        whether this pass made the move back: a pass that only reads it counts no action
        (KIT-358 review, round 3)."""
        rec, plan_it_name = q["replanning"], self.cfg["plan_it_state"]
        by = replan_yes(q, msg)[0]
        unseen = ""
        try:
            after = self.tracker.plan_trigger(issue["id"], plan_it)
        except BridgeError as exc:
            after, unseen = None, "I could not read its history to check (%s)" % exc
        if not unseen and (not after or after.get("id") == rec.get("before")
                           or after.get("actorId") != self.cfg["owner_user_id"]):
            unseen = "its history does not show the move back in"
        if unseen:
            self.replan_errors.append("%s: %s" % (q["ticket"], unseen))
            finish(DONE, "I moved %s out of %s and back in for <@%s>, but %s, so the planner "
                   "may not start.%s" % (q["ticket"], plan_it_name, by, unseen, owed),
                   "done: moved %s, unconfirmed" % q["ticket"], kind="replan-unconfirmed",
                   bind=REPLAN_BY_HAND_BIND % q["ticket"])
        else:
            finish(DONE, "Moved %s out of %s and back in for <@%s>. The planner starts a new "
                   "run within a few minutes.%s" % (q["ticket"], plan_it_name, by, owed),
                   "done: replanned %s" % q["ticket"], kind="replan-moved")
        if acted:
            self.counts["acted"] = self.counts.get("acted", 0) + 1
            self.say("  acted: moved %s out of %s and back in for %s"
                     % (q["ticket"], plan_it_name, by))

    def replan_changed(self, issue, plan_it, q, runs):
        """(text, bind, why) or None: what the question showed, changed since it was asked,
        checked before a re-plan under way moves its idea back (KIT-358 review): the idea's
        title, text, team and labels, read as if it were still in Plan it, since this
        re-plan moved it out; its newest planning run; and a move into Plan it since. A
        question stood on its creation when the idea was made in Plan it, and after this
        re-plan's own move out the history names no such trigger, which is no new move."""
        if ticket_fingerprint(dict(issue, state={"id": plan_it})) != q.get("fingerprint"):
            return ("%s changed since I asked (its title, text, team or labels)."
                    % q["ticket"], "", "changed since asked")
        newest = self.tracker.plan_trigger(issue["id"], plan_it)
        moved_in = bool(newest and not newest.get("creation")
                        and newest.get("id") != q.get("newest_move"))
        if moved_in or newest_run(runs) != q.get("last_run"):
            return ("%s's planning changed since I asked (a new run or a new move)."
                    % q["ticket"], "", "planning changed since asked")
        return None

    def resume_replan(self, issue, team, plan_it, q, msg, finish):
        """Finish a re-plan an earlier pass began and did not end (KIT-244). Its first move
        landed (settle_replan), so where the idea is now says where that pass stopped. Before
        the idea goes back into Plan it, the checks a fresh yes gets on the idea's work run
        again: a run or a plan since the yes would make a second (KIT-358)."""
        rec = q["replanning"]
        where = (issue.get("state") or {})
        if where.get("id") not in (rec.get("park"), plan_it):
            finish(DONE, "I began planning %s again for this yes, and it's in %s now, so I've "
                   "left it there." % (q["ticket"], notify.safe_title(where.get("name")
                                                                       or "another state")),
                   "done: %s moved elsewhere mid re-plan" % q["ticket"], kind="replan-partial",
                   bind=PLAN_IT_BIND % q["ticket"])
            return
        if where.get("id") == plan_it and not rec.get("parked"):
            # Not reached through answer(), which drops a record whose first move never
            # landed: never two moves on a yes that no check has judged now (KIT-358).
            q.pop("replanning", None)
            finish(REFUSED, "Nothing has moved %s yet, so I've closed this re-plan."
                   % q["ticket"], "refused: nothing under way",
                   bind=ASK_AGAIN_BIND % ("replan", q["ticket"]))
            return
        if where.get("id") == rec.get("park"):
            refusal, runs = self.replan_work_refusal(issue, team)
            if not refusal:
                refusal = self.replan_changed(issue, plan_it, q, runs)
            if refusal:
                text, bind, why = refusal
                finish(REFUSED, "%s So I've left %s in %s, where this re-plan put it." % (
                    text, q["ticket"], rec.get("park_name") or "the backlog"),
                    "refused mid re-plan: " + why,
                    bind=bind or PLAN_AGAIN_BIND % q["ticket"])
                return
        if self.dry:
            self.say("  [dry-run] would finish planning %s again" % q["ticket"])
            return
        owed = self.replan_attribution(issue, q, msg)
        if where.get("id") == rec.get("park"):
            self.replan_back(issue, plan_it, q, msg, finish, owed)
        else:
            # Both moves landed in an earlier pass: this one only reads the history.
            self.replan_verify(issue, plan_it, q, msg, finish, owed, acted=False)

    def replan_may_wait(self, q):
        """settle_replan for the end of a pass: a tracker that can't be read counts as the
        idea maybe waiting outside Plan it, which keeps the re-plan and the pass loud."""
        try:
            return self.settle_replan(q)
        except BridgeError as exc:
            self.say("  could not read %s to settle its re-plan: %s" % (q.get("ticket"), exc))
            return True

    def stranded_replans(self):
        """Re-plans begun for a yes and not finished by the end of this pass: their yes
        was cut short, deleted, or is past the window read (KIT-244 review). The idea may
        wait outside Plan it, so each keeps the pass loud until a yes finishes it or a `no`
        ends it (KIT-358). One whose first move never landed waits for nothing. One whose
        idea is back in Plan it, moved on or gone has nothing left to do, and is ended,
        said once. One asked where this bridge no longer reads can't be answered there, so
        it is ended, and said in Slack where the notifier watch speaks, for a lasting
        trace (KIT-358 review)."""
        changed = False
        for q in self.state["questions"].values():
            if not (q.get("kind") == "replan" and q.get("replanning")
                    and q.get("status") == OPEN):
                continue
            had = dict(q["replanning"])
            if not self.replan_may_wait(q):
                changed = True
                continue
            changed = changed or q.get("replanning") != had
            ticket, park = q["ticket"], q["replanning"].get("park_name") or "the backlog"
            try:
                issue = self.tracker.issue(ticket) or {}
            except BridgeError as exc:
                self.replan_errors.append(
                    "%s: a re-plan began for a yes and has not finished, and I could not read "
                    "where the idea is (%s)" % (ticket, exc))
                continue
            parked = (issue.get("state") or {}).get("id") == q["replanning"].get("park")
            if self.dry:
                # A rehearsal says what this pass would do, and does none of it.
                self.replan_errors.append(
                    "%s: [dry-run] would close the re-plan, since its idea has left %s"
                    % (ticket, park) if not parked else
                    "%s: [dry-run] would close the re-plan and say so in Slack, since it was "
                    "asked where this bridge no longer reads" % ticket
                    if not self.watched(q.get("channel")) else self.stranded_line(q))
                continue
            # Each change is saved as it is made: a pass cut later in this loop must never
            # write the comment, or post the note, twice (KIT-358 review).
            if self.explain_replan_move(issue, q):
                self.save()
            if not parked:
                try:
                    self.end_stranded_replan(q, issue)
                except BridgeError as exc:
                    self.replan_errors.append(
                        "%s: a re-plan began for a yes, its idea has left %s, and I could not "
                        "close it (%s)" % (ticket, park, exc))
                    continue
                self.save()
                continue
            if q["replanning"].get("blocked"):
                # Named until it is fixed: read again, so a fix ends the line (KIT-358 review).
                team = (issue.get("team") or {}).get("key")
                try:
                    found = team and self.tracker.team_state(team, self.cfg["plan_it_state"])[0]
                except BridgeError:
                    found = None
                if found:
                    q["replanning"].pop("blocked", None)
                    self.save()
            if not self.watched(q.get("channel")):
                # Said once in each place the notifier watch speaks, each recorded as it is
                # said. Closed once any place has heard it; a place that refuses is named.
                told = q["replanning"].setdefault("told", [])
                refused = []
                for channel in self.watch_destinations():
                    if channel in told:
                        continue
                    try:
                        self.post(channel, None, "I closed the re-plan of %s: it was asked "
                                  "where I no longer read, so nobody can answer it there. It "
                                  "may be waiting in %s." % (ticket, park), kind="replan-closed",
                                  bind=PLAN_IT_BIND % ticket, join=" ",
                                  facts={"ticket": ticket})
                    except BridgeError as exc:
                        refused.append("%s (%s)" % (channel, exc))
                        continue
                    told.append(channel)
                    self.save()
                if not told:
                    self.replan_errors.append(
                        "%s: a re-plan was asked where this bridge no longer reads, and I could "
                        "not say so in Slack: %s" % (ticket, "; ".join(refused)))
                    continue
                q["status"] = EXPIRED
                self.save()
                self.replan_errors.append(
                    "%s: a re-plan began for a yes and can't be finished: it was asked where "
                    "this bridge no longer reads, so I have closed it and said so in Slack%s. "
                    "The idea may be waiting in %s. To plan it, type `plan %s`"
                    % (ticket, " (not in %s)" % "; ".join(refused) if refused else "", park,
                       ticket))
                continue
            self.replan_errors.append(self.stranded_line(q))
        if changed:
            self.save()

    def end_stranded_replan(self, q, issue):
        """End a re-plan whose idea is no longer where it moved it (KIT-358 review), saying
        so where it was asked, or where the notifier watch speaks when that is no longer
        read. Back in Plan it, the history is read the planner's way, as the yes that
        finishes it would read it: the bridge's own move back, its pass cut, and a person's
        move by hand look alike, and either starts a run only if the history shows it.
        Moved on, or gone, the idea is left where it is."""
        rec, ticket = q["replanning"], q["ticket"]
        if self.watched(q.get("channel")):
            places = [(q["channel"], q.get("thread"))]
        else:
            # Each place the notifier watch speaks, in turn, until one takes it.
            places = [(channel, None) for channel in self.watch_destinations()]
        began = {"user": rec.get("by"), "ts": rec.get("said_at")}

        def finish(status, text, what, kind="replan-refused", bind=""):
            last = None
            for channel, thread in places:
                try:
                    self.post(channel, thread, text, kind=kind, bind=bind, join=" ",
                              facts={"ticket": ticket})
                    break
                except BridgeError as exc:
                    last = exc
            else:
                raise last or BridgeError("there is nowhere to say it")
            by, said_at = replan_yes(q, began)
            q.update(status=status, answered_by=by, answered_ts=said_at)
            self.say("  %s, mid re-plan" % what)
        if not issue.get("id"):
            finish(REFUSED, "I can't find %s in Linear any more, so I've closed this "
                   "re-plan." % ticket, "refused: gone")
            # Said where it was asked, and this pass kept loud, naming it (KIT-365).
            self.replan_errors.append(
                "%s: a re-plan began for a yes and has ended, since the idea can't be found in "
                "the tracker any more. If it still exists, it may be waiting in %s"
                % (ticket, rec.get("park_name") or "the backlog"))
            return
        team = (issue.get("team") or {}).get("key")
        plan_it = self.tracker.team_state(team, self.cfg["plan_it_state"])[0] if team else None
        state = issue.get("state") or {}
        if plan_it and state.get("id") == plan_it and rec.get("parked"):
            # This pass made no move: it only reads the history.
            self.replan_verify(issue, plan_it, q, began, finish,
                               self.replan_attribution(issue, q, began), acted=False)
            return
        finish(DONE, "%s is in %s now, so I've closed this re-plan and left it there."
               % (ticket, notify.safe_title(state.get("name") or "another state")),
               "done: %s moved elsewhere" % ticket, kind="replan-partial",
               bind=PLAN_IT_BIND % ticket)


# ══════════════════════════════════════════════════════════════════════════════════════
# Selftest — offline: this module's cases, moved here from the bridge's selftest (KIT-312),
# and its pins. The bridge's selftest runs these cases too, each in its place among the
# others, with one kit for all of them.
# ══════════════════════════════════════════════════════════════════════════════════════
def cases_replan(kit):
    """Re-plan from Slack (KIT-244): its cases from the bridge's selftest, moved
    here unchanged (KIT-312)."""
    import json
    from pipeline_bridge import (ALICE, BOB, CHATBOT, DM, Deadline, EXIT_ERROR, EXIT_OK,
                                 GUEST, ME, OWNER, run_text, state_path, voice)
    with kit as tmp:
        check, FakeWriter, now, outcome = kit.take("check", "FakeWriter", "now", "outcome")
        posted, run, status_of, world = kit.take("posted", "run", "status_of", "world")
        wrun, wworld = kit.take("wrun", "wworld")
        # 19. RE-PLAN FROM SLACK (KIT-244). `replan <id>` asks about an idea in Plan it whose
        #     planning ended without a plan that stands. A yes moves it out to the backlog
        #     and straight back in, as the owner, so the planner starts a new run, and the
        #     history is read back the planner's way. Each case runs alone.
        def rp_world(name, **over):
            c, s, t = world(name, **dict({"act": True}, **over))
            run(c, s, t)                                    # the first pass, empty
            t.in_plan_it("PROD-6", trigger="T1")
            t.add_run("PROD-6", "PROD-50", "completed", trigger="T1")
            return c, s, t

        def rp_ask(name, line="replan PROD-6", **over):
            c, s, t = rp_world(name, **over)
            root = s.add(ALICE, line)
            run(c, s, t)
            return c, s, t, root

        rp_bind = ("Reply `yes` in this thread within 24 hours, and I'll hand it back to the "
                   "planner for a new plan. Reply `no` to drop it. Merging stays yours. (Asked "
                   "for by <@%s>.)" % ALICE)

        def case_rp_asks():
            c, s, t, _root = rp_ask("rp-asks")
            qs = list(json.load(open(state_path(c)))["questions"].values())
            return (posted(s)[-1], [(q["kind"], q["ticket"], q["status"]) for q in qs],
                    qs[0]["promised"] if qs else None)
        check("replan-asks-about-an-idea-whose-planning-ended", outcome(case_rp_asks),
              ("*Plan this again?* PROD-6 “Light mode”: in Plan it on PROD, filed by Chat "
               "bot. Its last planning run, PROD-50, has ended.\n" + rp_bind,
               [("replan", "PROD-6", OPEN)],
               {"act": True, "question_ttl_hours": 24, "plan_it_state": "Plan it"}))

        def case_rp_yes():
            c, s, t, root = rp_ask("rp-yes")
            s.add(BOB, "yes", thread=root)
            code, _out = run(c, s, t)
            said = t.comments[-1][1] if t.comments else ""
            return (code, t.moves, posted(s)[-1], len(t.comments),
                    "said yes in Slack" in said and BOB in said, status_of(c),
                    len(t.triggers["uuid-6"]))
        check("replan-a-yes-moves-it-out-to-the-backlog-and-back-in, as the owner, once",
              outcome(case_rp_yes),
              (EXIT_OK, [("uuid-6", "st-backlog"), ("uuid-6", "st-plan-it")],
               "Moved PROD-6 out of Plan it and back in for <@%s>. The planner starts a new "
               "run within a few minutes." % BOB, 1, True, [DONE], 2))

        def case_rp_no():
            c, s, t, root = rp_ask("rp-no")
            s.add(BOB, "no", thread=root)
            run(c, s, t)
            return t.moves, posted(s)[-1]
        check("replan-a-no-moves-nothing", outcome(case_rp_no),
              ([], "OK, PROD-6 is not planned again."))

        def case_rp_act_off():
            c, s, t, root = rp_ask("rp-act-off", act=False)
            asked = posted(s)[-1]
            s.add(BOB, "yes", thread=root)
            run(c, s, t)
            return t.moves, "acting is switched off here" in asked, "switched off" in posted(s)[-1]
        check("replan-with-acting-off-moves-nothing", outcome(case_rp_act_off),
              ([], True, True))

        def rp_refused(name, setup, line="replan PROD-6"):
            c, s, t = rp_world(name)
            setup(t)
            s.add(ALICE, line)
            run(c, s, t)
            return posted(s)[-1], json.load(open(state_path(c)))["questions"], t.moves

        plan_70 = {"id": "uuid-70", "identifier": "PROD-70", "creator": {"id": OWNER},
                   "state": {"name": "Todo", "type": "unstarted"}}
        for name, setup, want in (
                ("not-in-plan-it", lambda t: t.set_state("PROD-6", "backlog"),
                 "PROD-6 isn't in Plan it, so there is no planning to repeat. To plan it, "
                 "type `plan PROD-6`."),
                ("a-run-still-open", lambda t: t.add_run("PROD-6", "PROD-51", "started",
                                                         trigger="T2"),
                 "PROD-6 is still being planned, in PROD-51. Ask again once that run has "
                 "finished."),
                ("a-plan-that-stands", lambda t: t.plans.__setitem__("PROD-50", [plan_70]),
                 "PROD-6 already has a plan: PROD-70 (Todo). Approve that plan, or cancel it "
                 "before you plan the idea again."),
                ("live-work", lambda t: t.live.__setitem__("uuid-6", ["it has an agent "
                                                                     "session"]),
                 "PROD-6 looks like live work, so I won't plan it again: it has an agent "
                 "session."),
                ("a-move-the-planner-has-yet-to-start-on", lambda t: t.triggers["uuid-6"].append(
                    {"id": "T9", "createdAt": "2025-10-09T08:50:00.000Z", "actorId": OWNER}),
                 "PROD-6 was moved to Plan it moments ago, and the planner has not started on "
                 "it yet. Give it 15 minutes, then ask again."),
                ("another-team", lambda t: t.issues["PROD-6"].__setitem__("team", {"key": "OPS"}),
                 "PROD-6 is on OPS, and I plan only on PROD.")):
            check("replan-refused-before-asking: " + name,
                  outcome(lambda: rp_refused("rp-" + name, setup)), (want, {}, []))

        def case_rp_old_move():
            # A move the planner never picked up, long enough ago (it refused to start, or
            # was stopped): asking is allowed, since no run can be starting on it now.
            c, s, t = rp_world("rp-old-move")
            t.triggers["uuid-6"].append({"id": "T9", "createdAt": "2025-10-09T08:30:00.000Z",
                                         "actorId": OWNER})
            s.add(ALICE, "replan PROD-6")
            run(c, s, t)
            return [q["kind"] for q in json.load(open(state_path(c)))["questions"].values()]
        check("replan-a-move-the-planner-never-picked-up-is-not-a-run-starting",
              outcome(case_rp_old_move), ["replan"])

        def case_rp_canceled_plan():
            c, s, t = rp_world("rp-canceled-plan")
            t.plans["PROD-50"] = [dict(plan_70, state={"name": "Canceled", "type": "canceled"})]
            s.add(ALICE, "replan PROD-6")
            run(c, s, t)
            return ([q["kind"] for q in json.load(open(state_path(c)))["questions"].values()],
                    ("plans", "PROD", ("PROD-50",)) in t.searches)
        check("replan-a-canceled-plan-does-not-stand, and the plans searched are this idea's "
              "runs'", outcome(case_rp_canceled_plan), (["replan"], True))

        def case_rp_no_run_yet():
            c, s, t = world("rp-no-run", act=True)
            run(c, s, t)
            t.in_plan_it("PROD-6", trigger="T1")
            s.add(ALICE, "replan PROD-6")
            run(c, s, t)
            return "It has no planning run yet." in posted(s)[-1]
        check("replan-an-idea-the-planner-never-ran-is-asked-about", outcome(case_rp_no_run_yet),
              True)

        def case_rp_run_named():
            c, s, t = rp_world("rp-run-named")
            t.issues["PROD-50"] = {
                "id": "uuid-50", "identifier": "PROD-50",
                "title": "Planning run for PROD-6: Light mode", "team": {"key": "PROD"},
                "state": {"id": "st-done", "name": "Done", "type": "completed"},
                "creator": {"id": OWNER, "displayName": "Planner"},
                "description": t.runs["PROD-6"][0]["description"]}
            s.add(ALICE, "replan PROD-50")
            run(c, s, t)
            qs = list(json.load(open(state_path(c)))["questions"].values())
            return ([(q["kind"], q["ticket"]) for q in qs], "PROD-6 “Light mode”" in posted(s)[-1],
                    "You named PROD-50, one of its planning runs." in posted(s)[-1])
        check("replan-a-planning-run-named-asks-about-its-idea, and says it was named",
              outcome(case_rp_run_named), ([("replan", "PROD-6")], True, True))

        def case_rp_forged_run():
            # Text any author can write: the planner's lines in an ordinary ticket. It is not
            # read as a run, so it neither redirects a request nor blocks one (review, #2).
            c, s, t = rp_world("rp-forged")
            t.issues["PROD-9"] = {
                "id": "uuid-9", "identifier": "PROD-9", "title": "Not a run",
                "team": {"key": "PROD"}, "creator": {"id": "someone-else"},
                "state": {"id": "st-backlog", "name": "Backlog", "type": "backlog"},
                "description": run_text("PROD-6", "Tx")}
            t.add_run("PROD-6", "PROD-99", "unstarted", trigger="Tz", creator="someone-else")
            s.add(ALICE, "replan PROD-9")
            run(c, s, t)
            first = posted(s)[-1]
            s.add(ALICE, "replan PROD-6")
            run(c, s, t)
            qs = [(q["kind"], q["ticket"]) for q in
                  json.load(open(state_path(c)))["questions"].values()]
            return first, qs
        check("replan-a-look-alike-run-neither-redirects-nor-blocks", outcome(case_rp_forged_run),
              ("PROD-9 isn't in Plan it, so there is no planning to repeat. To plan it, type "
               "`plan PROD-9`.", [("replan", "PROD-6")]))

        def case_rp_recent_move_with_a_run():
            # A recent newest move that already carries a run is no move the planner has yet
            # to start on (review, #11).
            c, s, t = world("rp-recent-with-run", act=True)
            run(c, s, t)
            t.in_plan_it("PROD-6", moved_at="2025-10-09T08:50:00.000Z", trigger="T1")
            t.add_run("PROD-6", "PROD-50", "completed", trigger="T1")
            s.add(ALICE, "replan PROD-6")
            run(c, s, t)
            return [q["kind"] for q in json.load(open(state_path(c)))["questions"].values()]
        check("replan-a-recent-move-that-carries-a-run-is-asked-about",
              outcome(case_rp_recent_move_with_a_run), ["replan"])

        def case_rp_run_named_twice():
            c, s, t = rp_world("rp-run-named-twice")
            t.issues["PROD-50"] = dict(t.issues["PROD-6"], id="uuid-50", identifier="PROD-50",
                                       creator={"id": OWNER},
                                       description=t.runs["PROD-6"][0]["description"])
            s.add(ALICE, "replan PROD-6")
            run(c, s, t)
            s.add(ALICE, "replan PROD-50")
            run(c, s, t)
            return len(json.load(open(state_path(c)))["questions"]), posted(s)[-1]
        check("replan-the-idea-already-asked-about-by-its-run-is-not-asked-twice",
              outcome(case_rp_run_named_twice),
              (1, "PROD-6 already has an open question. Answer that one."))

        tag = poller.machine.planning_tag(poller.machine.PLANNING_PREFIX + "prod")
        check("run-idea-reads-only-the-planners-own-tickets and lines, above the fence",
              [run_idea({"description": d, "creator": {"id": who}}, OWNER) for d, who in (
                  ("%s\n%sT1\n%sprod-6\n\n%s\nIdea: PROD-9\n%s" % (
                      tag, poller.TRIGGER_PREFIX, poller.IDEA_PREFIX, poller.FENCE_OPEN,
                      poller.FENCE_CLOSE), OWNER),
                  ("%s\n%sPROD-6\n" % (tag, poller.IDEA_PREFIX), OWNER),
                  ("%s\n%sT1\n%sPROD-6\n%sPROD-7\n" % (tag, poller.TRIGGER_PREFIX,
                                                       poller.IDEA_PREFIX, poller.IDEA_PREFIX),
                   OWNER),
                  ("%s\n%sT1\n%snot an id\n" % (tag, poller.TRIGGER_PREFIX, poller.IDEA_PREFIX),
                   OWNER),
                  ("%sT1\n%sPROD-6\n" % (poller.TRIGGER_PREFIX, poller.IDEA_PREFIX), OWNER),
                  (run_text("PROD-6", "T1"), "someone-else"),
                  (run_text("PROD-6", "T1"), OWNER),
                  ("", OWNER), (None, OWNER))],
              ["PROD-6", None, None, None, None, None, "PROD-6", None, None])

        def rp_after_yes(name, setup):
            c, s, t, root = rp_ask(name)
            setup(t)
            s.add(BOB, "yes", thread=root)
            code, out = run(c, s, t)
            return code, t.moves, posted(s)[-1], status_of(c), out

        def case_rp_rechecked():
            got = rp_after_yes("rp-recheck", lambda t: t.add_run("PROD-6", "PROD-51", "started",
                                                                 trigger="T2"))
            return got[:4]
        check("replan-every-check-runs-again-before-the-move: a run started since the "
              "question moves nothing", outcome(case_rp_rechecked),
              (EXIT_OK, [], "PROD-6 is still being planned, in PROD-51. Nothing was moved. Ask "
                            "again once that run has finished.", [REFUSED]))

        def case_rp_changed():
            got = rp_after_yes("rp-changed", lambda t: t.issues["PROD-6"].__setitem__(
                "title", "Light mode, changed"))
            return got[1], got[2]
        check("replan-a-ticket-changed-since-asked-moves-nothing", outcome(case_rp_changed),
              ([], "PROD-6 changed since I asked (its title, text, team, labels or state), so "
                   "nothing was moved, and I have closed the question. Ask again with "
                   "`replan PROD-6`."))

        def case_rp_no_backlog():
            got = rp_after_yes("rp-no-backlog", lambda t: setattr(t, "no_backlog", True))
            return got[1], got[2]
        check("replan-a-team-with-no-backlog-state-moves-nothing", outcome(case_rp_no_backlog),
              ([], "I can't plan PROD-6 again: PROD has no backlog state, so it has nowhere "
                   "to wait for a moment. Nothing was moved."))

        def case_rp_half():
            code, moves, last, statuses, out = rp_after_yes(
                "rp-half", lambda t: setattr(t, "move_raises", 2))
            return code, moves, last, statuses, "PROD-6 was left in Backlog" in out
        check("replan-a-failed-move-back-is-said-with-the-line-that-finishes-it, and the pass "
              "exits 1", outcome(case_rp_half),
              (EXIT_ERROR, [("uuid-6", "st-backlog")],
               "I moved PROD-6 to Backlog, but moving it back into Plan it failed, so no "
               "planning run starts. To finish, type `plan PROD-6`.", [DONE], True))

        def case_rp_unseen():
            code, moves, last, statuses, out = rp_after_yes(
                "rp-unseen", lambda t: t.history_drops.add("uuid-6"))
            return code, len(moves), last, statuses, "does not show the move back in" in out
        check("replan-a-move-the-history-does-not-show-is-said, and the pass exits 1",
              outcome(case_rp_unseen),
              (EXIT_ERROR, 2, "I moved PROD-6 out of Plan it and back in for <@%s>, but its "
               "history does not show the move back in, so the planner may not start. If no "
               "planning run starts within ten minutes, move PROD-6 out of planning in "
               "Linear and back in by hand." % BOB, [DONE], True))

        def case_rp_other_actor():
            # The newest move into Plan it is someone else's: the planner starts no run on
            # it, so the bridge's move is not confirmed.
            code, moves, last, _st, _out = rp_after_yes(
                "rp-other-actor", lambda t: setattr(t, "move_actor", "someone-else"))
            return code, len(moves), "does not show the move back in" in last
        check("replan-a-move-back-the-history-credits-to-someone-else-is-not-confirmed",
              outcome(case_rp_other_actor), (EXIT_ERROR, 2, True))

        def case_rp_history_unread():
            def setup(t):
                real = t.plan_trigger
                reads = [0]

                def flaky(issue_id, plan_it_id):
                    reads[0] += 1
                    if reads[0] >= 2:          # after the moves; the first read is at the yes
                        raise BridgeError("simulated")
                    return real(issue_id, plan_it_id)
                t.plan_trigger = flaky
            code, moves, last, _st, _out = rp_after_yes("rp-history-unread", setup)
            return code, len(moves), "I could not read its history to check" in last
        check("replan-a-history-it-could-not-read-afterwards-is-could-not-check, never done",
              outcome(case_rp_history_unread), (EXIT_ERROR, 2, True))

        # A re-plan cut midway is finished by the next pass, from where it stopped, never
        # judged again as a fresh question (review, the three-lens finding).
        def rp_cut(name, setup, after=None):
            c, s, t, root = rp_ask(name)
            setup(t)
            s.add(BOB, "yes", thread=root)
            try:
                first = run(c, s, t)[0]
            except Deadline:
                first = "cut"
            t.move_fail, t.trigger_fail, t.fail = None, None, False
            if after:
                after(t)
            code, out = run(c, s, t, at=now + 60)
            return (first, code, t.moves, len(t.comments), posted(s)[-1], status_of(c), out)

        moved_ok = ("Moved PROD-6 out of Plan it and back in for <@%s>. The planner starts a new "
                    "run within a few minutes." % BOB)
        both = [("uuid-6", "st-backlog"), ("uuid-6", "st-plan-it")]

        def case_rp_cut_between():
            got = rp_cut("rp-cut-between",
                         lambda t: setattr(t, "move_fail", (2, False, Deadline(), None)))
            return got[:6]
        check("replan-cut-between-the-moves: the next pass moves it back, once, and says so",
              outcome(case_rp_cut_between), ("cut", EXIT_OK, both, 1, moved_ok, [DONE]))

        def case_rp_cut_after_both():
            got = rp_cut("rp-cut-after", lambda t: setattr(t, "trigger_fail",
                                                          (3, Deadline())))
            return got[:6]
        check("replan-cut-after-both-moves: the next pass checks the history and moves nothing "
              "more", outcome(case_rp_cut_after_both), ("cut", EXIT_OK, both, 1, moved_ok, [DONE]))

        def case_rp_first_move_lost():
            got = rp_cut("rp-first-lost", lambda t: setattr(
                t, "move_fail", (1, False, BridgeError("simulated"), None)))
            return got[:6]
        check("replan-a-first-move-that-never-landed-is-made-by-the-next-pass",
              outcome(case_rp_first_move_lost), (EXIT_ERROR, EXIT_OK, both, 1, moved_ok, [DONE]))

        def case_rp_first_reply_lost():
            # The first move landed and only its reply was lost: the re-plan was recorded as
            # under way before it, so the next pass finds the idea parked and finishes.
            got = rp_cut("rp-first-reply-lost", lambda t: setattr(
                t, "move_fail", (1, True, BridgeError("simulated"), None)))
            return got[:6]
        check("replan-a-first-move-whose-reply-was-lost-is-finished-by-the-next-pass",
              outcome(case_rp_first_reply_lost), (EXIT_ERROR, EXIT_OK, both, 1, moved_ok, [DONE]))

        def case_rp_moved_elsewhere():
            got = rp_cut("rp-elsewhere",
                         lambda t: setattr(t, "move_fail", (2, False, Deadline(), None)),
                         after=lambda t: t.set_state("PROD-6", "unstarted"))
            return got[1:6]
        check("replan-an-idea-moved-elsewhere-midway-is-left-there-and-said",
              outcome(case_rp_moved_elsewhere),
              (EXIT_OK, [("uuid-6", "st-backlog")], 1,
               "I began planning PROD-6 again for this yes, and it's in Todo now, so I've left "
               "it there. To plan it, type `plan PROD-6`.", [DONE]))

        def case_rp_back_reply_lost():
            # The move back landed, and only its reply was lost: no false "failed".
            code, moves, last, statuses, _out = rp_after_yes(
                "rp-back-lost", lambda t: setattr(
                    t, "move_fail", (2, True, BridgeError("simulated"), None)))
            return code, moves, last, statuses
        check("replan-a-move-back-whose-reply-was-lost-is-read-again-and-confirmed",
              outcome(case_rp_back_reply_lost), (EXIT_OK, both, moved_ok, [DONE]))

        def case_rp_back_unknown():
            def setup(t):
                t.move_fail = (2, False, BridgeError("simulated"),
                               lambda: setattr(t, "fail", True))
            code, moves, last, _st, out = rp_after_yes("rp-back-unknown", setup)
            return code, moves, last, "may not have landed" in out
        check("replan-a-move-back-it-cannot-read-again-is-said-as-unknown, and the pass exits 1",
              outcome(case_rp_back_unknown),
              (EXIT_ERROR, [("uuid-6", "st-backlog")],
               "I moved PROD-6 to Backlog, and I can't tell whether the move back into Plan it "
               "landed. Look at PROD-6 in Linear. If it isn't back with the planner, type "
               "`plan PROD-6`.", True))

        # Stage E review: a re-plan under way is finished by the next pass even when a check
        # that closes or refuses an answer would now fire on its yes (the question edited,
        # the person who said yes now a guest), since that yes was judged when it began. A
        # yes the next pass cannot read keeps every pass loud, the clock never closes it,
        # and a later yes finishes it, naming who said yes first.
        def rp_cut_then(name, between, cut=(2, False, Deadline(), None)):
            c, s, t, root = rp_ask(name)
            t.move_fail = cut
            s.add(BOB, "yes", thread=root)
            try:
                run(c, s, t)
            except Deadline:
                pass
            t.move_fail = None
            between(s)
            return c, s, t, root

        def edit_the_replan_question(s):
            for m in s.messages:
                if "Plan this again?" in m.get("text", ""):
                    m["edited"] = {"user": ME, "ts": "1"}

        def bob_now_a_guest(s):
            s.profiles[BOB] = dict(s.profiles[BOB], is_restricted=True)

        def delete_bobs_yes(s):
            s.messages = [m for m in s.messages
                          if not (m.get("user") == BOB and m.get("text") == "yes")]

        for name, between in (("question-edited", edit_the_replan_question),
                              ("yes-giver-now-a-guest", bob_now_a_guest)):
            def case_rp_then(name=name, between=between):
                c, s, t, _root = rp_cut_then("rp-then-" + name, between)
                code, _out = run(c, s, t, at=now + 60)
                return code, t.moves, posted(s)[-1], status_of(c)
            check("replan-cut-then-%s: the next pass still finishes it" % name,
                  outcome(case_rp_then), (EXIT_OK, both, moved_ok, [DONE]))

        def case_rp_yes_gone():
            c, s, t, root = rp_cut_then("rp-yes-gone", delete_bobs_yes)
            first = run(c, s, t, at=now + 60)
            late = run(c, s, t, at=now + 25 * 3600)        # past the question's 24 hours
            s.add(ALICE, "yes PROD-6", thread=root, at=now + 25 * 3600 + 30)
            code, _out = run(c, s, t, at=now + 25 * 3600 + 60)
            q = list(json.load(open(state_path(c)))["questions"].values())[0]
            return (first[0], "has not finished" in first[1], "Reply `yes PROD-6`" in first[1],
                    late[0], "has not finished" in late[1], code, t.moves, posted(s)[-1],
                    q["status"], q["answered_by"])
        check("replan-its-yes-gone: every pass is loud, the clock never closes it, and a later "
              "yes finishes it, for whoever said yes first", outcome(case_rp_yes_gone),
              (EXIT_ERROR, True, True, EXIT_ERROR, True, EXIT_OK, both, moved_ok, DONE, BOB))

        def case_rp_named_on_resume():
            # Cut before the comment naming who said yes: the pass that finishes it, brought
            # back by ALICE's yes, still names BOB.
            c, s, t, root = rp_cut_then("rp-named", delete_bobs_yes,
                                        cut=(1, True, BridgeError("simulated"), None))
            s.add(ALICE, "yes PROD-6", thread=root, at=now + 30)
            code, _out = run(c, s, t, at=now + 60)
            said = t.comments[-1][1] if t.comments else ""
            return code, len(t.comments), BOB in said, ALICE in said, posted(s)[-1]
        check("replan-resumed-by-another-yes-names-who-said-yes-first",
              outcome(case_rp_named_on_resume), (EXIT_OK, 1, True, False, moved_ok))

        # KIT-358, the review of #223's last commit: only the yes that began a re-plan skips
        # the checks. A `no` never moves the idea, acting switched off moves nothing, any
        # other yes is judged now (its sender, and the work checks before the move back), a
        # re-plan whose first move never landed is an ordinary question, and one asked where
        # the bridge no longer reads is ended, loudly, once.
        parked = [("uuid-6", "st-backlog")]

        def rp_unlanded(name):
            # The first move refused, and nothing moved: the yes that began it is then gone.
            c, s, t, root = rp_ask(name)
            t.move_fail = (1, False, BridgeError("simulated"), None)
            s.add(BOB, "yes", thread=root)
            first = run(c, s, t)[0]
            t.move_fail = None
            return c, s, t, root, first

        def case_rp_act_off_midway():
            c, s, t, _root = rp_cut_then("rp-act-off-midway", lambda s: None)
            c["act"] = False
            code, _out = run(c, s, t, at=now + 60)
            return code, t.moves, "acting is switched off here" in posted(s)[-1], status_of(c)
        check("replan-under-way-with-acting-switched-off-moves-nothing-and-stays-loud",
              outcome(case_rp_act_off_midway), (EXIT_ERROR, parked, True, [OPEN]))

        def case_rp_act_off_unlanded():
            c, s, t, _root, first = rp_unlanded("rp-act-off-unlanded")
            c["act"] = False
            code, _out = run(c, s, t, at=now + 60)
            return first, code, t.moves, "acts on nothing" in posted(s)[-1], status_of(c)
        check("replan-whose-first-move-never-landed-then-acting-off: its yes acts on nothing",
              outcome(case_rp_act_off_unlanded), (EXIT_ERROR, EXIT_OK, [], True, [REFUSED]))

        def case_rp_no_midway():
            c, s, t, root = rp_cut_then("rp-no-midway", delete_bobs_yes)
            s.add(BOB, "no", thread=root, at=now + 30)
            code, _out = run(c, s, t, at=now + 60)
            again = run(c, s, t, at=now + 120)[0]
            return code, again, t.moves, posted(s)[-1], status_of(c)
        check("replan-a-no-to-a-re-plan-under-way-moves-nothing, says where it is, and ends it",
              outcome(case_rp_no_midway),
              (EXIT_OK, EXIT_OK, parked,
               "OK, I won't move PROD-6 back into Plan it. It stays in Backlog, where this "
               "re-plan put it, so no planning run starts. To plan it again, type "
               "`plan PROD-6`.", [DECLINED]))

        def case_rp_no_unlanded():
            c, s, t, root, _first = rp_unlanded("rp-no-unlanded")
            delete_bobs_yes(s)
            s.add(BOB, "no", thread=root, at=now + 30)
            code, _out = run(c, s, t, at=now + 60)
            return code, t.moves, posted(s)[-1], status_of(c)
        check("replan-a-no-after-a-first-move-that-never-landed-is-a-plain-no",
              outcome(case_rp_no_unlanded),
              (EXIT_OK, [], "OK, PROD-6 is not planned again.", [DECLINED]))

        def case_rp_bare_no_after_foreign():
            c, s, t, root = rp_cut_then("rp-foreign-no", delete_bobs_yes)
            s.add(CHATBOT, "Want me to re-run it?", thread=root, bot_id="B0CHATBOT1",
                  at=now + 20)
            s.add(BOB, "no", thread=root, at=now + 30)
            code, _out = run(c, s, t, at=now + 60)
            return (code, t.moves, "Something other than me posted" in posted(s)[-1],
                    status_of(c))
        check("replan-a-bare-no-after-a-foreign-post-is-refused, and the re-plan stays open",
              outcome(case_rp_bare_no_after_foreign), (EXIT_ERROR, parked, True, [OPEN]))

        def case_rp_bot_yes_midway():
            c, s, t, root = rp_cut_then("rp-bot-yes", delete_bobs_yes)
            s.add(CHATBOT, "yes PROD-6", thread=root, bot_id="B0CHATBOT1", at=now + 30)
            code, _out = run(c, s, t, at=now + 60)
            return (code, t.moves, "can't take that as an answer" in posted(s)[-1],
                    status_of(c))
        check("replan-a-bots-yes-to-a-re-plan-under-way-is-refused-aloud, and it stays open",
              outcome(case_rp_bot_yes_midway), (EXIT_ERROR, parked, True, [OPEN]))

        # KIT-365: a guest's yes goes through the same check as a bot's, and is pinned too.
        def case_rp_guest_yes_midway():
            c, s, t, root = rp_cut_then("rp-guest-yes", delete_bobs_yes)
            s.add(GUEST, "yes PROD-6", thread=root, at=now + 30)
            code, _out = run(c, s, t, at=now + 60)
            return (code, t.moves, "the account is a guest" in posted(s)[-1], status_of(c))
        check("replan-a-guests-yes-to-a-re-plan-under-way-is-refused-aloud, and it stays open",
              outcome(case_rp_guest_yes_midway), (EXIT_ERROR, parked, True, [OPEN]))

        # KIT-365: a re-plan under way that ends because its idea is gone, or has left the
        # teams the bridge plans on, keeps that pass loud and names the ticket: its idea may
        # wait in the backlog, and no later pass would say so.
        def rp_resume_after(name, change):
            c, s, t, _root = rp_cut_then(name, lambda s: None)
            change(t)
            code, out = run(c, s, t, at=now + 60)
            again = run(c, s, t, at=now + 120)[0]
            return (code, "PROD-6: a re-plan under way ended" in out,
                    "may be waiting in Backlog" in out, again, status_of(c))
        check("replan-resumed-whose-idea-is-gone-keeps-that-pass-loud-and-names-it",
              outcome(lambda: rp_resume_after("rp-resume-gone",
                                              lambda t: t.issues.pop("PROD-6"))),
              (EXIT_ERROR, True, True, EXIT_OK, [REFUSED]))
        check("replan-resumed-whose-idea-left-the-teams-keeps-that-pass-loud-and-names-it",
              outcome(lambda: rp_resume_after("rp-resume-team", lambda t: t.issues[
                  "PROD-6"].update(team={"key": "DEV"}))),
              (EXIT_ERROR, True, True, EXIT_OK, [REFUSED]))

        def case_rp_stranded_gone():
            c, s, t, _root = rp_cut_then("rp-stranded-gone", delete_bobs_yes)
            first = run(c, s, t, at=now + 60)[0]
            t.issues.pop("PROD-6")
            code, out = run(c, s, t, at=now + 120)
            again = run(c, s, t, at=now + 180)[0]
            return (first, code, "PROD-6: a re-plan began for a yes and has ended" in out,
                    again, status_of(c))
        check("replan-stranded-whose-idea-is-gone-keeps-that-pass-loud-and-names-it",
              outcome(case_rp_stranded_gone), (EXIT_ERROR, EXIT_ERROR, True, EXIT_OK,
                                               [REFUSED]))

        def case_rp_late_yes_plan_since():
            c, s, t, root = rp_cut_then("rp-late-plan", delete_bobs_yes)
            t.plans["PROD-50"] = [plan_70]
            s.add(ALICE, "yes PROD-6", thread=root, at=now + 3 * 86400)
            code, _out = run(c, s, t, at=now + 3 * 86400 + 60)
            return code, t.moves, posted(s)[-1], status_of(c)
        check("replan-a-late-yes-after-a-plan-was-filed-leaves-the-idea-parked-and-says-so",
              outcome(case_rp_late_yes_plan_since),
              (EXIT_OK, parked,
               "PROD-6 already has a plan: PROD-70 (Todo). So I've left PROD-6 in Backlog, "
               "where this re-plan put it. Approve that plan, or cancel it before you plan "
               "the idea again.", [REFUSED]))

        def case_rp_late_yes_unlanded():
            c, s, t, root, _first = rp_unlanded("rp-late-unlanded")
            delete_bobs_yes(s)
            s.add(ALICE, "yes PROD-6", thread=root, at=now + 5 * 86400)
            code, _out = run(c, s, t, at=now + 5 * 86400 + 60)
            return code, t.moves, status_of(c)
        check("replan-whose-first-move-never-landed-expires-like-any-question",
              outcome(case_rp_late_yes_unlanded), (EXIT_OK, [], [EXPIRED]))

        def case_rp_no_state_midway():
            c, s, t, _root = rp_cut_then("rp-no-state", lambda s: None)
            t.team_states.pop(("PROD", "Plan it"), None)
            code, out = run(c, s, t, at=now + 60)
            return (code, t.moves, "back into Plan it" in posted(s)[-1], status_of(c),
                    out.count("PROD-6: "), "I can't move it back into Plan it" in out)
        check("replan-under-way-whose-plan-it-state-is-gone-stays-open-and-loud, said once, "
              "with its cause", outcome(case_rp_no_state_midway),
              (EXIT_ERROR, parked, True, [OPEN], 1, True))

        def case_rp_unwatched():
            c, s, t, _root = rp_cut_then("rp-unwatched", delete_bobs_yes)
            first = run(c, s, t, at=now + 60)[0]
            c["channel_ids"] = ["C0OTHER001"]
            code, out = run(c, s, t, at=now + 120)
            again = run(c, s, t, at=now + 180)[0]
            return (first, code, "no longer reads" in out, again, status_of(c), t.moves,
                    any(ch == "C0OTHER001" and "I closed the re-plan of PROD-6" in x
                        for ch, _th, x in s.posts))
        check("replan-asked-where-the-bridge-no-longer-reads-is-ended-once, and said in Slack",
              outcome(case_rp_unwatched),
              (EXIT_ERROR, EXIT_ERROR, True, EXIT_OK, [EXPIRED], parked, True))

        # KIT-358, the review of the fix: a later yes is judged against what the question
        # showed, the owner's move out of Plan it is always explained, an ending that needs
        # no answer is closed once, and no reply offers a move by hand as a way out.
        def rp_later_yes(name, change):
            c, s, t, root = rp_cut_then(name, delete_bobs_yes)
            change(t)
            s.add(ALICE, "yes PROD-6", thread=root, at=now + 7200)
            code, _out = run(c, s, t, at=now + 7260)
            return code, t.moves, posted(s)[-1], status_of(c)

        def retitle(t):
            t.issues["PROD-6"]["title"] = "Something else entirely"
        check("replan-a-later-yes-after-the-idea-was-rewritten-moves-nothing-back",
              outcome(lambda: rp_later_yes("rp-later-rewritten", retitle)),
              (EXIT_OK, parked, "PROD-6 changed since I asked (its title, text, team or "
               "labels). So I've left PROD-6 in Backlog, where this re-plan put it. To plan it "
               "again, type `plan PROD-6`.", [REFUSED]))
        check("replan-a-later-yes-after-a-newer-run-moves-nothing-back",
              outcome(lambda: rp_later_yes("rp-later-new-run", lambda t: t.add_run(
                  "PROD-6", "PROD-51", "completed", trigger="T2"))),
              (EXIT_OK, parked, "PROD-6's planning changed since I asked (a new run or a new "
               "move). So I've left PROD-6 in Backlog, where this re-plan put it. To plan it "
               "again, type `plan PROD-6`.", [REFUSED]))

        def case_rp_made_in_plan_it_resumes():
            # The question stood on the idea's creation, and after the bridge's own move out
            # the history names no trigger at all: that is no new move, so a later yes ends
            # the re-plan as asked.
            c, s, t = world("rp-made-in-plan-it", act=True)
            run(c, s, t)
            t.in_plan_it("PROD-6", trigger="created-uuid-6")
            t.triggers["uuid-6"][-1]["creation"] = True
            t.add_run("PROD-6", "PROD-50", "completed", trigger="created-uuid-6")
            root = s.add(ALICE, "replan PROD-6")
            run(c, s, t)
            t.move_fail = (2, False, Deadline(), None)
            s.add(BOB, "yes", thread=root)
            try:
                run(c, s, t)
            except Deadline:
                pass
            t.move_fail = None
            t.triggers["uuid-6"] = []
            delete_bobs_yes(s)
            s.add(ALICE, "yes PROD-6", thread=root, at=now + 600)
            code, _out = run(c, s, t, at=now + 660)
            return code, t.moves, posted(s)[-1], status_of(c)
        check("replan-of-an-idea-made-in-plan-it-is-finished-by-a-later-yes",
              outcome(case_rp_made_in_plan_it_resumes), (EXIT_OK, both, moved_ok, [DONE]))

        def case_rp_lost_reply_then_no():
            # The first move lands, its reply is lost, and the pass is cut before anything
            # records it: the `no` that ends the re-plan still explains the owner's move.
            c, s, t, root = rp_cut_then("rp-lost-then-no", delete_bobs_yes,
                                        cut=(1, True, Deadline(), None))
            s.add(ALICE, "no PROD-6", thread=root, at=now + 30)
            code, _out = run(c, s, t, at=now + 60)
            said = t.comments[-1][1] if t.comments else ""
            return (code, t.moves, len(t.comments), BOB in said,
                    "moved this idea out of Plan it to Backlog" in said, status_of(c))
        check("replan-a-no-after-a-lost-first-reply-still-explains-the-move",
              outcome(case_rp_lost_reply_then_no), (EXIT_OK, parked, 1, True, True, [DECLINED]))

        def case_rp_lost_reply_explained_at_pass_end():
            # The failed message stops its own pass; the next pass finds the move and
            # explains it, and the one after writes nothing more.
            c, s, t, _root = rp_cut_then("rp-lost-pass-end", delete_bobs_yes,
                                         cut=(1, True, BridgeError("simulated"), None))
            first = len(t.comments)
            code, _out = run(c, s, t, at=now + 60)
            found = len(t.comments)
            run(c, s, t, at=now + 120)
            return first, found, len(t.comments), code, status_of(c)
        check("replan-a-lost-first-reply-is-explained-by-the-pass-that-finds-it, once",
              outcome(case_rp_lost_reply_explained_at_pass_end),
              (0, 1, 1, EXIT_ERROR, [OPEN]))

        def case_rp_act_off_offers_no_hand_move():
            c, s, t, _root = rp_cut_then("rp-act-off-words", lambda s: None)
            c["act"] = False
            _code, out = run(c, s, t, at=now + 60)
            return ("Switch acting on" in posted(s)[-1], "by hand" in posted(s)[-1],
                    "Acting is switched off, so reply `no PROD-6`" in out)
        check("replan-with-acting-off-offers-a-no-and-acting-on, never a move by hand",
              outcome(case_rp_act_off_offers_no_hand_move), (True, False, True))

        def case_rp_settings_changed_offers_a_no():
            # Its settings changed since it began, so only a `no` ends it, and the line says
            # how to plan the idea again after (KIT-342).
            c, s, t, _root = rp_cut_then("rp-settings-words", lambda s: None)
            c["question_ttl_hours"] += 1
            run(c, s, t, at=now + 60)
            return posted(s)[-1].endswith(REPLAN_LEAVE_BIND % ("PROD-6", "PROD-6"))
        check("replan-whose-settings-changed-offers-a-no-then-plan, never a move by hand",
              outcome(case_rp_settings_changed_offers_a_no), True)

        def case_rp_back_in_plan_it_by_hand():
            # Back in Plan it by hand: the history is read as a finishing yes would read it,
            # and the thread says what it shows (KIT-358 review, round 2).
            c, s, t, _root = rp_cut_then("rp-back-by-hand", delete_bobs_yes)
            t.in_plan_it("PROD-6", trigger="T5")
            code, out = run(c, s, t, at=now + 60)
            again = run(c, s, t, at=now + 120)[0]
            return code, posted(s)[-1], again, status_of(c), "acted: moved" in out
        check("replan-whose-idea-is-back-in-plan-it-is-closed-once, its history read, and no "
              "action counted", outcome(case_rp_back_in_plan_it_by_hand),
              (EXIT_OK, moved_ok, EXIT_OK, [DONE], False))

        def rp_cut_after_both(name, history=True):
            # Both moves land, and the pass is cut before it says so; the yes is then gone.
            c, s, t, root = rp_ask(name)
            if not history:
                t.history_drops.add("uuid-6")
            t.move_fail = (2, True, Deadline(), None)
            s.add(BOB, "yes", thread=root)
            try:
                run(c, s, t)
            except Deadline:
                pass
            t.move_fail = None
            return c, s, t, root

        def case_rp_own_move_back_cut():
            c, s, t, _root = rp_cut_after_both("rp-own-back-cut")
            delete_bobs_yes(s)
            code, _out = run(c, s, t, at=now + 60)
            return code, t.moves, posted(s)[-1], status_of(c)
        check("replan-its-own-move-back-cut: the next pass reads the history and says it",
              outcome(case_rp_own_move_back_cut), (EXIT_OK, both, moved_ok, [DONE]))

        def case_rp_own_move_back_unseen():
            c, s, t, _root = rp_cut_after_both("rp-own-back-unseen", history=False)
            delete_bobs_yes(s)
            code, _out = run(c, s, t, at=now + 60)
            return code, "its history does not show the move back in" in posted(s)[-1], \
                status_of(c)
        check("replan-its-own-move-back-cut-and-unseen-in-the-history-is-said, loudly",
              outcome(case_rp_own_move_back_unseen), (EXIT_ERROR, True, [DONE]))

        def case_rp_own_move_back_then_act_off():
            # Both moves landed: with acting switched off, the yes read again moves nothing
            # and says where the idea is, never "it may be waiting in Backlog".
            c, s, t, _root = rp_cut_after_both("rp-own-back-act-off")
            c["act"] = False
            code, _out = run(c, s, t, at=now + 60)
            return (code, t.moves, posted(s)[-1], "may be waiting" in " ".join(posted(s)),
                    status_of(c))
        check("replan-both-moves-landed-then-acting-off: the history is read, nothing moves",
              outcome(case_rp_own_move_back_then_act_off),
              (EXIT_OK, both, moved_ok, False, [DONE]))

        def case_rp_note_once_per_dm():
            # In DM mode the closing note goes to each listed DM once, each recorded as said:
            # a DM that keeps refusing never makes the others hear it again.
            c, s, t = world("rp-note-dms", act=True, dm_user_ids=[ALICE, BOB])
            run(c, s, t)                                    # the first pass: baselines
            t.in_plan_it("PROD-6", trigger="T1")
            t.add_run("PROD-6", "PROD-50", "completed", trigger="T1")
            root = s.add(ALICE, "replan PROD-6")
            run(c, s, t)
            t.move_fail = (2, False, Deadline(), None)
            s.add(BOB, "yes", thread=root)
            try:
                run(c, s, t)
            except Deadline:
                pass
            t.move_fail = None
            delete_bobs_yes(s)
            c["channel_ids"] = ["C0OTHER001"]
            real_post = s.post

            def bobs_dm_refuses(channel, thread_ts, text):
                if channel == "D0BOB00001":
                    raise BridgeError("Slack chat.postMessage refused: simulated")
                return real_post(channel, thread_ts, text)
            s.post = bobs_dm_refuses
            for k in range(3):
                run(c, s, t, at=now + 60 * (k + 1))
            noted = [ch for ch, _th, x in s.posts if "I closed the re-plan of PROD-6" in x]
            still = status_of(c)
            s.post = real_post
            run(c, s, t, at=now + 300)
            return (noted, still, [ch for ch, _th, x in s.posts
                                   if "I closed the re-plan of PROD-6" in x], status_of(c))
        check("replan-closing-note-is-said-once-and-closes-once-a-place-has-heard-it",
              outcome(case_rp_note_once_per_dm),
              (["D0ALICE001"], [EXPIRED], ["D0ALICE001"], [EXPIRED]))

        # KIT-358 review, round 3: acting off is judged on the read the move would follow;
        # each closure tries every place the watch speaks; a rehearsal says what it would do;
        # a fixed Plan it state ends its line; a pass that only reads counts no action.
        def case_rp_race_act_off():
            c, s, t, root = rp_cut_then("rp-race-act-off", delete_bobs_yes)
            c["act"] = False
            real_issue, reads = t.issue, []

            def racing(ident):
                got = real_issue(ident)
                if ident == "PROD-6" and got:
                    reads.append(1)
                    if len(reads) == 1:      # a person had it in Todo for this one read
                        got = dict(got, state={"id": "st-todo", "name": "Todo",
                                               "type": "unstarted"})
                return got
            t.issue = racing
            s.add(ALICE, "yes PROD-6", thread=root, at=now + 30)
            code, _out = run(c, s, t, at=now + 60)
            return (code, t.moves, "acting is switched off here" in posted(s)[-1],
                    status_of(c))
        check("replan-a-yes-with-acting-off-moves-nothing-whatever-an-earlier-read-said",
              outcome(case_rp_race_act_off), (EXIT_ERROR, parked, True, [OPEN]))

        def dm_stranded(name, between, dm_users):
            c, s, t = world(name, act=True, dm_user_ids=dm_users)
            run(c, s, t)                                    # the first pass: baselines
            t.in_plan_it("PROD-6", trigger="T1")
            t.add_run("PROD-6", "PROD-50", "completed", trigger="T1")
            root = s.add(ALICE, "replan PROD-6")
            run(c, s, t)
            t.move_fail = (2, False, Deadline(), None)
            s.add(BOB, "yes", thread=root)
            try:
                run(c, s, t)
            except Deadline:
                pass
            t.move_fail = None
            delete_bobs_yes(s)
            between(c, s, t)
            real_post = s.post

            def bobs_dm_refuses(channel, thread_ts, text):
                if channel == "D0BOB00001":
                    raise BridgeError("Slack chat.postMessage refused: simulated")
                return real_post(channel, thread_ts, text)
            s.post = bobs_dm_refuses
            code, out = run(c, s, t, at=now + 60)
            return c, s, t, code, out

        def unread_and_todo(c, _s, t):
            c["channel_ids"] = ["C0OTHER001"]
            t.set_state("PROD-6", "unstarted")

        def case_rp_closure_tries_each_place():
            c, s, t, code, out = dm_stranded("rp-closure-places", unread_and_todo,
                                             [BOB, ALICE])
            said = [ch for ch, _th, x in s.posts if "PROD-6 is in Todo now" in x]
            return code, said, status_of(c), "acted:" in out
        check("replan-a-closure-tries-each-place-until-one-takes-it, and counts no action",
              outcome(case_rp_closure_tries_each_place),
              (EXIT_OK, ["D0ALICE001"], [DONE], False))

        def case_rp_note_past_a_refusing_place():
            c, s, t, code, out = dm_stranded(
                "rp-note-past-refusing",
                lambda c, _s, _t: c.__setitem__("channel_ids", ["C0OTHER001"]), [BOB, ALICE])
            said = [ch for ch, _th, x in s.posts if "I closed the re-plan of PROD-6" in x]
            return code, said, status_of(c), "(not in D0BOB00001" in out
        check("replan-the-closing-note-goes-past-a-place-that-refuses, and names it",
              outcome(case_rp_note_past_a_refusing_place),
              (EXIT_ERROR, ["D0ALICE001"], [EXPIRED], True))

        def case_rp_dry_run_says_what_it_would_do():
            c, s, t, _root = rp_cut_then("rp-dry-closure", delete_bobs_yes)
            t.set_state("PROD-6", "unstarted")
            _code, out = run(c, s, t, dry=True, at=now + 60)
            return ("[dry-run] would close the re-plan" in out, "Reply `yes PROD-6`" in out,
                    status_of(c), s.posts[-1][2].startswith("PROD-6 is in Todo"))
        check("replan-a-dry-run-says-it-would-close, and closes nothing",
              outcome(case_rp_dry_run_says_what_it_would_do), (True, False, [OPEN], False))

        def case_rp_blocked_line_ends_when_fixed():
            c, s, t, _root = rp_cut_then("rp-blocked-fixed", lambda s: None)
            saved = t.team_states.pop(("PROD", "Plan it"))
            first = run(c, s, t, at=now + 60)[1]
            t.team_states[("PROD", "Plan it")] = saved
            later = run(c, s, t, at=now + 120)[1]
            return ("I can't move it back into Plan it" in first,
                    "I can't move it back into Plan it" in later, "Reply `yes PROD-6`" in later)
        check("replan-a-missing-plan-it-state-is-named-until-it-is-fixed, then not",
              outcome(case_rp_blocked_line_ends_when_fixed), (True, False, True))

        def case_rp_cut_mid_closure_writes_once():
            # A pass cut while saying the closing note keeps the comment it already wrote.
            c, s, t, _root = rp_cut_then("rp-cut-mid-closure", delete_bobs_yes,
                                         cut=(1, True, Deadline(), None))
            c["channel_ids"] = ["C0OTHER001"]
            real_post = s.post
            cut = []

            def cut_once(channel, thread_ts, text):
                if "I closed the re-plan" in text and not cut:
                    cut.append(1)
                    raise Deadline()
                return real_post(channel, thread_ts, text)
            s.post = cut_once
            try:
                run(c, s, t, at=now + 60)
            except Deadline:
                pass
            run(c, s, t, at=now + 120)
            return len(t.comments), status_of(c)
        check("replan-a-pass-cut-mid-closure-never-writes-the-comment-twice",
              outcome(case_rp_cut_mid_closure_writes_once), (1, [EXPIRED]))

        def case_rp_owed_comment_is_said():
            # The explanation comment fails and is owed: the yes that finishes the re-plan
            # still says the comment is owed, as it always has.
            c, s, t, root = rp_cut_then("rp-owed-said", delete_bobs_yes,
                                        cut=(1, True, Deadline(), None))

            def refuse(_issue_id, _body):
                raise BridgeError("the tracker would not comment (simulated)")
            t.comment = refuse
            s.add(ALICE, "yes PROD-6", thread=root, at=now + 30)
            run(c, s, t, at=now + 60)
            return "I will try again" in posted(s)[-1], len(json.load(open(state_path(c)))[
                "owed"])
        check("replan-a-finish-says-an-owed-comment-is-owed",
              outcome(case_rp_owed_comment_is_said), (True, 1))

        def case_rp_moved_on_unlanded():
            # The first move is refused while a person moves the idea to Todo: whether the
            # bridge's move landed can't be told, so the re-plan is kept, then closed once.
            c, s, t, root = rp_ask("rp-moved-on")
            t.move_fail = (1, False, BridgeError("simulated"),
                           lambda: t.set_state("PROD-6", "unstarted"))
            s.add(BOB, "yes", thread=root)
            first = run(c, s, t)[0]                 # the failed message stops this pass
            t.move_fail = None
            delete_bobs_yes(s)
            code, _out = run(c, s, t, at=now + 60)
            again = run(c, s, t, at=now + 120)[0]
            return first, code, posted(s)[-1], again, t.moves, status_of(c)
        check("replan-whose-idea-moved-on-before-its-first-move-landed-is-closed-once",
              outcome(case_rp_moved_on_unlanded),
              (EXIT_ERROR, EXIT_OK, "PROD-6 is in Todo now, so I've closed this re-plan and "
               "left it there. To plan it, type `plan PROD-6`.", EXIT_OK, [], [DONE]))

        def case_rp_partial_is_explained():
            # The comment naming who said yes lands as soon as the idea leaves Plan it, so a
            # move back that fails still leaves the owner's move explained.
            c, s, t, root = rp_ask("rp-partial-explained")
            t.move_raises = 2
            s.add(BOB, "yes", thread=root)
            run(c, s, t)
            return len(t.comments), "said yes in Slack" in (t.comments[-1][1] if t.comments
                                                            else "")
        check("replan-a-failed-move-back-still-leaves-the-comment-naming-who-said-yes",
              outcome(case_rp_partial_is_explained), (1, True))

        def case_rp_changed_planning():
            # The question named PROD-50 as the last run. A run since then, from a move since
            # then, is a new situation: the old yes moves nothing (review, #4).
            got = rp_after_yes("rp-changed-planning", lambda t: (
                t.add_run("PROD-6", "PROD-51", "canceled", trigger="T2"),
                t.triggers["uuid-6"].append({"id": "T2", "createdAt": "2025-10-09T07:30:00.000Z",
                                             "actorId": OWNER})))
            return got[1], got[2], got[3]
        check("replan-a-run-or-move-since-the-question-moves-nothing",
              outcome(case_rp_changed_planning),
              ([], "PROD-6's planning changed since I asked (a new run or a new move), so "
                   "nothing was moved, and I have closed the question. Ask again with "
                   "`replan PROD-6`.", [REFUSED]))

        def case_rp_written():
            fw = FakeWriter()
            c, s, t = wworld("w-replan", act=True, dm_user_ids=[ALICE])
            wrun(c, s, t, fw)
            t.in_plan_it("PROD-6", trigger="T1")
            t.add_run("PROD-6", "PROD-50", "completed", trigger="T1")
            s.add(ALICE, "replan PROD-6", channel=DM)
            wrun(c, s, t, fw)
            s.add(ALICE, "yes", channel=DM)
            wrun(c, s, t, fw)
            asked, moved = fw.last("question-replan"), fw.last("replan-moved")
            bad = [(x["kind"], why) for x in fw.calls
                   for why in [voice.check_text(x["fallback"], 100000, x["keep"],
                                                voice.fact_links(x["facts"]),
                                                voice.fact_ids(x["facts"], x["keep"]))]
                   if why]
            return asked["facts"]["last_run"], moved["fallback"], t.moves, bad
        check("writer-replan: the question's last run is a fact, the move is written with the "
              "member's name, and no text handed to the writer fails its own checks",
              outcome(case_rp_written),
              ("PROD-50", "Moved PROD-6 out of Plan it and back in for Alice Example. The "
                          "planner starts a new run within a few minutes.",
               [("uuid-6", "st-backlog"), ("uuid-6", "st-plan-it")], []))


def selftest():
    import pipeline_bridge
    return pipeline_bridge.split_module_selftest(os.path.abspath(__file__),
                                                 [cases_replan])


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        # As the module the bridge imports, never a copy of it named __main__, so its
        # cases and the pass share one copy of it (KIT-312).
        import pipeline_bridge_replan
        sys.exit(pipeline_bridge_replan.selftest())
    print("usage: pipeline_bridge_replan.py --selftest (the bridge imports this module)")
    sys.exit(EXIT_USAGE)
