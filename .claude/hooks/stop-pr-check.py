#!/usr/bin/env python3
"""
Stop hook: nudges Claude before ending a turn on a pushed branch that either
(a) has no PR yet, or (b) has an open PR with failing CI that Claude could
actually fix. CLAUDE.md may say
"open a PR when the task is done" and "watch CI to green," but written rules
aren't reliably followed across parallel worktree sessions — this makes both
a hard-to-miss reminder instead.

Ported from a production build's stop-pr-check.py (in production 2026-07-03) —
built after a PR's failing Prettier check went unnoticed until the owner
pointed it out.

Only fires once per (branch, HEAD commit, reason) — tracked in
.claude/.stop-pr-nag/, gitignored — so it cannot loop even if the harness
doesn't honor stop_hook_active — re-blocking on an unchanged commit/reason
would trap Claude if it explains rather than pushes a new commit. Keying by
reason (not just commit) means opening the PR after a "no PR" nag doesn't
suppress a later "CI failing" nag on that same commit.

The two not-green reasons additionally share ONE bounded budget per branch
(see MAX_FIX_ATTEMPTS): the per-commit dedup above stops a loop on an
unchanged commit, but a session that keeps pushing fixes gets a new sha each
time and was nagged forever. Some failures no session can clear — a fix under
.github/workflows/ that its push credential is deliberately not allowed to
land, a rebase the sandbox will not let it perform — and telling it to try
again is then pure waste. After the budget the hook escalates once, loudly,
and stops blocking.
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

# Checks that are red PENDING A HUMAN, not pending a fix. Nagging Claude to
# "read the log, fix it, push" is wrong for these: no code change clears them,
# so the nag and the guard deadlock each other — Claude cannot finish the turn,
# and the only way it *could* self-clear is by performing the very human
# acknowledgment the guard exists to demand. Matched on check NAME.
#   "Hooks change guard" — ci.yml fails it until a human adds the
#   `hooks-change` label to a PR touching .claude/hooks/** or settings*.json.
# Keep this set TINY and only for checks whose sole failure mode is an absent
# human action; anything that can fail for a second reason belongs above.
# THE ONE LIST. A second "these need a person" set anywhere would drift out of
# step with this one; every consumer reads this.
HUMAN_PENDING_CHECKS = {"Hooks change guard"}

# How many commits this hook will demand a fix for on one branch before it
# escalates instead. Deliberately equal to `/fix-ci`'s own ~3-iteration bound
# and to `budgets.fixIterations`' default in docs/PIPELINE-CONTRACT.md §7 —
# three places, one number, so change them together. This bound is the coarser
# of the two: `/fix-ci` bounds the cycles inside one skill invocation, this
# bounds how many turns a session may spend on the same red PR however it
# improvises.
MAX_FIX_ATTEMPTS = 3

# Named once, spent in every not-green message: a session improvising its own
# fix loop is the behaviour this hook exists to replace, and the kit already
# ships the loop.
FIX_CI_HINT = (
    "Run `/fix-ci` — the kit ships that loop (resolve the PR, triage conflicts "
    "first, read each failing job's log, smallest fix, push, re-watch). Use it "
    "rather than improvising a different one."
)

# The failure class no session can clear, named where the session will meet it
# rather than after it has spent the budget discovering it. A dispatched
# session's push credential deliberately lacks GitHub's Workflows permission —
# a session that can rewrite CI can disable the guards supervising it — so a
# change under .github/workflows/ cannot be landed by `git push` OR by the REST
# contents API. Observed live 2026-09-08, three sessions deep.
UNFIXABLE_HINT = (
    "If the fix lives under `.github/workflows/`, STOP now and escalate instead: "
    "a dispatched session's push credential deliberately lacks GitHub's Workflows "
    "permission, so neither `git push` nor the REST contents API can land it. "
    "Retrying only spends the budget."
)


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


# ── the shared fix budget ────────────────────────────────────────────────────
# Two files per branch, beside the per-reason dedup markers above:
#   <branch>__attempts   one HEAD sha per line — the commits already nagged for
#   <branch>__exhausted  present once the escalation has been delivered
# Keyed by sha, not by a counter, so a reason switch on one commit (DIRTY, then
# red once the rebase lands) cannot charge that commit twice. Only the two
# not-green reasons draw on it: `no-pr` asks for a different action entirely
# and is cleared by taking it, so it must not consume fix attempts.


def _attempts_file(branch: str) -> str:
    return os.path.join(STATE_DIR, f"{branch.replace('/', '_')}__attempts")


def _exhausted_file(branch: str) -> str:
    return os.path.join(STATE_DIR, f"{branch.replace('/', '_')}__exhausted")


def _read_attempts(branch: str) -> list:
    try:
        with open(_attempts_file(branch)) as f:
            return [ln.strip() for ln in f if ln.strip()]
    except FileNotFoundError:
        return []
    except OSError:
        return []


def _record_attempt(branch: str, head_sha: str) -> None:
    if head_sha in _read_attempts(branch):
        return
    os.makedirs(STATE_DIR, exist_ok=True)
    try:
        with open(_attempts_file(branch), "a") as f:
            f.write(head_sha + "\n")
    except OSError:
        pass


def _is_exhausted(branch: str) -> bool:
    return os.path.exists(_exhausted_file(branch))


def _mark_exhausted(branch: str) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    try:
        with open(_exhausted_file(branch), "w") as f:
            f.write("1")
    except OSError:
        pass


def _clear_budget(branch: str) -> None:
    """Called only when the PR is observed with nothing red and no conflict —
    the fix loop succeeded. A LATER red on this branch is a new failure and
    deserves its own three attempts, not the remains of a spent ledger. A
    human-pending-only red is NOT this: it is no evidence the loop worked, so
    it neither spends the budget nor clears it."""
    for path in (_attempts_file(branch), _exhausted_file(branch)):
        try:
            os.remove(path)
        except OSError:
            pass


def _block(branch: str, reason: str, head_sha: str, msg: str) -> None:
    if _already_nagged(branch, reason, head_sha):
        sys.exit(0)
    _record_nag(branch, reason, head_sha)
    print(json.dumps({"decision": "block", "reason": msg, "systemMessage": msg}))
    sys.exit(0)


def _notice(msg: str) -> None:
    """Allow the turn to end, but say so out loud. PIPELINE-CONTRACT.md §13:
    'nothing to do' and 'could not do it' must never share a rendering, and a
    still-red PR whose budget is spent is emphatically the second. Advisory —
    the guaranteed half of the escalation is the block that preceded it."""
    print(json.dumps({"systemMessage": msg}))
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

# ── classify: is this PR not-green, and for which reason? ────────────────────
# DIRTY = merge conflicts with the base branch. GitHub can't build the merge ref, so the
# PR's `pull_request` CI (Lint/Typecheck/Test/E2E) never runs — only side workflows like
# CodeQL/Vercel report, which can be SUCCESS and make a conflicted PR look green (a real
# near-miss, 2026-07-03: `gh pr checks` showed passing while the required CI hadn't run at
# all). Checked BEFORE the rollup and reported instead of it: fixing code cannot fix a
# conflict, so a PR that is both conflicted and red must be triaged as a conflict first or
# the session spends its attempts editing code that was never the problem. Fires only on
# explicit DIRTY — the transient UNKNOWN right after a push is ignored, so it can't
# false-block while GitHub is still computing mergeability.
dirty = info.get("mergeStateStatus") == "DIRTY"
failing = [c for c in checks if c.get("conclusion") in FAILING_CONCLUSIONS]
# Red-pending-a-human is not a defect: let the turn end so Claude can hand the
# action over, rather than trapping it in a loop it must not self-clear.
fixable = [c for c in failing if c.get("name") not in HUMAN_PENDING_CHECKS]
pending = [c.get("name", "?") for c in failing if c.get("name") in HUMAN_PENDING_CHECKS]
also = (
    f" (Also red, but waiting on you, not on a fix: {', '.join(pending)}.)"
    if pending else ""
)

if dirty:
    reason = "pr-dirty"
    what = "still has merge conflicts with `main`"
    msg = (
        f"PR #{pr['number']} for `{branch}` is DIRTY — it has merge conflicts with `main`, "
        "so the required CI (Lint/Typecheck/Test/E2E) never ran; only side checks such as "
        "CodeQL/Vercel reported, which can look green. Don't mistake that for a passing PR — "
        "rebase onto latest main, resolve, force-push, then watch CI to green "
        f"(`gh pr checks {pr['number']} --watch`):\n"
        "  git fetch origin main && git rebase origin/main\n"
        "  # resolve conflicts, then: git push --force-with-lease\n"
        f"{FIX_CI_HINT} It triages the conflict before touching any code. If the sandbox "
        "refuses the rebase or the force-push, that is an environment limit, not a bug to "
        "work around: say so and escalate."
    )
elif fixable:
    reason = "ci-failing"
    names = ", ".join(c.get("name", "?") for c in fixable[:5])
    what = f"is still red ({names})"
    msg = (
        f"PR #{pr['number']} for `{branch}` has failing CI ({names}). CLAUDE.md's "
        "branch workflow expects CI watched to green (`gh pr checks "
        f"{pr['number']} --watch`) before considering a task done — read the "
        f"failing job's log, fix it, push, and re-watch.{also}\n"
        f"{FIX_CI_HINT}\n{UNFIXABLE_HINT}"
    )
else:
    # Green, still running, or red only on a human-pending check. Only the first
    # of those is evidence the fix loop worked, so only it clears the budget.
    if not failing:
        _clear_budget(branch)
    sys.exit(0)

# ── spend the shared budget ──────────────────────────────────────────────────
if _already_nagged(branch, reason, head_sha):
    sys.exit(0)  # this exact commit was already nagged under this reason

if _is_exhausted(branch):
    # The bound is spent AND the escalation has already been delivered. Blocking
    # again would trap the session in the very loop it was told to stop — but
    # going silent would render "could not do it" as "nothing to do" (§13). One
    # visible, non-blocking notice per commit instead.
    _record_nag(branch, reason, head_sha)
    _notice(
        f"PR #{pr['number']} for `{branch}` {what}, and this branch's "
        f"{MAX_FIX_ATTEMPTS}-attempt fix budget is spent. Not blocking again — but "
        "this PR is NOT green and needs a person. Say so in your final message."
    )

if len(_read_attempts(branch)) >= MAX_FIX_ATTEMPTS:
    _mark_exhausted(branch)
    _block(
        branch,
        reason,
        head_sha,
        f"STOP FIXING. PR #{pr['number']} for `{branch}` {what} after "
        f"{MAX_FIX_ATTEMPTS} attempts — that is this branch's whole budget, and a "
        "human decision now beats a fourth guess. Do NOT push another fix. Instead, "
        "before you end the turn, report: every check still red and what its log "
        "actually says, each fix you already tried and why it didn't work, and your "
        "best hypothesis — including whether this is fixable by a session at all "
        "(a change under `.github/workflows/` is not). Ending quietly is the one "
        "wrong answer: an unfinished job and a finished one must never look alike.",
    )

_record_attempt(branch, head_sha)
_block(
    branch,
    reason,
    head_sha,
    f"{msg}\n(Fix attempt {len(_read_attempts(branch))} of {MAX_FIX_ATTEMPTS} on this "
    "branch — after that this hook stops asking and expects a written escalation.)",
)
sys.exit(0)
