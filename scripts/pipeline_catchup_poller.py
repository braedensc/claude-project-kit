#!/usr/bin/env python3
"""Stage E catch-up poller — a delegation the dispatcher never received is started late, not lost.

WHAT THIS IS, AND WHY IT EXISTS (KIT-47)

  The dispatcher (Cyrus) learns about work ONLY from Linear's webhooks. It never polls, never
  reconciles, and keeps no spool, so a delegation made while the dispatcher machine is asleep
  or off reaches it only if one of Linear's retries lands while it is back. Linear retries a
  failed delivery three times — after 1 minute, 1 hour and 6 hours — and then gives up, and
  "the webhook might be disabled by Linear, and must be re-enabled again manually"
  (https://linear.app/developers/webhooks). On the reference machine the lid closes at the
  desk: in 15 days the hourly health check saw 13 sleeps over 2 hours, seven of them longer
  than Linear's whole retry ladder. A delegation made in any of those was lost for good.

  This job is the polling Cyrus lacks. One pass, run by the dispatcher's role account:
    1. checks the dispatcher answers locally and the public front door answers, and does
       nothing else when either is down (a re-prompt then would travel a dead route);
    2. asks Linear whether the dispatcher's webhook is still enabled, and says so loudly
       when it is not — that is the failure that silently ends everything;
    3. finds agent sessions where a person delegated or prompted the dispatcher and the
       dispatcher NEVER answered — no thought, action, response, error or elicitation —
       older than Linear's retry ladder;
    4. replies ONCE under each such session's own root comment, with the owner's key, which
       Linear delivers to the dispatcher as a `prompted` event: the route the bounce driver
       proved live on 2026-09-08. A `prompted` event for a session the dispatcher has no
       record of rebuilds it and runs the issue as a new delegation (Cyrus 0.2.73,
       EdgeWorker.js 3536-3551).

WHY THE AGE FLOOR IS 7 h 30 min AND NOT LESS (owner decision, 2026-10-27)

  Cyrus 0.2.73's `created` handler has no duplicate check: a second `created` for a session
  it already runs starts a SECOND run. If this job re-prompted a session inside Linear's retry
  window and Linear's own retry then landed, the same ticket would run twice. So a session is
  only re-prompted once it is older than the whole ladder (1 min + 1 h + 6 h) plus a margin.
  A shorter outage needs nothing from this job: Linear's own retry is the catch-up. The floor
  can be configured upward, never below LADDER_SECONDS: lowering it is a code change that
  needs the live test the owner was promised first.

WHAT COUNTS AS "NEVER ANSWERED" (all of it, or the session is left alone)

  The session belongs to the configured agent user; it has no `startedAt`, `endedAt` or
  `dismissedAt`; none of its activities is an agent's (only `prompt` activities, which a
  person writes); it has a creator (Cyrus blocks a creator-less session whoever re-prompts)
  and a root comment to reply under. Its issue is on a configured team, is not a planning
  ticket (the idea gate's own poller times those out), is neither completed nor canceled, and
  is still delegated to the agent. Its age is between the floor and `lookback_hours`.

  Zero activity can be BY DESIGN — Cyrus stays silent when routing finds no repository, when
  a user is blocked with `silent`, when the issue vanished. So a session is re-prompted once,
  EVER. If it is still silent `stuck_after_seconds` after that, it is reported as stuck (one
  page through the heartbeat monitor) and never re-prompted again: a person looks.

DEDUP: THE LEDGER IS THE RECORD, AND THE THREAD IS THE BACKSTOP

  `<state_dir>/ledger.json` records each session as `owed` before the post and `posted` after
  it. Before any post the thread is read for this job's marker, so a post that landed but lost
  its answer — or a ledger that was lost — is never made twice. A corrupt ledger refuses the
  run. At most `max_per_pass` re-prompts per pass, `spacing_seconds` apart: each one starts a
  dispatcher session whose setup runs a blocking `git fetch`, which can push other webhook
  acknowledgements past Linear's 5-second budget. Over the cap is LEFT for the next pass and
  named, never dropped (§13).

WHAT IT NEVER DOES (asserted in --selftest against this file's own source)

  Its only Linear write is `commentCreate`, as a reply under an agent session's root comment.
  It creates no ticket, moves no state, sets no label, delegates nothing, never asks Linear for
  a webhook's `secret`, never touches a pull request, and never reads a chat token. It reads
  exactly one environment variable: the tracker key, by the name its config gives.

Usage:
    pipeline_catchup_poller.py run --config <poller.json> [--dry-run] [--timeout N]
    pipeline_catchup_poller.py check --config <poller.json>
    pipeline_catchup_poller.py --example-config
    pipeline_catchup_poller.py --selftest
Exit: 0 = the pass ran (it may have re-prompted nothing), or it held because the dispatcher or
      the front door did not answer; 3 = it ran and found something only a person can fix (a
      disabled webhook, a session still silent after its re-prompt); 1 = the pass could not
      complete; 2 = usage (bad arguments, config or credential); 4 = the wall clock ran out.
"""
import argparse
import json
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline_machine_tickets as machine  # noqa: E402  (what a planning ticket is — KIT-184)

HEARTBEAT_SCHEMA = "pipeline-catchup-heartbeat/1"
LEDGER_SCHEMA = "pipeline-catchup-ledger/1"
LINEAR_API = "https://api.linear.app/graphql"
USER_AGENT = "pipeline-catchup-poller/1"     # Cloudflare refuses Python's default agent

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_DECLINED, EXIT_TIMEOUT = 0, 1, 2, 3, 4
OK, FIRING, UNKNOWN = "ok", "firing", "unknown"

# Linear's documented retry ladder: 1 minute, 1 hour, 6 hours. No floor may be shorter.
LADDER_SECONDS = 60 + 3600 + 6 * 3600
DEFAULT_AGE_FLOOR_SECONDS = 27000            # 7 h 30 min: the ladder plus a margin
DEFAULT_STATE_DIR = "~/.stage-e/catchup"
DEFAULT_LOOKBACK_HOURS = 168                 # older than a week is not woken up
DEFAULT_MAX_PER_PASS = 3
DEFAULT_SPACING_SECONDS = 60
DEFAULT_STUCK_AFTER_SECONDS = 3600
DEFAULT_PAGE_SIZE = 50
DEFAULT_MAX_PAGES = 10
DEFAULT_RUN_TIMEOUT_SECONDS = 600
DEFAULT_DISPATCHER_URL = "http://127.0.0.1:3456"
AGENT_ACTIVITY_TYPES = ("AgentActivityThoughtContent", "AgentActivityActionContent",
                        "AgentActivityResponseContent", "AgentActivityErrorContent",
                        "AgentActivityElicitationContent")
MARK = "<!-- pipeline-catchup: %s -->"       # not `pipeline-escalation`: nothing pings on it
MARK_RE = re.compile(r"<!-- pipeline-catchup: ([A-Za-z0-9_-]+) -->")

CONFIG_KEYS = {
    "agent_user_id": "the dispatcher agent's Linear user id: only its sessions are read",
    "teams": "team KEYS whose delegations are caught up, e.g. [\"KIT\", \"TOD\"]. Leave the "
             "Reviews team out: the review poller times its own sessions out",
    "linear_key_env": "ENV VAR NAME holding the owner's tracker key (never the key)",
    "state_dir": "where the ledger and heartbeat live (default %s)" % DEFAULT_STATE_DIR,
    "age_floor_seconds": "re-prompt only sessions older than this; never below %d (Linear's "
                         "retry ladder), default %d" % (LADDER_SECONDS, DEFAULT_AGE_FLOOR_SECONDS),
    "lookback_hours": "never wake a session older than this (default %d)" % DEFAULT_LOOKBACK_HOURS,
    "max_per_pass": "re-prompts per pass, 1-10 (default %d)" % DEFAULT_MAX_PER_PASS,
    "spacing_seconds": "wait between two re-prompts (default %d)" % DEFAULT_SPACING_SECONDS,
    "stuck_after_seconds": "a re-prompted session still silent after this is reported "
                           "stuck (default %d)" % DEFAULT_STUCK_AFTER_SECONDS,
    "max_pages": "agent-session pages read per pass (default %d)" % DEFAULT_MAX_PAGES,
    "dispatcher_url": "the dispatcher's local base URL (default %s)" % DEFAULT_DISPATCHER_URL,
    "front_door_url": "the public status URL; empty skips that half of the gate",
    "webhook_id": "the dispatcher's Linear webhook id, once known; empty checks every "
                  "OAuth-application webhook the key can see",
    "run_timeout_seconds": "wall clock for one pass (default %d)" % DEFAULT_RUN_TIMEOUT_SECONDS,
}

EXAMPLE_CONFIG = {
    "agent_user_id": "<the dispatcher agent's Linear user UUID>",
    "teams": ["KIT", "TOD"],
    "linear_key_env": "STAGE_E_LINEAR_API_KEY",
    "state_dir": DEFAULT_STATE_DIR,
    "age_floor_seconds": DEFAULT_AGE_FLOOR_SECONDS,
    "lookback_hours": DEFAULT_LOOKBACK_HOURS,
    "max_per_pass": DEFAULT_MAX_PER_PASS,
    "spacing_seconds": DEFAULT_SPACING_SECONDS,
    "stuck_after_seconds": DEFAULT_STUCK_AFTER_SECONDS,
    "max_pages": DEFAULT_MAX_PAGES,
    "dispatcher_url": DEFAULT_DISPATCHER_URL,
    "front_door_url": "https://dispatch.example.com/status",
    "webhook_id": "",
    "run_timeout_seconds": DEFAULT_RUN_TIMEOUT_SECONDS,
}


class PollerError(Exception):
    """The pass could not complete (exit 1). Never confused with 'nothing to do'."""


class ConfigError(PollerError):
    """Bad arguments, config or credential (exit 2)."""


class RunTimeout(BaseException):
    """The wall clock ran out (exit 4). BaseException, so no bug-catcher swallows it."""


def log(msg):
    sys.stderr.write("[catchup-poller] %s\n" % msg)
    sys.stderr.flush()


def _iso(epoch=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if epoch is None else epoch))


def parse_iso(text):
    """Seconds since the epoch for an ISO-8601 instant, or None."""
    if not isinstance(text, str) or not text.strip():
        return None
    raw = text.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


# --------------------------------------------------------------------------- #
# Config + credential
# --------------------------------------------------------------------------- #
def _int(raw, key, default, low, high=None):
    value = raw.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < low or (
            high is not None and value > high):
        raise ConfigError("config %r must be an integer %s" % (
            key, ">= %d" % low if high is None else "from %d to %d" % (low, high)))
    return value


def load_config(path):
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ConfigError("could not read --config %s: %s" % (path, exc))
    if not isinstance(raw, dict):
        raise ConfigError("--config must hold one JSON object")
    unknown = sorted(set(raw) - set(CONFIG_KEYS))
    if unknown:
        raise ConfigError("unknown config key(s): %s (see --example-config)" % ", ".join(unknown))
    agent = raw.get("agent_user_id")
    if not isinstance(agent, str) or not agent.strip() or agent.startswith("<"):
        raise ConfigError("config needs 'agent_user_id': the dispatcher agent's Linear user id")
    teams = raw.get("teams")
    if not isinstance(teams, list) or not teams or not all(
            isinstance(t, str) and re.match(r"^[A-Z][A-Z0-9]*$", t) for t in teams):
        raise ConfigError("config 'teams' must be a non-empty list of team KEYS, e.g. [\"KIT\"]")
    key_env = raw.get("linear_key_env") or "STAGE_E_LINEAR_API_KEY"
    if not isinstance(key_env, str) or not re.match(r"^[A-Z][A-Z0-9_]*$", key_env):
        raise ConfigError("config 'linear_key_env' must be an ENV VAR NAME, never a value")
    floor = _int(raw, "age_floor_seconds", DEFAULT_AGE_FLOOR_SECONDS, 1)
    if floor < LADDER_SECONDS:
        raise ConfigError(
            "config 'age_floor_seconds' %d is inside Linear's retry ladder (%d s: 1 min, 1 h, "
            "6 h). A re-prompt there can race Linear's own retry, and Cyrus would run the "
            "ticket twice" % (floor, LADDER_SECONDS))
    door = raw.get("front_door_url") or ""
    if not isinstance(door, str) or (door and not door.startswith(("https://", "http://"))):
        raise ConfigError("config 'front_door_url' must be an http(s) URL, or empty")
    base = raw.get("dispatcher_url") or DEFAULT_DISPATCHER_URL
    if not isinstance(base, str) or not base.startswith(("https://", "http://")):
        raise ConfigError("config 'dispatcher_url' must be an http(s) URL")
    hook = raw.get("webhook_id") or ""
    if not isinstance(hook, str) or (hook and not re.match(r"^[A-Za-z0-9-]+$", hook)):
        raise ConfigError("config 'webhook_id' must be a Linear id, or empty")
    return {
        "agent_user_id": agent.strip(),
        "teams": list(teams),
        "linear_key_env": key_env,
        "state_dir": os.path.realpath(os.path.expanduser(raw.get("state_dir") or DEFAULT_STATE_DIR)),
        "age_floor_seconds": floor,
        "lookback_hours": _int(raw, "lookback_hours", DEFAULT_LOOKBACK_HOURS, 1),
        "max_per_pass": _int(raw, "max_per_pass", DEFAULT_MAX_PER_PASS, 1, 10),
        "spacing_seconds": _int(raw, "spacing_seconds", DEFAULT_SPACING_SECONDS, 0),
        "stuck_after_seconds": _int(raw, "stuck_after_seconds", DEFAULT_STUCK_AFTER_SECONDS, 60),
        "max_pages": _int(raw, "max_pages", DEFAULT_MAX_PAGES, 1, 100),
        "dispatcher_url": base.rstrip("/"),
        "front_door_url": door,
        "webhook_id": hook,
        "run_timeout_seconds": _int(raw, "run_timeout_seconds", DEFAULT_RUN_TIMEOUT_SECONDS, 30),
    }


def state_dir_hint(path):
    """Where a heartbeat goes for a run whose config failed. It cannot fail."""
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        if isinstance(raw, dict) and isinstance(raw.get("state_dir"), str):
            return os.path.realpath(os.path.expanduser(raw["state_dir"]))
    except Exception:        # noqa: BLE001 — any failure at all means "use the default"
        pass
    return os.path.realpath(os.path.expanduser(DEFAULT_STATE_DIR))


def credential(cfg, environ=None):
    environ = os.environ if environ is None else environ
    value = (environ.get(cfg["linear_key_env"]) or "").strip()
    if not value:
        raise ConfigError("$%s is unset: it lives in the role account's own env file "
                          "(<home>/.stage-e/env, mode 600)" % cfg["linear_key_env"])
    return value


# --------------------------------------------------------------------------- #
# State: the ledger and the heartbeat
# --------------------------------------------------------------------------- #
def _atomic_write_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.tmp-%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def ledger_path(state_dir):
    return os.path.join(state_dir, "ledger.json")


def heartbeat_path(state_dir):
    return os.path.join(state_dir, "heartbeat.json")


def load_ledger(path):
    """{session_id: {"state": "owed"|"posted"|"stuck", "issue", "at"}}. A corrupt ledger
    REFUSES the run: it is the record that stops a session being re-prompted twice."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise PollerError("ledger %s is unreadable (%s) — refusing to run so no session is "
                          "re-prompted twice; move or repair the file" % (path, exc))
    if not isinstance(doc, dict) or doc.get("schema") != LEDGER_SCHEMA or not isinstance(
            doc.get("sessions"), dict):
        raise PollerError("ledger %s is not a %s document — refusing to run so no session is "
                          "re-prompted twice" % (path, LEDGER_SCHEMA))
    return dict(doc["sessions"])


def save_ledger(path, sessions):
    _atomic_write_json(path, {"schema": LEDGER_SCHEMA, "sessions": sessions})


def write_heartbeat(state_dir, result, started_at, dry_run, findings=(), counts=None, error=None):
    """Every terminal path of a real run writes this; a --dry-run writes nothing, so an
    operator's rehearsal never freshens the liveness file the monitor reads."""
    if dry_run:
        return False
    findings = list(findings)
    doc = {"schema": HEARTBEAT_SCHEMA, "result": result, "started_at": started_at,
           "ended_at": _iso(), "findings": [f for f in findings if f["state"] != OK],
           "firing": sorted({f["kind"] for f in findings if f["state"] == FIRING}),
           "counts": counts or {}, "error": error}
    try:
        _atomic_write_json(heartbeat_path(state_dir), doc)
        return True
    except OSError as exc:
        log("NOTE: the heartbeat could not be written to %s (%s)" % (heartbeat_path(state_dir), exc))
        return False


# --------------------------------------------------------------------------- #
# The outside world: HTTP for the gate, GraphQL for Linear
# --------------------------------------------------------------------------- #
def http_get(url, timeout=8):
    """(status, body) for a GET, or (None, reason) when nothing answered."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (config URLs)
            return resp.status, resp.read(4096).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return None, str(getattr(exc, "reason", exc))


def linear_graphql(query, variables, api_key):
    req = urllib.request.Request(
        LINEAR_API, data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Content-Type": "application/json", "Authorization": api_key,
                 "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (fixed URL)
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        raise PollerError("Linear API HTTP %d" % exc.code)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise PollerError("could not reach the Linear API: %s" % getattr(exc, "reason", exc))
    if payload.get("errors"):
        log("Linear API error payload: %s" % json.dumps(payload["errors"])[:600])
        raise PollerError("Linear API error (payload logged)")
    return payload.get("data") or {}


# `secret` is never asked for: this job has no use for it, and the selftest pins its absence.
Q_WEBHOOKS = """
query CatchupWebhooks {
  webhooks(first: 50) { nodes { id enabled url label resourceTypes } }
}"""

Q_SESSIONS = """
query CatchupSessions($first: Int!, $after: String) {
  agentSessions(first: $first, after: $after, orderBy: createdAt) {
    nodes {
      id status createdAt startedAt endedAt dismissedAt
      appUser { id }
      creator { id }
      comment { id }
      issue {
        id identifier description archivedAt
        team { key }
        state { type }
        delegate { id }
        labels(first: 20) { nodes { name } }
      }
      activities(first: 20) { nodes { createdAt content { __typename } } }
    }
    pageInfo { hasNextPage endCursor }
  }
}"""

Q_THREAD = """
query CatchupThread($id: String!) {
  comment(id: $id) { id children(first: 50) { nodes { body } } }
}"""

M_REPLY = """
mutation CatchupReply($input: CommentCreateInput!) {
  commentCreate(input: $input) { success comment { id } }
}"""


# --------------------------------------------------------------------------- #
# Pure logic — every decision, no I/O
# --------------------------------------------------------------------------- #
def gate(cfg, get):
    """(open, reason). Re-prompt only when the dispatcher answers locally AND the public front
    door answers: the Mac makes dark wakes with network, and a re-prompt into a dead route
    spends the session's one attempt on nothing."""
    status, body = get(cfg["dispatcher_url"] + "/version")
    if status != 200 or "cyrus_cli_version" not in (body or ""):
        return False, "the dispatcher did not answer %s/version (%s)" % (
            cfg["dispatcher_url"], status if status is not None else body)
    if cfg["front_door_url"]:
        status, body = get(cfg["front_door_url"])
        if status != 200:
            return False, "the front door %s did not answer 200 (%s)" % (
                cfg["front_door_url"], status if status is not None else body)
    return True, "the dispatcher and the front door answer"


def webhook_finding(cfg, nodes, error=None):
    """One finding for the dispatcher's webhook: ok, firing (disabled) or unknown (could not
    see it). Unknown is never ok: it is named, so 'could not check' never reads as 'fine'."""
    fix = ("Re-enable it in Linear: Settings → API → OAuth applications → the dispatcher's app "
           "→ Webhooks. Delegations made while it is off reach nothing, retries included.")
    if error is not None:
        return {"kind": "webhook", "state": UNKNOWN,
                "summary": "could not read Linear's webhooks (%s)" % error}
    nodes = [n for n in nodes or [] if isinstance(n, dict)]
    if cfg["webhook_id"]:
        mine = [n for n in nodes if n.get("id") == cfg["webhook_id"]]
        if not mine:
            return {"kind": "webhook", "state": UNKNOWN,
                    "summary": "the configured webhook %s is not among the webhooks this key "
                               "can see" % cfg["webhook_id"]}
    else:
        # An OAuth application's webhook carries no URL of its own (schema: "Null for OAuth
        # application webhooks"); a workspace webhook does, and is not the dispatcher's.
        mine = [n for n in nodes if not n.get("url")]
        if not mine:
            return {"kind": "webhook", "state": UNKNOWN,
                    "summary": "this key sees no OAuth-application webhook, so the "
                               "dispatcher's cannot be checked; set webhook_id"}
    off = [n.get("id") for n in mine if n.get("enabled") is not True]
    if off:
        return {"kind": "webhook", "state": FIRING,
                "summary": "Linear has DISABLED the dispatcher's webhook (%s). %s"
                           % (", ".join(str(i) for i in off), fix)}
    return {"kind": "webhook", "state": OK,
            "summary": "%d webhook(s) enabled" % len(mine)}


def agent_activity(node):
    """True when any activity on the session is the agent's (anything but a person's prompt)."""
    acts = ((node.get("activities") or {}).get("nodes")) or []
    return any(((a or {}).get("content") or {}).get("__typename") in AGENT_ACTIVITY_TYPES
               for a in acts)


def classify(node, cfg, now, ledger):
    """(verdict, reason) for one agent session. Verdicts:
    skip      — not a lost delegation, or not ours to wake;
    young     — maybe lost, but still inside Linear's retry ladder;
    candidate — lost: re-prompt it;
    waiting   — re-prompted, not yet answered, not yet stuck;
    stuck     — re-prompted and still silent past stuck_after_seconds."""
    if ((node.get("appUser") or {}).get("id")) != cfg["agent_user_id"]:
        return "skip", "another agent's session"
    issue = node.get("issue") or {}
    ident = issue.get("identifier") or "?"
    entry = ledger.get(node.get("id") or "")
    if entry and entry.get("state") == "stuck":
        return "skip", "%s was already reported stuck" % ident
    if agent_activity(node) or node.get("startedAt") or node.get("endedAt") or node.get(
            "dismissedAt"):
        return "skip", "%s was answered" % ident
    if not issue.get("id") or issue.get("archivedAt"):
        return "skip", "%s has no live issue" % ident
    if ((issue.get("team") or {}).get("key")) not in cfg["teams"]:
        return "skip", "%s is not on a caught-up team" % ident
    if machine.issue_is_planning_ticket(issue):
        return "skip", "%s is a planning ticket (the idea gate times those out)" % ident
    if ((issue.get("state") or {}).get("type")) in ("completed", "canceled"):
        return "skip", "%s is %s" % (ident, (issue.get("state") or {}).get("type"))
    if ((issue.get("delegate") or {}).get("id")) != cfg["agent_user_id"]:
        return "skip", "%s is no longer delegated to the agent" % ident
    if not (node.get("creator") or {}).get("id"):
        return "skip", "%s has no creator (Cyrus would block any re-prompt)" % ident
    if not (node.get("comment") or {}).get("id"):
        return "skip", "%s has no thread to reply under" % ident
    if entry and entry.get("state") == "posted":
        at = parse_iso(entry.get("at")) or now
        if now - at >= cfg["stuck_after_seconds"]:
            return "stuck", "%s was re-prompted at %s and is still silent" % (ident, entry.get("at"))
        return "waiting", "%s was re-prompted at %s" % (ident, entry.get("at"))
    born = parse_iso(node.get("createdAt"))
    if born is None:
        return "skip", "%s has no readable createdAt" % ident
    age = now - born
    if age > cfg["lookback_hours"] * 3600:
        return "skip", "%s is older than the lookback" % ident
    if age < cfg["age_floor_seconds"]:
        return "young", "%s is %d min old; Linear's own retries may still deliver it" % (
            ident, int(age // 60))
    return "candidate", "%s was delegated %s and never answered" % (ident, node.get("createdAt"))


def reprompt_body(node):
    issue = node.get("issue") or {}
    ident = issue.get("identifier") or "this issue"
    return ("**Catch-up.** This delegation was made while the dispatcher could not be reached, "
            "and it never started. Please start the work %s was delegated for: read %s with "
            "your Linear tools and treat it as a new delegation.\n\n%s"
            % (ident, ident, MARK % node.get("id")))


def thread_has_mark(data, session_id):
    for child in (((data.get("comment") or {}).get("children") or {}).get("nodes")) or []:
        m = MARK_RE.search((child or {}).get("body") or "")
        if m and m.group(1) == session_id:
            return True
    return False


# --------------------------------------------------------------------------- #
# One pass
# --------------------------------------------------------------------------- #
def run_pass(cfg, api_key, dry_run=False, gql=None, get=None, sleep=None, now=None,
             deadline=None):
    """(exit_code, result, findings, counts). Writes the ledger as it goes; the caller writes
    the heartbeat."""
    gql = gql or (lambda q, v: linear_graphql(q, v, api_key))
    get = get or http_get
    sleep = sleep or time.sleep
    now = time.time() if now is None else now
    counts = {"sessions": 0, "candidates": 0, "reprompted": 0, "carried": 0, "young": 0,
              "waiting": 0, "stuck": 0, "healed": 0}
    findings = []

    def check_clock():
        if deadline is not None and time.monotonic() > deadline:
            raise RunTimeout()

    open_, why = gate(cfg, get)
    if not open_:
        log("HELD: %s. Nothing is re-prompted while the route is down." % why)
        return EXIT_OK, "held", [{"kind": "gate", "state": UNKNOWN, "summary": why}], counts
    path = ledger_path(cfg["state_dir"])
    ledger = load_ledger(path)

    try:
        hook = webhook_finding(cfg, (gql(Q_WEBHOOKS, {}).get("webhooks") or {}).get("nodes"))
    except PollerError as exc:
        hook = webhook_finding(cfg, None, error=str(exc))
    findings.append(hook)
    check_clock()

    # The tracker documents the sort FIELD (createdAt) but not its direction, so nothing here
    # assumes one. The read ends early only when what it has read so far is NEWEST FIRST and
    # a whole page is past the lookback: nothing after that page can be woken. Oldest first,
    # or a direction it cannot tell, it reads on. A read cut at max_pages before either end
    # is said as a check this pass could not make, never as a clean pass.
    cutoff = now - cfg["lookback_hours"] * 3600
    sessions, after, more = [], None, False
    for _ in range(cfg["max_pages"]):
        conn = gql(Q_SESSIONS, {"first": DEFAULT_PAGE_SIZE, "after": after}).get(
            "agentSessions") or {}
        page = conn.get("nodes") or []
        sessions.extend(page)
        info = conn.get("pageInfo") or {}
        more = bool(info.get("hasNextPage"))
        after = info.get("endCursor")
        check_clock()
        if not more or not after:
            more = False
            break
        first = parse_iso((sessions[0] or {}).get("createdAt"))
        last = parse_iso((sessions[-1] or {}).get("createdAt"))
        ages = [parse_iso((n or {}).get("createdAt")) for n in page]
        newest_first = first is not None and last is not None and first > last
        if newest_first and page and all(t is not None and t < cutoff for t in ages):
            more = False
            break
    if more:
        findings.append({"kind": "coverage", "state": UNKNOWN,
                         "summary": "the agent-session read stopped at max_pages (%d page(s) of "
                                    "%d) before its end or the lookback; a lost delegation "
                                    "could be beyond it. Raise max_pages" % (
                                        cfg["max_pages"], DEFAULT_PAGE_SIZE)})
        log("NOTE: " + findings[-1]["summary"])
    counts["sessions"] = len(sessions)

    candidates, stuck = [], []
    for node in sessions:
        verdict, reason = classify(node, cfg, now, ledger)
        if verdict in ("young", "waiting"):
            counts[verdict] += 1
        elif verdict == "stuck":
            stuck.append((node, reason))
        elif verdict == "candidate":
            candidates.append((node, reason))
    candidates.sort(key=lambda c: parse_iso(c[0].get("createdAt")) or 0)
    counts["candidates"] = len(candidates)

    for node, reason in stuck:
        counts["stuck"] += 1
        sid = node.get("id")
        findings.append({"kind": "stuck", "state": FIRING,
                         "summary": "%s. A person should look: Cyrus may have dropped it on "
                                    "purpose (no repository routed, a blocked user)." % reason})
        if not dry_run:
            ledger[sid] = dict(ledger.get(sid) or {}, state="stuck", reported_at=_iso(now))
            save_ledger(path, ledger)

    if hook["state"] == FIRING:
        log("HOLDING %d re-prompt(s): the webhook is disabled, so a reply would reach nothing."
            % len(candidates))
        counts["carried"] = len(candidates)
        candidates = []

    sent = 0
    for i, (node, reason) in enumerate(candidates):
        if sent >= cfg["max_per_pass"]:
            counts["carried"] = len(candidates) - i
            log("LEFT for the next pass (over max_per_pass %d): %s" % (
                cfg["max_per_pass"], ", ".join((c[0].get("issue") or {}).get("identifier") or "?"
                                               for c in candidates[i:])))
            break
        check_clock()
        sid = node.get("id")
        issue = node.get("issue") or {}
        root = (node.get("comment") or {}).get("id")
        if thread_has_mark(gql(Q_THREAD, {"id": root}), sid):
            counts["healed"] += 1
            log("%s already carries this job's re-prompt; recording it, not posting again"
                % issue.get("identifier"))
            if not dry_run:
                ledger[sid] = {"state": "posted", "issue": issue.get("identifier"), "at": _iso(now)}
                save_ledger(path, ledger)
            continue
        if dry_run:
            log("WOULD re-prompt %s: %s" % (issue.get("identifier"), reason))
            sent += 1
            continue
        if sent:
            sleep(cfg["spacing_seconds"])
        ledger[sid] = {"state": "owed", "issue": issue.get("identifier"), "at": _iso(now)}
        save_ledger(path, ledger)
        data = gql(M_REPLY, {"input": {"issueId": issue.get("id"), "parentId": root,
                                       "body": reprompt_body(node)}})
        if not (data.get("commentCreate") or {}).get("success"):
            raise PollerError("the re-prompt for %s did not report success; it stays owed and "
                              "the next pass reads its thread before trying again"
                              % issue.get("identifier"))
        ledger[sid] = {"state": "posted", "issue": issue.get("identifier"), "at": _iso(now)}
        save_ledger(path, ledger)
        sent += 1
        log("RE-PROMPTED %s: %s" % (issue.get("identifier"), reason))
    counts["reprompted"] = sent

    problem = any(f["state"] == FIRING for f in findings)
    return (EXIT_DECLINED if problem else EXIT_OK), ("problems" if problem else "ok"), findings, counts


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def cmd_run(args):
    started = _iso()
    state_dir = state_dir_hint(args.config)
    try:
        cfg = load_config(args.config)
        state_dir = cfg["state_dir"]
        key = credential(cfg)
    except ConfigError as exc:
        log("USAGE: %s" % exc)
        write_heartbeat(state_dir, "usage", started, args.dry_run, error=str(exc))
        return EXIT_USAGE
    timeout = args.timeout or cfg["run_timeout_seconds"]

    def _fired(_signum, _frame):
        raise RunTimeout()
    signal.signal(signal.SIGALRM, _fired)
    signal.alarm(timeout)
    try:
        code, result, findings, counts = run_pass(
            cfg, key, dry_run=args.dry_run, deadline=time.monotonic() + timeout)
    except RunTimeout:
        log("TIMEOUT: the pass outlived %d s" % timeout)
        write_heartbeat(state_dir, "timeout", started, args.dry_run, error="timeout")
        return EXIT_TIMEOUT
    except PollerError as exc:
        log("ERROR: %s" % exc)
        write_heartbeat(state_dir, "error", started, args.dry_run, error=str(exc))
        return EXIT_ERROR
    finally:
        signal.alarm(0)
    for f in findings:
        log("%-8s %-8s %s" % (f["state"].upper(), f["kind"], f["summary"]))
    log("pass %s%s: %s" % (result, " (dry run: nothing written)" if args.dry_run else "",
                           ", ".join("%s %d" % kv for kv in sorted(counts.items()))))
    write_heartbeat(state_dir, result, started, args.dry_run, findings, counts)
    return code


def cmd_check(args):
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        log("USAGE: %s" % exc)
        return EXIT_USAGE
    log("config OK: teams %s, floor %d s, at most %d re-prompt(s) per pass, state %s"
        % (",".join(cfg["teams"]), cfg["age_floor_seconds"], cfg["max_per_pass"], cfg["state_dir"]))
    return EXIT_OK


def main(argv=None):
    p = argparse.ArgumentParser(prog="pipeline_catchup_poller.py", description=__doc__.split("\n")[0])
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--example-config", action="store_true")
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("--config", required=True)
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--timeout", type=int, default=0)
    c = sub.add_parser("check")
    c.add_argument("--config", required=True)
    args = p.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.example_config:
        print(json.dumps(EXAMPLE_CONFIG, indent=2))
        return EXIT_OK
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "check":
        return cmd_check(args)
    p.print_help()
    return EXIT_USAGE


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
def selftest():
    import tempfile

    fails, ran = [], [0]

    def check(name, got, want):
        ran[0] += 1
        if got != want:
            fails.append("%s  (got %r, want %r)" % (name, got, want))

    NOW = parse_iso("2026-10-27T12:00:00Z")
    AGENT, OWNER = "agent-1", "owner-1"

    def session(sid, age_h, ident="KIT-1", acts=(), team="KIT", state="started", delegate=AGENT,
                creator=OWNER, comment="c-root", started=None, labels=(), desc="do the thing",
                app=AGENT):
        return {"id": sid, "status": "pending", "createdAt": _iso(NOW - age_h * 3600),
                "startedAt": started, "endedAt": None, "dismissedAt": None,
                "appUser": {"id": app}, "creator": {"id": creator} if creator else None,
                "comment": {"id": comment} if comment else None,
                "issue": {"id": "i-" + sid, "identifier": ident, "description": desc,
                          "archivedAt": None, "team": {"key": team}, "state": {"type": state},
                          "delegate": {"id": delegate} if delegate else None,
                          "labels": {"nodes": [{"name": n} for n in labels]}},
                "activities": {"nodes": [{"createdAt": _iso(NOW), "content": {"__typename": t}}
                                         for t in acts]}}

    class FakeLinear:
        def __init__(self, sessions, hooks=None, threads=None, fail_reply=False, hooks_error=False,
                     pages=None):
            self.sessions, self.hooks = sessions, hooks if hooks is not None else [
                {"id": "w-1", "enabled": True, "url": None}]
            self.threads, self.fail_reply, self.hooks_error = threads or {}, fail_reply, hooks_error
            self.pages, self.page_reads = pages, 0
            self.replies, self.queries = [], []

        def __call__(self, query, variables):
            self.queries.append(query)
            if "CatchupWebhooks" in query:
                if self.hooks_error:
                    raise PollerError("HTTP 403")
                return {"webhooks": {"nodes": self.hooks}}
            if "CatchupSessions" in query:
                if self.pages is not None:
                    i = self.page_reads
                    self.page_reads += 1
                    last = i + 1 >= len(self.pages)
                    return {"agentSessions": {"nodes": self.pages[i], "pageInfo": {
                        "hasNextPage": not last, "endCursor": None if last else "p%d" % i}}}
                return {"agentSessions": {"nodes": self.sessions,
                                          "pageInfo": {"hasNextPage": False, "endCursor": None}}}
            if "CatchupThread" in query:
                return {"comment": {"id": variables["id"], "children": {"nodes": [
                    {"body": b} for b in self.threads.get(variables["id"], [])]}}}
            if "CatchupReply" in query:
                self.replies.append(variables["input"])
                return {"commentCreate": {"success": not self.fail_reply, "comment": {"id": "r"}}}
            raise AssertionError("unexpected query")

    up = lambda url: (200, '{"cyrus_cli_version":"0.2.73"}')   # noqa: E731

    with tempfile.TemporaryDirectory() as tmp:
        def cfg(**over):
            raw = dict(EXAMPLE_CONFIG, agent_user_id=AGENT, state_dir=os.path.join(tmp, over.pop(
                "_dir", "s")), front_door_url="https://door.example.com/status", spacing_seconds=0)
            raw.update(over)
            p = os.path.join(tmp, "c.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(raw, fh)
            return load_config(p)

        c = cfg()
        # -- classification: every rule, one at a time ---------------------------------
        led = {}
        check("a lost delegation past the floor is a candidate",
              classify(session("s1", 8), c, NOW, led)[0], "candidate")
        check("inside Linear's retry ladder it is left to Linear's retries",
              classify(session("s1", 3), c, NOW, led)[0], "young")
        check("a session with ANY agent activity was answered",
              classify(session("s1", 8, acts=("AgentActivityThoughtContent",)), c, NOW, led)[0], "skip")
        check("…but a session holding only a person's prompt was not",
              classify(session("s1", 8, acts=("AgentActivityPromptContent",)), c, NOW, led)[0],
              "candidate")
        check("a started session is not lost", classify(session("s1", 8, started=_iso(NOW)), c,
                                                        NOW, led)[0], "skip")
        check("another agent's session is never touched",
              classify(session("s1", 8, app="other"), c, NOW, led)[0], "skip")
        check("a team that is not caught up is skipped (the Reviews team)",
              classify(session("s1", 8, team="REV"), c, NOW, led)[0], "skip")
        check("a planning ticket is skipped (its own poller times it out)",
              classify(session("s1", 8, labels=("stage-a-planning-myapp",)), c, NOW, led)[0], "skip")
        check("a completed issue is skipped", classify(session("s1", 8, state="completed"), c,
                                                       NOW, led)[0], "skip")
        check("a canceled issue is skipped", classify(session("s1", 8, state="canceled"), c,
                                                      NOW, led)[0], "skip")
        check("an issue no longer delegated to the agent is skipped",
              classify(session("s1", 8, delegate="someone"), c, NOW, led)[0], "skip")
        check("a creator-less session is skipped", classify(session("s1", 8, creator=None), c,
                                                            NOW, led)[0], "skip")
        check("a session with no thread to reply under is skipped",
              classify(session("s1", 8, comment=None), c, NOW, led)[0], "skip")
        check("older than the lookback is never woken", classify(session("s1", 24 * 8), c, NOW,
                                                                 led)[0], "skip")
        posted = {"s1": {"state": "posted", "at": _iso(NOW - 600)}}
        check("re-prompted recently and still silent: waiting",
              classify(session("s1", 9), c, NOW, posted)[0], "waiting")
        posted_old = {"s1": {"state": "posted", "at": _iso(NOW - 7200)}}
        check("re-prompted and still silent past stuck_after: stuck",
              classify(session("s1", 9), c, NOW, posted_old)[0], "stuck")
        check("…and once reported stuck, never again",
              classify(session("s1", 9), c, NOW, {"s1": {"state": "stuck"}})[0], "skip")

        # -- the age floor cannot be set inside Linear's retry ladder ------------------
        try:
            cfg(age_floor_seconds=LADDER_SECONDS - 1)
            refused = ""
        except ConfigError as exc:
            refused = str(exc)
        check("a floor inside Linear's retry ladder is refused", "twice" in refused, True)
        check("…and the default floor is the ladder plus a margin",
              (DEFAULT_AGE_FLOOR_SECONDS > LADDER_SECONDS, LADDER_SECONDS), (True, 25260))

        # -- the gate --------------------------------------------------------------------
        fake = FakeLinear([session("s1", 8)])
        code, result, _f, counts = run_pass(cfg(_dir="g"), "k", gql=fake,
                                            get=lambda url: (None, "no route"), now=NOW)
        check("with the dispatcher down nothing is re-prompted, and the pass holds",
              (code, result, fake.replies), (EXIT_OK, "held", []))
        door_down = lambda url: (200, '{"cyrus_cli_version":"x"}') if url.endswith("/version") \
            else (530, "")  # noqa: E731
        fake = FakeLinear([session("s1", 8)])
        check("…and with only the front door down it holds too",
              run_pass(cfg(_dir="g2"), "k", gql=fake, get=door_down, now=NOW)[1], "held")
        local_down = lambda url: (None, "refused") if url.endswith("/version") \
            else (200, "ok")  # noqa: E731
        fake = FakeLinear([session("s1", 8)])
        check("…and with only the dispatcher down, the front door up, it holds too",
              (run_pass(cfg(_dir="g3"), "k", gql=fake, get=local_down, now=NOW)[1], fake.replies),
              ("held", []))
        not_it = lambda url: (200, "<html>some other server</html>")  # noqa: E731
        fake = FakeLinear([session("s1", 8)])
        check("…and something else answering on the dispatcher's port is not the dispatcher",
              (run_pass(cfg(_dir="g4"), "k", gql=fake, get=not_it, now=NOW)[1], fake.replies),
              ("held", []))

        # -- a pass: re-prompts the lost one, once, under its own thread ---------------
        cr = cfg(_dir="r")
        fake = FakeLinear([session("s1", 8, ident="KIT-1"), session("s2", 2, ident="KIT-2")])
        code, result, findings, counts = run_pass(cr, "k", gql=fake, get=up, now=NOW)
        reply = fake.replies[0] if fake.replies else {}
        check("one lost delegation is re-prompted, the young one left",
              (code, result, len(fake.replies), counts["young"]), (EXIT_OK, "ok", 1, 1))
        check("…as a reply under the session's own root comment, carrying the marker",
              (reply.get("issueId"), reply.get("parentId"), MARK % "s1" in reply.get("body", "")),
              ("i-s1", "c-root", True))
        check("…and the ledger records it posted",
              load_ledger(ledger_path(cr["state_dir"])).get("s1", {}).get("state"), "posted")
        fake = FakeLinear([session("s1", 8, ident="KIT-1")])
        run_pass(cr, "k", gql=fake, get=up, now=NOW + 60)
        check("a second pass never re-prompts the same session", fake.replies, [])

        # -- the thread is the backstop when the ledger was lost ------------------------
        cl = cfg(_dir="lost")
        fake = FakeLinear([session("s1", 8)], threads={"c-root": ["hi", MARK % "s1"]})
        code, _r, _f, counts = run_pass(cl, "k", gql=fake, get=up, now=NOW)
        check("a thread already carrying the marker is recorded, not posted again",
              (fake.replies, counts["healed"],
               load_ledger(ledger_path(cl["state_dir"])).get("s1", {}).get("state")),
              ([], 1, "posted"))

        # -- the cap: over it is LEFT, oldest first --------------------------------------
        cc = cfg(_dir="cap", max_per_pass=2)
        many = [session("s%d" % i, 8 + i, ident="KIT-%d" % i, comment="c%d" % i) for i in range(5)]
        fake = FakeLinear(many)
        _c, _r, _f, counts = run_pass(cc, "k", gql=fake, get=up, now=NOW)
        check("at most max_per_pass re-prompts, the rest carried, oldest first",
              ([r["issueId"] for r in fake.replies], counts["carried"]), (["i-s4", "i-s3"], 3))
        slept = []
        cs = cfg(_dir="spacing", spacing_seconds=60)
        run_pass(cs, "k", gql=FakeLinear(many), get=up, now=NOW, sleep=slept.append)
        check("…spaced spacing_seconds apart", slept, [60, 60])

        # -- paging: no reliance on the tracker's sort direction -------------------------
        old = lambda sid, d: session(sid, 24 * d, ident="KIT-" + sid, comment="c-" + sid)  # noqa: E731
        fake = FakeLinear(None, pages=[[old("n1", 0.4), old("n2", 2)], [old("n3", 8), old("n4", 9)],
                                       [old("n5", 10)]])
        _c, _r, findingsP, _n = run_pass(cfg(_dir="pg-desc"), "k", gql=fake, get=up, now=NOW)
        check("newest first: a page wholly past the lookback ends the read, and says nothing",
              (fake.page_reads, [r["issueId"] for r in fake.replies],
               [f["kind"] for f in findingsP if f["kind"] == "coverage"]), (2, ["i-n2", "i-n1"], []))
        fake = FakeLinear(None, pages=[[old("o1", 10), old("o2", 9)], [old("o3", 0.4)]])
        run_pass(cfg(_dir="pg-asc"), "k", gql=fake, get=up, now=NOW)
        check("oldest first: an old first page does NOT end the read, the new one is reached",
              (fake.page_reads, [r["issueId"] for r in fake.replies]), (2, ["i-o3"]))
        fake = FakeLinear(None, pages=[[old("m1", 0.4)], [old("m2", 1)], [old("m3", 2)]])
        codeP, resultP, findingsP, _n = run_pass(cfg(_dir="pg-cut", max_pages=2), "k", gql=fake,
                                                 get=up, now=NOW)
        check("a read cut at max_pages inside the lookback is a check it could not make",
              (fake.page_reads, codeP, [(f["kind"], f["state"]) for f in findingsP
                                        if f["kind"] == "coverage"]),
              (2, EXIT_OK, [("coverage", UNKNOWN)]))

        # -- the webhook -----------------------------------------------------------------
        fake = FakeLinear([session("s1", 8)], hooks=[{"id": "w-1", "enabled": False, "url": None}])
        code, result, findings, counts = run_pass(cfg(_dir="wh"), "k", gql=fake, get=up, now=NOW)
        check("a disabled webhook is a problem, and holds every re-prompt",
              (code, result, fake.replies, counts["carried"], [f["kind"] for f in findings
                                                                if f["state"] == FIRING]),
              (EXIT_DECLINED, "problems", [], 1, ["webhook"]))
        check("…and names the manual re-enable step",
              "Settings → API" in [f for f in findings if f["kind"] == "webhook"][0]["summary"], True)
        fake = FakeLinear([], hooks=[{"id": "w-9", "enabled": True, "url": "https://x"}])
        _c, result, findings, _n = run_pass(cfg(_dir="wh2"), "k", gql=fake, get=up, now=NOW)
        check("seeing no OAuth-application webhook is UNKNOWN, never ok, and does not page",
              (result, [f["state"] for f in findings if f["kind"] == "webhook"]), ("ok", [UNKNOWN]))
        fake = FakeLinear([], hooks_error=True)
        _c, result, findings, _n = run_pass(cfg(_dir="wh3"), "k", gql=fake, get=up, now=NOW)
        check("a webhook read that fails is UNKNOWN, not a failed pass",
              (result, [f["state"] for f in findings if f["kind"] == "webhook"]), ("ok", [UNKNOWN]))
        fake = FakeLinear([], hooks=[{"id": "w-1", "enabled": True, "url": None},
                                     {"id": "w-2", "enabled": False, "url": None}])
        check("a pinned webhook_id is judged alone",
              run_pass(cfg(_dir="wh4", webhook_id="w-1"), "k", gql=fake, get=up, now=NOW)[1], "ok")

        # -- stuck pages once ------------------------------------------------------------
        cst = cfg(_dir="stuck")
        save_ledger(ledger_path(cst["state_dir"]), {"s1": {"state": "posted", "at": _iso(NOW - 7200)}})
        fake = FakeLinear([session("s1", 10)])
        code, result, findings, _n = run_pass(cst, "k", gql=fake, get=up, now=NOW)
        check("a re-prompted session still silent is a problem, reported once",
              (code, result, fake.replies, [f["kind"] for f in findings if f["state"] == FIRING]),
              (EXIT_DECLINED, "problems", [], ["stuck"]))
        _c, result, _f, _n = run_pass(cst, "k", gql=FakeLinear([session("s1", 10)]), get=up,
                                      now=NOW + 60)
        check("…and the next pass does not report it again", result, "ok")

        # -- a failed post stays owed; the next pass reads the thread first --------------
        cf = cfg(_dir="fail")
        try:
            run_pass(cf, "k", gql=FakeLinear([session("s1", 8)], fail_reply=True), get=up, now=NOW)
            failed = False
        except PollerError:
            failed = True
        check("a re-prompt that did not report success fails the pass and stays owed",
              (failed, load_ledger(ledger_path(cf["state_dir"])).get("s1", {}).get("state")),
              (True, "owed"))
        fake = FakeLinear([session("s1", 8)])
        run_pass(cf, "k", gql=fake, get=up, now=NOW + 60)
        check("…and the next pass posts it, after reading the thread",
              (len(fake.replies), any("CatchupThread" in q for q in fake.queries)), (1, True))

        # -- a dry run writes nothing ----------------------------------------------------
        cd = cfg(_dir="dry")
        fake = FakeLinear([session("s1", 8)])
        run_pass(cd, "k", dry_run=True, gql=fake, get=up, now=NOW)
        check("a dry run posts nothing and writes no ledger",
              (fake.replies, os.path.exists(ledger_path(cd["state_dir"]))), ([], False))
        check("…and writes no heartbeat",
              (write_heartbeat(cd["state_dir"], "ok", _iso(), True),
               os.path.exists(heartbeat_path(cd["state_dir"]))), (False, False))

        # -- a corrupt ledger refuses the run --------------------------------------------
        cb = cfg(_dir="bad")
        os.makedirs(cb["state_dir"])
        with open(ledger_path(cb["state_dir"]), "w") as fh:
            fh.write("{not json")
        try:
            run_pass(cb, "k", gql=FakeLinear([session("s1", 8)]), get=up, now=NOW)
            refused = False
        except PollerError:
            refused = True
        check("a corrupt ledger refuses the run rather than risk a second re-prompt", refused, True)

        # -- every terminal path of a real run writes the heartbeat ----------------------
        bad_cfg = os.path.join(tmp, "broken.json")
        with open(bad_cfg, "w") as fh:
            json.dump({"state_dir": os.path.join(tmp, "hb"), "teams": []}, fh)
        code = main(["run", "--config", bad_cfg])
        beat = json.load(open(heartbeat_path(os.path.join(tmp, "hb"))))
        check("a config failure still writes a heartbeat saying usage",
              (code, beat["schema"], beat["result"]), (EXIT_USAGE, HEARTBEAT_SCHEMA, "usage"))
        write_heartbeat(os.path.join(tmp, "hb2"), "problems", _iso(), False,
                        [{"kind": "webhook", "state": FIRING, "summary": "x"},
                         {"kind": "gate", "state": OK, "summary": "y"}])
        beat = json.load(open(heartbeat_path(os.path.join(tmp, "hb2"))))
        check("the heartbeat lists firing kinds and the non-ok findings, for the monitor",
              (beat["firing"], [f["kind"] for f in beat["findings"]]), (["webhook"], ["webhook"]))

        # -- config ----------------------------------------------------------------------
        for bad, why in (({"teams": ["kit"]}, "team KEYS"), ({"agent_user_id": ""}, "agent_user_id"),
                         ({"linear_key_env": "lin_api_abc"}, "ENV VAR NAME"),
                         ({"max_per_pass": 50}, "max_per_pass"), ({"surprise": 1}, "unknown"),
                         ({"front_door_url": "door"}, "front_door_url")):
            try:
                cfg(_dir="cfg", **bad)
                said = ""
            except ConfigError as exc:
                said = str(exc)
            check("config refuses %s" % sorted(bad), why in said, True)

    # -- what it never does: asserted against this file's own source ----------------------
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    body = src.split("def selftest():")[0]
    mutations = set(re.findall(r"\b(issue[A-Z]\w*|comment[A-Z]\w*|agentSession[A-Z]\w*|"
                               r"webhook[A-Z]\w*|attachment[A-Z]\w*)\s*\(", body))
    check("its only Linear write is commentCreate", mutations, {"commentCreate"})
    check("it never asks for a webhook's secret", " secret" in Q_WEBHOOKS, False)
    check("it reads exactly one environment variable: the tracker key's",
          len(re.findall(r"\bos\.environ\b", body)), 1)
    check("it never touches a pull request or a chat token",
          bool(re.search(r"/pulls\b|\bgh\s|\bslack\b|xoxb", body, re.IGNORECASE)), False)
    check("its marker is not one the notifier pings on", "pipeline-escalation" in MARK, False)

    for f in fails:
        print("FAIL:", f)
    print("%d/%d checks passed" % (ran[0] - len(fails), ran[0]))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
