#!/usr/bin/env python3
"""The Slack bridge reads each message: who sent it, what it is, and the request it holds.

    python3 scripts/pipeline_bridge_requests.py --selftest

A MODULE OF ITS OWN (KIT-312, part 2: KIT-407). scripts/pipeline_bridge.py was one file
of more than 12,000 lines, so Stage E withheld it from every review it was in, and every
bridge feature conflicted with every other. It is split by concern, with no change in
behaviour: each function, constant and method here is main's own text, moved unchanged.
pipeline_bridge imports each one back, so `pipeline_bridge.X` is still the very same
object. Its cases run in the bridge's own selftest, end to end through one pass. This
module's own selftest pins that it holds no HTTP call and no GraphQL write of its own,
so Slack and the tracker stay behind the bridge's two clients.

WHAT IS HERE
  `handle`       what each message is: an answer to the bridge's own question, a reply
                 under a dev's ping, a question for the tech lead, a request, or anything
                 else, which gets one line on what the bridge can do.
  Who sent it    the checks a message and an account must pass, read from Slack itself,
                 never from text (`message_refusal`, `account_refusal`).
  A request      the one strict line, `plan`, `replan` or `approve`, and the bridge's own
                 question for it: its words, the ticket as it showed it, and the settings
                 its text states. `replan` and `approve` go on in their own modules.
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
from pipeline_bridge_config import (EXIT_USAGE, OPEN, SLACK_USER_RE,  # noqa: E402
                                    TICKET_RE, key_of, label_names, load_pings,
                                    ticket_number)
from pipeline_bridge_plans import FOLLOWUP, FOLLOWUP_INTO  # noqa: E402

# What the bridge says to anything else a member writes in its DM, at most once an hour.
DM_HELP = ("I can do `plan <ticket id>`, `replan <ticket id>`, `approve <ticket id>` and "
           "yes or no here. To "
           "answer a dev, reply in the thread under its message. For anything else, ask "
           "the tech lead in the chat channel for now.")
# THE BIND SENTENCES (KIT-297, KIT-342): what to type, where to answer, and what a yes does.
# Each is fixed text, never the writer's, in plain words: no state name and no internal
# term. A question's says what a yes changes, how long the question stays open, and that
# merging stays the member's. The bridge's selftest pins every one word for word.
QUESTION_BIND = ("Reply `yes` %s within %d hours, and %s. Reply `no` to drop it. Merging "
                 "stays yours. (Asked for by %s.)")
PLAN_THEN_BIND = "I'll hand it to the planner"
REPLAN_THEN_BIND = "I'll hand it back to the planner for a new plan"
ACT_OFF_THEN_BIND = "I'll only note your answer: acting is switched off here, so nothing moves"
TOO_MANY_OPEN_BIND = "Answer one, or let it expire, then ask again."
ALREADY_ASKED_BIND = "Answer that one."
REPLAN_AGAIN_BIND = "To plan it again, type `replan %s`."
EDITED_BIND = "Send the answer again as a new reply."
# The verbs a request may start with (KIT-333): the tech lead's rules name only these.
REQUEST_VERBS = ("plan", "replan", "approve")
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
        return "the account is not on this bridge's list of members who may answer"
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


def question_text(issue, requester, cfg, now, by_bot=False, dm=False):
    head, body, bind, _facts = question_parts(issue, requester, cfg, now, by_bot, dm)
    return head + body + "\n" + bind


def newest_run(runs):
    """The planning ticket with the highest number, or "" (KIT-244)."""
    return max((r.get("identifier") or "" for r in runs), key=ticket_number, default="")


def question_parts(issue, requester, cfg, now, by_bot=False, dm=False, runs=None, via=""):
    """(head, body, bind, facts): a plan question in the pieces the writer needs (KIT-297).
    The head asks, so the writer is never given it. The bind is what a yes agrees to, and
    who asked: posted verbatim, never the model's. head + body + newline + bind is the
    question as it always read. With `runs` (a list, perhaps empty) it is a re-plan
    question instead (KIT-244), naming the idea's newest planning run, and `via` the
    planning ticket that was named in its place."""
    title = notify.safe_title(issue.get("title") or "")
    creator = notify.safe_title(((issue.get("creator") or {}).get("displayName")
                                 or (issue.get("creator") or {}).get("name") or "someone"))
    state = notify.safe_title((issue.get("state") or {}).get("name") or "an unknown state")
    team = ((issue.get("team") or {}).get("key")) or "?"
    # A bot's request is credited in words, never @mentioned: a mention of the chat bot
    # in its own thread could start a chat session whose reply lands after the question.
    if by_bot:
        who = "the tech lead"
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
    bind = QUESTION_BIND % ("here" if dm else "in this thread", cfg["question_ttl_hours"],
                            then, who)
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

    def request(self, channel, msg, kind, ident):
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
                          "which is my limit. Ask again later." % len(recent),
                          kind="lookup-limit", facts={"looked_up": len(recent)})
                self.state["limit_said"][channel] = self.now
            self.mark(channel, msg, "refused: rate limit")
            self.counts["refused"] += 1
            return
        open_now = [q for q in self.state["questions"].values() if q.get("status") == OPEN]
        if len(open_now) >= self.cfg["max_open_questions"]:
            self.refuse_request(channel, msg, thread, "%d questions are already open, which "
                                "is my limit." % len(open_now), "too many open",
                                bind=TOO_MANY_OPEN_BIND)
            return
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
        if kind == "approve":
            self.request_approve(channel, msg, thread, requester, ident, by_bot, dm)
            return
        if kind == "replan":
            self.request_replan(channel, msg, thread, requester, ident, by_bot, dm)
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
                 question_parts(issue, requester, self.cfg, self.now, by_bot=by_bot, dm=dm))

    def ask(self, channel, msg, thread, kind, issue, requester, parts, children=None,
            extra=None):
        # The head and body, then the bind on a line of its own, as a question always read.
        # The writer is given the body only: the head asks, and only the bind may.
        head, body, bind, facts = parts
        posted = self.post(channel, thread, head + body, kind="question-" + kind, bind=bind,
                           join="\n", plain=body, facts=facts)
        self.state["questions"][key_of(channel, posted)] = {
            "kind": kind, "ticket": issue["identifier"], "ticket_id": issue.get("id"),
            "channel": channel, "thread": thread, "ts": posted, "asked_at": self.now,
            "asked_for": requester, "status": OPEN,
            # WHAT THE MEMBER SAW, and the terms the question stated: a yes acts only while
            # both still hold.
            "fingerprint": ticket_fingerprint(issue), "promised": self.promised(kind)}
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
# through one pass; this one pins what the module may reach (KIT-407).
# ══════════════════════════════════════════════════════════════════════════════════════
def selftest():
    import pipeline_bridge
    return pipeline_bridge.split_module_selftest(os.path.abspath(__file__))


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(selftest())
    print("usage: pipeline_bridge_requests.py --selftest (the bridge imports this module)")
    sys.exit(EXIT_USAGE)
