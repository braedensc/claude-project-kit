#!/usr/bin/env python3
"""
Stop hook: nudges Claude before ending a turn on a pushed branch that either
(a) has no PR yet, or (b) has an open PR with failing CI. CLAUDE.md may say
"open a PR when the task is done" and "watch CI to green," but written rules
aren't reliably followed across parallel worktree sessions — this makes both
a hard-to-miss reminder instead.

Ported from todoclaw's stop-pr-check.py (PR #59, in production 2026-07-03) —
built after a PR's failing Prettier check went unnoticed until the owner
pointed it out.

Only fires once per (branch, HEAD commit, reason) — tracked in
.claude/.stop-pr-nag/, gitignored — so it cannot loop even if the harness
doesn't honor stop_hook_active — re-blocking on an unchanged commit/reason
would trap Claude if it explains rather than pushes a new commit. Keying by
reason (not just commit) means opening the PR after a "no PR" nag doesn't
suppress a later "CI failing" nag on that same commit.
"""
import json
import os
import shutil
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
PROTECTED_BRANCHES = {"main", "master"}
STATE_DIR = os.path.join(PROJECT_ROOT, ".claude", ".stop-pr-nag")

# GitHub check conclusions that mean "this needs attention," excluding SUCCESS,
# NEUTRAL, SKIPPED, and null/pending (still running — not something to nag about).
FAILING_CONCLUSIONS = {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}


def _run(args, timeout=5):
    try:
        r = subprocess.run(
            args, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=timeout
        )
        return r.returncode, r.stdout.strip()
    except Exception:
        return 1, ""


def _already_nagged(branch: str, reason: str, head_sha: str) -> bool:
    state_file = os.path.join(STATE_DIR, f"{branch.replace('/', '_')}__{reason}")
    try:
        with open(state_file) as f:
            return f.read().strip() == head_sha
    except FileNotFoundError:
        return False


def _record_nag(branch: str, reason: str, head_sha: str) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    state_file = os.path.join(STATE_DIR, f"{branch.replace('/', '_')}__{reason}")
    try:
        with open(state_file, "w") as f:
            f.write(head_sha)
    except OSError:
        pass


def _block(branch: str, reason: str, head_sha: str, msg: str) -> None:
    if _already_nagged(branch, reason, head_sha):
        sys.exit(0)
    _record_nag(branch, reason, head_sha)
    print(json.dumps({"decision": "block", "reason": msg, "systemMessage": msg}))
    sys.exit(0)


try:
    data = json.load(sys.stdin)
except Exception:
    data = {}

# Defense-in-depth: if the harness marks the re-entrant Stop call after a
# block, don't nag twice in the same cycle. The per-(branch,reason,sha) dedup
# above is the real backstop, since this field isn't guaranteed.
if data.get("stop_hook_active"):
    sys.exit(0)

code, branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
if code != 0 or not branch or branch in PROTECTED_BRANCHES:
    sys.exit(0)

# Only care about branches already pushed (has an upstream) — a branch never
# pushed is still in-progress local work, not a "forgot to ship it" gap.
code, _ = _run(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
if code != 0:
    sys.exit(0)

code, head_sha = _run(["git", "rev-parse", "HEAD"])
if code != 0 or not head_sha:
    sys.exit(0)

# Any commits on this branch not on the mainline? Compare against the REMOTE base
# (origin/main), not local `main` — in normal PR flow you branch off origin/main and
# never update local main, so it's usually stale, and comparing against it makes a
# fresh branch with zero commits look "ahead of main" and false-nags (2026-07-04 fix).
base = "main"
for _ref in ("origin/main", "origin/master", "main", "master"):
    if _run(["git", "rev-parse", "--verify", "--quiet", _ref])[0] == 0:
        base = _ref
        break
code, _ = _run(["git", "merge-base", "--is-ancestor", "HEAD", base])
if code == 0:
    sys.exit(0)  # HEAD is an ancestor of the mainline — nothing new to ship

if not shutil.which("gh"):
    sys.exit(0)

# --state all: a merged/closed PR means the "open a PR" task was already done
# (and there's nothing further to watch), even if local main hasn't caught up.
code, out = _run(
    ["gh", "pr", "list", "--head", branch, "--state", "all", "--json", "number,state"],
    timeout=10,
)
if code != 0:
    sys.exit(0)  # can't reach GitHub / not authed — don't block on what we can't verify

try:
    prs = json.loads(out or "[]")
except Exception:
    sys.exit(0)

if not prs:
    msg = (
        f"Branch `{branch}` has pushed commits ahead of `main` with no PR. "
        "CLAUDE.md's branch workflow expects a PR once a task is done "
        "(`gh pr create`). Open one now, or say explicitly why not "
        "(task genuinely unfinished, or the user explicitly asked to hold off)."
    )
    _block(branch, "no-pr", head_sha, msg)
    sys.exit(0)

pr = prs[0]
if pr.get("state") != "OPEN":
    sys.exit(0)  # merged or closed — nothing further to watch

code, out = _run(
    ["gh", "pr", "view", str(pr["number"]), "--json", "statusCheckRollup,mergeStateStatus"],
    timeout=10,
)
if code != 0:
    sys.exit(0)

try:
    info = json.loads(out or "{}")
except Exception:
    sys.exit(0)
checks = info.get("statusCheckRollup", [])

# DIRTY = merge conflicts with the base branch. GitHub can't build the merge ref, so the
# PR's `pull_request` CI (Lint/Typecheck/Test/E2E) never runs — only side workflows like
# CodeQL/Vercel report, which can be SUCCESS and make a conflicted PR look green (a real
# near-miss, 2026-07-03: `gh pr checks` showed passing while the required CI hadn't run at
# all). Block so the PR gets rebased instead of mistaken for done. Fires only on explicit
# DIRTY — the transient UNKNOWN right after a push is ignored, so it can't false-block while
# GitHub is still computing mergeability.
if info.get("mergeStateStatus") == "DIRTY":
    msg = (
        f"PR #{pr['number']} for `{branch}` is DIRTY — it has merge conflicts with `main`, "
        "so the required CI (Lint/Typecheck/Test/E2E) never ran; only side checks such as "
        "CodeQL/Vercel reported, which can look green. Don't mistake that for a passing PR — "
        "rebase onto latest main, resolve, force-push, then watch CI to green "
        f"(`gh pr checks {pr['number']} --watch`):\n"
        "  git fetch origin main && git rebase origin/main\n"
        "  # resolve conflicts, then: git push --force-with-lease"
    )
    _block(branch, "pr-dirty", head_sha, msg)
    sys.exit(0)

failing = [c for c in checks if c.get("conclusion") in FAILING_CONCLUSIONS]
if not failing:
    sys.exit(0)  # clean, or still running — nothing to nag about yet

names = ", ".join(c.get("name", "?") for c in failing[:5])
msg = (
    f"PR #{pr['number']} for `{branch}` has failing CI ({names}). CLAUDE.md's "
    "branch workflow expects CI watched to green (`gh pr checks "
    f"{pr['number']} --watch`) before considering a task done — read the "
    "failing job's log, fix it, push, and re-watch."
)
_block(branch, "ci-failing", head_sha, msg)
sys.exit(0)
