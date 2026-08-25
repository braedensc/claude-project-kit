---
name: ship
description: Ship a finished task — commit → push → open a PR (concise body) → queue the ticket's move to review when a pipeline is configured → watch CI to green → stop. Writes to the tracker only through safe-outputs requests. Never merges. Use this when work on a feature branch is done and ready for review, or invoke it manually as /ship.
argument-hint: [optional one-line summary of the change]
allowed-tools: Bash(git add *) Bash(git commit *) Bash(git status *) Bash(git branch *) Bash(git push *) Bash(git rev-parse *) Bash(git show *) Bash(gh pr create *) Bash(gh pr view *) Bash(gh pr checks *) Bash(test *) Bash(cat *) Bash(python3 *) mcp__linear__get_issue
---

## Repo state (injected before you start)

- Branch: !`git rev-parse --abbrev-ref HEAD 2>/dev/null`
- Status: !`git status --short`
- Commits ahead of main: !`git log --oneline origin/main..HEAD 2>/dev/null | wc -l | tr -d ' '`
- Pipeline configured: !`test -f "$(git rev-parse --show-toplevel 2>/dev/null)/delivery.json" && echo "yes — do step 6" || echo "no — SKIP step 6 entirely"`
- Safe-outputs channel: !`test -n "$PIPELINE_SAFE_OUTPUTS" && echo "$PIPELINE_SAFE_OUTPUTS" || echo "NONE — \$PIPELINE_SAFE_OUTPUTS unset"`

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

   **You do not write to the tracker.** You append **write-requests** to the safe-outputs
   file (§8); a separate job that holds the tracker credential validates the batch against
   the **dispatcher-supplied** pinned ticket ID and executes only the survivors. On the
   shipped GitHub Actions backend the session job carries no `LINEAR_API_KEY` at all, so
   there is no write path to take even if one were wanted. Reads (`mcp__linear__get_issue`)
   are unaffected and stay direct.

   The file is the path named by `$PIPELINE_SAFE_OUTPUTS` (basename `requests.json`).
   **Unset → no validator is collecting:** do not invent a path and never write it inside
   the worktree; print the requests you would have emitted, say plainly they were not
   delivered, and carry on to step 7.

   With a pinned ticket, append exactly these two requests — **append, never overwrite**,
   since `/work` writes to the same file, and the validator executes requests in array
   order:

   ```json
   { "type": "ticket-comment", "ticket_id": "ENG-123", "body": "…what changed, how it was verified, the PR URL…" }
   ```
   ```json
   { "type": "ticket-state", "ticket_id": "ENG-123", "to": "review" }
   ```

   ```bash
   # $REQ = a scratch file holding ONE request object as JSON
   python3 - "$PIPELINE_SAFE_OUTPUTS" "$REQ" <<'PY'
   import json, os, sys
   path, req = sys.argv[1], json.load(open(sys.argv[2]))
   doc = json.load(open(path)) if os.path.exists(path) else {"schema": "pipeline-safe-outputs/1", "requests": []}
   if doc.get("schema") != "pipeline-safe-outputs/1":
       sys.exit("refusing to append to an unrecognized schema: %r" % doc.get("schema"))
   doc["requests"].append(req)
   os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
   with open(path, "w") as fh:
       json.dump(doc, fh, indent=2)
   print("queued %s (%d request(s) pending)" % (req.get("type"), len(doc["requests"])))
   PY
   ```

   Four things decide whether the batch survives:
   - **`to` is the canonical state key `review`** — from `linear.stateIds`, never a UUID
     and never a display name. The validator resolves the key to an ID itself, reading
     `delivery.json` from the default branch. `raw`, `ready` and `done` are refused
     however the caller is configured: `ready` would be self-approval, `done` a session
     claiming its own merge (§5).
   - **At most one `ticket-state` in the whole batch.** `/work` never emits one, so this
     is yours — but a second one anywhere rejects everything.
   - **The PR URL goes in the comment body.** §8 defines no link-attachment request type,
     so the summary comment carries the URL; the branch is `<type>/<ticket-id>-<slug>`, so
     a tracker's own GitHub integration attaches the PR on its own.
   - **Never queue a `ticket-label` request here.** `agent:*`, `blocked:capacity`,
     `provenance:*` and `hooks-change` are dispatcher- and human-owned (§6) and are refused
     in `add` **and** `remove` — a session must not edit its own supervision.

   **One invalid request rejects the entire batch and nothing is applied** (§8). Caps: 20
   requests, 16 000-char comment bodies. And the batch needs **exactly one** telemetry
   block (§4) across all its comments, **valid** against
   `schemas/telemetry-block.schema.json` and not merely carrying the marker — the
   validator checks the shape now, so a malformed block fails here instead of being
   dropped without a word by the collector.

   `/work` queues that block in its step 9, *after* this skill returns, so it can report
   the PR number and the two lifecycle rows that only exist once you have run:
   `first_commit` and `pr_opened`, both `actor: "agent"`. Leave them the values to do it
   with — the numbers below are the ones §4 wants:

   ```bash
   gh pr view --json number,createdAt
   ```

   ```bash
   TZ=UTC git log --reverse --date=format-local:'%Y-%m-%dT%H:%M:%SZ' --format=%cd origin/main..HEAD | head -1
   ```

   If this session ran **without** `/work`, queue the block yourself, in its own comment
   rather than in the summary above, and carry those two rows: zero blocks rejects the
   batch as a `telemetry-required` violation, two as a double-count. Never report
   `ci_green`, `merged` or `deployed` — those are observed after you stop, by CI or by
   the platform, and §4 forbids an agent-claimed merge outright.

7. **Watch CI to green:** `gh pr checks <n> --watch`. If a check fails, read the log,
   fix, push, re-watch. A `DIRTY` PR is not green — rebase and force-push.
   **`Hooks change guard` red is a stop-and-report, not a task.** A PR touching
   `.claude/hooks/**` or `.claude/settings*.json` stays red until the `hooks-change`
   label is added, and that label is **set by a human** (`docs/PIPELINE-CONTRACT.md`
   §6) — it is the acknowledgement that guard machinery changed. A session that labels
   its own PR is acknowledging its own change, which is the one thing the gate exists
   to prevent. Report that the PR needs the label and stop; do not add it.
8. **STOP.** Do not merge. Merging is the human's action only (`gh pr merge` is
   hook-blocked). Report the PR URL and that it's ready for their review.
