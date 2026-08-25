---
name: ship
description: Ship a finished task — commit → push → open a PR (concise body) → move the ticket to review when a pipeline is configured → watch CI to green → stop. Never merges. Use this when work on a feature branch is done and ready for review, or invoke it manually as /ship.
argument-hint: [optional one-line summary of the change]
allowed-tools: Bash(git add *) Bash(git commit *) Bash(git status *) Bash(git branch *) Bash(git push *) Bash(git rev-parse *) Bash(git show *) Bash(gh pr create *) Bash(gh pr view *) Bash(gh pr checks *) Bash(test *) Bash(cat *) Bash(python3 *) mcp__linear__get_issue mcp__linear__save_issue mcp__linear__save_comment
---

## Repo state (injected before you start)

- Branch: !`git rev-parse --abbrev-ref HEAD 2>/dev/null`
- Status: !`git status --short`
- Commits ahead of main: !`git log --oneline origin/main..HEAD 2>/dev/null | wc -l | tr -d ' '`
- Pipeline configured: !`test -f "$(git rev-parse --show-toplevel 2>/dev/null)/delivery.json" && echo "yes — do step 6" || echo "no — SKIP step 6 entirely"`

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
6. **Update the ticket — only if a pipeline is configured.** `delivery.json` at the repo
   root is the sole discriminator (`docs/PIPELINE-CONTRACT.md` §2). **Absent → skip this
   step completely: no output, no tool call, no diagnostic.** Most projects using this kit
   have no tracker wired up, and `/ship` must behave for them exactly as it always has.

   Configured → resolve the ticket from **the pin**, never from the branch name (§3):
   read `<dispatch.pinsRoot>/<sha256(realpath(repo root))[:16]>.json` and use
   `pin.ticket.id`. **No valid pin → do not guess from the branch.** Print the ticket ID
   the branch implies and the values a human would need, and carry on to step 7 —
   attaching a PR to the wrong ticket is worse than attaching it to none.

   With a pinned ticket, make exactly these three updates:
   - **Move it to the review state** — `mcp__linear__save_issue` with `id` and `state` set
     to `linear.stateIds.review` from `delivery.json`. Compare and pass **state IDs, never
     display names**: a rename in the tracker UI must not silently desync the pipeline.
   - **Attach the PR URL** — the same `save_issue` call, `links: [{ "url": "<PR URL>",
     "title": "PR #<n>: <title>" }]`. `links` is append-only, so it never disturbs
     existing attachments.
   - **Post a summary comment** — `mcp__linear__save_comment` with `issueId` and a short
     `body`: what changed, how it was verified, the PR URL.

   `mcp__linear__*` assumes the server is keyed `linear` in the project's `.mcp.json`; a
   different key means different tool names here and in `allowed-tools` (an allow rule
   must name the server literally — a `mcp__*` wildcard is skipped).

   > **Never pass `labels` to `save_issue` here.** It **replaces the entire label set**, so
   > writing one label silently drops the rest — and `agent:*` labels are dispatcher-owned
   > anyway (§6): a session must not edit its own supervision. Read `delivery.json` values
   > from the copy committed on the default branch (`git show origin/<defaultBranch>:delivery.json`),
   > not the working tree — the working copy sits inside a worktree the session can edit.

7. **Watch CI to green:** `gh pr checks <n> --watch`. If a check fails, read the log,
   fix, push, re-watch. A `DIRTY` PR is not green — rebase and force-push.
   A PR touching `.claude/hooks/**` or `.claude/settings*.json` also needs the
   `hooks-change` label before the *Hooks change guard* job can pass:
   `gh pr edit <n> --add-label hooks-change`.
8. **STOP.** Do not merge. Merging is the human's action only (`gh pr merge` is
   hook-blocked). Report the PR URL and that it's ready for their review.
