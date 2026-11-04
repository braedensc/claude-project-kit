#!/usr/bin/env python3
"""The Slack bridge binds `it` to the pipeline's own message (ACT-5, KIT-369).

    python3 scripts/pipeline_bridge_bind.py --selftest

A MODULE OF ITS OWN, as each of the bridge's concerns is (KIT-312). pipeline_bridge
imports its mixin, and the bridge's selftest runs its cases, end to end through one pass.
Its own selftest runs them too, and pins that it holds no HTTP call and no GraphQL write
of its own: a bound ask goes the way any ask goes, through request() or ACT-4's direct path.

WHAT IS HERE
  The anchor    what a page's mark lets `it` mean: a planning page, replan; a plan awaiting
                approval, approve. The ticket comes from the notifier's DM ledger, and binds
                only when the page's own text in Slack names it. A relayed tech-lead post, a
                dev's question and a needs-human page never anchor `it`.
  A reply       under a page: `replan it` binds to its ticket. Anything else goes to the tech
                lead with one line naming the page, read from the ledger; with the tech lead
                off, the refusal names what works there.
  At the top    `replan it` binds only to the one eligible page posted since their previous
                message and within the hour, and only when it is the newest post in the DM: a
                newer post is never skipped. Two or more: it asks which. None: the tech lead,
                or with it off, it asks which ticket. Never silent.
  A bare yes    straight after a page that says what to type, with no question open: that
                page's command, in one pinned line.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pipeline_notify_local as notify  # noqa: E402
from pipeline_bridge_config import (EXIT_USAGE, OPEN, _TICKET_WORD_RE,  # noqa: E402
                                    key_of, ts_float)
from pipeline_bridge_requests import ask_text, read_ask  # noqa: E402

# What `it` may mean under each page, by its mark. Only these anchor it; a dev's question,
# a needs-human page, a relayed post of the tech lead's and every other page never do.
PAGE_VERBS = {notify.ESC_PLANNING_FAILED: "replan", notify.ESC_REJECTED: "replan",
              notify.ESC_NO_OUTPUT: "replan", notify.ESC_NEEDS_INPUT: "replan",
              notify.ESC_AWAITING_APPROVAL: "approve"}
# The pages whose own line says what to type: a bare yes straight after one, with no
# question open, is told that command (and never answers an older question).
COMMAND_MARKS = (notify.ESC_PLANNING_FAILED, notify.ESC_REJECTED, notify.ESC_NO_OUTPUT,
                 notify.ESC_NEEDS_INPUT)
# How long a page may anchor a top-level `it`.
BIND_WINDOW_SECONDS = 3600
PAGE_KINDS = {notify.ESC_PLANNING_FAILED: "planning failed",
              notify.ESC_REJECTED: "plan refused", notify.ESC_NO_OUTPUT: "no plan",
              notify.ESC_NEEDS_INPUT: "planning question",
              notify.ESC_AWAITING_APPROVAL: "plan waiting for approval"}

# Pinned word for word, each built only from the bridge's own reads.
WHICH_ONE_BIND = "Which one: %s? Say it again with the ticket id, as `%s <ticket id>`."
WHICH_TICKET_BIND = "Which ticket? Say it again with the ticket id, as `%s <ticket id>`."
PAGE_COMMAND_BIND = "To do that, type `%s %s` here as a new message."
PAGE_WORKS_BIND = "Under that message, `%s it` works, or `%s %s` as a new message."


def ids_or(ids):
    return ids[0] if len(ids) == 1 else "%s or %s" % (", ".join(ids[:-1]), ids[-1])


class BindMixin(object):
    """Pass's `it`, bound to the pipeline's own message (ACT-5)."""

    def page_at(self, channel, ts, verb=None):
        """The ticket a recorded page at `ts` names, or None: its mark must let `verb` mean
        it (any anchoring mark when None), and the page's own text in Slack must name the
        ledger's ticket."""
        page = (getattr(self, "dm_posts", None) or {}).get(key_of(channel, ts))
        if not isinstance(page, dict):
            return None
        allowed = PAGE_VERBS.get(page.get("mark"))
        ticket = (page.get("ticket_id") or "").upper()
        if not allowed or (verb is not None and allowed != verb) or not ticket:
            return None
        text = next((m.get("text") or "" for m in self.tops.get(channel, [])
                     if m.get("ts") == ts), None)
        if text is None:
            root = self.threads.get((channel, ts)) or []
            text = next((m.get("text") or "" for m in root if m.get("ts") == ts), "")
        named = set(w.upper() for w in _TICKET_WORD_RE.findall(text or ""))
        return ticket if ticket in named else None

    def bound_ask(self, channel, msg, read, ticket):
        """A member's `<verb> it`, bound to `ticket`, goes as their own ask with that id goes:
        ACT-4's direct path when it may take it, else the bridge's own question."""
        bound = dict(read, ids=[ticket], it=False)
        if hasattr(self, "direct_ok") and self.direct_ok(channel, msg, bound):
            self.direct_ask(channel, msg, bound)
        else:
            self.request(channel, msg, read["verb"], ticket)
        self.say("  bound `%s it` to %s" % (read["verb"], ticket))

    def bind_top(self, channel, msg, read):
        """A top-level `<verb> it` in a DM. True when it was handled here: bound, asked which,
        or, with the tech lead off and nothing to bind, asked which ticket. False sends it the
        way it went before, to the tech lead."""
        verb, ts = read["verb"], ts_float(msg.get("ts"))
        tops = sorted((m for m in self.tops.get(channel, []) if ts_float(m.get("ts")) < ts),
                      key=lambda m: ts_float(m.get("ts")))
        mine = [m for m in tops if m.get("user") == msg.get("user")]
        since = max(ts - BIND_WINDOW_SECONDS, ts_float(mine[-1].get("ts")) if mine else 0)
        pages = [(m, self.page_at(channel, m.get("ts"), verb)) for m in tops
                 if ts_float(m.get("ts")) > since]
        found = [(m, t) for m, t in pages if t]
        if len(found) == 1 and tops and found[0][0] is tops[-1]:
            self.bound_ask(channel, msg, read, found[0][1])
            return True
        if len(found) > 1:
            ids = sorted(set(t for _m, t in found))
            self.post(channel, None, "", kind="bind-which-one",
                      bind=WHICH_ONE_BIND % (ids_or(ids), verb), facts={"tickets": ids})
            self.mark(channel, msg, "asked which: %s" % ", ".join(ids))
            return True
        if self.cfg["ask_tech_lead"]:
            return False
        self.post(channel, None, "", kind="bind-which-ticket", bind=WHICH_TICKET_BIND % verb)
        self.mark(channel, msg, "asked which ticket")
        return True

    def bind_reply(self, channel, msg, thread):
        """A verified reply under a recorded page that waits on no answer. `<verb> it` with the
        verb its mark allows binds to its ticket. Anything else goes to the tech lead with one
        line naming the page, from the ledger; with the tech lead off, the refusal names
        what works there. True when handled here; False keeps today's refusal."""
        page = (self.dm_posts or {}).get(key_of(channel, thread)) or {}
        mark = page.get("mark")
        verb = PAGE_VERBS.get(mark)
        read = read_ask(ask_text(msg, self.me["user_id"]))
        if verb and read and read["it"] and read["verb"] == verb:
            ticket = self.page_at(channel, thread, verb)
            if ticket:
                self.bound_ask(channel, msg, read, ticket)
                return True
        if self.cfg["ask_tech_lead"] and hasattr(self, "forward"):
            self.forward(channel, msg, note="(Under the %s message about %s.)" % (
                PAGE_KINDS.get(mark, "pipeline"), page.get("ticket_id") or "a ticket"))
            return True
        if verb and page.get("ticket_id"):
            self.post(channel, None, "I can't pass that on: that message isn't waiting on an "
                      "answer.", kind="page-not-waiting",
                      bind=PAGE_WORKS_BIND % (verb, verb, page["ticket_id"]), join=" ")
            self.mark(channel, msg, "refused: page not waiting, its commands named")
            self.counts["refused"] += 1
            return True
        return False

    def page_command(self, channel, msg):
        """A bare yes in a DM with no question open, straight after a page that says what to
        type: that command, in one line. True when said."""
        ts = ts_float(msg.get("ts"))
        before = [m for m in self.tops.get(channel, []) if ts_float(m.get("ts")) < ts]
        if not before:
            return False
        newest = max(before, key=lambda m: ts_float(m.get("ts")))
        page = (self.dm_posts or {}).get(key_of(channel, newest.get("ts"))) or {}
        if page.get("mark") not in COMMAND_MARKS:
            return False
        ticket = self.page_at(channel, newest.get("ts"))
        if not ticket:
            return False
        self.post(channel, None, "", kind="page-command",
                  bind=PAGE_COMMAND_BIND % (PAGE_VERBS[page["mark"]], ticket),
                  facts={"ticket": ticket})
        self.mark(channel, msg, "told the page's command: %s" % ticket)
        return True


# ══════════════════════════════════════════════════════════════════════════════════════
# Selftest — offline. This module's cases run in the bridge's own selftest, end to end
# through one pass, and in its own, which pins what the module may reach (KIT-407).
# ══════════════════════════════════════════════════════════════════════════════════════
def cases_bind(kit):
    """`it` bound to the pipeline's own message (ACT-5, KIT-369)."""
    import json
    from pipeline_bridge import ALICE, BOT, DM, ME, OPEN as OPEN_, state_path
    with kit as _tmp:
        check, now, outcome, run = kit.take("check", "now", "outcome", "run")
        dmworld, tlworld, dm_lines = kit.take("dmworld", "tlworld", "dm_lines")
        failed = ("PROD-6 “Light mode”: its planning ended without a plan. To plan it again, "
                  "send `replan PROD-6` here as a new message.")

        def page(c, s, mark, ticket="PROD-6", text=failed, at=20):
            """A page the notifier posts into the DM as this app, and its ledger record."""
            ts = s.add(ME, text, channel=DM, bot_id=BOT, at=now + at)
            path = c["notifier_posts_file"]
            os.makedirs(os.path.dirname(path), exist_ok=True)
            doc = (json.load(open(path)) if os.path.exists(path)
                   else {"schema": "pipeline-notifier-dm-posts/1", "posts": {}})
            doc["posts"][key_of(DM, ts)] = {"channel": DM, "ts": ts, "mark": mark,
                                            "ticket_id": ticket}
            json.dump(doc, open(path, "w"))
            return ts

        def replannable(t):
            t.in_plan_it("PROD-6", trigger="T1")
            t.add_run("PROD-6", "PROD-50", "completed", trigger="T1")

        def questions(c):
            return sorted((q["kind"], q["ticket"], q["status"]) for q in json.load(
                open(state_path(c)))["questions"].values())

        # 1. A REPLY under a planning page: `replan it` binds to its ticket, the way their own
        #    `replan PROD-6` goes; under a page that allows another verb, or whose own text
        #    does not name the ticket, or under a dev's question, it binds nothing.
        def reply(name, mark, text=failed, line="replan it", **over):
            c, s, t = dmworld(name, act=True, **over)
            replannable(t)
            root = page(c, s, mark, text=text)
            s.add(ALICE, line, channel=DM, thread=root, at=now + 30)
            run(c, s, t, at=now + 60)
            return questions(c), dm_lines(s)[-1]
        got = outcome(lambda: reply("bd-reply", notify.ESC_PLANNING_FAILED))
        check("bd-replan-it-under-a-planning-page-is-your-own-replan-of-its-ticket",
              (got[0], got[1].startswith("*Plan this again?* PROD-6")) if isinstance(got, tuple)
              else got, ([("replan", "PROD-6", OPEN_)], True))
        check("bd-under-another-page-or-one-not-naming-it-nothing-binds",
              [outcome(lambda: reply(*a)[0]) for a in (
                  ("bd-approval-page", notify.ESC_AWAITING_APPROVAL),
                  ("bd-unnamed", notify.ESC_PLANNING_FAILED, "Its planning ended."),
                  ("bd-ready", notify.ESC_READY_TO_MERGE))], [[], [], []])

        # 2. ANY OTHER REPLY under a page that waits on no answer: to the tech lead, with one
        #    line naming the page from the ledger; with it off, the commands that work there.
        def other(name, world):
            c, s, t = world(name, act=True)
            root = page(c, s, notify.ESC_PLANNING_FAILED)
            s.add(ALICE, "why did that one fail?", channel=DM, thread=root, at=now + 30)
            run(c, s, t, at=now + 60)
            return [x for _c, _th, x in s.posts if "why did that one fail?" in x], dm_lines(s)[-1]
        got = outcome(lambda: other("bd-other-tl", tlworld))
        check("bd-another-reply-under-a-page-goes-to-the-tech-lead-naming-the-page",
              [x.split("\n")[1:3] for x in got[0]] if isinstance(got, tuple) else got,
              [["— asked by %s in a DM, ask 1" % ALICE,
                "(Under the planning failed message about PROD-6.)"]])
        got = outcome(lambda: other("bd-other-off", dmworld))
        check("bd-with-the-tech-lead-off-the-refusal-names-what-works-there",
              got[1] if isinstance(got, tuple) else got,
              "I can't pass that on: that message isn't waiting on an answer. Under that "
              "message, `replan it` works, or `replan PROD-6` as a new message.")

        # 3. AT THE TOP: only the one eligible page since their last message, within the hour,
        #    and only when it is the newest post; two ask which one; none ask which ticket.
        def top(name, setup, **over):
            c, s, t = dmworld(name, act=True, **over)
            replannable(t)
            setup(c, s)
            s.add(ALICE, "replan it", channel=DM, at=now + 40)
            run(c, s, t, at=now + 60)
            return questions(c), dm_lines(s)[-1]

        def one(c, s):
            page(c, s, notify.ESC_PLANNING_FAILED)

        def two(c, s):
            page(c, s, notify.ESC_PLANNING_FAILED, ticket="PROD-5",
                 text=failed.replace("PROD-6 “Light mode”", "PROD-5").replace(
                     "replan PROD-6", "replan PROD-5"), at=10)
            page(c, s, notify.ESC_PLANNING_FAILED)

        def newer(c, s):
            page(c, s, notify.ESC_PLANNING_FAILED)
            s.add(ME, "Something else the pipeline said.", channel=DM, bot_id=BOT, at=now + 30)

        def stale(c, s):
            page(c, s, notify.ESC_PLANNING_FAILED, at=-4000)

        def ready(c, s):
            page(c, s, notify.ESC_READY_TO_MERGE)
        got = [outcome(lambda: top("bd-top-" + f.__name__, f))
               for f in (one, two, newer, stale, ready)]
        check("bd-at-the-top-only-the-one-newest-eligible-page-binds",
              [(g[0], g[1].split("\n")[-1] if g[0] else g[1]) if isinstance(g, tuple) else g
               for g in got],
              [([("replan", "PROD-6", OPEN_)], got[0][1].split("\n")[-1]
                if isinstance(got[0], tuple) else ""),
               ([], "Which one: PROD-5 or PROD-6? Say it again with the ticket id, as `replan "
                    "<ticket id>`."),
               ([], "Which ticket? Say it again with the ticket id, as `replan <ticket id>`."),
               ([], "Which ticket? Say it again with the ticket id, as `replan <ticket id>`."),
               ([], "Which ticket? Say it again with the ticket id, as `replan <ticket id>`.")])

        # 4. A BARE YES straight after a page that says what to type, with no question open:
        #    that command, in one line; and such a page keeps a bare yes off an older question.
        def bare_yes(name, older=False):
            c, s, t = dmworld(name, act=True)
            if older:
                s.add(ALICE, "can you plan PROD-5?", channel=DM, at=now + 5)
                run(c, s, t, at=now + 10)
            page(c, s, notify.ESC_PLANNING_FAILED)
            s.add(ALICE, "yes", channel=DM, at=now + 30)
            run(c, s, t, at=now + 60)
            return questions(c), dm_lines(s)[-1], t.moves
        got = outcome(lambda: bare_yes("bd-yes"))
        check("bd-a-bare-yes-after-a-planning-page-is-told-its-command",
              got[1:] if isinstance(got, tuple) else got,
              ("To do that, type `replan PROD-6` here as a new message.", []))
        got = outcome(lambda: bare_yes("bd-yes-older", older=True))
        check("bd-a-planning-page-keeps-a-bare-yes-off-an-older-question",
              (got[0], got[2]) if isinstance(got, tuple) else got,
              ([("plan", "PROD-5", OPEN_)], []))

        # 5. WITH DIRECT ASKS ON, a bound ask takes ACT-4's path: the echo, then the act.
        got = outcome(lambda: reply("bd-direct", notify.ESC_PLANNING_FAILED,
                                    direct_asks=True, agent_user_name="Agent"))
        check("bd-with-direct-asks-on-a-bound-ask-is-echoed",
              got[1].split("\n")[-1] if isinstance(got, tuple) else got,
              "In 90 seconds I'll hand it back to the planner for a new plan: PROD-6 “Light "
              "mode”. Say stop to cancel.")


def selftest():
    import pipeline_bridge
    return pipeline_bridge.split_module_selftest(os.path.abspath(__file__), [cases_bind])


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        import pipeline_bridge_bind
        sys.exit(pipeline_bridge_bind.selftest())
    print("usage: pipeline_bridge_bind.py --selftest (the bridge imports this module)")
    sys.exit(EXIT_USAGE)
