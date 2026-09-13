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
  Then it records the source comment id in the seen-set and posts a "Filed as <ID>" reply
  on the source ticket.

WHICH COMMENTS A PASS READS  (and why it is not the tickets that changed)

  A finding is a COMMENT, so the scan is keyed on the COMMENT's `createdAt` — never on the
  ticket's `updatedAt`. Keying on the ticket asks the wrong question: a finding written on
  a ticket nobody has touched since is invisible to a ticket-window query, and a finding
  that goes unread on the pass after it was written is not delayed, it is LOST. The scan
  therefore pages the root `comments` query, filtered by team and by `createdAt`.

  Each team carries a WATERMARK in the state dir: the instant up to which that team's
  comments have been read and RESOLVED. A pass asks for everything newer than it (less a
  small overlap for clock skew), so a daemon that was down for a week covers the week
  rather than the last `lookback_hours`. `lookback_hours` is the COLD-START window only —
  what a team with no watermark yet asks for on its first pass.

  A watermark moves only to where the pass actually finished, and never past something it
  left behind. Anything over a flood cap, anything the wall clock cut off, and every team
  whose comment pages ran past the page bound holds its team's watermark where it was, so
  the next pass asks for that window again. Capped is LEFT, never dropped.

  LIVE-TEST ITEMS (coded defensively; verify on the first real run and amend here)
    - `Query.comments` is asked for `filter: {createdAt: …, issue: {team: {id: …}}}`. If a
      live schema refuses that filter, the pass fails LOUDLY — the API error is logged, the
      run exits non-zero and the heartbeat says `error` — rather than returning an empty
      window that would read as "nothing to file". That is the correct failure, but it is a
      failure: nothing is filed until the query is corrected.
    - The sort direction of `orderBy: createdAt` is not stated in the typings, so nothing
      here depends on it. A pass that runs past its page bound does not advance that team's
      watermark at all, so it cannot matter whether the unread remainder was the newest or
      the oldest part of the window — either way the next pass asks the same question.

DEDUP: THE SEEN-SET IS THE AUTHORITY

  A finding is filed at most once because its source COMMENT ID is in the seen-set, which
  is written through the moment the ticket is created — before the receipt, so a receipt
  that fails to post can never cost a dedup record. The "Filed as <ID>" receipt is a
  HUMAN-VISIBLE RECORD, and nothing more: it is posted with the poller's own (role-account)
  Linear key, so its author is the key's user and not the agent user, and a backstop that
  looked for a receipt "from the agent" therefore matched nothing and protected nothing.
  It was removed rather than left to read as a safety net that was not one. The operational
  consequence is stated plainly in docs/FINDING-POLLER.md: the state dir is the dedup
  record, so a seen-set that is deleted or restored from elsewhere can re-file findings
  already filed. A pass that finds no seen-set says so in its log rather than assuming it
  is the first one.

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

EVERY TERMINAL PATH WRITES THE HEARTBEAT, INCLUDING A CONFIG OR CREDENTIAL FAILURE. That
is the whole point of the file and it used to be the one path that missed it: a poller that
exited before it learned where its state dir was left a stale timestamp, which is the same
symptom as a daemon that never started. "Not running" and "ran and could not do it" have
opposite remedies, so the state dir is worked out FIRST — from the config file if it can be
read at all, otherwise from the default — and a failure writes a heartbeat there saying
`result: usage` with the error text before it exits. A `--dry-run` writes no heartbeat at
all, deliberately: an operator's dry run must never freshen a liveness file the operator is
about to read as proof the daemon is alive.

Usage:
    pipeline_finding_poller.py scan   --config <poller.json> [--dry-run] [--timeout N]
    pipeline_finding_poller.py --example-config
    pipeline_finding_poller.py --selftest
Exit: 0 = the pass ran (even if it filed nothing), 1 = the pass could not complete,
      2 = usage: bad arguments, an unreadable/invalid config, or a missing credential.
"""
import argparse
import json
import os
import re
import sys
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

SCHEMA = "pipeline-finding/1"
SEEN_SCHEMA = "pipeline-finding-poller-seen/1"
# /2: the review poller's heartbeat shape (command, result, exit_code, started/ended), so
# one monitoring rule reads every Stage E daemon, plus this poller's filed/skipped counts.
HEARTBEAT_SCHEMA = "pipeline-finding-poller-heartbeat/2"
WATERMARK_SCHEMA = "pipeline-finding-poller-watermark/1"
LINEAR_API = "https://api.linear.app/graphql"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
RESULT_BY_CODE = {EXIT_OK: "ok", EXIT_ERROR: "error", EXIT_USAGE: "usage"}

DEFAULT_STATE_DIR = "~/.stage-e/finding"
DEFAULT_RUN_TIMEOUT_SECONDS = 300
DEFAULT_LOOKBACK_HOURS = 72          # COLD START only: a team with no watermark yet
DEFAULT_MAX_PER_SOURCE = 3
DEFAULT_MAX_PER_RUN = 20
DEFAULT_COMMENT_PAGE = 50
DEFAULT_MAX_PAGES = 20               # per team, per pass; over it the watermark stays put
DEFAULT_WATERMARK_OVERLAP_MINUTES = 15
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
    "lookback_hours", "watermark_overlap_minutes", "max_per_source", "max_per_run",
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


def _iso(dt):
    """One canonical spelling, so a stored watermark and a Linear timestamp compare."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(text):
    """A UTC datetime from an ISO-8601 instant, or None. Tolerant of what Linear returns
    (fractional seconds, `Z` or `+00:00`) because these values are COMPARED — comparing the
    strings instead would sort `…06.123Z` before `…06Z` and quietly skip a second's worth
    of comments at every watermark boundary."""
    if not isinstance(text, str) or not text.strip():
        return None
    raw = text.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


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
        "state_dir": _resolve_state_dir(raw.get("state_dir")),
        "lookback_hours": int(raw.get("lookback_hours") or DEFAULT_LOOKBACK_HOURS),
        "watermark_overlap_minutes": int(
            raw.get("watermark_overlap_minutes", DEFAULT_WATERMARK_OVERLAP_MINUTES)),
        "max_per_source": int(raw.get("max_per_source") or DEFAULT_MAX_PER_SOURCE),
        "max_per_run": int(raw.get("max_per_run") or DEFAULT_MAX_PER_RUN),
    }
    for k in ("max_per_source", "max_per_run", "lookback_hours"):
        if cfg[k] < 1:
            raise PollerError("config %r must be >= 1" % k)
    if cfg["watermark_overlap_minutes"] < 0:
        raise PollerError("config 'watermark_overlap_minutes' must be >= 0")
    return cfg


def _resolve_state_dir(value):
    return os.path.realpath(os.path.expanduser(value or DEFAULT_STATE_DIR))


def state_dir_hint(path):
    """Where to write a heartbeat for a run that never got a config.

    Deliberately separate from load_config and deliberately incapable of failing: it is
    consulted when the config is the thing that went wrong, and a heartbeat that could not
    be placed is the one failure mode a liveness file must not have. It takes `state_dir`
    from the file if the file can be read and parsed at all — even if the config is invalid
    for every other reason — and otherwise falls back to the default the installer writes.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        if isinstance(raw, dict) and isinstance(raw.get("state_dir"), str):
            return _resolve_state_dir(raw["state_dir"])
    except Exception:       # noqa: BLE001 — any failure at all means "use the default"
        pass
    return _resolve_state_dir(None)


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
    "watermark_overlap_minutes": 15,
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
    'already-filed'; over a cap it is skipped as 'over …' and LEFT for the next pass.

    Returns the CANDIDATE (not just its id) with each skip reason, because a candidate left
    behind by a cap is what holds its team's watermark back — a caller that only knew the
    id could not work out how far the pass may safely advance, and would step over the very
    finding the cap promised to keep.
    """
    to_file, skipped, per_source, total = [], [], {}, 0
    for c in candidates:
        if c["comment_id"] in seen:
            skipped.append((c, "already-filed"))
            continue
        src = c["source_id"]
        if total >= max_per_run:
            skipped.append((c, "over max_per_run"))
            continue
        if per_source.get(src, 0) >= max_per_source:
            skipped.append((c, "over max_per_source"))
            continue
        per_source[src] = per_source.get(src, 0) + 1
        total += 1
        to_file.append(c)
    return to_file, skipped


def since_for_team(watermark, lookback_hours, overlap_minutes, now_iso):
    """The `createdAt >` bound one pass asks a team for.

    With a watermark: everything since it, less an overlap for clock skew and for comments
    that land out of order — re-reading a few minutes costs nothing, because the seen-set
    dedups. Without one (a team's first pass): the cold-start window, `lookback_hours`.

    The watermark is used even when it is far older than the cold-start window. That is the
    point: a daemon that was down for a week must ask for the week, not for the last 72
    hours, or the outage silently eats every finding written during it.
    """
    now = _parse_iso(now_iso) or datetime.now(timezone.utc)
    wm = _parse_iso(watermark)
    if wm is None:
        return _iso(now - timedelta(hours=int(lookback_hours)))
    return _iso(wm - timedelta(minutes=int(overlap_minutes)))


def next_watermark(previous, scan_started, oldest_left, complete):
    """Where a team's watermark may move after a pass — never past unfinished work.

    `complete` is False when the team's comment pages ran past the page bound, so part of
    the window was never read; the watermark then does not move at all and the next pass
    asks the same question. `oldest_left` is the createdAt of the oldest candidate this
    pass did NOT resolve (over a flood cap, or cut off by the wall clock): the watermark
    stops one second SHORT of it, because the query bound is exclusive (`gt`) and landing
    exactly on a leftover would skip the finding the cap promised to keep for next time.
    With nothing left behind, the watermark moves to the moment the scan began — never to
    "now", which would step over anything written while the pass was running.
    """
    if not complete:
        return previous
    floor = _parse_iso(oldest_left) if oldest_left else _parse_iso(scan_started)
    if floor is None:
        return previous
    candidate = floor - timedelta(seconds=1)
    prev = _parse_iso(previous)
    if prev is not None and candidate <= prev:
        return previous                      # never backwards, never a re-scan loop
    return _iso(candidate)


def advance_watermarks(watermarks, teams, complete, left_behind, scan_started):
    """Apply next_watermark per team; return True if any moved. Mutates `watermarks`."""
    oldest = {}
    for cand in left_behind:
        key, at = cand.get("team_key"), _parse_iso(cand.get("created_at"))
        if key is None or at is None:
            continue
        if key not in oldest or at < oldest[key]:
            oldest[key] = at
    moved = False
    for key in teams:
        nxt = next_watermark(watermarks.get(key), scan_started,
                             _iso(oldest[key]) if key in oldest else None,
                             bool(complete.get(key)))
        if nxt and nxt != watermarks.get(key):
            watermarks[key] = nxt
            moved = True
    return moved


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


def watermark_path(state_dir):
    return os.path.join(state_dir, "watermarks.json")


def load_seen(path):
    refuse = ("seen-set %s %%s — refusing to run so no finding is filed twice; move or "
              "repair the file" % path)
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        # Said out loud, because the seen-set is the ONLY dedup authority (the receipt is
        # a record, not a guard). On a first pass this line is the truth; on any later one
        # it means the file was lost, and findings already filed can be filed again.
        log("no seen-set at %s — reading this pass as a FIRST pass. If this poller has run "
            "before, the file was lost: findings it already filed can be filed a second "
            "time, because the seen-set is the only thing that remembers them." % path)
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


def load_watermarks(path):
    """{team_key: iso} — how far each team has been read AND resolved.

    Unlike the seen-set, a damaged watermark file does not refuse the run. Losing it cannot
    cause a double file (the seen-set prevents that), only a narrower window — so the pass
    degrades to the cold-start window and SAYS SO, loudly enough that the one thing this
    costs (findings older than `lookback_hours` are not re-asked for until the file is
    repaired) is on the record rather than inferred from an empty result.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        log("WARNING: the watermark file %s is unreadable (%s). This pass falls back to the "
            "cold-start window, so a finding older than that is NOT re-asked for until the "
            "file is repaired or removed. Nothing is filed twice — the seen-set still "
            "holds." % (path, exc))
        return {}
    if not isinstance(doc, dict) or doc.get("schema") != WATERMARK_SCHEMA:
        log("WARNING: %s is not a %s document; falling back to the cold-start window for "
            "every team (see the note above — no finding is filed twice)."
            % (path, WATERMARK_SCHEMA))
        return {}
    marks = doc.get("teams")
    if not isinstance(marks, dict):
        return {}
    return {k: v for k, v in marks.items() if isinstance(k, str) and _parse_iso(v)}


def save_watermarks(path, marks):
    _atomic_write_json(path, {"schema": WATERMARK_SCHEMA, "teams": marks,
                              "written_at": _now_iso()})


def write_heartbeat(state_dir, code, started_at, started_mono, dry_run,
                    filed=0, skipped=0, error=None):
    """Record that a run FINISHED and what it decided — the review poller's shape, so one
    monitoring rule reads every Stage E daemon's heartbeat.

    Best effort by design: a heartbeat that could not be written must never change a run's
    exit code, or the liveness probe becomes a second way to fail. Not written on
    --dry-run — a dry pass leaves no state, and an operator's dry run that freshened this
    file would be forging the very signal the operator then reads as proof of life.

    This is the file that separates "the poller is dead" (a stale timestamp) from "the
    poller ran and could not do it" (a fresh timestamp with a non-`ok` result), which is
    why even a config failure — the run that never learned anything else — writes one.
    """
    if dry_run:
        return False
    doc = {"schema": HEARTBEAT_SCHEMA, "command": "scan",
           "result": RESULT_BY_CODE.get(code, "unknown"), "exit_code": code,
           "started_at": started_at, "ended_at": _now_iso(),
           "duration_seconds": round(time.monotonic() - started_mono, 3),
           "pid": os.getpid(), "filed": int(filed), "skipped": int(skipped),
           "error": error}
    try:
        _atomic_write_json(heartbeat_path(state_dir), doc)
        return True
    except OSError as exc:
        log("NOTE: the heartbeat could not be written to %s (%s); the run's own result "
            "stands" % (heartbeat_path(state_dir), exc))
        return False


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

# COMMENTS, not issues, and `createdAt`, not `updatedAt`. The finding IS the comment, so
# the comment's own creation time is the only timestamp that answers "is there anything
# here I have not read?". Asking the issues endpoint for recently-UPDATED tickets asks a
# different question whose answer usually looks the same and sometimes silently is not: a
# finding left on a ticket that then sits untouched falls out of the window entirely, and a
# missed finding is lost rather than queued.
_Q_COMMENTS = """
query($teamId: ID!, $since: DateTimeOrDuration!, $after: String) {
  comments(filter: {createdAt: {gt: $since}, issue: {team: {id: {eq: $teamId}}}},
           first: %d, after: $after, orderBy: createdAt) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id body createdAt
      user { id name }
      issue { id identifier }
    }
  }
}""" % DEFAULT_COMMENT_PAGE

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


def scan_team(cfg, key, api_key, since):
    """([candidate], complete) — well-formed findings the configured agent user WROTE after
    `since`, keyed on each comment's own createdAt.

    `complete` is False when the page bound was reached, which means part of the window was
    never read; the caller must then leave that team's watermark where it is.
    """
    team_id, _backlog, _labels = resolve_team(cfg, key, api_key)
    agent_id = cfg.get("agent_user_id")
    agent_name = (cfg.get("agent_user_name") or "").strip().lower()
    out, after, pages = [], None, 0
    while True:
        data = linear_graphql(_Q_COMMENTS, {"teamId": team_id, "since": since,
                                            "after": after}, api_key)
        block = data.get("comments") or {}
        for c in block.get("nodes") or []:
            issue = c.get("issue") or {}
            if not issue.get("id"):
                continue                      # a document/project comment, not a ticket
            user = c.get("user") or {}
            if agent_id:
                if user.get("id") != agent_id:
                    continue
            elif (user.get("name") or "").strip().lower() != agent_name:
                continue
            parsed = parse_finding(c.get("body") or "")
            if not parsed:
                if SCHEMA in (c.get("body") or ""):
                    log("ignored a malformed %s block in comment %s on %s — one bad block "
                        "does not stop the pass" % (SCHEMA, c.get("id"), issue.get("identifier")))
                continue
            out.append({"comment_id": c["id"], "source_id": issue.get("identifier"),
                        "source_uuid": issue.get("id"), "team_id": team_id,
                        "team_key": key, "created_at": c.get("createdAt"),
                        "title": parsed[0], "body": parsed[1]})
        info = block.get("pageInfo") or {}
        pages += 1
        if not info.get("hasNextPage"):
            return out, True
        if pages >= DEFAULT_MAX_PAGES:
            return out, False
        after = info.get("endCursor")


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
    """A HUMAN-VISIBLE RECORD on the source ticket, and only that.

    It is not a dedup mechanism and must never be read as one. It is written with this
    poller's own Linear key, so its author is that key's user — the owner on a role-account
    deployment — and not the agent user whose comments the scan trusts. A backstop that
    looked for a receipt "from the agent" therefore matched nothing on every real
    deployment: an inert guard, which is worse than none, because it reads as a second
    line of defence that is not there. The seen-set is the authority.
    """
    body = ("Filed as **%s** (backlog, `provenance:agent`). This finding was requested by "
            "the session; the Stage E finding poller created the ticket." % filed_id)
    linear_graphql(_M_COMMENT, {"issueId": source_uuid, "body": body}, api_key)


# --------------------------------------------------------------------------- #
# The pass
# --------------------------------------------------------------------------- #
def run_scan(cfg, api_key, dry_run, deadline):
    """One pass. Returns (filed, already_filed, left_behind, asked).

    The two kinds of "not filed" are counted apart on purpose: `already_filed` is work that
    is DONE, `left_behind` is work still owed. One number for both would report a pass that
    hit its caps and a pass with nothing new to do as the same quiet green.

    `asked` is the §13 record of what this pass PUT to the tracker — one line per team,
    naming the window and what came back. A pass that files nothing is the common, correct,
    green outcome, and it is also what a broken pass looks like; the difference is that
    this one can say which question it asked and what the answer was.
    """
    state_dir = cfg["state_dir"]
    seen = load_seen(seen_path(state_dir))
    watermarks = load_watermarks(watermark_path(state_dir))
    scan_started = _now_iso()

    candidates, asked, complete = [], [], {}
    for key in cfg["teams"]:
        if time.time() > deadline:
            raise PollerError("timed out during the scan, before team %r was asked at all; "
                              "no watermark moved, so the next pass asks the same windows"
                              % key)
        since = since_for_team(watermarks.get(key), cfg["lookback_hours"],
                               cfg["watermark_overlap_minutes"], scan_started)
        found, done = scan_team(cfg, key, api_key, since)
        complete[key] = done
        candidates.extend(found)
        asked.append("%s: comments created after %s → %d finding(s)%s"
                     % (key, since, len(found), "" if done else "; PAGE BOUND HIT"))
        if not done:
            log("WARNING: team %s has more comments after %s than one pass reads (%d pages "
                "of %d). Its watermark stays put and the next pass asks the same window, so "
                "nothing is lost — but if this repeats, shorten the interval."
                % (key, since, DEFAULT_MAX_PAGES, DEFAULT_COMMENT_PAGE))

    to_file, skipped = select_to_file(candidates, seen, cfg["max_per_source"],
                                      cfg["max_per_run"])
    left_behind, already = [], 0   # left_behind is examined-but-NOT-resolved: it holds a
    for cand, why in skipped:      # team's watermark back until a later pass resolves it
        if why == "already-filed":
            already += 1
            continue
        left_behind.append(cand)
        log("left for the next pass (%s): comment %s on %s"
            % (why, cand["comment_id"], cand["source_id"]))

    filed = 0
    for idx, cand in enumerate(to_file):
        if time.time() > deadline:
            log("timed out mid-file: %d filed, %d left for the next pass"
                % (filed, len(to_file) - idx))
            left_behind.extend(to_file[idx:])
            break
        if dry_run:
            log("DRY-RUN would file: %r (from %s)" % (cand["title"][:80], cand["source_id"]))
            filed += 1
            continue
        filed_id = file_finding(cfg, cand, api_key)
        # THE SEEN-SET IS THE DEDUP AUTHORITY, so it is written through the moment the
        # ticket exists — BEFORE the receipt. The other order (receipt, then seen-set) put
        # a second failure between creating a ticket and remembering it, and a receipt that
        # failed to post cost the dedup record and filed the finding twice on the next pass.
        seen[cand["comment_id"]] = {"filed": filed_id, "source": cand["source_id"],
                                    "at": _now_iso()}
        save_seen(seen_path(state_dir), seen)
        log("filed %s from %s" % (filed_id, cand["source_id"]))
        filed += 1
        try:
            post_receipt(cand["source_uuid"], filed_id, api_key)
        except PollerError as exc:
            log("NOTE: %s was filed and recorded, but its receipt comment could not be "
                "posted (%s). The receipt is a record for a person, not the dedup guard, "
                "so the next pass will not file it again." % (filed_id, exc))

    if not dry_run and advance_watermarks(watermarks, cfg["teams"], complete,
                                          left_behind, scan_started):
        save_watermarks(watermark_path(state_dir), watermarks)
    return filed, already, len(left_behind), asked


def cmd_scan(args):
    """Every exit from here writes a heartbeat — including the ones that never got a
    config. A run that dies before it learns where `state_dir` is leaves a STALE heartbeat,
    which is the symptom of a daemon that was never started, and those two have opposite
    remedies (§13). So the state dir is worked out first, best effort, and a failure is
    recorded there as `result: usage` with its error text."""
    started_at, started_mono = _now_iso(), time.monotonic()
    hint = state_dir_hint(args.config)
    try:
        cfg = load_config(args.config)
        api_key = credential(cfg)
    except PollerError as exc:
        # The config IS the failure, so `hint` — not cfg["state_dir"] — is where this goes.
        write_heartbeat(hint, EXIT_USAGE, started_at, started_mono, args.dry_run,
                        error=str(exc))
        log("COULD NOT START — nothing was scanned and nothing was filed: %s" % exc)
        return EXIT_USAGE

    deadline = time.time() + (args.timeout or DEFAULT_RUN_TIMEOUT_SECONDS)
    try:
        filed, already, left, asked = run_scan(cfg, api_key, args.dry_run, deadline)
    except PollerError as exc:
        write_heartbeat(cfg["state_dir"], EXIT_ERROR, started_at, started_mono,
                        args.dry_run, error=str(exc))
        log("COULD NOT COMPLETE THE PASS: %s" % exc)
        return EXIT_ERROR
    except Exception as exc:                                          # noqa: BLE001
        # A bug outside every per-item catcher. Recorded, not swallowed: it still exits
        # non-zero, and the heartbeat says the run RAN and failed, so nobody reads a crash
        # as a dead daemon.
        write_heartbeat(cfg["state_dir"], EXIT_ERROR, started_at, started_mono,
                        args.dry_run, error="unexpected error: %s" % exc)
        log("COULD NOT COMPLETE THE PASS — this is a bug in this file, not a condition it "
            "handles. Traceback:\n%s" % traceback.format_exc())
        return EXIT_ERROR

    write_heartbeat(cfg["state_dir"], EXIT_OK, started_at, started_mono, args.dry_run,
                    filed=filed, skipped=already + left)
    # NOTHING TO DO SAYS WHAT IT ASKED AND WHAT CAME BACK. A bare "0 filed" is an
    # unlabelled green: identical to a pass that asked nothing, or asked the wrong
    # question — which is exactly how a lookback keyed on the wrong timestamp stayed
    # invisible for ~550 passes (§13).
    log("pass complete%s: asked %d team(s) — %s; %s %d, already filed %d, left for the "
        "next pass %d"
        % (" (dry-run: nothing was written, not even the heartbeat)" if args.dry_run else "",
           len(cfg["teams"]), "; ".join(asked) or "no teams configured",
           "WOULD file" if args.dry_run else "filed", filed, already, left))
    return EXIT_OK


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

    # select_to_file — dedup + both caps (unchanged by the lookback fix: the caps are the
    # flood guard, and a fix that quietly widened them would be a different bug)
    cands = [{"comment_id": "c%d" % i, "source_id": "KIT-1", "title": "t", "body": "b",
              "team_key": "KIT", "created_at": "2026-09-10T0%d:00:00Z" % i}
             for i in range(5)]
    tf, sk = select_to_file(cands, {"c0": {}}, max_per_source=3, max_per_run=20)
    ok("select: already-seen skipped", all(c["comment_id"] != "c0" for c in tf))
    ok("select: max_per_source caps one source at 3", len(tf) == 3)
    ok("select: the rest are left, not dropped", len(sk) == 2)  # c0 seen + one over-cap
    ok("select: a skip carries the CANDIDATE, so a cap can hold the watermark back",
       all(isinstance(c, dict) and "created_at" in c for c, _why in sk))
    ok("select: the two skip reasons are told apart",
       sorted(why for _c, why in sk) == ["already-filed", "over max_per_source"])
    many = [{"comment_id": "d%d" % i, "source_id": "KIT-%d" % i, "title": "t", "body": "b",
             "team_key": "KIT", "created_at": "2026-09-10T00:%02d:00Z" % i}
            for i in range(30)]
    tf2, _ = select_to_file(many, {}, max_per_source=3, max_per_run=20)
    ok("select: max_per_run caps the whole pass", len(tf2) == 20)

    # ---- the window: a comment's createdAt, never the ticket's updatedAt --------------
    ok("query: the scan asks the COMMENTS endpoint", "comments(filter:" in _Q_COMMENTS)
    ok("query: bounded by the comment's own createdAt", "createdAt: {gt: $since}" in _Q_COMMENTS)
    ok("query: nothing in the scan is keyed on updatedAt", "updatedAt" not in _Q_COMMENTS)
    ok("query: the comment's createdAt is read back (the watermark needs it)",
       "createdAt" in _Q_COMMENTS and "issue { id identifier }" in _Q_COMMENTS)

    now = datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc)
    ok("since: no watermark → the cold-start window",
       since_for_team(None, 72, 15, _iso(now)) == _iso(now - timedelta(hours=72)))
    ok("since: a watermark → from it, less the overlap",
       since_for_team(_iso(now - timedelta(hours=1)), 72, 15, _iso(now))
       == _iso(now - timedelta(hours=1, minutes=15)))
    ok("since: a WEEK-OLD watermark asks for the week, not the lookback — an outage must "
       "not eat the findings written during it",
       _parse_iso(since_for_team(_iso(now - timedelta(days=7)), 72, 15, _iso(now)))
       < now - timedelta(hours=72))
    ok("since: a corrupt watermark degrades to the cold-start window",
       since_for_team("not-a-date", 72, 15, _iso(now)) == _iso(now - timedelta(hours=72)))
    ok("parse: Linear's fractional seconds compare correctly (not as strings)",
       _parse_iso("2026-09-13T12:00:06.123Z") > _parse_iso("2026-09-13T12:00:06Z"))

    started = _iso(now)
    ok("watermark: a clean pass moves to the moment the scan STARTED (never 'now', which "
       "would step over anything written while the pass ran)",
       next_watermark(None, started, None, True) == _iso(now - timedelta(seconds=1)))
    left_at = _iso(now - timedelta(hours=2))
    ok("watermark: stops SHORT of the oldest thing a cap left behind",
       _parse_iso(next_watermark(None, started, left_at, True)) < _parse_iso(left_at))
    ok("watermark: an incomplete scan does not move it at all",
       next_watermark("2026-09-01T00:00:00Z", started, None, False) == "2026-09-01T00:00:00Z")
    ok("watermark: never moves backwards",
       next_watermark(_iso(now + timedelta(days=1)), started, None, True)
       == _iso(now + timedelta(days=1)))
    marks = {}
    capped = {"team_key": "KIT", "created_at": left_at}
    ok("watermark: a capped finding holds ITS team back, and only that team",
       advance_watermarks(marks, ["KIT", "TOD"], {"KIT": True, "TOD": True},
                          [capped], started)
       and _parse_iso(marks["KIT"]) < _parse_iso(marks["TOD"]))
    ok("watermark: the held-back team's next window re-asks for the capped finding",
       _parse_iso(since_for_team(marks["KIT"], 72, 15, started)) < _parse_iso(left_at))

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

    # ---- a whole pass, offline: every Linear call answered by the fake ----------------
    #
    # THE ONE CASE THAT MATTERS MOST is the first: a finding written an hour ago on a
    # ticket nobody has touched for a month. Under the old issue-`updatedAt` window that
    # ticket was outside the 72-hour lookback, so its finding was never read — and because
    # a pass that reads nothing files nothing and exits 0, ~550 passes reported success
    # while losing every finding of that shape.
    stale_ticket_hours = 24 * 30
    fresh = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
    finding_comment = {
        "id": "cmt-1", "createdAt": fresh,
        "user": {"id": "u-agent", "name": "agent"},
        # The ticket itself has not been touched in a month. It is carried here only so the
        # assertion below can say what the scan did NOT ask about.
        "issue": {"id": "iss-7", "identifier": "KIT-7",
                  "updatedAt": _iso(datetime.now(timezone.utc)
                                    - timedelta(hours=stale_ticket_hours))},
        "body": "```json\n%s\n```" % json.dumps(
            {"schema": SCHEMA, "title": "stale id in a workflow", "body": "line 5 is dead"}),
    }
    noise = [
        # someone else's comment carrying a perfectly well-formed finding: never trusted
        dict(finding_comment, id="cmt-2", user={"id": "u-outsider", "name": "outsider"}),
        # the agent's own ordinary comment, and a malformed block: neither is a candidate
        dict(finding_comment, id="cmt-3", body="just a plan comment"),
        dict(finding_comment, id="cmt-4", body="```json\n{\"schema\": \"%s\", oops\n```" % SCHEMA),
    ]

    class _FakeLinear:
        """Answers this file's GraphQL documents and records every write and every ask."""

        def __init__(self, comments):
            self.comments, self.created, self.receipts, self.asked = comments, [], [], []

        def __call__(self, query, variables, api_key):
            self.asked.append((query, variables))
            if "teams(filter:" in query:
                return {"teams": {"nodes": [{
                    "id": "team-1", "key": variables["key"],
                    "states": {"nodes": [{"id": "st-backlog", "name": "Backlog",
                                          "type": "backlog"}]},
                    "labels": {"nodes": [{"id": "lbl-prov", "name": AGENT_PROVENANCE}]}}]}}
            if "comments(filter:" in query:
                since = _parse_iso(variables["since"])
                nodes = [c for c in self.comments if _parse_iso(c["createdAt"]) > since]
                return {"comments": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                                     "nodes": nodes}}
            if "issueCreate" in query:
                self.created.append(variables["input"])
                ident = "KIT-%d" % (900 + len(self.created))
                return {"issueCreate": {"success": True,
                                        "issue": {"id": "new-1", "identifier": ident,
                                                  "url": "https://example.invalid/%s" % ident}}}
            if "commentCreate" in query:
                self.receipts.append(variables)
                return {"commentCreate": {"success": True}}
            raise AssertionError("the fake was asked an unexpected query: %r" % query[:80])

    def _pass(fake, config_path, argv_extra=()):
        """Run one real `scan` with the fake standing in for Linear."""
        real = globals()["linear_graphql"]
        globals()["linear_graphql"] = fake
        try:
            return main(["scan", "--config", config_path] + list(argv_extra))
        finally:
            globals()["linear_graphql"] = real

    key_env = "STAGE_E_FINDING_POLLER_SELFTEST_KEY"
    os.environ[key_env] = "not-a-real-key"
    with tempfile.TemporaryDirectory() as d:
        state = os.path.join(d, "state")
        cfg_path = os.path.join(d, "poller.json")
        base = {"teams": ["KIT"], "owner_user_id": "u-owner", "agent_user_id": "u-agent",
                "linear_key_env": key_env, "state_dir": state}
        json.dump(base, open(cfg_path, "w"))

        # 1. A FINDING ON A LONG-UNTOUCHED TICKET IS FILED.
        fake = _FakeLinear([finding_comment] + noise)
        rc = _pass(fake, cfg_path)
        ok("pass: a finding on a month-old ticket is FILED (the window is the comment's)",
           rc == EXIT_OK and len(fake.created) == 1)
        ok("pass: the scan never asked the issues endpoint about updated tickets",
           not any("issues(" in q for q, _v in fake.asked))
        ok("pass: it asked for comments created after a real instant",
           any("comments(filter:" in q and _parse_iso(v.get("since")) for q, v in fake.asked))
        if fake.created:
            inp = fake.created[0]
            ok("pass: filed into the backlog state, marked provenance:agent, owner "
               "subscribed, nobody assigned",
               inp["stateId"] == "st-backlog" and inp["labelIds"] == ["lbl-prov"]
               and inp["subscriberIds"] == ["u-owner"] and "assigneeId" not in inp)
            ok("pass: the source ticket travels in the body", "KIT-7" in inp["description"])
        ok("pass: a finding from anyone but the agent user is never filed",
           len(fake.created) == 1)
        ok("pass: the receipt is posted on the SOURCE ticket",
           len(fake.receipts) == 1 and fake.receipts[0].get("issueId") == "iss-7")
        seen_now = load_seen(seen_path(state))
        ok("pass: the seen-set records the source COMMENT id", "cmt-1" in seen_now)
        ok("pass: the watermark advanced for the team that was scanned",
           bool(load_watermarks(watermark_path(state)).get("KIT")))
        hb = json.load(open(heartbeat_path(state)))
        ok("heartbeat: a good pass records ok/0 in the review poller's shape",
           (hb["schema"], hb["command"], hb["result"], hb["exit_code"])
           == (HEARTBEAT_SCHEMA, "scan", "ok", EXIT_OK))
        ok("heartbeat: it counts what was filed", hb["filed"] == 1 and hb["error"] is None)
        ok("heartbeat: it timestamps both ends of the run",
           bool(hb["started_at"] and hb["ended_at"]) and isinstance(hb["duration_seconds"], float))

        # 2. DEDUP IS THE SEEN-SET — proven by removing everything else. The watermark is
        #    deleted, so the window re-offers the same comment, and the receipt backstop is
        #    gone (it matched the agent user, while the receipt is written by the poller's
        #    own key, so it never matched anything on a real deployment).
        os.remove(watermark_path(state))
        fake2 = _FakeLinear([finding_comment] + noise)
        rc2 = _pass(fake2, cfg_path)
        ok("dedup: the same comment, offered again, is NOT filed twice",
           rc2 == EXIT_OK and fake2.created == [] and fake2.receipts == [])
        ok("dedup: and it was the seen-set that said so — the pass read the comment again",
           any("comments(filter:" in q for q, _v in fake2.asked))
        ok("dedup: no receipt lookup is made (the inert backstop is gone)",
           "already_filed_reply" not in globals()
           and not any("issue(id:" in q for q, _v in fake2.asked))
        hb2 = json.load(open(heartbeat_path(state)))
        ok("heartbeat: a pass with nothing new to do is ok, and says nothing was filed",
           hb2["result"] == "ok" and hb2["filed"] == 0 and hb2["skipped"] == 1)

        # 3. --dry-run writes NOTHING, heartbeat included: an operator's dry run must not
        #    freshen the liveness file they are about to read as proof of life.
        before = open(heartbeat_path(state), encoding="utf-8").read()
        marks_before = open(watermark_path(state), encoding="utf-8").read()
        seen_before = open(seen_path(state), encoding="utf-8").read()
        # An UNSEEN finding, inside the window, so the dry run genuinely reaches the point
        # of filing and stops there — a case that could not reach it would prove nothing.
        fake3 = _FakeLinear([dict(finding_comment, id="cmt-9",
                                  createdAt=_iso(datetime.now(timezone.utc)))])
        rc3 = _pass(fake3, cfg_path, ["--dry-run"])
        ok("dry-run: a finding it WOULD file is reported and not created",
           rc3 == EXIT_OK and fake3.created == [] and fake3.receipts == []
           and any("comments(filter:" in q for q, _v in fake3.asked))
        ok("dry-run: leaves the heartbeat untouched (it must not forge proof of life)",
           open(heartbeat_path(state), encoding="utf-8").read() == before)
        ok("dry-run: moves no watermark and records nothing as seen, so the next real pass "
           "still files it",
           open(watermark_path(state), encoding="utf-8").read() == marks_before
           and open(seen_path(state), encoding="utf-8").read() == seen_before)

    # 4. THE ERROR HEARTBEAT. A config or credential failure used to exit before the
    #    heartbeat was written, leaving a stale timestamp — the symptom of a daemon that
    #    never started, whose remedy is the opposite one (§13).
    with tempfile.TemporaryDirectory() as d:
        state = os.path.join(d, "state")
        bad = os.path.join(d, "bad.json")
        json.dump({"teams": [], "owner_user_id": "O", "agent_user_id": "A",
                   "state_dir": state}, open(bad, "w"))
        rc = main(["scan", "--config", bad])
        ok("error heartbeat: an invalid config exits usage, not success", rc == EXIT_USAGE)
        hbe = json.load(open(heartbeat_path(state)))
        ok("error heartbeat: it is written, fresh, and says the run RAN and could not act",
           (hbe["result"], hbe["exit_code"], hbe["command"]) == ("usage", EXIT_USAGE, "scan"))
        ok("error heartbeat: it carries the reason a person has to act on",
           isinstance(hbe["error"], str) and "teams" in hbe["error"])
        ok("error heartbeat: it never claims work it did not do",
           hbe["filed"] == 0 and hbe["skipped"] == 0)

        missing_key = os.path.join(d, "nokey.json")
        state2 = os.path.join(d, "state2")
        json.dump({"teams": ["KIT"], "owner_user_id": "O", "agent_user_id": "A",
                   "state_dir": state2,
                   "linear_key_env": "STAGE_E_NO_SUCH_VAR_FOR_SELFTEST"}, open(missing_key, "w"))
        rc = main(["scan", "--config", missing_key])
        hbk = json.load(open(heartbeat_path(state2)))
        ok("error heartbeat: a MISSING CREDENTIAL is recorded the same way, not silently",
           rc == EXIT_USAGE and hbk["result"] == "usage"
           and "STAGE_E_NO_SUCH_VAR_FOR_SELFTEST" in hbk["error"])
        ok("error heartbeat: the reason names the env var, never a value",
           "not-a-real-key" not in json.dumps(hbk))

        # The hint is what makes the two cases above possible, and it must never fail: a
        # config that cannot be parsed at all still has to yield somewhere to write.
        ok("hint: state_dir is taken from the config when the file is merely INVALID",
           state_dir_hint(bad) == os.path.realpath(state))
        unreadable = os.path.join(d, "not-json.json")
        open(unreadable, "w").write("{ this is not json")
        ok("hint: an unparsable config falls back to the installed default",
           state_dir_hint(unreadable) == _resolve_state_dir(None))
        ok("hint: a config that does not exist falls back too, without raising",
           state_dir_hint(os.path.join(d, "nope.json")) == _resolve_state_dir(None))

    os.environ.pop(key_env, None)

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
        return EXIT_OK
    if args.command == "scan":
        if not args.config:
            print("scan needs --config", file=sys.stderr)
            return EXIT_USAGE
        return cmd_scan(args)
    ap.print_help()
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
