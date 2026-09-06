#!/usr/bin/env python3
"""Stage E PR poller — the trigger that launches a review session per new pipeline PR.

Runs on the dispatcher's machine (never inside a coding session), lists the managed
repository's open pull requests, selects the ones that are newly-opened pipeline ticket
branches, and — for each — invokes the merged reviewer core (`pipeline_review_local.py`,
KIT-90) in a fresh bundle. See
docs/adr/2026-09-05-stage-e-under-a-delegation-bound-dispatcher.md, decision 1.

WHY A POLLER, AND WHY IT NEVER TOUCHES CYRUS

  Cyrus's GitHub transport accepts exactly `issue_comment`, `pull_request_review_comment`,
  `pull_request_review` and `push` — a PR *opening* starts nothing there, and wiring its
  webhook to `pull_request` was refused on security grounds (both managed repos are
  public, so a fork's branch name or a stranger's comment is attacker-reachable text; see
  the ADR's "Rejected" list). So something else has to speak first, on a schedule, reading
  only the GitHub API. This file is that something — and it is a second, tiny dispatcher
  whose only job is review, never a modification of the real one.

  This script imports nothing from Cyrus, reads no path under `/opt/clawdispatch`, and
  assumes no Cyrus worktree exists. Its ONE coupling to the wider system is the same one
  the ADR names for all of Stage E: a ticket may already be terminal (KIT-51), which is the
  reviewer's problem to skip gracefully, not this poller's.

THE "OPENED-ONLY" RULE, AND HOW IT SURVIVES A RESTART

  `templates/workflows/pipeline-review.yml` reviews on `pull_request: types: [opened]`
  only, never `synchronize` — a bounce pushes commits, and reviewing on every push would
  multiply review cost by the number of fix pushes, exactly the loop the bounce budget
  exists to bound. A poller has no event stream to filter by type; it only ever sees
  "which PRs are open right now". So the rule here is the durable SEEN-SET: once a PR
  number has been handed to the reviewer, it is marked seen forever, regardless of the
  reviewer's own exit code — a crashed or declined review still counts as "the poller
  spoke", so a flaky first attempt does not turn into review-on-every-poll. The set is a
  daemon-owned file OUTSIDE every worktree (default `~/.claude/pipeline/stage-e/`,
  sibling to `dispatch.pinsRoot`), so a restart never re-reviews the backlog.

WHAT IT IS AND IS NOT

  It is: PR listing, the fork/branch/seen filter, and one subprocess launch per selected
  PR, with a persistent per-PR bundle directory so a later bounce decision (KIT-93) can
  read what the review found without any code coupling between the two scripts — purely a
  shared directory convention.

  It is not: the reviewer itself (KIT-90, already merged), the basis resolver (KIT-92 —
  this poller passes NO --basis-file by default, so an un-augmented reviewer declines
  exactly as it does today; wiring KIT-92 in is one flag, added once that PR lands), the
  telemetry emitter (KIT-94), the bounce loop (KIT-93), or any launchd/service wiring
  (KIT-95 — this file is started BY a service, it does not become one).

Usage:
    pipeline_review_poller.py [--repo O/R] [--team-key KIT ...] [--state-dir DIR]
                              [--limit N] [--model ID] [--reviewer-script PATH]
                              [--timeout SECONDS] [--dry-run]
    pipeline_review_poller.py --selftest

Exit: 0 = ran (including "nothing to review" — a real answer, not a failure)
      1 = could not even determine what to review (repo unresolvable, PR listing failed)
      2 = usage error
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gh_fallback  # noqa: E402  (try_gh / resolve_repo / api / Failure — the TLS-safe transport)
from pipeline_review_local import resolve_ticket  # noqa: E402  (ONE branch->ticket parser)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

SEEN_SCHEMA = "pipeline-review-poller-seen/1"
DEFAULT_STATE_DIR = "~/.claude/pipeline/stage-e"
DEFAULT_LIMIT = 100
DEFAULT_TIMEOUT = 1800  # 30 minutes wall clock for one reviewer invocation


def default_state_dir():
    return os.path.realpath(os.path.expanduser(DEFAULT_STATE_DIR))


def seen_path(state_dir):
    return os.path.join(state_dir, "seen-prs.json")


def bundle_dir_for(state_dir, pr_number):
    """Where a review's inputs/outputs are staged, kept AFTER the run.

    Deliberately not a tempdir: KIT-93's bounce decision reads
    REVIEW-FINDINGS.json back out of this same directory later, purely via this
    path convention — no import, no code coupling between the two scripts, so
    either can ship, merge, and run without the other existing.
    """
    return os.path.join(state_dir, "reviews", "pr-%d" % pr_number)


# --------------------------------------------------------------------------- #
# Pure logic — no I/O, so --selftest can exercise all of it
# --------------------------------------------------------------------------- #
def select_new_reviews(prs, seen, team_keys):
    """The PRs this poll should launch a review for, in list order.

    Three filters, in the order the ticket's test plan names them: fork PRs are never
    ours to review (the token cannot push to them and the branch name is
    attacker-reachable text on a public repo); a PR whose branch resolve_ticket does not
    recognize was not dispatched by this pipeline; and a PR number already in `seen` was
    handed to the reviewer on a prior poll, whatever the outcome — the whole point of the
    opened-only rule.
    """
    selected = []
    for pr in prs or []:
        if not isinstance(pr, dict):
            continue
        if pr.get("isCrossRepository"):
            continue
        number = pr.get("number")
        if not isinstance(number, int) or number in seen:
            continue
        ticket = resolve_ticket(pr.get("headRefName") or "", team_keys or [])
        if ticket is None:
            continue
        row = dict(pr)
        row["ticket_id"] = ticket
        selected.append(row)
    return selected


def load_seen(path):
    """The seen-set, or empty when the file is absent, unreadable, or foreign.

    A missing file is the FIRST-EVER-POLL state, not an error — §13's "nothing to do"
    vs "could not do it" applies to the FILE, and an absent seen-set is not a failure of
    this poller; it is the honest starting condition.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return set()
    if not isinstance(doc, dict) or doc.get("schema") != SEEN_SCHEMA:
        return set()
    out = set()
    for n in doc.get("seen") or []:
        if isinstance(n, bool):
            continue
        if isinstance(n, int):
            out.add(n)
        elif isinstance(n, float) and n.is_integer():
            out.add(int(n))
    return out


def save_seen(path, seen):
    """Write-then-rename, matching the pin's own write protocol (§3): a reader must
    never see a half-written seen-set, and a crash mid-write must not corrupt it."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    doc = {"schema": SEEN_SCHEMA, "seen": sorted(seen)}
    tmp = path + ".tmp-%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# I/O — GitHub reads (through gh_fallback's TLS-safe transport) and the reviewer subprocess
# --------------------------------------------------------------------------- #
def list_open_prs(owner_repo, limit=DEFAULT_LIMIT):
    """Open PRs on `owner_repo`, in the shape `select_new_reviews` expects.

    Tries `gh` first (gh_fallback.try_gh never raises), and falls back to the same REST
    path gh_fallback.py itself falls back to when `gh` cannot verify TLS inside the
    sandbox (KIT-71) — reusing its `api()` helper rather than re-implementing the
    TLS-honouring transport a second time.
    """
    ok, out, err = gh_fallback.try_gh([
        "pr", "list", "--repo", owner_repo, "--state", "open",
        "--json", "number,headRefName,isCrossRepository,createdAt,url",
        "--limit", str(limit),
    ])
    if ok:
        try:
            data = json.loads(out)
        except ValueError as exc:
            raise gh_fallback.Failure("gh pr list returned unparseable JSON: %s" % exc)
        if not isinstance(data, list):
            raise gh_fallback.Failure("gh pr list returned a non-list JSON value")
        return data
    owner, repo = owner_repo.split("/", 1)
    data = gh_fallback.api("GET", "/repos/%s/%s/pulls?state=open&per_page=%d" % (owner, repo, limit))
    if not isinstance(data, list):
        raise gh_fallback.Failure("GitHub REST returned a non-list value for open PRs")
    out_rows = []
    for p in data:
        head = p.get("head") or {}
        head_repo = (head.get("repo") or {}).get("full_name")
        out_rows.append({
            "number": p.get("number"),
            "headRefName": head.get("ref") or "",
            "isCrossRepository": head_repo is not None and head_repo != owner_repo,
            "createdAt": p.get("created_at"),
            "url": p.get("html_url"),
        })
    return out_rows


def default_reviewer_argv(reviewer_script, pr_number, owner_repo, bundle_dir, model):
    argv = [sys.executable, reviewer_script, "--pr", str(pr_number), "--bundle-dir", bundle_dir]
    if owner_repo:
        argv += ["--repo", owner_repo]
    if model:
        argv += ["--model", model]
    return argv


def invoke_reviewer(argv, timeout):
    """Run one reviewer invocation. Never raises: a launch failure is reported, not
    crashed on — the poller's job is to keep polling, not to die on one bad PR."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return None, "", "reviewer timed out after %ds" % timeout
    except OSError as exc:
        return None, "", "could not launch the reviewer: %s" % exc


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
def poll(args):
    state_dir = os.path.realpath(os.path.expanduser(args.state_dir or DEFAULT_STATE_DIR))
    seen_file = seen_path(state_dir)
    seen = load_seen(seen_file)

    try:
        owner, repo = gh_fallback.resolve_repo(args.repo)
    except gh_fallback.Failure as exc:
        sys.stderr.write("FAIL: could not resolve the managed repository: %s\n" % exc)
        return EXIT_ERROR
    owner_repo = "%s/%s" % (owner, repo)

    try:
        prs = list_open_prs(owner_repo, args.limit)
    except gh_fallback.Failure as exc:
        # A listing failure is "could not do it", never zero PRs found (§13) — the two
        # look identical downstream (nothing gets reviewed) unless this line is loud.
        sys.stderr.write("FAIL: could not list open PRs on %s: %s\n" % (owner_repo, exc))
        return EXIT_ERROR

    selected = select_new_reviews(prs, seen, args.team_key or [])
    if not selected:
        print("nothing to review — %d open PR(s) on %s, 0 new pipeline PR(s)"
              % (len(prs), owner_repo))
        return EXIT_OK

    reviewer_script = args.reviewer_script or os.path.join(HERE, "pipeline_review_local.py")
    for pr in selected:
        number = pr["number"]
        if args.dry_run:
            print("[dry-run] would launch a review for PR #%d (%s) — nothing launched"
                  % (number, pr["ticket_id"]))
            continue
        bundle_dir = bundle_dir_for(state_dir, number)
        os.makedirs(bundle_dir, exist_ok=True)
        argv = default_reviewer_argv(reviewer_script, number, owner_repo, bundle_dir, args.model)
        rc, out, err = invoke_reviewer(argv, args.timeout)
        # Marked seen regardless of outcome: a declined or crashed review still counts
        # as "the poller spoke to this PR" — the opened-only rule, held even on failure.
        seen.add(number)
        print("reviewed PR #%d (%s): reviewer exit %r" % (number, pr["ticket_id"], rc))
        if out:
            sys.stdout.write(out)
        if err:
            sys.stderr.write(err)
    if not args.dry_run:
        save_seen(seen_file, seen)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
def selftest():
    failures = []

    def check(name, got, want):
        if got != want:
            failures.append("%s: got %r, want %r" % (name, got, want))

    # 1. The fixture from the ticket's own test plan: fork / non-ticket branch /
    #    already-seen / new ticket PR -> selects exactly the new ticket PR.
    fixture = [
        {"number": 1, "headRefName": "feat/kit-1-forked-work", "isCrossRepository": True},
        {"number": 2, "headRefName": "claude/some-codename", "isCrossRepository": False},
        {"number": 3, "headRefName": "feat/kit-3-already-seen", "isCrossRepository": False},
        {"number": 4, "headRefName": "feat/kit-4-brand-new", "isCrossRepository": False},
    ]
    seen = {3}
    selected = select_new_reviews(fixture, seen, ["KIT"])
    check("selects exactly one PR", len(selected), 1)
    if selected:
        check("selects the new ticket PR", selected[0]["number"], 4)
        check("annotates the ticket id", selected[0]["ticket_id"], "KIT-4")

    check("re-poll selects nothing once seen", select_new_reviews(fixture, seen | {4}, ["KIT"]), [])
    check("a wrong team key is filtered out", select_new_reviews(
        [{"number": 9, "headRefName": "feat/tod-9-x", "isCrossRepository": False}], set(), ["KIT"]), [])
    check("an empty PR list selects nothing", select_new_reviews([], set(), ["KIT"]), [])
    check("a non-dict PR row is ignored, not a crash",
          select_new_reviews([None, 42, "x"], set(), ["KIT"]), [])

    # 2. The seen-set: round-trips, degrades to empty rather than crashing, and the
    #    write protocol leaves the marker a reader can check.
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "nested", "seen-prs.json")
        save_seen(path, {1, 2, 3})
        check("seen-set round-trips", load_seen(path), {1, 2, 3})
        with open(path, encoding="utf-8") as fh:
            check("seen-set carries its schema marker", json.load(fh).get("schema"), SEEN_SCHEMA)
    check("a missing seen-set reads as empty, not a crash",
          load_seen(os.path.join(tempfile.gettempdir(), "definitely-not-here-%d" % os.getpid())), set())
    check("a foreign JSON file is not mistaken for a seen-set",
          (lambda p: (open(p, "w").write('{"schema":"something-else"}'), load_seen(p))[1])(
              os.path.join(tempfile.mkdtemp(), "x.json")), set())

    # 3. The driver end to end, with GitHub reads and the reviewer subprocess both
    #    stubbed — this test must run with no network and no real `claude` process.
    saved = {k: globals()[k] for k in ("list_open_prs", "invoke_reviewer")}
    launched = []
    try:
        globals()["list_open_prs"] = lambda owner_repo, limit=100: fixture

        def fake_invoke(argv, timeout):
            launched.append(argv)
            return 0, "", ""
        globals()["invoke_reviewer"] = fake_invoke

        with tempfile.TemporaryDirectory() as tmp:
            # PR #3 was already reviewed on a prior poll (pre-seeded, exactly like a
            # real restart reading its seen-set back) — only #4 should launch here.
            save_seen(seen_path(tmp), {3})
            ns = argparse.Namespace(repo="o/r", state_dir=tmp, team_key=["KIT"], limit=100,
                                     model=None, reviewer_script=None, dry_run=False,
                                     timeout=5)
            check("driver exit is OK", poll(ns), EXIT_OK)
            check("driver launches exactly one review", len(launched), 1)
            check("driver marks the PR seen", load_seen(seen_path(tmp)), {3, 4})
            check("driver leaves a persistent bundle dir",
                  os.path.isdir(bundle_dir_for(tmp, 4)), True)

            # Re-polling the SAME open-PR list a second time launches nothing new —
            # the opened-only rule surviving a second run, not just a second call.
            rc2 = poll(ns)
            check("second poll is still OK", rc2, EXIT_OK)
            check("second poll launches nothing new", len(launched), 1)

        # dry-run launches nothing and marks nothing seen.
        launched.clear()
        with tempfile.TemporaryDirectory() as tmp:
            ns = argparse.Namespace(repo="o/r", state_dir=tmp, team_key=["KIT"], limit=100,
                                     model=None, reviewer_script=None, dry_run=True, timeout=5)
            check("dry-run exits OK", poll(ns), EXIT_OK)
            check("dry-run launches nothing", launched, [])
            check("dry-run marks nothing seen", load_seen(seen_path(tmp)), set())

        # A listing failure is LOUD, never "0 PRs found" (§13).
        def fail_list(owner_repo, limit=100):
            raise gh_fallback.Failure("simulated GitHub outage")
        globals()["list_open_prs"] = fail_list
        with tempfile.TemporaryDirectory() as tmp:
            ns = argparse.Namespace(repo="o/r", state_dir=tmp, team_key=["KIT"], limit=100,
                                     model=None, reviewer_script=None, dry_run=False, timeout=5)
            check("a listing failure is a loud non-zero exit", poll(ns), EXIT_ERROR)
    finally:
        globals().update(saved)

    # 4. An unresolvable repo is a loud failure too, not a silent "nothing to review".
    with tempfile.TemporaryDirectory() as tmp:
        ns = argparse.Namespace(repo="not-a-valid-repo-string", state_dir=tmp, team_key=["KIT"],
                                 limit=100, model=None, reviewer_script=None, dry_run=False,
                                 timeout=5)
        check("an unresolvable --repo is a loud failure", poll(ns), EXIT_ERROR)

    # 5. The approve/merge/label guard — this file constructs no such path, ever. Each
    #    token below appears exactly once in this file: right here. A count above one
    #    means a real approve/merge/label call slipped into the code elsewhere.
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    for banned in ("gh pr merge", "--approve", "issueAddLabel", "addLabels",
                   "pr edit --add-label", "createReview", "auto-merge"):
        if src.count(banned) > 1:
            failures.append("source names a forbidden op: %r" % banned)

    if failures:
        print("FAIL pipeline_review_poller selftest:")
        for f in failures:
            print("  -", f)
        return 1
    print("ok — pipeline_review_poller: opened-only selection, seen-set survives a "
          "restart, listing failures are loud, no approve/label/merge path")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Stage E PR poller (trigger for the local reviewer).")
    p.add_argument("--repo", help="OWNER/REPO (default: the cwd's origin remote)")
    p.add_argument("--team-key", action="append", help="a managed team key (repeatable)")
    p.add_argument("--state-dir", help="daemon-owned state root (default %s)" % DEFAULT_STATE_DIR)
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="max open PRs to list")
    p.add_argument("--model", help="reviewer model override (else the reviewer's own default)")
    p.add_argument("--reviewer-script", help="path to pipeline_review_local.py (default: alongside this file)")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="reviewer wall-clock seconds")
    p.add_argument("--dry-run", action="store_true", help="list and select only; launch nothing")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)

    if args.selftest:
        return selftest()
    return poll(args)


if __name__ == "__main__":
    sys.exit(main())
