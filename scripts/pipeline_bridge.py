#!/usr/bin/env python3
"""The Slack bridge — the chat side's only way to act as the owner (KIT-220, KIT-221).

    python3 scripts/pipeline_bridge.py run --config <path>            # one pass
    python3 scripts/pipeline_bridge.py run --config <path> --dry-run  # posts and saves nothing
    python3 scripts/pipeline_bridge.py --example-config
    python3 scripts/pipeline_bridge.py --selftest

WHY IT EXISTS. The planner and the dispatcher accept only the owner's own moves, on
purpose, and the chat bot writes as the dispatcher's app user. So nothing the chat bot does
can start planning or work. This job is the one bridge across: it reads the Slack channel,
checks WITH SLACK ITSELF who sent each message, and acts in the tracker with the owner's
key — only after a verified member answers "yes" to a question the bridge asked itself.

THIS VERSION ACTS ON NOTHING. It reads, verifies, asks and records. A verified "yes" is
recorded as confirmed and said in the thread; no tracker write exists in this file (the
selftest scans its own source for one). The actions are later tickets: KIT-222 (plan an
idea), KIT-223 (approve an epic and start its children in order), KIT-224 (answer a stuck
session, replacing the notifier's reply relay).

WHO ASKED COMES FROM SLACK, NEVER FROM TEXT

  A message's sender is the `user` field Slack's own history and replies calls return. It
  is then looked up with `users.info`, and refused unless the account is a human, not
  deleted, not a guest (restricted or ultra-restricted), not an app user, and named by Slack
  as in the same workspace as the bridge's own token: an account Slack names no workspace
  for is refused, not passed. Bot posts, edits, file shares and every message with a
  subtype (joins, broadcasts) are refused too. An optional `allowed_slack_users` list
  narrows it further; empty means any full member, which is the owner's decision of
  2026-10-10 (KIT-117): workspace membership is the access rule.

THE BRIDGE ASKS, A MEMBER ANSWERS

  A request is one strict line, `plan <ticket id>`, from anyone in the channel, the chat
  bot included: asking is harmless. (`approve <ticket id>` is recognised too; this version
  answers it once, saying approving from Slack is not built yet, and changes nothing.) The
  bridge reads the ticket from the tracker with the owner's key and posts its OWN question
  in that thread, carrying only facts it read itself. A question is answered by a reply
  `yes` or `no` in its thread, after it, from a verified member. A `yes` binds to the
  question recorded in this job's state, never to a ticket named in someone's text: with
  two questions open in one thread, a bare `yes` is refused and `yes <ticket id>` is asked
  for. So is a bare `yes` when anything other than the bridge or a verified member posted
  in the thread after the question (a bot, an app, Slackbot, a guest), because a look-alike
  question could have changed what the person thought they were answering. A question
  edited after it was posted, or gone from its thread, is refused and closed. A question
  expires `question_ttl_hours` after it was asked, judged by when the answer was SENT, so a
  yes sent while the Mac slept still counts. A question's status changes only after the
  bridge's reply saying so is posted. A tricked chat bot can make the bridge ask; it cannot
  make it act.

  THE BRIDGE KNOWS ITS OWN BOT. `bot_user_id` in the config is the bridge app's bot user,
  and a token that answers to anyone else is refused before anything is read: a pasted
  chat-bot token would otherwise make the chat bot's posts look like the bridge's.

CATCHES UP AFTER SLEEP

  Slack drops an event once the receiver has been unreachable for about six minutes, and
  nothing redelivers it. So the bridge does not listen: every pass it reads the channel's
  history over `lookback_days`, the threads that changed, and the thread of every open
  question, and handles each message once (keyed on channel and timestamp). A "yes" sent
  while the Mac slept is handled when it wakes, if its question has not expired. A thread is
  marked read only after every message taken from it is handled, so a pass that fails or
  runs out of time reads it again next time.

  A thread is found through its first message. So a new reply in a thread that started
  before the window is not read, unless that thread holds an open question: ask in a new
  message, or a newer thread, instead.

  The FIRST pass (no state file) records everything already in the window as seen and
  answers none of it. A state file that cannot be read is refused, never treated as a first
  pass: the record of open questions is what binds a "yes" to a ticket.

SAYS WHICH NOTHING IT DID (contract §13)

  Every refusal it can explain is said once in the thread. State is written through after
  every message, so a later failure cannot lose an answer already said. A Slack or tracker
  call that fails stops the pass with exit 1 and leaves the message for the next pass; a
  message that fails three passes running is given up on, said once, and passed over, so
  one bad message cannot stall the bridge. One pass runs at a time (a lock in the state
  dir). Messages older than the window are never handled, and the window must be at least
  as long as a question lives. A real pass writes a heartbeat; a dry run writes none, so a
  check that runs one never hides the job's own record.

  Every request that reads the tracker counts against `max_questions_per_hour`, whether
  the ticket is found or not: the key is the owner's, shared with Stage E. Past the limit a
  request is refused without a read, and that is said at most once an hour per channel.

WHAT IT NEVER DOES

  It writes nothing to the tracker in this version. It never posts outside its configured
  channels, never posts a message's own text back, and never prints a credential: both are
  read from env vars the config NAMES (the job's wrapper sources the role account's env
  file), and every line it writes (the log, the state file, a Slack post) is redacted
  against them. The state file is written mode 600.
"""
import argparse
import json
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# The notifier's own pieces, imported, never copied: its chat-text cleaner, its title
# scrub for the chat provider, its secret scan and redaction, its atomic JSON write and its
# worktree test. One copy of each means one place to fix it.
import pipeline_notify_local as notify  # noqa: E402

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_DECLINED, EXIT_TIMEOUT = (
    notify.EXIT_OK, notify.EXIT_ERROR, notify.EXIT_USAGE, notify.EXIT_DECLINED,
    notify.EXIT_TIMEOUT)

CONFIG_SCHEMA = "pipeline-bridge-config/1"
STATE_SCHEMA = "pipeline-bridge-state/1"
HEARTBEAT_SCHEMA = "pipeline-bridge-heartbeat/1"
TRACKER_API = "https://api.linear.app/graphql"
DEFAULT_STATE_DIR = "~/.stage-e/state"
MAX_THREAD_PAGES = 10

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
    "lookback_days": "how far back each pass reads the channel (default 7)",
    "max_questions_per_hour": "how many requests the bridge looks up in the tracker in an "
                              "hour, found or not (default 20)",
    "max_history_pages": "how many pages of channel history one pass may read (default 10)",
    "run_timeout_seconds": "wall clock for one pass (default 120)",
    "act": "whether the bridge acts; must be false in this version, which has no action",
}

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
    "question_ttl_hours": 24,
    "lookback_days": 7,
    "max_questions_per_hour": 20,
    "act": False,
}

ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
CHANNEL_RE = re.compile(r"^[CG][A-Z0-9]{8,}$")
SLACK_USER_RE = re.compile(r"^[UW][A-Z0-9]{8,}$")
TEAM_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]{0,9}$")
TICKET_RE = r"[A-Za-z][A-Za-z0-9]{0,9}-\d{1,7}"
# The whole grammar. A request is one line and nothing else; so is an answer.
REQUEST_RE = re.compile(r"^(plan|approve)\s+(%s)$" % TICKET_RE, re.IGNORECASE)
YES_RE = re.compile(r"^yes(?:\s+(%s))?[\s.!]*$" % TICKET_RE, re.IGNORECASE)
NO_RE = re.compile(r"^no(?:\s+(%s))?[\s.!]*$" % TICKET_RE, re.IGNORECASE)
_LEADING_MENTION_RE = re.compile(r"^<@([UWB][A-Z0-9]+)(?:\|[^>]*)?>\s*")

OPEN, CONFIRMED, DECLINED, EXPIRED = "open", "confirmed", "declined", "expired"


class BridgeError(Exception):
    def __init__(self, message, code=EXIT_ERROR):
        Exception.__init__(self, message)
        self.code = code


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
        "lookback_days": raw.get("lookback_days", 7),
        "max_questions_per_hour": raw.get("max_questions_per_hour", 20),
        "max_history_pages": raw.get("max_history_pages", 10),
        "run_timeout_seconds": raw.get("run_timeout_seconds", 120),
        "act": raw.get("act", False),
    }
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
    numbers_ok = True
    for key in ("question_ttl_hours", "lookback_days", "max_questions_per_hour",
                "max_history_pages", "run_timeout_seconds", "max_open_questions"):
        value = cfg[key]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            errors.append("config %r must be a positive integer, got %r" % (key, value))
            numbers_ok = False
    if numbers_ok and cfg["question_ttl_hours"] > 24 * cfg["lookback_days"]:
        errors.append("config 'question_ttl_hours' (%d) is longer than the window "
                      "'lookback_days' reads (%d days): an answer could fall out of the "
                      "window while its question is still open"
                      % (cfg["question_ttl_hours"], cfg["lookback_days"]))
    if not isinstance(cfg["act"], bool):
        errors.append("config 'act' must be true or false, got %r" % (cfg["act"],))
    elif cfg["act"]:
        errors.append("config 'act' is true, but this version of the bridge has no action "
                      "to take; set it to false")
    if notify._inside_git_worktree(cfg["state_dir"]):
        errors.append("config 'state_dir' (%s) is inside a git working tree — the record of "
                      "open questions must live outside every worktree, where no session "
                      "can write it" % cfg["state_dir"])
    if errors:
        raise BridgeError("config problems:\n  - " + "\n  - ".join(errors), EXIT_USAGE)
    return cfg


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
    return [v for v in values if v]


def state_path(cfg):
    return os.path.join(cfg["state_dir"], "bridge-state.json")


def heartbeat_path(cfg):
    return os.path.join(cfg["state_dir"], "bridge-heartbeat.json")


def new_state():
    # asked_at: when each tracker lookup for a request was made (the hourly limit).
    # limit_said: per channel, when the bridge last said that limit was reached.
    return {"schema": STATE_SCHEMA, "handled": {}, "threads": {}, "questions": {},
            "asked_at": [], "posted": {}, "attempts": {}, "limit_said": {}}


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
    return doc, False


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


def write_heartbeat(cfg, result, now_iso):
    doc = dict(result, schema=HEARTBEAT_SCHEMA, at=now_iso)
    try:
        notify._atomic_write_json(heartbeat_path(cfg), doc)
    except OSError as exc:
        sys.stderr.write("NOTE: could not write the heartbeat %s: %s\n"
                         % (heartbeat_path(cfg), exc))


# ══════════════════════════════════════════════════════════════════════════════════════
# Transports — Slack and the tracker. READS, plus one Slack post. No tracker write.
# ══════════════════════════════════════════════════════════════════════════════════════

class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the Authorization header wherever it named."""
    def redirect_request(self, *_a, **_k):
        raise urllib.error.HTTPError(_a[0].full_url, 307, "redirect refused", None, None)


_OPENER = urllib.request.build_opener(_RefuseRedirect)


def _http_json(url, headers, payload=None, timeout=20):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="GET" if payload is None else "POST")
    with _OPENER.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


class SlackClient(object):
    """The bridge app's side of Slack. Every `ok: false` is RAISED, never read as empty:
    a token that cannot see the channel must not look like a channel where nobody asked."""

    def __init__(self, cfg, token):
        self.base = cfg["chat_api_base"]
        self.token = token

    def _get(self, method, params):
        try:
            doc = _http_json("%s/%s?%s" % (self.base, method, urllib.parse.urlencode(params)),
                             {"Authorization": "Bearer %s" % self.token})
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise BridgeError("Slack %s could not be read (%s)" % (method, type(exc).__name__))
        if not isinstance(doc, dict) or not doc.get("ok"):
            raise BridgeError("Slack %s refused: %s" % (
                method, (doc or {}).get("error") if isinstance(doc, dict) else "no JSON"))
        return doc

    def auth_test(self):
        doc = self._get("auth.test", {})
        return {"user_id": doc.get("user_id"), "team_id": doc.get("team_id"),
                "bot_id": doc.get("bot_id")}

    def history(self, channel, oldest, max_pages):
        out, cursor = [], ""
        for _ in range(max_pages):
            params = {"channel": channel, "oldest": "%.6f" % oldest, "limit": 200}
            if cursor:
                params["cursor"] = cursor
            doc = self._get("conversations.history", params)
            out.extend(m for m in (doc.get("messages") or []) if isinstance(m, dict))
            cursor = (doc.get("response_metadata") or {}).get("next_cursor") or ""
            if not cursor:
                return out
        raise BridgeError("channel %s has more than %d pages of history in the window — "
                          "refusing to guess which messages were read" % (channel, max_pages))

    def replies(self, channel, ts):
        out, cursor = [], ""
        for _ in range(MAX_THREAD_PAGES):
            params = {"channel": channel, "ts": ts, "limit": 200}
            if cursor:
                params["cursor"] = cursor
            doc = self._get("conversations.replies", params)
            out.extend(m for m in (doc.get("messages") or []) if isinstance(m, dict))
            cursor = (doc.get("response_metadata") or {}).get("next_cursor") or ""
            if not cursor:
                return out
        raise BridgeError("thread %s has more than %d pages of replies" % (ts, MAX_THREAD_PAGES))

    def user(self, user_id):
        return self._get("users.info", {"user": user_id}).get("user") or {}

    def post(self, channel, thread_ts, text):
        try:
            doc = _http_json("%s/chat.postMessage" % self.base,
                             {"Authorization": "Bearer %s" % self.token,
                              "Content-Type": "application/json; charset=utf-8"},
                             {"channel": channel, "thread_ts": thread_ts, "text": text,
                              "unfurl_links": False, "unfurl_media": False})
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise BridgeError("Slack chat.postMessage failed (%s)" % type(exc).__name__)
        if not isinstance(doc, dict) or not doc.get("ok") or not doc.get("ts"):
            raise BridgeError("Slack chat.postMessage refused: %s"
                              % ((doc or {}).get("error") if isinstance(doc, dict) else "?"))
        return doc["ts"]


Q_VIEWER = "query BridgeViewer { viewer { id } }"
Q_ISSUE = """
query BridgeIssue($id: String!) {
  issue(id: $id) {
    id identifier title createdAt
    state { name type }
    team { key }
    creator { name displayName }
  }
}"""


class TrackerClient(object):
    """The tracker, READ ONLY in this version: two queries, one fixed endpoint."""

    def __init__(self, key):
        self.key = key

    def _query(self, query, variables):
        try:
            doc = _http_json(TRACKER_API, {"Authorization": self.key,
                                           "Content-Type": "application/json"},
                             {"query": query, "variables": variables})
        except urllib.error.HTTPError as exc:
            # The tracker answers an unknown id with an error STATUS and a GraphQL body.
            # Read the body before giving up, or one mistyped request stops every pass. A
            # body with no GraphQL errors in it (an outage, a proxy's 503, nothing at all)
            # is "could not check", never "not found".
            try:
                doc = json.loads((exc.read() or b"").decode("utf-8") or "{}")
            except (AttributeError, OSError, ValueError):
                doc = None
            if not (isinstance(doc, dict) and doc.get("errors")):
                raise BridgeError("the tracker answered HTTP %s" % exc.code)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise BridgeError("the tracker could not be read (%s)" % type(exc).__name__)
        if not isinstance(doc, dict) or doc.get("errors"):
            msgs = [str(e.get("message"))[:120] for e in (doc or {}).get("errors") or []
                    if isinstance(e, dict)] if isinstance(doc, dict) else []
            if any("not found" in m.lower() or "entity not found" in m.lower() for m in msgs):
                return None
            raise BridgeError("the tracker refused a read: %s" % ("; ".join(msgs) or "?"))
        if not isinstance(doc.get("data"), dict):
            raise BridgeError("the tracker's answer carried no data")
        return doc["data"]

    def viewer_id(self):
        return ((self._query(Q_VIEWER, {}) or {}).get("viewer") or {}).get("id")

    def issue(self, identifier):
        data = self._query(Q_ISSUE, {"id": identifier})
        return (data or {}).get("issue")


# ══════════════════════════════════════════════════════════════════════════════════════
# Pure logic
# ══════════════════════════════════════════════════════════════════════════════════════

def key_of(channel, ts):
    return "%s:%s" % (channel, ts)


def ts_float(ts):
    try:
        return float(ts)
    except (TypeError, ValueError):
        return 0.0


def command_text(msg, own_user_id):
    """The message as a command line: entities decoded, brackets gone, formatting and a
    leading mention of the bridge itself dropped, whitespace collapsed."""
    text = notify.decode_chat_entities(msg.get("text") or "").strip()
    m = _LEADING_MENTION_RE.match(text)
    if m and m.group(1) == own_user_id:
        text = text[m.end():]
    text = text.strip().strip("*_`~").strip()
    return re.sub(r"\s+", " ", text)


def message_refusal(msg, me):
    """Why this message cannot answer a question, from the message alone, or None."""
    if msg.get("bot_id") or msg.get("subtype"):
        return "it is a bot post or a %s message" % (msg.get("subtype") or "bot")
    if msg.get("edited"):
        return "it was edited after it was sent; send the answer again as a new reply"
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


def question_text(issue, requester, cfg, now, by_bot=False):
    title = notify.safe_title(issue.get("title") or "")
    creator = notify.safe_title(((issue.get("creator") or {}).get("displayName")
                                 or (issue.get("creator") or {}).get("name") or "someone"))
    state = notify.safe_title((issue.get("state") or {}).get("name") or "an unknown state")
    team = ((issue.get("team") or {}).get("key")) or "?"
    # A bot's request is credited in words, never @mentioned: a mention of the chat bot
    # in its own thread could start a chat session whose reply lands after the question.
    if by_bot:
        who = "the chat bot"
    else:
        who = ("<@%s>" % requester) if SLACK_USER_RE.match(requester or "") else "a bot"
    then = ("the bridge moves it to Plan it for you" if cfg["act"] else
            "the bridge records it; this version moves nothing yet")
    return ("*Plan this?* %s “%s”: in %s on %s, filed by %s.\n"
            "Reply `yes` in this thread within %d hours, and %s. Reply `no` to drop it. "
            "(Asked for by %s.)"
            % (issue.get("identifier"), title, state, team, creator,
               cfg["question_ttl_hours"], then, who))


def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ══════════════════════════════════════════════════════════════════════════════════════
# One pass
# ══════════════════════════════════════════════════════════════════════════════════════

class Pass(object):
    def __init__(self, cfg, slack, tracker, state, first, dry_run, now, out):
        self.cfg, self.slack, self.tracker, self.state = cfg, slack, tracker, state
        self.first, self.dry, self.now, self.out = first, dry_run, now, out
        self.me = None
        self.users = {}
        self.threads = {}                  # (channel, root ts) -> messages read this pass
        self.unrecorded = {}               # thread key -> latest_reply, once all handled
        self.secrets = live_secrets(cfg)
        self.counts = {"examined": 0, "asked": 0, "confirmed": 0, "declined": 0,
                       "expired": 0, "refused": 0, "baseline": 0, "gave_up": 0}

    def clean(self, text):
        return notify.redact(text, self.secrets)

    def say(self, line):
        self.out.write(self.clean(line) + "\n")

    def save(self):
        """Write-through: after every message, so a later failure loses nothing said."""
        if not self.dry:
            save_state(self.cfg, self.state)

    def post(self, channel, thread_ts, text):
        text = self.clean(text)
        if self.dry:
            self.say("  [dry-run] would post in %s thread %s (%d chars)"
                     % (channel, thread_ts, len(text)))
            return "%.6f" % self.now
        ts = self.slack.post(channel, thread_ts, text)
        self.state["posted"][key_of(channel, ts)] = self.now
        return ts

    def mark(self, channel, msg, what):
        # Redacted BEFORE it is cut short: a cut credential would no longer match.
        self.state["handled"][key_of(channel, msg.get("ts"))] = {
            "at": iso(self.now), "what": self.clean(what)[:160]}
        self.state["attempts"].pop(key_of(channel, msg.get("ts")), None)

    def account(self, uid):
        """Slack's own `users.info` for a sender, read once a pass."""
        if uid not in self.users:
            self.users[uid] = self.slack.user(uid)
        return self.users[uid]

    # -- reading -------------------------------------------------------------------
    def read_thread(self, channel, root):
        msgs = self.slack.replies(channel, root)
        self.threads[(channel, root)] = msgs
        return [r for r in msgs if r.get("ts") != root]

    def gather(self, channel):
        """Every message in the window this pass must look at, oldest first. A message
        older than the window is never handled, wherever it was found."""
        oldest = self.now - self.cfg["lookback_days"] * 86400
        tops = self.slack.history(channel, oldest, self.cfg["max_history_pages"])
        msgs = list(tops)
        for m in tops:
            root = m.get("ts")
            if not m.get("reply_count"):
                continue
            seen = self.state["threads"].get(key_of(channel, root))
            if m.get("latest_reply") and m.get("latest_reply") == seen:
                continue
            msgs.extend(self.read_thread(channel, root))
            # Recorded as read only once every message gathered here is handled (run): a
            # failure or the deadline leaves it unrecorded, so the next pass reads it again.
            self.unrecorded[key_of(channel, root)] = m.get("latest_reply")
        # The thread of every OPEN question is read whatever the window says: its root
        # may be older than the window, and its answer must still be heard.
        for q in self.state["questions"].values():
            if (q.get("status") == OPEN and q.get("channel") == channel
                    and (channel, q.get("thread")) not in self.threads):
                msgs.extend(self.read_thread(channel, q["thread"]))
        unique = {}
        for m in msgs:
            if m.get("ts") and ts_float(m.get("ts")) >= oldest:
                unique[m["ts"]] = m
        return [unique[t] for t in sorted(unique, key=ts_float)]

    def run(self):
        self.me = self.slack.auth_test()
        if not (self.me.get("user_id") and self.me.get("team_id")):
            raise BridgeError("Slack auth.test named no bot user or workspace")
        if self.me["user_id"] != self.cfg["bot_user_id"]:
            raise BridgeError("the Slack token answers to %s, not the bridge app's bot "
                              "(bot_user_id). Nothing was read. Put the bridge app's own "
                              "token in place" % self.me["user_id"], EXIT_USAGE)
        viewer = self.tracker.viewer_id()
        if viewer != self.cfg["owner_user_id"]:
            raise BridgeError("the tracker key belongs to %s, not owner_user_id. The bridge "
                              "acts as the owner, so it refuses to run with anyone else's "
                              "key" % (viewer or "nobody"), EXIT_USAGE)
        for channel in self.cfg["channel_ids"]:
            for msg in self.gather(channel):
                k = key_of(channel, msg.get("ts"))
                if k in self.state["handled"]:
                    continue
                self.counts["examined"] += 1
                if self.first:
                    self.mark(channel, msg, "baseline")
                    self.counts["baseline"] += 1
                    continue
                self.handle_once(channel, msg)
                self.save()
            self.state["threads"].update(self.unrecorded)
            self.unrecorded = {}
            self.save()
        self.expire()
        self.prune()
        self.save()

    def handle_once(self, channel, msg):
        """One message, with a failure counted against it. Three failed passes and it is
        given up on, said once, and passed over: one bad message must not stall every
        pass after it. Short of that the failure stops the pass, and the next one retries."""
        k = key_of(channel, msg.get("ts"))
        try:
            self.handle(channel, msg)
        except BridgeError as exc:
            tries = self.state["attempts"].get(k, 0) + 1
            self.state["attempts"][k] = tries
            if tries < 3:
                self.save()
                raise
            # The log line and the record are redacted where they are written (say, mark).
            self.mark(channel, msg, "gave up: %s" % exc)
            self.counts["gave_up"] += 1
            self.say("  gave up on message %s after %d failed passes: %s"
                     % (msg.get("ts"), tries, exc))
            try:
                self.post(channel, msg.get("thread_ts") or msg.get("ts"),
                          "I could not handle this message after %d tries, so I have "
                          "stopped trying. Nothing was changed." % tries)
            except BridgeError:
                pass

    # -- one message ---------------------------------------------------------------
    def handle(self, channel, msg):
        if msg.get("user") == self.me["user_id"] or (
                self.me.get("bot_id") and msg.get("bot_id") == self.me["bot_id"]):
            self.mark(channel, msg, "own")
            return
        text = command_text(msg, self.me["user_id"])
        req = REQUEST_RE.match(text)
        if req:
            self.request(channel, msg, req.group(1).lower(), req.group(2).upper())
            return
        yes, no = YES_RE.match(text), NO_RE.match(text)
        if (yes or no) and msg.get("thread_ts") and msg.get("thread_ts") != msg.get("ts"):
            self.answer(channel, msg, bool(yes), ((yes or no).group(1) or "").upper())
            return
        self.mark(channel, msg, "ignored")

    def refuse_request(self, channel, msg, thread, text, why):
        self.post(channel, thread, text)
        self.mark(channel, msg, "refused: %s" % why)
        self.counts["refused"] += 1

    def request(self, channel, msg, kind, ident):
        thread = msg.get("thread_ts") or msg.get("ts")
        requester = msg.get("user") or msg.get("bot_id") or ""
        by_bot = bool(msg.get("bot_id") or msg.get("app_id"))
        if kind == "approve":
            # Recognised so it is never silent. The action is a later ticket (KIT-223);
            # nothing is read and nothing is changed.
            self.refuse_request(channel, msg, thread, "Approving from Slack is not built in "
                                "this version of the bridge, so nothing was changed.",
                                "approve is not built")
            return
        # Every request that would read the tracker counts, found or not: the key is the
        # owner's, shared with Stage E. Past the limit a request is refused with no read,
        # and that is said at most once an hour in a channel.
        hour_ago = self.now - 3600
        recent = [t for t in self.state["asked_at"] if t > hour_ago]
        if len(recent) >= self.cfg["max_questions_per_hour"]:
            if self.state["limit_said"].get(channel, 0) <= hour_ago:
                self.post(channel, thread, "I have looked up %d tickets in the last hour, "
                          "which is my limit. Ask again later." % len(recent))
                self.state["limit_said"][channel] = self.now
            self.mark(channel, msg, "refused: rate limit")
            self.counts["refused"] += 1
            return
        open_now = [q for q in self.state["questions"].values() if q.get("status") == OPEN]
        if len(open_now) >= self.cfg["max_open_questions"]:
            self.refuse_request(channel, msg, thread, "%d questions are already open, which "
                                "is my limit. Answer one, or let it expire, then ask again."
                                % len(open_now), "too many open")
            return
        for q in open_now:
            if q.get("ticket") == ident:
                self.refuse_request(channel, msg, thread, "%s already has an open question, "
                                    "in %s. Answer that one." % (ident, "this thread" if
                                                               q.get("thread") == thread
                                                               else "another thread"),
                                    "already asked")
                return
        self.state["asked_at"].append(self.now)
        issue = self.tracker.issue(ident)
        if not issue or not issue.get("identifier"):
            self.refuse_request(channel, msg, thread, "I can't find %s in the tracker."
                                % ident, "not found")
            return
        team = (issue.get("team") or {}).get("key")
        if team not in self.cfg["team_keys"]:
            self.refuse_request(channel, msg, thread, "%s is on %s, and the bridge plans only "
                                "on %s." % (issue["identifier"], team or "no team",
                                            ", ".join(self.cfg["team_keys"])),
                                "team not planned")
            return
        posted = self.post(channel, thread, question_text(issue, requester, self.cfg,
                                                          self.now, by_bot=by_bot))
        self.state["questions"][key_of(channel, posted)] = {
            "kind": kind, "ticket": issue["identifier"], "ticket_id": issue.get("id"),
            "channel": channel, "thread": thread, "ts": posted, "asked_at": self.now,
            "asked_for": requester, "status": OPEN}
        self.mark(channel, msg, "asked: %s %s" % (kind, issue["identifier"]))
        self.counts["asked"] += 1
        self.say("  asked: %s %s (requested by %s)" % (kind, issue["identifier"],
                                                       requester or "?"))

    def foreign_post_after(self, channel, q, msg):
        """True when anything the bridge cannot vouch for posted in the question's thread
        between the question and this answer: a bot or an app, Slackbot, an account that
        could not answer itself (a guest, another workspace, no sender named), or the
        bridge's own account posting something it has no record of. Any of them could be a
        look-alike question. A verified member's post is not."""
        lo, hi = ts_float(q.get("ts")), ts_float(msg.get("ts"))
        for m in self.threads.get((channel, q.get("thread")), []):
            t = ts_float(m.get("ts"))
            if not lo < t < hi:
                continue
            ours = (m.get("user") == self.me["user_id"]
                    or (self.me.get("bot_id") and m.get("bot_id") == self.me["bot_id"]))
            if ours:
                if key_of(channel, m.get("ts")) not in self.state["posted"]:
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

    def question_closed(self, channel, q):
        """Why the question can no longer be answered as asked, or None. A question gone
        from its thread (deleted) is closed, never taken as unchanged."""
        for m in self.threads.get((channel, q.get("thread")), []):
            if m.get("ts") == q.get("ts"):
                return "was changed after I asked it" if m.get("edited") else None
        return "is no longer in this thread"

    def answer(self, channel, msg, is_yes, hint):
        thread = msg["thread_ts"]
        here = [(k, q) for k, q in self.state["questions"].items()
                if q.get("channel") == channel and q.get("thread") == thread
                and ts_float(q.get("ts")) < ts_float(msg.get("ts"))]
        open_here = [(k, q) for k, q in here if q.get("status") == OPEN]
        if hint:
            open_here = [(k, q) for k, q in open_here if q.get("ticket") == hint]
        if not open_here:
            if here:
                self.post(channel, thread, "There is no open question%s in this thread to "
                          "answer." % (" about %s" % hint if hint else ""))
                self.mark(channel, msg, "refused: no open question")
                self.counts["refused"] += 1
            else:
                self.mark(channel, msg, "ignored: no question here")
            return
        word = "yes" if is_yes else "no"
        if len(open_here) > 1:
            self.post(channel, thread, "%d questions are open in this thread. Reply `%s "
                      "<ticket id>` to say which." % (len(open_here), word))
            self.mark(channel, msg, "refused: ambiguous")
            self.counts["refused"] += 1
            return
        qkey, q = open_here[0]
        # From here on a question's status changes only AFTER the reply saying so is
        # posted: a failed post leaves it open, and the next pass says it.
        closed = self.question_closed(channel, q)
        if closed:
            self.post(channel, thread, "My question about %s %s, so I have closed it. Ask "
                      "again with `plan %s`." % (q["ticket"], closed, q["ticket"]))
            q["status"] = EXPIRED
            self.mark(channel, msg, "refused: question %s" % closed)
            self.counts["refused"] += 1
            return
        if not hint and self.foreign_post_after(channel, q, msg):
            self.post(channel, thread, "Something other than me posted in this thread after "
                      "my question, so I can't be sure which question you are answering. "
                      "Reply `%s %s` to answer the one about %s." % (word, q["ticket"],
                                                                     q["ticket"]))
            self.mark(channel, msg, "refused: something else posted after the question")
            self.counts["refused"] += 1
            return
        why = message_refusal(msg, self.me)
        if why is None:
            why = account_refusal(self.account(msg["user"]), self.me, self.cfg)
        if why:
            self.post(channel, thread, "I can't take that as an answer to the question about "
                      "%s: %s." % (q["ticket"], why))
            self.mark(channel, msg, "refused: %s" % why)
            self.counts["refused"] += 1
            self.say("  refused an answer on %s: %s" % (q["ticket"], why))
            return
        # Judged by when the answer was SENT: a yes sent in time while the Mac slept counts.
        if ts_float(msg.get("ts")) - q["asked_at"] > self.cfg["question_ttl_hours"] * 3600:
            self.post(channel, thread, "The question about %s has expired. Ask again with "
                      "`plan %s`." % (q["ticket"], q["ticket"]))
            q["status"] = EXPIRED
            self.mark(channel, msg, "refused: expired")
            self.counts["expired"] += 1
            return
        if not is_yes:
            self.post(channel, thread, "OK, %s is not planned." % q["ticket"])
            q.update(status=DECLINED, answered_by=msg["user"], answered_ts=msg.get("ts"))
            self.mark(channel, msg, "declined: %s" % q["ticket"])
            self.counts["declined"] += 1
            return
        self.post(channel, thread, "Confirmed by <@%s>. This version of the bridge acts on "
                  "nothing yet, so %s was not changed." % (msg["user"], q["ticket"]))
        q.update(status=CONFIRMED, answered_by=msg["user"], answered_ts=msg.get("ts"))
        self.mark(channel, msg, "confirmed: %s" % q["ticket"])
        self.counts["confirmed"] += 1
        self.say("  confirmed: %s %s by %s (acts on nothing in this version)"
                 % (q["kind"], q["ticket"], msg["user"]))

    # -- housekeeping --------------------------------------------------------------
    def expire(self):
        ttl = self.cfg["question_ttl_hours"] * 3600
        for q in self.state["questions"].values():
            if q.get("status") == OPEN and self.now - q.get("asked_at", 0) > ttl:
                q["status"] = EXPIRED
                self.counts["expired"] += 1

    def prune(self):
        horizon = self.now - (self.cfg["lookback_days"] + 1) * 86400

        def fresh(k):
            return ts_float(k.rsplit(":", 1)[-1]) >= horizon
        for name in ("handled", "threads", "posted", "attempts"):
            self.state[name] = dict((k, v) for k, v in self.state[name].items() if fresh(k))
        self.state["questions"] = dict(
            (k, q) for k, q in self.state["questions"].items()
            if q.get("status") == OPEN or q.get("asked_at", 0) >= horizon)
        self.state["asked_at"] = [t for t in self.state["asked_at"] if t > self.now - 3600]
        self.state["limit_said"] = dict((c, t) for c, t in self.state["limit_said"].items()
                                        if t > self.now - 3600)


def run_once(cfg, slack, tracker, dry_run, now=None, out=sys.stdout):
    state, first = load_state(cfg)
    p = Pass(cfg, slack, tracker, state, first, dry_run, now or time.time(), out)
    p.run()
    c = p.counts
    summary = ("bridge: %d message(s) examined, %d asked, %d confirmed, %d declined, "
               "%d expired, %d refused, %d given up%s%s" % (
                   c["examined"], c["asked"], c["confirmed"], c["declined"], c["expired"],
                   c["refused"], c["gave_up"], "; first pass: %d already in the window, "
                   "answered none" % c["baseline"] if first else "",
                   " (dry run)" if dry_run else ""))
    out.write(summary + "\n")
    return dict(c, exit=EXIT_OK, dry=bool(dry_run), summary=summary)


def run_command(cfg, dry_run, timeout, slack=None, tracker=None, out=sys.stdout, now=None):
    import fcntl

    def _alarm(_signum, _frame):
        raise Deadline()

    armed = False
    secrets = live_secrets(cfg)
    lock = None
    if timeout and hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, _alarm)
        signal.alarm(int(timeout))
        armed = True
    try:
        os.makedirs(cfg["state_dir"], exist_ok=True)
        lock = open(lock_path(cfg), "a")
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise BridgeError("another pass holds the bridge's lock, so this one did "
                              "nothing", EXIT_DECLINED)
        if slack is None or tracker is None:
            token = credential(cfg, "slack_token_env")
            key = credential(cfg, "linear_key_env")
            slack = slack or SlackClient(cfg, token)
            tracker = tracker or TrackerClient(key)
        result = run_once(cfg, slack, tracker, dry_run, now=now, out=out)
    except Deadline:
        result = {"exit": EXIT_TIMEOUT, "dry": bool(dry_run),
                  "summary": "FAIL: the %ds deadline passed; this pass is PARTIAL. Every "
                             "message it finished was saved as it went" % timeout}
        out.write(result["summary"] + "\n")
    except BridgeError as exc:
        result = {"exit": exc.code, "dry": bool(dry_run),
                  "summary": "FAIL: %s" % notify.redact(str(exc), secrets)}
        out.write(result["summary"] + "\n")
    except Exception as exc:                                       # noqa: BLE001
        result = {"exit": EXIT_ERROR, "dry": bool(dry_run),
                  "summary": "FAIL: unexpected error: %s"
                             % notify.redact("%s: %s" % (type(exc).__name__, exc), secrets)}
        out.write(result["summary"] + "\n")
    finally:
        if armed:
            signal.alarm(0)
        if lock is not None:
            lock.close()
    if not dry_run:
        write_heartbeat(cfg, result, iso(time.time()))
    return result["exit"]


# ══════════════════════════════════════════════════════════════════════════════════════
# Selftest — offline, every transport faked
# ══════════════════════════════════════════════════════════════════════════════════════

TEAM = "T0000000AA"
ME = "U0BRIDGE01"
BOT = "B0BRIDGE01"
OWNER = "00000000-0000-4000-8000-000000000001"
CHAN = "C0000000AA"
ALICE, BOB, GUEST, GONE, OTHERWS, CHATBOT = ("U0ALICE001", "U0BOB00001", "U0GUEST001",
                                             "U0GONE0001", "U0OTHER001", "U0CHATBOT1")
APPUSER, UGUEST, NOTEAM = "U0APPUSER1", "U0UGUEST01", "U0NOTEAM01"


class FakeSlack(object):
    def __init__(self, now):
        self.now = now
        self.messages = []                # top-level and replies, dicts with ts/thread_ts
        self.posts = []
        self.fail = set()
        self.reads = []
        self.profiles = {
            ALICE: {"id": ALICE, "team_id": TEAM},
            BOB: {"id": BOB, "team_id": TEAM},
            GUEST: {"id": GUEST, "team_id": TEAM, "is_restricted": True},
            GONE: {"id": GONE, "team_id": TEAM, "deleted": True},
            OTHERWS: {"id": OTHERWS, "team_id": "T0OTHER000"},
            CHATBOT: {"id": CHATBOT, "team_id": TEAM, "is_bot": True},
            APPUSER: {"id": APPUSER, "team_id": TEAM, "is_app_user": True},
            UGUEST: {"id": UGUEST, "team_id": TEAM, "is_ultra_restricted": True},
            NOTEAM: {"id": NOTEAM},
            # Slack documents Slackbot as is_bot false, so only its id gives it away.
            "USLACKBOT": {"id": "USLACKBOT", "team_id": TEAM},
        }
        self._clock = 0

    def add(self, user, text, thread=None, at=None, **extra):
        self._clock += 1
        ts = "%.6f" % (at if at is not None else self.now - 10000 + self._clock)
        m = dict({"ts": ts, "text": text}, **extra)
        if user:
            m["user"] = user
        if thread:
            m["thread_ts"] = thread
        self.messages.append(m)
        return ts

    def _check(self, method):
        self.reads.append(method)
        if method in self.fail:
            raise BridgeError("Slack %s refused: simulated" % method)

    def auth_test(self):
        self._check("auth.test")
        return {"user_id": ME, "team_id": TEAM, "bot_id": BOT}

    def _tops(self):
        out = []
        for m in self.messages:
            if m.get("thread_ts") and m["thread_ts"] != m["ts"]:
                continue
            top = dict(m)
            kids = [r for r in self.messages if r.get("thread_ts") == m["ts"]
                    and r["ts"] != m["ts"]]
            if kids:
                top["reply_count"] = len(kids)
                top["latest_reply"] = max(r["ts"] for r in kids)
            out.append(top)
        return out

    def history(self, channel, oldest, max_pages):
        self._check("conversations.history")
        return [m for m in self._tops() if float(m["ts"]) >= oldest]

    def replies(self, channel, ts):
        self._check("conversations.replies")
        return [m for m in self.messages if m["ts"] == ts or m.get("thread_ts") == ts]

    def user(self, uid):
        self._check("users.info")
        return self.profiles.get(uid, {})

    def post(self, channel, thread_ts, text):
        self._check("chat.postMessage")
        self._clock += 1
        ts = "%.6f" % (self.now - 10000 + self._clock)
        self.posts.append((channel, thread_ts, text))
        self.messages.append({"ts": ts, "thread_ts": thread_ts, "text": text,
                              "user": ME, "bot_id": BOT})
        return ts


class FakeTracker(object):
    def __init__(self, viewer=OWNER):
        self.viewer = viewer
        self.issues = {
            "PROD-5": {"id": "uuid-5", "identifier": "PROD-5", "title": "Dark mode <!channel>",
                       "createdAt": "2026-10-10T12:00:00Z",
                       "state": {"name": "Backlog", "type": "backlog"},
                       "team": {"key": "PROD"}, "creator": {"displayName": "Chat bot"}},
            "PROD-6": {"id": "uuid-6", "identifier": "PROD-6", "title": "Light mode",
                       "state": {"name": "Backlog", "type": "backlog"},
                       "team": {"key": "PROD"}, "creator": {"displayName": "Chat bot"}},
            "OPS-1": {"id": "uuid-o1", "identifier": "OPS-1", "title": "Other team",
                      "state": {"name": "Backlog", "type": "backlog"},
                      "team": {"key": "OPS"}, "creator": {"displayName": "Someone"}},
        }
        self.reads = 0
        self.fail = False

    def viewer_id(self):
        return self.viewer

    def issue(self, ident):
        self.reads += 1
        if self.fail:
            raise BridgeError("the tracker could not be read (simulated)")
        return self.issues.get(ident)


def _config_error(raw):
    try:
        validate_config(raw)
        return ""
    except BridgeError as exc:
        return str(exc)


def selftest():
    import io
    import tempfile
    failures, cases = [], [0]

    def check(name, got, want):
        cases[0] += 1
        if got != want:
            failures.append("%s: got %r, want %r" % (name, got, want))

    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    body = src[:src.index("# Selftest — offline")]

    with tempfile.TemporaryDirectory() as tmp:
        def cfg_for(name, **over):
            raw = dict(EXAMPLE_CONFIG, channel_ids=[CHAN], owner_user_id=OWNER,
                       bot_user_id=ME, state_dir=os.path.join(tmp, name))
            raw.update(over)
            return validate_config(raw)

        now = 1760000000.0

        def world(name, **over):
            cfg = cfg_for(name, **over)
            slack, tracker = FakeSlack(now), FakeTracker()
            return cfg, slack, tracker

        def run(cfg, slack, tracker, dry=False, at=None):
            out = io.StringIO()
            try:
                res = run_once(cfg, slack, tracker, dry, now=at or now, out=out)
                return res["exit"], out.getvalue()
            except BridgeError as exc:
                return exc.code, str(exc)

        def posted(slack):
            return [t for _c, _th, t in slack.posts]

        def status_of(cfg):
            doc = json.load(open(state_path(cfg)))
            return [v["status"] for v in doc["questions"].values()]

        # 1. FIRST PASS: everything already in the window is seen, and nothing is asked.
        cfg, slack, tracker = world("first")
        slack.add(ALICE, "plan PROD-5")
        code, out = run(cfg, slack, tracker)
        check("first-pass-asks-nothing", (code, slack.posts, "first pass" in out),
              (EXIT_OK, [], True))
        # ... and the second pass hears a NEW request, and asks in its thread.
        req = slack.add(ALICE, "plan prod-6")
        code, out = run(cfg, slack, tracker)
        check("request-asks-in-its-thread", (code, len(slack.posts), slack.posts[0][1]),
              (EXIT_OK, 1, req))
        q = posted(slack)[0]
        check("question-carries-tracker-facts",
              ("PROD-6" in q, "Light mode" in q, "Backlog" in q, "Chat bot" in q,
               "Reply `yes`" in q, "<@%s>" % ALICE in q), (True,) * 6)
        state = json.load(open(state_path(cfg)))
        check("question-recorded", [(v["ticket"], v["status"], v["thread"])
                                    for v in state["questions"].values()],
              [("PROD-6", OPEN, req)])
        # The same pass again: nothing new, nothing asked twice.
        run(cfg, slack, tracker)
        check("request-handled-once", len(slack.posts), 1)

        # 2. A VERIFIED MEMBER'S YES confirms — and in this version changes nothing.
        slack.add(BOB, "Yes.", thread=req)
        code, out = run(cfg, slack, tracker)
        check("yes-confirms", (code, "Confirmed by <@%s>" % BOB in posted(slack)[-1],
                               "was not changed" in posted(slack)[-1]), (EXIT_OK, True, True))
        state = json.load(open(state_path(cfg)))
        check("yes-recorded", [(v["status"], v.get("answered_by"))
                               for v in state["questions"].values()], [(CONFIRMED, BOB)])
        slack.add(ALICE, "yes", thread=req)
        run(cfg, slack, tracker)
        check("second-yes-does-nothing", sum("Confirmed" in t for t in posted(slack)), 1)

        # 3. EVERY SENDER THAT IS NOT A VERIFIED FULL MEMBER IS REFUSED, and says why.
        def refused_by(name, msg_user, extra=None, allowed=None, text="yes"):
            c, s, t = world(name, **({"allowed_slack_users": allowed} if allowed else {}))
            run(c, s, t)                                   # the first pass, empty
            root = s.add(ALICE, "plan PROD-5")
            run(c, s, t)
            s.add(msg_user, text, thread=root, **(extra or {}))
            code, _o = run(c, s, t)
            st = json.load(open(state_path(c)))
            return code, [v["status"] for v in st["questions"].values()], posted(s)[-1]

        for name, user, extra, needle in (
                ("guest", GUEST, None, "guest"),
                ("deactivated", GONE, None, "deactivated"),
                ("bot-account", CHATBOT, None, "bot or an app"),
                ("other-workspace-account", OTHERWS, None, "another Slack workspace"),
                ("bot-post", ALICE, {"bot_id": "B0OTHER001"}, "bot post"),
                ("subtype", ALICE, {"subtype": "thread_broadcast"}, "thread_broadcast"),
                ("edited", ALICE, {"edited": {"user": ALICE, "ts": "1"}}, "edited"),
                ("file", ALICE, {"files": [{"id": "F1"}]}, "file"),
                ("other-workspace-message", ALICE, {"user_team": "T0OTHER000"},
                 "another Slack workspace"),
                ("other-workspace-team-field", ALICE, {"team": "T0OTHER000"},
                 "another Slack workspace"),
                ("other-workspace-source-team", ALICE, {"source_team": "T0OTHER000"},
                 "another Slack workspace"),
                ("app-user", APPUSER, None, "bot or an app"),
                ("ultra-restricted-guest", UGUEST, None, "guest"),
                ("no-workspace-named", NOTEAM, None, "no workspace"),
                ("no-sender", None, None, "no sender")):
            code, statuses, last = refused_by("refuse-" + name, user, extra)
            check("refused:" + name, (code, statuses, needle in last), (EXIT_OK, [OPEN], True))
        code, statuses, last = refused_by("refuse-not-listed", BOB, allowed=[ALICE])
        check("refused:not-on-the-list", (statuses, "list of members" in last),
              ([OPEN], True))
        code, statuses, last = refused_by("allowed-listed", ALICE, allowed=[ALICE])
        check("allowed-member-confirms", statuses, [CONFIRMED])

        # 4. A yes binds to a question THIS job asked, in its thread, after it.
        c, s, t = world("binding")
        run(c, s, t)
        root = s.add(ALICE, "plan PROD-5")
        other = s.add(ALICE, "something else entirely")
        s.add(ALICE, "yes", thread=other)                   # another thread: nothing
        s.add(ALICE, "yes")                                 # top level: nothing
        run(c, s, t)
        st = json.load(open(state_path(c)))
        check("yes-elsewhere-is-nothing", ([v["status"] for v in st["questions"].values()],
                                           sum("Confirmed" in x for x in posted(s))),
              ([OPEN], 0))
        # After the question exists: a yes in ANOTHER thread still binds nothing, and a yes
        # that Slack shows with a timestamp older than the question (late, or forged order)
        # is not an answer to it.
        s.add(BOB, "yes", thread=other)
        q_ts = [v["ts"] for v in json.load(open(state_path(c)))["questions"].values()][0]
        s.messages.append({"ts": "%.6f" % (float(q_ts) - 0.5), "thread_ts": root,
                           "user": BOB, "text": "yes"})
        run(c, s, t)
        st = json.load(open(state_path(c)))
        check("yes-elsewhere-or-earlier-binds-nothing",
              ([v["status"] for v in st["questions"].values()],
               sum("Confirmed" in x for x in posted(s))), ([OPEN], 0))
        # A fake "question" posted by another bot in the thread is not one of ours.
        s.add(CHATBOT, "*Plan this?* PROD-6. Reply yes.", thread=root, bot_id="B0CHATBOT1")
        s.add(ALICE, "yes PROD-6", thread=root)
        run(c, s, t)
        st = json.load(open(state_path(c)))
        check("a-forged-question-binds-nothing",
              sorted((v["ticket"], v["status"]) for v in st["questions"].values()),
              [("PROD-5", OPEN)])
        # Two open questions in one thread: a bare yes is refused, a named one binds.
        s.add(ALICE, "plan PROD-6", thread=root)
        run(c, s, t)
        s.add(BOB, "yes", thread=root)
        run(c, s, t)
        check("two-open-bare-yes-asks-which", "2 questions are open" in posted(s)[-1], True)
        s.add(BOB, "yes prod-6", thread=root)
        run(c, s, t)
        st = json.load(open(state_path(c)))
        check("named-yes-binds-that-one",
              sorted((v["ticket"], v["status"]) for v in st["questions"].values()),
              [("PROD-5", OPEN), ("PROD-6", CONFIRMED)])
        s.add(BOB, "no", thread=root)
        run(c, s, t)
        check("a-bot-posted-after-the-question-so-a-bare-answer-is-refused",
              "Reply `no PROD-5`" in posted(s)[-1], True)
        s.add(BOB, "no prod-5", thread=root)
        run(c, s, t)
        st = json.load(open(state_path(c)))
        check("no-declines", sorted(v["status"] for v in st["questions"].values()),
              [CONFIRMED, DECLINED])

        # 5. REQUESTS that cannot become a question are refused, said once.
        c, s, t = world("requests")
        run(c, s, t)
        s.add(ALICE, "plan PROD-404")
        s.add(ALICE, "plan OPS-1")
        s.add(CHATBOT, "<@%s> plan PROD-5" % ME, bot_id="B0CHATBOT1")   # the chat bot asks
        s.add(ALICE, "plan PROD-5")                                       # a duplicate
        s.add(ALICE, "please plan PROD-6 now")                           # not the grammar
        run(c, s, t)
        texts = posted(s)
        check("request-unknown-ticket", any("can't find PROD-404" in x for x in texts), True)
        check("request-other-team", any("OPS-1 is on OPS" in x for x in texts), True)
        check("request-from-the-chat-bot-asks", any("*Plan this?* PROD-5" in x for x in texts),
              True)
        check("request-duplicate-refused", any("already has an open question" in x
                                               for x in texts), True)
        check("request-grammar-is-strict", any("PROD-6" in x for x in texts), False)
        check("question-title-made-inert", any("<!channel>" in x for x in texts), False)

        # 6. EXPIRY: a yes after the window is refused; an open question expires anyway.
        c, s, t = world("expiry", question_ttl_hours=1)
        run(c, s, t)
        root = s.add(ALICE, "plan PROD-5")
        run(c, s, t)
        s.add(BOB, "yes", thread=root, at=now + 2 * 3600)
        run(c, s, t, at=now + 2 * 3600 + 60)
        st = json.load(open(state_path(c)))
        check("late-yes-expired", ([v["status"] for v in st["questions"].values()],
                                   "expired" in posted(s)[-1]), ([EXPIRED], True))
        c, s, t = world("in-time-but-late", question_ttl_hours=1)
        run(c, s, t)
        root = s.add(ALICE, "plan PROD-5")
        run(c, s, t)
        s.add(BOB, "yes", thread=root, at=now + 1800)       # sent in time, Mac asleep
        run(c, s, t, at=now + 7200)                          # handled two hours on
        check("a-yes-sent-in-time-counts-when-handled-late", "Confirmed" in posted(s)[-1], True)
        c, s, t = world("expiry-quiet", question_ttl_hours=1)
        run(c, s, t)
        s.add(ALICE, "plan PROD-5")
        run(c, s, t)
        run(c, s, t, at=now + 2 * 3600)
        st = json.load(open(state_path(c)))
        check("unanswered-question-expires", [v["status"] for v in st["questions"].values()],
              [EXPIRED])

        # 7. RATE LIMIT on questions.
        c, s, t = world("rate", max_questions_per_hour=1)
        run(c, s, t)
        s.add(ALICE, "plan PROD-5")
        s.add(ALICE, "plan PROD-6")
        run(c, s, t)
        check("rate-limit", ("*Plan this?* PROD-5" in posted(s)[0], "my limit" in posted(s)[1]),
              (True, True))

        # 8. CATCH-UP: an open question's thread is read even when its root has left the
        #    window, and a yes sent "while the Mac slept" is heard on the next pass.
        c, s, t = world("catch-up", lookback_days=1)
        run(c, s, t)
        root = s.add(ALICE, "plan PROD-5")
        run(c, s, t)
        s.add(BOB, "yes", thread=root, at=now + 79000)    # sent while the Mac slept
        s.reads = []
        run(c, s, t, at=now + 80000)                  # the root is past the 1-day window
        check("catch-up-reads-the-open-thread", ("conversations.replies" in s.reads,
                                                 "Confirmed" in posted(s)[-1]), (True, True))

        # 9. FAILURES are loud, and leave the message for the next pass.
        c, s, t = world("fail-users")
        run(c, s, t)
        root = s.add(ALICE, "plan PROD-5")
        run(c, s, t)
        s.add(BOB, "yes", thread=root)
        s.fail = {"users.info"}
        code, out = run(c, s, t)
        check("users-info-failure-exits-1", (code, "users.info" in out), (EXIT_ERROR, True))
        s.fail = set()
        run(c, s, t)
        check("users-info-failure-retried-next-pass", "Confirmed" in posted(s)[-1], True)
        c, s, t = world("fail-history")
        s.fail = {"conversations.history"}
        check("history-failure-exits-1", run(c, s, t)[0], EXIT_ERROR)
        c, s, t = world("fail-post")
        run(c, s, t)
        s.add(ALICE, "plan PROD-5")
        s.fail = {"chat.postMessage"}
        run(c, s, t)
        s.fail = set()
        run(c, s, t)
        check("post-failure-asks-on-the-next-pass",
              sum("*Plan this?* PROD-5" in x for x in posted(s)), 1)
        c, s, t = world("fail-tracker")
        run(c, s, t)
        s.add(ALICE, "plan PROD-5")
        t.fail = True
        check("tracker-failure-exits-1", run(c, s, t)[0], EXIT_ERROR)
        c, s, t = world("not-owner")
        t.viewer = "someone-else"
        check("a-key-not-the-owners-refuses", run(c, s, t)[0], EXIT_USAGE)
        c, s, t = world("corrupt")
        os.makedirs(c["state_dir"], exist_ok=True)
        with open(state_path(c), "w") as fh:
            fh.write("{not json")
        code, out = run(c, s, t)
        check("corrupt-state-refused", (code, "cannot be read" in out, s.posts),
              (EXIT_ERROR, True, []))

        # 10. DRY RUN posts nothing and saves nothing.
        c, s, t = world("dry")
        run(c, s, t)
        before = open(state_path(c)).read()
        s.add(ALICE, "plan PROD-5")
        code, out = run(c, s, t, dry=True)
        check("dry-run-posts-and-saves-nothing",
              (code, s.posts, open(state_path(c)).read() == before, "would post" in out),
              (EXIT_OK, [], True, True))

        # 11. Own posts are never commands.
        c, s, t = world("own")
        run(c, s, t)
        s.add(ME, "plan PROD-5", bot_id=BOT)
        run(c, s, t)
        check("own-posts-ignored", s.posts, [])

        # 11b. REVIEW FIXES (KIT-221 review, 2026-10-10) ------------------------------
        # An edited question is refused and closed.
        c, s, t = world("edited")
        run(c, s, t)
        root = s.add(ALICE, "plan PROD-5")
        run(c, s, t)
        for m in s.messages:
            if m.get("user") == ME and "Plan this?" in m.get("text", ""):
                m["edited"] = {"user": ME, "ts": "1"}
        s.add(BOB, "yes", thread=root)
        run(c, s, t)
        st = json.load(open(state_path(c)))
        check("edited-question-refused-and-closed",
              ([v["status"] for v in st["questions"].values()], "was changed" in posted(s)[-1]),
              ([EXPIRED], True))
        # The bridge's own account posting something it has no record of is a look-alike.
        c, s, t = world("own-unrecorded")
        run(c, s, t)
        root = s.add(ALICE, "plan PROD-5")
        run(c, s, t)
        s.add(ME, "*Plan this?* PROD-6 Light mode", thread=root, bot_id=BOT)
        s.add(BOB, "yes", thread=root)
        run(c, s, t)
        check("an-unrecorded-post-by-the-bridges-own-account-forces-a-named-yes",
              "Reply `yes PROD-5`" in posted(s)[-1], True)
        # A token answering to another bot (the chat bot's, pasted) is refused at once.
        c, s, t = world("wrong-token", bot_user_id="U0OTHERBOT")
        code, out = run(c, s, t)
        check("a-token-that-is-not-the-bridges-is-refused",
              (code, "not the bridge app's bot" in out, s.reads), (EXIT_USAGE, True,
                                                                   ["auth.test"]))
        # Slackbot and app posts are refused.
        for name, extra in (("slackbot", {"user": "USLACKBOT"}), ("app", {"app_id": "A1"})):
            c, s, t = world("refuse-" + name)
            run(c, s, t)
            root = s.add(ALICE, "plan PROD-5")
            run(c, s, t)
            m = {"ts": "%.6f" % (now - 1), "thread_ts": root, "text": "yes", "user": ALICE}
            m.update(extra)
            s.messages.append(m)
            run(c, s, t)
            check("refused:" + name, "Slackbot or an app" in posted(s)[-1], True)
        # An old message found in a re-read thread is never handled (the window rule).
        c, s, t = world("old-in-thread", lookback_days=1, question_ttl_hours=24)
        run(c, s, t, at=now)
        root = s.add(ALICE, "plan PROD-5", at=now - 100)
        run(c, s, t, at=now)
        s.add(ALICE, "plan PROD-6", thread=root, at=now - 90)      # will age out
        s.reads = []
        run(c, s, t, at=now + 86400 - 50)
        check("a-message-older-than-the-window-is-never-handled",
              any("PROD-6" in x for x in posted(s)), False)
        check("config-refuses-a-question-outliving-the-window", "longer than the window" in
              str(_config_error(dict(EXAMPLE_CONFIG, bot_user_id=ME, owner_user_id=OWNER,
                                     channel_ids=[CHAN], lookback_days=1,
                                     question_ttl_hours=48,
                                     state_dir=os.path.join(tmp, "ttl"))))
              , True)
        # Write-through: a confirmation said before a later failure is never said twice.
        c, s, t = world("write-through")
        run(c, s, t)
        r1 = s.add(ALICE, "plan PROD-5")
        r2 = s.add(ALICE, "plan PROD-6")
        run(c, s, t)
        s.add(BOB, "yes", thread=r1)
        s.add(GONE, "yes", thread=r2)
        real_user = s.user

        def flaky(uid):
            if uid == GONE:
                raise BridgeError("Slack users.info refused: simulated")
            return real_user(uid)
        s.user = flaky
        code, _o = run(c, s, t)
        s.user = real_user
        run(c, s, t)
        check("write-through-says-a-confirmation-once",
              (code, sum("Confirmed by <@%s>" % BOB in x for x in posted(s))), (EXIT_ERROR, 1))
        # Write-through also covers a failure that is not the bridge's own kind.
        c, s, t = world("write-through-crash")
        run(c, s, t)
        r1 = s.add(ALICE, "plan PROD-5")
        r2 = s.add(ALICE, "plan PROD-6")
        run(c, s, t)
        s.add(BOB, "yes", thread=r1)
        s.add(GONE, "yes", thread=r2)

        def crash(uid):
            if uid == GONE:
                raise RuntimeError("simulated crash")
            return real_user(uid)
        real_user = s.user
        s.user = crash
        try:
            run(c, s, t)
        except RuntimeError:
            pass
        s.user = real_user
        run(c, s, t)
        check("write-through-survives-a-crash",
              sum("Confirmed by <@%s>" % BOB in x for x in posted(s)), 1)
        # A message that fails three passes is given up on, said once, and passed over.
        c, s, t = world("give-up")
        run(c, s, t)
        root = s.add(ALICE, "plan PROD-5")
        run(c, s, t)
        s.add(BOB, "yes", thread=root)
        s.fail = {"users.info"}
        codes = [run(c, s, t)[0] for _ in range(3)]
        s.fail = set()
        run(c, s, t)
        st = json.load(open(state_path(c)))
        check("a-failing-message-is-given-up-on-after-three-passes",
              (codes, any("stopped trying" in x for x in posted(s)),
               [v["status"] for v in st["questions"].values()]),
              ([EXIT_ERROR, EXIT_ERROR, EXIT_OK], True, [OPEN]))
        # Too many open questions; a yes in a thread whose question is closed is answered.
        c, s, t = world("too-many", max_open_questions=1)
        run(c, s, t)
        r1 = s.add(ALICE, "plan PROD-5")
        s.add(ALICE, "plan PROD-6")
        run(c, s, t)
        check("too-many-open-questions", "already open" in posted(s)[-1], True)
        s.add(BOB, "no", thread=r1)
        run(c, s, t)
        s.add(BOB, "yes", thread=r1)
        run(c, s, t)
        check("a-yes-to-a-closed-question-is-answered", "no open question" in posted(s)[-1],
              True)
        # The question says what this version does.
        check("question-says-this-version-moves-nothing",
              "this version moves nothing yet" in question_text(
                  FakeTracker().issues["PROD-5"], ALICE, cfg_for("q"), now), True)
        # The tracker's error STATUS with a not-found body is "not found", not a stall.
        import io as _io

        def http_404_like(*_a, **_k):
            raise urllib.error.HTTPError(TRACKER_API, 400, "Bad Request", {}, _io.BytesIO(
                b'{"errors": [{"message": "Entity not found: Issue"}]}'))
        real_http = globals()["_http_json"]
        globals()["_http_json"] = http_404_like
        try:
            check("tracker-not-found-on-an-error-status", TrackerClient("k").issue("PROD-1"),
                  None)
        finally:
            globals()["_http_json"] = real_http
        # The lock: a second pass does nothing, and says so.
        import fcntl
        c, s, t = world("lock")
        os.makedirs(c["state_dir"], exist_ok=True)
        holder = open(lock_path(c), "a")
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            out = io.StringIO()
            code = run_command(c, False, 30, slack=s, tracker=t, out=out, now=now)
            check("a-second-pass-holds-off", (code, "another pass holds" in out.getvalue(),
                                              s.reads), (EXIT_DECLINED, True, []))
        finally:
            holder.close()
        # A dry run writes no heartbeat.
        c, s, t = world("dry-beat")
        run_command(c, True, 30, slack=s, tracker=t, out=io.StringIO(), now=now)
        check("dry-run-writes-no-heartbeat", os.path.exists(heartbeat_path(c)), False)
        # The Slack address is pinned to Slack, over https.
        check("config-refuses-a-non-slack-api", "chat_api_base" in str(_config_error(
            dict(EXAMPLE_CONFIG, chat_api_base="http://evil.example/api",
                 state_dir=os.path.join(tmp, "api")))), True)

        # 11c. REVIEW FIXES, second round (KIT-221 review, 2026-10-10) ----------------
        # The host pin, apart from the scheme: https alone is not enough.
        for name, base in (("another-host", "https://evil.example/api"),
                           ("a-slack-look-alike", "https://slack.com.evil.example/api")):
            check("config-refuses-%s-over-https" % name, "chat_api_base" in _config_error(
                dict(EXAMPLE_CONFIG, chat_api_base=base,
                     state_dir=os.path.join(tmp, "api"))), True)
        check("config-takes-slacks-own-api", _config_error(dict(
            EXAMPLE_CONFIG, chat_api_base="https://slack.com/api",
            state_dir=os.path.join(tmp, "api"))), "")

        # The grammar is the whole line: a reply that only STARTS with yes or no, or a
        # request with words after the id, is none of them.
        c, s, t = world("grammar-anchors")
        run(c, s, t)
        root = s.add(ALICE, "plan PROD-5")
        run(c, s, t)
        s.add(BOB, "yesterday I said we should wait on this", thread=root)
        run(c, s, t)
        check("grammar:yesterday-is-not-a-yes", status_of(c), [OPEN])
        s.add(BOB, "nobody wants this", thread=root)
        run(c, s, t)
        check("grammar:nobody-is-not-a-no", status_of(c), [OPEN])
        s.add(ALICE, "plan PROD-6 now")
        run(c, s, t)
        check("grammar:plan-with-words-after-the-id-is-not-a-request",
              any("PROD-6" in x for x in posted(s)), False)

        # `approve <id>` is recognised: answered once, nothing read, nothing changed.
        c, s, t = world("approve")
        run(c, s, t)
        s.add(ALICE, "approve PROD-5")
        run(c, s, t)
        check("approve-is-answered-once-and-changes-nothing",
              (["not built" in x for x in posted(s)], t.reads,
               json.load(open(state_path(c)))["questions"]), ([True], 0, {}))

        # A bot's request is credited in words, never @mentioned (bot_id or app_id).
        c, s, t = world("bot-credit")
        run(c, s, t)
        s.add(CHATBOT, "plan PROD-5", bot_id="B0CHATBOT1")
        s.add(CHATBOT, "plan PROD-6", app_id="A0CHATBOT1")
        run(c, s, t)
        check("a-bots-request-is-credited-in-words-never-mentioned",
              [("Asked for by the chat bot" in x, "<@%s>" % CHATBOT in x) for x in posted(s)],
              [(True, False), (True, False)])

        # Every request that reads the tracker counts, found or not; past the limit a
        # request reads nothing, and the limit is said once an hour in the channel.
        c, s, t = world("rate-unknown", max_questions_per_hour=1)
        run(c, s, t)
        for n in range(50):
            s.add(GUEST, "plan PROD-%d" % (9000 + n))
        run(c, s, t)
        st = json.load(open(state_path(c)))
        check("unknown-ticket-requests-count-against-the-hourly-limit",
              (t.reads, len(s.posts), "my limit" in posted(s)[-1],
               sum(h["what"].startswith("refused") for h in st["handled"].values())),
              (1, 2, True, 50))
        for n in range(5):
            s.add(GUEST, "plan PROD-%d" % (9100 + n))
        run(c, s, t, at=now + 60)
        check("the-limit-is-said-once-an-hour-in-a-channel", (t.reads, len(s.posts)), (1, 2))

        # A look-alike is anything the bridge cannot vouch for, not only a flagged bot.
        def lookalike(name, poster, extra=None,
                      text="*Plan this?* PROD-6 “Light mode”: in Backlog on PROD. "
                           "Reply `yes` in this thread."):
            c, s, t = world("lookalike-" + name)
            run(c, s, t)
            root = s.add(ALICE, "plan PROD-5")
            run(c, s, t)
            s.add(poster, text, thread=root, **(extra or {}))
            s.add(BOB, "yes", thread=root)
            run(c, s, t)
            return status_of(c), posted(s)[-1]

        for name, poster, extra in (("guest", GUEST, None),
                                    ("slackbot", "USLACKBOT", None),
                                    ("app-id-post", ALICE, {"app_id": "A0LOOKALIKE"}),
                                    ("bot-message-post", ALICE, {"subtype": "bot_message"})):
            sts, last = lookalike(name, poster, extra)
            check("look-alike-by-a-%s-forces-a-named-yes" % name,
                  (sts, "Reply `yes PROD-5`" in last), ([OPEN], True))
        sts, last = lookalike("member", ALICE, text="Sounds right to me.")
        check("a-members-post-after-the-question-is-no-look-alike", sts, [CONFIRMED])

        # A question gone from its thread is closed, never answered.
        c, s, t = world("question-deleted")
        run(c, s, t)
        root = s.add(ALICE, "plan PROD-5")
        run(c, s, t)
        s.messages = [m for m in s.messages if "Plan this?" not in m.get("text", "")]
        s.add(BOB, "yes", thread=root)
        run(c, s, t)
        check("a-deleted-question-is-closed-not-answered",
              (status_of(c), "no longer in this thread" in posted(s)[-1]), ([EXPIRED], True))

        # A status changes only after the reply saying so is posted: when that post fails,
        # nothing is saved, and the next pass closes the question and says so, once.
        for name, answer, said, final in (("confirm", "yes", "Confirmed by", CONFIRMED),
                                          ("decline", "no", "OK, PROD-5", DECLINED),
                                          ("expire", "yes", "has expired", EXPIRED),
                                          ("edited-close", "yes", "was changed", EXPIRED)):
            c, s, t = world("post-fails-" + name, question_ttl_hours=1)
            run(c, s, t)
            root = s.add(ALICE, "plan PROD-5")
            run(c, s, t)
            if name == "edited-close":
                for m in s.messages:
                    if "Plan this?" in m.get("text", ""):
                        m["edited"] = {"user": ME, "ts": "1"}
            late = now + 2 * 3600 if name == "expire" else None
            s.add(BOB, answer, thread=root, at=late)
            real_post = s.post

            def failing(channel, thread_ts, text, said=said, real_post=real_post):
                if said in text:
                    raise BridgeError("Slack chat.postMessage failed (simulated)")
                return real_post(channel, thread_ts, text)
            s.post = failing
            code, _o = run(c, s, t, at=late and late + 60)
            saved = status_of(c)
            s.post = real_post
            run(c, s, t, at=late and late + 120)
            check("a-failed-%s-post-saves-nothing-and-the-next-pass-says-it" % name,
                  (code, saved, status_of(c), sum(said in x for x in posted(s))),
                  (EXIT_ERROR, [OPEN], [final], 1))

        # A request posted as a thread reply survives a failed pass, and the deadline.
        c, s, t = world("thread-reply-blip")
        run(c, s, t)
        root = s.add(ALICE, "I want light mode")
        s.add(CHATBOT, "plan PROD-6", thread=root, bot_id="B0CHATBOT1")
        t.fail = True
        code, _o = run(c, s, t)
        t.fail = False
        run(c, s, t)
        check("a-thread-reply-request-is-asked-after-a-tracker-blip",
              (code, sum("*Plan this?* PROD-6" in x for x in posted(s))), (EXIT_ERROR, 1))
        # The first reply is handled with no post and no question, so nothing else would
        # make the next pass read the thread again.
        c, s, t = world("thread-reply-deadline")
        run(c, s, t)
        root = s.add(ALICE, "I want light mode")
        s.add(CHATBOT, "I filed it as PROD-6.", thread=root, bot_id="B0CHATBOT1")
        s.add(CHATBOT, "plan PROD-6", thread=root, bot_id="B0CHATBOT1")
        real_issue = t.issue

        def deadline_on_prod6(ident):
            if ident == "PROD-6":
                raise Deadline()
            return real_issue(ident)
        t.issue = deadline_on_prod6
        code = run_command(c, False, 30, slack=s, tracker=t, out=io.StringIO(), now=now)
        t.issue = real_issue
        run(c, s, t)
        check("a-thread-reply-request-is-asked-after-a-deadline-mid-thread",
              (code, [x.split("“")[0] for x in posted(s)]),
              (EXIT_TIMEOUT, ["*Plan this?* PROD-6 "]))

        # A state file of another schema is refused, like one that cannot be read.
        c, s, t = world("wrong-schema")
        os.makedirs(c["state_dir"], exist_ok=True)
        with open(state_path(c), "w") as fh:
            json.dump(dict(new_state(), schema="pipeline-bridge-state/0"), fh)
        code, out = run(c, s, t)
        check("a-state-file-of-another-schema-is-refused",
              (code, "is not a %s document" % STATE_SCHEMA in out, s.posts),
              (EXIT_ERROR, True, []))

        # The REAL clients, with only the transport stubbed: a refusal from Slack or the
        # tracker is never read as "nothing there" or "not found".
        def through(answer, call):
            def stub(_url, _headers, payload=None, timeout=20):
                return answer(payload)
            real_http = globals()["_http_json"]
            globals()["_http_json"] = stub
            try:
                return call()
            except BridgeError as exc:
                return "BridgeError: %s" % exc
            finally:
                globals()["_http_json"] = real_http

        def http_error(code, body):
            def answer(_payload):
                raise urllib.error.HTTPError(TRACKER_API, code, "x", {}, io.BytesIO(body))
            return answer

        slack_real = SlackClient(cfg_for("real-clients"), "slack-test-token")
        got = through(lambda _p: {"ok": False, "error": "not_in_channel"},
                      lambda: slack_real.history(CHAN, 0, 10))
        check("real-slack:ok-false-is-an-error-not-an-empty-channel",
              (str(got).startswith("BridgeError"), "not_in_channel" in str(got)), (True, True))
        got = through(lambda _p: {"ok": True, "messages": [{"ts": "1.0"}],
                                  "response_metadata": {"next_cursor": "more"}},
                      lambda: slack_real.history(CHAN, 0, 2))
        check("real-slack:history-past-the-page-cap-is-an-error",
              (str(got).startswith("BridgeError"), "more than 2 pages" in str(got)),
              (True, True))
        got = through(lambda _p: {"errors": [{"message": "Authentication required"}]},
                      lambda: TrackerClient("k").issue("PROD-5"))
        check("real-tracker:an-auth-error-is-an-error-not-not-found",
              str(got).startswith("BridgeError"), True)
        got = through(lambda _p: {}, lambda: TrackerClient("k").issue("PROD-5"))
        check("real-tracker:an-answer-with-no-data-is-an-error-not-not-found",
              str(got).startswith("BridgeError"), True)
        for code, raw in ((503, b""), (502, b"{}"), (429, b'{"message": "rate limited"}'),
                          (500, b'{"data": {"issue": null, "viewer": null}}')):
            for what, call in (("issue", lambda: TrackerClient("k").issue("PROD-5")),
                               ("viewer", lambda: TrackerClient("k").viewer_id())):
                got = through(http_error(code, raw), call)
                check("real-tracker:http-%d-%s-is-could-not-check" % (code, what),
                      str(got).startswith("BridgeError"), True)

        def viewer_ok_issue_503(payload):
            if "BridgeViewer" in payload["query"]:
                return {"data": {"viewer": {"id": OWNER}}}
            return http_error(503, b"")(payload)
        c, s, _t = world("tracker-outage")
        through(viewer_ok_issue_503, lambda: run(c, s, TrackerClient("k")))
        s.add(ALICE, "plan PROD-5")
        code, _o = through(viewer_ok_issue_503, lambda: run(c, s, TrackerClient("k")))
        check("a-tracker-outage-exits-1-and-is-not-said-as-not-found",
              (code, any("can't find" in x for x in posted(s))), (EXIT_ERROR, False))

        # The real transport refuses a redirect: the token never reaches where it points.
        import http.server
        import threading

        class _Redirector(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.server.hits.append((self.path, self.headers.get("Authorization")))
                if self.path.startswith("/api/"):
                    self.send_response(302)
                    self.send_header("Location", "http://127.0.0.1:%d/stolen"
                                     % self.server.server_address[1])
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = json.dumps({"ok": True, "user_id": ME, "team_id": TEAM}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), _Redirector)
        server.hits = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        saved_env = dict((k, os.environ.get(k)) for k in ("no_proxy", "NO_PROXY"))
        os.environ["no_proxy"] = os.environ["NO_PROXY"] = "127.0.0.1,localhost"
        try:
            local = SlackClient({"chat_api_base": "http://127.0.0.1:%d/api"
                                 % server.server_address[1]}, "redirect-test-token")
            try:
                local.auth_test()
                got = "followed"
            except BridgeError as exc:
                got = str(exc)
            check("transport:a-redirect-is-refused-and-the-token-never-reaches-its-target",
                  ("could not be read" in got, [h for h in server.hits if h[0] == "/stolen"],
                   [h[1] for h in server.hits]),
                  (True, [], ["Bearer redirect-test-token"]))
        finally:
            server.shutdown()
            server.server_close()
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        # 12. CONFIG: every problem at once; the chat bot's token name and `act` refused.
        os.makedirs(os.path.join(tmp, "in-a-repo", ".git"))
        try:
            validate_config({"schema": "x", "channel_ids": ["nope"], "team_keys": [],
                             "slack_token_env": "SLACK_BOT_TOKEN", "act": True,
                             "question_ttl_hours": 0, "bogus": 1,
                             "state_dir": os.path.join(tmp, "in-a-repo", "state")})
            errs = ""
        except BridgeError as exc:
            errs = str(exc)
        for needle in ("unknown config key", "'schema'", "channel_ids", "owner_user_id",
                       "team_keys", "chat bot's own name", "has no action", "question_ttl_hours",
                       "git working tree"):
            check("config-names:" + needle, needle in errs, True)

        # 13. The heartbeat and the command wrapper: a credential never reaches an output.
        c, s, t = world("heartbeat")
        os.environ["BRIDGE_SLACK_BOT_TOKEN"] = "xoxb-" + "1" * 30
        os.environ["STAGE_E_LINEAR_API_KEY"] = "lin_api_" + "2" * 30
        try:
            s.add(ALICE, "plan PROD-5")
            run(c, s, t)                                         # first pass: baseline
            s.add(ALICE, "plan PROD-6")

            def leaky(_ident):
                # Padded so the key straddles where the state record is cut short.
                raise BridgeError("the tracker echoed %s Authorization: %s"
                                  % ("x" * 90, os.environ["STAGE_E_LINEAR_API_KEY"]))
            t.issue = leaky
            out = io.StringIO()
            code = run_command(c, False, 30, slack=s, tracker=t, out=out, now=now)
            beat_text = open(heartbeat_path(c)).read()
            beat = json.loads(beat_text)
            check("heartbeat-on-failure", (code, beat["exit"], beat["schema"]),
                  (EXIT_ERROR, EXIT_ERROR, HEARTBEAT_SCHEMA))
            check("credential-redacted-everywhere",
                  ("2" * 30 in out.getvalue() + beat_text, "redacted" in beat_text),
                  (False, True))
            # ... and on the give-up path, the third failed pass: the log line, the state
            # file and the heartbeat. The state file is mode 600.
            logs = []
            for _ in range(2):
                out = io.StringIO()
                logs.append((run_command(c, False, 30, slack=s, tracker=t, out=out,
                                         now=now), out.getvalue()))
            state_text = open(state_path(c)).read()
            written = "".join(l for _c, l in logs) + state_text + open(heartbeat_path(c)).read()
            check("credential-redacted-on-the-give-up-path",
                  ([code for code, _l in logs], "gave up" in logs[-1][1],
                   os.environ["STAGE_E_LINEAR_API_KEY"][:16] in written,
                   "redacted" in state_text, "redacted" in logs[-1][1]),
                  ([EXIT_ERROR, EXIT_OK], True, False, True, True))
            check("state-file-is-mode-600", oct(os.stat(state_path(c)).st_mode & 0o777),
                  oct(0o600))
            # A Slack post is redacted too: a key pasted into a ticket's title never posts.
            c2, s2, t2 = world("redact-post")
            run(c2, s2, t2)
            t2.issues["PROD-7"] = dict(t2.issues["PROD-6"], identifier="PROD-7", title=(
                "key %s pasted" % os.environ["STAGE_E_LINEAR_API_KEY"]))
            s2.add(ALICE, "plan PROD-7")
            run(c2, s2, t2)
            check("credential-redacted-in-a-slack-post",
                  ("2" * 30 in posted(s2)[-1], "redacted" in posted(s2)[-1]), (False, True))
        finally:
            os.environ.pop("BRIDGE_SLACK_BOT_TOKEN", None)
            os.environ.pop("STAGE_E_LINEAR_API_KEY", None)
        check("missing-credential-is-usage", run_command(c, False, 30, out=io.StringIO()),
              EXIT_USAGE)

    # 14. THIS VERSION WRITES NOTHING TO THE TRACKER: no mutation anywhere above the tests.
    check("no-tracker-write", "mutation" in body.lower(), False)
    check("tracker-endpoint-fixed", body.count("https://api.linear.app/graphql"), 1)

    if failures:
        print("FAIL: pipeline_bridge selftest")
        for f in failures:
            print("  - %s" % f)
        return 1
    print("OK: pipeline_bridge selftest (%d cases)" % cases[0])
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="The Slack bridge: one pass.")
    parser.add_argument("command", nargs="?", choices=("run",), default="run")
    parser.add_argument("--config")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=int, default=None)
    parser.add_argument("--example-config", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.example_config:
        print(json.dumps(EXAMPLE_CONFIG, indent=2, sort_keys=True))
        for key, doc in sorted(CONFIG_KEYS.items()):
            sys.stderr.write("  %-24s %s\n" % (key, doc))
        return EXIT_OK
    if not args.config:
        sys.stderr.write("FAIL: --config is required (see --example-config)\n")
        return EXIT_USAGE
    try:
        cfg = load_config(args.config)
        os.makedirs(cfg["state_dir"], exist_ok=True)
    except BridgeError as exc:
        sys.stderr.write("FAIL: %s\n" % exc)
        return exc.code
    except OSError as exc:
        sys.stderr.write("FAIL: could not create state_dir: %s\n" % exc)
        return EXIT_USAGE
    timeout = args.timeout if args.timeout is not None else cfg["run_timeout_seconds"]
    return run_command(cfg, args.dry_run, timeout)


if __name__ == "__main__":
    sys.exit(main())
