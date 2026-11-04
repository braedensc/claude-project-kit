#!/usr/bin/env python3
"""The Slack bridge's answers: a yes or a no to its own question, and the plan action.

    python3 scripts/pipeline_bridge_answers.py --selftest

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
from pipeline_bridge_config import (ASK_AGAIN_BIND, CONFIRMED, DECLINED,  # noqa: E402
                                    DONE, EXIT_USAGE, EXPIRED, OPEN, REFUSED, iso, key_of,
                                    load_pings, ts_float)
from pipeline_bridge_plans import FOLLOWUP  # noqa: E402
from pipeline_bridge_requests import (NAMED_YES_BIND, account_refusal,  # noqa: E402
                                      message_refusal, ticket_fingerprint)

# The notifier's pages that ask the member something: a dev or the planner stopped to ask,
# or a plan waits for approval. A bare yes typed after one may be meant for it, so such a
# page is never vouched for between a question and a bare yes (KIT-295 review).
DM_ASKING_MARKS = (notify.ESC_BLOCKED, notify.ESC_NEEDS_INPUT, notify.ESC_AWAITING_APPROVAL)
# The bind sentences of an answer (KIT-342), pinned word for word by the bridge's selftest.
FOREIGN_POST_BIND = "Reply `%s %s` to answer the one about %s."
AMBIGUOUS_BIND = "Reply `%s <ticket id>` to say which."
FOLLOWUP_LEFT_BIND = "The notes stay in my wrap-up; file them yourself if you want them."


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
                  kind="foreign-post", bind=FOREIGN_POST_BIND
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
                      kind="ambiguous", bind=AMBIGUOUS_BIND % word,
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
        if is_yes and not hint and q.get("named_yes"):
            # The tech lead picked the ticket, so only a yes naming it acts (ACT-1).
            self.post(channel, thread, "", kind="named-yes-needed",
                      bind=NAMED_YES_BIND % (q["ticket"], q["ticket"]))
            self.mark(channel, msg, "refused: the yes must name %s" % q["ticket"])
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
            return FOLLOWUP_LEFT_BIND
        return ASK_AGAIN_BIND % (q["kind"], q["ticket"])

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
            finish(REFUSED, "I can't find %s in Linear any more, so nothing was moved."
                   % q["ticket"], "refused: gone")
            return
        team = (issue.get("team") or {}).get("key")
        if team not in self.cfg["team_keys"]:
            finish(REFUSED, "%s is now on %s, which I don't plan on, so nothing was moved."
                   % (q["ticket"], team or "no team"), "refused: team")
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
                   "refused: changed since asked", bind=ASK_AGAIN_BIND % ("plan", q["ticket"]))
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
# Selftest — offline: this module's cases, moved here from the bridge's selftest (KIT-312),
# and its pins. The bridge's selftest runs these cases too, each in its place among the
# others, with one kit for all of them.
# ══════════════════════════════════════════════════════════════════════════════════════
def cases_plan(kit):
    """The plan action (KIT-222): its cases from the bridge's selftest, moved
    here unchanged (KIT-312)."""
    import json
    from pipeline_bridge import (ALICE, BOB, CHATBOT, EXIT_ERROR, EXIT_OK, GUEST, ME,
                                 Q_ISSUE, state_path)
    with kit as tmp:
        check, now, posted, run = kit.take("check", "now", "posted", "run")
        status_of, world = kit.take("status_of", "world")
        # 11d. THE PLAN ACTION (KIT-222) ------------------------------------------------
        # Acting off (the default): a yes changes nothing, and says so.
        c, s, t = world("act-off")
        run(c, s, t)
        root = s.add(ALICE, "plan PROD-5")
        run(c, s, t)
        s.add(BOB, "yes", thread=root)
        run(c, s, t)
        check("act-off-moves-nothing", (t.moves, t.comments, "switched off" in posted(s)[-1]),
              ([], [], True))
        # Acting on: a verified yes moves the idea into Plan it, as the owner, once.
        c, s, t = world("act-on", act=True)
        run(c, s, t)
        root = s.add(ALICE, "plan PROD-5")
        run(c, s, t)
        check("act-on-question-says-it-will-move", "and I'll hand it to the planner"
              in posted(s)[-1], True)
        s.add(BOB, "yes", thread=root)
        run(c, s, t)
        st = json.load(open(state_path(c)))
        check("yes-moves-the-idea", (t.moves, [v["status"] for v in st["questions"].values()],
                                     "Moved PROD-5 to Plan it" in posted(s)[-1]),
              ([("uuid-5", "st-plan-it")], [DONE], True))
        check("the-move-names-who-said-yes", len(t.comments) == 1 and BOB in t.comments[0][1]
              and "said yes in Slack" in t.comments[0][1], True)
        s.add(ALICE, "yes", thread=root)
        run(c, s, t)
        check("a-second-yes-moves-nothing", len(t.moves), 1)
        # Re-read before moving: already in Plan it is said, and nothing moves twice.
        c, s, t = world("already", act=True)
        run(c, s, t)
        root = s.add(ALICE, "plan PROD-5")
        run(c, s, t)
        t.issues["PROD-5"]["state"] = {"id": "st-plan-it", "name": "Plan it",
                                       "type": "unstarted"}
        s.add(BOB, "yes", thread=root)
        run(c, s, t)
        check("already-in-plan-it-is-said-not-moved", (t.moves, "already in Plan it"
                                                        in posted(s)[-1]), ([], True))
        # Live work: refused before asking, and again before moving.
        c, s, t = world("live-before", act=True)
        run(c, s, t)
        t.live["uuid-5"] = ["it is delegated to someone"]
        s.add(ALICE, "plan PROD-5")
        run(c, s, t)
        check("live-work-refused-before-asking", ("looks like live work" in posted(s)[-1],
                                                  json.load(open(state_path(c)))["questions"]),
              (True, {}))
        c, s, t = world("live-after", act=True)
        run(c, s, t)
        root = s.add(ALICE, "plan PROD-5")
        run(c, s, t)
        t.live["uuid-5"] = ["it has an agent session"]
        s.add(BOB, "yes", thread=root)
        run(c, s, t)
        check("live-work-refused-before-moving", (t.moves, "live work now" in posted(s)[-1]),
              ([], True))
        # A team with no single to-do Plan it state: refused, said.
        c, s, t = world("no-state", act=True)
        run(c, s, t)
        root = s.add(ALICE, "plan PROD-5")
        run(c, s, t)
        t.team_states = {}
        s.add(BOB, "yes", thread=root)
        run(c, s, t)
        check("no-plan-it-state-refused", (t.moves, "can't move PROD-5" in posted(s)[-1]),
              ([], True))
        # A ticket moved to another team after the question: refused, nothing moved.
        c, s, t = world("team-moved", act=True)
        run(c, s, t)
        root = s.add(ALICE, "plan PROD-5")
        run(c, s, t)
        t.issues["PROD-5"]["team"] = {"key": "OPS"}
        s.add(BOB, "yes", thread=root)
        run(c, s, t)
        check("a-ticket-moved-off-a-planned-team-is-refused",
              (t.moves, "now on OPS" in posted(s)[-1]), ([], True))
        # The action limit.
        c, s, t = world("act-limit", act=True, max_actions_per_hour=1)
        run(c, s, t)
        r5 = s.add(ALICE, "plan PROD-5")
        r6 = s.add(ALICE, "plan PROD-6")
        run(c, s, t)
        s.add(BOB, "yes", thread=r5)
        s.add(BOB, "yes", thread=r6)
        run(c, s, t)
        check("action-limit", (len(t.moves), "my limit" in posted(s)[-1]), (1, True))
        # A guest's yes never moves anything, with acting on.
        c, s, t = world("act-guest", act=True)
        run(c, s, t)
        root = s.add(ALICE, "plan PROD-5")
        run(c, s, t)
        s.add(GUEST, "yes", thread=root)
        run(c, s, t)
        check("a-guest-yes-moves-nothing-with-acting-on", t.moves, [])
        # REVIEW OF KIT-222 (KIT-262): every other no-op case, with acting on too. Each
        # refusal runs before the act, so a later edit that moved the act above one of
        # them would write as the owner; each case here goes red if that happened.
        def noop_with_acting_on(name, then, answer_at=None):
            c, s, t = world("act-noop-" + name, act=True,
                            **({"question_ttl_hours": 1} if answer_at else {}))
            run(c, s, t)
            root = s.add(ALICE, "plan PROD-5")
            run(c, s, t)
            then(s, root)
            run(c, s, t, at=answer_at)
            return (t.moves, t.comments, t.delegations), status_of(c), posted(s)[-1]

        def edit_the_question(s, _root):
            for m in s.messages:
                if m.get("user") == ME and "Plan this?" in m.get("text", ""):
                    m["edited"] = {"user": ME, "ts": "1"}
        for name, then, answer_at, want_status, said in (
                ("expired", lambda s, root: s.add(BOB, "yes", thread=root, at=now + 2 * 3600),
                 now + 2 * 3600 + 60, EXPIRED, "has expired"),
                ("bot-post", lambda s, root: s.add(BOB, "yes", thread=root,
                                                   bot_id="B0OTHER001"),
                 None, OPEN, "bot post"),
                ("look-alike", lambda s, root: (
                    s.add(CHATBOT, "*Plan this?* PROD-5 Something else. Reply yes.",
                          thread=root, bot_id="B0CHATBOT1"),
                    s.add(BOB, "yes", thread=root)), None, OPEN, "Reply `yes PROD-5`"),
                ("edited-question", lambda s, root: (edit_the_question(s, root),
                                                     s.add(BOB, "yes", thread=root)),
                 None, EXPIRED, "was changed")):
            writes, sts, last = noop_with_acting_on(name, then, answer_at)
            check("a-yes-moves-nothing-with-acting-on:" + name,
                  (writes, sts, said in last), (([], [], []), [want_status], True))

        # REVIEW OF KIT-222, 1: a yes moves only the ticket the member was shown. The question
        # records its title, text, team, labels and state; a yes after any of them changed
        # moves nothing, closes the question and asks for a new request.
        for name, change in (
                ("retitled", lambda i: i.update(title="Something the member never saw")),
                ("rewritten", lambda i: i.update(description="Different work entirely.")),
                ("relabelled", lambda i: i.update(labels={"nodes": [{"name": "track:other"}]})),
                ("restated", lambda i: i.update(state={"id": "st-todo", "name": "Todo",
                                                       "type": "unstarted"})),
                ("moved-to-another-planned-team", lambda i: i.update(team={"key": "DEV"})),
                ("another-ticket-under-the-id", lambda i: i.update(id="uuid-other"))):
            c, s, t = world("plan-changed-" + name, act=True, team_keys=["PROD", "DEV"])
            t.team_states[("DEV", "Plan it")] = "st-plan-it"
            run(c, s, t)
            root = s.add(ALICE, "plan PROD-5")
            run(c, s, t)
            change(t.issues["PROD-5"])
            s.add(BOB, "yes", thread=root)
            run(c, s, t)
            check("a-plan-yes-after-the-ticket-changed-moves-nothing:" + name,
                  (t.moves, status_of(c), "changed since I asked" in posted(s)[-1],
                   "`plan PROD-5`" in posted(s)[-1]), ([], [REFUSED], True, True))
        check("the-plan-fingerprint-reads-what-it-hashes",
              ("description" in Q_ISSUE, "labels(first: 50) { nodes { name } }" in Q_ISSUE),
              (True, True))

        # 2: a ticket already in Plan it is refused before asking, in the yes path's words.
        c, s, t = world("plan-already-before-asking", act=True)
        run(c, s, t)
        t.issues["PROD-5"]["state"] = {"id": "st-plan-it", "name": "Plan it",
                                       "type": "unstarted"}
        s.add(ALICE, "plan PROD-5")
        run(c, s, t)
        check("already-in-plan-it-is-refused-before-asking, and says how to plan it again "
              "(KIT-244)",
              (posted(s), json.load(open(state_path(c)))["questions"]),
              (["PROD-5 is already in Plan it. To plan it again, type `replan PROD-5`."], {}))

        # 3: a yes binds to the terms its question stated. Settings changed since it was
        # asked (acting switched on or off, another Plan it state, another time limit) close
        # the question, and nothing acts.
        for name, asked, answered in (
                ("acting-switched-on", {}, {"act": True}),
                ("acting-switched-off", {"act": True}, {"act": False}),
                ("plan-it-state-renamed", {"act": True}, {"act": True,
                                                          "plan_it_state": "Ready to plan"}),
                ("time-limit-changed", {"act": True}, {"act": True,
                                                       "question_ttl_hours": 48})):
            c, s, t = world("plan-promise-" + name, **asked)
            run(c, s, t)
            root = s.add(ALICE, "plan PROD-5")
            run(c, s, t)
            s.add(BOB, "yes", thread=root)
            run(dict(c, **answered), s, t)
            check("a-plan-yes-under-other-settings-than-asked-acts-on-nothing:" + name,
                  (t.moves, status_of(c), "settings changed since" in posted(s)[-1],
                   sum("Confirmed" in x for x in posted(s))), ([], [REFUSED], True, 0))

        # 4: the move stands when its attribution comment fails, and the comment is owed:
        # said in the thread, kept in state, retried every pass, and loud until it lands.
        c, s, t = world("plan-comment-fails", act=True)
        run(c, s, t)
        root = s.add(ALICE, "plan PROD-5")
        run(c, s, t)
        real_comment = t.comment

        refusing_comment = kit.refusing_comment
        t.comment = refusing_comment
        s.add(BOB, "yes", thread=root)
        codes = [run(c, s, t)[0], run(c, s, t)[0]]
        owed = json.load(open(state_path(c))).get("owed")
        t.comment = real_comment
        codes += [run(c, s, t)[0], run(c, s, t)[0]]
        check("a-failed-plan-comment-is-owed-retried-and-loud-until-it-lands",
              (t.moves, status_of(c), codes,
               sum("could not be written; I will try again" in x for x in posted(s)),
               [o.get("ticket") for o in owed or []], len(t.comments),
               BOB in (t.comments or [("", "")])[0][1],
               json.load(open(state_path(c))).get("owed")),
              ([("uuid-5", "st-plan-it")], [DONE], [EXIT_ERROR, EXIT_ERROR, EXIT_OK, EXIT_OK],
               1, ["PROD-5"], 1, True, []))


def selftest():
    import pipeline_bridge
    return pipeline_bridge.split_module_selftest(os.path.abspath(__file__),
                                                 [cases_plan])


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        # As the module the bridge imports, never a copy of it named __main__, so its
        # cases and the pass share one copy of it (KIT-312).
        import pipeline_bridge_answers
        sys.exit(pipeline_bridge_answers.selftest())
    print("usage: pipeline_bridge_answers.py --selftest (the bridge imports this module)")
    sys.exit(EXIT_USAGE)
