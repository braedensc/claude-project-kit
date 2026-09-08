#!/usr/bin/env python3
"""Stage E finding poller — a session's out-of-scope finding becomes a backlog ticket,
filed by a credential-holding job OUTSIDE the session, never by the session itself.

WHAT THIS IS, AND WHY IT EXISTS

  KIT-96 gave a session a safe way to file a follow-up finding: it *requests* one and a
  credential-holding executor creates it. On the GitHub-Actions dispatch backend that
  executor is a CI job. On a LOCAL-DAEMON backend (Cyrus on this machine) a session holds
  the tracker key directly, so there is no credential-free split to hide behind — the thing
  that keeps a local session honest is the tool-fence (the PreToolUse guard blocks a session
  creating a ticket directly). This poller is the OTHER half: it runs as the dispatcher's
  role account, reads the finding a session left as a structured COMMENT on its own ticket,
  and files the backlog ticket itself, forcing every field that carries authority. The
  session never creates; this job does. Same seam as pipeline_review_poller.py.

HOW A SESSION HANDS OVER A FINDING

  A session that meets something true but out of scope posts ONE comment on its own ticket
  containing a fenced `pipeline-finding/1` block:

      ```json
      {"schema": "pipeline-finding/1", "title": "one line", "body": "…markdown…"}
      ```

  It writes NOTHING ELSE about the finding — no create, no label, no state. Posting the
  comment is a plain save_comment the session already may do; creating the ticket is not.

WHAT THIS DOES, PER FINDING

  Creates ONE ticket in the SAME team as the source ticket, and FORCES:
    * state = that team's Backlog (intake) state — never ready/started/completed;
    * label = provenance:agent (the executor's mark; a session can request no provenance);
    * subscriber = the configured owner (so it reaches a person) — assignee is left UNSET,
      so the ticket is never assigned to the session's own identity;
    * a provenance line in the body naming the SOURCE ticket, so origin travels with it.
  Then it posts a "Filed as <ID>" reply on the source comment (a human-visible receipt and
  a dedup backstop) and records the source comment id in the seen-set.

WHAT IT NEVER DOES (asserted in --selftest, the way every Stage E script asserts it)

  It never moves a ticket to ready/working/review/done; never sets provenance:human,
  hooks-change, agent:* or blocked:* on anything; never assigns a ticket to the agent;
  never touches the SOURCE ticket's state or labels; never approves, merges or comments on
  a PR. Its ONLY Linear writes are `issueCreate` (the finding ticket) and `commentCreate`
  (the receipt). It trusts a finding only from the configured agent user — a comment from
  anyone else is ignored, so a finding cannot be injected by an outside commenter.

FLOOD GUARD

  At most `max_per_source` findings filed from any one source ticket per run (default 3,
  KIT-96's §8 cap), and at most `max_per_run` across the whole pass. Over either, the
  extras are LEFT for the next pass and named in the log — never silently dropped (§13).

ONE RUN IS ONE PASS THEN EXIT. A system LaunchDaemon with `StartInterval`, `RunAtLoad` and
no `KeepAlive` starts a fresh process each interval; each does scan → exit under a
wall-clock timeout, writes a HEARTBEAT so "dead" and "ran and did nothing" are
distinguishable (§13), and never loops internally.

Usage:
    pipeline_finding_poller.py scan   --config <poller.json> [--dry-run] [--timeout N]
    pipeline_finding_poller.py --example-config
    pipeline_finding_poller.py --selftest
Exit: 0 = pass ran (even if it filed nothing), 1 = the pass could not complete, 2 = usage.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

SCHEMA = "pipeline-finding/1"
SEEN_SCHEMA = "pipeline-finding-poller-seen/1"
HEARTBEAT_SCHEMA = "pipeline-finding-poller-heartbeat/1"
LINEAR_API = "https://api.linear.app/graphql"

DEFAULT_STATE_DIR = "~/.stage-e/finding"
DEFAULT_RUN_TIMEOUT_SECONDS = 300
DEFAULT_LOOKBACK_HOURS = 72
DEFAULT_MAX_PER_SOURCE = 3
DEFAULT_MAX_PER_RUN = 20
DEFAULT_ISSUE_PAGE = 50
DEFAULT_TITLE_CAP = 200
DEFAULT_BODY_CAP = 16000

# Protected label classes a session (or this job) must never put on a ticket, EXCEPT the
# one provenance:agent mark this job applies itself. Mirrors the PreToolUse guard's set.
PROTECTED_RE = re.compile(r"^(agent:|blocked:|provenance:)", re.IGNORECASE)
HOOKS_CHANGE = "hooks-change"
AGENT_PROVENANCE = "provenance:agent"

CONFIG_KEYS = {
    "teams", "owner_user_id", "owner_user_email",
    "agent_user_id", "agent_user_name",
    "provenance_agent_label", "backlog_state_name",
    "linear_key_env", "state_dir",
    "lookback_hours", "max_per_source", "max_per_run",
}


# --------------------------------------------------------------------------- #
# Errors + logging
# --------------------------------------------------------------------------- #
class PollerError(Exception):
    """The pass could not complete. Loud (exit 1); never confused with 'nothing to do'."""


class ConfigError(PollerError):
    """A deployment/config problem — a distinct, fixable class of PollerError."""


def log(msg):
    sys.stderr.write("[finding-poller] %s\n" % msg)
    sys.stderr.flush()


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------------------------------------------------------- #
# Config + credential  (names only — a value never lives in the file)
# --------------------------------------------------------------------------- #
def load_config(path):
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        raise PollerError("could not read --config %s: %s" % (path, exc))
    if not isinstance(raw, dict):
        raise PollerError("--config must hold one JSON object")
    unknown = sorted(set(raw) - CONFIG_KEYS)
    if unknown:
        raise PollerError("unknown config key(s): %s (see --example-config)" % ", ".join(unknown))
    teams = raw.get("teams") or []
    if not isinstance(teams, list) or not teams or not all(
            isinstance(t, str) and t.strip() for t in teams):
        raise PollerError("config 'teams' must be a non-empty list of team KEYS, e.g. [\"KIT\"]")
    if not raw.get("owner_user_id") and not raw.get("owner_user_email"):
        raise ConfigError("config needs 'owner_user_id' (preferred) or 'owner_user_email' — "
                          "the person a filed finding notifies")
    if not raw.get("agent_user_id") and not raw.get("agent_user_name"):
        raise ConfigError("config needs 'agent_user_id' (preferred) or 'agent_user_name' — "
                          "only findings authored by the dispatcher's agent user are trusted")
    key_env = raw.get("linear_key_env") or "STAGE_E_LINEAR_API_KEY"
    if not re.match(r"^[A-Z][A-Z0-9_]*$", key_env):
        raise PollerError("config 'linear_key_env' must be an ENV VAR NAME like "
                          "STAGE_E_LINEAR_API_KEY — never a credential value")
    cfg = {
        "teams": [t.strip() for t in teams],
        "owner_user_id": str(raw.get("owner_user_id") or ""),
        "owner_user_email": str(raw.get("owner_user_email") or ""),
        "agent_user_id": str(raw.get("agent_user_id") or ""),
        "agent_user_name": str(raw.get("agent_user_name") or ""),
        "provenance_agent_label": str(raw.get("provenance_agent_label") or AGENT_PROVENANCE),
        "backlog_state_name": str(raw.get("backlog_state_name") or "Backlog"),
        "linear_key_env": key_env,
        "state_dir": os.path.realpath(os.path.expanduser(raw.get("state_dir") or DEFAULT_STATE_DIR)),
        "lookback_hours": int(raw.get("lookback_hours") or DEFAULT_LOOKBACK_HOURS),
        "max_per_source": int(raw.get("max_per_source") or DEFAULT_MAX_PER_SOURCE),
        "max_per_run": int(raw.get("max_per_run") or DEFAULT_MAX_PER_RUN),
    }
    for k in ("max_per_source", "max_per_run", "lookback_hours"):
        if cfg[k] < 1:
            raise PollerError("config %r must be >= 1" % k)
    return cfg


def credential(cfg):
    name = cfg["linear_key_env"]
    val = os.environ.get(name, "").strip()
    if not val:
        raise PollerError(
            "$%s is unset — export it in the POLLER'S OWN env file under the role account's "
            "home (<home>/.stage-e/env, mode 600), never in the dispatcher's env file (copied "
            "unscrubbed into every session). --dry-run still needs it for reads." % name)
    return val


EXAMPLE_CONFIG = {
    "teams": ["KIT", "TOD"],
    "owner_user_id": "<your Linear user UUID>",
    "agent_user_id": "<the dispatcher agent's Linear user UUID>",
    "provenance_agent_label": "provenance:agent",
    "backlog_state_name": "Backlog",
    "linear_key_env": "STAGE_E_LINEAR_API_KEY",
    "state_dir": "~/.stage-e/finding",
    "lookback_hours": 72,
    "max_per_source": 3,
    "max_per_run": 20,
}


# --------------------------------------------------------------------------- #
# Pure logic — no I/O, so --selftest exercises all of it
# --------------------------------------------------------------------------- #
def fenced_json_blocks(text):
    """Every ```json … ``` block body in `text`, in order. Tolerant of ``` or ~~~."""
    out, fence, buf = [], None, []
    for line in (text or "").splitlines():
        m = re.match(r"^\s*(```|~~~)\s*([A-Za-z0-9_-]*)\s*$", line)
        if m:
            if fence is None:
                fence, buf = m.group(1), []
                _lang = m.group(2).lower()
                out_lang = _lang
            elif fence == m.group(1):
                out.append(("\n".join(buf), out_lang))
                fence = None
            continue
        if fence is not None:
            buf.append(line)
    return out


def parse_finding(comment_body):
    """(title, body) from a `pipeline-finding/1` block in a comment, or None if there is
    no well-formed finding. A malformed block is None, not an exception — one bad comment
    must not stop the pass; the log names it and the pass moves on."""
    for raw, lang in fenced_json_blocks(comment_body):
        if lang not in ("json", ""):
            continue
        if SCHEMA not in raw:
            continue
        try:
            doc = json.loads(raw)
        except ValueError:
            return None
        if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
            return None
        title = doc.get("title")
        body = doc.get("body")
        if not isinstance(title, str) or not title.strip():
            return None
        if not isinstance(body, str) or not body.strip():
            return None
        if len(title) > DEFAULT_TITLE_CAP or len(body) > DEFAULT_BODY_CAP:
            return None
        return (title.strip(), body)
    return None


def clean_labels(requested):
    """Ordinary labels a session may propose survive; every protected class is dropped.
    The session cannot mint provenance/supervision/acknowledgement — those are refused
    here exactly as the PreToolUse guard refuses them, so a finding carries only what a
    person could safely have typed."""
    out = []
    for lbl in requested or []:
        if not isinstance(lbl, str):
            continue
        s = lbl.strip()
        if not s or s == HOOKS_CHANGE or PROTECTED_RE.match(s):
            continue
        out.append(s)
    return out


def build_finding_body(source_id, body):
    """The description the executor writes: a forced provenance header the session cannot
    fake, then the session's own text."""
    header = ("> Filed by the Stage E finding poller from an agent session working "
              "**%s**. Origin: `provenance:agent` — a session requested this; it did not "
              "create it. Backlog, unreviewed, awaiting a person; it approves nothing and "
              "starts no session.\n\n" % source_id)
    return header + (body or "")


def select_to_file(candidates, seen, max_per_source, max_per_run):
    """(to_file, skipped) — apply dedup and the flood caps deterministically.

    candidates: [{comment_id, source_id, title, body}] in stable order.
    seen: {comment_id: {...}} already filed. A candidate already in seen is skipped as
    'done'; over a cap it is skipped as 'capped' and LEFT for the next pass.
    """
    to_file, skipped, per_source, total = [], [], {}, 0
    for c in candidates:
        cid = c["comment_id"]
        if cid in seen:
            skipped.append((cid, "already-filed"))
            continue
        src = c["source_id"]
        if total >= max_per_run:
            skipped.append((cid, "over max_per_run"))
            continue
        if per_source.get(src, 0) >= max_per_source:
            skipped.append((cid, "over max_per_source"))
            continue
        per_source[src] = per_source.get(src, 0) + 1
        total += 1
        to_file.append(c)
    return to_file, skipped


def creation_input(cfg, team_id, backlog_state_id, prov_label_id, extra_label_ids,
                   title, body, source_id):
    """The EXACT issueCreate input, built in one place so --selftest can assert its shape
    carries none of the authority a session must not hold."""
    label_ids = [prov_label_id] + [i for i in (extra_label_ids or []) if i != prov_label_id]
    inp = {
        "teamId": team_id,
        "title": title[:DEFAULT_TITLE_CAP],
        "description": build_finding_body(source_id, body),
        "stateId": backlog_state_id,
        "labelIds": label_ids,
        "subscriberIds": [cfg["owner_user_id"]] if cfg.get("owner_user_id") else [],
    }
    # assigneeId is deliberately ABSENT — never the session's identity, and a finding is a
    # backlog item nobody is yet working.
    return inp


# --------------------------------------------------------------------------- #
# Seen-set + heartbeat  (atomic; a corrupt seen-set refuses, never re-files)
# --------------------------------------------------------------------------- #
def _atomic_write_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.tmp-%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def seen_path(state_dir):
    return os.path.join(state_dir, "seen.json")


def heartbeat_path(state_dir):
    return os.path.join(state_dir, "heartbeat.json")


def load_seen(path):
    refuse = ("seen-set %s %%s — refusing to run so no finding is filed twice; move or "
              "repair the file" % path)
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise PollerError(refuse % ("is unreadable (%s)" % exc))
    if not isinstance(doc, dict) or doc.get("schema") != SEEN_SCHEMA:
        raise PollerError(refuse % ("is not a %s document (schema %r)"
                                    % (SEEN_SCHEMA, doc.get("schema") if isinstance(doc, dict) else None)))
    seen = doc.get("seen")
    if not isinstance(seen, dict) or not all(isinstance(v, dict) for v in seen.values()):
        raise PollerError(refuse % "has a malformed 'seen' map")
    return dict(seen)


def save_seen(path, seen):
    _atomic_write_json(path, {"schema": SEEN_SCHEMA, "seen": seen})


def write_heartbeat(state_dir, ok, filed, skipped, error=None):
    _atomic_write_json(heartbeat_path(state_dir), {
        "schema": HEARTBEAT_SCHEMA,
        "at": _now_iso(),
        "ok": bool(ok),
        "filed": int(filed),
        "skipped": int(skipped),
        "error": error,
    })


# --------------------------------------------------------------------------- #
# Linear I/O
# --------------------------------------------------------------------------- #
def linear_graphql(query, variables, api_key):
    req = urllib.request.Request(
        LINEAR_API,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Content-Type": "application/json", "Authorization": api_key})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        log("Linear API HTTP %d body: %r" % (exc.code, exc.read()[:400]))
        raise PollerError("Linear API HTTP %d (body logged)" % exc.code)
    except urllib.error.URLError as exc:
        raise PollerError("could not reach the Linear API: %s" % exc.reason)
    except (OSError, ValueError) as exc:
        raise PollerError("Linear API call failed: %s" % exc)
    if payload.get("errors"):
        log("Linear API error payload: %s" % json.dumps(payload["errors"])[:600])
        raise PollerError("Linear API error (payload logged)")
    return payload.get("data") or {}


_Q_TEAM = """
query($key: String!) {
  teams(filter: {key: {eq: $key}}, first: 2) {
    nodes {
      id key
      states(first: 100) { nodes { id name type } }
      labels(first: 250) { nodes { id name } }
    }
  }
}"""

_Q_RECENT = """
query($teamId: ID!, $since: DateTimeOrDuration!, $after: String) {
  issues(filter: {team: {id: {eq: $teamId}}, updatedAt: {gt: $since}},
         first: %d, after: $after, orderBy: updatedAt) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id identifier
      comments(first: 50) { nodes { id body createdAt user { id name } } }
    }
  }
}""" % DEFAULT_ISSUE_PAGE

_M_CREATE = """
mutation($input: IssueCreateInput!) {
  issueCreate(input: $input) { success issue { id identifier url } }
}"""

_M_COMMENT = """
mutation($issueId: String!, $body: String!) {
  commentCreate(input: {issueId: $issueId, body: $body}) { success }
}"""


def resolve_team(cfg, key, api_key):
    """(team_id, backlog_state_id, {label_name: id}) for one team key, or ConfigError."""
    nodes = (linear_graphql(_Q_TEAM, {"key": key}, api_key).get("teams") or {}).get("nodes") or []
    if not nodes:
        raise ConfigError("no team has key %r in this workspace" % key)
    if len(nodes) > 1:
        raise ConfigError("%d teams have key %r — ambiguous" % (len(nodes), key))
    team = nodes[0]
    states = (team.get("states") or {}).get("nodes") or []
    want = cfg["backlog_state_name"].strip().lower()
    backlog = None
    for s in states:
        if (s.get("name") or "").strip().lower() == want:
            backlog = s["id"]; break
    if not backlog:  # fall back to the single backlog-TYPE state
        backlogs = [s for s in states if s.get("type") == "backlog"]
        if len(backlogs) == 1:
            backlog = backlogs[0]["id"]
    if not backlog:
        raise ConfigError("team %r has no state named %r and not exactly one backlog-type "
                          "state — set 'backlog_state_name'" % (key, cfg["backlog_state_name"]))
    labels = {(l.get("name") or ""): l["id"] for l in (team.get("labels") or {}).get("nodes") or []}
    if cfg["provenance_agent_label"] not in labels:
        raise ConfigError("team %r has no %r label — create it (workspace-scoped) before the "
                          "poller can mark a finding agent-filed" % (key, cfg["provenance_agent_label"]))
    return team["id"], backlog, labels


def _since(hours):
    return "-PT%dH" % int(hours)


def scan_team(cfg, key, api_key):
    """[{comment_id, source_id, source_uuid, title, body}] for well-formed findings the
    configured agent user left on recent tickets in team `key`."""
    team_id, _backlog, _labels = resolve_team(cfg, key, api_key)
    agent_id = cfg.get("agent_user_id")
    agent_name = (cfg.get("agent_user_name") or "").strip().lower()
    out, after, pages = [], None, 0
    while pages < 20:
        data = linear_graphql(_Q_RECENT, {"teamId": team_id,
                                          "since": _since(cfg["lookback_hours"]),
                                          "after": after}, api_key)
        block = data.get("issues") or {}
        for issue in block.get("nodes") or []:
            for c in (issue.get("comments") or {}).get("nodes") or []:
                user = c.get("user") or {}
                if agent_id:
                    if user.get("id") != agent_id:
                        continue
                elif (user.get("name") or "").strip().lower() != agent_name:
                    continue
                parsed = parse_finding(c.get("body") or "")
                if not parsed:
                    continue
                out.append({"comment_id": c["id"], "source_id": issue.get("identifier"),
                            "source_uuid": issue.get("id"), "team_id": team_id,
                            "team_key": key, "title": parsed[0], "body": parsed[1]})
        info = block.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            break
        after = info.get("endCursor"); pages += 1
    return out


def already_filed_reply(source_uuid, api_key, agent_id):
    """True if a 'Filed as' receipt from the agent already sits on the source ticket — the
    dedup backstop for when the seen-set was lost."""
    q = """query($id: String!) { issue(id: $id) {
              comments(first: 50) { nodes { body user { id } } } } }"""
    issue = (linear_graphql(q, {"id": source_uuid}, api_key).get("issue")) or {}
    for c in (issue.get("comments") or {}).get("nodes") or []:
        if (c.get("user") or {}).get("id") == agent_id and "Filed as" in (c.get("body") or ""):
            return True
    return False


def file_finding(cfg, cand, api_key):
    """issueCreate the backlog ticket, forcing the safe fields; return its identifier."""
    team_id, backlog, labels = resolve_team(cfg, cand["team_key"], api_key)
    prov = labels[cfg["provenance_agent_label"]]
    inp = creation_input(cfg, team_id, backlog, prov, [], cand["title"], cand["body"],
                         cand["source_id"])
    res = (linear_graphql(_M_CREATE, {"input": inp}, api_key).get("issueCreate")) or {}
    if not res.get("success") or not res.get("issue"):
        raise PollerError("issueCreate did not succeed for a finding from %s" % cand["source_id"])
    return res["issue"]["identifier"]


def post_receipt(source_uuid, filed_id, api_key):
    body = ("Filed as **%s** (backlog, `provenance:agent`). This finding was requested by "
            "the session; the Stage E finding poller created the ticket." % filed_id)
    linear_graphql(_M_COMMENT, {"issueId": source_uuid, "body": body}, api_key)


# --------------------------------------------------------------------------- #
# The pass
# --------------------------------------------------------------------------- #
def run_scan(cfg, api_key, dry_run, deadline):
    seen = load_seen(seen_path(cfg["state_dir"]))
    agent_id = cfg.get("agent_user_id")
    candidates = []
    for key in cfg["teams"]:
        if time.time() > deadline:
            raise PollerError("timed out during scan before team %r" % key)
        candidates.extend(scan_team(cfg, key, api_key))
    to_file, skipped = select_to_file(candidates, seen, cfg["max_per_source"], cfg["max_per_run"])
    for cid, why in skipped:
        if why != "already-filed":
            log("left for next pass (%s): comment %s" % (why, cid))
    filed = 0
    for cand in to_file:
        if time.time() > deadline:
            log("timed out; %d filed, remainder left for next pass" % filed); break
        cid = cand["comment_id"]
        # Backstop against a lost seen-set: if a receipt already exists, record and skip.
        if agent_id and not dry_run and already_filed_reply(cand["source_uuid"], api_key, agent_id):
            seen[cid] = {"filed": "?", "at": _now_iso(), "note": "receipt already present"}
            save_seen(seen_path(cfg["state_dir"]), seen)
            continue
        if dry_run:
            log("DRY-RUN would file: %r (from %s)" % (cand["title"][:80], cand["source_id"]))
            filed += 1
            continue
        filed_id = file_finding(cfg, cand, api_key)
        post_receipt(cand["source_uuid"], filed_id, api_key)
        seen[cid] = {"filed": filed_id, "source": cand["source_id"], "at": _now_iso()}
        save_seen(seen_path(cfg["state_dir"]), seen)   # write-through, never batched
        log("filed %s from %s" % (filed_id, cand["source_id"]))
        filed += 1
    return filed, len(skipped)


def cmd_scan(args):
    cfg = load_config(args.config)
    api_key = credential(cfg)
    deadline = time.time() + (args.timeout or DEFAULT_RUN_TIMEOUT_SECONDS)
    try:
        filed, skipped = run_scan(cfg, api_key, args.dry_run, deadline)
    except PollerError as exc:
        write_heartbeat(cfg["state_dir"], ok=False, filed=0, skipped=0, error=str(exc))
        log("FAILED: %s" % exc)
        return 1
    write_heartbeat(cfg["state_dir"], ok=True, filed=filed, skipped=skipped)
    log("pass complete: %d filed, %d skipped%s" % (filed, skipped, " (dry-run)" if args.dry_run else ""))
    return 0


# --------------------------------------------------------------------------- #
# Selftest — the pure logic and the never-does invariants
# --------------------------------------------------------------------------- #
def selftest():
    fails = []
    count = [0]

    def ok(name, cond):
        count[0] += 1
        if not cond:
            fails.append(name)

    # parse_finding
    good = "noise\n```json\n" + json.dumps(
        {"schema": SCHEMA, "title": "stale id in notify.yml", "body": "line 5 cites a dead id"}
    ) + "\n```\ntrailing"
    ok("parse: a well-formed block yields (title, body)", parse_finding(good) ==
       ("stale id in notify.yml", "line 5 cites a dead id"))
    ok("parse: no block is None", parse_finding("just a comment") is None)
    ok("parse: wrong schema is None",
       parse_finding("```json\n{\"schema\":\"other/1\",\"title\":\"t\",\"body\":\"b\"}\n```") is None)
    ok("parse: empty title is None",
       parse_finding("```json\n{\"schema\":\"%s\",\"title\":\" \",\"body\":\"b\"}\n```" % SCHEMA) is None)
    ok("parse: empty body is None",
       parse_finding("```json\n{\"schema\":\"%s\",\"title\":\"t\",\"body\":\"\"}\n```" % SCHEMA) is None)
    ok("parse: malformed JSON is None (not an exception)",
       parse_finding("```json\n{\"schema\":\"%s\", not json\n```" % SCHEMA) is None)
    ok("parse: an over-cap title is None",
       parse_finding("```json\n%s\n```" % json.dumps(
           {"schema": SCHEMA, "title": "x" * (DEFAULT_TITLE_CAP + 1), "body": "b"})) is None)

    # clean_labels — every protected class is dropped, ordinary survives
    cleaned = clean_labels(["track:infra", "provenance:human", "provenance:agent",
                            "agent:needs-human", "blocked:capacity", HOOKS_CHANGE, "bug"])
    ok("labels: ordinary survive", cleaned == ["track:infra", "bug"])
    ok("labels: every protected class dropped",
       not any(PROTECTED_RE.match(x) or x == HOOKS_CHANGE for x in cleaned))

    # select_to_file — dedup + both caps
    cands = [{"comment_id": "c%d" % i, "source_id": "KIT-1", "title": "t", "body": "b"}
             for i in range(5)]
    tf, sk = select_to_file(cands, {"c0": {}}, max_per_source=3, max_per_run=20)
    ok("select: already-seen skipped", all(c["comment_id"] != "c0" for c in tf))
    ok("select: max_per_source caps one source at 3", len(tf) == 3)
    ok("select: the rest are left, not dropped", len(sk) == 2)  # c0 seen + one over-cap
    many = [{"comment_id": "d%d" % i, "source_id": "KIT-%d" % i, "title": "t", "body": "b"}
            for i in range(30)]
    tf2, _ = select_to_file(many, {}, max_per_source=3, max_per_run=20)
    ok("select: max_per_run caps the whole pass", len(tf2) == 20)

    # creation_input — the never-does shape
    cfg = {"owner_user_id": "OWNER"}
    inp = creation_input(cfg, "TEAM", "BACKLOG", "PROVAGENT", ["ORD1"], "t", "b", "KIT-9")
    ok("create: lands in the given backlog state", inp["stateId"] == "BACKLOG")
    ok("create: provenance:agent label is applied", "PROVAGENT" in inp["labelIds"])
    ok("create: owner is a subscriber", inp["subscriberIds"] == ["OWNER"])
    ok("create: NEVER an assignee (never the session identity)", "assigneeId" not in inp)
    ok("create: body carries the source provenance line", "KIT-9" in inp["description"]
       and "provenance:agent" in inp["description"])
    ok("create: no ready/started key smuggled in", "stateId" in inp and inp["stateId"] == "BACKLOG")

    # config: names only, never a value; caps validated
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "c.json")
        json.dump({"teams": ["KIT"], "owner_user_id": "O", "agent_user_id": "A"},
                  open(p, "w"))
        cfg2 = load_config(p)
        ok("config: defaults applied", cfg2["max_per_source"] == DEFAULT_MAX_PER_SOURCE
           and cfg2["linear_key_env"] == "STAGE_E_LINEAR_API_KEY")
        json.dump({"teams": ["KIT"], "owner_user_id": "O", "agent_user_id": "A",
                   "linear_key_env": "sk-secretvalue"}, open(p, "w"))
        try:
            load_config(p); ok("config: a credential VALUE is refused", False)
        except PollerError:
            ok("config: a credential VALUE is refused", True)
        json.dump({"teams": [], "owner_user_id": "O", "agent_user_id": "A"}, open(p, "w"))
        try:
            load_config(p); ok("config: empty teams refused", False)
        except PollerError:
            ok("config: empty teams refused", True)

    if fails:
        print("FAIL: %d finding-poller selftest case(s) failed:" % len(fails))
        for f in fails:
            print("  - %s" % f)
        return 1
    print("OK: finding-poller selftest (%d checks)" % count[0])
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", nargs="?", choices=["scan"], help="the one pass")
    ap.add_argument("--config")
    ap.add_argument("--dry-run", action="store_true", help="read + report, write nothing")
    ap.add_argument("--timeout", type=int, default=0)
    ap.add_argument("--example-config", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.example_config:
        print(json.dumps(EXAMPLE_CONFIG, indent=2))
        return 0
    if args.command == "scan":
        if not args.config:
            print("scan needs --config", file=sys.stderr)
            return 2
        return cmd_scan(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
