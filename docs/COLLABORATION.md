# Collaboration & Multi-Agent Workflow

How multiple people — and multiple Claude Code sessions — work on the same repo at the
same time without stepping on each other. Ported near-verbatim from todoclaw's
COLLABORATION.md (proven across a parallel Stage 5 ∥ Stage 6 build, 2026-07-01 → 03)
plus the retro's fast-merge protocol.

**Key mental model:** Claude Code does **not** coordinate across machines. Each session
is isolated and has no idea other humans or agents exist. Coordination is **git +
written context**, not a shared "Claude brain." The conflicts you'd hit are the same
two humans would hit — agents just hit them faster, so the discipline below matters more.

Most of this is **automatic** in this repo (see [Enforcement](#whats-automatic-enforcement)).

---

## The one rule

**One task = one branch = one PR.** Never have two sessions editing the same working
directory at once. Keep branches small and short-lived: a branch that lives 3 hours
merges cleanly; one that lives 3 days collides.

---

## Branch naming

`<type>/<short-kebab-desc>` — `type` matches the conventional-commit prefixes:

| type | use for | example |
|---|---|---|
| `feat` | new feature | `feat/grid-drag` |
| `fix` | bug fix | `fix/cluster-overlap` |
| `chore` | tooling, deps, config | `chore/bump-vite` |
| `refactor` | no behavior change | `refactor/scoring-lib` |
| `docs` | docs only | `docs/collaboration` |

---

## Starting new work (the routine)

Claude does this automatically; here it is explicitly:

```bash
git checkout main
git pull --ff-only                       # start from latest (skip if offline / no remote yet)
git checkout -b feat/<short-desc>
# ...work...  commit on the branch
gh pr create                             # open a PR; CI + review is the merge gate
```

You never merge your own work straight to `main` — that's what the PR + branch
protection is for.

**After committing on a feature branch, push and open the PR without waiting to be
asked** (`git push -u origin <branch>` → `gh pr create --body-file …`). The workflow
already routes everything through PRs, so opening one is the expected next step — but
still never push to `main` directly.

### The fast-merger protocol (never strand a commit)

If the repo owner merges PRs quickly, **never assume a prior PR is still open.** A
follow-up committed onto an already-merged branch is stranded — it never reaches main.
Before any follow-up edit:

```bash
git fetch --prune
gh pr view <n> --json state    # or: gh pr list --state merged --limit 5
```

If merged: `git checkout main && git pull --ff-only && git checkout -b <type>/<desc>`
and open a NEW PR. Caveat: squash-merges collapse stacked changes into one commit — to
confirm something landed, check the files in `origin/main`, not just the log.

---

## PR & commit format (concise, non-negotiable)

The PR body's job is a confident merge in under a minute — detailed is good, verbose is
not:

1. **What & why** — 2–3 plain sentences, no jargon.
2. **Changes** — one bullet per change, one line each.
3. **Verification** — one line: what ran, what passed.
4. Everything deeper (design rationale, edge cases, review write-ups) goes in a
   `<details>` block or an ADR — never the visible body.

Target ≤ ~150 visible words (the PR template encodes this). Same spirit for commit
messages: conventional subject + a few tight body lines. Write long text to a file and
use `git commit -F <file>` / `gh pr create --body-file <file>` — it also avoids the
hook's operation-vs-prose scanners entirely.

---

## Running several Claudes at once — git worktrees

A **worktree** is a second checkout of the same repo in a different folder, on its own
branch — the best practice for one person running multiple parallel agents without them
clobbering each other's files.

```bash
git worktree add ../<repo>-taskA feat/task-a     # new folder + new branch
git worktree add ../<repo>-taskB feat/task-b
# Open a separate Claude Code session in each folder. Fully isolated:
# separate files, separate branch, separate context.

git worktree list                                # see them all
git worktree remove ../<repo>-taskA              # clean up when merged
```

Why worktrees beat `git checkout` switching: switching mutates the **one** working
directory, so two sessions in the same folder fight. (Claude Code can also create/enter
worktrees for you — ask it to "work on X in a new worktree.")

**Caveats (each cost a debugging session — docs/LESSONS.md):**
- `node_modules/` and local service state are per-folder: run `npm install` in each
  worktree (the pre-commit secret scan self-heals via the shared git dir even before
  you do).
- **`.env.local` does not follow you into a worktree** — copy it by hand (a human step;
  hooks block Claude from `.env*`), or better, prefer tooling that resolves env at
  runtime from the running stack.
- Git hooks run from the MAIN checkout (`core.hooksPath` is absolute): a hook fix on a
  branch takes effect only after it merges AND the main checkout pulls it.

---

## Avoiding conflicts (the checklist)

- **Split work by feature folder, not by line.** One folder per system is the single
  biggest conflict-avoider — assign sessions to different folders.
- **Small PRs, merged often.** Don't let a branch drift for days behind `main`.
- **Rebase on main before opening/updating a PR** if main moved:
  `git fetch origin && git rebase origin/main`.
- **`CLAUDE.md` + feature READMEs are shared coordination, not just docs.** Written
  context is the *only* thing keeping isolated sessions consistent. Update docs in the
  same PR as the code.
- **Committed hooks + CI mean every contributor's Claude plays by the same rules**,
  even if they never read this file.

### The danger zone: ordered/generated files

Any file whose *name or order* is generated (DB migrations are the classic case) is a
serialized resource. Two branches generating them in parallel collide on ordering:

1. Pull latest `main` *immediately* before generating one, so yours sorts last.
2. Don't run two generating branches at once without coordinating.
3. Merge such PRs quickly — don't let them sit.

### Parallel-session protocol (learned the hard way, Stage 5 ∥ Stage 6)

Running several Claude sessions at once works well **if** shared serialized resources
are handled explicitly. The parallel build collided three times on ADR numbering and
twice on doc-tail merges before these rules existed:

1. **Surface contracts in every kickoff prompt.** Each session gets an explicit "you
   own these paths; read-only everywhere else" list. Code never collided under this
   rule — only shared docs did.
2. **Structurally eliminate shared counters.** ADRs are one-file-per-decision with
   date+slug names (docs/adr/README.md) — no number to claim, no common tail to
   conflict. Prefer this shape for any append-only log.
3. **Keep an explicit serialized-resources list** — things that cannot be parallelized;
   ask the owner to sequence them: DB migrations (timestamp ordering), the golden E2E
   suite (one shared test user + one dev port), near-simultaneous merges to main
   (doc-tail conflicts).
4. **Before claiming anything ordered**, check `origin/main` **and every open PR's
   diff** (`gh pr diff <n>`) — parallel sessions claim resources before they merge.
5. **The later-opened PR rebases.** Merge small and fast; the collision window is
   exactly the open-PR window.

---

## Task tracking — who works on what

Claude doesn't need a tracker; **humans do**, to claim a unit of work. Scale the tool:

| Scale | Tool |
|---|---|
| 2–3 people | **GitHub Issues + a Project board.** Free, next to the code; Claude reads/closes issues via `gh`. Start here. |
| Small team wanting polish | **Linear** (MCP server — Claude reads a ticket, implements, updates status). |
| Enterprise | **Jira / Azure DevOps**, usually via MCP. |

Claiming convention: assign the issue to yourself and move it to *In Progress* **before**
branching. Branch name references the issue (`feat/142-grid-drag`). The loop becomes:
"Claude, implement #142" → it reads the issue, branches, builds, opens the PR.

---

## What's automatic (enforcement)

Three layers, mirroring docs/SECURITY.md:

1. **Claude Code PreToolUse hook** — blocks `Edit`/`Write`/`git commit` while on
   `main`/`master`. The model **cannot** bypass this; the project's CLAUDE.md
   (created from docs/CLAUDE-template.md at bootstrap) also tells Claude to branch
   *proactively* before ever hitting the block.
2. **Git pre-commit hook** — blocks human/CLI commits on `main`. Bypassable with
   `--no-verify`, but…
3. **CI + branch protection** — the unbypassable gate. All changes land via PR with
   passing checks; no direct or force-push to `main`.

In practice: just start working. If you (or Claude) try to edit on `main`, you'll be
told to branch first — that's the system doing its job, not an error.

---

## Enterprise / large-scale notes

- **Claude Code GitHub Action / `@claude` mentions** — implement/review in CI, decoupled
  from laptops. The biggest "team" unlock.
- **Cloud / remote agent sessions** — fan out without tying up machines.
- **Review is the bottleneck and the quality gate** — required reviews, `CODEOWNERS`,
  automated passes (`/code-review`).
- **Centralized governance** — org-wide settings, permission policies, audit logs (the
  kit already appends `.claude/audit.log`), shared MCP/hook configs.
- **Architecture decides how well this parallelizes.** Clear module boundaries let many
  agents work with minimal merge surface; tangled shared files are where parallel
  agentic work breaks down.

---

## Quick reference

```bash
# Start a task
git checkout main && git pull --ff-only && git checkout -b feat/<desc>

# Run parallel agents (one worktree per task)
git worktree add ../<repo>-<task> feat/<desc>
git worktree list
git worktree remove ../<repo>-<task>

# Keep up to date / resolve drift
git fetch origin && git rebase origin/main

# Finish
gh pr create --body-file <file>   # concise body; push + PR without being asked
```
