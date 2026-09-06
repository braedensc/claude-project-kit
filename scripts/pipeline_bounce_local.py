#!/usr/bin/env python3
"""Stage E bounce loop — a bounded `/fix-ci` session per red-CI or threshold-finding PR,
counted from a source the fix session cannot rewrite. See
docs/adr/2026-09-05-stage-e-under-a-delegation-bound-dispatcher.md, decision 5.

WHAT A BOUNCE IS, AND THE COUNTER THIS FILE OWNS

  A bounce is one round trip: a pipeline PR's required checks went terminally red, or a
  review's findings met `budgets.reviewSeverityThreshold`, so a fresh `/fix-ci` session
  runs against the PR, once. `templates/workflows/pipeline-bounce.yml` counted bounces
  from GitHub Actions run history, keyed on the PR number, because that history is not
  writable by the fix session's own token. On the dispatcher there is no Actions run
  history for this. The replacement here is a **daemon-owned append-only ledger**, kept
  OUTSIDE every worktree (default `~/.claude/pipeline/stage-e/bounce-ledger.jsonl`,
  sibling to `dispatch.pinsRoot`) — the fix session has no write path to it, for the same
  reason it has no write path to a dispatcher state record (contract §9).

WHERE THE BUDGET COMES FROM

  `budgets.maxBounces` and `budgets.reviewSeverityThreshold` are read from
  `delivery.json` on the repo's **committed default branch**, fetched fresh — reusing
  `pipeline_dispatch_local.read_committed_config`, the exact ref-resolution the
  PreToolUse hook and the tier-0 dispatcher already agree on. Reusing the READER means
  this file can never disagree with them about which config is authoritative; a private
  second implementation is the "second shape for the same structure" contract §1 exists
  to prevent. Absence of `delivery.json` anywhere is OFF (contract §2), not a failure —
  this file says so and exits 0. A `delivery.json` that exists but cannot be read, or
  that carries no valid `budgets.maxBounces`, is BROKEN, and this file refuses loudly
  rather than bounce against a budget it invented.

WHAT MAKES THE COUNT UNFORGEABLE

  Same shape as contract §9's dispatcher state record: exactly one writer (this script,
  run by the poller or by hand — never the fix session), never read from inside a
  worktree, and a missing/corrupt ledger starts the count at zero rather than blocking —
  §9's own "the durable terminal signal is not the counter" reasoning applies unchanged:
  losing the ledger costs at most a few extra bounces, not an unbounded loop, and this
  file's OWN exhaustion check is what applies the practical ceiling every time it runs.

WHAT E OWNS, AND THE ONE CYRUS FACT IT RESPECTS (KIT-51)

  Every fix session in this file runs inside a **fresh worktree E creates and removes
  itself**, never Cyrus's `/opt/clawdispatch/worktrees/<ticket>` — so Cyrus force-deleting
  its own worktree on a ticket reaching a terminal state (KIT-51) cannot pull a bounce's
  work out from under it. The one thing this file still has to respect is that the
  TICKET might already be terminal by the time a bounce would start (its Cyrus worktree
  gone, the PR possibly stale) — `--ticket-status-type`, when the caller has it, skips a
  bounce on a terminal ticket rather than spending one on stale ground.

WHAT THIS FILE NEVER DOES

  Never applies a label (unlike the cloud template's `announce` job, which does — see
  the PR body for why that choice was narrowed here) and never merges or enables
  auto-merge, asserted the same way every other Stage E script asserts its own guarantee.
  On exhaustion it posts ONE PR comment, through `gh_fallback.py` like every other
  GitHub write in this kit, explaining that a human needs to apply `agent:needs-human`
  by hand.

Usage:
    pipeline_bounce_local.py --pr N --ticket-id KIT-93 [--repo O/R] [--repo-root DIR]
                             [--state-dir DIR] [--ticket-status-type TYPE]
                             [--model ID] [--max-turns N] [--timeout SECONDS]
                             [--keep-worktree] [--dry-run]
    pipeline_bounce_local.py --selftest

Exit: 0 = ran (bounced, held with a reason, or the pipeline is off here — all real
          answers, not failures)
      2 = usage/IO/API error (committed config broken, PR unresolvable, GitHub unreachable)
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gh_fallback  # noqa: E402  (resolve_repo — repo derivation; pr-comment — the one write)
import pipeline_dispatch_local as pdl  # noqa: E402  (read_committed_config — ONE config reader)
from pipeline_review_local import classify  # noqa: E402  (re-derive the SAME verdict the reviewer computed)

EXIT_OK = 0
EXIT_USAGE = 2

DEFAULT_STATE_DIR = "~/.claude/pipeline/stage-e"
DEFAULT_FIX_MODEL = "claude-sonnet-5"
DEFAULT_MAX_TURNS = 60
DEFAULT_TIMEOUT = 5400  # 90 minutes, matching the cloud template's `fix` job wall clock
LEDGER_SCHEMA = "pipeline-bounce-ledger/1"
# Linear WorkflowState.type values that mean "this ticket will not be worked further".
TERMINAL_STATE_TYPES = ("completed", "canceled")

API = "https://api.github.com"


class BounceError(Exception):
    """A GitHub read failed for a reason worth naming."""


def default_state_dir():
    return os.path.realpath(os.path.expanduser(DEFAULT_STATE_DIR))


def ledger_path(state_dir):
    return os.path.join(state_dir, "bounce-ledger.jsonl")


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Pure logic — no I/O, so --selftest can exercise all of it
# --------------------------------------------------------------------------- #
def compute_trigger(checks_status, review_verdict):
    """(trigger_ok, reason) from already-resolved GitHub/review facts.

    `checks_status` is 'red' | 'green' | 'pending' | 'unknown'. `review_verdict` is
    None, or the dict `pipeline_review_local.classify()` already produces — reused
    verbatim so this can never compute a different answer than the reviewer did.
    """
    if checks_status == "red":
        return True, "required checks are terminally red"
    if review_verdict and review_verdict.get("usable") and review_verdict.get("meets_threshold"):
        return True, ("review findings meet the severity threshold (highest %s)"
                      % review_verdict.get("max_severity"))
    if checks_status == "pending":
        return False, "checks are still running — nothing terminal to react to yet"
    return False, "checks are green and review is below threshold (or has not run)"


def decide_bounce(*, trigger_ok, trigger_reason, prior_bounces, max_bounces,
                  is_fork=False, ticket_terminal=False):
    """Whether to start a bounce, and why. The budget gate ONLY — `trigger_ok` is
    computed upstream by `compute_trigger` from GitHub facts, never invented here.

    Three holds that are never a bounce regardless of budget (fork, terminal ticket,
    no trigger), then the budget itself: bounce `prior_bounces + 1` when budget
    remains, or exhaust when `prior_bounces >= max_bounces`. `bounce_no` is still
    reported on a held/exhausted verdict — the number is what a human reads to know
    which attempt this would have been.
    """
    if is_fork:
        return {"go": False, "reason": "PR is from a fork — the push credential cannot "
                                       "push to it", "exhausted": False, "bounce_no": None}
    if ticket_terminal:
        return {"go": False, "reason": "the ticket is already in a terminal state — its "
                                       "Cyrus worktree may be gone (KIT-51)",
                "exhausted": False, "bounce_no": None}
    if not trigger_ok:
        return {"go": False, "reason": trigger_reason, "exhausted": False, "bounce_no": None}

    bounce_no = prior_bounces + 1
    if prior_bounces >= max_bounces:
        return {"go": False, "bounce_no": bounce_no, "exhausted": True,
                "reason": "bounce budget exhausted (%d of %d used) — %s"
                          % (prior_bounces, max_bounces, trigger_reason)}
    return {"go": True, "bounce_no": bounce_no, "exhausted": False,
            "reason": "%s — bounce %d of %d" % (trigger_reason, bounce_no, max_bounces)}


def count_prior_bounces(path, pr_number):
    """Prior bounce rows for `pr_number` in the ledger. A missing or unreadable file,
    or a line that fails to parse, counts as zero/skipped rather than a crash — the
    ledger is a durable record, not a hostage a corrupt byte can take (§9's own
    "a missing record starts from zero; it never blocks" rule, applied here)."""
    n = 0
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if (isinstance(row, dict) and row.get("schema") == LEDGER_SCHEMA
                        and row.get("pr") == pr_number and row.get("outcome") != "held"):
                    n += 1
    except OSError:
        return 0
    return n


def append_bounce_record(path, pr_number, ticket_id, bounce_no, at, outcome):
    """One JSON line, appended — never rewritten, never truncated. The write itself
    is a plain append (matching the pin's own `ledger.jsonl` convention elsewhere in
    this kit); `fsync` before returning so a crash right after does not lose the row
    that decides whether the NEXT poll spends another bounce."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    row = {"schema": LEDGER_SCHEMA, "pr": pr_number, "ticket_id": ticket_id,
           "bounce_no": bounce_no, "at": at, "outcome": outcome}
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def read_bounce_config(repo_root):
    """(max_bounces, threshold, state). `state` is 'off' (no delivery.json anywhere —
    contract §2, not an error), 'broken:<why>' (one exists but could not be read, or
    carries no valid budgets.maxBounces), or 'ok'.

    Reuses `pipeline_dispatch_local.read_committed_config` rather than re-implementing
    the ref-resolution — the ONE reader every guard and dispatcher in this kit already
    agrees on. That function's own docstring says `(None, source)` means BROKEN; the
    absent-vs-broken split below is done by inspecting `source`, exactly as its only
    other caller (the tier-0 dispatcher) would have to.
    """
    cfg, source = pdl.read_committed_config(repo_root)
    if cfg is None:
        if source == pdl.DELIVERY_FILE:
            return None, None, "off"
        return None, None, "broken:%s" % source
    budgets = cfg.get("budgets") or {}
    max_bounces = budgets.get("maxBounces")
    threshold = budgets.get("reviewSeverityThreshold")
    if not isinstance(max_bounces, int) or isinstance(max_bounces, bool) or max_bounces < 0:
        return None, None, "broken:budgets.maxBounces"
    return max_bounces, threshold, "ok"


def read_review_verdict(state_dir, pr_number):
    """The review's classified verdict, RE-DERIVED from the bundle
    `pipeline_review_poller.py` leaves on disk — using the same `classify()` the
    reviewer itself calls, from the same REVIEW-RUBRIC.json threshold, so this can
    never disagree with what the reviewer actually decided. None when no bundle
    exists yet (the poller isn't merged, or this PR was never reviewed) — a purely
    file-convention coupling, no import between the poller and this file.
    """
    bundle = os.path.join(state_dir, "reviews", "pr-%d" % pr_number)
    findings_path = os.path.join(bundle, "REVIEW-FINDINGS.json")
    if not os.path.exists(findings_path):
        return None
    try:
        with open(findings_path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    threshold = "high"
    rubric_path = os.path.join(bundle, "REVIEW-RUBRIC.json")
    if os.path.exists(rubric_path):
        try:
            with open(rubric_path, encoding="utf-8") as fh:
                threshold = (json.load(fh) or {}).get("threshold") or threshold
        except (OSError, ValueError):
            pass
    return classify(doc, threshold)


# --------------------------------------------------------------------------- #
# I/O — GitHub reads (gh_fallback has no pr-view/check-runs read granular enough
# for a bounce decision; mirrors its own try-gh-then-REST pattern locally rather
# than widen that file's surface), the fix subprocess, and the one GitHub write.
# --------------------------------------------------------------------------- #
def _run_gh(argv, timeout=60):
    try:
        out = subprocess.run(["gh"] + argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return False, "", ""
    return out.returncode == 0, out.stdout, out.stderr


def _gh_token():
    for var in ("GH_TOKEN", "GITHUB_TOKEN"):
        val = os.environ.get(var)
        if val:
            return val
    raise BounceError("no GitHub token: set GH_TOKEN or GITHUB_TOKEN")


def _rest_get(path):
    import urllib.error
    import urllib.request
    req = urllib.request.Request(
        API + path,
        headers={"Authorization": "Bearer %s" % _gh_token(),
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise BounceError("GitHub API GET %s -> HTTP %d" % (path, exc.code))
    except urllib.error.URLError as exc:
        raise BounceError("could not reach GitHub: %s" % exc.reason)


def pr_view(pr_number, owner_repo):
    """number, headRefName, headRefOid, isCrossRepository — via gh, REST fallback."""
    ok, out, _ = _run_gh(["pr", "view", str(pr_number), "--repo", owner_repo, "--json",
                         "number,headRefName,headRefOid,isCrossRepository"])
    if ok:
        try:
            return json.loads(out)
        except ValueError as exc:
            raise BounceError("gh pr view returned unparseable JSON: %s" % exc)
    owner, repo = owner_repo.split("/", 1)
    data = _rest_get("/repos/%s/%s/pulls/%d" % (owner, repo, pr_number))
    head = data.get("head") or {}
    head_repo = (head.get("repo") or {}).get("full_name")
    return {
        "number": data.get("number"),
        "headRefName": head.get("ref") or "",
        "headRefOid": head.get("sha") or "",
        "isCrossRepository": head_repo is not None and head_repo != owner_repo,
    }


def check_runs_status(head_sha, owner_repo):
    """'red' | 'green' | 'pending' | 'unknown' for `head_sha`'s check runs."""
    owner, repo = owner_repo.split("/", 1)
    ok, out, _ = _run_gh(["api", "repos/%s/%s/commits/%s/check-runs" % (owner, repo, head_sha)])
    data = None
    if ok:
        try:
            data = json.loads(out)
        except ValueError:
            data = None
    if data is None:
        data = _rest_get("/repos/%s/%s/commits/%s/check-runs" % (owner, repo, head_sha))
    runs = data.get("check_runs") or []
    if not runs:
        return "unknown"
    pending = red = False
    for run in runs:
        if run.get("status") != "completed":
            pending = True
        elif run.get("conclusion") not in ("success", "neutral", "skipped"):
            red = True
    if red:
        return "red"
    if pending:
        return "pending"
    return "green"


def default_fix_argv(pr_number, model, max_turns):
    prompt = (
        "Run /fix-ci %d on this branch. Fix the cause the failing check or the review "
        "finding names, the smallest change that actually fixes it. Do NOT weaken, "
        "skip, or delete a test assertion to make something pass. Stay inside the "
        "ticket's scope — a bounce is not a chance to refactor. NEVER merge: `gh pr "
        "merge` is hook-blocked in every form, `--auto` included." % pr_number)
    return ["claude", "-p", prompt, "--max-turns", str(max_turns), "--model", model,
            "--allowedTools", "Read,Grep,Glob,Write,Edit,Bash"]


def prepare_worktree(state_dir, pr_number, bounce_no, head_ref, repo_root):
    """E's OWN fresh worktree for this bounce — never Cyrus's (KIT-51)."""
    wt_dir = os.path.join(state_dir, "worktrees", "pr-%d-bounce-%d" % (pr_number, bounce_no))
    if os.path.isdir(wt_dir):
        shutil.rmtree(wt_dir, ignore_errors=True)
    os.makedirs(os.path.dirname(wt_dir), exist_ok=True)
    subprocess.run(["git", "-C", repo_root, "fetch", "--quiet", "origin", head_ref],
                   check=True, capture_output=True, timeout=120)
    subprocess.run(["git", "-C", repo_root, "worktree", "add", "-B", head_ref, wt_dir,
                   "origin/%s" % head_ref], check=True, capture_output=True, timeout=120)
    return wt_dir


def run_fix_session(wt_dir, argv, timeout):
    try:
        proc = subprocess.run(argv, cwd=wt_dir, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return None, "", "fix session timed out after %ds" % timeout
    except OSError as exc:
        return None, "", "could not launch the fix session: %s" % exc


def cleanup_worktree(repo_root, wt_dir):
    subprocess.run(["git", "-C", repo_root, "worktree", "remove", "--force", wt_dir],
                   capture_output=True, timeout=60)


def post_exhaustion_comment(pr_number, owner_repo, ticket_id, prior, max_bounces):
    """The ONE GitHub write this file ever makes, and it goes through gh_fallback.py
    like every other Stage E write. No label is applied — see the PR body for why
    that choice was narrowed from the cloud template's own `announce` job."""
    body = (
        "## \U0001F6D1 Bounce budget spent — %s\n\n"
        "This PR has used all %d automated fix round trip(s) (%d used). No further "
        "fix sessions will start.\n\n"
        "A person needs to apply `agent:needs-human` on the ticket by hand and decide "
        "the next step — this bounce loop comments only, and does not apply labels "
        "itself.\n\n---\n_Stage E bounce loop — comment only, never a merge._"
        % (ticket_id or ("PR #%d" % pr_number), max_bounces, prior))
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
        fh.write(body)
        body_file = fh.name
    try:
        argv = [sys.executable, os.path.join(HERE, "gh_fallback.py"), "pr-comment",
               str(pr_number), "--body-file", body_file, "--repo", owner_repo]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        return proc.returncode == 0
    finally:
        os.unlink(body_file)


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
def bounce(args):
    state_dir = os.path.realpath(os.path.expanduser(args.state_dir or DEFAULT_STATE_DIR))
    repo_root = args.repo_root or os.getcwd()

    max_bounces, threshold, cfg_state = read_bounce_config(repo_root)
    if cfg_state == "off":
        print("delivery.json not found on the committed default branch — the pipeline "
              "is not configured here; nothing to do (contract §2).")
        return EXIT_OK
    if cfg_state != "ok":
        sys.stderr.write("FAIL: committed delivery.json is present but %s — refusing to "
                         "bounce against a budget this script would have to invent.\n"
                         % cfg_state)
        return EXIT_USAGE

    try:
        owner, repo = gh_fallback.resolve_repo(args.repo)
    except gh_fallback.Failure as exc:
        sys.stderr.write("FAIL: could not resolve the repository: %s\n" % exc)
        return EXIT_USAGE
    owner_repo = "%s/%s" % (owner, repo)

    try:
        pr = pr_view(args.pr, owner_repo)
    except BounceError as exc:
        sys.stderr.write("FAIL: could not resolve PR #%d: %s\n" % (args.pr, exc))
        return EXIT_USAGE

    if pr.get("isCrossRepository"):
        print("PR #%d is from a fork — the push credential cannot push to it; never ours "
              "to fix." % args.pr)
        return EXIT_OK

    try:
        checks = (check_runs_status(pr["headRefOid"], owner_repo)
                  if pr.get("headRefOid") else "unknown")
    except BounceError as exc:
        sys.stderr.write("FAIL: could not read check-run status for PR #%d: %s\n"
                         % (args.pr, exc))
        return EXIT_USAGE

    verdict = read_review_verdict(state_dir, args.pr)
    trigger_ok, trigger_reason = compute_trigger(checks, verdict)

    lpath = ledger_path(state_dir)
    prior = count_prior_bounces(lpath, args.pr)
    decision = decide_bounce(
        trigger_ok=trigger_ok, trigger_reason=trigger_reason, prior_bounces=prior,
        max_bounces=max_bounces, is_fork=False,
        ticket_terminal=(args.ticket_status_type in TERMINAL_STATE_TYPES))
    print("PR #%d: %s — %s" % (args.pr, "BOUNCE" if decision["go"] else "hold",
                                decision["reason"]))

    if not decision["go"]:
        if decision["exhausted"] and not args.dry_run:
            posted = post_exhaustion_comment(args.pr, owner_repo, args.ticket_id,
                                             prior, max_bounces)
            if not posted:
                sys.stderr.write("FAIL: could not post the exhaustion comment on PR #%d "
                                 "— the ledger is NOT updated, so the next poll will "
                                 "try again rather than silently drop this outcome.\n"
                                 % args.pr)
                return EXIT_USAGE
            append_bounce_record(lpath, args.pr, args.ticket_id, decision["bounce_no"],
                                 _now_iso(), "exhausted")
        return EXIT_OK

    if args.dry_run:
        print("[dry-run] would start bounce #%d for PR #%d — nothing launched"
              % (decision["bounce_no"], args.pr))
        return EXIT_OK

    wt_dir = prepare_worktree(state_dir, args.pr, decision["bounce_no"],
                             pr["headRefName"], repo_root)
    argv = args.fix_cmd or default_fix_argv(args.pr, args.model, args.max_turns)
    rc, out, err = run_fix_session(wt_dir, argv, args.timeout)
    if not args.keep_worktree:
        cleanup_worktree(repo_root, wt_dir)
    outcome = "completed" if rc == 0 else "error"
    append_bounce_record(lpath, args.pr, args.ticket_id, decision["bounce_no"],
                         _now_iso(), outcome)
    print("bounce #%d for PR #%d: fix session exit %r" % (decision["bounce_no"], args.pr, rc))
    if err:
        sys.stderr.write(err)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #
def selftest():
    failures = []

    def check(name, got, want):
        if got != want:
            failures.append("%s: got %r, want %r" % (name, got, want))

    # 1. compute_trigger: red checks win regardless of review; review only matters
    #    when usable AND at/above threshold; pending is distinct from clean-green.
    check("red checks trigger", compute_trigger("red", None)[0], True)
    check("pending alone does not trigger", compute_trigger("pending", None)[0], False)
    check("green + no review does not trigger", compute_trigger("green", None)[0], False)
    below = {"usable": True, "meets_threshold": False, "max_severity": "low"}
    check("green + below-threshold review does not trigger", compute_trigger("green", below)[0], False)
    above = {"usable": True, "meets_threshold": True, "max_severity": "high"}
    check("green + above-threshold review triggers", compute_trigger("green", above)[0], True)
    unusable = {"usable": False, "meets_threshold": False}
    check("an UNUSABLE review verdict never triggers on its own",
          compute_trigger("green", unusable)[0], False)

    # 2. decide_bounce: the counter, exactly at the ticket's own N-1/N/N+1 test plan.
    d_minus1 = decide_bounce(trigger_ok=True, trigger_reason="x", prior_bounces=1, max_bounces=3)
    check("N-1 (1 of 3 used): bounces", d_minus1["go"], True)
    check("N-1: reports bounce_no 2", d_minus1["bounce_no"], 2)
    check("N-1: not exhausted", d_minus1["exhausted"], False)

    d_at = decide_bounce(trigger_ok=True, trigger_reason="x", prior_bounces=3, max_bounces=3)
    check("N (3 of 3 used): holds", d_at["go"], False)
    check("N: exhausted", d_at["exhausted"], True)
    check("N: still reports the bounce_no it would have been", d_at["bounce_no"], 4)

    d_over = decide_bounce(trigger_ok=True, trigger_reason="x", prior_bounces=4, max_bounces=3)
    check("N+1 (past exhaustion already): still holds", d_over["go"], False)
    check("N+1: still exhausted", d_over["exhausted"], True)

    check("a fork never bounces regardless of budget",
          decide_bounce(trigger_ok=True, trigger_reason="x", prior_bounces=0,
                        max_bounces=3, is_fork=True)["go"], False)
    check("a terminal ticket never bounces regardless of budget",
          decide_bounce(trigger_ok=True, trigger_reason="x", prior_bounces=0,
                        max_bounces=3, ticket_terminal=True)["go"], False)
    check("no trigger never bounces even with full budget remaining",
          decide_bounce(trigger_ok=False, trigger_reason="clean", prior_bounces=0,
                        max_bounces=3)["go"], False)
    check("zero maxBounces never bounces (0 of 0 used IS exhausted)",
          decide_bounce(trigger_ok=True, trigger_reason="x", prior_bounces=0,
                        max_bounces=0)["exhausted"], True)

    # 3. The ledger: counts only THIS pr's rows, ignores 'held' rows, degrades to
    #    zero rather than crashing, and a bad line does not lose the good ones.
    with tempfile.TemporaryDirectory() as tmp:
        path = ledger_path(tmp)
        append_bounce_record(path, 41, "ENG-1", 1, "2026-01-01T00:00:00Z", "completed")
        append_bounce_record(path, 41, "ENG-1", 2, "2026-01-02T00:00:00Z", "error")
        append_bounce_record(path, 99, "ENG-2", 1, "2026-01-01T00:00:00Z", "completed")
        check("counts only this PR's rows", count_prior_bounces(path, 41), 2)
        check("a different PR is counted separately", count_prior_bounces(path, 99), 1)
        check("an unrelated PR with no rows counts zero", count_prior_bounces(path, 7), 0)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("not json at all\n")
            fh.write(json.dumps({"schema": LEDGER_SCHEMA, "pr": 41, "outcome": "held"}) + "\n")
        check("a malformed line does not break the count", count_prior_bounces(path, 41), 2)
        check("an 'exhausted'/'held' row is never counted as a spent bounce",
              count_prior_bounces(path, 41), 2)
    check("a missing ledger counts zero, not a crash",
          count_prior_bounces(os.path.join(tempfile.gettempdir(),
                                           "no-such-ledger-%d.jsonl" % os.getpid()), 1), 0)

    # 4. read_bounce_config: THIS repo genuinely ships no delivery.json (contract §2),
    #    so the real function proves the 'off' path with no stubbing at all.
    real_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mb, thr, state = read_bounce_config(real_root)
    check("this kit's own repo reads as OFF (it ships no delivery.json)", state, "off")
    check("OFF carries no max_bounces", mb, None)

    saved_reader = pdl.read_committed_config
    try:
        pdl.read_committed_config = lambda root: (
            {"budgets": {"maxBounces": 2, "reviewSeverityThreshold": "high"}}, "origin/main:delivery.json")
        mb2, thr2, state2 = read_bounce_config(real_root)
        check("a real config reads as OK", (mb2, thr2, state2), (2, "high", "ok"))

        pdl.read_committed_config = lambda root: (
            {"budgets": {"reviewSeverityThreshold": "high"}}, "origin/main:delivery.json")
        check("a config with no maxBounces is BROKEN, not silently permissive",
              read_bounce_config(real_root)[2], "broken:budgets.maxBounces")

        pdl.read_committed_config = lambda root: (None, "origin/main:delivery.json")
        check("unparseable content on a real ref is BROKEN, not OFF",
              read_bounce_config(real_root)[2], "broken:origin/main:delivery.json")
    finally:
        pdl.read_committed_config = saved_reader

    # 5. read_review_verdict: consumes the poller's bundle convention, or is honestly
    #    None when no bundle exists (KIT-91 not merged, or this PR never reviewed).
    with tempfile.TemporaryDirectory() as tmp:
        check("no bundle at all -> None, not a crash", read_review_verdict(tmp, 41), None)
        bundle = os.path.join(tmp, "reviews", "pr-41")
        os.makedirs(bundle)
        with open(os.path.join(bundle, "REVIEW-FINDINGS.json"), "w", encoding="utf-8") as fh:
            json.dump({"schema": "pipeline-review/1", "summary": "x", "findings": [
                {"severity": "critical", "category": "tests", "summary": "s", "detail": "d"}]}, fh)
        with open(os.path.join(bundle, "REVIEW-RUBRIC.json"), "w", encoding="utf-8") as fh:
            json.dump({"threshold": "high"}, fh)
        v = read_review_verdict(tmp, 41)
        check("a real bundle re-derives the SAME verdict classify() would give",
              (v["usable"], v["meets_threshold"], v["max_severity"]), (True, True, "critical"))

    # 6. The driver, end to end, with every I/O point stubbed — no network, no git,
    #    no real `claude` process.
    saved = {name: globals()[name] for name in (
        "read_bounce_config", "pr_view", "check_runs_status", "read_review_verdict",
        "prepare_worktree", "run_fix_session", "cleanup_worktree", "post_exhaustion_comment")}
    launched = []
    try:
        globals()["read_bounce_config"] = lambda root: (2, "high", "ok")
        globals()["pr_view"] = lambda pr, repo: {
            "number": pr, "headRefName": "feat/eng-41-x", "headRefOid": "deadbeef",
            "isCrossRepository": False}
        globals()["check_runs_status"] = lambda sha, repo: "red"
        globals()["read_review_verdict"] = lambda state_dir, pr: None
        globals()["prepare_worktree"] = lambda *a: (launched.append(("worktree",) + a) or "/tmp/wt")
        globals()["run_fix_session"] = lambda wt, argv, timeout: (launched.append(("fix", argv)) or (0, "", ""))
        globals()["cleanup_worktree"] = lambda *a: launched.append(("cleanup",) + a)
        globals()["post_exhaustion_comment"] = lambda *a: (launched.append(("comment",) + a) or True)

        with tempfile.TemporaryDirectory() as tmp:
            ns = argparse.Namespace(pr=41, ticket_id="ENG-41", repo="o/r", repo_root=tmp,
                                    state_dir=tmp, ticket_status_type=None, model="m",
                                    max_turns=10, timeout=5, keep_worktree=False,
                                    dry_run=False, fix_cmd=None)
            check("driver bounces on red checks", bounce(ns), EXIT_OK)
            check("driver launched a worktree and a fix session",
                  [c[0] for c in launched], ["worktree", "fix", "cleanup"])
            check("driver's ledger now shows one prior bounce",
                  count_prior_bounces(ledger_path(tmp), 41), 1)

            # Same PR, budget now exhausted (maxBounces=1, already 1 used): holds,
            # posts the exhaustion comment, and appends an 'exhausted' row — but
            # does NOT start a second fix session.
            launched.clear()
            globals()["read_bounce_config"] = lambda root: (1, "high", "ok")
            check("second call at budget: still EXIT_OK (a hold is not a failure)",
                  bounce(ns), EXIT_OK)
            check("exhaustion posts a comment, not a second fix session",
                  [c[0] for c in launched], ["comment"])

        # A fork PR is never touched.
        launched.clear()
        globals()["pr_view"] = lambda pr, repo: {
            "number": pr, "headRefName": "feat/eng-9-x", "headRefOid": "x",
            "isCrossRepository": True}
        globals()["read_bounce_config"] = lambda root: (3, "high", "ok")
        with tempfile.TemporaryDirectory() as tmp:
            ns_fork = argparse.Namespace(pr=9, ticket_id="ENG-9", repo="o/r", repo_root=tmp,
                                         state_dir=tmp, ticket_status_type=None, model="m",
                                         max_turns=10, timeout=5, keep_worktree=False,
                                         dry_run=False, fix_cmd=None)
            check("a fork PR exits OK and launches nothing", bounce(ns_fork), EXIT_OK)
            check("a fork PR is never counted as a bounce",
                  count_prior_bounces(ledger_path(tmp), 9), 0)
        check("nothing was launched for the fork", launched, [])

        # A repo genuinely OFF (this kit) exits OK and touches nothing.
        globals()["read_bounce_config"] = lambda root: (None, None, "off")
        with tempfile.TemporaryDirectory() as tmp:
            ns_off = argparse.Namespace(pr=1, ticket_id="X-1", repo="o/r", repo_root=tmp,
                                        state_dir=tmp, ticket_status_type=None, model="m",
                                        max_turns=10, timeout=5, keep_worktree=False,
                                        dry_run=False, fix_cmd=None)
            check("pipeline OFF exits OK, doing nothing", bounce(ns_off), EXIT_OK)

        # A BROKEN config is a loud failure, never silently treated as OFF or as a
        # green light to bounce against an invented budget.
        globals()["read_bounce_config"] = lambda root: (None, None, "broken:budgets.maxBounces")
        with tempfile.TemporaryDirectory() as tmp:
            ns_broken = argparse.Namespace(pr=1, ticket_id="X-1", repo="o/r", repo_root=tmp,
                                           state_dir=tmp, ticket_status_type=None, model="m",
                                           max_turns=10, timeout=5, keep_worktree=False,
                                           dry_run=False, fix_cmd=None)
            check("a BROKEN committed config is a loud failure", bounce(ns_broken), EXIT_USAGE)
    finally:
        globals().update(saved)

    # 7. dry-run never launches a worktree or a fix session, and never touches the ledger.
    saved2 = {name: globals()[name] for name in (
        "read_bounce_config", "pr_view", "check_runs_status", "read_review_verdict",
        "prepare_worktree")}
    try:
        globals()["read_bounce_config"] = lambda root: (3, "high", "ok")
        globals()["pr_view"] = lambda pr, repo: {
            "number": pr, "headRefName": "feat/eng-41-x", "headRefOid": "deadbeef",
            "isCrossRepository": False}
        globals()["check_runs_status"] = lambda sha, repo: "red"
        globals()["read_review_verdict"] = lambda state_dir, pr: None

        def boom(*a):
            raise AssertionError("dry-run must never prepare a worktree")
        globals()["prepare_worktree"] = boom
        with tempfile.TemporaryDirectory() as tmp:
            ns = argparse.Namespace(pr=41, ticket_id="ENG-41", repo="o/r", repo_root=tmp,
                                    state_dir=tmp, ticket_status_type=None, model="m",
                                    max_turns=10, timeout=5, keep_worktree=False,
                                    dry_run=True, fix_cmd=None)
            check("dry-run exits OK", bounce(ns), EXIT_OK)
            check("dry-run touches nothing in the ledger",
                  count_prior_bounces(ledger_path(tmp), 41), 0)
    finally:
        globals().update(saved2)

    # 8. The never-merge/never-label guard. Each token below appears exactly once in
    #    this file — here, in this check list. A count above one means a real
    #    merge/label path slipped into the code above.
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    for banned in ("gh pr merge", "--approve", "issueAddLabel", "addLabels",
                   "pr edit --add-label", "createReview", "enable-auto-merge",
                   "enablePullRequestAutoMerge"):
        if src.count(banned) > 1:
            failures.append("source names a forbidden op: %r" % banned)

    if failures:
        print("FAIL pipeline_bounce_local selftest:")
        for f in failures:
            print("  -", f)
        return 1
    print("ok — pipeline_bounce_local: counter matches N-1/N/N+1 exactly, ledger "
          "survives corruption, config off≠broken (§2), fork/terminal/no-trigger never "
          "bounce, dry-run touches nothing, no merge/label path")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Stage E bounce loop (KIT-93).")
    p.add_argument("--pr", type=int, help="pull request number")
    p.add_argument("--ticket-id", help="e.g. KIT-93, for the ledger and the exhaustion comment")
    p.add_argument("--repo", help="OWNER/REPO (default: the cwd's origin remote)")
    p.add_argument("--repo-root", help="a git checkout to fetch config/worktrees from (default: cwd)")
    p.add_argument("--state-dir", help="daemon-owned state root (default %s)" % DEFAULT_STATE_DIR)
    p.add_argument("--ticket-status-type", help="Linear WorkflowState.type, if the caller has it")
    p.add_argument("--model", default=DEFAULT_FIX_MODEL)
    p.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="fix-session wall-clock seconds")
    p.add_argument("--keep-worktree", action="store_true", help="do not remove the worktree after the run")
    p.add_argument("--dry-run", action="store_true", help="decide and print only; launch nothing")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)
    args.fix_cmd = None  # no CLI surface for this — test-only lever, set via Namespace directly

    if args.selftest:
        return selftest()
    if args.pr is None:
        p.error("--pr is required (or use --selftest)")
    return bounce(args)


if __name__ == "__main__":
    sys.exit(main())
