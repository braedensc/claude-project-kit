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
    1. checks the dispatcher answers locally and the public front door answers, and posts
       nothing while either is down (a re-prompt then would travel a dead route). It still
       looks, so what it reports does not vanish while the route is down, and a route that
       stays down for HELD_ALERT_PASSES passes in a row pages: a wrong URL looks the same;
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

  Only the agent's NEWEST session on an issue is judged: a person who restarted a lost
  delegation by hand made a newer one, and replying under the old one would run it twice.

  Zero activity can be BY DESIGN — Cyrus stays silent when routing finds no repository, when
  a user is blocked with `silent`, when the issue vanished. So a session is re-prompted once,
  EVER. If it is still silent `stuck_after_seconds` after that, it is stuck: reported on every
  pass under its own kind (`stuck-<ticket>`), so the heartbeat monitor pages it once, until a
  person answers it, ends it, removes the delegation or closes the ticket. Never re-prompted.

DEDUP: THE LEDGER IS THE RECORD, AND THE THREAD IS THE BACKSTOP

  `<state_dir>/ledger.json` records each session as `owed` before the post and `posted` after
  it. Before any post the thread is read for this job's receipt line, written by this key's own
  user, so a post that landed but lost its answer — or a ledger that was lost — is never made
  twice. A corrupt ledger refuses the run, and one pass runs at a time. A session whose reply
  fails stays owed, fails the pass loudly, and goes to the back of the line: it never blocks
  the others. At most `max_per_pass` re-prompts per pass, `spacing_seconds` apart: each one starts a
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
      disabled webhook, a session still silent after its re-prompt, a session read cut short,
      a route down for many passes); 1 = the pass could not complete, or a reply failed;
      2 = usage (bad arguments, config or credential); 4 = the wall clock ran out.
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
# The receipt is PLAIN TEXT, not an HTML comment: the tracker may normalise markdown on save,
# and a receipt it strips is one that is never found. It counts only in a reply written by this
# key's own user, so another member cannot suppress a re-prompt by pasting the line.
MARK = "Catch-up receipt: %s"
MARK_RE = re.compile(r"(?m)^Catch-up receipt: ([A-Za-z0-9_-]+)\s*$")
HELD_ALERT_PASSES = 8                        # two awake hours on the default interval

CONFIG_KEYS = {
    "agent_user_id": "the dispatcher agent's Linear user id: only its sessions are read",
    "teams": "team KEYS whose delegations are caught up, e.g. [\"ENG\"]. Leave the "
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
    "teams": ["ENG"],
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
    per_pass = _int(raw, "max_per_pass", DEFAULT_MAX_PER_PASS, 1, 10)
    spacing = _int(raw, "spacing_seconds", DEFAULT_SPACING_SECONDS, 0)
    clock = _int(raw, "run_timeout_seconds", DEFAULT_RUN_TIMEOUT_SECONDS, 30)
    if (per_pass - 1) * spacing > clock // 2:
        raise ConfigError("config: %d re-prompts %d s apart sleep %d s, more than half the %d s "
                          "pass clock. A pass cut short between a reply and its ledger entry "
                          "leaves the receipt as the only record" % (
                              per_pass, spacing, (per_pass - 1) * spacing, clock))
    return {
        "agent_user_id": agent.strip(),
        "teams": list(teams),
        "linear_key_env": key_env,
        "state_dir": os.path.realpath(os.path.expanduser(raw.get("state_dir") or DEFAULT_STATE_DIR)),
        "age_floor_seconds": floor,
        "lookback_hours": _int(raw, "lookback_hours", DEFAULT_LOOKBACK_HOURS, 1),
        "max_per_pass": per_pass,
        "spacing_seconds": spacing,
        "stuck_after_seconds": _int(raw, "stuck_after_seconds", DEFAULT_STUCK_AFTER_SECONDS, 60),
        "max_pages": _int(raw, "max_pages", DEFAULT_MAX_PAGES, 1, 100),
        "dispatcher_url": base.rstrip("/"),
        "front_door_url": door,
        "webhook_id": hook,
        "run_timeout_seconds": clock,
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
    bad = sorted(str(k) for k, v in doc["sessions"].items()
                 if not isinstance(v, dict) or v.get("state") not in ("owed", "posted", "stuck"))
    if bad:
        raise PollerError("ledger %s has %d entr(y/ies) that are not owed, posted or stuck (%s) — "
                          "refusing to run so no session is re-prompted twice"
                          % (path, len(bad), ", ".join(bad[:3])))
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
  viewer { id }
  comment(id: $id) { id children(first: 100) { nodes { body user { id } } } }
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
        # More than one is ambiguous: another app's disabled webhook must never read as the
        # dispatcher's and hold every re-prompt. Then it is a check it could not make.
        mine = [n for n in nodes if not n.get("url")]
        if not mine:
            return {"kind": "webhook", "state": UNKNOWN,
                    "summary": "this key sees no OAuth-application webhook, so the "
                               "dispatcher's cannot be checked; set webhook_id"}
        if len(mine) > 1:
            return {"kind": "webhook", "state": UNKNOWN,
                    "summary": "this key sees %d OAuth-application webhooks (%s) and cannot "
                               "tell which is the dispatcher's; set webhook_id to it"
                               % (len(mine), ", ".join(str(n.get("id")) for n in mine[:5]))}
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


def classify(node, cfg, now, ledger, gate_open=True):
    """(verdict, reason) for one agent session. Verdicts:
    skip      — not a lost delegation, or not ours to wake;
    young     — maybe lost, but still inside Linear's retry ladder;
    candidate — lost: re-prompt it;
    waiting   — re-prompted, not yet answered, not yet stuck;
    stuck     — re-prompted and still silent past stuck_after_seconds. It STAYS stuck, and is
                reported on every pass, until a person acts: answers it, ends it, removes the
                delegation or closes the ticket. Any of those is a skip below, checked first.
    With the gate closed a re-prompted session is not newly judged stuck: its silence is the
    dispatcher's outage, which the gate already reports."""
    if ((node.get("appUser") or {}).get("id")) != cfg["agent_user_id"]:
        return "skip", "another agent's session"
    issue = node.get("issue") or {}
    ident = issue.get("identifier") or "?"
    entry = ledger.get(node.get("id") or "")
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
    if entry and entry.get("state") == "stuck":
        return "stuck", "%s was re-prompted at %s and is still silent" % (ident, entry.get("at"))
    if entry and entry.get("state") == "posted":
        at = parse_iso(entry.get("at")) or now
        if gate_open and now - at >= cfg["stuck_after_seconds"]:
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
    """Does a reply written by THIS key's own user carry this session's receipt?"""
    me = (data.get("viewer") or {}).get("id")
    for child in (((data.get("comment") or {}).get("children") or {}).get("nodes")) or []:
        child = child or {}
        if not me or ((child.get("user") or {}).get("id")) != me:
            continue
        if any(m == session_id for m in MARK_RE.findall(child.get("body") or "")):
            return True
    return False


def stuck_kind(node):
    """One firing kind per stuck ticket, so a second stuck ticket pages and the same one an
    hour later does not. The monitor accepts `[a-z][a-z0-9-]{0,31}`."""
    ident = re.sub(r"[^a-z0-9-]", "-", ((node.get("issue") or {}).get("identifier") or "").lower())
    return ("stuck-" + ident)[:32].rstrip("-") or "stuck"


def newest_by_issue(sessions, agent):
    """{issue id: the newest createdAt among the agent's sessions on it}."""
    out = {}
    for n in sessions:
        n = n or {}
        iid = (n.get("issue") or {}).get("id")
        born = parse_iso(n.get("createdAt"))
        if iid and born is not None and ((n.get("appUser") or {}).get("id")) == agent:
            out[iid] = max(out.get(iid, born), born)
    return out


# --------------------------------------------------------------------------- #
# One pass
# --------------------------------------------------------------------------- #
def gate_path(state_dir):
    return os.path.join(state_dir, "gate.json")


def load_held(state_dir):
    """How many passes in a row have held. Not a safety record — a damaged file only delays
    the alert — so it reads as 0 and says so."""
    try:
        with open(gate_path(state_dir), encoding="utf-8") as fh:
            value = json.load(fh).get("held_passes", 0)
        return value if isinstance(value, int) and value >= 0 else 0
    except FileNotFoundError:
        return 0
    except (OSError, ValueError, AttributeError) as exc:
        log("NOTE: %s is unreadable (%s); counting held passes from 0" % (gate_path(state_dir), exc))
        return 0


def last_firing(state_dir):
    """The FIRING findings of the last heartbeat, for a held pass that could not read Linear:
    carried, so a pass that could not look never reads as a recovery."""
    try:
        with open(heartbeat_path(state_dir), encoding="utf-8") as fh:
            doc = json.load(fh)
        return [dict(f, summary="(from the last pass that could look) %s" % f.get("summary"))
                for f in doc.get("findings") or []
                if isinstance(f, dict) and f.get("state") == FIRING and f.get("kind") != "gate"]
    except Exception:  # noqa: BLE001 — nothing to carry is the honest fallback
        return []


def run_pass(cfg, api_key, dry_run=False, gql=None, get=None, sleep=None, now=None,
             deadline=None):
    """(exit_code, result, findings, counts). Writes the ledger as it goes; the caller writes
    the heartbeat. A PollerError it raises carries `.findings` and `.counts`, so a pass that
    failed part-way still reports what it found."""
    gql = gql or (lambda q, v: linear_graphql(q, v, api_key))
    get = get or http_get
    sleep = sleep or time.sleep
    now = time.time() if now is None else now
    counts = {"sessions": 0, "candidates": 0, "reprompted": 0, "carried": 0, "young": 0,
              "waiting": 0, "stuck": 0, "healed": 0, "superseded": 0, "failed": 0}
    findings = []

    def check_clock():
        if deadline is not None and time.monotonic() > deadline:
            raise RunTimeout()

    def failed(message):
        exc = PollerError(message)
        exc.findings, exc.counts = findings, counts
        return exc

    path = ledger_path(cfg["state_dir"])
    ledger = load_ledger(path)

    # THE GATE GATES POSTING, NOT LOOKING. A held pass still reads the webhook and the
    # sessions, so a disabled webhook or a stuck session stays reported while the dispatcher
    # is down — a pass that stopped looking would read as a recovery.
    open_, why = gate(cfg, get)
    held = 0 if open_ else load_held(cfg["state_dir"]) + 1
    if not dry_run and (held or load_held(cfg["state_dir"])):
        try:
            _atomic_write_json(gate_path(cfg["state_dir"]), {"held_passes": held, "at": _iso(now)})
        except OSError as exc:
            log("NOTE: could not record the held count (%s)" % exc)
    if not open_:
        log("HELD: %s. Nothing is re-prompted while the route is down." % why)
        loud = held >= HELD_ALERT_PASSES
        findings.append({"kind": "gate", "state": FIRING if loud else UNKNOWN, "summary": (
            "%s, %d passes in a row. If the dispatcher is up, CATCHUP_DISPATCHER_URL or "
            "CATCHUP_FRONT_DOOR_URL is wrong, and nothing lost will ever be started" % (why, held)
            if loud else why)})

    try:
        hook = webhook_finding(cfg, (gql(Q_WEBHOOKS, {}).get("webhooks") or {}).get("nodes"))
    except PollerError as exc:
        hook = webhook_finding(cfg, None, error=str(exc))
    findings.append(hook)
    check_clock()

    # The tracker documents the sort FIELD (createdAt) but not its direction, so nothing here
    # assumes one. The read ends early only when what it has read so far is NEWEST FIRST and
    # a whole page is past the lookback: nothing after that page can be woken. Oldest first,
    # or a direction it cannot tell, it reads on. A read cut at max_pages before either end is
    # a blind spot that lasts until a person raises max_pages, so it is FIRING, not a note.
    cutoff = now - cfg["lookback_hours"] * 3600
    sessions, after, more = [], None, False
    try:
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
    except PollerError as exc:
        if open_:
            raise failed("could not read the agent sessions: %s" % exc)
        # Held AND unable to read Linear: the machine is half awake. Say so, and carry what
        # the last pass that could look found, so this beat is not read as a recovery.
        findings.append({"kind": "sessions", "state": UNKNOWN,
                         "summary": "could not read the agent sessions while held: %s" % exc})
        findings.extend(last_firing(cfg["state_dir"]))
        problem = any(f["state"] == FIRING for f in findings)
        return (EXIT_DECLINED if problem else EXIT_OK), ("problems" if problem else "held"), \
            findings, counts
    if more:
        findings.append({"kind": "coverage", "state": FIRING,
                         "summary": "the agent-session read stopped at max_pages (%d page(s) of "
                                    "%d) before its end or the lookback; a lost delegation "
                                    "could be beyond it. Raise max_pages" % (
                                        cfg["max_pages"], DEFAULT_PAGE_SIZE)})
        log("NOTE: " + findings[-1]["summary"])
    counts["sessions"] = len(sessions)

    # ONE SESSION PER ISSUE. A person who restarted a lost delegation by hand made a newer
    # session on the same issue; replying under the old one would run the ticket twice. Only
    # the agent's newest session on an issue is ever judged.
    newest = newest_by_issue(sessions, cfg["agent_user_id"])
    candidates, stuck = [], []
    for node in sessions:
        node = node or {}
        verdict, reason = classify(node, cfg, now, ledger, gate_open=open_)
        born = parse_iso(node.get("createdAt"))
        iid = (node.get("issue") or {}).get("id")
        if verdict != "skip" and iid in newest and born is not None and born < newest[iid]:
            counts["superseded"] += 1
            continue
        if verdict in ("young", "waiting"):
            counts[verdict] += 1
        elif verdict == "stuck":
            stuck.append((node, reason))
        elif verdict == "candidate":
            candidates.append((node, reason))
    # A session whose re-prompt failed before goes to the back, so it never blocks newer ones.
    candidates.sort(key=lambda c: ((ledger.get(c[0].get("id")) or {}).get("failures", 0),
                                   parse_iso(c[0].get("createdAt")) or 0))
    counts["candidates"] = len(candidates)
    # A DRY RUN NAMES EVERY CANDIDATE, before the hold and the cap trim what THIS pass would
    # post: the installer's first-count sign-off (CK-11) is a cost gate, and every candidate is
    # a paid session sooner or later. A count of only this pass's three would undercount it.
    if dry_run:
        for node, reason in candidates:
            log("CANDIDATE %s: %s" % ((node.get("issue") or {}).get("identifier") or "?", reason))

    for node, reason in stuck:
        counts["stuck"] += 1
        sid = node.get("id")
        findings.append({"kind": stuck_kind(node), "state": FIRING,
                         "summary": "%s. A person should look: the dispatcher may have dropped it "
                                    "on purpose (no repository routed, a blocked user). Reported "
                                    "until someone answers it, ends it, removes the delegation "
                                    "or closes the ticket" % reason})
        if not dry_run and (ledger.get(sid) or {}).get("state") != "stuck":
            ledger[sid] = dict(ledger.get(sid) or {}, state="stuck", reported_at=_iso(now))
            save_ledger(path, ledger)

    if not open_ or hook["state"] == FIRING:
        if candidates:
            log("HOLDING %d re-prompt(s): %s" % (len(candidates), (
                "the webhook is disabled, so a reply would reach nothing" if open_
                else "the route to the dispatcher is down")))
        counts["carried"] = len(candidates)
        candidates = []

    sent, posts, errors = 0, 0, []
    for i, (node, reason) in enumerate(candidates):
        if posts >= cfg["max_per_pass"]:
            counts["carried"] = len(candidates) - i
            log("LEFT for the next pass (over max_per_pass %d): %s" % (
                cfg["max_per_pass"], ", ".join((c[0].get("issue") or {}).get("identifier") or "?"
                                               for c in candidates[i:])))
            break
        check_clock()
        sid = node.get("id")
        issue = node.get("issue") or {}
        ident = issue.get("identifier") or "?"
        root = (node.get("comment") or {}).get("id")

        def note_failure(why_):
            errors.append("%s: %s" % (ident, why_))
            counts["failed"] += 1
            if not dry_run:
                prior = ledger.get(sid) or {}
                ledger[sid] = {"state": "owed", "issue": ident, "at": prior.get("at") or _iso(now),
                               "failures": int(prior.get("failures") or 0) + 1}
                save_ledger(path, ledger)

        try:
            thread = gql(Q_THREAD, {"id": root})
        except PollerError as exc:
            note_failure("could not read its thread (%s)" % exc)
            continue
        if thread_has_mark(thread, sid):
            counts["healed"] += 1
            log("%s already carries this job's re-prompt; recording it, not posting again" % ident)
            if not dry_run:
                ledger[sid] = {"state": "posted", "issue": ident, "at": _iso(now)}
                save_ledger(path, ledger)
            continue
        if dry_run:
            log("WOULD re-prompt %s: %s" % (ident, reason))
            posts += 1
            continue
        if posts:
            sleep(cfg["spacing_seconds"])
        posts += 1
        ledger[sid] = dict(ledger.get(sid) or {}, state="owed", issue=ident, at=_iso(now))
        save_ledger(path, ledger)
        try:
            data = gql(M_REPLY, {"input": {"issueId": issue.get("id"), "parentId": root,
                                           "body": reprompt_body(node)}})
            landed = bool((data.get("commentCreate") or {}).get("success"))
        except PollerError as exc:
            note_failure("the reply failed (%s); it stays owed, and the next pass reads the "
                         "thread before trying again" % exc)
            continue
        if not landed:
            note_failure("the reply did not report success; it stays owed, and the next pass "
                         "reads the thread before trying again")
            continue
        ledger[sid] = {"state": "posted", "issue": ident, "at": _iso(now)}
        save_ledger(path, ledger)
        sent += 1
        log("RE-PROMPTED %s: %s" % (ident, reason))
    counts["reprompted"] = sent
    if errors:
        raise failed("could not re-prompt %d session(s): %s" % (len(errors), "; ".join(errors)))

    problem = any(f["state"] == FIRING for f in findings)
    if problem:
        return EXIT_DECLINED, "problems", findings, counts
    return EXIT_OK, ("ok" if open_ else "held"), findings, counts


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

    # ONE PASS AT A TIME. A pass run by hand beside the scheduled one would read the same
    # unmarked thread and reply twice. A dry run writes nothing, so it takes no lock.
    lock = None
    if not args.dry_run:
        import fcntl
        try:
            os.makedirs(state_dir, exist_ok=True)
            lock = open(os.path.join(state_dir, "pass.lock"), "w")
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            log("ERROR: another pass holds %s/pass.lock (%s); this one did nothing, and the "
                "running pass writes the heartbeat" % (state_dir, exc))
            return EXIT_ERROR

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
        for f in getattr(exc, "findings", None) or []:
            log("%-8s %-8s %s" % (f["state"].upper(), f["kind"], f["summary"]))
        write_heartbeat(state_dir, "error", started, args.dry_run,
                        getattr(exc, "findings", None) or (), getattr(exc, "counts", None),
                        error=str(exc))
        return EXIT_ERROR
    except Exception as exc:  # noqa: BLE001 — a bug must still leave a heartbeat saying so
        log("ERROR: the pass crashed: %s: %s" % (type(exc).__name__, exc))
        write_heartbeat(state_dir, "error", started, args.dry_run,
                        error="crashed: %s: %s" % (type(exc).__name__, exc))
        return EXIT_ERROR
    finally:
        signal.alarm(0)
        if lock is not None:
            lock.close()
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
                     pages=None, fail_threads=(), sessions_error=False):
            self.sessions, self.hooks = sessions, hooks if hooks is not None else [
                {"id": "w-1", "enabled": True, "url": None}]
            self.threads, self.fail_reply, self.hooks_error = threads or {}, fail_reply, hooks_error
            self.pages, self.page_reads = pages, 0
            self.fail_threads, self.sessions_error = set(fail_threads), sessions_error
            self.replies, self.queries = [], []

        def __call__(self, query, variables):
            self.queries.append(query)
            if "CatchupWebhooks" in query:
                if self.hooks_error:
                    raise PollerError("HTTP 403")
                return {"webhooks": {"nodes": self.hooks}}
            if "CatchupSessions" in query:
                if self.sessions_error:
                    raise PollerError("could not reach the Linear API: offline")
                if self.pages is not None:
                    i = self.page_reads
                    self.page_reads += 1
                    last = i + 1 >= len(self.pages)
                    return {"agentSessions": {"nodes": self.pages[i], "pageInfo": {
                        "hasNextPage": not last, "endCursor": None if last else "p%d" % i}}}
                return {"agentSessions": {"nodes": self.sessions,
                                          "pageInfo": {"hasNextPage": False, "endCursor": None}}}
            if "CatchupThread" in query:
                if variables["id"] in self.fail_threads:
                    raise PollerError("Linear API error (payload logged)")
                # A thread entry is a body (written by the key's own user) or (body, author).
                return {"viewer": {"id": OWNER}, "comment": {"id": variables["id"], "children": {
                    "nodes": [{"body": b[0], "user": {"id": b[1]}} if isinstance(b, tuple)
                              else {"body": b, "user": {"id": OWNER}}
                              for b in self.threads.get(variables["id"], [])]}}}
            if "CatchupReply" in query:
                self.replies.append(variables["input"])
                return {"commentCreate": {"success": not self.fail_reply, "comment": {"id": "r"}}}
            raise AssertionError("unexpected query")

    up = lambda url: (200, '{"cyrus_cli_version":"0.2.73"}')   # noqa: E731

    with tempfile.TemporaryDirectory() as tmp:
        def cfg(**over):
            raw = dict(EXAMPLE_CONFIG, agent_user_id=AGENT, teams=["KIT"], state_dir=os.path.join(tmp, over.pop(
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
        check("…and once stuck it STAYS stuck, reported until a person acts",
              classify(session("s1", 9), c, NOW, {"s1": {"state": "stuck"}})[0], "stuck")
        check("…which answering it ends",
              classify(session("s1", 9, acts=("AgentActivityResponseContent",)), c, NOW,
                       {"s1": {"state": "stuck"}})[0], "skip")
        check("…as does removing the delegation",
              classify(session("s1", 9, delegate="someone"), c, NOW, {"s1": {"state": "stuck"}})[0],
              "skip")
        check("with the route down, a re-prompted session is not newly judged stuck",
              classify(session("s1", 9), c, NOW, posted_old, gate_open=False)[0], "waiting")

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
        import io
        import contextlib
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            run_pass(cfg(_dir="cap-dry", max_per_pass=2), "k", dry_run=True,
                     gql=FakeLinear(many), get=up, now=NOW)
        check("a dry run names EVERY candidate for the cost sign-off, not just this pass's cap",
              (said.getvalue().count("CANDIDATE "), said.getvalue().count("WOULD re-prompt")), (5, 2))
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            run_pass(cfg(_dir="cap-dry-held"), "k", dry_run=True, gql=FakeLinear(
                many, hooks=[{"id": "w-1", "enabled": False, "url": None}]), get=up, now=NOW)
        check("…including when a disabled webhook holds them all",
              said.getvalue().count("CANDIDATE "), 5)

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
        check("a read cut at max_pages before its end is a blind spot a person must fix: firing",
              (fake.page_reads, codeP, [(f["kind"], f["state"]) for f in findingsP
                                        if f["kind"] == "coverage"]),
              (2, EXIT_DECLINED, [("coverage", FIRING)]))

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
        fake = FakeLinear([session("s1", 8)], hooks=[{"id": "w-1", "enabled": True, "url": None},
                                                     {"id": "w-2", "enabled": False, "url": None}])
        _c, resultW, findingsW, _n = run_pass(cfg(_dir="wh5"), "k", gql=fake, get=up, now=NOW)
        check("two app webhooks and no webhook_id: could not tell, so it neither pages nor holds",
              (resultW, [f["state"] for f in findingsW if f["kind"] == "webhook"], len(fake.replies)),
              ("ok", [UNKNOWN], 1))
        fake = FakeLinear([], hooks=[{"id": "w-1", "enabled": True, "url": None},
                                     {"id": "w-2", "enabled": False, "url": None}])
        check("a pinned webhook_id is judged alone",
              run_pass(cfg(_dir="wh4", webhook_id="w-1"), "k", gql=fake, get=up, now=NOW)[1], "ok")

        # -- stuck pages once ------------------------------------------------------------
        cst = cfg(_dir="stuck")
        save_ledger(ledger_path(cst["state_dir"]), {"s1": {"state": "posted", "at": _iso(NOW - 7200)}})
        fake = FakeLinear([session("s1", 10)])
        code, result, findings, _n = run_pass(cst, "k", gql=fake, get=up, now=NOW)
        check("a re-prompted session still silent is a problem, named by its own ticket",
              (code, result, fake.replies, [f["kind"] for f in findings if f["state"] == FIRING]),
              (EXIT_DECLINED, "problems", [], ["stuck-kit-1"]))
        fake = FakeLinear([session("s1", 10)])
        _c, result, findings, _n = run_pass(cst, "k", gql=fake, get=up, now=NOW + 900)
        check("…and the next pass reports it AGAIN (the monitor pages once), never re-prompting",
              (result, fake.replies, [f["kind"] for f in findings if f["state"] == FIRING],
               load_ledger(ledger_path(cst["state_dir"]))["s1"]["state"]),
              ("problems", [], ["stuck-kit-1"], "stuck"))
        check("a second stuck ticket is a second kind, so it pages too",
              (stuck_kind(session("s1", 1, ident="KIT-1")), stuck_kind(session("s2", 1, ident="KIT-22"))),
              ("stuck-kit-1", "stuck-kit-22"))
        cstq = cfg(_dir="stuck-held")
        save_ledger(ledger_path(cstq["state_dir"]), {"s1": {"state": "stuck", "at": _iso(NOW - 9000)}})
        fake = FakeLinear([session("s1", 10)], hooks=[{"id": "w-1", "enabled": False, "url": None}])
        _c, result, findings, _n = run_pass(cstq, "k", gql=fake, get=lambda u: (None, "down"), now=NOW)
        check("a HELD pass still looks: a stuck session and a disabled webhook stay reported",
              (result, sorted(f["kind"] for f in findings if f["state"] == FIRING)),
              ("problems", ["stuck-kit-1", "webhook"]))
        fake = FakeLinear([session("s1", 10)], sessions_error=True)
        write_heartbeat(cstq["state_dir"], "problems", _iso(), False,
                        [{"kind": "stuck-kit-1", "state": FIRING, "summary": "KIT-1 is silent"}])
        _c, result, findings, _n = run_pass(cstq, "k", gql=fake, get=lambda u: (None, "down"), now=NOW)
        check("held AND unable to read Linear: what the last pass found is carried, not cleared",
              (result, [f["kind"] for f in findings if f["state"] == FIRING],
               [f["state"] for f in findings if f["kind"] == "sessions"]),
              ("problems", ["stuck-kit-1"], [UNKNOWN]))

        # -- the gate: a route that never opens becomes loud ------------------------------
        cgh = cfg(_dir="held-streak")
        results = [run_pass(cgh, "k", gql=FakeLinear([session("s1", 8)]),
                            get=lambda u: (None, "refused"), now=NOW)[1]
                   for _ in range(HELD_ALERT_PASSES)]
        check("held passes are fine for a while, then a wrong URL or a dead dispatcher pages",
              (results[0], results[HELD_ALERT_PASSES - 2], results[-1]), ("held", "held", "problems"))
        run_pass(cgh, "k", gql=FakeLinear([]), get=up, now=NOW)
        check("…and one open pass resets the count", load_held(cgh["state_dir"]), 0)

        # -- one session per issue -------------------------------------------------------
        def on_issue(sid, age_h, issue_id, acts=()):
            n = session(sid, age_h, ident="KIT-9", comment="c-" + sid, acts=acts)
            n["issue"]["id"] = issue_id
            return n
        fake = FakeLinear([on_issue("a", 9, "i-x"), on_issue("b", 8.5, "i-x",
                                                             acts=("AgentActivityThoughtContent",))])
        _c, _r, _f, countsS = run_pass(cfg(_dir="sup1"), "k", gql=fake, get=up, now=NOW)
        check("a lost session with a NEWER session on the same issue is never re-prompted",
              (fake.replies, countsS["superseded"]), ([], 1))
        fake = FakeLinear([on_issue("a", 9, "i-x"), on_issue("b", 8.5, "i-x")])
        run_pass(cfg(_dir="sup2"), "k", gql=fake, get=up, now=NOW)
        check("…and two lost sessions on one issue get ONE reply, under the newest",
              [r["parentId"] for r in fake.replies], ["c-b"])

        # -- the receipt -----------------------------------------------------------------
        fake = FakeLinear([session("s1", 8)], threads={"c-root": [(MARK % "s1", "someone-else")]})
        run_pass(cfg(_dir="spoof"), "k", gql=fake, get=up, now=NOW)
        check("a receipt pasted by another member does not suppress the reply", len(fake.replies), 1)

        # -- one failing session never blocks the others ---------------------------------
        cfx = cfg(_dir="fail-one")
        fake = FakeLinear([session("old", 30, ident="KIT-1", comment="c-old"),
                           session("new", 9, ident="KIT-2", comment="c-new")],
                          fail_threads=("c-old",))
        try:
            run_pass(cfx, "k", gql=fake, get=up, now=NOW)
            raisedX = None
        except PollerError as exc:
            raisedX = exc
        led = load_ledger(ledger_path(cfx["state_dir"]))
        check("a session whose thread cannot be read fails the pass, LOUDLY, and the rest go on",
              (raisedX is not None and "KIT-1" in str(raisedX), [r["parentId"] for r in fake.replies],
               led.get("old", {}).get("failures"), led.get("new", {}).get("state")),
              (True, ["c-new"], 1, "posted"))
        check("…and the failed pass still carries what it found",
              [f["kind"] for f in getattr(raisedX, "findings", None) or []], ["webhook"])
        fake = FakeLinear([session("old", 30, ident="KIT-1", comment="c-old"),
                           session("new2", 9, ident="KIT-3", comment="c-new2")])
        run_pass(cfg(_dir="fail-one", max_per_pass=1), "k", gql=fake, get=up, now=NOW + 900)
        check("…and next pass a session that failed before goes to the back of the line",
              [r["parentId"] for r in fake.replies], ["c-new2"])

        # -- a failed post stays owed; the next pass reads the thread first --------------
        cf = cfg(_dir="fail")
        try:
            run_pass(cf, "k", gql=FakeLinear([session("s1", 8)], fail_reply=True), get=up, now=NOW)
            failed = False
        except PollerError:
            failed = True
        check("a re-prompt that did not report success fails the pass and stays owed",
              (failed, load_ledger(ledger_path(cf["state_dir"])).get("s1", {}).get("state"),
               load_ledger(ledger_path(cf["state_dir"])).get("s1", {}).get("failures")),
              (True, "owed", 1))
        cfk = cfg(_dir="killed")
        dying = FakeLinear([session("s1", 8)])
        real_call = dying.__call__

        def _dies_mid_reply(query, variables):
            if "CatchupReply" in query:
                raise RunTimeout()          # the pass clock fires while the reply is in flight
            return real_call(query, variables)
        try:
            run_pass(cfk, "k", gql=_dies_mid_reply, get=up, now=NOW)
        except RunTimeout:
            pass
        check("a pass killed mid-reply leaves the session OWED: the reply may have landed",
              load_ledger(ledger_path(cfk["state_dir"])).get("s1", {}).get("state"), "owed")
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
        cb2 = cfg(_dir="bad2")
        save_ledger(ledger_path(cb2["state_dir"]), {"s1": "posted"})
        try:
            run_pass(cb2, "k", gql=FakeLinear([session("s1", 8)]), get=up, now=NOW)
            said = ""
        except PollerError as exc:
            said = str(exc)
        check("…and so does a ledger entry that is not owed, posted or stuck", "s1" in said, True)

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

        # A bug, and a second pass beside a running one: both leave a record, neither posts.
        good_cfg = os.path.join(tmp, "good.json")
        with open(good_cfg, "w") as fh:
            json.dump(dict(EXAMPLE_CONFIG, agent_user_id=AGENT, linear_key_env="CATCHUP_SELFTEST_KEY",
                           state_dir=os.path.join(tmp, "crash")), fh)
        saved_pass, saved_key = globals()["run_pass"], os.environ.get("CATCHUP_SELFTEST_KEY")
        os.environ["CATCHUP_SELFTEST_KEY"] = "not-a-real-key"

        def _boom(*_a, **_k):
            raise ValueError("a bug")
        globals()["run_pass"] = _boom
        try:
            code = main(["run", "--config", good_cfg])
            beat = json.load(open(heartbeat_path(os.path.join(tmp, "crash"))))
            check("a pass that crashes still writes a heartbeat saying so",
                  (code, beat["result"], "crashed" in (beat.get("error") or "")),
                  (EXIT_ERROR, "error", True))
            import fcntl
            os.remove(heartbeat_path(os.path.join(tmp, "crash")))
            with open(os.path.join(tmp, "crash", "pass.lock"), "w") as held_lock:
                fcntl.flock(held_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                code = main(["run", "--config", good_cfg])
            check("a second pass beside a running one does nothing and leaves the beat to it",
                  (code, os.path.exists(heartbeat_path(os.path.join(tmp, "crash")))),
                  (EXIT_ERROR, False))
        finally:
            globals()["run_pass"] = saved_pass
            if saved_key is None:
                os.environ.pop("CATCHUP_SELFTEST_KEY", None)
            else:
                os.environ["CATCHUP_SELFTEST_KEY"] = saved_key

        # -- config ----------------------------------------------------------------------
        for bad, why in (({"teams": ["kit"]}, "team KEYS"), ({"agent_user_id": ""}, "agent_user_id"),
                         ({"linear_key_env": "lin_api_abc"}, "ENV VAR NAME"),
                         ({"max_per_pass": 50}, "max_per_pass"), ({"surprise": 1}, "unknown"),
                         ({"front_door_url": "door"}, "front_door_url"),
                         ({"max_per_pass": 10, "spacing_seconds": 60}, "pass clock")):
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
    check("its receipt is plain text, and not a mark the notifier pings on",
          ("pipeline-escalation" in MARK, "<!--" in MARK), (False, False))

    for f in fails:
        print("FAIL:", f)
    print("%d/%d checks passed" % (ran[0] - len(fails), ran[0]))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
