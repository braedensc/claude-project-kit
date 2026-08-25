#!/usr/bin/env python3
"""Telemetry scrape — ticket comments → Postgres tables.

Deterministic, stdlib-only for everything except the database driver. Reads the
`pipeline-telemetry/1` blocks that `/work` and `/ship` post as ticket comments
(contract §4) and appends them to the tables §10 defines: `runs`,
`ticket_events`, and the optional `review_findings`.

THE SWEEP NEVER CRASHES. This is the design constraint that shapes the whole
file. A collector that dies on the first malformed block stops collecting
everything behind it, and the blocks most worth having — the ones from runs that
ended badly — are exactly the ones most likely to be malformed. So every layer
is independently fault-isolated: a comment that is not JSON, a fence that is not
telemetry, a row missing a required field, a timestamp in the wrong shape. Each
is counted, named in the log, and stepped over. The process exits non-zero only
when it could not reach the store at all.

IDEMPOTENT BY NATURAL KEY, NOT BY BOOKKEEPING. Re-running the sweep over the
same window must not double-count, and it must not need a "last scraped" cursor
to avoid it — a cursor is state that can be lost, skipped past, or rewound.
Instead each table has a key the contract already made stable:

    runs             `run_id`               §4: "stable across re-posts — it is
                                            the idempotency key"
    ticket_events    (ticket_id, event, at) §4: "the same (ticket_id, event, at)
                                            posted twice is one event"
    review_findings  a digest of the finding's own content

So the sweep is safe to run on a schedule, by hand, twice in a row, or over an
overlapping window. `runs` upserts (a re-post may carry a corrected `ended_at`);
the other two do nothing on conflict, because an event and a finding are facts,
not records that get amended.

REPORTING, NOT AUTHORITY (§4). Everything here was authored by a session. It
feeds dashboards and the weekly review; nothing read out of these tables may
gate a budget, an approval, or a merge. `budgets.dailyUsd` is metered against
the dispatcher's own ledger (§9), never against `runs.cost_usd`.

CONFIG. `delivery.json` → `telemetry` (§10). Absent ⇒ the scrape is OFF: exit 0,
emit nothing, same discriminator discipline as §2. **The DSN itself is never in
the config** — `dsnEnv` names an environment variable and the value is read from
there, so a connection string with a password in it can never land in a tracked
file (the kit's "reference a secret by name, never by value" rule).

Usage:
    telemetry_scrape.py --config PATH [--from-json PATH] [--from-file PATH ...]
                        [--since ISO] [--init] [--dry-run] [--json]
    telemetry_scrape.py --selftest

Input sources, highest precedence first:
    --from-json  a JSON array of {ticket_id, comment_id, body, created_at}
    --from-file  files whose contents are comment bodies (one comment per file)
    (default)    Linear, via LINEAR_API_KEY, over the configured lookback window

Exit: 0 = swept (skips are normal), 1 = the store was unreachable, 2 = usage.
"""
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

TELEMETRY_SCHEMA = "pipeline-telemetry/1"
SUPPORTED_VERSION = 1

# §4 enums. An unrecognized value is a skipped row, never a coerced one.
STAGES = ("epic", "dev", "review", "bounce", "triage", "diagnosis", "retro")
OUTCOMES = ("completed", "blocked", "timeout", "capacity", "error", "budget")
# §4: `error_class` is null unless the outcome is one of these.
ERROR_OUTCOMES = ("blocked", "error", "timeout", "capacity", "budget")
AUTH_MODES = ("subscription", "api-key")
EVENTS = ("created", "approved", "dispatched", "first_commit", "pr_opened",
          "ci_green", "review_posted", "bounce_started", "merged", "deployed",
          "reverted")
ACTORS = ("human", "agent", "system")
SEVERITIES = ("low", "medium", "high", "critical")

# §4: "the session_mode → allowed stage" cross-check. Only evaluable when the
# emitter included the optional `session_mode` field; when it did not, the row is
# accepted and the check is simply not performed (it is a contract-conformance
# flag, not a data-integrity one).
MODE_STAGES = {
    "ticket": ("dev", "bounce"),
    "planning": ("epic", "triage"),
    "diagnosis": ("diagnosis",),
    "maintenance": ("review", "retro"),
}

# A fenced ```json block. The fence info string is plain `json` so it renders
# everywhere (§4) — the `schema` key is the marker, so every fence is parsed and
# the non-telemetry ones are dropped by key, not by fence label.
FENCE_RE = re.compile(r"^[ \t]*```[ \t]*json[ \t]*$(.*?)^[ \t]*```[ \t]*$",
                      re.MULTILINE | re.DOTALL)
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


# --------------------------------------------------------------------------- #
# Table definitions — §10. One place, so the DDL and the inserts cannot drift.
# --------------------------------------------------------------------------- #
DDL = """
CREATE SCHEMA IF NOT EXISTS {s};

CREATE TABLE IF NOT EXISTS {s}.runs (
    run_id              text PRIMARY KEY,
    dispatch_id         text,
    ticket_id           text,
    team_key            text,
    stage               text NOT NULL,
    session_mode        text,
    model               text,
    auth_mode           text,
    started_at          timestamptz,
    ended_at            timestamptz,
    tokens_in           bigint NOT NULL DEFAULT 0,
    tokens_out          bigint NOT NULL DEFAULT 0,
    tokens_cache_read   bigint NOT NULL DEFAULT 0,
    tokens_cache_write  bigint NOT NULL DEFAULT 0,
    cost_usd            numeric(12,4) NOT NULL DEFAULT 0,
    turns               integer NOT NULL DEFAULT 0,
    outcome             text NOT NULL,
    error_class         text,
    files_changed       integer NOT NULL DEFAULT 0,
    lines_added         integer NOT NULL DEFAULT 0,
    lines_removed       integer NOT NULL DEFAULT 0,
    pr_number           integer,
    source_comment_id   text,
    ingested_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS runs_ticket_idx  ON {s}.runs (ticket_id);
CREATE INDEX IF NOT EXISTS runs_started_idx ON {s}.runs (started_at);

CREATE TABLE IF NOT EXISTS {s}.ticket_events (
    ticket_id           text NOT NULL,
    event               text NOT NULL,
    at                  timestamptz NOT NULL,
    actor               text,
    source_comment_id   text,
    ingested_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ticket_id, event, at)
);
CREATE INDEX IF NOT EXISTS ticket_events_at_idx ON {s}.ticket_events (at);

CREATE TABLE IF NOT EXISTS {s}.review_findings (
    finding_id          text PRIMARY KEY,
    ticket_id           text,
    pr_number           integer,
    severity            text NOT NULL,
    category            text,
    file                text,
    line                integer,
    summary             text,
    at                  timestamptz,
    source_comment_id   text,
    ingested_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS review_findings_cat_idx ON {s}.review_findings (category);
"""

RUN_COLUMNS = (
    "run_id", "dispatch_id", "ticket_id", "team_key", "stage", "session_mode",
    "model", "auth_mode", "started_at", "ended_at", "tokens_in", "tokens_out",
    "tokens_cache_read", "tokens_cache_write", "cost_usd", "turns", "outcome",
    "error_class", "files_changed", "lines_added", "lines_removed", "pr_number",
    "source_comment_id",
)
EVENT_COLUMNS = ("ticket_id", "event", "at", "actor", "source_comment_id")
FINDING_COLUMNS = (
    "finding_id", "ticket_id", "pr_number", "severity", "category", "file",
    "line", "summary", "at", "source_comment_id",
)


def upsert_sql(schema, table, columns, conflict, update):
    cols = ", ".join(columns)
    marks = ", ".join(["%s"] * len(columns))
    if update:
        sets = ", ".join(
            "%s = EXCLUDED.%s" % (c, c) for c in columns if c not in conflict
        )
        action = "DO UPDATE SET %s, ingested_at = now()" % sets
    else:
        action = "DO NOTHING"
    return "INSERT INTO %s.%s (%s) VALUES (%s) ON CONFLICT (%s) %s" % (
        schema, table, cols, marks, ", ".join(conflict), action,
    )


# --------------------------------------------------------------------------- #
# Row validation — §4
# --------------------------------------------------------------------------- #
class Skipped(Exception):
    """One row is unusable. Named, counted, stepped over — never fatal."""


def need_str(row, key, required=True, allowed=None):
    value = row.get(key)
    if value is None or value == "":
        if required:
            raise Skipped("%s is missing" % key)
        return None
    if not isinstance(value, str):
        raise Skipped("%s is %s, expected a string" % (key, type(value).__name__))
    if allowed and value not in allowed:
        raise Skipped("%s is %r, outside %s" % (key, value, "|".join(allowed)))
    return value


def need_int(row, key, required=False, default=0, allow_null=False):
    value = row.get(key)
    if value is None:
        if allow_null:
            return None
        if required:
            raise Skipped("%s is missing" % key)
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise Skipped("%s is %r, expected an integer" % (key, value))
    if value < 0:
        raise Skipped("%s is %d — §4 counters are non-negative" % (key, value))
    return value


def need_ts(row, key, required=True):
    value = row.get(key)
    if value is None or value == "":
        if required:
            raise Skipped("%s is missing" % key)
        return None
    if not isinstance(value, str) or not ISO_RE.match(value):
        raise Skipped("%s is %r — §4 requires ISO-8601 UTC with a trailing Z" % (key, value))
    return value


def parse_run(row, comment_id, flags):
    if not isinstance(row, dict):
        raise Skipped("runs entry is %s, expected an object" % type(row).__name__)
    stage = need_str(row, "stage", allowed=STAGES)
    outcome = need_str(row, "outcome", allowed=OUTCOMES)
    error_class = need_str(row, "error_class", required=False)
    if error_class and outcome not in ERROR_OUTCOMES:
        # §4: null unless the outcome is one of the five. Recorded rather than
        # dropped — the row's numbers are still true — but flagged, because it
        # means an emitter is not following the contract.
        flags.append("run %s: error_class %r with outcome %r (§4 allows it only for %s)"
                     % (row.get("run_id"), error_class, outcome, "|".join(ERROR_OUTCOMES)))
    mode = need_str(row, "session_mode", required=False, allowed=tuple(MODE_STAGES))
    if mode and stage not in MODE_STAGES[mode]:
        flags.append("run %s: stage %r is outside session_mode %r (§4 allows %s)"
                     % (row.get("run_id"), stage, mode, "|".join(MODE_STAGES[mode])))
    cost = row.get("cost_usd", 0)
    if isinstance(cost, bool) or not isinstance(cost, (int, float)):
        raise Skipped("cost_usd is %r, expected a number" % (cost,))
    if cost < 0:
        raise Skipped("cost_usd is %r — spend is non-negative" % (cost,))
    return (
        need_str(row, "run_id"),
        need_str(row, "dispatch_id", required=False),
        need_str(row, "ticket_id", required=False),
        need_str(row, "team_key", required=False),
        stage,
        mode,
        need_str(row, "model", required=False),
        need_str(row, "auth_mode", required=False, allowed=AUTH_MODES),
        need_ts(row, "started_at", required=False),
        need_ts(row, "ended_at", required=False),
        need_int(row, "tokens_in"),
        need_int(row, "tokens_out"),
        need_int(row, "tokens_cache_read"),
        need_int(row, "tokens_cache_write"),
        round(float(cost), 4),
        need_int(row, "turns"),
        outcome,
        error_class,
        need_int(row, "files_changed"),
        need_int(row, "lines_added"),
        need_int(row, "lines_removed"),
        need_int(row, "pr_number", allow_null=True),
        comment_id,
    )


def parse_event(row, comment_id):
    if not isinstance(row, dict):
        raise Skipped("ticket_events entry is %s, expected an object" % type(row).__name__)
    event = need_str(row, "event", allowed=EVENTS)
    actor = need_str(row, "actor", required=False, allowed=ACTORS)
    if event == "merged" and actor == "agent":
        # §4: "`merged` is always `human` or `system` — never `agent`." A session
        # claiming its own merge is the one telemetry value worth refusing
        # outright, because believing it would corrupt every autonomy metric the
        # dashboard computes.
        raise Skipped("event `merged` with actor `agent` — §4 forbids it; no agent merges")
    return (need_str(row, "ticket_id"), event, need_ts(row, "at"), actor, comment_id)


def finding_digest(ticket_id, pr_number, row):
    """A stable identity for a finding, since §4 gives findings no ID.

    Content-addressed so the same finding re-posted in a re-run collapses onto
    one row, and two genuinely different findings on the same line do not.
    """
    material = "|".join(str(x) for x in (
        ticket_id, pr_number, row.get("severity"), row.get("category"),
        row.get("file"), row.get("line"), row.get("summary"),
    ))
    return "f_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def parse_finding(row, ticket_id, pr_number, at, comment_id):
    if not isinstance(row, dict):
        raise Skipped("review_findings entry is %s, expected an object" % type(row).__name__)
    severity = need_str(row, "severity", allowed=SEVERITIES)
    return (
        finding_digest(ticket_id, pr_number, row),
        row.get("ticket_id") or ticket_id,
        need_int(row, "pr_number", allow_null=True) if "pr_number" in row else pr_number,
        severity,
        need_str(row, "category", required=False),
        need_str(row, "file", required=False),
        need_int(row, "line", allow_null=True),
        (str(row.get("summary")) if row.get("summary") is not None else None),
        need_ts(row, "at", required=False) or at,
        comment_id,
    )


# --------------------------------------------------------------------------- #
# Comment → blocks
# --------------------------------------------------------------------------- #
def telemetry_blocks(body):
    """Every `pipeline-telemetry/1` object in one comment body.

    §4 says one telemetry block per comment, but a *reader* that assumed it
    would silently drop the second one; counting them is how a double-post gets
    noticed. Every ```json fence is parsed and kept only if its `schema` marks
    it, so an unrelated JSON sample in the same comment is ignored rather than
    mistaken for telemetry.
    """
    blocks, malformed = [], 0
    for match in FENCE_RE.finditer(body or ""):
        try:
            doc = json.loads(match.group(1))
        except ValueError:
            malformed += 1
            continue
        if isinstance(doc, dict) and doc.get("schema") == TELEMETRY_SCHEMA:
            blocks.append(doc)
    return blocks, malformed


# --------------------------------------------------------------------------- #
# Sinks
# --------------------------------------------------------------------------- #
class DrySink:
    """Collects statements instead of executing them. Used by --dry-run and the
    selftest, so the full parse → SQL path is exercised with no database."""

    def __init__(self, schema):
        self.schema = schema
        self.statements = []
        self.rows = {"runs": [], "ticket_events": [], "review_findings": []}

    def init(self):
        self.statements.append(("DDL", None))

    def write(self, table, columns, values, conflict, update):
        self.statements.append((upsert_sql(self.schema, table, columns, conflict, update), values))
        self.rows[table].append(values)

    def commit(self):
        pass

    def close(self):
        pass


class PostgresSink:
    def __init__(self, dsn, schema):
        self.schema = schema
        self._conn = _connect(dsn)
        self._cur = self._conn.cursor()

    def init(self):
        self._cur.execute(DDL.format(s=self.schema))

    def write(self, table, columns, values, conflict, update):
        self._cur.execute(upsert_sql(self.schema, table, columns, conflict, update), values)

    def commit(self):
        self._conn.commit()

    def close(self):
        try:
            self._cur.close()
            self._conn.close()
        except Exception:
            pass


def _connect(dsn):
    """psycopg 3 preferred, psycopg2 accepted. Both speak the same DB-API here."""
    try:
        import psycopg  # noqa: F401

        return psycopg.connect(dsn)
    except ImportError:
        pass
    try:
        import psycopg2

        return psycopg2.connect(dsn)
    except ImportError:
        raise RuntimeError(
            "no Postgres driver available — install `psycopg[binary]` (preferred) or "
            "`psycopg2-binary`, or run with --dry-run to see the SQL this would execute"
        )


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #
def comments_from_json(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict) and "comments" in data:
        data = data["comments"]
    if not isinstance(data, list):
        raise ValueError('expected a JSON array of comments, or {"comments": [...]}')
    return [c for c in data if isinstance(c, dict)]


def comments_from_files(paths):
    out = []
    for p in paths:
        try:
            with open(p, encoding="utf-8") as fh:
                out.append({"comment_id": os.path.basename(p), "ticket_id": None,
                            "body": fh.read(), "created_at": None})
        except OSError as e:
            print("skip: cannot read %s: %s" % (p, e))
    return out


def comments_from_linear(config, since):
    """Comments across the team's issues, newest window first.

    Kept deliberately simple: one page of issues updated since the window,
    each with its comments. The sweep is idempotent, so a missed page is
    recovered by the next run rather than by pagination bookkeeping here.
    """
    import urllib.error
    import urllib.request

    key = os.environ.get("LINEAR_API_KEY")
    if not key:
        raise RuntimeError("LINEAR_API_KEY is not set — cannot read ticket comments")
    team = ((config.get("linear") or {}).get("teamKey") or "").strip()
    query = """
    query($filter: IssueFilter, $after: String) {
      issues(filter: $filter, first: 50, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          identifier
          comments(first: 50) { nodes { id body createdAt } }
        }
      }
    }"""
    filt = {"updatedAt": {"gte": since}}
    if team:
        filt["team"] = {"key": {"eq": team}}
    out, after = [], None
    while True:
        req = urllib.request.Request(
            "https://api.linear.app/graphql",
            data=json.dumps({"query": query, "variables": {"filter": filt, "after": after}}).encode(),
            headers={"Content-Type": "application/json", "Authorization": key},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                payload = json.load(r)
        except urllib.error.HTTPError as e:
            raise RuntimeError("Linear API HTTP %s: %s" % (e.code, e.read()[:300]))
        if payload.get("errors"):
            raise RuntimeError("Linear API error: %s" % json.dumps(payload["errors"])[:300])
        issues = payload["data"]["issues"]
        for issue in issues["nodes"]:
            for c in (issue.get("comments") or {}).get("nodes") or []:
                out.append({"comment_id": c["id"], "ticket_id": issue["identifier"],
                            "body": c.get("body") or "", "created_at": c.get("createdAt")})
        if not issues["pageInfo"]["hasNextPage"]:
            break
        after = issues["pageInfo"]["endCursor"]
    return out


# --------------------------------------------------------------------------- #
# The sweep
# --------------------------------------------------------------------------- #
def sweep(comments, sink):
    stats = {
        "comments": len(comments), "blocks": 0, "malformed_json": 0,
        "runs": 0, "ticket_events": 0, "review_findings": 0, "skipped": 0,
    }
    skips, flags = [], []

    for comment in comments:
        comment_id = str(comment.get("comment_id") or comment.get("id") or "")
        ticket_hint = comment.get("ticket_id")
        created = comment.get("created_at")
        blocks, malformed = telemetry_blocks(comment.get("body"))
        stats["malformed_json"] += malformed
        if malformed:
            skips.append("comment %s: %d json fence(s) did not parse" % (comment_id, malformed))
        if len(blocks) > 1:
            flags.append("comment %s carries %d telemetry blocks — §4 allows one"
                         % (comment_id, len(blocks)))
        for block in blocks:
            stats["blocks"] += 1
            for row in block.get("runs") or []:
                try:
                    values = parse_run(row, comment_id, flags)
                except Skipped as e:
                    stats["skipped"] += 1
                    skips.append("comment %s runs row: %s" % (comment_id, e))
                    continue
                sink.write("runs", RUN_COLUMNS, values, ("run_id",), update=True)
                stats["runs"] += 1
            for row in block.get("ticket_events") or []:
                try:
                    values = parse_event(row, comment_id)
                except Skipped as e:
                    stats["skipped"] += 1
                    skips.append("comment %s ticket_events row: %s" % (comment_id, e))
                    continue
                sink.write("ticket_events", EVENT_COLUMNS, values,
                           ("ticket_id", "event", "at"), update=False)
                stats["ticket_events"] += 1
            # Optional third array (§4). A block without it is entirely normal —
            # only a review-stage run has findings to report.
            pr_number = None
            for row in block.get("runs") or []:
                if isinstance(row, dict) and isinstance(row.get("pr_number"), int):
                    pr_number = row["pr_number"]
                    break
            for row in block.get("review_findings") or []:
                try:
                    values = parse_finding(row, ticket_hint, pr_number, created, comment_id)
                except Skipped as e:
                    stats["skipped"] += 1
                    skips.append("comment %s review_findings row: %s" % (comment_id, e))
                    continue
                sink.write("review_findings", FINDING_COLUMNS, values,
                           ("finding_id",), update=False)
                stats["review_findings"] += 1

    return {"schema": "pipeline-telemetry-scrape/1", "stats": stats,
            "skipped": skips, "flags": flags}


def telemetry_config(config):
    """(settings|None, reason). Absent block ⇒ the scrape is off, not broken."""
    tele = (config or {}).get("telemetry")
    if not isinstance(tele, dict):
        return None, "delivery.json has no `telemetry` block — the scrape is off (§10)"
    store = tele.get("store", "postgres")
    if store != "postgres":
        return None, "telemetry.store is %r; this collector targets postgres only" % store
    dsn_env = tele.get("dsnEnv")
    if not isinstance(dsn_env, str) or not dsn_env.strip():
        return None, ("telemetry.dsnEnv is unset — §10 requires the NAME of an "
                      "environment variable holding the DSN (never the DSN itself)")
    schema = tele.get("schema") or "pipeline"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(schema)):
        return None, "telemetry.schema %r is not a plain SQL identifier" % schema
    lookback = tele.get("lookbackDays", 30)
    if not isinstance(lookback, int) or isinstance(lookback, bool) or lookback < 1:
        return None, "telemetry.lookbackDays is %r — expected a positive integer" % lookback
    return {"dsn_env": dsn_env.strip(), "schema": str(schema), "lookback": lookback}, None


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
GOOD_RUN = {
    "run_id": "r_01JAV8Q2S6R7X0M4KDNP3YHTZ9", "dispatch_id": "d_01JAV8Q2S6",
    "ticket_id": "ENG-123", "team_key": "ENG", "stage": "dev",
    "session_mode": "ticket", "model": "claude-sonnet-5", "auth_mode": "api-key",
    "started_at": "2026-08-24T15:04:05Z", "ended_at": "2026-08-24T15:41:22Z",
    "tokens_in": 120000, "tokens_out": 8000, "tokens_cache_read": 90000,
    "tokens_cache_write": 12000, "cost_usd": 1.2345, "turns": 22,
    "outcome": "completed", "error_class": None, "files_changed": 3,
    "lines_added": 84, "lines_removed": 12, "pr_number": 41,
}


def comment(body, cid="c1", ticket="ENG-123"):
    return {"comment_id": cid, "ticket_id": ticket, "body": body,
            "created_at": "2026-08-24T15:41:30Z"}


def block_comment(doc, cid="c1", ticket="ENG-123", prose="Run complete.\n\n"):
    return comment(prose + "```json\n" + json.dumps(doc, indent=2) + "\n```\n", cid, ticket)


def selftest():
    import copy

    failures = []
    cases = [0]

    def check(label, cond, detail=""):
        cases[0] += 1
        if not cond:
            failures.append("%s%s" % (label, (": " + detail) if detail else ""))

    def run(comments):
        sink = DrySink("pipeline")
        return sweep(comments, sink), sink

    # ── The happy path ──────────────────────────────────────────────────────
    doc = {"schema": TELEMETRY_SCHEMA, "runs": [GOOD_RUN],
           "ticket_events": [{"ticket_id": "ENG-123", "event": "pr_opened",
                              "at": "2026-08-24T15:40:00Z", "actor": "agent"}]}
    result, sink = run([block_comment(doc)])
    check("baseline sweep records one run", result["stats"]["runs"] == 1,
          json.dumps(result["stats"]))
    check("baseline sweep records one event", result["stats"]["ticket_events"] == 1)
    check("baseline sweep skips nothing", result["stats"]["skipped"] == 0,
          "; ".join(result["skipped"]))
    check("baseline sweep flags nothing", not result["flags"], "; ".join(result["flags"]))

    # ── Idempotency is by SQL, so assert the SQL ────────────────────────────
    run_sql = sink.statements[0][0]
    check("runs upsert on run_id", "ON CONFLICT (run_id) DO UPDATE" in run_sql, run_sql)
    check("runs upsert refreshes ingested_at", "ingested_at = now()" in run_sql)
    check("runs upsert does not overwrite the key",
          "run_id = EXCLUDED.run_id" not in run_sql, run_sql)
    event_sql = sink.statements[1][0]
    check("events dedupe on the natural key",
          "ON CONFLICT (ticket_id, event, at) DO NOTHING" in event_sql, event_sql)

    # Sweeping the same comment twice produces identical statements — the store
    # collapses them, and nothing here needs a cursor to know that.
    twice, sink2 = run([block_comment(doc), block_comment(doc, cid="c2")])
    check("re-posting the same block yields the same run_id twice",
          [v[0] for v in sink2.rows["runs"]] == [GOOD_RUN["run_id"]] * 2)
    check("re-posted events keep an identical natural key",
          len({(v[0], v[1], v[2]) for v in sink2.rows["ticket_events"]}) == 1)
    check("re-sweep parses both comments", twice["stats"]["comments"] == 2)

    # ── Malformed input never crashes the sweep ─────────────────────────────
    result, _ = run([comment("```json\n{not json at all\n```\n")])
    check("unparseable fence is counted, not fatal", result["stats"]["malformed_json"] == 1)
    check("unparseable fence records nothing", result["stats"]["runs"] == 0)

    result, _ = run([comment("Just a human comment with no fences at all.")])
    check("a plain comment is a no-op", result["stats"]["blocks"] == 0)

    result, _ = run([comment('```json\n{"schema": "something-else/1", "runs": [{}]}\n```')])
    check("a non-telemetry fence is ignored by schema", result["stats"]["blocks"] == 0)

    bad = copy.deepcopy(GOOD_RUN)
    del bad["run_id"]
    result, _ = run([block_comment({"schema": TELEMETRY_SCHEMA, "runs": [bad, GOOD_RUN]})])
    check("a row missing run_id is skipped", result["stats"]["skipped"] == 1,
          "; ".join(result["skipped"]))
    check("the good row beside it still lands", result["stats"]["runs"] == 1)

    for field, value, why in (
        ("stage", "gardening", "unknown stage"),
        ("outcome", "vibes", "unknown outcome"),
        ("tokens_in", -5, "negative counter"),
        ("tokens_out", "many", "non-integer counter"),
        ("cost_usd", "1.20", "non-numeric cost"),
        ("started_at", "2026-08-24 15:04:05", "timestamp without Z"),
        ("auth_mode", "oauth", "unknown auth mode"),
    ):
        bad = copy.deepcopy(GOOD_RUN)
        bad[field] = value
        result, _ = run([block_comment({"schema": TELEMETRY_SCHEMA, "runs": [bad]})])
        check("skips %s" % why, result["stats"]["skipped"] == 1 and result["stats"]["runs"] == 0,
              "; ".join(result["skipped"]) or "nothing skipped")

    result, _ = run([block_comment({"schema": TELEMETRY_SCHEMA, "runs": ["not an object"]})])
    check("skips a non-object run row", result["stats"]["skipped"] == 1)

    # ── §4 conformance flags (recorded, not dropped) ────────────────────────
    odd = copy.deepcopy(GOOD_RUN)
    odd["error_class"] = "rate_limit"  # with outcome=completed
    result, _ = run([block_comment({"schema": TELEMETRY_SCHEMA, "runs": [odd]})])
    check("error_class on a completed run is flagged", len(result["flags"]) == 1,
          "; ".join(result["flags"]))
    check("...but the row is still recorded", result["stats"]["runs"] == 1)

    odd = copy.deepcopy(GOOD_RUN)
    odd["stage"] = "retro"  # illegal for session_mode=ticket
    result, _ = run([block_comment({"schema": TELEMETRY_SCHEMA, "runs": [odd]})])
    check("stage outside session_mode is flagged", len(result["flags"]) == 1,
          "; ".join(result["flags"]))

    two = block_comment({"schema": TELEMETRY_SCHEMA, "runs": [GOOD_RUN]})
    two["body"] += "\n```json\n" + json.dumps({"schema": TELEMETRY_SCHEMA, "runs": []}) + "\n```\n"
    result, _ = run([two])
    check("a double-posted block is flagged", any("two telemetry blocks" in f or
                                                  "2 telemetry blocks" in f
                                                  for f in result["flags"]),
          "; ".join(result["flags"]))

    # ── §4: no agent ever merges ────────────────────────────────────────────
    result, _ = run([block_comment({"schema": TELEMETRY_SCHEMA, "ticket_events": [
        {"ticket_id": "ENG-123", "event": "merged", "at": "2026-08-24T16:00:00Z",
         "actor": "agent"}]})])
    check("a self-claimed agent merge is refused", result["stats"]["skipped"] == 1
          and result["stats"]["ticket_events"] == 0, "; ".join(result["skipped"]))
    result, _ = run([block_comment({"schema": TELEMETRY_SCHEMA, "ticket_events": [
        {"ticket_id": "ENG-123", "event": "merged", "at": "2026-08-24T16:00:00Z",
         "actor": "human"}]})])
    check("a human merge is recorded", result["stats"]["ticket_events"] == 1)

    # ── review_findings ─────────────────────────────────────────────────────
    doc = {"schema": TELEMETRY_SCHEMA, "runs": [dict(GOOD_RUN, stage="review",
                                                     session_mode="maintenance")],
           "review_findings": [
               {"severity": "high", "category": "security", "file": "src/x.ts",
                "line": 42, "summary": "token logged"},
               {"severity": "low", "category": "tests", "summary": "assertion is weak"}]}
    result, sink3 = run([block_comment(doc)])
    check("findings are recorded", result["stats"]["review_findings"] == 2,
          json.dumps(result["stats"]))
    check("findings inherit the run's PR number",
          sink3.rows["review_findings"][0][2] == 41)
    check("finding digests are distinct",
          len({r[0] for r in sink3.rows["review_findings"]}) == 2)
    again, sink4 = run([block_comment(doc, cid="c9")])
    check("the same finding digests identically on a re-post",
          {r[0] for r in sink3.rows["review_findings"]} ==
          {r[0] for r in sink4.rows["review_findings"]})
    result, _ = run([block_comment({"schema": TELEMETRY_SCHEMA, "review_findings": [
        {"severity": "catastrophic", "summary": "x"}]})])
    check("an unknown severity is skipped", result["stats"]["skipped"] == 1)

    # ── Config resolution (§10) ─────────────────────────────────────────────
    settings, reason = telemetry_config({"version": 1})
    check("no telemetry block means off", settings is None and "off" in reason, str(reason))
    settings, reason = telemetry_config({"telemetry": {"store": "postgres",
                                                       "dsnEnv": "PIPELINE_TELEMETRY_DSN"}})
    check("a minimal block resolves", settings is not None and settings["schema"] == "pipeline",
          str(reason))
    settings, reason = telemetry_config({"telemetry": {"store": "postgres"}})
    check("a block without dsnEnv is refused", settings is None, str(reason))
    settings, reason = telemetry_config(
        {"telemetry": {"store": "postgres", "dsnEnv": "D", "schema": "drop table x;--"}})
    check("a non-identifier schema is refused", settings is None, str(reason))
    settings, reason = telemetry_config({"telemetry": {"store": "sqlite", "dsnEnv": "D"}})
    check("a non-postgres store is refused", settings is None, str(reason))

    # ── DDL matches the column tuples ───────────────────────────────────────
    ddl = DDL.format(s="pipeline")
    for col in RUN_COLUMNS:
        check("DDL declares runs.%s" % col, re.search(r"\b%s\b" % re.escape(col), ddl) is not None)
    for col in EVENT_COLUMNS:
        check("DDL declares ticket_events.%s" % col,
              re.search(r"\b%s\b" % re.escape(col), ddl) is not None)
    for col in FINDING_COLUMNS:
        check("DDL declares review_findings.%s" % col,
              re.search(r"\b%s\b" % re.escape(col), ddl) is not None)

    if failures:
        print("FAIL: %d of %d telemetry-scrape case(s) failed:" % (len(failures), cases[0]))
        for f in failures:
            print("  - %s" % f)
        return 1
    print("OK: %d telemetry-scrape case(s) passed" % cases[0])
    return 0


def main():
    ap = argparse.ArgumentParser(add_help=True, description=__doc__.splitlines()[0])
    ap.add_argument("--config", help="path to delivery.json")
    ap.add_argument("--from-json", help="JSON array of comments (bypasses the tracker)")
    ap.add_argument("--from-file", nargs="*", default=[], help="files holding comment bodies")
    ap.add_argument("--since", help="ISO-8601 UTC lower bound (default: lookbackDays ago)")
    ap.add_argument("--init", action="store_true", help="create the tables if absent")
    ap.add_argument("--dry-run", action="store_true", help="print the SQL, touch no database")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    config_path = args.config or "delivery.json"
    if not os.path.exists(config_path):
        # §2: absent is OFF, not broken. Silence, exit 0.
        return 0
    try:
        with open(config_path, encoding="utf-8") as fh:
            config = json.load(fh)
    except (OSError, ValueError) as e:
        print("FAIL: %s is present but unreadable: %s" % (config_path, e), file=sys.stderr)
        return 2
    if config.get("version") != SUPPORTED_VERSION:
        print("FAIL: %s declares version %r; this collector implements contract version %d"
              % (config_path, config.get("version"), SUPPORTED_VERSION), file=sys.stderr)
        return 2

    settings, reason = telemetry_config(config)
    if settings is None:
        print("Telemetry collection is not configured: %s" % reason)
        return 0

    since = args.since or (
        datetime.now(timezone.utc) - timedelta(days=settings["lookback"])
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        if args.from_json:
            comments = comments_from_json(args.from_json)
        elif args.from_file:
            comments = comments_from_files(args.from_file)
        else:
            comments = comments_from_linear(config, since)
    except (OSError, ValueError, RuntimeError) as e:
        print("FAIL: could not read comments: %s" % e, file=sys.stderr)
        return 1

    if args.dry_run:
        sink = DrySink(settings["schema"])
    else:
        dsn = os.environ.get(settings["dsn_env"])
        if not dsn:
            print("FAIL: $%s is not set — telemetry.dsnEnv names it, and the DSN is "
                  "never stored in delivery.json" % settings["dsn_env"], file=sys.stderr)
            return 1
        try:
            sink = PostgresSink(dsn, settings["schema"])
        except Exception as e:
            print("FAIL: could not connect to the telemetry store: %s" % e, file=sys.stderr)
            return 1

    try:
        if args.init:
            sink.init()
        result = sweep(comments, sink)
        sink.commit()
    except Exception as e:
        print("FAIL: the store rejected the sweep: %s" % e, file=sys.stderr)
        return 1
    finally:
        sink.close()

    result["since"] = since
    result["dry_run"] = bool(args.dry_run)
    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        s = result["stats"]
        print("Swept %d comment(s) since %s: %d block(s) → %d run(s), %d event(s), "
              "%d finding(s); %d row(s) skipped, %d fence(s) unparseable."
              % (s["comments"], since, s["blocks"], s["runs"], s["ticket_events"],
                 s["review_findings"], s["skipped"], s["malformed_json"]))
        for line in result["skipped"][:20]:
            print("  skip: %s" % line)
        for line in result["flags"][:20]:
            print("  flag: %s" % line)
        if args.dry_run:
            print("  (dry run — no database was contacted)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
