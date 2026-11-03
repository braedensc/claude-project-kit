#!/usr/bin/env python3
"""The Slack bridge's answers: a yes or a no to its own question, and the plan action.

    python3 scripts/pipeline_bridge_answers.py --selftest

A MODULE OF ITS OWN (KIT-312, part 2: KIT-407). scripts/pipeline_bridge.py was one file
of more than 12,000 lines, so Stage E withheld it from every review it was in, and every
bridge feature conflicted with every other. It is split by concern, with no change in
behaviour: each function, constant and method here is main's own text, moved unchanged.
pipeline_bridge imports every name it had, so `pipeline_bridge.X` is still the very same
object. Its cases run in the bridge's own selftest, end to end through one pass; this
module's selftest pins what it may reach.

WHAT IS HERE
  `answer`       a yes or a no binds to a question this job asked, in its thread or its
                 DM, after it, from a verified member: never to a ticket named in text.
  Its checks     a post between the question and a bare yes that the bridge cannot vouch
                 for, a question edited or gone, the time limit, the settings it stated.
  The plan action  a yes to `plan` moves the idea into Plan it, as the owner (KIT-222).
  Expiry         an open question closes once its time is up.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pipeline_notify_local as notify  # noqa: E402
from pipeline_bridge_transport import BridgeError  # noqa: E402
from pipeline_bridge_config import (CONFIRMED, DECLINED, DONE, EXIT_USAGE,  # noqa: E402
                                    EXPIRED, OPEN, REFUSED, iso, key_of, load_pings,
                                    ts_float)
from pipeline_bridge_plans import FOLLOWUP  # noqa: E402
from pipeline_bridge_requests import (account_refusal, message_refusal,  # noqa: E402
                                      ticket_fingerprint)

# The notifier's pages that ask the member something: a dev or the planner stopped to ask,
# or a plan waits for approval. A bare yes typed after one may be meant for it, so such a
# page is never vouched for between a question and a bare yes (KIT-295 review).
DM_ASKING_MARKS = (notify.ESC_BLOCKED, notify.ESC_NEEDS_INPUT, notify.ESC_AWAITING_APPROVAL)


class AnswersMixin(object):
    """Pass's answers to its own questions, and the plan action (KIT-312)."""

    def around(self, channel, q, msg):
        """What was posted where this answer was given, as read this pass: the question's
        thread in a channel. A question in a DM is a message of its own (KIT-295): the DM's
        own messages, or the thread under the question when the answer is a reply in it."""
        if q.get("thread"):
            return self.threads.get((channel, q["thread"]), [])
        if msg.get("thread_ts") and msg.get("thread_ts") != msg.get("ts"):
            return self.threads.get((channel, msg["thread_ts"]), [])
        return self.tops.get(channel, [])

    def foreign_post_after(self, channel, q, msg):
        """True when anything the bridge cannot vouch for posted where the answer was
        given, between the question and this answer: a bot or an app, Slackbot, an account
        that could not answer itself (a guest, another workspace, no sender named), or the
        bridge's own account posting something it has no record of. Any of them could be a
        look-alike question. A verified member's post is not, and neither is a page the
        notifier posted into a DM as this app and recorded that asks nothing (vouched_page,
        KIT-295)."""
        lo, hi = ts_float(q.get("ts")), ts_float(msg.get("ts"))
        for m in self.around(channel, q, msg):
            t = ts_float(m.get("ts"))
            if not lo < t < hi:
                continue
            ours = (m.get("user") == self.me["user_id"]
                    or (self.me.get("bot_id") and m.get("bot_id") == self.me["bot_id"]))
            if ours:
                if (key_of(channel, m.get("ts")) not in self.state["posted"]
                        and not self.vouched_page(channel, m.get("ts"))):
                    return True
                if key_of(channel, m.get("ts")) in self.state.get("relayed_posts", {}):
                    # KIT-298: the chat bot's words, posted as this app. A model wrote them,
                    # so a look-alike question could be among them.
                    return True
                continue
            if m.get("bot_id") or m.get("app_id") or m.get("subtype") == "bot_message":
                return True
            if m.get("user") == "USLACKBOT":
                return True
            if not m.get("user") or account_refusal(self.account(m["user"]), self.me,
                                                    self.cfg):
                return True
        return False

    def vouched_page(self, channel, ts):
        """True for a page the notifier posted into this DM as this app, recorded in its
        ledger for this DM, that waits on no answer (KIT-295). A page that asks something,
        by its mark or because the pings file holds it, is never vouched for: a bare yes
        typed after it may be meant for it. Nothing in a channel is vouched for this way."""
        rec = self.dm_posts.get(key_of(channel, ts))
        if (channel not in self.dm_channels or not isinstance(rec, dict)
                or rec.get("channel") != channel or rec.get("ts") != ts):
            return False
        if rec.get("mark") not in notify.MARKS or rec.get("mark") in DM_ASKING_MARKS:
            return False
        return notify.chat_message_key(channel, ts) not in load_pings(self.cfg)

    def question_closed(self, channel, q):
        """Why the question can no longer be answered as asked, or None. A question gone
        from where it was asked (deleted) is closed, never taken as unchanged. One in a DM
        is a message of its own, judged from the DM's own messages (KIT-295)."""
        if q.get("thread"):
            seen = self.threads.get((channel, q["thread"]), [])
            gone = "is no longer in this thread"
        else:
            seen, gone = self.tops.get(channel, []), "is no longer here"
        for m in seen:
            if m.get("ts") == q.get("ts"):
                return "was changed after I asked it" if m.get("edited") else None
        return gone

    def refused_foreign(self, channel, thread, q, msg, where, word):
        """True, having said so, when something the bridge can't vouch for posted between
        the question and this bare answer: it may be answering that instead."""
        if not self.foreign_post_after(channel, q, msg):
            return False
        # The writer is told which question, since only the bind names it here.
        self.post(channel, thread, "Something other than me posted %s after my question, "
                  "so I can't be sure which question you are answering." % where,
                  kind="foreign-post", bind="Reply `%s %s` to answer the one about %s."
                  % (word, q["ticket"], q["ticket"]), join=" ",
                  facts={"ticket": q["ticket"]},
                  plain="Something other than me posted %s after my question about %s, "
                        "so I can't be sure which question you are answering."
                        % (where, q["ticket"]))
        self.mark(channel, msg, "refused: something else posted after the question")
        self.counts["refused"] += 1
        return True

    def refused_sender(self, channel, thread, q, msg):
        """True, having said so, when the message or its sender can't answer: a bot, an
        app, a guest, an edited message, a member not listed."""
        why = message_refusal(msg, self.me)
        if why is None:
            why = account_refusal(self.account(msg["user"]), self.me, self.cfg)
        if not why:
            return False
        self.post_refusal(channel, thread, "I can't take that as an answer to the question "
                          "about %s: %s." % (q["ticket"], why), why, "answer-refused",
                          q["ticket"])
        self.mark(channel, msg, "refused: %s" % why)
        self.counts["refused"] += 1
        self.say("  refused an answer on %s: %s" % (q["ticket"], why))
        return True

    def answer(self, channel, msg, is_yes, hint):
        """A yes or no. In a channel it answers the open question in its thread. In a DM a
        question is a message of its own (KIT-295): an answer of its own answers the open
        question there asked before it, and a reply under a question answers that one.
        Every check after that is the same in both."""
        thread = msg.get("thread_ts") if msg.get("thread_ts") != msg.get("ts") else None
        dm = channel in self.dm_channels
        if dm:
            where = "in that thread" if thread else "here"
            here = [(k, q) for k, q in self.state["questions"].items()
                    if q.get("channel") == channel and (not thread or q.get("ts") == thread)
                    and ts_float(q.get("ts")) < ts_float(msg.get("ts"))]
            thread = None                         # every post in a DM is one of its own
        else:
            where = "in this thread"
            here = [(k, q) for k, q in self.state["questions"].items()
                    if q.get("channel") == channel and q.get("thread") == thread
                    and ts_float(q.get("ts")) < ts_float(msg.get("ts"))]
        open_here = [(k, q) for k, q in here if q.get("status") == OPEN]
        if hint:
            open_here = [(k, q) for k, q in open_here if q.get("ticket") == hint]
        if not open_here:
            if here or dm:
                self.post(channel, thread, "There is no open question%s %s to answer."
                          % (" about %s" % hint if hint else "", where), kind="no-question",
                          facts={"ticket": hint} if hint else None)
                self.mark(channel, msg, "refused: no open question")
                self.counts["refused"] += 1
            else:
                self.mark(channel, msg, "ignored: no question here")
            return
        word = "yes" if is_yes else "no"
        if len(open_here) > 1:
            self.post(channel, thread, "%d questions are open %s." % (len(open_here), where),
                      kind="ambiguous", bind="Reply `%s <ticket id>` to say which." % word,
                      join=" ", facts={"open_questions": len(open_here)})
            self.mark(channel, msg, "refused: ambiguous")
            self.counts["refused"] += 1
            return
        qkey, q = open_here[0]
        if q.get("kind") == FOLLOWUP and q.get("filing"):
            # A follow-up already being filed, its reply lost: say what was filed, or that it
            # can't be confirmed. Never "acts on nothing" for a ticket that may exist.
            self.act_followup(channel, thread, q, msg)
            return
        if q.get("kind") == "replan" and q.get("replanning") and self.settle_replan(q):
            # A re-plan under way, its pass cut (KIT-244 review): its idea may wait outside
            # Plan it, so nothing here may CLOSE it. Only the yes that began it skips the
            # checks below, and a `no` never moves it (KIT-358). One whose first move never
            # landed is an ordinary question again, and is judged below like any other.
            self.answer_replan_under_way(channel, thread, q, msg, is_yes, hint, where, word)
            return
        # From here on a question's status changes only AFTER the reply saying so is
        # posted: a failed post leaves it open, and the next pass says it.
        closed = self.question_closed(channel, q)
        if closed:
            self.post(channel, thread, "My question about %s %s, so I have closed it."
                      % (q["ticket"], closed), kind="question-closed",
                      bind=self.again(q), join=" ", facts={"ticket": q["ticket"]})
            q["status"] = EXPIRED
            self.mark(channel, msg, "refused: question %s" % closed)
            self.counts["refused"] += 1
            return
        if not hint and self.refused_foreign(channel, thread, q, msg, where, word):
            return
        if self.refused_sender(channel, thread, q, msg):
            return
        # Judged by when the answer was SENT: a yes sent in time while the Mac slept counts.
        if ts_float(msg.get("ts")) - q["asked_at"] > self.cfg["question_ttl_hours"] * 3600:
            self.post(channel, thread, "The question about %s has expired." % q["ticket"],
                      kind="question-expired", bind=self.again(q), join=" ",
                      facts={"ticket": q["ticket"]})
            q["status"] = EXPIRED
            self.mark(channel, msg, "refused: expired")
            self.counts["expired"] += 1
            return
        if not is_yes:
            self.post(channel, thread, "OK, I won't file the notes from %s." % q["ticket"]
                      if q["kind"] == FOLLOWUP else "OK, %s is not %s." % (
                          q["ticket"], {"approve": "approved", "replan": "planned again"}.get(
                              q["kind"], "planned")),
                      kind="declined", facts={"ticket": q["ticket"]})
            q.update(status=DECLINED, answered_by=msg["user"], answered_ts=msg.get("ts"))
            self.mark(channel, msg, "declined: %s" % q["ticket"])
            self.counts["declined"] += 1
            return
        if q.get("promised") != self.promised(q["kind"], q.get("team")):
            self.post(channel, thread, "My settings changed since I asked about %s, so this "
                      "yes acts on nothing, and I have closed the question." % q["ticket"],
                      kind="settings-changed", bind=self.again(q), join=" ",
                      facts={"ticket": q["ticket"]})
            q.update(status=REFUSED, answered_by=msg["user"], answered_ts=msg.get("ts"))
            self.mark(channel, msg, "refused: settings changed since asked")
            self.counts["refused"] += 1
            return
        self.counts["confirmed"] += 1
        if not self.cfg["act"]:
            self.post(channel, thread, "Confirmed by <@%s>. Acting is switched off here, so "
                      "%s was not changed." % (msg["user"], q["ticket"]),
                      kind="confirmed-acting-off", facts={"ticket": q["ticket"]})
            q.update(status=CONFIRMED, answered_by=msg["user"], answered_ts=msg.get("ts"))
            self.mark(channel, msg, "confirmed: %s" % q["ticket"])
            self.say("  confirmed: %s %s by %s (acting is off)"
                     % (q["kind"], q["ticket"], msg["user"]))
            return
        if q["kind"] == "approve":
            self.act_approve(channel, thread, q, msg)
        elif q["kind"] == FOLLOWUP:
            self.act_followup(channel, thread, q, msg)
        elif q["kind"] == "replan":
            self.act_replan(channel, thread, q, msg)
        else:
            self.act_plan(channel, thread, q, msg)

    @staticmethod
    def again(q):
        """What to do after a question closed unanswered: ask again, for a request. A closed
        plan's follow-up question has no request to repeat (KIT-299)."""
        if q.get("kind") == FOLLOWUP:
            return "The notes stay in my wrap-up; file them yourself if you want them."
        return "Ask again with `%s %s`." % (q["kind"], q["ticket"])

    def act_plan(self, channel, thread, q, msg):
        """Move the idea into its team's Plan it state, as the owner. Re-read first: the
        ticket may have changed since the question. Idempotent: a ticket already in Plan it
        is said and left alone, so a pass that crashed after the move never moves twice."""
        def finish(status, text, what, kind="plan-refused", bind=""):
            # Posted first: the status changes only after the reply saying so lands.
            self.post(channel, thread, text, kind=kind, bind=bind, join=" ",
                      facts={"ticket": q["ticket"]})
            q.update(status=status, answered_by=msg["user"], answered_ts=msg.get("ts"))
            self.mark(channel, msg, what)
        if self.refused_over_limit(channel, thread, msg):
            return
        issue = self.tracker.issue(q["ticket"])
        if not issue or not issue.get("id"):
            finish(REFUSED, "I can't find %s in the tracker any more, so nothing was moved."
                   % q["ticket"], "refused: gone")
            return
        team = (issue.get("team") or {}).get("key")
        if team not in self.cfg["team_keys"]:
            finish(REFUSED, "%s is now on %s, which the bridge does not plan on, so nothing "
                   "was moved." % (q["ticket"], team or "no team"), "refused: team")
            return
        state_id, why = self.tracker.team_state(team, self.cfg["plan_it_state"])
        if not state_id:
            finish(REFUSED, "I can't move %s: %s." % (q["ticket"], why), "refused: no state")
            return
        if (issue.get("state") or {}).get("id") == state_id:
            finish(DONE, "%s is already in %s." % (q["ticket"], self.cfg["plan_it_state"]),
                   "done: already", kind="plan-already")
            return
        if ticket_fingerprint(issue) != q.get("fingerprint"):
            finish(REFUSED, "%s changed since I asked (its title, text, team, labels or "
                   "state), so nothing was moved, and I have closed the question." % q["ticket"],
                   "refused: changed since asked", bind="Ask again with `plan %s`." % q["ticket"])
            return
        live = self.tracker.live_work(issue["id"])
        if live:
            finish(REFUSED, "%s looks like live work now, so nothing was moved: %s."
                   % (q["ticket"], "; ".join(live)), "refused: live work")
            return
        if self.dry:
            self.say("  [dry-run] would move %s to %s" % (q["ticket"], self.cfg["plan_it_state"]))
            return
        name = self.name_of(msg["user"])
        self.tracker.move(issue["id"], state_id)
        self.state["acted_at"].append(self.now)
        when = iso(ts_float(msg.get("ts")))
        body = ("Moved to %s by the Slack bridge for **%s** (Slack user %s), who said yes in "
                "Slack at %s." % (self.cfg["plan_it_state"], name, msg["user"], when))
        owed = ""
        try:
            self.tracker.comment(issue["id"], body)
        except BridgeError as exc:
            # The move stands: it cannot be taken back. The record of who said yes is owed.
            self.say("  NOTE: %s was moved, but its attribution comment failed: %s"
                     % (q["ticket"], exc))
            self.owe(issue["id"], q["ticket"], body)
            owed = (" The move was done, but the comment naming who approved it could not be "
                    "written; I will try again.")
        finish(DONE, "Moved %s to %s for <@%s>. The planner starts on it within a few "
               "minutes.%s" % (q["ticket"], self.cfg["plan_it_state"], msg["user"], owed),
               "done: moved %s" % q["ticket"], kind="plan-moved")
        self.counts["acted"] = self.counts.get("acted", 0) + 1
        self.say("  acted: moved %s to %s for %s" % (q["ticket"], self.cfg["plan_it_state"],
                                                     msg["user"]))

    # -- housekeeping --------------------------------------------------------------
    def expire(self):
        ttl = self.cfg["question_ttl_hours"] * 3600
        for q in self.state["questions"].values():
            if q.get("status") != OPEN or self.now - q.get("asked_at", 0) <= ttl:
                continue
            # A re-plan under way whose idea may wait outside Plan it is never closed by the
            # clock: run() says it each pass until a yes or a `no` ends it (KIT-244 review).
            # One whose first move never landed is an ordinary question (KIT-358).
            if q.get("replanning") and self.replan_may_wait(q):
                continue
            q["status"] = EXPIRED
            self.counts["expired"] += 1


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
    print("usage: pipeline_bridge_answers.py --selftest (the bridge imports this module)")
    sys.exit(EXIT_USAGE)
