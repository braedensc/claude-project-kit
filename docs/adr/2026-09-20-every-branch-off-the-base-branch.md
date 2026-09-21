# Every branch is cut from the base branch and merges back into it

**Date:** 2026-09-20 · **Status:** Accepted · **Context:** the six-deep stack of
2026-09-20 (#153←#154←#155←#156←#157←#158)

## Decision

A feature branch is **cut from the base branch and merges back into it** — never
branched off another feature branch, and never merged into one. Three layers enforce it,
each with a different reach, and the docs say which is which rather than implying one
guarantee:

1. **PreToolUse guard** (`_stacked_branch_block`) — blocks `git checkout -b` /
   `switch -c` / `git branch <new> <start>` / `git worktree add -b` from a non-base
   start point *including the implicit one* (branching again while HEAD is already on a
   feature branch), `git merge` / `git pull` of a feature ref, and
   `gh pr create|edit --base <feature>`. Reads command text, so **advisory**.
2. **Stop hook** — blocks ending a turn while an open PR's `baseRefName` is not a base
   branch. Reads GitHub's own record, so it does not care how the PR was created, and it
   fires on a **green** PR.
3. **CI** (`scripts/check_pr_base.py`, a step inside *Kit checks*) — fails any PR whose
   `pull_request` event base is not the repo's default branch.

The base branch is `main`/`master` **plus `github.defaultBranch` from the committed
`delivery.json`** when a project configured the delivery pipeline — read from the default
branch, never the worktree, and **widening only**.

## Why

**The incident.** On 2026-09-20 six PRs shipped as a chain, each branched off its
predecessor. Every squash-merge of a base *rewrites* `main`, which put all remaining
descendants into CONFLICTING at once — and **GitHub runs no checks at all on a
conflicted PR**, so five PRs read as "no checks reported", which looks like broken CI
rather than a conflict. Each merge forced a manual re-cascade of everything below it:
five of them. It ended by merging the tip alone (#158, which provably contained the
rest) and closing the other four. An earlier incident showed the second failure mode —
a stacked PR merged fifteen seconds after its base was squash-merged, into the now-dead
base branch, reaching `main` never and going red nowhere.

**Why the merge direction matters more than the rule.** The obvious guard — "block
`git merge`" — would have broken the kit. `git merge origin/main` *into* a feature
branch is this repo's documented conflict-resolution move (never rebase) and the literal
command `.github/workflows/pr-conflict-monitor.yml` asks a session to run. So the guard's
base set is an **allow-list** every arm consults, `_is_base_ref` is deliberately generous
about the remote segment (any `<x>/main` reads as `main`), and anything unparseable fails
**open**. A false negative there costs a stacked branch; a false positive there costs the
conflict loop. That asymmetry chose the fail direction.

**Why three layers and not one.** Measured (`npm run test:bypass`, 2026-09-20): the
PreToolUse guard holds 7 of 9 adversarial spellings — better than its six neighbours,
and for a structural reason rather than a lexical one. It tokenizes with `shlex` and
tests refs against an allow-list, so quote-collapse, `$VAR` and `$(…)` all fail *closed*:
what they leave behind still is not `main`. `${IFS}` still pays, because it defeats the
tokenizer before any arm runs. One bypass is one bypass, so the guard is classified
**advisory** like the rest, and the layers that do not read command text carry the
weight.

**The limit we are not hiding.** Even the CI check makes a stacked PR **red, not
unmergeable**. Branch protection covers the default branch only, so a PR whose base is a
feature branch has no required context gating it at all. What the three layers buy is
that stacking can no longer happen *quietly* — which is exactly what went wrong, since
the six-deep stack looked like broken CI rather than a rule violation.

**Alternatives rejected.** *An escape-hatch label* (`stacked-ok`) — rejected: the owner's
rule is absolute, and a label a session could ask for reintroduces the failure it
prevents. *Blocking `git rebase` too* — out of scope; rebasing onto a feature branch is
the same defect, but rebase is not currently guarded at all and folding it in would have
widened this change past the incident. Noted as a follow-up. *Detecting a branch CUT from
a feature branch but retargeted to `main`* — not decidable from the PR alone: its commits
look like ordinary extra commits. The docs say so instead of implying the check covers it.

## Verified

- **Hook battery**, `402/402` with the candidate hooks — 45 new cases: every
  branch-creating spelling, both merge directions, both PR-base spellings, the implicit
  start point on a feature branch *and* on `main`, the configured-`develop` project, and
  the read-only commands that name a feature branch without creating one.
- **Mutation-tested, which is the point.** Removing the PreToolUse dispatch turns **22**
  cases red; removing the Stop hook's base check turns **2** red. No new case passes with
  its guard removed. (Four selftest checks elsewhere in this repo were found on
  2026-09-20 matching their own source literals and unable to fail; that is the defect
  this step exists to rule out.)
- **Conflict-loop regression, explicitly:** `git merge origin/main` and
  `git fetch origin && git merge origin/main` are battery ALLOW cases and a `BY-DESIGN`
  probe in the bypass battery, shell-verified to build that exact argv.
- **Bypass battery**, `127/127` over 76 probes across 7 guards; `stacked-branch` records
  7 held / 1 through / 1 by design, with the one bypass (`${IFS}`) named, executed
  against shims, and attributed to the tokenizer rather than the allow-list. Two gaps
  found *while probing* — `git-merge` (the dashed plumbing binary) and `-bfeat/new` (an
  attached flag value) — were fixed before the guard shipped and are recorded as BLOCKED
  so a later refactor that loses them shows up as drift.
- **CI check selftest**, `8/8`, mutation-tested three ways: removing the comparison turns
  3 checks red; treating an absent base or an absent default branch as clean turns 1 red
  each, on the *reason* rather than the exit code.
