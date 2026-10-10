# A session's root comes from its transcript folder when the harness names the main checkout

**Date:** 2026-10-10 · **Status:** Accepted · **Context:** KIT-214; amends the session-root
rule from the 2026-08-24 subagent fix

## Decision

The hooks used to treat `CLAUDE_PROJECT_DIR` as "the checkout this session acts in".
They now recover the session's own worktree from a value the model cannot move: the
payload's `transcript_path`.
- The harness files a transcript under `~/.claude/projects/<launch folder>/`. The folder
  name is the launch path with every non-alphanumeric character turned into `-`.
- The hooks match that folder name against **this repository's own** `git worktree list`.
  Exactly one existing worktree must match.
- An ambiguous, missing or foreign match keeps `CLAUDE_PROJECT_DIR` as the root: the
  strict reading, as before. So does a path over 200 characters, which the harness
  truncates and hashes.

All three hooks that need a root use the same function:
- `pre-tool-use.py`: the branch, branch-naming and cross-worktree guards, and the pin key.
- `stop-pr-check.py`: the missing-PR, red-CI, conflict and stacked-PR checks.
- `session-start.py`: the orientation line.

The battery fails if the three copies drift apart.

The subagent rule is unchanged: a subagent may still widen to its own worktree through
its cwd. When it cannot, it now falls back to the session's worktree, not the main
checkout.

**When the hooks cannot tell, they say so** (contract §13):
- A branch or cross-worktree block carries a note: the hook misjudged the session, it is
  a harness or kit fault, and the session should tell the owner rather than work around
  it.
- The Stop hook says it checked **nothing**, once per session, instead of exiting quietly.
- `session-start.py` compares the folder the session started in with the root the guards
  will use. If they differ, it shows the person a warning (`systemMessage`). At session
  start the cwd is still the launch folder, because no tool has moved it. So this warning
  catches *any* future change in what the harness hands hooks, not only this one.

## Why

**What changed, and when.** Every desktop worktree session from 2026-08-22 to 2026-09-24
saw its own worktree's branch in the startup line: Claude Code 2.1.229 to 2.1.280, 137
sessions. All 3 since saw `main`: 2.1.286 on 2026-10-04, then 2.1.295 twice on 2026-10-10.

In the 2.1.295 source, hooks get `CLAUDE_PROJECT_DIR = launchOptions.projectConfigRoot()`.
That is where project settings and hooks load from, and the desktop app sets it to the
repository's main checkout. That is a reasonable harness choice. But the guards were
written to the old meaning, and nothing told them it had changed.

**What it broke:**
- Every Edit/Write in a desktop worktree was "cross-worktree".
- Every commit was "on `main`".
- The Stop hook saw `main` and exited, so for 6 days no desktop session had its PR, CI
  or stacked-base checked.

Sessions shipped only through owner-run landing scripts. Nobody was told; the owner
found out when a session reported it could not commit.

**Alternatives rejected:**
- *The cwd.* It is model-movable: a persisted `cd` moves it. That is why the 2026-08-24
  fix refused it for main sessions, and the reason stands.
- *Any worktree of the repo.* A session could pick a sibling's worktree.
- *Asking for a harness variable.* None exists in the payload or the hook env today:
  the payload carries `session_id`, `transcript_path`, `cwd`, `permission_mode`, and
  `agent_id` for subagents only.
- *Running sessions only from a terminal.* It throws away the desktop app, and the hook
  would still be wrong wherever the desktop app is used.

**Security argument.** The transcript folder is set by the harness at launch, and no
tool call changes it. The widening can only select a worktree of the anchor's own
repository that exists on disk. It selects exactly the checkout `CLAUDE_PROJECT_DIR`
named before 2.1.286, so a desktop session is judged as a CLI session in that worktree
always was. Every selected worktree is still subject to the branch and naming guards, so
no path to a commit on `main` opens that a CLI session did not already have.

Collisions fail closed. Another worktree whose path has the same alphanumeric skeleton
makes the match ambiguous, so the anchor is kept. The cost of that is the session's own
widening; it never moves the root anywhere else.

**Residual, unchanged by this ADR:** the commit guard judges the root's branch, not the
branch of the folder `git commit` runs in. A session rooted on its worktree that `cd`s
into the main checkout and commits there is not blocked by this hook. That was already
true of every CLI worktree session. The push-to-`main` guard and branch protection still
stop it leaving the machine (KIT-237).

## Verified

- **Fail-first:** the 10 new behaviour cases fail against the hooks on `main` (33ff62b)
  and pass against the change. They cover: write and commit in its own worktree, both
  after a `cd`; a subagent's fallback; the Stop hook's missing-PR check; the
  unresolved-root notes on stderr; the Stop notice; and both session-start checks.
- **Unchanged guards:** the cases that must block do so before and after. They cover
  writes into the main checkout and into a sibling, a codename branch, a transcript
  naming the main checkout, no transcript, a foreign repo's transcript, an ambiguous
  match, a deleted-but-listed worktree, and an over-long folder.
- **Battery:** 570/570 against the candidates; the adversarial bypass battery is unchanged at 150/150.
- **Mutation:** 11 mutants, all killed. They took the uniqueness rule, the on-disk check,
  the transcript read, the warning note, the subagent fallback, a cwd-derived root, the
  Stop re-root, its notice and its once-per-session dedup, and both session-start
  behaviours. The first run let the uniqueness mutant live. The ambiguity test had passed
  by luck, depending on which match the mutant happened to pick, and the identity check
  compared too little. Both tests were fixed before landing.

**What is not proven:**
- A live desktop session after the change lands: the owner opens one and commits (KIT-214).
- The harness's truncate-and-hash rule for long folders: read from the 2.1.295 source, not
  observed (no ticket yet).
- That the warning text reaches the person in the desktop app: `systemMessage` is a
  documented hook field ("warning message shown to the user"), not yet seen on screen
  (KIT-214).
- That a misjudged session reaches the owner when he is not looking at it. Every signal
  here is in-app; a page outside the app is KIT-236.
