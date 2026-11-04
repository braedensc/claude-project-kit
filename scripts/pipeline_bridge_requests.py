#!/usr/bin/env python3
"""The Slack bridge reads each message: who sent it, what it is, and the request it holds.

    python3 scripts/pipeline_bridge_requests.py --selftest

A MODULE OF ITS OWN (KIT-312, part 2: KIT-407). scripts/pipeline_bridge.py was one file
of more than 12,000 lines, so Stage E withheld it from every review it was in, and every
bridge feature conflicted with every other. It is split by concern, with no change in
behaviour: each function, constant and method here is main's own text, moved unchanged.
pipeline_bridge imports each one back, so `pipeline_bridge.X` is still the very same
object. Its cases sit below its selftest marker, and the bridge's selftest runs them too,
end to end through one pass. Its own selftest runs them as well, and pins that it holds
no HTTP call and no GraphQL write of its own, so Slack and the tracker stay behind the
bridge's two clients.

WHAT IS HERE
  `handle`       what each message is: an answer to the bridge's own question, a reply
                 under a dev's ping, a question for the tech lead, a request, or anything
                 else, which gets one line on what the bridge can do.
  Who sent it    the checks a message and an account must pass, read from Slack itself,
                 never from text (`message_refusal`, `account_refusal`).
  A request      the one strict line, `plan`, `replan` or `approve`, and the bridge's own
                 question for it: its words, the ticket as it showed it, and the settings
                 its text states. `replan` and `approve` go on in their own modules.
  Who picked it  a line the tech lead set up names the member's message it came after,
                 and whether the member or the tech lead picked the ticket. Picked by the
                 tech lead, only `yes <id>` acts. The tech lead's questions have a cap of
                 their own (ACT-1, KIT-364).
"""
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pipeline_notify_local as notify  # noqa: E402
from pipeline_bridge_transport import BridgeError  # noqa: E402
from pipeline_bridge_config import (ASK_AGAIN_BIND, CREDIT_BIND,  # noqa: E402
                                    EXIT_USAGE, EXPIRED, OPEN, REFUSED, SLACK_USER_RE,
                                    TICKET_RE, _TICKET_WORD_RE, clock, credit_terms, key_of,
                                    label_names, load_pings, ticket_number, ts_float)
from pipeline_bridge_plans import FOLLOWUP, FOLLOWUP_INTO  # noqa: E402

# What the bridge says to anything else a member writes in its DM, at most once an hour.
DM_HELP = ("I can do `plan <ticket id>`, `replan <ticket id>`, `approve <ticket id>` and "
           "yes or no here. To "
           "answer a dev, reply in the thread under its message. For anything else, ask "
           "the tech lead in the chat channel for now.")
# THE BIND SENTENCES (KIT-297, KIT-342): what to type, where to answer, and what a yes does.
# Each is fixed text, never the writer's, in plain words: no state name and no internal
# term. A question's says what a yes changes and how long the question stays open; one
# that can lead to work says that merging stays the member's, and the follow-up, which
# starts none, says so instead. The bridge's selftest pins every one word for word.
QUESTION_BIND = ("Reply %s %s within %d hours, and %s. Reply `no` to drop it. Merging stays "
                 "yours. %s")
# ACT-1 (KIT-364): a question the tech lead set up names the member's message it came
# after, and says who picked the ticket. Picked by the tech lead, only `yes <id>` acts.
TL_CREDIT_BIND = "Set up by the tech lead after your message at %s."
YOU_NAMED_BIND = "You named %s."
TL_PICKED_BIND = "The tech lead picked %s."
NAMED_YES_BIND = "The tech lead picked %s, so the yes must name it: reply `yes %s`."
PLAN_THEN_BIND = "I'll hand it to the planner"
REPLAN_THEN_BIND = "I'll hand it back to the planner for a new plan"
ACT_OFF_THEN_BIND = "I'll only note your answer: acting is switched off here, so nothing moves"
TOO_MANY_OPEN_BIND = "Answer one, or let it expire, then ask again."
LOOKUP_LIMIT_BIND = "Ask again later."
ALREADY_ASKED_BIND = "Answer that one."
REPLAN_AGAIN_BIND = "To plan it again, type `replan %s`."
EDITED_BIND = "Send the answer again as a new reply."
# The verbs a request may start with (KIT-333): the tech lead's rules name only these.
REQUEST_VERBS = ("plan", "replan", "approve")
# ACT-1: the tech lead's questions have a cap of their own, never counted against the
# member's max_open_questions. A newer one past it closes the oldest, and says so. A ticket
# is the member's own pick only when named in his DM within PICK_SECONDS, first by him, the
# bridge or a page whose mark only a configured job can raise: the two `agent:*` marks are
# raised by any author (pipeline_notify_local.py, marks_author_ok), so they never count.
TL_QUESTIONS_MAX = 3
PICK_SECONDS = 24 * 3600
ANY_AUTHOR_MARKS = (notify.ESC_BLOCKED, notify.ESC_NEEDS_HUMAN)
TL_LAPSED_SAYS = ("My question about %s has closed: the tech lead set up a newer one, and I "
                  "keep at most %d of its questions open.")
TL_REPLACED_SAYS = ("I've closed my question about %s that the tech lead set up, since you "
                    "asked for something else.")
# The whole grammar. A request is one line and nothing else; so is an answer.
REQUEST_RE = re.compile(r"^(%s)\s+(%s)$" % ("|".join(REQUEST_VERBS), TICKET_RE),
                        re.IGNORECASE)
YES_RE = re.compile(r"^yes(?:\s+(%s))?[\s.!]*$" % TICKET_RE, re.IGNORECASE)
NO_RE = re.compile(r"^no(?:\s+(%s))?[\s.!]*$" % TICKET_RE, re.IGNORECASE)
_LEADING_MENTION_RE = re.compile(r"^<@([UWB][A-Z0-9]+)(?:\|[^>]*)?>\s*")


def command_text(msg, own_user_id):
    """The message as a command line: entities decoded, brackets gone, formatting and a
    leading mention of the bridge itself dropped, whitespace collapsed."""
    text = notify.decode_chat_entities(msg.get("text") or "").strip()
    m = _LEADING_MENTION_RE.match(text)
    if m and m.group(1) == own_user_id:
        text = text[m.end():]
    text = text.strip().strip("*_`~").strip()
    return re.sub(r"\s+", " ", text)


# The one reason that has a way to answer again: a line carrying it ends with EDITED_BIND.
EDITED_WHY = "it was edited after it was sent"


def message_refusal(msg, me):
    """Why this message cannot answer a question, from the message alone, or None."""
    if msg.get("bot_id") or msg.get("subtype"):
        return "it is a bot post or a %s message" % (msg.get("subtype") or "bot")
    if msg.get("edited"):
        return EDITED_WHY
    if msg.get("files"):
        return "it carries a file"
    if not msg.get("user"):
        return "Slack names no sender for it"
    if msg.get("user") == "USLACKBOT" or msg.get("app_id"):
        return "it was posted by Slackbot or an app"
    for field in ("team", "user_team", "source_team"):
        if msg.get(field) and msg.get(field) != me["team_id"]:
            return "it came from another Slack workspace"
    return None


def account_refusal(profile, me, cfg):
    """Why this Slack account cannot answer, from Slack's own `users.info`, or None."""
    if not isinstance(profile, dict) or not profile.get("id"):
        return "Slack returned no account for the sender"
    if profile.get("deleted"):
        return "the account is deactivated"
    if profile.get("is_bot") or profile.get("is_app_user"):
        return "the account is a bot or an app"
    if profile.get("is_restricted") or profile.get("is_ultra_restricted"):
        return "the account is a guest; only full members may answer"
    # Same workspace must be POSITIVE: an account Slack names no workspace for is refused.
    if not profile.get("team_id"):
        return "Slack named no workspace for the account"
    if profile.get("team_id") != me["team_id"]:
        return "the account belongs to another Slack workspace"
    if cfg["allowed_slack_users"] and profile.get("id") not in cfg["allowed_slack_users"]:
        return "the account is not on my list of members who may answer"
    return None


def ticket_fingerprint(issue):
    """What a plan question showed, and what the planner will read: the ticket's id, title,
    text, team, labels and state. A session or the chat bot can write a ticket, so a yes
    moves it only while this still matches."""
    text = json.dumps([issue.get("id") or "", issue.get("title") or "",
                       issue.get("description") or "", (issue.get("team") or {}).get("key") or "",
                       sorted(label_names(issue) - {None}),
                       (issue.get("state") or {}).get("id") or ""])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def ticket_ids(text):
    """The ticket ids a text names, as whole words, upper-cased (ACT-1)."""
    return sorted(set(w.upper() for w in _TICKET_WORD_RE.findall(
        notify.decode_chat_entities(text or ""))))


def question_text(issue, requester, cfg, now, by_bot=False, dm=False):
    head, body, bind, _facts = question_parts(issue, requester, cfg, now, by_bot, dm)
    return head + body + "\n" + bind


def newest_run(runs):
    """The planning ticket with the highest number, or "" (KIT-244)."""
    return max((r.get("identifier") or "" for r in runs), key=ticket_number, default="")


def question_parts(issue, requester, cfg, now, by_bot=False, dm=False, runs=None, via="",
                   terms=None):
    """(head, body, bind, facts): a plan question in the pieces the writer needs (KIT-297).
    The head asks, so the writer is never given it. The bind is what a yes agrees to, and
    who asked: posted verbatim, never the model's. head + body + newline + bind is the
    question as it always read. With `runs` (a list, perhaps empty) it is a re-plan
    question instead (KIT-244), naming the idea's newest planning run, and `via` the
    planning ticket that was named in its place. `terms` (ACT-1) is (credit, the ticket a
    yes must name, or None)."""
    title = notify.safe_title(issue.get("title") or "")
    creator = notify.safe_title(((issue.get("creator") or {}).get("displayName")
                                 or (issue.get("creator") or {}).get("name") or "someone"))
    state = notify.safe_title((issue.get("state") or {}).get("name") or "an unknown state")
    team = ((issue.get("team") or {}).get("key")) or "?"
    # A bot's request is credited in words, never @mentioned: a mention of the chat bot
    # in its own thread could start a chat session whose reply lands after the question.
    if by_bot:
        # The tech lead only when it is the chat bot the config names: any other bot is "a bot".
        who = "the tech lead" if requester == cfg.get("chat_bot_user_id") else "a bot"
    else:
        who = ("<@%s>" % requester) if SLACK_USER_RE.match(requester or "") else "a bot"
    then = PLAN_THEN_BIND if runs is None else REPLAN_THEN_BIND
    if not cfg["act"]:
        then = ACT_OFF_THEN_BIND
    # In a DM the question is a message of its own, and a yes of its own answers it
    # (KIT-295). The terms a yes agrees to read the same in both.
    body = ("%s “%s”: in %s on %s, filed by %s."
            % (issue.get("identifier"), title, state, team, creator))
    last = newest_run(runs or [])
    if runs is not None:
        body += (" Its last planning run, %s, has ended." % last if last
                 else " It has no planning run yet.")
        if via:
            body += " You named %s, one of its planning runs." % via
    yes, credit = credit_terms(terms, who)
    bind = QUESTION_BIND % (yes, "here" if dm else "in this thread", cfg["question_ttl_hours"],
                            then, credit)
    facts = {"ticket": issue.get("identifier"), "title": title, "state": state, "team": team,
             "filed_by": creator, "needs": "his answer to the fixed line after this message"}
    if runs is not None:
        facts["last_run"] = last or "none"
        if via:
            facts["named"] = via
        return "*Plan this again?* ", body, bind, facts
    return "*Plan this?* ", body, bind, facts


class RequestsMixin(object):
    """Pass's reading of each message, and its requests (KIT-312)."""

    # -- one message ---------------------------------------------------------------
    def handle(self, channel, msg):
        if msg.get("user") == self.me["user_id"] or (
                self.me.get("bot_id") and msg.get("bot_id") == self.me["bot_id"]):
            self.mark(channel, msg, "own")
            return
        if self.tech_lead_reply(channel, msg):
            return
        thread = msg.get("thread_ts")
        reply = bool(thread and thread != msg.get("ts"))
        if reply:
            # Read as each reply is handled, not once a pass: a ping the notifier records
            # while this pass runs is still found.
            pings = load_pings(self.cfg)
            key = notify.chat_message_key(channel, thread)
            if key in pings:
                try:
                    self.answer_ping(channel, msg, pings[key])
                except BridgeError:
                    raise
                except Exception as exc:                           # noqa: BLE001
                    # A malformed record, or any other surprise, counts against this message
                    # like a failed read: the third pass gives it up and says so in the
                    # thread, so it cannot stall every pass after it.
                    raise BridgeError("the answer under the ping in %s could not be handled "
                                      "(%s)" % (channel, type(exc).__name__))
                return
            if self.unrecorded_ping(channel, msg, thread):
                return
        text = command_text(msg, self.me["user_id"])
        req = REQUEST_RE.match(text)
        if req:
            self.request(channel, msg, req.group(1).lower(), req.group(2).upper())
            return
        yes, no = YES_RE.match(text), NO_RE.match(text)
        dm = channel in self.dm_channels
        if dm and self.cfg["ask_tech_lead"] and self.for_tech_lead(channel, msg, yes or no):
            self.forward(channel, msg)
            return
        # In a channel only a reply in a question's thread answers it. In a DM a question
        # is a message of its own, so an answer of its own counts there too (KIT-295).
        if (yes or no) and (reply or dm):
            self.answer(channel, msg, bool(yes), ((yes or no).group(1) or "").upper())
            return
        if dm and not reply and self.cfg["ask_tech_lead"]:
            self.forward(channel, msg)
            return
        if dm and not reply:
            self.stray(channel, msg)
            return
        self.mark(channel, msg, "ignored")

    def stray(self, channel, msg):
        """Anything else a member writes in a DM: one line on what the bridge does there, at
        most once an hour in each DM, so a chat with it is never silent and never a flood
        (KIT-295). A bot's post, or any special message type, is passed over in silence."""
        human = (msg.get("user") and msg.get("user") != "USLACKBOT" and not msg.get("bot_id")
                 and not msg.get("app_id") and not msg.get("subtype"))
        if human and self.state["stray_said"].get(channel, 0) <= self.now - 3600:
            self.post(channel, None, "", kind="stray-help", bind=DM_HELP)
            self.state["stray_said"][channel] = self.now
        self.mark(channel, msg, "ignored")

    def post_refusal(self, channel, thread, line, why, kind, ticket):
        """A refusal naming its reason (KIT-297). A reason with a way to answer again ends
        with the bind saying how, after one space: it goes out as it is, never the model's."""
        return self.post(channel, thread, line, kind=kind,
                         bind=EDITED_BIND if why == EDITED_WHY else "", join=" ",
                         facts={"ticket": ticket})

    def refuse_request(self, channel, msg, thread, text, why, bind="", ticket=""):
        # `bind`, when given, is the sentence saying what to do next, after one space.
        self.post(channel, thread, text, kind="request-refused", bind=bind, join=" ",
                  facts={"ticket": ticket} if ticket else None)
        self.mark(channel, msg, "refused: %s" % why)
        self.counts["refused"] += 1

    def request(self, channel, msg, kind, ident, set_up=None):
        """One request line. `set_up`, for a line the tech lead set up from a DM question
        (ACT-1), is that question's forward: its record's key and asks, and the ask the
        reply's block named."""
        # In a DM the question is a message of its own, so it notifies (KIT-295).
        dm = channel in self.dm_channels
        thread = None if dm else (msg.get("thread_ts") or msg.get("ts"))
        requester = msg.get("user") or msg.get("bot_id") or ""
        by_bot = bool(msg.get("bot_id") or msg.get("app_id"))
        # Every request that would read the tracker counts, found or not: the key is the
        # owner's, shared with Stage E. Past the limit a request is refused with no read,
        # and that is said at most once an hour in a channel.
        hour_ago = self.now - 3600
        recent = [t for t in self.state["asked_at"] if t > hour_ago]
        if len(recent) >= self.cfg["max_questions_per_hour"]:
            if self.state["limit_said"].get(channel, 0) <= hour_ago:
                self.post(channel, thread, "I have looked up %d tickets in the last hour, "
                          "which is my limit." % len(recent), kind="lookup-limit",
                          bind=LOOKUP_LIMIT_BIND, join=" ", facts={"looked_up": len(recent)})
                self.state["limit_said"][channel] = self.now
            self.mark(channel, msg, "refused: rate limit")
            self.counts["refused"] += 1
            return
        open_now = [q for q in self.state["questions"].values() if q.get("status") == OPEN]
        # The tech lead's questions have a cap of their own (ask), and never count here.
        mine = [q for q in open_now if not q.get("by_tech_lead")]
        if not by_bot and len(mine) >= self.cfg["max_open_questions"]:
            self.refuse_request(channel, msg, thread, "%d questions are already open, which "
                                "is my limit." % len(mine), "too many open",
                                bind=TOO_MANY_OPEN_BIND)
            return
        for q in open_now:
            if (q.get("ticket") == ident and dm and not by_bot and q.get("by_tech_lead")
                    and q.get("channel") == channel):
                # His own ask on a ticket the tech lead's open question is about (ACT-1): the
                # same action answers it as `yes <id>`; another closes it, and goes ahead.
                if q.get("kind") == kind:
                    self.answer(channel, msg, True, ident)
                    return
                self.post(channel, None, TL_REPLACED_SAYS % ident, kind="question-replaced",
                          facts={"ticket": ident})
                q["status"] = REFUSED
                self.say("  closed the tech lead's question on %s: %s asked for %s"
                         % (ident, msg.get("user"), kind))
        open_now = [q for q in open_now if q.get("status") == OPEN]
        for q in open_now:
            if q.get("ticket") == ident:
                if dm:
                    where = " here" if q.get("channel") == channel else " elsewhere"
                else:
                    where = ", in %s" % ("this thread" if q.get("thread") == thread
                                         else "another thread")
                self.refuse_request(channel, msg, thread, "%s already has an open question%s."
                                    % (ident, where), "already asked",
                                    bind=ALREADY_ASKED_BIND, ticket=ident)
                return
        self.state["asked_at"].append(self.now)
        terms = self.pick_terms(channel, msg, ident, set_up) if dm else None
        if kind == "approve":
            self.request_approve(channel, msg, thread, requester, ident, by_bot, dm,
                                 terms=terms, set_up=set_up)
            return
        if kind == "replan":
            self.request_replan(channel, msg, thread, requester, ident, by_bot, dm,
                                terms=terms, set_up=set_up)
            return
        issue = self.tracker.issue(ident)
        if not issue or not issue.get("identifier"):
            self.refuse_request(channel, msg, thread, "I can't find %s in Linear."
                                % ident, "not found", ticket=ident)
            return
        team = (issue.get("team") or {}).get("key")
        if team not in self.cfg["team_keys"]:
            self.refuse_request(channel, msg, thread, "%s is on %s, and I plan only on %s."
                                % (issue["identifier"], team or "no team",
                                   ", ".join(self.cfg["team_keys"])),
                                "team not planned", ticket=issue["identifier"])
            return
        if (issue.get("state") or {}).get("name") == self.cfg["plan_it_state"]:
            # The yes path's own words: a ticket already there is not asked about. Its
            # planning may have ended without a plan, so say how to start it again (KIT-244).
            self.refuse_request(channel, msg, thread, "%s is already in %s."
                                % (issue["identifier"], self.cfg["plan_it_state"]),
                                "already in plan it", ticket=issue["identifier"],
                                bind=REPLAN_AGAIN_BIND % issue["identifier"])
            return
        live = self.tracker.live_work(issue["id"])
        if live:
            self.refuse_request(channel, msg, thread, "%s looks like live work, so I won't "
                                "ask about planning it: %s." % (issue["identifier"],
                                                                "; ".join(live)), "live work",
                                ticket=issue["identifier"])
            return
        self.ask(channel, msg, thread, kind, issue, requester,
                 question_parts(issue, requester, self.cfg, self.now, by_bot=by_bot, dm=dm,
                                terms=terms), terms=terms, set_up=set_up)

    def pick_terms(self, dm, msg, ident, set_up=None):
        """(credit, the ticket a yes must name or None) for a request in a DM (ACT-1), or None
        for the plain question. A line the tech lead set up names the member's message it
        came after, and says who picked the ticket. A member's own request on a ticket the
        tech lead named first is his ask on its pick: his question takes `yes <id>` too."""
        own = self.picked_by_member(dm, msg, ident, set_up)
        if set_up is not None:
            after = clock(ts_float(set_up["ask"].get("dm_ts")), self.now)
            return ("%s %s" % (TL_CREDIT_BIND % after,
                               (YOU_NAMED_BIND if own else TL_PICKED_BIND) % ident),
                    None if own else ident)
        if own or msg.get("bot_id") or msg.get("app_id"):
            return None
        return ("%s %s" % (CREDIT_BIND % ("<@%s>" % msg.get("user")), TL_PICKED_BIND % ident),
                ident)

    def picked_by_member(self, dm, msg, ticket, set_up=None):
        """True when the ticket is the member's own pick in this DM (ACT-1). Within the last
        PICK_SECONDS, up to his request, the first post there to name it is his own, one of
        the bridge's (never one made for a line the tech lead set up), or a page whose mark
        only a configured job can raise; never a post carrying the tech lead's words. For a
        line the tech lead set up, one of his messages passed on in that thread must name it
        too: the conversation quoted under a forward is never his message."""
        member = (set_up or {}).get("member") or msg.get("user")
        upto = self.now if set_up is not None else ts_float(msg.get("ts"))
        texts, namings = {}, []
        for m in self.tops.get(dm, []):
            key = key_of(dm, m.get("ts"))
            texts[key] = m.get("text") or ""
            if key in self.state["relayed_posts"] or key in self.state["set_up_posts"]:
                continue                       # the tech lead's: read from their records
            own = (m.get("user") == self.me["user_id"] or bool(
                self.me.get("bot_id") and m.get("bot_id") == self.me["bot_id"]))
            page = self.dm_posts.get(key)
            if own and isinstance(page, dict):
                if page.get("mark") not in notify.MARKS or page.get("mark") in ANY_AUTHOR_MARKS:
                    continue
                who = "page"
            elif own:
                if key not in self.state["posted"]:
                    continue                   # neither job's: never a pick
                who = "bridge"
            elif (m.get("user") == member and not m.get("bot_id") and not m.get("app_id")
                  and not m.get("subtype")):
                who = "member"
            else:
                continue
            namings.append((ts_float(m.get("ts")), who, ticket_ids(m.get("text"))))
        for name in ("relayed_posts", "set_up_posts"):
            for key, rec in self.state[name].items():
                if key.rsplit(":", 1)[0] == dm:
                    ids = (rec.get("ids") if isinstance(rec, dict)
                           else ticket_ids(texts.get(key)))
                    namings.append((ts_float(key.rsplit(":", 1)[1]), "tech lead", ids or []))
        first = next((who for at, who, ids in sorted(namings)
                      if self.now - PICK_SECONDS <= at <= upto and ticket in ids), None)
        if first in (None, "tech lead"):
            return False
        return set_up is None or any(ticket in (a.get("ids") or [])
                                     for a in set_up["asks"] if isinstance(a, dict))

    def ask(self, channel, msg, thread, kind, issue, requester, parts, children=None,
            extra=None, terms=None, set_up=None):
        # The head and body, then the bind on a line of its own, as a question always read.
        # The writer is given the body only: the head asks, and only the bind may.
        head, body, bind, facts = parts
        by_tech_lead = bool(msg.get("bot_id") or msg.get("app_id"))
        if by_tech_lead:
            self.lapse_tech_leads(TL_QUESTIONS_MAX - 1)
        posted = self.post(channel, thread, head + body, kind="question-" + kind, bind=bind,
                           join="\n", plain=body, facts=facts)
        self.state["questions"][key_of(channel, posted)] = {
            "kind": kind, "ticket": issue["identifier"], "ticket_id": issue.get("id"),
            "channel": channel, "thread": thread, "ts": posted, "asked_at": self.now,
            "asked_for": requester, "status": OPEN,
            # WHAT THE MEMBER SAW, and the terms the question stated: a yes acts only while
            # both still hold. A tech lead's question names the ticket a yes must name, and
            # the ask it was set up for (ACT-1).
            "fingerprint": ticket_fingerprint(issue), "promised": self.promised(kind),
            "by_tech_lead": by_tech_lead, "named_yes": (terms or (None, None))[1],
            "set_up": ({"forward": set_up["key"], "ask": set_up["ask"].get("n")}
                       if set_up else None)}
        if children is not None:
            # A yes approves exactly these children, as they were when the question was
            # asked; any change before the yes approves nothing.
            self.state["questions"][key_of(channel, posted)]["children"] = children
        if extra:
            # More of what the member saw (a re-plan's newest run and move, KIT-244).
            self.state["questions"][key_of(channel, posted)].update(extra)
        self.mark(channel, msg, "asked: %s %s" % (kind, issue["identifier"]))
        self.counts["asked"] += 1
        self.say("  asked: %s %s (requested by %s)" % (kind, issue["identifier"],
                                                       requester or "?"))

    def lapse_tech_leads(self, keep):
        """Close the oldest of the tech lead's open questions until `keep` are open (ACT-1),
        each said where it was asked: the newest are the ones it still means."""
        theirs = sorted((q for q in self.state["questions"].values()
                         if q.get("status") == OPEN and q.get("by_tech_lead")),
                        key=lambda q: ts_float(q.get("ts")))
        for q in theirs[:max(0, len(theirs) - keep)]:
            self.post(q["channel"], q.get("thread"), TL_LAPSED_SAYS % (q["ticket"],
                                                                     TL_QUESTIONS_MAX),
                      kind="question-lapsed", bind=ASK_AGAIN_BIND % (q["kind"], q["ticket"]),
                      join=" ", facts={"ticket": q["ticket"]})
            q["status"] = EXPIRED
            self.say("  closed the tech lead's oldest open question, on %s: past its cap of %d"
                     % (q["ticket"], TL_QUESTIONS_MAX))

    def promised(self, kind, team=None):
        """The settings a question's text states, as they are now. Recorded when it is
        asked: a yes acts only on the terms the member read."""
        terms = {"act": self.cfg["act"], "question_ttl_hours": self.cfg["question_ttl_hours"]}
        if kind == "approve":
            terms.update(ready_state=self.cfg["ready_state"], epic_wip=self.cfg["epic_wip"],
                         agent_user_name=self.cfg["agent_user_name"])
        elif kind == FOLLOWUP:
            # The team whose backlog the follow-up is filed in, and where in it (KIT-299).
            terms.update(team=team, files_into=FOLLOWUP_INTO)
        else:
            terms.update(plan_it_state=self.cfg["plan_it_state"])
        return terms


# ══════════════════════════════════════════════════════════════════════════════════════
# Selftest — offline. This module's cases run in the bridge's own selftest, end to end
# through one pass, and in its own, which pins what the module may reach (KIT-407).
# ══════════════════════════════════════════════════════════════════════════════════════
def cases_requests(kit):
    """A question the tech lead set up (ACT-1, KIT-364): who picked its ticket, the line
    that credits it, the yes that acts, the member's own ask on it, and its cap."""
    import json
    from pipeline_bridge import ALICE, BOT, DM, ME, state_path
    with kit as _tmp:
        check, now, outcome, run = kit.take("check", "now", "outcome", "run")
        asked_tl, tl_says, tl_root, dm_lines = kit.take("asked_tl", "tl_says", "tl_root",
                                                        "dm_lines")
        tl_on, tlworld, world, in_room = kit.take("tl_on", "tlworld", "world", "in_room")
        named_yes = NAMED_YES_BIND % ("PROD-6", "PROD-6")

        def questions(c):
            return dict((q["ticket"], q["status"])
                        for q in json.load(open(state_path(c)))["questions"].values())

        def paged(c, s, text, mark):
            """A page the notifier posted into the DM as this app, in its ledger."""
            ts = s.add(ME, text, channel=DM, bot_id=BOT)
            os.makedirs(os.path.dirname(c["notifier_posts_file"]), exist_ok=True)
            with open(c["notifier_posts_file"], "w") as fh:
                json.dump({"schema": "pipeline-notifier-dm-posts/1", "posts": {
                    key_of(DM, ts): {"channel": DM, "ts": ts, "mark": mark}}}, fh)
            return ts

        def set_up(name, asks, block, before=None, words="", **over):
            """She writes each of `asks` in the DM; the tech lead answers the newest ask
            with `block`. `before` runs first, on the world."""
            c, s, t = asked_tl(name, asks[0], **over)
            for line in asks[1:]:
                s.add(ALICE, line, channel=DM)
            if before:
                before(c, s, t)
            run(c, s, t, at=now + 60)
            n = len(json.load(open(state_path(c)))["forwards"][
                "%s:%s" % (c["back_room_channel_id"], tl_root(c))]["asks"])
            tl_says(s, tl_root(c), "%sFor ask %d:\n%s" % (words, n, block))
            run(c, s, t, at=now + 120)
            return c, s, t

        # 1. Her own pick: she named it in what was passed on, so a bare yes answers it.
        def case_her_pick():
            c, s, t = set_up("rq-her-pick", ["can you plan PROD-6?"], "plan PROD-6")
            asked = dm_lines(s)[-1]
            s.add(ALICE, "yes", channel=DM)
            run(c, s, t, at=now + 180)
            return (asked.endswith(". You named PROD-6."), "Reply `yes` here" in asked,
                    dm_lines(s)[-1].startswith("Confirmed by <@%s>" % ALICE))
        check("rq-a-ticket-she-named-is-her-pick-credited-so-and-a-bare-yes-answers-it",
              outcome(case_her_pick), (True, True, True))

        # 2. The tech lead's pick: only `yes <id>` acts; a bare yes gets the line to type.
        def case_its_pick():
            c, s, t = set_up("rq-its-pick", ["what should we plan next?"], "plan PROD-6",
                             words="The light mode idea.\n\n")
            asked = dm_lines(s)[-1]
            s.add(ALICE, "yes", channel=DM)
            run(c, s, t, at=now + 180)
            bare = (dm_lines(s)[-1], questions(c))
            s.add(ALICE, "yes PROD-6", channel=DM)
            run(c, s, t, at=now + 240)
            return (asked.endswith(". The tech lead picked PROD-6."),
                    "Reply `yes PROD-6` here" in asked, bare,
                    dm_lines(s)[-1].startswith("Confirmed by <@%s>" % ALICE))
        check("rq-a-ticket-the-tech-lead-picked-takes-only-a-yes-naming-it",
              outcome(case_its_pick), (True, True, (named_yes, {"PROD-6": OPEN}), True))

        # 3. Her own typed request on a ticket a relayed post named first is her ask on its
        #    pick: the question takes `yes PROD-6` too, and says so.
        def case_typed_on_its_pick():
            c, s, t = asked_tl("rq-typed", "anything worth planning?")
            tl_says(s, tl_root(c), "PROD-6, the light mode idea.")
            run(c, s, t, at=now + 60)
            s.add(ALICE, "plan PROD-6", channel=DM)
            run(c, s, t, at=now + 120)
            asked = dm_lines(s)[-1]
            return (asked.endswith("(Asked for by <@%s>.) The tech lead picked PROD-6." % ALICE),
                    "Reply `yes PROD-6` here" in asked)
        check("rq-her-own-request-on-an-id-a-relayed-post-named-first-takes-a-yes-naming-it",
              outcome(case_typed_on_its_pick), (True, True))

        # 4. A page counts only when its mark only a configured job can raise; the two
        #    `agent:*` marks any author raises never do. A page names PROD-6 first, then the
        #    tech lead's words, then her own.
        def case_page(mark):
            c, s, t = tlworld("rq-page-" + mark.replace(":", "-"))
            paged(c, s, "*Light mode* PROD-6 is ready for you", mark)
            run(c, s, t)
            s.add(ALICE, "what is that page about?", channel=DM)
            run(c, s, t, at=now + 60)                          # ask 1
            tl_says(s, tl_root(c), "That page is about PROD-6.")
            run(c, s, t, at=now + 120)                         # its words name PROD-6
            s.add(ALICE, "then plan PROD-6", channel=DM)
            run(c, s, t, at=now + 180)                         # ask 2, naming it
            tl_says(s, tl_root(c), "For ask 2:\nplan PROD-6")
            run(c, s, t, at=now + 240)
            return dm_lines(s)[-1].split(". ")[-1]
        check("rq-a-page-of-a-configured-jobs-mark-is-her-pick-and-an-any-author-one-is-not",
              (outcome(lambda: case_page(notify.ESC_AWAITING_APPROVAL)),
               outcome(lambda: case_page(notify.ESC_BLOCKED))),
              ("You named PROD-6.", "The tech lead picked PROD-6."))

        # 5. What is quoted under a forward never counts: she named PROD-6 once, before,
        #    but not in the words passed on, so the tech lead picked it.
        def case_history_block():
            c, s, t = world("rq-history", dm_user_ids=[ALICE], **tl_on)
            s.add(ALICE, "PROD-6 might be worth doing", channel=DM)
            run(c, s, t)                       # the first pass: what was there is seen
            s.add(ALICE, "what should we plan?", channel=DM)
            run(c, s, t, at=now + 60)
            tl_says(s, tl_root(c), "For ask 1:\nplan PROD-6")
            run(c, s, t, at=now + 120)
            return ("PROD-6 might be worth doing" in json.dumps(in_room(s)),
                    dm_lines(s)[-1].split(". ")[-1])
        check("rq-a-ticket-named-only-in-the-recent-conversation-is-the-tech-leads-pick",
              outcome(case_history_block), (True, "The tech lead picked PROD-6."))

        # 6. Only the last day counts: named by her 25 hours ago, then by the tech lead,
        #    then by her again, it is the tech lead's pick.
        def case_window():
            c, s, t = world("rq-window", dm_user_ids=[ALICE], **tl_on)
            s.add(ALICE, "remember PROD-6", channel=DM, at=now - 25 * 3600)
            run(c, s, t)
            s.add(ALICE, "anything worth planning?", channel=DM)
            run(c, s, t, at=now + 60)
            tl_says(s, tl_root(c), "PROD-6 still is.")
            run(c, s, t, at=now + 120)
            s.add(ALICE, "plan PROD-6 then?", channel=DM)
            run(c, s, t, at=now + 180)
            tl_says(s, tl_root(c), "For ask 2:\nplan PROD-6")
            run(c, s, t, at=now + 240)
            return dm_lines(s)[-1].split(". ")[-1]
        check("rq-a-naming-older-than-a-day-never-counts", outcome(case_window),
              "The tech lead picked PROD-6.")

        # 7. Her own ask on the ticket of the tech lead's open question: the same action
        #    answers it as `yes PROD-6`; another closes it and goes ahead.
        def case_her_ask(verb):
            c, s, t = set_up("rq-her-ask-" + verb, ["what should we plan next?"],
                             "plan PROD-6", words="Light mode.\n\n")
            s.add(ALICE, "%s PROD-6" % verb, channel=DM)
            run(c, s, t, at=now + 180)
            return questions(c), dm_lines(s)[-1].split(" “")[0].split(".")[0]
        check("rq-her-own-ask-of-the-same-action-answers-the-tech-leads-question",
              outcome(lambda: case_her_ask("plan")),
              ({"PROD-6": "confirmed"}, "Confirmed by <@%s>" % ALICE))
        check("rq-her-own-ask-of-another-action-closes-the-tech-leads-question-and-goes-on",
              outcome(lambda: case_her_ask("replan")),
              ({"PROD-6": REFUSED}, "PROD-6 isn't in Plan it, so there is no planning to "
                                    "repeat"))

        # 8. The tech lead's questions have a cap of their own: a fourth closes the oldest,
        #    and says so. They never count against hers.
        def case_cap():
            def more_tickets(_c, _s, t):
                for n in (7, 8, 9):
                    t.issues["PROD-%d" % n] = dict(t.issues["PROD-6"], id="uuid-%d" % n,
                                                   identifier="PROD-%d" % n)
            c, s, t = set_up("rq-cap", ["plan PROD-5", "plan these four?"],
                             "plan PROD-6\nplan PROD-7\nplan PROD-8\nplan PROD-9",
                             before=more_tickets, max_open_questions=1)
            lapsed = [x for x in dm_lines(s) if x.startswith("My question about")]
            return questions(c), lapsed
        check("rq-a-fourth-tech-lead-question-closes-the-oldest-and-hers-are-not-counted",
              outcome(case_cap),
              ({"PROD-5": OPEN, "PROD-6": EXPIRED, "PROD-7": OPEN, "PROD-8": OPEN,
                "PROD-9": OPEN},
               ["My question about PROD-6 has closed: the tech lead set up a newer one, and I "
                "keep at most 3 of its questions open. Ask again with `plan PROD-6`."]))


def selftest():
    import pipeline_bridge
    return pipeline_bridge.split_module_selftest(os.path.abspath(__file__),
                                                 [cases_requests])


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        # As the module the bridge imports, never a copy of it named __main__, so its
        # cases and the pass share one copy of it (KIT-312).
        import pipeline_bridge_requests
        sys.exit(pipeline_bridge_requests.selftest())
    print("usage: pipeline_bridge_requests.py --selftest (the bridge imports this module)")
    sys.exit(EXIT_USAGE)
