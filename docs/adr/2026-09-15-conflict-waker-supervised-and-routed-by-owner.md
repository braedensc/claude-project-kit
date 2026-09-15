# The conflict waker is supervised, and a conflict goes to the lane that owns the session

**Date:** 2026-09-15 · **Status:** Accepted · **Amends:** [A conflict fix runs where the worktree lives](2026-09-15-conflict-fix-runs-where-the-worktree-lives.md)

## Context

The first conflict ADR split the loop: GitHub keeps the clock and the budget, and a
waker on the machine does the work. The logic was generic. The activation was not. The
docs told a person to type `while true; do … wake; sleep 300; done`. Every other job in
the pipeline installs itself, runs supervised, and writes a heartbeat. That loop did
none of it. It died with its terminal, and nothing could tell it from a waker with
nothing to do.

It also left a hole. A real machine holds two kinds of session:

- **Local sessions**, run by the owner, in the owner's worktrees, under the repo's hooks.
- **Dispatcher sessions**, run by a role account, each inside a sandbox.

A waker starts a fresh `claude -p` outside any sandbox. Pointed at a dispatcher's branch,
it runs the owner's reach over text a sandboxed session wrote. The bounce driver already
had a wake path that keeps the sandbox: a reply in the session's own tracker thread. It
never read merge state, so it could not use it.

## Decision

1. **A conflict goes to the lane that owns the session, and each lane can prove it.**
   - A **local** session's PR goes to the waker. It takes a request only for a worktree
     owned by its own uid **and** with this user's own Claude Code transcripts in it.
     A dispatcher's sessions keep transcripts under the role account's home, which the
     owner's uid does not write. The waker also refuses root and any account holding
     `~/.stage-e/env`.
   - A **dispatcher's** PR goes to the bounce driver's new `conflict` action. It acts on
     GitHub's `mergeable == CONFLICTING` plus an open, unclaimed request from the Actions
     bot. It writes a ledger row, then replies in the session's thread, then posts the
     monitor's own `ack`. A later push that leaves the PR conflicted gets one
     `result outcome=unresolved`. It spends no bounce, is capped on the ledger at the
     monitor's three, and never falls back to a fix ticket, which would start a second
     session on a held branch. A concluded PR gets a notice, not a re-prompt.
2. **The waker is its own small installer, not a fourth Stage E daemon.**
   `scripts/pipeline_conflict_waker_setup.py` has the Stage E contract: `run`,
   `--dry-run`, `status`, `verify`, the same exit codes, the agent refusal, and a
   signed-off dry run (CK-W1). It installs a **user LaunchAgent for the invoking person**
   and needs no `sudo`. The Stage E installer runs those same steps as its `conflict-waker`
   step (card CK-8, `CONFLICT_WAKER_CONF=off` by name), so a fresh install brings it up.
3. **Why not a daemon, and why not the role account.** The waker serves local sessions.
   They use the owner's `claude` and `gh` logins, which live in the owner's login keychain.
   A system daemon cannot open that keychain. A role-account waker would be the
   out-of-sandbox fix this ADR exists to prevent. Coupling it to the Stage E installer
   alone would also deny it to a machine with no dispatcher, which is where the original
   incident happened.
4. **Honest under §13.** Every real pass writes `pr-conflict-waker-heartbeat/1`, starting
   with a `running` beat. A dry run and a refusal write none. A pass exits 1 when a
   repository could not be read or a fix ended `unknown`. `verify` reports a loaded job
   with a stale heartbeat as UNKNOWN (not running), never as installed.
5. **Bounded.** Each session has a spend cap and a timeout. Each pass has a session cap,
   and the conf is refused when cap × timeout would outlast the monitor's result
   deadline. Every session in a pass is acknowledged before the first starts. Each PR
   gets three requests, lifetime. There is no loop; launchd starts each pass.

## Consequences

- **The waker runs only while its owner is logged in.** Local sessions do not run then
  either, and the monitor's ack deadline pages. The heartbeat monitor does not read the
  waker's file: it runs as the role account, and a LaunchAgent's heartbeat goes stale at
  every logout. The page on the PR is the loud path when it matters.
- **Local ownership rests on Claude Code's transcript layout.** If that layout moves,
  every request reads "not a local session's" and pages. That fails loud and never fixes
  in the wrong place.
- **The waker's code is protected from local sessions by the hooks and `verify`, not by
  permissions.** Both run as the same uid. The job execs an installer-managed clone,
  level with origin, and `verify` reports a dirty or foreign clone as failed.
- **The driver's `ack` counts only if its token belongs to a writer.** If not, the
  monitor pages while the session works. That is loud, and documented as a requirement.
- **Both lanes are untested live.** The batteries cover them; the first real conflict on
  a dispatcher's PR is the live test.
- The template monitor now runs `pr_conflict.py monitor` like the kit's own. It drops
  the commented-out, force-pushing `@claude` Tier 2. A hand-typed `@claude` reply still
  works where `claude.yml` runs.
