#!/usr/bin/env python3
"""The Slack bridge's plans: `approve <epic id>`, its children's start, and its end.

    python3 scripts/pipeline_bridge_plans.py --selftest

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
  Approve (KIT-223)  a yes moves an epic the idea gate filed to its team's ready state;
                 then every pass hands its children to the agent user, as the owner, in
                 dependency order, at most `epic_wip` at once.
  A plan's page (KIT-274)  in a member's DM gets the approve question on the bridge's own,
                 once, naming the open questions the planner left, read from the ledger.
  The queue (KIT-454)  with `queue_config` set, the queue says what starts, a plan's pieces
                 included, inside the working window (scripts/pipeline_queue_start.py): the
                 day's ledger row first, then the same one hand-over.
  A stalled start  a hand-over not started two hours on, as its sessions show it (KIT-284),
                 offered again only once Linear has stopped retrying it (KIT-289).
  Finish (KIT-299)  once every piece is merged: the epic to its done state, one comment,
                 one wrap-up, and its review notes filed as one ticket after one more yes.
"""
import hashlib
import json
import os
import re
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pipeline_notify_local as notify  # noqa: E402
import pipeline_plan_poller as poller  # noqa: E402
# The queue (MVP 2, KIT-454): what may start now, and the ledger row before each start.
import pipeline_queue_start as starter  # noqa: E402
import pipeline_voice as voice  # noqa: E402
# The telemetry reader's own parse of a comment's `pipeline-telemetry/1` blocks: a review's
# notes are read the way the dashboard reads them (KIT-299).
import telemetry_scrape as scrape  # noqa: E402
from pipeline_bridge_transport import BridgeError  # noqa: E402
from pipeline_bridge_config import (ASK_AGAIN_BIND, DONE, EXIT_USAGE, EXPIRED,  # noqa: E402
                                    OPEN, REFUSED, SLACK_USER_RE, _TICKET_WORD_RE,
                                    credit_terms, epoch, iso, key_of, label_names,
                                    ticket_number, ts_float)

# KIT-474: the queue's room hold, as a small fields-only record the status job reads, so the
# status file and the stand-up can say it. Written by the queue's pass, in the bridge's own state
# directory, beside its heartbeat.
ROOM_SCHEMA = "pipeline-queue-room/1"
ROOM_FILE = "queue-room.json"

# The bind sentences of a plan's two questions (KIT-342): what a yes does and how long the
# question stays open; the approval says that merging stays the member's, and the follow-up,
# which starts no work, says so. Pinned word for word by the bridge's selftest. "Done" is
# what a piece waits on: a canceled one never lets the next start (blocker_done).
APPROVE_BIND = ("Reply %s %s within %d hours, and %s. Reply `no` to leave it. Merging stays "
                "yours. %s")
APPROVE_THEN_BIND = ("I'll mark the plan ready and hand its pieces to the agent, at most %d "
                     "at a time, each once the pieces it waits on are done")
APPROVE_OFF_THEN_BIND = ("I'll only note your answer: acting is switched off here, so nothing "
                         "moves or starts")
FOLLOWUP_BIND = ("Say `yes` %s within %d hours and I'll file %s as one follow-up ticket in "
                 "%s. It starts no work. Say `no` to leave %s.")
# KIT-274: who the approve question credits when the bridge asks it on its own, right after a
# plan's page lands in a DM; and what the question says of the plan's open questions, read
# from the notifier's count. Nothing is said of them when the count is unknown.
READY_PLAN_CREDIT_BIND = "(Asked because its plan was just filed.)"
APPROVE_NO_QUESTIONS = "It came with no open questions."
APPROVE_ONE_QUESTION = ("It came with one open question from the planner, in its planning "
                        "ticket's notes.")
APPROVE_QUESTIONS = ("It came with %d open questions from the planner, in its planning "
                     "ticket's notes.")
# How long a page the bridge asked about, or passed over, is remembered: past the four weeks
# the notifier keeps any record of it, so no page is ever asked about twice.
READY_PLANS_KEEP_SECONDS = 35 * 24 * 3600
# KIT-334: `answer <plan id>: <words>`. Its question quotes the member's words as they will
# be written, says where they go, and a yes adds them to the end of the plan's description.
# The binds are pinned word for word with the others; the refusals point at what works.
PLAN_ANSWER = "plan-answer"
ANSWER_BIND = "Reply %s %s within %d hours, and %s. Reply `no` to leave the plan as it is. %s"
ANSWER_THEN_BIND = "I'll add your words to its description"
ANSWER_OFF_THEN_BIND = ("I'll only note your answer: acting is switched off here, so nothing "
                        "is added")
ANSWER_SHAPE_BIND = "Send it as `answer %s: <your answer>`."
ANSWER_STARTED_BIND = "Use `replan %s`, or file a new idea."
PLAN_ANSWER_AGAIN_BIND = "Send `answer %s: <your answer>` again."
ANSWER_REASK_CREDIT_BIND = "(Asked again because your answer was added.)"
# A runaway's limit, well above any answer a person types (owner's rule, KIT-357).
ANSWER_MAX_CHARS = 4000
# Slack's own markup in a message: a mention, a special mention or a channel, each taken out
# whole, and a link, kept as its plain address unless it is a member's profile.
_SLACK_MARKUP_RE = re.compile(r"<([^<>\n]*)>")
_SLACK_PROFILE_RE = re.compile(r"^https?://[^/|]*slack\.com/team/", re.I)
# KIT-351: a plan this bridge runs, paused, resumed or cancelled from Slack, each asked as
# its own question and acted on only after a verified yes. A yes moves the plan alone,
# never a piece, so a piece already at work keeps going, and the cancel says so: Linear
# closes a parent's open pieces only when it is marked done. A pause or a cancel starts no
# work, and says that in place of the merge.
PLAN_MOVES = ("pause", "resume", "cancel")
PLAN_MOVE_BIND = "Reply %s %s within %d hours, and %s. Reply `no` to leave it as it is. %s"
PAUSE_THEN_BIND = ("I'll move the plan back to the backlog and start none of its pieces "
                   "until you resume it. A piece already at work keeps going")
RESUME_THEN_BIND = ("I'll mark the plan ready again and go on handing its pieces to the "
                    "agent, at most %d at a time, each once the pieces it waits on are done. "
                    "Merging stays yours")
CANCEL_THEN_BIND = ("I'll cancel the plan and never hand the agent another of its pieces. A "
                    "piece already at work keeps going: I cancel only the plan, never a "
                    "piece. You can't undo this here")
PLAN_PIECE_BIND = "To %s the plan, type `%s %s`."
RESUME_HINT_BIND = "To go on with it, type `resume %s`."
# KIT-444: a cancel also reaches an idea being planned, and a plan nobody approved here. An
# idea leaves planning for the backlog: no new run starts, and the planner drops a re-run it
# owes it; but a run already going is not stopped and still files its plan, since the
# planner reads the idea's state only before a run (pipeline_plan_poller.py).
STOP_PLANNING_THEN_BIND = ("I'll move the idea out of planning to the backlog, so no new "
                           "planning run starts and the planner drops any re-run it owes it. "
                           "A planning run already going keeps going, and still files its "
                           "plan")
PLAN_LATER_BIND = "When you want it planned, type `plan %s`."
FINISHED = ("completed", "canceled")
GATE_EPIC_LABEL, GATE_CHILD_LABEL = "provenance:agent", "provenance:epic"
MAX_CHILDREN = 30
# Labels that switch the dispatcher into a mode where it hands out, or stacks, children
# itself (0.2.73: the orchestrator prompts self-delegate sub-issues; a graphite child is cut
# from its blocker's branch). Matched without regard to case.
MODE_LABELS = ("orchestrator", "graphite")
# How long a child handed over may sit unstarted before the bridge says why, as its agent
# sessions show it (KIT-284). The dispatcher moves a ticket to a started state when its
# session begins.
STUCK_START_SECONDS = 2 * 3600
# KIT-289: how long Linear may still deliver a hand-over nobody picked up: its retries at 1
# minute, 1 hour and 6 hours, and a margin. A second hand-over before then could start two
# devs on one piece, so until then the note says when it is safe instead. The catch-up job
# waits exactly as long before it sends a lost one again (DEFAULT_AGE_FLOOR_SECONDS in
# pipeline_catchup_poller.py); the selftest checks the two stay equal.
HANDOVER_RETRY_SECONDS = 27000
# A pass reads sessions after setting its clock, so one up to this far past it is just made.
JUST_MADE_SECONDS = 600
# What a session's status says of a child two hours on. A hand-over always makes a session,
# and what the tracker calls one nobody answered is not proven (docs/CATCH-UP.md): the
# catch-up poller models it as pending, and it may turn stale. So a session is running only
# once the agent has picked it up, and has ended only with a status the agent's own response
# or error sets.
SESSION_RUNNING = ("active", "awaitingInput")
SESSION_RAN = ("complete", "error")
# A prompt the agent has not picked up. A finished child whose every open session is one
# holds its slot as before, but is never said to be worked on (KIT-290).
SESSION_UNPICKED = ("pending",)
# On a child in a hand-off lane, the label that says its review ran out of rounds with
# findings still open (contract §1). Matched without regard to case.
NEEDS_HUMAN_LABEL = "agent:needs-human"
# KIT-299: closing a finished plan. How often a plan whose close is held reads its pieces
# again (each piece is a read on the owner's key, shared with Stage E); how many links one
# piece's read holds (a piece with more holds the close: the one past them may be open);
# the kind of the one question asked after a plan closes, and where its yes files, which
# the question records as a stated term; and how many lines the wrap-up lists before it
# says how many more.
FINISH_RECHECK_SECONDS = 900
# How often a held epic's history is read again (review of #281): a move the owner made
# between two passes still counts, and a held epic pays one history read in that time.
HELD_RECHECK_SECONDS = 900
PIECE_LINKS = 50
FOLLOWUP = "followup"
FOLLOWUP_INTO = "the team's default state"
WRAP_LINES = 15
# A review is the owner's own comment that starts so, holding a `pipeline-telemetry/1`
# block: what the review poller posts on the original ticket (telemetry_block.py). One
# that saw only part of a change says so in its head, never in its block (KIT-138), with
# the number of files it never read.
REVIEW_HEAD = "**Pipeline review**"
PARTIAL_RE = re.compile(r"\*\*PARTIAL REVIEW(?: — (\d+) file)?")
# Only the GitHub INTEGRATION's own attachments name a piece's pull request: a session
# holds the tracker's write tools and can attach any link to its own ticket. A copy of the
# review poller's list and pull-request pattern, compared the same way (punctuation and case
# stripped). That file exits when its publisher is missing, so it is not imported here; the
# selftest pins the copies equal to it.
GITHUB_ATTACHMENT_SOURCES = ("github", "githubpullrequest", "githubpr", "githubissue",
                             "githubcommit", "githubbranch")
PR_URL_RE = re.compile(
    r"https?://(?:www\.)?github\.com/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)/pull/(\d+)\b")


def blockers_of(child):
    """THE ORDER CHECK, shared with the order job (B9): (ticket, its state) for each ticket
    that BLOCKS this one. The idea gate links a child to what it waits on with a `blocks`
    relation from the blocker, so the blockers are the child's inverse `blocks` relations."""
    out = []
    for rel in ((child.get("inverseRelations") or {}).get("nodes")) or []:
        issue = (rel or {}).get("issue") or {}
        if rel.get("type") == "blocks" and issue.get("id"):
            out.append((issue, issue.get("state") or {}))
    return out


def blocker_done(state):
    return (state or {}).get("type") == "completed"


def open_blockers(child):
    """What this child still waits on, as `ID (State)`; [] when it may start. A child whose
    blockers could not all be read never may."""
    if (((child.get("inverseRelations") or {}).get("pageInfo")) or {}).get("hasNextPage"):
        return ["more blockers than the bridge reads"]
    return ["%s (%s)" % (b.get("identifier") or "?", st.get("name") or "?")
            for b, st in blockers_of(child) if not blocker_done(st)]


def in_order(children):
    """The children in dependency order: each after the siblings that block it, ties by
    ticket number. A loop in the plan leaves the rest in ticket order."""
    ids = set(k["id"] for k in children)
    waits = dict((k["id"], set(b["id"] for b, _st in blockers_of(k) if b["id"] in ids))
                 for k in children)
    left = sorted(children, key=lambda k: ticket_number(k.get("identifier")))
    placed, out = set(), []
    while left:
        ready = [k for k in left if waits[k["id"]] <= placed]
        if not ready:
            out.extend(left)
            break
        out.append(ready[0])
        placed.add(ready[0]["id"])
        left.remove(ready[0])
    return out


def children_of(epic):
    return ((epic.get("children") or {}).get("nodes")) or []


def fingerprint(child):
    """What the owner approved of a child: its title, text, labels, project and what it
    waits on. A session holds the tracker's write tools, so a child is handed over only
    while this still matches: text is the work, labels and project are its route, and the
    blockers are its place in the order."""
    text = json.dumps([child.get("title") or "", child.get("description") or "",
                       sorted(label_names(child) - {None}),
                       (child.get("project") or {}).get("id") or "",
                       sorted(b.get("id") or "" for b, _st in blockers_of(child))])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def fingerprints(epic):
    return dict((k["id"], fingerprint(k)) for k in children_of(epic))


def changes_since(shown, epic):
    """How the epic's children differ from the ones shown, in words; "" when they don't."""
    now = fingerprints(epic)
    names = dict((k["id"], k.get("identifier") or "?") for k in children_of(epic))
    added = sorted(names[i] for i in now if i not in shown)
    changed = sorted(names[i] for i in now if i in shown and now[i] != shown[i])
    removed = len([i for i in shown if i not in now])
    parts = []
    if added:
        parts.append("added: %s" % ", ".join(added))
    if changed:
        parts.append("changed: %s" % ", ".join(changed))
    if removed:
        parts.append("%d removed" % removed)
    return "; ".join(parts)


def epic_problems(epic, kids=None, owner=None):
    """Why this is not an epic the idea gate filed, or one whose children may not start;
    [] when it may be approved. `kids` narrows the child checks to the approved ones. With
    `owner`, the epic and each piece must have been filed with the owner's key: a label,
    which any account can add, never makes them the idea gate's on its own (KIT-341). The
    idea gate files every piece with the key it files the epic with."""
    problems = []
    if owner and (epic.get("creator") or {}).get("id") != owner:
        problems.append("it was not filed with your key, so the idea gate did not file it")
    if GATE_EPIC_LABEL not in label_names(epic):
        problems.append("it does not carry `%s`, so the idea gate did not file it"
                        % GATE_EPIC_LABEL)
    if (epic.get("parent") or {}).get("id"):
        problems.append("it has a parent ticket")
    kids = children_of(epic) if kids is None else kids
    if not kids:
        problems.append("it has no pieces")
    if ((epic.get("children") or {}).get("pageInfo") or {}).get("hasNextPage"):
        problems.append("it has more than %d pieces" % MAX_CHILDREN)
    strays = [k.get("identifier") or "?" for k in kids if GATE_CHILD_LABEL not in label_names(k)]
    if strays:
        problems.append("%s lack%s `%s`, so the idea gate did not file %s"
                        % (", ".join(strays), "s" if len(strays) == 1 else "",
                           GATE_CHILD_LABEL, "it" if len(strays) == 1 else "them"))
    foreign = [k.get("identifier") or "?" for k in kids
               if owner and (k.get("creator") or {}).get("id") != owner]
    if foreign:
        # A label set when a ticket is made may name nobody in its history, so a piece the
        # agent's account made with its label on is caught here (review of #281).
        problems.append("%s %s not filed with your key, so the idea gate did not file %s"
                        % (", ".join(foreign), "was" if len(foreign) == 1 else "were",
                           "it" if len(foreign) == 1 else "them"))
    nested = [k.get("identifier") or "?" for k in kids
              if ((k.get("children") or {}).get("nodes"))]
    if nested:
        problems.append("%s ha%s pieces of its own" % (", ".join(nested),
                                                        "s" if len(nested) == 1 else "ve"))
    team = (epic.get("team") or {}).get("key")
    elsewhere = [k.get("identifier") or "?" for k in kids
                 if ((k.get("team") or {}).get("key")) != team]
    if elsewhere:
        problems.append("%s is on another team" % ", ".join(elsewhere))
    for node in [epic] + list(kids):
        modes = sorted(n for n in label_names(node) if (n or "").lower() in MODE_LABELS)
        if modes:
            problems.append("%s carries `%s`, which makes the agent hand out or stack "
                            "pieces itself" % (node.get("identifier") or "?",
                                               "`, `".join(modes)))
    if (epic.get("state") or {}).get("type") in FINISHED:
        problems.append("it is %s" % ((epic.get("state") or {}).get("name") or "closed"))
    return problems


def answer_words(raw):
    """(words, why): a member's answer as it may go into a plan's description (KIT-334), or
    ("", why) when it may not. Slack's mentions, special mentions and channel names come out
    whole, and so does a link to a member's profile; any other link keeps its plain address.
    Then the text is decoded and loses every angle bracket, to a fixed point, and the
    planner's own cleaning neutralizes each directive, fence token and mark in it, as it does
    for an idea's text. A credential shape is refused and never echoed."""
    def markup(m):
        inner = m.group(1)
        if inner[:1] in "@!#":
            return ""
        url = inner.split("|", 1)[0]
        return "" if _SLACK_PROFILE_RE.match(url) else url
    if notify.secret_hits(raw or ""):
        return "", "it looks like it holds a key or a password"
    text = poller.sanitize_text(notify.decode_then_strip(_SLACK_MARKUP_RE.sub(markup, raw or "")))
    text = "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in text.strip().splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if notify.secret_hits(text):
        return "", "it looks like it holds a key or a password"
    if not text:
        return "", "nothing is left once its mentions and links are taken out"
    if len(text) > ANSWER_MAX_CHARS:
        return "", "it is longer than %d characters" % ANSWER_MAX_CHARS
    return text, ""


def quoted(words):
    return "\n".join(("> " + line) if line else ">" for line in words.split("\n"))


def answer_block(name, uid, when, words):
    """What a yes adds to the end of a plan's description (KIT-334): who answered, when, and
    their words, quoted under a rule, so they read as theirs and not the planner's."""
    return ("\n\n---\n\n**Answer from %s (Slack user %s), %s, through the Slack bridge:**\n\n%s"
            % (name, uid, when, quoted(words)))


def plan_fingerprint(epic):
    """The plan as the answer question showed it, but for its description: its own id,
    title, team, labels and state, then each piece. The description is read and compared
    apart, since the epic's read does not carry it."""
    text = json.dumps([epic.get("id") or "", epic.get("title") or "",
                       (epic.get("team") or {}).get("key") or "",
                       sorted(label_names(epic) - {None}),
                       (epic.get("state") or {}).get("id") or "", fingerprints(epic)])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def text_fingerprint(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def answer_parts(epic, words, questions, requester, cfg, terms=None):
    """(head, body, bind, facts): the question an answer line gets (KIT-334), in the writer's
    pieces, as approve_parts. It names the plan, how many open questions came with it, and
    where the words go, and quotes them as they will be written. It is posted word for word,
    never by the writer, since a yes writes exactly what it quotes."""
    who = ("<@%s>" % requester) if SLACK_USER_RE.match(requester or "") else "a bot"
    title = notify.safe_title(epic.get("title") or "")
    team = (epic.get("team") or {}).get("key") or "?"
    count = ("I can't tell how many open questions it came with" if questions is None else
             "it came with no open questions" if questions == 0 else
             "it came with one open question" if questions == 1 else
             "it came with %d open questions" % questions)
    body = ("%s “%s” on %s: %s. Your words go at the end of its description, dated and "
            "naming you:\n%s" % (epic.get("identifier"), title, team, count, quoted(words)))
    then = ANSWER_THEN_BIND if cfg["act"] else ANSWER_OFF_THEN_BIND
    yes, credit = credit_terms(terms, who)
    bind = ANSWER_BIND % (yes, "here", cfg["question_ttl_hours"], then, credit)
    facts = {"ticket": epic.get("identifier"), "title": title, "team": team,
             "open_questions": questions, "needs": "his answer to the fixed line after this "
                                                  "message"}
    return "*Add this answer to the plan?* ", body, bind, facts


def approve_text(epic, requester, cfg, by_bot=False, dm=False):
    head, body, bind, _facts = approve_parts(epic, requester, cfg, by_bot, dm)
    return head + body + "\n" + bind


def approve_parts(epic, requester, cfg, by_bot=False, dm=False, terms=None, questions=None):
    """(head, body, bind, facts): an approve question in the writer's pieces, as
    question_parts (KIT-297). The bind states what a yes does, and that merging stays his.
    `terms` (ACT-1) is (credit, the ticket a yes must name, or None). `questions` (KIT-274)
    is how many open questions came with the plan, or None when it is unknown."""
    kids = in_order(children_of(epic))
    listing = ", ".join("%s “%s”" % (k.get("identifier"), notify.safe_title(k.get("title")
                                                                          or ""))
                        for k in kids[:12])
    if len(kids) > 12:
        listing += ", and %d more" % (len(kids) - 12)
    if by_bot:
        # The tech lead only when it is the chat bot the config names: any other bot is "a bot".
        who = "the tech lead" if requester == cfg.get("chat_bot_user_id") else "a bot"
    else:
        who = ("<@%s>" % requester) if SLACK_USER_RE.match(requester or "") else "a bot"
    then = (APPROVE_THEN_BIND % cfg["epic_wip"] if cfg["act"] else APPROVE_OFF_THEN_BIND)
    title = notify.safe_title(epic.get("title") or "")
    team = (epic.get("team") or {}).get("key") or "?"
    body = ("%s “%s” on %s: %d piece%s, in this order: %s."
            % (epic.get("identifier"), title, team, len(kids), "" if len(kids) == 1 else "s",
               listing))
    if questions is not None:
        body += " " + (APPROVE_NO_QUESTIONS if questions == 0 else APPROVE_ONE_QUESTION
                       if questions == 1 else APPROVE_QUESTIONS % questions)
    yes, credit = credit_terms(terms, who)
    bind = APPROVE_BIND % (yes, "here" if dm else "in this thread", cfg["question_ttl_hours"],
                           then, credit)
    facts = {"ticket": epic.get("identifier"), "title": title, "team": team,
             "children": len(kids), "order": [k.get("identifier") for k in kids[:12]],
             "needs": "his answer to the fixed line after this message"}
    if questions is not None:
        facts["open_questions"] = questions
    return "*Approve this plan?* ", body, bind, facts


def at_work_text(ids):
    """The plan's pieces at work, said as what a pause or a cancel leaves going (KIT-351)."""
    if not ids:
        return "None of its pieces is at work now."
    return "%s %s at work now, and %s going." % (", ".join(ids), "is" if len(ids) == 1
                                                  else "are", "keeps" if len(ids) == 1
                                                  else "keep")


def plan_move_parts(kind, epic, at_work, requester, cfg, by_bot=False, dm=False, terms=None):
    """(head, body, bind, facts): a plan's pause, resume or cancel question in the writer's
    pieces, as approve_parts (KIT-351). The body names the plan and its pieces at work; the
    bind states what a yes does, and a cancel's that a piece at work keeps going."""
    if by_bot:
        # The tech lead only when it is the chat bot the config names: any other bot is "a bot".
        who = "the tech lead" if requester == cfg.get("chat_bot_user_id") else "a bot"
    else:
        who = ("<@%s>" % requester) if SLACK_USER_RE.match(requester or "") else "a bot"
    if not cfg["act"]:
        then = APPROVE_OFF_THEN_BIND
    elif kind == "resume":
        then = RESUME_THEN_BIND % cfg["epic_wip"]
    else:
        then = PAUSE_THEN_BIND if kind == "pause" else CANCEL_THEN_BIND
    title = notify.safe_title(epic.get("title") or "")
    team = (epic.get("team") or {}).get("key") or "?"
    body = "%s “%s” on %s. %s" % (epic.get("identifier"), title, team, at_work_text(at_work))
    yes, credit = credit_terms(terms, who)
    bind = PLAN_MOVE_BIND % (yes, "here" if dm else "in this thread", cfg["question_ttl_hours"],
                             then, credit)
    facts = {"ticket": epic.get("identifier"), "title": title, "team": team,
             "at_work": list(at_work), "needs": "his answer to the fixed line after this message"}
    return "*%s this plan?* " % kind.capitalize(), body, bind, facts


def planning_text(ids, now="is"):
    """The idea's planning runs that have not ended, said as what a stop leaves going: each
    still files its plan when it ends (KIT-444). `now` is "is" when asking, "was" after."""
    if not ids:
        return "No planning run %s going." % now
    return "%s %s planning it now, and %s going: %s still filed when it ends." % (
        ", ".join(ids), "is" if len(ids) == 1 else "are", "keeps" if len(ids) == 1 else "keep",
        "its plan is" if len(ids) == 1 else "each one's plan is")


def plan_stop_parts(idea, going, requester, cfg, by_bot=False, dm=False, terms=None):
    """(head, body, bind, facts): `cancel` on an idea being planned, which asks to stop
    planning it (KIT-444), in the writer's pieces as plan_move_parts. The bind says that a
    planning run already going is not stopped."""
    if by_bot:
        # The tech lead only when it is the chat bot the config names: any other bot is "a bot".
        who = "the tech lead" if requester == cfg.get("chat_bot_user_id") else "a bot"
    else:
        who = ("<@%s>" % requester) if SLACK_USER_RE.match(requester or "") else "a bot"
    then = STOP_PLANNING_THEN_BIND if cfg["act"] else APPROVE_OFF_THEN_BIND
    title = notify.safe_title(idea.get("title") or "")
    team = (idea.get("team") or {}).get("key") or "?"
    body = "%s “%s” on %s. %s" % (idea.get("identifier"), title, team, planning_text(going))
    yes, credit = credit_terms(terms, who)
    bind = PLAN_MOVE_BIND % (yes, "here" if dm else "in this thread", cfg["question_ttl_hours"],
                             then, credit)
    facts = {"ticket": idea.get("identifier"), "title": title, "team": team,
             "planning": list(going), "needs": "his answer to the fixed line after this message"}
    return "*Stop planning this idea?* ", body, bind, facts


# -- A finished plan (KIT-299) ----------------------------------------------------------

def _source_key(value):
    """An attachment's source type, compared as the review poller compares it."""
    return re.sub(r"[^a-z0-9]", "", (value if isinstance(value, str) else "").lower())


def piece_prs(piece):
    """The pull requests the GitHub INTEGRATION linked to a piece, each as its repo,
    number, link and status: lower case, or None when the link carries no status. A link
    of any other source is a value a session could have written, so it is passed over, as
    the review poller passes it over."""
    out = []
    for att in (((piece or {}).get("attachments") or {}).get("nodes")) or []:
        att = att if isinstance(att, dict) else {}
        m = PR_URL_RE.search(att.get("url") if isinstance(att.get("url"), str) else "")
        if not m or _source_key(att.get("sourceType")) not in GITHUB_ATTACHMENT_SOURCES:
            continue
        repo, number = "%s/%s" % (m.group(1), m.group(2)), int(m.group(3))
        if any(p["repo"] == repo and p["number"] == number for p in out):
            continue
        meta = att.get("metadata") if isinstance(att.get("metadata"), dict) else {}
        status = meta.get("status")
        out.append({"repo": repo, "number": number, "url": m.group(0),
                    "status": (status.strip().lower() if isinstance(status, str)
                               and status.strip() else None)})
    return out


def piece_merge(prs):
    """How a finished piece's pull requests stand: (why it holds its plan open, or None;
    the merged ones; the ones the tracker names no status for). One still open holds it,
    and so does one closed unmerged, unless one in the same repository was merged: that
    one replaced it. A link with no status beside it replaces nothing."""
    merged = [p for p in prs if p["status"] == "merged"]
    unknown = [p for p in prs if p["status"] is None]
    still_open = [p for p in prs if p["status"] not in (None, "merged", "closed")]
    closed = [p for p in prs if p["status"] == "closed"
              and not any(m["repo"] == p["repo"] for m in merged)]
    if still_open:
        return ("#%d is %s, not merged" % (still_open[0]["number"],
                                           notify.safe_title(still_open[0]["status"])),
                merged, unknown)
    if closed:
        return "#%d was closed without being merged" % closed[0]["number"], merged, unknown
    return None, merged, unknown


def piece_hold(piece):
    """Why a finished piece holds its plan open, or None: a pull request not merged, or
    more links than one read holds, since one past them may be open."""
    if piece.get("links_over"):
        return ("%s has more than %d links, and I read only those, so I can't tell its pull "
                "requests are all merged" % (piece["ident"], PIECE_LINKS))
    why = piece_merge(piece["prs"])[0]
    return "%s's pull request %s" % (piece["ident"], why) if why else None


def piece_review(piece, author_id):
    """What a piece's newest review left: (its notes, or None when there are none to read;
    how many files it never read when it says it was partial, else None; True when it
    could not be read). A review is a comment by `author_id`, the owner's key the review
    poller posts with, whose body starts with REVIEW_HEAD. A comment by anyone else is
    never one: a session posts as the dispatcher's app, and could write a look-alike. The
    NEWEST review is the one read: when its `pipeline-telemetry/1` block does not parse, or
    its findings are not a list, the piece has no notes, never an older review's, which
    after a bounce are about code since fixed. A review with no findings left no notes; a
    partial one left none only on the files it read."""
    best = None
    for c in (((piece or {}).get("comments") or {}).get("nodes")) or []:
        c = c if isinstance(c, dict) else {}
        user = c.get("user") if isinstance(c.get("user"), dict) else {}
        body = c.get("body") if isinstance(c.get("body"), str) else ""
        if not author_id or user.get("id") != author_id \
                or not body.lstrip().startswith(REVIEW_HEAD):
            continue
        at = c.get("createdAt") if isinstance(c.get("createdAt"), str) else ""
        if best is None or at > best[0]:
            best = (at, body)
    if best is None:
        return None, None, False
    part = PARTIAL_RE.search(best[1].split("```", 1)[0])
    partial = (int(part.group(1)) if part.group(1) else 0) if part else None
    blocks, _malformed = scrape.telemetry_blocks(best[1])
    found = blocks[0].get("review_findings", []) if blocks else None
    if not isinstance(found, list):
        return None, partial, True
    notes = []
    for f in found:
        if not isinstance(f, dict) or not str(f.get("summary") or "").strip():
            continue
        line, pr = f.get("line"), f.get("pr_number")
        notes.append({"severity": str(f.get("severity") or "unrated").strip(),
                      "summary": str(f.get("summary")).strip(),
                      "file": str(f.get("file") or "").strip(),
                      "line": line if isinstance(line, int) and not isinstance(line, bool)
                      else None,
                      "pr": pr if isinstance(pr, int) and not isinstance(pr, bool) else None})
    return notes, partial, False


def notes_of(pieces):
    """Every note left on a plan's pieces, in plan order, each naming its piece, and its
    pull request: the one the review names, or else the piece's first."""
    out = []
    for p in pieces:
        first = (p["prs"][0]["number"] if p["prs"] else None)
        for n in p["review"] or []:
            out.append(dict(n, piece=p["ident"], pr=n["pr"] or first))
    return out


def notes_fingerprint(notes):
    """What a follow-up question showed: the exact notes. A yes files them only while this
    still matches."""
    text = json.dumps([[n["piece"], n["severity"], n["file"], n["line"], n["pr"],
                        n["summary"]] for n in notes])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _plain(text, cap):
    """Relayed text for the tracker: no angle bracket, one line, cut to `cap`."""
    out = re.sub(r"\s+", " ", re.sub(r"[<>]", " ", str(text or ""))).strip()
    return out if len(out) <= cap else out[:cap - 1] + "…"


def _relayed(text, cap):
    """Relayed text for a ticket the dispatcher may read once someone hands it over: the
    relay's own cleaner first, so a routing tag (`repo=`, `model=`, `agent=`) a session or a
    review wrote reaches it inert, as every other copied body does; then plain."""
    return _plain(notify.clean_relay_text(str(text or "")), cap)


def _partial(files):
    """What a partial review is said as: how many files it never read, when it names it."""
    return "its review was partial" + (": %d file%s never read" % (
        files, " was" if files == 1 else "s were") if files else "")


def _where(note):
    if not note["file"]:
        return "no file named"
    return note["file"] + (":%d" % note["line"] if note["line"] is not None else "")


def _numbers(prs):
    return ", ".join("#%d" % p["number"] for p in prs)


def piece_lines(pieces):
    """One line for each piece with something worth saying: no pull request linked, a
    merge the tracker does not confirm, no review found, a newest review that could not be
    read, or a partial one."""
    lines = []
    for p in pieces:
        _why, merged, unknown = piece_merge(p["prs"])
        said = []
        if not p["prs"]:
            said.append("no pull request linked")
        elif not merged and unknown:
            said.append("merge not confirmed by Linear (%s)" % _numbers(unknown))
        if p["review"] is None and p.get("unreadable"):
            said.append("its newest review could not be read")
        elif p["review"] is None:
            said.append("no review found" + (" in the 100 comments I read" if p["more"]
                                             else ""))
        if p.get("partial") is not None:
            said.append(_partial(p["partial"]))
        if said:
            lines.append("• %s: %s." % (p["ident"], "; ".join(said)))
    return lines


def wrap_up_text(epic, pieces, notes, done_name):
    """The one message a finished plan gets: what shipped, a line for each piece worth one,
    and the review notes left, at most WRAP_LINES lines and how many more."""
    text, notes_part, _about, _facts = wrap_up_parts(epic, pieces, notes, done_name)
    return text + ("\n" + notes_part if notes_part else "")


def wrap_up_parts(epic, pieces, notes, done_name):
    """(text, notes, about, facts): the wrap-up in the writer's pieces (KIT-297). The writer
    writes what shipped and the line for each piece, from `about`: the text, and the ids and
    links only the notes hold, since its words must keep each one. The notes, from the note
    list's first line on, follow verbatim: a yes to the follow-up files exactly the notes the
    member read."""
    title = notify.safe_title(epic.get("title") or "")
    merged = [m for p in pieces for m in piece_merge(p["prs"])[1]]
    every = bool(pieces) and all(piece_merge(p["prs"])[1] for p in pieces)
    shipped = (" — %s" % _numbers(merged)) if merged else ""
    head = "%s of %s “%s” %s finished%s%s. I've marked the plan %s." % (
        "The one piece" if len(pieces) == 1 else "All %d pieces" % len(pieces),
        epic.get("identifier"), title, "is" if len(pieces) == 1 else "are",
        " and merged" if every else "", shipped, notify.safe_title(done_name))
    lines = piece_lines(pieces)
    first_note = len(lines) if notes else None
    if notes:
        lines.append("%d review note%s left:" % (len(notes), " is" if len(notes) == 1
                                                 else "s are"))
        lines += ["• %s, %s, %s: %s" % (n["piece"], notify.safe_title(n["severity"])[:20],
                                        notify.safe_title(_where(n))[:80],
                                        notify.safe_title(n["summary"])) for n in notes]
    elif any(p["review"] is not None for p in pieces):
        # A partial review's "no notes" covers only the files it read (KIT-138).
        lines.append("Its reviews left no notes%s." % (
            " on the files they read" if any(p.get("partial") is not None for p in pieces)
            else ""))
    if len(lines) > WRAP_LINES:
        lines = lines[:WRAP_LINES] + ["…and %d more." % (len(lines) - WRAP_LINES)]
    cut = len(lines) if first_note is None else min(first_note, len(lines))
    facts = {"plan": epic.get("identifier"), "title": title, "done_state": done_name,
             "pieces": len(pieces), "all_merged": every, "notes": len(notes)}
    text, notes_part = "\n".join([head] + lines[:cut]), "\n".join(lines[cut:])
    # As whole words, as the writer keeps them: PROD-512 in the text does not name PROD-51.
    named = ([w for w in sorted(set(_TICKET_WORD_RE.findall(notes_part)))
              if not voice._whole(w, text)]
             + [u for u in sorted(voice.fact_links([notes_part])) if not voice._whole(u, text)])
    about = text + (" The notes below name %s." % ", ".join(named) if named else "")
    return text, notes_part, about, facts


def closing_comment(rec, pieces, done_name, now):
    """The comment on a plan the bridge closes: who approved it, when it finished, and each
    piece with its pull requests. Names and titles are relayed text."""
    lines = ["Marked %s by the Slack bridge at %s: every piece of this plan is finished. "
             "**%s** (Slack user %s) approved it in Slack at %s."
             % (done_name, iso(now), _relayed(rec.get("approved_name"), 120) or "?",
                rec.get("approved_by") or "?", rec.get("approved_at") or "?"), ""]
    for p in pieces:
        _why, merged, unknown = piece_merge(p["prs"])
        if merged:
            how = "%s, merged" % ", ".join(m["url"] for m in merged)
        elif unknown:
            how = "%s, merge not confirmed by the tracker" % ", ".join(m["url"] for m in unknown)
        else:
            how = "no pull request linked"
        lines.append("- %s “%s”: %s" % (p["ident"], _relayed(p["title"], 120), how))
    return "\n".join(lines)


def followup_text(ticket, count, team, cfg, dm=False):
    """The one question a closed plan with notes left asks. Its terms are fixed: what a yes
    files, where, and that it starts nothing."""
    head, _about, bind, _facts = followup_parts(ticket, count, team, cfg, dm)
    return head + " " + bind


def followup_parts(ticket, count, team, cfg, dm=False):
    """(head, about, bind, facts): the follow-up question in the writer's pieces, as
    question_parts (KIT-297). The head asks, and the writer is given `about` only; the bind
    states what a yes files, where, and that it starts nothing."""
    head = "*File the review notes from %s?*" % ticket
    # Never "listed in my wrap-up": past WRAP_LINES the wrap-up may show none of them.
    about = ("The reviews of %s's pieces left %s."
             % (ticket, "one note" if count == 1 else "%d notes" % count))
    bind = (FOLLOWUP_BIND
            % ("here" if dm else "in this thread", cfg["question_ttl_hours"],
               "this note" if count == 1 else "these %d notes" % count, team,
               "it" if count == 1 else "them"))
    facts = {"ticket": ticket, "notes": count, "team": team,
             "needs": "his answer to the fixed line after this message"}
    return head, about, bind, facts


def followup_title(epic):
    """The follow-up's title. The plan's title is relayed text: a session can edit it."""
    return _relayed("Follow-ups from %s" % (epic.get("title") or epic.get("identifier")
                                            or "a plan"), 120)


def followup_description(epic, notes, name, uid, when, pieces=()):
    """The follow-up's text: where the notes came from, who said yes, each note with its
    piece and pull request, and each piece whose review was partial. Every relayed field
    (the plan's title, the member's name, each note's parts) goes through the relay's
    cleaner: this ticket is filed as the owner, and the dispatcher reads routing tags out
    of a description once someone hands it over."""
    lines = ["Review notes left on the pieces of %s “%s” when every piece was finished. Filed "
             "by the Slack bridge for **%s** (Slack user %s), who said yes in Slack at %s. "
             "Nothing starts on this ticket until someone hands it over."
             % (epic.get("identifier") or "?", _relayed(epic.get("title"), 120),
                _relayed(name, 120) or uid, uid, when), ""]
    for n in notes[:100]:
        lines.append("- %s%s, %s, %s: %s" % (
            n["piece"], " (#%d)" % n["pr"] if n["pr"] else "", _relayed(n["severity"], 20),
            _relayed(_where(n), 200), _relayed(n["summary"], 500)))
    if len(notes) > 100:
        lines.append("- …and %d more, in each piece's review comment." % (len(notes) - 100))
    partial = [p for p in pieces if p.get("partial") is not None]
    if partial:
        lines += [""] + ["- %s: %s, so those files have no notes here."
                         % (p["ident"], _partial(p["partial"])) for p in partial]
    return "\n".join(lines)


class PlansMixin(object):
    """Pass's approved plans, from the request to the end (KIT-312)."""

    def request_approve(self, channel, msg, thread, requester, ident, by_bot=False, dm=False,
                        terms=None, set_up=None):
        if not self.cfg["agent_user_name"].strip():
            self.refuse_request(channel, msg, thread, "I name no agent user "
                                "(agent_user_name), so I can't start a plan's pieces. "
                                "Nothing was asked.", "no agent user", ticket=ident)
            return
        epic = self.tracker.epic(ident)
        if not epic or not epic.get("identifier"):
            self.refuse_request(channel, msg, thread, "I can't find %s in Linear."
                                % ident, "not found", ticket=ident)
            return
        team = (epic.get("team") or {}).get("key")
        if team not in self.cfg["team_keys"]:
            self.refuse_request(channel, msg, thread, "%s is on %s, and I act only on %s."
                                % (epic["identifier"], team or "no team",
                                   ", ".join(self.cfg["team_keys"])),
                                "team not planned", ticket=epic["identifier"])
            return
        if epic.get("id") in self.state["epics"]:
            self.refuse_request(channel, msg, thread, "%s is already approved, and I'm "
                                "starting its pieces. Nothing was asked."
                                % epic["identifier"], "already approved",
                                ticket=epic["identifier"])
            return
        problems = self.approve_problems(epic)
        if problems:
            self.refuse_request(channel, msg, thread, "I won't ask about approving %s: %s."
                                % (epic["identifier"], "; ".join(problems)),
                                "not an idea-gate epic", ticket=epic["identifier"])
            return
        self.ask(channel, msg, thread, "approve", epic, requester,
                 approve_parts(epic, requester, self.cfg, by_bot=by_bot, dm=dm, terms=terms),
                 children=fingerprints(epic), terms=terms, set_up=set_up)

    def approve_problems(self, epic):
        """Why an epic may not be asked about or approved: the trust checks request_approve
        refuses on, shared with the question the bridge asks on its own after a plan's page
        (KIT-274) and with act_approve's re-check before it moves anything, so a check added
        here holds on all three. Among them are KIT-341's: the owner's key filed it, and no
        label on it came from the agent's own account."""
        problems = (epic_problems(epic, owner=self.cfg["owner_user_id"])
                    + self.label_problems(epic, children_of(epic)))
        if self.handed_over(epic):
            problems.append("it is handed to the agent itself, so its pieces would stack on "
                            "its branch")
        return problems

    def ask_ready_plans(self):
        """KIT-274: the approve question, asked on the bridge's own right after the
        notifier's plan-ready page lands in a member's DM, naming how many open questions
        came with the plan. Once per page. The plan and the count are the ones the notifier
        read from the executor's line and kept in its DM ledger, never the page's text.
        A page the ledger held when this step first ran, in a DM read for the first time,
        or older than a question lives, is passed over, as a baseline is. So is a plan
        request_approve would refuse, and the pass log says why: `approve <id>` still
        works, and says the reason in the DM. Recorded before it asks, so no page is ever
        asked about twice: a post that fails ends the pass loud instead."""
        first = "ready_plans" not in self.state
        seen = self.state.setdefault("ready_plans", {})
        if not isinstance(seen, dict):
            raise BridgeError("the bridge's state has no readable record of the plans it "
                              "asked about. Nothing more was done")
        for key in [k for k, v in seen.items() if not isinstance(v, dict) or self.now - (
                epoch(v.get("at")) or 0) > READY_PLANS_KEEP_SECONDS]:
            del seen[key]
        users = dict((ch, uid) for uid, ch in self.state["dms"].items())
        pages = sorted((r for r in self.dm_posts.values() if isinstance(r, dict)
                        and r.get("mark") == notify.ESC_AWAITING_APPROVAL
                        and isinstance(r.get("event_key"), str)),
                       key=lambda r: (str(r.get("recorded_at")), str(r.get("ts"))))
        for rec in pages:
            key, channel = rec["event_key"], rec.get("channel")
            if key in seen:
                continue
            detail = rec.get("detail") if isinstance(rec.get("detail"), dict) else {}
            plan, questions = detail.get("plan"), detail.get("questions")
            recorded = epoch(rec.get("recorded_at"))
            why = None
            if first or channel in self.fresh_dms:
                why = "already there when this step first read it"
            elif channel not in self.dm_channels or channel not in users:
                why = "not a DM this pass reads"
            elif not (isinstance(plan, str) and notify.TICKET_ID_RE.match(plan)) or not (
                    questions is None or (type(questions) is int and 0 <= questions <= 9)):
                why = "the notifier read no plan line from it"
            elif recorded is None or self.now - recorded > self.cfg["question_ttl_hours"] * 3600:
                why = "older than a question lives"
            elif not self.cfg["agent_user_name"].strip():
                why = "no agent user is named (agent_user_name)"
            epic = None
            if why is None:
                epic = self.tracker.epic(plan)
                team = ((epic or {}).get("team") or {}).get("key")
                if not epic or epic.get("identifier") != plan:
                    why = "%s is not in Linear" % plan
                elif team not in self.cfg["team_keys"]:
                    why = "%s is on %s, not a team this acts on" % (plan, team or "no team")
                elif epic.get("id") in self.state["epics"]:
                    why = "%s is already approved" % plan
                elif any(q.get("status") == OPEN and q.get("ticket") == plan
                         for q in self.state["questions"].values()):
                    why = "%s already has an open question" % plan
                else:
                    problems = self.approve_problems(epic)
                    if problems:
                        why = "%s: %s" % (plan, "; ".join(problems))
            seen[key] = {"at": iso(self.now), "what": self.clean(
                "asking: approve %s" % plan if why is None else "not asked: %s" % why)[:160]}
            self.save()
            if why is not None:
                if not first:
                    self.say("  plan page %s: not asked: %s" % (key, why))
                continue
            requester, terms = users[channel], (READY_PLAN_CREDIT_BIND, None)
            # The page's own record stands for the message the question answers to: it is the
            # notifier's post, which a pass reads as this app's own.
            self.ask(channel, {"ts": rec.get("ts"), "user": requester}, None, "approve", epic,
                     requester, approve_parts(epic, requester, self.cfg, dm=True, terms=terms,
                                              questions=questions),
                     children=fingerprints(epic), terms=terms,
                     # The tech lead's wake reads it: a plan waiting on his answer (KIT-356).
                     extra={"ready_plan": True})
            seen[key]["what"] = "asked: approve %s" % plan

    def plan_open_questions(self, ident):
        """How many open questions came with the plan, as the notifier read them from the
        executor's line for its page (KIT-274), or None when no page of it says."""
        recs = [r for r in (self.dm_posts or {}).values() if isinstance(r, dict)
                and r.get("mark") == notify.ESC_AWAITING_APPROVAL
                and isinstance(r.get("detail"), dict) and r["detail"].get("plan") == ident]
        if not recs:
            return None
        newest = max(recs, key=lambda r: (str(r.get("recorded_at")), str(r.get("ts"))))
        n = newest["detail"].get("questions")
        return n if type(n) is int and 0 <= n <= 9 else None

    def answer_refusal(self, epic):
        """(text, bind, why) or None: why a plan takes no answer now (KIT-334). Only a plan
        the idea gate filed, by request_approve's trust checks, takes one, and only while it
        is not approved and no piece of it is under way."""
        ident = epic["identifier"]
        problems = self.approve_problems(epic)
        if problems:
            return ("I can only add an answer to a plan the planner filed, or pass one to a "
                    "dev waiting on you, and %s is neither: %s." % (ident, "; ".join(problems)),
                    "", "not a plan")
        if epic.get("id") in self.state["epics"]:
            return ("%s is already approved, so its pieces start soon, and an answer added now "
                    "would not reach them." % ident, ANSWER_STARTED_BIND % ident, "approved")
        started = [k.get("identifier") or "?" for k in children_of(epic) if self.handed_over(k)]
        if started:
            return ("Work on %s has started (%s), so an answer added now would not reach it."
                    % (ident, ", ".join(started)), ANSWER_STARTED_BIND % ident, "under way")
        return None

    def request_answer(self, channel, msg, thread, requester, ident, words, terms=None):
        """KIT-334: an answer line about a plan, after request()'s limits. The plan must pass
        answer_refusal, and the words answer_words; then a question of the bridge's own
        quotes them as they will be written. An open approve question on the plan is closed
        once this one is asked, so a bare yes answers this one: it is asked again once the
        words are in, or after a no."""
        epic = self.tracker.epic(ident)
        if not epic or not epic.get("identifier"):
            self.refuse_request(channel, msg, thread, "I can't find %s in Linear." % ident,
                                "not found", ticket=ident)
            return
        ident = epic["identifier"]
        team = (epic.get("team") or {}).get("key")
        if team not in self.cfg["team_keys"]:
            self.refuse_request(channel, msg, thread, "%s is on %s, and I act only on %s."
                                % (ident, team or "no team", ", ".join(self.cfg["team_keys"])),
                                "team not planned", ticket=ident)
            return
        refusal = self.answer_refusal(epic)
        if refusal:
            text, bind, why = refusal
            self.refuse_request(channel, msg, thread, text, why, bind=bind, ticket=ident)
            return
        cleaned, why = answer_words(words)
        if why:
            # Never the words themselves: a key is refused, not repeated.
            self.refuse_request(channel, msg, thread, "I didn't take that answer for %s: %s."
                                % (ident, why), "answer refused", ticket=ident)
            return
        issue = self.tracker.issue(ident)
        if not issue:
            self.refuse_request(channel, msg, thread, "I can't find %s in Linear." % ident,
                                "not found", ticket=ident)
            return
        self.ask(channel, msg, thread, PLAN_ANSWER, epic, requester,
                 answer_parts(epic, cleaned, self.plan_open_questions(ident), requester,
                              self.cfg, terms=terms),
                 children=fingerprints(epic), terms=terms,
                 extra={"words": cleaned, "plan": plan_fingerprint(epic),
                        "description": text_fingerprint(issue.get("description"))})
        asked = [q for q in self.state["questions"].values() if q.get("kind") == PLAN_ANSWER
                 and q.get("ticket") == ident and q.get("status") == OPEN]
        if asked:
            asked[-1]["reask"] = self.close_approve_questions(
                ident, "your answer to its open questions comes first, and I'll ask again "
                "after it")

    def close_approve_questions(self, ident, why):
        """Close every open approve question on the plan, each said where it was asked, as
        an answer line or a held plan needs (KIT-334). True when one was closed."""
        closed = False
        for q in self.state["questions"].values():
            if q.get("kind") == "approve" and q.get("ticket") == ident and q.get("status") == OPEN:
                self.post(q["channel"], q.get("thread"), "I've closed my question about "
                          "approving %s: %s." % (ident, why), kind="question-closed",
                          facts={"ticket": ident})
                q.update(status=EXPIRED, closed_by=why)
                closed = True
        return closed

    def act_plan_answer(self, channel, thread, q, msg):
        """KIT-334: a yes to an answer question adds its words to the end of the plan's
        description, as the owner: the bridge's fifth tracker write. The plan is read again
        first, and nothing is written when it changed, was approved or has a piece under way
        since the question. The write is recorded before it is made, so a pass that stops
        after it never writes twice; settle_answer reads the plan again after it."""
        def finish(status, text, what, kind="answer-refused", bind=""):
            # Posted first: the status changes only after the reply saying so lands.
            self.post(channel, thread, text, kind=kind, bind=bind, join=" ",
                      facts={"ticket": q["ticket"]})
            q.update(status=status, answered_by=msg["user"], answered_ts=msg.get("ts"))
            self.mark(channel, msg, what)
            self.counts["refused"] += 1

        ident = q["ticket"]
        # The name first, and the description last, right before the write: no other
        # read sits between them, so the window for an edit to be lost is the write's
        # own (Stage E's review of #305). The tracker has no compare-and-set.
        name = self.name_of(msg["user"])
        epic = self.tracker.epic(ident)
        if not epic:
            finish(REFUSED, "I can't find %s in Linear any more, so nothing was added." % ident,
                   "refused: not found")
            return
        changed = ("%s changed since I asked, so nothing was added, and I have closed the "
                   "question." % ident)
        if plan_fingerprint(epic) != q.get("plan"):
            finish(REFUSED, changed, "refused: changed since asked",
                   bind=PLAN_ANSWER_AGAIN_BIND % ident)
            return
        refusal = self.answer_refusal(epic)
        if refusal:
            text, bind, why = refusal
            finish(REFUSED, text, "refused: " + why, bind=bind)
            return
        if self.refused_over_limit(channel, thread, msg):
            return
        if self.dry:
            self.say("  [dry-run] would add an answer to %s's description" % ident)
            return
        issue = self.tracker.issue(ident)
        if not issue or text_fingerprint(issue.get("description")) != q.get("description"):
            finish(REFUSED, changed, "refused: changed since asked",
                   bind=PLAN_ANSWER_AGAIN_BIND % ident)
            return
        new = ((issue.get("description") or "").rstrip()
               + answer_block(name, msg["user"], iso(ts_float(msg.get("ts"))), q["words"]))
        q["writing"] = {"expect": text_fingerprint(new), "by": msg["user"],
                        "said_at": msg.get("ts")}
        self.save()
        self.tracker.describe(epic["id"], new)
        self.state["acted_at"].append(self.now)
        self.settle_answer(channel, thread, q, msg)

    def settle_answer(self, channel, thread, q, msg):
        """An answer's write read back (KIT-334), right after it or on a later pass when its
        pass stopped there. The description it wrote, with nothing else changed beside it,
        is said done, and an approve question the answer closed is asked again, so a yes
        covers the plan as it now reads. Anything else holds the plan: its approve questions
        close, and the member is told to read it again before approving it."""
        ident, writing = q["ticket"], q["writing"]
        epic, issue = self.tracker.epic(ident), self.tracker.issue(ident)
        landed = bool(issue) and text_fingerprint(issue.get("description")) == writing["expect"]
        if landed and epic and plan_fingerprint(epic) == q.get("plan"):
            self.post(channel, thread, "Added your answer to the end of %s's description, "
                      "dated and naming you." % ident, kind="answer-added",
                      facts={"ticket": ident})
            q.update(status=DONE, answered_by=writing["by"], answered_ts=writing["said_at"])
            self.mark(channel, msg, "done: answer added to %s" % ident)
            return
        self.close_approve_questions(ident, "it changed while your answer was being added")
        self.post(channel, thread, ("%s changed while I was adding your answer, so I've held "
                                    "it: read it again before you approve it." if landed else
                                    "I couldn't confirm that your answer reached %s's "
                                    "description, so I've held it: read it again before you "
                                    "approve it.") % ident, kind="answer-held",
                  bind=ASK_AGAIN_BIND % ("approve", ident), join=" ", facts={"ticket": ident})
        q.update(status=DONE, held=True, answered_by=writing["by"],
                 answered_ts=writing["said_at"])
        self.mark(channel, msg, "held: %s" % ident)

    def reask_approve(self, channel, q, epic):
        """The approve question an answer closed, asked again in its place (KIT-334), with
        every check a typed approve gets, so a yes approves the plan as it now reads. A plan
        that no longer passes them is left for `approve <id>`, which says why."""
        if not epic or self.answer_refusal(epic) or not self.cfg["agent_user_name"].strip():
            self.say("  did not ask again about approving %s" % q["ticket"])
            return
        requester, terms = q.get("asked_for") or "", (ANSWER_REASK_CREDIT_BIND, None)
        # The answer question's own post stands for the message this one answers to.
        self.ask(channel, {"ts": q.get("ts"), "user": requester}, None, "approve", epic,
                 requester, approve_parts(epic, requester, self.cfg,
                                          dm=channel in self.dm_channels, terms=terms,
                                          questions=self.plan_open_questions(epic["identifier"])),
                 children=fingerprints(epic), terms=terms)

    def label_problems(self, epic, kids):
        """The labels on the plan that the agent's own account added, read from each
        ticket's history (KIT-341). Sessions and the tech lead write as that account, so a
        label it added never makes a plan the idea gate's. Without that account's id nothing
        can be checked, and the plan is refused."""
        agent_id, why = self.tracker.agent_user(self.cfg["agent_user_name"])
        if not agent_id:
            return ["I can't check who added its labels: %s" % why]
        found = set()
        for node in [epic] + list(kids):
            names = dict((n.get("id"), n.get("name"))
                         for n in (node.get("labels") or {}).get("nodes") or [])
            for entry in self.tracker.trust_history(node["id"]):
                if entry.get("actorId") == agent_id:
                    found.update("%s's `%s`" % (node.get("identifier") or "?", names[lid])
                                 for lid in entry.get("addedLabelIds") or [] if lid in names)
        if not found:
            return []
        return ["%s %s added by the agent's own account, not by the idea gate"
                % (", ".join(sorted(found)), "was" if len(found) == 1 else "were")]

    def moved_by_owner(self, epic, state, rec):
        """True when the newest move of the epic into the state it is in was made with the
        owner's key: his own, or the bridge's (KIT-341). A move by anyone else, or one the
        history does not show, is never his pause or stop. Kept by state, and read again
        at most every HELD_RECHECK_SECONDS: a held epic's history is read once in that time,
        not every pass, and a move he made between two passes still counts."""
        sid = state.get("id")
        seen = rec.get("moved_into") or {}
        if seen.get("state") != sid or self.now - seen.get("at", 0) >= HELD_RECHECK_SECONDS:
            moves = [n for n in self.tracker.trust_history(epic["id"])
                     if n.get("toStateId") == sid]
            newest = max(moves, key=lambda n: n.get("createdAt") or "") if moves else {}
            seen = rec["moved_into"] = {
                "state": sid, "at": self.now,
                "by_owner": newest.get("actorId") == self.cfg["owner_user_id"]}
        return seen["by_owner"]

    def handed_over(self, node):
        """True when the ticket is delegated to anyone, or has an agent session."""
        return bool((node.get("delegate") or {}).get("id")
                    or self.tracker.sessions_on(node["id"]))

    def live_session(self, node):
        """True while an agent session on the ticket has not ended, whatever its state says.
        The workspace's sessions are read once a pass, so this costs no extra read."""
        return any((n or {}).get("status") not in poller.SESSION_FINISHED
                   for n in self.tracker.sessions_on(node["id"]))

    def only_unpicked(self, node):
        """True when a session on the ticket has not ended and every such one is a prompt
        the agent never picked up (KIT-290): nothing works on it, though a late pick-up
        still could. A status the bridge does not know is not this."""
        statuses = [(n or {}).get("status") for n in self.tracker.sessions_on(node["id"])]
        open_ = [s for s in statuses if s not in poller.SESSION_FINISHED]
        return bool(open_) and all(s in SESSION_UNPICKED for s in open_)

    def act_approve(self, channel, thread, q, msg):
        """Move the epic to its team's ready state, as the owner, and record it as approved,
        so every pass from now on starts its children in order. Re-read first."""
        def finish(status, text, what, kind="approve-refused", bind=""):
            # Posted first: the status changes only after the reply saying so lands.
            self.post(channel, thread, text, kind=kind, bind=bind, join=" ",
                      facts={"ticket": q["ticket"]})
            q.update(status=status, answered_by=msg["user"], answered_ts=msg.get("ts"))
            self.mark(channel, msg, what)
        if self.refused_over_limit(channel, thread, msg):
            return
        epic = self.tracker.epic(q["ticket"])
        if not epic or not epic.get("id"):
            finish(REFUSED, "I can't find %s in Linear any more, so nothing was "
                   "approved." % q["ticket"], "refused: gone")
            return
        team = (epic.get("team") or {}).get("key")
        if team not in self.cfg["team_keys"]:
            finish(REFUSED, "%s is now on %s, which I don't act on, so nothing was "
                   "approved." % (q["ticket"], team or "no team"), "refused: team")
            return
        if epic["id"] in self.state["epics"]:
            finish(DONE, "%s is already approved." % q["ticket"], "done: already",
                   kind="approve-already")
            return
        changed = changes_since(q.get("children") or {}, epic)
        if changed:
            finish(REFUSED, "%s's pieces changed since I asked (%s), so nothing was "
                   "approved." % (q["ticket"], changed), "refused: changed since asked",
                   bind=ASK_AGAIN_BIND % ("approve", q["ticket"]))
            return
        problems = self.approve_problems(epic)
        ready_id, why = self.tracker.team_state(team, self.cfg["ready_state"])
        if not ready_id:
            problems.append(why)
        agent_id, why = self.tracker.agent_user(self.cfg["agent_user_name"])
        if not agent_id:
            problems.append(why)
        if problems:
            finish(REFUSED, "I can't approve %s now: %s." % (q["ticket"], "; ".join(problems)),
                   "refused: %s" % problems[0][:80])
            return
        if self.dry:
            self.say("  [dry-run] would approve %s" % q["ticket"])
            return
        name = self.name_of(msg["user"])
        when = iso(ts_float(msg.get("ts")))
        state = epic.get("state") or {}
        if state.get("id") != ready_id and state.get("type") in ("backlog", "triage",
                                                                 "unstarted"):
            self.tracker.move(epic["id"], ready_id)
            self.state["acted_at"].append(self.now)
        self.state["epics"][epic["id"]] = {
            "ticket": epic["identifier"], "team": team, "channel": channel,
            "thread": thread, "approved_by": msg["user"], "approved_name": name,
            "approved_at": when, "started": [], "noted": [],
            "approved": dict(q.get("children") or {}), "started_at": {}, "failures": 0}
        self.save()
        body = ("Approved in Slack by **%s** (Slack user %s) at %s, through the Slack bridge."
                "\n\nIts children are handed to the agent in dependency order, at most %d at "
                "once, each once the tickets it waits on are Done. Merging stays the owner's."
                % (name, msg["user"], when, self.cfg["epic_wip"]))
        owed = ""
        try:
            self.tracker.comment(epic["id"], body)
        except BridgeError as exc:
            # The approval stands. The record of who approved it is owed.
            self.say("  NOTE: %s was approved, but its attribution comment failed: %s"
                     % (q["ticket"], exc))
            self.owe(epic["id"], q["ticket"], body)
            owed = (" The approval was done, but the comment naming who approved it could not "
                    "be written; I will try again.")
        finish(DONE, "Approved %s for <@%s>. I'll hand its pieces to the agent in order, "
               "at most %d at a time, each once what it waits on is done, and say here as "
               "each one starts.%s" % (q["ticket"], msg["user"], self.cfg["epic_wip"], owed),
               "done: approved %s" % q["ticket"], kind="approve-done")
        self.counts["acted"] += 1
        self.say("  acted: approved %s for %s" % (q["ticket"], msg["user"]))

    # -- A plan paused, resumed or cancelled from Slack (KIT-351) ---------------------------
    def request_plan_move(self, channel, msg, thread, requester, ident, kind, by_bot=False,
                          dm=False, terms=None, set_up=None):
        epic = self.tracker.epic(ident)
        if not epic or not epic.get("identifier"):
            self.refuse_request(channel, msg, thread, "I can't find %s in Linear."
                                % ident, "not found", ticket=ident)
            return
        team = (epic.get("team") or {}).get("key")
        if team not in self.cfg["team_keys"]:
            self.refuse_request(channel, msg, thread, "%s is on %s, and I act only on %s."
                                % (epic["identifier"], team or "no team",
                                   ", ".join(self.cfg["team_keys"])),
                                "team not planned", ticket=epic["identifier"])
            return
        if kind == "cancel" and (epic.get("state") or {}).get("name") == self.cfg[
                "plan_it_state"]:
            # An idea being planned: the cancel stops planning it (KIT-444).
            self.ask(channel, msg, thread, kind, epic, requester,
                     plan_stop_parts(epic, self.planning_now(epic), requester, self.cfg,
                                     by_bot=by_bot, dm=dm, terms=terms),
                     extra={"plan_state": (epic.get("state") or {}).get("id"), "idea": True},
                     terms=terms, set_up=set_up)
            return
        why, bind = self.plan_move_refusal(kind, epic)
        if why:
            self.refuse_request(channel, msg, thread, why, "no plan to %s" % kind, bind=bind,
                                ticket=epic["identifier"])
            return
        if kind == "pause" and terms:
            # The one exception to ACT-1's named yes, decided in KIT-351's design (2 Nov): a
            # pause starts no work and a resume undoes it, so one the tech lead set up takes
            # a bare yes. A verb that starts work, or can't be undone, keeps the named yes.
            terms = (terms[0], None)
        self.ask(channel, msg, thread, kind, epic, requester,
                 plan_move_parts(kind, epic, self.at_work(epic), requester, self.cfg,
                                 by_bot=by_bot, dm=dm, terms=terms),
                 extra={"plan_state": (epic.get("state") or {}).get("id")}, terms=terms,
                 set_up=set_up)

    def plan_move_refusal(self, kind, epic):
        """(why, bind) when this is no plan of mine to pause, resume or cancel as it stands,
        or (None, ""): a plan is one I run, approved here and not let go of, or for a cancel,
        one the idea gate filed that nobody approved (KIT-444). A paused or held one is in a
        backlog or triage state, or in a cancelled one someone else moved it to; any other
        open state is one whose pieces go on."""
        ident = epic["identifier"]
        followed = epic.get("id") in self.state["epics"]
        if not followed:
            rec = self.state["epics"].get((epic.get("parent") or {}).get("id"))
            if rec:
                return ("%s is a piece of %s, the plan I'm running." % (ident, rec["ticket"]),
                        PLAN_PIECE_BIND % (kind, kind, rec["ticket"]))
            if kind == "cancel" and not self.gate_plan(epic):
                return ("%s isn't a plan the idea gate filed, or an idea being planned, so "
                        "there's nothing for me to cancel." % ident, "")
            if kind != "cancel":
                return ("I'm not running a plan for %s, so there's nothing for me to %s."
                        % (ident, kind), "")
        state = epic.get("state") or {}
        name = state.get("name") or "an unknown state"
        held = state.get("type") in ("backlog", "triage", "canceled")
        if state.get("type") == "completed":
            return "%s is in %s, so its plan is finished." % (ident, name), ""
        if not followed and state.get("type") == "canceled":
            return "%s is already in %s." % (ident, name), ""
        if kind == "pause" and held:
            return ("%s is already held: it's in %s, so none of its pieces start."
                    % (ident, name), RESUME_HINT_BIND % ident)
        if kind == "resume" and not held:
            return "%s isn't paused: it's in %s, and its pieces go on." % (ident, name), ""
        return None, ""

    def at_work(self, epic):
        """The plan's pieces at work, as the release counts them toward its limit: handed
        over or started and not finished, or finished while a session on it runs."""
        def type_of(k):
            return (k.get("state") or {}).get("type")
        return [k.get("identifier") or "?" for k in in_order(children_of(epic))
                if (type_of(k) not in FINISHED
                    and (self.handed_over(k) or type_of(k) == "started"))
                or (type_of(k) in FINISHED and self.live_session(k))]

    def canceled_state(self, team):
        """(id, None) for the team's first cancelled state by position, or (None, why), read
        from its board: where a plan cancelled from Slack goes."""
        board = self.tracker.team_board(team) or {}
        states = sorted((s for s in (board.get("states") or {}).get("nodes") or []
                         if s.get("type") == "canceled" and s.get("id")),
                        key=lambda s: s.get("position") or 0)
        if not states:
            return None, "%s has no cancelled state" % team
        return states[0]["id"], None

    def planning_now(self, idea):
        """The planner's runs on this idea that have not ended, as a re-plan reads them
        (KIT-444): only the planner's own tickets, filed with the owner's key."""
        team = (idea.get("team") or {}).get("key")
        runs = self.tracker.planning_runs(team, idea["identifier"], self.cfg["owner_user_id"])
        return sorted((r.get("identifier") or "?" for r in runs
                       if (r.get("state") or {}).get("type") not in FINISHED), key=ticket_number)

    def gate_plan(self, epic):
        """True for a plan the idea gate filed, approved here or not (KIT-444): filed with the
        owner's key and carrying its mark, which no agent's account added, and no piece of
        another ticket. A cancel reaches it, so its idea can be planned again."""
        return ((epic.get("creator") or {}).get("id") == self.cfg["owner_user_id"]
                and GATE_EPIC_LABEL in label_names(epic)
                and not (epic.get("parent") or {}).get("id")
                and not self.label_problems(epic, []))

    def move_plan(self, issue_id, target):
        """The one move a plan's pause, resume or cancel makes, a stop to planning, and each
        cancel a re-plan makes first (KIT-351, KIT-444): of that ticket alone, as the owner."""
        self.tracker.move(issue_id, target)
        self.state["acted_at"].append(self.now)

    def note_plan_move(self, issue_id, ticket, body):
        """The comment on a moved ticket naming who said yes. One the tracker won't take is
        owed and written again every pass; the move stands. The line a reply adds then, or ""."""
        try:
            self.tracker.comment(issue_id, body)
        except BridgeError as exc:
            self.say("  NOTE: %s was moved, but its attribution comment failed: %s"
                     % (ticket, exc))
            self.owe(issue_id, ticket, body)
            return (" The move was done, but the comment naming who said yes could not be "
                    "written; I will try again.")
        return ""

    def act_plan_move(self, channel, thread, q, msg):
        """Move the plan alone, as the owner, through the one move write: to the backlog to
        pause it, to its team's ready state to resume it, to a cancelled state to cancel it,
        which lets it go. Re-read first: a yes acts only while the plan is still mine and in
        the state the question found it in."""
        kind = q["kind"]

        def finish(status, text, what, done=False, bind=""):
            # Posted first: the status changes only after the reply saying so lands.
            self.post(channel, thread, text, kind="%s-%s" % (kind, "done" if done
                                                             else "refused"),
                      bind=bind, join=" ", facts={"ticket": q["ticket"]})
            q.update(status=status, answered_by=msg["user"], answered_ts=msg.get("ts"))
            self.mark(channel, msg, what)
        if self.refused_over_limit(channel, thread, msg):
            return
        epic = self.tracker.epic(q["ticket"])
        if not epic or not epic.get("id"):
            finish(REFUSED, "I can't find %s in Linear any more, so nothing was moved."
                   % q["ticket"], "refused: gone")
            return
        team = (epic.get("team") or {}).get("key")
        if team not in self.cfg["team_keys"]:
            finish(REFUSED, "%s is now on %s, which I don't act on, so nothing was moved."
                   % (q["ticket"], team or "no team"), "refused: team")
            return
        idea = q.get("idea")                # an idea being planned (KIT-444)
        why, bind = (None, "") if idea else self.plan_move_refusal(kind, epic)
        if why:
            finish(REFUSED, "%s Nothing was moved." % why, "refused: %s" % why[:80],
                   bind=bind)
            return
        state = epic.get("state") or {}
        if state.get("id") != q.get("plan_state"):
            finish(REFUSED, "%s moved since I asked, so nothing was moved, and I have closed "
                   "the question." % q["ticket"], "refused: moved since asked",
                   bind=ASK_AGAIN_BIND % (kind, q["ticket"]))
            return
        if kind == "pause" or idea:
            target, _name, why = self.tracker.backlog_state(team)
        elif kind == "resume":
            target, why = self.tracker.team_state(team, self.cfg["ready_state"])
        else:
            target, why = self.canceled_state(team)
        if not target:
            finish(REFUSED, "I can't %s %s: %s." % (kind, q["ticket"], why),
                   "refused: no state")
            return
        if self.dry:
            self.say("  [dry-run] would %s %s" % (kind, q["ticket"]))
            return
        # A plan nobody approved here, or an idea, has no record of mine (KIT-444).
        rec = self.state["epics"].get(epic["id"])
        working = self.planning_now(epic) if idea else self.at_work(epic)
        if target != state.get("id"):
            self.move_plan(epic["id"], target)
        here = rec is not None and (rec.get("channel"), rec.get("thread")) == (channel, thread)
        if kind == "pause":
            # The bridge's own move is the owner's: no history read needed to trust it, and
            # where it is asked, the reply below says it, so the release does not again.
            rec["moved_into"] = {"state": target, "at": self.now, "by_owner": True}
            if here:
                rec["noted"] = (rec["noted"] + ["paused"])[-200:]
        elif rec is not None and kind == "cancel" and here:
            self.state["epics"].pop(epic["id"], None)
        elif rec is not None and kind == "cancel":
            self.drop(epic["id"], rec, "%s was cancelled from Slack by <@%s>, so I've stopped "
                      "starting its pieces." % (q["ticket"], msg["user"]))
        self.save()
        name = self.name_of(msg["user"])
        body = ("%s in Slack by **%s** (Slack user %s) at %s, through the Slack bridge."
                % ("Moved out of planning" if idea else
                   {"pause": "Paused", "resume": "Resumed", "cancel": "Cancelled"}[kind], name,
                   msg["user"], iso(ts_float(msg.get("ts")))))
        if q.get("direct"):
            # ACT-4's direct ask: a pause acts on the member's own clear ask at once.
            body += " No question was asked: it was their own clear ask."
        if idea:
            body += "\n\nA planning run already going keeps going, and still files its plan."
        elif kind != "resume":
            body += "\n\nIts pieces were left as they are, so one already at work keeps going."
        owed = self.note_plan_move(epic["id"], q["ticket"], body)
        if idea:
            finish(DONE, "Stopped planning %s for <@%s>. No new planning run starts. %s%s"
                   % (q["ticket"], msg["user"], planning_text(working, now="was"), owed),
                   "done: stopped planning %s" % q["ticket"], done=True,
                   bind=PLAN_LATER_BIND % q["ticket"])
        elif kind == "pause":
            finish(DONE, "Paused %s for <@%s>. None of its pieces will start until you resume "
                   "it. %s%s" % (q["ticket"], msg["user"], at_work_text(working), owed),
                   "done: paused %s" % q["ticket"], done=True,
                   bind=RESUME_HINT_BIND % q["ticket"])
        elif kind == "resume":
            finish(DONE, "Resumed %s for <@%s>. I'll go on handing its pieces to the agent, at "
                   "most %d at a time.%s" % (q["ticket"], msg["user"], self.cfg["epic_wip"],
                                             owed),
                   "done: resumed %s" % q["ticket"], done=True)
        else:
            finish(DONE, "Cancelled %s for <@%s>. I'll start none of its pieces again. %s%s"
                   % (q["ticket"], msg["user"], at_work_text(working), owed),
                   "done: cancelled %s" % q["ticket"], done=True)
        self.counts["acted"] += 1
        self.say("  acted: %s %s for %s" % (kind, q["ticket"], msg["user"]))

    def note(self, rec, key, text):
        """Say it in the epic's thread once, however many passes find it. The kind is the
        note's key up to its first colon (KIT-297)."""
        if key in rec["noted"]:
            return
        self.post(rec["channel"], rec["thread"], text, kind="epic-" + key.split(":", 1)[0],
                  facts={"epic": rec["ticket"]})
        rec["noted"] = (rec["noted"] + [key])[-200:]

    def drop(self, epic_id, rec, text):
        self.post(rec["channel"], rec["thread"], text, kind="epic-stopped",
                  facts={"epic": rec["ticket"]})
        self.state["epics"].pop(epic_id, None)
        self.say("  stopped following %s" % rec["ticket"])

    def release(self):
        """Every pass: start the next children of each approved epic (KIT-223). With the queue
        on (KIT-454), each plan is still followed here, but the queue says what starts, a
        plan's pieces included, inside the working window."""
        queue = bool(self.cfg["queue_config"])
        if not self.state["epics"] and not queue:
            return
        if not self.cfg["act"]:
            self.say("  acting is off: %d approved epic(s) wait" % len(self.state["epics"]))
            return
        agent_id = why = None
        if self.state["epics"]:
            agent_id, why = self.tracker.agent_user(self.cfg["agent_user_name"])
        if queue:
            self.queue_pass(agent_id, why)
            self.save()
        for epic_id in sorted(self.state["epics"]):
            rec = self.state["epics"][epic_id]
            if not agent_id:
                self.note(rec, "agent:" + why, "I can't start %s's pieces: %s."
                          % (rec["ticket"], why))
                continue
            # ONE EPIC'S FAILURE IS ITS OWN. A read the tracker refuses must not stop the
            # other epics, or the expiry and pruning after them; the pass still ends loud.
            try:
                self.release_epic(epic_id, rec, agent_id)
                rec["failures"] = 0
            except BridgeError as exc:
                rec["failures"] = rec.get("failures", 0) + 1
                # Redacted BEFORE it is cut short: a cut credential would no longer match.
                err = self.clean(str(exc))[:160]
                self.release_errors.append("%s: %s" % (rec["ticket"], err))
                if rec["failures"] == 3:
                    self.note(rec, "unreadable", "I can't read %s (%s). I'll keep trying "
                              "each pass, and start nothing in it until I can."
                              % (rec["ticket"], err))
            self.save()

    def release_epic(self, epic_id, rec, agent_id):
        ticket = rec["ticket"]
        epic = self.tracker.epic(epic_id)
        if not epic or epic.get("id") != epic_id:
            self.drop(epic_id, rec, "%s is no longer in Linear, so I've stopped starting its "
                      "pieces." % ticket)
            return
        state = epic.get("state") or {}
        if state.get("type") == "completed" and self.closed_already(epic_id, rec, epic):
            return
        if state.get("type") == "canceled" and not self.moved_by_owner(epic, state, rec):
            # Only his own stop, or the bridge's, ends the plan (KIT-341). Anyone else's
            # cancel holds it, and says so: nothing more starts, and nothing is dropped.
            self.note(rec, "held:" + str(state.get("id")), "%s was moved to %s, but not by "
                      "you, so I've held its pieces rather than stop following it. Move it "
                      "back to %s and they go on, or cancel it yourself to stop them."
                      % (ticket, state.get("name") or "Canceled", self.cfg["ready_state"]))
            return
        if state.get("type") in FINISHED:
            self.drop(epic_id, rec, "%s is %s, so I've stopped starting its pieces."
                      % (ticket, state.get("name") or "closed"))
            return
        team = (epic.get("team") or {}).get("key")
        if team not in self.cfg["team_keys"]:
            self.drop(epic_id, rec, "%s is now on %s, which I don't act on, so I've stopped "
                      "starting its pieces." % (ticket, team or "no team"))
            return
        if self.handed_over(epic):
            self.drop(epic_id, rec, "%s itself was handed to the agent. Its pieces would "
                      "stack on its branch, so I've stopped starting them." % ticket)
            return
        if state.get("type") in ("backlog", "triage"):
            name = state.get("name") or "Backlog"
            if self.moved_by_owner(epic, state, rec):
                self.note(rec, "paused", "%s is back in %s, so I've paused starting its "
                          "pieces. Move it to %s and they go on."
                          % (ticket, name, self.cfg["ready_state"]))
            else:
                # Anyone else's move holds it too, and says it wasn't his (KIT-341).
                self.note(rec, "held:" + str(state.get("id")), "%s was moved to %s, but not "
                          "by you, so I've held its pieces. Move it back to %s and they go "
                          "on, or move it to %s yourself to pause them."
                          % (ticket, name, self.cfg["ready_state"], name))
            return
        rec["noted"] = [n for n in rec["noted"] if n != "paused" and not n.startswith("held:")]
        rec.pop("moved_into", None)
        approved = rec.get("approved") or {}
        kids = in_order([k for k in children_of(epic) if k["id"] in approved])
        problems = epic_problems(epic, kids, owner=self.cfg["owner_user_id"])
        if problems:
            self.note(rec, "problems:" + "; ".join(problems), "I've paused starting %s's "
                      "pieces: %s." % (ticket, "; ".join(problems)))
            return

        def live(k):
            return (k.get("state") or {}).get("type") not in FINISHED
        extras = [k for k in children_of(epic) if k["id"] not in approved and live(k)]
        running = [k for k in extras if self.handed_over(k)
                   or (k.get("state") or {}).get("type") == "started"]
        for k in running:
            self.note(rec, "atwork:" + k["id"], "%s is at work but was not in the plan you "
                      "approved. It counts toward the limit of %d."
                      % (k.get("identifier") or "?", self.cfg["epic_wip"]))
        fresh = [k for k in extras if k not in running and "added:" + k["id"] not in rec["noted"]]
        if fresh:
            self.note(rec, "added:" + fresh[0]["id"], "%s %s added after the approval, so I "
                      "won't hand %s over. Hand %s over yourself if %s yours."
                      % (", ".join(k.get("identifier") or "?" for k in fresh),
                         "was" if len(fresh) == 1 else "were",
                         "it" if len(fresh) == 1 else "them",
                         "it" if len(fresh) == 1 else "them",
                         "it is" if len(fresh) == 1 else "they are"))
            rec["noted"] += ["added:" + k["id"] for k in fresh[1:]]
        unfinished = [k for k in kids if live(k)]
        if not unfinished:
            self.finish_epic(epic_id, rec, epic, kids)
            return
        running += [k for k in unfinished if self.handed_over(k)
                    or (k.get("state") or {}).get("type") == "started"]
        # A session can move its own child to Done while it still runs: a finished child
        # holds its slot until its session ends, and the hold is said once. One whose open
        # sessions are all prompts the agent never picked up holds it too, said as that.
        for k in children_of(epic):
            if not live(k) and self.live_session(k):
                running.append(k)
                if self.only_unpicked(k):
                    self.note(rec, "unpicked:" + k["id"], "%s is marked finished, but the "
                              "agent hasn't picked up a prompt on it. It counts toward the "
                              "limit of %d until that prompt ends. I can't tell when that "
                              "will be: the agent may still pick it up."
                              % (k.get("identifier") or "?", self.cfg["epic_wip"]))
                    continue
                self.note(rec, "running:" + k["id"], "%s is marked finished, but its dev is "
                          "still working on it. It counts toward the limit of %d until the "
                          "dev stops." % (k.get("identifier") or "?", self.cfg["epic_wip"]))
        room = self.cfg["epic_wip"] - len(running)

        def next_waits_on(k):
            """True when an approved child can't start yet but will once k is done: it
            waits on k alone, or on nothing while k holds the last slot. With more running
            than the limit, k's end frees no slot. Nor does it while a session on k has not
            ended, one the agent never picked up included: a child marked finished holds its
            slot until then, by the rule above."""
            mine = k["identifier"] + " ("
            return room >= 0 and not self.live_session(k) and any(
                q not in running and q["identifier"] not in rec["started"]
                and fingerprint(q) == approved.get(q["id"])
                and all(b.startswith(mine) for b in open_blockers(q))
                and (room == 0 or open_blockers(q)) for q in unfinished)
        for k in running:
            since = (rec.get("started_at") or {}).get(k.get("identifier"))
            if (since and self.now - since > STUCK_START_SECONDS
                    and (k.get("state") or {}).get("type") in ("backlog", "triage",
                                                               "unstarted")):
                self.not_started(rec, k, since, next_waits_on)
        waiting, started, held = [], 0, 0
        for k in unfinished:
            if k in running:
                continue
            if k["identifier"] in rec["started"]:
                waiting.append("%s was started once already; hand it over yourself if it "
                               "needs another go" % k["identifier"])
                continue
            if fingerprint(k) != approved.get(k["id"]):
                self.note(rec, "changed:%s:%s" % (k["id"], fingerprint(k)), "%s changed "
                          "after the approval, so I won't hand it over. Hand it over "
                          "yourself if the change is yours." % k["identifier"])
                held += 1
                continue
            blockers = open_blockers(k)
            if blockers:
                waiting.append("%s waits on %s" % (k["identifier"], ", ".join(blockers)))
                continue
            if self.cfg["queue_config"]:
                continue                # the queue starts it, inside the window (KIT-454)
            if room <= 0:
                continue
            if self.actions_left() <= 0:
                self.say("  action limit: %s waits for the next hour" % k["identifier"])
                room = 0
                continue
            self.start_child(rec, k, kids, agent_id)
            room -= 1
            started += 1
        if not running and not started and waiting and \
                len(waiting) + held == len(unfinished):
            self.note(rec, "stall:" + "; ".join(waiting), "Nothing in %s can start: %s."
                      % (ticket, "; ".join(waiting)))

    def not_started(self, rec, k, since, next_waits_on):
        """A child handed over two hours ago and still not in a started state, said once as
        its agent sessions show it (KIT-284). While one runs, nothing is said; one the agent
        never picked up is not running. In a hand-off lane its pull request waits for you,
        so it is never called not started, nor offered to be handed over again; while it
        carries `agent:needs-human` it is never called ready to merge either, and that is
        said even after the merge note. Anywhere else, a child whose newest session ran and
        ended stopped short. Any other never began: its newest session not picked up, or
        none at all, unless the list was too long to read whole and holds none: then it is
        said as could not tell, never as no session (contract §13). One that never began is
        said first as not picked up yet, with when a second hand-over is safe, and offered
        to be handed over again only past that time (KIT-289). A newest session made
        after the bridge's hand-over (its own, a hand-over again, or a mention) starts the
        two hours, and the note names its time. A list the tracker would not give fails the
        epic's pass before anything is said."""
        ident, wip = k["identifier"], self.cfg["epic_wip"]
        lane = (k.get("state") or {}).get("name") or "?"
        sessions = [n or {} for n in self.tracker.sessions_on(k["id"])]
        statuses = [n.get("status") for n in sessions]
        if any(s in SESSION_RUNNING for s in statuses):
            return
        if lane in self.cfg["handoff_states"]:
            if NEEDS_HUMAN_LABEL in set((n or "").lower() for n in label_names(k)):
                self.note(rec, "needs-human:" + k["id"], "%s is marked as needing a person, "
                          "and its pull request is waiting on you. That usually means its "
                          "review ran out of rounds with findings still open. It's yours to "
                          "fix and merge, or cancel. It still counts toward the limit of %d."
                          % (ident, wip))
            else:
                self.note(rec, "handoff:" + k["id"], "%s's pull request is waiting for you "
                          "to merge.%s" % (ident, " The next piece starts once it's merged."
                                           if next_waits_on(k) else ""))
        elif self.newest_ended(sessions):
            self.note(rec, "ended:" + k["id"], "%s's dev stopped before it finished, and "
                      "it's in %s now. It still counts toward the limit of %d: hand it over "
                      "again yourself, or cancel it." % (ident, lane, wip))
        elif not statuses and not self.tracker.sessions_read_whole():
            self.note(rec, "unsure:" + k["id"], "%s was handed to the agent at %s and is "
                      "still in %s. I couldn't tell whether its dev is working: Linear holds "
                      "more of the agent's work than I can read at once. It still counts "
                      "toward the limit of %d. Check it in Linear before you hand it over "
                      "again." % (ident, iso(since), lane, wip))
        else:
            since = self.handed_at(sessions, since)
            if self.now - since <= STUCK_START_SECONDS:
                return
            safe = since + HANDOVER_RETRY_SECONDS
            if self.now < safe:
                # KIT-289: Linear may still deliver that hand-over, so a second one now could
                # start two devs on it. Said once, with the time it is safe; then once more
                # past it, as below, if it still has not started.
                self.note(rec, "retrying:" + k["id"], "%s was handed to the agent at %s and "
                          "hasn't been picked up yet. Linear may still deliver that hand-over "
                          "until %s, so a second one before then could start two devs on it. "
                          "It still counts toward the limit of %d. I'll say so again if it "
                          "hasn't started by then." % (ident, iso(since), iso(safe), wip))
                return
            self.note(rec, "stuck:" + k["id"], "%s was handed to the agent at %s and has "
                      "not started. Its dev may have stopped. It still counts toward "
                      "the limit of %d: hand it over again yourself, or cancel it."
                      % (ident, iso(since), wip))

    def handed_at(self, sessions, since):
        """When the two hours start for a child the bridge handed over at `since`, once its
        newest session has not ended: at `since`, or at that session's own `createdAt` when
        it was made later (the bridge's own session, a hand-over again, or a mention). A time
        not in the tracker's own form, UTC ending in Z, never moves the clock (one with no
        zone would be read as this machine's local time), and nor does one more than
        JUST_MADE_SECONDS past this pass. One less far past it was made while this pass ran,
        or by a clock a little ahead of this one, so it counts as just made and the pass waits."""
        text = (self.newest(sessions) or {}).get("createdAt") or ""
        if not text.endswith("Z"):
            return since
        try:
            at = datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return since
        return at if since < at <= self.now + JUST_MADE_SECONDS else since

    def hand_over(self, issue_id, ident, agent_id, body, record=None):
        """The one hand-over, the bridge's own and the queue's (KIT-454): the ticket to the
        agent, then the comment saying why. `record` notes the start before the save. The
        hand-over stands if the comment fails, since it cannot be taken back: the comment is
        owed, and the words returned say so."""
        self.delegate_to(issue_id, agent_id)
        if record:
            record()
        self.save()
        return self.attribute(issue_id, ident, body)

    def delegate_to(self, issue_id, agent_id, count=True):
        """The bridge's one hand-over write: the ticket to the agent, or to nobody (None), as a
        retry takes it off first (KIT-352). `count` adds it to the actions an hour."""
        self.tracker.delegate(issue_id, agent_id)
        if count:
            self.state["acted_at"].append(self.now)

    def attribute(self, issue_id, ident, body, done="started"):
        """The one comment saying who approved a hand-over. A comment that fails is owed, and
        the words returned say so; the hand-over stands, since it cannot be taken back."""
        try:
            self.tracker.comment(issue_id, body)
        except BridgeError as exc:
            self.say("  NOTE: %s was %s, but its attribution comment failed: %s"
                     % (ident, done, exc))
            self.owe(issue_id, ident, body)
            return (" The hand-over was done, but the comment naming who approved it could "
                    "not be written; I will try again.")
        return ""

    def start_child(self, rec, child, kids, agent_id):
        ident = child["identifier"]
        if self.dry:
            self.say("  [dry-run] would hand %s to the agent" % ident)
            return
        after = [b.get("identifier") or "?" for b, _st in blockers_of(child)]
        body = ("Started by the Slack bridge for **%s** (Slack user %s), who approved %s in "
                "Slack at %s." % (rec["approved_name"], rec["approved_by"], rec["ticket"],
                                  rec["approved_at"]))
        if after:
            body += "\n\nIt waited on %s, now Done." % ", ".join(after)

        def record():
            rec["started"].append(ident)
            rec.setdefault("started_at", {})[ident] = self.now
        owed = self.hand_over(child["id"], ident, agent_id, body, record)
        position = [k["id"] for k in kids].index(child["id"]) + 1
        title = notify.safe_title(child.get("title") or "")
        # While the daily stand-up is on, each start is in the afternoon wrap instead of a
        # message of its own (decision 7, KIT-349). The hand-over itself is unchanged.
        if not self.standup_on:
            self.post(rec["channel"], rec["thread"], "Started %s “%s” (%d of %d)%s.%s" % (
                ident, title, position, len(kids),
                ", now that %s %s Done" % (", ".join(after), "is" if len(after) == 1 else "are")
                if after else "", owed), kind="started-child",
                facts={"ticket": ident, "title": title, "epic": rec["ticket"],
                       "position": position, "of": len(kids), "waited_on": after})
        self.counts["acted"] += 1
        self.say("  acted: handed %s to the agent (%s)" % (ident, rec["ticket"]))

    # -- the queue (MVP 2, KIT-454) --------------------------------------------------
    def record_room(self, lines=None):
        """KIT-474: what the queue's pass found of the machine's room, written whole and
        atomically beside the heartbeat. `lines`, when given, is the room hold standing: each
        line the machine is over, as fields (line, reading, limit, unit; reading null when it
        could not be taken). `since` keeps the first pass of this hold; when the hold ends the
        record says `clear` and when, and that time stays until the next hold. Fields only: no
        word of the machine's or the tracker's own. A pass that cannot write it ends loud. A dry
        run writes nothing."""
        if self.dry:
            return
        stamp = iso(self.now)
        since = self.state.get("queue_room_since")
        if lines is not None:
            since = since or stamp
            self.state["queue_room_since"] = since
            doc = {"state": "held", "since": since, "resumed_at": None, "lines": list(lines)}
        else:
            if since:
                self.state.pop("queue_room_since", None)
                self.state["queue_room_resumed"] = stamp
            doc = {"state": "clear", "since": None,
                   "resumed_at": self.state.get("queue_room_resumed"), "lines": []}
        doc.update(schema=ROOM_SCHEMA, written_at=stamp)
        try:
            notify._atomic_write_json(os.path.join(self.cfg["state_dir"], ROOM_FILE), doc)
        except OSError as exc:
            self.release_errors.append("the queue's room record could not be written (%s)"
                                       % type(exc).__name__)

    def queue_pass(self, agent_id, why):
        """Ask the queue what may start now, at most every `queue_every_seconds`, and start
        it, in its order: the ledger row first (scripts/pipeline_queue_start.py), then the
        hand-over. A piece of a plan approved in Slack starts as the bridge's own do, named
        to its approver and told in its thread; any other ticket gets the queue's comment.
        A machine short of memory or disk starts nothing (KIT-457), said once per change.
        A read that failed, a ledger that refuses, or a hand-over the tracker would not take
        stops this pass's starts, and the pass ends loud."""
        if self.now - self.state.get("queue_at", 0) < self.cfg["queue_every_seconds"]:
            return
        self.state["queue_at"] = self.now
        try:
            with open(self.cfg["queue_config"], encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError, RecursionError) as exc:
            return self.queue_failed("its settings could not be read (%s)" % type(exc).__name__)
        cfg, bad = starter.load(raw)
        if bad:
            return self.queue_failed("its settings are not usable: %s" % "; ".join(bad)[:300])
        gql = starter.transport(lambda q, v: self.tracker.linear(q, v), BridgeError)
        result = starter.decide(raw, gql, self.now,
                                say=lambda line: self.say("  queue: %s" % self.clean(line)))
        ledger = cfg["reader"]["ledger_file"]
        if result["state"] == "closed":
            self.say("  queue: the working window is closed (%s), so nothing starts"
                     % result["why"])
            return
        if result["state"] == "no-room":
            # KIT-457: the machine is short of room. Nothing starts; running work is untouched.
            # Said once per change in WHICH lines are over, not each reading.
            self.record_room(result.get("room") or [])
            lines = ",".join(result.get("lines") or ["?"])
            if self.state.get("queue_room") != lines:
                self.state["queue_room"] = lines
                self.queue_failed("the machine is short of room (%s)" % self.clean(result["why"]))
            else:
                self.say("  queue: still short of room (%s), so nothing starts"
                         % self.clean(result["why"]))
            return
        # The machine was read and has room, whatever the tracker then did (KIT-474).
        self.record_room()
        if result["state"] == "unread":
            return self.queue_failed("the tracker could not be read (%s)" % result["why"])
        if self.state.pop("queue_room", None) is not None:
            self.say("  queue: room again: starts resume")
        stopped = self.resume_stopped(cfg, ledger, agent_id, why)
        if stopped is None:
            return
        agent_id, why, resumed = stopped
        if resumed:
            # The order above was worked out before these rows were written, so a start now
            # could take the day past its load. The starts wait one pass, which reads them.
            self.say("  queue: %d resumed this pass, so new starts wait for the next one, which "
                     "counts them in the day's load" % resumed)
            return
        for entry in result["decision"]["start"]:
            if agent_id is None and why is None:
                agent_id, why = self.tracker.agent_user(self.cfg["agent_user_name"])
            if not agent_id:
                return self.queue_failed("there is no agent to hand work to (%s)" % why)
            if self.actions_left() <= 0:
                self.say("  action limit: %s waits for the next hour" % entry["ticket"])
                return
            try:
                self.start_queued(entry, agent_id, ledger)
            except (OSError, ValueError) as exc:
                return self.queue_failed("its ledger refused a start (%s)" % (
                    exc if isinstance(exc, ValueError) else type(exc).__name__))
            except BridgeError as exc:
                return self.queue_failed("the tracker would not take %s's hand-over (%s)"
                                         % (entry["ticket"], self.clean(str(exc))[:160]))

    def resume_stopped(self, cfg, ledger, agent_id, why):
        """(agent_id, why, resumed) once the plan's limit stops are dealt with, `resumed` being
        how many were handed over, or None when this pass ended loud (KIT-472). A coding piece the plan's five-hour or weekly window cut off is
        handed over once more after that window resets, inside the working window only (the
        pass has returned already when it is closed or short of room): the usage file's
        `limit_stops`, the pieces still the agent's with no session running and none made since
        the stop, through the one hand-over the retry uses. Each stop resumes once. A second
        stop, a resume that failed, and any stop it cannot place are said, and are never retried
        by themselves: the stop is written down before the hand-over, not after."""
        done = self.state.setdefault("queue_resumed", {})
        found = starter.resumable(starter.read_usage(cfg["usage_file"]), self.now, done)
        said = found["said"]
        for item in said:
            self.say("  queue: resume: %s" % self.clean(item["text"]))
        key = ",".join(sorted("%s:%s" % (i["ticket"] or "-", i["why"]) for i in said))
        if key != self.state.get("queue_resume_said", ""):
            self.state["queue_resume_said"] = key
            for item in said:
                self.release_errors.append("the queue: %s" % self.clean(item["text"]))
        for item in said:
            if item["why"] == "again":
                done[item["ticket"]]["again"] = item["ended_at"]
        for ident in found["waiting"]:
            self.say("  queue: resume: %s waits for its window to reset" % ident)
        resumed = 0
        for stop in found["resume"]:
            if agent_id is None and why is None:
                agent_id, why = self.tracker.agent_user(self.cfg["agent_user_name"])
            if not agent_id:
                self.queue_failed("there is no agent to hand work to (%s)" % why)
                return None
            if self.actions_left() <= 0:
                self.say("  action limit: resuming %s waits for the next hour" % stop["ticket"])
                break
            try:
                ok = self.resume_one(stop, agent_id, ledger, done)
            except (OSError, ValueError) as exc:
                self.queue_failed("its ledger refused a resume (%s)" % (
                    exc if isinstance(exc, ValueError) else type(exc).__name__))
                return None
            except BridgeError as exc:
                written = (done.get(stop["ticket"]) or {}).get("ended_at") == stop["ended_at"]
                self.queue_failed("the tracker would not take the resume of %s (%s)%s" % (
                    stop["ticket"], self.clean(str(exc))[:160],
                    ", and it is not retried by itself" if written
                    else "; nothing was written, so it is tried again next pass"))
                return None
            if ok is False:
                return None                 # said loud already: nothing more starts this pass
            resumed += bool(ok)
        return agent_id, why, resumed

    def resume_one(self, stop, agent_id, ledger, done):
        """One resume: every check read again, the ledger row and the stop written down, then
        the retry's two writes (the agent taken off, put back), a read to see Linear made a
        new hand-over, and the comment. A piece that is no longer the agent's or is closed, or
        was handed over again since the stop, is let go, written down so it is not read again.
        One that cannot be judged now (a session still running, a list not read whole) is
        held with nothing written, and read again next pass. True when it was handed over,
        False when it failed loud (the pass ends), None when it was let go or held."""
        ident = stop["ticket"]
        issue = self.tracker.issue(ident)
        if not issue or not issue.get("id"):
            self.say("  queue: resume: %s is not in the tracker, so it is let go" % ident)
            done[ident] = dict(done.get(ident) or {}, ended_at=stop["ended_at"], result="gone")
            return
        node = self.tracker.epic(ident) or {}
        sessions = [n or {} for n in self.tracker.sessions_on(issue["id"])]
        if not self.tracker.sessions_read_whole():
            self.say("  queue: resume: the sessions were not read to the end, so %s is held"
                     % ident)
            return
        stopped_at = datetime.fromisoformat(stop["ended_at"].replace("Z", "+00:00")).timestamp()
        for n in sessions:
            made = (n.get("createdAt") or "")
            try:
                later = datetime.fromisoformat(made.replace("Z", "+00:00")).timestamp()
            except ValueError:
                later = None
            if later is not None and later > stopped_at:
                self.say("  queue: resume: %s was handed over again since it stopped, so it "
                         "is let go" % ident)
                done[ident] = dict(done.get(ident) or {}, ended_at=stop["ended_at"],
                                   result="restarted")
                return
        refusal = self.handover_refusal("retry", issue, node, sessions, agent_id)
        if refusal:
            if refusal[2] in ("team", "closed", "not the agent's"):
                self.say("  queue: resume: %s is let go (%s)" % (ident, refusal[2]))
                done[ident] = dict(done.get(ident) or {}, ended_at=stop["ended_at"],
                                   result=refusal[2])
            else:
                self.say("  queue: resume: %s is held (%s)" % (ident, refusal[2]))
            return
        if self.dry:
            self.say("  [dry-run] the queue would hand %s over again" % ident)
            return
        starter.record_start(ledger, self.now, ident, "resume")
        done[ident] = dict(done.get(ident) or {}, ended_at=stop["ended_at"], result="failed")
        self.save()
        before = set(n.get("id") for n in sessions)
        self.delegate_to(issue["id"], None)
        try:
            self.delegate_to(issue["id"], agent_id, count=False)
        except BridgeError as exc:
            self.queue_failed("I took the agent off %s to resume it but could not put it back, "
                              "so nobody holds it now (%s)" % (ident, self.clean(str(exc))[:160]))
            return False
        self.tracker.forget_sessions()
        if not [n for n in self.tracker.sessions_on(issue["id"]) if (n or {}).get("id")
                not in before]:
            self.queue_failed("%s was handed over again, but Linear made no new hand-over for "
                              "it, so nothing was resumed" % ident)
            return False
        done[ident]["result"] = "resumed"
        self.save()
        self.attribute(issue["id"], ident, starter.resume_words(stop), done="handed over again")
        self.counts["acted"] += 1
        self.say("  acted: the queue handed %s over again (the plan's %s limit stopped it)"
                 % (ident, stop["window"]))
        return True

    def queue_failed(self, why):
        """Said in the log, and the pass ends loud: nothing more starts from the queue."""
        self.say("  QUEUE: %s; nothing more starts this pass" % why)
        self.release_errors.append("the queue: %s" % why)

    def queued_piece(self, entry):
        """(rec, child, kids) as start_child takes them, when a queued ticket is a piece of a
        plan approved in Slack, else None. The plan is read again, as each pass reads it."""
        for epic_id, rec in sorted(self.state["epics"].items()):
            if entry.get("plan") and rec.get("ticket") == entry["plan"]:
                approved = rec.get("approved") or {}
                kids = in_order([k for k in children_of(self.tracker.epic(epic_id) or {})
                                 if k["id"] in approved])
                child = next((k for k in kids if k.get("identifier") == entry["ticket"]), None)
                return (rec, child, kids) if child else None
        return None

    def start_queued(self, entry, agent_id, ledger):
        """One start the queue made: its ledger row, then the hand-over."""
        ident = entry["ticket"]
        piece = self.queued_piece(entry) if entry["source"] == "plan" else None
        if self.dry:
            self.say("  [dry-run] the queue would hand %s to the agent" % ident)
            return
        starter.record_start(ledger, self.now, ident, entry["source"])
        if piece:
            self.start_child(piece[0], piece[1], piece[2], agent_id)
            return
        self.hand_over(entry["id"], ident, agent_id, starter.attribution(entry))
        self.counts["acted"] += 1
        self.say("  acted: the queue handed %s to the agent (%s)" % (ident, entry["source"]))

    # -- a finished plan (KIT-299) -------------------------------------------------
    def finish_epic(self, epic_id, rec, epic, kids):
        """Every approved piece is finished: close the plan, once every child of it is Done
        (an extra one included: a plan is not done while any piece of it is open), none was
        canceled, no session on one is open (one the agent never picked up is said as that,
        KIT-290), and no pull request the GitHub integration linked is still open, or
        closed with none merged. Each hold is said once, and the plan is still followed;
        one held once its pieces were read (by a pull request, a done state the team lacks,
        the action limit) reads them again at most every FINISH_RECHECK_SECONDS. `kids` are
        the approved pieces, all finished here."""
        ticket, done = rec["ticket"], self.cfg["done_state"]
        pieces = in_order(children_of(epic)) or kids
        for k in pieces:
            ident, kind = k.get("identifier") or "?", (k.get("state") or {}).get("type")
            if kind == "canceled":
                self.note(rec, "canceled:" + k["id"], "%s was canceled, so not every piece of "
                          "%s shipped, and I won't mark it %s. Close it yourself when you're "
                          "happy with it." % (ident, ticket, done))
                return
            if kind != "completed":
                self.note(rec, "open:" + k["id"], "%s is still %s, so I'll mark %s %s once "
                          "it's finished too." % (ident, (k.get("state") or {}).get("name")
                                                  or "open", ticket, done))
                return
        for k in pieces:
            if self.only_unpicked(k):
                self.note(rec, "unpicked-end:" + k["id"], "%s is marked finished, but the "
                          "agent hasn't picked up a prompt on it. I'll mark %s %s once that "
                          "prompt ends. I can't tell when that will be: the agent may still "
                          "pick it up." % (k.get("identifier") or "?", ticket, done))
                return
            if self.live_session(k):
                self.note(rec, "ending:" + k["id"], "%s is marked finished, but its dev is "
                          "still working on it. I'll mark %s %s once the dev stops."
                          % (k.get("identifier") or "?", ticket, done))
                return
        if self.now - rec.get("finish_read", 0) < FINISH_RECHECK_SECONDS:
            return
        # Recorded before the read, so whatever holds the close after it, the pieces are
        # read again only that much later.
        rec["finish_read"] = self.now
        read = self.read_pieces(pieces)
        held = [(p, piece_hold(p)) for p in read]
        held = [(p, why) for p, why in held if why]
        if held:
            for p, why in held:
                self.note(rec, "unmerged:%s:%s" % (p["id"], why), "%s, so I haven't marked "
                          "%s %s. I'll look again." % (why, ticket, done))
            return
        self.close_epic(epic_id, rec, epic, read, move=True)

    def read_pieces(self, kids):
        """Each piece as the tracker shows it now: its pull requests, from the GitHub
        integration's own links, whether it has more links than one read holds, and what
        its newest review left. One read a piece."""
        out = []
        for k in kids:
            node = self.tracker.piece(k["id"]) or {}
            review, partial, unreadable = piece_review(node, self.cfg["owner_user_id"])
            out.append({"id": k["id"], "ident": k.get("identifier") or "?",
                        "title": k.get("title") or "", "prs": piece_prs(node),
                        "links_over": bool((((node.get("attachments") or {}).get("pageInfo"))
                                            or {}).get("hasNextPage")),
                        "review": review, "partial": partial, "unreadable": unreadable,
                        "more": bool((((node.get("comments") or {}).get("pageInfo"))
                                      or {}).get("hasNextPage"))})
        return out

    def closed_already(self, epic_id, rec, epic):
        """The plan is in a done state. One this bridge was closing when a pass stopped
        after the move is finished now, never moved again. One someone else closed once
        every piece was finished is said and let go, with no move. False otherwise: a plan
        closed before its pieces were finished is said as before."""
        kids = children_of(epic)
        if rec.get("closing"):
            self.close_epic(epic_id, rec, epic, self.read_pieces(in_order(kids)), move=False)
            return True
        if kids and all((k.get("state") or {}).get("type") == "completed" for k in kids):
            self.drop(epic_id, rec, "%s is already %s, and every piece of it is finished, so "
                      "there's nothing left for me to close. I've stopped following it."
                      % (rec["ticket"], (epic.get("state") or {}).get("name") or "done"))
            return True
        return False

    def close_epic(self, epic_id, rec, epic, read, move):
        """Move the plan to its team's done state, as the owner, comment on it, post the
        wrap-up, ask about the notes left, and stop following it. Each step is recorded as
        it lands: `closing` before the move, so a pass that stops after it is finished by
        closed_already without moving again, then `closed_at`, `commented`, `wrapped` and
        `asked`, so no pass after one that stopped repeats a step. The question is recorded
        before the plan is let go, and needs nothing in its record."""
        ticket = rec["ticket"]
        team = (epic.get("team") or {}).get("key") or rec.get("team")
        done = (self.cfg["done_state"] if move
                else (epic.get("state") or {}).get("name") or self.cfg["done_state"])
        if move and not rec.get("closed_at"):
            done_id, why = self.tracker.team_state(team, done, "completed")
            if not done_id:
                self.note(rec, "nodone:" + why, "Every piece of %s is finished, but I can't "
                          "mark it %s: %s." % (ticket, done, why))
                return
            if self.actions_left() <= 0:
                self.say("  action limit: closing %s waits for the next hour" % ticket)
                return
            if self.dry:
                self.say("  [dry-run] would mark %s %s and post its wrap-up" % (ticket, done))
                return
            rec["closing"] = self.now
            self.save()
            if (epic.get("state") or {}).get("id") != done_id:
                self.tracker.move(epic["id"], done_id)
                self.state["acted_at"].append(self.now)
            rec["closed_at"] = self.now
            self.save()
        elif self.dry:
            self.say("  [dry-run] would finish closing %s" % ticket)
            return
        if not rec.get("commented"):
            body = closing_comment(rec, read, done, self.now)
            try:
                self.tracker.comment(epic["id"], body)
            except BridgeError as exc:
                # The move stands: it cannot be taken back. The record of it is owed.
                self.say("  NOTE: %s was closed, but its comment failed: %s" % (ticket, exc))
                self.owe(epic["id"], ticket, body)
            rec["commented"] = True
            self.save()
        notes = notes_of(read)
        if not rec.get("wrapped"):
            text, notes_part, about, facts = wrap_up_parts(epic, read, notes, done)
            self.post(rec["channel"], rec["thread"], text, kind="plan-finished",
                      bind=notes_part, join="\n", plain=about, facts=facts)
            rec["wrapped"] = True
            self.save()
        if notes and not rec.get("asked"):
            self.ask_followup(rec, epic, team, notes)
            rec["asked"] = True
            self.save()
        self.state["epics"].pop(epic_id, None)
        self.save()
        self.counts["acted"] += 1
        self.say("  acted: closed %s as %s, %d review note(s) left" % (ticket, done,
                                                                        len(notes)))

    def ask_followup(self, rec, epic, team, notes):
        """ONE question once a plan closes with review notes left, built like the others: a
        yes binds to these exact notes and to the terms the text states, never to the
        epic's record, which is let go once it is asked."""
        channel = rec["channel"]
        dm = channel in self.dm_channels
        thread = None if dm else rec["thread"]
        head, about, bind, facts = followup_parts(rec["ticket"], len(notes), team, self.cfg,
                                                  dm=dm)
        posted = self.post(channel, thread, head, kind="question-followup", bind=bind,
                           join=" ", plain=about, facts=facts, asks=True)
        self.state["questions"][key_of(channel, posted)] = {
            "kind": FOLLOWUP, "ticket": rec["ticket"], "ticket_id": epic.get("id"),
            "channel": channel, "thread": thread, "ts": posted, "asked_at": self.now,
            "asked_for": rec.get("approved_by") or "", "status": OPEN,
            "fingerprint": notes_fingerprint(notes), "promised": self.promised(FOLLOWUP, team),
            "team": team, "notes": len(notes)}
        self.counts["asked"] += 1
        self.say("  asked: followup %s (%d notes)" % (rec["ticket"], len(notes)))

    def act_followup(self, channel, thread, q, msg):
        """File the notes a closed plan's reviews left as ONE ticket in its team, as the
        owner: a title and a description, and nothing else, so it starts no work. Re-read
        first: a yes after the notes changed files nothing. At most once: recorded before
        the write, so a pass that stops after it never files twice."""
        def finish(status, text, what):
            # Posted first: the status changes only after the reply saying so lands.
            self.post(channel, thread, text, kind="followup-" + ("filed" if status == DONE
                                                                 else "refused"),
                      facts={"ticket": q["ticket"]})
            q.update(status=status, answered_by=msg["user"], answered_ts=msg.get("ts"))
            self.mark(channel, msg, what)
        if q.get("filing") and not q.get("filed"):
            finish(REFUSED, "I started filing the notes from %s, but I can't confirm the "
                   "ticket was made, so I won't file it again. Look for “Follow-ups from” in "
                   "%s's tickets." % (q["ticket"], q.get("team") or "the team"),
                   "refused: filing not confirmed")
            return
        if not q.get("filed"):
            if self.refused_over_limit(channel, thread, msg):
                return
            epic = self.tracker.epic(q.get("ticket_id") or q["ticket"])
            if not epic or not epic.get("id"):
                finish(REFUSED, "I can't find %s in Linear any more, so nothing was "
                       "filed." % q["ticket"], "refused: gone")
                return
            team = (epic.get("team") or {}).get("key")
            if team != q.get("team") or team not in self.cfg["team_keys"]:
                finish(REFUSED, "%s is now on %s, so nothing was filed, and I have closed the "
                       "question." % (q["ticket"], team or "no team"), "refused: team")
                return
            read = self.read_pieces(in_order(children_of(epic)))
            notes = notes_of(read)
            if notes_fingerprint(notes) != q.get("fingerprint"):
                finish(REFUSED, "The review notes on %s's pieces changed since I asked, so "
                       "nothing was filed, and I have closed the question." % q["ticket"],
                       "refused: changed since asked")
                return
            team_id, why = self.tracker.team_id(team)
            if not team_id:
                finish(REFUSED, "I can't file the notes from %s: %s." % (q["ticket"], why),
                       "refused: no team")
                return
            if self.dry:
                self.say("  [dry-run] would file %d notes from %s" % (len(notes), q["ticket"]))
                return
            # The name is the member's own to set: relayed text, like the answer's (KIT-224).
            name = _relayed(self.name_of(msg["user"]), 120) or msg["user"]
            when = iso(ts_float(msg.get("ts")))
            q["filing"] = self.now
            self.save()
            made = self.tracker.file_followup(team_id, followup_title(epic),
                                              followup_description(epic, notes, name,
                                                                   msg["user"], when, read))
            self.state["acted_at"].append(self.now)
            q.update(filed=made.get("identifier"), filed_url=made.get("url") or "",
                     filed_title=followup_title(epic), filed_notes=len(notes),
                     epic_id=epic["id"], filed_body=(
                         "Follow-ups from this plan's reviews are in %s%s: %d note(s) from "
                         "its pieces, filed by the Slack bridge for **%s** (Slack user %s), "
                         "who said yes in Slack at %s."
                         % (made.get("identifier"), " (%s)" % made["url"] if made.get("url")
                            else "", len(notes), name, msg["user"], when)))
            self.save()
        owed = ""
        if not q.get("linked"):
            try:
                self.tracker.comment(q["epic_id"], q["filed_body"])
            except BridgeError as exc:
                # The ticket stands: it cannot be taken back. The link to it is owed.
                self.say("  NOTE: %s was filed, but the comment on %s failed: %s"
                         % (q["filed"], q["ticket"], exc))
                self.owe(q["epic_id"], q["ticket"], q["filed_body"])
                owed = (" The ticket was filed, but the comment on %s linking it could not be "
                        "written; I will try again." % q["ticket"])
            q["linked"] = True
            self.save()
        finish(DONE, "Filed %s “%s” in %s for <@%s>, with %d note%s. Nothing starts "
               "on it until someone hands it over.%s"
               % (q["filed"], notify.safe_title(q["filed_title"]), q.get("team") or "?",
                  msg["user"], q["filed_notes"], "" if q["filed_notes"] == 1 else "s", owed),
               "done: filed %s" % q["filed"])
        self.counts["acted"] += 1
        self.say("  acted: filed %s from %s for %s" % (q["filed"], q["ticket"], msg["user"]))


# ══════════════════════════════════════════════════════════════════════════════════════
# Selftest — offline: this module's cases, moved here from the bridge's selftest (KIT-312),
# and its pins. The bridge's selftest runs these cases too, each in its place among the
# others, with one kit for all of them.
# ══════════════════════════════════════════════════════════════════════════════════════
def cases_approve(kit):
    """Approve an epic, and its children start in order (KIT-223): its cases from the bridge's selftest, moved
    here unchanged (KIT-312)."""
    from pipeline_bridge import (ALICE, BOB, CHAN, CHATBOT, EXIT_ERROR, EXIT_OK, EXPIRED,
                                 OWNER, state_path)
    with kit as tmp:
        check, now, posted = kit.take("check", "now", "posted")
        refusing_comment, run, status_of = kit.take("refusing_comment", "run", "status_of")
        world = kit.world
        # 11e. APPROVE AN EPIC, AND ITS CHILDREN START IN ORDER (KIT-223) -------------------
        def approved(name, wip=1, kids=None, setup=None, **over):
            c, s, t = world(name, act=True, agent_user_name="Agent", epic_wip=wip, **over)
            t.add_epic("PROD-50", kids or (("PROD-51", ()), ("PROD-52", ("PROD-51",)),
                                           ("PROD-53", ("PROD-52",))))
            if setup:
                setup(t)
            run(c, s, t)
            root = s.add(ALICE, "approve PROD-50")
            run(c, s, t)
            s.add(BOB, "yes", thread=root)
            run(c, s, t)
            return c, s, t, root

        def delegated(t):
            return [i for i, _a in t.delegations]

        def followed(c):
            return sorted(json.load(open(state_path(c))).get("epics", {}))

        c, s, t, root = approved("approve")
        asked = [p for p in posted(s) if "Approve this plan?" in p]
        check("approve-question-lists-the-children-in-order",
              len(asked) == 1 and asked[0].index("PROD-51") < asked[0].index("PROD-52")
              < asked[0].index("PROD-53"), True)
        check("approve-moves-the-epic-to-ready", t.moves, [("uuid-50", "st-todo")])
        check("the-epic-says-who-approved",
              [b.splitlines()[0] for i, b in t.comments if i == "uuid-50"],
              ["Approved in Slack by **%s** (Slack user %s) at %s, through the Slack bridge."
               % (BOB, BOB, iso(float([m for m in s.messages if m.get("user") == BOB
                                         and m.get("text") == "yes"][0]["ts"])))])
        check("the-first-child-starts-at-once", delegated(t), ["uuid-51"])
        check("delegated-to-the-agent", t.delegations[0][1], "agent-1")
        first = [b for i, b in t.comments if i == "uuid-51"]
        check("a-delegation-first-line-names-the-approver",
              len(first) == 1 and BOB in first[0].splitlines()[0]
              and "approved PROD-50" in first[0].splitlines()[0], True)
        check("the-thread-says-the-child-started",
              sum("Started PROD-51" in p for p in posted(s)), 1)
        check("the-epic-is-never-delegated", "uuid-50" in delegated(t), False)
        run(c, s, t)
        check("wip-holds-the-next-child", delegated(t), ["uuid-51"])
        t.set_state("PROD-51", "completed")
        run(c, s, t)
        check("the-next-child-starts-when-its-blocker-is-done", delegated(t),
              ["uuid-51", "uuid-52"])
        t.set_state("PROD-52", "completed")
        run(c, s, t)
        t.set_state("PROD-53", "completed")
        run(c, s, t)
        run(c, s, t)
        check("every-child-finished-is-said-once-and-the-epic-let-go",
              (delegated(t), sum("pieces of PROD-50" in p for p in posted(s)), followed(c)),
              (["uuid-51", "uuid-52", "uuid-53"], 1, []))

        c, s, t, root = approved("order", wip=2, kids=(("PROD-51", ("PROD-52",)),
                                                       ("PROD-52", ())))
        check("dependency-order-not-ticket-order", delegated(t), ["uuid-52"])
        asked = [p for p in posted(s) if "Approve this plan?" in p][0]
        check("the-question-lists-dependency-order", asked.index("PROD-52")
              < asked.index("PROD-51"), True)
        c, s, t, root = approved("wip-two", wip=2, kids=(("PROD-51", ()), ("PROD-52", ()),
                                                         ("PROD-53", ())))
        check("wip-two-starts-two", delegated(t), ["uuid-51", "uuid-52"])

        # A blocker reopened: what waits on it waits again, the stall is said once, and a
        # child is never handed over twice.
        c, s, t, root = approved("reopen", kids=(("PROD-51", ()), ("PROD-52", ("PROD-51",)),
                                                 ("PROD-53", ("PROD-51",))))
        t.set_state("PROD-51", "completed")
        run(c, s, t)
        t.set_state("PROD-51", "backlog")
        t.issues["PROD-51"]["delegate"] = None
        t.set_state("PROD-52", "completed")
        run(c, s, t)
        run(c, s, t)
        check("a-reopened-blocker-holds-what-waits-on-it", delegated(t),
              ["uuid-51", "uuid-52"])
        check("a-stall-is-said-once",
              sum("PROD-53 waits on PROD-51" in p for p in posted(s)), 1)

        # A child already delegated, or with a session, is never handed over, and counts.
        c, s, t, root = approved("pre-delegated", kids=(("PROD-51", ()), ("PROD-52", ())),
                                 setup=lambda t_: t_.issues["PROD-51"].update(
                                     delegate={"id": "someone"}))
        check("a-delegated-child-is-not-handed-over-and-holds-wip", delegated(t), [])
        t.set_state("PROD-51", "completed")
        run(c, s, t)
        check("then-the-next-starts", delegated(t), ["uuid-52"])
        c, s, t, root = approved("has-session", kids=(("PROD-51", ()), ("PROD-52", ())),
                                 setup=lambda t_: t_.sessions.update({"uuid-51": [{"id": "x"}]}))
        check("a-child-with-a-session-is-not-handed-over", delegated(t), [])

        # Only an epic the idea gate filed.
        for name, labels, kid_label, needle in (
                ("not-gate", (), "provenance:epic", "provenance:agent"),
                ("kid-not-gate", ("provenance:agent",), None, "provenance:epic")):
            c, s, t = world(name, act=True, agent_user_name="Agent")
            t.add_epic("PROD-50", (("PROD-51", ()),), epic_labels=labels, kid_label=kid_label)
            run(c, s, t)
            s.add(ALICE, "approve PROD-50")
            run(c, s, t)
            check("refused-unless-the-idea-gate-filed-it:" + name,
                  (needle in posted(s)[-1], json.load(open(state_path(c)))["questions"],
                   t.moves, t.delegations), (True, {}, [], []))

        # The epic handed to the agent by someone: children would stack on it, so stop.
        c, s, t, root = approved("epic-delegated", kids=(("PROD-51", ()), ("PROD-52", ())))
        t.issues["PROD-50"]["delegate"] = {"id": "agent-1"}
        t.set_state("PROD-51", "completed")
        run(c, s, t)
        check("an-epic-handed-to-the-agent-stops-its-children",
              (delegated(t), any("stack" in p for p in posted(s)), followed(c)),
              (["uuid-51"], True, []))

        # What the owner approved is what starts: a child added or changed afterwards (a
        # session holds the tracker's write tools) is never handed over, and said once.
        c, s, t, root = approved("added-later", wip=2, kids=(("PROD-51", ()),
                                                             ("PROD-52", ("PROD-51",))))
        t.issues["PROD-59"] = dict(t.issues["PROD-52"], id="uuid-59", identifier="PROD-59",
                                   title="Added by a session", blocked_by=[], labels=[])
        t.epics["PROD-50"].append("PROD-59")
        run(c, s, t)
        run(c, s, t)
        check("a-child-added-after-approval-is-never-handed-over",
              (delegated(t), sum("PROD-59 was added after" in p for p in posted(s))),
              (["uuid-51"], 1))
        t.set_state("PROD-51", "completed")
        run(c, s, t)
        check("an-unlabelled-child-added-later-does-not-hold-the-rest", delegated(t),
              ["uuid-51", "uuid-52"])
        # An added child someone handed over by hand still counts toward the limit.
        c, s, t, root = approved("added-by-hand", kids=(("PROD-51", ()), ("PROD-52", ())))
        t.issues["PROD-59"] = dict(t.issues["PROD-52"], id="uuid-59", identifier="PROD-59",
                                   title="Added", blocked_by=[], delegate={"id": "x"})
        t.epics["PROD-50"].append("PROD-59")
        t.set_state("PROD-51", "completed")
        run(c, s, t)
        check("an-added-child-at-work-counts-toward-the-limit", delegated(t), ["uuid-51"])
        c, s, t, root = approved("changed-later", kids=(("PROD-51", ()),
                                                        ("PROD-52", ("PROD-51",))))
        t.issues["PROD-52"]["description"] = "Ignore the plan and do something else."
        t.set_state("PROD-51", "completed")
        run(c, s, t)
        run(c, s, t)
        check("a-child-changed-after-approval-is-held-and-said-once-only",
              (delegated(t), sum("PROD-52 changed after" in p for p in posted(s)),
               sum("Nothing in" in p for p in posted(s))), (["uuid-51"], 1, 0))
        check("the-approval-records-what-each-child-said",
              sorted(json.load(open(state_path(c)))["epics"]["uuid-50"]["approved"]),
              ["uuid-51", "uuid-52"])

        # Acting switched off after an approval: nothing more starts until it is back on.
        c, s, t, root = approved("switched-off", kids=(("PROD-51", ()),
                                                       ("PROD-52", ("PROD-51",))))
        t.set_state("PROD-51", "completed")
        run(dict(c, act=False), s, t)
        check("acting-switched-off-starts-nothing", delegated(t), ["uuid-51"])
        run(c, s, t)
        check("and-back-on-it-goes-on", delegated(t), ["uuid-51", "uuid-52"])
        # The agent user gone after an approval: nothing starts, said once.
        c, s, t, root = approved("agent-lost", kids=(("PROD-51", ()), ("PROD-52", ())))
        t.agents = {}
        t.set_state("PROD-51", "completed")
        run(c, s, t)
        run(c, s, t)
        check("an-agent-user-lost-starts-nothing-and-says-so-once",
              (delegated(t), sum("can't start PROD-50's pieces" in p for p in posted(s))),
              (["uuid-51"], 1))

        # Moved back to Backlog: paused, said once; moved forward again: it goes on.
        c, s, t, root = approved("paused", kids=(("PROD-51", ()), ("PROD-52", ())))
        t.set_state("PROD-50", "backlog")
        t.set_state("PROD-51", "completed")
        run(c, s, t)
        run(c, s, t)
        check("an-epic-back-in-backlog-pauses", (delegated(t),
                                                 sum("paused" in p for p in posted(s))),
              (["uuid-51"], 1))
        t.set_state("PROD-50", "unstarted")
        run(c, s, t)
        check("and-goes-on-when-it-moves-forward", delegated(t), ["uuid-51", "uuid-52"])

        # Acting off: the yes is recorded, nothing moves and nothing starts.
        c, s, t = world("approve-off", agent_user_name="Agent")
        t.add_epic("PROD-50", (("PROD-51", ()),))
        run(c, s, t)
        root = s.add(ALICE, "approve PROD-50")
        run(c, s, t)
        s.add(BOB, "yes", thread=root)
        run(c, s, t)
        run(c, s, t)
        check("approve-with-acting-off-starts-nothing", (t.moves, t.delegations,
                                                         "switched off" in posted(s)[-1]),
              ([], [], True))
        # No agent user named: refused before asking. One that cannot be found: refused.
        c, s, t = world("no-agent", act=True)
        t.add_epic("PROD-50", (("PROD-51", ()),))
        run(c, s, t)
        s.add(ALICE, "approve PROD-50")
        run(c, s, t)
        check("approve-without-an-agent-user-is-refused", "agent_user_name" in posted(s)[-1],
              True)
        c, s, t = world("agent-gone", act=True, agent_user_name="Agent")
        t.add_epic("PROD-50", (("PROD-51", ()),))
        run(c, s, t)
        root = s.add(ALICE, "approve PROD-50")
        run(c, s, t)
        t.agents = {}
        s.add(BOB, "yes", thread=root)
        run(c, s, t)
        check("an-agent-user-not-found-moves-nothing", (t.moves, t.delegations,
                                                        "can't approve" in posted(s)[-1]),
              ([], [], True))
        # Approving is an action: over the limit, nothing moves.
        c, s, t = world("approve-limit", act=True, agent_user_name="Agent",
                        max_actions_per_hour=1)
        t.add_epic("PROD-50", (("PROD-51", ()),))
        run(c, s, t)
        r5 = s.add(ALICE, "plan PROD-5")
        r50 = s.add(ALICE, "approve PROD-50")
        run(c, s, t)
        s.add(BOB, "yes", thread=r5)
        s.add(BOB, "yes", thread=r50)
        run(c, s, t)
        check("approving-counts-against-the-action-limit",
              (t.moves, t.delegations, "my limit" in posted(s)[-1]),
              ([("uuid-5", "st-plan-it")], [], True))
        # An epic moved to another team after the question: refused, nothing moves.
        c, s, t = world("approve-team-moved", act=True, agent_user_name="Agent")
        t.add_epic("PROD-50", (("PROD-51", ()),))
        run(c, s, t)
        root = s.add(ALICE, "approve PROD-50")
        run(c, s, t)
        for k in ("PROD-50", "PROD-51"):
            t.issues[k]["team"] = {"key": "OPS"}
        s.add(BOB, "yes", thread=root)
        run(c, s, t)
        check("an-epic-moved-off-a-team-is-refused", (t.moves, t.delegations,
                                                      "now on OPS" in posted(s)[-1]),
              ([], [], True))
        # The action limit counts each child started.
        c, s, t, root = approved("act-limit-kids", wip=3, max_actions_per_hour=2,
                                 kids=(("PROD-51", ()), ("PROD-52", ()), ("PROD-53", ())))
        check("the-action-limit-counts-children", delegated(t), ["uuid-51"])
        run(c, s, t, at=now + 3700)
        check("and-the-rest-start-an-hour-later", delegated(t),
              ["uuid-51", "uuid-52", "uuid-53"])
        # A dry run starts nothing.
        c, s, t, root = approved("dry-release", kids=(("PROD-51", ()), ("PROD-52", ())))
        t.set_state("PROD-51", "completed")
        run(c, s, t, dry=True)
        check("a-dry-run-starts-nothing", delegated(t), ["uuid-51"])
        run(c, s, t)
        check("a-real-pass-after-it-does", delegated(t), ["uuid-51", "uuid-52"])
        # An epic already followed is not asked about again.
        c, s, t, root = approved("again", kids=(("PROD-51", ()), ("PROD-52", ())))
        s.add(ALICE, "approve PROD-50")
        run(c, s, t)
        check("an-approved-epic-is-not-asked-again", "already approved" in posted(s)[-1], True)
        # The order check, as a function the order job (B9) shares.
        c, s, t = world("order-check")
        t.add_epic("PROD-50", (("PROD-51", ()), ("PROD-52", ("PROD-51",))))
        node = t.epic("PROD-50")["children"]["nodes"][1]
        check("open-blockers-names-each-blocker-and-its-state", open_blockers(node),
              ["PROD-51 (Backlog)"])
        t.set_state("PROD-51", "completed")
        check("open-blockers-is-empty-once-done",
              open_blockers(t.epic("PROD-50")["children"]["nodes"][1]), [])

        # REVIEW OF KIT-223, 1 (HIGH): what the member saw is what starts. The children are
        # recorded when the question is ASKED; a yes after they changed approves nothing.
        for name, change in (
                ("added-before-yes", lambda t_: (t_.issues.__setitem__("PROD-59", dict(
                    t_.issues["PROD-52"], id="uuid-59", identifier="PROD-59", title="Sneaky",
                    blocked_by=[])), t_.epics["PROD-50"].append("PROD-59"))),
                ("changed-before-yes", lambda t_: t_.issues["PROD-52"].update(
                    description="Do something else.")),
                ("retitled-before-yes", lambda t_: t_.issues["PROD-52"].update(
                    title="Something the member never saw")),
                ("removed-before-yes", lambda t_: t_.epics["PROD-50"].remove("PROD-52")),
                ("relabelled-before-yes", lambda t_: t_.issues["PROD-52"]["labels"].append(
                    "track:other")),
                ("unblocked-before-yes", lambda t_: t_.issues["PROD-52"].update(
                    blocked_by=[])),
                ("moved-project-before-yes", lambda t_: t_.issues["PROD-52"].update(
                    project={"id": "another-project"}))):
            c, s, t = world(name, act=True, agent_user_name="Agent")
            t.add_epic("PROD-50", (("PROD-51", ()), ("PROD-52", ("PROD-51",))))
            run(c, s, t)
            root = s.add(ALICE, "approve PROD-50")
            run(c, s, t)
            change(t)
            s.add(BOB, "yes", thread=root)
            run(c, s, t)
            check("a-yes-after-the-children-changed-approves-nothing:" + name,
                  (t.moves, t.delegations, "changed since I asked" in posted(s)[-1],
                   followed(c)), ([], [], True, []))

        # A child added later CARRYING provenance:epic is held exactly like an unlabelled one.
        c, s, t, root = approved("added-labelled", kids=(("PROD-51", ()), ("PROD-52", ())))
        t.issues["PROD-59"] = dict(t.issues["PROD-52"], id="uuid-59", identifier="PROD-59",
                                   title="Added", blocked_by=[], labels=["provenance:epic"])
        t.epics["PROD-50"].append("PROD-59")
        t.set_state("PROD-51", "completed")
        run(c, s, t)
        check("a-labelled-child-added-later-is-never-handed-over",
              (delegated(t), any("PROD-59 was added after" in p for p in posted(s))),
              (["uuid-51", "uuid-52"], True))

        # 2 (MEDIUM): a hold is never silent. A child outside the plan at work is named; a
        # hand-over that never started is named after a while.
        c, s, t, root = approved("extra-at-work", kids=(("PROD-51", ()), ("PROD-52", ())))
        t.issues["PROD-59"] = dict(t.issues["PROD-52"], id="uuid-59", identifier="PROD-59",
                                   title="Added", blocked_by=[], labels=[])
        t.epics["PROD-50"].append("PROD-59")
        t.set_state("PROD-59", "started")
        t.set_state("PROD-51", "completed")
        run(c, s, t)
        run(c, s, t)
        check("an-extra-child-at-work-holds-the-limit-and-is-said-once",
              (delegated(t), sum("PROD-59 is at work" in p for p in posted(s))),
              (["uuid-51"], 1))
        c, s, t, root = approved("stuck-start", kids=(("PROD-51", ()), ("PROD-52", ())))
        run(c, s, t, at=now + 3 * 3600)
        run(c, s, t, at=now + 4 * 3600)
        check("a-hand-over-that-never-started-is-said-once",
              (delegated(t), sum("PROD-51 was handed to the agent" in p for p in posted(s))),
              (["uuid-51"], 1))

        # KIT-284: two hours on, a child not in a started lane is said as its agent
        # sessions show it, once. A pull request waiting for your merge is never "not
        # started", and never offered to be handed over again.
        def two_hours_on(name, lane, sessions, whole=True, kids=None, after=None, between=None,
                         **over):
            """What the thread says of PROD-51 in two passes, three and four hours after it
            was handed over (`between` runs between them); ["config refused: ..."] when the
            config is."""
            try:
                c, s, t, _root = approved(name, kids=kids or (("PROD-51", ()),
                                                              ("PROD-52", ("PROD-51",))),
                                          **over)
            except BridgeError as exc:
                return ["config refused: %s" % exc]
            t.issues["PROD-51"]["state"] = {"id": "st-lane", "name": lane, "type": "unstarted"}
            t.sessions["uuid-51"] = sessions
            t.sessions_whole = whole
            if after:
                after(c, t)
            before = len(s.posts)
            run(c, s, t, at=now + 3 * 3600)
            if between:
                between(c, t)
            run(c, s, t, at=now + 4 * 3600)
            return [p for p in posted(s)[before:] if p.startswith("PROD-51")]
        ended = [{"id": "s-51", "status": "complete"}]
        live = [{"id": "s-51", "status": "active"}]
        waits = "PROD-51's pull request is waiting for you to merge."
        then = waits + " The next piece starts once it's merged."
        def stuck_at(at):
            """The note of PROD-51, handed over at `at` and never picked up, two hours on and
            inside Linear's retries: not picked up yet, and when a second hand-over is safe,
            with no word of handing it over again (KIT-289)."""
            return ("PROD-51 was handed to the agent at %s and hasn't been picked up yet. "
                    "Linear may still deliver that hand-over until %s, so a second one before "
                    "then could start two devs on it. It still counts toward the limit of 1. "
                    "I'll say so again if it hasn't started by then."
                    % (iso(at), iso(at + HANDOVER_RETRY_SECONDS)))
        stuck = stuck_at(now)
        check("a-child-waiting-for-your-merge-is-said-to-wait-on-you-once",
              two_hours_on("handoff", "Needs Approval", ended), [then])
        check("a-child-in-a-hand-off-lane-is-never-not-started-even-with-no-session-found",
              (two_hours_on("handoff-none", "Needs Approval", []),
               two_hours_on("handoff-cut", "Needs Approval", [], whole=False)),
              ([then], [then]))
        check("a-child-whose-session-still-runs-gets-no-note-in-any-lane",
              (two_hours_on("live-todo", "Todo", live),
               two_hours_on("asking-todo", "Todo", [{"id": "s-51", "status": "awaitingInput"}]),
               two_hours_on("live-handoff", "Needs Approval", live)), ([], [], []))
        ended_todo = ("PROD-51's dev stopped before it finished, and it's in Todo now. It "
                      "still counts toward the limit of 1: hand it over again yourself, or "
                      "cancel it.")
        unsure = ("PROD-51 was handed to the agent at %s and is still in Todo. I couldn't tell "
                  "whether its dev is working: Linear holds more of the agent's work than I "
                  "can read at once. It still counts toward the limit of 1. Check it in "
                  "Linear before you hand it over again." % iso(now))
        check("a-child-whose-session-ended-elsewhere-is-said-to-have-stopped-short",
              two_hours_on("ended-todo", "Todo", [{"id": "s-51", "status": "error"}]),
              [ended_todo])
        check("no-session-found-is-never-started-only-when-the-list-was-read-whole",
              (two_hours_on("none-todo", "Todo", []),
               two_hours_on("cut-todo", "Todo", [], whole=False)),
              ([stuck], [unsure]))
        # The change's review: a hand-over always makes a session, so one the agent never
        # picked up is the never-started case, whatever the tracker calls it, and never a
        # running one. Only a session that ran is said to have ended.
        # The second review: in a hand-off lane such a session is not running either, but
        # it holds the child's slot past the merge, so the next piece is not promised.
        pending = [{"id": "s-51", "status": "pending"}]
        stale = [{"id": "s-51", "status": "stale"}]
        reprompted = ended + [{"id": "s-51b", "status": "pending"}]
        check("a-session-the-agent-never-picked-up-is-not-a-running-one",
              (two_hours_on("pending-todo", "Todo", pending),
               two_hours_on("pending-cut", "Todo", pending, whole=False),
               two_hours_on("stale-todo", "Todo", stale),
               two_hours_on("pending-handoff", "Needs Approval", pending),
               two_hours_on("reprompted-handoff", "Needs Approval", reprompted)),
              ([stuck], [stuck], [stuck], [waits], [waits]))
        # Stage E's review: a child handed over again has a newer session than the one that
        # ended, and only its newest session says how it stands, by the sessions' own
        # creation time, in whichever order the tracker lists them. A newest one nobody
        # picked up gets the note it would get alone; an ended one newer than it is ended.
        def made(status, at, sid="s-51"):
            """A session as the tracker lists it: its status and its own creation time, an
            hour and minute of one day (None: the session carries no time)."""
            node = {"id": sid, "status": status}
            if at is not None:
                node["createdAt"] = "2025-10-09T%s:00.000Z" % at
            return node
        def stuck_from(at):
            """The not-started note of a child handed over again at `at`, an hour and minute
            of that day: the two hours, and the time the note names, start from then."""
            return stuck_at(datetime.fromisoformat("2025-10-09T%s:00+00:00" % at).timestamp())
        check("a-child-handed-over-again-is-said-as-its-newest-session-shows",
              (two_hours_on("again-todo", "Todo",
                            [made("complete", "09:00"), made("pending", "10:00", "s-51b")]),
               two_hours_on("again-todo-newest-first", "Todo",
                            [made("pending", "10:00", "s-51b"), made("error", "09:00")]),
               two_hours_on("again-stale-todo", "Todo",
                            [made("complete", "09:00"), made("stale", "10:00", "s-51b")]),
               two_hours_on("again-ended-todo", "Todo",
                            [made("pending", "09:00"), made("complete", "10:00", "s-51b")]),
               two_hours_on("again-ended-todo-newest-first", "Todo",
                            [made("error", "10:00", "s-51b"), made("pending", "09:00")]),
               two_hours_on("again-handoff", "Needs Approval",
                            [made("complete", "09:00"), made("pending", "10:00", "s-51b")]),
               two_hours_on("again-ended-handoff", "Needs Approval",
                            [made("pending", "09:00"), made("complete", "10:00", "s-51b")])),
              ([stuck_from("10:00")], [stuck_from("10:00")], [stuck_from("10:00")],
               [ended_todo], [ended_todo], [waits], [waits]))
        # Sessions that cannot be put in order, one with no time, a blank one or one that is
        # not the tracker's text, say a child ended only when every one of them did. On the
        # same time, one that has not ended counts as the newer.
        def timed(status, at, sid):
            return {"id": sid, "status": status, "createdAt": at}
        check("sessions-that-cannot-be-ordered-say-ended-only-when-every-one-did",
              (two_hours_on("untimed-ended", "Todo",
                            [made("complete", None), made("pending", "10:00", "s-51b")]),
               two_hours_on("untimed-pending", "Todo",
                            [made("complete", "09:00"), made("pending", None, "s-51b")]),
               two_hours_on("blank-time", "Todo",
                            [made("complete", "09:00"), timed("pending", "", "s-51b")]),
               two_hours_on("number-time", "Todo",
                            [timed("complete", 2, "s-51"), timed("pending", 1, "s-51b")]),
               two_hours_on("same-time", "Todo",
                            [made("complete", "09:00"), made("pending", "09:00", "s-51b")]),
               two_hours_on("untimed-all-ended", "Todo",
                            [made("complete", None), made("error", None, "s-51b")])),
              ([stuck], [stuck], [stuck], [stuck], [stuck_from("09:00")], [ended_todo]))
        # Round 4: the owner who follows the ended note hands the child over again, so its
        # newest session is newer than the bridge's own hand-over. Nothing more is said until
        # two hours after that session was made, and the not-started note then names its
        # time, once. Stage E's own case: handed over again just before the two-hour check.
        hour = 3600

        def created(t):
            """A time as the tracker's session list writes one."""
            return iso(t)[:-1] + ".000Z"

        def handed_again(name, first, again, times, seen=None):
            """What the thread says of PROD-51, in Todo, on a pass at each of `times`,
            seconds after the bridge handed it over: one list per pass. Its sessions are
            `first`, and from `again` on also a newer one made then, that nobody has picked
            up yet: the owner handed it over again. With `seen`, the passes list it from
            then on: a pass that started at `seen`, before `again`, read the list after it."""
            c, s, t, _root = approved(name, kids=(("PROD-51", ()), ("PROD-52", ("PROD-51",))))
            t.issues["PROD-51"]["state"] = {"id": "st-lane", "name": "Todo", "type": "unstarted"}
            t.sessions["uuid-51"] = list(first)
            seen = again if seen is None else seen
            said = []
            for at in times:
                if at >= seen and len(t.sessions["uuid-51"]) == len(first):
                    t.sessions["uuid-51"].append({"id": "s-51b", "status": "pending",
                                                  "createdAt": created(now + again)})
                before = len(s.posts)
                run(c, s, t, at=now + at)
                said.append([p for p in posted(s)[before:] if p.startswith("PROD-51")])
            return said
        check("the-ended-note-followed-says-nothing-until-two-hours-after-the-new-hand-over",
              handed_again("ended-then-again", [timed("error", created(now), "s-51")],
                           3 * hour + 60, (3 * hour, 3 * hour + 120, 5 * hour + 60,
                                           5 * hour + 120, 6 * hour)),
              [[ended_todo], [], [], [stuck_at(now + 3 * hour + 60)], []])
        check("a-child-handed-over-again-before-the-two-hour-check-waits-two-hours-from-then",
              handed_again("again-before-check", [timed("complete", created(now), "s-51")],
                           2 * hour - 300, (2 * hour + 60, 3 * hour, 4 * hour - 300,
                                            4 * hour - 240, 5 * hour)),
              [[], [], [], [stuck_at(now + 2 * hour - 300)], []])
        # A newer time not written as the tracker writes one, one more than ten minutes past
        # the pass, or one before the bridge's own hand-over never moves the clock: the note
        # is as before.
        check("a-time-that-cannot-start-the-two-hours-again-never-moves-the-clock",
              tuple(two_hours_on(name, "Todo", [timed("complete", created(now), "s-51"),
                                                timed("pending", at, "s-51b")])
                    for name, at in (("offset-time", "2025-10-09T10:00:00.000+00:00"),
                                     ("no-such-hour", "2025-10-09T25:00:00.000Z"),
                                     ("later-than-the-pass", created(now + 10 * hour)))) +
              (two_hours_on("before-the-hand-over", "Todo",
                            [timed("pending", created(now - hour), "s-51")]),),
              ([stuck], [stuck], [stuck], [stuck]))
        # Round 5: a pass sets its clock when it starts and reads the sessions later, so a
        # hand-over made in between is dated a moment past the pass. It counts as just made:
        # that pass says nothing, and the note comes once, two hours after the hand-over.
        check("a-hand-over-made-while-a-pass-runs-waits-two-hours-from-then",
              handed_again("again-during-a-pass", [timed("error", created(now), "s-51")],
                           3 * hour + 60, (3 * hour, 3 * hour + 59, 3 * hour + 120,
                                           5 * hour + 60, 5 * hour + 120, 6 * hour),
                           seen=3 * hour + 59),
              [[ended_todo], [], [], [],
               [stuck_at(now + 3 * hour + 60)], []])
        # Round 5: the hand-over most often made again, of a child the agent never picked
        # up, before the two-hour check. Nothing until two hours after it, then the note once.
        check("a-lost-hand-over-handed-over-again-before-the-check-waits-two-hours-from-then",
              handed_again("lost-then-again", [timed("pending", created(now), "s-51")],
                           2 * hour - 600, (2 * hour + 60, 3 * hour, 4 * hour - 540, 5 * hour)),
              [[], [], [stuck_at(now + 2 * hour - 600)], []])
        # Round 5: both edges of the allowance. A session dated JUST_MADE_SECONDS past the
        # pass counts as just made and waits; one dated a second further is refused.
        check("a-session-up-to-ten-minutes-past-the-pass-is-just-made-and-no-further",
              (two_hours_on("bound-in", "Todo", [timed("complete", created(now), "s-51"),
                            timed("pending", created(now + 3 * hour + 600), "s-51b")]),
               two_hours_on("bound-out", "Todo", [timed("complete", created(now), "s-51"),
                            timed("pending", created(now + 3 * hour + 601), "s-51b")])),
              ([], [stuck]))
        # KIT-289: Linear may still deliver a hand-over nobody picked up until
        # HANDOVER_RETRY_SECONDS after it, so until then the note offers no second one. Past
        # that time, a child that still has not started is said once more, as before, with
        # the offer. Each note is said once, however many passes find it.
        late = ("PROD-51 was handed to the agent at %s and has not started. Its dev may have "
                "stopped. It still counts toward the limit of 1: hand it over again yourself, "
                "or cancel it." % iso(now))
        safe = HANDOVER_RETRY_SECONDS

        def passes(name, sessions, times):
            """What the thread says of PROD-51, in Todo with `sessions`, on a pass at each of
            `times`, seconds after the bridge handed it over: one list per pass."""
            c, s, t, _root = approved(name, kids=(("PROD-51", ()), ("PROD-52", ("PROD-51",))))
            t.issues["PROD-51"]["state"] = {"id": "st-lane", "name": "Todo", "type": "unstarted"}
            t.sessions["uuid-51"] = sessions
            said = []
            for at in times:
                before = len(s.posts)
                run(c, s, t, at=now + at)
                said.append([p for p in posted(s)[before:] if p.startswith("PROD-51")])
            return said
        check("kit289-a-lost-hand-over-is-offered-again-only-past-linears-retries-once-each",
              (passes("retries-none", [], (3 * hour, 4 * hour, safe - 60, safe + 60,
                                           safe + 120, safe + 3 * hour)),
               passes("retries-pending", [{"id": "s-51", "status": "pending"}],
                      (3 * hour, safe + 60, safe + 120)),
               passes("retries-late-first", [], (safe + 60, safe + 120))),
              ([[stuck], [], [], [late], [], []], [[stuck], [late], []], [[late], []]))
        # Handed over again, the retries count from that hand-over, as the two hours do.
        again = 2 * hour - 300
        check("kit289-a-hand-over-made-again-starts-linears-retries-again",
              handed_again("retries-again", [timed("pending", created(now), "s-51")], again,
                           (again + 2 * hour + 60, safe + 60, again + safe + 60)),
              [[stuck_at(now + again)], [],
               [late.replace(iso(now), iso(now + again))]])
        # The time is the catch-up job's own wait, which is past Linear's last retry.
        import pipeline_catchup_poller as catchup
        check("kit289-the-safe-time-is-the-catch-up-jobs-own-wait",
              (HANDOVER_RETRY_SECONDS, HANDOVER_RETRY_SECONDS > catchup.LADDER_SECONDS),
              (catchup.DEFAULT_AGE_FLOOR_SECONDS, True))

        # The second review: the promise is kept. Merged (Done, its sessions as they were),
        # a child frees its slot only once no session on it is open, and then the next
        # piece is handed over on the next pass; the promise is made exactly then.
        def merged(name, sessions):
            c, s, t, _root = approved(name, kids=(("PROD-51", ()), ("PROD-52", ("PROD-51",))))
            t.issues["PROD-51"]["state"] = {"id": "st-lane", "name": "Needs Approval",
                                            "type": "unstarted"}
            t.sessions["uuid-51"] = sessions
            before = len(s.posts)
            run(c, s, t, at=now + 3 * 3600)
            promised = then in posted(s)[before:]
            t.set_state("PROD-51", "completed")
            run(c, s, t, at=now + 4 * 3600)
            return promised, "uuid-52" in delegated(t)
        check("the-next-piece-is-promised-only-when-its-merge-frees-the-slot",
              [merged("merged-" + name, sessions) for name, sessions in (
                  ("ended", ended), ("stale", stale), ("pending", pending),
                  ("reprompted", reprompted))],
              [(True, True), (True, True), (False, False), (False, False)])

        # The second review: each note has its own mark, so a child that moves on from one
        # is said again as it is now, and the owner never keeps advice that no longer holds.
        def moves_on(sessions, lane, whole=True):
            def flip(_c, t):
                t.sessions["uuid-51"] = sessions
                t.sessions_whole = whole
                t.issues["PROD-51"]["state"] = {"id": "st-next", "name": lane,
                                                "type": "unstarted"}
            return flip
        check("a-lost-hand-over-said-again-once-it-moves-on",
              (two_hours_on("lost-then-merge", "Todo", pending,
                            between=moves_on(ended, "Needs Approval")),
               two_hours_on("lost-then-ended", "Todo", pending,
                            between=moves_on([{"id": "s-51", "status": "error"}], "Todo")),
               two_hours_on("unsure-then-lost", "Todo", [], whole=False,
                            between=moves_on(pending, "Todo"))),
              ([stuck, then], [stuck, ended_todo], [unsure, stuck]))

        # The change's review: in a hand-off lane, `agent:needs-human` is what says review ran
        # out of rounds (contract §1). That pull request is never called ready to merge, and
        # a label that lands after the merge note is said too.
        def needs_human(label):
            def add(_c, t):
                t.issues["PROD-51"]["labels"].append(label)
            return add
        rounds = ("PROD-51 is marked as needing a person, and its pull request is waiting on "
                  "you. That usually means its review ran out of rounds with findings still "
                  "open. It's yours to fix and merge, or cancel. It still counts toward the "
                  "limit of 1.")
        check("a-pull-request-whose-review-ran-out-of-rounds-is-never-called-ready-to-merge",
              (two_hours_on("rounds", "Needs Approval", ended,
                            after=needs_human("agent:needs-human")),
               two_hours_on("rounds-cased", "Needs Approval", ended,
                            after=needs_human("Agent:Needs-Human")),
               two_hours_on("rounds-later", "Needs Approval", ended,
                            between=needs_human("agent:needs-human"))),
              ([rounds], [rounds], [then, rounds]))
        # The second review: outside a hand-off lane there may be no pull request at all, so
        # the label changes nothing there.
        check("the-needs-human-note-is-said-only-in-a-hand-off-lane",
              two_hours_on("rounds-todo", "Todo", ended, after=needs_human("agent:needs-human")),
              [ended_todo])
        check("the-hand-off-lanes-are-the-configured-ones",
              (two_hours_on("lane-named", "Ready to merge", ended,
                            handoff_states=["Awaiting merge", "Ready to merge"]),
               two_hours_on("lane-default", "Ready to merge", ended)),
              ([then], ["PROD-51's dev stopped before it finished, and it's in Ready to "
                        "merge now. It still counts toward the limit of 1: hand it over "
                        "again yourself, or cancel it."]))

        def outside_blocker(t):
            # Named so it starts with PROD-51's own identifier: a blocker is matched whole.
            t.issues["PROD-510"] = dict(t.issues["PROD-52"], id="uuid-510",
                                        identifier="PROD-510", title="Outside", blocked_by=[],
                                        parent=None, labels=[],
                                        state={"id": "st-backlog", "name": "Backlog",
                                               "type": "backlog"})
            t.issues["PROD-52"]["blocked_by"].append("PROD-510")

        def changed_52(_c, t):
            t.issues["PROD-52"]["description"] = "Something else."

        def started_52_once(c, _t):
            st = json.load(open(state_path(c)))
            st["epics"]["uuid-50"]["started"].append("PROD-52")
            json.dump(st, open(state_path(c), "w"))

        def extra_at_work(_c, t):
            t.issues["PROD-59"] = dict(t.issues["PROD-52"], id="uuid-59", identifier="PROD-59",
                                       title="Added", blocked_by=[], labels=[])
            t.epics["PROD-50"].append("PROD-59")
            t.set_state("PROD-59", "started")

        def handed_52_by_hand(_c, t):
            t.issues["PROD-52"]["delegate"] = {"id": "x"}

        def second_done(_c, t):
            t.set_state("PROD-52", "completed")
        check("the-next-piece-is-promised-only-when-it-waits-for-this-merge",
              [two_hours_on("next-" + name, "Needs Approval", ended, **over) for name, over in (
                  ("last-piece", {"kids": (("PROD-51", ()),)}),
                  ("other-blocker", {"setup": outside_blocker}),
                  ("changed", {"after": changed_52}),
                  ("started-once", {"after": started_52_once}),
                  ("slots-held-by-more", {"after": extra_at_work}),
                  ("handed-by-hand", {"wip": 2, "after": handed_52_by_hand}),
                  ("can-start-without-it", {"wip": 2, "after": second_done, "kids": (
                      ("PROD-51", ()), ("PROD-52", ()), ("PROD-53", ("PROD-52",)))}),
                  ("waits-on-it-alone", {"wip": 2}))],
              [[waits]] * 7 + [[then]])

        # 3 (MEDIUM): one epic that cannot be read does not stop the others, or the pass's
        # housekeeping; the pass still ends loud.
        c, s, t, root = approved("one-bad-epic", kids=(("PROD-51", ()), ("PROD-52", ())))
        t.add_epic("PROD-60", (("PROD-61", ()),))
        r60 = s.add(ALICE, "approve PROD-60")
        run(c, s, t)
        s.add(BOB, "yes", thread=r60)
        run(c, s, t)
        stale = s.add(ALICE, "approve PROD-60")
        real_epic = t.epic

        def flaky(ident):
            if ident in ("uuid-50", "PROD-50"):
                raise BridgeError("Query too complex (simulated)")
            return real_epic(ident)
        t.epic = flaky
        t.set_state("PROD-61", "completed")
        st = json.load(open(state_path(c)))
        st["questions"]["%s:x" % CHAN] = {"kind": "plan", "ticket": "PROD-6", "channel": CHAN,
                                          "thread": stale, "ts": stale,
                                          "asked_at": now - 30 * 3600, "status": OPEN}
        json.dump(st, open(state_path(c), "w"))
        code, _o = run(c, s, t)
        st = json.load(open(state_path(c)))
        check("one-unreadable-epic-is-loud-but-the-rest-of-the-pass-runs",
              (code, st["questions"]["%s:x" % CHAN]["status"],
               "the one piece of prod-60" in " ".join(posted(s)).lower()),
              (EXIT_ERROR, EXPIRED, True))
        for _ in range(3):
            run(c, s, t)
        check("an-epic-unreadable-three-passes-is-said-once",
              sum("I can't read PROD-50" in p for p in posted(s)), 1)
        t.epic = real_epic

        # 4 (MEDIUM): an epic moved off the bridge's teams is let go, by id, and said.
        c, s, t, root = approved("moved-team", kids=(("PROD-51", ()), ("PROD-52", ())))
        for k in ("PROD-50", "PROD-51", "PROD-52"):
            t.issues[k]["team"] = {"key": "OPS"}
        t.set_state("PROD-51", "completed")
        run(c, s, t)
        check("an-epic-moved-off-the-teams-is-let-go",
              (delegated(t), any("now on OPS" in p for p in posted(s)), followed(c)),
              (["uuid-51"], True, []))

        # …and it is followed by its id, so a new identifier (a team move renames it) does
        # not lose it.
        c, s, t, root = approved("renamed", kids=(("PROD-51", ()), ("PROD-52", ())))
        t.issues["PROD-77"] = t.issues.pop("PROD-50")
        t.issues["PROD-77"]["identifier"] = "PROD-77"
        t.epics["PROD-77"] = t.epics.pop("PROD-50")
        t.set_state("PROD-51", "completed")
        run(c, s, t)
        check("an-epic-is-followed-by-its-id", (delegated(t), followed(c)),
              (["uuid-51", "uuid-52"], ["uuid-50"]))

        # 5 (MEDIUM): many children added later are said once, together, and never again.
        c, s, t, root = approved("many-added", kids=(("PROD-51", ()),))
        for n in range(70, 92):
            k = "PROD-%d" % n
            t.issues[k] = dict(t.issues["PROD-51"], id="uuid-%d" % n, identifier=k,
                               title="Added", blocked_by=[], labels=[], delegate=None)
            t.epics["PROD-50"].append(k)
        before = len(s.posts)
        for _ in range(3):
            run(c, s, t)
        check("many-added-children-are-said-once-together", len(s.posts) - before, 1)

        # 6: the orchestrator and graphite labels make the dispatcher hand out or stack
        # children itself, so an epic carrying them anywhere is refused.
        for where in ("epic", "child"):
            c, s, t = world("mode-label-" + where, act=True, agent_user_name="Agent")
            t.add_epic("PROD-50", (("PROD-51", ()),))
            t.issues["PROD-50" if where == "epic" else "PROD-51"]["labels"].append("Graphite")
            run(c, s, t)
            s.add(ALICE, "approve PROD-50")
            run(c, s, t)
            check("a-dispatcher-mode-label-is-refused:" + where,
                  ("`Graphite`" in posted(s)[-1], t.moves), (True, []))

        # The approve question credits a bot in words too, never with a mention.
        c, s, t = world("approve-bot-credit", act=True, agent_user_name="Agent",
                        chat_bot_user_id=CHATBOT)
        t.add_epic("PROD-50", (("PROD-51", ()),))
        run(c, s, t)
        s.add(CHATBOT, "approve PROD-50", bot_id="B0CHATBOT1")
        run(c, s, t)
        check("an-approve-asked-by-a-bot-is-credited-in-words-never-mentioned",
              ("Asked for by the tech lead" in posted(s)[-1], "<@%s>" % CHATBOT in posted(s)[-1]),
              (True, False))

        # REVIEW OF KIT-223 (2026-10-10, second review) ----------------------------------
        # 1: a yes binds to the terms the approve question stated. Acting switched on, or
        # another limit, ready state or agent user since it was asked: nothing acts.
        for name, asked, answered in (
                ("acting-switched-on", {}, {"act": True}),
                ("epic-wip-raised", {"act": True}, {"act": True, "epic_wip": 3}),
                ("ready-state-renamed", {"act": True}, {"act": True, "ready_state": "Ready"}),
                ("agent-user-renamed", {"act": True}, {"act": True,
                                                       "agent_user_name": "Other"})):
            c, s, t = world("approve-promise-" + name, agent_user_name="Agent", **asked)
            t.add_epic("PROD-50", (("PROD-51", ()), ("PROD-52", ("PROD-51",))))
            run(c, s, t)
            root = s.add(ALICE, "approve PROD-50")
            run(c, s, t)
            s.add(BOB, "yes", thread=root)
            run(dict(c, **answered), s, t)
            check("an-approve-yes-under-other-settings-than-asked-acts-on-nothing:" + name,
                  (t.moves, t.delegations, followed(c), status_of(c),
                   "settings changed since" in posted(s)[-1]), ([], [], [], [REFUSED], True))

        # 2: every re-check of what the idea gate filed is pinned, before asking...
        for name, change, needle in (
                ("a-child-on-another-team", lambda t_: t_.issues["PROD-51"].update(
                    team={"key": "OPS"}), "PROD-51 is on another team"),
                ("a-child-with-children", lambda t_: t_.epics.__setitem__("PROD-51",
                                                                          ["PROD-6"]),
                 "PROD-51 has pieces of its own"),
                ("an-epic-with-a-parent", lambda t_: t_.issues["PROD-50"].update(
                    parent={"id": "uuid-6"}), "it has a parent ticket"),
                ("a-finished-epic", lambda t_: t_.set_state("PROD-50", "canceled"),
                 "it is Canceled"),
                ("more-children-than-it-reads", lambda t_: t_.overflow.add("PROD-50"),
                 "more than %d pieces" % MAX_CHILDREN)):
            c, s, t = world("approve-refused-" + name, act=True, agent_user_name="Agent")
            t.add_epic("PROD-50", (("PROD-51", ()), ("PROD-52", ())))
            change(t)
            run(c, s, t)
            s.add(ALICE, "approve PROD-50")
            run(c, s, t)
            check("refused-before-asking:" + name,
                  (needle in posted(s)[-1], json.load(open(state_path(c)))["questions"]),
                  (True, {}))
        # ...at the yes...
        for name, change, needle in (
                ("the-epic-handed-over", lambda t_: t_.issues["PROD-50"].update(
                    delegate={"id": "someone"}), "handed to the agent itself"),
                ("the-epic-relabelled", lambda t_: t_.issues["PROD-50"]["labels"].append(
                    "Graphite"), "`Graphite`")):
            c, s, t = world("approve-yes-refused-" + name, act=True, agent_user_name="Agent")
            t.add_epic("PROD-50", (("PROD-51", ()), ("PROD-52", ())))
            run(c, s, t)
            root = s.add(ALICE, "approve PROD-50")
            run(c, s, t)
            change(t)
            s.add(BOB, "yes", thread=root)
            run(c, s, t)
            check("refused-at-the-yes:" + name,
                  (t.moves, t.delegations, followed(c), "can't approve" in posted(s)[-1],
                   needle in posted(s)[-1]), ([], [], [], True, True))
        # ...and on every pass after it.
        c, s, t, root = approved("child-to-another-team", kids=(("PROD-51", ()),
                                                                ("PROD-52", ("PROD-51",))))
        t.issues["PROD-52"]["team"] = {"key": "OPS"}
        t.set_state("PROD-51", "completed")
        run(c, s, t)
        check("a-child-moved-to-another-team-after-the-yes-pauses-the-epic",
              (delegated(t), any("PROD-52 is on another team" in p for p in posted(s))),
              (["uuid-51"], True))
        c, s, t, root = approved("epic-canceled", kids=(("PROD-51", ()), ("PROD-52", ())))
        t.set_state("PROD-50", "canceled")
        t.set_state("PROD-51", "completed")
        run(c, s, t)
        check("a-canceled-epic-is-let-go-for-good",
              (delegated(t), followed(c),
               any("PROD-50 is Canceled, so I've stopped" in p for p in posted(s))),
              (["uuid-51"], [], True))

        # KIT-341: WHO MADE IT. An epic counts as the idea gate's only when the owner's key
        # filed it, and a label the agent's own account added refuses the plan, at the
        # question and at the yes. A move to Backlog or Canceled is his pause or stop only
        # when his key made it; anyone else's holds the pieces and says so.
        def refused_approve(name, setup):
            c, s, t = world(name, act=True, agent_user_name="Agent")
            setup(t)
            run(c, s, t)
            s.add(ALICE, "approve PROD-50")
            run(c, s, t)
            return (posted(s)[-1], json.load(open(state_path(c)))["questions"], t.moves,
                    t.delegations)
        got = refused_approve("another-key", lambda t_: t_.add_epic(
            "PROD-50", (("PROD-51", ()),), creator="someone-else"))
        check("an-epic-filed-with-another-key-is-refused",
              ("it was not filed with your key" in got[0],) + got[1:], (True, {}, [], []))

        # A piece the agent's account filed, its label on from the start, so no history
        # entry names who set it: refused, as the epic is (review of #281).
        def agents_piece(t_):
            t_.add_epic("PROD-50", (("PROD-51", ()), ("PROD-52", ())))
            t_.issues["PROD-52"]["creator"] = {"id": "agent-1", "displayName": "Agent"}
        got = refused_approve("agents-piece", agents_piece)
        check("a-piece-the-agents-account-filed-with-its-label-on-is-refused",
              ("PROD-52 was not filed with your key, so the idea gate did not file it"
               in got[0],) + got[1:], (True, {}, [], []))

        def agents_label(t_):
            t_.add_epic("PROD-50", (("PROD-51", ()),), kid_label=None)
            t_.add_label("PROD-51", "provenance:epic", actor="agent-1")
        def your_label(t_):
            t_.add_epic("PROD-50", (("PROD-51", ()),), kid_label=None)
            t_.add_label("PROD-51", "provenance:epic", actor=OWNER)
        got = refused_approve("your-label", your_label)
        check("a-label-your-key-added-is-trusted",
              (got[0].startswith("*Approve this plan?*"), len(got[1])), (True, 1))
        got = refused_approve("agents-label", agents_label)
        check("a-label-the-agents-account-added-is-refused",
              ("PROD-51's `provenance:epic` was added by the agent's own account" in got[0],)
              + got[1:], (True, {}, [], []))

        def case_label_after_the_question():
            c, s, t = world("agents-label-later", act=True, agent_user_name="Agent")
            t.add_epic("PROD-50", (("PROD-51", ()),))
            run(c, s, t)
            root = s.add(ALICE, "approve PROD-50")
            run(c, s, t)
            t.add_label("PROD-50", "urgent", actor="agent-1")
            s.add(BOB, "yes", thread=root)
            run(c, s, t)
            return ("PROD-50's `urgent` was added by the agent's own account" in posted(s)[-1],
                    t.moves, t.delegations)
        check("a-label-the-agents-account-added-after-the-question-refuses-the-yes",
              case_label_after_the_question(), (True, [], []))

        c, s, t, root = approved("held-backlog", kids=(("PROD-51", ()), ("PROD-52", ())))
        t.set_state("PROD-50", "backlog", actor="someone-else")
        t.set_state("PROD-51", "completed")
        run(c, s, t)
        run(c, s, t)
        held = ("PROD-50 was moved to Backlog, but not by you, so I've held its pieces. Move "
                "it back to Todo and they go on, or move it to Backlog yourself to pause them.")
        check("a-move-to-backlog-by-someone-else-holds-and-says-so-once",
              (delegated(t), sum(held in p for p in posted(s)),
               any("paused starting" in p for p in posted(s))), (["uuid-51"], 1, False))
        t.set_state("PROD-50", "unstarted")
        run(c, s, t)
        check("and-goes-on-when-it-moves-forward-again", delegated(t), ["uuid-51", "uuid-52"])

        c, s, t, root = approved("held-cancel", kids=(("PROD-51", ()), ("PROD-52", ())))
        t.set_state("PROD-50", "canceled", actor="someone-else")
        t.set_state("PROD-51", "completed")
        run(c, s, t)
        check("a-cancel-by-someone-else-holds-and-is-never-let-go",
              (delegated(t), followed(c), any(
                  "PROD-50 was moved to Canceled, but not by you, so I've held its pieces "
                  "rather than stop following it." in p for p in posted(s))),
              (["uuid-51"], ["uuid-50"], True))
        t.set_state("PROD-50", "unstarted")
        run(c, s, t)
        t.set_state("PROD-50", "canceled")
        run(c, s, t)
        check("and-his-own-cancel-then-lets-it-go", (followed(c), any(
            "PROD-50 is Canceled, so I've stopped" in p for p in posted(s))), ([], True))
        # His own cancel made between two passes, after someone else's: held until the
        # history is read again, then it lets go (review of #281).
        c, s, t, root = approved("held-recancel", kids=(("PROD-51", ()), ("PROD-52", ())))
        t.set_state("PROD-50", "canceled", actor="someone-else")
        run(c, s, t, at=now + 60)
        t.set_state("PROD-50", "unstarted")
        t.set_state("PROD-50", "canceled")
        run(c, s, t, at=now + 120)
        soon = followed(c)
        run(c, s, t, at=now + 60 + HELD_RECHECK_SECONDS)
        check("his-own-cancel-between-two-passes-counts-once-the-history-is-read-again",
              (soon, followed(c), sum("PROD-50 is Canceled, so I've stopped" in p
                                      for p in posted(s))), (["uuid-50"], [], 1))

        # A history the tracker would not give refuses, never approves: at the question, at
        # the yes, and while a plan runs, where it is a failed read, never a pause or a drop
        # (review of #281).
        def history_fails(name, when):
            c, s, t = world(name, act=True, agent_user_name="Agent")
            t.add_epic("PROD-50", (("PROD-51", ()), ("PROD-52", ("PROD-51",))))
            run(c, s, t)
            broken = BridgeError("the history could not be read (simulated)")
            t.history_raises = broken if when == "question" else None
            root = s.add(ALICE, "approve PROD-50")
            code, _o = run(c, s, t)
            if when != "question":
                t.history_raises = broken if when == "yes" else None
                s.add(BOB, "yes", thread=root)
                code, _o = run(c, s, t)
            if when == "release":
                t.history_raises = broken
                t.set_state("PROD-50", "backlog", actor="someone-else")
                code, _o = run(c, s, t)
            st = json.load(open(state_path(c)))
            return (code, sorted(q.get("status") for q in st["questions"].values()), t.moves,
                    delegated(t), followed(c),
                    any("paused" in p or "held its pieces" in p or "stopped" in p
                        for p in posted(s)))
        check("a-history-it-cannot-read-refuses-at-the-question-the-yes-and-the-release",
              [history_fails("history-" + w, w) for w in ("question", "yes", "release")],
              [(EXIT_ERROR, [], [], [], [], False),
               (EXIT_ERROR, [OPEN], [], [], [], False),
               (EXIT_ERROR, [DONE], [("uuid-50", "st-todo")], ["uuid-51"], ["uuid-50"],
                False)])
        # The order: a canceled blocker is not a Done one; a page of blockers that runs over
        # is never read as complete.
        c, s, t, root = approved("blocker-canceled", kids=(("PROD-51", ()),
                                                           ("PROD-52", ("PROD-51",))))
        t.set_state("PROD-51", "canceled")
        run(c, s, t)
        check("a-canceled-blocker-holds-what-waits-on-it",
              (delegated(t), any("PROD-52 waits on PROD-51 (Canceled)" in p
                                 for p in posted(s))), (["uuid-51"], True))
        c, s, t, root = approved("blocker-overflow", wip=2, kids=(("PROD-51", ()),
                                                                  ("PROD-52", ())),
                                 setup=lambda t_: t_.blocker_overflow.add("PROD-52"))
        check("more-blockers-than-it-reads-never-starts",
              (delegated(t), open_blockers(t.epic("PROD-50")["children"]["nodes"][1])),
              (["uuid-51"], ["more blockers than the bridge reads"]))
        # The limit: a child someone moved to a started state holds its slot, though no one
        # handed it over.
        c, s, t, root = approved("started-by-hand", kids=(("PROD-51", ()), ("PROD-52", ())),
                                 setup=lambda t_: t_.set_state("PROD-51", "started"))
        check("a-started-child-holds-the-limit-and-is-never-handed-over", delegated(t), [])
        # An epic already in progress is approved where it is, never moved back to Todo.
        c, s, t, root = approved("epic-in-progress", kids=(("PROD-51", ()),),
                                 setup=lambda t_: t_.set_state("PROD-50", "started"))
        check("an-epic-in-a-started-state-is-not-moved-back", (t.moves, delegated(t)),
              ([], ["uuid-51"]))

        # 3: a failed attribution comment is owed, said, retried every pass, and loud.
        c, s, t = world("approve-comment-fails", act=True, agent_user_name="Agent")
        t.add_epic("PROD-50", (("PROD-51", ()), ("PROD-52", ("PROD-51",))))
        run(c, s, t)
        root = s.add(ALICE, "approve PROD-50")
        run(c, s, t)
        real_comment = t.comment
        t.comment = refusing_comment
        s.add(BOB, "yes", thread=root)
        codes = [run(c, s, t)[0], run(c, s, t)[0]]
        owed = json.load(open(state_path(c))).get("owed")
        t.comment = real_comment
        codes += [run(c, s, t)[0], run(c, s, t)[0]]
        check("a-failed-approve-or-start-comment-is-owed-retried-and-loud-until-it-lands",
              (t.moves, delegated(t), codes,
               sum("could not be written; I will try again" in x for x in posted(s)),
               sorted(o.get("ticket") for o in owed or []),
               sorted(i for i, _b in t.comments),
               json.load(open(state_path(c))).get("owed")),
              ([("uuid-50", "st-todo")], ["uuid-51"],
               [EXIT_ERROR, EXIT_ERROR, EXIT_OK, EXIT_OK], 2, ["PROD-50", "PROD-51"],
               ["uuid-50", "uuid-51"], []))

        # 4: a session can mark its own child Done while it still runs. The child holds its
        # slot until the session ends.
        c, s, t, root = approved("done-but-running", kids=(("PROD-51", ()),
                                                           ("PROD-52", ("PROD-51",))))
        t.sessions["uuid-51"] = [{"id": "s-51", "status": "active"}]
        t.set_state("PROD-51", "completed")
        run(c, s, t)
        held = delegated(t)
        t.sessions["uuid-51"] = [{"id": "s-51", "status": "complete"}]
        run(c, s, t)
        check("a-child-done-while-its-session-runs-holds-the-slot-until-it-ends",
              (held, delegated(t)), (["uuid-51"], ["uuid-51", "uuid-52"]))
        check("and-that-hold-is-said-once",
              sum("PROD-51 is marked finished, but its dev is still working on it" in p
                  for p in posted(s)), 1)

        # KIT-290: a finished child whose open sessions are all prompts the agent never
        # picked up holds its slot the same, but is never said to be worked on. One the agent
        # works, or one waiting on you, keeps the note above word for word.
        def done_with(name, statuses, kids=(("PROD-51", ()), ("PROD-52", ("PROD-51",)))):
            c, s, t, root = approved(name, kids=kids)
            t.sessions["uuid-51"] = [{"id": "s-51" + "abc"[j], "status": st}
                                     for j, st in enumerate(statuses)]
            t.set_state("PROD-51", "completed")
            run(c, s, t)
            return c, s, t

        def held_notes(s):
            return [p for p in posted(s) if p.startswith("PROD-51 is marked finished")]
        unpicked = ("PROD-51 is marked finished, but the agent hasn't picked up a prompt on "
                    "it. It counts toward the limit of 1 until that prompt ends. I can't tell "
                    "when that will be: the agent may still pick it up.")
        working = ("PROD-51 is marked finished, but its dev is still working on it. It "
                   "counts toward the limit of 1 until the dev stops.")
        shown = (["pending"], ["complete", "pending"], ["pending", "pending"])
        kept = (["active"], ["awaitingInput"], ["pending", "active"],
                ["pending", "awaitingInput"], [None])
        said = []
        for i, statuses in enumerate(shown + kept):
            c, s, t = done_with("done-open-%d" % i, statuses)
            said.append((statuses, delegated(t), held_notes(s)))
        check("a-done-child-is-said-as-its-open-sessions-show-and-holds-its-slot-either-way",
              said, [(st, ["uuid-51"], [unpicked]) for st in shown]
              + [(st, ["uuid-51"], [working]) for st in kept])
        c, s, t = done_with("done-picked-up-late", ["pending"])
        t.sessions["uuid-51"] = [{"id": "s-51a", "status": "active"}]
        run(c, s, t)
        run(c, s, t)
        held = delegated(t)
        t.sessions["uuid-51"] = [{"id": "s-51a", "status": "stale"}]
        run(c, s, t)
        check("a-prompt-picked-up-late-is-said-as-worked-on-each-once-and-its-end-frees-the-slot",
              (held_notes(s), held, delegated(t)),
              ([unpicked, working], ["uuid-51"], ["uuid-51", "uuid-52"]))
        closing = {}
        for statuses in (["complete", "pending"], ["active"]):
            c, s, t = done_with("close-held-" + statuses[-1], statuses, kids=(("PROD-51", ()),))
            run(c, s, t)
            held = followed(c)
            t.sessions["uuid-51"] = [{"id": "s-51a", "status": "complete"}]
            run(c, s, t)
            closing[statuses[-1]] = (held_notes(s), held, followed(c))
        check("a-finished-plan-held-by-an-open-prompt-says-it-as-its-sessions-show",
              closing,
              {"pending": (["PROD-51 is marked finished, but the agent hasn't picked up a "
                            "prompt on it. I'll mark PROD-50 Done once that prompt ends. I "
                            "can't tell when that will be: the agent may still pick it up."],
                           ["uuid-50"], []),
               "active": (["PROD-51 is marked finished, but its dev is still working on it. "
                           "I'll mark PROD-50 Done once the dev stops."], ["uuid-50"], [])})

        # 5: pins the second review's mutants left standing. An approval said only after
        # its post lands: a failed "Approved" post is said on the next pass, and nothing is
        # recorded, moved or commented twice.
        c, s, t = world("approve-post-fails", act=True, agent_user_name="Agent")
        t.add_epic("PROD-50", (("PROD-51", ()), ("PROD-52", ("PROD-51",))))
        run(c, s, t)
        root = s.add(ALICE, "approve PROD-50")
        run(c, s, t)
        real_post = s.post

        def approved_post_fails(channel, thread_ts, text, real_post=real_post):
            if text.startswith("Approved PROD-50"):
                raise BridgeError("Slack chat.postMessage failed (simulated)")
            return real_post(channel, thread_ts, text)
        s.post = approved_post_fails
        s.add(BOB, "yes", thread=root)
        code, _o = run(c, s, t)
        s.post = real_post
        run(c, s, t)
        check("a-failed-approved-post-is-said-next-pass-and-nothing-twice",
              (code, t.moves, delegated(t), len([i for i, _b in t.comments if i == "uuid-50"]),
               status_of(c), "PROD-50 is already approved." in posted(s)),
              (EXIT_ERROR, [("uuid-50", "st-todo")], ["uuid-51"], 1, [DONE], True))
        # An epic read that answers with another ticket is never followed as this one.
        c, s, t, root = approved("epic-id-mismatch", kids=(("PROD-51", ()), ("PROD-52", ())))
        real_epic = t.epic
        t.epic = lambda ident: dict(real_epic(ident), id="uuid-someone-else")
        t.set_state("PROD-51", "completed")
        run(c, s, t)
        t.epic = real_epic
        check("an-epic-read-that-names-another-ticket-is-let-go",
              (delegated(t), followed(c), any("no longer in Linear" in p
                                              for p in posted(s))), (["uuid-51"], [], True))
        # A credential an unreadable epic's error echoes is redacted before it is cut short.
        key = "lin_api_" + "3" * 30
        os.environ["STAGE_E_LINEAR_API_KEY"] = key
        try:
            c, s, t, root = approved("epic-error-leaks", kids=(("PROD-51", ()),))

            def leaky_epic(_ident):
                # Padded so the key straddles where the error is cut short.
                raise BridgeError("%s Authorization: %s" % ("x" * 130, key))
            t.epic = leaky_epic
            outs = [run(c, s, t)[1] for _ in range(3)]
            check("an-unreadable-epics-error-is-redacted-before-it-is-cut",
                  (any("I can't read PROD-50" in p for p in posted(s)),
                   any(key[:10] in x for x in posted(s) + outs)), (True, False))
        finally:
            os.environ.pop("STAGE_E_LINEAR_API_KEY", None)

        # 11f. THE QUEUE SAYS WHAT STARTS (MVP 2, KIT-454) ------------------------------------
        # The starter's decide is stood in for; its ledger is the real one, in a folder here.
        def queue_at(name, seed=None):
            folder = os.path.join(tmp, "queue-" + name)
            os.makedirs(folder)
            ledger = os.path.join(folder, "ledger.json")
            if seed is not None:
                with open(ledger, "w", encoding="utf-8") as fh:
                    json.dump(seed, fh)
            conf = os.path.join(folder, "queue.json")
            with open(os.path.join(folder, "usage.json"), "w", encoding="utf-8") as fh:
                json.dump({"schema": "pipeline-usage/1", "written_at": iso(now + 1000 - 300),
                           "result": "ok", "limit_stops": [], "limit_stops_over_cap": 0}, fh)
            with open(conf, "w", encoding="utf-8") as fh:
                json.dump({"pool": {"owner_user_ids": ["owner-1"], "team_keys": ["PROD"],
                                    "dispatcher_user_ids": ["agent-1"]},
                           "reader": {"ledger_file": ledger,
                                      "inputs_file": os.path.join(folder, "in.json")},
                           "morning": {"usage_file": os.path.join(folder, "usage.json")}}, fh)
            return conf, ledger

        def start(ident, source="plan", lane="acme/app:1"):
            return {"ticket": ident, "id": "uuid-" + ident.split("-")[1], "repo": "acme/app",
                    "lane": lane, "slot": 1, "estimate": 2.0, "priority": 0, "source": source,
                    "plan": "PROD-50" if source == "plan" else None, "by": "owner-1",
                    "at": "2025-10-09T06:00:00Z"}

        def decided(*starts):
            return {"state": "decided", "why": None, "window": {}, "inputs": None,
                    "decision": {"start": list(starts), "held": [], "stops": []}}
        answer, asks, order_seen = {}, [], []
        real = (starter.decide, starter.record_start)

        def fake_decide(raw, gql, now, clock=None, say=None):
            asks.append(now)
            return answer["v"]

        def spy_record(path, now_, ticket, source):
            order_seen.append((ticket, len(active[0].delegations)))
            return real[1](path, now_, ticket, source)
        active = [None]
        starter.decide, starter.record_start = fake_decide, spy_record
        try:
            def queued(name, v, seed=None, kids=(("PROD-51", ()), ("PROD-52", ())), setup=None,
                       usage=None, **over):
                conf, ledger = queue_at(name, seed)
                if usage is False:
                    os.unlink(os.path.join(os.path.dirname(conf), "usage.json"))
                elif usage is not None:
                    with open(os.path.join(os.path.dirname(conf), "usage.json"), "w",
                              encoding="utf-8") as fh:
                        json.dump(usage, fh)
                answer["v"] = {"state": "closed", "why": "before-start", "decision": None}
                c, s, t, root = approved(name, wip=2, kids=kids, queue_config=conf, **over)
                active[0] = t
                if setup:
                    setup(t)
                answer["v"] = v
                del asks[:], order_seen[:]
                out = run(c, s, t, at=now + 1000)
                return c, s, t, out, ledger

            def rows(ledger):
                if not os.path.exists(ledger):
                    return None
                return [(r["ticket"], r["source"]) for r in json.load(open(ledger))["rows"]]
            c, s, t, out, ledger = queued("q-closed", {"state": "closed", "why": "before-start",
                                                       "decision": None})
            check("with-the-queue-on-a-closed-window-starts-nothing-even-an-approved-plan",
                  (delegated(t), rows(ledger), out[0]), ([], None, EXIT_OK))
            c, s, t, out, ledger = queued("q-starts", decided(
                start("PROD-51"), start("PROD-7", "todo", "acme/app:2")))
            comments = dict(t.comments)
            check("the-queue-starts-its-list-in-order-the-ledger-row-before-each-hand-over",
                  (delegated(t), rows(ledger), order_seen, out[0]),
                  (["uuid-51", "uuid-7"], [("PROD-51", "plan"), ("PROD-7", "todo")],
                   [("PROD-51", 0), ("PROD-7", 1)], EXIT_OK))
            check("a-plan-piece-names-its-approver-another-ticket-gets-the-queues-words",
                  (comments["uuid-51"].startswith("Started by the Slack bridge for **"),
                   comments["uuid-7"], sum("Started PROD-51" in p for p in posted(s))),
                  (True, "Started by the queue, in lane acme/app:2: you moved it into the ready "
                   "state at 2025-10-09 06:00 UTC.", 1))
            del asks[:]
            run(c, s, t, at=now + 1100)
            check("the-queue-is-asked-at-most-every-queue-every-seconds",
                  (len(asks), delegated(t)), (0, ["uuid-51", "uuid-7"]))
            c, s, t, out, ledger = queued("q-unread", {
                "state": "unread", "why": "the tracker did not answer", "decision": None})
            check("a-failed-read-starts-nothing-and-the-pass-ends-loud",
                  (delegated(t), out[0] != EXIT_OK, "the queue: the tracker could not be read"
                   in out[1]), ([], True, True))
            def short(*lines):
                names = {"disk": "free disk 18.2 GB is under the 25 GB line",
                         "swap": "swap in use 5.0 GB is over the 4 GB line"}
                fields = {"disk": {"line": "disk", "reading": 18.2, "limit": 25, "unit": "GB"},
                          "swap": {"line": "swap", "reading": 5.0, "limit": 4, "unit": "GB"}}
                return {"state": "no-room", "lines": list(lines), "decision": None,
                        "why": "; ".join(names[n] for n in lines),
                        "room": [fields[n] for n in lines]}

            def room_story(name):
                """Four passes: short on disk, still short, short on swap too, room again
                (one start), then the next pass. (exit codes, starts, loud, resumed) of each."""
                c_, s_, t_, out_, ledger_ = queued(name, short("disk"))
                seen_ = [out_]
                for k, v in ((1, short("disk")), (2, short("disk", "swap")),
                             (3, decided(start("PROD-51"))), (4, decided())):
                    answer["v"] = v
                    with open(os.path.join(os.path.dirname(ledger_), "usage.json"), "w") as fh:
                        json.dump({"schema": "pipeline-usage/1", "result": "ok",
                                   "written_at": iso(now + 1000 + 1000 * k - 300),
                                   "limit_stops": [], "limit_stops_over_cap": 0}, fh)
                    seen_.append(run(c_, s_, t_, at=now + 1000 + 1000 * k))
                    seen_[-1] = (seen_[-1][0], seen_[-1][1])
                return [(o_[0] != EXIT_OK, "the queue: the machine is short of room" in o_[1],
                         "still short of room" in o_[1], o_[1].count("room again: starts resume"))
                        for o_ in seen_], delegated(t_)
            story = room_story("q-room")
            check("a-machine-short-of-room-starts-nothing-and-says-so-once-per-change",
                  story, ([(True, True, False, 0), (False, False, True, 0),
                           (True, True, False, 0), (False, False, False, 1),
                           (False, False, False, 0)], ["uuid-51"]))
            check("no-room-names-its-reading-in-the-pass-and-writes-no-ledger",
                  (queued("q-room2", short("disk"))[3][1].count("free disk 18.2 GB is under the "
                                                                "25 GB line") >= 1,
                   rows(queued("q-room3", short("swap"))[4])), (True, None))
            # KIT-474: the room hold as a fields-only record beside the heartbeat, for the status
            # file and the stand-up: held with its lines and since when, clear with when it
            # resumed, untouched by a closed window, and a write that fails ends the pass loud.
            def room_record(c_):
                path = os.path.join(c_["state_dir"], ROOM_FILE)
                return json.load(open(path)) if os.path.exists(path) else None

            def room_story2(name):
                c_, s_, t_, out_, ledger_ = queued(name, short("disk"))
                seen_ = [room_record(c_)]
                for k, v in ((1, short("disk")), (2, short("disk", "swap")),
                             (3, {"state": "unread", "why": "x", "decision": None}),
                             (4, decided()),
                             (5, {"state": "closed", "why": "before-start", "decision": None})):
                    answer["v"] = v
                    with open(os.path.join(os.path.dirname(ledger_), "usage.json"), "w") as fh:
                        json.dump({"schema": "pipeline-usage/1", "result": "ok",
                                   "written_at": iso(now + 1000 + 1000 * k - 300),
                                   "limit_stops": [], "limit_stops_over_cap": 0}, fh)
                    run(c_, s_, t_, at=now + 1000 + 1000 * k)
                    seen_.append(room_record(c_))
                return seen_
            held_disk = [{"line": "disk", "reading": 18.2, "limit": 25, "unit": "GB"}]
            held_both = held_disk + [{"line": "swap", "reading": 5.0, "limit": 4, "unit": "GB"}]

            def rec(state, since, resumed, lines, at):
                return {"schema": "pipeline-queue-room/1", "written_at": iso(now + at),
                        "state": state, "since": since and iso(now + since),
                        "resumed_at": resumed and iso(now + resumed), "lines": lines}
            check("the-queue-writes-its-room-hold-as-fields-keeps-since-and-says-when-it-resumed",
                  room_story2("q-room-rec"),
                  [rec("held", 1000, None, held_disk, 1000),       # held on the first pass
                   rec("held", 1000, None, held_disk, 2000),       # still held: since stays
                   rec("held", 1000, None, held_both, 3000),       # another line over, same hold
                   rec("clear", None, 4000, [], 4000),   # room again, though the tracker was
                                                         # unread: resumed (Stage E on #420)
                   rec("clear", None, 4000, [], 5000),             # the time stays
                   rec("clear", None, 4000, [], 5000)])            # a closed window leaves it
            c, s, t, out, ledger = queued("q-room-closed", {"state": "closed",
                                                            "why": "before-start",
                                                            "decision": None})
            check("a-closed-window-writes-no-room-record-before-the-room-was-read",
                  room_record(c), None)
            conf_d, ledger_d = queue_at("q-room-dry")
            answer["v"] = {"state": "closed", "why": "before-start", "decision": None}
            c, s, t, root = approved("q-room-dry", wip=2, queue_config=conf_d,
                                     kids=(("PROD-51", ()), ("PROD-52", ())))
            active[0], answer["v"] = t, short("disk")
            run(c, s, t, dry=True, at=now + 1000)
            check("a-dry-run-writes-no-room-record", room_record(c), None)
            conf_f, ledger_f = queue_at("q-room-fail")
            answer["v"] = {"state": "closed", "why": "before-start", "decision": None}
            c, s, t, root = approved("q-room-fail", wip=2, queue_config=conf_f,
                                     kids=(("PROD-51", ()), ("PROD-52", ())))
            active[0], answer["v"] = t, short("disk")
            os.mkdir(os.path.join(c["state_dir"], ROOM_FILE))        # a write that cannot land
            out_f = run(c, s, t, at=now + 1000)
            check("a-room-record-that-cannot-be-written-ends-the-pass-loud",
                  (out_f[0] != EXIT_OK, "the queue's room record could not be written" in out_f[1]),
                  (True, True))
            c, s, t, out, ledger = queued("q-ahead", decided(start("PROD-51")), seed={
                "date": "2099-01-01", "starts": 0, "agent_filed_starts": 0})
            check("a-ledger-that-refuses-starts-nothing-and-the-pass-ends-loud",
                  (delegated(t), out[0] != EXIT_OK, "its ledger refused a start" in out[1]),
                  ([], True, True))
            c, s, t, out, ledger = queued("q-limit", decided(
                start("PROD-51"), start("PROD-52", lane="acme/app:2")), max_actions_per_hour=2)
            check("the-action-limit-holds-the-queue-too", (delegated(t), rows(ledger)),
                  (["uuid-51"], [("PROD-51", "plan")]))
            conf, ledger = queue_at("q-dry")
            answer["v"] = {"state": "closed", "why": "before-start", "decision": None}
            c, s, t, root = approved("q-dry", wip=2, queue_config=conf,
                                     kids=(("PROD-51", ()), ("PROD-52", ())))
            active[0], answer["v"] = t, decided(start("PROD-51"))
            run(c, s, t, dry=True, at=now + 1000)
            check("a-dry-run-hands-nothing-and-writes-no-ledger", (delegated(t), rows(ledger)),
                  ([], None))

            # 11g. A PIECE THE PLAN'S LIMIT STOPPED IS HANDED OVER ONCE MORE (KIT-472) ----------
            at_ = now + 1000

            def when(secs):
                return iso(at_ + secs)

            def stop_of(ident="PROD-51", ended=-7200, resets=-3600, **more):
                return dict({"ticket": ident, "kind": "coding", "window": "five_hour",
                             "ended_at": when(ended),
                             "resets_at": None if resets is None else when(resets)}, **more)

            def usage_of(*stops, **over):
                return dict({"schema": "pipeline-usage/1", "written_at": when(-300),
                             "result": "ok", "limit_stops": list(stops),
                             "limit_stops_over_cap": 0}, **over)

            def stopped(t, sessions=None, delegate="agent-1", making=True):
                """PROD-51 the agent's, its one session ended by the limit, and Linear making a
                new session on a real change of delegate, as the retry's own cases do."""
                t.issues["PROD-51"]["delegate"] = {"id": delegate}
                t.sessions["uuid-51"] = sessions if sessions is not None else [
                    {"id": "s-old", "status": "error", "createdAt": when(-9000)[:-1] + ".000Z"}]
                real_delegate = t.delegate

                def delegate_(issue_id, agent_id):
                    held_by = (t.issues["PROD-51"].get("delegate") or {}).get("id")
                    if making is not False or not agent_id:
                        real_delegate(issue_id, agent_id)
                    else:
                        t.delegations.append((issue_id, agent_id))
                        raise BridgeError("the tracker would not take it (simulated)")
                    if agent_id and agent_id != held_by and making:
                        t.sessions.setdefault(issue_id, []).append(
                            {"id": "s-new", "status": "pending",
                             "createdAt": when(0)[:-1] + ".000Z"})
                t.delegate = delegate_

            def resumed(name, usage, v=None, setup=stopped, **kw):
                c_, s_, t_, out_, ledger_ = queued(name, v or decided(), usage=usage,
                                                    setup=setup, **kw)
                return c_, s_, t_, out_, ledger_

            def resume_scene(name, usage, v=None, setup=stopped):
                c_, s_, t_, out_, ledger_ = resumed(name, usage, v, setup)
                return (t_.delegations, rows(ledger_), out_[0] != EXIT_OK, out_[1], t_.comments)
            RESUME_WORDS = ("Handed to the agent again by the queue: the plan's five_hour limit "
                            "stopped it at %s, and that window reset at %s." % (
                                when(-7200).replace("T", " ")[:16] + " UTC",
                                when(-3600).replace("T", " ")[:16] + " UTC"))
            got = resume_scene("q-resume", usage_of(stop_of()))
            check("a-piece-the-limit-stopped-is-taken-off-and-put-back-once-the-window-reset",
                  (got[0], got[1], got[2], [b for i, b in got[4] if i == "uuid-51"]),
                  ([("uuid-51", None), ("uuid-51", "agent-1")], [("PROD-51", "resume")], False,
                   [RESUME_WORDS]))
            c, s, t, out, ledger = resumed("q-resume-twice", usage_of(stop_of()))
            for k in (1, 2):
                answer["v"] = decided()
                run(c, s, t, at=at_ + 1000 * k)
            check("each-stop-resumes-once-and-the-ledger-counts-it-once",
                  (t.delegations, rows(ledger), len(rows(ledger) or [])),
                  ([("uuid-51", None), ("uuid-51", "agent-1")], [("PROD-51", "resume")], 1))
            got = resume_scene("q-resume-wait", usage_of(stop_of(resets=+600)))
            check("a-window-not-yet-reset-is-not-resumed", (got[0], got[1], got[2]),
                  ([], None, False))
            bad = [usage_of(stop_of(), schema="other/1"), usage_of(stop_of(), written_at=when(-7200)),
                   usage_of(stop_of(), limit_stops_over_cap=None),
                   usage_of(stop_of(), result="failed")]
            got = [resume_scene("q-resume-bad%d" % n, u) for n, u in enumerate(bad)]
            check("a-usage-file-it-cannot-trust-resumes-nothing-and-says-so",
                  [(g[0], g[1], g[2], "the usage file cannot be trusted" in g[3]) for g in got],
                  [([], None, True, True)] * 4)
            got = resume_scene("q-resume-nofile", False)
            check("no-usage-file-resumes-nothing-and-says-so",
                  (got[0], got[1], got[2], "the usage file cannot be trusted" in got[3]),
                  ([], None, True, True))
            got = resume_scene("q-resume-reset-unknown", usage_of(stop_of(resets=None)))
            check("a-reset-it-cannot-read-is-held-and-said", (got[0], got[2],
                  "names no reset time I can read" in got[3]), ([], True, True))
            got = resume_scene("q-resume-closed", usage_of(stop_of()), v={
                "state": "closed", "why": "before-start", "decision": None})
            check("a-closed-window-resumes-nothing", (got[0], got[1]), ([], None))
            for name, setup, why_ in (
                    ("running", lambda t_: stopped(t_, sessions=[
                        {"id": "s-old", "status": "active", "createdAt": when(-9000)}]),
                     "a-session-still-running"),
                    ("not-whole", lambda t_: (stopped(t_), setattr(t_, "sessions_whole", False)),
                     "a-session-list-not-read-whole"),
                    ("other-holder", lambda t_: stopped(t_, delegate="someone-else"),
                     "a-ticket-someone-else-holds"),
                    ("later-session", lambda t_: stopped(t_, sessions=[
                        {"id": "s-old", "status": "error", "createdAt": when(-9000)},
                        {"id": "s-mine", "status": "complete", "createdAt": when(-60)}]),
                     "a-piece-handed-over-again-since-the-stop")):
                got = resume_scene("q-resume-" + name, usage_of(stop_of()), setup=setup)
                check("resume-holds-" + why_, (got[0], got[1]), ([], None))
            c, s, t, out, ledger = resumed("q-resume-again", usage_of(stop_of(), ))
            t.sessions["uuid-51"][-1]["status"] = "error"
            fresh = usage_of(stop_of(ended=-600, resets=-300), stop_of())
            with open(os.path.join(os.path.dirname(ledger), "usage.json"), "w") as fh:
                json.dump(fresh, fh)
            outs = []
            for k in (1, 2):
                answer["v"] = decided()
                outs.append(run(c, s, t, at=at_ + 1000 * k))
            check("a-second-stop-of-the-piece-is-said-once-and-never-resumed",
                  (len(t.delegations), rows(ledger), outs[0][0] != EXIT_OK,
                   "stopped by the limit again" in outs[0][1], outs[1][0] != EXIT_OK),
                  (2, [("PROD-51", "resume")], True, True, False))
            c, s, t, out, ledger = resumed("q-resume-failed", usage_of(stop_of()),
                                           setup=lambda t_: stopped(t_, making=False))
            answer["v"] = decided()
            again = run(c, s, t, at=at_ + 1000)
            check("a-resume-that-fails-is-said-and-never-retried-by-itself",
                  (out[0] != EXIT_OK, "could not put it back" in out[1], len(t.delegations),
                   again[0] != EXIT_OK), (True, True, 2, False))
            c, s, t, out, ledger = resumed(
                "q-resume-fail-start", usage_of(stop_of()), v=decided(start("PROD-52")),
                setup=lambda t_: stopped(t_, making=False))
            check("a-failed-resume-ends-the-pass-before-any-start",
                  (out[0] != EXIT_OK, [d for d in t.delegations if d[0] == "uuid-52"],
                   rows(ledger)), (True, [], [("PROD-51", "resume")]))
            c, s, t, out, ledger = resumed("q-resume-then-start", usage_of(stop_of()),
                                           v=decided(start("PROD-52")))
            first = ([d for d in t.delegations if d[0] == "uuid-52"], rows(ledger), out[0])
            answer["v"] = decided(start("PROD-52"))
            run(c, s, t, at=at_ + 1000)
            check("a-resume-takes-the-days-load-so-the-starts-wait-for-the-next-pass",
                  (first, [d for d in t.delegations if d[0] == "uuid-52"], rows(ledger)),
                  (([], [("PROD-51", "resume")], EXIT_OK), [("uuid-52", "agent-1")],
                   [("PROD-51", "resume"), ("PROD-52", "plan")]))

            def unreadable(t_):
                stopped(t_)
                real_issue = t_.issue

                def issue(ident):
                    if ident == "PROD-51" and not t_.healed:
                        raise BridgeError("the tracker could not be read (simulated)")
                    return real_issue(ident)
                t_.healed, t_.issue = False, issue
            c, s, t, out, ledger = resumed("q-resume-unread", usage_of(stop_of()),
                                           setup=unreadable)
            t.healed = True
            answer["v"] = decided()
            run(c, s, t, at=at_ + 1000)
            check("a-read-that-fails-before-anything-is-written-says-it-is-tried-again",
                  (out[0] != EXIT_OK, "tried again next pass" in out[1],
                   "not retried by itself" in out[1], t.delegations, rows(ledger)),
                  (True, True, False, [("uuid-51", None), ("uuid-51", "agent-1")],
                   [("PROD-51", "resume")]))
            c, s, t, out, ledger = resumed("q-resume-nonew", usage_of(stop_of()),
                                           setup=lambda t_: stopped(t_, making=None))
            check("a-hand-over-linear-made-nothing-from-is-said",
                  (out[0] != EXIT_OK, "Linear made no new hand-over" in out[1]), (True, True))
            conf, ledger = queue_at("q-resume-dry")
            with open(os.path.join(os.path.dirname(conf), "usage.json"), "w") as fh:
                json.dump(usage_of(stop_of()), fh)
            c, s, t, root = approved("q-resume-dry", wip=2, queue_config=conf,
                                     kids=(("PROD-51", ()), ("PROD-52", ())))
            stopped(t)
            active[0], answer["v"] = t, decided()
            run(c, s, t, dry=True, at=at_)
            check("a-dry-run-resumes-nothing", (t.delegations, rows(ledger)), ([], None))

            # Each rule broken alone: its scene comes out otherwise.
            def scene_starts(name):
                queued(name, decided(start("PROD-51"), start("PROD-7", "todo", "acme/app:2")))
                return list(order_seen)

            def scene_closed(name):
                return delegated(queued(name, {"state": "closed", "why": "before-start",
                                               "decision": None})[2])

            def scene_interval(name):
                c_, s_, t_ = queued(name, decided())[:3]
                del asks[:]
                run(c_, s_, t_, at=now + 1100)
                return len(asks)

            def scene_limit(name):
                return delegated(queued(name, decided(start("PROD-51"), start(
                    "PROD-52", lane="acme/app:2")), max_actions_per_hour=2)[2])

            def late_ledger(_saved):
                def start_queued(self, entry, agent_id, ledger):
                    piece = self.queued_piece(entry) if entry["source"] == "plan" else None
                    if piece:
                        self.start_child(piece[0], piece[1], piece[2], agent_id)
                    else:
                        self.hand_over(entry["id"], entry["ticket"], agent_id,
                                       starter.attribution(entry))
                    starter.record_start(ledger, self.now, entry["ticket"], entry["source"])
                return start_queued

            def ungated(saved):
                def release_epic(self, *args):
                    queue, self.cfg["queue_config"] = self.cfg["queue_config"], ""
                    try:
                        return saved(self, *args)
                    finally:
                        self.cfg["queue_config"] = queue
                return release_epic

            def every_pass(saved):
                def queue_pass(self, *args):
                    self.state["queue_at"] = 0
                    return saved(self, *args)
                return queue_pass

            def forgetful(saved):
                def queue_pass(self, *args):
                    self.state.pop("queue_room", None)
                    return saved(self, *args)
                return queue_pass

            def mute_resume(saved):
                def queue_pass(self, *args):
                    say, self.say = self.say, lambda line: (
                        None if "room again" in line else say(line))
                    try:
                        return saved(self, *args)
                    finally:
                        del self.say
                return queue_pass

            def quiet(saved):
                def queue_pass(self, *args):
                    before = len(self.release_errors)
                    try:
                        return saved(self, *args)
                    finally:
                        del self.release_errors[before:]
                return queue_pass

            def broken(name, attr, make, scene, owner=PlansMixin):
                saved = getattr(owner, attr)
                setattr(owner, attr, make(saved))
                try:
                    return scene(name)
                finally:
                    setattr(owner, attr, saved)
            def scene_resumed(name):
                return resume_scene(name, usage_of(stop_of()))[:2]

            def never_off(saved):
                def delegate_to(self, issue_id, agent_id, count=True):
                    if agent_id is not None:
                        saved(self, issue_id, agent_id, count)
                return delegate_to

            import pipeline_bridge as bridge_module
            for what, got, good in (
                    ("the ledger row written after the hand-over",
                     broken("q-m1", "start_queued", late_ledger, scene_starts),
                     [("PROD-51", 0), ("PROD-7", 1)]),
                    ("the release loop starting a plan's piece with the queue on",
                     broken("q-m2", "release_epic", ungated, scene_closed), []),
                    ("the queue asked every pass", broken("q-m3", "queue_pass", every_pass,
                                                          scene_interval), 0),
                    ("a short machine said every pass", broken("q-m5", "queue_pass", forgetful,
                                                               room_story), story),
                    ("room again never said", broken("q-m6", "queue_pass", mute_resume,
                                                     room_story), story),
                    ("a short machine's pass not loud", broken("q-m7", "queue_pass", quiet,
                                                               room_story), story),
                    ("a resume that never takes the agent off first",
                     broken("q-m8", "delegate_to", never_off, scene_resumed, bridge_module.Pass),
                     ([("uuid-51", None), ("uuid-51", "agent-1")], [("PROD-51", "resume")])),
                    ("the action limit ignored", broken("q-m4", "actions_left",
                                                        lambda saved: lambda self: 99,
                                                        scene_limit, bridge_module.Pass),
                     ["uuid-51"])):
                check("mutant: %s is caught" % what, got != good, True)
        finally:
            starter.decide, starter.record_start = real
        kit.keep(approved=approved, delegated=delegated, followed=followed)


def cases_finish(kit):
    """A finished plan closes itself (KIT-299): its cases from the bridge's selftest, moved
    here unchanged (KIT-312)."""
    from pipeline_bridge import (ALICE, BOB, CHAN, DM, EXAMPLE_CONFIG, EXIT_ERROR,
                                 EXIT_OK, FakeSlack, ME, OWNER, _config_error, state_path)
    with kit as tmp:
        check, FakeWriter, approved = kit.take("check", "FakeWriter", "approved")
        cfg_for, delegated, dmworld = kit.take("cfg_for", "delegated", "dmworld")
        followed, now, outcome, posted = kit.take("followed", "now", "outcome", "posted")
        run, writer_bin, wrun = kit.take("run", "writer_bin", "wrun")
        # 17. A FINISHED PLAN CLOSES ITSELF (KIT-299). Every child Done, every pull request
        #     the GitHub integration linked merged: the epic moves to the done state, one
        #     comment, one wrap-up where it was approved, and the plan is let go. The notes its
        #     reviews left get ONE question; a yes files them as one ticket with a team, a
        #     title and a description only. Each case runs alone, so one that raises fails as
        #     itself.
        pr_link = "https://github.com/acme/app/pull/%d"

        def link(t, ident, number, status="merged", source="github", meta=True):
            att = {"url": pr_link % number, "sourceType": source}
            if meta:
                att["metadata"] = {"status": status} if status is not None else {"number": number}
            t.links.setdefault(ident, []).append(att)

        def finding(summary, sev="low", path="src/app.py", line=12, pr=None):
            return {"severity": sev, "summary": summary, "file": path, "line": line,
                    "pr_number": pr, "category": "general", "at": "2026-10-30T10:00:00Z"}

        def review(t, ident, findings, author=OWNER, at="2026-10-30T10:00:00Z", raw=None):
            block = {"schema": "pipeline-telemetry/1", "runs": [], "ticket_events": []}
            if findings:
                block["review_findings"] = findings
            body = raw if raw is not None else (
                "**Pipeline review** — PR #1\n\nA summary.\n\n```json\n%s\n```\n"
                % json.dumps(block, indent=2))
            t.notes_on.setdefault(ident, []).append({"body": body, "createdAt": at,
                                                     "user": {"id": author}})

        def finished(name, kids=None, setup=None, links=True, **over):
            """An approved plan with each child Done and its pull request merged, then one
            pass. `setup` changes the world before that pass."""
            c, s, t, root = approved(name, kids=kids or (("PROD-51", ()),
                                                         ("PROD-52", ("PROD-51",))), **over)
            for n, k in enumerate(t.epics["PROD-50"]):
                if links:
                    link(t, k, 437 + n)
                t.set_state(k, "completed")
            if setup:
                setup(t)
            code, _out = run(c, s, t)
            return c, s, t, root, code

        def done_moves(t):
            return [m for m in t.moves if m[1] == "st-done"]

        def wraps(s):
            return [(ch, th, x) for ch, th, x in s.posts if "pieces of PROD-50" in x]

        def asked_followup(s):
            return [(ch, th, x) for ch, th, x in s.posts if "*File the review notes" in x]

        def closing(t):
            return [b for i, b in t.comments if i == "uuid-50" and b.startswith("Marked ")]

        def case_closes():
            c, s, t, root, code = finished("fin-closes")
            wrap = wraps(s)
            return (code, done_moves(t), len(closing(t)),
                    BOB in (closing(t) or [""])[0] and pr_link % 437 in (closing(t) or [""])[0],
                    len(wrap), [n for n in ("#437", "#438", "and merged", "marked the plan Done")
                                if wrap and n not in wrap[0][2]],
                    followed(c), asked_followup(s))
        check("all-merged-closes-the-plan: one stateId move to Done, one comment, one wrap-up "
              "naming every PR, let go, no question", outcome(case_closes),
              (EXIT_OK, [("uuid-50", "st-done")], 1, True, 1, [], [], []))

        def case_where_channel():
            c, s, t, root, _code = finished("fin-channel")
            return [(ch, th == root, "#437, #438" in x and "marked the plan Done" in x)
                    for ch, th, x in wraps(s)]
        check("the wrap-up goes in the approval's thread when approved in a channel",
              outcome(case_where_channel), [(CHAN, True, True)])

        def case_where_dm():
            c, s, t = dmworld("fin-dm", act=True, agent_user_name="Agent")
            t.add_epic("PROD-50", (("PROD-51", ()), ("PROD-52", ("PROD-51",))))
            s.add(ALICE, "approve PROD-50", channel=DM)
            run(c, s, t)
            s.add(ALICE, "yes", channel=DM)
            run(c, s, t)
            for n, k in enumerate(t.epics["PROD-50"]):
                link(t, k, 437 + n)
                t.set_state(k, "completed")
            review(t, "PROD-51", [finding("Name the timeout")])
            run(c, s, t)
            q = [x for x in asked_followup(s)]
            s.add(ALICE, "yes", channel=DM)
            run(c, s, t)
            return ([(ch, th) for ch, th, _x in wraps(s) + q],
                    "Say `yes` here within 24 hours" in (q or [("", "", "")])[0][2],
                    len(t.filed), [th for ch, th, x in s.posts if "Filed PROD-900" in x])
        check("the wrap-up and its question are top-level when approved in a DM, and a yes "
              "there files", outcome(case_where_dm), ([(DM, None), (DM, None)], True, 1, [None]))

        def case_canceled():
            c, s, t, root, _code = finished(
                "fin-canceled", setup=lambda t: t.set_state("PROD-52", "canceled"))
            run(c, s, t)
            return (done_moves(t), sum("PROD-52 was canceled" in p for p in posted(s)),
                    wraps(s), followed(c))
        check("a canceled child blocks the close, said once, and the plan is still followed",
              outcome(case_canceled), ([], 1, [], ["uuid-50"]))

        def case_closed_pr():
            def closed_52(t):
                t.links["PROD-52"] = []
                link(t, "PROD-52", 438, status="Closed")
            c, s, t, root, _code = finished("fin-closed-pr", setup=closed_52)
            run(c, s, t, at=now + FINISH_RECHECK_SECONDS + 1)
            held = [p for p in posted(s) if "#438 was closed without being merged" in p]
            reads = t.piece_reads
            run(c, s, t, at=now + FINISH_RECHECK_SECONDS + 60)
            return (done_moves(t), len(held), wraps(s), followed(c),
                    t.piece_reads == reads)
        check("a pull request closed without a merge blocks the close, said once, read again "
              "at most every FINISH_RECHECK_SECONDS", outcome(case_closed_pr),
              ([], 1, [], ["uuid-50"], True))

        def case_closed_beside():
            def beside_unknown(t):
                t.links["PROD-52"] = []
                link(t, "PROD-52", 438, status="closed")
                link(t, "PROD-52", 439, status=None)

            def beside_other_repo(t):
                t.links["PROD-52"] = []
                link(t, "PROD-52", 438, status="closed")
                t.links["PROD-52"].append({"url": "https://github.com/acme/other/pull/9",
                                           "sourceType": "github",
                                           "metadata": {"status": "merged"}})
            got = []
            for name, setup in (("fin-beside-unknown", beside_unknown),
                                ("fin-beside-other", beside_other_repo)):
                c, s, t, root, _code = finished(name, setup=setup)
                got.append((done_moves(t), sum("#438 was closed without being merged" in p
                                               for p in posted(s)), wraps(s), followed(c)))
            return got
        check("a closed pull request holds the close beside a link with no status, or beside "
              "one merged in another repository", outcome(case_closed_beside),
              [([], 1, [], ["uuid-50"])] * 2)

        def case_links_over():
            c, s, t, root, _code = finished(
                "fin-links-over", setup=lambda t: t.more_links.add("PROD-52"))
            return (done_moves(t), sum("PROD-52 has more than 50 links" in p
                                       for p in posted(s)), wraps(s), followed(c))
        check("a piece with more links than one read takes holds the close, said once",
              outcome(case_links_over), ([], 1, [], ["uuid-50"]))

        def case_reopened(back_to):
            # The bridge handed PROD-52 over, it went Done, and the close waits on its pull
            # request. Then it is reopened: by the next look the PR is merged, but the piece
            # is live again. The plan stays open and followed, the piece is never handed
            # over twice, and it is said only as any handed-over piece is (KIT-284).
            c, s, t, root = approved("fin-reopened-" + back_to,
                                     kids=(("PROD-51", ()), ("PROD-52", ("PROD-51",))))
            t.set_state("PROD-51", "completed")
            run(c, s, t)
            link(t, "PROD-51", 437)
            link(t, "PROD-52", 438, status="Closed")
            t.set_state("PROD-52", "completed")
            run(c, s, t)
            held = sum("#438 was closed without being merged" in p for p in posted(s))
            t.set_state("PROD-52", back_to)
            t.links["PROD-52"] = []
            link(t, "PROD-52", 438)
            before = len(s.posts)
            for later in (FINISH_RECHECK_SECONDS + 1, STUCK_START_SECONDS + 60,
                          STUCK_START_SECONDS + 120):
                run(c, s, t, at=now + later)
            said = [x for _ch, _th, x in s.posts[before:]]
            return (held, delegated(t), done_moves(t), wraps(s), followed(c),
                    asked_followup(s), [("hasn't been picked up yet" in x) for x in said])
        check("a piece reopened before the close holds it: no move, no wrap-up, still "
              "followed, never handed over again, and a piece at work again is not called "
              "stuck", outcome(lambda: case_reopened("started")),
              (1, ["uuid-51", "uuid-52"], [], [], ["uuid-50"], [], []))
        check("a piece reopened to Todo holds the close, and after two hours that it hasn't "
              "been picked up yet is said once over two passes (KIT-289)",
              outcome(lambda: case_reopened("unstarted")),
              (1, ["uuid-51", "uuid-52"], [], [], ["uuid-50"], [], [True]))

        def case_extra_open():
            def extra(t):
                t.issues["PROD-59"] = dict(t.issues["PROD-52"], id="uuid-59",
                                           identifier="PROD-59", title="Added by a session",
                                           blocked_by=[], labels=[])
                t.epics["PROD-50"].append("PROD-59")
                t.set_state("PROD-59", "unstarted")
            c, s, t, root, _code = finished("fin-extra-open", setup=extra)
            run(c, s, t, at=now + FINISH_RECHECK_SECONDS + 1)
            held = (done_moves(t), wraps(s), followed(c),
                    sum("PROD-59 is still Todo" in p for p in posted(s)),
                    sum("PROD-59 was added after" in p for p in posted(s)))
            link(t, "PROD-59", 440)
            t.set_state("PROD-59", "completed")
            run(c, s, t, at=now + 2 * FINISH_RECHECK_SECONDS + 2)
            return held, (done_moves(t), len(wraps(s)), followed(c))
        check("a piece added after the approval and left open holds the close, said once; "
              "once it is Done the plan closes", outcome(case_extra_open),
              (([], [], ["uuid-50"], 1, 1), ([("uuid-50", "st-done")], 1, [])))

        def case_reads_bounded():
            c, s, t, root, _code = finished("fin-reads", done_state="Shipped")
            first = t.piece_reads
            run(c, s, t, at=now + 60)
            run(c, s, t, at=now + 120)
            again = t.piece_reads
            run(c, s, t, at=now + FINISH_RECHECK_SECONDS + 1)
            return first, again - first, t.piece_reads - again
        check("a close held by anything reads its pieces again at most every "
              "FINISH_RECHECK_SECONDS", outcome(case_reads_bounded), (2, 0, 2))

        def case_open_pr():
            def open_52(t):
                t.links["PROD-52"] = []
                link(t, "PROD-52", 438, status="open")
            c, s, t, root, _code = finished("fin-open-pr", setup=open_52)
            held = sum("#438 is open, not merged" in p for p in posted(s))
            t.links["PROD-52"] = []
            link(t, "PROD-52", 438, status="MERGED")
            run(c, s, t, at=now + FINISH_RECHECK_SECONDS + 1)
            return (held, done_moves(t), len(wraps(s)), followed(c))
        check("an open pull request holds the close until it is merged, then it closes",
              outcome(case_open_pr), (1, [("uuid-50", "st-done")], 1, []))

        def case_replaced_pr():
            def replaced(t):
                t.links["PROD-52"] = []
                link(t, "PROD-52", 438, status="closed")
                link(t, "PROD-52", 439, status="merged")
            c, s, t, root, _code = finished("fin-replaced", setup=replaced)
            wrap = (wraps(s) or [("", "", "")])[0][2]
            return (done_moves(t), "#439" in wrap, "#438" in wrap)
        check("a closed pull request beside a merged one was replaced, and does not hold it",
              outcome(case_replaced_pr), ([("uuid-50", "st-done")], True, False))

        def case_no_status():
            def unstated(t):
                t.links["PROD-52"] = []
                link(t, "PROD-52", 438, status=None)
            c, s, t, root, _code = finished("fin-no-status", setup=unstated)
            wrap = (wraps(s) or [("", "", "")])[0][2]
            return (done_moves(t), "PROD-52: merge not confirmed by Linear (#438)" in wrap,
                    "and merged" in wrap, "PROD-51: merge not confirmed" in wrap, followed(c))
        check("a link with no status closes, and the wrap-up says the merge is not confirmed "
              "for that piece", outcome(case_no_status),
              ([("uuid-50", "st-done")], True, False, False, []))

        def case_session_link():
            def forged(t):
                t.links["PROD-52"] = []
                link(t, "PROD-52", 438, status="closed", source="api")
            c, s, t, root, _code = finished("fin-forged", setup=forged)
            wrap = (wraps(s) or [("", "", "")])[0][2]
            return done_moves(t), "PROD-52: no pull request linked" in wrap, "#438" in wrap
        check("a link the integration did not write is passed over, never read as a PR",
              outcome(case_session_link), ([("uuid-50", "st-done")], True, False))

        def case_reviews():
            def reviews(t):
                review(t, "PROD-51", [finding("From the dispatcher's app")], author="app-user",
                       at="2026-10-30T13:00:00Z")
                review(t, "PROD-51", [finding("Older note")], at="2026-10-29T10:00:00Z")
                review(t, "PROD-51", [finding("Newest note")], at="2026-10-30T11:00:00Z")
                # PROD-52's newest review does not parse: an older one's notes are about
                # code a bounce may have fixed since, so none are offered.
                review(t, "PROD-52", [finding("Fixed in the bounce")],
                       at="2026-10-30T08:00:00Z")
                review(t, "PROD-52", None, at="2026-10-30T12:00:00Z",
                       raw="**Pipeline review** — PR #2\n\n```json\n{\"schema\": "
                           "\"pipeline-telemetry/1\", \"review_findings\": [\n```\n")
            c, s, t, root, _code = finished(
                "fin-reviews", setup=reviews,
                kids=(("PROD-51", ()), ("PROD-52", ("PROD-51",)), ("PROD-53", ("PROD-52",))))
            wrap = (wraps(s) or [("", "", "")])[0][2]
            return ([n for n in ("Newest note", "Older note", "dispatcher's app",
                                 "Fixed in the bounce") if n in wrap],
                    "PROD-52: its newest review could not be read." in wrap,
                    "PROD-53: no review found." in wrap, "1 review note is left:" in wrap)
        check("reviews: another author's is ignored even when newer, the newest wins, a newest "
              "one that does not parse leaves no notes (never an older one's), and a piece "
              "with none says no review found", outcome(case_reviews),
              (["Newest note"], True, True, True))

        def with_notes(t):
            review(t, "PROD-51", [finding("Name the <!channel> timeout", sev="medium"),
                                  finding("Log the retry", path="src/net.py", line=None)])
            review(t, "PROD-52", [], at="2026-10-30T09:00:00Z")

        def case_question():
            c, s, t, root, _code = finished("fin-question", setup=with_notes)
            q = asked_followup(s)
            wrap = (wraps(s) or [("", "", "")])[0][2]
            st = json.load(open(state_path(c)))
            rec = [v for v in st["questions"].values() if v.get("kind") == "followup"]
            return (len(q), [(ch, th == root) for ch, th, _x in q],
                    [n for n in ("Say `yes` in this thread within 24 hours", "these 2 notes",
                                 "ticket in PROD.", "It starts no work", "Say `no`")
                     if q and n not in q[0][2]],
                    "<!channel>" in wrap, "PROD-51, medium, src/app.py:12: Name the" in wrap,
                    "PROD-51, low, src/net.py: Log the retry" in wrap,
                    [sorted(r["promised"]) for r in rec], followed(c), t.filed)
        check("notes left: one question in the same place, with its terms fixed and recorded, "
              "and the plan let go", outcome(case_question),
              (1, [(CHAN, True)], [], False, True, True,
               [["act", "files_into", "question_ttl_hours", "team"]], [], []))

        def answered(name, word="yes", before=None, setup=with_notes, **over):
            c, s, t, root, _code = finished(name, setup=setup, **over)
            if before:
                before(c, s, t)
            s.add(BOB, word, thread=root)
            code, _out = run(c, s, t)
            run(c, s, t)
            return c, s, t, root, code

        def case_yes():
            c, s, t, root, code = answered("fin-yes")
            filed = t.filed
            link_comment = [b for i, b in t.comments if i == "uuid-50" and "PROD-900" in b]
            said = [(ch, th == root) for ch, th, x in s.posts if x.startswith("Filed PROD-900")]
            desc = (filed or [{}])[0].get("description", "")
            return (code, len(filed), [sorted(f) for f in filed],
                    (filed or [{}])[0].get("title"), (filed or [{}])[0].get("teamId"),
                    "#437" in desc and "Log the retry" in desc and "<" not in desc,
                    len(link_comment), said, done_moves(t))
        check("yes files exactly one issueCreate with teamId, title and description only, "
              "then the epic comment linking it, then the confirmation", outcome(case_yes),
              (EXIT_OK, 1, [["description", "teamId", "title"]],
               "Follow-ups from Epic PROD-50", "team-prod", True, 1, [(CHAN, True)],
               [("uuid-50", "st-done")]))

        def case_partial():
            import telemetry_block as tb

            def partial_review(t, ident):
                block = {"schema": "pipeline-telemetry/1", "runs": [], "ticket_events": []}
                review(t, ident, None, raw=tb.review_comment(
                    {"pr": 437, "coverage": "partial", "withheld_files": ["a.py", "b.py"]},
                    block))

            def partial_clean(t):
                partial_review(t, "PROD-51")
                review(t, "PROD-52", [])
            c, s, t, root, _code = finished("fin-partial", setup=partial_clean)
            wrap = (wraps(s) or [("", "", "")])[0][2]

            def partial_noted(t):
                partial_review(t, "PROD-51")
                review(t, "PROD-52", [finding("Log the retry")])
            c2, s2, t2, root2, _code2 = answered("fin-partial-yes", setup=partial_noted)
            desc = (t2.filed or [{}])[0].get("description", "")
            return ("PROD-51: its review was partial: 2 files were never read." in wrap,
                    "Its reviews left no notes." in wrap,
                    "Its reviews left no notes on the files they read." in wrap,
                    "PROD-51: its review was partial: 2 files were never read, so those files "
                    "have no notes here." in desc)
        check("a partial review is said as partial in the wrap-up and the follow-up, never as "
              "no notes", outcome(case_partial), (True, False, True, True))

        def case_no():
            c, s, t, root, code = answered("fin-no", word="no")
            return (t.filed, sum("won't file the notes from PROD-50" in p for p in posted(s)))
        check("no files nothing, and says so once", outcome(case_no), ([], 1))

        def case_changed():
            def another_note(_c, _s, t):
                review(t, "PROD-52", [finding("A note added after the question")],
                       at="2026-10-30T13:00:00Z")
            c, s, t, root, code = answered("fin-changed", before=another_note)
            st = json.load(open(state_path(c)))
            return (t.filed, sum("changed since I asked, so nothing was filed" in p
                                 for p in posted(s)),
                    [v["status"] for v in st["questions"].values() if v.get("kind") == "followup"])
        check("a yes after a note changed files nothing and closes the question",
              outcome(case_changed), ([], 1, [REFUSED]))

        def case_act_off():
            def acting_off(c, _s, _t):
                c["act"] = False
            c, s, t, root, code = answered("fin-act-off", before=acting_off)
            said = sum("settings changed since I asked about PROD-50" in p for p in posted(s))
            # A plan finishing while acting is off moves nothing and asks nothing.
            c2, s2, t2, root2 = approved("fin-act-off-early")
            c2["act"] = False
            for k in t2.epics["PROD-50"]:
                link(t2, k, 437)
                t2.set_state(k, "completed")
            _code, out = run(c2, s2, t2)
            return (t.filed, said, done_moves(t2), wraps(s2), followed(c2),
                    "acting is off: 1 approved epic(s) wait" in out)
        check("act off: nothing moved or filed, and a yes after it is said once",
              outcome(case_act_off), ([], 1, [], [], ["uuid-50"], True))

        def case_no_notes():
            def clean(t):
                review(t, "PROD-51", [])
                review(t, "PROD-52", [])
            c, s, t, root, _code = finished("fin-clean", setup=clean)
            wrap = (wraps(s) or [("", "", "")])[0][2]
            return asked_followup(s), "Its reviews left no notes." in wrap, followed(c)
        check("no notes left: no question", outcome(case_no_notes), ([], True, []))

        def case_by_hand():
            def closed_by_hand(t):
                t.set_state("PROD-50", "completed")
            c, s, t, root, _code = finished("fin-by-hand", setup=closed_by_hand)
            run(c, s, t)
            return (done_moves(t), t.piece_reads,
                    sum("PROD-50 is already Done" in p for p in posted(s)), wraps(s),
                    followed(c))
        check("a plan already Done by hand: no move, said, let go", outcome(case_by_hand),
              ([], 0, 1, [], []))

        def case_twice():
            c, s, t, root, _code = finished("fin-twice", setup=with_notes)
            first = (done_moves(t), len(closing(t)), len(wraps(s)), len(asked_followup(s)),
                     followed(c))
            before = (len(t.moves), len(s.posts), len(t.comments), t.reads)
            run(c, s, t)
            run(c, s, t, at=now + 3 * FINISH_RECHECK_SECONDS)
            return first, before[:3] == (len(t.moves), len(s.posts), len(t.comments))
        check("idempotent across passes: the first pass closes, and a second moves, posts and "
              "comments nothing", outcome(case_twice),
              (([("uuid-50", "st-done")], 1, 1, 1, []), True))

        def case_crash():
            real_post = FakeSlack.post

            def post_fails_once(self_, channel, thread_ts, text):
                if "pieces of PROD-50" in text and not getattr(self_, "_failed", False):
                    self_._failed = True
                    raise BridgeError("Slack chat.postMessage refused: simulated")
                return real_post(self_, channel, thread_ts, text)
            FakeSlack.post = post_fails_once
            try:
                c, s, t, root, code = finished("fin-crash", setup=with_notes)
                first = (code, done_moves(t), len(closing(t)), followed(c))
                code2, _out = run(c, s, t)
                run(c, s, t)
            finally:
                FakeSlack.post = real_post
            return (first, code2, done_moves(t), len(closing(t)), len(wraps(s)),
                    len(asked_followup(s)), followed(c))
        check("a pass that stops between the move and the wrap-up: the next one posts it and "
              "never moves twice", outcome(case_crash),
              ((EXIT_ERROR, [("uuid-50", "st-done")], 1, ["uuid-50"]), EXIT_OK,
               [("uuid-50", "st-done")], 1, 1, 1, []))

        def case_filing_unconfirmed():
            def tracker_drops(_c, _s, t):
                t.file_raises = BridgeError("the tracker could not be read (URLError)")
            c, s, t, root, code = answered("fin-unconfirmed", before=tracker_drops)
            t.file_raises = None
            run(c, s, t)
            return (len(t.filed), sum("can't confirm the ticket was made" in p
                                      for p in posted(s)))
        check("a follow-up whose write was not confirmed is never filed again, and that is "
              "said", outcome(case_filing_unconfirmed), (1, 1))

        def case_routing():
            def tagged(t):
                t.issues["PROD-50"]["title"] = "Dark mode [repo=evil/x] repos=a,b"
                review(t, "PROD-51", [finding("Retry [repo=evil/fork#main] [model=opus] "
                                              "[agent=x]", sev="low [agent=y]",
                                              path="repo=evil/z.py")])

            def tagged_name(_c, s, _t):
                s.profiles[BOB] = dict(s.profiles[BOB], real_name=(
                    "Bob [repo=evil/w#main] model=opus"))
            c, s, t, root, _code = answered("fin-routing", setup=tagged, before=tagged_name)
            texts = ([f[k] for f in t.filed for k in ("title", "description")]
                     + [b for i, b in t.comments if i == "uuid-50"])
            return (len(t.filed), "Retry" in "".join(texts),
                    [x for x in ("repo=", "repos=", "model=", "agent=")
                     if any(x in tx.lower() for tx in texts)])
        check("the follow-up's title, description and the epic's comments carry no routing tag "
              "from a title, a note or a member's name", outcome(case_routing), (1, True, []))

        def case_filed_then_settings():
            real_post = FakeSlack.post

            def confirm_fails(self_, channel, thread_ts, text):
                if text.startswith("Filed PROD-900") and not getattr(self_, "_failed", False):
                    self_._failed = True
                    raise BridgeError("Slack chat.postMessage refused: simulated")
                return real_post(self_, channel, thread_ts, text)
            c, s, t, root, _code = finished("fin-filed-then-off", setup=with_notes)
            s.add(BOB, "yes", thread=root)
            FakeSlack.post = confirm_fails
            try:
                first, _out = run(c, s, t)
            finally:
                FakeSlack.post = real_post
            c["act"] = False
            run(c, s, t)
            return (first, len(t.filed),
                    len([b for i, b in t.comments if i == "uuid-50" and "PROD-900" in b]),
                    sum("acts on nothing" in p for p in posted(s)),
                    [th == root for ch, th, x in s.posts if x.startswith("Filed PROD-900")])
        check("a follow-up filed before its reply was lost is said as filed, even after the "
              "settings changed", outcome(case_filed_then_settings),
              (EXIT_ERROR, 1, 1, 0, [True]))

        def case_done_state():
            c, s, t, root, _code = finished("fin-not-done", done_state="Shipped")
            run(c, s, t)
            named = finished("fin-named", done_state="Todo")
            return (done_moves(t), sum("can't mark it Shipped" in p for p in posted(s)),
                    followed(c), done_moves(named[2]),
                    sum("is not a done state" in p for p in posted(named[1])),
                    cfg_for("fin-default")["done_state"],
                    "done_state" in _config_error(dict(EXAMPLE_CONFIG, channel_ids=[CHAN],
                                                       owner_user_id=OWNER, bot_user_id=ME,
                                                       done_state=5,
                                                       state_dir=os.path.join(tmp, "fin-x"))))
        check("done_state: Done by default, a name the team lacks or one not a done state is "
              "said once and moves nothing", outcome(case_done_state),
              ([], 1, ["uuid-50"], [], 1, "Done", True))

        def case_dry():
            c, s, t, root = approved("fin-dry")
            for n, k in enumerate(t.epics["PROD-50"]):
                link(t, k, 437 + n)
                t.set_state(k, "completed")
            _code, out = run(c, s, t, dry=True)
            return (done_moves(t), wraps(s), followed(c),
                    "[dry-run] would mark PROD-50 Done" in out)
        check("a dry run says it would close, and moves and posts nothing", outcome(case_dry),
              ([], [], ["uuid-50"], True))

        def case_cap():
            def many(t):
                review(t, "PROD-51", [finding("Note %d" % i) for i in range(20)])
            c, s, t, root, _code = finished("fin-cap", setup=many)
            wrap = (wraps(s) or [("", "", "")])[0][2]
            lines = wrap.splitlines()
            return (len(lines), lines[-1], "Note 12" in wrap, "Note 13" in wrap,
                    "these 20 notes" in (asked_followup(s) or [("", "", "")])[0][2])
        check("the wrap-up lists at most 15 lines and says how many more",
              outcome(case_cap), (17, "…and 7 more.", True, False, True))

        def case_copies():
            import pipeline_review_poller as rp
            mine = globals().get("GITHUB_ATTACHMENT_SOURCES")
            pattern = getattr(globals().get("PR_URL_RE"), "pattern", None)
            return (mine == rp.GITHUB_ATTACHMENT_SOURCES, pattern == rp._PR_URL_RE.pattern,
                    [globals()["_source_key"](v) == rp._source_key(v)
                     for v in ("GitHub_PR", "github-pull-request", None, "Api")])
        check("the integration's source list and PR pattern are the review poller's own",
              outcome(case_copies), (True, True, [True, True, True, True]))

        # THE WRITER ON A FINISHED PLAN (KIT-297 on KIT-299): the wrap-up, the follow-up
        # question and its answer are written when sent. The notes a yes files and the
        # question's terms follow verbatim, and the writer is given what asks nothing.
        def case_finished_written():
            fw = FakeWriter()
            c, s, t, root = approved("fin-written", kids=(("PROD-51", ()),
                                                          ("PROD-52", ("PROD-51",))))
            for n, k in enumerate(t.epics["PROD-50"]):
                link(t, k, 437 + n)
                t.set_state(k, "completed")
            with_notes(t)
            c.update(writer=True, writer_bin=writer_bin)
            wrun(c, s, t, fw)
            s.add(BOB, "yes", thread=root)
            wrun(c, s, t, fw)
            wrun(c, s, t, fw)
            wrap = [x for _ch, _th, x in s.posts if "2 review notes are left:" in x]
            asked = [x for _ch, _th, x in s.posts if "I'll file these 2 notes" in x]
            calls = dict((x["kind"], x) for x in fw.calls)
            bad = [(x["kind"], why) for x in fw.calls
                   for why in [voice.check_text(x["fallback"], 100000, x["keep"],
                                                voice.fact_links(x["facts"]),
                                                voice.fact_ids(x["facts"], x["keep"]))]
                   if why]
            return ([k for k in ("plan-finished", "question-followup", "followup-filed")
                     if k not in calls], len(t.filed), bad,
                    [(x.startswith("I have news on"), "All 2 pieces" in x,
                      x.split("\n\n", 1)[-1].startswith("2 review notes are left:\n• PROD-51"),
                      "Log the retry" in x) for x in wrap],
                    asked == ["I have news on PROD-50.\n\nSay `yes` in this thread within 24 "
                              "hours and I'll file these 2 notes as one follow-up ticket in "
                              "PROD. It starts no work. Say `no` to leave them."],
                    (calls.get("question-followup") or {}).get("fallback"),
                    [x.startswith("I have news on ") for _ch, _th, x in s.posts
                     if "PROD-900" in x],
                    (calls.get("plan-finished") or {}).get("fallback", "").endswith(
                        " The notes below name PROD-51."),
                    # KIT-482 review: only the question's bind asks, so only its writer
                    # may take the model's own questions out; the wrap-up still refuses one.
                    [(calls.get(k) or {}).get("asks") for k in ("plan-finished",
                                                               "question-followup",
                                                               "followup-filed")])
        check("writer: a finished plan's wrap-up, question and answer are written; the notes "
              "and the question's terms follow verbatim", outcome(case_finished_written),
              ([], 1, [], [(True, False, True, True)], True,
               "The reviews of PROD-50's pieces left 2 notes.",
               [True], True, [False, True, False]))

        def case_followup_expired_written():
            # A closed plan's question has no request to ask again: its fixed line says
            # where the notes are, after the writer's words.
            fw = FakeWriter()
            c, s, t, root = approved("fin-expired-written", kids=(("PROD-51", ()),
                                                                  ("PROD-52", ("PROD-51",))))
            for n, k in enumerate(t.epics["PROD-50"]):
                link(t, k, 437 + n)
                t.set_state(k, "completed")
            with_notes(t)
            c.update(writer=True, writer_bin=writer_bin)
            wrun(c, s, t, fw)
            s.add(BOB, "yes", thread=root, ts="%.6f" % (now + 25 * 3600))
            wrun(c, s, t, fw, at=now + 25 * 3600 + 60)
            return [x for _ch, _th, x in s.posts if x.endswith(
                "\n\nThe notes stay in my wrap-up; file them yourself if you want them.")]
        check("writer: an expired follow-up question's fixed line follows the writer's words",
              outcome(case_followup_expired_written),
              ["I have news on PROD-50.\n\nThe notes stay in my wrap-up; file them yourself if "
               "you want them."])

        def case_note_ids_whole():
            # PROD-512 in the text does not name PROD-51, which only the notes hold.
            pieces = [{"ident": "PROD-512", "title": "A", "prs": [], "review": None,
                       "partial": None, "unreadable": False, "more": False, "links_over": False},
                      {"ident": "PROD-51", "title": "B", "review": [],
                       "prs": [{"repo": "acme/app", "number": 1, "status": "merged",
                                "url": "https://github.com/acme/app/pull/1"}],
                       "partial": None, "unreadable": False, "more": False, "links_over": False}]
            notes = [{"piece": "PROD-51", "severity": "low", "summary": "Name it",
                      "file": "src/a.py", "line": 3}]
            _text, _notes, about, _f = wrap_up_parts(
                {"identifier": "PROD-50", "title": "Plan"}, pieces, notes, "Done")
            return ("PROD-51:" not in _text and "PROD-512" in _text
                    and about.endswith(" The notes below name PROD-51."))
        check("writer: the ids only the notes hold are named as whole words",
              outcome(case_note_ids_whole), True)


def cases_ready_plans(kit):
    """A plan's page in a DM gets the bridge's own approve question there (KIT-274), once,
    naming how many open questions the planner left with the plan."""
    from pipeline_bridge import ALICE, DM, state_path
    with kit as tmp:  # noqa: F841
        check, dmworld, in_dm, now = kit.take("check", "dmworld", "in_dm", "now")
        outcome, run = kit.take("outcome", "run")
        plan = {"plan": "PROD-50", "pieces": 2, "questions": 2}

        def page(c, detail=plan, ts="1759990000.000100", key="comment:plan-1", at=None,
                 channel=DM, mark=notify.ESC_AWAITING_APPROVAL):
            """What the notifier records for a page it posts into the DM (KIT-296), with the
            plan-ready page's detail when it read the executor's line (KIT-274)."""
            path = c["notifier_posts_file"]
            posts = {}
            if os.path.exists(path):
                with open(path) as fh:
                    posts = json.load(fh)["posts"]
            rec = {"channel": channel, "ts": ts, "ticket_id": "PROD-49", "mark": mark,
                   "event_key": key, "recorded_at": at or iso(now)}
            if detail is not None:
                rec["detail"] = detail
            posts[key_of(channel, ts)] = rec
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                json.dump({"schema": "pipeline-notifier-dm-posts/1", "posts": posts}, fh)

        def ready(name, setup=None, **page_over):
            c, s, t = dmworld(name, act=True, agent_user_name="Agent")
            t.add_epic("PROD-50", (("PROD-51", ()), ("PROD-52", ("PROD-51",))))
            if setup:
                setup(c, s, t)
            page(c, **page_over)
            run(c, s, t)
            return c, s, t

        def asked(s):
            return [(th, x) for th, x in in_dm(s) if "Approve this plan?" in x]

        def case_asked():
            c, s, t = ready("ready-asked")
            first = asked(s)
            run(c, s, t)
            again = asked(s)
            s.add(ALICE, "yes", channel=DM)
            run(c, s, t)
            return ([th for th, _x in first], len(again), t.moves[:1],
                    "It came with 2 open questions from the planner, in its planning "
                    "ticket's notes." in (first or [("", "")])[0][1],
                    (first or [("", "")])[0][1].endswith(
                        "(Asked because its plan was just filed.)"))
        check("a-plan-ready-page-gets-the-approve-question-in-its-dm-once-and-a-yes-approves "
              "(KIT-274)", outcome(case_asked),
              ([None], 1, [("uuid-50", "st-todo")], True, True))

        def case_no_then_quiet():
            # Once its question is answered `no`, nothing is open on the plan and it is not
            # approved: only the record of the page keeps the next pass from asking again.
            c, s, t = ready("ready-no")
            s.add(ALICE, "no", channel=DM)
            run(c, s, t)
            run(c, s, t)
            return len(asked(s)), t.moves
        check("a-page-whose-question-was-answered-no-is-not-asked-about-again (KIT-274)",
              outcome(case_no_then_quiet), (1, []))

        def case_counts():
            got = []
            for n, key in ((0, "comment:plan-0"), (1, "comment:plan-1q"), (None, "comment:p")):
                detail = dict(plan, questions=n) if n is not None else {"plan": "PROD-50",
                                                                       "pieces": 2}
                c, s, t = ready("ready-count-%s" % n, detail=detail, key=key)
                text = (asked(s) or [("", "")])[0][1]
                got.append([w for w in ("It came with no open questions.",
                                        "It came with one open question from the planner, in "
                                        "its planning ticket's notes.", "It came with")
                            if w in text])
            return got
        check("the-question-says-none-one-or-nothing-when-the-count-is-unknown (KIT-274)",
              outcome(case_counts),
              [["It came with no open questions.", "It came with"],
               ["It came with one open question from the planner, in its planning ticket's "
                "notes.", "It came with"], []])

        def case_baseline():
            c, s, t = dmworld("ready-baseline", act=True, agent_user_name="Agent")
            t.add_epic("PROD-50", (("PROD-51", ()),))
            page(c)
            with open(state_path(c)) as fh:
                st = json.load(fh)
            st.pop("ready_plans", None)            # as a bridge from before this step left it
            with open(state_path(c), "w") as fh:
                json.dump(st, fh)
            run(c, s, t)
            before = len(asked(s))
            page(c, ts="1759990000.000200", key="comment:plan-2")
            run(c, s, t)
            return before, len(asked(s))
        check("pages-already-in-the-ledger-on-this-steps-first-pass-are-passed-over (KIT-274)",
              outcome(case_baseline), (0, 1))

        def case_prune():
            c, s, t = dmworld("ready-prune", act=True, agent_user_name="Agent")
            with open(state_path(c)) as fh:
                st = json.load(fh)
            st["ready_plans"] = {"comment:old": {"at": iso(now - 36 * 24 * 3600), "what": "x"},
                                 "comment:kept": {"at": iso(now - 34 * 24 * 3600), "what": "x"}}
            with open(state_path(c), "w") as fh:
                json.dump(st, fh)
            run(c, s, t)
            with open(state_path(c)) as fh:
                return sorted(json.load(fh)["ready_plans"])
        check("a-page-remembered-past-five-weeks-is-let-go (KIT-274)", outcome(case_prune),
              ["comment:kept"])

        def case_not_asked():
            got = {}
            for name, over in (
                    ("no-detail", {"detail": None}),
                    ("bad-plan-id", {"detail": dict(plan, plan="prod-50")}),
                    ("too-old", {"at": iso(now - 49 * 3600)}),
                    ("other-dm", {"channel": "D0SOMEONE"}),
                    ("other-mark", {"mark": notify.ESC_READY_TO_MERGE})):
                c, s, t = ready("ready-not-" + name, **over)
                got[name] = len(in_dm(s))
            c, s, t = ready("ready-not-gate", setup=lambda c_, s_, t_: t_.add_epic(
                "PROD-50", (("PROD-51", ()),), epic_labels=()))
            got["not-gate"] = len(in_dm(s))
            # KIT-341's check, through the helper both paths share: a label the agent's own
            # account added keeps the bridge from asking on its own, as from a typed approve.
            c, s, t = ready("ready-not-agent-label", setup=lambda c_, s_, t_: t_.add_label(
                "PROD-51", "provenance:epic", actor="agent-1"))
            got["agent-label"] = len(in_dm(s))

            def typed_first(c_, s_, t_):
                s_.add(ALICE, "approve PROD-50", channel=DM)
            c, s, t = ready("ready-typed", setup=typed_first)
            got["typed"] = len(asked(s))
            return got
        check("no-question-for-no-detail-a-bad-id-an-old-page-another-dm-another-mark-or-a-"
              "plan-the-checks-refuse-and-one-question-when-it-was-asked-already (KIT-274)",
              outcome(case_not_asked),
              {"no-detail": 0, "bad-plan-id": 0, "too-old": 0, "other-dm": 0, "other-mark": 0,
               "not-gate": 0, "agent-label": 0, "typed": 1})


def cases_answer(kit):
    """`answer <plan id>: <words>` in a DM (KIT-334): one question of the bridge's own, and a
    yes adds the member's words to the end of the plan's description, dated and naming them,
    before any piece starts."""
    from pipeline_bridge import ALICE, DM, state_path
    with kit as tmp:  # noqa: F841
        check, dmworld, in_dm, now = kit.take("check", "dmworld", "in_dm", "now")
        outcome, run = kit.take("outcome", "run")
        words = "Use the blue theme, and keep the old icons."

        def page(c, questions):
            """The notifier's record of the plan's page in the DM, as KIT-274 keeps it."""
            path = c["notifier_posts_file"]
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                json.dump({"schema": "pipeline-notifier-dm-posts/1", "posts": {
                    key_of(DM, "1759990000.000100"): {
                        "channel": DM, "ts": "1759990000.000100", "ticket_id": "PROD-49",
                        "mark": notify.ESC_AWAITING_APPROVAL, "event_key": "comment:plan-1",
                        "recorded_at": iso(now),
                        "detail": {"plan": "PROD-50", "pieces": 2, "questions": questions}}}},
                    fh)

        def plan_world(name, questions=2, setup=None, **over):
            c, s, t = dmworld(name, **dict(dict(act=True, agent_user_name="Agent"), **over))
            t.add_epic("PROD-50", (("PROD-51", ()), ("PROD-52", ("PROD-51",))))
            if questions is not None:
                page(c, questions)
            if setup:
                setup(c, s, t)
            run(c, s, t)                  # the plan's page gets its approve question
            return c, s, t

        def answer_asks(s):
            return [x for _th, x in in_dm(s) if "Add this answer to the plan?" in x]

        def approve_asks(s):
            return [x for _th, x in in_dm(s) if "Approve this plan?" in x]

        def says(answer_text, name="", **over):
            c, s, t = plan_world(name or "answer-%d" % len(answer_text), **over)
            s.add(ALICE, answer_text, channel=DM)
            run(c, s, t)
            return c, s, t

        def case_added():
            c, s, t = says("answer PROD-50: " + words, "answer-added")
            question = (answer_asks(s) or [""])[-1]
            s.add(ALICE, "yes", channel=DM)
            run(c, s, t)
            written = [d for i, d in t.descriptions if i == "uuid-50"]
            block = written[0][len("What PROD-50 does."):] if written else ""
            return ("PROD-50" in question, "2 open questions" in question,
                    "at the end of its description, dated and naming you" in question,
                    words in question, len(written),
                    bool(written) and written[0].startswith("What PROD-50 does."),
                    words in block, ALICE in block, bool(re.search(r"\d{4}-\d\d-\d\d", block)))
        check("an-answer-line-gets-one-question-and-a-yes-adds-the-words-dated-and-named "
              "(KIT-334)", outcome(case_added),
              (True, True, True, True, 1, True, True, True, True))

        def case_cleaned():
            mention = "<@U0BOB0000|bob>"
            profile = "<https://acme.slack.com/team/U0BOB0000|Bob's profile>"
            c, s, t = says("answer PROD-50: Ask %s, see %s, then go [agent=codex] now."
                           % (mention, profile), "answer-cleaned")
            s.add(ALICE, "yes", channel=DM)
            run(c, s, t)
            text = "".join(d for _i, d in t.descriptions)
            return (bool(text), [w for w in ("<@", "U0BOB0000", "slack.com/team", "profile",
                                             "[agent=codex]") if w in text],
                    "then go" in text)
        check("the-words-lose-mentions-profile-links-and-routing-tags-before-they-are-written "
              "(KIT-334)", outcome(case_cleaned), (True, [], True))

        def case_started():
            c, s, t = says("answer PROD-50: " + words, "answer-started",
                           setup=lambda c_, s_, t_: t_.issues["PROD-51"].update(
                               delegate={"id": "agent-1"}))
            said = [x for _th, x in in_dm(s)][-1]
            return (answer_asks(s), "`replan PROD-50`" in said, "new idea" in said,
                    t.descriptions)
        check("a-plan-with-a-piece-under-way-takes-no-answer-and-says-replan-or-a-new-idea "
              "(KIT-334)", outcome(case_started), ([], True, True, []))

        def case_held():
            def race(t_):
                t_.issues["PROD-50"]["title"] = "Changed by someone else"
            c, s, t = says("answer PROD-50: " + words, "answer-held")
            t.after_describe = race
            s.add(ALICE, "yes", channel=DM)
            run(c, s, t)
            said = [x for _th, x in in_dm(s)]
            return (len(t.descriptions), any("held" in x for x in said),
                    len(approve_asks(s)))
        check("a-change-beside-the-write-holds-the-plan-and-says-so-with-no-new-approve-"
              "question (KIT-334)", outcome(case_held), (1, True, 1))

        def case_reasked():
            c, s, t = plan_world("answer-reasked")
            first = len(approve_asks(s))
            s.add(ALICE, "answer PROD-50: " + words, channel=DM)
            run(c, s, t)
            with open(state_path(c)) as fh:
                st = json.load(fh)["questions"]
            approve_open = sorted(q["status"] for q in st.values() if q["kind"] == "approve")
            s.add(ALICE, "yes", channel=DM)
            run(c, s, t)
            with open(state_path(c)) as fh:
                st = json.load(fh)["questions"]
            now_open = sorted(q["kind"] for q in st.values() if q["status"] == "open")
            return first, approve_open, len(t.descriptions), len(approve_asks(s)), now_open
        check("an-open-approve-question-closes-and-is-asked-again-once-the-answer-is-in "
              "(KIT-334)", outcome(case_reasked), (1, ["expired"], 1, 2, ["approve"]))

        def case_refused():
            got = {}
            c, s, t = says("answer PROD-50", "answer-bare")
            got["bare"] = ([x for _th, x in in_dm(s)][-1].endswith(
                "Send it as `answer PROD-50: <your answer>`."), answer_asks(s))
            key = "xo" + "xb-" + "1" * 12 + "-" + "2" * 12 + "-" + "a" * 24
            c, s, t = says("answer PROD-50: the key is %s" % key, "answer-secret")
            got["secret"] = (any(key in x for _th, x in in_dm(s)), answer_asks(s))
            c, s, t = says("answer PROD-50 use the blue theme", "answer-no-colon")
            got["no-colon"] = ([x for _th, x in in_dm(s)][-1].endswith(
                "Send it as `answer PROD-50: <your answer>`."), answer_asks(s))
            c, s, t = says("answer PROD-6: " + words, "answer-not-a-plan")
            got["neither"] = ("a dev waiting on you" in [x for _th, x in in_dm(s)][-1],
                              answer_asks(s))

            def pinged(c_, s_, t_):
                os.makedirs(os.path.dirname(c_["pings_file"]), exist_ok=True)
                with open(c_["pings_file"], "w") as fh:
                    json.dump({"schema": notify.BRIDGE_PINGS_SCHEMA, "messages": {
                        notify.chat_message_key(DM, "1759990000.000200"): {
                            "channel": DM, "ts": "1759990000.000200", "ticket_id": "PROD-7",
                            "thread_id": "root-7", "event_key": "comment:m7",
                            "comment_id": "m7", "mark": "agent:blocked"}}}, fh)
            c, s, t = says("answer PROD-7: " + words, "answer-dev", setup=pinged)
            # A dev waiting on PROD-7 gets ACT-6's own question, never a plan's (KIT-370).
            got["dev"] = (any("Send your message to the PROD-7 dev as your answer?" in x
                              for _th, x in in_dm(s)), answer_asks(s))
            return got
        check("an-answer-line-with-no-words-a-key-or-no-plan-is-refused-aloud-and-a-waiting-"
              "dev-gets-act-6s-question (KIT-334)", outcome(case_refused),
              {"bare": (True, []), "no-colon": (True, []), "secret": (False, []),
               "neither": (True, []), "dev": (True, [])})

        # ACT-6's dev answer is a question kind of its own, `answer`: this one must never share
        # it, or a yes would reach the wrong act and each would promise the other's terms.
        def case_two_kinds():
            import types
            from pipeline_bridge import Pass
            me = types.SimpleNamespace(cfg={"act": True, "question_ttl_hours": 24,
                                            "relay_max_chars": 1000, "agent_user_name": "A"})
            return (PLAN_ANSWER != "answer", sorted(Pass.promised(me, PLAN_ANSWER)),
                    sorted(Pass.promised(me, "answer")))
        check("a-plan-answer-and-a-dev-answer-are-two-kinds-each-promising-its-own-terms "
              "(KIT-334, ACT-6)", outcome(case_two_kinds),
              (True, ["act", "question_ttl_hours", "writes"],
               ["act", "agent_user_name", "question_ttl_hours", "relay_max_chars"]))

        def case_cut():
            c, s, t = says("answer PROD-50: " + words, "answer-cut")

            def stop(t_):
                t_.after_describe = None
                raise BridgeError("the pass stopped right after the write (simulated)")
            t.after_describe = stop
            s.add(ALICE, "yes", channel=DM)
            run(c, s, t)                  # the write lands, then the pass stops
            run(c, s, t)                  # the next reads it back, and never writes again
            return len(t.descriptions), any("Added your answer" in x for _th, x in in_dm(s))
        check("a-pass-cut-right-after-the-write-reads-it-back-and-never-writes-twice "
              "(KIT-334)", outcome(case_cut), (1, True))

        def case_declined():
            c, s, t = plan_world("answer-declined")
            s.add(ALICE, "answer PROD-50: " + words, channel=DM)
            run(c, s, t)
            s.add(ALICE, "no", channel=DM)
            run(c, s, t)
            return len(approve_asks(s)), t.descriptions
        check("a-no-to-the-answer-asks-the-closed-approve-question-again-and-adds-nothing "
              "(KIT-334)", outcome(case_declined), (2, []))

        # Stage E's review of #305: every way an answer question ends gives back the approve
        # question it closed, but a held plan, which waits on the member's own approve.
        def case_expired():
            c, s, t = plan_world("answer-expired")
            s.add(ALICE, "answer PROD-50: " + words, channel=DM)
            run(c, s, t)
            run(c, s, t, at=now + 25 * 3600)          # the answer question's time runs out
            with open(state_path(c)) as fh:
                st = json.load(fh)["questions"]
            return (len(approve_asks(s)), [q["status"] for q in st.values()
                                           if q["kind"] == PLAN_ANSWER], t.descriptions)
        check("an-answer-question-that-expires-asks-the-closed-approve-question-again "
              "(KIT-334)", outcome(case_expired), (2, ["expired"], []))

        def case_refused_at_yes():
            c, s, t = plan_world("answer-refused-at-yes")
            s.add(ALICE, "answer PROD-50: " + words, channel=DM)
            run(c, s, t)
            t.issues["PROD-50"]["description"] = "Rewritten by someone."
            s.add(ALICE, "yes", channel=DM)
            run(c, s, t)
            return len(approve_asks(s)), t.descriptions
        check("a-yes-refused-because-the-plan-changed-asks-the-approve-question-again "
              "(KIT-334)", outcome(case_refused_at_yes), (2, []))

        FakeWriter, wrun, wworld = kit.take("FakeWriter", "wrun", "wworld")

        def case_writer():
            fw = FakeWriter()
            c, s, t = wworld("answer-writer", dm_user_ids=[ALICE], act=True,
                             agent_user_name="Agent")
            wrun(c, s, t, fw)             # the DM's first read: a baseline
            t.add_epic("PROD-50", (("PROD-51", ()),))
            s.add(ALICE, "answer PROD-50: " + words, channel=DM)
            wrun(c, s, t, fw)
            question = answer_asks(s)
            return len(question), words in (question or [""])[0]
        check("with-the-writer-on-the-answer-question-still-quotes-the-words-word-for-word "
              "(KIT-334)", outcome(case_writer), (1, True))

        def case_off():
            c, s, t = says("answer PROD-50: " + words, "answer-off", act=False)
            question = (answer_asks(s) or [""])[-1]
            s.add(ALICE, "yes", channel=DM)
            run(c, s, t)
            return "acting is switched off here, so nothing is added" in question, t.descriptions
        check("with-acting-off-the-question-says-so-and-a-yes-adds-nothing (KIT-334)",
              outcome(case_off), (True, []))

        def case_changed_and_twice():
            c, s, t = says("answer PROD-50: " + words, "answer-changed")
            t.issues["PROD-50"]["description"] = "Rewritten by someone."
            s.add(ALICE, "yes", channel=DM)
            run(c, s, t)
            changed = (t.descriptions, any("changed since I asked" in x
                                          for _th, x in in_dm(s)))
            c, s, t = says("answer PROD-50: " + words, "answer-twice")
            s.add(ALICE, "yes", channel=DM)
            run(c, s, t)
            s.add(ALICE, "yes PROD-50", channel=DM)
            run(c, s, t)
            return changed, len(t.descriptions)
        check("a-plan-changed-since-the-question-takes-nothing-and-a-second-yes-writes-"
              "nothing-more (KIT-334)", outcome(case_changed_and_twice), (([], True), 1))

        # The plan itself is compared apart from its description (Stage E's review of #305),
        # so it needs its own case: here its title changes, and nothing is written.
        def case_plan_retitled():
            c, s, t = says("answer PROD-50: " + words, "answer-retitled")
            t.issues["PROD-50"]["title"] = "Epic PROD-50, renamed"
            s.add(ALICE, "yes", channel=DM)
            run(c, s, t)
            return t.descriptions, any("changed since I asked" in x for _th, x in in_dm(s))
        check("a-plan-whose-title-changed-since-the-question-takes-nothing (KIT-334)",
              outcome(case_plan_retitled), ([], True))


def cases_plan_moves(kit):
    """A plan paused, resumed or cancelled from Slack (KIT-351): its own question naming the
    plan, a move only on a verified yes, of the plan alone through the one move write, and a
    cancel that says a piece at work keeps going."""
    import inspect
    from pipeline_bridge import ALICE, BOB, DM, state_path
    from pipeline_bridge_config import CREDIT_BIND
    from pipeline_bridge_requests import NAMED_YES_BIND, read_ask
    with kit as _tmp:
        check, now, posted, run, world = kit.take("check", "now", "posted", "run", "world")
        dmworld, dm_lines, tlworld, tl_root, tl_says = kit.take(
            "dmworld", "dm_lines", "tlworld", "tl_root", "tl_says")

        def followed(c):
            return sorted(json.load(open(state_path(c))).get("epics", {}))

        def delegated(t):
            return [i for i, _a in t.delegations]

        def running(name, **over):
            """A plan approved in the channel, its first piece handed over: at work."""
            c, s, t = world(name, act=True, agent_user_name="Agent", epic_wip=1, **over)
            t.add_epic("PROD-50", (("PROD-51", ()), ("PROD-52", ("PROD-51",))))
            run(c, s, t)
            root = s.add(ALICE, "approve PROD-50")
            run(c, s, t)
            s.add(BOB, "yes", thread=root)
            run(c, s, t)
            return c, s, t

        def asked(c, s, t, line, answer="yes", before=None):
            """`line` in the channel, then BOB's `answer` under the question it got, after
            `before` runs on the tracker. The first post the line got."""
            n = len(posted(s))
            req = s.add(ALICE, line)
            run(c, s, t)
            first = posted(s)[n] if len(posted(s)) > n else ""
            if before:
                before(t)
            if answer:
                s.add(BOB, answer, thread=req)
                run(c, s, t)
            return first

        def bind(then, yes="`yes`", where="in this thread", who=ALICE):
            return PLAN_MOVE_BIND % (yes, where, 24, then, CREDIT_BIND % ("<@%s>" % who))

        def said(s, start):
            return [p for p in posted(s) if p.startswith(start)]

        def noted(t, ticket_id):
            return ([b for i, b in t.comments if i == ticket_id] or [""])[-1]

        # 1. PAUSE: its own question names the plan and the piece at work. A yes moves the
        #    plan alone to the backlog, says what keeps going, and nothing more starts; where
        #    the plan was approved it is said once.
        c, s, t = running("pm-pause")
        q = asked(c, s, t, "pause PROD-50")
        check("pause-asks-its-own-question-naming-the-plan-and-its-piece-at-work",
              q, "*Pause this plan?* PROD-50 “Epic PROD-50” on PROD. PROD-51 is at work now, "
                 "and keeps going.\n" + bind(PAUSE_THEN_BIND))
        check("pause-a-yes-moves-the-plan-alone-to-the-backlog",
              t.moves, [("uuid-50", "st-todo"), ("uuid-50", "st-backlog")])
        check("pause-says-what-it-did-and-what-keeps-going", said(s, "Paused PROD-50"),
              ["Paused PROD-50 for <@%s>. None of its pieces will start until you resume it. "
               "PROD-51 is at work now, and keeps going. To go on with it, type `resume "
               "PROD-50`." % BOB])
        check("pause-names-who-said-yes-on-the-plan", noted(t, "uuid-50").split(" at ")[0],
              "Paused in Slack by **%s** (Slack user %s)" % (BOB, BOB))
        t.set_state("PROD-51", "completed")
        run(c, s, t)
        run(c, s, t)
        check("a-paused-plan-starts-nothing-more-and-says-so-once-where-it-was-approved",
              (delegated(t), sum("paused starting" in p for p in posted(s))), (["uuid-51"], 1))
        check("a-paused-plan-is-not-paused-again", asked(c, s, t, "pause PROD-50", answer=None),
              "PROD-50 is already held: it's in Backlog, so none of its pieces start. To go "
              "on with it, type `resume PROD-50`.")

        # 2. RESUME: asked as work that leads to merging; a yes moves the plan back to ready,
        #    and its next piece starts in the same pass.
        q = asked(c, s, t, "resume PROD-50")
        check("resume-asks-its-own-question-saying-merging-stays-yours",
              q.endswith("\n" + bind(RESUME_THEN_BIND % 1)), True)
        check("resume-a-yes-moves-the-plan-to-ready-and-its-pieces-go-on",
              (t.moves[-1], delegated(t), said(s, "Resumed PROD-50")),
              (("uuid-50", "st-todo"), ["uuid-51", "uuid-52"],
               ["Resumed PROD-50 for <@%s>. I'll go on handing its pieces to the agent, at "
                "most 1 at a time." % BOB]))
        check("a-running-plan-is-not-resumed", asked(c, s, t, "resume PROD-50", answer=None),
              "PROD-50 isn't paused: it's in Todo, and its pieces go on.")

        # 3. CANCEL: says a piece at work keeps going; a yes moves the plan alone, lets it
        #    go, and says so where it was approved. Nothing more starts, and no piece moves.
        c, s, t = running("pm-cancel")
        q = asked(c, s, t, "cancel PROD-50")
        check("cancel-asks-its-own-question-saying-a-piece-at-work-keeps-going",
              q, "*Cancel this plan?* PROD-50 “Epic PROD-50” on PROD. PROD-51 is at work "
                 "now, and keeps going.\n" + bind(CANCEL_THEN_BIND))
        check("cancel-a-yes-moves-the-plan-alone-and-lets-it-go", (t.moves, followed(c)),
              ([("uuid-50", "st-todo"), ("uuid-50", "st-canceled")], []))
        check("cancel-says-what-keeps-going-and-stops-where-it-was-approved",
              (said(s, "Cancelled PROD-50"), said(s, "PROD-50 was cancelled from Slack")),
              (["Cancelled PROD-50 for <@%s>. I'll start none of its pieces again. PROD-51 is "
                "at work now, and keeps going." % BOB],
               ["PROD-50 was cancelled from Slack by <@%s>, so I've stopped starting its "
                "pieces." % BOB]))
        check("cancel-tells-linear-its-pieces-were-left-as-they-are",
              noted(t, "uuid-50").split("\n\n")[1:],
              ["Its pieces were left as they are, so one already at work keeps going."])
        t.set_state("PROD-51", "completed")
        run(c, s, t)
        check("a-cancelled-plan-starts-nothing-more-and-never-moves-a-piece",
              (delegated(t), [i for i, _st in t.moves if i != "uuid-50"]), (["uuid-51"], []))
        check("a-cancelled-plan-is-run-no-more-and-a-cancel-says-it-is-cancelled-already",
              (asked(c, s, t, "cancel PROD-50", answer="no"), said(s, "OK, I won't")),
              ("PROD-50 is already in Canceled.", []))
        c, s, t = running("pm-no")
        asked(c, s, t, "cancel PROD-50", answer="no")
        check("a-no-leaves-a-running-plan-as-it-is",
              (said(s, "OK, I won't"), t.moves[-1], followed(c)),
              (["OK, I won't cancel PROD-50."], ("uuid-50", "st-todo"), ["uuid-50"]))

        # 4. In the DM where it was approved, a pause is asked and answered there, and the
        #    reply is the one line said: the next pass does not say it again.
        #    Her own words are read as the typed line is (ACT-3).
        def in_dm(name, line):
            c, s, t = dmworld(name, act=True, agent_user_name="Agent", epic_wip=1)
            t.add_epic("PROD-50", (("PROD-51", ()), ("PROD-52", ("PROD-51",))))
            for each in ("approve PROD-50", "yes", line):
                s.add(ALICE, each, channel=DM)
                run(c, s, t)
            q = dm_lines(s)[-1]
            s.add(ALICE, "yes", channel=DM)
            run(c, s, t)
            t.set_state("PROD-51", "completed")
            run(c, s, t)
            return c, s, t, q
        c, s, t, q = in_dm("pm-dm", "please pause PROD-50")
        check("in-the-dm-a-pause-in-her-own-words-is-asked-here-and-said-once",
              (q.endswith("\n" + bind(PAUSE_THEN_BIND, where="here")),
               t.moves[-1], delegated(t), sum("paused" in p.lower() for p in dm_lines(s))),
              (True, ("uuid-50", "st-backlog"), ["uuid-51"], 1))
        c, s, t, q = in_dm("pm-dm-cancel", "cancel PROD-50")
        check("in-the-dm-a-cancel-is-said-once",
              (t.moves[-1], followed(c), [p for p in dm_lines(s)
                                          if "stopped" in p or p.startswith("Cancelled")]),
              (("uuid-50", "st-canceled"), [],
               ["Cancelled PROD-50 for <@%s>. I'll start none of its pieces again. PROD-51 is "
                "at work now, and keeps going." % ALICE]))
        check("her-own-words-name-each-verb",
              [(read_ask(text) or {}).get("verb") for text in (
                  "please pause PROD-50", "unpause PROD-50", "resume the plan PROD-50",
                  "ok cancel PROD-50 thanks", "cancel PROD-50, the tech lead suggested it")],
              ["pause", "resume", "resume", "cancel", None])
        # On purpose for every verb: an ask that credits the tech lead or a suggestion is
        # the tech lead's to set up, so its question names who picked the ticket, and no
        # direct path can ever act on it. Plan and replan included (Stage E review of #302).
        check("an-ask-crediting-the-tech-lead-is-left-to-it-whatever-the-verb",
              [(read_ask(text) or {}).get("verb") for text in (
                  "please plan PROD-6", "please plan PROD-6, the tech lead suggested it",
                  "replan PROD-6, as the tech lead suggested", "approve PROD-50, as suggested")],
              ["plan", None, None, None])

        # 5. What it refuses, in one line each: a plan it does not run (a cancel says where to
        #    drop one), a piece of one it runs, a finished one, and a yes for a plan that has
        #    moved since the question.
        c, s, t = world("pm-none", act=True, agent_user_name="Agent")
        t.add_epic("PROD-50", (("PROD-51", ()),))
        run(c, s, t)
        check("a-plan-it-is-not-running-is-refused",
              [asked(c, s, t, line, answer=None) for line in ("pause PROD-50", "resume PROD-50")],
              ["I'm not running a plan for PROD-50, so there's nothing for me to %s." % kind
               for kind in ("pause", "resume")])
        c, s, t = running("pm-piece")
        check("a-piece-gets-the-plan-to-name", asked(c, s, t, "pause PROD-51", answer=None),
              "PROD-51 is a piece of PROD-50, the plan I'm running. To pause the plan, type "
              "`pause PROD-50`.")
        t.set_state("PROD-50", "completed")
        check("a-finished-plan-is-refused", asked(c, s, t, "cancel PROD-50", answer=None),
              "PROD-50 is in Done, so its plan is finished.")
        c, s, t = running("pm-moved")
        asked(c, s, t, "pause PROD-50")
        asked(c, s, t, "resume PROD-50", before=lambda t_: t_.set_state(
            "PROD-50", "canceled", actor="someone-else"))
        check("a-yes-for-a-plan-moved-since-the-question-moves-nothing",
              (said(s, "PROD-50 moved since I asked"), t.moves[-1], followed(c)),
              (["PROD-50 moved since I asked, so nothing was moved, and I have closed the "
                "question. Ask again with `resume PROD-50`."], ("uuid-50", "st-backlog"),
               ["uuid-50"]))
        # His own cancel of a plan someone else cancelled lets it go, with no move to make;
        # his resume of one moves it back, and it goes on.
        asked(c, s, t, "cancel PROD-50")
        check("a-cancel-of-a-plan-someone-else-cancelled-lets-it-go-and-moves-nothing",
              (t.moves[-1], followed(c)), (("uuid-50", "st-backlog"), []))
        c, s, t = running("pm-unhold")
        t.set_state("PROD-50", "canceled", actor="someone-else")
        t.set_state("PROD-51", "completed")
        run(c, s, t)
        asked(c, s, t, "resume PROD-50")
        check("a-resume-of-a-plan-someone-else-cancelled-moves-it-back-and-it-goes-on",
              (t.moves[-1], delegated(t)), (("uuid-50", "st-todo"), ["uuid-51", "uuid-52"]))
        # Its own pause is the owner's, whatever the history shows yet: never called held.
        c, s, t = running("pm-own-move")
        t.move_actor = None
        asked(c, s, t, "pause PROD-50")
        run(c, s, t)
        check("its-own-pause-is-trusted-before-the-history-shows-who-moved-it",
              (sum("paused starting" in p for p in posted(s)),
               sum("but not by you" in p for p in posted(s))), (1, 0))

        # 6. With acting off, the question says it only notes the answer, and nothing moves.
        c, s, t = running("pm-off")
        c["act"] = False
        q = asked(c, s, t, "cancel PROD-50")
        check("with-acting-off-a-cancel-is-only-noted-and-nothing-moves",
              (q.endswith("\n" + bind(APPROVE_OFF_THEN_BIND)),
               t.moves[-1], followed(c), "switched off" in posted(s)[-1]),
              (True, ("uuid-50", "st-todo"), ["uuid-50"], True))

        # 7. As the tech lead's line (ACT-1): a pause it set up takes a bare yes; a cancel it
        #    set up acts only on `yes <the plan>`, as a resume does.
        def tl_set_up(name, line):
            c, s, t = tlworld(name, act=True, agent_user_name="Agent", epic_wip=1)
            t.add_epic("PROD-50", (("PROD-51", ()), ("PROD-52", ("PROD-51",))))
            root = s.add(BOB, "approve PROD-50")
            run(c, s, t)
            s.add(BOB, "yes", thread=root)
            run(c, s, t)
            s.add(ALICE, "can we hold the light mode work for now", channel=DM)
            run(c, s, t, at=now + 60)
            tl_says(s, tl_root(c), "For ask 1:\n%s" % line)
            run(c, s, t, at=now + 120)
            return c, s, t, dm_lines(s)[-1]
        c, s, t, q = tl_set_up("pm-tl-pause", "pause PROD-50")
        s.add(ALICE, "yes", channel=DM)
        run(c, s, t, at=now + 180)
        check("a-pause-the-tech-lead-set-up-takes-a-bare-yes",
              ("Reply `yes` here" in q, q.endswith("The tech lead picked PROD-50."),
               t.moves[-1]), (True, True, ("uuid-50", "st-backlog")))
        c, s, t, q = tl_set_up("pm-tl-cancel", "cancel PROD-50")
        s.add(ALICE, "yes", channel=DM)
        run(c, s, t, at=now + 180)
        bare = (t.moves[-1], dm_lines(s)[-1].endswith(NAMED_YES_BIND % ("PROD-50", "PROD-50")))
        s.add(ALICE, "yes PROD-50", channel=DM)
        run(c, s, t, at=now + 240)
        check("a-cancel-the-tech-lead-set-up-acts-only-on-yes-with-the-plan",
              ("Reply `yes PROD-50` here" in q, bare, t.moves[-1], followed(c)),
              (True, (("uuid-50", "st-todo"), True), ("uuid-50", "st-canceled"), []))

        # 8. A yes past the hour's limit of actions acts on nothing, and says so; a dry run
        #    moves nothing; a comment that fails leaves the move standing, says so, and is
        #    owed; a resume asked before a change to how many start at once moves nothing.
        c, s, t = running("pm-limit", max_actions_per_hour=2)
        asked(c, s, t, "pause PROD-50")
        check("a-yes-past-the-hours-limit-of-actions-moves-nothing",
              (t.moves[-1], said(s, "I have acted 2 times in the last hour")),
              (("uuid-50", "st-todo"), ["I have acted 2 times in the last hour, which is my "
                                        "limit. Say yes again later."]))
        c, s, t = running("pm-dry")
        req = s.add(ALICE, "pause PROD-50")
        run(c, s, t)
        s.add(BOB, "yes", thread=req)
        run(c, s, t, dry=True)
        check("a-dry-run-moves-nothing", t.moves[-1], ("uuid-50", "st-todo"))
        c, s, t = running("pm-owed")
        t.comment = kit.take("refusing_comment")[0]
        asked(c, s, t, "cancel PROD-50")
        check("a-failed-comment-leaves-the-move-standing-said-and-owed",
              (t.moves[-1], (said(s, "Cancelled PROD-50") or [""])[0].endswith(
                  "The move was done, but the comment naming who said yes could not be "
                  "written; I will try again."),
               [o["ticket"] for o in json.load(open(state_path(c)))["owed"]]),
              (("uuid-50", "st-canceled"), True, ["PROD-50"]))
        c, s, t = running("pm-terms")
        asked(c, s, t, "pause PROD-50")
        asked(c, s, t, "resume PROD-50", before=lambda _t: c.update(epic_wip=2))
        check("a-resume-asked-before-its-terms-changed-moves-nothing",
              (t.moves[-1], said(s, "My settings changed since I asked about PROD-50")
               != []), (("uuid-50", "st-backlog"), True))

        # 9. ONE MOVE WRITE: the three share one call of the bridge's one move, and asking
        #    reaches none; so does a stop to planning, and a re-plan's cancel (KIT-444). The
        #    pins on the four writes stay as they were.
        def source(name, mixin=PlansMixin):
            fn = getattr(mixin, name, None)             # a missing one is a failed case
            return inspect.getsource(fn) if fn else ""
        import pipeline_bridge_replan as replan_module
        check("pause-resume-and-cancel-share-one-call-of-the-one-move-write",
              ([source(n).count(".move(") for n in ("move_plan", "act_plan_move",
                                                    "request_plan_move")],
               source("act_plan_move").count("self.move_plan("),
               source("cancel_plans", replan_module.ReplanMixin).count("self.move_plan("),
               "mutation" in source("move_plan")), ([1, 0, 0], 1, 1, False))

        # 10. KIT-444: a cancel stops planning an idea, and reaches a plan nobody approved.
        def idea_world(name, run_kind=None, act=True):
            c, s, t = world(name, act=act, agent_user_name="Agent")
            t.in_plan_it("PROD-6")
            if run_kind:
                t.add_run("PROD-6", "PROD-51", run_kind)
            run(c, s, t)
            return c, s, t
        c, s, t = idea_world("pm-stop-idle")
        q = asked(c, s, t, "cancel PROD-6")
        check("a-cancel-on-an-idea-asks-to-stop-planning-it",
              q, "*Stop planning this idea?* PROD-6 “Light mode” on PROD. No planning run is "
                 "going.\n" + bind(STOP_PLANNING_THEN_BIND))
        check("a-yes-moves-the-idea-alone-to-the-backlog-and-says-how-to-plan-it-later",
              (t.moves, said(s, "Stopped planning PROD-6"),
               noted(t, "uuid-6").split("\n\n")[1:]),
              ([("uuid-6", "st-backlog")],
               ["Stopped planning PROD-6 for <@%s>. No new planning run starts. No planning "
                "run was going. When you want it planned, type `plan PROD-6`." % BOB],
               ["A planning run already going keeps going, and still files its plan."]))
        c, s, t = idea_world("pm-stop-running", run_kind="started")
        q = asked(c, s, t, "cancel PROD-6")
        check("a-stop-says-plainly-that-a-run-already-going-keeps-going",
              (q.split("\n")[0], (said(s, "Stopped planning PROD-6") or [""])[0].split(". ")[2:4]),
              ("*Stop planning this idea?* PROD-6 “Light mode” on PROD. PROD-51 is planning "
               "it now, and keeps going: its plan is still filed when it ends.",
               ["PROD-51 is planning it now, and keeps going: its plan is still filed when it "
                "ends", "When you want it planned, type `plan PROD-6`."]))
        c, s, t = idea_world("pm-stop-moved")
        asked(c, s, t, "cancel PROD-6", before=lambda t_: t_.set_state("PROD-6", "backlog"))
        check("a-stop-for-an-idea-moved-since-the-question-moves-nothing",
              (t.moves, said(s, "PROD-6 moved since I asked")),
              ([], ["PROD-6 moved since I asked, so nothing was moved, and I have closed the "
                    "question. Ask again with `cancel PROD-6`."]))
        c, s, t = idea_world("pm-stop-off", act=False)
        check("with-acting-off-a-stop-is-only-noted-and-nothing-moves",
              (asked(c, s, t, "cancel PROD-6").endswith("\n" + bind(APPROVE_OFF_THEN_BIND)),
               t.moves), (True, []))
        c, s, t = world("pm-unapproved", act=True, agent_user_name="Agent")
        t.add_epic("PROD-50", (("PROD-51", ()), ("PROD-52", ("PROD-51",))))
        run(c, s, t)
        q = asked(c, s, t, "cancel PROD-50")
        check("a-cancel-reaches-a-plan-nobody-approved",
              (q.endswith("\n" + bind(CANCEL_THEN_BIND)), t.moves, followed(c),
               said(s, "Cancelled PROD-50")),
              (True, [("uuid-50", "st-canceled")], [],
               ["Cancelled PROD-50 for <@%s>. I'll start none of its pieces again. None of its "
                "pieces is at work now." % BOB]))
        for name, setup in (
                ("pm-not-the-owners", lambda t_: t_.add_epic(
                    "PROD-50", (("PROD-51", ()),), creator="someone-else")),
                ("pm-agents-mark", lambda t_: (t_.add_epic("PROD-50", (("PROD-51", ()),),
                                                           epic_labels=()),
                                               t_.add_label("PROD-50", "provenance:agent",
                                                            actor="agent-1")))):
            c, s, t = world(name, act=True, agent_user_name="Agent")
            setup(t)
            run(c, s, t)
            check("a-cancel-refuses-what-the-idea-gate-did-not-file: " + name,
                  (asked(c, s, t, "cancel PROD-50", answer=None), t.moves),
                  ("PROD-50 isn't a plan the idea gate filed, or an idea being planned, so "
                   "there's nothing for me to cancel.", []))


def selftest():
    import pipeline_bridge
    return pipeline_bridge.split_module_selftest(os.path.abspath(__file__),
                                                 [cases_approve, cases_plan_moves,
                                                  cases_finish, cases_ready_plans,
                                                  cases_answer])


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        # As the module the bridge imports, never a copy of it named __main__, so its
        # cases and the pass share one copy of it (KIT-312).
        import pipeline_bridge_plans
        sys.exit(pipeline_bridge_plans.selftest())
    print("usage: pipeline_bridge_plans.py --selftest (the bridge imports this module)")
    sys.exit(EXIT_USAGE)
