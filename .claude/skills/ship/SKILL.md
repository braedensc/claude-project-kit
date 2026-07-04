---
name: ship
description: Ship a finished task — commit → push → open a PR (concise body) → watch CI to green → stop. Never merges. Use this when work on a feature branch is done and ready for review, or invoke it manually as /ship.
argument-hint: [optional one-line summary of the change]
allowed-tools: Bash(git add *) Bash(git commit *) Bash(git status *) Bash(git branch *) Bash(git push *) Bash(git rev-parse *) Bash(gh pr create *) Bash(gh pr view *) Bash(gh pr checks *)
---

## Repo state (injected before you start)

- Branch: !`git rev-parse --abbrev-ref HEAD 2>/dev/null`
- Status: !`git status --short`
- Commits ahead of main: !`git log --oneline origin/main..HEAD 2>/dev/null | wc -l | tr -d ' '`

## Instructions

Ship the current work as a PR, following the kit's conventions exactly. `$ARGUMENTS`
is an optional one-line summary — use it to seed the title/body, or infer one from the
diff if empty.

1. **Confirm you're on a proper feature branch** (`<type>/<short-kebab-desc>`), not
   `main`/`master`. If on `main`, stop and tell the user to branch first — the hooks
   will block a commit anyway.
2. **Stage** the relevant files explicitly (never `git add -A` — see the kit's
   generated-file lesson).
3. **Write the commit message to a scratch file** and commit with `git commit -F`
   (not `-m` — long text goes through files, per the prose-vs-operation convention).
   Conventional prefix (`feat:`/`fix:`/`chore:`/`refactor:`/`docs:`); end the body with
   the `Co-Authored-By:` line the repo uses.
4. **Push** the branch (`git push -u origin <branch>`).
5. **Write the PR body to a scratch file** — 2–3 sentence what/why, one-line bullets,
   one verification line, depth in `<details>`, ≤ ~150 visible words — and open the PR
   with `gh pr create --body-file <file>`.
6. **Watch CI to green:** `gh pr checks <n> --watch`. If a check fails, read the log,
   fix, push, re-watch. A `DIRTY` PR is not green — rebase and force-push.
7. **STOP.** Do not merge. Merging is the human's action only (`gh pr merge` is
   hook-blocked). Report the PR URL and that it's ready for their review.
