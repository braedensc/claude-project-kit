# Every branch is cut from the base branch and merges back into it

**Date:** 2026-09-20 · **Status:** Accepted · **Context:** the six-deep stack of
2026-09-20 (#153←#154←#155←#156←#157←#158)

## Decision

A feature branch is **cut from the base branch and merges back into it** — never
branched off another feature branch, and never merged into one. Four things enforce it,
each with a different reach, and the docs say which is which rather than implying one
guarantee:

1. **PreToolUse stacked-branch guard** (`_stacked_branch_block`). Blocks cutting a branch
   whose start point carries commits no base branch has (`checkout -b`, `switch -c`,
   `git branch`, `worktree add` — including the *implicit* start, branching again where
   you stand); bringing another feature branch's work in (`merge`, `pull`, `rebase` onto
   it, `push HEAD:<other>`); and basing a PR on a non-base branch (`gh pr create|edit
   --base`, `gh api` to `/pulls`, `scripts/gh_fallback.py pr-create --base`, the
   `gh-merge-base` config). Reads command text, so **advisory**.
2. **Stop hook** — blocks ending a turn, once per commit, while an open PR's
   `baseRefName` is not a base branch; a PR *merged* into a feature branch gets a
   non-blocking notice. Reads GitHub's own record.
3. **`PR base` workflow** (`.github/workflows/pr-base.yml` → `scripts/check_pr_base.py`)
   — fails any PR whose `pull_request` base is not the default branch or `main`/`master`,
   and re-runs on `edited`, so a retarget clears it.
4. **The conflict loop** (`scripts/pr_conflict.py`) pages a conflicting stacked PR with a
   retarget recipe instead of sending a fix request, and `gh_fallback.py pr-create`
   refuses a non-base `--base` by construction.

**The base branch** is `main`/`master`, plus `github.defaultBranch` from a **committed**
`delivery.json` (the owner asked that the default branch be configurable in the
pipeline), plus the remote's recorded default (`refs/remotes/origin/HEAD`) — widening
only. The config anchor now also refuses repointing `origin/HEAD`.

## Why

**The incident.** Six PRs shipped as a chain, each branched off its predecessor. Every
squash-merge of a base *rewrites* `main`, which put all remaining descendants into
CONFLICTING at once — and **GitHub runs no checks at all on a conflicted PR**, so five
PRs read as "no checks reported", which looks like broken CI rather than a conflict.
Five forced re-cascades; it ended by merging the tip alone (#158, which provably
contained the rest) and closing the other four. An earlier incident showed the second
failure mode: a stacked PR merged into its already-deleted base, reached `main` never,
and went red nowhere.

**Why bringing the base IN must stay allowed.** `git fetch origin main && git merge
origin/main` is exactly what the conflict loop and the Stage E bounce driver ask a
session to run when `main` moves under a PR. A stacked-branch guard that blocked it would
break the loop. So refs are tested against an allow-list first, and the battery's first
thirty-odd cases are that move in every spelling a session uses — with `2>&1`, a pipe, a
comment, a line continuation, an env prefix.

**Why by content, not by name.** "Cut from the base" means the start point carries no
work that is not already on a base branch. `git rev-list -n 1 <start> --not <base tips>`
answers that directly. Names only approximate it, and the approximation failed both ways
in the first version: every Claude Code worktree session starts on `claude/<codename>` —
a branch with zero commits of its own — and was refused `git checkout -b`; a feature
branch literally named `feat/main` passed as a base.

**Why a lexer.** The first version split the command on `;`/`&`/`|` before reading quotes,
so `git merge origin/main 2>&1` read `2>` as the ref — a false block of the invariant
above — and so did a trailing comment, a heredoc body, and a read-only `grep` whose
pattern mentioned a git command. v2 reads the RAW command through a small quote-aware
lexer (comments, redirections, heredocs and continuations dropped; separators split only
outside quotes; `$(…)`, subshells, `sh -c` and literal `eval` followed). It is total and
linear: a 1 MB command lexes in ~0.25 s, where the first version's regex took over 10 s on
25 KB — past the hook's timeout, and a timed-out PreToolUse hook does not block, so every
later guard was off for that call.

**Fail direction.** A ref the hook cannot see at hook time (`$VAR`, `$(…)` output,
`FETCH_HEAD`, `-`, `@{-N}`) fails *closed*, with a message naming the literal ref to use.
A checkout with no base branch to measure against, and any git failure while measuring,
fail *open*: failing closed there would block that repo's own `git merge origin/trunk`.

**Why four layers and not one.** Measured (`npm run test:bypass`): the guard holds **19 of
28** adversarial spellings. What gets through defeats the lexer itself (`${IFS}`, a
variable command word, `eval "$var"`) or leaves `git`/`gh` behind (`curl` to the REST API,
`hub`, `xargs`, an alias, an interpreter). One bypass is one bypass, so it is classified
**advisory** like the other six Bash guards, and the layers that do not read text carry
the weight — within their own limits: the Stop hook needs `gh` to reach GitHub (it does
not in the dispatcher sandbox, which is why `gh_fallback.py` refuses by construction), and
the workflow makes a stacked PR **red, not unmergeable**, because branch protection
covers the default branch only.

**The gap no server-side layer closes.** A branch *cut* from a feature branch whose PR
targets `main` has a correct base; its extra commits are just commits. Only the
PreToolUse guard sees it. Stated, not implied.

**Alternatives rejected.** *An escape-hatch label* (`stacked-ok`) — the owner's rule is
absolute, and a label a session could ask for reintroduces the failure it prevents.
*Making "PR base" a required context* — every PR into `main` passes it, and branch
protection never covers a PR based on a feature branch, so requiring it adds nothing.
*Narrowing the base set to exactly the configured default* — a stale `origin/HEAD` or an
unreadable config would then take `main` away from the conflict loop. *Trusting the
worktree copy of `delivery.json`* for the base name — one Write would name a feature
branch a base.

## Verified

- **The first version was reviewed adversarially before it shipped** — six independent
  lenses, a reproduce-to-verify pass on every finding, and a completeness critic. 56
  findings; every one of the 24 verified reproduced, including the redirect false block
  above. That review is why this version exists. Its lessons are in `docs/LESSONS.md`.
- **Hook battery** — realistic scenario repos (`make_stack_repo`: a bare `origin` with
  `origin/HEAD`, feature branches *with* commits, a codename worktree branch, detached
  HEADs, a `trunk`-default repo, one with no `origin/HEAD`, a git-flow repo, a
  worktree-only and a malformed `delivery.json`) — plus Stop-hook sandboxes for a trunk
  default, a failing `gh repo view`, a committed and a worktree-only config, an
  `origin/HEAD` default, stacked-and-red across two turns, and merged-off-base.
- **Mutation-tested per sub-behaviour, not only per layer.** Removing the dispatch turns
  every BLOCK case red and no ALLOW case; removing the `origin/HEAD` anchor turns its 5
  red; removing the Stop check turns its 4 red. And **14 targeted single-line mutants** —
  worktree config accepted, content check off, redirections kept, comments kept, the v1
  name looseness, own-branch sync, simulated HEAD, `sh -c`/`eval`, `origin/HEAD`, unseen
  refs, the Stop dedup, config, `origin/HEAD` and GitHub lookups — are **all killed**.
- **Bypass battery** — 150/150 over 95 probes; `stacked-branch` records 19 held / 8 through
  / 1 by design, the through-probes shell-verified against shims where the shell builds
  the argv.
- **Robustness** — 20,000 fuzzed inputs through the lexer and verdict: no exception. (The
  hook's dispatch fails *closed* on any exception, so a crash would block every such
  command.)
- **`check_pr_base.py` selftest** — 20/20, including `main()` run as a subprocess with a
  clean environment, so a `main()` that ignored the verdict would fail it.
