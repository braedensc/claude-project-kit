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

Usage:
    pipeline_telemetry_local.py --from-review FINDINGS.json | --from-bounce ARTIFACT.json
                                --team-key KIT --model ID --auth-mode api-key
                                --run-id r_... --started-at ISO --ended-at ISO
                                [--dispatch-id ID] [--usage EXECUTION.json]
                                [--reviewer-outcome success] [--out REQUESTS.json]
                                [--api-key-env LINEAR_API_KEY] [--dry-run]
    pipeline_telemetry_local.py --selftest

Exit: 0 = built (and posted, unless --dry-run)
      1 = the block or the §8 envelope around it is malformed — refused, nothing posted
      2 = usage/IO/API error (bad arguments, no credential, Linear unreachable)
"""
import argparse
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
        execution = _read_usage(args.usage)
        block = tb.review_block(artifact, args.team_key, args.model, args.auth_mode,
                                args.run_id, args.started_at, args.ended_at,
                                dispatch_id=args.dispatch_id or None, execution=execution,
                                reviewer_outcome=args.reviewer_outcome or None)
        problems = tb.validate_block(block)
        if problems:
            raise ValueError("review telemetry block is malformed: " + "; ".join(problems))
        notes = []
        body = tb.review_comment(artifact, block, notes)
    elif args.from_bounce:
        with open(args.from_bounce, encoding="utf-8") as fh:
            artifact = json.load(fh)
        execution = _read_usage(args.usage)
        block = tb.bounce_block(artifact, args.team_key, args.model, args.auth_mode,
                                args.run_id, args.started_at, args.ended_at,
                                dispatch_id=args.dispatch_id or None, execution=execution)
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
