#!/usr/bin/env python3
"""The Slack bridge's re-plan (KIT-244): `replan <ticket id>`, from the request to the end.

    python3 scripts/pipeline_bridge_replan.py --selftest

A MODULE OF ITS OWN (KIT-312, part 2: KIT-407). scripts/pipeline_bridge.py was one file
of more than 12,000 lines, so Stage E withheld it from every review it was in, and every
bridge feature conflicted with every other. It is split by concern, with no change in
behaviour: each function, constant and method here is main's own text, moved unchanged.
pipeline_bridge imports every name it had, so `pipeline_bridge.X` is still the very same
object. Its cases run in the bridge's own selftest, end to end through one pass; this
module's selftest pins what it may reach.

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
from pipeline_bridge_config import (DECLINED, DONE, EXIT_USAGE, EXPIRED, OPEN,  # noqa: E402
                                    REFUSED, epoch, iso, run_idea, ticket_number, ts_float)
from pipeline_bridge_requests import (newest_run, question_parts,  # noqa: E402
                                      ticket_fingerprint)

# A re-plan (KIT-244): a planning ticket in one of these state types has ended, and a move
# into Plan it this recent with no run yet may be the one the planner is starting on now.
REPLAN_RUN_ENDED = ("completed", "canceled")
REPLAN_SETTLE_SECONDS = 15 * 60
# Said after a re-plan's outcome while its attribution comment is still owed (KIT-244).
REPLAN_COMMENT_OWED = (" The idea was moved, but the comment naming who approved it could not "
                       "be written; I will try again.")


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
            self.refuse_request(channel, msg, thread, "I can't find %s in the tracker."
                                % ident, "not found", ticket=ident)
            return
        idea, named = run_idea(issue, self.cfg["owner_user_id"]), ""
        if idea:
            named = issue["identifier"]
            issue = self.tracker.issue(idea)
            if not issue or not issue.get("identifier"):
                self.refuse_request(channel, msg, thread, "%s is a planning run for %s, and "
                                    "I can't find %s in the tracker." % (named, idea, idea),
                                    "not found", ticket=named)
                return
            if any(q.get("status") == OPEN and q.get("ticket") == issue["identifier"]
                   for q in self.state["questions"].values()):
                self.refuse_request(channel, msg, thread, "%s already has an open question."
                                    % issue["identifier"], "already asked",
                                    bind="Answer that one.", ticket=issue["identifier"])
                return
        team = (issue.get("team") or {}).get("key")
        if team not in self.cfg["team_keys"]:
            self.refuse_request(channel, msg, thread, "%s is on %s, and the bridge plans only "
                                "on %s." % (issue["identifier"], team or "no team",
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
                    "To plan it, type `plan %s`." % ident, "not in plan it"), [], None
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
                        "Give it %d minutes, then ask again." % (REPLAN_SETTLE_SECONDS // 60),
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
                    "Ask again once that run has finished.", "run open"), runs
        names = [r["identifier"] for r in runs]
        standing = sorted((p for p in self.tracker.filed_plans(team, names, owner)
                           if ((p.get("state") or {}).get("type")) != "canceled"),
                          key=lambda p: ticket_number(p.get("identifier")))
        if standing:
            return ("%s already has a plan: %s." % (ident, ", ".join(
                "%s (%s)" % (p.get("identifier") or "?", (p.get("state") or {}).get("name")
                             or "?") for p in standing)),
                    "Approve that plan, or cancel it before you plan the idea again.",
                    "plan filed"), runs
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
                bind = "To plan it again, type `plan %s`." % q["ticket"]
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
            bind = ("Switch acting on and reply `yes %s` to finish it, or reply `no %s` to "
                    "leave it there." % (q["ticket"], q["ticket"]))
        elif q.get("promised") != self.promised(q["kind"], q.get("team")):
            why = "my settings changed since this re-plan began"
            bind = ("Reply `no %s` to leave it there; then, to plan it, type `plan %s`."
                    % (q["ticket"], q["ticket"]))
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
            finish(REFUSED, "I can't find %s in the tracker any more, so nothing %s moved."
                   % (q["ticket"], "more was" if under_way else "was"), "refused: gone")
            if under_way:
                self.replan_errors.append(
                    "%s: a re-plan under way ended, since the idea can't be found in the "
                    "tracker any more. If it still exists, it may be waiting in %s"
                    % (q["ticket"], park))
            return
        team = (issue.get("team") or {}).get("key")
        if team not in self.cfg["team_keys"]:
            finish(REFUSED, "%s is now on %s, which the bridge does not plan on, so nothing "
                   "%s moved." % (q["ticket"], team or "no team",
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
                      kind="replan-waits", bind="Fix that, then reply `yes %s`, or reply `no %s` "
                      "to leave it there." % (q["ticket"], q["ticket"]), join=" ",
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
                   "refused: changed since asked", bind="Ask again with `replan %s`." % q["ticket"])
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
                   bind="Ask again with `replan %s`." % q["ticket"])
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
                       kind="replan-partial", bind="To finish, type `plan %s`." % q["ticket"])
                return
            if now_in != plan_it:
                self.replan_errors.append("%s: the move back may not have landed: %s"
                                          % (q["ticket"], exc))
                finish(DONE, "I moved %s to %s, and I can't tell whether the move back into %s "
                       "landed.%s" % (q["ticket"], park_name, plan_it_name, owed),
                       "partial: %s unknown" % q["ticket"], kind="replan-partial",
                       bind="Look at %s in the tracker. If it isn't in %s, type `plan %s`."
                       % (q["ticket"], plan_it_name, q["ticket"]))
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
                   bind="If no planning run starts within ten minutes, move %s out of %s and "
                   "back in by hand." % (q["ticket"], plan_it_name))
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
                   bind="To plan it, type `plan %s`." % q["ticket"])
            return
        if where.get("id") == plan_it and not rec.get("parked"):
            # Not reached through answer(), which drops a record whose first move never
            # landed: never two moves on a yes that no check has judged now (KIT-358).
            q.pop("replanning", None)
            finish(REFUSED, "Nothing has moved %s yet, so I've closed this re-plan."
                   % q["ticket"], "refused: nothing under way",
                   bind="Ask again with `replan %s`." % q["ticket"])
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
                    bind=bind or "To plan it again, type `plan %s`." % q["ticket"])
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
                                  bind="To plan it, type `plan %s`." % ticket, join=" ",
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
            finish(REFUSED, "I can't find %s in the tracker any more, so I've closed this "
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
               bind="To plan it, type `plan %s`." % ticket)


# ══════════════════════════════════════════════════════════════════════════════════════
# Selftest — offline. This module's cases run in the bridge's own selftest, end to end
# through one pass; this one pins what the module may reach (KIT-407).
# ══════════════════════════════════════════════════════════════════════════════════════
def selftest():
    import pipeline_bridge
    return pipeline_bridge.split_module_selftest(os.path.abspath(__file__))


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(selftest())
    print("usage: pipeline_bridge_replan.py --selftest (the bridge imports this module)")
    sys.exit(EXIT_USAGE)
