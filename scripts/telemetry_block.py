#!/usr/bin/env python3
"""The telemetry block, in one place: build it, scan for it, validate it.

WHY THIS FILE EXISTS

Contract §4 froze the shape of the block a run posts. Three parties handle that
shape and, until this file, each handled it differently:

  * the SESSION wrote one from a template in `/work`;
  * the safe-outputs validator (§8) COUNTED a marker with a regex and never once
    looked at what the marker was attached to;
  * the collector (`telemetry_scrape.py`) PARSED ```json fences and dropped
    anything it could not use.

The hole in the middle of that is the one telemetry exists to close: a block
malformed in any way the marker regex could not see passed the gate, then got
silently dropped by the collector, and the dashboard rendered an empty panel
with no error anywhere. Loud at the producer beats silent at the consumer, so
the gate now validates the block it is counting — against
`schemas/telemetry-block.schema.json`, which is §4's machine rendering (§12).

WHAT IS AUTHORITY HERE, AND WHAT IS NOT

Nothing. Conformance says the block is WELL FORMED; it says nothing about
whether the numbers are true. §4's rule stands unchanged: agent-authored ⇒
reporting only, and no value that passes through this file may gate a budget,
an approval or a merge.

THE SCAN MUST MATCH THE COLLECTOR'S, EXACTLY

`scan()` finds blocks with `telemetry_scrape.FENCE_RE` — imported, not
re-typed — because a gate that accepts what the collector will reject is worse
than no gate: it certifies data into a hole. The one place the two deliberately
differ is that the collector steps over what it cannot read (a sweep must never
die on one bad row) while the gate refuses it (the session that wrote it is
still there to fix it).

Usage:
    telemetry_block.py --gate REQUESTS.json [--no-require-telemetry]
    telemetry_block.py --from-review FINDINGS.json --out REQUESTS.json \
                       --team-key ENG --model claude-sonnet-5 --auth-mode api-key \
                       --run-id r_... --started-at ISO --ended-at ISO \
                       [--usage EXECUTION.json] [--reviewer-outcome success]
    telemetry_block.py --selftest

Exit: 0 = valid, 1 = rejected, 2 = usage/IO error.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jsonschema_mini as jsm  # noqa: E402
import telemetry_scrape as scrape  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(REPO_ROOT, "schemas", "telemetry-block.schema.json")

SAFE_OUTPUTS_SCHEMA = "pipeline-safe-outputs/1"
REVIEW_SCHEMA = "pipeline-review/1"

# The marker §8 counts, matched with the same whitespace tolerance the contract
# specifies so nothing hinges on a JSON writer's spacing.
MARKER_RE = re.compile(r'"schema"\s*:\s*"pipeline-telemetry/1"')

# §8 caps. A comment body over this is rejected by the validator, so an emitter
# that would exceed it must shed rows LOUDLY rather than produce a doomed batch.
MAX_BODY = 16000
MAX_FINDINGS = 50
MAX_SUMMARY = 500

_SCHEMA_CACHE = {}


def load_schema(path=None):
    """§4's machine rendering. Cached — the gate reads it once per batch."""
    path = path or SCHEMA_PATH
    if path not in _SCHEMA_CACHE:
        with open(path, encoding="utf-8") as handle:
            _SCHEMA_CACHE[path] = json.load(handle)
    return _SCHEMA_CACHE[path]


def finding_fields(schema=None):
    """The telemetry finding's field names, read OUT of the schema.

    Contract §4: there is one finding shape in the system, not two. The review
    workflow's `pipeline-review/1` artifact carries an extra `detail` field that
    §4 never defined, so the emitter PROJECTS onto these names. Deriving the
    list from the schema rather than re-typing it means the projection cannot
    drift from the definition it is projecting onto.
    """
    schema = schema or load_schema()
    return list((schema["$defs"]["reviewFinding"].get("properties") or {}).keys())


def validate_block(doc, schema=None):
    """[] when `doc` conforms to §4. Messages are already human-readable."""
    schema = schema or load_schema()
    out = []
    for error in jsm.validate(doc, schema):
        where = error["path"] or "the block"
        out.append("%s %s (%s)" % (where, error["message"], error["keyword"]))
    return out


def semantic_errors(doc):
    """The one §4 rule a schema deliberately cannot express.

    §4: "`merged` is always `human` or `system` — never `agent`", and §10 makes
    that row the single one a collector REFUSES rather than skips, because
    recording it would corrupt every autonomy metric computed downstream. The
    schema keeps such a block shape-valid on purpose (a pinned fixture in
    `check_schemas.py` asserts it), so the check lives here — and it lives at
    the gate rather than only at the collector because a session claiming its own
    merge should be told so, not quietly filtered three jobs later.

    Nothing else is re-checked here. The collector records-and-flags its other
    cross-field rules, and a row that is recorded is not a row that vanished.
    """
    out = []
    for index, row in enumerate(doc.get("ticket_events") or []):
        if not isinstance(row, dict):
            continue
        if row.get("event") == "merged" and row.get("actor") == "agent":
            out.append("ticket_events[%d]: `merged` with actor `agent` — §4 forbids it, "
                       "and no session merges its own PR" % index)
    return out


def scan(body, schema=None):
    """Every telemetry block in one comment body, and everything wrong with it.

    Returns {"blocks": [...], "count": n, "errors": [...]}. `count` is what §8
    counts toward its exactly-one rule; `errors` is non-empty whenever a
    reasonable reader would find a block here that the COLLECTOR would not.
    """
    body = body or ""
    errors = []
    blocks = []
    marked_fences = 0

    for match in scrape.FENCE_RE.finditer(body):
        raw = match.group(1)
        marked = bool(MARKER_RE.search(raw))
        try:
            doc = json.loads(raw)
        except ValueError as exc:
            if marked:
                marked_fences += 1
                errors.append("a ```json fence carrying the telemetry marker is not "
                              "valid JSON (%s) — the collector would drop it silently" % exc)
            continue
        if not (isinstance(doc, dict) and doc.get("schema") == scrape.TELEMETRY_SCHEMA):
            if marked:
                marked_fences += 1
                errors.append("a ```json fence carries the telemetry marker but is not a "
                              "telemetry object — the collector keys on the top-level "
                              "`schema` value")
            continue
        marked_fences += 1
        blocks.append(doc)
        for problem in validate_block(doc, schema):
            errors.append("telemetry block invalid: %s" % problem)
        errors.extend(semantic_errors(doc))

    # A marker the collector will never reach: outside a fence, inside a fence
    # whose info string is not `json`, or in prose. §4's marker is only ever
    # found by a fence scan, so an unfenced one is data written into a hole.
    stray = len(MARKER_RE.findall(body)) - marked_fences
    if stray > 0:
        errors.append("%d telemetry marker(s) sit outside a ```json fence — the "
                      "collector scans fences, so these rows would never be collected"
                      % stray)

    if len(blocks) > 1:
        errors.append("%d telemetry blocks in one comment — §4 allows one" % len(blocks))

    return {"blocks": blocks, "count": len(blocks), "errors": errors}


def gate(bodies, require_telemetry=True, schema=None):
    """The `telemetry-required` guard (§2), over every comment body in a batch."""
    errors, total, blocks = [], 0, []
    for index, body in enumerate(bodies):
        probe = scan(body, schema)
        total += probe["count"]
        blocks.extend(probe["blocks"])
        for problem in probe["errors"]:
            errors.append("comment[%d]: %s" % (index, problem))
    if require_telemetry and total != 1:
        errors.append("expected exactly one telemetry block across all comments, found "
                      "%d (contract §4)" % total)
    return {"ok": not errors, "errors": errors, "count": total, "blocks": blocks}


def comment_bodies(requests_doc):
    """Every `ticket-comment` body in a §8 requests document, in array order."""
    out = []
    for req in (requests_doc.get("requests") or []):
        if isinstance(req, dict) and req.get("type") == "ticket-comment":
            out.append(str(req.get("body") or ""))
    return out


# --------------------------------------------------------------------------- #
# The review emitter — `pipeline-review/1` artifact → a §4 block
# --------------------------------------------------------------------------- #
def usage_from(execution):
    """Best-effort token/cost figures out of a Claude Code execution log.

    Deliberately forgiving: the log's exact shape belongs to the action, not to
    this contract, so an unrecognized file yields zeros instead of an error. §4
    calls `cost_usd` a best-effort self-report for dashboards only — a review
    run that under-reports buys itself nothing, so guessing low is safe and
    crashing the emitter over a schema this file does not own is not.
    """
    found = {}

    def visit(node):
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if not isinstance(node, dict):
            return
        if "total_cost_usd" in node or isinstance(node.get("usage"), dict):
            found.update(node)
        for value in node.values():
            visit(value)

    visit(execution)
    usage = found.get("usage") if isinstance(found.get("usage"), dict) else {}

    def counter(*names):
        for name in names:
            value = usage.get(name)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                return value
        return 0

    cost = found.get("total_cost_usd")
    turns = found.get("num_turns")
    return {
        "tokens_in": counter("input_tokens"),
        "tokens_out": counter("output_tokens"),
        "tokens_cache_read": counter("cache_read_input_tokens"),
        "tokens_cache_write": counter("cache_creation_input_tokens"),
        "cost_usd": round(float(cost), 4) if isinstance(cost, (int, float))
                    and not isinstance(cost, bool) and cost >= 0 else 0.0,
        "turns": turns if isinstance(turns, int) and not isinstance(turns, bool)
                 and turns >= 0 else 0,
    }


def project_findings(findings, pr_number, at, schema=None):
    """`pipeline-review/1` findings → §4 `review_findings` rows.

    THE SHAPE DECISION, stated where it happens: the telemetry row wins. The
    review artifact carries a `detail` string that §4 never defined; it stays in
    the artifact (the PR comment renders it, and the bounce hands it to the fix
    session) and is dropped HERE, at the one boundary between the two. Every
    other field is carried across unchanged, so the store holds §4's shape and
    nothing else.
    """
    allowed = set(finding_fields(schema))
    rows = []
    for raw in findings or []:
        if not isinstance(raw, dict):
            continue
        row = {k: v for k, v in raw.items() if k in allowed}
        if not row.get("severity") or not str(row.get("summary") or "").strip():
            continue
        row["summary"] = str(row["summary"])[:MAX_SUMMARY]
        row.setdefault("category", "general")
        row["pr_number"] = pr_number
        row["at"] = at
        if row.get("line") is not None and not isinstance(row["line"], int):
            row["line"] = None
        if row.get("file") is not None:
            row["file"] = str(row["file"])
        rows.append(row)
    return rows[:MAX_FINDINGS]


def review_block(artifact, team_key, model, auth_mode, run_id, started_at, ended_at,
                 dispatch_id=None, execution=None, reviewer_outcome=None, schema=None):
    """The block the review pass posts: what it cost, that it ran, what it found."""
    usable = bool(artifact.get("usable"))
    pr_number = artifact.get("pr") if isinstance(artifact.get("pr"), int) else None
    if reviewer_outcome == "cancelled":
        outcome, error_class = "timeout", "review_cancelled"
    elif usable:
        outcome, error_class = "completed", None
    else:
        outcome, error_class = "error", "review_unusable"

    usage = usage_from(execution) if execution is not None else {
        "tokens_in": 0, "tokens_out": 0, "tokens_cache_read": 0,
        "tokens_cache_write": 0, "cost_usd": 0.0, "turns": 0,
    }

    run = {
        "run_id": run_id,
        "dispatch_id": dispatch_id or artifact.get("dispatch_id") or None,
        "session_mode": "maintenance",
        "ticket_id": artifact.get("ticket_id"),
        "team_key": team_key,
        "stage": "review",
        "model": model,
        "auth_mode": auth_mode,
        "started_at": started_at,
        "ended_at": ended_at,
        "tokens_in": usage["tokens_in"],
        "tokens_out": usage["tokens_out"],
        "tokens_cache_read": usage["tokens_cache_read"],
        "tokens_cache_write": usage["tokens_cache_write"],
        "cost_usd": usage["cost_usd"],
        "turns": usage["turns"],
        "outcome": outcome,
        "error_class": error_class,
        # A reviewer writes no code. Reporting a diff it did not make would put
        # phantom lines in every throughput figure the dashboard computes.
        "files_changed": 0, "lines_added": 0, "lines_removed": 0,
        "pr_number": pr_number,
    }
    events = [{
        "ticket_id": artifact.get("ticket_id"),
        "event": "review_posted",
        "at": ended_at,
        # §4: `system` = CI. The comment is posted by a deterministic job, and
        # the bounce ACTS on these findings — so the row comes from the side
        # that owns the loop, never from the model that wrote the prose.
        "actor": "system",
    }]
    block = {"schema": scrape.TELEMETRY_SCHEMA, "runs": [run], "ticket_events": events}
    findings = project_findings(artifact.get("findings"), pr_number, ended_at, schema)
    if findings:
        block["review_findings"] = findings
    return block


def review_comment(artifact, block, notes=None):
    """The ticket comment body carrying `block`, trimmed to §8's cap out loud."""
    notes = notes if notes is not None else []
    summary = str(artifact.get("summary") or "").strip()
    head = ["**Pipeline review** — PR #%s" % artifact.get("pr")]
    if summary:
        head.append("")
        head.append(summary[:1500])

    def render(doc):
        return "\n".join(head + ["", "```json", json.dumps(doc, indent=2), "```", ""])

    body = render(block)
    dropped = 0
    # §8 caps the body at 16 000 chars and rejects the WHOLE batch over it. Shed
    # findings rather than lose the run row and the lifecycle event with them —
    # and say how many, because a cap nobody logged reads as "that was all".
    while len(body) > MAX_BODY and len(block.get("review_findings") or []) > 0:
        block["review_findings"].pop()
        dropped += 1
        if not block["review_findings"]:
            del block["review_findings"]
        body = render(block)
    if dropped:
        notes.append("dropped %d finding(s) to stay inside the %d-char comment cap"
                     % (dropped, MAX_BODY))
    return body


def review_requests(artifact, body):
    """The §8 batch the review pass hands to the credential-holding validator.

    Workflow-authored, not agent-authored — the model's only output was a
    findings file, and everything here is computed from it by CI. It still
    travels through the same validator as a session's batch, because a narrower
    channel that skips the check is a channel nobody re-checks.
    """
    return {
        "schema": SAFE_OUTPUTS_SCHEMA,
        "requests": [{
            "type": "ticket-comment",
            "ticket_id": artifact.get("ticket_id"),
            "body": body,
        }],
    }


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
GOOD_ARTIFACT = {
    "schema": REVIEW_SCHEMA,
    "ticket_id": "ENG-123",
    "dispatch_id": "d_01JAV8Q2S6",
    "pr": 41,
    "threshold": "medium",
    "reviewer_outcome": "success",
    "summary": "Two real problems, one of them in the tests.",
    "findings": [
        {"severity": "high", "category": "security", "file": "src/auth.ts", "line": 42,
         "summary": "the refresh token is written to the request log",
         "detail": "log.info(req) serializes the whole body; redact before logging."},
        {"severity": "critical", "category": "tests",
         "summary": "the failing assertion was deleted rather than fixed",
         "detail": "expect(res.status).toBe(401) was removed in the same commit."},
    ],
    "max_severity": "critical",
    "meets_threshold": True,
    "usable": True,
}

EXECUTION_LOG = [
    {"type": "system", "subtype": "init"},
    {"type": "result", "subtype": "success", "num_turns": 12, "total_cost_usd": 0.8321,
     "usage": {"input_tokens": 90000, "output_tokens": 4200,
               "cache_read_input_tokens": 60000, "cache_creation_input_tokens": 8000}},
]


def fence(doc):
    return "Run complete.\n\n```json\n%s\n```\n" % json.dumps(doc, indent=2)


def selftest():
    import copy

    failures = []
    cases = [0]

    def check(label, cond, detail=""):
        cases[0] += 1
        if not cond:
            failures.append("%s%s" % (label, (": " + detail) if detail else ""))

    good = {"schema": scrape.TELEMETRY_SCHEMA, "runs": [copy.deepcopy(scrape.GOOD_RUN)],
            "ticket_events": [{"ticket_id": "ENG-123", "event": "pr_opened",
                               "at": "2026-08-24T15:40:00Z", "actor": "agent"}]}

    # ── The gate accepts exactly what the collector will read ───────────────
    check("the gate scans with the collector's own fence regex",
          scan.__globals__["scrape"].FENCE_RE is scrape.FENCE_RE)

    probe = scan(fence(good))
    check("a conforming block scans clean", probe["count"] == 1 and not probe["errors"],
          "; ".join(probe["errors"]))

    check("a conforming block validates against §4", validate_block(good) == [])

    # ── THE REGRESSION: passes the old marker count, dies at the collector ──
    # Every one of these carries the marker exactly once, so the string-matching
    # gate this file replaced waved all of them through — and the collector then
    # dropped the row with no error anywhere. That silent hole is the bug.
    for label, mutate in (
        ("a negative counter", lambda d: d["runs"][0].__setitem__("tokens_in", -1)),
        ("a null cache counter", lambda d: d["runs"][0].__setitem__("tokens_cache_read", None)),
        ("a stage outside the enum", lambda d: d["runs"][0].__setitem__("stage", "gardening")),
        ("a timestamp with no Z", lambda d: d["runs"][0].__setitem__("started_at", "2026-08-24 15:04:05")),
        ("a missing run_id", lambda d: d["runs"][0].pop("run_id")),
        ("an unknown run field", lambda d: d["runs"][0].__setitem__("cost", 1.0)),
        ("an event with no actor", lambda d: d["ticket_events"][0].pop("actor")),
        ("an event outside the enum", lambda d: d["ticket_events"][0].__setitem__("event", "vibed")),
        ("an empty runs array", lambda d: d.__setitem__("runs", [])),
    ):
        bad = copy.deepcopy(good)
        mutate(bad)
        body = fence(bad)
        check("old gate would have passed %s" % label,
              len(MARKER_RE.findall(body)) == 1)
        probe = scan(body)
        check("the gate now rejects %s" % label, probe["errors"],
              "no error raised")

    # And the half of that pair that motivates it: the collector really does
    # drop these, silently, one row at a time.
    dropped = copy.deepcopy(good)
    dropped["runs"][0]["stage"] = "gardening"
    sink = scrape.DrySink("pipeline")
    swept = scrape.sweep([scrape.comment(fence(dropped))], sink)
    check("the collector drops what the gate now catches",
          swept["stats"]["runs"] == 0 and swept["stats"]["skipped"] == 1,
          json.dumps(swept["stats"]))

    # ── Markers the collector could never reach ─────────────────────────────
    probe = scan('Here is what I would have posted: {"schema": "pipeline-telemetry/1"}')
    check("an unfenced marker is rejected", probe["errors"] and probe["count"] == 0,
          "; ".join(probe["errors"]))

    probe = scan('```json\n{"schema": "pipeline-telemetry/1", runs: []}\n```')
    check("a marked fence that is not JSON is rejected", probe["errors"])

    probe = scan('```json\n{"schema": "something-else/1", "runs": []}\n```')
    check("an unrelated json fence is ignored", probe["count"] == 0 and not probe["errors"],
          "; ".join(probe["errors"]))

    probe = scan(fence(good) + fence(good))
    check("two blocks in one comment is a double-count", probe["count"] == 2 and probe["errors"])

    # ── The one semantic rule, checked here as well as at the collector ─────
    claimed = copy.deepcopy(good)
    claimed["ticket_events"].append({"ticket_id": "ENG-123", "event": "merged",
                                     "at": "2026-08-24T18:00:00Z", "actor": "agent"})
    check("a session claiming its own merge is shape-VALID (§4's fixture says so)",
          validate_block(claimed) == [])
    check("...and is rejected anyway", scan(fence(claimed))["errors"])
    allowed = copy.deepcopy(good)
    allowed["ticket_events"].append({"ticket_id": "ENG-123", "event": "merged",
                                     "at": "2026-08-24T18:00:00Z", "actor": "human"})
    check("a human merge reported by a session is fine",
          not scan(fence(allowed))["errors"], "; ".join(scan(fence(allowed))["errors"]))

    # ── The batch-level rule (§8) ───────────────────────────────────────────
    check("zero blocks fails when telemetry is required",
          not gate(["just prose"], True)["ok"])
    check("zero blocks is fine when it is not required",
          gate(["just prose"], False)["ok"])
    check("exactly one block across two comments passes",
          gate(["the plan", fence(good)], True)["ok"])
    check("one block per comment across two comments is still two",
          not gate([fence(good), fence(good)], True)["ok"])

    # ── The finding shape: one shape, and `detail` is not in it ─────────────
    fields = finding_fields()
    check("the projection reads its fields out of the schema",
          set(fields) == {"severity", "category", "file", "line", "summary",
                          "pr_number", "at"}, ", ".join(sorted(fields)))
    check("`detail` is not a telemetry finding field", "detail" not in fields)

    rows = project_findings(GOOD_ARTIFACT["findings"], 41, "2026-08-24T16:00:00Z")
    check("every finding is projected", len(rows) == 2)
    check("`detail` is dropped at the boundary",
          all("detail" not in r for r in rows), json.dumps(rows))
    check("pr_number is stamped from the run", all(r["pr_number"] == 41 for r in rows))
    check("a finding with no summary is not a finding",
          project_findings([{"severity": "low", "summary": "  "}], 1, "2026-08-24T16:00:00Z") == [])

    # ── The review emitter produces a block that passes its own gate ────────
    block = review_block(GOOD_ARTIFACT, "ENG", "claude-sonnet-5", "api-key",
                         "r_review_1_1", "2026-08-24T15:50:00Z", "2026-08-24T16:00:00Z",
                         execution=EXECUTION_LOG, reviewer_outcome="success")
    check("the review block conforms to §4", validate_block(block) == [],
          "; ".join(validate_block(block)))
    check("the review run reports stage review", block["runs"][0]["stage"] == "review")
    check("the review run is a maintenance-mode run",
          block["runs"][0]["session_mode"] == "maintenance")
    check("the review run carries the real cost", block["runs"][0]["cost_usd"] == 0.8321)
    check("the review run carries the real turns", block["runs"][0]["turns"] == 12)
    check("review_posted is emitted by the system, never the agent",
          block["ticket_events"][0]["event"] == "review_posted"
          and block["ticket_events"][0]["actor"] == "system")
    check("the findings ride along", len(block["review_findings"]) == 2)

    unusable = dict(GOOD_ARTIFACT, usable=False, findings=[], summary="reviewer produced nothing")
    block_bad = review_block(unusable, "ENG", "claude-sonnet-5", "api-key", "r_review_2_1",
                             "2026-08-24T15:50:00Z", "2026-08-24T16:00:00Z")
    check("an unusable review is reported as an error, not as clean",
          block_bad["runs"][0]["outcome"] == "error"
          and block_bad["runs"][0]["error_class"] == "review_unusable")
    check("an unusable review conforms too", validate_block(block_bad) == [])
    check("no findings array when there are no findings",
          "review_findings" not in block_bad)

    # ── The body, the batch, and the cap ────────────────────────────────────
    notes = []
    body = review_comment(GOOD_ARTIFACT, copy.deepcopy(block), notes)
    check("the comment carries exactly one block", gate([body], True)["ok"],
          "; ".join(gate([body], True)["errors"]))
    check("nothing was silently dropped", not notes, "; ".join(notes))

    fat = copy.deepcopy(block)
    fat["review_findings"] = [dict(fat["review_findings"][0], summary="x" * MAX_SUMMARY)
                              for _ in range(MAX_FINDINGS)]
    notes = []
    body_fat = review_comment(GOOD_ARTIFACT, fat, notes)
    check("an over-cap comment is trimmed", len(body_fat) <= MAX_BODY, str(len(body_fat)))
    check("...and says so rather than truncating in silence", notes, "no note emitted")

    batch = review_requests(GOOD_ARTIFACT, body)
    check("the batch is a §8 document", batch["schema"] == SAFE_OUTPUTS_SCHEMA)
    check("the batch names the pinned ticket",
          batch["requests"][0]["ticket_id"] == "ENG-123")
    check("the batch's own comment passes the gate",
          gate(comment_bodies(batch), True)["ok"])

    # ── End to end: emitted comment → collector → rows ──────────────────────
    sink = scrape.DrySink("pipeline")
    swept = scrape.sweep([scrape.comment(body, cid="c-review", ticket="ENG-123")], sink)
    check("end-to-end: nothing is skipped", swept["stats"]["skipped"] == 0,
          "; ".join(swept["skipped"]))
    check("end-to-end: the review run lands", swept["stats"]["runs"] == 1)
    check("end-to-end: review_posted lands", swept["stats"]["ticket_events"] == 1)
    check("end-to-end: both findings land", swept["stats"]["review_findings"] == 2,
          json.dumps(swept["stats"]))

    # ── usage_from is forgiving by design ──────────────────────────────────
    for label, payload in (("None", None), ("an empty dict", {}),
                           ("a stray string", "not a log"), ("a list of noise", [1, 2, 3])):
        zeros = usage_from(payload)
        check("usage_from(%s) yields zeros, not an exception" % label,
              zeros["cost_usd"] == 0.0 and zeros["turns"] == 0)

    if failures:
        print("FAIL: %d of %d telemetry-block case(s) failed:" % (len(failures), cases[0]))
        for line in failures:
            print("  - %s" % line)
        return 1
    print("OK: %d telemetry-block case(s) passed" % cases[0])
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(add_help=True, description=__doc__.splitlines()[0])
    ap.add_argument("--gate", metavar="REQUESTS", help="validate a pipeline-safe-outputs/1 file")
    ap.add_argument("--no-require-telemetry", action="store_true",
                    help="with --gate: zero blocks is allowed")
    ap.add_argument("--from-review", metavar="FINDINGS",
                    help="build a batch from a pipeline-review/1 findings artifact")
    ap.add_argument("--out", help="with --from-review: where to write the batch")
    ap.add_argument("--team-key", default="")
    ap.add_argument("--model", default="")
    ap.add_argument("--auth-mode", default="api-key")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--dispatch-id", default="")
    ap.add_argument("--started-at", default="")
    ap.add_argument("--ended-at", default="")
    ap.add_argument("--usage", help="a Claude Code execution log, for cost and turns")
    ap.add_argument("--reviewer-outcome", default="")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if args.gate:
        try:
            with open(args.gate, encoding="utf-8") as handle:
                doc = json.load(handle)
        except (OSError, ValueError) as exc:
            print("::error::safe-outputs file unreadable: %s" % exc)
            return 1
        verdict = gate(comment_bodies(doc), not args.no_require_telemetry)
        for line in verdict["errors"]:
            print("::error::telemetry-required: %s" % line)
        print("%d telemetry block(s); %s"
              % (verdict["count"], "valid" if verdict["ok"] else "REJECTED"))
        return 0 if verdict["ok"] else 1

    if args.from_review:
        if not args.out:
            print("--from-review needs --out", file=sys.stderr)
            return 2
        try:
            with open(args.from_review, encoding="utf-8") as handle:
                artifact = json.load(handle)
        except (OSError, ValueError) as exc:
            print("::error::review findings unreadable: %s" % exc)
            return 2
        execution = None
        if args.usage and os.path.exists(args.usage):
            try:
                with open(args.usage, encoding="utf-8") as handle:
                    execution = json.load(handle)
            except (OSError, ValueError) as exc:
                print("::notice::execution log unreadable (%s) — reporting zero cost." % exc)
        block = review_block(artifact, args.team_key, args.model, args.auth_mode,
                             args.run_id, args.started_at, args.ended_at,
                             dispatch_id=args.dispatch_id or None, execution=execution,
                             reviewer_outcome=args.reviewer_outcome or None)
        problems = validate_block(block)
        if problems:
            # Fail here rather than let the validator reject the batch downstream:
            # this block is workflow-authored, so a shape error is OUR bug.
            for line in problems:
                print("::error::review telemetry block is malformed: %s" % line)
            return 1
        notes = []
        body = review_comment(artifact, block, notes)
        for line in notes:
            print("::notice::%s" % line)
        batch = review_requests(artifact, body)
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(batch, handle, indent=2)
        print("Wrote %s: 1 comment, %d finding(s), %d char(s)."
              % (args.out, len(block.get("review_findings") or []), len(body)))
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
