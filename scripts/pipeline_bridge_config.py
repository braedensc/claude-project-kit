#!/usr/bin/env python3
"""The Slack bridge's settings, its files, and the names every part of it shares.

    python3 scripts/pipeline_bridge_config.py --selftest

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
  The config     its keys, its example, and `validate_config`, which names every problem
                 at once; the two credentials, read from the env vars the config names.
  The job's files  its state (mode 600), its heartbeat and its lock; the notifier's record
                 of the pings it sent, and of the pages it posted into a DM.
  Shared names   the exit codes, a question's statuses, the ticket-id pattern, a message's
                 key, how a planning run names its idea, and the clock's words.
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pipeline_notify_local as notify  # noqa: E402
import pipeline_plan_poller as poller  # noqa: E402
import pipeline_voice as voice  # noqa: E402
import pipeline_tech_lead_turn as turn  # noqa: E402  (KIT-489: the turn's own keys and rules)
from pipeline_bridge_transport import BridgeError  # noqa: E402

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_DECLINED, EXIT_TIMEOUT = (
    notify.EXIT_OK, notify.EXIT_ERROR, notify.EXIT_USAGE, notify.EXIT_DECLINED,
    notify.EXIT_TIMEOUT)

CONFIG_SCHEMA = "pipeline-bridge-config/1"
STATE_SCHEMA = "pipeline-bridge-state/1"
HEARTBEAT_SCHEMA = "pipeline-bridge-heartbeat/1"
DEFAULT_STATE_DIR = "~/.stage-e/state"
# The lane a child sits in while its pull request waits for the owner's merge: the
# needs-approval lane (contract §1, `linear.stateIds.needsApproval`), named as setup-board
# names it. Matched by name, like `plan_it_state` and `ready_state`.
DEFAULT_HANDOFF_STATES = ["Needs Approval"]
# The done state a plan moves to once every piece of it is merged (KIT-299), by name.
DEFAULT_DONE_STATE = "Done"
# KIT-343: the hosts a link in a tech-lead answer may go to and stay a link in the DM, each
# with its subdomains: the owner's Linear and GitHub. Any other link reaches the DM as plain
# text. A host name of two labels or more, lower case, so no entry can be a whole TLD.
DEFAULT_TECH_LEAD_LINK_HOSTS = ["linear.app", "github.com"]
LINK_HOST_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
                          r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
# KIT-356: the tech lead's wake. Its day's cap stops only a runaway (the owner, 2 Nov 2026):
# a normal day is well under 20. Its quiet hours hold a wake until morning, never drop it.
DEFAULT_WAKE_MAX_PER_DAY = 100
WAKE_MAX_PER_DAY_TOP = 1000
DEFAULT_WAKE_QUIET_HOURS = "21:00-08:00"
DEFAULT_WAKE_PR_STUCK_HOURS = 6
QUIET_HOURS_RE = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])-([01][0-9]|2[0-3]):([0-5][0-9])$")


def quiet_bounds(spec):
    """(start, end) in minutes after midnight for quiet hours written HH:MM-HH:MM, or None
    for anything else, the empty "none" included. The two times must differ."""
    m = QUIET_HOURS_RE.match(spec) if isinstance(spec, str) else None
    if not m:
        return None
    start = int(m.group(1)) * 60 + int(m.group(2))
    end = int(m.group(3)) * 60 + int(m.group(4))
    return None if start == end else (start, end)

# The hourly limits stop only a runaway, never a normal hour (the owner, 2 Nov 2026,
# KIT-357): three times the first proposal of 100 lookups, 30 actions and 30 answers.
# The bridge keeps its own answer limit, so the notifier's relay keeps its default.
DEFAULT_MAX_QUESTIONS_PER_HOUR = 300
DEFAULT_MAX_ACTIONS_PER_HOUR = 90
DEFAULT_RELAY_MAX_PER_HOUR = 90
# KIT-340: an unanswered DM question gets ONE reminder this many hours after it was asked. One
# per question is the cap: a reminder is never repeated.
DEFAULT_QUESTION_REMINDER_HOURS = 4
# MVP 2, KIT-454: how often the queue is asked. Each ask reads the tracker whole, so it is
# minutes, not every pass; a lane that frees waits at most this long.
DEFAULT_QUEUE_EVERY_SECONDS = 900
# KIT-295: the notifier's record of the pages it posts into a DM as the bridge's own app.
# Its pages look like the bridge's own posts there, so the bridge reads which ones it made.
NOTIFIER_DM_POSTS_SCHEMA = "pipeline-notifier-dm-posts/1"
NOTIFIER_DM_POSTS_NAME = "notifier-dm-posts.json"
# KIT-297 review: the writer waits only while the pass can still post the message plain
# after it gives up: one Slack call at its own 20 s limit, and a little more. With less
# than WRITER_MIN_SECONDS of that left, the plain message goes at once.
WRITER_POST_SECONDS = 25
WRITER_MIN_SECONDS = 10
FORWARD_HISTORY_MESSAGES_MAX = 100         # forward_history_messages, at most
FORWARD_HISTORY_CHARS_MAX = 20000          # forward_history_chars, at most
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
CONFIG_KEYS = {
    "schema": "the config's schema id, %s" % CONFIG_SCHEMA,
    "state_dir": "where state and heartbeat live (default %s); outside every git worktree"
                 % DEFAULT_STATE_DIR,
    "chat_api_base": "the Slack Web API base (default https://slack.com/api)",
    "slack_token_env": "the NAME of the env var holding the bridge app's bot token",
    "linear_key_env": "the NAME of the env var holding the owner's tracker key",
    "channel_ids": "the private channel id(s) the bridge reads and answers in",
    "owner_user_id": "the tracker user id the key must belong to",
    "bot_user_id": "the bridge app's own Slack bot user id; a token answering to anyone "
                   "else is refused",
    "max_open_questions": "how many questions may be open at once (default 10)",
    "team_keys": "the work teams whose tickets the bridge will ask about",
    "allowed_slack_users": "optional: the only Slack user ids who may answer; empty means "
                           "any full member of the workspace",
    "question_ttl_hours": "how long a question can be answered (default 24)",
    "question_reminder_hours": "after how many hours a DM question not yet answered gets its "
                               "one reminder (default 4; 0: none; less than "
                               "question_ttl_hours, KIT-340)",
    "lookback_days": "how far back each pass reads the channel (default 7)",
    "max_questions_per_hour": "how many requests the bridge looks up in the tracker in an "
                              "hour, found or not (default %d, a runaway's limit)"
                              % DEFAULT_MAX_QUESTIONS_PER_HOUR,
    "max_history_pages": "how many pages of channel history one pass may read (default 10)",
    "run_timeout_seconds": "wall clock for one pass (default 120; with the writer on, at "
                           "least writer_timeout_seconds + 25)",
    "act": "whether a verified yes acts in the tracker (default false: ask and record only)",
    "plan_it_state": "the name of each team's Plan it state (default Plan it)",
    "max_actions_per_hour": "how many actions the bridge takes in an hour, each child "
                            "started included (default %d, a runaway's limit)"
                            % DEFAULT_MAX_ACTIONS_PER_HOUR,
    "ready_state": "the name of each team's to-do state an approved epic moves to "
                   "(default Todo)",
    "done_state": "the name of each team's done state a plan moves to once every piece of "
                  "it is merged (default %s, KIT-299)" % DEFAULT_DONE_STATE,
    "agent_user_name": "the dispatcher's app user, by display name, that an approved "
                       "epic's children are handed to; empty refuses every approve",
    "epic_wip": "how many children of one approved epic may be started and unfinished at "
                "once (default 1, at most 10)",
    "handoff_states": "the states, by name, a child sits in while its pull request waits "
                      "for your merge (default %s, the lane setup-board makes)"
                      % json.dumps(DEFAULT_HANDOFF_STATES),
    "queue_config": "the queue's config file (MVP 2, KIT-454). With it, the bridge asks the "
                    "queue what may start now, inside the working window, and starts that, "
                    "approved plans' pieces included; empty (the default) keeps the bridge's "
                    "own hand-over of approved plans, as before",
    "queue_every_seconds": "how often the queue is asked, in seconds (default %d, at least 60)"
                           % DEFAULT_QUEUE_EVERY_SECONDS,
    "pings_file": "the notifier's record of agent:blocked pings (default "
                  "<state_dir>/notifier-bridge-pings.json, where the notifier writes it)",
    "relay_max_chars": "a longer answer is refused, never cut (default the relay's, 1000)",
    "relay_max_per_hour": "answers passed on in an hour (default %d, a runaway's limit)"
                          % DEFAULT_RELAY_MAX_PER_HOUR,
    "notifier_bot_user_id": "optional: the notifier app's own Slack bot user id. A member's "
                            "reply under one of its posts that the pings file does not hold "
                            "is then refused aloud, never ignored",
    "notifier_plist": "optional: the notifier's LaunchDaemon plist. Set, each pass checks the "
                      "notifier's heartbeat beside pings_file against how often that plist "
                      "runs it, and says when it goes quiet or keeps failing (KIT-285): in each "
                      "listed member's DM with dm_user_ids set (KIT-332), else in the first "
                      "channel. Absent, no notifier is watched",
    "run_interval_seconds": "optional: how often launchd runs this job, its plist's "
                            "StartInterval (default 60). The notifier watch reads a gap in "
                            "its own passes of over twice this plus run_timeout_seconds as "
                            "the Mac asleep (KIT-285)",
    "dm_user_ids": "optional: the Slack user ids whose DM with the bridge app it also reads "
                   "and answers in, always as messages of their own, so each one notifies "
                   "(KIT-295). Empty, the default: no DM is read",
    "notifier_posts_file": "the notifier's record of the pages it posts into a DM as this "
                           "app (default <state_dir>/%s, where the notifier writes it). Read "
                           "only with dm_user_ids set; outside every git worktree"
                           % NOTIFIER_DM_POSTS_NAME,
    "push_mode": "optional (KIT-560): true lets the push-connection job (pipeline_bridge_push.py) "
                   "hold Slack's push connection and answer an owner's DM at once. The regular "
                   "pass stays as the catch-up. Off by default",
    "push_token_env": "the NAME of the env var holding the app-level token (xapp-) the push "
                        "connection job opens its connection with (KIT-560); asked at a hidden prompt, "
                        "never printed",
    "ask_tech_lead": "optional: true passes what a member writes in their DM, other than a "
                     "command or a yes or no, to the dispatcher's chat bot in "
                     "back_room_channel_id, and its answer back to the DM (KIT-298). "
                     "Default false: that text gets the one line on what the bridge can do",
    "chat_bot_user_id": "the dispatcher's chat bot's Slack user id, the one account the "
                        "bridge ever mentions; required with ask_tech_lead on",
    "back_room_channel_id": "the channel, one of channel_ids, where a DM question is passed "
                            "to the chat bot; required with ask_tech_lead on",
    "forward_max_per_hour": "how many DM questions are passed on in an hour, a guard against "
                            "a runaway only (default 100)",
    "forward_follow_minutes": "how long after a question or its answer the next DM message "
                              "goes into the same back-room thread, where the tech lead still "
                              "holds its own working (default 60). Memory never depends on it: "
                              "a new thread carries the DM's recent conversation",
    "forward_answer_minutes": "how long the bridge waits for the chat bot's answer before it "
                              "says none came (default 10)",
    "direct_asks": "optional: true lets a listed member's own DM ask, read by the fixed "
                   "rules as plan or replan with up to direct_max_tickets tickets, act "
                   "direct_grace_seconds after the bridge says what it will do, with no "
                   "question; any other message from them first stops it (ACT-4). Needs act, "
                   "dm_user_ids and agent_user_name. Default false",
    "direct_grace_seconds": "the stop window before a direct ask acts (default 90)",
    "direct_max_age_minutes": "a direct ask first read later than this after it was sent "
                              "gets a question instead (default 10)",
    "direct_max_tickets": "the most tickets one direct ask acts on; more get one combined "
                          "question (default 3, at most 10)",
    "forward_history_messages": "how many of the DM's newest messages before a question go "
                                "under it when it opens a new back-room thread, whatever their "
                                "age (default 30, at most %d; 0: none)"
                                % FORWARD_HISTORY_MESSAGES_MAX,
    "forward_history_chars": "that block's size; its oldest lines go first past it (default "
                             "8000, at most %d)" % FORWARD_HISTORY_CHARS_MAX,
    "tech_lead_link_hosts": "the hosts, each with its subdomains, a link in the tech lead's "
                            "answer may go to and stay a link in the DM; any other reaches it "
                            "as plain text (default %s, KIT-343)"
                            % json.dumps(DEFAULT_TECH_LEAD_LINK_HOSTS),
    "tech_lead_wake": "optional: true wakes the tech lead at set moments, once each, so it "
                      "reaches out first (KIT-356): a rundown or wrap that went out, a plan "
                      "waiting on the owner's answer, planning that stopped for good, a stuck "
                      "pull request. Needs ask_tech_lead on and dm_user_ids. Default false",
    "wake_max_per_day": "how many times a day the tech lead is woken, a guard against a "
                        "runaway only (default %d; a normal day is well under 20)"
                        % DEFAULT_WAKE_MAX_PER_DAY,
    "wake_quiet_hours": "local HH:MM-HH:MM when a wake waits until they end (default %s); "
                        "\"\" for none. They delay a wake, never drop it"
                        % json.dumps(DEFAULT_WAKE_QUIET_HOURS),
    "wake_pr_stuck_hours": "how long a pull request waits since its newest review asked for "
                           "fixes, could not run or left files unread, with nothing pushed, "
                           "before it wakes the tech lead (default %d)"
                           % DEFAULT_WAKE_PR_STUCK_HOURS,
    "wake_status_file": "the status job's file that the stuck pull request is read from "
                        "(default: status/pipeline-status.json beside state_dir, the status "
                        "job's own)",
}
# KIT-297: the writer's own keys, as the writer documents and checks them (pipeline_voice).
CONFIG_KEYS.update(voice.CONFIG_KEYS)
# KIT-489: the tech lead's turn, by its own module's keys. Off by default.
CONFIG_KEYS.update(turn.CONFIG_KEYS)

EXAMPLE_CONFIG = {
    "schema": CONFIG_SCHEMA,
    "state_dir": DEFAULT_STATE_DIR,
    "slack_token_env": "BRIDGE_SLACK_BOT_TOKEN",
    "linear_key_env": "STAGE_E_LINEAR_API_KEY",
    "channel_ids": ["C0123456789"],
    "owner_user_id": "00000000-0000-4000-8000-000000000001",
    "bot_user_id": "U0123456789",
    "team_keys": ["PROD"],
    "allowed_slack_users": [],
    "dm_user_ids": [],
    "question_ttl_hours": 24,
    "lookback_days": 7,
    "max_questions_per_hour": DEFAULT_MAX_QUESTIONS_PER_HOUR,
    "act": False,
    "done_state": DEFAULT_DONE_STATE,
    "ask_tech_lead": False,
    "push_mode": False,
    "push_token_env": "BRIDGE_PUSH_APP_TOKEN",
}

ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
CHANNEL_RE = re.compile(r"^[CG][A-Z0-9]{8,}$")
DM_RE = re.compile(r"^D[A-Z0-9]{8,}$")          # a member's DM with the app (KIT-295)
SLACK_USER_RE = re.compile(r"^[UW][A-Z0-9]{8,}$")
TEAM_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]{0,9}$")
TICKET_RE = r"[A-Za-z][A-Za-z0-9]{0,9}-\d{1,7}"
# A ticket id as a whole word: each one in a message the writer must keep (KIT-297).
_TICKET_WORD_RE = re.compile(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % TICKET_RE)

OPEN, CONFIRMED, DECLINED, EXPIRED = "open", "confirmed", "declined", "expired"
DONE, REFUSED = "done", "refused"
# The bind after a question closed unanswered, for every kind of request (KIT-342). Every
# bind sentence is fixed text, pinned word for word by the bridge's selftest.
ASK_AGAIN_BIND = "Ask again with `%s %s`."
# How a question's yes is typed, and who asked for it (ACT-1, KIT-364): a bare `yes`, or the
# ticket named when the tech lead picked it.
YES_BIND = "`yes`"
NAMED_YES_WORD_BIND = "`yes %s`"
CREDIT_BIND = "(Asked for by %s.)"


def credit_terms(terms, who):
    """(yes, credit): how a question's yes must be typed and who it credits (ACT-1). `terms`
    is (credit, the ticket a yes must name or None) from the request, or None: then a bare
    yes, credited to `who`."""
    credit, named_yes = terms or (None, None)
    return (NAMED_YES_WORD_BIND % named_yes if named_yes else YES_BIND,
            credit or CREDIT_BIND % who)
# How often launchd runs this job, and the longest one pass may take, when the config does
# not say. The watch reads a longer gap in its own passes as the Mac asleep (sleep_gap).
DEFAULT_RUN_INTERVAL, DEFAULT_RUN_TIMEOUT = 60, 120
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov",
           "Dec")


class Deadline(BaseException):
    """BaseException on purpose: no handler below may swallow the wall clock."""


# ══════════════════════════════════════════════════════════════════════════════════════
# Config, credentials, state
# ══════════════════════════════════════════════════════════════════════════════════════

def load_config(path):
    """Read the config and report EVERY problem at once."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        raise BridgeError("could not read --config %s: %s" % (path, exc), EXIT_USAGE)
    if not isinstance(raw, dict):
        raise BridgeError("--config must hold one JSON object", EXIT_USAGE)
    return validate_config(raw)


def validate_config(raw):
    errors = []
    unknown = sorted(set(raw) - set(CONFIG_KEYS))
    if unknown:
        errors.append("unknown config key(s): %s" % ", ".join(unknown))
    cfg = {
        "schema": raw.get("schema"),
        "state_dir": os.path.realpath(os.path.expanduser(raw.get("state_dir")
                                                         or DEFAULT_STATE_DIR)),
        "chat_api_base": (raw.get("chat_api_base") or "https://slack.com/api").rstrip("/"),
        "slack_token_env": raw.get("slack_token_env") or "BRIDGE_SLACK_BOT_TOKEN",
        "linear_key_env": raw.get("linear_key_env") or "STAGE_E_LINEAR_API_KEY",
        "channel_ids": raw.get("channel_ids", []),
        "owner_user_id": raw.get("owner_user_id") or "",
        "bot_user_id": raw.get("bot_user_id") or "",
        "max_open_questions": raw.get("max_open_questions", 10),
        "team_keys": raw.get("team_keys", []),
        "allowed_slack_users": raw.get("allowed_slack_users", []),
        "question_ttl_hours": raw.get("question_ttl_hours", 24),
        "question_reminder_hours": raw.get("question_reminder_hours",
                                           DEFAULT_QUESTION_REMINDER_HOURS),
        "lookback_days": raw.get("lookback_days", 7),
        "max_questions_per_hour": raw.get("max_questions_per_hour",
                                          DEFAULT_MAX_QUESTIONS_PER_HOUR),
        "max_history_pages": raw.get("max_history_pages", 10),
        "run_timeout_seconds": raw.get("run_timeout_seconds", DEFAULT_RUN_TIMEOUT),
        "act": raw.get("act", False),
        "plan_it_state": raw.get("plan_it_state") or "Plan it",
        "max_actions_per_hour": raw.get("max_actions_per_hour", DEFAULT_MAX_ACTIONS_PER_HOUR),
        "ready_state": raw.get("ready_state") or "Todo",
        "done_state": raw.get("done_state") or DEFAULT_DONE_STATE,
        "agent_user_name": raw.get("agent_user_name", ""),
        "epic_wip": raw.get("epic_wip", 1),
        "handoff_states": raw.get("handoff_states", list(DEFAULT_HANDOFF_STATES)),
        "queue_config": raw.get("queue_config") or "",
        "queue_every_seconds": raw.get("queue_every_seconds", DEFAULT_QUEUE_EVERY_SECONDS),
        "pings_file": raw.get("pings_file") or "",
        "relay_max_chars": raw.get("relay_max_chars", notify.DEFAULT_RELAY_MAX_CHARS),
        "relay_max_per_hour": raw.get("relay_max_per_hour", DEFAULT_RELAY_MAX_PER_HOUR),
        "notifier_bot_user_id": raw.get("notifier_bot_user_id") or "",
        "notifier_plist": raw.get("notifier_plist") or "",
        "run_interval_seconds": raw.get("run_interval_seconds", DEFAULT_RUN_INTERVAL),
        "dm_user_ids": raw.get("dm_user_ids", []),
        "notifier_posts_file": raw.get("notifier_posts_file") or "",
        "ask_tech_lead": raw.get("ask_tech_lead", False),
        "push_mode": raw.get("push_mode", False),
        "push_token_env": raw.get("push_token_env") or "BRIDGE_PUSH_APP_TOKEN",
        "chat_bot_user_id": raw.get("chat_bot_user_id") or "",
        "back_room_channel_id": raw.get("back_room_channel_id") or "",
        "forward_max_per_hour": raw.get("forward_max_per_hour", 100),
        "forward_follow_minutes": raw.get("forward_follow_minutes", 60),
        "forward_answer_minutes": raw.get("forward_answer_minutes", 10),
        "forward_history_messages": raw.get("forward_history_messages", 30),
        "forward_history_chars": raw.get("forward_history_chars", 8000),
        "tech_lead_link_hosts": raw.get("tech_lead_link_hosts",
                                        list(DEFAULT_TECH_LEAD_LINK_HOSTS)),
        "direct_asks": raw.get("direct_asks", False),
        "direct_grace_seconds": raw.get("direct_grace_seconds", 90),
        "direct_max_age_minutes": raw.get("direct_max_age_minutes", 10),
        "direct_max_tickets": raw.get("direct_max_tickets", 3),
        "tech_lead_wake": raw.get("tech_lead_wake", False),
        "wake_max_per_day": raw.get("wake_max_per_day", DEFAULT_WAKE_MAX_PER_DAY),
        "wake_quiet_hours": raw.get("wake_quiet_hours", DEFAULT_WAKE_QUIET_HOURS),
        "wake_pr_stuck_hours": raw.get("wake_pr_stuck_hours", DEFAULT_WAKE_PR_STUCK_HOURS),
        "wake_status_file": raw.get("wake_status_file") or "",
    }
    if not cfg["pings_file"]:
        cfg["pings_file"] = os.path.join(cfg["state_dir"], "notifier-bridge-pings.json")
    else:
        cfg["pings_file"] = os.path.realpath(os.path.expanduser(cfg["pings_file"]))
    if not cfg["notifier_posts_file"]:
        cfg["notifier_posts_file"] = os.path.join(cfg["state_dir"], NOTIFIER_DM_POSTS_NAME)
    elif isinstance(cfg["notifier_posts_file"], str):
        cfg["notifier_posts_file"] = os.path.realpath(
            os.path.expanduser(cfg["notifier_posts_file"]))
    if not cfg["wake_status_file"]:
        # The status job's own file sits beside the state dir: ~/.stage-e/status/.
        cfg["wake_status_file"] = os.path.join(os.path.dirname(cfg["state_dir"]), "status",
                                               "pipeline-status.json")
    elif isinstance(cfg["wake_status_file"], str):
        cfg["wake_status_file"] = os.path.realpath(os.path.expanduser(cfg["wake_status_file"]))
    if cfg["schema"] != CONFIG_SCHEMA:
        errors.append("config 'schema' must be %r, got %r" % (CONFIG_SCHEMA, cfg["schema"]))
    for key in ("slack_token_env", "linear_key_env"):
        if not ENV_NAME_RE.match(cfg[key] or ""):
            errors.append("config %r must be an env var NAME, got %r — never put a "
                          "credential value in the config" % (key, cfg[key]))
    if cfg["slack_token_env"] == "SLACK_BOT_TOKEN":
        errors.append("config 'slack_token_env' may not be SLACK_BOT_TOKEN: that is the "
                      "chat bot's own name, and the bridge must post as its own app, or the "
                      "chat bot could post a question that looks like the bridge's")
    chans = cfg["channel_ids"]
    if not isinstance(chans, list) or not chans or not all(
            isinstance(c, str) and CHANNEL_RE.match(c) for c in chans):
        errors.append("config 'channel_ids' must be a non-empty list of channel ids like "
                      "C0123456789, got %r" % (chans,))
    elif len(set(chans)) != len(chans):
        errors.append("config 'channel_ids' repeats a channel")
    if not re.match(r"^https://([a-z0-9-]+\.)*slack\.com/api$", cfg["chat_api_base"]):
        errors.append("config 'chat_api_base' must be an https Slack API address, got %r"
                      % cfg["chat_api_base"])
    if not isinstance(cfg["bot_user_id"], str) or not SLACK_USER_RE.match(cfg["bot_user_id"]):
        errors.append("config needs 'bot_user_id' — the bridge app's own bot user id, like "
                      "U0123456789; the bridge refuses a token that answers to anyone else")
    notifier_bot = cfg["notifier_bot_user_id"]
    if notifier_bot and (not isinstance(notifier_bot, str)
                         or not SLACK_USER_RE.match(notifier_bot)):
        errors.append("config 'notifier_bot_user_id' must be the notifier app's bot user id, "
                      "like U0123456789, or absent, got %r" % (notifier_bot,))
    elif notifier_bot and notifier_bot == cfg["bot_user_id"]:
        errors.append("config 'notifier_bot_user_id' is the bridge's own bot_user_id. The "
                      "notifier posts as its own app; with this, every reply to the bridge's "
                      "own questions would be refused as an answer to a ping")
    plist = cfg["notifier_plist"]
    if plist and (not isinstance(plist, str) or not os.path.isabs(plist)
                  or not plist.endswith(".plist")):
        errors.append("config 'notifier_plist' must be the notifier's LaunchDaemon plist, an "
                      "absolute path ending .plist, or absent, got %r" % (plist,))
    if not isinstance(cfg["owner_user_id"], str) or not cfg["owner_user_id"].strip():
        errors.append("config needs 'owner_user_id' — the tracker user the key must belong "
                      "to; the bridge acts as that user, so it refuses to guess")
    teams = cfg["team_keys"]
    if not isinstance(teams, list) or not teams or not all(
            isinstance(t, str) and TEAM_KEY_RE.match(t) for t in teams):
        errors.append("config 'team_keys' must be a non-empty list of team keys like PROD, "
                      "got %r" % (teams,))
    allowed = cfg["allowed_slack_users"]
    if not isinstance(allowed, list) or not all(
            isinstance(u, str) and SLACK_USER_RE.match(u) for u in allowed):
        errors.append("config 'allowed_slack_users' must be a list of Slack user ids like "
                      "U0123456789 (empty for any full member), got %r" % (allowed,))
    dms = cfg["dm_user_ids"]
    if not isinstance(dms, list) or not all(
            isinstance(u, str) and SLACK_USER_RE.match(u) for u in dms):
        errors.append("config 'dm_user_ids' must be a list of Slack user ids like "
                      "U0123456789 (empty for no DM), got %r" % (dms,))
    elif len(set(dms)) != len(dms):
        errors.append("config 'dm_user_ids' repeats a member")
    numbers_ok = True
    for key in ("question_ttl_hours", "lookback_days", "max_questions_per_hour",
                "max_history_pages", "run_timeout_seconds", "max_open_questions",
                "max_actions_per_hour", "epic_wip", "relay_max_chars",
                "relay_max_per_hour", "run_interval_seconds", "queue_every_seconds"):
        value = cfg[key]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            errors.append("config %r must be a positive integer, got %r" % (key, value))
            numbers_ok = False
    if numbers_ok and cfg["question_ttl_hours"] > 24 * cfg["lookback_days"]:
        errors.append("config 'question_ttl_hours' (%d) is longer than the window "
                      "'lookback_days' reads (%d days): an answer could fall out of the "
                      "window while its question is still open"
                      % (cfg["question_ttl_hours"], cfg["lookback_days"]))
    # KIT-340: the one reminder. 0 turns it off. Set by hand, it must come before the question
    # expires; left at its default under a shorter ttl, no reminder is sent.
    hours = cfg["question_reminder_hours"]
    if isinstance(hours, bool) or not isinstance(hours, int) or hours < 0:
        errors.append("config 'question_reminder_hours' must be a whole number of hours, 0 for "
                      "none, got %r" % (hours,))
    elif (numbers_ok and "question_reminder_hours" in raw and hours
          and hours >= cfg["question_ttl_hours"]):
        errors.append("config 'question_reminder_hours' (%d) must be less than "
                      "'question_ttl_hours' (%d), or the question expires before its reminder"
                      % (hours, cfg["question_ttl_hours"]))
    if not isinstance(cfg["act"], bool):
        errors.append("config 'act' must be true or false, got %r" % (cfg["act"],))
    for key in ("plan_it_state", "ready_state", "done_state"):
        if not isinstance(cfg[key], str) or not cfg[key].strip():
            errors.append("config %r must be a state name, got %r" % (key, cfg[key]))
    if not isinstance(cfg["agent_user_name"], str):
        errors.append("config 'agent_user_name' must be a display name, got %r"
                      % (cfg["agent_user_name"],))
    if numbers_ok and cfg["epic_wip"] > 10:
        errors.append("config 'epic_wip' must be at most 10, got %d" % cfg["epic_wip"])
    if numbers_ok and cfg["queue_every_seconds"] < 60:
        errors.append("config 'queue_every_seconds' must be at least 60: each ask reads the "
                      "tracker whole, got %d" % cfg["queue_every_seconds"])
    if not isinstance(cfg["queue_config"], str):
        errors.append("config 'queue_config' must be a path to the queue's config file, or "
                      "empty for none, got %r" % (cfg["queue_config"],))
    elif cfg["queue_config"]:
        cfg["queue_config"] = os.path.realpath(os.path.expanduser(cfg["queue_config"]))
    handoff = cfg["handoff_states"]
    if not isinstance(handoff, list) or not handoff or not all(
            isinstance(s, str) and s and s == s.strip() for s in handoff):
        errors.append("config 'handoff_states' must be a non-empty list of state names, "
                      "each spelled as the tracker shows it with no space around it, like "
                      "%s, got %r" % (json.dumps(DEFAULT_HANDOFF_STATES), handoff))
    else:
        early = [s for s in handoff if s in (cfg["ready_state"], cfg["plan_it_state"])]
        if early:
            errors.append("config 'handoff_states' names %s, which is also 'ready_state' or "
                          "'plan_it_state'. A child sits there before its work starts, never "
                          "while it waits for your merge" % ", ".join(early))
    if notify._inside_git_worktree(cfg["state_dir"]):
        errors.append("config 'state_dir' (%s) is inside a git working tree — the record of "
                      "open questions must live outside every worktree, where no session "
                      "can write it" % cfg["state_dir"])
    if notify._inside_git_worktree(os.path.dirname(cfg["pings_file"])):
        errors.append("config 'pings_file' (%s) is inside a git working tree — it decides "
                      "which session an answer reaches, so it must live outside every "
                      "worktree, where no session can write it" % cfg["pings_file"])
    posts_file = cfg["notifier_posts_file"]
    if not isinstance(posts_file, str):
        errors.append("config 'notifier_posts_file' must be a path, got %r" % (posts_file,))
    elif notify._inside_git_worktree(os.path.dirname(posts_file)):
        errors.append("config 'notifier_posts_file' (%s) is inside a git working tree — it "
                      "decides which posts in a DM the bridge vouches for, so it must live "
                      "outside every worktree, where no session can write it" % posts_file)
    # KIT-297: the writer's keys, by the writer's own rules, each one on its own so every
    # bad one is named at once, and whether the writer is on or off.
    cfg.update(voice.settings(raw)[0])
    if "writer" in raw and not isinstance(raw["writer"], bool):
        errors.append("config 'writer' must be true or false, got %r" % (raw["writer"],))
    bad = [k for k in sorted(voice.DEFAULTS)
           if k != "writer" and voice.settings({k: raw.get(k)})[1]]
    for key in bad:
        errors.append("config %r is not a valid writer setting, got %r: %s"
                      % (key, raw.get(key), voice.CONFIG_KEYS[key]))
    if cfg["writer"] and "writer_bin" not in bad and not os.path.isabs(cfg["writer_bin"]):
        errors.append("config 'writer_bin' must be the claude CLI's full path while 'writer' "
                      "is true, got %r: the job runs under launchd, whose PATH is short"
                      % cfg["writer_bin"])
    # KIT-297 review: a pass must have time to post a message plain after the writer gives
    # up on it, or a slow writer would cost the message.
    if cfg["writer"] and numbers_ok and "writer_timeout_seconds" not in bad:
        room = cfg["run_timeout_seconds"] - WRITER_POST_SECONDS
        if room < WRITER_MIN_SECONDS:
            errors.append("config 'run_timeout_seconds' (%d) leaves the writer no time: it "
                          "must be at least %d while 'writer' is true"
                          % (cfg["run_timeout_seconds"], WRITER_MIN_SECONDS
                             + WRITER_POST_SECONDS))
        elif cfg["writer_timeout_seconds"] > room:
            errors.append("config 'writer_timeout_seconds' (%g) must be at most %d while "
                          "'run_timeout_seconds' is %d: a pass must still have time to post "
                          "the message plain after the writer gives up"
                          % (cfg["writer_timeout_seconds"], room, cfg["run_timeout_seconds"]))
    # KIT-489: the tech lead's turn, by its own module's rules, whether it is on or off.
    cfg.update(turn.settings(raw)[0])
    errors.extend(turn_config_errors(cfg, raw, numbers_ok))
    errors.extend(push_config_errors(cfg))
    errors.extend(forward_config_errors(cfg))
    errors.extend(direct_config_errors(cfg))
    errors.extend(wake_config_errors(cfg))
    if errors:
        raise BridgeError("config problems:\n  - " + "\n  - ".join(errors), EXIT_USAGE)
    return cfg


def direct_config_errors(cfg):
    """ACT-4: the keys that let a member's own clear ask act after a stop window. Off by
    default. On, acting is on too, a DM is read, and the agent's account is named, or a
    ticket it filed could never be told apart."""
    errors = []
    on = cfg["direct_asks"]
    if not isinstance(on, bool):
        errors.append("config 'direct_asks' must be true or false, got %r" % (on,))
    for key, low, high in (("direct_grace_seconds", 30, 3600),
                           ("direct_max_age_minutes", 1, 1440), ("direct_max_tickets", 1, 10)):
        value = cfg[key]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            errors.append("config %r must be an integer from %d to %d, got %r"
                          % (key, low, high, value))
    if on is True:
        if cfg["act"] is not True:
            errors.append("config 'direct_asks' is on, but 'act' is off: a direct ask would "
                          "say it acts and then change nothing")
        if not cfg["dm_user_ids"]:
            errors.append("config 'direct_asks' is on, but 'dm_user_ids' is empty: a direct "
                          "ask is read only in a member's DM, and no DM is read")
        if not cfg["agent_user_name"]:
            errors.append("config 'direct_asks' is on, so it needs 'agent_user_name': a ticket "
                          "the agent's own account filed is always asked about")
    return errors


def push_config_errors(cfg):
    """KIT-560: the push-connection job's two keys, checked whether it is on or off. On, it needs the
    tech lead's turn (it exists to answer a DM at once) and its token's name must be its own: the
    app-level token opens a connection and is not the bot's or the tracker's."""
    errors = []
    if not isinstance(cfg["push_mode"], bool):
        errors.append("config 'push_mode' must be true or false, got %r" % (cfg["push_mode"],))
        return errors
    if not isinstance(cfg["push_token_env"], str) or not ENV_NAME_RE.match(
            cfg["push_token_env"]):
        errors.append("config 'push_token_env' must be an ENV VAR NAME, never the token itself")
    elif cfg["push_token_env"] in (cfg["slack_token_env"], cfg["linear_key_env"]):
        errors.append("config 'push_token_env' may not be the bot token's or the tracker "
                      "key's name: the app-level token is its own credential")
    if cfg["push_mode"] and not cfg.get("tech_lead_turn"):
        errors.append("config 'push_mode' is on, so it needs 'tech_lead_turn' on: the job "
                      "answers an owner's DM with the turn")
    return errors


def turn_config_errors(cfg, raw, numbers_ok):
    """KIT-489: the keys of the tech lead's turn. Off by default. On, it needs the CLI's full
    path (the job runs under launchd, whose PATH is short), the tool server's config, and the
    forward that it takes over: until the cutover the chat bot stays, so the turn is a second
    way to answer the same DM message, never the only one. A turn must also leave a pass the
    time to say it failed."""
    errors = []
    if "tech_lead_turn" in raw and not isinstance(raw["tech_lead_turn"], bool):
        errors.append("config 'tech_lead_turn' must be true or false, got %r"
                      % (raw["tech_lead_turn"],))
    bad = [k for k in sorted(turn.CONFIG_NOTE_KEYS) if turn.settings({k: raw.get(k)})[1]]
    for key in bad:
        errors.append("config %r is not a valid turn setting, got %r: %s"
                      % (key, raw.get(key), turn.CONFIG_KEYS[key]))
    if cfg["tech_lead_turn"]:
        if cfg["ask_tech_lead"] is not True:
            errors.append("config 'tech_lead_turn' is on, so it needs 'ask_tech_lead' on: the "
                          "turn answers what that passes on, and the chat bot stays until the "
                          "cutover")
        if "turn_bin" not in bad and not os.path.isabs(cfg["turn_bin"]):
            errors.append("config 'turn_bin' must be the claude CLI's full path while "
                          "'tech_lead_turn' is true, got %r: the job runs under launchd, whose "
                          "PATH is short" % cfg["turn_bin"])
        if not cfg["turn_tools_config"]:
            errors.append("config 'tech_lead_turn' is on, so it needs 'turn_tools_config': the "
                          "full path of the tool server's config file")
        if numbers_ok and "turn_timeout_seconds" not in bad:
            room = cfg["run_timeout_seconds"] - WRITER_POST_SECONDS
            if room < turn.TURN_MIN_SECONDS:
                errors.append("config 'run_timeout_seconds' (%d) leaves the turn no time: it "
                              "must be at least %d while 'tech_lead_turn' is true"
                              % (cfg["run_timeout_seconds"],
                                 turn.TURN_MIN_SECONDS + WRITER_POST_SECONDS))
            elif cfg["turn_timeout_seconds"] > room:
                errors.append("config 'turn_timeout_seconds' (%g) must be at most %d while "
                              "'run_timeout_seconds' is %d: a pass must still have time to say "
                              "a turn failed" % (cfg["turn_timeout_seconds"], room,
                                                 cfg["run_timeout_seconds"]))
    return errors


def forward_config_errors(cfg):
    """KIT-298: the keys that pass a DM question to the dispatcher's tech lead. Off by
    default. On, the chat bot and the back room are named, never guessed, and the back room
    is a channel this bridge reads, or it would never see the answer."""
    errors = []
    on = cfg["ask_tech_lead"]
    if not isinstance(on, bool):
        errors.append("config 'ask_tech_lead' must be true or false, got %r" % (on,))
    for key in ("forward_max_per_hour", "forward_follow_minutes", "forward_answer_minutes"):
        value = cfg[key]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            errors.append("config %r must be a positive integer, got %r" % (key, value))
    # The DM's recent conversation under a new thread's question: 0 turns it off.
    for key, low, high in (("forward_history_messages", 0, FORWARD_HISTORY_MESSAGES_MAX),
                           ("forward_history_chars", 1, FORWARD_HISTORY_CHARS_MAX)):
        value = cfg[key]
        if (isinstance(value, bool) or not isinstance(value, int)
                or not low <= value <= high):
            errors.append("config %r must be a whole number from %d to %d, got %r"
                          % (key, low, high, value))
    bot = cfg["chat_bot_user_id"]
    if bot and (not isinstance(bot, str) or not SLACK_USER_RE.match(bot)):
        errors.append("config 'chat_bot_user_id' must be the dispatcher's chat bot's Slack user "
                      "id, like U0123456789, got %r" % (bot,))
    elif bot and bot in (cfg["bot_user_id"], cfg["notifier_bot_user_id"]):
        errors.append("config 'chat_bot_user_id' is the bridge's or the notifier's own bot. It "
                      "must be the dispatcher's chat bot, which answers what the bridge passes on")
    room = cfg["back_room_channel_id"]
    chans = cfg["channel_ids"] if isinstance(cfg["channel_ids"], list) else []
    if room and room not in chans:
        errors.append("config 'back_room_channel_id' must be one of channel_ids, got %r: the "
                      "bridge reads only those, so it would never see the answer" % (room,))
    if on is True:
        if not bot:
            errors.append("config 'ask_tech_lead' is on, so it needs 'chat_bot_user_id' — the "
                          "dispatcher's chat bot's Slack user id, the one account it mentions")
        if not room:
            errors.append("config 'ask_tech_lead' is on, so it needs 'back_room_channel_id' — "
                          "the channel, one of channel_ids, where it asks the chat bot")
        if not cfg["dm_user_ids"]:
            errors.append("config 'ask_tech_lead' is on, but 'dm_user_ids' is empty: a question "
                          "is passed on only from a member's DM, and no DM is read")
    # KIT-343: an empty list is allowed, and keeps no link at all.
    hosts = cfg["tech_lead_link_hosts"]
    if not isinstance(hosts, list) or not all(
            isinstance(h, str) and LINK_HOST_RE.match(h) for h in hosts):
        errors.append("config 'tech_lead_link_hosts' must be a list of host names in lower "
                      "case, like [\"github.com\"], each of two labels or more, got %r"
                      % (hosts,))
    return errors


def wake_config_errors(cfg):
    """KIT-356: the keys that wake the tech lead. Off by default. On, it needs the relay that
    brings its answers back, and a DM member, the first of whom is the owner it writes to."""
    errors = []
    on = cfg["tech_lead_wake"]
    if not isinstance(on, bool):
        errors.append("config 'tech_lead_wake' must be true or false, got %r" % (on,))
    for key, top in (("wake_max_per_day", WAKE_MAX_PER_DAY_TOP), ("wake_pr_stuck_hours", 168)):
        value = cfg[key]
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= top:
            errors.append("config %r must be a whole number from 1 to %d, got %r"
                          % (key, top, value))
    quiet = cfg["wake_quiet_hours"]
    if quiet != "" and quiet_bounds(quiet) is None:
        errors.append("config 'wake_quiet_hours' must be two different local times as "
                      "HH:MM-HH:MM, 24-hour, such as \"21:00-08:00\", or \"\" for none, got %r"
                      % (quiet,))
    if not isinstance(cfg["wake_status_file"], str):
        errors.append("config 'wake_status_file' must be a path, got %r"
                      % (cfg["wake_status_file"],))
    if on is True:
        if cfg["ask_tech_lead"] is not True:
            errors.append("config 'tech_lead_wake' is on, so it needs 'ask_tech_lead' on: the "
                          "tech lead's answer to a wake comes back the way its answers do")
        if not cfg["dm_user_ids"]:
            errors.append("config 'tech_lead_wake' is on, but 'dm_user_ids' is empty: a wake's "
                          "answer goes to the first member's DM")
    return errors


def credential(cfg, key):
    """The value of the env var the config NAMES. Never printed, never defaulted."""
    name = cfg[key]
    value = os.environ.get(name, "").strip()
    if not value:
        raise BridgeError("$%s is unset — the bridge's wrapper sources it from the role "
                          "account's env file" % name, EXIT_USAGE)
    return value


def live_secrets(cfg):
    """Every credential the config names that is set, so an output is redacted against it
    even when a transport was handed in."""
    values = [os.environ.get(cfg[k], "").strip() for k in ("slack_token_env",
                                                            "linear_key_env")]
    # KIT-297: with the writer on, its subscription token is one of them.
    if cfg.get("writer") is True and cfg.get("writer_token_env"):
        values.append(os.environ.get(cfg["writer_token_env"], "").strip())
    # KIT-489: so is the tech lead's turn's, which is the writer's by default.
    if cfg.get("tech_lead_turn") is True and cfg.get("turn_token_env"):
        values.append(os.environ.get(cfg["turn_token_env"], "").strip())
    # KIT-560: and the push-connection job's app-level token.
    if cfg.get("push_mode") is True and cfg.get("push_token_env"):
        values.append(os.environ.get(cfg["push_token_env"], "").strip())
    return [v for v in values if v]


def state_path(cfg):
    return os.path.join(cfg["state_dir"], "bridge-state.json")


def heartbeat_path(cfg):
    return os.path.join(cfg["state_dir"], "bridge-heartbeat.json")


def new_state():
    # asked_at: when each tracker lookup for a request was made (the hourly limit).
    # limit_said: per channel, when the bridge last said that limit was reached.
    # owed: attribution comments a failed write left unwritten; retried every pass.
    # epics: each approved epic the bridge is starting the children of (KIT-223).
    # relays, relay_pending: answers passed on to a session in the last hour, and the
    # answers recorded before a post that never confirmed (KIT-224).
    # dms: each listed member's DM channel, opened once; stray_said: per DM, when the
    # bridge last said what it can do there (KIT-295).
    # forwards: each back-room thread a DM question opened, "<back room>:<root ts>";
    # forwarded_at: when each was passed on (the hourly limit); relayed_posts: the DM posts
    # that carry the chat bot's words, never vouched for before a bare yes (KIT-298), each
    # with the ticket ids it names; set_up_posts: the bridge's DM posts for a line the tech
    # lead set up, with the ticket it named (ACT-1, KIT-364).
    return {"schema": STATE_SCHEMA, "handled": {}, "threads": {}, "questions": {},
            "asked_at": [], "posted": {}, "attempts": {}, "limit_said": {}, "owed": [],
            "epics": {}, "relays": [], "relay_pending": {}, "dms": {}, "stray_said": {},
            "forwards": {}, "forwarded_at": [], "relayed_posts": {}, "set_up_posts": {},
            "direct": {}, "turn_sessions": {}, "turn_threads": {}}


def load_state(cfg):
    """(state, first_pass). A file that cannot be read is REFUSED: the open questions in it
    are what bind a yes to a ticket, and a fresh start would forget them silently."""
    path = state_path(cfg)
    if not os.path.exists(path):
        return new_state(), True
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        raise BridgeError("the bridge's state %s cannot be read (%s). Nothing was done. "
                          "Move it aside only if you accept that every open question is "
                          "forgotten" % (path, type(exc).__name__))
    if not isinstance(doc, dict) or doc.get("schema") != STATE_SCHEMA or not all(
            isinstance(doc.get(k), want) for k, want in (
                ("handled", dict), ("threads", dict), ("questions", dict),
                ("asked_at", list))):
        raise BridgeError("the bridge's state %s is not a %s document. Nothing was done"
                          % (path, STATE_SCHEMA))
    doc.setdefault("posted", {})
    doc.setdefault("attempts", {})
    doc.setdefault("limit_said", {})
    doc.setdefault("owed", [])
    if not isinstance(doc["owed"], list):
        raise BridgeError("the bridge's state %s has no readable list of owed comments. "
                          "Nothing was done" % path)
    doc.setdefault("epics", {})
    doc.setdefault("relays", [])
    doc.setdefault("relay_pending", {})
    if not isinstance(doc["relays"], list) or not isinstance(doc["relay_pending"], dict):
        raise BridgeError("the bridge's state %s has no readable record of answers passed "
                          "on. Nothing was done" % path)
    if not isinstance(doc["epics"], dict):
        raise BridgeError("the bridge's state %s has no readable list of approved epics. "
                          "Nothing was done" % path)
    doc.setdefault("dms", {})
    doc.setdefault("stray_said", {})
    if not isinstance(doc["dms"], dict) or not isinstance(doc["stray_said"], dict):
        raise BridgeError("the bridge's state %s has no readable record of its DMs. Nothing "
                          "was done" % path)
    # KIT-482: the once-a-day line about the writer is gone, and so is its time.
    doc.pop("writer_note_at", None)
    doc.setdefault("forwards", {})
    doc.setdefault("forwarded_at", [])
    doc.setdefault("relayed_posts", {})
    doc.setdefault("set_up_posts", {})
    doc.setdefault("direct", {})            # each direct ask, its window and its end (ACT-4)
    # KIT-489: each DM's tech-lead conversation, {"id", "last_at"}, by the DM's channel.
    doc.setdefault("turn_sessions", {})
    # KIT-560: the threads (channel:root) the turn has answered in, by when, with push_mode on.
    doc.setdefault("turn_threads", {})
    if not isinstance(doc["turn_threads"], dict):
        raise BridgeError("the bridge's state %s has no readable record of the threads the tech "
                          "lead answered in. Nothing was done" % path)
    if not isinstance(doc["turn_sessions"], dict):
        raise BridgeError("the bridge's state %s has no readable record of the tech lead's "
                          "conversations. Nothing was done" % path)
    if (not isinstance(doc["forwards"], dict) or not isinstance(doc["forwarded_at"], list)
            or not isinstance(doc["relayed_posts"], dict)
            or not isinstance(doc["set_up_posts"], dict)):
        # The relayed posts are what keep the chat bot's words from vouching for a bare yes.
        raise BridgeError("the bridge's state %s has no readable record of the questions it "
                          "passed to the tech lead. Nothing was done" % path)
    return doc, False


def load_pings(cfg):
    """The notifier's recorded agent:blocked pings, keyed as it keys them; {} when it has
    recorded none yet. A file that cannot be read stops the pass: an answer must never be
    judged against a guess at what was pinged."""
    path = cfg["pings_file"]
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        raise BridgeError("the notifier's pings file %s cannot be read (%s). Nothing was "
                          "passed on" % (path, type(exc).__name__))
    if (not isinstance(doc, dict) or doc.get("schema") != notify.BRIDGE_PINGS_SCHEMA
            or not isinstance(doc.get("messages"), dict)):
        raise BridgeError("the notifier's pings file %s is not a %s document"
                          % (path, notify.BRIDGE_PINGS_SCHEMA))
    return doc["messages"]


def load_dm_posts(cfg):
    """The notifier's record of the pages it posted into a DM as this app (KIT-295), keyed
    "<channel>:<ts>"; {} when it has recorded none yet. Its pages look like the bridge's own
    posts there. A file that cannot be read stops the pass, as the pings file does: a yes
    is never judged against a guess at what the notifier posted."""
    path = cfg["notifier_posts_file"]
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        raise BridgeError("the notifier's record of its DM pages %s cannot be read (%s). "
                          "Nothing was done" % (path, type(exc).__name__))
    if (not isinstance(doc, dict) or doc.get("schema") != NOTIFIER_DM_POSTS_SCHEMA
            or not isinstance(doc.get("posts"), dict)):
        raise BridgeError("the notifier's record of its DM pages %s is not a %s document. "
                          "Nothing was done" % (path, NOTIFIER_DM_POSTS_SCHEMA))
    return doc["posts"]


def lock_path(cfg):
    return os.path.join(cfg["state_dir"], "bridge.lock")


def save_state(cfg, state):
    """Mode 600: the record says who answered what. The umask is set around the
    notifier's atomic write, so the file is never readable by others, even briefly."""
    old = os.umask(0o077)
    try:
        notify._atomic_write_json(state_path(cfg), state)
    finally:
        os.umask(old)


def _awake_clock():
    """Seconds this machine has been AWAKE since boot: the heartbeat monitor's clock
    (pipeline_heartbeat_monitor._awake_clock), read here without importing it, so the job's
    script closure stays as it is. None when it cannot be read (KIT-330). The installer's
    selftest pins this copy to the monitor's."""
    try:
        if hasattr(time, "CLOCK_UPTIME_RAW"):
            return float(time.clock_gettime(time.CLOCK_UPTIME_RAW))
        return float(time.monotonic())
    except (OSError, AttributeError, ValueError):
        return None


def write_heartbeat(cfg, result, now_iso):
    # `awake_clock` is read beside `at`, so the installer can age the beat in awake time:
    # launchd's interval stops while the Mac sleeps (KIT-330).
    doc = dict(result, schema=HEARTBEAT_SCHEMA, at=now_iso, awake_clock=_awake_clock())
    try:
        notify._atomic_write_json(heartbeat_path(cfg), doc)
    except OSError as exc:
        sys.stderr.write("NOTE: could not write the heartbeat %s: %s\n"
                         % (heartbeat_path(cfg), exc))


def key_of(channel, ts):
    return "%s:%s" % (channel, ts)


def ts_float(ts):
    try:
        return float(ts)
    except (TypeError, ValueError):
        return 0.0


def label_names(node):
    return set((n or {}).get("name") for n in ((node.get("labels") or {}).get("nodes") or []))


def run_idea(node, owner):
    """The idea a planning ticket of the planner's was written for, or None (KIT-244). The
    ticket is the planner's own (pipeline_plan_poller.is_run_ticket: its routing tag and a
    `Planning-run trigger:` line), filed with the owner's key as the planner files them, and
    carries exactly one `Idea: <id>` line above the fence that quotes the idea (run_body). A
    session can edit a ticket, so this only chooses what the bridge ASKS about: the question
    names the ticket that was named and the idea it then read, and every check runs on that
    idea."""
    text = node.get("description") or ""
    if not poller.is_run_ticket(node) or (node.get("creator") or {}).get("id") != owner:
        return None
    head = text.split(poller.FENCE_OPEN, 1)[0].splitlines()
    if not any(line.startswith(poller.TRIGGER_PREFIX) for line in head):
        return None
    ideas = [line[len(poller.IDEA_PREFIX):].strip() for line in head
             if line.startswith(poller.IDEA_PREFIX)]
    if len(ideas) != 1 or not re.match(r"^%s$" % TICKET_RE, ideas[0]):
        return None
    return ideas[0].upper()


def plan_run(node, owner):
    """The planning run a plan was filed from, or None (RP-1): the run its one receipt names,
    `Plan id: <run>/<digest>`, as the executor writes it, on a plan filed with the owner's
    key, as the executor files. Like run_idea, this only chooses what the bridge ASKS about;
    every check runs on the idea the run then names."""
    if (node.get("creator") or {}).get("id") != owner:
        return None
    prefix = poller.executor.PLAN_RECEIPT_PREFIX
    runs = [line[len(prefix):].split("/", 1)[0].strip()
            for line in (node.get("description") or "").splitlines() if line.startswith(prefix)]
    if len(runs) != 1 or not re.match(r"^%s$" % TICKET_RE, runs[0]):
        return None
    return runs[0].upper()


def ticket_number(identifier):
    try:
        return int((identifier or "").rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return 0


def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch(text):
    """An iso() time back to seconds, or None when it is not one."""
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def clock(seconds, now):
    """A time as a person says it, on this Mac's own clock: `7:02am`, or `7:02am on Oct 27`
    when it was not today."""
    t, today = time.localtime(seconds), time.localtime(now)
    said = "%d:%02d%s" % (t.tm_hour % 12 or 12, t.tm_min, "am" if t.tm_hour < 12 else "pm")
    if (t.tm_year, t.tm_yday) != (today.tm_year, today.tm_yday):
        said += " on %s %d" % (_MONTHS[t.tm_mon - 1], t.tm_mday)
    return said


def day_clock(seconds, now):
    """A message's time in the DM's recent conversation (KIT-298), on this Mac's own clock:
    `Fri 4:10pm` within the last six days; before that the date too, `Fri Oct 17 4:10pm`,
    and the year when it was not this one."""
    t, today = time.localtime(seconds), time.localtime(now)
    days = (datetime(today.tm_year, today.tm_mon, today.tm_mday)
            - datetime(t.tm_year, t.tm_mon, t.tm_mday)).days
    day = _WEEKDAYS[t.tm_wday]
    if not 0 <= days < 7:
        day += " %s %d" % (_MONTHS[t.tm_mon - 1], t.tm_mday)
        if t.tm_year != today.tm_year:
            day += " %d" % t.tm_year
    return "%s %d:%02d%s" % (day, t.tm_hour % 12 or 12, t.tm_min,
                             "am" if t.tm_hour < 12 else "pm")


# ══════════════════════════════════════════════════════════════════════════════════════
# Selftest — offline: this module's cases, moved here from the bridge's selftest (KIT-312),
# and its pins. The bridge's selftest runs these cases too, each in its place among the
# others, with one kit for all of them.
# ══════════════════════════════════════════════════════════════════════════════════════
def cases_config(kit):
    """The config: every problem at once: its cases from the bridge's selftest, moved
    here unchanged (KIT-312)."""
    from pipeline_bridge import _config_error
    with kit as tmp:
        check, cfg_for = kit.take("check", "cfg_for")
        # 12. CONFIG: every problem at once; the chat bot's token name and `act` refused.
        os.makedirs(os.path.join(tmp, "in-a-repo", ".git"))
        try:
            validate_config({"schema": "x", "channel_ids": ["nope"], "team_keys": [],
                             "slack_token_env": "SLACK_BOT_TOKEN", "act": "yes",
                             "question_ttl_hours": 0, "bogus": 1,
                             "state_dir": os.path.join(tmp, "in-a-repo", "state")})
            errs = ""
        except BridgeError as exc:
            errs = str(exc)
        check("config-caps-epic-wip", "'epic_wip' must be at most 10" in _config_error(
            dict(EXAMPLE_CONFIG, epic_wip=11, state_dir=os.path.join(tmp, "wip"))), True)
        # KIT-454: the queue is off unless named, and asked at most once a minute.
        check("config-queue-off-by-default-asked-at-most-once-a-minute",
              (validate_config(dict(EXAMPLE_CONFIG, state_dir=os.path.join(tmp, "q0")))[
                  "queue_config"],
               "'queue_every_seconds' must be at least 60" in _config_error(dict(
                   EXAMPLE_CONFIG, queue_every_seconds=30, state_dir=os.path.join(tmp, "q1"))),
               "'queue_config' must be a path" in _config_error(dict(
                   EXAMPLE_CONFIG, queue_config=5, state_dir=os.path.join(tmp, "q2")))),
              ("", True, True))
        # KIT-284: the hand-off lanes are a list of names, the lane setup-board makes by
        # default, and never a state a child sits in before its work starts.
        check("config-handoff-states-default-to-the-lane-setup-board-makes",
              (cfg_for("handoff-default").get("handoff_states"),
               _config_error(dict(EXAMPLE_CONFIG, state_dir=os.path.join(tmp, "hs"),
                                  handoff_states=["Ready to merge", "Needs Approval"]))),
              (["Needs Approval"], ""))
        for name, value in (("a-bare-name", "Needs Approval"), ("an-empty-list", []),
                            ("a-blank-name", [" "]), ("an-empty-name", [""]),
                            ("a-padded-name", ["Needs Approval "]), ("a-number", [7]),
                            ("the-ready-state", ["Todo"]), ("the-plan-it-state", ["Plan it"])):
            check("config-handoff-states-refused:" + name,
                  "config 'handoff_states'" in _config_error(dict(
                      EXAMPLE_CONFIG, handoff_states=value, state_dir=os.path.join(tmp, "hs"))),
                  True)
        # The pings file decides which session an answer reaches: it lives outside every
        # worktree too, where no session can write it (KIT-224 review).
        check("config-refuses-a-pings-file-inside-a-worktree",
              "'pings_file'" in _config_error(dict(
                  EXAMPLE_CONFIG, state_dir=os.path.join(tmp, "pings-state"),
                  pings_file=os.path.join(tmp, "in-a-repo", "pings.json"))), True)
        for needle in ("unknown config key", "'schema'", "channel_ids", "owner_user_id",
                       "team_keys", "chat bot's own name", "'act' must be true or false",
                       "question_ttl_hours",
                       "git working tree"):
            check("config-names:" + needle, needle in errs, True)


def selftest():
    import pipeline_bridge
    return pipeline_bridge.split_module_selftest(os.path.abspath(__file__),
                                                 [cases_config])


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        # As the module the bridge imports, never a copy of it named __main__, so its
        # cases and the pass share one copy of it (KIT-312).
        import pipeline_bridge_config
        sys.exit(pipeline_bridge_config.selftest())
    print("usage: pipeline_bridge_config.py --selftest (the bridge imports this module)")
    sys.exit(EXIT_USAGE)
