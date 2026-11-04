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
from pipeline_bridge_config import (ASK_AGAIN_BIND, CREDIT_BIND, YES_BIND,  # noqa: E402
                                    EXIT_USAGE, EXPIRED, OPEN, REFUSED, SLACK_USER_RE,
                                    TICKET_RE, _TICKET_WORD_RE, clock, credit_terms, key_of,
                                    label_names, load_pings, ticket_number, ts_float)
from pipeline_bridge_plans import (ANSWER, ANSWER_DEV_BIND, ANSWER_SHAPE_BIND,  # noqa: E402
                                   FOLLOWUP, FOLLOWUP_INTO)

# Each verb the bridge has, with the phrases a member's own ask may use for it (ACT-3). The
# strict grammar, DM help and the reader below all come from this table.
VERB_TABLE = (
    ("plan", (r"start\s+planning(?:\s+(?:for|on))?", r"plan")),
    ("replan", (r"re-?\s?plan", r"plan\s+again", r"retry\s+planning(?:\s+(?:for|on))?",
                r"re-?run\s+(?:the\s+)?planning(?:\s+(?:for|on))?")),
    ("approve", (r"approve(?:\s+the\s+plan)?",)),
)
# What the bridge says to anything else a member writes in its DM, at most once an hour.
DM_HELP = ("I can do %s and yes or no here. I read an ask in your own words too, such as "
           "`please replan <ticket id>`, with up to 10 tickets to plan or replan at once. To "
           "add an answer to a plan's open questions, send `answer <plan id>: <your answer>`. "
           "To answer a dev, reply in the thread under its message. For anything else, ask the "
           "tech lead in the chat channel for now."
           % ", ".join("`%s <ticket id>`" % verb for verb, _p in VERB_TABLE))
# KIT-334: `answer <plan id>: <words>`, read on its own and kept out of VERB_TABLE, whose
# looser reader and pinned phrasings it would shift. The words are everything after the
# colon, lines and all; with no colon, or nothing after it, there are none.
_ANSWER_RE = re.compile(r"^answer\s+(%s)\b([\s\S]*)$" % TICKET_RE, re.I)
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
# KIT-432: one question for several tickets of his own ask, each acted on in turn.
COMBINED_BIND = ("Reply %s here within %d hours, and %s. Reply `no` to drop them all, or "
                 "`yes <ticket id>` for just one. Merging stays yours. %s")
COMBINED_PLAN_THEN_BIND = "I'll hand each one to the planner"
COMBINED_REPLAN_THEN_BIND = "I'll hand each one back to the planner for a new plan"
COMBINED_HEADS = {"plan": "*Plan these?*", "replan": "*Plan these again?*"}
ASK_MAX_TICKETS = 10
ACT_OFF_THEN_BIND = "I'll only note your answer: acting is switched off here, so nothing moves"
TOO_MANY_OPEN_BIND = "Answer one, or let it expire, then ask again."
LOOKUP_LIMIT_BIND = "Ask again later."
ALREADY_ASKED_BIND = "Answer that one."
REPLAN_AGAIN_BIND = "To plan it again, type `replan %s`."
EDITED_BIND = "Send the answer again as a new reply."
# A message edited after it was sent, as the tech lead's path and an ask both say it (ACT-3).
FORWARD_EDITED_BIND = "Send it again as a new message."
# The verbs a request may start with (KIT-333): the tech lead's rules name only these.
REQUEST_VERBS = tuple(verb for verb, _phrases in VERB_TABLE)
# ACT-1, KIT-431: the tech lead's questions have one ceiling of their own, across every DM
# and the channel, never counted against the member's max_open_questions. Nothing acts
# without a member's yes, so it only stops a flood, and a normal day never reaches it: a
# newer one past it closes the oldest anywhere, and says so. A ticket
# is the member's own pick only when named in his DM within PICK_SECONDS, first by him, the
# bridge or a page whose mark only a configured job can raise: the two `agent:*` marks are
# raised by any author (pipeline_notify_local.py, marks_author_ok), so they never count.
TL_QUESTIONS_MAX = 20
PICK_SECONDS = 24 * 3600
ANY_AUTHOR_MARKS = (notify.ESC_BLOCKED, notify.ESC_NEEDS_HUMAN)
TL_LAPSED_SAYS = ("My question about %s has closed: the tech lead set up a newer one, and I "
                  "keep at most %d of its questions open across every DM and the channel.")
TL_REPLACED_SAYS = ("I've closed my question about %s that the tech lead set up, since you "
                    "asked for something else.")
# The whole grammar. A request is one line and nothing else; so is an answer.
REQUEST_RE = re.compile(r"^(%s)\s+(%s)$" % ("|".join(REQUEST_VERBS), TICKET_RE),
                        re.IGNORECASE)
YES_RE = re.compile(r"^yes(?:\s+(%s))?[\s.!]*$" % TICKET_RE, re.IGNORECASE)
NO_RE = re.compile(r"^no(?:\s+(%s))?[\s.!]*$" % TICKET_RE, re.IGNORECASE)

# ACT-3 (KIT-367): HIS OWN ASK in his DM, read by a fixed grammar and never a model, so a
# miss costs him one plain line and a wrong read can only ever ask. The design's probes
# (wf_4ba07fed-0a2) chose its words. Longer phrases are tried first.
_ASK_PHRASES = sorted(((verb, phrase) for verb, phrases in VERB_TABLE for phrase in phrases),
                      key=lambda vp: -len(vp[1]))
_ASK_VERB = "|".join("(?P<v%d>%s)" % (i, phrase) for i, (_v, phrase) in enumerate(_ASK_PHRASES))
_ASK_IDS = r"(?P<ids>%s(?:\s*(?:,|&|and|,\s*and)\s*%s){0,2})" % (TICKET_RE, TICKET_RE)
# The looser shape names more (KIT-432): the router asks about ASK_MAX_TICKETS of them.
_ASK_MANY_IDS = r"(?P<ids>%s(?:\s*(?:,|&|and|,\s*and)\s*%s){0,29})" % (TICKET_RE, TICKET_RE)
# The PLAIN ask: up to two polite words, a verb, 1-3 ids or `it`, one polite word after, one
# stop, and a re-plan's note after `note:`. Nothing else, and never a `?`.
_PLAIN_ASK_RE = re.compile(
    r"^(?:(?:please|pls|plz|ok(?:ay)?|go\s+ahead\s+and|can\s+you|could\s+you|let'?s|just|"
    r"kindly)[\s,]+){0,2}(?:%s)\s+(?:%s|(?P<pron>it|this|that|this\s+one|that\s+one))"
    r"(?:[\s,]+(?:please|pls|plz|thanks|thank\s+you|thx|now))?\s*[.!]?"
    r"(?:\s+note:\s*(?P<note>\S.*))?$" % (_ASK_VERB, _ASK_IDS), re.IGNORECASE | re.DOTALL)
# The LOOSER ask: the same clause after up to four lead words, then free words after a
# break. It only ever asks.
_LOOSER_ASK_RE = re.compile(
    r"^(?:(?:please|pls|plz|can\s+you|could\s+you|would\s+you|will\s+you|can\s+u|"
    r"go\s+ahead\s+and|ok(?:ay)?|yes|yeah|yep|yup|sure|great|cool|thanks|thx|alright|right|"
    r"so|hey|hi|just|let'?s|kindly|then|and)[\s,!.]+){0,4}(?:%s)\s+"
    r"(?:(?:the\s+idea|idea|ticket|the\s+plan(?:\s+for)?|plan)\s+)?(?:%s|(?P<pron>it|this|that|"
    r"this\s+one|that\s+one|the\s+idea|this\s+idea|that\s+idea|the\s+plan|this\s+plan))"
    r"(?:\s+(?:please|pls|plz|again|now|thanks|thank\s+you|thx|asap|for\s+me|too|then)){0,3}"
    r"\s*(?P<end>[.!?]+|$|[,;:\n—–-]+\s*)(?P<rest>.*)$" % (_ASK_VERB, _ASK_MANY_IDS),
    re.IGNORECASE | re.DOTALL)
# Anything that can flip, condition or delay an ask, or asks something of its own, sends it
# to the tech lead: a negation, a condition, a delay, a question word.
_ASK_TURNS_RE = re.compile(
    r"\b(?:don'?t|do\s+not|never|not|no|nope|stop|wait|hold|undo|revert|unless|if|when|once|"
    r"after|before|until|whether|later|tomorrow|tonight|eventually|sometime|someday|"
    r"next\s+(?:week|month)|instead|why|what|how|which|who|where|did|does|is\s+it|was\s+it|"
    r"\w+n't|cant|wont|didnt|doesnt|isnt|wasnt|arent|shouldnt|wouldnt|couldnt|aint)\b",
    re.IGNORECASE)
# So does a retraction or a joke, from the red team's pass, and an emoji code.
_ASK_TAKEN_BACK_RE = re.compile(
    r"\b(?:forget|scratch|nvm|never\s*mind|actually|wrong|sorry|disregard|ignore|typo|oops|"
    r"test|lol|lmao|haha|jk|kidding|joking|meant|seriously|whatever|nah)\b", re.IGNORECASE)
_ASK_EMOJI_RE = re.compile(r":[a-z0-9_+'-]+:", re.IGNORECASE)
_ASK_MORE_VERBS_RE = re.compile(
    r"\b(?:re-?\s?plan|plan|approve|pause|resume|cancel|retry|start)\b", re.IGNORECASE)
# A hedge is kept, never refused: the read records it, and ACT-4 never acts on one.
_ASK_HEDGE_RE = re.compile(
    r"\b(?:maybe|perhaps|might|probably|possibly|think|consider|should|could|would)\b",
    re.IGNORECASE)
_ASK_YES_RE = re.compile(r"^(?:yes|yeah|yep|yup)\b", re.IGNORECASE)
ASK_MAX_CHARS = 300


def read_ask(text):
    """A member's own DM message read as an ask (ACT-3), or None when it is not one.

    Returns {"verb", "ids" (upper-cased, or [] when a pronoun stands for the ticket), "it",
    "shape" ("plain" or "looser"), "question" (it holds a `?`), "hedge" (its free words hold
    one), "note" (a re-plan's words after `note:`)}."""
    t = (text or "").replace("’", "'").replace("‘", "'").strip()
    t = t.strip("*_`~").strip()
    if not t or len(t) > ASK_MAX_CHARS or _ASK_EMOJI_RE.search(t):
        return None
    plain = (None if "\n" in t.split("note:", 1)[0] or "?" in t else _PLAIN_ASK_RE.match(t))
    m = plain or _LOOSER_ASK_RE.match(t)
    if not m:
        return None
    verb = next(_ASK_PHRASES[i][0] for i in range(len(_ASK_PHRASES)) if m.group("v%d" % i))
    note = (m.group("note") or "").strip() if plain else ""
    if note and verb != "replan":
        return None
    # A plain ask's note is free words too: a turn or a retraction in it sends the message
    # to the tech lead, as in the looser shape, since ACT-4 acts on a plain ask without a
    # yes (review of #280).
    if note and (_ASK_TURNS_RE.search(note) or _ASK_TAKEN_BACK_RE.search(note)):
        return None
    rest = "" if plain else (m.group("rest") or "")
    if not plain:
        # The verb's own words never count against it: its "plan" is no second verb.
        head = re.sub(_ASK_VERB, " ", t[:m.start("rest")], count=1, flags=re.IGNORECASE)
        if (_ASK_TURNS_RE.search(head) or _ASK_TURNS_RE.search(rest)
                or _ASK_TAKEN_BACK_RE.search(t) or t.count("?") > 1
                or re.search(r"\?\s*!", t) or _TICKET_WORD_RE.search(rest)
                or _ASK_MORE_VERBS_RE.search(rest)):
            return None
    ids = [] if m.group("pron") else [w.upper() for w in _TICKET_WORD_RE.findall(
        m.group("ids") or "")]
    return {"verb": verb, "ids": ids, "it": bool(m.group("pron")),
            "shape": "plain" if plain else "looser", "question": "?" in t,
            "hedge": bool(_ASK_HEDGE_RE.search(rest)), "note": note}
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


def answer_text(msg, own_user_id):
    """The message as read_answer reads it (KIT-334): as Slack sent it, with a leading
    mention of the bridge itself dropped and nothing else changed, so the words keep their
    lines and their markup for answer_words to judge."""
    text = (msg.get("text") or "").strip()
    m = _LEADING_MENTION_RE.match(text)
    if m and m.group(1) == own_user_id:
        text = text[m.end():]
    return text.strip()


def read_answer(text):
    """(ticket id, words) for an `answer` line, or None for any other message (KIT-334).
    The words are as typed after the colon, trimmed only, and "" with no colon or nothing
    after it. A line naming no ticket is no answer line."""
    m = _ANSWER_RE.match(text or "")
    if not m:
        return None
    rest = m.group(2).lstrip()
    return m.group(1).upper(), (rest[1:].strip() if rest.startswith(":") else "")


def ask_text(msg, own_user_id):
    """The message as read_ask reads it: as command_text has it, but with its lines kept,
    since a second line is never part of a plain ask (ACT-3)."""
    text = notify.decode_chat_entities(msg.get("text") or "").strip()
    m = _LEADING_MENTION_RE.match(text)
    if m and m.group(1) == own_user_id:
        text = text[m.end():]
    return "\n".join(re.sub(r"[ \t]+", " ", line).strip()
                     for line in text.strip().splitlines())


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
        if channel in self.dm_channels and not reply:
            # KIT-334: an answer line, read before every other reading, routed by its id.
            answered = read_answer(answer_text(msg, self.me["user_id"]))
            if answered is not None:
                self.route_answer(channel, msg, *answered)
                return
        req = REQUEST_RE.match(text)
        if req:
            self.request(channel, msg, req.group(1).lower(), req.group(2).upper())
            return
        if channel in self.dm_channels and not reply and self.own_ask(channel, msg):
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

    def route_answer(self, channel, msg, ident, words):
        """KIT-334: an answer line in a DM, routed by what its id names, never by its words.
        A dev waiting on an answer is ACT-6's (KIT-370): until it lands, the line says how to
        answer one today. Anything else goes through request()'s limits to a plan's answer
        question, which refuses what is no plan of the planner's."""
        why = message_refusal(msg, self.me)
        if why:
            self.refuse_request(channel, msg, None, "I didn't read that answer for %s, since %s."
                                % (ident, why), "answer refused", ticket=ident)
            return
        if not words:
            self.refuse_request(channel, msg, None, "", "answer with no words",
                                bind=ANSWER_SHAPE_BIND % ident, ticket=ident)
            return
        if self.has_waiting_ping(ident):
            self.refuse_request(channel, msg, None, "", "answer for a dev",
                                bind=ANSWER_DEV_BIND % ident, ticket=ident)
            return
        self.request(channel, msg, ANSWER, ident, words=words)

    def own_ask(self, channel, msg):
        """ACT-3 (KIT-367): his own top-level message in his DM, read by the fixed grammar as
        an ask for one ticket, becomes the bridge's own question, with every check a typed
        command gets and no tech-lead turn. An ask naming `it`, or more than one ticket, is
        left to the paths after this, as today (ACT-5, KIT-432). What the read found stays
        on the message's record, for ACT-4. True when the message was handled here."""
        if (msg.get("bot_id") or msg.get("app_id") or msg.get("subtype")
                or not msg.get("user")):
            return False
        text = ask_text(msg, self.me["user_id"])
        read = read_ask(text)
        if read is None or not read["ids"]:            # `it` reads with no ids
            return False
        ident = read["ids"][0]
        if msg.get("edited"):
            # Refused aloud, as the tech lead's path refuses an edited message.
            self.refuse_request(channel, msg, None, "I didn't ask about %s, since your "
                                "message was edited after it was sent."
                                % ", ".join(read["ids"]), "edited",
                                bind=FORWARD_EDITED_BIND, ticket=ident)
        elif len(read["ids"]) > 1:
            self.combined_ask(channel, msg, read)
        elif _ASK_YES_RE.match(text) and any(
                q.get("status") == OPEN and q.get("channel") == channel
                and q.get("kind") == read["verb"] and q.get("ticket") == ident
                for q in self.state["questions"].values()):
            self.answer(channel, msg, True, ident)     # "yes, plan PROD-6": `yes PROD-6`
        else:
            self.request(channel, msg, read["verb"], ident)
        rec = self.state["handled"].get(key_of(channel, msg.get("ts")))
        if isinstance(rec, dict):
            rec["read"] = dict((k, read[k]) for k in ("shape", "question", "hedge", "note"))
        return True

    def combined_ask(self, channel, msg, read):
        """KIT-432: his own ask naming several tickets to plan or replan gets ONE question
        listing each, and one yes acts on each in turn. Each ticket takes every check
        request() makes, and one that fails is named as left out, as is each past
        ASK_MAX_TICKETS. A ticket the tech lead named first gets its own `yes <id>` question
        (ACT-1), and so does each approve: its question is the only place Slack lists a
        plan's pieces."""
        # One ticket named twice is asked about once (review of #291).
        verb, ids = read["verb"], list(dict.fromkeys(read["ids"]))
        named, past = ids[:ASK_MAX_TICKETS], ids[ASK_MAX_TICKETS:]
        mine = ([i for i in named if self.picked_by_member(channel, msg, i)]
                if verb in COMBINED_HEADS else [])
        for ident in named:
            if ident not in mine:
                self.request(channel, msg, verb, ident)      # a question of its own
        self._collect = collected = []
        try:
            for ident in mine:
                self.request(channel, msg, verb, ident)
        finally:
            self._collect = None
        asks = [c for c in collected if "record" in c]
        seen = set(c["ticket"] for c in collected)
        left = (["%s: %s" % (c["ticket"], c["left"]) for c in collected if "left" in c]
                + ["%s: I couldn't ask about it now." % i for i in mine if i not in seen]
                + ["%s: one message can name at most %d." % (i, ASK_MAX_TICKETS)
                   for i in past])
        if len(asks) < 2:
            # Fewer than two to ask: each that passed is asked on its own, and the rest said.
            for a in asks:
                self.ask_one_collected(channel, msg, a)
            if left:
                self.post(channel, None, "Left out: %s" % " ".join(left),
                          kind="request-refused", facts={"tickets": [c["ticket"]
                                                                     for c in collected]})
            if not asks:
                self.mark(channel, msg, "refused: none of %d" % len(ids))
            return
        bodies = "\n".join("- " + a["parts"][1].strip() for a in asks)
        if left:
            bodies += "\n\nLeft out: " + " ".join(left)
        then = (ACT_OFF_THEN_BIND if not self.cfg["act"] else COMBINED_PLAN_THEN_BIND
                if verb == "plan" else COMBINED_REPLAN_THEN_BIND)
        bind = COMBINED_BIND % (YES_BIND, self.cfg["question_ttl_hours"], then,
                                CREDIT_BIND % ("<@%s>" % msg.get("user")))
        posted = self.post(channel, None, COMBINED_HEADS[verb] + "\n" + bodies,
                           kind="question-combined", bind=bind, join="\n", plain=bodies,
                           facts={"tickets": [a["ticket"] for a in asks],
                                  "needs": "his answer to the fixed line after this message"})
        group = key_of(channel, posted)
        for a in asks:
            self.state["questions"]["%s#%s" % (group, a["ticket"])] = dict(
                a["record"], ts=posted, group=group)
        self.mark(channel, msg, "asked: %s %s" % (verb, ", ".join(a["ticket"] for a in asks)))
        self.counts["asked"] += len(asks)
        self.say("  asked: %s %s, in one question (requested by %s)"
                 % (verb, ", ".join(a["ticket"] for a in asks), msg.get("user") or "?"))

    def ask_one_collected(self, channel, msg, a):
        """A collected question asked on its own, as ask() would have asked it."""
        head, body, bind, facts = a["parts"]
        posted = self.post(channel, None, head + body, kind="question-" + a["record"]["kind"],
                           bind=bind, join="\n", plain=body, facts=facts)
        self.state["questions"][key_of(channel, posted)] = dict(a["record"], ts=posted)
        # Marked as ask() marks it, so the message is never read again (review of #291).
        self.mark(channel, msg, "asked: %s %s" % (a["record"]["kind"], a["ticket"]))
        self.counts["asked"] += 1
        self.say("  asked: %s %s (requested by %s)" % (a["record"]["kind"], a["ticket"],
                                                       msg.get("user") or "?"))

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
        if getattr(self, "_collect", None) is not None:
            # KIT-432: a combined question's ticket that fails a check is named as left out.
            self._collect.append({"ticket": ticket, "left": text + (" " + bind if bind else "")})
            return
        self.post(channel, thread, text, kind="request-refused", bind=bind, join=" ",
                  facts={"ticket": ticket} if ticket else None)
        self.mark(channel, msg, "refused: %s" % why)
        self.counts["refused"] += 1

    def request(self, channel, msg, kind, ident, set_up=None, words=None):
        """One request line. `set_up`, for a line the tech lead set up from a DM question
        (ACT-1), is that question's forward: its record's key and asks, and the ask the
        reply's block named. `words` are an answer line's (KIT-334)."""
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
        # A combined question's tickets collected so far count too (review of #291).
        mine += [c for c in (getattr(self, "_collect", None) or []) if "record" in c]
        if not by_bot and len(mine) >= self.cfg["max_open_questions"]:
            self.refuse_request(channel, msg, thread, "%d questions are already open, which "
                                "is my limit." % len(mine), "too many open",
                                bind=TOO_MANY_OPEN_BIND, ticket=ident)
            return
        for q in open_now:
            if (q.get("ticket") == ident and dm and not by_bot and q.get("by_tech_lead")
                    and kind != ANSWER
                    and q.get("channel") == channel):
                # His own ask on a ticket the tech lead's open question is about (ACT-1): the
                # same action answers it as `yes <id>`; another closes it, and goes ahead.
                if q.get("kind") == kind:
                    self.answer(channel, msg, True, ident)
                    return
                self.post(channel, None, TL_REPLACED_SAYS % ident, kind="question-replaced",
                          facts={"ticket": ident})
                q.update(status=REFUSED, closed_by="they asked for something else")
                self.say("  closed the tech lead's question on %s: %s asked for %s"
                         % (ident, msg.get("user"), kind))
        open_now = [q for q in open_now if q.get("status") == OPEN]
        for q in open_now:
            # An answer line is held back only by another answer on the plan: its approve
            # question is closed once the answer's is asked (KIT-334).
            if q.get("ticket") == ident and (kind != ANSWER or q.get("kind") == ANSWER):
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
        if kind == ANSWER:
            self.request_answer(channel, msg, thread, requester, ident, words, terms=terms)
            return
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
        record = {
            "kind": kind, "ticket": issue["identifier"], "ticket_id": issue.get("id"),
            "channel": channel, "thread": thread, "ts": None, "asked_at": self.now,
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
            record["children"] = children
        if extra:
            # More of what the member saw (a re-plan's newest run and move, KIT-244).
            record.update(extra)
        if getattr(self, "_collect", None) is not None:
            # KIT-432: one of a combined question's tickets, asked in one post after.
            self._collect.append({"ticket": issue["identifier"], "parts": parts,
                                  "record": record})
            return
        # A question quoting a member's words goes out word for word, never by the writer:
        # a yes writes exactly what it quotes (KIT-334).
        posted = self.post(channel, thread, head + body,
                           kind="" if kind == ANSWER else "question-" + kind, bind=bind,
                           join="\n", plain=body, facts=facts)
        record["ts"] = posted
        self.state["questions"][key_of(channel, posted)] = record
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
            q.update(status=EXPIRED, closed_by="at most %d of yours stay open across every DM "
                     "and the channel, and a newer one was set up" % TL_QUESTIONS_MAX)
            self.say("  closed the tech lead's oldest open question, on %s: past its cap of %d"
                     % (q["ticket"], TL_QUESTIONS_MAX))

    def promised(self, kind, team=None):
        """The settings a question's text states, as they are now. Recorded when it is
        asked: a yes acts only on the terms the member read."""
        terms = {"act": self.cfg["act"], "question_ttl_hours": self.cfg["question_ttl_hours"]}
        if kind == "approve":
            terms.update(ready_state=self.cfg["ready_state"], epic_wip=self.cfg["epic_wip"],
                         agent_user_name=self.cfg["agent_user_name"])
        elif kind == ANSWER:
            # What a yes writes, and where: the words the question quotes (KIT-334).
            terms.update(writes="the end of the plan's description")
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
    from pipeline_bridge import (ALICE, BOB, BOT, CHATBOT, DM, EXPIRED, ME, OPEN, REFUSED,
                                 clock, state_path)
    with kit as _tmp:
        check, now, outcome, run = kit.take("check", "now", "outcome", "run")
        asked_tl, tl_says, tl_root, dm_lines = kit.take("asked_tl", "tl_says", "tl_root",
                                                        "dm_lines")
        tl_on, tlworld, world, in_room = kit.take("tl_on", "tlworld", "world", "in_room")
        named_yes = "The tech lead picked PROD-6, so the yes must name it: reply `yes PROD-6`."
        BOB_DM = "D0BOB00001"                          # the fake Slack's DM with BOB

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
            c, s, t = set_up("rq-her-pick", ["is PROD-6 ready to plan?"], "plan PROD-6")
            asked = dm_lines(s)[-1]
            hers = [m for m in s.messages if m.get("text") == "is PROD-6 ready to plan?"][0]
            s.add(ALICE, "yes", channel=DM)
            run(c, s, t, at=now + 180)
            # The credit names her message's own time, on the clock the pass asked by.
            return (asked.endswith("Set up by the tech lead after your message at %s. You "
                                   "named PROD-6." % clock(float(hers["ts"]), now + 120)),
                    "Reply `yes` here" in asked,
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
            s.add(ALICE, "what about PROD-6, then?", channel=DM)
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
            s.add(ALICE, "is PROD-6 ready, then?", channel=DM)
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

        # 7b. A question the tech lead set up is its naming, never hers: after she says no,
        #     her own `plan PROD-6` is her ask on its pick.
        def case_its_question_names_it():
            c, s, t = set_up("rq-its-question", ["what should we plan next?"], "plan PROD-6")
            s.add(ALICE, "no", channel=DM)
            run(c, s, t, at=now + 180)
            s.add(ALICE, "plan PROD-6", channel=DM)
            run(c, s, t, at=now + 240)
            return dm_lines(s)[-1].endswith("The tech lead picked PROD-6.")
        check("rq-a-question-the-tech-lead-set-up-is-its-naming-never-hers",
              outcome(case_its_question_names_it), True)

        # 8. The tech lead's questions have one ceiling, 20 across every DM and the channel
        #    (KIT-431): a 21st closes the oldest, and says so. They never count against hers.
        def more_tickets(_c, _s, t, upto=28):
            for n in range(7, upto):
                t.issues["PROD-%d" % n] = dict(t.issues["PROD-6"], id="uuid-%d" % n,
                                               identifier="PROD-%d" % n)

        def case_cap():
            c, s, t = set_up("rq-cap", ["plan PROD-5", "what else should we plan?"],
                             "\n".join("plan PROD-%d" % n for n in range(6, 27)),
                             before=more_tickets, max_open_questions=2)
            s.add(ALICE, "plan PROD-27", channel=DM)       # her second: the tech lead's
            run(c, s, t, at=now + 180)                     # twenty never count against her
            lapsed = [x for x in dm_lines(s) if x.startswith("My question about")]
            return questions(c), lapsed
        check("rq-a-question-past-the-ceiling-closes-the-oldest-and-hers-are-not-counted",
              outcome(case_cap),
              (dict([("PROD-5", OPEN), ("PROD-6", EXPIRED)]
                    + [("PROD-%d" % n, OPEN) for n in range(7, 28)]),
               ["My question about PROD-6 has closed: the tech lead set up a newer one, and I "
                "keep at most 20 of its questions open across every DM and the channel. Ask "
                "again with `plan PROD-6`."]))

        # 8b. No cap for each DM (KIT-431): below the ceiling, its questions in two members'
        #     DMs and in the channel never close each other, however many one DM holds.
        def case_no_cap_per_place():
            c, s, t = world("rq-one-ceiling", dm_user_ids=[ALICE, BOB], **tl_on)
            run(c, s, t)                                   # the first pass: baselines
            more_tickets(c, s, t, upto=12)
            s.add(ALICE, "what should we plan?", channel=DM)
            s.add(BOB, "and what about mine?", channel=BOB_DM)
            run(c, s, t, at=now + 60)
            roots = dict((r["dm_channel"], r["root"])
                         for r in json.load(open(state_path(c)))["forwards"].values())
            tl_says(s, roots[DM], "For ask 1:\nplan PROD-6\nplan PROD-7\nplan PROD-8\n"
                                  "plan PROD-9")
            tl_says(s, roots[BOB_DM], "For ask 1:\nplan PROD-10")
            s.add(CHATBOT, "plan PROD-11", bot_id="B0CHATBOT1", app_id="A0CHATAPP1")
            run(c, s, t, at=now + 120)
            return questions(c)
        check("rq-below-the-ceiling-two-dms-and-the-channel-never-close-each-other",
              outcome(case_no_cap_per_place), dict(("PROD-%d" % n, OPEN) for n in range(6, 12)))


def cases_reader(kit):
    """ACT-3 (KIT-367): the fixed reader of his own ask, over the design's 100 phrasings
    (wf_4ba07fed-0a2: the design's probe, the judge's pass and the red team's), and the way
    a read ask takes through a pass."""
    import json
    from pipeline_bridge import ALICE, CONFIRMED, DM, state_path
    with kit as _tmp:
        check, now, outcome, run = kit.take("check", "now", "outcome", "run")
        dmworld, tlworld, world, in_room = kit.take("dmworld", "tlworld", "world", "in_room")
        dm_lines, in_dm, dm_help = kit.take("dm_lines", "in_dm", "dm_help")

        def landing(text):
            r = read_ask(text)
            if r is None:
                return "tech lead"
            what = "%s %s %s" % (r["shape"], r["verb"], "it" if r["it"] else ",".join(r["ids"]))
            return what + (" ?" if r["question"] else "") + (" hedge" if r["hedge"] else "")

        # 1. THE 100 PHRASINGS, each where the review put it. "tech lead" is where it goes
        #    today; any read asks the bridge's own question, and only a plain one, or a
        #    looser one with no `?` and no hedge, may ever act without a yes (ACT-4).
        DESIGN = [
            ('replan TOD-140', 'plain replan TOD-140'),
            ('replan TOD-140 please', 'plain replan TOD-140'),
            ('Replan tod-140.', 'plain replan TOD-140'),
            ("pls replan TOD-140, the gate's fixed now", 'looser replan TOD-140'),
            ('can you replan TOD-140?', 'looser replan TOD-140 ?'),
            ('could you re-plan TOD-140 please', 'plain replan TOD-140'),
            ('ok replan TOD-140 again', 'looser replan TOD-140'),
            ('yes, replan TOD-140', 'looser replan TOD-140'),
            ('go ahead and replan TOD-140 thanks', 'plain replan TOD-140'),
            ('replan TOD-140 and TOD-141', 'plain replan TOD-140,TOD-141'),
            ('plan TOD-160 please', 'plain plan TOD-160'),
            ('approve TOD-150', 'plain approve TOD-150'),
            ('approve the plan TOD-150, looks good', 'looser approve TOD-150'),
            ('retry planning for TOD-140', 'plain replan TOD-140'),
            ('replan it', 'plain replan it'),
            ('yeah replan this one, I fixed the rubric', 'looser replan it'),
            ('`replan TOD-140`', 'plain replan TOD-140'),
            ('*replan TOD-140*', 'plain replan TOD-140'),
            ('hey can you replan TOD-140 — the gate was wrong', 'tech lead'),
            ("don't replan TOD-140", 'tech lead'),
            ('do not replan TOD-140 yet', 'tech lead'),
            ('should we replan TOD-140?', 'tech lead'),
            ('did you replan TOD-140?', 'tech lead'),
            ('why did TOD-140 need a replan', 'tech lead'),
            ('replan TOD-140 if the gate fix merged', 'tech lead'),
            ('replan TOD-140 when you get a chance', 'tech lead'),
            ('replan TOD-140 tomorrow', 'tech lead'),
            ('replan TOD-140 sounds wrong to me', 'tech lead'),
            ('replan TOD-140, TOD-141 is fine', 'tech lead'),
            ('replan TOD-141 instead of TOD-140', 'tech lead'),
            ('replan TOD-140 and approve TOD-150', 'tech lead'),
            ('maybe replan TOD-140', 'tech lead'),
            ('I think we should replan TOD-140', 'tech lead'),
            ('the replan of TOD-140 failed again', 'tech lead'),
            ('what happens if I replan TOD-140?', 'tech lead'),
            ('replan TOD-140? or wait for the fix', 'tech lead'),
            ('no, replan TOD-140', 'tech lead'),
            ('replan TOD-140 not TOD-141', 'tech lead'),
            ('can you explain why TOD-140 failed and replan it', 'tech lead'),
            ('I want dark mode', 'tech lead'),
            ('plan the dark mode one', 'tech lead'),
            ('replan TOD-140 and tell me why it failed', 'tech lead'),
            ('replan TOD-140 lol jk', 'tech lead'),
            ('replan TOD-140 (not really)', 'tech lead'),
            ('replan TOD-140 just kidding', 'tech lead'),
            ('approve TOD-150 once tests pass', 'tech lead'),
            ('approve TOD-150 after lunch', 'tech lead'),
            ('> replan TOD-140', 'tech lead'),
            ('he said replan TOD-140', 'tech lead'),
            ('replan TOD-140???', 'tech lead'),
            ('REPLAN TOD-140 PLEASE', 'plain replan TOD-140'),
            ('replan TOD-140, TOD-141 and TOD-142', 'plain replan TOD-140,TOD-141,TOD-142'),
            ('replan TOD-140, TOD-141, TOD-142 and TOD-143', 'looser replan TOD-140,TOD-141,TOD-142,TOD-143'),
        ]
        JUDGE = [
            ('replan TOD-140... actually forget it', 'tech lead'),
            ('replan TOD-140 lol', 'tech lead'),
            ('replan TOD-140 lmao', 'tech lead'),
            ('replan TOD-140, but only the backend part', 'looser replan TOD-140'),
            ('replan TOD-140 haha', 'tech lead'),
            ('plan TOD-140 or TOD-141, whichever is ready', 'tech lead'),
            ('replan TOD-140, no rush', 'tech lead'),
            ('replan TOD-140 at some point', 'tech lead'),
            ('replan TOD-140 eventually', 'tech lead'),
            ('replan TOD-140 next week', 'tech lead'),
            ('replan TOD-140 sometime', 'tech lead'),
            ('can you replan TOD-140 or is that a bad idea', 'tech lead'),
            ('replan TOD-140 — scratch that', 'tech lead'),
            ('replan TOD-140 nvm', 'tech lead'),
            ('start TOD-150', 'tech lead'),
            ('cancel TOD-150', 'tech lead'),
            ('pause TOD-150 for now', 'tech lead'),
            ('approve TOD-150 lgtm', 'tech lead'),
            ('plan TOD-14O', 'tech lead'),
            ("replan TOD-140, I'm not sure though", 'tech lead'),
            ('replan TOD-140 ignore that', 'tech lead'),
            ('replan TOD-140 (typo, meant TOD-141)', 'tech lead'),
            ('replan TOD-140 → TOD-141', 'tech lead'),
            ("Replan TOD-140 pls. Also what's up with TOD-141?", 'tech lead'),
            ('replan TOD-140\nalso check TOD-141', 'tech lead'),
            ('so replan it then', 'looser replan it'),
            ('replan the login one', 'tech lead'),
        ]
        RED_TEAM = [
            ('replan TOD-140??? it was just planned', 'tech lead'),
            ('replan TOD-140?! it has a plan already', 'tech lead'),
            ('retry TOD-151??? it just failed twice', 'tech lead'),
            ('start TOD-150?? the tech lead wants that?', 'tech lead'),
            ('start TOD-150?? seriously', 'tech lead'),
            ('replan TOD-140, wrong one', 'tech lead'),
            ('replan TOD-140, sorry wrong chat', 'tech lead'),
            ('replan TOD-140. disregard', 'tech lead'),
            ('replan TOD-140. typo', 'tech lead'),
            ('replan TOD-140 - oops', 'tech lead'),
            ('replan TOD-140... meant for someone else', 'tech lead'),
            ('replan TOD-140? :thinking_face:', 'tech lead'),
            ('replan TOD-140?', 'looser replan TOD-140 ?'),
            ('start TOD-150? the tech lead said to type that', 'tech lead'),
            ('resume TOD-200, the tech lead suggested it', 'tech lead'),
            ('replan it?? why', 'tech lead'),
            ('replan it??? it literally just ran', 'tech lead'),
            ('plan TOD-160. Test message, ignore', 'tech lead'),
            ('start TOD-150 :x:', 'tech lead'),
            ('retry TOD-151 :no_entry:', 'tech lead'),
        ]
        for name, rows in (("design", DESIGN), ("judge", JUDGE), ("red-team", RED_TEAM)):
            check("rd-the-%s-phrasings-land-as-reviewed" % name,
                  [(text, landing(text), want) for text, want in rows
                   if landing(text) != want], [])
        every = DESIGN + JUDGE + RED_TEAM
        check("rd-of-100-phrasings-only-14-are-plain",
              (len(every), sum(landing(text).startswith("plain ") for text, _w in every)),
              (100, 14))

        def could_act(text):
            r = read_ask(text)
            return bool(r) and (r["shape"] == "plain" or not (r["question"] or r["hedge"]))
        check("rd-none-of-the-red-teams-20-could-act-without-a-yes",
              (len(RED_TEAM), [text for text, _w in RED_TEAM if could_act(text)]), (20, []))
        check("rd-a-hedge-a-note-a-second-line-and-what-is-never-an-ask",
              ([landing(x) for x in ("replan TOD-140, I think the gate is fixed",
                                     "replan TOD-140\nthanks", "plan TOD-140 note: soon",
                                     "plan TOD-140 :rocket:", "plan TOD-140. " + "word " * 60,
                                     "plan TOD-140, then approve it",
                                     "replan TOD-140 note: is the gate fixed?")],
               (read_ask("replan TOD-140 note: the gate is fixed") or {}).get("note")),
              (["looser replan TOD-140 hedge", "looser replan TOD-140", "tech lead",
                "tech lead", "tech lead", "tech lead", "tech lead"], "the gate is fixed"))
        # A plain ask's note is free words too: one that turns or takes the ask back goes to
        # the tech lead, as the looser shape's words do (review of #280).
        check("rd-a-plain-asks-note-that-turns-or-takes-it-back-goes-to-the-tech-lead",
              [landing(x) for x in ("replan TOD-140 note: actually never mind, lol",
                                    "replan TOD-140 note: don't, jk",
                                    "replan TOD-140 note: wait until the gate is fixed",
                                    "replan TOD-140 note: oops, wrong ticket",
                                    "replan TOD-140 note: the gate is fixed")],
              ["tech lead", "tech lead", "tech lead", "tech lead", "plain replan TOD-140"])

        # 2. HIS ASK THROUGH A PASS. One ticket: the bridge's own question, with no turn of
        #    the tech lead's, and the read kept for ACT-4. Two tickets, `it`, or a turn: the
        #    tech lead, as today (KIT-432, ACT-5).
        def ask_and_see(name, text, **extra):
            c, s, t = tlworld(name)
            ts = s.add(ALICE, text, channel=DM, **extra)
            run(c, s, t, at=now + 60)
            st = json.load(open(state_path(c)))
            return (dm_lines(s)[-1:], len(in_room(s)), sorted(q["ticket"] for q in st[
                "questions"].values()), (st["handled"].get(key_of(DM, ts)) or {}).get("read"))
        asked = outcome(lambda: ask_and_see("rd-one", "can you plan PROD-6 please?"))
        check("rd-his-own-ask-for-one-ticket-is-the-bridges-question-and-its-read-is-kept",
              (asked[0][0].startswith("*Plan this?* PROD-6"),) + tuple(asked[1:]),
              (True, 0, ["PROD-6"], {"shape": "looser", "question": True, "hedge": False,
                                     "note": ""}))
        # A re-plan's note is kept on the read too, for ACT-4 to pass on (review of #280).
        noted = outcome(lambda: ask_and_see("rd-note", "replan PROD-6 note: the gate is fixed"))
        check("rd-a-replans-note-is-kept-on-its-read",
              noted[3] if isinstance(noted, tuple) else noted,
              {"shape": "plain", "question": False, "hedge": False, "note": "the gate is fixed"})
        check("rd-a-turn-or-it-goes-to-the-tech-lead-as-today",
              [outcome(lambda: ask_and_see("rd-%s" % n, text)[1:3])
               for n, text in (("two", "is PROD-6 or PROD-7 worth planning?"),
                               ("turn", "don't plan PROD-6"), ("it", "plan it"))]
              + [outcome(lambda: ask_and_see("rd-bot", "please plan PROD-6",
                                             bot_id="B0OTHER001")[2])],
              [(1, []), (1, []), (1, []), []])
        check("rd-an-edited-ask-is-refused-aloud",
              outcome(lambda: ask_and_see("rd-edited", "please plan PROD-6",
                                          edited={"user": ALICE, "ts": "1"})[:3]),
              (["I didn't ask about PROD-6, since your message was edited after it was sent. "
                "Send it again as a new message."], 0, []))

        # 3. "yes, plan PROD-6" with that question open answers it, as `yes PROD-6`, rather
        #    than asking it again; with the tech lead off, two tickets get the help line;
        #    and a channel reads only the strict grammar.
        def case_yes_names_it():
            c, s, t = tlworld("rd-yes")
            s.add(ALICE, "plan PROD-6", channel=DM)
            run(c, s, t, at=now + 60)
            s.add(ALICE, "yes, plan PROD-6", channel=DM)
            run(c, s, t, at=now + 120)
            st = json.load(open(state_path(c)))
            return ([q["status"] for q in st["questions"].values()],
                    dm_lines(s)[-1].startswith("Confirmed by <@%s>" % ALICE))
        check("rd-yes-naming-an-open-questions-ask-answers-it", outcome(case_yes_names_it),
              ([CONFIRMED], True))

        def case_help_and_channel():
            c, s, t = dmworld("rd-help")
            s.add(ALICE, "what's up with PROD-6?", channel=DM)
            s.add(ALICE, "please plan PROD-6")              # the channel: not the grammar
            run(c, s, t, at=now + 60)
            st = json.load(open(state_path(c)))
            return in_dm(s)[-1:], st["questions"]
        check("rd-tech-lead-off-a-question-gets-the-help-and-a-channel-reads-only-the-grammar",
              outcome(case_help_and_channel), ([(None, dm_help)], {}))


def cases_combined(kit):
    """ACT-3b (KIT-432): his own ask naming several tickets to plan or replan gets one
    question listing each, and one yes acts on each in turn."""
    import json
    from pipeline_bridge import ALICE, DECLINED, DONE, DM, OPEN, state_path
    with kit as _tmp:
        check, now, outcome, run = kit.take("check", "now", "outcome", "run")
        dmworld, dm_lines = kit.take("dmworld", "dm_lines")
        asked_tl, tl_says, tl_root = kit.take("asked_tl", "tl_says", "tl_root")

        def questions(c):
            return json.load(open(state_path(c)))["questions"]

        def tickets(t, upto):
            for n in range(7, upto):
                t.issues["PROD-%d" % n] = dict(t.issues["PROD-6"], id="uuid-%d" % n,
                                               identifier="PROD-%d" % n, title="Item %d" % n)

        def combined(name, text, answer=None, upto=8, **over):
            c, s, t = dmworld(name, **dict({"act": True}, **over))
            tickets(t, upto)
            s.add(ALICE, text, channel=DM)
            run(c, s, t, at=now + 60)
            asked = dm_lines(s)[-1]
            if answer:
                s.add(ALICE, answer, channel=DM)
                run(c, s, t, at=now + 120)
            qs = questions(c)
            return c, s, t, asked, qs

        # 1. One question for both, then one yes moves each; `yes <id>` moves one; `no` none.
        def case_both():
            _c, _s, t, asked, qs = combined("cb-both", "please plan PROD-6 and PROD-7", "yes")
            return (asked.startswith("*Plan these?*\n- PROD-6"), "- PROD-7" in asked,
                    asked.endswith("Reply `yes` here within 24 hours, and I'll hand each one to "
                                   "the planner. Reply `no` to drop them all, or `yes <ticket "
                                   "id>` for just one. Merging stays yours. (Asked for by "
                                   "<@%s>.)" % ALICE),
                    len(set(q["group"] for q in qs.values())), sorted(q["status"] for q in
                                                                      qs.values()),
                    sorted(i for i, _s in t.moves))
        check("cb-two-tickets-get-one-question-and-one-yes-acts-on-each",
              outcome(case_both), (True, True, True, 1, [DONE, DONE], ["uuid-6", "uuid-7"]))
        check("cb-yes-naming-one-acts-on-that-one-and-no-drops-them-all",
              (outcome(lambda: sorted((q["ticket"], q["status"]) for q in combined(
                  "cb-one", "plan PROD-6 and PROD-7", "yes PROD-7")[4].values())),
               outcome(lambda: sorted(q["status"] for q in combined(
                   "cb-no", "plan PROD-6 and PROD-7", "no")[4].values()))),
              ([("PROD-6", OPEN), ("PROD-7", DONE)], [DECLINED, DECLINED]))

        # 2. A ticket that fails a check, and each past ten, are named as left out.
        def case_left_out():
            asked = combined("cb-left", "plan PROD-6, PROD-7 and PROD-404")[3]
            many = combined("cb-many", "plan " + ", ".join("PROD-%d" % n for n in range(6, 17)),
                            upto=17)
            return ("Left out: PROD-404: I can't find PROD-404 in Linear." in asked,
                    len(many[4]), "PROD-16: one message can name at most 10." in many[3])
        check("cb-a-ticket-that-fails-a-check-and-each-past-ten-are-left-out",
              outcome(case_left_out), (True, 10, True))

        # 2b. Review of #291. One ticket of two passes: it is asked on its own, the other
        #     named, and the message is handled once, so a second pass posts nothing more.
        #     A ticket named twice is asked about once; the collected tickets count against
        #     the open-question cap, so one message never opens past it.
        def case_one_of_two():
            c, s, t = dmworld("cb-one-of-two", act=True)
            s.add(ALICE, "plan PROD-6 and PROD-404", channel=DM)
            run(c, s, t, at=now + 60)
            first = list(dm_lines(s))
            run(c, s, t, at=now + 120)
            return (first[-2:], dm_lines(s) == first,
                    sorted((q["ticket"], q["status"]) for q in questions(c).values()))
        got = outcome(case_one_of_two)
        check("cb-one-of-two-is-asked-alone-the-other-named-and-the-message-read-once",
              (got[0][0].startswith("*Plan this?* PROD-6"), got[0][1], got[1], got[2])
              if isinstance(got, tuple) else got,
              (True, "Left out: PROD-404: I can't find PROD-404 in Linear.", True,
               [("PROD-6", OPEN)]))

        def case_cap_and_twice():
            twice = combined("cb-twice", "plan PROD-6 and PROD-6")
            capped = combined("cb-capped", "plan PROD-6, PROD-7 and PROD-8", upto=9,
                              max_open_questions=2)
            return ([q["ticket"] for q in twice[4].values()], len(capped[4]),
                    capped[3][capped[3].find("Left out"):].split("\n")[0])
        check("cb-a-ticket-named-twice-is-asked-once-and-the-cap-holds-across-one-message",
              outcome(case_cap_and_twice),
              (["PROD-6"], 2, "Left out: PROD-8: 2 questions are already open, which is my "
                              "limit. Answer one, or let it expire, then ask again."))

        # 3. A re-plan of two: one question, and one yes re-plans each, each its own record.
        def case_replan():
            c, s, t = dmworld("cb-replan", act=True)
            tickets(t, 8)
            for ident in ("PROD-6", "PROD-7"):
                t.in_plan_it(ident, trigger="T-" + ident)
                t.add_run(ident, "PROD-5" + ident[-1], "completed", trigger="T-" + ident)
            s.add(ALICE, "replan PROD-6 and PROD-7", channel=DM)
            run(c, s, t, at=now + 60)
            asked = dm_lines(s)[-1]
            s.add(ALICE, "yes", channel=DM)
            run(c, s, t, at=now + 120)
            return (asked.startswith("*Plan these again?*"),
                    "I'll hand each one back to the planner for a new plan" in asked,
                    sorted(q["status"] for q in questions(c).values()))
        check("cb-a-replan-of-two-is-one-question-and-each-is-replanned",
              outcome(case_replan), (True, True, [DONE, DONE]))

        # Review of #291: a pass cut between the two re-plans. The first's moves stand and
        # are never made again; the yes is read again on the next pass, and only the second
        # re-plan, its own record, goes on.
        def case_replan_cut():
            c, s, t = dmworld("cb-replan-cut", act=True)
            tickets(t, 8)
            for ident in ("PROD-6", "PROD-7"):
                t.in_plan_it(ident, trigger="T-" + ident)
                t.add_run(ident, "PROD-5" + ident[-1], "completed", trigger="T-" + ident)
            s.add(ALICE, "replan PROD-6 and PROD-7", channel=DM)
            run(c, s, t, at=now + 60)
            s.add(ALICE, "yes", channel=DM)
            t.move_raises = 3                    # the second re-plan's first move is refused
            cut = run(c, s, t, at=now + 120)[0]
            after_cut = (list(t.moves), sorted((q["ticket"], q["status"])
                                               for q in questions(c).values()))
            t.move_raises = None
            run(c, s, t, at=now + 180)
            return (cut, after_cut, t.moves,
                    sorted((q["ticket"], q["status"]) for q in questions(c).values()))
        got = outcome(case_replan_cut)
        check("cb-a-replan-of-two-cut-mid-way-goes-on-for-the-second-alone",
              got, (1, ([("uuid-6", "st-backlog"), ("uuid-6", "st-plan-it")],
                        [("PROD-6", DONE), ("PROD-7", OPEN)]),
                    [("uuid-6", "st-backlog"), ("uuid-6", "st-plan-it"),
                     ("uuid-7", "st-backlog"), ("uuid-7", "st-plan-it")],
                    [("PROD-6", DONE), ("PROD-7", DONE)]) if not isinstance(got, str)
              else got)

        # 4. Never combined: a ticket the tech lead named first, and each approve.
        def case_apart():
            c, s, t = asked_tl("cb-tl", "anything worth planning?")
            tickets(t, 8)
            tl_says(s, tl_root(c), "PROD-7 is the dark theme one.")
            run(c, s, t, at=now + 60)
            s.add(ALICE, "plan PROD-6 and PROD-7", channel=DM)
            run(c, s, t, at=now + 120)
            picked = sorted((q["ticket"], q.get("group"), q.get("named_yes"))
                            for q in questions(c).values())
            c, s, t = dmworld("cb-approve", act=True, agent_user_name="Agent")
            t.add_epic("PROD-50", (("PROD-51", ()),))
            t.add_epic("PROD-60", (("PROD-61", ()),))
            run(c, s, t)
            s.add(ALICE, "approve PROD-50 and PROD-60", channel=DM)
            run(c, s, t, at=now + 60)
            return picked, sorted((q["ticket"], q.get("group")) for q in questions(c).values())
        check("cb-a-tech-lead-pick-and-each-approve-keep-their-own-question",
              outcome(case_apart),
              ([("PROD-6", None, None), ("PROD-7", None, "PROD-7")],
               [("PROD-50", None), ("PROD-60", None)]))


def selftest():
    import pipeline_bridge
    return pipeline_bridge.split_module_selftest(os.path.abspath(__file__),
                                                 [cases_requests, cases_reader, cases_combined])


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        # As the module the bridge imports, never a copy of it named __main__, so its
        # cases and the pass share one copy of it (KIT-312).
        import pipeline_bridge_requests
        sys.exit(pipeline_bridge_requests.selftest())
    print("usage: pipeline_bridge_requests.py --selftest (the bridge imports this module)")
    sys.exit(EXIT_USAGE)
