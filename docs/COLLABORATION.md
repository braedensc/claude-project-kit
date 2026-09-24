# Collaboration & Multi-Agent Workflow

How multiple people — and multiple Claude Code sessions — work on the same repo at the
same time without stepping on each other. Ported near-verbatim from a production
build's COLLABORATION.md (proven by running two Claude sessions in parallel on one
repo for the final build milestones, 2026-07-01 → 03) plus that retro's fast-merge
protocol.

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

**And every branch is cut from `main` and merges back into `main`.** Never branch off
another feature branch; never merge two feature branches into each other. See
[Never stack a PR](#never-stack-a-pr) — it is the rule the enforcement layers below
exist for.

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

**Opening the PR is where Claude's involvement ends. Merging is the human's action
only — never `gh pr merge` in any form, including `--auto`; enabling auto-merge still
means the agent caused the merge** (a real near-miss in production, 2026-07-03 — now
hook-blocked). The repo-level `allow_auto_merge` *setting* may stay enabled for the
human's own use — the boundary is the agent never invoking a merge, not the setting.
**Approving is the human's too — never `gh pr review --approve`, least of all on your
own PR.** An approval says *someone else read this*; a session that supplies its own
makes that sentence false. Leaving a `--comment` review is fine. **And watch CI to
green before considering the task done:**
`gh pr checks <n> --watch`; if a check fails, read the failing job's log, fix, push,
re-watch. Local checks passing is necessary but not sufficient — the PR's actual CI
status is the source of truth.

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

This protocol is now **deterministic, not just written**: the PreToolUse hook blocks
`git commit`/`git push` outright on any branch whose PR is already MERGED (fails open
if `gh`/network is unavailable — it never blocks on what it can't verify).

### Never stack a PR

**Every PR branches off `main` and merges back into `main`.** A feature branch is never
cut from another feature branch, and two feature branches are never merged into each
other. This is a rule, not a preference, and three layers enforce it (below).

**Why — the two ways a stack goes wrong, both observed here.**

*It makes every descendant conflict, repeatedly.* On 2026-09-20 six PRs shipped as a
six-deep stack: #153←#154←#155←#156←#157←#158, each branched off its predecessor. Every
squash-merge of a base **rewrites** `main`, which put all remaining descendants into
CONFLICTING at once — and **GitHub runs no checks at all on a conflicted PR**, so they
read as "no checks reported", which looks like broken CI rather than a conflict. Six
stacked PRs meant five forced re-cascades. It ended by merging the tip alone (#158,
which provably contained the rest) and closing the other four. Independent PRs off
`main` never do this.

*It can merge into a dead branch and reach nothing.* Earlier, one PR stacked on another
was merged fifteen seconds after its base was squash-merged — which had deleted the base
branch. GitHub had not retargeted the stacked PR, so it merged into that dead branch.
Nothing went red: the PR showed MERGED and its CI had been green. The fix never reached
`main` and had to be re-landed.

**The move that stays allowed.** Bringing `main` *into* your branch is the opposite of
stacking, and it is what the conflict loop (`scripts/pr_conflict.py`, run by
`.github/workflows/pr-conflict-monitor.yml`) and the Stage E bounce driver ask a session
to run when `main` moves under a PR:

```bash
git fetch origin main && git merge origin/main
```

Rebasing onto `origin/main` is allowed too, and so is syncing your branch from its own
remote copy (`git pull`, `git merge @{u}`). None of these is blocked.

**If you need work that only exists on another branch:** wait for that branch to merge,
then cut from the updated `main`. If you genuinely cannot wait, say so and stop — that
is a call for the repo owner, not a workaround.

**Recovering a PR that is already stacked.** Retarget first — the `PR base` check
re-runs by itself — then check what the diff actually contains:

```bash
gh pr edit <n> --base main
gh pr view <n> --json baseRefName,mergedAt
```

Retargeting fixes the *target*; it does not fix the *content*. If the branch was also
**cut from** the other feature branch, its commits are still in this PR. Move your own
commits off it and onto `main` — this is allowed, because the new base is `main`:

```bash
git fetch origin && git rebase --onto origin/main <the-other-branch>
git push --force-with-lease
```

If that PR already merged, re-land from a **new** branch instead (the merged-PR guard
blocks committing on the old one, which is the guard doing its job), and confirm the
change landed by looking at `origin/main`, not at the badge:

```bash
git fetch origin && git log origin/main --oneline -5
```

**What enforces it** (detail in [Enforcement](#whats-automatic-enforcement)):

| Layer | Catches | Reach |
|---|---|---|
| PreToolUse hook | cutting a branch whose start carries unmerged work (checked by content), bringing another feature branch in (`merge`/`pull`/`rebase`/`push HEAD:<other>`), and basing a PR on a non-base branch (`gh pr create\|edit --base`, `gh api`, `gh_fallback.py`, `gh-merge-base`) | **Advisory** — reads command text; 19 of 28 adversarial spellings held (docs/SECURITY.md). The **only** layer that sees a branch *cut* from a feature branch whose PR targets `main` |
| Stop hook | ending a turn while an open PR's `baseRefName` is not a base branch — including a PR with green CI; a PR *merged* into a feature branch gets a notice | Reads GitHub's own record, so it does not care how the PR was created. Needs `gh` — silent where `gh` cannot reach GitHub (the dispatcher sandbox) |
| `PR base` workflow (`scripts/check_pr_base.py`) | a PR whose `pull_request` base is not the default branch or `main`/`master`; re-runs on `edited`, so a retarget clears it | Unforgeable, but it makes such a PR **red, not unmergeable** — branch protection covers the default branch only |
| The conflict loop | a CONFLICTING stacked PR is paged with a retarget recipe, never sent a fix request | So no session is started on a merge the guard would refuse |

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
- **`.env.local` does not follow you into a worktree** (symptom: a blank page, not an
  error, in Vite-style apps) — and if parallel worktrees share one local test
  account, they collide. Run the per-worktree provisioning script once per new
  worktree: `scripts/dev-worktree-login.sh <slug>` (ships at `templates/scripts/`
  until bootstrap activates it) — regenerates `.env.local` from the running local
  stack + creates a dedicated `<slug>@dev.local` login. Claude may *invoke* the
  script (hooks block constructing/reading env content, not running a committed
  script); or prefer tooling that resolves env at runtime from the running stack.
- Git hooks run from the MAIN checkout (`core.hooksPath` is absolute): a hook fix on a
  branch takes effect only after it merges AND the main checkout pulls it.
- **Worktrees sited *inside* the repo** (Claude Code's default, `.claude/worktrees/`)
  break every tool that walks the tree — including the lint gate a pipeline session has
  to get green before it can ship. Exclude the worktree root in every glob-based tool,
  or keep worktrees outside the repo (docs/LESSONS.md).
- **Cross-worktree writes are hook-blocked**: a write whose path belongs to a
  *different* worktree (especially the main checkout on `main`) would otherwise land
  there silently — past the branch guard, with tests here still green. The PreToolUse
  hook now blocks it and prints the corrected in-worktree path. Prefer absolute
  worktree paths and `git -C <dir>` over a persisted `cd` into another checkout.

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

### Parallel-session protocol (learned the hard way, running two sessions at once)

Running several Claude sessions at once works well **if** shared serialized resources
are handled explicitly. The parallel build collided three times on ADR numbering and
twice on doc-tail merges before these rules existed:

1. **Surface contracts in every kickoff prompt.** Each session gets an explicit "you
   own these paths; read-only everywhere else" list. That rule stops *textual*
   collisions — the conflicts git reported were in shared docs — but not *semantic*
   ones. Disjoint paths still collided: a new check met a sibling's test fixture it
   rejects, and two review-lane rules each assumed the other did not exist
   (docs/LESSONS.md). Owning paths is not owning behaviour; item 6 covers the rest.
2. **Structurally eliminate shared counters.** ADRs are one-file-per-decision with
   date+slug names (docs/adr/README.md) — no number to claim, no common tail to
   conflict. Prefer this shape for any append-only log.
3. **Keep an explicit serialized-resources list** — things that cannot be parallelized;
   ask the owner to sequence them: DB migrations (timestamp ordering), the golden E2E
   suite (one shared test user + one dev port), near-simultaneous merges to main
   (doc-tail conflicts).
4. **Before claiming anything ordered**, check `origin/main` **and every open PR's
   diff** (`gh pr diff <n>`) — parallel sessions claim resources before they merge.
5. **The later-opened PR updates.** Merge small and fast; the collision window is
   exactly the open-PR window. Update by merging `origin/main` into the branch rather
   than rebasing, so nothing is force-pushed under a worktree that may still hold it.
6. **Seam-check after every parallel wave.** Two changes that are each correct can
   combine into something worse than the bug either one fixed. Real case (2026-08-25):
   one stream made the bounce workflow write a pin — right; another made an expired pin
   fail closed — right. Together, the bounce copied its pin verbatim from dispatch time,
   so it was *always already expired*, and the fix session it briefed was blocked from
   every tool call — strictly worse than the original bug of running unpinned. No
   single-stream review catches this, because neither diff is wrong on its own and CI
   tests each PR against `main`, never against its siblings (the semantic-conflict entry
   in docs/LESSONS.md is the same shape one layer down). The practice that does catch
   it: after each wave, list the handoffs the wave touched, and for each one **quote the
   exact string from both sides** and confirm they match. The mechanical half now runs
   on its own: `.github/workflows/pr-union-check.yml` merges every open PR into one
   throwaway tree, runs the battery there, and names the PR whose arrival turned it red.
   The quoting stays manual — a union can be green and still hold two handoffs that
   disagree.
7. **A brief is not authority — a correct refusal is the system working.** Across this
   build, sessions refused their instructions four times, each with reasoning and a
   citation: a rule a later contract had superseded; a premise about hardcoded text that
   was simply false; an API read that would have 403'd on every run because the token
   lacks the scope; and emptying a config default that had a second, legitimate consumer.
   Treat those as expected behaviour, not insubordination — a brief is written before the
   code is read, and the session is the one reading the code. Running many sessions at
   once, the failure mode to fear is the opposite: every one of them dutifully
   implementing a stale brief, in parallel, at speed.
8. **An idle session cannot see `main` move.** Its Stop hook sampled merge state at
   turn-end, once. When a sibling merges and its PR goes `CONFLICTING`, the conflict
   monitor requests a fix, and a **conflict waker** on the machine that holds the
   worktree starts a session there to merge `main`, resolve, push and watch CI.
   Install the waker once, as yourself. It starts paid sessions, so the install
   refuses to run inside one:

   ```bash
   cp conflict-waker.conf.example conflict-waker.conf   # set REPO_DIRS
   python3 scripts/pipeline_conflict_waker_setup.py run
   ```

   That is a LaunchAgent, not a loop in a terminal. It writes a heartbeat every pass,
   and `verify` reports a stale one as **not running**. It stops at one card: read the
   dry-run count and sign it off. It takes only a worktree **your own Claude Code
   worked in**. A dispatcher's PR never qualifies; the bounce driver re-prompts that
   session in its own thread. Each session is capped (`MAX_BUDGET_USD`, `TIMEOUT_MIN`),
   and so is each pass (`MAX_SESSIONS_PER_PASS`). Three automated attempts per PR.
   After that, or when nothing claims a request within 15 minutes, the monitor pages
   a person. Logged out, asleep, or never installed, you get the old page, never
   silence. On a Stage E machine, its installer's `conflict-waker` step does this for
   you. Before you rely on it:

   - **`REPO_DIRS` names every project.** A checkout left out is never claimed. Parallel
     sessions in two projects means both roots, comma-separated.
   - **`CLAUDE_ARGS` covers the whole prompt.** A headless session gets only the tools it
     is given. The prompt uses git, gh, and the local checks your `CLAUDE.md` names, so
     allow each runner. Missing git or gh ends `failed` and pages you; missing the gate's
     runners pushes an untested merge. `conflict-waker.conf.example` has the kit's line.
   - **`CLAUDE_CONFIG_DIR` is read at install.** The job keeps the value your shell had
     then (else `~/.claude`). Change it later and re-run `run`, or the waker finds no
     transcripts and every conflict pages you. Preflight refuses a directory with none.
   - **After a merge, run `verify`.** It reads `behind` only when a script the job runs
     changed: `scripts/pr_conflict.py` or a `scripts/` module it imports. Then `run`
     moves the clone. Nothing schedules this.
   - **`waker.log` grows forever.** `~/.pr-conflict-waker/waker.log` takes both output
     streams every 300 s, with no rotation and no size cap. Truncate it yourself when it
     grows. Judge health by the heartbeat, not the log.
   - **Who gets paged.** The `PR_CONFLICT_PAGE_TO` Actions variable when set; else the
     PR's author, when a person; else a person who owns the repository. A plain @mention
     of an organization notifies nobody, so on an org repository whose PRs a bot opens,
     set the variable. Otherwise the page says *Nobody was paged* and the monitor's run
     fails.
   - **Where nothing answers, page at once.** The monitor asks for a fix up to three
     times per pull request before paging anyone. That only helps where a waker or a
     dispatcher answers. In a repository with neither, add `--max-fix-requests 0` to the
     workflow's `monitor` line. The first conflict then pages a person instead of posting
     a request nobody reads.
     Raise it again the day a waker serves that repository's checkout.
   - **The installer is macOS-only.** It builds a LaunchAgent, and on any other platform
     its preflight says so and stops. The waker itself is portable:
     `python3 scripts/pr_conflict.py wake` is one pass that exits. On Linux, do by hand
     what the installer would: run it from a clone no session works in, kept level;
     read `wake --dry-run`'s `would wake` count first, with the flags you will schedule
     (`wake --help`); schedule it every few minutes from a systemd user timer or cron,
     with `claude` and `gh` on its `PATH` and logged in; and read
     `~/.pr-conflict-waker/state/heartbeat.json` — a stale `finished_at` means it is
     not running.

---

## Task tracking — who works on what

Claude doesn't need a tracker; **humans do**, to claim a unit of work. Scale the tool:

| Scale | Tool |
|---|---|
| 2–3 people | **GitHub Issues + a Project board.** Free, next to the code; Claude reads/closes issues via `gh`. Start here. |
| Small team wanting polish | **Linear** (MCP server — Claude reads a ticket, implements, updates status). Optionally the full agentic pipeline: `docs/PIPELINE-CONTRACT.md` + `/work`. |
| Enterprise | **Jira / Azure DevOps**, usually via MCP. |

Claiming convention: assign the issue to yourself and move it to *In Progress* **before**
branching. Branch name references the issue (`feat/142-grid-drag`). The loop becomes:
"Claude, implement #142" → it reads the issue, branches, builds, opens the PR.

**Projects running the delivery pipeline** (those with a `delivery.json`) use the
stricter form `<type>/<ticket-id-lowercased>-<slug>` — `feat/eng-123-token-refresh` —
because a guard parses the ticket ID back out of it. Lower-case the team key: the
branch-naming guard accepts `[a-z0-9-]` only. Everyone else keeps the simpler shape
above; both satisfy `<type>/<short-kebab-desc>`.

---

## What's automatic (enforcement)

Four layers — the three *security* layers of docs/SECURITY.md, plus a workflow-nag
Stop hook between them:

1. **Claude Code PreToolUse hook** — runs before every tool call; the model **cannot**
   skip the hook. Block reasons print to **stderr** (the only stream Claude Code relays
   for a blocking exit 2), and an internal hook error **fails closed** (blocks)
   instead of crashing to a non-blocking exit.

   > **Read this list as "catches mistakes", not "cannot be evaded".** The *invocation*
   > is unbypassable; the **Bash** guards below are regular expressions over raw command
   > text that bash rewrites before it runs — quote collapsing, `$IFS`, command
   > substitution. Measured 2026-08-26 against the GuardFall research, re-run with the
   > stacked-branch guard on 2026-09-20: **95 probes, all seven Bash pattern guards
   > classified advisory, none robust** (`npm run test:bypass`,
   > full results and the layer-by-layer breakdown in docs/SECURITY.md §
   > *What the pattern guards actually carry*). A cooperating agent that mistypes is
   > still caught every time; a motivated caller is not. Each block below therefore
   > names what it stops — and where a guarantee has to survive an adversary, the
   > server-side layer named in item 4 is the one carrying it. The Edit/Write guards
   > (self-protection, cross-worktree, branch) resolve real paths and refs rather than
   > matching text, and are not in that class.

   The blocks themselves:
   - **Protects itself**: blocks Edit/Write (and Bash mutations — redirects, `sed -i`,
     `cp`/`mv`/`rm`, `chmod`/`chown`/`awk`, `git checkout/restore/reset/clean/stash/
     apply/rm/mv`, any interpreter invocation naming a protected path) of the hook
     scripts, `settings.json`, **and `settings.local.json`** (local settings override
     project scalars, so writing it could neutralize every guard), so a guard can't be
     edited away or unwired — changing them is a human-only terminal step. (Reads are
     fine.)
   - Blocks `Edit`/`Write`/`git commit` while on `main`/`master` — a new task is
     forced onto a branch. The project's CLAUDE.md (from docs/CLAUDE-template.md)
     also tells Claude to branch *proactively* before ever hitting the block.
   - Blocks the same on a branch **not matching** `<type>/<short-kebab-desc>`, so an
     auto-generated `claude/<codename>` worktree branch is renamed before any work
     (one landed unrenamed in a real PR).
   - **Stacked branches** — blocks cutting a branch whose start point carries commits no
     base branch has (`checkout -b`, `switch -c`, `git branch`, `worktree add`, including
     the *implicit* start — branching again where you stand, which is how the 2026-09-20
     six-deep stack actually grew), bringing another feature branch's work in (`merge`,
     `pull`, `rebase` onto it, `push HEAD:<other>`), and basing a PR on a non-base branch
     (`gh pr create|edit --base`, `gh api` to `/pulls`, `gh_fallback.py pr-create`, the
     `gh-merge-base` config). "Cut from the base" is judged **by content** (`git rev-list
     <start> --not <base tips>` is empty), so a fresh `claude/<codename>` worktree branch,
     a tag on main or a detached HEAD on main pass, and a branch with one commit of its
     own does not, whatever it is called. **Bringing the base in stays allowed** — `git
     fetch origin main && git merge origin/main` is what `pr-conflict-monitor.yml` and
     the bounce driver ask for — as do rebasing onto the base, `rebase --onto origin/main
     <old-base>`, and syncing a branch from its own remote copy. The command is read by a
     quote-aware lexer (comments, redirections, heredocs and continuations dropped;
     `$(…)`, subshells, `sh -c` and literal `eval` followed). **Fail direction:** a ref the
     hook cannot see (`$VAR`, `$(…)` output, `FETCH_HEAD`, `-`, `@{-N}`) fails *closed*
     with a message naming the literal ref to use; a checkout with no base branch to
     measure against, and any git failure while measuring, fail *open*. The base branch is
     `main`/`master` plus `github.defaultBranch` from a **committed** `delivery.json` and
     the remote's recorded default (`origin/HEAD`, which the config anchor now protects)
     — widening only, never read from the worktree copy. Advisory like every Bash guard;
     the Stop hook and the `PR base` workflow are the layers that do not read command text.
   - Blocks `Edit`/`Write` whose path is in a **different worktree** than the acting
     session's (resolved via `git worktree list`) — a write into another checkout
     (classically the main checkout on `main`, reached via a stray `cd`) otherwise
     lands there silently, past every branch guard. The session's own worktree is
     `CLAUDE_PROJECT_DIR`, **widened to the hook process's cwd only for a genuine
     subagent** (payload has `agent_id`) whose cwd is a worktree of the *same* repo
     (shared `--git-common-dir`) — subagents inherit that env var from their *parent*,
     which used to false-block them inside their own SDK-created worktrees and let
     writes into the parent's checkout through. The cwd is never trusted on its own:
     a persisted `cd` moves it, which would otherwise disarm this very guard. Fails
     open; same-worktree and out-of-repo writes are untouched.
   - Blocks Bash commands **naming secret files** (`.env*` non-example, `*.pem`,
     `*.key`, `id_rsa`, `credentials`) whatever the leading command — a path-target
     match, not a reader-verb list — and **egress/exfiltration shapes** (upload
     flags, `@file` payloads, `$VAR`-in-URL, `scp`/`nc` pushes) aimed at hosts
     outside a domain-boundary allowlist; plain GETs stay allowed. Both match the
     path/shape *as spelled*: the durable layers are `permissions.deny` for secret
     reads and a sandbox network policy for egress. Worth knowing which half of the
     egress guard is which — the **host allowlist** holds under variable expansion
     (an unresolved `$H` is not on the list), the **exfil-shape denylist** does not
     (a secret in a request header or a URL path is not an enumerated shape).
   - Blocks `git commit`/`git push` on a branch whose PR is already **merged** —
     pushes there are silently stranded (GitHub stops syncing the head and stops
     running CI). Fails open if `gh`/network is unavailable.
   - Blocks `gh pr merge` outright, including `--auto` — **merging is the human's
     action only**; Claude opens the PR and stops. (`--disable-auto` is exempt: it
     only *undoes* an auto-merge.) The matcher knows the `gh pr merge` subcommand,
     not the REST endpoint behind it; what makes the merge actually impossible is
     branch protection plus the platform merge gate, in repository settings.
   - Blocks the reachable spellings of **approving a PR** — `gh pr review --approve`/`-a`
     (including pflag shorthand clusters like `-ab`), the **bare** `gh pr review`,
     `gh api`/GraphQL writes carrying `event: APPROVE`, and a hand-rolled `curl` at
     `/pulls/<n>/reviews`. **An approval is the human's action for the same reason a
     merge is:** it is a claim to the next reviewer that somebody *else* read the
     code, and under branch protection it can be the thing that unlocks the merge.
     Blocked → **print the command for the human**, like a hook edit. `--comment`
     and `--request-changes` stay allowed (neither manufactures a human signal), as
     do reading reviews and `--add-reviewer` — *asking* for a review is not giving
     one. The bare form and an `--input`-hidden event fail **closed** — and that
     fail-closed default is why this guard held well under measurement:
     mangling the *flag* leaves nothing recognizable, which reads as the interactive
     form and is refused. What still gets through is mangling the *command word*,
     respelling the API event value, or a `curl` whose event lives in a file. The
     durable layer is a branch-protection rule that will not count a review from
     the PR's own author.
   - **Config anchor** — blocks writing a protected git ref (`git update-ref`,
     `branch -f/-D main`, a fetch/pull **refspec** targeting `main`/`master`,
     `symbolic-ref`, `replace`, history rewriters), repointing `origin`
     (`remote set-url/remove/rename`, `git config remote.origin.url`), and any
     mutation of `.git/**` in Bash or Edit/Write. Several guards deliberately read
     from the **default branch** rather than the worktree — `delivery.json`, the
     merged-PR base, the changed-file set a review is judged against — and that is
     only worth more than reading the worktree while the ref itself is not
     model-movable. Reads and a plain `git fetch` (the one honest writer of
     `origin/main`) are untouched, as is `git remote add origin`. Tamper-**evident**,
     not tamper-proof: the backstop stays the reviewed PR + CI.
   - **Pipeline guards** (`docs/PIPELINE-CONTRACT.md`) — six more blocks that exist
     **only** when a project opted into the agentic delivery pipeline. The single
     discriminator is whether `delivery.json` exists at the repo root, and the
     existence test runs before anything that can fail: absent → exit 0, no output,
     no git, no network, so a project that never adopted the pipeline behaves exactly
     as it always did. Everything they trust comes from the **dispatcher**, never the
     session — the pinned ticket and session mode from a pin file *outside* the
     worktree, config values from the committed copy on the default branch, states
     and labels compared by **ID** rather than display name. They block: moving a
     ticket into the `ready` state (**approving work is a human's action, with no
     in-session exception** — only `epic/*` provenance auto-approves and only *out of
     session*, through `scripts/check_auto_approve.py`, which can read the epic;
     `autonomy.autoApproveProvenance` configures that out-of-session tier and is not
     a permission a session holds); any tracker write **naming** a protected label —
     `agent:*`, `blocked:*`, `provenance:*` or the exact `hooks-change` — which is
     supervision or a human/executor signal and not the session's to edit (the same
     set the gh/Bash path refuses, enforced on the tracker-MCP path too); tracker
     writes outside the session's own ticket, including creating tickets directly in
     `ticket` mode (a finding is *requested* through the safe-outputs `ticket-create`
     kind instead, §8); editing the session's own
     in-progress acceptance criteria; Edit/Write/Bash mutations of grader paths
     (`.github/workflows/**`, `delivery.json`, `autonomy.riskPaths`, **and the
     staging mirrors `templates/workflows/**` and `templates/hooks/**`** — the
     inert copies that *become* the guarded paths at bootstrap, so gating only
     the destination would attach the guard when a file becomes visible rather
     than when its bytes are decided, leaving the activation a bare `git mv`
     that reads in review as just a move); work on a branch
     that doesn't carry the pinned ticket ID when `branch.requireTicketId` is on; a
     `dispatch.pinsRoot` resolving inside the repo (a pins directory the session can
     write is a pin it can forge); and a malformed, unrecognized, foreign-worktree or
     — in `ticket` mode — **expired** pin. Write-blocking and approval checks fail
     **closed**; checks that merely withhold autonomy from an *unpinned* session fail
     **open**, so a human's ad-hoc session in a configured repo is never bricked. An
     **expiry is not an absence**, though: a lapsed pin means a binding was issued
     and can no longer be verified, so reading it as "unpinned" would make waiting an
     escape. A broken `delivery.json` still leaves `delivery.json` itself editable, so
     the repo can never be held hostage by its own config.
2. **Claude Code Stop hook** (`.claude/hooks/stop-pr-check.py`) — blocks ending a
   turn when the branch has pushed commits ahead of `main` with **no PR**, its open
   PR is **based on a branch other than the base branch**, its open
   PR has **failing CI**, or its open PR is **`DIRTY`** (merge conflicts — GitHub
   then never runs the required CI, so side checks like CodeQL/Vercel can make a
   conflicted PR look green). The base check reads `baseRefName` out of GitHub's own
   record rather than any command text, so it fires however the PR was created — a
   respelled command, the web UI, an automation — and it deliberately fires on a
   **green** PR, because a stacked PR looks fine until its base merges. Before blocking
   it asks GitHub for the default branch (`gh repo view`), so a `trunk`- or
   `develop`-default repo is not mistaken for a stacked one, and if gh cannot say it
   says nothing. It is said **once per commit**, then steps aside so the DIRTY and
   failing-CI triage and the fix budget still apply to a stacked PR that is also red.
   Its message points at `gh pr edit <n> --base <base>` and `rebase --onto`, and —
   unlike the CI-failure messages — deliberately not at `/fix-ci`. A PR that was
   **merged** into a feature branch (so the work never reached the base) gets a
   non-blocking notice.
   Dedups per (branch, reason, commit) so it can't loop;
   fails open like the PreToolUse guards. It samples **at turn-end only**: a PR that
   goes `DIRTY` after its session ends is the conflict monitor's (below).

   A hook cannot make the model run anything — it can block, and it can inject text —
   so the two not-green messages **name `/fix-ci`**, the loop the kit already ships,
   rather than leaving the session to improvise one. The block is the enforcement;
   naming the tool is what stops the enforcement producing a worse fix.

   Those two reasons also share **one bounded budget per branch** (`MAX_FIX_ATTEMPTS`,
   3 — the same number as `/fix-ci`'s own bound and `budgets.fixIterations`' default).
   The per-commit dedup only stops a loop on an *unchanged* commit; a session pushing
   fixes gets a fresh sha each time and was nagged forever, including for failures no
   session can clear — a change under `.github/workflows/`, which its push credential
   deliberately may not land, or a rebase the sandbox refuses. After three commits the
   hook **escalates once** (block: stop fixing, report every check still red and what
   you tried) and then stops blocking, emitting a non-blocking notice per commit so a
   spent budget never renders as a clean one (`docs/PIPELINE-CONTRACT.md` §13). A PR
   observed with nothing red clears the ledger, so a later failure gets its own three.
   Red **only** on a human-pending check neither spends the budget nor clears it.
   `HUMAN_PENDING_CHECKS` is the single list of those **for this hook**. The Stage E
   bounce driver judges the same redness and now honours the same rule through its own
   `human_pending_checks` config key, which the Stage E installer writes and its operator
   guide documents. Say the relationship precisely: **two lists, one rule.** The hook runs
   in a session's process and the driver in the daemon's, with no import path between
   them, so a check named in one and not the other is a real divergence — and the only
   thing keeping them in step is that both batteries name the check.
3. **Git pre-commit hook** — blocks human/CLI commits on `main`. Bypassable with
   `--no-verify`, but…
4. **CI + branch protection** — the unbypassable gate. All changes land via PR with
   passing checks; no direct or force-push to `main`. The **PR base** workflow
   (`.github/workflows/pr-base.yml`, running `scripts/check_pr_base.py`) fails any PR
   whose base is not the default branch or `main`/`master`, read off the `pull_request`
   event — the one input no command spelling reaches. It is its own workflow so that a
   retarget (`edited`) re-runs it and so a stacked PR still gets every Kit-checks step.
   State its limit plainly: it makes a stacked PR **red, not unmergeable**. Branch
   protection guards the default branch only, so a PR whose base is a feature branch has
   no required context gating it at all; what this buys is that stacking can no longer
   happen quietly. The **Hooks change guard** job
   is the server-side twin of self-protection and grader-path protection: a PR that
   touches a grader path — `.claude/hooks/**`, `.claude/settings*.json`,
   `.github/workflows/**`, `scripts/check_*.py`, the dispatch path those graders
   import (`scripts/pipeline_*.py`, `scripts/jsonschema_mini.py`),
   `templates/workflows/pipeline-*.yml`, `templates/hooks/**`, the conflict loop's
   `scripts/pr_conflict.py` and its staged workflow
   `templates/workflows/pr-conflict-monitor.yml`, and (when configured) `delivery.json`
   plus its `autonomy.riskPaths`, read from the PR's **base** sha — must carry the
   `hooks-change` label — and, since that label is the *acknowledgement*, the job
   also checks **who applied it**: a machine identity can never supply it, and a
   bot-authored PR cannot label itself. Local hooks only constrain sessions that run
   them; a PR authored in another clone or the web UI never meets one. The job runs
   the gate script from the PR's **base** sha for the same reason it reads config
   from base — a head that can rewrite the gate grades its own homework.

Plus two **standing watchers**, because every layer above samples a moment and a PR
can break after that moment, when nobody is in a session:

- **`.github/workflows/pr-conflict-monitor.yml`** — on every push to `main`, and every
  20 minutes, labels a newly `CONFLICTING` PR `conflict` and requests a fix. A local
  session's PR is answered by the conflict waker (a supervised LaunchAgent,
  `scripts/pipeline_conflict_waker_setup.py`; parallel-session protocol item 8). A
  dispatcher's PR is answered by the bounce driver, in the session's own thread. A request
  nobody acknowledges within 15 minutes, a fix that does not land, a fourth conflict on
  the same PR, or a fork pages a person instead: `PR_CONFLICT_PAGE_TO`, else the PR's
  author, else a person who owns the repository — and a page that reaches nobody fails
  the run. Every command it posts names the PR's own base branch. It clears the label
  when the PR is mergeable again, closed, or a draft. It never merges, approves or pushes, and a PR
  whose mergeability never settles fails the run rather than reading as clean.
- **`.github/workflows/pr-union-check.yml`** — on every push to `main`, and hourly, runs
  the battery on the union of all open same-repo PRs (`scripts/check_pr_union.py`) and
  comments once on the PR whose addition turned the union red. Green alone, red
  together is the one class no per-PR check can see. Report-only; past 8 open PRs it
  skips and names what it skipped.

Plus two non-enforcing complements: native `permissions.deny` rules in `settings.json`
hard-block secret-file reads independently of the Python hook (docs/SECURITY.md), and
an advisory `SessionStart` hook injects branch/PR/dirty-tree orientation so a fresh
session opens already knowing where it is (plus, only where a delivery pipeline is
configured, the pinned ticket behind an untrusted-data fence — advisory, never a trust
source; see `docs/PIPELINE-CONTRACT.md`).

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

# Finish — open the PR, watch CI to green, then STOP (the human merges)
gh pr create --body-file <file>   # concise body; push + PR without being asked
gh pr checks <n> --watch          # red check? read the log, fix, push, re-watch
```
