#!/usr/bin/env python3
"""The Slack bridge acts on your own clear ask after a stop window (ACT-4, KIT-368).

    python3 scripts/pipeline_bridge_direct.py --selftest

A MODULE OF ITS OWN, as each of the bridge's concerns is (KIT-312). pipeline_bridge
imports its mixin, and the bridge's selftest runs its cases, end to end through one pass.
Its own selftest runs them too, and pins that it holds no HTTP call and no GraphQL write
of its own: what it does, it does through the bridge's own act_plan and act_replan.

WHY IT EXISTS. The owner's decision 1 of 2 Nov (option b): what you clearly asked for just
happens. With `direct_asks` on, a listed member's own DM message that the fixed reader
(ACT-3) reads as plan or replan, naming 1 to `direct_max_tickets` tickets, gets no
question. The bridge says what it will do, in `direct_grace_seconds`, and acts when that
window ends, unless they say anything else in that DM first.

WHAT IS HERE
  The gate      direct_ok: their own ask, plan or replan, the plain shape or the looser one
                with no `?` and no hedge, no note, within the ticket limit, read soon after
                it was sent, from a sender a yes would take, each ticket their own pick, none
                filed by the agent's own account and none carrying provenance:agent.
  The echo      direct_ask: each ticket through request()'s checks, collected, not posted;
                then one post of what it will do and when, its bind pinned word for word.
  The stop      direct_stop: any other message from them in that DM before the act stops
                all that waits there, but for an ask, a `yes <id>` or `no <id>`, a reply
                under a page and a short acknowledgement. A bare `no` answers no question.
  The act       act_directs, once a pass after every message: their message read again from
                Slack, read again as the same ask, each ticket read again, the settings and
                the action limit; then act_plan or act_replan, whose attribution says no
                question was asked.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pipeline_notify_local as notify  # noqa: E402
from pipeline_bridge_config import (ASK_AGAIN_BIND, DONE, EXIT_USAGE, OPEN,  # noqa: E402
                                    REFUSED, clock, key_of, label_names, ts_float)
from pipeline_bridge_transport import BridgeError  # noqa: E402
from pipeline_bridge_plans import PLAN_MOVES  # noqa: E402
from pipeline_bridge_requests import (NO_RE, REQUEST_RE, YES_RE,  # noqa: E402
                                      _ASK_YES_RE, account_refusal, ask_text,
                                      command_text, message_refusal, quoted_lines,
                                      read_ask, ticket_fingerprint)

# The verbs that may act with no question (the owner's decision 1): plan and replan, after
# the stop window, and pause, at once (KIT-351): a pause starts no work, and a resume undoes
# it. Approve, resume and cancel always ask.
DIRECT_VERBS = ("plan", "replan", "pause")
# What acts at once, with no echo and no window. It starts no work, so a plan the idea gate
# filed, which carries provenance:agent, is not asked about for it either.
AT_ONCE = ("pause",)
PENDING, STOPPED = "pending", "stopped"
# How long an ask's record is kept once it ended, so a late `stop` hears what happened.
DIRECT_KEEP_SECONDS = 24 * 3600
# An act the tracker refused is tried again on the next passes, as a refused yes is, and
# given up on after this many, said once.
DIRECT_TRIES = 3
# What a member says to stop. In the window it stops what waits and needs nothing more;
# after the act, the same words but `no` hear that it already happened.
STOP_WORDS_RE = re.compile(r"^(?:stop|cancel|wait|hold(?:\s+on)?|undo|don'?t|no+|nope)"
                           r"[\s.!]*$", re.IGNORECASE)
LATE_STOP_RE = re.compile(r"^(?:stop|cancel|wait|hold(?:\s+on)?|undo|don'?t)[\s.!]*$",
                          re.IGNORECASE)
# A short acknowledgement, which never stops what waits.
ACK_RE = re.compile(r"^(?:ok(?:ay)?|k|thanks|thank\s+you|thx|ty|yes|yep|yeah|great|cool|"
                    r"perfect|sounds\s+good|:\+1:|:thumbsup:|:ok_hand:|\U0001F44D|"
                    r"\U0001F44C)[\s.!]*$", re.IGNORECASE)

# THE ECHO'S BIND, pinned word for word: when, what will happen, and each ticket with its
# title, from the bridge's own reads; then how to stop it. Its what-will-happen slot holds
# one of the four clauses after it (THEN_SLOTS), for one ticket or several.
DIRECT_BIND = "In %d seconds %s: %s. Say stop to cancel."
DIRECT_PLAN_THEN_BIND = "I'll hand it to the planner"
DIRECT_PLAN_EACH_THEN_BIND = "I'll hand each to the planner"
DIRECT_REPLAN_THEN_BIND = "I'll hand it back to the planner for a new plan"
DIRECT_REPLAN_EACH_THEN_BIND = "I'll hand each back to the planner for a new plan"
# RP-2 (KIT-372): a re-plan of one idea with a note says it goes with it, quoted line by line
# as the planner will read it, so the writer never words it.
DIRECT_NOTE_BIND = ("In %d seconds %s: %s, with your note as you wrote it:\n%s\nSay stop to "
                    "cancel.")
# What the bridge says of an ask in a stop, a refusal or a failure, and of one already done.
WHAT_WAITS = {"plan": "hand %s to the planner", "pause": "pause %s",
              "replan": "hand %s back to the planner for a new plan"}
WHAT_DONE = {"plan": "handed %s to the planner", "pause": "paused %s",
             "replan": "handed %s back to the planner for a new plan"}


def listing(items):
    """PROD-6 “Light mode”, PROD-7 “Dark mode” and PROD-8 “Fonts”."""
    named = ["%s “%s”" % (ident, notify.safe_title(title or "")) for ident, title in items]
    return named[0] if len(named) == 1 else "%s and %s" % (", ".join(named[:-1]), named[-1])


def direct_shape(read, max_tickets):
    """True when a read ask may act with no question by its words alone: plan, replan or
    pause, the plain shape or the looser one with no `?` and no hedge, and 1 to
    max_tickets tickets. A note (RP-2, KIT-372) goes with a re-plan of one idea: with
    several, it goes the way it went before, where it is refused aloud."""
    return bool(read) and (read["verb"] in DIRECT_VERBS
                           and (not read["note"] or len(read["ids"]) == 1)
                           and (read["shape"] == "plain"
                                or not (read["question"] or read["hedge"]))
                           and 1 <= len(read["ids"]) <= max_tickets)


def just_after(ts):
    """A Slack ts one microsecond on, from its digits: a float would round its last one."""
    whole, _dot, frac = (ts or "0").partition(".")
    n = int(whole) * 10 ** 6 + int((frac + "000000")[:6]) + 1
    return "%d.%06d" % divmod(n, 10 ** 6)


class DirectMixin(object):
    """Pass's direct asks (ACT-4)."""

    def direct_first(self, channel, msg):
        """ACT-4's way in, before the strict grammar: a member's own top-level DM message, read by
        ACT-3's fixed rules from its own text, so a `?` the command line would drop still
        asks. True when it was handled here; False sends it the way it went before. A
        `yes, plan PROD-6` is an answer, and an edited message is refused where it was."""
        if (msg.get("bot_id") or msg.get("app_id") or msg.get("subtype")
                or not msg.get("user") or msg.get("edited")):
            return False
        text = ask_text(msg, self.me["user_id"])
        read = read_ask(text)
        if read is None or not read["ids"] or _ASK_YES_RE.match(text):
            return False
        if not self.direct_ok(channel, msg, read):
            return False
        self.direct_ask(channel, msg, read)
        rec = self.state["handled"].get(key_of(channel, msg.get("ts")))
        if isinstance(rec, dict):
            rec["read"] = dict((k, read[k]) for k in ("shape", "question", "hedge", "note"))
        return True

    def direct_ok(self, channel, msg, read):
        """True when a member's own ask may act after the stop window with no question. False
        sends it the way it went before: a question, or ACT-3b's combined one."""
        cfg = self.cfg
        if not (cfg.get("direct_asks") and cfg["act"]):
            return False
        # A note goes with a re-plan of one idea (RP-2, KIT-372), and is echoed with it.
        if not direct_shape(read, cfg["direct_max_tickets"]):
            return False
        # Judged by when it was SENT: one first read long after, by a Mac that slept, asks.
        if self.now - ts_float(msg.get("ts")) > cfg["direct_max_age_minutes"] * 60:
            return False
        if message_refusal(msg, self.me) or account_refusal(self.account(msg["user"]),
                                                             self.me, cfg):
            return False
        if not all(self.picked_by_member(channel, msg, i) for i in read["ids"]):
            return False
        if read["verb"] in AT_ONCE:
            # A pause starts no work, so who filed the plan does not matter to it.
            return True
        # Words the agent's own account wrote may come from text other people wrote: a
        # ticket it filed, or one carrying provenance:agent, is asked about. Without the
        # account's id nothing can be checked, so it asks too.
        agent_id, _why = self.tracker.agent_user(cfg["agent_user_name"])
        if not agent_id:
            return False
        for ident in read["ids"]:
            # A ticket it could not read is asked about: who filed it was never checked.
            issue = self.tracker.issue(ident)
            if not issue:
                return False
            if ((issue.get("creator") or {}).get("id") == agent_id or "provenance:agent"
                    in set((n or "").lower() for n in label_names(issue))):
                return False
        return True

    def direct_ask(self, channel, msg, read):
        """Each ticket through request()'s checks, collected; then one echo for those that
        pass, with the rest named as left out, or one line saying why none can."""
        verb = read["verb"]
        self._collect = collected = []
        try:
            for ident in read["ids"]:
                self.request(channel, msg, verb, ident, note=read["note"])
        finally:
            self._collect = None
        asks = [c for c in collected if "record" in c]
        seen = set(c["ticket"] for c in collected)
        left = (["%s: %s" % (c["ticket"], c["left"]) for c in collected if "left" in c]
                + ["%s: I couldn't look it up now." % i for i in read["ids"] if i not in seen])
        if not asks:
            self.post(channel, None, "I didn't act on that. %s" % " ".join(left),
                      kind="direct-refused", facts={"tickets": read["ids"]})
            self.mark(channel, msg, "refused: direct, every ticket left out")
            self.counts["refused"] += 1
            return
        items = [(c["ticket"], (c.get("issue") or {}).get("title")) for c in asks]
        tickets = [i for i, _t in items]
        wait = 0 if verb in AT_ONCE else self.cfg["direct_grace_seconds"]
        if not wait:
            # Nothing waits, so nothing is echoed: it acts at this pass's end, after every
            # message, and its own reply says what it did. One left out is said here.
            posted = msg.get("ts")
            if left:
                self.post(channel, None, "Left out: %s" % " ".join(left),
                          kind="direct-left-out", facts={"tickets": [
                              i for i in read["ids"] if i not in tickets]})
        else:
            if verb == "plan":
                then = (DIRECT_PLAN_THEN_BIND if len(items) == 1
                        else DIRECT_PLAN_EACH_THEN_BIND)
            else:
                then = (DIRECT_REPLAN_THEN_BIND if len(items) == 1
                        else DIRECT_REPLAN_EACH_THEN_BIND)
            # RP-2: a re-plan's note, as request() checked it, goes with it and says so.
            note = asks[0]["record"].get("note") if len(asks) == 1 else ""
            bind = (DIRECT_NOTE_BIND % (wait, then, listing(items), quoted_lines(note))
                    if note else DIRECT_BIND % (wait, then, listing(items)))
            body = "Got it." + (" Left out: %s" % " ".join(left) if left else "")
            posted = self.post(channel, None, body, kind="direct-echo", bind=bind, join="\n",
                               plain=body, facts={"tickets": tickets, "action": verb,
                                                  "left_out": len(left)})
        self.state["direct"][key_of(channel, msg.get("ts"))] = {
            "channel": channel, "user": msg["user"], "msg_ts": msg.get("ts"), "verb": verb,
            "ids": list(read["ids"]), "tickets": tickets, "echo_ts": posted,
            "note": read["note"],
            "status": PENDING, "due_at": self.now + wait,
            "records": dict((c["ticket"], c["record"]) for c in asks)}
        self.mark(channel, msg, "direct: %s %s" % (verb, ", ".join(tickets)))
        self.say("  direct: %s %s, acting at %s unless stopped" % (
            verb, ", ".join(tickets), clock(self.now + wait, self.now)))

    @staticmethod
    def what_waits(d):
        tickets = d["tickets"]
        named = (tickets[0] if len(tickets) == 1
                 else "%s and %s" % (", ".join(tickets[:-1]), tickets[-1]))
        return WHAT_WAITS[d["verb"]] % named

    def direct_stop(self, channel, msg):
        """A message from a member in a DM where their ask waits: anything but an ask, a
        `yes <id>`, a `no <id>` naming another ticket, a reply under a page or a short
        acknowledgement stops all that waits there, said once; `no <id>` naming a ticket that
        waits stops it too. After the act, a stop word hears what already happened. True
        when the message was only that, and needs nothing more."""
        if channel not in getattr(self, "dm_channels", ()) or not msg.get("user") or (
                msg.get("bot_id") or msg.get("app_id") or msg.get("subtype")):
            return False
        mine = [d for d in self.state["direct"].values()
                if d.get("channel") == channel and d.get("user") == msg["user"]
                and ts_float(d.get("msg_ts")) < ts_float(msg.get("ts"))]
        if not mine:
            return False
        text = command_text(msg, self.me["user_id"])
        waiting = [d for d in mine if d.get("status") == PENDING]
        if not waiting:
            done = [d for d in mine if d.get("status") == DONE and not d.get("late_said")]
            if not (done and LATE_STOP_RE.match(text)):
                return False
            for d in done:
                d["late_said"] = True
            self.post(channel, None, "That already happened: I %s, at %s." % (
                "; and ".join(WHAT_DONE[d["verb"]] % ", ".join(d["tickets"]) for d in done),
                clock(max(d.get("acted_at") or self.now for d in done), self.now)),
                kind="direct-already-done", facts={"tickets": [
                    t for d in done for t in d["tickets"]]})
            self.mark(channel, msg, "direct: already done")
            return True
        thread = msg.get("thread_ts")
        reply = bool(thread and thread != msg.get("ts"))
        yes, no = YES_RE.match(text), NO_RE.match(text)
        named = (no.group(1) or "").upper() if no else ""
        # `no <id>` naming a ticket that waits here refuses that very act, so it stops it.
        # Naming another ticket, it is that ticket's answer, and stops nothing.
        refused = bool(named) and named in set(t.upper() for d in waiting for t in d["tickets"])
        under_page = reply and isinstance(self.dm_posts.get(key_of(channel, thread)), dict)
        if (REQUEST_RE.match(text) or read_ask(ask_text(msg, self.me["user_id"]))
                or (yes and yes.group(1)) or (named and not refused) or under_page
                or ACK_RE.match(text)):
            return False
        for d in waiting:
            d.update(status=STOPPED, stopped_by=msg.get("ts"), ended_at=self.now)
        self.post(channel, None, "Stopped. I won't %s." % "; nor ".join(
            self.what_waits(d) for d in waiting), kind="direct-stopped",
            facts={"tickets": [t for d in waiting for t in d["tickets"]]})
        self.say("  direct: stopped %s, by %s" % (
            ", ".join(t for d in waiting for t in d["tickets"]), msg["user"]))
        open_here = [q for q in self.state["questions"].values()
                     if q.get("status") == OPEN and q.get("channel") == channel]
        if no and not named and open_here:
            # A bare `no` stops what waits, and never answers a question here.
            self.post(channel, None, "If you meant to answer a question here, say `no "
                      "<ticket id>`.", kind="direct-no-named-none")
        # A `no <id>` that stopped its own ask needs nothing more, unless a question about
        # that ticket is open here too: then it is that question's answer as well.
        if STOP_WORDS_RE.match(text) or (refused and not any(
                (q.get("ticket") or "").upper() == named for q in open_here)):
            self.mark(channel, msg, "direct: stopped")
            return True
        return False

    def act_directs(self):
        """Each ask whose window has ended, acted on now; then the records past their keep
        dropped. Run once a pass, after every message it read, so a stop sent in time is
        always seen first."""
        for key, d in sorted(self.state["direct"].items(), key=lambda kv: kv[1]["due_at"]):
            if d.get("status") == PENDING and self.now >= d["due_at"]:
                try:
                    self.act_direct(key, d)
                except BridgeError as exc:
                    # What it did stands. The rest is tried again next pass, with every read
                    # again, and the pass ends loud; the last try says it gave up.
                    d["tries"] = d.get("tries", 0) + 1
                    if d["tries"] >= DIRECT_TRIES:
                        d.update(status=REFUSED, ended_at=self.now, why=str(exc)[:200])
                        for ident in d["tickets"]:
                            q = self.direct_question(d, ident, exists=True)
                            if q and q.get("status") == OPEN and not q.get("replanning"):
                                q.update(status=REFUSED, closed_by="Linear refused the act")
                        # Linear's own words stay in the log: they are not the bridge's.
                        self.post(d["channel"], None, "I couldn't %s: Linear refused it %d "
                                  "times." % (self.what_waits(d), DIRECT_TRIES),
                                  kind="direct-failed",
                                  bind=ASK_AGAIN_BIND % (d["verb"], d["tickets"][0]),
                                  join=" ", facts={"tickets": d["tickets"]})
                        self.say("  direct: gave up on %s after %d tries: %s"
                                 % (", ".join(d["tickets"]), DIRECT_TRIES, str(exc)[:200]))
                    self.save()
                    raise
                self.save()
        horizon = self.now - DIRECT_KEEP_SECONDS
        self.state["direct"] = dict(
            (k, d) for k, d in self.state["direct"].items()
            if d.get("status") == PENDING or d.get("ended_at", 0) >= horizon)

    def act_direct(self, key, d):
        """At the window's end: the member's message from Slack again, the same ask, each
        ticket as it was echoed, the settings and the action limit; any of those changed and
        it acts on nothing, saying which. Then each ticket through act_plan, act_replan or,
        for a pause, act_plan_move."""
        channel, verb = d["channel"], d["verb"]
        found = [m for m in self.slack.recent(channel, just_after(d["msg_ts"]), 1)
                 if m.get("ts") == d["msg_ts"]]
        msg = found[0] if found else None
        why = None
        if msg is None or msg.get("subtype") == "tombstone":
            why = "your message was deleted"
        elif msg.get("edited"):
            why = "your message was edited after I read it"
        elif message_refusal(msg, self.me) or account_refusal(self.account(msg["user"]),
                                                               self.me, self.cfg):
            why = "I can't take your message as an ask any more"
        else:
            read = read_ask(ask_text(msg, self.me["user_id"]))
            if (not read or read["verb"] != verb or read["ids"] != d["ids"]
                    or read["note"] != d.get("note", "")):
                why = "your message no longer reads as the same ask"
        if why is None:
            for ident in d["tickets"]:
                if self.direct_question(d, ident, exists=True) is not None:
                    continue                  # tried already: it is the act's own now
                rec = d["records"][ident]
                # A plan move reads its plan again itself, and moves it only in the state the
                # ask found it in. Its record holds the plan as the epic read saw it, with none
                # of the plan's own text, so the ticket read would not match it.
                if verb not in PLAN_MOVES:
                    issue = self.tracker.issue(ident)
                    if not issue or ticket_fingerprint(issue) != rec.get("fingerprint"):
                        why = ("%s changed since I said so (its title, text, team, labels or "
                               "state)" % ident)
                        break
                if rec.get("promised") != self.promised(verb):
                    why = "my settings changed since I said so"
                    break
        if why is None and self.actions_left() < len(d["tickets"]):
            why = ("I have reached my limit of %d actions an hour"
                   % self.cfg["max_actions_per_hour"])
        if why is not None:
            self.post(channel, None, "I didn't %s: %s. Nothing was changed."
                      % (self.what_waits(d), why), kind="direct-refused",
                      bind=ASK_AGAIN_BIND % (verb, d["tickets"][0]), join=" ",
                      facts={"tickets": d["tickets"]})
            d.update(status=REFUSED, ended_at=self.now, why=why)
            self.counts["refused"] += 1
            self.say("  direct: acted on nothing for %s: %s" % (", ".join(d["tickets"]), why))
            return
        for ident in d["tickets"]:
            q = self.direct_question(d, ident)
            if q.get("status") != OPEN or q.get("replanning"):
                continue                      # done on a try before, or a re-plan under way
            if verb == "replan":
                self.act_replan(channel, None, q, msg)
            elif verb in PLAN_MOVES:
                self.act_plan_move(channel, None, q, msg)
            else:
                self.act_plan(channel, None, q, msg)
            self.save()
        d.update(status=DONE, acted_at=self.now, ended_at=self.now)

    def direct_question(self, d, ident, exists=False):
        """A ticket's record for the act, kept with the questions, so a re-plan's later
        passes find it as they find any: one made on a try before, else a new one (None with
        `exists`). `direct` keeps every yes and no away from it: it asked nothing."""
        key = "%s#%s" % (key_of(d["channel"], d["echo_ts"]), ident)
        q = self.state["questions"].get(key)
        if q is None and not exists:
            q = self.state["questions"][key] = dict(
                d["records"][ident], status=OPEN, ts=d["echo_ts"], direct=True,
                grace=self.cfg["direct_grace_seconds"])
        return q


# ══════════════════════════════════════════════════════════════════════════════════════
# Selftest — offline. This module's cases run in the bridge's own selftest, end to end
# through one pass, and in its own, which pins what the module may reach (KIT-407).
# ══════════════════════════════════════════════════════════════════════════════════════
def cases_direct(kit):
    """Your own clear ask acts after the stop window (ACT-4, KIT-368): the echo and its
    window, the stop and what never stops, the reads again before the act, and each way
    an ask still gets its question."""
    import json
    from pipeline_bridge import (ALICE, DM, EXAMPLE_CONFIG, BridgeError, state_path,
                                 validate_config)
    AGAIN = ASK_AGAIN_BIND
    with kit as tmp:
        check, now, outcome, run = kit.take("check", "now", "outcome", "run")
        dmworld, dm_lines, tlworld = kit.take("dmworld", "dm_lines", "tlworld")
        tl_says, tl_root = kit.take("tl_says", "tl_root")
        on = dict(act=True, agent_user_name="Agent", direct_asks=True)
        echo = "In 90 seconds I'll hand it to the planner: PROD-6 “Light mode”. Say stop to cancel."
        moved = [("uuid-6", "st-plan-it")]

        def asked(name, text, at=30, **over):
            c, s, t = dmworld(name, **dict(on, **over))
            ts = s.add(ALICE, text, channel=DM, at=now + at)
            run(c, s, t, at=now + 60)
            return c, s, t, ts

        def direct_of(c):
            return list(json.load(open(state_path(c)))["direct"].values())

        # 1. YOUR CLEAR ASK: one echo, nothing in the window, then the act, whose comment says
        #    no question was asked; its record is never one a yes or no answers.
        def case_acts():
            c, s, t, _ts = asked("dc-acts", "plan PROD-6")
            said = dm_lines(s)[-1]
            run(c, s, t, at=now + 120)
            during = list(t.moves)
            run(c, s, t, at=now + 150)
            qs = list(json.load(open(state_path(c)))["questions"].values())
            return (said, during, t.moves, dm_lines(s)[-1],
                    "who asked for it in Slack at" in t.comments[-1][1]
                    and "with no question asked" in t.comments[-1][1],
                    [(q.get("direct"), q.get("status")) for q in qs],
                    [d["status"] for d in direct_of(c)])
        check("dc-your-clear-ask-is-echoed-and-acts-when-the-window-ends", outcome(case_acts),
              ("Got it.\n" + echo, [], moved,
               "Moved PROD-6 to Plan it for <@%s>. The planner starts on it within a few "
               "minutes. You'll hear if planning can't start or ends without a plan." % ALICE, True, [(True, "done")], ["done"]))

        # 2. A STOP: `stop` in the window stops it, said once, and nothing moves; any other
        #    message stops it too, and still goes its own way.
        def stopped_by(name, line):
            c, s, t, _ts = asked(name, "plan PROD-6")
            s.add(ALICE, line, channel=DM, at=now + 70)
            run(c, s, t, at=now + 80)
            run(c, s, t, at=now + 200)
            return t.moves, [x for x in dm_lines(s) if x.startswith("Stopped.")], dm_lines(s)[-1]
        check("dc-stop-in-the-window-acts-on-nothing-and-says-what-stopped",
              outcome(lambda: stopped_by("dc-stop", "stop")),
              ([], ["Stopped. I won't hand PROD-6 to the planner."],
               "Stopped. I won't hand PROD-6 to the planner."))
        got = outcome(lambda: stopped_by("dc-chat", "hmm, what about the fonts"))
        check("dc-any-other-message-stops-it-and-is-still-read",
              (got[0], got[1], got[2] not in got[1]) if isinstance(got, tuple) else got,
              ([], ["Stopped. I won't hand PROD-6 to the planner."], True))

        # 3. WHAT NEVER STOPS IT: a short acknowledgement, `yes <id>` or `no <id>`, and a new
        #    ask, which is its own.
        def not_stopped(name, line):
            c, s, t, _ts = asked(name, "plan PROD-6")
            s.add(ALICE, line, channel=DM, at=now + 70)
            run(c, s, t, at=now + 80)
            run(c, s, t, at=now + 200)
            return ("uuid-6", "st-plan-it") in t.moves, any(
                x.startswith("Stopped.") for x in dm_lines(s))
        check("dc-an-ack-a-named-yes-or-no-or-a-new-ask-never-stops-it",
              [outcome(lambda: not_stopped("dc-keep-%d" % i, line)) for i, line in enumerate(
                  ("ok", "thanks!", ":+1:", "yes PROD-9", "no PROD-9", "plan PROD-5"))],
              [(True, False)] * 6)

        # 4. A BARE `no` stops what waits and never answers a question; after the act, a
        #    stop hears that it already happened.
        def case_bare_no():
            c, s, t = dmworld("dc-no", **on)
            s.add(ALICE, "can you plan PROD-5?", channel=DM, at=now + 10)
            run(c, s, t, at=now + 20)
            s.add(ALICE, "plan PROD-6", channel=DM, at=now + 30)
            run(c, s, t, at=now + 60)
            s.add(ALICE, "no", channel=DM, at=now + 70)
            run(c, s, t, at=now + 80)
            run(c, s, t, at=now + 200)
            qs = json.load(open(state_path(c)))["questions"].values()
            return (t.moves, sorted((q["ticket"], q["status"]) for q in qs), dm_lines(s)[-2:])
        check("dc-a-bare-no-stops-it-and-answers-no-question", outcome(case_bare_no),
              ([], [("PROD-5", OPEN)], ["Stopped. I won't hand PROD-6 to the planner.",
                                        "If you meant to answer a question here, say `no "
                                        "<ticket id>`."]))

        # `no <id>` NAMING THE TICKET THAT WAITS refuses that very act: it stops it, says
        # so once, and is not then told there is no question to answer, nor asked to name
        # a ticket because another one's question is open. When that ticket has an open
        # question too, the same `no` declines it as well (Stage E on #304).
        def case_no_this_one(between):
            c, s, t = dmworld("dc-no-this-%d" % len(between), **on)
            s.add(ALICE, "can you plan PROD-5?", channel=DM, at=now + 10)
            run(c, s, t, at=now + 20)
            s.add(ALICE, "plan PROD-6", channel=DM, at=now + 30)
            run(c, s, t, at=now + 40)
            for n, line in enumerate(between):
                s.add(ALICE, line, channel=DM, at=now + 45 + n)
            run(c, s, t, at=now + 50)
            before = len(dm_lines(s))
            s.add(ALICE, "no PROD-6", channel=DM, at=now + 70)
            run(c, s, t, at=now + 80)
            run(c, s, t, at=now + 200)
            qs = json.load(open(state_path(c)))["questions"].values()
            return (t.moves, dm_lines(s)[before:][:1], len(dm_lines(s)) - before,
                    sorted((q["ticket"], q["status"]) for q in qs if not q.get("direct")),
                    [d["status"] for d in direct_of(c)])
        check("dc-no-naming-the-waiting-ticket-stops-it-and-says-only-that",
              [outcome(lambda: case_no_this_one(())),
               outcome(lambda: case_no_this_one(("can you plan PROD-6?",)))],
              [([], ["Stopped. I won't hand PROD-6 to the planner."], 1,
                [("PROD-5", OPEN)], [STOPPED]),
               ([], ["Stopped. I won't hand PROD-6 to the planner."], 2,
                [("PROD-5", OPEN), ("PROD-6", "declined")], [STOPPED])])

        def case_late_stop():
            c, s, t, _ts = asked("dc-late", "plan PROD-6")
            run(c, s, t, at=now + 150)
            s.add(ALICE, "stop", channel=DM, at=now + 160)
            run(c, s, t, at=now + 170)
            s.add(ALICE, "stop", channel=DM, at=now + 180)
            run(c, s, t, at=now + 190)
            return t.moves, [x for x in dm_lines(s) if x.startswith("That already happened")]
        got = outcome(case_late_stop)
        check("dc-a-stop-after-the-act-hears-it-already-happened-once",
              (got[0], [x.split(", at ")[0] for x in got[1]]) if isinstance(got, tuple)
              else got, (moved, ["That already happened: I handed PROD-6 to the planner"]))

        # 5. READ AGAIN BEFORE THE ACT: the message deleted or edited, the ticket changed, the
        #    settings changed, the action limit reached: it acts on nothing, and says which.
        def refused_at_act(name, change):
            c, s, t, ts = asked(name, "plan PROD-6")
            change(c, s, t, ts)
            run(c, s, t, at=now + 150)
            return t.moves, dm_lines(s)[-1]

        def deleted(_c, s, _t, ts):
            s.messages[:] = [m for m in s.messages if m.get("ts") != ts]

        def edited(_c, s, _t, ts):
            [m for m in s.messages if m.get("ts") == ts][0]["edited"] = {"user": ALICE}

        def retitled(_c, _s, t, _ts):
            t.issues["PROD-6"]["title"] = "Light mode, but bigger"

        def reset(c, _s, _t, _ts):
            c["question_ttl_hours"] = 48

        def busy(c, _s, _t, _ts):
            st = json.load(open(state_path(c)))
            st["acted_at"] = [now + 100] * c["max_actions_per_hour"]
            json.dump(st, open(state_path(c), "w"))
        again = AGAIN % ("plan", "PROD-6")
        check("dc-read-again-before-the-act-a-change-acts-on-nothing-and-says-which",
              [outcome(lambda: refused_at_act("dc-re-%s" % f.__name__, f))
               for f in (deleted, edited, retitled, reset, busy)],
              [([], "I didn't hand PROD-6 to the planner: %s. Nothing was changed. %s"
                % (why, again)) for why in (
                  "your message was deleted", "your message was edited after I read it",
                  "PROD-6 changed since I said so (its title, text, team, labels or state)",
                  "my settings changed since I said so",
                  "I have reached my limit of 90 actions an hour")])

        # 6. WHAT STILL GETS ITS QUESTION: direct asks off; a `?` or a hedge; a note; a
        #    ticket the agent's account filed, that carries provenance:agent, or that it
        #    could not read; a message first read too long after it was sent; a ticket the
        #    tech lead named first; more tickets than the setting, which get one combined
        #    question.
        def asks_instead(name, text, setup=None, at=30, **over):
            c, s, t = dmworld(name, **dict(on, **over))
            if setup:
                setup(t)
            s.add(ALICE, text, channel=DM, at=now + at)
            run(c, s, t, at=now + 60)
            run(c, s, t, at=now + 200)
            return (bool(direct_of(c)), t.moves,
                    any(x.startswith("*Plan this?*") or x.startswith("*Plan these?*")
                        for x in dm_lines(s)))

        def agents(t_):
            t_.issues["PROD-6"]["creator"] = {"id": "agent-1", "displayName": "Agent"}

        def labelled(t_):
            t_.issues["PROD-6"]["labels"] = {"nodes": [{"name": "provenance:agent"}]}

        def four(t_):
            # Four tickets it can read, so only the ticket limit decides: one it can't
            # read is asked about whatever the limit.
            for n in (7, 8):
                t_.issues["PROD-%d" % n] = dict(t_.issues["PROD-6"], id="uuid-%d" % n,
                                                identifier="PROD-%d" % n, title="Mode %d" % n)

        def unread_once(t_):
            # The agent's ticket, whose first read fails: who filed it was never checked,
            # so it is asked about, though a later read finds it (Stage E, #304).
            agents(t_)
            real, reads = t_.issue, []

            def issue(ident):
                reads.append(ident)
                return None if reads.count(ident) == 1 else real(ident)
            t_.issue = issue
        check("dc-each-of-these-gets-its-question-and-nothing-acts-with-no-yes",
              [outcome(lambda: asks_instead(*a, **k)) for a, k in (
                  (("dc-q-off", "plan PROD-6"), {"direct_asks": False}),
                  (("dc-q-mark", "can you plan PROD-6?"), {}),
                  (("dc-q-hedge", "plan PROD-6, I think"), {}),
                  (("dc-q-agent", "plan PROD-6", agents), {}),
                  (("dc-q-label", "plan PROD-6", labelled), {}),
                  (("dc-q-unread", "plan PROD-6", unread_once), {}),
                  (("dc-q-old", "plan PROD-6", None, -660), {}),
                  (("dc-q-four", "plan PROD-6, PROD-5, PROD-7 and PROD-8", four), {}))],
              [(False, [], True)] * 8)

        def case_tech_leads_pick():
            c, s, t = tlworld("dc-tl", **on)
            s.add(ALICE, "what should we do next?", channel=DM, at=now + 10)
            run(c, s, t, at=now + 20)
            tl_says(s, tl_root(c), "PROD-6 looks ready to plan.", at=now + 25)
            run(c, s, t, at=now + 30)
            s.add(ALICE, "plan PROD-6", channel=DM, at=now + 40)
            run(c, s, t, at=now + 60)
            run(c, s, t, at=now + 200)
            return bool(direct_of(c)), t.moves, "`yes PROD-6`" in dm_lines(s)[-1]
        check("dc-a-ticket-the-tech-lead-named-first-gets-its-question",
              outcome(case_tech_leads_pick), (False, [], True))

        # RP-2 (KIT-372): a re-plan of one idea with a note acts after the window too: the
        # echo shows the note as the planner will read it, and the attribution comment carries
        # it under the planner's own line. With several ideas a note is refused, and with a
        # message changed in the window it acts on nothing.
        def case_note(line="replan PROD-6 note: the gate is fixed", edit=None, name="dc-note"):
            c, s, t = dmworld(name, **on)
            t.issues["PROD-7"] = dict(t.issues["PROD-6"], id="uuid-7", identifier="PROD-7",
                                      title="Dark mode")
            for idea, run_ in (("PROD-6", "PROD-50"), ("PROD-7", "PROD-60")):
                t.in_plan_it(idea, trigger="T-" + idea)
                t.add_run(idea, run_, "completed", trigger="T-" + idea)
            ts = s.add(ALICE, line, channel=DM, at=now + 30)
            run(c, s, t, at=now + 60)
            echoed = dm_lines(s)[-1]
            for m in s.messages:
                if edit and m.get("ts") == ts:
                    m["text"] = edit                 # read differently at the act, unedited
            run(c, s, t, at=now + 200)
            said = [b for i, b in t.comments if i == "uuid-6"]
            return (echoed.split("\n", 1)[-1], t.moves,
                    said[-1].split("\n\n", 1)[-1] if said else dm_lines(s)[-1])
        from pipeline_plan_poller import OWNER_NOTE_LINE
        check("dc-a-replan-with-a-note-is-echoed-with-it-and-the-comment-carries-it",
              [outcome(case_note),
               outcome(lambda: case_note(edit="replan PROD-6 note: something else",
                                         name="dc-note-edited")),
               outcome(lambda: case_note("replan PROD-6 and PROD-7 note: the gate is fixed",
                                         name="dc-note-two"))],
              [("In 90 seconds I'll hand it back to the planner for a new plan: PROD-6 “Light "
                "mode”, with your note as you wrote it:\n> the gate is fixed\nSay stop to "
                "cancel.", [("uuid-6", "st-backlog"), ("uuid-6", "st-plan-it")],
                "%s\n> the gate is fixed" % OWNER_NOTE_LINE),
               ("In 90 seconds I'll hand it back to the planner for a new plan: PROD-6 “Light "
                "mode”, with your note as you wrote it:\n> the gate is fixed\nSay stop to "
                "cancel.", [], "I didn't hand PROD-6 back to the planner for a new plan: your "
                "message no longer reads as the same ask. Nothing was changed. %s"
                % (AGAIN % ("replan", "PROD-6"))),
               ("I didn't ask about PROD-6, PROD-7: a note goes with one idea at a time. "
                "Ask again with one ticket and its note.", [],
                "I didn't ask about PROD-6, PROD-7: a note goes with one idea at a time. "
                "Ask again with one ticket and its note.")])

        # A re-plan of an idea whose plan still stands is a cancel and a re-plan in one, which
        # never acts on the direct path: the cancel is asked on its own, and the member hears
        # why nothing acted. Later passes move nothing (KIT-444).
        def case_plan_stands():
            c, s, t = dmworld("dc-plan-stands", **on)
            t.in_plan_it("PROD-6", trigger="T1")
            t.add_run("PROD-6", "PROD-50", "completed", trigger="T1")
            t.add_epic("PROD-70", (("PROD-71", ()),))
            t.plans["PROD-50"] = [t.issues["PROD-70"]]
            s.add(ALICE, "replan PROD-6", channel=DM, at=now + 30)
            for at in (60, 200, 400):
                run(c, s, t, at=now + at)
            return (t.moves, bool(direct_of(c)),
                    dm_lines(s)[-2].startswith("*Cancel this plan and plan the idea again?*"),
                    dm_lines(s)[-1])
        check("dc-a-cancel-and-replan-never-acts-on-the-direct-path", outcome(case_plan_stands),
              ([], False, True, "I didn't act on that. PROD-6: its plan still stands, so I "
                                "asked about cancelling it on its own."))

        # 7. SEVERAL TICKETS: up to the setting, one echo and one act each; the line moves
        #    with the setting.
        def case_two():
            c, s, t, _ts = asked("dc-two", "plan PROD-6 and PROD-5")
            said = dm_lines(s)[-1]
            run(c, s, t, at=now + 150)
            return said.split("\n")[-1], sorted(t.moves)
        check("dc-two-tickets-one-echo-and-each-acts",
              outcome(case_two),
              ("In 90 seconds I'll hand each to the planner: PROD-6 “Light mode” and PROD-5 "
               "“Dark mode !channel”. Say stop to cancel.",
               [("uuid-5", "st-plan-it"), moved[0]]))
        check("dc-the-ticket-limit-is-a-setting",
              outcome(lambda: asks_instead("dc-q-four-ok", "plan PROD-6, PROD-5, PROD-7 and "
                                           "PROD-8", four, direct_max_tickets=4)[0]), True)

        # 8. A RE-PLAN: its own words, and the act moves each idea out and back in.
        def case_replan(name, line, ideas):
            c, s, t = dmworld(name, **on)
            for n, idea in enumerate(ideas):
                t.in_plan_it(idea, trigger="T%d" % n)
                t.add_run(idea, "PROD-5%d" % n, "completed", trigger="T%d" % n)
            s.add(ALICE, line, channel=DM, at=now + 30)
            run(c, s, t, at=now + 60)
            said = dm_lines(s)[-1].split("\n")[-1]
            run(c, s, t, at=now + 150)
            return said, sorted(t.moves), all("with no question asked" in b
                                              for _i, b in t.comments)
        check("dc-a-replan-says-so-and-moves-each-out-and-back-in",
              [outcome(lambda: case_replan("dc-rp", "replan PROD-6", ["PROD-6"])),
               outcome(lambda: case_replan("dc-rp-two", "replan PROD-6 and PROD-5",
                                           ["PROD-6", "PROD-5"]))],
              [("In 90 seconds I'll hand it back to the planner for a new plan: PROD-6 “Light "
                "mode”. Say stop to cancel.", [("uuid-6", "st-backlog"),
                                               ("uuid-6", "st-plan-it")], True),
               ("In 90 seconds I'll hand each back to the planner for a new plan: PROD-6 "
                "“Light mode” and PROD-5 “Dark mode !channel”. Say stop to cancel.",
                [("uuid-5", "st-backlog"), ("uuid-5", "st-plan-it"), ("uuid-6", "st-backlog"),
                 ("uuid-6", "st-plan-it")], True)])

        # 9. AN ACT LINEAR REFUSED: tried again next pass, as a refused yes is, and given up
        #    on after three, said once; a yes before the retry answers nothing. A re-plan cut
        #    between its two moves says how to finish it, as any re-plan does.
        def case_retry():
            c, s, t, _ts = asked("dc-retry", "plan PROD-6")
            t.move_raises = 1
            first = run(c, s, t, at=now + 150)[0]
            t.move_raises = None
            second = run(c, s, t, at=now + 210)[0]
            return first, second, t.moves, [d["status"] for d in direct_of(c)]
        check("dc-an-act-linear-refused-is-tried-again-next-pass", outcome(case_retry),
              (1, 0, moved, ["done"]))

        def case_partial():
            c, s, t, _ts = asked("dc-partial", "plan PROD-6 and PROD-5")
            t.move_raises = 2
            first = run(c, s, t, at=now + 150)[0]
            t.move_raises = None
            run(c, s, t, at=now + 210)
            return (first, t.moves, [x.split(" for ")[0] for x in dm_lines(s)
                                     if x.startswith("Moved") or "already in" in x])
        check("dc-a-retry-acts-only-on-what-is-left", outcome(case_partial),
              (1, [moved[0], ("uuid-5", "st-plan-it")],
               ["Moved PROD-6 to Plan it", "Moved PROD-5 to Plan it"]))

        def case_given_up():
            c, s, t, _ts = asked("dc-given-up", "plan PROD-6")
            t.move_raises = 1
            codes = [run(c, s, t, at=now + 150 + 60 * i)[0] for i in range(4)]
            qs = json.load(open(state_path(c)))["questions"].values()
            return (codes, t.moves, [d["status"] for d in direct_of(c)],
                    sum(x.startswith("I couldn't hand PROD-6 to the planner: Linear refused it "
                                     "3 times") for x in dm_lines(s)),
                    [q["status"] for q in qs if q.get("direct")])
        check("dc-an-act-refused-three-times-is-given-up-and-said-once", outcome(case_given_up),
              ([1, 1, 1, 0], [], ["refused"], 1, ["refused"]))

        def case_yes_between():
            c, s, t, _ts = asked("dc-yes-between", "plan PROD-6")
            t.move_raises = 1
            run(c, s, t, at=now + 150)
            t.move_raises = None
            s.add(ALICE, "yes", channel=DM, at=now + 160)
            run(c, s, t, at=now + 170)
            return dm_lines(s)[-2:], t.moves
        check("dc-a-yes-before-the-retry-answers-nothing-and-the-retry-acts",
              outcome(case_yes_between),
              (["There is no open question here to answer.",
                "Moved PROD-6 to Plan it for <@%s>. The planner starts on it within a few "
                "minutes. You'll hear if planning can't start or ends without a plan." % ALICE], moved))

        def case_cut():
            c, s, t = dmworld("dc-rp-cut", **on)
            t.in_plan_it("PROD-6", trigger="T1")
            t.add_run("PROD-6", "PROD-50", "completed", trigger="T1")
            s.add(ALICE, "replan PROD-6", channel=DM, at=now + 30)
            run(c, s, t, at=now + 60)
            t.move_raises = 2
            run(c, s, t, at=now + 150)
            t.move_raises = None
            run(c, s, t, at=now + 210)
            return [x for x in dm_lines(s) if "failed" in x], t.moves
        check("dc-a-replan-cut-mid-way-says-how-to-finish-once-and-moves-nothing-more",
              outcome(case_cut),
              (["I moved PROD-6 to Backlog, but moving it back into Plan it failed, so no "
                "planning run starts. To finish, type `plan PROD-6`."], [("uuid-6", "st-backlog")]))

        # 10. ITS SETTINGS: off by default; on, acting, a DM and the agent's account named;
        #    each number in its range.
        def errors(**over):
            try:
                validate_config(dict(EXAMPLE_CONFIG, state_dir=tmp, **over))
            except BridgeError as exc:
                return [line.strip()[2:] for line in str(exc).splitlines()[1:]
                        if "direct" in line]
            return []
        check("dc-settings-off-by-default-and-on-only-with-what-it-needs",
              ([e.split(":")[0] for e in errors(direct_asks=True, act=False, dm_user_ids=[],
                                                agent_user_name="")],
               errors(), len(errors(direct_grace_seconds=5, direct_max_tickets=11,
                                    direct_max_age_minutes=0))),
              (["config 'direct_asks' is on, but 'act' is off",
                "config 'direct_asks' is on, but 'dm_user_ids' is empty",
                "config 'direct_asks' is on, so it needs 'agent_user_name'"], [], 3))

        # 11. A PAUSE ACTS AT ONCE (KIT-351, ACT-5): no echo and no window, since it starts
        #    no work and a resume undoes it, so the plan the idea gate filed, which carries
        #    provenance:agent, is not asked about. Its comment says no question was asked,
        #    and a stop after it hears what happened. The plan is read as the tracker's epic
        #    query reads it, with no text of its own. Resume and cancel still ask.
        def running_plan(name, setup=None, **over):
            c, s, t = dmworld(name, **dict(on, epic_wip=1, **over))
            t.add_epic("PROD-50", (("PROD-51", ()), ("PROD-52", ("PROD-51",))))
            epic_of = t.epic

            def epic(ident):
                e = epic_of(ident)
                if e:
                    e.pop("description", None)
                return e
            t.epic = epic
            for n, each in enumerate(("approve PROD-50", "yes")):
                s.add(ALICE, each, channel=DM, at=now + 10 * n)
                run(c, s, t, at=now + 10 * n + 5)
            if setup:
                setup(c)
            return c, s, t

        def after(c, s, t, line, at=50):
            before, moved = len(dm_lines(s)), len(t.moves)
            s.add(ALICE, line, channel=DM, at=now + at)
            run(c, s, t, at=now + at + 10)
            return t.moves[moved:], dm_lines(s)[before:]

        def case_pause():
            c, s, t = running_plan("dc-pause")
            moves, said = after(c, s, t, "pause PROD-50")
            late = after(c, s, t, "stop", at=70)[1]
            return (moves, [x.split(".")[0] for x in said],
                    "No question was asked: it was their own clear ask." in [
                        b for i, b in t.comments if i == "uuid-50"][-1],
                    [x.split(", at ")[0] for x in late])
        check("dc-your-pause-acts-at-once-with-no-echo-and-says-no-question-was-asked",
              outcome(case_pause),
              ([("uuid-50", "st-backlog")], ["Paused PROD-50 for <@%s>" % ALICE], True,
               ["That already happened: I paused PROD-50"]))

        def full(c):
            st = json.load(open(state_path(c)))
            st["acted_at"] = [now + 45] * c["max_actions_per_hour"]
            json.dump(st, open(state_path(c), "w"))

        def left_and_limit():
            c, s, t = running_plan("dc-pause-left")
            moves, said = after(c, s, t, "pause PROD-50 and PROD-6")
            c2, s2, t2 = running_plan("dc-pause-full", full)
            return ((moves, [x.split(":")[0] for x in said]),
                    after(c2, s2, t2, "pause PROD-50"))
        check("dc-a-pause-names-what-it-left-out-and-what-stopped-it", outcome(left_and_limit),
              (([("uuid-50", "st-backlog")], ["Left out", "Paused PROD-50 for <@%s>. None of "
                                              "its pieces will start until you resume it. "
                                              "PROD-51 is at work now, and keeps going. To go "
                                              "on with it, type `resume PROD-50`." % ALICE]),
               ([], ["I didn't pause PROD-50: I have reached my limit of 90 actions an hour. "
                     "Nothing was changed. %s" % (AGAIN % ("pause", "PROD-50"))])))

        def still_asks(line):
            c, s, t = running_plan("dc-asks-%s" % line.split()[0])
            moves, said = after(c, s, t, line)
            return moves, bool(direct_of(c)), len(said) == 1 and not said[0].startswith(
                ("Paused", "Resumed", "Cancelled"))
        check("dc-resume-and-cancel-still-ask-with-direct-asks-on",
              [outcome(lambda: still_asks(line)) for line in ("resume PROD-50", "cancel PROD-50")],
              [([], False, True)] * 2)


def selftest():
    import pipeline_bridge
    return pipeline_bridge.split_module_selftest(os.path.abspath(__file__), [cases_direct])


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        # As the module the bridge imports, never a copy of it named __main__, so its
        # cases and the pass share one copy of it (KIT-312).
        import pipeline_bridge_direct
        sys.exit(pipeline_bridge_direct.selftest())
    print("usage: pipeline_bridge_direct.py --selftest (the bridge imports this module)")
    sys.exit(EXIT_USAGE)
