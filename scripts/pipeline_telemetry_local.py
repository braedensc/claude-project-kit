#!/usr/bin/env python3
"""Stage E telemetry publisher — one §4 block per review/bounce run, posted to the
ticket it names. See docs/adr/2026-09-05-stage-e-under-a-delegation-bound-dispatcher.md,
decision 4, and docs/PIPELINE-CONTRACT.md §4 (telemetry block) / §8 (safe-outputs).

WHY THIS FILE, WHEN telemetry_block.py ALREADY BUILDS THE BLOCK

  `scripts/telemetry_block.py` builds a §4 block and the §8 envelope around it
  (`--from-review`, and now `--from-bounce` — added in this same change) — but it never
  POSTS anything. In GitHub Actions that split is deliberate: a "telemetry" job builds
  the batch and a separate "report" job, holding the one Linear credential in the repo,
  executes it through `pipeline-safe-outputs.yml`. Stage E has no Actions workflow to
  split across, so this file is that "report" job's dispatcher-side equivalent — it
  imports `telemetry_block.py` for the ONE block/batch definition (never re-implements
  it) and adds the one thing missing: the write.

WHY POSTING DIRECTLY HERE IS NOT A §8 VIOLATION

  §8 exists because a CODING session must hold no tracker credential — an agent holding
  the Linear key could write anything a prompt asked it to, and no wording prevents that.
  This script is not a coding session. It is Stage E's own deterministic, non-agent
  daemon code, run by the poller (KIT-91) or the bounce loop (KIT-93) after a run
  completes — the same trust position the "report" job occupies in the cloud template,
  which also holds the credential directly. The ADR's decision 6 already says E reads
  "GitHub and Linear"; this is the write half of "Linear".

THE ONE WRITE THIS FILE CAN EVER MAKE

  `commentCreate` on the named ticket. No label, no state transition, no issue creation —
  asserted in --selftest by grepping this file's own source for any other Linear mutation
  name, the same idiom `pipeline_review_local.py` uses for its GitHub write guard.

WHERE THE MODEL AND THE COST COME FROM (KIT-130)

  Stage E starts no model session itself: the dispatcher runs the reviewer, and it
  resumes the coding session a bounce re-prompts. So neither daemon ever held a model
  name or a cost, and every row it posted said `label:<uuid>` or `unknown` and cost 0.
  The dispatcher does record both — each session's SDK message stream goes to
  `<dispatcher home>/logs/<issue identifier>/session-*.jsonl`, one `sdk-message` per
  line, carrying the SDK's own `system/init` message (the model) and `result` message
  (`total_cost_usd`, `usage`, `num_turns`). Stage E's daemons run as the dispatcher's
  role account, so that directory is readable to them; `--session-logs` names it.

  Two roles, because a row is not always the run it names:
    run      the row IS that session's run (a review): model and cost both come from
             its log, and a missing piece is said in §4's `model_note`/`cost_note`.
    resumed  the row is written BEFORE the session runs (a bounce): the model is read
             from that session's last run and says so; the cost is never taken from
             the log — it would be the earlier run's — and the note says Stage E does
             not record it.
  `--no-session REASON` is for a row no model session belongs to at all (a conclusion,
  an exhaustion): the model is `unknown` with the reason in `model_note`, and the cost
  is an exact 0 with no `cost_note`, because nothing ran.

  The log layout is read from the dispatcher's published source (v0.2.69,
  `ClaudeRunner.setupLogging`), not from a log on any machine; the parser therefore
  tolerates unknown lines, and anything it cannot find becomes a stated reason rather
  than a guess.

Usage:
    pipeline_telemetry_local.py --from-review FINDINGS.json | --from-bounce ARTIFACT.json
                                --team-key KIT --model ID --auth-mode api-key
                                --run-id r_... --started-at ISO --ended-at ISO
                                [--dispatch-id ID] [--usage EXECUTION.json]
                                [--session-logs DIR --session-issue ID
                                 [--session-role run|resumed]] [--no-session REASON]
                                [--model-note TEXT] [--cost-note TEXT]
                                [--reviewer-outcome success] [--out REQUESTS.json]
                                [--api-key-env LINEAR_API_KEY] [--dry-run]
    pipeline_telemetry_local.py --selftest

Exit: 0 = built (and posted, unless --dry-run)
      1 = the block or the §8 envelope around it is malformed — refused, nothing posted
      2 = usage/IO/API error (bad arguments, no credential, Linear unreachable)
"""
import argparse
import fnmatch
import json
import os
import re
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import check_schemas as cs        # noqa: E402  (document_problems — the §8 envelope check)
import telemetry_block as tb      # noqa: E402  (review_block/bounce_block — the ONE builder)

EXIT_OK = 0
EXIT_REJECTED = 1
EXIT_USAGE = 2

LINEAR_API = "https://api.linear.app/graphql"
TICKET_ID_RE = re.compile(r"^([A-Z][A-Z0-9]*)-([0-9]+)$")

ISSUE_ID_QUERY = """
query($team: String!, $number: Float!) {
  issues(filter: { team: { key: { eq: $team } }, number: { eq: $number } }, first: 1) {
    nodes { id identifier }
  }
}"""

# The COMPLETE set of Linear mutations this file will ever construct: exactly one.
# --selftest asserts no other mutation name appears in this source.
COMMENT_MUTATION = """
mutation($issueId: String!, $body: String!) {
  commentCreate(input: { issueId: $issueId, body: $body }) { success }
}"""


class LinearError(Exception):
    """A Linear API call failed for a reason worth naming — never a silent no-op."""


# --------------------------------------------------------------------------- #
# Linear I/O — the one place this file writes
# --------------------------------------------------------------------------- #
def _graphql(query, variables, api_key):
    req = urllib.request.Request(
        LINEAR_API,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Content-Type": "application/json", "Authorization": api_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        raise LinearError("Linear API HTTP %d: %r" % (exc.code, exc.read()[:400]))
    except urllib.error.URLError as exc:
        raise LinearError("could not reach the Linear API: %s" % exc.reason)
    except (OSError, ValueError) as exc:
        raise LinearError("Linear API call failed: %s" % exc)
    if payload.get("errors"):
        raise LinearError("Linear API error: %s" % json.dumps(payload["errors"])[:400])
    return payload.get("data") or {}


def resolve_issue_uuid(ticket_id, api_key):
    match = TICKET_ID_RE.match(ticket_id or "")
    if not match:
        raise LinearError("%r is not a TEAM-123 shaped ticket id" % ticket_id)
    team_key, number = match.group(1), float(match.group(2))
    data = _graphql(ISSUE_ID_QUERY, {"team": team_key, "number": number}, api_key)
    nodes = ((data.get("issues") or {}).get("nodes")) or []
    if not nodes:
        raise LinearError("%s not found in team %s" % (ticket_id, team_key))
    return nodes[0]["id"]


def post_ticket_comment(ticket_id, body, api_key):
    """The ONE Linear write this file ever makes."""
    issue_id = resolve_issue_uuid(ticket_id, api_key)
    data = _graphql(COMMENT_MUTATION, {"issueId": issue_id, "body": body}, api_key)
    if not (data.get("commentCreate") or {}).get("success"):
        raise LinearError("Linear reported commentCreate did not succeed")


# --------------------------------------------------------------------------- #
# The dispatcher's session log — where a real model and a real cost live
# --------------------------------------------------------------------------- #
SESSION_LOG_GLOB = "session-*.jsonl"
SESSION_ROLES = ("run", "resumed")
# A tracker identifier, never a path: it is joined onto the log root, so anything that
# could climb out of it is refused before the join.
ISSUE_IDENTIFIER_RE = re.compile(r"^[A-Z][A-Z0-9]*-[0-9]+$")
# A bounce row is written before the re-prompted run, and no later row records that run:
# its cost stays only in the dispatcher's session log (no ticket yet).
NOT_INCURRED = ("the re-prompted session has not run when this row is written, and Stage E "
                "never records its cost: it stays only in the dispatcher's session log "
                "(no ticket yet)")


def _log_messages(path):
    """Every SDK message in one session log, oldest first. A line that is not JSON, or
    not a message, is stepped over: the file's shape belongs to the dispatcher. So is a
    line nested too deeply to decode, which raises RecursionError, not ValueError: the
    session being reported on writes this log, and must not be able to crash the row."""
    out = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                doc = json.loads(line)
            except (ValueError, RecursionError):
                continue
            if isinstance(doc, dict) and doc.get("type") == "sdk-message":
                doc = doc.get("message")
            if isinstance(doc, dict):
                out.append(doc)
    return out


def _billed_most(result):
    """The model a result message billed most, from its `modelUsage` map — or None."""
    usage = (result or {}).get("modelUsage")
    if not isinstance(usage, dict) or not usage:
        return None

    def weight(item):
        stats = item[1] if isinstance(item[1], dict) else {}
        cost = stats.get("costUSD")
        return (cost if isinstance(cost, (int, float)) and not isinstance(cost, bool) else 0,
                item[0])

    return max(usage.items(), key=weight)[0]


def session_usage(log_root, issue_identifier, role="run"):
    """What the dispatcher's session log says about one issue's session.

    Returns {"model", "model_note", "execution", "cost_note"}. `model` is None when the
    log names none; `execution` is the `result` message to cost from, or None. Every
    None carries its reason in the matching note — the §13 rule, applied to telemetry:
    "could not read it" and "there was nothing to read" are said differently, and never
    as silence."""
    if role not in SESSION_ROLES:
        raise ValueError("session role must be one of %s" % "|".join(SESSION_ROLES))
    resumed = role == "resumed"

    def nothing(why):
        return {"model": None, "model_note": why, "execution": None,
                "cost_note": NOT_INCURRED if resumed else why}

    if not log_root:
        return nothing("no dispatcher session-log directory is configured (session_log_root), "
                       "so the session's own record was not read")
    if not ISSUE_IDENTIFIER_RE.match(issue_identifier or ""):
        return nothing("no dispatcher session is named for this row")
    # Listed with os.listdir, never glob: glob swallows every OSError and returns [], so
    # a missing root, an unreadable folder and a folder with no log would all read as
    # "no log". The first is a configuration fault on every row, and is said as one.
    root = os.path.expanduser(log_root)
    try:
        os.listdir(root)
    except OSError as exc:
        return nothing("session_log_root %s is missing or cannot be listed (%s): a "
                       "configuration fault, not a missing log" % (root, exc.__class__.__name__))
    folder = os.path.join(root, issue_identifier)
    try:
        names = os.listdir(folder)
    except FileNotFoundError:
        names = []
    except OSError as exc:
        return nothing("the dispatcher's session log folder for %s cannot be listed (%s)"
                       % (issue_identifier, exc.__class__.__name__))
    try:
        files = sorted((os.path.join(folder, n) for n in names
                        if fnmatch.fnmatch(n, SESSION_LOG_GLOB)),
                       key=lambda p: (os.path.getmtime(p), p))
    except OSError as exc:
        return nothing("the dispatcher's session log folder for %s cannot be listed (%s)"
                       % (issue_identifier, exc.__class__.__name__))
    if not files:
        return nothing("the dispatcher wrote no session log for %s" % issue_identifier)

    model, result = None, None
    # Newest first, stopping at the first log that says anything: a runner writes a
    # metadata-only `pending` file before its session id exists, then the real one.
    for path in reversed(files):
        try:
            messages = _log_messages(path)
        except OSError as exc:
            return nothing("the dispatcher's session log for %s could not be read (%s)"
                           % (issue_identifier, exc.__class__.__name__))
        for msg in messages:
            if msg.get("type") == "system" and msg.get("subtype") == "init" \
                    and isinstance(msg.get("model"), str) and msg["model"].strip():
                model = msg["model"].strip()
            elif msg.get("type") == "result":
                result = msg
        if model or result:
            break

    model_note = None
    if not model:
        model = _billed_most(result)
        model_note = (("the session log for %s has no init message; this is the model its "
                       "result billed most" % issue_identifier) if model else
                      "the dispatcher's session log for %s names no model" % issue_identifier)
    if resumed and model:
        model_note = ("read from the last run of the session this row re-prompts (%s); the "
                      "resumed run may differ if its model label changed" % issue_identifier)

    if resumed:
        return {"model": model, "model_note": model_note, "execution": None,
                "cost_note": NOT_INCURRED}
    if result is None:
        return {"model": model, "model_note": model_note, "execution": None,
                "cost_note": "the session for %s has no result in its log yet, so its cost is "
                             "unreported" % issue_identifier}
    if not tb.execution_has_cost(result):
        return {"model": model, "model_note": model_note, "execution": result,
                "cost_note": "the session log's result for %s carries no total_cost_usd"
                             % issue_identifier}
    return {"model": model, "model_note": model_note, "execution": result, "cost_note": None}


def resolve_usage(args, execution):
    """(model, model_note, execution, cost_note) for this row, from the most direct
    source available: an explicit `--no-session`, then the dispatcher's session log,
    then what the caller passed. A caller's `--model` is a CONFIGURED value, so when it
    is used in place of the log it says so.

    A `--no-session` row puts its reason in `model_note` only. No model ran, so its
    cost of 0 is exact, and a `cost_note` would count it as unmeasured spend (§4)."""
    model = getattr(args, "model", "") or ""
    model_note = getattr(args, "model_note", "") or None
    cost_note = getattr(args, "cost_note", "") or None
    no_session = getattr(args, "no_session", "") or ""
    if no_session:
        return tb.UNKNOWN_MODEL, model_note or no_session, None, cost_note
    logs, issue = getattr(args, "session_logs", "") or "", getattr(args, "session_issue", "") or ""
    if not (logs or issue) or execution is not None:
        return model, model_note, execution, cost_note
    found = session_usage(logs, issue, getattr(args, "session_role", "") or "run")
    if found["model"]:
        chosen_model, chosen_note = found["model"], model_note or found["model_note"]
    elif not tb.model_is_unknown(model):
        chosen_model = model
        chosen_note = model_note or ("taken from configuration, not read from the session: %s"
                                     % found["model_note"])
    else:
        chosen_model, chosen_note = tb.UNKNOWN_MODEL, model_note or found["model_note"]
    return chosen_model, chosen_note, found["execution"], cost_note or found["cost_note"]


# --------------------------------------------------------------------------- #
# Pure-ish logic — file reads only, no network, so --selftest covers it offline
# --------------------------------------------------------------------------- #
def _read_usage(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def build_batch(args):
    """(batch, body, ticket_id, notes) built via telemetry_block.py's own functions —
    the ONE definition of the block and the §8 envelope, never re-implemented here.

    Raises ValueError with a human-readable reason on any malformed input; the caller
    must post and write nothing when this raises (§14's "malformed is unusable, never
    silently smaller", applied to telemetry the same way it applies to findings).
    """
    if args.from_review and args.from_bounce:
        raise ValueError("--from-review and --from-bounce are mutually exclusive")
    if args.from_review:
        with open(args.from_review, encoding="utf-8") as fh:
            artifact = json.load(fh)
        model, model_note, execution, cost_note = resolve_usage(args, _read_usage(args.usage))
        block = tb.review_block(artifact, args.team_key, model, args.auth_mode,
                                args.run_id, args.started_at, args.ended_at,
                                dispatch_id=args.dispatch_id or None, execution=execution,
                                reviewer_outcome=args.reviewer_outcome or None,
                                model_note=model_note, cost_note=cost_note,
                                no_session=bool(getattr(args, "no_session", "")))
        problems = tb.validate_block(block)
        if problems:
            raise ValueError("review telemetry block is malformed: " + "; ".join(problems))
        notes = []
        body = tb.review_comment(artifact, block, notes)
    elif args.from_bounce:
        with open(args.from_bounce, encoding="utf-8") as fh:
            artifact = json.load(fh)
        model, model_note, execution, cost_note = resolve_usage(args, _read_usage(args.usage))
        block = tb.bounce_block(artifact, args.team_key, model, args.auth_mode,
                                args.run_id, args.started_at, args.ended_at,
                                dispatch_id=args.dispatch_id or None, execution=execution,
                                model_note=model_note, cost_note=cost_note,
                                no_session=bool(getattr(args, "no_session", "")))
        problems = tb.validate_block(block)
        if problems:
            raise ValueError("bounce telemetry block is malformed: " + "; ".join(problems))
        notes = []
        body = tb.bounce_comment(artifact, block, notes)
    else:
        raise ValueError("one of --from-review/--from-bounce is required")

    batch = tb.review_requests(artifact, body)  # generic §8 envelope, reused unchanged
    batch_problems = cs.document_problems(batch, "safe-outputs")
    if batch_problems:
        raise ValueError("safe-outputs batch is malformed: " + "; ".join(batch_problems))
    return batch, body, artifact.get("ticket_id"), notes


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
def run(args):
    try:
        batch, body, ticket_id, notes = build_batch(args)
    except (OSError, ValueError) as exc:
        sys.stderr.write("FAIL: %s\n" % exc)
        return EXIT_REJECTED
    for line in notes:
        sys.stderr.write("NOTE: %s\n" % line)

    if args.out:
        out_dir = os.path.dirname(os.path.abspath(args.out))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(batch, fh, indent=2)

    if args.dry_run:
        print("=== [dry-run] ticket comment that would be posted to %s ===" % ticket_id)
        print(body)
        return EXIT_OK

    if not ticket_id:
        sys.stderr.write("FAIL: the artifact carries no ticket_id — nothing to post to.\n")
        return EXIT_REJECTED
    api_key = os.environ.get(args.api_key_env, "").strip()
    if not api_key:
        sys.stderr.write("FAIL: $%s is unset. Export the key (never pass it as a flag), "
                          "or pass --dry-run to build the batch offline without posting.\n"
                          % args.api_key_env)
        return EXIT_USAGE
    try:
        post_ticket_comment(ticket_id, body, api_key)
    except LinearError as exc:
        sys.stderr.write("FAIL: could not post telemetry to %s: %s\n" % (ticket_id, exc))
        return EXIT_USAGE
    print("posted telemetry for %s (%d char(s))" % (ticket_id, len(body)))
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
def selftest():
    import tempfile

    failures = []

    def check(name, got, want):
        if got != want:
            failures.append("%s: got %r, want %r" % (name, got, want))

    GOOD_REVIEW = {"schema": "pipeline-review/1", "ticket_id": "ENG-123", "pr": 41,
                   "threshold": "medium", "reviewer_outcome": "success",
                   "summary": "clean", "findings": [], "max_severity": None,
                   "meets_threshold": False, "usable": True}
    GOOD_BOUNCE = {"ticket_id": "ENG-123", "pr": 41, "bounce_no": 1, "max_bounces": 3,
                   "outcome": "completed"}
    common = dict(team_key="ENG", model="claude-sonnet-5", auth_mode="api-key",
                  run_id="r_1", dispatch_id="", started_at="2026-08-24T15:00:00Z",
                  ended_at="2026-08-24T15:05:00Z", usage=None, reviewer_outcome="")

    with tempfile.TemporaryDirectory() as tmp:
        # 1. build_batch: both artifact kinds resolve to a §8-conformant batch that
        #    carries exactly one telemetry block.
        review_file = os.path.join(tmp, "review.json")
        with open(review_file, "w", encoding="utf-8") as fh:
            json.dump(GOOD_REVIEW, fh)
        ns = argparse.Namespace(from_review=review_file, from_bounce=None, out=None, **common)
        batch, body, ticket_id, notes = build_batch(ns)
        check("build_batch(review) resolves the ticket", ticket_id, "ENG-123")
        check("build_batch(review) is a §8 batch", batch["schema"], "pipeline-safe-outputs/1")
        check("build_batch(review) body carries one telemetry block",
              tb.gate([body], True)["ok"], True)

        bounce_file = os.path.join(tmp, "bounce.json")
        with open(bounce_file, "w", encoding="utf-8") as fh:
            json.dump(GOOD_BOUNCE, fh)
        ns2 = argparse.Namespace(from_review=None, from_bounce=bounce_file, out=None, **common)
        batch2, body2, ticket_id2, notes2 = build_batch(ns2)
        check("build_batch(bounce) resolves the ticket", ticket_id2, "ENG-123")
        check("build_batch(bounce) body carries one telemetry block",
              tb.gate([body2], True)["ok"], True)

        # 2. Refusals are ValueError, not a crash and not a silent success.
        ns3 = argparse.Namespace(from_review=None, from_bounce=None, out=None, **common)
        try:
            build_batch(ns3)
            failures.append("build_batch with neither flag should have raised")
        except ValueError:
            pass

        bad = dict(GOOD_REVIEW, ticket_id="eng-123")  # lowercased -> §8 rejects it
        bad_file = os.path.join(tmp, "bad.json")
        with open(bad_file, "w", encoding="utf-8") as fh:
            json.dump(bad, fh)
        ns4 = argparse.Namespace(from_review=bad_file, from_bounce=None, out=None, **common)
        try:
            build_batch(ns4)
            failures.append("build_batch should refuse a batch §8 would reject")
        except ValueError:
            pass

        both = argparse.Namespace(from_review=review_file, from_bounce=bounce_file,
                                  out=None, **common)
        try:
            build_batch(both)
            failures.append("build_batch should refuse both flags at once")
        except ValueError:
            pass

    # 3. run(): --dry-run never touches the network and still writes --out when given.
    with tempfile.TemporaryDirectory() as tmp:
        review_file = os.path.join(tmp, "review.json")
        with open(review_file, "w", encoding="utf-8") as fh:
            json.dump(GOOD_REVIEW, fh)
        out = os.path.join(tmp, "requests.json")
        ns = argparse.Namespace(from_review=review_file, from_bounce=None, out=out,
                                dry_run=True, api_key_env="LINEAR_API_KEY_TEST_94", **common)
        check("dry-run exits OK", run(ns), EXIT_OK)
        check("dry-run still writes --out", os.path.exists(out), True)

    # 4. run(): the poster is invoked exactly once, with the pinned ticket — stubbed
    #    offline so this test needs no network and no real credential.
    posted = []
    saved_poster = post_ticket_comment
    try:
        globals()["post_ticket_comment"] = (
            lambda ticket_id, body, api_key: posted.append((ticket_id, body)))
        with tempfile.TemporaryDirectory() as tmp:
            review_file = os.path.join(tmp, "review.json")
            with open(review_file, "w", encoding="utf-8") as fh:
                json.dump(GOOD_REVIEW, fh)
            os.environ["LINEAR_API_KEY_TEST_94"] = "x"
            ns = argparse.Namespace(from_review=review_file, from_bounce=None, out=None,
                                    dry_run=False, api_key_env="LINEAR_API_KEY_TEST_94",
                                    **common)
            check("run() posts exactly once", run(ns), EXIT_OK)
            check("run() posted to the pinned ticket",
                  posted[0][0] if posted else None, "ENG-123")
            del os.environ["LINEAR_API_KEY_TEST_94"]
    finally:
        globals()["post_ticket_comment"] = saved_poster

    # 5. A missing credential (and no --dry-run) is a loud usage error, never a
    #    silent skip that looks identical to "nothing to report" (§13).
    with tempfile.TemporaryDirectory() as tmp:
        review_file = os.path.join(tmp, "review.json")
        with open(review_file, "w", encoding="utf-8") as fh:
            json.dump(GOOD_REVIEW, fh)
        ns = argparse.Namespace(from_review=review_file, from_bounce=None, out=None,
                                dry_run=False, api_key_env="LINEAR_API_KEY_TEST_94_ABSENT",
                                **common)
        check("no credential and no --dry-run is a usage error", run(ns), EXIT_USAGE)

    # 5b. KIT-130 — the model and the cost come from the dispatcher's session log.
    #     The fixture mirrors what the dispatcher writes (ClaudeRunner.setupLogging):
    #     a metadata line, then one `sdk-message` per SDK message, and an older
    #     metadata-only `pending` file from before the session id was known.
    with tempfile.TemporaryDirectory() as root:
        def write_log(issue, name, lines, mtime):
            folder = os.path.join(root, issue)
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, name)
            with open(path, "w", encoding="utf-8") as fh:
                for line in lines:
                    fh.write((line if isinstance(line, str) else json.dumps(line)) + "\n")
            os.utime(path, (mtime, mtime))

        meta = {"type": "session-metadata", "sessionId": "s1", "workspaceName": "REV-3"}
        init = {"type": "sdk-message", "message": {"type": "system", "subtype": "init",
                                                   "model": "claude-opus-5"}}
        result = {"type": "sdk-message", "message": {
            "type": "result", "subtype": "success", "num_turns": 7, "total_cost_usd": 0.9132,
            "usage": {"input_tokens": 4100, "output_tokens": 950,
                      "cache_read_input_tokens": 30000, "cache_creation_input_tokens": 2000}}}
        write_log("REV-3", "session-pending-2026-09-16T10-00-00.jsonl", [meta], 1000)
        write_log("REV-3", "session-s1-2026-09-16T10-00-01.jsonl",
                  [meta, "not json at all", init, result], 2000)

        got = session_usage(root, "REV-3")
        check("run: the model is read from the init message", got["model"], "claude-opus-5")
        check("run: a model read from the run itself carries no note", got["model_note"], None)
        check("run: the cost is measured, so no cost note", got["cost_note"], None)
        check("run: the execution is the result message",
              tb.usage_from(got["execution"])["cost_usd"], 0.9132)

        resumed = session_usage(root, "REV-3", role="resumed")
        check("resumed: the model comes from the last run, and says so",
              (resumed["model"], "last run" in (resumed["model_note"] or "")),
              ("claude-opus-5", True))
        check("resumed: the earlier run's cost is never taken", resumed["execution"], None)
        check("resumed: the cost note says it is not incurred", resumed["cost_note"], NOT_INCURRED)

        write_log("TOD-9", "session-s2-2026-09-16T11-00-00.jsonl", [meta, init], 3000)
        running = session_usage(root, "TOD-9")
        check("a session with no result yet names its model and says cost is unreported",
              (running["model"], "no result" in (running["cost_note"] or "")),
              ("claude-opus-5", True))

        billed = {"type": "sdk-message", "message": {
            "type": "result", "total_cost_usd": 0.5,
            "modelUsage": {"claude-haiku-4-5": {"costUSD": 0.01},
                           "claude-opus-5": {"costUSD": 0.49}}}}
        write_log("TOD-10", "session-s3-2026-09-16T12-00-00.jsonl", [meta, billed], 4000)
        fallback = session_usage(root, "TOD-10")
        check("no init message: the model its result billed most, with the reason",
              (fallback["model"], "billed most" in (fallback["model_note"] or "")),
              ("claude-opus-5", True))

        for label, logs, issue, needle in (
                ("no root configured", "", "REV-3", "session_log_root"),
                ("no log for the issue", root, "REV-404", "wrote no session log"),
                ("an identifier that is a path", root, "../REV-3", "no dispatcher session")):
            none = session_usage(logs, issue)
            check("%s: model is None with a reason" % label,
                  (none["model"], needle in (none["model_note"] or "")), (None, True))
            check("%s: cost has a reason too" % label, bool(none["cost_note"]), True)

        # A root that is missing or cannot be listed, an issue folder that cannot
        # be listed, and an issue with no log are three different facts. glob said all
        # three as "no log"; the first is a configuration fault on every row.
        missing_root = os.path.join(root, "no-such-dispatcher-home", "logs")
        said = {"no log": session_usage(root, "REV-404")["model_note"] or "",
                "missing root": session_usage(missing_root, "REV-3")["model_note"] or ""}
        check("a missing root is a configuration fault that names the path",
              ("configuration fault" in said["missing root"], missing_root in said["missing root"],
               "session_log_root" in said["missing root"]), (True, True, True))
        check("an issue with no log says only that",
              ("wrote no session log for REV-404" in said["no log"],
               "configuration" in said["no log"], "cannot" in said["no log"]),
              (True, False, False))
        can_deny = hasattr(os, "geteuid") and os.geteuid() != 0
        if can_deny:
            locked_root = os.path.join(root, "locked-home")
            os.makedirs(os.path.join(locked_root, "REV-3"))
            write_log("REV-5", "session-s5-2026-09-16T13-00-00.jsonl", [meta, init, result], 5000)
            os.chmod(locked_root, 0)
            os.chmod(os.path.join(root, "REV-5"), 0)
            try:
                said["unlistable root"] = session_usage(locked_root, "REV-3")["model_note"] or ""
                said["unlistable folder"] = session_usage(root, "REV-5")["model_note"] or ""
            finally:
                os.chmod(locked_root, 0o700)
                os.chmod(os.path.join(root, "REV-5"), 0o700)
            check("a root this account cannot list is a configuration fault that names the path",
                  ("configuration fault" in said["unlistable root"],
                   locked_root in said["unlistable root"]), (True, True))
            check("an issue folder this account cannot list says so, and is not a missing log",
                  ("REV-5 cannot be listed" in said["unlistable folder"],
                   "configuration" in said["unlistable folder"]), (True, False))
        check("the root, the folder and the missing log are said differently",
              len(set(said.values())) == len(said) and all(said.values()), True)

        # A line nested past the decoder's recursion limit raises RecursionError, not
        # ValueError. The session reported on writes this log, so the line is stepped over
        # like any other it cannot parse, and the row still reads the lines around it.
        # The depth is the first that raises on the interpreter running this test (about
        # 1000 on 3.9, more on later versions); the deepest is used if none does.
        for depth in (2000, 20000, 200000):
            try:
                json.loads("[" * depth + "]" * depth)
            except RecursionError:
                break
        deep = "[" * depth + "]" * depth
        write_log("REV-7", "session-s7-2026-09-16T14-00-00.jsonl",
                  [meta, init, '{"type": "sdk-message", "message": %s}' % deep, result], 6000)
        try:
            nested = session_usage(root, "REV-7")
            check("a deeply nested log line is stepped over, and the row still reads",
                  (nested["model"], nested["cost_note"]), ("claude-opus-5", None))
        except RecursionError:
            failures.append("a deeply nested log line (depth %d) crashed session_usage with "
                            "RecursionError" % depth)

        base = dict(common, from_bounce=None, out=None, usage=None, model="",
                    session_logs=root, session_issue="REV-3", session_role="run",
                    no_session="", model_note="", cost_note="")
        with open(os.path.join(root, "review.json"), "w", encoding="utf-8") as fh:
            json.dump(GOOD_REVIEW, fh)
        ns = argparse.Namespace(**dict(base, from_review=os.path.join(root, "review.json")))
        _, body, _, _ = build_batch(ns)
        row = tb.scan(body)["blocks"][0]["runs"][0]
        check("end to end: a review row names the model that ran",
              (row["model"], row["model_note"]), ("claude-opus-5", None))
        check("end to end: a review row carries the real cost",
              (row["cost_usd"], row["cost_note"], row["turns"]), (0.9132, None, 7))
        check("end to end: the posted body passes the gate", tb.gate([body], True)["ok"], True)

        ns = argparse.Namespace(**dict(base, from_review=os.path.join(root, "review.json"),
                                       session_issue="REV-404", model="claude-sonnet-5"))
        row = tb.scan(build_batch(ns)[1])["blocks"][0]["runs"][0]
        check("a configured model stands in for a missing log, and says so",
              (row["model"], "taken from configuration" in (row["model_note"] or "")),
              ("claude-sonnet-5", True))

        ns = argparse.Namespace(**dict(base, from_review=None,
                                       from_bounce=os.path.join(root, "bounce.json"),
                                       session_issue="", no_session="a conclusion starts no "
                                                                     "model session"))
        with open(os.path.join(root, "bounce.json"), "w", encoding="utf-8") as fh:
            json.dump(GOOD_BOUNCE, fh)
        row = tb.scan(build_batch(ns)[1])["blocks"][0]["runs"][0]
        check("--no-session: model unknown with the reason, and an exact zero with no cost_note",
              (row["model"], row["model_note"], row["cost_note"], row["cost_usd"]),
              ("unknown", "a conclusion starts no model session", None, 0.0))
        check("the not-incurred note says Stage E never records the fix run's cost, in one "
              "stored line", ("never records" in NOT_INCURRED, "(no ticket yet)" in NOT_INCURRED,
                              len(NOT_INCURRED) <= 200), (True, True, True))

    # 6. The write guard: this file's only Linear mutation is commentCreate. Each
    #    banned name below appears exactly once in this file — right here, in this
    #    check list. A count above one means a real second mutation slipped into the
    #    code above (the same idiom pipeline_review_local.py uses for its own guard).
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    for banned in ("issueAddLabel", "issueUpdate", "issueCreate", "issueArchive",
                   "issueDelete", "workflowStateUpdate", "projectUpdate"):
        if src.count(banned) > 1:
            failures.append("source names a Linear mutation beyond commentCreate: %r" % banned)
    if src.count("commentCreate") < 1:
        failures.append("commentCreate mutation is missing entirely")

    if failures:
        print("FAIL pipeline_telemetry_local selftest:")
        for f in failures:
            print("  -", f)
        return 1
    print("ok — pipeline_telemetry_local: builds via telemetry_block.py (one definition, "
          "both review and bounce), posts exactly one comment to the pinned ticket, no "
          "other Linear write path exists")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Stage E telemetry publisher (KIT-94).")
    p.add_argument("--from-review", metavar="FINDINGS", help="a pipeline-review/1 artifact")
    p.add_argument("--from-bounce", metavar="ARTIFACT", help="a bounce-outcome artifact")
    p.add_argument("--team-key", help="e.g. KIT")
    p.add_argument("--model", help="exact model id as used")
    p.add_argument("--auth-mode", default="api-key", choices=["subscription", "api-key"])
    p.add_argument("--run-id", help="e.g. r_<ULID>")
    p.add_argument("--dispatch-id", default="")
    p.add_argument("--started-at", help="ISO-8601 UTC")
    p.add_argument("--ended-at", help="ISO-8601 UTC")
    p.add_argument("--usage", help="a Claude Code execution log, for cost and turns")
    p.add_argument("--session-logs", default="",
                   help="the dispatcher's session-log directory (<home>/logs)")
    p.add_argument("--session-issue", default="",
                   help="the issue identifier whose session log to read, e.g. REV-3")
    p.add_argument("--session-role", default="run", choices=list(SESSION_ROLES),
                   help="run: this row is that session's run; resumed: it precedes the run")
    p.add_argument("--no-session", default="",
                   help="REASON no model session belongs to this row (model becomes unknown)")
    p.add_argument("--model-note", default="", help="§4 model_note, overriding a derived one")
    p.add_argument("--cost-note", default="", help="§4 cost_note, overriding a derived one")
    p.add_argument("--reviewer-outcome", default="", help="review runs only")
    p.add_argument("--out", help="also write the built §8 batch here")
    p.add_argument("--api-key-env", default="LINEAR_API_KEY",
                   help="NAME of the env var holding the key — never the key itself")
    p.add_argument("--dry-run", action="store_true", help="build (and write --out) but post nothing")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)

    if args.selftest:
        return selftest()
    if not args.from_review and not args.from_bounce:
        p.error("one of --from-review/--from-bounce is required (or --selftest)")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
