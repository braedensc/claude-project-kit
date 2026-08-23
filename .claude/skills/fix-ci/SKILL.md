---
name: fix-ci
description: Drive a red PR back to green — find the current branch's PR, read each failing check's log, apply the smallest fix, push, re-watch. Bounded to ~3 iterations, then reports. Never merges. Use this when a PR's CI is failing (e.g. after /ship step 6 or a Stop-hook ci-failing nag), or invoke it manually as /fix-ci.
argument-hint: [optional PR number, if not the current branch's PR]
allowed-tools: Bash(gh pr view *) Bash(gh pr checks *) Bash(gh run view *) Bash(gh run rerun *) Bash(git add *) Bash(git commit *) Bash(git push *) Bash(git fetch *) Bash(git rebase *) Bash(git status *) Bash(git branch *) Bash(git rev-parse *) Bash(git log *) Bash(git diff *)
---

## Repo state (injected before you start)

- Branch: !`git rev-parse --abbrev-ref HEAD 2>/dev/null`
- Status: !`git status --short`
- PR: !`gh pr view --json number,title,mergeStateStatus -q '"#\(.number) \(.title) [\(.mergeStateStatus)]"' 2>/dev/null || echo "none for this branch"`

## Instructions

Fix the failing CI on this branch's PR, following the kit's conventions exactly.
`$ARGUMENTS` may name a PR number; otherwise resolve it from the current branch.

1. **Resolve the PR:** `gh pr view --json number,mergeStateStatus,url`. No PR for
   this branch → stop and say so (open one first — that's `/ship`'s job, not this
   skill's).
2. **Triage DIRTY first.** If `mergeStateStatus` is `DIRTY`, the PR has merge
   conflicts — **fixing code cannot fix a conflict**, and while conflicted the
   required CI never even runs (side checks like CodeQL can still look green).
   Hand off to the canonical rebase recipe before touching anything else:
   ```
   git fetch origin main && git rebase origin/main
   # resolve conflicts, then:
   git push --force-with-lease
   ```
   Then continue below — the force-push re-runs the required CI.
3. **Read the failures.** `gh pr checks <n>` lists every check; for each failure,
   pull the run id from the check's link and read only what broke:
   `gh run view <run-id> --log-failed`.
4. **Flaky-shaped? Rerun ONCE, then investigate for real.** A failure that smells
   like infrastructure — runner lost communication, network timeout fetching
   deps, 429s, a job cancelled by the platform — gets exactly one
   `gh run rerun <run-id> --failed`. If it fails again, it is real: stop assuming
   flake and diagnose. Never rerun twice; repeated reruns are how real bugs get
   laundered into "flaky".
5. **Smallest fix that addresses the log's actual error.** Run the repo's
   relevant local checks before pushing (for this kit: `npm run test:hooks`,
   `python3 scripts/check_placeholders.py`) — but remember **local green is
   necessary, not sufficient**: CI catches things local runs miss (environment
   differences, format checks, jobs you don't run locally). The log, not the
   local run, is the source of truth for what broke.
6. **Commit and push:** write the message to a scratch file, `git commit -F`
   (conventional prefix, `Co-Authored-By:` line), push.
7. **Re-watch:** `gh pr checks <n> --watch` until every check reports.
8. **Bound the loop.** At most ~3 fix → push → watch iterations. Still red after
   that → STOP and report: which checks fail, what each log says, what you tried,
   and your best hypothesis — a human decision beats a fourth guess.
9. **Never merge.** Green means "ready for review", not "merge it" — `gh pr merge`
   is hook-blocked; merging is the human's action only. Report the PR URL and
   final CI state.
